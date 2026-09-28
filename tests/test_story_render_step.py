"""The ``render`` step (AI Story phase 4, stage 9; spec 3 step 11, 2.9, 6.5,
13; DEC-156..159, DEC-161, DEC-164).

The episode is stage 8's own fixture (``tests/test_story_assets_step.py``:
phase 3's French story, written, planned fast, approved, its assets made by
the fake image adapter and the fake Edge voice), then its assets approved
with their current fingerprint. No ffmpeg runs here: every process is stage
7's fake (``tests/test_aistory_render_runner.py``: ``FakeFFmpeg`` writes the
files each command would write, ``_fake_run`` answers the pre-flight). The
real render of a whole episode is the stage's scratch proof.

Built once per session and copied per test (the story folder is plain
files). Offline and hermetic: stage 8's own ``hermetic`` fixture.

The step module is imported inside the tests, so on the parent commit
(``f499d9f``) each test fails on its own.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

import test_aistory_render_runner as rr
import test_story_assets_step as tas
import test_story_episode_steps as eps
from clipping.aistory import schemas
from clipping.aistory import store as store_mod
from clipping.cancel import Cancelled
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used here as they are

NOW = eps.NOW
TAIL_STAGES = ["A", "L1", "F", "L2", "P", "P:loudness", "M"]


def _render_mod():
    from clipping.aistory.steps import render

    return render


# ------------------------------------------------------------ the episode

@pytest.fixture(scope="session")
def built(tmp_path_factory):
    """``{kind: (outputs copy, story_id)}``, filled by the first test that
    needs each kind of episode."""
    return {"root": tmp_path_factory.mktemp("render_step_episodes"), "kinds": {}}


def _ec(store, story_id):
    from clipping.aistory.steps import episode_common

    return episode_common.load_context(store, story_id, 1)


def approve_assets(store, story_id, *, fingerprint=None):
    """What stage 11's approve will do: store the current fingerprint."""
    from clipping.aistory.steps import assets

    ec = _ec(store, story_id)
    script = store.read_episode_doc(story_id, 1, "script.json")
    board = store.read_episode_doc(story_id, 1, "storyboard.json")
    doc = store.read_episode_doc(story_id, 1, "assets.json")
    doc["approved"] = {"at": NOW, "fingerprint": fingerprint or assets.current_fingerprint(ec, board, script, doc)}
    store.write_episode_doc(story_id, 1, "assets.json", doc, now=NOW)


def _build_approved(store, tmp_path):
    story_id = tas._episode(store, tmp_path)
    summary, _log = tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    assert summary["complete"] is True
    approve_assets(store, story_id)
    return story_id


def episode(store, tmp_path, built, kind="approved"):
    """A copy of the session's *kind* episode in this test's ``outputs/``."""
    if kind not in built["kinds"]:
        story_id = {"approved": _build_approved}[kind](store, tmp_path)
        copy = built["root"] / kind
        shutil.copytree(store.outputs_dir, copy, symlinks=True)
        built["kinds"][kind] = (copy, story_id)
        return story_id
    copy, story_id = built["kinds"][kind]
    shutil.copytree(copy, store.outputs_dir, symlinks=True, dirs_exist_ok=True)
    return story_id


def ep_dir(store, story_id) -> Path:
    return Path(store.story_dir(story_id)) / "episodes" / "ep01"


def ctx_for(store, story_id, *, params=None, step="render"):
    return eps._ctx(store, story_id, step=step, params=params, settings=tas._settings())


def render(store, story_id, *, params=None, fake=None, run_process=None, ctx=None, tmp_path=None, **kwargs):
    """The step with stage 7's fakes; ``(summary, log, fake)``."""
    fake = fake or rr.FakeFFmpeg()
    if ctx is None:
        ctx, log = ctx_for(store, story_id, params=params)
    else:
        log = ctx.on_log
    fonts_dir = (tmp_path or Path(store.outputs_dir).parent) / "no_custom_fonts"
    summary = _render_mod().run(ctx, run_process=run_process or rr._fake_run(), popen=fake, clock=fake.clock,
                                custom_fonts_dir=fonts_dir, **kwargs)
    return summary, log, fake


def refused(store, story_id, **kwargs):
    fake = rr.FakeFFmpeg()
    with pytest.raises(eps.steps.StepFailed) as caught:
        render(store, story_id, fake=fake, **kwargs)
    return str(caught.value), fake


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _shots(store, story_id):
    return store.read_episode_doc(story_id, 1, "storyboard.json")["shots"]


def _lines(store, story_id):
    script = store.read_episode_doc(story_id, 1, "script.json")
    return [line for scene in script["scenes"] for line in scene["lines"]]


# ============================================================ the render

def test_a_render_is_planned_from_the_episode_and_its_files(store, tmp_path, built, monkeypatch):
    from clipping.aistory.render import plan as plan_mod
    from clipping.aistory.render import fonts

    story_id = episode(store, tmp_path, built)
    before = eps._story_bytes(store, story_id)
    seen = {}
    real = plan_mod.build_render_plan

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(plan_mod, "build_render_plan", spy)
    summary, log, fake = render(store, story_id, tmp_path=tmp_path)

    folder = ep_dir(store, story_id)
    shots, lines = _shots(store, story_id), _lines(store, story_id)
    assets_doc = store.read_episode_doc(story_id, 1, "assets.json")
    inputs = seen["inputs"]
    # every shot's image and every line's audio, as the store resolves them
    assert list(inputs["shots"]) == [shot["shot_id"] for shot in shots]
    for shot in shots:
        record = inputs["shots"][shot["shot_id"]]
        assert record["source"] == shot["assets"]["image"]
        assert record["path"] == os.path.realpath(folder / shot["assets"]["image"])
        assert record["sha256"] == _sha(record["path"])
    assert list(inputs["lines"]) == [line["line_id"] for line in lines]
    for line in lines:
        record = inputs["lines"][line["line_id"]]
        assert record["source"] == line["timing"]["audio"] == f"assets/voice/line_{line['line_id'][1:]}.mp3"
        assert record["sha256"] == _sha(folder / line["timing"]["audio"])
        # Edge's own word timings, from the line's sidecar (spec 6.4 source 1)
        sidecar = json.loads((folder / "assets" / "voice" / f"line_{line['line_id'][1:]}.json").read_text())
        assert inputs["word_timings"][line["line_id"]] == sidecar["words"]
    # SFX and the bed: the shipped files assets.json names (repo-relative)
    resolved = {cue["cue"]: cue["file"] for cue in assets_doc["sfx"] if cue["state"] == "resolved"}
    assert {cue: record["source"] for cue, record in inputs["sfx"].items()} == resolved
    assert inputs["bgm"]["source"] == assets_doc["bgm"]["file"] and inputs["bgm"]["sha256"] == assets_doc["bgm"]["sha256"]
    assert inputs["overlay"]["source"] == "assets/overlays/paper_texture.png"
    assert inputs["font"] == fonts.resolve_font("Montserrat ExtraBold", custom_fonts_dir=tmp_path / "no_custom_fonts")
    assert seen["story"] == {"story_id": story_id, "title": store.get(story_id)["title"], "language": "fr"}
    assert (seen["ep"], seen["subtitles"], seen["encoder"], seen["video_encoder"], seen["profile"]) == (
        1, "style", "libx264", None, "final")
    assert seen["ffmpeg"] == {"version": "6.1.1-test", "machine": seen["ffmpeg"]["machine"]}

    # the manifest (read and validated by the store), the video, the subtitles
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    stages = manifest["stages"]
    # Two shots of the fixture ask for the very same clip (the same cached
    # image, the same motion): the second one is the first one's cache hit.
    first_key = {}
    for stage in stages:
        if stage["kind"] == "shot":
            expected_state = "cached" if stage["cache_key"] in first_key else "done"
            first_key.setdefault(stage["cache_key"], stage["id"])
        else:
            expected_state = "done"
        assert stage["state"] == expected_state, stage["id"]
    assert manifest["params"] == {"subtitles": "word_pop", "encoder": "libx264"}
    assert manifest["output"]["path"] == "episode_final.mp4"
    assert manifest["output"]["sha256"] == _sha(folder / "episode_final.mp4")
    assert (folder / "subtitles.ass").read_bytes() == (folder / "render" / "subtitles.ass").read_bytes()
    assert "WordPop" in (folder / "subtitles.ass").read_text(encoding="utf-8")
    assert (folder / "render" / "logs").is_dir() and any((folder / "render" / "logs").iterdir())
    # the summary
    assert summary["ep"] == 1 and summary["state"] == "completed" and summary["profile"] == "final"
    assert summary["ran"] == [s["id"] for s in stages if s["state"] == "done"]
    assert summary["cached"] == [s["id"] for s in stages if s["state"] == "cached"]
    assert summary["ran"][-len(TAIL_STAGES):] == TAIL_STAGES
    assert [s["id"] for s in stages if s["kind"] == "shot"] == [f"S:{shot['shot_id']}" for shot in shots]
    assert summary["output"] == {"file": "episode_final.mp4", "sha256": manifest["output"]["sha256"],
                                 "width": 1080, "height": 1920, "fps": "30/1"}
    assert summary["duration_s"] == 4.76 and summary["loudness"] == {"i": -14.1, "tp": -1.2, "lra": 3.0}
    assert summary["fingerprint"] == store.read_episode_doc(story_id, 1, "assets.json")["approved"]["fingerprint"]
    assert (summary["manifest"], summary["subtitles_file"]) == ("render_manifest.json", "subtitles.ass")
    assert log[-1].startswith("✅ Episode 1 rendered: 4.8 s, 1080x1920 at 30/1 fps, -14.1 LUFS")
    # RC-E2: the story is never written
    assert eps._story_bytes(store, story_id) == before


def test_warnings_are_reported_in_the_feed_and_the_manifest_never_a_failure(store, tmp_path, built, monkeypatch):
    story_id = episode(store, tmp_path, built)
    loud = rr.FINAL_STDERR.replace('"-14.10"', '"-11.50"').replace('"-1.20"', '"-0.40"')
    monkeypatch.setattr(rr, "FINAL_STDERR", loud)

    summary, log, _fake = render(store, story_id, tmp_path=tmp_path)

    expected = ["The episode is 4.8 s long, outside 55-75 s.",
                "Loudness -11.5 LUFS is outside -14 +/- 1 LU.",
                "True peak -0.4 dBTP is above -1 dBTP."]
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    assert summary["state"] == "completed"
    assert summary["warnings"] == expected and manifest["warnings"] == expected
    assert [line for line in log if line.startswith("⚠️")] == [f"⚠️ {w}" for w in expected]


def test_a_length_and_loudness_inside_the_targets_warn_nothing(store, tmp_path, built, monkeypatch):
    story_id = episode(store, tmp_path, built)
    monkeypatch.setattr(rr, "PROBE", dict(rr.PROBE, format={"duration": "61.200000"}))
    summary, log, _fake = render(store, story_id, tmp_path=tmp_path)
    assert summary["warnings"] == [] and not any(line.startswith("⚠️") for line in log)


def test_a_subtitles_change_runs_only_what_the_cache_cannot_hold(store, tmp_path, built):
    """DEC-164: the shots come from the render cache; the mix, the final
    pass and what follows run again."""
    story_id = episode(store, tmp_path, built)
    first, _log, fake1 = render(store, story_id, tmp_path=tmp_path)
    shot_stages = [f"S:{shot['shot_id']}" for shot in _shots(store, story_id)]
    assert [stage for stage in first["ran"] + first["cached"] if stage.startswith("S:")] != []
    assert len(fake1.calls) == len(first["ran"])

    second, log, fake2 = render(store, story_id, tmp_path=tmp_path, params={"subtitles": "two_line"})

    assert second["cached"] == shot_stages
    assert second["ran"] == TAIL_STAGES and len(fake2.calls) == len(TAIL_STAGES)
    assert second["params"] == {"subtitles": "two_line", "encoder": "libx264"}
    folder = ep_dir(store, story_id)
    text = (folder / "subtitles.ass").read_text(encoding="utf-8")
    assert "WordPop" not in text and "Style: TwoLine_char_kiwilo," in text
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    assert [s["state"] for s in manifest["stages"] if s["kind"] == "shot"] == ["cached"] * len(shot_stages)
    assert any(line.endswith(f"{len(shot_stages)} from the cache.") for line in log)


def test_the_style_mode_and_none_are_accepted(store, tmp_path, built):
    story_id = episode(store, tmp_path, built)
    summary, _log, _fake = render(store, story_id, tmp_path=tmp_path, params={"subtitles": "none"})
    assert summary["params"]["subtitles"] == "none"
    text = (ep_dir(store, story_id) / "subtitles.ass").read_text(encoding="utf-8")
    assert "WordPop" not in text and "Généré par IA" in text


def test_a_cancel_during_ffmpeg_raises_cancelled_and_keeps_the_manifest(store, tmp_path, built):
    story_id = episode(store, tmp_path, built)
    ctx, log = ctx_for(store, story_id)
    fake = rr.FakeFFmpeg(behave=lambda argv: "hang" if "episode_pre.mkv" in argv else None,
                         on_wait=lambda clock: ctx.cancel.cancel())

    with pytest.raises(Cancelled):
        render(store, story_id, ctx=ctx, fake=fake, tmp_path=tmp_path)

    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    assert manifest["stages"][-1]["id"] == "F" and manifest["stages"][-1]["state"] == "cancelled"
    assert manifest["output"] is None and fake.procs[-1].terminated_at is not None
    assert not (ep_dir(store, story_id) / "episode_final.mp4").exists()
    assert any(line.startswith("⏹ Episode 1's render was cancelled") for line in log)


def test_a_failed_stage_is_a_step_failure_naming_it_and_its_stderr(store, tmp_path, built):
    story_id = episode(store, tmp_path, built)
    fake = rr.FakeFFmpeg(behave=lambda argv: (1, "[Parsed_xfade_3] something broke in xfade\n",
                                              "episode_pre.mkv") if "episode_pre.mkv" in argv else None)

    with pytest.raises(eps.steps.StepFailed) as caught:
        render(store, story_id, fake=fake, tmp_path=tmp_path)

    message = str(caught.value)
    assert message.startswith("Episode 1's render failed at stage F (final): stage F failed: exit 1.")
    assert "stderr: exit 1 [Parsed_xfade_3] something broke in xfade" in message
    assert "render_manifest.json has every command" in message
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    assert manifest["stages"][-1]["state"] == "failed" and manifest["stages"][-1]["output"] == "episode_pre.mkv"
    assert (ep_dir(store, story_id) / "render" / "episode_pre.mkv").read_bytes() == b"half a file"
    assert not (ep_dir(store, story_id) / "subtitles.ass").exists()


# ========================================================= preconditions

def _assets_doc(store, story_id):
    return store.read_episode_doc(story_id, 1, "assets.json")


def _unapprove(store, story_id):
    doc = _assets_doc(store, story_id)
    doc["approved"] = None
    store.write_episode_doc(story_id, 1, "assets.json", doc, now=NOW)


def _stale(store, story_id):
    path = ep_dir(store, story_id) / "assets" / "shots" / "shot_02.png"
    path.write_bytes(path.read_bytes() + b"another take")


def _no_image(store, story_id):
    (ep_dir(store, story_id) / "assets" / "shots" / "shot_03.png").unlink()


def _no_audio(store, story_id):
    line = _lines(store, story_id)[1]
    (ep_dir(store, story_id) / line["timing"]["audio"]).unlink()
    return {"line": line["line_id"]}


def _no_assets_doc(store, story_id):
    (ep_dir(store, story_id) / "assets.json").unlink()


def _script_unapproved(store, story_id):
    script = store.read_episode_doc(story_id, 1, "script.json")
    script["approved_at"] = None
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)


@pytest.mark.parametrize("breaks, expected", [
    (_unapprove, "Approve episode 1's assets first: the render is made from the approved images, voices and "
                 "sounds."),
    (_stale, "Episode 1's assets changed since they were approved (an image, a voice or a sound is not the one "
             "approved): look at them, approve them again, then render."),
    (_no_image, "Episode 1 cannot be rendered: shot sh03 has no image. Make it (the assets step, or regenerate "
                "shot:1:sh03), approve the assets again, then render."),
    (_no_audio, "Episode 1 cannot be rendered: line {line} has no audio in the speaker's pinned voice. Speak it "
                "(the assets step, or regenerate line:1:{line}), approve the assets again, then render."),
    (_no_assets_doc, "Episode 1 has no assets yet: make them (the assets step), approve them, then render."),
    (_script_unapproved, "Approve episode 1's script first"),
], ids=["unapproved", "stale-fingerprint", "missing-image", "missing-audio", "no-assets-doc", "script"])
def test_each_precondition_is_a_named_refusal_before_any_process(store, tmp_path, built, breaks, expected):
    story_id = episode(store, tmp_path, built)
    names = breaks(store, story_id) or {}
    expected = expected.format(**names)
    calls = []

    def run_process(cmd, **kwargs):
        calls.append(cmd)
        return rr._fake_run()(cmd, **kwargs)

    message, fake = refused(store, story_id, run_process=run_process, tmp_path=tmp_path)

    assert message.startswith(expected), message
    assert calls == [] and fake.calls == []
    folder = ep_dir(store, story_id)
    assert not (folder / "render").exists() and not (folder / "render_manifest.json").exists()


def test_a_missing_ffmpeg_is_refused_by_the_preflight_before_anything_is_written(store, tmp_path, built):
    story_id = episode(store, tmp_path, built)
    message, fake = refused(store, story_id, run_process=rr._fake_run(missing_tool="ffmpeg"), tmp_path=tmp_path)
    assert message.startswith("Episode 1 cannot be rendered: ffmpeg pre-flight: ffmpeg is not installed")
    assert fake.calls == [] and not (ep_dir(store, story_id) / "render").exists()


def test_a_missing_filter_is_refused_naming_it(store, tmp_path, built):
    story_id = episode(store, tmp_path, built)
    filters = "\n".join(line for line in rr.FILTERS_OUTPUT.splitlines() if " ass " not in line)
    message, fake = refused(store, story_id, run_process=rr._fake_run(filters=filters), tmp_path=tmp_path)
    assert "lacks the filter(s) ass the renderer needs" in message and fake.calls == []


@pytest.mark.parametrize("params, expected", [
    ({"subtitles": "karaoke"}, "The subtitles must be one of style, word_pop, two_line, none, not 'karaoke'."),
    ({"encoder": "nvenc"}, "The encoder must be one of libx264, auto, not 'nvenc'."),
])
def test_a_bad_parameter_is_refused_naming_the_choices(store, tmp_path, built, params, expected):
    story_id = episode(store, tmp_path, built)
    message, fake = refused(store, story_id, params=params, tmp_path=tmp_path)
    assert message == expected and fake.calls == []


def test_a_symlinked_render_folder_is_refused_never_followed(store, tmp_path, built):
    story_id = episode(store, tmp_path, built)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (ep_dir(store, story_id) / "render").symlink_to(elsewhere, target_is_directory=True)
    message, fake = refused(store, story_id, tmp_path=tmp_path)
    assert "is not a real file or folder; it is never followed" in message
    assert fake.calls == [] and list(elsewhere.iterdir()) == []


def test_a_symlinked_manifest_is_refused_never_followed(store, tmp_path, built):
    story_id = episode(store, tmp_path, built)
    target = tmp_path / "manifest_elsewhere.json"
    target.write_text("{}")
    (ep_dir(store, story_id) / "render_manifest.json").symlink_to(target)
    message, fake = refused(store, story_id, tmp_path=tmp_path)
    assert "render_manifest.json is not a real file or folder" in message
    assert fake.calls == [] and target.read_text() == "{}"


# =============================================================== encoder

def test_encoder_auto_puts_a_detected_hardware_encoder_on_the_final_pass_only(store, tmp_path, built):
    story_id = episode(store, tmp_path, built)
    asked = []

    def detect(cfg, target_h):
        asked.append((cfg, target_h))
        return {"name": "h264_nvenc", "args": ["-c:v", "h264_nvenc", "-preset", "p1", "-cq", "25"]}

    summary, log, fake = render(store, story_id, tmp_path=tmp_path, params={"encoder": "auto"}, detect=detect)

    assert asked == [(None, 1080)]
    final = next(argv for argv in fake.calls if "episode_pre.mkv" in argv)
    assert final[final.index("-c:v"):final.index("-c:v") + 8] == [
        "-c:v", "h264_nvenc", "-preset", "p1", "-cq", "25", "-pix_fmt", "yuv420p"]
    assert "libx264" not in final
    shots = [argv for argv in fake.calls if argv[-1].startswith("cache/")]
    assert len(shots) == len(summary["ran"]) - len(TAIL_STAGES) and all("libx264" in argv for argv in shots)
    assert summary["params"] == {"subtitles": "word_pop", "encoder": "auto"}
    assert "🚀 encoder auto: h264_nvenc encodes the final pass" in log


@pytest.mark.parametrize("answer, said", [
    ({"name": "libx264", "args": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "25"]},
     "ℹ️ encoder auto: no hardware encoder answered (libx264); the final pass stays on libx264."),
    ({"name": "h264_vaapi", "args": ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi"]},
     "ℹ️ encoder auto: h264_vaapi answered, but it needs its own upload filter"),
    (ImportError("No module named 'cv2'"),
     "⚠️ encoder auto: the hardware probe could not run (ImportError: No module named 'cv2'); the final pass "
     "stays on libx264."),
])
def test_encoder_auto_keeps_libx264_when_no_usable_hardware_answers(store, tmp_path, built, answer, said):
    story_id = episode(store, tmp_path, built)

    def detect(cfg, target_h):
        if isinstance(answer, Exception):
            raise answer
        return answer

    _summary, log, fake = render(store, story_id, tmp_path=tmp_path, params={"encoder": "auto"}, detect=detect)

    final = next(argv for argv in fake.calls if "episode_pre.mkv" in argv)
    assert final[final.index("-c:v"):final.index("-c:v") + 6] == ["-c:v", "libx264", "-preset", "medium",
                                                                   "-crf", "20"]
    assert any(line.startswith(said) for line in log), log


def test_the_default_encoder_never_probes_the_hardware(store, tmp_path, built):
    story_id = episode(store, tmp_path, built)

    def detect(cfg, target_h):
        raise AssertionError("the hardware probe is opt-in only")

    summary, _log, _fake = render(store, story_id, tmp_path=tmp_path, detect=detect)
    assert summary["params"]["encoder"] == "libx264"


def test_the_plan_takes_a_detected_encoder_only_for_a_final_render(tmp_path):
    from clipping.aistory.render import plan as plan_mod

    nvenc = {"name": "h264_nvenc", "args": ["-c:v", "h264_nvenc", "-preset", "p1"]}
    plan = rr._plan(tmp_path, profile="final", encoder="auto", video_encoder=nvenc)
    final = next(stage for stage in plan["stages"] if stage["id"] == "F")["argv"]
    assert "h264_nvenc" in final and "libx264" not in final and plan["params"]["encoder"] == "auto"
    with pytest.raises(plan_mod.PlanError, match="a golden render is libx264 only"):
        rr._plan(tmp_path, profile="golden", encoder="auto", video_encoder=nvenc)
    with pytest.raises(plan_mod.PlanError, match="carries its own filter"):
        rr._plan(tmp_path, profile="final", encoder="auto",
                 video_encoder={"name": "h264_amf", "args": ["-vf", "x", "-c:v", "h264_amf"]})
    plain = rr._plan(tmp_path, profile="final", encoder="auto", video_encoder={"name": "libx264", "args": []})
    assert next(s for s in plain["stages"] if s["id"] == "F")["argv"] == \
        next(s for s in rr._plan(tmp_path, profile="final")["stages"] if s["id"] == "F")["argv"]


# ============================================================ the store

def test_logs_is_one_of_the_render_folders_and_a_symlinked_one_is_refused(store, tmp_path):
    story_id = eps._ready_story(store)
    assert "logs" in store_mod.EPISODE_RENDER_SUBDIRS
    made = store.episode_render_dir(story_id, 1, "logs", create=True)
    assert made == os.path.join(store.episode_render_dir(story_id, 1), "logs") and os.path.isdir(made)
    os.rmdir(made)
    elsewhere = tmp_path / "logs_elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, made, target_is_directory=True)
    for create in (False, True):
        with pytest.raises(KeyError):
            store.episode_render_dir(story_id, 1, "logs", create=create)
    with pytest.raises(KeyError):
        store.episode_render_dir(story_id, 1, "log")


def test_episode_doc_path_is_the_stores_own_checked_path(store, tmp_path):
    story_id = eps._ready_story(store)
    with pytest.raises(KeyError):
        store.episode_doc_path(story_id, 1, "render_manifest.json")  # no episode folder yet
    path = store.episode_doc_path(story_id, 1, "render_manifest.json", create=True)
    assert path == os.path.join(store.episode_dir(story_id, 1), "render_manifest.json")
    with pytest.raises(ValueError, match="not an episode document"):
        store.episode_doc_path(story_id, 1, "../story.json")
    with pytest.raises(ValueError):
        store.episode_doc_path(story_id, 1, "episode_final.mp4")
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    os.symlink(target, path)
    with pytest.raises(KeyError):
        store.episode_doc_path(story_id, 1, "render_manifest.json")
    os.unlink(path)
    os.mkdir(path)
    with pytest.raises(KeyError):
        store.episode_doc_path(story_id, 1, "render_manifest.json")
    for bad in (0, 100, True, "1"):
        with pytest.raises((KeyError, ValueError, TypeError)):
            store.episode_doc_path(story_id, bad, "render_manifest.json")


def test_render_and_metadata_are_registered_steps():
    from clipping.aistory import steps

    for name in ("render", "metadata"):
        assert callable(steps.RUNNERS[name]) and steps.RUNNERS[name].__name__ == f"run_{name}"
    assert schemas.RENDER_ENCODERS == ("libx264", "auto")
    assert _render_mod().SUBTITLE_CHOICES == ("style", "word_pop", "two_line", "none")
