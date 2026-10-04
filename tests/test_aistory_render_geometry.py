"""The render geometry (plan 23 stage B6): every builder of the AI-Story
renderer takes a frame -- 9:16 (the default), 16:9 or 1:1 -- and the 9:16
output stays byte-for-byte what it was before the keyword existed.

**The byte-identity record.** ``tests/fixtures/aistory_render_geometry/
portrait_before_b6.json`` holds the outputs of :func:`_cases` captured from
``main`` at 42b6267, before the geometry keyword existed. It is never
re-recorded: a portrait argv that moves changes the shot cache keys
(``plan._cached_stage`` hashes the argv) and the golden digest (DEC-156,
RC-M2), so a difference here is a bug in the change, not a new pin.

Sections:

1. the geometries (profiles)
2. portrait byte identity: the kwarg omitted, and ``geometry=PORTRAIT``
3. the subtitle layout table
4. 16:9 and 1:1 builder outputs carry their own sizes
5. ``build_render_plan(aspect=)``: expected size, the recorded param, refusals

No ffmpeg is run here (string builders and the plan only).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipping.aistory import schemas
from clipping.aistory.render import filtergraph, golden, motion, profiles
from clipping.aistory.render import plan as plan_mod
from clipping.aistory.render import subtitles as sub
from clipping.aistory.render import timeline as rt

FIXTURE = Path(__file__).parent / "fixtures" / "aistory_render_geometry" / "portrait_before_b6.json"
FFMPEG = {"version": "6.1.1-3ubuntu5", "machine": "aarch64"}
TYPOGRAPHY = {"font_family": "Montserrat Black", "subtitle_mode": "word_pop", "highlight_colour": "#FFD400",
              "ai_label": True}
PALETTE = {"primary": ["#F2C14E", "#E4572E", "#3A7D44"], "accents": ["#FFFFFF", "#1E1E24"]}

MOTIONS = {
    "push_in": {"type": "push_in", "zoom_from": 1.0, "zoom_to": 1.1, "pan": "none"},
    "hold": {"type": "hold", "zoom_from": 1.0, "zoom_to": 1.0, "pan": "none"},
    "pull_out": {"type": "pull_out", "zoom_from": 1.12, "zoom_to": 1.0, "pan": "none"},
    "pan_lr": {"type": "pan_lr", "zoom_from": 1.0, "zoom_to": 1.0, "pan": "lr"},
    "pan_du": {"type": "pan_du", "zoom_from": 1.0, "zoom_to": 1.0, "pan": "du"},
}
MODIFIER_SETS = ((), ("handheld",), ("jitter_stopmotion",), ("handheld", "jitter_stopmotion"))
OVERLAY_SETS = ((), ("film_grain", "vignette"), ("paper_texture",), ("film_grain", "vignette", "paper_texture"))
PROFILES = {"shot": "SHOT", "golden": "GOLDEN", "final": "FINAL"}
STILL_SIZES = (None, (1080, 1920), (108, 192), (768, 1344), (1024, 1024), (1024, 1536), (1024, 576),
               (1920, 1080), (1023, 1023), (100, 3000))
CTA_TEXTS = ("Comment \"PART 2\" for the next one", "Commente « PARTIE 12 » pour la suite", "x" * 80, "")


def _shot(motion_name="hold", modifiers=(), duration_s=2.0, frames=60):
    return {"duration_s": duration_s, "frames": frames, "motion": dict(MOTIONS[motion_name]),
            "modifiers": list(modifiers)}


def _golden_subtitle_inputs():
    docs = golden.build_documents()
    script = docs["script"]
    script["hook"] = {"on_screen_text": "He knew all along"}
    timeline = rt.build_timeline(script, docs["storyboard"], docs["template"], golden.STORY["language"],
                                 style_lock=docs["style_lock"])
    return script, timeline


def _cases(gkw: dict, akw: dict, workdir) -> dict:
    """Every builder's output for a fixed set of inputs. *gkw* is passed to
    each geometry-aware builder (``{}`` or ``{"geometry": ...}``), *akw* to
    ``build_render_plan`` (``{}`` or ``{"aspect": ...}``)."""
    out = {}
    fg = filtergraph

    # filtergraph: the cover rule and the still crop
    out["cover_fill"] = fg._cover_fill(**gkw)
    for size in STILL_SIZES:
        out[f"still_crop/{size}"] = fg.still_crop(size, **gkw)

    # filtergraph: shot_argv
    for name in MOTIONS:
        for modifiers in MODIFIER_SETS:
            out[f"shot/{name}/{'+'.join(modifiers) or '-'}"] = fg.shot_argv(
                "in/a.png", _shot(name, modifiers), profiles.SHOT, [], "cache/x.mp4", **gkw)
    for overlays in OVERLAY_SETS:
        for label, attr in PROFILES.items():
            out[f"shot/overlays/{'+'.join(overlays) or '-'}/{label}"] = fg.shot_argv(
                "in/a.png", _shot("push_in", ("handheld",)), getattr(profiles, attr), list(overlays),
                "cache/x.mp4", **gkw)
    for size in STILL_SIZES:
        for label in ("shot", "golden"):
            out[f"shot/size/{size}/{label}"] = fg.shot_argv(
                "in/a.png", _shot("hold"), getattr(profiles, PROFILES[label]), ["paper_texture"], "cache/x.mp4",
                image_size=size, **gkw)
    for pan_pct in (4.0, 6.0, 2.5):
        out[f"shot/pan_pct/{pan_pct}"] = fg.shot_argv(
            "in/a.png", _shot("pan_lr", (), duration_s=1.5, frames=45), profiles.SHOT, [], "cache/x.mp4",
            pan_pct=pan_pct, **gkw)
    out["shot/one_frame"] = fg.shot_argv("in/a.png", _shot("push_in", ("handheld",), 0.033, 1), profiles.GOLDEN,
                                         [], "cache/x.mp4", **gkw)

    # filtergraph: tier2_clip_argv
    for label, attr in PROFILES.items():
        for duration_s, frames, clip_s in ((2.0, 60, None), (2.0, 60, 1.0), (2.0, 60, 3.0), (1.5, 45, None),
                                           (7.3, 219, 5)):
            out[f"tier2/{label}/{duration_s}/{clip_s}"] = fg.tier2_clip_argv(
                "in/c.mp4", _shot(duration_s=duration_s, frames=frames), getattr(profiles, attr), "cache/y.mp4",
                clip_s=clip_s, **gkw)

    # filtergraph: end card, cover
    for label, attr in PROFILES.items():
        for duration_s in (1.0, 0.5, 1.25):
            out[f"end_card/{label}/{duration_s}"] = fg.end_card_argv(
                "end_card.ass", "fonts", duration_s, getattr(profiles, attr), "cache/e.mp4", **gkw)
    out["cover"] = fg.cover_argv("in/hook.png", "cover.ass", "fonts", "cover.part.jpg", **gkw)

    # motion
    for modifiers in MODIFIER_SETS:
        out[f"zoompan_canvas/{'+'.join(modifiers) or '-'}"] = motion.zoompan_canvas(modifiers, **gkw)
    for frames in (1, 60, 219):
        out[f"handheld_crop/{frames}"] = motion.handheld_crop_expr(frames, **gkw)

    # subtitles
    script, timeline = _golden_subtitle_inputs()
    lines = sub.merge_timeline_lines(timeline, script)
    out["word_pop"] = sub.word_pop_dialogue(lines, word_timings=golden.WORD_TIMINGS, typography=TYPOGRAPHY, **gkw)
    out["two_line"] = sub.two_line_dialogue(lines, word_timings=golden.WORD_TIMINGS, palette=PALETTE,
                                            typography=TYPOGRAPHY, **gkw)
    out["hook"] = sub.hook_overlay_events("He knew all along", 1.9, typography=TYPOGRAPHY, **gkw)
    out["hook/empty"] = sub.hook_overlay_events("", 1.9, typography=TYPOGRAPHY, **gkw)
    for text in CTA_TEXTS:
        out[f"cta_size/{text}"] = sub.end_card_cta_font_size(text, **gkw)
    for language in ("en", "fr"):
        for cta in (False, True):
            out[f"end_card_ass/{language}/{cta}"] = sub.end_card_ass(language, 4, "Golden Fixture", TYPOGRAPHY, 1.0,
                                                                     cta=cta, **gkw)
    out["cover_ass"] = sub.cover_ass("He knew all along", TYPOGRAPHY, **gkw)
    for mode in sub.SUBTITLE_MODES:
        for hook_style in ("text_overlay", "insert_prop"):
            document, meta = sub.build_subtitles_ass(
                timeline=timeline, script=script, subtitle_mode=mode, language="en", hook_style=hook_style,
                ai_label_enabled=True, palette=PALETTE, typography=TYPOGRAPHY, word_timings=golden.WORD_TIMINGS,
                **gkw)
            out[f"subtitles/{mode}/{hook_style}"] = [document, meta]

    # the plan of the golden fixture (the argv the cache keys hash, the files)
    inputs = golden.write_sources(workdir)
    for profile in ("golden", "final"):
        docs = golden.build_documents()
        plan = plan_mod.build_render_plan(**docs, story=dict(golden.STORY), ep=golden.EP, inputs=inputs,
                                          ffmpeg=dict(FFMPEG), profile=profile, **akw)
        out[f"plan/{profile}"] = {
            "params": plan["params"], "expected": plan["expected"], "files": plan["files"],
            "stages": [[s["id"], s["argv"], s["output"], s["cache_key"]] for s in plan["stages"]],
        }
    return json.loads(json.dumps(out))


# =================================================================== 1. geometries

def test_the_three_geometries():
    assert profiles.PORTRAIT == profiles.Geometry("9:16", 1080, 1920)
    assert profiles.LANDSCAPE == profiles.Geometry("16:9", 1920, 1080)
    assert profiles.SQUARE == profiles.Geometry("1:1", 1080, 1080)
    assert profiles.GEOMETRIES == {"9:16": profiles.PORTRAIT, "16:9": profiles.LANDSCAPE, "1:1": profiles.SQUARE}
    assert all(name == geometry.name for name, geometry in profiles.GEOMETRIES.items())
    # the short side is 1080 in every frame: text sizes stay in pixels
    assert {min(g.width, g.height) for g in profiles.GEOMETRIES.values()} == {1080}


def test_width_and_height_stay_the_portrait_values():
    assert (profiles.WIDTH, profiles.HEIGHT) == (profiles.PORTRAIT.width, profiles.PORTRAIT.height) == (1080, 1920)


def test_a_geometry_is_frozen():
    with pytest.raises(AttributeError):
        profiles.PORTRAIT.width = 1920  # type: ignore[misc]


def test_the_render_params_schema_knows_every_non_portrait_aspect():
    assert set(schemas.RENDER_ASPECTS) == set(profiles.GEOMETRIES) - {"9:16"}


# =================================================================== 2. portrait byte identity

@pytest.fixture(scope="module")
def recorded():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _diff(got, want):
    keys = sorted(set(got) | set(want))
    return [key for key in keys if got.get(key) != want.get(key)]


def test_portrait_outputs_with_the_keyword_omitted_are_the_ones_recorded_before_b6(recorded, tmp_path):
    got = _cases({}, {}, tmp_path)
    assert _diff(got, recorded) == []


def test_portrait_outputs_with_geometry_portrait_are_the_ones_recorded_before_b6(recorded, tmp_path):
    got = _cases({"geometry": profiles.PORTRAIT}, {"aspect": "9:16"}, tmp_path)
    assert _diff(got, recorded) == []


def test_a_portrait_plan_records_no_aspect(tmp_path):
    docs = golden.build_documents()
    plan = plan_mod.build_render_plan(**docs, story=dict(golden.STORY), ep=golden.EP,
                                      inputs=golden.write_sources(tmp_path), ffmpeg=dict(FFMPEG), profile="golden",
                                      aspect="9:16")
    assert plan["params"] == {"subtitles": "word_pop", "encoder": "libx264"}


# =================================================================== 3. the subtitle layout

def test_the_portrait_layout_is_exactly_the_module_constants():
    layout = sub._layout(profiles.PORTRAIT)
    assert layout == sub._layout()
    assert (layout.width, layout.height, layout.center_x) == (1080, 1920, 540)
    assert layout.word_pop_y == sub.WORD_POP_Y == 1488
    assert layout.two_line_margin_v == sub.TWO_LINE_MARGIN_V == 346
    assert layout.hook_y == sub.HOOK_Y == 320
    assert layout.end_card_label_y == sub.END_CARD_LABEL_Y == 864
    assert layout.end_card_title_y == sub.END_CARD_TITLE_Y == 1056
    assert layout.end_card_cta_y == sub.END_CARD_CTA_Y == 960
    assert layout.cover_y == sub.COVER_Y == 320


@pytest.mark.parametrize("geometry, expected", [
    (profiles.LANDSCAPE, (1920, 1080, 960, 837, 194, 180, 486, 594, 540, 180)),
    (profiles.SQUARE, (1080, 1080, 540, 837, 194, 180, 486, 594, 540, 180)),
])
def test_the_other_layouts_keep_the_same_fractions_of_their_own_frame(geometry, expected):
    layout = sub._layout(geometry)
    assert (layout.width, layout.height, layout.center_x, layout.word_pop_y, layout.two_line_margin_v,
            layout.hook_y, layout.end_card_label_y, layout.end_card_title_y, layout.end_card_cta_y,
            layout.cover_y) == expected


# =================================================================== 4. 16:9 and 1:1 builders

def _filter(argv, flag="-filter_complex"):
    return argv[argv.index(flag) + 1]


@pytest.mark.parametrize("geometry", [profiles.LANDSCAPE, profiles.SQUARE], ids=lambda g: g.name)
def test_a_shot_renders_its_frame(geometry):
    w, h = geometry.width, geometry.height
    plain = _filter(filtergraph.shot_argv("in/a.png", _shot("push_in"), profiles.SHOT, [], "cache/x.mp4",
                                          geometry=geometry))
    assert plain.startswith(f"[0:v]scale={w * 4}:-2,zoompan=")
    assert f":s={w}x{h}:fps=30" in plain
    golden_profile = _filter(filtergraph.shot_argv("in/a.png", _shot("push_in"), profiles.GOLDEN, ["paper_texture"],
                                                   "cache/x.mp4", geometry=geometry))
    assert golden_profile.startswith(f"[0:v]scale={w}:-2,zoompan=")
    assert f"movie={filtergraph.PAPER_TEXTURE_REL},scale={w}:{h}[tex]" in golden_profile
    handheld = _filter(filtergraph.shot_argv("in/a.png", _shot("hold", ("handheld", "jitter_stopmotion")),
                                             profiles.SHOT, [], "cache/x.mp4", geometry=geometry))
    assert f":s={w + 4}x{h + 4}:fps=12,fps=30,crop={w}:{h}:x=" in handheld
    # the zoompan expressions are relative to the input (iw/ih), whatever the frame
    portrait = _filter(filtergraph.shot_argv("in/a.png", _shot("pan_lr"), profiles.SHOT, [], "cache/x.mp4"))
    other = _filter(filtergraph.shot_argv("in/a.png", _shot("pan_lr"), profiles.SHOT, [], "cache/x.mp4",
                                          geometry=geometry))
    zp = lambda graph: graph.split("zoompan=", 1)[1].split(":d=", 1)[0]  # noqa: E731
    assert zp(portrait) == zp(other)


@pytest.mark.parametrize("geometry, size, crop", [
    (profiles.LANDSCAPE, (1920, 1080), None),
    (profiles.LANDSCAPE, (192, 108), None),
    (profiles.LANDSCAPE, (1344, 768), None),          # +1.6 %: inside the tolerance
    (profiles.LANDSCAPE, (1024, 1024), (1024, 576)),
    (profiles.LANDSCAPE, (1080, 1920), (1056, 594)),
    (profiles.SQUARE, (1024, 1024), None),
    (profiles.SQUARE, (1010, 1024), None),            # -1.4 %
    (profiles.SQUARE, (1024, 576), (576, 576)),
    (profiles.SQUARE, (108, 192), (108, 108)),
    (profiles.SQUARE, (1023, 1023), None),
    (profiles.SQUARE, (1023, 1500), (1022, 1022)),
])
def test_a_still_is_cropped_to_its_frame_in_its_own_pixels(geometry, size, crop):
    assert filtergraph.still_crop(size, geometry=geometry) == crop
    if crop is not None:
        assert crop[0] * geometry.height == crop[1] * geometry.width and crop[0] % 2 == crop[1] % 2 == 0
        graph = _filter(filtergraph.shot_argv("in/a.png", _shot(), profiles.SHOT, [], "cache/x.mp4",
                                              image_size=size, geometry=geometry))
        assert graph.startswith(f"[0:v]crop={crop[0]}:{crop[1]},scale={geometry.width * 4}:-2,")


@pytest.mark.parametrize("geometry", [profiles.LANDSCAPE, profiles.SQUARE], ids=lambda g: g.name)
def test_a_clip_an_end_card_and_the_cover_are_framed_to_it(geometry):
    w, h = geometry.width, geometry.height
    fill = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
    assert filtergraph._cover_fill(geometry=geometry) == fill
    tier2 = filtergraph.tier2_clip_argv("in/c.mp4", _shot(), profiles.SHOT, "cache/y.mp4", geometry=geometry)
    assert _filter(tier2, "-vf").startswith(f"{fill},fps=30,")
    card = filtergraph.end_card_argv("end_card.ass", "fonts", 1.0, profiles.SHOT, "cache/e.mp4", geometry=geometry)
    assert f"color=black:s={w}x{h}:r=30:d=1" in card
    cover = filtergraph.cover_argv("in/hook.png", "cover.ass", "fonts", "cover.part.jpg", geometry=geometry)
    assert _filter(cover, "-vf") == f"{fill},ass=cover.ass:fontsdir=fonts"


@pytest.mark.parametrize("geometry", [profiles.LANDSCAPE, profiles.SQUARE], ids=lambda g: g.name)
def test_motion_canvas_and_handheld_window_follow_the_frame(geometry):
    w, h = geometry.width, geometry.height
    assert motion.zoompan_canvas((), geometry=geometry) == (w, h)
    assert motion.zoompan_canvas(("handheld",), geometry=geometry) == (w + 4, h + 4)
    crop = motion.handheld_crop_expr(60, geometry=geometry)
    assert (crop["w"], crop["h"]) == (str(w), str(h))
    assert (crop["x"], crop["y"]) == (motion.handheld_crop_expr(60)["x"], motion.handheld_crop_expr(60)["y"])


@pytest.mark.parametrize("geometry", [profiles.LANDSCAPE, profiles.SQUARE], ids=lambda g: g.name)
def test_every_text_layer_is_placed_in_the_frame(geometry):
    layout = sub._layout(geometry)
    cx = layout.center_x
    script, timeline = _golden_subtitle_inputs()
    document, _meta = sub.build_subtitles_ass(
        timeline=timeline, script=script, subtitle_mode="word_pop", language="en", hook_style="text_overlay",
        ai_label_enabled=True, palette=PALETTE, typography=TYPOGRAPHY, word_timings=golden.WORD_TIMINGS,
        geometry=geometry)
    assert f"PlayResX: {geometry.width}\nPlayResY: {geometry.height}\n" in document
    assert f"\\pos({cx},{layout.word_pop_y})" in document and f"\\pos({cx},{layout.hook_y})" in document
    assert "\\pos(540,1488)" not in document

    two_line, _meta = sub.build_subtitles_ass(
        timeline=timeline, script=script, subtitle_mode="two_line", language="en", hook_style="insert_prop",
        ai_label_enabled=False, palette=PALETTE, typography=TYPOGRAPHY, geometry=geometry)
    assert f",2,0,0,{layout.two_line_margin_v},1" in two_line

    card = sub.end_card_ass("en", 2, "Golden Fixture", TYPOGRAPHY, 1.0, cta=True, geometry=geometry)
    assert f"PlayResX: {geometry.width}\nPlayResY: {geometry.height}\n" in card
    for y in (layout.end_card_label_y, layout.end_card_title_y, layout.end_card_cta_y):
        assert f"\\pos({cx},{y})" in card
    cover = sub.cover_ass("He knew all along", TYPOGRAPHY, geometry=geometry)
    assert f"\\pos({cx},{layout.cover_y})" in cover and f"PlayResY: {geometry.height}\n" in cover


def test_the_cta_fits_the_width_of_its_own_frame():
    text = "Commente « PARTIE 12 » pour la suite"
    portrait = sub.end_card_cta_font_size(text)
    landscape = sub.end_card_cta_font_size(text, geometry=profiles.LANDSCAPE)
    square = sub.end_card_cta_font_size(text, geometry=profiles.SQUARE)
    assert square == portrait < landscape <= sub.END_CARD_CTA_MAX_FONT_SIZE
    long_text = "x" * 80
    usable = 1920 - 2 * sub.END_CARD_CTA_SIDE_MARGIN - 2 * sub.END_CARD_OUTLINE_PX
    assert sub.end_card_cta_font_size(long_text, geometry=profiles.LANDSCAPE) == int(usable // (80 * 0.62))


# =================================================================== 5. build_render_plan(aspect=)

@pytest.mark.parametrize("aspect, size", [("16:9", (1920, 1080)), ("1:1", (1080, 1080))])
def test_a_plan_at_another_aspect_expects_its_size_and_records_its_aspect(aspect, size, tmp_path):
    docs = golden.build_documents()
    plan = plan_mod.build_render_plan(**docs, story=dict(golden.STORY), ep=golden.EP,
                                      inputs=golden.write_sources(tmp_path), ffmpeg=dict(FFMPEG), profile="golden",
                                      aspect=aspect)
    assert (plan["expected"]["width"], plan["expected"]["height"]) == size
    assert plan["params"] == {"subtitles": "word_pop", "encoder": "libx264", "aspect": aspect}
    w, h = size
    stages = {stage["id"]: stage for stage in plan["stages"]}
    for shot_id in ("S:sh01", "S:sh02", "S:sh03"):
        assert f":s={w}x{h}:fps=30" in _filter(stages[shot_id]["argv"])
    assert f"color=black:s={w}x{h}:r=30:d=1" in stages["E"]["argv"]
    files = {item["path"]: item["text"] for item in plan["files"]}
    for text in files.values():
        assert f"PlayResX: {w}\nPlayResY: {h}\n" in text
    # another frame is another argv, so another cache entry
    portrait = plan_mod.build_render_plan(**golden.build_documents(), story=dict(golden.STORY), ep=golden.EP,
                                          inputs=golden.write_sources(tmp_path), ffmpeg=dict(FFMPEG),
                                          profile="golden")
    portrait_keys = {stage["cache_key"] for stage in portrait["stages"] if stage["cache_key"]}
    assert not portrait_keys & {stage["cache_key"] for stage in plan["stages"] if stage["cache_key"]}


def test_an_unknown_aspect_is_refused(tmp_path):
    docs = golden.build_documents()
    with pytest.raises(plan_mod.PlanError, match="aspect"):
        plan_mod.build_render_plan(**docs, story=dict(golden.STORY), ep=golden.EP,
                                   inputs=golden.write_sources(tmp_path), ffmpeg=dict(FFMPEG), profile="golden",
                                   aspect="4:3")


def test_the_manifest_schema_takes_the_aspect_param_only_for_another_frame():
    base = {"subtitles": "word_pop", "encoder": "libx264"}
    params = schemas._RENDER_PARAMS_SCHEMA
    assert schemas.validate(base, params) == []
    assert schemas.validate(dict(base, aspect="16:9"), params) == []
    assert schemas.validate(dict(base, aspect="1:1"), params) == []
    assert schemas.validate(dict(base, aspect="9:16"), params) != []
    assert schemas.validate(dict(base, aspect="4:3"), params) != []
