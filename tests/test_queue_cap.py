"""The queue has a limit.

POST /api/jobs accepted every job and queued it behind MAX_CONCURRENT_JOBS with
no ceiling. Each queued job holds an outputs/ directory and a record that the
store re-serializes on every write, so a client retrying a failed upload in a
loop could stack hundreds of them.

MAX_QUEUED_JOBS (default 20, 0 = no limit) is read per request. Past it, a new
job -- or a source attached to a waiting one -- is refused with 429: "try
again later", which is what it is. A 5xx would read as the server being broken.

The text guards run in the pytest-only CI job; the route tests need fastapi.
"""

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_limit_is_documented_in_the_env_template():
    assert "MAX_QUEUED_JOBS=" in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_the_limit_reaches_the_container():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "MAX_QUEUED_JOBS=${MAX_QUEUED_JOBS:-" in compose


@pytest.fixture
def api(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.api import store as job_store
    from web.api import worker as worker_mod
    from web.api.routes import jobs

    async def no_submit(job_id, payload):
        return None

    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "_persist", lambda force=True: None)
    monkeypatch.setattr(worker_mod, "submit_job", no_submit)
    monkeypatch.setattr(jobs, "_slow_chain_refusal", lambda payload: None)
    monkeypatch.setenv("DISABLE_AUTH", "1")
    monkeypatch.delenv("MAX_QUEUED_JOBS", raising=False)
    app = FastAPI()
    app.include_router(jobs.router)
    with TestClient(app) as client:
        yield client, job_store


def _queue(job_store, n):
    for i in range(n):
        job_store.create_job(job_id=f"queued{i}")


def test_a_job_past_the_limit_is_refused_with_429(api, monkeypatch):
    client, job_store = api
    monkeypatch.setenv("MAX_QUEUED_JOBS", "2")
    _queue(job_store, 2)

    response = client.post("/api/jobs", json={"upload_filename": "talk.mp4"})

    assert response.status_code == 429
    assert "2" in response.json()["detail"]
    assert len(job_store.list_jobs()) == 2


def test_a_job_under_the_limit_is_accepted(api, monkeypatch):
    client, job_store = api
    monkeypatch.setenv("MAX_QUEUED_JOBS", "2")
    _queue(job_store, 1)
    assert client.post("/api/jobs", json={"upload_filename": "talk.mp4"}).status_code == 201


def test_zero_means_no_limit(api, monkeypatch):
    client, job_store = api
    monkeypatch.setenv("MAX_QUEUED_JOBS", "0")
    _queue(job_store, 50)
    assert client.post("/api/jobs", json={"upload_filename": "talk.mp4"}).status_code == 201


def test_the_default_limit_is_twenty(api):
    client, job_store = api
    _queue(job_store, 19)
    assert client.post("/api/jobs", json={"upload_filename": "talk.mp4"}).status_code == 201
    assert client.post("/api/jobs", json={"upload_filename": "talk.mp4"}).status_code == 429


def test_running_and_finished_jobs_do_not_count(api, monkeypatch):
    client, job_store = api
    monkeypatch.setenv("MAX_QUEUED_JOBS", "1")
    job_store.create_job(job_id="running")
    job_store._jobs["running"]["status"] = "rendering"
    job_store.create_job(job_id="done")
    job_store._jobs["done"]["status"] = "completed"
    assert client.post("/api/jobs", json={"upload_filename": "talk.mp4"}).status_code == 201


def test_attaching_a_source_to_a_full_queue_is_refused(api, monkeypatch, tmp_path):
    from web.api.routes import files as files_route

    client, job_store = api
    # Were the refusal to regress, the upload must land here, not in uploads/.
    monkeypatch.setattr(files_route, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("MAX_QUEUED_JOBS", "1")
    _queue(job_store, 1)
    job_store.create_job(job_id="waiting")
    job_store._jobs["waiting"]["status"] = "needs_upload"

    response = client.post("/api/jobs/waiting/source",
                           files={"file": ("talk.mp4", b"x", "video/mp4")})

    assert response.status_code == 429
    assert job_store.get_job("waiting")["status"] == "needs_upload"


def test_a_malformed_limit_falls_back_to_the_default(api, monkeypatch):
    client, job_store = api
    monkeypatch.setenv("MAX_QUEUED_JOBS", "lots")
    _queue(job_store, 5)
    assert client.post("/api/jobs", json={"upload_filename": "talk.mp4"}).status_code == 201
