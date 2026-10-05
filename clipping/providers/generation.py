"""Generation provider chains: image, image edit, video, TTS and vision --
and the lipsync post-process of a made clip (``LIPSYNC``, DEC-258).

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

Before all three, a prompt over its link's size limit (``prompt_limits``) is
refused: never truncated, never sent, and the chain moves on.

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

from . import errors, gencache, prompt_limits
from .registry import ChainError, Link, describe, parse_chain
from .transport import urllib_transport

# ------------------------------------------------------------------ kinds

IMAGE = "image"
IMAGE_EDIT = "image_edit"
VIDEO = "video"
TTS = "tts"
VISION = "vision"
KINDS = (IMAGE, IMAGE_EDIT, VIDEO, TTS, VISION)
# A post-process of a made clip, not a generation from a prompt (DEC-258):
# the lips of a bought clip moved to its shot's dialogue. Its own chain, one
# link by default, outside ``KINDS`` -- the Settings page's chain list and
# its chain test cover the five generation kinds only.
LIPSYNC = "lipsync"
POST_KINDS = (LIPSYNC,)
ALL_KINDS = KINDS + POST_KINDS

ENV_NAMES = {
    IMAGE: "IMAGE_CHAIN",
    IMAGE_EDIT: "IMAGE_EDIT_CHAIN",
    VIDEO: "VIDEO_CHAIN",
    TTS: "TTS_CHAIN",
    VISION: "VISION_CHAIN",
    LIPSYNC: "LIPSYNC_CHAIN",
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
    # fal/ltx-2.5-fast is last (plan 23 stage C3): behind every other link no
    # automatic pick (cheapest wins, first_in_chain, first_with_audio) moves,
    # and the episode's video-link switch can still choose it.
    VIDEO: (
        "local/comfyui,fal/seedance-1-pro-fast,fal/ltx-2.3-fast,"
        "fal/kling-2.5-turbo-std,gemini/veo-3.1-lite,fal/ltx-2.5-fast"
    ),
    # elevenlabs/flash is last and paid: a keyless install skips it, and so does
    # allow_paid off (plan 23 stage B3), so nothing changes without a key.
    # No edge link (plan 28 stage B2, DEC-305 §2: its quality is too poor): the
    # adapter stays registered and ``edge`` stays a valid chain provider only
    # so a story that pinned an Edge voice before keeps speaking it through its
    # one-link chain (DEC-122); no default chain and no catalogue reaches it.
    TTS: "gemini/flash-lite-tts,local/piper,local/kokoro,local/chatterbox,elevenlabs/flash",
    VISION: f"gemini/flash-lite,openrouter/{OPENROUTER_VISION_DEFAULT_MODEL},local/ollama-vision,gemini/flash",
    LIPSYNC: "fal/kling-lipsync",
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
        notes=("Edge TTS through the edge-tts package: free, unofficial, one voice per request. "
               "Legacy: in no default chain and no AI Story voice catalogue; kept for stories that pinned an Edge voice."),
    ),
    "local": GenProvider(
        name="local",
        env_keys=(), free_tier=True, rpm=None, rpd=None, probe_timeout=10.0,
        signup_url="", base_url="",
        notes="ComfyUI, Ollama or a local TTS package on this machine or the Docker host.",
    ),
    # Plan 22 stage 5: the human is a provider. ``manual/upload`` is a link no
    # request is ever sent to: its adapter raises ``AwaitingUpload`` and the
    # file arrives through an upload route. Never hosted, never probed, never
    # paid, no key, $0 (RC-N4).
    "manual": GenProvider(
        name="manual",
        env_keys=(), free_tier=True, rpm=None, rpd=None, probe_timeout=0.0,
        signup_url="", base_url="",
        notes="Your own clips and images, made on your own subscriptions and uploaded: no call, no charge.",
    ),
    # Documented extension point (spec 8.1): in the table with a price, not in a default chain.
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
        base_url="https://api.elevenlabs.io",
        notes="Billed per character, on every link (plan 23 stage B3): Flash and Multilingual v2 voices with word timestamps.",
    ),
}

GEN_PROVIDER_NAMES = tuple(GEN_PROVIDERS)

# Which providers may appear in which chain. Refusing ``edge/x`` in
# IMAGE_CHAIN at parse time is cheaper than discovering it at run time.
KIND_PROVIDERS = {
    IMAGE: ("cloudflare", "pollinations", "local", "fal", "openai", "gemini", "manual"),
    IMAGE_EDIT: ("local", "gemini", "fal", "openai", "manual"),
    VIDEO: ("local", "fal", "gemini", "manual"),
    TTS: ("edge", "gemini", "local", "gcloud", "openai", "elevenlabs"),
    VISION: ("gemini", "openrouter", "local"),
    LIPSYNC: ("fal",),
}

# Links that cost money on a provider that is otherwise free (spec 8.1 ``*``).
# A provider with ``free_tier=False`` is paid on every link.
PAID_LINKS = frozenset({
    "gemini/nano-banana-2-lite",
    "gemini/nano-banana-2",
    "gemini/veo-3.1-lite",
    "gemini/veo-3.1-fast",
    "gemini/veo-3.1",
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


# Plan 22 stage 5: the one link of the ``manual`` provider (the human's upload).
MANUAL = "manual"
MANUAL_LINK = "manual/upload"
MANUAL_KINDS = (IMAGE, IMAGE_EDIT, VIDEO)


def is_manual(link) -> bool:
    """Whether *link* (a ``Link`` or a label) is the human's own upload."""
    provider = link.split("/", 1)[0] if isinstance(link, str) else getattr(link, "provider", None)
    return provider == MANUAL


def kinds_of(provider: str) -> tuple:
    return tuple(kind for kind in ALL_KINDS if provider in KIND_PROVIDERS[kind])


def parse_generation_chain(kind: str, chain) -> list:
    """``"a/b,c/d*"`` -> ``[Link, ...]`` for *kind*, with the paid marker stripped."""
    if kind not in ALL_KINDS:
        raise ChainError(f"Unknown generation kind {kind!r}. Known: {', '.join(ALL_KINDS)}.")
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


# A link that bills another account than its provider's other links reads its
# own variables instead of the provider's ``env_keys``. Veo and the nano-banana
# image links are billed on a separate, billing-enabled Google project, so a
# paid call never lands on the free chains' project: they read
# GEMINI_PAID_API_KEY and never GOOGLE_API_KEY, and the FREE gemini chains --
# LLM, vision, TTS -- never read GEMINI_PAID_API_KEY (RC-V4; nano-banana
# moved to the paid key in AI Story phase 7, DEC-222, amending DEC-205).
LINK_ENV_KEYS = {
    "gemini/veo-3.1-lite": ("GEMINI_PAID_API_KEY",),
    "gemini/veo-3.1-fast": ("GEMINI_PAID_API_KEY",),
    "gemini/veo-3.1": ("GEMINI_PAID_API_KEY",),
    "gemini/nano-banana-2": ("GEMINI_PAID_API_KEY",),
    "gemini/nano-banana-2-lite": ("GEMINI_PAID_API_KEY",),
}


def env_keys_for(link) -> tuple:
    """The variables *link* needs: its own (``LINK_ENV_KEYS``), else its provider's."""
    own = LINK_ENV_KEYS.get(describe(link))
    return tuple(own) if own is not None else tuple(provider_for(link).env_keys)


def missing_keys(link, env) -> list:
    return [name for name in env_keys_for(link) if not (env.get(name) or "").strip()]


def credentials_for(link, env) -> dict:
    """The values *link* is called with: its own variables and nothing else. A
    link with variables of its own gets none of its provider's optional ones."""
    optional = () if describe(link) in LINK_ENV_KEYS else tuple(provider_for(link).optional_keys)
    names = env_keys_for(link) + optional
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
    references: tuple = ()        # reference image paths (image_edit, video); the clip (lipsync)
    text: str = ""                # what to speak (tts)
    voice: str = ""               # voice id, when the link's model is not the voice
    images: tuple = ()            # frames to describe (vision)
    duration_s: float | None = None  # the clip's length in seconds, as bought (video, lipsync)
    fps: int | None = None        # the clip's frame rate (video)
    native_audio: bool = False    # the clip carries the model's own audio (video)
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


class AwaitingUpload(errors.ProviderError):
    """A ``manual/upload`` link was asked for a file (plan 22 stage 5): nothing
    is sent, nothing is booked -- the human makes it and uploads it. *kind* is
    the generation kind, *target* what is awaited (a shot id, an entity's
    image), as the caller named it in ``request.extra["target"]``."""

    def __init__(self, kind, target=None):
        self.kind = kind
        self.target = target
        what = f" ({target})" if target else ""
        super().__init__(f"{MANUAL_LINK}: awaiting your upload{what}; nothing was sent or booked")


# --------------------------------------------------------------- adapters

# ``(kind, provider) -> adapter``. An adapter is any object with
#   estimate(link, request) -> estimate or None
#   probe(link, *, credentials, **kw) -> (ok: bool, note: str)
#   generate(link, request, *, credentials, on_log, transport=None, **kw) -> GenResult
# ``kw`` carries ``transport`` when one is injected (tests) and, for a local
# link, ``env`` (where LOCAL_COMFYUI_URL / LOCAL_OLLAMA_URL come from). With a
# generation cache it also carries ``on_submit``, ``sleep_fn`` and ``time_fn``,
# and a queued adapter offers ``resume(link, request, entry, **kw)`` (fal).
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
    cache=None,
):
    """Try each link of *chain* in order; return ``(GenResult, link)`` from the first that works.

    *env* maps variable names to values (``FAL_KEY``, ``GOOGLE_API_KEY``, ...);
    it defaults to the process environment. *budget_check(estimate, link)*
    raises to refuse a paid call; its message is printed and the chain moves
    on. *limiter.acquire(provider)* returns a reason to skip a free link, or
    ``None``. *route* is ``auto`` | ``local`` | ``api`` (spec 8.2).

    *cache* is a ``gencache.GenCache`` (DEC-151..153), or ``None``: then nothing
    below changes. With one, a request that has a key is journaled: a kept
    answer is served and a submitted request resumed before any gate, every
    answer is booked through the cache's ``book`` (``meta["booked"]``, so the
    caller books nothing), and a submit that cannot be journaled or booked
    raises ``gencache.JournalError``, stopping the chain.
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

        if link.provider == MANUAL:
            # Plan 22 stage 5 (RC-N4): the human's own upload -- no route, key,
            # probe, budget, limiter or journal applies; nothing is sent or
            # booked. Its adapter says so by raising ``AwaitingUpload``, which
            # ends the chain here: a later link never stands in for the human.
            adapter = adapter_for(kind, link.provider, adapters)
            if adapter is None:
                skip(label, f"no adapter yet for {link.provider} {kind}")
                continue
            on_log(f"   ✋ {label}: awaiting your upload; nothing is sent")
            adapter.generate(link, request, credentials={}, on_log=on_log)
            raise AwaitingUpload(kind, (request.extra or {}).get("target"))

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
            failures=failures, extra_kwargs={"env": env} if is_local else {}, cache=cache,
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
                    extra_kwargs=None, cache=None):
    """Run *candidates[0]*, swapping to the next one only on "model not available".

    A journaled request the provider proves it never ran (``gencache.resume_verdict``:
    a 404 from its own status URL) is voided, its booking released, and the
    same request is sent once more on the same link in this run -- a new
    submit, through every gate again and booked again (RC-A3)."""
    for index, link in enumerate(candidates):
        label = describe(link)
        paid = is_paid(link)
        resent = False
        while True:
            estimate = None
            journal = cache.journal(kind, link, request, paid=paid, on_log=on_log) if cache is not None else None
            if journal is not None:
                # What an earlier run left comes first: a kept answer or a request
                # to resume costs nothing more, so it passes no gate (DEC-152).
                found = journal.lookup()
                if found == gencache.DONE:
                    paths = journal.restore(request)
                    if paths is not None:
                        return _kept(link, journal, paths, on_log), link
                elif found == gencache.SUBMITTED:
                    result = _resume(link, request, journal, adapter=adapter, credentials=credentials,
                                     transport=transport, on_log=on_log, sleep_fn=sleep_fn, time_fn=time_fn,
                                     failures=failures, extra_kwargs=extra_kwargs or {})
                    if isinstance(result, _Voided):
                        # The earlier run's request was never run: this run sends it, once, gated.
                        resent = True
                        on_log(f"   ↩ {label}: sending the request again, once, through the gates")
                    elif result is None:
                        return None
                    else:
                        result.paid = bool(journal.entry.get("paid"))
                        result.est_cost = float(journal.entry.get("est_usd") or 0.0)
                        return result, link
            refusal = _prompt_refusal(kind, link, request)
            if refusal is not None:
                # Free: nothing was sent, so no estimate, no budget verdict, no
                # free-tier slot and no journal entry; the link stays usable for
                # a shorter prompt.
                on_log(f"   ⏭ Skipping {label}: {refusal}.")
                failures.append((label, refusal))
                return None
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

            if journal is not None:
                journal.begin(_usd(estimate) if paid else 0.0)
            outcome = _attempt(link, request, adapter=adapter, credentials=credentials,
                               transport=transport, on_log=on_log, sleep_fn=sleep_fn,
                               time_fn=time_fn, failures=failures, extra_kwargs=extra_kwargs or {},
                               paid=paid, journal=journal, limiter=limiter)
            if isinstance(outcome, _Voided):
                if resent:
                    failures.append((label, f"{outcome.reason} (voided; it was already sent again once in "
                                            "this run)"))
                    return None
                resent = True
                on_log(f"   ↩ {label}: sending the request again, once, through the gates")
                continue
            if outcome is _SWAP:
                nxt = candidates[index + 1] if index + 1 < len(candidates) else None
                if nxt is None:
                    return None
                on_log(f"   ↪ {label}: model not available on this key → trying {describe(nxt)}")
                break
            if outcome is None:
                return None
            result = outcome
            result.paid = paid
            result.est_cost = _usd(estimate) if paid else 0.0
            return result, link
    return None


_SWAP = object()


class _Voided:
    """:func:`_resume`'s word that the journaled request was proven never run
    and voided (its booking released), and is worth sending once more."""

    def __init__(self, reason):
        self.reason = reason


def _prompt_refusal(kind, link, request):
    """Why *request*'s prompt (a TTS line's text) cannot go to *link*, or None:
    over the link's size limit (``prompt_limits.check``), as a chain reason
    headed ``prompt_limits.REFUSAL_HEAD``. A kept answer or a resumed request
    never comes here: it was accepted when it was sent."""
    try:
        prompt_limits.check(link, prompt_limits.sent_text(kind, request))
    except prompt_limits.PromptTooLong as exc:
        return f"{prompt_limits.REFUSAL_HEAD}{exc}; not sent"
    return None


def _release_free_slot(limiter, paid, link, exc) -> None:
    """Give back the free-tier slot the runner counted for *link* when the
    provider refused the call for credentials (401/403) instead of actually
    spending the allowance (T2-P5-F12): the counter must reflect calls the
    provider served, not calls it turned away at the door. A paid link is
    never counted by the limiter, and not every limiter offers ``release``
    (fakes in tests), so both are checked first."""
    if paid or limiter is None or errors.status_code(exc) not in (401, 403):
        return
    release = getattr(limiter, "release", None)
    if release is not None:
        release(link.provider)


def _attempt(link, request, *, adapter, credentials, transport, on_log, sleep_fn, time_fn, failures,
             extra_kwargs=None, paid=False, journal=None, limiter=None):
    if journal is not None:
        return _journaled_attempt(link, request, journal, adapter=adapter, credentials=credentials,
                                  transport=transport, on_log=on_log, sleep_fn=sleep_fn, time_fn=time_fn,
                                  failures=failures, extra_kwargs=extra_kwargs or {}, paid=paid,
                                  limiter=limiter)
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
            _release_free_slot(limiter, paid, link, exc)
            glyph = "⚠️" if retryable else "✖"
            on_log(f"   {glyph} {label} {'failed' if glyph == '⚠️' else 'fatal'} | {reason}")
            failures.append((label, reason))
            return None
        on_log(f"   ✅ {label} answered in {time_fn() - started:.1f}s")
        return result
    return None


# ------------------------------------------------ journaled calls (DEC-151..153)

class _Sent:
    """The transport, counting the requests handed to it: a paid call that
    failed with none counted provably sent nothing, so it is unbilled (DEC-153).
    ``free`` is the same transport, uncounted, for a request that bills
    nothing on its own (the lipsync's uploads to fal storage, DEC-258)."""

    def __init__(self, transport):
        self._transport = transport or urllib_transport
        self.free = self._transport
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self._transport(*args, **kwargs)


def _journal_kwargs(journal, extra_kwargs, sleep_fn, time_fn) -> dict:
    # The runner's own sleep (cancel-aware when a token is given) and clock, so
    # a cancel cuts a poll short and leaves the request journaled.
    return dict(extra_kwargs, on_submit=journal.on_submit, sleep_fn=sleep_fn, time_fn=time_fn)


def _journaled_attempt(link, request, journal, *, adapter, credentials, transport, on_log, sleep_fn,
                       time_fn, failures, extra_kwargs, paid, limiter=None):
    """``_attempt`` with a journal: the same attempts, glyphs and swap, plus
    the journal's seams. A request the provider acknowledged is resumed, never
    submitted again (:func:`_resume`); a paid call that failed before any
    acknowledgement is booked by the conservative rule; an answer is booked
    through the journal."""
    label = describe(link)
    kwargs = _journal_kwargs(journal, extra_kwargs, sleep_fn, time_fn)
    max_attempts = 1 if paid else MAX_ATTEMPTS  # DEC-106: a paid request is submitted once
    for attempt in range(1, max_attempts + 1):
        on_log(f"   🔁 {label}: attempt {attempt}/{max_attempts}")
        started = time_fn()
        sent = _Sent(transport) if paid and getattr(adapter, "speaks_through_transport", False) else None
        try:
            result = adapter.generate(link, request, credentials=credentials, on_log=on_log,
                                      transport=transport if sent is None else sent, **kwargs)
        except gencache.JournalError:
            raise
        except Exception as exc:  # noqa: BLE001 - classified below
            if journal.state == gencache.SUBMITTED:
                return _resume(link, request, journal, adapter=adapter, credentials=credentials,
                               transport=transport, on_log=on_log, sleep_fn=sleep_fn, time_fn=time_fn,
                               failures=failures, extra_kwargs=extra_kwargs, failure=exc, used=attempt)
            reason = f"{type(exc).__name__}: {exc}"
            if paid:
                journal.unanswered(exc, sent=None if sent is None else sent.calls > 0)
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
            _release_free_slot(limiter, paid, link, exc)
            glyph = "⚠️" if retryable else "✖"
            on_log(f"   {glyph} {label} {'failed' if glyph == '⚠️' else 'fatal'} | {reason}")
            failures.append((label, reason))
            return None
        return _journaled_answer(link, journal, result, started=started, on_log=on_log, time_fn=time_fn)
    return None


def _resume(link, request, journal, *, adapter, credentials, transport, on_log, sleep_fn, time_fn,
            failures, extra_kwargs, failure=None, used=0):
    """Poll and fetch the journaled request again, up to ``MAX_ATTEMPTS``
    attempts in this run (DEC-152): a resume is neither a submit nor a charge,
    so it is retried and never gated again. The answer, or ``None`` when the
    link ends: the provider settled the request (``failed``) or it is gone
    (``lost``), both still booked; or it is kept ``submitted`` for the next run
    (a poll budget spent, a fatal error, the attempts used up). A refusal that
    proves the request never ran (``gencache.resume_verdict``) voids it and
    releases its booking (:func:`_void`): a :class:`_Voided` when the same
    request is worth sending once more (a purged 404), else ``None``."""
    label = describe(link)
    request_id = journal.request_id
    resume = getattr(adapter, "resume", None)
    kwargs = _journal_kwargs(journal, extra_kwargs, sleep_fn, time_fn)
    while True:
        if failure is not None:
            reason = f"{type(failure).__name__}: {failure}"
            verdict = gencache.resume_verdict(failure, (journal.entry or {}).get("request"))
            if verdict is not None:
                return _void(link, journal, failure, reason, verdict, on_log=on_log, failures=failures)
            settled = None
            if errors.status_code(failure) in (404, 410):
                settled = gencache.LOST
            elif isinstance(failure, gencache.RequestFailed):
                settled = gencache.FAILED
            if settled is not None:
                journal.settle(settled, reason)
                on_log(f"   ✖ {label} {settled} | {reason}; request {request_id} stays booked")
                failures.append((label, f"{reason} (request {request_id} {settled}; it stays booked)"))
                return None
            retryable = errors.classify(failure) in (errors.RETRY, errors.RATE_LIMITED)
            if not (retryable and used < MAX_ATTEMPTS and resume is not None):
                on_log(f"   ⚠️ {label} failed | {reason}; request {request_id} kept for the next run")
                failures.append((label, f"{reason} (request {request_id} kept for the next run; it stays booked)"))
                return None
            wait = errors.retry_after_seconds(failure) or RETRY_BACKOFF_SECONDS
            on_log(f"   ⚠️ {label} failed | {reason} → resuming request {request_id} in {wait:.0f}s")
            sleep_fn(wait)
        if resume is None:
            reason = f"request {request_id} is journaled but this adapter cannot resume it"
            on_log(f"   ⚠️ {label}: {reason}; kept for the next run")
            failures.append((label, reason))
            return None
        used += 1
        on_log(f"   ↩️ {label}: resuming request {request_id} ({used}/{MAX_ATTEMPTS})")
        started = time_fn()
        try:
            result = resume(link, request, dict(journal.entry), credentials=credentials, on_log=on_log,
                            transport=transport, **kwargs)
        except gencache.JournalError:
            raise
        except Exception as exc:  # noqa: BLE001 - classified at the top of the loop
            failure = exc
            continue
        result = _journaled_answer(link, journal, result, started=started, on_log=on_log, time_fn=time_fn)
        result.meta["resumed"] = True
        return result


def _void(link, journal, failure, reason, verdict, *, on_log, failures):
    """The submitted request *failure* proves never run: written ``void``, its
    booking released (``Journal.void``), said in one line. A :class:`_Voided`
    when it is worth sending once more (*verdict*'s ``resend``), else the
    link ends here (``None``) with the refusal as its reason -- unbilled, so
    neither kept for the next run nor booked."""
    label = describe(link)
    status, resend = verdict
    request_id = journal.request_id
    types = tuple(getattr(failure, "error_types", ()) or ())
    if types:
        # The provider's own error type, whole: a cut detail never hides it.
        reason += f" [error type: {', '.join(types)}]"
    est = float((journal.booked or {}).get("est_usd") or 0.0)
    was_booked = journal.booked is not None
    released = journal.void(f"HTTP {status}: request {request_id} was never run by the provider; unbilled")
    if released is not None:
        booking = f"its booking of ${released:.3f} is released"
    elif was_booked:
        booking = f"its booking of ${est:.3f} stays (it could not be released)"
    else:
        booking = "nothing was booked for it"
    on_log(f"   ↩ {label}: request {request_id} was never run by the provider (HTTP {status}); {booking}")
    if resend:
        return _Voided(reason)
    failures.append((label, f"{reason} (request {request_id} refused, never run: {booking})"))
    return None


def _journaled_answer(link, journal, result, *, started, on_log, time_fn):
    if result.meta is None:
        result.meta = {}
    journal.answered(result.paths, seed=result.seed, meta=result.meta)
    result.meta["booked"] = journal.booked
    result.meta["cache_key"] = journal.key
    on_log(f"   ✅ {describe(link)} answered in {time_fn() - started:.1f}s")
    return result


def _kept(link, journal, paths, on_log):
    """The kept answer as a result: no call, no gate, nothing booked again."""
    entry = journal.entry
    on_log(f"   ♻️ {describe(link)}: kept answer {journal.key[:12]}, no call made")
    meta = dict(entry.get("meta") or {})
    meta.update(cached=True, booked=entry.get("booked"), cache_key=journal.key)
    return GenResult(provider=link.provider, model=link.model, paths=paths, seed=entry.get("seed"),
                     est_cost=0.0, paid=bool(entry.get("paid")), meta=meta)


def _usd(estimate) -> float:
    """An estimate is a number or an object with ``est_usd``; None means free."""
    if estimate is None:
        return 0.0
    if isinstance(estimate, (int, float)):
        return float(estimate)
    return float(getattr(estimate, "est_usd", 0.0) or 0.0)
