"""Tests for ``clipping.aistory.render.timeline`` (AI Story phase 4 stage 4;
plan phase 4 stage 4, "Renderer" -> timeline.py; spec 6.4, 6.5; DEC-156,
DEC-158).

Fixtures are built the way ``tests/test_story_shot_timing.py`` builds
its own: real ``storyboard_v1`` documents from ``shots.build_storyboard``,
reusing ``tests/test_story_shots.py``'s entities/style-lock/script builders
(``sh``) and ``tests/test_story_timing.py``'s lower-level scene/script
builders (``tt``) for the hand-built synthetic-error case, rather than
duplicating either.

Sections, in order:

1. portability guard (stdlib + clipping.aistory[.render] only)
2. real fixtures: FR fruit_drama (hard_stop) and EN family_3d
   (cut_to_black)
3. total == episode_pass's total
4. line offsets == timing.line_offsets on a covering board
5. shot entries: pass-through fields, frame counts (cumulative rounding)
6. SFX anchors
7. end card / hard_stop vs cut_to_black
8. the transition-window invariant, including the synthetic stale board

Stdlib + pytest only (DEC-012): this file runs in the CI environment.
"""

from __future__ import annotations

import ast
import copy
import sys

import pytest

import test_story_shots as sh
import test_story_timing as tt
from clipping.aistory import shots as shots_mod
from clipping.aistory import timing
from clipping.aistory.render import timeline as rt

EN = "en"
FR = "fr"
TEMPLATE = sh.TEMPLATE
FRUIT_DRAMA = sh.FRUIT_DRAMA
FAMILY_3D = sh.FAMILY_3D


# ======================================================== 1. portability guard

def test_it_imports_nothing_outside_the_standard_library_and_aistory():
    """RC-P8: timeline.py must stay pure. A relative import (``from . import
    x``, ``from .. import x``) or an absolute ``clipping.aistory.x`` import
    is allowed -- everything else must resolve inside the standard
    library."""
    import pathlib

    path = pathlib.Path(rt.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in sys.stdlib_module_names:
                    offenders.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level >= 1:
                continue  # "from . import profiles" / "from .. import timing"
            if node.module and node.module.split(".")[0] == "clipping":
                continue
            top = (node.module or "").split(".")[0]
            if top not in sys.stdlib_module_names:
                offenders.append(node.module)
    assert offenders == [], f"non-stdlib, non-aistory imports: {offenders}"


# ======================================================== 2. real fixtures

def _fr_fruit_drama_fixture():
    """FR fruit_drama, ``hard_stop`` (spec 5's fruit_drama default): a copy
    of ``test_story_shots.SCRIPT`` with the cliffhanger flipped to
    ``hard_stop``, ``language`` set to ``"fr"`` (the timing engine only
    ever reads ``language`` as its own explicit argument, never from the
    script -- so FR text is not needed for a numeric-timing fixture), and
    two SFX cues (one ``"at": "start"``, one ``"at": "lNN"``) for the
    anchor tests."""
    script = copy.deepcopy(sh.SCRIPT)
    script["language"] = FR
    script["cliffhanger"]["cut_to_black"] = False
    script["scenes"][0]["sfx_cues"] = [{"at": "start", "cue": "waves_soft"}]
    script["scenes"][2]["sfx_cues"] = [{"at": "l12", "cue": "gasp_crowd"}]

    plans, sources = sh._fast_plans_for_script(script, FRUIT_DRAMA)
    board, _notes = shots_mod.build_storyboard(
        script, plans, sources, entities=sh.ENTITIES, style_lock=FRUIT_DRAMA, template=TEMPLATE,
        language=FR, consistency_mode="references", now=sh.NOW,
    )
    return script, board


def _en_family_3d_fixture():
    """EN family_3d, ``cut_to_black`` (spec 5's family_3d default,
    ``test_story_shots.SCRIPT`` already has it): two SFX cues added the
    same way as the FR fixture."""
    script = copy.deepcopy(sh.SCRIPT)
    script["scenes"][0]["sfx_cues"] = [{"at": "start", "cue": "twinkle"}]
    script["scenes"][3]["sfx_cues"] = [{"at": "l16", "cue": "pop"}]

    plans, sources = sh._fast_plans_for_script(script, FAMILY_3D)
    board, _notes = shots_mod.build_storyboard(
        script, plans, sources, entities=sh.ENTITIES, style_lock=FAMILY_3D, template=TEMPLATE,
        language=EN, consistency_mode="references", now=sh.NOW,
    )
    return script, board


FIXTURES = [
    pytest.param(_fr_fruit_drama_fixture, FR, FRUIT_DRAMA, False, id="fr_fruit_drama_hard_stop"),
    pytest.param(_en_family_3d_fixture, EN, FAMILY_3D, True, id="en_family_3d_cut_to_black"),
]


def test_the_fixtures_cover_their_scripts():
    """Both fixtures must be *covering* boards (``timing.covers``) -- the
    whole point of building them via ``shots.build_storyboard`` over the
    whole script -- otherwise the "line offsets == timing.line_offsets"
    cross-check below would be vacuous."""
    for builder, _lang, _style, _cut_to_black in [p.values for p in FIXTURES]:
        script, board = builder()
        assert timing.covers(board, script)


# ======================================================== 3. total

@pytest.mark.parametrize("builder,language,style_lock,cut_to_black", [p.values for p in FIXTURES], ids=[
    p.id for p in FIXTURES])
def test_timeline_total_equals_episode_pass_total(builder, language, style_lock, cut_to_black):
    script, board = builder()
    expected, _scene_t = timing.episode_pass(script, TEMPLATE, language, style_lock=style_lock, storyboard=board)
    tl = rt.build_timeline(script, board, TEMPLATE, language, style_lock=style_lock)
    assert tl["total_s"] == expected["total_s"]


# ======================================================== 4. line offsets

@pytest.mark.parametrize("builder,language,style_lock,cut_to_black", [p.values for p in FIXTURES], ids=[
    p.id for p in FIXTURES])
def test_line_offsets_match_timing_line_offsets_on_a_covering_board(builder, language, style_lock, cut_to_black):
    script, board = builder()
    expected_timing, _scene_t = timing.episode_pass(script, TEMPLATE, language, style_lock=style_lock,
                                                     storyboard=board)
    expected_offsets = timing.line_offsets(script, expected_timing, TEMPLATE, storyboard=board)

    tl = rt.build_timeline(script, board, TEMPLATE, language, style_lock=style_lock)
    got = {line["line_id"]: (line["start_s"], round(line["start_s"] + line["duration_s"], 3)) for line in tl["lines"]}

    assert got == expected_offsets


# ============================================== 5. shot entries / frames

@pytest.mark.parametrize("builder,language,style_lock,cut_to_black", [p.values for p in FIXTURES], ids=[
    p.id for p in FIXTURES])
def test_shot_entries_carry_motion_and_modifiers_from_the_storyboard(builder, language, style_lock, cut_to_black):
    script, board = builder()
    tl = rt.build_timeline(script, board, TEMPLATE, language, style_lock=style_lock)

    board_shots_by_id = {shot["shot_id"]: shot for shot in board["shots"]}
    assert len(tl["shots"]) == len(board["shots"])
    for entry in tl["shots"]:
        source = board_shots_by_id[entry["shot_id"]]
        assert entry["scene_id"] == source["scene_id"]
        assert entry["motion"] == source["motion"]
        assert entry["modifiers"] == source["modifiers"]
        assert entry["duration_s"] == source["duration_s"]
        assert entry["frames"] >= 1


@pytest.mark.parametrize("builder,language,style_lock,cut_to_black", [p.values for p in FIXTURES], ids=[
    p.id for p in FIXTURES])
def test_shot_entries_are_in_ascending_start_order_with_no_gap_bigger_than_a_transition(
        builder, language, style_lock, cut_to_black):
    script, board = builder()
    tl = rt.build_timeline(script, board, TEMPLATE, language, style_lock=style_lock)
    starts = [entry["start_s"] for entry in tl["shots"]]
    assert starts == sorted(starts)
    for i in range(len(tl["shots"]) - 1):
        entry = tl["shots"][i]
        nxt = tl["shots"][i + 1]
        natural_end = entry["start_s"] + entry["duration_s"]
        transition_dur = entry["transition_after"]["duration_s"] if entry["transition_after"] else 0.0
        assert nxt["start_s"] == pytest.approx(natural_end - transition_dur, abs=1e-3)


@pytest.mark.parametrize("builder,language,style_lock,cut_to_black", [p.values for p in FIXTURES], ids=[
    p.id for p in FIXTURES])
def test_per_shot_frames_sum_to_the_raw_shot_duration_total(builder, language, style_lock, cut_to_black):
    """The per-shot cumulative-rounding rule (module docstring): summing
    every shot's own ``frames`` reproduces ``round(sum(shot durations) *
    fps)`` exactly, regardless of each shot's individual fractional
    remainder."""
    script, board = builder()
    tl = rt.build_timeline(script, board, TEMPLATE, language, style_lock=style_lock)
    raw_total = sum(shot["duration_s"] for shot in board["shots"])
    assert sum(entry["frames"] for entry in tl["shots"]) == round(raw_total * tl["fps"])


@pytest.mark.parametrize("builder,language,style_lock,cut_to_black", [p.values for p in FIXTURES], ids=[
    p.id for p in FIXTURES])
def test_total_frames_equals_round_total_s_times_fps(builder, language, style_lock, cut_to_black):
    script, board = builder()
    tl = rt.build_timeline(script, board, TEMPLATE, language, style_lock=style_lock)
    assert tl["total_frames"] == round(tl["total_s"] * tl["fps"])
    assert tl["fps"] == 30


def test_cumulative_frames_sums_exactly_over_many_fractional_durations():
    """:func:`timeline._cumulative_frames`, exercised directly over a
    generated set of durations with awkward fractional remainders (the
    "largest remainder" rounding rule's own reason to exist: naive
    per-item rounding can drift by more than one frame over many items,
    this must never drift at all)."""
    import random

    rng = random.Random(20260928)
    for _ in range(50):
        n = rng.randint(1, 40)
        durations = [round(rng.uniform(0.5, 3.5), 3) for _ in range(n)]
        frames = rt._cumulative_frames(durations, 30)
        assert len(frames) == n
        assert all(f >= 0 for f in frames)
        assert sum(frames) == round(sum(durations) * 30)


# ======================================================== 6. sfx anchors

def test_sfx_anchor_at_start_is_the_scenes_own_start():
    script, board = _fr_fruit_drama_fixture()
    tl = rt.build_timeline(script, board, TEMPLATE, FR, style_lock=FRUIT_DRAMA)
    anchor = next(a for a in tl["sfx_anchors"] if a["scene_id"] == "s01" and a["at"] == "start")
    assert anchor["cue"] == "waves_soft"
    scene_start = next(entry["start_s"] for entry in tl["shots"] if entry["scene_id"] == "s01")
    assert anchor["start_s"] == pytest.approx(scene_start, abs=1e-3)


def test_sfx_anchor_at_a_line_is_that_lines_own_start():
    script, board = _fr_fruit_drama_fixture()
    tl = rt.build_timeline(script, board, TEMPLATE, FR, style_lock=FRUIT_DRAMA)
    anchor = next(a for a in tl["sfx_anchors"] if a["at"] == "l12")
    assert anchor["cue"] == "gasp_crowd"
    line = next(line for line in tl["lines"] if line["line_id"] == "l12")
    assert anchor["start_s"] == line["start_s"]


def test_sfx_anchors_present_for_the_family_3d_fixture_too():
    script, board = _en_family_3d_fixture()
    tl = rt.build_timeline(script, board, TEMPLATE, EN, style_lock=FAMILY_3D)
    cues = {(a["scene_id"], a["at"], a["cue"]) for a in tl["sfx_anchors"]}
    assert ("s01", "start", "twinkle") in cues
    assert ("s04", "l16", "pop") in cues


# ================================================ 7. end card / cliffhanger

def test_hard_stop_has_no_end_card_and_the_last_shot_ends_the_file():
    script, board = _fr_fruit_drama_fixture()
    tl = rt.build_timeline(script, board, TEMPLATE, FR, style_lock=FRUIT_DRAMA)
    assert tl["end_card"] is None
    assert tl["shots"][-1]["transition_after"] is None
    last = tl["shots"][-1]
    assert last["start_s"] + last["duration_s"] == pytest.approx(tl["total_s"], abs=1e-3)


def test_cut_to_black_end_card_arithmetic_matches_episode_pass():
    script, board = _en_family_3d_fixture()
    tl = rt.build_timeline(script, board, TEMPLATE, EN, style_lock=FAMILY_3D)
    fadeblack = TEMPLATE["transitions_s"]["fadeblack"]
    card_s = TEMPLATE["end_card_s"]

    assert tl["end_card"] is not None
    assert tl["end_card"]["fade_duration_s"] == fadeblack
    assert tl["end_card"]["duration_s"] == card_s
    # spec 6.4/timing.py's own end_card_addition: card_s - fadeblack added
    # to the raw (shots - transitions) total.
    assert tl["end_card"]["end_s"] == pytest.approx(tl["total_s"], abs=1e-3)
    # the fade is carved out of the last shot's own natural runtime, not
    # appended after it (timing._effective_tail_floor's own contract).
    last = tl["shots"][-1]
    natural_end = last["start_s"] + last["duration_s"]
    assert tl["end_card"]["fade_start_s"] == pytest.approx(natural_end - fadeblack, abs=1e-3)
    assert last["transition_after"] == {"type": "fadeblack", "duration_s": fadeblack}


# ==================================================== 8. transition window

def _bare_shot(shot_id, scene_id, duration_s, order):
    return {
        "shot_id": shot_id, "scene_id": scene_id, "order": order, "duration_s": round(duration_s, 3),
        "motion": {"type": "hold", "zoom_from": 1.0, "zoom_to": 1.0, "pan": "none"}, "modifiers": [],
    }


def _three_scene_two_place_script():
    """Not built through ``shots.build_storyboard`` -- a hand-built
    ``timing.py``-style fixture (``tt``'s own helpers), so a synthetic,
    deliberately stale storyboard can be constructed shot by shot (see
    :func:`test_a_stale_storyboard_desyncing_a_scenes_own_shots_raises`).
    s01/s02 share a place (dissolve between them); s02/s03 do not
    (fadeblack)."""
    line1 = tt._timed_line("l01", "one two three", 2.0)
    line2 = tt._timed_line("l02", "four five six", 2.0)
    line3 = tt._timed_line("l03", "seven eight nine", 2.0)
    s01 = tt._scene("s01", "setup", place_id="place_a", lines=[line1])
    s02 = tt._scene("s02", "setup", place_id="place_a", lines=[line2])
    s03 = tt._scene("s03", "setup", place_id="place_b", lines=[line3])
    scenes = [s01, s02, s03]
    return scenes, tt._episode_script(scenes, cut_to_black=False)


def test_a_well_formed_hand_built_board_never_raises():
    """Sanity companion to the synthetic-error test below: the SAME script,
    with shot durations taken directly from ``timing.episode_pass``'s own
    output (no desync at all), must never trip the invariant -- proving the
    error case below is really about the *staleness*, not an overly eager
    check."""
    scenes, script = _three_scene_two_place_script()
    real_timing, _scene_t = timing.episode_pass(script, TEMPLATE, EN, style_lock=None)
    durations = {sid: real_timing["scenes"][sid]["duration_s"] for sid in ("s01", "s02", "s03")}

    board_shots = [
        _bare_shot("sh01", "s01", durations["s01"], 1),
        _bare_shot("sh02", "s02", durations["s02"], 2),
        _bare_shot("sh03", "s03", durations["s03"], 3),
    ]
    transitions = [
        {"after": "sh01", "type": "dissolve", "duration_s": TEMPLATE["transitions_s"]["dissolve"]},
        {"after": "sh02", "type": "fadeblack", "duration_s": TEMPLATE["transitions_s"]["fadeblack"]},
    ]
    board = {"shots": board_shots, "transitions": transitions}

    tl = rt.build_timeline(script, board, TEMPLATE, EN, style_lock=None)
    assert tl["total_s"] == real_timing["total_s"]


def test_a_stale_storyboard_desyncing_a_scenes_own_shots_raises():
    """A "synthetic board" (spec: the fail-first requirement's own
    phrasing) that shifts duration from scene s01's own shot onto s03's,
    while leaving s02's shot at its true value: the grand total is
    preserved (shot1 loses exactly what shot3 gains), so the total
    cross-check alone cannot catch it, but s02's own start -- and so its
    own OUTGOING (fadeblack, into s03) transition window -- shifts earlier
    by the same amount, sliding backward into where s02's own line
    (placed independently, from the untouched script) is still speaking."""
    scenes, script = _three_scene_two_place_script()
    real_timing, _scene_t = timing.episode_pass(script, TEMPLATE, EN, style_lock=None)
    d1 = real_timing["scenes"]["s01"]["duration_s"]
    d2 = real_timing["scenes"]["s02"]["duration_s"]
    d3 = real_timing["scenes"]["s03"]["duration_s"]

    delta = 2.0
    assert d1 > delta, "fixture must have enough room to shrink s01 by delta"

    board_shots = [
        _bare_shot("sh01", "s01", d1 - delta, 1),
        _bare_shot("sh02", "s02", d2, 2),
        _bare_shot("sh03", "s03", d3 + delta, 3),
    ]
    transitions = [
        {"after": "sh01", "type": "dissolve", "duration_s": TEMPLATE["transitions_s"]["dissolve"]},
        {"after": "sh02", "type": "fadeblack", "duration_s": TEMPLATE["transitions_s"]["fadeblack"]},
    ]
    board = {"shots": board_shots, "transitions": transitions}
    assert timing.covers(board, script)

    with pytest.raises(rt.TimelineError, match="transition window"):
        rt.build_timeline(script, board, TEMPLATE, EN, style_lock=None)


def test_timeline_error_is_a_value_error():
    """A named error (spec), not a bare ``ValueError``/``KeyError`` -- but
    still catchable as a ``ValueError`` by a caller that only wants
    "something about this input was wrong"."""
    assert issubclass(rt.TimelineError, ValueError)
