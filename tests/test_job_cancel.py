"""Cancelling a job stops it: its ffmpeg dies, and it ends CANCELLED.

Before, DELETE /api/jobs/{id} set the status and dropped the record while the
worker thread carried on -- the remaining provider requests, transcription and
render all still ran. Now:

- POST /api/jobs/{id}/cancel sets the job's CancelToken (the pipeline checks it
  before each step, see test_cancel.py) and kills the job's child processes.
- web/api/children.py attributes each subprocess to the job whose thread
  spawned it, the way the stdout tee attributes each printed line, so the
  render layer's dozen ffmpeg call sites need no change.
- A cancelled job is terminal. The worker's late writes -- status, error,
  clips, progress -- are dropped under the store lock, so a job ends either
  COMPLETED (the cancel is refused) or CANCELLED, never FAILED.

The children tests use real processes and only the stdlib, so they run in the
pytest-only CI environment; the store, worker and route tests skip without
pydantic / fastapi (DEC-012).
"""

import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from clipping.cancel import Cancelled, CancelToken
from web.api import children

SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]


@pytest.fixture
def tracked():
    children.install()
    yield children
    children.uninstall()


# ------------------------------------------------------------- children (CI)

def test_a_jobs_child_is_killed_by_its_cancel(tracked):
    token = CancelToken()
    with tracked.attributed("job1", token):
        proc = subprocess.Popen(SLEEPER)
    started = time.monotonic()
    token.cancel()
    assert tracked.kill("job1") == 1
    proc.wait(timeout=10)
    assert time.monotonic() - started < 10
    assert proc.returncode != 0


def test_a_spawn_on_a_cancelled_job_never_starts(tracked, tmp_path):
    marker = tmp_path / "ran"
    token = CancelToken()
    token.cancel()
    with tracked.attributed("job1", token), pytest.raises(Cancelled):
        subprocess.run([sys.executable, "-c", f"open({str(marker)!r}, 'w')"])
    assert not marker.exists()


def test_other_jobs_and_unattributed_threads_are_untouched(tracked):
    mine, other = CancelToken(), CancelToken()
    with tracked.attributed("mine", mine):
        doomed = subprocess.Popen(SLEEPER)
    with tracked.attributed("other", other):
        bystander = subprocess.Popen(SLEEPER)
    outsider = subprocess.Popen(SLEEPER)
    try:
        mine.cancel()
        tracked.kill("mine")
        doomed.wait(timeout=10)
        time.sleep(0.2)
        assert bystander.poll() is None
        assert outsider.poll() is None
    finally:
        for proc in (doomed, bystander, outsider):
            proc.kill()
            proc.wait(timeout=10)


def test_a_cancel_racing_the_spawn_still_kills_the_child(tracked):
    """The token is checked before the process starts and again once it is
    registered. A cancel landing between the two -- after the first check,
    before kill() could see the child -- is caught by the second."""

    class RacingToken(CancelToken):
        def check(self):  # the pre-spawn check passes...
            self.cancel()  # ...and the cancel lands right after it

    token = RacingToken()
    with tracked.attributed("job1", token):
        proc = subprocess.Popen(SLEEPER)
    proc.wait(timeout=10)
    assert proc.returncode != 0


def test_install_is_idempotent_and_uninstall_restores(tracked):
    tracked.install()
    assert subprocess.Popen is not children.ORIGINAL_POPEN
    tracked.uninstall()
    assert subprocess.Popen is children.ORIGINAL_POPEN
    tracked.install()


# ------------------------------------------------------------- the store

@pytest.fixture
def store(monkeypatch):
    pytest.importorskip("pydantic")
    from web.api import store as job_store

    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "_persist", lambda force=True: None)
    return job_store


def _status(store, job_id):
    return store.get_job(job_id)["status"]


def test_a_running_job_can_be_cancelled(store):
    from web.api.models import JobStatus

    store.create_job(job_id="j1")
    store.set_status("j1", JobStatus.RENDERING)
    assert store.request_cancel("j1") == "cancelled"
    assert _status(store, "j1") == JobStatus.CANCELLED.value


@pytest.mark.parametrize("final", ["completed", "failed", "cancelled"])
def test_a_finished_job_cannot_be_cancelled(store, final):
    store.create_job(job_id="j1")
    store._jobs["j1"]["status"] = final
    assert store.request_cancel("j1") == "terminal"
    assert _status(store, "j1") == final


def test_a_missing_job_is_reported(store):
    assert store.request_cancel("nope") == "missing"


def test_late_worker_writes_do_not_undo_a_cancel(store):
    from web.api.models import JobStatus

    store.create_job(job_id="j1")
    store.set_status("j1", JobStatus.ANALYZING)
    store.update_progress("j1", "analyze", 3, 7, "asking", 36.0)
    store.request_cancel("j1")

    store.set_status("j1", JobStatus.RENDERING)
    store.set_error("j1", "RuntimeError: ffmpeg exited 255")
    store.set_clips("j1", [])
    store.update_progress("j1", "render", 6, 7, "rendering", 60.0)
    store.refine_progress("j1", detail="a late line")

    job = store.get_job("j1")
    assert job["status"] == JobStatus.CANCELLED.value
    assert job["error"] is None
    assert job["progress"].step == "analyze"
    assert job["progress"].detail != "a late line"


def test_a_cancelled_job_still_records_events_and_flags(store):
    store.create_job(job_id="j1")
    store.request_cancel("j1")
    store.append_event("j1", "Cancelled.", "warning", "worker")
    store.update_job("j1", delete_requested=True)
    job = store.get_job("j1")
    assert job["events"][-1]["message"] == "Cancelled."
    assert job["delete_requested"] is True


def test_a_completion_that_wins_the_race_refuses_the_cancel(store):
    from web.api.models import JobStatus

    store.create_job(job_id="j1")
    store.set_clips("j1", [])
    assert store.request_cancel("j1") == "terminal"
    assert _status(store, "j1") == JobStatus.COMPLETED.value


# ------------------------------------------------------------- the worker

@pytest.fixture
def worker(store, monkeypatch, tmp_path):
    from web.api import worker as worker_mod

    video = tmp_path / "talk.mp4"
    video.write_bytes(b"\x00")
    cfg = SimpleNamespace(ai_provider="chain", outputs_dir=str(tmp_path),
                          load_gemini_json=False, file_video_asli=str(video),
                          transcript_path=None)
    built = []

    def build(payload, job_id, env_overrides=None):
        built.append(job_id)
        return cfg

    monkeypatch.setattr(worker_mod, "build_config_from_payload", build)
    monkeypatch.setattr("clipping.config.missing_provider_key", lambda cfg: None)
    monkeypatch.setattr("clipping.config.chain_not_ready", lambda cfg, hint=None: None)
    monkeypatch.setattr("clipping.config.preflight_chain", lambda cfg, on_log=None: None)
    worker_mod.built = built
    worker_mod.test_cfg = cfg
    return worker_mod


def test_a_job_cancelled_while_queued_never_builds_its_config(worker, store):
    from web.api.models import JobStatus

    store.create_job(job_id="j1")
    token = CancelToken()
    store.request_cancel("j1")
    token.cancel()

    worker._run_pipeline_sync("j1", {}, token)

    assert worker.built == []
    assert _status(store, "j1") == JobStatus.CANCELLED.value


def test_a_cancel_mid_transcription_ends_cancelled_not_failed(worker, store, monkeypatch):
    from web.api.models import JobStatus

    store.create_job(job_id="j1")
    token = CancelToken()

    def transcribe_then_get_cancelled(cfg):
        assert cfg.cancel_token is token
        store.request_cancel("j1")
        token.cancel()
        # What a killed ffmpeg looks like to the code that ran it.
        raise RuntimeError("ffmpeg exited with 255")

    monkeypatch.setattr("clipping.runner.resolve_transcript", transcribe_then_get_cancelled)

    worker._run_pipeline_sync("j1", {}, token)

    job = store.get_job("j1")
    assert job["status"] == JobStatus.CANCELLED.value
    assert job["error"] is None
    assert any("cancel" in e["message"].lower() for e in job["events"])


def test_the_pipelines_cancelled_exception_ends_cancelled(worker, store, monkeypatch):
    from web.api.models import JobStatus

    store.create_job(job_id="j1")
    token = CancelToken()

    def cancelled(cfg):
        store.request_cancel("j1")
        token.cancel()
        token.check()

    monkeypatch.setattr("clipping.runner.resolve_transcript", cancelled)
    worker._run_pipeline_sync("j1", {}, token)
    assert _status(store, "j1") == JobStatus.CANCELLED.value


def test_a_genuine_failure_is_still_a_failure(worker, store, monkeypatch):
    from web.api.models import JobStatus

    store.create_job(job_id="j1")

    def boom(cfg):
        raise RuntimeError("disk full")

    monkeypatch.setattr("clipping.runner.resolve_transcript", boom)
    worker._run_pipeline_sync("j1", {}, CancelToken())
    job = store.get_job("j1")
    assert job["status"] == JobStatus.FAILED.value
    assert "disk full" in job["error"]


def test_worker_cancel_sets_the_token_and_kills_children(worker, monkeypatch):
    token = CancelToken()
    killed = []
    monkeypatch.setattr(worker.children, "kill", lambda job_id: killed.append(job_id) or 0)
    monkeypatch.setitem(worker._active, "j1", (token, None))
    assert worker.cancel("j1") is True
    assert token.cancelled and killed == ["j1"]
    assert worker.cancel("not-running") is False


# ------------------------------------------------------------- the routes

@pytest.fixture
def client(store, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.api import worker as worker_mod
    from web.api.routes import jobs

    monkeypatch.setenv("DISABLE_AUTH", "1")
    cancelled = []
    monkeypatch.setattr(worker_mod, "cancel", lambda job_id: cancelled.append(job_id) or True)
    app = FastAPI()
    app.include_router(jobs.router)
    with TestClient(app) as test_client:
        test_client.cancelled = cancelled
        yield test_client


def test_the_cancel_route_stops_a_running_job(client, store):
    from web.api.models import JobStatus

    store.create_job(job_id="j1")
    store.set_status("j1", JobStatus.RENDERING)
    response = client.post("/api/jobs/j1/cancel")
    assert response.status_code == 202
    assert response.json()["status"] == "cancelled"
    assert client.cancelled == ["j1"]


def test_cancelling_a_finished_job_is_a_conflict(client, store):
    store.create_job(job_id="j1")
    store.set_clips("j1", [])
    response = client.post("/api/jobs/j1/cancel")
    assert response.status_code == 409
    assert client.cancelled == []


def test_cancelling_an_unknown_job_is_not_found(client):
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_a_rerun_of_a_job_that_is_still_running_is_refused(client, store, monkeypatch):
    from web.api import worker as worker_mod

    store.create_job(job_id="j1")
    monkeypatch.setitem(worker_mod._active, "j1", (CancelToken(), None))
    response = client.post("/api/jobs", json={"reuse_job_id": "j1", "load_gemini_json": True})
    assert response.status_code == 409
