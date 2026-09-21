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


# The built dashboard, served by this same app so the browser talks to one
# origin and api.js needs no configuration. Mounted LAST: a mount at "/" would
# otherwise swallow every /api route above it.
#
# html=True makes unknown paths fall back to index.html, which is what a
# single-page app's client-side routes need in order to survive a refresh.
_DIST = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "dashboard", "dist")
)
if os.path.isdir(_DIST):
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="ui")
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
