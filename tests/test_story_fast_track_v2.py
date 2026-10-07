"""The fast track on a v2 story (phase 7, the stage-6 follow-ups; DEC-230,
DEC-231 part 2, DEC-236).

- The script: on v2 a first-watch report (J1) that is missing, stale or
  failed is a refusal of its own, and a length outside the window is never
  "approve it yourself" (a v2 episode outside its window is never approved).
- The keyframes: a v2 episode's clips wait for the human's keyframe approval
  (RC-Q3), so the fast track makes the keyframes, then stops there -- it
  never auto-approves assets whose clips are held -- and after the approval
  Continue buys the clips.

The pure rule reuses ``tests/test_story_fast_track.py``'s script shape; the
run is stage 6b's own v2 episode (``tests/test_story_keyframe_gate.py``).
Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import test_story_fast_track as tft
import test_story_keyframe_gate as kg
import test_story_video_phase as tvp
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_keyframe_gate import built, unpaced  # noqa: F401
from test_story_video_phase import timings_path  # noqa: F401

SCRIPT = {"scenes": [{"scene_id": "s01", "function": "hook", "state": "written"}], "rev": 3,
          "next_episode_teaser": "x", "cliffhanger": {"reveal": "y"},
          "consistency_report": {"passed": True, "issues": [], "checked_rev": 3, "stale": False},
          "first_watch": {"who_wants_what": "a", "what_happens": "b", "why_it_matters": "c", "passed": True,
                          "issues": [], "checked_rev": 3, "checked_at": "2026-10-02T10:00:00+00:00",
                          "stale": False, "version": 2},
          "timing": {"state": "ok", "total_s": 62.0, "window_s": [55, 75], "measured_lines": 0,
                     "estimated_lines": 2, "flags": []}}


def _with(**changes):
    script = dict(SCRIPT)
    script.update(changes)
    return script


def test_on_v2_the_first_watch_report_is_a_refusal_of_its_own():
    ft = tft._ft()
    assert ft.script_refusal(SCRIPT, 1, v2=True) is None
    no_report = _with(first_watch=None)
    no_report.pop("first_watch")
    assert "first-watch check (J1) has not run on this revision" in ft.script_refusal(no_report, 1, v2=True)
    stale = _with(first_watch=dict(SCRIPT["first_watch"], stale=True))
    assert "first-watch check (J1) has not run on this revision" in ft.script_refusal(stale, 1, v2=True)
    failed = _with(first_watch=dict(SCRIPT["first_watch"], passed=False, issues=[
        {"scene_id": "s03", "kind": "object_unseen", "fix": "Show the key before it matters."}]))
    message = ft.script_refusal(failed, 1, v2=True)
    assert "first-watch check (J1) found 1 issue: s03 (object_unseen): Show the key before it matters." in message
    assert "never approves over blocking issues" in message and "approve the script anyway yourself" in message
    # A legacy script has no first-watch report and needs none.
    legacy = dict(SCRIPT)
    legacy.pop("first_watch")
    assert ft.script_refusal(legacy, 1) is None


def test_on_v2_a_script_outside_its_window_is_never_approve_it_yourself():
    ft = tft._ft()
    under = _with(timing=dict(SCRIPT["timing"], state="under", total_s=48.0))
    message = ft.script_refusal(under, 1, v2=True)
    assert "approve it yourself" not in message
    assert "never approved outside" in message and "fill pass" in message
    over = _with(timing=dict(SCRIPT["timing"], state="over", total_s=81.0))
    message = ft.script_refusal(over, 1, v2=True)
    assert "approve it yourself" not in message and "shorten it" in message
    # The legacy sentence is unchanged.
    assert "or approve it yourself" in ft.script_refusal(under, 1)


def test_the_fast_track_stops_at_the_keyframe_approval_and_buys_no_clip_until_it(store, tmp_path, built):
    """Phase 7 follow-up stage C, re-pinned on purpose: the stop at the
    keyframes is now the ``stop_at_keyframes`` param's (the default goes up
    to the render, ``tests/test_story_fast_track_one_click.py``). Plan 33
    stage 4: the shots the key-shots plan leaves out are kept still, so the
    render (every shot a video clip) is reached."""
    story_id = kg._v2_keyframes(store, tmp_path, built)
    tvp.keep_unplanned_still(store, story_id, tvp.planned_ids(store, story_id, kg.SETTINGS))
    video = tvp.FakeVideo()
    fakes = tft.Fakes(tmp_path, runner=tft.no_llm())
    fakes.adapters = kg._adapters(video=video, vision=kg.FakeVision())

    message = tft.stopped(store, story_id, fakes, settings=kg.SETTINGS, params={"stop_at_keyframes": True})

    assert message.startswith("Fast track stopped at the assets (step 4 of 6): Episode 1's keyframes are made")
    assert "approve the keyframes first (keyframes:1)" in message
    assert "Approve keyframes" in message
    assert video.requests == []  # no clip bought before the approval (RC-Q3)
    doc = kg._doc(store, story_id)
    assert doc.get("keyframe_verdicts") and not doc.get("approved")

    # The human approves the keyframes; Continue buys the clips and finishes the episode.
    kg._approve_keyframes(store, story_id)
    again = tft.Fakes(tmp_path, runner=tft.eps.FakeLLM(default={"M1": tft.tms.m1_reply}))
    again.adapters = kg._adapters(video=video, vision=kg.FakeVision())

    summary, _log = tft.run(store, story_id, again, settings=kg.SETTINGS)

    assert video.requests, "the clips are bought once the keyframes are approved"
    assert summary["auto_approved"] == ["assets"] and summary["steps"]["render"]["state"] == "completed"
    assert kg._doc(store, story_id)["approved"]


def test_a_fully_animated_storys_paid_stop_never_offers_still_shots_as_a_way_out(store, tmp_path):
    """DEC-236: every shot of a fully animated story is a clip, so the paid
    check's way out of the clips' cost is allow_paid or a cap -- never
    "keep their shots still" or "animate off". Plan 33 stage 4, re-pinned on
    purpose: the legacy sentence no longer offers them either."""
    import test_story_assets_step as tas
    import test_story_clip_estimate as tce
    from clipping.aistory.steps import fast_track

    story_id = tas._episode(store, tmp_path)
    tce._tier(store, story_id)
    units = tce._units(store, story_id, tce._settings())

    legacy = fast_track.paid_verdict(units, ep=1)
    assert "keep their shots still" not in legacy["stop"] and "animate off" not in legacy["stop"]

    fully = fast_track.paid_verdict(units, ep=1, fully_animated=True)
    assert fully["verdict"] == legacy["verdict"] == fast_track.STOPS_BEFORE_PAID
    assert "keep their shots still" not in fully["stop"] and "animate off" not in fully["stop"]
    assert "turn allow_paid on in Settings" in fully["stop"]
