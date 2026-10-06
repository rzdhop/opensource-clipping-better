"""Step ``regenerate``: one piece again, with an optional note (spec 3, 9.2).

``params = {"target": <target>, "note": str | None, "voice": {...} | None}``.

Phase 1:

- ``concepts`` is the concepts step itself ("10 more"), the note joining the
  seed as "Author's note: ...". It needs no chosen concept: concepts are how
  one gets chosen.
- ``bible:<field>`` (``prompts.REGENERATE_TARGETS``) re-runs the one prompt
  that owns the field, shown its current values and told to rewrite only
  that field; whatever else the model sends back, only the target's keys are
  applied (``tone`` also carries ``genre_tags``; ``world`` and ``themes`` are
  a whole prompt). The bible approval is cleared, as by the bible step.

Phase 2 (the entity targets; each touches its own item only):

- ``character:<id>:text`` -- K1 again with the note (``cast.write_text``:
  each design reference not yet described is described first, a failure
  printed and K1 run without it); the images and the pinned voice stay, the
  voice brief is rewritten. On a v2 story (phase 7) every ``<kind>:<id>:text``
  writes the entity's look again right after its text (D2, D3 or R1v2, shown
  the current look and the note): no separate target.
- ``character:<id>:image:portrait`` -- a fresh seed and the note; then the
  turnaround and the expressions sheet **that already existed** are made
  again from the new portrait (they were drawn from the old one). A sheet
  with no editor to make it is recorded (``needs_editor``), not failed; it
  keeps its old image until it is regenerated. ``...:image:turnaround`` and
  ``...:image:expressions`` -- that sheet only, a fresh seed and the note.
  ``...:image:extra:<n>`` arrives in a later phase.
- ``character:<id>:variant:<variant_id>`` (plan 23 stage D5) -- every sheet of
  that appearance variant the story's sheet mode draws, each an edit of the
  base portrait (``refimages.variant_image``), a fresh seed and the note; the
  variant's ``approved_at`` is cleared, the character's own approval never
  (the job ends awaiting ``variant:<id>:<variant_id>``).
- ``character:<id>:voice`` -- ``params.voice = {provider, voice_id, rate?,
  pitch?}`` (a voice of the story's language on ``TTS_CHAIN``, not another
  lead's or support's) or, without it, the best other voice; pinned (rate and
  pitch as given, else as they were), then a new sample.
- ``place:<id>:text`` -- P1 again; ``place:<id>:image:<variant>`` -- ``day``
  or one of ``schemas.TIME_VARIANT_CHOICES`` (a variant the place did not have
  is added), a fresh seed and the note.
- ``prop:<id>:text`` -- R1 again (its owner replaces the current one);
  ``prop:<id>:image`` -- a fresh seed and the note.
- ``season:<ep>`` -- S2 again for that arc entry.

Phase 3 (the episode targets, ``episode_regenerate``): ``scene:<ep>:<sid>``,
``hook:<ep>``, ``cliffhanger:<ep>``, ``teaser:<ep>`` and
``shot:<ep>:<shid>:plan``. :func:`parse_target` reads them with the others:
one grammar for the web layer, the CLI and this runner.

Phase 4 (``episode_regenerate`` too): ``shot:<ep>:<shid>`` -- the shot's
image, tuple kind ``shot_image`` (DEC-140: ``:plan`` is read first) --,
``line:<ep>:<lid>`` -- the line's voice, kind ``line`` -- and
``metadata:<ep>:<platform>`` -- one platform's metadata, kind ``metadata``,
``platform`` one of ``schemas.PLATFORMS``. Phase 6 (stage 8):
``shot:<ep>:<shid>:video`` -- the shot's clip, kind ``shot_video``; any other
``shot:<ep>:<shid>:<word>`` is still a later phase's.

Every entity regenerate clears that entity's ``approved_at`` -- an approval
never outlives what it approved; ``approvals.cast``/``places`` re-fold as the
entity is written -- and ``season:<ep>`` clears ``approvals.season``. An
unknown id, a malformed target or one of another phase is refused, naming
the valid shapes.
"""

from __future__ import annotations

import re
import time

from .. import media_policy, prompts, refimages, schemas, voice_reference, voices
from .. import store as store_mod
from . import bible, concepts, entities, llm_call, sheet_gate
from .entities import CHARACTERS, PLACES, PROPS
from .llm_call import StepFailed

BIBLE_PREFIX = "bible:"
CONCEPTS_TARGET = "concepts"

# Phase 1's fixed targets, matched exactly; phase 2's are shapes
# (``parse_target``). ``workflow.check_regenerate_target`` accepts both.
VALID_TARGETS = tuple(f"{BIBLE_PREFIX}{field}" for field in prompts.REGENERATE_TARGETS) + (CONCEPTS_TARGET,)

# The episode targets (spec 9.2; ``episode_regenerate`` runs them): phase 3's
# text and shot plans, phase 4's shot images, line voices and platforms.
# ``EPISODE_KINDS`` are the first words of their tuples (:func:`parse_target`):
# ``shot:<ep>:<shot_id>`` is ``("shot_image", ep, shot_id)``, told apart from
# its ``:plan`` (``("shot", ep, shot_id)``).
EPISODE_TARGETS = (
    "scene:<ep>:<scene_id>",
    "hook:<ep>",
    "cliffhanger:<ep>",
    "teaser:<ep>",
    "shot:<ep>:<shot_id>:plan",
    "shot:<ep>:<shot_id>",
    "shot:<ep>:<shot_id>:video",
    "line:<ep>:<line_id>",
    f"metadata:<ep>:{'|'.join(schemas.PLATFORMS)}",
)
FRAMING_TARGETS = ("hook", "cliffhanger", "teaser")
SHOT_IMAGE_KIND = "shot_image"
SHOT_VIDEO_KIND = "shot_video"
LINE_KIND = "line"
METADATA_KIND = "metadata"
EPISODE_KINDS = ("scene",) + FRAMING_TARGETS + ("shot", SHOT_IMAGE_KIND, SHOT_VIDEO_KIND, LINE_KIND, METADATA_KIND)

# The entity and episode target shapes (spec 9.2) -- every shape
# :func:`parse_target` reads -- as a refusal names them.
PLACE_VARIANTS = tuple(dict.fromkeys((schemas.MASTER_PLATE_VARIANT,) + schemas.TIME_VARIANT_CHOICES))
ENTITY_TARGETS = (
    "character:<char_id>:text",
    "character:<char_id>:image:portrait|turnaround|expressions",
    "character:<char_id>:voice",
    "character:<char_id>:variant:<variant_id>",
    "place:<place_id>:text",
    f"place:<place_id>:image:{'|'.join(PLACE_VARIANTS)}",
    "prop:<prop_id>:text",
    "prop:<prop_id>:image",
    "season:<ep>",
) + EPISODE_TARGETS
TARGET_SHAPES = VALID_TARGETS + ENTITY_TARGETS

_KINDS = {"character": CHARACTERS, "place": PLACES, "prop": PROPS}
_EP = re.compile(r"^[1-9][0-9]{0,2}$")
# An episode's number in a target: the store's 1..99 (``store.EPISODE_MAX``).
_EPISODE = re.compile(r"^[1-9][0-9]?$")
_SCENE = re.compile(schemas.SCENE_ID_PATTERN)
_SHOT = re.compile(schemas.SHOT_ID_PATTERN)
_LINE = re.compile(schemas.LINE_ID_PATTERN)
_EXTRA = re.compile(r"^extra:[0-9]+$")
_VARIANT = re.compile(schemas.VARIANT_ID_PATTERN)
# Plan 23 stage D5: the third word of an appearance variant's target.
VARIANT_WORD = "variant"


def _note(params):
    note = params.get("note")
    if note is None:
        return None
    if not isinstance(note, str):
        raise StepFailed(f"The note must be text, not {type(note).__name__}.")
    return note.strip() or None


def _invalid(target) -> StepFailed:
    return StepFailed(f"Cannot regenerate {target!r}: the valid targets are {', '.join(TARGET_SHAPES)}.")


def parse_episode_target(target):
    """``("scene", ep, scene_id)``, ``("hook"|"cliffhanger"|"teaser", ep)``,
    ``("shot", ep, shot_id)`` (its ``:plan``), ``("shot_image", ep,
    shot_id)``, ``("shot_video", ep, shot_id)`` (its ``:video``, phase 6),
    ``("line", ep, line_id)`` or ``("metadata", ep, platform)`` for an
    episode target, None for anything else -- another
    ``shot:<ep>:<shid>:<word>`` among them (a later phase's). The shape only
    (the episode 1..99, the id patterns, the platforms), never the story."""
    if not isinstance(target, str):
        return None
    parts = target.split(":")
    if len(parts) < 2 or not _EPISODE.fullmatch(parts[1]):
        return None
    ep = int(parts[1])
    kind = parts[0]
    if kind in FRAMING_TARGETS and len(parts) == 2:
        return (kind, ep)
    if kind == "scene" and len(parts) == 3 and _SCENE.fullmatch(parts[2]):
        return ("scene", ep, parts[2])
    if kind == "shot" and len(parts) in (3, 4) and _SHOT.fullmatch(parts[2]):
        if len(parts) == 3:
            return (SHOT_IMAGE_KIND, ep, parts[2])
        if parts[3] == "plan":
            return ("shot", ep, parts[2])
        if parts[3] == "video":
            return (SHOT_VIDEO_KIND, ep, parts[2])
    if kind == LINE_KIND and len(parts) == 3 and _LINE.fullmatch(parts[2]):
        return (LINE_KIND, ep, parts[2])
    if kind == METADATA_KIND and len(parts) == 3 and parts[2] in schemas.PLATFORMS:
        return (METADATA_KIND, ep, parts[2])
    return None


def parse_target(target):
    """The entity or episode target *target* as a tuple --
    ``("character", id, "text")``, ``("character", id, "image", which)``,
    ``("character", id, "voice")``, ``("place", id, "text" | "image",
    [variant])``, ``("prop", id, "text" | "image")``, ``("season", ep)``, or
    an episode target's (:func:`parse_episode_target`; its first word is one
    of ``EPISODE_KINDS``) -- or None when it is not one. Checks the shape only
    (the id's pattern, the image names), never the story."""
    if not isinstance(target, str):
        return None
    episode = parse_episode_target(target)
    if episode is not None:
        return episode
    parts = target.split(":")
    if parts[0] == "season":
        if len(parts) == 2 and _EP.fullmatch(parts[1]):
            return ("season", int(parts[1]))
        return None
    kind = _KINDS.get(parts[0])
    if kind is None or len(parts) < 3:
        return None
    eid = parts[1]
    if store_mod.ENTITY_KINDS[kind].pattern.fullmatch(eid) is None:
        return None
    rest = parts[2:]
    if rest == ["text"]:
        return (parts[0], eid, "text")
    if kind == CHARACTERS:
        if rest == ["voice"]:
            return ("character", eid, "voice")
        if len(rest) == 2 and rest[0] == "image" and rest[1] in refimages.CHARACTER_IMAGES:
            return ("character", eid, "image", rest[1])
        if len(rest) == 2 and rest[0] == VARIANT_WORD and _VARIANT.fullmatch(rest[1]):
            return ("character", eid, VARIANT_WORD, rest[1])
    elif kind == PLACES:
        if len(rest) == 2 and rest[0] == "image" and rest[1] in PLACE_VARIANTS:
            return ("place", eid, "image", rest[1])
    elif rest == ["image"]:
        return ("prop", eid, "image")
    return None


def is_extra_target(target) -> bool:
    """Whether *target* is ``character:<char_id>:image:extra:<n>`` -- a shape
    of the 9.2 grammar whose images arrive in a later phase."""
    if not isinstance(target, str):
        return False
    parts = target.split(":")
    return (len(parts) >= 4 and parts[0] == "character" and parts[2] == "image"
            and store_mod.ENTITY_KINDS[CHARACTERS].pattern.fullmatch(parts[1]) is not None
            and _EXTRA.fullmatch(":".join(parts[3:])) is not None)


def run(ctx, *, runner=None, time_fn=time.monotonic, sleep_fn=time.sleep, adapters=None, transport=None) -> dict:
    params = ctx.params or {}
    target = params.get("target")

    if target == CONCEPTS_TARGET:
        return concepts.run(ctx, note=_note(params), runner=runner, time_fn=time_fn)

    if isinstance(target, str) and target.startswith(BIBLE_PREFIX):
        field = target[len(BIBLE_PREFIX):]
        if field in prompts.REGENERATE_TARGETS:
            return _regenerate_bible_field(ctx, field, _note(params), runner=runner, time_fn=time_fn)
        raise _invalid(target)

    parsed = parse_target(target)
    if parsed is None:
        if is_extra_target(target):
            raise StepFailed(f"Cannot regenerate {target!r}: extra images arrive in a later phase.")
        raise _invalid(target)
    if parsed[0] in EPISODE_KINDS:
        # Imported here: the episode targets pull in the timing engine and
        # shot resolution, which the phase-1/2 targets never need.
        from . import episode_regenerate

        return episode_regenerate.run(ctx, target, parsed, _note(params), runner=runner, time_fn=time_fn,
                                      sleep_fn=sleep_fn, adapters=adapters, transport=transport)

    tools = entities.Tools(runner=runner, time_fn=time_fn, sleep_fn=sleep_fn, adapters=adapters,
                           transport=transport)
    note = _note(params)
    store, story = llm_call.open_story(ctx)
    ctx.cancel.check()
    if parsed[0] == "season":
        return _regenerate_season(ctx, store, target, parsed[1], note, tools)

    kind, eid = _KINDS[parsed[0]], parsed[1]
    try:
        store.read_entity(ctx.story_id, kind, eid)
    except KeyError:
        raise StepFailed(f"Cannot regenerate {target!r}: there is no {parsed[0]} {eid!r} in this story.") from None

    what = parsed[2]
    if what == "text":
        return _regenerate_text(ctx, store, target, kind, eid, note, tools)
    if what == "voice":
        return _regenerate_voice(ctx, store, story, target, eid, params, tools)
    if what == VARIANT_WORD:
        return _regenerate_variant(ctx, store, target, eid, parsed[3], note, tools)
    slot = parsed[3] if len(parsed) > 3 else "image"
    return _regenerate_image(ctx, store, target, kind, eid, slot, note, tools)


# ------------------------------------------------------------------- phase 1

def _regenerate_bible_field(ctx, field, note, *, runner, time_fn) -> dict:
    part, keys = prompts.REGENERATE_TARGETS[field]
    store, story = llm_call.open_story(ctx)
    llm_call.require_concept(story)
    ctx.cancel.check()

    # The pack budgets the note like every other section (and says if it cut it).
    pack = bible.pack_for(story, note=note, setup=llm_call.setup_block(store, ctx.story_id, story))
    llm_call.announce_trimmed(ctx, pack, set())
    system, user, schema = bible.BUILDERS[part](
        pack,
        regenerate={"field": field, "current": bible.current_fields(story, part), "note": pack.note},
    )
    reply = llm_call.call_json(
        ctx, part, system, user, schema,
        validator=bible.validator_for(story, part, keys), runner=runner, time_fn=time_fn,
    )

    store.update(ctx.story_id, lambda doc: bible.apply(doc, part, reply, keys), now=llm_call.utc_now())
    ctx.on_log(f"🔁 Regenerated {field}" + (f" (note: {pack.note})" if pack.note else ""))
    return {"target": f"{BIBLE_PREFIX}{field}", "fields": list(keys)}


# ------------------------------------------------------------------- phase 2

def _noted(note) -> str:
    return f" (note: {note})" if note else ""


def _regenerate_text(ctx, store, target, kind, eid, note, tools) -> dict:
    # Imported here: each pulls in its own step's prompts and helpers.
    if kind == CHARACTERS:
        from . import cast

        # A character's dossier is written from its text too (phase 7, D1 before D2).
        text, after = cast.write_text, (cast.write_dossier, cast.write_look)
    elif kind == PLACES:
        from . import places

        text, after = places.write_place_text, (places.write_place_look,)
    else:
        from . import places

        text, after = places.write_prop_text, (places.write_prop_look,)
    text(ctx, store, eid, tools=tools, note=note, regenerate=True)
    if media_policy.is_v2(store.get(ctx.story_id)):
        # A v2 story's look is drawn from the text: written again with it (phase 7).
        for write in after:
            write(ctx, store, eid, tools=tools, note=note, regenerate=True)
    ctx.on_log(f"🔁 Regenerated {target}{_noted(note)}")
    return {"target": target}


def _image_failed(target, exc) -> StepFailed:
    return StepFailed(f"Cannot regenerate {target!r}: {exc}")


def _regenerate_image(ctx, store, target, kind, eid, slot, note, tools) -> dict:
    kwargs = tools.image_kwargs(ctx)
    seed = entities.fresh_seed()
    try:
        if kind == CHARACTERS:
            ref = refimages.character_image(store, ctx.story_id, eid, slot, note=note, seed=seed, **kwargs)
        elif kind == PLACES:
            ref = refimages.place_image(store, ctx.story_id, eid, slot, note=note, seed=seed, **kwargs)
        else:
            ref = refimages.prop_image(store, ctx.story_id, eid, note=note, seed=seed, **kwargs)
    except refimages.RefImageError as exc:
        raise _image_failed(target, exc) from None
    entities.clear_approval(store, ctx.story_id, kind, eid, now=ref["created_at"])
    images = {slot: ref["consistency"]}
    summary = {"target": target, "images": images, "needs_editor": []}
    ctx.on_log(f"🔁 Regenerated {target} (seed {ref['seed']}){_noted(note)}")
    if kind != CHARACTERS or slot != "portrait":
        return _judged(ctx, store, kind, eid, summary, tools)

    # The sheets that were drawn from the old portrait are drawn again.
    character = store.read_entity(ctx.story_id, CHARACTERS, eid)
    failed = []
    # Plan 23 stage D4: only the sheets the story's sheet_mode draws from the portrait.
    for sheet in refimages.character_sheets(store.get(ctx.story_id)):
        if character["refs"][sheet] is None:
            continue
        ctx.cancel.check()
        ctx.on_log(f"👤 {character['name']}: {sheet} again, from the new portrait")
        try:
            sheet_ref = refimages.character_image(store, ctx.story_id, eid, sheet, note=note, **kwargs)
        except refimages.NeedsEditor as exc:
            summary["needs_editor"].append({"char_id": eid, "message": exc.readiness["message"]})
            ctx.on_log(f"🟡 {character['name']}: the turnaround and expressions still show the previous "
                       f"portrait -- {exc.readiness['message']} Regenerate them once an editor or prompt-only "
                       "consistency is available.")
            break
        except refimages.RefImageError as exc:
            failed.append((sheet, str(exc)))
            ctx.on_log(f"✖ {character['name']} {sheet} failed: {exc}")
            continue
        images[sheet] = sheet_ref["consistency"]
    entities.clear_approval(store, ctx.story_id, CHARACTERS, eid, now=llm_call.utc_now())
    if failed:
        parts = "; ".join(f"{sheet} failed ({reason})" for sheet, reason in failed)
        targets = [entities.target(CHARACTERS, eid, "image", sheet) for sheet, _ in failed]
        raise StepFailed(f"The portrait of {character['name']} was made again, but {parts}. Regenerate "
                         f"{entities.quoted_list(targets)} to finish it.")
    return _judged(ctx, store, kind, eid, summary, tools)


def _judged(ctx, store, kind, eid, summary, tools) -> dict:
    """Plan 28 F3: the images just made are judged (``sheet_gate.review``),
    a failed one drawn again within the story's redraw budget; the
    sentences of those still failing join the summary."""
    failing = sheet_gate.review(ctx, store, kind, eid, tools=tools)
    if failing:
        summary["sheet_issues"] = failing
    return summary


def _regenerate_variant(ctx, store, target, char_id, variant_id, note, tools) -> dict:
    """Every sheet of appearance variant *variant_id* the story's sheet mode
    draws (plan 23 stage D5, ``refimages.variant_image``), on one seed (the
    variant's own the first time, a fresh one when they are drawn again),
    the note on each. The variant's approval is cleared by each image; the
    character's is never touched. A sheet that fails stops the job naming
    what was made and the target that finishes it."""
    character = store.read_entity(ctx.story_id, CHARACTERS, char_id)
    variant = refimages.variant_record(character, variant_id)
    if variant is None:
        raise StepFailed(f"Cannot regenerate {target!r}: {character['name']} has no appearance variant "
                         f"{variant_id!r}.")
    kwargs = tools.image_kwargs(ctx)
    # The first sheets of a variant are drawn on its own seed (refimages.variant_seed); drawn again, on a
    # fresh one, so a provider that honours seeds does not answer the same picture.
    made_before = any(ref is not None for ref in variant["refs"].values())
    seed = entities.fresh_seed() if made_before else None
    images = {}
    for which in refimages.character_images(store.get(ctx.story_id)):
        ctx.cancel.check()
        ctx.on_log(f"👤 {character['name']} ({variant['label']}): {which}, an edit of the portrait")
        try:
            ref = refimages.variant_image(store, ctx.story_id, char_id, variant_id, which, note=note, seed=seed,
                                          **kwargs)
        except refimages.RefImageError as exc:
            made = f" ({', '.join(images)} made)" if images else ""
            raise StepFailed(f"Cannot regenerate {target!r}{made}: {exc}") from None
        images[which] = ref["consistency"]
    used = seed if seed is not None else refimages.variant_seed(ctx.story_id, char_id, variant_id)
    ctx.on_log(f"🔁 Regenerated {target} (seed {used}){_noted(note)}: approve it (variant:{char_id}:{variant_id})")
    return {"target": target, "images": images, "needs_editor": []}


def _voice_key(voice) -> tuple:
    return (voice.provider, voice.voice_id)


def _pinned_by_others(store, story_id, char_id) -> dict:
    """``{(provider, voice_id): name}`` of the other lead/support characters."""
    taken = {}
    for doc in store.list_entities(story_id, CHARACTERS):
        if doc["char_id"] == char_id or doc["role"] not in schemas.CAST_APPROVAL_ROLES or not doc["voice"]:
            continue
        if voice_reference.is_reference_voice(doc["voice"]):
            continue  # a character's own recording is its alone (plan 23 stage B4)
        taken.setdefault((doc["voice"]["provider"], doc["voice"]["voice_id"]), doc["name"])
    return taken


def _chosen_voice(ctx, story, character, wanted, taken, store):
    """The catalogue voice *wanted* names; ``StepFailed`` when it is not one
    of the story language's on ``TTS_CHAIN`` or another lead's/support's."""
    provider, voice_id = wanted.get("provider"), wanted.get("voice_id")
    if not (isinstance(provider, str) and provider and isinstance(voice_id, str) and voice_id):
        raise StepFailed("A voice is {provider, voice_id} (and optionally rate, pitch).")
    if (provider, voice_id) == (voice_reference.REFERENCE_PROVIDER, voice_reference.REFERENCE_VOICE_ID):
        try:
            return voices.reference_voice(store, ctx.story_id, character)
        except voices.VoiceError as exc:
            raise StepFailed(str(exc)) from None
    catalogue = voices.catalogue(story["language"], env=ctx.settings_env, v2=media_policy.is_v2(story))
    choice = next((v for v in catalogue if (v.provider, v.voice_id) == (provider, voice_id)), None)
    if choice is None:
        offered = ", ".join(f"{v.provider}/{v.voice_id}" for v in catalogue) or "none"
        raise StepFailed(f"{provider}/{voice_id} is not a {story['language']} voice TTS_CHAIN can reach; "
                         f"pick one of: {offered}.")
    owner = taken.get(_voice_key(choice))
    if owner is not None:
        raise StepFailed(f"{provider}/{voice_id} is already {owner}'s voice: no two leads or supports share a "
                         f"voice; pick another for {character['name']}.")
    return choice


def _regenerate_voice(ctx, store, story, target, char_id, params, tools) -> dict:
    character = store.read_entity(ctx.story_id, CHARACTERS, char_id)
    if not (character["voice_hints"] or character["voice"]):
        raise StepFailed(f"Write {character['name']} first: the voice's sample line comes from the character's "
                         f"text (regenerate 'character:{char_id}:text').")
    wanted = params.get("voice")
    if wanted is not None and not isinstance(wanted, dict):
        raise StepFailed("A voice is {provider, voice_id} (and optionally rate, pitch).")
    taken = _pinned_by_others(store, ctx.story_id, char_id)
    current = character["voice"] or {}

    if wanted:
        choice = _chosen_voice(ctx, story, character, wanted, taken, store)
    else:
        excluded = set(taken)
        if current:
            excluded.add((current["provider"], current["voice_id"]))
        others = voices.alternates(character, story["language"], env=ctx.settings_env, taken=excluded,
                                   v2=media_policy.is_v2(story))
        if not others:
            raise StepFailed(f"No other voice is available for {character['name']} on TTS_CHAIN in "
                             f"{story['language']}; add a TTS provider or pick a voice.")
        choice = others[0]
    wanted = wanted or {}
    rate = wanted["rate"] if "rate" in wanted else current.get("rate")
    pitch = wanted["pitch"] if "pitch" in wanted else current.get("pitch")

    def block_for(doc):
        # The direction and sample line of K1's latest brief (a text
        # regenerate rewrites the brief and leaves the pinned voice alone).
        source = dict(doc, voice=None) if doc["voice_hints"] else doc
        return voices.pin(source, choice, rate=rate, pitch=pitch)

    try:
        block_for(character)
    except voices.VoiceError as exc:
        raise StepFailed(str(exc)) from None
    ctx.cancel.check()

    def write(doc):
        doc["voice"] = block_for(doc)
        doc["approved_at"] = None

    entities.write_character(store, ctx.story_id, char_id, write, now=llm_call.utc_now())
    entities.drop_sample(store, ctx.story_id, char_id)
    label = f"{choice.provider}/{choice.voice_id}"
    ctx.on_log(f"👤 {character['name']}: voice {label}")
    try:
        sample = voices.synthesize_sample(store, ctx.story_id, char_id, **tools.voice_kwargs(ctx))
    except voices.VoiceError as exc:
        others = ", ".join(f"{v.provider}/{v.voice_id}" for v in exc.alternates)
        raise StepFailed(f"{character['name']} now speaks with {label}, but its sample failed: {exc}"
                         + (f" Other voices: {others}." if others else "")) from None
    ctx.on_log(f"🔁 Regenerated {target}")
    return {"target": target, "voice": label, "sample": sample["name"]}


def _regenerate_season(ctx, store, target, ep, note, tools) -> dict:
    from . import season

    entry = season.expand_entry(ctx, store, ep, tools=tools, note=note, regenerate=True)

    def clear(doc):
        doc["approvals"]["season"] = None

    store.update(ctx.story_id, clear, now=llm_call.utc_now())
    ctx.on_log(f"🔁 Regenerated {target}{_noted(note)}")
    return {"target": target, "summary": entry["summary"]}
