"""
web.api.store — In-memory job store with JSON file persistence.

Stores all job state in a dict keyed by job ID.
Periodically persists to ``jobs.json`` in the outputs directory.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from .models import (
    ClipDetail,
    JobProgressEvent,
    JobResponse,
    JobStatus,
)


# Reentrant on purpose. The activity tee turns any `print` into a
# `append_event`, which takes this lock — so a plain Lock would deadlock the
# worker thread the moment anything printed while the lock was held. Nothing
# does today; an RLock means nothing has to remember not to.
_lock = threading.RLock()
_jobs: dict[str, dict] = {}

# The activity feed is a ring buffer. A long render prints thousands of lines
# and the browser only ever shows the tail, so keeping everything would cost
# memory and persistence time for nothing.
MAX_EVENTS = 500

# `_persist` re-serializes EVERY job on every call, under the lock. That was
# affordable when only the 13 coarse worker messages triggered it; the pipeline's
# own output arrives orders of magnitude faster. Event appends are therefore
# throttled, while anything a client waits on (status, progress, completion)
# still writes through immediately.
_PERSIST_MIN_INTERVAL = 1.0
_last_persist = 0.0

# A job in one of these states is finished; nothing may move it on -- and so
# nothing may cancel it. AWAITING_APPROVAL is finished for the worker; only
# approve_step_job / supersede_step_job move it on, to COMPLETED.
_TERMINAL = frozenset({
    JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value,
    JobStatus.AWAITING_APPROVAL.value,
})

# What a job is. Every record carries one; a record written before kinds
# existed has none and is read as a clip job (``job.get("kind", KIND_CLIP)``),
# never rewritten to say so.
KIND_CLIP = "clip"
KIND_STORY_STEP = "story_step"
JOB_KINDS = (KIND_CLIP, KIND_STORY_STEP)

# What only the worker writes as a job runs. Once a job is CANCELLED these are
# dropped: its worker may still be finishing a request or unwinding a killed
# ffmpeg, and a late set_error or set_clips must not turn CANCELLED into FAILED
# or COMPLETED. The worker's lines still reach the feed (append_event).
_WORKER_FIELDS = frozenset({"status", "error", "clips"})


def _status_value(job: dict):
    status = job.get("status")
    return getattr(status, "value", status)


def _is_cancelled(job: dict) -> bool:
    return _status_value(job) == JobStatus.CANCELLED.value


# Resolve absolute path to the project root (2 levels up from web/api)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PERSIST_PATH = os.path.join(PROJECT_ROOT, "outputs", "jobs.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _persist(force: bool = True) -> None:
    """Write current job store to disk (best-effort).

    ``force=False`` skips the write when one happened less than
    ``_PERSIST_MIN_INTERVAL`` ago. The next unthrottled write — and every
    status/progress change is one — flushes whatever was skipped, so nothing is
    lost beyond a crash window that best-effort persistence already had.
    """
    global _last_persist
    now = time.monotonic()
    if not force and (now - _last_persist) < _PERSIST_MIN_INTERVAL:
        return
    _last_persist = now
    try:
        os.makedirs(os.path.dirname(PERSIST_PATH), exist_ok=True)
        serializable = {}
        for jid, job in _jobs.items():
            entry = dict(job)
            for key in ("created_at", "updated_at"):
                if isinstance(entry.get(key), datetime):
                    entry[key] = entry[key].isoformat()
            # Convert progress event
            if entry.get("progress") and isinstance(entry["progress"], JobProgressEvent):
                entry["progress"] = entry["progress"].model_dump()
                if isinstance(entry["progress"].get("timestamp"), datetime):
                    entry["progress"]["timestamp"] = entry["progress"]["timestamp"].isoformat()
            # Convert clip details
            if entry.get("clips"):
                entry["clips"] = [
                    c.model_dump() if isinstance(c, ClipDetail) else c
                    for c in entry["clips"]
                ]
            serializable[jid] = entry
        with open(PERSIST_PATH, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass  # best-effort persistence


def fail_stale_jobs(reason="Interrupted by a server restart.") -> list[str]:
    """Mark every interrupted job failed. Called once at startup.

    A job whose worker thread died with the process stays in `analyzing`
    forever: nothing re-queues it and nothing marks it failed, so the dashboard
    shows a job that is running and will never finish, and the health endpoint
    counts it as occupying a worker slot. `outputs/jobs.json` currently holds
    one such record from 2026-09-18.

    Three buckets. Finished jobs (completed, failed, cancelled) are left alone.
    A story step in `awaiting_approval` is left alone too: it is neither
    finished nor interrupted -- no worker holds it, and the story waits on the
    user's approval, which a restart must not throw away. Everything else was
    interrupted and is failed, a story step in `running` included. So is
    `needs_upload`, as it always was; that is a known mishandling with its own
    follow-up.

    Returns the ids it changed, so the caller can say how many.
    """
    from .models import JobStatus

    terminal = {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }
    waiting_for_the_user = {JobStatus.AWAITING_APPROVAL}
    changed: list[str] = []
    with _lock:
        for job_id, job in _jobs.items():
            status = job.get("status") if isinstance(job, dict) else getattr(job, "status", None)
            if status is None:
                continue
            value = getattr(status, "value", status)
            if value in {getattr(t, "value", t) for t in terminal}:
                continue
            if value in {getattr(t, "value", t) for t in waiting_for_the_user}:
                continue
            if isinstance(job, dict):
                job["status"] = getattr(JobStatus.FAILED, "value", "failed")
                job["error"] = reason
            else:
                job.status = JobStatus.FAILED
                job.error = reason
            changed.append(job_id)
        if changed:
            _persist(force=True)
    return changed


def _load() -> None:
    """Load persisted jobs from disk on startup."""
    global _jobs
    if not os.path.exists(PERSIST_PATH):
        return
    try:
        with open(PERSIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for jid, entry in data.items():
            for key in ("created_at", "updated_at"):
                if isinstance(entry.get(key), str):
                    entry[key] = datetime.fromisoformat(entry[key])
            # Jobs persisted before the activity feed existed have neither key.
            entry.setdefault("events", [])
            entry.setdefault("event_seq", len(entry["events"]))
            _jobs[jid] = entry
    except Exception:
        pass


# Load on module import
_load()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def _story_step_fields(story_id, ep, step, params) -> dict:
    """The fields a story-step record carries, checked. ValueError otherwise."""
    # Lazily: a clip job never imports the AI Story package.
    from clipping.aistory.store import is_story_id

    if not is_story_id(story_id):
        raise ValueError(f"a story_step job needs a well-formed story_id, not {story_id!r}")
    if not isinstance(step, str) or not step.strip():
        raise ValueError(f"a story_step job needs a step name, not {step!r}")
    # bool is an int in Python; an episode number is not a flag.
    if ep is not None and (not isinstance(ep, int) or isinstance(ep, bool)):
        raise ValueError(f"ep must be an episode number or null, not {ep!r}")
    if params is not None and not isinstance(params, dict):
        raise ValueError(f"params must be an object, not {type(params).__name__}")
    return {"story_id": story_id, "ep": ep, "step": step, "params": dict(params or {})}


def create_job(
    upload_filename: Optional[str] = None,
    transcript_filename: Optional[str] = None,
    source_url: Optional[str] = None,
    config: dict | None = None,
    job_id: str | None = None,
    kind: str = KIND_CLIP,
    story_id: str | None = None,
    ep: int | None = None,
    step: str | None = None,
    params: dict | None = None,
) -> str:
    """Create a new job and return its ID.

    Every record carries its ``kind``. A clip job carries nothing else new; a
    ``story_step`` job also carries ``story_id``, ``ep``, ``step`` and
    ``params``. ValueError, and nothing created, for an unknown kind, a story
    step without a well-formed story id or a step name, or story fields on a
    clip job.
    """
    if kind not in JOB_KINDS:
        raise ValueError(f"unknown job kind {kind!r} (known: {', '.join(JOB_KINDS)})")
    if kind == KIND_STORY_STEP:
        extra = _story_step_fields(story_id, ep, step, params)
    else:
        given = [name for name, value in (
            ("story_id", story_id), ("ep", ep), ("step", step), ("params", params),
        ) if value is not None]
        if given:
            raise ValueError(f"{', '.join(given)} belong to a {KIND_STORY_STEP} job, not a {kind} job")
        extra = {}
    job_id = job_id or uuid.uuid4().hex[:12]
    now = _now()
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": JobStatus.QUEUED.value,
            "created_at": now,
            "updated_at": now,
            "upload_filename": upload_filename,
            "transcript_filename": transcript_filename,
            "source_url": source_url,
            "config": config or {},
            "progress": None,
            "clips": [],
            "error": None,
            "log": [],
            "events": [],
            "event_seq": 0,
            "kind": kind,
            **extra,
        }
        _persist()
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    """Return a job dict or None."""
    with _lock:
        return _jobs.get(job_id)


def list_jobs() -> list[dict]:
    """Return all jobs sorted by creation time (newest first)."""
    with _lock:
        return sorted(
            _jobs.values(),
            key=lambda j: j.get("created_at", _now()),
            reverse=True,
        )


def update_job(job_id: str, **kwargs) -> None:
    """Update arbitrary fields on a job."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        if _is_cancelled(job):
            kwargs = {k: v for k, v in kwargs.items() if k not in _WORKER_FIELDS}
            if not kwargs:
                return
        job.update(kwargs)
        job["updated_at"] = _now()
        _persist()


def _append_event_locked(job: dict, message: str, level: str, source: str) -> None:
    """Append one feed entry. Caller must already hold ``_lock``.

    Separate from :func:`append_event` so a caller that already holds the lock
    does not depend on its reentrancy for ordinary work.
    """
    events = job.setdefault("events", [])
    job["event_seq"] = job.get("event_seq", len(events)) + 1
    events.append(
        {
            "seq": job["event_seq"],
            "ts": _now().isoformat(),
            "level": level,
            "source": source,
            "message": message,
        }
    )
    if len(events) > MAX_EVENTS:
        del events[: len(events) - MAX_EVENTS]


def _as_progress(value) -> Optional[JobProgressEvent]:
    """Coerce a stored progress value to a model. Records loaded from disk are dicts."""
    if value is None or isinstance(value, JobProgressEvent):
        return value
    if isinstance(value, dict):
        try:
            return JobProgressEvent(**value)
        except Exception:
            return None
    return None


def update_progress(
    job_id: str,
    step: str,
    step_number: int,
    total_steps: int,
    message: str,
    percent: float = 0.0,
    *,
    provider: str | None = None,
    model: str | None = None,
    detail: str | None = None,
    clip_index: int | None = None,
    clip_total: int | None = None,
) -> None:
    """Advance a job to a new step."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None or _is_cancelled(job):
            return

        # Time-in-step, not time-since-start: a step that has not changed keeps
        # the clock it started with, so the UI can say "12m on this step".
        previous = _as_progress(job.get("progress"))
        if previous is not None and previous.step == step and previous.step_started_at:
            step_started_at = previous.step_started_at
        else:
            step_started_at = _now()

        job["progress"] = JobProgressEvent(
            step=step,
            step_number=step_number,
            total_steps=total_steps,
            message=message,
            percent=percent,
            provider=provider,
            model=model,
            detail=detail,
            clip_index=clip_index,
            clip_total=clip_total,
            step_started_at=step_started_at,
        )
        job["updated_at"] = _now()
        # Append to log
        job["log"].append(f"[{step}] {message}")
        if len(job["log"]) > 500:
            job["log"] = job["log"][-500:]
        _append_event_locked(
            job, message, "error" if step == "error" else "step", "worker"
        )
        _persist()


def refine_progress(
    job_id: str,
    *,
    detail: str | None = None,
    attempt: int | None = None,
    max_attempts: int | None = None,
) -> None:
    """Update what the CURRENT step is doing, without advancing it.

    Called for every line the pipeline prints, so it deliberately does not touch
    ``updated_at`` or force a write: the SSE stream picks the change up on its
    next tick, and persistence catches it with the next throttled write.
    """
    with _lock:
        job = _jobs.get(job_id)
        if job is None or _is_cancelled(job):
            return
        current = _as_progress(job.get("progress"))
        if current is None:
            return
        updates = {}
        if detail is not None:
            updates["detail"] = detail
        if attempt is not None:
            updates["attempt"] = attempt
        if max_attempts is not None:
            updates["max_attempts"] = max_attempts
        if not updates:
            return
        job["progress"] = current.model_copy(update=updates)
        _persist(force=False)


def append_event(
    job_id: str,
    message: str,
    level: str = "info",
    source: str = "stdout",
) -> None:
    """Record one line of pipeline output against a job.

    Called from the tee in :mod:`web.api.activity`, so it runs on the worker
    thread for every line the pipeline prints. It must stay cheap: it does not
    touch ``updated_at`` (which would make the SSE diff fire on every line) and
    its persistence is throttled.
    """
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        _append_event_locked(job, message, level, source)
        _persist(force=False)


def last_event(job_id: str) -> Optional[dict]:
    """A copy of the most recent feed entry, or None."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        events = job.get("events") or []
        return dict(events[-1]) if events else None


def get_events_since(job_id: str, after_seq: int = 0) -> list[dict]:
    """Events recorded after ``after_seq``, oldest first.

    Sequence numbers rather than list indices, because the ring buffer drops
    from the front and would silently shift any index the client is holding.
    """
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return []
        return [e for e in job.get("events", []) if e.get("seq", 0) > after_seq]


def set_status(job_id: str, status: JobStatus) -> None:
    """Set job status."""
    update_job(job_id, status=status.value)


def set_error(job_id: str, error: str) -> None:
    """Mark job as failed with an error message."""
    update_job(job_id, status=JobStatus.FAILED.value, error=error)


def set_clips(job_id: str, clips: list[ClipDetail]) -> None:
    """Store rendered clip details."""
    update_job(job_id, clips=clips, status=JobStatus.COMPLETED.value)


def request_cancel(job_id: str) -> str:
    """Mark *job_id* cancelled, unless it has already finished.

    Returns ``"cancelled"``, ``"terminal"`` (it completed, failed, was
    cancelled first, or is a story step awaiting approval) or ``"missing"``.
    Decided under the store lock, so a job ends either COMPLETED -- the
    worker's set_clips got there first and the cancel is refused -- or
    CANCELLED, never both.
    """
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return "missing"
        if _status_value(job) in _TERMINAL:
            return "terminal"
        job["status"] = JobStatus.CANCELLED.value
        job["error"] = None
        job["updated_at"] = _now()
        _append_event_locked(job, "Cancel requested.", "warning", "worker")
        _persist()
        return "cancelled"


def _complete_awaiting_step(job_id: str, stamp: dict) -> str:
    """Move a story step out of AWAITING_APPROVAL, to COMPLETED, with *stamp*.

    Not a worker write, so DEC-076's rule for a cancelled job does not come
    into it: a cancelled job is not awaiting, and is refused like any other.
    """
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return "missing"
        if _status_value(job) != JobStatus.AWAITING_APPROVAL.value:
            return "not_awaiting"
        job.update(stamp)
        job["status"] = JobStatus.COMPLETED.value
        job["updated_at"] = _now()
        _persist()
        return "ok"


def approve_step_job(job_id: str) -> str:
    """The user approved a story step's result.

    ``"ok"`` (now COMPLETED, ``approved_at`` stamped), ``"missing"``, or
    ``"not_awaiting"`` for a job in any other status -- including one already
    approved. With :func:`supersede_step_job`, the only way out of
    AWAITING_APPROVAL.
    """
    return _complete_awaiting_step(job_id, {"approved_at": _now().isoformat()})


def supersede_step_job(job_id: str, by_job_id: str) -> str:
    """A regenerated step (*by_job_id*) replaces this one's result.

    Same outcomes as :func:`approve_step_job`; ``superseded_by`` is stamped
    instead of ``approved_at``.
    """
    return _complete_awaiting_step(job_id, {"superseded_by": by_job_id})


def list_step_jobs(story_id: str, *, step: str | None = None, statuses=None) -> list[dict]:
    """The story-step jobs of *story_id*, oldest first.

    ``step`` narrows to one step; ``statuses`` (JobStatus members or their
    values) to those statuses. Shallow copies: change a job through the
    functions above, not through what this returns.
    """
    wanted = None if statuses is None else {getattr(s, "value", s) for s in statuses}
    oldest = datetime.min.replace(tzinfo=timezone.utc)

    def created(job):
        value = job.get("created_at")
        if not isinstance(value, datetime):
            return oldest
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    with _lock:
        found = [
            dict(job) for job in _jobs.values()
            if job.get("kind", KIND_CLIP) == KIND_STORY_STEP
            and job.get("story_id") == story_id
            and (step is None or job.get("step") == step)
            and (wanted is None or _status_value(job) in wanted)
        ]
    found.sort(key=lambda j: (created(j), j.get("id") or ""))
    return found


def delete_job(job_id: str) -> bool:
    """Delete a job. Returns True if found."""
    with _lock:
        if job_id in _jobs:
            del _jobs[job_id]
            _persist()
            return True
        return False


def get_running_count() -> int:
    """Count jobs currently in processing states."""
    processing = {JobStatus.DOWNLOADING.value, JobStatus.TRANSCRIBING.value,
                  JobStatus.ANALYZING.value, JobStatus.RENDERING.value,
                  JobStatus.RUNNING.value}
    with _lock:
        return sum(1 for j in _jobs.values() if j.get("status") in processing)


def get_queued_count() -> int:
    """Count jobs waiting to start."""
    with _lock:
        return sum(1 for j in _jobs.values() if j.get("status") == JobStatus.QUEUED.value)
