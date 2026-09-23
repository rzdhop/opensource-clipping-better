"""The provider catalogue and how a chain is spelled.

Stdlib only: no SDK, no network.
"""

import pathlib
import re

import pytest

from clipping.providers import registry
from clipping.providers.registry import ChainError, Link


# -------------------------------------------------------------------- specs

@pytest.mark.parametrize(
    "spec,expected",
    [
        # The case that a naive split("/") gets wrong: the MODEL contains slashes.
        ("groq/openai/gpt-oss-120b", Link("groq", "openai/gpt-oss-120b")),
        ("openrouter/meta-llama/llama-3.3-70b:free",
         Link("openrouter", "meta-llama/llama-3.3-70b:free")),
        ("nvidia/deepseek-ai/deepseek-v4-flash-0731",
         Link("nvidia", "deepseek-ai/deepseek-v4-flash-0731")),
        ("gemini/gemini-2.5-flash-lite", Link("gemini", "gemini-2.5-flash-lite")),
        ("  groq/llama-3.3-70b-versatile  ", Link("groq", "llama-3.3-70b-versatile")),
        ("GROQ/llama-3.3-70b-versatile", Link("groq", "llama-3.3-70b-versatile")),
    ],
)
def test_parse_spec(spec, expected):
    assert registry.parse_spec(spec) == expected


def test_a_model_containing_slashes_is_not_truncated():
    """Pinned separately because getting this wrong 404s with a confusing message."""
    link = registry.parse_spec("groq/openai/gpt-oss-120b")
    assert link.model == "openai/gpt-oss-120b"
    assert link.model != "openai"


@pytest.mark.parametrize("spec", ["", "   ", "noslash", "groq/", "/model", "bogus/x"])
def test_bad_specs_raise(spec):
    with pytest.raises(ChainError):
        registry.parse_spec(spec)


# ------------------------------------------------------------------- chains

def test_parse_chain_preserves_order():
    chain = registry.parse_chain("groq/a,nvidia/b,gemini/c")
    assert [link.provider for link in chain] == ["groq", "nvidia", "gemini"]


def test_parse_chain_accepts_a_list_and_existing_links():
    chain = registry.parse_chain(["groq/a", Link("nvidia", "b")])
    assert chain == [Link("groq", "a"), Link("nvidia", "b")]


def test_parse_chain_ignores_blank_entries():
    assert len(registry.parse_chain("groq/a, ,nvidia/b,")) == 2


def test_empty_chain_raises():
    with pytest.raises(ChainError):
        registry.parse_chain("")


def test_duplicate_links_are_kept():
    """Asking for the same model twice is a legitimate 'try again, fresh'."""
    assert len(registry.parse_chain("groq/a,groq/a")) == 2


def test_default_chain_is_parseable_and_ordered_fast_then_plentiful_then_floor():
    chain = registry.parse_chain(registry.DEFAULT_LLM_CHAIN)
    assert [link.provider for link in chain] == ["groq", "gemini", "nvidia"]


def test_chain_from_env(monkeypatch):
    monkeypatch.setenv("LLM_CHAIN", "mistral/mistral-small-latest")
    assert registry.chain_from_env() == [Link("mistral", "mistral-small-latest")]


def test_chain_from_env_falls_back_to_the_default(monkeypatch):
    monkeypatch.delenv("LLM_CHAIN", raising=False)
    assert registry.chain_from_env() == registry.parse_chain(registry.DEFAULT_LLM_CHAIN)


# ---------------------------------------------------------------- providers

def test_every_provider_declares_a_key_and_a_timeout():
    for name, provider in registry.PROVIDERS.items():
        assert provider.name == name
        assert provider.env_key, f"{name} has no env key"
        assert provider.default_timeout > 0, f"{name} has no timeout"
        for level in provider.structured:
            assert level in ("json_schema", "json_object"), f"{name}: {level}"


def test_nvidias_timeout_sits_above_the_measured_gateway_cutoff():
    """330s > the ~300s NIM gateway limit, so its 504 is received rather than
    raced to a local timeout that carries no information (DEC-019)."""
    assert registry.PROVIDERS["nvidia"].default_timeout == 330


def test_groq_declares_the_tpm_that_actually_binds():
    """8000 TPM is why the analyzer must keep every request small."""
    assert registry.PROVIDERS["groq"].tpm == 8000


def test_the_existing_two_providers_keep_their_env_vars():
    """Renaming these would silently orphan every existing .env and compose file."""
    assert registry.PROVIDERS["nvidia"].env_key == "NVIDIA_API_KEY"
    assert registry.PROVIDERS["gemini"].env_key == "GOOGLE_API_KEY"


def test_custom_provider_needs_a_base_url(monkeypatch):
    monkeypatch.delenv("LLM_CUSTOM_BASE_URL", raising=False)
    with pytest.raises(ChainError):
        registry.provider_for(Link("custom", "whatever"))


def test_custom_provider_resolves_its_base_url(monkeypatch):
    monkeypatch.setenv("LLM_CUSTOM_BASE_URL", "http://127.0.0.1:11434/v1")
    provider = registry.provider_for(Link("custom", "llama3"))
    assert provider.base_url == "http://127.0.0.1:11434/v1"


def test_describe_round_trips_through_parse_spec():
    for spec in ("groq/openai/gpt-oss-120b", "nvidia/deepseek-ai/deepseek-v4-flash-0731"):
        assert registry.describe(registry.parse_spec(spec)) == spec


# ------------------------------------------------------- the one NIM default

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

# The four files that used to carry their own copy of the model id. web/api/*
# is read as TEXT, never imported: importing it needs pydantic, which CI does
# not install, and an importorskip on a drift guard means it never runs in the
# one place that checks every push (DEC-012, and the DEC-050 precedent).
_FORMER_COPIES = (
    PROJECT_ROOT / "clipping" / "config.py",
    PROJECT_ROOT / "web" / "api" / "models.py",
    PROJECT_ROOT / "web" / "api" / "config_adapter.py",
)

# Every NIM model id this project has ever shipped or benchmarked. A new literal
# in any of the files above will almost certainly match one of these families.
_MODEL_LITERAL = re.compile(
    r'["\'][^"\']*(deepseek|gemma|nemotron|gpt-oss|glm-|kimi)[^"\']*["\']'
)


def test_the_nim_default_is_defined_once_and_the_chain_is_built_from_it():
    assert registry.NVIDIA_DEFAULT_MODEL
    assert f"nvidia/{registry.NVIDIA_DEFAULT_MODEL}" in registry.DEFAULT_LLM_CHAIN
    link = registry.parse_chain(registry.DEFAULT_LLM_CHAIN)[-1]
    assert link == Link("nvidia", registry.NVIDIA_DEFAULT_MODEL)


def test_no_former_copy_grew_a_model_literal_back():
    """The negative half is the whole point of this test.

    Asserting that config.NVIDIA_MODEL equals the registry constant proves
    nothing on its own -- it would still pass if web/api/models.py quietly
    carried its own string, which is exactly how the dashboard and the CLI came
    to disagree about which model an unconfigured job calls.
    """
    offenders = []
    for path in _FORMER_COPIES:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            code = line.split("#", 1)[0]
            if _MODEL_LITERAL.search(code):
                offenders.append(f"{path.name}:{line_no}: {line.strip()}")
    assert not offenders, (
        "A model id must be written once, in registry.NVIDIA_DEFAULT_MODEL:\n"
        + "\n".join(offenders)
    )


def test_the_web_api_references_the_shared_constant():
    for name in ("models.py", "config_adapter.py"):
        text = (PROJECT_ROOT / "web" / "api" / name).read_text(encoding="utf-8")
        assert "NVIDIA_MODEL" in text, name


def test_config_reexports_the_same_object():
    from clipping import config

    assert config.NVIDIA_MODEL == registry.NVIDIA_DEFAULT_MODEL


def test_the_shipped_nim_default_is_covered_by_the_thinking_switch():
    """The default is only usable because its reasoning preamble is turned off.

    Measured 2026-09-21: with thinking ON this model answers ``content=null``
    and the run dies in ``LlmClient._content_of``. With it off, 1.3-2.9s and
    schema-valid JSON. ``_extra_body``'s predicate is a substring match on the
    model name, so a future default that is also a reasoning model but is not
    called "deepseek" would silently lose the switch -- which is what this pins.
    """
    from clipping.providers import llm

    extra = llm._extra_body(Link("nvidia", registry.NVIDIA_DEFAULT_MODEL))
    assert extra == {"chat_template_kwargs": {"thinking": False}}


def test_every_nim_reasoning_family_gets_the_thinking_switch():
    """The gap that disqualified a model.

    ``_extra_body``'s predicate once named "deepseek" and nothing else.
    nemotron-3.5-lightning was benchmarked as "reasoning prose, unparseable" and
    rejected on that basis; with thinking off it answers the same request in
    ~20s with usable candidates. It was disqualified by a missing flag, not by
    its own behaviour, and it is the shipped default now.

    Each family here was measured failing a different way with thinking ON:
    deepseek returns ``content=null``, nemotron-3.5-lightning returns prose, and
    glm spends the whole token budget on the preamble and truncates the JSON.
    """
    from clipping.providers import llm

    off = {"chat_template_kwargs": {"thinking": False}}
    for family in llm._NIM_REASONING_FAMILIES:
        assert llm._extra_body(Link("nvidia", f"vendor/{family}-something")) == off, family

    # Only on NIM: the flag is a NIM chat-template argument, and sending it to
    # another provider is a request body that provider never asked for.
    assert llm._extra_body(Link("groq", "deepseek-r1")) is None
    # And not to a model that does not need it.
    assert llm._extra_body(Link("nvidia", "meta/llama-3.3-70b")) is None


def test_a_pinned_model_string_cannot_detect_a_retirement():
    """Documentation, deliberately not an assertion about the string itself.

    DEC-007 believed a pinned string would catch a retirement; DEC-024 corrected
    it. The job that prompted this proved the sharper version: gemma-4-31b-it
    was neither retired nor 410 Gone -- it stayed in GET /v1/models, accepted the
    request, and answered nothing for 120s. Only a real request finds that, which
    is what the preflight ping and tools/bench_llm.py are for.
    """
    assert registry.NVIDIA_DEFAULT_MODEL != "google/gemma-4-31b-it"


def test_the_default_is_not_the_one_that_answered_with_nothing():
    """Liveness is not suitability, pinned as a string.

    deepseek-v4.1-flash was shipped for one commit on the strength of being
    fast and schema-valid. It answers ``{"candidates": []}`` in seven tokens
    on every real transcript, including one that had already yielded seven
    clips. Fast, valid and useless is still useless.
    """
    assert registry.NVIDIA_DEFAULT_MODEL != "deepseek-ai/deepseek-v4.1-flash"


# ---------------------------------------------- probe timeouts (DEC-072)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _link(name):
    return Link(name, "some/model")


def test_every_provider_declares_a_positive_probe_timeout():
    for name, provider in registry.PROVIDERS.items():
        assert provider.probe_timeout > 0, name


def test_the_nim_probe_waits_past_its_measured_queue():
    """A working key answered a 2-token ping in 48.9 / 57.0 / 49.7s on
    2026-09-23 -- the queue, not the model. A 45s probe called that dead."""
    assert registry.probe_timeout(_link("nvidia")) >= 2 * 57.0
    assert registry.probe_timeout(_link("groq")) == registry.DEFAULT_PROBE_TIMEOUT


@pytest.mark.parametrize("name", list(registry.PROVIDERS))
def test_a_probe_can_never_inherit_a_request_timeout(name, monkeypatch):
    """The probe exists to be cheaper than a request. If it could wait as long
    as one, preflight would be the delay it was written to prevent."""
    # custom resolves its base URL at build time and cannot exist without one.
    monkeypatch.setenv("LLM_CUSTOM_BASE_URL", "http://localhost:11434/v1")
    link = _link(name)
    assert registry.probe_timeout(link) < registry.effective_timeout(link)
    assert registry.work_probe_timeout(link) <= registry.effective_timeout(link)


def test_the_work_probe_keeps_its_old_cap_where_the_ping_did_not_change(monkeypatch):
    """90s was 2 x 45s; only a provider that declared a slower ping moves."""
    monkeypatch.setenv("LLM_CUSTOM_BASE_URL", "http://localhost:11434/v1")
    for name, provider in registry.PROVIDERS.items():
        if provider.probe_timeout == registry.DEFAULT_PROBE_TIMEOUT:
            assert registry.work_probe_timeout(_link(name)) == 90.0, name
    assert registry.work_probe_timeout(_link("nvidia")) == 240.0


def test_an_explicit_probe_override_wins():
    assert registry.probe_timeout(_link("nvidia"), override=5) == 5.0
    assert registry.probe_timeout(_link("nvidia"), override=0) == 120.0


def test_an_old_style_provider_record_still_builds():
    """The new fields are trailing and defaulted."""
    p = registry.Provider("x", "u", "K", 1, None, (), 60, "n")
    assert p.probe_timeout == registry.DEFAULT_PROBE_TIMEOUT
    assert p.primary is True
    assert p.signup_url == ""


# ------------------------------------------- primary vs floor (DEC-073)

def test_nim_is_the_floor_and_something_else_is_primary():
    assert registry.PROVIDERS["nvidia"].primary is False
    assert not registry.is_primary(_link("nvidia"))
    primaries = [n for n, p in registry.PROVIDERS.items() if p.primary]
    # The default chain's first two links must be primaries, or the gate that
    # refuses an NVIDIA-only job would have nothing to send the user to.
    for link in registry.parse_chain(registry.DEFAULT_LLM_CHAIN)[:2]:
        assert link.provider in primaries


def test_every_hosted_provider_names_where_to_get_its_free_key():
    for name, provider in registry.PROVIDERS.items():
        if name == "custom":
            continue  # the user's own endpoint; there is nothing to sign up for
        assert provider.signup_url.startswith("https://"), name


def test_every_signup_url_is_the_one_env_example_documents():
    """One source for the URL. .env.example is the human-facing list, and it is
    this test's oracle rather than a fourth copy that can drift."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name, provider in registry.PROVIDERS.items():
        if provider.signup_url:
            assert provider.signup_url in text, name
