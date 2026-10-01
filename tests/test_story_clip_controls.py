"""The clip controls stage 11 opens to the front ends (AI Story phase 6,
stage 11; A-087, DEC-152..154, DEC-204, DEC-209).

- The episode's video link is switched only by the user, through the assets
  edit ``{"links": {"video": "<link>"}}`` -- the ``switch`` the sticky video
  offer now carries: ``assets.json``'s ``links.video`` records it with the
  link it replaced, the clips another link made go stale (the next run
  animates exactly those again), and the storyboard -- its bytes, its
  approval -- never moves.
- The render's clip refusal is met before a job exists (``workflow.
  require_step_inputs`` / ``render_estimate``): a failed clip is named with
  its regenerate target; a re-animate whose request the provider holds is
  offered Continue only -- even when the process stopped before its answer
  was recorded (stage 9's noted flaw: it was offered its target, which would
  buy a second clip) -- and ``fill_failed_with_motion`` lets both through.
- The fast track's estimate counts the clips at tier 2, and its paid-check
  stop says how to do without them.

The story, the fakes and the fixtures are the assets step's, the video
phase's and the render's own (``tests/test_story_assets_step.py``,
``test_story_video_phase.py``, ``test_story_render_clips.py``). Nothing leaves
the process. Stdlib + pytest (DEC-012); the new behaviour is reached inside
the tests, so on the parent commit each test fails on its own.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_episode_steps as eps
import test_story_render_clips as trc
import test_story_render_step as trs
import test_story_video_phase as tvp
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_video_phase import timings_path  # noqa: F401 -- the measured clip timings under tmp_path

LATER = tce.LATER
SEEDANCE, KLING = tvp.SEEDANCE, tvp.KLING


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _board_file(store, story_id) -> Path:
    return Path(store.story_dir(story_id)) / "episodes" / "ep01" / "storyboard.json"


# ============================================================ the video link

def test_the_offers_switch_moves_the_video_link_staling_exactly_the_old_links_clips_and_keeps_the_storyboard(
        store, tmp_path):
    """Clips made on seedance, the episode's video link. The sticky video
    offer names kling and carries the assets edit that takes it; that edit
    (checked against VIDEO_CHAIN: another link is refused, nothing written)
    records ``links.video {kling, since, switched_from: seedance}``, every
    clip seedance made is stale on kling -- the shots without a clip stay
    without -- the assets approval goes stale and the storyboard's bytes
    (its approval) never move."""
    from clipping.aistory import workflow
    from clipping.aistory.steps import assets, clips

    settings = tas._settings(**tvp.PAID, **tvp.BOTH_VIDEO)
    story_id = tvp._keyframes(store, tmp_path, settings=settings)
    tas._run(store, story_id, adapters=tvp._adapters(tvp.FakeVideo()), settings=settings)
    trs.approve_assets(store, story_id)
    ec, board, script = tas._ec(store, story_id), tas._board(store, story_id), eps._script(store, story_id)
    made = {shot["shot_id"] for shot in board["shots"] if (shot["assets"].get("clip") or {}).get("state") == "current"}
    assert made and len(made) < len(board["shots"])
    assert tas._assets_doc(store, story_id)["links"]["video"]["link"] == SEEDANCE
    board_bytes = _board_file(store, story_id).read_bytes()

    offer = assets.video_offer(ec, board, SEEDANCE, why="HTTP 401", env=settings).as_dict()
    assert offer["switch"] == {"links": {"video": KLING}}
    assert '{"links": {"video": "fal/kling-2.5-turbo-std"}}' in offer["message"]

    with pytest.raises(workflow.WorkflowError) as caught:
        workflow.patch_assets(store, story_id, 1, {"links": {"video": "fal/flux-schnell"}}, now=LATER, env=settings)
    assert caught.value.detail["errors"] == [
        f"links.video: 'fal/flux-schnell' is not a link of VIDEO_CHAIN (its links: {SEEDANCE}, {KLING})"]
    assert tas._assets_doc(store, story_id)["links"]["video"]["link"] == SEEDANCE

    workflow.patch_assets(store, story_id, 1, offer["switch"], now=LATER, env=settings)

    assert _board_file(store, story_id).read_bytes() == board_bytes  # the storyboard's approval never moves
    doc = tas._assets_doc(store, story_id)
    assert doc["links"]["video"] == {"link": KLING, "since": LATER, "switched_from": SEEDANCE}
    states = {}
    for shot in board["shots"]:
        image = tas._episode_file(store, story_id, *shot["assets"]["image"].split("/"))
        states[shot["shot_id"]] = clips.clip_state(ec, shot, script, link=KLING, tier=2,
                                                   flags=clips.shot_flags(shot, doc), image_sha=_sha(image))
    assert {shot_id for shot_id, state in states.items() if state == "stale"} == made
    assert {state for shot_id, state in states.items() if shot_id not in made} == {"none"}
    assert workflow.assets_approval_state(ec, board, script, doc) == "stale"
    video = tce._units(store, story_id, settings, adapters=tvp._adapters(tvp.FakeVideo()))["video"]
    assert (video["link"], video["source"]) == (KLING, "record")
    assert "current clip" not in video["message"]  # nothing seedance made is kept on kling

    # The same link again: nothing written.
    workflow.patch_assets(store, story_id, 1, {"links": {"video": KLING}}, now="2026-10-02T00:00:00+00:00",
                          env=settings)
    assert tas._assets_doc(store, story_id)["links"]["video"]["since"] == LATER


# ============================================================ the render's check

class _Crash(tvp.FakeVideo):
    """Takes the request (journaled at its submit), then the process stops
    before the answer is recorded."""

    def _answer(self, link, request):
        raise KeyboardInterrupt


def test_the_render_check_before_a_job_names_a_failed_clips_target_and_a_held_re_animates_continue(store, tmp_path):
    """One planned clip failed (settled without a clip); another shot was
    re-animated and the provider took the request, but the process stopped
    before its answer: the storyboard still holds the old clip's key with the
    ``pending`` re-animate. The check a render meets before a job exists --
    and its estimate -- refuse naming the failed shot's target and, for the
    re-animate the provider holds, Continue only (never its target: a new
    request would buy a second clip). ``fill_failed_with_motion`` lets both
    through."""
    from clipping.aistory import workflow
    from clipping.aistory.steps import regenerate

    shot_ids = {}

    def video(planned):
        shot_ids["failed"], shot_ids["again"] = planned[0], planned[1]
        return tvp.FakeVideo(fail_for={f"shot_{planned[0][2:]}"})

    story_id, _planned = trc._animated(store, tmp_path, video=video)
    failed, again = shot_ids["failed"], shot_ids["again"]
    roomy = tce._settings(**dict(tvp.PAID, PER_EPISODE_CAP_USD="1.00"))
    ctx, _log = eps._ctx(store, story_id, step="regenerate", settings=roomy,
                         params={"target": f"shot:1:{again}:video", "note": "slower"})
    crash = _Crash()
    with pytest.raises(KeyboardInterrupt):
        regenerate.run(ctx, adapters=tvp._adapters(crash), time_fn=eps.Clock(0.0), sleep_fn=lambda _s: None)
    assert len(crash.requests) == 1
    clip = tvp._shot(store, story_id, again)["assets"]["clip"]
    assert clip["state"] == "current" and clip["pending"]["note"] == "slower"
    trs.approve_assets(store, story_id)
    ec = tas._ec(store, story_id)

    for check in (lambda: workflow.require_step_inputs(ec, "render", params={}),
                  lambda: workflow.render_estimate(ec, {})):
        with pytest.raises(workflow.WorkflowError) as caught:
            check()
        message = str(caught.value.detail)
        assert caught.value.code == workflow.CONFLICT
        assert f"shot {failed}'s clip failed" in message and f"shot:1:{failed}:video" in message
        assert f"shot {again}'s clip is still generating" in message and f"shot:1:{again}:video" not in message
        assert "press Continue (the assets step resumes it; nothing is bought again)" in message

    filled = {"fill_failed_with_motion": True}
    assert workflow.require_step_inputs(ec, "render", params=filled) is None
    assert workflow.render_estimate(ec, filled)["ready"] is True
    assert len(crash.requests) == 1  # nothing was asked again


# ============================================================ the fast track

def test_the_fast_track_estimate_counts_the_clips_at_tier_2_and_its_stop_says_how_to_do_without_them(
        store, tmp_path):
    """Tier 2, seedance keyed and allow_paid off: the fast track's estimate
    shows the planner's clips (``video``, the assets estimate's own part) in
    its paid part and total, and its stop -- before any generation call --
    names them and the ways out: keep their shots still, or animate off."""
    from clipping.aistory.steps import fast_track

    story_id = tas._episode(store, tmp_path)
    tce._tier(store, story_id)

    est = fast_track.estimate(tas._ec(store, story_id), env=tce._settings(), adapters=tce._adapters())

    video = est["video"]
    assert (video["link"], video["route_class"], video["ready"]) == (SEEDANCE, "paid", False)
    assert "allow_paid is off" in video["refused"]
    assert video["count"] >= 1 and video["est_usd"] > 0
    assert est["est_usd"] == pytest.approx(video["est_usd"])  # the images and voices are free
    paid = est["paid"]
    assert paid["verdict"] == "stops_before_paid"
    assert any(f"clip{'s' if video['count'] != 1 else ''} ({video['seconds']} s) on {SEEDANCE}" in part
               for part in paid["parts"])
    stop = est["stops_at"]["reason"]
    assert est["stops_at"]["step"] == "paid_check" and "Nothing was generated or spent" in stop
    assert "keep their shots still" in stop and "animate off" in stop
