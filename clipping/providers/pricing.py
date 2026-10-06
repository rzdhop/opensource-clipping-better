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

import math
import re
from collections import namedtuple
from datetime import date

from .generation import is_paid
from .registry import PROVIDERS, describe, split_effort

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
    # DEC-312: the local image templates on RunPod Serverless (runpod_images.py), billed by the GPU second and
    # priced here per image like every image link. Not measured yet: a FLUX.2 klein 4B image at 832x1216 is a
    # few seconds warm on an RTX 5090 ($1.58/h = $0.00044/s, so under a cent), and the first image after an
    # idle pays a 1-3 min cold start (about $0.05) that the batch shares. The figures keep a share of that
    # cold start in (an estimate checked against a cap is never low); what RunPod really billed is logged per
    # image from executionTime + delayTime.
    "runpod/t2i_flux2_klein": Price("image", 0.01, "FLUX.2 klein 4B text to image on a RunPod worker: seconds warm, a cold start shared by the batch; not measured, the highest plausible figure is kept"),
    "runpod/edit_flux2_klein_multiref": Price("image", 0.015, "FLUX.2 klein 4B edit with up to four references on a RunPod worker: about 1.5x the text-to-image figure (the references are encoded); not measured"),
    "runpod/edit_qwen_image": Price("image", 0.03, "Qwen-Image-Edit on a RunPod worker: a 20B model, about 3x the klein figure; not measured"),
    # --- video, per second of output (appendix B). Re-read on each model's own
    # page on 2026-09-30: no price had moved; the notes carry what was learned.
    # The 1080p clip of the same link (phase 7 stage 4, DEC-227: a v2 story's
    # per-story switch); read by price_key when a request asks 1080p.
    "fal/seedance-1-pro-fast@1080p": Price("second", 0.0486, "1080x1920 at 24 fps is 48,600 tokens a second at $1.00 per million; read 2026-10-01 on fal.ai (A-100)"),
    "fal/seedance-1-pro-fast": Price("second", 0.022, "token-billed, $1.00 per million tokens, tokens = width x height x 24 fps x seconds / 1024: 720x1280 is 21,600 tokens, $0.0216, a second (rounded up); 1080p, the endpoint's default, is $0.0486 a second; no audio"),
    "fal/ltx-2-fast": Price("second", 0.04, "1080p, its smallest size, audio included; its output is locked to 16:9; its fal-ai/ltx-2 twin was deprecated on 2026-08-15 for LTX-2.3 fast ($0.06 a second at 1080p, with 9:16)"),
    "fal/ltx-2.3-fast": Price("second", 0.06, "fal-ai/ltx-2.3/image-to-video/fast, read on its fal page and schema on 2026-09-30: $0.06 a second at 1080p, its smallest size (9:16 is 1080x1920), $0.12 at 1440p, $0.24 at 2160p; audio not priced apart; a summary block on the same page says $0.04 at 1080p, the higher 'your request will cost' line is kept"),
    # LTX-2.5 fast (plan 23 stage C1, A-151): read on 2026-10-04 from third-party listings (segmind,
    # aireiter); fal's own page shows a "$0 per compute second" placeholder. Listings gave $0.13-0.16 a
    # second at 1080p: the highest, $0.16, is kept (an estimate checked against a cap is never low).
    # Every size above 1080p is refused by the adapter, so no other row exists.
    "fal/ltx-2.5-fast@1080p": Price("second", 0.16, "fal-ai/ltx-2.5/image-to-video/fast at 1080p, audio included; third-party listings (segmind, aireiter) read on 2026-10-04 say $0.13-0.16, the highest is kept; fal's page shows a \"$0 per compute second\" placeholder (A-151)"),
    "fal/ltx-2.5-fast": Price("second", 0.09, "fal-ai/ltx-2.5/image-to-video/fast at 720p, audio included; read on 2026-10-04 from third-party listings (segmind, aireiter), fal's page shows a \"$0 per compute second\" placeholder, the highest figure seen is kept (an estimate checked against a cap is never low); 6 to 20 s, 6 s the shortest (A-151)"),
    "fal/kling-2.5-turbo-std": Price("second", 0.042, "$0.21 per 5 s, $0.042 per extra second; 5 or 10 s; no audio"),
    # --- DEC-310: the local workflow templates on RunPod Serverless, billed by the GPU second, priced here
    # per second of OUTPUT like every video link. Measured 2026-10-06 on the author's endpoint: a 720x1280 x 81-
    # frame Wan 2.2 14B Lightning clip took 186 GPU-s warm on an L40S at $1.75/h ($0.090, $0.018 a second) and
    # 300-340 GPU-s cold (the 35 GB of weights read from the volume count); the templates' 480x832 default is
    # about 2.5x fewer pixels. The highest figure seen is kept (an estimate checked against a cap is never
    # low); what RunPod really billed is logged per clip from executionTime + delayTime (A-194).
    "runpod/i2v_wan22_14b_lightning": Price("second", 0.02, "Wan 2.2 14B fp8 + Lightning 4-step on a RunPod L40S/RTX 5090 worker: $0.018 a second measured warm at 720p on 2026-10-06, 480p default; the first clip after an idle also pays the cold start"),
    "runpod/i2v_wan22_5b": Price("second", 0.012, "Wan 2.2 5B ti2v on a RunPod worker: not measured; about 0.6x the 14B Lightning figure (one 5B pass against two 14B passes at 4 steps); the highest plausible figure is kept"),
    "runpod/i2v_ltx2": Price("second", 0.03, "LTX-2 fp8 distilled on a RunPod worker: not measured; about 1.5x the 14B Lightning figure (a 22B model at 25 fps); the highest plausible figure is kept"),
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
    # Plan 23 stage B3: read 2026-10-04 at elevenlabs.io/pricing/api (the API tab): Flash/Turbo $0.04 and
    # Multilingual v2 (and v3) $0.08 per 1,000 characters; the page's v4 promotion ("72% off until Oct 12")
    # is not used, and the flat per-character rate is what an estimate checked against a cap needs.
    "elevenlabs/flash": Price("char", 0.00004, "eleven_flash_v2_5: $0.04 per 1k characters; read 2026-10-04 at elevenlabs.io/pricing/api"),
    "elevenlabs/multilingual-v2": Price("char", 0.00008, "eleven_multilingual_v2: $0.08 per 1k characters; read 2026-10-04 at elevenlabs.io/pricing/api"),
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

# ``cache_read_usd_per_m`` / ``cache_write_usd_per_m`` (plan 23 stage D1):
# what a prompt-cache read and a cache write cost per M tokens, for a
# provider that reports them (Anthropic's ``cache_read_input_tokens`` /
# ``cache_creation_input_tokens``). Trailing and defaulted to None -- "the
# input price" -- so every row written before them is unchanged.
LlmPrice = namedtuple("LlmPrice", "input_usd_per_m output_usd_per_m note cache_read_usd_per_m cache_write_usd_per_m",
                      defaults=(None, None))

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
    # Claude as a writer (plan 23 stage D1): the two premium links, then
    # every model the server-side fallback ("default" mode) may plausibly
    # serve a declined request on, so a fallback reply is booked at the
    # SERVED model's own row (``llm_spend``). Base prices read 2026-10-04 in
    # the claude-api reference's model table (cached 2026-09-25; Anthropic
    # first-party API rates). Cache reads are 0.1x input (0.05x on Opus 5.5:
    # $0.20) and 5-minute cache writes 1.25x input, as the same reference
    # states for prompt caching.
    "anthropic/claude-sonnet-5-5": LlmPrice(2.00, 10.00, "the premium writer's default; read 2026-10-04 "
                                            "(claude-api reference)", 0.20, 2.50),
    "anthropic/claude-opus-5-5": LlmPrice(4.00, 20.00, "the premium writer by name; read 2026-10-04 "
                                          "(claude-api reference)", 0.20, 5.00),
    "anthropic/claude-opus-5": LlmPrice(5.00, 25.00, "a server-side fallback target of Opus 5.5; read "
                                        "2026-10-04 (claude-api reference)", 0.50, 6.25),
    "anthropic/claude-opus-4-8": LlmPrice(5.00, 25.00, "a server-side fallback target of Opus 5.5; read "
                                          "2026-10-04 (claude-api reference)", 0.50, 6.25),
    "anthropic/claude-sonnet-5": LlmPrice(2.00, 10.00, "the server-side fallback target of Sonnet 5.5; read "
                                          "2026-10-04 (claude-api reference)", 0.20, 2.50),
    "anthropic/claude-haiku-4-5": LlmPrice(1.00, 5.00, "priced in case a fallback serves on it; read "
                                           "2026-10-04 (claude-api reference)", 0.10, 1.25),
}

# The rows a request to each Anthropic model may be billed at: the model
# itself and the models its server-side fallback may serve a declined request
# on (Sonnet 5.5 falls back to Sonnet 5; Opus 5.5 is expected to fall back to
# Opus 5 / Opus 4.8 -- claude-api reference, 2026-10-04). The pre-call
# estimate prices a request at the DEAREST of these rows, and a reply served
# by a model with no row is booked at it with a flagged note: never low.
ANTHROPIC_FALLBACK_FAMILIES = {
    "claude-sonnet-5-5": ("claude-sonnet-5-5", "claude-sonnet-5"),
    "claude-opus-5-5": ("claude-opus-5-5", "claude-opus-5", "claude-opus-4-8"),
}
# Claude's tokenizer counts French well above ``pacing.estimate_tokens``'s
# chars/4 (the claude-api reference: the newer tokenizer uses up to ~1.35x as
# many tokens), so the estimate scales its input count by this.
CLAUDE_TOKENS_FACTOR = 1.35

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


def _llm_label(link) -> str:
    """*link*'s price-table key: an Anthropic link's ``@effort`` suffix is not
    part of it (``registry.split_effort``)."""
    model, _effort = split_effort(link)
    return f"{link.provider}/{model}"


def llm_price_for(link, today=None) -> LlmPrice:
    """The :class:`LlmPrice` of the LLM *link*; :class:`PriceUnknown` without a
    row. *today* (an ISO ``"YYYY-MM-DD"`` string or a :class:`datetime.date`;
    the real date by default) picks the later row of :data:`LLM_PRICE_CHANGES`
    once it is reached -- the estimate and every booking read the same clock."""
    label = _llm_label(link)
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


def llm_cost_cached(link, tokens_in, tokens_out, *, cache_read=0, cache_write=0, today=None,
                    price=None) -> float:
    """What a reply with *tokens_in* uncached prompt tokens, *cache_read* and
    *cache_write* prompt-cache tokens and *tokens_out* completion tokens (the
    model's thinking included) costs, unrounded: each at its own rate of
    *price* (the link's row by default, :func:`llm_price_for`), a cache rate
    the row leaves None at the input price."""
    price = price or llm_price_for(link, today)
    read = price.input_usd_per_m if price.cache_read_usd_per_m is None else price.cache_read_usd_per_m
    write = price.input_usd_per_m if price.cache_write_usd_per_m is None else price.cache_write_usd_per_m
    return (tokens_in * price.input_usd_per_m + cache_read * read + cache_write * write
            + tokens_out * price.output_usd_per_m) / 1_000_000


def _is_anthropic(link) -> bool:
    return getattr(PROVIDERS.get(link.provider), "api", "openai") == "anthropic"


def dearest_family_price(link, today=None) -> LlmPrice:
    """The dearest rate of every row an Anthropic *link* may be billed at
    (:data:`ANTHROPIC_FALLBACK_FAMILIES`; the link's own row alone for a
    model with no family), each rate taken separately. :class:`PriceUnknown`
    when the link's own row is missing."""
    model, _effort = split_effort(link)
    rows = [llm_price_for(link, today)]
    for member in ANTHROPIC_FALLBACK_FAMILIES.get(model, ()):
        price = LLM_PRICES.get(f"{link.provider}/{member}")
        if price is not None:
            rows.append(price)

    def rate(price, name):
        value = getattr(price, name)
        return price.input_usd_per_m if value is None else value

    return LlmPrice(
        max(p.input_usd_per_m for p in rows), max(p.output_usd_per_m for p in rows),
        f"the dearest row of {model}'s fallback family",
        max(rate(p, "cache_read_usd_per_m") for p in rows), max(rate(p, "cache_write_usd_per_m") for p in rows),
    )


def served_price(link, served_model, today=None):
    """``(price, note)`` for a reply to *link* that *served_model* produced: the
    served model's own row and None, or -- for a model with no row -- the
    dearest row of the requested model's family and a note saying so."""
    model, _effort = split_effort(link)
    # A dated snapshot id of the requested model itself ("<model>-YYYYMMDD")
    # is the requested model, not a fallback.
    if served_model and re.fullmatch(re.escape(model) + r"-\d{8}", str(served_model)):
        served_model = model
    if served_model and served_model != model:
        price = LLM_PRICES.get(f"{link.provider}/{served_model}")
        if price is None:
            return dearest_family_price(link, today), (
                f"FLAGGED: served by {served_model}, which has no price row; booked at the dearest row of "
                f"{model}'s fallback family")
        return llm_price_for(type(link)(link.provider, served_model), today), None
    return llm_price_for(link, today), None


def llm_estimate_cost(link, tokens_in, tokens_out, *, today=None) -> float:
    """The pre-call estimate of one request to *link*: :func:`llm_cost` for
    every link but an Anthropic one, which is priced never low -- its input at
    the cache-WRITE rate (the dearer of the two a first request pays), scaled
    by :data:`CLAUDE_TOKENS_FACTOR`, and both halves at the dearest row of
    its fallback family (:func:`dearest_family_price`)."""
    if not _is_anthropic(link):
        return llm_cost(link, tokens_in, tokens_out, today=today)
    price = dearest_family_price(link, today)
    write = max(price.input_usd_per_m, price.cache_write_usd_per_m or 0.0)
    scaled_in = math.ceil(tokens_in * CLAUDE_TOKENS_FACTOR)
    return (scaled_in * write + tokens_out * price.output_usd_per_m) / 1_000_000


def price_table() -> list:
    """Every priced link, for the Settings page and the docs."""
    rows = []
    for label, price in sorted(PRICES.items()):
        rows.append({
            "link": label, "unit": price.unit, "usd": price.usd,
            "per_megapixel": price.per_megapixel, "note": price.note,
        })
    return rows
