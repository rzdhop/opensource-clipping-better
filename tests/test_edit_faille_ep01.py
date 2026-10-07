"""The final-edit tool of "Faille d'amour" ep01 (tools/edit_faille_ep01.py): the
timeline lays the lines on their shots one after the other and warns when a shot
needs a continuation clip; the ASS file carries every line at its time; the
whole edit renders when ffmpeg is present (tiny synthetic clips and lines)."""

import importlib.util
import os
import pathlib
import shutil
import subprocess
import wave

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_tool():
    spec = importlib.util.spec_from_file_location("edit_faille_ep01", ROOT / "tools" / "edit_faille_ep01.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ass_times_and_rows_follow_the_lines():
    tool = load_tool()
    assert tool.ass_time(0) == "0:00:00.00" and tool.ass_time(65.5) == "0:01:05.50"
    lines = [{"id": "l01_rida", "start": 0.3, "end": 2.79, "text": "Vaulta… ouverte."}]
    out = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "edit_test.ass"
    tool.write_ass(lines, 480, 832, str(out))
    text = out.read_text(encoding="utf-8")
    assert "PlayResX: 480" in text and "Dialogue: 0,0:00:00.30,0:00:02.94,Line,,0,0,0,,Vaulta… ouverte." in text
    assert set(tool.TEXTS) == {lid for ids in tool.LINES_ON.values() for lid in ids}  # every line has its text


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed")
def test_the_edit_renders_the_shots_their_continuations_the_lines_and_the_card(tmp_path, monkeypatch):
    tool = load_tool()
    clips = tmp_path / "clips"
    clips.mkdir()
    for name in [*tool.SHOTS, "clip02b"]:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", "1", "-i", "color=c=gray:s=96x160:r=16",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clips / f"{name}.mp4")], check=True)
    voices = tmp_path / "voices"
    voices.mkdir()
    for lid in tool.TEXTS:
        with wave.open(str(voices / f"{lid}.wav"), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000)
            w.writeframes(b"\x10\x00" * int(24000 * 0.4))
    monkeypatch.setattr(tool, "VOICES", str(voices))
    segments, lines, total = tool.timeline(str(clips))
    assert [s["shot"] for s in segments] == ["clip01", "clip02", "clip02", *tool.SHOTS[2:]]  # clip02 + its continuation
    assert len(lines) == 13 and abs(total - 11.0) < 0.1
    assert lines[0]["start"] == 0.3 and lines[2]["start"] > lines[1]["end"]  # one after the other on clip02
    report = tool.assemble(str(clips), str(tmp_path / "out.mp4"))
    assert report["segments"] == 11 and report["lines"] == 13
    assert abs(report["duration_s"] - (11.0 + tool.CARD_S)) < 0.3
    assert (tmp_path / "out.ass").exists()
