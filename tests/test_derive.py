"""Fields derived in Python that the model used to be asked for.

Deriving them removed ~1200 output tokens per clip AND made them correct: the
old prompt asked for emphasis words from the transcript and for b-roll to stay
out of the hook, and nothing checked either.

Stdlib only.
"""

import pytest

from clipping.analysis import derive


def beats(n=10, length=4.0, gap=0.5, start=0.0):
    out = []
    t = start
    for i in range(n):
        out.append({"i": i, "start": t, "end": t + length,
                    "text": f"beat {i} words here", "n_words": 4,
                    "w0": i * 4, "w1": i * 4 + 3})
        t += length + gap
    return out


def words(text, step=0.4):
    return [{"word": w, "start": i * step, "end": (i + 1) * step - 0.01}
            for i, w in enumerate(text.split())]


# ------------------------------------------------------------- normalization

@pytest.mark.parametrize("raw,expected", [
    ("Sure,", "sure"), ("WORD", "word"), ('"quoted"', "quoted"),
    ("don't", "don't"), ("île!", "île"), ("", ""),
])
def test_normalize_matches_the_render_layers_own_rule(raw, expected):
    """Must stay identical to subtitles.py:94 and :247. If it drifts, emphasis
    silently stops matching and every word renders unstyled."""
    assert derive.normalize_word(raw) == expected


# ------------------------------------------------------------------ emphasis

def test_only_words_actually_spoken_survive():
    spoken = words("twelve people stranded on a tiny island")
    plan = derive.typography_plan(["island", "hallucinated", "twelve"], spoken)
    assert [e["kata_utama"] for e in plan] == ["island", "twelve"] or \
           sorted(e["kata_utama"] for e in plan) == ["island", "twelve"]


def test_matching_ignores_case_and_punctuation():
    spoken = words("The Island, finally!")
    plan = derive.typography_plan(["ISLAND", "finally"], spoken)
    assert {e["kata_utama"] for e in plan} == {"island", "finally"}


def test_rarer_words_get_more_weight():
    """A better proxy for 'the word that matters' than the order the model
    happened to list them in."""
    spoken = words("island island island island rare twelve twelve")
    plan = derive.typography_plan(["island", "rare"], spoken)
    by_word = {e["kata_utama"]: e for e in plan}
    assert by_word["rare"]["scale_level"] > by_word["island"]["scale_level"]


def test_duplicates_are_collapsed():
    spoken = words("island island twelve")
    plan = derive.typography_plan(["island", "Island", "ISLAND"], spoken)
    assert len(plan) == 1


def test_the_plan_is_capped():
    spoken = words(" ".join(f"w{i}" for i in range(20)))
    plan = derive.typography_plan([f"w{i}" for i in range(20)], spoken, max_words=6)
    assert len(plan) == 6


def test_no_emphasis_is_an_empty_plan_not_an_error():
    assert derive.typography_plan(None, words("a b c")) == []
    assert derive.typography_plan([], words("a b c")) == []


def test_the_plan_is_deterministic():
    spoken = words("alpha bravo charlie delta")
    first = derive.typography_plan(["alpha", "bravo", "charlie"], spoken)
    second = derive.typography_plan(["alpha", "bravo", "charlie"], spoken)
    assert first == second


# ---------------------------------------------------------------------- hook

def test_the_hook_starts_at_the_named_beat():
    all_beats = beats()
    start, end = derive.hook_window(2, all_beats, clip_start=0.0, clip_end=40.0,
                                    hook_duration=3)
    assert start == pytest.approx(all_beats[2]["start"])
    assert end == pytest.approx(start + 3)


def test_a_hook_beat_outside_the_clip_falls_back_to_the_opening():
    start, _ = derive.hook_window(999, beats(), clip_start=5.0, clip_end=40.0,
                                  hook_duration=3)
    assert start == pytest.approx(5.0)


def test_the_hook_never_runs_past_the_clip():
    _, end = derive.hook_window(0, beats(), clip_start=0.0, clip_end=2.0,
                                hook_duration=10)
    assert end <= 2.0


def test_hook_v2_items_have_the_shape_core_py_reads():
    items = derive.hook_v2_items(beats(), want=3)
    assert 1 <= len(items) <= 3
    for item in items:
        assert set(item) == {"start_time", "end_time", "text"}
        assert item["text"] == item["text"].upper()
        assert item["end_time"] > item["start_time"]


def test_hook_v2_items_are_chronological():
    items = derive.hook_v2_items(beats(n=8), want=4)
    for a, b in zip(items, items[1:]):
        assert a["start_time"] <= b["start_time"]


# -------------------------------------------------------------------- b-roll

def test_broll_is_never_placed_over_the_hook():
    """The old prompt asked for this and nothing verified it."""
    all_beats = beats()
    entries = derive.broll_list(["a", "b"], all_beats, clip_start=0.0,
                                clip_end=45.0, hook_end=9.0)
    assert entries
    for entry in entries:
        assert entry["start_time"] >= 9.0


def test_broll_stays_inside_the_clip():
    entries = derive.broll_list(["a"], beats(), clip_start=0.0, clip_end=12.0,
                                hook_end=4.5)
    for entry in entries:
        assert entry["end_time"] <= 12.0


def test_broll_entries_do_not_touch_each_other():
    entries = derive.broll_list(["a", "b"], beats(n=12), clip_start=0.0,
                                clip_end=60.0, hook_end=0.0)
    for a, b in zip(entries, entries[1:]):
        assert b["start_time"] >= a["end_time"]


def test_broll_is_empty_when_disabled():
    assert derive.broll_list(["a"], beats(), clip_start=0.0, clip_end=40.0,
                             hook_end=0.0, enabled=False) == []


def test_broll_is_empty_with_no_queries():
    assert derive.broll_list([], beats(), clip_start=0.0, clip_end=40.0,
                             hook_end=0.0) == []


def test_broll_entries_have_the_shape_core_py_reads():
    for entry in derive.broll_list(["tropical island"], beats(), clip_start=0.0,
                                   clip_end=40.0, hook_end=0.0):
        assert set(entry) == {"start_time", "end_time", "search_query"}


# ------------------------------------------------------------- keep_segments

def test_dropping_a_middle_beat_splits_the_clip():
    all_beats = beats(n=6)
    segments = derive.keep_segments([2], all_beats, clip_start=0.0, clip_end=27.0)
    assert len(segments) == 2
    assert segments[0]["end_time"] <= segments[1]["start_time"]


def test_nothing_dropped_means_render_the_whole_clip():
    assert derive.keep_segments([], beats(), clip_start=0.0, clip_end=40.0) == []
    assert derive.keep_segments(None, beats(), clip_start=0.0, clip_end=40.0) == []


def test_the_opening_and_the_payoff_are_never_dropped():
    all_beats = beats(n=5)
    segments = derive.keep_segments([0, 4], all_beats, clip_start=0.0, clip_end=22.0)
    assert segments == []


def test_a_single_remaining_segment_is_not_emitted():
    """studio/core.py:456 only activates trimming for more than one segment."""
    all_beats = beats(n=3)
    assert derive.keep_segments([2], all_beats, clip_start=0.0, clip_end=13.0) == []


def test_segments_shorter_than_the_minimum_are_discarded():
    tiny = [{"i": i, "start": i * 1.0, "end": i * 1.0 + 0.5, "text": "x",
             "n_words": 1, "w0": i, "w1": i} for i in range(6)]
    assert derive.keep_segments([2], tiny, clip_start=0.0, clip_end=5.5) == []


def test_segments_have_the_shape_core_py_reads():
    for segment in derive.keep_segments([2], beats(n=6), clip_start=0.0, clip_end=27.0):
        assert set(segment) == {"start_time", "end_time"}


# ----------------------------------------------------------------- mood/tags

@pytest.mark.parametrize("raw,expected", [
    ("epic", "epic"), ("EPIC", "epic"), ("  chill ", "chill"),
    ("nonsense", "chill"), ("", "chill"), (None, "chill"),
])
def test_mood_is_validated_against_the_enum(raw, expected):
    assert derive.bgm_mood(raw) == expected


def test_hashtags_are_normalized():
    assert derive.hashtags(["Reality", "#TV", "tv", "Island!!"]) == "#reality #tv #island"


def test_hashtags_are_capped_at_three():
    assert len(derive.hashtags(["a", "b", "c", "d", "e"]).split()) == 3


def test_hashtags_of_nothing_is_empty():
    assert derive.hashtags([]) == ""
    assert derive.hashtags(None) == ""


def test_keywords_dedupe_case_insensitively():
    assert derive.keywords(["Island", "island", "twelve"]) == ["Island", "twelve"]


def test_keywords_are_capped_at_eight():
    assert len(derive.keywords([f"k{i}" for i in range(20)])) == 8
