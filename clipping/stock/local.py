"""The human's own B-roll folder (``BROLL_LOCAL_DIR``).

Indexed: ``.mp4``/``.mov``/``.webm`` only, symlinks refused, every real path kept
inside the configured root. Keywords come from the filename tokens plus an optional
sidecar ``<name>.json`` (``keywords``, ``licence``, ``author``, ``source_url``) or a
folder ``index.json`` (filename -> the same fields; a sidecar wins). ffprobe gives
width, height, duration and rotation; the probe result is cached by path, size and
mtime (in memory, and on disk under the stock cache because the folder is mounted
read-only). Scoring is the token overlap with plural stripping: at least one query
token must match, and a clip whose orientation suits the aspect ranks above one that
does not."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import threading

from . import base
from .base import StockClip

logger = logging.getLogger("clipping.stock")

EXTENSIONS = (".mp4", ".mov", ".webm")
SIDECAR_MAX_BYTES = 1024 * 1024
UNKNOWN_LICENCE = "unknown"
STOPWORDS = frozenset({"a", "an", "the", "of", "in", "on", "at", "and", "with", "to", "for", "is", "by"})
_TOKEN = re.compile(r"[a-z0-9]+")


# ------------------------------------------------------------------ tokens

def stem(token: str) -> str:
    """Plural stripping: cities -> city, boxes -> box, cars -> car (kept: glass, bus)."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("sses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokens(text) -> list:
    return [stem(t) for t in _TOKEN.findall(str(text).lower()) if t not in STOPWORDS and not t.isdigit()]


def score(query_tokens, clip_tokens) -> int:
    return len(set(query_tokens) & set(clip_tokens))


# ------------------------------------------------------------------- probe

def probe_video(path, *, run=None):
    """``{"width", "height", "duration_s"}`` (rotation applied) or None when ffprobe
    cannot read the file. Same command as ``manual_uploads.probe_clip`` (kept apart:
    that module sits in the aistory package, whose imports are heavy)."""
    argv = ["ffprobe", "-v", "error", "-show_entries",
            "stream=codec_type,codec_name,width,height:stream_tags=rotate:stream_side_data=rotation:format=duration",
            "-of", "json", os.fspath(path)]
    try:
        result = (run or subprocess.run)(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        data = json.loads(result.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not isinstance(data, dict):
        return None
    video = next((s for s in data.get("streams") or () if isinstance(s, dict) and s.get("codec_type") == "video"
                  and s.get("codec_name") not in ("mjpeg", "png")), None)
    if video is None:
        return None
    try:
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        return None
    rotation = 0
    for item in video.get("side_data_list") or ():
        try:
            rotation = int(float(item.get("rotation") or 0))
        except (TypeError, ValueError, AttributeError):
            pass
    try:
        rotation = rotation or int(float((video.get("tags") or {}).get("rotate") or 0))
    except (TypeError, ValueError):
        pass
    if abs(rotation) % 180 == 90:
        width, height = height, width
    if width <= 0 or height <= 0:
        return None
    return {"width": width, "height": height, "duration_s": round(duration, 3)}


# ------------------------------------------------------------------- paths

def inside_root(root_real: str, path: str) -> bool:
    """Whether the real path of *path* is *root_real* or below it."""
    real = os.path.realpath(path)
    return real == root_real or real.startswith(root_real.rstrip(os.sep) + os.sep)


def _read_json(path, root_real):
    """A small JSON object next to the clips, or {} (symlink, outside the root, too
    big, unreadable or not an object)."""
    try:
        if os.path.islink(path) or not os.path.isfile(path) or not inside_root(root_real, path):
            return {}
        if os.path.getsize(path) > SIDECAR_MAX_BYTES:
            return {}
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _keywords(value) -> list:
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, (list, tuple)) else []


def _http(value) -> str:
    value = str(value or "").strip()
    return value if value.lower().startswith(("http://", "https://")) else ""


# ------------------------------------------------------------------- index

class LocalIndex:
    """The clips under *root*. ``probe(path) -> dict | None`` is the seam for tests."""

    def __init__(self, root, *, probe=probe_video, cache_file=None):
        self.root = os.fspath(root)
        self.root_real = os.path.realpath(self.root)
        self._probe = probe
        self._cache_file = cache_file
        self._probes: dict = self._load_cache()
        self.probe_calls = 0

    def _load_cache(self) -> dict:
        if not self._cache_file:
            return {}
        try:
            with open(self._cache_file, encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_cache(self) -> None:
        if not self._cache_file:
            return
        try:
            os.makedirs(os.path.dirname(self._cache_file), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self._cache_file), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._probes, handle)
            os.replace(tmp, self._cache_file)
        except OSError:
            pass

    def _files(self):
        for folder, dirs, names in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(folder, d)))
            for name in sorted(names):
                if name.startswith(".") or os.path.splitext(name)[1].lower() not in EXTENSIONS:
                    continue
                path = os.path.join(folder, name)
                if os.path.islink(path):
                    logger.warning("stock: symlink %s refused", path)
                    continue
                if not inside_root(self.root_real, path):
                    logger.warning("stock: %s resolves outside the B-roll folder, refused", path)
                    continue
                yield folder, name, path

    def scan(self) -> list:
        """Every usable clip as a StockClip (``local:<sha>``), sorted by relative path."""
        clips, seen, changed = [], {}, False
        folder_index: dict = {}
        for folder, name, path in self._files():
            rel = os.path.relpath(path, self.root).replace(os.sep, "/")
            try:
                stat = os.stat(path)
            except OSError:
                continue
            stamp = [stat.st_size, stat.st_mtime_ns]
            cached = self._probes.get(rel)
            if isinstance(cached, dict) and cached.get("stamp") == stamp and isinstance(cached.get("info"), dict):
                info = cached["info"]
            else:
                self.probe_calls += 1
                info = self._probe(path)
                changed = True
                if not info:
                    logger.warning("stock: %s is not a readable video, skipped", rel)
                    continue
            seen[rel] = {"stamp": stamp, "info": info}
            if folder not in folder_index:
                data = _read_json(os.path.join(folder, "index.json"), self.root_real)
                folder_index[folder] = data.get("files") if isinstance(data.get("files"), dict) else data
            stem_name = os.path.splitext(name)[0]
            meta = {}
            index_meta = folder_index[folder].get(name) or folder_index[folder].get(stem_name)
            if isinstance(index_meta, dict):
                meta.update(index_meta)
            for sidecar in (stem_name + ".json", name + ".json"):
                side = _read_json(os.path.join(folder, sidecar), self.root_real)
                if side:
                    meta.update(side)
                    break
            words = _keywords(meta.get("keywords"))
            digest = hashlib.sha256(f"{rel}\x00{stat.st_size}".encode("utf-8")).hexdigest()[:16]
            clips.append(StockClip(
                provider="local", id=digest, url=os.path.realpath(path),
                width=int(info["width"]), height=int(info["height"]), duration_s=float(info.get("duration_s") or 0.0),
                tags=tuple(dict.fromkeys(tokens(stem_name) + [stem(w.lower()) for w in words])),
                author=str(meta.get("author") or ""), author_url="", page_url=_http(meta.get("source_url")),
                licence=str(meta.get("licence") or UNKNOWN_LICENCE), licence_url=""))
        if changed or set(seen) != set(self._probes):
            self._probes = seen
            self._save_cache()
        return clips


def rank(clips, query, *, aspect, min_duration_s=0.0) -> list:
    """The clips matching at least one query token, best first: a matching
    orientation above a mismatched one, then by overlap, then by id."""
    wanted = tokens(query)
    ranked = []
    for clip in clips:
        overlap = score(wanted, clip.tags)
        if overlap < 1 or (min_duration_s and clip.duration_s and clip.duration_s < float(min_duration_s)):
            continue
        ranked.append((not base.orientation_matches(aspect, clip.width, clip.height), -overlap, clip.url, clip))
    ranked.sort(key=lambda item: item[:3])
    return [item[3] for item in ranked]


class LocalSource:
    name = "local"
    shuffle = False

    def __init__(self, probe=None):
        self._probe = probe
        self._indexes: dict = {}
        self._lock = threading.Lock()

    def root(self, env):
        value = str(base.environ(env).get("BROLL_LOCAL_DIR") or "").strip()
        return value if value and os.path.isdir(value) else ""

    def available(self, env) -> bool:
        return bool(self.root(env))

    def index(self, env) -> LocalIndex:
        root = self.root(env)
        real = os.path.realpath(root)
        with self._lock:
            if real not in self._indexes:
                cache = os.path.join(base.cache_root(env), "local", hashlib.sha256(real.encode("utf-8")).hexdigest()[:16] + ".json")
                self._indexes[real] = LocalIndex(root, probe=self._probe or probe_video, cache_file=cache)
            return self._indexes[real]

    def candidates(self, query, *, aspect, min_duration_s=0.0, env=None):
        if not self.available(env):
            return []
        return rank(self.index(env).scan(), query, aspect=aspect, min_duration_s=min_duration_s)


SOURCE = LocalSource()
