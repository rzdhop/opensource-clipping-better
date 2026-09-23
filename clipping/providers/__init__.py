"""Hosted AI providers: one OpenAI-compatible client layer for the free tiers.

The pipeline talks to Groq, Gemini, NVIDIA NIM, OpenRouter, Mistral or any other
OpenAI-compatible endpoint through the same client, selected by an explicit,
user-configured chain (``LLM_CHAIN``). Nothing here imports an SDK at module
scope, so the package stays importable in the pytest-only CI environment.
"""

from .errors import ProviderError
from .registry import (
    DEFAULT_LLM_CHAIN,
    DEFAULT_PROBE_TIMEOUT,
    NVIDIA_DEFAULT_MODEL,
    PROVIDERS,
    ChainError,
    Link,
    chain_from_env,
    describe,
    effective_timeout,
    env_key_for,
    is_primary,
    parse_chain,
    parse_spec,
    probe_timeout,
    provider_for,
    work_probe_timeout,
)

__all__ = [
    "ChainError",
    "DEFAULT_LLM_CHAIN",
    "DEFAULT_PROBE_TIMEOUT",
    "Link",
    "NVIDIA_DEFAULT_MODEL",
    "PROVIDERS",
    "ProviderError",
    "chain_from_env",
    "describe",
    "effective_timeout",
    "env_key_for",
    "is_primary",
    "parse_chain",
    "parse_spec",
    "probe_timeout",
    "provider_for",
    "work_probe_timeout",
]
