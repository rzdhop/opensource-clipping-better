"""Claude on the Anthropic Messages API, behind the OpenAI chat-completions
shape (plan 23 stage D1, ``clipping/providers/anthropic_llm.py``).

Every test drives a fake SDK client (``anthropic_llm._sdk_client`` replaced):
no network, and the key is a test value that never leaves the process. One
test reads the real SDK's signatures and skips without it (DEC-012: CI
installs pytest and nothing else).
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

from clipping.providers import anthropic_llm, errors, llm, pacing
from clipping.providers.registry import Link

SONNET = Link("anthropic", "claude-sonnet-5-5")
OPUS_XHIGH = Link("anthropic", "claude-opus-5-5@xhigh")
FREE = Link("gemini", "gemini-test")
KEY = "test-anthropic-key"
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 3, "maxLength": 80},
        "beats": {"type": "array", "minItems": 1, "maxItems": 12,
                  "items": {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1}},
                            "required": ["n"]}},
        # A property NAMED like a bound is a name, not a keyword.
        "maximum": {"type": "number", "maximum": 9},
    },
    "required": ["title", "beats", "maximum"],
    "additionalProperties": True,
}
VALID = json.dumps({"title": "Le verger", "beats": [{"n": 1}], "maximum": 3})


def message(text=VALID, *, stop="end_turn", model="claude-sonnet-5-5", usage=None, content=None,
            stop_details=None):
    usage = usage or SimpleNamespace(input_tokens=40, cache_read_input_tokens=900, cache_creation_input_tokens=0,
                                     output_tokens=120, output_tokens_details=SimpleNamespace(thinking_tokens=70),
                                     iterations=None)
    blocks = content if content is not None else [SimpleNamespace(type="thinking", thinking=""),
                                                  SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=blocks, stop_reason=stop, stop_details=stop_details, model=model, usage=usage)


class FakeSdk:
    """The Anthropic SDK client's stand-in: ``beta.messages.create`` answers
    from a queue (a message, or an exception to raise) and records each
    request; ``models.retrieve`` records the model asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent = []
        self.retrieved = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.models = SimpleNamespace(retrieve=self._retrieve)

    def _create(self, **request):
        self.sent.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def _retrieve(self, model_id):
        self.retrieved.append(model_id)
        return SimpleNamespace(id=model_id)


class BadRequestError(Exception):
    status_code = 400


@pytest.fixture(autouse=True)
def _reset():
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    pacing.reset_limiters()
    yield
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    pacing.reset_limiters()


@pytest.fixture
def sdk(monkeypatch):
    holder = SimpleNamespace(fake=FakeSdk(), built=[])

    def make(**kwargs):
        holder.built.append(kwargs)
        return holder.fake

    monkeypatch.setattr(anthropic_llm, "_sdk_client", make)
    return holder


def _complete(link=SONNET, *, schema=SCHEMA, effort=None, max_tokens=900, temperature=0.4):
    client = llm.LlmClient(
        link, api_key=KEY,
        client_factory=lambda lnk, *, api_key, timeout: llm.build_client(lnk, api_key=api_key, timeout=timeout,
                                                                          effort=effort),
        on_log=lambda line: None,
    )
    return client, client.complete_json(system="Tu écris la bible.", user="Écris-la.", schema=schema,
                                        schema_name="bible", max_tokens=max_tokens, temperature=temperature)


# ----------------------------------------------------------- the request

def test_the_body_is_translated(sdk):
    sdk.fake.answers.append(message())
    _client, value = _complete(effort="high")
    assert value == json.loads(VALID)

    [request] = sdk.fake.sent
    assert request["model"] == "claude-sonnet-5-5"
    assert request["max_tokens"] == 900
    assert request["messages"] == [{"role": "user", "content": "Écris-la."}]
    # The system prompt is one cached text block.
    assert request["system"] == [{"type": "text", "text": "Tu écris la bible.", "cache_control": {"type": "ephemeral"}}]
    # Server-side fallback ON, the "default" form and its beta.
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    assert request["fallbacks"] == "default"
    assert request["output_config"]["effort"] == "high"
    assert request["output_config"]["format"]["type"] == "json_schema"
    # Dropped or never sent: sampling, thinking, prefill, tool_choice.
    for absent in ("temperature", "top_p", "top_k", "thinking", "tool_choice", "extra_body", "response_format"):
        assert absent not in request
    assert all(m["role"] == "user" for m in request["messages"])


def test_the_sdk_client_is_built_with_the_key_and_the_links_timeout(sdk):
    sdk.fake.answers.append(message())
    _complete()
    assert sdk.built == [{"api_key": KEY, "timeout": 240.0, "base_url": "https://api.anthropic.com"}]


def test_the_schema_is_sanitised():
    out = anthropic_llm.sanitize_schema(SCHEMA)
    assert out["additionalProperties"] is False
    assert out["properties"]["title"] == {"type": "string"}
    beats = out["properties"]["beats"]
    assert "minItems" not in beats and "maxItems" not in beats
    assert beats["items"]["additionalProperties"] is False
    assert beats["items"]["properties"]["n"] == {"type": "integer"}
    # The property named "maximum" is kept; its own bound is stripped.
    assert out["properties"]["maximum"] == {"type": "number"}
    assert out["required"] == ["title", "beats", "maximum"]
    assert SCHEMA["properties"]["title"]["minLength"] == 3   # the caller's schema is untouched


def test_the_effort_suffix_is_stripped_from_the_model_and_no_effort_means_none_sent(sdk):
    sdk.fake.answers.extend([message(model="claude-opus-5-5"), message()])
    _complete(OPUS_XHIGH, effort="xhigh")
    _complete(SONNET, effort=None)
    first, second = sdk.fake.sent
    assert first["model"] == "claude-opus-5-5" and first["output_config"]["effort"] == "xhigh"
    assert "effort" not in second["output_config"]


def test_json_object_and_prompt_only_send_no_format():
    body = {"model": "claude-sonnet-5-5", "max_tokens": 10, "temperature": 0.2,
            "messages": [{"role": "user", "content": "x"}], "response_format": {"type": "json_object"}}
    assert "output_config" not in anthropic_llm.translate_body(body)
    del body["response_format"]
    assert "output_config" not in anthropic_llm.translate_body(body)
    assert "system" not in anthropic_llm.translate_body(body)


# ------------------------------------------------------------- the reply

def test_the_usage_is_mapped_to_the_openai_shape_with_the_served_model(sdk):
    sdk.fake.answers.append(message(model="claude-sonnet-5"))
    client, _value = _complete()
    usage = client.last_usage
    assert usage.prompt_tokens == 40 + 900 + 0
    assert usage.completion_tokens == 120
    assert usage.total_tokens == 1060
    assert (usage.input_tokens, usage.cache_read_input_tokens, usage.cache_creation_input_tokens) == (40, 900, 0)
    assert usage.thinking_tokens == 70


def test_the_reply_carries_the_served_model_fallbacks_and_iterations():
    iterations = [
        SimpleNamespace(type="message", model=None, input_tokens=500, cache_read_input_tokens=0,
                        cache_creation_input_tokens=0, output_tokens=0),
        SimpleNamespace(type="fallback_message", model="claude-sonnet-5", input_tokens=500,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=90),
    ]
    usage = SimpleNamespace(input_tokens=500, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                            output_tokens=90, output_tokens_details=None, iterations=iterations)
    blocks = [SimpleNamespace(type="fallback", from_=SimpleNamespace(model="claude-sonnet-5-5"),
                              to=SimpleNamespace(model="claude-sonnet-5")),
              SimpleNamespace(type="text", text=VALID)]
    reply = anthropic_llm.shape_reply(message(model="claude-sonnet-5", usage=usage, content=blocks))
    assert reply.choices[0].message.content == VALID
    assert reply.model == "claude-sonnet-5"
    assert reply.fallbacks == [{"from": "claude-sonnet-5-5", "to": "claude-sonnet-5"}]
    assert [row["type"] for row in reply.iterations] == ["message", "fallback_message"]
    assert reply.iterations[1]["model"] == "claude-sonnet-5" and reply.iterations[1]["output_tokens"] == 90


def test_a_refusal_is_fatal_with_its_usage_attached(sdk):
    sdk.fake.answers.append(message(text="", stop="refusal", stop_details=SimpleNamespace(category="cyber")))
    with pytest.raises(errors.ModelRefusedError) as caught:
        _complete()
    assert errors.classify(caught.value) == errors.FATAL
    assert caught.value.reply.usage.completion_tokens == 120
    assert "cyber" in str(caught.value)


def test_a_reply_stopped_at_max_tokens_without_json_is_fatal(sdk):
    sdk.fake.answers.append(message(text='{"title": "Le ver', stop="max_tokens"))
    with pytest.raises(errors.OutputTruncatedError) as caught:
        _complete()
    assert errors.classify(caught.value) == errors.FATAL
    assert caught.value.reply.usage.completion_tokens == 120


def test_a_reply_stopped_at_max_tokens_with_complete_json_is_kept(sdk):
    sdk.fake.answers.append(message(stop="max_tokens"))
    _client, value = _complete()
    assert value == json.loads(VALID)


def test_a_schema_400_steps_the_ladder_down_to_prompt_only(sdk):
    refused = BadRequestError("output_config.format.schema: this keyword is not supported")
    assert errors.rejects_structured_output(refused)
    sdk.fake.answers.extend([refused, message()])
    log = []
    client = llm.LlmClient(SONNET, api_key=KEY, on_log=log.append)
    value = client.complete_json(system="s", user="u", schema=SCHEMA, max_tokens=50)
    assert value == json.loads(VALID)
    first, second = sdk.fake.sent
    assert "format" in first["output_config"]
    assert "output_config" not in second
    assert any("rejected json_schema" in line for line in log)


def test_a_bare_format_word_is_not_read_as_a_refused_schema():
    assert not errors.rejects_structured_output(BadRequestError("invalid request format"))


def test_a_refusal_moves_the_chain_to_its_next_link_without_a_retry(sdk):
    sdk.fake.answers.append(message(text="", stop="refusal"))
    free_sent = []

    def factory(link, *, api_key, timeout):
        if link.provider == "anthropic":
            return llm.build_client(link, api_key=api_key, timeout=timeout)

        def create(**body):
            free_sent.append(body)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=VALID))],
                                   usage=SimpleNamespace(total_tokens=10))
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    log = []
    value, link = llm.run_chain([SONNET, FREE], system="s", user="u", schema=SCHEMA, max_tokens=50,
                                keys={"anthropic": KEY, "gemini": "test-gemini-key"}, on_log=log.append,
                                client_factory=factory, sleep_fn=lambda seconds: None)
    assert link == FREE and value == json.loads(VALID)
    assert len(sdk.fake.sent) == 1          # FATAL: never asked again
    assert len(free_sent) == 1
    assert any("ModelRefusedError" in line for line in log)


def test_check_model_is_a_free_models_lookup(sdk):
    client = llm.build_client(OPUS_XHIGH, api_key=KEY, timeout=45)
    client.check_model()
    assert sdk.fake.retrieved == ["claude-opus-5-5"]
    assert sdk.fake.sent == []


# --------------------------------------------------- the real SDK's shape

def test_the_request_matches_the_real_sdks_signatures():
    """Read-only: the installed SDK's own signatures accept every keyword the
    adapter sends. Nothing is constructed against the network."""
    anthropic = pytest.importorskip("anthropic")
    from anthropic.resources.beta.messages import Messages
    from anthropic.resources.models import Models

    body = {"model": "claude-sonnet-5-5@high", "max_tokens": 100, "temperature": 0.3,
            "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "x", "schema": SCHEMA}}}
    request = anthropic_llm.translate_body(body, effort="high")
    params = inspect.signature(Messages.create).parameters
    assert set(request) <= set(params), set(request) - set(params)
    assert "model_id" in inspect.signature(Models.retrieve).parameters
    init = inspect.signature(anthropic.Anthropic.__init__).parameters
    assert {"api_key", "base_url", "max_retries", "timeout"} <= set(init)
    client = anthropic_llm._sdk_client(api_key=KEY, timeout=30, base_url="https://api.anthropic.com")
    assert client.max_retries == 0
