"""Tests for ``clipping.aistory.render.subtitles`` (AI Story phase 4 stage
5; plan phase 4 stage 5, "Renderer" -> subtitles.py; spec 6.4, 6.5;
DEC-159).

Sections, in order:

1. portability guard (stdlib + aistory only, no PIL)
2. escaping (braces, backslash, newline, French text untouched)
3. time formatting / event time-code pairs (rounding rule)
4. word_pop: uppercase, one event per word, pop tag, 77.5 % position
5. two_line: one Style per speaker, per-word events, 2x32 wrap, MarginV,
   no \\k
6. none: no dialogue events, other layers still apply
7. approximate vs provider word timing
8. hook overlay only for text_overlay
9. ai_label per language / disabled
10. end card / cover
11. integration: real ``render.timeline.build_timeline`` fixtures (reused
    from ``test_aistory_render_timeline``/``test_story_shots``)
12. two_line accent colours stay distinct from the highlight and from each
    other, for both shipped styles' own real palette + typography

Stdlib + pytest only (DEC-012): this file runs in the CI environment.
"""

from __future__ import annotations

import ast
import copy
import re
import sys

import pytest

import test_aistory_render_timeline as rtt
import test_story_shots as sh
from clipping.aistory import templates as templates_mod
from clipping.aistory.render import subtitles as sub

EN = "en"
FR = "fr"

TYPOGRAPHY = {"font_family": "Montserrat Black", "highlight_colour": "#FFD400"}
PALETTE = {"primary": ["#F2C14E", "#E4572E", "#3A7D44"], "accents": ["#FFFFFF", "#1E1E24"]}


def _strip_tags(text: str) -> str:
    return re.sub(r"\{[^}]*\}", "", text)


# ======================================================== 1. portability guard

def test_it_imports_nothing_outside_the_standard_library_and_aistory():
    """DEC-012/RC-A1: subtitles.py is pure -- text only, no PIL, no
    filesystem, no subprocess."""
    import pathlib

    path = pathlib.Path(sub.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in sys.stdlib_module_names:
                    offenders.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level >= 1:
                continue
            if node.module and node.module.split(".")[0] == "clipping":
                continue
            top = (node.module or "").split(".")[0]
            if top not in sys.stdlib_module_names:
                offenders.append(node.module)
    assert offenders == []


# ======================================================== 2. escaping

@pytest.mark.parametrize("text,expected", [
    ("plain text", "plain text"),
    ("a {b} c", "a ｛b｝ c"),
    ("back\\slash", "back＼slash"),
    ("line1\nline2", "line1\\Nline2"),
    ("line1\r\nline2", "line1\\Nline2"),
    ("{{nested}}", "｛｛nested｝｝"),
    (None, ""),
])
def test_escape_ass_text(text, expected):
    assert sub.escape_ass_text(text) == expected


def test_escape_backslash_runs_before_newline_conversion():
    """A literal backslash-N in user text must not be re-escaped into a
    line break by the newline pass -- backslash is escaped FIRST (module
    docstring), so it becomes '＼N', never a real '\\N' hard break."""
    out = sub.escape_ass_text("literal \\N in text")
    assert out == "literal ＼N in text"
    assert "\\N" not in out


def test_french_accents_and_apostrophes_pass_through_unchanged():
    text = "C'est l'été... salut ’ éèêàç"
    assert sub.escape_ass_text(text) == text


# ======================================================== 3. time formatting

@pytest.mark.parametrize("seconds,expected", [
    (0, "0:00:00.00"),
    (-5, "0:00:00.00"),
    (61.2, "0:01:01.20"),
    (3600, "1:00:00.00"),
    (0.004, "0:00:00.00"),
    (0.006, "0:00:00.01"),
])
def test_format_ass_time(seconds, expected):
    assert sub.format_ass_time(seconds) == expected


def test_event_time_pair_never_negative():
    start, end = sub.event_time_pair(-1.0, -0.5)
    assert start == "0:00:00.00"
    assert end == "0:00:00.01"


def test_event_time_pair_bumps_a_zero_width_span_by_one_centisecond():
    start, end = sub.event_time_pair(2.0, 2.0)
    assert start == "0:00:02.00"
    assert end == "0:00:02.01"


def test_event_time_pair_end_before_start_is_still_bumped_forward():
    start, end = sub.event_time_pair(5.0, 1.0)
    assert start == "0:00:05.00"
    assert end == "0:00:05.01"


def test_event_time_pair_ordinary_span():
    assert sub.event_time_pair(1.234, 5.678) == ("0:00:01.23", "0:00:05.68")


# ======================================================== 4. word_pop

def _one_line(text="Hello world", duration_s=1.0, line_id="l1", speaker="narrator", start_s=0.0):
    return [{"line_id": line_id, "scene_id": "s1", "start_s": start_s, "duration_s": duration_s,
             "text": text, "speaker": speaker}]


def test_word_pop_one_event_per_word_uppercase():
    lines = _one_line("Hello world")
    styles, events, approx = sub.word_pop_dialogue(lines, typography=TYPOGRAPHY)
    assert len(events) == 2
    assert "HELLO" in events[0]
    assert "WORLD" in events[1]
    assert approx == {"l1": True}


def test_word_pop_pop_tag_and_position_are_verbatim():
    lines = _one_line("Hi")
    _styles, events, _approx = sub.word_pop_dialogue(lines, typography=TYPOGRAPHY)
    assert r"\an5\pos(540,1488)" in events[0]
    assert r"\fscx80\fscy80\t(0,90,\fscx100\fscy100)" in events[0]


def test_word_pop_style_is_bold_italic_3px_outline_white_on_black():
    styles, _events, _approx = sub.word_pop_dialogue(_one_line(), typography=TYPOGRAPHY)
    assert len(styles) == 1
    style = styles[0]
    fields = style.split(",")
    # Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,
    #         Bold, Italic, ..., Outline, ...
    assert fields[0] == "Style: WordPop"
    assert fields[3] == "&H00FFFFFF"  # white primary
    assert fields[5] == "&H00000000"  # black outline
    assert fields[7] == "-1"  # Bold
    assert fields[8] == "-1"  # Italic
    assert fields[16] == "3"  # Outline (px)


def test_word_pop_word_timing_split_evenly_across_line_duration():
    lines = _one_line("one two three four", duration_s=4.0)
    _styles, events, _approx = sub.word_pop_dialogue(lines, typography=TYPOGRAPHY)
    starts_ends = []
    for ev in events:
        parts = ev.split(",")
        starts_ends.append((parts[1], parts[2]))
    assert starts_ends == [
        ("0:00:00.00", "0:00:01.00"),
        ("0:00:01.00", "0:00:02.00"),
        ("0:00:02.00", "0:00:03.00"),
        ("0:00:03.00", "0:00:04.00"),
    ]


def test_word_pop_absolute_start_offset_from_the_lines_own_start_s():
    lines = _one_line("hi there", duration_s=2.0, start_s=10.0)
    _styles, events, _approx = sub.word_pop_dialogue(lines, typography=TYPOGRAPHY)
    first_start = events[0].split(",")[1]
    assert first_start == "0:00:10.00"


# ======================================================== 5. two_line

def test_two_line_one_style_per_speaker():
    lines = [
        {"line_id": "l1", "scene_id": "s1", "start_s": 0.0, "duration_s": 1.0, "text": "hi", "speaker": "char_a"},
        {"line_id": "l2", "scene_id": "s1", "start_s": 1.0, "duration_s": 1.0, "text": "yo", "speaker": "char_b"},
        {"line_id": "l3", "scene_id": "s1", "start_s": 2.0, "duration_s": 1.0, "text": "hey", "speaker": "char_a"},
    ]
    styles, _events, _approx = sub.two_line_dialogue(lines, palette=PALETTE, typography=TYPOGRAPHY)
    names = [s.split(",")[0] for s in styles]
    assert len(styles) == 2  # one per DISTINCT speaker, not per line
    assert "Style: TwoLine_char_a" in names
    assert "Style: TwoLine_char_b" in names


def test_two_line_distinct_speakers_get_distinct_accent_colours():
    lines = [
        {"line_id": "l1", "scene_id": "s1", "start_s": 0.0, "duration_s": 1.0, "text": "hi", "speaker": "char_a"},
        {"line_id": "l2", "scene_id": "s1", "start_s": 1.0, "duration_s": 1.0, "text": "yo", "speaker": "char_b"},
    ]
    styles, _events, _approx = sub.two_line_dialogue(lines, palette=PALETTE, typography=TYPOGRAPHY)
    primary_colours = [s.split(",")[3] for s in styles]
    assert len(set(primary_colours)) == 2


def test_two_line_one_event_per_word():
    lines = _one_line("one two three", duration_s=3.0, speaker="char_a")
    _styles, events, _approx = sub.two_line_dialogue(lines, palette=PALETTE, typography=TYPOGRAPHY)
    assert len(events) == 3


def test_two_line_no_karaoke_tag():
    lines = _one_line("one two three four five", duration_s=5.0, speaker="char_a")
    _styles, events, _approx = sub.two_line_dialogue(lines, palette=PALETTE, typography=TYPOGRAPHY)
    for ev in events:
        assert r"\k" not in ev
        assert r"\K" not in ev
        assert r"\kf" not in ev


def test_two_line_wrap_is_at_most_2_lines_of_32_chars():
    text = "this is a rather long dialogue line"
    lines = _one_line(text, duration_s=7.0, speaker="char_a")
    _styles, events, _approx = sub.two_line_dialogue(lines, palette=PALETTE, typography=TYPOGRAPHY)
    for ev in events:
        rendered_text = ev.split(",", 9)[9]
        plain = _strip_tags(rendered_text)
        physical_lines = plain.split(r"\N")
        assert len(physical_lines) <= 2
        for physical_line in physical_lines:
            assert len(physical_line) <= 32


def test_two_line_wrap_never_exceeds_2_lines_even_when_the_tail_overflows_32_chars():
    """More text than 2x32 chars can hold: the wrap rule (module docstring
    of ``_wrap_word_indices``) never produces a 3rd line -- the last line
    absorbs whatever is left, even past the 32-char target -- and no word
    is ever dropped."""
    text = "this is a rather long dialogue line that should wrap across two lines of subtitles"
    lines = _one_line(text, duration_s=8.0, speaker="char_a")
    _styles, events, _approx = sub.two_line_dialogue(lines, palette=PALETTE, typography=TYPOGRAPHY)
    rendered_text = events[0].split(",", 9)[9]
    plain = _strip_tags(rendered_text)
    physical_lines = plain.split(r"\N")
    assert len(physical_lines) == 2
    assert len(physical_lines[0]) <= 32
    reconstructed_word_count = sum(len(pl.split()) for pl in physical_lines)
    assert reconstructed_word_count == len(text.split())


def test_two_line_margin_v_is_the_18_percent_safe_area():
    styles, _events, _approx = sub.two_line_dialogue(_one_line(speaker="char_a"), palette=PALETTE,
                                                       typography=TYPOGRAPHY)
    margin_v = styles[0].split(",")[21]
    assert margin_v == str(sub.TWO_LINE_MARGIN_V)
    assert sub.TWO_LINE_MARGIN_V == round(1920 * 0.18)


def test_two_line_current_word_is_highlighted_and_reset():
    lines = _one_line("one two", duration_s=2.0, speaker="char_a")
    _styles, events, _approx = sub.two_line_dialogue(lines, palette=PALETTE, typography=TYPOGRAPHY)
    highlight_colour = sub._ass_override_color(TYPOGRAPHY["highlight_colour"])
    assert highlight_colour in events[0]
    assert r"\r" in events[0]


# ======================================================== 6. none

def test_none_mode_has_no_dialogue_events():
    script, board = rtt._en_family_3d_fixture()
    from clipping.aistory.render import timeline as rt
    tl = rt.build_timeline(script, board, rtt.TEMPLATE, EN, style_lock=rtt.FAMILY_3D)
    doc, _meta = sub.build_subtitles_ass(
        timeline=tl, script=script, subtitle_mode="none", language=EN,
        hook_style="shocking_image", ai_label_enabled=True,
        palette=rtt.FAMILY_3D["palette"], typography=TYPOGRAPHY,
    )
    assert "Dialogue" in doc  # ai_label is still there
    assert sub.WORD_POP_STYLE_NAME not in doc
    assert "TwoLine_" not in doc
    assert sub.AI_LABEL_STYLE_NAME in doc


def test_none_mode_word_pop_and_two_line_are_never_called_for_it():
    """subtitle_mode "none" produces no dialogue Style/Events even when
    lines exist -- checked directly against build_subtitles_ass's own
    output (no dialogue Style block beyond ai_label/hook)."""
    lines = _one_line("should not appear", speaker="char_a")
    script, board = rtt._en_family_3d_fixture()
    from clipping.aistory.render import timeline as rt
    tl = rt.build_timeline(script, board, rtt.TEMPLATE, EN, style_lock=rtt.FAMILY_3D)
    doc, _meta = sub.build_subtitles_ass(
        timeline=tl, script=script, subtitle_mode="none", language=EN,
        hook_style="shocking_image", ai_label_enabled=False,
        palette=rtt.FAMILY_3D["palette"], typography=TYPOGRAPHY,
    )
    assert "[Events]" in doc
    assert "Dialogue" not in doc


def test_unknown_subtitle_mode_raises():
    script, board = rtt._en_family_3d_fixture()
    from clipping.aistory.render import timeline as rt
    tl = rt.build_timeline(script, board, rtt.TEMPLATE, EN, style_lock=rtt.FAMILY_3D)
    with pytest.raises(sub.SubtitleError):
        sub.build_subtitles_ass(
            timeline=tl, script=script, subtitle_mode="bogus", language=EN,
            hook_style="shocking_image", ai_label_enabled=False,
            palette=rtt.FAMILY_3D["palette"], typography=TYPOGRAPHY,
        )


# ======================================================== 7. approximate vs provider timing

def test_provider_word_timings_are_not_approximate():
    lines = _one_line("hi there", duration_s=1.0)
    words = {"l1": [{"word": "hi", "start": 0.0, "end": 0.3}, {"word": "there", "start": 0.3, "end": 1.0}]}
    _styles, events, approx = sub.word_pop_dialogue(lines, word_timings=words, typography=TYPOGRAPHY)
    assert approx == {"l1": False}
    assert events[0].split(",")[2] == "0:00:00.30"


def test_missing_word_timings_are_approximate():
    lines = _one_line("hi there", duration_s=1.0)
    _styles, _events, approx = sub.word_pop_dialogue(lines, word_timings=None, typography=TYPOGRAPHY)
    assert approx == {"l1": True}


def test_empty_word_list_is_still_approximate():
    """A ``SOURCE_DURATION`` sidecar (no word cues at all) records
    ``"words": []`` -- falsy, so it must fall through to the even split,
    not be treated as "zero words, zero events"."""
    lines = _one_line("hi there", duration_s=1.0)
    _styles, events, approx = sub.word_pop_dialogue(lines, word_timings={"l1": []}, typography=TYPOGRAPHY)
    assert approx == {"l1": True}
    assert len(events) == 2


def test_build_subtitles_ass_meta_reports_approx_line_ids():
    script, board = rtt._en_family_3d_fixture()
    from clipping.aistory.render import timeline as rt
    tl = rt.build_timeline(script, board, rtt.TEMPLATE, EN, style_lock=rtt.FAMILY_3D)
    some_line_id = tl["lines"][0]["line_id"]
    words = {some_line_id: [{"word": w, "start": i * 0.1, "end": (i + 1) * 0.1}
                             for i, w in enumerate(["a", "b"])]}
    _doc, meta = sub.build_subtitles_ass(
        timeline=tl, script=script, subtitle_mode="word_pop", language=EN,
        hook_style="shocking_image", ai_label_enabled=False,
        palette=rtt.FAMILY_3D["palette"], typography=TYPOGRAPHY, word_timings=words,
    )
    assert some_line_id not in meta["approx_line_ids"]
    other_line_ids = {line["line_id"] for line in tl["lines"]} - {some_line_id}
    assert other_line_ids.issubset(set(meta["approx_line_ids"]))


# ======================================================== 8. hook overlay

def test_hook_overlay_only_for_text_overlay_style():
    script, board = rtt._en_family_3d_fixture()
    script = copy.deepcopy(script)
    script["hook"]["on_screen_text"] = "A shocking image appears"
    from clipping.aistory.render import timeline as rt
    tl = rt.build_timeline(script, board, rtt.TEMPLATE, EN, style_lock=rtt.FAMILY_3D)

    doc_no_overlay, _meta = sub.build_subtitles_ass(
        timeline=tl, script=script, subtitle_mode="none", language=EN,
        hook_style="shocking_image", ai_label_enabled=False,
        palette=rtt.FAMILY_3D["palette"], typography=TYPOGRAPHY,
    )
    assert sub.HOOK_STYLE_NAME not in doc_no_overlay

    doc_overlay, _meta2 = sub.build_subtitles_ass(
        timeline=tl, script=script, subtitle_mode="none", language=EN,
        hook_style="text_overlay", ai_label_enabled=False,
        palette=rtt.FAMILY_3D["palette"], typography=TYPOGRAPHY,
    )
    assert sub.HOOK_STYLE_NAME in doc_overlay
    assert "A SHOCKING IMAGE APPEARS" in doc_overlay


def test_hook_overlay_starts_at_episode_zero():
    styles, events = sub.hook_overlay_events("hello there", 2.5, typography=TYPOGRAPHY)
    assert len(styles) == 1
    assert events[0].split(",")[1] == "0:00:00.00"
    assert events[0].split(",")[2] == "0:00:02.50"


def test_hook_overlay_empty_text_produces_nothing():
    styles, events = sub.hook_overlay_events(None, 2.5, typography=TYPOGRAPHY)
    assert styles == []
    assert events == []
    styles2, events2 = sub.hook_overlay_events("", 2.5, typography=TYPOGRAPHY)
    assert styles2 == []
    assert events2 == []


# ======================================================== 9. ai_label

@pytest.mark.parametrize("language,expected_text", [(EN, "AI-generated"), (FR, "Généré par IA")])
def test_ai_label_text_per_language(language, expected_text):
    _styles, events = sub.ai_label_events(60.0, language=language, typography=TYPOGRAPHY)
    assert expected_text in events[0]


def test_ai_label_is_bottom_left_small():
    styles, _events = sub.ai_label_events(60.0, language=EN, typography=TYPOGRAPHY)
    fields = styles[0].split(",")
    assert fields[18] == "1"  # Alignment: bottom-left
    assert int(fields[2]) < sub.WORD_POP_FONT_SIZE  # "small"


def test_ai_label_disabled_is_absent_from_the_full_document():
    script, board = rtt._en_family_3d_fixture()
    from clipping.aistory.render import timeline as rt
    tl = rt.build_timeline(script, board, rtt.TEMPLATE, EN, style_lock=rtt.FAMILY_3D)
    doc, _meta = sub.build_subtitles_ass(
        timeline=tl, script=script, subtitle_mode="none", language=EN,
        hook_style="shocking_image", ai_label_enabled=False,
        palette=rtt.FAMILY_3D["palette"], typography=TYPOGRAPHY,
    )
    assert sub.AI_LABEL_STYLE_NAME not in doc
    assert "AI-generated" not in doc


# ======================================================== 10. end card / cover

def test_end_card_english_says_part():
    doc = sub.end_card_ass(EN, 2, "My Story", TYPOGRAPHY, 1.0)
    assert "PART 2" in doc
    assert "My Story" in doc
    assert "PARTIE" not in doc


def test_end_card_french_says_partie():
    doc = sub.end_card_ass(FR, 3, "Mon Histoire", TYPOGRAPHY, 1.0)
    assert "PARTIE 3" in doc
    assert "Mon Histoire" in doc


def test_end_card_is_a_standalone_document():
    doc = sub.end_card_ass(EN, 2, "My Story", TYPOGRAPHY, 1.0)
    assert doc.startswith("[Script Info]")
    assert "PlayResX: 1080" in doc
    assert "PlayResY: 1920" in doc
    assert "[V4+ Styles]" in doc
    assert "[Events]" in doc


def test_end_card_escapes_the_title():
    doc = sub.end_card_ass(EN, 2, "A {Weird} Title", TYPOGRAPHY, 1.0)
    assert "｛Weird｝" in doc
    assert "{Weird}" not in doc


def test_cover_ass_is_uppercase_and_standalone():
    doc = sub.cover_ass("a shocking secret", TYPOGRAPHY)
    assert "A SHOCKING SECRET" in doc
    assert doc.startswith("[Script Info]")
    assert "[Events]" in doc


def test_cover_ass_shows_text_as_given_no_truncation():
    text = "one two three four five six seven eight"
    doc = sub.cover_ass(text, TYPOGRAPHY)
    assert text.upper() in doc


# ======================================================== 11. integration with real timelines

@pytest.mark.parametrize("builder,language,style_lock", [
    pytest.param(rtt._fr_fruit_drama_fixture, FR, rtt.FRUIT_DRAMA, id="fr_fruit_drama"),
    pytest.param(rtt._en_family_3d_fixture, EN, rtt.FAMILY_3D, id="en_family_3d"),
])
def test_word_pop_over_a_real_timeline_has_no_exceptions_and_covers_every_line(builder, language, style_lock):
    from clipping.aistory.render import timeline as rt
    script, board = builder()
    tl = rt.build_timeline(script, board, rtt.TEMPLATE, language, style_lock=style_lock)
    lines = sub.merge_timeline_lines(tl, script)
    assert len(lines) == len(tl["lines"])
    _styles, events, approx = sub.word_pop_dialogue(lines, typography=TYPOGRAPHY)
    assert len(events) > 0
    assert set(approx.keys()) == {line["line_id"] for line in lines}


@pytest.mark.parametrize("builder,language,style_lock", [
    pytest.param(rtt._fr_fruit_drama_fixture, FR, rtt.FRUIT_DRAMA, id="fr_fruit_drama"),
    pytest.param(rtt._en_family_3d_fixture, EN, rtt.FAMILY_3D, id="en_family_3d"),
])
def test_build_subtitles_ass_over_a_real_timeline(builder, language, style_lock):
    from clipping.aistory.render import timeline as rt
    script, board = builder()
    tl = rt.build_timeline(script, board, rtt.TEMPLATE, language, style_lock=style_lock)
    doc, meta = sub.build_subtitles_ass(
        timeline=tl, script=script, subtitle_mode="word_pop", language=language,
        hook_style="insert_prop", ai_label_enabled=True,
        palette=style_lock["palette"], typography=TYPOGRAPHY,
    )
    assert doc.startswith("[Script Info]")
    assert sub.AI_LABEL_STYLE_NAME in doc
    assert isinstance(meta["approx_line_ids"], list)


def test_merge_timeline_lines_matches_script_text_and_speaker():
    from clipping.aistory.render import timeline as rt
    script, board = rtt._fr_fruit_drama_fixture()
    tl = rt.build_timeline(script, board, rtt.TEMPLATE, FR, style_lock=rtt.FRUIT_DRAMA)
    merged = sub.merge_timeline_lines(tl, script)
    by_id = {line["line_id"]: line for scene in script["scenes"] for line in scene["lines"]}
    for entry in merged:
        source = by_id[entry["line_id"]]
        assert entry["text"] == source["text"]
        assert entry["speaker"] == source["speaker"]


# =============================================== 12. two_line accent distinctness
#
# Regression coverage for the bug the coordinator flagged from the scratch
# render: fruit_drama's own primary #F2C14E sits only ~81 colour-distance
# units from its own highlight #FFD400 -- close enough that the highlighted
# word did not read as different from its neighbours. Every assertion below
# reads the SHIPPED style's own real palette/typography (``templates.
# load_style``), never a hand-picked test fixture, so a future edit to
# either style JSON is caught here too.

def _three_speaker_lines():
    return [
        {"line_id": "l1", "scene_id": "s1", "start_s": 0.0, "duration_s": 1.0, "text": "a", "speaker": "char_a"},
        {"line_id": "l2", "scene_id": "s1", "start_s": 1.0, "duration_s": 1.0, "text": "b", "speaker": "char_b"},
        {"line_id": "l3", "scene_id": "s1", "start_s": 2.0, "duration_s": 1.0, "text": "c", "speaker": "char_c"},
    ]


@pytest.mark.parametrize("style_id", ["fruit_drama", "family_3d"])
def test_shipped_style_closest_primary_is_actually_close_to_its_own_highlight(style_id):
    """Sanity-check on the bug report itself: without the fix, the FIRST
    palette colour picked for a speaker would have been within the
    rejection threshold of the style's own highlight -- this is what made
    the highlight invisible in the original scratch render."""
    style = templates_mod.load_style(style_id)
    highlight = style["typography"]["highlight_colour"]
    palette_colours = style["palette"]["primary"] + style["palette"]["accents"]
    closest = min(sub._color_distance(c, highlight) for c in palette_colours)
    assert closest < sub.TWO_LINE_MIN_COLOR_DISTANCE


@pytest.mark.parametrize("style_id", ["fruit_drama", "family_3d"])
def test_shipped_style_three_speaker_accents_stay_distinct_from_the_highlight(style_id):
    style = templates_mod.load_style(style_id)
    highlight = style["typography"]["highlight_colour"]
    typography = dict(style["typography"])
    typography["font_family"] = "Montserrat Black"

    accents = sub._speaker_accents(_three_speaker_lines(), style["palette"], highlight)
    assert len(accents) == 3
    for speaker, colour in accents.items():
        distance = sub._color_distance(colour, highlight)
        assert distance >= sub.TWO_LINE_MIN_COLOR_DISTANCE, (
            f"{style_id}: {speaker}'s accent {colour} is only {distance:.1f} units from "
            f"highlight {highlight} (threshold {sub.TWO_LINE_MIN_COLOR_DISTANCE})"
        )


@pytest.mark.parametrize("style_id", ["fruit_drama", "family_3d"])
def test_shipped_style_three_speaker_accents_stay_distinct_from_each_other(style_id):
    style = templates_mod.load_style(style_id)
    highlight = style["typography"]["highlight_colour"]
    accents = sub._speaker_accents(_three_speaker_lines(), style["palette"], highlight)
    colours = list(accents.values())
    assert len(colours) == len(set(colours))  # no two speakers literally share a colour
    for i in range(len(colours)):
        for j in range(i + 1, len(colours)):
            distance = sub._color_distance(colours[i], colours[j])
            assert distance >= sub.TWO_LINE_MIN_COLOR_DISTANCE, (
                f"{style_id}: {colours[i]} and {colours[j]} are only {distance:.1f} units apart"
            )


def test_fruit_drama_three_speaker_accents_are_the_expected_colours():
    """Golden: the exact, deterministic pick for fruit_drama's own shipped
    palette (primary ``#F2C14E, #E4572E, #3A7D44``, accents ``#FFFFFF,
    #1E1E24``) against its own highlight ``#FFD400``. ``#F2C14E`` (~81
    units from the highlight) is skipped for every speaker; the first three
    candidates that clear the threshold from the highlight AND from each
    other are picked in palette order."""
    style = templates_mod.load_style("fruit_drama")
    accents = sub._speaker_accents(_three_speaker_lines(), style["palette"], style["typography"]["highlight_colour"])
    assert accents == {"char_a": "#E4572E", "char_b": "#3A7D44", "char_c": "#FFFFFF"}


def test_family_3d_three_speaker_accents_are_the_expected_colours():
    """Golden: family_3d's own shipped palette (primary ``#6B7A8F,
    #F28C28, #8BC34A``, accents ``#FFFFFF, #5D3A9B``) against its own
    highlight ``#FFB703``. ``#F28C28`` (~51 units from the highlight) is
    skipped for every speaker."""
    style = templates_mod.load_style("family_3d")
    accents = sub._speaker_accents(_three_speaker_lines(), style["palette"], style["typography"]["highlight_colour"])
    assert accents == {"char_a": "#6B7A8F", "char_b": "#8BC34A", "char_c": "#FFFFFF"}


def test_pick_next_accent_falls_back_to_the_neutral_ramp_when_the_palette_cannot_clear_the_highlight():
    """A style whose entire palette sits within the threshold of its own
    highlight (a degenerate case neither shipped style hits, but the
    fallback must still hold): every speaker's accent must come from
    :data:`sub.TWO_LINE_NEUTRAL_FALLBACK`, and they must still stay
    distinct from each other and from the highlight."""
    tiny_palette = {"primary": ["#FFD500"], "accents": []}  # a near-duplicate of the highlight below
    highlight = "#FFD400"
    assert sub._color_distance("#FFD500", highlight) < sub.TWO_LINE_MIN_COLOR_DISTANCE

    lines = _three_speaker_lines()
    accents = sub._speaker_accents(lines, tiny_palette, highlight)
    assert len(set(accents.values())) == 3
    for colour in accents.values():
        assert colour in sub.TWO_LINE_NEUTRAL_FALLBACK
        assert sub._color_distance(colour, highlight) >= sub.TWO_LINE_MIN_COLOR_DISTANCE
    values = list(accents.values())
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            assert sub._color_distance(values[i], values[j]) >= sub.TWO_LINE_MIN_COLOR_DISTANCE


def test_color_distance_is_symmetric_and_zero_for_identical_colours():
    assert sub._color_distance("#FFD400", "#FFD400") == 0.0
    assert sub._color_distance("#000000", "#FFFFFF") == pytest.approx(441.6729, abs=1e-3)
    assert sub._color_distance("#F2C14E", "#FFD400") == sub._color_distance("#FFD400", "#F2C14E")


# ------------------------------------------------ French spaced punctuation
# Tier-2 (2026-09-29): French writes a space before ? ! : ; and inside « »,
# so a lone "?" was split off and popped as a word of its own at 45 s of the
# live FR episode. A punctuation-only token joins its neighbour instead.

def test_a_spaced_question_mark_joins_the_word_before_it_in_an_even_split():
    spans, approx = sub._line_word_spans("Tu es à ta place ?", 3.0, None)
    assert approx is True
    assert [text for text, _start, _end in spans] == ["Tu", "es", "à", "ta", "place ?"]
    assert spans[-1][2] == pytest.approx(3.0)


def test_a_provider_punctuation_cue_extends_the_word_before_it():
    words = [{"word": "Vraiment", "start": 0.0, "end": 0.5}, {"word": "?", "start": 0.55, "end": 0.7}]
    spans, approx = sub._line_word_spans("Vraiment ?", 0.8, words)
    assert approx is False
    assert spans == [("Vraiment ?", 0.0, 0.7)]


def test_opening_guillemets_join_the_word_after_them():
    spans, _approx = sub._line_word_spans("« Bonjour » dit-il !", 2.0, None)
    assert [text for text, _start, _end in spans] == ["« Bonjour »", "dit-il !"]
    assert spans[0][1] == 0.0


def test_a_line_of_punctuation_alone_keeps_its_one_span():
    spans, _approx = sub._line_word_spans("?!", 1.0, None)
    assert [text for text, _start, _end in spans] == ["?!"]
