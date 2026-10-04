"""
web.api.models — Pydantic schemas for request/response validation.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_serializer

# Render defaults are sourced from the CLI config so the API and the CLI cannot
# drift apart: config_adapter falls back to these same constants.
from clipping.config import (
    KARAOKE_HIGHLIGHT_COLOR,
    NVIDIA_MODEL,
    VIDEO_PRESET,
    VIDEO_QUALITY_CQ,
    VIDEO_QUALITY_CRF,
    VIDEO_SCALE_ALGO,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    # The server tried to fetch source_url and was refused by the site. Not a
    # failure: the job keeps its id, its settings and its output directory, and
    # resumes as soon as a file is attached to POST /api/jobs/{id}/source.
    NEEDS_UPLOAD = "needs_upload"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    RENDERING = "rendering"
    # A story step (kind "story_step") at work. Interrupted by a restart like
    # any of the processing states above, and failed for it.
    RUNNING = "running"
    # A story step finished and waits for the user. Finished for the worker --
    # the slot is freed, the stream closes, a cancel is refused -- but not for
    # the user, and it survives a restart. Approving it, or superseding it with
    # a regenerated step, moves it on to COMPLETED; nothing else does.
    AWAITING_APPROVAL = "awaiting_approval"
    # Plan 22 stage 5 (the manual link): a story step that waits for the
    # user's own clips (the assets step, or the fast track or agent run paused
    # there). Finished for the worker, as AWAITING_APPROVAL is; an upload that
    # leaves nothing missing runs the step again and moves this one on to
    # COMPLETED (``resumed_by``). It survives a restart.
    AWAITING_UPLOADS = "awaiting_uploads"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AspectRatio(str, enum.Enum):
    RATIO_9_16 = "9:16"
    RATIO_16_9 = "16:9"
    RATIO_1_1 = "1:1"
    RATIO_3_4 = "3:4"
    RATIO_4_5 = "4:5"


class FontStyle(str, enum.Enum):
    DEFAULT = "DEFAULT"
    STORYTELLER = "STORYTELLER"
    HORMOZI = "HORMOZI"
    CINEMATIC = "CINEMATIC"


class FaceDetector(str, enum.Enum):
    MEDIAPIPE = "mediapipe"
    YOLO = "yolo"


class AIProvider(str, enum.Enum):
    # The three-pass analyzer over LLM_CHAIN. The default, and now the only
    # way a transcript is analysed: the single-request path the other two
    # members named is deleted.
    CHAIN = "chain"
    # The same analyzer against one endpoint that speaks the OpenAI chat API:
    # OpenRouter, Groq, Mistral, a self-hosted vLLM, a local Ollama.
    # config.apply_openai_compat_alias turns it into a one-link chain.
    OPENAI_COMPAT = "openai_compat"


class Platform(str, enum.Enum):
    """Target platform, which sets the clip duration window."""

    TIKTOK = "tiktok"
    REELS = "reels"
    SHORTS = "shorts"
    AUTO = "auto"
    LONG = "long"


class WhisperDevice(str, enum.Enum):
    CUDA = "cuda"
    CPU = "cpu"
    AUTO = "auto"


class SplitTrigger(str, enum.Enum):
    """Mirrors ``--split-trigger`` choices in clipping/config.py:310."""
    DIARIZATION = "diarization"
    FACE = "face"


class VideoScaleAlgo(str, enum.Enum):
    """Mirrors ``--video-scale-algo`` choices in clipping/config.py:528."""
    LANCZOS = "lanczos"
    BICUBIC = "bicubic"
    BILINEAR = "bilinear"
    AREA = "area"


class YoloSize(str, enum.Enum):
    """Mirrors ``--yolo-size`` choices in clipping/config.py:387.

    Constrained rather than free-form because config_adapter interpolates this
    value into both a local model filename and a HuggingFace download URL.
    """
    V8N = "8n"
    V8S = "8s"
    V8M = "8m"
    V8N_V2 = "8n_v2"
    V9C = "9c"


# ---------------------------------------------------------------------------
# Job Creation Request
# ---------------------------------------------------------------------------

class JobCreateRequest(BaseModel):
    """Payload to create a new clipping job."""

    # Source
    upload_filename: Optional[str] = Field(None, description="Filename of an uploaded video")
    reuse_job_id: Optional[str] = Field(None, description="Existing Job ID to reuse its downloads and JSON")

    # Main settings
    clips: int = Field(7, ge=1, le=30, description="Number of clips to generate")
    ratio: AspectRatio = Field(AspectRatio.RATIO_9_16, description="Output aspect ratio")
    render_height: str = Field("1080", description="Target output height")

    # Content & Hook
    words_per_sub: int = Field(5, ge=1, le=15)
    hook_duration: int = Field(3, ge=1, le=10)
    use_broll: bool = True
    # Off by default: a one-second full-frame effect on every clip is an
    # opt-in, not an opt-out. See clipping/config.USE_HOOK_GLITCH.
    use_hook_glitch: bool = False
    use_auto_bgm: bool = True
    # Off by default, like the glitch: see clipping/config.LOUDNORM.
    loudnorm: bool = False
    use_karaoke_effect: bool = True
    # The word being spoken, in ASS BGR: &HBBGGRR&. Default is yellow.
    karaoke_color: str = Field(
        KARAOKE_HIGHLIGHT_COLOR, pattern=r"^&H[0-9A-Fa-f]{6}&$"
    )
    use_split_screen: bool = False
    use_camera_switch: bool = False
    no_subs: bool = False

    # Hook V2
    hook_v2: bool = False
    hook_v2_items: int = Field(3, ge=2, le=6)
    hook_v2_style: str = "controversial_fast_glitch"
    white_flash_duration: float = 0.12
    no_segment_trim: bool = False
    silence_trim: bool = False

    # Split screen, camera switch & diarization
    use_dynamic_split: bool = False
    split_trigger: SplitTrigger = SplitTrigger.DIARIZATION
    split_zoom: float = 1.0
    split_v_align: float = 0.5
    split_auto_zoom: bool = False
    split_max_zoom: float = 2.5
    switch_hold_duration: float = 2.0
    switch_blend_duration: float = 0.0
    # "auto" or an explicit speaker count, matching _parse_speakers
    # (clipping/config.py:169). Consumers do str(x).lower() == "auto".
    diarization_speakers: Union[Literal["auto"], int] = "auto"

    # Subtitle & Typography
    font_style: FontStyle = FontStyle.HORMOZI
    advanced_text: bool = False
    advanced_text_hook: bool = False

    # Render / encoding
    video_cq: int = VIDEO_QUALITY_CQ
    video_crf: int = VIDEO_QUALITY_CRF
    video_bitrate: str = "auto"
    video_sharpen: bool = False
    video_preset: str = VIDEO_PRESET
    video_scale_algo: VideoScaleAlgo = VideoScaleAlgo(VIDEO_SCALE_ALGO)
    static_crop: bool = False

    # Whisper
    whisper_model: str = "large-v3"
    whisper_device: WhisperDevice = WhisperDevice.AUTO
    whisper_compute_type: str = "auto"

    # AI
    # Local-first inputs.
    transcript_filename: Optional[str] = None
    transcript_offset: float = 0.0
    source_url: Optional[str] = None
    # The dashboard has always sent this, but it was never declared here, so
    # Pydantic dropped it and the "Bypass AI" toggle silently did nothing.
    load_gemini_json: bool = False
    ai_provider: AIProvider = AIProvider.CHAIN
    # Empty means "use $LLM_CHAIN, else the shipped default" — resolved in the
    # provider registry so one place owns it.
    llm_chain: str = ""
    # Ordered transcription chain; empty uses the hosted-first default,
    # "none" disables transcription, "local/faster-whisper" forces in-process.
    stt_chain: str = ""
    platform: Platform = Platform.AUTO
    # One line of context handed to every scan window, e.g. "home espresso gear
    # review". Optional, and free: nothing is asked of a model to obtain it.
    topic: str = ""
    # Reuse this job's own scan answers on a rerun. Off re-asks the model.
    analysis_cache: bool = True
    # Transcript windows scanned at once. Default 1, because the provider this
    # ships with serialises concurrent requests on one key and measured SLOWER
    # at 2 -- see clipping/analysis/analyzer.DEFAULT_ANALYSIS_WORKERS.
    analysis_workers: int = Field(1, ge=1, le=3)
    # "auto" follows the transcript's own language.
    output_language: str = "auto"
    # Run the analysis, save it, and stop before any ffmpeg work.
    dry_run_analysis: bool = False
    gemini_model: str = "gemini-3-flash-preview"
    gemini_fallback_model: str = "gemini-2.5-flash"
    # One definition, in clipping/providers/registry.py. Never a literal here:
    # the four copies of this string were how the dashboard and the CLI came to
    # disagree about which model a job with no explicit setting would call.
    nvidia_model: str = NVIDIA_MODEL
    # Per-job override for the custom endpoint's model. Empty means "use the
    # one saved in Settings". The base URL and key are credentials and live
    # only in Settings, never in a job payload.
    openai_compat_model: str = ""
    face_detector: FaceDetector = FaceDetector.MEDIAPIPE
    yolo_size: YoloSize = YoloSize.V8M


# ---------------------------------------------------------------------------
# Job Progress Event
# ---------------------------------------------------------------------------

class JobProgressEvent(BaseModel):
    """Single progress event for SSE streaming."""
    step: str
    step_number: int
    total_steps: int
    message: str
    percent: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # --- what is actually happening inside this step ----------------------
    # `message` names the step; these say what it is doing right now, which is
    # the difference between "Analyzing with AI..." for forty minutes and
    # "NVIDIA, attempt 2 of 3".
    #
    # `detail` is the last line the pipeline printed. Keeping it generic rather
    # than a fixed vocabulary means every step reports something useful without
    # web/ having to know what clipping/ prints.
    detail: Optional[str] = None
    provider: Optional[str] = None      # "nvidia" | "gemini"
    model: Optional[str] = None         # the exact model id being asked
    attempt: Optional[int] = None       # retry ladder position, when retrying
    max_attempts: Optional[int] = None
    clip_index: Optional[int] = None    # render loop: clip N...
    clip_total: Optional[int] = None    # ...of M
    # When the CURRENT step began. The UI shows time-in-step, which is what
    # tells a user whether something is stuck; time-since-creation does not.
    step_started_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Job Activity Event
# ---------------------------------------------------------------------------

class JobEvent(BaseModel):
    """One line of output the pipeline printed while this job was running.

    Captured by :mod:`web.api.activity`, which tees stdout/stderr on the worker
    thread. ``seq`` is a per-job counter rather than a list index because the
    feed is a ring buffer and indices would shift as it drops from the front.
    """
    seq: int = 0
    ts: Optional[datetime] = None
    level: str = "info"
    source: str = "stdout"
    message: str


# ---------------------------------------------------------------------------
# Clip Detail
# ---------------------------------------------------------------------------

class ClipDetail(BaseModel):
    """Metadata for a single rendered clip."""
    rank: int
    viral_score: Optional[int] = None
    title: Optional[str] = None
    title_en: Optional[str] = None
    filename: str
    duration: Optional[float] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    download_url: str
    thumbnail_url: Optional[str] = None
    # A clip with no speech legitimately has no .srt, so this stays optional.
    srt_url: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Job Response
# ---------------------------------------------------------------------------

class JobResponse(BaseModel):
    """Full job detail for API responses."""
    id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    upload_filename: Optional[str] = None
    transcript_filename: Optional[str] = None
    source_url: Optional[str] = None
    # Retained so job records created before the local-first purge still
    # deserialize; never populated for new jobs.
    url: Optional[str] = None
    config: dict = Field(default_factory=dict)
    progress: Optional[JobProgressEvent] = None
    clips: list[ClipDetail] = Field(default_factory=list)
    error: Optional[str] = None
    log: list[str] = Field(default_factory=list)
    # The pipeline's own console output. `log` keeps the coarse worker messages
    # it always had; this is the detailed feed the dashboard tails live.
    events: list[JobEvent] = Field(default_factory=list)
    # "clip", or "story_step" for an AI Story step. A record written before
    # kinds existed has none and is a clip job. The fields below belong to a
    # story step and are null on a clip job.
    kind: str = "clip"
    story_id: Optional[str] = None
    ep: Optional[int] = None
    step: Optional[str] = None
    params: Optional[dict] = None
    approved_at: Optional[datetime] = None
    # The id of the regenerated step job that replaced this one.
    superseded_by: Optional[str] = None
    # The archive of the episode whose document this job awaited approval for
    # (POST /api/stories/{id}/switch-pipeline): settled, never approved.
    discarded: Optional[str] = None
    # Plan 21 stage 1: the part a chained story step is on (the agent run,
    # ``story-fast-track``: ``steps.story_fast_track.PARTS``); null for every
    # other job.
    sub_step: Optional[str] = None
    # Plan 22 stage 5: what a job awaiting uploads waits for ({count, missing,
    # message, brief}), and the job an upload started to go on with it.
    uploads: Optional[dict] = None
    resumed_by: Optional[str] = None


class JobListResponse(BaseModel):
    """Paginated job list."""
    jobs: list[JobResponse]
    total: int


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsRequest(BaseModel):
    """Settings update payload."""
    google_api_key: Optional[str] = None
    pexels_api_key: Optional[str] = None
    hf_token: Optional[str] = None
    nvidia_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    mistral_api_key: Optional[str] = None
    openai_compat_base_url: Optional[str] = None
    openai_compat_api_key: Optional[str] = None
    openai_compat_model: Optional[str] = None
    # AI Story's own chain override (phase 7 stage 2b, DEC-224). Empty clears
    # it, like every other value here (DEC-043); a non-empty value is
    # validated as a chain (registry.parse_chain) before it is stored. No
    # dashboard field reads or writes this yet (stage 7).
    story_llm_chain: Optional[str] = None
    # The premium writing chain (plan 22 stage 1, DEC-273): same validation
    # and clearing rule as story_llm_chain above.
    story_llm_premium_chain: Optional[str] = None
    # Defaults
    default_clips: Optional[int] = None
    default_ratio: Optional[AspectRatio] = None
    default_font_style: Optional[FontStyle] = None
    default_whisper_model: Optional[str] = None
    default_whisper_device: Optional[WhisperDevice] = None
    default_ai_provider: Optional[AIProvider] = None
    # Let a chain job run when only the slow floor (NVIDIA) has a key. False
    # CLEARS the stored switch rather than storing "0" (DEC-043).
    allow_slow_chain: Optional[bool] = None
    # Budget (AI Story, DEC-097): the switch clears like allow_slow_chain; the
    # caps are amounts in USD; the profile is free | one_dollar | quality, or
    # "" to follow the switch.
    allow_paid: Optional[bool] = None
    per_episode_cap_usd: Optional[float] = None
    daily_cap_usd: Optional[float] = None
    per_story_cap_usd: Optional[float] = None
    budget_profile: Optional[str] = None
    # Generation providers (spec 8.6). Empty clears, like every key (DEC-043).
    fal_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    cloudflare_api_token: Optional[str] = None
    cloudflare_account_id: Optional[str] = None
    pollinations_api_key: Optional[str] = None
    # Veo and the nano-banana images (DEC-222): a separate, billing-enabled
    # Google project (RC-V4). The
    # response half (``gemini_paid_api_key_set``) has reported this since
    # stage 2; this is the save side (phase 6 stage 12).
    gemini_paid_api_key: Optional[str] = None
    # Claude on the Anthropic API (plan 23 stage D1): billed per request.
    anthropic_api_key: Optional[str] = None
    # ElevenLabs voices (plan 23 stage B3): billed per character.
    elevenlabs_api_key: Optional[str] = None
    local_comfyui_url: Optional[str] = None
    local_ollama_url: Optional[str] = None


class SettingsResponse(BaseModel):
    """Current settings (keys are masked)."""
    google_api_key_set: bool = False
    pexels_api_key_set: bool = False
    hf_token_set: bool = False
    nvidia_api_key_set: bool = False
    groq_api_key_set: bool = False
    openrouter_api_key_set: bool = False
    mistral_api_key_set: bool = False
    openai_compat_api_key_set: bool = False
    # Echoed back, unlike the keys, because the Settings page has to prefill
    # them and New Job has to say which of the three is still missing.
    openai_compat_base_url: str = ""
    openai_compat_model: str = ""
    # Echoed back like the compat URL/model above: not a secret, and the
    # Settings page needs its value to prefill. "" means AI Story falls
    # through to LLM_CHAIN, then to DEFAULT_STORY_LLM_CHAIN (resolve_chain).
    story_llm_chain: str = ""
    # Echoed back like story_llm_chain above: "" means a premium prompt
    # falls through to STORY_LLM_CHAIN, then LLM_CHAIN, then
    # registry.PREMIUM_STORY_LLM_CHAIN (resolve_premium_chain).
    story_llm_premium_chain: str = ""
    allow_slow_chain: bool = False
    # Budget (AI Story): five-place defaults, clipping/providers/budget.py
    allow_paid: bool = False
    per_episode_cap_usd: float = 4.0
    daily_cap_usd: float = 12.0
    per_story_cap_usd: float = 40.0
    budget_profile: str = ""
    effective_budget_profile: str = "free"
    spend_today_usd: float = 0.0
    # Plan 23 A3, response only (never settings): today's day key and zone, what
    # was allowed on top of the daily cap for today, whether the saved cap is
    # below what today already spent, and the stories that spent it.
    spend_day: str = ""
    spend_zone: str = "UTC"
    day_extra_usd: float = 0.0
    daily_cap_below_spend: bool = False
    day_contributors: list = []
    # Generation providers (spec 8.6): keys as booleans, the effective local
    # URLs, every chain's links as the runner sees them, today's free usage.
    fal_key_set: bool = False
    openai_api_key_set: bool = False
    cloudflare_api_token_set: bool = False
    cloudflare_account_id_set: bool = False
    pollinations_api_key_set: bool = False
    # Veo's and nano-banana's key (a separate, billing-enabled Google project). Reported
    # here; the field that sets it arrives with its Settings control.
    gemini_paid_api_key_set: bool = False
    anthropic_api_key_set: bool = False
    elevenlabs_api_key_set: bool = False
    local_comfyui_url: str = ""
    local_ollama_url: str = ""
    generation_chains: dict = {}
    usage_today: dict = {}
    # Why a chain job would be refused right now, or "" when it would start.
    # The server's own verdict (chain_readiness), so the dashboard never keeps a
    # second copy of the rule that could disagree with POST /api/jobs.
    chain_blocked_reason: str = ""
    default_clips: int = 7
    default_ratio: str = "9:16"
    default_font_style: str = "HORMOZI"
    default_whisper_model: str = "large-v3"
    default_whisper_device: str = "auto"
    # "chain" since the three-pass analyzer landed. tests/test_web_settings_defaults
    # asserts this equals clipping.config.AI_PROVIDER, so the API cannot report a
    # default the pipeline does not use.
    default_ai_provider: str = "chain"
    gpu_available: bool = False


class ChainTestRequest(BaseModel):
    """Which chain to test. Empty tests the configured one.

    There is deliberately no base URL here: ``custom/<model>`` resolves
    LLM_CUSTOM_BASE_URL from the environment, so this route cannot be pointed
    at an arbitrary host.
    """
    llm_chain: str = ""


class ChainLinkResult(BaseModel):
    """One link's answer to the real analysis request (DEC-090).

    ``status``: ``ok`` completed the real request; ``alive`` failed it but
    answered a ping (reachable, cannot do the job); ``failed`` neither;
    ``no_key`` skipped; ``unused`` a key that is set for a provider the chain
    does not name -- never contacted (DEC-023); ``listed`` a provider whose
    every request is billed: its key was accepted and its model listed by
    the free model lookup, and no request was sent (plan 23 stage D1).
    """
    label: str
    provider: str
    model: str
    status: Literal["ok", "alive", "failed", "no_key", "unused", "listed"]
    latency_seconds: Optional[float] = None
    reason: Optional[str] = None
    probe_timeout_seconds: float
    # How long the real request was allowed (registry.diagnostic_timeout).
    work_timeout_seconds: Optional[float] = None
    primary: bool
    env_key: str
    signup_url: str = ""
    # Which question the row's status answers.
    kind: Optional[Literal["work", "ping", "listed"]] = None
    # What the real request found in the test transcript.
    candidates: Optional[int] = None
    found_moment: Optional[bool] = None
    # The structured-output rung that worked, and the model that answered --
    # not the link's own when a retired model was swapped (DEC-089).
    level: Optional[str] = None
    used_model: Optional[str] = None
    # One sentence for a person, or "".
    note: str = ""


class GenerationChainTestRequest(BaseModel):
    """Which generation chain to test (DEC-103).

    ``kind`` is image | image_edit | video | tts | vision. ``chain`` overrides
    the configured chain; ``link`` names ONE link to run -- the only way a paid
    link is ever called by this route, and then at most once.
    """
    kind: str
    link: str = ""
    chain: str = ""


class GenerationLinkResult(BaseModel):
    """One link of a generation chain, as the chain runner would treat it."""
    label: str
    provider: str
    model: str
    status: Literal["ok", "failed", "no_key", "no_adapter", "unreachable", "refused", "skipped"]
    paid: bool = False
    est_usd: float = 0.0
    # Whether the budget would let this link run right now (free links: yes).
    allowed: bool = False
    reason: Optional[str] = None
    note: Optional[str] = None
    latency_seconds: Optional[float] = None
    # A signed URL under /api/outputs/_chain_test/ for the sample it produced.
    artifact_url: Optional[str] = None
    artifact_kind: Optional[str] = None
    env_keys: list[str] = []
    missing_keys: list[str] = []
    signup_url: str = ""


class GenerationChainTestResponse(BaseModel):
    kind: str
    chain: str
    # ready: a free or local link answered (or the named paid link did).
    # paid_only: nothing free answered but a paid link is keyed and allowed.
    # no_adapter: every link waits for a later phase. blocked: nothing can run.
    verdict: Literal["ready", "paid_only", "blocked", "no_adapter"] = "blocked"
    results: list[GenerationLinkResult]
    elapsed_seconds: float
    message: str = ""
    tested_link: Optional[str] = None


class ChainTestResponse(BaseModel):
    """Every keyed link, asked the real request -- not just up to the first."""
    chain: str
    # ready: a primary link completed the real request and a job may start.
    # floor_only: only the slow floor did. blocked: the key gate would refuse
    # the job (DEC-073). dead: nothing completed it.
    verdict: Literal["ready", "floor_only", "blocked", "dead"] = "dead"
    # verdict == "ready".
    ready: bool
    live_link: Optional[str] = None
    results: list[ChainLinkResult]
    elapsed_seconds: float
    # Why it is not ready, when it is not.
    message: str = ""


class SystemHealthResponse(BaseModel):
    """System health check."""
    status: str = "ok"
    version: str
    gpu_available: bool
    ffmpeg_available: bool
    jobs_running: int
    jobs_queued: int


# ---------------------------------------------------------------------------
# AI Story (phase 1: spec 2.1, 9.2) -- routes/stories.py
#
# Request bodies only; the story routes answer plain JSON dicts, documented on
# each route. "Sent or not" is model_fields_set, never a None check: a PATCH
# that sends `"logline": null` clears the logline, one that leaves it out
# leaves it alone.
# ---------------------------------------------------------------------------

class GenerationProfileModel(BaseModel):
    """A story's generation profile (spec 2.1, 8, 8.1, 8.5).

    The defaults are the story defaults of ``clipping/aistory/defaults.py``;
    tests/test_stories_api.py asserts they agree, and the story-default
    agreement test reads the four lines below as text.
    """
    tier: int = 1
    route: Literal["auto","local","api"] = "auto"
    consistency_mode: Literal["references","prompt_only"] = "references"
    budget_profile: Literal["free","one_dollar","quality","native_speech","native_speech_manual"] = "free"
    # Optional (phase 7, DEC-221): left out, a story is on the legacy pipeline.
    pipeline: Optional[Literal["v2"]] = None
    # Optional (plan 22): a native-speech story's speaking-clip model; left
    # out, the budget profile's (media_policy.speech_link).
    speech_model: Optional[Literal["lite","fast","premium"]] = None
    # Optional (plan 22 stage 5): "manual" -- the sheets, plates, props and
    # keyframes are the human's own uploads; left out, the profile's links.
    images: Optional[Literal["manual"]] = None
    # Optional (plan 23 stage D4): left out, three sheets a character (portrait,
    # turnaround, expressions) and the style's own body rules.
    sheet_mode: Optional[Literal["three_sheet","two_view","two_view_expressions"]] = None
    body_rule: Optional[Literal["human_body","all_matter"]] = None

    @model_serializer(mode="wrap")
    def _without_unset_pipeline(self, handler):
        # An absent pipeline is no key at all, as in a story written before it.
        data = handler(self)
        if isinstance(data, dict) and data.get("pipeline") is None:
            data.pop("pipeline", None)
        if isinstance(data, dict) and data.get("speech_model") is None:
            data.pop("speech_model", None)
        if isinstance(data, dict) and data.get("images") is None:
            data.pop("images", None)
        for key in ("sheet_mode", "body_rule"):
            if isinstance(data, dict) and data.get(key) is None:
                data.pop(key, None)
        return data


class StoryCreateRequest(BaseModel):
    """POST /api/stories. There is deliberately no default language: a French
    user who forgot the field must not get an English season (defaults.py)."""
    language: Literal["fr", "en"]
    seed_text: Optional[str] = Field(None, max_length=2000)
    style_template_id: Optional[str] = None
    generation_profile: Optional[GenerationProfileModel] = None
    # Plan 20 stage 1: the story's own episode template (the new-story form's
    # "Episode format", pre-filled from the style's suggestion); left out,
    # the pipeline's default. An unshipped id is a 400 (store.create).
    episode_template_id: Optional[str] = None
    # Plan 21 stage 1: Studio (every step waits for your approval, the
    # default) or agent mode (one story-fast-track job approves by rule),
    # stored as ``generation_profile.mode`` -- an agent story only.
    mode: Literal["studio", "agent"] = "studio"


class StoryPatchRequest(BaseModel):
    """PATCH /api/stories/{id}. Only the fields sent are applied.

    Values are checked by the story schema when the story is saved (400 with
    its errors), not here, so the rules live in one place. A bible field sent
    clears the bible approval; ``title``, ``seed_text``, ``narrator``,
    ``generation_profile`` and ``episode_template_id`` do not. ``narrator``
    and ``generation_profile`` may be partial: they are merged onto the
    story's current values, and the profile is checked against
    ``clipping.aistory.defaults`` (400, not 422, which is why it is a plain
    object here). ``episode_template_id`` (phase 3) is one of the shipped
    episode templates (400), and changes only while no episode has a script
    (409).
    """
    title: Optional[str] = Field(None, max_length=120)
    seed_text: Optional[str] = Field(None, max_length=2000)
    logline: Optional[str] = None
    premise: Optional[str] = None
    tone: Optional[str] = None
    genre_tags: Optional[list[str]] = None
    world: Optional[dict] = None
    themes_and_values: Optional[list[str]] = None
    audience: Optional[dict] = None
    why_come_back: Optional[list[str]] = None
    narrator: Optional[dict] = None
    generation_profile: Optional[dict] = None
    episode_template_id: Optional[str] = None


class SubtitleStylePatchRequest(BaseModel):
    """PATCH /api/stories/{id}/subtitle-style (plan 23 stage B5): the story's
    own subtitle look as a whole -- the body replaces it -- or a JSON ``null``
    (or ``{}``) to clear it.

    Every field is optional and typed loosely on purpose, and an unknown key
    is kept: the values are checked once, by ``clipping.aistory.
    subtitle_style.validate`` (400 with ``{"message", "errors"}``, the
    contrast ratio named), not by pydantic (a 422). ``box`` is ``{colour,
    opacity_pct}`` or ``null`` (no box).
    """
    model_config = ConfigDict(extra="allow")

    font_family: Any = None
    size_pct: Any = None
    position_pct: Any = None
    text_colour: Any = None
    highlight_colour: Any = None
    outline_px: Any = None
    outline_colour: Any = None
    box: Any = None


class StorySwitchPipelineRequest(BaseModel):
    """POST /api/stories/{id}/switch-pipeline: ``generation_profile`` as
    ``StoryPatchRequest``'s (partial, merged, checked by the workflow: 400,
    which is why it is a plain object here); ``regenerate_episodes`` archives
    the episodes that have a script when the profile moves the story onto
    or off the v2 pipeline (``workflow.switch_pipeline``) -- without it that
    move is refused as ``PATCH`` refuses it (409)."""
    generation_profile: dict
    regenerate_episodes: bool = False


class ConceptChooseRequest(BaseModel):
    """POST /api/stories/{id}/concepts/choose: exactly one of the two.

    ``concept_id`` is a library id or a generated card's ``gen_NN``;
    ``concept`` is a card the user wrote (or edited), in the shape of a
    generated card.
    """
    concept_id: Optional[str] = None
    concept: Optional[dict] = None


class ConceptsGenerateRequest(BaseModel):
    """POST /api/stories/{id}/concepts/generate (optional body, plan 21
    stage 1): ``count``, the number of concepts to write, 1 to 10 (checked by
    ``workflow.concepts_request``: 400, not 422); left out, ten."""
    count: Optional[Any] = None


class StoryStepRequest(BaseModel):
    """POST /api/stories/{id}/steps/{step} (spec 9.1: ``{ep?, params?}``)."""
    ep: Optional[int] = None
    params: Optional[dict] = None


class StoryRegenerateRequest(BaseModel):
    """POST /api/stories/{id}/regenerate: one target of the spec 9.2 grammar,
    and an optional note for the model ("make it darker").

    ``voice`` (phase 2) is the voice a user picked for the target
    ``character:<id>:voice``: ``{provider, voice_id, rate?, pitch?}``, checked
    by ``clipping.aistory.workflow.check_voice_choice`` (400/409, not 422,
    which is why it is a plain object here). Refused with any other target.
    """
    target: str
    note: Optional[str] = Field(None, max_length=300)
    voice: Optional[dict] = None


# Phase 2 (spec 2.3-2.5, 9.2): inline edits of a character, a place, a prop.
# Only the fields sent are applied (model_fields_set). Values are checked by
# the entity's own rules when it is saved (400 with their errors), not here,
# so the rules live in one place (clipping.aistory.workflow.patch_entity).
# Every edit clears that entity's approval.

class CharacterPatchRequest(BaseModel):
    """PATCH /api/stories/{id}/characters/{char_id}.

    ``voice_direction`` and ``sample_line`` edit the pinned voice and the
    voice brief; ``rate`` and ``pitch`` the pinned voice (``"+10%"``,
    ``"-5Hz"``). ``personality`` is merged onto the current one. A change to
    the descriptor or the signature items recomputes the prompt block; a
    change to the sample line, rate or pitch removes the voice sample.
    ``look`` and ``dossier`` (phase 7, a v2 story only: 409 otherwise) are
    merged onto the current blocks and checked by the character schema (400
    with its errors, not 422, which is why they are plain objects here).
    """
    name: Optional[str] = None
    role: Optional[str] = None
    archetype: Optional[str] = None
    one_line: Optional[str] = None
    descriptor: Optional[str] = None
    signature_items: Optional[list[str]] = None
    personality: Optional[dict] = None
    voice_direction: Optional[str] = None
    sample_line: Optional[str] = None
    rate: Optional[str] = None
    pitch: Optional[str] = None
    look: Optional[dict] = None
    dossier: Optional[dict] = None


class PlacePatchRequest(BaseModel):
    """PATCH /api/stories/{id}/places/{place_id}. A change to the descriptor
    or the layout notes recomputes the prompt block. ``look`` (phase 7, a v2
    story only) -- the layout map, the scale note, the light per time
    variant, the props that live there -- is merged onto the current one."""
    name: Optional[str] = None
    one_line: Optional[str] = None
    descriptor: Optional[str] = None
    layout_notes: Optional[str] = None
    look: Optional[dict] = None


class PropPatchRequest(BaseModel):
    """PATCH /api/stories/{id}/props/{prop_id}. ``owner_char_id`` is one of
    the story's characters, or null. A change to the descriptor recomputes
    the prompt block. ``look`` (phase 7, a v2 story only) is merged onto the
    current one."""
    name: Optional[str] = None
    one_line: Optional[str] = None
    descriptor: Optional[str] = None
    owner_char_id: Optional[str] = None
    look: Optional[dict] = None


class KnowledgeBeatPatch(BaseModel):
    """One beat of ``PATCH /knowledge``'s ``beats``, named by its episode and
    its 1-based position in that episode's timeline entry; each field sent
    replaces the beat's own (``place_id: null`` is "no place")."""
    ep: int
    beat: int
    what: Optional[str] = None
    place_id: Optional[str] = None
    who: Optional[list[str]] = None
    objects: Optional[list[str]] = None
    knows_after: Optional[dict] = None


class KnowledgePatchRequest(BaseModel):
    """PATCH /api/stories/{id}/knowledge (phase 7 stage 7, a v2 story's
    knowledge base). Only what is sent is applied (``model_fields_set``):
    ``world`` merged onto the current one, ``beats`` by episode and position,
    ``props_registry`` as a whole, ``ledger_seed`` merged per character.
    Checked by the knowledge rules and every id against the story when it is
    saved (400 with every error); any write moves ``rev``, so an approved
    base must be approved again (``clipping.aistory.workflow.patch_knowledge``)."""
    world: Optional[dict] = None
    beats: Optional[list[KnowledgeBeatPatch]] = None
    props_registry: Optional[list[str]] = None
    ledger_seed: Optional[dict] = None


# Phase 3 (spec 2.7, 2.8, 9.2): one episode's documents. Only what is sent is
# applied (model_fields_set, the list items' too); an item names what it
# edits by its id. The field lists are the workflow's closed lists
# (SCRIPT_PATCH_FIELDS & co., compared in tests/test_stories_api_episode.py);
# values -- the closed lists of emotions, framings, camera motions, modifiers
# and transitions among them -- are checked by the episode rules when the
# document is saved (400 with every error), not here, so the rules live in
# one place (clipping.aistory.workflow.patch_script / patch_storyboard).

class ScriptLinePatch(BaseModel):
    """One line of ``PATCH /episodes/{ep}/script``'s ``lines``: a speaker of
    its scene (or the narrator, when the story has one), at most 22 words."""
    line_id: str
    text: Optional[str] = None
    speaker: Optional[str] = None
    emotion: Optional[str] = None
    delivery: Optional[str] = None


class ScriptScenePatch(BaseModel):
    """One scene of ``PATCH /episodes/{ep}/script``'s ``scenes``;
    ``pays_off`` (AI Story phase 5 stage 7) names at most one hook open
    before the episode, [] or null for none."""
    scene_id: str
    summary: Optional[str] = None
    on_screen_text: Optional[str] = None
    pays_off: Optional[list[str]] = None


class ScriptPatchRequest(BaseModel):
    """PATCH /api/stories/{id}/episodes/{ep}/script. A change re-times the
    script, clears the script's approval and stales the consistency report;
    a text-only edit (a line's words or delivery, a scene's ``pays_off``)
    keeps the storyboard's approval and its shots -- a scene whose words
    changed is re-timed in place once re-voiced -- and any other change
    clears it and stales the storyboard's scenes it changed (DEC-129 as
    amended, ``workflow.patch_script``)."""
    lines: Optional[list[ScriptLinePatch]] = None
    scenes: Optional[list[ScriptScenePatch]] = None
    hook_on_screen_text: Optional[str] = None
    cliffhanger_reveal: Optional[str] = None
    next_episode_teaser: Optional[str] = None


class StoryboardShotPatch(BaseModel):
    """One shot of ``PATCH /episodes/{ep}/storyboard``'s ``shots``: its action
    names people, the place and props by their tags only (``@char_x``,
    ``#place_y:variant``, ``%prop_z``)."""
    shot_id: str
    framing: Optional[str] = None
    camera_motion: Optional[str] = None
    modifiers: Optional[list[str]] = None
    action: Optional[str] = None
    keep_still: Optional[bool] = None
    prompt_override: Optional[str] = None


class StoryboardTransitionPatch(BaseModel):
    """One transition of ``PATCH /episodes/{ep}/storyboard``'s
    ``transitions``, named by the shot it follows (``after``)."""
    after: str
    type: Optional[str] = None


class StoryboardPatchRequest(BaseModel):
    """PATCH /api/stories/{id}/episodes/{ep}/storyboard. A shot whose framing,
    motion or action changed is resolved again; ``refresh_prompts: true``
    resolves every prompt from the entities as they are now. A change clears
    the storyboard's approval."""
    shots: Optional[list[StoryboardShotPatch]] = None
    transitions: Optional[list[StoryboardTransitionPatch]] = None
    refresh_prompts: Optional[bool] = None


class StoryApproveRequest(BaseModel):
    """POST /api/stories/{id}/approve/{doc}'s optional body. ``approve_anyway``
    (``script:<ep>`` and, phase 7 stage 6b, ``keyframes:<ep>`` only)
    approves a script whose consistency or first-watch check found issues,
    or a v2 episode's keyframes whose check (J2) failed or did not run; the
    approval records it.

    ``direction`` (phase 5, ``feedback:<ep>`` only, required there -- 0, 1, 2
    or null for none) chooses which of F1's three directions steers episode
    ``ep`` + 1's script and its proposals; sent for any other document, the
    workflow answers 400. "Sent or not" is ``model_fields_set``, as PATCH
    above: a body that leaves ``direction`` out is not the same as one that
    sends ``"direction": null`` -- the first is not a choice, the second is
    "no direction". Its value reaches ``workflow.approve_series`` exactly as
    sent, never coerced here (``Any``, not ``int``), so a bool, a float or a
    text answers the same ``invalid`` the CLI's own check would."""
    approve_anyway: Optional[bool] = None
    direction: Optional[Any] = None


# Phase 4 (spec 3 steps 10-12 and the fast track, 9.2). The params of the new
# steps, carried by ``StoryStepRequest.params``: the fields are the
# workflow's closed lists (ASSETS_PARAMS & co., compared in
# tests/test_stories_api_phase4.py; the metadata step takes none); values are
# checked by the step's own rules before a job exists (400 naming the
# choices: clipping.aistory.workflow.phase4_request), and the job carries
# only what was sent (model_fields_set), so the runner fills its defaults.

class AssetsStepParams(BaseModel):
    """``POST /steps/assets``'s params: ``align_words`` opts in to forced
    alignment of the lines whose voice timed no words (DEC-165); ``animate``
    (tier >= 2, phase 6 stage 8) makes the clips after the images and
    voices unless sent false."""
    align_words: Optional[bool] = None
    animate: Optional[bool] = None


class RenderStepParams(BaseModel):
    """``POST /steps/render``'s params: ``subtitles`` (``style`` -- the style
    lock's own --, ``word_pop``, ``two_line``, ``none``; DEC-164),
    ``encoder`` (``libx264``, or ``auto``: a hardware encoder for the final
    pass, opt-in) and ``fill_failed_with_motion`` (phase 6 stage 9: at tier
    >= 2 a shot whose clip failed, went stale or is still generating gets
    Tier-1 motion instead of refusing the render; off by default)."""
    subtitles: Optional[str] = None
    encoder: Optional[str] = None
    fill_failed_with_motion: Optional[bool] = None


class FastTrackStepParams(BaseModel):
    """``POST /steps/fast-track``'s params: ``storyboard`` -- ``t1`` (one T1
    call per scene) or ``fast`` (the deterministic plan, no call);
    ``stop_at_keyframes`` (stage C) stops a v2 episode once its keyframes are
    made and checked, for the human's own approval, instead of the default:
    the one click records that approval itself and goes up to the render.
    ``stop_on_script_issues`` (plan 19 stage 3) stops a v2 episode at its
    script over blocking issues its repair passes could not fix, instead of
    the default: once they are spent, the one click approves it anyway."""
    storyboard: Optional[str] = None
    stop_at_keyframes: Optional[bool] = None
    stop_on_script_issues: Optional[bool] = None


class AssetsShotPatch(BaseModel):
    """One shot of ``PATCH /episodes/{ep}/assets``'s ``shots``: ``locked``
    keeps the image it has (only a shot with an image may be locked); phase 6
    stage 11, what its clip does -- ``keep_still``, ``animate`` (a pin: the
    planner animates it first) and ``keep_native_audio`` (tier 3) -- true or
    false, or null to clear the override (``workflow.ASSETS_SHOT_FLAG_FIELDS``,
    kept in ``assets.json``, never the storyboard). "Sent" is
    ``model_fields_set``, so a null sent is not a field left out."""
    shot_id: str
    locked: Optional[bool] = None
    keep_still: Optional[bool] = None
    animate: Optional[bool] = None
    keep_native_audio: Optional[bool] = None


class AssetsPatchRequest(BaseModel):
    """PATCH /api/stories/{id}/episodes/{ep}/assets. A lock never moves the
    storyboard's revision or approval; it is part of the fingerprint the
    assets are approved with, so a new one makes that approval stale.

    ``links`` (phase 6, A-087) ``{"image"?: "<link>", "video"?: "<link>"}``
    switches the episode's image or video link -- the sticky offer's
    ``switch`` -- checked against the Settings chains by
    ``workflow.patch_assets`` (400 with its errors, not 422, which is why it
    is a plain object here)."""
    shots: Optional[list[AssetsShotPatch]] = None
    links: Optional[dict] = None


# Phase 5 (spec 2.6, 9.1, 9.2, plan 11 stages 4-5): the series steps --
# ``memory``, ``feedback``, ``propose-next`` -- run through the generic
# ``POST /steps/{step}`` (``StoryStepRequest``, unchanged) like any other
# episode step. These two are the series' own endpoints.

class StoryEpisodeFeedbackRequest(BaseModel):
    """POST /api/stories/{id}/episodes/{ep}/feedback: the audience comments
    pasted for the episode (and, optionally, its stats), stored as one item
    per episode -- a new paste replaces the one before -- then the
    ``feedback`` step is queued. ``text`` and ``stats`` are refused whole,
    never trimmed, over 6,000 characters each (422 here, pydantic's own
    ``max_length``, the same cap as ``clipping.aistory.schemas.
    FEEDBACK_TEXT_MAX_LENGTH`` / ``FEEDBACK_STATS_MAX_LENGTH``); an empty
    text is ``workflow.store_feedback``'s own 400, which also re-checks the
    cap for a caller that skips this model (the CLI)."""
    text: str = Field(..., max_length=6000)
    stats: Optional[str] = Field(None, max_length=6000)


class StoryProposalDecisionRequest(BaseModel):
    """POST /api/stories/{id}/episodes/{ep}/proposals/{item_id}: accept or
    reject one item of the N1 proposals made for episode ``ep``.

    ``accept`` is required and reaches ``workflow.decide_proposal`` exactly
    as sent, never coerced here (``Any``, not ``bool``), so a non-bool (a
    ``1``, a ``"yes"``) answers the same ``invalid`` (400) the CLI's own
    check would. ``role`` overrides the proposal's own role, chosen only
    when accepting a character (one of ``clipping.aistory.schemas.
    CHARACTER_ROLES``; sent otherwise, the workflow answers 400)."""
    accept: Any
    role: Optional[str] = None


# ---------------------------------------------------------------------------
# Budget: today's spending and the "allow more for today" override (plan 23 A3)
# ---------------------------------------------------------------------------

class BudgetExtraRequest(BaseModel):
    """POST /api/budget/today/extra: allow ``usd`` more for today only.

    Every limit is the route's own 400, not pydantic's 422: ``usd`` must be
    positive and keep today's extra within ``budget.DAY_EXTRA_MAX_USD`` (the
    ledger's ``ValueError`` message is the answer), ``story_id`` must name an
    existing story (the grant is then logged in its activity log), ``note`` is
    at most 200 characters, ``estimate_usd`` (what the refused call needed) is
    not negative."""
    usd: float
    story_id: Optional[str] = None
    note: Optional[str] = None
    estimate_usd: Optional[float] = None


class BudgetStoryUsd(BaseModel):
    story_id: str
    title: str = ""
    usd: float = 0.0


class BudgetGrant(BaseModel):
    day: str = ""
    at: str = ""
    usd: float = 0.0
    story_id: Optional[str] = None
    note: str = ""


class BudgetTodayResponse(BaseModel):
    """Today's paid spending against the daily cap (``GET /api/budget/today``).

    ``extra_usd`` is what was allowed for today on top of ``daily_cap_usd``;
    ``effective_cap_usd`` is their sum. ``cap_below_spend`` is true when the
    saved cap is below what today already spent. ``stories`` are the biggest
    contributors, ``other_usd`` the rest of ``spent_usd``. ``zone_error`` is
    null unless the spend file's zone could not be used."""
    day: str
    zone: str = "UTC"
    zone_error: Optional[str] = None
    spent_usd: float = 0.0
    extra_usd: float = 0.0
    daily_cap_usd: float = 0.0
    effective_cap_usd: float = 0.0
    cap_below_spend: bool = False
    stories: list[BudgetStoryUsd] = []
    other_usd: float = 0.0
    grants_today: list[BudgetGrant] = []
    resets_at: str = ""
