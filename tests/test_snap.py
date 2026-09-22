"""Deterministic clip boundaries.

The model picks which beats are interesting; nothing here asks it where a cut
lands. These are the properties that makes that safe. Stdlib only.
"""

import pytest

from clipping.analysis import presets as P
from clipping.analysis import snap as S


def beats(n=40, length=5.0, gap=0.5):
    """*n* evenly spaced beats, each *length* long with *gap* of silence after."""
    out = []
    t = 0.0
    for i in range(n):
        out.append(
            {"i": i, "start": t, "end": t + length, "text": f"beat {i}",
             "n_words": 8, "w0": i * 8, "w1": i * 8 + 7}
        )
        t += length + gap
    return out


TIKTOK = P.get("tiktok")
SHORTS = P.get("shorts")


# ------------------------------------------------------------------- presets

def test_every_preset_is_coherent():
    for name, preset in P.PRESETS.items():
        assert 0 < preset.min < preset.max, name
        assert preset.min <= preset.target <= preset.max, name


def test_an_unknown_platform_falls_back_to_auto():
    """A misspelt platform should produce clips that work everywhere, not abort
    a run that has already paid for its transcription."""
    assert P.get("tikok") is P.PRESETS["auto"]
    assert P.get(None) is P.PRESETS["auto"]
    assert P.get("") is P.PRESETS["auto"]


def test_get_accepts_a_preset_unchanged():
    assert P.get(TIKTOK) is TIKTOK


def test_long_preserves_the_pipelines_previous_bounds():
    assert P.PRESETS["long"].min == 60.0
    assert P.PRESETS["long"].max == 179.0


# ---------------------------------------------------------------- the property

@pytest.mark.parametrize("preset_name", list(P.PRESETS))
@pytest.mark.parametrize("b0,b1", [(0, 0), (0, 1), (5, 9), (10, 10), (30, 39)])
def test_a_snapped_span_is_in_bounds_or_refused(preset_name, b0, b1):
    """The property the whole design rests on: never an out-of-bounds clip."""
    preset = P.get(preset_name)
    result = S.snap(b0, b1, beats(), preset)
    if result.span is None:
        assert result.reason
        return
    duration = result.span.end - result.span.start
    assert preset.min - 1e-9 <= duration <= preset.max + 1e-9


def test_a_short_candidate_grows_toward_the_target():
    result = S.snap(4, 4, beats(), TIKTOK)
    assert result.span is not None
    assert result.span.b1 > result.span.b0
    assert result.span.end - result.span.start >= TIKTOK.min


def test_growth_prefers_forward():
    """A candidate that is too short usually stops before its own payoff."""
    result = S.snap(10, 10, beats(), TIKTOK)
    assert result.span.b0 == 10
    assert result.span.b1 > 10


def test_growth_falls_back_to_earlier_beats_at_the_end_of_the_video():
    all_beats = beats(n=12)
    result = S.snap(11, 11, all_beats, TIKTOK)
    assert result.span is not None
    assert result.span.b0 < 11


def test_trimming_keeps_the_payoff_at_the_end():
    """The payoff of a short-form clip is its last line. Trimming from the end
    to fit a window produces a clip that stops before the reason it was picked."""
    result = S.snap(0, 30, beats(), SHORTS)
    assert result.span is not None
    assert result.span.b1 == 30
    assert result.span.b0 > 0


# -------------------------------------------------------------------- padding

def test_padding_never_reaches_into_a_neighbouring_beat():
    """The single most recognisable sign of an auto-generated cut."""
    all_beats = beats(gap=0.05)
    start, end = S.pad(3, 5, all_beats, lead_in=0.5, tail=0.5)
    assert start >= all_beats[2]["end"]
    assert end <= all_beats[6]["start"]


def test_padding_is_applied_when_there_is_room():
    all_beats = beats(gap=2.0)
    start, end = S.pad(3, 5, all_beats, lead_in=0.15, tail=0.35)
    assert start == pytest.approx(all_beats[3]["start"] - 0.15)
    assert end == pytest.approx(all_beats[5]["end"] + 0.35)


def test_the_tail_is_capped_even_with_unlimited_room():
    """A long silence after the payoff should not be part of the clip."""
    all_beats = beats(gap=30.0)
    _, end = S.pad(1, 1, all_beats, tail=10.0)
    assert end - all_beats[1]["end"] == pytest.approx(S.MAX_TAIL)


def test_the_first_beat_never_starts_before_zero():
    all_beats = beats()
    start, _ = S.pad(0, 1, all_beats, lead_in=5.0)
    assert start >= 0.0


def test_the_last_beat_still_gets_a_tail():
    all_beats = beats(n=5)
    _, end = S.pad(4, 4, all_beats, tail=0.35)
    assert end > all_beats[4]["end"]


# ------------------------------------------------------------------- failures

def test_a_nonexistent_beat_is_refused_with_a_reason():
    result = S.snap(0, 999, beats(), TIKTOK)
    assert result.span is None
    assert "999" in result.reason


def test_a_reversed_range_is_tolerated():
    forward = S.snap(3, 7, beats(), TIKTOK)
    backward = S.snap(7, 3, beats(), TIKTOK)
    assert forward.span == backward.span


def test_a_transcript_too_short_for_the_preset_is_refused():
    result = S.snap(0, 0, beats(n=1, length=2.0), TIKTOK)
    assert result.span is None
    assert "tiktok" in result.reason and "15" in result.reason


def test_a_single_unsplittable_beat_over_the_maximum_is_refused():
    one = [{"i": 0, "start": 0.0, "end": 200.0, "text": "x", "n_words": 1,
            "w0": 0, "w1": 0}]
    result = S.snap(0, 0, one, TIKTOK)
    assert result.span is None
    assert "longer than" in result.reason


# --------------------------------------------------------------------- dedupe

def _span(start, end, score=50):
    return S.Span(start, end, 0, 1, score)


def test_overlap_is_measured_against_the_shorter_span():
    """Three seconds shared with a 60s clip is incidental; three shared with a
    15s clip is most of its hook."""
    long_span = _span(0.0, 60.0)
    short_span = _span(57.0, 72.0)
    assert S.overlap_ratio(long_span, short_span) == pytest.approx(3.0 / 15.0)


def test_disjoint_spans_do_not_overlap():
    assert S.overlap_ratio(_span(0.0, 10.0), _span(20.0, 30.0)) == 0.0
    assert S.overlap_ratio(_span(0.0, 10.0), _span(10.0, 20.0)) == 0.0


def test_dedupe_keeps_the_higher_score():
    kept = S.dedupe([_span(0.0, 30.0, 40), _span(1.0, 31.0, 90)])
    assert len(kept) == 1
    assert kept[0].score == 90


def test_dedupe_keeps_genuinely_distinct_clips():
    spans = [_span(0.0, 30.0, 90), _span(100.0, 130.0, 80), _span(300.0, 330.0, 70)]
    assert len(S.dedupe(spans)) == 3


def test_dedupe_allows_a_touch_of_shared_padding():
    """Adjacent beats routinely share a fraction of a second; rejecting on that
    would throw away distinct clips."""
    spans = [_span(0.0, 30.0, 90), _span(29.0, 59.0, 80)]
    assert len(S.dedupe(spans, max_overlap_ratio=0.15)) == 2


def test_dedupe_returns_chronological_order():
    spans = [_span(300.0, 330.0, 70), _span(0.0, 30.0, 90), _span(100.0, 130.0, 80)]
    assert [s.start for s in S.dedupe(spans)] == [0.0, 100.0, 300.0]


def test_dedupe_of_nothing_is_nothing():
    assert S.dedupe([]) == []


# ------------------------------------------------------------------- snap_all

def test_snap_all_reports_what_it_dropped():
    """A moment the model liked and the snapper refused should be visible in the
    log, not silently vanish."""
    rejected = []
    spans = S.snap_all(
        [(0, 2, 90), (999, 1000, 80)],
        beats(),
        TIKTOK,
        on_reject=lambda b0, b1, reason: rejected.append((b0, b1, reason)),
    )
    assert len(spans) == 1
    assert len(rejected) == 1
    assert rejected[0][0] == 999


def test_snap_all_carries_the_score_through():
    spans = S.snap_all([(0, 2, 77)], beats(), TIKTOK)
    assert spans[0].score == 77


def test_snapped_spans_never_overlap_after_dedupe():
    """End to end: the two guarantees the render loop depends on."""
    candidates = [(i, i + 2, 100 - i) for i in range(0, 30, 2)]
    spans = S.dedupe(S.snap_all(candidates, beats(), TIKTOK))
    for a, b in zip(spans, spans[1:]):
        assert S.overlap_ratio(a, b) <= 0.15
        assert a.start <= b.start


# -------------------------------------------------- the candidate's own words

def test_span_is_still_constructible_positionally():
    """Five positional fields, as three existing call sites build them.

    ``gist``/``kind`` are appended with defaults precisely so those sites --
    and any caller in the wild -- keep working unchanged.
    """
    span = S.Span(0.0, 40.0, 0, 8, 88)
    assert span.score == 88
    assert span.gist == ""
    assert span.kind == ""


def test_snap_all_carries_the_gist_through_a_moved_boundary():
    """The bug this fixes: ``snap`` rewrites b0/b1 while growing toward the
    target, so a lookup keyed on the candidate's ORIGINAL ids misses and the
    re-rank sees an anonymous duration instead of the moment it named."""
    all_beats = beats()
    candidate = {"b0": 0, "b1": 1, "score": 88, "gist": "loses the account",
                 "kind": "story"}

    spans = S.snap_all([candidate], all_beats, TIKTOK)

    assert len(spans) == 1
    # Growing really did move the boundary -- otherwise this test proves nothing.
    assert (spans[0].b0, spans[0].b1) != (candidate["b0"], candidate["b1"])
    assert spans[0].gist == "loses the account"
    assert spans[0].kind == "story"


def test_snap_all_still_accepts_a_bare_triple():
    """The old shape stays valid; gist and kind simply come back empty."""
    spans = S.snap_all([(0, 2, 77)], beats(), TIKTOK)
    assert spans[0].score == 77
    assert spans[0].gist == ""


def test_rescoring_a_span_keeps_its_gist():
    """``_pass_b`` rewrites the score with ``_replace``; the words must survive."""
    span = S.Span(0.0, 40.0, 0, 8, 50, "loses the account", "story")
    assert span._replace(score=95).gist == "loses the account"
