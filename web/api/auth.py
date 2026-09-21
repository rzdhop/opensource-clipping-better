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

import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import quote

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


# ---------------------------------------------------------------------------
# Signed media URLs
#
# A <video src> and an <a href download> are requests the BROWSER makes, not
# fetch() calls, so they cannot carry a header -- and every /api/outputs/ route
# is header-gated. The result was the bug this exists to fix: the player showed
# nothing, the download button saved the 401's JSON body (the browser rewrote the
# extension to match its application/json type), and a pasted URL said the file
# was not available. The clips were fine; the request for them was unauthorized.
#
# The rule in token_from_request -- the credential never travels in a URL -- is
# NOT relaxed here. A signature is not the credential: it is an HMAC over one
# (job_id, filename, exp) triple, keyed by a value DERIVED from the token, so it
# authorizes exactly one file, expires, and cannot be reversed into the token or
# replayed on another path. A leaked media URL reads one mp4 for a few hours; a
# leaked token is the whole API, POST /api/shutdown included.
# ---------------------------------------------------------------------------

# Domain separation: the signing key is not the token, so a captured signature
# gives an attacker nothing to grind against the real credential.
MEDIA_KEY_CONTEXT = b"rzc-media-url-v1"

DEFAULT_MEDIA_URL_TTL = 12 * 3600
# Below this, a URL could expire while the page that minted it is still loading.
MIN_MEDIA_URL_TTL = 300


def media_url_ttl(env=None):
    """Lifetime of a minted media URL in seconds. ``MEDIA_URL_TTL`` overrides."""
    env = os.environ if env is None else env
    try:
        ttl = int(str(env.get("MEDIA_URL_TTL", "")).strip())
    except (TypeError, ValueError):
        return DEFAULT_MEDIA_URL_TTL
    return ttl if ttl >= MIN_MEDIA_URL_TTL else DEFAULT_MEDIA_URL_TTL


def _media_key(token=None):
    """The signing key: HMAC of the API token under a fixed context string."""
    secret = str(token if token is not None else current_token()).encode("utf-8")
    return hmac.new(secret, MEDIA_KEY_CONTEXT, hashlib.sha256).digest()


def _media_payload(job_id, filename, exp):
    """The exact bytes that get signed.

    Length-prefixed so no delimiter choice can make two different triples
    produce the same signed string -- without it, ("a|b", "c") and ("a", "b|c")
    would collide.

    Signed from the DECODED job_id and filename, never from request.url.path.
    A minted URL has to percent-encode the filename and the path does not, and
    the two spellings are not guaranteed to round-trip for a name containing a
    space or a non-ASCII character. Signing the decoded values makes that whole
    class of "401 on exactly those clips" impossible. Current names are ASCII
    (highlight_rank_N_ready.mp4) but they come from the render layer, not from a
    sanitiser.
    """
    return (
        f"{len(job_id)}:{job_id}|{len(filename)}:{filename}|{int(exp)}"
    ).encode("utf-8")


def sign_media(job_id, filename, exp, *, token=None):
    """The hex signature for one (job_id, filename, exp) triple."""
    return hmac.new(
        _media_key(token), _media_payload(job_id, filename, exp), hashlib.sha256
    ).hexdigest()


def media_expiry(now=None, ttl=None):
    """An expiry timestamp, QUANTISED into buckets of ttl/2.

    The quantisation is the non-obvious part, and it is not cosmetic. With
    ``exp = now + ttl`` every response would mint a different URL for the same
    file, and handing a <video> a new `src` TEARS DOWN AND RESTARTS playback and
    throws away the browser's cached bytes. The dashboard re-fetches the job
    whenever the page re-renders, so that would happen constantly.

    Bucketing makes the minted URL byte-identical for every response inside the
    window, so a re-fetch is a no-op for the player and the cache keeps working
    across navigations. It also makes the tests deterministic without freezing
    the clock. Effective lifetime is ttl/2 to ttl.
    """
    ttl = media_url_ttl() if ttl is None else int(ttl)
    now = time.time() if now is None else float(now)
    bucket = max(1, ttl // 2)
    return int((int(now // bucket) + 2) * bucket)


def media_url(job_id, filename, *, token=None, now=None, ttl=None):
    """A signed, expiring URL for one output file, fetchable with no headers."""
    exp = media_expiry(now=now, ttl=ttl)
    sig = sign_media(job_id, filename, exp, token=token)
    path = f"/api/outputs/{quote(str(job_id), safe='')}/{quote(str(filename), safe='')}"
    return f"{path}?exp={exp}&sig={sig}"


def media_signature_is_valid(job_id, filename, exp, sig, *, token=None, now=None):
    """Whether *sig* attests this exact file and has not expired.

    Never raises, and never allows on error: a malformed ``exp`` is a rejection,
    not an exception that some caller might catch into a default of True.
    """
    if not job_id or not filename or not sig:
        return False
    try:
        exp_int = int(str(exp).strip())
    except (TypeError, ValueError):
        return False

    now = time.time() if now is None else float(now)
    if exp_int <= now:
        return False

    expected = sign_media(job_id, filename, exp_int, token=token)
    return hmac.compare_digest(str(sig), expected)


# Only a path under one of these may ever be opened by a signature instead of a
# header. Not a route list: the scope comes from the three conditions in
# signed_media_request_is_valid, all of which must hold.
SIGNABLE_PREFIXES = ("/api/outputs/",)


def signed_media_request_is_valid(request):
    """Whether *request* carries a signature that attests this exact file.

    Three independent conditions, all required:

    1. the path is under SIGNABLE_PREFIXES;
    2. the matched route has BOTH a ``job_id`` and a ``filename`` path
       parameter -- Starlette fills ``path_params`` during routing, before
       dependencies run;
    3. the HMAC over that exact (job_id, filename, exp) triple verifies and
       ``exp`` is still in the future.

    Condition 2 is what keeps this tight, and it is worth spelling out.
    ``GET /api/outputs/{job_id}`` -- the directory listing -- has no
    ``filename`` parameter, so no signature can ever open it and it stays 401.
    A signature minted for a clip, pasted onto ``/api/jobs``, ``/api/settings``,
    ``/api/upload`` or ``/api/shutdown``, fails all three conditions.

    The ``filename`` half of condition 2 is deliberately redundant with
    media_signature_is_valid's own emptiness check -- a mutation test confirms
    removing either one still keeps the listing private. Both are kept so that
    refactoring one away later cannot quietly open it.

    Never raises. A malformed request is a rejection, not an exception that
    could escape into a 500 or, worse, into some caller's default of True.
    """
    try:
        if not request.url.path.startswith(SIGNABLE_PREFIXES):
            return False

        params = request.path_params or {}
        job_id = params.get("job_id")
        filename = params.get("filename")
        if not job_id or not filename:
            return False

        query = request.query_params
        return media_signature_is_valid(
            job_id, filename, query.get("exp"), query.get("sig")
        )
    except Exception:  # noqa: BLE001 - fail closed, whatever went wrong
        return False


async def require_token(request: "Request"):
    """FastAPI dependency: 401 unless a valid token or media signature is given.

    The header is checked first; the signature is only consulted when no valid
    token was presented, so nothing about the authenticated path changes.
    """
    from fastapi import HTTPException

    if auth_disabled() or is_public(request.url.path):
        return

    presented = token_from_request(request.headers)
    if token_is_valid(presented, current_token()):
        return

    # A browser cannot send a header for a <video src> or an <a href download>,
    # so one signed, expiring, single-file capability is accepted in its place.
    if signed_media_request_is_valid(request):
        return

    raise HTTPException(
        status_code=401,
        detail="Missing or invalid API token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
