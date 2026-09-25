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
    assert abs(est.est_usd - 0.0062) < 0.0001
    assert estimate(link("fal/flux-schnell"), 1, width=1024, height=1024).est_usd == 0.0031


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
