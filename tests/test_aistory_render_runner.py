"""Tests for the AI-Story render plan, manifest and runner (AI Story phase 4
stage 7; plan phase 4 stage 7, "Renderer" -> runner.py/manifest.py/plan.py and
the loudness.py bullet; DEC-156, DEC-157, A-066; RC-A7).

No ffmpeg runs here: every process is a fake handed to the runner's ``popen``
seam, every clock a fake clock. The real render is
``tests/test_aistory_render_golden.py``. The fixture documents and media are
the golden fixture's own (``render/golden.py``), written into ``tmp_path``.

Sections, in order:

1. loudness.py: ``target=`` on the three functions, the clips' default
   commands byte-identical to before (RC-A7)
2. the plan: stage order, relative argv, staged inputs, the AI-Story
   loudnorm target, cache keys, refusals as ``PlanError``
3. the manifest: validated on every write, cache keys, relative paths
4. the runner: manifest-before-run, failure keeps partials and the stderr
   tail, cancel within one poll, per-stage timeout, cache hit starts no
   process, pruning, publishing the output
5. pre-flight and ``render()``'s failed results

Stdlib + pytest only (DEC-012): this file runs in the CI environment.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from clipping import cancel as cancel_mod
from clipping import loudness
from clipping.aistory import schemas
from clipping.aistory.render import filtergraph, golden, profiles, runner
from clipping.aistory.render import manifest as manifest_mod
from clipping.aistory.render import plan as plan_mod

FFMPEG = {"version": "6.1.1-test", "machine": "testarch"}

MEASURED_STDERR = """\
[Parsed_loudnorm_0 @ 0x1]
{
\t"input_i" : "-21.85",
\t"input_tp" : "-4.55",
\t"input_lra" : "3.00",
\t"input_thresh" : "-31.85",
\t"output_i" : "-14.05",
\t"output_tp" : "-1.70",
\t"output_lra" : "2.00",
\t"output_thresh" : "-24.05",
\t"normalization_type" : "linear",
\t"target_offset" : "0.05"
}
"""

FINAL_STDERR = MEASURED_STDERR.replace('"-21.85"', '"-14.10"').replace('"-4.55"', '"-1.20"')

PROBE = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920, "r_frame_rate": "30/1"},
                     {"codec_type": "audio"}],
         "format": {"duration": "4.760000"}}

FILTERS_OUTPUT = """Filters:
  T.. = Timeline support
  .S. = Slice threading
  ..C = Command support
  A = Audio input/output
  V = Video input/output
  N = Dynamic number and/or type of input/output
  | = Source or sink filter
 ... zoompan           V->V       Apply Zoom & Pan effect.
 .S. xfade             VV->V      Cross fade one video with another video.
 ..C sidechaincompress AA->A      Sidechain compressor.
 ... loudnorm          A->A       EBU R128 loudness normalization
 ... ass               V->V       Render ASS subtitles onto input video using the libass library.
"""


# ================================================================ fixtures

def _plan_args(tmp_path, *, variant_shot=None):
    docs = golden.build_documents()
    inputs = golden.write_sources(tmp_path, variant_shot=variant_shot)
    return {**docs, "story": dict(golden.STORY), "ep": golden.EP, "inputs": inputs}


def _plan(tmp_path, *, profile="golden", **changes):
    args = _plan_args(tmp_path)
    args.update(changes)
    return plan_mod.build_render_plan(**args, ffmpeg=dict(FFMPEG), profile=profile)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _outputs_of(argv):
    """The files an ffmpeg argv writes: every media path not given to -i."""
    found = []
    for i, token in enumerate(argv):
        if i and argv[i - 1] != "-i" and re.match(r"^[\w./-]+\.(mp4|mkv|wav|framemd5)$", token):
            found.append(token)
    return found


class FakeFFmpeg:
    """A ``popen`` stand-in that does each stage's work at once: writes the
    files ffmpeg would write, ffprobe's JSON on stdout and loudnorm's
    measurement on stderr. *behave(argv)* may return ``"hang"`` (never
    exits), ``"stubborn"`` (never exits, ignores terminate) or an exit code
    with an optional stderr text and partial file."""

    def __init__(self, clock=None, behave=None, on_start=None, on_wait=None):
        self.clock = clock or Clock()
        self.behave = behave or (lambda argv: None)
        self.on_start = on_start
        self.on_wait = on_wait or (lambda clock: None)
        self.calls = []
        self.procs = []

    def __call__(self, argv, *, cwd, stdin, stdout, stderr):
        assert stdin is subprocess.DEVNULL
        self.calls.append(list(argv))
        if self.on_start is not None:
            self.on_start(argv, cwd)
        proc = FakeProc(self, argv, cwd, stdout, stderr)
        self.procs.append(proc)
        return proc


class FakeProc:
    def __init__(self, owner, argv, cwd, stdout, stderr):
        self.owner = owner
        self.argv = argv
        self.returncode = None
        self.terminated_at = None
        self.killed_at = None
        self.waits = 0
        mode = owner.behave(argv)
        self.mode = mode if mode in ("hang", "stubborn") else None
        if self.mode is not None:
            return
        if isinstance(mode, tuple):
            code, text, partial = mode
            stderr.write(text.encode("utf-8"))
            if partial:
                with open(os.path.join(cwd, partial), "wb") as handle:
                    handle.write(b"half a file")
            self.returncode = code
            return
        if argv[0] == "ffprobe":
            stdout.write(json.dumps(PROBE).encode("utf-8"))
        elif "-f" in argv and argv[argv.index("-f") + 1] == "null":
            final = argv[argv.index("-i") + 1] == plan_mod.FINAL_REL
            stderr.write((FINAL_STDERR if final else MEASURED_STDERR).encode("utf-8"))
        else:
            for out in _outputs_of(argv):
                with open(os.path.join(cwd, out), "wb") as handle:
                    handle.write(("fake:" + out).encode("utf-8"))
        self.returncode = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        self.waits += 1
        self.owner.clock.t += timeout
        self.owner.on_wait(self.owner.clock)
        raise subprocess.TimeoutExpired(self.argv, timeout)

    def terminate(self):
        self.terminated_at = self.owner.clock()
        if self.mode == "hang":
            self.returncode = -15

    def kill(self):
        self.killed_at = self.owner.clock()
        self.returncode = -9


def _run(tmp_path, plan, fake, **kwargs):
    return runner.run_render(plan, render_dir=tmp_path / "render", manifest_path=tmp_path / "render_manifest.json",
                             final_path=tmp_path / "episode_final.mp4", popen=fake, clock=fake.clock,
                             now=lambda: "2026-09-28T12:00:00+00:00", **kwargs)


def _stage_ids(plan):
    return [stage["id"] for stage in plan["stages"]]


# ============================================================ 1. loudness.py

def test_the_clips_loudness_target_is_unchanged():
    assert loudness.TARGET == "I=-14:TP=-1.5:LRA=11"
    assert profiles.LOUDNORM_TARGET == "I=-14:TP=-2.5:LRA=11"


def test_the_default_commands_are_byte_identical_to_before():
    """The strings the clips ran before ``target=`` existed (RC-A7)."""
    assert loudness.measure_cmd("clip.mp4") == [
        "ffmpeg", "-hide_banner", "-nostats", "-i", "clip.mp4", "-vn",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-",
    ]
    measured = loudness.parse_measurement(MEASURED_STDERR)
    assert loudness.apply_cmd("in.mp4", "out.mp4", measured, 48000) == [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "in.mp4",
        "-map", "0", "-c:v", "copy",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=-21.85:measured_TP=-4.55:measured_LRA=3.00"
               ":measured_thresh=-31.85:offset=0.05:linear=true:print_format=summary",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "out.mp4",
    ]


def test_a_target_is_passed_through_both_passes():
    measured = loudness.parse_measurement(MEASURED_STDERR)
    measure = loudness.measure_cmd("mix.wav", target=profiles.LOUDNORM_TARGET)
    assert measure[measure.index("-af") + 1] == "loudnorm=I=-14:TP=-2.5:LRA=11:print_format=json"
    apply = loudness.apply_cmd("pre.mkv", "final.mp4", measured, 48000, target=profiles.LOUDNORM_TARGET)
    assert apply[apply.index("-af") + 1].startswith("loudnorm=I=-14:TP=-2.5:LRA=11:measured_I=-21.85")
    assert apply[-3:] == ["-movflags", "+faststart", "final.mp4"]
    assert loudness.TARGET == "I=-14:TP=-1.5:LRA=11"


def test_normalize_file_passes_its_target_to_both_commands(tmp_path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"original")
    calls, lines = [], []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, stdout="48000\n", stderr="")
        if "-f" in cmd and cmd[cmd.index("-f") + 1] == "null":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=MEASURED_STDERR)
        (tmp_path / os.path.basename(cmd[-1])).write_bytes(b"levelled")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    assert loudness.normalize_file(str(clip), run=run, on_log=lines.append, target="I=-16:TP=-1:LRA=11")
    filters = [cmd[cmd.index("-af") + 1] for cmd in calls if "-af" in cmd]
    assert len(filters) == 2 and all(f.startswith("loudnorm=I=-16:TP=-1:LRA=11:") for f in filters)
    assert lines[-1].endswith("-21.85 LUFS -> -16 LUFS")


# ================================================================== 2. plan

def test_the_plan_runs_the_stages_in_spec_order(tmp_path):
    plan = _plan(tmp_path)
    assert _stage_ids(plan) == ["S:sh01", "S:sh02", "S:sh03", "E", "A", "L1", "F", "L2", "P", "P:loudness", "M"]
    kinds = [stage["kind"] for stage in plan["stages"]]
    assert kinds == ["shot"] * 3 + ["end_card", "audio_mix", "loudness_measure", "final", "loudness_apply",
                                     "probe", "probe", "framemd5"]
    order = {kind: i for i, kind in enumerate(schemas.RENDER_STAGE_KINDS)}
    assert [order[k] for k in kinds] == sorted(order[k] for k in kinds)
    assert plan["params"] == {"subtitles": "word_pop", "encoder": "libx264"}
    assert plan["ffmpeg"] == FFMPEG
    assert plan["expected"] == {"duration_s": 4.75, "width": 1080, "height": 1920, "fps": "30/1"}


def test_every_argv_is_relative_and_every_input_staged_by_content(tmp_path):
    plan = _plan(tmp_path)
    for stage in plan["stages"]:
        for token in stage["argv"] or []:
            assert not token.startswith("/") and str(tmp_path) not in token, (stage["id"], token)
    for item in plan["inputs"]:
        if item["role"] == "overlay":
            assert item["staged"] == filtergraph.PAPER_TEXTURE_REL
        else:
            assert re.match(r"^in/[0-9a-f]{64}\.(png|wav)$", item["staged"]), item
            assert item["staged"] == f"in/{item['sha256']}{os.path.splitext(item['source'])[1]}"
    roles = [(item["role"], item["id"]) for item in plan["inputs"]]
    assert roles == [("overlay", "paper_texture"), ("shot", "sh01"), ("shot", "sh02"), ("shot", "sh03"),
                     ("line", "l01"), ("line", "l02"), ("line", "l03"), ("sfx", "dramatic_sting"), ("bgm", None)]


def test_the_ai_story_loudness_target_is_tp_minus_two_and_a_half(tmp_path):
    plan = _plan(tmp_path)
    stages = {stage["id"]: stage for stage in plan["stages"]}
    assert stages["L1"]["argv"] == loudness.measure_cmd("mix.wav", target="I=-14:TP=-2.5:LRA=11")
    assert stages["P:loudness"]["argv"] == loudness.measure_cmd("episode_final.mp4", target="I=-14:TP=-2.5:LRA=11")
    level = stages["L2"]
    assert level["argv"] is None
    argv = plan_mod.loudness_apply_argv(level, loudness.parse_measurement(MEASURED_STDERR))
    assert argv[argv.index("-af") + 1].startswith("loudnorm=I=-14:TP=-2.5:LRA=11:measured_I=-21.85")
    assert argv[argv.index("-c:v") + 1] == "copy" and argv[argv.index("-b:a") + 1] == "192k"
    assert argv[argv.index("-movflags") + 1] == "+faststart"
    assert argv[argv.index("-i") + 1] == "episode_pre.mkv" and argv[-1] == "episode_final.mp4"
    assert loudness.TARGET == "I=-14:TP=-1.5:LRA=11"


def test_the_final_aac_encode_is_peak_safe(tmp_path):
    """The episode's true-peak chain (DEC-157 as amended by the Tier-2 finding
    that TP -1 did not survive the AAC encode): loudnorm's ceiling at -2.5
    dBTP, 1.5 dB under the delivery limit the P:loudness warning checks, and
    the AAC encoder without perceptual noise substitution (its synthesized
    noise turned a -2.4 dBTP burst in a TTS line into a +4 dBTP over)."""
    assert profiles.LOUDNORM_TARGET == "I=-14:TP=-2.5:LRA=11"
    assert profiles.AAC_ENCODER_ARGS == ("-aac_pns", "0")
    assert profiles.TRUE_PEAK_MAX_DBTP == -1.0
    assert profiles.LOUDNESS_TARGET_I == -14.0 and profiles.LOUDNESS_TOLERANCE_LU == 1.0
    ceiling = float(dict(part.split("=") for part in profiles.LOUDNORM_TARGET.split(":"))["TP"])
    assert profiles.TRUE_PEAK_MAX_DBTP - ceiling >= 1.5

    level = {stage["id"]: stage for stage in _plan(tmp_path)["stages"]}["L2"]
    assert plan_mod.loudness_apply_argv(level, loudness.parse_measurement(MEASURED_STDERR)) == [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "episode_pre.mkv",
        "-map", "0", "-c:v", "copy",
        "-af", "loudnorm=I=-14:TP=-2.5:LRA=11:measured_I=-21.85:measured_TP=-4.55:measured_LRA=3.00"
               ":measured_thresh=-31.85:offset=0.05:linear=true:print_format=summary",
        "-c:a", "aac", "-b:a", "192k", "-aac_pns", "0", "-ar", "48000",
        "-movflags", "+faststart", "episode_final.mp4",
    ]
    # the clips' own encode keeps its default AAC settings (RC-A7)
    assert "-aac_pns" not in loudness.apply_cmd("in.mp4", "out.mp4", loudness.parse_measurement(MEASURED_STDERR),
                                                48000)
    # the warning still fires on a real over of the delivery limit, not on the chain's margin
    within = {"duration_s": 4.76, "loudness": {"i": -14.2, "tp": -1.2, "lra": 4.9}}
    assert runner.output_warnings({"length_window_s": [3, 9]}, within) == []
    over = {"duration_s": 4.76, "loudness": {"i": -14.07, "tp": 0.57, "lra": 5.6}}
    assert runner.output_warnings({"length_window_s": [3, 9]}, over) == ["True peak 0.6 dBTP is above -1 dBTP."]


def test_the_mix_is_float_until_the_loudnorm_pass(tmp_path):
    stages = {stage["id"]: stage for stage in _plan(tmp_path)["stages"]}
    mix = stages["A"]["argv"]
    at = mix.index("mix.wav")
    assert mix[at - 12:at - 10] == ["-c:a", "pcm_f32le"]
    final = stages["F"]["argv"]
    assert final[final.index("-c:a") + 1] == "pcm_f32le"


def test_shot_and_end_card_write_a_part_file_under_their_key(tmp_path):
    plan = _plan(tmp_path)
    for stage in plan["stages"]:
        if stage["kind"] in schemas.RENDER_CACHED_KINDS:
            key = stage["cache_key"]
            assert re.match(r"^[0-9a-f]{64}$", key)
            assert stage["output"] == f"cache/{key}.mp4"
            assert stage["write"] == f"cache/{key}.part.mp4" == stage["argv"][-1]
        else:
            assert stage["cache_key"] is None and stage["write"] == stage["output"]


def test_one_changed_image_changes_only_its_shots_key(tmp_path):
    first = _plan(tmp_path / "a")
    second = plan_mod.build_render_plan(**_plan_args(tmp_path / "b", variant_shot="sh02"), ffmpeg=dict(FFMPEG),
                                        profile="golden")
    keys = lambda plan: {s["id"]: s["cache_key"] for s in plan["stages"] if s["cache_key"]}  # noqa: E731
    a, b = keys(first), keys(second)
    assert [sid for sid in a if a[sid] != b[sid]] == ["S:sh02"]


def test_the_key_follows_the_ffmpeg_version_and_the_profile_but_not_the_path():
    argv = ["ffmpeg", "-i", "in/aaa.png", "-vf", "movie=in/paper_texture.png", "cache/__output__.mp4"]
    base = dict(output_token="cache/__output__.mp4", input_shas={"in/aaa.png": "a" * 64, "in/paper_texture.png":
                                                                   "b" * 64}, profile="final", ffmpeg_version="6.1.1")
    key = plan_mod.cache_key(argv, **base)
    renamed = ["ffmpeg", "-i", "in/zzz.png", "-vf", "movie=in/paper_texture.png", "cache/__output__.mp4"]
    assert plan_mod.cache_key(renamed, **dict(base, input_shas={"in/zzz.png": "a" * 64,
                                                                 "in/paper_texture.png": "b" * 64})) == key
    assert plan_mod.cache_key(argv, **dict(base, ffmpeg_version="7.1.5")) != key
    assert plan_mod.cache_key(argv, **dict(base, profile="golden")) != key
    texture_changed = dict(base, input_shas={"in/aaa.png": "a" * 64, "in/paper_texture.png": "c" * 64})
    assert plan_mod.cache_key(argv, **texture_changed) != key


def test_the_end_card_key_follows_its_text_and_font(tmp_path):
    first = {s["id"]: s for s in _plan(tmp_path)["stages"]}["E"]["cache_key"]
    story = dict(golden.STORY, title="Another Title")
    second = {s["id"]: s for s in _plan(tmp_path, story=story)["stages"]}["E"]["cache_key"]
    assert first != second


def test_final_mode_uses_the_shot_and_final_profiles(tmp_path):
    args = _plan_args(tmp_path)
    plan = plan_mod.build_render_plan(**args, ffmpeg=dict(FFMPEG), profile="final")
    stages = {stage["id"]: stage for stage in plan["stages"]}
    shot = stages["S:sh01"]["argv"]
    assert shot[shot.index("-preset") + 1] == "veryfast" and "scale=4320:-2" in " ".join(shot)
    final = stages["F"]["argv"]
    assert final[final.index("-preset") + 1] == "medium" and "-threads" not in final
    golden_plan = _plan(tmp_path)
    assert {s["id"]: s["cache_key"] for s in golden_plan["stages"]}["S:sh01"] != stages["S:sh01"]["cache_key"]


def test_a_board_that_does_not_cover_the_script_is_a_plan_error(tmp_path):
    args = _plan_args(tmp_path)
    board = args["storyboard"]
    board["shots"][0]["scene_id"], board["shots"][2]["scene_id"] = "s02", "s01"
    with pytest.raises(plan_mod.PlanError, match="does not cover the script"):
        plan_mod.build_render_plan(**args, ffmpeg=dict(FFMPEG), profile="golden")


def test_a_timeline_error_is_a_plan_error(tmp_path):
    args = _plan_args(tmp_path)
    args["storyboard"]["shots"][2]["duration_s"] += 0.5
    with pytest.raises(plan_mod.PlanError, match=r"^TimelineError: timeline total 5\.250s disagrees"):
        plan_mod.build_render_plan(**args, ffmpeg=dict(FFMPEG), profile="golden")


def test_a_transition_that_is_not_whole_frames_is_a_plan_error(tmp_path):
    args = _plan_args(tmp_path)
    args["template"]["transitions_s"]["dissolve"] = 0.41
    args["storyboard"]["transitions"][0]["duration_s"] = 0.41
    with pytest.raises(plan_mod.PlanError, match="GraphError: .*whole number of frames"):
        plan_mod.build_render_plan(**args, ffmpeg=dict(FFMPEG), profile="golden")


@pytest.mark.parametrize("change,message", [
    (lambda a: a["inputs"]["lines"].pop("l02"), "line 'l02' has no audio"),
    (lambda a: a["inputs"]["shots"].pop("sh03"), "shot 'sh03' has no image"),
    (lambda a: a["inputs"].pop("overlay"), "paper_texture but no overlay"),
    (lambda a: a["inputs"].pop("bgm"), "BGM track"),
    (lambda a: a["inputs"]["sfx"].clear(), "resolved in the assets but its file"),
    (lambda a: a["inputs"].pop("font"), "no font record"),
    (lambda a: a["inputs"]["shots"]["sh01"].update(source="/abs/shot.png"), "must be a relative path"),
    (lambda a: a["inputs"]["shots"]["sh01"].update(sha256="nope"), "is not a sha256"),
    (lambda a: a.update(encoder="auto"), "not available in this renderer yet"),
    (lambda a: a.update(subtitles="karaoke"), "unknown subtitles mode"),
])
def test_bad_inputs_are_named_plan_errors(tmp_path, change, message):
    args = _plan_args(tmp_path)
    change(args)
    with pytest.raises(plan_mod.PlanError, match=re.escape(message)):
        plan_mod.build_render_plan(**args, ffmpeg=dict(FFMPEG), profile="golden")


def test_a_missing_sfx_cue_is_skipped_and_reported(tmp_path):
    args = _plan_args(tmp_path)
    cue = args["assets"]["sfx"][0]
    cue.update(state="missing", file=None)
    plan = plan_mod.build_render_plan(**args, ffmpeg=dict(FFMPEG), profile="golden")
    assert plan["warnings"] == ["SFX cue 'dramatic_sting' (s01, at start) is missing; skipped."]
    assert "sfx" not in [item["role"] for item in plan["inputs"]]


def test_the_subtitles_parameter_overrides_the_style(tmp_path):
    plan = _plan(tmp_path, subtitles="none")
    assert plan["params"]["subtitles"] == "none"
    text = next(f["text"] for f in plan["files"] if f["path"] == "subtitles.ass")
    assert "WordPop" not in text and "AI-generated" in text
    assert _plan(tmp_path, subtitles="style")["params"]["subtitles"] == "word_pop"


def test_provider_words_are_used_only_where_the_assets_say_so(tmp_path):
    plan = _plan(tmp_path)
    assert plan["approx_line_ids"] == ["l02", "l03"]


# ============================================================== 3. manifest

def test_an_invalid_manifest_is_never_written(tmp_path):
    path = tmp_path / "render_manifest.json"
    doc = manifest_mod.new_manifest(_plan(tmp_path), now="2026-09-28T12:00:00+00:00")
    manifest_mod.write_manifest(path, doc)
    before = path.read_text()
    doc["stages"].append({"id": "S:sh01", "kind": "shot", "argv": ["ffmpeg"], "cache_key": None,
                          "state": "cached", "output": None, "output_sha256": None, "seconds": None,
                          "stderr_tail": None})
    with pytest.raises(manifest_mod.ManifestError, match="cached"):
        manifest_mod.write_manifest(path, doc)
    assert path.read_text() == before


def test_cache_keys_of_a_damaged_manifest_are_ignored():
    assert manifest_mod.cache_keys(None) == set()
    assert manifest_mod.cache_keys({"stages": "x"}) == set()
    assert manifest_mod.cache_keys({"stages": [{"cache_key": "../../etc"}, {"cache_key": "a" * 64}, 3]}) == {"a" * 64}


def test_manifest_paths_never_climb(tmp_path):
    assert manifest_mod.relative_to(tmp_path / "render" / "x.mp4", tmp_path) == "render/x.mp4"
    with pytest.raises(ValueError):
        manifest_mod.relative_to(tmp_path.parent / "x.mp4", tmp_path)


# ================================================================ 4. runner

def test_every_stage_is_on_disk_as_running_before_its_process_starts(tmp_path):
    manifest_path = tmp_path / "render_manifest.json"
    seen = []

    def on_start(argv, cwd):
        doc = json.loads(manifest_path.read_text())
        last = doc["stages"][-1]
        assert last["state"] == "running" and last["argv"] == list(argv)
        assert last["output_sha256"] is None and last["seconds"] is None
        assert schemas.render_manifest_errors(doc) == []
        assert cwd == str(tmp_path / "render")
        seen.append(last["id"])

    plan = _plan(tmp_path)
    fake = FakeFFmpeg(on_start=on_start)
    result = _run(tmp_path, plan, fake)
    assert result["state"] == "completed", result["error"]
    assert seen == _stage_ids(plan) == result["ran"]


def test_a_completed_render_records_everything(tmp_path):
    plan = _plan(tmp_path)
    fake = FakeFFmpeg()
    result = _run(tmp_path, plan, fake)
    doc = json.loads((tmp_path / "render_manifest.json").read_text())
    assert result["manifest"] == doc and schemas.render_manifest_errors(doc) == []
    assert all(stage["state"] == "done" and stage["output_sha256"] for stage in doc["stages"])
    level = next(stage for stage in doc["stages"] if stage["id"] == "L2")
    assert "measured_I=-21.85" in " ".join(level["argv"])  # built from L1's measurement
    assert doc["ffmpeg"] == FFMPEG and doc["profile"] == "golden"
    assert doc["font"]["family"] == "Montserrat Black"
    assert doc["output"] == result["output"]
    assert doc["output"]["path"] == "episode_final.mp4"
    assert doc["output"]["framemd5"]["file"] == "render/episode_final.framemd5"
    assert doc["output"]["loudness"] == {"i": -14.1, "tp": -1.2, "lra": 3.0}
    assert (doc["output"]["width"], doc["output"]["height"], doc["output"]["fps"]) == (1080, 1920, "30/1")
    assert (tmp_path / "episode_final.mp4").is_file() and not (tmp_path / "render" / "episode_final.mp4").exists()
    assert doc["timings"]["finished_at"] and doc["timings"]["total_s"] is not None
    # 4.76 s is inside the fixture template's 3-9 s, -14.1 LUFS and -1.2 dBTP are on target
    assert doc["warnings"] == []
    staged = sorted(os.listdir(tmp_path / "render" / "in"))
    assert "paper_texture.png" in staged and len(staged) == len(plan["inputs"]) == 9
    assert sorted(os.listdir(tmp_path / "render" / "fonts")) == ["Montserrat-Black.ttf"]
    assert (tmp_path / "render" / "subtitles.ass").read_text().startswith("[Script Info]")


def test_warnings_for_length_and_loudness():
    plan = {"length_window_s": [55, 75]}
    output = {"duration_s": 80.2, "loudness": {"i": -16.3, "tp": -0.4, "lra": 5.0}}
    assert runner.output_warnings(plan, output) == [
        "The episode is 80.2 s long, outside 55-75 s.",
        "Loudness -16.3 LUFS is outside -14 +/- 1 LU.",
        "True peak -0.4 dBTP is above -1 dBTP.",
    ]
    assert runner.output_warnings(plan, {"duration_s": 60.0, "loudness": {"i": -14.6, "tp": -1.0, "lra": 5}}) == []


def test_a_failure_keeps_the_partial_file_and_the_stderr_tail(tmp_path):
    plan = _plan(tmp_path)

    def behave(argv):
        if argv[-1] == plan_mod.PRE_REL:
            return (1, "x" * 5000 + "\nError while filtering: Invalid argument\n", plan_mod.PRE_REL)
        return None

    result = _run(tmp_path, plan, FakeFFmpeg(behave=behave))
    assert result["state"] == "failed" and result["failed_stage"] == "F"
    assert result["error"] == "stage F failed: exit 1"
    doc = json.loads((tmp_path / "render_manifest.json").read_text())
    assert schemas.render_manifest_errors(doc) == []
    failed = doc["stages"][-1]
    assert failed["id"] == "F" and failed["state"] == "failed"
    assert failed["stderr_tail"].startswith("exit 1\n") and "Invalid argument" in failed["stderr_tail"]
    assert len(failed["stderr_tail"]) <= schemas.STDERR_TAIL_MAX
    assert failed["output"] == "episode_pre.mkv" and failed["output_sha256"] is None
    assert (tmp_path / "render" / "episode_pre.mkv").read_bytes() == b"half a file"
    assert [stage["id"] for stage in doc["stages"]][-2:] == ["L1", "F"] and doc["output"] is None
    assert doc["timings"]["finished_at"] is not None


def test_a_failed_shot_leaves_no_file_under_its_key(tmp_path):
    plan = _plan(tmp_path)
    shot = plan["stages"][1]

    def behave(argv):
        return (1, "boom", shot["write"]) if argv[-1] == shot["write"] else None

    result = _run(tmp_path, plan, FakeFFmpeg(behave=behave))
    assert result["failed_stage"] == "S:sh02"
    assert (tmp_path / "render" / shot["write"]).is_file()
    assert not (tmp_path / "render" / shot["output"]).exists()
    again = _run(tmp_path, plan, FakeFFmpeg())
    assert again["state"] == "completed" and "S:sh02" in again["ran"]


def test_cancel_terminates_within_one_poll(tmp_path):
    plan = _plan(tmp_path)
    token = cancel_mod.CancelToken()
    cancel_at = []

    def on_wait(clock):
        if clock.t >= 2.0 and not token.cancelled:
            token.cancel()  # the user presses Cancel while F runs
            cancel_at.append(clock.t)

    fake = FakeFFmpeg(behave=lambda argv: "hang" if argv[-1] == plan_mod.PRE_REL else None, on_wait=on_wait)
    result = _run(tmp_path, plan, fake, cancel=token)
    proc = fake.procs[-1]
    assert proc.argv[-1] == plan_mod.PRE_REL
    assert result["state"] == "cancelled" and result["failed_stage"] == "F"
    assert proc.terminated_at is not None and proc.terminated_at - cancel_at[0] <= runner.POLL_S
    assert proc.killed_at is None
    doc = json.loads((tmp_path / "render_manifest.json").read_text())
    assert doc["stages"][-1]["state"] == "cancelled" and schemas.render_manifest_errors(doc) == []
    assert result["ran"][-1] == "F"


def test_a_process_that_ignores_terminate_is_killed_after_three_seconds(tmp_path):
    plan = _plan(tmp_path)
    token = cancel_mod.CancelToken()
    token.cancel()
    fake = FakeFFmpeg(behave=lambda argv: "stubborn")
    # the token is already set: the runner stops before starting anything
    result = _run(tmp_path, plan, fake, cancel=token)
    assert result["state"] == "cancelled" and fake.calls == []

    clock = Clock()
    proc = FakeProc(FakeFFmpeg(clock=clock, behave=lambda argv: "stubborn"), ["ffmpeg"], str(tmp_path), None, None)
    runner._stop(proc, clock=clock)
    assert proc.terminated_at == 0.0 and proc.killed_at >= runner.KILL_GRACE_S
    assert proc.killed_at < runner.KILL_GRACE_S + runner.POLL_S + 1e-9


def test_a_stage_that_outlives_its_timeout_fails(tmp_path):
    plan = _plan(tmp_path)
    fake = FakeFFmpeg(behave=lambda argv: "hang" if argv[-1] == plan["stages"][0]["write"] else None)
    result = _run(tmp_path, plan, fake, timeouts={"shot": 2.0})
    assert result["state"] == "failed" and result["failed_stage"] == "S:sh01"
    assert result["error"] == "stage S:sh01 failed: timed out after 2 s"
    proc = fake.procs[0]
    assert proc.terminated_at is not None and 2.0 < proc.terminated_at <= 2.0 + runner.POLL_S
    stage = json.loads((tmp_path / "render_manifest.json").read_text())["stages"][0]
    assert stage["state"] == "failed" and stage["stderr_tail"].startswith("timed out after 2 s")


def test_default_timeouts_cover_every_stage_kind():
    assert set(runner.STAGE_TIMEOUT_S) == set(schemas.RENDER_STAGE_KINDS)
    assert runner.POLL_S == 0.25 and runner.KILL_GRACE_S == 3.0


def test_a_cache_hit_starts_no_process(tmp_path):
    plan = _plan(tmp_path)
    first = _run(tmp_path, plan, FakeFFmpeg())
    assert first["cached"] == []
    fake = FakeFFmpeg()
    second = _run(tmp_path, plan, fake)
    assert second["state"] == "completed"
    assert second["cached"] == ["S:sh01", "S:sh02", "S:sh03", "E"]
    assert second["ran"] == ["A", "L1", "F", "L2", "P", "P:loudness", "M"]
    assert len(fake.calls) == 7 and not any(token.startswith("cache/") for call in fake.calls for token in call
                                             if call.index(token) == len(call) - 1)
    doc = second["manifest"]
    cached = [stage for stage in doc["stages"] if stage["state"] == "cached"]
    assert [s["kind"] for s in cached] == ["shot", "shot", "shot", "end_card"]
    assert all(s["seconds"] is None and s["cache_key"] and s["output_sha256"] for s in cached)


def test_one_changed_image_reruns_one_shot(tmp_path):
    _run(tmp_path, _plan(tmp_path), FakeFFmpeg())
    changed = plan_mod.build_render_plan(**_plan_args(tmp_path, variant_shot="sh02"), ffmpeg=dict(FFMPEG),
                                         profile="golden")
    fake = FakeFFmpeg()
    result = _run(tmp_path, changed, fake)
    assert [sid for sid in result["ran"] if sid.startswith("S:")] == ["S:sh02"]
    assert result["cached"] == ["S:sh01", "S:sh03", "E"]


def test_the_cache_keeps_what_the_last_two_manifests_name(tmp_path):
    cache = tmp_path / "render" / "cache"
    plan_a = _plan(tmp_path)
    _run(tmp_path, plan_a, FakeFFmpeg())
    keys_a = {s["cache_key"] for s in plan_a["stages"] if s["cache_key"]}
    plan_b = plan_mod.build_render_plan(**_plan_args(tmp_path, variant_shot="sh02"), ffmpeg=dict(FFMPEG),
                                        profile="golden")
    stray = "f" * 64
    (cache / f"{stray}.mp4").write_bytes(b"old")
    (cache / f"{stray}.part.mp4").write_bytes(b"old partial")
    (cache / "notes.txt").write_text("not a cache file")
    _run(tmp_path, plan_b, FakeFFmpeg())
    keys_b = {s["cache_key"] for s in plan_b["stages"] if s["cache_key"]}
    on_disk = {name.split(".")[0] for name in os.listdir(cache) if name.endswith(".mp4")}
    assert on_disk == keys_a | keys_b and len(on_disk) == 5
    assert (cache / "notes.txt").exists()
    # a third render with plan_a again: plan_b's changed shot is still named by
    # the previous manifest, so it stays; nothing else is left to prune.
    _run(tmp_path, plan_a, FakeFFmpeg())
    assert {n.split(".")[0] for n in os.listdir(cache) if n.endswith(".mp4")} == keys_a | keys_b
    # and a fourth: now only plan_a is named by the last two manifests.
    _run(tmp_path, plan_a, FakeFFmpeg())
    assert {n.split(".")[0] for n in os.listdir(cache) if n.endswith(".mp4")} == keys_a


def test_prune_cache_touches_only_cache_files(tmp_path):
    keep, drop = "a" * 64, "b" * 64
    for name in (f"{keep}.mp4", f"{drop}.mp4", f"{drop}.part.mp4", "readme.txt", f"{drop}.mp4.tmp"):
        (tmp_path / name).write_bytes(b"x")
    assert runner.prune_cache(str(tmp_path), {keep}) == [f"{drop}.mp4", f"{drop}.part.mp4"]
    assert sorted(os.listdir(tmp_path)) == sorted([f"{keep}.mp4", "readme.txt", f"{drop}.mp4.tmp"])


def test_an_input_that_changed_after_planning_fails_the_render(tmp_path):
    plan = _plan(tmp_path)
    shot = next(item for item in plan["inputs"] if item["id"] == "sh01")
    with open(shot["path"], "ab") as handle:
        handle.write(b"tampered")
    fake = FakeFFmpeg()
    result = _run(tmp_path, plan, fake)
    assert result["state"] == "failed" and "changed after the render was planned" in result["error"]
    assert fake.calls == []
    assert schemas.render_manifest_errors(json.loads((tmp_path / "render_manifest.json").read_text())) == []


def test_a_symlinked_work_folder_is_refused(tmp_path):
    plan = _plan(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "render").mkdir()
    os.symlink(tmp_path / "elsewhere", tmp_path / "render" / "cache")
    with pytest.raises(runner.RunnerError, match="symlink"):
        _run(tmp_path, plan, FakeFFmpeg())


def test_a_process_that_cannot_start_fails_its_stage(tmp_path):
    plan = _plan(tmp_path)

    def popen(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    result = runner.run_render(plan, render_dir=tmp_path / "render", manifest_path=tmp_path / "m.json",
                               popen=popen, clock=Clock())
    assert result["state"] == "failed" and result["failed_stage"] == "S:sh01"
    assert "could not start" in result["manifest"]["stages"][0]["stderr_tail"]


# ================================================== 5. pre-flight, render()

def _fake_run(*, filters=FILTERS_OUTPUT, missing_tool=None):
    def run(cmd, **kwargs):
        if cmd[0] == missing_tool:
            raise FileNotFoundError(2, "No such file or directory", cmd[0])
        if cmd[-1] == "-version":
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{cmd[0]} version 6.1.1-test Copyright (c)\n",
                                               stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=filters, stderr="")
    return run


def test_preflight_reads_the_version_and_the_filters():
    assert runner.preflight(run=_fake_run(), machine="aarch64") == {"version": "6.1.1-test", "machine": "aarch64"}
    assert runner.ffmpeg_version("ffmpeg version 7.1.5-0+deb13u1 Copyright (c) 2000-2025") == "7.1.5-0+deb13u1"
    assert runner.listed_filters(FILTERS_OUTPUT) == set(runner.REQUIRED_FILTERS)


@pytest.mark.parametrize("name", runner.REQUIRED_FILTERS)
def test_preflight_names_a_missing_filter(name):
    filters = "\n".join(line for line in FILTERS_OUTPUT.splitlines() if f" {name} " not in line)
    with pytest.raises(runner.PreflightError, match=rf"lacks the filter\(s\) {name} "):
        runner.preflight(run=_fake_run(filters=filters))


@pytest.mark.parametrize("tool", ["ffmpeg", "ffprobe"])
def test_preflight_names_a_missing_tool(tool):
    with pytest.raises(runner.PreflightError, match=rf"^{tool} is not installed"):
        runner.preflight(run=_fake_run(missing_tool=tool))


def test_render_reports_a_preflight_failure_and_writes_nothing(tmp_path):
    fake = FakeFFmpeg()
    result = runner.render(plan_args=_plan_args(tmp_path), profile="golden", render_dir=tmp_path / "render",
                           manifest_path=tmp_path / "render_manifest.json", run=_fake_run(missing_tool="ffprobe"),
                           popen=fake)
    assert result["state"] == "failed" and result["error"].startswith("ffmpeg pre-flight: ffprobe is not installed")
    assert result["manifest"] is None and fake.calls == []
    assert not (tmp_path / "render_manifest.json").exists() and not (tmp_path / "render").exists()


def test_render_reports_a_graph_error_as_a_failed_plan(tmp_path):
    manifest_path = tmp_path / "render_manifest.json"
    manifest_path.write_text('{"previous": "render"}')
    args = _plan_args(tmp_path)
    args["template"]["transitions_s"]["dissolve"] = 0.41
    args["storyboard"]["transitions"][0]["duration_s"] = 0.41
    fake = FakeFFmpeg()
    result = runner.render(plan_args=args, profile="golden", render_dir=tmp_path / "render",
                           manifest_path=manifest_path, run=_fake_run(), popen=fake)
    assert result["state"] == "failed"
    assert result["error"].startswith("render plan: GraphError: the dissolve after 'sh02' lasts 0.41s")
    assert fake.calls == [] and manifest_path.read_text() == '{"previous": "render"}'


def test_render_runs_the_plan_with_the_preflight_version(tmp_path):
    fake = FakeFFmpeg()
    result = runner.render(plan_args=_plan_args(tmp_path), profile="golden", render_dir=tmp_path / "render",
                           manifest_path=tmp_path / "render_manifest.json",
                           final_path=tmp_path / "episode_final.mp4", run=_fake_run(), popen=fake,
                           clock=fake.clock, machine="testarch")
    assert result["state"] == "completed", result["error"]
    assert result["manifest"]["ffmpeg"] == {"version": "6.1.1-test", "machine": "testarch"}
