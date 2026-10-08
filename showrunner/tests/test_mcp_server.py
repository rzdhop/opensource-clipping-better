"""The showrunner MCP server (stage 2): its settings, its two doors and its tools, in process. Needs fastmcp
(the repo's .venv); skipped elsewhere. Run: PYTHONPATH=<a pytest> .venv/bin/python -m pytest showrunner/tests"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import pytest

fastmcp = pytest.importorskip("fastmcp")
try:  # fastmcp 4 ships against httpx2
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    httpx = pytest.importorskip("httpx")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from showrunner import mcp_auth, mcp_server as S  # noqa: E402

PUBLIC = "https://node.example.ts.net:8443"
TOKEN = "s3cret-token"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class FakeEndpoint:
    """A RunPod endpoint that answers from a script; records what it was sent."""

    def __init__(self, kind, eid, key, log):
        self.kind, self.id, self.key, self.log = kind, eid, key, log

    def health(self):
        self.log.append(("health", self.kind, self.id, self.key))
        return {"workers": {"idle": 1, "running": 0, "throttled": 2}, "jobs": {"inQueue": 0}}


def make_backend(tmp_path, **over):
    log: list = []
    settings = S.Settings(stories_dir=str(tmp_path / "stories"), state_dir=str(tmp_path / "outputs"),
                          endpoints={"video": "vid-ep", "images": "img-ep"}, keys={"video": "kv", "images": "ki"}, **over)
    backend = S.Backend(settings, endpoint_factory=lambda kind, eid, key: FakeEndpoint(kind, eid, key, log))
    backend.log = log
    return backend


def call(server, tool, **args):
    async def go():
        async with fastmcp.Client(server) as client:
            return await client.call_tool(tool, args, raise_on_error=False)
    return asyncio.run(go())


def payload(result):
    return json.loads(result.content[0].text)


# ------------------------------------------------------------------ settings

def test_settings_read_their_own_names_never_the_live_servers_or_the_live_endpoint():
    env = {"MCP_PORT": "8787", "MCP_PUBLIC_URL": "https://live.example", "MCP_HOST": "0.0.0.0", "MCP_TOKEN": "t",
           "RUNPOD_COMFY_ENDPOINT_ID": "LIVE", "RUNPOD_API_KEY": "main"}
    s = S.load_settings(env)
    assert (s.host, s.port, s.public_url, s.token) == ("127.0.0.1", 8788, "", "t")
    assert s.endpoints == {} and "LIVE" not in json.dumps(s.__dict__)
    s = S.load_settings({"SHOWRUNNER_MCP_PORT": "9000", "SHOWRUNNER_MCP_PUBLIC_URL": "https://x.ts.net:8443/",
                         "RUNPOD_SHOWRUNNER_VIDEO_ENDPOINT_ID": "v", "RUNPOD_SHOWRUNNER_VIDEO_KEY": "kv",
                         "RUNPOD_IMAGE_ENDPOINT_ID": "i", "RUNPOD_API_KEY": "main"})
    assert (s.port, s.public_url) == (9000, "https://x.ts.net:8443")
    assert s.endpoints == {"video": "v", "images": "i"} and s.keys == {"video": "kv", "images": "main"}


def test_templates_route_to_their_endpoint():
    assert S.template_kind("t2i_flux2_klein") == "images" and S.template_kind("edit_flux2_klein_multiref") == "images"
    assert S.template_kind("ltx25_i2v_speech") == "video" and S.template_kind("vc_chatterbox") == "video"


# ------------------------------------------------------------------ tools

def test_runpod_health_asks_both_endpoints_with_their_own_keys(tmp_path):
    backend = make_backend(tmp_path)
    got = payload(call(S.build_server(backend), "runpod_health"))
    assert got["video"]["endpoint"] == "vid-ep" and got["video"]["workers"]["throttled"] == 2
    assert sorted(backend.log) == [("health", "images", "img-ep", "ki"), ("health", "video", "vid-ep", "kv")]


def test_templates_list_names_values_files_and_endpoint(tmp_path):
    rows = {r["name"]: r for r in payload(call(S.build_server(make_backend(tmp_path)), "templates_list"))}
    assert set(rows) >= {"ltx25_i2v_speech", "vc_chatterbox", "t2i_flux2_klein", "edit_flux2_klein_multiref"}
    assert rows["ltx25_i2v_speech"]["endpoint"] == "video" and "image" in rows["ltx25_i2v_speech"]["files"]
    assert "prompt" in rows["ltx25_i2v_speech"]["values"] and "image" not in rows["ltx25_i2v_speech"]["values"]
    assert rows["edit_flux2_klein_multiref"]["endpoint"] == "images"


# ------------------------------------------------------------------ the doors (as the live server's auth test)

@contextlib.asynccontextmanager
async def client_for(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC) as client:
            yield client


def initialize(client, headers):
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    return client.post("/mcp", json=body, headers={**MCP_HEADERS, **headers})


def app_for(tmp_path):
    return S.build_server(make_backend(tmp_path, token=TOKEN, public_url=PUBLIC)).http_app()


@pytest.mark.anyio
async def test_no_token_is_refused_and_the_bearer_opens(tmp_path):
    async with client_for(app_for(tmp_path)) as c:
        assert (await initialize(c, {})).status_code == 401
        assert (await initialize(c, {"Authorization": "Bearer nope"})).status_code == 401
        ok = await initialize(c, {"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200, ok.text
        meta = (await c.get("/.well-known/oauth-authorization-server")).json()
        assert meta["authorization_endpoint"].startswith(PUBLIC) and "S256" in meta["code_challenge_methods_supported"]


@pytest.mark.anyio
async def test_the_connector_flow_logs_in_with_the_token_and_keeps_its_own_state(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_auth.time, "sleep", lambda s: None)
    async with client_for(app_for(tmp_path)) as c:
        meta = (await c.get("/.well-known/oauth-authorization-server")).json()
        reg = await c.post(meta["registration_endpoint"], json={
            "client_name": "Claude", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none", "scope": "story"})
        assert reg.status_code == 201, reg.text
        cid = reg.json()["client_id"]
        verifier = secrets.token_urlsafe(40)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        auth = await c.get(meta["authorization_endpoint"], params={
            "response_type": "code", "client_id": cid, "code_challenge": challenge, "code_challenge_method": "S256",
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback", "state": "xyz", "scope": "story"},
            follow_redirects=False)
        login = auth.headers["location"]
        assert login.startswith(PUBLIC + mcp_auth.LOGIN_PATH + "?pending=")
        page = await c.get(login)
        assert "showrunner" in page.text and "MCP_TOKEN" in page.text
        pending = parse_qs(urlparse(login).query)["pending"][0]
        wrong = await c.post(mcp_auth.LOGIN_PATH, data={"pending": pending, "secret": "guess"}, follow_redirects=False)
        assert wrong.status_code == 403 and "Wrong token" in wrong.text
        ok = await c.post(mcp_auth.LOGIN_PATH, data={"pending": pending, "secret": TOKEN}, follow_redirects=False)
        assert ok.status_code == 303
        code = parse_qs(urlparse(ok.headers["location"]).query)["code"][0]
        tok = await c.post(meta["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "client_id": cid, "code_verifier": verifier})
        assert tok.status_code == 200, tok.text
        assert (await initialize(c, {"Authorization": f"Bearer {tok.json()['access_token']}"})).status_code == 200
    state = tmp_path / "outputs" / "showrunner-mcp" / "oauth.json"
    assert cid in json.load(open(state, encoding="utf-8"))["clients"]
    assert oct(os.stat(state).st_mode)[-3:] == "600"
