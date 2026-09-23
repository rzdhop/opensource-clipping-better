"""
web.api.routes.settings — Settings management endpoints.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time

from fastapi import Depends, APIRouter, HTTPException

from ..auth import require_token

# Imported rather than repeated so the API cannot drift from the pipeline.
from clipping import __version__
from clipping.config import AI_PROVIDER, WHISPER_DEVICE
from ..models import (
    ChainLinkResult,
    ChainTestRequest,
    ChainTestResponse,
    SettingsRequest,
    SettingsResponse,
    SystemHealthResponse,
)
from .. import store as job_store
from .. import worker
from ..config_adapter import env_flag, resolve_provider_keys

router = APIRouter(tags=["settings"], dependencies=[Depends(require_token)])


def _check_gpu() -> bool:
    """Whether a GPU is usable for transcription.

    Delegates to clipping.device so the health endpoint and the Whisper loader
    cannot disagree. Asking torch alone was misleading: the default CTranslate2
    wheel is CPU-only, so torch can report CUDA on a machine where Whisper
    still cannot use it.
    """
    from clipping.device import whisper_cuda_available

    return whisper_cuda_available()


def _check_ffmpeg() -> bool:
    """Check if ffmpeg is available."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _chain_blocked_reason(env) -> str:
    """What POST /api/jobs would refuse a chain job with right now, or ""."""
    from clipping.config import WEB_SLOW_CHAIN_HINT, chain_readiness

    readiness = chain_readiness(
        env.get("LLM_CHAIN", os.environ.get("LLM_CHAIN", "")),
        resolve_provider_keys(env),
        allow_slow=env_flag(env, "ALLOW_SLOW_CHAIN"),
        hint=WEB_SLOW_CHAIN_HINT,
    )
    return "" if readiness.ready else readiness.message


@router.get("/api/settings")
async def get_settings() -> SettingsResponse:
    """Get current settings (API keys are masked)."""
    env = worker.get_settings_env()

    # Check which keys are set (from worker env or os.environ)
    google_key = env.get("GOOGLE_API_KEY", os.environ.get("GOOGLE_API_KEY", ""))
    pexels_key = env.get("PEXELS_API_KEY", os.environ.get("PEXELS_API_KEY", ""))
    hf_token = env.get("HF_TOKEN", os.environ.get("HF_TOKEN", ""))
    nvidia_key = env.get("NVIDIA_API_KEY", os.environ.get("NVIDIA_API_KEY", ""))
    groq_key = env.get("GROQ_API_KEY", os.environ.get("GROQ_API_KEY", ""))
    openrouter_key = env.get("OPENROUTER_API_KEY", os.environ.get("OPENROUTER_API_KEY", ""))
    mistral_key = env.get("MISTRAL_API_KEY", os.environ.get("MISTRAL_API_KEY", ""))
    compat_key = env.get(
        "OPENAI_COMPAT_API_KEY", os.environ.get("OPENAI_COMPAT_API_KEY", "")
    )
    compat_url = env.get(
        "OPENAI_COMPAT_BASE_URL", os.environ.get("OPENAI_COMPAT_BASE_URL", "")
    )
    compat_model = env.get(
        "OPENAI_COMPAT_MODEL", os.environ.get("OPENAI_COMPAT_MODEL", "")
    )

    return SettingsResponse(
        google_api_key_set=bool(google_key),
        pexels_api_key_set=bool(pexels_key),
        hf_token_set=bool(hf_token),
        nvidia_api_key_set=bool(nvidia_key),
        groq_api_key_set=bool(groq_key),
        openrouter_api_key_set=bool(openrouter_key),
        mistral_api_key_set=bool(mistral_key),
        # The key is reported as a boolean only; the URL and model are not
        # secrets and both front ends need their values.
        openai_compat_api_key_set=bool(compat_key),
        openai_compat_base_url=compat_url,
        openai_compat_model=compat_model,
        allow_slow_chain=env_flag(env, "ALLOW_SLOW_CHAIN"),
        chain_blocked_reason=_chain_blocked_reason(env),
        default_clips=int(env.get("DEFAULT_CLIPS", "7")),
        default_ratio=env.get("DEFAULT_RATIO", "9:16"),
        default_font_style=env.get("DEFAULT_FONT_STYLE", "HORMOZI"),
        default_whisper_model=env.get("DEFAULT_WHISPER_MODEL", "large-v3"),
        # These two fall back to the REAL pipeline defaults, imported above, so
        # the API cannot report a default the pipeline does not use. They used to
        # say "cuda"/"gemini", wrong on both counts: "cuda" crashes on any
        # machine without a working CUDA stack -- the failure clipping/device.py
        # exists to prevent, and the reason two jobs in outputs/jobs.json died --
        # and "gemini" has not been the default provider since the local-first
        # refactor.
        default_whisper_device=env.get("DEFAULT_WHISPER_DEVICE", WHISPER_DEVICE),
        default_ai_provider=env.get("DEFAULT_AI_PROVIDER", AI_PROVIDER),
        gpu_available=_check_gpu(),
    )


@router.put("/api/settings")
async def update_settings(req: SettingsRequest) -> SettingsResponse:
    """Update settings (API keys and defaults)."""
    env_updates: dict[str, str] = {}

    if req.google_api_key is not None:
        env_updates["GOOGLE_API_KEY"] = req.google_api_key
    if req.pexels_api_key is not None:
        env_updates["PEXELS_API_KEY"] = req.pexels_api_key
    if req.hf_token is not None:
        env_updates["HF_TOKEN"] = req.hf_token
    if req.nvidia_api_key is not None:
        env_updates["NVIDIA_API_KEY"] = req.nvidia_api_key
    if req.groq_api_key is not None:
        env_updates["GROQ_API_KEY"] = req.groq_api_key
    if req.openrouter_api_key is not None:
        env_updates["OPENROUTER_API_KEY"] = req.openrouter_api_key
    if req.mistral_api_key is not None:
        env_updates["MISTRAL_API_KEY"] = req.mistral_api_key
    if req.openai_compat_api_key is not None:
        env_updates["OPENAI_COMPAT_API_KEY"] = req.openai_compat_api_key
    if req.openai_compat_base_url is not None:
        env_updates["OPENAI_COMPAT_BASE_URL"] = req.openai_compat_base_url
    if req.openai_compat_model is not None:
        env_updates["OPENAI_COMPAT_MODEL"] = req.openai_compat_model
    if req.allow_slow_chain is not None:
        # "" removes the override (DEC-043). Storing "0" would read as off but
        # shadow an ALLOW_SLOW_CHAIN=1 in .env forever, with no way back from
        # the UI.
        env_updates["ALLOW_SLOW_CHAIN"] = "1" if req.allow_slow_chain else ""
    if req.default_clips is not None:
        env_updates["DEFAULT_CLIPS"] = str(req.default_clips)
    if req.default_ratio is not None:
        env_updates["DEFAULT_RATIO"] = req.default_ratio.value if hasattr(req.default_ratio, "value") else req.default_ratio
    if req.default_font_style is not None:
        env_updates["DEFAULT_FONT_STYLE"] = req.default_font_style.value if hasattr(req.default_font_style, "value") else req.default_font_style
    if req.default_whisper_model is not None:
        env_updates["DEFAULT_WHISPER_MODEL"] = req.default_whisper_model
    if req.default_whisper_device is not None:
        env_updates["DEFAULT_WHISPER_DEVICE"] = req.default_whisper_device.value if hasattr(req.default_whisper_device, "value") else req.default_whisper_device
    if req.default_ai_provider is not None:
        env_updates["DEFAULT_AI_PROVIDER"] = req.default_ai_provider.value if hasattr(req.default_ai_provider, "value") else req.default_ai_provider

    worker.set_settings_env(env_updates)

    return await get_settings()


@router.get("/api/health")
async def health_check() -> SystemHealthResponse:
    """System health check."""
    return SystemHealthResponse(
        status="ok",
        version=__version__,
        gpu_available=_check_gpu(),
        ffmpeg_available=_check_ffmpeg(),
        jobs_running=job_store.get_running_count(),
        jobs_queued=job_store.get_queued_count(),
    )


# One chain test at a time per process: a double click must not spend two
# rounds of free-tier quota, and a second request is refused rather than queued
# behind a probe that can take minutes.
_CHAIN_TEST_LOCK = asyncio.Lock()

# The route never holds a connection longer than this, whatever the chain.
_CHAIN_TEST_CEILING_SECONDS = 300.0


def _probe_every_link(links, keys):
    """The blocking half: ping every keyed link, not just up to the first."""
    from clipping.providers import llm

    return llm.probe_chain(
        links, keys, work=None, stop_at_first=False, on_log=lambda *_: None
    )


@router.post("/api/settings/test-chain")
async def run_chain_test(req: ChainTestRequest) -> ChainTestResponse:
    """Ping every link of a chain and report each one (DEC-074).

    The only way to learn a link was dead used to be starting a job and
    waiting. This reuses ``llm.probe_chain`` -- the preflight's own primitive --
    but not ``preflight_chain``, which honours ``--no-preflight``, defers to the
    key gate and seeds the scan cache; none of that belongs in a diagnostic.

    The probe is synchronous and can take minutes (NVIDIA's ping allowance is
    120s), so it runs in the default thread pool -- NOT the worker's executor,
    which is sized to MAX_CONCURRENT_JOBS and would let a click stall a queued
    job. ``wait_for`` cancels the await, not the thread: on timeout the probe
    finishes in the background and its result is dropped. Each SDK client
    carries its own timeout, so that thread always ends.
    """
    from clipping.config import WEB_SLOW_CHAIN_HINT, chain_readiness
    from clipping.providers import llm, registry

    env = worker.get_settings_env()
    spec = (req.llm_chain or env.get("LLM_CHAIN", os.environ.get("LLM_CHAIN", ""))).strip()
    try:
        links = registry.parse_chain(spec) if spec else registry.chain_from_env()
    except registry.ChainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    keys = resolve_provider_keys(env)
    if _CHAIN_TEST_LOCK.locked():
        raise HTTPException(
            status_code=409, detail="A chain test is already running.")

    budget = sum(
        registry.probe_timeout(link) for link in links if keys.get(link.provider)
    ) + 10.0
    started = time.monotonic()
    async with _CHAIN_TEST_LOCK:
        try:
            live, results, _ = await asyncio.wait_for(
                asyncio.to_thread(_probe_every_link, links, keys),
                timeout=min(budget, _CHAIN_TEST_CEILING_SECONDS),
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail=(
                    f"The chain test gave up after {time.monotonic() - started:.0f}s. "
                    "At least one link is holding requests far past its probe "
                    "timeout."
                ),
            )
        except registry.ChainError as exc:
            # e.g. custom/<model> with no LLM_CUSTOM_BASE_URL
            raise HTTPException(status_code=400, detail=str(exc))

    rows = []
    for link, (label, reason, elapsed, kind) in zip(links, results):
        provider = registry.PROVIDERS[link.provider]
        status = "no_key" if kind == "skipped" else ("ok" if reason == "ok" else "failed")
        rows.append(ChainLinkResult(
            label=label,
            provider=link.provider,
            model=link.model,
            status=status,
            latency_seconds=None if elapsed is None else round(elapsed, 2),
            reason=None if status == "ok" else reason,
            probe_timeout_seconds=registry.probe_timeout(link),
            primary=provider.primary,
            env_key=provider.env_key,
            signup_url=provider.signup_url,
        ))

    readiness = chain_readiness(
        links, keys,
        allow_slow=env_flag(env, "ALLOW_SLOW_CHAIN"),
        hint=WEB_SLOW_CHAIN_HINT,
    )
    if live is None:
        message = llm.preflight_message(results)
    elif not readiness.ready:
        message = readiness.message
    else:
        message = ""

    return ChainTestResponse(
        chain=",".join(registry.describe(link) for link in links),
        ready=live is not None and readiness.ready,
        live_link=None if live is None else registry.describe(live),
        results=rows,
        elapsed_seconds=round(time.monotonic() - started, 2),
        message=message,
    )
