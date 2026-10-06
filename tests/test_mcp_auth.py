"""The server's two doors (``mcp_server/auth.py``): the bearer token, and the
OAuth flow a claude.ai connector drives -- dynamic client registration, the
authorize redirect to the login page, the secret typed there, the code
exchanged (PKCE) for tokens, a tool called with them, the state surviving a
restart. Over the real HTTP app with an in-process client; no network.
"""

import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlparse

import pytest

fastmcp = pytest.importorskip("fastmcp")
try:  # fastmcp 4 ships against httpx2; older environments have httpx
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    httpx = pytest.importorskip("httpx")

from mcp_server import auth as auth_mod  # noqa: E402
from mcp_server.config import Settings  # noqa: E402
from mcp_server.director import Director  # noqa: E402
from mcp_server.runpod_jobs import JobClient  # noqa: E402
from mcp_server.server import Backend, build_server  # noqa: E402
from mcp_server.story_tools import StoryBackend  # noqa: E402

PUBLIC = "https://story.example.ts.net"
TOKEN = "s3cret-token"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def make_app(tmp_path, *, public_url=PUBLIC, token=TOKEN):
    outputs = str(tmp_path / "outputs")
    settings = Settings(api_key="k", endpoints={"video": "v"}, outputs_dir=outputs, token=token, public_url=public_url)
    story = StoryBackend(outputs, director=Director(outputs, settings_env={}, event_wait=1.0), settings_env={})
    backend = Backend(settings, client=JobClient(settings, transport=lambda *a, **k: None), story=story)
    return build_server(backend).http_app()


import contextlib  # noqa: E402


@contextlib.asynccontextmanager
async def client_for(app):
    """An in-process client with the app's lifespan running (the MCP session
    manager needs it), as uvicorn would run it."""
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC) as client:
            yield client


def initialize(client, headers):
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "0"}}}
    return client.post("/mcp", json=body, headers={**MCP_HEADERS, **headers})


@pytest.mark.anyio
async def test_without_a_token_the_mcp_is_refused_and_the_metadata_is_served(tmp_path):
    app = make_app(tmp_path)
    async with client_for(app) as c:
        refused = await initialize(c, {})
        assert refused.status_code == 401
        meta = await c.get("/.well-known/oauth-authorization-server")
        assert meta.status_code == 200
        doc = meta.json()
        assert doc["authorization_endpoint"].startswith(PUBLIC) and doc["registration_endpoint"].startswith(PUBLIC)
        assert "S256" in doc["code_challenge_methods_supported"]


@pytest.mark.anyio
async def test_the_bearer_token_opens_the_mcp(tmp_path):
    app = make_app(tmp_path)
    async with client_for(app) as c:
        ok = await initialize(c, {"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200, ok.text
        bad = await initialize(c, {"Authorization": "Bearer nope"})
        assert bad.status_code == 401


async def oauth_dance(c, meta, *, secret=TOKEN):
    """Register, authorize through the login page, exchange: the token answer
    (or the login page's refusal when *secret* is wrong)."""
    reg = await c.post(meta["registration_endpoint"], json={
        "client_name": "Claude", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "none", "scope": "story"})
    assert reg.status_code == 201, reg.text
    client_info = reg.json()
    verifier = secrets.token_urlsafe(40)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    auth = await c.get(meta["authorization_endpoint"], params={
        "response_type": "code", "client_id": client_info["client_id"], "code_challenge": challenge,
        "code_challenge_method": "S256", "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
        "state": "xyz", "scope": "story"}, follow_redirects=False)
    assert auth.status_code in (302, 307), auth.text
    login_url = auth.headers["location"]
    assert login_url.startswith(PUBLIC + auth_mod.LOGIN_PATH + "?pending=")
    pending = parse_qs(urlparse(login_url).query)["pending"][0]
    page = await c.get(login_url)
    assert page.status_code == 200 and "MCP_TOKEN" in page.text
    submit = await c.post(auth_mod.LOGIN_PATH, data={"pending": pending, "secret": secret}, follow_redirects=False)
    if submit.status_code == 403:
        return None, submit
    assert submit.status_code == 303, submit.text
    back = urlparse(submit.headers["location"])
    assert back.netloc == "claude.ai"
    query = parse_qs(back.query)
    assert query["state"] == ["xyz"]
    tok = await c.post(meta["token_endpoint"], data={
        "grant_type": "authorization_code", "code": query["code"][0], "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
        "client_id": client_info["client_id"], "code_verifier": verifier})
    assert tok.status_code == 200, tok.text
    return tok.json(), client_info


@pytest.mark.anyio
async def test_the_connector_flow_passes_the_login_page_and_calls_a_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_mod.time, "sleep", lambda s: None)
    app = make_app(tmp_path)
    async with client_for(app) as c:
        meta = (await c.get("/.well-known/oauth-authorization-server")).json()
        wrong, page = await oauth_dance(c, meta, secret="guess")
        assert wrong is None and "Wrong token" in page.text
        tokens, client_info = await oauth_dance(c, meta)
        assert tokens["token_type"].lower() == "bearer" and tokens["refresh_token"]
        ok = await initialize(c, {"Authorization": f"Bearer {tokens['access_token']}"})
        assert ok.status_code == 200, ok.text
        # Refresh: the old access token dies, the new one works.
        refreshed = await c.post(meta["token_endpoint"], data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            "client_id": client_info["client_id"]})
        assert refreshed.status_code == 200, refreshed.text
        assert (await initialize(c, {"Authorization": f"Bearer {tokens['access_token']}"})).status_code == 401
        assert (await initialize(c, {"Authorization": f"Bearer {refreshed.json()['access_token']}"})).status_code == 200
    # The state outlives the process: a new app on the same outputs dir knows the tokens.
    state = json.load(open(tmp_path / "outputs" / "mcp" / "oauth.json", encoding="utf-8"))
    assert client_info["client_id"] in state["clients"] and refreshed.json()["access_token"] in state["access_tokens"]
    again = make_app(tmp_path)
    async with client_for(again) as c:
        assert (await initialize(c, {"Authorization": f"Bearer {refreshed.json()['access_token']}"})).status_code == 200


def test_without_a_public_url_only_the_bearer_door_exists_and_without_a_token_none():
    auth, provider = auth_mod.build_auth(token=TOKEN, public_url="")
    assert provider is None and auth is not None
    assert auth_mod.build_auth(token="", public_url=PUBLIC) == (None, None)
