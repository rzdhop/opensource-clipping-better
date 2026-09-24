"""The chain-readiness gate and the chain test, as the web API exposes them.

The text guards at the top run in CI, which installs pytest and nothing else
(DEC-012). The functional tests need fastapi and skip where it is absent. They
mount only the routers under test on a bare app -- no lifespan -- so nothing
here reads or writes the real outputs/jobs.json or data/settings.json.
"""

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
API = ROOT / "web" / "api"


# ------------------------------------------------------------ text guards (CI)

def test_the_settings_switch_reaches_the_job_config():
    """The web path never set this attribute, so the switch would have been
    stored, shown as on, and ignored by every job."""
    text = (API / "config_adapter.py").read_text(encoding="utf-8")
    assert 'allow_slow_chain=env_flag(env, "ALLOW_SLOW_CHAIN")' in text


def test_the_switch_is_persisted_but_is_not_a_secret():
    from web.api import settings_store

    assert "ALLOW_SLOW_CHAIN" in settings_store.PERSISTED_KEYS
    assert "ALLOW_SLOW_CHAIN" not in settings_store.SECRET_KEYS


def test_the_worker_gates_after_the_key_gate_and_before_the_probe():
    text = (API / "worker.py").read_text(encoding="utf-8")
    key_gate = text.index("missing_provider_key(cfg)")
    readiness = text.index("chain_not_ready(cfg")
    probe = text.index("preflight_chain(cfg")
    assert key_gate < readiness < probe


# ------------------------------------------------------------ functional

@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    """An empty Settings env backed by a throwaway file, and a clean process env."""
    pytest.importorskip("pydantic")
    from web.api import worker

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(worker, "_settings_env", {})
    for name in ("GROQ_API_KEY", "GOOGLE_API_KEY", "NVIDIA_API_KEY",
                 "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "LLM_CUSTOM_API_KEY",
                 "LLM_CHAIN", "ALLOW_SLOW_CHAIN"):
        monkeypatch.delenv(name, raising=False)
    return worker


@pytest.fixture
def client(settings_env, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.api.routes import jobs, settings

    monkeypatch.setenv("DISABLE_AUTH", "1")
    app = FastAPI()
    app.include_router(settings.router)
    app.include_router(jobs.router)
    with TestClient(app) as test_client:
        yield test_client


def test_the_switch_round_trips_through_settings(client, settings_env, tmp_path):
    assert client.get("/api/settings").json()["allow_slow_chain"] is False

    assert client.put("/api/settings", json={"allow_slow_chain": True}).json()[
        "allow_slow_chain"] is True
    assert settings_env.get_settings_env()["ALLOW_SLOW_CHAIN"] == "1"
    assert '"ALLOW_SLOW_CHAIN": "1"' in (tmp_path / "settings.json").read_text()


def test_turning_the_switch_off_clears_it_rather_than_storing_zero(
        client, settings_env, monkeypatch):
    """DEC-043, one layer down: a stored "0" reads as off but would shadow an
    ALLOW_SLOW_CHAIN=1 in .env forever, with no way back from the UI."""
    client.put("/api/settings", json={"allow_slow_chain": True})
    client.put("/api/settings", json={"allow_slow_chain": False})

    assert "ALLOW_SLOW_CHAIN" not in settings_env.get_settings_env()
    monkeypatch.setenv("ALLOW_SLOW_CHAIN", "1")
    assert client.get("/api/settings").json()["allow_slow_chain"] is True


def test_an_unrelated_settings_save_leaves_the_switch_alone(client, settings_env):
    client.put("/api/settings", json={"allow_slow_chain": True})
    client.put("/api/settings", json={"default_clips": 3})
    assert settings_env.get_settings_env()["ALLOW_SLOW_CHAIN"] == "1"


def test_the_job_config_carries_the_switch(settings_env, tmp_path, monkeypatch):
    from web.api import config_adapter

    # The builder creates the job's output directory under the real project
    # root; this test has no business leaving one behind.
    monkeypatch.setattr(config_adapter.os, "makedirs", lambda *a, **k: None)
    cfg = config_adapter.build_config_from_payload(
        {"upload_filename": "v.mp4"}, "job1", env_overrides={})
    assert cfg.allow_slow_chain is False
    cfg = config_adapter.build_config_from_payload(
        {"upload_filename": "v.mp4"}, "job2", env_overrides={"ALLOW_SLOW_CHAIN": "1"})
    assert cfg.allow_slow_chain is True


# ------------------------------------------------ POST /api/jobs (Stage 5)

def test_the_route_refuses_before_the_job_exists():
    """The refusal must precede store.create_job, or a refused job is still
    written to the list (and, via the config builder, to disk)."""
    text = (API / "routes" / "jobs.py").read_text(encoding="utf-8")
    body = text[text.index("async def create_job("):text.index("async def attach_source(")]
    assert body.index("_slow_chain_refusal(payload)") < body.index("store.create_job(")


@pytest.fixture
def created(client, monkeypatch):
    """Record what reaches the store and the queue, instead of doing it."""
    from web.api import store, worker

    calls = {"create": [], "submit": []}

    def create_job(**kwargs):
        calls["create"].append(kwargs)
        return "job1"

    async def submit_job(job_id, payload):
        calls["submit"].append((job_id, payload))

    monkeypatch.setattr(store, "create_job", create_job)
    monkeypatch.setattr(store, "get_job", lambda job_id: {"id": job_id})
    monkeypatch.setattr(worker, "submit_job", submit_job)
    return calls


def test_only_the_floor_keyed_is_refused_with_both_signup_urls(
        client, created, settings_env):
    from clipping.providers import registry

    settings_env.set_settings_env({"NVIDIA_API_KEY": "k"}, persist=False)
    r = client.post("/api/jobs", json={"upload_filename": "v.mp4"})

    assert r.status_code == 400
    detail = r.json()["detail"]
    for name in ("groq", "gemini"):
        assert registry.PROVIDERS[name].signup_url in detail
        assert registry.PROVIDERS[name].env_key in detail
    assert "Settings" in detail
    assert created["create"] == [] and created["submit"] == []


def test_a_primary_key_lets_the_job_through(client, created, settings_env):
    settings_env.set_settings_env(
        {"NVIDIA_API_KEY": "k", "GROQ_API_KEY": "g"}, persist=False)
    r = client.post("/api/jobs", json={"upload_filename": "v.mp4"})

    assert r.status_code == 201
    assert len(created["submit"]) == 1


def test_a_key_in_the_process_env_counts_too(client, created, settings_env, monkeypatch):
    """.env keys never pass through the Settings page, and still count."""
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert client.post("/api/jobs", json={"upload_filename": "v.mp4"}).status_code == 201


def test_the_settings_switch_lets_it_run(client, created, settings_env):
    settings_env.set_settings_env(
        {"NVIDIA_API_KEY": "k", "ALLOW_SLOW_CHAIN": "1"}, persist=False)
    assert client.post("/api/jobs", json={"upload_filename": "v.mp4"}).status_code == 201


def test_a_per_job_chain_that_names_no_primary_is_the_users_to_run(
        client, created, settings_env):
    settings_env.set_settings_env({"NVIDIA_API_KEY": "k"}, persist=False)
    r = client.post("/api/jobs", json={"upload_filename": "v.mp4",
                                       "llm_chain": "nvidia/some-model"})
    assert r.status_code == 201


def test_a_render_only_rerun_needs_no_key_and_meets_no_gate(
        client, created, settings_env):
    """It calls no provider (RC-B7). reuse_job_id alone defaults
    load_gemini_json on, which is what makes it render-only."""
    settings_env.set_settings_env({"NVIDIA_API_KEY": "k"}, persist=False)
    r = client.post("/api/jobs", json={"reuse_job_id": "abc123"})
    assert r.status_code == 201


def test_a_rerun_that_asks_for_a_fresh_analysis_is_gated(
        client, created, settings_env):
    settings_env.set_settings_env({"NVIDIA_API_KEY": "k"}, persist=False)
    r = client.post("/api/jobs", json={"reuse_job_id": "abc123",
                                       "load_gemini_json": False})
    assert r.status_code == 400


# ------------------------------------ POST /api/settings/test-chain (Stage 6)

# The route now sends the real pass-A request (DEC-078), so the fake answers it
# the way a good model does: the fixture's one clip, beats 4-9. A ping ignores
# the content.
_FOUND = (
    '{"candidates": [{"b0": 4, "b1": 9, "score": 85, '
    '"gist": "ships on a Friday", "kind": "story"}]}'
)


class _Reply:
    choices = [type("C", (), {"message": type("M", (), {"content": _FOUND})()})()]
    usage = None


def _gone():
    err = type("NotFoundError", (Exception,), {})(
        "Error code: 404 - This model is no longer available to new users.")
    err.status_code = 404
    return err


def registry_default(provider):
    from clipping.providers import registry

    return {
        "openrouter": registry.OPENROUTER_DEFAULT_MODEL,
        "gemini": registry.GEMINI_DEFAULT_MODEL,
    }[provider]


@pytest.fixture
def fake_providers(monkeypatch):
    """Replace the SDK client: each provider answers or raises, instantly."""
    from types import SimpleNamespace

    from clipping.providers import llm

    behaviour = {}
    contacted = []

    def build_client(link, api_key=None, timeout=None):
        def create(**_):
            contacted.append(link.provider)
            outcome = behaviour.get(link.provider, _Reply())
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(llm, "build_client", build_client)
    return SimpleNamespace(behaviour=behaviour, contacted=contacted)


def test_every_link_is_reported_not_just_the_first(client, settings_env, fake_providers):
    fake_providers.behaviour["nvidia"] = TimeoutError("Request timed out.")
    settings_env.set_settings_env({"GROQ_API_KEY": "g", "NVIDIA_API_KEY": "n"},
                                  persist=False)

    body = client.post("/api/settings/test-chain", json={}).json()

    assert [r["provider"] for r in body["results"]] == [
        "groq", "gemini", "openrouter", "mistral", "nvidia"]
    assert [r["status"] for r in body["results"]] == [
        "ok", "no_key", "no_key", "no_key", "failed"]
    assert body["results"][1]["env_key"] == "GOOGLE_API_KEY"
    assert body["results"][1]["signup_url"].startswith("https://")
    assert "TimeoutError" in body["results"][-1]["reason"]
    assert body["results"][-1]["probe_timeout_seconds"] == 120.0
    assert body["live_link"].startswith("groq/")
    assert body["ready"] is True
    # Providers are asked at the same time now, so order is asserted on the
    # results above, and here only who was asked.
    assert sorted(fake_providers.contacted) == ["groq", "nvidia"]


def test_a_primary_that_fails_the_work_is_not_ready_even_when_the_floor_answers(
        client, settings_env, fake_providers):
    """The report the human got on 2026-09-24: Gemini 404, NVIDIA answered,
    and the summary said "Jobs can start". A job would have run on the floor
    alone. The key gate cannot see this; only the real request can."""
    fake_providers.behaviour["gemini"] = _gone()
    settings_env.set_settings_env({"GOOGLE_API_KEY": "g", "NVIDIA_API_KEY": "n"},
                                  persist=False)

    body = client.post("/api/settings/test-chain", json={}).json()

    assert body["verdict"] == "floor_only"
    assert body["ready"] is False
    assert body["live_link"].startswith("nvidia/")
    gemini = next(r for r in body["results"] if r["provider"] == "gemini")
    assert gemini["status"] == "failed"
    assert "gemini" in body["message"]


def test_every_row_says_what_the_real_request_found(
        client, settings_env, fake_providers):
    settings_env.set_settings_env({"GOOGLE_API_KEY": "g"}, persist=False)
    body = client.post("/api/settings/test-chain", json={}).json()

    gemini = next(r for r in body["results"] if r["provider"] == "gemini")
    assert gemini["status"] == "ok"
    assert gemini["kind"] == "work"
    assert gemini["candidates"] == 1
    assert gemini["found_moment"] is True
    assert gemini["used_model"] == gemini["model"]
    assert body["verdict"] == "ready" and body["ready"] is True


def test_a_key_outside_the_chain_is_reported_unused_and_never_contacted(
        client, settings_env, fake_providers):
    """DEC-023: a provider the chain does not name is never contacted -- not
    even by a diagnostic. The row says how to put the key to work instead."""
    settings_env.set_settings_env(
        {"GOOGLE_API_KEY": "g", "OPENROUTER_API_KEY": "o"}, persist=False)

    body = client.post("/api/settings/test-chain",
                       json={"llm_chain": "gemini/some-model"}).json()

    assert fake_providers.contacted == ["gemini"]
    unused = [r for r in body["results"] if r["status"] == "unused"]
    assert [r["provider"] for r in unused] == ["openrouter"]
    assert f"openrouter/{registry_default('openrouter')}" in unused[0]["note"]


def test_a_chain_with_no_key_at_all_says_so(client, settings_env, fake_providers):
    settings_env.set_settings_env({}, persist=False)
    body = client.post("/api/settings/test-chain", json={}).json()
    assert body["verdict"] == "dead" and body["ready"] is False
    assert fake_providers.contacted == []
    assert "GOOGLE_API_KEY" in body["message"]


def test_the_route_budget_is_the_registrys():
    text = (API / "routes" / "settings.py").read_text(encoding="utf-8")
    body = text[text.index("async def run_chain_test("):]
    assert "registry.diagnostic_budget(" in body
    assert "probe_timeout(link) for link in links" not in body


def test_a_live_floor_alone_is_not_ready_and_says_why(
        client, settings_env, fake_providers):
    """NVIDIA answering is a live chain, but not one a job may start on."""
    settings_env.set_settings_env({"NVIDIA_API_KEY": "n"}, persist=False)

    body = client.post("/api/settings/test-chain", json={}).json()

    assert body["live_link"].startswith("nvidia/")
    assert body["ready"] is False
    assert "GROQ_API_KEY" in body["message"]


def test_nothing_answering_explains_every_link(client, settings_env, fake_providers):
    fake_providers.behaviour["groq"] = TimeoutError("x")
    settings_env.set_settings_env({"GROQ_API_KEY": "g"}, persist=False)

    body = client.post("/api/settings/test-chain", json={}).json()

    assert body["ready"] is False and body["live_link"] is None
    assert "No provider in the chain answered" in body["message"]


def test_a_requested_chain_is_the_one_tested(client, settings_env, fake_providers):
    settings_env.set_settings_env({"MISTRAL_API_KEY": "m"}, persist=False)
    body = client.post("/api/settings/test-chain",
                       json={"llm_chain": "mistral/some-model"}).json()
    assert body["chain"] == "mistral/some-model"
    assert fake_providers.contacted == ["mistral"]


def test_a_malformed_chain_is_a_400_not_a_crash(client, settings_env, fake_providers):
    r = client.post("/api/settings/test-chain", json={"llm_chain": "nosuch/x"})
    assert r.status_code == 400
    assert fake_providers.contacted == []


def test_the_request_cannot_carry_a_base_url():
    """custom/<model> must resolve its host from the environment only."""
    pytest.importorskip("pydantic")
    from web.api.models import ChainTestRequest

    assert set(ChainTestRequest.model_fields) == {"llm_chain"}


def test_the_probe_runs_off_the_event_loop_and_off_the_job_pool():
    text = (API / "routes" / "settings.py").read_text(encoding="utf-8")
    body = text[text.index("async def run_chain_test("):]
    assert "asyncio.to_thread(_probe_every_link" in body
    assert "_executor" not in body


# ------------------------------------------- the dashboard's view (Stage 7)

def test_settings_report_the_same_verdict_the_job_route_would(
        client, created, settings_env):
    """The page shows this instead of re-deriving the rule in JavaScript."""
    settings_env.set_settings_env({"NVIDIA_API_KEY": "k"}, persist=False)
    reason = client.get("/api/settings").json()["chain_blocked_reason"]
    refusal = client.post("/api/jobs", json={"upload_filename": "v.mp4"}).json()["detail"]
    assert reason and reason == refusal

    settings_env.set_settings_env({"GROQ_API_KEY": "g"}, persist=False)
    assert client.get("/api/settings").json()["chain_blocked_reason"] == ""


def test_a_saved_chain_that_names_no_primary_is_not_reported_blocked(
        client, settings_env, monkeypatch):
    monkeypatch.setenv("LLM_CHAIN", "nvidia/some-model")
    settings_env.set_settings_env({"NVIDIA_API_KEY": "k"}, persist=False)
    assert client.get("/api/settings").json()["chain_blocked_reason"] == ""


def _literal_values(source, class_name, field):
    import re

    block = source[source.index(f"class {class_name}("):]
    block = block[: block.index("\nclass ", 1)] if "\nclass " in block[1:] else block
    match = re.search(rf"{field}: Literal\[([^\]]+)\]", block)
    assert match, f"{class_name}.{field} is not a Literal"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def _js_object_keys(source, const):
    """The top-level keys of ``const NAME = { ... }``, skipping nested objects."""
    import re

    start = source.index(f"const {const} = {{") + len(f"const {const} = {{")
    depth, top = 0, []
    for char in source[start:]:
        if char == "{":
            depth += 1
        elif char == "}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0:
            top.append(char)
    return set(re.findall(r"(\w+):", "".join(top)))


def test_every_status_and_verdict_the_backend_sends_is_drawn_by_the_page():
    """Read as text: CI has no pydantic and no node (DEC-012). A status the page
    has no glyph for renders as a bare dot, and a verdict it has no colour for
    renders as an error -- both silent."""
    models = (API / "models.py").read_text(encoding="utf-8")
    page = (API.parent / "dashboard" / "src" / "pages" / "Settings.jsx").read_text(
        encoding="utf-8")

    statuses = _literal_values(models, "ChainLinkResult", "status")
    verdicts = _literal_values(models, "ChainTestResponse", "verdict")

    assert statuses == {"ok", "alive", "failed", "no_key", "unused"}
    assert statuses <= _js_object_keys(page, "STATUS_GLYPH")
    assert verdicts <= _js_object_keys(page, "VERDICT_STYLE")
