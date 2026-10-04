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

def test_a_valid_clip_is_stored_recorded_and_taken(api):
    """Fail-first: the route did not exist. The clip lands as
    shot_NN.manual.mp4 with link manual/upload, \\$0, its sha256 and real
    length; the take hears the line; the response says what is missing."""
    story_id = _manual(api.store)
    shot = _shot(api.store, story_id)
    api.heard["words"] = tnt.words(_line_text(api.store, story_id, shot), start=1.0)
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
