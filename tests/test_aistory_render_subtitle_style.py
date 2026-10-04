"""A story's own subtitle look in the renderer (plan 23 stage B5).

``look=None`` -- every story without ``subtitle_style`` -- is byte for byte
what the builders always gave (the B6 fixture in
``tests/test_aistory_render_geometry.py`` pins the portrait documents; the
three goldens pin the render); a look changes the dialogue's size, place,
colours, outline and box only. Sections:

1. ``_style_line``'s new keywords and their defaults
2. word_pop under each override (exact style and event lines)
3. two_line under each override
4. a box is libass ``BorderStyle 3``; the hook keeps its own look
5. absent look: the same bytes, builder by builder
6. the plan: only the final pass's text (and the end card's font) moves

No ffmpeg is run here (string builders and the plan only).
"""

from __future__ import annotations

import pytest

from clipping.aistory import subtitle_style
from clipping.aistory.render import golden, subtitles as sub
from clipping.aistory.render import plan as plan_mod
from clipping.aistory.render import timeline as rt

TYPOGRAPHY = {"font_family": "Montserrat Black", "subtitle_mode": "word_pop", "highlight_colour": "#FFD400",
              "ai_label": True}
LOCK = {"typography": dict(TYPOGRAPHY)}
PALETTE = {"primary": ["#F2C14E", "#E4572E", "#3A7D44"], "accents": ["#FFFFFF", "#1E1E24"]}
FFMPEG = {"version": "6.1.1-3ubuntu5", "machine": "aarch64"}
LINES = [
    {"line_id": "l1", "speaker": "char_a", "start_s": 0.0, "duration_s": 1.0, "text": "Hello world"},
    {"line_id": "l2", "speaker": "char_b", "start_s": 1.5, "duration_s": 1.0, "text": "Not again"},
]

DEFAULT_WORD_POP = ("Style: WordPop,Montserrat Black,62,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
                    "-1,-1,0,0,100,100,0,0,1,3,0,5,0,0,0,1")


def _look(**style):
    return subtitle_style.resolve(LOCK, {"subtitle_style": style})


def _word_pop(look=None):
    return sub.word_pop_dialogue(LINES, typography=TYPOGRAPHY, look=look)


def _two_line(look=None):
    return sub.two_line_dialogue(LINES, palette=PALETTE, typography=TYPOGRAPHY, look=look)


# ============================================== 1. _style_line

def test_style_line_defaults_are_todays_exact_string():
    assert sub._style_line("WordPop", "Montserrat Black", 62, "#FFFFFF", "#000000", bold=True, italic=True,
                           outline_px=3, alignment=5) == DEFAULT_WORD_POP
    assert sub._style_line("WordPop", "Montserrat Black", 62, "#FFFFFF", "#000000", bold=True, italic=True,
                           outline_px=3, alignment=5, border_style=1, back=None) == DEFAULT_WORD_POP


def test_style_line_takes_a_border_style_a_back_colour_and_an_outline_alpha():
    line = sub._style_line("X", "F", 40, "#FFFFFF", "#202020", outline_px=10, border_style=3,
                           back="&H66202020", outline_alpha="66")
    assert line == "Style: X,F,40,&H00FFFFFF,&H00FFFFFF,&H66202020,&H66202020,-1,0,0,0,100,100,0,0,3,10,0,5,0,0,0,1"


# ============================================== 2. word_pop

def test_the_default_word_pop_style_is_unchanged():
    assert _word_pop()[0] == [DEFAULT_WORD_POP]


def test_word_pop_size_is_a_percentage_of_the_styles_own():
    [style] = _word_pop(_look(size_pct=150))[0]
    assert style == DEFAULT_WORD_POP.replace(",62,", ",93,")
    [style] = _word_pop(_look(size_pct=60))[0]
    assert style == DEFAULT_WORD_POP.replace(",62,", ",37,")


def test_word_pop_position_is_the_words_centre_in_percent_of_the_frame_height():
    events = _word_pop(_look(position_pct=50))[1]
    assert events[0].startswith("Dialogue: 0,0:00:00.00,0:00:00.50,WordPop,,0,0,0,,{\\an5\\pos(540,960)")
    assert all("\\pos(540,960)" in event for event in events)
    assert "\\pos(540,1488)" in _word_pop()[1][0]


def test_word_pop_text_colour_outline_width_and_outline_colour():
    [style] = _word_pop(_look(text_colour="#FFFF00"))[0]
    assert style == DEFAULT_WORD_POP.replace("&H00FFFFFF,&H00FFFFFF", "&H0000FFFF,&H0000FFFF")
    [style] = _word_pop(_look(outline_px=6, outline_colour="#112233"))[0]
    assert style == DEFAULT_WORD_POP.replace("&H00000000", "&H00332211").replace(",1,3,0,5,", ",1,6,0,5,")
    [style] = _word_pop(_look(outline_px=0))[0]
    assert ",1,0,0,5," in style


def test_word_pop_with_every_override_at_once():
    look = _look(size_pct=120, position_pct=40, text_colour="#FFFF00", outline_px=4, outline_colour="#112233")
    style_lines, events, _approx = _word_pop(look)
    assert style_lines == ["Style: WordPop,Montserrat Black,74,&H0000FFFF,&H0000FFFF,&H00332211,&H80000000,"
                           "-1,-1,0,0,100,100,0,0,1,4,0,5,0,0,0,1"]
    assert events[0] == ("Dialogue: 0,0:00:00.00,0:00:00.50,WordPop,,0,0,0,,"
                         "{\\an5\\pos(540,768)\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)}HELLO")


# ============================================== 3. two_line

def test_two_line_size_and_bottom_edge():
    base = _two_line()[0]
    styles = _two_line(_look(size_pct=90, position_pct=90))[0]
    assert len(styles) == len(base) == 2
    for plain, changed in zip(base, styles):
        assert ",56," in plain and ",346,1" in plain
        assert changed == plain.replace(",56,", ",50,").replace(",346,1", ",192,1")


def test_two_line_keeps_each_speakers_accent_whatever_the_text_colour():
    base_styles, base_events, _ = _two_line()
    styles, events, _ = _two_line(_look(text_colour="#FF0000"))  # word_pop's own field
    assert (styles, events) == (base_styles, base_events)


def test_two_line_takes_the_highlight_colour_for_the_word_being_spoken():
    _styles, base_events, _ = _two_line()
    _styles, events, _ = _two_line(_look(highlight_colour="#00FF00"))
    assert "\\c&H00D4FF&" in base_events[0] and "\\c&H00FF00&" in events[0]
    assert "\\c&H00D4FF&" not in events[0]


def test_two_line_outline_width_and_colour():
    styles = _two_line(_look(outline_px=5, outline_colour="#112233"))[0]
    for line in styles:
        assert "&H00332211," in line and ",1,5,0,2,0,0,346,1" in line


def test_two_line_accents_stay_readable_on_a_light_box():
    look = _look(box={"colour": "#F0F0F0", "opacity_pct": 100}, highlight_colour="#202020")
    styles = _two_line(look)[0]
    colours = [line.split(",")[3] for line in styles]  # PrimaryColour, &H00BBGGRR
    for colour in colours:
        bgr = colour[4:]
        hex_colour = "#" + bgr[4:6] + bgr[2:4] + bgr[0:2]
        assert sub._contrast_ratio(hex_colour, "#F0F0F0") >= sub.TWO_LINE_MIN_CONTRAST_RATIO, colour


# ============================================== 4. the box; the hook

def test_a_box_is_border_style_3_in_the_box_colour_with_its_opacity():
    box = {"colour": "#202020", "opacity_pct": 60}
    [style] = _word_pop(_look(box=box))[0]
    # opacity 60 % = alpha 0x66; the box colour is the OutlineColour (and the BackColour); the Outline
    # field is the box's padding (10 px)
    assert style == ("Style: WordPop,Montserrat Black,62,&H00FFFFFF,&H00FFFFFF,&H66202020,&H66202020,"
                     "-1,-1,0,0,100,100,0,0,3,10,0,5,0,0,0,1")
    [opaque] = _word_pop(_look(box={"colour": "#202020", "opacity_pct": 100}))[0]
    assert "&H00202020,&H00202020" in opaque
    [clear] = _word_pop(_look(box={"colour": "#202020", "opacity_pct": 0}))[0]
    assert "&HFF202020,&HFF202020" in clear


def test_a_box_turns_the_outline_off_whatever_the_outline_fields_say():
    box = {"colour": "#202020", "opacity_pct": 60}
    plain = _word_pop(_look(box=box))[0]
    assert _word_pop(_look(box=box, outline_px=7, outline_colour="#FF00FF"))[0] == plain
    for style in _two_line(_look(box=box))[0]:
        assert ",3,10,0,2,0,0,346,1" in style and "&H66202020,&H66202020" in style


def test_no_box_is_border_style_1_even_for_box_null():
    assert _word_pop(_look(box=None))[0] == [DEFAULT_WORD_POP]


def test_the_hook_keeps_its_own_look_whatever_the_story_picks():
    plain = sub.hook_overlay_events("He knew all along", 1.9, typography=TYPOGRAPHY)
    loud = _look(size_pct=160, position_pct=20, text_colour="#000000", outline_colour="#FFFFFF",
                 box={"colour": "#FFFFFF", "opacity_pct": 100})
    assert sub.hook_overlay_events("He knew all along", 1.9, typography=TYPOGRAPHY, look=loud) == plain
    assert sub.hook_overlay_events("", 1.9, typography=TYPOGRAPHY, look=loud) == ([], [])


# ============================================== 5. absent look: the same bytes

def _docs():
    """``(timeline, script)`` of the golden fixture, with a hook text so the
    hook overlay is built too (the B6 test's own inputs)."""
    docs = golden.build_documents()
    script = docs["script"]
    script["hook"] = {"on_screen_text": "He knew all along"}
    timeline = rt.build_timeline(script, docs["storyboard"], docs["template"], golden.STORY["language"],
                                 style_lock=docs["style_lock"])
    return timeline, script


@pytest.mark.parametrize("mode", sub.SUBTITLE_MODES)
@pytest.mark.parametrize("hook_style", ("text_overlay", "insert_prop"))
def test_a_document_without_a_look_is_the_document_it_always_was(mode, hook_style):
    timeline, script = _docs()
    kwargs = dict(timeline=timeline, script=script, subtitle_mode=mode, language="en", hook_style=hook_style,
                  ai_label_enabled=True, palette=PALETTE, typography=TYPOGRAPHY, word_timings=golden.WORD_TIMINGS)
    assert sub.build_subtitles_ass(**kwargs, look=None) == sub.build_subtitles_ass(**kwargs)


@pytest.mark.parametrize("mode", ("word_pop", "two_line"))
def test_a_look_that_changes_nothing_gives_the_same_bytes_as_no_look(mode):
    timeline, script = _docs()
    kwargs = dict(timeline=timeline, script=script, subtitle_mode=mode, language="en", hook_style="text_overlay",
                  ai_label_enabled=True, palette=PALETTE, typography=TYPOGRAPHY, word_timings=golden.WORD_TIMINGS)
    neutral = subtitle_style.resolve(LOCK, {"subtitle_style": {"size_pct": 100}})
    assert neutral.size_pct == 100 and neutral.box is None
    assert sub.build_subtitles_ass(**kwargs, look=neutral) == sub.build_subtitles_ass(**kwargs)


def test_the_builders_without_a_look_give_the_lines_of_the_literal_constants():
    styles, events, _ = _word_pop()
    assert styles == [DEFAULT_WORD_POP] and events[0].endswith(
        "{\\an5\\pos(540,1488)\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)}HELLO")
    two = _two_line()[0]
    assert all(",56," in s and ",1,2,0,2,0,0,346,1" in s for s in two)


def test_the_full_document_with_a_look_differs_only_in_the_dialogue_styles_and_positions():
    timeline, script = _docs()
    kwargs = dict(timeline=timeline, script=script, subtitle_mode="word_pop", language="en",
                  hook_style="text_overlay", ai_label_enabled=True, palette=PALETTE, typography=TYPOGRAPHY,
                  word_timings=golden.WORD_TIMINGS)
    plain, _ = sub.build_subtitles_ass(**kwargs)
    shaped, _ = sub.build_subtitles_ass(**kwargs, look=_look(size_pct=120, position_pct=50))
    before, after = plain.splitlines(), shaped.splitlines()
    assert len(before) == len(after)
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert changed and all(("WordPop" in a) for a, _b in changed)
    # the hook and the AI label lines are untouched
    assert [l for l in after if l.startswith(("Style: Hook", "Style: AiLabel"))] == \
        [l for l in before if l.startswith(("Style: Hook", "Style: AiLabel"))]


# ============================================== 6. the plan

def _plan(tmp_path, *, look=None, font_family=None, **extra):
    from clipping.aistory.render import fonts

    inputs = golden.write_sources(tmp_path)
    if font_family is not None:
        inputs["font"] = fonts.resolve_font(font_family, custom_fonts_dir=tmp_path / "no_custom")
    kwargs = dict(extra)
    if look is not None:
        kwargs["look"] = look
    return plan_mod.build_render_plan(**golden.build_documents(), story=dict(golden.STORY), ep=golden.EP,
                                      inputs=inputs, ffmpeg=dict(FFMPEG), profile="golden", **kwargs)


def _stage_keys(plan):
    return {stage["id"]: stage["cache_key"] for stage in plan["stages"]}


def test_a_plan_without_a_look_is_the_plan_it_always_was(tmp_path):
    assert _plan(tmp_path) == _plan(tmp_path, look=None)


def test_a_look_moves_the_subtitles_text_and_no_shot_or_end_card_key(tmp_path):
    plain = _plan(tmp_path)
    shaped = _plan(tmp_path, look=subtitle_style.resolve(
        golden.STYLE_LOCK, {"subtitle_style": {"size_pct": 130, "position_pct": 50,
                                               "box": {"colour": "#202020", "opacity_pct": 60}}}))
    plain_files = {item["path"]: item["text"] for item in plain["files"]}
    shaped_files = {item["path"]: item["text"] for item in shaped["files"]}
    assert plain_files.keys() == shaped_files.keys()
    assert plain_files["subtitles.ass"] != shaped_files["subtitles.ass"]
    assert ",3,10,0,5," in shaped_files["subtitles.ass"]
    for path in plain_files.keys() - {"subtitles.ass"}:
        assert plain_files[path] == shaped_files[path]  # the end card's text is the same
    # every cached stage (the shots, the end card) keeps its key; the argv of every stage is the same
    assert _stage_keys(plain) == _stage_keys(shaped)
    assert [s["argv"] for s in plain["stages"]] == [s["argv"] for s in shaped["stages"]]
    assert plain["font"] == shaped["font"] and plain["params"] == shaped["params"]


def test_a_font_override_moves_every_text_layer_and_only_the_end_cards_key(tmp_path):
    plain = _plan(tmp_path)
    look = subtitle_style.resolve(golden.STYLE_LOCK, {"subtitle_style": {"font_family": "Bangers"}})
    shaped = _plan(tmp_path, look=look, font_family=look.font_family)
    assert shaped["font"]["family"] == "Bangers" and plain["font"]["family"] != "Bangers"
    before, after = _stage_keys(plain), _stage_keys(shaped)
    moved = sorted(stage for stage in before if before[stage] != after[stage])
    assert moved == ["E"]  # the shots carry no text: their cache keys do not move
    text = {item["path"]: item["text"] for item in shaped["files"]}
    assert "Style: WordPop,Bangers," in text["subtitles.ass"]
    assert all("Bangers" in text[path] for path in text if path != "subtitles.ass")
