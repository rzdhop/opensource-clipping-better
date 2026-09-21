"""Acquiring a video and its subtitles from a URL, best-effort.

The pipeline is local-first and stays that way: this is an optional convenience
that either produces a file or says plainly that it could not, never a step the
rest of the pipeline depends on.

The implementation lives in ``downloader`` rather than ``fetch`` so that the
package can export a function called ``fetch`` without shadowing the module of
the same name -- which it did, making ``from clipping.ingest import fetch``
return the function to some callers and the module to others.
"""

from .downloader import (
    FetchError,
    FetchResult,
    build_options,
    enabled,
    fetch,
    is_bot_wall,
    pick_subtitle,
)

__all__ = [
    "FetchError",
    "FetchResult",
    "build_options",
    "enabled",
    "fetch",
    "is_bot_wall",
    "pick_subtitle",
]
