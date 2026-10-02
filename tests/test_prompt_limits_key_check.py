"""fal's free key check also reads each endpoint's published OpenAPI schema
(``https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=``, public: no
key goes to it) and reports the prompt limit it publishes, or that it
publishes none; ``POST /api/settings/check-video-keys`` stores what was read
next to the Settings file, where ``prompt_limits.limit_for`` prefers it over
the table. Every answer here is a fake transport's; the route test needs
fastapi and httpx.
"""

from __future__ import annotations

import json
import os

import pytest

from clipping.providers import prompt_limits, video
from clipping.providers.transport import APIConnectionError

from test_prompt_limits import KLING, KLING_APP, SCHEMA, SEEDANCE, SEEDANCE_APP, Routes, fal_schema


@pytest.fixture(autouse=True)
def _isolated_live_file(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))


PRICE = {"prices": [{"endpoint_id": KLING_APP, "unit_price": 0.042, "unit": "second", "currency": "USD"}]}


def test_the_fal_key_check_reports_the_published_prompt_limit():
    transport = Routes({"api.fal.ai/v1/models/pricing": (200, PRICE),
                        SCHEMA: (200, fal_schema(KLING_APP, {"type": "string", "maxLength": 2500}))})
    result = video.check_key(KLING, {"FAL_KEY": "fal-test-key"}, transport=transport)
    assert result["status"] == "ok"
    assert result["prompt_limit"]["status"] == "published" and result["prompt_limit"]["prompt_max_chars"] == 2500
    assert "prompt ≤ 2500 chars (fal's schema)" in result["text"]
    schema_call = next(call for call in transport.calls if SCHEMA in call["url"])
    assert "fal-test-key" not in json.dumps(schema_call)


def test_the_fal_key_check_says_when_no_limit_is_published():
    transport = Routes({"api.fal.ai/v1/models/pricing": (200, PRICE),
                        SCHEMA: (200, fal_schema(SEEDANCE_APP, {"type": "string"}))})
    result = video.check_key(SEEDANCE, {"FAL_KEY": "k"}, transport=transport)
    assert result["prompt_limit"]["status"] == "not_published"
    assert "not published" in result["text"] and "1500 chars" in result["text"]


def test_a_schema_failure_leaves_the_key_verdict_alone():
    transport = Routes({"api.fal.ai/v1/models/pricing": (200, PRICE), SCHEMA: (503, "busy")})
    result = video.check_key(KLING, {"FAL_KEY": "k"}, transport=transport)
    assert result["status"] == "ok" and result["prompt_limit"]["status"] == "failed"
    assert "could not be read" in result["text"]


def test_an_unreachable_fal_is_not_asked_for_its_schema():
    transport = Routes({"api.fal.ai": APIConnectionError("refused")})
    result = video.check_key(KLING, {"FAL_KEY": "k"}, transport=transport)
    assert result["status"] == "unreachable" and len(transport.calls) == 1


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
    monkeypatch.setenv("DISABLE_AUTH", "1")
    monkeypatch.setattr(worker, "_settings_env", {"FAL_KEY": "fal-test-key"})
    app = FastAPI()
    app.include_router(settings.router)
    with TestClient(app) as test_client:
        yield test_client, settings, monkeypatch


def test_the_key_check_route_reports_and_stores_the_prompt_limits(client, tmp_path):
    test_client, settings_route, monkeypatch = client
    transport = Routes({"api.fal.ai/v1/models/pricing": (200, PRICE),
                        f"{SCHEMA}?endpoint_id={KLING_APP}": (200, fal_schema(KLING_APP, {"type": "string",
                                                                                          "maxLength": 2500})),
                        SCHEMA: (200, fal_schema("other", {"type": "string"}))})
    monkeypatch.setattr(settings_route, "_TRANSPORT", transport)

    response = test_client.post("/api/settings/check-video-keys")

    assert response.status_code == 200, response.text
    rows = {row["label"]: row for row in response.json()["results"]}
    assert rows["fal/kling-2.5-turbo-std"]["prompt_limit"]["prompt_max_chars"] == 2500
    assert "prompt ≤ 2500 chars (fal's schema)" in rows["fal/kling-2.5-turbo-std"]["text"]
    assert rows["fal/seedance-1-pro-fast"]["prompt_limit"]["status"] == "not_published"
    assert rows["local/comfyui"]["prompt_limit"] is None
    stored = json.loads((tmp_path / "provider_limits.json").read_text(encoding="utf-8"))["links"]
    assert stored["fal/kling-2.5-turbo-std"]["prompt_max_chars"] == 2500
    assert stored["fal/seedance-1-pro-fast"]["status"] == "not_published"
    assert prompt_limits.limit_for(KLING).source.startswith("fal's schema")
    assert "fal-test-key" not in response.text


def test_the_dashboards_key_check_says_it_reads_the_prompt_limit():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "web", "dashboard", "src", "pages", "Settings.jsx"), encoding="utf-8") as fh:
        page = fh.read()
    block = page[page.index("function VideoKeyCheck"):]
    block = block[:block.index("\n}\n")]
    assert "prompt limit" in block
