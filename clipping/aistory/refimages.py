"""The consistency images of a story's cast, places and props (AI Story phase 2,
stage 5; spec 2.3-2.5, 5, 8.1).

Every image here goes through the generation stack the style preview uses
(``imaging.py``): the story's ``generation_profile.route``, ``allow_paid``
and the caps of the budget with the story's ledger total (a paid link is
tried once, DEC-106), and the free-tier limiter. Each answered call is booked
once -- a row in the story's ``cost_ledger.json`` (``step`` names the image:
``character_image:<char_id>:<which>``, ``place_image:<place_id>:<variant>``,
``prop_image:<prop_id>``), and today's spend for a paid one; the runner books
nothing itself.

Which chain makes which image, and the label it carries
(``schemas.CONSISTENCY``, in its JSON and in the log):

====================================  ==========================================  ===========
image                                 how                                         label
====================================  ==========================================  ===========
character portrait                    IMAGE_CHAIN, text to image                  base
turnaround, expressions               IMAGE_EDIT_CHAIN, the portrait (+ design    references
                                      references) as reference images
... in ``prompt_only`` mode           IMAGE_CHAIN, the same prompt and the        prompt_only
                                      portrait's seed
place master plate (``day``)          IMAGE_CHAIN, text to image                  base
another time variant                  IMAGE_EDIT_CHAIN, the plate as reference    references
... in ``prompt_only`` mode           IMAGE_CHAIN, the plate's seed               prompt_only
prop image                            IMAGE_CHAIN, text to image                  base
====================================  ==========================================  ===========

**Stop and ask (spec 8.1).** In ``references`` mode -- the default -- an image
that needs an editor is first checked with :func:`edit_readiness`, which
calls nothing. When no link of IMAGE_EDIT_CHAIN can run for the story's route
and budget -- or the only ones that could are local servers and none answers
its probe -- :class:`NeedsEditor` is raised before any generation request:
nothing is spent, no file or document changes. The mode is never switched
here. Only the user sets the story's ``consistency_mode`` to ``prompt_only``;
every image then made from text alone is labelled so, and printed with 🟡.

**Reference images per link.** At most :data:`MAX_REFERENCES` are sent: the
portrait first, then the oldest design references (a place variant sends its
master plate alone). ``local/comfyui``'s multi-reference template has four
slots; ``fal/seedream-4-edit`` and Gemini take them all (Gemini does not
honour a seed); ``fal/flux-kontext-pro`` uses only the first -- the portrait
(:data:`REFERENCES_USED`). The link that answered is the image's ``source``,
and a line says when it used fewer references than it was sent.

Prompts come from ``prompting.py`` and never carry a name (spec 2.3):
characters are drawn from their descriptor and signature items. A
regenerate-with-note appends ``Author's note: <note>.`` after the locked
blocks, never in place of them; the name of any entity of the story in the
note is replaced by a neutral word ("the character", "the place", "the
object"). A regenerate also passes a fresh ``seed``, so a provider that
honours seeds does not answer the same picture; the seed is recorded, and a
portrait's becomes the character's ``ref_seed``. Each image replaces its own
slot's file and reference only (a file of another extension in the same slot
goes too); the entity is re-read right before it is written, so an upload
appended meanwhile is kept.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from clipping.providers import adapters as adapters_mod
from clipping.providers import gating
from clipping.providers import generation as gen

from . import imaging, prompting, schemas
from . import uploads as uploads_mod

CHARACTERS, PLACES, PROPS = "characters", "places", "props"

# Output sizes (width, height). The sheets are wide; everything a shot is
# composed on (portrait, plates) is vertical 9:16.
PORTRAIT_SIZE = (720, 1280)
TURNAROUND_SIZE = (1280, 720)
EXPRESSIONS_SIZE = (1200, 800)
PLATE_SIZE = (720, 1280)
VARIANT_SIZE = PLATE_SIZE
PROP_SIZE = (1024, 1024)

CHARACTER_IMAGES = ("portrait", "turnaround", "expressions")
CHARACTER_SIZES = {"portrait": PORTRAIT_SIZE, "turnaround": TURNAROUND_SIZE, "expressions": EXPRESSIONS_SIZE}
_CHARACTER_PROMPTS = {
    "portrait": prompting.portrait_prompt,
    "turnaround": prompting.turnaround_prompt,
    "expressions": prompting.expressions_prompt,
}

MASTER_PLATE = schemas.MASTER_PLATE_VARIANT
BASE, REFERENCES, PROMPT_ONLY = "base", "references", "prompt_only"

# The most reference images one edit request carries: local/comfyui's
# multi-reference template has four slots, and more is refused there.
MAX_REFERENCES = 4
# Links that use fewer references than they are sent (images.FalAdapter).
REFERENCES_USED = {"fal/flux-kontext-pro": 1}

NOTE_MAX_CHARS = 300

# The no-call pre-check's own wording (``imaging.estimate``).
READINESS_STEP = "image_edit"
_EDIT_WHAT = "a reference image"
_EDIT_WHEN = "the image is made"

_VARIANT_NAME = re.compile(schemas.TIME_VARIANT_PATTERN)


class RefImageError(Exception):
    """An image that was not made; ``str()`` says why and what to do,
    ``reasons`` lists what each link or check said when there is more."""

    def __init__(self, message, *, reasons=()):
        super().__init__(message)
        self.reasons = list(reasons)


class NeedsEditor(RefImageError):
    """Spec 8.1's "stop and ask": the image needs an editor and no link of
    IMAGE_EDIT_CHAIN can run for the story's route and budget. Raised before
    any generation request; nothing was spent or written. ``reasons`` has
    each link's reason, ``readiness`` the pre-check (:func:`edit_readiness`)."""

    def __init__(self, reasons, readiness, *, subject="This image"):
        super().__init__(
            f"{subject} needs an editor: {readiness['message']} Nothing was generated or spent. "
            "Start ComfyUI, or allow a paid editor, or switch the story to prompt-only consistency.",
            reasons=reasons,
        )
        self.readiness = readiness


@dataclass
class _Plan:
    """One image to make, decided before anything is called."""

    subject: str                # "Kiwilo turnaround", for the log and the errors
    kind: str                   # gen.IMAGE or gen.IMAGE_EDIT
    prompt: str
    size: tuple
    seed: int
    references: tuple
    consistency: str            # base | references | prompt_only
    stem: str                   # the file's name without its extension
    step: str                   # the ledger's step
    via: str                    # how, for the first log line


# ------------------------------------------------------------------ helpers

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(cancel) -> None:
    if cancel is not None:
        cancel.check()


def image_seed(story_id, kind, eid) -> int:
    """The first seed of an entity's images: the same on every run and in
    every process (not ``hash()``, salted per process), 1 .. 2**31-2."""
    digest = hashlib.sha256(f"refimage:{story_id}:{kind}:{eid}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (2**31 - 2) + 1


def _seed_of(ref):
    return ref["seed"] if ref is not None and ref.get("seed") is not None else None


def _check_seed(seed) -> None:
    """A seed override is a whole number >= 0 (``bool`` is not one), or None."""
    if seed is None:
        return
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise RefImageError(f"A seed must be a whole number >= 0, not {seed!r}.")


def _existing(stories, story_id, kind, eid, ref):
    """The real path of *ref*'s file, or None when there is no ref or no such
    regular file (``StoryStore.media_path`` rules)."""
    if ref is None:
        return None
    try:
        return stories.media_path(story_id, kind, eid, ref["name"])
    except KeyError:
        return None


# What an entity's name becomes in a note (no name enters an image prompt).
NEUTRAL_WORDS = {CHARACTERS: "the character", PLACES: "the place", PROPS: "the object"}


def _without_names(text, names) -> str:
    """*text* with every name of *names* replaced, longest first, as a whole
    word and whatever its case. *names* is ``{name: neutral word}``, or an
    iterable of character names ("the character")."""
    if not isinstance(names, dict):
        names = {name: NEUTRAL_WORDS[CHARACTERS] for name in names}
    words = {}
    for name, word in names.items():
        key = str(name).strip() if name else ""
        if key:
            words.setdefault(key, word)
    for name in sorted(words, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", words[name], text, flags=re.IGNORECASE)
    return text


def _entity_names(stories, story_id) -> dict:
    """``{name: neutral word}`` for every character, place and prop of the
    story; a character's word wins when a place or prop has the same name."""
    names = {}
    for kind in (CHARACTERS, PLACES, PROPS):
        for doc in stories.list_entities(story_id, kind):
            names.setdefault(doc["name"], NEUTRAL_WORDS[kind])
    return names


def _with_note(prompt, note, *, stories, story_id) -> str:
    """*prompt*, then ``Author's note: <note>.`` -- after the locked blocks,
    never instead of them. The name of any character, place or prop of the
    story in the note becomes a neutral word (no name enters an image
    prompt, spec 2.3)."""
    if note is None:
        return prompt
    if not isinstance(note, str):
        raise RefImageError("A note must be text.")
    text = " ".join(note.split())
    if not text:
        return prompt
    if len(text) > NOTE_MAX_CHARS:
        raise RefImageError(f"A note is at most {NOTE_MAX_CHARS} characters ({len(text)} given).")
    text = _without_names(text, _entity_names(stories, story_id))
    if text[-1] not in ".!?":
        text += "."
    return f"{prompt} Author's note: {text}"


def _derived(story, *, subject, prompt, size, seed, references, stem, step, source) -> _Plan:
    """An image made from another (a sheet from the portrait, a variant from
    the plate): an edit with *references* in ``references`` mode, the same
    prompt and seed on IMAGE_CHAIN in ``prompt_only`` mode -- the user's
    choice, never made here."""
    if story["generation_profile"]["consistency_mode"] == PROMPT_ONLY:
        return _Plan(subject, gen.IMAGE, prompt, size, seed, (), PROMPT_ONLY, stem, step,
                     f"{gen.ENV_NAMES[gen.IMAGE]}, text only with {source}'s seed {seed}")
    count = len(references)
    return _Plan(subject, gen.IMAGE_EDIT, prompt, size, seed, tuple(references), REFERENCES, stem, step,
                 f"{gen.ENV_NAMES[gen.IMAGE_EDIT]} with {count} reference image{'' if count == 1 else 's'}")


def _remove_other_extensions(stories, story_id, kind, eid, stem, keep) -> None:
    """The slot's file of another extension (``portrait.jpg`` once
    ``portrait.png`` is made): a regular file in the entity's refs/ folder
    only; a symlink is left alone, never followed."""
    try:
        folder = stories.refs_dir(story_id, kind, eid)
    except KeyError:
        return
    for ext in imaging.KEPT_EXTENSIONS:
        name = f"{stem}.{ext}"
        path = os.path.join(folder, name)
        if name == keep or os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            os.remove(path)
        except OSError:
            pass


# ------------------------------------------------------------------ readiness

def edit_readiness(story, *, env, qty=1, stories=None, story_spent=None, adapters=None,
                   size=TURNAROUND_SIZE) -> dict:
    """Whether IMAGE_EDIT_CHAIN can make *qty* reference images for *story*
    right now, calling nothing -- the same shape as the style preview's
    estimate::

        {"step": "image_edit", "est_usd", "units": {"images": qty},
         "route_class": "local" | "free" | "paid" | "blocked",
         "link", "links": [{"link", "status", "reason", "paid", "est_usd"}],
         "ready": bool, "message": str}

    Each link meets the runner's gates in the runner's order: the story's
    route, an adapter, its keys; a local link is "probed when it runs"; a paid
    link needs ``allow_paid`` and the caps (per episode, per day, per story
    with the story's ledger total: *story_spent*, or read from the story's
    ledger when *stories* is given); a free link needs its daily allowance.
    ``ready`` false is spec 8.1's "stop and ask" signal: the image needs an
    editor, or the user's switch to prompt-only consistency.
    """
    if story_spent is None:
        story_spent = 0.0
        if stories is not None:
            ledger = imaging.open_ledger(stories, story["story_id"], error=RefImageError,
                                         doing="making an image")
            story_spent = ledger.totals()["est_usd"]
    request = gen.GenRequest(kind=gen.IMAGE_EDIT, width=size[0], height=size[1])
    return imaging.estimate(gen.IMAGE_EDIT, env, route=story["generation_profile"]["route"], request=request,
                            qty=qty, story_spent=story_spent, adapters=adapters, step=READINESS_STEP,
                            what=_EDIT_WHAT, when=_EDIT_WHEN)


def _probe_locals(readiness, chain, merged, *, route, adapters, transport) -> dict:
    """When the only links the pre-check found runnable are local servers,
    ask each whether it is there (its adapter's probe: no generation, nothing
    spent). None answering makes the readiness blocked -- a server that is
    down is not an editor. Otherwise *readiness* is returned as it was, and
    the runner probes again when it runs."""
    rows = readiness["links"]
    runnable = [row for row in rows if row["status"] == "runnable"]
    if not runnable or any(not row["link"].startswith("local/") for row in runnable):
        return readiness
    links = {gen.describe(link): link for link in chain}
    probed = copy.deepcopy(rows)
    for row in probed:
        if row["status"] != "runnable":
            continue
        link = links.get(row["link"])
        adapter = gen.adapter_for(gen.IMAGE_EDIT, link.provider, adapters) if link is not None else None
        if adapter is None:
            return readiness  # nothing to ask: the runner decides
        kwargs = {"credentials": gen.credentials_for(link, merged), "env": merged}
        if transport is not None:
            kwargs["transport"] = transport
        try:
            ok, note = adapter.probe(link, **kwargs)
        except Exception as exc:  # noqa: BLE001 - a probe that breaks is a server that is not there
            ok, note = False, f"{type(exc).__name__}: {exc}"
        if ok:
            return readiness
        row.update(status="skipped", reason=note or "not reachable")
    message = imaging.no_link_message(gen.IMAGE_EDIT, probed, what=_EDIT_WHAT, route=route)
    return imaging.blocked(READINESS_STEP, readiness["units"]["images"], probed, message)


# ---------------------------------------------------------------------- make

def _make(stories, story, plan, *, entity, eid, lock, env, on_log, cancel, adapters, transport,
          sleep_fn, time_fn):
    """Make one image of *plan* and store its file in the entity's refs/;
    ``(image ref, link label, est_usd, paid)``. The entity's document is the
    caller's to write."""
    story_id = story["story_id"]
    route = story["generation_profile"]["route"]
    merged, chain, budget_obj = imaging.resolve(plan.kind, env, error=RefImageError)
    if adapters is None:
        adapters_mod.load_all()
    ledger = imaging.open_ledger(stories, story_id, error=RefImageError, doing="making an image")

    if plan.kind == gen.IMAGE_EDIT:
        readiness = edit_readiness(story, env=env, story_spent=ledger.totals()["est_usd"], adapters=adapters,
                                   size=plan.size)
        readiness = _probe_locals(readiness, chain, merged, route=route, adapters=adapters, transport=transport)
        if not readiness["ready"]:
            reasons = [f"{row['link']}: {row['reason']}" for row in readiness["links"]] or [readiness["message"]]
            error = NeedsEditor(reasons, readiness, subject=plan.subject)
            on_log(f"✋ {error}")
            raise error

    width, height = plan.size
    on_log(f"🖼 {plan.subject}: {width}x{height} on {plan.via}, route {route}.")
    if plan.consistency == PROMPT_ONLY:
        on_log(f"🟡 {plan.subject}: consistency: prompt-only")
    check = gating.budget_check(budget_obj, story_spent=lambda: ledger.totals()["est_usd"])
    limiter = gating.FreeTierLimiter()
    _check(cancel)

    # The adapter writes into a scratch folder of its own; only an image of a
    # kept type is copied into the entity's refs/.
    with tempfile.TemporaryDirectory(prefix="refimage-") as incoming:
        request = gen.GenRequest(
            kind=plan.kind, prompt=plan.prompt, negative=prompting.negative_prompt(lock), width=width,
            height=height, seed=plan.seed, references=plan.references, out_dir=incoming,
            extra={"name": plan.stem},
        )
        try:
            result, answered = imaging.run_one(
                plan.kind, chain, request, merged=merged, budget_obj=budget_obj, route=route,
                budget_check=check, limiter=limiter, on_log=on_log, cancel=cancel, adapters=adapters,
                transport=transport, sleep_fn=sleep_fn, time_fn=time_fn,
            )
        except imaging.NoImage as exc:
            on_log(f"✖ {plan.subject} not made: {'; '.join(exc.reasons)}")
            raise RefImageError(f"{plan.subject}: no link of {gen.ENV_NAMES[plan.kind]} could make it on route "
                                f"{route}.", reasons=exc.reasons) from None
        except Exception as exc:  # noqa: BLE001 - an adapter's bug fails this image, named
            reason = f"{type(exc).__name__}: {exc}"
            on_log(f"✖ {plan.subject} not made: {reason}")
            raise RefImageError(f"{plan.subject}: {reason}", reasons=[reason]) from None

        # Answered: booked first, whatever becomes of the file.
        est = imaging.book(ledger, result, answered, kind=plan.kind, step=plan.step)
        label = gen.describe(answered)
        try:
            produced, ext = imaging.produced_image(result)
        except imaging.NotKept as exc:
            raise RefImageError(f"{plan.subject}: {label}: {exc}", reasons=[f"{label}: {exc}"]) from None
        name = f"{plan.stem}.{ext}"
        try:
            stories.write_media(story_id, entity, eid, name, produced)
        except (KeyError, ValueError) as exc:
            raise RefImageError(f"{plan.subject}: the image could not be stored ({exc}); its refs/ folder or "
                                "the file in its place is not a real one (a symlink is never followed).") from None

    used = REFERENCES_USED.get(label)
    if used is not None and len(plan.references) > used:
        on_log(f"ℹ️ {plan.subject}: {label} uses only its first {used} reference image(s); "
               f"{len(plan.references) - used} more were sent and not used.")
    seed = result.seed if isinstance(result.seed, int) and not isinstance(result.seed, bool) \
        and result.seed >= 0 else plan.seed
    if (result.meta or {}).get("seed_honoured") is False:
        on_log(f"ℹ️ {plan.subject}: {label} does not honour seeds; seed {seed} is recorded, not reproducible.")
    ref = {"name": name, "consistency": plan.consistency, "source": label, "seed": seed,
           "created_at": _utc_now()}
    return ref, label, est, bool(result.paid)


def _done(on_log, plan, ref, label, est, paid) -> dict:
    on_log(f"🖼 {plan.subject} via {label} (${est:.3f}{' paid' if paid else ''}), "
           f"consistency: {ref['consistency'].replace('_', '-')}")
    return copy.deepcopy(ref)


# ----------------------------------------------------------------- characters

def character_image(stories, story_id, char_id, which, *, env, on_log, cancel, note=None, seed=None,
                    adapters=None, transport=None, sleep_fn=time.sleep, time_fn=time.monotonic) -> dict:
    """Make one of a character's three reference images; returns its image
    ref ``{name, consistency, source, seed, created_at}``.

    *which* is ``portrait`` (IMAGE_CHAIN, text to image, ``base``: the seed is
    the character's ``ref_seed`` or :func:`image_seed`; ``ref_seed`` and
    ``prompt_block`` are set from it), ``turnaround`` or ``expressions``
    (made from the portrait: see :func:`_derived`; ``NeedsEditor`` when no
    editor can run). *env* is the Settings values (merged over the process
    environment here); *note* is appended to the prompt; *seed*, when given,
    is the request's seed instead of the one the image would reuse (a
    regenerate's fresh seed) and is recorded -- a portrait's also becomes
    ``ref_seed``. *adapters*, *transport*, *sleep_fn* and *time_fn* are
    handed to the chain (tests).

    KeyError for an unknown story or character; ``RefImageError`` before
    anything is spent when the character is not written yet, a sheet has no
    portrait, the style lock, a chain, the budget or the ledger cannot be
    used; after a call, when no link made the image (every reason) or its
    file cannot be kept. ``Cancelled`` before the call.
    """
    if which not in CHARACTER_IMAGES:
        raise RefImageError(f"{which!r} is not a character image ({', '.join(CHARACTER_IMAGES)}).")
    _check_seed(seed)
    _check(cancel)
    story = stories.get(story_id)
    character = stories.read_entity(story_id, CHARACTERS, char_id)
    name = character["name"]
    subject = f"{name} {which}"
    if not character["descriptor"] or not character["signature_items"]:
        raise RefImageError(f"{name}: write the character first -- its descriptor and signature items "
                            "make every image.")
    lock = imaging.read_lock(stories, story_id, error=RefImageError)
    prompt = _CHARACTER_PROMPTS[which](lock, descriptor=character["descriptor"],
                                       signature_items=character["signature_items"])
    prompt = _with_note(prompt, note, stories=stories, story_id=story_id)
    step = f"character_image:{char_id}:{which}"
    size = CHARACTER_SIZES[which]

    override = seed
    if which == "portrait":
        seed = override if override is not None else character["ref_seed"]
        if seed is None:
            seed = image_seed(story_id, CHARACTERS, char_id)
        plan = _Plan(subject, gen.IMAGE, prompt, size, seed, (), BASE, which, step,
                     f"{gen.ENV_NAMES[gen.IMAGE]}, text to image")
    else:
        portrait = character["refs"]["portrait"]
        portrait_path = _existing(stories, story_id, CHARACTERS, char_id, portrait)
        if portrait_path is None:
            raise RefImageError(f"{name}: make the portrait first -- the {which} is made from it.")
        seed = override if override is not None else _seed_of(portrait)
        if seed is None:
            seed = character["ref_seed"] if character["ref_seed"] is not None \
                else image_seed(story_id, CHARACTERS, char_id)
        references = [portrait_path]
        for entry in character["refs"]["uploads"]:
            path = _existing(stories, story_id, CHARACTERS, char_id, entry)
            if path is None:
                on_log(f"⚠️ {name}: design reference {entry['name']} is missing on disk; not sent.")
            else:
                references.append(path)
        if len(references) > MAX_REFERENCES:
            left = len(references) - MAX_REFERENCES
            references = references[:MAX_REFERENCES]
            if story["generation_profile"]["consistency_mode"] != PROMPT_ONLY:
                on_log(f"ℹ️ {subject}: {left} design reference(s) left out -- an edit takes at most "
                       f"{MAX_REFERENCES} reference images (the portrait first).")
        plan = _derived(story, subject=subject, prompt=prompt, size=size, seed=seed, references=references,
                        stem=which, step=step, source="the portrait")

    ref, label, est, paid = _make(stories, story, plan, entity=CHARACTERS, eid=char_id, lock=lock, env=env,
                                  on_log=on_log, cancel=cancel, adapters=adapters, transport=transport,
                                  sleep_fn=sleep_fn, time_fn=time_fn)

    # Re-read right before writing, under the uploads' own lock: an upload
    # appended while the image was being made must not be lost.
    with uploads_mod._ENTRIES_LOCK:
        current = stories.read_entity(story_id, CHARACTERS, char_id)
        current["refs"][which] = ref
        if which == "portrait":
            current["ref_seed"] = ref["seed"]
            if current["descriptor"] and current["signature_items"]:
                current["prompt_block"] = prompting.character_prompt_block(
                    lock, descriptor=current["descriptor"], signature_items=current["signature_items"])
        stories.write_entity(story_id, CHARACTERS, current, now=ref["created_at"])
    _remove_other_extensions(stories, story_id, CHARACTERS, char_id, which, ref["name"])
    return _done(on_log, plan, ref, label, est, paid)


# --------------------------------------------------------------------- places

def place_image(stories, story_id, place_id, variant, *, env, on_log, cancel, note=None, seed=None,
                adapters=None, transport=None, sleep_fn=time.sleep, time_fn=time.monotonic) -> dict:
    """Make one time variant of a place; returns its image ref, now
    ``time_variants[variant]``.

    ``day`` is the master plate (IMAGE_CHAIN, text to image, ``base``; the
    place's ``prompt_block`` is set when its layout notes are written). Any
    other variant name (``schemas.TIME_VARIANT_PATTERN``) is made from the
    plate, its single reference image (:func:`_derived`; ``NeedsEditor`` when
    no editor can run) with the plate's seed. *seed* overrides the seed as
    in :func:`character_image`; the rules and errors of
    :func:`character_image` otherwise.
    """
    if not isinstance(variant, str) or _VARIANT_NAME.fullmatch(variant) is None:
        raise RefImageError(f"{variant!r} is not a time variant name (lowercase letters, digits and _, "
                            "starting with a letter: night, golden_hour...).")
    _check_seed(seed)
    _check(cancel)
    story = stories.get(story_id)
    place = stories.read_entity(story_id, PLACES, place_id)
    name = place["name"]
    subject = f"{name} {variant}"
    if not place["descriptor"]:
        raise RefImageError(f"{name}: write the place first -- its descriptor makes every image.")
    lock = imaging.read_lock(stories, story_id, error=RefImageError)
    prompt = prompting.variant_prompt(lock, place_descriptor=place["descriptor"], variant=variant)
    prompt = _with_note(prompt, note, stories=stories, story_id=story_id)
    step = f"place_image:{place_id}:{variant}"
    stem = f"variant_{variant}"
    plate = place["time_variants"].get(MASTER_PLATE)
    override = seed

    if variant == MASTER_PLATE:
        seed = override if override is not None else _seed_of(plate)
        if seed is None:
            seed = image_seed(story_id, PLACES, place_id)
        plan = _Plan(subject, gen.IMAGE, prompt, PLATE_SIZE, seed, (), BASE, stem, step,
                     f"{gen.ENV_NAMES[gen.IMAGE]}, text to image")
    else:
        plate_path = _existing(stories, story_id, PLACES, place_id, plate)
        if plate_path is None:
            raise RefImageError(f"{name}: make the master plate ({MASTER_PLATE}) first -- every other "
                                "variant is made from it.")
        seed = override if override is not None else _seed_of(plate)
        if seed is None:
            seed = image_seed(story_id, PLACES, place_id)
        plan = _derived(story, subject=subject, prompt=prompt, size=VARIANT_SIZE, seed=seed,
                        references=[plate_path], stem=stem, step=step, source="the master plate")

    ref, label, est, paid = _make(stories, story, plan, entity=PLACES, eid=place_id, lock=lock, env=env,
                                  on_log=on_log, cancel=cancel, adapters=adapters, transport=transport,
                                  sleep_fn=sleep_fn, time_fn=time_fn)

    current = stories.read_entity(story_id, PLACES, place_id)
    current["time_variants"][variant] = ref
    if variant == MASTER_PLATE and current["descriptor"] and current["layout_notes"]:
        current["prompt_block"] = prompting.place_prompt_block(
            lock, descriptor=current["descriptor"], layout_notes=current["layout_notes"])
    stories.write_entity(story_id, PLACES, current, now=ref["created_at"])
    _remove_other_extensions(stories, story_id, PLACES, place_id, stem, ref["name"])
    return _done(on_log, plan, ref, label, est, paid)


# ---------------------------------------------------------------------- props

def prop_image(stories, story_id, prop_id, *, env, on_log, cancel, note=None, seed=None, adapters=None,
               transport=None, sleep_fn=time.sleep, time_fn=time.monotonic) -> dict:
    """Make a prop's image (IMAGE_CHAIN, text to image, ``base``, saved as
    ``image.<ext>``; its ``prompt_block`` is set); returns its image ref.
    *seed* overrides the seed as in :func:`character_image`; the rules and
    errors of :func:`character_image` otherwise."""
    _check_seed(seed)
    _check(cancel)
    story = stories.get(story_id)
    prop = stories.read_entity(story_id, PROPS, prop_id)
    name = prop["name"]
    subject = f"{name} image"
    if not prop["descriptor"]:
        raise RefImageError(f"{name}: write the prop first -- its descriptor makes its image.")
    lock = imaging.read_lock(stories, story_id, error=RefImageError)
    prompt = _with_note(prompting.prop_image_prompt(lock, descriptor=prop["descriptor"]), note,
                        stories=stories, story_id=story_id)
    if seed is None:
        seed = _seed_of(prop["image"])
    if seed is None:
        seed = image_seed(story_id, PROPS, prop_id)
    plan = _Plan(subject, gen.IMAGE, prompt, PROP_SIZE, seed, (), BASE, "image", f"prop_image:{prop_id}",
                 f"{gen.ENV_NAMES[gen.IMAGE]}, text to image")

    ref, label, est, paid = _make(stories, story, plan, entity=PROPS, eid=prop_id, lock=lock, env=env,
                                  on_log=on_log, cancel=cancel, adapters=adapters, transport=transport,
                                  sleep_fn=sleep_fn, time_fn=time_fn)

    current = stories.read_entity(story_id, PROPS, prop_id)
    current["image"] = ref
    if current["descriptor"]:
        current["prompt_block"] = prompting.prop_prompt_block(lock, descriptor=current["descriptor"])
    stories.write_entity(story_id, PROPS, current, now=ref["created_at"])
    _remove_other_extensions(stories, story_id, PROPS, prop_id, "image", ref["name"])
    return _done(on_log, plan, ref, label, est, paid)
