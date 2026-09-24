"""
clipping.config — Master Configuration (Dashboard)

Holds all default values and builds the config from CLI args.
"""

import argparse
from collections import namedtuple
import os
import re
from types import SimpleNamespace

# Valid device / compute-type values live beside the resolver that
# interprets them, so argparse and the resolver cannot disagree.
from clipping.device import VALID_COMPUTE_TYPES, VALID_DEVICES

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ==============================================================================
# DEFAULT VALUES  (identical to notebook Cell 0)
# ==============================================================================

BASE_DIR = os.getcwd()
FONT_DIR = os.path.abspath(os.path.join(BASE_DIR, "custom_fonts"))

# 1. MAIN SETTINGS
JUMLAH_CLIP = 7
PILIHAN_RASIO = "9:16"

# 2. CONTENT & HOOK SETTINGS
MAX_KATA_PER_SUBTITLE = 5
DURASI_HOOK = 3
USE_BROLL = True
# Off by default. The transition is a one-second full-frame effect dropped
# between the hook and the body of every clip, and it fires on EVERY clip unless
# asked not to -- an opt-out that most runs did not want. Enable per job in the
# dashboard, or with --hook-glitch.
USE_HOOK_GLITCH = False
USE_SPLIT_SCREEN = False
USE_CAMERA_SWITCH = False
DIARIZATION_NUM_SPEAKERS = "auto"
SWITCH_HOLD_DURATION = 2.0
SWITCH_BLEND_DURATION = 0.0  # 0 = instant snap, >0 = smooth blend in seconds

# Source Platform

# 3. SUBTITLE & TYPOGRAPHY SETTINGS (ASS STYLE)
USE_ADVANCED_TEXT = False
USE_ADVANCED_TEXT_ON_HOOK = False
USE_KARAOKE_EFFECT = True

GAYA_FONT_AKTIF = "HORMOZI"

DAFTAR_FONT = {
    "DEFAULT": {
        "utama": {
            "nama": "Montserrat Black",
            "file": "Montserrat-Black.ttf",
            "url": "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Black.ttf",
            "bold": 1,
        },
        "khusus": {
            "nama": "Montserrat Medium",
            "file": "Montserrat-Medium.ttf",
            "url": "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Medium.ttf",
            "bold": 0,
        },
    },
    "STORYTELLER": {
        "utama": {
            "nama": "Inter",
            "file": "Inter-Regular.ttf",
            "url": "https://cdn.jsdelivr.net/fontsource/fonts/inter@latest/latin-400-normal.ttf",
            "bold": 0,
        },
        "khusus": {
            "nama": "Lora",
            "file": "Lora-Bold.ttf",
            "url": "https://cdn.jsdelivr.net/fontsource/fonts/lora@latest/latin-700-normal.ttf",
            "bold": 1,
        },
    },
    "HORMOZI": {
        "utama": {
            "nama": "Montserrat",
            "file": "Montserrat-Regular.ttf",
            # NOT the fontsource mirror. cdn.jsdelivr.net/fontsource/fonts/
            # montserrat@latest/latin-400-normal.ttf serves a 48832-byte face
            # whose name table says "Montserrat Thin", so libass found no family
            # called "Montserrat" and every clip was burned in DejaVuSans while
            # the pipeline printed "All fonts prepared successfully". This is the
            # upstream project, and the same source the DEFAULT style above
            # already uses. Verified: 445928 bytes, family "Montserrat".
            "url": "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Regular.ttf",
            "bold": 0,
        },
        "khusus": {
            "nama": "Anton",
            "file": "Anton-Regular.ttf",
            "url": "https://cdn.jsdelivr.net/fontsource/fonts/anton@latest/latin-400-normal.ttf",
            "bold": 0,
        },
    },
    "CINEMATIC": {
        "utama": {
            "nama": "Roboto",
            "file": "Roboto-Regular.ttf",
            "url": "https://cdn.jsdelivr.net/fontsource/fonts/roboto@latest/latin-400-normal.ttf",
            "bold": 0,
        },
        "khusus": {
            "nama": "Bebas Neue",
            "file": "BebasNeue-Regular.ttf",
            "url": "https://cdn.jsdelivr.net/fontsource/fonts/bebas-neue@latest/latin-400-normal.ttf",
            "bold": 0,
        },
    },
}

# Specific to 9:16 (Vertical)
ASS_ALIGN_916 = 2
ASS_MARGIN_916 = 450
ASS_FONT_916 = 90
SCALE_KATA_KHUSUS_916 = ASS_FONT_916 + 120

# Specific to 16:9 (Horizontal)
ASS_ALIGN_169 = 2
ASS_MARGIN_169 = 70
ASS_FONT_169 = 80
SCALE_KATA_KHUSUS_169 = ASS_FONT_169 + 120

# ASS inline colours are BGR, not RGB: &H[Blue][Green][Red]&.
#
# These two are DIFFERENT KNOBS and get confused constantly:
#
#   WARNA_KATA_KHUSUS      the AI-chosen emphasis words, when karaoke is OFF
#   KARAOKE_HIGHLIGHT_COLOR the word currently being spoken, when karaoke is ON
#
# The second was hardcoded in two branches of subtitles.py, which is how the
# one visual choice people actually ask about became the one they could not
# change. The default is exactly the yellow it replaced.
WARNA_KATA_KHUSUS = "&HFFFFFF&"
KARAOKE_HIGHLIGHT_COLOR = "&H00FFFF&"
# What the highlight reverts to: the base subtitle colour. Not a setting --
# changing it would mean every word rendering in the highlight's off-state.
KARAOKE_BASE_COLOR = "&HFFFFFF&"

# 4. EXTERNAL ASSET SETTINGS
NAMA_FONT_THUMBNAIL = "Montserrat-Black.ttf"
URL_FONT_THUMBNAIL = (
    "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Black.ttf"
)

# Empty on purpose: the FFmpeg lavfi generator in clipping/studio/effects.py is
# the intended path. The video that used to be pinned here
# (youtube.com/watch?v=5nBcNRYmjs0) went private, so EVERY run spent a yt-dlp
# attempt on it, printed "⚠️ Glitch download failed: Private video", and then
# generated the transition locally anyway -- which is what it does now without
# the detour or the alarming line. Set this to any URL yt-dlp can fetch to use a
# real clip instead; siapkan_glitch_video already handles both cases.
URL_GLITCH_VIDEO = ""
URL_MEDIAPIPE_MODEL = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_full_range/float16/latest/blaze_face_full_range.tflite"

# 5. Auto-BGM & Audio Ducking SETTINGS
USE_AUTO_BGM = True
BGM_BASE_VOLUME = 0.25
BGM_MODE = "ducking"  # 'ducking' = sidechain compress, 'background' = constant volume mix

# List of supported moods (matches folder names under assets/bgm/)
BGM_MOODS = ["chill", "epic", "sad", "upbeat", "suspense"]
BGM_DIR = os.path.abspath(os.path.join(BASE_DIR, "assets", "bgm"))

# Whisper
WHISPER_MODEL = "large-v3"
WHISPER_DEVICE = "auto"
WHISPER_COMPUTE_TYPE = "auto"
VIDEO_QUALITY_CQ = 23
VIDEO_QUALITY_CRF = 20
VIDEO_PRESET = "auto"
VIDEO_SCALE_ALGO = "lanczos"
RENDER_OUTPUT_HEIGHT = 1080

from clipping.analysis.presets import DEFAULT_PRESET, PRESET_NAMES
from clipping.providers.registry import DEFAULT_LLM_CHAIN, NVIDIA_DEFAULT_MODEL

# AI Provider
# "chain" walks LLM_CHAIN with the three-pass analyzer (clipping/analysis/) and
# is the only way the transcript is analysed now. "openai_compat" is the same
# analyzer pointed at one custom endpoint; apply_openai_compat_alias rewrites it
# into a one-link chain at config time.
#
# The single-provider legacy path that used to live here -- one request asking
# for 22 fields per clip -- is deleted. At ~1200 output tokens per clip against
# a measured 12-13 tokens/s it could not finish for more than about three clips,
# and every job that ever ran it failed.
AI_PROVIDER = "chain"
# ONE definition, in the stdlib-only registry, together with the measurements
# that picked it and the two traps that cost the most time (a listed model is not
# necessarily callable; the fast candidates are reasoning models that must have
# thinking turned off). Do not paste a model id back in here -- an agreement test
# fails if any of the four former copies grows a literal again.
NVIDIA_MODEL = NVIDIA_DEFAULT_MODEL
GEMINI_MODEL = "gemini-3-flash-preview"
GEMINI_FALLBACK_MODEL = "gemini-2.5-flash"

# The provider CHAIN used by the new analysis path. Unlike the single
# --ai-provider above, this is an ordered list of "<provider>/<model>" links
# tried in order, and every hop is printed. That is not the silent
# cross-provider fallback DEC-003 forbade: it is a list the user wrote down, and
# a provider absent from it is never contacted.
#
# The catalogue of providers, their free-tier limits and the default chain live
# in clipping/providers/registry.py, which is stdlib-only and importable with no
# SDK installed. Re-pick the models with tools/bench_llm.py.
LLM_CHAIN = os.environ.get("LLM_CHAIN", "").strip()
LLM_TIMEOUT = 0  # 0 = use each provider's own default

# Run a chain job even when the only keyed links are the slow floor (NVIDIA).
# Off by default: see chain_readiness below and DEC-073.
ALLOW_SLOW_CHAIN = (
    os.environ.get("ALLOW_SLOW_CHAIN", "").strip().lower() in {"1", "true", "yes"}
)

# Hosted transcription, same "<provider>/<model>" spelling. Empty uses the
# default in clipping/providers/stt.py; "none" disables transcription entirely
# (the modern spelling of --no-whisper); "local/faster-whisper" forces the
# in-process path. Local Whisper is kept but is no longer the default: on a
# CPU-only host it was measured at 4.6x realtime, i.e. 94 minutes for a
# 20-minute video.
STT_CHAIN = os.environ.get("STT_CHAIN", "").strip()


# ==============================================================================
# CLI PARSER
# ==============================================================================


_ASS_COLOUR_RE = re.compile(r"^&H[0-9A-Fa-f]{6}&$")


def _ass_colour(value: str) -> str:
    """Validate an ASS inline colour, e.g. ``&H00FFFF&``.

    Validated rather than trusted because libass ignores an override it cannot
    parse and carries on: a typo here would produce subtitles with no highlight
    at all, no error, and nothing in the log to explain it.
    """
    text = str(value).strip()
    if not _ASS_COLOUR_RE.match(text):
        raise argparse.ArgumentTypeError(
            f"{value!r} is not an ASS colour. Write &HBBGGRR& -- six hex "
            f"digits between ampersands, in BLUE-GREEN-RED order, "
            f"e.g. &H00FFFF& for yellow."
        )
    return text


def _parse_speakers(val: str) -> str | int:
    if val.lower() == "auto":
        return "auto"
    try:
        return int(val)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{val}' is not a valid integer or 'auto'")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="🎬 rzdhop's clips — AI Auto-Clipper & Teaser Generator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # --- Local input (local-first) ---
    # The pipeline assumes nothing about how the media was acquired: external
    # tools produce the .mp4 and the .vtt, and these flags point at them.
    p.add_argument(
        "--video", "-v", default=None,
        help="Path to the local source video (.mp4/.mkv/.mov/...). Required unless --story-mode is used.",
    )
    p.add_argument(
        "--transcript", "-t", default=None,
        help="Path to a local transcript (.vtt/.srt/.json3). If given, Whisper is skipped entirely.",
    )
    p.add_argument(
        "--transcript-offset", type=float, default=0.0,
        help="Shift every transcript timestamp by N seconds (for a video trimmed after its transcript was made).",
    )
    p.add_argument(
        "--no-whisper", action="store_true", default=False,
        help="Fail loudly instead of falling back to Whisper when --transcript is absent.",
    )
    p.add_argument(
        "--source-url", default=None,
        help="Source attribution for the description/manifest only. Never fetched.",
    )

    # --- Main settings ---
    p.add_argument(
        "--clips",
        "-n",
        type=int,
        default=JUMLAH_CLIP,
        help="Number of highlight clips to generate",
    )
    p.add_argument(
        "--ratio",
        "-r",
        default=PILIHAN_RASIO,
        choices=["9:16", "16:9", "1:1", "3:4", "4:5"],
        help="Output aspect ratio",
    )
    p.add_argument(
        "--render-height",
        default=str(RENDER_OUTPUT_HEIGHT),
        help="Target output height for the render. Use 'source' to match the source video height, or a number (e.g. 1080, 1440).",
    )

    # --- Content & Hook ---
    p.add_argument(
        "--words-per-sub",
        type=int,
        default=MAX_KATA_PER_SUBTITLE,
        help="Max words per karaoke subtitle group",
    )
    p.add_argument(
        "--hook-duration",
        type=int,
        default=DURASI_HOOK,
        help="Hook teaser duration in seconds",
    )
    p.add_argument(
        "--hook-source",
        default=None,
        help="Google Drive URL or local path for a single custom hook video (.mp4)",
    )
    p.add_argument(
        "--hook-source-start",
        type=float,
        default=0.0,
        help="Start time in seconds for the custom hook video",
    )
    p.add_argument("--no-broll", action="store_true", help="Disable B-roll footage")
    # --hook-glitch turns it ON now that the default is off. --no-hook is kept
    # and still wins, so an existing script that disables it explicitly keeps
    # working and does not silently start enabling it.
    p.add_argument("--hook-glitch", action="store_true",
                   help="Enable the glitch transition between hook and body")
    p.add_argument("--no-hook", action="store_true", help="Disable hook glitch teaser")
    p.add_argument("--no-bgm", action="store_true", help="Disable background music")
    p.add_argument(
        "--bgm-mode",
        choices=["ducking", "background"],
        default=BGM_MODE,
        help="BGM mixing mode: 'ducking' (sidechain compress — BGM auto-lowers during speech) or 'background' (constant low volume mix)",
    )
    p.add_argument(
        "--no-karaoke",
        action="store_true",
        help="Disable karaoke highlight effect (use clean text instead)",
    )
    p.add_argument(
        "--split-screen",
        action="store_true",
        default=USE_SPLIT_SCREEN,
        help="Enable split-screen mode for podcast with 2 speakers (9:16 only, requires HF_TOKEN for Pyannote)",
    )
    p.add_argument(
        "--diarization-speakers",
        type=_parse_speakers,
        default=DIARIZATION_NUM_SPEAKERS,
        help="Number of speakers for diarization, or 'auto' to auto-detect visually (used with --split-screen or --camera-switch)",
    )
    p.add_argument(
        "--camera-switch",
        action="store_true",
        default=USE_CAMERA_SWITCH,
        help="Enable camera-switch mode for podcast (9:16 only, requires HF_TOKEN). "
        "Mutually exclusive with --split-screen; split-screen takes precedence if both are set.",
    )
    p.add_argument(
        "--switch-hold-duration",
        type=float,
        default=SWITCH_HOLD_DURATION,
        help="Minimum seconds to hold on the current speaker before switching cameras (camera-switch mode only)",
    )
    p.add_argument(
        "--switch-blend-duration",
        type=float,
        default=SWITCH_BLEND_DURATION,
        help="Blend duration when switching speakers (0 = instant snap, 0.2 = smooth 200ms transition). Default is 0 (instant snap).",
    )
    p.add_argument(
        "--no-subs",
        action="store_true",
        help="Disable all subtitle rendering (useful if you only want the video without text)",
    )
    p.add_argument(
        "--dynamic-split",
        action="store_true",
        help="Automatically switch between full-screen (1 speaker) and split-screen (2 speakers) based on who is talking. Only active with --split-screen.",
    )
    p.add_argument(
        "--split-trigger",
        choices=["diarization", "face"],
        default="diarization",
        help="The trigger used to decide when to split the screen. 'diarization' uses audio (who is talking), 'face' uses video (how many faces are visible).",
    )


    # --- Split Screen Optimizations ---
    p.add_argument(
        "--split-zoom",
        type=float,
        default=1.0,
        help="Zoom factor for split-screen panels (e.g. 1.2, 1.5). Default is 1.0 (no zoom).",
    )
    p.add_argument(
        "--split-v-align",
        type=float,
        default=0.5,
        help="Vertical alignment for split-screen panels (0.0=top, 0.5=center, 1.0=bottom). Default is 0.5 (center).",
    )
    p.add_argument(
        "--split-auto-zoom",
        action="store_true",
        help="Automatically zoom in each split-screen panel until only one person is visible in each frame.",
    )
    p.add_argument(
        "--split-max-zoom",
        type=float,
        default=2.5,
        help="Maximum zoom factor allowed for auto-zoom (default: 2.5).",
    )


    # --- Subtitle & Typography ---
    p.add_argument(
        "--font-style",
        default=GAYA_FONT_AKTIF,
        choices=["DEFAULT", "STORYTELLER", "HORMOZI", "CINEMATIC"],
        help="Font style preset",
    )
    p.add_argument(
        "--advanced-text",
        action="store_true",
        default=USE_ADVANCED_TEXT,
        help="Enable advanced kinetic typography",
    )
    p.add_argument(
        "--advanced-text-hook",
        action="store_true",
        default=USE_ADVANCED_TEXT_ON_HOOK,
        help="Enable advanced typography on hook",
    )

    # --- Whisper ---
    p.add_argument(
        "--whisper-model", default=WHISPER_MODEL, help="Faster-Whisper model size"
    )
    p.add_argument(
        "--whisper-device",
        default=WHISPER_DEVICE,
        choices=list(VALID_DEVICES),
        help="Device for Whisper inference",
    )
    p.add_argument(
        "--whisper-compute-type",
        default=WHISPER_COMPUTE_TYPE,
        choices=list(VALID_COMPUTE_TYPES),
        help=(
            "Compute type for Whisper. 'auto' pairs float16 with a GPU and int8 "
            "with CPU; float16 on CPU is not supported and is downgraded"
        ),
    )

    # --- Gemini & Face Detection ---
    p.add_argument(
        "--face-detector",
        choices=["mediapipe", "yolo"],
        default="mediapipe",
        help="AI model for face tracking (mediapipe is CPU, yolo uses GPU if available)",
    )
    p.add_argument(
        "--yolo-size",
        choices=["8n", "8s", "8m", "8n_v2", "9c"],
        default="8m",
        help="YOLO face model version/size (8n, 8s, 8m, 8n_v2, 9c). Only active if --face-detector yolo",
    )
    p.add_argument(
        "--topic",
        default="",
        help=(
            "One line of context about the video, e.g. 'home espresso gear "
            "review'. Given to every scan window so it knows what it is "
            "looking at when judging whether a moment stands on its own. "
            "Optional; nothing is asked of a model to obtain it."
        ),
    )
    p.add_argument(
        "--ai-provider",
        choices=["chain", "openai_compat"],
        default=AI_PROVIDER,
        help=(
            "How to analyse the transcript. 'chain' (default) runs the "
            "three-pass analyzer over --llm-chain. 'openai_compat' runs the "
            "same analyzer against a single custom endpoint, built from "
            "--openai-compat-base-url and --openai-compat-model. Use 'chain' "
            "with an explicit --llm-chain to mix that endpoint with others."
        ),
    )
    p.add_argument(
        "--openai-compat-base-url",
        default=None,
        help=(
            "Base URL of the OpenAI-compatible endpoint, including the version "
            "path, e.g. https://openrouter.ai/api/v1. Defaults to "
            "$OPENAI_COMPAT_BASE_URL. Used with --ai-provider openai_compat, "
            "which becomes a one-link chain over this endpoint."
        ),
    )
    p.add_argument(
        "--openai-compat-model",
        default=None,
        help=(
            "Model id the custom endpoint expects. Defaults to "
            "$OPENAI_COMPAT_MODEL. Only used with --ai-provider openai_compat."
        ),
    )
    p.add_argument(
        "--platform",
        choices=list(PRESET_NAMES),
        default=DEFAULT_PRESET,
        help=(
            "Target platform, which sets the clip duration window: "
            "tiktok/reels 15-90s, shorts 15-59s, auto 20-75s, long 60-179s "
            "(the pipeline's previous behaviour)."
        ),
    )
    p.add_argument(
        "--output-language",
        default="auto",
        help=(
            "ISO-639-1 code for titles and captions, e.g. 'fr'. Defaults to "
            "'auto', which follows the language of the transcript. English "
            "titles, keywords and hashtags are produced either way."
        ),
    )
    p.add_argument(
        "--dry-run-analysis",
        action="store_true",
        help=(
            "Run the analysis, write gemini_response.json and "
            "metadata_preview.json, and stop before rendering. Lets an "
            "expensive analysis be inspected once before any ffmpeg work."
        ),
    )
    p.add_argument(
        "--nvidia-model",
        default=NVIDIA_MODEL,
        help="Model name for NVIDIA NIM API. See https://integrate.api.nvidia.com/v1/models",
    )
    p.add_argument("--gemini-model", default=GEMINI_MODEL, help="Gemini model name")
    p.add_argument(
        "--gemini-fallback-model",
        default=GEMINI_FALLBACK_MODEL,
        help="Gemini fallback model name if main model fails",
    )
    p.add_argument(
        "--llm-chain",
        default=LLM_CHAIN,
        help=(
            "Ordered provider chain for AI analysis. Each link is tried in "
            "order and every hop is printed; a provider not named here is "
            "never called. Defaults to $LLM_CHAIN, then to the shipped "
            f"default in clipping/providers/registry.py, currently "
            f"'{DEFAULT_LLM_CHAIN}'."
        ),
    )
    p.add_argument(
        "--karaoke-color",
        default=KARAOKE_HIGHLIGHT_COLOR,
        type=_ass_colour,
        help=(
            "Colour of the word being spoken, in ASS format &HBBGGRR& -- BGR, "
            f"not RGB. Default {KARAOKE_HIGHLIGHT_COLOR} (yellow); "
            "'&H0000FF&' is red and '&H00FF00&' is green. Only applies with "
            "karaoke subtitles on."
        ),
    )
    p.add_argument(
        "--analysis-workers",
        type=int,
        choices=[1, 2, 3],
        default=None,
        help=(
            "How many transcript windows to scan at once. Default 1. Windows "
            "are independent, so a batch CAN cost its slowest member rather "
            "than their sum -- but only if the provider actually runs them at "
            "once. NVIDIA's free tier does not, and measured slower at 2; Groq "
            "is the link where this should pay, and is untested. Capped at 3: "
            "the limit is the provider's rate limit, not this machine."
        ),
    )
    p.add_argument(
        "--no-analysis-cache",
        action="store_true",
        help=(
            "Re-scan every transcript window even if this job already has an "
            "answer for it. The cache is keyed on the window's text plus the "
            "prompt version, the chain and the clip-length preset, so a stale "
            "entry cannot be served; use this to pay for a second opinion from "
            "the same model anyway."
        ),
    )
    p.add_argument(
        "--no-preflight",
        action="store_true",
        help=(
            "Skip the liveness check that asks the provider chain an 8-token "
            "question before transcribing. The check exists because a dead "
            "provider used to be discovered only after 47 minutes of CPU "
            "Whisper; skip it if a provider is merely slow to wake."
        ),
    )
    p.add_argument(
        "--allow-slow-chain",
        action="store_true",
        help=(
            "Run even though no primary link in the chain has a key, so the "
            "whole analysis would run on the slow floor (NVIDIA NIM). Refused "
            "by default because at ~12 tokens/s behind a ~50s queue a scan "
            "that takes seconds on Groq takes tens of minutes and may not "
            "finish. Also settable as ALLOW_SLOW_CHAIN=1."
        ),
    )
    p.add_argument(
        "--stt-chain",
        default=STT_CHAIN,
        help=(
            "Ordered transcription chain, e.g. "
            "'groq/whisper-large-v3-turbo,local/faster-whisper'. "
            "'none' disables transcription (same as --no-whisper). Defaults to "
            "$STT_CHAIN, then to hosted-first with a local fallback."
        ),
    )
    p.add_argument(
        "--llm-timeout",
        type=int,
        default=LLM_TIMEOUT,
        help="Per-request timeout in seconds for chain providers (0 = provider default).",
    )
    p.add_argument(
        "--load-gemini-json",
        action="store_true",
        help="Load the saved gemini_response.json from outputs dir to bypass the AI generation step (useful for debugging)",
    )
    p.add_argument(
        "--box-face-detection",
        action="store_true",
        help="Draw a yellow bounding box around the detected face for debugging/tracking visualization",
    )
    p.add_argument(
        "--dev-mode",
        action="store_true",
        help="Enable developer visualization mode for 9:16 tracking (shows stabilization box and dimmed background)",
    )
    p.add_argument(
        "--dev-mode-with-output",
        action="store_true",
        help="Render BOTH the Dev Mode visualization AND the standard output video simultaneously.",
    )
    p.add_argument(
        "--dev-mode-with-output-merge",
        action="store_true",
        help="Render a merged side-by-side video of both Dev Mode and standard output.",
    )
    p.add_argument(
        "--track-lines",
        action="store_true",
        help="Draw crosshair tracking lines extending from the face box to the boundaries",
    )
    p.add_argument(
        "--static-crop",
        action="store_true",
        help="Disable face tracking and use static center crop for 1:1, 3:4, and 4:5 ratios",
    )

    # --- Smart Auto-Framing / Tracking ---
    p.add_argument(
        "--track-step",
        type=float,
        default=None,
        help="Face detection frequency in seconds (default: 0.25)",
    )
    p.add_argument(
        "--track-deadzone",
        type=float,
        default=None,
        help="Camera deadzone ratio (default: 0.15)",
    )
    p.add_argument(
        "--track-smooth",
        type=float,
        default=None,
        help="Camera smoothing speed (default: 0.30)",
    )
    p.add_argument(
        "--track-jitter",
        type=int,
        default=None,
        help="Pixel jitter threshold (default: 5)",
    )
    p.add_argument(
        "--track-snap",
        type=float,
        default=None,
        help="Face jump snap threshold (default: 0.25)",
    )
    p.add_argument(
        "--track-conf",
        type=float,
        default=0.55,
        help="[Experimental] Higher confidence threshold for face detection to prevent ghosts (default: 0.55)",
    )
    p.add_argument(
        "--track-smooth-window",
        type=int,
        default=12,
        help="[Experimental] Majority-vote window for layout stability (default: 12 frames)",
    )
    p.add_argument(
        "--scene-cut-threshold",
        type=int,
        default=18,
        help="[Experimental] Visibility change threshold to detect camera cuts and reset layout history (default: 18)",
    )
    p.add_argument(
        "--track-iou-threshold",
        type=float,
        default=0.2,
        help="[Experimental] Box overlap threshold to merge duplicate detections (default: 0.2)",
    )
    p.add_argument(
        "--video-bitrate",
        default="auto",
        help="Target video bitrate (e.g. 8M, 12M, auto). 'auto' scales based on resolution.",
    )
    p.add_argument(
        "--video-sharpen",
        action="store_true",
        help="Apply a subtle sharpening filter for clearer output.",
    )
    p.add_argument(
        "--video-cq",
        type=int,
        default=VIDEO_QUALITY_CQ,
        help="NVENC constant quality value (lower is sharper, bigger file).",
    )
    p.add_argument(
        "--video-crf",
        type=int,
        default=VIDEO_QUALITY_CRF,
        help="libx264 CRF value (lower is sharper, bigger file).",
    )
    p.add_argument(
        "--video-preset",
        default=VIDEO_PRESET,
        help="Override encoder preset for NVENC/libx264, or 'auto' to keep defaults.",
    )
    p.add_argument(
        "--video-scale-algo",
        choices=["lanczos", "bicubic", "bilinear", "area"],
        default=VIDEO_SCALE_ALGO,
        help="Resize algorithm for OpenCV scaling steps during rendering.",
    )

    # --- Hook V2 & Segment Trimming ---
    hook_v2_group = p.add_argument_group("Hook V2 & Segment Trimming")
    hook_v2_group.add_argument(
        "--hook-v2",
        action="store_true",
        default=False,
        help="Enable Multi-Hook Intro V2 mode (3-4 micro-hook clips with flash/glitch transitions).",
    )
    hook_v2_group.add_argument(
        "--hook-v2-items",
        type=int,
        default=3,
        help="Number of micro-hooks to generate in V2 mode.",
    )
    hook_v2_group.add_argument(
        "--hook-v2-style",
        default="controversial_fast_glitch",
        help="Style prompt hint for AI to pick the hook style.",
    )
    hook_v2_group.add_argument(
        "--white-flash-duration",
        type=float,
        default=0.12,
        help="Duration of white flash transition between hooks (seconds).",
    )
    hook_v2_group.add_argument(
        "--no-segment-trim",
        action="store_true",
        default=False,
        help="Disable AI segment trimming (render full start-to-end instead of keep_segments).",
    )
    hook_v2_group.add_argument(
        "--silence-trim",
        action="store_true",
        default=False,
        help="Instruct AI to aggressively trim silence/dead air from clips.",
    )

    # --- Story Clip Mode ---
    story_group = p.add_argument_group("Story Clip Mode")
    story_group.add_argument(
        "--story-mode",
        action="store_true",
        default=False,
        help="Enable Story Clip mode: assemble clips from multiple video sources using a JSON recipe.",
    )
    story_group.add_argument(
        "--story-recipe",
        default="story_recipe.json",
        help="Path to the story recipe JSON file.",
    )
    story_group.add_argument(
        "--sources-json",
        default="sources.json",
        help="Path to the sources registry JSON file.",
    )
    story_group.add_argument(
        "--story-output-dir",
        default=None,
        help="Output directory for story clips (default: outputs/story_clips).",
    )

    # --- Voice-Over Commentary Pipeline ---
    vo_group = p.add_argument_group("Voice-Over Commentary (TTS)")
    vo_group.add_argument(
        "--voiceover",
        action="store_true",
        default=False,
        help="Enable AI voice-over commentary mode using Gemini and edge-tts.",
    )
    vo_group.add_argument(
        "--voiceover-voice",
        default="en-GB-MaisieNeural",
        help="TTS voice for edge-tts (e.g. id-ID-ArdiNeural, en-US-AvaNeural).",
    )
    vo_group.add_argument(
        "--voiceover-lang",
        choices=["id", "en"],
        default="en",
        help="Language for the commentary script generation.",
    )
    vo_group.add_argument(
        "--voiceover-style",
        choices=["analysis", "reaction", "lesson", "summary"],
        default="analysis",
        help="Style of the generated commentary.",
    )
    vo_group.add_argument(
        "--voiceover-length",
        choices=["short", "normal", "long"],
        default="short",
        help="Length of the generated commentary (short: ~10s, normal: ~30s, long: ~50s).",
    )
    vo_group.add_argument(
        "--voiceover-volume",
        type=float,
        default=1.0,
        help="Volume of the voice-over audio (0.0 to 1.0+).",
    )
    vo_group.add_argument(
        "--original-volume",
        type=float,
        default=0.15,
        help="Volume of the original video audio when voice-over is active.",
    )
    vo_group.add_argument(
        "--edge-glow",
        action="store_true",
        default=False,
        help="Enable ambient edge glow effect on the entire clip (hook, clip, broll, voiceover). Without this flag, glow only appears on voice-over intro.",
    )
    vo_group.add_argument(
        "--edge-glow-mode",
        choices=["default", "smooth", "full"],
        default="smooth",
        help=(
            "Edge glow rendering strategy. "
            "'default': 10s loop (original, may stutter at loop point). "
            "'smooth': 10s loop with auto-adjusted speed for seamless looping. "
            "'full': render glow for the full video duration (no loop needed, "
            "heavier but zero stutter)."
        ),
    )

    # --- Watermark ---
    wm_group = p.add_argument_group("Watermark")
    wm_group.add_argument(
        "--watermark",
        action="store_true",
        default=False,
        help="Enable watermark overlay on rendered clips.",
    )
    wm_group.add_argument(
        "--text",
        default=None,
        help="Watermark text to overlay (e.g. 'Channel Name').",
    )
    wm_group.add_argument(
        "--image",
        default=None,
        help="Path to watermark image file (supports PNG, JPG, JPEG, WEBP). PNG with transparency recommended.",
    )
    wm_group.add_argument(
        "--opacity",
        type=int,
        default=70,
        help="Watermark opacity in percent (1-100). Default: 70.",
    )
    wm_group.add_argument(
        "--position",
        default="center-right",
        choices=[
            "top-left", "top-center", "top-right",
            "center-left", "center", "center-right",
            "bottom-left", "bottom-center", "bottom-right",
        ],
        help="Watermark position on the video frame.",
    )
    wm_group.add_argument(
        "--padding",
        type=int,
        default=0,
        help="Watermark padding from the nearest edge in pixels.",
    )
    wm_group.add_argument(
        "--watermark-font-size",
        type=int,
        default=0,
        help="Watermark font size in pixels. 0 = auto (3%% of frame height).",
    )
    wm_group.add_argument(
        "--watermark-scale",
        type=int,
        default=15,
        help="Watermark image height as %% of frame height (1-100). Default: 15.",
    )

    return p


# Which env var backs which provider. Used by the early key gates in main.py
# and the web worker so a missing key fails in 40ms rather than after ingestion
# and transcription have already run.
PROVIDER_KEYS = {
    "nvidia": ("api_key_nvidia", "NVIDIA_API_KEY"),
    "gemini": ("api_key_gemini", "GOOGLE_API_KEY"),
    "groq": ("api_key_groq", "GROQ_API_KEY"),
    "openrouter": ("api_key_openrouter", "OPENROUTER_API_KEY"),
    "mistral": ("api_key_mistral", "MISTRAL_API_KEY"),
    "custom": ("api_key_custom", "LLM_CUSTOM_API_KEY"),
    # The legacy single-request path's own custom endpoint, kept alongside the
    # chain's "custom" link rather than folded into it: they are reached by
    # different --ai-provider values and configured by different env vars, and
    # collapsing them would have silently changed the meaning of an existing
    # OPENAI_COMPAT_* setup. See DEC-046.
    "openai_compat": ("api_key_openai_compat", "OPENAI_COMPAT_API_KEY"),
}

# Settings that are not keys but that a provider still cannot run without.
# Same (attr, ENV_NAME) shape as PROVIDER_KEYS so the gate's return type and
# every one of its callers stay unchanged.
PROVIDER_REQUIRED_EXTRA = {
    "openai_compat": (
        ("openai_compat_base_url", "OPENAI_COMPAT_BASE_URL"),
        ("openai_compat_model", "OPENAI_COMPAT_MODEL"),
    ),
}


def provider_keys(cfg) -> dict:
    """``{provider_name: api_key}`` for every provider that has one.

    This is what the chain runner is handed. A provider with no key is skipped
    with a printed line rather than failing the run, so a partially-configured
    chain degrades to the providers actually set up.
    """
    keys = {}
    for name, (attr, _env) in PROVIDER_KEYS.items():
        value = getattr(cfg, attr, "") or ""
        if value:
            keys[name] = value
    return keys


def missing_provider_key(cfg) -> tuple[str, str] | None:
    """Return ``(attr, ENV_NAME)`` when the active provider has no key, else None.

    For a chain, "has no key" means *no link at all* has one: a chain whose
    first provider is unconfigured is fine, because that link is skipped with a
    printed reason and the next one answers. Failing there would make adding a
    second provider to the chain a downgrade.
    """
    provider = getattr(cfg, "ai_provider", AI_PROVIDER)

    if provider in ("chain", "auto"):
        from clipping.providers.registry import chain_from_env, parse_chain

        spec = getattr(cfg, "llm_chain", "") or ""
        try:
            chain = parse_chain(spec) if spec else chain_from_env()
        except Exception:  # noqa: BLE001 - a bad chain is reported when it runs
            return None
        available = provider_keys(cfg)
        if any(link.provider in available for link in chain):
            return None
        first = chain[0].provider if chain else "groq"
        return PROVIDER_KEYS.get(first, PROVIDER_KEYS["groq"])

    # A half-configured custom endpoint counts as missing: a base URL with no
    # model name fails just as surely as an absent key, and it should fail just
    # as early -- before ingestion and transcription have run.
    required = (PROVIDER_KEYS.get(provider, PROVIDER_KEYS["nvidia"]),)
    required += PROVIDER_REQUIRED_EXTRA.get(provider, ())

    for attr, env_name in required:
        if not getattr(cfg, attr, ""):
            return attr, env_name
    return None


ChainReadiness = namedtuple("ChainReadiness", "ready message missing keyed_slow")
# missing:    [(link, ENV_NAME, signup_url), ...] -- named primary links with no key
# keyed_slow: [link, ...] -- the non-primary links that DO have a key

READY = ChainReadiness(True, "", [], [])

CLI_SLOW_CHAIN_HINT = "or run anyway with --allow-slow-chain."
WEB_SLOW_CHAIN_HINT = (
    'or turn on "Run on the slow chain anyway" in Settings.'
)


def chain_readiness(chain, keys, *, ai_provider="chain", allow_slow=False,
                    hint=CLI_SLOW_CHAIN_HINT) -> ChainReadiness:
    """Whether a chain job may START, from loose values.

    Pure -- no cfg, no filesystem, no network -- because the job-creation route
    must answer before a job exists, and building a config creates the job's
    output directory.

    Refuses exactly one case: the chain names at least one *primary* provider
    (registry ``Provider.primary``), none of the named primaries has a key, and
    a non-primary link does. That is the job that failed on 2026-09-23 -- a
    three-link default chain with only ``NVIDIA_API_KEY`` set, which is a chain
    of one running on the floor.

    Deliberately NOT refused, each for a reason:

    * a non-chain provider -- the escape hatch, as in ``preflight_chain``;
    * ``allow_slow`` -- the user said so;
    * an unparseable chain -- it is reported when it runs;
    * no key at all -- ``missing_provider_key`` owns that case and says it
      better; the handoff is pinned by a test that checks both;
    * a chain that names no primary (``LLM_CHAIN=nvidia/...``) -- the user wrote
      that list down and DEC-023 forbids second-guessing it.

    The chain itself is never edited, reordered or trimmed (DEC-003, DEC-023).
    This refuses to start; it does not change what would run.
    """
    if ai_provider not in ("chain", "auto") or allow_slow:
        return READY

    from clipping.providers.registry import (
        PROVIDERS,
        chain_from_env,
        describe,
        is_primary,
        parse_chain,
    )

    try:
        links = parse_chain(chain) if chain else chain_from_env()
    except Exception:  # noqa: BLE001 - a bad chain is reported when it runs
        return READY

    keys = keys or {}
    if not any(keys.get(link.provider) for link in links):
        return READY

    primaries = [link for link in links if is_primary(link)]
    if not primaries:
        return READY
    if any(keys.get(link.provider) for link in primaries):
        return READY

    keyed_slow = [link for link in links if keys.get(link.provider)]
    missing = [
        (link, PROVIDERS[link.provider].env_key, PROVIDERS[link.provider].signup_url)
        for link in primaries
    ]
    return ChainReadiness(
        False, _slow_chain_message(keyed_slow, missing, hint, describe),
        missing, keyed_slow,
    )


def _slow_chain_message(keyed_slow, missing, hint, describe) -> str:
    """The refusal, built from the registry so it carries no model literal."""
    running = ", ".join(describe(link) for link in keyed_slow)
    alone = "alone" if len(keyed_slow) == 1 else "and nothing faster"

    from clipping.providers.registry import PROVIDERS

    n = len(missing)
    # "Free" only for a hosted link whose default model costs nothing
    # (Provider.free_tier). OpenRouter's default is billed, and a message that
    # promised otherwise would send someone to add a card they did not expect.
    is_free = [bool(url) and PROVIDERS[link.provider].free_tier
               for link, _, url in missing]
    free, some_free = all(is_free), sum(is_free)
    if n == 1:
        head = "One link in your chain has no key" + (", and it is free" if free else "")
        ask = "Set it"
    else:
        amount = "Two" if n == 2 else str(n)
        both = "both" if n == 2 else "all"
        if free:
            head = f"{amount} links in your chain have no key, and {both} are free"
        elif some_free:
            head = (f"{amount} links in your chain have no key; {some_free} of "
                    f"them {'is' if some_free == 1 else 'are'} free")
        else:
            head = f"{amount} links in your chain have no key"
        ask = "Set any one of them"

    labels = [describe(link) for link, _, _ in missing]
    width_label = max(len(label) for label in labels)
    width_env = max(len(env) for _, env, _ in missing)
    rows = "\n".join(
        f"  {label.ljust(width_label)}  {env.ljust(width_env)}  "
        f"{url or '(your own endpoint)'}"
        f"{'  (paid)' if url and not free_link else ''}"
        for label, (_, env, url), free_link in zip(labels, missing, is_free)
    )
    return (
        f"This job would run on {running} {alone}, and that is the chain's "
        "floor, not a primary. Measured: ~12 tokens/s behind a ~50s queue, so a "
        "scan that takes seconds on a primary link takes tens of minutes here "
        "and may not finish inside the time budget at all.\n\n"
        f"{head}:\n{rows}\n\n{ask}, {hint}"
    )


def chain_not_ready(cfg, hint=CLI_SLOW_CHAIN_HINT) -> str | None:
    """``chain_readiness`` for a built config: the refusal message, or None."""
    readiness = chain_readiness(
        getattr(cfg, "llm_chain", "") or "",
        provider_keys(cfg),
        ai_provider=getattr(cfg, "ai_provider", AI_PROVIDER),
        allow_slow=bool(getattr(cfg, "allow_slow_chain", False)),
        hint=hint,
    )
    return None if readiness.ready else readiness.message


def preflight_chain(cfg, on_log=print, **probe_kwargs) -> str | None:
    """Ask the chain whether anything answers, before the expensive work starts.

    Returns ``None`` when at least one link replied (or when the check does not
    apply), and an explanatory message when nothing did.

    ``missing_provider_key`` above already refuses a chain where *no* link has a
    key. It cannot refuse the case that actually happened: one link had a key,
    the gate passed, 47 minutes of CPU Whisper ran, and only then did the
    analysis discover that the one keyed provider answered nothing at all. A
    key proves a provider was configured, not that it is alive.

    Only the chain path is checked. The legacy single-provider paths have their
    own behaviour and are an escape hatch, not somewhere to add a new gate.

    A link that fails the probe is **not** removed from the chain -- DEC-003 and
    DEC-023 forbid editing a list the user wrote down. It is only reported.
    """
    if not getattr(cfg, "preflight", True):
        return None
    if getattr(cfg, "ai_provider", AI_PROVIDER) not in ("chain", "auto"):
        return None

    from clipping.providers import llm as llm_mod
    from clipping.providers.registry import chain_from_env, parse_chain

    spec = getattr(cfg, "llm_chain", "") or ""
    try:
        chain = parse_chain(spec) if spec else chain_from_env()
    except Exception:  # noqa: BLE001 - a bad chain is reported when it runs
        return None

    keys = provider_keys(cfg)
    if not any(link.provider in keys for link in chain):
        return None  # missing_provider_key owns this case and says it better

    work, seed = _preflight_work(cfg, chain)
    if work is None:
        on_log("   🔎 Checking the provider chain answers before transcribing...")
    else:
        on_log("   🔎 Asking the provider chain a real analysis request...")

    live, results, value = llm_mod.probe_chain(
        chain, keys, on_log=on_log, work=work, **probe_kwargs
    )
    if live is None:
        return llm_mod.preflight_message(results)

    # The answer was paid for; keeping it means pass A hits it instead of
    # asking the same question again a few minutes later.
    if seed is not None and value is not None:
        seed(value, on_log)
    return None


def _preflight_work(cfg, chain):
    """``(work, seed)`` for a real probe, or ``(None, None)`` for the ping.

    A real request needs a transcript, and on the Whisper path there is not one
    yet — which is the whole point of running before transcription. So this
    returns work only when the transcript is already on disk: the user supplied
    one, or DEC-022's saved ``transcript.vtt`` is sitting in the job's own
    output directory from an earlier run.

    Every failure here falls back to the ping. A preflight check is not allowed
    to fail a job that would otherwise have run.
    """
    path = str(getattr(cfg, "transcript_path", "") or "")
    outputs_dir = str(getattr(cfg, "outputs_dir", "") or "")
    if not path and outputs_dir:
        from clipping.transcript import SAVED_TRANSCRIPT_NAME

        saved = os.path.join(outputs_dir, SAVED_TRANSCRIPT_NAME)
        if os.path.isfile(saved):
            path = saved
    if not path or not os.path.isfile(path):
        return None, None

    try:
        from clipping.analysis import analyzer, beats as beats_mod
        from clipping.analysis import cache as cache_mod
        from clipping.analysis import presets as presets_mod
        from clipping.analysis import diagnostic
        from clipping.transcript import load_transcript

        _text, data_segmen = load_transcript(
            path,
            max_words_per_subtitle=int(getattr(cfg, "max_kata_per_subtitle", 5) or 5),
            offset=float(getattr(cfg, "transcript_offset", 0.0) or 0.0),
            dedupe=bool(getattr(cfg, "transcript_dedupe", True)),
        )
        all_beats = beats_mod.build_beats(data_segmen)
        if not all_beats:
            return None, None

        ranges = beats_mod.windows(
            all_beats,
            size=analyzer.WINDOW_BEATS,
            overlap=analyzer.WINDOW_OVERLAP,
        )
        if not ranges:
            return None, None

        lo, hi = ranges[0]
        beats_text = beats_mod.render_beats(all_beats, lo, hi)
        preset = presets_mod.get(getattr(cfg, "platform", presets_mod.DEFAULT_PRESET))
        # One builder for every pass-A request that is not the scan itself, so
        # the preflight, the bench and the settings test cannot drift apart.
        work = diagnostic.pass_a_work(
            beats_text,
            preset=preset,
            language=_probe_language(cfg),
            total_seconds=all_beats[-1]["end"],
            topic=str(getattr(cfg, "topic", "") or "").strip(),
            max_candidates=analyzer.MAX_CANDIDATES_PER_WINDOW,
        )
    except Exception:  # noqa: BLE001 - the ping is always available
        return None, None

    def seed(value, on_log):
        try:
            store = analyzer._build_cache(cfg, chain, preset)
            if store is None:
                return
            found = analyzer._clean_candidates(value, lo, hi)
            store.put(beats_text, found)
            if store.save():
                on_log(
                    f"   💾 Kept the probe's {len(found)} candidate(s); the scan "
                    f"will not ask for window 1 again."
                )
        except Exception:  # noqa: BLE001 - a cache is never a gate
            pass

    return work, seed


def _probe_language(cfg):
    """The output language, when it was set explicitly. Never detected here.

    Detection needs the transcript read and scored, and the probe's prompt only
    uses this for one line of context. ``None`` simply omits that line.
    """
    explicit = str(getattr(cfg, "output_language", "") or "").strip().lower()
    return explicit if explicit and explicit != "auto" else None


def apply_openai_compat_alias(cfg, *, on_log=None):
    """Turn ``--ai-provider openai_compat`` into a one-link ``custom/`` chain.

    DEC-046 kept a second custom-endpoint path so that collapsing the two could
    not silently change the meaning of an existing ``OPENAI_COMPAT_*`` setup.
    The path it protected — one request for 22 fields — is gone, but the
    reasoning still holds for the *surface*: three environment variables, three
    Settings fields and a fail-fast gate that all still work.

    So the setting is preserved and re-expressed. This runs at config time over
    values the user wrote down, and prints what it built; it is not the runtime
    chain-editing DEC-003 and DEC-023 forbid.

    The environment write is forced by ``registry.provider_for``, which resolves
    ``custom``'s base URL from ``LLM_CUSTOM_BASE_URL``. ``setdefault`` means an
    explicit setting always wins: someone who set the chain's own variable
    meant it.
    """
    if getattr(cfg, "ai_provider", "") != "openai_compat":
        return cfg

    model = str(getattr(cfg, "openai_compat_model", "") or "").strip()
    base_url = str(getattr(cfg, "openai_compat_base_url", "") or "").strip()
    if not model or not base_url:
        # missing_provider_key reports this properly, and fails fast. Leaving
        # the provider as it is keeps that message rather than replacing it
        # with a confusing one about an empty chain.
        return cfg

    if base_url:
        os.environ.setdefault("LLM_CUSTOM_BASE_URL", base_url)
    cfg.llm_chain = f"custom/{model}"
    cfg.api_key_custom = getattr(cfg, "api_key_openai_compat", "") or ""
    cfg.ai_provider = "chain"
    if on_log is not None:
        on_log(f"   ↪ --ai-provider openai_compat → chain: custom/{model}")
    return cfg


def build_config(argv: list[str] | None = None) -> SimpleNamespace:
    """Parse CLI args and merge with defaults into a config namespace."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Validate local inputs. These checks live here, beside the --image check
    # below, so a typo fails in ~40ms instead of after a model load or a render.
    if args.transcript and not args.video:
        parser.error("--transcript requires --video (a transcript without a video cannot be rendered).")

    if not args.story_mode and not args.video:
        parser.error("--video is required unless --story-mode is used.")

    if args.video:
        if not os.path.isfile(args.video):
            parser.error(f"Video file not found: {args.video}")
        valid_video_exts = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts", ".flv", ".m4v")
        if not args.video.lower().endswith(valid_video_exts):
            parser.error(
                f"Unsupported video extension: {args.video}. "
                f"Supported formats: {', '.join(valid_video_exts)}"
            )

    if args.transcript:
        if not os.path.isfile(args.transcript):
            parser.error(f"Transcript file not found: {args.transcript}")
        valid_transcript_exts = (".vtt", ".srt", ".json3", ".json")
        if not args.transcript.lower().endswith(valid_transcript_exts):
            parser.error(
                f"Unsupported transcript format: {args.transcript}. "
                f"Supported formats: {', '.join(valid_transcript_exts)}"
            )

    if args.no_whisper and not args.transcript:
        parser.error("--no-whisper requires --transcript.")

    # Validate watermark args
    if args.watermark:
        if not args.text and not args.image:
            parser.error("--watermark requires --text or --image.")
        if not (1 <= args.opacity <= 100):
            parser.error(f"--opacity must be between 1-100, given: {args.opacity}")
        if args.padding < 0:
            parser.error(f"--padding cannot be negative, given: {args.padding}")
        if args.watermark_font_size < 0:
            parser.error(f"--watermark-font-size cannot be negative, given: {args.watermark_font_size}")
        if not (1 <= args.watermark_scale <= 100):
            parser.error(f"--watermark-scale must be between 1-100, given: {args.watermark_scale}")
        if args.image:
            if not os.path.exists(args.image):
                parser.error(f"Watermark image file not found: {args.image}")
            valid_exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff")
            if not args.image.lower().endswith(valid_exts):
                parser.error(
                    f"Unsupported watermark image format: {args.image}. "
                    f"Supported formats: {', '.join(valid_exts)}"
                )

    base_dir = os.getcwd()
    outputs_dir = os.path.abspath(os.path.join(base_dir, "outputs"))
    os.makedirs(outputs_dir, exist_ok=True)
    font_dir = os.path.abspath(os.path.join(base_dir, "custom_fonts"))
    os.makedirs(font_dir, exist_ok=True)

    cfg = SimpleNamespace(
        # Paths
        base_dir=base_dir,
        outputs_dir=outputs_dir,
        font_dir=font_dir,
        # Local-first: --video IS the source of truth. Overriding this field
        # rather than adding a parallel one means the whole render layer
        # (runner, studio, diarization) becomes local-first for free, since it
        # already reads cfg.file_video_asli everywhere.
        file_video_asli=(
            os.path.abspath(args.video)
            if args.video
            else os.path.abspath(os.path.join(base_dir, "video_asli.mp4"))
        ),
        video_provided=bool(args.video),
        transcript_path=os.path.abspath(args.transcript) if args.transcript else None,
        transcript_offset=args.transcript_offset,
        no_whisper=args.no_whisper,
        source_url=args.source_url,
        file_font_thumbnail=os.path.abspath(
            os.path.join(base_dir, NAMA_FONT_THUMBNAIL)
        ),
        file_mediapipe_model=os.path.abspath(
            os.path.join(base_dir, "blaze_face_full_range.tflite")
        ),
        # YOLO configs
        face_detector=args.face_detector,
        yolo_size=args.yolo_size,
        url_yolo_model=f"https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov{args.yolo_size}.pt",
        file_yolo_model=os.path.abspath(
            os.path.join(base_dir, f"face_yolov{args.yolo_size}.pt")
        ),
        # API keys (from env)
        api_key_gemini=os.environ.get("GOOGLE_API_KEY", ""),
        hf_token=os.environ.get("HF_TOKEN", ""),
        pexels_api_key=os.environ.get("PEXELS_API_KEY", ""),
        # Main settings
        jumlah_clip=args.clips,
        pilihan_rasio=args.ratio,
        render_output_height=args.render_height,
        # Content & Hook
        max_kata_per_subtitle=args.words_per_sub,
        durasi_hook=args.hook_duration,
        hook_source=args.hook_source,
        hook_source_start=args.hook_source_start,
        # Hook V2 & Segment Trimming
        hook_v2=args.hook_v2,
        hook_v2_items=args.hook_v2_items,
        hook_v2_style=args.hook_v2_style,
        white_flash_duration=args.white_flash_duration,
        no_segment_trim=args.no_segment_trim,
        silence_trim=args.silence_trim,
        use_broll=not args.no_broll,
        use_hook_glitch=(args.hook_glitch and not args.no_hook),
        use_auto_bgm=not args.no_bgm,
        use_karaoke_effect=not args.no_karaoke,
        use_split_screen=args.split_screen,
        use_dynamic_split=args.dynamic_split,
        split_trigger=args.split_trigger,
        use_camera_switch=args.camera_switch,
        diarization_num_speakers=args.diarization_speakers,
        switch_hold_duration=args.switch_hold_duration,
        switch_blend_duration=args.switch_blend_duration,
        split_zoom=args.split_zoom,
        split_v_align=args.split_v_align,
        split_auto_zoom=args.split_auto_zoom,
        split_max_zoom=args.split_max_zoom,
        # Subtitle & Typography
        no_subs=args.no_subs,
        gaya_font_aktif=args.font_style,
        daftar_font=DAFTAR_FONT,
        use_advanced_text=args.advanced_text,
        use_advanced_text_on_hook=args.advanced_text_hook,
        # ASS position values
        ass_align_916=ASS_ALIGN_916,
        ass_margin_916=ASS_MARGIN_916,
        ass_font_916=ASS_FONT_916,
        scale_kata_khusus_916=SCALE_KATA_KHUSUS_916,
        ass_align_169=ASS_ALIGN_169,
        ass_margin_169=ASS_MARGIN_169,
        ass_font_169=ASS_FONT_169,
        scale_kata_khusus_169=SCALE_KATA_KHUSUS_169,
        warna_kata_khusus=WARNA_KATA_KHUSUS,
        karaoke_color=args.karaoke_color,
        karaoke_base_color=KARAOKE_BASE_COLOR,
        # Asset URLs
        url_font_thumbnail=URL_FONT_THUMBNAIL,
        url_glitch_video=URL_GLITCH_VIDEO,
        url_mediapipe_model=URL_MEDIAPIPE_MODEL,
        # BGM
        bgm_base_volume=BGM_BASE_VOLUME,
        bgm_mode=args.bgm_mode,
        bgm_moods=BGM_MOODS,
        bgm_dir=BGM_DIR,
        # Whisper
        whisper_model=args.whisper_model,
        whisper_device=args.whisper_device,
        whisper_compute_type=args.whisper_compute_type,
        # AI
        ai_provider=args.ai_provider,
        platform=args.platform,
        topic=args.topic,
        analysis_cache=not args.no_analysis_cache,
        analysis_workers=args.analysis_workers,
        output_language=args.output_language,
        dry_run_analysis=args.dry_run_analysis,
        api_key_nvidia=os.environ.get("NVIDIA_API_KEY", ""),
        # Chain providers. Read here rather than inside the provider layer so
        # every key in the process comes from one place and the web adapter can
        # override them per job exactly as it already does for the other two.
        api_key_groq=os.environ.get("GROQ_API_KEY", ""),
        api_key_openrouter=os.environ.get("OPENROUTER_API_KEY", ""),
        api_key_mistral=os.environ.get("MISTRAL_API_KEY", ""),
        api_key_custom=os.environ.get("LLM_CUSTOM_API_KEY", ""),
        llm_chain=args.llm_chain,
        llm_timeout=args.llm_timeout,
        preflight=not args.no_preflight,
        allow_slow_chain=args.allow_slow_chain or ALLOW_SLOW_CHAIN,
        stt_chain=args.stt_chain,
        # Filled in by a hosted transcription provider that reports what it
        # heard; beats guessing the language from stopwords afterwards.
        detected_language="",
        nvidia_model=args.nvidia_model,
        # A flag wins over the environment; the environment is read here rather
        # than at import so monkeypatched values still apply.
        api_key_openai_compat=os.environ.get("OPENAI_COMPAT_API_KEY", ""),
        openai_compat_base_url=(
            args.openai_compat_base_url or os.environ.get("OPENAI_COMPAT_BASE_URL", "")
        ),
        openai_compat_model=(
            args.openai_compat_model or os.environ.get("OPENAI_COMPAT_MODEL", "")
        ),
        gemini_model=args.gemini_model,
        gemini_fallback_model=args.gemini_fallback_model,
        load_gemini_json=args.load_gemini_json,
        # Tracking Tuning
        track_step=args.track_step,
        track_deadzone=args.track_deadzone,
        track_smooth=args.track_smooth,
        track_jitter=args.track_jitter,
        track_snap=args.track_snap,
        track_conf=args.track_conf,
        track_smooth_window=args.track_smooth_window,
        scene_cut_threshold=args.scene_cut_threshold,
        track_iou_threshold=args.track_iou_threshold,
        video_quality_cq=args.video_cq,
        video_quality_crf=args.video_crf,
        video_bitrate=args.video_bitrate,
        video_sharpen=args.video_sharpen,
        video_preset=args.video_preset,
        video_scale_algo=args.video_scale_algo,
        box_face_detection=args.box_face_detection,
        dev_mode=args.dev_mode,
        dev_mode_with_output=args.dev_mode_with_output,
        dev_mode_with_output_merge=args.dev_mode_with_output_merge,
        track_lines=args.track_lines,
        static_crop=args.static_crop,
        # Story Clip Mode
        story_mode=args.story_mode,
        story_recipe_path=os.path.abspath(args.story_recipe) if args.story_recipe else None,
        sources_json_path=os.path.abspath(args.sources_json) if args.sources_json else None,
        story_output_dir=(
            os.path.abspath(args.story_output_dir)
            if args.story_output_dir
            else os.path.join(outputs_dir, "story_clips")
        ),
        # Voice-Over Commentary
        voiceover=args.voiceover,
        voiceover_voice=args.voiceover_voice,
        voiceover_lang=args.voiceover_lang,
        voiceover_style=args.voiceover_style,
        voiceover_length=args.voiceover_length,
        voiceover_volume=args.voiceover_volume,
        original_volume=args.original_volume,
        edge_glow=args.edge_glow,
        edge_glow_mode=args.edge_glow_mode,
        # Watermark
        watermark_enabled=args.watermark,
        watermark_text=args.text,
        watermark_image=args.image,
        watermark_opacity=args.opacity,
        watermark_position=args.position,
        watermark_padding=args.padding,
        watermark_font_size=args.watermark_font_size,
        watermark_scale=args.watermark_scale,
    )

    return apply_openai_compat_alias(cfg, on_log=print)
