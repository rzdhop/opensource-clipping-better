"""An opt-in bearer token on every API route (DEC-037, made opt-in by DEC-173).

Auth is ON exactly when ``API_TOKEN`` is set. With nothing set the API is open:
this app runs on localhost or a private tailnet, and asking for a token there
was friction with no one to keep out. The two paths that do reach the public
internet stay guarded -- a public ``DOMAIN`` (the Caddy profile) refuses to
start without a token, and the Kaggle notebook sets its own -- and in open mode
a browser's cross-site write is refused, which blocks another website, never
the user.

When auth is on, the token is checked with `hmac.compare_digest` on every
router, `/api/health` stays open (it reports only booleans and counts), and
media the browser fetches by itself carries a signed, expiring URL.

Stdlib plus FastAPI. The FastAPI import is deferred into the dependency so the
pure functions stay testable in the pytest-only CI environment.
"""

from __future__ import annotations

import hashlib
import hmac
import os
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
# Where the token USED to be generated and stored. It is no longer read or
# written (DEC-173); the startup banner names a leftover file so an install that
# relied on it knows to move its value into API_TOKEN.
TOKEN_PATH = os.path.join(DATA_DIR, "api_token")

# Paths reachable without a token. Deliberately tiny: everything here must be
# safe to expose to anyone who can reach the port.
PUBLIC_PATHS = frozenset({"/api/health"})

BEARER_PREFIX = "bearer "


def configured_token(env=None):
    """The API token from ``$API_TOKEN``, or None when it is unset or blank.

    The only source. A generated, stored token (the old default) made every
    fresh start ask for a credential on machines nobody else can reach.
    """
    env = os.environ if env is None else env
    return (env.get("API_TOKEN") or "").strip() or None


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
    """Force auth off even with a token set. Never set in compose.

    Kept for the machines and tests that already set it; with auth opt-in it
    only matters when API_TOKEN is set too.
    """
    env = os.environ if env is None else env
    return str(env.get("DISABLE_AUTH", "")).strip().lower() in {"1", "true", "yes"}


# A pinned token, for tests that stand in for a configured server. When None,
# the environment is read on every call -- no cache, so a token set or unset
# at runtime takes effect at once and nothing stale outlives a test.
_TOKEN = None


def current_token():
    """The token this server requires, or None when auth is off."""
    return _TOKEN if _TOKEN is not None else configured_token()


def auth_enabled(env=None):
    """Whether requests need a token: one is configured and DISABLE_AUTH is not set.

    With *env*, decided from that mapping alone; without it, from this process
    (the environment, or a pinned token).
    """
    if auth_disabled(env):
        return False
    if env is not None:
        return configured_token(env) is not None
    return bool(current_token())


def open_public_exposure(env=None):
    """Why this server must not start, or None.

    ``DOMAIN`` is set only by the Caddy profile, which serves the API on the
    public internet with a certificate. Open there, anyone could read every job,
    rewrite the provider keys and spend on them, or shut the server down.
    """
    env = os.environ if env is None else env
    domain = (env.get("DOMAIN") or "").strip()
    if not domain or auth_enabled(env):
        return None
    why = "DISABLE_AUTH is set" if auth_disabled(env) else "API_TOKEN is not set"
    return (
        f"DOMAIN={domain} serves this API on the public internet, but {why}. "
        "Set API_TOKEN (and unset DISABLE_AUTH), or unset DOMAIN to stay private."
    )


def announce(token=None, *, host_hint=None):
    """Say at startup whether the API asks for a token, and how to reach it.

    The token itself is never printed: it was set by the operator, and a log
    line is the easiest place for it to leak from.
    """
    if auth_disabled():
        print("🔓 DISABLE_AUTH is set — the API is UNAUTHENTICATED. Do not "
              "expose this port.")
        return
    if token or current_token():
        print("🔑 Token auth is ON (API_TOKEN is set): send it as "
              "'Authorization: Bearer <token>'.")
    else:
        print("🔓 No API_TOKEN — the API is OPEN to anyone who can reach this "
              "port. Keep it on localhost or a private tailnet; set API_TOKEN "
              "before exposing it.")
        if os.path.exists(TOKEN_PATH):
            print(f"   {TOKEN_PATH} is no longer read: set API_TOKEN to its "
                  "value to keep token auth.")
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


def _signing_secret(token=None):
    """The token to derive a signing key from; never a value anyone could guess.

    With no token, ``str(None)`` or ``""`` would be a public key and every
    signature forgeable, so this raises instead. Open servers mint plain paths
    and never get here.
    """
    secret = token if token is not None else current_token()
    if not secret:
        raise ValueError("no API token to sign with: auth is off")
    return str(secret).encode("utf-8")


def _media_key(token=None):
    """The signing key: HMAC of the API token under a fixed context string."""
    return hmac.new(_signing_secret(token), MEDIA_KEY_CONTEXT, hashlib.sha256).digest()


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
    """A URL for one output file, fetchable with no headers.

    Signed and expiring when auth is on (or a token is passed); the plain path
    when it is off, because the gate lets it through and there is no secret to
    sign with.
    """
    path = f"/api/outputs/{quote(str(job_id), safe='')}/{quote(str(filename), safe='')}"
    if token is None and not auth_enabled():
        return path
    exp = media_expiry(now=now, ttl=ttl)
    sig = sign_media(job_id, filename, exp, token=token)
    return f"{path}?exp={exp}&sig={sig}"


def media_signature_is_valid(job_id, filename, exp, sig, *, token=None, now=None):
    """Whether *sig* attests this exact file and has not expired.

    Never raises, and never allows on error: a malformed ``exp`` is a rejection,
    not an exception that some caller might catch into a default of True.
    """
    if not job_id or not filename or not sig:
        return False
    if not (token or current_token()):
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

    Only when auth is on (DEC-173): with no API_TOKEN every request passes, and
    the check returns before the token comparison -- whose "empty never matches"
    rule would otherwise turn an open server into one that refuses everyone.

    The header is checked first; the signature is only consulted when no valid
    token was presented, so nothing about the authenticated path changes.
    """
    from fastapi import HTTPException

    if not auth_enabled() or is_public(request.url.path):
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


# ---------------------------------------------------------------------------
# Cross-site writes, refused while the API is open (DEC-173)
#
# With no token, the only thing between a website open in the same browser and
# POST /api/shutdown, an upload or PUT /api/settings was nothing: a form POST
# or a no-cors fetch is a "simple" request the browser sends without asking.
# Browsers label every request with Sec-Fetch-Site, so a write marked
# "cross-site" is refused. The dashboard is same-origin and curl or a script
# sends no such header, so neither is ever caught; the Vite dev server on
# another localhost port is "same-site", not cross-site. With a token the guard
# steps aside -- a cross-site request cannot carry the Authorization header, so
# the gate already refuses it.
# ---------------------------------------------------------------------------

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_cross_site_write(method, headers):
    """Whether a browser sent a state-changing request from another site."""
    if str(method or "").upper() in SAFE_METHODS:
        return False
    get = headers.get if hasattr(headers, "get") else lambda k, d=None: None
    return str(get("sec-fetch-site") or "").strip().lower() == "cross-site"


class CrossSiteWriteGuard:
    """Pure ASGI middleware: 403 for a cross-site write while auth is off.

    Plain ASGI rather than BaseHTTPMiddleware, so a request it lets through --
    a video's range read, the job stream -- reaches the app untouched and
    unbuffered. Origins named in ALLOWED_ORIGINS are trusted and may write.
    """

    def __init__(self, app, allowed_origins=()):
        self.app = app
        self.allowed_origins = frozenset(o.rstrip("/") for o in allowed_origins if o)

    def refuses(self, method, headers):
        if auth_enabled() or not is_cross_site_write(method, headers):
            return False
        origin = str(headers.get("origin") or "").rstrip("/")
        return origin not in self.allowed_origins

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers") or []
            }
            if self.refuses(scope.get("method"), headers):
                from starlette.responses import JSONResponse

                response = JSONResponse(
                    {"detail": "Cross-site write refused: this API has no token, "
                               "so only its own pages may change anything."},
                    status_code=403,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
