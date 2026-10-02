"""The runner checks what it made before calling a render complete (the log
sweep of 2026-10-02):

- **The music bed.** Phase 6 stage 10 saw ffmpeg 7.1.5 cut the bgm stem
  short (10 of 12 runs with an mp4 input of the audio stage, once in ~76
  plain runs, never reproduced). After stage ``A`` the mix and its stems are
  measured from their WAV headers (``runner.wav_seconds``: any sample format;
  the renderer writes 32-bit float, which the stdlib ``wave`` refuses); one
  shorter than the episode by more than a frame fails the render at ``A``,
  nothing published.
- **The frame count.** A final whose framemd5 lists other than the timeline's
  ``total_frames`` is not published (the 30/45-frame bug class).

Files that are not a WAV / a framemd5 are not measured, so the runner tests'
stand-in bytes still pass; the real-ffmpeg goldens exercise both checks.
The plan and the fake ffmpeg are ``tests/test_aistory_render_runner.py``'s.
Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import os
import struct
import wave

import test_aistory_render_runner as trr
from clipping.aistory.render import plan as plan_mod
from clipping.aistory.render import runner


def _float_wav(path, seconds, *, rate=48000, channels=2):
    """A 32-bit float WAV (format tag 3), as the renderer writes them."""
    frames = int(round(seconds * rate))
    align = 4 * channels
    data = bytes(frames * align)
    fmt = struct.pack("<HHIIHH", 3, channels, rate, rate * align, align, 32)
    with open(path, "wb") as handle:
        handle.write(b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE")
        handle.write(b"fmt " + struct.pack("<I", len(fmt)) + fmt)
        handle.write(b"LIST" + struct.pack("<I", 4) + b"INFO")  # an extra chunk to skip
        handle.write(b"data" + struct.pack("<I", len(data)) + data)


def _framemd5(path, frames):
    lines = ["#format: frame checksums", "#version: 2", "#hash: MD5", "#tb 0: 1/30",
             "#stream#, dts,        pts, duration,     size, hash"]
    lines += [f"0, {i:10d}, {i:10d},        1,  3110400, 0123456789abcdef0123456789abcdef" for i in range(frames)]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


class RealFormats(trr.FakeFFmpeg):
    """The runner tests' fake, whose audio stage writes real (small, mono
    8 kHz) WAV files and whose M stage writes a real framemd5 -- each of a
    length the test chooses."""

    def __init__(self, *, seconds, short=None, frames=None):
        super().__init__()
        self.seconds, self.short, self.frames = seconds, dict(short or {}), frames

    def __call__(self, argv, *, cwd, stdin, stdout, stderr):
        proc = super().__call__(argv, cwd=cwd, stdin=stdin, stdout=stdout, stderr=stderr)
        for out in trr._outputs_of(argv):
            path = os.path.join(cwd, out)
            if out.endswith(".wav"):
                _float_wav(path, self.short.get(out, self.seconds), rate=8000, channels=1)
            elif out.endswith(".framemd5") and self.frames is not None:
                _framemd5(path, self.frames)
        return proc


# ------------------------------------------------------------ the readers

def test_wav_seconds_reads_float_and_pcm_and_refuses_stand_ins(tmp_path):
    _float_wav(tmp_path / "f.wav", 2.5)
    assert runner.wav_seconds(str(tmp_path / "f.wav")) == 2.5
    with wave.open(str(tmp_path / "p.wav"), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(bytes(2 * 16000 * 3))
    assert runner.wav_seconds(str(tmp_path / "p.wav")) == 3.0
    (tmp_path / "fake.wav").write_bytes(b"fake:mix.wav")
    assert runner.wav_seconds(str(tmp_path / "fake.wav")) is None
    assert runner.wav_seconds(str(tmp_path / "missing.wav")) is None


def test_framemd5_frames_counts_video_frames_only_in_a_real_file(tmp_path):
    _framemd5(tmp_path / "a.framemd5", 143)
    assert runner.framemd5_frames(str(tmp_path / "a.framemd5")) == 143
    (tmp_path / "b.framemd5").write_text("fake:episode_final.framemd5")
    assert runner.framemd5_frames(str(tmp_path / "b.framemd5")) is None


# ------------------------------------------------------------ the render

def test_a_render_whose_audio_and_frames_match_completes(tmp_path):
    plan = trr._plan(tmp_path)
    fake = RealFormats(seconds=plan["timeline"]["total_s"], frames=plan["timeline"]["total_frames"])

    result = trr._run(tmp_path, plan, fake)

    assert result["state"] == "completed", result["error"]


def test_a_music_bed_cut_short_fails_the_render_at_the_audio_stage(tmp_path):
    plan = trr._plan(tmp_path)
    total = plan["timeline"]["total_s"]
    fake = RealFormats(seconds=total, short={plan_mod.STEMS_REL["bgm"]: total - 1.5},
                       frames=plan["timeline"]["total_frames"])

    result = trr._run(tmp_path, plan, fake)

    assert result["state"] == "failed" and result["failed_stage"] == "A"
    assert "stems/bgm.wav lasts" in result["error"] and "render again" in result["error"]
    assert "F" not in result["ran"]  # nothing after the mix ran
    assert not (tmp_path / "episode_final.mp4").exists()


def test_a_final_with_missing_frames_is_never_published(tmp_path):
    plan = trr._plan(tmp_path)
    expected = plan["timeline"]["total_frames"]
    fake = RealFormats(seconds=plan["timeline"]["total_s"], frames=expected - 15)

    result = trr._run(tmp_path, plan, fake)

    assert result["state"] == "failed"
    assert f"{expected - 15} video frames where the timeline has {expected}" in result["error"]
    assert not (tmp_path / "episode_final.mp4").exists()
    assert not (tmp_path / "render_manifest.last_good.json").exists()  # never the baseline
