"""The three-pass analysis: scan, re-rank, describe.

Replaces a single request that asked for 22 required fields per clip. That
request could not be answered: at ~1200 output tokens per clip and a measured
12-13 tokens/s, seven clips needed ~660s against a ~300s gateway, and it also
exceeded Groq's 8000 tokens/minute outright. Every job that ever ran it failed.

    A. candidate scan   one request per ~45-beat window   ~320 tokens out
    -- snap + dedupe, in Python, no model --
    B. global re-rank   one request for the whole video   ~200 tokens out
    C. clip metadata    one request per selected clip     ~280 tokens out

For a 20-minute video and seven clips that is ~12 requests and ~19k tokens, with
no single generation over ~320 tokens. The pass that decides the cut points is
pass A; the pass that decides which of them ship is B, and it sees the whole
video at once — which the monolith claimed to do and never achieved, because it
could not finish.

A failure in C costs one clip's metadata, not the job.

Imports nothing heavier than the standard library plus the provider layer.
"""

from __future__ import annotations

import time

from . import beats as beats_mod
from . import derive, prompts, schema, snap
from . import presets as presets_mod
from .adapter import assert_renderable, to_legacy_clip
from .langdetect import detect, language_name

WINDOW_BEATS = 45
WINDOW_OVERLAP = 5
MAX_CANDIDATES_PER_WINDOW = 6
# Ask for more than we need so dedupe and the duration filter have something to
# choose from. Roughly three times, floored, because most windows contribute
# nothing and a handful contribute several.
CANDIDATE_MULTIPLIER = 3

# The whole analysis, not one request. DEC-020's budget, re-expressed: with
# twelve small requests the failure mode is no longer one enormous call, but a
# provider that is slow on every one of them.
DEFAULT_TOTAL_BUDGET_SECONDS = 900


class AnalysisError(RuntimeError):
    """Analysis produced no usable clips."""


def analyze(
    data_segmen,
    cfg,
    *,
    chain,
    keys,
    on_log=print,
    time_fn=time.monotonic,
    run_chain=None,
):
    """Return clips in the legacy dict shape, ready for the render loop.

    *data_segmen* is the transcript contract; *cfg* supplies the clip count,
    platform preset, hook duration and feature flags.
    """
    from ..providers import llm as llm_mod

    runner = run_chain or llm_mod.run_chain
    started = time_fn()
    budget = float(getattr(cfg, "analysis_budget_seconds", 0) or DEFAULT_TOTAL_BUDGET_SECONDS)
    deadline = started + budget

    want = int(getattr(cfg, "jumlah_clip", 7) or 7)
    preset = presets_mod.get(getattr(cfg, "platform", presets_mod.DEFAULT_PRESET))

    all_beats = beats_mod.build_beats(data_segmen)
    if not all_beats:
        raise AnalysisError("The transcript produced no beats; there is nothing to analyse.")
    words = beats_mod.flatten_words(data_segmen)

    language = _resolve_language(cfg, data_segmen, on_log)

    on_log(
        f"   🧩 {len(all_beats)} beats from {len(words)} words. "
        f"Target: {want} clip(s), {preset.name} ({preset.min:.0f}-{preset.max:.0f}s), "
        f"metadata in {language_name(language)}."
    )

    def ask(system, user, json_schema, name, max_tokens):
        return runner(
            chain,
            system=system,
            user=user,
            schema=json_schema,
            schema_name=name,
            max_tokens=max_tokens,
            keys=keys,
            on_log=on_log,
            deadline=deadline,
            time_fn=time_fn,
        )[0]

    candidates = _pass_a(all_beats, preset, want, ask, on_log)
    if not candidates:
        raise AnalysisError(
            "No clippable moment was found anywhere in this transcript. "
            "That can mean the video is all housekeeping, or that the "
            "transcript does not match the video."
        )

    spans = _snap_candidates(candidates, all_beats, preset, on_log)
    if not spans:
        raise AnalysisError(
            f"{len(candidates)} moment(s) were found but none fit the "
            f"{preset.name} duration window ({preset.min:.0f}-{preset.max:.0f}s)."
        )

    chosen = _pass_b(spans, candidates, want, ask, on_log, time_fn, deadline)
    clips = _pass_c(
        chosen, all_beats, words, cfg, preset, language, ask, on_log, time_fn, deadline
    )

    if not clips:
        raise AnalysisError("Every clip failed to produce metadata.")

    _report_shortfall(want, len(clips), on_log)
    elapsed = time_fn() - started
    on_log(f"   ✅ Analysis complete: {len(clips)} clip(s) in {elapsed:.0f}s.")
    return clips


# --------------------------------------------------------------------- passes

def _pass_a(all_beats, preset, want, ask, on_log):
    """Scan each window for candidate moments."""
    ranges = beats_mod.windows(all_beats, size=WINDOW_BEATS, overlap=WINDOW_OVERLAP)
    on_log(f"   [1/3] Scanning {len(ranges)} window(s) for candidate moments...")

    candidates = []
    for index, (lo, hi) in enumerate(ranges, start=1):
        beats_text = beats_mod.render_beats(all_beats, lo, hi)
        try:
            answer = ask(
                prompts.SYSTEM,
                prompts.candidates_prompt(
                    beats_text,
                    max_candidates=MAX_CANDIDATES_PER_WINDOW,
                    preset=preset,
                ),
                schema.CANDIDATES_SCHEMA,
                "candidates",
                schema.MAX_TOKENS_CANDIDATES,
            )
        except Exception as exc:  # noqa: BLE001 - one window, not the run
            on_log(f"   ⚠️ Window {index}/{len(ranges)} ({lo}-{hi}) failed | {exc}")
            continue

        found = _clean_candidates(answer, lo, hi)
        candidates.extend(found)
        on_log(f"   [1/3] Window {index}/{len(ranges)}: {len(found)} candidate(s).")

    return candidates


def _pass_b(spans, candidates, want, ask, on_log, time_fn, deadline):
    """Rank every surviving candidate against the whole video's field."""
    if len(spans) <= want:
        on_log(f"   [2/3] {len(spans)} candidate(s) survived snapping; taking all.")
        return sorted(spans, key=lambda s: -s.score)[:want]

    by_gist = {(c["b0"], c["b1"]): c for c in candidates}
    lines = []
    for index, span in enumerate(spans):
        meta = by_gist.get((span.b0, span.b1), {})
        lines.append(
            f"#{index} [{span.end - span.start:.0f}s] score={span.score} "
            f"{meta.get('kind', 'clip')} {meta.get('gist', '')}".rstrip()
        )

    on_log(f"   [2/3] Ranking {len(spans)} candidates for {want} slot(s)...")
    try:
        answer = ask(
            prompts.SYSTEM,
            prompts.rerank_prompt("\n".join(lines), want=want),
            schema.RANKED_SCHEMA,
            "ranked",
            schema.MAX_TOKENS_RANKED,
        )
    except Exception as exc:  # noqa: BLE001
        on_log(f"   ⚠️ Ranking failed, falling back to the scan's own scores | {exc}")
        return sorted(spans, key=lambda s: -s.score)[:want]

    ordered = []
    seen = set()
    for entry in (answer or {}).get("ranked", []):
        try:
            index = int(entry["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if index in seen or not 0 <= index < len(spans):
            continue
        seen.add(index)
        ordered.append(spans[index]._replace(score=int(entry.get("score", 0) or 0)))
        if len(ordered) >= want:
            break

    if not ordered:
        on_log("   ⚠️ Ranking named no usable candidate; using the scan's scores.")
        return sorted(spans, key=lambda s: -s.score)[:want]
    return ordered


def _pass_c(spans, all_beats, words, cfg, preset, language, ask, on_log, time_fn, deadline):
    """Describe each selected clip, then build its legacy dict."""
    on_log(f"   [3/3] Writing metadata for {len(spans)} clip(s)...")
    want_broll = bool(getattr(cfg, "use_broll", True)) and bool(
        getattr(cfg, "pexels_api_key", "")
    )

    clips = []
    for rank, span in enumerate(sorted(spans, key=lambda s: -s.score), start=1):
        clip_beats = derive.clip_beats_of(all_beats, span.b0, span.b1)
        clip_words = derive.clip_words_of(all_beats, words, span.b0, span.b1)
        beats_text = beats_mod.render_beats(all_beats, span.b0, span.b1)

        meta = {}
        if time_fn() >= deadline:
            on_log(f"   ⏱ Clip {rank}: no time left in the budget for metadata.")
        else:
            try:
                meta = ask(
                    prompts.SYSTEM,
                    prompts.clip_meta_prompt(
                        beats_text, language=language, want_broll=want_broll
                    ),
                    schema.CLIP_META_SCHEMA,
                    "clip_meta",
                    schema.MAX_TOKENS_CLIP_META,
                ) or {}
            except Exception as exc:  # noqa: BLE001 - one clip, not the run
                on_log(
                    f"   ⚠️ Clip {rank} metadata failed; it will render with a "
                    f"basic title | {exc}"
                )

        derived = _derive_all(meta, span, clip_beats, clip_words, cfg, want_broll)
        clip = to_legacy_clip(
            rank=rank, span=span, meta=meta, derived=derived,
            gist=f"Clip {rank}",
        )
        assert_renderable(clip)
        clips.append(clip)
        on_log(
            f"   [3/3] Clip {rank}: {span.start:.1f}s → {span.end:.1f}s "
            f"({span.end - span.start:.0f}s) — {clip['title_inggris'][:60]!r}"
        )
    return clips


# ------------------------------------------------------------------- helpers

def _derive_all(meta, span, clip_beats, clip_words, cfg, want_broll):
    hook_start, hook_end = derive.hook_window(
        meta.get("hook_beat", span.b0),
        clip_beats,
        clip_start=span.start,
        clip_end=span.end,
        hook_duration=getattr(cfg, "durasi_hook", 3),
    )
    derived = {
        "hook_start": hook_start,
        "hook_end": hook_end,
        "hashtags": derive.hashtags(meta.get("hashtags")),
        "keywords": derive.keywords(meta.get("keywords")),
        "bgm_mood": derive.bgm_mood(meta.get("mood")),
        "typography_plan": derive.typography_plan(meta.get("emphasis"), clip_words),
        "broll_list": derive.broll_list(
            meta.get("broll_queries"),
            clip_beats,
            clip_start=span.start,
            clip_end=span.end,
            hook_end=hook_end,
            enabled=want_broll,
        ),
        "keep_segments": [],
        "hook_v2": None,
    }

    if not getattr(cfg, "no_segment_trim", False):
        derived["keep_segments"] = derive.keep_segments(
            meta.get("drop_beats"), clip_beats,
            clip_start=span.start, clip_end=span.end,
        )

    if getattr(cfg, "hook_v2", False):
        items = derive.hook_v2_items(
            clip_beats, want=int(getattr(cfg, "hook_v2_items", 3) or 3)
        )
        if items:
            derived["hook_v2"] = {
                "enabled": True,
                "items": items,
                "transition": {"type": "white_flash"},
            }
    return derived


def _clean_candidates(answer, lo, hi):
    """Keep only well-formed candidates whose beat ids are inside the window."""
    out = []
    for raw in (answer or {}).get("candidates", []):
        if not isinstance(raw, dict):
            continue
        try:
            b0 = int(raw["b0"])
            b1 = int(raw["b1"])
        except (KeyError, TypeError, ValueError):
            continue
        if b1 < b0:
            b0, b1 = b1, b0
        # A window can only speak about its own beats. Anything else is the
        # model guessing, and a guessed id is a clip cut from nowhere.
        if b0 < lo or b1 > hi:
            continue
        out.append(
            {
                "b0": b0,
                "b1": b1,
                "score": _clamp_score(raw.get("score")),
                "gist": str(raw.get("gist") or "").strip(),
                "kind": str(raw.get("kind") or "clip").strip(),
            }
        )
    return out


def _clamp_score(value):
    try:
        return max(1, min(100, int(value)))
    except (TypeError, ValueError):
        return 50


def _snap_candidates(candidates, all_beats, preset, on_log):
    rejected = []
    spans = snap.snap_all(
        [(c["b0"], c["b1"], c["score"]) for c in candidates],
        all_beats,
        preset,
        on_reject=lambda b0, b1, reason: rejected.append((b0, b1, reason)),
    )
    for b0, b1, reason in rejected[:5]:
        on_log(f"   ↷ Dropped beats {b0}-{b1}: {reason}")
    if len(rejected) > 5:
        on_log(f"   ↷ ...and {len(rejected) - 5} more that did not fit.")

    deduped = snap.dedupe(spans)
    on_log(
        f"   ✂️ {len(candidates)} candidate(s) → {len(spans)} that fit → "
        f"{len(deduped)} after removing overlaps."
    )
    return deduped


def _resolve_language(cfg, data_segmen, on_log):
    """Explicit setting, else what the transcript reads as."""
    explicit = str(getattr(cfg, "output_language", "") or "").strip().lower()
    if explicit and explicit != "auto":
        return explicit

    reported = str(getattr(cfg, "detected_language", "") or "").strip().lower()
    if reported:
        return reported

    text = " ".join(
        word["word"]
        for segment in data_segmen[:400]
        for word in segment.get("words", [])
    )
    code, confidence = detect(text)
    on_log(f"   🌍 Transcript reads as {language_name(code)} ({confidence:.0%} of stopwords).")
    return code


def _report_shortfall(want, got, on_log):
    """Say so when fewer clips were delivered than asked for.

    DEC-021 forbids silently changing the requested clip count. This is the
    other side of it: when the transcript genuinely does not contain that many
    clippable moments, deliver what exists and say plainly that you did. A
    supply limit is not a provider limit, and hiding it would be the same
    failure DEC-021 was written about.
    """
    if got < want:
        on_log(
            f"   ℹ️ Asked for {want} clip(s); this transcript yielded {got}. "
            f"The rest of it did not contain a moment that stands on its own."
        )
