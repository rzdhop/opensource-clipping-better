"""The three-pass analyzer's orchestration, against a scripted chain.

No network: ``run_chain`` is injected. These tests pin the properties that keep
a model's answer from reaching the render layer unchecked.
"""

import pytest

from clipping.analysis import analyzer, presets
from clipping.analysis.analyzer import AnalysisError
from clipping.providers.registry import Link


class Cfg:
    jumlah_clip = 3
    platform = "tiktok"
    durasi_hook = 3
    use_broll = False
    pexels_api_key = ""
    hook_v2 = False
    no_segment_trim = False
    output_language = "en"
    analysis_budget_seconds = 900


def segmen(n_sentences=60):
    """A transcript long enough to need several windows."""
    out = []
    t = 0.0
    for i in range(n_sentences):
        chunk = []
        for j, w in enumerate(f"sentence number {i} says something interesting here.".split()):
            chunk.append({"word": w, "start": t + j * 0.4, "end": t + (j + 1) * 0.4 - 0.01})
        out.append({"start": chunk[0]["start"], "end": chunk[-1]["end"], "words": chunk})
        t = chunk[-1]["end"] + 0.8
    return out


def scripted(*answers):
    """A run_chain stand-in that replays *answers* in order."""
    calls = []
    queue = list(answers)

    def _run(chain, **kwargs):
        calls.append(kwargs)
        if not queue:
            raise AssertionError("analyzer made more requests than scripted")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, chain[0]

    _run.calls = calls
    return _run


# A beat here is ~2.8s of speech on a 3.6s stride, so a candidate grown to the
# 34s tiktok target spans ~10 beats. Candidates must therefore sit ~15 beats
# apart, or dedupe collapses them -- correctly, since they would overlap.
FAR_APART = ((0, 3), (20, 23), (45, 48))


def candidates(*triples):
    return {"candidates": [
        {"b0": b0, "b1": b1, "score": score, "gist": "a thing happens",
         "kind": "story"}
        for b0, b1, score in triples
    ]}


META = {
    "score": 80, "title_native": "A title", "title_en": "A title",
    "hashtags": ["#a", "#b", "#c"], "desc_hook": "Hook.", "desc_context": "Context.",
    "keywords": ["a", "b", "c", "d", "e"], "caption_native": "cap",
    "caption_en": "cap", "reason": "because", "hook_beat": 0,
    "emphasis": ["sentence"], "broll_queries": [], "mood": "chill",
    "drop_beats": [],
}


def run(answers, cfg=None, segments=None):
    logs = []
    return analyzer.analyze(
        segments or segmen(),
        cfg or Cfg(),
        chain=[("groq", "m")],
        keys={"groq": "k"},
        on_log=logs.append,
        run_chain=scripted(*answers),
    ), logs


# ----------------------------------------------------------------- the passes

def test_a_full_run_produces_renderable_clips():
    from clipping.analysis import adapter

    # 2 windows + 1 rerank + 3 metadata
    answers = [
        candidates((0, 3, 90), (20, 23, 70)),
        candidates((45, 48, 85)),
        {"ranked": [{"id": 0, "score": 95}, {"id": 1, "score": 80}, {"id": 2, "score": 70}]},
        META, META, META,
    ]
    clips, _ = run(answers)

    assert len(clips) == 3
    for clip in clips:
        adapter.assert_renderable(clip)
    assert [c["rank"] for c in clips] == [1, 2, 3]


def test_every_window_is_scanned():
    segments = segmen(120)
    answers = [candidates((0, 2, 60))] * 10 + [
        {"ranked": [{"id": 0, "score": 90}]}
    ] + [META] * 5
    _, logs = run(answers, segments=segments)
    scanned = [line for line in logs if "[1/3] Window" in line]
    assert len(scanned) >= 2


def test_a_failed_window_does_not_fail_the_run():
    answers = [
        RuntimeError("provider exploded"),
        candidates((45, 48, 85)),
        {"ranked": [{"id": 0, "score": 90}]},
        META,
    ]
    clips, logs = run(answers)
    assert len(clips) == 1
    assert any("Window" in line and "failed" in line for line in logs)


def test_a_failed_ranking_falls_back_to_the_scan_scores():
    # Four candidates for three slots, so the ranking pass actually runs.
    answers = [
        candidates((0, 3, 90), (15, 18, 85), (30, 33, 70)),
        candidates((45, 48, 60)),
        RuntimeError("ranking exploded"),
        META, META, META,
    ]
    clips, logs = run(answers)
    assert len(clips) == 3
    assert any("Ranking failed" in line for line in logs)
    # It fell back to the scan's own scores, best first.
    assert [c["viral_score"] for c in clips] == sorted(
        (c["viral_score"] for c in clips), reverse=True
    )


def test_a_failed_metadata_request_costs_one_clip_not_the_job():
    answers = [
        candidates((0, 3, 90), (20, 23, 70)),
        candidates((45, 48, 85)),
        {"ranked": [{"id": 0, "score": 95}, {"id": 1, "score": 80}, {"id": 2, "score": 70}]},
        META, RuntimeError("metadata exploded"), META,
    ]
    clips, logs = run(answers)
    assert len(clips) == 3
    assert any("will render with a basic title" in line for line in logs)


# -------------------------------------------------- guarding the model's ids

def test_a_beat_id_outside_the_window_is_discarded():
    """A window can only speak about its own beats; anything else is the model
    guessing, and a guessed id is a clip cut from nowhere."""
    answers = [
        candidates((0, 3, 90), (9000, 9001, 99)),
        candidates((45, 48, 85)),
        {"ranked": [{"id": 0, "score": 95}, {"id": 1, "score": 80}]},
        META, META,
    ]
    clips, _ = run(answers)
    assert len(clips) == 2
    for clip in clips:
        assert clip["end_time"] > clip["start_time"]


def test_a_malformed_candidate_is_skipped():
    answers = [
        {"candidates": [
            {"b0": "not a number", "b1": 3, "score": 90},
            {"b0": 0, "b1": 3, "score": 90, "gist": "ok", "kind": "story"},
        ]},
        candidates((45, 48, 85)),
        {"ranked": [{"id": 0, "score": 95}, {"id": 1, "score": 80}]},
        META, META,
    ]
    clips, _ = run(answers)
    assert len(clips) == 2


def test_a_ranking_naming_an_unknown_id_is_ignored():
    # Four candidates for three slots, so the ranking pass actually runs; it
    # then names one id that exists and one that does not.
    answers = [
        candidates((0, 3, 90), (15, 18, 85), (30, 33, 70)),
        candidates((45, 48, 55)),
        {"ranked": [{"id": 999, "score": 99}, {"id": 0, "score": 95}]},
        META,
    ]
    clips, _ = run(answers)
    assert len(clips) == 1


def test_reversed_beat_ids_are_tolerated():
    answers = [
        candidates((3, 0, 90)),
        candidates((45, 48, 85)),
        {"ranked": [{"id": 0, "score": 95}, {"id": 1, "score": 80}]},
        META, META,
    ]
    clips, _ = run(answers)
    assert clips[0]["end_time"] > clips[0]["start_time"]


# ------------------------------------------------------------------ failures

def test_no_candidates_anywhere_is_an_explained_failure():
    with pytest.raises(AnalysisError) as info:
        run([{"candidates": []}, {"candidates": []}])
    assert "clippable" in str(info.value)


def test_when_every_window_fails_the_error_names_the_provider_not_the_transcript():
    """The recorded job: every window failed, and the user was told the video
    was "all housekeeping".

    The negative assertions are the point. A message that merely mentions the
    provider while still offering the transcript as an explanation sends the
    user to re-cut something that was never the problem.
    """
    boom = RuntimeError("Every provider in the chain failed (3 tried)")
    with pytest.raises(AnalysisError) as info:
        run([boom, boom, boom, boom, boom, boom])

    message = str(info.value)
    assert "never analysed" in message
    assert "Every provider in the chain failed" in message
    assert "housekeeping" not in message
    assert "does not match the video" not in message


def test_a_partial_scan_failure_is_named_alongside_the_empty_result():
    """Some windows answered, none found anything, others never ran.

    The transcript explanation is still offered -- windows did answer -- but it
    is no longer the whole story.
    """
    with pytest.raises(AnalysisError) as info:
        run([{"candidates": []}, RuntimeError("504"), {"candidates": []}])

    message = str(info.value)
    assert "clippable" in message
    assert "never considered" in message


def test_a_partially_scanned_transcript_says_so_in_the_shortfall():
    """A shortfall after a failed window is not evidence about the video."""
    answers = [
        candidates((0, 3, 90)),
        RuntimeError("InternalServerError: 504"),
        META,
    ]
    clips, logs = run(answers)
    joined = "\n".join(logs)

    assert len(clips) == 1
    assert "Asked for 3" in joined and "yielded 1" in joined
    assert "not a verdict on the transcript" in joined
    assert "did not contain a moment that stands on its own" not in joined


def test_an_empty_transcript_is_refused_before_any_request():
    runner = scripted()
    with pytest.raises(AnalysisError):
        analyzer.analyze([], Cfg(), chain=[("groq", "m")], keys={"groq": "k"},
                         on_log=lambda *a: None, run_chain=runner)
    assert runner.calls == []


def test_candidates_that_cannot_fit_the_window_are_explained():
    class LongOnly(Cfg):
        platform = "long"   # 60-179s, against a transcript of ~4s sentences

    # A transcript far too short to reach the 60s floor, however much it grows.
    with pytest.raises(AnalysisError) as info:
        run([candidates((0, 0, 90))], cfg=LongOnly(), segments=segmen(4))
    assert "duration window" in str(info.value)


# ------------------------------------------------------------- clip counting

def test_fewer_clips_than_asked_for_is_reported_not_hidden():
    """DEC-021's other side: a supply limit is not a provider limit, but hiding
    it would be the same failure DEC-021 was written about."""
    answers = [
        candidates((0, 3, 90)),
        candidates((45, 48, 85)),
        {"ranked": [{"id": 0, "score": 95}, {"id": 1, "score": 80}]},
        META, META,
    ]
    clips, logs = run(answers)
    assert len(clips) == 2
    assert any("Asked for 3" in line and "yielded 2" in line for line in logs)


def test_the_requested_count_is_never_exceeded():
    answers = [
        candidates((0, 3, 90), (20, 23, 85)),
        candidates((45, 48, 80), (55, 58, 75)),
        {"ranked": [{"id": i, "score": 90 - i} for i in range(6)]},
        META, META, META,
    ]
    clips, _ = run(answers)
    assert len(clips) == Cfg.jumlah_clip


# ------------------------------------------------------------------ language

def test_an_explicit_output_language_is_used():
    class French(Cfg):
        output_language = "fr"

    answers = [candidates((0, 3, 90)), candidates((45, 48, 85)),
               {"ranked": [{"id": 0, "score": 95}]}, META]
    _, logs = run(answers, cfg=French())
    assert any("French" in line for line in logs)


def test_auto_language_detects_from_the_transcript():
    class Auto(Cfg):
        output_language = "auto"

    answers = [candidates((0, 3, 90)), candidates((45, 48, 85)),
               {"ranked": [{"id": 0, "score": 95}]}, META]
    _, logs = run(answers, cfg=Auto())
    assert any("Transcript reads as" in line for line in logs)


def test_a_language_reported_by_the_transcription_provider_wins_over_detection():
    class Reported(Cfg):
        output_language = "auto"
        detected_language = "pt"

    answers = [candidates((0, 3, 90)), candidates((45, 48, 85)),
               {"ranked": [{"id": 0, "score": 95}]}, META]
    _, logs = run(answers, cfg=Reported())
    assert any("Portuguese" in line for line in logs)


# ------------------------------------------------------------ request shape

def test_no_single_request_asks_for_a_large_generation():
    """The whole reason the previous design could not work: ~1200 output tokens
    per clip in one call, against a provider measured at 12-13 tokens/s behind
    a ~300s gateway."""
    answers = [candidates((0, 3, 90)), candidates((45, 48, 85)),
               {"ranked": [{"id": 0, "score": 95}]}, META]
    runner = scripted(*answers)
    analyzer.analyze(segmen(), Cfg(), chain=[("groq", "m")], keys={"groq": "k"},
                     on_log=lambda *a: None, run_chain=runner)

    assert runner.calls
    for call in runner.calls:
        assert call["max_tokens"] <= 900


def test_every_request_carries_a_schema_and_the_deadline():
    answers = [candidates((0, 3, 90)), candidates((45, 48, 85)),
               {"ranked": [{"id": 0, "score": 95}]}, META]
    runner = scripted(*answers)
    analyzer.analyze(segmen(), Cfg(), chain=[("groq", "m")], keys={"groq": "k"},
                     on_log=lambda *a: None, run_chain=runner)

    for call in runner.calls:
        assert call["schema"] is not None
        assert call["schema_name"]
        assert call["deadline"] is not None


# ------------------------------------------------ the per-window time share

class Clock:
    """A fake monotonic clock a scripted request can push forward."""

    def __init__(self, start=0.0):
        self.now = float(start)

    def __call__(self):
        return self.now


# Groq's 120s request timeout, so five windows fit inside the pass-A pool.
# NVIDIA's 330s would not, and "six windows cannot each have a 330s request
# inside a 900s budget" is arithmetic, not a bug.
GROQ_CHAIN = [Link("groq", "m")]
GROQ_KEYS = {"groq": "k"}


def greedy(clock, floor=120.0, answers=()):
    """A run_chain stand-in that behaves like the real one under a deadline.

    Two properties are copied from ``llm.run_chain``, and both are load-bearing
    for these tests. It **refuses** an allowance too small for one request --
    that is Stage 2's predictive check, and without it a fake runner happily
    "succeeds" on a window that the real chain would never have contacted, which
    hides the exact bug under test. And it **spends its whole allowance**, the
    worst honest case for a window that does get to run.
    """
    queue = list(answers)
    seen = []

    def _run(chain, **kwargs):
        deadline = kwargs["deadline"]
        allowance = deadline - clock.now
        if allowance < floor:
            raise RuntimeError(
                f"Every provider in the chain failed: a {floor:.0f}s request "
                f"does not fit the {max(0.0, allowance):.0f}s left in the budget"
            )
        seen.append({"deadline": deadline, "at": clock.now, "allowance": allowance})
        clock.now = deadline
        if not queue:
            raise RuntimeError("InternalServerError: Error code: 504")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, chain[0]

    _run.seen = seen
    return _run


def analyze_with(runner, clock, cfg=None, segments=None):
    logs = []
    return analyzer.analyze(
        segments or segmen(200),
        cfg or Cfg(),
        chain=GROQ_CHAIN,
        keys=GROQ_KEYS,
        on_log=logs.append,
        time_fn=clock,
        run_chain=runner,
    ), logs


def test_one_slow_window_cannot_starve_the_others():
    """The recorded failure, inverted.

    Window 1's ladder spent ~925s of a 900s budget against a provider that
    answered nothing, and windows 2-6 were skipped without ever being tried.
    DEC-027 says a failed window loses one window's candidates; it lost the run.

    The invariant is that the later windows are *attempted*, not that they
    succeed -- here every one of them fails too.
    """
    clock = Clock()
    runner = greedy(clock)

    with pytest.raises(AnalysisError):
        analyze_with(runner, clock)

    assert len(runner.seen) == 5, (
        "every window must be given a workable share; a greedy first window "
        "must not consume the other four windows' time"
    )


def test_no_window_is_handed_the_whole_runs_budget():
    """Each window's allowance is a share, not the pool."""
    clock = Clock()
    runner = greedy(clock)

    with pytest.raises(AnalysisError):
        analyze_with(runner, clock)

    budget = Cfg.analysis_budget_seconds
    assert runner.seen[0]["allowance"] < budget


def test_unused_time_returns_to_the_pool():
    """A fast window leaves more for the windows after it, with no accumulator.

    The share is recomputed from ``remaining / windows_left`` at the top of each
    iteration, so this falls out of the arithmetic rather than being tracked.
    """
    clock = Clock()
    seen = []

    def runner(chain, **kwargs):
        seen.append(kwargs["deadline"] - clock.now)
        clock.now += 1.0            # answers almost instantly
        return candidates((0, 2, 60)), chain[0]

    logs = []
    analyzer.analyze(
        segmen(200), Cfg(), chain=GROQ_CHAIN, keys=GROQ_KEYS,
        on_log=logs.append, time_fn=clock, run_chain=runner,
    )
    scan_allowances = seen[:3]
    assert scan_allowances[1] > scan_allowances[0]


def test_a_window_always_gets_room_for_one_full_request():
    """The floor, and the regression it prevents.

    A strict ``remaining / windows_left`` share gives 900s over six windows as
    150s each -- below a 330s request timeout -- so the predictive check would
    refuse every window and the run would issue no requests at all. That trades
    a starvation bug for a never-tries bug.
    """
    clock = Clock()
    runner = greedy(clock, floor=330.0)

    with pytest.raises(AnalysisError):
        analyzer.analyze(
            segmen(200), Cfg(), chain=[Link("nvidia", "m")], keys={"nvidia": "k"},
            on_log=lambda *a: None, time_fn=clock, run_chain=runner,
        )

    assert runner.seen, "at least one window must actually be tried"
    assert runner.seen[0]["allowance"] >= 330.0


def test_a_window_with_no_room_left_is_skipped_and_says_why():
    clock = Clock()
    runner = greedy(clock, floor=330.0)
    logs = []

    with pytest.raises(AnalysisError) as info:
        analyzer.analyze(
            segmen(200), Cfg(), chain=[Link("nvidia", "m")], keys={"nvidia": "k"},
            on_log=logs.append, time_fn=clock, run_chain=runner,
        )

    joined = "\n".join(logs)
    assert "skipped" in joined and "budget" in joined
    assert "skipped for want of time" in str(info.value)
    assert "housekeeping" not in str(info.value)


def test_a_keyless_link_does_not_shrink_every_windows_allowance():
    """A link that is never contacted costs no time, so it sets no floor.

    Counting NVIDIA's 330s here would shrink the allowance of every window in a
    run that cannot use NVIDIA at all.
    """
    assert analyzer._request_floor(
        [Link("nvidia", "m"), Link("groq", "m")], {"groq": "k"}
    ) == 120.0


def test_an_unreadable_chain_falls_back_to_the_slowest_timeout():
    """Tuples and unknown providers must not take a run down, and must not
    produce a floor so small that nothing is ever tried."""
    assert analyzer._request_floor([("groq", "m")], {"groq": "k"}) == 330.0
    assert analyzer._request_floor([], {}) == 330.0


def test_pass_c_refuses_metadata_predictively():
    """DEC-027 locality: a clip with no time left for metadata still renders.

    Pass A cannot starve pass C -- PASS_A_BUDGET_SHARE holds time back for it --
    so the way to run C dry is for C's own earlier requests to be slow, which is
    what this scripts. The first clip gets its title; the rest are refused
    *before* a request is made, and render with a basic one.
    """
    clock = Clock()
    calls = []

    def runner(chain, **kwargs):
        calls.append(kwargs["schema_name"])
        if kwargs["schema_name"] == "candidates":
            clock.now = kwargs["deadline"]          # spend the window's share
            window = len([c for c in calls if c == "candidates"])
            if window == 1:
                return candidates((0, 3, 90), (20, 23, 85)), chain[0]
            return candidates((45, 48, 80)), chain[0]
        clock.now += 200.0                          # a slow metadata request
        return META, chain[0]

    logs = []
    clips = analyzer.analyze(
        segmen(), Cfg(), chain=GROQ_CHAIN, keys=GROQ_KEYS,
        on_log=logs.append, time_fn=clock, run_chain=runner,
    )

    assert len(clips) == 3, "every selected clip still renders"
    assert calls.count("clip_meta") == 1, "the rest were refused before the request"
    assert any("basic title" in line for line in logs)
    for clip in clips:
        assert clip["title_inggris"]


def test_a_granted_window_is_actually_attempted_by_the_chain():
    """The epsilon that reintroduced the never-tries bug, found on a real job.

    ``run_chain`` measures the remaining time slightly *after* this module grants
    it, so a window handed exactly one request's worth is handed slightly less
    than that by the time it is checked, and is refused. Every window but the
    last was skipped with "a 330s request does not fit the 330s left in the time
    budget" -- the exact failure the floor exists to prevent.

    The runner here mimics ``run_chain`` faithfully: it reads the clock itself
    rather than trusting the allowance it was handed.
    """
    clock = Clock()
    attempts = []

    def runner(chain, **kwargs):
        clock.now += 0.001                       # rendering, prompt building
        if 330.0 > kwargs["deadline"] - clock.now:
            raise RuntimeError("a 330s request does not fit the time budget")
        attempts.append(kwargs["deadline"])
        clock.now += 15.0
        return candidates((0, 2, 60)), chain[0]

    analyzer.analyze(
        segmen(200), Cfg(), chain=[Link("nvidia", "m")], keys={"nvidia": "k"},
        on_log=lambda *a: None, time_fn=clock, run_chain=runner,
    )

    assert len(attempts) >= 5, (
        "a window granted one request's worth must survive the clock moving "
        "between the grant and the check"
    )


# ------------------------------------------- the moment keeps its description

def _pass_b_call(calls):
    """The re-rank request out of a scripted run's recorded calls."""
    return next(c for c in calls if c["schema_name"] == "ranked")


def _clip_meta_calls(calls):
    return [c for c in calls if c["schema_name"] == "clip_meta"]


def test_the_rerank_lines_name_the_kind_and_gist():
    """Pass B ranks candidates against each other. Until the snapped-id lookup
    was fixed it saw ``clip`` and an empty gist for every candidate whose
    boundary ``snap`` had moved -- which is most of them, because growing
    toward the preset target moves nearly every boundary."""
    runner = scripted(
        candidates((0, 3, 90), (15, 18, 85), (30, 33, 70)),
        candidates((45, 48, 60)),
        {"ranked": [{"id": 0, "score": 95}, {"id": 1, "score": 80},
                    {"id": 2, "score": 70}]},
        META, META, META,
    )
    analyzer.analyze(
        segmen(), Cfg(), chain=[("groq", "m")], keys={"groq": "k"},
        on_log=lambda _line: None, run_chain=runner,
    )

    rerank = _pass_b_call(runner.calls)["user"]
    assert "a thing happens" in rerank
    assert "story" in rerank


def test_pass_c_is_told_what_kind_of_moment_it_is():
    """``clip_meta_prompt`` has always accepted kind/gist; nothing passed them."""
    runner = scripted(
        candidates((0, 3, 90)),
        candidates((45, 48, 85)),
        {"ranked": [{"id": 0, "score": 95}, {"id": 1, "score": 80}]},
        META, META,
    )
    analyzer.analyze(
        segmen(), Cfg(), chain=[("groq", "m")], keys={"groq": "k"},
        on_log=lambda _line: None, run_chain=runner,
    )

    metas = _clip_meta_calls(runner.calls)
    assert metas, "no metadata request was made"
    assert all("picked as a story" in c["user"] for c in metas)
    assert all("a thing happens" in c["user"] for c in metas)


# ``viral_score`` prefers the metadata pass's own score (adapter.py:92), so the
# shared META would mask which candidate pass B actually picked. This fixture
# omits it, leaving the span's score -- the one the ranking wrote -- visible.
META_NO_SCORE = {k: v for k, v in META.items() if k != "score"}

# --------------------------------------------------- what the re-rank can see

def test_pass_b_is_shown_each_candidates_opening_line():
    """The hook line is what decides whether a stranger keeps watching, and it
    was the one thing the ranking pass could not see."""
    runner = scripted(
        candidates((0, 3, 90), (15, 18, 85), (30, 33, 70)),
        candidates((45, 48, 60)),
        {"ranked": [{"id": 0, "score": 95, "topic": "alpha"},
                    {"id": 1, "score": 80, "topic": "beta"},
                    {"id": 2, "score": 70, "topic": "gamma"}]},
        META, META, META,
    )
    analyzer.analyze(
        segmen(), Cfg(), chain=[("groq", "m")], keys={"groq": "k"},
        on_log=lambda _line: None, run_chain=runner,
    )

    rerank = _pass_b_call(runner.calls)["user"]
    assert "hook:" in rerank
    # segmen() writes "sentence number N says something interesting here."
    assert "sentence number" in rerank


def test_a_long_hook_line_is_truncated():
    long_words = " ".join(f"word{i}" for i in range(60))
    segments = [
        {"start": 0.0, "end": 1.0,
         "words": [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25}
                   for i, w in enumerate(long_words.split())]}
    ]
    line = analyzer._hook_line({"text": long_words})
    assert line.endswith("…")
    assert len(line.split()) <= 16  # 15 words plus the ellipsis marker


# ------------------------------------------------------- variety, in Python

def test_a_repeated_topic_is_demoted_below_a_fresh_one():
    """Five versions of one point is worse than five different points, and the
    instruction alone does not reliably produce that."""
    cfg = Cfg()
    cfg.jumlah_clip = 2
    answers = [
        candidates((0, 3, 95), (15, 18, 90), (30, 33, 60)),
        candidates((45, 48, 55)),
        {"ranked": [{"id": 0, "score": 95, "topic": "pricing"},
                    {"id": 1, "score": 90, "topic": "pricing"},
                    {"id": 2, "score": 60, "topic": "burnout"}]},
        META_NO_SCORE, META_NO_SCORE,
    ]
    clips, logs = run(answers, cfg=cfg)

    # The second pricing clip loses its slot to the one about something else.
    assert len(clips) == 2
    assert [c["viral_score"] for c in clips] == [95, 60]
    assert any("demoted" in line for line in logs)


def test_a_topic_is_matched_case_and_punctuation_insensitively():
    cfg = Cfg()
    cfg.jumlah_clip = 2
    answers = [
        candidates((0, 3, 95), (15, 18, 90), (30, 33, 60)),
        candidates((45, 48, 55)),
        {"ranked": [{"id": 0, "score": 95, "topic": "Pricing."},
                    {"id": 1, "score": 90, "topic": "  pricing  "},
                    {"id": 2, "score": 60, "topic": "burnout"}]},
        META_NO_SCORE, META_NO_SCORE,
    ]
    clips, _ = run(answers, cfg=cfg)
    assert [c["viral_score"] for c in clips] == [95, 60]


def test_variety_never_returns_fewer_clips_than_asked_for():
    """DEC-021: a supply limit is not a silent clip-count change. A video
    genuinely about one subject still yields what was asked for."""
    cfg = Cfg()
    cfg.jumlah_clip = 3
    answers = [
        candidates((0, 3, 95), (15, 18, 90), (30, 33, 85)),
        candidates((45, 48, 80)),
        {"ranked": [{"id": 0, "score": 95, "topic": "pricing"},
                    {"id": 1, "score": 90, "topic": "pricing"},
                    {"id": 2, "score": 85, "topic": "pricing"}]},
        META, META, META,
    ]
    clips, logs = run(answers, cfg=cfg)
    assert len(clips) == 3
    assert any("demoted" in line for line in logs)


def test_a_missing_topic_is_not_treated_as_a_duplicate():
    """Two blanks are not the same subject; they are two unknowns."""
    cfg = Cfg()
    cfg.jumlah_clip = 2
    answers = [
        candidates((0, 3, 95), (15, 18, 90), (30, 33, 60)),
        candidates((45, 48, 55)),
        {"ranked": [{"id": 0, "score": 95, "topic": ""},
                    {"id": 1, "score": 90, "topic": ""},
                    {"id": 2, "score": 60, "topic": "burnout"}]},
        META_NO_SCORE, META_NO_SCORE,
    ]
    clips, _ = run(answers, cfg=cfg)
    assert len(clips) == 2
    assert [c["viral_score"] for c in clips] == [95, 90]


def test_a_ranking_without_topics_at_all_still_works():
    """An older or weaker model that omits the field must not break the run."""
    cfg = Cfg()
    cfg.jumlah_clip = 2
    answers = [
        candidates((0, 3, 95), (15, 18, 90), (30, 33, 60)),
        candidates((45, 48, 55)),
        {"ranked": [{"id": 0, "score": 95}, {"id": 1, "score": 90}]},
        META, META,
    ]
    clips, _ = run(answers, cfg=cfg)
    assert len(clips) == 2


# ------------------------------------------------ what the scan prompt can see

def _pass_a_call(calls):
    return next(c for c in calls if c["schema_name"] == "candidates")


def _scan_prompt(cfg=None, segments=None):
    runner = scripted(
        candidates((0, 3, 90)),
        candidates((45, 48, 85)),
        {"ranked": [{"id": 0, "score": 95, "topic": "a"},
                    {"id": 1, "score": 80, "topic": "b"}]},
        META, META,
    )
    analyzer.analyze(
        segments or segmen(), cfg or Cfg(), chain=[("groq", "m")],
        keys={"groq": "k"}, on_log=lambda _line: None, run_chain=runner,
    )
    return _pass_a_call(runner.calls)["user"]


def test_the_scan_prompt_puts_the_beats_before_the_rules():
    """Instructions read against material already seen beat instructions read
    against nothing."""
    prompt = _scan_prompt()
    assert prompt.index("BEATS:") < prompt.index("RULES")


def test_the_scan_prompt_states_the_video_context():
    """Every window judged "standalone clarity" without knowing what the video
    was, how long it ran, or what language it was in."""
    cfg = Cfg()
    cfg.output_language = "fr"
    prompt = _scan_prompt(cfg=cfg)
    assert "French" in prompt
    assert "minutes long" in prompt


def test_a_topic_reaches_the_scan_prompt():
    cfg = Cfg()
    cfg.topic = "home espresso gear"
    assert "home espresso gear" in _scan_prompt(cfg=cfg)


def test_no_topic_leaves_no_dangling_label():
    """An empty --topic must not print a header with nothing under it."""
    prompt = _scan_prompt()
    assert "home espresso" not in prompt
    assert "WHAT IT IS ABOUT" not in prompt


def test_the_scan_prompt_calibrates_the_score():
    """A score given with no scale is a number the re-rank cannot trust."""
    prompt = _scan_prompt()
    assert "SCORING" in prompt
    assert "90-100" in prompt


def test_the_scan_prompt_shows_a_good_and_a_bad_example():
    prompt = _scan_prompt()
    assert "GOOD" in prompt and "BAD" in prompt
    assert "do not look for them above" in prompt
