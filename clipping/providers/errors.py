"""Provider error classification — what a failure means, without importing an SDK.

Every predicate here works off the exception's class *name*, its status code and
its message, never off ``isinstance`` against an SDK type. That is deliberate and
load-bearing: this module must stay importable with nothing installed but the
standard library, because the CI suite installs pytest and nothing else
(DEC-012). It also fails closed — an exception shape nobody anticipated is
treated as fatal for the current provider rather than retried forever.

The classification is generalized from the NVIDIA-only version in
``clipping/engine.py`` (``_nvidia_is_retryable``, ``_nvidia_rejects_response_format``),
which stays where it is until Stage 11 retires that path.
"""

from __future__ import annotations

import json
import re

# Longest unit first so "ms" is never read as "m".
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

# Retrying these is worth another sample: the request was fine, the provider or
# the model misbehaved transiently.
RETRYABLE_EXC_NAMES = frozenset({
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "InternalServerError",
    "APIError",
    "APIStatusError",
})

# Retrying these cannot help. A bad key stays bad.
FATAL_EXC_NAMES = frozenset({
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "BadRequestError",
    "UnprocessableEntityError",
})

RETRY = "retry"
FATAL = "fatal"
RATE_LIMITED = "rate_limited"
STRUCTURED_UNSUPPORTED = "structured_unsupported"


def status_code(exc: Exception):
    """Best-effort HTTP status for *exc*, or ``None``.

    SDKs disagree on the attribute name, so all three are tried in the order the
    OpenAI SDK, httpx wrappers and hand-rolled clients use.
    """
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def retry_after_seconds(exc: Exception):
    """Seconds the provider asked us to wait, or ``None``.

    Read from the ``Retry-After`` header, falling back to a ``retry_after`` key
    in a parsed JSON body. A provider that tells us when to come back is worth
    obeying exactly: guessing shorter earns another 429 and guessing longer
    wastes the daily budget's wall-clock.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        for key in ("retry-after", "Retry-After", "x-ratelimit-reset-requests"):
            try:
                raw = headers.get(key)
            except AttributeError:
                raw = None
            if raw:
                parsed = _parse_duration(raw)
                if parsed is not None:
                    return parsed

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        for key in ("retry_after", "retryAfter"):
            parsed = _parse_duration(body.get(key))
            if parsed is not None:
                return parsed
    return None


def _parse_duration(raw):
    """``"12"`` / ``"1.5s"`` / ``"2m30s"`` -> seconds. Returns None if unusable."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw >= 0 else None

    text = str(raw).strip().lower()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        pass
    else:
        return value if value >= 0 else None

    # Groq answers "2m30.5s", "7.66s", "500ms" and friends.
    #
    # The units must be matched longest-first: a naive character scan reads the
    # 'm' of "500ms" as minutes and turns half a second into eight hours.
    total = 0.0
    seen = False
    for number, unit in _DURATION_PART.findall(text):
        total += float(number) * _UNIT_SECONDS[unit]
        seen = True
    return total if seen else None


def rejects_structured_output(exc: Exception) -> bool:
    """Whether *exc* is the provider refusing ``response_format``.

    Distinct from a plain 400: the request is well-formed, the model simply does
    not implement structured output. Re-sending the same body cannot help, but
    re-sending it *without* the parameter can — which is the whole point of the
    negotiation ladder in ``llm.py`` (DEC-013, generalized).

    Matched on the message because SDKs surface this as a generic
    ``BadRequestError``. The message must name the parameter AND carry a
    rejection word, so an ordinary 400 about, say, a bad temperature is not
    mistaken for one of these and silently stripped of its schema.
    """
    status = status_code(exc)
    if isinstance(status, int) and status not in (400, 422):
        return False

    message = str(exc).lower()
    if not any(
        name in message
        for name in ("response_format", "json_schema", "guided_json", "response_schema")
    ):
        return False

    return any(
        marker in message
        for marker in (
            "unknown field",
            "unsupported",
            "not supported",
            "unrecognized",
            "invalid",
            "does not support",
            "is not permitted",
        )
    )


# Phrases providers use when the MODEL is the problem, not the request or the
# key. Observed: Google 404 "is no longer available to new users" (2026-09-24);
# Groq 400 "has been decommissioned"; Mistral 400 "Invalid model"; OpenRouter
# 400 "is not a valid model ID"; OpenAI-style 404 "model_not_found" / "does not
# exist".
_MODEL_GONE_MARKERS = (
    "no longer available",
    "not found",
    "does not exist",
    "decommissioned",
    "end of life",
    "model_not_found",
    "invalid model",
    "not a valid model",
    "no such model",
    "unknown model",
)

# A 404 that names one of these is about the REQUEST: OpenRouter answers 404
# "No endpoints found that can handle the requested parameters" and "...matching
# your data policy". Another model would not fix either.
_REQUEST_NOT_MODEL = ("parameter", "data policy")


def is_model_unavailable(exc: Exception) -> bool:
    """Whether *exc* says this MODEL cannot be used on this key, and nothing else.

    The one failure where another model on the same provider and the same key
    is the right next step (DEC-089). Everything else keeps its meaning: a bad
    key, a rate limit, a refused schema or a malformed request is not fixed by
    changing the model, and swapping on those would hide the real error.

    Matched on status, class name and message, like everything in this module.
    ``classify`` still calls these FATAL; this is a narrower question asked
    after it, never instead of it.
    """
    if rejects_structured_output(exc):
        return False
    status = status_code(exc)
    name = type(exc).__name__
    if status in (401, 403, 429) or name in (
        "AuthenticationError", "PermissionDeniedError", "RateLimitError",
    ):
        return False

    message = str(exc).lower()
    if any(marker in message for marker in _REQUEST_NOT_MODEL):
        return False

    if status in (404, 410) or name == "NotFoundError":
        return True
    if status in (400, 422) or name in ("BadRequestError", "UnprocessableEntityError"):
        return "model" in message and any(m in message for m in _MODEL_GONE_MARKERS)
    return False


def classify(exc: Exception) -> str:
    """Return one of RETRY / FATAL / RATE_LIMITED / STRUCTURED_UNSUPPORTED.

    ``FATAL`` means *for this provider*: the caller abandons the link and moves
    to the next one in the chain, rather than giving up on the run.
    """
    if rejects_structured_output(exc):
        return STRUCTURED_UNSUPPORTED

    name = type(exc).__name__
    status = status_code(exc)

    if status == 429 or name == "RateLimitError":
        return RATE_LIMITED

    if name in FATAL_EXC_NAMES:
        return FATAL

    if isinstance(status, int):
        if status in (408, 409) or status >= 500:
            return RETRY
        if 400 <= status < 500:
            return FATAL

    if isinstance(exc, json.JSONDecodeError):
        return RETRY
    if name in RETRYABLE_EXC_NAMES:
        return RETRY
    # Our own shape validation raises ValueError; another sample may well fix it.
    if isinstance(exc, ValueError):
        return RETRY

    # Unknown shape: fail closed onto the next provider instead of looping.
    return FATAL


class ProviderError(RuntimeError):
    """Every link in the chain failed. Carries the per-link reasons."""

    def __init__(self, message: str, failures=None):
        super().__init__(message)
        self.failures = list(failures or [])
