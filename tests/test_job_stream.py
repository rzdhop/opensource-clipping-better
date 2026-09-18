"""
The SSE stream: does a watching browser get the pipeline's output as it happens,
and can it tell a quiet job from a dead one?

Needs fastapi and pydantic, so the whole module skips in the pytest-only CI
environment (DEC-012).
"""

import asyncio
import json

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("fastapi")

from web.api import store as store_mod          # noqa: E402
from web.api.models import JobStatus            # noqa: E402
from web.api.routes import jobs as jobs_route   # noqa: E402


class _StopStream(Exception):
    """Safety valve: ends the otherwise infinite 1s poll loop."""


@pytest.fixture()
def job():
    original_persist = store_mod._persist
    store_mod._persist = lambda force=True: None
    job_id = store_mod.create_job()
    try:
        yield job_id
    finally:
        store_mod.delete_job(job_id)
        store_mod._persist = original_persist


def drive(monkeypatch, job_id, ticks, on_tick=None):
    """Run the stream for `ticks` poll cycles and return the decoded frames."""
    remaining = {"n": ticks}

    async def fake_sleep(_seconds):
        if on_tick is not None:
            on_tick(ticks - remaining["n"])
        remaining["n"] -= 1
        if remaining["n"] <= 0:
            raise _StopStream

    monkeypatch.setattr(jobs_route.asyncio, "sleep", fake_sleep)

    frames = []

    async def run():
        response = await jobs_route.job_status_sse(job_id)
        try:
            async for chunk in response.body_iterator:
                text = chunk.decode() if isinstance(chunk, bytes) else chunk
                for line in text.splitlines():
                    if line.startswith("data: "):
                        frames.append(json.loads(line[len("data: "):]))
        except _StopStream:
            pass

    asyncio.run(run())
    return frames


def frames_of(frames, kind):
    return [f for f in frames if f.get("type") == kind]


# ---------------------------------------------------------------------------

def test_the_stream_replays_the_activity_feed(monkeypatch, job):
    store_mod.update_progress(
        job, step="analyze", step_number=3, total_steps=7,
        message="Asking nvidia for the best moments...", percent=36.0,
        provider="nvidia", model="deepseek-ai/deepseek-v4-flash-0731",
    )
    store_mod.append_event(job, "🔁 NVIDIA attempt 2/3...", "warn", "stdout")

    frames = drive(monkeypatch, job, ticks=2)

    feed = frames_of(frames, "events")
    assert feed, "the stream never sent the activity feed"
    messages = [e["message"] for f in feed for e in f["events"]]
    assert "🔁 NVIDIA attempt 2/3..." in messages


def test_the_progress_frame_carries_the_provider_and_model(monkeypatch, job):
    store_mod.update_progress(
        job, step="analyze", step_number=3, total_steps=7,
        message="Asking nvidia for the best moments...", percent=36.0,
        provider="nvidia", model="deepseek-ai/deepseek-v4-flash-0731",
    )
    frames = drive(monkeypatch, job, ticks=2)
    progress = frames_of(frames, "progress")[0]["progress"]
    assert progress["provider"] == "nvidia"
    assert progress["model"] == "deepseek-ai/deepseek-v4-flash-0731"
    assert progress["step_started_at"] is not None


def test_an_event_is_sent_once(monkeypatch, job):
    """The cursor must advance, or a long job re-sends its whole feed every second."""
    store_mod.append_event(job, "first line")
    frames = drive(monkeypatch, job, ticks=5)
    messages = [e["message"] for f in frames_of(frames, "events") for e in f["events"]]
    assert messages.count("first line") == 1


def test_a_quiet_stream_still_says_it_is_alive(monkeypatch, job):
    """One AI call can hold a job silent for tens of minutes."""
    store_mod.update_progress(
        job, step="analyze", step_number=3, total_steps=7, message="Asking...", percent=36.0,
    )
    quiet = drive(monkeypatch, job, ticks=5)
    assert frames_of(quiet, "heartbeat") == []

    long_quiet = drive(monkeypatch, job, ticks=20)
    assert frames_of(long_quiet, "heartbeat"), "an idle stream looks dead"


def test_the_last_lines_arrive_before_the_stream_closes(monkeypatch, job):
    """The lines that say WHY it failed are recorded after the status flips."""
    store_mod.set_error(job, "RuntimeError: boom")
    store_mod.append_event(job, "❌ the actual reason", "error", "stderr")

    frames = drive(monkeypatch, job, ticks=50)  # terminal state ends it early

    messages = [e["message"] for f in frames_of(frames, "events") for e in f["events"]]
    assert "❌ the actual reason" in messages


def test_a_job_deleted_mid_stream_ends_it(monkeypatch, job):
    """The browser is told, rather than left watching a stream that never moves."""
    def delete_after_first_tick(tick):
        if tick == 0:
            store_mod.delete_job(job)

    frames = drive(monkeypatch, job, ticks=10, on_tick=delete_after_first_tick)
    assert frames[-1] == {"type": "deleted"}
