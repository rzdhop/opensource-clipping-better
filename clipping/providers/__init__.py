"""Hosted AI providers: one OpenAI-compatible client layer for the free tiers.

The pipeline talks to Groq, Gemini, NVIDIA NIM, OpenRouter, Mistral or any other
OpenAI-compatible endpoint through the same client, selected by an explicit,
user-configured chain (``LLM_CHAIN``). Nothing here imports an SDK at module
scope, so the package stays importable in the pytest-only CI environment.
"""

from .errors import ProviderError
from .registry import (
    DEFAULT_LLM_CHAIN,
    PROVIDERS,
    ChainError,
    Link,
    chain_from_env,
    describe,
    env_key_for,
    parse_chain,
    parse_spec,
    provider_for,
)

__all__ = [
    "ChainError",
    "DEFAULT_LLM_CHAIN",
    "Link",
    "PROVIDERS",
    "ProviderError",
    "chain_from_env",
    "describe",
    "env_key_for",
    "parse_chain",
    "parse_spec",
    "provider_for",
]
