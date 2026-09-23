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
