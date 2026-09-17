#!/usr/bin/env python3
"""
OpenSource Clipping — AI Auto-Clipper & Teaser Generator

Usage:
    python main.py --url "https://..."      # run with required URL
    python main.py --url "https://..." --clips 5 --ratio 16:9
    python main.py --help                   # show all available options
"""

import sys

from clipping.config import build_config


def main():
    cfg = build_config(sys.argv[1:])

    version = "1.12.0"

    # ── Story Clip Mode ──────────────────────────────────────────────
    if getattr(cfg, "story_mode", False):
        from clipping.story_runner import run_story_pipeline

        print("=" * 70)
        print(f"🎬 OpenSource Clipping v{version} — Story Clip Mode")
        print("=" * 70)
        print(f"   Recipe      : {cfg.story_recipe_path}")
        print(f"   Sources     : {cfg.sources_json_path}")
        print(f"   Rasio       : {cfg.pilihan_rasio}")
        print(f"   Output Dir  : {cfg.story_output_dir}")
        print(f"   Skip DL     : {'YES' if cfg.skip_download else 'NO'}")
        print("=" * 70)

        run_story_pipeline(cfg)

        print("\n✅ Selesai! Semua story clips telah dirender.")
        return

    # ── Normal Auto-Clip Mode ────────────────────────────────────────
    # Lazy import so --help works without heavy deps
    from clipping.runner import run_pipeline

    if not cfg.api_key_gemini:
        print("❌ ERROR: GOOGLE_API_KEY environment variable tidak ditemukan.")
        print("   Set via: export GOOGLE_API_KEY='your-key' atau buat file .env")
        sys.exit(1)

    import os

    transcript_path = getattr(cfg, "transcript_path", None)

    print("=" * 70)
    print(f"🎬 OpenSource Clipping v{version}")
    print("=" * 70)
    if getattr(cfg, "video_provided", False):
        print(f"   Video       : {os.path.basename(cfg.file_video_asli)}")
    else:
        # Legacy remote path, removed with the download layer.
        print(f"   URL         : {cfg.url_youtube}")
    # State the transcript source explicitly. The Whisper fallback is the slow
    # path and must never be taken without the user seeing it.
    if transcript_path:
        print(f"   Transcript  : {os.path.basename(transcript_path)} (Whisper bypassed)")
    else:
        print(f"   Transcript  : none → Whisper ({cfg.whisper_model}, {cfg.whisper_device})")
    if getattr(cfg, "source_url", None):
        print(f"   Source Attr : {cfg.source_url}")
    print(f"   Jumlah Clip : {cfg.jumlah_clip}")
    print(f"   Rasio       : {cfg.pilihan_rasio}")
    print(f"   Font Style  : {cfg.gaya_font_aktif}")
    print(f"   Subtitles   : {'OFF' if cfg.no_subs else 'ON'}")
    print(f"   B-Roll      : {'ON' if cfg.use_broll else 'OFF'}")
    print(f"   Hook Glitch : {'ON' if cfg.use_hook_glitch else 'OFF'}")
    print(f"   BGM         : {'ON' if cfg.use_auto_bgm else 'OFF'}")
    print(f"   Karaoke     : {'ON' if cfg.use_karaoke_effect else 'OFF'}")
    print(f"   Split-Screen: {'ON' if cfg.use_split_screen else 'OFF'}")
    if cfg.use_split_screen:
        print(f"   Dynamic Split: {'ON' if cfg.use_dynamic_split else 'OFF'}")
        print(f"   Split Trigger: {cfg.split_trigger}")
    print(f"   Gemini      : {cfg.gemini_model}")
    if getattr(cfg, "watermark_enabled", False):
        wm_type = "Text" if cfg.watermark_text else "Image"
        wm_content = cfg.watermark_text or cfg.watermark_image or "-"
        print(f"   Watermark   : ON ({wm_type}: {wm_content})")
        print(f"   WM Opacity  : {cfg.watermark_opacity}%")
        print(f"   WM Position : {cfg.watermark_position}")
        print(f"   WM Padding  : {cfg.watermark_padding}px")
        if cfg.watermark_image:
            print(f"   WM Scale    : {getattr(cfg, 'watermark_scale', 15)}% of frame height")
    print("=" * 70)

    run_pipeline(cfg)

    print("\n✅ Selesai! Semua klip telah dirender.")


if __name__ == "__main__":
    main()
