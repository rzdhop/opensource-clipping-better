"""The SubRip writer — full transcript and per-clip windows.

``render_srt`` is the export half of the human's "add also srt" request, and the
primitive the per-clip ``.srt`` sidecars are built from later. It is windowed and
rebased because ``data_segmen`` is always source-absolute (see the module header
in ``clipping/transcript.py``) while a file shipped next to
``highlight_rank_2.mp4`` must start at zero.

Pure stdlib.
"""

import re

import pytest

from conftest import FIXTURES
from helpers import assert_valid_data_segmen

from clipping import transcript

CUE_RE = re.compile(
    r"^(\d+)\n"
    r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n"
    r"(.+)$"
)


def _parse(name, **kwargs):
    return transcript.parse_vtt_subs(str(FIXTURES / name), **kwargs)


def _cues(text):
    """Split rendered SRT into (index, start, end, text) tuples."""
    out = []
    for block in text.strip().split("\n\n"):
        match = CUE_RE.match(block.strip())
        assert match, f"malformed SRT cue: {block!r}"
        out.append(
            (
                int(match.group(1)),
                transcript._parse_timestamp(match.group(2)),
                transcript._parse_timestamp(match.group(3)),
                match.group(4),
            )
        )
    return out


# ------------------------------------------------------------------- format

def test_timestamps_use_a_comma_not_a_period():
    """SRT's decimal separator. A period makes the file silently unreadable."""
    assert transcript._format_srt_timestamp(12.345) == "00:00:12,345"
    assert transcript._format_srt_timestamp(3723.45) == "01:02:03,450"
    assert transcript._format_srt_timestamp(0.0) == "00:00:00,000"
    # Negative rebasing must never produce a negative stamp.
    assert transcript._format_srt_timestamp(-5.0) == "00:00:00,000"


def test_indices_are_one_based_and_contiguous():
    _, segmen = _parse("rolling_overlap.vtt", max_words_per_subtitle=2)
    cues = _cues(transcript.render_srt(segmen))
    assert [c[0] for c in cues] == list(range(1, len(cues) + 1))


def test_full_render_covers_every_word_in_order():
    _, segmen = _parse("rolling_overlap.vtt")
    rendered = " ".join(c[3] for c in _cues(transcript.render_srt(segmen)))
    expected = " ".join(w["word"] for seg in segmen for w in seg["words"])
    assert rendered == expected


def test_timestamps_are_absolute_when_no_window_is_given():
    _, segmen = _parse("manual_plain.vtt")
    cues = _cues(transcript.render_srt(segmen))
    assert cues[0][1] == pytest.approx(10.0)
    assert cues[-1][2] == pytest.approx(18.0)


# ------------------------------------------------------------------ windowing

def test_window_rebases_to_zero():
    _, segmen = _parse("manual_plain.vtt")
    cues = _cues(transcript.render_srt(segmen, clip_start=12.0, clip_end=16.0))
    assert cues[0][1] == pytest.approx(0.0)


def test_window_keeps_only_the_words_inside_it():
    """A straddling segment contributes its inside words, not a whole line.

    manual_plain.vtt is "one two three four five" at 10-15s (1s per word) then
    "no no no" at 15-18s. A 12-16s clip can hear three, four, five and one no.
    """
    _, segmen = _parse("manual_plain.vtt")
    cues = _cues(transcript.render_srt(segmen, clip_start=12.0, clip_end=16.0))

    assert [c[3] for c in cues] == ["three four five", "no"]
    assert cues[0][1] == pytest.approx(0.0)   # 12.0 -> 0
    assert cues[0][2] == pytest.approx(3.0)   # 15.0 -> 3
    assert cues[1][1] == pytest.approx(3.0)
    assert cues[1][2] == pytest.approx(4.0)   # clamped to the 16s cut


def test_nothing_extends_past_the_clip():
    _, segmen = _parse("manual_plain.vtt")
    duration = 4.0
    for _, start, end, _text in _cues(
        transcript.render_srt(segmen, clip_start=12.0, clip_end=12.0 + duration)
    ):
        assert 0.0 <= start < end <= duration + 1e-9


def test_a_window_outside_the_transcript_renders_empty():
    """Empty, not malformed: a clip with no speech simply has no subtitles."""
    _, segmen = _parse("manual_plain.vtt")
    assert transcript.render_srt(segmen, clip_start=600.0, clip_end=630.0) == ""


def test_open_ended_window_runs_to_the_end():
    _, segmen = _parse("manual_plain.vtt")
    cues = _cues(transcript.render_srt(segmen, clip_start=15.0))
    assert [c[3] for c in cues] == ["no no no"]


# ----------------------------------------------------------------- round trip

def test_round_trips_through_the_parser(tmp_path):
    """Written, re-read, and still contract-valid with every word intact.

    Word *times* do not survive exactly -- SRT carries no word-level timing, so
    the reader divides each cue evenly. Word count, order and the cue span do.
    """
    _, original = _parse("rolling_overlap.vtt")
    path = tmp_path / "out.srt"
    transcript.write_srt(original, str(path))

    _, reread = transcript.parse_vtt_subs(str(path), dedupe=False)

    assert_valid_data_segmen(reread)
    assert [w["word"] for seg in reread for w in seg["words"]] == [
        w["word"] for seg in original for w in seg["words"]
    ]
    assert reread[0]["start"] == pytest.approx(original[0]["start"])
    assert reread[-1]["end"] == pytest.approx(original[-1]["end"])


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    """The reader raises on a malformed transcript and no endpoint can delete a
    file from an output directory, so a half-written file is unrecoverable."""
    _, segmen = _parse("manual_plain.vtt")
    path = tmp_path / transcript.SAVED_TRANSCRIPT_SRT_NAME
    transcript.write_srt(segmen, str(path))

    assert path.read_text(encoding="utf-8") == transcript.render_srt(segmen)
    assert list(tmp_path.iterdir()) == [path]


def test_saved_name_is_the_vtt_name_with_an_srt_suffix():
    assert transcript.SAVED_TRANSCRIPT_NAME == "transcript.vtt"
    assert transcript.SAVED_TRANSCRIPT_SRT_NAME == "transcript.srt"


# ------------------------------------------------------------------- edges

def test_segments_without_words_are_skipped_not_emitted_empty():
    segmen = [
        {"start": 0.0, "end": 1.0, "words": []},
        {
            "start": 1.0,
            "end": 2.0,
            "words": [{"word": "kept", "start": 1.0, "end": 2.0}],
        },
    ]
    cues = _cues(transcript.render_srt(segmen))
    assert [c[3] for c in cues] == ["kept"]
    assert cues[0][0] == 1
