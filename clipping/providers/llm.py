"""One OpenAI-compatible LLM client for every provider, and the chain runner.

Two policies live here, and both exist because of something this project already
got wrong once:

**The SDK's own retries are off.** ``max_retries=0``, always (DEC-019). The
OpenAI SDK defaults to 2 and retries anything >= 500, so a ladder that reported
"attempt 3/3" had really made nine requests, and three deterministic 504s at
~300s each cost 45 minutes instead of 15 — invisibly, because the SDK's retries
print nothing. Any client built in this project must set this explicitly.

**Structured output is negotiated, not assumed.** The first real analysis call
this project ever made died on ``400 unknown field 'guided_json'`` (DEC-013).
Providers disagree about how to ask for JSON, so each (provider, model) pair is
walked down a ladder — ``json_schema`` -> ``json_object`` -> prompt-only — once
per process, and the level that worked is remembered. Being wrong costs one 400,
not a run.

``openai`` is imported lazily inside the client factory, so this module stays
importable in the pytest-only CI environment (DEC-012).
"""

from __future__ import annotations

import time

from . import errors, jsonx, pacing
from .registry import describe, env_key_for, provider_for

# Negotiation ladder, best first.
JSON_SCHEMA = "json_schema"
JSON_OBJECT = "json_object"
PROMPT_ONLY = "prompt"
LEVELS = (JSON_SCHEMA, JSON_OBJECT, PROMPT_ONLY)

MAX_ATTEMPTS = 3
# Short on purpose. A failure on this path is overwhelmingly a malformed sample
# or a transient gateway hiccup, and the only remedy for a bad sample is another
# sample. Quota exhaustion is handled by Retry-After, not by this ladder.
BACKOFF_SECONDS = (4, 12)
# A provider that answers 429 is telling us when to come back, so obeying it is
# not a retry — it is a wait. Bounded so a misconfigured key cannot stall a job.
MAX_RATE_LIMIT_WAITS = 2
MAX_RATE_LIMIT_SLEEP = 90.0

# Remembered per (provider, model) for the life of the process.
_NEGOTIATED = {}


def reset_negotiation():
    """Forget every negotiated level. For tests."""
    _NEGOTIATED.clear()


def negotiated_level(link):
    """The structured-output level last known to work for *link*, or None."""
    return _NEGOTIATED.get((link.provider, link.model))


def _initial_level(link, provider, schema):
    """Where to start the ladder for *link*."""
    if schema is None:
        return PROMPT_ONLY
    remembered = _NEGOTIATED.get((link.provider, link.model))
    if remembered is not None:
        return remembered
    supported = provider.structured or ()
    for level in LEVELS:
        if level in supported:
            return level
    return PROMPT_ONLY


def _next_level(level):
    """The next rung down, or None at the bottom."""
    idx = LEVELS.index(level)
    return LEVELS[idx + 1] if idx + 1 < len(LEVELS) else None


def _extra_body(link):
    """Provider/model-specific body additions.

    DeepSeek on NIM emits a long reasoning preamble unless thinking is switched
    off, and on a provider measured at ~12-13 tokens/s that preamble is the
    difference between answering and hitting the gateway's ~300s cut-off.
    """
    if link.provider == "nvidia" and "deepseek" in link.model.lower():
        return {"chat_template_kwargs": {"thinking": False}}
    return None


def build_client(link, *, api_key, timeout):
    """Construct the OpenAI SDK client for *link*. Substituted by tests."""
    from openai import OpenAI

    provider = provider_for(link)
    return OpenAI(
        base_url=provider.base_url,
        api_key=api_key,
        # See the module docstring. Never remove without reading DEC-019.
        max_retries=0,
        timeout=timeout,
    )


class LlmClient:
    """A single (provider, model) endpoint that returns parsed JSON."""

    def __init__(
        self,
        link,
        *,
        api_key,
        timeout=None,
        client_factory=None,
        limiter=None,
        on_log=print,
    ):
        self.link = link
        self.provider = provider_for(link)
        self.api_key = api_key
        self.timeout = timeout or self.provider.default_timeout
        self._factory = client_factory or build_client
        self._client = None
        self.limiter = limiter if limiter is not None else pacing.limiter_for(self.provider)
        self._log = on_log
        self.last_usage = None

    @property
    def client(self):
        if self._client is None:
            self._client = self._factory(
                self.link, api_key=self.api_key, timeout=self.timeout
            )
        return self._client

    # ------------------------------------------------------------------ request

    def _body(self, *, system, user, schema, schema_name, max_tokens, temperature, level):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        body = {
            "model": self.link.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if level == JSON_SCHEMA and schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                },
            }
        elif level == JSON_OBJECT:
            body["response_format"] = {"type": "json_object"}

        extra = _extra_body(self.link)
        if extra:
            body["extra_body"] = extra
        return body

    def complete_json(
        self,
        *,
        system,
        user,
        schema=None,
        schema_name="result",
        max_tokens=1024,
        temperature=0.2,
    ):
        """One request, returning parsed JSON. Raises on failure.

        Walks the negotiation ladder internally: a provider that rejects the
        schema is immediately re-asked without it, which is a different thing
        from a retry and so does not consume one.
        """
        level = _initial_level(self.link, self.provider, schema)

        while True:
            body = self._body(
                system=system,
                user=user,
                schema=schema,
                schema_name=schema_name,
                max_tokens=max_tokens,
                temperature=temperature,
                level=level,
            )

            est = pacing.estimate_tokens(system, user) + max_tokens
            self.limiter.acquire(est)

            try:
                response = self.client.chat.completions.create(**body)
            except Exception as exc:
                if errors.rejects_structured_output(exc):
                    lower = _next_level(level)
                    if lower is None:
                        raise
                    self._log(
                        f"   ↩ {describe(self.link)} rejected {level}; "
                        f"retrying with {lower}."
                    )
                    level = lower
                    continue
                raise

            usage = getattr(response, "usage", None)
            total = getattr(usage, "total_tokens", None)
            self.limiter.record(total)
            self.last_usage = usage

            content = self._content_of(response)
            value = jsonx.extract_json(content)

            # Only remember a level that produced parseable JSON. A provider
            # that accepts json_schema and then ignores it is worse than one
            # that refuses it, and this is where that is caught.
            _NEGOTIATED[(self.link.provider, self.link.model)] = level
            return value

    @staticmethod
    def _content_of(response):
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise ValueError("Provider returned no choices.")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if content is None:
            raise ValueError("Provider returned a choice with no content.")
        return content


def run_chain(
    chain,
    *,
    system,
    user,
    schema=None,
    schema_name="result",
    max_tokens=1024,
    temperature=0.2,
    keys=None,
    on_log=print,
    client_factory=None,
    sleep_fn=time.sleep,
    deadline=None,
    time_fn=time.monotonic,
):
    """Try each link in order; return ``(value, link)`` from the first that works.

    A link is abandoned only when it is exhausted or fails fatally. Every hop is
    printed, and a provider absent from *chain* is never contacted — which is
    what makes this an explicit chain rather than the silent cross-provider
    fallback DEC-003 forbade.

    *keys* maps provider name -> API key; a link with no key is skipped with a
    printed line rather than raising, so a partially-configured chain degrades
    to the providers that are actually set up.
    """
    keys = keys or {}
    failures = []

    for link in chain:
        label = describe(link)
        key = keys.get(link.provider) or ""
        if not key:
            reason = f"no API key ({env_key_for(link)} is not set)"
            on_log(f"   ⏭ Skipping {label}: {reason}.")
            failures.append((label, reason))
            continue

        if deadline is not None and time_fn() >= deadline:
            reason = "time budget exhausted before this provider was tried"
            on_log(f"   ⏭ Skipping {label}: {reason}.")
            failures.append((label, reason))
            continue

        try:
            value = _run_link(
                link,
                system=system,
                user=user,
                schema=schema,
                schema_name=schema_name,
                max_tokens=max_tokens,
                temperature=temperature,
                api_key=key,
                on_log=on_log,
                client_factory=client_factory,
                sleep_fn=sleep_fn,
                deadline=deadline,
                time_fn=time_fn,
            )
        except Exception as exc:  # noqa: BLE001 - recorded, then the next link
            reason = f"{type(exc).__name__}: {exc}"
            failures.append((label, reason))
            on_log(f"   ⚠️ {label} failed | {reason}")
            continue

        return value, link

    detail = "\n".join(f"  {label}: {reason}" for label, reason in failures)
    raise errors.ProviderError(
        f"Every provider in the chain failed ({len(failures)} tried):\n{detail}",
        failures=failures,
    )


def _run_link(
    link,
    *,
    system,
    user,
    schema,
    schema_name,
    max_tokens,
    temperature,
    api_key,
    on_log,
    client_factory,
    sleep_fn,
    deadline,
    time_fn,
):
    """Run one link's retry ladder. Raises if it is exhausted or fails fatally."""
    client = LlmClient(
        link,
        api_key=api_key,
        client_factory=client_factory,
        on_log=on_log,
    )
    label = describe(link)
    rate_limit_waits = 0
    attempt = 0
    last_exc = None

    while attempt < MAX_ATTEMPTS:
        attempt += 1
        # The wording matters: web/api/signals.py matches `attempt N/M` to drive
        # the dashboard's retry counter (DEC-014, the stdout-progress entry).
        on_log(f"   🔁 {label} attempt {attempt}/{MAX_ATTEMPTS}...")

        try:
            return client.complete_json(
                system=system,
                user=user,
                schema=schema,
                schema_name=schema_name,
                max_tokens=max_tokens,
                # Cool off each retry: the first sample failed, so a less
                # adventurous one is more likely to parse.
                temperature=max(0.0, temperature - 0.1 * (attempt - 1)),
            )
        except Exception as exc:  # noqa: BLE001 - classified immediately below
            last_exc = exc
            kind = errors.classify(exc)

            if kind == errors.RATE_LIMITED:
                if rate_limit_waits >= MAX_RATE_LIMIT_WAITS:
                    raise
                rate_limit_waits += 1
                wait = errors.retry_after_seconds(exc)
                wait = min(MAX_RATE_LIMIT_SLEEP, wait if wait is not None else 20.0)
                on_log(
                    f"   ⏳ {label} is rate limited; waiting {wait:.0f}s "
                    f"as asked ({rate_limit_waits}/{MAX_RATE_LIMIT_WAITS})."
                )
                sleep_fn(wait)
                # A wait is not a retry: the request was never really made.
                attempt -= 1
                continue

            if kind == errors.FATAL:
                on_log(f"   ✖ {label}: {type(exc).__name__}: {exc}")
                raise

            on_log(f"   ⚠️ {label} attempt {attempt} failed | {type(exc).__name__}: {exc}")

            if attempt >= MAX_ATTEMPTS:
                raise

            backoff = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
            if deadline is not None and time_fn() + backoff >= deadline:
                on_log(f"   ⏱ {label}: no time left in the budget for another attempt.")
                raise
            sleep_fn(backoff)

    if last_exc is not None:
        raise last_exc
    raise errors.ProviderError(f"{label} produced no result and no error.")
