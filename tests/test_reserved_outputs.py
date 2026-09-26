"""No job can own outputs/stories/ or outputs/_chain_test/.

``POST /api/jobs`` takes ``reuse_job_id`` from the client and makes it the job
id, and the id names the job's output directory: ``outputs/<id>/``. Deleting
the job removes that directory (``cleanup.remove_job_files``). AI Story keeps
every story in ``outputs/stories/`` and the Settings chain test its samples in
``outputs/_chain_test/``, so a job called ``stories`` would render into every
story workspace and, once deleted, wipe all of them.

Closed three ways: the cleanup never removes a reserved directory, the route
refuses a reserved ``reuse_job_id`` before anything is created or queued, and
``GET /api/outputs/stories`` is refused so the outputs route cannot enumerate
story ids (``_chain_test`` stays served -- its samples are signed URLs).

The cleanup and agreement tests are stdlib and run in the pytest-only CI
environment; the route tests skip without fastapi.
"""

import os
import pathlib
import re

import pytest

from web.api import cleanup

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def roots(tmp_path):
    outputs = tmp_path / "outputs"
    uploads = tmp_path / "uploads"
    outputs.mkdir()
    uploads.mkdir()
    return outputs, uploads


# ------------------------------------------------------------ cleanup (CI)

def test_the_reserved_names():
    assert cleanup.RESERVED_OUTPUT_NAMES == frozenset({"stories", "_chain_test", "stories.json", "jobs.json"})


@pytest.mark.parametrize("name", ["stories", "_chain_test"])
def test_deleting_a_job_named_after_a_reserved_directory_keeps_it(roots, name):
    outputs, uploads = roots
    (outputs / name / "0123456789ab").mkdir(parents=True)
    (outputs / name / "0123456789ab" / "story.json").write_text("{}", encoding="utf-8")
    (outputs / name / "sample.png").write_bytes(b"x")

    report = cleanup.remove_job_files({"id": name, "upload_filename": None},
                                      outputs_root=str(outputs), uploads_root=str(uploads),
                                      other_jobs=[])

    assert (outputs / name / "0123456789ab" / "story.json").exists()
    assert (outputs / name / "sample.png").exists()
    assert report == {"removed": [], "kept": [f"outputs/{name}/ (reserved, never removed)"]}


@pytest.mark.parametrize("name", [
    "stories", "_chain_test", "Stories", "STORIES", "stories.", "stories ", "stories. .", "_Chain_Test",
    "stories.json", "jobs.json", "Jobs.JSON",
])
def test_a_name_that_lands_on_a_reserved_directory_is_reserved(name):
    """A case-insensitive filesystem opens ``Stories`` as ``stories``, and
    Windows drops a trailing dot or space."""
    assert cleanup.is_reserved(name)


@pytest.mark.parametrize("name", [
    "stories2", "my_stories", "_stories", "chain_test", "abc123", ".stories", "stories.jsonl", "jobs",
    "", None, 7, ["stories"],
])
def test_other_names_are_not_reserved(name):
    assert not cleanup.is_reserved(name)


def test_an_ordinary_job_directory_next_to_them_is_still_removed(roots):
    outputs, uploads = roots
    for name in ("stories", "_chain_test", "stories2"):
        (outputs / name).mkdir()

    report = cleanup.remove_job_files({"id": "stories2", "upload_filename": None},
                                      outputs_root=str(outputs), uploads_root=str(uploads),
                                      other_jobs=[])

    assert report == {"removed": ["outputs/stories2/"], "kept": []}
    assert (outputs / "stories").is_dir() and (outputs / "_chain_test").is_dir()


def test_the_reserved_names_agree_with_their_owners():
    """The story store and the chain test name their own directories; the
    cleanup cannot import either (stdlib only, and clipping/ never imports
    web/), so the three are held together here."""
    from clipping.aistory import store as story_store

    assert story_store.STORIES_DIRNAME == cleanup.STORIES_DIRNAME
    assert cleanup.STORIES_DIRNAME in cleanup.RESERVED_OUTPUT_NAMES

    settings_src = (ROOT / "web" / "api" / "routes" / "settings.py").read_text(encoding="utf-8")
    found = re.search(r'^CHAIN_TEST_DIRNAME = "([^"]+)"$', settings_src, re.MULTILINE)
    assert found, "routes/settings.py no longer defines CHAIN_TEST_DIRNAME"
    assert found.group(1) in cleanup.RESERVED_OUTPUT_NAMES


# ------------------------------------------------------------ POST /api/jobs

@pytest.fixture
def api(monkeypatch):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.api import store as job_store
    from web.api import worker as worker_mod
    from web.api.routes import jobs

    submitted = []

    async def record_submit(job_id, payload):
        submitted.append(job_id)

    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "_persist", lambda force=True: None)
    monkeypatch.setattr(worker_mod, "submit_job", record_submit)
    monkeypatch.setattr(jobs, "_slow_chain_refusal", lambda payload: None)
    monkeypatch.setenv("DISABLE_AUTH", "1")
    monkeypatch.delenv("MAX_QUEUED_JOBS", raising=False)
    app = FastAPI()
    app.include_router(jobs.router)
    with TestClient(app) as client:
        yield client, job_store, worker_mod, submitted


@pytest.mark.parametrize("name", ["stories", "_chain_test", "Stories", "stories.", "stories.json", "jobs.json"])
def test_a_reserved_reuse_job_id_is_refused_before_a_job_exists(api, name):
    client, job_store, _, submitted = api

    response = client.post("/api/jobs", json={"reuse_job_id": name, "upload_filename": "talk.mp4"})

    assert response.status_code == 400
    assert name in response.json()["detail"]
    assert "reserve" in response.json()["detail"]
    assert job_store.list_jobs() == []
    assert submitted == []


def test_the_refusal_comes_before_the_queue_limit(api, monkeypatch):
    client, job_store, _, submitted = api
    monkeypatch.setenv("MAX_QUEUED_JOBS", "1")
    job_store.create_job(job_id="queued0")

    response = client.post("/api/jobs", json={"reuse_job_id": "stories"})

    assert response.status_code == 400
    assert [job["id"] for job in job_store.list_jobs()] == ["queued0"]
    assert submitted == []


def test_the_refusal_comes_before_the_running_check(api, monkeypatch):
    client, _, worker_mod, submitted = api
    monkeypatch.setattr(worker_mod, "is_active", lambda job_id: True)

    assert client.post("/api/jobs", json={"reuse_job_id": "_chain_test"}).status_code == 400
    assert submitted == []


def test_an_ordinary_reuse_id_still_reruns_the_job(api):
    client, job_store, _, submitted = api

    response = client.post("/api/jobs", json={"reuse_job_id": "job123"})

    assert response.status_code == 201
    assert response.json()["id"] == "job123"
    assert job_store.get_job("job123")["config"]["load_gemini_json"] is True
    assert submitted == ["job123"]


# ------------------------------------------------------------ outputs route

@pytest.fixture
def outputs_route(monkeypatch, roots):
    pytest.importorskip("fastapi")
    from web.api.routes import files as files_route

    outputs, _ = roots
    monkeypatch.setattr(files_route, "OUTPUTS_DIR", str(outputs))
    (outputs / "stories" / "0123456789ab").mkdir(parents=True)
    (outputs / "stories" / "0123456789ab" / "story.json").write_text("{}", encoding="utf-8")
    (outputs / "stories" / "readme.txt").write_text("x", encoding="utf-8")
    (outputs / "_chain_test").mkdir()
    (outputs / "_chain_test" / "a.png").write_bytes(b"x")
    return files_route, outputs


@pytest.mark.parametrize("job_id, filename", [
    ("stories", None),
    ("stories", "x"),
    ("stories", "readme.txt"),
    ("Stories", "readme.txt"),
    ("stories.", None),
])
def test_the_stories_directory_is_not_an_output_path(outputs_route, job_id, filename):
    from fastapi import HTTPException

    files_route, _ = outputs_route
    with pytest.raises(HTTPException) as info:
        files_route.resolve_output_path(job_id, filename)
    assert info.value.status_code == 400
    assert info.value.detail == "Invalid path"


def test_the_chain_test_directory_is_still_an_output_path(outputs_route):
    files_route, outputs = outputs_route
    resolved = files_route.resolve_output_path("_chain_test", "a.png")
    assert resolved == os.path.join(os.path.realpath(outputs), "_chain_test", "a.png")


def test_listing_the_stories_directory_is_refused(outputs_route, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    files_route, _ = outputs_route
    monkeypatch.setenv("DISABLE_AUTH", "1")
    app = FastAPI()
    app.include_router(files_route.router)
    with TestClient(app) as client:
        assert client.get("/api/outputs/stories").status_code == 400
        assert client.get("/api/outputs/stories/readme.txt").status_code == 400
        assert client.get("/api/outputs/_chain_test/a.png").status_code == 200
