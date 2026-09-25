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
    GenerationChainTestRequest,
    GenerationChainTestResponse,
    GenerationLinkResult,
    SettingsRequest,
    SettingsResponse,
    SystemHealthResponse,
)
from .. import settings_store
from .. import store as job_store
from .. import worker
from . import files as files_route
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


def _budget_fields(env) -> dict:
    """The budget the settings describe, read the way the pipeline reads it (DEC-097)."""
    from clipping.providers import budget as budget_mod

    merged = {name: env.get(name, os.environ.get(name, "")) for name in budget_mod.ENV_NAMES}
    resolved = budget_mod.budget_from_env(merged)
    return {
        "allow_paid": resolved.allow_paid,
        "per_episode_cap_usd": resolved.per_episode_cap_usd,
        "daily_cap_usd": resolved.daily_cap_usd,
        "per_story_cap_usd": resolved.per_story_cap_usd,
        "budget_profile": str(merged.get("BUDGET_PROFILE") or "").strip().lower(),
        "effective_budget_profile": resolved.profile,
        "spend_today_usd": budget_mod.day_spent(),
    }


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
        **_budget_fields(env),
        **_generation_fields(env),
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
    # Budget (AI Story, DEC-097): same clearing rule for the switch; the caps
    # are stored as amounts; the profile is validated before it is stored.
    if req.allow_paid is not None:
        env_updates["ALLOW_PAID"] = "1" if req.allow_paid else ""
    for name, value in (("PER_EPISODE_CAP_USD", req.per_episode_cap_usd),
                        ("DAILY_CAP_USD", req.daily_cap_usd),
                        ("PER_STORY_CAP_USD", req.per_story_cap_usd)):
        if value is not None:
            if value <= 0:
                raise HTTPException(status_code=400, detail=f"{name} must be a positive amount in USD")
            env_updates[name] = f"{value:.2f}"
    if req.budget_profile is not None:
        from clipping.providers.budget import resolve_profile

        try:
            resolve_profile(req.budget_profile, True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        env_updates["BUDGET_PROFILE"] = req.budget_profile.strip().lower()
    # Generation providers (spec 8.6): keys clear on "" like every key; the
    # local URLs are stored as typed and normalised when read.
    for name, value in (("FAL_KEY", req.fal_key), ("OPENAI_API_KEY", req.openai_api_key),
                        ("CLOUDFLARE_API_TOKEN", req.cloudflare_api_token),
                        ("CLOUDFLARE_ACCOUNT_ID", req.cloudflare_account_id),
                        ("POLLINATIONS_API_KEY", req.pollinations_api_key),
                        ("LOCAL_COMFYUI_URL", req.local_comfyui_url), ("LOCAL_OLLAMA_URL", req.local_ollama_url)):
        if value is not None:
            env_updates[name] = value.strip()
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
    """The blocking half: ask every keyed link the real request (DEC-090)."""
    from clipping.analysis import diagnostic
    from clipping.providers import llm

    return llm.diagnose_chain(
        links, keys, diagnostic.diagnostic_work(),
        judge=diagnostic.judge, on_log=lambda *_: None,
    )


@router.post("/api/settings/test-chain")
async def run_chain_test(req: ChainTestRequest) -> ChainTestResponse:
    """Ask every keyed link of a chain the real analysis request (DEC-090).

    The only way to learn a link was dead used to be starting a job and
    waiting; then this route sent a one-word ping, which on 2026-09-24 called a
    chain ready whose only working link was the NVIDIA floor. It now sends each
    keyed link the scan's own request on a 14-beat test transcript with one
    clip in it, and says what each found. The job gate itself is unchanged and
    stays key-based (DEC-073); ``verdict`` is this route's own finding.

    It runs in the default thread pool -- NOT the worker's executor, which is
    sized to MAX_CONCURRENT_JOBS and would let a click stall a queued job.
    ``wait_for`` cancels the await, not the thread: on timeout the requests
    finish in the background and their results are dropped. Each SDK client
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

    budget = registry.diagnostic_budget(links, keys)
    started = time.monotonic()
    async with _CHAIN_TEST_LOCK:
        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(_probe_every_link, links, keys),
                timeout=min(budget, _CHAIN_TEST_CEILING_SECONDS),
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail=(
                    f"The chain test gave up after {time.monotonic() - started:.0f}s. "
                    "At least one provider is holding requests far past its "
                    "allowance."
                ),
            )
        except registry.ChainError as exc:
            # e.g. custom/<model> with no LLM_CUSTOM_BASE_URL
            raise HTTPException(status_code=400, detail=str(exc))

    busy = job_store.get_running_count() > 0
    rows = [
        _link_row(registry, link, probe, busy)
        for link, probe in zip(links, results)
    ]

    # Keys that are set for a provider the chain does not name. Never
    # contacted (DEC-023): the row says how to put the key to work instead.
    named = {link.provider for link in links}
    for name in registry.PROVIDERS:
        if keys.get(name) and name not in named:
            provider = registry.PROVIDERS[name]
            suggestion = registry.suggested_link(name)
            rows.append(ChainLinkResult(
                label=suggestion,
                provider=name,
                model=registry.default_model(name) or "",
                status="unused",
                probe_timeout_seconds=provider.probe_timeout,
                primary=provider.primary,
                env_key=provider.env_key,
                signup_url=provider.signup_url,
                note=(
                    f"{provider.env_key} is set, but this chain does not name "
                    f"{name}, so nothing uses it. Add {suggestion} to LLM_CHAIN "
                    f"to use it."
                ),
            ))

    allow_slow = env_flag(env, "ALLOW_SLOW_CHAIN")
    readiness = chain_readiness(
        links, keys, allow_slow=allow_slow, hint=WEB_SLOW_CHAIN_HINT,
    )
    completed = [
        (link, probe) for link, probe in zip(links, results)
        if probe.kind == "work" and probe.reason == "ok"
    ]
    live_link = (
        f"{completed[0][0].provider}/{completed[0][1].used_model}"
        if completed else None
    )

    if not any(keys.get(link.provider) for link in links):
        verdict = "dead"
        wanted = [
            registry.PROVIDERS[link.provider] for link in links
            if registry.is_primary(link)
        ] or [registry.PROVIDERS[link.provider] for link in links]
        message = (
            "No link in this chain has an API key, so a job cannot run. Set "
            "one of: "
            + ", ".join(f"{p.env_key} ({p.signup_url or 'your own endpoint'})"
                        for p in dict.fromkeys(wanted))
            + "."
        )
    elif not readiness.ready:
        verdict = "blocked"
        message = readiness.message
    elif not completed:
        verdict = "dead"
        alive = [probe.label for probe in results if probe.kind == "ping"
                 and probe.reason == "ok"]
        if alive:
            message = (
                "No link completed the real analysis request, so a job's "
                "analysis would fail. " + ", ".join(alive) + " answered a plain "
                "ping, so the key works but the model could not do the job; "
                "each row says why."
            )
        else:
            message = llm.preflight_message(results)
    elif (
        allow_slow
        or any(registry.is_primary(link) for link, _ in completed)
        or not any(registry.is_primary(link) for link in links)
    ):
        verdict = "ready"
        message = ""
    else:
        verdict = "floor_only"
        failed = [
            registry.describe(link) for link, probe in zip(links, results)
            if registry.is_primary(link) and keys.get(link.provider)
            and not (probe.kind == "work" and probe.reason == "ok")
        ]
        floor = ", ".join(registry.describe(link) for link, _ in completed)
        message = (
            f"Only the chain's floor completed the real analysis request. "
            f"{', '.join(failed)} did not (see its row), so a job would run on "
            f"{floor} alone: about 12 tokens/s behind a queue, and it may not "
            f"finish inside the time budget. Fix that link or set another key."
        )

    return ChainTestResponse(
        chain=",".join(registry.describe(link) for link in links),
        verdict=verdict,
        ready=verdict == "ready",
        live_link=live_link,
        results=rows,
        elapsed_seconds=round(time.monotonic() - started, 2),
        message=message,
    )


def _link_row(registry, link, probe, busy):
    """One chain link's ``ChainLinkResult`` from its ``llm.LinkProbe``."""
    provider = registry.PROVIDERS[link.provider]
    judgement = probe.judgement
    if probe.kind == "skipped":
        status = "no_key"
    elif probe.reason == "ok":
        status = "ok" if probe.kind == "work" else "alive"
    else:
        status = "failed"

    notes = []
    if probe.used_model and probe.used_model != link.model:
        notes.append(
            f"{link.model} is not available on this key; {probe.used_model} "
            f"answered instead, and jobs will use it too."
        )
    if status == "ok" and judgement is not None and judgement.note:
        notes.append(judgement.note)
    if status == "alive":
        notes.append(
            "The key works, but the model could not complete the real "
            "analysis request."
        )
    if busy and link.provider == "nvidia" and status != "no_key":
        notes.append(
            "A job is running. NVIDIA answers one request at a time per key, "
            "so this one may have waited behind it."
        )

    if status == "ok":
        reason = None
    elif status == "alive":
        reason = probe.work_error
    else:
        reason = probe.reason
    return ChainLinkResult(
        label=probe.label,
        provider=link.provider,
        model=link.model,
        status=status,
        latency_seconds=None if probe.elapsed is None else round(probe.elapsed, 2),
        reason=reason,
        probe_timeout_seconds=registry.probe_timeout(link),
        work_timeout_seconds=registry.diagnostic_timeout(link),
        primary=provider.primary,
        env_key=provider.env_key,
        signup_url=provider.signup_url,
        kind=None if probe.kind == "skipped" else probe.kind,
        candidates=None if judgement is None else judgement.candidates,
        found_moment=None if judgement is None else judgement.found_moment,
        level=probe.level,
        used_model=probe.used_model,
        note=" ".join(notes),
    )


# =============================================================================
# Generation chains (spec 8.6, DEC-103)
# =============================================================================

# The transport every generation call of this module goes through; tests
# inject a fake here so nothing leaves the process.
_TRANSPORT = None

# Samples live under outputs/<CHAIN_TEST_DIRNAME>/ so the existing signed
# outputs route serves them to a phone's <img>/<audio> with no header; the id
# is reserved and never a job. Paid samples are booked in this ledger.
CHAIN_TEST_DIRNAME = "_chain_test"
CHAIN_TEST_LEDGER = os.path.join(settings_store.DATA_DIR, "chain_test_ledger.json")

_TEST_IMAGE_PROMPT = ("A single ripe kiwi fruit with a small friendly cartoon face, sitting on a warm wooden table, "
                      "soft studio light, vertical composition, no text")
_TEST_EDIT_PROMPT = "Keep this exact fruit character and its face; add a small white linen shirt with an open collar"
_TEST_TEXT = {"fr": "Bonjour, ceci est un test de voix pour rzdhop AI.", "en": "Hello, this is a voice test for rzdhop AI."}
_TEST_VISION_PROMPT = "Describe this image in one sentence: subject, colours, lighting."


def _merged_env(env) -> dict:
    """The saved Settings on top of the process environment, as every reader sees them."""
    merged = dict(os.environ)
    for name, value in (env or {}).items():
        if value:
            merged[name] = value
        else:
            merged.pop(name, None)
    return merged


def _api_model_id(kind, link) -> str:
    """The provider's own model id behind a chain link (for the ledger)."""
    from clipping.providers import images, tts, vision

    tables = {
        "cloudflare": images.CLOUDFLARE_MODELS, "fal": images.FAL_APPS,
        "gemini": {**images.GEMINI_MODELS, **tts.GEMINI_TTS_MODELS, **vision.GEMINI_VISION_MODELS},
        "openai": {name: pair[0] for name, pair in images.OPENAI_MODELS.items()},
    }
    return tables.get(link.provider, {}).get(link.model, link.model)


def _link_summary(kind, link, merged, budget_obj) -> dict:
    from clipping.providers import budget as budget_mod, generation as gen, pricing

    provider = gen.provider_for(link)
    missing = gen.missing_keys(link, merged)
    paid = gen.is_paid(link)
    est = 0.0
    if paid:
        try:
            est = pricing.estimate(link, 1, width=1080, height=1920).est_usd
        except pricing.PriceUnknown:
            est = 0.0
    allowed = not missing
    reason = None
    if allowed and paid:
        try:
            budget_mod.check(est, link, budget=budget_obj, day_spent=budget_mod.day_spent())
        except budget_mod.BudgetRefused as exc:
            allowed, reason = False, str(exc)
    return {
        "label": gen.describe(link), "provider": link.provider, "model": link.model,
        "paid": paid, "keyed": not missing, "missing_keys": missing,
        "adapter": gen.adapter_for(kind, link.provider) is not None,
        "allowed": allowed, "est_usd": est, "reason": reason,
        "env_keys": list(provider.env_keys), "signup_url": provider.signup_url,
    }


def _generation_fields(env) -> dict:
    from clipping.providers import adapters, budget as budget_mod, generation as gen, limits

    adapters.load_all()
    merged = _merged_env(env)
    budget_obj = budget_mod.budget_from_env({name: merged.get(name, "") for name in budget_mod.ENV_NAMES})
    chains = {}
    for kind in gen.KINDS:
        raw = (merged.get(gen.ENV_NAMES[kind]) or "").strip()
        entry = {"env": gen.ENV_NAMES[kind], "source": "env" if raw else "default", "chain": raw or gen.DEFAULT_CHAINS[kind],
                 "links": [], "error": None}
        try:
            links = gen.parse_generation_chain(kind, raw or gen.DEFAULT_CHAINS[kind])
        except gen.ChainError as exc:
            entry["error"] = str(exc)
            links = []
        entry["links"] = [_link_summary(kind, link, merged, budget_obj) for link in links]
        chains[kind] = entry
    return {
        "fal_key_set": bool(merged.get("FAL_KEY")),
        "openai_api_key_set": bool(merged.get("OPENAI_API_KEY")),
        "cloudflare_api_token_set": bool(merged.get("CLOUDFLARE_API_TOKEN")),
        "cloudflare_account_id_set": bool(merged.get("CLOUDFLARE_ACCOUNT_ID")),
        "pollinations_api_key_set": bool(merged.get("POLLINATIONS_API_KEY")),
        "local_comfyui_url": gen.local_url("comfyui", merged),
        "local_ollama_url": gen.local_url("ollama", merged),
        "generation_chains": chains,
        "usage_today": limits.budget_left_today(),
    }


def _write_reference_png(path: str, size: int = 256) -> str:
    """A bundled reference image, written with the standard library: a green disc on a warm gradient."""
    import struct
    import zlib

    if os.path.isfile(path):
        return path
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            dx, dy = x - size // 2, y - size // 2
            if dx * dx + dy * dy < (size * 0.31) ** 2:
                row += bytes((92, 168, 58))
            else:
                row += bytes((240, max(0, 200 - y // 3), 120))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(png)
    return path


def _test_request(kind, link, out_dir):
    from clipping.providers.generation import GenRequest

    name = f"{kind}_{link.provider}_{link.model}".replace("/", "_").replace(":", "_")
    if kind == "image_edit":
        return GenRequest(kind=kind, prompt=_TEST_EDIT_PROMPT, negative="text, watermark, blurry", width=1080, height=1920,
                          seed=20260925, references=(_write_reference_png(os.path.join(out_dir, "reference.png")),),
                          out_dir=out_dir, extra={"name": name})
    if kind == "tts":
        lang = "fr" if link.model.lower().startswith("fr") or link.provider != "edge" else "en"
        voice = link.model if link.provider == "edge" else ""
        return GenRequest(kind=kind, text=_TEST_TEXT[lang], voice=voice, out_dir=out_dir, extra={"name": name})
    if kind == "vision":
        return GenRequest(kind=kind, prompt=_TEST_VISION_PROMPT,
                          images=(_write_reference_png(os.path.join(out_dir, "reference.png")),), out_dir=out_dir, extra={"name": name})
    return GenRequest(kind=kind, prompt=_TEST_IMAGE_PROMPT, negative="text, watermark, blurry", width=1080, height=1920,
                      seed=20260925, out_dir=out_dir, extra={"name": name})


def _status_from_reason(reason: str) -> str:
    text = (reason or "").lower()
    if "no api key" in text:
        return "no_key"
    if "no adapter yet" in text:
        return "no_adapter"
    if "unreachable" in text or "not installed" in text or "not pulled" in text or "not reachable" in text:
        return "unreachable"
    if "allow_paid is off" in text or text.startswith("refused"):
        return "refused"
    return "failed"


def _artifact_kind(kind, path) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".mp3", ".wav", ".ogg"):
        return "audio"
    if ext in (".png", ".jpg", ".jpeg", ".webp"):
        return "image"
    return "file"


def _test_generation_links(kind, links, tested, env):
    """The blocking half: run the free and local links, report the paid ones, run the named one."""
    from clipping.aistory.ledger import CostLedger
    from clipping.providers import adapters, budget as budget_mod, generation as gen, limits
    from ..auth import media_url

    adapters.load_all()
    merged = _merged_env(env)
    budget_obj = budget_mod.budget_from_env({name: merged.get(name, "") for name in budget_mod.ENV_NAMES})
    out_dir = os.path.join(files_route.OUTPUTS_DIR, CHAIN_TEST_DIRNAME)
    os.makedirs(out_dir, exist_ok=True)

    class _Limiter:
        def acquire(self, provider):
            return limits.acquire(provider)

    def check(est, link):
        budget_mod.check(est, link, budget=budget_obj, day_spent=budget_mod.day_spent())

    rows = []
    for link in links:
        summary = _link_summary(kind, link, merged, budget_obj)
        row = GenerationLinkResult(
            label=summary["label"], provider=link.provider, model=link.model, status="skipped",
            paid=summary["paid"], est_usd=summary["est_usd"], allowed=summary["allowed"],
            env_keys=summary["env_keys"], missing_keys=summary["missing_keys"], signup_url=summary["signup_url"],
        )
        if not summary["adapter"]:
            row.status, row.reason = "no_adapter", f"no adapter yet for {link.provider} {kind} (phase 6)"
            rows.append(row)
            continue
        if summary["missing_keys"]:
            row.status = "no_key"
            row.reason = f"no API key ({' and '.join(summary['missing_keys'])} not set)"
            rows.append(row)
            continue
        if summary["paid"] and tested is None:
            if summary["allowed"]:
                row.status = "skipped"
                row.note = f"paid, est ${summary['est_usd']:.3f}; allowed — press Test on this link to spend it once"
            else:
                row.status, row.reason = "refused", summary["reason"]
            rows.append(row)
            continue
        if summary["paid"] and not summary["allowed"]:
            row.status, row.reason = "refused", summary["reason"]
            rows.append(row)
            continue

        request = _test_request(kind, link, out_dir)
        log = []
        started = time.monotonic()
        try:
            result, answered = gen.run_generation_chain(
                kind, [link], request, env=merged, allow_paid=budget_obj.allow_paid, on_log=log.append,
                budget_check=check, limiter=_Limiter(), transport=_TRANSPORT,
            )
        except gen.NoRunnableLink as exc:
            reason = exc.failures[-1][1] if exc.failures else str(exc)
            row.status, row.reason = _status_from_reason(reason), reason
            row.latency_seconds = round(time.monotonic() - started, 2)
            rows.append(row)
            continue
        except Exception as exc:  # noqa: BLE001 - a bug in an adapter is reported, never a 500
            row.status, row.reason = "failed", f"{type(exc).__name__}: {exc}"
            rows.append(row)
            continue
        row.status = "ok"
        row.latency_seconds = round(time.monotonic() - started, 2)
        row.model = answered.model
        if result.paths:
            filename = os.path.basename(result.paths[0])
            row.artifact_url = media_url(CHAIN_TEST_DIRNAME, filename)
            row.artifact_kind = _artifact_kind(kind, filename)
        text = (result.meta or {}).get("text")
        if text:
            row.note = str(text)[:300]
        if result.paid and result.est_cost > 0:
            CostLedger(CHAIN_TEST_LEDGER).append(
                step="chain_test", provider=answered.provider, model=_api_model_id(kind, answered),
                unit="image" if kind.startswith("image") else "second" if kind == "video" else "char", qty=1,
                est_usd=result.est_cost, paid=True,
            )
            budget_mod.record(result.est_cost)
            row.est_usd = result.est_cost
        rows.append(row)

    statuses = [r.status for r in rows]
    if rows and all(s == "no_adapter" for s in statuses):
        verdict, message = "no_adapter", f"No {kind} adapter exists yet: they arrive in phase 6."
    elif any(r.status == "ok" for r in rows):
        verdict = "ready"
        answered = [r.label for r in rows if r.status == "ok"]
        message = f"{', '.join(answered)} answered."
    elif any(r.status == "skipped" and r.paid and r.allowed for r in rows):
        verdict, message = "paid_only", "No free or local link answered; a paid link is keyed and allowed — test it from its row."
    else:
        verdict, message = "blocked", "No link of this chain can run right now; see each row."
    return rows, verdict, message


@router.post("/api/settings/test-generation-chain")
async def run_generation_chain_test(req: GenerationChainTestRequest) -> GenerationChainTestResponse:
    """Run a generation chain's free and local links; report the paid ones (DEC-103).

    A paid link is called only when ``link`` names it, at most once, after the
    budget verdict, and the call is booked. Shares the LLM chain test's lock
    and ceiling: one chain test at a time per process.
    """
    from clipping.providers import generation as gen
    from clipping.providers.registry import ChainError

    if req.kind not in gen.KINDS:
        raise HTTPException(status_code=400, detail=f"Unknown chain kind {req.kind!r}. Known: {', '.join(gen.KINDS)}.")
    env = worker.get_settings_env()
    merged = _merged_env(env)
    try:
        links = (gen.parse_generation_chain(req.kind, req.chain) if req.chain.strip()
                 else gen.chain_from_env(req.kind, merged))
        tested = None
        if req.link.strip():
            tested = gen.parse_generation_chain(req.kind, req.link)[0]
            links = [tested]
    except ChainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if _CHAIN_TEST_LOCK.locked():
        raise HTTPException(status_code=409, detail="A chain test is already running.")
    started = time.monotonic()
    async with _CHAIN_TEST_LOCK:
        try:
            rows, verdict, message = await asyncio.wait_for(
                asyncio.to_thread(_test_generation_links, req.kind, links, tested, env),
                timeout=_CHAIN_TEST_CEILING_SECONDS,
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail=f"The {req.kind} chain test gave up after {time.monotonic() - started:.0f}s.",
            )
    return GenerationChainTestResponse(
        kind=req.kind, chain=",".join(gen.describe(link) for link in links), verdict=verdict, results=rows,
        elapsed_seconds=round(time.monotonic() - started, 2), message=message,
        tested_link=gen.describe(tested) if tested else None,
    )
