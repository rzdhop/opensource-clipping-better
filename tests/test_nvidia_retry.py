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


def test_the_schema_is_sent_via_response_format(run):
    """Structured output goes through the OpenAI-standard response_format.

    It used to go through extra_body["nvext"]["guided_json"], which the default
    model rejects with a 400 -- confirmed against the live endpoint. This test
    previously asserted that broken mechanism.
    """
    _, client = run([GOOD_JSON])
    rf = client.calls[0]["response_format"]

    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"]["type"] == "array"
    assert rf["json_schema"]["strict"] is True


def test_guided_json_is_no_longer_sent(run):
    """Pin the removal: nvext.guided_json is a 400 on the shipped model."""
    _, client = run([GOOD_JSON])

    assert "nvext" not in client.calls[0].get("extra_body", {})


def test_response_format_rejection_falls_back_to_prompt_only(run):
    """A model that refuses the parameter should still produce clips."""
    rejection = _exc("BadRequestError", 400)
    rejection.args = ("400 - unknown field `response_format`",)

    result, client = run([rejection, GOOD_JSON])

    assert result == [GOOD_CLIP]
    assert "response_format" in client.calls[0], "first attempt should try it"
    assert "response_format" not in client.calls[1], "retry should drop it"


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


# ---------------------------------------------------------------------------
# response_format rejection
#
# The shipped code asked for structured output via nvext.guided_json, which the
# default model rejects outright:
#   400 unknown field `guided_json`, expected one of `greed_sampling`, ...
# Probing the live endpoint showed response_format={"type":"json_schema"} works,
# so that is now the primary path -- but not every model implements it, and a
# model that refuses it says so with a 400 naming the parameter. That is not a
# sampling failure: retrying the same body cannot help, retrying without the
# parameter can.
# ---------------------------------------------------------------------------

from clipping.engine import _nvidia_rejects_response_format


class _Err(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def test_unknown_response_format_field_is_detected():
    exc = _Err("Error code: 400 - unknown field `response_format`, expected one of ...")

    assert _nvidia_rejects_response_format(exc) is True


def test_unsupported_json_schema_is_detected():
    exc = _Err("400 - json_schema is not supported by this model")

    assert _nvidia_rejects_response_format(exc) is True


def test_an_ordinary_bad_request_is_not_mistaken_for_it():
    """A 400 about something else must not silently drop structured output."""
    exc = _Err("Error code: 400 - messages[0].content too long")

    assert _nvidia_rejects_response_format(exc) is False


def test_a_server_error_is_not_mistaken_for_it():
    exc = _Err("500 - internal error mentioning response_format", status_code=500)

    assert _nvidia_rejects_response_format(exc) is False


def test_auth_failure_is_not_mistaken_for_it():
    exc = _Err("401 - invalid api key", status_code=401)

    assert _nvidia_rejects_response_format(exc) is False


def test_the_real_guided_json_rejection_is_still_fatal():
    """The message that actually shipped. It names guided_json, not
    response_format, so it must NOT trigger the response_format fallback."""
    exc = _Err(
        "Error code: 400 - {'message': 'Failed to deserialize the JSON body into "
        "the target type: unknown field `guided_json`, expected one of "
        "`greed_sampling`, `use_raw_prompt`...'}"
    )

    assert _nvidia_rejects_response_format(exc) is False


# ------------------------------------------------- the client's own retry policy

def test_client_disables_the_sdks_own_retries(monkeypatch):
    """The ladder in analyze_with_nvidia must be the ONLY retry policy.

    Regression test for a real 2h22m job failure: the SDK defaults to
    max_retries=2 and retries any status >= 500, so each of the 3 scripted
    attempts was silently 3 http requests. Three deterministic 504s at ~300s
    each cost 45 minutes instead of 15, and the log reported 3 attempts while
    9 requests had been made.
    """
    captured = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import sys
    from types import ModuleType, SimpleNamespace

    fake_module = ModuleType("openai")
    fake_module.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    engine._make_nvidia_client(SimpleNamespace(api_key_nvidia="k"))

    assert captured["max_retries"] == 0, (
        "the openai SDK retries >=500 twice by default; leaving that on "
        "multiplies every attempt in the ladder by three"
    )
    assert captured["timeout"] == engine.NVIDIA_REQUEST_TIMEOUT_SECONDS
    assert captured["base_url"] == "https://integrate.api.nvidia.com/v1"


def test_request_timeout_exceeds_the_measured_gateway_limit():
    """Probed live: the gateway 504s at ~302s. A shorter local timeout would
    race it and replace an informative 504 with a bare client timeout."""
    assert engine.NVIDIA_REQUEST_TIMEOUT_SECONDS > 300
    # ...but not so long that a stalled request blocks for the SDK's 600s default
    assert engine.NVIDIA_REQUEST_TIMEOUT_SECONDS < 600
