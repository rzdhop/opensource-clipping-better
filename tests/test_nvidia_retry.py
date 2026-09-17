"""Tests for the NVIDIA NIM provider: retry, shape validation, and fail-fast.

No network. A fake client replays a scripted list of responses and exceptions.
"""

import json
from types import SimpleNamespace

import pytest

from clipping import engine


# ------------------------------------------------------------------ fakes

class _FakeCompletions:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        item = self._owner.scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=item))]
        )


class FakeClient:
    """Replays *scripted* items: a str is returned as content, an Exception raised."""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []
        self.chat = SimpleNamespace(completions=_FakeCompletions(self))


def _exc(name, status=None):
    """Build an exception whose class name matches an openai SDK error."""
    cls = type(name, (Exception,), {})
    err = cls(f"simulated {name}")
    if status is not None:
        err.status_code = status
    return err


GOOD_CLIP = {
    "rank": 1,
    "viral_score": 90,
    "start_time": 10.0,
    "end_time": 40.0,
    "title_indonesia": "judul",
}
GOOD_JSON = json.dumps([GOOD_CLIP])


@pytest.fixture
def cfg():
    return SimpleNamespace(
        api_key_nvidia="test-key",
        api_key_gemini="",
        ai_provider="nvidia",
        nvidia_model="deepseek-ai/deepseek-v4-flash-0731",
        jumlah_clip=3,
        durasi_hook=3,
        hook_v2=False,
        no_segment_trim=False,
        silence_trim=False,
    )


@pytest.fixture
def run(monkeypatch, cfg):
    """Return a callable that runs analyze_with_nvidia against a FakeClient."""
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)

    def _run(scripted, config=None):
        client = FakeClient(scripted)
        monkeypatch.setattr(engine, "_make_nvidia_client", lambda c: client)
        result = engine.analyze_with_nvidia("[0.0 - 1.0] hi\n", config or cfg)
        return result, client

    return _run


# ------------------------------------------------------------------- retry

def test_success_first_try(run):
    result, client = run([GOOD_JSON])
    assert result == [GOOD_CLIP]
    assert len(client.calls) == 1


def test_malformed_json_then_success(run):
    result, client = run(["not json at all", GOOD_JSON])
    assert result == [GOOD_CLIP]
    assert len(client.calls) == 2


def test_three_malformed_exhausts_and_raises(run):
    with pytest.raises(RuntimeError) as excinfo:
        run(["bad one", "bad two", "bad three"])

    message = str(excinfo.value)
    assert "3 attempt(s)" in message
    # Every attempt is reported, not just the last. Counts the per-attempt
    # detail lines specifically: the summary line now also contains the word
    # "attempt", so a bare count("attempt") would be 4 and would silently
    # depend on the summary's wording.
    assert message.count("\n  attempt ") == 3


def test_exhaustion_makes_exactly_max_attempts(monkeypatch, cfg):
    """Exactly NVIDIA_MAX_ATTEMPTS calls -- no more, no fewer."""
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    # Four items scripted, so an over-eager loop would NOT IndexError; the count
    # assertion below is the only thing catching it.
    client = FakeClient(["bad", "bad", "bad", GOOD_JSON])
    monkeypatch.setattr(engine, "_make_nvidia_client", lambda c: client)

    with pytest.raises(RuntimeError):
        engine.analyze_with_nvidia("[0.0 - 1.0] hi\n", cfg)

    assert len(client.calls) == engine.NVIDIA_MAX_ATTEMPTS == 3


def test_backoff_sleeps_between_attempts(monkeypatch, cfg):
    slept = []
    monkeypatch.setattr(engine.time, "sleep", lambda s: slept.append(s))
    client = FakeClient(["bad", "bad", GOOD_JSON])
    monkeypatch.setattr(engine, "_make_nvidia_client", lambda c: client)

    engine.analyze_with_nvidia("[0.0 - 1.0] hi\n", cfg)

    assert slept == list(engine.NVIDIA_BACKOFF_SECONDS[:2])


def test_retries_cool_the_temperature(run):
    _, client = run(["bad", GOOD_JSON])
    assert client.calls[0]["temperature"] > client.calls[1]["temperature"]


def test_guided_json_schema_is_sent(run):
    _, client = run([GOOD_JSON])
    extra = client.calls[0]["extra_body"]
    assert "guided_json" in extra["nvext"]
    assert extra["nvext"]["guided_json"]["type"] == "array"


# --------------------------------------------------------- error classing

def test_auth_error_is_fatal_and_not_retried(run):
    with pytest.raises(RuntimeError) as excinfo:
        run([_exc("AuthenticationError", 401), GOOD_JSON])

    # Stops after one attempt -- the second scripted item is never consumed.
    assert "1 attempt(s)" in str(excinfo.value)


def test_bad_request_is_fatal(run):
    """A malformed guided_json will never succeed on retry."""
    with pytest.raises(RuntimeError) as excinfo:
        run([_exc("BadRequestError", 400), GOOD_JSON])
    assert "1 attempt(s)" in str(excinfo.value)


def test_rate_limit_is_retried(run):
    result, client = run([_exc("RateLimitError", 429), GOOD_JSON])
    assert result == [GOOD_CLIP]
    assert len(client.calls) == 2


def test_server_error_is_retried(run):
    result, client = run([_exc("InternalServerError", 503), GOOD_JSON])
    assert result == [GOOD_CLIP]
    assert len(client.calls) == 2


def test_connection_error_is_retried(run):
    result, client = run([_exc("APIConnectionError"), GOOD_JSON])
    assert result == [GOOD_CLIP]
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "name,status,retryable",
    [
        ("AuthenticationError", 401, False),
        ("PermissionDeniedError", 403, False),
        ("NotFoundError", 404, False),
        ("BadRequestError", 400, False),
        ("RateLimitError", 429, True),
        ("InternalServerError", 500, True),
        ("APITimeoutError", None, True),
        ("APIConnectionError", None, True),
    ],
)
def test_is_retryable_classification(name, status, retryable):
    assert engine._nvidia_is_retryable(_exc(name, status)) is retryable


def test_unknown_exception_fails_closed():
    """An SDK rename must make an error non-retryable, not silently retryable."""
    assert engine._nvidia_is_retryable(_exc("SomeBrandNewError")) is False


# ------------------------------------------------------------ parse/shape

def test_json_fences_are_stripped(run):
    result, _ = run(["```json\n" + GOOD_JSON + "\n```"])
    assert result == [GOOD_CLIP]


def test_dict_wrapper_is_unwrapped(run):
    result, _ = run([json.dumps({"clips": [GOOD_CLIP]})])
    assert result == [GOOD_CLIP]


@pytest.mark.parametrize("key", ["clips", "data", "highlights"])
def test_all_wrapper_keys(run, key):
    result, _ = run([json.dumps({key: [GOOD_CLIP]})])
    assert result == [GOOD_CLIP]


def test_bare_dict_becomes_single_item_list(run):
    result, _ = run([json.dumps(GOOD_CLIP)])
    assert result == [GOOD_CLIP]


def test_empty_array_is_retried_then_fails(run):
    """A schema-conformant empty array used to sail through and detonate later
    inside metadata.normalize_and_validate."""
    with pytest.raises(RuntimeError):
        run(["[]", "[]", "[]"])


def test_empty_array_then_success(run):
    result, client = run(["[]", GOOD_JSON])
    assert result == [GOOD_CLIP]
    assert len(client.calls) == 2


def test_clip_missing_required_field_is_retried(run):
    incomplete = json.dumps([{"rank": 1, "start_time": 1.0}])  # no end_time
    result, client = run([incomplete, GOOD_JSON])
    assert result == [GOOD_CLIP]
    assert len(client.calls) == 2


def test_empty_content_is_retried(run):
    result, client = run(["   ", GOOD_JSON])
    assert result == [GOOD_CLIP]
    assert len(client.calls) == 2


@pytest.mark.parametrize("payload", ["not json", "[]", '{"nope": 1}', "   "])
def test_extract_clip_list_rejects(payload):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        engine._extract_clip_list(payload)


def test_extract_clip_list_accepts_good():
    assert engine._extract_clip_list(GOOD_JSON) == [GOOD_CLIP]


# --------------------------------------------------------------- dispatch

def test_dispatch_never_falls_back_to_gemini(monkeypatch, cfg):
    """A user who chose NVIDIA must never be silently billed on Gemini."""
    called = []
    monkeypatch.setattr(
        engine, "analyze_with_gemini", lambda *a, **k: called.append(1) or []
    )
    monkeypatch.setattr(
        engine, "analyze_with_nvidia", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("nvidia exploded")
        )
    )

    with pytest.raises(RuntimeError, match="nvidia exploded"):
        engine.analyze_with_ai("transcript", cfg)

    assert not called, "fell back to Gemini"


def test_dispatch_missing_nvidia_key_fails_before_any_call(monkeypatch, cfg):
    cfg.api_key_nvidia = ""
    monkeypatch.setattr(
        engine, "analyze_with_nvidia", lambda *a, **k: pytest.fail("should not run")
    )

    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY"):
        engine.analyze_with_ai("transcript", cfg)


def test_dispatch_missing_gemini_key_fails(monkeypatch, cfg):
    cfg.ai_provider = "gemini"
    cfg.api_key_gemini = ""

    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        engine.analyze_with_ai("transcript", cfg)


def test_dispatch_unknown_provider(cfg):
    cfg.ai_provider = "openai"
    with pytest.raises(ValueError, match="Unknown AI provider"):
        engine.analyze_with_ai("transcript", cfg)


def test_dispatch_routes_to_gemini(monkeypatch, cfg):
    cfg.ai_provider = "gemini"
    cfg.api_key_gemini = "g-key"
    monkeypatch.setattr(engine, "analyze_with_gemini", lambda *a, **k: [GOOD_CLIP])

    assert engine.analyze_with_ai("transcript", cfg) == [GOOD_CLIP]
