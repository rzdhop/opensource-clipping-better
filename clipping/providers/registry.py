"""The catalogue of OpenAI-compatible LLM providers, and how a chain is spelled.

Every provider here speaks the OpenAI chat-completions API, which is why one
client class can drive all of them. What differs is the base URL, which env var
holds the key, what the free tier allows, and whether the model accepts a
structured-output schema.

A *chain* is an ordered, explicitly configured list of links:

    LLM_CHAIN=groq/openai/gpt-oss-120b,gemini/gemini-2.5-flash-lite,nvidia/...

This is not the silent cross-provider fallback DEC-003 forbade. That one caught
a bare Exception and billed a user on Gemini when they had chosen NVIDIA, with
the real error reduced to a warning. A chain is something the user wrote down,
every hop is printed, and a provider that is not in the list is never called —
which is asserted by a test, not just intended.

Stdlib only: this module must import with nothing installed (DEC-012).
"""

from __future__ import annotations

import os
from collections import namedtuple

Provider = namedtuple(
    "Provider",
    "name base_url env_key rpm tpm structured default_timeout notes",
)

# ``structured`` lists the response_format levels the provider is known to
# accept, best first. It seeds the negotiation in ``llm.py``; an empty tuple
# means "go straight to prompt-only". Being wrong here costs one 400, not a run.
#
# rpm/tpm are the FREE-tier limits as published in September 2026. They pace
# requests (``pacing.py``); they are not enforcement, and None means "not
# published / not the binding limit".
PROVIDERS = {
    "groq": Provider(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        env_key="GROQ_API_KEY",
        rpm=30,
        tpm=8000,
        structured=("json_schema", "json_object"),
        default_timeout=120,
        notes="Fastest free tier. TPM is the binding limit: keep requests small.",
    ),
    "gemini": Provider(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        env_key="GOOGLE_API_KEY",
        rpm=10,
        tpm=250000,
        structured=("json_schema", "json_object"),
        default_timeout=180,
        notes="OpenAI-compatible endpoint. Flash-Lite has the most daily requests.",
    ),
    "nvidia": Provider(
        name="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        env_key="NVIDIA_API_KEY",
        rpm=40,
        tpm=None,
        structured=("json_schema",),
        default_timeout=330,
        notes=(
            "No published daily cap, but slow: measured at ~12-13 tokens/s with "
            "the gateway cutting a request off at ~300s. The 330s timeout is "
            "deliberately above that so the server's 504 is received rather "
            "than raced to a local timeout (DEC-019)."
        ),
    ),
    "openrouter": Provider(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        env_key="OPENROUTER_API_KEY",
        rpm=20,
        tpm=None,
        structured=("json_schema", "json_object"),
        default_timeout=180,
        notes=":free models are capped at 50 requests/day without credits.",
    ),
    "mistral": Provider(
        name="mistral",
        base_url="https://api.mistral.ai/v1",
        env_key="MISTRAL_API_KEY",
        rpm=30,
        tpm=None,
        structured=("json_object",),
        default_timeout=180,
        notes="Free 'Experiment' tier; exact limits live in the admin console.",
    ),
    "custom": Provider(
        name="custom",
        base_url="",  # resolved from LLM_CUSTOM_BASE_URL at build time
        env_key="LLM_CUSTOM_API_KEY",
        rpm=20,
        tpm=None,
        structured=(),
        default_timeout=180,
        notes="Any other OpenAI-compatible endpoint, incl. a local one.",
    ),
}

PROVIDER_NAMES = tuple(PROVIDERS)

Link = namedtuple("Link", "provider model")


class ChainError(ValueError):
    """The chain string is malformed or names a provider that does not exist."""


def parse_spec(spec: str) -> Link:
    """``"groq/openai/gpt-oss-120b"`` -> ``Link("groq", "openai/gpt-oss-120b")``.

    Split on the FIRST slash only. This is not a detail: Groq and OpenRouter
    model ids contain slashes (``openai/gpt-oss-120b``,
    ``meta-llama/llama-3.3-70b``), so a naive ``split("/")`` or an rsplit
    silently truncates the model and the request 404s with a confusing message.
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ChainError("Empty provider spec.")

    text = spec.strip()
    if "/" not in text:
        raise ChainError(
            f"{text!r} is missing a model. Write '<provider>/<model>', e.g. "
            f"'groq/openai/gpt-oss-120b'."
        )

    provider, model = text.split("/", 1)
    provider = provider.strip().lower()
    model = model.strip()

    if provider not in PROVIDERS:
        raise ChainError(
            f"Unknown provider {provider!r} in {text!r}. "
            f"Known: {', '.join(PROVIDER_NAMES)}."
        )
    if not model:
        raise ChainError(f"{text!r} names a provider but no model.")

    return Link(provider, model)


def parse_chain(chain) -> list:
    """Parse ``"a/b,c/d"`` (or a list of specs) into ``[Link, ...]``.

    Order is preserved and duplicates are kept: asking for the same model twice
    is a legitimate way to say "try it again on a fresh connection".
    """
    if chain is None:
        return []
    if isinstance(chain, str):
        parts = [p for p in chain.split(",") if p.strip()]
    else:
        parts = list(chain)

    links = [parse_spec(p) if not isinstance(p, Link) else p for p in parts]
    if not links:
        raise ChainError("Empty chain: nothing to call.")
    return links


def provider_for(link) -> Provider:
    """The :class:`Provider` record behind *link*, with ``custom`` resolved."""
    provider = PROVIDERS[link.provider]
    if provider.name == "custom":
        base = os.environ.get("LLM_CUSTOM_BASE_URL", "").strip()
        if not base:
            raise ChainError(
                "The 'custom' provider needs LLM_CUSTOM_BASE_URL set to an "
                "OpenAI-compatible base URL."
            )
        provider = provider._replace(base_url=base)
    return provider


def env_key_for(link) -> str:
    """Name of the environment variable holding *link*'s API key."""
    return PROVIDERS[link.provider].env_key


def effective_timeout(link, override=None) -> float:
    """How long ONE request to *link* may take, in seconds.

    The single source of the number that both the socket and the time-budget
    check use. They have to be the same value or the check is decoration: the
    budget guard in ``llm.py`` used to compare against a 4-12s backoff while the
    socket waited 330s, so a third attempt could start at 614s against a 900s
    deadline and end at 925s.

    *override* of 0 or None means "the provider's own default" (``--llm-timeout``
    spells it that way).
    """
    try:
        value = float(override or 0)
    except (TypeError, ValueError):
        value = 0.0
    return value if value > 0 else float(provider_for(link).default_timeout)


def describe(link) -> str:
    """``"groq/openai/gpt-oss-120b"`` — what gets printed in the activity feed."""
    return f"{link.provider}/{link.model}"


# The NIM model, in ONE place. Four copies of this string used to exist -- here,
# clipping/config.NVIDIA_MODEL, web/api/models.py and web/api/config_adapter.py
# -- and nothing made them agree. They all point here now, and
# test_one_definition_of_the_nim_default asserts none of them grew a literal back.
#
# The fourth NIM default this project has had, because NVIDIA retires and
# un-provisions models faster than anyone tracks them: deepseek-v4-pro died
# 2026-08-07; deepseek-v4-flash-0731 died between 2026-09-19 and 2026-09-21;
# google/gemma-4-31b-it was benchmarked at 6.0s on 2026-09-21 and by that
# evening answered NOTHING AT ALL -- not 410, not an error, no reply in 120s to
# an 8-token request.
#
# **Picked on whether it finds clips, not on whether it replies.** That
# distinction is the whole lesson of this round. Measured 2026-09-22 against the
# REAL Pass-A request (45 beats of two real transcripts, strict
# CANDIDATES_SCHEMA, max_tokens=700), asking how many candidates came back:
#
#   nvidia/nemotron-3.5-lightning-30b-a3b  20-90s  2 candidates per window  <-
#   deepseek-ai/deepseek-v4.1-flash         8-31s  ZERO, every window, both
#                                                  transcripts, at every
#                                                  structured-output level
#   z-ai/glm-5.3-flash, z-ai/glm-5.3               timed out at 240s
#   nvidia/nemotron-3-super-120b-a12b        6s    malformed JSON
#   google/gemma-4-31b-it, openai/gpt-oss-20b     hang
#   ...and 10 more listed models: 404 for this account
#
# deepseek was briefly the default on the strength of being fast and
# schema-valid. It answers `{"candidates": []}` in seven tokens on every real
# transcript, including one that had previously yielded seven clips. **Fast,
# valid and useless is still useless**, and only a benchmark that counts
# candidates catches it -- which is why tools/bench_llm.py now measures that and
# why the preflight probe (DEC-056) is documented as liveness, not suitability.
#
# nemotron-3.5-lightning was rejected in an earlier round as "reasoning prose,
# unparseable". That was a missing flag, not the model: _extra_body now turns
# thinking off for it, and it answers the same request with usable candidates.
#
# **Listed is not callable.** Ten models in GET /v1/models answer
# 404 "Function <uuid>: Not found for account <id>". Reading the catalogue proves
# nothing; only a real request does. Re-pick with
# `tools/bench_llm.py --nim-shortlist`.
#
# NOTE: a test pinning this STRING cannot detect a retirement (DEC-007 believed
# otherwise, DEC-024 corrected it). What survives one is the chain: a dead link
# fails and the next provider answers -- PROVIDED it has a key. The job that
# prompted this had keys for exactly one of three links, so there was nothing to
# fall through to. A chain with one key is a chain of one.
NVIDIA_DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"

# The shipped default. Groq first because it is by far the fastest free tier;
# Gemini second because its daily request budget is the largest; NVIDIA last
# because it has no published daily cap, so it is the floor that still answers
# when the other two are exhausted. Re-pick the NVIDIA model with tools/bench_llm.py.
#
# parse_spec splits on the FIRST slash only, which is what lets the NIM link
# carry a model id that itself contains one.
DEFAULT_LLM_CHAIN = (
    "groq/openai/gpt-oss-120b,"
    "gemini/gemini-2.5-flash-lite,"
    f"nvidia/{NVIDIA_DEFAULT_MODEL}"
)


def chain_from_env(default: str = DEFAULT_LLM_CHAIN) -> list:
    """The chain named by ``LLM_CHAIN``, or the shipped default."""
    return parse_chain(os.environ.get("LLM_CHAIN", "").strip() or default)
