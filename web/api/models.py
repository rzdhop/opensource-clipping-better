"""
web.api.models — Pydantic schemas for request/response validation.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

# Render defaults are sourced from the CLI config so the API and the CLI cannot
# drift apart: config_adapter falls back to these same constants.
from clipping.config import (
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
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    RENDERING = "rendering"
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
    # The three-pass analyzer over LLM_CHAIN. The default.
    CHAIN = "chain"
    # Single-request legacy path, kept as an escape hatch.
    NVIDIA = "nvidia"
    GEMINI = "gemini"


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
    use_hook_glitch: bool = True
    use_auto_bgm: bool = True
    use_karaoke_effect: bool = True
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
    # "auto" follows the transcript's own language.
    output_language: str = "auto"
    # Run the analysis, save it, and stop before any ffmpeg work.
    dry_run_analysis: bool = False
    gemini_model: str = "gemini-3-flash-preview"
    gemini_fallback_model: str = "gemini-2.5-flash"
    nvidia_model: str = "google/gemma-4-31b-it"
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
    # Defaults
    default_clips: Optional[int] = None
    default_ratio: Optional[AspectRatio] = None
    default_font_style: Optional[FontStyle] = None
    default_whisper_model: Optional[str] = None
    default_whisper_device: Optional[WhisperDevice] = None
    default_ai_provider: Optional[AIProvider] = None


class SettingsResponse(BaseModel):
    """Current settings (keys are masked)."""
    google_api_key_set: bool = False
    pexels_api_key_set: bool = False
    hf_token_set: bool = False
    nvidia_api_key_set: bool = False
    groq_api_key_set: bool = False
    openrouter_api_key_set: bool = False
    mistral_api_key_set: bool = False
    default_clips: int = 7
    default_ratio: str = "9:16"
    default_font_style: str = "HORMOZI"
    default_whisper_model: str = "large-v3"
    default_whisper_device: str = "cuda"
    default_ai_provider: str = "nvidia"
    gpu_available: bool = False


class SystemHealthResponse(BaseModel):
    """System health check."""
    status: str = "ok"
    version: str
    gpu_available: bool
    ffmpeg_available: bool
    jobs_running: int
    jobs_queued: int
