"""Tests for the local WebVTT/SRT parser — the Whisper bypass.

Every test here is pure stdlib: no ffmpeg, no GPU, no network. That is what lets
CI run the suite without installing torch or mediapipe.
"""

import pytest

from conftest import FIXTURES
from helpers import assert_valid_data_segmen, assert_valid_transkrip

from clipping import transcript
from clipping.transcript import TranscriptParseError

REAL_FIXTURES = [
    "auto_rolling.vtt",
    "manual_plain.vtt",
    "no_hours.vtt",
    "with_notes.vtt",
    "entities_and_artifacts.vtt",
    "srt_style.srt",
    # Overlapping rolling cues with no inline tags -- see test_rolling_cues.py.
    "rolling_overlap.vtt",
    "rolling_overlap.srt",
]


def _parse(name, **kwargs):
    return transcript.parse_vtt_subs(str(FIXTURES / name), **kwargs)


def _words(segmen):
    return [w for seg in segmen for w in seg["words"]]


# ------------------------------------------------------------------ contract

@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_contract_all_fixtures(name):
    transkrip, segmen = _parse(name, max_words_per_subtitle=5)
    assert_valid_data_segmen(segmen)
    assert_valid_transkrip(transkrip, max_words_per_subtitle=5)


@pytest.mark.parametrize("name", REAL_FIXTURES)
@pytest.mark.parametrize("max_words", [1, 3, 7])
def test_contract_holds_at_every_chunk_size(name, max_words):
    transkrip, segmen = _parse(name, max_words_per_subtitle=max_words)
    assert_valid_data_segmen(segmen)
    assert_valid_transkrip(transkrip, max_words_per_subtitle=max_words)


# ------------------------------------------------------------ word timings

def test_inline_word_timings_are_used():
    """A cue's inline <00:00:01.200> tags must beat even division.

    Even division over cue 1 (0.870 -> 3.000, three words) would put "world" at
    ~1.58s. The inline tag says 1.200, and the tag must win.
    """
    _, segmen = _parse("auto_rolling.vtt")
    words = {w["word"]: w for w in _words(segmen)}

    assert words["world"]["start"] == pytest.approx(1.200)
    assert words["this"]["start"] == pytest.approx(1.800)
    assert words["hello"]["start"] == pytest.approx(0.870)
    # The last run of a cue runs to the cue end.
    assert words["this"]["end"] == pytest.approx(3.000)


def test_even_division_fallback():
    """A cue with no inline tags divides its span evenly across its words."""
    _, segmen = _parse("manual_plain.vtt")
    first_five = _words(segmen)[:5]

    assert [w["word"] for w in first_five] == ["one", "two", "three", "four", "five"]
    for idx, word in enumerate(first_five):
        assert word["start"] == pytest.approx(10.0 + idx)
        assert word["end"] == pytest.approx(11.0 + idx)


def test_timestamps_are_absolute_not_rebased():
    """buat_file_ass windows by subtracting start_clip, so input must be absolute."""
    _, segmen = _parse("manual_plain.vtt")
    assert segmen[0]["start"] == pytest.approx(10.0)


def test_short_form_timestamps():
    """WebVTT allows MM:SS.mmm; yt-dlp emits it."""
    _, segmen = _parse("no_hours.vtt")
    assert segmen[0]["start"] == pytest.approx(62.5)
    assert segmen[-1]["end"] == pytest.approx(65.5)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("01:02:03.450", 3723.45),
        ("02:03.450", 123.45),
        ("00:00:00.001", 0.001),
        ("1:00:00.5", 3600.5),
        ("00:00:01,500", 1.5),
        ("00:00:02.05", 2.05),
    ],
)
def test_parse_timestamp(text, expected):
    assert transcript._parse_timestamp(text) == pytest.approx(expected)


def test_parse_timestamp_rejects_garbage():
    with pytest.raises(TranscriptParseError):
        transcript._parse_timestamp("not a timestamp")


def test_offset_applied():
    _, base = _parse("manual_plain.vtt")
    _, shifted = _parse("manual_plain.vtt", offset=5.0)

    assert shifted[0]["start"] == pytest.approx(base[0]["start"] + 5.0)
    assert shifted[-1]["end"] == pytest.approx(base[-1]["end"] + 5.0)
    assert_valid_data_segmen(shifted)


def test_negative_offset_clamps_at_zero():
    _, shifted = _parse("manual_plain.vtt", offset=-1000.0)
    assert shifted[0]["start"] >= 0.0
    assert_valid_data_segmen(shifted)


# --------------------------------------------------------------- de-dupe

def test_rolling_dedupe():
    """YouTube repeats the previous cue's line; the transcript must not."""
    transkrip, segmen = _parse("auto_rolling.vtt")

    assert transkrip.count("hello") == 1, transkrip
    assert transkrip.count("rolling") == 1, transkrip

    words = [w["word"] for w in _words(segmen)]
    assert words == ["hello", "world", "this", "is", "rolling", "captions"]


def test_dedupe_can_be_disabled():
    _, deduped = _parse("auto_rolling.vtt", dedupe=True)
    _, raw = _parse("auto_rolling.vtt", dedupe=False)

    assert len(_words(raw)) > len(_words(deduped))
    assert_valid_data_segmen(raw)


def test_legit_repetition_preserved():
    """Dedupe drops carried-over *lines*, never repeated words within a line."""
    _, segmen = _parse("manual_plain.vtt")
    words = [w["word"] for w in _words(segmen)]
    assert words.count("no") == 3


# ------------------------------------------------------- headers and noise

def test_headers_notes_and_style_skipped():
    """A '-->' inside a NOTE or STYLE block must never be read as a cue."""
    transkrip, segmen = _parse("with_notes.vtt")
    words = [w["word"] for w in _words(segmen)]

    assert words == ["actual", "subtitle", "text"]
    for forbidden in ("WEBVTT", "NOTE", "STYLE", "REGION", "Kind", "Language", "cue"):
        assert forbidden not in transkrip
    assert segmen[0]["start"] == pytest.approx(2.0)


def test_entities_and_artifacts_stripped():
    transkrip, segmen = _parse("entities_and_artifacts.vtt")
    words = [w["word"] for w in _words(segmen)]

    # &gt;&gt; unescapes to >> and is then stripped as a speaker marker.
    assert ">>" not in transkrip
    assert "&gt;" not in transkrip and "&amp;" not in transkrip
    assert "Welcome" in words and "friends" in words
    # &amp; became a literal ampersand, proving unescape ran before stripping.
    assert "&" in words

    # [Music] and the music glyphs are gone, the surrounding words are not.
    assert "Music" not in transkrip and "♪" not in transkrip
    assert words.count("la") == 2

    # <i> tags and the leading speaker dash are gone.
    assert "<i>" not in transkrip
    assert "emphasis" in words and "here" in words
    assert not any(w.startswith("-") for w in words)


def test_srt_accepted():
    """Comma decimals and numeric index lines come free with the VTT parser."""
    _, segmen = _parse("srt_style.srt")
    words = [w["word"] for w in _words(segmen)]

    assert words[:3] == ["first", "srt", "line"]
    assert "1" not in words and "2" not in words  # index lines are not text
    assert segmen[0]["start"] == pytest.approx(1.0)
    assert segmen[-1]["end"] == pytest.approx(5.5)


# ---------------------------------------------------------------- failures

@pytest.mark.parametrize(
    "name", ["empty.vtt", "not_a_subtitle.vtt", "only_artifacts.vtt"]
)
def test_unusable_transcripts_raise(name):
    """Never return empty: in local-first mode that degrades to a silent
    multi-hour Whisper run instead of a 40ms failure."""
    with pytest.raises(TranscriptParseError):
        _parse(name)


def test_missing_file_raises_filenotfound():
    with pytest.raises(FileNotFoundError):
        transcript.load_transcript(str(FIXTURES / "nope.vtt"))
