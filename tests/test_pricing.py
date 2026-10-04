"""The price table is dated and complete (DEC-099): every paid link of every
default chain has a unit price, every free link estimates $0.00, and an
estimate is arithmetic on the table -- never a guess."""

import re

import pytest

from clipping.providers import generation, pricing, registry
from clipping.providers.generation import DEFAULT_CHAINS, KINDS, is_paid, parse_generation_chain
from clipping.providers.pricing import PRICES_AS_OF, UNITS, PriceUnknown, estimate, price_for


def test_the_table_is_dated():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", PRICES_AS_OF)
    assert PRICES_AS_OF == "2026-09-25"


@pytest.mark.parametrize("kind", KINDS)
def test_every_paid_link_of_a_default_chain_has_a_price(kind):
    for link in parse_generation_chain(kind, DEFAULT_CHAINS[kind]):
        if is_paid(link):
            price = price_for(link)
            assert price.usd > 0, link
            assert price.unit in UNITS, link


@pytest.mark.parametrize("kind", KINDS)
def test_every_free_link_of_a_default_chain_estimates_zero(kind):
    for link in parse_generation_chain(kind, DEFAULT_CHAINS[kind]):
        if not is_paid(link):
            est = estimate(link, 5)
            assert est.est_usd == 0.0 and est.paid is False, link


def test_units_are_a_closed_set():
    assert UNITS == ("image", "second", "char", "token")
    for label, price in pricing.PRICES.items():
        assert price.unit in UNITS, label


def link(spec):
    return registry.parse_spec(spec, providers=generation.GEN_PROVIDERS)


def test_an_estimate_is_arithmetic_on_the_table():
    twenty = estimate(link("fal/seedream-4-edit"), 20)
    assert (twenty.unit, twenty.qty, twenty.price_usd, twenty.est_usd, twenty.paid) == ("image", 20, 0.03, 0.6, True)
    five_seconds = estimate(link("fal/seedance-1-pro-fast"), 5)
    assert five_seconds.unit == "second" and five_seconds.est_usd == 0.11
    lite = estimate(link("gemini/nano-banana-2-lite"), 12)
    assert lite.est_usd == 0.4032


def test_a_per_megapixel_price_uses_the_requested_size():
    est = estimate(link("fal/flux-schnell"), 1, width=1080, height=1920)
    assert est.unit == "image"
    assert est.est_usd == 0.006
    assert estimate(link("fal/flux-schnell"), 1, width=1024, height=1024).est_usd == 0.003


@pytest.mark.parametrize("width,height,usd", [
    (720, 1280, 0.003),    # an AI Story shot: 0.88 MP bills as 1
    (576, 1024, 0.003),    # a style-preview sample
    (1024, 1024, 0.003),   # exactly one megapixel
    (1025, 1024, 0.006),   # one column over bills as 2
    (1080, 1920, 0.006),   # 1.98 MP bills as 2
])
def test_fal_bills_whole_megapixels_rounded_up(width, height, usd):
    """fal: "$0.003 per megapixel. Images are billed by rounding up to the
    nearest megapixel", a megapixel being 1024x1024. The paid test of
    2026-09-29 booked $0.6934 against fal's $0.70 before this rule."""
    assert estimate(link("fal/flux-schnell"), 1, width=width, height=height).est_usd == usd
    assert estimate(link("fal/flux-schnell"), 20, width=width, height=height).est_usd == round(usd * 20, 4)


def test_a_paid_link_without_a_price_is_refused_not_guessed():
    with pytest.raises(PriceUnknown) as excinfo:
        estimate(link("fal/made-up-model"), 1)
    assert "fal/made-up-model" in str(excinfo.value)
    assert PRICES_AS_OF in str(excinfo.value)


def test_the_one_dollar_profile_fits_the_ceiling_as_the_appendix_computes_it():
    """Spec 8.5 / appendix E: 20 reference-consistent images on the cheapest
    editor plus 3 key shots animated on the cheapest video link stays under $1."""
    images = estimate(link("fal/seedream-4-edit"), 20).est_usd
    clips = estimate(link("fal/seedance-1-pro-fast"), 3 * 5).est_usd
    assert round(images + clips, 2) == 0.93
    assert images + clips <= 1.00


def test_the_extension_points_are_priced_too():
    for spec in ("gcloud/neural2", "openai/gpt-4o-mini-tts", "elevenlabs/flash"):
        assert price_for(link(spec)).usd > 0


# ================================================== LLM_PRICE_CHANGES (plan 22)

def test_gemini_38_flash_price_changes_on_2027_01_01():
    """The promo ends 2027-01-01 (plan 22 stage 1, DEC-273): the estimate and
    every booking move to the next row the day Google's own pricing page
    says they do, with no separate deploy."""
    from clipping.providers.pricing import LLM_PRICE_CHANGES, llm_price_for

    link = registry.parse_spec("gemini-paid/gemini-3.8-flash", providers=None)
    before = llm_price_for(link, today="2026-12-31")
    on_day = llm_price_for(link, today="2027-01-01")
    after = llm_price_for(link, today="2027-06-01")

    assert (before.input_usd_per_m, before.output_usd_per_m) == (0.75, 3.75)
    assert (on_day.input_usd_per_m, on_day.output_usd_per_m) == (1.50, 7.50)
    assert on_day == after
    assert LLM_PRICE_CHANGES["gemini-paid/gemini-3.8-flash"][0] == "2027-01-01"

    # A date object works the same way a string does.
    import datetime
    assert llm_price_for(link, today=datetime.date(2027, 1, 1)) == on_day

    # A link with no change row is unaffected by *today* at all.
    pro = registry.parse_spec("gemini-paid/gemini-3.1-pro-preview", providers=None)
    assert llm_price_for(pro, today="2030-01-01") == llm_price_for(pro)


def test_gemini_paid_rows_are_priced():
    from clipping.providers.pricing import llm_price_for

    flash = llm_price_for(registry.parse_spec("gemini-paid/gemini-3.8-flash", providers=None))
    pro = llm_price_for(registry.parse_spec("gemini-paid/gemini-3.1-pro-preview", providers=None))
    assert (flash.input_usd_per_m, flash.output_usd_per_m) == (0.75, 3.75)
    assert (pro.input_usd_per_m, pro.output_usd_per_m) == (2.00, 12.00)


def test_the_veo_speaking_links_are_priced_per_second_at_each_size():
    """Plan 22 (read 2026-10-04 at ai.google.dev/gemini-api/docs/pricing):
    Veo 3.1 Fast $0.10 a second at 720p and $0.12 at 1080p, Veo 3.1 $0.40 at
    both; lite keeps its one $0.05 (its body asks 720p whatever the story's
    size, RC-N1)."""
    rows = {("gemini/veo-3.1-fast", None): 0.10, ("gemini/veo-3.1-fast", "1080p"): 0.12,
            ("gemini/veo-3.1", None): 0.40, ("gemini/veo-3.1", "1080p"): 0.40,
            ("gemini/veo-3.1-lite", None): 0.05, ("gemini/veo-3.1-lite", "1080p"): 0.05}
    for (spec, resolution), usd in rows.items():
        price = price_for(link(spec), resolution)
        assert (price.unit, price.usd) == ("second", usd), (spec, resolution)
    assert estimate(link("gemini/veo-3.1-fast"), 6).est_usd == 0.6
    assert estimate(link("gemini/veo-3.1"), 8, resolution="1080p").est_usd == 3.2


def test_ltx25_price_ladder():
    """Plan 23 stage C1 (A-151; third-party listings read 2026-10-04, the highest kept): $0.09 a second
    at 720p (the row), $0.16 at 1080p (``@1080p``), any other size falls back to the 720p row."""
    ltx = "fal/ltx-2.5-fast"
    assert pricing.price_key(link(ltx)) == ltx
    assert pricing.price_key(link(ltx), "720p") == ltx
    assert pricing.price_key(link(ltx), "1080p") == f"{ltx}@1080p"
    assert pricing.price_key(link(ltx), "1440p") == ltx
    assert (price_for(link(ltx)).unit, price_for(link(ltx)).usd) == ("second", 0.09)
    assert price_for(link(ltx), "1080p").usd == 0.16
    assert price_for(link(ltx), "1440p").usd == 0.09
    assert estimate(link(ltx), 6).est_usd == pytest.approx(0.54)
    assert estimate(link(ltx), 6, resolution="1080p").est_usd == pytest.approx(0.96)
    assert "A-151" in pricing.PRICES[ltx].note and "2026-10-04" in pricing.PRICES[ltx].note
