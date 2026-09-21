"""Per-platform duration bounds for a finished clip.

The numbers are the practical publishing windows as of September 2026, not the
absolute maxima a platform will technically accept: a clip that *can* be three
minutes long on Reels is still not a short-form clip. ``target`` is what the
snapper aims for when a candidate is too short and has room to grow.

``long`` reproduces the bounds the old prompt hardcoded
(``MIN_CLIP_DURATION``/``MAX_CLIP_DURATION`` in ``clipping/engine.py``) so a run
that wants the previous behaviour can ask for it by name.

Stdlib only.
"""

from __future__ import annotations

from collections import namedtuple

Preset = namedtuple("Preset", "name min max target")

PRESETS = {
    "tiktok": Preset("tiktok", 15.0, 90.0, 34.0),
    "reels": Preset("reels", 15.0, 90.0, 30.0),
    "shorts": Preset("shorts", 15.0, 59.0, 45.0),
    # The default: wide enough to suit all three, so one run can be posted
    # anywhere without re-cutting.
    "auto": Preset("auto", 20.0, 75.0, 40.0),
    # What the pipeline used before platform presets existed.
    "long": Preset("long", 60.0, 179.0, 90.0),
}

DEFAULT_PRESET = "auto"
PRESET_NAMES = tuple(PRESETS)


def get(name):
    """The preset called *name*, falling back to ``auto`` for anything unknown.

    Deliberately forgiving: an unrecognised platform name should produce clips
    that work everywhere, not abort a run that has already paid for its
    transcription.
    """
    if isinstance(name, Preset):
        return name
    return PRESETS.get(str(name or "").strip().lower(), PRESETS[DEFAULT_PRESET])
