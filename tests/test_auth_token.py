"""API token authentication.

Until this existed there was none at all, and the backend bound 0.0.0.0:8000 on
a machine with a public IP: anyone who found the port could read every job,
upload a 2 GB file, read which keys were configured, or call POST /api/shutdown.

The pure functions are tested without FastAPI so they run in the pytest-only CI
environment; the wiring is checked by AST for the same reason.
"""

import ast
import os
import pathlib
import stat

import pytest

from web.api import auth

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


# ------------------------------------------------------------- comparison

def test_a_matching_token_is_valid():
    assert auth.token_is_valid("abc", "abc") is True


@pytest.mark.parametrize("presented,expected", [
    ("abc", "abd"), ("abc", "ab"), ("ab", "abc"), ("ABC", "abc"),
    (" abc", "abc"), ("abc ", "abc"),
])
def test_a_mismatched_token_is_not(presented, expected):
    assert auth.token_is_valid(presented, expected) is False


@pytest.mark.parametrize("presented,expected", [
    ("", "abc"), (None, "abc"), ("abc", ""), ("abc", None), ("", ""), (None, None),
])
def test_empty_never_matches_including_an_empty_expectation(presented, expected):
    """A server that failed to load its token must not accept every request."""
    assert auth.token_is_valid(presented, expected) is False


def test_the_comparison_is_constant_time():
    """hmac.compare_digest, not ==. Pinned because == is the easy edit that
    makes this timing-attackable and passes every other test here."""
    src = (PROJECT_ROOT / "web" / "api" / "auth.py").read_text(encoding="utf-8")
    assert "compare_digest" in src


# ------------------------------------------------------------- extraction

@pytest.mark.parametrize("headers,expected", [
    ({"authorization": "Bearer abc"}, "abc"),
    ({"Authorization": "Bearer abc"}, "abc"),
    ({"authorization": "bearer abc"}, "abc"),
    ({"authorization": "BEARER abc"}, "abc"),
    ({"authorization": "Bearer   abc  "}, "abc"),
    ({"x-api-key": "abc"}, "abc"),
    ({"X-API-Key": "abc"}, "abc"),
])
def test_a_token_is_found_in_either_header(headers, expected):
    assert auth.token_from_request(headers) == expected


@pytest.mark.parametrize("headers", [
    {}, {"authorization": ""}, {"authorization": "Bearer"},
    {"authorization": "Bearer   "}, {"authorization": "Basic abc"},
    {"x-api-key": "   "},
])
def test_no_token_is_none(headers):
    assert auth.token_from_request(headers) is None


def test_bearer_wins_over_x_api_key():
    assert auth.token_from_request(
        {"authorization": "Bearer from-bearer", "x-api-key": "from-key"}
    ) == "from-bearer"


# ------------------------------------------------------------ token storage

def test_an_env_token_wins(tmp_path):
    path = str(tmp_path / "api_token")
    assert auth.load_or_create_token(path, env={"API_TOKEN": "pinned"}) == "pinned"
    assert not os.path.exists(path), "an env token must not be written to disk"


def test_a_stored_token_is_reused(tmp_path):
    path = tmp_path / "api_token"
    path.write_text("stored-token", encoding="utf-8")
    assert auth.load_or_create_token(str(path), env={}) == "stored-token"


def test_a_token_is_generated_and_persisted_on_first_start(tmp_path):
    """Generating rather than refusing to start is deliberate: a server that
    will not boot without a hand-written token is one people work around by
    disabling auth."""
    path = str(tmp_path / "api_token")
    first = auth.load_or_create_token(path, env={})
    assert len(first) >= 32
    assert auth.load_or_create_token(path, env={}) == first


def test_the_token_file_is_not_readable_by_anyone_else(tmp_path):
    path = str(tmp_path / "api_token")
    auth.load_or_create_token(path, env={})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_an_unwritable_location_still_yields_a_usable_token(tmp_path):
    """Better a token that changes on restart than a server that will not run."""
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        token = auth.load_or_create_token(str(blocked / "api_token"), env={})
        assert token
    finally:
        blocked.chmod(0o700)


def test_generated_tokens_differ(tmp_path):
    a = auth.load_or_create_token(str(tmp_path / "a"), env={})
    b = auth.load_or_create_token(str(tmp_path / "b"), env={})
    assert a != b


# ------------------------------------------------------------- public paths

def test_health_is_reachable_without_a_token():
    """A container healthcheck and a reverse proxy need it, and it reports only
    booleans and counts."""
    assert auth.is_public("/api/health")
    assert auth.is_public("/api/health/")


@pytest.mark.parametrize("path", [
    "/api/jobs", "/api/settings", "/api/upload", "/api/shutdown",
    "/api/outputs/abc/clip.mp4", "/api/jobs/abc/status",
])
def test_everything_else_is_not(path):
    assert not auth.is_public(path)


def test_the_public_list_stays_tiny():
    """Every entry is reachable by anyone who can reach the port."""
    assert auth.PUBLIC_PATHS == frozenset({"/api/health"})


# ------------------------------------------------------------ escape hatch

def test_auth_can_be_disabled_only_explicitly():
    for value in ("1", "true", "TRUE", "yes"):
        assert auth.auth_disabled({"DISABLE_AUTH": value}) is True
    for value in ("", "0", "false", "no", "maybe"):
        assert auth.auth_disabled({"DISABLE_AUTH": value}) is False
    assert auth.auth_disabled({}) is False


def test_the_escape_hatch_is_not_in_the_compose_files():
    """It exists for a developer's terminal, never for a running deployment."""
    for name in ("docker-compose.yml", "docker-compose.dev.yml"):
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert "DISABLE_AUTH" not in text, name


# --------------------------------------------------------------- the wiring

def _routers_with_dependencies():
    """{filename: bool} — whether each router declares a dependencies= list."""
    out = {}
    for path in (PROJECT_ROOT / "web" / "api" / "routes").glob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "APIRouter":
                continue
            out[path.name] = any(kw.arg == "dependencies" for kw in node.keywords)
    return out


def test_every_router_requires_a_token():
    """A new route file added without the dependency would be wide open, and
    nothing else in the codebase would notice."""
    routers = _routers_with_dependencies()
    assert routers, "no APIRouter found; has the routes package moved?"
    unprotected = sorted(name for name, guarded in routers.items() if not guarded)
    assert unprotected == [], f"routers with no auth dependency: {unprotected}"


def test_the_shutdown_endpoint_is_protected():
    """It was an unauthenticated kill switch on a port bound to 0.0.0.0."""
    src = (PROJECT_ROOT / "web" / "api" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) and not isinstance(node, ast.AsyncFunctionDef):
            continue
        if node.name != "shutdown_server":
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call):
                if any(kw.arg == "dependencies" for kw in decorator.keywords):
                    return
        raise AssertionError("shutdown_server has no auth dependency")
    raise AssertionError("shutdown_server not found")


def test_cors_no_longer_ships_a_hardcoded_allow_list():
    """The dashboard is same-origin now; wildcard-ish defaults are a liability
    on a host with a public IP."""
    src = (PROJECT_ROOT / "web" / "api" / "app.py").read_text(encoding="utf-8")
    assert "naufalrizqullah.github.io" not in src
    assert "ALLOWED_ORIGINS" in src


def test_the_backend_port_is_bound_to_loopback():
    text = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:8000:8000"' in text
    assert '"8000:8000"' not in text


def test_the_token_never_travels_in_a_query_string():
    """A token in a URL lands in access logs, browser history and Referer
    headers. This is why the job stream uses fetch instead of EventSource."""
    api_js = (PROJECT_ROOT / "web" / "dashboard" / "src" / "api.js").read_text(
        encoding="utf-8"
    )
    assert "token=" not in api_js
    assert "new EventSource" not in api_js
    assert "Authorization" in api_js


# ------------------------------------------- the app, end to end (needs fastapi)

@pytest.fixture
def client(monkeypatch):
    """A TestClient against the real app with a known token."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("API_TOKEN", "test-token-12345")
    monkeypatch.delenv("DISABLE_AUTH", raising=False)
    monkeypatch.setattr(auth, "_TOKEN", None)

    from web.api.app import app

    with TestClient(app) as test_client:
        yield test_client
    monkeypatch.setattr(auth, "_TOKEN", None)


BEARER = {"Authorization": "Bearer test-token-12345"}


def test_health_answers_without_a_token(client):
    assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/jobs"),
    ("GET", "/api/settings"),
    ("POST", "/api/shutdown"),
    ("GET", "/api/outputs/somejob"),
])
def test_protected_routes_reject_a_missing_token(client, method, path):
    assert client.request(method, path).status_code == 401


def test_a_wrong_token_is_rejected(client):
    response = client.get("/api/jobs", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


@pytest.mark.parametrize("headers", [
    BEARER,
    {"X-API-Key": "test-token-12345"},
])
def test_a_valid_token_is_accepted_in_either_header(client, headers):
    assert client.get("/api/jobs", headers=headers).status_code == 200


def test_the_rejection_is_401_and_not_422(client):
    """The regression this test exists for.

    `require_token(request)` without a type annotation makes FastAPI treat
    `request` as a request-body field, so every route -- including the public
    health check -- answered 422 and the token was never looked at. The
    functions were all correct; only a live request could show it.
    """
    for path in ("/api/jobs", "/api/settings"):
        assert client.get(path).status_code == 401, path
    assert client.get("/api/health").status_code == 200


def test_a_401_tells_the_client_how_to_authenticate(client):
    assert client.get("/api/jobs").headers.get("www-authenticate") == "Bearer"


# ------------------------------------------------------- output path safety

def test_an_absolute_segment_cannot_escape_the_outputs_directory():
    """os.path.join discards everything before an absolute component:
    join("/app/outputs", "/etc", "passwd") is "/etc/passwd".

    Starlette's router happens to block that today, because a path parameter
    does not match "/". That is an accident of routing, not a defence --
    changing the route to {filename:path}, the obvious edit the first time
    someone wants nested outputs, would make it exploitable immediately. So the
    check is tested directly, below the router.
    """
    pytest.importorskip("fastapi")
    from fastapi import HTTPException

    from web.api.routes.files import resolve_output_path

    for job_id, filename in [
        ("/etc", "passwd"),
        ("..", "secrets"),
        ("../..", "etc/passwd"),
        ("job", "/etc/passwd"),
        ("job", ".."),
        ("", "x"),
        (".", "x"),
    ]:
        with pytest.raises(HTTPException) as info:
            resolve_output_path(job_id, filename)
        assert info.value.status_code == 400, (job_id, filename)


def test_an_ordinary_output_path_still_resolves():
    pytest.importorskip("fastapi")
    from web.api.routes.files import OUTPUTS_DIR, resolve_output_path

    resolved = resolve_output_path("abc123", "highlight_rank_1_ready.mp4")
    assert resolved.startswith(os.path.realpath(OUTPUTS_DIR) + os.sep)
    assert resolved.endswith("highlight_rank_1_ready.mp4")


def test_a_filename_containing_dots_is_not_rejected():
    """The old guard was a substring test for '..', which also rejected a
    legitimate name that happened to contain two dots."""
    pytest.importorskip("fastapi")
    from web.api.routes.files import resolve_output_path

    assert resolve_output_path("job", "my..clip.mp4").endswith("my..clip.mp4")


@pytest.mark.parametrize("path", [
    "/api/outputs/..%2f..%2f..%2fetc/passwd",
    "/api/outputs/%2fetc/passwd",
    "/api/outputs/x/..%2f..%2f..%2f..%2fetc%2fpasswd",
    "/api/outputs/%2e%2e/%2e%2e/etc/passwd",
])
def test_traversal_attempts_over_http_are_refused(client, path):
    response = client.get(path, headers=BEARER)
    assert response.status_code in (400, 404)
    assert "root:" not in response.text


def test_secrets_never_enter_the_docker_build_context():
    """data/ holds the API token, the saved settings (every provider key) and an
    optional cookies.txt. The Dockerfile does `COPY . .`, so anything not
    excluded here is baked into an image layer, where it survives every later
    deletion and travels with the image."""
    ignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    entries = {line.strip().rstrip("/") for line in ignore.splitlines()}
    for secret in ("data", ".env"):
        assert secret in entries, f"{secret} is not in .dockerignore"


def test_the_dashboard_mount_is_the_last_route_registered():
    """A Mount at "/" matches every path that reaches it, so any route declared
    after it is unreachable.

    This was not theoretical: the mount sat above POST /api/shutdown, which
    therefore answered 405 instead of running -- and only in production, because
    the mount is skipped when web/dashboard/dist is absent, which is the case on
    a dev box and in CI. The bug was invisible everywhere it was tested.
    """
    import ast

    src = (PROJECT_ROOT / "web" / "api" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    mount_line = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "mount"):
            mount_line = node.lineno

    assert mount_line is not None, "the dashboard mount disappeared"

    later_routes = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            func = call.func if call else decorator
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in {"get", "post", "put", "delete", "patch"}:
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id == "app"):
                continue
            # The fallback route inside the else: branch is part of the mount
            # block itself, so it is allowed to sit beside it.
            if node.name == "_no_dashboard":
                continue
            if decorator.lineno > mount_line:
                later_routes.append(f"{node.name} (line {decorator.lineno})")

    assert later_routes == [], (
        "these routes are declared after the catch-all mount and are "
        f"unreachable: {later_routes}"
    )


def test_an_empty_dist_falls_back_instead_of_serving_404s():
    """An empty dist/ happens in two ordinary ways: a build that failed halfway,
    and the anonymous volume docker-compose creates as a mount point on the
    host. Mounting StaticFiles over it answers 404 for every page -- including
    the fallback that would have explained the problem."""
    src = (PROJECT_ROOT / "web" / "api" / "app.py").read_text(encoding="utf-8")
    assert 'os.path.isfile(os.path.join(_DIST, "index.html"))' in src
    assert "os.path.isdir(_DIST)" not in src
