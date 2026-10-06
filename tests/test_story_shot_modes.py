"""A per-shot auto / my-own mode for clips and keyframes (plan 25 stage 1, D-1).

``assets.json``'s ``shot_modes[shot_id] = {"clip": "auto"|"manual",
"image": "auto"|"manual"}`` (optional; absent: the story's profile as
before). On a native-speech story a shot set to ``manual`` is the human's
own upload whatever the profile, and a shot set to ``auto`` on the manual
profile is bought on the ``native_speech`` profile's link of its class (the
story's speech model for a speaking shot). The assets step prices and sends
the auto rows only, lists the manual rows and ends ``awaiting_uploads`` for
them; a link that cannot run refuses the plan naming the shot and the key,
never a silent switch to the human. An upload is accepted on a shot whose
mode makes it the human's; refused on an auto shot with the sentence that
says how to switch. ``PATCH .../shots/{shot_id}/mode`` sets the modes and
answers what went stale (the link-change rule: a clip made on the other
link) and, for auto, the gate's dry-run verdict.

Stdlib + pytest (DEC-012). Real ffmpeg on tiny fixture clips; no provider
call (the speaking link is faked).
"""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_native_speech_plan as nsp
import test_story_native_take as tnt
from test_api_shot_upload import api  # noqa: F401 - the upload routes' client
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_clip_estimate import timings_path  # noqa: F401 - the measured clip timings under tmp_path

NOW = tas.NOW
MANUAL = "manual/upload"
FAST, LITE = nsp.FAST, nsp.LITE
PAID = {"GEMINI_PAID_API_KEY": "test-gemini-paid-key", "ALLOW_PAID": "1", "PER_EPISODE_CAP_USD": "40",
        "DAILY_CAP_USD": "80", "PER_STORY_CAP_USD": "200"}
NO_KEY = {"ALLOW_PAID": "1", "PER_EPISODE_CAP_USD": "40", "DAILY_CAP_USD": "80", "PER_STORY_CAP_USD": "200"}


def _speaking(store, story_id, count=3):
    return [shot["shot_id"] for shot in tas._board(store, story_id)["shots"] if shot.get("speaks")][:count]


def _three_speaking(store, story_id):
    """Three speaking shots left to animate, every other shot kept still
    (``assets.json``'s overrides): the episode the step plans is those three."""
    from clipping.aistory import workflow

    kept = _speaking(store, story_id)
    assert len(kept) == 3
    still = [{"shot_id": shot["shot_id"], "keep_still": True} for shot in tas._board(store, story_id)["shots"]
             if shot["shot_id"] not in kept]
    workflow.patch_assets(store, story_id, 1, {"shots": still}, now=NOW)
    return kept


def _set_mode(store, story_id, shot_id, **modes):
    from clipping.aistory import workflow

    return workflow.patch_shot_mode(store, story_id, 1, shot_id, modes, now=NOW, env=tas._settings(**PAID))


def _keyframe(store, story_id, shot_id):
    """A keyframe file on disk for *shot_id*, named on its board."""
    board = tas._board(store, story_id)
    shot = next(item for item in board["shots"] if item["shot_id"] == shot_id)
    name = f"shot_{shot_id[2:]}.png"
    with open(store.episode_asset_path(story_id, 1, "shots", name, create=True), "wb") as fh:
        fh.write(tas.PNG)
    shot["assets"]["image"] = f"assets/shots/{name}"
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)


def _host(store, story_id, *, settings, veo):
    from clipping.aistory.steps import assets

    host, log = tnt._host(store, story_id, settings=settings, video=veo)
    host.open_asset_gates()
    units = assets.asset_units(tas._ec(store, story_id), host.script, host.storyboard, env=host.ctx.settings_env,
                               adapters=host.tools.adapters, animate=True)
    host.planned_video = units["video"]
    host.video = assets._video_summary(units["video"], animate=True)
    return host, log, units


# ================================================================ (a) a mixed episode

def test_a_manual_episode_with_one_auto_shot_prices_and_sends_that_shot_alone(store, tmp_path, monkeypatch):
    """Fail-first. The manual profile, three speaking shots, the middle one
    set to auto with the Veo key set: the estimate prices that one row on
    the native_speech profile's fast link (the others $0 on manual/upload),
    the step sends exactly that one request, waits for the other two and
    ends ``awaiting_uploads``; the episode's speaking link is never recorded
    as the auto shot's (the others stay the human's)."""
    from clipping.aistory.steps import assets, sticky_link

    tnt._require_ffmpeg()
    monkeypatch.setattr(assets, "keyframe_problem", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(assets, "clip_hold", lambda *_args, **_kwargs: None)
    story_id = nsp.planned_story(store, profile="native_speech_manual")
    first, auto, third = _three_speaking(store, story_id)
    _keyframe(store, story_id, auto)
    answer = _set_mode(store, story_id, auto, clip="auto")
    assert answer["modes"] == {"clip": "auto", "image": "auto"} and answer["set"] == {"clip": "auto"}
    assert answer["verdict"]["clip"]["link"] == FAST and answer["verdict"]["clip"]["allowed"] is True

    board = {shot["shot_id"]: shot for shot in tas._board(store, story_id)["shots"]}
    veo = tnt.FakeVeo([tnt.make_clip(tmp_path / "auto.mp4", board[auto]["clip_s"])])
    host, log, units = _host(store, story_id, settings=PAID, veo=veo)
    video = units["video"]
    rows = {row["shot_id"]: row for row in video["plan"]}
    assert set(rows) == {first, auto, third}
    assert (rows[auto]["link"], rows[auto]["mode"]) == (FAST, "auto")
    assert rows[auto]["est_usd"] == pytest.approx(board[auto]["clip_s"] * 0.10)
    assert [rows[sid]["link"] for sid in (first, third)] == [MANUAL, MANUAL]
    assert [rows[sid]["est_usd"] for sid in (first, third)] == [0.0, 0.0]
    assert "mode" not in rows[first] and "mode" not in rows[third]
    assert video["est_usd"] == pytest.approx(board[auto]["clip_s"] * 0.10)
    assert video["ready"] and video["refused"] is None and video["route_class"] == "paid"
    assert video["speech"]["manual_shots"] == [first, third]
    assert [row["link"] for row in units["paid_links"] if row["kind"] == "video"] == [FAST]
    assert units["est_usd"] == pytest.approx(video["est_usd"])

    doc = host.animate_clips(host.write_assets_doc())
    result = host.finish(doc)
    assert [request.extra["name"] for request in veo.requests] == [f"shot_{auto[2:]}"]
    stored = {shot["shot_id"]: shot for shot in tas._board(store, story_id)["shots"]}
    assert stored[auto]["assets"]["clip"]["link"] == FAST and stored[auto]["assets"]["clip"]["state"] == "current"
    assert result["state"] == "awaiting_uploads"
    assert [item["shot_id"] for item in result["uploads"]["missing"]] == [first, third]
    booked = [row for row in tas._ledger(store, story_id) if row.get("unit") == "second"]
    assert len(booked) == 1 and booked[0]["est_usd"] == pytest.approx(board[auto]["clip_s"] * 0.10)
    after = tas._assets_doc(store, story_id)
    assert sticky_link.recorded(after, sticky_link.VIDEO_SPEECH) is None
    assert any(f"on {FAST}" in line and line.startswith("🎬 Animating 1 shot") for line in log)


# ================================================================ (b) no key

def test_an_auto_shot_with_no_key_refuses_the_plan_naming_the_shot_and_the_key(store, monkeypatch):
    """Never silently the human's: with no Veo key the auto row refuses the
    plan with one sentence naming the shot, the link and the missing key;
    nothing is sent."""
    from clipping.aistory.steps import assets
    from clipping.aistory.steps.llm_call import StepFailed

    monkeypatch.setattr(assets, "keyframe_problem", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(assets, "clip_hold", lambda *_args, **_kwargs: None)
    story_id = nsp.planned_story(store, profile="native_speech_manual")
    _first, auto, _third = _three_speaking(store, story_id)
    _set_mode(store, story_id, auto, clip="auto")
    veo = tnt.FakeVeo([])
    host, _log, units = _host(store, story_id, settings=NO_KEY, veo=veo)
    video = units["video"]
    assert video["ready"] is False
    assert video["refused"] == (f"shot {auto} is set to auto on {FAST}, which cannot run now (no API key "
                                "(GEMINI_PAID_API_KEY is not set)): set the key in Settings, or switch it to "
                                "'my own'")
    # The keyframes' own refusal comes first in the fixture (no image adapter): the clips' is asked alone.
    refusal = assets.plan_refusal(tas._ec(store, story_id), dict(units, images=dict(units["images"], count=0)))
    assert refusal is not None and video["refused"] in refusal and "Nothing was generated or spent" in refusal
    with pytest.raises(StepFailed) as caught:
        host.animate_clips(host.write_assets_doc())
    assert "GEMINI_PAID_API_KEY" in str(caught.value)
    assert veo.requests == [] and tas._ledger(store, story_id) == []
    verdict = _set_mode(store, story_id, auto, clip="auto")["verdict"]["clip"]
    assert verdict["allowed"] is True  # the route's env has the key; the step's had none


# ================================================================ (c) uploads

def test_an_upload_follows_the_shot_s_mode(store):
    from clipping.aistory import manual_uploads
    from clipping.aistory.steps import assets

    story_id = nsp.planned_story(store, profile="native_speech_manual")
    first, auto, _third = _three_speaking(store, story_id)
    _set_mode(store, story_id, auto, clip="auto")
    ec = tas._ec(store, story_id)
    doc = assets._read_assets_doc(ec)
    board = {shot["shot_id"]: shot for shot in tas._board(store, story_id)["shots"]}
    assert manual_uploads.upload_target_refusal(ec, board[auto], doc) == (
        f"Shot {auto}'s clip is made on {FAST} (auto): switch it to 'my own' to upload.")
    assert manual_uploads.upload_target_refusal(ec, board[first], doc) is None

    # An API story: a shot flipped to manual takes an upload; the others keep today's sentence.
    api_story = nsp.planned_story(store)
    own, other = _speaking(store, api_story, 2)
    _set_mode(store, api_story, own, clip="manual")
    ec = tas._ec(store, api_story)
    doc = assets._read_assets_doc(ec)
    board = {shot["shot_id"]: shot for shot in tas._board(store, api_story)["shots"]}
    assert manual_uploads.upload_target_refusal(ec, board[own], doc) is None
    assert manual_uploads.upload_target_refusal(ec, board[other], doc) == (
        f"Shot {other}'s clip is made on {FAST}, not uploaded: its link is not {MANUAL}.")


def test_an_api_story_takes_a_manual_shot_s_clip_and_keeps_its_speaking_link(store, tmp_path):
    """The upload of a shot set to manual on the API profile is stored as
    any upload; the episode's speaking link is not recorded as manual (the
    other speaking shots stay on Veo); a speaking shot's clip with no sound
    is still refused."""
    from clipping.aistory import manual_uploads
    from clipping.aistory.steps import sticky_link

    tnt._require_ffmpeg()
    story_id = nsp.planned_story(store)
    own, other = _speaking(store, story_id, 2)
    _set_mode(store, story_id, own, clip="manual")
    # Plan 28 F7 (DEC-305 section 5), re-pinned on purpose: an app-made keyframe must exist and pass its check
    # before its clip is taken; this shot's keyframe is the human's own (allowed).
    _set_mode(store, story_id, own, image="manual")
    folder = manual_uploads.clips_folder(store, story_id, 1)
    received = f"{folder}/.upload-mute.part"
    shutil.copyfile(tnt.make_clip(tmp_path / "mute.mp4", 8, sound=False), received)
    with pytest.raises(manual_uploads.UploadRefused) as caught:
        manual_uploads.accept_clip(store, story_id, 1, own, received, filename="mute.mp4", env=tas._settings())
    assert "no sound" in str(caught.value)
    received = f"{folder}/.upload.part"
    shutil.copyfile(tnt.make_clip(tmp_path / "take.mp4", 8), received)
    out = manual_uploads.accept_clip(store, story_id, 1, own, received, filename="take.mp4", env=tas._settings())
    assert out["clip"]["link"] == MANUAL and out["clip"]["state"] == "current"
    assert sticky_link.recorded(tas._assets_doc(store, story_id), sticky_link.VIDEO_SPEECH) is None
    video = tce._units(store, story_id, tas._settings(**PAID))["video"]
    rows = {row["shot_id"]: row for row in video["plan"]}
    assert rows[own]["why"] == "current" and rows[own]["link"] == MANUAL
    assert rows[other]["link"] == FAST


def test_a_keyframe_upload_follows_the_shot_s_image_mode(store):
    from clipping.aistory import manual_uploads
    from clipping.aistory.steps import assets

    story_id = nsp.planned_story(store, profile="native_speech_manual")  # keyframes made by the app
    own, other, auto = _speaking(store, story_id)
    _set_mode(store, story_id, own, image="manual")
    _set_mode(store, story_id, auto, image="auto")
    ec = tas._ec(store, story_id)
    doc = assets._read_assets_doc(ec)
    board = {shot["shot_id"]: shot for shot in tas._board(store, story_id)["shots"]}
    assert manual_uploads.keyframe_target_refusal(ec, board[own], doc) is None
    assert manual_uploads.keyframe_target_refusal(ec, board[other], doc) == manual_uploads._images_refusal(ec.story)
    assert manual_uploads.keyframe_target_refusal(ec, board[auto], doc).startswith(
        f"Shot {auto}'s keyframe is made on ")
    assert manual_uploads.keyframe_target_refusal(ec, board[auto], doc).endswith(
        " (auto): switch it to 'my own' to upload.")


def test_a_shot_s_own_keyframe_reads_current_and_is_never_drawn_or_priced(store, api):
    """An image mode ``manual`` on a story whose keyframes the app draws:
    the shot is not priced nor drawn, it is listed as a keyframe to upload,
    its upload reads current on the episode's API image link and records no
    link for the episode."""
    import io

    from PIL import Image

    from clipping.aistory.steps import assets, sticky_link

    story_id = nsp.planned_story(api.store, profile="native_speech_manual")
    own = tas._board(api.store, story_id)["shots"][0]["shot_id"]
    before = tce._units(api.store, story_id, tas._settings(**PAID))["images"]
    _set_mode(api.store, story_id, own, image="manual")
    images = tce._units(api.store, story_id, tas._settings(**PAID))["images"]
    assert own in before["shots"] and own not in images["shots"] and images["count"] == before["count"] - 1
    buffer = io.BytesIO()
    Image.new("RGB", (1080, 1920), (200, 40, 40)).save(buffer, format="PNG")
    response = api.client.post(f"/api/stories/{story_id}/episodes/1/shots/{own}/keyframe",
                               files={"file": ("kf.png", buffer.getvalue(), "image/png")})
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "current"
    doc = tas._assets_doc(api.store, story_id)
    assert sticky_link.recorded(doc, sticky_link.IMAGE) is None
    ec = tas._ec(api.store, story_id)
    shot = next(item for item in tas._board(api.store, story_id)["shots"] if item["shot_id"] == own)
    assert assets.shot_state(ec, shot, link="fal/nano-banana-pro-edit") == "current"
    other = tas._board(api.store, story_id)["shots"][1]["shot_id"]
    refused = api.client.post(f"/api/stories/{story_id}/episodes/1/shots/{other}/keyframe",
                              files={"file": ("kf.png", buffer.getvalue(), "image/png")})
    assert refused.status_code == 400 and "made by the app" in refused.json()["detail"]["message"]


# ================================================================ (d) the route

def _current_api_clip(store, story_id, shot_id):
    """A current Veo clip on *shot_id* (its keyframe, prompt hash and file
    as the video phase records them)."""
    from clipping.aistory.steps import clips

    _keyframe(store, story_id, shot_id)
    board = tas._board(store, story_id)
    shot = next(item for item in board["shots"] if item["shot_id"] == shot_id)
    ec = tas._ec(store, story_id)
    script = tas.eps._script(store, story_id)
    flags = clips.shot_flags(shot, None)
    digest = clips.clip_request_parts(ec, shot, script, tier=3, flags=flags, link=FAST)["hash"]
    with open(store.episode_asset_path(story_id, 1, "clips", clips.clip_name(shot_id), create=True), "wb") as fh:
        fh.write(b"\x00" * 32)
    shot["assets"]["clip"] = {"state": "current", "link": FAST, "route": "paid", "clip_s": shot["clip_s"],
                              "est_usd": 0.8, "prompt_hash": digest,
                              "image_sha256": hashlib.sha256(tas.PNG).hexdigest(), "cache_key": None,
                              "generated_at": NOW}
    shot["assets"]["video"] = clips.clip_rel(shot_id)
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    return shot


def test_the_mode_route_sets_answers_and_refuses(api, monkeypatch):
    from web.api import worker

    url = "/api/stories/{}/episodes/1/shots/{}/mode"
    story_id = nsp.planned_story(api.store)
    own, other = _speaking(api.store, story_id, 2)
    _current_api_clip(api.store, story_id, own)
    page = api.client.get(f"/api/stories/{story_id}/episodes/1").json()
    shot_view = next(item for item in page["assets"]["shots"] if item["shot_id"] == own)
    assert shot_view["clip"]["state"] == "current" and "shot_modes" not in page["assets"]

    flipped = api.client.patch(url.format(story_id, own), json={"clip": "manual"})
    assert flipped.status_code == 200, flipped.text
    body = flipped.json()
    assert body["shot_id"] == own and body["modes"] == {"clip": "manual", "image": "auto"}
    assert body["states"]["clip"] == "stale" and body["stale"] == ["clip"]
    assert body["link"]["clip"] == MANUAL and body["verdict"] == {"clip": None, "image": None}
    page = api.client.get(f"/api/stories/{story_id}/episodes/1").json()
    shot_view = next(item for item in page["assets"]["shots"] if item["shot_id"] == own)
    assert shot_view["clip"]["state"] == "stale" and page["assets"]["shot_modes"] == {own: {"clip": "manual"}}

    keyless = api.client.patch(url.format(story_id, other), json={"clip": "auto"}).json()["verdict"]["clip"]
    assert keyless["allowed"] is False and "GEMINI_PAID_API_KEY is not set" in keyless["reason"]
    monkeypatch.setattr(worker, "_settings_env", tas._settings(**PAID))
    auto = api.client.patch(url.format(story_id, other), json={"clip": "auto"}).json()
    assert auto["stale"] == [] and auto["verdict"]["clip"]["link"] == FAST
    verdict = auto["verdict"]["clip"]
    assert verdict["allowed"] is True and verdict["reason"] == "paid, allowed"
    assert set(verdict) == {"link", "est_usd", "allowed", "reason"} and verdict["est_usd"] > 0

    back = api.client.patch(url.format(story_id, own), json={"clip": None}).json()
    assert back["modes"]["clip"] == "auto" and back["states"]["clip"] == "current" and back["set"] == {}

    assert api.client.patch(url.format(story_id, "sh99"), json={"clip": "auto"}).status_code == 404
    bad = api.client.patch(url.format(story_id, own), json={"clip": "robot"})
    assert bad.status_code == 400 and "'auto' or 'manual'" in json.dumps(bad.json())
    assert api.client.patch(url.format(story_id, own), json={}).status_code == 400
    api.jobs.create_job(kind=api.jobs.KIND_STORY_STEP, story_id=story_id, step="assets", ep=1, params={})
    busy = api.client.patch(url.format(story_id, own), json={"clip": "manual"})
    assert busy.status_code == 409 and "Story step 'assets'" in json.dumps(busy.json())


def test_the_mode_route_refuses_what_this_stage_does_not_make(api):
    """A clip mode on a story that is not native speech, and an image mode
    ``auto`` on a story whose images are all the human's, are refused with
    one sentence each (deviations of stage 1, recorded)."""
    url = "/api/stories/{}/episodes/1/shots/{}/mode"
    story_id = nsp.planned_story(api.store, profile="native_speech_manual")
    api.store.update(story_id, lambda doc: doc["generation_profile"].update(images="manual"), now=NOW)
    shot_id = tas._board(api.store, story_id)["shots"][0]["shot_id"]
    refused = api.client.patch(url.format(story_id, shot_id), json={"image": "auto"})
    assert refused.status_code == 400 and "Images: manual" in json.dumps(refused.json())
    api.store.update(story_id, lambda doc: doc["generation_profile"].update(budget_profile="quality"), now=NOW)
    refused = api.client.patch(url.format(story_id, shot_id), json={"clip": "manual"})
    # DEC-305 section 9 (plan 28 S2): "a native-speech story" is now said as the characters speaking in their clips.
    assert refused.status_code == 400 and "characters speak in their clips" in json.dumps(refused.json())


# ================================================================ (e) byte identity

def test_no_mode_or_a_mode_cleared_leaves_the_episode_as_it_was(store):
    """No ``shot_modes``: the estimate, the fingerprint and the document
    are what they were; a mode set then cleared leaves no key behind."""
    from clipping.aistory.steps import assets

    story_id = nsp.planned_story(store, profile="native_speech_manual")
    _three_speaking(store, story_id)
    shot_id = _speaking(store, story_id)[1]
    before_doc = tas._assets_doc(store, story_id)
    before = tce._units(store, story_id, tas._settings(**PAID))
    ec = tas._ec(store, story_id)
    script = tas.eps._script(store, story_id)
    fingerprint = assets.current_fingerprint(ec, tas._board(store, story_id), script, before_doc)
    _set_mode(store, story_id, shot_id, clip="auto", image="manual")
    moved = tas._assets_doc(store, story_id)
    assert moved["shot_modes"] == {shot_id: {"clip": "auto", "image": "manual"}}
    assert assets.current_fingerprint(ec, tas._board(store, story_id), script, moved) != fingerprint
    _set_mode(store, story_id, shot_id, clip=None, image=None)
    after_doc = tas._assets_doc(store, story_id)
    assert "shot_modes" not in after_doc
    assert {k: v for k, v in after_doc.items() if k != "updated_at"} == {
        k: v for k, v in before_doc.items() if k != "updated_at"}
    assert assets.current_fingerprint(ec, tas._board(store, story_id), script, after_doc) == fingerprint
    after = tce._units(store, story_id, tas._settings(**PAID))
    assert after["video"] == before["video"] and after["images"] == before["images"]


def test_a_mode_names_a_shot_and_one_of_two_values(store):
    from clipping.aistory import schemas

    doc = tas._assets_doc(store, nsp.planned_story(store)) or {
        "$schema": schemas.EPISODE_ASSETS_SCHEMA_NAME, "ep": 1, "lines": {}, "sfx": [], "bgm": None,
        "approved": None, "created_at": NOW, "updated_at": NOW}
    assert schemas.episode_assets_errors(dict(doc, shot_modes={"sh01": {"clip": "manual"}})) == []
    assert schemas.episode_assets_errors(dict(doc, shot_modes={"sh01": {"clip": "robot"}}))
    assert schemas.episode_assets_errors(dict(doc, shot_modes={"sh01": {}}))
    assert schemas.episode_assets_errors(dict(doc, shot_modes={"x1": {"image": "auto"}}))
    assert "shot_modes" in schemas.EPISODE_ASSETS_SHOT_MAPS


def test_the_upload_refusal_of_a_story_that_makes_its_own_clips_is_plain():
    """Plan 28 stage S2 (DEC-305 section 9): no "native-speech profile" or link id in the sentence a
    human reads; it names the spending plan to choose."""
    import types

    from clipping.aistory import manual_uploads

    ec = types.SimpleNamespace(ep=1, story={"generation_profile": {"budget_profile": "free"}})
    message = manual_uploads.upload_target_refusal(ec, {"shot_id": "sh01"}, {})
    assert message == ("Episode 1's clips are not yours to upload: the story's spending plan is not one where you make "
                       "the clips (choose \"Characters speak in your own clips\" under How it's made, Advanced).")
