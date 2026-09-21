"""Translate a slim analysis result into the clip dict the render layer expects.

This module is the entire reason `clipping/studio/` needs no changes. The render
layer, both uploaders and `metadata.normalize_and_validate` read a fixed set of
key names, several of them Indonesian and one of them misspelled (`hastag`).
Renaming those would mean editing a render layer that has zero automated tests
and is verified only by live renders (RC-7).

So the names live here, at one boundary, and nowhere else. The model never sees
them — which is also what stops it defaulting to Indonesian: it is asked for
`title_native`, not `title_indonesia`.

`LEGACY_KEYS` is asserted against the render layer by an AST test, so a key that
`studio/` starts reading and this module does not produce fails a test rather
than a render.

Stdlib only.
"""

from __future__ import annotations

# Every key any consumer reads off a clip dict. Extracted by AST from
# clipping/studio/*.py, clipping/runner.py, both uploaders and web/api, then
# split by how it is accessed.
#
# REQUIRED are read with [] and raise KeyError if absent:
#   studio/core.py:144-145 clip["start_time"], clip["end_time"]
#   runner.py:349/357 and web/api/worker.py:335 sorted(..., x["rank"])
REQUIRED_KEYS = ("rank", "start_time", "end_time")

# Read with .get() and a default, so absence degrades rather than crashes — but
# every one of them is a feature silently turning itself off.
OPTIONAL_KEYS = (
    "viral_score",
    "hook_start_time",
    "hook_end_time",
    "title_indonesia",
    "title_inggris",
    "hastag",
    "description_hook",
    "description_context",
    "keyword_tags",
    "tiktok_caption",
    "tiktok_title_id",
    "tiktok_caption_id",
    "alasan",
    "bgm_mood",
    "typography_plan",
    "broll_list",
    "keep_segments",
    "hook_v2",
)

LEGACY_KEYS = REQUIRED_KEYS + OPTIONAL_KEYS

# Written by the pipeline itself, never by analysis: runner.py:341 sets
# klip["voiceover"] after TTS, and runner.py:353 sets klip["custom_hook_info"]
# from --hook-source. Listed so the AST guard does not demand them of us.
PIPELINE_WRITTEN_KEYS = ("voiceover", "custom_hook_info")

# Synthesized downstream by metadata.normalize_and_validate from the keys above.
# studio/core.py reads them, but producing them here would be duplicating the
# normalizer, whose trimming and hashtag rules are the single source of truth.
NORMALIZER_KEYS = (
    "youtube_title_final",
    "youtube_description_final",
    "youtube_tags_final",
    "tiktok_caption_final",
    "tiktok_title_id_final",
    "tiktok_caption_id_final",
)


def to_legacy_clip(*, rank, span, meta, derived, gist=""):
    """Build the clip dict the pipeline consumes.

    *meta* is the model's slim output for this clip (possibly empty, if its
    metadata request failed); *derived* is what ``derive.py`` computed. A clip
    with no metadata still renders — it falls back to the gist for its title,
    which is a worse clip, not a failed job.
    """
    meta = meta or {}
    title_native = (meta.get("title_native") or gist or "").strip()
    title_en = (meta.get("title_en") or gist or title_native or "").strip()

    clip = {
        # --- required by the render loop -----------------------------------
        "rank": int(rank),
        "start_time": float(span.start),
        "end_time": float(span.end),
        # --- ordering and display ------------------------------------------
        "viral_score": int(meta.get("score", span.score) or 0),
        # --- the teaser -----------------------------------------------------
        "hook_start_time": float(derived["hook_start"]),
        "hook_end_time": float(derived["hook_end"]),
        # --- titles and captions -------------------------------------------
        # title_indonesia is the NATIVE-language title, whatever that language
        # is. The key name is historical; renaming it would touch the render
        # layer and both uploaders for no functional gain.
        "title_indonesia": title_native,
        "title_inggris": title_en,
        "hastag": derived["hashtags"],
        "description_hook": (meta.get("desc_hook") or "").strip(),
        "description_context": (meta.get("desc_context") or "").strip(),
        "keyword_tags": derived["keywords"],
        "tiktok_caption": (meta.get("caption_en") or "").strip(),
        "tiktok_title_id": title_native,
        "tiktok_caption_id": (meta.get("caption_native") or "").strip(),
        "alasan": (meta.get("reason") or "").strip(),
        # --- render styling --------------------------------------------------
        "bgm_mood": derived["bgm_mood"],
        "typography_plan": derived["typography_plan"],
        "broll_list": derived["broll_list"],
    }

    # Omitted rather than empty when they do not apply: studio/core.py:262 and
    # :456 have their own fallbacks, and an empty list would suppress them.
    if derived.get("keep_segments"):
        clip["keep_segments"] = derived["keep_segments"]
    if derived.get("hook_v2"):
        clip["hook_v2"] = derived["hook_v2"]

    return clip


def assert_renderable(clip):
    """Raise if *clip* would crash the render loop. Cheap insurance.

    The three required keys are read with ``[]``, minutes into a job, after the
    transcription and analysis have already been paid for.
    """
    missing = [key for key in REQUIRED_KEYS if key not in clip]
    if missing:
        raise ValueError(f"clip is missing required key(s): {missing}")
    if not isinstance(clip["rank"], int):
        raise ValueError(f"rank must be an int, got {type(clip['rank']).__name__}")
    for key in ("start_time", "end_time"):
        if not isinstance(clip[key], float):
            raise ValueError(f"{key} must be a float, got {type(clip[key]).__name__}")
    if clip["end_time"] <= clip["start_time"]:
        raise ValueError(
            f"clip {clip['rank']} has a degenerate span: "
            f"{clip['start_time']} -> {clip['end_time']}"
        )
    return clip
