"""Client-side rate pacing, so a free tier is used rather than tripped.

Every provider in the registry publishes a requests-per-minute and sometimes a
tokens-per-minute ceiling. Groq's TPM is the binding one: 8000 tokens/minute on
the fastest free model means the analyzer's twelve small requests have to be
spread out, and a burst earns a 429 that costs more wall-clock than the wait
would have.

This is pacing, not enforcement. It keeps us under the published limit when the
published limit is right; ``errors.RATE_LIMITED`` handling in ``llm.py`` is what
copes when it is not.

The clock is injected so tests drive it with a fake and never sleep. Stdlib only.
"""

from __future__ import annotations

import threading
import time
from collections import deque

WINDOW_SECONDS = 60.0


class Limiter:
    """Sliding-window limiter over requests and tokens per minute.

    A sliding window rather than a token bucket because the limits being
    respected are literally phrased as "N per minute", and a bucket's refill
    rate lets a burst through at a window boundary — which is exactly when a
    retry ladder tends to fire.
    """

    def __init__(self, name, rpm=None, tpm=None, *, time_fn=None, sleep_fn=None):
        self.name = name
        self.rpm = rpm if rpm and rpm > 0 else None
        self.tpm = tpm if tpm and tpm > 0 else None
        self._time = time_fn or time.monotonic
        self._sleep = sleep_fn or time.sleep
        self._events = deque()  # (timestamp, tokens)
        self._lock = threading.Lock()
        self.total_slept = 0.0

    # -------------------------------------------------------------- internals

    def _prune(self, now):
        cutoff = now - WINDOW_SECONDS
        while self._events and self._events[0][0] <= cutoff:
            self._events.popleft()

    def _wait_for_request_slot(self, now):
        """Seconds until the window has room for one more request."""
        if self.rpm is None or len(self._events) < self.rpm:
            return 0.0
        oldest = self._events[0][0]
        return max(0.0, oldest + WINDOW_SECONDS - now)

    def _wait_for_token_room(self, now, est_tokens):
        """Seconds until the window has room for *est_tokens* more tokens."""
        if self.tpm is None or est_tokens <= 0:
            return 0.0
        used = sum(tokens for _, tokens in self._events)
        if used + est_tokens <= self.tpm:
            return 0.0
        # Drop the oldest events until there is room, and wait for the last of
        # them to age out.
        freed = 0
        needed = used + est_tokens - self.tpm
        for timestamp, tokens in self._events:
            freed += tokens
            if freed >= needed:
                return max(0.0, timestamp + WINDOW_SECONDS - now)
        # Even an empty window cannot fit this request; do not wait forever for
        # something that will never fit — let the provider answer.
        return max(0.0, self._events[0][0] + WINDOW_SECONDS - now) if self._events else 0.0

    # ----------------------------------------------------------------- public

    def acquire(self, est_tokens=0):
        """Block until the request may go out. Returns the seconds slept."""
        slept = 0.0
        while True:
            with self._lock:
                now = self._time()
                self._prune(now)
                wait = max(
                    self._wait_for_request_slot(now),
                    self._wait_for_token_room(now, est_tokens),
                )
                if wait <= 0:
                    self._events.append((now, max(0, int(est_tokens))))
                    self.total_slept += slept
                    return slept
            self._sleep(wait)
            slept += wait

    def record(self, actual_tokens):
        """Correct the last reservation with the usage the provider reported."""
        if actual_tokens is None:
            return
        with self._lock:
            if self._events:
                timestamp, _ = self._events[-1]
                self._events[-1] = (timestamp, max(0, int(actual_tokens)))

    def snapshot(self):
        """``(requests_in_window, tokens_in_window)`` — for tests and logging."""
        with self._lock:
            self._prune(self._time())
            return len(self._events), sum(t for _, t in self._events)


_LIMITERS = {}
_LIMITERS_LOCK = threading.Lock()


def limiter_for(provider, *, time_fn=None, sleep_fn=None):
    """The process-wide limiter for a :class:`registry.Provider`.

    Shared per provider name, because the rate limit is per API key, not per
    client object: two analyzer passes building their own clients must still
    queue behind one another.
    """
    with _LIMITERS_LOCK:
        existing = _LIMITERS.get(provider.name)
        if existing is None:
            existing = Limiter(
                provider.name,
                rpm=provider.rpm,
                tpm=provider.tpm,
                time_fn=time_fn,
                sleep_fn=sleep_fn,
            )
            _LIMITERS[provider.name] = existing
        return existing


def reset_limiters():
    """Drop every cached limiter. For tests."""
    with _LIMITERS_LOCK:
        _LIMITERS.clear()


def estimate_tokens(*texts):
    """A deliberately rough token estimate: ~4 characters per token.

    Used only to pace against a TPM ceiling before the real usage is known, and
    corrected by :meth:`Limiter.record` the moment the provider reports it. A
    tokenizer would be more accurate and would mean shipping one per provider
    family for a number that is superseded seconds later.
    """
    total = sum(len(t) for t in texts if t)
    return max(1, total // 4)
