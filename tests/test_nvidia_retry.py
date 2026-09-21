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
        nvidia_model="nvidia/nemotron-3-super-120b-a12b",
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


# ------------------------------------------- the generic OpenAI-compatible path
#
# analyze_with_nvidia is now a thin wrapper over _analyze_openai_compatible,
# which also serves a user-supplied endpoint (OpenRouter, Groq, Mistral, xAI, a
# local Ollama). These tests pin the three things that must not blur together:
# the fake-client seam, the NIM-only extra_body, and the fail-fast gate.

@pytest.fixture
def compat_cfg(cfg):
    cfg.ai_provider = "openai_compat"
    cfg.api_key_openai_compat = "compat-key"
    cfg.openai_compat_base_url = "https://openrouter.ai/api/v1"
    cfg.openai_compat_model = "meta-llama/llama-3.3-70b-instruct"
    return cfg


def test_make_nvidia_client_is_still_the_seam(monkeypatch, cfg):
    """The single most dangerous regression in the extraction.

    Every test in this file replaces engine._make_nvidia_client. If the wrapper
    ever captured it as a default argument instead of looking it up at call
    time, the patch would stop taking effect and these 'unit' tests would start
    making real HTTP calls while still passing locally.
    """
    client = FakeClient([GOOD_JSON])
    monkeypatch.setattr(engine, "_make_nvidia_client", lambda c: client)

    engine.analyze_with_nvidia("[0.0 - 1.0] hi\n", cfg)

    assert client.calls, "the patched factory was never used"


def test_nvidia_still_sends_the_nim_chat_template_kwarg(run):
    _, client = run([GOOD_JSON])

    assert client.calls[0]["extra_body"] == {"chat_template_kwargs": {"thinking": False}}


def test_openai_compat_sends_no_vendor_extra_body(monkeypatch, compat_cfg):
    """Sending NIM's extra_body to another provider would 400 the request."""
    client = FakeClient([GOOD_JSON])
    monkeypatch.setattr(engine, "_make_openai_compat_client", lambda c: client)

    result = engine.analyze_with_openai_compat("[0.0 - 1.0] hi\n", compat_cfg)

    assert result == [GOOD_CLIP]
    assert "extra_body" not in client.calls[0]
    assert client.calls[0]["model"] == "meta-llama/llama-3.3-70b-instruct"


def test_openai_compat_still_asks_for_structured_output(monkeypatch, compat_cfg):
    client = FakeClient([GOOD_JSON])
    monkeypatch.setattr(engine, "_make_openai_compat_client", lambda c: client)

    engine.analyze_with_openai_compat("[0.0 - 1.0] hi\n", compat_cfg)

    assert client.calls[0]["response_format"]["type"] == "json_schema"


def test_openai_compat_keeps_the_response_format_fallback(monkeypatch, compat_cfg):
    """DEC-013 applies here more than anywhere: an arbitrary endpoint is the
    most likely one to reject json_schema."""
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    rejection = _exc("BadRequestError", status=400)
    rejection.args = ("unknown field `response_format`",)
    client = FakeClient([rejection, GOOD_JSON])
    monkeypatch.setattr(engine, "_make_openai_compat_client", lambda c: client)

    assert engine.analyze_with_openai_compat("x", compat_cfg) == [GOOD_CLIP]
    assert "response_format" in client.calls[0]
    assert "response_format" not in client.calls[1]


def test_base_url_trailing_slash_is_trimmed(compat_cfg):
    compat_cfg.openai_compat_base_url = "https://openrouter.ai/api/v1/"

    assert engine._openai_compat_base_url(compat_cfg) == "https://openrouter.ai/api/v1"


def test_log_label_names_the_host_not_the_whole_url(compat_cfg):
    """Some gateways carry a token in the URL path, and this string lands in
    job logs the user may share."""
    compat_cfg.openai_compat_base_url = "https://gw.example.com/v1/secret-token-abc"

    assert engine._openai_compat_host(compat_cfg) == "gw.example.com"


def test_dispatch_routes_to_openai_compat(monkeypatch, compat_cfg):
    monkeypatch.setattr(
        engine, "analyze_with_openai_compat", lambda *a, **k: [GOOD_CLIP]
    )

    assert engine.analyze_with_ai("transcript", compat_cfg) == [GOOD_CLIP]


@pytest.mark.parametrize(
    "attr,env_name",
    [
        ("api_key_openai_compat", "OPENAI_COMPAT_API_KEY"),
        ("openai_compat_base_url", "OPENAI_COMPAT_BASE_URL"),
        ("openai_compat_model", "OPENAI_COMPAT_MODEL"),
    ],
)
def test_dispatch_fails_fast_on_a_half_configured_endpoint(
    monkeypatch, compat_cfg, attr, env_name
):
    """A base URL with no model fails as surely as a missing key -- and should
    fail just as early, before ingestion and transcription have run."""
    setattr(compat_cfg, attr, "")
    monkeypatch.setattr(
        engine, "analyze_with_openai_compat", lambda *a, **k: pytest.fail("should not run")
    )

    with pytest.raises(RuntimeError, match=env_name):
        engine.analyze_with_ai("transcript", compat_cfg)


def test_unknown_provider_lists_all_three_choices(cfg):
    cfg.ai_provider = "openai"  # still not a provider: the id is openai_compat

    with pytest.raises(ValueError, match="openai_compat"):
        engine.analyze_with_ai("transcript", cfg)


# --------------------------------------------- salvaging a reasoning model's reply
#
# Observed live, from a reasoning model asked for a strict json_schema array via
# a custom OpenAI-compatible endpoint: the content began with a bare "[" on its
# own line, followed by the real array. finish_reason was "stop" and nothing was
# truncated -- the model had simply leaked a fragment of its own scratchpad.
# NVIDIA's own path never sees this because it suppresses the reasoning pass with
# a NIM-only extra_body, but an arbitrary endpoint has no such switch.

LEAKED = '[\n[{"rank": 1, "start_time": 0, "end_time": 8, "title_indonesia": "x"}]'


def test_salvages_a_leading_scratchpad_fragment():
    assert engine._extract_clip_list(LEAKED) == [
        {"rank": 1, "start_time": 0, "end_time": 8, "title_indonesia": "x"}
    ]


def test_salvage_does_not_disturb_well_formed_content():
    """The fast path must stay the fast path."""
    assert engine._extract_clip_list(GOOD_JSON) == [GOOD_CLIP]


def test_salvage_ignores_brackets_inside_strings():
    """A naive regex would cut the span short at the ']' in the title."""
    raw = 'preamble [{"start_time": 1, "end_time": 2, "title": "a]b}c"}] trailing'

    assert engine._extract_clip_list(raw) == [
        {"start_time": 1, "end_time": 2, "title": "a]b}c"}
    ]


def test_salvage_finds_an_object_when_there_is_no_array():
    raw = 'Here you go:\n{"start_time": 1, "end_time": 2}\nhope that helps'

    assert engine._extract_clip_list(raw) == [{"start_time": 1, "end_time": 2}]


def test_unsalvageable_content_still_raises_retryably():
    """Genuine rubbish must stay retryable, not be silently rescued."""
    with pytest.raises((ValueError, json.JSONDecodeError)):
        engine._extract_clip_list("the model apologised and returned prose")


def test_salvaged_content_still_faces_the_shape_checks():
    """Salvage must not become a way to smuggle an invalid clip through."""
    with pytest.raises(ValueError, match="missing required field"):
        engine._extract_clip_list('[\n[{"rank": 1, "title_indonesia": "no times"}]')
