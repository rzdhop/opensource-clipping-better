"""The provider catalogue and how a chain is spelled.

Stdlib only: no SDK, no network.
"""

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
