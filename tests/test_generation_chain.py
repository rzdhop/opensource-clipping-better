"""Generation chains (DEC-096): image, image edit, video, TTS and vision share
LLM_CHAIN's grammar and hop rules -- and add three gates of their own: a paid
link needs ``allow_paid`` and a budget verdict, a local link needs a reachable
server, a free-tier link asks the limiter first. Nothing here needs a network
or an SDK.
"""

import pytest

from clipping.providers import errors, generation, registry
from clipping.providers.generation import (
    DEFAULT_CHAINS, ENV_NAMES, KINDS, GenRequest, GenResult, NoRunnableLink,
    chain_from_env, is_paid, local_url, parse_generation_chain, run_generation_chain,
)
from clipping.providers.registry import ChainError, Link, parse_chain


# ------------------------------------------------------------------ parsing

@pytest.mark.parametrize("kind", KINDS)
def test_every_default_chain_parses_and_every_link_offers_its_kind(kind):
    links = parse_generation_chain(kind, DEFAULT_CHAINS[kind])
    assert len(links) >= 4
    for link in links:
        assert link.provider in generation.KIND_PROVIDERS[kind], (kind, link)


def test_the_spec_notation_for_a_paid_link_is_tolerated_and_stripped():
    links = parse_generation_chain("image", "pollinations/flux, fal/flux-schnell*")
    assert links == [Link("pollinations", "flux"), Link("fal", "flux-schnell")]


def test_an_unknown_provider_names_the_generation_table_not_the_llm_one():
    with pytest.raises(ChainError) as excinfo:
        parse_generation_chain("image", "groq/flux")
    assert "fal" in str(excinfo.value) and "cloudflare" in str(excinfo.value)


def test_a_provider_that_does_not_offer_the_kind_is_refused():
    with pytest.raises(ChainError) as excinfo:
        parse_generation_chain("image", "edge/fr-FR-HenriNeural")
    assert "IMAGE_CHAIN" in str(excinfo.value)
    assert "tts" in str(excinfo.value)


def test_an_unknown_kind_is_refused():
    with pytest.raises(ChainError):
        parse_generation_chain("audio", "edge/x")


def test_the_llm_parser_and_table_are_untouched():
    """The generation providers never leak into the LLM key gate (DEC-073):
    ``registry.PROVIDERS`` keeps its five hosted providers + custom."""
    assert "fal" not in registry.PROVIDERS
    assert "local" not in registry.PROVIDERS
    with pytest.raises(ChainError):
        parse_chain("fal/flux-schnell")
    assert parse_chain("groq/openai/gpt-oss-120b") == [Link("groq", "openai/gpt-oss-120b")]


@pytest.mark.parametrize("kind", KINDS)
def test_chain_from_env_reads_its_own_variable(kind, monkeypatch):
    for name in ENV_NAMES.values():
        monkeypatch.delenv(name, raising=False)
    assert chain_from_env(kind) == parse_generation_chain(kind, DEFAULT_CHAINS[kind])
    monkeypatch.setenv(ENV_NAMES[kind], "local/comfyui" if kind != "tts" else "local/piper")
    assert [l.provider for l in chain_from_env(kind)] == ["local"]


# ---------------------------------------------------------------- local URLs

def test_local_urls_default_to_the_host_and_to_the_docker_gateway_in_a_container():
    assert local_url("comfyui", env={}, container=False) == "http://127.0.0.1:8188"
    assert local_url("ollama", env={}, container=False) == "http://127.0.0.1:11434"
    assert local_url("comfyui", env={}, container=True) == "http://host.docker.internal:8188"
    assert local_url("comfyui", env={"LOCAL_COMFYUI_URL": "http://gpu-box:8188/"}, container=True) == "http://gpu-box:8188"


# ------------------------------------------------------------------ paid?

@pytest.mark.parametrize("spec,paid", [
    ("gemini/nano-banana-2-lite", True), ("gemini/nano-banana-2", True),
    ("gemini/flash-lite", False), ("gemini/flash-lite-tts", False), ("gemini/veo-3.1-lite", True),
    ("fal/flux-schnell", True), ("fal/seedream-4-edit", True), ("openai/gpt-image-2-low", True),
    ("cloudflare/flux-1-schnell", False), ("pollinations/flux", False),
    ("edge/fr-FR-HenriNeural", False), ("local/comfyui", False), ("local/piper", False),
    ("openrouter/qwen/qwen3.8-27b:free", False),
])
def test_paid_is_decided_per_link(spec, paid):
    assert is_paid(registry.parse_spec(spec, providers=generation.GEN_PROVIDERS)) is paid


# ------------------------------------------------------------- the runner

class FakeAdapter:
    def __init__(self, *, fail=None, unavailable=(), est=None, reachable=True):
        self.calls = []
        self.probes = []
        self.fail = list(fail or [])
        self.unavailable = set(unavailable)
        self.est = est
        self.reachable = reachable

    def estimate(self, link, request):
        return self.est

    def probe(self, link, *, credentials, **_):
        self.probes.append(link)
        return self.reachable, "ok" if self.reachable else f"unreachable at {local_url('comfyui', env={}, container=False)}"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.calls.append((link, dict(credentials)))
        if link.model in self.unavailable:
            exc = RuntimeError(f"model {link.model} is not found")
            exc.status_code = 404
            raise exc
        if self.fail:
            raise self.fail.pop(0)
        return GenResult(provider=link.provider, model=link.model, paths=("out.png",), seed=7)


def run(kind, chain, adapters, **kw):
    log = []
    kw.setdefault("env", {"FAL_KEY": "f", "GOOGLE_API_KEY": "g", "OPENAI_API_KEY": "o",
                          "CLOUDFLARE_API_TOKEN": "c", "CLOUDFLARE_ACCOUNT_ID": "acc"})
    kw.setdefault("sleep_fn", lambda s: None)
    result = run_generation_chain(kind, parse_generation_chain(kind, chain), GenRequest(kind=kind, prompt="a kiwi"),
                                  adapters=adapters, on_log=log.append, **kw)
    return result, log


def test_a_provider_not_in_the_chain_is_never_contacted():
    """The invariant of DEC-003/023, mirrored from the LLM chain."""
    fal, openai = FakeAdapter(), FakeAdapter()
    adapters = {("image", "fal"): fal, ("image", "openai"): openai}
    (result, link), log = run("image", "fal/flux-schnell", adapters, allow_paid=True)
    assert [c[0].provider for c in fal.calls] == ["fal"]
    assert openai.calls == []
    assert link == Link("fal", "flux-schnell") and result.paths == ("out.png",)


def test_a_keyless_link_is_skipped_naming_the_variable():
    fal = FakeAdapter()
    with pytest.raises(NoRunnableLink) as excinfo:
        run("image", "fal/flux-schnell", {("image", "fal"): fal}, env={}, allow_paid=True)
    assert fal.calls == []
    assert any("⏭" in line and "FAL_KEY" in line for line in log_of(excinfo))


def log_of(excinfo):
    return [f"{label}: {reason}" for label, reason in excinfo.value.failures] + ["⏭ " + r for _, r in excinfo.value.failures]


def test_cloudflare_needs_both_of_its_variables():
    cf = FakeAdapter()
    with pytest.raises(NoRunnableLink) as excinfo:
        run("image", "cloudflare/flux-1-schnell", {("image", "cloudflare"): cf},
            env={"CLOUDFLARE_API_TOKEN": "t"})
    assert "CLOUDFLARE_ACCOUNT_ID" in str(excinfo.value)
    assert cf.calls == []


def test_a_paid_link_is_skipped_when_allow_paid_is_off_and_the_transport_never_called():
    fal = FakeAdapter(est=0.03)
    with pytest.raises(NoRunnableLink) as excinfo:
        run("image_edit", "fal/seedream-4-edit", {("image_edit", "fal"): fal}, allow_paid=False)
    assert fal.calls == []
    assert "allow_paid is off" in str(excinfo.value)


def test_a_paid_link_runs_only_after_the_budget_check_accepts_its_estimate():
    fal = FakeAdapter(est=0.03)
    seen = []

    def check(est, link):
        seen.append((est, link))

    (result, link), log = run("image_edit", "fal/seedream-4-edit", {("image_edit", "fal"): fal},
                              allow_paid=True, budget_check=check)
    assert seen == [(0.03, Link("fal", "seedream-4-edit"))]
    assert result.paid is True and result.est_cost == 0.03
    assert any("💸" in line and "0.030" in line for line in log)


def test_a_refused_estimate_moves_the_chain_on_with_the_refusal_printed():
    fal = FakeAdapter(est=0.03)

    def check(est, link):
        raise RuntimeError("refused: est $0.030 would exceed today's $3.00 cap")

    with pytest.raises(NoRunnableLink) as excinfo:
        run("image_edit", "fal/seedream-4-edit", {("image_edit", "fal"): fal}, allow_paid=True, budget_check=check)
    assert fal.calls == []
    assert "$3.00 cap" in str(excinfo.value)


def test_a_free_link_never_consults_the_budget_but_asks_the_limiter():
    pol = FakeAdapter()
    asked = []

    class Limiter:
        def acquire(self, provider):
            asked.append(provider)
            return None

    def check(est, link):
        raise AssertionError("a free link must not be budgeted")

    (result, link), log = run("image", "pollinations/flux", {("image", "pollinations"): pol},
                              budget_check=check, limiter=Limiter())
    assert asked == ["pollinations"] and result.paid is False and result.est_cost == 0.0


def test_a_spent_daily_allowance_moves_the_chain_on():
    cf, pol = FakeAdapter(), FakeAdapter()

    class Limiter:
        def acquire(self, provider):
            return "daily allowance spent (170/170 today)" if provider == "cloudflare" else None

    (result, link), log = run("image", "cloudflare/flux-1-schnell,pollinations/flux",
                              {("image", "cloudflare"): cf, ("image", "pollinations"): pol}, limiter=Limiter())
    assert cf.calls == [] and link.provider == "pollinations"
    assert any("⏳" in line and "170/170" in line for line in log)


def test_a_local_link_is_skipped_with_its_url_when_unreachable():
    comfy = FakeAdapter(reachable=False)
    pol = FakeAdapter()
    (result, link), log = run("image", "local/comfyui,pollinations/flux",
                              {("image", "local"): comfy, ("image", "pollinations"): pol})
    assert comfy.calls == [] and comfy.probes == [Link("local", "comfyui")]
    assert link.provider == "pollinations"
    assert any("⏭" in line and "unreachable at http://127.0.0.1:8188" in line for line in log)


def test_a_link_without_an_adapter_is_reported_not_called():
    """VIDEO_CHAIN parses and tests from phase 0; its adapters come in phase 6."""
    with pytest.raises(NoRunnableLink) as excinfo:
        run("video", DEFAULT_CHAINS["video"], {}, allow_paid=True)
    reasons = [reason for _, reason in excinfo.value.failures]
    assert len(reasons) == 5 and all("no adapter yet" in r for r in reasons)


@pytest.mark.parametrize("route,expected", [("local", ["local"]), ("api", ["pollinations"]), ("auto", ["local"])])
def test_the_route_filters_local_against_hosted_links(route, expected):
    comfy, pol = FakeAdapter(), FakeAdapter()
    (result, link), log = run("image", "local/comfyui,pollinations/flux",
                              {("image", "local"): comfy, ("image", "pollinations"): pol}, route=route)
    assert [link.provider] == expected


def test_a_retired_model_is_swapped_for_the_same_providers_fallback_on_the_same_key():
    """DEC-089 for generation: the swap stays inside the provider."""
    gem = FakeAdapter(unavailable={"nano-banana-2-lite"}, est=0.0336)
    (result, link), log = run("image_edit", "gemini/nano-banana-2-lite", {("image_edit", "gemini"): gem},
                              allow_paid=True, budget_check=lambda est, link: None)
    assert [c[0].model for c in gem.calls] == ["nano-banana-2-lite", "nano-banana-2"]
    assert link == Link("gemini", "nano-banana-2")
    assert any("↪" in line for line in log)


def test_a_bad_key_is_fatal_for_the_link_and_the_chain_moves_on():
    exc = RuntimeError("invalid api key")
    exc.status_code = 401
    fal, openai = FakeAdapter(fail=[exc]), FakeAdapter()
    (result, link), log = run("image", "fal/flux-schnell,openai/gpt-image-2-low",
                              {("image", "fal"): fal, ("image", "openai"): openai},
                              allow_paid=True, budget_check=lambda e, l: None)
    assert len(fal.calls) == 1 and link.provider == "openai"
    assert any("✖" in line and "fal/flux-schnell" in line for line in log)


def test_a_transient_failure_is_retried_once_on_the_same_link():
    exc = RuntimeError("upstream hiccup")
    exc.status_code = 503
    pol = FakeAdapter(fail=[exc])
    (result, link), log = run("image", "pollinations/flux", {("image", "pollinations"): pol})
    assert len(pol.calls) == 2 and link.provider == "pollinations"
    assert any("⚠️" in line for line in log)


def test_every_failure_reason_reaches_the_final_error():
    with pytest.raises(NoRunnableLink) as excinfo:
        run("image", "fal/flux-schnell,openai/gpt-image-2-low",
            {("image", "fal"): FakeAdapter(), ("image", "openai"): FakeAdapter()}, allow_paid=False)
    labels = [label for label, _ in excinfo.value.failures]
    assert labels == ["fal/flux-schnell", "openai/gpt-image-2-low"]
    assert isinstance(excinfo.value, errors.ProviderError)


def test_the_hop_lines_use_the_llm_chains_glyphs():
    fal = FakeAdapter(est=0.03)
    (result, link), log = run("image", "pollinations/flux,fal/flux-schnell",
                              {("image", "fal"): fal}, allow_paid=True, budget_check=lambda e, l: None)
    assert log[0].startswith("   ⏭ Skipping pollinations/flux: no adapter yet")
    assert any(line.startswith("   💸 fal/flux-schnell") for line in log)
    assert any(line.startswith("   🔁 fal/flux-schnell") for line in log)
    assert any(line.startswith("   ✅ fal/flux-schnell answered") for line in log)
