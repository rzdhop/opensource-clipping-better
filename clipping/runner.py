"""
clipping.runner — Pipeline Orchestrator

Maps to Cell 4 (Execute) of the notebook.
Orchestrates the full clip generation pipeline.
"""

import json
import os

from . import diarization as diarization_mod
from . import engine, metadata, hook_manager

# studio pulls in cv2/mediapipe/ultralytics at module scope. Importing it here
# would mean `import clipping.runner` requires the full render stack, which
# defeats the --transcript bypass (a transcript-only run would still load the
# very ML stack it exists to avoid) and would force CI to install OpenCV just to
# test a text parser. It is imported inside run_pipeline, at first use.
#
# voiceover is imported inside the `if cfg.voiceover` branch, not here: it pulls
# in google-genai, and an optional feature must not make its dependency
# mandatory for every run.


def probe_video_duration(video_path: str) -> float | None:
    """Return *video_path*'s duration in seconds, or None if it cannot be read."""
    try:
        import cv2
    except ImportError:
        return None

    cap = cv2.VideoCapture(video_path)
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    finally:
        cap.release()

    if not fps or fps <= 0 or not frames or frames <= 0:
        return None
    return frames / fps


def _warn_on_transcript_video_mismatch(cfg, data_segmen) -> None:
    """Warn when a transcript plainly does not belong to its video.

    This is the single most likely mistake in the local-first workflow: the user
    pairs an mp4 with the wrong .vtt. It does not raise, because legitimate cases
    exist (a transcript covering only the first half of a long recording), but it
    must be loud, because the failure mode is otherwise invisible —
    ``buat_file_ass`` windows each clip by subtracting ``start_clip`` and
    silently drops segments whose span inverts, so a mismatched pair renders
    clean-looking clips with no subtitles and no error anywhere.
    """
    duration = probe_video_duration(cfg.file_video_asli)
    if not duration or not data_segmen:
        return

    transcript_end = float(data_segmen[-1]["end"])

    if transcript_end > duration * 1.1:
        print(
            f"\n   ⚠️  PERINGATAN: transkrip berakhir di {transcript_end:.0f}s "
            f"tetapi video hanya {duration:.0f}s. "
            "Transkrip kemungkinan bukan milik video ini — subtitle bisa hilang "
            "tanpa pesan error. Cek pasangan file, atau gunakan --transcript-offset.\n"
        )
    elif transcript_end < duration * 0.25:
        print(
            f"\n   ⚠️  PERINGATAN: transkrip hanya mencakup {transcript_end:.0f}s "
            f"dari video {duration:.0f}s ({transcript_end / duration:.0%}). "
            "Klip di luar rentang itu tidak akan punya subtitle.\n"
        )


def resolve_transcript(cfg) -> tuple[str, list[dict]]:
    """Return ``(transkrip_lengkap, data_segmen)`` for *cfg*'s source video.

    A local ``--transcript`` bypasses Whisper entirely: ``engine.load_transcript``
    is stdlib-only, so this path never imports CTranslate2 and never touches a
    GPU. Otherwise Whisper runs on the local video.

    Deliberately raises rather than falling back. A bad transcript must abort
    here, in milliseconds, instead of surfacing as silently missing subtitles
    after a 20-minute render.

    Shared by ``run_pipeline`` and the web worker so the two cannot drift.
    """
    transcript_path = getattr(cfg, "transcript_path", None)

    if transcript_path:
        print(
            f"[+] Local transcript injected ({os.path.basename(transcript_path)}). "
            "Bypassing Whisper inference."
        )
        transkrip_lengkap, data_segmen = engine.load_transcript(
            transcript_path,
            max_words_per_subtitle=cfg.max_kata_per_subtitle,
            offset=getattr(cfg, "transcript_offset", 0.0),
        )
        total_kata = sum(len(seg["words"]) for seg in data_segmen)
        print(
            f"   ✅ {len(data_segmen)} segmen, {total_kata} kata "
            f"({data_segmen[0]['start']:.1f}s → {data_segmen[-1]['end']:.1f}s)"
        )
        _warn_on_transcript_video_mismatch(cfg, data_segmen)
    else:
        if getattr(cfg, "no_whisper", False):
            raise RuntimeError(
                "--no-whisper aktif tetapi --transcript tidak diberikan."
            )
        transkrip_lengkap, data_segmen = engine.transcribe_video(
            cfg.file_video_asli,
            max_words_per_subtitle=cfg.max_kata_per_subtitle,
            model_size=cfg.whisper_model,
            device=cfg.whisper_device,
            compute_type=cfg.whisper_compute_type,
        )

    if not data_segmen:
        raise RuntimeError(
            "Transkrip kosong — tidak ada yang bisa dianalisis atau dirender."
        )

    return transkrip_lengkap, data_segmen


def run_pipeline(cfg) -> list[dict]:
    """
    Run the full clipping pipeline:
      1. Ingest the local source video
      2. Load the local transcript, or transcribe with Whisper
      3. Analyse with the configured AI provider
      4. Normalize metadata
      5. Prepare glitch transition
      6. Render each clip
      7. Save render_manifest.json

    Parameters
    ----------
    cfg : SimpleNamespace
        Configuration object from ``config.build_config()``.

    Returns
    -------
    list[dict]
        Render manifest (one dict per clip).
    """

    from . import studio

    # Step 1 — Ingest the source video.
    #
    # Local-first: the pipeline acquires nothing. This existence check is the
    # whole ingestion layer, and it is exactly the guarantee download_video()
    # used to provide on return.
    if not os.path.isfile(cfg.file_video_asli):
        raise FileNotFoundError(
            f"Video sumber tidak ditemukan: {cfg.file_video_asli}"
        )

    # Step 2 — Transcript (local file, or Whisper).
    transkrip_lengkap, data_segmen = resolve_transcript(cfg)

    # Step 3 — Gemini AI analysis
    gemini_output_path = os.path.join(cfg.outputs_dir, "gemini_response.json")
    
    if getattr(cfg, "load_gemini_json", False) and os.path.exists(gemini_output_path):
        print(f"\n🔄 [3/3] Memuat data AI ({cfg.ai_provider}) dari file lokal: {gemini_output_path}")
        with open(gemini_output_path, "r", encoding="utf-8") as f:
            hasil_json = json.load(f)
    else:
        hasil_json = engine.analyze_with_ai(transkrip_lengkap, cfg)
        
        # Save raw gemini json for future loading/reproduction
        with open(gemini_output_path, "w", encoding="utf-8") as f:
            json.dump(hasil_json, f, indent=4, ensure_ascii=False)
        print(f"💾 Raw AI response tersimpan di: {gemini_output_path}")

    # Step 4 — Metadata normalisation
    hasil_json = metadata.normalize_and_validate(hasil_json)
    metadata.print_preview(hasil_json)

    metadata_path = os.path.join(cfg.outputs_dir, "metadata_preview.json")
    metadata.save_metadata_preview(hasil_json, path=metadata_path)

    # Step 5 — Diarization (split-screen / camera-switch)
    diarization_data = None
    if (
        (getattr(cfg, "use_split_screen", False) and cfg.split_trigger == "diarization")
        or getattr(cfg, "use_camera_switch", False)
    ) and studio._is_vertical_ratio(cfg.pilihan_rasio):
        try:
            mode_label = (
                "Split-Screen"
                if getattr(cfg, "use_split_screen", False)
                else "Camera-Switch"
            )
            print(f"\n🎙️ [{mode_label}] Menjalankan speaker diarization...")
            audio_path = diarization_mod.derive_audio_path(
                cfg.file_video_asli, getattr(cfg, "outputs_dir", None)
            )
            diarization_mod.extract_audio(cfg.file_video_asli, audio_path)
            num_speakers_arg = getattr(cfg, "diarization_num_speakers", 2)
            min_spk = None
            max_spk = None

            if str(num_speakers_arg).lower() == "auto":
                max_faces = studio.estimate_speaker_count_from_video(
                    cfg.file_video_asli, cfg
                )
                num_speakers_arg = "auto"
                min_spk = max(1, max_faces)
                max_spk = min_spk + 2
                print(f"   ℹ️ Instruksi Pyannote: {min_spk} hingga {max_spk} speaker.")

            diarization_data = diarization_mod.run_diarization(
                audio_path,
                hf_token=cfg.hf_token,
                num_speakers=num_speakers_arg,
                min_speakers=min_spk,
                max_speakers=max_spk,
            )
            # Clean up temp audio
            if os.path.exists(audio_path):
                os.remove(audio_path)
        except Exception as e:
            print(f"⚠️ Diarization gagal: {e}")
            print("   Fallback ke mode render biasa (tanpa split-screen).")
            diarization_data = None

    # Step 6 — Video encoder & glitch
    os.environ["OSC_VIDEO_SCALE_ALGO"] = str(
        getattr(cfg, "video_scale_algo", "lanczos")
    )
    
    # Get target dimensions for auto-bitrate calculation
    import cv2
    cap_e = cv2.VideoCapture(cfg.file_video_asli)
    src_h_e = int(cap_e.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap_e.release()
    
    target_w_e, target_h_e = studio._get_render_dims(cfg, cfg.pilihan_rasio, source_h=src_h_e)
    video_encoder = studio.detect_video_encoder(cfg, target_h=target_h_e)

    file_glitch_ts = None
    if cfg.use_hook_glitch:
        print("⚙️ Menyiapkan Video Glitch Transisi...")
        
        # Get source dimensions for proper glitch scaling
        import cv2
        cap_g = cv2.VideoCapture(cfg.file_video_asli)
        source_h_g = int(cap_g.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap_g.release()

        file_glitch_ts = studio.siapkan_glitch_video(
            cfg.pilihan_rasio, cfg, video_encoder, source_h=source_h_g
        )

    # Step 6 — Render each clip
    render_manifest: list[dict] = []

    custom_hook_path = None
    if getattr(cfg, "hook_source", None):
        print("\n🎣 Mengunduh sumber klip Hook kustom...")
        custom_hook_path = hook_manager.download_custom_hook(cfg)

    # Step 5.5 — Generate Voice-Over (if enabled)
    if getattr(cfg, "voiceover", False):
        from . import voiceover

        print(f"\n🎙️ Meng-generate Voice-Over untuk {len(hasil_json)} klip...")
        for klip in hasil_json:
            try:
                # 1. Generate commentary script from snippet
                start = float(klip["start_time"])
                end = float(klip["end_time"])
                # Extract transcript snippet for this time range
                snippet_lines = []
                for seg in data_segmen:
                    if float(seg["end"]) > start and float(seg["start"]) < end:
                        # Support both Whisper format (has 'text') and YouTube JSON3 (only 'words')
                        seg_text = seg.get("text") or " ".join(w["word"] for w in seg.get("words", []))
                        if seg_text:
                            snippet_lines.append(seg_text)
                snippet_text = " ".join(snippet_lines)

                script = voiceover.generate_commentary_script(
                    snippet_text,
                    cfg,
                    style=cfg.voiceover_style,
                    language=cfg.voiceover_lang,
                    length=cfg.voiceover_length,
                )

                if script:
                    # 2. Synthesize TTS
                    audio_path, vo_segments = voiceover.synthesize_voice(
                        script,
                        cfg.voiceover_voice,
                        cfg.outputs_dir,
                        str(klip["rank"])
                    )
                    
                    if os.path.exists(audio_path):
                        klip["voiceover"] = {
                            "script": script,
                            "audio_path": audio_path,
                            "segments": vo_segments,
                            "voice": cfg.voiceover_voice
                        }

            except Exception as e:
                print(f"   ⚠️ Gagal generate voice-over untuk Rank {klip['rank']}: {e}")

    for klip in sorted(hasil_json, key=lambda x: x["rank"]):
        
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

    # Step 7 — Inject provenance for attribution & safety tracking.
    #
    # With no download step there is no URL to record, so source_url becomes
    # *declared* provenance (--source-url) rather than a fetch address. The key
    # is omitted when unknown: metadata._build_youtube_description already
    # no-ops on a falsy value, and writing null into every manifest row just
    # pushes a useless field downstream to the uploaders.
    declared_source = getattr(cfg, "source_url", None)
    for row in render_manifest:
        row.setdefault("source_video", os.path.basename(cfg.file_video_asli))
        row.setdefault(
            "transcript_source",
            os.path.basename(cfg.transcript_path)
            if getattr(cfg, "transcript_path", None)
            else f"whisper:{cfg.whisper_model}",
        )
        if declared_source and not row.get("source_url"):
            row["source_url"] = declared_source

    # Step 8 — Save manifest
    manifest_path = os.path.join(cfg.outputs_dir, "render_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(render_manifest, f, ensure_ascii=False, indent=2)

    print(
        f"\n💾 Render manifest disimpan ke {manifest_path} ({len(render_manifest)} item)"
    )


    return render_manifest

