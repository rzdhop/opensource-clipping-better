"""A shot longer than the longest clip its link sells (AI Story, DEC-250 as
amended by plan 35 / DEC-318; the human's report of 2026-10-03: "shots sh04
(12.767 s) and sh03 (14.133 s) run longer than the 12 s clip
fal/seedance-1-pro-fast sells, and every shot of this story is one clip: plan
the storyboard again" -- the storyboard had planned one shot a scene on the
estimate, and the Gemini voices then spoke 1.16-1.80x longer than it).

Two layers. At plan time the storyboard step decides one or two beat shots
on the scene's length as its voices will measure it
(``storyboard.expected_scene_seconds``: the estimate plus each unmeasured
line's provider overrun, ``voices.SPEECH_OVERRUN``), not on the estimate
alone. At run time a fully animated shot longer than the longest clip by
more than DEC-208's hold is refused, naming it and its regenerate target:
DEC-250's slowed clip (``cover: stretch``, up to 1.25x) is gone since plan 35
-- the human watched a two-line shot's 5-s clip stretched under 7 s of
speech, the mouths out of step -- so ``clips.MAX_STRETCH`` is 1.0 and no new
clip is slowed (on the own_gpu profile a speaking shot is cut into one
talking clip per line instead: test_story_talking_parts.py). A clip recorded
``cover: stretch`` before still renders as it did
(``filtergraph.tier2_clip_argv(clip_s=)``). DEC-208's held last frame stays
for every other story.

Offline and hermetic but for the one real-ffmpeg check (ffmpeg is on PATH
here and in CI); stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import copy
import os
import re
import subprocess

import pytest

import test_story_ambience as amb
import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_episode_steps as eps
import test_story_fast_track as tft
import test_story_fast_track_one_click as oc
import test_story_keyframe_gate as kg
import test_story_storyboard_props as tsp
import test_story_video_phase as tvp
from clipping.aistory import schemas, shots, timing, video_plan, voices
from clipping.aistory.render import filtergraph, golden_tier2, profiles
from clipping.aistory.steps import clips, storyboard
from test_story_assets_step import hermetic, store  # noqa: F401 -- the step's fixtures (hermetic is autouse)
from test_story_clip_estimate import timings_path  # noqa: F401 -- the measured clip timings under tmp_path
from test_story_keyframe_gate import built, unpaced  # noqa: F401 -- the free Gemini tier's pacing lifted

NOW = tas.NOW
SEEDANCE, VEO, GEMINI = amb.SEEDANCE, amb.VEO, amb.GEMINI


def _without_lipsync(store, story_id):
    """DEC-258 re-pin: the story turns the quality preset's lipsync off, so
    DEC-250's 12 s seedance clip is what is pinned here (a lipsyncing story
    buys 10 s at most: test_story_lipsync.py)."""
    store.update(story_id, lambda doc: doc["generation_profile"].update(lipsync="none"), now=NOW)
    return story_id


# ======================================================== 1. the estimate

def test_the_live_shots_are_refused_never_slowed_naming_each_and_its_regenerate_target(store, tmp_path):
    """The human's episode: 14.133 s and 12.767 s shots on seedance (12 s).
    Plan 35 (DEC-250 amended): no clip is slowed -- the plan is not ready,
    the long shots' rows hold (``held_s``) with no ``cover``, and the
    refusal names each with its length, its ``shot:<ep>:<shot_id>:plan``
    target and the fix; the assets step refuses before any call."""
    from clipping.aistory.steps import assets

    story_id = _without_lipsync(store, amb._story(store, tmp_path))
    board = copy.deepcopy(tas._board(store, story_id))
    long_a, long_b, short = board["shots"][1], board["shots"][2], board["shots"][0]
    long_a["duration_s"], long_b["duration_s"] = 14.133, 12.767

    units = amb._too_long_units(store, story_id, board, **tas.FAL)
    video = units["video"]
    assert video["link"] == SEEDANCE and video["ready"] is False
    rows = {row["shot_id"]: row for row in video["plan"]}
    for shot, held in ((long_a, 2.133), (long_b, 0.767)):
        row = rows[shot["shot_id"]]
        assert row["clip_s"] == 12 and row["held_s"] == pytest.approx(held)
        assert "cover" not in row and "stretch" not in row
    assert "cover" not in rows[short["shot_id"]] and "held_s" not in rows[short["shot_id"]]
    sentence = video["too_long"]
    assert sentence.startswith(f"shots {long_a['shot_id']} (14.133 s) and {long_b['shot_id']} (12.767 s) run longer "
                               f"than the 12 s clip {SEEDANCE} sells, a clip is never slowed")
    assert (f"(regenerate shot:1:{long_a['shot_id']}:plan and shot:1:{long_b['shot_id']}:plan)" in sentence)
    assert sentence.endswith("or shorten the scene's lines") and short["shot_id"] not in sentence
    assert "slowed to cover it" not in video["message"]
    refusal = assets.plan_refusal(tas._ec(store, story_id), units)
    assert refusal and sentence in refusal and "Nothing was generated or spent" in refusal


def test_a_shot_within_the_hold_is_held_and_only_the_one_past_it_is_named(store, tmp_path):
    """DEC-208's half second still holds a clip's last frame (12.4 s on a
    12 s clip); past it (16 s, and 12.9 s which DEC-250 once slowed) a shot
    is named -- the most a clip covers is its own length."""
    story_id = _without_lipsync(store, amb._story(store, tmp_path))
    board = copy.deepcopy(tas._board(store, story_id))
    too_long, once_slowed, held = board["shots"][1], board["shots"][2], board["shots"][0]
    too_long["duration_s"], once_slowed["duration_s"], held["duration_s"] = 16.0, 12.9, 12.4

    video = amb._too_long_units(store, story_id, board, **tas.FAL)["video"]
    assert video["ready"] is False
    sentence = video["too_long"]
    assert f"{too_long['shot_id']} (16 s)" in sentence and f"{once_slowed['shot_id']} (12.9 s)" in sentence
    assert held["shot_id"] not in sentence and "even slowed" not in sentence
    assert "plan the storyboard again" in sentence and sentence.endswith("or shorten the scene's lines")
    rows = {row["shot_id"]: row for row in video["plan"]}
    assert rows[held["shot_id"]]["held_s"] == pytest.approx(0.4) and not any(row.get("cover")
                                                                              for row in video["plan"])
    assert "held on its last frame for 0.4 s" in video["message"]


def test_guard_a_story_that_does_not_animate_every_shot_holds_and_never_slows(store, tmp_path):
    """DEC-208 unchanged where a still may stand in: a key-shots story's long
    shot is held on its last frame, no row says stretch."""
    story_id = amb._story(store, tmp_path, budget_profile="one_dollar")
    board = copy.deepcopy(tas._board(store, story_id))
    board["shots"][1]["duration_s"] = 14.0
    video = amb._too_long_units(store, story_id, board, **tas.FAL)["video"]
    held = [row for row in video["plan"] if row.get("held_s")]
    assert held and not any(row.get("cover") for row in video["plan"])
    assert "held on its last frame for 2 s" in video["message"]


def test_no_clip_is_slowed_any_more_max_stretch_is_one():
    """Plan 35 (DEC-250 amended): ``stretch_of`` never slows a clip -- None
    within the hold and past it alike."""
    assert clips.MAX_STRETCH == 1.0
    for duration, clip_s in ((12.0, 12), (12.5, 12), (12.6, 12), (15.0, 12), (15.1, 12), (9.4, 8), (14.133, 0)):
        assert clips.stretch_of(duration, clip_s) is None, (duration, clip_s)


# ======================================================= 2. the plan time

def _gemini_voices(store, story_id):
    for cid in list(tas._ec(store, story_id).entities["characters"]):
        doc = store.read_entity(story_id, "characters", cid)
        doc["voice"] = dict(doc["voice"], provider="gemini", voice_id="Charon")
        store.write_entity(story_id, "characters", doc, now=NOW)


def _measured(script):
    for scene in script["scenes"]:
        for line in scene["lines"]:
            line["timing"] = {"source": "audio_duration_only", "duration_s": line["timing"]["duration_s"],
                              "text_hash": timing.text_hash(line["text"]), "voice": "gemini/Charon",
                              "audio": f"assets/voice/{line['line_id']}.wav"}


def test_a_scene_is_measured_longer_than_its_estimate_when_its_voices_are_gemini_s(store):
    """``storyboard.expected_scene_seconds``: Edge voices speak the estimate
    (the French rate was measured on them); Gemini's add 35 % of each
    unmeasured line's estimate; a measured line adds nothing. The beat-shot
    count reads it: a scene the estimate puts under the clip's length but
    the voices past it is two shots."""
    story_id = amb._v2_storyboard_story(store)
    ec = storyboard.episode_common.load_context(store, story_id, 1)
    script = eps._script(store, story_id)
    assert voices.speech_overrun({"provider": "gemini"}) == 1.35 and voices.speech_overrun(None) == 1.0
    for scene in script["scenes"]:
        assert storyboard.expected_scene_seconds(ec, script, scene) == storyboard.scene_seconds(ec, script, scene)

    _gemini_voices(store, story_id)
    ec = storyboard.episode_common.load_context(store, story_id, 1)
    flipped = 0
    for scene in script["scenes"]:
        plain, expected = storyboard.scene_seconds(ec, script, scene), storyboard.expected_scene_seconds(ec, script,
                                                                                                        scene)
        speech = sum(timing.estimate_line(line["text"], "fr") for line in scene["lines"])
        assert expected == pytest.approx(plain + 0.35 * speech, abs=0.002)
        limit = (plain + expected) / 2  # a clip exactly this long: the estimate fits, the voices do not
        assert storyboard.beat_shot_count(ec, script, scene, limit_s=limit) == (2, 2)
        assert storyboard.beat_shot_count(ec, script, scene, limit_s=expected + 0.01,
                                         rhythm=False) == (1, 1)  # DEC-252 re-pin: the clip-length rule alone
        flipped += 1
    assert flipped == len(script["scenes"])

    _measured(script)
    for scene in script["scenes"]:
        assert storyboard.expected_scene_seconds(ec, script, scene) == storyboard.scene_seconds(ec, script, scene)


def test_a_scene_with_a_line_plan_is_expected_to_last_the_plans_seconds(store):
    """Plan 24 stage 4, fail-first: with a stored line plan, every unmeasured
    line speaks for its plan entry's seconds (the pauses and tail around the
    lines are the scene's), whatever the estimate or the voices' overrun
    say; a measured line stays what was measured; a scene without a plan is
    as it was. The beat count reads it."""
    story_id = amb._v2_storyboard_story(store)
    _gemini_voices(store, story_id)
    ec = storyboard.episode_common.load_context(store, story_id, 1)
    script = eps._script(store, story_id)
    scene = script["scenes"][0]
    before = storyboard.expected_scene_seconds(ec, script, scene)
    estimates = [timing.estimate_line(line["text"], "fr") for line in scene["lines"]]
    around = storyboard.scene_seconds(ec, script, scene) - sum(estimates)

    scene["line_plan"] = {"allowed_speech_s": 9.0, "max_words": 30, "min_words": 15, "lines": [
        {"kind": "narrator" if line["speaker"] == "narrator" else "character", "speaker": line["speaker"],
         "seconds": 3.0 + k, "max_words": 10} for k, line in enumerate(scene["lines"])]}
    planned_sum = sum(3.0 + k for k in range(len(scene["lines"])))
    assert storyboard.expected_scene_seconds(ec, script, scene) == pytest.approx(around + planned_sum, abs=0.002)
    assert storyboard.beat_shot_count(ec, script, scene, limit_s=around + planned_sum - 0.01) == (2, 2)

    other = script["scenes"][1]
    assert storyboard.expected_scene_seconds(ec, script, other) == storyboard.expected_scene_seconds(
        ec, {**script, "scenes": [other]}, other)
    del scene["line_plan"]
    assert storyboard.expected_scene_seconds(ec, script, scene) == before


def test_the_storyboard_step_plans_two_beat_shots_where_the_voices_will_run_past_the_clip(store):
    """On seedance (12 s) with Gemini voices, T1 v2 is asked two beat shots
    for every scene whose voices will run past 12 s though its estimate
    does not, one for the rest; the storyboard follows."""
    m = eps._new()
    story_id = amb._v2_storyboard_story(store)
    _gemini_voices(store, story_id)
    ec = storyboard.episode_common.load_context(store, story_id, 1)
    script = eps._script(store, story_id)
    assert storyboard.max_shot_s(ec, amb._plan_settings(**tas.FAL)) == 12
    expected = {scene["scene_id"]: storyboard.expected_scene_seconds(ec, script, scene) for scene in script["scenes"]}
    plain = {scene["scene_id"]: storyboard.scene_seconds(ec, script, scene) for scene in script["scenes"]}
    moved = sorted(sid for sid in expected if plain[sid] <= 12 < expected[sid])
    assert moved, (plain, expected)  # the fixture reaches the new rule: the estimate fits, the voices do not

    llm = eps.FakeLLM(default={"T1v2": tsp.t1_v2_reply})
    eps._run(m.storyboard, store, story_id, llm=llm, step="storyboard", settings=amb._plan_settings(**tas.FAL))

    per_scene = {}
    for shot in eps._storyboard(store, story_id)["shots"]:
        per_scene[shot["scene_id"]] = per_scene.get(shot["scene_id"], 0) + 1
    # Re-pinned on purpose (plan 28 stage A2, DEC-305): the step re-times the script on the re-slotted template
    # before it plans (a body scene held up to its 10 s floor), so the rhythm is read on the scenes as it timed them.
    timed = eps._script(store, story_id)
    expected = {scene["scene_id"]: storyboard.expected_scene_seconds(ec, timed, scene) for scene in timed["scenes"]}
    for call, scene in zip(llm.calls, script["scenes"]):
        asked = tsp.two_beats(scene, expected[scene["scene_id"]])  # DEC-252 re-pin: + the rhythm
        assert per_scene[scene["scene_id"]] == asked, (scene["scene_id"], plain, expected)
        assert ("exactly 2 entries" if asked == 2 else "exactly 1 entry") in call["user"]
    assert all(per_scene[sid] == 2 for sid in moved)


# ========================================================== 3. the render

def _tier2_shot(duration_s, frames):
    return {"shot_id": "sh01", "scene_id": "s01", "start_s": 0.0, "duration_s": duration_s, "frames": frames,
            "motion": None, "modifiers": [], "transition_after": None}


def test_tier2_clip_argv_slows_a_recorded_stretch_and_is_byte_identical_without_one():
    """``filtergraph.tier2_clip_argv(clip_s=)``: the shot's length over the
    clip's as ``setpts`` before the fps resample, the hold and the trim as
    they were; with no clip length, or one at least the shot's, the argv it
    always was (the tier-2 goldens hold)."""
    stretched = filtergraph.tier2_clip_argv("in/clip.mp4", _tier2_shot(14.133, 424), profiles.SHOT, "out.mp4",
                                            clip_s=12)
    assert stretched[stretched.index("-vf") + 1] == (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,"
        f"setpts={round(14.133 / 12, 4)}*PTS,fps=30,tpad=stop_mode=clone:stop_duration=14.133,"
        "trim=duration=14.133,format=yuv420p")
    assert stretched[stretched.index("-frames:v") + 1] == "424" and "-an" in stretched
    plain = filtergraph.tier2_clip_argv("in/clip.mp4", _tier2_shot(2.0, 60), profiles.SHOT, "out.mp4")
    assert filtergraph.tier2_clip_argv("in/clip.mp4", _tier2_shot(2.0, 60), profiles.SHOT, "out.mp4", clip_s=2) == plain
    assert filtergraph.tier2_clip_argv("in/clip.mp4", _tier2_shot(2.0, 60), profiles.SHOT, "out.mp4", clip_s=None) == plain
    assert "setpts" not in " ".join(plain)


def _frame_changes(path) -> list:
    """How much each frame of *path* differs from the one before it: the
    average luma of their difference (``tblend`` + ``signalstats``), one
    number a frame from the second on. Lossy encoding keeps a cloned frame's
    hash from repeating, so the change is measured, not the bytes."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", os.fspath(path), "-vf",
                          "tblend=all_mode=difference,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                          "-f", "null", "-"], capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True)
    return [float(m.group(1)) for m in re.finditer(r"lavfi\.signalstats\.YAVG=([0-9.]+)", out.stderr)]


def _frame_count(path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", os.fspath(path)], capture_output=True,
                         text=True, stdin=subprocess.DEVNULL, check=True)
    return int(out.stdout.strip())


def test_a_slowed_clip_keeps_moving_to_the_shots_last_frame_where_a_held_one_freezes(tmp_path):
    """Real ffmpeg: the tier-2 golden's 1.0 s clip (``testsrc``, a running
    counter) for a 1.5 s shot of 45 frames. Held (DEC-208) the last 15
    frames are one frozen picture (no change between them); slowed (DEC-250)
    the picture keeps changing to the last frame, and the shot still has
    exactly 45 frames."""
    if any(__import__("shutil").which(tool) is None for tool in ("ffmpeg", "ffprobe")):
        pytest.fail("ffmpeg not found on PATH: this check never skips (DEC-156); install ffmpeg")
    golden_tier2.make_clip(tmp_path / "clip.mp4")
    shot = {"duration_s": 1.5, "frames": 45}
    for name, clip_s in (("held.mp4", None), ("slowed.mp4", 1)):
        argv = filtergraph.tier2_clip_argv("clip.mp4", shot, profiles.GOLDEN, name, clip_s=clip_s)
        subprocess.run(argv, cwd=tmp_path, capture_output=True, stdin=subprocess.DEVNULL, check=True)
    assert _frame_count(tmp_path / "held.mp4") == _frame_count(tmp_path / "slowed.mp4") == 45
    held, slowed = _frame_changes(tmp_path / "held.mp4"), _frame_changes(tmp_path / "slowed.mp4")
    assert len(held) == len(slowed) == 44
    assert max(held[-14:]) < 0.5, held[-14:]  # the frozen last frame, as before
    assert sum(1 for change in slowed[-14:] if change > 0.5) >= 5, slowed[-14:]  # the motion runs to the end
    assert slowed[-1] > 0.5 or slowed[-2] > 0.5


def test_a_clip_record_may_say_how_the_render_covers_the_shot():
    """``storyboard`` schema: ``assets.clip.cover`` is optional -- absent
    (every stored story) or ``stretch`` (a clip recorded before plan 35);
    anything else is refused."""
    base = {"state": "current", "link": SEEDANCE, "route": "paid", "clip_s": 12, "est_usd": 0.48,
            "prompt_hash": "a" * 64, "image_sha256": "b" * 64, "cache_key": None, "generated_at": None}
    assert schemas.validate(base, schemas._STORYBOARD_CLIP_SCHEMA) == []
    assert schemas.validate(dict(base, cover="stretch"), schemas._STORYBOARD_CLIP_SCHEMA) == []
    assert schemas.validate(dict(base, cover=None), schemas._STORYBOARD_CLIP_SCHEMA) == []
    assert schemas.validate(dict(base, cover="hold"), schemas._STORYBOARD_CLIP_SCHEMA) != []
    assert schemas.validate(dict(base, cover="faster"), schemas._STORYBOARD_CLIP_SCHEMA) != []


# ============================================ 4. the one click, end to end

def test_the_one_click_never_buys_a_clip_for_a_shot_longer_than_its_link_sells(store, tmp_path, monkeypatch):
    """The human's scenario on the quality episode: every shot is one clip,
    the voices are measured, and the link's longest clip is shorter than the
    longest shots (the sold lengths narrowed under this fixture's re-timed
    shots). Plan 35: the fast track stops at the assets plan -- no clip is
    bought, none slowed -- naming the long shots and their regenerate
    targets."""
    story_id = oc._v2_unmade(store, tmp_path)
    # The voices first (stop at the keyframes): the shots are re-timed from them, as live.
    fakes, _image = oc._quality_fakes(tmp_path, video=tvp.FakeVideo(), vision=kg.FakeVision())
    message = tft.stopped(store, story_id, fakes, settings=oc.QUALITY_SETTINGS, params={"stop_at_keyframes": True})
    assert "keyframes are made" in message and fakes.adapters[("video", "fal")].requests == []
    durations = {shot["shot_id"]: float(shot["duration_s"]) for shot in tas._board(store, story_id)["shots"]}
    longest_shot = max(durations.values())
    sold = next(n for n in range(12, 1, -1) if longest_shot - n > clips.HOLD_TOLERANCE_S)
    monkeypatch.setitem(video_plan.CLIP_LENGTHS, SEEDANCE, tuple(range(2, sold + 1)))
    long_ids = sorted(sid for sid, value in durations.items() if value - sold > clips.HOLD_TOLERANCE_S)
    assert long_ids and len(long_ids) < len(durations), durations
    kg._approve_keyframes(store, story_id)
    video, vision = tvp.FakeVideo(), kg.FakeVision()
    fakes, _image = oc._quality_fakes(tmp_path, video=video, vision=vision)

    message = tft.stopped(store, story_id, fakes, settings=oc.QUALITY_SETTINGS)

    assert video.requests == []
    assert "a clip is never slowed" in message and "is slowed to cover it" not in message
    for shot_id in long_ids:
        assert f"shot:1:{shot_id}:plan" in message, message
    assert all(not (shot["assets"].get("clip") or {}).get("cover") for shot in tas._board(store, story_id)["shots"])
