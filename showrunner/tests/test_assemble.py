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
    # s01: 4 s clip, its line ends at 1.5 s -> cut at 1.8 s; s02: 3 s, two lines, ends at 2.6 s -> cut at 2.9 s
    # (the test tone never pauses, so the voice ends where the clip check says and no pause is tightened)
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
    assert seg1["seconds"] == pytest.approx(A.frames_floor(1.8)) and seg2["seconds"] == pytest.approx(A.frames_floor(2.9))
    assert all(s["seconds"] <= s["source_s"] for s in cut["segments"])
    assert cut["card_start"] == pytest.approx(seg1["seconds"] + seg2["seconds"], abs=1e-3)   # times kept to the ms
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


def _framings(ps):
    return [(p["framing"], round(p["start"], 3), round(p["end"], 3)) for p in ps]


def test_the_punch_in_follows_the_speaker_from_the_middle_of_the_silence():
    shot = {"characters": ["octave", "marie_jeanne"],
            "lines": [{"speaker": "octave"}, {"speaker": "marie_jeanne"}, {"speaker": "octave"}]}
    timed = [{"start_s": 0.0, "end_s": 3.98}, {"start_s": 4.06, "end_s": 6.0}, {"start_s": 6.32, "end_s": 9.06}]
    ps = A.pieces(shot, timed, 9.375)
    assert [p["framing"] for p in ps] == ["wide", "right", "left"]
    assert ps[0]["start"] == 0 and ps[-1]["end"] == 9.375                           # the clip keeps its length
    assert all(a["end"] == b["start"] for a, b in zip(ps, ps[1:]))                    # back to back
    assert ps[1]["start"] == pytest.approx(A.frames_floor((3.98 + 4.06) / 2))
    assert ps[1]["zoom"] == A.PUNCH_SIDE and ps[0]["zoom"] == 1.0


def test_positions_override_the_order_and_three_characters_get_a_centre():
    shot = {"characters": ["a", "b", "c"], "positions": {"a": "right", "c": "left"},
            "lines": [{"speaker": "a"}, {"speaker": "b"}, {"speaker": "c"}]}
    timed = [{"start_s": 0, "end_s": 2}, {"start_s": 2.2, "end_s": 4}, {"start_s": 4.2, "end_s": 6}]
    assert [p["framing"] for p in A.pieces(shot, timed, 6.5)] == ["wide", "center", "left"]


def test_a_one_character_clip_punches_in_once_when_long_enough():
    solo = {"characters": ["rida"], "lines": [{"speaker": "rida"}]}
    ps = A.pieces(solo, [{"start_s": 0, "end_s": 6.9}], 7.375)
    assert _framings(ps) == [("wide", 0, round(A.frames_floor(7.375 * A.PUNCH_SOLO_AT), 3)),
                             ("center", round(A.frames_floor(7.375 * A.PUNCH_SOLO_AT), 3), 7.375)]
    assert ps[1]["zoom"] == A.PUNCH_SOLO
    assert _framings(A.pieces(solo, [{"start_s": 0, "end_s": 2.4}], 2.7)) == [("wide", 0, 2.7)]


def test_flash_cuts_merge_and_the_edit_can_be_switched_off():
    shot = {"characters": ["a", "b"], "lines": [{"speaker": "a"}, {"speaker": "b"}, {"speaker": "a"}]}
    timed = [{"start_s": 0, "end_s": 2}, {"start_s": 2.1, "end_s": 2.5}, {"start_s": 2.6, "end_s": 5}]
    ps = A.pieces(shot, timed, 5.5)                      # b's piece (0.5 s) is too short: it joins a neighbour
    assert all(p["end"] - p["start"] >= A.PIECE_MIN_S for p in ps)
    assert all(a["framing"] != b["framing"] for a, b in zip(ps, ps[1:]))
    assert _framings(A.pieces(shot, timed, 5.5, enabled=False)) == [("wide", 0, 5.5)]
    assert _framings(A.pieces({**shot, "punch_in": False}, timed, 5.5)) == [("wide", 0, 5.5)]


def test_the_crop_keeps_the_frame_size_and_the_top():
    assert A.punch_filter("wide", 1.0) == ""
    f = A.punch_filter("right", 1.25)
    assert "x=iw-ow" in f and f"y=ih*{A.PUNCH_TOP}" in f and f.endswith(f"scale={A.W}:{A.H},setsar=1")


def test_the_punched_episode_keeps_its_length(story):
    story.approve_take(1, "s01", "v1", "ok")
    story.approve_take(1, "s02", "v1", "ok")
    cut = A.plan(story, 1)
    assert [p["framing"] for p in cut["segments"][1]["pieces"]] == ["wide", "right"]   # paloma then rida
    report = A.assemble(story, 1, preset="ultrafast")
    assert report["cuts"] == 3
    info = verify.probe(story.path(story.final(1)))
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["duration_s"] == pytest.approx(report["expected_s"], abs=0.1)


def test_ass_helpers():
    assert A.ass_time(61.234) == "0:01:01.23"
    assert A.ass_colour("#4B8BC3") == "&HC38B4B&" and A.ass_colour("nope") is None
    assert A.ass_text("a {b} \\N c") == "a (b) /N c"


# ------------------------------------------------------------------ the tightened pauses

def test_the_voice_end_follows_the_real_silence_within_half_a_second():
    assert A.voice_end(3.72, [(0.0, 0.36), (4.05, 5.0)]) == pytest.approx(4.05)       # the timed end was early
    assert A.voice_end(3.72, [(3.5, 5.0)]) == pytest.approx(3.72)                       # silence already started
    assert A.voice_end(3.72, [(4.9, 5.0)]) == pytest.approx(3.72 + A.VOICE_END_MAX_S)   # never more than 0.5 s
    assert A.voice_end(3.72, []) == pytest.approx(3.72)                                 # no pause: trust the check


def test_only_long_pauses_inside_the_speech_are_tightened():
    shot = {"characters": ["octave"], "lines": [{"speaker": "octave"}]}
    timed = [{"start_s": 0.0, "end_s": 4.1}]
    sil = [(0.0, 0.3), (1.39, 2.78), (2.9, 3.2), (4.27, 5.0)]     # lead-in, a 1.4 s pause, a short one, the tail
    cut = A.pauses_to_cut(shot, timed, sil, 4.5)
    assert cut == [(A.frames_floor(1.39 + 0.1), A.frames_floor(2.78 - 0.1))]
    assert A.pauses_to_cut({**shot, "tighten": False}, timed, sil, 4.5) == []
    assert A.pauses_to_cut({**shot, "punch_in": False}, timed, sil, 4.5) == []
    assert A.pauses_to_cut({**shot, "sfx": [{"at": 2.0}]}, timed, sil, 4.5) == []     # the effect keeps its pause
    assert A.pauses_to_cut(shot, timed, [(0.3, 1.0)], 4.5) == []                      # would leave a 0.4 s piece


def test_every_tightened_pause_is_a_change_of_framing():
    solo = {"characters": ["octave"], "lines": [{"speaker": "octave"}]}
    timed = [{"start_s": 0.0, "end_s": 4.1}]
    cut = [(1.5, 2.6667)]
    ps = A.edit(solo, timed, 4.4, cut)
    assert [p["framing"] for p in ps] == ["wide", "center"]
    assert ps[0]["end"] == 1.5 and ps[1]["start"] == 2.6667                          # the pause is left out
    duo = {"characters": ["a", "b"], "lines": [{"speaker": "a"}, {"speaker": "b"}]}
    timed2 = [{"start_s": 0.0, "end_s": 3.0}, {"start_s": 5.0, "end_s": 8.0}]
    ps = A.edit(duo, timed2, 8.3, [(1.0, 2.2)])                                      # a pause inside a's line
    framings = [p["framing"] for p in ps]
    assert framings[:2] == ["wide", "left"] and all(x != y for x, y in zip(framings, framings[1:]))
    assert A.out_time(4.0, [(1.0, 2.2)]) == pytest.approx(2.8) and A.out_time(1.5, [(1.0, 2.2)]) == 1.0


def _paused_clip(path, seconds, pause):
    """A test clip whose sound stops between pause[0] and pause[1] (a line, a silence, a line)."""
    a, b = pause
    tone = f"aevalsrc='if(between(t,{a},{b}),0,0.3*sin(2*PI*300*t))':s=48000:d={seconds}"
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", f"testsrc=s=704x1280:r=24:d={seconds}",
                    "-f", "lavfi", "-i", tone, "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", str(path)], check=True, capture_output=True)
    return str(path)


def test_the_tightened_episode_is_shorter_and_keeps_its_subtitles_in_place(tmp_path):
    s = S.Story.create(str(tmp_path), "pause", title="Pause", language="fr")
    s.write_text(s.sheet("octave"), "# Octave\n\n## Head\nOctave, a pineapple man\n\n## Voice (en)\ndeep\n")
    rel = s.clip(1, "s01", "v1")
    s.copy_in(_paused_clip(tmp_path / "raw.mp4", 5, (1.4, 2.8)), rel)    # the line ends at 4.1, the clip at 5.0
    s.add_take(1, "s01", rel, seed=1, verdict={"state": "ok", "start_s": 0.0, "end_s": 4.1,
                                              "lines": [{"start_s": 0.0, "end_s": 4.1}]})
    s.approve_take(1, "s01", "v1", "ok")
    s.write_json(s.shots(1), {"card": None, "shots": [{"id": "s01", "characters": ["octave"],
                                                       "lines": [{"speaker": "octave", "text": "Elle signera tout."}]}]})
    cut = A.plan(s, 1)
    seg = cut["segments"][0]
    assert seg["cut_out"] and 1.0 < seg["tightened_s"] < 1.3                         # 1.4 s pause -> 0.2 s
    assert seg["seconds"] == pytest.approx(seg["trim"] - seg["tightened_s"], abs=1e-3)
    assert cut["events"][0]["end"] <= seg["seconds"] + 1e-6                          # the subtitle moved with it
    report = A.assemble(s, 1, preset="ultrafast")
    final = s.path(s.final(1))
    assert verify.probe(final)["duration_s"] == pytest.approx(report["expected_s"], abs=0.1)
    gaps = [e - a for a, e in verify.silences(final, min_s=0.15) if a > 0.2 and e < seg["seconds"] - 0.5]
    assert gaps and max(gaps) < 0.4                                                   # the long pause is gone
