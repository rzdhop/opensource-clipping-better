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

import copy
import math
import re
import unicodedata

from . import defaults, platforms

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
# The short-video platforms a story targets and the metadata pack writes for.
PLATFORMS = ("tiktok", "shorts", "reels")

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
        # A v2 story's style lock only (phase 7 stage 6c, the human's
        # CLARIFY answer 11): the minimum time a word_pop card stays on
        # screen, in ms. Optional -- absent on every shipped template and on
        # a legacy lock, so render/subtitles.py's floor applies only when it
        # is present (RC-M2: golden.py's STYLE_LOCK has none).
        "word_min_card_ms": {"type": "integer", "minimum": 1},
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
        # The episode template the style suggests for a new story (the
        # new-story form pre-fills it when it fits the story's pipeline;
        # plan 20 stage 1 lifted the const to the shipped ids). A suggestion,
        # never read by the pipeline: the story keeps its own.
        "episode_template_id": {"type": "string", "enum": list(defaults.EPISODE_TEMPLATE_IDS)},
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
                # DEC-252: the v2 clip prompt's own suffix (optional; v1 reads the key above).
                "tier2_prompt_suffix_v2": _NON_EMPTY_STRING,
            },
            "required": ["tier1", "tier2_prompt_suffix"],
            "additionalProperties": False,
        },
        "typography": _TYPOGRAPHY_SCHEMA,
        "episode_defaults": _EPISODE_DEFAULTS_SCHEMA,
        "audio": _AUDIO_SCHEMA,
        "notes": {"type": "string"},
        # Optional (plan 23 stage D4): the body rules of ``generation_profile.body_rule`` --
        # ``body_rules.all_matter`` is a sentence with a ``{material}`` slot that replaces
        # ``character_design_rules`` in the lock; ``default_material`` fills the slot;
        # ``default_body_rule`` is what a story with no ``body_rule`` gets (absent: human_body).
        # None of the three reaches a lock (``stylelock.build_style_lock`` drops them).
        "body_rules": {
            "type": "object",
            "properties": {"all_matter": _NON_EMPTY_STRING},
            "required": ["all_matter"],
            "additionalProperties": False,
        },
        "default_material": _NON_EMPTY_STRING,
        "default_body_rule": {"type": "string", "enum": list(defaults.BODY_RULES)},
        # Optional (plan 23 stage D2): the universes (templates/universes.json ids) a story of this
        # style may take, and the one a story with no ``generation_profile.universe`` gets. Like the
        # body-rule hooks, neither reaches a lock (``stylelock.build_style_lock`` drops them).
        "universes": {"type": "array", "items": {"type": "string", "enum": list(defaults.UNIVERSES)},
                      "minItems": 1},
        "default_universe": {"type": "string", "enum": list(defaults.UNIVERSES)},
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
    errors = _style_prose_extra_errors(tpl)
    # Plan 23 stage D2: the default universe is one the style accepts.
    default = tpl.get("default_universe")
    if default is not None and default not in tpl.get("universes", ()):
        errors.append(f"$.default_universe: {default!r} is not in this style's universes")
    return errors


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
                # DEC-252: the v2 clip prompt's own suffix (optional; v1 reads the key above).
                "tier2_prompt_suffix_v2": _NON_EMPTY_STRING,
            },
            "required": ["tier1", "tier2_prompt_suffix"],
            "additionalProperties": False,
        },
        "typography": _TYPOGRAPHY_SCHEMA,
        "episode_defaults": _EPISODE_DEFAULTS_SCHEMA,
        "audio": _AUDIO_SCHEMA,
        # Dotted override path -> value, as actually applied (stylelock.OVERRIDABLE).
        "overrides": {"type": "object"},
        # Optional (plan 23 stage D2): what the story's cast is made of, copied from
        # templates/universes.json when the style is approved (``stylelock.lock_style``).
        "universe": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "pattern": _ID_PATTERN},
                "label": bilingual(),
                "species": {"type": "array", "items": _NON_EMPTY_STRING, "minItems": 1},
                "subject_phrase": _NON_EMPTY_STRING,
            },
            "required": ["id", "label", "species", "subject_phrase"],
            "additionalProperties": False,
        },
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


# ------------------------------------------------------- universes_v1 (plan 23 stage D2)
#
# templates/universes.json: what a story's cast can be made of. A universe is
# the matter of the cast (a fruit, a drink can, a gadget...), not a rendering:
# the look stays the style's. ``species`` are generic names -- never a brand
# (:data:`BRAND_DENYLIST`); ``material_rule`` fills the ``{material}`` slot of a
# style's ``body_rules.all_matter``; ``subject_phrase`` says what the head is in
# a prompt; ``audience_note`` is shown under the wizard's select.

UNIVERSE_SCHEMA_NAME = "universes_v1"

_UNIVERSE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "pattern": _ID_PATTERN},
        "label": bilingual(),
        "species": {"type": "array", "items": _NON_EMPTY_STRING, "minItems": 1},
        "material_rule": _NON_EMPTY_STRING,
        "subject_phrase": _NON_EMPTY_STRING,
        "audience_note": bilingual(),
    },
    "required": ["id", "label", "species", "material_rule", "subject_phrase"],
    "additionalProperties": False,
}

UNIVERSES_SCHEMA = {
    "type": "object",
    "properties": {
        "$schema": {"type": "string", "const": UNIVERSE_SCHEMA_NAME},
        "universes": {"type": "array", "items": _UNIVERSE_SCHEMA, "minItems": 1},
    },
    "required": ["$schema", "universes"],
    "additionalProperties": False,
}

# Plan 23 stage D2: the brand names no generated text may carry once a story has a
# universe (every one of them is a real product a universe's generic species stands
# in for); checked in the C1v2, K1, D2, P1, R1, R1v2 and D3 validators
# (:func:`brand_errors`), a told-why retry (DEC-259).
BRAND_DENYLIST = (
    "coca", "pepsi", "fanta", "sprite", "dr pepper", "mountain dew", "red bull", "monster", "gatorade",
    "starbucks", "iphone", "samsung", "airpods", "playstation", "nintendo", "xbox", "oreo", "nutella",
    "haribo", "m&m", "kinder", "lego",
)
# Ordinary English words that are also a brand: flagged only beside a word that
# makes the brand reading (a monster in the story is not a drink).
_BRAND_CONTEXT = {
    "monster": ("energy", "drink", "can", "ultra"),
    "sprite": ("soda", "can", "bottle", "drink", "zero", "cola"),
}


def _fold_brand(text) -> str:
    folded = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _brand_pattern(brand) -> re.Pattern:
    body = r"[\s\-]+".join(re.escape(part) for part in brand.split(" "))
    after = ""
    if brand in _BRAND_CONTEXT:
        after = r"[\s\-]+(?:" + "|".join(_BRAND_CONTEXT[brand]) + ")"
    return re.compile(r"(?<![a-z0-9])" + body + (after if after else "") + r"(?![a-z0-9])")


_BRAND_PATTERNS = tuple((brand, _brand_pattern(brand)) for brand in BRAND_DENYLIST)


def _strings_of(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings_of(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings_of(item)


def brand_hits(value) -> list:
    """The :data:`BRAND_DENYLIST` names found (whole words, accent- and
    case-folded) in every string of *value* (a string, or a reply's nested
    dicts and lists), in the list's order, each once."""
    text = "\n".join(_fold_brand(item) for item in _strings_of(value))
    return [brand for brand, pattern in _BRAND_PATTERNS if pattern.search(text)]


def brand_errors(value, path="$") -> list:
    """One told-why sentence per brand *value* names: the reply is asked again
    with the brand replaced by what it is (DEC-259)."""
    return [f"{path}: names the brand {brand!r}; write the generic thing instead (no brand names, "
            "no trademarked products)" for brand in brand_hits(value)]


def universes_errors(doc) -> list:
    """``validate()`` against ``UNIVERSES_SCHEMA``, plus: ids unique, a
    universe's species unique and brand-free."""
    errors = validate(doc, UNIVERSES_SCHEMA)
    if errors:
        return errors
    seen = set()
    for i, universe in enumerate(doc["universes"]):
        path = f"$.universes[{i}]"
        if universe["id"] in seen:
            errors.append(f"{path}.id: {universe['id']!r} is used twice")
        seen.add(universe["id"])
        species = universe["species"]
        if len(set(map(_fold_brand, species))) != len(species):
            errors.append(f"{path}.species: a species is listed twice")
        for brand in brand_hits(species):
            errors.append(f"{path}.species: names the brand {brand!r}; species are generic")
        if brand_hits(universe["material_rule"]) or brand_hits(universe["subject_phrase"]):
            errors.append(f"{path}: the material rule and the subject phrase name no brand")
    return errors


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
        "platforms": {"type": "array", "items": {"type": "string", "enum": list(PLATFORMS)}},
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
        # Optional (phase 7, DEC-221): absent on every story created before it.
        "pipeline": {"type": "string", "enum": list(defaults.PIPELINES)},
        # Optional (phase 7 stage 4, DEC-227): the clips' size; absent, the
        # budget profile's, else 720p (media_policy.video_resolution).
        "video_resolution": {"type": "string", "enum": list(defaults.VIDEO_RESOLUTIONS)},
        # Optional (DEC-258): the clips' lipsync for this story; absent, the
        # budget profile's (media_policy.lipsync).
        "lipsync": {"type": "string", "enum": list(defaults.LIPSYNC_MODES)},
        # Optional (plan 22): a native-speech story's speaking-clip model; absent,
        # the budget profile's (media_policy.speech_link).
        "speech_model": {"type": "string", "enum": list(defaults.SPEECH_MODELS)},
        # Optional (plan 22 stage 5): "manual" makes the sheets, plates, props and
        # keyframes the human's own uploads (media_policy.images_manual); absent,
        # the budget profile's image links.
        "images": {"type": "string", "enum": list(defaults.IMAGE_MODES)},
        # Optional (plan 21 stage 1): absent is Studio; "agent" lets the
        # story-fast-track job approve by rule (defaults.STORY_MODES).
        "mode": {"type": "string", "enum": list(defaults.STORY_MODES)},
        # Optional (plan 22 stage 2, DEC-274): absent or "v2" keeps every
        # prompt as it was; "v3" gates the brief-faithful concept and bible
        # prompts, only with a non-empty seed_text too (defaults.WRITING_VERSIONS).
        "writing": {"type": "string", "enum": list(defaults.WRITING_VERSIONS)},
        # Optional (plan 23 stage D4): absent is three sheets a character
        # (portrait, turnaround, expressions); the two-view modes draw one
        # front+back sheet (defaults.SHEET_MODES).
        "sheet_mode": {"type": "string", "enum": list(defaults.SHEET_MODES)},
        # Optional (plan 23 stage D4): absent is the style's default_body_rule;
        # "all_matter" draws the whole body in the character's own matter
        # (defaults.BODY_RULES).
        "body_rule": {"type": "string", "enum": list(defaults.BODY_RULES)},
        # Optional (plan 23 stage D2): what the cast is made of, an id of
        # templates/universes.json; absent is the style's default_universe, else none.
        "universe": {"type": "string", "enum": list(defaults.UNIVERSES)},
        # Optional (plan 23 stage A9): absent tries the budget profile's order (fal first);
        # "gemini_first" tries the gemini/* image links first (defaults.IMAGE_PREFERENCES).
        "image_preference": {"type": "string", "enum": list(defaults.IMAGE_PREFERENCES)},
        # Optional (plan 23 stage D6): absent is "studio", today's clip prompts; "action"
        # writes one continuous action with a colour/species anchor per character
        # (defaults.PROMPT_STYLES).
        "prompt_style": {"type": "string", "enum": list(defaults.PROMPT_STYLES)},
        # Optional (plan 23 stage D5): "on" lets a v2 story's characters carry
        # appearance variants without a sheet_mode (media_policy.variants_enabled).
        "variants": {"type": "string", "enum": list(defaults.VARIANTS_MODES)},
        # Optional (plan 23 stage B7): the output frame, "16:9" or "1:1", set at creation only;
        # absent is 9:16 (defaults.ASPECTS, media_policy.aspect).
        "aspect": {"type": "string", "enum": list(defaults.ASPECTS)},
        # Optional (plan 23 stage B8): "on" fills the eligible establishing shots with stock clips
        # at the next assets run, never replacing a clip (defaults.STOCK_CUTAWAYS_MODES).
        "stock_cutaways": {"type": "string", "enum": list(defaults.STOCK_CUTAWAYS_MODES)},
        # Optional (plan 28 stage B1, DEC-305): "none" -- no generated voice, no narrator (a
        # native-speech story only); absent is "tts", every story made before it (defaults.VOICES_MODES).
        "voices": {"type": "string", "enum": list(defaults.VOICES_MODES)},
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

# Plan 21 stage 1 (agent mode): who recorded an approval of a story
# document when it was not the human -- the agent run (``story-fast-track``)
# -- written beside the approval it marks: ``approved_by`` on story.json (a
# map from the approval's key), on a character, a place, a prop, the season
# arc and the knowledge base. Absent on the human's approvals. It speaks for
# the approval while that approval stands: every ``workflow.approve_*`` sets
# it (the agent) or removes it (the human), and nothing else approves.
AGENT_APPROVED = "agent"
_AGENT_APPROVED_SCHEMA = {"type": "string", "enum": [AGENT_APPROVED]}
STORY_AGENT_APPROVALS = ("concept", "bible", "style", "season")
_STORY_APPROVED_BY_SCHEMA = {
    "type": "object",
    "properties": {key: _AGENT_APPROVED_SCHEMA for key in STORY_AGENT_APPROVALS},
    "additionalProperties": False,
}

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


# Optional (plan 23 stage B5): the story's own subtitle look, a render-only
# setting edited at any time -- also after the style lock froze, which is why
# it lives here and not in the lock. Every field is optional; absent, the
# style lock's typography and the render's own numbers apply, so a story
# without the key renders exactly as before. The font is one of the families
# ``assets/fonts/fonts_index.json`` ships (tests/test_story_subtitle_style.py
# keeps the two in step); the contrast rule between the text and its outline
# or box is ``subtitle_style.validate``'s, since it reads two fields at once.
SUBTITLE_FONT_FAMILIES = ("Montserrat", "Bangers", "Luckiest Guy", "Bebas Neue", "Chewy", "Patrick Hand")
SUBTITLE_SIZE_PCT = (60, 160)
# word_pop: the word's centre; two_line: the block's bottom edge -- both a
# percentage of the frame's height measured from the top.
SUBTITLE_POSITION_PCT = (15, 95)
SUBTITLE_OUTLINE_PX = (0, 8)
SUBTITLE_BOX_OPACITY_PCT = (0, 100)

SUBTITLE_STYLE_SCHEMA = {
    "type": "object",
    "properties": {
        "font_family": {"type": "string", "enum": list(SUBTITLE_FONT_FAMILIES)},
        "size_pct": {"type": "integer", "minimum": SUBTITLE_SIZE_PCT[0], "maximum": SUBTITLE_SIZE_PCT[1]},
        "position_pct": {"type": "number", "minimum": SUBTITLE_POSITION_PCT[0],
                         "maximum": SUBTITLE_POSITION_PCT[1]},
        "text_colour": {"type": "string", "pattern": HEX_COLOUR},
        "highlight_colour": {"type": "string", "pattern": HEX_COLOUR},
        "outline_px": {"type": "integer", "minimum": SUBTITLE_OUTLINE_PX[0], "maximum": SUBTITLE_OUTLINE_PX[1]},
        "outline_colour": {"type": "string", "pattern": HEX_COLOUR},
        "box": {
            "type": ["object", "null"],
            "properties": {
                "colour": {"type": "string", "pattern": HEX_COLOUR},
                "opacity_pct": {"type": "integer", "minimum": SUBTITLE_BOX_OPACITY_PCT[0],
                                "maximum": SUBTITLE_BOX_OPACITY_PCT[1]},
            },
            "required": ["colour", "opacity_pct"],
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


# Optional (plan 28 stage F5, DEC-305 section 5): the one link the story's character sheets, place plates and
# props are made on (``steps/sticky_link.py``, ``refimages._make``): the chain link's label as the first image made
# answered it, since when, and the link it replaced when the user switched to it. Absent on a story that has made
# none yet and on every story written before it; a story without the key is made exactly as before.
STORY_LINK_PATTERN = r"^[a-z][a-z0-9_-]*/[^\s,*]+$"
_STORY_LINK_LABEL = {"type": "string", "maxLength": 160, "pattern": STORY_LINK_PATTERN}
STORY_LINKS_SCHEMA = {
    "type": "object",
    "properties": {
        "image": {
            "type": "object",
            "properties": {"link": _STORY_LINK_LABEL, "since": {"type": "string", "minLength": 1},
                           "switched_from": _STORY_LINK_LABEL},
            "required": ["link", "since"],
            "additionalProperties": False,
        },
    },
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
        "episode_template_id": {"type": "string", "enum": list(defaults.EPISODE_TEMPLATE_IDS)},
        "generation_profile": _GENERATION_PROFILE_SCHEMA,
        "narrator": _NARRATOR_SCHEMA,
        "approvals": _APPROVALS_SCHEMA,
        "status": {"type": "string", "enum": list(defaults.STATUSES)},
        "created_at": _NON_EMPTY_STRING,
        "updated_at": _NON_EMPTY_STRING,
        # Optional (plan 21 stage 1): the approvals the agent run recorded.
        "approved_by": _STORY_APPROVED_BY_SCHEMA,
        # Optional (plan 23 stage B5): the subtitle look; absent = today's.
        "subtitle_style": SUBTITLE_STYLE_SCHEMA,
        # Optional (plan 28 stage F5): the story's one image link for sheets, plates and props.
        "links": STORY_LINKS_SCHEMA,
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


def c1_schema(style_ids, *, species=False) -> dict:
    """The C1 output schema (spec 4.2, row C1): ``{"concepts": [...]}`` with
    ``C1_CONCEPTS_PER_CALL`` concept(s) -- the envelope a card is made from.

    ``style_fit`` is constrained to *style_ids* (the shipped style templates),
    passed in by the caller so this module needs no import of ``templates``.

    Plan 23 stage D2: with *species* (a story that has a universe) each cast
    member also names its ``species``; without it the schema is what it always was.
    """
    member = {
        "name": {"type": "string", "description": "the character's name"},
        "role": {"type": "string", "enum": list(_CAST_SKETCH_ROLES)},
        "one_line": {"type": "string", "description": "one sentence describing this character"},
    }
    if species:
        member["species"] = {"type": "string", "description": "what this character is, from the species pool"}
    cast_member = _llm_obj(member)
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
            "items": {"type": "string", "enum": list(PLATFORMS)},
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


def _check_chars(errors, path, value, max_chars) -> None:
    """Same as ``_check_text``, capped in characters rather than words --
    for fields whose stored counterpart is itself character-capped
    (``_text``), e.g. N1's proposed character fields (spec 4.2, row N1)."""
    if not (isinstance(value, str) and value.strip()):
        errors.append(f"{path}: must be a non-empty string")
        return
    if len(value) > max_chars:
        errors.append(f"{path}: {len(value)} characters, expected at most {max_chars}")


def c1_errors(doc, style_ids, *, species=False) -> list:
    """Post-validation for a C1 response, beyond what ``c1_schema`` can express."""
    schema = c1_schema(style_ids, species=species)
    if species:
        # The model is asked for a species (strict mode wants every key); a reply without one is
        # still a card, its species is optional (``_GENERATED_CAST_MEMBER_SCHEMA``).
        member = schema["properties"]["concepts"]["items"]["properties"]["cast_sketch"]["items"]
        member["required"] = [key for key in member["required"] if key != "species"]
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
            if species and "species" in member:
                _check_text(errors, f"{member_path}.species", member["species"], max_words=4)

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
        # Optional (plan 23 stage D2): what this character is, in a story with a universe.
        "species": _NON_EMPTY_STRING,
    },
    "required": ["name", "role", "one_line"],
    "additionalProperties": False,
}

# Plan 23 stage D2: a card of a story with a universe records which one and the
# species its lead was assigned (``universes.assign_species``); optional, absent on every other card.
_CARD_UNIVERSE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "pattern": _ID_PATTERN},
        "lead_species": _NON_EMPTY_STRING,
    },
    "required": ["id", "lead_species"],
    "additionalProperties": False,
}

# Plan 22 stage 2 (DEC-274): the brief judge's verdict on a v3 card, written
# once C1J ran (optional: a card from before this stage, or one the judge
# skipped -- the premium chain had no usable link -- carries none, RC-M3).
_BRIEF_FIT_SCHEMA = {
    "type": "object",
    "properties": {
        "kept": {"type": "boolean"},
        "missing": _string_array(max_items=5),
        "checked_by": _NON_EMPTY_STRING,
        "checked_at": _NON_EMPTY_STRING,
    },
    "required": ["kept", "missing", "checked_by", "checked_at"],
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
        "brief_fit": _BRIEF_FIT_SCHEMA,
        "universe": _CARD_UNIVERSE_SCHEMA,
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

def _document(properties, optional=None) -> dict:
    """A closed object: every property required, no other key allowed.

    *optional* properties are allowed as well, and checked when present, but
    never required: the keys a later phase adds to an object an earlier phase
    already wrote without them, so its documents on disk still validate."""
    return {
        "type": "object",
        "properties": {**properties, **(optional or {})},
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

# Plan 23 stage D5: a character's appearance variant ("ghost version") is named
# by a slug fixed when it is created (never renamed); its sheets sit beside the
# base ones as ``<slot>_<variant_id>.<ext>`` (``portrait_ghost_version.png``).
VARIANT_ID_PATTERN = r"^[a-z][a-z0-9_]{0,23}$"
_VARIANT_STEM = r"(portrait|turnaround|expressions)_[a-z][a-z0-9_]{0,23}"
# Any reference image, whatever its entity (the name field of an image ref)...
REF_IMAGE_NAME_PATTERN = (
    r"^(portrait|turnaround|expressions|variant_[a-z][a-z0-9_]{0,19}|image|extra_[0-9]{2}|" + _VARIANT_STEM + r")"
    r"\.(png|jpg|jpeg|webp)$"
)
# ... and the ones each kind may keep in its refs/ folder.
CHARACTER_REF_NAME_PATTERN = (r"^(portrait|turnaround|expressions|extra_[0-9]{2}|" + _VARIANT_STEM
                              + r")\.(png|jpg|jpeg|webp)$")
PLACE_REF_NAME_PATTERN = r"^variant_[a-z][a-z0-9_]{0,19}\.(png|jpg|jpeg|webp)$"
PROP_REF_NAME_PATTERN = r"^image\.(png|jpg|jpeg|webp)$"
EXTRA_REF_NAME_PATTERN = r"^extra_[0-9]{2}\.(png|jpg|jpeg|webp)$"
# A user's design reference, re-encoded and named by a uuid (refs/uploads/).
UPLOAD_NAME_PATTERN = r"^[0-9a-f]{32}\.png$"
# A character's voice sample, at the root of its folder.
VOICE_SAMPLE_NAME_PATTERN = r"^voice_sample\.(mp3|wav)$"
# A character's voice reference (plan 23 stage B4): the re-encoded recording a
# local engine clones, at the root of its folder.
VOICE_REFERENCE_NAME_PATTERN = r"^voice_reference\.wav$"

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

# K1's voice brief, kept so a voice can be proposed (or re-proposed) at any
# time, including before one is pinned: the pinned ``voice`` block copies
# ``direction`` and ``sample_line`` from here.
_VOICE_HINTS_SCHEMA = _or_null(_document({
    "gender": {"type": "string", "enum": ["female", "male", "neutral"]},
    "age": {"type": "string", "enum": ["child", "young", "adult", "elder"]},
    "style_tags": {"type": "array", "items": {"type": "string", "maxLength": 20}, "maxItems": 3},
    "direction": {"type": "string", "maxLength": 200},
    "sample_line": _text(120),
}))

_UPLOAD_SCHEMA = _document({
    "name": {"type": "string", "pattern": UPLOAD_NAME_PATTERN},
    # What the vision chain saw in it (U1), folded into the descriptor.
    "description": {"type": ["string", "null"], "maxLength": 400},
    "uploaded_at": _NON_EMPTY_STRING,
})

# A character's voice reference (plan 23 stage B4; DEC-281): ``consent`` is
# always true -- an upload without the speaker's permission is refused, so a
# document that says otherwise is not one.
VOICE_REFERENCE_SCHEMA = _document({
    "name": {"type": "string", "pattern": VOICE_REFERENCE_NAME_PATTERN},
    "sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
    "duration_s": {"type": "number", "minimum": 0},
    "uploaded_at": _NON_EMPTY_STRING,
    "consent": {"type": "boolean", "const": True},
})

# ------------------------------------------------- phase 7: the structured look (A10)
#
# Optional blocks a v2 story's entities gain (``_document(optional=)``):
# checked when present, never required, so every document a legacy story
# wrote still validates (the plan's schema rule). ``look`` is what the image
# prompts are rendered from (``shots.render_look/render_place/render_prop``);
# a character's ``dossier`` is its story knowledge (written by D1, a later
# stage). Word caps are checked by the ``*_look_errors`` helpers below, the
# way every other word cap here is (``_check_text``).

LOOK_BUILD_MAX_WORDS = 15
LOOK_SILHOUETTE_MAX_WORDS = 12
LOOK_FACE_MAX_WORDS = 15
LOOK_HAIR_MAX_WORDS = 12
LOOK_SKIN_MAX_WORDS = 12
# The story's own scale: a mouse may be 8 cm, a giant 400.
LOOK_HEIGHT_CM = (5, 500)
LOOK_PALETTE_RANGE = (1, 4)
LOOK_COLOUR_MAX_WORDS = 3
LOOK_WARDROBE_SETS_RANGE = (1, 3)
LOOK_WARDROBE_CONTEXT_MAX_WORDS = 8
LOOK_WARDROBE_ITEMS_MAX_WORDS = 20
LOOK_SEASON_CHANGE_MAX_WORDS = 20
WARDROBE_SET_ID_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
# Optional (A3): apparent age and gender presentation, asked for by D2 and
# rendered first by shots.render_look when present -- a character whose
# descriptor and signature items leave its presentation unsaid (e.g. a fruit
# head) was otherwise drawn as a default assumption.
LOOK_PRESENTATION_MAX_WORDS = 8
# Optional (phase 7 follow-up, stage F2): posture and how the character
# carries themselves, asked for by D2 and said by every keyframe
# (shots._bearing) and sheet (shots.visual_cues) -- a few words a model
# keeps from shot to shot.
LOOK_BEARING_MAX_WORDS = 10
# Optional (plan 26 stage 7a): the fruit or vegetable the character's head is
# ("pear", "dragon fruit") -- in a fruit world every head is one. Said by
# every reader of the look (shots.render_look / named_look / visual_cues,
# prompt_templates._species, steps/judge._character_look); a look without it
# reads exactly as before.
LOOK_SPECIES_MAX_WORDS = 4

_LOOK_TEXT_FIELDS = (
    ("build", LOOK_BUILD_MAX_WORDS),
    ("silhouette", LOOK_SILHOUETTE_MAX_WORDS),
    ("face", LOOK_FACE_MAX_WORDS),
    ("hair", LOOK_HAIR_MAX_WORDS),
    ("skin_material", LOOK_SKIN_MAX_WORDS),
)

_WARDROBE_SET_SCHEMA = _document({
    "id": {"type": "string", "pattern": WARDROBE_SET_ID_PATTERN},
    "context": _NON_EMPTY_STRING,
    "items": _NON_EMPTY_STRING,
})

CHARACTER_LOOK_SCHEMA = _document({
    "build": _NON_EMPTY_STRING,
    "silhouette": _NON_EMPTY_STRING,
    "face": _NON_EMPTY_STRING,
    "hair": _NON_EMPTY_STRING,
    "skin_material": _NON_EMPTY_STRING,
    "height_cm": {"type": "integer", "minimum": LOOK_HEIGHT_CM[0], "maximum": LOOK_HEIGHT_CM[1]},
    "palette": {"type": "array", "items": _NON_EMPTY_STRING,
                "minItems": LOOK_PALETTE_RANGE[0], "maxItems": LOOK_PALETTE_RANGE[1]},
    "wardrobe_sets": {"type": "array", "items": _WARDROBE_SET_SCHEMA,
                      "minItems": LOOK_WARDROBE_SETS_RANGE[0], "maxItems": LOOK_WARDROBE_SETS_RANGE[1]},
    # How the look changes with the seasons; null when it does not.
    "season_change": {"type": ["string", "null"], "minLength": 1},
}, optional={
    # Apparent age and gender presentation (A3); never required, so a look
    # written before this field existed still validates.
    "presentation": _NON_EMPTY_STRING,
    # Posture and bearing (stage F2); optional for the same reason.
    "bearing": _NON_EMPTY_STRING,
    # The head's fruit or vegetable (plan 26 stage 7a); optional for the same reason.
    "species": _NON_EMPTY_STRING,
})

DOSSIER_BACKSTORY_MAX_WORDS = 60
DOSSIER_GOAL_MAX_WORDS = 20
DOSSIER_SECRETS_MAX = 2
DOSSIER_SECRET_MAX_WORDS = 20
DOSSIER_HISTORY_MAX_WORDS = 30
DOSSIER_NOW_MAX_WORDS = 15
DOSSIER_VOICE_MAX_WORDS = 20
DOSSIER_CATCHPHRASES_MAX = 2
DOSSIER_CATCHPHRASE_MAX_WORDS = 10
DOSSIER_ARC_MAX_WORDS = 30

CHARACTER_DOSSIER_SCHEMA = _document({
    "backstory": _NON_EMPTY_STRING,
    "goal": _NON_EMPTY_STRING,
    "need": _NON_EMPTY_STRING,
    "fears": _NON_EMPTY_STRING,
    "secrets": {"type": "array", "items": _NON_EMPTY_STRING, "maxItems": DOSSIER_SECRETS_MAX},
    "relationships": {"type": "array", "items": _document({
        "with": {"type": "string", "pattern": CHAR_ID_PATTERN},
        "history": _NON_EMPTY_STRING,
        "now": _NON_EMPTY_STRING,
    })},
    "voice": _document({
        "patterns": _NON_EMPTY_STRING,
        "vocabulary": _NON_EMPTY_STRING,
        "catchphrases": {"type": "array", "items": _NON_EMPTY_STRING, "maxItems": DOSSIER_CATCHPHRASES_MAX},
    }),
    "arc": _NON_EMPTY_STRING,
})

LAYOUT_MAP_KEYS = ("left", "right", "back", "foreground", "centre")
LAYOUT_MAP_MAX_WORDS = 15
SCALE_NOTE_MAX_WORDS = 15
LIGHTING_MAX_WORDS = 15

PLACE_LOOK_SCHEMA = _document({
    # Any side may be an empty string: nothing there.
    "layout_map": _document({key: {"type": "string"} for key in LAYOUT_MAP_KEYS}),
    "scale_note": _NON_EMPTY_STRING,
    # Time variant name -> its light (keys checked by place_look_errors).
    "lighting": {"type": "object"},
    "props_here": _id_array(PROP_ID_PATTERN),
})

PROP_MATERIAL_MAX_WORDS = 8
PROP_COLOUR_MAX_WORDS = 6
PROP_SCALE_PHRASE_MAX_WORDS = 10
WHERE_WHEN_NOTE_MAX_WORDS = 12

PROP_LOOK_SCHEMA = _document({
    "scale_cm": {"type": "number", "minimum": 0},
    "material": _NON_EMPTY_STRING,
    "colour": _NON_EMPTY_STRING,
    "scale_phrase": _NON_EMPTY_STRING,
    "where_when": {"type": "array", "items": _document({
        "ep": {"type": "integer", "minimum": 1},
        "holder_char_id": {"type": ["string", "null"], "pattern": CHAR_ID_PATTERN},
        "place_id": {"type": ["string", "null"], "pattern": PLACE_ID_PATTERN},
        "note": _NON_EMPTY_STRING,
    })},
})


def character_look_errors(look, path="$.look") -> list:
    """``validate()`` against ``CHARACTER_LOOK_SCHEMA``, plus every field's
    word cap, each colour's, and the wardrobe set ids unique."""
    errors = validate(look, CHARACTER_LOOK_SCHEMA, path)
    if errors:
        return errors
    for key, cap in _LOOK_TEXT_FIELDS:
        _check_text(errors, f"{path}.{key}", look[key], max_words=cap)
    for i, colour in enumerate(look["palette"]):
        _check_text(errors, f"{path}.palette[{i}]", colour, max_words=LOOK_COLOUR_MAX_WORDS)
    ids = []
    for i, wardrobe in enumerate(look["wardrobe_sets"]):
        _check_text(errors, f"{path}.wardrobe_sets[{i}].context", wardrobe["context"],
                    max_words=LOOK_WARDROBE_CONTEXT_MAX_WORDS)
        _check_text(errors, f"{path}.wardrobe_sets[{i}].items", wardrobe["items"],
                    max_words=LOOK_WARDROBE_ITEMS_MAX_WORDS)
        if wardrobe["id"] in ids:
            errors.append(f"{path}.wardrobe_sets[{i}].id: {wardrobe['id']!r} is used twice")
        ids.append(wardrobe["id"])
    if look["season_change"] is not None:
        _check_text(errors, f"{path}.season_change", look["season_change"], max_words=LOOK_SEASON_CHANGE_MAX_WORDS)
    if "presentation" in look:
        _check_text(errors, f"{path}.presentation", look["presentation"], max_words=LOOK_PRESENTATION_MAX_WORDS)
    if "bearing" in look:
        _check_text(errors, f"{path}.bearing", look["bearing"], max_words=LOOK_BEARING_MAX_WORDS)
    if "species" in look:
        _check_text(errors, f"{path}.species", look["species"], max_words=LOOK_SPECIES_MAX_WORDS)
    return errors


def character_dossier_errors(dossier, char_id, path="$.dossier") -> list:
    """``validate()`` against ``CHARACTER_DOSSIER_SCHEMA``, plus the word caps
    and the relationships: another character each, at most once."""
    errors = validate(dossier, CHARACTER_DOSSIER_SCHEMA, path)
    if errors:
        return errors
    seen = set()
    for i, relationship in enumerate(dossier["relationships"]):
        other = relationship["with"]
        if other == char_id:
            errors.append(f"{path}.relationships[{i}].with: {other!r} is the character itself")
        elif other in seen:
            errors.append(f"{path}.relationships[{i}].with: {other!r} is listed twice")
        seen.add(other)
    _dossier_text_errors(errors, dossier, path)
    return errors


def _dossier_text_errors(errors, dossier, path) -> None:
    """The dossier's word caps (the stored block's and D1's reply's)."""
    _check_text(errors, f"{path}.backstory", dossier["backstory"], max_words=DOSSIER_BACKSTORY_MAX_WORDS)
    for key in ("goal", "need", "fears"):
        _check_text(errors, f"{path}.{key}", dossier[key], max_words=DOSSIER_GOAL_MAX_WORDS)
    for i, secret in enumerate(dossier["secrets"]):
        _check_text(errors, f"{path}.secrets[{i}]", secret, max_words=DOSSIER_SECRET_MAX_WORDS)
    for i, relationship in enumerate(dossier["relationships"]):
        _check_text(errors, f"{path}.relationships[{i}].history", relationship["history"],
                    max_words=DOSSIER_HISTORY_MAX_WORDS)
        _check_text(errors, f"{path}.relationships[{i}].now", relationship["now"], max_words=DOSSIER_NOW_MAX_WORDS)
    voice = dossier["voice"]
    _check_text(errors, f"{path}.voice.patterns", voice["patterns"], max_words=DOSSIER_VOICE_MAX_WORDS)
    _check_text(errors, f"{path}.voice.vocabulary", voice["vocabulary"], max_words=DOSSIER_VOICE_MAX_WORDS)
    for i, phrase in enumerate(voice["catchphrases"]):
        _check_text(errors, f"{path}.voice.catchphrases[{i}]", phrase, max_words=DOSSIER_CATCHPHRASE_MAX_WORDS)
    _check_text(errors, f"{path}.arc", dossier["arc"], max_words=DOSSIER_ARC_MAX_WORDS)


def place_look_errors(look, path="$.look") -> list:
    """``validate()`` against ``PLACE_LOOK_SCHEMA``, plus the word caps, the
    lighting keyed by time variant names, and no prop listed twice."""
    errors = validate(look, PLACE_LOOK_SCHEMA, path)
    if errors:
        return errors
    for key in LAYOUT_MAP_KEYS:
        value = look["layout_map"][key]
        if value.strip():
            _check_text(errors, f"{path}.layout_map.{key}", value, max_words=LAYOUT_MAP_MAX_WORDS)
    _check_text(errors, f"{path}.scale_note", look["scale_note"], max_words=SCALE_NOTE_MAX_WORDS)
    for key, value in look["lighting"].items():
        if not (isinstance(key, str) and _search(TIME_VARIANT_PATTERN, key)):
            errors.append(f"{path}.lighting: {key!r} is not a variant name ({TIME_VARIANT_PATTERN})")
            continue
        _check_text(errors, f"{path}.lighting.{key}", value, max_words=LIGHTING_MAX_WORDS)
    props = look["props_here"]
    for prop_id in sorted({prop_id for prop_id in props if props.count(prop_id) > 1}):
        errors.append(f"{path}.props_here: {prop_id!r} is listed twice")
    return errors


def prop_look_errors(look, path="$.look") -> list:
    """``validate()`` against ``PROP_LOOK_SCHEMA``, plus a real size (more
    than 0 cm) and the word caps."""
    errors = validate(look, PROP_LOOK_SCHEMA, path)
    if errors:
        return errors
    scale = look["scale_cm"]
    if not (math.isfinite(scale) and scale > 0):
        errors.append(f"{path}.scale_cm: {scale!r} is not a size (more than 0 cm)")
    _check_text(errors, f"{path}.material", look["material"], max_words=PROP_MATERIAL_MAX_WORDS)
    _check_text(errors, f"{path}.colour", look["colour"], max_words=PROP_COLOUR_MAX_WORDS)
    _check_text(errors, f"{path}.scale_phrase", look["scale_phrase"], max_words=PROP_SCALE_PHRASE_MAX_WORDS)
    for i, entry in enumerate(look["where_when"]):
        _check_text(errors, f"{path}.where_when[{i}].note", entry["note"], max_words=WHERE_WHEN_NOTE_MAX_WORDS)
    return errors


# Plan 23 stage D5: a character's named appearance variants, each an edit of
# its base sheet(s) (``refimages.variant_image``) picked per shot by the
# storyboard (``shots.variant_view``). ``variant_id`` is a slug fixed at
# creation and never renamed (what shots, scenes and the sheet files name);
# ``refs`` holds the slots of the story's sheet mode (``portrait`` always, the
# turnaround and the expressions sheet when the mode draws them), each null
# until made; ``source`` says who asked for it (the human, or an accepted N1v2
# twist); a variant is approved on its own, the character's approval never
# reopened by it.
VARIANTS_MAX = 3
VARIANT_LABEL_MAX_CHARS = 40
VARIANT_DELTA_MAX_WORDS = 60
VARIANT_SOURCES = ("human", "twist")
VARIANT_SLOTS = ("portrait", "turnaround", "expressions")
_VARIANT_SCHEMA = _document({
    "variant_id": {"type": "string", "pattern": VARIANT_ID_PATTERN},
    "label": _text(VARIANT_LABEL_MAX_CHARS),
    "delta_text": _NON_EMPTY_STRING,
    "refs": _document({"portrait": _IMAGE_REF_OR_NULL}, optional={
        "turnaround": _IMAGE_REF_OR_NULL, "expressions": _IMAGE_REF_OR_NULL}),
    "source": {"type": "string", "enum": list(VARIANT_SOURCES)},
    "created_at": _NON_EMPTY_STRING,
    "approved_at": _TIMESTAMP_OR_NULL,
})


def variant_errors(variant, path="$.variant") -> list:
    """``validate()`` against the variant schema, plus the label's and the
    delta's caps (:data:`VARIANT_LABEL_MAX_CHARS`, :data:`VARIANT_DELTA_MAX_WORDS`)
    and each sheet named ``<slot>_<variant_id>.<ext>`` and labelled as an edit
    of the base portrait (``references``/``prompt_only``)."""
    errors = validate(variant, _VARIANT_SCHEMA, path)
    if errors:
        return errors
    _check_text(errors, f"{path}.label", variant["label"])
    _check_text(errors, f"{path}.delta_text", variant["delta_text"], max_words=VARIANT_DELTA_MAX_WORDS)
    for slot, ref in variant["refs"].items():
        _slot_errors(errors, f"{path}.refs.{slot}", ref, stem=f"{slot}_{variant['variant_id']}", allowed=_DERIVED)
    return errors


# Plan 28 F3 (DEC-305 section 5): the sheet judge's verdict on each of an
# entity's images, by slot (a character's portrait/turnaround/expressions, a
# place's time variant, a prop's "image"): the J3 version, passed (null until
# judged), what it saw, when, and the sha256 of the very file judged;
# ``redraws``/``redraw_usd`` once the cast or places step redrew it, ``link``
# the vision link that judged it.
SHEET_CHECK_ISSUES_MAX = 3
SHEET_CHECK_REDRAWS_MAX = 2
_SHEET_CHECK_SCHEMA = _document({
    "version": {"type": "integer", "minimum": 1},
    "passed": {"type": ["boolean", "null"]},
    "issues": {"type": "array", "items": _NON_EMPTY_STRING, "maxItems": SHEET_CHECK_ISSUES_MAX},
    "judged_at": _TIMESTAMP_OR_NULL,
    "image_hash": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
}, optional={
    "redraws": {"type": "integer", "minimum": 0, "maximum": SHEET_CHECK_REDRAWS_MAX},
    "redraw_usd": {"type": "number", "minimum": 0},
    "link": _NON_EMPTY_STRING,
})
_SHEET_CHECK_SLOT = r"^[a-z][a-z0-9_]{0,19}$"


def sheet_checks_errors(checks, path="$.sheet_checks") -> list:
    """Each entry of an entity's ``sheet_checks`` (plan 28 F3): keyed by a
    slot name, :data:`_SHEET_CHECK_SCHEMA`'s shape."""
    errors = validate(checks, {"type": "object"}, path)
    if errors:
        return errors
    for key, entry in checks.items():
        if not (isinstance(key, str) and _search(_SHEET_CHECK_SLOT, key)):
            errors.append(f"{path}: {key!r} is not an image slot")
            continue
        errors.extend(validate(entry, _SHEET_CHECK_SCHEMA, f"{path}.{key}"))
    return errors


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
    "voice_hints": _VOICE_HINTS_SCHEMA,
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
}, optional={
    # Phase 7, a v2 story: D2's look (A10) and D1's dossier.
    "look": CHARACTER_LOOK_SCHEMA,
    "dossier": CHARACTER_DOSSIER_SCHEMA,
    # Plan 21 stage 1: approved by the agent run (see AGENT_APPROVED).
    "approved_by": _AGENT_APPROVED_SCHEMA,
    # Plan 23 stage D5: the character's appearance variants (_VARIANT_SCHEMA, at most VARIANTS_MAX).
    "variants": {"type": "array", "items": _VARIANT_SCHEMA, "maxItems": VARIANTS_MAX},
    # Plan 23 stage B4: the character's own voice recording, cloned locally.
    "voice_reference": VOICE_REFERENCE_SCHEMA,
    # Plan 28 F3: the sheet judge's verdict on each image (sheet_checks_errors).
    "sheet_checks": {"type": "object"},
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
    if "look" in doc:
        errors.extend(character_look_errors(doc["look"]))
    if "dossier" in doc:
        errors.extend(character_dossier_errors(doc["dossier"], doc["char_id"]))
    if "sheet_checks" in doc:
        errors.extend(sheet_checks_errors(doc["sheet_checks"]))
    seen = set()
    for i, variant in enumerate(doc.get("variants") or ()):
        errors.extend(variant_errors(variant, f"$.variants[{i}]"))
        if variant["variant_id"] in seen:
            errors.append(f"$.variants[{i}].variant_id: {variant['variant_id']!r} is used twice")
        seen.add(variant["variant_id"])
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
}, optional={
    # Phase 7, a v2 story: D3's look (A10).
    "look": PLACE_LOOK_SCHEMA,
    # Plan 21 stage 1: approved by the agent run (see AGENT_APPROVED).
    "approved_by": _AGENT_APPROVED_SCHEMA,
    # Plan 28 F3: the sheet judge's verdict on each plate (sheet_checks_errors).
    "sheet_checks": {"type": "object"},
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
    if "look" in doc:
        errors.extend(place_look_errors(doc["look"]))
    if "sheet_checks" in doc:
        errors.extend(sheet_checks_errors(doc["sheet_checks"]))
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
}, optional={
    # Phase 7, a v2 story: R1v2's look (A10).
    "look": PROP_LOOK_SCHEMA,
    # Plan 21 stage 1: approved by the agent run (see AGENT_APPROVED).
    "approved_by": _AGENT_APPROVED_SCHEMA,
    # Plan 28 F3: the sheet judge's verdict on its picture (sheet_checks_errors).
    "sheet_checks": {"type": "object"},
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
    if "look" in doc:
        errors.extend(prop_look_errors(doc["look"]))
    if "sheet_checks" in doc:
        errors.extend(sheet_checks_errors(doc["sheet_checks"]))
    return errors


# ----------------------------------------------------------- season_arc_v1 (spec 2.6)

SEASON_ARC_SCHEMA_NAME = "season_arc_v1"
ARC_FUNCTIONS = ("setup", "escalation", "complication", "midpoint_twist", "crisis", "climax_and_reset")
EPISODES_PLANNED_MIN = 3
EPISODES_PLANNED_MAX = 12
ARC_SUMMARY_MAX_WORDS = 60
HOOK_MAX_LENGTH = 120

_HOOKS = {"type": "array", "items": _text(HOOK_MAX_LENGTH)}

# Phase 5 (plan 11, stage 1). An arc entry a twist rewrote keeps what it said
# before, newest last. ``source`` says what rewrote it: an accepted N1 twist
# (``proposal``) is the only writer so far; the list is closed and can grow.
ARC_HISTORY_SOURCES = ("proposal",)

_ARC_HISTORY_ITEM_SCHEMA = _document({
    "summary": _NON_EMPTY_STRING,
    "open_hooks_out": _HOOKS,
    "replaced_at": _NON_EMPTY_STRING,
    "source": {"type": "string", "enum": list(ARC_HISTORY_SOURCES)},
})

_ARC_ENTRY_SCHEMA = _document({
    "ep": {"type": "integer", "minimum": 1},
    "function": {"type": "string", "enum": list(ARC_FUNCTIONS)},
    "summary": _NON_EMPTY_STRING,
    "open_hooks_in": _HOOKS,
    "open_hooks_out": _HOOKS,
    "characters": _id_array(CHAR_ID_PATTERN),
}, optional={
    "history": {"type": "array", "items": _ARC_HISTORY_ITEM_SCHEMA},
    # Plan 20 stage 2 (a v2 story's S1): the plot archetype whose beat this
    # episode plays, the season's primary or its secondary
    # (``templates/archetypes``); checked against the library in season_arc_errors.
    "archetype": {"type": "string", "pattern": _ID_PATTERN},
})

# series_memory.entries (phase 5): what the memory step (S3) wrote for each
# episode, keyed like ``recaps`` ("ep01".."ep99"). The spec-2.6 fields
# ``recaps``, ``open_hooks`` and ``relationship_state`` are folded from them
# (``series_memory.fold_memory``) and stored alongside, so their readers are
# unchanged; ``introduced`` is the cast path's and is not folded.
RECAP_MAX_WORDS = 40
HOOKS_OPENED_MAX = 3
MEMORY_KEY_PATTERN = r"^ep(0[1-9]|[1-9][0-9])$"
# "<char_a>|<char_b>": two character ids, a < b (``series_memory.pair_key``).
RELATIONSHIP_PAIR_PATTERN = r"^(char_[a-z0-9_]{1,40})\|(char_[a-z0-9_]{1,40})$"
# Plan 11 stage 2 (S3): a relationship delta's own text had no cap in stage
# 1 -- ``test_story_episode_prompt_budgets.py`` already sizes E1's budget on
# ~15-word relationship texts, so that is the number both S3's reply and a
# stored entry are held to.
RELATIONSHIP_DELTA_MAX_WORDS = 15
# How many pairs one S3 reply may report a delta for. Not a stage-1 field
# (a stored entry may carry as many as its episodes accumulate), but S3's
# own *reply* needs a stated bound the same way every other array field of
# this catalogue has one (hooks_opened <= 3, characters <= 5, ...): without
# one the "largest French reply" a cap is measured against grows with the
# cast size alone (28 pairs at 8 cast) rather than with what one episode
# could plausibly shift. Five is the same order of magnitude as
# HOOKS_OPENED_MAX and realistic for 60 seconds of story.
RELATIONSHIP_DELTAS_MAX = 5

# The continuity ledger (phase 7, A14/A13): one character's state at a point
# of the story -- where they are, what they wear (a wardrobe set of their
# look), what they hold, how they are hurt, how they stand with the others.
# The knowledge document's ``ledger_seed`` is the state before episode 1; a
# memory entry's optional ``ledger`` the state after its episode (written by
# the ledger step, L1). Keyed by character id; "none" is null (or an empty
# possessions list). The ids are checked against the story where the whole
# story is at hand (``knowledge_reference_errors``).
LEDGER_INJURIES_MAX_WORDS = 10
LEDGER_RELATIONSHIP_NOTES_MAX_WORDS = 20

LEDGER_STATE_SCHEMA = _document({
    "location": {"type": ["string", "null"], "pattern": PLACE_ID_PATTERN},
    "wardrobe_set": {"type": ["string", "null"], "pattern": WARDROBE_SET_ID_PATTERN},
    "possessions": _id_array(PROP_ID_PATTERN),
    "injuries": {"type": ["string", "null"], "minLength": 1},
    "relationship_notes": {"type": ["string", "null"], "minLength": 1},
})


def ledger_errors(ledger, path) -> list:
    """A ledger (``{char_id: state}``) on its own: an object keyed by
    character ids, each state ``LEDGER_STATE_SCHEMA`` with its word caps and
    no prop held twice."""
    if not isinstance(ledger, dict):
        return [f"{path}: expected an object"]
    errors = []
    for char_id, state in ledger.items():
        where = f"{path}.{char_id}"
        if not (isinstance(char_id, str) and _search(CHAR_ID_PATTERN, char_id)):
            errors.append(f"{path}: {char_id!r} is not a character id")
            continue
        found = validate(state, LEDGER_STATE_SCHEMA, where)
        if found:
            errors.extend(found)
            continue
        if state["injuries"] is not None:
            _check_text(errors, f"{where}.injuries", state["injuries"], max_words=LEDGER_INJURIES_MAX_WORDS)
        if state["relationship_notes"] is not None:
            _check_text(errors, f"{where}.relationship_notes", state["relationship_notes"],
                        max_words=LEDGER_RELATIONSHIP_NOTES_MAX_WORDS)
        if len(set(state["possessions"])) != len(state["possessions"]):
            errors.append(f"{where}.possessions: a prop is listed twice")
    return errors


_MEMORY_ENTRY_SCHEMA = _document({
    "recap": _NON_EMPTY_STRING,
    "hooks_opened": {"type": "array", "items": _text(HOOK_MAX_LENGTH), "maxItems": HOOKS_OPENED_MAX},
    # Each exactly the text of a hook open before this episode (the fold checks it).
    "hooks_closed": _HOOKS,
    # pair key -> what the pair now is, checked in memory_entry_errors.
    "relationship_deltas": {"type": "object"},
    # The script's ``rev`` the entry was written from; another rev is stale.
    "script_rev": {"type": "integer", "minimum": 1},
    "at": _NON_EMPTY_STRING,
    "approved_at": _TIMESTAMP_OR_NULL,
}, optional={
    # Phase 7 (a v2 story): the ledger after this episode, checked in memory_entry_errors.
    "ledger": {"type": "object"},
})

# audience_feedback items (spec 2.6, phase 5): the pasted text (and, from
# stage 4, the optional pasted stats F1 reads beside it), F1's digest and its
# three directions once F1 ran, and the direction the user chose (or none)
# when approving it. Both pasted texts are capped, never trimmed: over the
# cap the paste is refused (``workflow.store_feedback``).
FEEDBACK_TEXT_MAX_LENGTH = 6000
FEEDBACK_STATS_MAX_LENGTH = 6000
FEEDBACK_DIGEST_MAX_WORDS = 60
FEEDBACK_DIRECTIONS = 3

_AUDIENCE_FEEDBACK_SCHEMA = _document({
    "ep": {"type": "integer", "minimum": 1, "maximum": 99},
    "pasted_at": _NON_EMPTY_STRING,
    "text": _text(FEEDBACK_TEXT_MAX_LENGTH),
}, optional={
    "stats": _text(FEEDBACK_STATS_MAX_LENGTH),
    "digest": _NON_EMPTY_STRING,
    "directions": {"type": "array", "items": _NON_EMPTY_STRING,
                   "minItems": FEEDBACK_DIRECTIONS, "maxItems": FEEDBACK_DIRECTIONS},
    # An index into directions; the type keeps True and 1.0 out of the enum.
    "chosen_direction": {"type": ["integer", "null"], "enum": [*range(FEEDBACK_DIRECTIONS), None]},
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
    }, optional={
        # "epNN" -> _MEMORY_ENTRY_SCHEMA, checked in season_arc_errors.
        "entries": {"type": "object"},
    }),
    "audience_feedback": {"type": "array", "items": _AUDIENCE_FEEDBACK_SCHEMA},
    "approved_at": _TIMESTAMP_OR_NULL,
    "updated_at": _NON_EMPTY_STRING,
}, optional={
    # Plan 20 stage 2: the plot archetypes a v2 story's S1 chose for the
    # season's spine -- one primary, at most one secondary (null when none).
    "archetypes": _document({
        "primary": {"type": "string", "pattern": _ID_PATTERN},
        "secondary": {"type": ["string", "null"], "pattern": _ID_PATTERN},
    }),
    # Plan 21 stage 1: approved by the agent run (see AGENT_APPROVED).
    "approved_by": _AGENT_APPROVED_SCHEMA,
})


def memory_entry_errors(entry, path="$") -> list:
    """What can be checked of one ``series_memory.entries`` value on its own:
    ``_MEMORY_ENTRY_SCHEMA``, the recap (non-blank, at most
    ``RECAP_MAX_WORDS`` words), each hook non-blank and listed once, none
    both opened and closed, and each relationship key a pair of two
    different character ids in order (``RELATIONSHIP_PAIR_PATTERN``, a < b)
    valued with a non-blank text of at most ``RELATIONSHIP_DELTA_MAX_WORDS``
    words. What needs more than the entry -- the hooks open before it, the
    story's cast -- is ``series_memory.entry_errors``."""
    errors = validate(entry, _MEMORY_ENTRY_SCHEMA, path)
    if errors:
        return errors

    errors = []
    _check_text(errors, f"{path}.recap", entry["recap"], max_words=RECAP_MAX_WORDS)
    for field in ("hooks_opened", "hooks_closed"):
        hooks = entry[field]
        for i, hook in enumerate(hooks):
            _check_text(errors, f"{path}.{field}[{i}]", hook)
        for hook in sorted({hook for hook in hooks if hooks.count(hook) > 1}):
            errors.append(f"{path}.{field}: {hook!r} is listed twice")
    for hook in sorted(set(entry["hooks_opened"]) & set(entry["hooks_closed"])):
        errors.append(f"{path}: {hook!r} is both opened and closed")
    for key, text in entry["relationship_deltas"].items():
        match = _search(RELATIONSHIP_PAIR_PATTERN, key) if isinstance(key, str) else None
        if match is None or not match.group(1) < match.group(2):
            errors.append(f"{path}.relationship_deltas: {key!r} is not a pair key "
                          "('<char_a>|<char_b>', two different character ids in order)")
            continue
        _check_text(errors, f"{path}.relationship_deltas.{key}", text, max_words=RELATIONSHIP_DELTA_MAX_WORDS)
    if "ledger" in entry:
        errors.extend(ledger_errors(entry["ledger"], f"{path}.ledger"))
    return errors


def _audience_feedback_errors(errors, path, item) -> None:
    _check_text(errors, f"{path}.text", item["text"])
    if "stats" in item:
        _check_text(errors, f"{path}.stats", item["stats"])
    if "digest" in item:
        _check_text(errors, f"{path}.digest", item["digest"], max_words=FEEDBACK_DIGEST_MAX_WORDS)
    for i, direction in enumerate(item.get("directions") or []):
        _check_text(errors, f"{path}.directions[{i}]", direction)
    if "chosen_direction" in item and "directions" not in item:
        errors.append(f"{path}.chosen_direction: there are no directions to choose from")


def _series_memory_errors(errors, memory) -> None:
    """``entries``, when there are any: each keyed by an episode and valid on
    its own (``memory_entry_errors``), their fold without error, and the
    stored ``recaps``/``open_hooks``/``relationship_state`` equal to that
    fold (a season whose derived fields disagree with its entries is
    corrupt). No entries (absent or empty) leaves those fields as loose as
    before phase 5."""
    entries = memory.get("entries")
    if not entries:
        return
    found = []
    for key, entry in entries.items():
        if not (isinstance(key, str) and _search(MEMORY_KEY_PATTERN, key)):
            found.append(f"$.series_memory.entries: {key!r} is not an episode key (ep01..ep99)")
            continue
        found.extend(memory_entry_errors(entry, f"$.series_memory.entries.{key}"))
    if found:
        errors.extend(found)
        return

    from . import series_memory  # it builds on this module, so it is imported here, not at the top

    problems = series_memory.fold_errors(entries)
    if problems:
        errors.extend(f"$.series_memory.entries: {problem}" for problem in problems)
        return
    folded = series_memory.fold_memory(entries)
    for field in series_memory.DERIVED_FIELDS:
        if memory[field] != folded[field]:
            errors.append(f"$.series_memory.{field}: does not match the fold of series_memory.entries")


def season_arc_errors(doc) -> list:
    """``validate()`` against ``SEASON_ARC_SCHEMA``, plus: each summary's word
    cap (a history item's too); the arc is empty (before S1) or exactly
    episodes 1..episodes_planned in order; an empty arc is never approved;
    each feedback item's text and directions non-blank, its digest's word
    cap, a chosen direction only among directions; the series memory's
    entries and the fields folded from them (``_series_memory_errors``).

    Only what season.json decides on its own: the checks against the story's
    cast are ``series_memory.entry_errors``, which the memory step runs."""
    errors = validate(doc, SEASON_ARC_SCHEMA)
    if errors:
        return errors

    errors = []
    arc = doc["arc"]
    for i, entry in enumerate(arc):
        _check_text(errors, f"$.arc[{i}].summary", entry["summary"], max_words=ARC_SUMMARY_MAX_WORDS)
        for j, item in enumerate(entry.get("history") or []):
            _check_text(errors, f"$.arc[{i}].history[{j}].summary", item["summary"],
                        max_words=ARC_SUMMARY_MAX_WORDS)
    planned = doc["episodes_planned"]
    episodes = [entry["ep"] for entry in arc]
    if arc and episodes != list(range(1, planned + 1)):
        errors.append(f"$.arc: episodes {episodes} must be exactly 1..{planned} in order")
    if doc["approved_at"] is not None and not arc:
        errors.append("$.approved_at: an empty arc cannot be approved")
    for i, item in enumerate(doc["audience_feedback"]):
        _audience_feedback_errors(errors, f"$.audience_feedback[{i}]", item)
    _series_memory_errors(errors, doc["series_memory"])
    _season_archetype_errors(errors, doc)
    return errors


def _season_archetype_errors(errors, doc) -> None:
    """The season's plot archetypes (plan 20 stage 2), when it names any:
    every id one of the shipped library's (``templates/archetypes``), the
    secondary not the primary, and each entry's ``archetype`` one the season
    chose. An entry may name one only when the season chose its archetypes.
    A season naming none (every v1 story's, and v2 ones written before)
    reads no file at all."""
    chosen = doc.get("archetypes")
    named = [(i, entry["archetype"]) for i, entry in enumerate(doc["arc"]) if "archetype" in entry]
    if chosen is None and not named:
        return

    from . import templates  # it builds on this module, so it is imported here, not at the top

    library = set(templates.list_archetype_ids())
    allowed = set()
    if chosen is not None:
        for key in ("primary", "secondary"):
            value = chosen[key]
            if value is None:
                continue
            if value not in library:
                errors.append(f"$.archetypes.{key}: {value!r} is not a shipped plot archetype")
            allowed.add(value)
        if chosen["secondary"] is not None and chosen["secondary"] == chosen["primary"]:
            errors.append("$.archetypes.secondary: must differ from the primary")
    for i, archetype in named:
        if chosen is None:
            errors.append(f"$.arc[{i}].archetype: the season chose no archetypes")
        elif archetype not in allowed:
            errors.append(f"$.arc[{i}].archetype: {archetype!r} is neither the season's primary nor its secondary")


# ------------------------------------------------------------ archetype_v1 (plan 20 stage 2)
#
# The plot-archetype library (``templates/archetypes/*.json``): the
# telenovela engines a v2 story's season arc is built on. One beat per arc
# function, in ARC_FUNCTIONS' order, so each episode of the season plays one
# beat; three twists the writer may reach for; the payoff the season lands
# on; and the archetypes it pairs well with as a secondary line. Every text
# in French and English; each capped in words so the S1 pick-list and the
# S2/D5 beat lines stay inside their prompt budgets.
ARCHETYPE_SCHEMA_NAME = "archetype_v1"
ARCHETYPE_PREMISE_MAX_WORDS = 20
ARCHETYPE_BEAT_MAX_WORDS = 25
ARCHETYPE_TWIST_MAX_WORDS = 15
ARCHETYPE_PAYOFF_MAX_WORDS = 25
ARCHETYPE_TWISTS = 3

ARCHETYPE_SCHEMA = _document({
    "$schema": {"type": "string", "const": ARCHETYPE_SCHEMA_NAME},
    "id": {"type": "string", "pattern": _ID_PATTERN},
    "label": bilingual(),
    "premise": bilingual(),
    "beats": {
        "type": "array",
        "items": _document({
            "function": {"type": "string", "enum": list(ARC_FUNCTIONS)},
            "beat": bilingual(),
        }),
        "minItems": len(ARC_FUNCTIONS),
        "maxItems": len(ARC_FUNCTIONS),
    },
    "twists": {"type": "array", "items": bilingual(), "minItems": ARCHETYPE_TWISTS, "maxItems": ARCHETYPE_TWISTS},
    "payoff": bilingual(),
    "pairs_well_with": {"type": "array", "items": {"type": "string", "pattern": _ID_PATTERN}},
})


def archetype_errors(archetype) -> list:
    """``validate()`` against ``ARCHETYPE_SCHEMA``, plus: the beats name the
    arc functions exactly in ``ARC_FUNCTIONS``' order; every text non-blank
    in both languages and within its word cap; ``pairs_well_with`` neither
    names the archetype itself nor any id twice. Whether the pairs resolve
    is the library's check (``archetype_library_errors``)."""
    errors = validate(archetype, ARCHETYPE_SCHEMA)
    if errors:
        return errors

    errors = []
    functions = [beat["function"] for beat in archetype["beats"]]
    if functions != list(ARC_FUNCTIONS):
        errors.append(f"$.beats: functions {functions} must be exactly {list(ARC_FUNCTIONS)} in order")
    for language in LANGUAGES:
        _check_text(errors, f"$.label.{language}", archetype["label"][language])
        _check_text(errors, f"$.premise.{language}", archetype["premise"][language],
                    max_words=ARCHETYPE_PREMISE_MAX_WORDS)
        for i, beat in enumerate(archetype["beats"]):
            _check_text(errors, f"$.beats[{i}].beat.{language}", beat["beat"][language],
                        max_words=ARCHETYPE_BEAT_MAX_WORDS)
        for i, twist in enumerate(archetype["twists"]):
            _check_text(errors, f"$.twists[{i}].{language}", twist[language], max_words=ARCHETYPE_TWIST_MAX_WORDS)
        _check_text(errors, f"$.payoff.{language}", archetype["payoff"][language],
                    max_words=ARCHETYPE_PAYOFF_MAX_WORDS)
    pairs = archetype["pairs_well_with"]
    if archetype["id"] in pairs:
        errors.append("$.pairs_well_with: an archetype cannot pair with itself")
    for pair in sorted({pair for pair in pairs if pairs.count(pair) > 1}):
        errors.append(f"$.pairs_well_with: {pair!r} is listed twice")
    return errors


def archetype_library_errors(archetypes) -> list:
    """The library as a whole: ids unique, and every ``pairs_well_with`` id
    one of the library's, the pairing mutual (a secondary that pairs well
    with a primary is paired back)."""
    errors = []
    by_id = {}
    for archetype in archetypes:
        if archetype["id"] in by_id:
            errors.append(f"{archetype['id']}: listed twice")
        by_id[archetype["id"]] = archetype
    for archetype_id, archetype in by_id.items():
        for pair in archetype["pairs_well_with"]:
            if pair not in by_id:
                errors.append(f"{archetype_id}.pairs_well_with: {pair!r} is not an archetype of the library")
            elif archetype_id not in by_id[pair]["pairs_well_with"]:
                errors.append(f"{archetype_id}.pairs_well_with: {pair!r} does not pair back")
    return errors


# ------------------------------------------------------ story_knowledge_v1 (phase 7, A14)
#
# A v2 story's knowledge base, one story-level document (``knowledge.json``)
# written by the knowledge step before episode 1 and approved in the
# dashboard: the world's geography, period details and visual motifs; a
# timeline of beats per planned episode (what happens, where, who, with
# which objects, and what each character knows afterwards); the props the
# step registered; and every character's starting ledger. The sections are
# optional until written (the step is resumable, one call at a time); an
# approved document has all of them. Ids are checked against the story when
# the document is written (``knowledge_reference_errors``,
# ``StoryStore.write_knowledge``); stored stories have no such document.
KNOWLEDGE_SCHEMA_NAME = "story_knowledge_v1"
WORLD_GEOGRAPHY_MAX_WORDS = 60
WORLD_PERIOD_DETAILS_MAX_WORDS = 30
WORLD_VISUAL_MOTIFS_MAX = 4
WORLD_VISUAL_MOTIF_MAX_WORDS = 12
TIMELINE_BEATS_MAX = 8
BEAT_WHAT_MAX_WORDS = 25
BEAT_KNOWS_AFTER_MAX_WORDS = 15
# D6's registry (A14): the props the knowledge step creates, at most.
KNOWLEDGE_PROPS_MAX = 8
KNOWLEDGE_SECTIONS = ("world", "timeline", "props_registry", "ledger_seed")

# Stage 5b (DEC-228): the objects a beat names that are no prop of the story
# yet, by name, until the props registry (D6) registers them (their ids then
# join ``objects``) or leaves them out; D6 clears every one.
BEAT_NEW_OBJECTS_MAX = 2
BEAT_NEW_OBJECT_MAX_CHARS = 60

_BEAT_SCHEMA = _document({
    "what": _NON_EMPTY_STRING,
    "place_id": {"type": ["string", "null"], "pattern": PLACE_ID_PATTERN},
    "who": _id_array(CHAR_ID_PATTERN),
    "objects": _id_array(PROP_ID_PATTERN),
    # char id -> what they know after the beat, checked in knowledge_errors.
    "knows_after": {"type": "object"},
}, optional={
    "new_objects": {"type": "array", "items": _text(BEAT_NEW_OBJECT_MAX_CHARS), "maxItems": BEAT_NEW_OBJECTS_MAX},
})

KNOWLEDGE_SCHEMA = _document({
    "$schema": {"type": "string", "const": KNOWLEDGE_SCHEMA_NAME},
    "rev": {"type": "integer", "minimum": 1},
    "approved_at": _TIMESTAMP_OR_NULL,
    "updated_at": _NON_EMPTY_STRING,
}, optional={
    # Stage 5b (DEC-228): the ``rev`` the approval was given at; the episode
    # gate holds a v2 script back while it differs from ``rev`` (any write
    # after the approval moves ``rev``).
    "approved_rev": {"type": "integer", "minimum": 1},
    "world": _document({
        "geography": _NON_EMPTY_STRING,
        "period_details": _NON_EMPTY_STRING,
        "visual_motifs": {"type": "array", "items": _NON_EMPTY_STRING, "maxItems": WORLD_VISUAL_MOTIFS_MAX},
    }),
    "timeline": {"type": "array", "maxItems": EPISODES_PLANNED_MAX, "items": _document({
        "ep": {"type": "integer", "minimum": 1, "maximum": EPISODES_PLANNED_MAX},
        "beats": {"type": "array", "items": _BEAT_SCHEMA, "minItems": 1, "maxItems": TIMELINE_BEATS_MAX},
    })},
    "props_registry": dict(_id_array(PROP_ID_PATTERN), maxItems=KNOWLEDGE_PROPS_MAX),
    # char id -> LEDGER_STATE_SCHEMA, checked in knowledge_errors.
    "ledger_seed": {"type": "object"},
    # Plan 21 stage 1: approved by the agent run (see AGENT_APPROVED).
    "approved_by": _AGENT_APPROVED_SCHEMA,
})


def _listed_once(errors, path, ids) -> None:
    for item in sorted({item for item in ids if ids.count(item) > 1}):
        errors.append(f"{path}: {item!r} is listed twice")


def knowledge_errors(doc) -> list:
    """``validate()`` against ``KNOWLEDGE_SCHEMA``, plus what it cannot say:
    the world's and each beat's word caps; the timeline's episodes in
    increasing order, each once; ``knows_after`` keyed by character ids; no
    id listed twice in a beat or the registry; the ledger seed's states
    (``ledger_errors``); an approved document has every section. Only what
    the document decides on its own: the ids against the story are
    :func:`knowledge_reference_errors`."""
    errors = validate(doc, KNOWLEDGE_SCHEMA)
    if errors:
        return errors

    errors = []
    world = doc.get("world")
    if world is not None:
        _check_text(errors, "$.world.geography", world["geography"], max_words=WORLD_GEOGRAPHY_MAX_WORDS)
        _check_text(errors, "$.world.period_details", world["period_details"],
                    max_words=WORLD_PERIOD_DETAILS_MAX_WORDS)
        for i, motif in enumerate(world["visual_motifs"]):
            _check_text(errors, f"$.world.visual_motifs[{i}]", motif, max_words=WORLD_VISUAL_MOTIF_MAX_WORDS)
    timeline = doc.get("timeline") or []
    episodes = [entry["ep"] for entry in timeline]
    if episodes != sorted(set(episodes)):
        errors.append(f"$.timeline: episodes {episodes} must be in increasing order, each once")
    for i, entry in enumerate(timeline):
        for j, beat in enumerate(entry["beats"]):
            path = f"$.timeline[{i}].beats[{j}]"
            _check_text(errors, f"{path}.what", beat["what"], max_words=BEAT_WHAT_MAX_WORDS)
            _listed_once(errors, f"{path}.who", beat["who"])
            _listed_once(errors, f"{path}.objects", beat["objects"])
            for char_id, fact in beat["knows_after"].items():
                if not _search(CHAR_ID_PATTERN, char_id):
                    errors.append(f"{path}.knows_after: {char_id!r} is not a character id")
                    continue
                _check_text(errors, f"{path}.knows_after.{char_id}", fact, max_words=BEAT_KNOWS_AFTER_MAX_WORDS)
    if "props_registry" in doc:
        _listed_once(errors, "$.props_registry", doc["props_registry"])
    if "ledger_seed" in doc:
        errors.extend(ledger_errors(doc["ledger_seed"], "$.ledger_seed"))
    if doc["approved_at"] is not None:
        missing = [section for section in KNOWLEDGE_SECTIONS if section not in doc]
        if missing:
            errors.append(f"$.approved_at: the knowledge is approved with {', '.join(missing)} not written")
    return errors


def knowledge_reference_errors(doc, *, char_ids, place_ids, prop_ids, wardrobe_sets=None) -> list:
    """Every id the knowledge document names is one the story has:
    characters (*char_ids*) in each beat's ``who`` and ``knows_after`` and the
    ledger seed's keys, places (*place_ids*) and props (*prop_ids*) where
    they are named; with *wardrobe_sets* (``{char_id: [set ids of its
    look]}``), a ledger's wardrobe set is one of that character's. Run on a
    document :func:`knowledge_errors` accepts."""
    chars, places, props = set(char_ids), set(place_ids), set(prop_ids)
    errors = []

    def check(path, value, known, what):
        if value is not None and value not in known:
            errors.append(f"{path}: {value!r} is no {what} of the story")

    for i, entry in enumerate(doc.get("timeline") or []):
        for j, beat in enumerate(entry["beats"]):
            path = f"$.timeline[{i}].beats[{j}]"
            check(f"{path}.place_id", beat["place_id"], places, "place")
            for k, char_id in enumerate(beat["who"]):
                check(f"{path}.who[{k}]", char_id, chars, "character")
            for k, prop_id in enumerate(beat["objects"]):
                check(f"{path}.objects[{k}]", prop_id, props, "prop")
            for char_id in beat["knows_after"]:
                check(f"{path}.knows_after.{char_id}", char_id, chars, "character")
    for i, prop_id in enumerate(doc.get("props_registry") or []):
        check(f"$.props_registry[{i}]", prop_id, props, "prop")
    for char_id, state in (doc.get("ledger_seed") or {}).items():
        path = f"$.ledger_seed.{char_id}"
        check(path, char_id, chars, "character")
        check(f"{path}.location", state["location"], places, "place")
        for k, prop_id in enumerate(state["possessions"]):
            check(f"{path}.possessions[{k}]", prop_id, props, "prop")
        if wardrobe_sets is not None and char_id in chars:
            check(f"{path}.wardrobe_set", state["wardrobe_set"], set(wardrobe_sets.get(char_id) or ()),
                  f"wardrobe set of {char_id}")
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


# ============================================================ phase 3 documents (spec 2.7, 2.8, 6.2-6.4)
#
# What the phase-3 episode writer keeps on disk, plus the shipped episode
# templates it is written against (spec 6.2). Same rules as the phase-1/2
# documents above: every fixed object is closed; a ``*_errors`` function adds
# the checks the subset schema cannot express. Timing (6.4), prompts, shot
# resolution and the store wiring are later stages; this module only fixes
# the shapes they will build on.

# ------------------------------------------------------- episode_template_v1 (spec 6.2)

EPISODE_TEMPLATE_SCHEMA_NAME = "episode_template_v1"
EPISODE_TEMPLATE_SLOTS = ("recap", "hook", "body", "cliffhanger")

_RANGE_S = {"type": "array", "items": {"type": "number", "minimum": 0}, "minItems": 2, "maxItems": 2}
_RANGE_INT = {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 2, "maxItems": 2}

_EPISODE_TEMPLATE_SLOT_SCHEMA = _document({
    "functions": {"type": "array", "items": {"type": "string", "enum": list(SCENE_FUNCTIONS)}, "minItems": 1},
    "count": _RANGE_INT,
    "duration_s": _RANGE_S,
})

EPISODE_TEMPLATE_SCHEMA = _document({
    "$schema": {"type": "string", "const": EPISODE_TEMPLATE_SCHEMA_NAME},
    "template_id": {"type": "string", "pattern": _ID_PATTERN},
    "version": {"type": "integer", "minimum": 1},
    "label": bilingual(),
    "window_s": _RANGE_S,
    "target_s": {"type": "number", "minimum": 0},
    "tighten_above_s": {"type": "number", "minimum": 0},
    "scenes": _RANGE_INT,
    "shots": _RANGE_INT,
    "min_shot_s": {"type": "number", "minimum": 0},
    "recap_from_episode": {"type": "integer", "minimum": 1},
    "default_body_count": {"type": "integer", "minimum": 1},
    "slots": _document({slot: _EPISODE_TEMPLATE_SLOT_SCHEMA for slot in EPISODE_TEMPLATE_SLOTS}),
    "pauses_s": _document({
        "before_first_line": {"type": "number", "minimum": 0},
        "between_lines": {"type": "number", "minimum": 0},
        "tail": {"type": "number", "minimum": 0},
        "tail_peak": {"type": "number", "minimum": 0},
        "tail_floor": {"type": "number", "minimum": 0},
    }),
    # checked against SCENE_FUNCTIONS in episode_template_errors.
    "tail_peak_functions": {"type": "array", "items": {"type": "string"}},
    "hold_extension_max_s": {"type": "number", "minimum": 0},
    "end_card_s": {"type": "number", "minimum": 0},
    # keys checked against TRANSITIONS exactly in episode_template_errors.
    "transitions_s": {"type": "object"},
    "notes": {"type": "string"},
}, optional={
    # Phase 7 stage 4 (DEC-227): a template may set its own shots per scene
    # (EpisodeContext.episode_defaults merges it over the style's) and the
    # longest shot one clip covers; absent on the v1 templates.
    "shots_per_scene": {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 2, "maxItems": 2},
    "max_shot_s": {"type": "number", "minimum": 0},
    # Fruit-drama pack stage 1 (plan 20): a narrated template's [lo, hi]
    # share of the episode's words the narrator carries, and the short
    # character lines an episode has (the ones in frame, so lip-synced);
    # E1v2 and E2 turn them into one ask line (prompts.narration_of). The
    # end card's call to action is opt-in per template (stage 3). Absent on
    # every other template, whose prompts stay byte for byte (RC-M1).
    "narrator_share": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1},
                       "minItems": 2, "maxItems": 2},
    "character_lines": _RANGE_INT,
    "end_card_cta": {"type": "boolean"},
    # Plan 22 stage 3 (the confrontation format, confrontation_50s_v2): the
    # episode is one continuous scene in one place (E1 is told so and
    # max_places is 1, ``EpisodeContext.episode_defaults``); every boundary
    # a cut (``timing.plan_transitions``); the narrator offered only in
    # these slots (``steps/script.py``); a line's [lo, hi] words and the
    # episode's spoken [lo, hi] words (writing v3, ``timing.word_budget_v3``);
    # the silent reaction shots a scene of a native-speech board may have
    # (``shots.speech_shot_plan``). Each absent on every other template, whose
    # prompts, transitions and timing stay byte for byte (RC-M1, RC-M2).
    "single_place": {"type": "boolean"},
    "scene_transition": {"type": "string", "enum": ["cut"]},
    "narrator_slots": {"type": "array", "items": {"type": "string", "enum": list(EPISODE_TEMPLATE_SLOTS)},
                       "minItems": 1, "maxItems": len(EPISODE_TEMPLATE_SLOTS)},
    "line_words": _RANGE_INT,
    "episode_words": _RANGE_INT,
    "reaction_shots": _RANGE_INT,
})

# The longest line one native-speech shot can speak (``native_speech.capacity``
# of its longest sold length): a template's line_words never asks for more.
# Plan 27 stage 2: 22 (was 17) -- the 5-10 s window's 10 s shot, at 2.4 words/s.
TEMPLATE_LINE_WORDS_MAX = 22


def _range_pair_errors(errors, path, pair) -> None:
    """A [lo, hi] pair: 0 <= lo <= hi."""
    lo, hi = pair
    if not (0 <= lo <= hi):
        errors.append(f"{path}: [{lo}, {hi}] must satisfy 0 <= lo <= hi")


def _scene_count_feasible(scenes_range, *slot_counts) -> bool:
    """Whether some combination of counts within *slot_counts* (each a
    [lo, hi] pair) can land the total scene count inside *scenes_range*."""
    scenes_lo, scenes_hi = scenes_range
    lo = sum(count[0] for count in slot_counts)
    hi = sum(count[1] for count in slot_counts)
    return lo <= scenes_hi and hi >= scenes_lo


def episode_template_errors(doc) -> list:
    """``validate()`` against ``EPISODE_TEMPLATE_SCHEMA``, plus the cross-field
    checks the subset schema cannot express (spec 6.2, 6.4): the window/
    target/tighten ordering, every [lo, hi] pair, the four slots' functions
    covering ``SCENE_FUNCTIONS`` exactly once each, ``transitions_s``' keys
    equalling ``TRANSITIONS`` exactly, the pause ordering, that a valid body
    count exists both with and without the recap scene, and (stage 12b)
    that ``default_body_count`` -- E1's exact-count ask -- sits inside
    ``slots.body.count`` and its clamped total (:func:`timing.episode_slots`'
    own clamp, reimplemented locally) lands inside ``scenes`` for both
    cases."""
    errors = validate(doc, EPISODE_TEMPLATE_SCHEMA)
    if errors:
        return errors

    errors = []
    _range_pair_errors(errors, "$.window_s", doc["window_s"])
    _range_pair_errors(errors, "$.scenes", doc["scenes"])
    _range_pair_errors(errors, "$.shots", doc["shots"])
    if "shots_per_scene" in doc:
        _range_pair_errors(errors, "$.shots_per_scene", doc["shots_per_scene"])
    for key in ("narrator_share", "character_lines"):
        if key in doc:
            _range_pair_errors(errors, f"$.{key}", doc[key])
    if ("narrator_share" in doc) != ("character_lines" in doc):
        errors.append("$: narrator_share and character_lines come together (a narrated template) or not at all")
    # Plan 22 stage 3: the confrontation format's keys.
    for key in ("line_words", "episode_words", "reaction_shots"):
        if key in doc:
            _range_pair_errors(errors, f"$.{key}", doc[key])
    if "line_words" in doc:
        lo, hi = doc["line_words"]
        if lo < 1 or hi > TEMPLATE_LINE_WORDS_MAX:
            errors.append(f"$.line_words: [{lo}, {hi}] must lie within [1, {TEMPLATE_LINE_WORDS_MAX}] (one "
                          "line, one shot of at most 10 s)")
        if "episode_words" in doc and doc["episode_words"][1] < lo:
            errors.append(f"$.episode_words: {doc['episode_words']} cannot hold one line of line_words {lo}")
    if len(set(doc.get("narrator_slots") or ())) != len(doc.get("narrator_slots") or ()):
        errors.append("$.narrator_slots: each slot at most once")
    if doc.get("single_place") is False:
        errors.append("$.single_place: true or absent (absent: the style's max_places)")
    if "max_shot_s" in doc and doc["max_shot_s"] < doc["min_shot_s"]:
        errors.append(f"$.max_shot_s: {doc['max_shot_s']} is below min_shot_s ({doc['min_shot_s']})")

    window_lo, window_hi = doc["window_s"]
    target, tighten = doc["target_s"], doc["tighten_above_s"]
    if not (window_lo < target < tighten < window_hi):
        errors.append(
            f"$: window_s lo ({window_lo}) < target_s ({target}) < tighten_above_s ({tighten}) "
            f"< window_s hi ({window_hi}) does not hold"
        )

    slots = doc["slots"]
    all_functions = []
    for slot_name in EPISODE_TEMPLATE_SLOTS:
        slot = slots[slot_name]
        _range_pair_errors(errors, f"$.slots.{slot_name}.count", slot["count"])
        _range_pair_errors(errors, f"$.slots.{slot_name}.duration_s", slot["duration_s"])
        all_functions.extend(slot["functions"])

    seen = set()
    for fn in all_functions:
        if fn in seen:
            errors.append(f"$.slots: function {fn!r} is claimed by more than one slot")
        seen.add(fn)
    if set(all_functions) != set(SCENE_FUNCTIONS):
        errors.append(
            f"$.slots: functions {sorted(set(all_functions))} must equal SCENE_FUNCTIONS "
            f"{sorted(SCENE_FUNCTIONS)} exactly"
        )

    transitions = doc["transitions_s"]
    if set(transitions) != set(TRANSITIONS):
        errors.append(
            f"$.transitions_s: keys {sorted(transitions)} must equal TRANSITIONS {sorted(TRANSITIONS)} exactly"
        )
    for key, value in transitions.items():
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0):
            errors.append(f"$.transitions_s.{key}: {value!r} must be a number >= 0")

    for fn in doc["tail_peak_functions"]:
        if fn not in SCENE_FUNCTIONS:
            errors.append(f"$.tail_peak_functions: {fn!r} is not a scene function")

    pauses = doc["pauses_s"]
    tail_floor, tail, tail_peak = pauses["tail_floor"], pauses["tail"], pauses["tail_peak"]
    if not (tail_floor <= tail <= tail_peak):
        errors.append(
            f"$.pauses_s: tail_floor ({tail_floor}) <= tail ({tail}) <= tail_peak ({tail_peak}) does not hold"
        )

    if not _scene_count_feasible(doc["scenes"], slots["hook"]["count"], slots["body"]["count"], slots["cliffhanger"]["count"]):
        errors.append("$.scenes: no body count in slots.body.count fits scenes for an episode without a recap (episode 1)")
    if not _scene_count_feasible(
        doc["scenes"], slots["recap"]["count"], slots["hook"]["count"], slots["body"]["count"], slots["cliffhanger"]["count"]
    ):
        errors.append(
            f"$.scenes: no body count in slots.body.count fits scenes for an episode with the recap scene "
            f"(episode >= {doc['recap_from_episode']})"
        )

    # Stage 12b: default_body_count is E1's exact-count ask (timing.episode_slots).
    # It must itself sit inside slots.body.count, and -- clamped the same way
    # episode_slots clamps it -- the resulting scene total must still land
    # inside scenes, both without a recap (episode 1) and with one (episode
    # >= recap_from_episode). Given the two feasibility checks above already
    # passed, this can only fail on default_body_count itself being out of
    # range; it stays a belt-and-suspenders check (same idiom as the
    # SCENE_FUNCTIONS/TRANSITIONS-key equality checks above) rather than a
    # shared implementation, so this module keeps no dependency on timing.py.
    body_lo, body_hi = slots["body"]["count"]
    default_body_count = doc["default_body_count"]
    if not (body_lo <= default_body_count <= body_hi):
        errors.append(
            f"$.default_body_count: {default_body_count} is outside slots.body.count [{body_lo}, {body_hi}]"
        )
    scenes_lo, scenes_hi = doc["scenes"]
    for ep_label, has_recap in (("episode 1", False), (f"episode >= {doc['recap_from_episode']}", True)):
        fixed = 2 + (1 if has_recap else 0)
        lo = max(body_lo, scenes_lo - fixed)
        hi = min(body_hi, scenes_hi - fixed)
        if lo > hi:
            continue  # already reported by the feasibility checks above
        n = min(max(default_body_count, lo), hi)
        total = fixed + n
        if not (scenes_lo <= total <= scenes_hi):
            errors.append(
                f"$.default_body_count: the clamped slot list for {ep_label} has {total} scene(s), "
                f"outside scenes [{scenes_lo}, {scenes_hi}]"
            )

    return errors


# ----------------------------------------------------------- episode_script_v1 (spec 2.7)

EPISODE_SCRIPT_SCHEMA_NAME = "episode_script_v1"

SCENE_ID_PATTERN = r"^s[0-9]{2}$"
LINE_ID_PATTERN = r"^l[0-9]{2}$"
# A shot id is a stable key, never a position (walk follow-up F5): a re-plan
# gives new shots the next free numbers, so an episode can pass sh99 --
# sh100..sh999 are ids too. Every id of the two-digit form still is one.
SHOT_ID_PATTERN = r"^sh([0-9]{2}|[1-9][0-9]{2})$"
SPEAKER_PATTERN = r"^(char_[a-z0-9_]{1,40}|narrator)$"
SFX_AT_PATTERN = r"^(start|l[0-9]{2})$"
TEXT_HASH_PATTERN = r"^[0-9a-f]{16}$"
# A scene's time variant names one of its place's variants, so it has the
# place document's own bound (episode_script_context_errors checks it exists).
SCENE_TIME_VARIANT_PATTERN = TIME_VARIANT_PATTERN

BODY_FUNCTIONS = ("setup", "rising", "peak", "turn")

# Line ids are fixed blocks per scene: scene sNN owns l(4*NN) .. l(4*NN+3),
# one per line a scene may hold (``maxItems`` 4). A regenerated scene keeps
# its own block, so a line id never moves to another scene and the audio
# measured for one line (``assets/voice/line_NN.*``) can never be taken for
# another's. s00 -> l00-l03, s01 -> l04-l07, ..., s12 -> l48-l51.
LINES_PER_SCENE_BLOCK = 4
_LAST_BLOCK_SCENE = 24  # l96-l99: the last block two digits can hold


def line_id_for(scene_id, k) -> str:
    """The id of line *k* (0-based) of scene *scene_id*: ``l{4 * NN + k:02d}``.
    ``ValueError`` for a malformed scene id, a *k* outside 0..3, or a scene
    past s24 (whose block no longer fits two digits)."""
    if not (isinstance(scene_id, str) and len(scene_id) == 3 and scene_id[0] == "s"
            and scene_id[1:].isascii() and scene_id[1:].isdigit()):
        raise ValueError(f"not a scene id: {scene_id!r}")
    number = int(scene_id[1:])
    if type(k) is not int or not 0 <= k < LINES_PER_SCENE_BLOCK:
        raise ValueError(f"a scene holds lines 0..{LINES_PER_SCENE_BLOCK - 1}, not {k!r}")
    if number > _LAST_BLOCK_SCENE:
        raise ValueError(f"scene {scene_id!r} has no two-digit line block")
    return f"l{LINES_PER_SCENE_BLOCK * number + k:02d}"

_EPISODE_SCRIPT_LINE_TIMING_SCHEMA = _document({
    "source": {"type": "string", "enum": ["estimated", "tts_word_timestamps", "audio_duration_only"]},
    "duration_s": {"type": "number", "minimum": 0},
    "text_hash": {"type": "string", "pattern": TEXT_HASH_PATTERN},
    "voice": {"type": ["string", "null"]},
    "audio": {"type": ["string", "null"]},
}, optional={
    # Plan 24 stage 1 (D-1): an estimate spoken by a voice whose overrun is
    # not 1.0 records it (``timing.estimated_timing``); absent on every line
    # estimated or measured before, which validates as is.
    "speech_factor": {"type": "number", "minimum": 0.1},
})

_EPISODE_SCRIPT_LINE_SCHEMA = _document({
    "line_id": {"type": "string", "pattern": LINE_ID_PATTERN},
    "speaker": {"type": "string", "pattern": SPEAKER_PATTERN},
    "text": _text(400),
    "emotion": {"type": "string", "enum": list(EMOTIONS)},
    "delivery": {"type": "string", "maxLength": 120},
    "timing": _EPISODE_SCRIPT_LINE_TIMING_SCHEMA,
})

_EPISODE_SCRIPT_SFX_CUE_SCHEMA = _document({
    "at": {"type": "string", "pattern": SFX_AT_PATTERN},
    "cue": {"type": "string", "pattern": _ID_PATTERN},
})

# Phase 5 (plan 11 stage 3, DEC-177): the most hooks open when an episode
# starts that E1 offers it to pay off -- the oldest still open first, and
# the window E4's memory block shows (``prompts._E4_MEMORY_MAX_HOOKS``) --
# and so the most one scene's ``pays_off`` may name. The fold can hold far
# more (HOOKS_OPENED_MAX per episode); a prompt never lists them all.
PAYOFF_HOOKS_MAX = 4

# The kinds of a consistency issue (spec 4.3): E4's own, and ``hook_payoff``
# (phase 5 stage 3) -- a scene that does not pay off the hook it names, or an
# episode where no body scene pays off an open one (the script step's
# pre-check, or E4 judging the lines). Widening the list keeps every stored
# report valid.
CONSISTENCY_ISSUE_KINDS = ("continuity", "character", "place", "series_memory", "hook_payoff", "other")

SCENE_SUMMARY_MAX_CHARS = 400
# A writing-v3 summary's words (E1v3: one or two sentences of cause and
# effect); every other script keeps 15 (``episode_script_errors``).
SCENE_SUMMARY_V3_MAX_WORDS = 30

_EPISODE_SCRIPT_LINE_PLAN_LINE_SCHEMA = _document({
    "kind": {"type": "string", "enum": ["narrator", "character"]},
    "speaker": {"type": "string", "pattern": SPEAKER_PATTERN},
    "seconds": {"type": "number", "minimum": 0},
    "max_words": {"type": "integer", "minimum": 1},
}, optional={
    # A native-speech story's planned clip length for the line.
    "clip_s": {"type": "integer", "minimum": 1},
    # Plan 27 stage 2: a native-speech line's planned floor (its share of its shot's).
    "min_words": {"type": "integer", "minimum": 1},
})

# Plan 27 stage 2: one planned shot of a native-speech scene -- its clip, the
# lines it speaks in order (one exchange; one line for the narrator's silent
# clip), the words it is planned at and whether it is a speaking shot.
_EPISODE_SCRIPT_LINE_PLAN_SHOT_SCHEMA = _document({
    "clip_s": {"type": "integer", "minimum": 1},
    "line_ids": {"type": "array", "items": {"type": "string", "pattern": LINE_ID_PATTERN}, "minItems": 1,
                 "maxItems": 4},
    "words_min": {"type": "integer", "minimum": 0},
    "words_max": {"type": "integer", "minimum": 1},
    "speaks": {"type": "boolean"},
})

_EPISODE_SCRIPT_LINE_PLAN_SCHEMA = _document({
    "allowed_speech_s": {"type": "number", "minimum": 0},
    "lines": {"type": "array", "items": _EPISODE_SCRIPT_LINE_PLAN_LINE_SCHEMA, "maxItems": 4},
    "max_words": {"type": "integer", "minimum": 0},
    "min_words": {"type": "integer", "minimum": 0},
}, optional={
    # Plan 27 stage 2: a native-speech scene's shots (``timing.scene_plan``);
    # absent on every plan stored before and on every TTS story's.
    "shots": {"type": "array", "items": _EPISODE_SCRIPT_LINE_PLAN_SHOT_SCHEMA, "maxItems": 4},
})

_EPISODE_SCRIPT_SCENE_SCHEMA = _document({
    "scene_id": {"type": "string", "pattern": SCENE_ID_PATTERN},
    "function": {"type": "string", "enum": list(SCENE_FUNCTIONS)},
    "place_id": {"type": "string", "pattern": PLACE_ID_PATTERN},
    "time_variant": {"type": "string", "pattern": SCENE_TIME_VARIANT_PATTERN},
    "characters": {"type": "array", "items": {"type": "string", "pattern": CHAR_ID_PATTERN}, "maxItems": 6},
    "props": {"type": "array", "items": {"type": "string", "pattern": PROP_ID_PATTERN}, "maxItems": 4},
    # Plan 22 stage 3: 400 (was 200) -- a writing-v3 summary is one or two
    # sentences of up to 30 words; widening keeps every stored script valid.
    "summary": {"type": "string", "maxLength": SCENE_SUMMARY_MAX_CHARS},
    "emotion": {"type": "string", "enum": list(EMOTIONS)},
    "target_duration_s": {"type": "number", "minimum": 0.5, "maximum": 20},
    "lines": {"type": "array", "items": _EPISODE_SCRIPT_LINE_SCHEMA, "maxItems": 4},
    "sfx_cues": {"type": "array", "items": _EPISODE_SCRIPT_SFX_CUE_SCHEMA, "maxItems": 6},
    "on_screen_text": {"type": ["string", "null"]},
    "state": {"type": "string", "enum": ["stub", "written"]},
    "source": {"type": "string", "enum": ["E1", "E2", "E3", "edit"]},
    "rev": {"type": "integer", "minimum": 1},
}, optional={
    # Phase 5 stage 3: the open hooks E1 said this scene pays off, each
    # verbatim (checked against the season by the script step's pre-check,
    # never here: the document does not know the season). Absent means none,
    # and so does an empty list; a script written before it validates as is.
    "pays_off": {"type": "array", "items": _text(HOOK_MAX_LENGTH), "maxItems": PAYOFF_HOOKS_MAX},
    # Plan 23 stage D5: the appearance variant a character of the scene wears in it
    # ({char_id: variant_id}, E1v3's character states block, offered only when the cast
    # has variants). Absent on every other scene: each character in its base look.
    "states": {"type": "object"},
    # Plan 24 stage 1 (D-2): the scene's slot [lo, hi] and its line plan
    # (``timing.scene_plan``), stored when a writing-v3 beat sheet is applied.
    # Absent on every scene written before, and on every v1/v2 scene.
    "slot_s": {"type": "array", "items": {"type": "number", "minimum": 0}, "minItems": 2, "maxItems": 2},
    "line_plan": _EPISODE_SCRIPT_LINE_PLAN_SCHEMA,
    # Plan 24 stage 5 (D-6): on a narrated format's writing-v3 beat sheet, whether
    # the body scene carries one character line (true) or is the narrator's alone
    # (false); ``timing.scene_plan`` plans the scene's lines from it. Absent on
    # every other scene: the plan is what it was. Plan 28 stage A2: on a native-speech
    # story with the narrator on, the episode's fit (timing.fit_episode_plans) sets it
    # False on a plain body scene of any format when the episode's clips cannot fit.
    "character_line": {"type": "boolean"},
    # Plan 28 stage A2: the seconds a native-speech scene's clips are held under (below
    # its slot's top) by the episode's fit, so the plan recomputes the same; absent: the slot's top.
    "clip_cap_s": {"type": "number", "minimum": 1},
})

_EPISODE_SCRIPT_HOOK_SCHEMA = _document({"on_screen_text": {"type": ["string", "null"]}})

_EPISODE_SCRIPT_CLIFFHANGER_SCHEMA = _document({
    "scene_id": {"type": ["string", "null"]},
    "reveal": {"type": ["string", "null"], "maxLength": 300},
    "cut_to_black": {"type": "boolean"},
})

_EPISODE_SCRIPT_TIMING_SCENE_SCHEMA = _document({
    "duration_s": {"type": "number"},
    "tail_s": {"type": "number"},
    "hold_s": {"type": "number"},
    "state": {"type": "string", "enum": ["ok", "tightened", "over", "under"]},
})

_EPISODE_SCRIPT_TIMING_FLAG_SCHEMA = _document({
    "kind": {"type": "string", "enum": ["trim_line", "scene_over", "episode_over", "episode_under"]},
    "scene_id": {"type": ["string", "null"]},
    "line_id": {"type": ["string", "null"]},
    "seconds": {"type": "number"},
    "message": {"type": "string"},
})

_EPISODE_SCRIPT_TIMING_SCHEMA = _or_null(_document({
    "total_s": {"type": "number"},
    "window_s": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
    "target_s": {"type": "number"},
    "state": {"type": "string", "enum": ["ok", "tightened", "over", "under"]},
    # keyed by scene id -> _EPISODE_SCRIPT_TIMING_SCENE_SCHEMA, checked in episode_script_errors.
    "scenes": {"type": "object"},
    "flags": {"type": "array", "items": _EPISODE_SCRIPT_TIMING_FLAG_SCHEMA},
    "estimated_lines": {"type": "integer"},
    "measured_lines": {"type": "integer"},
}, optional={
    # Plan 24 stage 5 (D-6): the narrator's share of the episode's planned words
    # (``timing.plan_narrator_share``), stored on a narrated template's writing-v3
    # script only; a report, never a refusal.
    "narrator_share": {"type": "number", "minimum": 0, "maximum": 1},
}))

_EPISODE_SCRIPT_ISSUE_SCHEMA = _document({
    "scene_id": {"type": ["string", "null"]},
    "kind": {"type": "string", "enum": list(CONSISTENCY_ISSUE_KINDS)},
    "fix": {"type": "string", "maxLength": 300},
})

_EPISODE_SCRIPT_CONSISTENCY_REPORT_SCHEMA = _or_null(_document({
    "passed": {"type": "boolean"},
    "issues": {"type": "array", "items": _EPISODE_SCRIPT_ISSUE_SCHEMA, "maxItems": 20},
    "checked_rev": {"type": "integer"},
    "checked_at": {"type": "string"},
    "stale": {"type": "boolean"},
}))

# Phase 7 stage 6a (A16, DEC-230): a v2 script's first-watch report -- J1,
# a judge reading the episode as a first-time viewer would, plus the
# script step's deterministic checks (a line repeating another, a hook with
# no on-screen text) merged into it. What a viewer takes away after one
# watch (who wants what, what happens, why it matters), whether it passed,
# and the issues of this closed list. Its staleness follows the consistency
# report's: ``checked_rev``, and ``stale`` once the script is rewritten.
FIRST_WATCH_ISSUE_KINDS = ("unclear_goal", "unmotivated", "unintroduced", "object_unseen", "repeated_line",
                           "no_hook_text")
# Plan 22 stage 3 (writing v3, J1 version 3): the kinds J1v3 adds -- a line
# that adds nothing, a line that is a fragment, scenes that do not tell the
# episode's logline -- and ``line_too_long``, the script step's own
# deterministic check of a line past the format's words per line. J1v2's
# list above is unchanged (its prompt and schema are pinned); a stored report
# takes either list (:data:`FIRST_WATCH_ALL_KINDS`), so every older one stays
# valid.
FIRST_WATCH_ISSUE_KINDS_V3 = ("line_no_progress", "incomplete_sentence", "logline_mismatch")
FIRST_WATCH_DETERMINISTIC_KINDS_V3 = ("line_too_long",)
FIRST_WATCH_ALL_KINDS = FIRST_WATCH_ISSUE_KINDS + FIRST_WATCH_ISSUE_KINDS_V3 + FIRST_WATCH_DETERMINISTIC_KINDS_V3
FIRST_WATCH_TEXT_MAX_CHARS = 300
# J1 version 2 (DEC-248): each issue's severity, and the report passes
# exactly when none is blocking. An issue without one (a version-1 report)
# reads as blocking: version 1 failed on any issue.
FIRST_WATCH_SEVERITIES = ("blocking", "minor")

_EPISODE_SCRIPT_FIRST_WATCH_ISSUE_SCHEMA = _document({
    "scene_id": {"type": ["string", "null"]},
    "kind": {"type": "string", "enum": list(FIRST_WATCH_ALL_KINDS)},
    "fix": {"type": "string", "maxLength": FIRST_WATCH_TEXT_MAX_CHARS},
}, optional={
    "severity": {"type": "string", "enum": list(FIRST_WATCH_SEVERITIES)},
})

_EPISODE_SCRIPT_FIRST_WATCH_SCHEMA = _document({
    "who_wants_what": {"type": "string", "maxLength": FIRST_WATCH_TEXT_MAX_CHARS},
    "what_happens": {"type": "string", "maxLength": FIRST_WATCH_TEXT_MAX_CHARS},
    "why_it_matters": {"type": "string", "maxLength": FIRST_WATCH_TEXT_MAX_CHARS},
    "passed": {"type": "boolean"},
    "issues": {"type": "array", "items": _EPISODE_SCRIPT_FIRST_WATCH_ISSUE_SCHEMA, "maxItems": 20},
    "checked_rev": {"type": "integer"},
    "checked_at": {"type": "string"},
    "stale": {"type": "boolean"},
}, optional={
    # The J1 prompt version that judged it (``prompts.J1_PROMPT_VERSION``);
    # absent: version 1.
    "version": {"type": "integer", "minimum": 1},
})

# Phase 7 follow-up, stage G: what the script step's repair pass tried on a
# v2 script after J1 (``steps/script.py``, ``_Run.repair``): one record per
# pass of the run that produced the current report -- the scenes written
# again, each with the issue kinds that sent it and the framing part when
# the partial E3 wrote it (null: E2), the report's issue count before the
# pass and after its J1 (null while J1 has not answered again), and the
# scenes whose call failed. Replaced by the next run that repairs.
_EPISODE_SCRIPT_REPAIR_SCENE_SCHEMA = _document({
    "scene_id": {"type": "string", "pattern": SCENE_ID_PATTERN},
    # DEC-260: a repair is also written for the consistency check's (E4) issues.
    "kinds": {"type": "array", "items": {"type": "string",
                                         "enum": list(FIRST_WATCH_ALL_KINDS) + list(CONSISTENCY_ISSUE_KINDS)},
              "minItems": 1, "maxItems": len(FIRST_WATCH_ALL_KINDS) + len(CONSISTENCY_ISSUE_KINDS)},
    "part": {"type": ["string", "null"], "enum": ["hook", "cliffhanger", "recap", None]},
}, optional={
    # DEC-248: the story props an object_unseen repair listed on the scene
    # for its rewrite (never a new prop of the library).
    "props_added": {"type": "array", "items": {"type": "string", "pattern": PROP_ID_PATTERN}, "minItems": 1,
                    "maxItems": 4},
})

_EPISODE_SCRIPT_REPAIR_PASS_SCHEMA = _document({
    "pass": {"type": "integer", "minimum": 1},
    "scenes": {"type": "array", "items": _EPISODE_SCRIPT_REPAIR_SCENE_SCHEMA, "maxItems": 12},
    "issues_before": {"type": "integer", "minimum": 0},
    "issues_after": {"type": ["integer", "null"], "minimum": 0},
    "failed": {"type": "array", "items": {"type": "string", "pattern": SCENE_ID_PATTERN}, "maxItems": 12},
})

# Plan 19 stage 3 (F4): who approved the script when the fast track did
# (``approved_by: "fast_track"``, ``APPROVED_BY``'s value, written out here:
# the tuple is defined further down) and, over an approval "anyway", the
# blocking issues it went over -- E4's (``check: consistency``) and J1's
# (``check: first_watch``) -- so the review names them. Absent on the
# human's approvals; cleared with the approval (``episode_common.mark_changed``).
SCRIPT_APPROVED_OVER_CHECKS = ("consistency", "first_watch")
_EPISODE_SCRIPT_APPROVED_OVER_SCHEMA = _document({
    "scene_id": {"type": ["string", "null"]},
    "kind": {"type": "string", "enum": sorted(set(CONSISTENCY_ISSUE_KINDS) | set(FIRST_WATCH_ALL_KINDS))},
    "fix": {"type": "string", "maxLength": 300},
    "check": {"type": "string", "enum": list(SCRIPT_APPROVED_OVER_CHECKS)},
})

# Plan 22 stage 3 (writing v3, E1v3): the episode's spine -- its logline (one
# complete sentence: who wants what, what they do, where it leaves them),
# what the main character wants, what stands in the way, what is at stake and
# the turn -- word-capped by the prompt (``prompts.SPINE_MAX_WORDS``), bounded
# here in characters.
SPINE_KEYS = ("logline", "want", "obstacle", "stakes", "turn")
_EPISODE_SCRIPT_SPINE_SCHEMA = _document({key: _text(300) for key in SPINE_KEYS})

EPISODE_SCRIPT_SCHEMA = _document({
    "$schema": {"type": "string", "const": EPISODE_SCRIPT_SCHEMA_NAME},
    "ep": {"type": "integer", "minimum": 1, "maximum": 99},
    "title": {"type": ["string", "null"], "maxLength": 80},
    "language": {"type": "string", "enum": list(LANGUAGES)},
    "template_id": {"type": "string", "enum": list(defaults.EPISODE_TEMPLATE_IDS)},
    "hook": _EPISODE_SCRIPT_HOOK_SCHEMA,
    "scenes": {"type": "array", "items": _EPISODE_SCRIPT_SCENE_SCHEMA, "maxItems": 12},
    "cliffhanger": _EPISODE_SCRIPT_CLIFFHANGER_SCHEMA,
    "next_episode_teaser": {"type": ["string", "null"]},
    "timing": _EPISODE_SCRIPT_TIMING_SCHEMA,
    "consistency_report": _EPISODE_SCRIPT_CONSISTENCY_REPORT_SCHEMA,
    "approved_anyway": {"type": ["string", "null"]},
    "approved_at": {"type": ["string", "null"]},
    "rev": {"type": "integer", "minimum": 1},
    "created_at": _NON_EMPTY_STRING,
    "updated_at": _NON_EMPTY_STRING,
}, optional={
    # Phase 7 stage 6a (DEC-230): a v2 script's own, once J1 has run;
    # absent on every legacy script and on a v2 one not judged yet.
    "first_watch": _EPISODE_SCRIPT_FIRST_WATCH_SCHEMA,
    # Phase 7 follow-up, stage G: the repair passes of the run that produced
    # the first-watch report; absent until one ran on this script.
    "repairs": {"type": "array", "items": _EPISODE_SCRIPT_REPAIR_PASS_SCHEMA, "maxItems": 10},
    # Plan 19 stage 3 (F4): the fast track's own approval (see above).
    "approved_by": {"type": "string", "enum": ["fast_track"]},
    "approved_over": {"type": "array", "items": _EPISODE_SCRIPT_APPROVED_OVER_SCHEMA, "maxItems": 40},
    # Plan 22 stage 3 (writing v3): the episode's dramatic spine E1v3 writes
    # before its scenes; absent on every script written before (RC-M3).
    "spine": _EPISODE_SCRIPT_SPINE_SCHEMA,
})


def _word_cap_errors(errors, path, value, max_words) -> None:
    """Enforce a maximum word count on *value* when it is a string; a null
    value (an unwritten optional field) is not an error here."""
    if isinstance(value, str) and _words(value) > max_words:
        errors.append(f"{path}: {_words(value)} words, expected at most {max_words}")


def _variant_map_errors(errors, path, mapping, char_ids) -> None:
    """A ``{char_id: variant_id}`` map (plan 23 stage D5: a scene's
    ``states``, a shot's ``variants``): each key one of *char_ids* (the
    scene's characters, the shot's framed ones), each value a variant id."""
    for key, value in mapping.items():
        if key not in char_ids:
            errors.append(f"{path}: {key!r} is not one of its characters")
        if not (isinstance(value, str) and _search(VARIANT_ID_PATTERN, value)):
            errors.append(f"{path}.{key}: {value!r} is not a variant id ({VARIANT_ID_PATTERN})")


def episode_script_errors(doc) -> list:
    """``validate()`` against ``EPISODE_SCRIPT_SCHEMA``, plus the cross-field
    checks the subset schema cannot express (spec 2.7): scene/line id
    sequencing, the function order, speaker/sfx/cliffhanger references within
    the episode, word caps, stub scenes carrying no lines, a scene's
    ``pays_off`` hooks non-blank and each listed once, and the timing
    block's scenes keyed only by real scene ids."""
    errors = validate(doc, EPISODE_SCRIPT_SCHEMA)
    if errors:
        return errors

    errors = []
    scenes = doc["scenes"]
    scene_ids = [scene["scene_id"] for scene in scenes]

    for i in range(1, len(scene_ids)):
        if scene_ids[i] <= scene_ids[i - 1]:
            errors.append(
                f"$.scenes[{i}].scene_id: {scene_ids[i]!r} does not strictly increase after {scene_ids[i - 1]!r}"
            )

    for scene in scenes:
        sid, fn = scene["scene_id"], scene["function"]
        if sid == "s00" and fn != "recap":
            errors.append(f"$.scenes[{sid}]: scene_id 's00' must have function 'recap', not {fn!r}")
        if fn == "recap" and sid != "s00":
            errors.append(f"$.scenes[{sid}]: a recap scene's scene_id must be 's00', not {sid!r}")

    if scenes:
        functions = [scene["function"] for scene in scenes]
        recap_positions = [i for i, fn in enumerate(functions) if fn == "recap"]
        if recap_positions not in ([], [0]):
            errors.append(f"$.scenes: function order {functions} has 'recap' somewhere other than first")

        hook_index = 1 if recap_positions == [0] else 0
        hook_positions = [i for i, fn in enumerate(functions) if fn == "hook"]
        if hook_positions != [hook_index]:
            errors.append(
                f"$.scenes: function order {functions} must have exactly one 'hook' right after an optional recap"
            )

        cliff_positions = [i for i, fn in enumerate(functions) if fn == "cliffhanger"]
        if cliff_positions != [len(functions) - 1]:
            errors.append(f"$.scenes: function order {functions} must have exactly one 'cliffhanger', last")

        body_start = hook_index + 1 if hook_positions == [hook_index] else hook_index
        body_end = len(functions) - 1 if cliff_positions == [len(functions) - 1] else len(functions)
        for i in range(body_start, body_end):
            if functions[i] not in BODY_FUNCTIONS:
                errors.append(f"$.scenes[{i}].function: {functions[i]!r} is not a body function {BODY_FUNCTIONS}")

    for scene in scenes:
        chars = set(scene["characters"])
        line_ids = {line["line_id"] for line in scene["lines"]}
        for k, line in enumerate(scene["lines"]):
            line_id = line["line_id"]
            # Each scene's lines use its own block, in order (line_id_for):
            # unique and increasing in reading order follow from it.
            try:
                expected = line_id_for(scene["scene_id"], k)
            except ValueError as exc:
                errors.append(f"$.scenes[{scene['scene_id']}].lines[{k}].line_id: {exc}")
            else:
                if line_id != expected:
                    errors.append(
                        f"$.scenes[{scene['scene_id']}].lines[{k}].line_id: {line_id!r}, expected {expected!r} "
                        f"(line {k + 1} of scene {scene['scene_id']}'s block)"
                    )

            speaker = line["speaker"]
            if speaker != "narrator" and speaker not in chars:
                errors.append(
                    f"$.scenes[{scene['scene_id']}].lines[{line_id}]: speaker {speaker!r} is not in the "
                    f"scene's characters"
                )

            if line["timing"]["duration_s"] <= 0:
                errors.append(f"$.scenes[{scene['scene_id']}].lines[{line_id}].timing.duration_s: must be > 0")

            _word_cap_errors(errors, f"$.scenes[{scene['scene_id']}].lines[{line_id}].text", line["text"], 22)

        for cue in scene["sfx_cues"]:
            at = cue["at"]
            if at != "start" and at not in line_ids:
                errors.append(
                    f"$.scenes[{scene['scene_id']}].sfx_cues: 'at' {at!r} is not 'start' or a line id of this scene"
                )

        if scene["state"] == "stub" and scene["lines"]:
            errors.append(f"$.scenes[{scene['scene_id']}]: a stub scene must have no lines")

        # Plan 22 stage 3: a writing-v3 script (it has a spine) summarises a scene in up to 30 words.
        summary_words = SCENE_SUMMARY_V3_MAX_WORDS if doc.get("spine") is not None else 15
        _word_cap_errors(errors, f"$.scenes[{scene['scene_id']}].summary", scene["summary"], summary_words)
        _word_cap_errors(errors, f"$.scenes[{scene['scene_id']}].on_screen_text", scene["on_screen_text"], 6)

        paid = scene.get("pays_off") or []
        for k, hook in enumerate(paid):
            _check_text(errors, f"$.scenes[{scene['scene_id']}].pays_off[{k}]", hook)
        for hook in sorted({hook for hook in paid if paid.count(hook) > 1}):
            errors.append(f"$.scenes[{scene['scene_id']}].pays_off: {hook!r} is listed twice")
        if "states" in scene:
            _variant_map_errors(errors, f"$.scenes[{scene['scene_id']}].states", scene["states"], chars)

    cliff_scene_id = doc["cliffhanger"]["scene_id"]
    if cliff_scene_id is not None:
        last_id = scene_ids[-1] if scene_ids else None
        if cliff_scene_id != last_id:
            errors.append(f"$.cliffhanger.scene_id: {cliff_scene_id!r} is not the last scene's id ({last_id!r})")

    _word_cap_errors(errors, "$.hook.on_screen_text", doc["hook"]["on_screen_text"], 6)
    _word_cap_errors(errors, "$.next_episode_teaser", doc["next_episode_teaser"], 15)

    timing = doc["timing"]
    if timing is not None:
        scene_id_set = set(scene_ids)
        for key, entry in timing["scenes"].items():
            if key not in scene_id_set:
                errors.append(f"$.timing.scenes: {key!r} is not one of the episode's scene ids")
            else:
                errors.extend(validate(entry, _EPISODE_SCRIPT_TIMING_SCENE_SCHEMA, f"$.timing.scenes.{key}"))

    return errors


def episode_script_context_errors(doc, *, cast_ids, places, prop_ids, sfx_cues, narrator_enabled, max_places) -> list:
    """Cross-checks against the story (pure, no schema validation): the
    entity ids, place time variants, sfx cue names and the narrator opt-in an
    ``episode_script_v1`` document may reference, and the story's cap on
    distinct places per episode. ``places`` is a dict place_id -> iterable of
    that place's existing time-variant names."""
    errors = []
    cast_ids = set(cast_ids)
    prop_ids = set(prop_ids)
    sfx_cues = set(sfx_cues)
    used_places = set()

    for scene in doc["scenes"]:
        sid = scene["scene_id"]
        for char_id in scene["characters"]:
            if char_id not in cast_ids:
                errors.append(f"$.scenes[{sid}].characters: {char_id!r} is not in the story's cast")

        place_id = scene["place_id"]
        used_places.add(place_id)
        if place_id not in places:
            errors.append(f"$.scenes[{sid}].place_id: {place_id!r} is not one of the story's places")
        elif scene["time_variant"] not in set(places[place_id]):
            errors.append(f"$.scenes[{sid}].time_variant: {scene['time_variant']!r} is not a variant of {place_id!r}")

        for prop_id in scene["props"]:
            if prop_id not in prop_ids:
                errors.append(f"$.scenes[{sid}].props: {prop_id!r} is not in the story's props")

        for cue in scene["sfx_cues"]:
            if cue["cue"] not in sfx_cues:
                errors.append(f"$.scenes[{sid}].sfx_cues: {cue['cue']!r} is not one of the story's sfx cues")

        for line in scene["lines"]:
            if line["speaker"] == "narrator" and not narrator_enabled:
                errors.append(f"$.scenes[{sid}].lines[{line['line_id']}]: 'narrator' is not enabled for this story")

    if len(used_places) > max_places:
        errors.append(f"$.scenes: {len(used_places)} distinct place(s) used, more than max_places ({max_places})")

    return errors


# --------------------------------------------------------------- storyboard_v1 (spec 2.8)

STORYBOARD_SCHEMA_NAME = "storyboard_v1"

# @char_x / #place_y:variant / %prop_z -- reuses the entity id patterns' body.
SUBJECT_TAG_PATTERN = r"^(@char_[a-z0-9_]+|#place_[a-z0-9_]+:[a-z][a-z0-9_]*|%prop_[a-z0-9_]+)$"
# A relative path: no leading "/", no ".." path segment, no backslash.
REFERENCE_IMAGE_PATH_PATTERN = r"^(?!/)(?!.*\\)(?!\.\.(?:/|$))(?!.*/\.\.(?:/|$)).+$"

_STORYBOARD_MOTION_SCHEMA = _document({
    "type": {"type": "string", "enum": list(CAMERA_MOTIONS)},
    "zoom_from": {"type": "number", "minimum": 0.5, "maximum": 2},
    "zoom_to": {"type": "number", "minimum": 0.5, "maximum": 2},
    "pan": {"type": "string", "enum": ["none", "lr", "rl", "ud", "du"]},
})

# A shot's image (phase 4, DEC-155): assets/shots/shot_NN.<ext> in the
# episode's folder, NN the shot's own number (sh03 -> shot_03, sh112 ->
# shot_112: a stable id, never the shot's position). The only names
# store.EPISODE_ASSET_NAME_PATTERNS["shots"] holds.
SHOT_IMAGE_DIR = "assets/shots"
SHOT_IMAGE_NAME_PATTERN = r"^shot_(0[1-9]|[1-9][0-9]{1,2})\.(png|jpg|jpeg|webp)$"
# A shot's clip (phase 6 stage 7, tier >= 2): assets/clips/shot_NN.mp4, named
# the image's way -- and (DEC-258) its lip-synced take beside it,
# shot_NN.lipsync.mp4. The only names store.EPISODE_ASSET_NAME_PATTERNS["clips"]
# holds.
SHOT_CLIP_DIR = "assets/clips"
SHOT_CLIP_NAME_PATTERN = r"^shot_(0[1-9]|[1-9][0-9]{1,2})(\.lipsync|\.manual|\.stock)?\.mp4$"
SHOT_LIPSYNC_SUFFIX = ".lipsync"
# Plan 22 stage 5: a clip the human uploaded (``manual/upload``) is kept as
# shot_NN.manual.mp4, named only while its clip record's link is that one.
SHOT_MANUAL_SUFFIX = ".manual"
# Plan 23 stage B8: a stock cutaway's clip (route "stock") is kept as shot_NN.stock.mp4.
SHOT_STOCK_SUFFIX = ".stock"
STOCK_ROUTE = "stock"
MANUAL_LINK = "manual/upload"
# How an image was paid for: a free API link, a local engine, a paid link.
IMAGE_ROUTES = ("free", "local", "paid", "stock")
# A full sha256, hex (a prompt hash, a cache key, a file's digest).
SHA256_PATTERN = r"^[0-9a-f]{64}$"
# The regenerate note's own bound (web/api/models.py, StoryRegenerateRequest).
REGENERATE_NOTE_MAX = 300

_SHA256 = {"type": "string", "pattern": SHA256_PATTERN}
_NOTE_OR_NULL = {"type": ["string", "null"], "maxLength": REGENERATE_NOTE_MAX}

# A regenerate's fresh seed and note, persisted before the call so a retry
# asks for the same image (DEC-124, DEC-154); null once it is answered.
_STORYBOARD_PENDING_SCHEMA = _or_null(_document({
    "seed": {"type": "integer", "minimum": 0},
    "note": _NOTE_OR_NULL,
    "requested_at": _NON_EMPTY_STRING,
}))

# Phase 6 stage 7: a shot's clip (tier >= 2), recorded next to its image in
# the shot's ``assets`` (DEC-155) -- the video phase (stage 8) writes it with
# the storyboard's approval kept, as it writes the image. ``state``: current
# (made from the shot's image and video prompt as they are, on the episode's
# video link), stale, or failed (``reason`` says why). The file is
# ``assets.video`` (SHOT_CLIP_DIR/shot_NN.mp4), set only while the clip is
# current: the renderer reads it (render/plan.py). ``link`` is a chain link's
# label (EPISODE_LINK_PATTERN's rule); ``route`` how it is paid for.
CLIP_STATES = ("current", "stale", "failed")
CLIP_ROUTES = ("local", "paid", "manual", "stock")
# DEC-258: the clip's lipsync, when its story lipsyncs (media_policy.lipsync)
# and the shot holds a line spoken by a character in its frame. ``current``:
# ``assets.video`` is the lip-synced take (SHOT_CLIP_DIR/shot_NN.lipsync.mp4),
# made from the clip whose sha256 is ``clip_sha256`` and the dialogue track
# whose inputs hash to ``track_hash`` (its bytes: ``audio_sha256``) on
# ``link``; ``failed``: the plain clip stays ``assets.video`` and ``reason``
# says why; ``no_face``: the link found no face in that clip (Kling's
# ``face_detection_error``) -- final for the clip whose sha256 is
# ``clip_sha256`` on ``link``, never sent again (a new clip clears it), the
# plain clip the take, its booking released. Optional on the clip: every clip
# recorded before it validates.
LIPSYNC_STATES = ("current", "failed", "no_face")
_STORYBOARD_LIPSYNC_SCHEMA = _or_null(_document({
    "state": {"type": "string", "enum": list(LIPSYNC_STATES)},
    "link": {"type": "string", "maxLength": 160, "pattern": r"^[a-z][a-z0-9_-]*/[^\s,*]+$"},
    "clip_sha256": _SHA256,
    "track_hash": _SHA256,
    "audio_sha256": {"type": ["string", "null"], "pattern": SHA256_PATTERN},
    "cache_key": {"type": ["string", "null"], "pattern": SHA256_PATTERN},
    "est_usd": {"type": "number", "minimum": 0},
    "generated_at": _TIMESTAMP_OR_NULL,
}, optional={
    # The lines the track carries (in-frame speakers only) and the seconds billed.
    "lines": {"type": "array", "items": {"type": "string", "pattern": r"^l[0-9]{2}$"}, "maxItems": 60},
    "billed_s": {"type": "integer", "minimum": 0},
    "reason": {"type": ["string", "null"], "maxLength": 1000},
}))
# Plan 22: a native-speech story's take of a speaking clip (the native take,
# ``assets._Assets.native_take_shot``): the clip's own sound transcribed and
# aligned against its line. ``state`` ok (heard, timed), mismatch (the words
# heard are not enough of the line, or its last word runs to the clip's
# end), no_speech, or stt_unavailable (no STT link could run: the planned
# window, approximate). ``clip_sha256`` names the clip it is the take of (a
# new clip makes it stale); ``start_s``/``end_s`` the speech inside the clip
# (the line's audio); ``clip_real_s`` the clip's own length. Optional on the
# clip: every clip recorded before validates.
NATIVE_TAKE_STATES = ("ok", "mismatch", "no_speech", "stt_unavailable")
_STORYBOARD_NATIVE_TAKE_SCHEMA = _or_null(_document({
    "state": {"type": "string", "enum": list(NATIVE_TAKE_STATES)},
    "matched": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
    "heard": {"type": ["string", "null"], "maxLength": 400},
    "start_s": {"type": ["number", "null"], "minimum": 0},
    "end_s": {"type": ["number", "null"], "minimum": 0},
    "aligned_by": {"type": ["string", "null"], "maxLength": 120},
    "clip_sha256": _SHA256,
    "clip_real_s": {"type": "number", "minimum": 0},
    "line_id": {"type": "string", "pattern": r"^l[0-9]{2}$"},
    "checked_at": _NON_EMPTY_STRING,
}, optional={
    "reason": {"type": ["string", "null"], "maxLength": 1000},
    # Plan 27 stage 4: an exchange's take, line by line, in the shot's order (a one-line take has none).
    "lines": {"type": "array", "minItems": 2, "maxItems": 4, "items": _document({
        "line_id": {"type": "string", "pattern": r"^l[0-9]{2}$"},
        "speaker": {"type": "string", "minLength": 1, "maxLength": 80},
        "matched": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "heard": {"type": ["string", "null"], "maxLength": 400},
        "start_s": {"type": ["number", "null"], "minimum": 0},
        "end_s": {"type": ["number", "null"], "minimum": 0},
    })},
}))
_STORYBOARD_CLIP_SCHEMA = _or_null(_document({
    "state": {"type": "string", "enum": list(CLIP_STATES)},
    "link": {"type": "string", "maxLength": 160, "pattern": r"^[a-z][a-z0-9_-]*/[^\s,*]+$"},
    "route": {"type": "string", "enum": list(CLIP_ROUTES)},
    # The length bought (a whole second of the link's or template's table).
    "clip_s": {"type": "integer", "minimum": 1},
    "est_usd": {"type": "number", "minimum": 0},
    # sha256 of the video prompt it was asked with, and of the keyframe sent.
    "prompt_hash": _SHA256,
    "image_sha256": _SHA256,
    "cache_key": {"type": ["string", "null"], "pattern": SHA256_PATTERN},
    "generated_at": _TIMESTAMP_OR_NULL,
}, optional={
    # A re-animate's note, and its fresh seed persisted before the call (DEC-154).
    "note": _NOTE_OR_NULL,
    "pending": _STORYBOARD_PENDING_SCHEMA,
    "reason": {"type": ["string", "null"], "maxLength": 1000},
    # How the render covers a shot longer than the clip (DEC-250): slowed to the shot's
    # length (``stretch``); absent, held on its last frame (DEC-208).
    "cover": {"type": ["string", "null"], "enum": ["stretch", None]},
    # DEC-258: the clip's lipsync (_STORYBOARD_LIPSYNC_SCHEMA).
    "lipsync": _STORYBOARD_LIPSYNC_SCHEMA,
    # Plan 22: a speaking clip's take (_STORYBOARD_NATIVE_TAKE_SCHEMA).
    "native_speech": _STORYBOARD_NATIVE_TAKE_SCHEMA,
    # Plan 22: how many times the clip was bought again for its take (a retake).
    "retakes": {"type": "integer", "minimum": 0},
    # Plan 22 stage 5: a clip the human uploaded (link manual/upload): its
    # file's sha256, when it landed, its real length and the name it was sent as.
    "sha256": _SHA256,
    "uploaded_at": _NON_EMPTY_STRING,
    "duration_s": {"type": "number", "minimum": 0},
    "filename": {"type": "string", "maxLength": 200},
    # Plan 23 stage B8: a stock clip's credit record (route stock, link stock/<provider>):
    # who made it and under what licence (clipping/stock/credits.credit_record).
    "source": {"type": "object"},
    # Plan 28 F7: the first frame of the human's clip against its keyframe (judge.check_first_frame), a
    # warning on the Handoff card, never a refusal.
    "first_frame": _document({
        "version": {"type": "integer", "minimum": 1},
        "passed": {"type": "boolean"},
        "issues": {"type": "array", "items": _NON_EMPTY_STRING, "maxItems": 3},
        "checked_at": _NON_EMPTY_STRING,
        "link": _NON_EMPTY_STRING,
        "keyframe_sha256": _SHA256,
    }),
}))

# The five keys of spec 2.8 stay required; phase 4's record of the image is
# optional, so a phase-3 board (the five alone) validates unchanged. Closed.
_STORYBOARD_ASSETS_SCHEMA = _document({
    "image": {"type": ["string", "null"]},
    "video": {"type": ["string", "null"]},
    "seed": {"type": ["integer", "null"]},
    "provider": {"type": ["string", "null"]},
    "approved": {"type": "boolean"},
}, optional={
    "model": {"type": ["string", "null"], "maxLength": 120},
    # How the image was actually made (spec 8.1), which the grid labels.
    "consistency": {"type": "string", "enum": list(_DERIVED)},
    "route": {"type": "string", "enum": list(IMAGE_ROUTES)},
    # sha256 of what the image was made from; a different one makes it stale.
    "prompt_hash": _SHA256,
    "locked": {"type": "boolean"},
    "note": _NOTE_OR_NULL,
    "generated_at": _TIMESTAMP_OR_NULL,
    "est_usd": {"type": "number", "minimum": 0},
    # Plan 23 stage B8: where a stock keyframe came from, "stock/<provider>" (route stock).
    "source": {"type": "string", "maxLength": 160},
    # The generation cache's key for the request (clipping/providers/gencache.py).
    "cache_key": {"type": ["string", "null"], "pattern": SHA256_PATTERN},
    "pending": _STORYBOARD_PENDING_SCHEMA,
    # Phase 6 stage 7: the shot's clip (_STORYBOARD_CLIP_SCHEMA).
    "clip": _STORYBOARD_CLIP_SCHEMA,
    # Phase 8 stage B: the previous keyframe of the scene its image was asked
    # with (a v2 shot's continuity reference): that shot and the sha256 of the
    # image sent; null when none was. A record only -- never in prompt_hash, so
    # a redrawn previous keyframe never makes this image stale.
    "continuity": _or_null(_document({
        "shot_id": {"type": "string", "pattern": SHOT_ID_PATTERN},
        "image_sha256": _SHA256,
    })),
})

# The one prompt layout a storyboard shot names (phase 7 stage 3b).
STORYBOARD_PROMPT_LAYOUT_V1 = "layered_v1"
# Plan 22: the one timing mode a storyboard names (native_speech.TIMING_MODE).
STORYBOARD_TIMING_NATIVE = "native_speech"

# Where a T1 v2 shot's subject stands (phase 7 stage 4).
STAGING_POSITIONS = ("left", "centre", "right", "back")
STORYBOARD_STAGING_SCHEMA = _document({
    "subject": {"type": "string", "pattern": SUBJECT_TAG_PATTERN},
    "position": {"type": "string", "enum": list(STAGING_POSITIONS)},
    "facing": {"type": "string", "maxLength": 120},
    "expression": {"type": "string", "maxLength": 120},
})

_STORYBOARD_SHOT_SCHEMA = _document({
    "shot_id": {"type": "string", "pattern": SHOT_ID_PATTERN},
    "scene_id": {"type": "string", "pattern": SCENE_ID_PATTERN},
    "order": {"type": "integer", "minimum": 1},
    "framing": {"type": "string", "enum": list(FRAMINGS)},
    "camera_motion": {"type": "string", "enum": list(CAMERA_MOTIONS)},
    "modifiers": {"type": "array", "items": {"type": "string", "enum": list(MODIFIERS)}},
    "subject_tags": {"type": "array", "items": {"type": "string", "pattern": SUBJECT_TAG_PATTERN}, "maxItems": 8},
    "action": _text(400),
    "lines": {"type": "array", "items": {"type": "string", "pattern": LINE_ID_PATTERN}},
    "image_prompt": {"type": "string"},
    "negative_prompt": {"type": "string"},
    "prompt_override": {"type": ["string", "null"]},
    # At most 8 on a legacy shot (shots._reference_images), 10 on a v2 one
    # (shots.V2_MAX_REFERENCES, phase 7 stage 3b): a relaxed maximum.
    "reference_images": {
        "type": "array", "items": {"type": "string", "pattern": REFERENCE_IMAGE_PATH_PATTERN}, "maxItems": 10,
    },
    "consistency": {"type": "string", "enum": list(_DERIVED)},
    "duration_s": {"type": "number", "minimum": 0},
    "keep_still": {"type": "boolean"},
    "motion": _STORYBOARD_MOTION_SCHEMA,
    "video_prompt": {"type": ["string", "null"]},
    "assets": _STORYBOARD_ASSETS_SCHEMA,
}, optional={
    # Phase 7 stage 1 (D1): the shot's action with every @char/#place/%prop
    # tag resolved AND every entity name swept (shots.resolve_shot), stored
    # so video_plan.build_video_prompt never puts a raw tag or a leaked name
    # in the I2V clip prompt. Absent on a storyboard built before this key
    # existed, which keeps -- and its clip prompt still builds from -- its
    # raw `action` (video_plan.build_video_prompt falls back to it), so an
    # already-stored clip stays "current" until the storyboard is refreshed.
    "video_action": {"type": "string"},
    # Phase 7 stage 3b (A8): how image_prompt was written -- "layered_v1" on a
    # v2 story's shot (shots.resolve_shot), whose video_prompt then holds its
    # clip prompt and whose references are sent up to the link's own limit
    # (steps/assets.REFERENCE_LIMITS). Absent on every legacy shot.
    "prompt_layout": {"type": "string", "enum": [STORYBOARD_PROMPT_LAYOUT_V1]},
    # Phase 7 stage 4 (DEC-227): T1 v2's ``motion`` -- what the characters do
    # during the clip, in tags like ``action`` (the clip prompt's action,
    # shots._layered) -- and its ``staging`` (where each subject stands, faces
    # and what it shows: the keyframe's positions). Absent on every shot T1 v2
    # did not plan; the existing ``motion`` key is the Tier-1 camera motion.
    "clip_motion": _text(400),
    "staging": {"type": "array", "items": STORYBOARD_STAGING_SCHEMA, "maxItems": 4},
    # Plan 22 (a native-speech board, ``timing_mode: native_speech``): whether
    # the shot's clip speaks its one line on camera, and the clip length it
    # was planned at (a length its link sells). Absent on every other shot.
    "speaks": {"type": "boolean"},
    "clip_s": {"type": "integer", "minimum": 1},
    # Plan 27 stage 3: a speaking shot that carries an exchange of several
    # lines (``shots.speech_shot_plan``): its speakers, in the order they
    # first speak. Absent on every shot of one line.
    "speakers": {"type": "array", "items": {"type": "string", "pattern": SPEAKER_PATTERN}, "minItems": 1,
                 "maxItems": 4},
    # Plan 23 stage D5: the appearance variant each framed character wears in this shot
    # ({char_id: variant_id}): inherited from its scene's ``states`` when the shot is
    # planned, the human's own once overridden (kept by a re-plan, like the shot's
    # assets). Absent on every other shot: each character in its base look.
    "variants": {"type": "object"},
})

_STORYBOARD_TRANSITION_SCHEMA = _document({
    "after": {"type": "string", "pattern": SHOT_ID_PATTERN},
    "type": {"type": "string", "enum": list(TRANSITIONS)},
    "duration_s": {"type": "number", "minimum": 0, "maximum": 1},
})

_STORYBOARD_SCENE_ENTRY_SCHEMA = _document({
    "source": {"type": "string", "enum": ["t1", "fast"]},
    "script_rev": {"type": "integer", "minimum": 1},
    "stale": {"type": "boolean"},
}, optional={
    # Phase 5 stage 7: a text-only edit of the scene's lines kept its shot
    # plan (DEC-129 as amended): its shots are re-timed in place once its
    # lines are measured again (shots.retime_storyboard clears it). Absent
    # on every scene no such edit touched.
    "retime_only": {"type": "boolean"},
})

STORYBOARD_SCHEMA = _document({
    "$schema": {"type": "string", "const": STORYBOARD_SCHEMA_NAME},
    "ep": {"type": "integer", "minimum": 1, "maximum": 99},
    "shots": {"type": "array", "items": _STORYBOARD_SHOT_SCHEMA, "maxItems": 60},
    "transitions": {"type": "array", "items": _STORYBOARD_TRANSITION_SCHEMA},
    # keyed by scene id -> _STORYBOARD_SCENE_ENTRY_SCHEMA, checked in storyboard_errors.
    "scenes": {"type": "object"},
    # entity id -> ISO timestamp, when each resolved entity was last read.
    "resolved_from": {"type": "object"},
    "approved_at": {"type": ["string", "null"]},
    "rev": {"type": "integer", "minimum": 1},
    "created_at": _NON_EMPTY_STRING,
    "updated_at": _NON_EMPTY_STRING,
}, optional={
    # Phase 5 stage 6: the shots are timed in whole frames
    # (timing.board_whole_frames). Absent on a storyboard timed before, which
    # keeps -- and renders in -- its old timing until a full re-time.
    "whole_frames": {"type": "boolean"},
    # Plan 22: a native-speech story's board (shots.speech_shot_plan): each
    # shot lasts its clip, each character line is spoken by its own shot's
    # clip (timing.native_pass). Absent on every other board.
    "timing_mode": {"type": "string", "enum": [STORYBOARD_TIMING_NATIVE]},
})


def storyboard_errors(doc, *, min_shot_s=0.8) -> list:
    """``validate()`` against ``STORYBOARD_SCHEMA``, plus the cross-field
    checks the subset schema cannot express (spec 2.8, 6.4): unique shot ids
    (stable keys, in any order: walk follow-up F5) and order sequencing
    (``order`` is the shot's position), scene references and contiguity, line references, transition
    references, a non-cut transition sitting only on a scene boundary (spec
    6.3: ``cut`` inside a scene), the per-shot minimum length once timed (a shot with a current clip is
    exempt: it keeps the length it was made at, plan 27), the
    motion type matching the shot's own camera motion, and a shot's image
    being its own file (``SHOT_IMAGE_DIR``/``shot_NN.<ext>``), and its clip
    (phase 6 stage 7) its own ``SHOT_CLIP_DIR``/``shot_NN.mp4``, named only
    while its ``assets.clip`` is current."""
    errors = validate(doc, STORYBOARD_SCHEMA)
    if errors:
        return errors

    errors = []
    shots = doc["shots"]
    scenes = doc["scenes"]

    for key, entry in scenes.items():
        if not (isinstance(key, str) and _search(SCENE_ID_PATTERN, key)):
            errors.append(f"$.scenes: {key!r} is not a scene id")
            continue
        errors.extend(validate(entry, _STORYBOARD_SCENE_ENTRY_SCHEMA, f"$.scenes.{key}"))

    shot_ids = [shot["shot_id"] for shot in shots]
    first_index = {}
    for i, shot_id in enumerate(shot_ids):
        if shot_id in first_index:
            errors.append(f"$.shots[{i}].shot_id: {shot_id!r} is not unique (shot {first_index[shot_id] + 1} "
                          "has it too)")
        else:
            first_index[shot_id] = i

    for i, shot in enumerate(shots):
        if shot["order"] != i + 1:
            errors.append(f"$.shots[{i}].order: {shot['order']}, expected {i + 1}")
        if shot["scene_id"] not in scenes:
            errors.append(f"$.shots[{i}].scene_id: {shot['scene_id']!r} is not a key of scenes")
        if shot["motion"]["type"] != shot["camera_motion"]:
            errors.append(
                f"$.shots[{i}].motion.type: {shot['motion']['type']!r} does not match "
                f"camera_motion {shot['camera_motion']!r}"
            )
        duration = shot["duration_s"]
        # Plan 27: a shot made under an older, lower floor keeps the length its clip gave it
        # (a current clip's shot is grandfathered; the floor binds the shots still to be made).
        made = ((shot["assets"].get("clip") or {}).get("state") == "current")
        if duration != 0 and duration < min_shot_s and not made:
            errors.append(f"$.shots[{i}].duration_s: {duration} < the minimum shot length {min_shot_s}")
        image = shot["assets"]["image"]
        if image is not None:
            folder, _, name = image.rpartition("/")
            if (folder != SHOT_IMAGE_DIR or _search(SHOT_IMAGE_NAME_PATTERN, name) is None
                    or not name.startswith(f"shot_{shot['shot_id'][2:]}.")):
                errors.append(
                    f"$.shots[{i}].assets.image: {image!r} is not {shot['shot_id']}'s image "
                    f"({SHOT_IMAGE_DIR}/shot_NN.<png|jpg|jpeg|webp>)"
                )
        video = shot["assets"]["video"]
        if video is not None:
            folder, _, name = video.rpartition("/")
            plain = f"shot_{shot['shot_id'][2:]}.mp4"
            synced = f"shot_{shot['shot_id'][2:]}{SHOT_LIPSYNC_SUFFIX}.mp4"
            manual = f"shot_{shot['shot_id'][2:]}{SHOT_MANUAL_SUFFIX}.mp4"
            stock = f"shot_{shot['shot_id'][2:]}{SHOT_STOCK_SUFFIX}.mp4"
            clip = shot["assets"].get("clip") or {}
            if (folder != SHOT_CLIP_DIR or _search(SHOT_CLIP_NAME_PATTERN, name) is None
                    or name not in (plain, synced, manual, stock)):
                errors.append(
                    f"$.shots[{i}].assets.video: {video!r} is not {shot['shot_id']}'s clip "
                    f"({SHOT_CLIP_DIR}/shot_NN.mp4)"
                )
            elif clip.get("state") != "current":
                errors.append(f"$.shots[{i}].assets.video: set only with a current clip (assets.clip.state "
                              "'current')")
            elif name == synced and (clip.get("lipsync") or {}).get("state") != "current":
                errors.append(f"$.shots[{i}].assets.video: the lip-synced take is set only with a current lipsync "
                              "(assets.clip.lipsync.state 'current')")
            elif name == manual and clip.get("link") != MANUAL_LINK:
                errors.append(f"$.shots[{i}].assets.video: an uploaded clip is set only with a clip on {MANUAL_LINK} "
                              f"(assets.clip.link {MANUAL_LINK!r})")
            elif name == stock and clip.get("route") != STOCK_ROUTE:
                errors.append(f"$.shots[{i}].assets.video: a stock clip is set only with a stock clip record "
                              f"(assets.clip.route {STOCK_ROUTE!r})")

    for i, shot in enumerate(shots):
        if "variants" in shot:
            framed = {tag[1:] for tag in shot["subject_tags"] if tag.startswith("@")}
            _variant_map_errors(errors, f"$.shots[{i}].variants", shot["variants"], framed)

    seen_scenes = []
    for shot in shots:
        sid = shot["scene_id"]
        if not seen_scenes or seen_scenes[-1] != sid:
            if sid in seen_scenes:
                errors.append(f"$.shots: scene {sid!r}'s shots are not contiguous")
            seen_scenes.append(sid)

    line_to_shot = {}
    last_line_number = None
    for shot in shots:
        for line_id in shot["lines"]:
            if line_id in line_to_shot:
                errors.append(
                    f"$.shots: line {line_id!r} appears in more than one shot "
                    f"({line_to_shot[line_id]!r} and {shot['shot_id']!r})"
                )
            else:
                line_to_shot[line_id] = shot["shot_id"]
            n = int(line_id[1:])
            if last_line_number is not None and n <= last_line_number:
                errors.append(f"$.shots: line {line_id!r} does not increase after 'l{last_line_number:02d}'")
            last_line_number = n

    last_shot_id = shot_ids[-1] if shot_ids else None
    id_to_index = {shot_id: i for i, shot_id in enumerate(shot_ids)}
    seen_after = set()
    for i, transition in enumerate(doc["transitions"]):
        after = transition["after"]
        if after not in shot_ids:
            errors.append(f"$.transitions[{i}].after: {after!r} is not an existing shot")
        elif after == last_shot_id:
            errors.append(f"$.transitions[{i}].after: {after!r} is the last shot; it cannot have a transition")
        elif transition["type"] != "cut":
            idx = id_to_index[after]
            if shots[idx]["scene_id"] == shots[idx + 1]["scene_id"]:
                errors.append(
                    f"$.transitions[{i}].type: {transition['type']!r} is not 'cut', but shot {after!r} and "
                    f"the next shot are in the same scene (spec 6.3: only cut inside a scene)"
                )
        if after in seen_after:
            errors.append(f"$.transitions[{i}].after: {after!r} already has a transition")
        seen_after.add(after)

    return errors


def storyboard_context_errors(doc, script, *, shots_per_scene) -> list:
    """Cross-checks against the episode script (pure, no schema validation):
    every storyboard scene exists in the script, each scene's shot count is
    within *shots_per_scene* (a [lo, hi] pair), every shot's line ids belong
    to its scene, and every subject tag names only that scene's own
    characters, place (with its time variant) and props."""
    errors = []
    script_scenes = {scene["scene_id"]: scene for scene in script["scenes"]}
    lo, hi = shots_per_scene

    for scene_id in doc["scenes"]:
        if scene_id not in script_scenes:
            errors.append(f"$.scenes.{scene_id}: not a scene of the script")

    shots_by_scene: dict = {}
    for shot in doc["shots"]:
        shots_by_scene.setdefault(shot["scene_id"], []).append(shot)

    for scene_id, scene_shots in shots_by_scene.items():
        if scene_id not in script_scenes:
            continue
        count = len(scene_shots)
        if not (lo <= count <= hi):
            errors.append(f"$.shots: scene {scene_id!r} has {count} shot(s), expected {lo}-{hi}")

    for shot in doc["shots"]:
        scene = script_scenes.get(shot["scene_id"])
        if scene is None:
            continue

        scene_line_ids = {line["line_id"] for line in scene["lines"]}
        for line_id in shot["lines"]:
            if line_id not in scene_line_ids:
                errors.append(
                    f"$.shots[{shot['shot_id']}].lines: {line_id!r} does not belong to scene {shot['scene_id']!r}"
                )

        allowed_tags = {f"@{char_id}" for char_id in scene["characters"]}
        allowed_tags.add(f"#{scene['place_id']}:{scene['time_variant']}")
        allowed_tags |= {f"%{prop_id}" for prop_id in scene["props"]}
        for tag in shot["subject_tags"]:
            if tag not in allowed_tags:
                errors.append(
                    f"$.shots[{shot['shot_id']}].subject_tags: {tag!r} is not in the scene's "
                    f"characters, place or props"
                )

    return errors


# ================================================================ phase 4 documents
#
# The phase-4 plan's "Documents": what the assets, render and metadata steps
# keep in episodes/epNN/ besides the storyboard's per-shot image record. Each
# carries ``ep`` (checked against its folder by the store) and both
# timestamps; every fixed object is closed.

# A relative path: the storyboard's reference-image rule (no leading "/", no
# ".." segment, no backslash), bounded.
_RELATIVE_PATH = {"type": "string", "minLength": 1, "maxLength": 300, "pattern": REFERENCE_IMAGE_PATH_PATTERN}
_EP = {"type": "integer", "minimum": 1, "maximum": 99}


def _finite_errors(errors, path, values) -> None:
    """``validate()`` lets NaN and infinity through as numbers (json writes
    and reads them); a measurement must be a real one."""
    for key, value in values.items():
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(f"{path}.{key}: {value!r} is not a finite number")


# -------------------------------------------------------- episode_assets_v1 (DEC-155)

EPISODE_ASSETS_SCHEMA_NAME = "episode_assets_v1"

# Where a line's word timings came from, in spec 6.4's order of truth: the
# TTS provider's own, forced alignment through the STT chain (opt-in), or an
# even split labelled "approximate timing".
WORD_SOURCES = ("provider", "alignment", "even_split")
SFX_STATES = ("resolved", "missing")

# A voice regenerate's take (``secrets.token_hex(8)``): a field of the
# generation cache's key, so a new take misses it on purpose (phase 4).
LINE_TAKE_PATTERN = r"^[0-9a-f]{16}$"
_LINE_TAKE = {"type": "string", "pattern": LINE_TAKE_PATTERN}

# ``words_source`` is required but on an entry holding a pending take alone
# (a line never voiced yet, a regenerate asked): episode_assets_errors.
_EPISODE_ASSETS_LINE_SCHEMA = _document({}, optional={
    "words_source": {"type": "string", "enum": list(WORD_SOURCES)},
    # The STT link that aligned the words; only with "alignment".
    "aligned_by": _text(120),
    # Phase 5 stage 7: the voice regenerate take the line's audio was spoken
    # with, the note it was directed with, and that audio's sha256 (a record
    # of another file no longer describes the line: dropped).
    "take": _document({"id": _LINE_TAKE, "note": _NOTE_OR_NULL, "audio_sha256": _SHA256}),
    # A voice regenerate asked and not answered yet, persisted before the
    # call: a retry with the same note asks the same take, so a request the
    # provider holds is resumed, never bought twice (DEC-154's rule).
    "pending": _document({"take": _LINE_TAKE, "note": _NOTE_OR_NULL, "requested_at": _NON_EMPTY_STRING}),
})

_EPISODE_ASSETS_SFX_SCHEMA = _document({
    "scene_id": {"type": "string", "pattern": SCENE_ID_PATTERN},
    "at": {"type": "string", "pattern": SFX_AT_PATTERN},
    "cue": {"type": "string", "pattern": _ID_PATTERN},
    "pack": {"type": "string", "enum": list(SFX_PACKS)},
    # The shipped file the cue resolved to; null when it is missing.
    "file": _or_null(_RELATIVE_PATH),
    # On the episode's timeline: the scene's start, or its line's.
    "offset_s": {"type": "number", "minimum": 0},
    "state": {"type": "string", "enum": list(SFX_STATES)},
})

# Null until the step resolves it. file/sha256/licence are null together
# when no track carries the mood.
_EPISODE_ASSETS_BGM_SCHEMA = _or_null(_document({
    "mood": {"type": "string", "pattern": _ID_PATTERN},
    "dominant_emotion": {"type": "string", "enum": list(EMOTIONS)},
    # emotion -> its scenes' summed seconds, checked in episode_assets_errors.
    "weights_s": {"type": "object"},
    "file": _or_null(_RELATIVE_PATH),
    "sha256": _or_null(_SHA256),
    "licence": {"type": ["string", "null"], "minLength": 1, "maxLength": 200},
}))

# Who recorded an approval of the episode's assets or keyframes (phase 7
# follow-up, stage C): the human, or the fast track's one click, which
# approves them on the human's behalf (the review screen says which).
APPROVED_BY = ("user", "fast_track")
_APPROVED_BY_SCHEMA = {"type": "string", "enum": list(APPROVED_BY)}

# The grid approval, and the fingerprint of what it approved: once the
# current fingerprint differs, the approval is stale (derived, never cleared).
_EPISODE_ASSETS_APPROVED_SCHEMA = _or_null(_document({
    "at": _NON_EMPTY_STRING,
    "fingerprint": _SHA256,
}, optional={
    # Stage C: absent on an approval recorded before it (the human's).
    "by": _APPROVED_BY_SCHEMA,
}))

# Phase 6 stage 6 (A-087): the one link an episode's shot images are made on
# (``steps/sticky_link.py``): the chain link's label, since when, and the link
# it replaced when the user switched to it. ``video`` is the clips' slot
# (stage 8). Optional: a document written before it validates unchanged.
EPISODE_LINK_PATTERN = r"^[a-z][a-z0-9_-]*/[^\s,*]+$"
_EPISODE_LINK_LABEL = {"type": "string", "maxLength": 160, "pattern": EPISODE_LINK_PATTERN}
_EPISODE_LINK_SCHEMA = _document({
    "link": _EPISODE_LINK_LABEL,
    "since": _NON_EMPTY_STRING,
}, optional={
    "switched_from": _EPISODE_LINK_LABEL,
})
# Plan 22: ``video_speech`` -- a native-speech episode's speaking clips' link,
# sticky on its own beside ``video`` (its silent clips').
_EPISODE_LINKS_SCHEMA = _document({}, optional={"image": _EPISODE_LINK_SCHEMA, "video": _EPISODE_LINK_SCHEMA,
                                                "video_speech": _EPISODE_LINK_SCHEMA})

# Phase 6 stage 7: the user's per-shot overrides of what the clips do
# (``workflow.patch_assets``): ``keep_still`` over the storyboard's own,
# ``animate`` (a pin: animated first, even past the cap) and
# ``keep_native_audio`` (tier 3). Kept here, not on the storyboard, so
# choosing which shots move never clears the storyboard's approval
# (``video_plan.effective_shot_flags`` resolves them). Keyed by shot id, an
# entry naming at least one; optional: a document without it validates as
# before.
SHOT_OVERRIDE_FLAGS = ("keep_still", "animate", "keep_native_audio")
_EPISODE_ASSETS_SHOT_SCHEMA = _document({}, optional={flag: {"type": "boolean"} for flag in SHOT_OVERRIDE_FLAGS})

# Plan 25 stage 1 (D-1, DEC-301): who makes a shot's clip and its keyframe --
# ``auto`` (the app, on an API link) or ``manual`` (the human's own upload,
# ``manual/upload``) -- over the story's own choice (its budget profile, its
# ``images`` switch). Kept beside the overrides above, keyed by shot id, an
# entry naming at least one kind; optional: absent, every shot is made as the
# story's profile says, as before.
SHOT_MODE_KINDS = ("clip", "image")
SHOT_MODES = ("auto", "manual")
_EPISODE_ASSETS_SHOT_MODE_SCHEMA = _document({}, optional={
    kind: {"type": "string", "enum": list(SHOT_MODES)} for kind in SHOT_MODE_KINDS})

# Plan 25 stage 2 (D-2): where the human makes the episode's clips -- the
# platform (``platforms.PLATFORMS``) and, optionally, the model on it -- which
# ``GET .../handoff`` reads when its query names none. Optional on the
# document; never part of the assets approval.
HANDOFF_PLATFORMS = platforms.PLATFORMS
_EPISODE_ASSETS_HANDOFF_SCHEMA = _document(
    {"platform": {"type": "string", "enum": list(HANDOFF_PLATFORMS)}},
    optional={"model": {"type": "string", "minLength": 1, "maxLength": 80}})

# Phase 7 stage 6b (A16, DEC-230): a v2 episode's keyframe judge (J2), one
# vision verdict per shot, keyed by shot id: does the keyframe show the
# shot's beat, what it misses, and what changed from the previous shot's
# keyframe that should not have. A verdict is current while the shot's
# keyframe (``image_sha256``) and the previous shot's (``previous_sha256``,
# null for the first shot) are the images it saw.
KEYFRAME_MISSING_MAX = 3
# Plan 28 F2: each character that does not match its sheet, in J2's words.
KEYFRAME_SHEET_ISSUES_MAX = 2
_EPISODE_ASSETS_KEYFRAME_VERDICT_SCHEMA = _document({
    "image_sha256": _SHA256,
    "previous_sha256": _or_null(_SHA256),
    "shows_beat": {"type": "boolean"},
    "missing": {"type": "array", "items": _text(120), "maxItems": KEYFRAME_MISSING_MAX},
    "continuity_issue": {"type": ["string", "null"], "minLength": 1, "maxLength": 300},
    "link": _text(160),
    "checked_at": _NON_EMPTY_STRING,
}, optional={
    # Phase 8 stage B: the J2 prompt version that judged it
    # (prompts.J2_PROMPT_VERSION); absent on a stage-6b verdict (version 1),
    # which stays readable and is asked again (judge.verdict_current).
    "prompt_version": {"type": "integer", "minimum": 1},
    # Plan 19 stage 3 (F3): the framing the keyframe has when it is not the
    # one asked; absent when J2 named none, and on every older verdict.
    "framing_issue": {"type": "string", "minLength": 1, "maxLength": 200},
    # Plan 28 F2: each on-screen character that does not match its sheet
    # and written look ("Gaston's head is a pear, the sheet shows a
    # pineapple"); absent when J2 named none, and on every older verdict.
    "sheet_issues": {"type": "array", "items": _text(300), "minItems": 1,
                     "maxItems": KEYFRAME_SHEET_ISSUES_MAX},
})

# Phase 8 stage B: what the assets step's keyframe auto-fix did -- per shot
# (keyed by shot id) how many times it redrew the keyframe, what that cost,
# whether it gave up (still flagged after its redraws), and the history of
# the keyframes it judged: the one J2 flagged first, then each redraw with
# the correction note it was asked with; and per episode the budget it runs
# under (the story's budget profile's ``keyframe_fix``) and what it spent.
# Both optional: absent until the auto-fix met a flagged keyframe.
KEYFRAME_FIX_HISTORY_MAX = 12
_KEYFRAME_FIX_HISTORY_SCHEMA = _document({
    "image_sha256": _SHA256,
    "passed": {"type": "boolean"},
    "issue": {"type": ["string", "null"], "minLength": 1, "maxLength": 1000},
    "note": _NOTE_OR_NULL,
    "at": _NON_EMPTY_STRING,
})
_EPISODE_ASSETS_KEYFRAME_FIX_SCHEMA = _document({
    "redraws": {"type": "integer", "minimum": 0},
    "spent_usd": {"type": "number", "minimum": 0},
    "gave_up": {"type": "boolean"},
    "history": {"type": "array", "items": _KEYFRAME_FIX_HISTORY_SCHEMA, "maxItems": KEYFRAME_FIX_HISTORY_MAX},
})
_EPISODE_ASSETS_KEYFRAME_FIX_BUDGET_SCHEMA = _document({
    "max_redraws_per_shot": {"type": "integer", "minimum": 0},
    "cap_usd": {"type": "number", "minimum": 0},
    "spent_usd": {"type": "number", "minimum": 0},
})

_EPISODE_ASSETS_SPEECH_RETAKES_SCHEMA = _document({
    "max_per_shot": {"type": "integer", "minimum": 0},
    "cap_usd": {"type": "number", "minimum": 0},
    "spent_usd": {"type": "number", "minimum": 0},
    # keyed by shot id -> the number of retakes bought, checked in episode_assets_errors.
    "shots": {"type": "object"},
})

# The keyframe approval (phase 7 stage 6b, DEC-230): when, whether it went
# over a failed or missing verdict ("anyway"), and the fingerprint of the
# keyframes it approved -- once the current fingerprint differs, the
# approval is stale (derived, never cleared: DEC-155's rule).
_EPISODE_ASSETS_KEYFRAMES_APPROVED_SCHEMA = _document({
    "at": _NON_EMPTY_STRING,
    "anyway": {"type": "boolean"},
    "fingerprint": _SHA256,
}, optional={
    # Stage C: who approved (the fast track's one click records its own),
    # and the shots it went over -- a failed or missing keyframe check (J2)
    # -- so the review can show them; absent on a stage-6b approval.
    "by": _APPROVED_BY_SCHEMA,
    "flagged": {"type": "array", "items": {"type": "string", "pattern": SHOT_ID_PATTERN}},
})

EPISODE_ASSETS_SCHEMA = _document({
    "$schema": {"type": "string", "const": EPISODE_ASSETS_SCHEMA_NAME},
    "ep": _EP,
    # keyed by line id -> _EPISODE_ASSETS_LINE_SCHEMA, checked in episode_assets_errors.
    "lines": {"type": "object"},
    "sfx": {"type": "array", "items": _EPISODE_ASSETS_SFX_SCHEMA},
    "bgm": _EPISODE_ASSETS_BGM_SCHEMA,
    "approved": _EPISODE_ASSETS_APPROVED_SCHEMA,
    "created_at": _NON_EMPTY_STRING,
    "updated_at": _NON_EMPTY_STRING,
}, optional={
    "links": _EPISODE_LINKS_SCHEMA,
    # keyed by shot id -> _EPISODE_ASSETS_SHOT_SCHEMA, checked in episode_assets_errors.
    "shots": {"type": "object"},
    # Phase 7 stage 6b (DEC-230), a v2 episode's own: keyed by shot id ->
    # _EPISODE_ASSETS_KEYFRAME_VERDICT_SCHEMA, checked in episode_assets_errors.
    "keyframe_verdicts": {"type": "object"},
    "keyframes_approved": _EPISODE_ASSETS_KEYFRAMES_APPROVED_SCHEMA,
    # Phase 8 stage B: keyed by shot id -> _EPISODE_ASSETS_KEYFRAME_FIX_SCHEMA,
    # checked in episode_assets_errors; and the episode's fix budget.
    "keyframe_fixes": {"type": "object"},
    "keyframe_fix_budget": _EPISODE_ASSETS_KEYFRAME_FIX_BUDGET_SCHEMA,
    # Plan 22: a native-speech episode's retakes (a speaking clip bought again
    # once for a take that missed its line): the profile's ``speech_retake``
    # budget, what it spent, and per shot (keyed by shot id) how many.
    "speech_retakes": _EPISODE_ASSETS_SPEECH_RETAKES_SCHEMA,
    # Plan 25 stage 1: keyed by shot id -> _EPISODE_ASSETS_SHOT_MODE_SCHEMA,
    # checked in episode_assets_errors.
    "shot_modes": {"type": "object"},
    # Plan 25 stage 2: the handoff's platform and model.
    "handoff": _EPISODE_ASSETS_HANDOFF_SCHEMA,
})
# The maps of assets.json keyed by shot id (above): the ids a storyboard
# re-plan never gives a new shot (walk follow-up F5), so no override, verdict,
# fix record or mode of a shot gone is ever read as a new shot's.
EPISODE_ASSETS_SHOT_MAPS = ("shots", "keyframe_verdicts", "keyframe_fixes", "shot_modes")


def episode_assets_errors(doc) -> list:
    """``validate()`` against ``EPISODE_ASSETS_SCHEMA``, plus: ``lines`` keyed
    by line ids, each with its ``words_source`` but an entry holding a
    pending voice take alone (phase 5 stage 7), ``aligned_by`` exactly when
    the words were aligned, an SFX
    cue's file there exactly when it resolved, the BGM weights keyed by
    emotions with the dominant one the heaviest, a track's file, sha256
    and licence recorded together, the per-shot overrides (phase 6
    stage 7) keyed by shot ids, none empty, and the keyframe verdicts (phase
    7 stage 6b) keyed by shot ids."""
    errors = validate(doc, EPISODE_ASSETS_SCHEMA)
    if errors:
        return errors

    errors = []
    for key, entry in doc["lines"].items():
        path = f"$.lines.{key}"
        if not (isinstance(key, str) and _search(LINE_ID_PATTERN, key)):
            errors.append(f"$.lines: {key!r} is not a line id")
            continue
        found = validate(entry, _EPISODE_ASSETS_LINE_SCHEMA, path)
        if found:
            errors.extend(found)
            continue
        if "words_source" not in entry:
            if set(entry) != {"pending"}:
                errors.append(f"{path}.words_source: required (only an entry holding a pending take alone goes "
                              "without)")
            continue
        if (entry["words_source"] == "alignment") != ("aligned_by" in entry):
            errors.append(f"{path}.aligned_by: present exactly when words_source is 'alignment'")

    for key, entry in (doc.get("shots") or {}).items():
        path = f"$.shots.{key}"
        if not (isinstance(key, str) and _search(SHOT_ID_PATTERN, key)):
            errors.append(f"$.shots: {key!r} is not a shot id")
            continue
        found = validate(entry, _EPISODE_ASSETS_SHOT_SCHEMA, path)
        if found:
            errors.extend(found)
        elif not entry:
            errors.append(f"{path}: an override names at least one of {', '.join(SHOT_OVERRIDE_FLAGS)}")

    for key, entry in (doc.get("shot_modes") or {}).items():
        path = f"$.shot_modes.{key}"
        if not (isinstance(key, str) and _search(SHOT_ID_PATTERN, key)):
            errors.append(f"$.shot_modes: {key!r} is not a shot id")
            continue
        found = validate(entry, _EPISODE_ASSETS_SHOT_MODE_SCHEMA, path)
        if found:
            errors.extend(found)
        elif not entry:
            errors.append(f"{path}: a mode names at least one of {', '.join(SHOT_MODE_KINDS)}")

    for key, entry in (doc.get("keyframe_verdicts") or {}).items():
        path = f"$.keyframe_verdicts.{key}"
        if not (isinstance(key, str) and _search(SHOT_ID_PATTERN, key)):
            errors.append(f"$.keyframe_verdicts: {key!r} is not a shot id")
            continue
        errors.extend(validate(entry, _EPISODE_ASSETS_KEYFRAME_VERDICT_SCHEMA, path))

    for key, entry in (doc.get("keyframe_fixes") or {}).items():
        path = f"$.keyframe_fixes.{key}"
        if not (isinstance(key, str) and _search(SHOT_ID_PATTERN, key)):
            errors.append(f"$.keyframe_fixes: {key!r} is not a shot id")
            continue
        errors.extend(validate(entry, _EPISODE_ASSETS_KEYFRAME_FIX_SCHEMA, path))

    for key, count in ((doc.get("speech_retakes") or {}).get("shots") or {}).items():
        if not (isinstance(key, str) and _search(SHOT_ID_PATTERN, key)):
            errors.append(f"$.speech_retakes.shots: {key!r} is not a shot id")
        elif isinstance(count, bool) or not isinstance(count, int) or count < 0:
            errors.append(f"$.speech_retakes.shots.{key}: {count!r} is not a number of retakes")

    for i, cue in enumerate(doc["sfx"]):
        if (cue["state"] == "resolved") != (cue["file"] is not None):
            errors.append(f"$.sfx[{i}].file: a 'resolved' cue has a file and a 'missing' one has none")

    bgm = doc["bgm"]
    if bgm is not None:
        weights = bgm["weights_s"]
        bad_weights = False
        for emotion, seconds in weights.items():
            if emotion not in EMOTIONS:
                errors.append(f"$.bgm.weights_s: {emotion!r} is not an emotion")
                bad_weights = True
            elif not _is_type(seconds, "number") or seconds < 0:
                errors.append(f"$.bgm.weights_s.{emotion}: {seconds!r} is not a number of seconds >= 0")
                bad_weights = True
        if weights and not bad_weights:
            heaviest = max(weights.values())
            if weights.get(bgm["dominant_emotion"]) != heaviest:
                errors.append(
                    f"$.bgm.dominant_emotion: {bgm['dominant_emotion']!r} does not carry the largest "
                    f"weight ({heaviest}s)"
                )
        recorded = [bgm[key] is not None for key in ("file", "sha256", "licence")]
        if any(recorded) and not all(recorded):
            errors.append("$.bgm: file, sha256 and licence are recorded together, or all null")

    return errors


# ------------------------------------------------------- render_manifest_v1 (spec 2.9)

RENDER_MANIFEST_SCHEMA_NAME = "render_manifest_v1"

# A render is made with one profile (DEC-157): "final" for the episode,
# "golden" for the parity fixture (spec 13).
RENDER_PROFILES = ("final", "golden")
RENDER_ENCODERS = ("libx264", "auto")
# One per ffmpeg/ffprobe command, in the order they run: S per shot, E the
# end card, A the audio mix, L1 the loudness measurement, F the final pass,
# L2 the loudness correction, P the probe, M the framemd5.
RENDER_STAGE_KINDS = (
    "shot", "end_card", "audio_mix", "loudness_measure", "final", "loudness_apply", "probe", "framemd5",
)
RENDER_STAGE_STATES = ("running", "done", "failed", "cancelled", "cached")
# Only these stages' outputs are kept in render/cache/ and reused.
RENDER_CACHED_KINDS = ("shot", "end_card")
# Phase 6 stage 10: ``clip_audio`` -- a tier-3 shot's clip, staged again as
# the audio mix's input, its sound kept in place of the shot's lines (or,
# stage E, under them as ambience).
RENDER_INPUT_ROLES = ("shot", "line", "sfx", "bgm", "overlay", "clip_audio")
# What a shot was cut from at tier >= 2 (phase 6 stage 9): its own clip
# (``video``), or its image with Tier-1 motion -- plain (``motion``: tier 1,
# or no clip was planned for it), because its effective flags keep it still
# (``motion_keep_still``), or in place of a clip that failed, went stale or is
# still generating, by the render param ``fill_failed_with_motion``
# (``motion_fill``). The render step's own param name is
# ``RENDER_FILL_PARAM``. Phase 6 stage 10 (DEC-201): at tier 3, its own clip
# with the clip's sound heard in place of the shot's lines
# (``video_native_audio``). Phase 7 follow-up, stage E: on an ambience story
# (``media_policy.ambience``), its own clip with the clip's sound heard UNDER
# the shot's lines (``video_ambience``).
RENDER_SHOT_MODES = ("video", "motion", "motion_keep_still", "motion_fill", "video_native_audio", "video_ambience")
RENDER_FILL_PARAM = "fill_failed_with_motion"
# Plan 23 stage B6: the frame a render drew when it is not 9:16
# (``render.profiles.GEOMETRIES`` less the portrait default). A 9:16 render
# records no aspect, so every manifest written before stays as it was.
RENDER_ASPECT_PARAM = "aspect"
RENDER_ASPECTS = ("16:9", "1:1")
RENDER_STAGE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_:.-]{0,39}$"
STDERR_TAIL_MAX = 4000

_RENDER_INPUT_SCHEMA = _document({
    "role": {"type": "string", "enum": list(RENDER_INPUT_ROLES)},
    # The shot (its image, its clip, or its clip's sound), line, cue or
    # overlay it belongs to; null for the bed.
    "id": {"type": ["string", "null"], "maxLength": 40, "pattern": _ID_PATTERN},
    # Where it came from (the story's folder, or the shipped assets/)...
    "source": _RELATIVE_PATH,
    # ... and its copy under render/, named by its content hash.
    "staged": _RELATIVE_PATH,
    "sha256": _SHA256,
})

# Written before its command runs (state "running"), then settled.
_RENDER_STAGE_SCHEMA = _document({
    "id": {"type": "string", "pattern": RENDER_STAGE_ID_PATTERN},
    "kind": {"type": "string", "enum": list(RENDER_STAGE_KINDS)},
    "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    "cache_key": {"type": ["string", "null"], "pattern": SHA256_PATTERN},
    "state": {"type": "string", "enum": list(RENDER_STAGE_STATES)},
    "output": _or_null(_RELATIVE_PATH),
    "output_sha256": {"type": ["string", "null"], "pattern": SHA256_PATTERN},
    "seconds": {"type": ["number", "null"], "minimum": 0},
    "stderr_tail": {"type": ["string", "null"], "maxLength": STDERR_TAIL_MAX},
})

_RENDER_PARAMS_SCHEMA = _document({
    "subtitles": {"type": "string", "enum": list(SUBTITLE_MODES)},
    "encoder": {"type": "string", "enum": list(RENDER_ENCODERS)},
}, optional={
    # Phase 6 stage 9: recorded only when it is on (a render without it is
    # the one it always was).
    RENDER_FILL_PARAM: {"type": "boolean", "const": True},
    # Plan 23 stage B6: recorded only when the frame is not 9:16.
    RENDER_ASPECT_PARAM: {"type": "string", "enum": list(RENDER_ASPECTS)},
})

# The framemd5 parity key is "<version>/<machine>" (DEC-156).
_RENDER_FFMPEG_SCHEMA = _document({
    "version": _text(120),
    "machine": _text(40),
})

# The font the text was burned with, and why it was chosen (DEC-159); null
# when the render burns no text at all.
_RENDER_FONT_SCHEMA = _or_null(_document({
    "family": _text(120),
    "file": _RELATIVE_PATH,
    "sha256": _SHA256,
    "reason": _text(300),
}))

_RENDER_LOUDNESS_SCHEMA = _document({
    "i": {"type": "number"},
    "tp": {"type": "number"},
    "lra": {"type": "number"},
})

# Null until the render finishes.
_RENDER_OUTPUT_SCHEMA = _or_null(_document({
    "path": _RELATIVE_PATH,
    "sha256": _SHA256,
    "duration_s": {"type": "number", "minimum": 0},
    "width": {"type": "integer", "minimum": 1},
    "height": {"type": "integer", "minimum": 1},
    "fps": {"type": "string", "pattern": r"^[1-9][0-9]*/[1-9][0-9]*$"},
    "loudness": _RENDER_LOUDNESS_SCHEMA,
    "framemd5": _document({"file": _RELATIVE_PATH, "sha256": _SHA256}),
}))

_RENDER_TIMINGS_SCHEMA = _document({
    "started_at": _NON_EMPTY_STRING,
    "finished_at": _TIMESTAMP_OR_NULL,
    "total_s": {"type": ["number", "null"], "minimum": 0},
})

# Why a shot's clip is made again rather than taken from the render cache
# (phase 5 stage 8, ``render/partial.py``): what changed in its command since
# the last good render -- its image (or video), its camera motion, its frame
# count, its modifiers, the style's overlays -- or, its command unchanged, a
# clip that is not in the cache or not the one recorded (missing, corrupt); a
# shot the last good render did not have (new); anything else in the command's
# key -- the ffmpeg, the render profile, the encoder settings (settings).
RENDER_REUSE_REASONS = ("image", "motion", "frames", "modifiers", "overlay", "missing", "corrupt", "new",
                        "settings")

# What a render made again and took from the cache, relative to the last good
# render of the episode (``render_manifest.last_good.json``); absent or null
# when there was none to compare with. Written before the first process, true
# at every write: once the output is recorded, the shots it lists as rebuilt
# are exactly the ``S:`` stages that ran, the reused ones those found cached.
_RENDER_REUSE_SCHEMA = _or_null(_document({
    "baseline_output_sha256": _SHA256,
    "shots_total": {"type": "integer", "minimum": 0},
    "shots_rebuilt": {"type": "array", "items": {"type": "string", "pattern": SHOT_ID_PATTERN}},
    "shots_reused": {"type": "array", "items": {"type": "string", "pattern": SHOT_ID_PATTERN}},
    # {shot id: one of RENDER_REUSE_REASONS}, one per rebuilt shot (checked below)
    "reasons": {"type": "object"},
    # The shots' timing moved to whole frames (phase 5 stage 6) since the last
    # good render: most of their frames moved once, so this render is not partial.
    "timing_converted": {"type": "boolean"},
}))

RENDER_MANIFEST_SCHEMA = _document({
    "$schema": {"type": "string", "const": RENDER_MANIFEST_SCHEMA_NAME},
    "ep": _EP,
    "profile": {"type": "string", "enum": list(RENDER_PROFILES)},
    "params": _RENDER_PARAMS_SCHEMA,
    "ffmpeg": _RENDER_FFMPEG_SCHEMA,
    "font": _RENDER_FONT_SCHEMA,
    "inputs": {"type": "array", "items": _RENDER_INPUT_SCHEMA},
    "stages": {"type": "array", "items": _RENDER_STAGE_SCHEMA},
    "output": _RENDER_OUTPUT_SCHEMA,
    "timings": _RENDER_TIMINGS_SCHEMA,
    # A length or a loudness outside the target: reported, not a failure.
    "warnings": {"type": "array", "items": _text(300)},
    "created_at": _NON_EMPTY_STRING,
    "updated_at": _NON_EMPTY_STRING,
}, optional={
    # Phase 5 stage 8: whether the storyboard it was cut from is timed in
    # whole frames (``timing.board_whole_frames``; a manifest written before
    # has no flag), and what it rebuilt and reused against the last good render.
    "whole_frames": {"type": "boolean"},
    "reuse": _RENDER_REUSE_SCHEMA,
    # {cache key: [sha256, ...]}: the clips render/cache/ keeps once the render
    # completed (this render's and the last one's), each with the sha256 its
    # making recorded -- so a clip the previous manifest named is still
    # provable after this one replaced it (an edit undone).
    "cache": {"type": "object"},
    # Phase 6 stage 9: {shot id: one of RENDER_SHOT_MODES}, one per shot (checked
    # below); written only when some shot is not plain ``motion``, so a tier-1
    # render's manifest is the one it always was.
    "shot_modes": {"type": "object"},
})


def _render_reuse_errors(doc) -> list:
    """The ``reuse`` record's own rules (``_RENDER_REUSE_SCHEMA``): the rebuilt
    and the reused shots are apart and count ``shots_total``, one reason of
    ``RENDER_REUSE_REASONS`` per rebuilt shot and none else; once the output is
    recorded, they are exactly the ``S:`` stages that ran and those found
    cached."""
    reuse = doc.get("reuse")
    if reuse is None:
        return []
    errors = []
    rebuilt, reused = reuse["shots_rebuilt"], reuse["shots_reused"]
    if len(set(rebuilt)) != len(rebuilt) or len(set(reused)) != len(reused) or set(rebuilt) & set(reused):
        errors.append("$.reuse: a shot is listed twice between shots_rebuilt and shots_reused")
    if reuse["shots_total"] != len(rebuilt) + len(reused):
        errors.append(f"$.reuse.shots_total: {reuse['shots_total']} is not the {len(rebuilt) + len(reused)} shots "
                      "listed")
    reasons = reuse["reasons"]
    if set(reasons) != set(rebuilt):
        errors.append("$.reuse.reasons: one reason per rebuilt shot, and none for another")
    for shot_id, reason in reasons.items():
        if reason not in RENDER_REUSE_REASONS:
            errors.append(f"$.reuse.reasons.{shot_id}: {reason!r} is not one of {list(RENDER_REUSE_REASONS)}")
    if doc["output"] is not None:
        shot_stages = [stage for stage in doc["stages"] if stage["kind"] == "shot"]
        ran = [stage["id"][2:] for stage in shot_stages if stage["state"] == "done"]
        found = [stage["id"][2:] for stage in shot_stages if stage["state"] == "cached"]
        if sorted(ran) != sorted(rebuilt) or sorted(found) != sorted(reused):
            errors.append("$.reuse: the shots it lists as rebuilt and reused are not the S stages that ran and "
                          "those found cached")
    return errors


def render_manifest_errors(doc) -> list:
    """``validate()`` against ``RENDER_MANIFEST_SCHEMA``, plus the rules of a
    stage's life: ids unique; a running stage has no result yet; a done or
    cached one names its output and its sha256; only a shot or the end card
    is cached, and by its key. An output is recorded only once every stage
    is done or cached, and its loudness is finite. The optional ``reuse``
    record (phase 5 stage 8) tells the truth about the stages
    (``_render_reuse_errors``); the optional ``cache`` map names cache keys,
    each with its recorded sha256s; the optional ``shot_modes`` (phase 6
    stage 9) names one of ``RENDER_SHOT_MODES`` for every shot it lists, and
    lists every S stage's shot."""
    errors = validate(doc, RENDER_MANIFEST_SCHEMA)
    if errors:
        return errors

    errors = []
    seen = set()
    for i, stage in enumerate(doc["stages"]):
        path = f"$.stages[{i}]"
        if stage["id"] in seen:
            errors.append(f"{path}.id: {stage['id']!r} is listed twice")
        seen.add(stage["id"])
        state = stage["state"]
        if state == "running" and (stage["output_sha256"] is not None or stage["seconds"] is not None):
            errors.append(f"{path}: a 'running' stage has no output_sha256 or seconds yet")
        if state in ("done", "cached") and (stage["output"] is None or stage["output_sha256"] is None):
            errors.append(f"{path}: a {state!r} stage names its output and output_sha256")
        if state == "cached":
            if stage["kind"] not in RENDER_CACHED_KINDS:
                errors.append(f"{path}.state: a {stage['kind']!r} stage is never cached ({list(RENDER_CACHED_KINDS)})")
            if stage["cache_key"] is None:
                errors.append(f"{path}.cache_key: a 'cached' stage names the key it was found under")

    output = doc["output"]
    if output is not None:
        unsettled = [stage["id"] for stage in doc["stages"] if stage["state"] not in ("done", "cached")]
        if unsettled:
            errors.append(f"$.output: recorded while stage(s) {unsettled} are not done or cached")
        _finite_errors(errors, "$.output.loudness", output["loudness"])

    for key, shas in (doc.get("cache") or {}).items():
        if not re.match(SHA256_PATTERN, key) or not isinstance(shas, list) or not shas or not all(
                isinstance(sha, str) and re.match(SHA256_PATTERN, sha) for sha in shas):
            errors.append(f"$.cache: {key!r} must be a cache key naming a list of sha256s")
            break

    modes = doc.get("shot_modes")
    if modes is not None:
        shot_ids = [stage["id"][2:] for stage in doc["stages"] if stage["kind"] == "shot"]
        if not modes or any(not re.match(SHOT_ID_PATTERN, str(key)) or value not in RENDER_SHOT_MODES
                            for key, value in modes.items()):
            errors.append(f"$.shot_modes: each shot id must name one of {list(RENDER_SHOT_MODES)}")
        elif any(shot_id not in modes for shot_id in shot_ids):
            errors.append("$.shot_modes: every S stage's shot needs its mode")

    errors.extend(_render_reuse_errors(doc))
    return errors


# --------------------------------------------------------- metadata_pack_v1 (spec 2.10)

METADATA_PACK_SCHEMA_NAME = "metadata_pack_v1"

METADATA_HASHTAGS_RANGE = (3, 6)
# One tag, with its "#", as it is pasted into the platform.
HASHTAG_PATTERN = r"^#[^\s#]{1,59}$"

_HASHTAGS = {
    "type": "array", "items": {"type": "string", "pattern": HASHTAG_PATTERN},
    "minItems": METADATA_HASHTAGS_RANGE[0], "maxItems": METADATA_HASHTAGS_RANGE[1],
}

_METADATA_PLATFORM_SCHEMA = _document({
    "title": _text(100),
    # Ends with the script's next_episode_teaser (appended by Python).
    "description": _text(5000),
    "hashtags": _HASHTAGS,
    "hook_text": _text(150),
    # The teaser + the call to comment "PART n+1" (steps/metadata.PART_CALL).
    "pinned_comment": _text(500),
    # Relative to the episode's folder.
    "cover": _RELATIVE_PATH,
    "written_at": _NON_EMPTY_STRING,
}, optional={
    # A French story's English title and tags, and only a French story's.
    "title_en": _text(100),
    "hashtags_en": _HASHTAGS,
})

METADATA_PACK_SCHEMA = _document({
    "$schema": {"type": "string", "const": METADATA_PACK_SCHEMA_NAME},
    "ep": _EP,
    "language": {"type": "string", "enum": list(LANGUAGES)},
    # What the pack was written from: the script's rev and the render's file.
    "script_rev": {"type": "integer", "minimum": 1},
    "render_sha256": _SHA256,
    # keyed by platform -> _METADATA_PLATFORM_SCHEMA, checked in metadata_pack_errors.
    "platforms": {"type": "object"},
    "created_at": _NON_EMPTY_STRING,
    "updated_at": _NON_EMPTY_STRING,
}, optional={
    # Plan 23 stage B7: a 16:9 or 1:1 episode's frame, and what that means for where it goes
    # (steps/metadata.ASPECT_NOTES); absent on a 9:16 one.
    "aspect": {"type": "string", "enum": ["16:9", "1:1"]},
    "aspect_note": _text(300),
    # Plan 23 stage B8: the stock clips the episode is cut from, one credit record each
    # (clipping/stock/credits.credit_record); absent when the episode has none.
    "credits": {"type": "array", "items": {"type": "object"}, "maxItems": 100},
})

_EN_FIELDS = ("title_en", "hashtags_en")


def metadata_pack_errors(doc) -> list:
    """``validate()`` against ``METADATA_PACK_SCHEMA``, plus: ``platforms``
    keyed by ``PLATFORMS``; the English fields on every platform of a
    French story and on none of an English one; no tag listed twice."""
    errors = validate(doc, METADATA_PACK_SCHEMA)
    if errors:
        return errors

    errors = []
    french = doc["language"] == "fr"
    for key, entry in doc["platforms"].items():
        path = f"$.platforms.{key}"
        if key not in PLATFORMS:
            errors.append(f"$.platforms: {key!r} is not one of {list(PLATFORMS)}")
            continue
        found = validate(entry, _METADATA_PLATFORM_SCHEMA, path)
        if found:
            errors.extend(found)
            continue
        for field in _EN_FIELDS:
            if french and field not in entry:
                errors.append(f"{path}.{field}: required for a French story")
            elif not french and field in entry:
                errors.append(f"{path}.{field}: only a French story carries English fields")
        for field in ("hashtags", "hashtags_en"):
            tags = entry.get(field) or []
            for tag in sorted({tag for tag in tags if tags.count(tag) > 1}):
                errors.append(f"{path}.{field}: {tag!r} is listed twice")

    return errors


# ============================================================ phase 5 documents (plan 11, stage 1)

# ---------------------------------------------- next_proposals_v1 (episodes/epNN/proposals.json)
#
# N1's proposals for the next episode: at most two new characters and two
# twists, written from episode ``based_on.memory_ep``'s approved memory
# (and the script rev it was written from) into the folder of the episode
# they are for (``for_ep`` = memory_ep + 1). ``decisions`` records what the
# user accepted or rejected, item by item.

NEXT_PROPOSALS_SCHEMA_NAME = "next_proposals_v1"
PROPOSALS_MAX_CHARACTERS = 2
PROPOSALS_MAX_TWISTS = 2
TWIST_HOOKS_MAX = 3
# An item's id is also a URL segment (the accept/reject route): a short slug.
PROPOSAL_ITEM_ID_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
PROPOSAL_DECISIONS = ("accepted", "rejected")

_PROPOSAL_ITEM_ID = {"type": "string", "pattern": PROPOSAL_ITEM_ID_PATTERN}

_PROPOSED_CHARACTER_SCHEMA = _document({
    "item_id": _PROPOSAL_ITEM_ID,
    # The caps of character_v1.
    "name": _text(60),
    "role": {"type": "string", "enum": list(CHARACTER_ROLES)},
    "one_line": _text(200),
    "why": _text(300),
}, optional={
    "archetype": _text(60),
})

_PROPOSED_TWIST_SCHEMA = _document({
    "item_id": _PROPOSAL_ITEM_ID,
    "target_ep": _EP,
    # What the target arc entry's summary and open_hooks_out become (season_arc_v1's caps).
    "summary": _NON_EMPTY_STRING,
    "open_hooks_out": {"type": "array", "items": _text(HOOK_MAX_LENGTH), "maxItems": TWIST_HOOKS_MAX},
    "why": _text(300),
}, optional={
    # Plan 23 stage D5 (N1v2): the appearance variant of an existing character the
    # twist brings; accepting the twist creates it (``workflow.decide_proposal``).
    "variant": _document({
        "char_id": {"type": "string", "pattern": CHAR_ID_PATTERN},
        "label": _text(VARIANT_LABEL_MAX_CHARS),
        "delta_text": _NON_EMPTY_STRING,
    }),
})

NEXT_PROPOSALS_SCHEMA = _document({
    "$schema": {"type": "string", "const": NEXT_PROPOSALS_SCHEMA_NAME},
    "for_ep": _EP,
    "based_on": _document({
        "memory_ep": _EP,
        "script_rev": {"type": "integer", "minimum": 1},
    }),
    "characters": {"type": "array", "items": _PROPOSED_CHARACTER_SCHEMA, "maxItems": PROPOSALS_MAX_CHARACTERS},
    "twists": {"type": "array", "items": _PROPOSED_TWIST_SCHEMA, "maxItems": PROPOSALS_MAX_TWISTS},
    # item_id -> one of PROPOSAL_DECISIONS, checked in next_proposals_errors.
    "decisions": {"type": "object"},
    "created_at": _NON_EMPTY_STRING,
    "updated_at": _NON_EMPTY_STRING,
})


def next_proposals_errors(doc) -> list:
    """``validate()`` against ``NEXT_PROPOSALS_SCHEMA``, plus: the proposals
    are for the episode after the memory they were written from
    (``for_ep == based_on.memory_ep + 1``); each twist targets an episode
    after that memory; the texts non-blank, a twist's summary within the
    arc's word cap; each item id used once across characters and twists;
    ``decisions`` keyed by those ids, each accepted or rejected."""
    errors = validate(doc, NEXT_PROPOSALS_SCHEMA)
    if errors:
        return errors

    errors = []
    memory_ep = doc["based_on"]["memory_ep"]
    if doc["for_ep"] != memory_ep + 1:
        errors.append(f"$.for_ep: {doc['for_ep']} is not the episode after based_on.memory_ep {memory_ep}")

    ids = []
    for i, item in enumerate(doc["characters"]):
        path = f"$.characters[{i}]"
        for field in ("name", "one_line", "why", "archetype"):
            if field in item:
                _check_text(errors, f"{path}.{field}", item[field])
        ids.append(item["item_id"])
    for i, item in enumerate(doc["twists"]):
        path = f"$.twists[{i}]"
        if item["target_ep"] <= memory_ep:
            errors.append(f"{path}.target_ep: {item['target_ep']} is not after based_on.memory_ep {memory_ep}")
        _check_text(errors, f"{path}.summary", item["summary"], max_words=ARC_SUMMARY_MAX_WORDS)
        _check_text(errors, f"{path}.why", item["why"])
        for j, hook in enumerate(item["open_hooks_out"]):
            _check_text(errors, f"{path}.open_hooks_out[{j}]", hook)
        ids.append(item["item_id"])
    for item_id in sorted({item_id for item_id in ids if ids.count(item_id) > 1}):
        errors.append(f"$: item id {item_id!r} is used twice")

    for item_id, decision in doc["decisions"].items():
        if item_id not in ids:
            errors.append(f"$.decisions: {item_id!r} is not a proposed item")
        elif decision not in PROPOSAL_DECISIONS:
            errors.append(f"$.decisions.{item_id}: {decision!r} is not one of {list(PROPOSAL_DECISIONS)}")
    return errors


# ============================================================ LLM output schemas (spec 4.2) -- phase 2
#
# What a *model* returns for the K1/P0/P1/R1/S1/S2/U1 prompts of
# ``prompts.py`` (a phase-2 counterpart to the C1/B1/B2/B3 schemas above):
# the same strict-mode subset, the same "lengths and counts are the prompt
# text's and the post-validator's job, never the schema's" rule. Word/count
# caps reuse the ``character_v1``/``place_v1``/``prop_v1``/``season_arc_v1``
# constants above wherever the phase-1 document and the phase-2 prompt share
# the same limit (spec 4.2), so the two never drift apart silently.

VOICE_GENDERS = ("female", "male", "neutral")
VOICE_AGES = ("child", "young", "adult", "elder")
# A closed list (spec 4.2, row K1): kept short and orthogonal so a TTS
# provider's own voice catalogue can be matched against it later.
VOICE_STYLE_TAGS = (
    "warm", "bright", "deep", "raspy", "soft", "fast", "slow", "smug",
    "nervous", "authoritative", "playful", "calm",
)

K1_TRAITS_RANGE = (2, 5)
K1_TRAIT_MAX_WORDS = 4
K1_WANTS_FEARS_SPEECH_MAX_WORDS = 25
K1_VOICE_DIRECTION_MAX_WORDS = 20
K1_STYLE_TAGS_RANGE = (1, 3)
K1_RELATIONSHIPS_MAX = 5
K1_RELATION_MAX_WORDS = 15


def k1_schema(cast_names) -> dict:
    """The K1 output schema (spec 4.2, row K1): one character's descriptor,
    signature items, personality, voice and relationships.

    ``cast_names`` constrains ``relationships[].with`` to the existing cast
    (passed in by the caller so this module needs no store import); an
    empty list leaves it free text, since an empty cast can only ever
    produce an empty ``relationships`` array anyway.
    """
    with_schema = {"type": "string", "enum": list(cast_names)} if cast_names else {"type": "string"}
    personality = _llm_obj({
        "traits": {
            "type": "array",
            "description": "2-5 traits, each at most 4 words",
            "items": {"type": "string"},
        },
        "wants": {"type": "string", "description": "story language, at most 25 words"},
        "fears": {"type": "string", "description": "story language, at most 25 words"},
        "speech_style": {"type": "string", "description": "story language, at most 25 words"},
    })
    voice = _llm_obj({
        "gender": {"type": "string", "enum": list(VOICE_GENDERS)},
        "age": {"type": "string", "enum": list(VOICE_AGES)},
        "style_tags": {
            "type": "array",
            "description": "1-3 tags from the closed list",
            "items": {"type": "string", "enum": list(VOICE_STYLE_TAGS)},
        },
        "direction": {"type": "string", "description": "English, at most 20 words, for a voice actor"},
        "sample_line": {"type": "string", "description": "story language, at most 12 words, in character, no name"},
    })
    relationship = _llm_obj({
        "with": with_schema,
        "relation": {"type": "string", "description": "story language, at most 15 words"},
    })
    return _llm_obj({
        "descriptor": {
            "type": "string",
            "description": "English, at most 45 words, appearance only, never the character's name",
        },
        "signature_items": {
            "type": "array",
            "description": "English, 2-3 items, each at most 8 words",
            "items": {"type": "string"},
        },
        "personality": personality,
        "voice": voice,
        "relationships": {
            "type": "array",
            "description": "0-5 relationships to the existing cast",
            "items": relationship,
        },
    })


def k1_errors(doc, name) -> list:
    """Post-validation for a K1 response, beyond what ``k1_schema`` can
    express: descriptor/signature/personality/voice/relationship word and
    count caps, and the name-leak check -- *name* (the character's own)
    must never appear in the descriptor, a signature item or the sample
    line (spec 2.3: prompts reference appearance, never the name).
    """
    errors = validate(doc, k1_schema(()))
    if errors:
        return errors

    errors = []
    descriptor = doc["descriptor"]
    _check_text(errors, "$.descriptor", descriptor, max_words=DESCRIPTOR_MAX_WORDS)

    items = doc["signature_items"]
    lo, hi = SIGNATURE_ITEMS_WRITTEN
    if not (lo <= len(items) <= hi):
        errors.append(f"$.signature_items: {len(items)} item(s), expected {lo}-{hi}")
    for i, item in enumerate(items):
        _check_text(errors, f"$.signature_items[{i}]", item, max_words=8)

    personality = doc["personality"]
    traits = personality["traits"]
    lo, hi = K1_TRAITS_RANGE
    if not (lo <= len(traits) <= hi):
        errors.append(f"$.personality.traits: {len(traits)} trait(s), expected {lo}-{hi}")
    for i, trait in enumerate(traits):
        _check_text(errors, f"$.personality.traits[{i}]", trait, max_words=K1_TRAIT_MAX_WORDS)
    _check_text(errors, "$.personality.wants", personality["wants"], max_words=K1_WANTS_FEARS_SPEECH_MAX_WORDS)
    _check_text(errors, "$.personality.fears", personality["fears"], max_words=K1_WANTS_FEARS_SPEECH_MAX_WORDS)
    _check_text(
        errors, "$.personality.speech_style", personality["speech_style"],
        max_words=K1_WANTS_FEARS_SPEECH_MAX_WORDS,
    )

    voice = doc["voice"]
    style_tags = voice["style_tags"]
    lo, hi = K1_STYLE_TAGS_RANGE
    if not (lo <= len(style_tags) <= hi):
        errors.append(f"$.voice.style_tags: {len(style_tags)} tag(s), expected {lo}-{hi}")
    _check_text(errors, "$.voice.direction", voice["direction"], max_words=K1_VOICE_DIRECTION_MAX_WORDS)
    sample_line = voice["sample_line"]
    _check_text(errors, "$.voice.sample_line", sample_line, max_words=SAMPLE_LINE_MAX_WORDS)

    relationships = doc["relationships"]
    if len(relationships) > K1_RELATIONSHIPS_MAX:
        errors.append(f"$.relationships: {len(relationships)} relationship(s), expected at most {K1_RELATIONSHIPS_MAX}")
    for i, relationship in enumerate(relationships):
        _check_text(errors, f"$.relationships[{i}].relation", relationship["relation"], max_words=K1_RELATION_MAX_WORDS)

    name = (name or "").strip()
    if name:
        name_pattern = re.compile(
            r"(?<!\w)" + r"\s+".join(re.escape(word) for word in name.split()) + r"(?!\w)",
            re.IGNORECASE,
        )
        haystacks = [("$.descriptor", descriptor)] + [
            (f"$.signature_items[{i}]", item) for i, item in enumerate(items)
        ]
        if isinstance(sample_line, str):
            haystacks.append(("$.voice.sample_line", sample_line))
        for path, text in haystacks:
            if isinstance(text, str) and name_pattern.search(text):
                errors.append(f"{path}: must not mention the character's own name")

    return errors


# ------------------------------------------------------------- P0 (places_proposal, spec plan 1.2)

P0_PLACES_RANGE = (2, 3)
P0_PROPS_MAX = 3
P0_NAME_MAX_WORDS = 5
P0_ONE_LINE_MAX_WORDS = 20


def p0_schema(cast_names) -> dict:
    """The P0 output schema: propose 2-3 places and 0-3 props sourced from
    the bible's recurring motifs and the cast's signature items.

    ``cast_names`` constrains a prop's ``owner`` to the existing cast (or
    null); passed in so this module needs no store import.
    """
    owner_schema = (
        {"type": ["string", "null"], "enum": list(cast_names) + [None]}
        if cast_names else {"type": ["string", "null"]}
    )
    place = _llm_obj({
        "name": {"type": "string", "description": "story language, at most 5 words"},
        "one_line": {"type": "string", "description": "story language, at most 20 words"},
    })
    prop = _llm_obj({
        "name": {"type": "string", "description": "story language, at most 5 words"},
        "one_line": {"type": "string", "description": "story language, at most 20 words"},
        "owner": owner_schema,
    })
    return _llm_obj({
        "places": {"type": "array", "description": "2-3 places", "items": place},
        "props": {
            "type": "array",
            "description": "0-3 props, from the cast's signature items or the bible's recurring motifs",
            "items": prop,
        },
    })


def p0_errors(doc) -> list:
    """Post-validation for a P0 response, beyond what ``p0_schema`` can express."""
    errors = validate(doc, p0_schema(()))
    if errors:
        return errors

    errors = []
    places = doc["places"]
    lo, hi = P0_PLACES_RANGE
    if not (lo <= len(places) <= hi):
        errors.append(f"$.places: {len(places)} place(s), expected {lo}-{hi}")
    for i, place in enumerate(places):
        _check_text(errors, f"$.places[{i}].name", place["name"], max_words=P0_NAME_MAX_WORDS)
        _check_text(errors, f"$.places[{i}].one_line", place["one_line"], max_words=P0_ONE_LINE_MAX_WORDS)

    props = doc["props"]
    if len(props) > P0_PROPS_MAX:
        errors.append(f"$.props: {len(props)} prop(s), expected at most {P0_PROPS_MAX}")
    for i, prop in enumerate(props):
        _check_text(errors, f"$.props[{i}].name", prop["name"], max_words=P0_NAME_MAX_WORDS)
        _check_text(errors, f"$.props[{i}].one_line", prop["one_line"], max_words=P0_ONE_LINE_MAX_WORDS)

    return errors


# ------------------------------------------------------------------------- P1 (place_v1)

# The closed list a P1 reply may pick time variants from (spec 4.2, row P1);
# a subset of the free-form ``TIME_VARIANT_PATTERN`` a place's document may
# carry once images exist for other variants too.
TIME_VARIANT_CHOICES = ("day", "night", "dusk", "rain", "dawn")


def p1_schema() -> dict:
    """The P1 output schema (spec 4.2, row P1): one place's descriptor,
    layout notes and time variants."""
    return _llm_obj({
        "descriptor": {
            "type": "string",
            "description": "English, at most 45 words, the place alone, no people, no characters",
        },
        "layout_notes": {
            "type": "string",
            "description": "English, at most 60 words: what is left, right, back and foreground, for continuity",
        },
        "time_variants": {
            "type": "array",
            "description": "1-3 variants from day, night, dusk, rain, dawn, always including day",
            "items": {"type": "string", "enum": list(TIME_VARIANT_CHOICES)},
        },
    })


def p1_errors(doc) -> list:
    """Post-validation for a P1 response, beyond what ``p1_schema`` can express."""
    errors = validate(doc, p1_schema())
    if errors:
        return errors

    errors = []
    _check_text(errors, "$.descriptor", doc["descriptor"], max_words=DESCRIPTOR_MAX_WORDS)
    _check_text(errors, "$.layout_notes", doc["layout_notes"], max_words=LAYOUT_NOTES_MAX_WORDS)

    variants = doc["time_variants"]
    if not (1 <= len(variants) <= 3):
        errors.append(f"$.time_variants: {len(variants)} variant(s), expected 1-3")
    if MASTER_PLATE_VARIANT not in variants:
        errors.append(f"$.time_variants: missing {MASTER_PLATE_VARIANT!r} (always included)")
    if len(variants) != len(set(variants)):
        errors.append("$.time_variants: duplicate variants are not allowed")

    return errors


# ------------------------------------------------------------------------- R1 (prop_v1)

def r1_schema(cast_names) -> dict:
    """The R1 output schema (spec 4.2, row R1): one prop's descriptor and
    owner. ``cast_names`` constrains ``owner`` the same way as P0's."""
    owner_schema = (
        {"type": ["string", "null"], "enum": list(cast_names) + [None]}
        if cast_names else {"type": ["string", "null"]}
    )
    return _llm_obj({
        "descriptor": {"type": "string", "description": "English, at most 30 words, the object alone"},
        "owner": owner_schema,
    })


def r1_errors(doc) -> list:
    """Post-validation for an R1 response, beyond what ``r1_schema`` can express."""
    errors = validate(doc, r1_schema(()))
    if errors:
        return errors

    errors = []
    _check_text(errors, "$.descriptor", doc["descriptor"], max_words=PROP_DESCRIPTOR_MAX_WORDS)
    return errors


# ------------------------------------------------- D1 (phase 7, the dossier)
#
# One character's dossier, written right after K1 on a v2 story (A14): story
# knowledge for the writers, never sent to an image model, so names are
# allowed and the text is in the story's language. ``relationships[].with``
# names another cast member (mapped to its id by the cast step; an unknown
# name, the character itself or a repeat is dropped and printed, as K1's).
# The reply bounds its relationships (the stored block does not), so the
# largest reply fits its cap (MAX_TOKENS["D1"]).
D1_RELATIONSHIPS_MAX = 3


def d1_schema(other_names) -> dict:
    """The D1 output schema; *other_names* (the other cast members) constrain
    ``relationships[].with`` (none: free text, the reply's list stays empty)."""
    with_schema = {"type": "string", "enum": list(other_names)} if other_names else {"type": "string"}
    relationship = _llm_obj({
        "with": with_schema,
        "history": {"type": "string", "description": "story language, their past together, at most 30 words"},
        "now": {"type": "string", "description": "story language, where they stand now, at most 15 words"},
    })
    voice = _llm_obj({
        "patterns": {"type": "string", "description": "story language, how they speak, at most 20 words"},
        "vocabulary": {"type": "string", "description": "story language, the words they use, at most 20 words"},
        "catchphrases": {"type": "array", "description": f"0-{DOSSIER_CATCHPHRASES_MAX}, each at most 10 words",
                         "items": {"type": "string"}},
    })
    return _llm_obj({
        "backstory": {"type": "string", "description": "story language, at most 60 words"},
        "goal": {"type": "string", "description": "story language, what they want, at most 20 words"},
        "need": {"type": "string", "description": "story language, what they truly need, at most 20 words"},
        "fears": {"type": "string", "description": "story language, at most 20 words"},
        "secrets": {"type": "array", "description": f"0-{DOSSIER_SECRETS_MAX}, each at most 20 words",
                    "items": {"type": "string"}},
        "relationships": {"type": "array", "description": f"0-{D1_RELATIONSHIPS_MAX} other cast members",
                          "items": relationship},
        "voice": voice,
        "arc": {"type": "string", "description": "story language, how they change, at most 30 words"},
    })


def d1_errors(doc) -> list:
    """Post-validation for a D1 response: the counts (secrets,
    relationships, catchphrases) and the dossier's word caps."""
    errors = validate(doc, d1_schema(()))
    if errors:
        return errors
    errors = []
    for key, items, cap in (("secrets", doc["secrets"], DOSSIER_SECRETS_MAX),
                            ("relationships", doc["relationships"], D1_RELATIONSHIPS_MAX),
                            ("voice.catchphrases", doc["voice"]["catchphrases"], DOSSIER_CATCHPHRASES_MAX)):
        if len(items) > cap:
            errors.append(f"$.{key}: {len(items)} item(s), expected at most {cap}")
    _dossier_text_errors(errors, doc, "$")
    return errors


def d1_dossier(doc, ids_by_name) -> tuple:
    """D1's reply as the stored ``dossier`` block, and the relationship names
    dropped: *ids_by_name* maps a name to the other character's id (None:
    no such character, or the character itself); a name already used is
    dropped too."""
    relationships, dropped, seen = [], [], set()
    for item in doc["relationships"]:
        char_id = ids_by_name(item["with"])
        if char_id is None or char_id in seen:
            dropped.append(item["with"])
            continue
        seen.add(char_id)
        relationships.append({"with": char_id, "history": item["history"], "now": item["now"]})
    voice = doc["voice"]
    dossier = {
        "backstory": doc["backstory"], "goal": doc["goal"], "need": doc["need"], "fears": doc["fears"],
        "secrets": list(doc["secrets"]), "relationships": relationships,
        "voice": {"patterns": voice["patterns"], "vocabulary": voice["vocabulary"],
                  "catchphrases": list(voice["catchphrases"])},
        "arc": doc["arc"],
    }
    return dossier, dropped


# ------------------------------------------------- D4 / D5 / D6 (phase 7 stage 5b, the knowledge step)
#
# The knowledge step's calls (A14, DEC-228), one artifact each: D4 the
# world notes, D5 one episode's timeline, D6 the props registry. Text for
# the writers, never for an image model: the story's language, names allowed.
# A reply names characters, places and props by name; the step maps them to
# ids (an unknown name is dropped and printed, as K1's and S2's are).

# D5's reply bounds, per beat, so its largest reply has a measured cap
# (MAX_TOKENS["D5"]): the characters acting in it, the objects that matter in
# it, the characters whose knowledge it changes; and the new objects (no prop
# of the story yet) of one episode, at most, for D6 to register.
D5_WHO_MAX = 4
D5_OBJECTS_MAX = 2
D5_KNOWS_MAX = 2
D5_NEW_OBJECTS_MAX = BEAT_NEW_OBJECTS_MAX
# D6: the new props it may create in one call, and a new prop's one-line.
D6_NEW_PROPS_MAX = 3
D6_ONE_LINE_MAX_WORDS = 20
D6_ONE_LINE_MAX_CHARS = 200
D6_NAME_MAX_CHARS = 60


def _named(names) -> dict:
    """A name constrained to *names* (none: free text)."""
    return {"type": "string", "enum": list(names)} if names else {"type": "string"}


def _named_or_null(names) -> dict:
    return {"type": ["string", "null"], "enum": list(names) + [None]} if names else {"type": ["string", "null"]}


def d4_schema() -> dict:
    """The D4 output schema: the world notes of the knowledge base."""
    return _llm_obj({
        "geography": {"type": "string", "description": "story language, at most 60 words"},
        "period_details": {"type": "string", "description": "story language, at most 30 words"},
        "visual_motifs": {"type": "array", "description": f"1-{WORLD_VISUAL_MOTIFS_MAX}, each at most 12 words",
                          "items": {"type": "string"}},
    })


def d4_errors(doc) -> list:
    """Post-validation for a D4 response: the world's word caps and 1 to
    ``WORLD_VISUAL_MOTIFS_MAX`` motifs."""
    errors = validate(doc, d4_schema())
    if errors:
        return errors
    errors = []
    _check_text(errors, "$.geography", doc["geography"], max_words=WORLD_GEOGRAPHY_MAX_WORDS)
    _check_text(errors, "$.period_details", doc["period_details"], max_words=WORLD_PERIOD_DETAILS_MAX_WORDS)
    motifs = doc["visual_motifs"]
    if not 1 <= len(motifs) <= WORLD_VISUAL_MOTIFS_MAX:
        errors.append(f"$.visual_motifs: {len(motifs)} motif(s), expected 1-{WORLD_VISUAL_MOTIFS_MAX}")
    for i, motif in enumerate(motifs):
        _check_text(errors, f"$.visual_motifs[{i}]", motif, max_words=WORLD_VISUAL_MOTIF_MAX_WORDS)
    return errors


def d5_schema(cast_names, place_names) -> dict:
    """The D5 output schema: one episode's beats. *cast_names* constrain
    ``who`` and ``knows_after[].who``, *place_names* ``place`` (or null);
    ``objects`` stay free text (a prop's name, or a new object's)."""
    knows = _llm_obj({
        "who": _named(cast_names),
        "knows": {"type": "string", "description": "story language, what they know after the beat, at most 15 words"},
    })
    beat = _llm_obj({
        "what": {"type": "string", "description": "story language, what happens, at most 25 words"},
        "place": _named_or_null(place_names),
        "who": {"type": "array", "description": f"1-{D5_WHO_MAX} characters", "items": _named(cast_names)},
        "objects": {"type": "array", "description": f"0-{D5_OBJECTS_MAX} objects, by name",
                    "items": {"type": "string"}},
        "knows_after": {"type": "array", "description": f"0-{D5_KNOWS_MAX}", "items": knows},
    })
    return _llm_obj({
        "beats": {"type": "array", "description": f"1-{TIMELINE_BEATS_MAX} beats in story order", "items": beat},
    })


def d5_errors(doc) -> list:
    """Post-validation for a D5 response: 1 to ``TIMELINE_BEATS_MAX`` beats,
    each within its counts (``D5_WHO_MAX``, ``D5_OBJECTS_MAX``,
    ``D5_KNOWS_MAX``) and word caps, an object named at most once a beat.
    Which objects are new (at most ``D5_NEW_OBJECTS_MAX`` an episode) needs
    the story's props: the step checks it."""
    errors = validate(doc, d5_schema((), ()))
    if errors:
        return errors
    errors = []
    beats = doc["beats"]
    if not 1 <= len(beats) <= TIMELINE_BEATS_MAX:
        errors.append(f"$.beats: {len(beats)} beat(s), expected 1-{TIMELINE_BEATS_MAX}")
    for i, beat in enumerate(beats):
        path = f"$.beats[{i}]"
        _check_text(errors, f"{path}.what", beat["what"], max_words=BEAT_WHAT_MAX_WORDS)
        for key, low, high in (("who", 1, D5_WHO_MAX), ("objects", 0, D5_OBJECTS_MAX),
                               ("knows_after", 0, D5_KNOWS_MAX)):
            if not low <= len(beat[key]) <= high:
                errors.append(f"{path}.{key}: {len(beat[key])} item(s), expected {low}-{high}")
        for j, name in enumerate(beat["objects"]):
            _check_chars(errors, f"{path}.objects[{j}]", name, BEAT_NEW_OBJECT_MAX_CHARS)
        keys = [str(name).strip().casefold() for name in beat["objects"]]
        if len(set(keys)) != len(keys):
            errors.append(f"{path}.objects: an object is named twice")
        for j, item in enumerate(beat["knows_after"]):
            _check_text(errors, f"{path}.knows_after[{j}].knows", item["knows"], max_words=BEAT_KNOWS_AFTER_MAX_WORDS)
    return errors


def d6_schema(prop_names, cast_names) -> dict:
    """The D6 output schema: the props that matter (*prop_names*, the
    story's), and the new ones to create (an owner of *cast_names*, or null)."""
    new_prop = _llm_obj({
        "name": {"type": "string", "description": f"story language, at most {D6_NAME_MAX_CHARS} characters"},
        "one_line": {"type": "string",
                     "description": f"story language, what it is and why it matters, at most {D6_ONE_LINE_MAX_WORDS} words"},
        "owner": _named_or_null(cast_names),
    })
    return _llm_obj({
        "keep": {"type": "array", "description": f"the props that matter, at most {KNOWLEDGE_PROPS_MAX} in all",
                 "items": _named(prop_names)},
        "new_props": {"type": "array", "description": f"0-{D6_NEW_PROPS_MAX} new props", "items": new_prop},
    })


def d6_errors(doc) -> list:
    """Post-validation for a D6 response: at most ``D6_NEW_PROPS_MAX`` new
    props, ``KNOWLEDGE_PROPS_MAX`` in all, each name once, a new prop's name
    and one-line within their caps."""
    errors = validate(doc, d6_schema((), ()))
    if errors:
        return errors
    errors = []
    new = doc["new_props"]
    if len(new) > D6_NEW_PROPS_MAX:
        errors.append(f"$.new_props: {len(new)} prop(s), expected at most {D6_NEW_PROPS_MAX}")
    if len(doc["keep"]) + len(new) > KNOWLEDGE_PROPS_MAX:
        errors.append(f"$: {len(doc['keep'])} kept and {len(new)} new prop(s), expected at most "
                      f"{KNOWLEDGE_PROPS_MAX} in all")
    for i, item in enumerate(new):
        _check_chars(errors, f"$.new_props[{i}].name", item["name"], D6_NAME_MAX_CHARS)
        _check_chars(errors, f"$.new_props[{i}].one_line", item["one_line"], D6_ONE_LINE_MAX_CHARS)
        _check_text(errors, f"$.new_props[{i}].one_line", item["one_line"], max_words=D6_ONE_LINE_MAX_WORDS)
    keys = [str(name).strip().casefold() for name in list(doc["keep"]) + [item["name"] for item in new]]
    if len(set(keys)) != len(keys):
        errors.append("$: a prop is named twice")
    return errors


# ------------------------------------------------- L1 (phase 7 stage 5d, the continuity ledger)
#
# The memory step's second call on a v2 story (DEC-229), after S3: one
# ``LEDGER_STATE_SCHEMA`` entry per character present the episode, written
# into the memory entry's own optional ``ledger`` (``_MEMORY_ENTRY_SCHEMA``,
# stage 5a). ``character``, ``location`` and ``possessions`` are ids the
# call already knows in full (the episode's present cast, the story's
# places and props) and so are enumerated at the schema level, the way
# ``s3_schema`` enumerates hooks and pairs; ``wardrobe_set`` cannot be --
# each character's own look has its own set of ids, and a strict-mode array
# schema cannot vary its items' enum by another field of the same item --
# so it is left a free string here and checked against that one
# character's own ids by the memory step itself (module docstring: "Unknown
# ids refused by the validator").

def l1_schema(char_ids, place_ids, prop_ids) -> dict:
    """The L1 output schema: the continuity ledger after one episode, one
    entry per character of *char_ids* (the episode's present cast).
    *place_ids*/*prop_ids* bound ``location``/``possessions`` to the
    story's own; ``wardrobe_set`` is free text here (see the section
    comment above)."""
    item = _llm_obj({
        "character": _named(char_ids),
        "location": _named_or_null(place_ids),
        "wardrobe_set": {"type": ["string", "null"],
                         "description": "one of that character's own wardrobe set ids shown above, or null"},
        "possessions": {"type": "array", "items": _named(prop_ids)},
        "injuries": {"type": ["string", "null"], "description": f"at most {LEDGER_INJURIES_MAX_WORDS} words, or null"},
        "relationship_notes": {"type": ["string", "null"],
                               "description": f"at most {LEDGER_RELATIONSHIP_NOTES_MAX_WORDS} words, or null"},
    })
    return _llm_obj({
        "ledger": {"type": "array", "description": f"exactly one entry per character, {len(char_ids)} in all",
                  "items": item},
    })


def l1_errors(reply, *, char_ids, place_ids, prop_ids) -> list:
    """Post-validation for an L1 response: the schema (``character``,
    ``location`` and each of ``possessions`` already bound to *char_ids* /
    *place_ids* / *prop_ids* there), exactly one entry per character of
    *char_ids* (none missing, none repeated), then the entries themselves as
    a ledger (``ledger_errors``: the word caps, no prop held twice). A
    ``wardrobe_set`` wrong for its own character is the memory step's own
    check, not this one (it alone knows which ids are whose)."""
    errors = validate(reply, l1_schema(char_ids, place_ids, prop_ids))
    if errors:
        return errors
    ledger = reply["ledger"]
    seen = [item["character"] for item in ledger]
    missing = [cid for cid in char_ids if cid not in seen]
    extra = sorted({cid for cid in seen if seen.count(cid) > 1})
    if missing or extra or len(seen) != len(char_ids):
        parts = []
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        if extra:
            parts.append(f"{', '.join(extra)} listed twice")
        return [f"$.ledger: {'; '.join(parts) or f'{len(seen)} entries, expected {len(char_ids)}'}"]
    as_ledger = {item["character"]: {key: item[key] for key in
                                    ("location", "wardrobe_set", "possessions", "injuries", "relationship_notes")}
                for item in ledger}
    return ledger_errors(as_ledger, "$.ledger")


# ------------------------------------------------- D2 / D3 / R1v2 (phase 7, the look)
#
# One artifact per call (DEC-027): a v2 story's character look (D2, after
# K1), place look (D3, after P1) and prop look (R1v2, after R1). Each reply is
# checked with the document's own ``*_look_errors`` (one set of caps for the
# reply and the stored block), plus the name-leak check: no name of the story
# may appear in a field an image model reads (spec 2.3).

def _name_pattern(name):
    return re.compile(r"(?<!\w)" + r"\s+".join(re.escape(word) for word in name.split()) + r"(?!\w)",
                      re.IGNORECASE)


def _name_leaks(errors, haystacks, names) -> None:
    """One error per (field, name) pair where a name appears in the field."""
    patterns = [(name, _name_pattern(name)) for name in dict.fromkeys(n.strip() for n in names if n and n.strip())]
    for path, text in haystacks:
        if not isinstance(text, str):
            continue
        for name, pattern in patterns:
            if pattern.search(text):
                errors.append(f"{path}: must not mention a name ({name!r})")


def d2_schema(species=False) -> dict:
    """The D2 output schema: one character's look (``CHARACTER_LOOK_SCHEMA``'s
    fields; ``season_change`` an empty string when the look does not change).
    ``presentation`` (A3) and ``bearing`` (stage F2) are optional -- the
    model may leave them out entirely, not just send them empty -- so they
    are not in the ``required`` list.

    Plan 26 stage 7b: with *species* True (a species world) the reply also
    names the head's ``species`` (required); ``"optional"`` accepts one
    without asking for it; the default is the schema it always was."""
    wardrobe = _llm_obj({
        "id": {"type": "string", "description": "lowercase slug, e.g. daily, night_out"},
        "context": {"type": "string", "description": "English, when it is worn, at most 8 words"},
        "items": {"type": "string", "description": "English, what is worn, at most 20 words"},
    })
    properties = {
        "build": {"type": "string", "description": "English, body type and proportions, at most 15 words"},
        "silhouette": {"type": "string", "description": "English, the outline at a glance, at most 12 words"},
        "face": {"type": "string", "description": "English, at most 15 words"},
        "hair": {"type": "string", "description": "English, at most 12 words"},
        "skin_material": {"type": "string", "description": "English, skin or surface, at most 12 words"},
        "height_cm": {"type": "integer", "description": "5-500, the cast's own scale"},
        "palette": {"type": "array", "description": "1-4 short colour names", "items": {"type": "string"}},
        "wardrobe_sets": {"type": "array", "description": "1-3 outfits, the everyday one first", "items": wardrobe},
        "season_change": {"type": "string", "description": "English, at most 20 words, or empty"},
        "presentation": {"type": "string",
                         "description": "English, apparent age and gender presentation, e.g. "
                                        "'woman in her thirties', at most 8 words"},
        "bearing": {"type": "string",
                    "description": "English, posture and how they carry themselves, e.g. 'stands very straight, "
                                   "chin up', at most 10 words"},
    }
    if species:
        properties["species"] = {"type": "string",
                                 "description": "English, the one fruit or vegetable the head is, at most 4 words"}
    optional = ("presentation", "bearing") + (("species",) if species == "optional" else ())
    return _llm_obj(properties, required=[key for key in properties if key not in optional])


def d2_look(doc) -> dict:
    """D2's reply as the stored ``look`` block (an empty season change is
    null; ``presentation`` and ``bearing`` kept only when the reply has a
    non-empty one)."""
    season = (doc.get("season_change") or "").strip()
    look = {
        "build": doc["build"], "silhouette": doc["silhouette"], "face": doc["face"], "hair": doc["hair"],
        "skin_material": doc["skin_material"], "height_cm": doc["height_cm"], "palette": list(doc["palette"]),
        "wardrobe_sets": [{"id": item["id"], "context": item["context"], "items": item["items"]}
                          for item in doc["wardrobe_sets"]],
        "season_change": season or None,
    }
    presentation = (doc.get("presentation") or "").strip()
    if presentation:
        look["presentation"] = presentation
    bearing = (doc.get("bearing") or "").strip()
    if bearing:
        look["bearing"] = bearing
    species = (doc.get("species") or "").strip()
    if species:
        look["species"] = species
    return look


_HUMAN_WORD = re.compile(r"\bhuman\b", re.IGNORECASE)


def d2_errors(doc, names, *, species_world=False) -> list:
    """Post-validation for a D2 response: the stored look's own rules
    (``character_look_errors``) and no name of *names* (the story's
    characters, places and props) in any visual field.

    Plan 26 stage 7b: in a *species_world* the reply must name the head's
    ``species`` and neither the face nor the skin_material may say "human"
    (the head is a whole fruit or vegetable, told why on a retry)."""
    errors = validate(doc, d2_schema(species=True if species_world else "optional"))
    if errors:
        return errors
    errors = character_look_errors(d2_look(doc), "$")
    if species_world:
        if not (doc.get("species") or "").strip():
            errors.append("$.species: a species world needs the head's fruit or vegetable")
        for key in ("face", "skin_material"):
            if _HUMAN_WORD.search(doc[key]):
                errors.append(f"$.{key}: a species world needs a fruit or vegetable head, not a human one")
    haystacks = [(f"$.{key}", doc[key]) for key, _cap in _LOOK_TEXT_FIELDS]
    haystacks += [(f"$.palette[{i}]", colour) for i, colour in enumerate(doc["palette"])]
    haystacks += [(f"$.wardrobe_sets[{i}].items", item["items"]) for i, item in enumerate(doc["wardrobe_sets"])]
    haystacks.append(("$.season_change", doc["season_change"]))
    if doc.get("presentation"):
        haystacks.append(("$.presentation", doc["presentation"]))
    if doc.get("bearing"):
        haystacks.append(("$.bearing", doc["bearing"]))
    _name_leaks(errors, haystacks, names)
    return errors


def d3_schema(variants, prop_names) -> dict:
    """The D3 output schema: one place's layout map, scale note, one light
    per time variant of the place (*variants*, fixed keys) and the props of
    the story that live there, by name (*prop_names*; none: always empty)."""
    lighting = _llm_obj({variant: {"type": "string", "description": "English, at most 15 words"}
                         for variant in variants})
    prop_item = {"type": "string", "enum": list(prop_names)} if prop_names else {"type": "string"}
    return _llm_obj({
        "layout_map": _llm_obj({key: {"type": "string", "description": "English, at most 15 words, or empty"}
                                for key in LAYOUT_MAP_KEYS}),
        "scale_note": {"type": "string", "description": "English, the size against a person, at most 15 words"},
        "lighting": lighting,
        "props_here": {"type": "array", "description": f"0-{D3_PROPS_HERE_MAX} props of the story that live here",
                       "items": prop_item},
    })


def d3_errors(doc, variants, prop_names, names) -> list:
    """Post-validation for a D3 response: the place look's rules on the
    reply, one light per variant exactly, props of the story only (once), no
    name in a visual field."""
    errors = validate(doc, d3_schema(variants, prop_names))
    if errors:
        return errors
    look = {"layout_map": doc["layout_map"], "scale_note": doc["scale_note"], "lighting": doc["lighting"],
            "props_here": []}
    errors = place_look_errors(look, "$")
    if set(doc["lighting"]) != set(variants):
        errors.append(f"$.lighting: expected one light for each of {', '.join(variants)}")
    known = set(prop_names)
    for i, name in enumerate(doc["props_here"]):
        if name not in known:
            errors.append(f"$.props_here[{i}]: {name!r} is no prop of the story")
    if len(set(doc["props_here"])) != len(doc["props_here"]):
        errors.append("$.props_here: a prop is listed twice")
    if len(doc["props_here"]) > D3_PROPS_HERE_MAX:
        errors.append(f"$.props_here: {len(doc['props_here'])} props, expected at most {D3_PROPS_HERE_MAX}")
    haystacks = [(f"$.layout_map.{key}", doc["layout_map"][key]) for key in LAYOUT_MAP_KEYS]
    haystacks.append(("$.scale_note", doc["scale_note"]))
    haystacks += [(f"$.lighting.{key}", value) for key, value in doc["lighting"].items()]
    _name_leaks(errors, haystacks, names)
    return errors


# How many places a D3 reply may dress with props, and how many where-when
# entries an R1v2 reply may give: reply bounds (not the stored document's),
# so the largest reply each ask allows fits its output cap (MAX_TOKENS).
D3_PROPS_HERE_MAX = 3
R1V2_WHERE_WHEN_MAX = 2


def r1v2_schema(cast_names, place_names) -> dict:
    """The R1v2 output schema: one prop's look; ``where_when`` names its
    holder and place by the cast's and the places' names (or null)."""

    def named(names):
        return {"type": ["string", "null"], "enum": list(names) + [None]} if names else {"type": ["string", "null"]}

    entry = _llm_obj({
        "ep": {"type": "integer", "description": "the episode, 1-based"},
        "holder": named(cast_names),
        "place": named(place_names),
        "note": {"type": "string", "description": "English, at most 12 words"},
    })
    return _llm_obj({
        "scale_cm": {"type": "number", "description": "its longest side in centimetres, more than 0"},
        "material": {"type": "string", "description": "English, at most 8 words"},
        "colour": {"type": "string", "description": "English, at most 6 words"},
        "scale_phrase": {"type": "string", "description": "English, its size in words, at most 10 words"},
        "where_when": {"type": "array", "description": f"0-{R1V2_WHERE_WHEN_MAX} entries: where and with whom it is",
                       "items": entry},
    })


def r1v2_errors(doc, names) -> list:
    """Post-validation for an R1v2 response: the prop look's rules on the
    reply (``prop_look_errors``, holders and places checked once mapped) and
    no name in a visual field."""
    errors = validate(doc, r1v2_schema((), ()))
    if errors:
        return errors
    look = {"scale_cm": doc["scale_cm"], "material": doc["material"], "colour": doc["colour"],
            "scale_phrase": doc["scale_phrase"],
            "where_when": [{"ep": entry["ep"], "holder_char_id": None, "place_id": None, "note": entry["note"]}
                           for entry in doc["where_when"]]}
    errors = prop_look_errors(look, "$")
    if len(doc["where_when"]) > R1V2_WHERE_WHEN_MAX:
        errors.append(f"$.where_when: {len(doc['where_when'])} entries, expected at most {R1V2_WHERE_WHEN_MAX}")
    haystacks = [(f"$.{key}", doc[key]) for key in ("material", "colour", "scale_phrase")]
    _name_leaks(errors, haystacks, names)
    return errors


# ------------------------------------------------------------------------- S1 (season_arc_v1 skeleton)

S1_SUMMARY_MAX_WORDS = 25


def s1_schema(episodes) -> dict:
    """The S1 output schema (spec 4.2, row S1): exactly *episodes* arc
    entries, function + one-line summary each. *episodes* is baked into
    the schema's description (an exact count the prompt text also states),
    not left to the post-validator alone.
    """
    entry = _llm_obj({
        "ep": {"type": "integer", "description": "the episode number, 1-based"},
        "function": {"type": "string", "enum": list(ARC_FUNCTIONS)},
        "summary": {"type": "string", "description": "story language, at most 25 words"},
    })
    return _llm_obj({
        "arc": {"type": "array", "description": f"exactly {episodes} entries, one per episode", "items": entry},
    })


def s1_errors(doc, episodes) -> list:
    """Post-validation for an S1 response, beyond what ``s1_schema`` can
    express: exactly *episodes* entries, numbered 1..episodes in order,
    episode 1 is ``setup``, the last is ``climax_and_reset``, and one entry
    is ``midpoint_twist`` (spec 2.6)."""
    errors = validate(doc, s1_schema(episodes))
    if errors:
        return errors

    errors = []
    arc = doc["arc"]
    if len(arc) != episodes:
        errors.append(f"$.arc: {len(arc)} entries, expected exactly {episodes}")
    eps = [entry["ep"] for entry in arc]
    if eps != list(range(1, episodes + 1)):
        errors.append(f"$.arc: episodes {eps} must be exactly 1..{episodes} in order")
    for i, entry in enumerate(arc):
        _check_text(errors, f"$.arc[{i}].summary", entry["summary"], max_words=S1_SUMMARY_MAX_WORDS)

    if arc:
        if arc[0]["function"] != "setup":
            errors.append("$.arc[0].function: episode 1 must be 'setup'")
        if arc[-1]["function"] != "climax_and_reset":
            errors.append(f"$.arc[{len(arc) - 1}].function: the last episode must be 'climax_and_reset'")
        if "midpoint_twist" not in [entry["function"] for entry in arc]:
            errors.append("$.arc: no episode has function 'midpoint_twist'")

    return errors


def s1_archetype_schema(episodes, archetype_ids) -> dict:
    """S1's output schema on a v2 story (plan 20 stage 2): the season's plot
    archetypes first -- a primary and a secondary (null for none), each
    among *archetype_ids* -- then ``s1_schema``'s entries, each naming the
    archetype whose beat it plays."""
    ids = list(archetype_ids)
    entry = _llm_obj({
        "ep": {"type": "integer", "description": "the episode number, 1-based"},
        "function": {"type": "string", "enum": list(ARC_FUNCTIONS)},
        "archetype": {"type": "string", "enum": ids, "description": "the primary or the secondary"},
        "summary": {"type": "string", "description": "story language, at most 25 words"},
    })
    return _llm_obj({
        "archetypes": _llm_obj({
            "primary": {"type": "string", "enum": ids},
            "secondary": {"type": ["string", "null"], "enum": ids + [None],
                          "description": "one that pairs well with the primary, or null"},
        }),
        "arc": {"type": "array", "description": f"exactly {episodes} entries, one per episode", "items": entry},
    })


def s1_archetype_errors(doc, episodes, pairs) -> list:
    """Post-validation for S1's v2 reply: ``s1_errors``' rules on the arc,
    plus (*pairs*: ``{archetype id: [ids it pairs well with]}``, the
    library) the secondary, when named, one the primary pairs well with;
    each entry on the primary or the secondary; episode 1 and the last on
    the primary (the spine opens and closes the season); a named secondary
    playing at least one episode."""
    errors = validate(doc, s1_archetype_schema(episodes, list(pairs)))
    if errors:
        return errors

    arc = [{key: entry[key] for key in ("ep", "function", "summary")} for entry in doc["arc"]]
    errors = s1_errors({"arc": arc}, episodes)
    primary, secondary = doc["archetypes"]["primary"], doc["archetypes"]["secondary"]
    if secondary is not None and secondary not in pairs[primary]:
        errors.append(f"$.archetypes.secondary: {secondary!r} does not pair with {primary!r} "
                      f"(it pairs with {', '.join(pairs[primary]) or 'none'})")
    chosen = [primary] if secondary is None else [primary, secondary]
    for i, entry in enumerate(doc["arc"]):
        if entry["archetype"] not in chosen:
            errors.append(f"$.arc[{i}].archetype: {entry['archetype']!r} is neither the primary nor the secondary")
    if doc["arc"]:
        for i in sorted({0, len(doc["arc"]) - 1}):
            if doc["arc"][i]["archetype"] != primary:
                errors.append(f"$.arc[{i}].archetype: the first and the last episode follow the primary, {primary!r}")
        if secondary is not None and secondary not in [entry["archetype"] for entry in doc["arc"]]:
            errors.append(f"$.archetypes.secondary: {secondary!r} plays no episode; give it one or name none")
    return errors


# ------------------------------------------------------------------------- S2 (expand one arc entry)

def s2_schema(cast_names) -> dict:
    """The S2 output schema (spec 4.2, row S2): expand one arc entry.
    ``cast_names`` constrains ``characters`` to the existing cast."""
    character_schema = {"type": "string", "enum": list(cast_names)} if cast_names else {"type": "string"}
    return _llm_obj({
        "summary": {"type": "string", "description": "story language, at most 60 words"},
        "open_hooks_in": {
            "type": "array",
            "description": "0-3 hooks, each at most 15 words",
            "items": {"type": "string"},
        },
        "open_hooks_out": {
            "type": "array",
            "description": "1-3 hooks, each at most 15 words",
            "items": {"type": "string"},
        },
        "characters": {
            "type": "array",
            "description": "1-5 of the existing cast",
            "items": character_schema,
        },
    })


def s2_errors(doc) -> list:
    """Post-validation for an S2 response, beyond what ``s2_schema`` can express."""
    errors = validate(doc, s2_schema(()))
    if errors:
        return errors

    errors = []
    _check_text(errors, "$.summary", doc["summary"], max_words=ARC_SUMMARY_MAX_WORDS)

    hooks_in = doc["open_hooks_in"]
    if len(hooks_in) > 3:
        errors.append(f"$.open_hooks_in: {len(hooks_in)} hook(s), expected at most 3")
    for i, hook in enumerate(hooks_in):
        _check_text(errors, f"$.open_hooks_in[{i}]", hook, max_words=15)

    hooks_out = doc["open_hooks_out"]
    if not (1 <= len(hooks_out) <= 3):
        errors.append(f"$.open_hooks_out: {len(hooks_out)} hook(s), expected 1-3")
    for i, hook in enumerate(hooks_out):
        _check_text(errors, f"$.open_hooks_out[{i}]", hook, max_words=15)

    characters = doc["characters"]
    if not (1 <= len(characters) <= 5):
        errors.append(f"$.characters: {len(characters)} character(s), expected 1-5")

    return errors


# ------------------------------------------------------------------------- U1 (vision: design reference)

U1_MAX_WORDS = 40


def u1_schema() -> dict:
    """The U1 output schema (spec 4.2, row U1): describe an uploaded design
    reference as appearance notes for K1 to fold into a character."""
    return _llm_obj({
        "appearance_notes": {
            "type": "string",
            "description": (
                "English, at most 40 words: body shape, colours, clothing, "
                "accessories, distinctive marks"
            ),
        },
    })


def u1_errors(doc) -> list:
    """Post-validation for a U1 response, beyond what ``u1_schema`` can express."""
    errors = validate(doc, u1_schema())
    if errors:
        return errors

    errors = []
    _check_text(errors, "$.appearance_notes", doc["appearance_notes"], max_words=U1_MAX_WORDS)
    return errors


# ============================================================ LLM output schemas (spec 4.2) -- phase 5
#
# S3/F1/N1 (plan 11 stage 2): same strict-mode subset as the phase-2 section
# above, model schema + ``*_errors`` post-validator co-located here rather
# than in ``prompts.py`` (unlike the phase-3/4 E1-E4/T1/T1r/M1 builders,
# these three read straight off this module's own stage-1 constants --
# ``RECAP_MAX_WORDS``, ``HOOKS_OPENED_MAX``, ``HOOK_MAX_LENGTH``,
# ``RELATIONSHIP_DELTA_MAX_WORDS``, ``CHARACTER_ROLES``, ``TWIST_HOOKS_MAX``
# -- so keeping the validator beside them means one file to change when a
# cap moves, not two kept in sync by hand).
#
# A repair function per prompt applies the French-elision fix (DEC-144) to
# every model-written text field before its ``*_errors`` runs (a merged
# elision changes a word count, so it must happen first -- the same order
# ``steps/script.py``'s ``_repair_e*_reply`` uses). The rule lives here, once:
# this module cannot import ``prompts`` (``prompts`` imports this module), so
# ``prompts.repair_fr_elisions`` -- the name every step calls -- is this same
# function.
#
# The elidable words this repairs (spec 4.2, F1): le/la, de, je, ce, ne, me,
# se (one letter once their own vowel is dropped) and que (only its "e"
# drops, not the "u"). Matched case-insensitively and standalone -- neither
# lookaround uses ``\w`` loosely: "des" and "quand" never match, only a
# whole word spelled exactly "d" or "qu" -- followed by whitespace and a
# word starting with a vowel or "h" (accented vowels included). "y", "a" and
# "à" are never treated as a vowel-starting word to elide *into* (the
# human's own choice: "il y a" is a different word, not a dropped
# apostrophe, and is not worth the false positives).
_FR_ELIDABLE_RE = re.compile(r"(?<!\w)(qu|[ldjcnms])(?!\w)([ \t]+)(\S+)", re.IGNORECASE)
_FR_VOWEL_OR_H = set("aeiouAEIOUhH" "àâäæçéèêëîïôöœùûü" "ÀÂÄÆÇÉÈÊËÎÏÔÖŒÙÛÜ")
_FR_NEVER_ELIDED = {"y", "a", "à"}


def _fr_lead_word(token: str) -> str:
    """The leading run of letters of *token* (stops at the first digit,
    punctuation mark or apostrophe): what the elision check itself reads,
    a trailing comma or period never part of the question."""
    match = re.match(r"[^\W\d_]+", token)
    return match.group(0) if match else ""


def _fr_elision_sub(match) -> str:
    prefix, word = match.group(1), match.group(3)
    lead = _fr_lead_word(word)
    if not lead or lead.lower() in _FR_NEVER_ELIDED or lead[0] not in _FR_VOWEL_OR_H:
        return match.group(0)
    return f"{prefix}'{word}"


def repair_fr_elisions(text: str) -> str:
    """Deterministic repair of a French reply's dropped elision apostrophe
    (spec 4.2, F1; DEC-144): ``"l alliance"`` -> ``"l'alliance"``, ``"d Etat"`` ->
    ``"d'Etat"``, ``"m échappent"`` -> ``"m'échappent"``. No call, cannot
    fail (pure text -> text), and never touches *text* that already carries
    an apostrophe anywhere, straight or curly -- a reply with one correct
    elision and one dropped one is left exactly as it is, the safer of the
    two ways for this to be wrong.

    Applied by the steps (never by a builder) to every model-written field
    they store when the story's language is ``"fr"``; ``prompts`` exposes it
    as ``prompts.repair_fr_elisions``."""
    if "'" in text or "’" in text:
        return text
    return _FR_ELIDABLE_RE.sub(_fr_elision_sub, text)


def _repair_text_field(value):
    return repair_fr_elisions(value) if isinstance(value, str) else value


# ------------------------------------------------------------------------- S3 (series_memory_entry)

def s3_schema(open_hooks, pairs) -> dict:
    """The S3 output schema (spec 2.6, 4.2, row S3): the series memory entry
    for an approved episode -- recap, hooks opened and closed, relationship
    deltas.

    *open_hooks* are the hooks open before this episode
    (``series_memory.open_hooks_before``), verbatim: ``hooks_closed`` is an
    enum of exactly those strings, so a hook is only ever closed by its
    exact text (spec 2.6), never fuzzy-matched -- with none open, the item
    schema falls back to a bare string (DEC-171's empty-enum precedent: an
    empty ``enum`` is not valid JSON Schema), and the ask asks for an
    always-empty list instead.

    *pairs* are every sorted ``"<char_a>|<char_b>"`` combination of the
    cast. ``relationship_deltas`` cannot be the stored dict shape here: the
    strict-mode subset this module's LLM schemas share (the note above
    ``_llm_obj``) has no way to enumerate an *object's keys*, only a
    string's values -- so it is an array of ``{pair, text}`` with ``pair``
    the enum, which ``s3_errors``/the memory step turn into the
    ``memory_entry_v1`` dict.
    """
    hooks_closed_item = {"type": "string", "enum": list(open_hooks)} if open_hooks else {"type": "string"}
    pair_item = {"type": "string", "enum": list(pairs)} if pairs else {"type": "string"}
    delta = _llm_obj({
        "pair": pair_item,
        "text": {"type": "string", "description": f"at most {RELATIONSHIP_DELTA_MAX_WORDS} words"},
    })
    return _llm_obj({
        "recap": {"type": "string", "description": f"at most {RECAP_MAX_WORDS} words"},
        "hooks_opened": {
            "type": "array",
            "description": f"0-{HOOKS_OPENED_MAX} new hooks, each at most {HOOK_MAX_LENGTH} characters",
            "items": {"type": "string"},
        },
        "hooks_closed": {
            "type": "array",
            "description": ("which of the open hooks above this episode resolves"
                            if open_hooks else "always []: there are no open hooks yet"),
            "items": hooks_closed_item,
        },
        "relationship_deltas": {
            "type": "array",
            "description": (f"0-{RELATIONSHIP_DELTAS_MAX} updates, one per pair that changed"
                            if pairs else "always []: fewer than two characters exist yet"),
            "items": delta,
        },
    })


def s3_errors(reply, *, open_hooks, pairs) -> list:
    """Post-validation for an S3 reply, beyond what ``s3_schema`` can
    express: the recap's word cap; each ``hooks_opened`` item's character
    cap, the array's own count cap, and no duplicate; each ``hooks_closed``
    item exactly one of *open_hooks*, no duplicate, and never also in
    ``hooks_opened``; at most ``RELATIONSHIP_DELTAS_MAX`` relationship
    deltas, each ``pair`` exactly one of *pairs*, no pair twice, and its
    ``text`` within ``RELATIONSHIP_DELTA_MAX_WORDS``."""
    errors = validate(reply, s3_schema(open_hooks, pairs))
    if errors:
        return errors

    errors = []
    _check_text(errors, "$.recap", reply["recap"], max_words=RECAP_MAX_WORDS)

    opened = reply["hooks_opened"]
    if len(opened) > HOOKS_OPENED_MAX:
        errors.append(f"$.hooks_opened: {len(opened)} hook(s), expected at most {HOOKS_OPENED_MAX}")
    for i, hook in enumerate(opened):
        _check_chars(errors, f"$.hooks_opened[{i}]", hook, HOOK_MAX_LENGTH)
    for hook in sorted({h for h in opened if isinstance(h, str) and opened.count(h) > 1}):
        errors.append(f"$.hooks_opened: {hook!r} is listed twice")

    closed = reply["hooks_closed"]
    for i, hook in enumerate(closed):
        if hook not in open_hooks:
            errors.append(f"$.hooks_closed[{i}]: {hook!r} is not an open hook "
                          "(a hook is closed only by its exact text)")
    for hook in sorted({h for h in closed if isinstance(h, str) and closed.count(h) > 1}):
        errors.append(f"$.hooks_closed: {hook!r} is listed twice")
    for hook in sorted(set(opened) & set(closed)):
        errors.append(f"$: {hook!r} is both opened and closed")

    deltas = reply["relationship_deltas"]
    if len(deltas) > RELATIONSHIP_DELTAS_MAX:
        errors.append(f"$.relationship_deltas: {len(deltas)} update(s), expected at most {RELATIONSHIP_DELTAS_MAX}")
    seen_pairs = set()
    for i, item in enumerate(deltas):
        pair = item["pair"]
        if pair not in pairs:
            errors.append(f"$.relationship_deltas[{i}].pair: {pair!r} is not one of the cast's pairs")
        elif pair in seen_pairs:
            errors.append(f"$.relationship_deltas[{i}].pair: {pair!r} is listed twice")
        seen_pairs.add(pair)
        _check_text(errors, f"$.relationship_deltas[{i}].text", item["text"],
                    max_words=RELATIONSHIP_DELTA_MAX_WORDS)
    return errors


def repair_s3_reply(reply, language):
    """A copy of *reply* with the French elision fix (DEC-144) applied to
    ``recap``, every ``hooks_opened`` item and every ``relationship_deltas``
    item's ``text`` -- before ``s3_errors`` runs (a merged elision changes a
    word count). ``hooks_closed`` is never touched: it must match one of
    *open_hooks* byte for byte. A story whose language is not French comes
    back an unmodified copy."""
    reply = copy.deepcopy(reply)
    if language != "fr" or not isinstance(reply, dict):
        return reply
    if isinstance(reply.get("recap"), str):
        reply["recap"] = repair_fr_elisions(reply["recap"])
    if isinstance(reply.get("hooks_opened"), list):
        reply["hooks_opened"] = [_repair_text_field(hook) for hook in reply["hooks_opened"]]
    if isinstance(reply.get("relationship_deltas"), list):
        for item in reply["relationship_deltas"]:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                item["text"] = repair_fr_elisions(item["text"])
    return reply


# ------------------------------------------------------------------------- F1 (audience_feedback_digest)

# Not stated by the spec table (only "60 words + 3 directions" is): a
# direction is one suggested pivot sentence, not a scene -- capped the same
# order of magnitude as a hook (15 words) or a K1 want/fear (25 words); 25
# was picked so three directions plus the digest stay comfortably inside
# F1's own 250-token spec cap once measured (see prompts.py's MAX_TOKENS
# comment).
F1_DIRECTION_MAX_WORDS = 25


def f1_schema() -> dict:
    """The F1 output schema (spec 2.6, 4.2, row F1): a short digest of
    pasted audience feedback plus exactly ``FEEDBACK_DIRECTIONS`` suggested
    directions for the next episode."""
    return _llm_obj({
        "digest": {"type": "string", "description": f"at most {FEEDBACK_DIGEST_MAX_WORDS} words"},
        "directions": {
            "type": "array",
            "description": f"exactly {FEEDBACK_DIRECTIONS} directions, each at most {F1_DIRECTION_MAX_WORDS} words",
            "items": {"type": "string"},
        },
    })


def f1_errors(reply) -> list:
    """Post-validation for an F1 reply, beyond what ``f1_schema`` can
    express: the digest's word cap, exactly ``FEEDBACK_DIRECTIONS``
    directions, each within ``F1_DIRECTION_MAX_WORDS``."""
    errors = validate(reply, f1_schema())
    if errors:
        return errors

    errors = []
    _check_text(errors, "$.digest", reply["digest"], max_words=FEEDBACK_DIGEST_MAX_WORDS)
    directions = reply["directions"]
    if len(directions) != FEEDBACK_DIRECTIONS:
        errors.append(f"$.directions: {len(directions)} direction(s), expected exactly {FEEDBACK_DIRECTIONS}")
    for i, direction in enumerate(directions):
        _check_text(errors, f"$.directions[{i}]", direction, max_words=F1_DIRECTION_MAX_WORDS)
    return errors


def repair_f1_reply(reply, language):
    """A copy of *reply* with the French elision fix applied to ``digest``
    and every ``directions`` item, before ``f1_errors`` runs. See
    :func:`repair_s3_reply`."""
    reply = copy.deepcopy(reply)
    if language != "fr" or not isinstance(reply, dict):
        return reply
    if isinstance(reply.get("digest"), str):
        reply["digest"] = repair_fr_elisions(reply["digest"])
    if isinstance(reply.get("directions"), list):
        reply["directions"] = [_repair_text_field(d) for d in reply["directions"]]
    return reply


# ------------------------------------------------------------------------- N1 (next_episode_proposals)

# The proposed character/twist fields mirror next_proposals_v1's own caps
# (schemas.py's ``_PROPOSED_CHARACTER_SCHEMA``/``_PROPOSED_TWIST_SCHEMA`,
# themselves character_v1's/season_arc_v1's own): character-counted, not
# word-counted, so a model-facing reply and the stored proposal agree on
# what "too long" means.
N1_NAME_MAX_CHARS = 60
N1_ONE_LINE_MAX_CHARS = 200
N1_WHY_MAX_CHARS = 300
N1_ARCHETYPE_MAX_CHARS = 60


def n1_schema(target_eps) -> dict:
    """The N1 output schema (spec 2.6, 4.2, row N1): up to
    ``PROPOSALS_MAX_CHARACTERS`` new characters and up to
    ``PROPOSALS_MAX_TWISTS`` twists for the episode after the one memory was
    written from.

    *target_eps* are the arc's own episodes after that one (what a twist may
    target, spec 4.2): enumerated the same way S2's ``characters`` enums the
    existing cast, never free text, so Python never has to guess which arc
    entry an accepted twist amends. None yet in the arc falls back to the
    bare-integer schema (DEC-171's empty-enum precedent) and the ask asks
    for an always-empty ``twists`` instead.
    """
    character = _llm_obj({
        "name": {"type": "string", "description": f"at most {N1_NAME_MAX_CHARS} characters"},
        "role": {"type": "string", "enum": list(CHARACTER_ROLES)},
        "one_line": {"type": "string", "description": f"at most {N1_ONE_LINE_MAX_CHARS} characters"},
        "archetype": {"type": ["string", "null"], "description": f"at most {N1_ARCHETYPE_MAX_CHARS} characters, or null"},
        "why": {"type": "string", "description": f"at most {N1_WHY_MAX_CHARS} characters"},
    })
    target_ep_item = {"type": "integer", "enum": list(target_eps)} if target_eps else {"type": "integer"}
    twist = _llm_obj({
        "target_ep": target_ep_item,
        "summary": {"type": "string", "description": f"at most {ARC_SUMMARY_MAX_WORDS} words"},
        "open_hooks_out": {
            "type": "array",
            "description": f"0-{TWIST_HOOKS_MAX} new hooks this leaves open, each at most {HOOK_MAX_LENGTH} characters",
            "items": {"type": "string"},
        },
        "why": {"type": "string", "description": f"at most {N1_WHY_MAX_CHARS} characters"},
    })
    return _llm_obj({
        "characters": {
            "type": "array",
            "description": f"0-{PROPOSALS_MAX_CHARACTERS} new characters, prefer role recurring or guest",
            "items": character,
        },
        "twists": {
            "type": "array",
            "description": (f"0-{PROPOSALS_MAX_TWISTS} twists, target_ep one of the episodes above"
                            if target_eps else "always []: there is no episode after this one in the arc yet"),
            "items": twist,
        },
    })


def n1_errors(reply, *, target_eps) -> list:
    """Post-validation for an N1 reply, beyond what ``n1_schema`` can
    express: at most ``PROPOSALS_MAX_CHARACTERS`` characters and
    ``PROPOSALS_MAX_TWISTS`` twists, each proposed field's character cap,
    each twist's ``target_ep`` exactly one of *target_eps*, its
    ``summary``'s word cap and ``open_hooks_out``'s count and character cap."""
    errors = validate(reply, n1_schema(target_eps))
    if errors:
        return errors

    errors = []
    characters = reply["characters"]
    if len(characters) > PROPOSALS_MAX_CHARACTERS:
        errors.append(f"$.characters: {len(characters)} character(s), expected at most {PROPOSALS_MAX_CHARACTERS}")
    for i, item in enumerate(characters):
        path = f"$.characters[{i}]"
        _check_chars(errors, f"{path}.name", item["name"], N1_NAME_MAX_CHARS)
        _check_chars(errors, f"{path}.one_line", item["one_line"], N1_ONE_LINE_MAX_CHARS)
        _check_chars(errors, f"{path}.why", item["why"], N1_WHY_MAX_CHARS)
        if item["archetype"] is not None:
            _check_chars(errors, f"{path}.archetype", item["archetype"], N1_ARCHETYPE_MAX_CHARS)

    twists = reply["twists"]
    if len(twists) > PROPOSALS_MAX_TWISTS:
        errors.append(f"$.twists: {len(twists)} twist(s), expected at most {PROPOSALS_MAX_TWISTS}")
    for i, item in enumerate(twists):
        path = f"$.twists[{i}]"
        if item["target_ep"] not in target_eps:
            errors.append(f"{path}.target_ep: {item['target_ep']} is not one of {target_eps}")
        _check_text(errors, f"{path}.summary", item["summary"], max_words=ARC_SUMMARY_MAX_WORDS)
        _check_chars(errors, f"{path}.why", item["why"], N1_WHY_MAX_CHARS)
        hooks = item["open_hooks_out"]
        if len(hooks) > TWIST_HOOKS_MAX:
            errors.append(f"{path}.open_hooks_out: {len(hooks)} hook(s), expected at most {TWIST_HOOKS_MAX}")
        for j, hook in enumerate(hooks):
            _check_chars(errors, f"{path}.open_hooks_out[{j}]", hook, HOOK_MAX_LENGTH)
    return errors


def n1_v2_schema(target_eps, variant_char_ids) -> dict:
    """N1v2's schema (plan 23 stage D5): :func:`n1_schema` with each twist's
    ``variant`` -- null, or the appearance variant of one existing character
    (*variant_char_ids*: the ones that may gain one) it brings ``{char_id,
    label, delta_text}``. Without such a character the twist's variant is
    always null."""
    schema = n1_schema(target_eps)
    twist = schema["properties"]["twists"]["items"]
    char_item = ({"type": "string", "enum": list(variant_char_ids)} if variant_char_ids else {"type": "string"})
    variant = _llm_obj({
        "char_id": char_item,
        "label": {"type": "string", "description": f"at most {VARIANT_LABEL_MAX_CHARS} characters"},
        "delta_text": {"type": "string",
                       "description": f"what changes in the look, at most {VARIANT_DELTA_MAX_WORDS} words, no name"},
    })
    variant["type"] = ["object", "null"]
    twist["properties"]["variant"] = variant
    twist["required"] = list(twist["properties"])
    return schema


def n1_v2_errors(reply, *, target_eps, variant_char_ids) -> list:
    """Post-validation for an N1v2 reply: :func:`n1_errors`' checks on the
    reply without the twists' variants, then each variant's: a character of
    *variant_char_ids*, the label's character cap, the delta's word cap, at
    most one variant per character in the reply."""
    errors = validate(reply, n1_v2_schema(target_eps, variant_char_ids))
    if errors:
        return errors
    bare = dict(reply, twists=[{key: value for key, value in twist.items() if key != "variant"}
                               for twist in reply["twists"]])
    errors = n1_errors(bare, target_eps=target_eps)
    seen = set()
    for i, twist in enumerate(reply["twists"]):
        variant = twist.get("variant")
        if variant is None:
            continue
        path = f"$.twists[{i}].variant"
        if variant["char_id"] not in variant_char_ids:
            errors.append(f"{path}.char_id: {variant['char_id']!r} is not one of {list(variant_char_ids)}")
        elif variant["char_id"] in seen:
            errors.append(f"{path}.char_id: {variant['char_id']!r} gains a variant in another twist already")
        seen.add(variant["char_id"])
        _check_chars(errors, f"{path}.label", variant["label"], VARIANT_LABEL_MAX_CHARS)
        _check_text(errors, f"{path}.delta_text", variant["delta_text"], max_words=VARIANT_DELTA_MAX_WORDS)
    return errors


def repair_n1_reply(reply, language):
    """A copy of *reply* with the French elision fix applied to every
    proposed character's ``name``/``one_line``/``why``/``archetype`` and
    every twist's ``summary``/``why``/``open_hooks_out`` items, before
    ``n1_errors`` runs. See :func:`repair_s3_reply`."""
    reply = copy.deepcopy(reply)
    if language != "fr" or not isinstance(reply, dict):
        return reply
    for item in reply.get("characters") or []:
        if not isinstance(item, dict):
            continue
        for field in ("name", "one_line", "why", "archetype"):
            if isinstance(item.get(field), str):
                item[field] = repair_fr_elisions(item[field])
    for item in reply.get("twists") or []:
        if not isinstance(item, dict):
            continue
        for field in ("summary", "why"):
            if isinstance(item.get(field), str):
                item[field] = repair_fr_elisions(item[field])
        if isinstance(item.get("open_hooks_out"), list):
            item["open_hooks_out"] = [_repair_text_field(hook) for hook in item["open_hooks_out"]]
        variant = item.get("variant")  # N1v2 (plan 23 stage D5)
        if isinstance(variant, dict):
            for field in ("label", "delta_text"):
                if isinstance(variant.get(field), str):
                    variant[field] = repair_fr_elisions(variant[field])
    return reply
