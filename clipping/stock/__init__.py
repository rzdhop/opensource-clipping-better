"""Stock B-roll sources (Pexels, Pixabay, a local folder) behind one contract.

Stdlib only; independent of ``clipping.studio`` (plan 23 stage B1)."""

from .base import (
    DEFAULT_POOL,
    DEFAULT_SOURCES,
    StockClip,
    StockError,
    StockPool,
    download,
    orientation_for,
    orientation_matches,
    redact,
    search,
)
from .clips import (
    any_source_available,
    available_sources,
    fetch_for_clips,
    local_clip_count,
    local_root,
    resolve_local_dir,
)
from .credits import credit_line, credit_record
from .local import LocalIndex, probe_video

__all__ = [
    "DEFAULT_POOL", "DEFAULT_SOURCES", "LocalIndex", "StockClip", "StockError", "StockPool",
    "any_source_available", "available_sources", "credit_line", "credit_record", "download",
    "fetch_for_clips", "local_clip_count", "local_root", "orientation_for", "orientation_matches",
    "probe_video", "redact", "resolve_local_dir", "search",
]
