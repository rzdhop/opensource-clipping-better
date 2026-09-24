"""Whether a chain job may start at all (DEC-073).

The job this was written for had a three-link default chain and one key --
NVIDIA's, the chain's slow floor. It passed the key gate, spent 93s in
preflight, and failed. The two fast free links had simply never been set up.

Stdlib only: no SDK, no network.
"""

from types import SimpleNamespace

import pytest

from clipping import config
from clipping.config import (
    CLI_SLOW_CHAIN_HINT,
    WEB_SLOW_CHAIN_HINT,
    chain_not_ready,
    chain_readiness,
    missing_provider_key,
)
from clipping.providers import registry

DEFAULT = registry.DEFAULT_LLM_CHAIN


# --------------------------------------------------------------- the refusal

def test_the_reported_job_is_refused():
    """Default chain, only NVIDIA keyed: a chain of one, running on the floor."""
    r = chain_readiness(DEFAULT, {"nvidia": "k"})

    assert r.ready is False
    assert [link.provider for link in r.keyed_slow] == ["nvidia"]
    primaries = [
        link.provider for link in registry.parse_chain(DEFAULT)
        if registry.is_primary(link)
    ]
    assert [link.provider for link, _, _ in r.missing] == primaries


def test_the_refusal_says_what_to_set_and_where_to_get_it():
    """"Set a key" without the URL is the difference between a two-minute fix
    and a search."""
    message = chain_readiness(DEFAULT, {"nvidia": "k"}).message

    for name in ("groq", "gemini"):
        provider = registry.PROVIDERS[name]
        assert provider.env_key in message
        assert provider.signup_url in message
    assert "--allow-slow-chain" in message


def test_the_refusal_calls_free_only_what_is_free():
    """OpenRouter's default is paid. The refusal may still point at it, but
    must not promise that every missing key costs nothing."""
    message = chain_readiness(DEFAULT, {"nvidia": "k"}).message

    assert "all are free" not in message
    rows = {line.split()[0]: line for line in message.splitlines()
            if line.startswith("  ")}
    openrouter = next(v for k, v in rows.items() if k.startswith("openrouter/"))
    assert "paid" in openrouter
    gemini = next(v for k, v in rows.items() if k.startswith("gemini/"))
    assert "paid" not in gemini


def test_an_all_free_refusal_still_says_so():
    message = chain_readiness("groq/a,gemini/b,nvidia/c", {"nvidia": "k"}).message
    assert "both are free" in message


def test_the_refusal_names_the_links_from_the_registry_not_a_literal():
    """The NIM model id lives in one place (DEC-024); the message reads it."""
    message = chain_readiness(DEFAULT, {"nvidia": "k"}).message
    nim = registry.describe(registry.parse_chain(DEFAULT)[-1])
    assert nim in message


def test_each_surface_gets_its_own_way_out():
    web = chain_readiness(DEFAULT, {"nvidia": "k"}, hint=WEB_SLOW_CHAIN_HINT)
    assert "Settings" in web.message
    assert "--allow-slow-chain" not in web.message
    cli = chain_readiness(DEFAULT, {"nvidia": "k"})
    assert cli.message.endswith(CLI_SLOW_CHAIN_HINT)


# --------------------------------------------------------- what still starts

@pytest.mark.parametrize("keyed", ["groq", "gemini", "openrouter", "mistral"])
def test_one_primary_key_is_enough(keyed):
    assert chain_readiness(DEFAULT, {keyed: "k", "nvidia": "k"}).ready
    assert chain_readiness(DEFAULT, {keyed: "k"}).ready


@pytest.mark.parametrize("keyed", ["openrouter", "mistral"])
def test_any_primary_counts_not_just_the_two_in_the_default(keyed):
    """The question is "can this carry the analysis", which the registry
    answers -- not a brand list."""
    chain = f"{keyed}/m,nvidia/n"
    assert chain_readiness(chain, {keyed: "k", "nvidia": "k"}).ready
    assert not chain_readiness(chain, {"nvidia": "k"}).ready


def test_the_override_lets_it_run():
    assert chain_readiness(DEFAULT, {"nvidia": "k"}, allow_slow=True).ready


def test_a_chain_the_user_wrote_as_nvidia_only_is_theirs_to_run():
    """DEC-023: the chain is a list the user wrote down. If it names no primary
    link there is nothing to send them to, and refusing would be overruling
    their choice rather than pointing out a gap in it."""
    assert chain_readiness("nvidia/a", {"nvidia": "k"}).ready
    assert chain_readiness("nvidia/a,nvidia/b", {"nvidia": "k"}).ready


def test_the_legacy_escape_hatch_is_not_gated():
    assert chain_readiness(DEFAULT, {"nvidia": "k"}, ai_provider="openai_compat").ready


def test_a_malformed_chain_is_reported_when_it_runs_not_here():
    assert chain_readiness("nosuchprovider/x", {"nvidia": "k"}).ready


def test_the_chain_is_never_edited():
    """This refuses to start. It does not change what would run (DEC-003)."""
    links = registry.parse_chain(DEFAULT)
    before = list(links)
    chain_readiness(links, {"nvidia": "k"})
    assert links == before


# ------------------------------------------------ the handoff to the key gate

def _cfg(**keys):
    cfg = SimpleNamespace(ai_provider="chain", llm_chain="", allow_slow_chain=False)
    for name, (attr, _env) in config.PROVIDER_KEYS.items():
        setattr(cfg, attr, keys.get(name, ""))
    return cfg


def test_no_key_at_all_is_the_key_gates_case_and_it_catches_it():
    """The one seam where a hole could open: readiness defers "no key at all"
    to missing_provider_key. Asserted together, so if either side changes the
    job cannot slip between them."""
    cfg = _cfg()
    assert chain_not_ready(cfg) is None
    assert missing_provider_key(cfg) is not None


def test_the_cfg_adapter_agrees_with_the_pure_function():
    assert chain_not_ready(_cfg(nvidia="k")) is not None
    assert chain_not_ready(_cfg(nvidia="k", groq="k")) is None

    overridden = _cfg(nvidia="k")
    overridden.allow_slow_chain = True
    assert chain_not_ready(overridden) is None


# --------------------------------------------------------------------- CLI

def test_the_cli_flag_reaches_the_config(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    assert config.build_config(["--video", str(video)]).allow_slow_chain is False
    cfg = config.build_config(["--video", str(video), "--allow-slow-chain"])
    assert cfg.allow_slow_chain is True


def test_the_cli_gates_after_the_key_gate_and_before_the_probe():
    """Order matters: "no keys at all" must reach missing_provider_key first for
    its better message, and the probe must not be paid for a job that is about
    to be refused."""
    import pathlib

    text = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text()
    key_gate = text.index("missing_provider_key(cfg)")
    readiness = text.index("chain_not_ready(cfg)")
    probe = text.index("preflight_chain(cfg)")
    assert key_gate < readiness < probe
