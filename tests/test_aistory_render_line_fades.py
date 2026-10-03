"""The episode mix fades every dialogue line at its own file's edges, on a
real ffmpeg (``filtergraph.audio_mix_argv``; phase 7 follow-up, a bug fix
for every story): 5 ms in from the file's first sample, 10 ms out to its
last -- the file's real end, not the line's ``duration_s``, which Edge sets
to the last word's end while its file runs on. So no line, of any engine,
can click in or out.

Skipped without ffmpeg (the CI image has it, as for the golden renders).
Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import array
import math
import shutil
import struct
import subprocess
import wave

import pytest

import test_aistory_render_graph as trg
from clipping.aistory.render import filtergraph

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")

RATE_IN = 24000
RATE_OUT = 48000
OVERRUN_S = 0.3  # the file runs on past duration_s, as an Edge mp3 does


def _tone(path, seconds):
    samples = array.array("h", [int(0.5 * 32767 * math.sin(2 * math.pi * 440 * i / RATE_IN))
                                for i in range(int(seconds * RATE_IN))])
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE_IN)
        wav.writeframes(samples.tobytes())


def _left_channel(path) -> array.array:
    """The left channel of a 32-bit float WAV (WAVE_FORMAT_EXTENSIBLE: the
    stdlib wave module does not read it)."""
    data = path.read_bytes()
    at = data.find(b"data")
    size = struct.unpack("<I", data[at + 4:at + 8])[0]
    samples = array.array("f")
    samples.frombytes(data[at + 8:at + 8 + size])
    return samples[0::2]


def _peak(samples, start_s, end_s) -> float:
    return max(abs(x) for x in samples[int(start_s * RATE_OUT):int(end_s * RATE_OUT)])


def test_a_line_fades_in_at_its_file_start_and_out_at_its_file_end_not_at_duration_s(tmp_path):
    tl = trg._first_shot_dissolve()
    (tmp_path / "in").mkdir()
    (tmp_path / "stems").mkdir()
    lines = {}
    for line in tl["lines"]:
        rel = f"in/{line['line_id']}.wav"
        _tone(tmp_path / rel, line["duration_s"] + OVERRUN_S)
        lines[line["line_id"]] = rel
    argv = filtergraph.audio_mix_argv(tl, line_inputs=lines, sfx_inputs={}, bgm_input=None,
                                      ending=trg._ending(tl), out_rel="mix.wav", stems_rel=dict(trg.STEMS))
    done =subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-800:]

    dialogue = _left_channel(tmp_path / "stems" / "dialogue.wav")
    line = tl["lines"][0]
    start, end_file = line["start_s"], line["start_s"] + line["duration_s"] + OVERRUN_S
    full = _peak(dialogue, start + 0.5, start + 0.6)  # the line's own level (mono upmixed to stereo: -3 dB)
    assert full > 0.3
    # In: silent at the file's first sample, full a few ms later.
    assert _peak(dialogue, start, start + 0.0005) < 0.1 * full
    assert _peak(dialogue, start + 0.006, start + 0.012) > 0.9 * full
    # At duration_s -- inside the file -- nothing is faded.
    at = start + line["duration_s"]
    assert _peak(dialogue, at - 0.01, at + 0.01) > 0.9 * full
    # Out: full until the last 10 ms of the file, near silent at its last sample.
    assert _peak(dialogue, end_file - 0.03, end_file - 0.015) > 0.9 * full
    assert _peak(dialogue, end_file - 0.001, end_file) < 0.15 * full
    assert _peak(dialogue, end_file + 0.002, end_file + 0.02) < 1e-4
