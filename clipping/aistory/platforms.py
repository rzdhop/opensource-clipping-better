"""The platform presets of the shot brief (plan 22 stage 5): where the human
makes their own clips -- Google Flow (Veo 3.1) and Higgsfield / Freepik.

Each preset is a data file, ``templates/platforms/<name>.json``
(``platform_preset_v1``), so a platform's drift in prompt syntax, lengths,
frames (``aspects``, plan 23 stage B7) or credits is one edit, never code. :func:`load` reads and validates one
(:func:`preset_errors`: a small closed schema); :func:`model_of` picks the
model a shot is made on (the preset's ``default_model``; a speaking shot in a
language the model does not speak moves to the first model that does);
:func:`length_for` the length to pick; :func:`credits_line` what a number of
clips costs in the platform's credits ("≈ 240 Flow credits on AI Pro").

Stdlib only (DEC-012).
"""

from __future__ import annotations

import functools
import json
import os

SCHEMA = "platform_preset_v1"
PLATFORMS = ("flow", "higgsfield")
DEFAULT_PLATFORM = "flow"
PLATFORMS_DIR = os.path.join(os.path.dirname(__file__), "templates", "platforms")
# Plan 23 stage B7: the frames a preset may list in ``aspects`` (a story is
# briefed on a platform only when its frame is one of them).
FRAMES = ("9:16", "16:9", "1:1")

_REQUIRED = ("$schema", "platform", "name", "checked_at", "url", "default_model", "models", "aspects", "length_note",
             "modes", "prompt_order", "dialogue_syntax", "ambient_syntax", "sfx_syntax", "closing", "prompt_notes",
             "where_to_paste", "credits")
# The key ``aspects`` replaced (plan 23 stage B7): refused with that said, the rest still checked.
_LEGACY_ASPECT = "aspect"
_MODEL_KEYS = ("label", "max_references", "lengths", "speech", "languages", "reference_syntax")
_CREDIT_KEYS = ("unit", "per_clip", "default", "labels", "note")


class PresetError(ValueError):
    """A preset file that cannot be used; the message names what is wrong."""


def preset_errors(doc) -> list:
    """What is wrong with the preset *doc* (an empty list: nothing)."""
    if not isinstance(doc, dict):
        return ["a preset is a JSON object"]
    errors = [f"missing {key!r}" for key in _REQUIRED if key not in doc]
    unknown = sorted(set(doc) - set(_REQUIRED) - {_LEGACY_ASPECT})
    if unknown:
        errors.append(f"unknown key(s) {', '.join(unknown)}")
    if errors:
        return errors
    if _LEGACY_ASPECT in doc:
        # Plan 23 stage B7: the one frame a preset used to name is now the list it makes.
        errors.append("'aspect' is replaced by 'aspects', the list of frames the platform makes")
    if doc["$schema"] != SCHEMA:
        errors.append(f"$schema must be {SCHEMA!r}")
    if doc["platform"] not in PLATFORMS:
        errors.append(f"platform must be one of {', '.join(PLATFORMS)}")
    aspects = doc["aspects"]
    if (not isinstance(aspects, list) or not aspects or "9:16" not in aspects
            or not all(aspect in FRAMES for aspect in aspects) or len(set(aspects)) != len(aspects)):
        errors.append(f"aspects must list the frames the platform makes, 9:16 among them (of {', '.join(FRAMES)})")
    models = doc["models"]
    if not isinstance(models, dict) or not models:
        errors.append("models must be a non-empty object")
        models = {}
    if doc["default_model"] not in models:
        errors.append(f"default_model {doc['default_model']!r} is not one of the models")
    for name, model in models.items():
        where = f"models.{name}"
        if not isinstance(model, dict) or sorted(model) != sorted(_MODEL_KEYS):
            errors.append(f"{where} must hold exactly {', '.join(_MODEL_KEYS)}")
            continue
        count = model["max_references"]
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 14:
            errors.append(f"{where}.max_references must be a whole number from 1 to 14")
        lengths = model["lengths"]
        if (not isinstance(lengths, list) or not lengths
                or not all(isinstance(n, int) and not isinstance(n, bool) and 2 <= n <= 30 for n in lengths)
                or lengths != sorted(set(lengths))):
            errors.append(f"{where}.lengths must be whole seconds from 2 to 30, ascending")
        if not isinstance(model["speech"], bool):
            errors.append(f"{where}.speech must be true or false")
        languages = model["languages"]
        if languages is not None and (not isinstance(languages, list)
                                      or not all(isinstance(code, str) and code for code in languages)):
            errors.append(f"{where}.languages must be null (every language) or a list of language codes")
        syntax = model["reference_syntax"]
        if syntax is not None and (not isinstance(syntax, str) or "{n}" not in syntax or "{label}" not in syntax):
            errors.append(f"{where}.reference_syntax must be null or a template naming {{n}} and {{label}}")
    modes = doc["modes"]
    if not isinstance(modes, dict) or sorted(modes) != ["keyframe", "references"]:
        errors.append("modes must hold exactly keyframe and references")
    for key in ("dialogue_syntax",):
        for field in ("{speaker}", "{voice}", "{line}"):
            if field not in str(doc[key]):
                errors.append(f"{key} must name {field}")
    for key in ("name", "checked_at", "url", "length_note", "closing", "where_to_paste", "ambient_syntax",
                "sfx_syntax"):
        if not isinstance(doc[key], str) or not doc[key].strip():
            errors.append(f"{key} must be a non-empty string")
    if not isinstance(doc["prompt_notes"], list) or not all(isinstance(n, str) and n for n in doc["prompt_notes"]):
        errors.append("prompt_notes must be a list of sentences")
    credits = doc["credits"]
    if not isinstance(credits, dict) or sorted(credits) != sorted(_CREDIT_KEYS):
        errors.append(f"credits must hold exactly {', '.join(_CREDIT_KEYS)}")
    else:
        per_clip = credits["per_clip"]
        if not isinstance(per_clip, dict) or not all(
                isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in per_clip.values()):
            errors.append("credits.per_clip must map a plan to whole credits a clip")
        elif credits["default"] is not None and credits["default"] not in per_clip:
            errors.append("credits.default must be null or a key of credits.per_clip")
    return errors


@functools.lru_cache(maxsize=None)
def _read(name):
    path = os.path.join(PLATFORMS_DIR, f"{name}.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load(name=None) -> dict:
    """The preset *name* (:data:`PLATFORMS`; None: :data:`DEFAULT_PLATFORM`),
    validated; :class:`PresetError` for an unknown name or a bad file."""
    name = name or DEFAULT_PLATFORM
    if name not in PLATFORMS:
        raise PresetError(f"Unknown platform {name!r}. Known: {', '.join(PLATFORMS)}.")
    try:
        doc = _read(name)
    except (OSError, ValueError) as exc:
        raise PresetError(f"The {name} preset cannot be read ({exc}).") from None
    errors = preset_errors(doc)
    if errors:
        raise PresetError(f"The {name} preset is not valid: {'; '.join(errors)}.")
    if doc["platform"] != name:
        raise PresetError(f"The {name} preset names platform {doc['platform']!r}.")
    return json.loads(json.dumps(doc))


def model_of(preset, *, speaks=False, language=None) -> tuple:
    """``(name, model)`` a shot is made on: the preset's default model, or --
    a speaking shot in a language that model does not speak -- the first
    model that speaks it."""
    models = preset["models"]
    chosen = preset["default_model"]

    def speaks_it(model):
        return model["speech"] and (model["languages"] is None or (language or "") in model["languages"])

    if speaks and not speaks_it(models[chosen]):
        chosen = next((name for name, model in models.items() if speaks_it(model)), chosen)
    return chosen, models[chosen]


def length_for(model, clip_s) -> int:
    """The length to pick on *model* for a shot planned at *clip_s*: the
    smallest it sells that holds it, else its longest."""
    lengths = model["lengths"]
    need = int(round(float(clip_s or 0)))
    return next((length for length in lengths if length >= need), lengths[-1])


def credits_of(preset, clips, plan=None):
    """``(credits, label)`` *clips* clips cost on the preset's *plan* (its
    ``credits.default`` when None), or ``(None, None)`` when the preset prices
    none."""
    credits = preset["credits"]
    plan = plan or credits["default"]
    if plan is None or plan not in credits["per_clip"]:
        return None, None
    return int(clips) * int(credits["per_clip"][plan]), credits["labels"].get(plan, "")


def credits_line(preset, clips, plan=None) -> str:
    """"≈ 240 Flow credits on AI Pro (Veo 3.1 Fast)" for *clips* clips, or the
    preset's own credits note when it prices none."""
    total, label = credits_of(preset, clips, plan)
    if total is None:
        return preset["credits"]["note"]
    return f"≈ {total} {preset['credits']['unit']} {label}".rstrip()


def own_clips_phrase(clips, *, platform=None) -> str:
    """"your own clips (12 shots, ≈ 240 Flow credits on AI Pro (Veo 3.1 Fast))":
    the estimate's words for a manual link's clips (plan 22 stage 5)."""
    preset = load(platform)
    shots = f"{clips} shot{'' if clips == 1 else 's'}"
    total, _label = credits_of(preset, clips)
    if total is None:
        return f"your own clips ({shots} on {preset['name']})"
    return f"your own clips ({shots}, {credits_line(preset, clips)})"
