"""
web.api.worker — Background task runner for the clipping pipeline.

Wraps ``clipping.runner.run_pipeline()`` in an asyncio task with
progress reporting via the job store.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .config_adapter import build_config_from_payload
from . import settings_store
from .models import ClipDetail, JobStatus
from . import activity
from . import signals
from . import store

# How much of a printed line is worth showing as the step's one-line detail.
# The feed keeps the whole thing; this is just the headline.
MAX_DETAIL_CHARS = 240


def _record_pipeline_line(job_id: str, message: str, level: str, source: str) -> None:
    """Sink for the activity tee: file the line, and let it refine the step.

    The most recent line the pipeline printed IS the best answer to "what is it
    doing right now", so it becomes the current step's detail whatever it says.
    A retry counter additionally lands in a structured field.

    A progress bar's redraws are the exception. They belong in the detail line,
    which updates live, and not in the feed: two 12-second clips were enough to
    make them 90% of a 500-entry buffer and evict the transcript warning and the
    provider line with it. The first tick of each bar is kept, so the feed still
    records that the stage started.
    """
    previous = store.last_event(job_id)
    redraw = (
        previous is not None
        and previous.get("source") == source
        and signals.is_progress_redraw(previous.get("message"), message)
    )
    if not redraw:
        store.append_event(job_id, message, level, source)
    attempt = signals.attempt_from(message)
    store.refine_progress(
        job_id,
        detail=message[:MAX_DETAIL_CHARS],
        attempt=attempt[0] if attempt else None,
        max_attempts=attempt[1] if attempt else None,
    )


# Tee stdout/stderr so everything the pipeline prints is recorded against the
# job that printed it. Installed at import, which is after uvicorn has already
# configured its own logging handlers against the real streams -- so server logs
# keep going where they always went, and only worker threads inside
# `activity.capture(...)` contribute to a job's feed.
activity.install(_record_pipeline_line)

# Semaphore to control max concurrent jobs
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "1"))
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS)

# Settings overrides (API keys etc.): in memory for the running process, mirrored
# to data/settings.json so a restart does not lose them. They used to live only
# here, which meant a `docker compose restart` silently emptied them and the next
# job failed for a key the user could still see listed as set.
#
# NOT loaded at import (DEC-043). An import-time read of a secrets file means any
# test that imports this module picks up the developer's real keys. The app
# lifespan calls load_settings_env() instead.
_settings_env: dict[str, str] = {}


def set_settings_env(env: dict[str, str], *, persist: bool = True) -> None:
    """Update the runtime settings environment, and store it unless told not to.

    An empty value REMOVES the override rather than storing an empty string
    (DEC-043). config_adapter resolves every key as
    ``env.get(NAME, os.environ.get(NAME, ""))``, so a stored "" would shadow a
    perfectly good .env key forever, with no way to undo it from the UI.
    """
    for name, value in env.items():
        if value == "" or value is None:
            _settings_env.pop(name, None)
        else:
            _settings_env[name] = value

    if persist:
        # Write through, so the value is on disk before the response says it is set.
        settings_store.save(_settings_env)


def get_settings_env() -> dict[str, str]:
    """Get current settings environment."""
    return dict(_settings_env)


def load_settings_env() -> int:
    """Load stored settings into the runtime environment. Returns how many.

    Called once from the app lifespan rather than at import: reading a secrets
    file as an import side effect would mean any test that imports this module
    picks up the developer's real keys.
    """
    stored = settings_store.load()
    set_settings_env(stored, persist=False)
    return len(stored)


def _run_pipeline_sync(job_id: str, payload: dict) -> None:
    """Run the pipeline, attributing everything it prints to this job."""
    with activity.capture(job_id):
        _execute_pipeline(job_id, payload)


TOTAL_STEPS = 7


def _transcript_plan(cfg) -> tuple[str, str]:
    """What step 2 is about to do, and why it may take a very long time.

    Whisper is the second place a job appears to hang, and the reason is never
    visible: a CPU transcription of a long video runs for hours, and the first
    run also downloads the model weights. Say so up front. The pipeline prints
    the device it actually settles on, and that line arrives as the step detail.
    """
    transcript = getattr(cfg, "transcript_path", None)
    if transcript:
        return (
            "Loading the transcript you supplied...",
            f"Reading {os.path.basename(transcript)} — Whisper is skipped entirely.",
        )
    model = getattr(cfg, "whisper_model", None) or "?"
    device = getattr(cfg, "whisper_device", None) or "auto"
    compute = getattr(cfg, "whisper_compute_type", None) or "auto"
    return (
        f"Transcribing with Whisper {model}...",
        f"Requested device {device}, compute {compute}. On CPU this is the slow "
        "path and the first run also downloads the model. Supplying a .vtt "
        "skips this step entirely.",
    )


def _server_fetch_enabled() -> bool:
    from clipping.ingest import enabled

    return enabled()


def _try_fetch(job_id, url, cfg, progress):
    """Attempt a server-side download. Returns ``(video, subtitle)`` or None.

    None means the job has been parked in ``needs_upload`` with a message; the
    caller must simply return. Failing the job instead would throw away its id,
    its settings and its output directory for something the user can fix in one
    step.
    """
    from clipping.ingest import FetchError, fetch

    progress("download", 1, "Trying to download the source video...", 8.0)
    dest = os.path.join(cfg.outputs_dir, "source")

    try:
        result = fetch(
            url,
            dest,
            cookies_path=_cookies_path(),
            pot_provider_url=os.environ.get("POT_PROVIDER_URL") or None,
        )
    except FetchError as exc:
        store.set_error(job_id, f"Could not download {url}: {exc}")
        return None

    if result.needs_upload or not result.video_path:
        store.set_status(job_id, JobStatus.NEEDS_UPLOAD)
        store.set_error(job_id, result.error or "This server could not download the video.")
        progress("download", 1, "Waiting for the video to be attached.", 8.0)
        return None

    return result.video_path, result.subtitle_path


def _cookies_path():
    """An optional cookies.txt, for sites that need a signed-in session."""
    candidate = os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
        "data",
        "cookies.txt",
    )
    return candidate if os.path.isfile(candidate) else None


def _execute_pipeline(job_id: str, payload: dict) -> None:
    """
    Run the clipping pipeline synchronously (called from thread pool).

    This function updates the job store at each pipeline step so the
    frontend can poll or receive SSE progress updates.
    """
    # Filled in once cfg exists, and attached to every progress event, so the
    # dashboard can always say WHO is being asked rather than only that "AI" is.
    # Declared before the try so the failure path can still emit.
    ai = {"provider": None, "model": None}

    def progress(step: str, step_number: int, message: str, percent: float, **extra):
        store.update_progress(
            job_id,
            step=step,
            step_number=step_number,
            total_steps=TOTAL_STEPS,
            message=message,
            percent=percent,
            provider=ai["provider"],
            model=ai["model"],
            **extra,
        )

    try:
        # Build config from API payload
        cfg = build_config_from_payload(
            payload, job_id, env_overrides=_settings_env
        )

        ai["provider"] = str(getattr(cfg, "ai_provider", "") or "") or None
        ai["model"] = (
            getattr(cfg, "nvidia_model", None)
            if ai["provider"] == "nvidia"
            else getattr(cfg, "gemini_model", None)
        )

        # Validate API key
        # Gate on the ACTIVE provider's key. An unconditional Gemini check here
        # would fail every job the moment NVIDIA became the default.
        from clipping.config import missing_provider_key

        # A render-only rerun (the dashboard's "Bypass AI" toggle) reuses a
        # cached response and calls no provider, so it must not require a key.
        # This mirrors the same skip in main.py.
        cached_ai = os.path.join(cfg.outputs_dir, "gemini_response.json")
        render_only = getattr(cfg, "load_gemini_json", False) and os.path.isfile(cached_ai)

        missing = None if render_only else missing_provider_key(cfg)
        if missing:
            _, env_name = missing
            store.set_error(
                job_id,
                f"{env_name} not found (active provider: {cfg.ai_provider}). "
                "Set it via Settings or the .env file.",
            )
            return

        # --- Step 1: Download ---
        store.set_status(job_id, JobStatus.DOWNLOADING)
        progress("download", 1, "Preparing source video...", 5.0)

        from clipping import engine
        from clipping.runner import resolve_transcript

        # --- Step 1: resolve the source video (no downloads) ---
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )

        if payload.get("upload_filename"):
            upload_path = os.path.join(
                project_root, "uploads", payload["upload_filename"]
            )
            if not os.path.isfile(upload_path):
                store.set_error(
                    job_id,
                    f"Uploaded file not found: {payload['upload_filename']}",
                )
                return
            cfg.file_video_asli = upload_path
            message = "Using uploaded file."
        elif os.path.isfile(cfg.file_video_asli):
            # Reuse-job path: an earlier job's video is still on disk.
            message = "Using video from a previous job."
        elif payload.get("source_url") and _server_fetch_enabled():
            fetched = _try_fetch(job_id, payload["source_url"], cfg, progress)
            if fetched is None:
                # Parked in needs_upload with an explanation, not failed: the
                # job keeps its id, settings and output directory, and resumes
                # the moment a file is attached.
                return
            cfg.file_video_asli, subtitle_path = fetched
            if subtitle_path and not cfg.transcript_path:
                cfg.transcript_path = subtitle_path
            message = "Downloaded from the source URL."
        else:
            store.set_error(
                job_id,
                "Video not found. Upload a video file, or pick an older job "
                "whose video is still present.",
            )
            return

        progress("download", 1, message, 14.0)

        # --- Step 2: Transcript ---
        store.set_status(job_id, JobStatus.TRANSCRIBING)
        headline, detail = _transcript_plan(cfg)
        progress("transcribe", 2, headline, 15.0, detail=detail)

        # Shared with the CLI runner so the two cannot drift.
        transkrip_lengkap, data_segmen = resolve_transcript(cfg)

        progress("transcribe", 2, "Transcription complete.", 35.0)

        # --- Step 3: AI Analysis ---
        store.set_status(job_id, JobStatus.ANALYZING)
        progress(
            "analyze",
            3,
            # The single longest opaque wait in the pipeline: one blocking call
            # that can retry for tens of minutes without the percentage moving.
            f"Asking {ai['provider'] or 'the AI provider'} for the best moments...",
            36.0,
        )

        import json

        gemini_output_path = os.path.join(cfg.outputs_dir, "gemini_response.json")

        if getattr(cfg, "load_gemini_json", False) and os.path.exists(gemini_output_path):
            with open(gemini_output_path, "r", encoding="utf-8") as f:
                hasil_json = json.load(f)
        else:
            hasil_json = engine.analyze_with_ai(
                transkrip_lengkap, cfg, data_segmen=data_segmen
            )
            with open(gemini_output_path, "w", encoding="utf-8") as f:
                json.dump(hasil_json, f, indent=4, ensure_ascii=False)

        progress("analyze", 3, f"AI found {len(hasil_json)} viral clips.", 50.0)

        # --- Step 4: Metadata ---
        from clipping import metadata

        hasil_json = metadata.normalize_and_validate(hasil_json)
        metadata_path = os.path.join(cfg.outputs_dir, "metadata_preview.json")
        metadata.save_metadata_preview(hasil_json, path=metadata_path)

        progress("metadata", 4, "Metadata normalized.", 55.0)

        # --- Step 5: Diarization (optional) ---
        diarization_data = None
        from clipping import studio, diarization as diarization_mod

        if (
            (getattr(cfg, "use_split_screen", False) and cfg.split_trigger == "diarization")
            or getattr(cfg, "use_camera_switch", False)
        ) and studio._is_vertical_ratio(cfg.pilihan_rasio):
            try:
                progress("diarization", 5, "Running speaker diarization...", 56.0)
                audio_path = diarization_mod.derive_audio_path(
                    cfg.file_video_asli, getattr(cfg, "outputs_dir", None)
                )
                diarization_mod.extract_audio(cfg.file_video_asli, audio_path)
                num_speakers_arg = getattr(cfg, "diarization_num_speakers", 2)
                min_spk = None
                max_spk = None

                if str(num_speakers_arg).lower() == "auto":
                    max_faces = studio.estimate_speaker_count_from_video(cfg.file_video_asli, cfg)
                    num_speakers_arg = "auto"
                    min_spk = max(1, max_faces)
                    max_spk = min_spk + 2

                diarization_data = diarization_mod.run_diarization(
                    audio_path,
                    hf_token=cfg.hf_token,
                    num_speakers=num_speakers_arg,
                    min_speakers=min_spk,
                    max_speakers=max_spk,
                )
                if os.path.exists(audio_path):
                    os.remove(audio_path)
            except Exception as e:
                progress(
                    "diarization",
                    5,
                    f"Diarization failed: {e}. Falling back to normal mode.",
                    58.0,
                )
                diarization_data = None

        # --- Step 6: Render Preparation ---
        store.set_status(job_id, JobStatus.RENDERING)
        progress("render", 6, "Preparing rendering...", 60.0)

        os.environ["OSC_VIDEO_SCALE_ALGO"] = str(getattr(cfg, "video_scale_algo", "lanczos"))

        import cv2

        cap_e = cv2.VideoCapture(cfg.file_video_asli)
        src_h_e = int(cap_e.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap_e.release()

        target_w_e, target_h_e = studio._get_render_dims(cfg, cfg.pilihan_rasio, source_h=src_h_e)
        video_encoder = studio.detect_video_encoder(cfg, target_h=target_h_e)

        file_glitch_ts = None
        if cfg.use_hook_glitch:
            cap_g = cv2.VideoCapture(cfg.file_video_asli)
            source_h_g = int(cap_g.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap_g.release()
            file_glitch_ts = studio.siapkan_glitch_video(
                cfg.pilihan_rasio, cfg, video_encoder, source_h=source_h_g
            )

        # --- Step 7: Render Each Clip ---
        from clipping import hook_manager

        render_manifest: list[dict] = []
        total_clips = len(hasil_json)

        custom_hook_path = None
        if getattr(cfg, "hook_source", None):
            custom_hook_path = hook_manager.download_custom_hook(cfg)

        for idx, klip in enumerate(sorted(hasil_json, key=lambda x: x["rank"])):
            clip_num = idx + 1
            progress(
                "render",
                6,
                f"Rendering clip {clip_num}/{total_clips}...",
                60.0 + (35.0 * clip_num / total_clips),
                clip_index=clip_num,
                clip_total=total_clips,
            )

            if custom_hook_path:
                klip["custom_hook_info"] = {"file_path": custom_hook_path}

            hasil_render = studio.proses_klip(
                klip["rank"],
                klip,
                cfg.pilihan_rasio,
                file_glitch_ts,
                data_segmen,
                cfg,
                video_encoder,
                diarization_data=diarization_data,
            )
            if hasil_render:
                render_manifest.append(hasil_render)

        # A .srt beside each clip, before the manifest is written so srt_path
        # lands in it. Best-effort: the clips are already rendered.
        from clipping import subtitles_export

        subtitles_export.export_all(
            data_segmen, render_manifest, cfg.outputs_dir, on_log=print
        )

        # --- Save manifest ---
        manifest_path = os.path.join(cfg.outputs_dir, "render_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(render_manifest, f, ensure_ascii=False, indent=2)

        # --- Build clip details for the job store ---
        clips: list[ClipDetail] = []
        for entry in render_manifest:
            # "video_path" is the only name the manifest has ever used. This
            # line used to try "output_file" first -- a key no version of
            # clipping/studio/core.py has ever written, checked across the whole
            # history. Removing it makes the manifest/worker agreement exact and
            # testable (tests/test_manifest_fields.py) instead of permanently
            # excusing one dead name.
            filename = os.path.basename(entry.get("video_path") or "")
            # thumbnail_rank_N.jpg and highlight_rank_N.srt are written for every
            # clip and were never exposed: thumbnail_url was declared on
            # ClipDetail and never set, so the dashboard showed a grid of black
            # rectangles, and the .srt was reachable only inside the opaque
            # metadata blob. Basenames, unsigned, exactly like download_url --
            # routes/jobs.py signs all three on the way out.
            thumb = os.path.basename(entry.get("thumbnail_path") or "")
            srt = os.path.basename(entry.get("srt_path") or "")
            clips.append(
                ClipDetail(
                    rank=entry.get("rank", 0),
                    viral_score=entry.get("viral_score"),
                    title=entry.get("title_indonesia", ""),
                    title_en=entry.get("title_inggris", ""),
                    filename=filename,
                    duration=entry.get("duration"),
                    start_time=entry.get("start_time"),
                    end_time=entry.get("end_time"),
                    download_url=f"/api/outputs/{job_id}/{filename}",
                    thumbnail_url=f"/api/outputs/{job_id}/{thumb}" if thumb else None,
                    srt_url=f"/api/outputs/{job_id}/{srt}" if srt else None,
                    metadata=entry,
                )
            )

        store.set_clips(job_id, clips)
        progress("done", 7, f"Done! {len(clips)} clips rendered successfully.", 100.0)

    except Exception as exc:
        tb = traceback.format_exc()
        error_msg = f"{type(exc).__name__}: {exc}"
        store.set_error(job_id, error_msg)
        progress("error", 0, f"Pipeline failed: {error_msg}", 0.0)
        print(f"[Worker] Job {job_id} failed:\n{tb}", file=sys.stderr)


async def submit_job(job_id: str, payload: dict) -> None:
    """
    Submit a job to the background worker queue.

    Uses a semaphore to limit concurrency and runs the pipeline
    in a thread pool to avoid blocking the async event loop.
    """
    async def _run():
        async with _semaphore:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                _executor, _run_pipeline_sync, job_id, payload
            )

    asyncio.create_task(_run())
