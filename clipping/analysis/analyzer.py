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

import string
import time
from collections import namedtuple

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

# How much of that budget pass A may spend. The rest is held back for the
# re-rank and the per-clip metadata, because a pass A that consumes everything
# leaves B falling back to the scan's own scores and every clip rendering with a
# basic title -- a silent quality loss of exactly the kind DEC-021 was written
# about. Clamped upward below so A always gets room for at least one request.
PASS_A_BUDGET_SHARE = 0.7

# Used when the chain's own timeouts cannot be read (a caller passing plain
# tuples, an unknown provider): assume the slowest link this project ships,
# because a floor that is too small refuses every window and makes zero requests.
FALLBACK_REQUEST_FLOOR_SECONDS = 330.0

# The clock moves between this module granting a window its deadline and
# run_chain checking that a request still fits inside it -- rendering the beats,
# building the prompt, walking the chain's keyless links. Granting exactly one
# request's worth therefore grants slightly less than one request's worth by the
# time it is measured, and the window is refused for an epsilon.
#
# Found by running the real failed job: every window but the last was skipped
# with "a 330s request does not fit the 330s left in the time budget" -- the
# never-tries bug the floor exists to prevent, reintroduced by rounding.
GRANT_MARGIN_SECONDS = 1.0

# Passes A and B are judgement calls over material that is already written:
# which moment is strongest, which five are most different. Sampling wider
# there buys nothing and costs consistency between windows.
ANALYTIC_TEMPERATURE = 0.2
# Pass C is the only pass that WRITES something a person reads -- titles,
# captions, a one-line hook. At 0.2 it returns competent, flat, largely
# interchangeable phrasing.
WRITING_TEMPERATURE = 0.5


class AnalysisError(RuntimeError):
    """Analysis produced no usable clips."""


# What pass A actually managed, as opposed to what it returned. Without this the
# analyzer could not tell "every window answered and found nothing" from "no
# window ever answered", and told a user whose provider was dead that their video
# was "all housekeeping".
ScanStats = namedtuple("ScanStats", "total answered failed skipped last_error")

# What every scan window is told about the video it is a part of. All three
# are free: two are already known to Python and the third is typed by the
# uploader, so none of this costs a request.
VideoContext = namedtuple("VideoContext", "language total_seconds topic")


def _lost(stats):
    """Windows that contributed nothing because they never ran."""
    return stats.failed + stats.skipped


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
    floor = _request_floor(chain, keys)
    started = time_fn()
    budget = float(getattr(cfg, "analysis_budget_seconds", 0) or DEFAULT_TOTAL_BUDGET_SECONDS)
    run_deadline = started + budget

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

    def ask(system, user, json_schema, name, max_tokens, deadline=None,
            temperature=ANALYTIC_TEMPERATURE):
        # run_chain's signature is unchanged: it still takes one absolute
        # deadline and neither knows nor cares that a window hands it a smaller
        # one than the run's own.
        return runner(
            chain,
            system=system,
            user=user,
            schema=json_schema,
            schema_name=name,
            max_tokens=max_tokens,
            temperature=temperature,
            keys=keys,
            on_log=on_log,
            deadline=run_deadline if deadline is None else deadline,
            time_fn=time_fn,
        )[0]

    # Pass A gets a share of the pool, never all of it -- but always at least one
    # full request, or the predictive check would refuse every window and the run
    # would make no requests at all.
    pass_a_deadline = max(
        min(run_deadline, started + budget * PASS_A_BUDGET_SHARE),
        min(run_deadline, started + floor),
    )

    context = VideoContext(
        language=language,
        total_seconds=all_beats[-1]["end"],
        topic=str(getattr(cfg, "topic", "") or "").strip(),
    )

    candidates, stats = _pass_a(
        all_beats, preset, want, ask, on_log, time_fn, pass_a_deadline, floor,
        context=context,
    )
    if stats.answered == 0:
        # Not a verdict on the video: nothing was ever read. Saying otherwise
        # sends the user to re-cut a transcript that was never the problem.
        raise AnalysisError(
            f"The video was never analysed: all {stats.total} scan window(s) "
            f"failed before the model answered "
            f"({stats.failed} provider error(s), "
            f"{stats.skipped} skipped for want of time). "
            f"Last error | {stats.last_error}"
        )
    if not candidates:
        message = (
            "No clippable moment was found anywhere in this transcript. "
            "That can mean the video is all housekeeping, or that the "
            "transcript does not match the video."
        )
        if _lost(stats):
            message += (
                f" Note that {_lost(stats)} of {stats.total} window(s) never "
                f"returned, so part of the video was never considered."
            )
        raise AnalysisError(message)

    spans = _snap_candidates(candidates, all_beats, preset, on_log)
    if not spans:
        raise AnalysisError(
            f"{len(candidates)} moment(s) were found but none fit the "
            f"{preset.name} duration window ({preset.min:.0f}-{preset.max:.0f}s)."
        )

    chosen = _pass_b(
        spans, want, ask, on_log, time_fn, run_deadline, all_beats=all_beats
    )
    clips = _pass_c(
        chosen, all_beats, words, cfg, preset, language, ask, on_log, time_fn,
        run_deadline, floor,
    )

    if not clips:
        raise AnalysisError("Every clip failed to produce metadata.")

    _report_shortfall(want, len(clips), stats, on_log)
    elapsed = time_fn() - started
    on_log(f"   ✅ Analysis complete: {len(clips)} clip(s) in {elapsed:.0f}s.")
    return clips


# --------------------------------------------------------------------- passes

def _pass_a(all_beats, preset, want, ask, on_log, time_fn, deadline, floor,
           context=None):
    """Scan each window for candidate moments, each inside its own time share.

    DEC-027 states the invariant this restores: *a failure is local -- a failed
    window loses one window's candidates*. It was not true. Every window shared
    one absolute deadline, so a window whose provider hung for three attempts
    spent the whole run's budget and the remaining five were skipped without
    ever being tried. Catching a window's exception is worthless if that window
    already spent everyone else's time.

    The share is recomputed at the top of every iteration, which is what makes
    unused time return to the pool with no accumulator and nothing that can go
    stale: a window that answers in two seconds simply leaves a larger
    ``remaining`` for the windows after it.

    The ``floor`` is one full request against the slowest usable link. Without
    it a 900s pool over six windows gives 150s each, which is below NVIDIA's
    330s request timeout -- the predictive check would refuse every window and
    the run would issue no requests at all, trading a starvation bug for a
    never-tries bug. An early window may therefore borrow from the pool for one
    honest attempt, and a later window that no longer fits is skipped with its
    arithmetic printed.
    """
    ranges = beats_mod.windows(all_beats, size=WINDOW_BEATS, overlap=WINDOW_OVERLAP)
    on_log(f"   [1/3] Scanning {len(ranges)} window(s) for candidate moments...")

    candidates = []
    answered = 0
    failed = 0
    skipped = 0
    last_error = None
    needed = floor + GRANT_MARGIN_SECONDS
    for index, (lo, hi) in enumerate(ranges, start=1):
        now = time_fn()
        remaining = deadline - now
        if remaining < needed:
            skipped += 1
            last_error = last_error or (
                f"{max(0.0, remaining):.0f}s left, {floor:.0f}s needed per request"
            )
            on_log(
                f"   ⏱ Window {index}/{len(ranges)} ({lo}-{hi}) skipped: "
                f"{max(0.0, remaining):.0f}s left in the scan budget, and one "
                f"request to this chain can take {floor:.0f}s."
            )
            continue

        windows_left = len(ranges) - index + 1
        share = remaining / windows_left
        # `needed`, not `floor`: a window granted exactly one request's worth is
        # granted slightly less than that by the time run_chain measures it.
        window_deadline = min(deadline, now + max(share, needed))

        beats_text = beats_mod.render_beats(all_beats, lo, hi)
        try:
            answer = ask(
                prompts.SYSTEM,
                prompts.candidates_prompt(
                    beats_text,
                    max_candidates=MAX_CANDIDATES_PER_WINDOW,
                    preset=preset,
                    # The same one-line preface on every window: a window that
                    # does not know what the video is cannot judge whether a
                    # moment stands alone outside it.
                    language=context.language if context else None,
                    total_seconds=context.total_seconds if context else None,
                    topic=context.topic if context else None,
                ),
                schema.CANDIDATES_SCHEMA,
                "candidates",
                schema.MAX_TOKENS_CANDIDATES,
                window_deadline,
            )
        except Exception as exc:  # noqa: BLE001 - one window, not the run
            # Logged as before, but the reason is kept rather than discarded:
            # it is the difference between a failure of the provider and a
            # verdict on the transcript, and only one of those is the user's
            # problem to fix.
            failed += 1
            last_error = f"{type(exc).__name__}: {exc}"
            on_log(f"   ⚠️ Window {index}/{len(ranges)} ({lo}-{hi}) failed | {exc}")
            continue

        answered += 1
        found = _clean_candidates(answer, lo, hi)
        candidates.extend(found)
        on_log(f"   [1/3] Window {index}/{len(ranges)}: {len(found)} candidate(s).")

    return candidates, ScanStats(len(ranges), answered, failed, skipped, last_error)


HOOK_LINE_WORDS = 15


def _hook_line(beat):
    """The opening words of *beat*, truncated for the re-rank's candidate list.

    Truncated rather than summarised: the point is to show the model the words
    a viewer actually hears first, and a summary of a hook is not a hook.
    """
    words = str((beat or {}).get("text", "")).split()
    line = " ".join(words[:HOOK_LINE_WORDS])
    return f"{line}…" if len(words) > HOOK_LINE_WORDS else line


def _normalize_topic(value):
    return str(value or "").strip().strip(string.punctuation).lower()


def _enforce_variety(entries, want, on_log):
    """Demote picks that repeat an earlier pick's topic, then backfill.

    The prompt asks for variety; models agree and then return five versions of
    the strongest point anyway. This makes it mechanical.

    Demotion, not deletion, is what keeps DEC-021 true: a video genuinely about
    one subject still yields the number of clips that was asked for. A repeat
    only loses its slot to something different — never to nothing.
    """
    seen = set()
    picked = []
    demoted = []

    for entry in entries:
        topic = _normalize_topic(entry.get("topic"))
        # A blank topic is an unknown, not a match. Two unknowns are not the
        # same subject, and treating them as one would silently drop a clip
        # every time a model omitted the field.
        if topic and topic in seen:
            demoted.append(entry)
            continue
        if topic:
            seen.add(topic)
        picked.append(entry)

    if len(picked) < want and demoted:
        backfilled = demoted[: want - len(picked)]
        on_log(
            f"   ↷ {len(backfilled)} pick(s) repeated a topic and were demoted, "
            f"not dropped — there was nothing else to promote."
        )
        picked.extend(backfilled)
    elif demoted:
        on_log(
            f"   ↷ {len(demoted)} pick(s) repeated a topic already covered and "
            f"were demoted in favour of a different subject."
        )
    return picked


def _pass_b(spans, want, ask, on_log, time_fn, deadline, all_beats=None):
    """Rank every surviving candidate against the whole video's field.

    The description comes off the span itself. It used to be looked up in a
    dict keyed on the candidate's original beat ids, which missed whenever
    ``snap`` had moved a boundary — so the model ranking the video's best
    moments was reading ``clip`` and an empty gist for nearly all of them.
    """
    if len(spans) <= want:
        on_log(f"   [2/3] {len(spans)} candidate(s) survived snapping; taking all.")
        return sorted(spans, key=lambda s: -s.score)[:want]

    by_id = {beat["i"]: beat for beat in all_beats or ()}
    lines = []
    for index, span in enumerate(spans):
        line = (
            f"#{index} [{span.end - span.start:.0f}s] score={span.score} "
            f"{span.kind or 'clip'} | {span.gist}"
        )
        hook = _hook_line(by_id.get(span.b0))
        if hook:
            line += f' | hook: "{hook}"'
        lines.append(line)

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

    # Every usable entry is collected before anything is cut, because the
    # variety rule below can only demote a repeat if it has something further
    # down the ranking to promote in its place.
    valid = []
    seen = set()
    for entry in (answer or {}).get("ranked", []):
        try:
            index = int(entry["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if index in seen or not 0 <= index < len(spans):
            continue
        seen.add(index)
        # ``_span`` carries the position in *spans* through the variety pass,
        # which reorders the entries and must not lose what each one points at.
        valid.append(dict(entry, _span=index))

    ordered = [
        spans[entry["_span"]]._replace(score=int(entry.get("score", 0) or 0))
        for entry in _enforce_variety(valid, want, on_log)[:want]
    ]

    if not ordered:
        on_log("   ⚠️ Ranking named no usable candidate; using the scan's scores.")
        return sorted(spans, key=lambda s: -s.score)[:want]
    return ordered


def _pass_c(spans, all_beats, words, cfg, preset, language, ask, on_log, time_fn,
            deadline, floor):
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
        # Predictive, like every other check now: refuse a request that
        # cannot finish rather than start one and overrun.
        if time_fn() + floor > deadline:
            on_log(
                f"   ⏱ Clip {rank}: no room left in the budget for a "
                f"{floor:.0f}s metadata request; it will render with a basic title."
            )
        else:
            try:
                meta = ask(
                    prompts.SYSTEM,
                    prompts.clip_meta_prompt(
                        beats_text,
                        language=language,
                        # Carried from the scan that found this moment. The
                        # prompt has always accepted them; nothing sent them.
                        kind=span.kind,
                        gist=span.gist,
                        want_broll=want_broll,
                    ),
                    schema.CLIP_META_SCHEMA,
                    "clip_meta",
                    schema.MAX_TOKENS_CLIP_META,
                    temperature=WRITING_TEMPERATURE,
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

def _request_floor(chain, keys):
    """The longest single request any *usable* link in *chain* could make.

    Keyless links are excluded: they are never contacted, so their timeout is
    not a cost anyone pays, and counting a 330s link the run cannot use would
    shrink every window's allowance for nothing.

    Deliberately tolerant. Callers in tests pass plain ``(provider, model)``
    tuples rather than ``Link``s, and an unknown provider must not take the run
    down; both fall back to the slowest timeout this project ships, because a
    floor that is too small is the worse failure -- it refuses every window and
    issues no requests at all.
    """
    from ..providers import registry

    timeouts = []
    for link in chain or ():
        provider = getattr(link, "provider", None)
        if provider is None and isinstance(link, (tuple, list)) and link:
            provider = link[0]
        if keys and provider and not keys.get(provider):
            continue
        try:
            timeouts.append(float(registry.effective_timeout(link)))
        except Exception:  # noqa: BLE001 - an unreadable link is not fatal here
            timeouts.append(FALLBACK_REQUEST_FLOOR_SECONDS)
    return max(timeouts) if timeouts else FALLBACK_REQUEST_FLOOR_SECONDS


def _hook_beats_of(meta, span):
    """The beat ids the metadata pass named, strongest first.

    Tolerant on purpose: a missing key, a null, a string id or a stray float
    all mean "the model did not usefully answer this", and the caller falls
    back to the clip's own opening. None of that is worth failing a clip over.
    """
    heads = []
    for raw in (meta or {}).get("hook_beats") or ():
        try:
            heads.append(int(raw))
        except (TypeError, ValueError):
            continue
    return heads


def _derive_all(meta, span, clip_beats, clip_words, cfg, want_broll):
    hook_beats = _hook_beats_of(meta, span)
    hook_start, hook_end = derive.hook_window(
        hook_beats[0] if hook_beats else span.b0,
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
            clip_beats,
            want=int(getattr(cfg, "hook_v2_items", 3) or 3),
            # The pass that read the clip names these; the heuristic inside is
            # only the fallback for when it did not.
            hook_beats=hook_beats,
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
    # The candidate dicts go in whole, so each span comes back carrying the
    # gist and kind the scan gave it. Rebuilding bare triples here is what used
    # to strand that description on the far side of the snapper.
    spans = snap.snap_all(
        candidates,
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


def _report_shortfall(want, got, stats, on_log):
    """Say so when fewer clips were delivered than asked for.

    DEC-021 forbids silently changing the requested clip count. This is the
    other side of it: when the transcript genuinely does not contain that many
    clippable moments, deliver what exists and say plainly that you did. A
    supply limit is not a provider limit, and hiding it would be the same
    failure DEC-021 was written about.

    The amendment: this could not previously tell which of the two it was
    looking at, and so blamed the transcript for both. A window that never
    answered is not evidence about the video.
    """
    if got >= want:
        return

    line = f"   ℹ️ Asked for {want} clip(s); this transcript yielded {got}. "
    if _lost(stats):
        line += (
            f"{_lost(stats)} of {stats.total} scan window(s) never returned, so "
            f"part of the video was not considered — this is not a verdict on "
            f"the transcript."
        )
    else:
        line += "The rest of it did not contain a moment that stands on its own."
    on_log(line)
