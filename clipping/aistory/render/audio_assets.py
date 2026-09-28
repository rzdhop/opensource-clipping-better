"""SFX/BGM asset resolution for the AI-Story renderer (spec 6.5, 11;
plan phase 4 stage 2, "Assets step" point 5; DEC-160, RC-A9).

Pure and stdlib-only (DEC-012): every function here only reads already-built
index JSON files and does arithmetic. Nothing calls ffmpeg/ffprobe, nothing
writes, and nothing raises on a missing asset -- a caller (the future
``steps/assets.py``) is expected to report a ``None`` result, never crash on
one.

Layout assumed (see ``tools/make_sfx.py`` and ``assets/bgm/bgm_index.json``):

- ``assets/sfx/<pack>/sfx_index.json``: ``{"$schema": "sfx_index_v1",
  "pack": ..., "cues": {cue: {file, duration_s, sha256, licence, source}}}``.
  ``file`` is relative to ``assets/sfx/`` and may point into a *different*
  pack's folder when a cue name is shared (e.g. ``cartoon_soft``'s "boing"
  entry has ``file: "cartoon/boing.wav"``) -- there is exactly one physical
  file per cue name, never a duplicate.
- ``assets/bgm/bgm_index.json``: ``{"$schema": "bgm_index_v1", "tracks":
  [{file, moods[], duration_s, bpm, licence, source}], "notes": {...}}``.
  ``file`` is relative to ``assets/bgm/`` and always lands inside one of the
  existing mood folders (``clipping/studio/audio_bgm.py`` keeps reading
  those folders directly and never this index -- RC-A9).

Repo root is found relative to this file, the same pattern
``clipping/aistory/templates.py`` uses for ``templates/``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ASSETS_DIR = REPO_ROOT / "assets"
SFX_DIR = ASSETS_DIR / "sfx"
BGM_DIR = ASSETS_DIR / "bgm"
FONTS_DIR = ASSETS_DIR / "fonts"


# ------------------------------------------------------------------ paths

def _safe_join(base, rel) -> Optional[Path]:
    """Resolve *rel* (a ``/``-separated relative path recorded in an index)
    against *base*, refusing anything that would escape *base* or pass
    through a symlink anywhere along the way. Returns ``None`` instead of
    raising for any bad input -- an absolute path, ``..`` traversal, a
    missing target or a symlinked component.
    """
    if not isinstance(rel, str) or not rel:
        return None
    if rel.startswith("/") or rel.startswith("\\") or ":" in rel.split("/")[0]:
        return None
    parts = [p for p in rel.replace("\\", "/").split("/")]
    if any(p in ("", ".", "..") for p in parts):
        return None

    base = Path(base)
    try:
        real_base = os.path.realpath(str(base))
    except OSError:
        return None

    current = base
    for part in parts:
        current = current / part
        # A symlink anywhere on the path (including the final component)
        # is refused outright -- it is never followed.
        if os.path.islink(str(current)):
            return None

    if not os.path.exists(str(current)):
        return None

    real_current = os.path.realpath(str(current))
    if os.path.commonpath([real_base, real_current]) != real_base:
        return None

    return current


# ------------------------------------------------------------------ indexes

def _load_json_index(path: Path, expected_schema: str) -> dict:
    if not path.is_file() or os.path.islink(str(path)):
        raise FileNotFoundError(f"{path} does not exist or is a symlink")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("$schema") != expected_schema:
        raise ValueError(f"{path}: expected $schema {expected_schema!r}, got {data.get('$schema')!r}")
    return data


def load_sfx_index(pack: str) -> dict:
    """Load ``assets/sfx/<pack>/sfx_index.json``. Raises if the pack or
    file is missing/malformed -- callers that must never raise use
    ``resolve_sfx`` instead."""
    if not isinstance(pack, str) or not pack or "/" in pack or "\\" in pack or pack in (".", ".."):
        raise ValueError(f"not a pack name: {pack!r}")
    path = SFX_DIR / pack / "sfx_index.json"
    return _load_json_index(path, "sfx_index_v1")


def load_bgm_index() -> dict:
    """Load ``assets/bgm/bgm_index.json``."""
    return _load_json_index(BGM_DIR / "bgm_index.json", "bgm_index_v1")


def load_fonts_index() -> dict:
    """Load ``assets/fonts/fonts_index.json``."""
    return _load_json_index(FONTS_DIR / "fonts_index.json", "fonts_index_v1")


# ------------------------------------------------------------------ sfx

def resolve_sfx(pack, cue) -> Optional[dict]:
    """Resolve a ``(pack, cue)`` SFX reference to its file provenance.

    Returns ``{pack, cue, file, abs_path, duration_s, sha256, licence,
    source}`` or ``None`` when the pack, the cue or the file on disk is
    missing -- never raises. The caller is responsible for reporting a
    ``None`` result (spec 11: "missing cue -> the cue is skipped and
    reported, never a crash").
    """
    if not isinstance(pack, str) or not isinstance(cue, str):
        return None
    try:
        index = load_sfx_index(pack)
    except (OSError, ValueError, LookupError, json.JSONDecodeError):
        return None

    entry = index.get("cues", {}).get(cue)
    if entry is None:
        return None

    abs_path = _safe_join(SFX_DIR, entry.get("file"))
    if abs_path is None:
        return None

    return {
        "pack": pack,
        "cue": cue,
        "file": entry.get("file"),
        "abs_path": str(abs_path),
        "duration_s": entry.get("duration_s"),
        "sha256": entry.get("sha256"),
        "licence": entry.get("licence"),
        "source": entry.get("source"),
    }


# ------------------------------------------------------------------ bgm mood

def dominant_emotion(scenes) -> Optional[str]:
    """The emotion with the largest summed scene duration.

    *scenes* is an iterable of mappings, each carrying at least ``emotion``
    and ``duration_s``, given in chronological (episode) order. Ties go to
    whichever tied emotion's earliest scene comes first in *scenes* --
    never to e.g. alphabetical order. Returns ``None`` for no scenes.
    """
    totals: dict = {}
    first_index: dict = {}
    count = 0
    for i, scene in enumerate(scenes):
        emotion = scene["emotion"]
        duration = float(scene.get("duration_s", 0.0))
        totals[emotion] = totals.get(emotion, 0.0) + duration
        if emotion not in first_index:
            first_index[emotion] = i
        count += 1

    if count == 0:
        return None

    max_total = max(totals.values())
    tied = [emotion for emotion, total in totals.items() if total == max_total]
    return min(tied, key=lambda emotion: first_index[emotion])


def bgm_mood(style_audio: dict, emotion) -> Optional[str]:
    """Resolve *emotion* to a BGM mood name via ``style_audio``'s
    ``emotion_to_mood`` table (the style template's or style_lock's
    ``audio`` block), falling back to its ``"default"`` entry.
    """
    mapping = (style_audio or {}).get("emotion_to_mood") or {}
    if emotion in mapping:
        return mapping[emotion]
    return mapping.get("default")


def pick_track(bgm_index: dict, mood, story_id, ep) -> Optional[dict]:
    """Deterministically pick one track carrying *mood* from *bgm_index*
    (as returned by ``load_bgm_index``, or an equivalent in-memory dict for
    testing).

    The pick is ``sha256(f"{story_id}:{ep}") mod n`` among the tracks that
    list *mood*, over the tracks sorted by file path so the choice does not
    depend on JSON key order. Returns ``None`` when no track carries the
    mood.
    """
    tracks = [t for t in bgm_index.get("tracks", []) if mood in (t.get("moods") or [])]
    if not tracks:
        return None
    tracks = sorted(tracks, key=lambda t: t["file"])

    key = f"{story_id}:{ep}".encode("utf-8")
    digest = hashlib.sha256(key).hexdigest()
    index = int(digest, 16) % len(tracks)
    track = tracks[index]

    abs_path = _safe_join(BGM_DIR, track.get("file"))
    return {
        "file": track.get("file"),
        "abs_path": str(abs_path) if abs_path is not None else None,
        "moods": track.get("moods"),
        "duration_s": track.get("duration_s"),
        "bpm": track.get("bpm"),
        "licence": track.get("licence"),
        "source": track.get("source"),
    }
