"""Loudness normalisation is available, and off unless asked for.

Nothing in the render levelled the audio: the only dynamics were BGM ducking.
The hook, the voice-over and the main clip are mixed separately, and clips from
different sources arrive at whatever level they were recorded at, so a batch
of clips jumps in volume from one to the next. Short-form platforms turn loud
audio down but do not turn quiet audio up.

--loudnorm runs one two-pass EBU R128 loudnorm (I=-14, TP=-1.5, LRA=11) over
each finished file: video stream-copied, audio re-encoded. It is OPT-IN, like
the hook glitch (DEC-050) and clip length (DEC-051): turning it on by default
would silently change the level of every existing script's output and add an
AAC generation to it.

The command builder and parser are stdlib (clipping/loudness.py). The real
ffmpeg test runs where ffmpeg exists -- GitHub's ubuntu-latest image has it.
"""

import json
import pathlib
import re
import shutil
import subprocess
import types

import pytest

from clipping import loudness

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Real stderr from `ffmpeg ... -af loudnorm=...:print_format=json -f null -`.
MEASURED_STDERR = """\
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, mono, fltp, 69 kb/s (default)
[Parsed_loudnorm_0 @ 0000026eab024b40]
{
\t"input_i" : "-21.85",
\t"input_tp" : "-14.55",
\t"input_lra" : "0.00",
\t"input_thresh" : "-31.85",
\t"output_i" : "-14.05",
\t"output_tp" : "-6.70",
\t"output_lra" : "0.00",
\t"output_thresh" : "-24.05",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.05"
}
[out#0/null @ 0000026eab024f80] video:298KiB audio:9000KiB subtitle:0KiB
"""


# ------------------------------------------------------------ the commands (CI)

def test_the_measuring_pass_prints_json_and_writes_nothing():
    cmd = loudness.measure_cmd("clip.mp4")
    af = cmd[cmd.index("-af") + 1]
    assert af.startswith("loudnorm=I=-14:TP=-1.5:LRA=11")
    assert "print_format=json" in af
    assert cmd[-3:] == ["-f", "null", "-"]


def test_the_measurement_is_parsed():
    measured = loudness.parse_measurement(MEASURED_STDERR)
    assert measured == {"input_i": "-21.85", "input_tp": "-14.55", "input_lra": "0.00",
                        "input_thresh": "-31.85", "target_offset": "0.05"}


@pytest.mark.parametrize("stderr", ["", "no json here", "[Parsed_loudnorm_0] {not json}",
                                    '[Parsed_loudnorm_0]\n{"input_i": "-inf"}'])
def test_an_unusable_measurement_is_none(stderr):
    assert loudness.parse_measurement(stderr) is None


def test_the_second_pass_uses_the_measurement_and_copies_the_video():
    measured = loudness.parse_measurement(MEASURED_STDERR)
    cmd = loudness.apply_cmd("in.mp4", "out.mp4", measured, sample_rate=44100)
    af = cmd[cmd.index("-af") + 1]
    for part in ("measured_I=-21.85", "measured_TP=-14.55", "measured_LRA=0.00",
                 "measured_thresh=-31.85", "offset=0.05", "linear=true"):
        assert part in af, part
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-ar") + 1] == "44100"
    assert cmd[-1] == "out.mp4"


def _fake_run(measure_stderr=MEASURED_STDERR, measure_rc=0, apply_rc=0, rate="48000"):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, stdout=rate + "\n", stderr="")
        if "-f" in cmd and cmd[cmd.index("-f") + 1] == "null":
            return subprocess.CompletedProcess(cmd, measure_rc, stdout="", stderr=measure_stderr)
        if apply_rc == 0:
            pathlib.Path(cmd[-1]).write_bytes(b"normalised")
        return subprocess.CompletedProcess(cmd, apply_rc, stdout="", stderr="apply failed")

    run.calls = calls
    return run


def test_a_file_is_replaced_by_its_normalised_version(tmp_path):
    clip = tmp_path / "highlight_rank_1_ready.mp4"
    clip.write_bytes(b"original")
    assert loudness.normalize_file(str(clip), run=_fake_run(), on_log=lambda *a: None)
    assert clip.read_bytes() == b"normalised"
    assert sorted(p.name for p in tmp_path.iterdir()) == [clip.name]


@pytest.mark.parametrize("fake", [
    _fake_run(measure_rc=1),
    _fake_run(measure_stderr="nothing useful"),
    _fake_run(apply_rc=1),
])
def test_a_failure_keeps_the_rendered_clip(tmp_path, fake):
    """Best-effort: the clip is already rendered, and a levelling failure must
    not cost it."""
    clip = tmp_path / "highlight_rank_1_ready.mp4"
    clip.write_bytes(b"original")
    lines = []
    assert loudness.normalize_file(str(clip), run=fake, on_log=lines.append) is False
    assert clip.read_bytes() == b"original"
    assert sorted(p.name for p in tmp_path.iterdir()) == [clip.name]
    assert lines and "loudness" in lines[0].lower()


def test_the_source_sample_rate_is_kept(tmp_path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"original")
    run = _fake_run(rate="44100")
    loudness.normalize_file(str(clip), run=run, on_log=lambda *a: None)
    apply = next(c for c in run.calls if c[0] == "ffmpeg" and "-c:v" in c)
    assert apply[apply.index("-ar") + 1] == "44100"


# ------------------------------------------------------------ the default (CI)

def test_it_is_opt_in_everywhere():
    """One default in five places, read as text where importing would need
    pydantic (DEC-012) -- the same shape as the hook glitch's guard."""
    from clipping import config

    assert config.LOUDNORM is False
    models = (ROOT / "web" / "api" / "models.py").read_text(encoding="utf-8")
    assert "loudnorm: bool = False" in models
    adapter = (ROOT / "web" / "api" / "config_adapter.py").read_text(encoding="utf-8")
    assert 'payload.get("loudnorm", False)' in adapter
    jsx = (ROOT / "web" / "dashboard" / "src" / "pages" / "NewJob.jsx").read_text(encoding="utf-8")
    assert "const [loudnorm, setLoudnorm] = useState(false)" in jsx
    assert "loudnorm: loudnorm," in jsx
    assert "if (config.loudnorm !== undefined) setLoudnorm(config.loudnorm)" in jsx


def test_the_cli_flag(tmp_path):
    from clipping.config import build_config

    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 2048)
    assert build_config(["--video", str(video)]).loudnorm is False
    assert build_config(["--video", str(video), "--loudnorm"]).loudnorm is True


def test_both_renderers_level_their_finished_files():
    core = (ROOT / "clipping" / "studio" / "core.py").read_text(encoding="utf-8")
    story = (ROOT / "clipping" / "story_runner.py").read_text(encoding="utf-8")
    for name, text in (("core.py", core), ("story_runner.py", story)):
        assert 'getattr(cfg, "loudnorm", False)' in text, name
        assert "loudness.normalize_file(" in text, name
    # After the concat and the edge glow, before the thumbnail: the last write.
    assert core.index("loudness.normalize_file(") > core.index("EDGE GLOW POST-PROCESSING")
    assert core.index("loudness.normalize_file(") < core.index("buat_thumbnail(out_vid")


# ------------------------------------------------------------ real ffmpeg

def _integrated_lufs(path):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                          "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", out)[-1])


def test_a_quiet_clip_comes_out_at_minus_14_lufs(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not installed here")
    clip = tmp_path / "quiet.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=gray:s=320x240:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "6", "-af", "volume=-24dB", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)], check=True)
    before_video = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(clip),
                                   "-map", "0:v", "-f", "md5", "-"], capture_output=True, text=True).stdout

    assert _integrated_lufs(clip) < -30
    assert loudness.normalize_file(str(clip), on_log=lambda *a: None)
    assert abs(_integrated_lufs(clip) - (-14.0)) < 1.0

    after_video = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(clip),
                                  "-map", "0:v", "-f", "md5", "-"], capture_output=True, text=True).stdout
    assert before_video == after_video  # the picture is untouched
