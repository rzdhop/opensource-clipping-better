"""One talking clip per line (AI Story, plan 35, DEC-318): the human watched
"Faille d'amour" ep01 made from the chat -- silent i2v clips with the voices
laid on top, a two-character shot carrying two lines (7 s of speech on a 5-s
clip the render stretched), the mouths still. The hand-made remedy that
worked is now the pipeline's on the ``own_gpu`` profile:

- **The split** (``steps/talking.split``, ``shot_verdict``): a shot of two
  lines or more that cannot talk as one S2V clip (two speakers, a two-shot,
  its speech past one chunk) is cut into one part per line -- each a close-up
  of that line's speaker, with that line alone on its track at its place in
  the part; the parts cover the shot end to end (never slowed). A one-line
  shot keeps plan 32 stage 8's single talking clip.
- **The video phase** (``assets.make_talk_parts``, ``make_closeup``): each
  part's close-up is a multi-reference edit (the speaker's sheet, then the
  shot's keyframe) stored as ``assets/shots/shot_NN.lNN.<ext>``; each part's
  clip ``assets/clips/shot_NN.lNN.mp4``; the clip record carries ``parts``.
- **The render** (``filtergraph.tier2_parts_argv``, ``render.plan``): the
  parts cut back to back to the shot's exact frames, no ``setpts``.
- **The regenerate target** ``shot:<ep>:<shot_id>:closeup:<line_id>``: that
  close-up and that part's clip again; the other parts served at $0.

Every provider is a fake; ffmpeg is real (on PATH here and in CI). Stdlib +
pytest (DEC-012).
"""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_talking_clips as ttc
from test_story_assets_step import hermetic, store  # noqa: F401 -- the step's fixtures (hermetic is autouse)
from test_story_keyframe_gate import built, unpaced  # noqa: F401 -- the free Gemini tier's pacing lifted
from test_story_lipsync import media, no_lipsync_chain  # noqa: F401 -- real mp4s; the shipped LIPSYNC_CHAIN
from test_story_talking_clips import no_runpod_env  # noqa: F401 -- no RunPod name of the machine
from test_story_video_phase import timings_path  # noqa: F401 -- the measured clip timings under tmp_path

S2V = ttc.S2V


def _mods():
    from clipping.aistory import schemas
    from clipping.aistory.render import filtergraph, golden_tier2, profiles
    from clipping.aistory.steps import assets, clips, lipsync, regenerate, talking

    return SimpleNamespace(schemas=schemas, filtergraph=filtergraph, golden_tier2=golden_tier2, profiles=profiles,
                           assets=assets, clips=clips, lipsync=lipsync, regenerate=regenerate, talking=talking)


def _two_speakers(store, tmp_path):
    """The own_gpu episode with sh12 (a medium single of Kiwilo saying l20,
    then l21, 5.1 s: past one chunk) turned into an exchange -- l21 spoken by
    Mangella -- and each line voiced by its own wav."""
    story_id = ttc._own_gpu(store, tmp_path)
    ec, script, board = tas._ec(store, story_id), eps._script(store, story_id), tas._board(store, story_id)
    script = copy.deepcopy(script)
    line = next(item for scene in script["scenes"] for item in scene["lines"] if item["line_id"] == "l21")
    line["speaker"] = "char_mangella"
    wavs = {line_id: ttc._wav(tmp_path / f"{line_id}.wav", 1.0 + index / 10)
            for index, line_id in enumerate(("l20", "l21"))}
    shot = next(item for item in board["shots"] if item["shot_id"] == "sh12")
    return ec, script, board, shot, wavs


# ================================================================ 1. the split

def test_a_two_speaker_two_line_shot_is_cut_into_one_talking_part_per_line(store, tmp_path):
    m = _mods()
    ec, script, board, shot, wavs = _two_speakers(store, tmp_path)
    timeline = m.lipsync.episode_timeline(ec, script, board)
    audio_of = lambda line: str(wavs.get(line["line_id"], wavs["l20"]))  # noqa: E731

    single = m.talking.verdict(ec, script, board, shot, timeline=timeline, audio_of=audio_of)
    assert single["talks"] is False and single["why"] == m.talking.SPEAKERS
    found = m.talking.shot_verdict(ec, script, board, shot, timeline=timeline, audio_of=audio_of)
    assert found["talks"] is True and found["single"] == m.talking.SPEAKERS and m.talking.is_split(found)

    entry = next(item for item in timeline["shots"] if item["shot_id"] == "sh12")
    timed = {item["line_id"]: item for item in timeline["lines"]}
    parts = found["parts"]
    assert [(part["line_id"], part["speaker"]) for part in parts] == [("l20", "char_kiwilo"),
                                                                     ("l21", "char_mangella")]
    # The parts cover the shot end to end: the first from its start, the cut in the pause between the lines.
    assert parts[0]["start_s"] == 0.0
    assert parts[1]["start_s"] == pytest.approx(parts[0]["duration_s"], abs=1e-3)
    assert sum(part["duration_s"] for part in parts) == pytest.approx(entry["duration_s"], abs=1e-3)
    l20_end = timed["l20"]["start_s"] + timed["l20"]["duration_s"] - entry["start_s"]
    l21_start = timed["l21"]["start_s"] - entry["start_s"]
    assert l20_end - 1e-3 <= parts[1]["start_s"] <= l21_start + 1e-3
    for part in parts:
        assert part["duration_s"] <= m.talking.MAX_PART_S + 1e-9
        (row,) = part["spec"]["lines"]
        # Each part's track holds its own line's wav alone, at its place in the part.
        assert row["line_id"] == part["line_id"] and row["path"] == str(wavs[part["line_id"]])
        assert row["at_s"] == pytest.approx(timed[part["line_id"]]["start_s"] - entry["start_s"] - part["start_s"],
                                            abs=1e-3)
        assert part["spec"]["clip_s"] == m.talking.TALK_CLIP_S and part["spec"]["tempo"] == 1.0
    assert found["spec"]["hash"] != parts[0]["spec"]["hash"]

    # A line re-voiced moves the combined hash (the clip goes stale).
    louder = ttc._wav(tmp_path / "l21b.wav", 2.0)
    moved = m.talking.shot_verdict(ec, script, board, shot, timeline=timeline,
                                   audio_of=lambda line: str(louder if line["line_id"] == "l21" else wavs["l20"]))
    assert moved["spec"]["hash"] != found["spec"]["hash"]


def test_a_one_line_shot_keeps_its_single_talking_clip_and_a_silent_one_stays_on_i2v(store, tmp_path):
    m = _mods()
    story_id = ttc._own_gpu(store, tmp_path)
    ec, script, board = tas._ec(store, story_id), eps._script(store, story_id), tas._board(store, story_id)
    wav = str(ttc._wav(tmp_path / "line.wav"))
    timeline = m.lipsync.episode_timeline(ec, script, board)
    by_id = {shot["shot_id"]: shot for shot in board["shots"]}
    for shot_id in ("sh03", "sh04", "sh01"):  # a one-line close-up; a one-line two-shot; no line
        single = m.talking.verdict(ec, script, board, by_id[shot_id], timeline=timeline, audio_of=lambda _l: wav)
        found = m.talking.shot_verdict(ec, script, board, by_id[shot_id], timeline=timeline,
                                       audio_of=lambda _l: wav)
        assert found == single and "parts" not in found, shot_id
    assert m.talking.shot_verdict(ec, script, board, by_id["sh03"], timeline=timeline,
                                  audio_of=lambda _l: wav)["talks"] is True


def test_the_cuts_fall_in_the_pauses_and_a_line_past_one_chunk_is_never_cut():
    m = _mods()
    cuts = m.talking._cuts
    assert cuts([(0.35, 3.2), (3.45, 5.5)], 5.5) == [0.0, pytest.approx(3.325)]
    assert cuts([(0.3, 2.0), (2.5, 4.0), (4.4, 6.0)], 6.4) == [0.0, pytest.approx(2.25), pytest.approx(4.2)]
    # A part that would run past one chunk and its hold has its cut moved inside the pause.
    moved = cuts([(0.2, 2.0), (7.0, 9.0)], 9.5)
    assert moved is not None and moved[1] - moved[0] <= m.talking.MAX_PART_S + 1e-9
    assert 9.5 - moved[1] <= m.talking.MAX_PART_S + 1e-9 and 2.0 <= moved[1] <= 7.0
    assert cuts([(0.3, 5.2), (5.4, 6.0)], 6.2) is None  # the first line alone runs past one chunk
    assert cuts([(0.3, 1.0), (1.2, 7.0)], 7.2) is None  # so does the second
    assert cuts([(0.2, 1.0), (11.0, 12.0)], 12.0) is None  # two parts cannot cover 12 s


# ================================================================ 2. the render

def _tier2_shot(duration_s, frames):
    return {"shot_id": "sh01", "scene_id": "s01", "start_s": 0.0, "duration_s": duration_s, "frames": frames,
            "motion": None, "modifiers": [], "transition_after": None}


def test_the_parts_are_cut_back_to_back_to_the_shots_exact_frames_never_slowed(tmp_path):
    m = _mods()
    assert m.filtergraph.part_frames([3.325, 2.175], 165) == [100, 65]
    assert m.filtergraph.part_frames([1.0, 1.0, 1.0], 91) == [30, 30, 31]
    with pytest.raises(ValueError):
        m.filtergraph.part_frames([3.0, 3.0], 80)  # the first part alone takes 90
    argv = m.filtergraph.tier2_parts_argv(["in/a.mp4", "in/b.mp4"], [100, 65], _tier2_shot(5.5, 165),
                                         m.profiles.SHOT, "out.mp4")
    graph = argv[argv.index("-filter_complex") + 1]
    assert argv.count("-i") == 2 and "concat=n=2:v=1:a=0[out]" in graph and "setpts=PTS-STARTPTS" in graph
    assert "trim=end_frame=100" in graph and "trim=end_frame=65" in graph and "*PTS" not in graph
    assert argv[argv.index("-frames:v") + 1] == "165" and "-an" in argv
    with pytest.raises(ValueError):
        m.filtergraph.tier2_parts_argv(["in/a.mp4"], [100], _tier2_shot(5.5, 165), m.profiles.SHOT, "out.mp4")

    # Real ffmpeg: two 1.0 s fixture clips (16 fps, a running counter) cut to 20 + 25 frames of a 1.5 s shot.
    if any(shutil.which(tool) is None for tool in ("ffmpeg", "ffprobe")):
        pytest.fail("ffmpeg not found on PATH: this check never skips (DEC-156); install ffmpeg")
    m.golden_tier2.make_clip(tmp_path / "a.mp4")
    m.golden_tier2.make_clip(tmp_path / "b.mp4", variant=True)
    argv = m.filtergraph.tier2_parts_argv(["a.mp4", "b.mp4"], [20, 25], {"duration_s": 1.5, "frames": 45},
                                         m.profiles.GOLDEN, "shot.mp4")
    subprocess.run(argv, cwd=tmp_path, capture_output=True, stdin=subprocess.DEVNULL, check=True)
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", os.fspath(tmp_path / "shot.mp4")],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True)
    assert int(out.stdout.strip()) == 45


# ================================================================ 3. the one click, end to end

def test_the_one_click_cuts_a_long_exchange_into_talking_parts_with_close_ups_and_renders_them(store, tmp_path,
                                                                                               media):
    m = _mods()
    story_id = ttc._own_gpu(store, tmp_path)
    seams, summary, log = ttc._one_click(store, tmp_path, media, story_id)

    assert summary["steps"]["render"]["state"] == "completed", summary
    ec, script, board = tas._ec(store, story_id), eps._script(store, story_id), tas._board(store, story_id)
    assert m.schemas.storyboard_errors(board) == []
    found = m.talking.verdicts(ec, script, board)
    cut = sorted(shot_id for shot_id, item in found.items() if m.talking.is_split(item))
    assert "sh12" in cut  # Kiwilo's two lines past one chunk
    images = {request.extra["name"]: request for request in seams.image.requests}
    videos = {request.extra["name"]: request for request in seams.video.requests}
    manifest = ttc.tft._doc(store, story_id, "render_manifest.json")
    stages = {stage["id"]: stage for stage in manifest["stages"]}
    staged = {item["id"]: item for item in manifest["inputs"] if item["role"] == "shot"}
    for shot in board["shots"]:
        if shot["shot_id"] not in cut:
            continue
        shot_id, clip = shot["shot_id"], shot["assets"]["clip"]
        parts = found[shot_id]["parts"]
        assert clip["link"] == S2V and clip["state"] == "current" and clip["clip_s"] == 5 * len(parts)
        assert [part["line_id"] for part in clip["parts"]] == shot["lines"]
        assert clip["talk"]["track_hash"] == found[shot_id]["spec"]["hash"]
        assert shot["assets"]["video"] == clip["parts"][0]["video"]
        keyframe = m.assets.shot_image_path(ec, shot)
        for part, planned in zip(clip["parts"], parts):
            stem = f"shot_{shot_id[2:]}.{part['line_id']}"
            assert (part["speaker"], part["start_s"], part["duration_s"]) == (
                planned["speaker"], planned["start_s"], planned["duration_s"])
            # The close-up: the speaker's sheet, then the shot's keyframe, the frame asked in words.
            closeup = images[stem]
            assert closeup.references == (m.talking.closeup_reference(ec, part["speaker"]), keyframe)
            assert "Head and shoulders, face centred, looking at the camera, mouth slightly open" in closeup.prompt
            path = m.clips.closeup_path(ec, part["closeup"])
            assert path and part["closeup"].startswith(f"assets/shots/{stem}.")
            assert part["closeup_sha256"] == m.assets._sha256_file(path)
            # The part's clip: that close-up as its keyframe, its own line's track, one 5 s chunk.
            request = videos[stem]
            assert request.references == (path,) and request.duration_s == 5
            assert seams.video.tracks[stem] == part["audio_sha256"]
            assert part["video"] == f"assets/clips/{stem}.mp4"
            assert part["video_sha256"] == m.assets._sha256_file(m.clips.part_clip_path(ec, shot_id,
                                                                                       part["line_id"]))
            assert f"{shot_id}_{part['line_id']}" in staged
        argv = " ".join(stages[f"S:{shot_id}"]["argv"])
        assert f"concat=n={len(parts)}:v=1:a=0" in argv and "*PTS" not in argv
        assert m.clips.clip_state(ec, shot, script, link=ttc.tce.SEEDANCE, tier=2,
                                  flags=m.clips.shot_flags(shot, None),
                                  image_sha=m.assets._sha256_file(keyframe)) == "current"
    assert any(line.startswith("🗣️ sh12 cut into 2 talking clips, one per line") for line in log)

    # Continue repeats nothing: every part and every close-up is current.
    again = ttc.TalkingVideo(media.silent)
    ttc._one_click(store, tmp_path, media, story_id, video=again)
    assert again.requests == []


def test_regenerating_one_close_up_buys_that_part_again_and_keeps_the_other(store, tmp_path, media, monkeypatch):
    from clipping.aistory import workflow
    from clipping.aistory.steps import entities, regenerate

    m = _mods()
    assert regenerate.parse_target("shot:1:sh12:closeup:l21") == (regenerate.SHOT_CLOSEUP_KIND, 1, "sh12", "l21")
    assert regenerate.parse_target("shot:1:sh12:closeup:x21") is None
    assert "shot:<ep>:<shot_id>:closeup:<line_id>" in regenerate.EPISODE_TARGETS
    story_id = ttc._own_gpu(store, tmp_path)
    ttc._one_click(store, tmp_path, media, story_id)
    before = next(shot for shot in tas._board(store, story_id)["shots"] if shot["shot_id"] == "sh12")
    kept = {part["line_id"]: part for part in before["assets"]["clip"]["parts"]}
    settings = dict(ttc.OWN, VIDEO_CHAIN=ttc.tce.SEEDANCE)
    video = ttc.TalkingVideo(media.silent)
    seams = ttc.tls._fakes(tmp_path, media, video=video, llm=False)
    adapters = seams.fakes.adapters
    adapters[("video", "runpod")] = video
    adapters[("image_edit", "runpod")] = seams.image
    adapters[("image", "runpod")] = seams.image
    estimate = workflow.regenerate_clip_estimate(store, store.get(story_id), regenerate.parse_target(
        "shot:1:sh12:closeup:l21"), env=settings, adapters=adapters)
    assert estimate["ready"] is True and estimate["link"] == S2V

    monkeypatch.setattr(entities, "fresh_seed", lambda: 4242)
    ctx, _log = eps._ctx(store, story_id, step="regenerate", settings=settings,
                         params={"target": "shot:1:sh12:closeup:l21", "note": "a wider smile"})
    result = regenerate.run(ctx, adapters=adapters, time_fn=eps.Clock(0.0), sleep_fn=lambda _s: None)

    assert result["line"] == "l21" and result["closeup_seed"] == 4242 and result["link"] == S2V
    # One close-up drawn again (the new seed, the note), one part's clip bought again; l20's stays as it was.
    assert [request.extra["name"] for request in seams.image.requests] == ["shot_12.l21"]
    assert seams.image.requests[0].seed == 4242 and seams.image.requests[0].prompt.endswith("a wider smile")
    assert video.names() == ["shot_12.l21"]
    after = next(shot for shot in tas._board(store, story_id)["shots"] if shot["shot_id"] == "sh12")
    parts = {part["line_id"]: part for part in after["assets"]["clip"]["parts"]}
    assert parts["l20"] == kept["l20"]
    assert parts["l21"]["closeup_seed"] == 4242 and parts["l21"]["closeup_note"] == "a wider smile"
    assert parts["l21"]["closeup_sha256"] != kept["l21"]["closeup_sha256"]
    assert after["assets"]["clip"]["state"] == "current" and after["assets"]["clip"].get("pending") is None
    assert m.schemas.storyboard_errors(tas._board(store, story_id)) == []
