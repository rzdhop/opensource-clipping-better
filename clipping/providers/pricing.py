"""The price table behind every estimate (DEC-099).

Every paid link of every default chain has a unit price here; a paid link
without one is refused, never guessed. The numbers were read on
``PRICES_AS_OF`` from the pages listed in
``.claude/plans/ai-story/09-APPENDIX-research-2026-09-25.md`` and change
monthly -- that appendix is the place to re-read from, this file the place to
update. Free-tier links estimate $0.00 whatever the table says beyond their
allowance; the allowance itself is ``limits.py``'s business.

Stdlib only.
"""

from __future__ import annotations

from collections import namedtuple

from .generation import is_paid
from .registry import describe

PRICES_AS_OF = "2026-09-25"

UNITS = ("image", "second", "char", "token")

# ``usd`` is the price of ONE unit. ``per_megapixel`` says the price is per
# megapixel of output and scales with the requested size.
Price = namedtuple("Price", "unit usd note per_megapixel", defaults=("", False))

PRICES = {
    # --- images (appendix A, spec 8.7)
    "cloudflare/flux-1-schnell": Price("image", 0.0, "free: 10,000 neurons/day, about 170 images; $0.0006/image beyond, on a paid Workers plan"),
    "pollinations/flux": Price("image", 0.0, "pollen credits; keyless at the legacy rate"),
    "gemini/nano-banana-2-lite": Price("image", 0.0336, "gemini-3.1-flash-lite-image at 1K; batch $0.0168; not on the free tier"),
    "gemini/nano-banana-2": Price("image", 0.067, "gemini-3.1-flash-image at 1K; not on the free tier"),
    "fal/flux-schnell": Price("image", 0.003, "$0.003 per megapixel: about $0.0062 at 1080x1920", per_megapixel=True),
    "fal/seedream-4-edit": Price("image", 0.03, "multi-reference edit"),
    "fal/flux-kontext-pro": Price("image", 0.04, "single-reference edit"),
    "openai/gpt-image-2-low": Price("image", 0.005, "quality low, 1024x1536"),
    # --- video, per second of output (appendix B)
    "fal/seedance-1-pro-fast": Price("second", 0.022, "about $0.11 per 5 s at 720p, token-billed"),
    "fal/ltx-2-fast": Price("second", 0.04, "1080p with native audio; the endpoint is moving to LTX-2.3"),
    "fal/kling-2.5-turbo-std": Price("second", 0.042, "$0.21 per 5 s"),
    "gemini/veo-3.1-lite": Price("second", 0.05, "$0.25 per 5 s at 720p"),
    # --- speech (appendix C)
    "gemini/flash-lite-tts": Price("second", 0.0, "free tier; $0.0015 per 10 s beyond"),
    "gcloud/neural2": Price("char", 0.000016, "$16 per million characters; extension point"),
    "openai/gpt-4o-mini-tts": Price("second", 0.00025, "about $0.015 per minute; extension point"),
    "elevenlabs/flash": Price("char", 0.00005, "$0.05 per 1k characters; extension point"),
    # --- text and vision (appendix D)
    "gemini/flash-lite": Price("token", 0.0, "free tier"),
    "gemini/flash": Price("token", 0.0000003, "not in the appendix: Flash-class input tokens at about $0.30 per million on ai.google.dev pricing -- verify before relying on it"),
}

# Whole providers whose every link is free: the unit is what such a link
# produces, the price is nothing.
FREE_PROVIDER_PRICES = {
    "edge": Price("char", 0.0, "Edge TTS: free, unofficial"),
    "local": Price("image", 0.0, "your own hardware"),
    "openrouter": Price("token", 0.0, ":free model"),
    "pollinations": Price("image", 0.0, "pollen credits; keyless at the legacy rate"),
    "cloudflare": Price("image", 0.0, "free allowance"),
}

Estimate = namedtuple("Estimate", "link unit qty price_usd est_usd paid")


class PriceUnknown(LookupError):
    """A paid link has no price in the table. Add it; never guess."""


def price_for(link) -> Price:
    """The :class:`Price` of *link*: exact entry, else the provider's free price."""
    label = describe(link)
    price = PRICES.get(label)
    if price is not None:
        return price
    if not is_paid(link):
        fallback = FREE_PROVIDER_PRICES.get(link.provider)
        if fallback is not None:
            return fallback
        return Price("image", 0.0, "free")
    raise PriceUnknown(
        f"No price for {label} in the table dated {PRICES_AS_OF} "
        f"(clipping/providers/pricing.py). Add it before calling a paid link."
    )


def estimate(link, qty=1, *, width=None, height=None) -> Estimate:
    """What *qty* units on *link* would cost, from the table and nothing else."""
    price = price_for(link)
    paid = is_paid(link)
    unit_price = price.usd
    if price.per_megapixel:
        megapixels = (width or 1080) * (height or 1920) / 1_000_000
        unit_price = price.usd * megapixels
    est = round(unit_price * qty, 4) if paid else 0.0
    return Estimate(describe(link), price.unit, qty, round(unit_price, 6), est, paid)


def price_table() -> list:
    """Every priced link, for the Settings page and the docs."""
    rows = []
    for label, price in sorted(PRICES.items()):
        rows.append({
            "link": label, "unit": price.unit, "usd": price.usd,
            "per_megapixel": price.per_megapixel, "note": price.note,
        })
    return rows
