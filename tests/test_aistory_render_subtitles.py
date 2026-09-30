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
13. two_line accent colours are readable (WCAG contrast) against the
    outline, for both shipped styles AND the live episode's own palette,
    at 3 and 4 speakers
14. script text, provider timing: displayed tokens are the script's own,
    timed from a matching provider cue, punctuation kept
15. two_line accents for the phase-5 styles (cinematic_real, claymation,
    storybook_watercolor) against DEC-169's outline-contrast gate

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
    units from the highlight) is skipped for every speaker, same as before
    the readability fix.

    DELIBERATE CHANGE (Tier-2, 2026-09-29, the "broccolia" bug): ``char_b``
    and ``char_c`` are no longer ``#3A7D44``/``#FFFFFF`` -- ``#3A7D44`` (a
    dark forest green) now fails :func:`sub._contrast_ratio` against the
    style's own black outline (~4.20:1, under the 4.5:1 WCAG gate), so it is
    skipped for every speaker; ``#1E1E24`` was already failing it far worse
    (~1.27:1, the actual reported bug). With both dark palette entries
    excluded, only ``#E4572E`` and ``#FFFFFF`` remain in-palette, and the
    3rd speaker now comes from :data:`sub.TWO_LINE_NEUTRAL_FALLBACK`."""
    style = templates_mod.load_style("fruit_drama")
    accents = sub._speaker_accents(_three_speaker_lines(), style["palette"], style["typography"]["highlight_colour"])
    assert accents == {"char_a": "#E4572E", "char_b": "#FFFFFF", "char_c": "#C5C5C5"}


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


# ================================================== 13. two_line readability
#
# Regression coverage for the second Tier-2 finding (2026-09-29, the "FR
# fruit_drama episode 1, two_line render" bug report): the live story's own
# style_lock.json (outputs/stories/b1104ec66b05/style_lock.json, read-only)
# swaps the shipped fruit_drama accent #FFFFFF for #ffd400 -- the SAME
# colour as its own highlight -- so a speaker ended up with #1E1E24 as a
# base fill: a near-black accent that, against the style's own 3 px BLACK
# outline, was nearly invisible. The old contrast/distance rule never
# checked readability against the OUTLINE, only against the highlight and
# other speakers. Copied into ``LIVE_FRUIT_DRAMA_PALETTE`` below because it
# differs from the shipped template (the file itself is never read here).

LIVE_FRUIT_DRAMA_PALETTE = {"primary": ["#F2C14E", "#E4572E", "#3A7D44"], "accents": ["#FFD400", "#1E1E24"]}
LIVE_FRUIT_DRAMA_HIGHLIGHT = "#FFD400"


def _four_speaker_lines():
    return _three_speaker_lines() + [
        {"line_id": "l4", "scene_id": "s1", "start_s": 3.0, "duration_s": 1.0, "text": "d", "speaker": "char_d"},
    ]


def _assert_accents_are_readable_distinct_and_far_from_highlight(accents, highlight):
    colours = list(accents.values())
    assert len(set(colours)) == len(colours), "every speaker must get its own colour"
    for speaker, colour in accents.items():
        assert sub._contrast_ratio(colour, sub.TWO_LINE_OUTLINE_HEX) >= sub.TWO_LINE_MIN_CONTRAST_RATIO, (
            f"{speaker}'s accent {colour} fails WCAG contrast against the outline {sub.TWO_LINE_OUTLINE_HEX}")
        assert sub._color_distance(colour, highlight) >= sub.TWO_LINE_MIN_COLOR_DISTANCE, (
            f"{speaker}'s accent {colour} is too close to the highlight {highlight}")
    for i in range(len(colours)):
        for j in range(i + 1, len(colours)):
            assert sub._color_distance(colours[i], colours[j]) >= sub.TWO_LINE_MIN_COLOR_DISTANCE, (
                f"{colours[i]} and {colours[j]} are too close to each other")


@pytest.mark.parametrize("style_id", ["fruit_drama", "family_3d"])
@pytest.mark.parametrize("build_lines", [_three_speaker_lines, _four_speaker_lines],
                          ids=["3_speakers", "4_speakers"])
def test_shipped_style_accents_are_readable_against_the_outline(style_id, build_lines):
    style = templates_mod.load_style(style_id)
    highlight = style["typography"]["highlight_colour"]
    accents = sub._speaker_accents(build_lines(), style["palette"], highlight)
    assert len(accents) == len(build_lines())
    _assert_accents_are_readable_distinct_and_far_from_highlight(accents, highlight)


@pytest.mark.parametrize("build_lines", [_three_speaker_lines, _four_speaker_lines],
                          ids=["3_speakers", "4_speakers"])
def test_live_fruit_drama_palette_accents_are_readable_against_the_outline(build_lines):
    """The exact palette from the live episode's style_lock.json: its own
    #1E1E24 accent must never be picked for any speaker (it is the accent
    that was actually unreadable in the scratch render), and its #ffd400
    accent -- identical to the highlight -- must never be picked either."""
    accents = sub._speaker_accents(build_lines(), LIVE_FRUIT_DRAMA_PALETTE, LIVE_FRUIT_DRAMA_HIGHLIGHT)
    assert len(accents) == len(build_lines())
    _assert_accents_are_readable_distinct_and_far_from_highlight(accents, LIVE_FRUIT_DRAMA_HIGHLIGHT)
    colours = {c.upper() for c in accents.values()}
    assert "#1E1E24" not in colours
    assert "#FFD400" not in colours


def test_the_reported_unreadable_accent_fails_contrast_against_the_black_outline():
    """Sanity-check on the bug report itself: #1E1E24 (the accent actually
    burned into subtitles.ass in the scratch render) fails the WCAG gate
    against the style's own black outline well before it would ever fail
    the (unrelated) colour-distance rule."""
    assert sub._contrast_ratio("#1E1E24", sub.TWO_LINE_OUTLINE_HEX) < sub.TWO_LINE_MIN_CONTRAST_RATIO


def test_neutral_fallback_colours_are_all_light_and_readable():
    for colour in sub.TWO_LINE_NEUTRAL_FALLBACK:
        assert sub._contrast_ratio(colour, sub.TWO_LINE_OUTLINE_HEX) >= sub.TWO_LINE_MIN_CONTRAST_RATIO, colour


def test_contrast_ratio_is_symmetric_bounded_and_one_for_identical_colours():
    assert sub._contrast_ratio("#1E1E24", "#000000") == sub._contrast_ratio("#000000", "#1E1E24")
    assert sub._contrast_ratio("#FFD400", "#FFD400") == pytest.approx(1.0, abs=1e-9)
    assert sub._contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, abs=1e-2)


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
    """DELIBERATE CHANGE (Tier-2, 2026-09-29): the displayed tokens are now
    the SCRIPT's own, matched to the provider's cues by normalised words
    (module docstring). A punctuation-only script token ("?") normalises to
    nothing (:func:`wordtiming.normalise`), so it can never match anything
    on either side (same rule ``wordtiming.align`` already applies to a
    dash or an ellipsis) -- the provider's own "?" cue is simply unused, and
    the script's "?" is interpolated from the end of "Vraiment"'s match to
    the line's own duration, same as :func:`wordtiming.align`'s own
    "spread to the end of the line's audio" rule for a trailing unmatched
    word. ``_join_spaced_punctuation`` still joins it to "Vraiment", now
    ending at the line's duration (0.8) rather than the provider's own
    (discarded) cue end (0.7)."""
    words = [{"word": "Vraiment", "start": 0.0, "end": 0.5}, {"word": "?", "start": 0.55, "end": 0.7}]
    spans, approx = sub._line_word_spans("Vraiment ?", 0.8, words)
    assert approx is False
    assert spans == [("Vraiment ?", 0.0, 0.8)]


def test_opening_guillemets_join_the_word_after_them():
    spans, _approx = sub._line_word_spans("« Bonjour » dit-il !", 2.0, None)
    assert [text for text, _start, _end in spans] == ["« Bonjour »", "dit-il !"]
    assert spans[0][1] == 0.0


def test_a_line_of_punctuation_alone_keeps_its_one_span():
    spans, _approx = sub._line_word_spans("?!", 1.0, None)
    assert [text for text, _start, _end in spans] == ["?!"]


# ================================================ 14. script text, provider timing
#
# Tier-2 (2026-09-29): Edge's own word-boundary cues (the sidecar ``words``)
# carry bare words -- no punctuation -- so displaying the provider's own
# tokens verbatim dropped "sécurité."'s period and "trompent."'s (last word
# of the live FR episode's line): "...en sécurité. Ils se trompent." showed
# as "sécurité Ils se trompent". The fix: display the SCRIPT's own tokens
# (``text.split()``), each timed from the provider cue it matches via
# ``wordtiming.align``'s difflib match on normalised words.

def _cues(*words_with_times):
    return [{"word": w, "start": s, "end": e} for w, s, e in words_with_times]


def test_provider_cues_without_punctuation_keep_the_scripts_own_punctuation():
    text = "Nos rivaux croient être en sécurité. Ils se trompent."
    bare = ["Nos", "rivaux", "croient", "être", "en", "sécurité", "Ils", "se", "trompent"]
    words = _cues(*[(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(bare)])
    spans, is_approx = sub._line_word_spans(text, len(bare) * 0.3, words)
    assert is_approx is False
    assert [t for t, _s, _e in spans] == [
        "Nos", "rivaux", "croient", "être", "en", "sécurité.", "Ils", "se", "trompent."]
    # Real provider timestamps still drive every span (never re-derived from
    # an even split): the matched "sécurité." keeps its own provider cue.
    securite = spans[5]
    assert securite[1] == pytest.approx(5 * 0.3) and securite[2] == pytest.approx(5 * 0.3 + 0.25)


def test_provider_cues_preserve_french_elision_and_apostrophes():
    text = "Tu n'as pas le choix, chérie."
    words = _cues(
        ("Tu", 0.0, 0.2), ("n’as", 0.25, 0.5), ("pas", 0.55, 0.8), ("le", 0.85, 1.0),
        ("choix", 1.05, 1.4), ("cherie", 1.5, 2.0),  # curly apostrophe + an accent-free "cherie"
    )
    spans, is_approx = sub._line_word_spans(text, 2.2, words)
    assert is_approx is False
    assert [t for t, _s, _e in spans] == ["Tu", "n'as", "pas", "le", "choix,", "chérie."]
    assert spans[0][1] == pytest.approx(0.0) and spans[-1][2] == pytest.approx(2.0)


def test_provider_cues_with_a_missing_word_interpolate_between_matched_neighbours():
    text = "Nos rivaux croient être en sécurité."
    # "être" was not heard by the provider (a missing cue).
    words = _cues(
        ("Nos", 0.0, 0.2), ("rivaux", 0.25, 0.55), ("croient", 0.6, 0.95),
        ("en", 1.3, 1.5), ("sécurité", 1.55, 1.9),
    )
    spans, is_approx = sub._line_word_spans(text, 2.0, words)
    assert is_approx is False
    assert [t for t, _s, _e in spans] == ["Nos", "rivaux", "croient", "être", "en", "sécurité."]
    etre_start, etre_end = spans[3][1], spans[3][2]
    assert 0.95 <= etre_start < etre_end <= 1.3  # interpolated inside the "croient" .. "en" gap
    for i in range(1, len(spans)):
        assert spans[i][1] >= spans[i - 1][2] - 1e-9  # never overlapping, never going back in time
    assert spans[-1][2] <= 2.0 + 1e-9  # last end never past the line's own duration


def test_provider_cues_with_an_extra_word_still_keep_the_scripts_own_tokens():
    text = "Ils se trompent."
    # The provider heard a stray repeated "se" that is not in the script.
    words = _cues(("Ils", 0.0, 0.2), ("se", 0.25, 0.4), ("se", 0.42, 0.55), ("trompent", 0.6, 0.95))
    spans, is_approx = sub._line_word_spans(text, 1.0, words)
    assert is_approx is False
    assert [t for t, _s, _e in spans] == ["Ils", "se", "trompent."]


def test_provider_cues_missing_a_spaced_punctuation_token_still_join_it_by_interpolation():
    text = "Vraiment ? Ils se trompent."
    words = _cues(("Vraiment", 0.0, 0.4), ("Ils", 0.6, 0.8), ("se", 0.85, 1.0), ("trompent", 1.05, 1.4))
    spans, is_approx = sub._line_word_spans(text, 1.5, words)
    assert is_approx is False
    assert [t for t, _s, _e in spans] == ["Vraiment ?", "Ils", "se", "trompent."]


def test_provider_cues_that_do_not_match_the_script_at_all_fall_back_to_the_provider_tokens():
    """``wordtiming.align`` returns ``None`` when not one word matches (its
    own contract) -- rather than lose the provider's real timing entirely,
    the provider's own bare tokens are used as-is, same as before this fix."""
    text = "Bonjour tout le monde"
    words = _cues(("hello", 0.0, 0.4), ("world", 0.5, 0.9))
    spans, is_approx = sub._line_word_spans(text, 1.0, words)
    assert is_approx is False
    assert [t for t, _s, _e in spans] == ["hello", "world"]


def test_word_pop_events_carry_the_scripts_punctuation_from_provider_word_timings():
    lines = _one_line("Vraiment ? Ils se trompent.", duration_s=1.5)
    words = {"l1": [
        {"word": "Vraiment", "start": 0.0, "end": 0.4}, {"word": "Ils", "start": 0.6, "end": 0.8},
        {"word": "se", "start": 0.85, "end": 1.0}, {"word": "trompent", "start": 1.05, "end": 1.4},
    ]}
    _styles, events, approx = sub.word_pop_dialogue(lines, word_timings=words, typography=TYPOGRAPHY)
    assert approx == {"l1": False}
    texts = [_strip_tags(ev.split(",", 9)[9]) for ev in events]
    assert texts == ["VRAIMENT ?", "ILS", "SE", "TROMPENT."]


def test_word_pop_events_keep_the_scripts_period_with_a_provider_missing_a_final_word():
    lines = _one_line("Nos rivaux croient être en sécurité. Ils se trompent.", duration_s=2.7, line_id="l1")
    bare = ["Nos", "rivaux", "croient", "être", "en", "sécurité", "Ils", "se", "trompent"]
    words = {"l1": [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(bare)]}
    _styles, events, approx = sub.word_pop_dialogue(lines, word_timings=words, typography=TYPOGRAPHY)
    assert approx == {"l1": False}
    texts = [_strip_tags(ev.split(",", 9)[9]) for ev in events]
    assert texts[-1] == "TROMPENT."
    assert "SÉCURITÉ." in texts


# =========================== 15. two_line accents, the phase-5 styles (DEC-169)
#
# Stage 12's own measurement: the plan named cinematic_real and claymation's
# palettes as suspicious (a near-black accent against a black outline); the
# storybook_watercolor palette (also two_line) is checked too. Read from the
# REAL shipped templates (``templates.load_style``), never a hand-picked
# fixture, exactly like section 12/13's own MVP-style coverage above.
#
# Measured directly with the production picker (no reimplementation): every
# one of the three styles' own dark/near-highlight palette entries already
# fails one of DEC-169's two hard gates and is therefore NEVER selected --
# the existing fallback mechanism (readability + highlight-distance +
# already-chosen-distinctness, relaxing onto TWO_LINE_NEUTRAL_FALLBACK) was
# already substituting correctly before this stage touched anything here.
# No template edit, no version bump: recorded as an A-entry for stage 14's
# live check instead (module docstring's own "if the substitution is
# visibly wrong" test -- it is not).

TWO_LINE_PHASE5_STYLES = ("cinematic_real", "claymation", "storybook_watercolor")

# Per style, the palette entries that fail :func:`sub._is_readable` against
# the black outline (measured with real _contrast_ratio numbers below) --
# these must NEVER appear in any :func:`sub._speaker_accents` result.
UNREADABLE_PALETTE_ENTRIES = {
    "cinematic_real": ("#0B1C2C", "#5C6B73"),   # contrast ~1.22 and ~3.81 (< 4.5)
    "claymation": ("#264653",),                  # contrast ~2.08
    "storybook_watercolor": ("#6B4F3A",),        # contrast ~2.80
}


@pytest.mark.parametrize("style_id", TWO_LINE_PHASE5_STYLES)
def test_two_line_style_has_a_palette_entry_that_fails_the_outline_contrast_gate(style_id):
    """Sanity-check on the measurement itself (mirrors section 12's own
    ``test_shipped_style_closest_primary_is_actually_close_to_its_own_
    highlight`` sanity-check): each of these three styles really does carry
    at least one palette entry unreadable against a black outline, which is
    exactly why this section exists."""
    style = templates_mod.load_style(style_id)
    palette_colours = style["palette"]["primary"] + style["palette"]["accents"]
    for expected_bad in UNREADABLE_PALETTE_ENTRIES[style_id]:
        assert expected_bad in palette_colours
        assert sub._contrast_ratio(expected_bad, sub.TWO_LINE_OUTLINE_HEX) < sub.TWO_LINE_MIN_CONTRAST_RATIO


def _assert_accents_are_readable_and_far_from_highlight(accents, highlight):
    """The two HARD DEC-169 gates only (readability, highlight-distance) --
    unlike section 13's ``_assert_accents_are_readable_distinct_and_far_
    from_highlight``, this does NOT require every speaker to get a distinct
    colour: :func:`sub._pick_next_accent`'s own docstring says the
    already-chosen-distinctness check is relaxed, and cycles the palette
    again, once a small palette runs out of mutually distinct options --
    see ``test_two_line_phase5_style_distinctness_is_limited_by_a_small_
    palette`` below for exactly where that happens for these three styles."""
    for speaker, colour in accents.items():
        assert sub._contrast_ratio(colour, sub.TWO_LINE_OUTLINE_HEX) >= sub.TWO_LINE_MIN_CONTRAST_RATIO, (
            f"{speaker}'s accent {colour} fails WCAG contrast against the outline")
        assert sub._color_distance(colour, highlight) >= sub.TWO_LINE_MIN_COLOR_DISTANCE, (
            f"{speaker}'s accent {colour} is too close to the highlight {highlight}")


@pytest.mark.parametrize("style_id", TWO_LINE_PHASE5_STYLES)
@pytest.mark.parametrize("build_lines", [_three_speaker_lines, _four_speaker_lines],
                          ids=["3_speakers", "4_speakers"])
def test_two_line_phase5_style_accents_are_readable_and_far_from_highlight(style_id, build_lines):
    style = templates_mod.load_style(style_id)
    highlight = style["typography"]["highlight_colour"]
    accents = sub._speaker_accents(build_lines(), style["palette"], highlight)
    assert len(accents) == len(build_lines())
    _assert_accents_are_readable_and_far_from_highlight(accents, highlight)


# Finding (measured, not a bug): cinematic_real and claymation's usable
# palette -- once the outline-contrast and highlight-distance HARD gates
# (above) strip out their dark/near-highlight entries -- is down to just 1-2
# colours, so :func:`sub._pick_next_accent`'s documented relaxation (reuse a
# colour once the palette can no longer keep every speaker distinct) kicks
# in as early as the 2nd/3rd speaker; storybook_watercolor's is a little
# larger and only collides at a 4th speaker. Every accent stays readable
# regardless (DEC-169's hard gate never relaxes) -- this is a distinctness
# ceiling, not a legibility bug, so no template edit/version bump: recorded
# as an A-entry for stage 14's live check (module docstring: "only if the
# substitution is visibly wrong").
TWO_LINE_PHASE5_COLLISIONS = {
    # style_id: {n_speakers: expected duplicate accent}
    "cinematic_real": {3: "#F2F2F2", 4: "#F2F2F2"},
    "claymation": {3: "#2A9D8F", 4: "#2A9D8F"},
    "storybook_watercolor": {4: "#A7C7E7"},  # 3 speakers stay fully distinct
}


@pytest.mark.parametrize("style_id,n_speakers,dup_colour", [
    (style_id, n, colour)
    for style_id, by_n in TWO_LINE_PHASE5_COLLISIONS.items()
    for n, colour in by_n.items()
])
def test_two_line_phase5_style_distinctness_is_limited_by_a_small_palette(style_id, n_speakers, dup_colour):
    style = templates_mod.load_style(style_id)
    highlight = style["typography"]["highlight_colour"]
    build_lines = _three_speaker_lines if n_speakers == 3 else _four_speaker_lines
    accents = sub._speaker_accents(build_lines(), style["palette"], highlight)
    colours = list(accents.values())
    assert len(set(colours)) < len(colours), (
        f"{style_id} at {n_speakers} speakers: expected a repeated accent (small-palette ceiling), got all-distinct")
    assert colours.count(dup_colour) >= 2


def test_storybook_watercolor_three_speakers_stay_fully_distinct():
    """The one case among the three where the palette is just big enough:
    3 speakers, unlike cinematic_real/claymation at the same count."""
    style = templates_mod.load_style("storybook_watercolor")
    highlight = style["typography"]["highlight_colour"]
    accents = sub._speaker_accents(_three_speaker_lines(), style["palette"], highlight)
    colours = list(accents.values())
    assert len(set(colours)) == len(colours)


@pytest.mark.parametrize("style_id", TWO_LINE_PHASE5_STYLES)
@pytest.mark.parametrize("build_lines", [_three_speaker_lines, _four_speaker_lines],
                          ids=["3_speakers", "4_speakers"])
def test_two_line_phase5_style_never_selects_its_own_unreadable_palette_entry(style_id, build_lines):
    """The exact DEC-169 acceptance: the accent the renderer actually burns
    is never one of the style's own dark/unreadable palette entries -- the
    fallback ramp substitutes for it every time, never leaving the
    near-invisible original colour in the chosen set."""
    style = templates_mod.load_style(style_id)
    highlight = style["typography"]["highlight_colour"]
    accents = sub._speaker_accents(build_lines(), style["palette"], highlight)
    chosen = {c.upper() for c in accents.values()}
    for bad in UNREADABLE_PALETTE_ENTRIES[style_id]:
        assert bad.upper() not in chosen, f"{style_id}: unreadable {bad} was substituted into a speaker's accent"


def test_cinematic_real_speaker_accents_are_the_expected_colours():
    """Golden (measured against the real shipped template, 2026-09-30):
    primary ``#0B1C2C, #D98E04, #5C6B73`` and accent ``#F2F2F2`` against
    highlight ``#D98E04``. ``#0B1C2C``/``#5C6B73`` fail the outline-contrast
    gate; ``#D98E04`` is the highlight itself (distance 0). Only ``#F2F2F2``
    ever clears every gate from the style's own palette -- every further
    speaker falls to :data:`sub.TWO_LINE_NEUTRAL_FALLBACK`, relaxed to
    reuse ``#F2F2F2`` once the ramp's own distinct-from-each-other budget
    (3 speakers apart) is exhausted."""
    style = templates_mod.load_style("cinematic_real")
    accents = sub._speaker_accents(_three_speaker_lines(), style["palette"], style["typography"]["highlight_colour"])
    assert accents == {"char_a": "#F2F2F2", "char_b": "#8B8B8B", "char_c": "#F2F2F2"}


def test_claymation_speaker_accents_are_the_expected_colours():
    """Golden: primary ``#E76F51, #2A9D8F, #E9C46A``, accents ``#264653,
    #F4F1DE`` against highlight ``#E9C46A``. ``#264653`` fails the outline
    gate; ``#E9C46A`` is the highlight itself; ``#E76F51`` is only ~88.6
    units from the highlight (< the 100-unit gate) and is excluded too --
    only ``#2A9D8F`` and ``#F4F1DE`` remain."""
    style = templates_mod.load_style("claymation")
    accents = sub._speaker_accents(_three_speaker_lines(), style["palette"], style["typography"]["highlight_colour"])
    assert accents == {"char_a": "#2A9D8F", "char_b": "#F4F1DE", "char_c": "#2A9D8F"}


def test_storybook_watercolor_speaker_accents_are_the_expected_colours():
    """Golden: primary ``#A7C7E7, #F4A6A6, #C9E4CA``, accent ``#6B4F3A``
    against highlight ``#F4A6A6``. ``#6B4F3A`` fails the outline gate;
    ``#F4A6A6`` is the highlight itself; ``#C9E4CA`` is only ~83.6 units
    from the highlight and is excluded too -- ``#A7C7E7`` and the neutral
    fallback (``#FFFFFF``, then ``#8B8B8B``) carry every speaker."""
    style = templates_mod.load_style("storybook_watercolor")
    accents = sub._speaker_accents(_three_speaker_lines(), style["palette"], style["typography"]["highlight_colour"])
    assert accents == {"char_a": "#A7C7E7", "char_b": "#FFFFFF", "char_c": "#8B8B8B"}
