"""Remember what each scan window answered, so a rerun does not re-pay for it.

Pass A is where the time goes: one request per ~45-beat window, and against the
last link in the shipped chain that is ~90s each. A rerun re-paid for all of it,
and reruns are the common case — Clone & Rerun, a changed render flag, a
different clip count, a second look at a video you already scanned.

Only pass A is cached. B and C are cheap, and both depend on what survives
snapping, which depends on settings the cache deliberately does not key on.

The key is a content hash of the window's own text plus a *salt* the caller
builds from everything else that could change the answer — the prompt version,
the chain, the preset, the per-window candidate cap. Nothing here interprets the
salt; it just has to differ when the question differs. A stale entry is
therefore not something that can be served: it is a different key.

Every read and write is exception-tolerant. A cache that cannot be read is a
cold cache, and a cache that cannot be written is a run that was not made any
slower than it would have been without one. Neither is allowed to fail a job
that would otherwise have produced clips.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import hashlib
import json
import os
import time

CACHE_FILENAME = "analysis_cache.json"

# Bumped if the stored shape changes. A file from a version this code does not
# recognise is ignored rather than guessed at.
CACHE_VERSION = 1

# The CLI writes every run into one shared ``outputs/`` directory
# (``config.py``), unlike the web path which is per job. Without a cap this file
# would grow for the life of the install, so the oldest entries are dropped.
# 200 windows is roughly thirty 20-minute videos.
DEFAULT_LIMIT = 200


class WindowCache:
    """A content-addressed store of one scan window's candidates.

    Loaded eagerly on construction and written once, by an explicit
    :meth:`save`, so a run that dies halfway leaves the previous file intact
    rather than a partial one.
    """

    def __init__(self, path, *, salt="", limit=DEFAULT_LIMIT, time_fn=time.time):
        self.path = str(path)
        self.salt = str(salt)
        self.limit = max(1, int(limit))
        self._time_fn = time_fn
        self._entries = {}
        self._dirty = False
        self._load()

    # ------------------------------------------------------------- reading

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            # Missing, unreadable or malformed: all mean "cold", none mean
            # "stop". The file is rewritten on the next successful save.
            return

        if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
            return

        entries = data.get("entries")
        if isinstance(entries, dict):
            self._entries = {
                key: value
                for key, value in entries.items()
                if isinstance(value, dict) and isinstance(value.get("candidates"), list)
            }

    def key(self, beats_text):
        """The entry key for *beats_text* under this cache's salt.

        Every part is length-delimited by a NUL so that no two different
        (salt, text) pairs can be concatenated into the same string.
        """
        digest = hashlib.sha256()
        for part in (str(CACHE_VERSION), self.salt, str(beats_text)):
            digest.update(part.encode("utf-8", "replace"))
            digest.update(b"\x00")
        return digest.hexdigest()

    def get(self, beats_text):
        """The candidates remembered for this window, or ``None``."""
        entry = self._entries.get(self.key(beats_text))
        if not isinstance(entry, dict):
            return None
        candidates = entry.get("candidates")
        return list(candidates) if isinstance(candidates, list) else None

    # ------------------------------------------------------------- writing

    def put(self, beats_text, candidates):
        """Remember *candidates* for this window. Not written until :meth:`save`."""
        self._entries[self.key(beats_text)] = {
            "saved_at": float(self._time_fn()),
            "candidates": list(candidates or []),
        }
        self._dirty = True

    def save(self):
        """Write the store. Returns whether anything was actually written.

        Atomic: a temporary file is replaced into position, so a crash mid-write
        cannot leave a half-written cache for the next run to choke on.
        """
        if not self._dirty:
            return False

        self._evict()
        payload = {"version": CACHE_VERSION, "entries": self._entries}
        temporary = f"{self.path}.tmp"
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(temporary, self.path)
        except (OSError, ValueError, TypeError):
            try:
                os.unlink(temporary)
            except (OSError, ValueError, TypeError):
                # The cleanup must not raise either. `unlink` rejects some
                # paths (an embedded NUL) before it touches the filesystem, so
                # catching only OSError here would let the handler throw out of
                # a function whose whole contract is that it cannot.
                pass
            return False

        self._dirty = False
        return True

    def _evict(self):
        """Drop the oldest entries until the store fits its limit."""
        excess = len(self._entries) - self.limit
        if excess <= 0:
            return
        oldest = sorted(
            self._entries.items(), key=lambda item: item[1].get("saved_at", 0.0)
        )
        for key, _entry in oldest[:excess]:
            self._entries.pop(key, None)
