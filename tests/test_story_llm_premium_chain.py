"""The premium writing chain (AI Story plan 22 stage 1, DEC-273).

The prompts the human singled out as the ones that matter -- concepts (C1),
the bible (B1), the episode script in every version (E1/E2/E3) and the
first-watch judge (J1) -- are written on ``STORY_LLM_PREMIUM_CHAIN`` rather
than ``STORY_LLM_CHAIN``; everything else is unaffected. ``call_json``
decides this from the prompt id alone (``prompts.PREMIUM_PROMPT_IDS``), so no
step runner has to know or care which chain its own prompt is written on.

A stand-in ``runner`` (not ``llm.run_chain`` itself) is used throughout: it
records every call and answers from a queue, no network. Because it is not
``llm.run_chain``, ``llm_spend.is_provider_chain`` reports it as a test
stand-in and no metering engages, so these tests need no real
``StoryStore`` -- they are about which chain and which cap ``call_json``
builds, not about booking a paid reply (``test_story_llm_booking.py``
covers that).

No network, no sleeping, stdlib + pytest only (DEC-012).
"""

from __future__ import annotations

import copy

import pytest

from clipping.aistory import prompts, steps
from clipping.cancel import CancelToken
from clipping.providers import llm, pacing, registry
from clipping.providers.registry import Link

NOW = "2026-10-04T10:00:00+00:00"
GEMINI_PAID = Link("gemini-paid", registry.STORY_PREMIUM_MODEL)


class FakeRunner:
    """Stands in for ``llm.run_chain``: records every call (``self.calls``,
    one dict a call, with ``chain`` added), answers from a queue -- a reply
    or an exception to raise. The first link of the chain it was handed
    "answers", unless *link* names another."""

    def __init__(self, *replies, link=None):
        self.queue = list(replies)
        self.calls = []
        self._link = link

    def __call__(self, chain, **kwargs):
        call = dict(kwargs, chain=list(chain))
        self.calls.append(call)
        if not self.queue:
            raise AssertionError(f"no reply queued for call {len(self.calls)}")
        reply = self.queue.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        link = self._link or chain[0]
        return copy.deepcopy(reply), link


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No key or chain from the machine running the tests reaches a step."""
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in ("STORY_LLM_CHAIN", "STORY_LLM_PREMIUM_CHAIN", "LLM_CHAIN", "ALLOW_PAID"):
        monkeypatch.delenv(name, raising=False)
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    yield
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()


def _ctx(settings, log=None):
    return steps.StepContext(
        job_id="job000000001", story_id="story0000001", step="bible", ep=None, params={},
        cancel=CancelToken(), settings_env=dict(settings), outputs_dir="/unused",
        on_log=(log.append if log is not None else lambda line: None),
    )


def _llm_call():
    from clipping.aistory.steps import llm_call

    return llm_call


VALID = {"ok": True}


# ======================================================== PREMIUM_PROMPT_IDS

def test_premium_prompt_ids_are_the_writing_and_judge_families():
    # Plan 22 stage 2 (DEC-274): re-pinned on purpose -- C1v2/B1v3 match the
    # C1/B1 family by their version suffix; C1J (the brief judge) is listed
    # on its own (its suffix is not a version number of C1).
    assert prompts.PREMIUM_PROMPT_IDS == frozenset(
        {"C1", "C1v2", "C1J", "B1", "B1v3", "E1", "E1v2", "E2", "E2v2", "E3", "E3v2", "J1"}
    )
    # E4 (the consistency check) and J2 (the keyframe judge) check what was
    # written; they stay on the free chain.
    assert "E4" not in prompts.PREMIUM_PROMPT_IDS
    assert "J2" not in prompts.PREMIUM_PROMPT_IDS
    # A hypothetical future version of the same family is matched by the
    # family rule, not listed one id at a time.
    assert prompts._is_premium_family("E1v3")
    assert not prompts._is_premium_family("E4v2")


# ============================================================ resolve_premium_chain

def test_the_settings_key_wins_over_env_wins_over_default(monkeypatch):
    llm_call = _llm_call()

    # Nothing set anywhere: the shipped premium default.
    assert llm_call.resolve_premium_chain({}) == registry.parse_chain(registry.PREMIUM_STORY_LLM_CHAIN)
    assert llm_call.resolve_premium_chain({}) == [
        Link("gemini-paid", "gemini-3.8-flash"),
        *registry.parse_chain(registry.DEFAULT_STORY_LLM_CHAIN),
    ]

    # An operator who already configured LLM_CHAIN or STORY_LLM_CHAIN for
    # Clips/AI Story is not disturbed: a premium prompt falls through to the
    # same chain a non-premium one already resolves to.
    assert llm_call.resolve_premium_chain({"LLM_CHAIN": "gemini/from-settings"}) == [
        Link("gemini", "from-settings")
    ]
    assert llm_call.resolve_premium_chain({"STORY_LLM_CHAIN": "groq/story-model",
                                           "LLM_CHAIN": "gemini/from-settings"}) == [
        Link("groq", "story-model")
    ]

    # STORY_LLM_PREMIUM_CHAIN in Settings wins over both.
    assert llm_call.resolve_premium_chain({
        "STORY_LLM_PREMIUM_CHAIN": "gemini-paid/premium-model",
        "STORY_LLM_CHAIN": "groq/story-model",
    }) == [Link("gemini-paid", "premium-model")]

    # STORY_LLM_PREMIUM_CHAIN in the process env wins over STORY_LLM_CHAIN in
    # Settings too.
    monkeypatch.setenv("STORY_LLM_PREMIUM_CHAIN", "mistral/from-process-premium")
    assert llm_call.resolve_premium_chain({"STORY_LLM_CHAIN": "groq/story-model"}) == [
        Link("mistral", "from-process-premium")
    ]
    # But a Settings STORY_LLM_PREMIUM_CHAIN still beats the process one.
    assert llm_call.resolve_premium_chain({"STORY_LLM_PREMIUM_CHAIN": "groq/from-settings-premium"}) == [
        Link("groq", "from-settings-premium")
    ]
    monkeypatch.delenv("STORY_LLM_PREMIUM_CHAIN")

    # Blank values at a level fall through to the next.
    assert llm_call.resolve_premium_chain({"STORY_LLM_PREMIUM_CHAIN": "   ",
                                           "STORY_LLM_CHAIN": "gemini/from-settings"}) == [
        Link("gemini", "from-settings")
    ]


def test_resolve_chain_is_unaffected():
    """The non-premium resolver keeps its own behaviour exactly (no change
    in shape, only shared internals)."""
    llm_call = _llm_call()
    assert llm_call.resolve_chain({}) == registry.parse_chain(registry.DEFAULT_STORY_LLM_CHAIN)
    assert llm_call.resolve_chain({"LLM_CHAIN": "gemini/x"}) == [Link("gemini", "x")]


# ==================================================================== call_json

def test_a_premium_prompt_id_routes_to_the_premium_chains_free_tail():
    """allow_paid off: the premium link is skipped like any other paid link
    (DEC-115), and the free tail -- the premium chain's own default tail is
    DEFAULT_STORY_LLM_CHAIN -- runs exactly as it would for a non-premium
    call on the same settings."""
    llm_call = _llm_call()
    settings = {
        "STORY_LLM_PREMIUM_CHAIN": "gemini-paid/gemini-3.8-flash,gemini/gemini-test",
        "GOOGLE_API_KEY": "test-gemini-key",
    }
    log = []
    runner = FakeRunner(VALID, link=Link("gemini", "gemini-test"))

    value = llm_call.call_json(_ctx(settings, log), "C1", "system", "user", {},
                               validator=lambda v: [], runner=runner)

    assert value == VALID
    [call] = runner.calls
    assert call["chain"] == [Link("gemini", "gemini-test")]
    assert any(line.startswith("   ⏭ Skipping gemini-paid/gemini-3.8-flash: paid link, "
                               "allow_paid is off") for line in log)


def test_a_non_premium_prompt_id_never_touches_the_premium_chain():
    llm_call = _llm_call()
    settings = {
        "STORY_LLM_CHAIN": "gemini/regular-model",
        "STORY_LLM_PREMIUM_CHAIN": "gemini-paid/gemini-3.8-flash",
        "GOOGLE_API_KEY": "test-gemini-key",
    }
    runner = FakeRunner(VALID, link=Link("gemini", "regular-model"))

    # K1 (a cast prompt) is not in PREMIUM_PROMPT_IDS.
    assert "K1" not in prompts.PREMIUM_PROMPT_IDS
    value = llm_call.call_json(_ctx(settings), "K1", "system", "user", {},
                               validator=lambda v: [], runner=runner)

    assert value == VALID
    [call] = runner.calls
    assert call["chain"] == [Link("gemini", "regular-model")]
    assert call["max_tokens"] == prompts.MAX_TOKENS["K1"]


def test_headroom_is_added_to_the_cap_for_a_keyed_premium_link():
    """registry.MODEL_OUTPUT_HEADROOM is added to the cap call_json sends --
    only when the resolved, allow_paid-filtered chain actually names such a
    link. allow_paid on: story_chain filters nothing (the caps are checked
    per call elsewhere), so the premium link stays in the chain handed to
    the runner regardless of whether it ends up answering."""
    llm_call = _llm_call()
    settings = {"STORY_LLM_PREMIUM_CHAIN": "gemini-paid/gemini-3.8-flash", "ALLOW_PAID": "1"}
    runner = FakeRunner(VALID, link=GEMINI_PAID)

    value = llm_call.call_json(_ctx(settings), "C1", "system", "user", {},
                               validator=lambda v: [], runner=runner)

    assert value == VALID
    [call] = runner.calls
    headroom = registry.MODEL_OUTPUT_HEADROOM[("gemini-paid", "gemini-3.8-flash")]
    assert call["max_tokens"] == prompts.MAX_TOKENS["C1"] + headroom
    assert headroom == 2048


# ======================================================= Settings "Test chain"

def test_settings_test_chain_never_reaches_story_llm_premium_chain():
    """RC-P10: no chain test spends more than one paid call, and the premium
    chain is never one of them. Read as text (no pydantic import, DEC-012):
    ``POST /api/settings/test-chain`` reads only LLM_CHAIN, never
    STORY_LLM_CHAIN or STORY_LLM_PREMIUM_CHAIN at all."""
    import pathlib

    text = (pathlib.Path(__file__).resolve().parents[1]
            / "web/api/routes/settings.py").read_text(encoding="utf-8")
    start = text.index("async def run_chain_test")
    after = text[start + 1:]
    end = start + 1 + after.index("\n@router.") if "\n@router." in after else len(text)
    body = text[start:end]

    assert "STORY_LLM_PREMIUM_CHAIN" not in body
    assert "STORY_LLM_CHAIN" not in body
    assert "LLM_CHAIN" in body


def test_no_headroom_when_the_chain_names_no_such_link():
    """A non-premium call -- or a premium one whose chain fell through to
    free links only -- sends exactly the prompt's own cap, unchanged."""
    llm_call = _llm_call()
    settings = {"STORY_LLM_PREMIUM_CHAIN": "gemini/gemini-test", "GOOGLE_API_KEY": "k"}
    runner = FakeRunner(VALID, link=Link("gemini", "gemini-test"))

    llm_call.call_json(_ctx(settings), "C1", "system", "user", {}, validator=lambda v: [], runner=runner)

    [call] = runner.calls
    assert call["max_tokens"] == prompts.MAX_TOKENS["C1"]


# ========================================= workflow._premium_text_estimate

def _row(part, calls):
    return {"part": part, "units": {"llm_calls": calls, "images": 0, "edit_images": 0, "tts_chars": 0}}


def test_premium_text_estimate_prices_pending_premium_parts():
    from clipping.aistory import workflow

    rows = [_row("concepts", 1), _row("bible", 3), _row("cast", 5), _row("episode", 10)]
    row = workflow._premium_text_estimate(
        rows, {"STORY_LLM_PREMIUM_CHAIN": "gemini-paid/gemini-3.8-flash", "GEMINI_PAID_API_KEY": "k",
               "ALLOW_PAID": "1"},
    )

    assert row["calls"] == 1 + 3 + 10  # "cast" is not a premium-writing part
    assert row["usd"] > 0
    assert row["message"].startswith(f"+ ${row['usd']:.2f} writing ({row['calls']} premium calls on "
                                      "gemini-paid/gemini-3.8-flash)")


def test_premium_text_estimate_is_zero_with_nothing_pending():
    from clipping.aistory import workflow

    row = workflow._premium_text_estimate([_row("cast", 5)], {"ALLOW_PAID": "1"})
    assert row == {"usd": 0.0, "calls": 0, "message": ""}


def test_premium_text_estimate_names_why_when_allow_paid_is_off():
    from clipping.aistory import workflow

    row = workflow._premium_text_estimate(
        [_row("concepts", 1)], {"STORY_LLM_PREMIUM_CHAIN": "gemini-paid/gemini-3.8-flash",
                                "GEMINI_PAID_API_KEY": "k"},
    )
    assert row == {"usd": 0.0, "calls": 1, "message": "No premium writing: allow_paid is off."}


def test_premium_text_estimate_names_why_when_the_key_is_missing():
    from clipping.aistory import workflow

    row = workflow._premium_text_estimate(
        [_row("bible", 3)], {"STORY_LLM_PREMIUM_CHAIN": "gemini-paid/gemini-3.8-flash", "ALLOW_PAID": "1"},
    )
    assert row == {"usd": 0.0, "calls": 3, "message": "No premium writing: no key for GEMINI_PAID_API_KEY."}
