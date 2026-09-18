"""
The activity tee: does the pipeline's own output reach the job that printed it,
and can it ever break printing?

`web.api.activity` is deliberately stdlib-only so these run in the pytest-only
CI environment (DEC-012). The store tests at the bottom need pydantic and are
gated accordingly.
"""

import io
import threading

import pytest

from web.api import activity
from web.api import signals


def _tee(source="stdout"):
    """A tee over an in-memory stream, plus the list its sink writes to."""
    stream = io.StringIO()
    recorded = []

    def sink(job_id, message, level, source_name):
        recorded.append((job_id, message, level, source_name))

    return activity._Tee(stream, sink, source), stream, recorded


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def test_a_line_reaches_both_the_real_stream_and_the_sink():
    tee, stream, recorded = _tee()
    with activity.capture("job-1"):
        tee.write("🔁 NVIDIA attempt 2/3...\n")
    assert stream.getvalue() == "🔁 NVIDIA attempt 2/3...\n"
    assert recorded == [("job-1", "🔁 NVIDIA attempt 2/3...", "warn", "stdout")]


def test_nothing_is_recorded_outside_a_capture_block():
    tee, stream, recorded = _tee()
    tee.write("INFO:     Uvicorn running on http://0.0.0.0:8000\n")
    assert stream.getvalue().endswith("8000\n")
    assert recorded == []


def test_a_line_split_across_writes_is_recorded_once_whole():
    tee, _, recorded = _tee()
    with activity.capture("job-1"):
        tee.write("[3/3] Analyzing Top 7 ")
        assert recorded == []  # not terminated yet
        tee.write("moments using NVIDIA\n")
    assert [m for _, m, _, _ in recorded] == ["[3/3] Analyzing Top 7 moments using NVIDIA"]


def test_carriage_returns_terminate_lines():
    """tqdm redraws with \\r; that redraw is the only proof Whisper is alive."""
    tee, _, recorded = _tee()
    with activity.capture("job-1"):
        tee.write("Transcribing:  10%\rTranscribing:  20%\rTranscribing:  30%\n")
    assert [m for _, m, _, _ in recorded] == [
        "Transcribing:  10%",
        "Transcribing:  20%",
        "Transcribing:  30%",
    ]


def test_an_immediately_repeated_line_is_dropped():
    tee, _, recorded = _tee()
    with activity.capture("job-1"):
        tee.write("same\nsame\nsame\nother\nsame\n")
    assert [m for _, m, _, _ in recorded] == ["same", "other", "same"]


def test_blank_lines_are_not_recorded():
    tee, stream, recorded = _tee()
    with activity.capture("job-1"):
        tee.write("\n\n   \nreal\n")
    assert [m for _, m, _, _ in recorded] == ["real"]
    assert stream.getvalue() == "\n\n   \nreal\n"  # still printed verbatim


def test_drain_emits_an_unterminated_trailing_line():
    tee, _, recorded = _tee()
    with activity.capture("job-1"):
        tee.write("⏳ Decoding audio & extracting features")
        assert recorded == []
        tee.drain()
    assert [m for _, m, _, _ in recorded] == ["⏳ Decoding audio & extracting features"]


def test_stdout_and_stderr_buffers_do_not_interleave():
    out, _, out_recorded = _tee("stdout")
    err, _, err_recorded = _tee("stderr")
    with activity.capture("job-1"):
        out.write("half of stdout ")
        err.write("a whole stderr line\n")
        out.write("and its other half\n")
    assert [m for _, m, _, _ in out_recorded] == ["half of stdout and its other half"]
    assert [m for _, m, _, _ in err_recorded] == ["a whole stderr line"]


def test_capture_restores_the_previous_binding():
    assert activity.current_job_id() is None
    with activity.capture("outer"):
        assert activity.current_job_id() == "outer"
        with activity.capture("inner"):
            assert activity.current_job_id() == "inner"
        assert activity.current_job_id() == "outer"
    assert activity.current_job_id() is None


def test_another_thread_is_not_captured():
    """One job's binding must never claim another thread's output."""
    tee, _, recorded = _tee()
    seen = []

    def other():
        seen.append(activity.current_job_id())
        tee.write("from an unbound thread\n")

    with activity.capture("job-1"):
        t = threading.Thread(target=other)
        t.start()
        t.join()
    assert seen == [None]
    assert recorded == []


def test_a_long_line_is_truncated_not_dropped():
    tee, _, recorded = _tee()
    with activity.capture("job-1"):
        tee.write("x" * (activity.MAX_LINE_CHARS + 500) + "\n")
    assert len(recorded) == 1
    assert len(recorded[0][1]) == activity.MAX_LINE_CHARS


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

def test_a_failing_sink_cannot_break_printing():
    stream = io.StringIO()

    def exploding_sink(*_args):
        raise RuntimeError("the store is on fire")

    tee = activity._Tee(stream, exploding_sink, "stdout")
    with activity.capture("job-1"):
        written = tee.write("this must still be printed\n")
    assert stream.getvalue() == "this must still be printed\n"
    assert written == len("this must still be printed\n")


def test_a_sink_that_prints_does_not_feed_itself():
    stream = io.StringIO()
    recorded = []

    def printing_sink(job_id, message, level, source):
        recorded.append(message)
        tee.write("the sink printing something\n")

    tee = activity._Tee(stream, printing_sink, "stdout")
    with activity.capture("job-1"):
        tee.write("original\n")
    assert recorded == ["original"]  # no recursion, no flood


def test_unknown_attributes_are_delegated_to_the_real_stream():
    tee, stream, _ = _tee()
    assert tee.isatty() is False
    assert tee.writable() is True
    tee.flush()  # must not raise


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "line,expected",
    [
        ("🔥 [Rank 1] Processing clip", "info"),
        ("✅ Found a maximum of 2 face(s)", "info"),
        ("⚠️ CUDA was requested but this CTranslate2 build cannot use a GPU", "warn"),
        ("🔁 NVIDIA attempt 2/3...", "warn"),
        ("[Gemini] Attempt 3/10 failed | status=503", "warn"),
        ("❌ Gemini fallback failed", "error"),
        ("Traceback (most recent call last):", "error"),
    ],
)
def test_levels_are_classified_from_the_pipeline_markers(line, expected):
    assert activity.classify(line) == expected


# ---------------------------------------------------------------------------
# Retry ladder extraction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "line,expected",
    [
        ("   🔁 NVIDIA attempt 2/3...", (2, 3)),
        ("[Gemini] Attempt 3/10...", (3, 10)),
        ("[Gemini] Attempt 10/10 failed | status=503 | error=overloaded", (10, 10)),
        ("attempt 1 / 3", (1, 3)),
        # Not a retry ladder.
        ("🔥 [Rank 1] Processing clip", None),
        ("Rendering clip 3/7...", None),
        ("attempt 5/3", None),          # position past the end
        ("attempt 0/3", None),          # ladders are 1-based
        ("attempt 1/9999", None),       # implausible: a false positive
    ],
)
def test_the_retry_ladder_is_read_out_of_the_printed_line(line, expected):
    assert signals.attempt_from(line) == expected


def test_a_reworded_retry_print_degrades_rather_than_breaks():
    """The counter disappears; the line itself is still shown verbatim."""
    assert signals.attempt_from("NVIDIA is having another go") is None
    assert activity.classify("NVIDIA is having another go") == "info"


# ---------------------------------------------------------------------------
# The store side of the feed (needs pydantic -- see DEC-012)
# ---------------------------------------------------------------------------

@pytest.fixture()
def store():
    pytest.importorskip("pydantic")
    from web.api import store as store_mod

    created = []

    class _Sandbox:
        """Talk to the real store, but never let it write jobs.json."""

        def __init__(self):
            self.mod = store_mod

        def new_job(self):
            job_id = store_mod.create_job()
            created.append(job_id)
            return job_id

    original_persist = store_mod._persist
    store_mod._persist = lambda force=True: None
    try:
        yield _Sandbox()
    finally:
        # Delete BEFORE restoring: delete_job persists, and these tests must
        # never write the real outputs/jobs.json.
        for job_id in created:
            store_mod.delete_job(job_id)
        store_mod._persist = original_persist


def test_pipeline_output_lands_on_the_job(store):
    job_id = store.new_job()
    store.mod.append_event(job_id, "🔁 NVIDIA attempt 2/3...", "warn", "stdout")
    events = store.mod.get_events_since(job_id)
    assert [e["message"] for e in events] == ["🔁 NVIDIA attempt 2/3..."]
    assert events[0]["level"] == "warn"
    assert events[0]["source"] == "stdout"
    assert events[0]["seq"] == 1


def test_progress_updates_join_the_same_feed(store):
    """Otherwise the console reads as if the steps never happened."""
    job_id = store.new_job()
    store.mod.update_progress(
        job_id, step="analyze", step_number=3, total_steps=7,
        message="Analyzing with AI...", percent=36.0,
    )
    events = store.mod.get_events_since(job_id)
    assert [(e["message"], e["level"], e["source"]) for e in events] == [
        ("Analyzing with AI...", "step", "worker")
    ]
    # ...and the flat log it always had is untouched.
    assert store.mod.get_job(job_id)["log"] == ["[analyze] Analyzing with AI..."]


def test_an_error_step_is_recorded_as_an_error(store):
    job_id = store.new_job()
    store.mod.update_progress(
        job_id, step="error", step_number=0, total_steps=7,
        message="Pipeline failed: RuntimeError: boom", percent=0.0,
    )
    assert store.mod.get_events_since(job_id)[0]["level"] == "error"


def test_the_feed_is_capped_and_keeps_the_newest(store):
    job_id = store.new_job()
    for i in range(store.mod.MAX_EVENTS + 50):
        store.mod.append_event(job_id, f"line {i}")
    events = store.mod.get_job(job_id)["events"]
    assert len(events) == store.mod.MAX_EVENTS
    assert events[-1]["message"] == f"line {store.mod.MAX_EVENTS + 49}"
    assert events[0]["message"] == "line 50"


def test_the_cursor_survives_ring_buffer_truncation(store):
    """A list index would silently shift as the buffer drops from the front."""
    job_id = store.new_job()
    for i in range(store.mod.MAX_EVENTS + 50):
        store.mod.append_event(job_id, f"line {i}")
    last_seq = store.mod.get_job(job_id)["events"][-1]["seq"]
    assert store.mod.get_events_since(job_id, last_seq) == []
    # Strictly after: the client already has `last_seq - 3` itself.
    tail = store.mod.get_events_since(job_id, last_seq - 3)
    assert [e["message"] for e in tail] == [
        f"line {store.mod.MAX_EVENTS + 47}",
        f"line {store.mod.MAX_EVENTS + 48}",
        f"line {store.mod.MAX_EVENTS + 49}",
    ]


def test_events_for_an_unknown_job_are_dropped_silently(store):
    store.mod.append_event("no-such-job", "orphan line")
    assert store.mod.get_events_since("no-such-job") == []
