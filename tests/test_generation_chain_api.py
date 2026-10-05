"""The settings surface of the generation chains (spec 8.6, DEC-103).

``GET /api/settings`` reports every chain's links (keyed, paid, adapter,
allowed), the new keys' ``_set`` flags, the local URLs and today's free
usage; ``POST /api/settings/test-generation-chain`` runs a chain's free and
local links and only *reports* the paid ones -- a paid link is called only
when named in ``link``, at most once, after the budget verdict, and the
call is booked in the chain-test ledger and the daily spend. Samples are
served through the existing signed outputs route under the reserved id
``_chain_test``. Everything here runs offline through a fake transport.
"""

import json
import pathlib
import re

import pytest

from clipping.providers.transport import APIConnectionError, Response

ROOT = pathlib.Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
GEN_VARS = ("FAL_KEY", "OPENAI_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "POLLINATIONS_API_KEY",
            "ELEVENLABS_API_KEY", "IMAGE_CHAIN", "IMAGE_EDIT_CHAIN", "VIDEO_CHAIN", "TTS_CHAIN", "VISION_CHAIN",
            "LOCAL_COMFYUI_URL", "LOCAL_OLLAMA_URL",
            "ALLOW_PAID", "PER_EPISODE_CAP_USD", "DAILY_CAP_USD", "PER_STORY_CAP_USD", "BUDGET_PROFILE")


class FakeTransport:
    """Answers by URL substring, in order of the rules; records every call."""

    def __init__(self, rules):
        self.rules = list(rules)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body})
        for needle, answer in self.rules:
            if needle in url:
                if isinstance(answer, Exception):
                    raise answer
                status, payload = answer
                if isinstance(payload, (dict, list)):
                    payload = json.dumps(payload).encode()
                return Response(status, {}, payload)
        raise AssertionError(f"unexpected request {method} {url}")

    def urls(self):
        return [c["url"] for c in self.calls]


@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    pytest.importorskip("pydantic")
    from web.api import worker
    from clipping.providers import budget, limits

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(worker, "_settings_env", {})
    for name in ("GROQ_API_KEY", "GOOGLE_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY",
                 "LLM_CUSTOM_API_KEY", "LLM_CHAIN", "ALLOW_SLOW_CHAIN") + GEN_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "spend.json"))
    limits.reset()
    budget.reset()
    yield worker
    limits.reset()
    budget.reset()


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    from web.api.routes import files, settings as settings_route

    out = tmp_path / "outputs"
    out.mkdir()
    monkeypatch.setattr(files, "OUTPUTS_DIR", str(out))
    monkeypatch.setattr(settings_route, "CHAIN_TEST_LEDGER", str(tmp_path / "chain_test_ledger.json"))
    return out


@pytest.fixture
def transport(monkeypatch):
    from web.api.routes import settings as settings_route

    fake = FakeTransport([])
    monkeypatch.setattr(settings_route, "_TRANSPORT", fake)
    return fake


def make_client(monkeypatch, *, disable_auth=True):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.api.routes import files, settings

    if disable_auth:
        monkeypatch.setenv("DISABLE_AUTH", "1")
    else:
        monkeypatch.delenv("DISABLE_AUTH", raising=False)
    app = FastAPI()
    app.include_router(settings.router)
    app.include_router(files.router)
    return TestClient(app)


@pytest.fixture
def client(settings_env, outputs, transport, monkeypatch):
    with make_client(monkeypatch) as test_client:
        yield test_client


# ---------------------------------------------------------------- GET / PUT

def test_get_settings_reports_the_generation_surface(client):
    data = client.get("/api/settings").json()
    for name in ("fal_key_set", "openai_api_key_set", "cloudflare_api_token_set", "cloudflare_account_id_set", "pollinations_api_key_set"):
        assert data[name] is False, name
    chains = data["generation_chains"]
    assert set(chains) == {"image", "image_edit", "video", "tts", "vision"}
    assert chains["image_edit"]["env"] == "IMAGE_EDIT_CHAIN"
    assert chains["image_edit"]["source"] == "default"
    rows = chains["image_edit"]["links"]
    assert [r["label"] for r in rows][:2] == ["local/comfyui", "gemini/nano-banana-2-lite"]
    seedream = next(r for r in rows if r["label"] == "fal/seedream-4-edit")
    assert seedream["paid"] is True and seedream["keyed"] is False and seedream["adapter"] is True
    assert seedream["missing_keys"] == ["FAL_KEY"] and seedream["allowed"] is False
    assert seedream["est_usd"] == 0.03
    # Plan 23 stage C3 re-pin: six links (fal/ltx-2.5-fast last).
    # Every video link has its adapter: local/comfyui since phase 6 stage 4, the hosted ones since stage 3.
    assert [r["adapter"] for r in chains["video"]["links"]] == [True, True, True, True, True, True]
    assert data["local_comfyui_url"] == "http://127.0.0.1:8188" and data["local_ollama_url"] == "http://127.0.0.1:11434"
    assert "day" in data["usage_today"] and data["usage_today"]["cloudflare"]["rpd"] == 170
    assert data["spend_today_usd"] == 0.0


def test_new_keys_and_local_urls_round_trip_and_clear(client, settings_env, tmp_path):
    updated = client.put("/api/settings", json={"fal_key": "fk", "cloudflare_account_id": "acc", "local_comfyui_url": "http://gpu:8188/"}).json()
    assert updated["fal_key_set"] is True and updated["cloudflare_account_id_set"] is True
    assert updated["local_comfyui_url"] == "http://gpu:8188"
    stored = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert stored["FAL_KEY"] == "fk" and stored["LOCAL_COMFYUI_URL"] == "http://gpu:8188/"
    rows = updated["generation_chains"]["image_edit"]["links"]
    assert next(r for r in rows if r["label"] == "fal/seedream-4-edit")["keyed"] is True
    cleared = client.put("/api/settings", json={"fal_key": "", "local_comfyui_url": ""}).json()
    assert cleared["fal_key_set"] is False and cleared["local_comfyui_url"] == "http://127.0.0.1:8188"


def test_a_paid_link_is_never_summarised_as_allowed_while_paid_is_off(client):
    """Found live: gemini/flash's token estimate rounded to $0.000 and the row said "allowed"."""
    client.put("/api/settings", json={"google_api_key": "gk"})
    rows = {r["label"]: r for r in client.get("/api/settings").json()["generation_chains"]["vision"]["links"]}
    flash = rows["gemini/flash"]
    assert flash["keyed"] is True and flash["paid"] is True and flash["allowed"] is False
    assert "allow_paid is off" in flash["reason"] and flash["est_usd"] > 0
    assert rows["gemini/flash-lite"]["allowed"] is True and rows["gemini/flash-lite"]["paid"] is False
    client.put("/api/settings", json={"allow_paid": True})
    rows = {r["label"]: r for r in client.get("/api/settings").json()["generation_chains"]["vision"]["links"]}
    assert rows["gemini/flash"]["allowed"] is True


def test_the_elevenlabs_key_round_trips_and_its_tts_link_is_listed_paid_and_refused_while_paid_is_off(client, tmp_path):
    """Plan 23 stage B3: the key is a persisted secret, the TTS chain's last link shows its price per
    1,000 characters' worth of line, and a keyed link is still refused until allow_paid is on."""
    rows = {r["label"]: r for r in client.get("/api/settings").json()["generation_chains"]["tts"]["links"]}
    assert list(rows)[-1] == "elevenlabs/flash"
    assert rows["elevenlabs/flash"]["keyed"] is False and rows["elevenlabs/flash"]["paid"] is True
    assert rows["elevenlabs/flash"]["adapter"] is True and rows["elevenlabs/flash"]["missing_keys"] == ["ELEVENLABS_API_KEY"]
    updated = client.put("/api/settings", json={"elevenlabs_api_key": "xi"}).json()
    assert updated["elevenlabs_api_key_set"] is True
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["ELEVENLABS_API_KEY"] == "xi"
    row = next(r for r in updated["generation_chains"]["tts"]["links"] if r["label"] == "elevenlabs/flash")
    assert row["keyed"] is True and row["allowed"] is False and "allow_paid is off" in row["reason"]
    assert client.put("/api/settings", json={"elevenlabs_api_key": ""}).json()["elevenlabs_api_key_set"] is False


def test_the_new_secrets_are_persisted_and_redacted():
    from web.api import settings_store

    for name in ("FAL_KEY", "OPENAI_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "POLLINATIONS_API_KEY",
                 "ELEVENLABS_API_KEY"):
        assert name in settings_store.PERSISTED_KEYS and name in settings_store.SECRET_KEYS, name
    for name in ("LOCAL_COMFYUI_URL", "LOCAL_OLLAMA_URL"):
        assert name in settings_store.PERSISTED_KEYS and name not in settings_store.SECRET_KEYS, name


# ----------------------------------------------------------- the chain test

def test_the_chain_test_calls_free_links_and_reports_paid_ones_without_calling(client, transport, outputs):
    client.put("/api/settings", json={"fal_key": "fk"})
    transport.rules[:] = [
        ("/system_stats", APIConnectionError("connection refused")),
        ("image.pollinations.ai/prompt/", (200, PNG)),
    ]
    data = client.post("/api/settings/test-generation-chain", json={"kind": "image"}).json()
    assert data["verdict"] == "ready" and data["kind"] == "image"
    rows = {r["label"]: r for r in data["results"]}
    assert rows["cloudflare/flux-1-schnell"]["status"] == "no_key" and "CLOUDFLARE_API_TOKEN" in rows["cloudflare/flux-1-schnell"]["reason"]
    assert rows["pollinations/flux"]["status"] == "ok"
    assert rows["pollinations/flux"]["artifact_url"].startswith("/api/outputs/_chain_test/")
    assert rows["pollinations/flux"]["artifact_kind"] == "image"
    assert (outputs / "_chain_test").is_dir() and list((outputs / "_chain_test").glob("image_pollinations_flux*.png"))
    assert rows["local/comfyui"]["status"] == "unreachable"
    fal = rows["fal/flux-schnell"]
    assert fal["status"] == "refused" and fal["paid"] is True and fal["est_usd"] == 0.006
    assert "allow_paid is off" in fal["reason"] and fal["allowed"] is False
    assert rows["openai/gpt-image-2-low"]["status"] == "no_key"
    assert not any("fal.run" in u or "openai.com" in u for u in transport.urls())
    usage = json.loads(pathlib.Path(client.app.state.usage_path).read_text()) if hasattr(client.app.state, "usage_path") else None
    assert usage is None or usage["providers"]["pollinations"]["calls"] == 1


def test_a_free_call_counts_in_usage_and_never_in_spend(client, transport, tmp_path):
    transport.rules[:] = [("/system_stats", APIConnectionError("refused")), ("image.pollinations.ai/prompt/", (200, PNG))]
    client.post("/api/settings/test-generation-chain", json={"kind": "image", "link": "pollinations/flux"})
    usage = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert usage["providers"]["pollinations"]["calls"] == 1
    assert not (tmp_path / "spend.json").exists()


def fal_rules(app="fal-ai/bytedance/seedream/v4/edit"):
    base = f"https://queue.fal.run/{app}/requests/req-1"
    return [
        ("/system_stats", APIConnectionError("refused")),
        ("/requests/req-1/status", (200, {"status": "COMPLETED"})),
        ("/requests/req-1", (200, {"images": [{"url": "https://v3.fal.media/files/x/out.png", "content_type": "image/png"}], "seed": 4}),),
        ("v3.fal.media", (200, PNG)),
        (f"queue.fal.run/{app}", (200, {"request_id": "req-1", "status_url": base + "/status", "response_url": base})),
    ]


def test_a_paid_link_is_tested_only_on_request_and_booked(client, transport, tmp_path, outputs):
    from web.api.routes import settings as settings_route

    client.put("/api/settings", json={"fal_key": "fk", "allow_paid": True})
    transport.rules[:] = fal_rules()
    data = client.post("/api/settings/test-generation-chain", json={"kind": "image_edit", "link": "fal/seedream-4-edit"}).json()
    assert data["tested_link"] == "fal/seedream-4-edit"
    rows = {r["label"]: r for r in data["results"]}
    row = rows["fal/seedream-4-edit"]
    assert row["status"] == "ok" and row["paid"] is True and row["est_usd"] == 0.03 and row["allowed"] is True
    assert row["artifact_url"].startswith("/api/outputs/_chain_test/") and row["artifact_url"].split("?")[0].endswith(".png")
    assert (outputs / "_chain_test" / "reference.png").is_file(), "the edit used the bundled reference"
    submit = next(c for c in transport.calls if c["method"] == "POST" and "queue.fal.run" in c["url"])
    assert json.loads(submit["body"])["image_urls"][0].startswith("data:image/png;base64,")
    assert not any("googleapis" in u for u in transport.urls()), "only the named link was called"
    ledger = json.loads(pathlib.Path(settings_route.CHAIN_TEST_LEDGER).read_text(encoding="utf-8"))
    assert ledger["$schema"] == "cost_ledger_v1"
    assert [(e["provider"], e["model"], e["est_usd"], e["paid"], e["step"]) for e in ledger["entries"]] == [("fal", "fal-ai/bytedance/seedream/v4/edit", 0.03, True, "chain_test")]
    spend = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))
    assert list(spend["days"].values()) == [0.03]
    assert not (tmp_path / "usage.json").exists(), "a paid call never touches the free counters"
    assert data["verdict"] == "ready"


def test_a_paid_link_named_while_paid_is_off_is_refused_and_never_called(client, transport):
    client.put("/api/settings", json={"fal_key": "fk"})
    transport.rules[:] = fal_rules()
    data = client.post("/api/settings/test-generation-chain", json={"kind": "image_edit", "link": "fal/seedream-4-edit"}).json()
    row = data["results"][0]
    assert row["label"] == "fal/seedream-4-edit" and row["status"] == "refused" and "allow_paid is off" in row["reason"]
    assert data["verdict"] == "blocked"
    assert not any("fal.run" in u for u in transport.urls())


def test_a_paid_link_over_the_daily_cap_is_refused_with_the_numbers(client, transport):
    client.put("/api/settings", json={"fal_key": "fk", "allow_paid": True, "daily_cap_usd": 0.02})
    transport.rules[:] = fal_rules()
    data = client.post("/api/settings/test-generation-chain", json={"kind": "image_edit", "link": "fal/seedream-4-edit"}).json()
    row = data["results"][0]
    assert row["status"] == "refused" and "$0.02" in row["reason"] and "0.030" in row["reason"]
    assert not any("fal.run" in u for u in transport.urls())


def test_the_video_chain_reports_local_unreachable_and_hosted_links_unkeyed(client, transport, monkeypatch):
    # Re-pinned in phase 6 stage 4 (and plan 23 stage C3: six links): local/comfyui has its adapter and is asked /system_stats only.
    monkeypatch.delenv("GEMINI_PAID_API_KEY", raising=False)
    transport.rules[:] = [("/system_stats", APIConnectionError("connection refused"))]
    data = client.post("/api/settings/test-generation-chain", json={"kind": "video"}).json()
    assert data["verdict"] == "blocked"
    assert [r["status"] for r in data["results"]] == ["unreachable", "no_key", "no_key", "no_key", "no_key", "no_key"]
    assert "unreachable at http://127.0.0.1:8188" in data["results"][0]["reason"]
    assert [(c["method"], c["url"]) for c in transport.calls] == [("GET", "http://127.0.0.1:8188/system_stats")]


def test_the_tts_chain_reports_what_is_missing_on_this_host(client, transport, monkeypatch):
    from clipping.providers import tts

    monkeypatch.setattr(tts, "_installed", lambda name: False)
    data = client.post("/api/settings/test-generation-chain", json={"kind": "tts"}).json()
    rows = {r["label"]: r for r in data["results"]}
    assert "edge/fr-FR-HenriNeural" not in rows, "Edge is in no default chain any more (plan 28 stage B2)"
    assert rows["gemini/flash-lite-tts"]["status"] == "no_key"
    assert rows["local/piper"]["status"] == "unreachable" and "rzdhop-ai[local-tts]" in rows["local/piper"]["reason"]
    assert data["verdict"] == "blocked"


def test_unknown_kinds_links_and_chains_are_400s(client):
    assert client.post("/api/settings/test-generation-chain", json={"kind": "audio"}).status_code == 400
    assert client.post("/api/settings/test-generation-chain", json={"kind": "image", "link": "edge/x"}).status_code == 400
    assert client.post("/api/settings/test-generation-chain", json={"kind": "image", "chain": "nobody/x"}).status_code == 400


def test_the_generation_test_shares_the_llm_tests_lock_and_ceiling():
    src = (ROOT / "web" / "api" / "routes" / "settings.py").read_text(encoding="utf-8")
    body = src[src.index("def run_generation_chain_test"):]
    assert "_CHAIN_TEST_LOCK" in body and "asyncio.wait_for" in body and "_CHAIN_TEST_CEILING_SECONDS" in body


# -------------------------------------------------------------- serving

def test_the_reserved_chain_test_directory_is_served_by_the_signed_outputs_route(settings_env, outputs, monkeypatch, tmp_path):
    from web.api import auth

    (outputs / "_chain_test").mkdir()
    (outputs / "_chain_test" / "sample.png").write_bytes(PNG)
    # Signed URLs exist only when auth is on, and auth is opt-in (DEC-173):
    # this server needs a token of its own to be "strict".
    monkeypatch.setenv("API_TOKEN", "chain-test-token")
    monkeypatch.setattr(auth, "_TOKEN", None)
    with make_client(monkeypatch, disable_auth=False) as strict:
        # Signed with the token this server holds, exactly as the settings
        # route signs its samples.
        url = auth.media_url("_chain_test", "sample.png", token=auth.current_token())
        assert strict.get(url).status_code == 200
        assert strict.get("/api/outputs/_chain_test/sample.png").status_code == 401


# ------------------------------------------------------------ deployment

def test_compose_passes_every_generation_variable_and_the_host_gateway():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for name in GEN_VARS:
        assert f"- {name}=${{{name}:-}}" in compose, name
    assert "host.docker.internal:host-gateway" in compose


def test_env_example_documents_the_generation_surface():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("FAL_KEY", "OPENAI_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "POLLINATIONS_API_KEY",
                 "ELEVENLABS_API_KEY", "IMAGE_CHAIN", "IMAGE_EDIT_CHAIN", "VIDEO_CHAIN", "TTS_CHAIN", "VISION_CHAIN", "LOCAL_COMFYUI_URL", "LOCAL_OLLAMA_URL"):
        assert re.search(rf"^{name}=", text, re.M), name


def test_load_all_registers_every_adapter_of_phase_0():
    from clipping.providers import adapters, generation, local_comfyui, video

    adapters.load_all()
    for kind, provider in (("image", "cloudflare"), ("image", "fal"), ("image_edit", "gemini"), ("image_edit", "local"),
                           ("tts", "edge"), ("tts", "local"), ("vision", "gemini"), ("vision", "local")):
        assert generation.adapter_for(kind, provider) is not None, (kind, provider)
    assert generation.adapter_for("video", "fal") is video.FAL_VIDEO
    assert generation.adapter_for("video", "gemini") is video.VEO
    assert generation.adapter_for("video", "local") is local_comfyui.COMFYUI_VIDEO  # phase 6 stage 4


# ------------------------------------------------- video: never generated (RC-V8)

def comfy_object_info(template, *, drop_file=None):
    info = {}
    for node in template["graph"].values():
        info.setdefault(node["class_type"], {"input": {"required": {}}})
    for req in template["requires"]:
        field = info[template["graph"][req["node"]]["class_type"]]["input"]["required"].setdefault(req["field"], [[]])
        if req["file"] != drop_file and req["file"] not in field[0]:
            field[0].append(req["file"])
    return info


def test_a_video_press_on_a_paid_link_never_generates_and_answers_with_the_estimate(client, transport, tmp_path):
    """DEC-103 amended for video: even named, allowed and keyed, a clip is never bought by a test."""
    from web.api.routes import settings as settings_route

    client.put("/api/settings", json={"fal_key": "fk", "allow_paid": True})
    transport.rules[:] = fal_rules("fal-ai/bytedance/seedance/v1/pro/fast/image-to-video")
    data = client.post("/api/settings/test-generation-chain",
                       json={"kind": "video", "link": "fal/seedance-1-pro-fast"}).json()
    row = data["results"][0]
    assert row["label"] == "fal/seedance-1-pro-fast" and row["status"] == "skipped" and row["est_usd"] == 0.11
    assert "never test-generated" in row["note"] and "$0.110" in row["note"] and "5 s" in row["note"]
    assert "never test-generated" in data["message"]
    assert transport.calls == []
    assert not pathlib.Path(settings_route.CHAIN_TEST_LEDGER).exists() and not (tmp_path / "spend.json").exists()
    rows = client.get("/api/settings").json()["generation_chains"]["video"]["links"]
    assert next(r for r in rows if r["label"] == "fal/seedance-1-pro-fast")["est_usd"] == 0.11


def test_a_local_video_link_is_checked_with_object_info_only(client, transport):
    from clipping.providers import local_comfyui

    template = local_comfyui.load_template("i2v_wan22_5b")
    stats = {"system": {"comfyui_version": "0.4.1"},
             "devices": [{"name": "cuda:0 NVIDIA RTX 4070 : cudaMallocAsync", "type": "cuda",
                          "vram_total": 12 * 1024 ** 3, "vram_free": 11 * 1024 ** 3}]}
    transport.rules[:] = [("/system_stats", (200, stats)),
                          ("/object_info", (200, comfy_object_info(template, drop_file="wan2.2_vae.safetensors")))]
    data = client.post("/api/settings/test-generation-chain", json={"kind": "video", "link": "local/comfyui"}).json()
    row = data["results"][0]
    assert row["status"] == "unreachable" and "wan2.2_vae.safetensors" in row["reason"] and "models/vae" in row["reason"]
    transport.rules[:] = [("/system_stats", (200, stats)), ("/object_info", (200, comfy_object_info(template)))]
    data = client.post("/api/settings/test-generation-chain", json={"kind": "video", "link": "local/comfyui"}).json()
    row = data["results"][0]
    assert data["verdict"] == "ready" and row["status"] == "ok" and "i2v_wan22_5b" in row["note"]
    assert all(c["method"] == "GET" for c in transport.calls), "no upload, no /prompt: nothing is generated"
