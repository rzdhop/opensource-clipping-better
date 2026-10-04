"""The shared contract of the stock B-roll sources (plan 23 stage B1).

A *source* (Pexels, Pixabay, the local folder) turns a query and an aspect ratio
into ranked :class:`StockClip` candidates; :func:`search` tries the sources in
order and returns the first match that the :class:`StockPool` has not handed out
yet; :func:`download` writes the chosen clip to disk through a ``.part`` file.

Stdlib only (DEC-012). Nothing here imports ``clipping.studio`` (it loads cv2 and
mediapipe on import and is frozen by the render-layer guard). Every network call
goes through ``clipping.providers.transport._OPENER``, whose redirect handler
drops the credential headers when a hop leaves their origin (DEC-195, DEC-196).
"""

from __future__ import annotations

import logging
import os
import random
import re
import shutil
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

logger = logging.getLogger("clipping.stock")

# Carried over from clipping/studio/utils.py: the ratios cropped from a portrait source.
VERTICAL_RATIOS = frozenset({"9:16", "1:1", "3:4", "4:5"})
DEFAULT_SOURCES = ("local", "pexels", "pixabay")
USER_AGENT = "Mozilla/5.0"
REQUEST_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 120
DEFAULT_CACHE_DIR = os.path.join("outputs", ".cache", "stock")


class StockError(Exception):
    """A source or a download failed. The message never carries an API key."""


@dataclass(frozen=True)
class StockClip:
    """One downloadable stock clip and what its licence asks for."""

    provider: str
    id: str
    url: str
    width: int
    height: int
    duration_s: float
    tags: tuple = ()
    author: str = ""
    author_url: str = ""
    page_url: str = ""
    licence: str = ""
    licence_url: str = ""

    @property
    def key(self) -> str:
        """The pool's identity: ``pexels:123``, ``pixabay:9``, ``local:<sha>``."""
        return f"{self.provider}:{self.id}"


# ------------------------------------------------------------------ aspect

def orientation_for(aspect) -> str:
    """``portrait`` for the ratios cropped from a vertical source (incl. 1:1, as
    the Pexels request always did), ``landscape`` otherwise."""
    return "portrait" if str(aspect) in VERTICAL_RATIOS else "landscape"


def orientation_matches(aspect, width, height) -> bool:
    """Whether a *width* x *height* clip suits *aspect* without a rotation: a
    landscape target wants a wider-than-tall clip, a portrait or square target a
    clip at least as tall as it is wide."""
    width, height = int(width or 0), int(height or 0)
    if width <= 0 or height <= 0:
        return False
    if orientation_for(aspect) == "landscape":
        return width > height
    return height >= width


# --------------------------------------------------------------- redaction

_KEY_PARAM = re.compile(r"(?i)([?&;]key=)[^&\s'\"<>]*")


def redact(text, *secrets) -> str:
    """*text* with every secret and every ``key=...`` query value removed."""
    out = _KEY_PARAM.sub(r"\1REDACTED", str(text))
    for secret in secrets:
        if secret:
            out = out.replace(str(secret), "REDACTED")
            out = out.replace(urllib.parse.quote(str(secret), safe=""), "REDACTED")
    return out


def environ(env) -> Mapping:
    return os.environ if env is None else env


def cache_root(env=None) -> str:
    """``STOCK_CACHE_DIR`` or ``outputs/.cache/stock``."""
    return str(environ(env).get("STOCK_CACHE_DIR") or DEFAULT_CACHE_DIR)


def opener():
    """The project's credential-safe opener (looked up on each call)."""
    from clipping.providers import transport
    return transport._OPENER


# -------------------------------------------------------------------- pool

class StockPool:
    """Hands a clip out once. When every candidate of a query was handed out the
    pool resets for that query (its candidates become available again), the contract
    of ``USED_PEXELS_IDS`` in studio/broll.py."""

    def __init__(self, rng=None):
        self._used: set[str] = set()
        self._lock = threading.Lock()
        self._rng = rng

    def __contains__(self, key) -> bool:
        with self._lock:
            return str(key) in self._used

    def __len__(self) -> int:
        with self._lock:
            return len(self._used)

    def clear(self) -> None:
        with self._lock:
            self._used.clear()

    def mark(self, clip) -> None:
        with self._lock:
            self._used.add(clip.key)

    def take(self, candidates: Sequence[StockClip], *, shuffle=False, rng=None):
        """The first (or, with *shuffle*, a random) candidate not used yet, else, after
        the reset, one of them again; None when there is no candidate at all."""
        candidates = list(candidates)
        if not candidates:
            return None
        with self._lock:
            fresh = [clip for clip in candidates if clip.key not in self._used]
            if not fresh:
                logger.info("stock pool for the query is exhausted, resetting")
                for clip in candidates:
                    self._used.discard(clip.key)
                fresh = candidates
            chooser = rng or self._rng or random
            clip = chooser.choice(fresh) if shuffle else fresh[0]
            self._used.add(clip.key)
            return clip


DEFAULT_POOL = StockPool()


# ------------------------------------------------------------------ search

def _registry() -> dict:
    from . import local, pexels, pixabay
    return {"local": local.SOURCE, "pexels": pexels.SOURCE, "pixabay": pixabay.SOURCE}


def parse_sources(value) -> tuple:
    """``"local, pexels"`` or a sequence of names/source objects -> a tuple, in order."""
    if value is None:
        return DEFAULT_SOURCES
    if isinstance(value, str):
        names = [part.strip().lower() for part in value.split(",")]
        return tuple(name for name in names if name) or DEFAULT_SOURCES
    return tuple(value)


def search(query, *, aspect, min_duration_s=0.0, sources=None, env=None, pool=None, rng=None):
    """The first match over *sources* (names or source objects, tried in order), or None.

    A source without its key or folder is skipped; one that fails is logged (key
    removed) and the next is tried. A clip the *pool* already handed out is skipped
    until the query runs out, then the pool resets."""
    query = " ".join(str(query or "").split())
    if not query:
        return None
    env = environ(env)
    pool = DEFAULT_POOL if pool is None else pool
    registry = None
    for entry in parse_sources(sources):
        if isinstance(entry, str):
            registry = registry or _registry()
            source = registry.get(entry)
            if source is None:
                logger.warning("stock: unknown source %r skipped", entry)
                continue
        else:
            source = entry
        try:
            if not source.available(env):
                continue
            found = list(source.candidates(query, aspect=aspect, min_duration_s=min_duration_s, env=env))
        except Exception as exc:  # noqa: BLE001 - one failing source must not stop the next
            logger.warning("stock: %s failed for %r: %s", getattr(source, "name", source), query, redact(exc))
            continue
        if min_duration_s:
            found = [c for c in found if not c.duration_s or c.duration_s >= float(min_duration_s)]
        clip = pool.take(found, shuffle=bool(getattr(source, "shuffle", False)), rng=rng)
        if clip is not None:
            return clip
    return None


# ---------------------------------------------------------------- download

def download(clip: StockClip, dest, *, timeout=DOWNLOAD_TIMEOUT, opener_=None) -> str:
    """Write *clip* to *dest* through ``<dest>.part`` and rename it; returns *dest*.
    A local clip is copied. Only http(s) URLs are fetched."""
    dest = os.fspath(dest)
    part = dest + ".part"
    parent = os.path.dirname(os.path.abspath(dest))
    os.makedirs(parent, exist_ok=True)
    try:
        if clip.provider == "local":
            if os.path.islink(clip.url) or not os.path.isfile(clip.url):
                raise StockError("the local clip is gone or is a symlink")
            shutil.copyfile(clip.url, part)
        else:
            if urllib.parse.urlsplit(clip.url).scheme not in ("http", "https"):
                raise StockError(f"refusing a non-http(s) clip URL for {clip.key}")
            request = urllib.request.Request(clip.url, headers={"User-Agent": USER_AGENT})
            with (opener_ or opener()).open(request, timeout=timeout) as response, open(part, "wb") as handle:
                shutil.copyfileobj(response, handle)
        os.replace(part, dest)
    except StockError:
        _remove(part)
        raise
    except Exception as exc:  # noqa: BLE001
        _remove(part)
        raise StockError(f"download of {clip.key} failed: {redact(exc)}") from None
    return dest


def _remove(path) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
