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
from .credits import credit_line, credit_record
from .local import LocalIndex, probe_video

__all__ = [
    "DEFAULT_POOL", "DEFAULT_SOURCES", "LocalIndex", "StockClip", "StockError", "StockPool",
    "credit_line", "credit_record", "download", "orientation_for", "orientation_matches",
    "probe_video", "redact", "search",
]
