"""A stdlib-only JSON-Schema subset validator, plus the closed lists and the
document schemas of AI Story phase 1 (spec 5, 6.3, 7, 11, 2.1, 2.2).

DEC-012: this module is imported by a pytest suite that must run in CI with
pytest alone, so no third-party schema library (``jsonschema``, ``pydantic``)
is used. The subset below covers exactly what the style-template and concept
schemas need. Unknown keywords are ignored on purpose: the same dicts are
meant to be usable later as a JSON Schema handed to an LLM, where extra
keywords (``description``, ``title``, ...) are harmless.
"""

from __future__ import annotations

import re

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

_APPROVALS_SCHEMA = {
    "type": "object",
    "properties": {"concept": _APPROVAL, "bible": _APPROVAL, "style": _APPROVAL},
    "required": ["concept", "bible", "style"],
    "additionalProperties": False,
}

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
        # Filled by the cast / places steps (phase 2); empty in phase 1.
        "cast_ids": _string_array(),
        "place_ids": _string_array(),
        "prop_ids": _string_array(),
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
