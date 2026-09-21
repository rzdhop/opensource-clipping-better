"""Tolerant JSON extraction from a model reply.

Structured output is negotiated, not guaranteed, so every one of these shapes
arrives in practice. Stdlib only.
"""

import pytest

from clipping.providers import jsonx


# ----------------------------------------------------------------- the easy path

def test_plain_array():
    assert jsonx.extract_json('[1, 2, 3]') == [1, 2, 3]


def test_plain_object():
    assert jsonx.extract_json('{"a": 1}') == {"a": 1}


# --------------------------------------------------------------- decorations

def test_fenced_json():
    assert jsonx.extract_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]


def test_fenced_without_a_language_tag():
    assert jsonx.extract_json('```\n[1]\n```') == [1]


def test_commentary_before_and_after():
    reply = 'Sure! Here are the clips:\n```json\n[{"a": 1}]\n```\nLet me know!'
    assert jsonx.extract_json(reply) == [{"a": 1}]


def test_prose_with_no_fence():
    assert jsonx.extract_json('The answer is [1, 2] as requested.') == [1, 2]


def test_trailing_comma_is_rescued():
    assert jsonx.extract_json('[1, 2, ]') == [1, 2]
    assert jsonx.extract_json('{"a": 1, }') == {"a": 1}


def test_trailing_comma_inside_a_string_is_not_touched():
    assert jsonx.extract_json('{"a": "x, ]"}') == {"a": "x, ]"}


# ---------------------------------------------------- the bracket-in-a-string case

def test_a_bracket_inside_a_string_does_not_end_the_scan():
    """A title like 'Why [this] matters' is routine, and a rfind(']') scanner
    would truncate the document at the wrong place."""
    reply = 'Result:\n[{"title": "Why [this] matters", "n": 2}]'
    assert jsonx.extract_json(reply) == [{"title": "Why [this] matters", "n": 2}]


def test_an_escaped_quote_does_not_end_a_string():
    reply = r'[{"title": "she said \"hi\" [loudly]"}]'
    assert jsonx.extract_json(reply)[0]["title"] == 'she said "hi" [loudly]'


def test_nested_structures_survive():
    value = jsonx.extract_json('prefix {"a": {"b": [1, {"c": 2}]}} suffix')
    assert value == {"a": {"b": [1, {"c": 2}]}}


# ------------------------------------------------------------------ unwrapping

@pytest.mark.parametrize("key", ["clips", "candidates", "ranked", "data", "items",
                                 "results", "highlights", "output", "response"])
def test_wrapper_keys_are_unwrapped(key):
    assert jsonx.extract_list('{"%s": [{"a": 1}]}' % key) == [{"a": 1}]


def test_a_lone_object_becomes_a_list_of_one():
    """A model answering with one item instead of a list of one is being
    helpful, not wrong."""
    assert jsonx.extract_list('{"a": 1}') == [{"a": 1}]


def test_an_already_list_is_returned_unchanged():
    assert jsonx.extract_list('[{"a": 1}]') == [{"a": 1}]


def test_an_empty_array_is_returned_not_rejected():
    """Emptiness is a decision for the caller: Pass A legitimately finds no
    candidate in a window of silence, while an empty clip list is fatal."""
    assert jsonx.extract_list('[]') == []


def test_unwrap_prefers_the_named_key_over_wrapping_the_object():
    assert jsonx.unwrap_list({"clips": [1], "other": 2}) == [1]


# ------------------------------------------------------------------- failures

@pytest.mark.parametrize("reply", ["", "   ", "no json here", "I cannot help with that."])
def test_unparseable_replies_raise_value_error(reply):
    """ValueError is classified retryable, so the caller resamples."""
    with pytest.raises(ValueError):
        jsonx.extract_json(reply)


def test_truncated_json_raises():
    with pytest.raises(ValueError):
        jsonx.extract_json('[{"a": 1}, {"b":')


def test_a_scalar_cannot_be_unwrapped_to_a_list():
    with pytest.raises(ValueError):
        jsonx.unwrap_list(42)
