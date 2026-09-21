"""Sentence-level beats — the unit the model is allowed to reason in.

The model only ever emits beat ids, so a hallucinated timestamp is structurally
impossible. These tests pin that property and the three cut rules.

Stdlib only.
"""

import pytest

from conftest import FIXTURES

from clipping import transcript
from clipping.analysis import beats as B


def words(*specs):
    """``("hello", 0.0, 0.5)`` triples -> the word dicts the parsers produce."""
    return [{"word": w, "start": float(s), "end": float(e)} for w, s, e in specs]


def segmen(word_list, per=5):
    """Wrap a flat word list into data_segmen chunks, as the parsers do."""
    out = []
    for i in range(0, len(word_list), per):
        chunk = word_list[i : i + per]
        out.append({"start": chunk[0]["start"], "end": chunk[-1]["end"], "words": chunk})
    return out


def _even(text, start=0.0, step=0.4):
    """Evenly-spaced words, the shape an untagged caption file produces."""
    return [
        {"word": w, "start": start + i * step, "end": start + (i + 1) * step - 0.01}
        for i, w in enumerate(text.split())
    ]


# ------------------------------------------------------------------ cut rules

def test_a_sentence_boundary_cuts():
    beats = B.build_beats(segmen(_even("one two three. four five six.")), min_beat_seconds=0.0)
    assert [b["text"] for b in beats] == ["one two three.", "four five six."]


@pytest.mark.parametrize("mark", [".", "!", "?", "…", "。", "？", "؟"])
def test_every_sentence_mark_cuts(mark):
    beats = B.build_beats(
        segmen(_even(f"one two{mark} three four")), min_beat_seconds=0.0
    )
    assert len(beats) == 2


def test_a_closing_quote_after_the_stop_still_cuts():
    beats = B.build_beats(segmen(_even('he said "no." then left')), min_beat_seconds=0.0)
    assert len(beats) == 2


def test_a_pause_cuts_even_without_punctuation():
    """The only boundary a scraped auto-caption offers: it has no punctuation."""
    flat = words(("no", 0.0, 0.4), ("punctuation", 0.4, 1.0),
                 ("here", 3.0, 3.4), ("at", 3.4, 3.8), ("all", 3.8, 4.5))
    beats = B.build_beats(segmen(flat), max_pause=0.6, min_beat_seconds=0.0)
    assert [b["text"] for b in beats] == ["no punctuation", "here at all"]


def test_a_long_run_is_capped():
    """One unpunctuated monologue must not become a beat spanning the video."""
    flat = [{"word": f"w{i}", "start": i * 1.0, "end": i * 1.0 + 0.9} for i in range(40)]
    beats = B.build_beats(segmen(flat), max_beat_seconds=8.0, min_beat_seconds=0.0)
    assert len(beats) > 1
    # A soft cap: the cut lands on the first word that crosses it, so a beat may
    # exceed it by at most one word's duration.
    longest_word = max(w["end"] - w["start"] for w in flat)
    for beat in beats:
        assert beat["end"] - beat["start"] <= 8.0 + longest_word


# ------------------------------------------------------------------- merging

def test_a_very_short_beat_is_folded_into_its_predecessor():
    flat = _even("this is a whole sentence here. ok.", step=0.5)
    beats = B.build_beats(segmen(flat), min_beat_seconds=1.2)
    assert len(beats) == 1
    assert beats[0]["text"].endswith("ok.")


def test_merging_never_bridges_a_pause():
    """Found by measuring a real transcript: a short beat 18 seconds after its
    predecessor was merged into it, producing a 25.3s 'beat' that jumped between
    two unrelated scenes — undoing the boundary the pause rule had just drawn."""
    flat = words(("a", 0.0, 0.5), ("long", 0.5, 1.0), ("sentence", 1.0, 2.0),
                 ("here", 2.0, 3.0),
                 ("isolated", 21.0, 21.4))   # 18s later, and short
    beats = B.build_beats(segmen(flat), max_pause=0.6, min_beat_seconds=1.2)

    assert len(beats) == 2
    assert beats[1]["text"] == "isolated"
    for beat in beats:
        assert beat["end"] - beat["start"] < 5.0


def test_merging_never_exceeds_the_cap():
    """Otherwise a run of short beats cascades into one long one."""
    flat = []
    for i in range(12):
        flat.extend(words((f"w{i}", i * 1.0, i * 1.0 + 0.3)))
    beats = B.build_beats(segmen(flat), max_pause=1.0, max_beat_seconds=4.0,
                          min_beat_seconds=2.0)
    longest_word = max(w["end"] - w["start"] for w in flat)
    for beat in beats:
        # The cutter's soft cap is max_beat_seconds plus at most one word; the
        # merge must not add to it.
        assert beat["end"] - beat["start"] == pytest.approx(
            min(beat["end"] - beat["start"], 4.0 + longest_word)
        )


# ---------------------------------------------------------------- the contract

def test_ids_are_contiguous_from_zero():
    """They are used as list indices after the merge pass."""
    beats = B.build_beats(segmen(_even("one. two. three. four. five.")))
    assert [b["i"] for b in beats] == list(range(len(beats)))


def test_no_word_is_lost_or_reordered():
    flat = _even("alpha bravo. charlie delta echo. foxtrot")
    beats = B.build_beats(segmen(flat), min_beat_seconds=0.0)
    recovered = " ".join(b["text"] for b in beats).split()
    assert recovered == [w["word"] for w in flat]


def test_indices_point_back_at_the_right_words():
    flat = _even("alpha bravo. charlie delta echo.")
    beats = B.build_beats(segmen(flat), min_beat_seconds=0.0)
    for beat in beats:
        chunk = flat[beat["w0"] : beat["w1"] + 1]
        assert " ".join(w["word"] for w in chunk) == beat["text"]
        assert beat["start"] == pytest.approx(chunk[0]["start"])
        assert beat["end"] == pytest.approx(chunk[-1]["end"])


def test_beats_are_chronological_and_non_degenerate():
    beats = B.build_beats(segmen(_even("one. two. three. four.")))
    for previous, beat in zip(beats, beats[1:]):
        assert beat["start"] >= previous["start"]
    for beat in beats:
        assert beat["end"] > beat["start"]
        assert beat["n_words"] >= 1
        assert beat["text"].strip()


def test_an_empty_transcript_yields_no_beats():
    assert B.build_beats([]) == []
    assert B.build_beats([{"start": 0.0, "end": 1.0, "words": []}]) == []


# ------------------------------------------------- lookups are the safety net

def test_span_of_rejects_an_id_that_does_not_exist():
    """The boundary where a hallucinated id becomes a caught error rather than
    a clip cut from nowhere."""
    beats = B.build_beats(segmen(_even("one. two.")))
    with pytest.raises(KeyError):
        B.span_of(beats, 0, 999)
    with pytest.raises(KeyError):
        B.span_of(beats, -4, 1)


def test_span_of_tolerates_a_reversed_range():
    beats = B.build_beats(segmen(_even("one. two. three.")), min_beat_seconds=0.0)
    assert B.span_of(beats, 2, 0) == B.span_of(beats, 0, 2)


def test_words_between_returns_exactly_those_words():
    flat = _even("alpha bravo. charlie delta. echo foxtrot.")
    beats = B.build_beats(segmen(flat), min_beat_seconds=0.0)
    got = B.words_between(beats, flat, 1, 1)
    assert [w["word"] for w in got] == ["charlie", "delta."]


# ------------------------------------------------------------------ rendering

def test_render_puts_the_id_first():
    beats = B.build_beats(segmen(_even("hello world.")), min_beat_seconds=0.0)
    line = B.render_beats(beats)
    assert line.startswith("#0 [")
    assert "hello world." in line


def test_render_honours_the_window():
    beats = B.build_beats(segmen(_even("one. two. three. four.")), min_beat_seconds=0.0)
    rendered = B.render_beats(beats, 1, 2)
    assert "#1" in rendered and "#2" in rendered
    assert "#0" not in rendered and "#3" not in rendered


# ------------------------------------------------------------------- windows

def test_windows_cover_every_beat():
    beats = B.build_beats(segmen(_even(" ".join(f"w{i}." for i in range(300)))),
                          min_beat_seconds=0.0)
    covered = set()
    for lo, hi in B.windows(beats, size=45, overlap=5):
        covered.update(range(lo, hi + 1))
    assert covered == {b["i"] for b in beats}


def test_windows_overlap_so_a_straddling_moment_is_seen_whole():
    beats = B.build_beats(segmen(_even(" ".join(f"w{i}." for i in range(300)))),
                          min_beat_seconds=0.0)
    ranges = B.windows(beats, size=45, overlap=5)
    assert len(ranges) > 1
    for (lo1, hi1), (lo2, _hi2) in zip(ranges, ranges[1:]):
        assert lo2 <= hi1, "consecutive windows must overlap"


def test_a_short_transcript_is_one_window():
    beats = B.build_beats(segmen(_even("one. two. three.")), min_beat_seconds=0.0)
    assert B.windows(beats, size=45) == [(0, beats[-1]["i"])]


def test_no_beats_means_no_windows():
    assert B.windows([]) == []


# --------------------------------------------------------- against real input

def test_real_scraped_transcript_produces_usable_beats():
    """The fixture that used to lose a third of its words (test_rolling_cues)."""
    _, segments = transcript.parse_vtt_subs(str(FIXTURES / "rolling_overlap.vtt"))
    beats = B.build_beats(segments, min_beat_seconds=0.0)

    assert beats
    recovered = " ".join(b["text"] for b in beats).split()
    assert len(recovered) == 14
    for beat in beats:
        assert beat["end"] > beat["start"]
