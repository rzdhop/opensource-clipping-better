"""Load and render the fruitstory ComfyUI workflow templates (``fruitstory/workflows/*.json``).

A template is an API-format ComfyUI graph whose literal inputs are ``{{placeholders}}``.
:func:`render` fills them with typed values and computes the derived ones (frame count under
the 8n+1 rule, the half size of stage 1, the index of the last frame). Pure: no disk writes,
no network. Stdlib only, so the stage-0 runner and the future MCP server share it.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re

WORKFLOWS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflows")
PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

# Placeholders that must land as numbers in the graph (ComfyUI validates input types).
INT_FIELDS = {"seed", "width", "height", "frames", "half_width", "half_height", "last_index", "batch_index"}
FLOAT_FIELDS = {"fps", "seconds", "exaggeration", "identity_guidance", "strength", "cfg"}


def load_template(name: str) -> dict:
    """The template *name* (``ltx25_i2v_speech`` ...) as a dict; ``ValueError`` on a wrong schema."""
    path = os.path.join(WORKFLOWS_DIR, f"{name}.json")
    with open(path, encoding="utf-8") as fh:
        template = json.load(fh)
    if template.get("$schema") != "comfy_workflow_v1":
        raise ValueError(f"{path}: expected \"$schema\": \"comfy_workflow_v1\"")
    return template


def frames_for(seconds: float, fps: float = 24, step: int = 8, max_frames: int = 241) -> int:
    """Frame count of a *seconds* clip under LTX's 8n+1 rule: ``1 + floor(fps*seconds/8)*8``
    (the formula of the official A2V template, so a clip never runs longer than its audio).
    ``ValueError`` past *max_frames* (241 = 10 s at 24 fps)."""
    frames = 1 + int(math.floor(fps * seconds / step)) * step
    if frames > max_frames:
        raise ValueError(f"{seconds} s is {frames} frames at {fps} fps, more than {max_frames}")
    return frames


def derived_values(values: dict) -> dict:
    """The derived placeholders from the given ones: ``frames``, ``half_width``, ``half_height``,
    ``last_index``. Width and height must be multiples of 64 so stage 1 (half size) stays on the
    32 px latent grid."""
    width, height = int(values["width"]), int(values["height"])
    for side, label in ((width, "width"), (height, "height")):
        if side % 64:
            raise ValueError(f"{label} {side} is not a multiple of 64 (stage 1 runs at half size on a 32 px grid)")
    frames = frames_for(float(values["seconds"]), float(values.get("fps", 24)))
    return {"frames": frames, "half_width": width // 2, "half_height": height // 2, "last_index": frames - 1}


def _typed(field: str, value):
    if field in INT_FIELDS:
        return int(value)
    if field in FLOAT_FIELDS:
        return float(value)
    return value


def render(template: dict, values: dict) -> dict:
    """The graph of *template* with every placeholder filled from *values* (+ the template's
    ``defaults``, + the derived values). Missing placeholders raise ``ValueError`` by name."""
    merged = dict(template.get("defaults") or {})
    merged.update(values)
    if "width" in merged and "seconds" in merged:
        merged.update(derived_values(merged))
    declared = list(template.get("placeholders", []))
    missing = [p for p in declared if p not in merged]
    if missing:
        raise ValueError(f"{template.get('name')} needs values for: {', '.join(missing)}")

    graph = copy.deepcopy(template["graph"])
    for node in graph.values():
        for key, value in list(node["inputs"].items()):
            if not isinstance(value, str) or "{{" not in value:
                continue
            whole = PLACEHOLDER.fullmatch(value)
            if whole:
                field = whole.group(1)
                if field not in merged:
                    raise ValueError(f"{template.get('name')}: no value for {{{{{field}}}}} ({key})")
                node["inputs"][key] = _typed(field, merged[field])
            else:
                node["inputs"][key] = PLACEHOLDER.sub(lambda m: str(merged[m.group(1)]), value)
    return graph


def file_placeholders(template: dict) -> list:
    """The placeholders that name an uploaded input file (image, audio, voice_ref ...)."""
    return list((template.get("files") or {}).keys())
