"""The cut-sheet renderer (tools/episode_cut.py, plan 35): segments trimmed to
their seconds (never slowed or frozen), the lines at their offsets, the hook and
the card as ASS events (UTF-8 whole), one render with synthetic clips."""

import importlib.util
import json
import pathlib
import shutil
import subprocess
import wave

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_tool():
    spec = importlib.util.spec_from_file_location("episode_cut", ROOT / "tools" / "episode_cut.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_ass_carries_the_lines_the_hook_and_the_card_whole(tmp_path):
    tool = load_tool()
    lines = [{"id": "l01", "start": 0.3, "end": 2.79, "text": "Vaulta… t'as laissé la porte grande ouverte."}]
    out = tmp_path / "x.ass"
    tool.write_ass(lines, 480, 832, str(out), hook={"text": "Il a hacké sa boîte… et son cœur ?", "seconds": 2.2},
                   card={"lines": ["Team Rida", "ou Team Marie-Jeanne ?", "Partie 2 demain"], "seconds": 1.5},
                   card_start=40.0)
    text = out.read_text(encoding="utf-8")
    assert "Style: Line," in text and "Style: Hook," in text and "Style: Card," in text
    assert "Dialogue: 0,0:00:00.30,0:00:02.94,Line,,0,0,0,,Vaulta… t'as laissé la porte grande ouverte." in text
    assert "Dialogue: 1,0:00:00.00,0:00:02.20,Hook,,0,0,0,,Il a hacké sa boîte…\\Net son cœur ?" in text
    assert "Dialogue: 1,0:00:40.00,0:00:41.50,Card,,0,0,0,,Team Rida\\Nou Team Marie-Jeanne ?\\N{\\c&H66D1FF&}Partie 2 demain" in text
    assert tool.wrap_words("Il a hacké sa boîte… et son cœur ?", 22) == ["Il a hacké sa boîte…", "et son cœur ?"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed")
def test_a_sheet_renders_trimmed_segments_lines_hook_and_card_and_refuses_a_stretch(tmp_path):
    tool = load_tool()
    outputs = tmp_path / "outputs"
    (outputs / "c").mkdir(parents=True)
    for name in ("a", "b"):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", "2", "-i", "color=c=gray:s=96x160:r=16",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(outputs / "c" / f"{name}.mp4")], check=True)
    with wave.open(str(outputs / "c" / "l1.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000)
        w.writeframes(b"\x10\x00" * int(24000 * 0.6))
    sheet = {"fps": 16, "hook": {"text": "Un hook accentué…", "seconds": 1.0},
             "segments": [{"clip": "c/a.mp4", "seconds": 1.5, "lines": [{"id": "l1", "wav": "c/l1.wav", "text": "Salut !", "at": 0.2}]},
                          {"clip": "c/b.mp4"}],
             "card": {"lines": ["Fin", "Demain"], "seconds": 1.0}}
    segments, lines, total = tool.timeline(sheet, str(outputs))
    assert [s["seconds"] for s in segments] == [1.5, 2.0] and total == 3.5
    assert lines[0]["start"] == 0.2 and abs(lines[0]["end"] - 0.8) < 0.01
    report = tool.render(sheet, str(tmp_path / "out.mp4"), outputs_dir=str(outputs))
    assert report["segments"] == 2 and report["lines"] == 1 and abs(report["duration_s"] - 4.5) < 0.3
    ass = (tmp_path / "out.ass").read_text(encoding="utf-8")
    assert "Hook,,0,0,0,,Un hook accentué…" in ass and "Card,,0,0,0,,Fin\\N{\\c&H66D1FF&}Demain" in ass
    with pytest.raises(tool.CutError, match="never slowed or frozen"):
        tool.timeline({"segments": [{"clip": "c/a.mp4", "seconds": 5}]}, str(outputs))
