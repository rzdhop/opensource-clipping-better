"""AI Story's own LLM chain default (phase 7 stage 2b, DEC-224).

AI Story used to fall all the way through to Clips' DEFAULT_LLM_CHAIN (groq,
gemini, openrouter, mistral, nvidia) whenever nothing was configured -- in
practice, gemini/gemini-3.5-flash-lite, never benchmarked against a
story-writing prompt. The 2026-10-01 free bench found NVIDIA's
nemotron-3-ultra and nemotron-3-super gave the strongest beat sheets with
thinking off, so AI Story now gets its own default
(``registry.DEFAULT_STORY_LLM_CHAIN``), reached only when neither
``STORY_LLM_CHAIN`` nor ``LLM_CHAIN`` is set anywhere -- an operator who
already set ``LLM_CHAIN`` keeps exactly today's behaviour.

No network, no sleeping, stdlib + pytest only (DEC-012).
"""

from __future__ import annotations

import pytest

from clipping.providers import pricing, registry
from clipping.providers.registry import Link


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No key or chain from the machine running the tests reaches a step."""
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in ("STORY_LLM_CHAIN", "LLM_CHAIN", "ALLOW_PAID"):
        monkeypatch.delenv(name, raising=False)


def _llm_call():
    from clipping.aistory.steps import llm_call

    return llm_call


# ============================================================ resolve_chain

def test_story_chain_puts_the_free_gemini_link_first_and_nvidia_last(monkeypatch):
    llm_call = _llm_call()

    # Nothing set anywhere: the shipped AI Story default, in order (DEC-305:
    # the reliable free link first, the paid cents link next, the two Nvidia
    # links, which answered HTTP 5xx for a whole run, last).
    assert llm_call.resolve_chain({}) == [
        Link("gemini", "gemini-3.5-flash-lite"),
        Link("openrouter", "mistralai/mistral-medium-3.1"),
        Link("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
        Link("nvidia", "nvidia/nemotron-3-super-120b-a12b"),
    ]
    assert llm_call.resolve_chain({}) == registry.parse_chain(registry.DEFAULT_STORY_LLM_CHAIN)
    assert llm_call.resolve_chain(None) == registry.parse_chain(registry.DEFAULT_STORY_LLM_CHAIN)

    # An LLM_CHAIN in Settings wins over the new default (today's behaviour,
    # unchanged): an operator who already configured Clips is not disturbed.
    assert llm_call.resolve_chain({"LLM_CHAIN": "gemini/from-settings"}) == [
        Link("gemini", "from-settings")
    ]

    # STORY_LLM_CHAIN in Settings wins over LLM_CHAIN in Settings.
    assert llm_call.resolve_chain({
        "STORY_LLM_CHAIN": "groq/story-model",
        "LLM_CHAIN": "gemini/from-settings",
    }) == [Link("groq", "story-model")]

    # STORY_LLM_CHAIN in the process env wins over LLM_CHAIN in Settings too
    # -- the full precedence is Settings STORY_LLM_CHAIN, process STORY_LLM_CHAIN,
    # Settings LLM_CHAIN, process LLM_CHAIN, then the shipped default.
    monkeypatch.setenv("STORY_LLM_CHAIN", "mistral/from-process-story")
    assert llm_call.resolve_chain({"LLM_CHAIN": "gemini/from-settings"}) == [
        Link("mistral", "from-process-story")
    ]
    # But a Settings STORY_LLM_CHAIN still beats the process one.
    assert llm_call.resolve_chain({"STORY_LLM_CHAIN": "groq/from-settings-story"}) == [
        Link("groq", "from-settings-story")
    ]
    monkeypatch.delenv("STORY_LLM_CHAIN")

    # Blank values at a level fall through to the next, as they do for LLM_CHAIN.
    assert llm_call.resolve_chain({"STORY_LLM_CHAIN": "   ", "LLM_CHAIN": "gemini/from-settings"}) == [
        Link("gemini", "from-settings")
    ]


# ================================================= paid link gated by DEC-115

def test_paid_openrouter_skipped_without_allow_paid():
    llm_call = _llm_call()

    usable, skipped = llm_call.story_chain({})

    default = registry.parse_chain(registry.DEFAULT_STORY_LLM_CHAIN)
    openrouter_link = Link("openrouter", "mistralai/mistral-medium-3.1")
    assert openrouter_link in default  # sanity: it really is in the default chain

    assert openrouter_link not in usable
    assert usable == [link for link in default if link.provider != "openrouter"]
    assert skipped == [(openrouter_link, llm_call.PAID_SKIP_REASON)]

    # With allow_paid on, nothing is filtered here (caps are checked per call).
    usable_paid, skipped_paid = llm_call.story_chain({"ALLOW_PAID": "1"})
    assert usable_paid == default
    assert skipped_paid == []


# ======================================================================= pricing

def test_mistral_medium_priced():
    link = Link("openrouter", "mistralai/mistral-medium-3.1")

    price = pricing.llm_price_for(link)
    assert (price.input_usd_per_m, price.output_usd_per_m) == (0.44, 2.20)

    # A known token count: 1,000,000 in, 1,000,000 out -> $0.44 + $2.20.
    assert pricing.llm_cost(link, 1_000_000, 1_000_000) == pytest.approx(2.64)
    # And a smaller, less round count, to rule out a coincidental match.
    assert pricing.llm_cost(link, 500_000, 250_000) == pytest.approx(
        500_000 * 0.44 / 1_000_000 + 250_000 * 2.20 / 1_000_000
    )


# ================================================================== _extra_body

@pytest.mark.parametrize("model", [
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3-super-120b-a12b",
])
def test_nemotron_3_thinking_is_off(model):
    from clipping.providers import llm

    assert llm._extra_body(Link("nvidia", model)) == {"chat_template_kwargs": {"thinking": False}}


def test_nemotron_3_5_lightning_thinking_is_still_off():
    from clipping.providers import llm

    assert llm._extra_body(Link("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b")) == {
        "chat_template_kwargs": {"thinking": False}
    }


def test_a_non_matching_nvidia_model_gets_no_extra_body():
    from clipping.providers import llm

    assert llm._extra_body(Link("nvidia", "meta/llama-3.3-70b-instruct")) is None


def test_the_story_chain_tries_nvidia_and_still_reaches_the_free_floor():
    """The phase-7 walk found every NIM link skipped: "a 330s request does not
    fit the 300s left in the time budget". The nemotron-3 links get their own
    measured timeouts, and one story call's budget holds both NIM retry
    ladders (MAX_ATTEMPTS each) and still a full request to the free gemini
    floor, so a NIM outage never leaves a step without a model."""
    from clipping.aistory.steps import llm_call
    from clipping.providers import llm, registry

    chain = registry.parse_chain(registry.DEFAULT_STORY_LLM_CHAIN)
    timeouts = {registry.describe(link): registry.effective_timeout(link) for link in chain}
    nim = [t for label, t in timeouts.items() if label.startswith("nvidia/")]
    floor = timeouts["gemini/" + registry.GEMINI_DEFAULT_MODEL]

    assert all(t < llm_call.STORY_CALL_BUDGET_SECONDS for t in nim)  # a step plans with it
    assert llm.MAX_ATTEMPTS * sum(nim) + floor <= llm_call.STORY_CALL_DEADLINE_SECONDS
    # The Clips mode's NIM default keeps the provider's own timeout.
    assert registry.effective_timeout(registry.parse_spec("nvidia/nvidia/nemotron-3.5-lightning-30b-a3b")) == 330
