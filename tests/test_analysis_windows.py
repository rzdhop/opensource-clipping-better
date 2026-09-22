"""The three-pass analyzer's orchestration, against a scripted chain.

No network: ``run_chain`` is injected. These tests pin the properties that keep
a model's answer from reaching the render layer unchecked.
"""

import pytest

from clipping.analysis import analyzer, presets
from clipping.analysis.analyzer import AnalysisError


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
