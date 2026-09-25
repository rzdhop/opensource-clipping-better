"""
clipping.engine — Transcription & AI Analysis

The download layer that used to live here is gone: this pipeline is local-first
and acquires nothing. Media and transcripts arrive as local paths via --video
and --transcript; see clipping.transcript for parsing.
"""

import json
import os
import re
import time

# Transcript parsing lives in clipping.transcript (stdlib-only, so a --transcript
# run never touches the ML stack). Re-exported here because callers reach for
# engine.load_transcript alongside engine.transcribe_video.
from .transcript import (  # noqa: F401
    TranscriptParseError,
    load_transcript,
    parse_vtt_subs,
    parse_youtube_json3_subs,
)

# NOTE: faster_whisper is imported lazily inside transcribe_video(). Importing it
# here would drag CTranslate2/cuDNN into every `import clipping.engine`, which
# defeats the --transcript bypass and makes the test suite require a GPU stack.


# ==============================================================================
# STAGE 2: WHISPER TRANSCRIPTION & JSON3 FALLBACK
# ==============================================================================

# Measured on this project's own CPU path: a 1211s video took ~93 minutes on
# cpu/int8 with large-v3 at beam_size=5, i.e. ~4.6x realtime. It is a rough
# guide, not a promise -- it scales with the host's cores -- but the order of
# magnitude is the part that matters to someone deciding whether to wait.
CPU_WHISPER_REALTIME_FACTOR = 4.6

# Below this there is nothing worth warning about, and a notice on every short
# clip would just be noise.
CPU_WHISPER_WARN_THRESHOLD_SECONDS = 10 * 60


def estimate_cpu_transcription_seconds(audio_seconds: float) -> float:
    """Rough wall-clock estimate for transcribing *audio_seconds* on CPU."""
    return audio_seconds * CPU_WHISPER_REALTIME_FACTOR


def _warn_if_cpu_transcription_will_be_slow(audio_seconds: float, device: str) -> str | None:
    """Return the warning for a slow CPU run, or None. Pure, so it is testable.

    This exists because a real job spent 94 minutes here before anyone could
    tell it was going to. The engine already skips Whisper entirely when a
    transcript is supplied, which is the actual remedy -- so the warning names
    it.
    """
    if device != "cpu":
        return None
    estimate = estimate_cpu_transcription_seconds(audio_seconds)
    if estimate < CPU_WHISPER_WARN_THRESHOLD_SECONDS:
        return None
    return (
        f"      \u26a0\ufe0f No GPU: transcribing {audio_seconds / 60:.0f} minutes of audio on "
        f"CPU takes roughly {estimate / 60:.0f} minutes. Supplying a transcript "
        f"(.vtt) with the video skips this step entirely."
    )


def load_whisper_model(
    model_size: str = "large-v3",
    device: str = "auto",
    compute_type: str = "auto",
):
    """Build a Faster-Whisper model.

    Split out so callers that transcribe several files (Story Clip assembly) can build the
    model once. large-v3 costs ~30s and several GB to load, and it was previously
    rebuilt on every transcribe_video call.

    Device and compute type are resolved here rather than at each call site: the
    CLI, Story Clip assembly and the web worker all funnel through this function, and
    resolving in one place is what stops the three of them drifting apart.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RuntimeError(
            "faster-whisper is not installed, so in-process transcription cannot "
            "run. Install it with `pip install faster-whisper`, or run "
            "with --transcript <file.vtt> to skip Whisper entirely."
        ) from exc

    from clipping.device import resolve_whisper_runtime

    device, compute_type = resolve_whisper_runtime(device, compute_type)

    print(
        f"      ⏳ Loading Whisper model '{model_size}' ({device}/{compute_type})"
        " — the first download may take a while...",
        flush=True,
    )
    try:
        return WhisperModel(model_size, device=device, compute_type=compute_type)
    except Exception as exc:
        # CTranslate2 raises a bare ValueError naming neither the flag to change
        # nor the alternative, which is how this cost the user two failed runs.
        raise RuntimeError(
            f"Whisper could not start on {device}/{compute_type}: {exc}\n"
            "  If this mentions CUDA, the installed CTranslate2 is a CPU-only "
            "build. Run with --whisper-device cpu --whisper-compute-type int8, "
            "or skip Whisper entirely with --transcript <file.vtt>."
        ) from exc


def transcribe_video(
    video_path: str,
    max_words_per_subtitle: int = 5,
    model_size: str = "large-v3",
    device: str = "auto",
    compute_type: str = "auto",
    model=None,
    cancel=None,
) -> tuple[str, list[dict]]:
    """
    Transcribe *video_path* using Faster-Whisper.

    Returns
    -------
    transkrip_lengkap : str
        Human-readable transcript with timestamps.
    data_segmen : list[dict]
        Word-level segments grouped by *max_words_per_subtitle*.

    Notes
    -----
    Pass *model* to reuse an already-loaded WhisperModel across several files;
    otherwise one is built from *model_size*/*device*/*compute_type*.

    *cancel* stops it between segments. Loading the model and decoding the
    first window cannot be interrupted.
    """
    print("[2/3] Starting transcription with Faster-Whisper (Word-Level)...")

    # Faster-whisper produces no output until the first segment, so each phase is
    # announced -- otherwise a first CPU run (model download + full audio decode)
    # looks like a hang.
    # Resolved once here, not twice. resolve_whisper_runtime prints a warning
    # when it has to fall back from CUDA, and it is idempotent, so passing the
    # resolved pair down means load_whisper_model re-resolves to the same answer
    # silently instead of repeating the warning into the activity feed.
    from clipping.device import resolve_whisper_runtime

    resolved_device, resolved_compute = resolve_whisper_runtime(device, compute_type)

    if model is None:
        model = load_whisper_model(model_size, resolved_device, resolved_compute)

    print("      ⏳ Decoding audio & extracting features (no output yet)...", flush=True)
    segments, info = model.transcribe(video_path, beam_size=5, word_timestamps=True)

    transkrip_lengkap = ""
    data_segmen: list[dict] = []

    # Progress bar based on audio timestamp. faster-whisper streams segments
    # lazily, so the bar advances to each segment's end time as it arrives.
    from tqdm import tqdm

    total_dur = round(info.duration, 2)

    # The duration is only knowable once transcribe() has decoded the audio, so
    # this is the earliest the estimate can be made -- still before the long part.
    _slow_notice = _warn_if_cpu_transcription_will_be_slow(total_dur, resolved_device)
    if _slow_notice:
        print(_slow_notice, flush=True)

    progress = tqdm(
        total=total_dur,
        unit="s",
        desc="      Transcribing",
        bar_format="{desc}: {percentage:3.0f}%|{bar}| {n:.0f}/{total:.0f}s [{elapsed}<{remaining}]",
    )

    for segment in segments:
        if cancel is not None:
            cancel.check()
        # Clamp so floating-point drift past the duration doesn't overshoot.
        progress.update(min(segment.end, total_dur) - progress.n)
        transkrip_lengkap += f"[{segment.start:.1f} - {segment.end:.1f}] {segment.text}\n"

        if segment.words:
            chunk_words: list[dict] = []
            chunk_start = 0.0

            for i, w in enumerate(segment.words):
                if len(chunk_words) == 0:
                    chunk_start = w.start

                chunk_words.append({
                    "word": w.word.strip(),
                    "start": w.start,
                    "end": w.end,
                })

                if len(chunk_words) == max_words_per_subtitle or i == len(segment.words) - 1:
                    data_segmen.append({
                        "start": chunk_start,
                        "end": w.end,
                        "words": chunk_words,
                    })
                    chunk_words = []

    progress.update(total_dur - progress.n)  # snap to 100% when done
    progress.close()
    return transkrip_lengkap, data_segmen


# ==============================================================================
# STAGE 3: GEMINI AI ANALYSIS
def analyze_with_ai(transkrip_lengkap: str, cfg, *, data_segmen=None) -> list[dict]:
    """Run the three-pass analyzer over the configured provider chain.

    There used to be a second path here: one request asking a single model for
    22 required fields per clip. At ~1200 output tokens per clip against a
    measured 12-13 tokens/s and a ~300s gateway it could not finish, it
    exceeded Groq's tokens-per-minute limit outright, and every job that ever
    ran it failed. It is gone, with its 330-line prompt.

    ``--ai-provider openai_compat`` still works, and still means what it did:
    ``clipping/config.py`` turns it into a one-link chain over the same
    endpoint before this is ever called, so a configured OPENAI_COMPAT_* setup
    reaches the same place by a better road.

    There is still no *silent* cross-provider fallback. A chain is an ordered
    list the user wrote down and every hop is printed; see DEC-023.
    """
    if data_segmen is None:
        raise ValueError(
            "The analyzer needs data_segmen; call "
            "analyze_with_ai(transkrip, cfg, data_segmen=segments)."
        )

    from clipping.analysis import analyzer
    from clipping.config import provider_keys
    from clipping.providers.registry import chain_from_env, parse_chain

    spec = getattr(cfg, "llm_chain", "") or ""
    chain = parse_chain(spec) if spec else chain_from_env()
    keys = provider_keys(cfg)
    if not keys:
        raise RuntimeError(
            "No AI provider key is set. Add at least one of GROQ_API_KEY, "
            "GOOGLE_API_KEY, NVIDIA_API_KEY, OPENROUTER_API_KEY or "
            "MISTRAL_API_KEY to your .env."
        )
    print(
        f"[3/3] Analyzing with the provider chain: "
        f"{', '.join(f'{l.provider}/{l.model}' for l in chain)}"
    )
    return analyzer.analyze(data_segmen, cfg, chain=chain, keys=keys)
