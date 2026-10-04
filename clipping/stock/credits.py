"""The credit a stock clip's licence asks for: a line for a description or an end
card, and a JSON record for the metadata."""

from __future__ import annotations

import logging

from .base import StockClip

logger = logging.getLogger("clipping.stock")

_SITES = {"pexels": "Pexels", "pixabay": "Pixabay"}


def credit_line(clip: StockClip) -> str:
    site = _SITES.get(clip.provider)
    if site:
        who = clip.author or "an unnamed contributor"
        where = f" ({clip.page_url})" if clip.page_url else ""
        return f"Video by {who} on {site}{where}, {clip.licence}"
    author = clip.author or "unknown author"
    return f"{author}, licence: {clip.licence or 'unknown'}" + (f" ({clip.page_url})" if clip.page_url else "")


def credit_record(clip: StockClip) -> dict:
    """The JSON-serialisable record; a local clip with no licence carries a warning."""
    licence = clip.licence or "unknown"
    record = {
        "key": clip.key, "provider": clip.provider, "id": clip.id, "credit": credit_line(clip),
        "author": clip.author, "author_url": clip.author_url, "page_url": clip.page_url,
        "licence": licence, "licence_url": clip.licence_url,
        "width": clip.width, "height": clip.height, "duration_s": clip.duration_s, "tags": list(clip.tags),
    }
    if clip.provider == "local" and licence.lower() == "unknown":
        record["warning"] = "licence unknown: add a sidecar <name>.json with a licence before publishing"
        logger.warning("stock: local clip %s has an unknown licence", clip.id)
    return record
