"""Tolerant JSON extraction from a model's reply.

Structured output is negotiated, not guaranteed (see ``llm.py``), so a reply can
arrive as a bare array, fenced in ``​```json``, wrapped in a one-key object, or
prefixed with a sentence of commentary. This module turns all of those into the
value the caller asked for, and raises a retryable ``ValueError`` when it cannot.

The bracket scanner is the part that matters. Splitting on fences — which is
what ``engine._extract_clip_list`` does — breaks on a reply whose JSON *contains*
a fenced code sample, and a ``find("[")``/``rfind("]")`` pair breaks on any
trailing prose containing a bracket. Walking the structure is both simpler to
reason about and correct for the string-with-a-bracket-in-it case, which a
title like ``"Why [this] matters"`` produces routinely.

Stdlib only.
"""

from __future__ import annotations

import json

# Keys a non-conforming model wraps its array in. Ordered by how often each has
# actually been observed on this project's providers.
WRAPPER_KEYS = ("clips", "candidates", "ranked", "data", "items", "results",
                "highlights", "output", "response")


def extract_json(content: str):
    """Parse *content* into a Python object, tolerating the usual decorations.

    Raises ``ValueError`` (retryable, by ``errors.classify``) on anything
    unparseable, so the caller can simply take another sample.
    """
    if not content or not content.strip():
        raise ValueError("Provider returned empty content.")

    text = content.strip()

    # The cheap path: a well-behaved model with json_schema honoured.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    candidate = _first_json_value(text)
    if candidate is None:
        preview = text[:160].replace("\n", " ")
        raise ValueError(f"No JSON value found in provider reply: {preview!r}")

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # One rescue pass for the single malformation models actually emit.
        try:
            return json.loads(_strip_trailing_commas(candidate))
        except json.JSONDecodeError as exc:
            preview = candidate[:160].replace("\n", " ")
            raise ValueError(
                f"Provider reply is not valid JSON ({exc.msg}): {preview!r}"
            ) from exc


def unwrap_list(value, *, keys=WRAPPER_KEYS) -> list:
    """Return the list inside *value*, unwrapping a one-key object if needed.

    A lone object becomes a single-element list — a model answering with one
    item instead of a list of one is being helpful, not wrong.
    """
    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        for key in keys:
            inner = value.get(key)
            if isinstance(inner, list):
                return inner
        # A single unwrapped object: treat it as a list of one.
        return [value]

    raise ValueError(f"Expected a JSON array or object, got {type(value).__name__}.")


def extract_list(content: str, *, keys=WRAPPER_KEYS) -> list:
    """``extract_json`` followed by ``unwrap_list``."""
    return unwrap_list(extract_json(content), keys=keys)


def _first_json_value(text: str):
    """Return the first balanced ``[...]`` or ``{...}`` substring, or None.

    String-aware: a bracket inside a JSON string (``"Why [this] matters"``) does
    not affect the depth, and a backslash escape cannot end a string early.
    """
    start = None
    opener = None
    for idx, char in enumerate(text):
        if char in "[{":
            start = idx
            opener = char
            break
    if start is None:
        return None

    closer = "]" if opener == "[" else "}"
    depth = 0
    in_string = False
    escaped = False

    for idx in range(start, len(text)):
        char = text[idx]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]

    return None


def _strip_trailing_commas(text: str) -> str:
    """Remove ``,`` immediately before a ``]`` or ``}``, outside strings."""
    out = []
    in_string = False
    escaped = False

    for idx, char in enumerate(text):
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            out.append(char)
            continue

        if char == ",":
            rest = text[idx + 1 :]
            stripped = rest.lstrip()
            if stripped[:1] in ("]", "}"):
                continue  # drop this comma
        out.append(char)

    return "".join(out)
