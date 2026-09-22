"""The karaoke highlight colour is a setting, not a literal.

`clipping/studio/` imports cv2, mediapipe and PIL at file scope, so DEC-012
forbids importing any of it here: CI installs pytest and nothing else. These
are text scans over the source, plus config-level assertions — which is the
honest limit of what can be proven without a render.

**What these tests cannot prove** is that the .ass file still renders
correctly. That needs one live render at the default colour, diffed against a
pre-change .ass and expected byte-identical (RC-4, RC-7). It is recorded in
CHECKPOINT.md, not here.

The scans match `&H00FFFF&` WITH its trailing ampersand on purpose.
`subtitles.py` also contains `&H00FFFFFF` — an 8-digit style colour in the
`[V4+ Styles]` line, a different thing entirely — and a looser pattern would
match it and demand a change that would break every subtitle.
"""

import pathlib
import re

import pytest

from clipping.config import KARAOKE_HIGHLIGHT_COLOR, WARNA_KATA_KHUSUS, build_config

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SUBTITLES = PROJECT_ROOT / "clipping" / "studio" / "subtitles.py"

# Six hex digits between ampersands: an ASS inline colour override. The
# eight-digit style colour has no trailing ampersand and is deliberately not
# matched.
INLINE_COLOUR = re.compile(r"&H[0-9A-Fa-f]{6}&")


def source():
    return SUBTITLES.read_text(encoding="utf-8")


# ---------------------------------------------------------- the literals went

def test_the_highlight_yellow_is_no_longer_hardcoded():
    assert "&H00FFFF&" not in source(), (
        "the karaoke highlight colour should come from cfg.karaoke_color"
    )


def test_the_subtitle_builder_reads_the_setting():
    assert "karaoke_color" in source()


def test_the_eight_digit_style_colour_is_untouched():
    """`&H00FFFFFF` in the [V4+ Styles] line is the base subtitle colour, in a
    different format. Confusing the two would break every subtitle."""
    assert "&H00FFFFFF" in source()


def test_no_inline_colour_literal_survives_in_the_karaoke_paths():
    """Both the simple and the advanced karaoke branches used to carry their
    own copy of the pair, which is how two code paths come to disagree."""
    remaining = INLINE_COLOUR.findall(source())
    assert remaining == [], f"inline colour literals left behind: {remaining}"


# --------------------------------------------------------------- the default

def test_the_default_is_exactly_the_colour_it_replaced():
    """Nobody's clips change appearance because this became configurable."""
    assert KARAOKE_HIGHLIGHT_COLOR == "&H00FFFF&"


def test_the_highlight_and_the_emphasis_colour_are_different_settings():
    """`warna_kata_khusus` styles the AI-chosen emphasis words when karaoke is
    OFF. It is white, it always was, and it is not this."""
    assert KARAOKE_HIGHLIGHT_COLOR != WARNA_KATA_KHUSUS


def test_the_flag_defaults_to_the_constant(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    assert build_config(["--video", str(video)]).karaoke_color == KARAOKE_HIGHLIGHT_COLOR


def test_a_chosen_colour_reaches_the_config(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    cfg = build_config(["--video", str(video), "--karaoke-color", "&H0000FF&"])
    assert cfg.karaoke_color == "&H0000FF&"


# ----------------------------------------------------------------- validation

BAD_COLOURS = [
    "#FFFF00",      # the CSS spelling
    "yellow",
    "&H00FFFF",     # missing the closing ampersand
    "H00FFFF&",
    "&HGGGGGG&",    # not hex
    "&H00FF&",      # too short
    "",
]


@pytest.mark.parametrize("value", BAD_COLOURS)
def test_a_malformed_colour_is_refused_by_the_parser(tmp_path, value):
    """libass silently ignores an override it cannot parse, so a typo would
    produce subtitles with no highlight and no error anywhere."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with pytest.raises(SystemExit):
        build_config(["--video", str(video), "--karaoke-color", value])


def test_lowercase_hex_is_accepted(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    assert build_config(
        ["--video", str(video), "--karaoke-color", "&H00ffff&"]
    ).karaoke_color == "&H00ffff&"
