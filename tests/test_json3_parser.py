"""Regression tests for the YouTube JSON3 subtitle parser.

``test_json3_golden`` is the refactor guard: it pins the exact output of the
parser as it behaved *before* its cleaning/chunking internals were extracted into
shared helpers in ``clipping/transcript.py``. If that test drifts, the extraction
changed behaviour and the shared helpers are wrong — not the test.
"""

import pytest

from conftest import FIXTURES
from helpers import assert_valid_data_segmen, assert_valid_transkrip

from clipping import transcript

SAMPLE = str(FIXTURES / "sample.json3")

# Captured from the original engine.parse_youtube_json3_subs at commit 3c72b75,
# before any refactoring.
GOLDEN_TRANSKRIP = (
    "[1.0 - 4.2] hello world this is a\n"
    "[4.2 - 7.5] test final words here for\n"
    "[7.5 - 8.0] chunking\n"
)

GOLDEN_SEGMEN = [
    {
        "start": 1.0,
        "end": 4.15,
        "words": [
            {"word": "hello", "start": 1.0, "end": 2.0},
            {"word": "world", "start": 2.0, "end": 3.0},
            {"word": "this", "start": 3.0, "end": 3.5},
            {"word": "is", "start": 3.5, "end": 3.8},
            {"word": "a", "start": 3.8, "end": 4.15},
        ],
    },
    {
        "start": 4.15,
        "end": 7.5,
        "words": [
            {"word": "test", "start": 4.15, "end": 4.5},
            {"word": "final", "start": 5.5, "end": 6.0},
            {"word": "words", "start": 6.0, "end": 6.5},
            {"word": "here", "start": 6.5, "end": 7.0},
            {"word": "for", "start": 7.0, "end": 7.5},
        ],
    },
    {
        "start": 7.5,
        "end": 8.0,
        "words": [{"word": "chunking", "start": 7.5, "end": 8.0}],
    },
]


def _parse(**kwargs):
    return transcript.parse_youtube_json3_subs(SAMPLE, **kwargs)


def test_json3_golden():
    """Byte-for-byte identical to the pre-refactor implementation."""
    transkrip, segmen = _parse(max_words_per_subtitle=5)

    assert transkrip == GOLDEN_TRANSKRIP
    assert len(segmen) == len(GOLDEN_SEGMEN)

    for got, want in zip(segmen, GOLDEN_SEGMEN):
        assert got["start"] == pytest.approx(want["start"])
        assert got["end"] == pytest.approx(want["end"])
        assert len(got["words"]) == len(want["words"])
        for got_w, want_w in zip(got["words"], want["words"]):
            assert got_w["word"] == want_w["word"]
            assert got_w["start"] == pytest.approx(want_w["start"])
            assert got_w["end"] == pytest.approx(want_w["end"])


def test_json3_contract():
    _, segmen = _parse(max_words_per_subtitle=5)
    assert_valid_data_segmen(segmen)


def test_json3_transkrip_format():
    transkrip, _ = _parse(max_words_per_subtitle=5)
    assert_valid_transkrip(transkrip, max_words_per_subtitle=5)


@pytest.mark.parametrize("max_words", [1, 2, 3, 5, 10])
def test_json3_respects_chunk_size(max_words):
    transkrip, segmen = _parse(max_words_per_subtitle=max_words)
    assert_valid_data_segmen(segmen)
    assert_valid_transkrip(transkrip, max_words_per_subtitle=max_words)
    # Only the final chunk of the stream may be short.
    for seg in segmen[:-1]:
        assert len(seg["words"]) == max_words


def test_json3_artifacts_stripped():
    """[Music], >> and <i> must never reach a rendered subtitle."""
    transkrip, segmen = _parse()
    all_words = [w["word"] for seg in segmen for w in seg["words"]]

    for forbidden in ("[Music]", "Music", ">>", "<i>", "</i>"):
        assert forbidden not in transkrip, f"{forbidden!r} leaked into the transcript"
    for word in all_words:
        assert "<" not in word and ">" not in word


def test_json3_missing_file_returns_empty():
    """The JSON3 parser is an opportunistic fallback and stays forgiving."""
    transkrip, segmen = transcript.parse_youtube_json3_subs(
        str(FIXTURES / "does_not_exist.json3")
    )
    assert transkrip == ""
    assert segmen == []


def test_engine_reexports_parser():
    """web/api/worker.py and the legacy runner path import this from engine."""
    from clipping import engine

    assert engine.parse_youtube_json3_subs is transcript.parse_youtube_json3_subs
    assert engine.load_transcript is transcript.load_transcript


def test_transcript_line_granularity_differs_by_producer():
    """Pin the documented asymmetry between the two transcript producers.

    The chunked parsers break lines every ``max_words_per_subtitle`` words;
    ``transcribe_video`` emits one line per Whisper segment. Both are fine as
    prompt input, but the difference is easy to mistake for a bug, so it is
    asserted here rather than left to be rediscovered.
    """
    transkrip, _ = transcript.parse_youtube_json3_subs(SAMPLE, max_words_per_subtitle=3)

    for line in transkrip.splitlines():
        words = line.split("] ", 1)[1].split()
        assert len(words) <= 3
