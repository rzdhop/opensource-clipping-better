"""
web.api.activity — attribute the pipeline's own console output to the job that
produced it.

The clipping pipeline already narrates itself in detail. It prints the AI
provider and model it is about to call, each retry attempt and why the last one
failed, the Whisper device it settled on, ``🔥 [Rank 3] Processing clip``, every
render sub-stage, and the model downloads that otherwise look like a hang. All
of it goes to the server's stdout, where the dashboard cannot see any of it — so
a job that sits at 36% for forty minutes while Gemini walks its ten-attempt
backoff ladder looks, from the browser, completely dead.

This module tees stdout and stderr so those lines are also recorded against the
job whose worker thread produced them.

Why a tee and not a progress callback
-------------------------------------
``run_pipeline(cfg)`` takes no progress hook, and neither does
``studio.proses_klip``. Threading one through ``runner.py``, ``engine.py`` and
``studio/core.py`` would mean changing the CLI pipeline's signatures and editing
the render layer, which the regression contract protects. The print statements
already say exactly what we want to show the user, so we read them where they
are and leave ``clipping/`` alone.

Why this is safe
----------------
* The real stream is written FIRST, and its return value is what callers get.
  Recording happens afterwards inside a ``try``, so a failure in the sink can
  never break a ``print``.
* A line is recorded only when the calling thread has bound a job id via
  :func:`capture`. Uvicorn's own logging, and anything else in the process, is
  therefore untouched — it passes straight through.
* The sink runs with a re-entrancy guard, so a sink that printed something would
  not feed itself.

What it cannot capture
----------------------
Subprocess output. ffmpeg inherits the real file descriptors and never passes
through Python's ``sys.stdout``, so its output stays in the container log. The
same is true of anything CTranslate2 or OpenCV write from C. What we get is the
Python-level narration, which is the part that names steps, providers and
models.
"""

from __future__ import annotations

import re
import sys
import threading
from contextlib import contextmanager
from typing import Callable, Optional

# One line of pipeline output can be a full metadata dump. Keep the feed
# readable and bounded rather than storing a paragraph per event.
MAX_LINE_CHARS = 2000

# tqdm redraws its bar with a carriage return rather than a newline, and that
# redraw is precisely the "still alive" signal worth surfacing. Treat \r as a
# line terminator too.
_LINE_SPLIT = re.compile(r"\r\n|\r|\n")

LEVEL_INFO = "info"
LEVEL_WARN = "warn"
LEVEL_ERROR = "error"

# The pipeline is consistent about its emoji, which makes them a more reliable
# severity signal than English keywords that also occur in ordinary prose.
_ERROR_MARKERS = ("❌", "Traceback (most recent call last)")
_WARN_MARKERS = ("⚠️", "🔁")

# "RuntimeError: NVIDIA analysis failed after 3 attempt(s):" — the line of a
# traceback that actually says what went wrong, and the one a user needs to find
# in a wall of frames.
_EXCEPTION_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Exit)\b\s*:")

_local = threading.local()
_install_lock = threading.Lock()
_installed = False


def classify(line: str, source: str = "stdout") -> str:
    """Guess a severity for a line of pipeline output. Never raises.

    ``source`` matters: nothing in ``clipping/`` writes to stderr, so anything
    captured there during a job is a problem — in practice the worker's failure
    traceback. Its frames are not individually alarming, but they are not
    ordinary narration either, so stderr's floor is a warning rather than an
    error, and the markers still promote the lines that matter.
    """
    if any(marker in line for marker in _ERROR_MARKERS) or _EXCEPTION_LINE.match(line):
        return LEVEL_ERROR
    if source == "stderr":
        return LEVEL_WARN
    if any(marker in line for marker in _WARN_MARKERS):
        return LEVEL_WARN
    # "attempt 2 failed", "Diarization failed: ..." — a real problem the user
    # should see, but one the pipeline recovers from on its own.
    if "failed" in line.lower():
        return LEVEL_WARN
    return LEVEL_INFO


class _Tee:
    """A stdout/stderr stand-in that also reports lines to the current job.

    Deliberately not an ``io.TextIOBase`` subclass: unknown attributes are
    delegated to the wrapped stream, so libraries that reach for ``.buffer``,
    ``.isatty()`` or anything else find the real thing.
    """

    def __init__(self, stream, sink: Callable[[str, str, str, str], None], source: str):
        self._stream = stream
        self._sink = sink
        self._source = source

    # -- stream interface ---------------------------------------------------

    def write(self, s):
        written = self._stream.write(s)
        try:
            self._record(s)
        except Exception:
            pass  # recording must never break printing
        return written

    def flush(self):
        return self._stream.flush()

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def __getattr__(self, name):
        # Guarded against recursion: if _stream itself is missing (an attribute
        # touched before __init__ finished) this would otherwise call itself.
        if name == "_stream":
            raise AttributeError(name)
        return getattr(self._stream, name)

    # -- recording ----------------------------------------------------------

    def _state(self) -> dict:
        """Per-thread, per-stream buffer. stdout and stderr must not interleave."""
        states = getattr(_local, "states", None)
        if states is None:
            states = {}
            _local.states = states
        return states.setdefault(self._source, {"buffer": "", "last": None})

    def _record(self, s: str) -> None:
        job_id = getattr(_local, "job_id", None)
        if not job_id or not s:
            return
        if getattr(_local, "recording", False):
            return  # the sink printed something; do not feed it back

        state = self._state()
        parts = _LINE_SPLIT.split(state["buffer"] + s)
        state["buffer"] = parts.pop()  # trailing partial line, not yet terminated
        self._emit(job_id, state, parts)

    def _emit(self, job_id: str, state: dict, lines) -> None:
        _local.recording = True
        try:
            for raw in lines:
                line = raw.strip()
                if not line or line == state["last"]:
                    continue  # a redrawn progress bar repeats itself constantly
                state["last"] = line
                self._sink(
                    job_id,
                    line[:MAX_LINE_CHARS],
                    classify(line, self._source),
                    self._source,
                )
        finally:
            _local.recording = False

    def drain(self) -> None:
        """Emit a trailing line that was never terminated (a bare tqdm bar)."""
        job_id = getattr(_local, "job_id", None)
        state = self._state()
        pending, state["buffer"] = state["buffer"], ""
        if job_id and pending.strip():
            self._emit(job_id, state, [pending])


_stdout_tee: Optional[_Tee] = None
_stderr_tee: Optional[_Tee] = None


def install(sink: Callable[[str, str, str, str], None]) -> None:
    """Tee ``sys.stdout``/``sys.stderr`` into ``sink``. Idempotent.

    ``sink(job_id, message, level, source)`` is called once per complete line,
    and only for threads inside a :func:`capture` block.
    """
    global _installed, _stdout_tee, _stderr_tee
    with _install_lock:
        if _installed:
            return
        _stdout_tee = _Tee(sys.stdout, sink, "stdout")
        _stderr_tee = _Tee(sys.stderr, sink, "stderr")
        sys.stdout = _stdout_tee
        sys.stderr = _stderr_tee
        _installed = True


@contextmanager
def capture(job_id: str):
    """Attribute everything this thread prints to ``job_id`` for the duration."""
    previous = getattr(_local, "job_id", None)
    _local.job_id = job_id
    try:
        yield
    finally:
        for tee in (_stdout_tee, _stderr_tee):
            if tee is not None:
                try:
                    tee.drain()
                except Exception:
                    pass
        _local.job_id = previous
        _local.states = {}


def current_job_id() -> Optional[str]:
    """The job this thread's output is being attributed to, if any."""
    return getattr(_local, "job_id", None)
