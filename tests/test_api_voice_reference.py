"""The voice-reference routes (plan 23 stage B4; DEC-281): ``POST`` and
``DELETE /api/stories/{id}/characters/{char_id}/voice-reference`` and the
``GET`` the cast step reads, on a throwaway app (the stories router and the
jobs router, never the singleton) with the job store on an empty table under
``tmp_path`` and ffprobe/ffmpeg replaced by a fake (``FakeTools``: no tool is
needed and no real recording is decoded). Everything here needs the app
(pydantic, fastapi, httpx) and skips in the pytest-only CI environment, like
the other route tests.
"""

from __future__ import annotations

import json
import os
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipping.aistory import schemas, voice_reference
from clipping.aistory.store import StoryStore

NOW = "2026-10-05T10:00:00+00:00"
ROOT = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024
WAV_MIME = "audio/wav"


class FakeTools:
    def __init__(self, duration=12.0, *, streams=("audio",)):
        self.duration = duration
        self.streams = streams
        self.argvs = []

    def __call__(self, argv, **kwargs):
        self.argvs.append(list(argv))
        if argv[0] == "ffprobe":
            body = {"streams": [{"codec_type": kind} for kind in self.streams],
                    "format": {"duration": str(self.duration)}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")
        with wave.open(argv[-1], "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(24000)
            out.writeframes(b"\x00\x01" * int(self.duration * 24000))
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from web.api import store as job_store
    from web.api import worker
    from web.api.routes import jobs, stories

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "PERSIST_PATH", str(tmp_path / "jobs.json"))
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(worker, "OUTPUTS_ROOT", str(outputs))
    monkeypatch.setenv("DISABLE_AUTH", "1")

    tools = FakeTools()
    real_run = voice_reference._run
    monkeypatch.setattr(voice_reference, "_run", lambda run, argv: real_run(tools, argv))

    app = FastAPI()
    app.include_router(stories.router)
    app.include_router(jobs.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, jobs=job_store, outputs=outputs, tools=tools, monkeypatch=monkeypatch,
                              store=StoryStore(outputs, on_log=lambda line: None))


def _story(api):
    return api.store.create(language="fr", seed_text="x", now=NOW)["story_id"]


def _character(api, story_id, char_id="char_kiwilo", *, voice=None):
    doc = {
        "$schema": schemas.CHARACTER_SCHEMA_NAME, "char_id": char_id, "name": "Kiwilo", "role": "lead",
        "archetype": "x", "one_line": "x", "descriptor": None, "signature_items": [],
        "personality": {"traits": [], "wants": None, "fears": None, "speech_style": None},
        "relationships": {}, "voice": voice, "voice_hints": None,
        "refs": {"portrait": None, "turnaround": None, "expressions": None, "extra": [], "uploads": []},
        "ref_seed": None, "prompt_block": None,
        "state": {"alive": True, "location": None, "arc_notes": []},
        "source": "custom", "approved_at": None, "created_at": NOW, "updated_at": NOW,
    }
    api.store.write_entity(story_id, "characters", doc, now=NOW, validator=schemas.character_errors)
    return char_id


def _url(story_id, suffix=""):
    return f"/api/stories/{story_id}/characters/char_kiwilo/voice-reference{suffix}"


def _post(api, story_id, body=b"RIFFrecording", *, consent="true", char_id="char_kiwilo", field="file"):
    url = f"/api/stories/{story_id}/characters/{char_id}/voice-reference"
    if consent is not None:
        url += f"?consent={consent}"
    return api.client.post(url, files={field: ("my voice.m4a", body, "audio/mp4")})


def _folder(api, story_id) -> Path:
    return api.outputs / "stories" / story_id / "characters" / "char_kiwilo"


def _running_step(api, story_id, status="running"):
    from web.api.models import JobStatus

    job_id = api.jobs.create_job(kind="story_step", story_id=story_id, step="cast", params=None)
    api.jobs.set_status(job_id, JobStatus(status))
    return job_id


# -------------------------------------------------------------------- upload

def test_a_recording_is_accepted_stored_and_played_back(api):
    story_id = _story(api)
    _character(api, story_id)

    response = _post(api, story_id)

    assert response.status_code == 201, response.text
    entry = response.json()
    assert entry["name"] == "voice_reference.wav" and entry["consent"] is True and entry["duration_s"] == 12.0
    assert len(entry["sha256"]) == 64 and entry["uploaded_at"]
    kiwi = api.store.read_entity(story_id, "characters", "char_kiwilo")
    assert kiwi["voice_reference"] == entry
    # The original file name is nowhere; only the re-encoded file is kept.
    assert sorted(p.name for p in _folder(api, story_id).iterdir()) == ["character.json", "voice_reference.wav"]
    served = api.client.get(f"/api/stories/{story_id}/media/characters/char_kiwilo/voice_reference.wav")
    assert served.status_code == 200 and served.headers["content-type"] == WAV_MIME
    assert served.content[:4] == b"RIFF"

    state = api.client.get(_url(story_id))
    assert state.status_code == 200
    assert state.json()["voice_reference"] == entry and state.json()["pinned"] is False
    assert set(state.json()["engine"]) == {"ready", "reason"}


def test_without_consent_it_is_a_400_and_the_body_is_never_read(api):
    story_id = _story(api)
    _character(api, story_id)

    for consent in (None, "false"):
        response = _post(api, story_id, consent=consent)
        assert response.status_code == 400
        assert "permission" in response.json()["detail"]["message"]
    assert api.tools.argvs == []
    assert [p.name for p in _folder(api, story_id).iterdir()] == ["character.json"]
    assert "voice_reference" not in api.store.read_entity(story_id, "characters", "char_kiwilo")


def test_a_body_over_the_cap_is_a_413_declared_or_streamed(api):
    story_id = _story(api)
    _character(api, story_id)

    response = _post(api, story_id, body=b"x" * (voice_reference.MAX_UPLOAD_BYTES + 1))

    assert response.status_code == 413
    assert "10 MB" in response.json()["detail"]["message"]
    assert [p.name for p in _folder(api, story_id).iterdir()] == ["character.json"]
    assert api.tools.argvs == []


def test_a_file_without_audio_is_a_415(api):
    story_id = _story(api)
    _character(api, story_id)
    api.tools.streams = ("video",)

    response = _post(api, story_id)

    assert response.status_code == 415
    assert "no audio" in response.json()["detail"]["message"]
    assert [p.name for p in _folder(api, story_id).iterdir()] == ["character.json"]


@pytest.mark.parametrize("duration", [4.0, 31.0])
def test_a_recording_outside_five_to_thirty_seconds_is_a_400(api, duration):
    story_id = _story(api)
    _character(api, story_id)
    api.tools.duration = duration

    response = _post(api, story_id)

    assert response.status_code == 400 and "5 to 30 seconds" in response.json()["detail"]["message"]
    assert [p.name for p in _folder(api, story_id).iterdir()] == ["character.json"]


def test_a_form_with_no_file_is_a_400(api):
    story_id = _story(api)
    _character(api, story_id)
    response = _post(api, story_id, field="other")
    assert response.status_code == 400


def test_while_a_step_runs_the_upload_and_the_delete_are_409(api):
    story_id = _story(api)
    _character(api, story_id)
    assert _post(api, story_id).status_code == 201
    running = _running_step(api, story_id)

    response = _post(api, story_id)
    assert response.status_code == 409 and running in response.json()["detail"]
    response = api.client.delete(_url(story_id))
    assert response.status_code == 409 and running in response.json()["detail"]
    # Nothing moved.
    assert "voice_reference" in api.store.read_entity(story_id, "characters", "char_kiwilo")
    assert (_folder(api, story_id) / "voice_reference.wav").is_file()


def test_an_unknown_story_or_character_is_a_404(api):
    story_id = _story(api)
    _character(api, story_id)
    assert _post(api, story_id, char_id="char_nobody").status_code == 404
    assert _post(api, "0123456789ab").status_code == 404
    assert api.client.delete(f"/api/stories/{story_id}/characters/char_nobody/voice-reference").status_code == 404
    assert api.client.get(f"/api/stories/{story_id}/characters/char_nobody/voice-reference").status_code == 404


# -------------------------------------------------------------------- delete

def test_the_recording_is_removed_with_its_entry(api):
    story_id = _story(api)
    _character(api, story_id)
    assert _post(api, story_id).status_code == 201

    response = api.client.delete(_url(story_id))

    assert response.status_code == 200
    assert response.json() == {"name": "voice_reference.wav", "entry_removed": True, "file_removed": True}
    assert [p.name for p in _folder(api, story_id).iterdir()] == ["character.json"]
    assert "voice_reference" not in api.store.read_entity(story_id, "characters", "char_kiwilo")
    assert api.client.delete(_url(story_id)).status_code == 404


def test_the_delete_is_refused_while_the_voice_is_pinned_to_the_reference(api):
    story_id = _story(api)
    pinned = {"provider": "chatterbox", "voice_id": "reference", "rate": None, "pitch": None,
              "direction": "d", "sample_line": "Salut !"}
    _character(api, story_id, voice=pinned)
    assert _post(api, story_id).status_code == 201

    response = api.client.delete(_url(story_id))

    assert response.status_code == 409
    assert "pick another voice first" in response.json()["detail"]["message"]
    assert (_folder(api, story_id) / "voice_reference.wav").is_file()
    assert "voice_reference" in api.store.read_entity(story_id, "characters", "char_kiwilo")
    assert api.client.get(_url(story_id)).json()["pinned"] is True


def test_the_probe_reason_reaches_the_cast_step_when_chatterbox_is_missing(api):
    from clipping.providers import tts

    story_id = _story(api)
    _character(api, story_id)
    api.monkeypatch.setattr(tts, "_installed", lambda name: False)

    engine = api.client.get(_url(story_id)).json()["engine"]

    assert engine["ready"] is False and "chatterbox is not installed" in engine["reason"]
    api.monkeypatch.setattr(tts, "_installed", lambda name: True)
    assert api.client.get(_url(story_id)).json()["engine"]["ready"] is True


def test_the_routes_are_behind_the_token(api):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from web.api import auth
    from web.api.routes import stories

    story_id = _story(api)
    _character(api, story_id)
    api.monkeypatch.setenv("API_TOKEN", "test-token-12345")
    api.monkeypatch.delenv("DISABLE_AUTH", raising=False)
    api.monkeypatch.setattr(auth, "_TOKEN", None)
    app = FastAPI()
    app.include_router(stories.router)
    with TestClient(app) as client:
        assert client.post(_url(story_id) + "?consent=true",
                           files={"file": ("a.wav", b"x" * 100, "audio/wav")}).status_code == 401
        assert client.delete(_url(story_id)).status_code == 401
        assert client.get(_url(story_id)).status_code == 401
    api.monkeypatch.setattr(auth, "_TOKEN", None)
    assert [p.name for p in _folder(api, story_id).iterdir()] == ["character.json"]
