"""The executable specification for the transcript contract.

Every transcript producer in this repo -- Whisper, the YouTube JSON3 parser and
the VTT/SRT parser -- must satisfy ``assert_valid_data_segmen``. The contract is
not a style preference: ``clipping/studio/subtitles.py`` indexes ``seg["words"]``
with ``[]`` rather than ``.get()`` (see ``buat_file_ass`` lines 172 and 247), so a
segment missing the key raises ``KeyError`` in the middle of a render, and an
empty word list produces a silently blank subtitle.

If a future producer cannot satisfy this function, the fix belongs in the
producer -- never in ``subtitles.py``.
"""

REQUIRED_SEGMENT_KEYS = {"start", "end", "words"}
REQUIRED_WORD_KEYS = {"word", "start", "end"}


def assert_valid_data_segmen(data_segmen, *, video_duration=None):
    """Assert *data_segmen* satisfies the contract consumed by ``buat_file_ass``."""
    assert isinstance(data_segmen, list), f"expected list, got {type(data_segmen)}"
    assert data_segmen, "empty data_segmen -- no subtitles would render at all"

    prev_seg_start = float("-inf")

    for idx, seg in enumerate(data_segmen):
        where = f"segment[{idx}]"

        assert isinstance(seg, dict), f"{where}: expected dict, got {type(seg)}"
        missing = REQUIRED_SEGMENT_KEYS - set(seg)
        assert not missing, f"{where}: missing key(s) {sorted(missing)}"

        assert isinstance(seg["start"], float), f"{where}: start must be float"
        assert isinstance(seg["end"], float), f"{where}: end must be float"

        # buat_file_ass (subtitles.py:169) silently SKIPS segments where
        # start >= end. A degenerate segment is therefore an invisible subtitle,
        # not a crash -- which is exactly why it must be caught here instead.
        assert seg["end"] > seg["start"], f"{where}: degenerate span {seg['start']} -> {seg['end']}"

        assert seg["start"] >= prev_seg_start, (
            f"{where}: starts at {seg['start']} before the previous segment's "
            f"{prev_seg_start} -- segments must be non-decreasing"
        )
        prev_seg_start = seg["start"]

        words = seg["words"]
        assert isinstance(words, list), f"{where}: words must be a list"
        assert words, f"{where}: empty words list breaks subtitles.py:172"

        prev_w_start = float("-inf")
        for w_idx, w in enumerate(words):
            w_where = f"{where}.words[{w_idx}]"
            assert isinstance(w, dict), f"{w_where}: expected dict"
            assert set(w) == REQUIRED_WORD_KEYS, (
                f"{w_where}: keys {sorted(set(w))} != {sorted(REQUIRED_WORD_KEYS)}"
            )
            assert isinstance(w["word"], str) and w["word"].strip(), (
                f"{w_where}: word must be a non-blank string, got {w['word']!r}"
            )
            assert isinstance(w["start"], float) and isinstance(w["end"], float), (
                f"{w_where}: word timestamps must be floats"
            )
            assert w["end"] > w["start"], f"{w_where}: degenerate word span"
            assert w["start"] >= prev_w_start, f"{w_where}: word starts before its predecessor"
            prev_w_start = w["start"]

        # The karaoke highlight walks words and the segment box is drawn from the
        # segment span; if they disagree the highlight drifts off the box.
        assert words[0]["start"] == seg["start"], (
            f"{where}: segment start {seg['start']} != first word start {words[0]['start']}"
        )
        assert words[-1]["end"] == seg["end"], (
            f"{where}: segment end {seg['end']} != last word end {words[-1]['end']}"
        )

        if video_duration is not None:
            assert seg["start"] >= 0.0, f"{where}: negative start"
            assert seg["end"] <= video_duration + 1.0, (
                f"{where}: ends at {seg['end']}, past the {video_duration}s video"
            )


def assert_valid_transkrip(transkrip_lengkap, max_words_per_subtitle=None):
    """Assert the human-readable transcript matches the format the prompt documents.

    ``get_analysis_prompt`` tells the model the transcript arrives as
    ``[detik_mulai - detik_selesai] teks`` (engine.py:606-607).
    """
    import re

    assert isinstance(transkrip_lengkap, str)
    assert transkrip_lengkap.strip(), "empty transcript -- the LLM would get nothing"

    line_re = re.compile(r"^\[\d+\.\d+ - \d+\.\d+\] .+$")
    for line in transkrip_lengkap.splitlines():
        if not line.strip():
            continue
        assert line_re.match(line), f"malformed transcript line: {line!r}"
        if max_words_per_subtitle is not None:
            text = line.split("] ", 1)[1]
            n = len(text.split())
            assert n <= max_words_per_subtitle, (
                f"{n} words exceeds max_words_per_subtitle={max_words_per_subtitle}: {line!r}"
            )
