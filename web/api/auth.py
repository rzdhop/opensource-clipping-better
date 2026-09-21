"""A bearer token on every API route.

Until now there was no authentication of any kind, and the backend bound
0.0.0.0:8000 on a machine with a public IP. Anyone who found it could read every
job, upload a 2 GB file, read back which API keys were configured, or call
`POST /api/shutdown` — which was, until this module, an unauthenticated kill
switch.

The token is checked with `hmac.compare_digest`, generated on first start if
absent, and stored 0600 next to the settings. `/api/health` stays open so a
container healthcheck and a reverse proxy can use it without a credential; it
reports only booleans and counts.

Stdlib plus FastAPI. The FastAPI import is deferred into the dependency so the
pure functions stay testable in the pytest-only CI environment.
"""

from __future__ import annotations

import hmac
import os
import secrets

try:
    # Imported at module scope, but guarded, for one specific reason: the
    # dependency below must be ANNOTATED `request: Request` or FastAPI treats
    # `request` as a request-body field and every route answers 422 instead of
    # running the check. (That is exactly what happened; no unit test could see
    # it, because the function itself was correct.)
    #
    # With `from __future__ import annotations` the annotation is a string that
    # FastAPI resolves against this module's globals, so the real class has to
    # live here. The guard keeps the pure functions importable with nothing but
    # the standard library, which the CI suite relies on.
    from fastapi import Request
except ImportError:  # pragma: no cover - CI installs pytest and nothing else
    Request = None

DATA_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data")
)
TOKEN_PATH = os.path.join(DATA_DIR, "api_token")

# Paths reachable without a token. Deliberately tiny: everything here must be
# safe to expose to anyone who can reach the port.
PUBLIC_PATHS = frozenset({"/api/health"})

BEARER_PREFIX = "bearer "


def load_or_create_token(path=None, *, env=None):
    """The API token: ``$API_TOKEN``, else the stored one, else a new one.

    Generating rather than refusing to start is deliberate. A server that will
    not boot without a hand-written token is a server people work around by
    disabling auth; one that prints a token on first run is one they use.
    """
    env = os.environ if env is None else env
    from_env = (env.get("API_TOKEN") or "").strip()
    if from_env:
        return from_env

    path = path or TOKEN_PATH
    try:
        with open(path, "r", encoding="utf-8") as handle:
            existing = handle.read().strip()
        if existing:
            return existing
    except OSError:
        pass

    token = secrets.token_urlsafe(32)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # Create 0600 from the outset rather than chmod-ing afterwards, so the
        # token is never briefly world-readable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
    except OSError as exc:
        print(
            f"⚠️ Could not save the API token to {path}: {exc}. "
            f"It will change on the next restart — set API_TOKEN to pin it."
        )
    return token


def token_from_request(headers):
    """The presented token, from ``Authorization: Bearer`` or ``X-API-Key``.

    Two header names because ``EventSource`` cannot send headers at all, so the
    dashboard reads the job stream with ``fetch`` instead; a query parameter
    would have been the easy alternative and would have put the token in every
    access log and browser history.
    """
    get = headers.get if hasattr(headers, "get") else lambda k, d=None: None

    raw = (get("authorization") or get("Authorization") or "").strip()
    if raw.lower().startswith(BEARER_PREFIX):
        candidate = raw[len(BEARER_PREFIX):].strip()
        if candidate:
            return candidate

    candidate = (get("x-api-key") or get("X-API-Key") or "").strip()
    return candidate or None


def token_is_valid(presented, expected):
    """Constant-time comparison. Empty never matches, including expected."""
    if not presented or not expected:
        return False
    return hmac.compare_digest(str(presented), str(expected))


def is_public(path):
    """Whether *path* may be reached without a token."""
    return path.rstrip("/") in {p.rstrip("/") for p in PUBLIC_PATHS}


def auth_disabled(env=None):
    """Escape hatch, off unless explicitly set. Never set in compose."""
    env = os.environ if env is None else env
    return str(env.get("DISABLE_AUTH", "")).strip().lower() in {"1", "true", "yes"}


_TOKEN = None


def current_token():
    """The process's token, resolved once."""
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = load_or_create_token()
    return _TOKEN


def announce(token=None, *, host_hint=None):
    """Print the token once at startup, with how to reach the app."""
    token = token or current_token()
    if auth_disabled():
        print("🔓 DISABLE_AUTH is set — the API is UNAUTHENTICATED. Do not "
              "expose this port.")
        return
    print(f"🔑 API token: {token}")
    print(f"   Stored in {TOKEN_PATH} (0600). Set API_TOKEN to pin it.")
    if host_hint:
        print(f"   From any device on your tailnet: {host_hint}")


async def require_token(request: "Request"):
    """FastAPI dependency: 401 unless a valid token is presented."""
    from fastapi import HTTPException

    if auth_disabled() or is_public(request.url.path):
        return

    presented = token_from_request(request.headers)
    if not token_is_valid(presented, current_token()):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
