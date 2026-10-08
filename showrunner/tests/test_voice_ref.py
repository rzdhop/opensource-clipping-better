"""The locked voice of a character from a take Rida picked (voice.reference_from_take): the speaker's own
lines only, cut by the clip check's timings, 2-10 s, never silent."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from showrunner import verify, voice  # noqa: E402

FFMPEG = shutil.which("ffmpeg") is not None


def _verdict(*lines, duration=10.0):
    return {"duration_s": duration, "lines": [{"speaker": who, "start_s": a, "end_s": b} for who, a, b in lines]}


def test_the_plan_keeps_the_speakers_lines_inside_their_own_part():
    v = _verdict(("ana", 0.5, 3.0), ("bo", 3.6, 5.0), ("ana", 5.4, 8.0))
    plan = voice.reference_plan(v, 10.0, "ana")
    assert plan == [(0.35, 3.3), (5.25, 8.3)]          # padded; the first stops at the middle of the pause (3.3)
    with pytest.raises(voice.VoiceRefError, match="at least"):
        voice.reference_plan(v, 10.0, "bo")               # 1.4 s of speech is too short
    with pytest.raises(voice.VoiceRefError, match="no line"):
        voice.reference_plan(v, 10.0, "cy")
    with pytest.raises(voice.VoiceRefError, match="not heard"):
        voice.reference_plan({"lines": [{"speaker": "ana", "start_s": None, "end_s": None}]}, 5.0, "ana")


def test_a_long_line_is_capped_at_ten_seconds():
    plan = voice.reference_plan(_verdict(("ana", 0.2, 11.5), duration=12.0), 12.0, "ana")
    assert plan == [(0.05, 10.05)]


def _clip(tmp_path):
    """A 6-s clip: a tone 0.5-2.8 s (ana), silence, a tone 3.6-5.6 s (bo)."""
    path = tmp_path / "take.mp4"
    graph = ("[1:a]atrim=0:2.3,adelay=500|500[a];[2:a]atrim=0:2.0,adelay=3600|3600[b];"
             "[a][b]amix=inputs=2:normalize=0,apad=whole_dur=6[out]")
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x568:r=24:d=6",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=6", "-f", "lavfi", "-i", "sine=frequency=600:duration=6",
                    "-filter_complex", graph, "-map", "0:v", "-map", "[out]", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-t", "6", str(path)], check=True, capture_output=True)
    return str(path)


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg")
def test_the_reference_is_the_speakers_line_as_a_mono_24k_wav(tmp_path):
    clip = _clip(tmp_path)
    v = _verdict(("ana", 0.5, 2.8), ("bo", 3.6, 5.6), duration=6.0)
    dest = str(tmp_path / "voice_ref.wav")
    got = voice.reference_from_take(clip, v, "ana", dest)
    info = verify.probe(dest)
    assert info["sample_rate"] == 24000 and info["has_audio"]
    assert got["parts"] == [(0.35, 3.1)] and info["duration_s"] == pytest.approx(2.75, abs=0.06)
    assert got["speech_s"] == pytest.approx(2.3) and not verify.loudness(dest)["silent"]
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".voice_ref_part")]


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg")
def test_a_silent_cut_is_refused_and_leaves_no_file(tmp_path):
    clip = _clip(tmp_path)
    v = _verdict(("ana", 5.8, 5.9), ("bo", 6.0, 8.6), duration=9.0)   # the timings point past the sound
    dest = str(tmp_path / "voice_ref.wav")
    with pytest.raises(voice.VoiceRefError, match="silent"):
        voice.reference_from_take(clip, v, "bo", dest)
    assert not os.path.exists(dest)
