"""Pixabay video search.

``GET /api/videos/?key=&q=&per_page=&safesearch=true&video_type=film``. The key
travels in the query string, so it is removed from every log line and exception
message (:func:`base.redact`). The API has no video orientation filter: the
rendition is the biggest of large/medium that matches the wanted orientation,
chosen client-side. Responses are cached 24 h (Pixabay's terms require caching)
under ``<cache>/pixabay/<sha256(q + aspect)>.json``."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import urllib.parse
import urllib.request

from . import base
from .base import StockClip, StockError

PIXABAY_VIDEO_SEARCH_URL = "https://pixabay.com/api/videos/"
LICENCE = "Pixabay Content License"
LICENCE_URL = "https://pixabay.com/service/license-summary/"
CACHE_TTL_S = 24 * 3600
PER_PAGE = 50
QUERY_MAX = 100  # the API's limit


def cache_file(env, query, aspect) -> str:
    digest = hashlib.sha256(f"{query}\x00{aspect}".encode("utf-8")).hexdigest()
    return os.path.join(base.cache_root(env), "pixabay", f"{digest}.json")


def _read_cache(path, now):
    try:
        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
        if now - float(stored["fetched_at"]) < CACHE_TTL_S and isinstance(stored["data"], dict):
            return stored["data"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _write_cache(path, data, now) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"fetched_at": now, "data": data}, handle)
        os.replace(tmp, path)
    except OSError:
        pass  # the cache is an optimisation, never a failure


def _rendition(hit, aspect):
    best = None
    for name in ("large", "medium"):
        item = (hit.get("videos") or {}).get(name) or {}
        url = str(item.get("url") or "")
        try:
            width, height = int(item.get("width") or 0), int(item.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if not url or not base.orientation_matches(aspect, width, height):
            continue
        if best is None or width * height > best[1] * best[2]:
            best = (url, width, height)
    return best


def _clip(hit, aspect) -> StockClip | None:
    rendition = _rendition(hit, aspect)
    if rendition is None:
        return None
    url, width, height = rendition
    user, user_id = str(hit.get("user") or ""), hit.get("user_id")
    return StockClip(
        provider="pixabay", id=str(hit["id"]), url=url, width=width, height=height,
        duration_s=float(hit.get("duration") or 0.0),
        tags=tuple(t.strip() for t in str(hit.get("tags") or "").split(",") if t.strip()),
        author=user, author_url=f"https://pixabay.com/users/{user}-{user_id}/" if user and user_id else "",
        page_url=str(hit.get("pageURL") or ""), licence=LICENCE, licence_url=LICENCE_URL)


class PixabaySource:
    name = "pixabay"
    shuffle = True

    def available(self, env) -> bool:
        return bool(str(base.environ(env).get("PIXABAY_API_KEY") or "").strip())

    def _fetch(self, query, aspect, key, env, opener, now):
        path = cache_file(env, query, aspect)
        cached = _read_cache(path, now)
        if cached is not None:
            return cached
        params = urllib.parse.urlencode({
            "key": key, "q": query[:QUERY_MAX], "per_page": PER_PAGE,
            "safesearch": "true", "video_type": "film"})
        request = urllib.request.Request(
            f"{PIXABAY_VIDEO_SEARCH_URL}?{params}", headers={"User-Agent": base.USER_AGENT})
        try:
            with (opener or base.opener()).open(request, timeout=base.REQUEST_TIMEOUT) as response:
                data = json.load(response)
        except Exception as exc:  # noqa: BLE001
            raise StockError(f"Pixabay search for {query!r} failed: {base.redact(exc, key)}") from None
        if not isinstance(data, dict):
            raise StockError(f"Pixabay search for {query!r} returned an unexpected answer")
        _write_cache(path, data, now)
        return data

    def candidates(self, query, *, aspect, min_duration_s=0.0, env=None, opener=None, now=None):
        key = str(base.environ(env).get("PIXABAY_API_KEY") or "").strip()
        if not key:
            return []
        data = self._fetch(query, aspect, key, env, opener, time.time() if now is None else now)
        clips = []
        for hit in data.get("hits") or ():
            try:
                clip = _clip(hit, aspect)
            except (KeyError, TypeError, ValueError):
                continue
            if clip is not None:
                clips.append(clip)
        return clips


SOURCE = PixabaySource()
