"""Assembly (stage 1.5) on synthetic clips in a temp story: approved clips only, never slowed, the trim
after the last word, the subtitles, the card, the optional music bed."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from showrunner import assemble as A, store as S, verify  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")


def _clip(path, seconds, freq=300):
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", f"testsrc=s=704x1280:r=24:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate=48000",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
                   check=True, capture_output=True)
    return str(path)


@pytest.fixture
def story(tmp_path):
    s = S.Story.create(str(tmp_path), "demo", title="Démo d'été", language="fr")
    s.write_text(s.sheet("paloma"), "# Paloma\n\n## Head\nPaloma, a mango woman\n\n## Voice (en)\nbright\n\n## Colour\n#FF9800\n")
    s.write_text(s.sheet("rida"), "# Rida\n\n## Head\nRida, a kiwi man\n\n## Voice (en)\ncalm\n")
    raw = tmp_path / "raw"
    raw.mkdir()
    # s01: 4 s clip, its line ends at 1.5 s -> cut at 1.8 s; s02: 3 s, two lines, ends at 2.6 s -> kept whole
    for shot, seconds, verdict in (
        ("s01", 4, {"state": "ok", "end_s": 1.5, "lines": [{"start_s": 0.2, "end_s": 1.5}]}),
        ("s02", 3, {"state": "ok", "end_s": 2.6, "lines": [{"start_s": 0.1, "end_s": 1.2}, {"start_s": 1.4, "end_s": 2.6}]}),
    ):
        rel = s.clip(1, shot, "v1")
        s.copy_in(_clip(raw / f"{shot}.mp4", seconds), rel)
        s.add_take(1, shot, rel, seed=33, verdict=verdict)
    s.write_json(s.shots(1), {"shots": [
        {"id": "s01", "lines": [{"speaker": "paloma", "text": "C'est qui, le kiwi ?"}]},
        {"id": "s02", "lines": [{"speaker": "paloma", "text": "Personne !"}, {"speaker": "rida", "text": "Sympa {vraiment}."}]},
    ]})
    return s


def test_unapproved_shots_stop_the_assembly_by_name(story):
    story.approve_take(1, "s01", "v1", "ok")
    with pytest.raises(A.AssemblyError, match="no approved clip for s02"):
        A.plan(story, 1)


def test_the_cut_trims_after_the_last_word_and_never_slows(story):
    story.approve_take(1, "s01", "v1", "ok")
    story.approve_take(1, "s02", "v1", "ok")
    cut = A.plan(story, 1)
    seg1, seg2 = cut["segments"]
    assert seg1["seconds"] == pytest.approx(A.frames_floor(1.8)) and seg2["seconds"] == pytest.approx(3.0)
    assert all(s["seconds"] <= s["source_s"] for s in cut["segments"])
    assert cut["card_start"] == pytest.approx(seg1["seconds"] + 3.0, abs=1e-3)   # times kept to the ms
    assert cut["card"]["lines"] == ["Démo d'été", "Partie 2 demain"]
    e = cut["events"]
    assert [(x["speaker"], x["name"]) for x in e] == [("paloma", "PALOMA"), ("paloma", "PALOMA"), ("rida", "RIDA")]
    assert e[0]["colour"] == "&H0098FF&"                                # from the sheet's ## Colour
    assert e[1]["end"] <= e[2]["start"]                                 # lines of one clip never overlap
    assert e[2]["start"] == pytest.approx(seg2["start"] + 1.4, abs=1e-3)


def test_the_episode_is_1080x1920_with_its_sound_subtitles_and_card(story, tmp_path):
    story.approve_take(1, "s01", "v1", "ok")
    story.approve_take(1, "s02", "v1", "ok")
    bgm = tmp_path / "bed.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "sine=frequency=110:duration=2",
                    str(bgm)], check=True, capture_output=True)
    sheet = story.read_json(story.shots(1))
    sheet["bgm"] = {"file": str(bgm), "gain": 0.3}        # absolute paths pass through os.path.join
    story.write_json(story.shots(1), sheet)
    report = A.assemble(story, 1, preset="ultrafast")
    info = verify.probe(story.path(story.final(1)))
    assert (info["width"], info["height"], info["fps"], info["has_audio"]) == (1080, 1920, 24.0, True)
    assert info["duration_s"] == pytest.approx(report["expected_s"], abs=0.1)
    assert report["subtitles"] == 4 and report["unsubtitled"] == []      # 3 lines + the card
    ass = story.read_text("ep01/final.ass")
    assert "{\\fs52\\c&H0098FF&}PALOMA\\N{\\fs74\\c&HFFFFFF&}C'est qui, le kiwi ?" in ass
    assert "Sympa (vraiment)." in ass and "Partie 2 demain" in ass
    assert not verify.loudness(story.path(story.final(1)))["silent"]
    assert story.exists("ep01/final_sheet.jpg") and story.exists(story.metadata(1))


def test_a_locked_final_is_not_overwritten(story):
    story.approve_take(1, "s01", "v1", "ok")
    story.approve_take(1, "s02", "v1", "ok")
    story.write_bytes(story.final(1), b"approved cut")
    story.lock(story.final(1), "Rida approved the episode")
    with pytest.raises(S.Locked):
        A.assemble(story, 1, preset="ultrafast")


def test_ass_helpers():
    assert A.ass_time(61.234) == "0:01:01.23"
    assert A.ass_colour("#4B8BC3") == "&HC38B4B&" and A.ass_colour("nope") is None
    assert A.ass_text("a {b} \\N c") == "a (b) /N c"
