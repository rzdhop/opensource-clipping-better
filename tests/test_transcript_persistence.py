"""Saving a Whisper transcript so a re-run does not have to redo it.

A measured job spent 94 minutes transcribing a 20-minute video on CPU, then
failed at AI analysis and lost the lot: nothing wrote the transcript out, not
even on success.
"""

import os
from types import SimpleNamespace

import pytest

from clipping import runner, transcript as transcript_mod
from clipping.transcript import parse_vtt_subs, render_vtt, write_vtt


WHISPER_SEGMENTS = [
    {"start": 1.0, "end": 2.4, "words": [
        {"word": "hello", "start": 1.0, "end": 1.4},
        {"word": "world", "start": 1.6, "end": 2.4},
    ]},
    {"start": 3.0, "end": 4.2, "words": [
        {"word": "second", "start": 3.0, "end": 3.5},
        {"word": "segment", "start": 3.5, "end": 4.2},
    ]},
]


def _roundtrip(segments, tmp_path, max_words_per_subtitle=5, **kwargs):
    path = tmp_path / "transcript.vtt"
    write_vtt(segments, str(path))
    return parse_vtt_subs(
        str(path), max_words_per_subtitle=max_words_per_subtitle, **kwargs)


# ------------------------------------------------------------------ the format

def test_every_word_and_start_survives_the_round_trip(tmp_path):
    """Words and their start times are the payload that matters -- they are what
    90 minutes of CPU bought."""
    _, back = _roundtrip(WHISPER_SEGMENTS, tmp_path)

    words = [w["word"] for seg in back for w in seg["words"]]
    starts = [w["start"] for seg in back for w in seg["words"]]
    assert words == ["hello", "world", "second", "segment"]
    assert starts == [1.0, 1.6, 3.0, 3.5]
    # the span is preserved end to end
    assert back[0]["start"] == 1.0
    assert back[-1]["end"] == 4.2


def test_segments_are_rechunked_not_preserved(tmp_path):
    """Documented loss. The reader flattens every word and re-chunks purely by
    max_words_per_subtitle, ignoring the cue boundaries we wrote -- whereas
    Whisper also breaks a chunk at the end of each of ITS segments. So two
    2-word segments come back as one 4-word segment at max_words=5.

    It only regroups subtitle lines; no word, order or start time is lost. Pinned
    here so a future change to either side is a visible decision.
    """
    _, back = _roundtrip(WHISPER_SEGMENTS, tmp_path)
    assert len(WHISPER_SEGMENTS) == 2
    assert len(back) == 1

    _, tighter = _roundtrip(WHISPER_SEGMENTS, tmp_path, max_words_per_subtitle=2)
    assert len(tighter) == 2
    assert [s["start"] for s in tighter] == [1.0, 3.0]


def test_non_final_word_ends_snap_to_the_next_start(tmp_path):
    """Documented loss, and harmless: studio/subtitles.buat_file_ass recomputes
    every non-final end the same way, so the renderer never sees the original."""
    _, back = _roundtrip(WHISPER_SEGMENTS, tmp_path, max_words_per_subtitle=2)
    # written as 1.4, comes back as the next word's start
    assert back[0]["words"][0]["end"] == 1.6
    # the final word of a cue keeps its exact end
    assert back[0]["words"][-1]["end"] == 2.4


def test_every_segment_still_has_words(tmp_path):
    """A-001: buat_file_ass indexes seg["words"] with [], not .get()."""
    _, back = _roundtrip(WHISPER_SEGMENTS, tmp_path)
    assert all(seg["words"] for seg in back)
    assert all("text" not in seg for seg in back)


def test_the_output_is_valid_webvtt():
    text = render_vtt(WHISPER_SEGMENTS)
    assert text.startswith("WEBVTT\n\n")
    assert "-->" in text
    assert "<00:00:01.000>hello" in text


# ------------------------------------------------- the dedupe trap

REPEATED = [
    {"start": 0.0, "end": 1.0, "words": [
        {"word": "you", "start": 0.0, "end": 0.5},
        {"word": "know", "start": 0.5, "end": 1.0}]},
    {"start": 1.0, "end": 2.0, "words": [
        {"word": "you", "start": 1.0, "end": 1.5},
        {"word": "know", "start": 1.5, "end": 2.0}]},
    {"start": 2.0, "end": 2.5, "words": [
        {"word": "right", "start": 2.0, "end": 2.5}]},
]


def test_dedupe_would_eat_genuine_repetition(tmp_path):
    """Why cfg.transcript_dedupe exists.

    The reader drops a cue whose text repeats the previous cue's -- correct for
    scraped captions with rolling repetition, wrong for speech we transcribed
    ourselves, where "you know / you know" is really said twice.
    """
    _, deduped = _roundtrip(REPEATED, tmp_path, dedupe=True)
    _, kept = _roundtrip(REPEATED, tmp_path, dedupe=False)

    assert [w["word"] for s in deduped for w in s["words"]] == ["you", "know", "right"]
    assert [w["word"] for s in kept for w in s["words"]] == [
        "you", "know", "you", "know", "right"]


# ------------------------------------------------------------- the save itself

def _cfg(tmp_path, **over):
    cfg = SimpleNamespace(
        transcript_path=None, transcript_offset=0.0, no_whisper=False,
        max_kata_per_subtitle=5, whisper_model="large-v3", whisper_device="auto",
        whisper_compute_type="auto", file_video_asli="video.mp4",
        outputs_dir=str(tmp_path), durasi_video=None,
    )
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def test_a_whisper_run_saves_its_transcript(monkeypatch, tmp_path):
    monkeypatch.setattr(
        runner.engine, "transcribe_video",
        lambda *a, **k: ("[1.0 - 2.4] hello world\n", WHISPER_SEGMENTS))
    monkeypatch.setattr(runner, "_warn_on_transcript_video_mismatch", lambda *a: None)

    runner.resolve_transcript(_cfg(tmp_path))

    saved = tmp_path / transcript_mod.SAVED_TRANSCRIPT_NAME
    assert saved.is_file(), "the 94 minutes must not be thrown away"
    _, back = parse_vtt_subs(str(saved), max_words_per_subtitle=5)
    assert [w["word"] for s in back for w in s["words"]] == [
        "hello", "world", "second", "segment"]


def test_a_supplied_transcript_is_not_rewritten_from_itself(monkeypatch, tmp_path):
    src = tmp_path / "given.vtt"
    write_vtt(WHISPER_SEGMENTS, str(src))
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(runner, "_warn_on_transcript_video_mismatch", lambda *a: None)

    runner.resolve_transcript(
        _cfg(out, transcript_path=str(src), outputs_dir=str(out)))

    assert not (out / transcript_mod.SAVED_TRANSCRIPT_NAME).exists()


def test_a_save_failure_does_not_fail_the_run(monkeypatch, tmp_path):
    """The expensive work already succeeded. Protecting the transcript must not
    become a new way to lose it."""
    monkeypatch.setattr(
        runner.engine, "transcribe_video",
        lambda *a, **k: ("text", WHISPER_SEGMENTS))
    monkeypatch.setattr(runner, "_warn_on_transcript_video_mismatch", lambda *a: None)

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(runner.transcript_mod, "write_vtt", _boom)

    text, segs = runner.resolve_transcript(_cfg(tmp_path))
    assert segs == WHISPER_SEGMENTS


def test_the_write_is_atomic(tmp_path):
    """The reader raises on a malformed transcript and nothing can delete a file
    from an output directory, so a half-written file would be unrecoverable."""
    path = tmp_path / "transcript.vtt"
    write_vtt(WHISPER_SEGMENTS, str(path))
    assert path.is_file()
    assert not list(tmp_path.glob("*.tmp")), "temp file left behind"


def test_resolve_transcript_passes_the_dedupe_flag_through(monkeypatch, tmp_path):
    """The flag is set in the web adapter but consumed here; if the plumbing
    breaks, a saved transcript silently loses repeated speech."""
    seen = {}

    def _fake_load(path, **kwargs):
        seen.update(kwargs)
        return "text", WHISPER_SEGMENTS

    monkeypatch.setattr(runner.engine, "load_transcript", _fake_load)
    monkeypatch.setattr(runner, "_warn_on_transcript_video_mismatch", lambda *a: None)

    src = tmp_path / "given.vtt"
    write_vtt(WHISPER_SEGMENTS, str(src))

    runner.resolve_transcript(
        _cfg(tmp_path, transcript_path=str(src), transcript_dedupe=False))
    assert seen["dedupe"] is False

    runner.resolve_transcript(_cfg(tmp_path, transcript_path=str(src)))
    assert seen["dedupe"] is True, "default must stay True for scraped captions"
