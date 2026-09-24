"""
web.api.children — kill a cancelled job's child processes.

The render layer runs ffmpeg as a subprocess from about a dozen call sites, and
a clip can spend minutes of CPU inside one of them. The pipeline's own cancel
checks (clipping/cancel.py) only run between steps, so without this a cancelled
job would still finish encoding the clip it was on.

Rather than thread a handle through every call site in an untested layer, this
installs a process-wide ``subprocess.Popen`` subclass that records each child
against the job whose thread started it -- the same per-thread attribution the
stdout tee uses (activity.py). Threads with no job, which is every thread except
the workers', pass straight through.

Two guarantees, both from the token checked at spawn:
- A cancelled job starts nothing. The refusal happens before the process
  exists, so an ``ffmpeg -y`` never gets to truncate an output file.
- A cancel landing between that check and the child's registration -- when
  ``kill`` could not yet see it -- is caught by a second check afterwards.

Stdlib only.
"""

from __future__ import annotations

import subprocess
import threading
import weakref
from contextlib import contextmanager

ORIGINAL_POPEN = subprocess.Popen

_local = threading.local()
_lock = threading.Lock()
_children: dict[str, "weakref.WeakSet"] = {}
_installed = False


def _kill_quietly(proc) -> None:
    try:
        proc.kill()
    except OSError:
        pass  # it exited on its own in the meantime


class _TrackedPopen(ORIGINAL_POPEN):
    def __init__(self, *args, **kwargs):
        job_id = getattr(_local, "job_id", None)
        token = getattr(_local, "token", None)
        if token is not None:
            token.check()
        super().__init__(*args, **kwargs)
        if job_id is None:
            return
        with _lock:
            _children.setdefault(job_id, weakref.WeakSet()).add(self)
        if token is not None and token.cancelled:
            _kill_quietly(self)


def install() -> None:
    """Route every ``subprocess.Popen`` through the tracker. Idempotent."""
    global _installed
    with _lock:
        if not _installed:
            subprocess.Popen = _TrackedPopen
            _installed = True


def uninstall() -> None:
    """Restore the original ``Popen`` (for tests)."""
    global _installed
    with _lock:
        subprocess.Popen = ORIGINAL_POPEN
        _installed = False


@contextmanager
def attributed(job_id: str, token):
    """Attribute every child this thread starts to *job_id*, checked against
    *token*, for the duration."""
    previous = (getattr(_local, "job_id", None), getattr(_local, "token", None))
    _local.job_id, _local.token = job_id, token
    try:
        yield
    finally:
        _local.job_id, _local.token = previous


def kill(job_id: str) -> int:
    """Kill *job_id*'s live children. Returns how many were still running."""
    with _lock:
        procs = list(_children.get(job_id, ()))
    killed = 0
    for proc in procs:
        if proc.poll() is None:
            _kill_quietly(proc)
            killed += 1
    return killed


def forget(job_id: str) -> None:
    """Drop *job_id*'s record once its worker has finished."""
    with _lock:
        _children.pop(job_id, None)
