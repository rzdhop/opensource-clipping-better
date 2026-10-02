"""Asking the video providers whether a key works, for free (the human,
2026-10-02: "ask the provider for video and check api key").

The generation-chain test never calls a hosted video link (RC-V8: it reports
the key's presence and an estimate), so a wrong FAL_KEY showed only when the
first clip was bought. ``video.check_key`` asks without generating: fal's
Platform API pricing for the model's endpoint (``Authorization: Key``),
Gemini's ``models.get`` for the Veo model (``x-goog-api-key``). ``POST
/api/settings/check-video-keys`` runs it over the video chain. Every answer
here is a fake transport's; the route test needs fastapi and httpx.
"""

from __future__ import annotations

import json

import pytest

from clipping.providers import video
from clipping.providers.registry import Link
from clipping.providers.transport import APIConnectionError, Response

SEEDANCE = Link("fal", "seedance-1-pro-fast")
SEEDANCE_APP = "fal-ai/bytedance/seedance/v1/pro/fast/image-to-video"
VEO = Link("gemini", "veo-3.1-lite")
FAL = {"FAL_KEY": "fal-test-key"}
GEMINI = {"GEMINI_PAID_API_KEY": "gemini-test-key"}


class Transport:
    """One answer for every request; records them."""

    def __init__(self, status, payload=None, *, raises=None):
        self.status, self.payload, self.raises = status, payload, raises
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body})
        if self.raises is not None:
            raise self.raises
        body = json.dumps(self.payload).encode() if isinstance(self.payload, (dict, list)) else self.payload
        return Response(self.status, {}, body)


def _only_a_get(transport, host):
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["method"] == "GET" and call["body"] is None and host in call["url"]
    return call


def test_fal_answers_the_key_and_the_live_price_without_a_queue_request():
    transport = Transport(200, {"prices": [{"endpoint_id": SEEDANCE_APP, "unit_price": 0.022, "unit": "second",
                                            "currency": "USD"}], "has_more": False})

    result = video.check_key(SEEDANCE, FAL, transport=transport)

    call = _only_a_get(transport, "api.fal.ai/v1/models/pricing")
    assert "queue.fal.run" not in call["url"]  # nothing submitted, nothing billed
    assert f"endpoint_id={SEEDANCE_APP}" in call["url"]
    assert call["headers"]["Authorization"] == "Key fal-test-key"
    assert result["status"] == "ok" and result["price"] == {"unit_price": 0.022, "unit": "second", "currency": "USD"}
    assert "0.022" in result["text"] and "fal-test-key" not in result["text"]


@pytest.mark.parametrize(("status", "expected"), [(401, "bad_key"), (403, "bad_key"), (404, "no_model"),
                                                  (500, "failed")])
def test_fal_refusals_are_named(status, expected):
    transport = Transport(status, {"detail": "nope"})
    result = video.check_key(SEEDANCE, FAL, transport=transport)
    assert result["status"] == expected
    assert f"HTTP {status}" in result["text"] and "fal-test-key" not in result["text"]


def test_fal_listing_no_price_for_the_model_is_no_model():
    result = video.check_key(SEEDANCE, FAL, transport=Transport(200, {"prices": []}))
    assert result["status"] == "no_model" and SEEDANCE_APP in result["text"]


def test_gemini_veo_is_checked_with_models_get_and_the_header_key():
    transport = Transport(200, {"name": "models/veo-3.1-lite-generate-preview",
                                "supportedGenerationMethods": ["predictLongRunning"]})

    result = video.check_key(VEO, GEMINI, transport=transport)

    call = _only_a_get(transport, "generativelanguage.googleapis.com/v1beta/models/veo-3.1-lite-generate-preview")
    assert call["headers"]["x-goog-api-key"] == "gemini-test-key" and "key=" not in call["url"]
    assert result["status"] == "ok" and "predictLongRunning" in result["text"] and "billed" in result["text"]


def test_gemini_invalid_key_is_bad_key():
    payload = {"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT",
                         "details": [{"reason": "API_KEY_INVALID"}]}}
    result = video.check_key(VEO, GEMINI, transport=Transport(400, payload))
    assert result["status"] == "bad_key" and "GEMINI_PAID_API_KEY" in result["text"]


def test_an_unreachable_provider_is_reported_not_raised():
    result = video.check_key(SEEDANCE, FAL, transport=Transport(0, raises=APIConnectionError("refused")))
    assert result["status"] == "unreachable" and "APIConnectionError" in result["text"]


def test_a_link_without_a_key_check_is_refused_before_anything_is_sent():
    transport = Transport(200, {})
    with pytest.raises(ValueError):
        video.check_key(Link("local", "comfyui"), {}, transport=transport)
    assert transport.calls == []


# ------------------------------------------------------------ the route

@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipping.config import PROVIDER_KEYS
    from web.api import worker
    from web.api.routes import settings

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in ("VIDEO_CHAIN", "ALLOW_PAID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setenv("DISABLE_AUTH", "1")
    monkeypatch.setattr(worker, "_settings_env", {"FAL_KEY": "fal-test-key"})
    app = FastAPI()
    app.include_router(settings.router)
    with TestClient(app) as test_client:
        yield test_client, settings, monkeypatch


def test_the_route_asks_each_keyed_hosted_video_link_and_never_generates(client):
    test_client, settings_route, monkeypatch = client
    transport = Transport(200, {"prices": [{"endpoint_id": SEEDANCE_APP, "unit_price": 0.022, "unit": "second",
                                            "currency": "USD"}]})
    monkeypatch.setattr(settings_route, "_TRANSPORT", transport)

    response = test_client.post("/api/settings/check-video-keys")

    assert response.status_code == 200, response.text
    data = response.json()
    rows = {row["label"]: row for row in data["results"]}
    assert rows["local/comfyui"]["status"] == "skipped"
    assert rows["gemini/veo-3.1-lite"]["status"] == "no_key"
    keyed = [row for row in data["results"] if row["provider"] == "fal"]
    assert keyed and all(row["status"] in ("ok", "no_model") for row in keyed)
    assert rows["fal/seedance-1-pro-fast"]["status"] == "ok"
    assert data["verdict"] == "ready" and "Nothing was generated" in data["message"]
    assert all(call["method"] == "GET" and "api.fal.ai" in call["url"] for call in transport.calls)
    assert len(transport.calls) == len(keyed)
    assert "fal-test-key" not in response.text
