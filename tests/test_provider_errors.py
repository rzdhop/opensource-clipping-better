"""Provider error classification.

Every predicate works off the exception's class name, status code and message,
never off an SDK type — the module has to import with nothing but the standard
library installed (DEC-012), and an unanticipated shape must fail closed.
"""

from types import SimpleNamespace

import json
import pytest

from clipping.providers import errors


def exc(name, status=None, message=None, headers=None, body=None):
    cls = type(name, (Exception,), {})
    err = cls(message or f"simulated {name}")
    if status is not None:
        err.status_code = status
    if headers is not None:
        err.response = SimpleNamespace(status_code=status, headers=headers)
    if body is not None:
        err.body = body
    return err


# ------------------------------------------------------------- classification

@pytest.mark.parametrize(
    "name,status,expected",
    [
        ("InternalServerError", 500, errors.RETRY),
        ("InternalServerError", 502, errors.RETRY),
        ("InternalServerError", 503, errors.RETRY),
        ("InternalServerError", 504, errors.RETRY),
        ("APITimeoutError", None, errors.RETRY),
        ("APIConnectionError", None, errors.RETRY),
        ("SomeError", 408, errors.RETRY),
        ("SomeError", 409, errors.RETRY),
        ("RateLimitError", 429, errors.RATE_LIMITED),
        ("SomeError", 429, errors.RATE_LIMITED),
        ("AuthenticationError", 401, errors.FATAL),
        ("PermissionDeniedError", 403, errors.FATAL),
        ("NotFoundError", 404, errors.FATAL),
        ("SomeError", 410, errors.FATAL),
        ("BadRequestError", 400, errors.FATAL),
    ],
)
def test_classify(name, status, expected):
    assert errors.classify(exc(name, status)) == expected


def test_a_model_that_reached_end_of_life_is_fatal_not_retried():
    """The shipped NIM default once returned 410 Gone; retrying it three times
    only made the failure slower."""
    err = exc("NotFoundError", 410, "deepseek-v4-pro has reached its end of life")
    assert errors.classify(err) == errors.FATAL


def test_our_own_shape_validation_is_retryable():
    """A bad sample is worth another sample."""
    assert errors.classify(ValueError("empty clip array")) == errors.RETRY


def test_malformed_json_is_retryable():
    err = json.JSONDecodeError("Expecting value", "", 0)
    assert errors.classify(err) == errors.RETRY


def test_an_unknown_exception_fails_closed_onto_the_next_provider():
    """Looping forever on something nobody anticipated is the worse failure."""
    assert errors.classify(RuntimeError("who knows")) == errors.FATAL


# -------------------------------------------------- structured-output refusal

@pytest.mark.parametrize(
    "message",
    [
        "unknown field 'guided_json'",
        "response_format is not supported by this model",
        "Invalid value for response_format",
        "json_schema: unrecognized parameter",
        "This model does not support response_format",
    ],
)
def test_a_refusal_of_the_schema_is_its_own_category(message):
    err = exc("BadRequestError", 400, message)
    assert errors.classify(err) == errors.STRUCTURED_UNSUPPORTED


@pytest.mark.parametrize(
    "message",
    [
        "temperature must be <= 2",
        "max_tokens exceeds the model limit",
        "invalid api key",
        # Names the parameter but is not a refusal of it.
        "response_format produced a result that was filtered",
    ],
)
def test_an_ordinary_400_is_not_mistaken_for_a_refusal(message):
    """Otherwise the client would silently strip the schema and accept whatever
    prose the model felt like returning."""
    err = exc("BadRequestError", 400, message)
    assert errors.classify(err) == errors.FATAL


def test_a_500_naming_response_format_is_not_a_refusal():
    err = exc("InternalServerError", 500, "response_format handler crashed")
    assert errors.classify(err) == errors.RETRY


# ------------------------------------------------------------- retry-after

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12", 12.0),
        ("1.5", 1.5),
        ("2m30s", 150.0),
        ("2m59.56s", 179.56),   # the shape Groq actually sends
        ("7.66s", 7.66),
        ("1h", 3600.0),
        ("500ms", 0.5),
        ("0", 0.0),
    ],
)
def test_retry_after_parsing(raw, expected):
    err = exc("RateLimitError", 429, headers={"retry-after": raw})
    assert errors.retry_after_seconds(err) == pytest.approx(expected)


def test_milliseconds_are_not_read_as_minutes():
    """Units must be matched longest-first. A character scan reads the 'm' of
    "500ms" as minutes and turns half a second into an eight-hour wait."""
    err = exc("RateLimitError", 429, headers={"retry-after": "500ms"})
    assert errors.retry_after_seconds(err) == pytest.approx(0.5)


def test_retry_after_from_the_body_when_there_is_no_header():
    err = exc("RateLimitError", 429, body={"retry_after": 9})
    assert errors.retry_after_seconds(err) == pytest.approx(9.0)


def test_retry_after_absent_is_none():
    assert errors.retry_after_seconds(exc("RateLimitError", 429)) is None


def test_unparseable_retry_after_is_none():
    err = exc("RateLimitError", 429, headers={"retry-after": "soon"})
    assert errors.retry_after_seconds(err) is None


# ------------------------------------------------------------- status codes

def test_status_is_read_from_whichever_attribute_the_sdk_used():
    for attr in ("status_code", "status", "code"):
        err = Exception("x")
        setattr(err, attr, 503)
        assert errors.status_code(err) == 503


def test_status_is_read_from_a_nested_response():
    err = Exception("x")
    err.response = SimpleNamespace(status_code=418)
    assert errors.status_code(err) == 418


def test_a_non_integer_status_is_ignored():
    err = Exception("x")
    err.code = "insufficient_quota"
    assert errors.status_code(err) is None
