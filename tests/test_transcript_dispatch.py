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


# ------------------------------------------------- hosted transcription fork

def test_a_supplied_transcript_still_bypasses_everything(tmp_path, monkeypatch):
    """The Whisper bypass is the premise of the whole local-first refactor and
    must not be weakened by adding a hosted path beside it."""
    from clipping import runner

    called = []
    monkeypatch.setattr(runner, "_transcribe", lambda cfg: called.append("transcribed"))

    vtt = tmp_path / "t.vtt"
    vtt.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello there\n",
                   encoding="utf-8")
    cfg = SimpleNamespace(
        transcript_path=str(vtt), max_kata_per_subtitle=5, transcript_offset=0.0,
        transcript_dedupe=True, outputs_dir=str(tmp_path), no_whisper=False,
        file_video_asli="v.mp4",
    )
    runner.resolve_transcript(cfg)
    assert called == []


def test_stt_chain_none_is_the_modern_no_whisper(tmp_path):
    from clipping import runner

    cfg = SimpleNamespace(
        transcript_path=None, max_kata_per_subtitle=5, outputs_dir=str(tmp_path),
        no_whisper=False, stt_chain="none", file_video_asli="v.mp4",
    )
    with pytest.raises(RuntimeError) as info:
        runner.resolve_transcript(cfg)
    assert "--stt-chain none" in str(info.value)


def test_no_hosted_key_falls_through_to_local_whisper(tmp_path, monkeypatch):
    from clipping import engine, runner

    monkeypatch.setattr(
        engine, "transcribe_video",
        lambda *a, **kw: ("[0.0 - 1.0] local\n",
                          [{"start": 0.0, "end": 1.0,
                            "words": [{"word": "local", "start": 0.0, "end": 1.0}]}]),
    )
    cfg = SimpleNamespace(
        file_video_asli="v.mp4", max_kata_per_subtitle=5, whisper_model="tiny",
        whisper_device="cpu", whisper_compute_type="int8", stt_chain="",
        output_language="auto", api_key_groq="", api_key_mistral="",
        api_key_nvidia="", api_key_gemini="", api_key_openrouter="",
        api_key_custom="", outputs_dir=str(tmp_path),
    )
    _, segmen = runner._transcribe(cfg)
    assert [w["word"] for s in segmen for w in s["words"]] == ["local"]


def test_a_hosted_provider_is_used_when_its_key_is_set(tmp_path, monkeypatch):
    from clipping import runner
    from clipping.providers import stt as stt_mod

    monkeypatch.setattr(
        stt_mod, "transcribe",
        lambda *a, **kw: ("[0.0 - 1.0] hosted\n",
                          [{"start": 0.0, "end": 1.0,
                            "words": [{"word": "hosted", "start": 0.0, "end": 1.0}]}],
                          "fr"),
    )
    cfg = SimpleNamespace(
        file_video_asli="v.mp4", max_kata_per_subtitle=5, whisper_model="tiny",
        whisper_device="cpu", whisper_compute_type="int8", stt_chain="",
        output_language="auto", api_key_groq="a-key", api_key_mistral="",
        api_key_nvidia="", api_key_gemini="", api_key_openrouter="",
        api_key_custom="", outputs_dir=str(tmp_path), detected_language="",
    )
    _, segmen = runner._transcribe(cfg)
    assert [w["word"] for s in segmen for w in s["words"]] == ["hosted"]
    # What the provider actually heard beats guessing from stopwords later.
    assert cfg.detected_language == "fr"


def test_a_hosted_failure_falls_back_to_local_when_the_chain_allows(tmp_path, monkeypatch):
    from clipping import engine, runner
    from clipping.providers import stt as stt_mod

    def boom(*a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(stt_mod, "transcribe", boom)
    monkeypatch.setattr(
        engine, "transcribe_video",
        lambda *a, **kw: ("[0.0 - 1.0] local\n",
                          [{"start": 0.0, "end": 1.0,
                            "words": [{"word": "local", "start": 0.0, "end": 1.0}]}]),
    )
    cfg = SimpleNamespace(
        file_video_asli="v.mp4", max_kata_per_subtitle=5, whisper_model="tiny",
        whisper_device="cpu", whisper_compute_type="int8",
        stt_chain="groq/whisper-large-v3-turbo,local/faster-whisper",
        output_language="auto", api_key_groq="a-key", api_key_mistral="",
        api_key_nvidia="", api_key_gemini="", api_key_openrouter="",
        api_key_custom="", outputs_dir=str(tmp_path), detected_language="",
    )
    _, segmen = runner._transcribe(cfg)
    assert [w["word"] for s in segmen for w in s["words"]] == ["local"]


def test_a_hosted_failure_raises_when_the_chain_has_no_local_link(tmp_path, monkeypatch):
    """Silently spending 94 minutes on CPU Whisper because a hosted call failed
    is exactly the surprise this pipeline exists to avoid."""
    from clipping import runner
    from clipping.providers import stt as stt_mod

    def boom(*a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(stt_mod, "transcribe", boom)
    cfg = SimpleNamespace(
        file_video_asli="v.mp4", max_kata_per_subtitle=5, whisper_model="tiny",
        whisper_device="cpu", whisper_compute_type="int8",
        stt_chain="groq/whisper-large-v3-turbo",
        output_language="auto", api_key_groq="a-key", api_key_mistral="",
        api_key_nvidia="", api_key_gemini="", api_key_openrouter="",
        api_key_custom="", outputs_dir=str(tmp_path), detected_language="",
    )
    with pytest.raises(RuntimeError):
        runner._transcribe(cfg)


def test_an_explicit_output_language_is_passed_as_a_transcription_hint():
    from clipping import runner

    assert runner._requested_language(SimpleNamespace(output_language="fr")) == "fr"
    assert runner._requested_language(SimpleNamespace(output_language="auto")) is None
    assert runner._requested_language(SimpleNamespace()) is None
