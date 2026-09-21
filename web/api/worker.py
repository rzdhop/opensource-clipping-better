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
from . import store

# Semaphore to control max concurrent jobs
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "1"))
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS)

# Settings overrides (API keys etc.). In memory for the running process, and
# mirrored to disk by settings_store so a restart does not lose them.
_settings_env: dict[str, str] = {}


def set_settings_env(env: dict[str, str], *, persist: bool = True) -> None:
    """Update the runtime settings environment, and store it unless told not to.

    An empty value REMOVES the override rather than storing an empty string.
    config_adapter resolves every key as ``env.get(NAME, os.environ.get(NAME))``,
    so a stored "" would shadow a perfectly good .env key forever, and the user
    would have no way to undo it from the UI.
    """
    for name, value in env.items():
        if value == "" or value is None:
            _settings_env.pop(name, None)
        else:
            _settings_env[name] = value

    if persist:
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
    """
    Run the clipping pipeline synchronously (called from thread pool).

    This function updates the job store at each pipeline step so the
    frontend can poll or receive SSE progress updates.
    """
    try:
        # Build config from API payload
        cfg = build_config_from_payload(
            payload, job_id, env_overrides=_settings_env
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
        store.update_progress(
            job_id,
            step="download",
            step_number=1,
            total_steps=7,
            message="Preparing source video...",
            percent=5.0,
        )

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
        else:
            store.set_error(
                job_id,
                "Video not found. Upload a video file, or pick an older job "
                "whose video is still present.",
            )
            return

        store.update_progress(
            job_id,
            step="download",
            step_number=1,
            total_steps=7,
            message=message,
            percent=14.0,
        )

        # --- Step 2: Transcript ---
        store.set_status(job_id, JobStatus.TRANSCRIBING)
        store.update_progress(
            job_id,
            step="transcribe",
            step_number=2,
            total_steps=7,
            message=(
                "Loading local transcript..."
                if getattr(cfg, "transcript_path", None)
                else "Starting transcription..."
            ),
            percent=15.0,
        )

        # Shared with the CLI runner so the two cannot drift.
        transkrip_lengkap, data_segmen = resolve_transcript(cfg)

        store.update_progress(
            job_id,
            step="transcribe",
            step_number=2,
            total_steps=7,
            message="Transcription complete.",
            percent=35.0,
        )

        # --- Step 3: AI Analysis ---
        store.set_status(job_id, JobStatus.ANALYZING)
        store.update_progress(
            job_id,
            step="analyze",
            step_number=3,
            total_steps=7,
            message="Analyzing with AI...",
            percent=36.0,
        )

        import json

        gemini_output_path = os.path.join(cfg.outputs_dir, "gemini_response.json")

        if getattr(cfg, "load_gemini_json", False) and os.path.exists(gemini_output_path):
            with open(gemini_output_path, "r", encoding="utf-8") as f:
                hasil_json = json.load(f)
        else:
            hasil_json = engine.analyze_with_ai(transkrip_lengkap, cfg)
            with open(gemini_output_path, "w", encoding="utf-8") as f:
                json.dump(hasil_json, f, indent=4, ensure_ascii=False)

        store.update_progress(
            job_id,
            step="analyze",
            step_number=3,
            total_steps=7,
            message=f"AI found {len(hasil_json)} viral clips.",
            percent=50.0,
        )

        # --- Step 4: Metadata ---
        from clipping import metadata

        hasil_json = metadata.normalize_and_validate(hasil_json)
        metadata_path = os.path.join(cfg.outputs_dir, "metadata_preview.json")
        metadata.save_metadata_preview(hasil_json, path=metadata_path)

        store.update_progress(
            job_id,
            step="metadata",
            step_number=4,
            total_steps=7,
            message="Metadata normalized.",
            percent=55.0,
        )

        # --- Step 5: Diarization (optional) ---
        diarization_data = None
        from clipping import studio, diarization as diarization_mod

        if (
            (getattr(cfg, "use_split_screen", False) and cfg.split_trigger == "diarization")
            or getattr(cfg, "use_camera_switch", False)
        ) and studio._is_vertical_ratio(cfg.pilihan_rasio):
            try:
                store.update_progress(
                    job_id,
                    step="diarization",
                    step_number=5,
                    total_steps=7,
                    message="Running speaker diarization...",
                    percent=56.0,
                )
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
                store.update_progress(
                    job_id,
                    step="diarization",
                    step_number=5,
                    total_steps=7,
                    message=f"Diarization failed: {e}. Falling back to normal mode.",
                    percent=58.0,
                )
                diarization_data = None

        # --- Step 6: Render Preparation ---
        store.set_status(job_id, JobStatus.RENDERING)
        store.update_progress(
            job_id,
            step="render",
            step_number=6,
            total_steps=7,
            message="Preparing rendering...",
            percent=60.0,
        )

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
            store.update_progress(
                job_id,
                step="render",
                step_number=6,
                total_steps=7,
                message=f"Rendering clip {clip_num}/{total_clips}...",
                percent=60.0 + (35.0 * clip_num / total_clips),
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

        # --- Save manifest ---
        manifest_path = os.path.join(cfg.outputs_dir, "render_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(render_manifest, f, ensure_ascii=False, indent=2)

        # --- Build clip details for the job store ---
        clips: list[ClipDetail] = []
        for entry in render_manifest:
            filename = os.path.basename(entry.get("output_file") or entry.get("video_path") or "")
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
                    metadata=entry,
                )
            )

        store.set_clips(job_id, clips)
        store.update_progress(
            job_id,
            step="done",
            step_number=7,
            total_steps=7,
            message=f"Done! {len(clips)} clips rendered successfully.",
            percent=100.0,
        )

    except Exception as exc:
        tb = traceback.format_exc()
        error_msg = f"{type(exc).__name__}: {exc}"
        store.set_error(job_id, error_msg)
        store.update_progress(
            job_id,
            step="error",
            step_number=0,
            total_steps=7,
            message=f"Pipeline failed: {error_msg}",
            percent=0.0,
        )
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
