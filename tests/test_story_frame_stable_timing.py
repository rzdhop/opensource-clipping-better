"""Whole-frame shot timing (AI Story phase 5, stage 6; DEC-142 as amended).

Before this stage a scene lasted whatever its lines and pauses summed to, to
the millisecond, and the timeline cut every shot's ``frames`` by cumulative
rounding over all the shot durations before it (``render/timeline.py``
``_cumulative_frames``). A line edit that moved scene k by a non-whole
number of frames then flipped a frame on about a fifth of the LATER shots:
new frame counts, new render cache keys, clips rendered again for nothing.

Timed in whole frames, every scene and shot duration is a whole number of
frames stored as ``round(n / 30, 3)`` (so ``round(d * 30) == n``): a change
in scene k moves every later shot by whole frames only, and a later shot
keeps its frames and its cache key unless its own scene's duration moved
(the window pass may legitimately do that). A storyboard says it is timed
this way (``whole_frames: true``); one timed before keeps -- and renders in
-- the timing it was cut to, until its first full re-time converts it.

Sections, in order:

1. the frame rate
2. scene_timing in whole frames (the flagged twins of the phase-3 pins,
   ``test_story_timing.py``, which stay on the old timing)
3. allocate_shots in whole frames
4. episode timing in whole frames (the window pass)
5. the flag: new storyboards, the schema, the timeline, the re-times and
   the conversion of an old storyboard (and the script re-timed after it)
6. the property: a change in one scene leaves every later shot's frames and
   render cache key as they were

Stdlib + pytest only (DEC-012): this file runs in the CI environment.
"""

from __future__ import annotations

import copy
import hashlib
import random
import types

import test_story_shots as sh
import test_story_timing as tt
from clipping.aistory import schemas, shots, templates, timing
from clipping.aistory.render import plan as plan_mod
from clipping.aistory.render import profiles
from clipping.aistory.render import timeline as rt
from clipping.aistory.steps import episode_common, voice_lines

EN = "en"
TEMPLATE = sh.TEMPLATE
FLOOR = TEMPLATE["pauses_s"]["tail_floor"]
STYLES = {"fruit_drama": sh.FRUIT_DRAMA, "family_3d": sh.FAMILY_3D, "claymation": templates.load_style("claymation")}


def _frames(seconds) -> int:
    return round(seconds * profiles.FPS)


def _residue(seconds) -> float:
    """How far *seconds* is from a whole frame, in frames."""
    return abs(seconds * profiles.FPS - round(seconds * profiles.FPS))


def _canonical(seconds) -> bool:
    """Stored as ``round(n / 30, 3)`` for its own whole frame count."""
    return seconds == round(_frames(seconds) / profiles.FPS, 3)


# ================================================================ 1. frame rate

def test_the_timing_engines_frame_rate_is_the_renders():
    """``timing.py`` keeps its own constant so it stays pure (RC-P8)."""
    assert timing.FPS == profiles.FPS == 30


# ======================================================= 2. scene_timing

def test_an_over_scene_rounds_up_so_its_tail_stays_at_or_above_the_floor():
    """``test_scene_timing_over_hi_stays_over_once_the_tail_hits_the_floor``
    in whole frames: 9.65 s is 289.5 frames, the next frame up (290) keeps
    the tail at 0.317, never under its 0.3 floor."""
    lines = [tt._timed_line("l01", "A very long line that overruns the whole scene badly.", 9.0)]
    scene = tt._scene("s02", "setup", lines=lines)
    result = timing.scene_timing(scene, TEMPLATE, EN)
    old = timing.scene_timing(scene, TEMPLATE, EN, whole_frames=False)
    assert (old["duration_s"], old["tail_s"]) == (9.65, FLOOR)
    assert result["state"] == "over"
    assert (result["duration_s"], result["tail_s"], result["hold_s"]) == (9.667, 0.317, 0.0)
    assert _frames(result["duration_s"]) == 290 and _canonical(result["duration_s"])
    assert result["line_starts"] == old["line_starts"]


def test_a_raised_tail_floor_is_kept_in_whole_frames_too():
    """``test_scene_timing_tail_floor_argument_raises_the_effective_floor``
    in whole frames: the tail ends at 0.517, over its raised 0.5 floor."""
    lines = [tt._timed_line("l01", "A longer line that fills the scene.", 7.6)]
    scene = tt._scene("s02", "setup", lines=lines)
    result = timing.scene_timing(scene, TEMPLATE, EN, tail_floor=0.5)
    assert result["state"] == "over"
    assert (result["duration_s"], result["tail_s"]) == (8.467, 0.517)
    assert _frames(result["duration_s"]) == 254


def test_a_scene_takes_the_nearest_frame_ties_up_and_no_line_moves():
    lines = [tt._timed_line("l01", "x", 4.1)]  # 0.35 + 4.1 + 0.6 = 5.05 s = 151.5 frames
    tie = timing.scene_timing(tt._scene("s02", "setup", lines=lines), TEMPLATE, EN)
    assert (tie["duration_s"], tie["tail_s"], tie["state"]) == (5.067, 0.617, "ok")

    lines = [tt._timed_line("l01", "x", 4.09)]  # 5.04 s = 151.2 frames: down to 151
    scene = tt._scene("s02", "setup", lines=lines)
    down, old = timing.scene_timing(scene, TEMPLATE, EN), timing.scene_timing(scene, TEMPLATE, EN, whole_frames=False)
    assert (down["duration_s"], down["tail_s"]) == (5.033, 0.593)
    assert (old["duration_s"], old["tail_s"]) == (5.04, 0.6)
    assert down["line_starts"] == old["line_starts"] and down["speech_s"] == old["speech_s"]


def test_a_held_scene_keeps_its_hold_and_a_quiet_scene_its_slot():
    lines = [tt._timed_line("l01", "Short.", 1.0)]
    held = timing.scene_timing(tt._scene("s02", "setup", lines=lines), TEMPLATE, EN)
    assert (held["duration_s"], held["hold_s"], held["tail_s"]) == (4.0, 2.05, 0.6)

    quiet = tt._scene("s01", "hook", lines=[], target_duration=2.37)  # 71.1 frames
    assert timing.scene_timing(quiet, TEMPLATE, EN)["duration_s"] == 2.367
    assert timing.scene_timing(quiet, TEMPLATE, EN, whole_frames=False)["duration_s"] == 2.37
    for target, expected in ((0.5, 1.5), (99, 3.5)):  # clamped into the hook slot, whole frames already
        scene = tt._scene("s01", "hook", lines=[], target_duration=target)
        assert timing.scene_timing(scene, TEMPLATE, EN)["duration_s"] == expected


def test_every_scene_of_a_generated_set_is_a_whole_frame_and_its_lines_never_move():
    rng = random.Random(6)
    for _ in range(300):
        function = rng.choice(["hook", "setup", "peak", "cliffhanger"])
        lines = [tt._timed_line(f"l{n:02d}", f"line {n}", round(rng.uniform(0.4, 4.5), 3))
                 for n in range(1, rng.randint(1, 3) + 1)]
        scene = tt._scene("s02", function, lines=lines)
        tail_floor = rng.choice([None, 0.4, 0.5])
        whole = timing.scene_timing(scene, TEMPLATE, EN, tail_floor=tail_floor)
        old = timing.scene_timing(scene, TEMPLATE, EN, tail_floor=tail_floor, whole_frames=False)
        assert _canonical(whole["duration_s"]) and _residue(whole["duration_s"]) < 0.02
        assert abs(whole["duration_s"] - old["duration_s"]) <= 1 / 30 + 1e-9
        assert whole["line_starts"] == old["line_starts"] and whole["state"] == old["state"]
        assert whole["tail_s"] >= max(FLOOR, tail_floor or 0.0) - 1e-9 and whole["hold_s"] >= 0.0


# ======================================================= 3. allocate_shots

def _alloc_scene(line_durations, function="setup"):
    lines = [tt._timed_line(f"l{n:02d}", f"line {n}", d) for n, d in enumerate(line_durations, 1)]
    return tt._scene("s02", function, lines=lines)


def test_shot_boundaries_land_on_whole_frames_and_the_shots_sum_to_the_scene():
    rng = random.Random(7)
    for _ in range(300):
        scene, shot_list = tt._gen_scene_and_shots(rng)
        scene_t = timing.scene_timing(scene, TEMPLATE, EN)
        durations, extra = timing.allocate_shots(scene, scene_t, shot_list, TEMPLATE)
        assert extra == 0.0
        assert round(sum(durations), 3) == scene_t["duration_s"]
        assert all(d >= TEMPLATE["min_shot_s"] - 1e-9 for d in durations)
        assert all(_canonical(d) for d in durations[:-1])
        assert all(_residue(d) < 0.1 for d in durations)
        # the running sum: every boundary on the frame the shots before it add up to
        assert sum(_frames(d) for d in durations) == _frames(scene_t["duration_s"])


def test_an_anchored_shot_starts_on_the_nearest_frame_of_its_first_line():
    scene = _alloc_scene([1.234, 1.567])
    scene_t = timing.scene_timing(scene, TEMPLATE, EN)
    shot_list = [{"lines": []}, {"lines": ["l01"]}, {"lines": ["l02"]}]
    durations, _extra = timing.allocate_shots(scene, scene_t, shot_list, TEMPLATE)
    old, _extra = timing.allocate_shots(scene, scene_t, shot_list, TEMPLATE, whole_frames=False)
    start_l02 = scene_t["line_starts"]["l02"]
    assert round(sum(old[:2]), 3) == start_l02
    assert _frames(sum(durations[:2])) == round(start_l02 * 30)


def test_a_shot_rounding_would_put_under_the_minimum_falls_back_to_the_even_split():
    """A template whose ``min_shot_s`` is not a whole frame (0.81 s = 24.3
    frames): the first shot's own span (0.35 + 0.215 + 0.25 = 0.815 s, 24.45
    frames) lands on 24 frames, 0.8 s, under the minimum -- so the scene is
    split evenly instead, still in whole frames."""
    template = dict(TEMPLATE, min_shot_s=0.81)
    scene = _alloc_scene([0.215, 1.0])
    scene_t = timing.scene_timing(scene, template, EN)
    assert scene_t["duration_s"] == 4.0
    shot_list = [{"lines": ["l01"]}, {"lines": ["l02"]}]
    assert timing.allocate_shots(scene, scene_t, shot_list, template, whole_frames=False) == ([0.815, 3.185], 0.0)
    assert timing.allocate_shots(scene, scene_t, shot_list, template) == ([2.0, 2.0], 0.0)


# ======================================================= 4. episode timing

def test_the_tightened_episode_in_whole_frames():
    """``test_episode_timing_tightened_case`` in whole frames: a tail that
    reaches its floor keeps the part of a frame its line ends in (here half a
    frame, twelve times), so the cliffhanger's tail is 0.317 and the episode
    78.004 s (2340 frames) instead of 77.8 s -- the edge of the window, up
    to about a frame per scene (DEC-142 as amended)."""
    scenes = tt._ok_scenes(n_body=10)
    script = tt._episode_script(scenes, cut_to_black=False)
    result = timing.episode_timing(script, TEMPLATE, EN)
    old = timing.episode_timing(script, TEMPLATE, EN, whole_frames=False)

    assert (result["state"], result["flags"]) == ("tightened", [])
    assert (old["total_s"], result["total_s"]) == (77.8, 78.004)
    assert _frames(result["total_s"]) == 2340
    cliff = result["scenes"][scenes[-1]["scene_id"]]
    assert (cliff["tail_s"], cliff["duration_s"]) == (0.317, 4.067)
    for sid, scene_t in result["scenes"].items():
        assert _canonical(scene_t["duration_s"]), sid
        floor = 0.4 if sid != scenes[-1]["scene_id"] else FLOOR  # a dissolve leaves every other scene
        assert scene_t["tail_s"] >= floor - 1e-9, sid


def test_the_hold_extension_of_an_under_episode_is_whole_frames_and_capped():
    """An episode far under the window: the cliffhanger and the first scene
    at each place are held longer by exactly the cap (``hold_extension_max_s``,
    30 frames), less what a scene's shot floor already held (s01's three
    shots need 72 frames, 13 more than its own 59) -- counted in frames."""
    def scene(sid, function, place_id, seconds):
        return tt._scene(sid, function, place_id=place_id,
                         lines=[tt._timed_line(f"l{sid[1:]}", "A line.", seconds)])

    scenes = [scene("s01", "hook", "place_a", 1.0), scene("s02", "setup", "place_a", 3.1),
              scene("s03", "setup", "place_a", 3.1), scene("s04", "setup", "place_b", 3.1),
              scene("s05", "setup", "place_b", 3.1), scene("s06", "cliffhanger", "place_b", 1.0)]
    script = tt._episode_script(scenes, cut_to_black=False)
    board = {"shots": [tt._shot(f"sh{i:02d}", "s01", []) for i in (1, 2, 3)]
             + [tt._shot(f"sh{i:02d}", s["scene_id"], []) for i, s in enumerate(scenes[1:], 4)],
             "transitions": []}  # every boundary a cut: every tail floor is the template's 0.3

    result, _scene_ts = timing.episode_pass(script, TEMPLATE, EN, storyboard=board)

    assert result["state"] == "under"
    natural = {s["scene_id"]: _frames(timing.scene_timing(s, TEMPLATE, EN)["duration_s"]) for s in scenes}
    got = {sid: _frames(scene_t["duration_s"]) for sid, scene_t in result["scenes"].items()}
    assert natural == {"s01": 59, "s02": 122, "s03": 122, "s04": 122, "s05": 122, "s06": 77}
    assert got == {"s01": 72 + 17, "s02": 122, "s03": 122, "s04": 122 + 30, "s05": 122, "s06": 77 + 30}
    for sid, scene_t in result["scenes"].items():
        assert _canonical(scene_t["duration_s"]), sid


def test_a_window_decision_counts_frames_not_the_3_decimal_sum():
    """Eleven 151-frame body scenes (each stored as 5.033 s, 0.3 ms short of
    its frames) and a cliffhanger held 3 frames to reach exactly 1650 frames
    (55.0 s): the stored durations sum to 54.997 s, yet the episode is inside
    the window -- storage noise never makes it "under", nor flags it."""
    scenes = [tt._scene("s01", "hook", lines=[tt._timed_line("l01", "Hook.", 0.9)])]
    for i in range(2, 13):
        scenes.append(tt._scene(f"s{i:02d}", "setup", lines=[tt._timed_line(f"l{i:02d}", "Body.", 4.083)]))
    scenes.append(tt._scene("s13", "cliffhanger", lines=[tt._timed_line("l13", "Cliff.", 0.9)]))
    result = timing.episode_timing(tt._episode_script(scenes), TEMPLATE, EN)
    assert result["total_s"] == 54.997 and _frames(result["total_s"]) == 1650
    assert (result["state"], result["flags"]) == ("ok", [])
    assert result["scenes"]["s13"]["duration_s"] == 2.567  # 74 frames, held 3 more


def test_an_episode_timed_either_way_puts_every_line_where_it_was_within_its_scene():
    scenes, script = tt._four_scene_two_place_script()
    whole, whole_t = timing.episode_pass(script, TEMPLATE, EN)
    old, old_t = timing.episode_pass(script, TEMPLATE, EN, whole_frames=False)
    for sid in whole_t:
        assert whole_t[sid]["line_starts"] == old_t[sid]["line_starts"]
        assert abs(whole_t[sid]["duration_s"] - old_t[sid]["duration_s"]) <= 1 / 30 + 1e-9
    assert whole["state"] == old["state"]


# ============================================================ 5. the flag

def _ec(style_lock, template=TEMPLATE, language=EN):
    """What ``episode_common.retime`` reads of an episode context."""
    return types.SimpleNamespace(template=template, language=language, style_lock=style_lock)


def _board(script, style_lock, *, language=EN):
    plans, sources = sh._fast_plans_for_script(script, style_lock)
    board, _notes = shots.build_storyboard(script, plans, sources, entities=sh.ENTITIES, style_lock=style_lock,
                                           template=TEMPLATE, language=language, consistency_mode="references",
                                           now=sh.NOW)
    return board


def _legacy(board, script, style_lock):
    """*board* as a storyboard timed before this stage was stored: no flag,
    its shots cut to the old timing (``_time_shots``'s own call then)."""
    old = copy.deepcopy(board)
    del old["whole_frames"]
    shots._time_shots(old["shots"], old["transitions"], script, template=TEMPLATE, language=EN,
                      style_lock=style_lock, whole_frames=False)
    return old


def _scene_sums(board) -> dict:
    sums: dict = {}
    for shot in board["shots"]:
        sums[shot["scene_id"]] = round(sums.get(shot["scene_id"], 0.0) + shot["duration_s"], 3)
    return sums


def _script_with_measured_lines(seed=11):
    """``test_story_shots.SCRIPT`` with every line measured to a millisecond
    (as real TTS is), so its scenes fall between frames."""
    rng = random.Random(seed)
    script = copy.deepcopy(sh.SCRIPT)
    for scene in script["scenes"]:
        for line in scene["lines"]:
            line["timing"] = {"source": "tts_word_timestamps", "duration_s": round(rng.uniform(1.2, 2.9), 3),
                              "text_hash": timing.text_hash(line["text"]), "voice": "edge/x",
                              "audio": f"assets/voice/{line['line_id']}.mp3"}
    return script


def test_a_new_storyboard_says_it_is_timed_in_whole_frames_and_validates():
    script = _script_with_measured_lines()
    board = _board(script, sh.FRUIT_DRAMA)
    assert board["whole_frames"] is True
    assert schemas.storyboard_errors(board, min_shot_s=TEMPLATE["min_shot_s"]) == []
    assert all(_residue(shot["duration_s"]) < 0.1 for shot in board["shots"])
    # the script beside it is timed the same way (episode_common.retime), to the millisecond
    episode_common.retime(script, _ec(sh.FRUIT_DRAMA), board)
    assert {sid: s["duration_s"] for sid, s in script["timing"]["scenes"].items()} == _scene_sums(board)
    assert all(_canonical(s["duration_s"]) for s in script["timing"]["scenes"].values())


def test_the_schema_takes_the_flag_as_an_optional_boolean():
    board = _board(_script_with_measured_lines(), sh.FRUIT_DRAMA)
    for value in (True, False):
        assert schemas.storyboard_errors(dict(board, whole_frames=value)) == []
    absent = {k: v for k, v in board.items() if k != "whole_frames"}
    assert schemas.storyboard_errors(absent) == []
    assert any("whole_frames" in error for error in schemas.storyboard_errors(dict(board, whole_frames="yes")))


def test_board_whole_frames_reads_the_flag_and_no_board_is_timed_in_whole_frames():
    assert timing.board_whole_frames(None) is True
    assert timing.board_whole_frames({"shots": [], "transitions": [], "whole_frames": True}) is True
    assert timing.board_whole_frames({"shots": [], "transitions": []}) is False
    assert timing.board_whole_frames({"shots": [], "transitions": [], "whole_frames": False}) is False


def test_the_script_is_retimed_in_whole_frames_only_with_no_board_or_a_flagged_one():
    script = _script_with_measured_lines()
    ec = _ec(sh.FRUIT_DRAMA)
    board = _board(script, sh.FRUIT_DRAMA)
    legacy = _legacy(board, script, sh.FRUIT_DRAMA)

    alone = episode_common.retime(copy.deepcopy(script), ec)["timing"]
    assert alone == timing.episode_timing(script, TEMPLATE, EN, style_lock=sh.FRUIT_DRAMA)
    assert all(_canonical(s["duration_s"]) for s in alone["scenes"].values())

    beside_old = episode_common.retime(copy.deepcopy(script), ec, legacy)["timing"]
    assert beside_old == timing.episode_timing(script, TEMPLATE, EN, style_lock=sh.FRUIT_DRAMA, storyboard=legacy,
                                               whole_frames=False)
    assert {sid: s["duration_s"] for sid, s in beside_old["scenes"].items()} == _scene_sums(legacy)
    assert beside_old != episode_common.retime(copy.deepcopy(script), ec, board)["timing"]


def test_an_old_storyboard_renders_in_the_timing_it_was_cut_to():
    """RC-M3 in miniature: the timeline of a storyboard with no flag is the
    old one, frame for frame, and its shot durations are left as stored."""
    script = _script_with_measured_lines()
    legacy = _legacy(_board(script, sh.FRUIT_DRAMA), script, sh.FRUIT_DRAMA)
    assert any(_residue(shot["duration_s"]) > 0.05 for shot in legacy["shots"])  # a real old board: between frames

    before = copy.deepcopy(legacy)
    tl = rt.build_timeline(script, legacy, TEMPLATE, EN, style_lock=sh.FRUIT_DRAMA)
    assert legacy == before
    old_total = timing.episode_timing(script, TEMPLATE, EN, style_lock=sh.FRUIT_DRAMA, storyboard=legacy,
                                      whole_frames=False)["total_s"]
    assert tl["total_s"] == old_total
    assert [s["frames"] for s in tl["shots"]] == rt._cumulative_frames(
        [shot["duration_s"] for shot in legacy["shots"]], profiles.FPS)
    assert [s["duration_s"] for s in tl["shots"]] == [shot["duration_s"] for shot in legacy["shots"]]


def test_an_old_storyboard_with_a_stale_scene_stays_in_its_old_timing():
    script = _script_with_measured_lines()
    legacy = _legacy(_board(script, sh.FRUIT_DRAMA), script, sh.FRUIT_DRAMA)
    legacy["scenes"]["s03"]["stale"] = True
    before = copy.deepcopy(legacy)
    timeline_before = rt.build_timeline(script, legacy, TEMPLATE, EN, style_lock=sh.FRUIT_DRAMA)

    changed = shots.retime_storyboard(legacy, script, template=TEMPLATE, language=EN, style_lock=sh.FRUIT_DRAMA)

    assert changed is False and legacy == before and "whole_frames" not in legacy
    assert rt.build_timeline(script, legacy, TEMPLATE, EN, style_lock=sh.FRUIT_DRAMA) == timeline_before


def test_the_first_full_retime_converts_an_old_storyboard_to_whole_frames():
    script = _script_with_measured_lines()
    board = _board(script, sh.FRUIT_DRAMA)
    legacy = _legacy(board, script, sh.FRUIT_DRAMA)
    kept = {k: v for k, v in legacy.items() if k not in ("shots", "whole_frames")}

    changed = shots.retime_storyboard(legacy, script, template=TEMPLATE, language=EN, style_lock=sh.FRUIT_DRAMA)

    assert changed is True and legacy["whole_frames"] is True
    assert [s["duration_s"] for s in legacy["shots"]] == [s["duration_s"] for s in board["shots"]]
    assert {k: v for k, v in legacy.items() if k not in ("shots", "whole_frames")} == kept
    for old, new in zip(board["shots"], legacy["shots"]):
        assert new == old
    # the script re-timed beside it agrees, and the timeline cuts every shot to its own frames
    episode_common.retime(script, _ec(sh.FRUIT_DRAMA), legacy)
    assert {sid: s["duration_s"] for sid, s in script["timing"]["scenes"].items()} == _scene_sums(legacy)
    tl = rt.build_timeline(script, legacy, TEMPLATE, EN, style_lock=sh.FRUIT_DRAMA)
    assert [s["frames"] for s in tl["shots"]] == [_frames(s["duration_s"]) for s in tl["shots"]]
    # nothing moves on a second re-time
    assert shots.retime_storyboard(legacy, script, template=TEMPLATE, language=EN,
                                   style_lock=sh.FRUIT_DRAMA) is False


class _Run(voice_lines.LineMeasurement):
    """The part of a step run ``sync_storyboard`` reads: its documents and a
    ``save()`` that re-times the script beside the storyboard, as the script
    and assets steps' own do (``episode_common.retime``, then the write)."""

    def __init__(self, ec, script, storyboard):
        self.ec, self.script, self.storyboard = ec, script, storyboard
        self.board_refused = False
        self.saved = []

    def save(self):
        episode_common.retime(self.script, self.ec, self.storyboard)
        self.saved.append(copy.deepcopy(self.script["timing"]))


def test_a_measurement_that_converts_the_storyboard_retimes_the_script_after_it(monkeypatch):
    """``voice_lines``: a measured line saves the script (re-timed beside
    the storyboard as it still is) and then re-times the storyboard; when
    that re-time converts an old storyboard, the script is re-timed and
    saved again, so the two stored documents stay one timing."""
    written = []
    monkeypatch.setattr(episode_common, "write_storyboard",
                        lambda ec, board, script, *, now: written.append(copy.deepcopy(board)) or board)
    script = _script_with_measured_lines()
    ec = _ec(sh.FRUIT_DRAMA)
    legacy = _legacy(_board(script, sh.FRUIT_DRAMA), script, sh.FRUIT_DRAMA)
    episode_common.retime(script, ec, legacy)
    run = _Run(ec, script, legacy)

    line = script["scenes"][2]["lines"][0]
    line["timing"] = dict(line["timing"], duration_s=round(line["timing"]["duration_s"] + 0.137, 3))
    run.save()                   # what measure_line does first ...
    run.sync_storyboard()        # ... then this

    assert len(written) == 1 and written[0]["whole_frames"] is True
    assert len(run.saved) == 2
    assert {sid: s["duration_s"] for sid, s in script["timing"]["scenes"].items()} == _scene_sums(written[0])
    assert script["timing"] == timing.episode_timing(script, TEMPLATE, EN, style_lock=sh.FRUIT_DRAMA,
                                                     storyboard=written[0])

    # a flagged storyboard needs no second save
    line["timing"] = dict(line["timing"], duration_s=round(line["timing"]["duration_s"] + 0.2, 3))
    run.save()
    run.sync_storyboard()
    assert len(written) == 2 and len(run.saved) == 3


# ============================================================ 6. the property

WORDS = ("the phone is ringing again and nobody wants to answer it tonight because "
         "everyone knows who is calling from the island at this hour").split()


def _random_script(rng, n_body, pace=1.0):
    """hook + *n_body* body scenes + cliffhanger over two places, every line
    measured to the millisecond (as real TTS is); *pace* stretches the lines
    so the episodes land under, inside, tightened into and over the 55-80 s
    window."""
    functions = ["hook"] + [rng.choice(["setup", "rising", "peak", "turn"]) for _ in range(n_body)] + ["cliffhanger"]
    chars = [sh.CHAR_KIWILO, sh.CHAR_MANGELLA, sh.CHAR_BROCCOLIA]
    scenes, n = [], 0
    for i, function in enumerate(functions, 1):
        edge = function in ("hook", "cliffhanger")
        lines = []
        for _ in range(1 if edge else rng.choice([2, 2, 3])):
            n += 1
            text = " ".join(rng.sample(WORDS, rng.randint(3, 8)))
            seconds = (rng.uniform(1.0, 1.9) if edge else rng.uniform(1.6, 2.9)) * pace
            lines.append({"line_id": f"l{n:02d}", "speaker": rng.choice(chars), "text": text, "emotion": "neutral",
                          "delivery": "calm",
                          "timing": {"source": "tts_word_timestamps", "duration_s": round(seconds, 3),
                                     "text_hash": timing.text_hash(text), "voice": "edge/x",
                                     "audio": f"assets/voice/l{n:02d}.mp3"}})
        scenes.append(sh._scene(f"s{i:02d}", function, place_id=rng.choice([sh.PLACE_PARLOIR, sh.PLACE_PISCINE]),
                                characters=list(chars), props=[sh.PROP_PHONE] if function == "hook" else [],
                                lines=lines, target_duration_s=5.0))
    script = copy.deepcopy(sh.SCRIPT)
    script["scenes"] = scenes
    script["cliffhanger"]["scene_id"] = scenes[-1]["scene_id"]
    return script


def _record(name) -> dict:
    return {"path": f"/fixture/{name}", "source": name, "sha256": hashlib.sha256(name.encode()).hexdigest()}


def _shot_keys(script, board, style_lock) -> dict:
    """``{shot_id: (scene_id, frames, duration_s, cache_key)}`` of the
    render plan (``render.plan``: the key its S stage is cached under), one
    image per shot, the same images before and after."""
    inputs = {"shots": {s["shot_id"]: _record(f"assets/shots/{s['shot_id']}.png") for s in board["shots"]},
              "lines": {line["line_id"]: _record(f"assets/voice/{line['line_id']}.mp3")
                        for scene in script["scenes"] for line in scene["lines"]},
              "sfx": {}, "bgm": None, "overlay": _record("paper_texture.png"),
              "font": {"family": "Montserrat", "file": "fonts/Montserrat.ttf", "sha256": "0" * 64,
                       "reason": "fixture"}, "word_timings": {}}
    plan = plan_mod.build_render_plan(script=script, storyboard=board, assets={"lines": {}, "sfx": [], "bgm": None},
                                      style_lock=style_lock, template=TEMPLATE, inputs=inputs, ep=1,
                                      story={"story_id": "fixture", "title": "Fixture", "language": EN},
                                      ffmpeg={"version": "6.1.1", "machine": "x86_64"})
    keys = {stage["id"][2:]: stage["cache_key"] for stage in plan["stages"] if stage["kind"] == "shot"}
    return {s["shot_id"]: (s["scene_id"], s["frames"], s["duration_s"], keys[s["shot_id"]])
            for s in plan["timeline"]["shots"]}


def _shift(script, board, style_lock, k, delta):
    """Scene *k*'s first line *delta* seconds longer, the storyboard re-timed
    (``shots.retime_storyboard``, as a measurement does)."""
    script2, board2 = copy.deepcopy(script), copy.deepcopy(board)
    line = script2["scenes"][k]["lines"][0]
    line["timing"] = dict(line["timing"], duration_s=round(line["timing"]["duration_s"] + delta, 3))
    shots.retime_storyboard(board2, script2, template=TEMPLATE, language=EN, style_lock=style_lock)
    return script2, board2


def _trials():
    """32 episodes over the three styles (claymation's jittered shots put
    their duration in their render command, the others only their frames),
    each with three scenes shifted by up to 0.6 s either way."""
    rng = random.Random(20260929)
    for n in range(32):
        style_name = sorted(STYLES)[n % len(STYLES)]
        script = _random_script(rng, rng.randint(6, 9), pace=(1.0, 1.25, 1.45, 1.6)[n % 4])
        ks = rng.sample(range(len(script["scenes"]) - 1), 3)
        yield style_name, script, [(k, round(rng.choice([-1, 1]) * rng.uniform(0.01, 0.6), 3)) for k in ks]


def test_a_change_in_one_scene_leaves_every_later_shots_frames_and_cache_key():
    """Scene k's line moves by any delta: every shot of a later scene whose
    own duration stayed (the window pass may legitimately move a later
    scene -- the cliffhanger's hold, a tightened tail -- and those are left
    out) keeps its frames, its duration and its render cache key. Before
    whole frames, about a fifth of them moved (the stage-6 spike: 20.3 %)."""
    compared = moved_by_the_window = 0
    drifted, states = [], set()
    for style_name, script, shifts in _trials():
        style_lock = STYLES[style_name]
        board = _board(script, style_lock)
        states.add(episode_common.retime(copy.deepcopy(script), _ec(style_lock), board)["timing"]["state"])
        before = _shot_keys(script, board, style_lock)
        for k, delta in shifts:
            script2, board2 = _shift(script, board, style_lock, k, delta)
            after = _shot_keys(script2, board2, style_lock)
            sums, sums2 = _scene_sums(board), _scene_sums(board2)
            later = {scene["scene_id"] for scene in script["scenes"][k + 1:]}
            for shot_id, (sid, frames, duration, key) in before.items():
                if sid not in later:
                    continue
                if sums[sid] != sums2[sid]:
                    moved_by_the_window += 1
                    continue
                compared += 1
                if after[shot_id] != (sid, frames, duration, key):
                    drifted.append((style_name, k, delta, shot_id, (frames, duration), after[shot_id][1:3]))
    assert states == {"under", "ok", "tightened", "over"}
    assert compared > 1000 and 0 < moved_by_the_window < compared / 10
    assert drifted == [], f"{len(drifted)} of {compared} later shots moved: {drifted[:5]}"


def test_every_scene_and_shot_of_a_flagged_storyboard_is_a_whole_frame_and_the_timeline_agrees():
    """The timeline's cumulative frames (``_cumulative_frames``) equal each
    shot's own rounding, and its total is a whole number of frames too."""
    shots_seen = 0
    for style_name, script, _shifts in _trials():
        style_lock = STYLES[style_name]
        board = _board(script, style_lock)
        episode_common.retime(script, _ec(style_lock), board)
        for sid, scene_t in script["timing"]["scenes"].items():
            assert _canonical(scene_t["duration_s"]), (style_name, sid)
        assert _scene_sums(board) == {sid: s["duration_s"] for sid, s in script["timing"]["scenes"].items()}
        tl = rt.build_timeline(script, board, TEMPLATE, EN, style_lock=style_lock)
        for entry in tl["shots"]:
            shots_seen += 1
            assert _residue(entry["duration_s"]) < 0.1, entry
            assert entry["frames"] == _frames(entry["duration_s"]), entry
        assert _residue(tl["total_s"]) < 0.2 and tl["total_frames"] == _frames(tl["total_s"])
    assert shots_seen > 400
