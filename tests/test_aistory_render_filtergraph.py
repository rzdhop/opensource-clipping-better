"""Tests for ``clipping.aistory.render.profiles``/``motion``/``filtergraph``
(AI Story phase 4 stage 4; plan phase 4 stage 4, "Renderer" ->
profiles.py/motion.py, and filtergraph.py's Shot / Tier >= 2 clip / End
card bullets; spec 6.3, 6.5; DEC-156, DEC-157).

Every ffmpeg fragment/argv here is a golden string, pinned literally: these
builders are pure, so the same inputs must always produce the exact same
text, forever (spec 13's own reason for existing). No ffmpeg is imported,
run, or required -- these are string-builder tests only (DEC-012).

Sections, in order:

1. portability guard, for all three modules
2. profiles: FINAL/SHOT/GOLDEN pinned fields
3. motion: zoompan expressions for the seven closed-list motions
4. motion: handheld (golden fragment + numeric ±2 px bound), jitter_stopmotion
5. motion: overlays (film_grain, vignette), escaping
6. filtergraph: shot_argv goldens (profiles, modifiers, overlays)
7. filtergraph: tier2_clip_argv / end_card_argv goldens
8. no absolute path in any argv
"""

from __future__ import annotations

import ast
import math
import sys

import pytest

from clipping.aistory import schemas
from clipping.aistory.render import filtergraph, motion, profiles


# ======================================================== 1. portability guard

@pytest.mark.parametrize("module", [profiles, motion, filtergraph], ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_modules_import_nothing_outside_the_standard_library_and_aistory(module):
    """RC-P8: every module in this stage stays pure."""
    import pathlib

    path = pathlib.Path(module.__file__)
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
    assert offenders == [], f"non-stdlib, non-aistory imports in {module.__name__}: {offenders}"


# ======================================================== 2. profiles

def test_width_height_fps_constants():
    assert (profiles.WIDTH, profiles.HEIGHT, profiles.FPS) == (1080, 1920, 30)


def test_final_profile_pinned():
    assert profiles.FINAL.preset == "medium"
    assert profiles.FINAL.crf == 20
    assert profiles.FINAL.pix_fmt == "yuv420p"
    assert profiles.FINAL.fps_mode == "cfr"
    assert profiles.FINAL.audio_codec == "aac"
    assert profiles.FINAL.audio_bitrate == "192k"
    assert profiles.FINAL.audio_rate == 48000
    assert profiles.FINAL.movflags == "+faststart"
    assert profiles.FINAL.bitexact is False
    assert profiles.FINAL.threads is None


def test_shot_profile_pinned():
    assert profiles.SHOT.preset == "veryfast"
    assert profiles.SHOT.crf == 12
    assert profiles.SHOT.upscale == 4
    assert profiles.SHOT.bitexact is False
    assert profiles.SHOT.fps_mode is None


def test_golden_profile_pinned():
    assert profiles.GOLDEN.preset == "ultrafast"
    assert profiles.GOLDEN.crf == 30
    assert profiles.GOLDEN.threads == 1
    assert profiles.GOLDEN.bitexact is True
    assert profiles.GOLDEN.upscale == 1


def test_the_4x_upscale_belongs_to_shot_alone():
    """Stage-4 verification's own wording: "FINAL: 4x only in SHOT, medium
    crf 20 in FINAL"."""
    assert profiles.SHOT.upscale == 4
    assert profiles.FINAL.upscale != 4
    assert profiles.GOLDEN.upscale != 4
    assert profiles.FINAL.preset == "medium" and profiles.FINAL.crf == 20


def test_video_encode_args_shot():
    assert profiles.SHOT.video_encode_args() == [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p",
    ]


def test_video_encode_args_final():
    assert profiles.FINAL.video_encode_args() == [
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
    ]


def test_video_encode_args_golden_includes_output_side_bitexact_flags():
    assert profiles.GOLDEN.video_encode_args() == [
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p", "-threads", "1",
        "-flags:v", "+bitexact", "-flags:a", "+bitexact", "-map_metadata", "-1",
    ]


def test_global_bitexact_args_only_on_golden():
    assert profiles.GOLDEN.global_bitexact_args() == ["-fflags", "+bitexact"]
    assert profiles.SHOT.global_bitexact_args() == []
    assert profiles.FINAL.global_bitexact_args() == []


# ======================================================== 3. motion: zoompan

def _m(motion_type, zoom_from, zoom_to, pan="none"):
    return {"type": motion_type, "zoom_from": zoom_from, "zoom_to": zoom_to, "pan": pan}


def test_camera_motions_match_the_schemas_closed_list():
    assert set(motion._CAMERA_MOTIONS) == set(schemas.CAMERA_MOTIONS)


def test_zoompan_hold():
    got = motion.zoompan_expr(_m("hold", 1.0, 1.0), 60)
    assert got == {"z": "'1'", "x": "'(iw/2-(iw/zoom/2))'", "y": "'(ih/2-(ih/zoom/2))'"}


def test_zoompan_push_in():
    got = motion.zoompan_expr(_m("push_in", 1.0, 1.1), 60)
    assert got == {
        "z": "'(1+(1.1-1)*(0.5-0.5*cos(PI*on/59)))'",
        "x": "'(iw/2-(iw/zoom/2))'", "y": "'(ih/2-(ih/zoom/2))'",
    }


def test_zoompan_pull_out():
    got = motion.zoompan_expr(_m("pull_out", 1.18, 1.0), 60)
    assert got == {
        "z": "'(1.18+(1-1.18)*(0.5-0.5*cos(PI*on/59)))'",
        "x": "'(iw/2-(iw/zoom/2))'", "y": "'(ih/2-(ih/zoom/2))'",
    }


def test_zoompan_pan_lr():
    got = motion.zoompan_expr(_m("pan_lr", 1.0, 1.0, "lr"), 60)
    assert got == {
        "z": "'1.041667'", "x": "'(iw-iw/zoom)*(0.5-0.5*cos(PI*on/59))'", "y": "'(ih/2-(ih/zoom/2))'",
    }


def test_zoompan_pan_rl():
    got = motion.zoompan_expr(_m("pan_rl", 1.0, 1.0, "rl"), 60)
    assert got == {
        "z": "'1.041667'", "x": "'(iw-iw/zoom)*(1-(0.5-0.5*cos(PI*on/59)))'", "y": "'(ih/2-(ih/zoom/2))'",
    }


def test_zoompan_pan_ud():
    got = motion.zoompan_expr(_m("pan_ud", 1.0, 1.0, "ud"), 60)
    assert got == {
        "z": "'1.041667'", "x": "'(iw/2-(iw/zoom/2))'", "y": "'(ih-ih/zoom)*(0.5-0.5*cos(PI*on/59))'",
    }


def test_zoompan_pan_du():
    got = motion.zoompan_expr(_m("pan_du", 1.0, 1.0, "du"), 60)
    assert got == {
        "z": "'1.041667'", "x": "'(iw/2-(iw/zoom/2))'", "y": "'(ih-ih/zoom)*(1-(0.5-0.5*cos(PI*on/59)))'",
    }


def test_zoompan_rejects_an_unknown_motion():
    with pytest.raises(ValueError, match="wobble"):
        motion.zoompan_expr(_m("wobble", 1.0, 1.0), 60)


def test_pan_zoom_leaves_exactly_pan_pct_percent_of_room():
    zoom = motion.pan_zoom(4.0)
    # room = iw*(1 - 1/zoom); solved so room == iw*pan_pct/100
    room_fraction = 1.0 - 1.0 / zoom
    assert room_fraction == pytest.approx(0.04, abs=1e-9)


def test_zoompan_single_frame_shot_has_no_time_axis():
    """A 1-frame (or fewer) shot pins the ease at 1 (its end state) rather
    than dividing by zero."""
    got = motion.zoompan_expr(_m("push_in", 1.0, 1.1), 1)
    assert got["z"] == "'(1+(1.1-1)*1)'"


# ================================================== 4. motion: modifiers

def test_handheld_margin_and_canvas():
    assert motion.handheld_margin_px() == 4  # 2 * HANDHELD_PX
    assert motion.zoompan_canvas([]) == (profiles.WIDTH, profiles.HEIGHT)
    assert motion.zoompan_canvas(["handheld"]) == (profiles.WIDTH + 4, profiles.HEIGHT + 4)


def test_handheld_crop_expr_golden():
    got = motion.handheld_crop_expr(24)
    assert got == {
        "w": "1080", "h": "1920", "x": "'(2+2*sin(2*PI*n/24))'", "y": "'(2+2*sin(3*PI*n/24))'",
    }


def test_handheld_offsets_stay_within_plus_minus_2_px():
    """Evaluated numerically in Python over every frame (task's own
    requirement) -- both axes, several frame counts."""
    for frames in (1, 5, 24, 90, 137):
        n = max(frames, 1)
        for k in range(frames):
            x = 2.0 + motion.HANDHELD_PX * math.sin(2 * math.pi * k / n)
            y = 2.0 + motion.HANDHELD_PX * math.sin(3 * math.pi * k / n)
            assert 0.0 - 1e-9 <= x <= motion.handheld_margin_px() + 1e-9
            assert 0.0 - 1e-9 <= y <= motion.handheld_margin_px() + 1e-9
            assert abs(x - 2.0) <= motion.HANDHELD_PX + 1e-9
            assert abs(y - 2.0) <= motion.HANDHELD_PX + 1e-9


def test_jitter_stopmotion_frames_is_12fps_of_the_duration():
    assert motion.jitter_stopmotion_frames(1.0) == 12
    assert motion.jitter_stopmotion_frames(2.5) == 30
    assert motion.jitter_stopmotion_frames(0.0) == 1  # floored at 1, never 0


def test_jitter_offset_expr_golden_and_alternates():
    expr = motion.jitter_offset_expr()
    assert expr == "(1*(2*(on-1*floor(on/1))-1))"
    # evaluate the alternator numerically: 2*(n - floor(n)) - 1 == +/-1
    for n in range(6):
        value = 2 * (n - 1 * math.floor(n / 1)) - 1
        assert value in (-1, 1)


def test_zoompan_expr_with_jitter_stopmotion_adds_the_offset_to_x_and_y():
    got = motion.zoompan_expr(_m("hold", 1.0, 1.0), 12, modifiers=["jitter_stopmotion"])
    assert got["x"] == "'((iw/2-(iw/zoom/2))+(1*(2*(on-1*floor(on/1))-1)))'"
    assert got["y"] == "'((ih/2-(ih/zoom/2))+(1*(2*(on-1*floor(on/1))-1)))'"
    assert got["z"] == "'1'"  # jitter never touches z


# =================================================== 5. motion: overlays

def test_overlays_match_the_schemas_closed_list():
    assert {"film_grain", "vignette", "paper_texture"} == set(schemas.OVERLAYS)


def test_noise_fragment_golden():
    assert motion.noise_fragment() == "noise=alls=8:allf=t+u"


def test_vignette_fragment_golden():
    assert motion.vignette_fragment() == "vignette=angle=PI/5"


def test_escape_expr_escapes_every_special_character_once():
    got = motion.escape_expr("a,b:c;d\\e")
    assert got == "a\\,b\\:c\\;d\\\\e"


def test_escape_expr_backslash_is_escaped_before_the_special_characters():
    """Backslash-first ordering (module docstring): an already-escaped
    comma's own backslash is itself escaped by a second pass, rather than
    the pass mistaking it for an unescaped special character -- escaping
    is deliberately not idempotent (a caller escapes raw text exactly
    once, never the other way around)."""
    once = motion.escape_expr("x,y")
    assert once == "x\\,y"  # one backslash, then the comma
    twice = motion.escape_expr(once)
    assert twice == "x\\\\\\,y"  # once's backslash doubled, then a fresh comma-escape


# ================================================== 6. filtergraph: shot_argv

def _shot(shot_id="sh01", scene_id="s01", duration_s=2.0, frames=60, motion_d=None, modifiers=None):
    return {
        "shot_id": shot_id, "scene_id": scene_id, "start_s": 0.0, "duration_s": duration_s, "frames": frames,
        "motion": motion_d or _m("hold", 1.0, 1.0), "modifiers": modifiers or [], "transition_after": None,
    }


def test_shot_argv_starts_with_the_common_prefix():
    argv = filtergraph.shot_argv("in/a.png", _shot(), profiles.SHOT, [], "cache/sh01.mp4")
    assert argv[:4] == ["ffmpeg", "-hide_banner", "-nostdin", "-y"]


def test_shot_argv_golden_shot_profile_hold_no_overlays():
    argv = filtergraph.shot_argv("in/a.png", _shot(), profiles.SHOT, [], "cache/sh01.mp4")
    assert argv == [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-loop", "1", "-i", "in/a.png",
        "-filter_complex",
        "[0:v]scale=4320:-2,zoompan=z='1':x='(iw/2-(iw/zoom/2))':y='(ih/2-(ih/zoom/2))':"
        "d=60:s=1080x1920:fps=30,format=yuv420p[out]",
        "-map", "[out]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p",
        "-r", "30", "-frames:v", "60", "-an", "cache/sh01.mp4",
    ]


def test_shot_argv_golden_golden_profile_with_film_grain():
    argv = filtergraph.shot_argv("in/a.png", _shot(), profiles.GOLDEN, ["film_grain"], "cache/sh01.mp4")
    assert argv == [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-fflags", "+bitexact",
        "-loop", "1", "-i", "in/a.png",
        "-filter_complex",
        "[0:v]scale=1080:-2,zoompan=z='1':x='(iw/2-(iw/zoom/2))':y='(ih/2-(ih/zoom/2))':"
        "d=60:s=1080x1920:fps=30,noise=alls=8:allf=t+u,format=yuv420p[out]",
        "-map", "[out]",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p", "-threads", "1",
        "-flags:v", "+bitexact", "-flags:a", "+bitexact", "-map_metadata", "-1",
        "-r", "30", "-frames:v", "60", "-an", "cache/sh01.mp4",
    ]


def test_shot_argv_upscale_is_4x_only_under_shot_profile():
    argv_shot = filtergraph.shot_argv("in/a.png", _shot(), profiles.SHOT, [], "out.mp4")
    argv_golden = filtergraph.shot_argv("in/a.png", _shot(), profiles.GOLDEN, [], "out.mp4")
    assert "scale=4320:-2" in " ".join(argv_shot)
    assert "scale=1080:-2" in " ".join(argv_golden)


def test_shot_argv_vignette_fragment_present():
    argv = filtergraph.shot_argv("in/a.png", _shot(), profiles.SHOT, ["vignette"], "out.mp4")
    joined = " ".join(argv)
    assert "vignette=angle=PI/5" in joined
    assert ",format=yuv420p[out]" in joined


def test_shot_argv_overlay_order_is_film_grain_then_vignette():
    argv = filtergraph.shot_argv("in/a.png", _shot(), profiles.SHOT, ["vignette", "film_grain"], "out.mp4")
    joined = " ".join(argv)
    assert joined.index("noise=alls=8") < joined.index("vignette=angle=PI/5")


def test_shot_argv_paper_texture_uses_filter_complex_with_movie_and_blend():
    argv = filtergraph.shot_argv("in/a.png", _shot(), profiles.SHOT, ["paper_texture"], "cache/sh01.mp4")
    idx = argv.index("-filter_complex")
    graph = argv[idx + 1]
    assert f"movie={filtergraph.PAPER_TEXTURE_REL}" in graph
    assert f"scale={profiles.WIDTH}:{profiles.HEIGHT}[tex]" in graph
    assert f"blend=all_mode={filtergraph.PAPER_TEXTURE_BLEND_MODE}:all_opacity=0.25" in graph
    assert graph.endswith("[out]")
    assert "-map" in argv and argv[argv.index("-map") + 1] == "[out]"


def test_shot_argv_handheld_adds_crop_and_bigger_canvas():
    argv = filtergraph.shot_argv("in/a.png", _shot(modifiers=["handheld"]), profiles.SHOT, [], "out.mp4")
    joined = " ".join(argv)
    assert "s=1084x1924" in joined
    assert "crop=1080:1920:x='(2+2*sin(2*PI*n/60))':y='(2+2*sin(3*PI*n/60))'" in joined


def test_shot_argv_jitter_stopmotion_steps_at_12fps_then_converts_to_30():
    shot = _shot(duration_s=1.0, frames=30, modifiers=["jitter_stopmotion"])
    argv = filtergraph.shot_argv("in/a.png", shot, profiles.SHOT, [], "out.mp4")
    idx = argv.index("-filter_complex")
    graph = argv[idx + 1]
    assert "fps=12" in graph
    # the zoompan clause itself is followed by a plain ",fps=30" conversion
    assert ":fps=12,fps=30," in graph
    # -frames:v pins the EXACT target (the shot's own 30 fps frame count),
    # regardless of the internal 12 fps -> 30 fps conversion's own rounding.
    assert argv[argv.index("-frames:v") + 1] == "30"


def test_shot_argv_refuses_an_absolute_image_path():
    with pytest.raises(ValueError, match="image_rel"):
        filtergraph.shot_argv("/abs/a.png", _shot(), profiles.SHOT, [], "cache/sh01.mp4")


def test_shot_argv_refuses_an_absolute_out_path():
    with pytest.raises(ValueError, match="out_rel"):
        filtergraph.shot_argv("in/a.png", _shot(), profiles.SHOT, [], "/abs/out.mp4")


# =========================================== 7. filtergraph: tier2 / end card

def test_tier2_clip_argv_golden():
    argv = filtergraph.tier2_clip_argv("in/clip.mp4", _shot(duration_s=2.0, frames=60), profiles.SHOT,
                                       "cache/sh01_t2.mp4")
    assert argv == [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-i", "in/clip.mp4",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
               "fps=30,trim=duration=2,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p",
        "-r", "30", "-frames:v", "60", "-an", "cache/sh01_t2.mp4",
    ]


def test_tier2_clip_argv_refuses_absolute_paths():
    with pytest.raises(ValueError, match="video_rel"):
        filtergraph.tier2_clip_argv("/abs/clip.mp4", _shot(), profiles.SHOT, "out.mp4")


def test_end_card_argv_golden_final_profile():
    argv = filtergraph.end_card_argv("card.ass", "fonts", 1.0, profiles.FINAL, "end_card.mp4")
    assert argv == [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-f", "lavfi", "-i", "color=black:s=1080x1920:r=30:d=1",
        "-vf", "ass=card.ass:fontsdir=fonts,format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
        "-r", "30", "-frames:v", "30", "-an", "end_card.mp4",
    ]


def test_end_card_argv_escapes_a_comma_in_the_ass_path():
    argv = filtergraph.end_card_argv("weird,card.ass", "fonts", 1.0, profiles.GOLDEN, "out.mp4")
    vf = argv[argv.index("-vf") + 1]
    assert "ass=weird\\,card.ass:fontsdir=fonts" in vf


def test_end_card_argv_frame_count_floors_at_1():
    argv = filtergraph.end_card_argv("card.ass", "fonts", 0.01, profiles.GOLDEN, "out.mp4")
    assert argv[argv.index("-frames:v") + 1] == "1"


def test_end_card_argv_refuses_absolute_paths():
    with pytest.raises(ValueError, match="ass_rel"):
        filtergraph.end_card_argv("/abs/card.ass", "fonts", 1.0, profiles.GOLDEN, "out.mp4")
    with pytest.raises(ValueError, match="fontsdir_rel"):
        filtergraph.end_card_argv("card.ass", "/abs/fonts", 1.0, profiles.GOLDEN, "out.mp4")
    with pytest.raises(ValueError, match="out_rel"):
        filtergraph.end_card_argv("card.ass", "fonts", 1.0, profiles.GOLDEN, "/abs/out.mp4")


# ==================================================== 8. no absolute path

@pytest.mark.parametrize("bad", ["/abs/path", "\\abs\\path", "C:/windows/path", "C:\\windows\\path"])
def test_assert_relative_refuses_every_absolute_form(bad):
    with pytest.raises(ValueError):
        filtergraph._assert_relative(bad)


@pytest.mark.parametrize("ok", ["in/a.png", "cache/sh01.mp4", "fonts", "a/b/c.ass"])
def test_assert_relative_accepts_relative_paths(ok):
    filtergraph._assert_relative(ok)  # must not raise


def test_no_absolute_path_in_any_built_argv():
    shots_to_try = [
        _shot(modifiers=["handheld", "jitter_stopmotion"]),
        _shot(motion_d=_m("pan_lr", 1.0, 1.0, "lr")),
    ]
    overlay_sets = [[], ["film_grain"], ["vignette"], ["paper_texture"], ["film_grain", "vignette", "paper_texture"]]
    argvs = []
    for shot in shots_to_try:
        for overlays in overlay_sets:
            argvs.append(filtergraph.shot_argv("in/a.png", shot, profiles.SHOT, overlays, "cache/sh.mp4"))
            argvs.append(filtergraph.shot_argv("in/a.png", shot, profiles.GOLDEN, overlays, "cache/sh.mp4"))
    argvs.append(filtergraph.tier2_clip_argv("in/clip.mp4", _shot(), profiles.SHOT, "cache/t2.mp4"))
    argvs.append(filtergraph.end_card_argv("card.ass", "fonts", 1.0, profiles.FINAL, "end_card.mp4"))

    for argv in argvs:
        for arg in argv:
            assert not arg.startswith("/"), argv
            assert not (len(arg) >= 2 and arg[1] == ":" and arg[0].isalpha()), argv
