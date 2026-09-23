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

import threading
import time

from . import errors, jsonx, pacing
from .registry import (
    DEFAULT_PROBE_TIMEOUT,
    describe,
    effective_timeout,
    env_key_for,
    probe_timeout,
    provider_for,
    work_probe_timeout,
)

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
#
# Guarded, now that scan windows run concurrently. A dict write is atomic in
# CPython, so the race was never corruption -- it was two threads meeting the
# same unknown model at once, both walking the ladder from the top and both
# paying the 400 that walking it costs. The lock makes the second wait for the
# first's answer.
_NEGOTIATED = {}
_NEGOTIATED_LOCK = threading.Lock()


def reset_negotiation():
    """Forget every negotiated level. For tests."""
    with _NEGOTIATED_LOCK:
        _NEGOTIATED.clear()


def negotiated_level(link):
    """The structured-output level last known to work for *link*, or None."""
    with _NEGOTIATED_LOCK:
        return _NEGOTIATED.get((link.provider, link.model))


def _initial_level(link, provider, schema):
    """Where to start the ladder for *link*."""
    if schema is None:
        return PROMPT_ONLY
    with _NEGOTIATED_LOCK:
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


# NIM model families that emit a reasoning preamble unless it is switched off.
# Each was measured failing WITH thinking on, in a different way, which is why
# this is a list rather than a special case:
#
#   deepseek-*   returns ``content=null`` -- the reply is all reasoning, and
#                LlmClient._content_of raises on it
#   nemotron-3.5-lightning  returns prose instead of JSON, and on a 700-token
#                budget never reaches the JSON at all
#   glm-*        spends the whole token budget on the preamble and truncates the
#                JSON mid-object
#
# This predicate used to name deepseek and nothing else, and that was not a
# cosmetic gap: nemotron-3.5-lightning was rejected as "reasoning prose,
# unparseable" in one benchmark and, with thinking off, answers the same request
# in ~20s with usable candidates. A model was disqualified by a missing flag.
_NIM_REASONING_FAMILIES = ("deepseek", "nemotron-3.5-lightning", "glm-")


def _extra_body(link):
    """Provider/model-specific body additions.

    Several NIM models emit a long reasoning preamble unless thinking is
    switched off, and on a provider measured at ~12-13 tokens/s that preamble is
    the difference between answering and hitting the gateway's ~300s cut-off --
    or, worse, between usable JSON and none.
    """
    if link.provider == "nvidia":
        model = link.model.lower()
        if any(family in model for family in _NIM_REASONING_FAMILIES):
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
        # Via the registry helper, never off self.provider directly: the
        # budget check in run_chain/_run_link reads the same function, and
        # the two disagreeing is precisely the bug this replaced.
        self.timeout = effective_timeout(link, timeout)
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
            with _NEGOTIATED_LOCK:
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

        # DEC-020's check, predictive rather than retrospective: not "is the
        # budget already spent" but "could one request to THIS link outlast it".
        # Per link, not a blanket abort, because a 330s NVIDIA link can be out of
        # room while a 120s Groq link still fits comfortably.
        if deadline is not None:
            need = effective_timeout(link)
            left = deadline - time_fn()
            if need > left:
                reason = (
                    f"a {need:.0f}s request does not fit the {max(0.0, left):.0f}s "
                    f"left in the time budget"
                )
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
        # Before the attempt is announced, not after: web/api/signals.py turns
        # every `attempt N/M` line into a retry the dashboard shows, so counting
        # an attempt that was never made would report a retry that never
        # happened. This is DEC-020's predictive check at the rung that actually
        # spends the time -- the old guard below only ever weighed a 4-12s
        # backoff against the deadline while the request itself ran for up to
        # 330s, which is how a ladder overran a 900s budget by 25 seconds.
        if deadline is not None and time_fn() + client.timeout > deadline:
            left = max(0.0, deadline - time_fn())
            note = (
                f"{label}: stopping after {attempt}/{MAX_ATTEMPTS} attempt(s); "
                f"another {client.timeout:.0f}s request would outlast the "
                f"{left:.0f}s left in the time budget"
            )
            on_log(f"   ⏱ {note}.")
            if last_exc is not None:
                raise last_exc
            raise errors.ProviderError(note)

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
            # Weigh the sleep AND the request it leads to, so a 12s backoff is
            # never slept only for the loop-top check to refuse afterwards. With
            # that check in place this is an optimisation, not a second policy.
            if (
                deadline is not None
                and time_fn() + backoff + client.timeout > deadline
            ):
                left = max(0.0, deadline - time_fn())
                on_log(
                    f"   ⏱ {label}: {left:.0f}s left in the time budget, not "
                    f"enough for a {backoff}s backoff and another "
                    f"{client.timeout:.0f}s request."
                )
                raise
            sleep_fn(backoff)

    if last_exc is not None:
        raise last_exc
    raise errors.ProviderError(f"{label} produced no result and no error.")


# ---------------------------------------------------------------- preflight

# How long a probe may wait is now a per-provider fact in the registry
# (``probe_timeout`` / ``work_probe_timeout``), not one number here. The history
# is why, and it is the context DEC-072 needs:
#
# DEC-056 chose 45s against measurement: five probes of the then-default NIM
# model ran 1.3 / 1.6 / 2.2 / 2.7 / 11.4s, the dead model it was written for did
# not answer in 120s, and 45s sat roughly four times above the slowest healthy
# probe. The cost of being wrong is asymmetric -- too short fails a job whose
# provider was merely slow; too long still catches a dead one far faster than
# the 47 minutes of CPU Whisper this replaced.
#
# Two days later that measurement was stale. With a WORKING key the NIM free
# tier answered the same 2-token ping with "ok" in 48.9 / 57.0 / 49.7s, nearly
# all of it time to first byte: a queue, not a slow model. 45s declared a live
# provider dead and failed the job. The fast hosted tiers keep 45s; the NIM
# floor gets 120s; and one number for every provider was the actual mistake.
#
# This name survives as the fallback for a provider that declares nothing.
PROBE_TIMEOUT_SECONDS = DEFAULT_PROBE_TIMEOUT
PROBE_MAX_TOKENS = 8
PROBE_PROMPT = "Reply with the single word: ok"


def probe_chain(chain, keys, *, timeout=None, on_log=print,
                client_factory=None, time_fn=time.monotonic, work=None):
    """Ask the chain's keyed links, in order, whether they answer.

    Returns ``(live_link, results, value)``; *live_link* is ``None`` when
    nothing answered, and *value* is the parsed reply to a *work* probe when one
    succeeded. Stops at the first link that replies, so the cost of a healthy
    run is one request.

    *timeout* of None gives each link its own ping allowance from the registry
    (``probe_timeout``); a number overrides it for every link.

    **The cheap ping proves liveness, not suitability**, and that gap is real: a
    model measured here answered it in 0.67s and still failed the real pass-A
    request, spending its whole token budget on a reasoning preamble. Another
    answered in seven tokens with ``{"candidates": []}`` on every transcript it
    was ever shown.

    So when the caller can supply *work* — a real request, which it can only do
    once a transcript exists — the probe asks that instead, and the answer is
    handed back rather than discarded. What is then proven is that this link
    does the actual job.

    **Three things stay true whichever probe runs**, and each is load-bearing:

    * A work probe that fails falls back to the ping before the link is called
      dead. A provider too slow for its work budget but alive on a ping is alive,
      and DEC-020 forbids cutting off a slow-but-healthy call.
    * An empty but well-formed answer is LIVE. Zero candidates in one window is
      an opinion about 45 beats, not evidence about the endpoint.
    * A failing link is reported, never removed (DEC-003, DEC-023). The chain is
      a list the user wrote down.
    """
    keys = keys or {}
    results = []

    for link in chain:
        label = describe(link)
        if not keys.get(link.provider):
            results.append((label, "no API key", None, "skipped"))
            continue

        if work is not None:
            value, elapsed, reason = _work_probe(
                link, keys[link.provider], work,
                on_log=on_log, client_factory=client_factory, time_fn=time_fn,
            )
            if reason is None:
                results.append((label, "ok", elapsed, "work"))
                on_log(
                    f"   ✅ {label} answered a real analysis request in "
                    f"{elapsed:.1f}s."
                )
                return link, results, value
            on_log(
                f"   … {label} did not complete a real request in "
                f"{elapsed:.0f}s; trying a plain liveness ping | {reason}"
            )

        elapsed, reason = _ping_probe(
            link, keys[link.provider], probe_timeout(link, timeout),
            on_log=on_log, client_factory=client_factory, time_fn=time_fn,
        )
        if reason is not None:
            results.append((label, reason, elapsed, "ping"))
            on_log(f"   ✖ {label} did not answer after {elapsed:.0f}s | {reason}")
            continue

        results.append((label, "ok", elapsed, "ping"))
        on_log(f"   ✅ {label} answered in {elapsed:.1f}s.")
        return link, results, None

    return None, results, None


def _ping_probe(link, api_key, timeout, *, on_log, client_factory, time_fn):
    """``(elapsed, reason)``; *reason* is ``None`` when the link answered.

    A plain completion, with no schema and no negotiation: the question is
    whether anything comes back, and involving the structured-output ladder
    would let a provider's json_schema support decide a liveness question.
    """
    client = LlmClient(
        link, api_key=api_key, timeout=timeout,
        client_factory=client_factory, on_log=on_log,
    )
    started = time_fn()
    try:
        client.client.chat.completions.create(
            model=link.model,
            messages=[{"role": "user", "content": PROBE_PROMPT}],
            max_tokens=PROBE_MAX_TOKENS,
            temperature=0,
        )
    except Exception as exc:  # noqa: BLE001 - every failure is just "not this one"
        return time_fn() - started, f"{type(exc).__name__}: {exc}"
    return time_fn() - started, None


def _work_probe(link, api_key, work, *, on_log, client_factory, time_fn):
    """``(value, elapsed, reason)`` for one real request against *link*.

    Unlike the ping this goes through ``complete_json``, so the negotiation
    ladder applies — which is the point. A provider whose ``json_schema`` is
    refused drops to ``json_object`` and then to prompt-only, and only a link
    that answers at no rung at all has failed.
    """
    client = LlmClient(
        link, api_key=api_key,
        # Twice the ping's allowance, never more than a real request may take
        # (registry.work_probe_timeout). It runs before transcription, so it
        # must not become the delay it exists to prevent.
        timeout=work_probe_timeout(link),
        client_factory=client_factory, on_log=on_log,
    )
    started = time_fn()
    try:
        value = client.complete_json(
            system=work["system"],
            user=work["user"],
            schema=work.get("schema"),
            schema_name=work.get("schema_name", "result"),
            max_tokens=work.get("max_tokens", 700),
            temperature=work.get("temperature", 0.2),
        )
    except Exception as exc:  # noqa: BLE001 - falls back to the ping
        return None, time_fn() - started, f"{type(exc).__name__}: {exc}"
    return value, time_fn() - started, None


def preflight_message(results):
    """The one-line-per-link explanation for a chain where nothing answered."""
    lines = []
    for label, reason, elapsed, kind in results:
        when = f" after {elapsed:.0f}s" if elapsed is not None else ""
        how = " (real analysis request)" if kind == "work" else ""
        lines.append(f"  {label}: {reason}{when}{how}")
    return (
        "No provider in the chain answered a liveness check, so the analysis "
        "cannot run. Nothing was transcribed, because that would have been "
        "wasted.\n" + "\n".join(lines)
    )
