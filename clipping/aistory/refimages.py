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
nothing is spent, no file or document changes. The story page and the cast
and places estimates ask the same question ahead of time, so the user sees
it before pressing Continue: ``edit_readiness(..., probe_local=True)`` asks a
local editor that would run whether it is there (a short status probe,
remembered per server for a minute); the pre-check here probes afresh. The
mode is never switched here. Only the user sets the story's
``consistency_mode`` to ``prompt_only``; every image then made from text
alone is labelled so, and printed with 🟡.

**Reference images per link.** At most :data:`MAX_REFERENCES` are sent: the
portrait first, then the oldest design references (a place variant sends its
master plate alone). ``local/comfyui``'s multi-reference template has four
slots; ``fal/seedream-4-edit`` and Gemini take them all (Gemini does not
honour a seed); ``fal/flux-kontext-pro`` uses only the first -- the portrait
(:data:`REFERENCES_USED`). The link that answered is the image's ``source``,
and a line says when it used fewer references than it was sent.

Prompts come from ``prompting.py`` and never carry a name (spec 2.3):
characters are drawn from their descriptor and signature items. On a v2
story (phase 7) an entity with a ``look`` is drawn from it instead, through
the v2 builders (``portrait/turnaround/expressions/plate/prop_prompt_v2``
over ``shots.render_look/render_place/render_prop``); the turnaround and the
expressions are edits of the portrait whose prompt says so first. A legacy
story, or an entity with no look, gets the legacy builders byte for byte. A
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
from clipping.providers.registry import ChainError

from . import defaults, imaging, media_policy, prompt_budgets, prompting, schemas, shots
from . import names as names_mod
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

# Plan 23 stage D4: a two-view sheet (the front and the back side by side, in
# the portrait slot) is asked at the render's own 1080x1920; flat-priced per
# image on Seedream, which scales every request up to 1440x2560 alike.
TWO_VIEW_SIZE = (1080, 1920)

# Every slot a character's image can be in; a story's own are
# :func:`character_images` (its ``sheet_mode``).
CHARACTER_IMAGES = ("portrait", "turnaround", "expressions")
CHARACTER_SIZES = {"portrait": PORTRAIT_SIZE, "turnaround": TURNAROUND_SIZE, "expressions": EXPRESSIONS_SIZE}


def character_images(story) -> tuple:
    """The images *story* draws for each character, in order (plan 23 stage
    D4, ``media_policy.sheet_mode``): the portrait, turnaround and
    expressions sheet (``three_sheet``, absent); the portrait alone -- one
    front+back sheet -- in ``two_view``; the portrait and the expressions
    sheet in ``two_view_expressions``."""
    mode = media_policy.sheet_mode(story)
    if mode == defaults.SHEET_TWO_VIEW:
        return CHARACTER_IMAGES[:1]
    if mode == defaults.SHEET_TWO_VIEW_EXPRESSIONS:
        return ("portrait", "expressions")
    return CHARACTER_IMAGES


def character_sheets(story) -> tuple:
    """:func:`character_images` after the portrait: the sheets *story* draws
    from it, each an edit of it in ``references`` mode."""
    return character_images(story)[1:]


def character_size(story, which) -> tuple:
    """The size of a character's image *which* in *story*: the two-view
    sheet (the portrait slot of a two-view mode) at :data:`TWO_VIEW_SIZE`,
    else :data:`CHARACTER_SIZES`."""
    if which == "portrait" and media_policy.two_view(story):
        return TWO_VIEW_SIZE
    return CHARACTER_SIZES[which]
_CHARACTER_PROMPTS = {
    "portrait": prompting.portrait_prompt,
    "turnaround": prompting.turnaround_prompt,
    "expressions": prompting.expressions_prompt,
}
# Phase 7: a v2 story's character with a look is drawn from it (A8).
_CHARACTER_PROMPTS_V2 = {
    "portrait": prompting.portrait_prompt_v2,
    "turnaround": prompting.turnaround_prompt_v2,
    "expressions": prompting.expressions_prompt_v2,
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
    ``reasons`` lists what each link or check said when there is more.

    ``failures`` is the chain's raw ``(label, reason)`` pairs
    (``NoRunnableLink.failures``, passed through from ``imaging.NoImage`` when
    there is a chain run behind this; empty for every other refusal -- a
    style lock, a chain or a budget that cannot be used, before anything is
    spent), what :func:`pacing.rate_limited_by` reads."""

    def __init__(self, message, *, reasons=(), failures=()):
        super().__init__(message)
        self.reasons = list(reasons)
        self.failures = tuple(failures)


# DEC-117's offer, word for word (a legacy story).
LEGACY_EDITOR_ADVICE = "Start ComfyUI, or allow a paid editor, or switch the story to prompt-only consistency."


def quality_advice(readiness) -> str:
    """What a v2 story's stop-and-ask asks for (DEC-221): the quality keys and
    ``allow_paid``, with the estimate of the first paid link -- never the
    prompt-only switch, which a v2 story does not have."""
    paid = next((row for row in readiness.get("links") or () if row.get("paid")), None)
    estimate = ""
    if paid is not None:
        qty = (readiness.get("units") or {}).get("images", 1)
        estimate = (f" (est ${paid['est_usd']:.3f} for {qty} image{'' if qty == 1 else 's'} on "
                    f"{paid['link']})")
    return (f"Add {' and '.join(media_policy.QUALITY_KEYS)} in Settings and allow paid providers{estimate}: "
            "this story's images run on quality links only.")


def editor_advice(story, readiness) -> str:
    """The last sentence of a stop-and-ask: DEC-117's offer for a legacy
    story, :func:`quality_advice` for a v2 one."""
    return quality_advice(readiness) if media_policy.is_v2(story) else LEGACY_EDITOR_ADVICE


class NeedsEditor(RefImageError):
    """Spec 8.1's "stop and ask": the image needs an editor and no link of
    IMAGE_EDIT_CHAIN can run for the story's route and budget. Raised before
    any generation request; nothing was spent or written. ``reasons`` has
    each link's reason, ``readiness`` the pre-check (:func:`edit_readiness`).

    For a v2 *story* (phase 7, DEC-221) it says that no quality image link can
    run, each link's reason, and the keys and the switch to add with the
    estimate -- never the prompt-only offer. A legacy story's words are
    DEC-117's, unchanged."""

    def __init__(self, reasons, readiness, *, subject="This image", story=None):
        if media_policy.is_v2(story):
            why = "; ".join(reasons) or readiness["message"]
            message = (f"{subject} cannot be made: no quality image link can run ({why}). Nothing was "
                       f"generated or spent. {quality_advice(readiness)}")
        else:
            message = (f"{subject} needs an editor: {readiness['message']} Nothing was generated or spent. "
                       f"{LEGACY_EDITOR_ADVICE}")
        super().__init__(message, reasons=reasons)
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
    role: str = "sheet"         # media_policy's role: sheet | plate | prop (phase 7)


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
    iterable of character names ("the character").

    Delegates to the shared :func:`names.without_names` (lifted out for
    ``shots.py``, phase 3 stage 5); behaviour is unchanged (RC-E5)."""
    return names_mod.without_names(text, names, default_word=NEUTRAL_WORDS[CHARACTERS])


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


def _derived(story, *, subject, prompt, size, seed, references, stem, step, source, role) -> _Plan:
    """An image made from another (a sheet from the portrait, a variant from
    the plate): an edit with *references* in ``references`` mode, the same
    prompt and seed on IMAGE_CHAIN in ``prompt_only`` mode -- the user's
    choice, never made here."""
    if story["generation_profile"]["consistency_mode"] == PROMPT_ONLY:
        return _Plan(subject, gen.IMAGE, prompt, size, seed, (), PROMPT_ONLY, stem, step,
                     f"{gen.ENV_NAMES[gen.IMAGE]}, text only with {source}'s seed {seed}", role=role)
    count = len(references)
    return _Plan(subject, gen.IMAGE_EDIT, prompt, size, seed, tuple(references), REFERENCES, stem, step,
                 f"{media_policy.chain_name(role, gen.IMAGE_EDIT, story)} with {count} reference "
                 f"image{'' if count == 1 else 's'}", role=role)


def _first_link(story, role, kind, env):
    """The first link an image of *role* and *kind* goes to on *story*
    (``media_policy.role_chain``): what its prompt's word budget is read
    from (stage F2, ``prompt_budgets``). None when the chain cannot be read
    -- :func:`_make` then says why, before anything is sent."""
    try:
        chain = media_policy.role_chain(role, kind, gating.merged_env(env), story)
    except ChainError:
        return None
    return gen.describe(chain[0]) if chain else None


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
                   size=TURNAROUND_SIZE, probe_local=False, transport=None, role="sheet") -> dict:
    """Whether IMAGE_EDIT_CHAIN can make *qty* reference images for *story*
    right now, calling nothing unless *probe_local* -- the same shape as the
    style preview's estimate::

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

    *probe_local* (the story page, the cast and places estimates): when a
    local link would be the one to run *qty* >= 1 images, it is asked whether
    it is there (:func:`_ask_locals_first`: its adapter's short status probe,
    remembered per server for a minute; never a generation). One that does
    not answer is ``skipped`` with the probe's reason and the next runnable
    link decides -- blocked when none is left. *transport* is handed to that
    probe (tests). The runner's own pre-check (:func:`_make`) leaves it off
    and probes afresh.

    *role* (phase 7): the images' ``media_policy`` role -- a v2 story's
    edits run on that role's quality links; a legacy story's on
    IMAGE_EDIT_CHAIN whatever the role.
    """
    if story_spent is None:
        story_spent = 0.0
        if stories is not None:
            ledger = imaging.open_ledger(stories, story["story_id"], error=RefImageError,
                                         doing="making an image")
            story_spent = ledger.totals()["est_usd"]
    route = story["generation_profile"]["route"]
    request = gen.GenRequest(kind=gen.IMAGE_EDIT, width=size[0], height=size[1])
    readiness = imaging.estimate(gen.IMAGE_EDIT, env, route=route, request=request, qty=qty,
                                 story_spent=story_spent, adapters=adapters, step=READINESS_STEP,
                                 what=_EDIT_WHAT, when=_EDIT_WHEN, role=role, story=story)
    if probe_local and qty > 0 and readiness["ready"]:
        readiness = _ask_locals_first(readiness, env, route=route, adapters=adapters, transport=transport,
                                      role=role, story=story)
    return readiness


def _ask_status(adapter, link, merged, transport):
    """A local adapter's answer to "are you there": its cached status probe
    (``probe_cached``) when it has one, else its ``probe``; ``(ok, note)``."""
    probe = getattr(adapter, "probe_cached", None) or adapter.probe
    kwargs = {"credentials": gen.credentials_for(link, merged), "env": merged}
    if transport is not None:
        kwargs["transport"] = transport
    try:
        return probe(link, **kwargs)
    except Exception as exc:  # noqa: BLE001 - a probe that breaks is a server that is not there
        return False, f"{type(exc).__name__}: {exc}"


def _ask_locals_first(readiness, env, *, route, adapters, transport, role="sheet", story=None) -> dict:
    """``edit_readiness(probe_local=True)``: while the link that would run
    first is a local server, ask it whether it is there (:func:`_ask_status`).
    One that does not answer is ``skipped`` with the probe's reason, as the
    runner would skip it, and the verdict is read again from the rows
    (``imaging.verdict``): the next runnable link's, or blocked when none is
    left. The first local link that answers -- or a first runnable link that
    is not local -- leaves *readiness* as it was."""
    merged = gating.merged_env(env)
    links = {gen.describe(link): link for link in media_policy.role_chain(role, gen.IMAGE_EDIT, merged, story)}
    rows = copy.deepcopy(readiness["links"])
    skipped = False
    for row in rows:
        if row["status"] != "runnable":
            continue
        link = links.get(row["link"])
        adapter = gen.adapter_for(gen.IMAGE_EDIT, link.provider, adapters) if link is not None else None
        if link is None or link.provider != "local" or adapter is None:
            break
        ok, note = _ask_status(adapter, link, merged, transport)
        if ok:
            break
        row.update(status="skipped", reason=note or "not reachable")
        skipped = True
    if not skipped:
        return readiness
    name = media_policy.chain_name(role, gen.IMAGE_EDIT, story)
    return imaging.verdict(gen.IMAGE_EDIT, rows, route=route, qty=readiness["units"]["images"],
                           step=READINESS_STEP, what=_EDIT_WHAT, when=_EDIT_WHEN,
                           chain_name=None if name == gen.ENV_NAMES[gen.IMAGE_EDIT] else name)


def _probe_locals(readiness, chain, merged, *, route, adapters, transport, chain_name=None) -> dict:
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
    message = imaging.no_link_message(gen.IMAGE_EDIT, probed, what=_EDIT_WHAT, route=route, chain_name=chain_name)
    return imaging.blocked(READINESS_STEP, readiness["units"]["images"], probed, message)


# ---------------------------------------------------------------------- make

def _make(stories, story, plan, *, entity, eid, lock, env, on_log, cancel, adapters, transport,
          sleep_fn, time_fn, sticky=None):
    """Make one image of *plan* and store its file in the entity's refs/;
    ``(image ref, link label, est_usd, paid)``. The entity's document is the
    caller's to write. *sticky*, a dict one cast or places job shares (DEC-280): the
    provider that answered first is tried first for the rest of the job, so one cast
    does not drift between providers once a link has been refused."""
    story_id = story["story_id"]
    route = story["generation_profile"]["route"]
    merged, chain, budget_obj = imaging.resolve(plan.kind, env, error=RefImageError, role=plan.role, story=story)
    if sticky and sticky.get("provider"):
        chain = sorted(chain, key=lambda link: link.provider != sticky["provider"])  # stable
    name = media_policy.chain_name(plan.role, plan.kind, story)
    if adapters is None:
        adapters_mod.load_all()
    ledger = imaging.open_ledger(stories, story_id, error=RefImageError, doing="making an image")

    if plan.kind == gen.IMAGE_EDIT:
        readiness = edit_readiness(story, env=env, story_spent=ledger.totals()["est_usd"], adapters=adapters,
                                   size=plan.size, role=plan.role)
        readiness = _probe_locals(readiness, chain, merged, route=route, adapters=adapters, transport=transport,
                                  chain_name=None if name == gen.ENV_NAMES[plan.kind] else name)
        if not readiness["ready"]:
            reasons = [f"{row['link']}: {row['reason']}" for row in readiness["links"]] or [readiness["message"]]
            error = NeedsEditor(reasons, readiness, subject=plan.subject, story=story)
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
            raise RefImageError(f"{plan.subject}: no link of {name} could make it on route "
                                f"{route}.", reasons=exc.reasons, failures=exc.failures) from None
        except gen.AwaitingUpload:
            # Plan 22 stage 5: the story's images are the user's own -- nothing was sent or booked.
            reason = (f"your own image ({gen.MANUAL_LINK}): make it from the image brief and upload it on its "
                      "tile; nothing was sent or booked")
            on_log(f"✋ {plan.subject}: {reason}")
            raise RefImageError(f"{plan.subject}: {reason}.", reasons=[reason]) from None
        except Exception as exc:  # noqa: BLE001 - an adapter's bug fails this image, named
            reason = f"{type(exc).__name__}: {exc}"
            on_log(f"✖ {plan.subject} not made: {reason}")
            raise RefImageError(f"{plan.subject}: {reason}", reasons=[reason]) from None

        # Answered: booked first, whatever becomes of the file.
        est = imaging.book(ledger, result, answered, kind=plan.kind, step=plan.step)
        label = gen.describe(answered)
        if sticky is not None:
            sticky.setdefault("provider", answered.provider)
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

def character_prompt(story, character, which, *, env, lock) -> str:
    """The prompt of a character's image *which*, as :func:`character_image`
    asks it (the shot brief's image part reads it too, plan 22 stage 5)."""
    if media_policy.is_v2(story) and character.get("look"):
        # Stage F2: the sheet fills the budget of the link it goes to (the portrait text to image, the
        # others edits of it -- text to image too in prompt-only mode, _derived).
        edit = which != "portrait" and story["generation_profile"]["consistency_mode"] != PROMPT_ONLY
        link = _first_link(story, "sheet", gen.IMAGE_EDIT if edit else gen.IMAGE, env)
        if which == "portrait" and media_policy.two_view(story):
            # Plan 23 stage D4: the portrait slot holds the front+back sheet.
            return prompting.two_view_prompt_v2(lock, look_text=shots.render_look(character),
                                                signature_items=character["signature_items"],
                                                budget=prompt_budgets.two_view_words(link),
                                                cues=shots.visual_cues(character))
        return _CHARACTER_PROMPTS_V2[which](lock, look_text=shots.render_look(character),
                                            signature_items=character["signature_items"],
                                            budget=prompt_budgets.sheet_words(link),
                                            cues=shots.visual_cues(character))
    return _CHARACTER_PROMPTS[which](lock, descriptor=character["descriptor"],
                                     signature_items=character["signature_items"])


def character_image(stories, story_id, char_id, which, *, env, on_log, cancel, note=None, seed=None,
                    adapters=None, transport=None, sleep_fn=time.sleep, time_fn=time.monotonic,
                    sticky=None) -> dict:
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
    if which not in character_images(story):
        raise RefImageError(f"{which!r} is not drawn in this story's sheet mode "
                            f"({media_policy.sheet_mode(story)}: {', '.join(character_images(story))}).")
    character = stories.read_entity(story_id, CHARACTERS, char_id)
    name = character["name"]
    subject = f"{name} {which}"
    if not character["descriptor"] or not character["signature_items"]:
        raise RefImageError(f"{name}: write the character first -- its descriptor and signature items "
                            "make every image.")
    lock = imaging.read_lock(stories, story_id, error=RefImageError)
    prompt = character_prompt(story, character, which, env=env, lock=lock)
    prompt = _with_note(prompt, note, stories=stories, story_id=story_id)
    step = f"character_image:{char_id}:{which}"
    size = character_size(story, which)

    override = seed
    if which == "portrait":
        seed = override if override is not None else character["ref_seed"]
        if seed is None:
            seed = image_seed(story_id, CHARACTERS, char_id)
        plan = _Plan(subject, gen.IMAGE, prompt, size, seed, (), BASE, which, step,
                     f"{media_policy.chain_name('sheet', gen.IMAGE, story)}, text to image", role="sheet")
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
                        stem=which, step=step, source="the portrait", role="sheet")

    ref, label, est, paid = _make(stories, story, plan, entity=CHARACTERS, eid=char_id, lock=lock, env=env,
                                  on_log=on_log, cancel=cancel, adapters=adapters, transport=transport,
                                  sleep_fn=sleep_fn, time_fn=time_fn, sticky=sticky)

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

def place_prompt(stories, story, place, variant, *, env, lock) -> str:
    """The prompt of a place's *variant* image, as :func:`place_image` asks
    it (the shot brief's image part reads it too, plan 22 stage 5)."""
    if media_policy.is_v2(story) and place.get("look"):
        place_text = shots.render_place(place, variant, "wide_establishing",
                                        props=_props_here(stories, story["story_id"], place["look"]))
        # Stage F2: the plate fills the budget of its link (a variant is an edit of the master plate).
        edit = variant != MASTER_PLATE and story["generation_profile"]["consistency_mode"] != PROMPT_ONLY
        link = _first_link(story, "plate", gen.IMAGE_EDIT if edit else gen.IMAGE, env)
        return prompting.plate_prompt_v2(lock, place_text=place_text, variant=variant,
                                         budget=prompt_budgets.plate_words(link))
    return prompting.variant_prompt(lock, place_descriptor=place["descriptor"], variant=variant)


def place_image(stories, story_id, place_id, variant, *, env, on_log, cancel, note=None, seed=None,
                adapters=None, transport=None, sleep_fn=time.sleep, time_fn=time.monotonic,
                sticky=None) -> dict:
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
    prompt = place_prompt(stories, story, place, variant, env=env, lock=lock)
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
                     f"{media_policy.chain_name('plate', gen.IMAGE, story)}, text to image", role="plate")
    else:
        plate_path = _existing(stories, story_id, PLACES, place_id, plate)
        if plate_path is None:
            raise RefImageError(f"{name}: make the master plate ({MASTER_PLATE}) first -- every other "
                                "variant is made from it.")
        seed = override if override is not None else _seed_of(plate)
        if seed is None:
            seed = image_seed(story_id, PLACES, place_id)
        plan = _derived(story, subject=subject, prompt=prompt, size=VARIANT_SIZE, seed=seed,
                        references=[plate_path], stem=stem, step=step, source="the master plate", role="plate")

    ref, label, est, paid = _make(stories, story, plan, entity=PLACES, eid=place_id, lock=lock, env=env,
                                  on_log=on_log, cancel=cancel, adapters=adapters, transport=transport,
                                  sleep_fn=sleep_fn, time_fn=time_fn, sticky=sticky)

    current = stories.read_entity(story_id, PLACES, place_id)
    current["time_variants"][variant] = ref
    if variant == MASTER_PLATE and current["descriptor"] and current["layout_notes"]:
        current["prompt_block"] = prompting.place_prompt_block(
            lock, descriptor=current["descriptor"], layout_notes=current["layout_notes"])
    stories.write_entity(story_id, PLACES, current, now=ref["created_at"])
    _remove_other_extensions(stories, story_id, PLACES, place_id, stem, ref["name"])
    return _done(on_log, plan, ref, label, est, paid)


def _props_here(stories, story_id, look) -> list:
    """The written prop documents a place's look says live there (set
    dressing on its plate); a prop gone or not written yet is left out."""
    props = []
    for prop_id in look.get("props_here") or ():
        try:
            prop = stories.read_entity(story_id, PROPS, prop_id)
        except (KeyError, schemas.SchemaError):
            continue
        if prop["descriptor"]:
            props.append(prop)
    return props


# ---------------------------------------------------------------------- props

def prop_prompt(story, prop, *, env, lock) -> str:
    """The prompt of a prop's image, as :func:`prop_image` asks it (the shot
    brief's image part reads it too, plan 22 stage 5)."""
    if media_policy.is_v2(story) and prop.get("look"):
        # Stage F2: the prop's reference fills the budget of its link.
        return prompting.prop_prompt_v2(lock, prop_text=shots.render_prop(prop, for_reference=True),
                                        budget=prompt_budgets.prop_words(_first_link(story, "prop", gen.IMAGE, env)))
    return prompting.prop_image_prompt(lock, descriptor=prop["descriptor"])


def prop_image(stories, story_id, prop_id, *, env, on_log, cancel, note=None, seed=None, adapters=None,
               transport=None, sleep_fn=time.sleep, time_fn=time.monotonic, sticky=None) -> dict:
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
    prompt = prop_prompt(story, prop, env=env, lock=lock)
    prompt = _with_note(prompt, note, stories=stories, story_id=story_id)
    if seed is None:
        seed = _seed_of(prop["image"])
    if seed is None:
        seed = image_seed(story_id, PROPS, prop_id)
    plan = _Plan(subject, gen.IMAGE, prompt, PROP_SIZE, seed, (), BASE, "image", f"prop_image:{prop_id}",
                 f"{media_policy.chain_name('prop', gen.IMAGE, story)}, text to image", role="prop")

    ref, label, est, paid = _make(stories, story, plan, entity=PROPS, eid=prop_id, lock=lock, env=env,
                                  on_log=on_log, cancel=cancel, adapters=adapters, transport=transport,
                                  sleep_fn=sleep_fn, time_fn=time_fn, sticky=sticky)

    current = stories.read_entity(story_id, PROPS, prop_id)
    current["image"] = ref
    if current["descriptor"]:
        current["prompt_block"] = prompting.prop_prompt_block(lock, descriptor=current["descriptor"])
    stories.write_entity(story_id, PROPS, current, now=ref["created_at"])
    _remove_other_extensions(stories, story_id, PROPS, prop_id, "image", ref["name"])
    return _done(on_log, plan, ref, label, est, paid)
