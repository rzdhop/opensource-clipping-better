"""Story-step jobs: a second job kind on the store, worker and SSE clips use.

AI Story phase 1, stage 4 (spec 9.1). A story step -- "write the bible",
"generate ten concepts" -- is an ordinary job with ``kind: "story_step"``, so
it is queued behind the same single worker slot, cancelled by the same token
and narrated through the same stdout tee as a clip job. What differs:

- it ends in ``awaiting_approval``, which is finished for the worker (the slot
  is freed, the stream closes, a cancel is refused) but not for the user:
  approving it, or superseding it with a regenerated step, moves it on to
  ``completed``;
- a restart leaves it alone, where an interrupted job is failed;
- while it runs it is ``running``, and at a restart that is interrupted;
- what it prints is mirrored into the story's own ``activity.log``.

A clip job's record gains exactly one key, ``kind: "clip"``, and its path
through the worker is the one it always had.

The step registry is stdlib and runs in the pytest-only CI environment; the
store and worker tests need pydantic, the stream and route tests fastapi, and
they skip without them like the existing job tests (DEC-012). Every file lives
under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from clipping.cancel import Cancelled, CancelToken

# A well-formed story id with no folder behind it.
STORY_ID = "0123456789ab"
OTHER_STORY_ID = "ba9876543210"

# Every key a job record had before this stage, written out rather than read
# from the code: the point is to notice when the code changes it.
CLIP_RECORD_KEYS_BEFORE = {
    "id", "status", "created_at", "updated_at", "upload_filename",
    "transcript_filename", "source_url", "config", "progress", "clips",
    "error", "log", "events", "event_seq",
}

# Every field a job response had before this stage, likewise.
RESPONSE_KEYS_BEFORE = {
    "id", "status", "created_at", "updated_at", "upload_filename",
    "transcript_filename", "source_url", "url", "config", "progress", "clips",
    "error", "log", "events",
}
STORY_RESPONSE_KEYS = {"story_id", "ep", "step", "params", "approved_at", "superseded_by"}


# ------------------------------------------------------------ registry (CI)

def _ctx(token=None, **overrides):
    from clipping.aistory import steps

    values = dict(
        job_id="job000000001", story_id=STORY_ID, step="test_echo", ep=None,
        params={}, cancel=token or CancelToken(), settings_env={}, outputs_dir="/nowhere",
    )
    values.update(overrides)
    return steps.StepContext(**values)


def test_a_registered_runner_is_called_with_its_context(monkeypatch):
    from clipping.aistory import steps

    seen = []
    monkeypatch.setitem(steps.RUNNERS, "test_echo", lambda ctx: seen.append(ctx) or "result")
    ctx = _ctx()

    assert steps.run("test_echo", ctx) == "result"
    assert seen == [ctx]


def test_an_unknown_step_names_the_known_ones(monkeypatch):
    from clipping.aistory import steps

    monkeypatch.setitem(steps.RUNNERS, "test_b", lambda ctx: None)
    monkeypatch.setitem(steps.RUNNERS, "test_a", lambda ctx: None)

    with pytest.raises(steps.UnknownStep) as caught:
        steps.run("nope", _ctx(step="nope"))

    assert isinstance(caught.value, KeyError)
    message = str(caught.value)
    assert "'nope'" in message
    assert "test_a" in message and "test_b" in message
    # KeyError's own str() would wrap the whole sentence in quotes.
    assert not message.startswith(("'", '"'))


def test_a_cancelled_step_never_starts(monkeypatch):
    from clipping.aistory import steps

    ran = []
    monkeypatch.setitem(steps.RUNNERS, "test_echo", lambda ctx: ran.append(ctx))
    token = CancelToken()
    token.cancel()

    with pytest.raises(Cancelled):
        steps.run("test_echo", _ctx(token))
    assert ran == []


def test_a_context_logs_with_print_unless_told_otherwise():
    assert _ctx().on_log is print


# ------------------------------------------------------------------ store

@pytest.fixture
def job_store(monkeypatch, tmp_path):
    """The real store, on an empty job table persisted under tmp_path."""
    pytest.importorskip("pydantic")
    from web.api import store as job_store

    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "PERSIST_PATH", str(tmp_path / "jobs.json"))
    return job_store


def _step_job(job_store, step="test_echo", story_id=STORY_ID, **extra):
    return job_store.create_job(kind="story_step", story_id=story_id, step=step, **extra)


def _status(job_store, job_id):
    return job_store.get_job(job_id)["status"]


def test_a_clip_record_gains_only_its_kind(job_store):
    job_id = job_store.create_job(upload_filename="talk.mp4", config={"clips": 3})
    job = job_store.get_job(job_id)

    assert set(job) == CLIP_RECORD_KEYS_BEFORE | {"kind"}
    assert job["kind"] == "clip"


def test_a_story_step_record_carries_its_story(job_store):
    job_id = _step_job(job_store, step="bible", ep=2, params={"field": "tone"})
    job = job_store.get_job(job_id)

    assert set(job) == CLIP_RECORD_KEYS_BEFORE | {"kind", "story_id", "ep", "step", "params"}
    assert job["kind"] == "story_step"
    assert job["status"] == "queued"
    assert (job["story_id"], job["ep"], job["step"], job["params"]) == (
        STORY_ID, 2, "bible", {"field": "tone"})


def test_a_story_step_defaults_to_no_episode_and_no_params(job_store):
    job = job_store.get_job(_step_job(job_store))
    assert job["ep"] is None
    assert job["params"] == {}


@pytest.mark.parametrize("kwargs", [
    {"kind": "story"},
    {"kind": None},
    {"kind": "story_step", "step": "bible"},
    {"kind": "story_step", "story_id": STORY_ID},
    {"kind": "story_step", "story_id": "../stories", "step": "bible"},
    {"kind": "story_step", "story_id": STORY_ID.upper(), "step": "bible"},
    {"kind": "story_step", "story_id": STORY_ID, "step": ""},
    {"kind": "story_step", "story_id": STORY_ID, "step": "   "},
    {"kind": "story_step", "story_id": STORY_ID, "step": 7},
    {"kind": "story_step", "story_id": STORY_ID, "step": "bible", "ep": "2"},
    {"kind": "story_step", "story_id": STORY_ID, "step": "bible", "ep": True},
    {"kind": "story_step", "story_id": STORY_ID, "step": "bible", "params": ["tone"]},
    {"story_id": STORY_ID},
    {"step": "bible"},
    {"params": {"field": "tone"}},
    {"ep": 1},
])
def test_a_malformed_job_is_refused_and_nothing_is_created(job_store, kwargs):
    with pytest.raises(ValueError):
        job_store.create_job(**kwargs)
    assert job_store.list_jobs() == []


def test_a_restart_fails_interrupted_jobs_and_keeps_steps_awaiting_approval(job_store, monkeypatch):
    """The acceptance criterion: a story left awaiting approval is still
    awaiting, and still approvable, after the backend restarts."""
    from web.api.models import JobStatus

    awaiting = _step_job(job_store, step="bible", ep=1, params={"note": "plus sombre"})
    job_store.set_status(awaiting, JobStatus.AWAITING_APPROVAL)
    analyzing = job_store.create_job(upload_filename="talk.mp4")
    job_store.set_status(analyzing, JobStatus.ANALYZING)
    running = _step_job(job_store, step="concepts")
    job_store.set_status(running, JobStatus.RUNNING)
    done = job_store.create_job(upload_filename="talk.mp4")
    job_store.set_clips(done, [])
    job_store._persist(force=True)

    # A new process: the table is empty until the file is read back.
    monkeypatch.setattr(job_store, "_jobs", {})
    job_store._load()
    assert set(job_store._jobs) == {awaiting, analyzing, running, done}

    changed = job_store.fail_stale_jobs()

    assert sorted(changed) == sorted([analyzing, running])
    assert _status(job_store, awaiting) == "awaiting_approval"
    assert job_store.get_job(awaiting)["error"] is None
    assert _status(job_store, analyzing) == "failed"
    assert _status(job_store, running) == "failed"
    assert _status(job_store, done) == "completed"

    # And the file says so too: the next restart reads the same thing.
    monkeypatch.setattr(job_store, "_jobs", {})
    job_store._load()
    job = job_store.get_job(awaiting)
    assert job["status"] == "awaiting_approval"
    assert (job["kind"], job["story_id"], job["ep"], job["step"], job["params"]) == (
        "story_step", STORY_ID, 1, "bible", {"note": "plus sombre"})
    assert job_store.approve_step_job(awaiting) == "ok"
    assert _status(job_store, awaiting) == "completed"


def test_an_old_record_without_a_kind_loads_as_a_clip_job_untouched(job_store, tmp_path):
    record = {
        "id": "old000000001", "status": "completed",
        "created_at": "2026-09-18T10:00:00+00:00", "updated_at": "2026-09-18T11:00:00+00:00",
        "upload_filename": "talk.mp4", "transcript_filename": None, "source_url": None,
        "config": {}, "progress": None, "clips": [], "error": None, "log": [],
    }
    (tmp_path / "jobs.json").write_text(json.dumps({"old000000001": record}), encoding="utf-8")

    job_store._load()

    job = job_store.get_job("old000000001")
    assert "kind" not in job  # read as "clip", never rewritten at load
    assert job_store.list_step_jobs(STORY_ID) == []
    assert job_store.approve_step_job("old000000001") == "not_awaiting"


def test_a_step_awaiting_approval_cannot_be_cancelled(job_store):
    from web.api.models import JobStatus

    job_id = _step_job(job_store)
    job_store.set_status(job_id, JobStatus.AWAITING_APPROVAL)

    assert job_store.request_cancel(job_id) == "terminal"
    assert _status(job_store, job_id) == "awaiting_approval"


def test_a_running_step_can_be_cancelled(job_store):
    from web.api.models import JobStatus

    job_id = _step_job(job_store)
    job_store.set_status(job_id, JobStatus.RUNNING)

    assert job_store.request_cancel(job_id) == "cancelled"
    assert _status(job_store, job_id) == "cancelled"


def test_approving_completes_the_step_and_is_on_disk_at_once(job_store, monkeypatch):
    from web.api.models import JobStatus

    job_id = _step_job(job_store)
    job_store.set_status(job_id, JobStatus.AWAITING_APPROVAL)

    assert job_store.approve_step_job(job_id) == "ok"

    job = job_store.get_job(job_id)
    assert job["status"] == "completed"
    stamped = datetime.fromisoformat(job["approved_at"])
    assert abs(datetime.now(timezone.utc) - stamped) < timedelta(minutes=1)
    assert job.get("superseded_by") is None

    monkeypatch.setattr(job_store, "_jobs", {})
    job_store._load()
    assert _status(job_store, job_id) == "completed"
    assert job_store.get_job(job_id)["approved_at"] == job["approved_at"]


def test_superseding_completes_the_step_and_names_its_successor(job_store, monkeypatch):
    from web.api.models import JobStatus

    old = _step_job(job_store, step="bible")
    job_store.set_status(old, JobStatus.AWAITING_APPROVAL)
    new = _step_job(job_store, step="bible")

    assert job_store.supersede_step_job(old, new) == "ok"

    job = job_store.get_job(old)
    assert job["status"] == "completed"
    assert job["superseded_by"] == new
    assert job.get("approved_at") is None

    monkeypatch.setattr(job_store, "_jobs", {})
    job_store._load()
    assert job_store.get_job(old)["superseded_by"] == new


@pytest.mark.parametrize("move", ["approve_step_job", "supersede_step_job"])
def test_only_a_step_awaiting_approval_can_be_moved_on(job_store, move):
    from web.api.models import JobStatus

    def call(job_id):
        fn = getattr(job_store, move)
        return fn(job_id) if move == "approve_step_job" else fn(job_id, "successor001")

    assert call("missing00001") == "missing"

    for status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED):
        job_id = _step_job(job_store)
        job_store._jobs[job_id]["status"] = status.value
        assert call(job_id) == "not_awaiting", status
        assert _status(job_store, job_id) == status.value

    finished = _step_job(job_store)
    job_store.set_status(finished, JobStatus.AWAITING_APPROVAL)
    assert call(finished) == "ok"
    assert call(finished) == "not_awaiting"  # completed now: no second approval

    cancelled = _step_job(job_store)
    job_store.request_cancel(cancelled)
    assert call(cancelled) == "not_awaiting"
    assert _status(job_store, cancelled) == "cancelled"

    clip = job_store.create_job()
    job_store.set_clips(clip, [])
    assert call(clip) == "not_awaiting"


def test_step_jobs_are_listed_per_story_oldest_first(job_store):
    from web.api.models import JobStatus

    base = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
    concepts = _step_job(job_store, step="concepts")
    bible = _step_job(job_store, step="bible")
    bible_again = _step_job(job_store, step="bible")
    _step_job(job_store, step="bible", story_id=OTHER_STORY_ID)
    job_store.create_job(upload_filename="talk.mp4")
    legacy = job_store.create_job()
    del job_store._jobs[legacy]["kind"]
    # Created out of order on purpose: the list follows created_at.
    for minutes, job_id in ((3, concepts), (1, bible), (2, bible_again)):
        job_store._jobs[job_id]["created_at"] = base + timedelta(minutes=minutes)
    job_store.set_status(bible, JobStatus.AWAITING_APPROVAL)

    ids = lambda jobs: [j["id"] for j in jobs]  # noqa: E731
    assert ids(job_store.list_step_jobs(STORY_ID)) == [bible, bible_again, concepts]
    assert ids(job_store.list_step_jobs(STORY_ID, step="bible")) == [bible, bible_again]
    assert ids(job_store.list_step_jobs(STORY_ID, statuses={"awaiting_approval"})) == [bible]
    assert ids(job_store.list_step_jobs(
        STORY_ID, statuses=[JobStatus.QUEUED, JobStatus.AWAITING_APPROVAL])) == [
            bible, bible_again, concepts]
    assert job_store.list_step_jobs("ffffffffffff") == []


def test_a_running_step_counts_as_running_and_an_awaiting_one_does_not(job_store):
    from web.api.models import JobStatus

    job_id = _step_job(job_store)
    assert job_store.get_running_count() == 0
    assert job_store.get_queued_count() == 1

    job_store.set_status(job_id, JobStatus.RUNNING)
    assert job_store.get_running_count() == 1
    assert job_store.get_queued_count() == 0

    job_store.set_status(job_id, JobStatus.AWAITING_APPROVAL)
    assert job_store.get_running_count() == 0


# ----------------------------------------------------------------- worker

@pytest.fixture
def worker(job_store, monkeypatch, tmp_path):
    """The worker, writing story files under tmp_path, with no real pipeline."""
    from clipping.aistory.store import StoryStore
    from web.api import worker as worker_mod

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    story = StoryStore(str(outputs), on_log=lambda line: None).create(
        language="fr", now="2026-09-26T10:00:00+00:00")
    clip_runs = []

    def no_pipeline(job_id, payload, token=None):
        clip_runs.append(job_id)

    monkeypatch.setattr(worker_mod, "OUTPUTS_ROOT", str(outputs))
    monkeypatch.setattr(worker_mod, "_active", {})
    monkeypatch.setattr(worker_mod, "_execute_pipeline", no_pipeline)
    # Handles for the tests, removed again at teardown.
    monkeypatch.setattr(worker_mod, "story_id", story["story_id"], raising=False)
    monkeypatch.setattr(worker_mod, "outputs", outputs, raising=False)
    monkeypatch.setattr(worker_mod, "clip_runs", clip_runs, raising=False)
    was_installed = worker_mod.children._installed
    yield worker_mod
    if not was_installed:
        worker_mod.children.uninstall()  # submit_job installs the Popen tracker


def _tee_stdout(monkeypatch):
    """Put back the tee worker.py installs at import.

    pytest swaps sys.stdout between a test's phases, which silently drops the
    tee installed when the worker module was first imported. Installed here,
    in the test body, it lasts for the call.
    """
    from web.api import activity
    from web.api import worker as worker_mod

    if not isinstance(sys.stdout, activity._Tee):
        monkeypatch.setattr(
            sys, "stdout", activity._Tee(sys.stdout, worker_mod._record_pipeline_line, "stdout"))


def _register(monkeypatch, name, runner):
    from clipping.aistory import steps

    monkeypatch.setitem(steps.RUNNERS, name, runner)


def _messages(job_store, job_id):
    return [e["message"] for e in job_store.get_job(job_id)["events"]]


def _activity_log(worker):
    path = worker.outputs / "stories" / worker.story_id / "activity.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_a_step_that_returns_awaits_approval(worker, job_store, monkeypatch):
    _register(monkeypatch, "test_echo", lambda ctx: None)
    job_id = _step_job(job_store, story_id=worker.story_id)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    job = job_store.get_job(job_id)
    assert job["status"] == "awaiting_approval"
    assert job["error"] is None
    messages = _messages(job_store, job_id)
    assert any("test_echo" in m and "started" in m for m in messages)
    assert "awaiting your approval" in messages[-1]
    assert worker.clip_runs == []


def test_the_runner_gets_its_job_story_settings_and_token(worker, job_store, monkeypatch):
    seen = []
    _register(monkeypatch, "test_echo", seen.append)
    monkeypatch.setattr(worker, "_settings_env", {"LLM_CHAIN": "groq/llama"})
    job_id = _step_job(job_store, story_id=worker.story_id, ep=3, params={"field": "tone"})
    token = CancelToken()

    worker._run_pipeline_sync(job_id, {}, token)

    (ctx,) = seen
    assert (ctx.job_id, ctx.story_id, ctx.step, ctx.ep, ctx.params) == (
        job_id, worker.story_id, "test_echo", 3, {"field": "tone"})
    assert ctx.cancel is token
    assert ctx.outputs_dir == str(worker.outputs)
    assert ctx.on_log is print
    assert ctx.settings_env == {"LLM_CHAIN": "groq/llama"}
    ctx.settings_env["LLM_CHAIN"] = "changed by the step"
    ctx.params["field"] = "changed by the step"
    assert worker._settings_env == {"LLM_CHAIN": "groq/llama"}
    assert job_store.get_job(job_id)["params"] == {"field": "tone"}


def test_what_a_step_prints_reaches_the_feed_and_the_story_log(worker, job_store, monkeypatch):
    _tee_stdout(monkeypatch)

    def chatty(ctx):
        print("✍️ B1 via groq/llama ≈180 tokens out (cap 250)")
        ctx.on_log("B2 via groq/llama")

    _register(monkeypatch, "test_echo", chatty)
    job_id = _step_job(job_store, story_id=worker.story_id)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    messages = _messages(job_store, job_id)
    assert "✍️ B1 via groq/llama ≈180 tokens out (cap 250)" in messages
    assert "B2 via groq/llama" in messages

    lines = _activity_log(worker).splitlines()
    assert len(lines) == len(messages)
    for line, message in zip(lines, messages):
        assert message in line and job_id in line


def test_a_step_that_raises_fails_with_its_reason(worker, job_store, monkeypatch):
    def broken(ctx):
        raise ValueError("the bible has no title")

    _register(monkeypatch, "test_echo", broken)
    job_id = _step_job(job_store, story_id=worker.story_id)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    job = job_store.get_job(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "ValueError: the bible has no title"
    assert "the bible has no title" in _activity_log(worker)


def test_an_unknown_step_fails_naming_the_known_steps(worker, job_store, monkeypatch):
    _register(monkeypatch, "test_a", lambda ctx: None)
    _register(monkeypatch, "test_b", lambda ctx: None)
    job_id = _step_job(job_store, step="nope", story_id=worker.story_id)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    job = job_store.get_job(job_id)
    assert job["status"] == "failed"
    assert job["error"].startswith("UnknownStep: ")
    assert "'nope'" in job["error"]
    assert "test_a" in job["error"] and "test_b" in job["error"]


def test_a_step_that_checks_after_a_cancel_ends_cancelled(worker, job_store, monkeypatch):
    def cancelled_midway(ctx):
        job_store.request_cancel(ctx.job_id)  # what the cancel route does first
        ctx.cancel.cancel()                    # then worker.cancel()
        ctx.cancel.check()

    _register(monkeypatch, "test_echo", cancelled_midway)
    job_id = _step_job(job_store, story_id=worker.story_id)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    job = job_store.get_job(job_id)
    assert job["status"] == "cancelled"
    assert job["error"] is None
    assert any("Cancelled" in m for m in _messages(job_store, job_id))
    assert "Cancelled" in _activity_log(worker)


def test_a_failure_caused_by_a_cancel_is_a_cancel(worker, job_store, monkeypatch):
    def interrupted(ctx):
        ctx.cancel.cancel()  # a token set without the route: the worker marks the store
        raise RuntimeError("the request was interrupted")

    _register(monkeypatch, "test_echo", interrupted)
    job_id = _step_job(job_store, story_id=worker.story_id)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    assert _status(job_store, job_id) == "cancelled"
    assert job_store.get_job(job_id)["error"] is None


def test_a_result_that_lands_after_a_cancel_is_not_offered_for_approval(worker, job_store, monkeypatch):
    def finishes_anyway(ctx):
        job_store.request_cancel(ctx.job_id)
        ctx.cancel.cancel()
        return "a result nobody asked for any more"

    _register(monkeypatch, "test_echo", finishes_anyway)
    job_id = _step_job(job_store, story_id=worker.story_id)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    assert _status(job_store, job_id) == "cancelled"
    assert not any("awaiting your approval" in m for m in _messages(job_store, job_id))


def test_a_step_cancelled_before_it_starts_never_runs(worker, job_store, monkeypatch):
    ran = []
    _register(monkeypatch, "test_echo", ran.append)
    job_id = _step_job(job_store, story_id=worker.story_id)
    token = CancelToken()
    job_store.request_cancel(job_id)
    token.cancel()

    worker._run_pipeline_sync(job_id, {}, token)

    assert ran == []
    assert _status(job_store, job_id) == "cancelled"


def test_a_story_log_that_cannot_be_written_never_breaks_the_step(worker, job_store, monkeypatch):
    from clipping.aistory.store import StoryStore

    def refuse(self, story_id, line):
        raise OSError("disk full")

    monkeypatch.setattr(StoryStore, "append_activity", refuse)
    _register(monkeypatch, "test_echo", lambda ctx: None)
    job_id = _step_job(job_store, story_id=worker.story_id)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    assert _status(job_store, job_id) == "awaiting_approval"


def test_a_step_of_a_story_with_no_folder_still_runs(worker, job_store, monkeypatch):
    _register(monkeypatch, "test_echo", lambda ctx: None)
    job_id = _step_job(job_store, story_id=STORY_ID)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    assert _status(job_store, job_id) == "awaiting_approval"


def test_clip_jobs_and_old_records_take_the_clip_path(worker, job_store, monkeypatch):
    from clipping.aistory import steps

    monkeypatch.setattr(steps, "run", lambda step, ctx: pytest.fail("a clip job ran a story step"))
    clip = job_store.create_job(upload_filename="talk.mp4")
    legacy = job_store.create_job(upload_filename="talk.mp4")
    del job_store._jobs[legacy]["kind"]

    worker._run_pipeline_sync(clip, {"upload_filename": "talk.mp4"}, CancelToken())
    worker._run_pipeline_sync(legacy, {"upload_filename": "talk.mp4"}, CancelToken())

    assert worker.clip_runs == [clip, legacy]


async def _until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the worker")
        await asyncio.sleep(0.01)


def test_a_step_awaiting_approval_frees_the_only_slot(worker, job_store, monkeypatch):
    """MAX_CONCURRENT_JOBS=1: a clip job queued behind a running step starts as
    soon as the step is ready for approval -- not when it is approved."""
    release = threading.Event()
    _register(monkeypatch, "test_block", lambda ctx: release.wait(10))
    step_job = _step_job(job_store, step="test_block", story_id=worker.story_id)
    clip_job = job_store.create_job(upload_filename="talk.mp4")
    clip_saw = []

    def clip_pipeline(job_id, payload, token=None):
        clip_saw.append(_status(job_store, step_job))
        job_store.set_clips(job_id, [])

    monkeypatch.setattr(worker, "_execute_pipeline", clip_pipeline)

    async def scenario():
        executor = ThreadPoolExecutor(max_workers=1)
        monkeypatch.setattr(worker, "_semaphore", asyncio.Semaphore(1))
        monkeypatch.setattr(worker, "_executor", executor)
        try:
            await worker.submit_job(step_job, {})
            await worker.submit_job(clip_job, {})
            await _until(lambda: _status(job_store, step_job) == "running")
            await asyncio.sleep(0.1)
            assert clip_saw == []  # one slot, and the step holds it
            assert worker.is_active(clip_job)

            release.set()
            await _until(lambda: not worker.is_active(clip_job))
            assert not worker.is_active(step_job)
        finally:
            release.set()
            executor.shutdown(wait=True)

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))

    assert clip_saw == ["awaiting_approval"]
    assert _status(job_store, step_job) == "awaiting_approval"
    assert _status(job_store, clip_job) == "completed"


# ------------------------------------------------------ stream and routes

def test_the_stream_of_a_step_awaiting_approval_ends(job_store):
    pytest.importorskip("fastapi")
    from web.api.models import JobStatus
    from web.api.routes import jobs as jobs_route

    job_id = _step_job(job_store)
    job_store.append_event(job_id, "the last line")
    job_store.set_status(job_id, JobStatus.AWAITING_APPROVAL)

    async def read_to_the_end():
        frames = []
        response = await jobs_route.job_status_sse(job_id)
        async for chunk in response.body_iterator:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            for line in text.splitlines():
                if line.startswith("data: "):
                    frames.append(json.loads(line[len("data: "):]))
        return frames

    # Without the bound, a stream that never closes hangs the suite.
    frames = asyncio.run(asyncio.wait_for(read_to_the_end(), timeout=5))

    assert frames[0]["status"] == "awaiting_approval"
    messages = [e["message"] for f in frames if f.get("type") == "events" for e in f["events"]]
    assert "the last line" in messages


@pytest.fixture
def client(job_store, monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.api import worker as worker_mod
    from web.api.routes import jobs

    outputs = tmp_path / "outputs"
    outputs.mkdir(exist_ok=True)
    monkeypatch.setattr(worker_mod, "OUTPUTS_ROOT", str(outputs))
    monkeypatch.setattr(worker_mod, "UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setenv("DISABLE_AUTH", "1")
    cancelled = []
    monkeypatch.setattr(worker_mod, "cancel", lambda job_id: cancelled.append(job_id) or False)
    app = FastAPI()
    app.include_router(jobs.router)
    with TestClient(app) as test_client:
        test_client.cancelled = cancelled
        test_client.outputs = outputs
        yield test_client


def test_cancelling_a_step_awaiting_approval_is_a_conflict(client, job_store):
    from web.api.models import JobStatus

    job_id = _step_job(job_store)
    job_store.set_status(job_id, JobStatus.AWAITING_APPROVAL)

    response = client.post(f"/api/jobs/{job_id}/cancel")

    assert response.status_code == 409
    assert "awaiting_approval" in response.json()["detail"]
    assert client.cancelled == []
    assert _status(job_store, job_id) == "awaiting_approval"


def test_a_clip_jobs_response_gains_only_its_kind_and_empty_story_fields(client, job_store):
    job_id = job_store.create_job(upload_filename="talk.mp4")

    body = client.get(f"/api/jobs/{job_id}").json()

    assert set(body) == RESPONSE_KEYS_BEFORE | {"kind"} | STORY_RESPONSE_KEYS
    assert body["kind"] == "clip"
    assert all(body[key] is None for key in STORY_RESPONSE_KEYS)


def test_an_old_record_without_a_kind_is_answered_as_a_clip_job(client, job_store):
    job_id = job_store.create_job(upload_filename="talk.mp4")
    del job_store._jobs[job_id]["kind"]

    assert client.get(f"/api/jobs/{job_id}").json()["kind"] == "clip"


def test_a_story_step_response_carries_its_story(client, job_store):
    from web.api.models import JobStatus

    job_id = _step_job(job_store, step="bible", ep=2, params={"field": "tone"})
    job_store.set_status(job_id, JobStatus.AWAITING_APPROVAL)

    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "awaiting_approval"
    assert (body["kind"], body["story_id"], body["ep"], body["step"], body["params"]) == (
        "story_step", STORY_ID, 2, "bible", {"field": "tone"})
    assert body["approved_at"] is None

    job_store.approve_step_job(job_id)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "completed"
    assert body["approved_at"] is not None

    listed = client.get("/api/jobs").json()["jobs"]
    assert [j["kind"] for j in listed] == ["story_step"]


def test_deleting_a_step_job_leaves_its_story_alone(client, job_store):
    from clipping.aistory.store import StoryStore
    from web.api.models import JobStatus

    story = StoryStore(str(client.outputs), on_log=lambda line: None).create(
        language="fr", now="2026-09-26T10:00:00+00:00")
    job_id = _step_job(job_store, story_id=story["story_id"])
    job_store.set_status(job_id, JobStatus.AWAITING_APPROVAL)

    response = client.delete(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    assert job_store.get_job(job_id) is None
    assert (client.outputs / "stories" / story["story_id"] / "story.json").is_file()


def test_a_step_job_cannot_be_rerun_as_a_clip_job(client, job_store, monkeypatch):
    # create_job(job_id=...) replaces the record, so a Clone & Rerun aimed at a
    # step job would turn it into a clip job and lose the step it recorded.
    from web.api import worker as worker_mod

    submitted = []

    async def fake_submit(job_id, payload):
        submitted.append(job_id)

    monkeypatch.setattr(worker_mod, "submit_job", fake_submit)
    from web.api.models import JobStatus

    job_id = _step_job(job_store)
    job_store.set_status(job_id, JobStatus.AWAITING_APPROVAL)

    response = client.post("/api/jobs", json={"reuse_job_id": job_id, "load_gemini_json": True})

    assert response.status_code == 409
    assert "AI Story" in response.json()["detail"]
    assert submitted == []
    record = job_store.get_job(job_id)
    assert record["kind"] == "story_step" and _status(job_store, job_id) == "awaiting_approval"
