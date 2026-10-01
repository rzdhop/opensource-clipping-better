"""Shot resolution for AI Story (spec 5, 2.8, 6.3, 6.4): turns per-scene shot
plans -- from the T1 prompt's reply, or from the deterministic "fast" path
below -- into a complete, valid ``storyboard_v1`` document.

A *plan* is the shared shape both paths produce, the same shape T1's own
reply carries (``prompts.t1_schema``): ``{"framing", "camera_motion",
"modifiers", "action", "subjects", "lines"}``, where ``action`` is one English
sentence that names people, the place and props only by their tags
(``@char_x``, ``#place_y:variant``, ``%prop_z`` -- spec 2.8's grammar),
``subjects`` is the tags visible in the shot, and ``lines`` is which of the
scene's own numbered lines (1-indexed, Python's own line-id order) the shot
covers. Both paths hand their scene's plans to :func:`rule_pass`, then
:func:`build_storyboard` resolves every plan into a shot of the final
document: an English image prompt with no entity name in it (spec 2.3), a
negative prompt, a reference-image list, motion and a duration.

Every function here is pure: no clock, no disk, no network, no import outside
the standard library and this package (DEC-012; RC-P8). ``entities`` is
always ``{"characters": {id: doc}, "places": {id: doc}, "props": {id: doc}}``
-- the story's full rosters, the shape ``store.list_entities`` already reads
into. Nothing here ever writes a character's or place's or prop's *name*
into a prompt (spec 2.3): a character is described by its descriptor and
signature items, never named, and every entity name still found by accident
(a plan's action written against the rules) is stripped at the very end with
the shared :func:`names.without_names` (the same helper ``refimages.py``
uses, RC-E5).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import re

from . import names as names_mod
from . import prompting, schemas, timing

# --------------------------------------------------------------- constants

# The emotions that pull a speaking-turn shot to close_up in the fast path
# (spec 5's "reaction" framings).
_TIGHT_EMOTIONS = ("angry", "shocked", "fear", "tension", "sad")

# Scene functions whose zoom uses motion_rules.tier1.zoom.peak instead of
# .dialogue, and whose last speaking-turn gets a fast-path reaction shot.
_PEAK_FUNCTIONS = ("peak", "cliffhanger")
_REACTION_FUNCTIONS = ("peak", "turn")

# rule_pass (a): the later shot's framing substitution table (spec: a fixed
# table, never chosen at random). A framing missing here (insert_prop) is
# never a change target -- it is always the *other* shot that moves instead.
_FRAMING_SUBSTITUTES = {
    "close_up": "medium_single",
    "medium_single": "close_up",
    "medium_two_shot": "over_shoulder",
    "over_shoulder": "medium_two_shot",
    "wide_establishing": "high_angle",
    "high_angle": "wide_establishing",
    "low_angle": "medium_single",
    "extreme_close_up": "close_up",
}

_TIGHT_FRAMINGS = ("close_up", "extreme_close_up")

# The leading-phrase cut points for character_handles/prop_handles: whichever
# of these occurs earliest in the descriptor's first sentence ends the kept
# phrase (2026-09-27 revision: keeps the noun after the last comma, not the
# leading adjective list before it -- see _leading_phrase).
_HANDLE_CUT_MARKERS = (" serving as ", " with ", " wearing ", " who ", " that ", ";")
_LEADING_ARTICLES = ("a ", "an ", "the ")
_HANDLE_MAX_WORDS = 10

# resolve_action/_subjects_block/_reference_images: a tag anywhere in text.
_TAG_FINDER = re.compile(r"[@%#][a-z0-9_:]+")

_CHAR_TAG = re.compile(r"^@(char_[a-z0-9_]+)$")
_PROP_TAG = re.compile(r"^%(prop_[a-z0-9_]+)$")
_PLACE_TAG = re.compile(r"^#(place_[a-z0-9_]+):([a-z][a-z0-9_]*)$")

_NEUTRAL_WORDS = {"characters": "the character", "places": "the place", "props": "the object"}
_KIND_TO_ENTITY_KEY = {"char": "characters", "place": "places", "prop": "props"}


# ------------------------------------------------------------------ helpers

def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_period(text: str) -> str:
    text = text.strip()
    if text.endswith("."):
        text = text[:-1]
    return text


# -------------------------------------------------------------- tag grammar

def parse_tag(tag) -> tuple:
    """``(kind, entity_id, variant)`` for one subject tag: ``kind`` is
    ``"char"``, ``"place"`` or ``"prop"``; ``variant`` is the place's time
    variant, ``None`` for a character or prop. ``ValueError`` for anything
    that is not one of the three (the storyboard schema's own
    ``SUBJECT_TAG_PATTERN`` grammar)."""
    if not isinstance(tag, str):
        raise ValueError(f"not a tag: {tag!r}")
    match = _CHAR_TAG.match(tag)
    if match:
        return "char", match.group(1), None
    match = _PROP_TAG.match(tag)
    if match:
        return "prop", match.group(1), None
    match = _PLACE_TAG.match(tag)
    if match:
        return "place", match.group(1), match.group(2)
    raise ValueError(f"not a valid subject tag: {tag!r}")


# ------------------------------------------------------------------ handles

def _leading_phrase(descriptor: str) -> str:
    """The descriptor's own head-noun phrase: (1) its first sentence (split
    at the first '. '); (2) cut at the earliest of ' serving as '/' with '/
    ' wearing '/' who '/' that '/';'; (3) drop a leading article; (4) keep
    only the text after that phrase's LAST comma, if it has one -- this
    drops the leading comma-separated adjective list a K1-written
    descriptor usually opens with ("A fuzzy, dark brown ripe kiwi fruit..."
    -> "dark brown ripe kiwi fruit"), rather than keeping the adjectives and
    losing the noun; (5) collapse whitespace, keep <= 10 words."""
    text = descriptor.strip()

    end = text.find(". ")
    text = text[:end] if end != -1 else text.rstrip(".")

    positions = [text.find(marker) for marker in _HANDLE_CUT_MARKERS]
    positions = [p for p in positions if p != -1]
    if positions:
        text = text[: min(positions)]

    lowered = text.lower()
    for article in _LEADING_ARTICLES:
        if lowered.startswith(article):
            text = text[len(article):]
            break

    if "," in text:
        text = text.rsplit(",", 1)[1]

    words = text.strip().strip(",;").split()
    return " ".join(words[:_HANDLE_MAX_WORDS])


def _handle_from_descriptor(descriptor: str) -> str:
    phrase = _leading_phrase(descriptor)
    return f"the {phrase}".strip()


def character_handles(characters: dict) -> dict:
    """``{char_id: handle}``: a short English handle per character, from the
    leading noun phrase of its own descriptor (never its name -- spec 2.3).
    Two characters whose base handle collides both get
    ``" wearing " + their first signature item`` appended; if that still
    collides, ``" (n)"`` in cast order (the order *characters* iterates).
    Deterministic."""
    order = list(characters.keys())
    base = {cid: _handle_from_descriptor(characters[cid]["descriptor"]) for cid in order}

    counts = {}
    for cid in order:
        counts[base[cid]] = counts.get(base[cid], 0) + 1

    handles = dict(base)
    for cid in order:
        if counts[base[cid]] > 1:
            items = characters[cid].get("signature_items") or []
            first_item = items[0] if items else ""
            handles[cid] = _collapse_ws(f"{base[cid]} wearing {first_item}")

    counts2 = {}
    for cid in order:
        counts2[handles[cid]] = counts2.get(handles[cid], 0) + 1

    seen = {}
    for cid in order:
        if counts2[handles[cid]] > 1:
            key = handles[cid]
            seen[key] = seen.get(key, 0) + 1
            handles[cid] = f"{key} ({seen[key]})"

    return handles


def prop_handles(props: dict) -> dict:
    """``{prop_id: handle}``, the same way as :func:`character_handles` but
    without the "wearing" disambiguation: a collision goes straight to
    ``" (n)"`` in cast order."""
    order = list(props.keys())
    base = {pid: _handle_from_descriptor(props[pid]["descriptor"]) for pid in order}

    counts = {}
    for pid in order:
        counts[base[pid]] = counts.get(base[pid], 0) + 1

    handles = dict(base)
    seen = {}
    for pid in order:
        key = base[pid]
        if counts[key] > 1:
            seen[key] = seen.get(key, 0) + 1
            handles[pid] = f"{key} ({seen[key]})"

    return handles


# ------------------------------------------------------------- action text

def resolve_action(action, *, char_handles, prop_handles, place_names) -> str:
    """*action*'s tags replaced by their handle (``@char_x``, ``%prop_y``) or
    ``"the setting"`` (``#place_z:variant``). ``ValueError`` for a tag whose
    entity is not in the given handles/names -- an unknown tag is a bug, never
    silently left in place."""

    def _replace(match):
        tag = match.group(0)
        kind, eid, _variant = parse_tag(tag)
        if kind == "char":
            if eid not in char_handles:
                raise ValueError(f"resolve_action: unknown character tag {tag!r}")
            return char_handles[eid]
        if kind == "prop":
            if eid not in prop_handles:
                raise ValueError(f"resolve_action: unknown prop tag {tag!r}")
            return prop_handles[eid]
        if eid not in place_names:
            raise ValueError(f"resolve_action: unknown place tag {tag!r}")
        return "the setting"

    return _TAG_FINDER.sub(_replace, action)


# ------------------------------------------------------------- shot prompts

def _character_block(doc) -> str:
    descriptor = _strip_period(doc["descriptor"])
    items = list(doc.get("signature_items") or [])
    if not items:
        raise ValueError("a character's signature_items must not be empty to build a shot prompt")
    return f"{descriptor}, wearing {', '.join(items)}."


def _prop_block(doc) -> str:
    return f"{_strip_period(doc['descriptor'])}."


def _place_block(doc) -> str:
    descriptor = _strip_period(doc["descriptor"])
    layout = _strip_period(doc["layout_notes"])
    return _collapse_ws(f"{descriptor}. {layout}")


def _subjects_block(subject_tags, *, characters, props) -> str:
    """Spec 5's ``subjects_block``: every character tag's descriptor +
    signature items (WITHOUT the style's character_design_rules --
    ``shot_prompt`` injects those itself, once), in subject order; with no
    character tag, the props' descriptors in subject order; with neither,
    "No people in frame."."""
    char_parts = []
    for tag in subject_tags:
        if tag.startswith("@"):
            _kind, cid, _variant = parse_tag(tag)
            doc = characters.get(cid)
            if doc is None:
                raise ValueError(f"_subjects_block: unknown character tag {tag!r}")
            char_parts.append(_character_block(doc))
    if char_parts:
        return _collapse_ws(" ".join(char_parts))

    prop_parts = []
    for tag in subject_tags:
        if tag.startswith("%"):
            _kind, pid, _variant = parse_tag(tag)
            doc = props.get(pid)
            if doc is None:
                raise ValueError(f"_subjects_block: unknown prop tag {tag!r}")
            prop_parts.append(_prop_block(doc))
    if prop_parts:
        return _collapse_ws(" ".join(prop_parts))

    return "No people in frame."


def _reference_images(subject_tags, *, scene, characters, places, props) -> list:
    """Spec 5's reference list: characters in subject order (portrait), then
    the place's own variant image (falling back to the day plate), then
    props in subject order (image); entries with no image on the entity are
    skipped; capped to 8."""
    refs = []

    for tag in subject_tags:
        if tag.startswith("@"):
            _kind, cid, _variant = parse_tag(tag)
            doc = characters.get(cid)
            if doc is None:
                continue
            ref = (doc.get("refs") or {}).get("portrait")
            if ref and ref.get("name"):
                refs.append(f"characters/{cid}/refs/{ref['name']}")

    place_doc = places.get(scene["place_id"])
    if place_doc is not None:
        variants = place_doc.get("time_variants") or {}
        ref = variants.get(scene["time_variant"])
        if not ref:
            ref = variants.get(schemas.MASTER_PLATE_VARIANT)
        if ref and ref.get("name"):
            refs.append(f"places/{scene['place_id']}/refs/{ref['name']}")

    for tag in subject_tags:
        if tag.startswith("%"):
            _kind, pid, _variant = parse_tag(tag)
            doc = props.get(pid)
            if doc is None:
                continue
            ref = doc.get("image")
            if ref and ref.get("name"):
                refs.append(f"props/{pid}/refs/{ref['name']}")

    return refs[:8]


def _story_name_map(entities) -> dict:
    """``{name: neutral word}`` for every character, place and prop of
    *entities* -- a character's word wins on a name shared with a place or
    prop (mirrors ``refimages._entity_names``)."""
    names = {}
    for kind in ("characters", "places", "props"):
        for doc in entities.get(kind, {}).values():
            names.setdefault(doc["name"], _NEUTRAL_WORDS[kind])
    return names


def resolve_shot(shot, *, scene, entities, style_lock, consistency_mode) -> dict:
    """*shot* (a plan: ``framing``/``action``/``subjects``) resolved into
    ``{"image_prompt", "video_action", "negative_prompt", "reference_images",
    "consistency"}``. Every entity name is stripped from the resolved action
    alone (``video_action``, which also feeds ``image_prompt``'s action
    sentence), even one a plan's action wrongly spelled out instead of using
    a tag (spec 2.3) -- never from the whole assembled ``image_prompt``, so
    a place's own name recurring as ordinary words inside its own descriptor
    (phase 7 D2) survives untouched. ``prompt_override`` is never touched
    here -- the caller decides whether to use it instead of
    ``image_prompt``."""
    characters = entities.get("characters", {})
    places = entities.get("places", {})
    props = entities.get("props", {})

    char_handles_map = character_handles(characters)
    prop_handles_map = prop_handles(props)
    place_names = {pid: doc.get("name") for pid, doc in places.items()}

    resolved_action = resolve_action(
        shot["action"], char_handles=char_handles_map, prop_handles=prop_handles_map, place_names=place_names,
    )
    # Phase 7 D1/D2: every entity name swept from the resolved action (tags
    # already resolved above) -- this is both the text an I2V clip prompt is
    # built from (video_plan.build_video_prompt) and the action sentence
    # image_prompt is assembled with, so neither a raw tag nor a leaked name
    # ever reaches either prompt.
    video_action = names_mod.without_names(resolved_action, _story_name_map(entities))
    subjects_block = _subjects_block(shot["subjects"], characters=characters, props=props)

    place_doc = places[scene["place_id"]]
    place_block = _place_block(place_doc)

    image_prompt = prompting.shot_prompt(
        style_lock,
        subjects_block=subjects_block,
        action=video_action,
        place_block=place_block,
        time_variant=scene["time_variant"],
        framing=shot["framing"],
    )

    return {
        "image_prompt": image_prompt,
        "video_action": video_action,
        "negative_prompt": prompting.negative_prompt(style_lock),
        "reference_images": _reference_images(shot["subjects"], scene=scene, characters=characters,
                                              places=places, props=props),
        "consistency": consistency_mode,
    }


# ------------------------------------------------------------------- motion

def _clamp_zoom(value, zoom_max):
    return round(min(max(value, 1.0), zoom_max), 3)


def motion_for(framing, camera_motion, scene_function, style_lock) -> dict:
    """``{"type", "zoom_from", "zoom_to", "pan"}`` for one shot (spec 5's
    tier-1 motion): the TYPE is ``by_function[framing]`` if present, else
    ``by_function[scene_function]`` if present, else the given
    *camera_motion*, else the style's own ``default_motion``. ``push_in``
    zooms from ``zoom.dialogue`` to itself (``zoom.peak`` on a peak or
    cliffhanger scene); ``pull_out`` is the reverse; both clamp to
    ``zoom.max``. A ``pan_*`` type holds the zoom at 1.0 and pans that way;
    ``hold`` holds the zoom at 1.0 with no pan."""
    tier1 = style_lock["motion_rules"]["tier1"]
    by_function = tier1["by_function"]

    if framing in by_function:
        motion_type = by_function[framing]
    elif scene_function in by_function:
        motion_type = by_function[scene_function]
    elif camera_motion is not None:
        motion_type = camera_motion
    else:
        motion_type = tier1["default_motion"]

    zoom = tier1["zoom"]
    zoom_max = zoom["max"]

    if motion_type in ("push_in", "pull_out"):
        lo, hi = zoom["peak"] if scene_function in _PEAK_FUNCTIONS else zoom["dialogue"]
        zoom_from, zoom_to = (lo, hi) if motion_type == "push_in" else (hi, lo)
        pan = "none"
    elif motion_type in ("pan_lr", "pan_rl", "pan_ud", "pan_du"):
        zoom_from, zoom_to = 1.0, 1.0
        pan = motion_type[len("pan_"):]
    else:  # hold
        zoom_from, zoom_to = 1.0, 1.0
        pan = "none"

    return {
        "type": motion_type,
        "zoom_from": _clamp_zoom(zoom_from, zoom_max),
        "zoom_to": _clamp_zoom(zoom_to, zoom_max),
        "pan": pan,
    }


# ---------------------------------------------------------------- fast path

def _plan(*, framing, camera_motion, modifiers, action, subjects, lines) -> dict:
    return {
        "framing": framing, "camera_motion": camera_motion, "modifiers": list(modifiers),
        "action": action, "subjects": list(subjects), "lines": list(lines),
    }


def _base_modifiers(scene, style_lock) -> list:
    allowed = set(style_lock["motion_rules"]["tier1"].get("modifiers") or [])
    mods = []
    if "jitter_stopmotion" in allowed:
        mods.append("jitter_stopmotion")
    if scene["emotion"] in ("tension", "fear") and "handheld" in allowed:
        mods.append("handheld")
    return mods


def _group_speaking_turns(lines) -> list:
    """``[(start, end, speaker), ...]`` (0-based, inclusive), one per run of
    consecutive lines by the same speaker."""
    turns = []
    i, n = 0, len(lines)
    while i < n:
        j = i
        speaker = lines[i]["speaker"]
        while j + 1 < n and lines[j + 1]["speaker"] == speaker:
            j += 1
        turns.append((i, j, speaker))
        i = j + 1
    return turns


def fast_plan(scene, *, lines, entities, episode_defaults, style_lock, first_at_place) -> list:
    """A deterministic (no LLM call) shot list for one scene, in the T1 reply
    shape: an insert-prop shot first on an ``insert_prop`` hook with a prop
    present, an opening shot (a wordless wide establishing when
    *first_at_place*, else the first speaking turn forced to
    medium_two_shot/medium_single), one shot per speaking turn (close_up on
    an angry/shocked/fear/tension/sad line, else medium_single), a reaction
    close_up after the last line on a peak/turn scene, or -- with no lines at
    all -- a wide_establishing plus one medium shot. Clamped to
    ``episode_defaults["shots_per_scene"]`` by merging speaking shots from
    the end (over) or adding a reaction/establishing shot (under)."""
    style_modifiers = style_lock["motion_rules"]["tier1"].get("modifiers") or []
    default_motion = style_lock["motion_rules"]["tier1"]["default_motion"]
    mods = _base_modifiers(scene, style_lock)

    chars = list(scene["characters"])
    props = list(scene["props"])
    place_tag = f"#{scene['place_id']}:{scene['time_variant']}"
    char_tags = [f"@{c}" for c in chars]

    plans = []

    if scene["function"] == "hook" and episode_defaults["hook_style"] == "insert_prop" and props:
        prop_tag = f"%{props[0]}"
        plans.append(_plan(
            framing="insert_prop", camera_motion=default_motion, modifiers=mods,
            action=f"{prop_tag} is shown in tight close-up, stating the episode's premise.",
            subjects=[prop_tag], lines=[],
        ))

    if not lines:
        plans.append(_plan(
            framing="wide_establishing", camera_motion=default_motion, modifiers=mods,
            action=(f"Wide view of {place_tag} with {', '.join(char_tags)} present." if char_tags
                    else f"Wide view of {place_tag}, empty."),
            subjects=[place_tag] + char_tags, lines=[],
        ))
        second_framing = "medium_two_shot" if len(chars) >= 2 else "medium_single"
        plans.append(_plan(
            framing=second_framing, camera_motion=default_motion, modifiers=mods,
            action=(f"{', '.join(char_tags)} in a quiet moment, no dialogue." if char_tags
                    else "A quiet moment, no dialogue."),
            subjects=char_tags or [place_tag], lines=[],
        ))
    else:
        opening_framing = None
        if first_at_place:
            plans.append(_plan(
                framing="wide_establishing", camera_motion=default_motion, modifiers=mods,
                action=(f"Wide view of {place_tag} with {', '.join(char_tags)} present." if char_tags
                        else f"Wide view of {place_tag}, empty."),
                subjects=[place_tag] + char_tags, lines=[],
            ))
        else:
            opening_framing = "medium_two_shot" if len(chars) >= 2 else "medium_single"

        turns = _group_speaking_turns(lines)
        for turn_index, (start, end, speaker) in enumerate(turns):
            turn_lines = lines[start:end + 1]
            line_numbers = list(range(start + 1, end + 2))
            emotional = any(l["emotion"] in _TIGHT_EMOTIONS for l in turn_lines)
            if turn_index == 0 and opening_framing is not None:
                framing = opening_framing
            else:
                framing = "close_up" if emotional else "medium_single"

            speaker_tag = f"@{speaker}" if speaker != "narrator" else None
            if framing == "medium_two_shot":
                subjects = char_tags
            elif speaker_tag is not None:
                subjects = [speaker_tag]
            else:
                subjects = char_tags

            emotion = turn_lines[-1]["emotion"]
            if speaker_tag is None:
                action = f"Narration plays over the scene, {emotion}."
            else:
                others = [t for t in char_tags if t != speaker_tag]
                action = (f"{speaker_tag} speaks, {emotion}, facing {others[0]}." if others
                          else f"{speaker_tag} speaks, {emotion}.")

            plans.append(_plan(
                framing=framing, camera_motion=default_motion, modifiers=mods,
                action=action, subjects=subjects, lines=line_numbers,
            ))

        if scene["function"] in _REACTION_FUNCTIONS and turns:
            last_speaker = turns[-1][2]
            other_chars = [c for c in chars if c != last_speaker]
            reaction_char = other_chars[0] if other_chars else (chars[0] if chars else None)
            if reaction_char is not None:
                reaction_tag = f"@{reaction_char}"
                plans.append(_plan(
                    framing="close_up", camera_motion=default_motion, modifiers=mods,
                    action=f"{reaction_tag} reacts silently.", subjects=[reaction_tag], lines=[],
                ))

    lo, hi = episode_defaults["shots_per_scene"]
    plans = _clamp_shot_count(plans, lo, hi, chars=chars, place_tag=place_tag,
                              default_motion=default_motion, mods=mods)
    return plans


def _clamp_shot_count(plans, lo, hi, *, chars, place_tag, default_motion, mods) -> list:
    plans = list(plans)

    while len(plans) > hi:
        speaking = [i for i, p in enumerate(plans) if p["lines"]]
        if len(speaking) >= 2:
            later_i, earlier_i = speaking[-1], speaking[-2]
            earlier, later = plans[earlier_i], plans[later_i]
            earlier["lines"] = earlier["lines"] + later["lines"]
            earlier["subjects"] = list(dict.fromkeys(earlier["subjects"] + later["subjects"]))
            del plans[later_i]
            continue
        # No more speaking shots to merge into one another: drop a trailing
        # wordless shot (a reaction close_up) instead, never the scene's own
        # first shot (its opening/establishing coverage).
        if len(plans) <= 1:
            break
        removable = [i for i in range(1, len(plans)) if not plans[i]["lines"]]
        if removable:
            del plans[removable[-1]]
        else:
            del plans[-1]

    while len(plans) < lo:
        if chars:
            tag = f"@{chars[0]}"
            plans.append(_plan(
                framing="close_up", camera_motion=default_motion, modifiers=mods,
                action=f"{tag} reacts silently.", subjects=[tag], lines=[],
            ))
        else:
            plans.append(_plan(
                framing="wide_establishing", camera_motion=default_motion, modifiers=mods,
                action=f"Wide view of {place_tag}.", subjects=[place_tag], lines=[],
            ))

    return plans


# ---------------------------------------------------------------- rule pass

def _flatten(plans_by_scene) -> list:
    """``[(scene_index, shot_index, scene, plan), ...]`` across every scene,
    in order."""
    out = []
    for si, (scene, plans) in enumerate(plans_by_scene):
        for pi, plan in enumerate(plans):
            out.append((si, pi, scene, plan))
    return out


def _is_protected(shot_index, plan) -> bool:
    """Never changed by rule (a): an insert_prop shot, or a scene's own
    opening wide_establishing shot."""
    return plan["framing"] == "insert_prop" or (shot_index == 0 and plan["framing"] == "wide_establishing")


def _apply_no_repeat_framing(plans_by_scene, notes) -> bool:
    """One pass of rule (a); returns whether anything changed."""
    items = _flatten(plans_by_scene)
    changed = False
    for k in range(1, len(items)):
        _si, pi, scene, plan = items[k]
        _psi, ppi, pscene, pplan = items[k - 1]
        if plan["framing"] != pplan["framing"]:
            continue
        if not _is_protected(pi, plan) and plan["framing"] in _FRAMING_SUBSTITUTES:
            new_framing = _FRAMING_SUBSTITUTES[plan["framing"]]
            notes.append(
                f"rule_pass: scene {scene['scene_id']} shot {pi + 1}: framing changed "
                f"{plan['framing']!r} -> {new_framing!r} (repeated the previous shot's framing)"
            )
            plan["framing"] = new_framing
            changed = True
        elif not _is_protected(ppi, pplan) and pplan["framing"] in _FRAMING_SUBSTITUTES:
            new_framing = _FRAMING_SUBSTITUTES[pplan["framing"]]
            notes.append(
                f"rule_pass: scene {pscene['scene_id']} shot {ppi + 1}: framing changed "
                f"{pplan['framing']!r} -> {new_framing!r} (repeated the next shot's framing)"
            )
            pplan["framing"] = new_framing
            changed = True
        else:
            # Both shots are protected (or have no substitute): the repeat
            # is left in place -- reported, never silently dropped.
            note = (
                f"rule_pass: scene {pscene['scene_id']} shot {ppi + 1} and scene {scene['scene_id']} shot "
                f"{pi + 1}: both share framing {plan['framing']!r} and neither can be changed "
                "(protected or no substitute); left unresolved"
            )
            if note not in notes:
                notes.append(note)
    return changed


def _apply_close_up_window(plans_by_scene, notes) -> None:
    """Rule (b): every window of 3 consecutive scenes has a close_up or
    extreme_close_up somewhere in it; otherwise the last non-insert_prop shot
    of the window's third scene becomes one."""
    n = len(plans_by_scene)
    for start in range(0, max(0, n - 2)):
        window = plans_by_scene[start:start + 3]
        has_tight = any(p["framing"] in _TIGHT_FRAMINGS for _scene, plans in window for p in plans)
        if has_tight:
            continue
        third_scene, third_plans = window[2]
        candidates = [p for p in third_plans if p["framing"] != "insert_prop"]
        if not candidates:
            continue
        target = candidates[-1]
        target["framing"] = "close_up"
        has_char_tag = any(t.startswith("@") for t in target["subjects"])
        if not has_char_tag and third_scene["characters"]:
            target["subjects"] = list(target["subjects"]) + [f"@{third_scene['characters'][0]}"]
        notes.append(
            f"rule_pass: scene {third_scene['scene_id']}: no close_up/extreme_close_up in this 3-scene "
            "window, its last shot was forced to close_up"
        )


def _apply_motion_precedence(plans_by_scene, style_lock, notes) -> None:
    """Rule (c): each plan's camera_motion becomes whatever motion_for's
    precedence would pick for it -- the "push-in on peaks" rule."""
    for scene, plans in plans_by_scene:
        for plan in plans:
            motion = motion_for(plan["framing"], plan["camera_motion"], scene["function"], style_lock)
            if motion["type"] != plan["camera_motion"]:
                notes.append(
                    f"rule_pass: scene {scene['scene_id']}: camera motion changed "
                    f"{plan['camera_motion']!r} -> {motion['type']!r}"
                )
                plan["camera_motion"] = motion["type"]


def rule_pass(plans_by_scene, style_lock) -> tuple:
    """The cross-scene rules applied to every scene's shot plans, on both the
    T1 and the fast path (spec 5): (a) no two consecutive shots in the whole
    episode share a framing (never changing an insert_prop shot or a scene's
    own opening wide_establishing -- the earlier shot moves instead); (b)
    every window of 3 consecutive scenes has a close_up or extreme_close_up,
    re-checking (a) afterwards; (c) each shot's camera motion follows
    :func:`motion_for`'s precedence. Returns ``(plans_by_scene, notes)``, a
    new structure -- *plans_by_scene* itself and its plan dicts are not
    mutated."""
    plans_by_scene = [(scene, [dict(p) for p in plans]) for scene, plans in plans_by_scene]
    notes = []

    guard = len(_flatten(plans_by_scene)) + 2
    for _ in range(guard):
        if not _apply_no_repeat_framing(plans_by_scene, notes):
            break

    _apply_close_up_window(plans_by_scene, notes)

    for _ in range(guard):
        if not _apply_no_repeat_framing(plans_by_scene, notes):
            break

    _apply_motion_precedence(plans_by_scene, style_lock, notes)

    return plans_by_scene, notes


REPEATED_FRAMING = "two shots in a row would repeat a framing"
NO_CLOSE_UP = "three scenes in a row would hold no close-up"


def rule_moves(storyboard, script, style_lock) -> dict:
    """``{shot_id: (framing, framing the rules give it, why)}``: the shots
    :func:`rule_pass` would move were *storyboard* built again from its own
    plans (:func:`plans_from_storyboard`) -- *why* is
    :data:`REPEATED_FRAMING` (rule a) or :data:`NO_CLOSE_UP` (rule b). A
    storyboard that keeps the rules gives ``{}``. An edit that makes one
    breaks them (phase 5 stage 7: refused, never a neighbour moved
    silently). Pure; *storyboard* is not changed."""
    plans = plans_from_storyboard(storyboard, script)
    ordered = [(scene, plans[scene["scene_id"]]) for scene in script["scenes"] if scene["scene_id"] in plans]
    by_scene: dict = {}
    for shot in storyboard["shots"]:
        by_scene.setdefault(shot["scene_id"], []).append(shot["shot_id"])
    ids = [shot_id for scene, _plans in ordered for shot_id in by_scene[scene["scene_id"]]]
    start = [plan["framing"] for _scene, scene_plans in ordered for plan in scene_plans]

    repeats = [(scene, [dict(plan) for plan in scene_plans]) for scene, scene_plans in ordered]
    for _ in range(len(start) + 2):
        if not _apply_no_repeat_framing(repeats, []):
            break
    after_a = [plan["framing"] for _scene, scene_plans in repeats for plan in scene_plans]
    moved, _notes = rule_pass(ordered, style_lock)
    end = [plan["framing"] for _scene, scene_plans in moved for plan in scene_plans]
    return {ids[i]: (start[i], end[i], REPEATED_FRAMING if after_a[i] != start[i] else NO_CLOSE_UP)
            for i in range(len(start)) if end[i] != start[i]}


# ------------------------------------------------------------- storyboard

def _collect_resolved_from(resolved_from, subject_tags, scene, entities) -> None:
    for tag in subject_tags:
        kind, eid, _variant = parse_tag(tag)
        doc = entities.get(_KIND_TO_ENTITY_KEY[kind], {}).get(eid)
        if doc is not None:
            resolved_from[eid] = doc["updated_at"]
    place_doc = entities.get("places", {}).get(scene["place_id"])
    if place_doc is not None:
        resolved_from[scene["place_id"]] = place_doc["updated_at"]


def _time_shots(shots, transitions, script, *, template, language, style_lock, skip=(), whole_frames) -> list:
    """Every shot's ``duration_s``, in place, the one way a storyboard is
    timed: each scene's EPISODE-LEVEL timing -- :func:`timing.episode_pass`
    over the whole *script* beside these very shots and transitions, the
    same call the script's stored ``timing`` comes from
    (``episode_common.retime``), window pass and shot floor included --
    split across the scene's shots by :func:`timing.allocate_shots`. So a
    scene's shots always sum to ``script.timing.scenes[sid].duration_s``.
    *whole_frames*: the episode timing and the split are in whole frames
    (DEC-142 as amended by phase 5 stage 6); off, a storyboard timed before
    is kept in its old timing. A scene in *skip* keeps its shots' durations. Returns the notes (a scene
    whose shots sit at the floor length -- never expected: the shot floor
    already grew every scene to what its shots need)."""
    notes = []
    _timing, scene_timings = timing.episode_pass(
        script, template, language, style_lock=style_lock, storyboard={"shots": shots, "transitions": transitions},
        whole_frames=whole_frames)
    shots_by_scene: dict = {}
    for shot in shots:
        shots_by_scene.setdefault(shot["scene_id"], []).append(shot)

    for scene in script["scenes"]:
        sid = scene["scene_id"]
        if sid in skip or sid not in shots_by_scene:
            continue
        scene_t = scene_timings[sid]
        scene_shots = shots_by_scene[sid]
        scene_plans_for_alloc = [{"lines": shot["lines"]} for shot in scene_shots]
        durations, extra_hold = timing.allocate_shots(scene, scene_t, scene_plans_for_alloc, template,
                                                      whole_frames=whole_frames)
        for shot, duration in zip(scene_shots, durations):
            shot["duration_s"] = duration
        if extra_hold > 0:
            notes.append(f"build_storyboard: scene {sid}: extra_hold_s={extra_hold} (shots at the floor length)")
    return notes


def retime_storyboard(storyboard, script, *, template, language, style_lock) -> bool:
    """*storyboard*'s shot durations recomputed in place from *script*'s
    lines as they are now (measured, or estimated), exactly as
    :func:`build_storyboard` times the same plans -- and nothing else moves:
    the plans, prompts, ids, transitions, the scenes' entries, the revision
    and the approval stay as they are (a duration is derived, like the
    script's ``timing``). A scene planned from another revision of its
    scene, or marked stale, keeps its durations until it is planned again:
    its shots may name lines it no longer has. Returns whether any duration
    changed. The other scenes follow the episode-level timing the script is
    stored with (:func:`_time_shots`): a change in one scene can move
    another one's durations through the episode's window pass.

    A scene marked ``retime_only`` (phase 5 stage 7: a text-only edit kept
    its plan, and its entry follows the new revision) is no stale one: it is
    re-timed with the rest, in place, and its mark is dropped once every one
    of its lines is measured for its words (returned as a change, so the
    caller writes it).

    Whole frames (phase 5 stage 6): a storyboard flagged ``whole_frames`` is
    re-timed in whole frames. One timed before keeps its old timing while
    any of its shots keeps its duration (a skipped scene's shots would
    disagree with a whole-frame episode timing at render); its first re-time
    that re-cuts every shot switches it to whole frames and sets the flag
    (returned as a change, so the caller writes it -- and must re-time the
    script beside it again, ``episode_common.retime``)."""
    scenes_in_order = [scene for scene in script["scenes"] if scene["scene_id"] in storyboard["scenes"]]
    line_ids = {scene["scene_id"]: {line["line_id"] for line in scene["lines"]} for scene in scenes_in_order}
    skip = {scene["scene_id"] for scene in script["scenes"] if scene["scene_id"] not in storyboard["scenes"]}
    for scene in scenes_in_order:
        entry = storyboard["scenes"][scene["scene_id"]]
        if entry.get("stale") or entry.get("script_rev") != scene["rev"]:
            skip.add(scene["scene_id"])
    for shot in storyboard["shots"]:
        known = line_ids.get(shot["scene_id"])
        if known is not None and any(line_id not in known for line_id in shot["lines"]):
            skip.add(shot["scene_id"])
    if _non_cut_inside_a_scene(storyboard):
        # Transitions that break spec 6.3: the episode cannot be timed
        # (timing._boundary_from_storyboard refuses them). Left as it is:
        # the storyboard's own validation (schemas.storyboard_errors)
        # refuses it too, and a PATCH that made it is refused whole.
        return False
    before = [shot["duration_s"] for shot in storyboard["shots"]]
    whole_frames = timing.board_whole_frames(storyboard) or not any(
        shot["scene_id"] in skip for shot in storyboard["shots"])
    _time_shots(storyboard["shots"], storyboard["transitions"], script, template=template,
                language=language, style_lock=style_lock, skip=skip, whole_frames=whole_frames)
    changed = [shot["duration_s"] for shot in storyboard["shots"]] != before
    if whole_frames and not storyboard.get("whole_frames"):
        storyboard["whole_frames"] = True
        changed = True
    for scene in scenes_in_order:
        entry = storyboard["scenes"][scene["scene_id"]]
        if entry.get("retime_only") and scene["scene_id"] not in skip and all(
                _measured_for_its_words(line) for line in scene["lines"]):
            del entry["retime_only"]
            changed = True
    return changed


def _measured_for_its_words(line) -> bool:
    """Whether *line*'s timing is a measurement of its words as they are now
    (not an estimate, not a take of other words): the part of
    ``voice_lines.is_measured`` a pure function can see."""
    current = line.get("timing") or {}
    return current.get("source") not in (None, "estimated") and current.get("text_hash") == timing.text_hash(
        line["text"])


def _non_cut_inside_a_scene(storyboard) -> bool:
    """Whether a transition other than ``cut`` joins two shots of the same
    scene (spec 6.3; ``schemas.storyboard_errors`` names it)."""
    shots = storyboard["shots"]
    same_scene = {prev["shot_id"]: prev["scene_id"] == nxt["scene_id"] for prev, nxt in zip(shots, shots[1:])}
    return any(t["type"] != "cut" and same_scene.get(t["after"]) for t in storyboard["transitions"])


def build_storyboard(script, plans, sources, *, entities, style_lock, template, language, consistency_mode,
                     now, previous=None) -> tuple:
    """*plans* (``{scene_id: [plan, ...]}``) and *sources* (``{scene_id:
    "t1"|"fast"}``) resolved into a complete ``storyboard_v1`` document:
    scenes in the script's own order (only the ones *plans* covers), the
    cross-scene :func:`rule_pass`, ``sh01..`` ids, every shot resolved
    (:func:`resolve_shot`), durations from the episode-level
    :func:`timing.episode_pass` + :func:`timing.allocate_shots`
    (:func:`_time_shots`) in whole frames (the document says so:
    ``whole_frames: true``), and transitions from
    :func:`timing.plan_transitions`. Raises ``ValueError`` (never writes a
    document that fails its own validation -- a bug, not a user error) when
    the result does not pass ``schemas.storyboard_errors`` and
    ``schemas.storyboard_context_errors``. Returns ``(document, notes)``.
    """
    scenes_by_id = {scene["scene_id"]: scene for scene in script["scenes"]}
    scenes_in_order = [scene for scene in script["scenes"] if scene["scene_id"] in plans]

    plans_by_scene = [(scene, plans[scene["scene_id"]]) for scene in scenes_in_order]
    plans_by_scene, notes = rule_pass(plans_by_scene, style_lock)

    shots = []
    resolved_from = {}
    order = 0
    for scene, scene_plans in plans_by_scene:
        for plan in scene_plans:
            order += 1
            shot_id = f"sh{order:02d}"
            line_ids = [scene["lines"][n - 1]["line_id"] for n in plan["lines"]]
            motion = motion_for(plan["framing"], plan["camera_motion"], scene["function"], style_lock)
            resolved = resolve_shot(plan, scene=scene, entities=entities, style_lock=style_lock,
                                    consistency_mode=consistency_mode)
            _collect_resolved_from(resolved_from, plan["subjects"], scene, entities)
            shots.append({
                "shot_id": shot_id, "scene_id": scene["scene_id"], "order": order,
                "framing": plan["framing"], "camera_motion": motion["type"],
                "modifiers": list(plan["modifiers"]), "subject_tags": list(plan["subjects"]),
                "action": plan["action"], "lines": line_ids,
                "image_prompt": resolved["image_prompt"], "video_action": resolved["video_action"],
                "negative_prompt": resolved["negative_prompt"],
                "prompt_override": None, "reference_images": resolved["reference_images"],
                "consistency": resolved["consistency"], "duration_s": 0.0, "keep_still": False,
                "motion": motion, "video_prompt": None,
                "assets": {"image": None, "video": None, "seed": None, "provider": None, "approved": False},
            })

    transitions = timing.plan_transitions(shots, scenes_by_id, template)
    notes.extend(_time_shots(shots, transitions, script, template=template, language=language,
                             style_lock=style_lock, whole_frames=True))

    doc = {
        "$schema": schemas.STORYBOARD_SCHEMA_NAME,
        "ep": script["ep"],
        "shots": shots,
        "transitions": transitions,
        "scenes": {
            scene["scene_id"]: {"source": sources[scene["scene_id"]], "script_rev": scene["rev"], "stale": False}
            for scene in scenes_in_order
        },
        "resolved_from": resolved_from,
        # Timed in whole frames (phase 5 stage 6): the timeline and every
        # re-time read it (timing.board_whole_frames).
        "whole_frames": True,
        "approved_at": None,
        "rev": (previous["rev"] + 1) if previous is not None else 1,
        "created_at": previous["created_at"] if previous is not None else now,
        "updated_at": now,
    }

    errors = schemas.storyboard_errors(doc, min_shot_s=template["min_shot_s"])
    errors += schemas.storyboard_context_errors(doc, script, shots_per_scene=style_lock["episode_defaults"]["shots_per_scene"])
    if errors:
        raise ValueError(f"build_storyboard produced an invalid storyboard: {'; '.join(errors)}")

    return doc, notes


def refresh_prompts(storyboard, script, *, entities, style_lock, consistency_mode) -> dict:
    """*storyboard* with every shot's ``image_prompt``/``video_action``/
    ``negative_prompt``/``reference_images``/``consistency`` and the
    document's ``resolved_from`` re-resolved from *entities* as they are now
    -- plans (framing, camera motion, modifiers, action, subject_tags,
    lines), durations, motion and transitions are left exactly as they were
    (used when an entity changes after the storyboard was built)."""
    scenes_by_id = {scene["scene_id"]: scene for scene in script["scenes"]}
    resolved_from: dict = {}
    new_shots = []
    for shot in storyboard["shots"]:
        scene = scenes_by_id[shot["scene_id"]]
        plan = {"framing": shot["framing"], "action": shot["action"], "subjects": shot["subject_tags"]}
        resolved = resolve_shot(plan, scene=scene, entities=entities, style_lock=style_lock,
                                consistency_mode=consistency_mode)
        _collect_resolved_from(resolved_from, shot["subject_tags"], scene, entities)
        new_shot = dict(shot)
        new_shot["image_prompt"] = resolved["image_prompt"]
        new_shot["video_action"] = resolved["video_action"]
        new_shot["negative_prompt"] = resolved["negative_prompt"]
        new_shot["reference_images"] = resolved["reference_images"]
        new_shot["consistency"] = resolved["consistency"]
        new_shots.append(new_shot)

    new_doc = dict(storyboard)
    new_doc["shots"] = new_shots
    new_doc["resolved_from"] = resolved_from
    return new_doc


def plans_from_storyboard(storyboard, script) -> dict:
    """``{scene_id: [plan, ...]}``: the plans a storyboard was built from,
    read back from its shots (the reverse of :func:`build_storyboard`'s
    resolution) so a step can rebuild the storyboard with some scenes
    re-planned and every other scene kept as it is. Each shot's line ids
    become the 1-based numbers of its scene's lines as they are now; a line
    id the scene no longer has is left out (its scene is stale, and the
    caller knows it), and a shot of a scene the script no longer has is
    dropped. The framings and motions are the rule pass's own (running the
    rule pass on them again changes nothing that did not change around
    them)."""
    scenes_by_id = {scene["scene_id"]: scene for scene in script["scenes"]}
    plans: dict = {}
    for shot in storyboard["shots"]:
        scene = scenes_by_id.get(shot["scene_id"])
        if scene is None:
            continue
        numbers = {line["line_id"]: n for n, line in enumerate(scene["lines"], start=1)}
        plans.setdefault(scene["scene_id"], []).append(_plan(
            framing=shot["framing"], camera_motion=shot["camera_motion"], modifiers=list(shot["modifiers"]),
            action=shot["action"], subjects=list(shot["subject_tags"]),
            lines=[numbers[line_id] for line_id in shot["lines"] if line_id in numbers],
        ))
    return plans
