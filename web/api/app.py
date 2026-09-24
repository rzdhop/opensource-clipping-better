"""
web.api.app — FastAPI Application Entry Point

rzdhop's clips — Web API

Run with:
    uvicorn web.api.app:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from clipping import __version__

from .auth import announce, require_token
from .routes import jobs, files, settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle."""
    print(f"🚀 rzdhop's clips v{__version__} — backend starting...")

    # A job whose worker thread died with the previous process is stuck in a
    # non-terminal status forever: nothing re-queues it and nothing fails it, so
    # the dashboard shows a job that is running and never will be again.
    from . import store as job_store

    announce(host_hint=os.environ.get("PUBLIC_URL") or None)

    stale = job_store.fail_stale_jobs()
    if stale:
        print(
            f"   ↻ Marked {len(stale)} interrupted job(s) as failed: "
            f"{', '.join(stale[:5])}{'...' if len(stale) > 5 else ''}"
        )

    # Restore whatever was saved from the Settings page. Keys entered there used
    # to vanish on every restart. Loaded HERE rather than at worker import time
    # on purpose (DEC-043): an import-time read of a secrets file means any test
    # that imports the worker picks up the developer's real keys.
    from . import settings_store, worker

    restored = worker.load_settings_env()
    if restored:
        print(
            f"   🔐 Restored {restored} saved setting(s) from "
            f"{settings_store.SETTINGS_PATH}"
        )

    # A job deleted while it was running is removed by its worker when it
    # stops. If the process died first, the request is still on the record.
    deleted = worker.finish_deferred_deletes()
    if deleted:
        print(f"   🗑 Finished {deleted} deletion(s) interrupted by the restart.")

    yield
    print("👋 Backend shutting down...")


app = FastAPI(
    title="rzdhop's clips",
    description="Turn long videos into vertical short-form clips.",
    version=__version__,
    lifespan=lifespan,
)

# CORS. The dashboard is now served by this same app, so the normal case needs
# no cross-origin permission at all. The localhost origins come back only for
# `DEV=1`, when the Vite dev server is running on its own port, and anything
# else has to be named explicitly in ALLOWED_ORIGINS.
_DEV = os.environ.get("DEV", "").strip().lower() in {"1", "true", "yes"}
_ALLOWED = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
if _DEV:
    _ALLOWED += [
        f"http://{host}:{port}"
        for host in ("localhost", "127.0.0.1")
        for port in (5173, 5174, 5175)
    ]
if _ALLOWED:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Routes
app.include_router(jobs.router)
app.include_router(files.router)
app.include_router(settings.router)


@app.get("/api")
async def api_root():
    return {
        "name": "rzdhop's clips",
        "version": __version__,
        "docs": "/docs",
        "health": "/api/health",
    }


import signal
import asyncio

@app.post("/api/shutdown", dependencies=[Depends(require_token)])
async def shutdown_server():
    """Trigger graceful shutdown of the FastAPI server.

    Behind the token like everything else. This was an unauthenticated kill
    switch on a port bound to 0.0.0.0.
    """
    # Send SIGINT to own process to trigger uvicorn graceful shutdown
    async def _shutdown():
        await asyncio.sleep(0.5)
        os.kill(os.getpid(), signal.SIGINT)
    
    asyncio.create_task(_shutdown())
    return {"status": "shutting down", "message": "Server is stopping..."}


# ---------------------------------------------------------------------------
# The dashboard mount goes at the very END of this file.
#
# A Mount at "/" matches every path that reaches it, so ANY route declared
# after it is unreachable. This was not theoretical: the mount used to sit
# above `POST /api/shutdown`, which meant that endpoint answered 405 instead of
# running -- in production only, because the mount is skipped when dist/ is
# absent, which is exactly the case on a dev box and in CI.
#
# Anything new goes above this line.
# ---------------------------------------------------------------------------
_DIST = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "dashboard", "dist")
)

# The check is for index.html, not for the directory. An empty dist/ exists in
# two ordinary situations -- a build that failed halfway, and the anonymous
# volume docker-compose creates as a mount point on the host -- and mounting
# StaticFiles over an empty directory answers 404 for every page, including the
# fallback below that would have explained the problem.
class _SPAStaticFiles(StaticFiles):
    """StaticFiles that really does fall back to index.html.

    ``html=True`` does NOT do this, despite how it reads. On a miss it looks for
    a ``404.html`` and, finding none, raises 404 -- so every client-side route
    (`/job/<id>`, `/new`, `/settings`) answered `{"detail":"Not Found"}` the
    moment it was refreshed, deep-linked or opened from a shared link. Clicking
    through from the home page worked, because that never leaves the SPA, which
    is why this survived: it is invisible unless you reload.

    Only paths that could plausibly BE a client-side route fall back. A miss
    under /assets/ or with a file extension still 404s -- answering a missing
    bundle or a missing favicon with HTML turns a cache problem into a blank page
    with no error in the console. And /api/ still 404s as JSON, so an unknown
    endpoint does not hand an API client a page of HTML to parse.
    """

    # Prefixes that are never a client-side route, whatever the router says.
    NEVER_SPA = ("api/", "assets/", "docs", "openapi.json")

    @classmethod
    def _is_spa_route(cls, path):
        if path.startswith(cls.NEVER_SPA):
            return False
        # "job/abc123" yes; "index-abc.js", "favicon.ico", "manifest.webmanifest" no.
        return "." not in path.rsplit("/", 1)[-1]

    async def get_response(self, path, scope):
        from starlette.exceptions import HTTPException as StarletteHTTPException

        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # StaticFiles RAISES on a miss rather than returning a 404 response,
            # so checking response.status_code here catches nothing -- the first
            # version of this class did exactly that and changed nothing.
            if exc.status_code != 404 or not self._is_spa_route(path):
                raise
            return await super().get_response("index.html", scope)


if os.path.isfile(os.path.join(_DIST, "index.html")):
    app.mount("/", _SPAStaticFiles(directory=_DIST, html=True), name="ui")
else:
    @app.get("/")
    async def _no_dashboard():
        return {
            "name": "rzdhop's clips",
            "dashboard": "not built",
            "hint": "run `npm ci && npm run build` in web/dashboard",
            "docs": "/docs",
            "health": "/api/health",
        }
