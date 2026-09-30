"""How long a clip really takes to make (AI Story phase 6, stage 7; DEC-211).

``data/gen_timings.json`` (``gen_timings_v1``) keeps, per key
``<link>|<template>|<profile>`` (:func:`timing_key`; the template and the
hardware profile are empty for a hosted link), the last :data:`KEEP`
measurements ``{wall_s, clip_s, at}``: the wall time one clip took and its
length in seconds. The video phase records one per clip it makes
(:func:`record`); the estimate turns them into an ETA (:func:`eta_s`): the
median of ``wall_s / clip_s`` times the seconds to make -- or None, "no
measured history", when the key has none. A guess is never shown as a
measurement.

It is **not** reset daily: ``data/usage.json`` (``limits.DailyUsage``) is,
which is why the history lives in a file of its own. The file handling is
``DailyUsage``'s -- an atomic write through a temporary file in the same
folder, a lock per store, a file that cannot be read starting afresh. The
path is ``GEN_TIMINGS_PATH``, else ``data/gen_timings.json`` in the repo (as
``USAGE_PATH`` and ``SPEND_PATH`` resolve theirs; tests point it at a
temporary folder).

Stdlib only.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
import threading
import time
from datetime import datetime, timezone

SCHEMA = "gen_timings_v1"
# The measurements kept per key: the latest ones, the oldest dropped first.
KEEP = 20

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def default_timings_path() -> str:
    return os.environ.get("GEN_TIMINGS_PATH") or os.path.join(_ROOT, "data", "gen_timings.json")


def timing_key(link, template=None, profile=None) -> str:
    """``<link>|<template>|<profile>``: a local clip's time depends on the
    workflow and the card it runs on, a hosted one's on the link alone."""
    return f"{link}|{template or ''}|{profile or ''}"


def _positive(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and value > 0)


class GenTimings:
    """The measured clip times, on disk (``gen_timings_v1``)."""

    def __init__(self, path, *, time_fn=None):
        self.path = path
        self._time = time_fn or time.time
        self._lock = threading.Lock()

    def _empty(self) -> dict:
        return {"$schema": SCHEMA, "keys": {}}

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return self._empty()
        if not isinstance(data, dict) or data.get("$schema") != SCHEMA or not isinstance(data.get("keys"), dict):
            return self._empty()
        return data

    def _write(self, data: dict) -> None:
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=directory, prefix=".gen-timings-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def samples(self, key) -> list:
        """The measurements kept for *key*, oldest first (only well-formed ones)."""
        with self._lock:
            entries = self._load()["keys"].get(key)
        if not isinstance(entries, list):
            return []
        return [entry for entry in entries
                if isinstance(entry, dict) and _positive(entry.get("wall_s")) and _positive(entry.get("clip_s"))]

    def record(self, key, wall_s, clip_s) -> list:
        """Keep one clip's measurement under *key* (the last :data:`KEEP`);
        returns what is kept. ``ValueError`` for a time or a length that is
        not a positive number: a broken clock is never a measurement."""
        if not _positive(wall_s) or not _positive(clip_s):
            raise ValueError(f"a clip timing needs a positive wall_s and clip_s, not {wall_s!r} and {clip_s!r}")
        entry = {"wall_s": round(float(wall_s), 3), "clip_s": clip_s,
                 "at": datetime.fromtimestamp(self._time(), tz=timezone.utc).isoformat()}
        with self._lock:
            data = self._load()
            kept = [item for item in data["keys"].get(key) or [] if isinstance(item, dict)]
            kept = (kept + [entry])[-KEEP:]
            data["keys"][key] = kept
            data["updated_at"] = entry["at"]
            self._write(data)
            return list(kept)

    def eta_s(self, key, seconds):
        """The wall time *seconds* of clips should take under *key*: the
        median of the kept ``wall_s / clip_s``, times *seconds*; None with
        no measured history."""
        rates = [entry["wall_s"] / entry["clip_s"] for entry in self.samples(key)]
        if not rates:
            return None
        return round(statistics.median(rates) * float(seconds), 1)


_DEFAULT = None
_DEFAULT_LOCK = threading.Lock()


def default_timings() -> GenTimings:
    global _DEFAULT
    with _DEFAULT_LOCK:
        path = default_timings_path()
        if _DEFAULT is None or _DEFAULT.path != path:
            _DEFAULT = GenTimings(path)
        return _DEFAULT


def reset() -> None:
    """Drop the cached default store. For tests."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        _DEFAULT = None


def record(key, wall_s, clip_s, *, timings=None) -> list:
    """:meth:`GenTimings.record` on the default store (the video phase's call)."""
    return (timings or default_timings()).record(key, wall_s, clip_s)


def eta_s(key, seconds, *, timings=None):
    """:meth:`GenTimings.eta_s` on the default store."""
    return (timings or default_timings()).eta_s(key, seconds)


def samples(key, *, timings=None) -> list:
    return (timings or default_timings()).samples(key)
