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
from datetime import date

from .generation import is_paid
from .registry import describe

PRICES_AS_OF = "2026-09-25"

UNITS = ("image", "second", "char", "token")

# ``usd`` is the price of ONE unit. ``per_megapixel`` says the price is per
# megapixel of output and scales with the requested size: whole megapixels of
# 1024x1024, rounded up, as fal bills them ("billed by rounding up to the
# nearest megapixel") -- a 720x1280 shot costs one full megapixel.
MEGAPIXEL = 1024 * 1024
Price = namedtuple("Price", "unit usd note per_megapixel", defaults=("", False))

PRICES = {
    # --- images (appendix A, spec 8.7)
    "cloudflare/flux-1-schnell": Price("image", 0.0, "free: 10,000 neurons/day, about 170 images; $0.0006/image beyond, on a paid Workers plan"),
    "pollinations/flux": Price("image", 0.0, "pollen credits; keyless at the legacy rate"),
    "gemini/nano-banana-2-lite": Price("image", 0.0336, "gemini-3.1-flash-lite-image at 1K; batch $0.0168; not on the free tier"),
    "gemini/nano-banana-2": Price("image", 0.067, "gemini-3.1-flash-image at 1K; not on the free tier"),
    "fal/flux-schnell": Price("image", 0.003, "$0.003 per megapixel, rounded up: $0.003 at 720x1280, $0.006 at 1080x1920", per_megapixel=True),
    "fal/seedream-4-edit": Price("image", 0.03, "multi-reference edit"),
    "fal/seedream-4.5": Price("image", 0.04, "text-to-image, seed honoured, no negative_prompt; read 2026-10-01 on fal.ai (DEC-235)"),
    "fal/seedream-4.5-edit": Price("image", 0.04, "multi-reference edit, up to 10 references, seed honoured; read 2026-10-01 on fal.ai (A-111)"),
    "fal/flux-kontext-pro": Price("image", 0.04, "single-reference edit"),
    "openai/gpt-image-2-low": Price("image", 0.005, "quality low, 1024x1536"),
    # --- video, per second of output (appendix B). Re-read on each model's own
    # page on 2026-09-30: no price had moved; the notes carry what was learned.
    # The 1080p clip of the same link (phase 7 stage 4, DEC-227: a v2 story's
    # per-story switch); read by price_key when a request asks 1080p.
    "fal/seedance-1-pro-fast@1080p": Price("second", 0.0486, "1080x1920 at 24 fps is 48,600 tokens a second at $1.00 per million; read 2026-10-01 on fal.ai (A-100)"),
    "fal/seedance-1-pro-fast": Price("second", 0.022, "token-billed, $1.00 per million tokens, tokens = width x height x 24 fps x seconds / 1024: 720x1280 is 21,600 tokens, $0.0216, a second (rounded up); 1080p, the endpoint's default, is $0.0486 a second; no audio"),
    "fal/ltx-2-fast": Price("second", 0.04, "1080p, its smallest size, audio included; its output is locked to 16:9; its fal-ai/ltx-2 twin was deprecated on 2026-08-15 for LTX-2.3 fast ($0.06 a second at 1080p, with 9:16)"),
    "fal/ltx-2.3-fast": Price("second", 0.06, "fal-ai/ltx-2.3/image-to-video/fast, read on its fal page and schema on 2026-09-30: $0.06 a second at 1080p, its smallest size (9:16 is 1080x1920), $0.12 at 1440p, $0.24 at 2160p; audio not priced apart; a summary block on the same page says $0.04 at 1080p, the higher 'your request will cost' line is kept"),
    "fal/kling-2.5-turbo-std": Price("second", 0.042, "$0.21 per 5 s, $0.042 per extra second; 5 or 10 s; no audio"),
    # --- the lipsync post-process of a made clip (DEC-258), per second of
    # input video; the adapter's estimate rounds the clip up to 5 s.
    "fal/kling-lipsync": Price("second", 0.0028, "fal-ai/kling-video/lipsync/audio-to-video: $0.014 per 5 s of input video, rounded up to 5 s; video 2-10 s at 720-1920 px, audio 2-60 s and at most 5 MB; read 2026-10-03"),
    "gemini/veo-3.1-lite": Price("second", 0.05, "veo-3.1-lite-generate-preview at 720p ($0.20 per 4 s), audio always on and included; $0.08 a second at 1080p (8 s only); no free tier"),
    # Plan 22: the speaking links (read 2026-10-04 at https://ai.google.dev/gemini-api/docs/pricing).
    # Lite keeps its one price: its body asks 720p whatever the story's size (RC-N1).
    "gemini/veo-3.1-fast": Price("second", 0.10, "veo-3.1-fast-generate-preview at 720p, audio always on and included; $0.12 a second at 1080p (8 s only); no free tier; read 2026-10-04 at ai.google.dev/gemini-api/docs/pricing"),
    "gemini/veo-3.1-fast@1080p": Price("second", 0.12, "veo-3.1-fast-generate-preview at 1080p (8 s only), audio included; read 2026-10-04 at ai.google.dev/gemini-api/docs/pricing"),
    "gemini/veo-3.1": Price("second", 0.40, "veo-3.1-generate-preview at 720p or 1080p, audio always on and included; no free tier; read 2026-10-04 at ai.google.dev/gemini-api/docs/pricing"),
    "gemini/veo-3.1@1080p": Price("second", 0.40, "veo-3.1-generate-preview at 1080p (8 s only), the 720p price; read 2026-10-04 at ai.google.dev/gemini-api/docs/pricing"),
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
    # Plan 22 stage 5: the human's own clips and images, uploaded (a clip is
    # counted in seconds like every video link; nothing is ever charged).
    "manual": Price("second", 0.0, "your own clips and images, uploaded: no call, no charge"),
}

Estimate = namedtuple("Estimate", "link unit qty price_usd est_usd paid")

# --- LLM links, per million tokens (AI Story phase 6, stage 5; DEC-115's
# follow-up). Only the PAID links a story step can reach by default: the
# default chain's OpenRouter model and the model DEC-089 falls back to on the
# same key (``registry.PROVIDERS["openrouter"].fallback_models``); every other
# default link is free (``llm_call.is_free_link``). A paid link with no row is
# refused before any call, never guessed.
#
# OpenRouter serves one model from several hosts at different prices and
# picks the host per request, so each row holds the DEAREST host's price read
# on the model's endpoints page: an estimate checked against a cap must never
# be low. What a reply is booked at is its own ``usage.cost`` whenever it
# carries one (OpenRouter returns it with every response); this table prices
# the estimate, and a reply without it.
LLM_PRICES_AS_OF = "2026-09-30"

LlmPrice = namedtuple("LlmPrice", "input_usd_per_m output_usd_per_m note")

LLM_PRICES = {
    "openrouter/mistralai/mistral-small-3.2-24b-instruct": LlmPrice(0.10, 0.30, "the dearest of 4 hosts (Mistral's own), read on 2026-09-30 at https://openrouter.ai/api/v1/models/mistralai/mistral-small-3.2-24b-instruct/endpoints; the model's listed price at https://openrouter.ai/api/v1/models is $0.09375 in / $0.25 out, DeepInfra $0.075 / $0.20 the cheapest"),
    "openrouter/meta-llama/llama-3.3-70b-instruct": LlmPrice(1.04, 1.04, "the dearest of 11 hosts (Together), read on 2026-09-30 at https://openrouter.ai/api/v1/models/meta-llama/llama-3.3-70b-instruct/endpoints; the model's listed price at https://openrouter.ai/api/v1/models is $0.10 in / $0.32 out (DeepInfra, the cheapest); DEC-089's fallback, reached only when the default model is unavailable"),
    # AI Story's own chain (phase 7 stage 2b, DEC-224, A-115): the dearest
    # host, read 2026-10-01 at https://openrouter.ai/api/v1/models/mistralai/mistral-medium-3.1/endpoints
    # ($0.44 in / $2.20 out); the cheapest host on the same page lists
    # $0.40 / $2.00. DEC-206's metering books whatever ``usage.cost`` a reply
    # actually carries; this row only prices the pre-call estimate, and a
    # reply without one, and is never contacted while allow_paid is off
    # (DEC-115, story_chain/is_free_link).
    "openrouter/mistralai/mistral-medium-3.1": LlmPrice(0.44, 2.20, "the dearest host, read 2026-10-01 at https://openrouter.ai/api/v1/models/mistralai/mistral-medium-3.1/endpoints; the cheapest host lists $0.40 in / $2.00 out (A-115)"),
    # The premium writing chain (plan 22 stage 1, DEC-273, A-145): the paid
    # Gemini project. Read 2026-10-04 at ai.google.dev/gemini-api/docs/pricing
    # -- re-read that page before relying on either row past its own date.
    "gemini-paid/gemini-3.8-flash": LlmPrice(0.75, 3.75, "promo pricing through 2026-12-31; $1.50 / $7.50 from "
                                             "2027-01-01 (LLM_PRICE_CHANGES); read 2026-10-04 at "
                                             "ai.google.dev/gemini-api/docs/pricing"),
    "gemini-paid/gemini-3.1-pro-preview": LlmPrice(2.00, 12.00, "priced and parseable; never a default link "
                                          "(the per-story premium switch, stage 4); read 2026-10-04 at "
                                          "ai.google.dev/gemini-api/docs/pricing"),
}

# Dated replacements of an LLM_PRICES row (plan 22 stage 1): the promo price
# above ends 2026-12-31, and Google's own pricing page already states the
# row that replaces it on 2027-01-01. ``llm_price_for`` returns the later row
# once *today* reaches its date, so the estimate and every booking move
# together the day the promo ends, with no separate deploy to flip them.
LLM_PRICE_CHANGES = {
    "gemini-paid/gemini-3.8-flash": (
        "2027-01-01",
        LlmPrice(1.50, 7.50, "the promo above ends; read 2026-10-04 at ai.google.dev/gemini-api/docs/pricing"),
    ),
}


class PriceUnknown(LookupError):
    """A paid link has no price in the table. Add it; never guess."""


# The clip size every video price row without a suffix is for.
DEFAULT_RESOLUTION = "720p"


def price_key(link, resolution=None) -> str:
    """The :data:`PRICES` row of *link* at *resolution* (phase 7 stage 4):
    ``"<link>@<resolution>"`` when the request asks a size other than
    :data:`DEFAULT_RESOLUTION` and the table has a row for it, else the
    link's own row -- so every 720p estimate reads the row it always read,
    and a link that ignores the size keeps its one price."""
    label = describe(link)
    if resolution and resolution != DEFAULT_RESOLUTION:
        keyed = f"{label}@{resolution}"
        if keyed in PRICES:
            return keyed
    return label


def price_for(link, resolution=None) -> Price:
    """The :class:`Price` of *link* (at *resolution*, :func:`price_key`):
    exact entry, else the provider's free price."""
    label = describe(link)
    price = PRICES.get(price_key(link, resolution))
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


def estimate(link, qty=1, *, width=None, height=None, resolution=None) -> Estimate:
    """What *qty* units on *link* would cost, from the table and nothing else
    (a clip at *resolution*: :func:`price_key`)."""
    price = price_for(link, resolution)
    paid = is_paid(link)
    unit_price = price.usd
    if price.per_megapixel:
        pixels = (width or 1080) * (height or 1920)
        megapixels = -(-pixels // MEGAPIXEL)  # ceiling, in integers
        unit_price = price.usd * megapixels
    est = round(unit_price * qty, 4) if paid else 0.0
    return Estimate(describe(link), price.unit, qty, round(unit_price, 6), est, paid)


def llm_price_for(link, today=None) -> LlmPrice:
    """The :class:`LlmPrice` of the LLM *link*; :class:`PriceUnknown` without a
    row. *today* (an ISO ``"YYYY-MM-DD"`` string or a :class:`datetime.date`;
    the real date by default) picks the later row of :data:`LLM_PRICE_CHANGES`
    once it is reached -- the estimate and every booking read the same clock."""
    label = describe(link)
    price = LLM_PRICES.get(label)
    if price is None:
        raise PriceUnknown(
            f"No price for {label} in the LLM price table dated {LLM_PRICES_AS_OF} "
            f"(clipping/providers/pricing.py, LLM_PRICES). Add it before calling a paid link."
        )
    change = LLM_PRICE_CHANGES.get(label)
    if change is not None:
        effective, later = change
        when = str(today) if today is not None else date.today().isoformat()
        if when >= effective:
            return later
    return price


def llm_cost(link, tokens_in, tokens_out, *, today=None) -> float:
    """What *tokens_in* prompt and *tokens_out* completion tokens on *link*
    cost, unrounded, at the price in effect on *today* (:func:`llm_price_for`)."""
    price = llm_price_for(link, today)
    return (tokens_in * price.input_usd_per_m + tokens_out * price.output_usd_per_m) / 1_000_000


def price_table() -> list:
    """Every priced link, for the Settings page and the docs."""
    rows = []
    for label, price in sorted(PRICES.items()):
        rows.append({
            "link": label, "unit": price.unit, "usd": price.usd,
            "per_megapixel": price.per_megapixel, "note": price.note,
        })
    return rows
