"""A fully animated story never shows a still (the human, 2026-10-02: "only
fully animated episodes, no diaporama, even if it costs money").

On a story ``media_policy.fully_animated`` holds for (v2, tier >= 2, a budget
profile that animates every shot) the assets approval and the render refuse
while a shot the user did not pin ``keep_still`` has no current clip -- one
never made as well as one failed or stale -- and the render's
``fill_failed_with_motion`` cannot turn one into a still with a zoom. Plan 33
stage 4: the render's side of that rule now holds for every story at tier
>= 2 (``fill_failed_with_motion`` itself is refused); a story that is not
fully animated refuses with the render's own sentence
(``tests/test_story_render_clips.py``).

The episode is the render-clip tests' own: a tier-2 episode whose planner
animated only some shots (the ``one_dollar`` profile), with
``media_policy.fully_animated`` forced on after its assets were made, so the
gate is reached on the same files the phase-6 tests use. Stdlib + pytest
(DEC-012).
"""

from __future__ import annotations

import pytest

import test_story_assets_step as tas
import test_story_render_clips as trc
import test_story_render_step as trs
import test_story_video_phase as tvp
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_video_phase import timings_path  # noqa: F401 -- the measured clip timings under tmp_path

LATER = "2026-10-02T12:00:00+00:00"

QUALITY_V2 = {"tier": 2, "route": "api", "consistency_mode": "references", "budget_profile": "quality",
              "pipeline": "v2"}


def _fully_animated(monkeypatch):
    from clipping.aistory import media_policy

    monkeypatch.setattr(media_policy, "fully_animated", lambda story: True)


# ------------------------------------------------------------ the rule

@pytest.mark.parametrize(("profile", "expected"), [
    (QUALITY_V2, True),
    ({**QUALITY_V2, "tier": 3}, True),
    ({**QUALITY_V2, "tier": 1}, False),  # tier 1 is the user's choice of stills
    ({**QUALITY_V2, "budget_profile": "one_dollar"}, False),  # key shots only
    ({**QUALITY_V2, "budget_profile": "free"}, False),
    ({key: value for key, value in QUALITY_V2.items() if key != "pipeline"}, False),  # legacy: RC-M3
])
def test_only_a_v2_story_that_animates_every_shot_is_fully_animated(profile, expected):
    from clipping.aistory import media_policy

    assert media_policy.fully_animated({"generation_profile": profile}) is expected


# ------------------------------------------------------------ the gates

def test_the_render_refuses_a_shot_never_animated_and_no_fill_makes_it_a_still(store, tmp_path, monkeypatch):
    story_id, planned = trc._animated(store, tmp_path)
    unplanned = [shot["shot_id"] for shot in tas._shots(store, story_id) if shot["shot_id"] not in planned]
    assert unplanned, "the fixture's planner must leave some shot without a clip"
    _fully_animated(monkeypatch)

    message, fake = trs.refused(store, story_id, tmp_path=tmp_path)

    assert fake.calls == []
    assert "fully animated" in message and "no clip yet" in message
    for shot_id in unplanned:
        assert shot_id in message
    assert "fill_failed_with_motion" not in message  # Tier-1 motion is never offered

    # Plan 33 stage 4, re-pinned on purpose: the param itself is refused now, before any clip is read.
    message, fake = trs.refused(store, story_id, tmp_path=tmp_path, params={"fill_failed_with_motion": True})
    assert fake.calls == [] and message.startswith("fill_failed_with_motion is no longer a render option")


def test_a_failed_clip_cannot_be_filled_with_motion_on_a_fully_animated_story(store, tmp_path, monkeypatch):
    shot_ids = {}

    def video(planned):
        shot_ids["failed"] = planned[0]
        return tvp.FakeVideo(fail_for={trc._clip_name(planned[0])})

    story_id, planned = trc._animated(store, tmp_path, video=video)
    _fully_animated(monkeypatch)

    message, fake = trs.refused(store, story_id, tmp_path=tmp_path)

    assert fake.calls == []
    assert f"shot {shot_ids['failed']}'s clip failed" in message
    assert f"shot:1:{shot_ids['failed']}:video" in message
    # Plan 33 stage 4, re-pinned on purpose: the fill is refused as a param of its own, before any clip is read.
    message, fake = trs.refused(store, story_id, tmp_path=tmp_path, params={"fill_failed_with_motion": True})
    assert fake.calls == [] and message.startswith("fill_failed_with_motion is no longer a render option")


def test_a_shot_the_user_keeps_still_is_the_one_exemption(store, tmp_path, monkeypatch):
    import test_story_clip_estimate as tce

    story_id, planned = trc._animated(store, tmp_path)
    unplanned = [shot["shot_id"] for shot in tas._shots(store, story_id) if shot["shot_id"] not in planned]
    for shot_id in unplanned:
        tce._patch(store, story_id, {"shot_id": shot_id, "keep_still": True})
    trs.approve_assets(store, story_id)
    _fully_animated(monkeypatch)

    summary, _log, _fake = trs.render(store, story_id, tmp_path=tmp_path)

    assert summary["state"] == "completed"
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    assert {shot_id for shot_id, mode in manifest["shot_modes"].items() if mode != "video"} == set(unplanned)
    assert all(manifest["shot_modes"][shot_id] == "motion_keep_still" for shot_id in unplanned)


def test_the_assets_approval_refuses_until_every_shot_has_its_clip(store, tmp_path, monkeypatch):
    from clipping.aistory import workflow

    story_id, planned = trc._animated(store, tmp_path)
    unplanned = [shot["shot_id"] for shot in tas._shots(store, story_id) if shot["shot_id"] not in planned]
    _fully_animated(monkeypatch)

    with pytest.raises(workflow.WorkflowError) as info:
        workflow.approve_assets(store, story_id, 1, now=LATER)

    assert info.value.code == workflow.CONFLICT
    assert "cannot be approved" in info.value.detail and "fully animated" in info.value.detail
    for shot_id in unplanned:
        assert shot_id in info.value.detail


def test_a_story_that_is_not_fully_animated_refuses_its_unplanned_shots_too(store, tmp_path):
    """Plan 33 stage 4, re-pinned on purpose (phase 6 rendered them with
    Tier-1 motion): the same episode, ``fully_animated`` off (its real profile
    is not v2, its budget profile animates key shots only), refuses the
    render before any process, naming each unplanned shot's regenerate
    target -- every shot of every tier-2+ story is a video clip -- and
    offers no fill. Kept still, those shots are the one exemption, as on a
    fully animated story."""
    import test_story_clip_estimate as tce
    from clipping.aistory import media_policy

    story_id, planned = trc._animated(store, tmp_path)
    assert media_policy.fully_animated(store.get(story_id)) is False
    unplanned = [shot["shot_id"] for shot in tas._shots(store, story_id) if shot["shot_id"] not in planned]
    assert unplanned

    message, fake = trs.refused(store, story_id, tmp_path=tmp_path)

    assert fake.calls == [] and store.read_episode_doc(story_id, 1, "render_manifest.json") is None
    assert message.startswith("Episode 1 cannot be rendered: ")
    assert "Every shot is a video clip, never a still with camera motion" in message
    for shot_id in unplanned:
        assert f"shot:1:{shot_id}:video" in message
    assert "fill_failed_with_motion" not in message

    for shot_id in unplanned:
        tce._patch(store, story_id, {"shot_id": shot_id, "keep_still": True})
    trs.approve_assets(store, story_id)
    summary, _log, _fake = trs.render(store, story_id, tmp_path=tmp_path)

    assert summary["state"] == "completed"
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    assert "motion" not in manifest["shot_modes"].values()
    assert {shot_id for shot_id, mode in manifest["shot_modes"].items() if mode == "motion_keep_still"} == set(
        unplanned)
