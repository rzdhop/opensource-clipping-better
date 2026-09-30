"""Partial re-render (AI Story phase 5, stage 8 -- the plan's riskiest; plan 11
"Stage 8 -- partial re-render"; DEC-156, DEC-161, DEC-164; RC-M2, RC-M8).

A stale reuse would ship an old frame silently, so every reuse is proven:

1. the selector (``render/partial.py``), pure, over the golden fixture's
   plans and a fake-ffmpeg baseline: which shot clips a render takes from the
   cache and which it makes again, and why (``image``, ``motion``,
   ``frames``, ``modifiers``, ``overlay``, ``missing``, ``corrupt``, plus
   ``new`` and ``settings``); a hit is the cached clip's **recorded sha256**,
   not only a file of some size;
2. the runner (fake ffmpeg): the baseline -- ``render_manifest.last_good.json``
   -- is swapped only by a render that completed, a failed one leaves it and
   the old final untouched; the manifest's optional ``reuse`` record says what
   was rebuilt and reused, and an old manifest still validates;
3. the selection table on the render step's episode, one row per edit of
   stage 7's table: the shots whose render key moved are the ones rebuilt, for
   the expected reason, and the dry run (``render.render_changes``) names
   exactly what the re-render then runs;
4. the ``rerender`` step: its refusals, its end (``completed``), the feed and
   the episode payload's "N of M shots re-rendered", the metadata pack left
   stale, a storyboard's conversion to whole frames said as such;
5. partial == full under the real ffmpeg: the golden fixture with a line's
   duration and one image changed, re-rendered over a warm cache, has the
   same framemd5 as a clean render of the same documents, and only the
   expected ``S:`` stages ran. Like the golden test, it never skips.

The modules under test are imported inside the tests, so on the parent
commit each test fails on its own. Stdlib + pytest (DEC-012): runs in the CI
environment.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import shutil
from pathlib import Path

import pytest

import test_aistory_render_runner as rr
import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_metadata_step as tms
import test_story_reedit as tre
import test_story_render_step as trs
from clipping.aistory import schemas, shots, templates
from clipping.aistory.render import golden
from clipping.aistory.render import manifest as manifest_mod
from clipping.aistory.render import plan as plan_mod
from clipping.aistory.render import runner
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_render_step import built  # noqa: F401 -- the render step's session copy of the episode

NOW = eps.NOW
LATER = tre.LATER
LATEST = tre.LATEST
FFMPEG = dict(rr.FFMPEG)
GOLDEN_SHOTS = [shot_id for shot_id, _scene, _d, _m in golden.SHOTS]


def _partial():
    return importlib.import_module("clipping.aistory.render.partial")


def _render_mod():
    from clipping.aistory.steps import render

    return render


def _rerender_mod():
    return importlib.import_module("clipping.aistory.steps.rerender")


def _wf():
    return importlib.import_module("clipping.aistory.workflow")


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ===================================================== the golden fixture, faked

def _args(tmp_path, *, docs=None, variant_shot=None):
    docs = docs if docs is not None else golden.build_documents()
    inputs = golden.write_sources(tmp_path, variant_shot=variant_shot)
    return {**docs, "story": dict(golden.STORY), "ep": golden.EP, "inputs": inputs}


def _plan(tmp_path, *, docs=None, variant_shot=None, ffmpeg=None, profile="golden", inputs=None):
    args = _args(tmp_path, docs=docs, variant_shot=variant_shot)
    if inputs is not None:
        args["inputs"].update(inputs)
    return plan_mod.build_render_plan(**args, ffmpeg=dict(ffmpeg or FFMPEG), profile=profile)


def _baseline(tmp_path):
    """A completed fake render of the golden fixture: ``(plan, manifest)``;
    its clips sit in ``render/cache/``."""
    plan = _plan(tmp_path)
    result = rr._run(tmp_path, plan, rr.FakeFFmpeg())
    assert result["state"] == "completed", result["error"]
    return plan, result["manifest"]


def _cache(tmp_path) -> Path:
    return tmp_path / "render" / "cache"


def _key(plan, stage_id) -> str:
    return next(stage["cache_key"] for stage in plan["stages"] if stage["id"] == stage_id)


def _select(baseline, plan, tmp_path, **kwargs):
    return _partial().select(baseline, plan, str(_cache(tmp_path)), **kwargs)


def _docs(**board_changes):
    docs = golden.build_documents()
    for shot_id, changes in board_changes.items():
        next(shot for shot in docs["storyboard"]["shots"] if shot["shot_id"] == shot_id).update(changes)
    return docs


# ================================================================ 1. the selector

def test_nothing_changed_reuses_every_shot_and_the_end_card(tmp_path):
    plan, baseline = _baseline(tmp_path)
    selection = _select(baseline, _plan(tmp_path), tmp_path)
    assert selection["rebuild"] == [] and selection["reasons"] == {}
    assert selection["reuse"] == GOLDEN_SHOTS and selection["shots_total"] == 3
    assert selection["end_card"] == "reuse" and selection["timing_converted"] is False


@pytest.mark.parametrize("change, expected", [
    ({"variant_shot": "sh02"}, {"sh02": "image"}),
    ({"docs": _docs(sh02={"motion": {"type": "push_in", "zoom_from": 1.0, "zoom_to": 1.1, "pan": "none"}})},
     {"sh02": "motion"}),
    ({"docs": _docs(sh02={"modifiers": ["handheld"]})}, {"sh02": "modifiers"}),
    ({"docs": _docs(sh03={"modifiers": ["jitter_stopmotion"]})}, {"sh03": "modifiers"}),
], ids=["image", "motion", "modifiers-handheld", "modifiers-jitter"])
def test_one_shot_changed_is_the_only_one_rebuilt_naming_why(tmp_path, change, expected):
    _plan0, baseline = _baseline(tmp_path)
    selection = _select(baseline, _plan(tmp_path, **change), tmp_path)
    assert selection["reasons"] == expected
    assert selection["rebuild"] == list(expected)
    assert selection["reuse"] == [sid for sid in GOLDEN_SHOTS if sid not in expected]
    assert selection["end_card"] == "reuse"


def test_shots_whose_frames_moved_are_rebuilt_for_their_frames(tmp_path):
    _plan0, baseline = _baseline(tmp_path)
    docs = golden.build_documents()
    sh01, sh02 = docs["storyboard"]["shots"][:2]
    sh01["duration_s"], sh02["duration_s"] = 1.0, round(sh02["duration_s"] - 0.1, 3)
    selection = _select(baseline, _plan(tmp_path, docs=docs), tmp_path)
    assert selection["reasons"] == {"sh01": "frames", "sh02": "frames"} and selection["reuse"] == ["sh03"]


def test_an_image_and_its_frames_changed_together_is_an_image_change(tmp_path):
    _plan0, baseline = _baseline(tmp_path)
    docs = golden.build_documents()
    sh01, sh02 = docs["storyboard"]["shots"][:2]
    sh01["duration_s"], sh02["duration_s"] = 1.0, round(sh02["duration_s"] - 0.1, 3)
    selection = _select(baseline, _plan(tmp_path, docs=docs, variant_shot="sh01"), tmp_path)
    assert selection["reasons"] == {"sh01": "image", "sh02": "frames"}


def test_an_overlay_change_rebuilds_every_shot_for_its_overlay(tmp_path):
    _plan0, baseline = _baseline(tmp_path)
    docs = golden.build_documents()
    docs["style_lock"]["motion_rules"]["tier1"]["overlays"] = ["paper_texture", "vignette"]
    selection = _select(baseline, _plan(tmp_path, docs=docs), tmp_path)
    assert selection["reasons"] == {sid: "overlay" for sid in GOLDEN_SHOTS}
    assert selection["end_card"] == "reuse"


def test_a_new_paper_texture_file_is_an_overlay_change(tmp_path):
    _plan0, baseline = _baseline(tmp_path)
    texture = tmp_path / "texture.png"
    texture.write_bytes(golden.png_bytes(8, 8, lambda x, y: (x * 30, y * 30, 90)))
    other = runner.file_record(texture, plan_mod.PAPER_TEXTURE_SOURCE)
    selection = _select(baseline, _plan(tmp_path, inputs={"overlay": other}), tmp_path)
    assert selection["reasons"] == {sid: "overlay" for sid in GOLDEN_SHOTS}


def test_another_ffmpeg_or_profile_rebuilds_everything_for_its_settings(tmp_path):
    _plan0, baseline = _baseline(tmp_path)
    upgraded = _select(baseline, _plan(tmp_path, ffmpeg={"version": "7.1.5-test", "machine": "testarch"}), tmp_path)
    assert upgraded["reasons"] == {sid: "settings" for sid in GOLDEN_SHOTS}
    assert upgraded["end_card"] == "rebuild"
    final = _select(baseline, _plan(tmp_path, profile="final"), tmp_path)
    assert final["reasons"] == {sid: "settings" for sid in GOLDEN_SHOTS} and final["reuse"] == []


def test_a_shot_the_baseline_never_rendered_is_new(tmp_path):
    plan, baseline = _baseline(tmp_path)
    older = copy.deepcopy(baseline)
    older["stages"] = [stage for stage in older["stages"] if stage["id"] != "S:sh03"]
    del older["cache"][_key(plan, "S:sh03")]  # nor kept its clip
    selection = _select(older, plan, tmp_path)
    assert selection["reasons"] == {"sh03": "new"} and selection["reuse"] == ["sh01", "sh02"]


def test_a_missing_clip_is_rebuilt_as_missing(tmp_path):
    plan, baseline = _baseline(tmp_path)
    (_cache(tmp_path) / f"{_key(plan, 'S:sh01')}.mp4").unlink()
    selection = _select(baseline, plan, tmp_path)
    assert selection["reasons"] == {"sh01": "missing"} and selection["reuse"] == ["sh02", "sh03"]


@pytest.mark.parametrize("damage", ["same-size", "empty", "symlink"])
def test_a_clip_that_is_not_the_recorded_one_is_rebuilt(tmp_path, damage):
    plan, baseline = _baseline(tmp_path)
    path = _cache(tmp_path) / f"{_key(plan, 'S:sh02')}.mp4"
    size = path.stat().st_size
    if damage == "same-size":
        path.write_bytes(b"X" * size)  # the right size, the wrong bytes
        expected = "corrupt"
    elif damage == "empty":
        path.write_bytes(b"")
        expected = "missing"
    else:
        (tmp_path / "elsewhere.mp4").write_bytes(path.read_bytes())
        path.unlink()
        os.symlink(tmp_path / "elsewhere.mp4", path)
        expected = "missing"
    selection = _select(baseline, plan, tmp_path)
    assert selection["reasons"] == {"sh02": expected} and selection["rebuild"] == ["sh02"]


def test_a_clip_no_manifest_records_is_never_reused(tmp_path):
    plan, baseline = _baseline(tmp_path)
    selection = _select(baseline, plan, tmp_path, recorded={})
    assert selection["rebuild"] == GOLDEN_SHOTS and selection["reasons"] == {sid: "missing" for sid in GOLDEN_SHOTS}
    assert selection["end_card"] == "rebuild"


def test_the_recorded_sha256s_come_from_done_and_cached_stages_only():
    p = _partial()
    key, sha, other = "a" * 64, "b" * 64, "c" * 64

    def doc(state, output_sha):
        return {"stages": [{"id": "S:sh01", "kind": "shot", "cache_key": key, "state": state,
                            "output": f"cache/{key}.mp4", "output_sha256": output_sha}]}

    assert p.recorded_shas(doc("done", sha), doc("cached", other)) == {key: {sha, other}}
    assert p.recorded_shas(doc("running", None), doc("failed", None), None, {"stages": "damaged"}) == {}
    assert p.recorded_shas() == {}
    # the clips a completed render kept, carried forward in its ``cache`` map
    kept = {"stages": [], "cache": {key: [other], "b" * 64: [sha], "not a key": [sha], "c" * 64: "no list"}}
    assert p.recorded_shas(kept, doc("done", sha)) == {key: {sha, other}, "b" * 64: {sha}}


def test_a_clip_made_twice_in_one_render_is_made_once(tmp_path):
    plan, baseline = _baseline(tmp_path)
    twice = copy.deepcopy(plan)
    first = next(stage for stage in twice["stages"] if stage["id"] == "S:sh02")
    twin = dict(copy.deepcopy(first), id="S:sh04")
    twice["stages"].insert(twice["stages"].index(first) + 1, twin)
    (_cache(tmp_path) / f"{first['cache_key']}.mp4").unlink()
    selection = _select(baseline, twice, tmp_path)
    assert selection["rebuild"] == ["sh02"] and selection["reuse"] == ["sh01", "sh04", "sh03"]
    assert selection["shots_total"] == 4


def test_without_a_baseline_every_shot_not_in_the_cache_is_new(tmp_path):
    plan, baseline = _baseline(tmp_path)
    p = _partial()
    assert _select(None, plan, tmp_path, recorded={})["reasons"] == {sid: "new" for sid in GOLDEN_SHOTS}
    # a failed render's clips are recorded by its manifest: reused, though there is no baseline
    warm = _select(None, plan, tmp_path, recorded=p.recorded_shas(baseline))
    assert warm["rebuild"] == [] and warm["reuse"] == GOLDEN_SHOTS


def test_the_selection_says_when_the_timing_converted_to_whole_frames(tmp_path):
    _plan0, baseline = _baseline(tmp_path)
    docs = golden.build_documents()
    sh01, sh02 = docs["storyboard"]["shots"][:2]
    sh01["duration_s"], sh02["duration_s"] = 1.0, round(sh02["duration_s"] - 0.1, 3)
    plan = _plan(tmp_path, docs=docs)
    assert baseline["whole_frames"] is False and plan["whole_frames"] is False
    converted = dict(plan, whole_frames=True)
    assert _select(baseline, converted, tmp_path)["timing_converted"] is True
    # a baseline recorded before stage 8 (no flag) was cut from a board timed before whole frames
    legacy = {k: v for k, v in baseline.items() if k != "whole_frames"}
    assert _select(legacy, converted, tmp_path)["timing_converted"] is True
    assert _select(dict(baseline, whole_frames=True), converted, tmp_path)["timing_converted"] is False
    assert _select(baseline, dict(_plan(tmp_path), whole_frames=True), tmp_path)["timing_converted"] is False


def test_the_baseline_is_the_last_good_render_else_a_completed_manifest():
    p = _partial()
    good = {"output": {"sha256": "a" * 64}, "ep": 1}
    failed = {"output": None, "ep": 1}
    assert p.baseline_of(good, failed) is good
    assert p.baseline_of(None, good) is good
    assert p.baseline_of(None, failed) is None and p.baseline_of(None, None) is None


def test_the_summary_sentence():
    p = _partial()
    record = {"shots_total": 11, "shots_rebuilt": ["sh01", "sh02", "sh03"], "timing_converted": False}
    assert p.summary(record) == "3 of 11 shots re-rendered"
    assert p.summary(dict(record, shots_rebuilt=["sh01"])) == "1 of 11 shots re-rendered"
    assert p.summary(dict(record, shots_total=1, shots_rebuilt=[])) == "0 of 1 shot re-rendered"
    assert p.summary(dict(record, timing_converted=True)) == (
        "3 of 11 shots re-rendered (the shot timing moved to whole frames)")
    assert set(p.REASONS) == {"image", "motion", "frames", "modifiers", "overlay", "missing", "corrupt", "new",
                              "settings"}


# ================================================================ 2. the runner

def _last_good(tmp_path) -> Path:
    return tmp_path / "render_manifest.last_good.json"


def _fail_at_f(argv):
    return (1, "boom in the final pass\n", plan_mod.PRE_REL) if argv[-1] == plan_mod.PRE_REL else None


def test_a_completed_render_becomes_the_baseline_and_a_failed_one_never_does(tmp_path):
    first = rr._run(tmp_path, _plan(tmp_path), rr.FakeFFmpeg())
    assert first["state"] == "completed"
    assert json.loads(_last_good(tmp_path).read_text()) == first["manifest"]
    assert "reuse" not in first["manifest"] or first["manifest"]["reuse"] is None  # a first render has no baseline
    good_bytes = _last_good(tmp_path).read_bytes()
    final = tmp_path / "episode_final.mp4"
    final.write_bytes(b"the final the baseline made")

    failed = rr._run(tmp_path, _plan(tmp_path, variant_shot="sh02"), rr.FakeFFmpeg(behave=_fail_at_f))
    assert failed["state"] == "failed" and failed["failed_stage"] == "F"
    assert _last_good(tmp_path).read_bytes() == good_bytes
    assert final.read_bytes() == b"the final the baseline made"
    # the failed run said what it was about to rebuild, against the last good render
    assert failed["manifest"]["reuse"]["shots_rebuilt"] == ["sh02"]
    assert failed["manifest"]["reuse"]["baseline_output_sha256"] == first["output"]["sha256"]

    again = rr._run(tmp_path, _plan(tmp_path, variant_shot="sh02"), rr.FakeFFmpeg())
    assert again["state"] == "completed"
    # the failed run made sh02's clip: this run takes it from the cache, recorded by the failed manifest
    assert again["manifest"]["reuse"]["shots_rebuilt"] == [] and again["cached"][:3] == ["S:sh01", "S:sh02",
                                                                                          "S:sh03"]
    assert json.loads(_last_good(tmp_path).read_text()) == again["manifest"]


def test_a_render_that_cannot_be_read_back_leaves_the_old_final_and_the_baseline(tmp_path, monkeypatch):
    rr._run(tmp_path, _plan(tmp_path), rr.FakeFFmpeg())
    final = tmp_path / "episode_final.mp4"
    final.write_bytes(b"the final the baseline made")
    good_bytes = _last_good(tmp_path).read_bytes()
    monkeypatch.setattr(rr, "PROBE", {"streams": [{"codec_type": "audio"}], "format": {"duration": "4.76"}})

    result = rr._run(tmp_path, _plan(tmp_path, variant_shot="sh01"), rr.FakeFFmpeg())

    assert result["state"] == "failed" and "no video stream" in result["error"]
    assert final.read_bytes() == b"the final the baseline made"
    assert _last_good(tmp_path).read_bytes() == good_bytes
    assert result["manifest"]["output"] is None


def test_a_corrupt_clip_is_rebuilt_and_recorded_as_corrupt(tmp_path):
    plan, _baseline_doc = _baseline(tmp_path)
    path = _cache(tmp_path) / f"{_key(plan, 'S:sh01')}.mp4"
    path.write_bytes(b"Y" * path.stat().st_size)
    fake = rr.FakeFFmpeg()

    result = rr._run(tmp_path, plan, fake)

    assert result["state"] == "completed"
    assert [sid for sid in result["ran"] if sid.startswith("S:")] == ["S:sh01"]
    assert result["cached"] == ["S:sh02", "S:sh03", "E"]
    record = result["manifest"]["reuse"]
    assert record["shots_rebuilt"] == ["sh01"] and record["reasons"] == {"sh01": "corrupt"}
    assert path.read_bytes() != b"Y" * len(path.read_bytes())


def test_a_missing_clip_is_rebuilt_and_recorded_as_missing(tmp_path):
    plan, _baseline_doc = _baseline(tmp_path)
    (_cache(tmp_path) / f"{_key(plan, 'S:sh03')}.mp4").unlink()
    result = rr._run(tmp_path, plan, rr.FakeFFmpeg())
    assert [sid for sid in result["ran"] if sid.startswith("S:")] == ["S:sh03"]
    assert result["manifest"]["reuse"]["reasons"] == {"sh03": "missing"}


def test_a_cached_file_no_manifest_records_is_rendered_again(tmp_path):
    plan, _baseline_doc = _baseline(tmp_path)
    for name in ("render_manifest.json", "render_manifest.last_good.json"):
        (tmp_path / name).unlink()
    fake = rr.FakeFFmpeg()
    result = rr._run(tmp_path, plan, fake)
    assert result["cached"] == [] and result["ran"] == rr._stage_ids(plan)
    assert result["manifest"].get("reuse") is None  # no baseline to compare with


def test_the_reuse_record_is_on_disk_before_the_first_process_and_true_at_the_end(tmp_path):
    _baseline(tmp_path)
    manifest_path = tmp_path / "render_manifest.json"
    seen = []

    def on_start(argv, cwd):
        doc = json.loads(manifest_path.read_text())
        assert schemas.render_manifest_errors(doc) == []
        seen.append(doc["reuse"])

    plan = _plan(tmp_path, variant_shot="sh03")
    result = rr._run(tmp_path, plan, rr.FakeFFmpeg(on_start=on_start))
    record = result["manifest"]["reuse"]
    assert seen[0] == record
    assert record == {"baseline_output_sha256": record["baseline_output_sha256"], "shots_total": 3,
                      "shots_rebuilt": ["sh03"], "shots_reused": ["sh01", "sh02"], "reasons": {"sh03": "image"},
                      "timing_converted": False}
    states = {stage["id"]: stage["state"] for stage in result["manifest"]["stages"]}
    assert [sid for sid in GOLDEN_SHOTS if states[f"S:{sid}"] == "done"] == record["shots_rebuilt"]
    assert schemas.render_manifest_errors(json.loads(_last_good(tmp_path).read_text())) == []


def test_an_edit_undone_takes_its_clip_back_from_the_cache(tmp_path):
    """The cache keeps the last two renders' clips; the manifest carries the
    sha256 of each (``cache``), so the clip only the replaced manifest named
    is still proven -- never taken on its size alone."""
    plan_a = _plan(tmp_path)
    rr._run(tmp_path, plan_a, rr.FakeFFmpeg())
    plan_b = _plan(tmp_path, variant_shot="sh02")
    second = rr._run(tmp_path, plan_b, rr.FakeFFmpeg())
    kept = second["manifest"]["cache"]
    assert set(kept) == {stage["cache_key"] for stage in plan_a["stages"] + plan_b["stages"] if stage["cache_key"]}
    assert all(shas == [_sha(_cache(tmp_path) / f"{key}.mp4")] for key, shas in kept.items())
    assert json.loads(_last_good(tmp_path).read_text())["cache"] == kept

    third = rr._run(tmp_path, _plan(tmp_path), rr.FakeFFmpeg())  # sh02's image as it was

    assert third["cached"] == ["S:sh01", "S:sh02", "S:sh03", "E"]
    record = third["manifest"]["reuse"]
    assert record["shots_rebuilt"] == [] and record["shots_reused"] == GOLDEN_SHOTS
    # a clip whose recorded sha256 no longer matches is not proven by the map either
    path = _cache(tmp_path) / f"{_key(plan_b, 'S:sh02')}.mp4"
    path.write_bytes(b"Z" * path.stat().st_size)
    fourth = rr._run(tmp_path, plan_b, rr.FakeFFmpeg())
    assert [sid for sid in fourth["ran"] if sid.startswith("S:")] == ["S:sh02"]
    assert fourth["manifest"]["reuse"]["reasons"] == {"sh02": "image"}


def test_a_symlinked_baseline_is_refused_never_followed(tmp_path):
    (tmp_path / "elsewhere.json").write_text("{}")
    os.symlink(tmp_path / "elsewhere.json", _last_good(tmp_path))
    with pytest.raises(runner.RunnerError, match="symlink"):
        rr._run(tmp_path, _plan(tmp_path), rr.FakeFFmpeg())
    assert (tmp_path / "elsewhere.json").read_text() == "{}"


def test_an_old_manifest_still_validates_and_a_false_reuse_record_does_not(tmp_path):
    _plan0, doc = _baseline(tmp_path)
    old = {k: v for k, v in doc.items() if k not in ("reuse", "whole_frames")}
    assert schemas.render_manifest_errors(old) == []
    assert schemas.render_manifest_errors(dict(old, reuse=None)) == []
    record = {"baseline_output_sha256": "a" * 64, "shots_total": 3, "shots_rebuilt": ["sh01"],
              "shots_reused": ["sh02", "sh03"], "reasons": {"sh01": "image"}, "timing_converted": False}
    # the stages all ran (done): a record saying two were reused is false once the output is recorded
    assert any("reuse" in e for e in schemas.render_manifest_errors(dict(old, reuse=record)))
    truthful = copy.deepcopy(old)
    for stage in truthful["stages"]:
        if stage["id"] in ("S:sh02", "S:sh03"):
            stage.update(state="cached", seconds=None)
    assert schemas.render_manifest_errors(dict(truthful, reuse=record)) == []
    assert schemas.render_manifest_errors(dict(truthful, cache={"a" * 64: ["b" * 64]})) == []
    for cache in ({"a" * 64: []}, {"not a key": ["b" * 64]}, {"a" * 64: ["not a sha"]}, {"a" * 64: "b" * 64}):
        assert schemas.render_manifest_errors(dict(truthful, cache=cache)), cache
    for bad in (dict(record, reasons={"sh01": "because"}), dict(record, reasons={}),
                dict(record, shots_total=4), dict(record, shots_reused=["sh01", "sh03"]),
                dict(record, reasons={"sh01": "image", "sh02": "frames"}),
                {k: v for k, v in record.items() if k != "timing_converted"}):
        assert schemas.render_manifest_errors(dict(truthful, reuse=bad)), bad


# ====================================================== 3. the selection table

def _fonts(tmp_path) -> Path:
    return tmp_path / "no_custom_fonts"


def rerender(store, story_id, *, fake=None, ctx=None, params=None, tmp_path=None):
    """The ``rerender`` step with the render step's fakes; ``(summary, log, fake)``."""
    fake = fake or rr.FakeFFmpeg()
    if ctx is None:
        ctx, log = trs.ctx_for(store, story_id, params=params, step="rerender")
    else:
        log = ctx.on_log
    fonts_dir = (tmp_path or Path(store.outputs_dir).parent) / "no_custom_fonts"
    summary = _rerender_mod().run(ctx, run_process=rr._fake_run(), popen=fake, clock=fake.clock,
                                  custom_fonts_dir=fonts_dir)
    return summary, log, fake


def rerender_refused(store, story_id, **kwargs):
    fake = rr.FakeFFmpeg()
    with pytest.raises(eps.steps.StepFailed) as caught:
        rerender(store, story_id, fake=fake, **kwargs)
    return str(caught.value), fake


def _rendered(store, tmp_path, built):
    """The render step's episode, rendered once (the baseline)."""
    story_id = trs.episode(store, tmp_path, built)
    summary, _log, _fake = trs.render(store, story_id, tmp_path=tmp_path)
    assert summary["state"] == "completed" and summary["reuse"] is None
    return story_id


def _ec(store, story_id):
    return trs._ec(store, story_id)


def _dry_run(store, story_id, tmp_path, params=None):
    return _render_mod().render_changes(_ec(store, story_id), params, custom_fonts_dir=_fonts(tmp_path))


def _edit_text_only(store, story_id):
    wf = _wf()
    wf.patch_script(store, story_id, 1, {"lines": [{"line_id": "l08", "text": tre.NEW_WORDS}]}, now=LATER)
    tre._check_and_approve_script(store, story_id)
    tre._revoice(store, story_id, "l08")
    wf.approve_assets(store, story_id, 1, now=LATEST)
    return "frames"


def _edit_delivery(store, story_id):
    _wf().patch_script(store, story_id, 1, {"lines": [{"line_id": "l08", "delivery": "froid"}]}, now=LATER)
    tre._check_and_approve_script(store, story_id)
    return None


def _edit_revoice(store, story_id):
    tre._revoice(store, story_id, "l08", note="plus fort")
    _wf().approve_assets(store, story_id, 1, now=LATEST)
    return None


def _edit_image(store, story_id):
    tas._regenerate_shot(store, story_id, "sh05", note="plus sombre", adapters=tas._adapters(image=tas.FakeImage()))
    _wf().approve_assets(store, story_id, 1, now=LATEST)
    return "image"


def _storyboard_edit(store, story_id, item):
    wf = _wf()
    wf.patch_storyboard(store, story_id, 1, {"shots": [item]}, now=LATER)
    wf.approve_storyboard(store, story_id, 1, now=LATEST)


def _edit_motion(store, story_id):
    _storyboard_edit(store, story_id, {"shot_id": "sh05", "camera_motion": "pan_rl"})
    return "motion"


def _edit_modifiers(store, story_id):
    """The fixture's style allows no modifier (the PATCH refuses one), so the
    storyboard is written as a style allowing ``handheld`` would leave it."""
    board = tre._board(store, story_id)
    tre._shot(board, "sh05")["modifiers"] = ["handheld"]
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=LATER)
    return "modifiers"


def _edit_keep_still(store, story_id):
    _storyboard_edit(store, story_id, {"shot_id": "sh05", "keep_still": True})
    return None


def _edit_framing(store, story_id):
    _storyboard_edit(store, story_id, {"shot_id": "sh04", "framing": "low_angle"})
    tas._regenerate_shot(store, story_id, "sh04", adapters=tas._adapters(image=tas.FakeImage()))
    _wf().approve_assets(store, story_id, 1, now=LATEST)
    return "image"


def _moving_transition(store, story_id):
    """The first scene boundary and transition type (stage 7's sweep) whose
    re-time moves a shot of the scene it leaves."""
    board, script = tre._board(store, story_id), tre._script(store, story_id)
    lock = store.read_doc(story_id, "style_lock.json")
    template = templates.load_episode_template(script["template_id"])
    scene_of = {shot["shot_id"]: shot["scene_id"] for shot in board["shots"]}
    order = [shot["shot_id"] for shot in board["shots"]]
    base = tre._pure_keys(script, board, lock, template)
    for transition in board["transitions"]:
        after = transition["after"]
        if scene_of[after] == scene_of[order[order.index(after) + 1]]:
            continue
        for kind in schemas.TRANSITIONS:
            if kind == transition["type"]:
                continue
            edited = copy.deepcopy(board)
            next(t for t in edited["transitions"] if t["after"] == after).update(
                type=kind, duration_s=template["transitions_s"][kind])
            shots.retime_storyboard(edited, script, template=template, language="fr", style_lock=lock)
            if tre._changed(base, tre._pure_keys(script, edited, lock, template)):
                return after, kind
    raise AssertionError("no transition of the fixture moves a shot")


def _edit_transition(store, story_id):
    after, kind = _moving_transition(store, story_id)
    wf = _wf()
    wf.patch_storyboard(store, story_id, 1, {"transitions": [{"after": after, "type": kind}]}, now=LATER)
    wf.approve_storyboard(store, story_id, 1, now=LATEST)
    return "frames"


# Stage 7's edit table, one row each: the edit and the shots its measured row names.
EDITS = [
    (_edit_text_only, ["sh04"]),
    (_edit_delivery, []),
    (_edit_revoice, []),
    (_edit_image, ["sh05"]),
    (_edit_motion, ["sh05"]),
    (_edit_modifiers, ["sh05"]),
    (_edit_keep_still, []),
    (_edit_framing, ["sh04"]),
    (_edit_transition, None),  # the scene the transition leaves, by whole frames
]


@pytest.mark.parametrize("edit, measured", EDITS, ids=[edit.__name__[len("_edit_"):] for edit, _m in EDITS])
def test_the_selection_table_rebuilds_the_shots_whose_render_key_moved(store, tmp_path, built, edit, measured):
    story_id = _rendered(store, tmp_path, built)
    board = tre._board(store, story_id)
    scene_of = {shot["shot_id"]: shot["scene_id"] for shot in board["shots"]}
    keys_before = tre._render_keys(store, story_id)

    reason = edit(store, story_id)

    changed = tre._changed(keys_before, tre._render_keys(store, story_id))
    if measured is not None:
        assert changed == measured
    else:
        assert changed and len({scene_of[sid] for sid in changed}) == 1
    dry = _dry_run(store, story_id, tmp_path)
    summary, log, _fake = rerender(store, story_id, tmp_path=tmp_path)

    record = summary["reuse"]
    assert record["shots_rebuilt"] == changed == dry["rebuild"]
    assert record["reasons"] == {sid: reason for sid in changed} == dry["reasons"]
    assert record["shots_reused"] == [sid for sid in scene_of if sid not in changed] == dry["reuse"]
    assert record["shots_total"] == len(scene_of) and record["timing_converted"] is False
    assert [sid for sid in summary["ran"] if sid.startswith("S:")] == [f"S:{sid}" for sid in changed]
    assert summary["ran"][-len(trs.TAIL_STAGES):] == trs.TAIL_STAGES
    sentence = f"{len(changed)} of {len(scene_of)} shots re-rendered"
    assert dry["summary"] == sentence and any(sentence in line for line in log)
    assert _dry_run(store, story_id, tmp_path)["rebuild"] == []  # and now nothing is left to make


def test_a_subtitles_toggle_rebuilds_no_shot(store, tmp_path, built):
    story_id = _rendered(store, tmp_path, built)
    dry = _dry_run(store, story_id, tmp_path, {"subtitles": "none"})
    assert dry["rebuild"] == [] and dry["params"]["subtitles"] == "none"
    summary, _log, fake = trs.render(store, story_id, tmp_path=tmp_path, params={"subtitles": "none"})
    record = summary["reuse"]
    assert record["shots_rebuilt"] == [] and record["reasons"] == {}
    assert summary["ran"] == trs.TAIL_STAGES and len(fake.calls) == len(trs.TAIL_STAGES)


def test_the_dry_run_of_an_episode_never_rendered_makes_everything_new(store, tmp_path, built):
    story_id = trs.episode(store, tmp_path, built)
    dry = _dry_run(store, story_id, tmp_path)
    total = len(tre._board(store, story_id)["shots"])
    assert dry["baseline"] is None and dry["current"] is False and dry["summary"] is None
    assert len(dry["rebuild"]) + len(dry["reuse"]) == dry["shots_total"] == total
    assert set(dry["reasons"].values()) == {"new"} and dry["end_card"] in (None, "rebuild")
    assert all(stage["change"] == "new" and stage["runs"] for stage in dry["stages"] if stage["kind"] != "shot"
               or stage["id"][2:] in dry["rebuild"])
    assert not (trs.ep_dir(store, story_id) / "render").exists()  # a dry run makes nothing


def test_the_dry_run_diffs_every_stage_and_input_against_the_last_good_render(store, tmp_path, built):
    story_id = _rendered(store, tmp_path, built)
    unchanged = _dry_run(store, story_id, tmp_path)
    assert unchanged["current"] is True and unchanged["rebuild"] == [] and unchanged["inputs"] == []
    assert all(stage["change"] is None for stage in unchanged["stages"])
    assert unchanged["baseline"]["output_sha256"] == trs._sha(trs.ep_dir(store, story_id) / "episode_final.mp4")

    _edit_image(store, story_id)
    dry = _dry_run(store, story_id, tmp_path)
    assert dry["current"] is False and dry["rebuild"] == ["sh05"]
    assert dry["inputs"] == [{"role": "shot", "id": "sh05", "change": "changed"}]
    by_id = {stage["id"]: stage for stage in dry["stages"]}
    assert by_id["S:sh05"] == {"id": "S:sh05", "kind": "shot", "change": "image", "runs": True}
    assert by_id["S:sh04"] == {"id": "S:sh04", "kind": "shot", "change": None, "runs": False}
    assert by_id["F"]["change"] == "command" and by_id["F"]["runs"] is True  # it reads sh05's new clip
    assert by_id["A"] == {"id": "A", "kind": "audio_mix", "change": None, "runs": True}


# ================================================================== 4. the step

def test_rerender_is_registered_ends_completed_and_left_the_later_steps():
    from clipping.aistory import steps

    wf = _wf()
    assert steps.RUNNERS["rerender"].__name__ == "run_rerender"
    assert "rerender" in steps.COMPLETED_STEPS and steps.ends_completed("rerender", {}) is True
    assert wf.LATER_STEPS == ("import",) and "rerender" in wf.EPISODE_STEPS


def test_rerender_needs_a_completed_render(store, tmp_path, built):
    story_id = trs.episode(store, tmp_path, built)
    message, fake = rerender_refused(store, story_id, tmp_path=tmp_path)
    assert message == ("Episode 1 has no finished render to re-render: render it first (the render step); "
                       "a re-render makes again only what changed since the last good render.")
    assert fake.calls == []

    # a render that failed is not one either
    with pytest.raises(eps.steps.StepFailed):
        trs.render(store, story_id, fake=rr.FakeFFmpeg(behave=_fail_at_f), tmp_path=tmp_path)
    message, fake = rerender_refused(store, story_id, tmp_path=tmp_path)
    assert message.startswith("Episode 1 has no finished render to re-render") and fake.calls == []


def test_rerender_needs_the_final_file_its_last_render_made(store, tmp_path, built):
    story_id = _rendered(store, tmp_path, built)
    (trs.ep_dir(store, story_id) / "episode_final.mp4").write_bytes(b"not the render")
    message, fake = rerender_refused(store, story_id, tmp_path=tmp_path)
    assert message.startswith("Episode 1's episode_final.mp4 is not the one its last good render made")
    assert fake.calls == []


def test_rerender_needs_the_assets_approved_and_current(store, tmp_path, built):
    story_id = _rendered(store, tmp_path, built)
    trs._unapprove(store, story_id)
    message, fake = rerender_refused(store, story_id, tmp_path=tmp_path)
    assert message.startswith("Approve episode 1's assets first") and fake.calls == []


def test_rerender_refuses_an_outdated_shot_image_naming_it(store, tmp_path, built):
    story_id = _rendered(store, tmp_path, built)
    _storyboard_edit(store, story_id, {"shot_id": "sh04", "framing": "low_angle"})
    message, fake = rerender_refused(store, story_id, tmp_path=tmp_path)
    assert "sh04" in message and "out of date" in message and "shot:1:sh04" in message
    assert fake.calls == []


def test_rerender_takes_no_parameters(store, tmp_path, built):
    story_id = _rendered(store, tmp_path, built)
    message, _fake = rerender_refused(store, story_id, tmp_path=tmp_path, params={"subtitles": "none"})
    assert message == ("A re-render keeps the last render's subtitles and encoder and takes no parameters "
                       "(got subtitles); to change them, render the episode (the render step).")


def test_a_rerender_reports_what_it_rebuilt_in_the_feed_the_summary_and_the_episode_page(store, tmp_path, built):
    wf = _wf()
    story_id = _rendered(store, tmp_path, built)
    total = len(tre._board(store, story_id)["shots"])
    before = store.read_episode_doc(story_id, 1, "render_manifest.json")
    _edit_image(store, story_id)

    summary, log, _fake = rerender(store, story_id, tmp_path=tmp_path)

    sentence = f"1 of {total} shots re-rendered"
    assert summary["state"] == "completed" and summary["step"] == "rerender"
    assert summary["reuse"]["summary"] == sentence
    assert summary["reuse"]["baseline_output_sha256"] == before["output"]["sha256"]
    assert summary["params"] == before["params"]
    assert any(line.startswith(f"✅ Episode 1 re-rendered: {sentence} · {total - 1} reused") for line in log)
    render = wf.episode_outputs(store, store.get(story_id), 1)["render"]
    assert render["reuse"] == dict(store.read_episode_doc(story_id, 1, "render_manifest.json")["reuse"],
                                   summary=sentence)
    last_good = store.read_episode_doc(story_id, 1, "render_manifest.last_good.json")
    assert last_good == store.read_episode_doc(story_id, 1, "render_manifest.json")


def test_a_first_render_has_no_reuse_record_on_the_episode_page(store, tmp_path, built):
    story_id = _rendered(store, tmp_path, built)
    assert _wf().episode_outputs(store, store.get(story_id), 1)["render"]["reuse"] is None


class _Tagged(rr.FakeFFmpeg):
    """The fake ffmpeg, its final file tagged per render (a real re-render
    makes other bytes)."""

    def __init__(self, tag, **kwargs):
        super().__init__(**kwargs)
        self.tag = tag

    def __call__(self, argv, *, cwd, stdin, stdout, stderr):
        proc = super().__call__(argv, cwd=cwd, stdin=stdin, stdout=stdout, stderr=stderr)
        if plan_mod.FINAL_REL in rr._outputs_of(argv):
            with open(os.path.join(cwd, plan_mod.FINAL_REL), "ab") as handle:
                handle.write(self.tag)
        return proc


def test_a_rerender_leaves_the_metadata_pack_stale_and_unwritten(store, tmp_path, built):
    story_id = trs.episode(store, tmp_path, built)
    trs.render(store, story_id, fake=_Tagged(b"first"), tmp_path=tmp_path)
    tms.run_step(store, story_id, tmp_path=tmp_path)
    pack_path = trs.ep_dir(store, story_id) / "metadata_pack.json"
    pack_bytes = pack_path.read_bytes()
    wf = _wf()
    assert wf.episode_outputs(store, store.get(story_id), 1)["metadata"]["current"] is True
    _edit_image(store, story_id)

    rerender(store, story_id, fake=_Tagged(b"second"), tmp_path=tmp_path)

    assert pack_path.read_bytes() == pack_bytes
    assert wf.episode_outputs(store, store.get(story_id), 1)["metadata"]["current"] is False


def test_after_a_storyboard_converts_to_whole_frames_the_rerender_says_so(store, tmp_path, built):
    """Stage 6: an old storyboard converts on its first full re-time; the
    first re-render after it is not partial, and says why."""
    story_id = trs.episode(store, tmp_path, built)
    tre._legacy(store, story_id)
    summary, _log, _fake = trs.render(store, story_id, tmp_path=tmp_path)
    assert summary["state"] == "completed"
    assert store.read_episode_doc(story_id, 1, "render_manifest.json")["whole_frames"] is False
    keys_before = tre._render_keys(store, story_id)

    _edit_text_only(store, story_id)

    assert tre._board(store, story_id)["whole_frames"] is True
    changed = tre._changed(keys_before, tre._render_keys(store, story_id))
    summary, log, _fake = rerender(store, story_id, tmp_path=tmp_path)
    record = summary["reuse"]
    total = len(tre._board(store, story_id)["shots"])
    assert record["timing_converted"] is True and record["shots_rebuilt"] == changed
    assert len(changed) > 1 and set(record["reasons"].values()) == {"frames"}
    assert summary["reuse"]["summary"] == (f"{len(changed)} of {total} shots re-rendered "
                                           "(the shot timing moved to whole frames)")
    assert store.read_episode_doc(story_id, 1, "render_manifest.json")["whole_frames"] is True
    # and the next edit is partial again
    _edit_image(store, story_id)
    summary, _log, _fake = rerender(store, story_id, tmp_path=tmp_path)
    assert summary["reuse"]["shots_rebuilt"] == ["sh05"] and summary["reuse"]["timing_converted"] is False


def test_a_rerender_that_fails_keeps_the_baseline_and_the_final(store, tmp_path, built):
    story_id = trs.episode(store, tmp_path, built)
    trs.render(store, story_id, fake=_Tagged(b"first"), tmp_path=tmp_path)
    folder = trs.ep_dir(store, story_id)
    final_bytes = (folder / "episode_final.mp4").read_bytes()
    good_bytes = (folder / "render_manifest.last_good.json").read_bytes()
    _edit_image(store, story_id)

    with pytest.raises(eps.steps.StepFailed) as caught:
        rerender(store, story_id, fake=rr.FakeFFmpeg(behave=_fail_at_f), tmp_path=tmp_path)

    assert str(caught.value).startswith("Episode 1's re-render failed at stage F (final)")
    assert (folder / "episode_final.mp4").read_bytes() == final_bytes
    assert (folder / "render_manifest.last_good.json").read_bytes() == good_bytes
    # the Preview still has the last good render to play and compare with
    assert _dry_run(store, story_id, tmp_path)["rebuild"] == []  # sh05's clip was made before F failed
    assert _render_mod().render_changes(_ec(store, story_id), custom_fonts_dir=_fonts(tmp_path))["baseline"][
        "output_sha256"] == trs._sha(folder / "episode_final.mp4")


# ============================================ 5. partial == full (real ffmpeg)

def _require_ffmpeg():
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        pytest.fail(f"{' and '.join(missing)} not found on PATH. Partial == full never skips (RC-M8, like the "
                    "golden render, DEC-156): install ffmpeg (CI installs it: apt-get install ffmpeg).")


def _completed(result):
    if result["state"] != "completed":
        manifest = result.get("manifest") or {}
        tail = next((s["stderr_tail"] for s in reversed(manifest.get("stages", [])) if s.get("stderr_tail")), "")
        pytest.fail(f"the render did not complete: {result['error']}\n{tail}")
    return result


def test_a_partial_re_render_equals_a_full_render_of_the_same_documents(tmp_path):
    """RC-M8. The golden fixture rendered once (the warm cache and the
    baseline), then l02 made longer (1.0 s instead of 0.8 s: s02, so sh03,
    lasts longer) and sh01's image changed: the partial re-render over the
    warm cache makes only sh01 and sh03 again, and its frames are exactly
    those of a clean full render of the same documents."""
    _require_ffmpeg()
    edit = {"variant_shot": "sh01", "line_seconds": {"l02": 1.0}}
    warm = tmp_path / "warm"
    base = _completed(golden.render_fixture(warm))
    partial = _completed(golden.render_fixture(warm, **edit))
    full = _completed(golden.render_fixture(tmp_path / "clean", **edit))

    assert [sid for sid in partial["ran"] if sid.startswith("S:")] == ["S:sh01", "S:sh03"]
    assert partial["cached"] == ["S:sh02", "E"]
    assert full["cached"] == [] and [sid for sid in full["ran"] if sid[:2] in ("S:", "E")] == [
        "S:sh01", "S:sh02", "S:sh03", "E"]
    record = partial["manifest"]["reuse"]
    assert record == {"baseline_output_sha256": base["output"]["sha256"], "shots_total": 3,
                      "shots_rebuilt": ["sh01", "sh03"], "shots_reused": ["sh02"],
                      "reasons": {"sh01": "image", "sh03": "frames"}, "timing_converted": False}

    assert partial["output"]["framemd5"]["sha256"] == full["output"]["framemd5"]["sha256"]
    assert partial["output"]["framemd5"]["sha256"] != base["output"]["framemd5"]["sha256"]
    assert partial["output"]["duration_s"] == full["output"]["duration_s"] > base["output"]["duration_s"]
    # the reused clip is the very file the baseline made, checked by its recorded sha256
    reused = next(s for s in partial["manifest"]["stages"] if s["id"] == "S:sh02")
    made = next(s for s in base["manifest"]["stages"] if s["id"] == "S:sh02")
    assert reused["state"] == "cached" and reused["output_sha256"] == made["output_sha256"]
    last_good = json.loads((warm / "render_manifest.last_good.json").read_text())
    assert last_good == partial["manifest"]
