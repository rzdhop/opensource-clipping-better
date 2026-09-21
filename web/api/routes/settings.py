"""
web.api.routes.settings — Settings management endpoints.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from fastapi import Depends, APIRouter

from ..auth import require_token

# Imported rather than repeated so the API cannot drift from the pipeline.
from clipping.config import AI_PROVIDER, WHISPER_DEVICE
from ..models import SettingsRequest, SettingsResponse, SystemHealthResponse
from .. import store as job_store
from .. import worker

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
        version="1.12.0",
        gpu_available=_check_gpu(),
        ffmpeg_available=_check_ffmpeg(),
        jobs_running=job_store.get_running_count(),
        jobs_queued=job_store.get_queued_count(),
    )
