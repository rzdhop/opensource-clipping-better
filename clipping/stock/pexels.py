"""Pexels video search, carried over from clipping/studio/broll.py (the request,
the orientation from the aspect, the 1080p-then-HD quality sort).

The key is the ``Authorization`` header; the search opens through the project's
credential-safe opener, so a redirect to another origin drops it (DEC-195/196)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from . import base
from .base import StockClip, StockError

PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"
LICENCE = "Pexels License"
LICENCE_URL = "https://www.pexels.com/license/"


def _quality_key(video_file):
    return (video_file.get("quality") != "hd", -(video_file.get("width") or 0), -(video_file.get("height") or 0))


def _clip(video) -> StockClip | None:
    files = [f for f in video.get("video_files") or () if f.get("file_type") == "video/mp4" and f.get("link")]
    if not files:
        return None
    best = sorted(files, key=_quality_key)[0]
    user = video.get("user") or {}
    return StockClip(
        provider="pexels", id=str(video["id"]), url=str(best["link"]),
        width=int(best.get("width") or 0), height=int(best.get("height") or 0),
        duration_s=float(video.get("duration") or 0.0), tags=(),
        author=str(user.get("name") or ""), author_url=str(user.get("url") or ""),
        page_url=str(video.get("url") or ""), licence=LICENCE, licence_url=LICENCE_URL)


class PexelsSource:
    name = "pexels"
    shuffle = True  # the original picked at random among the unused results

    def available(self, env) -> bool:
        return bool(str(base.environ(env).get("PEXELS_API_KEY") or "").strip())

    def candidates(self, query, *, aspect, min_duration_s=0.0, env=None, opener=None):
        key = str(base.environ(env).get("PEXELS_API_KEY") or "").strip()
        if not key:
            return []
        params = urllib.parse.urlencode({
            "query": query, "orientation": base.orientation_for(aspect),
            "per_page": 30, "size": "large", "resolution_name": "1080p"})
        request = urllib.request.Request(
            f"{PEXELS_VIDEO_SEARCH_URL}?{params}",
            headers={"Authorization": key, "User-Agent": base.USER_AGENT})
        try:
            with (opener or base.opener()).open(request, timeout=base.REQUEST_TIMEOUT) as response:
                data = json.load(response)
        except Exception as exc:  # noqa: BLE001
            raise StockError(f"Pexels search for {query!r} failed: {base.redact(exc, key)}") from None
        clips = []
        for video in (data.get("videos") or ()) if isinstance(data, dict) else ():
            try:
                clip = _clip(video)
            except (KeyError, TypeError, ValueError):
                continue
            if clip is not None:
                clips.append(clip)
        return clips


SOURCE = PexelsSource()
