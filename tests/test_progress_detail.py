"""
Does a progress event say WHO is being asked WHAT, and how long it has been
asking?

`message` names the step. These fields are the difference between "Analyzing
with AI..." for forty minutes and "NVIDIA / deepseek-v4-flash, attempt 2 of 3,
12 minutes on this step".

Needs pydantic, so the whole module skips in the pytest-only CI env (DEC-012).
"""

import time

import pytest

pytest.importorskip("pydantic")

from web.api import store as store_mod  # noqa: E402


@pytest.fixture()
def job():
    """A real job in the real store, with persistence disabled."""
    original_persist = store_mod._persist
    store_mod._persist = lambda force=True: None
    job_id = store_mod.create_job()
    try:
        yield job_id
    finally:
        store_mod.delete_job(job_id)
        store_mod._persist = original_persist


def progress_of(job_id):
    return store_mod.get_job(job_id)["progress"]


# ---------------------------------------------------------------------------
# Who is being asked what
# ---------------------------------------------------------------------------

def test_the_provider_and_model_travel_with_the_event(job):
    store_mod.update_progress(
        job, step="analyze", step_number=3, total_steps=7,
        message="Asking nvidia for the best moments...", percent=36.0,
        provider="nvidia", model="deepseek-ai/deepseek-v4-flash-0731",
    )
    event = progress_of(job)
    assert event.provider == "nvidia"
    assert event.model == "deepseek-ai/deepseek-v4-flash-0731"


def test_the_render_loop_reports_which_clip(job):
    store_mod.update_progress(
        job, step="render", step_number=6, total_steps=7,
        message="Rendering clip 3/7...", percent=75.0,
        clip_index=3, clip_total=7,
    )
    event = progress_of(job)
    assert (event.clip_index, event.clip_total) == (3, 7)


def test_fields_left_out_stay_none(job):
    store_mod.update_progress(
        job, step="metadata", step_number=4, total_steps=7,
        message="Metadata normalized.", percent=55.0,
    )
    event = progress_of(job)
    assert event.provider is None and event.clip_index is None and event.attempt is None


# ---------------------------------------------------------------------------
# Time in step
# ---------------------------------------------------------------------------

def test_staying_on_a_step_keeps_the_clock_it_started_with(job):
    """Time-in-step is the number that tells a user something is stuck."""
    store_mod.update_progress(
        job, step="render", step_number=6, total_steps=7,
        message="Rendering clip 1/7...", percent=65.0,
    )
    started = progress_of(job).step_started_at
    time.sleep(0.01)
    store_mod.update_progress(
        job, step="render", step_number=6, total_steps=7,
        message="Rendering clip 2/7...", percent=70.0,
    )
    assert progress_of(job).step_started_at == started


def test_moving_to_a_new_step_restarts_the_clock(job):
    store_mod.update_progress(
        job, step="analyze", step_number=3, total_steps=7,
        message="Asking...", percent=36.0,
    )
    first = progress_of(job).step_started_at
    time.sleep(0.01)
    store_mod.update_progress(
        job, step="render", step_number=6, total_steps=7,
        message="Preparing rendering...", percent=60.0,
    )
    assert progress_of(job).step_started_at > first


# ---------------------------------------------------------------------------
# Refinement from the pipeline's own output
# ---------------------------------------------------------------------------

def test_a_printed_line_refines_the_step_without_advancing_it(job):
    store_mod.update_progress(
        job, step="analyze", step_number=3, total_steps=7,
        message="Asking nvidia for the best moments...", percent=36.0,
        provider="nvidia", model="deepseek-ai/deepseek-v4-flash-0731",
    )
    before = progress_of(job)
    store_mod.refine_progress(job, detail="🔁 NVIDIA attempt 2/3...", attempt=2, max_attempts=3)
    after = progress_of(job)

    assert after.detail == "🔁 NVIDIA attempt 2/3..."
    assert (after.attempt, after.max_attempts) == (2, 3)
    # Everything that describes the STEP is untouched.
    assert (after.step, after.percent, after.message) == (before.step, before.percent, before.message)
    assert after.provider == "nvidia" and after.step_started_at == before.step_started_at


def test_refining_before_any_progress_exists_is_a_no_op(job):
    store_mod.refine_progress(job, detail="something printed very early")
    assert progress_of(job) is None


def test_refining_an_unknown_job_is_silent():
    store_mod.refine_progress("no-such-job", detail="orphan")  # must not raise


def test_advancing_the_step_clears_the_previous_detail(job):
    store_mod.update_progress(
        job, step="analyze", step_number=3, total_steps=7, message="Asking...", percent=36.0,
    )
    store_mod.refine_progress(job, detail="🔁 NVIDIA attempt 3/3...", attempt=3, max_attempts=3)
    store_mod.update_progress(
        job, step="metadata", step_number=4, total_steps=7,
        message="Metadata normalized.", percent=55.0,
    )
    event = progress_of(job)
    assert event.detail is None and event.attempt is None


# ---------------------------------------------------------------------------
# The worker's sink and its step descriptions
# ---------------------------------------------------------------------------

def test_the_sink_files_the_line_and_refines_the_step(job):
    from web.api import worker

    store_mod.update_progress(
        job, step="analyze", step_number=3, total_steps=7, message="Asking...", percent=36.0,
    )
    worker._record_pipeline_line(job, "🔁 NVIDIA attempt 2/3...", "warn", "stdout")

    event = progress_of(job)
    assert event.detail == "🔁 NVIDIA attempt 2/3..."
    assert (event.attempt, event.max_attempts) == (2, 3)
    assert [e["message"] for e in store_mod.get_events_since(job)][-1] == "🔁 NVIDIA attempt 2/3..."


def test_an_ordinary_line_becomes_the_detail_without_an_attempt(job):
    from web.api import worker

    store_mod.update_progress(
        job, step="render", step_number=6, total_steps=7, message="Rendering...", percent=65.0,
    )
    worker._record_pipeline_line(job, "🔥 [Rank 1] Processing clip", "info", "stdout")
    event = progress_of(job)
    assert event.detail == "🔥 [Rank 1] Processing clip"
    assert event.attempt is None


def test_a_very_long_line_is_trimmed_for_the_headline_but_kept_in_the_feed(job):
    from web.api import worker

    store_mod.update_progress(
        job, step="render", step_number=6, total_steps=7, message="Rendering...", percent=65.0,
    )
    line = "y" * (worker.MAX_DETAIL_CHARS + 200)
    worker._record_pipeline_line(job, line, "info", "stdout")
    assert len(progress_of(job).detail) == worker.MAX_DETAIL_CHARS
    assert len(store_mod.get_events_since(job)[-1]["message"]) == len(line)


class _Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_a_supplied_transcript_says_whisper_is_skipped():
    from web.api import worker

    headline, detail = worker._transcript_plan(_Cfg(transcript_path="/uploads/talk.vtt"))
    assert "transcript you supplied" in headline
    assert "talk.vtt" in detail and "skipped" in detail


def test_whisper_warns_up_front_that_it_is_the_slow_path():
    from web.api import worker

    headline, detail = worker._transcript_plan(
        _Cfg(transcript_path=None, whisper_model="large-v3",
             whisper_device="auto", whisper_compute_type="auto")
    )
    assert "large-v3" in headline
    assert "auto" in detail and "slow path" in detail and ".vtt" in detail
