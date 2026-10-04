"""Every liveness question to an Anthropic link is free (plan 23 stage D1).

Every request on the Anthropic API is billed, so the preflight ping, the
Settings "Test provider chain" diagnostic and the new free route
``POST /api/settings/check-anthropic-key`` ask only ``models.retrieve``
(``AnthropicChat.check_model``) -- never ``messages.create``. The fake SDK
client below fails the test the moment a completion is attempted.

The llm-layer tests are stdlib + pytest (DEC-012); the route tests need
fastapi and skip where it is absent, mounting only the settings router on a
bare app so nothing reads or writes the real data/settings.json.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipping.providers import anthropic_llm, llm, pacing
from clipping.providers.registry import Link

SONNET = Link("anthropic", "claude-sonnet-5-5")
OPUS = Link("anthropic", "claude-opus-5-5@xhigh")
KEY = "test-anthropic-key"
LISTED = "key valid, model available — not exercised: every request is billed"


class NotFoundError(Exception):
    status_code = 404


class NoCompletionSdk:
    """``models.retrieve`` answers (or raises *missing*'s error for a model
    in it); any completion fails the test."""

    def __init__(self, missing=()):
        self.missing = set(missing)
        self.retrieved = []
        self.built = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.messages = SimpleNamespace(create=self._create)
        self.models = SimpleNamespace(retrieve=self._retrieve)

    def _create(self, **_request):
        raise AssertionError("a completion was sent to a billed-per-request provider")

    def _retrieve(self, model_id):
        self.retrieved.append(model_id)
        if model_id in self.missing:
            raise NotFoundError(f"model: {model_id} not found")
        return SimpleNamespace(id=model_id)


@pytest.fixture(autouse=True)
def _reset():
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    pacing.reset_limiters()
    yield
    llm.reset_model_fallbacks()


@pytest.fixture
def sdk(monkeypatch):
    fake = NoCompletionSdk()

    def make(**kwargs):
        fake.built.append(kwargs)
        return fake

    monkeypatch.setattr(anthropic_llm, "_sdk_client", make)
    return fake


WORK = {"system": "s", "user": "u", "schema": None, "max_tokens": 50}


def test_the_preflight_ping_asks_the_model_lookup(sdk):
    live, results, value = llm.probe_chain([OPUS], {"anthropic": KEY}, on_log=lambda line: None)
    assert live == OPUS and value is None
    [(label, reason, _elapsed, kind)] = results
    assert (label, reason, kind) == ("anthropic/claude-opus-5-5@xhigh", "ok", "listed")
    assert sdk.retrieved == ["claude-opus-5-5"]


def test_the_diagnostic_never_sends_the_real_request(sdk):
    [probe] = llm.diagnose_chain([SONNET], {"anthropic": KEY}, WORK, judge=lambda value: 1 / 0,
                                 on_log=lambda line: None)
    assert (probe.kind, probe.reason, probe.value, probe.used_model) == ("listed", "ok", None, "claude-sonnet-5-5")
    assert sdk.retrieved == ["claude-sonnet-5-5"]


def test_a_model_the_key_cannot_use_is_reported_on_its_row(sdk):
    sdk.missing.add("claude-sonnet-5-5")
    [probe] = llm.diagnose_chain([SONNET], {"anthropic": KEY}, WORK, on_log=lambda line: None)
    assert probe.kind == "listed"
    assert "NotFoundError" in probe.reason


def test_a_ping_through_a_meter_like_factory_stays_free(sdk):
    """Whatever factory builds the client, the free probe goes to check_model."""
    seen = []

    def factory(link, *, api_key, timeout):
        seen.append(link)
        return llm.build_client(link, api_key=api_key, timeout=timeout)

    _none, exc = llm._ping_once(SONNET, KEY, 45.0, on_log=lambda line: None, client_factory=factory)
    assert exc is None and seen == [SONNET] and sdk.retrieved == ["claude-sonnet-5-5"]


# ------------------------------------------------------------- the routes

@pytest.fixture
def client(tmp_path, monkeypatch, sdk):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.api import worker
    from web.api.routes import settings

    from clipping.config import PROVIDER_KEYS

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(worker, "_settings_env", {})
    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in ("LLM_CHAIN", "STORY_LLM_CHAIN", "STORY_LLM_PREMIUM_CHAIN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DISABLE_AUTH", "1")
    app = FastAPI()
    app.include_router(settings.router)
    with TestClient(app) as test_client:
        yield SimpleNamespace(http=test_client, worker=worker)


def test_the_key_is_saved_as_a_secret_and_reported_as_a_boolean(client):
    from web.api import settings_store

    assert "ANTHROPIC_API_KEY" in settings_store.PERSISTED_KEYS
    assert "ANTHROPIC_API_KEY" in settings_store.SECRET_KEYS
    assert client.http.get("/api/settings").json()["anthropic_api_key_set"] is False
    body = client.http.put("/api/settings", json={"anthropic_api_key": KEY}).json()
    assert body["anthropic_api_key_set"] is True
    assert KEY not in str(body)


def test_check_anthropic_key_asks_each_anthropic_link_for_free(client, sdk):
    client.worker.set_settings_env({
        "STORY_LLM_PREMIUM_CHAIN": "anthropic/claude-sonnet-5-5,anthropic/claude-opus-5-5@xhigh,"
                                   "gemini/gemini-3.5-flash-lite",
        "ANTHROPIC_API_KEY": KEY,
    })
    body = client.http.post("/api/settings/check-anthropic-key").json()

    assert body["verdict"] == "ready"
    assert [row["label"] for row in body["results"]] == ["anthropic/claude-sonnet-5-5",
                                                         "anthropic/claude-opus-5-5@xhigh"]
    assert all(row["status"] == "ok" and LISTED in row["text"] for row in body["results"])
    assert sdk.retrieved == ["claude-sonnet-5-5", "claude-opus-5-5"]
    assert sdk.built and all(kwargs["api_key"] == KEY for kwargs in sdk.built)
    assert KEY not in str(body)


def test_check_anthropic_key_without_a_key_or_a_link(client, sdk):
    client.worker.set_settings_env({"STORY_LLM_PREMIUM_CHAIN": "anthropic/claude-sonnet-5-5"})
    body = client.http.post("/api/settings/check-anthropic-key").json()
    assert body["verdict"] == "blocked"
    assert body["results"][0]["status"] == "no_key"

    client.worker.set_settings_env({"STORY_LLM_PREMIUM_CHAIN": "gemini/gemini-3.5-flash-lite",
                                    "ANTHROPIC_API_KEY": KEY})
    body = client.http.post("/api/settings/check-anthropic-key").json()
    assert (body["verdict"], body["results"]) == ("blocked", [])
    assert sdk.retrieved == []


def test_check_anthropic_key_reports_a_missing_model(client, sdk):
    sdk.missing.add("claude-opus-5-5")
    client.worker.set_settings_env({"STORY_LLM_PREMIUM_CHAIN": "anthropic/claude-opus-5-5",
                                    "ANTHROPIC_API_KEY": KEY})
    body = client.http.post("/api/settings/check-anthropic-key").json()
    assert body["verdict"] == "blocked"
    assert body["results"][0]["status"] == "no_model"


def test_a_chain_test_row_for_an_anthropic_link_says_listed(client, sdk):
    client.worker.set_settings_env({"ANTHROPIC_API_KEY": KEY})
    body = client.http.post("/api/settings/test-chain", json={"llm_chain": "anthropic/claude-sonnet-5-5"}).json()
    [row] = body["results"]
    assert (row["status"], row["kind"]) == ("listed", "listed")
    assert LISTED in row["note"]
    assert sdk.retrieved == ["claude-sonnet-5-5"]
