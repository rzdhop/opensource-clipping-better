"""A story's own subtitle look (plan 23 stage B5): ``story.json``'s optional
``subtitle_style``, resolved over the style lock into the ``Look`` the
renderer burns.

**Why it is not in the style lock.** The lock freezes at ``lock_style``
(``stylelock.apply_overrides`` refuses afterwards), and the subtitle look must
stay editable until the last render. So it is a story-level key, written by
``StoryStore.set_subtitle_style`` at any time, read only by the render
(``steps/render.py``); it touches no prompt, no image and no cache of any
shot.

**Resolution order** (:func:`resolve`): the story's override, then the lock's
typography -- which already carries the template's values with the pre-lock
overrides applied (``stylelock.build_style_lock``) -- then the render's own
numbers (``render/subtitles.py``: size 62/56, the 77.5 % centre, the 3 px and
2 px outlines, white text). A field the override leaves out is therefore
never changed; a story without the key is rendered with no ``Look`` at all
(:func:`active`), byte for byte as before.

**Validation** (:func:`validate`): the schema's ranges
(``schemas.SUBTITLE_STYLE_SCHEMA``: the font one of the families
``assets/fonts/fonts_index.json`` ships, size 60-160 %, position, outline 0-8
px, a box's opacity 0-100 %, every colour ``#RRGGBB``, no other key) and one
rule that reads two fields at once: the contrast ratio between the text and
what it is drawn on -- the box colour when there is a box, else the outline
colour (the default black when none is set; nothing when the outline is 0 px)
-- must be at least 4.5 (WCAG AA for normal text, ``subtitles._contrast_ratio``
and ``TWO_LINE_MIN_CONTRAST_RATIO``, reused). The text is ``text_colour`` (the
default white when absent) and the ``highlight_colour``. Below that the edit
is refused with the ratio named. A box turns the outline off (libass
``BorderStyle 3``; ``render/subtitles.py``'s module docstring), so
``outline_px`` and ``outline_colour`` are accepted beside one but not drawn.

Stdlib only (DEC-012): this module imports ``schemas`` and the pure subtitle
builders' contrast helper, nothing that needs the network or PIL.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import NamedTuple, Optional

from . import schemas
from .render import subtitles as _subtitles

# The text and the plate it sits on when the story's look names neither.
DEFAULT_TEXT_HEX = _subtitles.WORD_POP_PRIMARY_HEX
DEFAULT_PLATE_HEX = _subtitles.WORD_POP_OUTLINE_HEX
MIN_CONTRAST_RATIO = _subtitles.TWO_LINE_MIN_CONTRAST_RATIO

FONTS_INDEX = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts" / "fonts_index.json"
# The family a lock without typography is given (the committed fallback font).
DEFAULT_FONT_FAMILY = "Montserrat"


class SubtitleStyleError(ValueError):
    """A subtitle style the rules refuse; ``errors`` is every problem found."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("subtitle_style: " + "; ".join(self.errors))


class Box(NamedTuple):
    colour: str
    opacity_pct: int


class Look(NamedTuple):
    """What the dialogue builders read (``render/subtitles.py``, by attribute).

    ``font_family`` and ``highlight_colour`` are always the resolved values
    (the override, else the lock's); every other field is ``None`` (the
    builder's own number) unless the story's override set it, ``size_pct``
    100 for "the style's own size"."""

    font_family: str
    highlight_colour: Optional[str] = None
    size_pct: int = 100
    position_pct: Optional[float] = None
    text_colour: Optional[str] = None
    outline_px: Optional[int] = None
    outline_colour: Optional[str] = None
    box: Optional[Box] = None


# ------------------------------------------------------------------ families

def shipped_families() -> tuple:
    """The font families ``assets/fonts/fonts_index.json`` ships, in its order
    -- the only ones a story may pick (``schemas.SUBTITLE_FONT_FAMILIES`` is
    the same list for the schema; ``tests/test_story_subtitle_style.py``
    keeps them equal). An unreadable index gives the schema's own list."""
    try:
        with open(FONTS_INDEX, encoding="utf-8") as fh:
            families = tuple(entry["family"] for entry in json.load(fh)["fonts"])
    except (OSError, ValueError, KeyError, TypeError):
        return tuple(schemas.SUBTITLE_FONT_FAMILIES)
    return families or tuple(schemas.SUBTITLE_FONT_FAMILIES)


# ---------------------------------------------------------------- validation

def _contrast_errors(obj: dict) -> list:
    box = obj.get("box")
    if box:
        plate, plate_name = box["colour"], "the box colour"
    elif obj.get("outline_px") == 0:
        return []  # no outline and no box: nothing the text is drawn on
    else:
        plate, plate_name = obj.get("outline_colour", DEFAULT_PLATE_HEX), "the outline colour"
    errors = []
    texts = (("text_colour", obj.get("text_colour", DEFAULT_TEXT_HEX)),
             ("highlight_colour", obj.get("highlight_colour")))
    for name, colour in texts:
        if colour is None:
            continue
        ratio = _subtitles._contrast_ratio(colour, plate)
        if ratio < MIN_CONTRAST_RATIO:
            errors.append(
                f"{name} {colour} on {plate_name} {plate}: contrast ratio {ratio:.2f} is below the "
                f"{MIN_CONTRAST_RATIO} minimum (the text would not be readable)")
    return errors


def validate(obj) -> list:
    """Every problem with *obj* as a ``subtitle_style`` (module docstring);
    empty means valid. ``{}`` is valid (no override)."""
    if not isinstance(obj, dict):
        return [f"$: expected an object, got {type(obj).__name__}"]
    errors = schemas.validate(obj, schemas.SUBTITLE_STYLE_SCHEMA, "$")
    if errors:
        return errors
    return _contrast_errors(obj)


def check(obj) -> None:
    """:func:`validate`, raising :class:`SubtitleStyleError` with every problem."""
    errors = validate(obj)
    if errors:
        raise SubtitleStyleError(errors)


def normalise(obj) -> Optional[dict]:
    """The document to store for a validated *obj*: a copy, or ``None`` (the
    key is removed) for ``None`` and for ``{}``."""
    return copy.deepcopy(obj) if obj else None


# ---------------------------------------------------------------- resolution

def active(story) -> bool:
    """Whether *story* carries a subtitle look to apply: the render builds no
    ``Look`` (and so changes nothing) otherwise."""
    return bool((story or {}).get("subtitle_style"))


def resolve(style_lock, story) -> Look:
    """The :class:`Look` of *story*'s ``subtitle_style`` over *style_lock*'s
    typography (module docstring's resolution order)."""
    typography = (style_lock or {}).get("typography") or {}
    style = (story or {}).get("subtitle_style") or {}
    box = style.get("box")
    return Look(
        font_family=style.get("font_family") or typography.get("font_family") or DEFAULT_FONT_FAMILY,
        highlight_colour=style.get("highlight_colour") or typography.get("highlight_colour"),
        size_pct=style.get("size_pct", 100),
        position_pct=style.get("position_pct"),
        text_colour=style.get("text_colour"),
        outline_px=style.get("outline_px"),
        outline_colour=style.get("outline_colour"),
        box=Box(box["colour"], box["opacity_pct"]) if box else None,
    )


def look_for(style_lock, story) -> Optional[Look]:
    """:func:`resolve` for a story with a look, ``None`` for one without --
    what ``steps/render.py`` hands ``build_render_plan``."""
    return resolve(style_lock, story) if active(story) else None

