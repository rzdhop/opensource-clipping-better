"""Tests for the Whisper-bypass dispatch in clipping.runner.

These prove the central promise of the refactor: when a transcript is supplied,
``transcribe_video`` is never called, so faster-whisper/CTranslate2 is never
imported.
"""

import os
import sys
from types import SimpleNamespace

import pytest

from conftest import FIXTURES
from helpers import assert_valid_data_segmen

from clipping import runner
from clipping.transcript import TranscriptParseError


def make_cfg(tmp_path, transcript=None, **overrides):
    video = tmp_path / "sample.mp4"
    if not video.exists():
        video.write_bytes(b"\x00")

    cfg = SimpleNamespace(
        file_video_asli=str(video),
        transcript_path=str(transcript) if transcript else None,
        transcript_offset=0.0,
        no_whisper=False,
        max_kata_per_subtitle=5,
        whisper_model="large-v3",
        whisper_device="cuda",
        whisper_compute_type="float16",
        outputs_dir=str(tmp_path / "outputs"),
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


@pytest.fixture
def no_whisper_allowed(monkeypatch):
    """Make any call into Whisper an immediate, obvious test failure."""
    def _boom(*args, **kwargs):
        raise AssertionError(
            "transcribe_video was called even though a transcript was supplied"
        )

    monkeypatch.setattr(runner.engine, "transcribe_video", _boom)


@pytest.fixture(autouse=True)
def no_cv2(monkeypatch):
    """The duration cross-check is optional; skip it in unit tests."""
    monkeypatch.setattr(runner, "probe_video_duration", lambda path: None)


# ------------------------------------------------------------------- bypass

def test_vtt_bypasses_whisper(tmp_path, no_whisper_allowed):
    cfg = make_cfg(tmp_path, transcript=FIXTURES / "manual_plain.vtt")

    transkrip, segmen = runner.resolve_transcript(cfg)

    assert_valid_data_segmen(segmen)
    assert "one two three four five" in transkrip


def test_srt_bypasses_whisper(tmp_path, no_whisper_allowed):
    cfg = make_cfg(tmp_path, transcript=FIXTURES / "srt_style.srt")
    _, segmen = runner.resolve_transcript(cfg)
    assert_valid_data_segmen(segmen)


def test_json3_bypasses_whisper(tmp_path, no_whisper_allowed):
    cfg = make_cfg(tmp_path, transcript=FIXTURES / "sample.json3")
    _, segmen = runner.resolve_transcript(cfg)
    assert_valid_data_segmen(segmen)


def test_bypass_does_not_import_ctranslate2(tmp_path, no_whisper_allowed):
    """The whole point of the refactor: no ML runtime on the transcript path."""
    cfg = make_cfg(tmp_path, transcript=FIXTURES / "manual_plain.vtt")
    runner.resolve_transcript(cfg)

    leaked = [m for m in ("faster_whisper", "ctranslate2", "torch") if m in sys.modules]
    assert not leaked, f"transcript path pulled in {leaked}"


def test_offset_is_forwarded(tmp_path, no_whisper_allowed):
    plain = make_cfg(tmp_path, transcript=FIXTURES / "manual_plain.vtt")
    _, base = runner.resolve_transcript(plain)

    shifted_cfg = make_cfg(
        tmp_path, transcript=FIXTURES / "manual_plain.vtt", transcript_offset=7.0
    )
    _, shifted = runner.resolve_transcript(shifted_cfg)

    assert shifted[0]["start"] == pytest.approx(base[0]["start"] + 7.0)


# ---------------------------------------------------------------- fallback

def test_falls_back_to_whisper_without_transcript(tmp_path, monkeypatch):
    calls = []

    def _fake(video_path, **kwargs):
        calls.append((video_path, kwargs))
        return "[0.0 - 1.0] hi\n", [
            {"start": 0.0, "end": 1.0, "words": [{"word": "hi", "start": 0.0, "end": 1.0}]}
        ]

    monkeypatch.setattr(runner.engine, "transcribe_video", _fake)
    cfg = make_cfg(tmp_path, transcript=None)

    _, segmen = runner.resolve_transcript(cfg)

    assert len(calls) == 1
    video_path, kwargs = calls[0]
    assert video_path == cfg.file_video_asli
    assert kwargs["model_size"] == "large-v3"
    assert kwargs["max_words_per_subtitle"] == 5
    assert_valid_data_segmen(segmen)


def test_no_whisper_flag_hard_fails(tmp_path, no_whisper_allowed):
    cfg = make_cfg(tmp_path, transcript=None, no_whisper=True)

    with pytest.raises(RuntimeError, match="--no-whisper"):
        runner.resolve_transcript(cfg)


# ------------------------------------------------------------------ failure

def test_bad_transcript_raises_rather_than_falling_back(tmp_path, no_whisper_allowed):
    """A broken transcript must abort, not silently degrade into a Whisper run."""
    bad = tmp_path / "broken.vtt"
    bad.write_text("this is not a subtitle file", encoding="utf-8")
    cfg = make_cfg(tmp_path, transcript=bad)

    with pytest.raises(TranscriptParseError):
        runner.resolve_transcript(cfg)


def test_missing_transcript_raises(tmp_path, no_whisper_allowed):
    cfg = make_cfg(tmp_path, transcript=tmp_path / "gone.vtt")

    with pytest.raises(FileNotFoundError):
        runner.resolve_transcript(cfg)


def test_empty_whisper_result_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.engine, "transcribe_video", lambda *a, **k: ("", []))
    cfg = make_cfg(tmp_path, transcript=None)

    with pytest.raises(RuntimeError, match="Transcript is empty"):
        runner.resolve_transcript(cfg)


# --------------------------------------------------------- mismatch warning

@pytest.mark.parametrize(
    "duration,expect_warning",
    [
        (18.0, False),   # transcript ends at 18s, video 18s -> fine
        (5.0, True),     # transcript far overruns the video
        (200.0, True),   # transcript covers <25% of the video
        (None, False),   # duration unreadable -> stay quiet
    ],
)
def test_mismatch_warning(tmp_path, monkeypatch, capsys, duration, expect_warning):
    monkeypatch.setattr(runner, "probe_video_duration", lambda path: duration)
    cfg = make_cfg(tmp_path, transcript=FIXTURES / "manual_plain.vtt")

    runner.resolve_transcript(cfg)

    out = capsys.readouterr().out
    assert ("WARNING" in out) is expect_warning


def test_mismatch_warning_never_raises(tmp_path, monkeypatch):
    """A short transcript is legitimate; warn, never abort."""
    monkeypatch.setattr(runner, "probe_video_duration", lambda path: 9999.0)
    cfg = make_cfg(tmp_path, transcript=FIXTURES / "manual_plain.vtt")

    _, segmen = runner.resolve_transcript(cfg)
    assert_valid_data_segmen(segmen)
