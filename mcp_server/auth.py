"""Who may call the server.

Two doors, one secret (``MCP_TOKEN``):

* a **bearer header** -- Claude Code and any client that can send
  ``Authorization: Bearer <MCP_TOKEN>`` (``StaticTokenVerifier``);
* **OAuth** -- the claude.ai connector (web, desktop, phone) speaks OAuth 2.1
  with dynamic client registration and PKCE and cannot send a header. The
  server is its own authorization server (fastmcp's in-memory provider,
  which implements the whole flow), with two changes this single-user
  deployment needs on a public Funnel URL:

  1. the authorize step **asks for the secret** on a small login page
     instead of issuing a code to anyone who registers a client;
  2. clients and tokens are **kept on disk** (``outputs/mcp/oauth.json``), so
     a restart of the server does not log the connector out.

``MCP_PUBLIC_URL`` is the address clients reach the server at (the Funnel
URL); the OAuth metadata and the login page are built on it.
"""

from __future__ import annotations

import hmac
import html
import json
import os
import secrets
import tempfile
import threading
import time
from typing import Optional

from mcp.server.auth.provider import AccessToken, RefreshToken
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull

from fastmcp.server.auth import MultiAuth
from fastmcp.server.auth.providers.in_memory import AuthorizeError, InMemoryOAuthProvider
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

LOGIN_PATH = "/login"
PENDING_TTL_SECONDS = 600.0
STATE_REL = os.path.join("mcp", "oauth.json")
SCOPES = ["story"]

LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>rzdhop story — sign in</title>
<style>body{{font-family:system-ui,sans-serif;background:#111;color:#eee;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
form{{background:#1c1c1c;padding:2rem;border-radius:12px;min-width:320px;box-shadow:0 8px 30px #0008}}h1{{font-size:1.1rem;margin:0 0 1rem}}
input{{width:100%;box-sizing:border-box;padding:.6rem;border-radius:8px;border:1px solid #444;background:#111;color:#eee;margin:.3rem 0 1rem}}
button{{width:100%;padding:.7rem;border:0;border-radius:8px;background:#e8c547;color:#111;font-weight:600}}p.err{{color:#f66}}</style></head>
<body><form method="post" action="{action}"><h1>rzdhop story backend</h1>{error}
<label>Server token (MCP_TOKEN)<input type="password" name="secret" autofocus autocomplete="current-password"></label>
<input type="hidden" name="pending" value="{pending}"><button type="submit">Connect Claude</button></form></body></html>"""


class GatedOAuthProvider(InMemoryOAuthProvider):
    """The in-memory provider behind a login page, its state on disk."""

    def __init__(self, *, public_url: str, secret: str, state_path: Optional[str] = None, mcp_path: str = "/mcp"):
        super().__init__(
            base_url=public_url,
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=SCOPES,
                                                                  default_scopes=SCOPES),
            required_scopes=SCOPES,
        )
        self.public_url = public_url.rstrip("/")
        self.secret = secret
        self.state_path = state_path
        self.mcp_path = mcp_path
        self.pending: dict = {}   # pending_id -> (client, params, expires_at)
        self._lock = threading.RLock()
        self._load()

    # -- the gate

    async def authorize(self, client: OAuthClientInformationFull, params) -> str:
        """Park the request and send the person to the login page; the code
        is issued only once the secret has been typed there."""
        if client.client_id not in self.clients:
            raise AuthorizeError(error="unauthorized_client",
                                 error_description=f"Client '{client.client_id}' not registered.")
        self._sweep_pending()
        pending_id = secrets.token_urlsafe(24)
        with self._lock:
            self.pending[pending_id] = (client, params, time.time() + PENDING_TTL_SECONDS)
        return f"{self.public_url}{LOGIN_PATH}?pending={pending_id}"

    def _sweep_pending(self) -> None:
        now = time.time()
        with self._lock:
            for key in [k for k, (_c, _p, exp) in self.pending.items() if exp < now]:
                self.pending.pop(key, None)

    def login_page(self, pending_id: str, error: str = "") -> str:
        err = f'<p class="err">{html.escape(error)}</p>' if error else ""
        return LOGIN_PAGE.format(action=LOGIN_PATH, error=err, pending=html.escape(pending_id or ""))

    async def complete_login(self, pending_id: str, secret: str):
        """``(redirect_url, None)`` once the secret matches, else ``(None, reason)``."""
        self._sweep_pending()
        with self._lock:
            entry = self.pending.get(pending_id)
        if entry is None:
            return None, "This sign-in link has expired; start again from Claude."
        if not self.secret or not hmac.compare_digest(secret or "", self.secret):
            time.sleep(1.0)  # a wrong secret costs a second
            return None, "Wrong token."
        client, params, _exp = entry
        with self._lock:
            self.pending.pop(pending_id, None)
        url = await super().authorize(client, params)
        self._save()
        return url, None

    # -- persistence

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await super().register_client(client_info)
        self._save()

    async def exchange_authorization_code(self, client, authorization_code):
        token = await super().exchange_authorization_code(client, authorization_code)
        self._save()
        return token

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        token = await super().exchange_refresh_token(client, refresh_token, scopes)
        self._save()
        return token

    async def revoke_token(self, token) -> None:
        await super().revoke_token(token)
        self._save()

    def _save(self) -> None:
        if not self.state_path:
            return
        with self._lock:
            data = {
                "clients": {cid: c.model_dump(mode="json") for cid, c in self.clients.items()},
                "access_tokens": {t: a.model_dump(mode="json") for t, a in self.access_tokens.items()},
                "refresh_tokens": {t: r.model_dump(mode="json") for t, r in self.refresh_tokens.items()},
            }
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            handle, tmp = tempfile.mkstemp(dir=os.path.dirname(self.state_path), prefix=".oauth-", suffix=".tmp")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.state_path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    def _load(self) -> None:
        if not self.state_path or not os.path.isfile(self.state_path):
            return
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.clients = {cid: OAuthClientInformationFull.model_validate(c) for cid, c in (data.get("clients") or {}).items()}
            self.access_tokens = {t: AccessToken.model_validate(a) for t, a in (data.get("access_tokens") or {}).items()}
            self.refresh_tokens = {t: RefreshToken.model_validate(r) for t, r in (data.get("refresh_tokens") or {}).items()}
        except (OSError, ValueError) as exc:  # a corrupt file: start empty, keep the file for a look
            self.clients, self.access_tokens, self.refresh_tokens = {}, {}, {}
            print(f"mcp_server: {self.state_path} could not be read ({exc}); OAuth state starts empty")


def build_auth(*, token: str, public_url: str = "", state_path: Optional[str] = None):
    """The server's auth: None without a token (local use only); the bearer
    verifier alone without a public URL; both doors with one."""
    if not token:
        return None, None
    verifier = StaticTokenVerifier(tokens={token: {"client_id": "rzdhop-bearer", "scopes": SCOPES}},
                                   required_scopes=SCOPES)
    if not public_url:
        return verifier, None
    provider = GatedOAuthProvider(public_url=public_url, secret=token, state_path=state_path)
    return MultiAuth(server=provider, verifiers=[verifier], base_url=public_url, required_scopes=SCOPES), provider


def register_login_routes(mcp, provider: GatedOAuthProvider) -> None:
    """The login page the gated provider sends people to."""
    from starlette.responses import HTMLResponse, RedirectResponse

    @mcp.custom_route(LOGIN_PATH, methods=["GET"])
    async def login_form(request):
        pending = request.query_params.get("pending", "")
        return HTMLResponse(provider.login_page(pending))

    @mcp.custom_route(LOGIN_PATH, methods=["POST"])
    async def login_submit(request):
        form = await request.form()
        pending = str(form.get("pending") or "")
        url, error = await provider.complete_login(pending, str(form.get("secret") or ""))
        if url is None:
            return HTMLResponse(provider.login_page(pending, error or "Sign-in failed."), status_code=403)
        return RedirectResponse(url, status_code=303)
