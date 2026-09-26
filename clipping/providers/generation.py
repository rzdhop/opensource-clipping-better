"""Generation provider chains: image, image edit, video, TTS and vision.

Same grammar and hop rules as ``LLM_CHAIN`` (DEC-096): an ordered list of
``<provider>/<model>`` links tried in order, a link that cannot run is skipped
with a printed line, every hop is printed, and a provider absent from the chain
is never contacted (DEC-003/023). Three gates are new for generation:

* a **paid** link runs only when ``allow_paid`` is on and the budget check
  accepts its estimate (DEC-097; the check itself lives in ``budget.py``);
* a **local** link (``local/comfyui``, ``local/ollama-vision``, ``local/piper``,
  ...) runs only when its adapter reports the server or package reachable;
* a **free-tier** link asks the limiter first (``limits.py``), so a spent daily
  allowance moves the chain on instead of earning a 429.

Adapters register themselves per ``(kind, provider)``; a link whose adapter
does not exist yet is reported as such and never called -- ``VIDEO_CHAIN``
parses and tests from phase 0 and gets its adapters in phase 6 (DEC-102).

This module imports nothing outside the standard library (DEC-012).
"""

from __future__ import annotations

import os
import time
from collections import namedtuple
from dataclasses import dataclass, field

from . import errors
from .registry import ChainError, Link, describe, parse_chain

# ------------------------------------------------------------------ kinds

IMAGE = "image"
IMAGE_EDIT = "image_edit"
VIDEO = "video"
TTS = "tts"
VISION = "vision"
KINDS = (IMAGE, IMAGE_EDIT, VIDEO, TTS, VISION)

ENV_NAMES = {
    IMAGE: "IMAGE_CHAIN",
    IMAGE_EDIT: "IMAGE_EDIT_CHAIN",
    VIDEO: "VIDEO_CHAIN",
    TTS: "TTS_CHAIN",
    VISION: "VISION_CHAIN",
}

# Candidate only: the OpenRouter free vision model is measured with
# tools/bench_llm.py before it is trusted (A-032).
OPENROUTER_VISION_DEFAULT_MODEL = "qwen/qwen3.8-27b:free"

# Spec section 8.1. The spec writes a paid link with a trailing ``*``; the
# parser tolerates and strips that marker, ``is_paid`` is the truth.
DEFAULT_CHAINS = {
    IMAGE: (
        "cloudflare/flux-1-schnell,pollinations/flux,local/comfyui,"
        "fal/flux-schnell,openai/gpt-image-2-low"
    ),
    IMAGE_EDIT: (
        "local/comfyui,gemini/nano-banana-2-lite,fal/seedream-4-edit,"
        "fal/flux-kontext-pro,gemini/nano-banana-2"
    ),
    VIDEO: (
        "local/comfyui,fal/seedance-1-pro-fast,fal/ltx-2-fast,"
        "fal/kling-2.5-turbo-std,gemini/veo-3.1-lite"
    ),
    TTS: "edge/fr-FR-HenriNeural,gemini/flash-lite-tts,local/piper,local/kokoro,local/chatterbox",
    VISION: f"gemini/flash-lite,openrouter/{OPENROUTER_VISION_DEFAULT_MODEL},local/ollama-vision,gemini/flash",
}

# -------------------------------------------------------------- providers

# ``env_keys`` are the variables a link needs before it is contacted (all of
# them: Cloudflare needs a token AND an account id); ``optional_keys`` raise a
# limit when present but are not required (Pollinations works keyless at its
# legacy rate). ``free_tier`` says whether the provider has ANY free
# allowance; whether a given LINK is paid is ``is_paid`` (Gemini's text and
# TTS are free, its image models are not). ``rpm``/``rpd`` are the published
# free-tier numbers (spec 8.4, appendix D) that ``limits.py`` enforces.
GenProvider = namedtuple(
    "GenProvider",
    "name env_keys free_tier rpm rpd probe_timeout signup_url base_url notes optional_keys",
    defaults=((),),
)

GEN_PROVIDERS = {
    "cloudflare": GenProvider(
        name="cloudflare",
        env_keys=("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"),
        free_tier=True, rpm=10, rpd=170, probe_timeout=30.0,
        signup_url="https://dash.cloudflare.com/profile/api-tokens",
        base_url="https://api.cloudflare.com/client/v4",
        notes="Workers AI: 10,000 neurons/day free, about 170 flux-1-schnell images.",
    ),
    "pollinations": GenProvider(
        name="pollinations",
        env_keys=(), free_tier=True, rpm=6, rpd=None, probe_timeout=60.0,
        signup_url="https://auth.pollinations.ai/",
        base_url="https://image.pollinations.ai",
        notes="Keyless at the legacy rate (about one image per IP per hour); a key adds pollen credits.",
        optional_keys=("POLLINATIONS_API_KEY",),
    ),
    "gemini": GenProvider(
        name="gemini",
        env_keys=("GOOGLE_API_KEY",),
        free_tier=True, rpm=15, rpd=250, probe_timeout=60.0,
        signup_url="https://aistudio.google.com/apikey",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        notes="Text, vision and TTS on the free tier; the image and video models are billed (checked 2026-09-25).",
    ),
    "fal": GenProvider(
        name="fal",
        env_keys=("FAL_KEY",),
        free_tier=False, rpm=30, rpd=None, probe_timeout=60.0,
        signup_url="https://fal.ai/dashboard/keys",
        base_url="https://queue.fal.run",
        notes="Billed per image or per second of video; no recurring free allowance.",
    ),
    "openai": GenProvider(
        name="openai",
        env_keys=("OPENAI_API_KEY",),
        free_tier=False, rpm=30, rpd=None, probe_timeout=60.0,
        signup_url="https://platform.openai.com/api-keys",
        base_url="https://api.openai.com/v1",
        notes="A real OpenAI account, distinct from the OpenAI-compatible LLM providers (DEC-042).",
    ),
    "openrouter": GenProvider(
        name="openrouter",
        env_keys=("OPENROUTER_API_KEY",),
        free_tier=True, rpm=20, rpd=50, probe_timeout=60.0,
        signup_url="https://openrouter.ai/keys",
        base_url="https://openrouter.ai/api/v1",
        notes=":free models: 50 requests/day, 1000 once $10 of credit was ever bought.",
    ),
    "edge": GenProvider(
        name="edge",
        env_keys=(), free_tier=True, rpm=30, rpd=None, probe_timeout=30.0,
        signup_url="", base_url="",
        notes="Edge TTS through the edge-tts package: free, unofficial, one voice per request.",
    ),
    "local": GenProvider(
        name="local",
        env_keys=(), free_tier=True, rpm=None, rpd=None, probe_timeout=10.0,
        signup_url="", base_url="",
        notes="ComfyUI, Ollama or a local TTS package on this machine or the Docker host.",
    ),
    # Documented extension points (spec 8.1): in the table with a price, not in a default chain.
    "gcloud": GenProvider(
        name="gcloud",
        env_keys=("GOOGLE_CLOUD_TTS_API_KEY",),
        free_tier=False, rpm=30, rpd=None, probe_timeout=60.0,
        signup_url="https://console.cloud.google.com/apis/credentials",
        base_url="https://texttospeech.googleapis.com/v1",
        notes="Extension point: Google Cloud Text-to-Speech Neural2 voices.",
    ),
    "elevenlabs": GenProvider(
        name="elevenlabs",
        env_keys=("ELEVENLABS_API_KEY",),
        free_tier=False, rpm=30, rpd=None, probe_timeout=60.0,
        signup_url="https://elevenlabs.io/app/settings/api-keys",
        base_url="https://api.elevenlabs.io/v1",
        notes="Extension point: ElevenLabs Flash voices.",
    ),
}

GEN_PROVIDER_NAMES = tuple(GEN_PROVIDERS)

# Which providers may appear in which chain. Refusing ``edge/x`` in
# IMAGE_CHAIN at parse time is cheaper than discovering it at run time.
KIND_PROVIDERS = {
    IMAGE: ("cloudflare", "pollinations", "local", "fal", "openai", "gemini"),
    IMAGE_EDIT: ("local", "gemini", "fal", "openai"),
    VIDEO: ("local", "fal", "gemini"),
    TTS: ("edge", "gemini", "local", "gcloud", "openai", "elevenlabs"),
    VISION: ("gemini", "openrouter", "local"),
}

# Links that cost money on a provider that is otherwise free (spec 8.1 ``*``).
# A provider with ``free_tier=False`` is paid on every link.
PAID_LINKS = frozenset({
    "gemini/nano-banana-2-lite",
    "gemini/nano-banana-2",
    "gemini/veo-3.1-lite",
    "gemini/flash",
})

# DEC-089 for generation: when a model answers "not available", the next model
# of the SAME provider on the SAME key is tried, and only then. Kept to
# like-for-like swaps: same capability, so the request still means the same.
FALLBACK_LINKS = {
    "gemini/nano-banana-2-lite": ("gemini/nano-banana-2",),
    "gemini/flash-lite": ("gemini/flash-lite-latest",),
    f"openrouter/{OPENROUTER_VISION_DEFAULT_MODEL}": ("openrouter/google/gemma-4-31b:free",),
}


def kinds_of(provider: str) -> tuple:
    return tuple(kind for kind in KINDS if provider in KIND_PROVIDERS[kind])


def parse_generation_chain(kind: str, chain) -> list:
    """``"a/b,c/d*"`` -> ``[Link, ...]`` for *kind*, with the paid marker stripped."""
    if kind not in KINDS:
        raise ChainError(f"Unknown generation kind {kind!r}. Known: {', '.join(KINDS)}.")
    if isinstance(chain, str):
        parts = [p.strip().rstrip("*").strip() for p in chain.split(",") if p.strip()]
    else:
        parts = list(chain or [])
    links = parse_chain(parts, providers=GEN_PROVIDERS)
    for link in links:
        if link.provider not in KIND_PROVIDERS[kind]:
            offers = ", ".join(kinds_of(link.provider)) or "nothing"
            raise ChainError(
                f"{describe(link)} cannot be a link of {ENV_NAMES[kind]}: "
                f"{link.provider} offers {offers}, not {kind}."
            )
    return links


def chain_from_env(kind: str, env=None) -> list:
    """The chain named by ``IMAGE_CHAIN`` (etc.), or the shipped default."""
    env = os.environ if env is None else env
    spec = (env.get(ENV_NAMES[kind]) or "").strip()
    return parse_generation_chain(kind, spec or DEFAULT_CHAINS[kind])


def provider_for(link) -> GenProvider:
    return GEN_PROVIDERS[link.provider]


def is_paid(link) -> bool:
    """Whether calling *link* costs money. Decided per link, not per provider."""
    provider = GEN_PROVIDERS[link.provider]
    if not provider.free_tier:
        return True
    return describe(link) in PAID_LINKS


def missing_keys(link, env) -> list:
    return [name for name in provider_for(link).env_keys if not (env.get(name) or "").strip()]


def credentials_for(link, env) -> dict:
    provider = provider_for(link)
    names = tuple(provider.env_keys) + tuple(provider.optional_keys)
    return {name: env[name].strip() for name in names if (env.get(name) or "").strip()}


# ------------------------------------------------------------ local URLs

LOCAL_SERVICES = {
    "comfyui": ("LOCAL_COMFYUI_URL", 8188),
    "ollama": ("LOCAL_OLLAMA_URL", 11434),
}


def in_container() -> bool:
    """Whether this process runs inside Docker, where ``127.0.0.1`` is not the host."""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8", errors="replace") as fh:
            return "docker" in fh.read() or "containerd" in fh.read()
    except OSError:
        return False


def local_url(service: str, env=None, *, container=None) -> str:
    """Base URL of a local service: the env var when set, else a Docker-aware default.

    In a container the host's ComfyUI/Ollama are reached over
    ``host.docker.internal`` (compose adds the ``host-gateway`` extra host);
    on a host they are on loopback (spec 8.1).
    """
    env_name, port = LOCAL_SERVICES[service]
    env = os.environ if env is None else env
    value = (env.get(env_name) or "").strip()
    if value:
        return value.rstrip("/")
    inside = in_container() if container is None else container
    host = "host.docker.internal" if inside else "127.0.0.1"
    return f"http://{host}:{port}"


# ------------------------------------------------------- request / result

@dataclass
class GenRequest:
    """One generation request, whatever the kind. Unused fields stay at their defaults."""

    kind: str
    prompt: str = ""
    negative: str = ""
    width: int = 1080
    height: int = 1920
    seed: int | None = None
    references: tuple = ()        # reference image paths (image_edit, video)
    text: str = ""                # what to speak (tts)
    voice: str = ""               # voice id, when the link's model is not the voice
    images: tuple = ()            # frames to describe (vision)
    duration_s: float | None = None
    out_dir: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class GenResult:
    provider: str
    model: str
    paths: tuple = ()
    seed: int | None = None
    est_cost: float = 0.0
    paid: bool = False
    meta: dict = field(default_factory=dict)


class NoRunnableLink(errors.ProviderError):
    """No link of the chain could run. Carries every (label, reason) pair."""


# --------------------------------------------------------------- adapters

# ``(kind, provider) -> adapter``. An adapter is any object with
#   estimate(link, request) -> estimate or None
#   probe(link, *, credentials, **kw) -> (ok: bool, note: str)
#   generate(link, request, *, credentials, on_log, transport=None, **kw) -> GenResult
# ``kw`` carries ``transport`` when one is injected (tests) and, for a local
# link, ``env`` (where LOCAL_COMFYUI_URL / LOCAL_OLLAMA_URL come from).
# Exceptions raised by ``generate`` carry ``.status_code`` when they come from
# HTTP, so ``errors.classify`` and ``errors.is_model_unavailable`` apply.
_ADAPTERS = {}


def register_adapter(kind: str, provider: str, adapter) -> None:
    _ADAPTERS[(kind, provider)] = adapter


def adapter_for(kind: str, provider: str, adapters=None):
    table = _ADAPTERS if adapters is None else adapters
    return table.get((kind, provider))


# ----------------------------------------------------------------- runner

MAX_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 3.0


def run_generation_chain(
    kind,
    chain,
    request,
    *,
    env=None,
    allow_paid=False,
    route="auto",
    on_log=print,
    budget_check=None,
    limiter=None,
    adapters=None,
    transport=None,
    sleep_fn=time.sleep,
    time_fn=time.monotonic,
    cancel=None,
):
    """Try each link of *chain* in order; return ``(GenResult, link)`` from the first that works.

    *env* maps variable names to values (``FAL_KEY``, ``GOOGLE_API_KEY``, ...);
    it defaults to the process environment. *budget_check(estimate, link)*
    raises to refuse a paid call; its message is printed and the chain moves
    on. *limiter.acquire(provider)* returns a reason to skip a free link, or
    ``None``. *route* is ``auto`` | ``local`` | ``api`` (spec 8.2).
    """
    env = os.environ if env is None else env
    if cancel is not None:
        sleep_fn = cancel.sleeper(sleep_fn)
    failures = []

    def skip(label, reason):
        on_log(f"   ⏭ Skipping {label}: {reason}.")
        failures.append((label, reason))

    for link in chain:
        if cancel is not None:
            cancel.check()
        label = describe(link)
        is_local = link.provider == "local"

        if route == "local" and not is_local:
            skip(label, "route is local")
            continue
        if route == "api" and is_local:
            skip(label, "route is api")
            continue

        adapter = adapter_for(kind, link.provider, adapters)
        if adapter is None:
            skip(label, f"no adapter yet for {link.provider} {kind}")
            continue

        missing = missing_keys(link, env)
        if missing:
            skip(label, f"no API key ({' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set)")
            continue
        credentials = credentials_for(link, env)

        if is_local:
            probe_kwargs = {"credentials": credentials, "env": env}
            if transport is not None:
                probe_kwargs["transport"] = transport
            ok, note = adapter.probe(link, **probe_kwargs)
            if not ok:
                skip(label, note or "not reachable")
                continue

        candidates = [link] + [
            _parse_fallback(spec) for spec in FALLBACK_LINKS.get(label, ())
        ]
        answered = _run_candidates(
            kind, candidates, request, adapter=adapter, credentials=credentials,
            allow_paid=allow_paid, budget_check=budget_check, limiter=limiter,
            transport=transport, on_log=on_log, sleep_fn=sleep_fn, time_fn=time_fn,
            failures=failures, extra_kwargs={"env": env} if is_local else {},
        )
        if answered is not None:
            return answered

    detail = "\n".join(f"  {label}: {reason}" for label, reason in failures)
    raise NoRunnableLink(
        f"No link of {ENV_NAMES.get(kind, kind)} could run ({len(failures)} tried):\n{detail}",
        failures=failures,
    )


def _parse_fallback(spec):
    provider, model = spec.split("/", 1)
    return Link(provider, model)


def _run_candidates(kind, candidates, request, *, adapter, credentials, allow_paid,
                    budget_check, limiter, transport, on_log, sleep_fn, time_fn, failures,
                    extra_kwargs=None):
    """Run *candidates[0]*, swapping to the next one only on "model not available"."""
    for index, link in enumerate(candidates):
        label = describe(link)
        paid = is_paid(link)
        estimate = None
        if paid:
            if not allow_paid:
                on_log(f"   ⏭ Skipping {label}: paid link; allow_paid is off.")
                failures.append((label, "paid link; allow_paid is off"))
                return None
            estimate = adapter.estimate(link, request)
            if budget_check is not None:
                try:
                    budget_check(estimate, link)
                except Exception as exc:  # noqa: BLE001 - the refusal is the reason
                    reason = str(exc) or type(exc).__name__
                    on_log(f"   ⏭ Skipping {label}: {reason}.")
                    failures.append((label, reason))
                    return None
            on_log(f"   💸 {label}: est ${_usd(estimate):.3f} (paid, allowed)")
        elif limiter is not None:
            reason = limiter.acquire(link.provider)
            if reason:
                on_log(f"   ⏳ {label}: {reason}")
                failures.append((label, reason))
                return None

        outcome = _attempt(link, request, adapter=adapter, credentials=credentials,
                           transport=transport, on_log=on_log, sleep_fn=sleep_fn,
                           time_fn=time_fn, failures=failures, extra_kwargs=extra_kwargs or {},
                           paid=paid)
        if outcome is _SWAP:
            nxt = candidates[index + 1] if index + 1 < len(candidates) else None
            if nxt is None:
                return None
            on_log(f"   ↪ {label}: model not available on this key → trying {describe(nxt)}")
            continue
        if outcome is None:
            return None
        result = outcome
        result.paid = paid
        result.est_cost = _usd(estimate) if paid else 0.0
        return result, link
    return None


_SWAP = object()


def _attempt(link, request, *, adapter, credentials, transport, on_log, sleep_fn, time_fn, failures,
             extra_kwargs=None, paid=False):
    label = describe(link)
    # A paid request is billed once the provider accepts it (fal: at submit, before
    # the polls), so a retry after a failed poll bills a second job the ledger
    # never records. One attempt per paid link (DEC-106).
    max_attempts = 1 if paid else MAX_ATTEMPTS
    for attempt in range(1, max_attempts + 1):
        on_log(f"   🔁 {label}: attempt {attempt}/{max_attempts}")
        started = time_fn()
        try:
            result = adapter.generate(link, request, credentials=credentials, on_log=on_log,
                                      transport=transport, **(extra_kwargs or {}))
        except Exception as exc:  # noqa: BLE001 - classified below
            reason = f"{type(exc).__name__}: {exc}"
            if errors.is_model_unavailable(exc):
                failures.append((label, reason))
                return _SWAP
            verdict = errors.classify(exc)
            retryable = verdict in (errors.RETRY, errors.RATE_LIMITED)
            if retryable and attempt < max_attempts:
                wait = errors.retry_after_seconds(exc) or RETRY_BACKOFF_SECONDS
                on_log(f"   ⚠️ {label} failed | {reason} → retrying in {wait:.0f}s")
                sleep_fn(wait)
                continue
            if retryable and paid:
                reason += " (paid link: not retried, a second request could be billed again)"
            glyph = "⚠️" if retryable else "✖"
            on_log(f"   {glyph} {label} {'failed' if glyph == '⚠️' else 'fatal'} | {reason}")
            failures.append((label, reason))
            return None
        on_log(f"   ✅ {label} answered in {time_fn() - started:.1f}s")
        return result
    return None


def _usd(estimate) -> float:
    """An estimate is a number or an object with ``est_usd``; None means free."""
    if estimate is None:
        return 0.0
    if isinstance(estimate, (int, float)):
        return float(estimate)
    return float(getattr(estimate, "est_usd", 0.0) or 0.0)
