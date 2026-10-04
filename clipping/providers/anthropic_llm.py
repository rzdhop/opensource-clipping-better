"""Claude on the Anthropic Messages API, behind the OpenAI chat-completions shape.

Plan 23 stage D1. Everything above the client -- ``llm.run_chain``, its retry
ladder and structured-output negotiation, ``llm_spend.Meter`` and the caps --
calls ``client.chat.completions.create(**openai_body)`` and reads
``choices[0].message.content`` and ``usage``. This module answers exactly that
call, so none of them changes: :func:`build_client` returns an object with
``.chat.completions.create`` that translates the OpenAI body into one
``client.beta.messages.create`` request and the reply back.

What the translation does, and why:

- **No SDK retries** (DEC-019): the client is built with ``max_retries=0``;
  the ladder in ``llm.py`` is the only one.
- **The key, and only the key** (RC-W4): ``api_key`` is passed explicitly, so
  the SDK reads no credential from the environment, and ``base_url`` is the
  registry's, so ``ANTHROPIC_BASE_URL`` cannot send the key elsewhere.
- **Server-side fallback ON** (the human's choice): ``betas=
  ["server-side-fallback-2026-07-01"]`` with ``fallbacks="default"`` -- the
  only form Claude Sonnet 5.5 accepts, Claude API only. A policy decline is
  re-run on Anthropic's recommended model inside the same call; the served
  model comes back as ``model`` and in ``usage.iterations``, and the reply is
  booked at the SERVED model's price (``llm_spend``).
- **The system prompt is one cached text block** (``cache_control:
  ephemeral``). Below the model's cacheable minimum it simply does not cache.
- **``temperature`` is dropped**: Claude Opus 5.5 rejects every sampling
  parameter and Claude Sonnet 5.5 any non-default value, so the ladder's
  "cool off each retry" has no effect on these links.
- **Structured output** is ``output_config.format`` (``json_schema``), the
  schema sanitised as the API requires (every object ``additionalProperties:
  false``; ``min*``/``max*``/``exclusive*``/``multipleOf`` constraints
  stripped -- the step's own validator still checks them). It is the first
  rung of the ladder; a schema 400 matches ``errors.rejects_structured_output``
  and steps down. ``json_object`` has no Anthropic equivalent and is sent
  prompt-only, as the bottom rung is -- ``jsonx.extract_json`` reads either.
- **``output_config.effort``** when the link or the prompt names one; omitted
  otherwise (the model's own default). No ``thinking`` (adaptive thinking is
  the default; Opus 5.5 cannot switch it off), no prefill, no ``tool_choice``.
- **A refusal is FATAL**: ``stop_reason == "refusal"`` (the whole fallback
  chain declined) raises ``errors.ModelRefusedError`` with the shaped reply
  attached, so it is booked at its real usage and the chain moves on, instead
  of an empty reply retried three times at full price. A reply stopped at
  ``max_tokens`` with no JSON in it raises ``errors.OutputTruncatedError``.
- **Usage in the OpenAI shape**: ``prompt_tokens = input + cache_read +
  cache_creation``, ``completion_tokens = output`` (thinking included, never
  counted twice), plus ``input_tokens``, ``cache_read_input_tokens``,
  ``cache_creation_input_tokens`` and ``thinking_tokens``; the reply carries
  the served ``model``, ``fallbacks`` (each declined -> continued switch) and
  ``iterations`` (each attempt's own usage and model).

:meth:`AnthropicChat.check_model` is the free liveness question
(``models.retrieve``): the Settings test and the preflight ping use it, never
a completion -- every completion is billed.

``anthropic`` is imported lazily, inside :func:`_sdk_client`, so this module
imports with only pytest installed (DEC-012). Stdlib at import.
"""

from __future__ import annotations

from types import SimpleNamespace

from . import errors, jsonx
from .registry import provider_for, split_effort

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODE = "default"

# JSON-schema keywords the structured-output API does not accept: numeric and
# string bounds and array/object size bounds. Removed before sending; the
# step's validator still enforces them on the reply.
_STRIPPED_PREFIXES = ("min", "max", "exclusive")
_STRIPPED_KEYS = frozenset({"multipleOf"})
# Keywords whose value is a single sub-schema, a list of them, or a map of them.
_SUBSCHEMA_ONE = ("items", "not", "if", "then", "else", "contains", "additionalItems")
_SUBSCHEMA_LIST = ("anyOf", "allOf", "oneOf", "prefixItems")
_SUBSCHEMA_MAP = ("properties", "$defs", "definitions", "patternProperties")


def sanitize_schema(schema):
    """A copy of *schema* the structured-output API accepts: every object node
    gains ``additionalProperties: false``; the bounds it rejects are removed.
    A property NAMED like a bound (``"maximum"`` inside ``properties``) is a
    name, not a keyword, and is kept."""
    if isinstance(schema, list):
        return [sanitize_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key in _STRIPPED_KEYS or (key != "additionalProperties" and key.startswith(_STRIPPED_PREFIXES)):
            continue
        if key in _SUBSCHEMA_MAP and isinstance(value, dict):
            out[key] = {name: sanitize_schema(sub) for name, sub in value.items()}
        elif key in _SUBSCHEMA_ONE or key in _SUBSCHEMA_LIST:
            out[key] = sanitize_schema(value)
        elif key == "additionalProperties":
            continue  # set below: anything but false is rejected
        else:
            out[key] = value
    types = out.get("type")
    if types == "object" or (isinstance(types, list) and "object" in types) or "properties" in out:
        out["additionalProperties"] = False
    return out


def _sdk_client(*, api_key, timeout, base_url):
    """The Anthropic SDK client. Substituted by tests (a fake answers)."""
    from anthropic import Anthropic

    return Anthropic(
        api_key=api_key,
        base_url=base_url,
        # DEC-019: the ladder in llm.py is the only retry policy.
        max_retries=0,
        timeout=timeout,
    )


def _field(obj, name, default=None):
    """*obj*'s *name*, whether *obj* is an SDK model or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _usage(usage):
    """The reply's usage in the OpenAI shape, with the Anthropic fields kept."""
    if usage is None:
        return None
    uncached = _count(_field(usage, "input_tokens"))
    cache_read = _count(_field(usage, "cache_read_input_tokens"))
    cache_write = _count(_field(usage, "cache_creation_input_tokens"))
    output = _count(_field(usage, "output_tokens"))
    details = _field(usage, "output_tokens_details")
    thinking = _field(details, "thinking_tokens") if details is not None else None
    prompt = uncached + cache_read + cache_write
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=output,
        total_tokens=prompt + output,
        input_tokens=uncached,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
        thinking_tokens=thinking if isinstance(thinking, int) else None,
    )


def _iterations(usage):
    """Each attempt of the call, as ``{"type", "model", "input_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens",
    "output_tokens"}`` -- a ``fallback_message`` entry marks the attempt a
    fallback model served; declined attempts are ``message`` entries."""
    rows = []
    entries = (_field(usage, "iterations") or []) if usage is not None else []
    for entry in entries:
        rows.append({
            "type": _field(entry, "type"),
            "model": _field(entry, "model"),
            "input_tokens": _count(_field(entry, "input_tokens")),
            "cache_read_input_tokens": _count(_field(entry, "cache_read_input_tokens")),
            "cache_creation_input_tokens": _count(_field(entry, "cache_creation_input_tokens")),
            "output_tokens": _count(_field(entry, "output_tokens")),
        })
    return rows


def _fallbacks(content):
    """``[{"from": model, "to": model}, ...]``: each switch point of the reply."""
    out = []
    for block in content or []:
        if _field(block, "type") != "fallback":
            continue
        source = _field(block, "from_") or _field(block, "from")
        target = _field(block, "to")
        out.append({"from": _field(source, "model"), "to": _field(target, "model")})
    return out


def _text(content):
    """The reply's text: its ``text`` blocks joined. Thinking blocks (empty
    by default on these models) and fallback markers are not the answer."""
    return "".join(_field(block, "text") or "" for block in content or [] if _field(block, "type") == "text")


def shape_reply(message):
    """*message* (a Messages API reply) in the OpenAI chat-completions shape."""
    content = _field(message, "content") or []
    text = _text(content)
    raw_usage = _field(message, "usage")
    stop = _field(message, "stop_reason")
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, role="assistant"), finish_reason=stop)],
        usage=_usage(raw_usage),
        model=_field(message, "model"),
        stop_reason=stop,
        stop_details=_field(message, "stop_details"),
        fallbacks=_fallbacks(content),
        iterations=_iterations(raw_usage),
    )


def translate_body(body, *, effort=None):
    """The ``beta.messages.create`` keyword arguments for one OpenAI-shaped
    *body* (what ``llm.LlmClient._body`` builds)."""
    system_parts, messages = [], []
    for message in body.get("messages") or []:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            if content:
                system_parts.append(str(content))
        else:
            messages.append({"role": role, "content": content})
    model = str(body["model"]).partition("@")[0]
    request = {
        "model": model,
        "max_tokens": int(body["max_tokens"]),
        "messages": messages,
        "betas": [FALLBACK_BETA],
        "fallbacks": FALLBACK_MODE,
    }
    if system_parts:
        request["system"] = [{"type": "text", "text": "\n\n".join(system_parts),
                              "cache_control": {"type": "ephemeral"}}]
    output_config = {}
    if effort:
        output_config["effort"] = effort
    response_format = body.get("response_format") or {}
    if response_format.get("type") == "json_schema":
        schema = (response_format.get("json_schema") or {}).get("schema")
        if schema:  # an empty schema constrains nothing: sent prompt-only
            output_config["format"] = {"type": "json_schema", "schema": sanitize_schema(schema)}
    if output_config:
        request["output_config"] = output_config
    # Deliberately not carried over: "temperature" (rejected by these models,
    # see the module docstring), "extra_body" (an OpenAI-compatible
    # provider's own additions), "response_format" json_object (no equivalent).
    return request


class _Completions:
    def __init__(self, chat):
        self._chat = chat

    def create(self, **body):
        return self._chat.complete(body)


class AnthropicChat:
    """One Anthropic link, answering ``.chat.completions.create(**openai_body)``
    and :meth:`check_model`. The SDK client is built on first use."""

    def __init__(self, link, *, api_key, timeout, effort=None):
        self.link = link
        self.model, _suffix = split_effort(link)
        self.effort = effort
        self._api_key = api_key
        self._timeout = timeout
        self._client = None
        self.chat = SimpleNamespace(completions=_Completions(self))

    @property
    def client(self):
        if self._client is None:
            self._client = _sdk_client(api_key=self._api_key, timeout=self._timeout,
                                       base_url=provider_for(self.link).base_url)
        return self._client

    def complete(self, body):
        request = translate_body(body, effort=self.effort)
        message = self.client.beta.messages.create(**request)
        reply = shape_reply(message)
        label = f"{self.link.provider}/{reply.model or self.model}"
        if reply.stop_reason == "refusal":
            category = _field(reply.stop_details, "category")
            why = f" ({category})" if category else ""
            raise errors.ModelRefusedError(
                f"{label} declined the request{why}; the fallback model, if any, declined too", reply=reply)
        if reply.stop_reason == "max_tokens":
            try:
                jsonx.extract_json(reply.choices[0].message.content)
            except Exception:  # noqa: BLE001 - any parse failure is the truncation
                raise errors.OutputTruncatedError(
                    f"{label} stopped at max_tokens ({request['max_tokens']}) with no complete JSON",
                    reply=reply) from None
        return reply

    def check_model(self):
        """The free liveness question: ``models.retrieve`` for this link's
        model. Raises what the SDK raises (a bad key, a model this key cannot
        use); never sends a completion."""
        return self.client.models.retrieve(self.model)


def build_client(link, *, api_key, timeout, effort=None):
    """The client ``llm.build_client`` returns for an ``api == "anthropic"``
    provider."""
    return AnthropicChat(link, api_key=api_key, timeout=timeout, effort=effort)
