"""The render step at tier >= 2 (AI Story phase 6, stage 9; spec 6.5, 8.1;
DEC-156, DEC-158).

At tier 2 or 3 the render reads the clips the assets step's video phase made
(stage 8): a shot whose clip is current, and which is not kept still by its
**effective** flags (``assets.json``'s ``shots`` overrides win over the
storyboard's own), is cut from its clip (``filtergraph.tier2_clip_argv``);
a shot kept still keeps its Tier-1 motion. Plan 33 stage 4: every other
shot is a video clip -- one never planned (no clip record), or whose clip
failed, went stale or is still generating, refuses the render before any
process, naming it and its regenerate target; nothing fills it with motion
(``fill_failed_with_motion`` is refused). The manifest's
``shot_modes`` says which shot got what -- written only when some shot is
not plain motion, so a tier-1 render is the parent commit's, byte for byte
(RC-M3).

The episode is the render step's own (``tests/test_story_render_step.py``),
its clips made by stage 8's fake video adapter
(``tests/test_story_video_phase.py``); every process is stage 7's fake
ffmpeg. The real ffmpeg's side -- the hold of a short clip's last frame, the
tier-2 golden, partial == full -- is ``tests/test_aistory_render_golden_tier2.py``.

The new behaviour is reached inside the tests, so on the parent commit each
test fails on its own. Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import hashlib
import json
import os

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_render_step as trs
import test_story_video_phase as tvp
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_render_step import built  # noqa: F401 -- the render step's session copy of the episode
from test_story_video_phase import timings_path  # noqa: F401 -- the measured clip timings under tmp_path

# A tier-1 render of the render step's episode -- the plan's inputs, the plan
# and the manifest, absolute paths, timestamps and the machine masked --
# computed on the parent commit (3f2523a) with this file's fixture before any
# stage-9 line existed: RC-M3's guard.
# TIER1_PLAN_SHA and TIER1_MANIFEST_SHA re-pinned on purpose (phase 7
# follow-up, a bug fix for every story): each dialogue line of the mix is
# faded at its own file's edges (filtergraph.LINE_FADE_IN_S/LINE_FADE_OUT_S),
# which moves the audio stage's argv and its cache key. With the fades taken
# out of the mix argv the plan and the manifest still hash to the old values
# 386a0330…c357 and 7af9d816…b20c, so nothing else moved.
TIER1_INPUTS_SHA = "769b976e4032d0a4a8f460f70e4284ba82970e2e6f667691d6123c2902e1c8a7"
TIER1_PLAN_SHA = "ca1cec8dc3a73f5c9d658155792a5dbf205cb4218e24709a2ab3c14869b519b3"
TIER1_MANIFEST_SHA = "d48dc052da0810b17ea22a9cd2e60a0a03e185848c5ff628bc0516fa7ed83c34"
_VOLATILE = {"created_at", "updated_at", "started_at", "finished_at", "total_s", "seconds", "machine"}


def _mask(value):
    if isinstance(value, dict):
        return {key: ("<v>" if key in _VOLATILE and item is not None else _mask(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask(item) for item in value]
    if isinstance(value, str) and os.path.isabs(value):
        return "<abs>/" + os.path.basename(value)
    return value


def _masked_sha(value) -> str:
    return hashlib.sha256(json.dumps(_mask(value), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _spy(monkeypatch):
    """Every ``build_render_plan`` call's kwargs and plan, in order."""
    from clipping.aistory.render import plan as plan_mod

    seen = []
    real = plan_mod.build_render_plan

    def spy(**kwargs):
        plan = real(**kwargs)
        seen.append((kwargs, plan))
        return plan

    monkeypatch.setattr(plan_mod, "build_render_plan", spy)
    return seen


# ================================================================ guards

def test_guard_a_tier_1_render_plan_inputs_and_manifest_are_the_parent_commits(store, tmp_path, built, monkeypatch):
    """RC-M3: at tier 1 the render reads no clip -- no ``videos`` in its
    inputs, no ``shot_modes`` in its manifest, no new param -- and its
    inputs, plan and manifest are the parent commit's."""
    story_id = trs.episode(store, tmp_path, built)
    seen = _spy(monkeypatch)

    summary, _log, _fake = trs.render(store, story_id, tmp_path=tmp_path)

    assert summary["state"] == "completed" and len(seen) == 1
    kwargs, plan = seen[0]
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    assert "videos" not in kwargs["inputs"] and "shot_modes" not in manifest
    assert (_masked_sha(kwargs["inputs"]), _masked_sha(plan), _masked_sha(manifest)) == (
        TIER1_INPUTS_SHA, TIER1_PLAN_SHA, TIER1_MANIFEST_SHA)


# ============================================================ the episode

def _animated(store, tmp_path, *, video=None, name_of=None, still_unplanned=False):
    """A tier-2 episode whose planned clips stage 8's video phase made
    (``tests/test_story_video_phase.py``'s fakes), its assets approved:
    ``(story_id, planned shot ids)``. *video(planned)* builds the fake video
    adapter from the planned shot ids (default: every clip answers).
    *still_unplanned*: the shots the plan leaves out pinned ``keep_still``
    first (``tvp.keep_unplanned_still``; plan 33 stage 4: else the render
    refuses them, every shot being a video clip)."""
    settings = tce._settings(**tvp.PAID)
    story_id = tvp._keyframes(store, tmp_path, settings=settings)
    planned = tvp.planned_ids(store, story_id, settings)
    if still_unplanned:
        tvp.keep_unplanned_still(store, story_id, planned)
        assert tvp.planned_ids(store, story_id, settings) == planned
    adapter = video(planned) if video is not None else tvp.FakeVideo()
    tas._run(store, story_id, adapters=tvp._adapters(adapter), settings=settings)
    trs.approve_assets(store, story_id)
    return story_id, planned


def _clip_name(shot_id) -> str:
    return f"shot_{shot_id[2:]}"


def _stage(manifest, shot_id):
    return next(stage for stage in manifest["stages"] if stage["id"] == f"S:{shot_id}")


# ============================================================ fail-first

def test_a_current_clip_is_cut_from_its_video_unless_an_override_keeps_its_shot_still(store, tmp_path, monkeypatch):
    """Every planned shot's clip is current. The render cuts each from its
    clip (``tier2_clip_argv``: the clip staged, no looped still), except the
    one ``assets.json`` now keeps still -- its override wins over the
    storyboard's own flag (false) -- which keeps its Tier-1 motion. Plan 33
    stage 4, re-pinned on purpose: a shot the plan left without a clip no
    longer keeps plain motion -- it refuses the render, naming its
    regenerate target -- so the episode is rendered with those shots kept
    still too (DEC-236's one exemption). The manifest says which shot got
    what: ``video`` or ``motion_keep_still``, never plain ``motion``."""
    from clipping.aistory.steps import clips

    story_id, planned = _animated(store, tmp_path)
    assert len(planned) > 1
    unplanned = [shot["shot_id"] for shot in tas._shots(store, story_id) if shot["shot_id"] not in planned]
    assert unplanned
    message, fake = trs.refused(store, story_id, tmp_path=tmp_path)
    assert fake.calls == [] and "Every shot is a video clip, never a still with camera motion" in message
    assert all(f"shot:1:{shot_id}:video" in message for shot_id in unplanned)
    assert "fill_failed_with_motion" not in message
    tvp.keep_unplanned_still(store, story_id, planned)
    kept, animated = planned[-1], planned[:-1]
    board_flag = next(shot for shot in tas._shots(store, story_id) if shot["shot_id"] == kept)["keep_still"]
    assert board_flag is False
    tce._patch(store, story_id, {"shot_id": kept, "keep_still": True})
    trs.approve_assets(store, story_id)
    seen = _spy(monkeypatch)

    summary, _log, _fake = trs.render(store, story_id, tmp_path=tmp_path)

    assert summary["state"] == "completed"
    shots = tas._shots(store, story_id)
    kwargs, _plan = seen[0]
    videos = kwargs["inputs"]["videos"]
    assert sorted(videos) == sorted(animated)
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    staged = {item["id"]: item for item in manifest["inputs"] if item["role"] == "shot"}
    doc = store.read_episode_doc(story_id, 1, "assets.json")
    expected = {}
    for shot in shots:
        shot_id = shot["shot_id"]
        argv = _stage(manifest, shot_id)["argv"]
        if shot_id in animated:
            assert shot["assets"]["clip"]["state"] == "current"
            assert staged[shot_id]["source"] == shot["assets"]["video"] == f"assets/clips/{_clip_name(shot_id)}.mp4"
            assert "-loop" not in argv and "tpad=stop_mode=clone" in argv[argv.index("-vf") + 1]
            assert argv[argv.index("-i") + 1] == staged[shot_id]["staged"].replace("\\", "/")
            expected[shot_id] = "video"
        else:
            assert staged[shot_id]["source"] == shot["assets"]["image"] and "-loop" in argv
            expected[shot_id] = "motion_keep_still" if clips.shot_flags(shot, doc)["keep_still"] else "motion"
    assert expected[kept] == "motion_keep_still" and "motion" not in expected.values()
    assert manifest["shot_modes"] == expected
    assert manifest["params"] == {"subtitles": "word_pop", "encoder": "libx264"}


def test_a_failed_clip_refuses_the_render_naming_its_target_and_nothing_fills_it_with_motion(store, tmp_path):
    """One planned clip failed (the provider settled it without a clip),
    another is still generating (its poll ran out); the shots the plan left
    out are kept still. The render refuses before any process: it names the
    failed shot with its regenerate target, the one still generating with
    Continue only. Plan 33 stage 4, re-pinned on purpose: no param fills them
    with motion any more -- ``fill_failed_with_motion`` sent true is refused
    with its own sentence, and nothing is rendered."""
    shot_ids = {}

    def video(planned):
        shot_ids["failed"], shot_ids["slow"] = planned[0], planned[1]
        return tvp.FakeVideo(fail_for={_clip_name(planned[0])}, slow_for={_clip_name(planned[1])})

    story_id, planned = _animated(store, tmp_path, video=video, still_unplanned=True)
    failed, slow = shot_ids["failed"], shot_ids["slow"]

    message, fake = trs.refused(store, story_id, tmp_path=tmp_path)

    assert fake.calls == []
    assert f"shot {failed}'s clip failed" in message and f"shot:1:{failed}:video" in message
    assert f"shot {slow}'s clip is still generating" in message and f"shot:1:{slow}:video" not in message
    assert "press Continue (the assets step resumes it; nothing is bought again)" in message
    assert "Every shot is a video clip, never a still with camera motion" in message
    assert "fill_failed_with_motion" not in message and "no clip" not in message

    message, fake = trs.refused(store, story_id, tmp_path=tmp_path, params={"fill_failed_with_motion": True})

    assert fake.calls == []
    assert message.startswith("fill_failed_with_motion is no longer a render option: every shot of an episode is a "
                              "video clip")
    assert "shot:<ep>:<shot_id>:video" in message
    assert store.read_episode_doc(story_id, 1, "render_manifest.json") is None
