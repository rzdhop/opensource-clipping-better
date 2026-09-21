"""Jobs interrupted by a restart must not stay 'running' forever.

`outputs/jobs.json` holds a record stuck in `analyzing` since 2026-09-18: its
worker thread died when the containers were recreated, and nothing re-queues or
fails it. The dashboard shows a job that is running and never will be, and the
health endpoint counts it as occupying a worker slot.
"""

import pytest

pytest.importorskip("pydantic")

from web.api import store as job_store
from web.api.models import JobStatus


@pytest.fixture
def seeded(monkeypatch, tmp_path):
    """Replace the store's in-memory jobs, leaving the real file alone."""
    jobs = {
        "running1": {"id": "running1", "status": JobStatus.ANALYZING.value},
        "running2": {"id": "running2", "status": JobStatus.RENDERING.value},
        "queued1": {"id": "queued1", "status": JobStatus.QUEUED.value},
        "done": {"id": "done", "status": JobStatus.COMPLETED.value},
        "failed": {"id": "failed", "status": JobStatus.FAILED.value,
                   "error": "a real earlier failure"},
        "cancelled": {"id": "cancelled", "status": JobStatus.CANCELLED.value},
    }
    monkeypatch.setattr(job_store, "_jobs", jobs)
    monkeypatch.setattr(job_store, "_persist", lambda force=True: None)
    return jobs


def test_non_terminal_jobs_are_failed(seeded):
    changed = job_store.fail_stale_jobs()
    assert sorted(changed) == ["queued1", "running1", "running2"]
    for job_id in changed:
        assert seeded[job_id]["status"] == JobStatus.FAILED.value


def test_a_reason_is_recorded_so_the_ui_can_explain_itself(seeded):
    job_store.fail_stale_jobs()
    assert "restart" in seeded["running1"]["error"].lower()


def test_terminal_jobs_are_untouched(seeded):
    job_store.fail_stale_jobs()
    assert seeded["done"]["status"] == JobStatus.COMPLETED.value
    assert seeded["cancelled"]["status"] == JobStatus.CANCELLED.value


def test_an_earlier_failures_reason_is_not_overwritten(seeded):
    """Replacing a real diagnosis with 'interrupted by a restart' would destroy
    the only record of why the job actually failed."""
    job_store.fail_stale_jobs()
    assert seeded["failed"]["error"] == "a real earlier failure"


def test_it_is_idempotent(seeded):
    first = job_store.fail_stale_jobs()
    second = job_store.fail_stale_jobs()
    assert first and second == []


def test_an_empty_store_is_fine(monkeypatch):
    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "_persist", lambda force=True: None)
    assert job_store.fail_stale_jobs() == []


def test_a_custom_reason_is_used(seeded):
    job_store.fail_stale_jobs(reason="the machine caught fire")
    assert seeded["running1"]["error"] == "the machine caught fire"


def test_the_startup_hook_calls_it():
    """Wired into app.py's lifespan, or it never runs."""
    import ast
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1]
           / "web" / "api" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "fail_stale_jobs" in called
