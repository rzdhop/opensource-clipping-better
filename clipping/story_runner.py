"""
clipping.story_runner — Story Clip Pipeline Orchestrator

Orchestrates the full Story Clip pipeline:
  1. Load & validate sources.json
  2. Download & cache all source videos
  3. Transcribe each source with Whisper
  4. Load & validate story_recipe.json
  5. Assemble each clip (hook + highlight) — clean, no subs
  6. Save story_manifest.json
"""

import json
import os

from .story import loader, source_manager, assembler


# ==============================================================================
# WHISPER TRANSCRIPTION FOR STORY SOURCES
# ==============================================================================

def _load_source_transcripts(
    cached_paths: dict[str, str],
    source_registry: dict[str, dict],
    cache_dir: str,
    cfg,
) -> dict[str, dict]:
    """
    Resolve a transcript for every cached source.

    Resolution order per source:
      1. an existing ``<cache>/<sid>_transcript.json``;
      2. the source's ``transcript_path`` (local .vtt/.srt/.json3) -- no Whisper;
      3. Whisper on the cached video, unless ``--no-whisper`` is set.

    Returns
    -------
    dict[str, dict]
        Mapping of source_id -> {"source_id", "transkrip", "segmen", "path"}.
    """
    from . import engine

    whisper_model = getattr(cfg, "whisper_model", "large-v3")
    whisper_device = getattr(cfg, "whisper_device", "auto")
    whisper_compute = getattr(cfg, "whisper_compute_type", "auto")
    max_words = getattr(cfg, "max_kata_per_subtitle", 5)
    no_whisper = getattr(cfg, "no_whisper", False)

    transcripts: dict[str, dict] = {}
    total = len(cached_paths)

    # One Whisper model shared across every source that needs it. Previously the
    # model was rebuilt inside transcribe_video on each call, so an N-source
    # story paid the large-v3 load (~30s and several GB) N times.
    shared_model = None

    for idx, (sid, video_path) in enumerate(cached_paths.items(), 1):
        out_path = os.path.join(cache_dir, f"{sid}_transcript.json")

        # --- 1. Cached transcript -------------------------------------------
        if os.path.exists(out_path):
            print(f"   ⏩ [{idx}/{total}] '{sid}' already has a transcript, skipping.")
            try:
                with open(out_path, "r", encoding="utf-8") as f:
                    transcripts[sid] = json.load(f)
                continue
            except Exception:
                pass  # corrupted cache -> fall through and rebuild

        if not os.path.exists(video_path):
            print(f"   ⚠️ [{idx}/{total}] '{sid}' file not found, skipping transcript.")
            continue

        source = source_registry.get(sid, {})
        declared_transcript = source.get("transcript_path")

        try:
            # --- 2. Local transcript -> no Whisper --------------------------
            if declared_transcript:
                print(
                    f"   [+] [{idx}/{total}] Local transcript injected for '{sid}' "
                    f"({os.path.basename(declared_transcript)}). "
                    "Bypassing Whisper inference."
                )
                transkrip, segmen = engine.load_transcript(
                    declared_transcript, max_words_per_subtitle=max_words
                )
            # --- 3. Whisper -------------------------------------------------
            else:
                if no_whisper:
                    raise RuntimeError(
                        f"--no-whisper is active but source '{sid}' has no "
                        "'transcript_path'."
                    )
                print(f"   🎤 [{idx}/{total}] Transcribing '{sid}'...")
                if shared_model is None:
                    shared_model = engine.load_whisper_model(
                        whisper_model, whisper_device, whisper_compute
                    )
                transkrip, segmen = engine.transcribe_video(
                    video_path,
                    max_words_per_subtitle=max_words,
                    model_size=whisper_model,
                    device=whisper_device,
                    compute_type=whisper_compute,
                    model=shared_model,
                )

            result = {
                "source_id": sid,
                "transkrip": transkrip,
                "segmen": segmen,
                "path": out_path,
            }
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            transcripts[sid] = result
            print(f"   ✅ '{sid}' ready ({len(segmen)} segments).")

        except Exception as e:
            # One bad source should not sink the whole story, but say so loudly.
            print(f"   ⚠️ '{sid}' failed to prepare: {e}")

    return transcripts


# ==============================================================================
# MAIN PIPELINE
# ==============================================================================

def run_story_pipeline(cfg) -> list[dict]:
    """
    Run the full Story Clip pipeline.

    Parameters
    ----------
    cfg : SimpleNamespace
        Configuration object from ``config.build_config()``.
        Must include ``story_recipe_path``, ``sources_json_path``,
        and standard config fields.

    Returns
    -------
    list[dict]
        Story manifest (one dict per clip with output paths).
    """

    print("=" * 70)
    print("🎬 Story Clip — Multi-Source Narrative Assembly")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Step 1 — Load sources.json
    # ------------------------------------------------------------------
    sources_path = getattr(cfg, "sources_json_path", "sources.json")
    print(f"\n[1/6] Loading sources: {sources_path}")
    source_registry = loader.load_sources(sources_path)

    # ------------------------------------------------------------------
    # Step 2 — Ingest local sources into the cache
    # ------------------------------------------------------------------
    cache_dir = source_manager.get_cache_dir(cfg.outputs_dir)

    print(f"\n[2/6] Preparing local sources → {cache_dir}")
    cached_paths = source_manager.ingest_all_sources(source_registry, cache_dir)

    # Save ingest status
    source_manager.save_sources_status(source_registry, cached_paths, cfg.outputs_dir)

    # ------------------------------------------------------------------
    # Step 3 — Resolve a transcript for each source
    # ------------------------------------------------------------------
    print("\n[3/6] Preparing transcript for each source...")
    transcripts = _load_source_transcripts(
        cached_paths, source_registry, cache_dir, cfg
    )
    print(f"   📝 {len(transcripts)}/{len(cached_paths)} source(s) have a transcript.")

    # ------------------------------------------------------------------
    # Step 4 — Load & validate recipe
    # ------------------------------------------------------------------
    recipe_path = getattr(cfg, "story_recipe_path", "story_recipe.json")
    print(f"\n[4/6] Loading recipe: {recipe_path}")
    recipe = loader.load_recipe(recipe_path, source_registry)

    # ------------------------------------------------------------------
    # Step 5 — Assemble each clip (clean, no subs, no text overlay)
    # ------------------------------------------------------------------
    clips = recipe.get("clips", [])
    defaults = recipe.get("_defaults", None)
    ratio = getattr(cfg, "pilihan_rasio", None) or (defaults.ratio if defaults else "9:16")

    story_output_dir = getattr(
        cfg, "story_output_dir",
        os.path.join(cfg.outputs_dir, "story_clips")
    )
    os.makedirs(story_output_dir, exist_ok=True)

    print(f"\n[5/6] Assembling {len(clips)} clip(s)...")
    print(f"   Output dir: {story_output_dir}")
    print(f"   Ratio: {ratio}")
    print(f"   Mode: clean (no subs, no text overlay)")

    manifest: list[dict] = []

    for clip_config in sorted(clips, key=lambda c: c["clip_id"]):
        cid = clip_config["clip_id"]
        title = clip_config.get("title", f"Clip {cid}")

        print(f"\n{'─'*50}")
        print(f"📎 Clip #{cid}: {title}")
        print(f"{'─'*50}")

        clip_dir = os.path.join(story_output_dir, f"clip_{cid}")
        os.makedirs(clip_dir, exist_ok=True)

        # --- Assemble Hook (clean) ---
        hook_path = assembler.assemble_hook(
            clip_config=clip_config,
            source_registry=source_registry,
            cache_dir=cache_dir,
            output_dir=clip_dir,
            ratio=ratio,
        )

        # --- Assemble Highlight (clean) ---
        highlight_path = assembler.assemble_highlight(
            clip_config=clip_config,
            source_registry=source_registry,
            cache_dir=cache_dir,
            output_dir=clip_dir,
            ratio=ratio,
        )

        entry = {
            "clip_id": cid,
            "title": title,
            "hook_path": hook_path,
            "highlight_path": highlight_path,
            "status": "ok" if (hook_path and highlight_path) else "partial",
            "metadata": clip_config.get("metadata", {}),
        }
        manifest.append(entry)

    # ------------------------------------------------------------------
    # Step 6 — Save manifest
    # ------------------------------------------------------------------
    manifest_path = os.path.join(cfg.outputs_dir, "story_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # Save transcripts index
    transcripts_index_path = os.path.join(cfg.outputs_dir, "story_transcripts.json")
    transcripts_summary = {}
    for sid, t in transcripts.items():
        transcripts_summary[sid] = {
            "path": t.get("path", ""),
            "segmen_count": len(t.get("segmen", [])),
        }
    with open(transcripts_index_path, "w", encoding="utf-8") as f:
        json.dump(transcripts_summary, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*70}")
    print(f"✅ Story Clip done! {len(manifest)} clip(s) rendered.")
    print(f"💾 Manifest: {manifest_path}")
    print(f"📝 Transcripts: {transcripts_index_path}")
    print(f"📁 Output: {story_output_dir}")
    print(f"{'='*70}")

    # Print summary table
    print(f"\n{'Clip':>6} | {'Title':<35} | {'Hook':>6} | {'Highlight':>10} | Status")
    print(f"{'─'*6} | {'─'*35} | {'─'*6} | {'─'*10} | {'─'*8}")
    for entry in manifest:
        hook_ok = "✅" if entry["hook_path"] else "❌"
        hl_ok = "✅" if entry["highlight_path"] else "❌"
        title_short = entry["title"][:35]
        print(f"  {entry['clip_id']:>4} | {title_short:<35} | {hook_ok:>6} | {hl_ok:>10} | {entry['status']}")

    return manifest

