"""The warning that a CPU Whisper run is going to take a long time.

Regression test for a real job that spent 94 minutes transcribing a 20-minute
video on CPU with nothing said about it until it was over.
"""

import pytest

from clipping import engine
from clipping.engine import _warn_if_cpu_transcription_will_be_slow as warn


def test_warns_on_a_long_cpu_run():
    msg = warn(1211, "cpu")
    assert msg is not None
    # the numbers a user needs to decide whether to wait
    assert "20 minutes of audio" in msg
    assert "93 minutes" in msg          # what the real job actually took
    # and the remedy, which is the whole point of saying anything
    assert ".vtt" in msg


def test_silent_on_gpu():
    assert warn(1211, "cuda") is None


def test_silent_on_a_short_clip():
    """A notice on every short clip would be noise."""
    assert warn(120, "cpu") is None


def test_threshold_is_the_boundary():
    just_under = (engine.CPU_WHISPER_WARN_THRESHOLD_SECONDS / engine.CPU_WHISPER_REALTIME_FACTOR) - 1
    just_over = (engine.CPU_WHISPER_WARN_THRESHOLD_SECONDS / engine.CPU_WHISPER_REALTIME_FACTOR) + 1
    assert warn(just_under, "cpu") is None
    assert warn(just_over, "cpu") is not None


def test_estimate_matches_the_measured_job():
    """1211s of audio took ~93 minutes on this project's own CPU path."""
    minutes = engine.estimate_cpu_transcription_seconds(1211) / 60
    assert 85 <= minutes <= 100


def test_the_warning_is_severity_warn_for_the_activity_feed():
    """web/api infers severity from the pipeline's emoji, so the marker matters."""
    assert warn(1211, "cpu").lstrip().startswith("⚠")


def test_the_cuda_fallback_warning_is_not_printed_twice(monkeypatch, capsys):
    """transcribe_video resolves the device once and passes the result down.

    resolve_whisper_runtime warns when it falls back from CUDA to CPU. Adding a
    second call site to learn the device for the slow-run estimate would have
    repeated that warning into the activity feed.
    """
    from types import SimpleNamespace

    class _Seg:
        start, end, text, words = 0.0, 1.0, "hai", []

    class _Model:
        def transcribe(self, *a, **k):
            return iter([_Seg()]), SimpleNamespace(duration=60.0)

    loaded = {}

    def _fake_load(model_size, device, compute_type):
        loaded["args"] = (model_size, device, compute_type)
        return _Model()

    monkeypatch.setattr(engine, "load_whisper_model", _fake_load)
    monkeypatch.setattr("clipping.device.whisper_cuda_available", lambda: False)

    engine.transcribe_video("video.mp4", device="cuda", compute_type="auto")

    out = capsys.readouterr().out
    assert out.count("CUDA was requested") == 1, (
        f"the CUDA fallback warning appeared {out.count('CUDA was requested')} times"
    )
    # and the already-resolved pair is what reaches the loader
    assert loaded["args"] == ("large-v3", "cpu", "int8")
