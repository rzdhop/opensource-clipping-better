"""AI Story phase 7 stage 7 (log finding): the estimate sentences must be
true. The cast, places and image-regenerate estimates said their LLM calls
had "no LLM price table" -- DEC-224 added one (``pricing.LLM_PRICES``,
OpenRouter mistral-medium-3.1) -- and the dashboard's EstimateChip comment
said the same. A paid LLM link the story chain can fall through to is now
priced in words, "up to $Z if the free links fail", from the table
(``llm_spend.worst_call_usd``: the widest prompt and reply cap at its
price); est_usd keeps counting what it always counted.

The route tests need fastapi and skip without it; the text contracts run in
CI (DEC-012).
"""

from __future__ import annotations

import pathlib

import pytest

from clipping.aistory.steps import llm_spend
from clipping.aistory.store import StoryStore
from clipping.providers import registry

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUTES = ROOT / "web" / "api" / "routes" / "stories.py"
ESTIMATE_CHIP = ROOT / "web" / "dashboard" / "src" / "components" / "EstimateChip.jsx"

NOW = "2026-10-02T10:00:00+00:00"
PAID = "openrouter/mistralai/mistral-medium-3.1"
# Test values only: nothing is called.
FREE_ONLY = {"LLM_CHAIN": "gemini/gemini-test", "GOOGLE_API_KEY": "test-gemini-key"}
FREE_THEN_PAID = {"LLM_CHAIN": f"gemini/gemini-test,{PAID}", "GOOGLE_API_KEY": "test-gemini-key",
                  "OPENROUTER_API_KEY": "test-openrouter-key", "ALLOW_PAID": "1"}
PAID_ONLY = {"LLM_CHAIN": PAID, "OPENROUTER_API_KEY": "test-openrouter-key", "ALLOW_PAID": "1"}


def _worst(calls):
    return calls * llm_spend.worst_call_usd(registry.parse_chain(PAID)[0])


def test_neither_the_routes_nor_the_estimate_chip_say_there_is_no_llm_price_table():
    assert "no LLM price table" not in ROUTES.read_text(encoding="utf-8")
    chip = ESTIMATE_CHIP.read_text(encoding="utf-8")
    assert "no LLM price table" not in chip
    assert "LLM_PRICES" in chip


@pytest.fixture
def routes(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env
    from web.api.routes import stories as stories_route

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in ("LLM_CHAIN", "STORY_LLM_CHAIN", "ALLOW_PAID", "ALLOW_SLOW_CHAIN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    return stories_route


def _generation(routes, tmp_path, env, calls=2):
    stories = StoryStore(str(tmp_path / "outputs"))
    story = stories.create(language="fr", now=NOW)
    units = {"llm_calls": calls, "images": 0, "edit_images": 0, "tts_chars": 0}
    return routes._generation_estimate(stories, story, "cast", units, env=env)


def test_a_generation_estimates_llm_calls_are_named_on_their_link_and_their_price(routes, tmp_path):
    free = _generation(routes, tmp_path, FREE_ONLY)
    assert free["message"] == "2 LLM calls on gemini/gemini-test (free tier): $0."
    assert free["est_usd"] == 0.0 and free["ready"] is True

    fallthrough = _generation(routes, tmp_path, FREE_THEN_PAID)
    assert fallthrough["message"].startswith("2 LLM calls on gemini/gemini-test (free tier): $0, ")
    assert f"up to ${_worst(2):.4f} if the free links fail ({PAID}, billed; not in est_usd)." in fallthrough["message"]
    assert fallthrough["est_usd"] == 0.0

    paid = _generation(routes, tmp_path, PAID_ONLY)
    assert paid["message"] == f"2 LLM calls on {PAID} (billed): up to ${_worst(2):.4f}, not in est_usd."
    assert paid["est_usd"] == 0.0


def test_an_llm_steps_estimate_prices_the_paid_link_it_can_fall_through_to(routes):
    body = routes._llm_estimate("concepts", 10, env=FREE_THEN_PAID)

    assert (body["route_class"], body["link"], body["est_usd"]) == ("free", "gemini/gemini-test", 0.0)
    assert body["message"] == (
        f"10 LLM calls on gemini/gemini-test (free tier). Up to ${_worst(10):.4f} if the free links fail "
        f"and {PAID} (billed) answers; est_usd counts the first link only, so it stays 0.0.")
