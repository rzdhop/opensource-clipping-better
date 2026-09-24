"""The catalogue of OpenAI-compatible LLM providers, and how a chain is spelled.

Every provider here speaks the OpenAI chat-completions API, which is why one
client class can drive all of them. What differs is the base URL, which env var
holds the key, what the free tier allows, and whether the model accepts a
structured-output schema.

A *chain* is an ordered, explicitly configured list of links:

    LLM_CHAIN=groq/openai/gpt-oss-120b,gemini/gemini-3.5-flash-lite,nvidia/...

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

# How long a liveness probe may wait for a provider that declares nothing
# faster or slower. See ``probe_timeout`` below and DEC-072.
DEFAULT_PROBE_TIMEOUT = 45.0

# ``probe_timeout`` is how long the preflight ping may wait for this provider;
# ``primary`` says whether it can carry the analysis on its own (False = the
# chain's slow floor, which a job may not run on alone without an explicit
# override, DEC-073); ``signup_url`` is where to get its key; ``free_tier``
# says whether the DEFAULT model on it costs nothing, so a message never calls
# a billed link free (DEC-088); ``fallback_models`` are the models tried, in
# order, on the SAME key when the configured one answers "this model is not
# available" -- and only then (DEC-089). All five are trailing and defaulted
# so a Provider built without them still works.
Provider = namedtuple(
    "Provider",
    "name base_url env_key rpm tpm structured default_timeout notes "
    "probe_timeout primary signup_url free_tier fallback_models",
    defaults=(DEFAULT_PROBE_TIMEOUT, True, "", True, ()),
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
        signup_url="https://console.groq.com/keys",
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
        signup_url="https://aistudio.google.com/apikey",
        # Measured 2026-09-24 against the real pass-A request: found the test
        # transcript's clip 3/3 and answered real windows in 1.0-1.3s. An alias
        # that Google moves forward, which is what a fallback for a retired
        # model wants -- and why it is NOT the default (DEC-087).
        fallback_models=("gemini-flash-lite-latest",),
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
            "than raced to a local timeout (DEC-019). The free tier also "
            "QUEUES: a 2-token ping with a working key answered 'ok' in "
            "48.9 / 57.0 / 49.7s on 2026-09-23, almost all of it time to first "
            "byte, so the probe gets 120s (DEC-072)."
        ),
        probe_timeout=120.0,
        # The floor, not a primary: at ~12 tokens/s behind a ~50s queue a scan
        # that takes seconds elsewhere takes tens of minutes here (DEC-073).
        primary=False,
        signup_url="https://build.nvidia.com/",
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
        signup_url="https://openrouter.ai/keys",
        # The default model is billed (~$0.10 per million tokens in). The
        # ``:free`` ones measured on 2026-09-24 could not do the job.
        free_tier=False,
        # The only other OpenRouter model that found the clip 3/3 (2026-09-24).
        # Same price tier as the default ($0.10/$0.32 vs $0.094/$0.25 per M),
        # less selective, slower on real windows -- a fallback, not a default.
        fallback_models=("meta-llama/llama-3.3-70b-instruct",),
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
        signup_url="https://console.mistral.ai/",
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
        # No measurement to appeal to, and a local model on CPU is routinely
        # slower to first token than a hosted free tier.
        probe_timeout=60.0,
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


def probe_timeout(link, override=None) -> float:
    """How long a liveness PING to *link* may wait, in seconds.

    Per provider for the same reason ``effective_timeout`` is: one number for
    every provider was measured against one provider. 45s came from probes of a
    NIM model that ran 1.3-11.4s (DEC-056); two days later the NIM free tier
    took ~50s to say "ok" with a working key, and the probe declared a live
    provider dead (DEC-072). *override* of 0 or None means "the provider's own".
    """
    try:
        value = float(override or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value > 0:
        return value
    return float(PROVIDERS[link.provider].probe_timeout or DEFAULT_PROBE_TIMEOUT)


def work_probe_timeout(link) -> float:
    """The cap on a preflight WORK probe (a real pass-A request) to *link*.

    Twice the ping allowance, never more than one real request may take. The
    2x reproduces the previous fixed 90s = 2 x 45s exactly for every provider
    with the default ping, so only a provider that declared a slower probe gets
    a longer work probe.
    """
    return min(effective_timeout(link), 2.0 * probe_timeout(link))


# Settings -> Test provider chain (DEC-090, DEC-091). Each keyed link is asked
# the real pass-A request on a small fixture, and may take as long as a JOB's
# own request to it would -- so a timeout here means what it means in a job.
# Measured 2026-09-24 on requests that then succeeded: Gemini's free tier took
# 1.1s to 100.8s for the same kind of window (queueing, not thinking: 76-195
# output tokens and no reasoning tokens), NVIDIA's floor 93-193s. The
# preflight's 90s work cap would have called that Gemini dead.
#
# Providers run at once and links on one provider one after another, so the
# route waits for the slowest provider's SUM, plus DIAGNOSTIC_SLACK_SECONDS for
# the round trip. The per-link cap keeps the default chain inside the route's
# 300s ceiling: NVIDIA's 330s request timeout becomes 280s here.
DIAGNOSTIC_SLACK_SECONDS = 10.0
DIAGNOSTIC_LINK_CAP_SECONDS = 280.0


def diagnostic_timeout(link) -> float:
    """How long the diagnostic may spend on *link*: a job request's timeout, capped.

    A ping after a failed request runs only inside what is left of this, so the
    allowance is the link's whole cost.
    """
    return min(effective_timeout(link), DIAGNOSTIC_LINK_CAP_SECONDS)


def diagnostic_budget(links, keys) -> float:
    """The longest the diagnostic can take for *links*, given *keys*."""
    per_provider = {}
    for link in links:
        if (keys or {}).get(link.provider):
            per_provider[link.provider] = (
                per_provider.get(link.provider, 0.0) + diagnostic_timeout(link)
            )
    return max(per_provider.values(), default=0.0) + DIAGNOSTIC_SLACK_SECONDS


def is_primary(link) -> bool:
    """Whether *link*'s provider can carry the analysis on its own (DEC-073)."""
    return bool(PROVIDERS[link.provider].primary)


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

# Gemini's default, in one place for the same reason as the NIM one.
#
# gemini-2.5-flash-lite was the default until 2026-09-24, when Google closed it
# to new accounts: `404 This model models/gemini-2.5-flash-lite is no longer
# available to new users`. It kept working for old keys, so nothing on an
# existing setup noticed -- every NEW user's Gemini link failed, the chain fell
# through to NVIDIA, and the job crawled or died there.
#
# Measured 2026-09-24 with tools/bench_llm.py on the real pass-A request:
#
#   gemini-3.5-flash-lite      test transcript 3/3 found the clip, 0.9-4.4s;
#                              3 real windows x2: 1.1-2.0s, 1-3 candidates  <-
#   gemini-flash-lite-latest   same quality, 1.0-1.7s -- but an ALIAS: its model
#                              changes under us without a benchmark
#   gemini-3.5-flash           503 "high demand" on all three samples
#
# No truncation, json_schema accepted, no thinking switch needed (72-200 output
# tokens against a 700 cap).
GEMINI_DEFAULT_MODEL = "gemini-3.5-flash-lite"

# Groq's default. NOT benchmarked on this project: no Groq key has been
# available to measure it with. It is kept because it is Groq's own flagship
# open model and the chain reports it the moment it fails.
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"

# OpenRouter's default: PAID, on purpose, and placed after Gemini in the chain so
# a funded key is only spent when a free tier did not answer.
#
# Measured 2026-09-24 with tools/bench_llm.py on the real pass-A request:
#
#   mistralai/mistral-small-3.2-24b-instruct  test transcript 3/3, 2.5-2.7s;
#       real windows 5.1-13.5s, 2-6 candidates, $0.094/$0.25 per M tokens  <-
#   meta-llama/llama-3.3-70b-instruct          test transcript 3/3, 2.1-2.9s;
#       real windows 2.3-30.0s and ALWAYS the maximum of 6 candidates -- it
#       ignores "two strong moments beat six weak ones". $0.10/$0.32.
#   nvidia/nemotron-3.5-lightning:free         malformed JSON after 79-100s
#   openai/gpt-oss-20b                          no content (reasoning ate it)
#
# A whole job is ~20k tokens: well under a cent. Not a Gemini or NIM model, so
# a retirement at either of those cannot take this link down with it.
OPENROUTER_DEFAULT_MODEL = "mistralai/mistral-small-3.2-24b-instruct"

# Mistral's default: its own rolling alias, which Mistral moves forward when it
# retires the model behind it. NOT benchmarked: no Mistral key on this project
# (ASSUMPTIONS). The link is skipped with a line saying so until one is set, and
# Settings -> Test provider chain measures it the moment one is.
MISTRAL_DEFAULT_MODEL = "mistral-small-latest"

# The shipped default. Groq first because it is by far the fastest free tier;
# Gemini second because its daily request budget is the largest; OpenRouter
# third because it is paid, so it is only spent when both free tiers failed;
# Mistral fourth, free but unmeasured; NVIDIA last because it has no published
# daily cap, so it is the floor that still answers when the others are
# exhausted. A link with no key is skipped at no cost, so listing five costs a
# user with one key nothing. Re-pick any model with tools/bench_llm.py.
#
# parse_spec splits on the FIRST slash only, which is what lets the NIM link
# carry a model id that itself contains one.
DEFAULT_LLM_CHAIN = (
    f"groq/{GROQ_DEFAULT_MODEL},"
    f"gemini/{GEMINI_DEFAULT_MODEL},"
    f"openrouter/{OPENROUTER_DEFAULT_MODEL},"
    f"mistral/{MISTRAL_DEFAULT_MODEL},"
    f"nvidia/{NVIDIA_DEFAULT_MODEL}"
)


def chain_from_env(default: str = DEFAULT_LLM_CHAIN) -> list:
    """The chain named by ``LLM_CHAIN``, or the shipped default."""
    return parse_chain(os.environ.get("LLM_CHAIN", "").strip() or default)


def default_model(provider):
    """The model the shipped chain uses on *provider*, or None."""
    return {
        "groq": GROQ_DEFAULT_MODEL,
        "gemini": GEMINI_DEFAULT_MODEL,
        "openrouter": OPENROUTER_DEFAULT_MODEL,
        "mistral": MISTRAL_DEFAULT_MODEL,
        "nvidia": NVIDIA_DEFAULT_MODEL,
    }.get(provider)


def suggested_link(provider):
    """What to add to LLM_CHAIN to put *provider*'s key to work."""
    return f"{provider}/{default_model(provider) or '<model>'}"
