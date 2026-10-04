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
from clipping import stock
from .. import settings_store
from .. import store as job_store
from .. import worker
from . import files as files_route
from .budget import today_block as _today_block
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
    today = _today_block(env)
    return {
        "allow_paid": resolved.allow_paid,
        "per_episode_cap_usd": resolved.per_episode_cap_usd,
        "daily_cap_usd": resolved.daily_cap_usd,
        "per_story_cap_usd": resolved.per_story_cap_usd,
        "budget_profile": str(merged.get("BUDGET_PROFILE") or "").strip().lower(),
        "effective_budget_profile": resolved.profile,
        "spend_today_usd": budget_mod.day_spent(),
        # Response only, never settings (plan 23 A3).
        "spend_day": today["day"],
        "spend_zone": today["zone"],
        # Plan 23 A7: the saved zone as typed, and why it is not in force.
        "budget_timezone": str(env.get(budget_mod.TIMEZONE_ENV, os.environ.get(budget_mod.TIMEZONE_ENV, ""))
                               or "").strip(),
        "spend_zone_error": today["zone_error"],
        "day_extra_usd": today["extra_usd"],
        "daily_cap_below_spend": today["cap_below_spend"],
        "day_contributors": today["stories"],
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
    story_chain = env.get("STORY_LLM_CHAIN", os.environ.get("STORY_LLM_CHAIN", ""))
    story_premium_chain = env.get("STORY_LLM_PREMIUM_CHAIN", os.environ.get("STORY_LLM_PREMIUM_CHAIN", ""))

    merged = _merged_env(env)
    return SettingsResponse(
        google_api_key_set=bool(google_key),
        pexels_api_key_set=bool(pexels_key),
        pixabay_api_key_set=bool(merged.get("PIXABAY_API_KEY")),
        broll_sources=merged.get("BROLL_SOURCES", ""),
        broll_local_dir=merged.get("BROLL_LOCAL_DIR", ""),
        broll_available=await asyncio.to_thread(stock.any_source_available, merged),
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
        story_llm_chain=story_chain,
        story_llm_premium_chain=story_premium_chain,
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


@router.get("/api/broll/status")
async def broll_status() -> dict:
    """What the B-roll sources can answer right now: the order, which sources are
    usable and how many clips the local folder holds (plan 23 stage B2)."""
    merged = _merged_env(worker.get_settings_env())

    def probe() -> dict:
        return {
            "sources": list(stock.base.parse_sources(merged.get("BROLL_SOURCES"))),
            "available": stock.available_sources(merged),
            "local_dir": merged.get("BROLL_LOCAL_DIR", ""),
            "local_clips": stock.local_clip_count(merged),
            "root": stock.local_root(),
        }

    return await asyncio.to_thread(probe)


@router.put("/api/settings")
async def update_settings(req: SettingsRequest) -> SettingsResponse:
    """Update settings (API keys and defaults)."""
    env_updates: dict[str, str] = {}

    if req.google_api_key is not None:
        env_updates["GOOGLE_API_KEY"] = req.google_api_key
    if req.pexels_api_key is not None:
        env_updates["PEXELS_API_KEY"] = req.pexels_api_key
    if req.pixabay_api_key is not None:
        env_updates["PIXABAY_API_KEY"] = req.pixabay_api_key.strip()
    if req.broll_sources is not None:
        # "" restores the default order (DEC-043); otherwise every name must be a source.
        names = [part.strip().lower() for part in req.broll_sources.split(",") if part.strip()]
        unknown = [name for name in names if name not in stock.DEFAULT_SOURCES]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"BROLL_SOURCES: unknown source {', '.join(unknown)}; use {', '.join(stock.DEFAULT_SOURCES)}",
            )
        env_updates["BROLL_SOURCES"] = ",".join(dict.fromkeys(names))
    if req.broll_local_dir is not None:
        # "" clears it; anything else must resolve to the B-roll root or below it, so
        # the Settings page cannot point the indexer at an arbitrary folder.
        try:
            env_updates["BROLL_LOCAL_DIR"] = stock.resolve_local_dir(req.broll_local_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
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
    if req.story_llm_chain is not None:
        # "" clears the override (DEC-043); a non-empty value is validated
        # the same way /api/settings/test-chain validates LLM_CHAIN.
        spec = req.story_llm_chain.strip()
        if spec:
            from clipping.providers import registry as registry_mod

            try:
                registry_mod.parse_chain(spec)
            except registry_mod.ChainError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
        env_updates["STORY_LLM_CHAIN"] = spec
    if req.story_llm_premium_chain is not None:
        # Same rule as story_llm_chain above (DEC-043 clearing; validated as
        # a chain before it is stored).
        spec = req.story_llm_premium_chain.strip()
        if spec:
            from clipping.providers import registry as registry_mod

            try:
                registry_mod.parse_chain(spec)
            except registry_mod.ChainError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
        env_updates["STORY_LLM_PREMIUM_CHAIN"] = spec
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
    if req.budget_timezone is not None:
        # The budget day's zone (plan 23 A7): "" clears it (UTC again, DEC-043);
        # anything else must be a zone ZoneInfo knows before it is stored.
        from clipping.providers.budget import check_zone_name

        try:
            env_updates["BUDGET_TIMEZONE"] = check_zone_name(req.budget_timezone)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
    # Generation providers (spec 8.6): keys clear on "" like every key; the
    # local URLs are stored as typed and normalised when read.
    for name, value in (("FAL_KEY", req.fal_key), ("OPENAI_API_KEY", req.openai_api_key),
                        ("CLOUDFLARE_API_TOKEN", req.cloudflare_api_token),
                        ("CLOUDFLARE_ACCOUNT_ID", req.cloudflare_account_id),
                        ("POLLINATIONS_API_KEY", req.pollinations_api_key),
                        ("GEMINI_PAID_API_KEY", req.gemini_paid_api_key),
                        ("ANTHROPIC_API_KEY", req.anthropic_api_key),
                        ("ELEVENLABS_API_KEY", req.elevenlabs_api_key),
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
    elif probe.reason == "ok" and probe.kind == "listed":
        status = "listed"
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
    if status == "listed":
        notes.append(ANTHROPIC_LISTED_TEXT)
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
    from clipping.providers import gating

    return gating.merged_env(env)


def _api_model_id(kind, link) -> str:
    """The provider's own model id behind a chain link (for the ledger)."""
    from clipping.providers import gating

    return gating.api_model_id(kind, link)


def _link_summary(kind, link, merged, budget_obj) -> dict:
    """The gates' verdict on *link* for the chain test's request (``gating.link_summary``)."""
    from clipping.providers import gating

    return gating.link_summary(kind, link, merged, budget_obj, _summary_request(kind, link))


# The clip a video link is priced for in Settings: never bought (RC-V8), only
# estimated, at the link's own length for a shot this long.
VIDEO_TEST_CLIP_SECONDS = 5.0


def _video_test_seconds(link):
    """The length *link* would sell for a :data:`VIDEO_TEST_CLIP_SECONDS` shot, or
    ``None`` for a link with no table of lengths (local, or refused)."""
    from clipping.aistory import video_plan
    from clipping.providers.registry import describe
    from clipping.providers.video import CLIP_LENGTHS

    label = describe(link)
    if label not in CLIP_LENGTHS:
        return None
    return video_plan.requested_seconds(label, VIDEO_TEST_CLIP_SECONDS)


def _summary_request(kind, link=None):
    """A request shaped like the chain test's, for estimates only (no files are read)."""
    from clipping.providers.generation import GenRequest

    if kind == "tts":
        return GenRequest(kind=kind, text=_TEST_TEXT["fr"])
    if kind == "vision":
        return GenRequest(kind=kind, prompt=_TEST_VISION_PROMPT, images=("reference.png",))
    if kind == "video":
        seconds = _video_test_seconds(link) if link is not None else None
        return GenRequest(kind=kind, prompt=_TEST_IMAGE_PROMPT, width=1080, height=1920,
                          duration_s=float(seconds) if seconds else None)
    return GenRequest(kind=kind, prompt=_TEST_IMAGE_PROMPT, width=1080, height=1920)


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
        "gemini_paid_api_key_set": bool(merged.get("GEMINI_PAID_API_KEY")),
        "anthropic_api_key_set": bool(merged.get("ANTHROPIC_API_KEY")),
        "elevenlabs_api_key_set": bool(merged.get("ELEVENLABS_API_KEY")),
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


def _check_local_video(merged):
    """``(status, text)`` for ``local/comfyui`` video without generating:
    ComfyUI's ``/system_stats`` picks the profile's template and
    ``/object_info`` says whether its nodes and model files are there."""
    from clipping.aistory import hardware
    from clipping.providers import generation as gen, local_comfyui

    client = local_comfyui.ComfyUIClient(gen.local_url("comfyui", merged), transport=_TRANSPORT)
    try:
        stats = client.system_stats()
    except Exception as exc:  # noqa: BLE001 - any failure to answer is a server that is not there
        return "unreachable", f"ComfyUI unreachable at {client.base_url} ({type(exc).__name__}: {exc})"
    profile, vram, gpu = hardware.profile_from_system_stats(stats, in_container=gen.in_container())
    name = hardware.video_workflow_for(profile)
    card = f"{gpu}, {vram:g} GB" if vram else "no GPU reported"
    if name is None:
        return "unreachable", (f"ComfyUI at {client.base_url} ({card}) is profile {profile}: no local video "
                               "workflow for it; the hosted links make the clips")
    try:
        problems = local_comfyui.validate_template(local_comfyui.load_template(name), client.object_info())
    except Exception as exc:  # noqa: BLE001 - reported, never a 500
        return "failed", f"{type(exc).__name__}: {exc}"
    if problems:
        return "unreachable", local_comfyui.install_message(name, client.base_url, problems)
    return "ok", (f"{name} can run on ComfyUI at {client.base_url} ({card}, profile {profile}); checked with "
                  "/object_info only: video links are never test-generated")


def _test_video_links(links, tested, env):
    """Video is never test-generated (DEC-103 amended for video, RC-V8): a
    local link is checked with ``/system_stats`` and ``/object_info`` only; a
    hosted link reports its key and the estimate of a default clip and is
    never called, even when it is the named link and allowed."""
    from clipping.providers import adapters, gating

    adapters.load_all()
    merged = _merged_env(env)
    budget_obj = gating.budget_of(merged)
    rows = []
    for link in links:
        summary = _link_summary("video", link, merged, budget_obj)
        row = GenerationLinkResult(
            label=summary["label"], provider=link.provider, model=link.model, status="skipped",
            paid=summary["paid"], est_usd=summary["est_usd"], allowed=summary["allowed"],
            env_keys=summary["env_keys"], missing_keys=summary["missing_keys"], signup_url=summary["signup_url"],
        )
        if not summary["adapter"]:
            row.status, row.reason = "no_adapter", f"no adapter yet for {link.provider} video"
        elif summary["missing_keys"]:
            row.status = "no_key"
            row.reason = f"no API key ({' and '.join(summary['missing_keys'])} not set)"
        elif link.provider == "local":
            status, text = _check_local_video(merged)
            row.status = status
            if status == "ok":
                row.note = text
            else:
                row.reason = text
        else:
            seconds = _video_test_seconds(link)
            row.note = (f"video links are never test-generated; the estimate is ${summary['est_usd']:.3f} "
                        f"for {seconds} s" if seconds else
                        "video links are never test-generated; this link has no clip length to price")
            if summary["paid"] and not summary["allowed"]:
                row.status, row.reason = "refused", summary["reason"]
        rows.append(row)

    statuses = [r.status for r in rows]
    if rows and all(s == "no_adapter" for s in statuses):
        verdict, message = "no_adapter", "No video adapter exists for this chain."
    elif any(r.status == "ok" for r in rows):
        verdict = "ready"
        message = f"{', '.join(r.label for r in rows if r.status == 'ok')} can run; video links are never test-generated."
    elif any(r.status == "skipped" and r.paid and r.allowed for r in rows):
        verdict = "paid_only"
        message = ("No local link can run; a paid video link is keyed and allowed. Video links are never "
                   "test-generated: see each row's estimate.")
        if tested is not None:
            message = f"{rows[0].label}: {rows[0].note}."
    else:
        verdict, message = "blocked", "No link of this chain can run right now; see each row. Video links are never test-generated."
    return rows, verdict, message


def _test_generation_links(kind, links, tested, env):
    """The blocking half: run the free and local links, report the paid ones, run the named one."""
    from clipping.aistory.ledger import CostLedger
    from clipping.providers import adapters, budget as budget_mod, gating, generation as gen
    from ..auth import media_url

    if kind == "video":
        return _test_video_links(links, tested, env)
    adapters.load_all()
    merged = _merged_env(env)
    budget_obj = gating.budget_of(merged)
    out_dir = os.path.join(files_route.OUTPUTS_DIR, CHAIN_TEST_DIRNAME)
    os.makedirs(out_dir, exist_ok=True)

    check = gating.budget_check(budget_obj)

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
                budget_check=check, limiter=gating.FreeTierLimiter(), transport=_TRANSPORT,
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


def _check_video_keys(links, merged) -> list:
    """The blocking half of :func:`check_video_keys`: one row per link."""
    from clipping.providers import generation as gen, video as video_providers

    rows = []
    for link in links:
        row = {"label": gen.describe(link), "provider": link.provider, "model": link.model,
               "status": "skipped", "text": "", "endpoint": None, "price": None, "prompt_limit": None}
        missing = gen.missing_keys(link, merged)
        if link.provider == "local":
            row["text"] = "local: no key to check (the chain test asks ComfyUI's /object_info)."
        elif missing:
            row.update(status="no_key", text=f"{row['label']}: no API key ({' and '.join(missing)} not set).")
        else:
            try:
                row.update(video_providers.check_key(link, gen.credentials_for(link, merged), transport=_TRANSPORT))
            except ValueError as exc:
                row["text"] = str(exc)
        rows.append(row)
    return rows


def _store_prompt_limits(rows) -> str:
    """Keep the prompt limits fal's schemas published (or said they do not)
    next to the Settings file, where the runner's check reads them
    (``prompt_limits.record_live``). A schema that could not be read keeps
    what was there. "" when stored or nothing to store, else why not."""
    from clipping.providers import prompt_limits

    reads = {row["label"]: row["prompt_limit"] for row in rows
             if (row.get("prompt_limit") or {}).get("status") in (prompt_limits.PUBLISHED,
                                                                   prompt_limits.NOT_PUBLISHED)}
    if not reads:
        return ""
    try:
        prompt_limits.record_live(reads)
    except OSError as exc:
        return f" The prompt limits read could not be stored ({type(exc).__name__}: {exc})."
    return ""


@router.post("/api/settings/check-video-keys")
async def check_video_keys() -> dict:
    """Ask every hosted link of the video chain whether its key is accepted
    and its model is there: one free metadata request per keyed link (fal's
    pricing, Gemini's ``models.get``), nothing generated or billed
    (``video.check_key``); for a fal link, its public schema's prompt limit
    too, stored for the runner's check (``prompt_limits``). The chain test
    itself stays call-free for hosted video (RC-V8). ``{"results": [{label,
    provider, model, status, text, endpoint, price, prompt_limit}],
    "verdict": "ready" | "blocked", "message"}``; never a key's value.
    Shares the chain tests' lock: one at a time."""
    from clipping.providers import generation as gen
    from clipping.providers.registry import ChainError

    merged = _merged_env(worker.get_settings_env())
    try:
        links = gen.chain_from_env(gen.VIDEO, merged)
    except ChainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if _CHAIN_TEST_LOCK.locked():
        raise HTTPException(status_code=409, detail="A chain test is already running.")
    async with _CHAIN_TEST_LOCK:
        try:
            rows = await asyncio.wait_for(asyncio.to_thread(_check_video_keys, links, merged),
                                          timeout=_CHAIN_TEST_CEILING_SECONDS)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="The video key check gave up.")
        not_stored = await asyncio.to_thread(_store_prompt_limits, rows)
    good = [row["label"] for row in rows if row["status"] == "ok"]
    if good:
        verdict, message = "ready", f"{', '.join(good)} answered: key accepted, model live. Nothing was generated."
    else:
        verdict = "blocked"
        message = "No hosted video link answered with an accepted key; see each row. Nothing was generated."
    return {"results": rows, "verdict": verdict, "message": message + not_stored}


# What a free Anthropic check proves, and what it does not (plan 23 stage D1).
ANTHROPIC_LISTED_TEXT = "key valid, model available — not exercised: every request is billed"


def _check_anthropic_links(links, key) -> list:
    """The blocking half of :func:`check_anthropic_key`: one free
    ``models.retrieve`` per link (``AnthropicChat.check_model``), one row
    each. Never a completion."""
    from clipping.providers import errors as provider_errors, llm, registry

    rows = []
    for link in links:
        label = registry.describe(link)
        row = {"label": label, "provider": link.provider, "model": link.model, "status": "skipped", "text": ""}
        if not key:
            env_key = registry.PROVIDERS[link.provider].env_key
            row.update(status="no_key", text=f"{label}: no API key ({env_key} is not set).")
            rows.append(row)
            continue
        try:
            llm.build_client(link, api_key=key, timeout=registry.probe_timeout(link)).check_model()
        except Exception as exc:  # noqa: BLE001 - every failure is reported on its row
            status = provider_errors.status_code(exc)
            name = type(exc).__name__
            if status in (401, 403) or name in ("AuthenticationError", "PermissionDeniedError"):
                kind = "bad_key"
            elif status == 404 or name == "NotFoundError":
                kind = "no_model"
            elif name in ("APIConnectionError", "APITimeoutError"):
                kind = "unreachable"
            else:
                kind = "failed"
            row.update(status=kind, text=f"{label}: {name}: {' '.join(str(exc).split())}")
        else:
            row.update(status="ok", text=f"{label}: {ANTHROPIC_LISTED_TEXT}.")
        rows.append(row)
    return rows


@router.post("/api/settings/check-anthropic-key")
async def check_anthropic_key() -> dict:
    """Ask Anthropic whether ``ANTHROPIC_API_KEY`` is accepted and each
    ``anthropic/`` link of the story premium writing chain is available: one
    free ``models.retrieve`` per link, never a completion -- every request on
    this provider is billed, so the check proves the key and the model and
    nothing else. ``{"results": [{label, provider, model, status, text}],
    "verdict": "ready" | "blocked", "message"}``; never the key's value.
    Shares the chain tests' lock: one at a time."""
    from clipping.aistory.steps import llm_call
    from clipping.providers import registry

    env = worker.get_settings_env()
    try:
        chain = llm_call.resolve_premium_chain(env)
    except registry.ChainError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    links = [link for link in chain if registry.PROVIDERS[link.provider].api == "anthropic"]
    if not links:
        return {"results": [], "verdict": "blocked",
                "message": "The story premium writing chain names no anthropic/ link, so nothing was checked."}
    key = llm_call.resolve_keys(env).get("anthropic", "")
    if _CHAIN_TEST_LOCK.locked():
        raise HTTPException(status_code=409, detail="A chain test is already running.")
    async with _CHAIN_TEST_LOCK:
        try:
            rows = await asyncio.wait_for(asyncio.to_thread(_check_anthropic_links, links, key),
                                          timeout=_CHAIN_TEST_CEILING_SECONDS)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="The Anthropic key check gave up.")
    good = [row["label"] for row in rows if row["status"] == "ok"]
    if good and len(good) == len(rows):
        verdict, message = "ready", f"{', '.join(good)}: {ANTHROPIC_LISTED_TEXT}. Nothing was billed."
    else:
        verdict = "blocked"
        message = "At least one Anthropic link did not pass the free check; see each row. Nothing was billed."
    return {"results": rows, "verdict": verdict, "message": message}


@router.post("/api/settings/test-generation-chain")
async def run_generation_chain_test(req: GenerationChainTestRequest) -> GenerationChainTestResponse:
    """Run a generation chain's free and local links; report the paid ones (DEC-103).

    A paid link is called only when ``link`` names it, at most once, after the
    budget verdict, and the call is booked. A video chain generates nothing,
    named link or not (RC-V8): local is checked with ``/object_info``, hosted
    links report their key and estimate. Shares the LLM chain test's lock and
    ceiling: one chain test at a time per process.
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
