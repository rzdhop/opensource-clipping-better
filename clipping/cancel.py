"""
clipping.cancel — cooperative cancellation for a running job.

The web backend runs a job in a worker thread, and a thread cannot be stopped
from outside. So a job stops the way a long loop does: it asks, at each point
where the next step would start spending -- a request to a provider, a chunk to
transcribe, a segment of Whisper output -- and stops if the answer is yes.
Whatever is already in flight finishes or times out; nothing new starts.

The CLI never sets a token. ``token_of`` then returns ``NEVER``, and callers
use ``kwargs_for`` so that a CLI call does not even gain the keyword.

``Cancelled`` derives from BaseException, not Exception, on purpose. The
pipeline has dozens of ``except Exception`` blocks whose job is to record a
failure and try the next provider or clip; a cancel caught by one of them would
be reported as a failure and retried.

Stdlib only: importable in the pytest-only CI environment (DEC-012).
"""

import threading
import time


class Cancelled(BaseException):
    """The job this thread is running was cancelled."""


class CancelToken:
    """Set once, from any thread; checked by the thread doing the work."""

    def __init__(self):
        self._event = threading.Event()

    def cancel(self):
        self._event.set()

    @property
    def cancelled(self):
        return self._event.is_set()

    def check(self):
        if self._event.is_set():
            raise Cancelled("The job was cancelled.")

    def sleep(self, seconds):
        """``time.sleep`` that a cancel cuts short -- then raises."""
        if seconds > 0:
            self._event.wait(seconds)
        self.check()

    def sleeper(self, sleep_fn):
        """*sleep_fn*, made cancellable.

        The real ``time.sleep`` becomes an interruptible wait. An injected one
        (a test's) is kept, and bracketed by checks.
        """
        if sleep_fn is time.sleep:
            return self.sleep

        def _sleep(seconds):
            self.check()
            sleep_fn(seconds)
            self.check()

        return _sleep


class _Never(CancelToken):
    def cancel(self):
        raise RuntimeError(
            "NEVER is shared by every job without a token; it cannot be cancelled."
        )


NEVER = _Never()


def token_of(cfg):
    """The job's token, or ``NEVER`` for a run that has none (the CLI)."""
    token = getattr(cfg, "cancel_token", None)
    return token if token is not None else NEVER


def kwargs_for(token):
    """``{"cancel": token}`` for a real token, ``{}`` for ``NEVER``.

    Lets a caller pass the token on without changing the call a CLI run makes,
    or the call an injected stand-in (a test's fake ``run_chain``) receives.
    """
    return {} if token is NEVER else {"cancel": token}
