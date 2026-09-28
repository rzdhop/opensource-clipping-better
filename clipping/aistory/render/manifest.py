"""The render manifest (``render_manifest_v1``, spec 2.9; plan phase 4 stage 7,
"Renderer" -> manifest.py; DEC-156).

The runner (``render/runner.py``) keeps one manifest per episode render and
rewrites it at every change of state, so a reader -- the dashboard, a crashed
job's next run, a human -- always sees which command is running, with the
exact argv, before the process exists (**manifest-before-run**).

Every write here is validated with ``schemas.render_manifest_errors`` first
and then written atomically (``store._atomic_write_json``: a temp file in the
same folder, then ``os.replace``). An invalid document is never written: it
raises :class:`ManifestError`, which is a bug in the runner, not an input
problem.

Stdlib + this package only (DEC-012).
"""

from __future__ import annotations

import json
import os
import re

from .. import schemas
from .. import store as store_mod

_CACHE_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(RuntimeError):
    """A manifest the runner was about to write does not validate."""


def new_manifest(plan: dict, *, now: str) -> dict:
    """The manifest of a render that is about to start: no stage, no output,
    the timings open. *plan* is ``render/plan.py``'s plan."""
    return {
        "$schema": schemas.RENDER_MANIFEST_SCHEMA_NAME,
        "ep": plan["ep"],
        "profile": plan["profile"],
        "params": dict(plan["params"]),
        "ffmpeg": dict(plan["ffmpeg"]),
        "font": dict(plan["font"]) if plan["font"] is not None else None,
        "inputs": [
            {key: item[key] for key in ("role", "id", "source", "staged", "sha256")}
            for item in plan["inputs"]
        ],
        "stages": [],
        "output": None,
        "timings": {"started_at": now, "finished_at": None, "total_s": None},
        "warnings": list(plan["warnings"]),
        "created_at": now,
        "updated_at": now,
    }


def stage_entry(stage: dict, argv: list, *, state: str = "running") -> dict:
    """A stage's manifest entry, before it has a result."""
    return {
        "id": stage["id"],
        "kind": stage["kind"],
        "argv": list(argv),
        "cache_key": stage.get("cache_key"),
        "state": state,
        "output": None,
        "output_sha256": None,
        "seconds": None,
        "stderr_tail": None,
    }


def write_manifest(path: str, doc: dict) -> None:
    """Validate *doc*, then write it to *path* atomically."""
    errors = schemas.render_manifest_errors(doc)
    if errors:
        raise ManifestError("the render manifest does not validate: " + "; ".join(errors[:5]))
    store_mod._atomic_write_json(path, doc)


def read_manifest(path: str):
    """The manifest at *path*, or None when there is none or it cannot be
    read as a JSON object (a previous render is then simply unknown)."""
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def cache_keys(doc) -> set:
    """Every cache key a manifest's stages name (the shots and the end card
    it rendered or found in ``render/cache/``). Tolerant of an old or
    damaged document: whatever is not a list of stages with 64-hex keys is
    ignored, so a bad previous manifest can only prune more, never crash."""
    keys = set()
    if not isinstance(doc, dict) or not isinstance(doc.get("stages"), list):
        return keys
    for stage in doc["stages"]:
        if isinstance(stage, dict):
            key = stage.get("cache_key")
            if isinstance(key, str) and _CACHE_KEY_RE.match(key):
                keys.add(key)
    return keys


def relative_to(path: str, base_dir: str) -> str:
    """*path* relative to *base_dir*, with forward slashes; ValueError when
    it is not inside it (a manifest path never climbs with ``..``)."""
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(base_dir)).replace(os.sep, "/")
    if rel == ".." or rel.startswith("../") or os.path.isabs(rel):
        raise ValueError(f"{path!r} is not inside {base_dir!r}")
    return rel
