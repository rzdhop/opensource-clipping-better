"""A story's own subtitle look (plan 23 stage B5): ``story.json``'s optional
``subtitle_style`` -- its schema, its validation (the ranges, the shipped
fonts, the 4.5 contrast between the text and its outline or box), its
resolution over the style lock, how the store writes it (atomically, at any
time, the style lock's freeze included) and how the render step reads it.

The render-step tests use the render step's own fixtures
(``tests/test_story_render_step.py``): no ffmpeg runs, every process is the
fake of ``tests/test_aistory_render_runner.py``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_story_render_step as trs
from clipping.aistory import schemas, store as store_mod, stylelock, subtitle_style, templates
from clipping.aistory.render import fonts
from test_story_assets_step import hermetic, store  # noqa: F401 -- fixtures
from test_story_render_step import built  # noqa: F401 -- fixture

NOW = "2026-10-04T10:00:00+00:00"
LATER = "2026-10-04T11:00:00+00:00"
FONTS_INDEX = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "fonts_index.json"

FULL = {
    "font_family": "Bangers", "size_pct": 120, "position_pct": 60, "text_colour": "#FFFF00",
    "highlight_colour": "#00FF00", "outline_px": 4, "outline_colour": "#112233",
    "box": {"colour": "#202020", "opacity_pct": 60},
}


@pytest.fixture
def outputs(tmp_path):
    path = tmp_path / "outputs"
    path.mkdir()
    return path


@pytest.fixture
def stories(outputs):
    return store_mod.StoryStore(str(outputs), on_log=lambda _m: None)


def _story(stories, template="fruit_drama"):
    return stories.create(language="en", style_template_id=template, now=NOW)["story_id"]


def _lock(template="fruit_drama", *, overrides=None, frozen=False):
    lock = stylelock.build_style_lock(templates.load_style(template), overrides, now=NOW)
    return stylelock.lock_style(lock, now=NOW) if frozen else lock


# ===================================================== the schema

def test_the_shipped_families_are_the_ones_the_fonts_index_lists():
    index = json.loads(FONTS_INDEX.read_text(encoding="utf-8"))
    families = tuple(entry["family"] for entry in index["fonts"])
    assert families == schemas.SUBTITLE_FONT_FAMILIES == subtitle_style.shipped_families()
    assert "Montserrat" in families and len(families) == 6


def test_a_story_without_the_key_is_valid_and_so_is_one_with_every_field(stories):
    story = stories.get(_story(stories))
    assert "subtitle_style" not in story and schemas.story_bible_errors(story) == []
    story["subtitle_style"] = copy.deepcopy(FULL)
    assert schemas.story_bible_errors(story) == []
    story["subtitle_style"] = {}
    assert schemas.story_bible_errors(story) == []


@pytest.mark.parametrize("bad", [
    {"font_family": "Comic Sans"},
    {"size_pct": 59}, {"size_pct": 161}, {"size_pct": 100.5}, {"size_pct": True},
    {"position_pct": 14}, {"position_pct": 96},
    {"outline_px": -1}, {"outline_px": 9},
    {"text_colour": "white"}, {"highlight_colour": "#FFF"}, {"outline_colour": "#GGGGGG"},
    {"box": {"colour": "#000000"}}, {"box": {"colour": "#000000", "opacity_pct": 101}},
    {"box": {"colour": "#000000", "opacity_pct": -1}}, {"box": {"colour": "#000000", "opacity_pct": 50, "x": 1}},
    {"box": "yes"}, {"surprise": 1},
])
def test_the_schema_refuses_a_value_out_of_its_range_or_an_unknown_key(bad, stories):
    story = stories.get(_story(stories))
    story["subtitle_style"] = bad
    assert schemas.story_bible_errors(story) != []
    assert subtitle_style.validate(bad) != []


@pytest.mark.parametrize("good", [
    {"size_pct": 60}, {"size_pct": 160}, {"position_pct": 15}, {"position_pct": 94.5}, {"outline_px": 0},
    {"outline_px": 8}, {"box": None}, {"box": {"colour": "#000000", "opacity_pct": 0}},
    {"box": {"colour": "#000000", "opacity_pct": 100}}, {"font_family": "Luckiest Guy"},
])
def test_the_ends_of_every_range_are_valid(good):
    assert subtitle_style.validate(good) == []


def test_only_an_object_is_a_subtitle_style():
    assert subtitle_style.validate("x") == ["$: expected an object, got str"]
    assert subtitle_style.validate({}) == []


# ===================================================== the contrast rule

def test_the_text_must_clear_4_5_against_its_outline_and_the_ratio_is_named():
    [error] = subtitle_style.validate({"text_colour": "#777777", "outline_colour": "#888888"})
    assert "text_colour #777777" in error and "outline colour #888888" in error and "1.26" in error
    assert "4.5" in error


def test_the_default_white_text_is_checked_against_a_light_outline_or_box():
    [error] = subtitle_style.validate({"outline_colour": "#EEEEEE"})
    assert "text_colour #FFFFFF" in error and "outline colour #EEEEEE" in error
    [error] = subtitle_style.validate({"box": {"colour": "#FFFFFF", "opacity_pct": 90}})
    assert "box colour #FFFFFF" in error


def test_a_box_replaces_the_outline_in_the_rule():
    # the outline colour would fail, but a box is drawn instead of the outline: only the box counts
    style = {"outline_colour": "#FFFFFF", "box": {"colour": "#000000", "opacity_pct": 50}}
    assert subtitle_style.validate(style) == []
    style = {"text_colour": "#222222", "box": {"colour": "#000000", "opacity_pct": 50}}
    assert subtitle_style.validate(style) != []


def test_the_highlight_must_read_on_the_plate_too_and_no_outline_means_no_check():
    [error] = subtitle_style.validate({"highlight_colour": "#101010"})
    assert "highlight_colour #101010" in error
    assert subtitle_style.validate({"highlight_colour": "#101010", "outline_px": 0}) == []
    assert subtitle_style.validate({"text_colour": "#000000", "outline_colour": "#FFFFFF"}) == []
    assert subtitle_style.validate({"size_pct": 100}) == []


def test_the_contrast_reuses_the_renderers_own_function():
    from clipping.aistory.render import subtitles

    assert subtitle_style._subtitles._contrast_ratio is subtitles._contrast_ratio
    assert subtitle_style.MIN_CONTRAST_RATIO == subtitles.TWO_LINE_MIN_CONTRAST_RATIO == 4.5


# ===================================================== the resolution order

def test_a_story_without_a_look_has_none_and_the_override_resolves_over_the_lock_over_the_template():
    assert subtitle_style.look_for(_lock(), {}) is None
    assert subtitle_style.look_for(_lock(), {"subtitle_style": {}}) is None
    assert not subtitle_style.active({"title": "x"})

    template = templates.load_style("anime")
    # nothing overridden: the template's own font and highlight
    look = subtitle_style.resolve(_lock("anime"), {"subtitle_style": {"size_pct": 80}})
    assert look.font_family == template["typography"]["font_family"] == "Bangers"
    assert look.highlight_colour == template["typography"]["highlight_colour"]
    assert (look.size_pct, look.position_pct, look.text_colour, look.outline_px, look.box) == (80, None, None, None, None)

    # a pre-lock override of the lock's typography beats the template ...
    lock = _lock("anime", overrides={"typography.font_family": "Chewy", "typography.highlight_colour": "#00FFAA"})
    look = subtitle_style.resolve(lock, {"subtitle_style": {"size_pct": 80}})
    assert (look.font_family, look.highlight_colour) == ("Chewy", "#00FFAA")

    # ... and the story's own override beats both
    look = subtitle_style.resolve(lock, {"subtitle_style": {"font_family": "Bebas Neue",
                                                           "highlight_colour": "#FF0000"}})
    assert (look.font_family, look.highlight_colour) == ("Bebas Neue", "#FF0000")


def test_every_field_reaches_the_look():
    look = subtitle_style.resolve(_lock(), {"subtitle_style": copy.deepcopy(FULL)})
    assert look == subtitle_style.Look(
        font_family="Bangers", highlight_colour="#00FF00", size_pct=120, position_pct=60, text_colour="#FFFF00",
        outline_px=4, outline_colour="#112233", box=subtitle_style.Box("#202020", 60))
    assert subtitle_style.resolve(_lock(), {"subtitle_style": {"box": None}}).box is None


def test_the_override_font_resolves_through_the_font_picker_to_a_shipped_file(tmp_path):
    for family in schemas.SUBTITLE_FONT_FAMILIES:
        record = fonts.resolve_font(family, custom_fonts_dir=tmp_path / "none")
        assert record["file"].startswith("assets/fonts/")
        if family == "Montserrat":  # the committed fallback face
            assert record["family"] == fonts.FALLBACK_FONT_FAMILY
        else:
            assert record["family"] == family


# ===================================================== the store

def test_the_store_writes_the_look_atomically_and_clears_it(stories, outputs):
    story_id = _story(stories)
    saved = stories.set_subtitle_style(story_id, copy.deepcopy(FULL), now=LATER)
    assert saved["subtitle_style"] == FULL and saved["updated_at"] == LATER
    on_disk = json.loads((outputs / "stories" / story_id / "story.json").read_text(encoding="utf-8"))
    assert on_disk["subtitle_style"] == FULL
    assert not [p for p in outputs.rglob("*") if p.name.startswith(".story-") or p.suffix == ".tmp"]
    assert stories.get(story_id)["subtitle_style"] == FULL

    for nothing in (None, {}):
        stories.set_subtitle_style(story_id, copy.deepcopy(FULL), now=LATER)
        assert "subtitle_style" not in stories.set_subtitle_style(story_id, nothing, now=LATER)
        assert "subtitle_style" not in stories.get(story_id)


def test_a_refused_look_writes_nothing_and_names_every_error(stories, outputs):
    story_id = _story(stories)
    path = outputs / "stories" / story_id / "story.json"
    before = path.read_bytes()
    with pytest.raises(schemas.SchemaError) as caught:
        stories.set_subtitle_style(story_id, {"size_pct": 500, "text_colour": "#808080",
                                              "outline_colour": "#8A8A8A"}, now=LATER)
    assert caught.value.name == "subtitle_style" and len(caught.value.errors) == 1  # the schema stops first
    with pytest.raises(schemas.SchemaError) as caught:
        stories.set_subtitle_style(story_id, {"text_colour": "#808080", "outline_colour": "#8A8A8A"}, now=LATER)
    assert "contrast ratio" in caught.value.errors[0]
    assert path.read_bytes() == before


def test_the_look_is_editable_after_the_style_lock_froze_and_touches_no_approval(stories):
    story_id = _story(stories)
    lock = _lock(frozen=True)
    stories.write_doc(story_id, "style_lock.json", lock, now=NOW, validator=schemas.style_lock_errors)
    with pytest.raises(stylelock.StyleLockError):
        stylelock.apply_overrides(lock, {"typography.font_family": "Chewy"}, now=LATER)  # the lock is frozen

    before = stories.get(story_id)
    saved = stories.set_subtitle_style(story_id, {"size_pct": 130, "font_family": "Chewy"}, now=LATER)

    assert saved["subtitle_style"] == {"size_pct": 130, "font_family": "Chewy"}
    assert saved["approvals"] == before["approvals"] and saved["status"] == before["status"]
    assert stories.read_doc(story_id, "style_lock.json") == dict(lock)  # the lock is untouched
    look = subtitle_style.resolve(stories.read_doc(story_id, "style_lock.json"), saved)
    assert (look.font_family, look.size_pct) == ("Chewy", 130)


# ===================================================== the render step

def _set_look(store, story_id, style):
    store.set_subtitle_style(story_id, style, now=LATER)


def test_plan_args_hand_the_plan_a_look_only_for_a_story_that_has_one():
    from clipping.aistory.steps import render as step

    ec = SimpleNamespace(
        style_lock=_lock(), template={"template_id": "t"}, ep=1, story_id="s", language="en",
        story={"title": "T", "subtitle_style": {"size_pct": 110}})
    args = step.plan_args(ec, {}, {}, {}, {}, subtitles=None, encoder="libx264")
    assert args["look"] == subtitle_style.resolve(ec.style_lock, ec.story) and args["look"].size_pct == 110

    for story in ({"title": "T"}, {"title": "T", "subtitle_style": {}}):
        ec.story = story
        assert "look" not in step.plan_args(ec, {}, {}, {}, {}, subtitles=None, encoder="libx264")


def test_render_inputs_resolve_the_override_font_else_the_locks(store, tmp_path, built):  # noqa: F811
    step = trs._render_mod()
    story_id = trs.episode(store, tmp_path, built)

    def font_of():
        ec = trs._ec(store, story_id)
        script, board, assets_doc = step.require_renderable(ec)
        return step.render_inputs(ec, script, board, assets_doc, custom_fonts_dir=tmp_path / "none")["font"]["family"]

    plain = font_of()
    _set_look(store, story_id, {"size_pct": 120})
    assert font_of() == plain  # no font in the look: the lock's font
    _set_look(store, story_id, {"font_family": "Luckiest Guy"})
    assert font_of() == "Luckiest Guy"
    _set_look(store, story_id, None)
    assert font_of() == plain


def test_a_look_change_re_runs_only_the_final_pass_and_what_follows(store, tmp_path, built):  # noqa: F811
    """The shots come from the cache; the end card and the final pass (their
    keys hold the ASS text) run again -- with no font change the end card's
    own key is the same."""
    story_id = trs.episode(store, tmp_path, built)
    first, _log, _fake = trs.render(store, story_id, tmp_path=tmp_path)
    shot_stages = [f"S:{shot['shot_id']}" for shot in trs._shots(store, story_id)]
    assert first["params"] == {"subtitles": "word_pop", "encoder": "libx264"}
    folder = trs.ep_dir(store, story_id)
    plain_ass = (folder / "subtitles.ass").read_text(encoding="utf-8")
    assert ",1,3,0,5," in plain_ass

    _set_look(store, story_id, {"size_pct": 150, "position_pct": 40,
                                "box": {"colour": "#202020", "opacity_pct": 60}})
    second, _log, fake2 = trs.render(store, story_id, tmp_path=tmp_path)

    assert second["cached"] == shot_stages
    assert second["ran"] == trs.TAIL_STAGES and len(fake2.calls) == len(trs.TAIL_STAGES)
    ass = (folder / "subtitles.ass").read_text(encoding="utf-8")
    assert ass != plain_ass and ",93," in ass and ",3,10,0,5," in ass and "\\pos(540,768)" in ass
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    assert [s["state"] for s in manifest["stages"] if s["kind"] == "shot"] == ["cached"] * len(shot_stages)


def test_an_unchanged_look_is_a_render_that_is_current_and_a_changed_one_is_not(store, tmp_path, built):  # noqa: F811
    step = trs._render_mod()
    story_id = trs.episode(store, tmp_path, built)
    trs.render(store, story_id, tmp_path=tmp_path)
    fonts_dir = tmp_path / "no_custom_fonts"
    assert step.current_render(trs._ec(store, story_id), None, custom_fonts_dir=fonts_dir) is True

    _set_look(store, story_id, {"size_pct": 90})
    assert step.current_render(trs._ec(store, story_id), None, custom_fonts_dir=fonts_dir) is False
    _set_look(store, story_id, None)
    assert step.current_render(trs._ec(store, story_id), None, custom_fonts_dir=fonts_dir) is True
