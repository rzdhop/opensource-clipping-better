"""Prompt assembly for AI Story (spec 5, 2.3).

Every function here is pure: it takes a ``style_lock_v1``-shaped dict (the
same prose fields also live on a ``style_template_v1`` dict, so these
functions work equally well against ``templates.load_style(id)`` output)
plus a handful of scalar placeholders, and returns exactly one prompt
string. Nothing here calls a model, touches the filesystem, or reads the
clock — that keeps prompt assembly trivially testable and lets phase 2
inject it anywhere a prompt is needed.

Builders never take a character's name: spec 2.3 is explicit that prompts
reference appearance, never names, so a name leaking into an image prompt
would be a spec violation, not just a style nit.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import re

from . import schemas

# --------------------------------------------------------------- constants

# Spec 5's common negative-prompt base; each template appends its own on top.
NEGATIVE_BASE = (
    "text, watermark, logo, signature, extra limbs, extra fingers, deformed hands, "
    "duplicated character, cropped face, blurry, low resolution, jpeg artifacts, "
    "out of frame, split screen, collage, frame border, caption"
)

# One phrase per framing in schemas.FRAMINGS (spec 5's per-shot table).
FRAMING_PHRASES = {
    "wide_establishing": "wide establishing shot, characters small in the frame",
    "medium_single": "medium shot, single character waist-up",
    "medium_two_shot": "medium two-shot, both characters waist-up",
    "close_up": "tight close-up on the face",
    "extreme_close_up": "extreme close-up, eyes and skin detail only",
    "over_shoulder": "over-the-shoulder shot",
    "low_angle": "low-angle shot looking up at the subject",
    "high_angle": "high-angle shot looking down at the subject",
    "insert_prop": "insert shot, tight on the object",
}

# Framings with a fixed lens regardless of the template's own camera text.
_WIDE_FRAMINGS = ("wide_establishing",)
_TIGHT_FRAMINGS = ("close_up", "extreme_close_up", "insert_prop")

# Matches e.g. "50mm look" or "50–85mm looks" (en dash or hyphen in a range).
_LENS_PATTERN = re.compile(r"\d+(?:[–-]\d+)?mm\s+looks?")


# ------------------------------------------------------------------ helpers

def _strip_trailing_period(text: str) -> str:
    """Strip surrounding whitespace and one trailing '.', if present.

    Used only where the caller's skeleton adds its own '.' right after the
    fragment, so a fragment that already ends in '.' never produces '..'.
    """
    text = text.strip()
    if text.endswith("."):
        text = text[:-1]
    return text


def _collapse_ws(text: str) -> str:
    """Collapse any run of whitespace to a single space and trim the ends."""
    return re.sub(r"\s+", " ", text).strip()


def _join_items(signature_items) -> str:
    items = list(signature_items)
    if not items:
        raise ValueError(
            "signature_items must not be empty: an identity sheet without "
            "signature items defeats the lock"
        )
    return ", ".join(items)


def _check_framing(framing: str) -> None:
    if framing not in schemas.FRAMINGS:
        raise ValueError(f"unknown framing: {framing!r}")


# -------------------------------------------------------------- primitives

def lens_phrase(style_lock: dict, framing: str) -> str:
    """The lens fragment for one framing, deterministic per spec 5.

    ``wide_establishing`` and the three tight framings get a fixed lens.
    Every other framing derives its lens from the template's own ``camera``
    text, never from the framing itself — the template's camera paragraph
    is otherwise never injected into a shot prompt (spec: it guides T1, not
    the image model), so this is the only place its wording leaks through,
    and only as a short extracted fragment.
    """
    _check_framing(framing)

    if framing in _WIDE_FRAMINGS:
        return "24mm equivalent, deep focus"
    if framing in _TIGHT_FRAMINGS:
        return "85mm look, shallow depth of field"

    camera_text = style_lock["camera"]
    match = _LENS_PATTERN.search(camera_text)
    if match:
        lens = re.sub(r"looks\b", "look", match.group(0))
    elif "macro" in camera_text.lower():
        lens = "macro lens look"
    else:
        lens = "natural lens"

    if "no depth of field" in camera_text:
        return f"{lens}, no depth of field"
    return f"{lens}, shallow depth of field"


def palette_line(style_lock: dict) -> str:
    return style_lock["palette"]["palette_line"]


# --------------------------------------------------------------- builders

def shot_prompt(
    style_lock: dict,
    *,
    subjects_block: str,
    action: str,
    place_block: str,
    time_variant: str,
    framing: str,
) -> str:
    """The common shot-prompt skeleton (spec 5): subject -> action -> setting
    -> style -> camera -> lighting -> quality, in that fixed order.
    """
    _check_framing(framing)
    framing_phrase = FRAMING_PHRASES[framing]
    lens = lens_phrase(style_lock, framing)

    subjects = _strip_trailing_period(subjects_block)
    act = _strip_trailing_period(action)
    place = place_block.strip()
    time_v = _strip_trailing_period(time_variant)
    rendering = _strip_trailing_period(style_lock["rendering"])
    palette = _strip_trailing_period(palette_line(style_lock))
    character_design_rules = style_lock["character_design_rules"].strip()
    lighting = _strip_trailing_period(style_lock["lighting"])
    quality_tail = style_lock["quality_tail"].strip()

    text = (
        f"{subjects}. {act}. Setting: {place}, {time_v}. "
        f"Style: {rendering}. Palette: {palette}. {character_design_rules} "
        f"Camera: {framing_phrase}, {lens}. Lighting: {lighting}. "
        "Vertical 9:16 composition, subject kept in the central safe area "
        "(leave the bottom 22% free of faces for subtitles). "
        f"{quality_tail}"
    )
    return _collapse_ws(text)


def negative_prompt(style_lock: dict) -> str:
    text = f"{NEGATIVE_BASE}, {style_lock['negative_prompt']}"
    return _collapse_ws(text)


def portrait_prompt(style_lock: dict, *, descriptor: str, signature_items) -> str:
    items = _join_items(signature_items)
    text = (
        f"Character portrait, {_strip_trailing_period(descriptor)}, wearing {items}. "
        "Neutral expression, looking at camera, three-quarter view, chest-up. "
        f"{style_lock['rendering']}. {style_lock['character_design_rules']} "
        f"Plain {style_lock['sheet_background']} background, even soft studio lighting, "
        "no props, no text. Vertical 9:16."
    )
    return _collapse_ws(text)


def turnaround_prompt(style_lock: dict, *, descriptor: str, signature_items) -> str:
    items = _join_items(signature_items)
    text = (
        f"Character design turnaround sheet of the same character: "
        f"{_strip_trailing_period(descriptor)}, {items}. "
        "Four full-body views side by side in one row: front, three-quarter, profile, back. "
        f"Identical proportions and outfit in every view. {style_lock['rendering']}. "
        f"{style_lock['character_design_rules']} Plain {style_lock['sheet_background']} "
        "background, flat even lighting, no text, no labels."
    )
    return _collapse_ws(text)


def expressions_prompt(style_lock: dict, *, descriptor: str, signature_items) -> str:
    items = _join_items(signature_items)
    text = (
        f"Expression sheet of the same character: {_strip_trailing_period(descriptor)}, {items}. "
        "Six head-and-shoulders portraits in a 3x2 grid: neutral, happy, angry, shocked, "
        "sad, scheming. Same face, same outfit, same lighting in every cell. "
        f"{style_lock['rendering']}. Plain {style_lock['sheet_background']} background, no text."
    )
    return _collapse_ws(text)


def master_plate_prompt(style_lock: dict, *, place_descriptor: str, time_variant: str) -> str:
    environment_rules = style_lock["environment_rules"].strip()
    if environment_rules and environment_rules[-1] not in ".!?":
        environment_rules += "."
    text = (
        f"Establishing wide shot of {_strip_trailing_period(place_descriptor)}, {time_variant}, "
        "no people, no characters. "
        f"{environment_rules} {style_lock['rendering']}. "
        f"Palette: {palette_line(style_lock)}. Camera: wide, eye level, 24mm equivalent. "
        f"Lighting: {style_lock['lighting']}. Vertical 9:16, horizon in the upper third, "
        f"foreground detail in the lower third. {style_lock['quality_tail']}"
    )
    return _collapse_ws(text)


def variant_prompt(style_lock: dict, *, place_descriptor: str, variant: str) -> str:
    """The master-plate prompt (spec 5) of one time variant of a place: the
    variant name is its time and weather (``night``, ``golden_hour`` ->
    "golden hour"). ``variant_prompt(..., variant="day")`` is the master plate
    itself. ValueError for a name that is not a variant name
    (``schemas.TIME_VARIANT_PATTERN``).
    """
    if not isinstance(variant, str) or re.fullmatch(schemas.TIME_VARIANT_PATTERN, variant) is None:
        raise ValueError(f"not a time variant name: {variant!r}")
    return master_plate_prompt(style_lock, place_descriptor=place_descriptor,
                               time_variant=variant.replace("_", " "))


def prop_image_prompt(style_lock: dict, *, descriptor: str) -> str:
    """A prop's reference image (spec 2.5): the object alone on the sheet
    background, in the style's rendering, with nobody holding it."""
    text = (
        f"Product shot of {_strip_trailing_period(descriptor)}, alone, centered, plain "
        f"{style_lock['sheet_background']} background. {_strip_trailing_period(style_lock['rendering'])}. "
        f"No people, no hands, no text. {style_lock['quality_tail']}"
    )
    return _collapse_ws(text)


def character_prompt_block(style_lock: dict, *, descriptor: str, signature_items) -> str:
    """Spec 2.3: auto-assembled from descriptor + signature_items +
    style_lock.character_design_rules. Pure and deterministic.
    """
    items = _join_items(signature_items)
    text = f"{_strip_trailing_period(descriptor)}, wearing {items}. {style_lock['character_design_rules']}"
    return _collapse_ws(text)


def place_prompt_block(style_lock: dict, *, descriptor: str, layout_notes: str) -> str:
    """Spec 2.4: auto-assembled from descriptor + layout_notes +
    style_lock.environment_rules. Pure and deterministic, no names.
    """
    place = _strip_trailing_period(descriptor)
    layout = _strip_trailing_period(layout_notes)
    text = f"{place}. {layout}. {style_lock['environment_rules']}"
    return _collapse_ws(text)


def prop_prompt_block(style_lock: dict, *, descriptor: str) -> str:
    """Spec 2.5: auto-assembled from descriptor + style_lock.rendering.
    Pure and deterministic, no names.
    """
    text = f"{_strip_trailing_period(descriptor)}. {style_lock['rendering']}"
    return _collapse_ws(text)
