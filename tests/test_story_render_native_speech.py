"""Native speech in the render (plan 22, stage 4), on the real ffmpeg.

A native-speech board (``timing_mode: native_speech``): each speaking shot
is cut from its clip and its clip's sound is heard in place of its line
(DEC-201's ``video_native_audio``, the clip from its first frame), its
subtitles at the times its take aligned; a silent shot's clip is heard as
ambience under the narrator's TTS voice-over (``video_ambience``), ducked by
it. Three shots of 2 s, every boundary a cut: ``sh01`` speaks ``l01`` (its
clip a 1 kHz tone, the take from 0.30 s), ``sh02`` is silent under the
narrator's ``l02`` (its clip a 700 Hz tone; the narrator's 440 Hz blip
0.35 s in), ``sh03`` speaks ``l03`` (a 1.3 kHz clip, the take from 0.50 s).
The lines' own files are 330 / 440 / 550 Hz blips: a speaking line's is
never heard, the narrator's is. The goldens (``test_aistory_render_golden*``)
prove every other board renders as it did.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import copy
import os
import re
import shutil
import subprocess

import pytest

import test_story_render_native_audio as tna
from clipping.aistory import timing
from clipping.aistory.render import fonts, golden, profiles, runner
from clipping.aistory.render import plan as plan_mod
from clipping.aistory.render import timeline as rt

SHA = "0" * 64
# (shot_id, scene_id, speaks, its clip's tone, its lines)
SHOTS = (("sh01", "s01", True, 1000.0, ["l01"]), ("sh02", "s02", False, 700.0, ["l02"]),
         ("sh03", "s02", True, 1300.0, ["l03"]))
# (line_id, scene_id, speaker, text, the blip of its own file, seconds)
LINES = (("l01", "s01", "char_a", "Look out now", 330.0, 0.7), ("l02", "s02", "narrator", "Not again", 440.0, 0.8),
         ("l03", "s02", "char_b", "Run now", 550.0, 0.6))
TAKES = {"sh01": 0.30, "sh03": 0.50}
ALIGNED = {"l01": [{"word": "Look", "start": 0.0, "end": 0.2}, {"word": "out", "start": 0.25, "end": 0.45},
                   {"word": "now", "start": 0.5, "end": 0.7}],
           "l03": [{"word": "Run", "start": 0.0, "end": 0.25}, {"word": "now", "start": 0.3, "end": 0.6}]}


def _documents():
    by_scene = {}
    for line_id, scene_id, speaker, text, _hz, seconds in LINES:
        line = golden._line(line_id, speaker, text, seconds)
        if speaker != "narrator":
            line["timing"].update(source="audio_duration_only", voice="native/clip")
        by_scene.setdefault(scene_id, []).append(line)
    script = {"scenes": [golden._scene("s01", "hook", by_scene["s01"]),
                         golden._scene("s02", "cliffhanger", by_scene["s02"])],
              "hook": None, "cliffhanger": {"scene_id": "s02", "reveal": "The door opens.", "cut_to_black": True}}
    shots = []
    for order, (shot_id, scene_id, speaks, _hz, lines) in enumerate(SHOTS, 1):
        clip = {"state": "current", "link": "gemini/veo-3.1-fast" if speaks else "gemini/veo-3.1-lite",
                "route": "paid", "clip_s": 2, "est_usd": 0.2, "prompt_hash": SHA, "image_sha256": SHA,
                "cache_key": None, "generated_at": "2026-10-04T10:00:00+00:00"}
        if speaks:
            line = next(item for item in LINES if item[0] == lines[0])
            clip["native_speech"] = {"state": "ok", "matched": 1.0, "heard": line[3], "start_s": TAKES[shot_id],
                                     "end_s": TAKES[shot_id] + line[5], "aligned_by": "groq/whisper-large-v3-turbo",
                                     "clip_sha256": SHA, "clip_real_s": 2.0, "line_id": lines[0],
                                     "checked_at": "2026-10-04T10:00:00+00:00"}
        shots.append({"shot_id": shot_id, "scene_id": scene_id, "order": order, "duration_s": 2.0,
                      "motion": {"type": "hold", "zoom_from": 1.0, "zoom_to": 1.0, "pan": "none"}, "modifiers": [],
                      "keep_still": False, "lines": list(lines), "speaks": speaks, "clip_s": 2,
                      "assets": {"image": f"assets/shots/shot_{order:02d}.png",
                                 "video": f"assets/clips/shot_{order:02d}.mp4", "seed": None, "provider": None,
                                 "approved": True, "clip": clip}})
    board = {"shots": shots, "transitions": [], "timing_mode": "native_speech"}
    assets = {"lines": {"l01": {"words_source": "alignment", "aligned_by": "groq"},
                        "l02": {"words_source": "even_split"},
                        "l03": {"words_source": "alignment", "aligned_by": "groq"}},
              "sfx": [],
              "bgm": {"mood": "golden_bed", "dominant_emotion": "tension", "weights_s": {"tension": 1.0},
                      "file": "fixture/bed.wav", "sha256": None, "licence": "self-made"}}
    return {"script": script, "storyboard": board, "assets": assets, "style_lock": copy.deepcopy(golden.STYLE_LOCK),
            "template": copy.deepcopy(golden.TEMPLATE)}


def _clip(path, hz, seconds=2.0):
    width, height = 120, 208
    profile = profiles.GOLDEN
    argv = (["ffmpeg", "-hide_banner", "-nostdin", "-y"] + profile.global_bitexact_args()
            + ["-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate=24:duration={seconds:g}",
               "-f", "lavfi", "-i", f"sine=frequency={hz:g}:sample_rate=48000:duration={seconds:g}"]
            + profile.video_encode_args() + ["-c:a", "aac", "-b:a", "128k", os.fspath(path)])
    result = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr[-800:]


def _inputs(workdir):
    src = workdir / "sources"
    for sub in ("shots", "clips", "voice", "bgm", "custom_fonts"):
        (src / sub).mkdir(parents=True, exist_ok=True)
    shots, videos = {}, {}
    for index, (shot_id, _scene, _speaks, hz, _lines) in enumerate(SHOTS):
        image = src / "shots" / f"shot_{index + 1:02d}.png"
        image.write_bytes(golden.png_bytes(*golden.IMAGE_SIZE, golden._shot_pixel(index, 0)))
        shots[shot_id] = runner.file_record(image, f"fixture/shots/{image.name}")
        clip = src / "clips" / f"shot_{index + 1:02d}.mp4"
        _clip(clip, hz)
        videos[shot_id] = runner.file_record(clip, f"fixture/clips/{clip.name}")
    lines = {}
    for line_id, _scene, _speaker, _text, hz, seconds in LINES:
        path = src / "voice" / f"{line_id}.wav"
        golden.wav_bytes_to(path, golden.LINE_RATE, golden._blip(golden.LINE_RATE, seconds, hz))
        lines[line_id] = runner.file_record(path, f"fixture/voice/{path.name}")
    bed = src / "bgm" / "bed.wav"
    golden.wav_bytes_to(bed, golden.BED_RATE, golden._bed(golden.BED_RATE, golden.BED_S))
    font = fonts.resolve_font(golden.STYLE_LOCK["typography"]["font_family"], custom_fonts_dir=src / "custom_fonts")
    return {"shots": shots, "videos": videos, "keep_still": {shot_id: False for shot_id, *_rest in SHOTS},
            "lines": lines, "sfx": {}, "bgm": runner.file_record(bed, "fixture/bgm/bed.wav"),
            "overlay": runner.paper_texture_record(), "font": font, "word_timings": copy.deepcopy(ALIGNED),
            "native_audio": ["sh01", "sh03"], "ambience": ["sh02"]}


@pytest.fixture(scope="module")
def render(tmp_path_factory):
    tna._require_ffmpeg()
    workdir = tmp_path_factory.mktemp("native_speech")
    docs = _documents()
    plan = plan_mod.build_render_plan(**docs, story=dict(golden.STORY), ep=golden.EP, inputs=_inputs(workdir),
                                      ffmpeg={"version": "here", "machine": "here"}, profile="golden")
    render_dir = workdir / "render"
    for sub in plan_mod.WORK_DIRS:
        (render_dir / sub).mkdir(parents=True, exist_ok=True)
    for item in plan["inputs"]:
        shutil.copyfile(item["path"], render_dir / item["staged"])
    for item in plan["files"]:
        (render_dir / item["path"]).write_text(item["text"], encoding="utf-8")
    stage = next(stage for stage in plan["stages"] if stage["id"] == "A")
    result = subprocess.run(stage["argv"], cwd=render_dir, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr[-1500:]
    return {"plan": plan, "samples": tna._pcm(render_dir / plan_mod.MIX_REL), "docs": docs, "dir": render_dir}


def test_the_native_board_places_each_line_where_its_clip_speaks_it(render):
    """Fail-first. Every shot lasts its clip (2 s), every boundary a cut: a
    speaking line starts where its shot starts plus its take's start, the
    narrator's 0.35 s into its silent shot; the timeline's total is the
    shots' sum plus the end card, as the script's timing says."""
    docs = render["docs"]
    timeline = rt.build_timeline(docs["script"], docs["storyboard"], docs["template"], "en",
                                 style_lock=docs["style_lock"])
    starts = {line["line_id"]: line["start_s"] for line in timeline["lines"]}
    assert starts == {"l01": 0.3, "l02": 2.35, "l03": 4.5}
    assert [shot["start_s"] for shot in timeline["shots"]] == [0.0, 2.0, 4.0]
    total, _scenes = timing.episode_pass(docs["script"], docs["template"], "en", storyboard=docs["storyboard"])
    assert timeline["total_s"] == total["total_s"] == pytest.approx(6.6)


def test_a_speaking_shots_clip_is_heard_in_place_of_its_line_and_a_silent_one_under_the_narrator(render):
    """The mix: each speaking clip's tone from its shot's first frame, its
    line's own file never heard; the narrator's line heard over the silent
    shot, whose clip's own tone is heard under it. Each shot is labelled by
    its mode, and the plan keeps both tier-3 modes in one mix."""
    samples, plan = render["samples"], render["plan"]
    assert plan["shot_modes"] == {"sh01": "video_native_audio", "sh02": "video_ambience",
                                  "sh03": "video_native_audio"}
    level = tna._level
    assert level(samples, 1000.0, 0.4, 1.6) > tna.PRESENT
    assert level(samples, 330.0, 0.45, 0.95) < tna.ABSENT      # l01's own file
    assert level(samples, 440.0, 2.5, 2.95) > tna.PRESENT      # the narrator's voice-over
    assert level(samples, 700.0, 3.3, 3.9) > tna.ABSENT        # the silent clip's ambience, under it
    assert level(samples, 1000.0, 2.3, 3.7) < tna.ABSENT
    assert level(samples, 1300.0, 4.4, 5.6) > tna.PRESENT
    assert level(samples, 550.0, 4.65, 5.05) < tna.ABSENT      # l03's own file
    mix = next(stage for stage in plan["stages"] if stage["id"] == "A")["argv"]
    graph = mix[mix.index("-filter_complex") + 1]
    assert "[amb_bed][amb_sc]sidechaincompress" in graph and graph.count("amovie=") == 3


def test_the_subtitles_burn_each_line_at_its_aligned_times(render):
    text = next(item["text"] for item in render["plan"]["files"] if item["path"] == plan_mod.SUBTITLES_REL)
    events = re.findall(r"^Dialogue: \d+,(\d+:\d\d:\d\d\.\d\d),", text, re.M)
    assert "0:00:00.30" in events and "0:00:04.50" in events and "0:00:04.80" in events
    assert render["plan"]["approx_line_ids"] == ["l02"]


def test_guard_both_tier3_modes_in_one_mix_are_refused_unless_the_board_is_native():
    """A render keeps one tier-3 mode (stage E) -- except a native-speech
    board's, which the plan asks with ``speech``."""
    from clipping.aistory.render import filtergraph

    docs = _documents()
    timeline = rt.build_timeline(docs["script"], docs["storyboard"], docs["template"], "en",
                                 style_lock=docs["style_lock"])
    kwargs = {"line_inputs": {line_id: f"in/{line_id}.wav" for line_id, *_rest in LINES}, "sfx_inputs": {},
              "bgm_input": None, "ending": "cut_to_black", "out_rel": "mix.wav", "stems_rel": dict(plan_mod.STEMS_REL),
              "native_audio": {"sh01": {"input": "in/c1.mp4", "lines": ["l01"]}}, "ambience": {"sh02": "in/c2.mp4"}}
    with pytest.raises(filtergraph.GraphError, match="one tier-3 audio mode"):
        filtergraph.audio_mix_argv(timeline, **kwargs)
    assert "-filter_complex" in filtergraph.audio_mix_argv(timeline, speech=True, **kwargs)
