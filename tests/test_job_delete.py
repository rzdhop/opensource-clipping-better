"""Deleting a job removes its files -- and never anyone else's.

store.delete_job dropped the record and nothing else. outputs/{id}/ (the source
video copy, every rendered clip, thumbnails, the analysis) and the job's upload
stayed on disk forever, and on a single-disk VPS that is a slow outage.

What may be removed is narrow on purpose:
- outputs/<id>/, only as a real directory directly inside outputs/ -- never
  the root, never through a symlink, never a path with a separator in it.
  reuse_job_id comes from the client and is not validated anywhere else.
- the job's uploads, only when no other job references the same name
  (uploads keep their original file name, so two jobs can share one) and the
  file is not newer than when this job took it -- a newer one is someone
  else's upload that reused the name.

A running job is cancelled first and removed by its worker once it stops.

The containment and selection rules are stdlib (web/api/cleanup.py) and run in
the pytest-only CI environment; the route and worker tests skip without
pydantic / fastapi.
"""

import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from web.api import cleanup


@pytest.fixture
def roots(tmp_path):
    outputs = tmp_path / "outputs"
    uploads = tmp_path / "uploads"
    outputs.mkdir()
    uploads.mkdir()
    return outputs, uploads


# ------------------------------------------------------------ containment (CI)

@pytest.mark.parametrize("name", [
    "", ".", "..", "a/..", "../outputs", "a/b", "a\\b", "/etc", "C:\\Windows",
    "C:x", None, 7,
])
def test_anything_but_a_plain_child_name_is_refused(roots, name):
    outputs, _ = roots
    assert cleanup.contained(str(outputs), name, want_dir=True) is None


def test_a_file_is_not_accepted_as_a_job_directory(roots):
    outputs, _ = roots
    (outputs / "jobs.json").write_text("{}")
    assert cleanup.contained(str(outputs), "jobs.json", want_dir=True) is None


def test_a_symlink_is_never_followed(roots, tmp_path):
    outputs, _ = roots
    elsewhere = tmp_path / "precious"
    elsewhere.mkdir()
    try:
        os.symlink(elsewhere, outputs / "abc123", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a symlink here")
    assert cleanup.contained(str(outputs), "abc123", want_dir=True) is None


def test_a_real_job_directory_is_accepted(roots):
    outputs, _ = roots
    (outputs / "abc123").mkdir()
    found = cleanup.contained(str(outputs), "abc123", want_dir=True)
    assert found == os.path.realpath(outputs / "abc123")


# ------------------------------------------------------------ selection (CI)

def _job(job_id="abc123", upload="talk.mp4", transcript=None, created=None, **extra):
    created = created or datetime.now(timezone.utc)
    return {"id": job_id, "upload_filename": upload, "transcript_filename": transcript,
            "config": {"upload_filename": upload, "transcript_filename": transcript},
            "created_at": created, **extra}


def _aged(path, seconds_ago):
    stamp = time.time() - seconds_ago
    os.utime(path, (stamp, stamp))


def test_a_jobs_output_directory_and_its_own_upload_are_removed(roots):
    outputs, uploads = roots
    (outputs / "abc123").mkdir()
    (outputs / "abc123" / "highlight_rank_1_ready.mp4").write_bytes(b"x")
    (uploads / "talk.mp4").write_bytes(b"x")
    (uploads / "talk.vtt").write_bytes(b"x")
    _aged(uploads / "talk.mp4", 60)
    _aged(uploads / "talk.vtt", 60)

    report = cleanup.remove_job_files(_job(transcript="talk.vtt"), outputs_root=str(outputs),
                                      uploads_root=str(uploads), other_jobs=[])

    assert not (outputs / "abc123").exists()
    assert not (uploads / "talk.mp4").exists()
    assert not (uploads / "talk.vtt").exists()
    assert sorted(report["removed"]) == ["outputs/abc123/", "uploads/talk.mp4", "uploads/talk.vtt"]


def test_an_upload_another_job_uses_is_kept(roots):
    outputs, uploads = roots
    (uploads / "talk.mp4").write_bytes(b"x")
    _aged(uploads / "talk.mp4", 60)

    report = cleanup.remove_job_files(_job(), outputs_root=str(outputs), uploads_root=str(uploads),
                                      other_jobs=[_job("other1")])

    assert (uploads / "talk.mp4").exists()
    assert any("talk.mp4" in line for line in report["kept"])


def test_an_upload_newer_than_the_job_is_someone_elses(roots):
    """Same name, uploaded after this job took its file: another upload
    overwrote it, possibly for a job that does not exist yet."""
    outputs, uploads = roots
    (uploads / "talk.mp4").write_bytes(b"x")
    created = datetime.now(timezone.utc) - timedelta(minutes=10)

    cleanup.remove_job_files(_job(created=created), outputs_root=str(outputs),
                             uploads_root=str(uploads), other_jobs=[])

    assert (uploads / "talk.mp4").exists()


def test_a_source_attached_later_counts_from_when_it_was_attached(roots):
    outputs, uploads = roots
    (uploads / "talk.mp4").write_bytes(b"x")
    _aged(uploads / "talk.mp4", 5)
    created = datetime.now(timezone.utc) - timedelta(hours=1)
    attached = datetime.now(timezone.utc)

    cleanup.remove_job_files(_job(created=created, source_attached_at=attached),
                             outputs_root=str(outputs), uploads_root=str(uploads), other_jobs=[])

    assert not (uploads / "talk.mp4").exists()


def test_a_persisted_iso_timestamp_is_understood(roots):
    outputs, uploads = roots
    (uploads / "talk.mp4").write_bytes(b"x")
    _aged(uploads / "talk.mp4", 60)
    job = _job(created=datetime.now(timezone.utc))
    job["created_at"] = job["created_at"].isoformat()

    cleanup.remove_job_files(job, outputs_root=str(outputs), uploads_root=str(uploads), other_jobs=[])

    assert not (uploads / "talk.mp4").exists()


def test_without_a_usable_timestamp_uploads_are_kept(roots):
    outputs, uploads = roots
    (uploads / "talk.mp4").write_bytes(b"x")
    _aged(uploads / "talk.mp4", 60)
    job = _job()
    job["created_at"] = "not a date"

    cleanup.remove_job_files(job, outputs_root=str(outputs), uploads_root=str(uploads), other_jobs=[])

    assert (uploads / "talk.mp4").exists()


def test_nothing_outside_the_job_is_touched(roots):
    outputs, uploads = roots
    (outputs / "abc123").mkdir()
    (outputs / "abc1234").mkdir()
    (outputs / "jobs.json").write_text("{}")
    (uploads / "unrelated.mp4").write_bytes(b"x")
    _aged(uploads / "unrelated.mp4", 60)

    cleanup.remove_job_files(_job(upload=None), outputs_root=str(outputs),
                             uploads_root=str(uploads), other_jobs=[])

    assert (outputs / "abc1234").exists()
    assert (outputs / "jobs.json").exists()
    assert (uploads / "unrelated.mp4").exists()


def test_a_hostile_job_id_or_upload_name_removes_nothing(roots):
    outputs, uploads = roots
    (outputs / "keep").mkdir()
    (uploads / "keep.mp4").write_bytes(b"x")
    _aged(uploads / "keep.mp4", 60)

    cleanup.remove_job_files(_job(job_id="..", upload="../outputs/keep"),
                             outputs_root=str(outputs), uploads_root=str(uploads), other_jobs=[])

    assert (outputs / "keep").exists()
    assert (uploads / "keep.mp4").exists()
    assert outputs.exists() and uploads.exists()


# ------------------------------------------------------------ route + worker

@pytest.fixture
def api(monkeypatch, roots):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.api import store as job_store
    from web.api import worker as worker_mod
    from web.api.routes import jobs

    outputs, uploads = roots
    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "_persist", lambda force=True: None)
    monkeypatch.setattr(worker_mod, "OUTPUTS_ROOT", str(outputs))
    monkeypatch.setattr(worker_mod, "UPLOADS_ROOT", str(uploads))
    monkeypatch.setenv("DISABLE_AUTH", "1")
    app = FastAPI()
    app.include_router(jobs.router)
    with TestClient(app) as client:
        yield client, job_store, worker_mod, outputs, uploads


def _finished_job(job_store, outputs, uploads, job_id="abc123"):
    job_store.create_job(job_id=job_id, upload_filename="talk.mp4",
                         config={"upload_filename": "talk.mp4"})
    job_store._jobs[job_id]["created_at"] = datetime.now(timezone.utc) + timedelta(seconds=5)
    (outputs / job_id).mkdir()
    (outputs / job_id / "clip.mp4").write_bytes(b"x")
    (uploads / "talk.mp4").write_bytes(b"x")
    job_store.set_clips(job_id, [])


def test_deleting_a_finished_job_removes_its_files(api):
    client, job_store, _, outputs, uploads = api
    _finished_job(job_store, outputs, uploads)

    response = client.delete("/api/jobs/abc123")

    assert response.status_code == 200
    assert job_store.get_job("abc123") is None
    assert not (outputs / "abc123").exists()
    assert not (uploads / "talk.mp4").exists()
    assert "outputs/abc123/" in response.json()["removed"]


def test_deleting_a_running_job_cancels_it_and_defers_the_files(api, monkeypatch):
    from clipping.cancel import CancelToken

    client, job_store, worker_mod, outputs, uploads = api
    _finished_job(job_store, outputs, uploads)
    job_store._jobs["abc123"]["status"] = "rendering"
    token = CancelToken()
    monkeypatch.setitem(worker_mod._active, "abc123", (token, None))
    monkeypatch.setattr(worker_mod.children, "kill", lambda job_id: 0)

    response = client.delete("/api/jobs/abc123")

    assert response.status_code == 202
    assert token.cancelled
    assert (outputs / "abc123").exists()
    assert job_store.get_job("abc123")["delete_requested"] is True

    # The worker stops; its cleanup honours the request.
    worker_mod._active.pop("abc123")
    worker_mod.finish_deferred_delete("abc123")
    assert job_store.get_job("abc123") is None
    assert not (outputs / "abc123").exists()


def test_cancel_alone_keeps_the_files_for_a_rerun(api, monkeypatch):
    """DEC-022: a rerun detects the saved transcript in outputs/{id}/."""
    client, job_store, worker_mod, outputs, uploads = api
    _finished_job(job_store, outputs, uploads)
    job_store._jobs["abc123"]["status"] = "analyzing"
    monkeypatch.setattr(worker_mod, "cancel", lambda job_id: True)

    assert client.post("/api/jobs/abc123/cancel").status_code == 202
    assert (outputs / "abc123" / "clip.mp4").exists()


def test_a_delete_left_over_from_a_crash_is_finished_at_startup(api):
    client, job_store, worker_mod, outputs, uploads = api
    _finished_job(job_store, outputs, uploads)
    job_store._jobs["abc123"]["delete_requested"] = True

    assert worker_mod.finish_deferred_deletes() == 1
    assert job_store.get_job("abc123") is None
    assert not (outputs / "abc123").exists()


def test_attaching_a_source_records_when(api, monkeypatch):
    client, job_store, worker_mod, outputs, uploads = api
    job_store.create_job(job_id="abc123")
    job_store._jobs["abc123"]["status"] = "needs_upload"

    async def no_submit(job_id, payload):
        return None

    monkeypatch.setattr(worker_mod, "submit_job", no_submit)
    from web.api.routes import files as files_route
    monkeypatch.setattr(files_route, "UPLOAD_DIR", str(uploads))

    response = client.post("/api/jobs/abc123/source",
                           files={"file": ("talk.mp4", b"x", "video/mp4")})
    assert response.status_code == 200
    assert job_store.get_job("abc123").get("source_attached_at") is not None
