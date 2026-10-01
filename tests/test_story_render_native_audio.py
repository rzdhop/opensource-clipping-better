"""Tier-3 native audio in the render (AI Story phase 6, stage 10; spec 6.5,
8.1; DEC-158, DEC-201).

The rule: a model's own sound is discarded at tier 2 -- dialogue comes from
our TTS, for voice consistency -- and kept only under the tier-3 opt-in: a
shot whose effective flags keep its native audio (``keep_native_audio``,
``assets.json``'s ``shots`` override) and whose clip is current. Such a
shot's clip sound is one more stem of DEC-158's absolute timeline: placed at
the sample its first video frame starts, trimmed to its frames, with 10 ms
fades at its edges; that shot's own lines are left out of what is heard,
while the ducking and the subtitles still follow every line's TTS timing.
The video stage keeps ``-an``. A clip with no sound track keeps the shot's
lines, said in the feed.

Two halves:

- **What is heard** -- the render plan of a tiny episode (the tier-2
  golden's documents and media, ``render/golden_tier2.py``, with the clip
  moved to the second shot and given a 1 kHz sine track; its lines are 330,
  440 and 550 Hz blips), its audio mix (the A stage, the one stage this
  changes) run with the ffmpeg on this machine. The mix (``mix.wav``) is
  decoded to PCM and measured with a Goertzel filter in pure Python: the
  clip's tone starts at its shot's first frame, the shot's own line tones are
  gone, the bed's ducking is the one tier 2 makes -- and at tier 2 the same
  clip's tone is nowhere.
- **What the render step decides** -- the render step's own episode with
  stage 7's fake ffmpeg (``tests/test_story_render_clips.py``'s way), one
  shot's clip given a real sound track: at tier 3 that shot takes it, a shot
  whose clip has none keeps its lines with a note; at tier 2 neither does.

The new behaviour is reached inside the tests, so on the parent commit each
test fails on its own. Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import array
import math
import os
import shutil
import subprocess
import sys

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_render_step as trs
import test_story_video_phase as tvp
from clipping.aistory import schemas
from clipping.aistory.render import golden_tier2 as gt2
from clipping.aistory.render import profiles, runner
from clipping.aistory.render import filtergraph
from clipping.aistory.render import plan as plan_mod
from clipping.aistory.render import timeline as rt
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_video_phase import timings_path  # noqa: F401 -- the measured clip timings under tmp_path

RATE = 48000
CLIP_HZ = 1000.0
LINE_HZ = {line_id: hz for line_id, _scene, _speaker, _text, hz, _s in gt2.LINES}
NATIVE_SHOT = "sh02"
NATIVE_LINES = ["l02", "l03"]
# Longer than the shot (about 2.8 s), so the stem's trim is measured too.
CLIP_S = 4.0
ONE_FRAME_S = 1.0 / profiles.FPS
PRESENT = 0.02     # a tone's amplitude in the mono mix: the blips read ~0.5, the clip's sine ~0.125
ABSENT = 0.002


def _require_ffmpeg():
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        pytest.fail(f"{' and '.join(missing)} not found on PATH: the native-audio render runs the real ffmpeg "
                    "(CI installs it: apt-get install ffmpeg).")


def make_clip_with_sound(out_path, seconds=CLIP_S) -> None:
    """A clip as a tier-3 model answers one: ``testsrc`` video and a
    :data:`CLIP_HZ` sine sound track (AAC), *seconds* long."""
    width, height = gt2.CLIP_SIZE
    profile = profiles.GOLDEN
    argv = (["ffmpeg", "-hide_banner", "-nostdin", "-y"] + profile.global_bitexact_args()
            + ["-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={gt2.CLIP_FPS}:duration={seconds:g}",
               "-f", "lavfi", "-i", f"sine=frequency={CLIP_HZ:g}:sample_rate={RATE}:duration={seconds:g}"]
            + profile.video_encode_args() + ["-c:a", "aac", "-b:a", "128k", os.fspath(out_path)])
    result = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0 and os.path.isfile(out_path), result.stderr[-800:]


# ================================================================ measuring

def _pcm(path) -> array.array:
    """*path*'s audio as mono 48 kHz float samples (ffmpeg to a pipe)."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", os.fspath(path), "-ac", "1",
                          "-ar", str(RATE), "-f", "f32le", "-"], capture_output=True, stdin=subprocess.DEVNULL,
                         check=True).stdout
    samples = array.array("f")
    samples.frombytes(out)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def _level(samples, hz, start_s, end_s) -> float:
    """The amplitude of an *hz* tone in ``[start_s, end_s)``: a Goertzel
    filter over a Hann window, scaled so a full-window sine of amplitude A
    reads A."""
    first, last = max(0, int(round(start_s * RATE))), min(len(samples), int(round(end_s * RATE)))
    n = last - first
    assert n > 16, (start_s, end_s)
    coeff = 2.0 * math.cos(2.0 * math.pi * hz / RATE)
    s1 = s2 = weights = 0.0
    for i in range(n):
        w = 0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1))
        weights += w
        s0 = samples[first + i] * w + coeff * s1 - s2
        s2, s1 = s1, s0
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return 2.0 * math.sqrt(max(power, 0.0)) / weights


def _onset(samples, hz, around_s, *, span_s=0.25, window_s=0.01, step_s=0.001) -> float:
    """Where the *hz* tone starts near *around_s*: the first 10 ms window
    (1 ms steps, from ``around_s - span_s``) holding half its steady
    amplitude (measured 0.1-0.4 s after *around_s*)."""
    steady = _level(samples, hz, around_s + 0.1, around_s + 0.4)
    assert steady > PRESENT, f"no {hz:g} Hz tone after {around_s:.3f} s (amplitude {steady:.5f})"
    t = around_s - span_s
    while t < around_s + span_s:
        if _level(samples, hz, t, t + window_s) >= steady / 2:
            return t + window_s / 2
        t += step_s
    raise AssertionError(f"the {hz:g} Hz tone does not start within {span_s} s of {around_s:.3f} s")


# =========================================================== what is heard

def _documents():
    """The tier-2 golden's documents with the clip on ``sh02`` (the second
    shot, so its start is not the episode's) and each shot naming its lines."""
    docs = gt2.build_documents()
    for shot in docs["storyboard"]["shots"]:
        shot["assets"]["video"] = f"assets/clips/shot_{shot['shot_id'][2:]}.mp4" if shot["shot_id"] == NATIVE_SHOT \
            else None
        shot["lines"] = [line["line_id"] for scene in docs["script"]["scenes"] if scene["scene_id"] == shot["scene_id"]
                         for line in scene["lines"]]
    return docs


def _mix(workdir, *, native):
    """The tiny episode's plan (GOLDEN), ``sh02`` cut from a clip with a sound
    track -- *native*: its sound kept (tier 3), as the render step hands it,
    or not (tier 2) -- its inputs staged under ``<workdir>/render`` as the
    runner stages them, and its A stage run there with the real ffmpeg:
    ``(render dir, plan)``."""
    workdir.mkdir(parents=True, exist_ok=True)
    docs = _documents()
    inputs = gt2.write_sources(workdir)
    clip = workdir / "sources" / "clips" / "shot_02.mp4"
    make_clip_with_sound(clip)
    inputs["videos"] = {NATIVE_SHOT: runner.file_record(clip, "fixture/clips/shot_02.mp4")}
    if native:
        inputs["native_audio"] = [NATIVE_SHOT]
    plan = plan_mod.build_render_plan(**docs, story=dict(gt2.STORY), ep=gt2.EP, inputs=inputs,
                                      ffmpeg={"version": "here", "machine": "here"}, profile="golden")
    render_dir = workdir / "render"
    for sub in plan_mod.WORK_DIRS:
        (render_dir / sub).mkdir(parents=True, exist_ok=True)
    for item in plan["inputs"]:
        shutil.copyfile(item["path"], render_dir / item["staged"])
    stage = next(stage for stage in plan["stages"] if stage["id"] == "A")
    result = subprocess.run(stage["argv"], cwd=render_dir, capture_output=True, text=True,
                            stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr[-1500:]
    return render_dir, plan


@pytest.fixture(scope="module")
def renders(tmp_path_factory):
    _require_ffmpeg()
    root = tmp_path_factory.mktemp("native_audio")
    docs = _documents()
    timeline = rt.build_timeline(docs["script"], docs["storyboard"], docs["template"], gt2.STORY["language"],
                                 style_lock=docs["style_lock"])
    return {"tier3": _mix(root / "tier3", native=True), "tier2": _mix(root / "tier2", native=False),
            "timeline": timeline}


def _spans(timeline):
    """``(shot_start_s, shot_end_s, {line_id: (tone_start_s, tone_end_s)})``:
    the native shot's span as its frames are cut (the final pass's first
    frame of it, and its own frame count) and each line's tone (a golden
    blip sounds from 0.15 s into its line, for at most 0.5 s)."""
    start_frame = filtergraph.sequence_plan(timeline)["start_frames"][NATIVE_SHOT]
    frames = next(shot["frames"] for shot in timeline["shots"] if shot["shot_id"] == NATIVE_SHOT)
    tones = {line["line_id"]: (line["start_s"] + 0.15, line["start_s"] + min(line["duration_s"], 0.65))
             for line in timeline["lines"]}
    return start_frame / profiles.FPS, (start_frame + frames) / profiles.FPS, tones


def test_at_tier_3_the_clips_sound_starts_at_its_shots_first_frame_in_place_of_the_shots_lines(renders):
    """Fail-first. The mix carries the clip's 1 kHz tone from ``sh02``'s
    first frame (within a frame) to its last, and none of it before or after;
    ``sh02``'s own lines (440 and 550 Hz) are not heard, ``sh01``'s (330 Hz)
    still is; the ducked bed is exactly tier 2's (the sidechain is still
    every TTS line). The plan stages the clip once more, as the A stage's
    ``clip_audio`` input (read inside its graph), and labels the shot
    ``video_native_audio``; the video stage keeps ``-an``."""
    render_dir, plan = renders["tier3"]
    samples = _pcm(render_dir / plan_mod.MIX_REL)
    start_s, end_s, tones = _spans(renders["timeline"])
    onset = _onset(samples, CLIP_HZ, start_s)
    print(f"clip tone onset {onset:.4f} s, shot's first frame {start_s:.4f} s")
    assert abs(onset - start_s) <= ONE_FRAME_S, (onset, start_s)
    assert _level(samples, CLIP_HZ, 0.05, start_s - 0.02) < ABSENT
    assert _level(samples, CLIP_HZ, end_s + 0.02, renders["timeline"]["total_s"] - 0.01) < ABSENT
    for line_id in NATIVE_LINES:
        heard = _level(samples, LINE_HZ[line_id], *tones[line_id])
        print(f"{line_id} at {LINE_HZ[line_id]:g} Hz: {heard:.5f}")
        assert heard < ABSENT, (line_id, heard)
        assert _level(samples, CLIP_HZ, *tones[line_id]) > PRESENT
    assert _level(samples, LINE_HZ["l01"], *tones["l01"]) > PRESENT

    tier2_dir, _tier2 = renders["tier2"]
    bed = plan_mod.STEMS_REL["bgm"]
    assert (render_dir / bed).read_bytes() == (tier2_dir / bed).read_bytes()

    assert plan["shot_modes"] == {"sh01": "motion", NATIVE_SHOT: "video_native_audio"}
    by_role = {(item["role"], item["id"]): item for item in plan["inputs"]}
    shot_clip, clip_audio = by_role[("shot", NATIVE_SHOT)], by_role[("clip_audio", NATIVE_SHOT)]
    assert (clip_audio["source"], clip_audio["staged"], clip_audio["sha256"]) == (
        shot_clip["source"], shot_clip["staged"], shot_clip["sha256"])
    stages = {stage["id"]: stage for stage in plan["stages"]}
    assert "-an" in stages[f"S:{NATIVE_SHOT}"]["argv"]
    mix_argv = stages["A"]["argv"]
    # read inside the graph, never an -i input (filtergraph._native_dialogue: ffmpeg 7.1.5)
    assert clip_audio["staged"] not in mix_argv
    assert f"amovie={clip_audio['staged']}," in mix_argv[mix_argv.index("-filter_complex") + 1]


def test_guard_at_tier_2_the_same_clips_sound_is_discarded_and_the_shots_lines_are_heard(renders):
    """DEC-201: the same clip, its sound not kept -- the render step's tier 2
    -- adds no stem: no 1 kHz anywhere in the mix, ``sh02``'s lines heard,
    the shot labelled plain ``video``, no ``clip_audio`` input."""
    render_dir, plan = renders["tier2"]
    assert plan["shot_modes"] == {"sh01": "motion", NATIVE_SHOT: "video"}
    assert [item for item in plan["inputs"] if item["role"] not in ("shot", "line", "bgm", "overlay")] == []

    samples = _pcm(render_dir / plan_mod.MIX_REL)
    _start_s, _end_s, tones = _spans(renders["timeline"])
    assert _level(samples, CLIP_HZ, 0.0, renders["timeline"]["total_s"]) < ABSENT
    for line_id in NATIVE_LINES:
        assert _level(samples, LINE_HZ[line_id], *tones[line_id]) > PRESENT, line_id


# ============================================== what the render step decides

def _spy_mix(monkeypatch):
    """Every ``filtergraph.audio_mix_argv`` call's kwargs, in order."""
    seen = []
    real = filtergraph.audio_mix_argv

    def spy(timeline, **kwargs):
        seen.append(kwargs)
        return real(timeline, **kwargs)

    monkeypatch.setattr(filtergraph, "audio_mix_argv", spy)
    return seen


def _native_episode(store, tmp_path, *, tier):
    """The render step's episode at *tier*, its clips made by stage 8's fake
    video adapter with two voiced planned shots keeping their native audio
    (``assets.json`` overrides): ``loud``'s clip then replaced by a real one
    with a sound track, ``mute``'s left as the fake answered it (no sound
    track). Its assets approved: ``(story_id, loud, mute)``."""
    settings = tce._settings(**tvp.PAID)
    story_id = tvp._keyframes(store, tmp_path, settings=settings)
    tce._tier(store, story_id, tier=tier)
    planned = [row["shot_id"] for row in tvp._plan(store, story_id, settings, tvp._adapters(tvp.FakeVideo()))["plan"]]
    shots = {shot["shot_id"]: shot for shot in tas._shots(store, story_id)}
    voiced = [shot_id for shot_id in planned if shots[shot_id]["lines"]]
    assert len(voiced) >= 2, planned
    loud, mute = voiced[:2]
    tce._patch(store, story_id, {"shot_id": loud, "keep_native_audio": True},
               {"shot_id": mute, "keep_native_audio": True})
    tas._run(store, story_id, adapters=tvp._adapters(tvp.FakeVideo()), settings=settings)
    shots = {shot["shot_id"]: shot for shot in tas._shots(store, story_id)}
    assert shots[loud]["assets"]["clip"]["state"] == shots[mute]["assets"]["clip"]["state"] == "current"
    make_clip_with_sound(trs.ep_dir(store, story_id) / shots[loud]["assets"]["video"], seconds=1.0)
    trs.approve_assets(store, story_id)
    return story_id, loud, mute


@pytest.mark.parametrize("tier", [3, 2])
def test_a_native_audio_shot_takes_its_clips_sound_only_at_tier_3_and_only_from_a_clip_that_has_one(
        store, tmp_path, monkeypatch, tier):
    """At tier 3 the shot whose clip has a sound track is cut from its clip
    with that sound in place of its lines (``video_native_audio``, the clip
    staged again as ``clip_audio``); the shot whose clip has none keeps its
    lines -- rendered as at tier 2 -- and the feed says so once, naming it.
    At tier 2 both are plain ``video`` and nothing is said (DEC-201)."""
    _require_ffmpeg()
    story_id, loud, mute = _native_episode(store, tmp_path, tier=tier)
    lines_of = {shot["shot_id"]: shot["lines"] for shot in tas._shots(store, story_id)}
    seen = _spy_mix(monkeypatch)

    summary, log, _fake = trs.render(store, story_id, tmp_path=tmp_path)

    assert summary["state"] == "completed" and len(seen) == 1
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    assert schemas.render_manifest_errors(manifest) == []
    clip_audio = [item for item in manifest["inputs"] if item["role"] == "clip_audio"]
    notes = [line for line in log if mute in line and "sound" in line]
    if tier == 3:
        assert (manifest["shot_modes"][loud], manifest["shot_modes"][mute]) == ("video_native_audio", "video")
        assert [item["id"] for item in clip_audio] == [loud]
        assert seen[0]["native_audio"] == {loud: {"input": clip_audio[0]["staged"], "lines": lines_of[loud]}}
        assert len(notes) == 1 and loud not in notes[0], log
    else:
        assert (manifest["shot_modes"][loud], manifest["shot_modes"][mute]) == ("video", "video")
        assert clip_audio == [] and not seen[0].get("native_audio") and notes == []
