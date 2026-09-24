"""The hardware-encoder probe runs once per render, not once per clip.

detect_video_encoder is called by every render path for every clip, and each
call ran `ffmpeg -encoders` once per candidate plus up to four one-second test
encodes. On a CPU host whose ffmpeg build lists h264_nvenc/amf/vaapi -- every
probe fails -- that was seven ffmpeg processes before each clip's real encode.

The encoder list is a property of the ffmpeg binary and is cached for the
process. A runtime probe's answer is cached for _PROBE_TTL_SECONDS, so a long-
lived server still notices a GPU that appeared or went away.

ffmpeg_utils imports only subprocess, so it is loaded by path (clipping.studio
is shadowed by clipping/studio.py) and runs in the pytest-only CI environment.
"""

import importlib.util
import pathlib
import subprocess

import pytest

PATH = pathlib.Path(__file__).resolve().parents[1] / "clipping" / "studio" / "ffmpeg_utils.py"

CPU_ARGS_1080 = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
    "-maxrate", "8M", "-bufsize", "16M",
]


@pytest.fixture
def ffu(monkeypatch):
    spec = importlib.util.spec_from_file_location("ffmpeg_utils_under_test", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if "-encoders" in cmd:
            listing = " V..... h264_nvenc\n V..... h264_amf\n V..... h264_vaapi\n V..... libx264\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=listing, stderr="")
        # A CPU host: every hardware test encode fails.
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No capable devices found")

    monkeypatch.setattr(module.subprocess, "run", run)
    module.calls = calls
    return module


def test_three_clips_probe_once(ffu):
    for _ in range(3):
        assert ffu.detect_video_encoder(None, target_h=1080) == {
            "name": "libx264", "args": CPU_ARGS_1080}

    listings = [c for c in ffu.calls if "-encoders" in c]
    runtime = [c for c in ffu.calls if "-encoders" not in c]
    assert len(listings) == 1
    assert len(runtime) == 4  # nvenc p1, nvenc fast, amf, vaapi -- once each


def test_a_different_height_is_probed_with_its_own_arguments(ffu):
    """The render paths probe at the default 1080 while the runner probes at the
    real output height; the bitrate differs, so the probe must too."""
    ffu.detect_video_encoder(None, target_h=1080)
    before = len(ffu.calls)
    ffu.detect_video_encoder(None, target_h=720)
    assert len(ffu.calls) > before


def test_a_probe_is_repeated_once_its_answer_is_stale(ffu, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ffu.time, "monotonic", lambda: now[0])
    ffu.detect_video_encoder(None, target_h=1080)
    runtime_before = len([c for c in ffu.calls if "-encoders" not in c])

    now[0] += ffu._PROBE_TTL_SECONDS + 1
    ffu.detect_video_encoder(None, target_h=1080)

    runtime_after = len([c for c in ffu.calls if "-encoders" not in c])
    assert runtime_after == runtime_before * 2
