"""Paid LLM calls of a story step are estimated, capped and booked (AI Story
phase 6, stage 5; DEC-115's follow-up: "LLM spend is not estimated or
booked").

Every call goes through the real ``llm.run_chain`` with ``llm.build_client``
-- the default client factory -- replaced by a fake provider that answers
from a queue and records every request it is sent: no network, and the keys
below are test values that never leave the process. The story is a real
``StoryStore`` under ``tmp_path``; today's spend is a ``spend.json`` there too.

The guard pins the free path: with ``allow_paid`` off, or no paid link in the
chain, ``run_chain`` receives exactly the keyword arguments it received
before this stage (read off the parent commit with the same recorder).

Stdlib + pytest (DEC-012); the estimate test needs the API's packages and
skips without them, like the other route tests.
"""

from __future__ import annotations

import functools
import json
import os
from types import SimpleNamespace

import pytest

import test_stories_api as tsa
from clipping.aistory import context, prompts, steps
from clipping.aistory.ledger import CostLedger
from clipping.aistory.store import StoryStore
from clipping.cancel import CancelToken
from clipping.providers import budget, llm, pacing, pricing
from clipping.providers.registry import Link, describe
from test_stories_api import api  # noqa: F401 -- the route tests' app fixture, used as it is

NOW = "2026-09-30T10:00:00+00:00"
# The default chain's paid link (priced) and its free neighbour.
PAID = Link("openrouter", "mistralai/mistral-small-3.2-24b-instruct")
FREE = Link("gemini", "gemini-test")
UNPRICED = Link("openrouter", "test/unpriced-model")
# Test values only: every request goes to the fake provider.
KEYS = {"GOOGLE_API_KEY": "test-gemini-key", "OPENROUTER_API_KEY": "test-openrouter-key"}

SYSTEM = "You write the story bible."
USER = "Écris la bible de l'histoire. " * 20
VALID = json.dumps({"ok": True})
B1_CAP = 400


def TIME_FN():  # noqa: N802 -- a constant clock, named like the constant it is
    return 1000.0


class Log(list):
    def __call__(self, line):
        self.append(str(line))


class Garbled(Exception):
    """No status and no usable answer: DEC-153 cannot prove it unbilled."""


def usage(prompt, completion, **extra):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion,
                           total_tokens=prompt + completion, **extra)


class FakeProvider:
    """``llm.build_client``'s stand-in: each provider answers from its own
    queue -- ``(content, usage)`` or an exception to raise -- and every
    request it is sent is recorded (``sent``), queued or not."""

    def __init__(self):
        self.queues = {}
        self.sent = []

    def answer(self, provider, *answers):
        self.queues.setdefault(provider, []).extend(answers)

    def __call__(self, link, *, api_key, timeout):
        create = functools.partial(self._create, link)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def _create(self, link, **body):
        self.sent.append(describe(link))
        queue = self.queues.get(link.provider) or []
        if not queue:
            raise AssertionError(f"{describe(link)} was sent a request no answer was queued for")
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        content, reply_usage = answer
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                               usage=reply_usage)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    """No key, chain or cap of the machine reaches a test; today's spend is
    kept under ``tmp_path``."""
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in ("LLM_CHAIN", "ALLOW_SLOW_CHAIN", *budget.ENV_NAMES):
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
    fake = FakeProvider()
    monkeypatch.setattr(llm, "build_client", fake)
    ledger_path = os.path.join(store.story_dir(story_id), "cost_ledger.json")

    def ctx(settings, ep=None):
        log = Log()
        return steps.StepContext(
            job_id="job000000001", story_id=story_id, step="bible", ep=ep, params={}, cancel=CancelToken(),
            settings_env=dict(settings), outputs_dir=str(tmp_path), on_log=log,
        ), log

    return SimpleNamespace(ctx=ctx, fake=fake, ledger=CostLedger(ledger_path), ledger_path=ledger_path)


def _settings(*links, **extra):
    return {"LLM_CHAIN": ",".join(describe(link) for link in links), **KEYS, **extra}


def _call(ctx, validator=lambda value: []):
    from clipping.aistory.steps import llm_call

    return llm_call.call_json(ctx, "B1", SYSTEM, USER, {}, validator=validator, time_fn=TIME_FN)


def _cost(link, tokens_in, tokens_out):
    price = pricing.LLM_PRICES[describe(link)]
    return (tokens_in * price.input_usd_per_m + tokens_out * price.output_usd_per_m) / 1_000_000


# ================================================================ booking

def test_a_paid_reply_is_booked_from_its_usage_on_the_ledger_and_todays_spend(story):
    story.fake.answer("openrouter", (VALID, usage(1000, 500)))
    ctx, _log = story.ctx(_settings(PAID, ALLOW_PAID="1"), ep=2)

    assert _call(ctx) == {"ok": True}

    [row] = story.ledger.entries()
    assert (row["ep"], row["step"], row["provider"], row["model"], row["unit"], row["qty"], row["paid"]) == (
        2, "bible", "openrouter", PAID.model, "token", 1500, True)
    assert "note" not in row
    # The ledger keeps four decimals: a cost is rounded up to them, never down.
    usd = _cost(PAID, 1000, 500)
    assert usd <= row["est_usd"] < usd + 0.0001
    assert budget.day_spent() == row["est_usd"]
    assert story.fake.sent == [describe(PAID)]


def test_over_the_episode_cap_the_paid_link_is_skipped_with_the_numbers_and_a_free_one_answers(story):
    story.ledger.append(step="assets", provider="fal", model="flux", unit="image", qty=1, est_usd=0.5,
                        paid=True, ep=1)
    seeded = story.ledger.entries()
    story.fake.answer("gemini", (VALID, usage(1000, 500)))
    ctx, log = story.ctx(_settings(PAID, FREE, ALLOW_PAID="1", PER_EPISODE_CAP_USD="0.50"), ep=1)

    assert _call(ctx) == {"ok": True}

    assert story.fake.sent == [describe(FREE)]
    skipped = [line for line in log if line.startswith(f"   ⏭ Skipping {describe(PAID)}: refused: est $")]
    assert len(skipped) == 1
    assert "would bring this episode to $0.50 of its $0.50 cap" in skipped[0]
    tokens_in = pacing.estimate_tokens(SYSTEM, USER)
    assert f"≈{tokens_in} tokens in + {B1_CAP} out" in skipped[0]
    assert story.ledger.entries() == seeded
    assert budget.day_spent() == 0.0


def test_a_paid_reply_that_fails_validation_is_booked_all_the_same(story):
    story.fake.answer("openrouter", (VALID, usage(1000, 500)), (VALID, usage(800, 400, cost=0.00042)))
    ctx, _log = story.ctx(_settings(PAID, ALLOW_PAID="1"))

    with pytest.raises(steps.StepFailed, match="the reply failed validation twice"):
        _call(ctx, validator=lambda value: ["not what was asked"])

    rows = story.ledger.entries()
    assert [(row["qty"], row["ep"], row["unit"]) for row in rows] == [(1500, None, "token"), (1200, None, "token")]
    # The second reply carried the provider's own cost: that is what is booked.
    assert rows[1]["est_usd"] == 0.0005
    assert budget.day_spent() == round(rows[0]["est_usd"] + rows[1]["est_usd"], 4)


def test_a_paid_links_two_invalid_replies_fall_through_and_each_is_booked_once(story):
    """Phase 7 stage 2d: the paid link's reply fails validation twice (booked
    both times, DEC-206, as before) and the call falls through to the next,
    free link rather than failing -- the free reply is never billed."""
    story.fake.answer("openrouter", (VALID, usage(1000, 500)), (VALID, usage(900, 450)))
    story.fake.answer("gemini", (VALID, usage(10, 5)))
    ctx, log = story.ctx(_settings(PAID, FREE, ALLOW_PAID="1"))
    seen = {"n": 0}

    def validator(value):
        seen["n"] += 1
        return ["not what was asked"] if seen["n"] <= 2 else []

    assert _call(ctx, validator=validator) == {"ok": True}

    assert story.fake.sent == [describe(PAID), describe(PAID), describe(FREE)]
    rows = story.ledger.entries()
    # Each of the paid link's two replies booked exactly once; the free
    # link's accepted reply is never billed.
    assert [(row["provider"], row["paid"]) for row in rows] == [("openrouter", True), ("openrouter", True)]
    assert budget.day_spent() == round(rows[0]["est_usd"] + rows[1]["est_usd"], 4)
    assert any("trying the next link" in line and describe(FREE) in line for line in log)


@pytest.mark.parametrize("answer,note", [
    (Garbled("connection dropped mid-reply"), "no usable answer after sending (Garbled): may be billed"),
    ((VALID, None), "no usage in the reply"),
])
def test_a_paid_request_with_no_usage_is_booked_at_its_estimate_with_a_note(story, answer, note):
    story.fake.answer("openrouter", answer)
    story.fake.answer("gemini", (VALID, usage(10, 5)))
    ctx, _log = story.ctx(_settings(PAID, FREE, ALLOW_PAID="1"))

    assert _call(ctx) == {"ok": True}

    [row] = story.ledger.entries()
    tokens_in = pacing.estimate_tokens(SYSTEM, USER)
    assert (row["provider"], row["qty"], row["paid"]) == ("openrouter", tokens_in + B1_CAP, True)
    assert note in row["note"]
    usd = _cost(PAID, tokens_in, B1_CAP)
    assert usd <= row["est_usd"] < usd + 0.0001
    assert budget.day_spent() == row["est_usd"]


def test_a_paid_link_without_a_price_is_refused_before_any_call(story):
    story.fake.answer("gemini", (VALID, usage(10, 5)))
    ctx, log = story.ctx(_settings(UNPRICED, FREE, ALLOW_PAID="1"))

    assert _call(ctx) == {"ok": True}

    assert story.fake.sent == [describe(FREE)]
    assert any(line.startswith(f"   ⏭ Skipping {describe(UNPRICED)}: No price for {describe(UNPRICED)}")
               for line in log)

    # Alone in the chain, nothing is left to run: the step fails before any call.
    ctx, _log = story.ctx(_settings(UNPRICED, ALLOW_PAID="1"))
    with pytest.raises(steps.StepFailed, match=f"No price for {describe(UNPRICED)}"):
        _call(ctx)
    assert story.fake.sent == [describe(FREE)]
    assert story.ledger.entries() == []


# ================================================================== guard

@pytest.mark.parametrize("links,allow_paid,chain,metered", [
    ((FREE, PAID), "", [FREE], False),        # allow_paid off: DEC-115 leaves the paid link out
    ((FREE,), "1", [FREE], False),            # allow_paid on, no paid link in the chain
    ((FREE, PAID), "1", [FREE, PAID], True),  # the paid path: the same recorder is metered
])
def test_the_free_path_hands_run_chain_exactly_what_it_did_before(story, monkeypatch, links, allow_paid, chain,
                                                                  metered):
    real = llm.run_chain
    calls = []

    @functools.wraps(real)
    def recorder(links_run, **kwargs):
        calls.append((list(links_run), dict(kwargs)))
        return real(links_run, **kwargs)

    monkeypatch.setattr(llm, "run_chain", recorder)
    story.fake.answer("gemini", (VALID, usage(10, 5)))
    ctx, log = story.ctx(_settings(*links, ALLOW_PAID=allow_paid))

    assert _call(ctx) == {"ok": True}

    [(sent_chain, kwargs)] = calls
    factory = kwargs.pop("client_factory", None)
    # The snapshot: what call_json handed run_chain on the parent commit.
    assert kwargs == {
        "system": SYSTEM, "user": USER, "schema": {}, "schema_name": "bible_core", "max_tokens": B1_CAP,
        "temperature": 0.5, "keys": {"gemini": "test-gemini-key", "openrouter": "test-openrouter-key"},
        # DEC-224 (phase 7): run_chain's deadline is STORY_CALL_DEADLINE_SECONDS (480 s).
        "on_log": log, "deadline": 1480.0, "time_fn": TIME_FN, "cancel": ctx.cancel,
    }
    assert sent_chain == chain
    assert (factory is not None) is metered
    assert story.fake.sent == [describe(FREE)]
    assert not os.path.exists(story.ledger_path)


# =============================================================== estimate

def test_an_llm_steps_estimate_is_the_worst_case_only_when_its_first_usable_link_is_paid(api):  # noqa: F811
    from clipping.aistory.steps import llm_spend

    story_id = tsa._create(api)["story_id"]
    url = f"/api/stories/{story_id}/estimate/bible"

    for settings in (_settings(FREE, PAID), _settings(FREE, PAID, ALLOW_PAID="1")):
        tsa._settings(api, settings)
        body = api.client.get(url).json()
        assert (body["route_class"], body["est_usd"]) == ("free", 0.0)

    tsa._settings(api, {"LLM_CHAIN": f"{describe(FREE)},{describe(PAID)}",
                        "OPENROUTER_API_KEY": KEYS["OPENROUTER_API_KEY"], "ALLOW_PAID": "1"})
    body = api.client.get(url).json()

    # The worst call a story step can make: the widest input budget a prompt
    # may be sent with and the widest reply cap (E1's payoff variant counted).
    pairs = [(prompts.INPUT_BUDGET.get(pid, context.PACK_TOKEN_BUDGET), cap) for pid, cap in prompts.MAX_TOKENS.items()]
    pairs.append((prompts.INPUT_BUDGET["E1"], prompts.E1_PAYOFF_MAX_TOKENS))
    worst = max(_cost(PAID, tokens_in, tokens_out) for tokens_in, tokens_out in pairs)
    assert (body["route_class"], body["link"], body["units"]) == ("paid", describe(PAID), {"llm_calls": 3})
    assert body["est_usd"] == round(3 * llm_spend.ledger_usd(worst), 6)
    assert worst <= llm_spend.ledger_usd(worst) < worst + 0.0001
