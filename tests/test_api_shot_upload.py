"""The upload routes of the manual link (plan 22, stage 5).

``POST /api/stories/{id}/episodes/{ep}/shots/{shot_id}/clip`` takes the
human's own clip (multipart, field ``file``): checked with ffprobe -- a video
stream, at least 2 s, 9:16 within 2 %, a sound track on a speaking shot --
stored as ``assets/clips/shot_NN.manual.mp4`` (a clip already there kept in
``assets/clips/takes/``), recorded with ``link: manual/upload`` and taken
(a fake transcriber here). A refusal names its reason (400). ``GET
.../brief`` and ``.../brief.zip`` hand the shot brief out. No auth exists on
this app by design.

Real ffmpeg on tiny fixture clips; no provider call.
"""

from __future__ import annotations

import io
import os
import zipfile
from types import SimpleNamespace

import pytest

import test_story_assets_step as tas
import test_story_native_speech_plan as nsp
import test_story_native_take as tnt
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)

PROFILE = "native_speech_manual"


@pytest.fixture
def api(monkeypatch, tmp_path, store):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipping.aistory.steps import assets
    from web.api import store as job_store
    from web.api import worker
    from web.api.routes import jobs, stories

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(worker, "_settings_env", tas._settings())
    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "PERSIST_PATH", str(tmp_path / "jobs.json"))
    outputs = tmp_path / "outputs"
    outputs.mkdir(exist_ok=True)
    monkeypatch.setattr(worker, "OUTPUTS_ROOT", str(outputs))
    monkeypatch.setattr(worker, "UPLOADS_ROOT", str(tmp_path / "uploads"))
    submitted = []

    async def fake_submit(job_id, payload):
        submitted.append(job_id)

    monkeypatch.setattr(worker, "submit_job", fake_submit)
    monkeypatch.setenv("DISABLE_AUTH", "1")
    heard = {}

    def fake_default(env):
        def transcribe(path, *, language, on_log, cancel):
            return heard.get("words", []), "groq/whisper-large-v3-turbo"
        return transcribe, None

    monkeypatch.setattr(assets, "default_transcriber", fake_default)
    app = FastAPI()
    app.include_router(stories.router)
    app.include_router(jobs.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, jobs=job_store, submitted=submitted, store=store, heard=heard,
                              tmp_path=tmp_path)


def _manual(store):
    tnt._require_ffmpeg()
    return nsp.planned_story(store, profile=PROFILE)


def _shot(store, story_id, *, speaks=True):
    return next(shot for shot in tas._board(store, story_id)["shots"] if bool(shot.get("speaks")) == speaks)


def _line_text(store, story_id, shot):
    script = tas.eps._script(store, story_id)
    return next(line["text"] for scene in script["scenes"] for line in scene["lines"] if line["line_id"] == shot["lines"][0])


def _post(api, story_id, shot_id, path, name="take.mp4"):
    with open(path, "rb") as fh:
        return api.client.post(f"/api/stories/{story_id}/episodes/1/shots/{shot_id}/clip",
                               files={"file": (name, fh, "video/mp4")})


def _clip(tmp_path, name, seconds, *, size="64x112", sound=True):
    import subprocess

    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"testsrc=size={size}:rate=24:duration={seconds:g}"]
    if sound:
        argv += ["-f", "lavfi", "-i", f"sine=frequency=800:sample_rate=48000:duration={seconds:g}"]
    argv += ["-c:v", "libx264", "-pix_fmt", "yuv420p"] + (["-c:a", "aac"] if sound else ["-an"])
    path = os.fspath(tmp_path / name)
    result = subprocess.run(argv + [path], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-400:]
    return path


# ================================================================ a valid clip

def test_a_valid_clip_is_stored_recorded_and_taken(api, monkeypatch):
    """Fail-first: the route did not exist. The clip lands as
    shot_NN.manual.mp4 with link manual/upload, \\$0, its sha256 and real
    length; the take hears the line; the response says what is missing."""
    from clipping.aistory.steps import assets

    story_id = _manual(api.store)
    shot = _shot(api.store, story_id)
    api.heard["words"] = tnt.words(_line_text(api.store, story_id, shot), start=1.0)
    hashed = []
    real_sha = assets._sha256_file
    monkeypatch.setattr(assets, "_sha256_file", lambda path: hashed.append(str(path)) or real_sha(path))
    response = _post(api, story_id, shot["shot_id"], _clip(api.tmp_path, "ok.mp4", 8), name="Rouge take 2.mp4")
    assert response.status_code == 201, response.text
    body = response.json()
    clip = body["clip"]
    assert (clip["link"], clip["route"], clip["state"], clip["est_usd"]) == ("manual/upload", "manual", "current", 0.0)
    assert clip["filename"] == "Rouge take 2.mp4" and len(clip["sha256"]) == 64
    assert clip["duration_s"] == pytest.approx(8.0, abs=0.05) and clip["clip_s"] == shot["clip_s"]
    assert body["state"] == "take_ok" and body["take"]["state"] == "ok" and body["replaced"] is False
    stored = next(item for item in tas._board(api.store, story_id)["shots"] if item["shot_id"] == shot["shot_id"])
    assert stored["assets"]["video"] == f"assets/clips/shot_{shot['shot_id'][2:]}.manual.mp4"
    assert body["missing"] and all(item["shot_id"] != shot["shot_id"] for item in body["missing"])
    assert body["waiting"].startswith(f"Waiting for {len(body['missing'])} clips")
    # The stored clip is hashed once, as it is stored: the take reads that sha256.
    assert not [path for path in hashed if path.endswith(".manual.mp4")]
    # Nothing booked: a manual clip is never a ledger row.
    ledger = tas._ledger(api.store, story_id)
    assert not [row for row in ledger if row.get("unit") == "second"]


def test_a_new_upload_replaces_the_clip_and_keeps_the_old_take(api):
    story_id = _manual(api.store)
    shot = _shot(api.store, story_id, speaks=False)
    assert _post(api, story_id, shot["shot_id"], _clip(api.tmp_path, "a.mp4", 4)).status_code == 201
    first = next(item for item in tas._board(api.store, story_id)["shots"] if item["shot_id"] == shot["shot_id"])
    response = _post(api, story_id, shot["shot_id"], _clip(api.tmp_path, "b.mp4", 6))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["replaced"] is True and body["clip"]["sha256"] != first["assets"]["clip"]["sha256"]
    takes = api.store.episode_take_path(story_id, 1, f"shot_{shot['shot_id'][2:]}.manual.20260101T000000Z.mp4")
    kept = os.listdir(os.path.dirname(takes))
    assert len(kept) == 1 and kept[0].startswith(f"shot_{shot['shot_id'][2:]}.manual.")
    # A silent clip keeps its real length.
    assert body["duration_s"] == pytest.approx(6.0, abs=0.05)


# ================================================================ refusals

@pytest.mark.parametrize("make,speaks,words", [
    (lambda tmp: _audio_only(tmp), True, "no video stream"),
    (lambda tmp: _clip(tmp, "short.mp4", 1), False, "at least 2 s"),
    (lambda tmp: _clip(tmp, "wide.mp4", 4, size="160x90"), False, "not 9:16"),
    (lambda tmp: _clip(tmp, "mute.mp4", 4, sound=False), True, "no sound track"),
])
def test_an_off_spec_clip_is_refused_with_its_reason(api, make, speaks, words):
    story_id = _manual(api.store)
    shot = _shot(api.store, story_id, speaks=speaks)
    response = _post(api, story_id, shot["shot_id"], make(api.tmp_path))
    assert response.status_code == 400, response.text
    assert words in response.json()["detail"]["message"]
    stored = next(item for item in tas._board(api.store, story_id)["shots"] if item["shot_id"] == shot["shot_id"])
    assert stored["assets"].get("clip") is None and stored["assets"]["video"] is None
    folder = os.path.dirname(api.store.episode_asset_path(story_id, 1, "clips", "shot_01.mp4"))
    assert [name for name in os.listdir(folder) if not os.path.isdir(os.path.join(folder, name))] == []


def _audio_only(tmp_path):
    import subprocess

    path = os.fspath(tmp_path / "voice.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=500:sample_rate=48000:duration=3", "-c:a", "aac", path], check=True)
    return path


def test_a_shot_that_is_not_yours_to_upload_is_refused(api):
    tnt._require_ffmpeg()
    story_id = nsp.planned_story(api.store)  # native_speech: its clips are bought on Veo
    shot = _shot(api.store, story_id)
    response = _post(api, story_id, shot["shot_id"], _clip(api.tmp_path, "x.mp4", 8))
    assert response.status_code == 400 and "not manual/upload" in response.json()["detail"]["message"]
    assert _post(api, story_id, "sh99", _clip(api.tmp_path, "y.mp4", 8)).status_code == 404


# ================================================================ the brief routes

def test_the_brief_routes_hand_out_the_json_and_the_zip(api):
    story_id = _manual(api.store)
    response = api.client.get(f"/api/stories/{story_id}/episodes/1/brief?platform=higgsfield")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["platform"]["platform"] == "higgsfield" and body["counts"]["missing"] == body["counts"]["total"]
    assert body["zip_url"].endswith("/brief.zip?platform=higgsfield")
    assert body["files"]["md"].endswith("assets/brief/shot_brief.md")
    assert api.client.get(f"/api/stories/{story_id}/episodes/1/brief?platform=sora").status_code == 400
    response = api.client.get(f"/api/stories/{story_id}/episodes/1/brief.zip")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert {"shot_brief.md", "shot_brief.json"} <= set(names)


# ================================================================ resume

def test_the_last_missing_clip_starts_the_paused_run_again(api):
    """A job paused awaiting the user's clips follows the count while clips
    are missing; the upload that leaves none missing starts the same step
    again as a new job and completes the paused one (``resumed_by``)."""
    import shutil

    from clipping.aistory import manual_uploads

    story_id = _manual(api.store)
    shots = tas._board(api.store, story_id)["shots"]
    source = _clip(api.tmp_path, "every.mp4", 8)
    folder = manual_uploads.clips_folder(api.store, story_id, 1)
    for number, shot in enumerate(shots[:-2]):
        received = f"{folder}/.upload-{number}.part"
        shutil.copyfile(source, received)
        manual_uploads.accept_clip(api.store, story_id, 1, shot["shot_id"], received, filename="t.mp4",
                                   env=tas._settings())
    paused = api.jobs.create_job(kind=api.jobs.KIND_STORY_STEP, story_id=story_id, step="fast-track", ep=1,
                                 params={"storyboard": "fast"})
    api.jobs.update_job(paused, status="awaiting_uploads", uploads={"count": 2})

    first = _post(api, story_id, shots[-2]["shot_id"], source).json()
    assert first["resumed"] == {"waiting": "Waiting for 1 clip — download the brief", "jobs": [paused]}
    assert api.jobs.get_job(paused)["uploads"]["count"] == 1 and api.submitted == []

    last = _post(api, story_id, shots[-1]["shot_id"], source).json()
    assert last["missing"] == [] and last["waiting"] is None
    new_id = last["resumed"]["job_id"]
    assert last["resumed"]["step"] == "fast-track" and api.submitted == [new_id]
    job = api.jobs.get_job(new_id)
    assert (job["step"], job["ep"], job["params"], job["status"]) == ("fast-track", 1, {"storyboard": "fast"}, "queued")
    old = api.jobs.get_job(paused)
    assert (old["status"], old["resumed_by"]) == ("completed", new_id)


# ================================================================ your own images

def _png(tmp_path, name, size):
    image_module = pytest.importorskip("PIL.Image")
    path = tmp_path / name
    image_module.new("RGB", size, (200, 40, 60)).save(path, format="PNG")
    return os.fspath(path)


def _post_image(api, path, url):
    with open(path, "rb") as fh:
        return api.client.post(url, files={"file": (os.path.basename(path), fh, "image/png")})


def _manual_images(store):
    story_id = _manual(store)
    store.update(story_id, lambda doc: doc["generation_profile"].update(images="manual"), now=tas.NOW)
    return story_id


def test_your_own_sheets_plates_props_and_keyframes_are_stored_as_made_ones(api):
    story_id = _manual_images(api.store)
    shot_id = tas._board(api.store, story_id)["shots"][0]["shot_id"]
    keyframe = _post_image(api, _png(api.tmp_path, "kf.png", (1080, 1920)),
                           f"/api/stories/{story_id}/episodes/1/shots/{shot_id}/keyframe")
    assert keyframe.status_code == 201, keyframe.text
    body = keyframe.json()
    assert body["state"] == "current" and body["size"] == [1080, 1920]
    assert all(item["shot_id"] != shot_id for item in body["missing"]) and body["missing"]
    assert body["waiting"].startswith(f"Waiting for {len(body['missing'])} keyframes")
    stored = tas._board(api.store, story_id)["shots"][0]["assets"]
    assert (stored["provider"], stored["model"], stored["route"], stored["est_usd"]) == ("manual", "upload",
                                                                                       "free", 0.0)
    brief = api.client.get(f"/api/stories/{story_id}/image-brief?ep=1").json()
    assert {entry["kind"] for entry in brief["images"]} == {"sheet", "plate", "prop", "keyframe"}
    assert all(entry["upload_slot"] and entry["prompt"] for entry in brief["images"])

    sheet = _post_image(api, _png(api.tmp_path, "rouge.png", (720, 1280)),
                        f"/api/stories/{story_id}/cast/char_kiwilo/sheet?which=portrait")
    assert sheet.status_code == 201, sheet.text
    assert sheet.json()["ref"] == {"name": "portrait.png", "consistency": "base", "source": "manual/upload",
                                   "seed": None, "created_at": sheet.json()["ref"]["created_at"]}
    portrait = api.store.read_entity(story_id, "characters", "char_kiwilo")["refs"]["portrait"]
    assert portrait["source"] == "manual/upload"
    plate = _post_image(api, _png(api.tmp_path, "night.png", (720, 1280)),
                        f"/api/stories/{story_id}/places/place_le_parloir_des_secrets/plate?variant=night")
    assert plate.status_code == 201 and plate.json()["ref"]["consistency"] == "references"
    prop = _post_image(api, _png(api.tmp_path, "phone.png", (1024, 1024)),
                       f"/api/stories/{story_id}/props/{tas.eps.PHONE}/image")
    assert prop.status_code == 201, prop.text


def test_an_image_upload_is_refused_with_its_reason(api):
    story_id = _manual_images(api.store)
    small = _post_image(api, _png(api.tmp_path, "small.png", (200, 300)),
                        f"/api/stories/{story_id}/cast/char_kiwilo/sheet?which=portrait")
    assert small.status_code == 400 and "at least 360x640" in small.json()["detail"]["message"]
    shot_id = tas._board(api.store, story_id)["shots"][0]["shot_id"]
    wide = _post_image(api, _png(api.tmp_path, "wide.png", (1920, 1080)),
                       f"/api/stories/{story_id}/episodes/1/shots/{shot_id}/keyframe")
    assert wide.status_code == 400 and "not 9:16" in wide.json()["detail"]["message"]
    text = api.tmp_path / "notes.png"
    text.write_text("not an image", encoding="utf-8")
    bad = _post_image(api, os.fspath(text), f"/api/stories/{story_id}/props/{tas.eps.PHONE}/image")
    assert bad.status_code == 415
    # A story whose images are the app's own takes none.
    app_story = _manual(api.store)
    refused = _post_image(api, _png(api.tmp_path, "p.png", (720, 1280)),
                          f"/api/stories/{app_story}/cast/char_kiwilo/sheet?which=portrait")
    assert refused.status_code == 400 and "made by the app" in refused.json()["detail"]["message"]


def test_a_variant_sheet_upload_fills_the_variant_slot_and_never_the_base(api):
    """Plan 23 D5 follow-up: ``?variant=<vid>`` on the sheet route stores
    ``refs/<which>_<vid>.png`` in that variant's slot (an edit of the
    portrait: ``references``), clears the variant's approval and leaves the
    character's own sheets alone; an unknown variant is one plain sentence."""
    from clipping.aistory import workflow

    story_id = _manual_images(api.store)
    api.store.update(story_id, lambda doc: doc["generation_profile"].update(sheet_mode="three_sheet"), now=tas.NOW)
    workflow.add_variant(api.store, story_id, "char_kiwilo",
                         {"label": "Ghost version", "delta_text": "a translucent pale-blue glowing ghost"}, now=tas.NOW)
    doc = api.store.read_entity(story_id, "characters", "char_kiwilo")
    doc["variants"][0]["approved_at"] = tas.NOW
    api.store.write_entity(story_id, "characters", doc, now=tas.NOW)
    base_refs = doc["refs"]

    url = f"/api/stories/{story_id}/cast/char_kiwilo/sheet?which=portrait&variant=ghost_version"
    done = _post_image(api, _png(api.tmp_path, "ghost.png", (720, 1280)), url)
    assert done.status_code == 201, done.text
    assert done.json()["ref"] == {"name": "portrait_ghost_version.png", "consistency": "references",
                                  "source": "manual/upload", "seed": None,
                                  "created_at": done.json()["ref"]["created_at"]}
    assert done.json()["variant_id"] == "ghost_version"
    after = api.store.read_entity(story_id, "characters", "char_kiwilo")
    variant = after["variants"][0]
    assert variant["refs"]["portrait"]["name"] == "portrait_ghost_version.png" and variant["approved_at"] is None
    assert after["refs"] == base_refs
    assert os.path.isfile(api.store.media_path(story_id, "characters", "char_kiwilo", "portrait_ghost_version.png"))

    unknown = _post_image(api, _png(api.tmp_path, "nope.png", (720, 1280)),
                          f"/api/stories/{story_id}/cast/char_kiwilo/sheet?which=portrait&variant=nope")
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["message"] == "Kiwilo has no appearance variant 'nope'."
    assert api.store.read_entity(story_id, "characters", "char_kiwilo") == after


def test_a_step_started_while_the_clip_arrives_refuses_it_and_writes_nothing(api, monkeypatch):
    """The in-flight check runs again once the body is in, right before the
    first write: a step queued meanwhile (Continue, or a parallel upload
    that resumed the run) answers 409 with the busy wording, the received
    file is deleted, the storyboard and the clips folder are untouched."""
    from web.api.routes import stories as routes

    story_id = _manual(api.store)
    shot = _shot(api.store, story_id)
    before = tas._board(api.store, story_id)
    real_receive = routes._receive_upload

    async def receive_then_start_a_step(*args, **kwargs):
        received = await real_receive(*args, **kwargs)
        api.jobs.create_job(kind=api.jobs.KIND_STORY_STEP, story_id=story_id, step="assets", ep=1, params={})
        return received

    monkeypatch.setattr(routes, "_receive_upload", receive_then_start_a_step)
    response = _post(api, story_id, shot["shot_id"], _clip(api.tmp_path, "late.mp4", 8))
    assert response.status_code == 409, response.text
    message = response.json()["detail"]["message"]
    assert "Story step 'assets'" in message and "upload the clip once that step is done" in message
    assert tas._board(api.store, story_id) == before
    folder = os.path.dirname(api.store.episode_asset_path(story_id, 1, "clips", "shot_01.mp4"))
    assert [name for name in os.listdir(folder) if not os.path.isdir(os.path.join(folder, name))] == []
