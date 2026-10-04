"""Claude links are priced, capped and booked like every paid link (plan 23
stage D1): cache reads and writes at their own rates, a refusal at its real
usage, a server-side fallback at the model that SERVED it, and a pre-call
estimate that is never below what the worst call can cost.

The real ``llm.run_chain`` and ``llm_spend.Meter`` run; only the Anthropic
SDK client (``anthropic_llm._sdk_client``) and the free link's OpenAI client
are fakes. No network; the keys are test values that never leave the process.
The story is a real ``StoryStore`` under ``tmp_path``; today's spend is a
``spend.json`` there too. Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import json
import math
import os
from types import SimpleNamespace

import pytest

from clipping.aistory import prompts, steps
from clipping.aistory.ledger import CostLedger
from clipping.aistory.steps import llm_call, llm_spend
from clipping.aistory.store import StoryStore
from clipping.cancel import CancelToken
from clipping.providers import anthropic_llm, budget, llm, pacing, pricing, registry
from clipping.providers.registry import Link, describe

NOW = "2026-10-04T10:00:00+00:00"
SONNET = Link("anthropic", "claude-sonnet-5-5")
OPUS = Link("anthropic", "claude-opus-5-5")
FREE = Link("gemini", "gemini-test")
KEYS = {"ANTHROPIC_API_KEY": "test-anthropic-key", "GOOGLE_API_KEY": "test-gemini-key"}
SYSTEM = "Tu écris la bible de l'histoire."
USER = "Écris la bible de l'histoire. " * 20
VALID = json.dumps({"ok": True})


def TIME_FN():  # noqa: N802 -- a constant clock
    return 1000.0


class Log(list):
    def __call__(self, line):
        self.append(str(line))


def claude_usage(input_tokens=100, read=0, write=0, output=300, thinking=None, iterations=None):
    details = SimpleNamespace(thinking_tokens=thinking) if thinking is not None else None
    return SimpleNamespace(input_tokens=input_tokens, cache_read_input_tokens=read, cache_creation_input_tokens=write,
                           output_tokens=output, output_tokens_details=details, iterations=iterations)


def claude_message(text=VALID, *, stop="end_turn", model="claude-sonnet-5-5", usage=None, content=None):
    blocks = content if content is not None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=blocks, stop_reason=stop, stop_details=None, model=model,
                           usage=usage or claude_usage())


class Fakes:
    """The Anthropic SDK client and the free link's OpenAI client, each
    answering from its own queue and recording what it is sent."""

    def __init__(self):
        self.claude = []
        self.claude_sent = []
        self.free = []
        self.free_sent = []

    def sdk(self, **_kwargs):
        def create(**request):
            self.claude_sent.append(request)
            return self.claude.pop(0)

        return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)),
                               models=SimpleNamespace(retrieve=lambda model_id: SimpleNamespace(id=model_id)))

    def openai(self, link, *, api_key, timeout):
        def create(**body):
            self.free_sent.append(describe(link))
            content = self.free.pop(0)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                                   usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15))

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in ("LLM_CHAIN", "STORY_LLM_CHAIN", "STORY_LLM_PREMIUM_CHAIN", *budget.ENV_NAMES):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "spend.json"))
    budget.reset()
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    yield
    budget.reset()
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()


@pytest.fixture
def story(tmp_path, monkeypatch):
    store = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = store.create(language="fr", seed_text=None, style_template_id="fruit_drama", now=NOW)["story_id"]
    fakes = Fakes()
    real_build = llm.build_client

    def build(link, *, api_key, timeout, **extra):
        if registry.PROVIDERS[link.provider].api == "anthropic":
            return real_build(link, api_key=api_key, timeout=timeout, **extra)
        return fakes.openai(link, api_key=api_key, timeout=timeout)

    monkeypatch.setattr(llm, "build_client", build)
    monkeypatch.setattr(anthropic_llm, "_sdk_client", fakes.sdk)
    ledger_path = os.path.join(store.story_dir(story_id), "cost_ledger.json")

    def ctx(*links, ep=None, **extra):
        spec = ",".join(describe(link) for link in links)
        log = Log()
        settings = {"STORY_LLM_PREMIUM_CHAIN": spec, "LLM_CHAIN": spec, "ALLOW_PAID": "1", **KEYS, **extra}
        return steps.StepContext(
            job_id="job000000001", story_id=story_id, step="bible", ep=ep, params={}, cancel=CancelToken(),
            settings_env=settings, outputs_dir=str(tmp_path), on_log=log,
        ), log

    return SimpleNamespace(ctx=ctx, fakes=fakes, ledger=CostLedger(ledger_path), ledger_path=ledger_path)


def _call(ctx, prompt_id="B1"):
    return llm_call.call_json(ctx, prompt_id, SYSTEM, USER, {}, validator=lambda value: [], time_fn=TIME_FN)


def _up(usd):
    return math.ceil(round(usd * 10_000, 6)) / 10_000


# ================================================================ booking

def test_cache_reads_and_writes_are_booked_at_their_rates(story):
    story.fakes.claude.append(claude_message(usage=claude_usage(input_tokens=100, read=2000, write=500,
                                                                output=300, thinking=180)))
    ctx, _log = story.ctx(SONNET, ep=1)

    assert _call(ctx) == {"ok": True}

    [row] = story.ledger.entries()
    assert (row["provider"], row["model"], row["unit"], row["paid"], row["ep"]) == (
        "anthropic", "claude-sonnet-5-5", "token", True, 1)
    assert row["qty"] == 100 + 2000 + 500 + 300
    usd = (100 * 2.00 + 2000 * 0.20 + 500 * 2.50 + 300 * 10.00) / 1e6
    assert row["est_usd"] == _up(usd)
    assert "prompt cache: 2000 read, 500 written" in row["note"]
    # Thinking is inside the output count: noted, never booked twice.
    assert "180 of the output tokens were thinking" in row["note"]
    assert budget.day_spent() == row["est_usd"]


def test_the_prompts_effort_and_its_thinking_room_reach_the_request(story):
    """B1's family effort is medium (prompts.ANTHROPIC_EFFORT); the cap gains
    medium's room; a link's own @effort outranks the family's."""
    story.fakes.claude.extend([claude_message(), claude_message(model="claude-opus-5-5")])
    ctx, _log = story.ctx(SONNET)
    _call(ctx)
    ctx, _log = story.ctx(Link("anthropic", "claude-opus-5-5@xhigh"))
    _call(ctx)

    first, second = story.fakes.claude_sent
    assert first["output_config"] == {"effort": "medium"}
    assert first["max_tokens"] == prompts.MAX_TOKENS["B1"] + 3072
    assert second["model"] == "claude-opus-5-5"
    assert second["output_config"] == {"effort": "xhigh"}
    assert second["max_tokens"] == prompts.MAX_TOKENS["B1"] + 12288
    # The ledger names the model, never the suffix.
    assert [row["model"] for row in story.ledger.entries()] == ["claude-sonnet-5-5", "claude-opus-5-5"]


def test_a_refusal_is_booked_at_its_usage_and_the_chain_moves_on(story):
    story.fakes.claude.append(claude_message(text="", stop="refusal", usage=claude_usage(input_tokens=900, output=0)))
    story.fakes.free.append(VALID)
    ctx, log = story.ctx(SONNET, FREE)

    assert _call(ctx) == {"ok": True}

    assert len(story.fakes.claude_sent) == 1      # FATAL: no retry at full price
    assert story.fakes.free_sent == [describe(FREE)]
    [row] = story.ledger.entries()
    assert row["model"] == "claude-sonnet-5-5"
    assert row["qty"] == 900
    assert row["est_usd"] == _up(900 * 2.00 / 1e6)
    assert row["note"].startswith("ModelRefusedError")
    assert any("ModelRefusedError" in line for line in log)


def test_a_server_side_fallback_is_booked_at_the_served_model_with_every_attempt(story):
    iterations = [
        SimpleNamespace(type="message", model=None, input_tokens=1000, cache_read_input_tokens=0,
                        cache_creation_input_tokens=0, output_tokens=40),
        SimpleNamespace(type="fallback_message", model="claude-sonnet-5", input_tokens=1000,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=200),
    ]
    blocks = [SimpleNamespace(type="fallback", from_=SimpleNamespace(model="claude-sonnet-5-5"),
                              to=SimpleNamespace(model="claude-sonnet-5")),
              SimpleNamespace(type="text", text=VALID)]
    story.fakes.claude.append(claude_message(model="claude-sonnet-5", content=blocks,
                                             usage=claude_usage(input_tokens=1000, output=200,
                                                                iterations=iterations)))
    ctx, log = story.ctx(SONNET)

    assert _call(ctx) == {"ok": True}

    [row] = story.ledger.entries()
    assert row["model"] == "claude-sonnet-5"
    assert row["qty"] == 1000 + 40 + 1000 + 200
    declined = pricing.llm_cost_cached(SONNET, 1000, 40)
    served = pricing.llm_cost_cached(Link("anthropic", "claude-sonnet-5"), 1000, 200)
    assert row["est_usd"] == _up(declined + served)
    assert "server-side fallback: claude-sonnet-5-5 declined, claude-sonnet-5 continued" in row["note"]
    assert any("server-side fallback" in line and "claude-sonnet-5's price" in line for line in log)


def test_an_unknown_served_model_is_booked_at_the_dearest_family_row_and_flagged(story):
    story.fakes.claude.append(claude_message(model="claude-opus-9", usage=claude_usage(input_tokens=1000,
                                                                                       output=100)))
    ctx, _log = story.ctx(OPUS)

    assert _call(ctx) == {"ok": True}

    [row] = story.ledger.entries()
    assert row["model"] == "claude-opus-9"
    assert row["est_usd"] == _up((1000 * 5.00 + 100 * 25.00) / 1e6)   # Opus 5 / 4.8's row
    assert "FLAGGED" in row["note"]


def test_a_truncated_reply_is_booked_and_fatal(story):
    story.fakes.claude.append(claude_message(text='{"ok": tr', stop="max_tokens",
                                             usage=claude_usage(input_tokens=500, output=400)))
    story.fakes.free.append(VALID)
    ctx, _log = story.ctx(SONNET, FREE)

    assert _call(ctx) == {"ok": True}

    assert len(story.fakes.claude_sent) == 1
    [row] = story.ledger.entries()
    assert row["est_usd"] == _up((500 * 2.00 + 400 * 10.00) / 1e6)
    assert row["note"].startswith("OutputTruncatedError")


# ======================================================== caps and opt-in

def test_a_claude_link_over_the_cap_is_refused_before_any_call(story):
    story.fakes.free.append(VALID)
    ctx, log = story.ctx(OPUS, FREE, ep=1, PER_EPISODE_CAP_USD="0.01")

    assert _call(ctx) == {"ok": True}

    assert story.fakes.claude_sent == []
    assert any(line.startswith(f"   ⏭ Skipping {describe(OPUS)}: refused: est $") for line in log)
    assert not os.path.exists(story.ledger_path)


def test_with_allow_paid_off_a_claude_link_is_skipped(story):
    """DEC-115: a paid link is never called while allow_paid is off."""
    story.fakes.free.append(VALID)
    ctx, log = story.ctx(SONNET, FREE, ALLOW_PAID="")

    assert _call(ctx) == {"ok": True}

    assert story.fakes.claude_sent == []
    assert any(f"Skipping {describe(SONNET)}: paid link, allow_paid is off" in line for line in log)


# ============================================================== estimate

@pytest.mark.parametrize("link", [SONNET, OPUS])
def test_the_estimate_covers_the_worst_e1v3_call_on_any_model_that_may_serve_it(link):
    """The widest E1v3 ask -- its input budget (pacing's chars/4 count, which
    Claude's tokenizer exceeds by up to 1.35x), its payoff cap and high
    effort's thinking room -- every prompt token a cache WRITE, every output
    token spent, served by any model of the link's fallback family: the
    estimate the meter checks against the caps is never below it."""
    tokens_in = prompts.input_budget("E1v3")
    effort = prompts.anthropic_effort("E1v3")
    assert effort == "high"
    cap = max(prompts.MAX_TOKENS["E1v3"], prompts.E1V3_PAYOFF_MAX_TOKENS) + registry.output_headroom(link, effort)
    real_in = math.ceil(tokens_in * pricing.CLAUDE_TOKENS_FACTOR)

    estimate = llm_spend.ledger_usd(pricing.llm_estimate_cost(link, tokens_in, cap))
    for member in pricing.ANTHROPIC_FALLBACK_FAMILIES[link.model]:
        price = pricing.llm_price_for(Link("anthropic", member))
        actual = pricing.llm_cost_cached(link, 0, cap, cache_write=real_in, price=price)
        assert estimate >= actual, member
    # The story-wide worst case reads the same never-low price.
    assert llm_spend.worst_call_usd(link) >= llm_spend.ledger_usd(
        pricing.llm_estimate_cost(link, tokens_in, prompts.E1V3_PAYOFF_MAX_TOKENS))
