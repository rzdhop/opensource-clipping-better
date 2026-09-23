#!/usr/bin/env python3
"""
rzdhop's clips — AI Auto-Clipper & Teaser Generator

Local-first: this tool downloads nothing. Acquire the video and (optionally) its
transcript with your own tools, then point it at the files.

Usage:
    python main.py --video talk.mp4
    python main.py --video talk.mp4 --transcript talk.vtt   # skips Whisper
    python main.py --video talk.mp4 --transcript talk.vtt --clips 5 --ratio 16:9
    python main.py --help                                   # all options
"""

import sys

from clipping.config import build_config


def main():
    cfg = build_config(sys.argv[1:])

    from clipping import __version__ as version

    # ── Story Clip Mode ──────────────────────────────────────────────
    if getattr(cfg, "story_mode", False):
        from clipping.story_runner import run_story_pipeline

        print("=" * 70)
        print(f"🎬 rzdhop's clips v{version} — Story Clip Mode")
        print("=" * 70)
        print(f"   Recipe      : {cfg.story_recipe_path}")
        print(f"   Sources     : {cfg.sources_json_path}")
        print(f"   Ratio       : {cfg.pilihan_rasio}")
        print(f"   Output Dir  : {cfg.story_output_dir}")
        print("=" * 70)

        run_story_pipeline(cfg)

        print("\n✅ Done! All story clips have been rendered.")
        return

    # ── Normal Auto-Clip Mode ────────────────────────────────────────
    # Lazy import so --help works without heavy deps
    from clipping.runner import run_pipeline

    # Gate on the *active* provider's key, and gate early: analyze_with_ai only
    # runs after ingestion and transcription, so failing there wastes minutes.
    #
    # Skipped entirely for --load-gemini-json with a cached response, because a
    # render-only rerun needs no API key at all. (The old gate exited even then.)
    import os

    from clipping.config import missing_provider_key

    cached_ai = os.path.join(cfg.outputs_dir, "gemini_response.json")
    render_only = getattr(cfg, "load_gemini_json", False) and os.path.isfile(cached_ai)

    if not render_only:
        missing = missing_provider_key(cfg)
        if missing:
            _, env_name = missing
            # The parser's own choices, not PROVIDER_KEYS: that dict lists chain
            # LINKS, and suggesting them as --ai-provider values sent the user
            # to six flags argparse rejects.
            others = [p for p in ("chain", "openai_compat") if p != cfg.ai_provider]
            print(f"❌ ERROR: {env_name} not found (active provider: {cfg.ai_provider}).")
            print(f"   Set via: export {env_name}='your-key' or create a .env file")
            print(f"   Or switch provider: --ai-provider {' | '.join(others)}")
            sys.exit(1)

        # A key on the slow floor alone is not a chain that can carry the
        # analysis. After the key gate, so "no key at all" gets its own message;
        # before the probe, so it costs nothing (DEC-073).
        from clipping.config import chain_not_ready

        slow = chain_not_ready(cfg)
        if slow:
            print(f"❌ ERROR: {slow}")
            sys.exit(1)

        # Having a key is not the same as answering. Ask the chain an 8-token
        # question now rather than discover a dead provider after transcribing.
        from clipping.config import preflight_chain

        dead = preflight_chain(cfg)
        if dead:
            print(f"❌ ERROR: {dead}")
            print("   Re-run with --no-preflight to try anyway.")
            sys.exit(1)

    transcript_path = getattr(cfg, "transcript_path", None)

    print("=" * 70)
    print(f"🎬 rzdhop's clips v{version}")
    print("=" * 70)
    print(f"   Video       : {os.path.basename(cfg.file_video_asli)}")
    # State the transcript source explicitly. The Whisper fallback is the slow
    # path and must never be taken without the user seeing it.
    if transcript_path:
        print(f"   Transcript  : {os.path.basename(transcript_path)} (Whisper bypassed)")
    else:
        print(f"   Transcript  : none → Whisper ({cfg.whisper_model}, {cfg.whisper_device})")
    if getattr(cfg, "source_url", None):
        print(f"   Source Attr : {cfg.source_url}")
    print(f"   Clip Count  : {cfg.jumlah_clip}")
    print(f"   Ratio       : {cfg.pilihan_rasio}")
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
    # Every analysis path is a chain now, including --ai-provider openai_compat,
    # which build_config rewrites into a one-link one. So the banner names the
    # links it will actually call rather than a model nobody asked for.
    from clipping.providers.registry import chain_from_env, describe, parse_chain

    try:
        links = parse_chain(cfg.llm_chain) if cfg.llm_chain else chain_from_env()
        active_model = " → ".join(describe(link) for link in links)
    except Exception:  # noqa: BLE001 - a bad chain is reported when it runs
        active_model = cfg.llm_chain or "(invalid chain)"
    print(f"   Platform    : {cfg.platform}")
    print(f"   Language    : {cfg.output_language}")
    print(f"   AI          : {cfg.ai_provider} ({active_model})")
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

    if getattr(cfg, "dry_run_analysis", False):
        # run_pipeline returned before the render loop, so saying otherwise
        # would be a lie printed directly under the notice explaining that it
        # stopped early.
        print("\n✅ Done! Analysis only — nothing was rendered.")
    else:
        print("\n✅ Done! All clips have been rendered.")


if __name__ == "__main__":
    main()
