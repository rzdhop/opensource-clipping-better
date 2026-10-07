"""The AI Story API's clip routes and fields (phase 6, stage 11; DEC-113,
DEC-173, DEC-204, DEC-209, A-087).

- ``PATCH /episodes/{ep}/assets`` takes each shot's ``keep_still``,
  ``animate`` and ``keep_native_audio`` (true, false, or null to clear) and
  ``links {image?, video?}`` -- a switch checked against the Settings
  chains, never the process's.
- ``GET /estimate/assets?ep=&route=`` prices the episode on another route
  without patching the story.
- ``POST /steps/render`` meets the clip refusal before a job exists (409);
  plan 33 stage 4: ``fill_failed_with_motion`` is retired (400, unknown).
- ``GET /episodes/{ep}/clips/{name}`` is the shot-image route's twin for a
  clip, open while ``API_TOKEN`` is unset (RC-M9); the episode page carries
  each shot's clip, the tier, the episode's links and the video estimate.

The throwaway app, its job store and the episodes are phase 4's API tests'
own (``tests/test_stories_api_phase4.py``); the clips are made by stage 8's
fake video adapter (``tests/test_story_video_phase.py``). Nothing leaves the
process. These need pydantic, fastapi and httpx and skip without them, like
the other route tests; the routes are reached through the client only, so on
the parent commit each test fails on its own.
"""

from __future__ import annotations

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_render_clips as trc
import test_story_video_phase as tvp
from test_stories_api_phase4 import _doc, _ep_dir, _episode, _post_step, _settings, _url, api, episode  # noqa: F401
from test_stories_api_phase4 import episodes  # noqa: F401 -- the session's episodes
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_video_phase import timings_path  # noqa: F401 -- the measured clip timings under tmp_path

CF, POLL = "cloudflare/flux-1-schnell", "pollinations/flux"
SEEDANCE, KLING = tvp.SEEDANCE, tvp.KLING
LATER = tce.LATER


def _story(api, story_id):
    return api.store.get(story_id)


def test_the_assets_patch_takes_shot_flags_and_switches_links_on_the_settings_chains(api, episodes):
    """The process env has no VIDEO_CHAIN (its default lists seedance); the
    Settings list kling alone. Seedance is refused (400, nothing written);
    the flags and both links are taken in one edit; a null clears a flag.
    The storyboard -- bytes and approval -- never moves."""
    story_id = episode(api, episodes, "made")
    tce._tier(api.store, story_id)
    assert _doc(api, story_id, "assets.json")["links"]["image"]["link"] == POLL
    board_file = _ep_dir(api, story_id) / "storyboard.json"
    board_bytes = board_file.read_bytes()
    _settings(api, tas._settings(IMAGE_CHAIN=f"{POLL},{CF}", VIDEO_CHAIN=KLING, **tas.FAL))
    path = _url(story_id, "/episodes/1/assets")

    refused = api.client.patch(path, json={"links": {"video": SEEDANCE}})
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"]["errors"] == [
        f"links.video: '{SEEDANCE}' is not a link of VIDEO_CHAIN (its links: {KLING})"]
    assert "video" not in (_doc(api, story_id, "assets.json").get("links") or {})

    response = api.client.patch(path, json={
        "shots": [{"shot_id": "sh01", "keep_still": True},
                  {"shot_id": "sh02", "animate": True, "keep_native_audio": False}],
        "links": {"image": CF, "video": KLING}})
    assert response.status_code == 200, response.text
    doc = _doc(api, story_id, "assets.json")
    assert doc["shots"] == {"sh01": {"keep_still": True}, "sh02": {"animate": True, "keep_native_audio": False}}
    assert doc["links"]["image"]["link"] == CF and doc["links"]["image"]["switched_from"] == POLL
    assert doc["links"]["video"]["link"] == KLING and "switched_from" not in doc["links"]["video"]
    page = response.json()
    assert page["assets"]["links"]["video"]["link"] == KLING
    assert next(shot for shot in page["assets"]["shots"] if shot["shot_id"] == "sh01")["clip"]["flags"][
        "keep_still"] is True

    cleared = api.client.patch(path, json={"shots": [{"shot_id": "sh02", "animate": None}]})
    assert cleared.status_code == 200, cleared.text
    assert _doc(api, story_id, "assets.json")["shots"]["sh02"] == {"keep_native_audio": False}
    assert board_file.read_bytes() == board_bytes


def test_the_assets_estimate_prices_another_route_and_leaves_the_story_as_it_was(api, episodes):
    """A tier-2 story on route auto, its images and voices made; seedance
    keyed, paid on. ``route=api`` plans the clips on seedance at a price;
    ``route=local`` has no local ComfyUI, so it plans none, at $0. The
    story's own route is unchanged; any other route is a 400."""
    story_id = episode(api, episodes, "made")
    tce._tier(api.store, story_id)
    _settings(api, tce._settings(**tvp.PAID))
    story_before = _story(api, story_id)
    path = _url(story_id, "/estimate/assets")

    on_api = api.client.get(path, params={"ep": 1, "route": "api"})
    on_local = api.client.get(path, params={"ep": 1, "route": "local"})

    assert on_api.status_code == on_local.status_code == 200, (on_api.text, on_local.text)
    paid, local = on_api.json()["video"], on_local.json()["video"]
    assert (paid["route"], paid["link"], paid["route_class"], paid["ready"]) == ("api", SEEDANCE, "paid", True)
    assert paid["count"] >= 1 and paid["est_usd"] > 0
    assert (local["route"], local["link"], local["count"], local["est_usd"], local["ready"]) == (
        "local", None, 0, 0.0, False)
    assert _story(api, story_id) == story_before and story_before["generation_profile"]["route"] == "auto"
    assert api.client.get(path, params={"ep": 1}).json()["video"]["route"] == "auto"
    assert api.client.get(path, params={"ep": 1, "route": "cloud"}).status_code == 400


def test_the_render_is_refused_before_a_job_with_the_clip_refusal_and_nothing_fills_it(api, tmp_path):
    """A failed clip: ``POST /steps/render`` answers 409 with the render's
    own sentence (the shot's target), and creates no job. Plan 33 stage 4,
    re-pinned on purpose: the sentence offers no fill, and
    ``fill_failed_with_motion`` is an unknown parameter now (400, no job)."""
    shot_ids = {}

    def video(planned):
        shot_ids["failed"] = planned[0]
        return tvp.FakeVideo(fail_for={f"shot_{planned[0][2:]}"})

    story_id, _planned = trc._animated(api.store, tmp_path, video=video, still_unplanned=True)
    failed = shot_ids["failed"]

    refused = _post_step(api, story_id, "render", ep=1)

    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert f"shot {failed}'s clip failed" in detail and f"shot:1:{failed}:video" in detail
    assert "fill_failed_with_motion" not in detail and api.jobs.list_jobs() == []

    filled = _post_step(api, story_id, "render", ep=1, params={"fill_failed_with_motion": True})

    assert filled.status_code == 400, filled.text
    assert filled.json()["detail"] == ("Unknown render parameter(s) fill_failed_with_motion (known: subtitles, "
                                       "encoder).")
    assert api.jobs.list_jobs() == []


def test_the_render_estimate_meets_the_same_clip_refusal_and_no_flag_clears_it(api, tmp_path):
    """``GET /estimate/render`` is priced the same way the real render params
    would be -- a failed clip refuses the estimate (409, the same sentence
    ``POST /steps/render`` gives). Plan 33 stage 4, re-pinned on purpose: the
    phase 6 stage 12 follow-up's ``?fill_failed_with_motion=1`` is gone -- the
    route reads no such query any more, so it clears nothing. No job is ever
    created by an estimate."""
    shot_ids = {}

    def video(planned):
        shot_ids["failed"] = planned[0]
        return tvp.FakeVideo(fail_for={f"shot_{planned[0][2:]}"})

    story_id, _planned = trc._animated(api.store, tmp_path, video=video, still_unplanned=True)
    failed = shot_ids["failed"]
    path = _url(story_id, "/estimate/render")

    refused = api.client.get(path, params={"ep": 1})

    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert f"shot {failed}'s clip failed" in detail and f"shot:1:{failed}:video" in detail
    assert "fill_failed_with_motion" not in detail

    filled = api.client.get(path, params={"ep": 1, "fill_failed_with_motion": 1})

    assert filled.status_code == 409, filled.text
    assert filled.json()["detail"] == detail
    assert api.jobs.list_jobs() == []


def test_the_clip_route_serves_a_stored_clip_open_without_a_token_and_nothing_else(api, tmp_path, monkeypatch):
    """A clip the video phase made: the episode page names it -- its state,
    link, route and the plain path of the clip route (auth is off) -- and that
    route answers it as ``video/mp4`` with no token at all (RC-M9: no
    API_TOKEN, no DISABLE_AUTH either). A name outside the pattern, another
    folder's file or a missing clip is a 404."""
    from web.api import auth

    # Plan 33 stage 4: the unplanned shots kept still, so nothing blocks the render (render_blocked None).
    story_id, planned = trc._animated(api.store, tmp_path, still_unplanned=True)
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("DISABLE_AUTH", raising=False)
    monkeypatch.setattr(auth, "_TOKEN", None)
    shot_id = planned[0]
    name = f"shot_{shot_id[2:]}.mp4"
    clip_file = _ep_dir(api, story_id) / "assets" / "clips" / name

    page = _episode(api, story_id)
    assets = page["assets"]
    assert assets["tier"] == 2 and assets["links"]["video"]["link"] == SEEDANCE
    assert assets["video"]["link"] == SEEDANCE and assets["video"]["render_blocked"] is None
    clip = next(shot for shot in assets["shots"] if shot["shot_id"] == shot_id)["clip"]
    assert (clip["state"], clip["link"], clip["route"], clip["continue"]) == ("current", SEEDANCE, "paid", False)
    assert clip["target"] == f"shot:1:{shot_id}:video" and clip["blocked"] is None
    assert clip["url"] == f"/api/stories/{story_id}/episodes/1/clips/{name}"

    response = api.client.get(clip["url"])
    assert response.status_code == 200 and response.content == clip_file.read_bytes()
    assert response.headers["content-type"] == "video/mp4" and response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].startswith("inline")

    still = next(shot for shot in assets["shots"] if shot["shot_id"] not in planned)
    assert still["clip"]["state"] == "none" and still["clip"]["url"] is None
    for bad in (f"shot_{still['shot_id'][2:]}.mp4", "shot_1.mp4", "shot_00.mp4", f"shot_{shot_id[2:]}.mov",
                "shot_02.png", "..%2Fstoryboard.json", "..%2Fshots%2Fshot_02.png"):
        assert api.client.get(_url(story_id, f"/episodes/1/clips/{bad}")).status_code == 404, bad
    assert api.client.get(_url(story_id, f"/episodes/x/clips/{name}")).status_code == 404
