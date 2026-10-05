"""Auth is opt-in (DEC-173): a token is required only when API_TOKEN is set.

The human's call, 2026-09-29: the app runs on localhost or a private tailnet,
so a fresh start must not ask for a token. What stays guarded is the two paths
that reach the public internet (the Caddy DOMAIN profile refuses to start open;
the Kaggle notebook makes its own token) and, in open mode, a browser's
cross-site write -- which blocks another website, never the user.

The pure functions run in the pytest-only CI environment; the app tests need
FastAPI and skip without it, like every other app test.
"""

import pathlib
import time

import pytest

from web.api import auth

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
TOKEN = "opt-in-token-12345"


@pytest.fixture(autouse=True)
def _no_pinned_token(monkeypatch):
    """Every test starts with no token anywhere: env, pin or DISABLE_AUTH."""
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("DISABLE_AUTH", raising=False)
    monkeypatch.delenv("DOMAIN", raising=False)
    monkeypatch.setattr(auth, "_TOKEN", None)


# ------------------------------------------------------------------ the rule

@pytest.mark.parametrize("env,expected", [
    ({}, None),
    ({"API_TOKEN": ""}, None),
    ({"API_TOKEN": "   "}, None),
    ({"API_TOKEN": " pinned "}, "pinned"),
])
def test_the_token_comes_from_api_token_only(env, expected):
    assert auth.configured_token(env) == expected


@pytest.mark.parametrize("env,expected", [
    ({}, False),
    ({"API_TOKEN": "  "}, False),
    ({"API_TOKEN": TOKEN}, True),
    ({"API_TOKEN": TOKEN, "DISABLE_AUTH": "1"}, False),
    ({"DISABLE_AUTH": "1"}, False),
])
def test_auth_is_on_only_when_a_token_is_set(env, expected):
    assert auth.auth_enabled(env) is expected


def test_nothing_set_means_no_token_and_no_file(monkeypatch, tmp_path):
    """The old default generated data/api_token on first start. Nothing may be
    written now, and a stale file from an older install is not read."""
    stale = tmp_path / "api_token"
    stale.write_text("from-an-older-install", encoding="utf-8")
    monkeypatch.setattr(auth, "TOKEN_PATH", str(stale))
    assert auth.current_token() is None
    assert auth.auth_enabled() is False
    assert stale.read_text(encoding="utf-8") == "from-an-older-install"
    assert not hasattr(auth, "load_or_create_token")


def test_the_env_token_is_read_live(monkeypatch):
    """No cache: a token set (or unset) in the environment takes effect at once."""
    assert auth.current_token() is None
    monkeypatch.setenv("API_TOKEN", TOKEN)
    assert auth.current_token() == TOKEN and auth.auth_enabled() is True
    monkeypatch.delenv("API_TOKEN")
    assert auth.current_token() is None and auth.auth_enabled() is False


def test_a_pinned_token_still_turns_auth_on(monkeypatch):
    """Tests pin auth._TOKEN; that pin must keep meaning "this server has a token"."""
    monkeypatch.setattr(auth, "_TOKEN", TOKEN)
    assert auth.current_token() == TOKEN and auth.auth_enabled() is True


# ---------------------------------------------------------- media URLs open

def test_with_auth_off_a_clip_url_is_the_plain_path():
    """A signature keyed by no secret would be forgeable, and the gate is open."""
    assert auth.media_url("abc123", "clip 1.mp4") == "/api/outputs/abc123/clip%201.mp4"


def test_with_auth_off_a_story_url_is_the_plain_path():
    assert auth.story_media_url("s1", 1, "episode_final.mp4") == (
        "/api/stories/s1/episodes/1/media/episode_final.mp4")


def test_with_auth_on_urls_are_still_signed(monkeypatch):
    monkeypatch.setenv("API_TOKEN", TOKEN)
    assert "?exp=" in auth.media_url("abc123", "clip.mp4")
    assert "?exp=" in auth.story_media_url("s1", 1, "cover.jpg")


def test_an_explicit_token_always_signs():
    """The pure signing API is unchanged for callers that pass the token."""
    assert "&sig=" in auth.media_url("abc123", "clip.mp4", token=TOKEN)


def test_signing_never_uses_a_known_key():
    """With no token, the signing key must not fall back to str(None) or ''."""
    with pytest.raises(ValueError):
        auth.sign_media("abc123", "clip.mp4", 1)
    with pytest.raises(ValueError):
        auth.sign_story_media("s1", 1, "cover.jpg", 1)


def test_verification_without_a_token_refuses_instead_of_raising():
    assert auth.media_signature_is_valid("abc123", "clip.mp4", int(time.time()) + 600, "00") is False
    assert auth.story_media_signature_is_valid("s1", 1, "cover.jpg", int(time.time()) + 600, "00") is False


# ------------------------------------------------------------- the banner

def test_the_banner_says_the_api_is_open_and_names_a_stale_token_file(monkeypatch, tmp_path, capsys):
    stale = tmp_path / "api_token"
    stale.write_text("old", encoding="utf-8")
    monkeypatch.setattr(auth, "TOKEN_PATH", str(stale))
    auth.announce()
    out = capsys.readouterr().out
    assert "OPEN" in out and "API_TOKEN" in out
    assert str(stale) in out and "no longer read" in out
    assert "old" not in out.replace(str(stale), "")


def test_the_banner_never_prints_the_token(monkeypatch, capsys):
    monkeypatch.setenv("API_TOKEN", TOKEN)
    auth.announce()
    out = capsys.readouterr().out
    assert TOKEN not in out and "ON" in out


def test_the_banner_creates_no_file(monkeypatch, tmp_path):
    path = tmp_path / "api_token"
    monkeypatch.setattr(auth, "TOKEN_PATH", str(path))
    auth.announce()
    assert not path.exists()


# ------------------------------------------------------- public exposure

@pytest.mark.parametrize("env,refused", [
    ({}, False),
    ({"DOMAIN": "clips.example.org"}, True),
    ({"DOMAIN": "  "}, False),
    ({"DOMAIN": "clips.example.org", "API_TOKEN": TOKEN}, False),
    ({"DOMAIN": "clips.example.org", "API_TOKEN": TOKEN, "DISABLE_AUTH": "1"}, True),
])
def test_a_public_domain_never_starts_open(env, refused):
    reason = auth.open_public_exposure(env)
    assert bool(reason) is refused
    if refused:
        assert "API_TOKEN" in reason and "DOMAIN" in reason


# ------------------------------------------------------- cross-site writes

@pytest.mark.parametrize("method,site,expected", [
    ("POST", "cross-site", True),
    ("PUT", "cross-site", True),
    ("PATCH", "CROSS-SITE", True),
    ("DELETE", "cross-site", True),
    ("GET", "cross-site", False),
    ("HEAD", "cross-site", False),
    ("OPTIONS", "cross-site", False),
    ("POST", "same-origin", False),
    ("POST", "same-site", False),
    ("POST", "none", False),
    ("POST", None, False),
])
def test_only_a_browsers_cross_site_write_counts(method, site, expected):
    headers = {} if site is None else {"sec-fetch-site": site}
    assert auth.is_cross_site_write(method, headers) is expected


# --------------------------------------------- the app, end to end (needs fastapi)

def _client(monkeypatch, **env):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    for name, value in env.items():
        monkeypatch.setenv(name, value)
    from web.api.app import app

    return TestClient(app)


@pytest.mark.parametrize("path", ["/api/jobs", "/api/settings", "/api/stories"])
def test_with_no_token_every_router_answers(monkeypatch, path):
    with _client(monkeypatch) as client:
        assert client.get(path).status_code == 200, path


def test_with_no_token_a_clip_is_served_unsigned(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")  # before the route import, which needs it
    from web.api.routes import files as files_route

    (tmp_path / "abc123def456").mkdir()
    (tmp_path / "abc123def456" / "clip.mp4").write_bytes(b"\x00" * 64)
    monkeypatch.setattr(files_route, "OUTPUTS_DIR", str(tmp_path))
    with _client(monkeypatch) as client:
        url = auth.media_url("abc123def456", "clip.mp4")
        assert "?" not in url
        assert client.get(url).status_code == 200


def test_with_a_token_the_gate_is_unchanged(monkeypatch):
    with _client(monkeypatch, API_TOKEN=TOKEN) as client:
        assert client.get("/api/jobs").status_code == 401
        assert client.get("/api/jobs", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_a_cross_site_write_is_refused_when_open(monkeypatch):
    """Any website open in the same browser could otherwise POST /api/shutdown."""
    with _client(monkeypatch) as client:
        response = client.post("/api/shutdown", headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 403
        assert "cross-site" in response.json()["detail"].lower()


def test_a_cross_site_approve_all_is_refused_when_open(monkeypatch):
    """Plan 28 C1: the story's "Approve all" is a write like the others; another
    website cannot make it, the dashboard (same-origin) and curl still can."""
    with _client(monkeypatch) as client:
        for group in ("cast", "places"):
            response = client.post(f"/api/stories/0123456789ab/approve-all/{group}",
                                   headers={"Sec-Fetch-Site": "cross-site"})
            assert response.status_code == 403, group
        for headers in ({"Sec-Fetch-Site": "same-origin"}, {}):
            response = client.post("/api/stories/0123456789ab/approve-all/cast", headers=headers)
            assert response.status_code == 404, headers  # the route, answering: no such story


def test_same_origin_and_non_browser_writes_pass_the_guard(monkeypatch):
    """The dashboard is same-origin and curl sends no Sec-Fetch-Site; neither
    may be caught. A bad body answering 4xx from the route proves it got there."""
    with _client(monkeypatch) as client:
        for headers in ({"Sec-Fetch-Site": "same-origin"}, {}):
            response = client.post("/api/stories", json={}, headers=headers)
            assert response.status_code not in (401, 403), headers


def test_the_guard_steps_aside_when_auth_is_on(monkeypatch):
    """With a token, a cross-site request cannot carry the header anyway; the
    answer is the gate's 401, not the guard's 403."""
    with _client(monkeypatch, API_TOKEN=TOKEN) as client:
        response = client.post("/api/shutdown", headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 401


def test_a_named_allowed_origin_may_still_write(monkeypatch):
    """ALLOWED_ORIGINS names sites the operator trusts; the guard honours them."""
    from web.api import auth as auth_module

    guard = auth_module.CrossSiteWriteGuard(app=None, allowed_origins=["https://studio.example.org"])
    assert guard.refuses("POST", {"sec-fetch-site": "cross-site", "origin": "https://studio.example.org"}) is False
    assert guard.refuses("POST", {"sec-fetch-site": "cross-site", "origin": "https://evil.example"}) is True


def test_a_public_domain_refuses_to_start_the_app(monkeypatch):
    with pytest.raises(RuntimeError, match="API_TOKEN"):
        with _client(monkeypatch, DOMAIN="clips.example.org"):
            pass
