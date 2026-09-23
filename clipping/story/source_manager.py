"""
clipping.story.source_manager — Local Source Cache Manager

Copies each story source's local video file into the story cache, so the
assembler can address every source by a uniform ``<cache>/<id>.mp4`` path.
Sources are local files only -- nothing is downloaded, matching
``clipping.story.loader``. Acquire media with your own tools and point each
source's ``local_path`` at it.

Note: Engine functions are imported lazily to avoid pulling in heavy
dependencies (faster_whisper) at module level.
"""

import os
import shutil


# ==============================================================================
# CACHE DIRECTORY
# ==============================================================================

def get_cache_dir(outputs_dir: str) -> str:
    """Return (and create) the story source cache directory."""
    cache_dir = os.path.join(outputs_dir, "story_cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


# ==============================================================================
# SINGLE SOURCE INGEST
# ==============================================================================

def _ingest_single_source(source: dict, cache_dir: str) -> str:
    """Copy a local source video into the story cache and return its path.

    Story sources are local files: nothing is fetched. The cache still exists so
    the assembler can address every source by a uniform ``<cache>/<id>.mp4``
    path regardless of where the original lives.

    Raises
    ------
    RuntimeError
        If the source file is missing or empty.
    """
    sid = source["id"]
    cached_path = os.path.join(cache_dir, f"{sid}.mp4")

    if os.path.exists(cached_path):
        size_mb = os.path.getsize(cached_path) / (1024 * 1024)
        print(f"   ⏩ '{sid}' already in cache ({size_mb:.1f} MB), skipping.")
        return cached_path

    local_path = source.get("local_path")
    if not local_path:
        raise RuntimeError(f"❌ Source '{sid}' has no 'local_path'.")
    if not os.path.isfile(local_path):
        raise RuntimeError(
            f"❌ Source '{sid}': file not found: {local_path}"
        )
    if os.path.getsize(local_path) == 0:
        raise RuntimeError(f"❌ Source '{sid}': file is empty: {local_path}")

    print(f"   📁 [{sid}] Copying local file: {local_path}")
    shutil.copy2(local_path, cached_path)

    size_mb = os.path.getsize(cached_path) / (1024 * 1024)
    print(f"   ✅ '{sid}' successfully copied to cache ({size_mb:.1f} MB).")
    return cached_path


# ==============================================================================
# BATCH INGEST ALL SOURCES
# ==============================================================================

def ingest_all_sources(
    source_registry: dict[str, dict],
    cache_dir: str,
) -> dict[str, str]:
    """
    Copy every source in the registry into the story cache.

    Parameters
    ----------
    source_registry : dict
        Mapping of source_id → source entry dict.
    cache_dir : str
        Directory holding the cached copies.

    Returns
    -------
    dict[str, str]
        Mapping of source_id → cached file path.
    """
    total = len(source_registry)
    print(f"\n📦 Preparing {total} local video sources...\n")

    paths: dict[str, str] = {}
    failed: list[str] = []

    for idx, (sid, source) in enumerate(source_registry.items(), 1):
        print(f"[{idx}/{total}] Source: {source.get('name', sid)}")
        try:
            paths[sid] = _ingest_single_source(source, cache_dir)
        except Exception as e:
            # One bad source should not sink an otherwise valid story.
            print(f"   ⚠️ FAILED to prepare '{sid}': {e}")
            failed.append(sid)

    # --- Summary ---
    print(f"\n{'='*50}")
    print(f"📦 Ingest Summary: {len(paths)}/{total} succeeded")
    if failed:
        print(f"   ❌ Failed: {', '.join(failed)}")
    print(f"{'='*50}\n")

    return paths


# ==============================================================================
# STATUS SAVER
# ==============================================================================

def save_sources_status(
    source_registry: dict[str, dict],
    cached_paths: dict[str, str],
    outputs_dir: str,
) -> str:
    """
    Save a ``sources_status.json`` file documenting download results.

    Returns the path to the saved file.
    """
    import json

    status_entries = []
    for sid, src in source_registry.items():
        entry = {
            "id": sid,
            "name": src.get("name", sid),
            "platform": src["platform"],
            "url": src.get("url"),
            "local_path": src.get("local_path"),
            "cached_path": cached_paths.get(sid),
            "status": "ok" if sid in cached_paths else "failed",
        }
        if sid in cached_paths and os.path.exists(cached_paths[sid]):
            entry["size_mb"] = round(
                os.path.getsize(cached_paths[sid]) / (1024 * 1024), 2
            )
        status_entries.append(entry)

    status_path = os.path.join(outputs_dir, "sources_status.json")
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump({"sources": status_entries}, f, indent=2, ensure_ascii=False)

    print(f"💾 Sources status saved to: {status_path}")
    return status_path
