"""
web.api.routes.hardware — what this machine can generate locally (spec 8.2).
"""

from __future__ import annotations

import asyncio
import os
import threading
import time

from fastapi import APIRouter, Depends

from clipping.aistory.hardware import probe, to_dict

from .. import worker
from ..auth import require_token

router = APIRouter(tags=["hardware"], dependencies=[Depends(require_token)])

# The probe shells out (nvidia-smi, system_profiler) and pings ComfyUI and
# Ollama; the Settings page asks on every visit, so one answer is kept for a
# minute. ``?refresh=1`` forces a new probe.
CACHE_SECONDS = 60.0
_cache = {"at": 0.0, "value": None}
_lock = threading.Lock()


def reset_cache() -> None:
    with _lock:
        _cache["at"] = 0.0
        _cache["value"] = None


def _env() -> dict:
    """The process env with the saved Settings on top (LOCAL_COMFYUI_URL, LOCAL_OLLAMA_URL)."""
    merged = dict(os.environ)
    merged.update({k: v for k, v in worker.get_settings_env().items() if v is not None})
    return merged


def _probe_now() -> dict:
    return to_dict(probe(env=_env()))


@router.get("/api/hardware")
async def get_hardware(refresh: int = 0) -> dict:
    now = time.monotonic()
    with _lock:
        cached = _cache["value"]
        fresh = cached is not None and now - _cache["at"] < CACHE_SECONDS
    if fresh and not refresh:
        return cached
    value = await asyncio.to_thread(_probe_now)
    with _lock:
        _cache["at"] = time.monotonic()
        _cache["value"] = value
    return value
