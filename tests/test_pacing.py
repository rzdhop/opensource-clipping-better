"""Client-side rate pacing. The clock is fake; no test ever sleeps."""

import pytest

from clipping.providers import pacing
from clipping.providers.pacing import Limiter


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


def _limiter(clock, **kwargs):
    return Limiter("test", time_fn=clock.time, sleep_fn=clock.sleep, **kwargs)


# ----------------------------------------------------------------------- rpm

def test_requests_under_the_limit_never_wait(clock):
    limiter = _limiter(clock, rpm=5)
    for _ in range(5):
        assert limiter.acquire() == 0.0
    assert clock.sleeps == []


def test_the_request_over_the_limit_waits_for_the_window_to_roll(clock):
    limiter = _limiter(clock, rpm=3)
    for _ in range(3):
        limiter.acquire()
    assert limiter.acquire() == pytest.approx(60.0)
    assert clock.now == pytest.approx(60.0)


def test_the_window_slides_rather_than_resetting(clock):
    """Three requests at t=0,10,20 under rpm=3: the fourth waits only until the
    FIRST ages out at t=60, not for a fresh 60s window."""
    limiter = _limiter(clock, rpm=3)
    for offset in (0, 10, 20):
        clock.now = float(offset)
        limiter.acquire()
    clock.now = 30.0
    assert limiter.acquire() == pytest.approx(30.0)


def test_no_rpm_means_no_request_pacing(clock):
    limiter = _limiter(clock, rpm=None)
    for _ in range(50):
        limiter.acquire()
    assert clock.sleeps == []


# ----------------------------------------------------------------------- tpm

def test_token_ceiling_paces_even_when_request_count_is_fine(clock):
    """Groq's binding limit. 8000 TPM with 3000-token requests means the third
    one waits, though only two requests have been made."""
    limiter = _limiter(clock, rpm=30, tpm=8000)
    limiter.acquire(3000)
    limiter.acquire(3000)
    assert limiter.acquire(3000) == pytest.approx(60.0)


def test_recorded_usage_corrects_the_estimate(clock):
    """The estimate reserves 3000; the provider reports 500, so the window
    frees up and the next request goes straight out."""
    limiter = _limiter(clock, tpm=8000)
    for _ in range(3):
        limiter.acquire(3000)      # reserves the estimate
        limiter.record(500)        # corrected by what the provider reported
    # Three 3000-token reservations would have tripped the 8000 ceiling; three
    # recorded 500s do not, and the window holds the truth.
    assert clock.sleeps == []
    assert limiter.snapshot()[1] == pytest.approx(1500)


def test_no_tpm_means_no_token_pacing(clock):
    limiter = _limiter(clock, rpm=100, tpm=None)
    for _ in range(10):
        assert limiter.acquire(999999) == 0.0


def test_a_request_larger_than_the_whole_ceiling_does_not_hang(clock):
    """It cannot ever fit, so waiting forever helps nobody — let the provider
    answer and classify its error."""
    limiter = _limiter(clock, tpm=1000)
    limiter.acquire(100)
    limiter.acquire(50_000)
    assert clock.now <= 60.0


def test_record_before_any_request_is_a_no_op(clock):
    _limiter(clock, tpm=100).record(50)  # must not raise


def test_record_none_is_ignored(clock):
    limiter = _limiter(clock, tpm=8000)
    limiter.acquire(100)
    limiter.record(None)
    assert limiter.snapshot()[1] == pytest.approx(100)


# ------------------------------------------------------------------- sharing

def test_limiters_are_shared_per_provider():
    """The rate limit is per API key, not per client object: two passes building
    their own clients must queue behind one another."""
    from clipping.providers.registry import PROVIDERS

    pacing.reset_limiters()
    try:
        first = pacing.limiter_for(PROVIDERS["groq"])
        second = pacing.limiter_for(PROVIDERS["groq"])
        other = pacing.limiter_for(PROVIDERS["nvidia"])
        assert first is second
        assert first is not other
    finally:
        pacing.reset_limiters()


def test_limiter_inherits_the_providers_published_limits():
    from clipping.providers.registry import PROVIDERS

    pacing.reset_limiters()
    try:
        limiter = pacing.limiter_for(PROVIDERS["groq"])
        assert limiter.rpm == 30 and limiter.tpm == 8000
    finally:
        pacing.reset_limiters()


# ------------------------------------------------------------------ estimate

@pytest.mark.parametrize("text,expected", [("", 1), ("a" * 4, 1), ("a" * 400, 100)])
def test_estimate_tokens(text, expected):
    assert pacing.estimate_tokens(text) == expected


def test_estimate_tokens_ignores_none():
    assert pacing.estimate_tokens(None, "a" * 40) == 10
