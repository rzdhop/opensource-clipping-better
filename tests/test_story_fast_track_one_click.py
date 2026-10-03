"""The fast track as one click up to the finished render, and the review it
leaves behind (phase 7 follow-up, stage C; the human, 2026-10-02: "I generate
directly the whole thing at once, ready to read and approve").

- On a v2 story at tier >= 2 the fast track no longer stops at the keyframes:
  once they are made, checked (J2) and auto-fixed, it records the keyframe
  approval itself (``keyframes_approved.by == "fast_track"``, ``anyway`` and
  ``flagged`` naming the shots still flagged), buys the clips, approves the
  assets (``approved.by``), renders and writes the metadata. The job ends
  completed, "ready for review". ``stop_at_keyframes`` keeps today's stop.
- The paid check before anything is bought counts the whole episode -- the
  clips of a plan held for the keyframe approval included -- against the
  caps: refused whole with the numbers, never half-bought.
- The time budget is derived from the plan (the clips' poll budget, J2, the
  auto-fix) under a ceiling, instead of the flat hour.
- The episode page's ``review`` block: per shot the keyframe, the clip, the
  verdict's state, the fix and the lines; per episode what the fast track
  approved, what is still pending, the flagged shots, the spend by kind and
  the render's state.

The run is stage 6b's own v2 tier-2 episode (``tests/test_story_keyframe_gate.py``,
its keyframes made) and, made from scratch, stage B's quality episode
(``tests/test_story_keyframe_fix.py``: the keyframes on fal's editor at $0.04,
the auto-fix on) at tier 2 on fal's seedance -- a fully animated episode;
every provider is a fake. Stdlib + pytest (DEC-012); the API test skips
without fastapi.
"""

from __future__ import annotations

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_episode_steps as eps
import test_story_fast_track as tft
import test_story_keyframe_consistency as kc
import test_story_keyframe_fix as kf
import test_story_keyframe_gate as kg
import test_story_video_phase as tvp
from clipping.aistory import schemas
from test_stories_api_phase4 import api  # noqa: F401 -- the throwaway app (skips without fastapi)
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_keyframe_gate import built, unpaced  # noqa: F401
from test_story_video_phase import timings_path  # noqa: F401

NOW = eps.NOW
# The quality episode's settings with a video link: fal's seedance (tce.VIDEO_API).
QUALITY_SETTINGS = dict(kf.SETTINGS, **tce.VIDEO_API)


def _ft():
    from clipping.aistory.steps import fast_track

    return fast_track


def _wf():
    from clipping.aistory import workflow

    return workflow


def _fakes(tmp_path, *, video=None, vision=None, llm=True):
    runner = tft.eps.FakeLLM(default={"M1": tft.tms.m1_reply}) if llm else tft.no_llm()
    fakes = tft.Fakes(tmp_path, runner=runner)
    fakes.adapters = kg._adapters(video=video or tvp.FakeVideo(), vision=vision or kg.FakeVision())
    return fakes


def _v2_unmade(store, tmp_path):
    """Stage B's quality episode (v2, script and storyboard approved, nothing
    made) at tier 2 on the quality profile: fully animated, so the one click
    makes every keyframe, checks it, buys every clip and renders."""
    story_id = kf._quality(store, tmp_path)
    tce._tier(store, story_id, tier=2, budget_profile="quality")
    return story_id


def _quality_fakes(tmp_path, *, video, vision=None, llm=True):
    """The quality episode's seams: fal's editor (``kc.SeededImage``, $0.04 an
    image), J2, the video link; returns ``(fakes, image)``."""
    runner = tft.eps.FakeLLM(default={"M1": tft.tms.m1_reply}) if llm else tft.no_llm()
    fakes = tft.Fakes(tmp_path, runner=runner)
    image = kc.SeededImage(price=kf.PRICE)
    table = kf._adapters(image=image, vision=vision or kg.FakeVision())
    table[("video", "fal")] = video
    fakes.adapters = table
    return fakes, image


def _page(store, story_id):
    """The episode page as the web layer assembles it (``_episode_page``):
    the view, the outputs, the clips merged into the assets' shots."""
    wf = _wf()
    story = store.get(story_id)
    page = wf.episode_view(store, story, 1)
    page.update(wf.episode_outputs(store, story, 1))
    clips = wf.episode_clips(store, story, 1, env=kg.SETTINGS)
    page["assets"].update(tier=clips["tier"], links=clips["links"], video=clips["video"],
                          image_offer=clips["image_offer"])
    for shot in page["assets"]["shots"]:
        clip = clips["shots"].get(shot["shot_id"])
        shot["clip"] = None if clip is None else dict(clip, url=f"/clips/{clip['name']}" if clip["name"] else None)
    return page


# ============================================================ the params and the budget

def test_the_fast_track_takes_stop_at_keyframes_true_or_false():
    ft = _ft()
    assert ft.PARAMS == ("storyboard", "stop_at_keyframes")
    assert ft.read_params(None) == {"storyboard": "t1", "stop_at_keyframes": False}
    assert ft.read_params({"stop_at_keyframes": True})["stop_at_keyframes"] is True
    try:
        ft.read_params({"stop_at_keyframes": "yes"})
    except tft.steps.StepFailed as exc:
        assert str(exc) == "The fast track's stop_at_keyframes is true or false, not 'yes'."
    else:
        raise AssertionError("a non-boolean stop_at_keyframes was taken")


def test_the_budget_is_derived_from_the_plan_under_a_ceiling():
    """A free-chain episode keeps its hour; a clip adds its poll budget, a v2
    shot its J2 call, a possible redraw its image and two checks; and a fully
    animated episode never holds the worker slot past the ceiling."""
    from clipping.aistory.steps import assets, judge

    ft = _ft()
    assert ft.budget_seconds(shots=24, clips=0) == ft.FAST_TRACK_BUDGET_SECONDS == 3600
    assert ft.budget_seconds(shots=10, clips=3) == 3600 + 3 * assets.STORY_CLIP_CALL_SECONDS
    assert ft.budget_seconds(shots=10, clips=0, v2=True) == 3600 + 10 * judge.STORY_VISION_CALL_SECONDS
    one = assets.STORY_IMAGE_CALL_SECONDS + 2 * judge.STORY_VISION_CALL_SECONDS
    assert ft.budget_seconds(shots=10, clips=0, v2=True, redraws=2) == (
        3600 + 10 * judge.STORY_VISION_CALL_SECONDS + 2 * one)
    assert ft.budget_seconds(shots=24, clips=24, v2=True, redraws=48) == ft.FAST_TRACK_BUDGET_CEILING_SECONDS
    assert ft.FAST_TRACK_BUDGET_CEILING_SECONDS == 4 * 3600


# ============================================================ the one click

def test_the_one_click_approves_the_keyframes_buys_the_clips_approves_the_assets_and_renders(store, tmp_path,
                                                                                            built):
    story_id = kg._v2_keyframes(store, tmp_path, built)
    video = tvp.FakeVideo()
    fakes = _fakes(tmp_path, video=video)

    summary, log = tft.run(store, story_id, fakes, settings=kg.SETTINGS)

    assert video.requests, "the clips are bought in the same run"
    doc = kg._doc(store, story_id)
    approved = doc["keyframes_approved"]
    assert approved["by"] == "fast_track" and approved["anyway"] is False and approved["flagged"] == []
    assert approved["fingerprint"] and approved["at"]
    assert doc["approved"]["by"] == "fast_track"
    assert summary["auto_approved"] == ["keyframes", "assets"]
    assert summary["steps"]["render"]["state"] == "completed"
    assert summary["keyframes"] == {"auto_approved": True, "anyway": False, "flagged": [], "unchecked": []}
    assert any(line.startswith("✅ Fast track: episode 1's keyframes auto-approved (") for line in log)
    assert log[-1].startswith("🏁 Fast track done: episode 1 is rendered with its metadata pack")
    assert "ready for review" in log[-1]
    # The plan-derived budget: the clips' poll time and the J2 calls on top of the hour, said in minutes.
    ft = _ft()
    shots = len(tas._board(store, story_id)["shots"])
    clips = summary["steps"]["paid_check"]["clips"]
    assert clips == len(video.requests) > 0
    expected = ft.budget_seconds(shots=shots, clips=clips, v2=True)
    assert any(f"under a {int(expected // 60)}-minute budget" in line for line in log), log[:3]


def test_flagged_keyframes_are_approved_anyway_by_the_one_click_naming_them(store, tmp_path, built):
    story_id = kg._v2_keyframes(store, tmp_path, built)
    video = tvp.FakeVideo()
    fakes = _fakes(tmp_path, video=video, vision=kg.FakeVision(kg._failing("sh02")))

    summary, log = tft.run(store, story_id, fakes, settings=kg.SETTINGS)

    approved = kg._doc(store, story_id)["keyframes_approved"]
    assert approved["by"] == "fast_track" and approved["anyway"] is True and approved["flagged"] == ["sh02"]
    assert summary["keyframes"] == {"auto_approved": True, "anyway": True, "flagged": ["sh02"], "unchecked": []}
    approval = next(line for line in log if line.startswith("✅ Fast track: episode 1's keyframes auto-approved"))
    assert "anyway" in approval and "sh02" in approval and "missing the coconut phone" in approval
    assert video.requests and summary["steps"]["render"]["state"] == "completed"
    assert "still flagged: sh02" in log[-1]


def test_stop_at_keyframes_keeps_todays_stop_and_the_human_s_approval(store, tmp_path, built):
    story_id = kg._v2_keyframes(store, tmp_path, built)
    video = tvp.FakeVideo()
    fakes = _fakes(tmp_path, video=video, llm=False)

    message = tft.stopped(store, story_id, fakes, settings=kg.SETTINGS, params={"stop_at_keyframes": True})

    assert message.startswith("Fast track stopped at the assets (step 4 of 6): Episode 1's keyframes are made")
    assert video.requests == []
    doc = kg._doc(store, story_id)
    assert "keyframes_approved" not in doc and not doc.get("approved")
    # The human's own approval is recorded as theirs.
    kg._approve_keyframes(store, story_id)
    approved = kg._doc(store, story_id)["keyframes_approved"]
    assert approved["by"] == "user" and approved["flagged"] == []


def test_the_paid_check_counts_the_held_clips_against_every_cap_before_anything_is_made(store, tmp_path):
    """A v2 plan's clips wait for the keyframe approval the fast track records
    itself, so they are in the check that runs before the first call: a day
    cap the clips alone break stops it at the paid check, naming them, and
    no image, voice or clip is asked."""
    story_id = _v2_unmade(store, tmp_path)
    video, vision = tvp.FakeVideo(), kg.FakeVision()
    fakes, image = _quality_fakes(tmp_path, video=video, vision=vision, llm=False)
    units = tce._units(store, story_id, QUALITY_SETTINGS, adapters=fakes.adapters)
    plan = units["video"]
    assert plan["count"] == len(tas._shots(store, story_id)) and plan["est_usd"] > 0 and plan["hold"]
    # The keyframes and the auto-fix's ceiling fit the day; with the clips, the plan does not.
    cap = units["est_usd"] + plan["est_usd"] / 2
    settings = dict(QUALITY_SETTINGS, DAILY_CAP_USD=f"{cap:.3f}")

    message = tft.stopped(store, story_id, fakes, settings=settings)

    assert message.startswith("Fast track stopped at the paid check (step 3 of 6): Episode 1's assets would go over "
                              "a cap -- ")
    assert f"{plan['count']} clips ({plan['seconds']} s) on {plan['link']} (est ${plan['est_usd']:.3f})" in message
    assert "up to $0.40 to redraw flagged keyframes" in message
    assert "daily cap" in message and "Nothing was generated or spent" in message
    assert image.requests == [] and vision.requests == [] and video.requests == []
    assert tas._assets_doc(store, story_id) is None


def test_from_scratch_the_one_click_makes_checks_approves_buys_and_renders(store, tmp_path):
    ft = _ft()
    story_id = _v2_unmade(store, tmp_path)
    video, vision = tvp.FakeVideo(), kg.FakeVision()
    fakes, image = _quality_fakes(tmp_path, video=video, vision=vision)

    summary, log = tft.run(store, story_id, fakes, settings=QUALITY_SETTINGS)

    shots = [shot["shot_id"] for shot in tas._board(store, story_id)["shots"]]
    assert len(image.requests) == len(shots) and vision.shots() == shots  # every keyframe, J2 once a shot
    assert len(video.requests) == len(shots), "a fully animated episode: every clip bought in the same run"
    assert summary["steps"]["paid_check"]["verdict"] == ft.PAID_WITHIN_CAPS
    assert summary["steps"]["paid_check"]["clips"] == len(shots)
    assert summary["steps"]["render"]["state"] == "completed"
    doc = tas._assets_doc(store, story_id)
    assert doc["keyframes_approved"]["by"] == "fast_track" and doc["approved"]["by"] == "fast_track"
    assert summary["auto_approved"] == ["keyframes", "assets"]
    assert summary["keyframes"]["flagged"] == [] and summary["keyframes"]["anyway"] is False
    # The plan-derived budget on a fully animated episode is the ceiling, said once the plan is exact.
    assert any(f"under a {int(ft.FAST_TRACK_BUDGET_CEILING_SECONDS // 60)}-minute budget" in line for line in log)
    # Continue repeats nothing: every approval stands, no call is made.
    again, image = _quality_fakes(tmp_path, video=tvp.FakeVideo(), vision=kg.FakeVision(), llm=False)
    summary, _log = tft.run(store, story_id, again, settings=QUALITY_SETTINGS)
    assert image.requests == [] and again.adapters[("video", "fal")].requests == []
    assert again.adapters[("vision", "gemini")].requests == []
    assert summary["auto_approved"] == [] and summary["steps"]["assets"]["kept"] is True


def test_the_estimate_says_what_the_one_click_does_with_the_keyframes(store, tmp_path, built):
    ft = _ft()
    story_id = kg._v2_keyframes(store, tmp_path, built)

    estimate = ft.estimate(tas._ec(store, story_id), env=kg.SETTINGS, adapters=kg._adapters())

    assert estimate["keyframes"] == {"v2": True, "tier": 2, "approval": "none", "fix_usd": 0.0}
    assert estimate["video"]["count"] > 0 and estimate["est_usd"] == estimate["video"]["est_usd"]
    assert estimate["paid"]["verdict"] == ft.PAID_WITHIN_CAPS and estimate["stops_at"] is None
    # A legacy story's estimate names no keyframe check.
    legacy = ft.estimate(tas._ec(store, kg._v2_keyframes(store, tmp_path, built, v2=False)), env=kg.SETTINGS,
                         adapters=kg._adapters())
    assert legacy["keyframes"] == {"v2": False, "tier": 2, "approval": None, "fix_usd": 0.0}


# ============================================================ the schema

def test_the_keyframe_and_assets_approvals_record_who_approved_and_what_was_flagged():
    import test_story_schemas_phase4 as tsp

    doc = tsp._assets()
    doc["approved"] = {"at": NOW, "fingerprint": tsp.SHA_A, "by": "fast_track"}
    doc["keyframes_approved"] = {"at": NOW, "anyway": True, "fingerprint": tsp.SHA_A, "by": "fast_track",
                                 "flagged": ["sh02", "sh07"]}
    assert schemas.episode_assets_errors(doc) == []
    doc["approved"]["by"] = "robot"
    assert any("$.approved.by" in error for error in schemas.episode_assets_errors(doc))
    doc["approved"]["by"] = "user"
    doc["keyframes_approved"]["flagged"] = ["nope"]
    assert any("$.keyframes_approved.flagged" in error for error in schemas.episode_assets_errors(doc))


# ============================================================ the review

def test_the_review_block_reads_the_finished_episode_and_what_is_still_pending(store, tmp_path, built):
    from clipping.aistory.steps import assets

    wf = _wf()
    story_id = kg._v2_keyframes(store, tmp_path, built)
    video = tvp.FakeVideo()
    tft.run(store, story_id, _fakes(tmp_path, video=video, vision=kg.FakeVision(kg._failing("sh02"))),
            settings=kg.SETTINGS)

    review = wf.episode_review(_page(store, story_id))

    assert review["status"] == "ready" and review["ready"] is True
    assert review["headline"].startswith("Ready for review")
    assert review["auto_approved"] == ["keyframes", "assets"] and review["pending"] == []
    assert review["flagged"] == ["sh02"] and review["unchecked"] == [] and review["fixed"] == []
    approvals = review["approvals"]
    assert approvals["keyframes"]["by"] == "fast_track" and approvals["keyframes"]["anyway"] is True
    assert approvals["keyframes"]["flagged"] == ["sh02"] and approvals["keyframes"]["target"] == "keyframes:1"
    assert approvals["assets"] == {"approval": "current", "at": approvals["assets"]["at"], "by": "fast_track",
                                   "target": "assets:1"}
    assert approvals["script"]["approved"] is True and approvals["storyboard"]["approved"] is True
    assert review["render"]["state"] == "completed" and review["render"]["out_of_date"] is False
    assert review["script_repairs"] is None
    spend = review["spend"]
    assert spend["clips_usd"] > 0 and spend["total_usd"] >= spend["clips_usd"]
    assert set(spend) == {"images_usd", "fixes_usd", "voices_usd", "clips_usd", "other_usd", "total_usd"}
    # Per shot: the keyframe, the clip, the verdict, the lines.
    board = tas._board(store, story_id)
    by_id = {shot["shot_id"]: shot for shot in review["shots"]}
    assert [shot["shot_id"] for shot in review["shots"]] == [shot["shot_id"] for shot in board["shots"]]
    first = by_id["sh01"]
    assert first["image_name"] and first["target"] == "shot:1:sh01" and first["image_state"] == "current"
    assert first["verdict"] == {"state": "passed", "issue": None, "redraws": 0, "gave_up": False}
    assert by_id["sh02"]["verdict"]["state"] == "flagged"
    assert by_id["sh02"]["verdict"]["issue"] == "does not show the beat, missing the coconut phone"
    animated = [shot for shot in review["shots"] if shot["clip"] and shot["clip"]["name"]]
    assert len(animated) == len(video.requests)
    clip = animated[0]["clip"]
    assert clip["current"] is True and clip["url"] == f"/clips/{clip['name']}"
    assert clip["target"] == f"shot:1:{animated[0]['shot_id']}:video"
    script = eps._script(store, story_id)
    lines = {line["line_id"]: line for scene in script["scenes"] for line in scene["lines"]}
    shot = next(shot for shot in board["shots"] if shot["lines"])
    assert by_id[shot["shot_id"]]["lines"] == [
        {"line_id": line_id, "speaker": lines[line_id]["speaker"], "text": lines[line_id]["text"]}
        for line_id in shot["lines"]]
    assert set(review["shots"][0]) == {"shot_id", "scene_id", "order", "image_name", "image_state", "locked",
                                       "target", "clip", "verdict", "fix", "lines"}

    # A keyframe changed since: both approvals are stale, pending in order, the render out of date.
    ec = tas._ec(store, story_id)
    with open(assets.shot_image_path(ec, board["shots"][3]), "ab") as handle:
        handle.write(b"another take")
    review = wf.episode_review(_page(store, story_id))
    assert review["status"] == "keyframes_pending" and review["ready"] is False
    assert review["pending"] == ["keyframes", "assets"]
    assert review["approvals"]["keyframes"]["approval"] == "stale"
    assert review["shots"][3]["verdict"]["state"] == "not_current"
    assert review["render"]["out_of_date"] is True
    assert review["headline"].startswith("The keyframes changed since they were approved")


def test_the_review_block_of_an_episode_not_made_yet_and_of_a_fixed_keyframe(store, tmp_path, monkeypatch):
    import test_story_keyframe_fix as kf

    wf = _wf()
    story_id = kf._quality(store, tmp_path)
    page = _page(store, story_id)
    review = wf.episode_review(page)
    assert review["status"] == "not_made" and review["pending"] == ["keyframes", "assets"]
    assert review["shots"] and review["shots"][0]["image_name"] is None
    assert review["shots"][0]["verdict"] == {"state": "unchecked", "issue": None, "redraws": 0, "gave_up": False}

    kf._seeds(monkeypatch)
    kf._run(store, story_id, vision=kf.Judge({"sh05": 1, "sh07": None}))
    review = wf.episode_review(_page(store, story_id))
    assert review["status"] == "keyframes_pending" and review["auto_approved"] == []
    by_id = {shot["shot_id"]: shot for shot in review["shots"]}
    assert by_id["sh05"]["verdict"] == {"state": "fixed", "issue": None, "redraws": 1, "gave_up": False}
    assert by_id["sh07"]["verdict"]["state"] == "flagged" and by_id["sh07"]["verdict"]["gave_up"] is True
    assert by_id["sh07"]["verdict"]["redraws"] == 2 and by_id["sh07"]["fix"]["history"]
    assert review["fixed"] == ["sh05"] and review["flagged"] == ["sh07"]
    assert review["spend"]["fixes_usd"] == round(3 * kf.PRICE, 4)
    assert "1 still flagged" in review["headline"]
    assert wf.REVIEW_VERDICT_STATES == ("passed", "fixed", "flagged", "unchecked", "not_current")
    assert wf.REVIEW_STATUSES == ("not_made", "keyframes_pending", "assets_pending", "render_needed", "ready")


def test_a_legacy_episode_s_review_has_no_keyframe_approval(store, tmp_path):
    wf = _wf()
    story_id = tas._episode(store, tmp_path)
    tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    review = wf.episode_review(_page(store, story_id))
    assert review["approvals"]["keyframes"] is None and review["pending"] == ["assets"]
    assert review["status"] == "assets_pending" and review["flagged"] == []
    assert all(shot["verdict"]["state"] == "unchecked" and shot["clip"] is None for shot in review["shots"])


# ============================================================ the API

def test_the_episode_page_carries_the_review_and_the_step_takes_the_param(api, tmp_path, built):
    story_id = kg._v2_keyframes(api.store, tmp_path, built)
    tft.run(api.store, story_id, _fakes(tmp_path), settings=kg.SETTINGS)

    page = api.client.get(f"/api/stories/{story_id}/episodes/1").json()
    review = page["review"]
    assert review["status"] == "ready" and review["auto_approved"] == ["keyframes", "assets"]
    animated = next(shot for shot in review["shots"] if shot["clip"] and shot["clip"]["name"])
    assert animated["clip"]["url"].startswith(f"/api/stories/{story_id}/episodes/1/clips/")

    refused = api.client.post(f"/api/stories/{story_id}/steps/fast-track",
                              json={"ep": 1, "params": {"stop_at_keyframes": "yes"}})
    assert refused.status_code == 400
    assert refused.json()["detail"] == "The fast track's stop_at_keyframes is true or false, not 'yes'."
    queued = api.client.post(f"/api/stories/{story_id}/steps/fast-track",
                             json={"ep": 1, "params": {"stop_at_keyframes": True}})
    assert queued.status_code == 201, queued.text
    assert queued.json()["params"] == {"stop_at_keyframes": True}
