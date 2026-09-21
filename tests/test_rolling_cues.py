"""Rolling-display cue semantics — the 35% word loss.

Scraped auto-captions (DownloadYoutubeSubtitles.com and friends, VTT and SRT
alike) ship *overlapping* cue windows with unique text and no inline word tags:
each cue stays on screen until two cues later. Dividing a cue's words evenly
across that declared span pushes them past the next cue's words, and
``_enforce_monotonic`` then drops them -- blaming the file while destroying a
third of it.

Measured on the real 371-cue file the human uploaded: 718 of 2095 words gone.
The fixture here is the same shape at 14 words, and reproduces the loss exactly
(3 of 14, 21%) against the pre-fix parser.

Pure stdlib: no ffmpeg, no GPU, no network.
"""

import pytest

from conftest import FIXTURES
from helpers import assert_valid_data_segmen, assert_valid_transkrip

from clipping import transcript

ROLLING = ["rolling_overlap.vtt", "rolling_overlap.srt"]

# The fixture's cues, overlapping exactly as the real file's do.
#   0.560 -> 2.280   alpha bravo charlie delta
#   1.439 -> 3.800   echo foxtrot golf hotel
#   2.280 -> 6.000   india juliett
#   3.800 -> 8.360   kilo lima mike november
EXPECTED_WORDS = [
    "alpha", "bravo", "charlie", "delta",
    "echo", "foxtrot", "golf", "hotel",
    "india", "juliett",
    "kilo", "lima", "mike", "november",
]


def _parse(name, **kwargs):
    return transcript.parse_vtt_subs(str(FIXTURES / name), **kwargs)


def _words(segmen):
    return [w for seg in segmen for w in seg["words"]]


# ------------------------------------------------------------- the loss itself

@pytest.mark.parametrize("name", ROLLING)
def test_no_word_is_dropped_from_overlapping_cues(name):
    """The whole point. Pre-fix this returns 11 of 14 words."""
    _, segmen = _parse(name)
    assert [w["word"] for w in _words(segmen)] == EXPECTED_WORDS


@pytest.mark.parametrize("name", ROLLING)
def test_nothing_is_reported_as_backwards(name, capsys):
    """The warning must not fire on an ordinary YouTube export.

    It stays in place for genuinely mismatched transcripts (see
    ``test_out_of_order_cues_are_still_caught``); firing on every scraped file
    is what made it useless.
    """
    _parse(name)
    assert "backwards timestamps" not in capsys.readouterr().out


@pytest.mark.parametrize("name", ROLLING)
def test_contract_holds(name):
    transkrip, segmen = _parse(name, max_words_per_subtitle=5)
    assert_valid_data_segmen(segmen)
    assert_valid_transkrip(transkrip, max_words_per_subtitle=5)


def test_vtt_and_srt_agree():
    """The same rolling shape in either container must parse identically."""
    _, from_vtt = _parse("rolling_overlap.vtt")
    _, from_srt = _parse("rolling_overlap.srt")

    assert [w["word"] for w in _words(from_vtt)] == [w["word"] for w in _words(from_srt)]
    for a, b in zip(_words(from_vtt), _words(from_srt)):
        assert a["start"] == pytest.approx(b["start"])
        assert a["end"] == pytest.approx(b["end"])


# ------------------------------------------------------------- the clamp itself

def test_words_are_spread_over_the_speech_span_not_the_display_span():
    """Cue 1 is displayed until 2.280 but speaks until 1.439, when cue 2 opens.

    Four words over [0.560, 1.439) is 0.21975s each. Over the declared
    [0.560, 2.280) it would be 0.430s each, putting "delta" at 1.850 -- past
    the next cue's first word at 1.439, which is precisely what got it dropped.
    """
    _, segmen = _parse("rolling_overlap.vtt")
    words = {w["word"]: w for w in _words(segmen)}

    assert words["alpha"]["start"] == pytest.approx(0.560)
    assert words["delta"]["end"] == pytest.approx(1.439)
    assert words["echo"]["start"] == pytest.approx(1.439)
    # Contrast with the pre-fix even division across the display span.
    assert words["delta"]["start"] == pytest.approx(1.21925)
    assert words["delta"]["start"] != pytest.approx(1.850)


def test_the_last_cue_keeps_its_declared_end():
    """Nothing follows it, so there is nothing to clamp to."""
    _, segmen = _parse("rolling_overlap.vtt")
    assert _words(segmen)[-1]["end"] == pytest.approx(8.360)


@pytest.mark.parametrize(
    "cue_start,cue_end,next_start,tagged,expected",
    [
        # The rolling case: the next cue opens while this one is displayed.
        (0.560, 2.280, 1.439, False, 1.439),
        # Sequential cues -- nothing to clamp.
        (0.000, 1.000, 1.000, False, 1.000),
        (0.000, 1.000, 5.000, False, 1.000),
        # A cue that already carries word tags knows its own timings.
        (0.560, 2.280, 1.439, True, 2.280),
        # The last cue in the file.
        (0.560, 2.280, None, False, 2.280),
        # Out of order: a real mismatch, left for _enforce_monotonic.
        (5.000, 7.000, 1.000, False, 7.000),
        # Equal starts are not "inside" the cue either.
        (1.000, 3.000, 1.000, False, 3.000),
    ],
)
def test_effective_cue_end(cue_start, cue_end, next_start, tagged, expected):
    got = transcript._effective_cue_end(cue_start, cue_end, next_start, tagged)
    assert got == pytest.approx(expected)


# --------------------------------------------------- what must NOT change

def test_inline_tagged_rolling_captions_are_untouched():
    """``auto_rolling.vtt`` is YouTube's *tagged* rolling format.

    Its cue 1 (0.870 -> 3.000) is followed by a 10ms bridge cue at 3.000, so no
    clamp applies anyway -- but the tag guard is what guarantees it, and this
    pins the exact timings the karaoke highlight depends on (RC-4).
    """
    _, segmen = _parse("auto_rolling.vtt")
    words = {w["word"]: w for w in _words(segmen)}

    assert [w["word"] for w in _words(segmen)] == [
        "hello", "world", "this", "is", "rolling", "captions",
    ]
    assert words["hello"]["start"] == pytest.approx(0.870)
    assert words["world"]["start"] == pytest.approx(1.200)
    assert words["this"]["start"] == pytest.approx(1.800)
    assert words["this"]["end"] == pytest.approx(3.000)


def test_out_of_order_cues_are_still_caught(tmp_path, capsys):
    """The safety net stays armed for a transcript that really is mismatched."""
    path = tmp_path / "mismatched.vtt"
    path.write_text(
        "WEBVTT\n\n"
        "00:00:30.000 --> 00:00:33.000\n"
        "later words spoken here\n\n"
        "00:00:01.000 --> 00:00:04.000\n"
        "earlier words spoken here\n",
        encoding="utf-8",
    )

    _, segmen = transcript.parse_vtt_subs(str(path))

    assert "backwards timestamps" in capsys.readouterr().out
    assert [w["word"] for w in _words(segmen)] == [
        "later", "words", "spoken", "here",
    ]


def test_a_tight_overlap_keeps_its_words_rather_than_dropping_them():
    """A cue replaced 50ms after opening really did only get 50ms.

    No floor is applied: briefly-flashing words beat destroyed words, and the
    contract (strictly increasing, non-degenerate spans) still holds.
    """
    segmen = _parse_inline(
        "00:00:01.000 --> 00:00:09.000\none two three four\n\n"
        "00:00:01.050 --> 00:00:12.000\nfive six\n"
    )
    words = _words(segmen)

    assert [w["word"] for w in words] == ["one", "two", "three", "four", "five", "six"]
    assert words[3]["end"] == pytest.approx(1.050)
    assert_valid_data_segmen(segmen)


def _parse_inline(body):
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".vtt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("WEBVTT\n\n" + body)
        return transcript.parse_vtt_subs(path)[1]
    finally:
        os.unlink(path)
