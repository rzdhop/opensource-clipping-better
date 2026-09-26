"""A stdlib-only JSON-Schema subset validator, plus the closed lists and the
document schemas of AI Story phases 1 and 2 (spec 5, 6.3, 7, 11, 2.1-2.6).

DEC-012: this module is imported by a pytest suite that must run in CI with
pytest alone, so no third-party schema library (``jsonschema``, ``pydantic``)
is used. The subset below covers exactly what the style-template and concept
schemas need. Unknown keywords are ignored on purpose: the same dicts are
meant to be usable later as a JSON Schema handed to an LLM, where extra
keywords (``description``, ``title``, ...) are harmless.
"""

from __future__ import annotations

import re
import unicodedata

from . import defaults

# --------------------------------------------------------------- validator

_TYPE_NAMES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _is_type(value, name) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPE_NAMES[name])


def _search(pattern, text):
    """``re.search`` with JSON Schema's meaning of a final ``$``.

    Schema patterns are ECMA-262, where ``$`` is the very end of the string.
    Python's ``$`` also matches just before a trailing newline, which would let
    ``"fruit_drama\n"`` pass as an id and then be used to build a path.
    """
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        pattern = pattern[:-1] + r"\Z"
    return re.search(pattern, text)


def validate(doc, schema, path="$") -> list:
    """Return human-readable error strings; never raises. Empty means valid."""
    errors: list = []
    if not isinstance(schema, dict):
        return errors

    types = schema.get("type")
    if types is not None:
        names = types if isinstance(types, list) else [types]
        if not any(_is_type(doc, n) for n in names):
            errors.append(f"{path}: expected type {'/'.join(names)}, got {type(doc).__name__}")

    if "const" in schema and doc != schema["const"]:
        errors.append(f"{path}: {doc!r} does not equal const {schema['const']!r}")
    if "enum" in schema and doc not in schema["enum"]:
        errors.append(f"{path}: {doc!r} is not one of {schema['enum']!r}")

    if isinstance(doc, str):
        if "pattern" in schema and _search(schema["pattern"], doc) is None:
            errors.append(f"{path}: {doc!r} does not match {schema['pattern']}")
        if "minLength" in schema and len(doc) < schema["minLength"]:
            errors.append(f"{path}: length {len(doc)} < minLength {schema['minLength']}")
        if "maxLength" in schema and len(doc) > schema["maxLength"]:
            errors.append(f"{path}: length {len(doc)} > maxLength {schema['maxLength']}")

    if isinstance(doc, (int, float)) and not isinstance(doc, bool):
        if "minimum" in schema and doc < schema["minimum"]:
            errors.append(f"{path}: {doc} < minimum {schema['minimum']}")
        if "maximum" in schema and doc > schema["maximum"]:
            errors.append(f"{path}: {doc} > maximum {schema['maximum']}")

    if isinstance(doc, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in doc:
                errors.append(f"{path}.{key}: required property missing")
        if schema.get("additionalProperties") is False:
            for key in doc:
                if key not in properties:
                    errors.append(f"{path}.{key}: additional property not allowed")
        for key, subschema in properties.items():
            if key in doc:
                errors.extend(validate(doc[key], subschema, f"{path}.{key}"))

    if isinstance(doc, list):
        if "minItems" in schema and len(doc) < schema["minItems"]:
            errors.append(f"{path}: {len(doc)} items < minItems {schema['minItems']}")
        if "maxItems" in schema and len(doc) > schema["maxItems"]:
            errors.append(f"{path}: {len(doc)} items > maxItems {schema['maxItems']}")
        if "items" in schema:
            for i, item in enumerate(doc):
                errors.extend(validate(item, schema["items"], f"{path}[{i}]"))

    return errors


class SchemaError(ValueError):
    def __init__(self, name, errors):
        self.name = name
        self.errors = list(errors)
        super().__init__(f"{name}: " + "; ".join(self.errors))

    def __str__(self) -> str:
        lines = "\n".join(f"  - {e}" for e in self.errors)
        return f"{self.name} failed validation:\n{lines}"


def check(doc, schema, name) -> None:
    errors = validate(doc, schema)
    if errors:
        raise SchemaError(name, errors)


def bilingual(max_length=None) -> dict:
    """A required {fr, en} object of non-empty strings, no other keys."""
    field = {"type": "string", "minLength": 1}
    if max_length is not None:
        field["maxLength"] = max_length
    return {
        "type": "object",
        "properties": {"fr": field, "en": field},
        "required": ["fr", "en"],
        "additionalProperties": False,
    }


# ------------------------------------------------------------- closed lists (spec 6.3)

SCENE_FUNCTIONS = ("recap", "hook", "setup", "rising", "peak", "turn", "cliffhanger")
FRAMINGS = (
    "wide_establishing", "medium_single", "medium_two_shot", "close_up",
    "extreme_close_up", "over_shoulder", "low_angle", "high_angle", "insert_prop",
)
CAMERA_MOTIONS = ("hold", "push_in", "pull_out", "pan_lr", "pan_rl", "pan_ud", "pan_du")
MODIFIERS = ("handheld", "jitter_stopmotion")
OVERLAYS = ("paper_texture", "film_grain", "vignette")
TRANSITIONS = ("cut", "dissolve", "fadeblack", "fadewhite", "wipeleft", "wiperight", "slideup")
EMOTIONS = (
    "neutral", "happy", "angry", "shocked", "sad", "scheming",
    "tension", "tender", "fear", "triumph",
)
HOOK_STYLES = ("insert_prop", "shocking_image", "text_overlay")
CLIFFHANGER_STYLES = ("cut_to_black", "hard_stop")
SUBTITLE_MODES = ("word_pop", "two_line", "none")

LANGUAGES = ("fr", "en")
HEX_COLOUR = r"^#[0-9A-Fa-f]{6}$"

# Cue names taken verbatim from the seven templates' audio lines (spec 5.x, 11).
SFX_PACKS = {
    "soap": ("gasp_crowd", "dramatic_sting", "slap", "phone_ring", "waves_soft", "door_slam", "heartbeat"),
    "cartoon_soft": ("boing", "whoosh_soft", "twinkle", "pop", "footsteps_tiny"),
    "anime": ("whoosh_sharp", "impact_hit", "heartbeat", "rain_loop", "chime"),
    "real": ("room_tone", "footsteps_concrete", "car_pass", "phone_buzz", "breath"),
    "cartoon": ("boing", "honk", "slide_whistle", "pop", "record_scratch"),
    "gentle": ("page_turn", "wind_soft", "birds", "twinkle", "footsteps_grass"),
    "foley": ("clay_squish", "wood_knock", "paper_rustle", "tiny_bell", "footsteps_felt"),
}

_ID_PATTERN = r"^[a-z][a-z0-9_]*$"
_NON_EMPTY_STRING = {"type": "string", "minLength": 1}
_HEX_ARRAY = {"type": "array", "items": {"type": "string", "pattern": HEX_COLOUR}, "minItems": 1, "maxItems": 6}

# ------------------------------------------------------- style_template_v1 (spec 5, 2.2)

_PALETTE_SCHEMA = {
    "type": "object",
    "properties": {
        "primary": _HEX_ARRAY,
        "accents": _HEX_ARRAY,
        "forbidden": {"type": "array", "items": {"type": "string"}},
        "palette_line": _NON_EMPTY_STRING,
    },
    "required": ["primary", "accents", "forbidden", "palette_line"],
    "additionalProperties": False,
}

_MOTION_TIER1_SCHEMA = {
    "type": "object",
    "properties": {
        "default_motion": {"type": "string", "enum": list(CAMERA_MOTIONS)},
        "by_function": {"type": "object"},  # keys/values checked by style_template_errors
        "zoom": {
            "type": "object",
            "properties": {
                "dialogue": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                "peak": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                "max": {"type": "number"},
            },
            "required": ["dialogue", "peak", "max"],
            "additionalProperties": False,
        },
        "pan_pct": {"type": "number", "minimum": 0, "maximum": 10},
        "fps": {"type": "integer", "const": 30},
        "easing": {"type": "string", "enum": ["ease_in_out", "linear"]},
        "modifiers": {"type": "array", "items": {"type": "string", "enum": list(MODIFIERS)}},
        "overlays": {"type": "array", "items": {"type": "string", "enum": list(OVERLAYS)}},
        "summary": _NON_EMPTY_STRING,
    },
    "required": [
        "default_motion", "by_function", "zoom", "pan_pct", "fps",
        "easing", "modifiers", "overlays", "summary",
    ],
    "additionalProperties": False,
}

_TYPOGRAPHY_SCHEMA = {
    "type": "object",
    "properties": {
        "font_family": {"type": "string"},
        "font_fallback": {"type": ["string", "null"]},
        "subtitle_mode": {"type": "string", "enum": list(SUBTITLE_MODES)},
        "subtitle_style": {"type": "string"},
        "highlight_colour": {"type": "string", "pattern": HEX_COLOUR},
        "overlay_style": {"type": "string"},
        "ai_label": {"type": "boolean"},
    },
    "required": [
        "font_family", "font_fallback", "subtitle_mode", "subtitle_style",
        "highlight_colour", "overlay_style", "ai_label",
    ],
    "additionalProperties": False,
}

_EPISODE_DEFAULTS_SCHEMA = {
    "type": "object",
    "properties": {
        "hook_style": {"type": "string", "enum": list(HOOK_STYLES)},
        "cliffhanger_style": {"type": "string", "enum": list(CLIFFHANGER_STYLES)},
        "shots_per_scene": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
        "max_places": {"type": "integer", "minimum": 1, "maximum": 4},
        "episode_template_id": {"type": "string", "const": "serial_60s_v1"},
        "scene_clamp_s": {"type": "object"},
    },
    "required": [
        "hook_style", "cliffhanger_style", "shots_per_scene",
        "max_places", "episode_template_id",
    ],
    "additionalProperties": False,
}

_AUDIO_SCHEMA = {
    "type": "object",
    "properties": {
        "bgm_moods": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6},
        "emotion_to_mood": {"type": "object"},  # checked by style_template_errors
        "sfx_pack": {"type": "string"},
        "sfx_cues": {"type": "array", "items": {"type": "string"}},
        "voice_direction": _NON_EMPTY_STRING,
    },
    "required": ["bgm_moods", "emotion_to_mood", "sfx_pack", "sfx_cues", "voice_direction"],
    "additionalProperties": False,
}

STYLE_TEMPLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "$schema": {"type": "string", "const": "style_template_v1"},
        "template_id": {"type": "string", "pattern": _ID_PATTERN},
        "version": {"type": "integer", "minimum": 1},
        "name": bilingual(),
        "rendering": _NON_EMPTY_STRING,
        "camera": _NON_EMPTY_STRING,
        "lighting": _NON_EMPTY_STRING,
        "character_design_rules": _NON_EMPTY_STRING,
        "environment_rules": _NON_EMPTY_STRING,
        "negative_prompt": _NON_EMPTY_STRING,
        "sheet_background": _NON_EMPTY_STRING,
        "quality_tail": _NON_EMPTY_STRING,
        "palette": _PALETTE_SCHEMA,
        "motion_rules": {
            "type": "object",
            "properties": {
                "tier1": _MOTION_TIER1_SCHEMA,
                "tier2_prompt_suffix": _NON_EMPTY_STRING,
            },
            "required": ["tier1", "tier2_prompt_suffix"],
            "additionalProperties": False,
        },
        "typography": _TYPOGRAPHY_SCHEMA,
        "episode_defaults": _EPISODE_DEFAULTS_SCHEMA,
        "audio": _AUDIO_SCHEMA,
        "notes": {"type": "string"},
    },
    "required": [
        "$schema", "template_id", "version", "name", "rendering", "camera", "lighting",
        "character_design_rules", "environment_rules", "negative_prompt",
        "sheet_background", "quality_tail", "palette", "motion_rules",
        "typography", "episode_defaults", "audio",
    ],
    "additionalProperties": False,
}


def _style_prose_extra_errors(doc) -> list:
    """The style-document checks the subset schema cannot express.

    Shared by ``style_template_errors`` and ``style_lock_errors``: a
    ``style_lock_v1`` document carries ``motion_rules``, ``audio`` and
    ``episode_defaults`` in exactly the same shape as a ``style_template_v1``
    document (spec 2.2), so the same cross-field checks apply to both.
    """
    errors = []
    tier1 = doc["motion_rules"]["tier1"]
    valid_functions = set(SCENE_FUNCTIONS) | {"wide_establishing"}
    for key, value in tier1["by_function"].items():
        if key not in valid_functions:
            errors.append(f"$.motion_rules.tier1.by_function: {key!r} is not a scene function")
        if value not in CAMERA_MOTIONS:
            errors.append(f"$.motion_rules.tier1.by_function.{key}: {value!r} is not a camera motion")

    zoom = tier1["zoom"]
    zoom_max = zoom["max"]
    if not (1.0 <= zoom_max <= 1.20):
        errors.append(f"$.motion_rules.tier1.zoom.max: {zoom_max} must be within [1.0, 1.20]")
    for key in ("dialogue", "peak"):
        lo, hi = zoom[key]
        if not (1.0 <= lo <= hi <= zoom_max):
            errors.append(
                f"$.motion_rules.tier1.zoom.{key}: [{lo}, {hi}] must satisfy "
                f"1.0 <= min <= max <= zoom.max ({zoom_max})"
            )

    audio = doc["audio"]
    bgm_moods = set(audio["bgm_moods"])
    emotion_to_mood = audio["emotion_to_mood"]
    if "default" not in emotion_to_mood:
        errors.append("$.audio.emotion_to_mood: missing required key 'default'")
    valid_emotions = set(EMOTIONS) | {"default"}
    for key, value in emotion_to_mood.items():
        if key not in valid_emotions:
            errors.append(f"$.audio.emotion_to_mood: {key!r} is not a known emotion")
        if value not in bgm_moods:
            errors.append(f"$.audio.emotion_to_mood.{key}: {value!r} is not in audio.bgm_moods")

    sfx_pack = audio["sfx_pack"]
    expected_cues = SFX_PACKS.get(sfx_pack)
    if expected_cues is None:
        errors.append(f"$.audio.sfx_pack: {sfx_pack!r} is not a known sfx pack")
    elif list(audio["sfx_cues"]) != list(expected_cues):
        errors.append(f"$.audio.sfx_cues: {audio['sfx_cues']} must equal SFX_PACKS[{sfx_pack!r}] {list(expected_cues)}")

    lo, hi = doc["episode_defaults"]["shots_per_scene"]
    if lo > hi:
        errors.append(f"$.episode_defaults.shots_per_scene: [{lo}, {hi}] min must be <= max")

    return errors


def style_template_errors(tpl) -> list:
    """``validate()`` plus the checks the subset schema cannot express."""
    errors = validate(tpl, STYLE_TEMPLATE_SCHEMA)
    if errors:
        return errors
    return _style_prose_extra_errors(tpl)


# --------------------------------------------------------------- style_lock_v1 (spec 2.2)

STYLE_LOCK_SCHEMA = {
    "type": "object",
    "properties": {
        "$schema": {"type": "string", "const": "style_lock_v1"},
        "template_id": {"type": "string", "pattern": _ID_PATTERN},
        "template_version": {"type": "integer", "minimum": 1},
        "template_name": bilingual(),
        "rendering": _NON_EMPTY_STRING,
        "camera": _NON_EMPTY_STRING,
        "lighting": _NON_EMPTY_STRING,
        "character_design_rules": _NON_EMPTY_STRING,
        "environment_rules": _NON_EMPTY_STRING,
        "negative_prompt": _NON_EMPTY_STRING,
        "sheet_background": _NON_EMPTY_STRING,
        "quality_tail": _NON_EMPTY_STRING,
        "palette": _PALETTE_SCHEMA,
        "motion_rules": {
            "type": "object",
            "properties": {
                "tier1": _MOTION_TIER1_SCHEMA,
                "tier2_prompt_suffix": _NON_EMPTY_STRING,
            },
            "required": ["tier1", "tier2_prompt_suffix"],
            "additionalProperties": False,
        },
        "typography": _TYPOGRAPHY_SCHEMA,
        "episode_defaults": _EPISODE_DEFAULTS_SCHEMA,
        "audio": _AUDIO_SCHEMA,
        # Dotted override path -> value, as actually applied (stylelock.OVERRIDABLE).
        "overrides": {"type": "object"},
        # None until stylelock.lock_style() freezes the document.
        "locked_at": {"type": ["string", "null"]},
        "updated_at": _NON_EMPTY_STRING,
    },
    "required": [
        "$schema", "template_id", "template_version", "template_name", "rendering",
        "camera", "lighting", "character_design_rules", "environment_rules",
        "negative_prompt", "sheet_background", "quality_tail", "palette",
        "motion_rules", "typography", "episode_defaults", "audio",
        "overrides", "locked_at", "updated_at",
    ],
    "additionalProperties": False,
}


def style_lock_errors(lock) -> list:
    """``validate()`` plus the same extra checks as ``style_template_errors``.

    A ``style_lock_v1`` document shares its ``motion_rules``/``audio``/
    ``episode_defaults`` shape with ``style_template_v1``, so the same
    cross-field checks (closed-list membership, zoom bounds, sfx pack
    consistency, ...) apply unchanged.
    """
    errors = validate(lock, STYLE_LOCK_SCHEMA)
    if errors:
        return errors
    return _style_prose_extra_errors(lock)


# ------------------------------------------------------------- concept_v1 (spec 7)

_CAST_MEMBER_SCHEMA = {
    "type": "object",
    "properties": {
        "name": bilingual(),
        "role": {"type": "string", "enum": ["lead", "support", "recurring", "guest"]},
        "archetype": bilingual(),
        "one_line": bilingual(),
        "signature_hint": bilingual(),
    },
    "required": ["name", "role", "archetype", "one_line", "signature_hint"],
    "additionalProperties": False,
}

_STYLE_FIT_SCHEMA = {
    "type": "object",
    "properties": {
        "default": {"type": "string", "pattern": _ID_PATTERN},
        "alternatives": {"type": "array", "items": {"type": "string", "pattern": _ID_PATTERN}},
    },
    "required": ["default", "alternatives"],
    "additionalProperties": False,
}

_CONTENT_FLAGS_SCHEMA = {
    "type": "object",
    "properties": {
        "violence": {"type": "string", "enum": ["none", "mild", "moderate"]},
        "romance": {"type": "string", "enum": ["none", "mild", "moderate"]},
        "age": {"type": "string", "enum": ["all", "10+", "13+", "16+"]},
    },
    "required": ["violence", "romance", "age"],
    "additionalProperties": False,
}

CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        "$schema": {"type": "string", "const": "concept_v1"},
        "concept_id": {"type": "string", "pattern": _ID_PATTERN},
        "version": {"type": "integer", "minimum": 1},
        "title": bilingual(),
        "logline": bilingual(),
        "world": bilingual(),
        "main_line": bilingual(),
        "hook_formula": bilingual(),
        "retention_mechanics": bilingual(),
        "value": bilingual(),
        "cast_sketch": {"type": "array", "items": _CAST_MEMBER_SCHEMA, "minItems": 3, "maxItems": 5},
        "style_fit": _STYLE_FIT_SCHEMA,
        "episode_seed": {"type": "array", "items": bilingual(), "minItems": 3, "maxItems": 6},
        "content_flags": _CONTENT_FLAGS_SCHEMA,
    },
    "required": [
        "$schema", "concept_id", "version", "title", "logline", "world", "main_line",
        "hook_formula", "retention_mechanics", "value", "cast_sketch", "style_fit",
        "episode_seed", "content_flags",
    ],
    "additionalProperties": False,
}


def concept_errors(concept, style_ids) -> list:
    """``validate()`` plus the cross-check against the shipped style ids.

    ``style_ids`` is passed in (rather than imported from ``templates``) to
    avoid a schemas <-> templates import cycle.
    """
    errors = validate(concept, CONCEPT_SCHEMA)
    if errors:
        return errors

    errors = []
    style_ids = set(style_ids)
    default = concept["style_fit"]["default"]
    if default not in style_ids:
        errors.append(f"$.style_fit.default: {default!r} is not a shipped style id")
    for alt in concept["style_fit"]["alternatives"]:
        if alt not in style_ids:
            errors.append(f"$.style_fit.alternatives: {alt!r} is not a shipped style id")

    return errors


# ------------------------------------------------------------ story_bible_v1 (spec 2.1)

STORY_ID_PATTERN = r"^[0-9a-f]{12}$"
# A library concept id, a user-written one, or one derived from an import (spec 12).
CONCEPT_REF_PATTERN = r"^([a-z][a-z0-9_]*|custom|import:[0-9a-f]{12})$"

# Entity ids (spec 2): "<type>_<slug>", the slug an ASCII rendering of the name
# (``slugify``). Each one is also a folder name under the story, so it is
# checked against its pattern before any path is built from it (store.py).
CHAR_ID_PATTERN = r"^char_[a-z0-9_]{1,40}$"
PLACE_ID_PATTERN = r"^place_[a-z0-9_]{1,40}$"
PROP_ID_PATTERN = r"^prop_[a-z0-9_]{1,40}$"


def _nullable_string(max_length) -> dict:
    return {"type": ["string", "null"], "maxLength": max_length}


def _string_array(max_items=None, item_max_length=None) -> dict:
    item = {"type": "string"}
    if item_max_length is not None:
        item["maxLength"] = item_max_length
    schema = {"type": "array", "items": item}
    if max_items is not None:
        schema["maxItems"] = max_items
    return schema


_WORLD_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "setting_summary": {"type": "string", "maxLength": 800},
        "rules": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8},
        "time_period": {"type": "string", "maxLength": 80},
        "recurring_motifs": _string_array(max_items=6),
    },
    "required": ["setting_summary", "rules", "time_period", "recurring_motifs"],
    "additionalProperties": False,
}

_AUDIENCE_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "age": {"type": "string", "maxLength": 10},
        "platforms": {"type": "array", "items": {"type": "string", "enum": ["tiktok", "shorts", "reels"]}},
    },
    "required": ["age", "platforms"],
    "additionalProperties": False,
}

_GENERATION_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        # "integer" as well as the enum: ``True in [1, 2, 3]`` is true in Python.
        "tier": {"type": "integer", "enum": list(defaults.TIERS)},
        "route": {"type": "string", "enum": list(defaults.ROUTES)},
        "consistency_mode": {"type": "string", "enum": list(defaults.CONSISTENCY_MODES)},
        "budget_profile": {"type": "string", "enum": list(defaults.BUDGET_PROFILES)},
    },
    "required": ["tier", "route", "consistency_mode", "budget_profile"],
    "additionalProperties": False,
}

_NARRATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean"},
        "voice": {"type": ["object", "null"]},
    },
    "required": ["enabled", "voice"],
    "additionalProperties": False,
}

# An approval is the timestamp it was given at; null until then.
_APPROVAL = {"type": ["string", "null"], "minLength": 1}

# In the order the story moves through them (store._APPROVAL_STEPS). The last
# three arrived with phase 2; a phase-1 story.json without them is upgraded in
# memory when it is read (store.PHASE2_APPROVALS) and saved with them.
_APPROVALS_SCHEMA = {
    "type": "object",
    "properties": {
        "concept": _APPROVAL, "bible": _APPROVAL, "style": _APPROVAL,
        # Folded from the entities (store.recompute_group_approvals): every
        # lead/support character approved; every place and prop approved.
        "cast": _APPROVAL, "places": _APPROVAL,
        "season": _APPROVAL,
    },
    "required": ["concept", "bible", "style", "cast", "places", "season"],
    "additionalProperties": False,
}


def _id_array(pattern) -> dict:
    return {"type": "array", "items": {"type": "string", "pattern": pattern}}

# Field order is the order a human reads story.json in (the store never sorts keys).
STORY_BIBLE_SCHEMA = {
    "type": "object",
    "properties": {
        "$schema": {"type": "string", "const": "story_bible_v1"},
        "story_id": {"type": "string", "pattern": STORY_ID_PATTERN},
        "title": {"type": "string", "maxLength": 120},
        "language": {"type": "string", "enum": list(LANGUAGES)},
        "seed_text": _nullable_string(2000),
        "concept_id": {"type": ["string", "null"], "pattern": CONCEPT_REF_PATTERN},
        # A localized snapshot of the chosen concept; loose on purpose, since a
        # custom or imported concept need not have the library's shape.
        "concept": {"type": ["object", "null"]},
        "logline": _nullable_string(400),
        "premise": _nullable_string(1500),
        "tone": _nullable_string(200),
        "genre_tags": _string_array(max_items=8, item_max_length=40),
        "world": _WORLD_SCHEMA,
        "themes_and_values": _string_array(max_items=6),
        "audience": _AUDIENCE_SCHEMA,
        "why_come_back": _string_array(max_items=3),
        # Filled by the cast / places steps (phase 2; store.write_entity).
        "cast_ids": _id_array(CHAR_ID_PATTERN),
        "place_ids": _id_array(PLACE_ID_PATTERN),
        "prop_ids": _id_array(PROP_ID_PATTERN),
        "style_template_id": {"type": ["string", "null"], "pattern": _ID_PATTERN},
        "episode_template_id": {"type": "string", "const": defaults.EPISODE_TEMPLATE_ID},
        "generation_profile": _GENERATION_PROFILE_SCHEMA,
        "narrator": _NARRATOR_SCHEMA,
        "approvals": _APPROVALS_SCHEMA,
        "status": {"type": "string", "enum": list(defaults.STATUSES)},
        "created_at": _NON_EMPTY_STRING,
        "updated_at": _NON_EMPTY_STRING,
    },
    "required": [
        "$schema", "story_id", "title", "language", "seed_text", "concept_id", "concept",
        "logline", "premise", "tone", "genre_tags", "world", "themes_and_values",
        "audience", "why_come_back", "cast_ids", "place_ids", "prop_ids",
        "style_template_id", "episode_template_id", "generation_profile", "narrator",
        "approvals", "status", "created_at", "updated_at",
    ],
    "additionalProperties": False,
}


def story_bible_errors(doc) -> list:
    """``validate()`` against ``STORY_BIBLE_SCHEMA``; empty means valid.

    Whether ``status`` agrees with ``approvals`` is not checked here: the
    store derives it on every save (``store.derive_status``), so a document
    on disk can only disagree if a human edited it by hand, and the next save
    corrects it.
    """
    return validate(doc, STORY_BIBLE_SCHEMA)


# ------------------------------------------------------- LLM output schemas (spec 4.1, 4.2)
#
# These describe what a *model* returns for the C1/B1/B2/B3 prompts of
# ``prompts.py`` -- a different shape from the document schemas above, which
# describe what is stored on disk. They follow the same strict-mode subset as
# ``clipping.analysis.schema``: only type/properties/required/
# additionalProperties/items/enum/description, because some providers reject
# minLength/pattern/minItems/etc. in ``strict`` json_schema mode. Lengths and
# counts are enforced by the prompt text and by the ``*_errors`` post-
# validators below, never by the schema itself.

_CAST_SKETCH_ROLES = ("lead", "support", "recurring", "guest")

# How many concepts one C1 reply carries. One: two French cards need ~900-1,100
# output tokens and were cut off mid-JSON at the old 500 cap (2026-09-26); one
# card per call halves what a single reply must fit in ``prompts.MAX_TOKENS``.
# Defined here, not in ``prompts``, because ``prompts`` imports this module;
# ``prompts.C1_CONCEPTS_PER_CALL`` is this value.
C1_CONCEPTS_PER_CALL = 1


def _llm_obj(properties, required=None) -> dict:
    """Object schema for a strict-mode LLM call: every property required
    unless *required* says otherwise, ``additionalProperties`` always False.

    Mirrors ``clipping.analysis.schema._obj``; kept local rather than
    imported so this module stays free of a dependency on ``clipping.analysis``.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": list(required if required is not None else properties),
        "additionalProperties": False,
    }


def c1_schema(style_ids) -> dict:
    """The C1 output schema (spec 4.2, row C1): ``{"concepts": [...]}`` with
    ``C1_CONCEPTS_PER_CALL`` concept(s) -- the envelope a card is made from.

    ``style_fit`` is constrained to *style_ids* (the shipped style templates),
    passed in by the caller so this module needs no import of ``templates``.
    """
    cast_member = _llm_obj({
        "name": {"type": "string", "description": "the character's name"},
        "role": {"type": "string", "enum": list(_CAST_SKETCH_ROLES)},
        "one_line": {"type": "string", "description": "one sentence describing this character"},
    })
    concept = _llm_obj({
        "title": {"type": "string", "description": "at most 8 words"},
        "logline": {"type": "string", "description": "one sentence, at most 30 words"},
        "world": {"type": "string", "description": "the setting and premise, at most 60 words"},
        "cast_sketch": {
            "type": "array",
            "description": "3 to 5 characters",
            "items": cast_member,
        },
        "hook_formula": {"type": "string", "description": "what makes someone stop scrolling on episode 1"},
        "value": {"type": "string", "description": "the real substance this story carries"},
        "retention_mechanics": {"type": "string", "description": "why someone comes back for episode 2"},
        "style_fit": {"type": "string", "enum": list(style_ids)},
    })
    return _llm_obj({
        "concepts": {
            "type": "array",
            "description": f"exactly {C1_CONCEPTS_PER_CALL} concept(s)",
            "items": concept,
        },
    })


B1_SCHEMA = _llm_obj({
    "logline": {"type": "string", "description": "one sentence, at most 30 words"},
    "premise": {"type": "string", "description": "2-6 sentences, at most 120 words"},
    "tone": {"type": "string", "description": "at most 15 words"},
    "genre_tags": {
        "type": "array",
        "description": "2-5 tags, each at most 3 words",
        "items": {"type": "string"},
    },
})

B2_SCHEMA = _llm_obj({
    "setting_summary": {"type": "string", "description": "at most 80 words"},
    "rules": {"type": "array", "description": "4-6 rules, each at most 25 words", "items": {"type": "string"}},
    "time_period": {"type": "string", "description": "at most 6 words"},
    "recurring_motifs": {"type": "array", "description": "exactly 3 motifs", "items": {"type": "string"}},
})

B3_SCHEMA = _llm_obj({
    "themes_and_values": {
        "type": "array",
        "description": "2-4 themes, each at most 12 words",
        "items": {"type": "string"},
    },
    "audience": _llm_obj({
        "age": {"type": "string", "enum": ["all", "10+", "13+", "16+"]},
        "platforms": {
            "type": "array",
            "description": "1-3 platforms, no duplicates",
            "items": {"type": "string", "enum": ["tiktok", "shorts", "reels"]},
        },
    }),
    "why_come_back": {
        "type": "array",
        "description": "exactly 3 lines, each at most 20 words",
        "items": {"type": "string"},
    },
})

_SENTENCE_END = re.compile(r"[.!?]+")


def _words(text) -> int:
    return len(text.split())


def _sentences(text) -> int:
    return len(_SENTENCE_END.findall(text))


def _check_text(errors, path, value, *, max_words=None) -> None:
    if not (isinstance(value, str) and value.strip()):
        errors.append(f"{path}: must be a non-empty string")
        return
    if max_words is not None and _words(value) > max_words:
        errors.append(f"{path}: {_words(value)} words, expected at most {max_words}")


def c1_errors(doc, style_ids) -> list:
    """Post-validation for a C1 response, beyond what ``c1_schema`` can express."""
    schema = c1_schema(style_ids)
    errors = validate(doc, schema)
    if errors:
        return errors

    errors = []
    concepts = doc["concepts"]
    if len(concepts) != C1_CONCEPTS_PER_CALL:
        errors.append(f"$.concepts: {len(concepts)} concept(s), expected exactly {C1_CONCEPTS_PER_CALL}")

    allowed_styles = set(style_ids)
    for i, concept in enumerate(concepts):
        path = f"$.concepts[{i}]"
        _check_text(errors, f"{path}.title", concept["title"], max_words=8)
        _check_text(errors, f"{path}.logline", concept["logline"], max_words=30)
        _check_text(errors, f"{path}.world", concept["world"], max_words=60)
        _check_text(errors, f"{path}.hook_formula", concept["hook_formula"])
        _check_text(errors, f"{path}.value", concept["value"])
        _check_text(errors, f"{path}.retention_mechanics", concept["retention_mechanics"])

        cast = concept["cast_sketch"]
        if not (3 <= len(cast) <= 5):
            errors.append(f"{path}.cast_sketch: {len(cast)} member(s), expected 3-5")
        for j, member in enumerate(cast):
            member_path = f"{path}.cast_sketch[{j}]"
            _check_text(errors, f"{member_path}.name", member["name"])
            _check_text(errors, f"{member_path}.one_line", member["one_line"], max_words=25)

        style_fit = concept["style_fit"]
        if style_fit not in allowed_styles:
            errors.append(f"{path}.style_fit: {style_fit!r} is not a shipped style id")

    return errors


def b1_errors(doc) -> list:
    """Post-validation for a B1 response, beyond what ``B1_SCHEMA`` can express."""
    errors = validate(doc, B1_SCHEMA)
    if errors:
        return errors

    errors = []
    _check_text(errors, "$.logline", doc["logline"], max_words=30)

    premise = doc["premise"]
    _check_text(errors, "$.premise", premise, max_words=120)
    if isinstance(premise, str) and premise.strip():
        count = _sentences(premise)
        if not (2 <= count <= 6):
            errors.append(f"$.premise: {count} sentence(s), expected 2-6")

    _check_text(errors, "$.tone", doc["tone"], max_words=15)

    tags = doc["genre_tags"]
    if not (2 <= len(tags) <= 5):
        errors.append(f"$.genre_tags: {len(tags)} tag(s), expected 2-5")
    for i, tag in enumerate(tags):
        _check_text(errors, f"$.genre_tags[{i}]", tag, max_words=3)

    return errors


def b2_errors(doc) -> list:
    """Post-validation for a B2 response, beyond what ``B2_SCHEMA`` can express."""
    errors = validate(doc, B2_SCHEMA)
    if errors:
        return errors

    errors = []
    _check_text(errors, "$.setting_summary", doc["setting_summary"], max_words=80)

    rules = doc["rules"]
    if not (4 <= len(rules) <= 6):
        errors.append(f"$.rules: {len(rules)} rule(s), expected 4-6")
    for i, rule in enumerate(rules):
        _check_text(errors, f"$.rules[{i}]", rule, max_words=25)

    _check_text(errors, "$.time_period", doc["time_period"], max_words=6)

    motifs = doc["recurring_motifs"]
    if len(motifs) != 3:
        errors.append(f"$.recurring_motifs: {len(motifs)} motif(s), expected exactly 3")
    for i, motif in enumerate(motifs):
        _check_text(errors, f"$.recurring_motifs[{i}]", motif)

    return errors


def b3_errors(doc) -> list:
    """Post-validation for a B3 response, beyond what ``B3_SCHEMA`` can express."""
    errors = validate(doc, B3_SCHEMA)
    if errors:
        return errors

    errors = []
    themes = doc["themes_and_values"]
    if not (2 <= len(themes) <= 4):
        errors.append(f"$.themes_and_values: {len(themes)} theme(s), expected 2-4")
    for i, theme in enumerate(themes):
        _check_text(errors, f"$.themes_and_values[{i}]", theme, max_words=12)

    platforms = doc["audience"]["platforms"]
    if not (1 <= len(platforms) <= 3):
        errors.append(f"$.audience.platforms: {len(platforms)} platform(s), expected 1-3")
    if len(platforms) != len(set(platforms)):
        errors.append("$.audience.platforms: duplicate platforms are not allowed")

    lines = doc["why_come_back"]
    if len(lines) != 3:
        errors.append(f"$.why_come_back: {len(lines)} line(s), expected exactly 3")
    for i, line in enumerate(lines):
        _check_text(errors, f"$.why_come_back[{i}]", line, max_words=20)

    return errors


# ------------------------------------------------------ story_concepts_v1 (spec 3, step 2)
#
# ``concepts.json``: the cards "Generate 10 more" appended to a story, one per
# accepted C1 concept. A card is the C1 concept as the model wrote it (already
# in the story's language, so not bilingual like a library concept) plus where
# it came from. Its ``cast_sketch`` members have the ``name``/``role``/
# ``one_line`` shape ``context.concept_block`` renders, so a chosen card feeds
# the bible prompts exactly as a localized library concept does.

STORY_CONCEPTS_SCHEMA_NAME = "story_concepts_v1"

# gen_01 ... gen_99, then gen_100 ...: two digits at least, never gen_00.
GENERATED_CONCEPT_ID_PATTERN = r"^gen_(0[1-9]|[1-9][0-9]+)$"

_GENERATED_CAST_MEMBER_SCHEMA = {
    "type": "object",
    "properties": {
        "name": _NON_EMPTY_STRING,
        "role": {"type": "string", "enum": list(_CAST_SKETCH_ROLES)},
        "one_line": _NON_EMPTY_STRING,
    },
    "required": ["name", "role", "one_line"],
    "additionalProperties": False,
}

STORY_CONCEPT_CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "concept_id": {"type": "string", "pattern": GENERATED_CONCEPT_ID_PATTERN},
        "source": {"type": "string", "const": "generated"},
        "prompt_version": _NON_EMPTY_STRING,
        "created_at": _NON_EMPTY_STRING,
        "language": {"type": "string", "enum": list(LANGUAGES)},
        "title": _NON_EMPTY_STRING,
        "logline": _NON_EMPTY_STRING,
        "world": _NON_EMPTY_STRING,
        "cast_sketch": {"type": "array", "items": _GENERATED_CAST_MEMBER_SCHEMA, "minItems": 3, "maxItems": 5},
        "hook_formula": _NON_EMPTY_STRING,
        "value": _NON_EMPTY_STRING,
        "retention_mechanics": _NON_EMPTY_STRING,
        # A shipped style id when it was written; only the shape is checked
        # here, so retiring a style later cannot invalidate an old file.
        "style_fit": {"type": "string", "pattern": _ID_PATTERN},
    },
    "required": [
        "concept_id", "source", "prompt_version", "created_at", "language", "title",
        "logline", "world", "cast_sketch", "hook_formula", "value",
        "retention_mechanics", "style_fit",
    ],
    "additionalProperties": False,
}

STORY_CONCEPTS_SCHEMA = {
    "type": "object",
    "properties": {
        "$schema": {"type": "string", "const": STORY_CONCEPTS_SCHEMA_NAME},
        "concepts": {"type": "array", "items": STORY_CONCEPT_CARD_SCHEMA},
        "updated_at": _NON_EMPTY_STRING,
    },
    "required": ["$schema", "concepts", "updated_at"],
    "additionalProperties": False,
}


def story_concepts_errors(doc) -> list:
    """``validate()`` against ``STORY_CONCEPTS_SCHEMA``, plus unique card ids."""
    errors = validate(doc, STORY_CONCEPTS_SCHEMA)
    if errors:
        return errors
    seen = set()
    for i, card in enumerate(doc["concepts"]):
        concept_id = card["concept_id"]
        if concept_id in seen:
            errors.append(f"$.concepts[{i}].concept_id: {concept_id!r} is used twice")
        seen.add(concept_id)
    return errors


# ------------------------------------------------------- style_preview_v1 (spec 3 step 4)

# The preview strip of the style step (phase-1 plan 2): which lock it shows,
# the images kept in styles/preview/, and the samples no link could make.

STYLE_PREVIEW_SCHEMA_NAME = "style_preview_v1"

# The only names a preview image has on disk, and the only ones
# GET /api/stories/{id}/files/{name} serves.
PREVIEW_IMAGE_NAME_PATTERN = r"^preview_[1-9]\.(png|jpg|jpeg|webp)$"

_PREVIEW_IMAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "pattern": PREVIEW_IMAGE_NAME_PATTERN},
        "n": {"type": "integer", "minimum": 1, "maximum": 9},
        "link": _NON_EMPTY_STRING,
        "seed": {"type": "integer", "minimum": 0},
        "paid": {"type": "boolean"},
        "est_usd": {"type": "number", "minimum": 0},
        "prompt": _NON_EMPTY_STRING,
    },
    "required": ["name", "n", "link", "seed", "paid", "est_usd", "prompt"],
    "additionalProperties": False,
}

_PREVIEW_FAILURE_SCHEMA = {
    "type": "object",
    "properties": {
        "n": {"type": "integer", "minimum": 1, "maximum": 9},
        "reasons": {"type": "array", "items": _NON_EMPTY_STRING, "minItems": 1},
    },
    "required": ["n", "reasons"],
    "additionalProperties": False,
}

STYLE_PREVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "$schema": {"type": "string", "const": STYLE_PREVIEW_SCHEMA_NAME},
        "template_id": {"type": "string", "pattern": _ID_PATTERN},
        "template_version": {"type": "integer", "minimum": 1},
        # The lock's overrides when the preview was made (stylelock.OVERRIDABLE paths).
        "overrides": {"type": "object"},
        "images": {"type": "array", "items": _PREVIEW_IMAGE_SCHEMA, "maxItems": 9},
        "failed": {"type": "array", "items": _PREVIEW_FAILURE_SCHEMA, "maxItems": 9},
        "updated_at": _NON_EMPTY_STRING,
    },
    "required": ["$schema", "template_id", "template_version", "overrides", "images", "failed", "updated_at"],
    "additionalProperties": False,
}


def style_preview_errors(doc) -> list:
    """``validate()`` against ``STYLE_PREVIEW_SCHEMA``, plus: an image's name is
    its own number, and no sample is listed twice."""
    errors = validate(doc, STYLE_PREVIEW_SCHEMA)
    if errors:
        return errors
    seen = set()
    for key in ("images", "failed"):
        for i, entry in enumerate(doc[key]):
            n = entry["n"]
            if key == "images" and not entry["name"].startswith(f"preview_{n}."):
                errors.append(f"$.images[{i}].name: {entry['name']!r} is not sample {n}'s name")
            if n in seen:
                errors.append(f"$.{key}[{i}].n: sample {n} is listed twice")
            seen.add(n)
    return errors


# ================================================================ phase 2 documents
#
# Spec 2.3-2.6 and the phase-2 plan's "Documents": what the cast, places and
# season steps keep on disk. Every fixed object is closed; each ``*_errors``
# function adds the rules the subset cannot express (word caps, an image named
# and labelled for its slot, the keys of an open object, the arc's episodes).

def _document(properties) -> dict:
    """A closed object: every property required, no other key allowed."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _or_null(schema) -> dict:
    types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    return {**schema, "type": types + ["null"]}


def _text(max_length) -> dict:
    return {"type": "string", "minLength": 1, "maxLength": max_length}


# A timestamp once it happened, null until then (an entity's approved_at).
_TIMESTAMP_OR_NULL = {"type": ["string", "null"], "minLength": 1}

# ---------------------------------------------------------------------- entity ids

# store.ENTITY_KINDS -> the prefix of their ids (CHAR/PLACE/PROP_ID_PATTERN).
ENTITY_ID_PREFIXES = {"characters": "char_", "places": "place_", "props": "prop_"}

SLUG_MAX = 40
SLUG_FALLBACK = "item"

# Letters NFKD leaves whole, which the ASCII pass would otherwise drop
# ("Sœur" would become "sur").
_SLUG_LETTERS = str.maketrans({
    "ß": "ss", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "ø": "o", "Ø": "o",
    "đ": "d", "Đ": "d", "ł": "l", "Ł": "l", "þ": "th", "Þ": "th", "ð": "d", "Ð": "d",
})
_SLUG_SEPARATORS = re.compile(r"[^a-z0-9]+")


def slugify(name) -> str:
    """The ASCII slug of *name*: NFKD with the accents dropped, lowercase,
    every run of anything but ``[a-z0-9]`` one ``_``, trimmed, at most
    ``SLUG_MAX`` characters -- and never empty (``"item"``).

    ``"Mamie Figue"`` -> ``"mamie_figue"``, ``"Éléonore"`` -> ``"eleonore"``.
    """
    if not isinstance(name, str):
        raise ValueError(f"a name must be a string, not {type(name).__name__}")
    text = unicodedata.normalize("NFKD", name).translate(_SLUG_LETTERS)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_SEPARATORS.sub("_", text).strip("_")
    return slug[:SLUG_MAX].rstrip("_") or SLUG_FALLBACK


def entity_id(kind, name, taken=()) -> str:
    """A new id for an entity of *kind* (``characters``/``places``/``props``)
    named *name*: its prefix + ``slugify(name)``, then ``_2``, ``_3``... until
    it is not in *taken*. The slug is shortened to make room for the suffix,
    so the id always matches its kind's pattern."""
    prefix = ENTITY_ID_PREFIXES.get(kind) if isinstance(kind, str) else None
    if prefix is None:
        raise ValueError(f"unknown entity kind {kind!r} (known: {', '.join(ENTITY_ID_PREFIXES)})")
    taken = set(taken)
    slug = slugify(name)
    candidate = prefix + slug
    n = 1
    while candidate in taken:
        n += 1
        suffix = f"_{n}"
        candidate = prefix + slug[: SLUG_MAX - len(suffix)].rstrip("_") + suffix
    return candidate


# ---------------------------------------------------------------------- media names

# The only names an entity's files have on disk, and the only ones the entity
# media route will serve (store.MEDIA_NAME_PATTERNS). Checked before any path
# is built from them.
TIME_VARIANT_PATTERN = r"^[a-z][a-z0-9_]{0,19}$"
# The master plate of a place IS its "day" variant (spec 2.4): one source of
# truth, no separate master_plate field.
MASTER_PLATE_VARIANT = "day"

# Any reference image, whatever its entity (the name field of an image ref)...
REF_IMAGE_NAME_PATTERN = (
    r"^(portrait|turnaround|expressions|variant_[a-z][a-z0-9_]{0,19}|image|extra_[0-9]{2})"
    r"\.(png|jpg|jpeg|webp)$"
)
# ... and the ones each kind may keep in its refs/ folder.
CHARACTER_REF_NAME_PATTERN = r"^(portrait|turnaround|expressions|extra_[0-9]{2})\.(png|jpg|jpeg|webp)$"
PLACE_REF_NAME_PATTERN = r"^variant_[a-z][a-z0-9_]{0,19}\.(png|jpg|jpeg|webp)$"
PROP_REF_NAME_PATTERN = r"^image\.(png|jpg|jpeg|webp)$"
EXTRA_REF_NAME_PATTERN = r"^extra_[0-9]{2}\.(png|jpg|jpeg|webp)$"
# A user's design reference, re-encoded and named by a uuid (refs/uploads/).
UPLOAD_NAME_PATTERN = r"^[0-9a-f]{32}\.png$"
# A character's voice sample, at the root of its folder.
VOICE_SAMPLE_NAME_PATTERN = r"^voice_sample\.(mp3|wav)$"

# How each generated image was made (spec 8.1): "base" = without references by
# design (a portrait, a master plate, a prop image); "references" = edited with
# reference images; "prompt_only" = the degraded mode the user chose, where
# the prompt block and seed alone carry the look.
CONSISTENCY = ("base", "references", "prompt_only")
_BASE_ONLY = ("base",)
_DERIVED = ("references", "prompt_only")

_IMAGE_REF_SCHEMA = _document({
    "name": {"type": "string", "pattern": REF_IMAGE_NAME_PATTERN},
    "consistency": {"type": "string", "enum": list(CONSISTENCY)},
    # The chain link that made it ("pollinations/flux", "local/comfyui"...).
    "source": _text(120),
    "seed": {"type": ["integer", "null"], "minimum": 0},
    "created_at": _NON_EMPTY_STRING,
})
_IMAGE_REF_OR_NULL = _or_null(_IMAGE_REF_SCHEMA)


def _slot_errors(errors, path, ref, *, stem, allowed) -> None:
    """An image in its slot is named after the slot and labelled as the slot
    is made (a portrait is never "references"; a turnaround never "base")."""
    if ref is None:
        return
    if not ref["name"].startswith(stem + "."):
        errors.append(f"{path}.name: {ref['name']!r} is not a {stem} image")
    if ref["consistency"] not in allowed:
        errors.append(f"{path}.consistency: {ref['consistency']!r} must be one of {list(allowed)} here")


def _unique_names(errors, path, entries) -> None:
    seen = set()
    for i, entry in enumerate(entries):
        if entry["name"] in seen:
            errors.append(f"{path}[{i}].name: {entry['name']!r} is listed twice")
        seen.add(entry["name"])


# ------------------------------------------------------------ character_v1 (spec 2.3)

CHARACTER_SCHEMA_NAME = "character_v1"
CHARACTER_ROLES = _CAST_SKETCH_ROLES
# The roles the cast approval waits for; recurring and guest are optional.
CAST_APPROVAL_ROLES = ("lead", "support")
CHARACTER_SOURCES = ("sketch", "custom")

DESCRIPTOR_MAX_WORDS = 45
SIGNATURE_ITEMS_WRITTEN = (2, 3)
SAMPLE_LINE_MAX_WORDS = 12
RELATIONSHIP_MAX_LENGTH = 120
MAX_UPLOADS = 4

VOICE_RATE_PATTERN = r"^[+-][0-9]{1,3}%$"
VOICE_PITCH_PATTERN = r"^[+-][0-9]{1,3}Hz$"

_VOICE_SCHEMA = _or_null(_document({
    "provider": {"type": "string", "pattern": r"^[a-z][a-z0-9_]{0,39}$"},
    "voice_id": _text(120),
    "rate": {"type": ["string", "null"], "pattern": VOICE_RATE_PATTERN},
    "pitch": {"type": ["string", "null"], "pattern": VOICE_PITCH_PATTERN},
    "direction": {"type": "string", "maxLength": 200},
    # Written by K1 in the story's language, in character (<= 12 words).
    "sample_line": _text(120),
}))

_UPLOAD_SCHEMA = _document({
    "name": {"type": "string", "pattern": UPLOAD_NAME_PATTERN},
    # What the vision chain saw in it (U1), folded into the descriptor.
    "description": {"type": ["string", "null"], "maxLength": 400},
    "uploaded_at": _NON_EMPTY_STRING,
})

CHARACTER_SCHEMA = _document({
    "$schema": {"type": "string", "const": CHARACTER_SCHEMA_NAME},
    "char_id": {"type": "string", "pattern": CHAR_ID_PATTERN},
    "name": _text(60),
    "role": {"type": "string", "enum": list(CHARACTER_ROLES)},
    "archetype": {"type": "string", "maxLength": 60},
    "one_line": _text(200),
    # null until K1 writes the character (<= 45 words, checked by character_errors).
    "descriptor": {"type": ["string", "null"], "minLength": 1},
    "signature_items": {"type": "array", "items": _text(60), "maxItems": 3},
    "personality": _document({
        "traits": {"type": "array", "items": _text(40), "maxItems": 5},
        "wants": _nullable_string(200),
        "fears": _nullable_string(200),
        "speech_style": _nullable_string(200),
    }),
    # Another character's id -> what they are to this one (checked by character_errors).
    "relationships": {"type": "object"},
    "voice": _VOICE_SCHEMA,
    "refs": _document({
        "portrait": _IMAGE_REF_OR_NULL,
        "turnaround": _IMAGE_REF_OR_NULL,
        "expressions": _IMAGE_REF_OR_NULL,
        "extra": {"type": "array", "items": _IMAGE_REF_SCHEMA},
        "uploads": {"type": "array", "items": _UPLOAD_SCHEMA, "maxItems": MAX_UPLOADS},
    }),
    # The portrait's seed, reused when the provider honours seeds.
    "ref_seed": {"type": ["integer", "null"], "minimum": 0},
    "prompt_block": {"type": ["string", "null"]},
    "state": _document({
        "alive": {"type": "boolean"},
        "location": {"type": ["string", "null"], "pattern": PLACE_ID_PATTERN},
        "arc_notes": {"type": "array", "items": _NON_EMPTY_STRING},
    }),
    # Picked from the concept's cast sketch, or written by the user.
    "source": {"type": "string", "enum": list(CHARACTER_SOURCES)},
    "approved_at": _TIMESTAMP_OR_NULL,
    "created_at": _NON_EMPTY_STRING,
    "updated_at": _NON_EMPTY_STRING,
})


def character_errors(doc) -> list:
    """``validate()`` against ``CHARACTER_SCHEMA``, plus: the descriptor's word
    cap and 2-3 signature items once it is written, relationship keys are
    other characters' ids, the sample line's word cap, each image named and
    labelled for its slot, and no file listed twice."""
    errors = validate(doc, CHARACTER_SCHEMA)
    if errors:
        return errors

    errors = []
    _check_text(errors, "$.name", doc["name"])
    _check_text(errors, "$.one_line", doc["one_line"])
    if doc["descriptor"] is not None:
        _check_text(errors, "$.descriptor", doc["descriptor"], max_words=DESCRIPTOR_MAX_WORDS)
        lo, hi = SIGNATURE_ITEMS_WRITTEN
        count = len(doc["signature_items"])
        if not lo <= count <= hi:
            errors.append(f"$.signature_items: {count} item(s), expected {lo}-{hi} once the descriptor is written")

    for key, value in doc["relationships"].items():
        if not (isinstance(key, str) and _search(CHAR_ID_PATTERN, key)):
            errors.append(f"$.relationships: {key!r} is not a character id")
        elif key == doc["char_id"]:
            errors.append(f"$.relationships: {key!r} is the character itself")
        if not (isinstance(value, str) and value.strip()) or len(value) > RELATIONSHIP_MAX_LENGTH:
            errors.append(f"$.relationships.{key}: expected a non-empty string of at most "
                          f"{RELATIONSHIP_MAX_LENGTH} characters")

    if doc["voice"] is not None:
        _check_text(errors, "$.voice.sample_line", doc["voice"]["sample_line"], max_words=SAMPLE_LINE_MAX_WORDS)

    refs = doc["refs"]
    _slot_errors(errors, "$.refs.portrait", refs["portrait"], stem="portrait", allowed=_BASE_ONLY)
    _slot_errors(errors, "$.refs.turnaround", refs["turnaround"], stem="turnaround", allowed=_DERIVED)
    _slot_errors(errors, "$.refs.expressions", refs["expressions"], stem="expressions", allowed=_DERIVED)
    for i, extra in enumerate(refs["extra"]):
        if _search(EXTRA_REF_NAME_PATTERN, extra["name"]) is None:
            errors.append(f"$.refs.extra[{i}].name: {extra['name']!r} is not an extra image")
    _unique_names(errors, "$.refs.extra", refs["extra"])
    _unique_names(errors, "$.refs.uploads", refs["uploads"])
    return errors


# ---------------------------------------------------------------- place_v1 (spec 2.4)

PLACE_SCHEMA_NAME = "place_v1"
LAYOUT_NOTES_MAX_WORDS = 60

PLACE_SCHEMA = _document({
    "$schema": {"type": "string", "const": PLACE_SCHEMA_NAME},
    "place_id": {"type": "string", "pattern": PLACE_ID_PATTERN},
    "name": _text(60),
    "one_line": _text(200),
    "descriptor": {"type": ["string", "null"], "minLength": 1},
    # What is left/right/back, for continuity (<= 60 words).
    "layout_notes": {"type": ["string", "null"], "minLength": 1},
    # Variant name -> its image ref, or null until made. "day" is always there
    # and its image is the master plate (checked by place_errors).
    "time_variants": {"type": "object"},
    "prompt_block": {"type": ["string", "null"]},
    "approved_at": _TIMESTAMP_OR_NULL,
    "created_at": _NON_EMPTY_STRING,
    "updated_at": _NON_EMPTY_STRING,
})


def place_errors(doc) -> list:
    """``validate()`` against ``PLACE_SCHEMA``, plus the word caps and the
    time variants: named ``TIME_VARIANT_PATTERN``, "day" among them, each an
    image ref (or null) named ``variant_<name>.*``; the day plate is a base
    image, every other variant is made from it."""
    errors = validate(doc, PLACE_SCHEMA)
    if errors:
        return errors

    errors = []
    _check_text(errors, "$.name", doc["name"])
    _check_text(errors, "$.one_line", doc["one_line"])
    if doc["descriptor"] is not None:
        _check_text(errors, "$.descriptor", doc["descriptor"], max_words=DESCRIPTOR_MAX_WORDS)
    if doc["layout_notes"] is not None:
        _check_text(errors, "$.layout_notes", doc["layout_notes"], max_words=LAYOUT_NOTES_MAX_WORDS)

    variants = doc["time_variants"]
    if MASTER_PLATE_VARIANT not in variants:
        errors.append(f"$.time_variants: missing {MASTER_PLATE_VARIANT!r} (the master plate)")
    for key, ref in variants.items():
        if not (isinstance(key, str) and _search(TIME_VARIANT_PATTERN, key)):
            errors.append(f"$.time_variants: {key!r} is not a variant name ({TIME_VARIANT_PATTERN})")
            continue
        path = f"$.time_variants.{key}"
        found = validate(ref, _IMAGE_REF_OR_NULL, path)
        if found:
            errors.extend(found)
            continue
        allowed = _BASE_ONLY if key == MASTER_PLATE_VARIANT else _DERIVED
        _slot_errors(errors, path, ref, stem=f"variant_{key}", allowed=allowed)
    return errors


# ----------------------------------------------------------------- prop_v1 (spec 2.5)

PROP_SCHEMA_NAME = "prop_v1"
PROP_DESCRIPTOR_MAX_WORDS = 30

PROP_SCHEMA = _document({
    "$schema": {"type": "string", "const": PROP_SCHEMA_NAME},
    "prop_id": {"type": "string", "pattern": PROP_ID_PATTERN},
    "name": _text(60),
    "one_line": _text(200),
    "descriptor": {"type": ["string", "null"], "minLength": 1},
    "owner_char_id": {"type": ["string", "null"], "pattern": CHAR_ID_PATTERN},
    "image": _IMAGE_REF_OR_NULL,
    "prompt_block": {"type": ["string", "null"]},
    "approved_at": _TIMESTAMP_OR_NULL,
    "created_at": _NON_EMPTY_STRING,
    "updated_at": _NON_EMPTY_STRING,
})


def prop_errors(doc) -> list:
    """``validate()`` against ``PROP_SCHEMA``, plus the descriptor's word cap
    and the image named ``image.*`` and labelled "base"."""
    errors = validate(doc, PROP_SCHEMA)
    if errors:
        return errors

    errors = []
    _check_text(errors, "$.name", doc["name"])
    _check_text(errors, "$.one_line", doc["one_line"])
    if doc["descriptor"] is not None:
        _check_text(errors, "$.descriptor", doc["descriptor"], max_words=PROP_DESCRIPTOR_MAX_WORDS)
    _slot_errors(errors, "$.image", doc["image"], stem="image", allowed=_BASE_ONLY)
    return errors


# ----------------------------------------------------------- season_arc_v1 (spec 2.6)

SEASON_ARC_SCHEMA_NAME = "season_arc_v1"
ARC_FUNCTIONS = ("setup", "escalation", "complication", "midpoint_twist", "crisis", "climax_and_reset")
EPISODES_PLANNED_MIN = 3
EPISODES_PLANNED_MAX = 12
ARC_SUMMARY_MAX_WORDS = 60
HOOK_MAX_LENGTH = 120

_HOOKS = {"type": "array", "items": _text(HOOK_MAX_LENGTH)}

_ARC_ENTRY_SCHEMA = _document({
    "ep": {"type": "integer", "minimum": 1},
    "function": {"type": "string", "enum": list(ARC_FUNCTIONS)},
    "summary": _NON_EMPTY_STRING,
    "open_hooks_in": _HOOKS,
    "open_hooks_out": _HOOKS,
    "characters": _id_array(CHAR_ID_PATTERN),
})

SEASON_ARC_SCHEMA = _document({
    "$schema": {"type": "string", "const": SEASON_ARC_SCHEMA_NAME},
    "episodes_planned": {"type": "integer", "minimum": EPISODES_PLANNED_MIN, "maximum": EPISODES_PLANNED_MAX},
    "arc": {"type": "array", "items": _ARC_ENTRY_SCHEMA, "maxItems": EPISODES_PLANNED_MAX},
    # Updated after every approved episode (phase 3+); empty until then.
    "series_memory": _document({
        "recaps": {"type": "object"},
        "open_hooks": {"type": "array", "items": {"type": "string"}},
        "relationship_state": {"type": "object"},
        "introduced": {"type": "object"},
    }),
    "audience_feedback": {"type": "array", "items": {"type": "object"}},
    "approved_at": _TIMESTAMP_OR_NULL,
    "updated_at": _NON_EMPTY_STRING,
})


def season_arc_errors(doc) -> list:
    """``validate()`` against ``SEASON_ARC_SCHEMA``, plus: each summary's word
    cap; the arc is empty (before S1) or exactly episodes 1..episodes_planned
    in order; an empty arc is never approved."""
    errors = validate(doc, SEASON_ARC_SCHEMA)
    if errors:
        return errors

    errors = []
    arc = doc["arc"]
    for i, entry in enumerate(arc):
        _check_text(errors, f"$.arc[{i}].summary", entry["summary"], max_words=ARC_SUMMARY_MAX_WORDS)
    planned = doc["episodes_planned"]
    episodes = [entry["ep"] for entry in arc]
    if arc and episodes != list(range(1, planned + 1)):
        errors.append(f"$.arc: episodes {episodes} must be exactly 1..{planned} in order")
    if doc["approved_at"] is not None and not arc:
        errors.append("$.approved_at: an empty arc cannot be approved")
    return errors


# ------------------------------------------------------ places_proposal_v1 (plan 1.2)

# P0's editable list: the places and props the places step will write.
PLACES_PROPOSAL_SCHEMA_NAME = "places_proposal_v1"
PROPOSAL_MAX_ITEMS = 6

PLACES_PROPOSAL_SCHEMA = _document({
    "$schema": {"type": "string", "const": PLACES_PROPOSAL_SCHEMA_NAME},
    "places": {
        "type": "array",
        "items": _document({"name": _text(60), "one_line": _text(200)}),
        "maxItems": PROPOSAL_MAX_ITEMS,
    },
    "props": {
        "type": "array",
        "items": _document({
            "name": _text(60),
            "one_line": _text(200),
            "owner": {"type": ["string", "null"], "pattern": CHAR_ID_PATTERN},
        }),
        "maxItems": PROPOSAL_MAX_ITEMS,
    },
    "updated_at": _NON_EMPTY_STRING,
})


def places_proposal_errors(doc) -> list:
    """``validate()`` against ``PLACES_PROPOSAL_SCHEMA``, plus no blank name."""
    errors = validate(doc, PLACES_PROPOSAL_SCHEMA)
    if errors:
        return errors

    errors = []
    for key in ("places", "props"):
        for i, item in enumerate(doc[key]):
            _check_text(errors, f"$.{key}[{i}].name", item["name"])
            _check_text(errors, f"$.{key}[{i}].one_line", item["one_line"])
    return errors
