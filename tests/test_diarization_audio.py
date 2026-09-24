"""extract_audio says why ffmpeg failed.

It ran ffmpeg with stderr sent to DEVNULL and check=True, so a corrupt or
unsupported source surfaced as `Command [...] returned non-zero exit status 1`
-- which is exactly what the runner and the web worker print when they fall
back to a normal render, with ffmpeg's actual reason thrown away.

clipping.diarization imports only os and subprocess at module level, so this
runs in the pytest-only CI environment.
"""

import subprocess

import pytest

from clipping import diarization

EXPECTED_CMD = [
    "ffmpeg", "-hide_banner", "-y", "-i", "in.mp4", "-vn", "-acodec", "pcm_s16le",
    "-ar", "16000", "-ac", "1", "out.wav",
]


def _fake_run(returncode, stderr=b""):
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, stdout=b"", stderr=stderr)

    return run, calls


def test_a_failure_carries_ffmpegs_reason(monkeypatch):
    noise = b"".join(b"frame=%d\n" % i for i in range(100))
    run, _ = _fake_run(1, noise + b"in.mp4: Invalid data found when processing input\n")
    monkeypatch.setattr(diarization.subprocess, "run", run)

    with pytest.raises(RuntimeError) as excinfo:
        diarization.extract_audio("in.mp4", "out.wav")

    message = str(excinfo.value)
    assert "Invalid data found when processing input" in message
    assert "in.mp4" in message
    # The tail, not the whole log: progress noise must not bury the reason.
    assert "frame=10\n" not in message


def test_success_returns_the_wav_path_with_the_command_unchanged(monkeypatch):
    run, calls = _fake_run(0)
    monkeypatch.setattr(diarization.subprocess, "run", run)

    assert diarization.extract_audio("in.mp4", "out.wav") == "out.wav"
    assert calls[0][0] == EXPECTED_CMD
