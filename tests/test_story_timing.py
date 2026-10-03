"""Tests for the AI Story timing engine (phase 3 stage 2; spec 6.4).

``clipping/aistory/timing.py`` is pure and stdlib-only (plus its own package,
``clipping.aistory``): no clock, no disk, no network, so every case here
builds its scripts/scenes/shots as plain dicts with small helpers, the same
style as ``tests/test_story_episode_schemas.py``. Sections, in order:

1. portability guard (stdlib + clipping.aistory only)
2. constants
3. line timing: text_hash, estimate_line, estimated_timing, line_duration
4. slot lookup: slot_name, slot_range (incl. the family_3d scene_clamp_s),
   tail_for
5. scene_timing's branches (no lines, under -> hold, tightened, over)
6. word_budget sanity values
7. transitions: plan_transitions' grammar
8. allocate_shots: invariants over a generated set, plus hand cases
9. episode_timing: ok / tightened / over / under, end card arithmetic,
   storyboard transitions vs predicted ones
10. line_offsets: no overlap, no line before its scene's start

Stdlib + pytest only (DEC-012): this file runs in the CI environment.
"""

from __future__ import annotations

import ast
import random
import sys

import pytest

from clipping.aistory import schemas, templates, timing

EN = "en"
FR = "fr"

TEMPLATE = templates.load_episode_template("serial_60s_v1")

FAMILY_3D_STYLE_LOCK = {
    "episode_defaults": {"scene_clamp_s": {"peak": 12, "tender": 12}},
}


# ======================================================== 1. portability guard

def test_it_imports_nothing_outside_the_standard_library_and_aistory():
    """RC-P8: the timing engine must stay pure. A relative import (``from .
    import x``) or an absolute ``clipping.aistory.x`` import is allowed --
    everything else must resolve inside the standard library."""
    import pathlib

    path = pathlib.Path(timing.__file__)
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
                continue  # "from . import schemas" etc: inside clipping.aistory
            if node.module and node.module.split(".")[0] == "clipping":
                continue  # "from clipping.aistory import schemas"
            top = (node.module or "").split(".")[0]
            if top not in sys.stdlib_module_names:
                offenders.append(node.module)
    assert offenders == [], f"non-stdlib, non-aistory imports: {offenders}"


# ======================================================== 2. constants

def test_rate_per_char_documented_values():
    assert timing.RATE_PER_CHAR == {"fr": 0.070, "en": 0.065}


def test_chars_per_word_documented_values():
    assert timing.CHARS_PER_WORD == {"fr": 5.7, "en": 5.5}


def test_min_line_s_documented_value():
    assert timing.MIN_LINE_S == 0.5


# ======================================================== 3. line timing

def test_text_hash_is_16_hex_chars():
    h = timing.text_hash("Bonjour tout le monde")
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


def test_text_hash_normalises_whitespace():
    assert timing.text_hash("  Hello   world  ") == timing.text_hash("Hello world")
    assert timing.text_hash("Hello\nworld\t!") == timing.text_hash("Hello world !")


def test_text_hash_differs_for_different_text():
    assert timing.text_hash("Hello world") != timing.text_hash("Hello there")


def test_estimate_line_floors_short_text():
    assert timing.estimate_line("Hi", EN) == timing.MIN_LINE_S


def test_estimate_line_matches_the_rate_formula():
    text = "x" * 100
    expected = round(max(timing.MIN_LINE_S, 100 * timing.RATE_PER_CHAR[FR]), 3)
    assert timing.estimate_line(text, FR) == expected == 7.0


def test_estimate_line_normalises_before_counting():
    assert timing.estimate_line("  x  " * 20, EN) == timing.estimate_line("x " * 19 + "x", EN)


def test_estimate_line_rejects_unknown_language():
    with pytest.raises(ValueError, match="de"):
        timing.estimate_line("hallo", "de")


def test_estimated_timing_shape():
    block = timing.estimated_timing("Bonjour", FR)
    assert block == {
        "source": "estimated",
        "duration_s": timing.estimate_line("Bonjour", FR),
        "text_hash": timing.text_hash("Bonjour"),
        "voice": None,
        "audio": None,
    }


def _line(line_id, text, *, timing_block=None):
    return {
        "line_id": line_id, "speaker": "char_a", "text": text, "emotion": "neutral",
        "delivery": "calm", "timing": timing_block,
    }


def _timed_line(line_id, text, duration, *, source="tts_word_timestamps"):
    return _line(line_id, text, timing_block={
        "source": source, "duration_s": duration, "text_hash": timing.text_hash(text),
        "voice": "edge/fr-FR-DeniseNeural", "audio": "assets/voice/l.mp3",
    })


def test_line_duration_uses_measured_timing_when_the_hash_matches():
    line = _timed_line("l01", "Un texte mesure.", 3.21)
    assert timing.line_duration(line, FR) == (3.21, "tts_word_timestamps")


def test_line_duration_falls_back_to_estimate_when_the_hash_mismatches():
    line = _timed_line("l01", "Un texte mesure.", 3.21)
    line["text"] = "Un texte edite depuis."
    seconds, source = timing.line_duration(line, FR)
    assert source == "estimated"
    assert seconds == timing.estimate_line("Un texte edite depuis.", FR)


def test_line_duration_estimates_when_timing_is_absent():
    line = _line("l01", "Pas encore mesure.")
    seconds, source = timing.line_duration(line, FR)
    assert source == "estimated"
    assert seconds == timing.estimate_line("Pas encore mesure.", FR)


def test_line_duration_uses_estimated_timing_whose_hash_still_matches():
    block = timing.estimated_timing("Une phrase stable.", EN)
    line = _line("l01", "Une phrase stable.", timing_block=block)
    assert timing.line_duration(line, EN) == (block["duration_s"], "estimated")


# ======================================================== 4. slot lookup

@pytest.mark.parametrize("function,slot", [
    ("recap", "recap"), ("hook", "hook"), ("setup", "body"), ("rising", "body"),
    ("peak", "body"), ("turn", "body"), ("cliffhanger", "cliffhanger"),
])
def test_slot_name_matches_every_scene_function(function, slot):
    assert timing.slot_name(function, TEMPLATE) == slot


def test_slot_name_rejects_an_unknown_function():
    with pytest.raises(ValueError, match="wide_establishing"):
        timing.slot_name("wide_establishing", TEMPLATE)


def _scene(scene_id, function, *, place_id="place_a", lines=None, target_duration=6.0, emotion="neutral"):
    return {
        "scene_id": scene_id, "function": function, "place_id": place_id, "time_variant": "day",
        "characters": ["char_a", "char_b"], "props": [], "summary": "Something happens.",
        "emotion": emotion, "target_duration_s": target_duration,
        "lines": lines if lines is not None else [], "sfx_cues": [], "on_screen_text": None,
        "state": "written", "source": "E2", "rev": 1,
    }


def test_slot_range_without_a_style_lock():
    assert timing.slot_range(_scene("s02", "setup"), TEMPLATE) == (4.0, 8.0)
    assert timing.slot_range(_scene("s01", "hook"), TEMPLATE) == (1.5, 3.5)


def test_slot_range_raised_by_function_via_scene_clamp_s():
    scene = _scene("s03", "peak", emotion="neutral")
    lo, hi = timing.slot_range(scene, TEMPLATE, style_lock=FAMILY_3D_STYLE_LOCK)
    assert (lo, hi) == (4.0, 12.0)


def test_slot_range_raised_by_emotion_via_scene_clamp_s():
    scene = _scene("s03", "setup", emotion="tender")
    lo, hi = timing.slot_range(scene, TEMPLATE, style_lock=FAMILY_3D_STYLE_LOCK)
    assert (lo, hi) == (4.0, 12.0)


def test_slot_range_clamp_never_lowers_the_upper_bound():
    style_lock = {"episode_defaults": {"scene_clamp_s": {"hook": 1.0}}}
    scene = _scene("s01", "hook")
    assert timing.slot_range(scene, TEMPLATE, style_lock=style_lock) == (1.5, 3.5)


def test_slot_range_against_the_real_family_3d_style_template():
    style = templates.load_style("family_3d")
    scene = _scene("s03", "peak", emotion="tender")
    lo, hi = timing.slot_range(scene, TEMPLATE, style_lock=style)
    assert (lo, hi) == (4.0, 12.0)


def test_tail_for_peak_functions_get_the_peak_pause():
    assert timing.tail_for("peak", TEMPLATE) == 1.2
    assert timing.tail_for("cliffhanger", TEMPLATE) == 1.2


def test_tail_for_other_functions_get_the_plain_tail():
    assert timing.tail_for("setup", TEMPLATE) == 0.6
    assert timing.tail_for("hook", TEMPLATE) == 0.6


# ======================================================== 4b. episode_slots (stage 12b)

TEMPLATE_90 = templates.load_episode_template("serial_90s_v1")


def test_episode_slots_60s_ep1_no_recap_8_body():
    assert timing.episode_slots(TEMPLATE, 1) == ["hook"] + ["body"] * 8 + ["cliffhanger"]


def test_episode_slots_60s_ep2_recap_first_8_body():
    assert timing.episode_slots(TEMPLATE, 2) == ["recap", "hook"] + ["body"] * 8 + ["cliffhanger"]


def test_episode_slots_90s_ep1_no_recap_10_body():
    assert timing.episode_slots(TEMPLATE_90, 1) == ["hook"] + ["body"] * 10 + ["cliffhanger"]


def test_episode_slots_90s_ep2_recap_first_body_clamped_down_to_9():
    # default_body_count is 10, but scenes tops out at 12 and the recap
    # episode already spends 3 scenes on recap+hook+cliffhanger.
    assert timing.episode_slots(TEMPLATE_90, 2) == ["recap", "hook"] + ["body"] * 9 + ["cliffhanger"]


def test_episode_slots_is_pure_and_returns_a_fresh_list():
    a = timing.episode_slots(TEMPLATE, 1)
    a.append("intruder")
    b = timing.episode_slots(TEMPLATE, 1)
    assert b == ["hook"] + ["body"] * 8 + ["cliffhanger"]


# ======================================================== 5. scene_timing

def test_scene_timing_no_lines_clamps_target_into_the_slot():
    scene = _scene("s01", "hook", lines=[], target_duration=2.5)
    result = timing.scene_timing(scene, TEMPLATE, EN)
    assert result == {
        "duration_s": 2.5, "tail_s": 0.0, "hold_s": 0.0, "speech_s": 0.0,
        "state": "ok", "line_starts": {},
    }


def test_scene_timing_no_lines_clamps_below_lo():
    scene = _scene("s01", "hook", lines=[], target_duration=0.5)
    result = timing.scene_timing(scene, TEMPLATE, EN)
    assert result["duration_s"] == 1.5  # hook slot lo


def test_scene_timing_no_lines_clamps_above_hi():
    scene = _scene("s02", "setup", lines=[], target_duration=99)
    result = timing.scene_timing(scene, TEMPLATE, EN)
    assert result["duration_s"] == 8.0  # body slot hi


def test_scene_timing_line_starts_layout():
    lines = [_timed_line("l01", "One.", 1.0), _timed_line("l02", "Two.", 1.5)]
    scene = _scene("s02", "setup", lines=lines)
    result = timing.scene_timing(scene, TEMPLATE, EN)
    assert result["line_starts"] == {"l01": 0.35, "l02": 0.35 + 1.0 + 0.25}
    assert result["speech_s"] == 2.5


def test_scene_timing_under_lo_extends_a_hold():
    lines = [_timed_line("l01", "Short.", 1.0)]
    scene = _scene("s02", "setup", lines=lines)
    raw = 0.35 + 1.0 + 0.6  # before_first + line + tail, no gaps (one line)
    result = timing.scene_timing(scene, TEMPLATE, EN)
    assert result["state"] == "ok"
    assert result["duration_s"] == 4.0  # body slot lo
    assert result["hold_s"] == round(4.0 - raw, 3)
    assert result["tail_s"] == 0.6


def test_scene_timing_over_hi_tightens_the_tail():
    lines = [_timed_line("l01", "A longer line that fills the scene.", 7.3)]
    scene = _scene("s02", "setup", lines=lines)
    raw = 0.35 + 7.3 + 0.6  # = 8.25, over the body hi of 8.0 but within the tail's shrink room
    result = timing.scene_timing(scene, TEMPLATE, EN)
    assert result["state"] == "tightened"
    assert result["duration_s"] == 8.0
    assert result["tail_s"] == round(0.6 - (raw - 8.0), 3)
    assert result["tail_s"] >= TEMPLATE["pauses_s"]["tail_floor"]


def test_scene_timing_over_hi_stays_over_once_the_tail_hits_the_floor():
    lines = [_timed_line("l01", "A very long line that overruns the whole scene badly.", 9.0)]
    scene = _scene("s02", "setup", lines=lines)
    raw = 0.35 + 9.0 + 0.6  # = 9.95
    max_shrink = 0.6 - TEMPLATE["pauses_s"]["tail_floor"]  # 0.3
    result = timing.scene_timing(scene, TEMPLATE, EN, whole_frames=False)
    assert result["state"] == "over"
    assert result["tail_s"] == TEMPLATE["pauses_s"]["tail_floor"]
    assert result["duration_s"] == round(raw - max_shrink, 3)
    assert result["duration_s"] > 8.0


def test_scene_timing_tail_floor_argument_raises_the_effective_floor():
    lines = [_timed_line("l01", "A longer line that fills the scene.", 7.6)]
    scene = _scene("s02", "setup", lines=lines)
    raw = 0.35 + 7.6 + 0.6
    result = timing.scene_timing(scene, TEMPLATE, EN, tail_floor=0.5, whole_frames=False)
    # only 0.1 s of shrink room now (0.6 - 0.5), not the template's 0.3
    assert result["tail_s"] == 0.5
    assert result["duration_s"] == round(raw - 0.1, 3)
    assert result["state"] == "over"


def test_scene_timing_style_lock_clamp_avoids_tightening_a_peak_scene():
    lines = [_timed_line("l01", "A long emotional hold for the peak beat here.", 9.0)]
    scene = _scene("s03", "peak", lines=lines, emotion="tender")
    without_clamp = timing.scene_timing(scene, TEMPLATE, EN)
    with_clamp = timing.scene_timing(scene, TEMPLATE, EN, style_lock=FAMILY_3D_STYLE_LOCK)
    assert without_clamp["state"] in ("tightened", "over")
    assert with_clamp["state"] == "ok"


def test_scene_timing_rounds_to_three_decimals():
    lines = [_timed_line("l01", "x", 1.23456)]
    scene = _scene("s02", "setup", lines=lines)
    result = timing.scene_timing(scene, TEMPLATE, EN)
    for key in ("duration_s", "tail_s", "hold_s", "speech_s"):
        assert round(result[key], 3) == result[key]


# ======================================================== 6. word_budget

def test_word_budget_fr_body_sanity():
    assert timing.word_budget("setup", 6, FR, TEMPLATE) == 12


def test_word_budget_fr_hook_sanity():
    assert timing.word_budget("hook", 3, FR, TEMPLATE) == 5


def test_word_budget_fr_cliffhanger_sanity():
    assert timing.word_budget("cliffhanger", 4, FR, TEMPLATE) == 6


def test_word_budget_never_below_three():
    assert timing.word_budget("hook", 0.0, FR, TEMPLATE) == 3


def test_word_budget_clamps_target_into_the_slot():
    # target_hint far outside [1.5, 3.5]: clamped to 3.5 before the formula runs
    at_hint = timing.word_budget("hook", 3.5, FR, TEMPLATE)
    assert timing.word_budget("hook", 999, FR, TEMPLATE) == at_hint


def test_word_budget_rejects_unknown_language():
    with pytest.raises(ValueError, match="de"):
        timing.word_budget("hook", 3, "de", TEMPLATE)


# ======================================================== 7. transitions

def _shot(shot_id, scene_id, lines):
    return {"shot_id": shot_id, "scene_id": scene_id, "lines": lines}


def test_plan_transitions_cut_within_a_scene():
    shots = [_shot("sh01", "s01", ["l01"]), _shot("sh02", "s01", ["l02"])]
    scenes_by_id = {"s01": _scene("s01", "setup", place_id="place_a")}
    result = timing.plan_transitions(shots, scenes_by_id, TEMPLATE)
    assert result == [{"after": "sh01", "type": "cut", "duration_s": 0.0}]


def test_plan_transitions_dissolve_same_place():
    shots = [_shot("sh01", "s01", []), _shot("sh02", "s02", [])]
    scenes_by_id = {
        "s01": _scene("s01", "setup", place_id="place_a"),
        "s02": _scene("s02", "rising", place_id="place_a"),
    }
    result = timing.plan_transitions(shots, scenes_by_id, TEMPLATE)
    assert result == [{"after": "sh01", "type": "dissolve", "duration_s": 0.4}]


def test_plan_transitions_fadeblack_place_change():
    shots = [_shot("sh01", "s01", []), _shot("sh02", "s02", [])]
    scenes_by_id = {
        "s01": _scene("s01", "setup", place_id="place_a"),
        "s02": _scene("s02", "rising", place_id="place_b"),
    }
    result = timing.plan_transitions(shots, scenes_by_id, TEMPLATE)
    assert result == [{"after": "sh01", "type": "fadeblack", "duration_s": 0.4}]


def test_plan_transitions_one_entry_per_boundary_no_end_card():
    shots = [_shot(f"sh{i:02d}", "s01", []) for i in range(1, 4)]
    scenes_by_id = {"s01": _scene("s01", "setup")}
    result = timing.plan_transitions(shots, scenes_by_id, TEMPLATE)
    assert len(result) == 2
    assert all(t["type"] == "cut" for t in result)


# ======================================================== 8. allocate_shots

def _gen_scene_and_shots(rng, function="setup"):
    n_lines = rng.randint(1, 4)
    lines = [_timed_line(f"l{j + 1:02d}", "mot " * rng.randint(2, 10), round(rng.uniform(0.6, 3.5), 3))
             for j in range(n_lines)]
    scene = _scene("s01", function, lines=lines)
    n_shots = rng.randint(2, 4)
    shots = [_shot(f"sh{i + 1:02d}", "s01", []) for i in range(n_shots)]
    line_ids = [line["line_id"] for line in lines]
    assignment = sorted(rng.randrange(n_shots) for _ in line_ids)
    for line_id, shot_idx in zip(line_ids, assignment):
        shots[shot_idx]["lines"].append(line_id)
    return scene, shots


def test_allocate_shots_invariants_over_a_generated_set():
    rng = random.Random(20260927)
    min_shot = TEMPLATE["min_shot_s"]
    for _ in range(300):
        function = rng.choice(list(schemas.BODY_FUNCTIONS))
        scene, shots = _gen_scene_and_shots(rng, function)
        scene_t = timing.scene_timing(scene, TEMPLATE, EN)

        durations, extra_hold = timing.allocate_shots(scene, scene_t, shots, TEMPLATE)

        assert len(durations) == len(shots)
        assert abs(sum(durations) - (scene_t["duration_s"] + extra_hold)) < 1e-6
        assert all(d >= min_shot - 1e-9 for d in durations)

        need = len(shots) * min_shot - scene_t["duration_s"]
        if need > 1e-9:
            assert extra_hold > 0
            assert abs(extra_hold - need) < 1e-6
        else:
            assert extra_hold == 0.0

        again, extra_again = timing.allocate_shots(scene, scene_t, shots, TEMPLATE)
        assert again == durations and extra_again == extra_hold


def test_allocate_shots_hand_case_establishing_two_speakers_reaction():
    lines = [_timed_line("l01", "First speaker line here today.", 2.0),
             _timed_line("l02", "Second speaker responds right now.", 2.5)]
    scene = _scene("s01", "rising", lines=lines)
    scene_t = timing.scene_timing(scene, TEMPLATE, EN)
    shots = [
        _shot("sh01", "s01", []),      # establishing (leading, unanchored)
        _shot("sh02", "s01", ["l01"]),  # speaker 1
        _shot("sh03", "s01", ["l02"]),  # speaker 2
        _shot("sh04", "s01", []),      # reaction (trailing, unanchored)
    ]
    min_shot = TEMPLATE["min_shot_s"]

    durations, extra_hold = timing.allocate_shots(scene, scene_t, shots, TEMPLATE)

    assert extra_hold == 0.0
    assert abs(sum(durations) - scene_t["duration_s"]) < 1e-6
    assert all(d >= min_shot - 1e-9 for d in durations)

    # the establishing shot carved its minimum from speaker 1's span
    assert durations[0] == pytest.approx(max(min_shot, 1.0))
    # the reaction shot carved its minimum from speaker 2's span
    assert durations[3] == pytest.approx(max(min_shot, 1.0))
    # speaker 2 is never preceded by anything carved from ITS OWN span, so its
    # on-screen start lands exactly on its first line's start: a cut on a line start
    speaker2_start = durations[0] + durations[1]
    assert speaker2_start == pytest.approx(scene_t["line_starts"]["l02"])


def test_allocate_shots_no_anchors_split_evenly():
    scene = _scene("s01", "hook", lines=[], target_duration=3.0)
    scene_t = timing.scene_timing(scene, TEMPLATE, EN)
    shots = [_shot("sh01", "s01", []), _shot("sh02", "s01", []), _shot("sh03", "s01", [])]
    durations, extra_hold = timing.allocate_shots(scene, scene_t, shots, TEMPLATE)
    assert extra_hold == 0.0
    assert abs(sum(durations) - scene_t["duration_s"]) < 1e-6
    assert durations[0] == durations[1] == pytest.approx(scene_t["duration_s"] / 3, abs=1e-3)


def test_allocate_shots_insufficient_room_holds_and_reports_shortfall():
    scene = _scene("s01", "hook", lines=[], target_duration=1.5)  # hook lo
    scene_t = timing.scene_timing(scene, TEMPLATE, EN)
    shots = [_shot(f"sh{i:02d}", "s01", []) for i in range(1, 5)]  # 4 * 0.8 = 3.2 > 1.5
    durations, extra_hold = timing.allocate_shots(scene, scene_t, shots, TEMPLATE)
    min_shot = TEMPLATE["min_shot_s"]
    assert durations == [min_shot] * 4
    assert extra_hold == pytest.approx(4 * min_shot - scene_t["duration_s"])


# ======================================================== 9. episode_timing

def _episode_line(line_id, text, duration):
    return _timed_line(line_id, text, duration)


def _episode_script(scenes, *, cut_to_black=False):
    return {
        "scenes": scenes,
        "cliffhanger": {"scene_id": scenes[-1]["scene_id"], "reveal": "A reveal.", "cut_to_black": cut_to_black},
    }


def _storyboard_from_scenes(scenes, *, transition_types=None):
    """A minimal ``storyboard_v1``-shaped dict (only ``shots``/``transitions``,
    what ``_boundary_from_storyboard`` reads): one shot per scene, matching
    *scenes* in order. ``transition_types`` maps a scene-boundary index
    (0-based, between ``scenes[i]`` and ``scenes[i + 1]``) to ``(type,
    duration_s)``; a boundary left out gets no transition entry at all --
    an implicit cut, per production semantics."""
    shots = [_shot(f"sh{i + 1:02d}", scene["scene_id"], []) for i, scene in enumerate(scenes)]
    transitions = []
    for i, (kind, duration) in (transition_types or {}).items():
        transitions.append({"after": shots[i]["shot_id"], "type": kind, "duration_s": duration})
    return {"shots": shots, "transitions": transitions}


def _four_scene_two_place_script():
    scenes = [
        _scene("s01", "hook", place_id="place_a", lines=[_episode_line("l01", "Hook.", 2.0)]),
        _scene("s02", "setup", place_id="place_a", lines=[_episode_line("l02", "Body one.", 6.5)]),
        _scene("s03", "setup", place_id="place_b", lines=[_episode_line("l03", "Body two.", 6.5)]),
        _scene("s04", "cliffhanger", place_id="place_b", lines=[_episode_line("l04", "Cliff.", 3.0)]),
    ]
    return scenes, _episode_script(scenes, cut_to_black=False)


def _ok_scenes(n_body=8, *, place_id="place_a"):
    scenes = [_scene("s01", "hook", place_id=place_id, lines=[_episode_line("l01", "The hook line.", 2.5)])]
    for i in range(2, 2 + n_body):
        function = "peak" if i == 3 else "setup"
        duration = 6.2 if function == "peak" else 6.8
        scenes.append(_scene(f"s{i:02d}", function, place_id=place_id,
                              lines=[_episode_line(f"l{i:02d}", "A body line.", duration)]))
    cliff_id = f"s{1 + n_body + 1:02d}"
    scenes.append(_scene(cliff_id, "cliffhanger", place_id=place_id,
                          lines=[_episode_line(f"l{1 + n_body + 1:02d}", "The cliffhanger.", 3.4)]))
    return scenes


def _sum_of_reported_scenes(result, boundary_sum, end_card_addition):
    return round(sum(s["duration_s"] for s in result["scenes"].values()) - boundary_sum + end_card_addition, 3)


def test_episode_timing_ok_case():
    scenes = _ok_scenes(n_body=8)
    script = _episode_script(scenes, cut_to_black=False)
    result = timing.episode_timing(script, TEMPLATE, EN)

    assert result["state"] == "ok"
    assert result["flags"] == []
    assert TEMPLATE["window_s"][0] <= result["total_s"] <= TEMPLATE["tighten_above_s"]
    boundary_sum = 0.4 * (len(scenes) - 1)  # all same place: dissolve
    assert result["total_s"] == _sum_of_reported_scenes(result, boundary_sum, 0.0)
    assert all(s["state"] == "ok" for s in result["scenes"].values())


def test_episode_timing_tightened_case():
    scenes = _ok_scenes(n_body=10)
    script = _episode_script(scenes, cut_to_black=False)
    result = timing.episode_timing(script, TEMPLATE, EN, whole_frames=False)

    assert result["state"] == "tightened"
    assert result["flags"] == []
    assert TEMPLATE["tighten_above_s"] < result["total_s"] <= TEMPLATE["window_s"][1]
    floor = TEMPLATE["pauses_s"]["tail_floor"]
    # the cliffhanger has the largest natural tail (1.2), so it is shrunk first
    # and, given how far over tighten_above_s this episode is, all the way to the floor
    cliff_sid = scenes[-1]["scene_id"]
    assert result["scenes"][cliff_sid]["tail_s"] == pytest.approx(floor)


def test_episode_timing_over_case_flags_and_order():
    scenes = _ok_scenes(n_body=14)
    script = _episode_script(scenes, cut_to_black=False)
    result = timing.episode_timing(script, TEMPLATE, EN)

    assert result["state"] == "over"
    assert result["total_s"] > TEMPLATE["window_s"][1]
    assert result["flags"][0]["kind"] == "episode_over"
    excess = result["flags"][0]["seconds"]
    assert excess == pytest.approx(result["total_s"] - TEMPLATE["window_s"][1], abs=1e-3)

    trim_flags = [f for f in result["flags"] if f["kind"] == "trim_line"]
    assert len(trim_flags) >= 1
    assert all("Trim this line" in f["message"] for f in trim_flags)
    # longest lines first, and only as many as needed
    durations = [f["seconds"] for f in trim_flags]
    assert durations == sorted(durations, reverse=True)
    assert sum(durations) * 2 >= excess
    if len(durations) > 1:
        assert sum(durations[:-1]) * 2 < excess  # the last one was necessary


def test_episode_timing_under_case_holds_cliffhanger_first():
    scenes = [
        _scene("s01", "hook", place_id="place_a", lines=[_episode_line("l01", "Hook.", 1.0)]),
    ]
    for i in range(2, 8):
        scenes.append(_scene(f"s{i:02d}", "setup", place_id="place_a",
                              lines=[_episode_line(f"l{i:02d}", "Body.", 1.0)]))
    scenes.append(_scene("s08", "cliffhanger", place_id="place_a", lines=[_episode_line("l08", "Cliff.", 1.0)]))
    script = _episode_script(scenes, cut_to_black=False)

    result = timing.episode_timing(script, TEMPLATE, EN)

    assert result["total_s"] < TEMPLATE["window_s"][0]
    assert result["state"] == "under"
    assert len(result["flags"]) == 1
    assert result["flags"][0]["kind"] == "episode_under"
    assert result["flags"][0]["seconds"] == pytest.approx(TEMPLATE["window_s"][0] - result["total_s"], abs=1e-3)

    # cliffhanger extended first
    cliff_scene = _scene("s08", "cliffhanger", lines=[_episode_line("l08", "Cliff.", 1.0)])
    cliff_natural = timing.scene_timing(cliff_scene, TEMPLATE, EN)
    assert result["scenes"]["s08"]["hold_s"] > cliff_natural["hold_s"]

    # the hook is the first scene at "place_a" and is extended second
    hook_scene = _scene("s01", "hook", lines=[_episode_line("l01", "Hook.", 1.0)])
    hook_natural = timing.scene_timing(hook_scene, TEMPLATE, EN)
    assert result["scenes"]["s01"]["hold_s"] > hook_natural["hold_s"]

    # an untouched body scene keeps its own natural (scene-level) hold only
    body_scene = _scene("s02", "setup", lines=[_episode_line("l02", "Body.", 1.0)])
    body_natural = timing.scene_timing(body_scene, TEMPLATE, EN)
    assert result["scenes"]["s02"]["hold_s"] == body_natural["hold_s"]


def test_episode_timing_end_card_arithmetic_cut_to_black_vs_hard_stop():
    scenes = _ok_scenes(n_body=6)
    hard_stop = timing.episode_timing(_episode_script(scenes, cut_to_black=False), TEMPLATE, EN)
    cut_to_black = timing.episode_timing(_episode_script(scenes, cut_to_black=True), TEMPLATE, EN)
    expected_delta = TEMPLATE["end_card_s"] - TEMPLATE["transitions_s"]["fadeblack"]
    assert cut_to_black["total_s"] - hard_stop["total_s"] == pytest.approx(expected_delta, abs=1e-3)


def test_episode_timing_storyboard_transitions_override_the_prediction():
    scenes = [
        _scene("s01", "hook", place_id="place_a", lines=[_episode_line("l01", "Hook.", 2.5)]),
        _scene("s02", "setup", place_id="place_a", lines=[_episode_line("l02", "Body one.", 6.8)]),
        _scene("s03", "cliffhanger", place_id="place_b", lines=[_episode_line("l03", "Cliff.", 3.4)]),
    ]
    script = _episode_script(scenes, cut_to_black=False)

    predicted = timing.episode_timing(script, TEMPLATE, EN)
    # both boundaries would otherwise be dissolve/fadeblack (0.4 each); override
    # with cheaper 0.3 s transitions that will not change any scene's tail (its
    # natural tail, 0.6 or 1.2, is comfortably above 0.4 already either way)
    storyboard = _storyboard_from_scenes(scenes, transition_types={
        0: ("wipeleft", 0.3), 1: ("fadewhite", 0.3),
    })
    given = timing.episode_timing(script, TEMPLATE, EN, storyboard=storyboard)

    assert given["total_s"] != predicted["total_s"]
    predicted_overlap = 0.4 + 0.4  # dissolve (same place) + fadeblack (place change)
    given_overlap = 0.3 + 0.3
    assert given["total_s"] - predicted["total_s"] == pytest.approx(predicted_overlap - given_overlap, abs=1e-3)


def test_episode_timing_storyboard_boundary_edited_to_cut_only_shifts_that_boundary():
    scenes, script = _four_scene_two_place_script()
    predicted = timing.episode_timing(script, TEMPLATE, EN)  # dissolve, fadeblack, dissolve (0.4 each)
    storyboard = _storyboard_from_scenes(scenes, transition_types={
        0: ("cut", 0.0), 1: ("fadeblack", 0.4), 2: ("dissolve", 0.4),
    })
    result = timing.episode_timing(script, TEMPLATE, EN, storyboard=storyboard)
    # only boundary 0 lost its 0.4 s overlap; boundaries 1 and 2 are unchanged,
    # so every scene from s02 onward keeps its predicted alignment
    assert result["total_s"] - predicted["total_s"] == pytest.approx(0.4, abs=1e-3)
    for sid in ("s02", "s03", "s04"):
        assert result["scenes"][sid]["duration_s"] == pytest.approx(predicted["scenes"][sid]["duration_s"])


def test_episode_timing_storyboard_missing_boundary_entry_is_an_implicit_cut():
    scenes, script = _four_scene_two_place_script()
    explicit_cut = _storyboard_from_scenes(scenes, transition_types={
        0: ("cut", 0.0), 1: ("fadeblack", 0.4), 2: ("dissolve", 0.4),
    })
    missing_entry = _storyboard_from_scenes(scenes, transition_types={
        1: ("fadeblack", 0.4), 2: ("dissolve", 0.4),  # boundary 0 has no entry at all
    })
    a = timing.episode_timing(script, TEMPLATE, EN, storyboard=explicit_cut)
    b = timing.episode_timing(script, TEMPLATE, EN, storyboard=missing_entry)
    assert a["total_s"] == pytest.approx(b["total_s"], abs=1e-9)


def test_episode_timing_storyboard_non_cut_transition_inside_a_scene_raises():
    scenes, script = _four_scene_two_place_script()
    shots = [
        _shot("sh01", "s01", []),
        _shot("sh02", "s02", []),
        _shot("sh03", "s02", []),  # a second shot still inside s02
        _shot("sh04", "s03", []),
        _shot("sh05", "s04", []),
    ]
    transitions = [
        {"after": "sh01", "type": "dissolve", "duration_s": 0.4},
        {"after": "sh02", "type": "dissolve", "duration_s": 0.4},  # sh02 -> sh03: same scene, illegal
        {"after": "sh03", "type": "fadeblack", "duration_s": 0.4},
        {"after": "sh04", "type": "dissolve", "duration_s": 0.4},
    ]
    storyboard = {"shots": shots, "transitions": transitions}
    with pytest.raises(ValueError, match="only 'cut' is allowed inside a scene"):
        timing.episode_timing(script, TEMPLATE, EN, storyboard=storyboard)


def test_episode_timing_storyboard_scene_sequence_mismatch_raises():
    scenes, script = _four_scene_two_place_script()
    shots = [
        _shot("sh01", "s01", []),
        _shot("sh02", "s03", []),  # swapped: s03 shown before s02
        _shot("sh03", "s02", []),
        _shot("sh04", "s04", []),
    ]
    storyboard = {"shots": shots, "transitions": []}
    with pytest.raises(ValueError, match="does not match the script"):
        timing.episode_timing(script, TEMPLATE, EN, storyboard=storyboard)


def test_episode_timing_scene_over_and_trim_line_flags():
    huge_line = _episode_line("l02", "An overwhelmingly long line for this scene today.", 20.0)
    scenes = [
        _scene("s01", "hook", lines=[_episode_line("l01", "Hook.", 2.5)]),
        _scene("s02", "setup", lines=[huge_line]),
        _scene("s03", "cliffhanger", lines=[_episode_line("l03", "Cliff.", 3.4)]),
    ]
    script = _episode_script(scenes, cut_to_black=False)
    result = timing.episode_timing(script, TEMPLATE, EN)

    scene_over = [f for f in result["flags"] if f["kind"] == "scene_over" and f["scene_id"] == "s02"]
    assert len(scene_over) == 1
    trim_for_scene = [f for f in result["flags"] if f["kind"] == "trim_line" and f["scene_id"] == "s02"]
    assert len(trim_for_scene) == 1
    assert trim_for_scene[0]["line_id"] == "l02"
    assert result["scenes"]["s02"]["state"] == "over"


def test_episode_timing_estimated_and_measured_line_counts():
    measured = _episode_line("l01", "A measured line.", 2.0)
    estimated = _line("l02", "A never-measured line.")
    scenes = [
        _scene("s01", "hook", lines=[measured]),
        _scene("s02", "cliffhanger", lines=[estimated]),
    ]
    script = _episode_script(scenes, cut_to_black=False)
    result = timing.episode_timing(script, TEMPLATE, EN)
    assert result["measured_lines"] == 1
    assert result["estimated_lines"] == 1


# ======================================================== 10. line_offsets

def test_line_offsets_hand_case():
    scenes = [
        _scene("s01", "hook", place_id="place_a", lines=[_episode_line("l01", "Hook.", 2.5)]),
        _scene("s02", "setup", place_id="place_a", lines=[_episode_line("l02", "Body.", 6.8)]),
    ]
    script = _episode_script(scenes, cut_to_black=False)
    result = timing.episode_timing(script, TEMPLATE, EN)
    offsets = timing.line_offsets(script, result, TEMPLATE)

    l01_start, l01_end = offsets["l01"]
    assert l01_start == pytest.approx(0.35)
    assert l01_end == pytest.approx(0.35 + 2.5)

    scene1_duration = result["scenes"]["s01"]["duration_s"]
    boundary = timing._boundary_transitions(scenes, TEMPLATE, None)
    scene2_start = scene1_duration - boundary[0][1]
    l02_start, _l02_end = offsets["l02"]
    assert l02_start == pytest.approx(scene2_start + 0.35)


def test_line_offsets_with_a_storyboard_matches_its_edited_boundary():
    scenes = [
        _scene("s01", "hook", place_id="place_a", lines=[_episode_line("l01", "Hook.", 2.5)]),
        _scene("s02", "setup", place_id="place_a", lines=[_episode_line("l02", "Body.", 6.8)]),
    ]
    script = _episode_script(scenes, cut_to_black=False)
    storyboard = _storyboard_from_scenes(scenes, transition_types={0: ("cut", 0.0)})
    result = timing.episode_timing(script, TEMPLATE, EN, storyboard=storyboard)
    offsets = timing.line_offsets(script, result, TEMPLATE, storyboard=storyboard)

    scene1_duration = result["scenes"]["s01"]["duration_s"]
    l02_start, _l02_end = offsets["l02"]
    # a cut boundary has zero overlap, so scene 2 starts right where scene 1 ends
    assert l02_start == pytest.approx(scene1_duration + 0.35)


def _gen_script(rng, n_scenes=5):
    places = ["place_a", "place_b"]
    scenes = []
    for i in range(1, n_scenes + 1):
        function = "hook" if i == 1 else ("cliffhanger" if i == n_scenes else "setup")
        n_lines = rng.randint(0, 3)
        lines = [_timed_line(f"l{i:02d}{j}", "mot " * rng.randint(2, 8), round(rng.uniform(0.5, 3.0), 3))
                 for j in range(n_lines)]
        scenes.append(_scene(f"s{i:02d}", function, place_id=rng.choice(places), lines=lines))
    return _episode_script(scenes, cut_to_black=rng.choice([True, False]))


def test_line_offsets_never_overlap_and_never_precede_their_scene():
    rng = random.Random(20260927)
    for _ in range(150):
        script = _gen_script(rng)
        timing_result = timing.episode_timing(script, TEMPLATE, EN)
        offsets = timing.line_offsets(script, timing_result, TEMPLATE)

        intervals = sorted(offsets.values())
        for (start_a, end_a), (start_b, _end_b) in zip(intervals, intervals[1:]):
            assert end_a <= start_b + 1e-6

        scene_starts = {}
        running = 0.0
        boundary = timing._boundary_transitions(script["scenes"], TEMPLATE, None)
        for i, scene in enumerate(script["scenes"]):
            if i > 0:
                prev_sid = script["scenes"][i - 1]["scene_id"]
                running += timing_result["scenes"][prev_sid]["duration_s"] - boundary[i - 1][1]
            scene_starts[scene["scene_id"]] = running

        for scene in script["scenes"]:
            for line in scene["lines"]:
                start, _end = offsets[line["line_id"]]
                assert start >= scene_starts[scene["scene_id"]] - 1e-6


# ======================================================== 11. serial_60s_v2 (phase 7 stage 4, DEC-227)
#
# A v2 story's episode is 6-10 beat shots of 5-12 s (the hook 3-6 s), one
# seedance clip each: the template is data only (timing.py is unchanged), so
# these tests check that its slots, its hold cap and its window keep every
# shot a clip can cover (seedance sells 2-12 s, DEC-208), on any episode
# whose lines fit their slots.

V2_MAX_SHOT_S = 12


def _v2_template():
    return templates.load_episode_template("serial_60s_v2")


def test_v1_template_unchanged():
    """The v1 template file is not touched by the v2 one (RC-M2's render reads it)."""
    v1 = templates.load_episode_template("serial_60s_v1")
    assert (v1["window_s"], v1["target_s"], v1["tighten_above_s"], v1["scenes"], v1["shots"], v1["min_shot_s"],
            v1["default_body_count"], v1["hold_extension_max_s"], v1["end_card_s"]) == (
        [55, 80], 60, 75, [8, 12], [16, 30], 0.8, 8, 1.0, 1.0)
    assert {name: (slot["count"], slot["duration_s"]) for name, slot in v1["slots"].items()} == {
        "recap": ([1, 1], [2.0, 3.0]), "hook": ([1, 1], [1.5, 3.5]), "body": ([5, 9], [4.0, 8.0]),
        "cliffhanger": ([1, 1], [2.0, 5.0])}
    assert "shots_per_scene" not in v1 and "max_shot_s" not in v1


def _v2_episode(ep, *, body_lines=(3.5, 3.5), hook_line=3.0, cliff_line=4.0, places=("place_a", "place_b")):
    """Episode *ep* on serial_60s_v2's own slot list (timing.episode_slots):
    measured lines, the places alternating every two scenes."""
    template = _v2_template()
    scenes, n = [], 0
    for k, slot in enumerate(timing.episode_slots(template, ep)):
        n += 1
        sid = f"s{n:02d}"
        place = places[(k // 2) % len(places)]
        if slot == "body":
            function = ["setup", "rising", "peak", "turn"][(k - 1) % 4]
            durations = body_lines
        elif slot == "hook":
            function, durations = "hook", (hook_line,)
        elif slot == "recap":
            function, durations = "recap", (2.0,)
        else:
            function, durations = "cliffhanger", (cliff_line,)
        lines = [_episode_line(f"l{n:02d}{j}", "Une ligne de dialogue.", d) for j, d in enumerate(durations)]
        scenes.append(_scene(sid, function, place_id=place, lines=lines))
    return _episode_script(scenes, cut_to_black=True)


def test_v2_template_passes_its_schema_and_an_episode_of_6_to_10_shots_lands_in_55_75_s():
    template = _v2_template()
    assert schemas.episode_template_errors(template) == []
    # DEC-252 re-pin: two beat shots per body scene by default, so the template's shots range is 6-18 (7 body
    # scenes x 2, the recap, the hook and a cliffhanger past Veo's 8 s); the boards below stay one a scene.
    assert (template["window_s"], template["scenes"], template["shots"]) == ([55, 75], [6, 10], [6, 18])
    assert (template["shots_per_scene"], template["max_shot_s"], template["min_shot_s"]) == ([1, 2], 12, 3.0)
    assert template["slots"]["hook"]["duration_s"] == [3.0, 6.0]

    for ep in (1, 2):
        script = _v2_episode(ep)
        board = _storyboard_from_scenes(script["scenes"], transition_types={
            i: ("dissolve" if a["place_id"] == b["place_id"] else "fadeblack", 0.4)
            for i, (a, b) in enumerate(zip(script["scenes"], script["scenes"][1:]))})
        result, scene_t = timing.episode_pass(script, template, FR, storyboard=board)
        assert 6 <= len(board["shots"]) <= 10, ep
        assert result["state"] == "ok" and 55 <= result["total_s"] <= 75, (ep, result["total_s"])
        for scene in script["scenes"]:
            durations, extra = timing.allocate_shots(scene, scene_t[scene["scene_id"]], [{"lines": []}], template)
            assert extra == 0.0 and durations[0] <= V2_MAX_SHOT_S


def test_v2_scene_plus_hold_never_exceeds_12s():
    """Whatever an episode's lines (as long as each scene's lines fit its
    slot -- a scene the writer overfills is flagged ``over`` and its clip held,
    DEC-208), a v2 scene's one shot, its held tail and the window pass's hold
    extension included, never runs past 12 s; a hook lasts 3-6 s. Short
    episodes (held to reach the window) and long ones (tightened) alike, with
    and without the family_3d clamp (peak/tender up to 12 s)."""
    template = _v2_template()
    rng = random.Random(20261001)
    checked = 0
    for _ in range(300):
        ep = rng.choice((1, 2))
        script = _v2_episode(
            ep, body_lines=tuple(round(rng.uniform(0.6, 4.6), 3) for _ in range(rng.randint(1, 3))),
            hook_line=round(rng.uniform(0.6, 4.4), 3), cliff_line=round(rng.uniform(0.6, 7.0), 3),
            places=rng.choice((("place_a",), ("place_a", "place_b"), ("place_a", "place_b", "place_c"))))
        board = _storyboard_from_scenes(script["scenes"])
        style_lock = rng.choice((None, FAMILY_3D_STYLE_LOCK))
        _result, scene_t = timing.episode_pass(script, template, EN, style_lock=style_lock, storyboard=board)
        for scene in script["scenes"]:
            timed = scene_t[scene["scene_id"]]
            if timed["state"] == "over":
                continue  # the writer overfilled it: flagged, its clip is held (DEC-208)
            (shot,), extra = timing.allocate_shots(scene, timed, [{"lines": [scene["lines"][0]["line_id"]]}],
                                                   template)
            assert extra == 0.0
            assert shot == timed["duration_s"] <= V2_MAX_SHOT_S, (scene["function"], timed)
            if scene["function"] == "hook":
                assert 3.0 <= shot <= 6.0, timed
            checked += 1
    assert checked > 2000
