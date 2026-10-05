"""Plan 24 stage 1: the one speech clock and the per-line plan (D-1, D-2, D-5).

``timing.seconds_for`` is the estimate at the speaking voice's overrun (Gemini
1.35, Edge 1.0) and, with no provider, exactly ``timing.estimate_line`` as it
always was. ``timing.scene_plan`` splits a scene's slot into one slot a line,
pays every pause and a 5 % margin, and turns each line slot into a hard word
cap -- so a scene written at its caps never comes out "over" on the Script
step's own timing, and on a native-speech story its clips sum inside the slot.
"""

from __future__ import annotations

import copy
import math
import types

import pytest

import test_story_episode_schemas as tes
from clipping.aistory import media_policy, schemas, templates, timing, voices
from clipping.aistory.steps import script as script_step
from clipping.aistory.steps import storyboard as storyboard_step

FR = "fr"
NARRATED = templates.load_episode_template("narrated_drama_60s_v2")
CONFRONTATION = templates.load_episode_template("confrontation_50s_v2")
# e7412a3efcc6's profile: v2, tier 3, the native_speech_manual budget profile.
NATIVE_STORY = {"generation_profile": {"tier": 3, "route": "api", "consistency_mode": "references",
                                       "budget_profile": "native_speech_manual", "pipeline": "v2",
                                       "mode": "agent", "writing": "v3"}}


def _scene(function, *, characters=("char_rida",), scene_id="s02", lines=None):
    return {
        "scene_id": scene_id, "function": function, "place_id": "place_a", "time_variant": "day",
        "characters": list(characters), "props": [], "summary": "Something happens.", "emotion": "neutral",
        "target_duration_s": 10.0, "lines": lines or [], "sfx_cues": [], "on_screen_text": None,
        "state": "stub", "source": "E1", "rev": 1,
    }


def _text_at(words):
    """A text of *words* words whose length is the v3 average (6.6 a word,
    the space included), rounded up -- a reply exactly at its cap."""
    length = math.ceil(words * timing.CHARS_PER_WORD_V3[FR])
    letters = length - (words - 1)
    sizes = [letters // words + (1 if k < letters % words else 0) for k in range(words)]
    text = " ".join("e" * size for size in sizes)
    assert len(text) == length and len(text.split()) == words
    return text


def _written(scene, plan, providers):
    """*scene* written exactly at its plan's caps, each line estimated at its
    voice's overrun (what the Script step stores)."""
    lines = []
    for k, planned in enumerate(plan["lines"]):
        text = _text_at(planned["max_words"])
        lines.append({"line_id": schemas.line_id_for(scene["scene_id"], k), "speaker": planned["speaker"],
                      "text": text, "emotion": "neutral", "delivery": "calm",
                      "timing": timing.estimated_timing(text, FR, provider=providers.get(planned["speaker"]))})
    return dict(scene, lines=lines, state="written", source="E2")


# ------------------------------------------------- (a) every pause is paid

@pytest.mark.parametrize("native", [False, True])
def test_a_13s_body_scene_at_its_caps_pays_every_pause_and_never_flags(native):
    scene = _scene("setup")
    floor = timing.plan_tail_floor(NARRATED)
    providers = {"narrator": "edge", "char_rida": "gemini"}
    plan = timing.scene_plan(NARRATED, scene, lang=FR, native=native, narrator_provider="edge",
                             speakers={"char_rida": "gemini"}, tail_floor=floor)
    assert plan["slot_s"] == [9.0, 13.0]
    assert [line["kind"] for line in plan["lines"]] == ["narrator", "character"]
    pauses = NARRATED["pauses_s"]
    # On a native story the character line is spoken by its clip: no TTS overrun.
    spoken_by = {"narrator": "edge", "char_rida": None if native else "gemini"}
    speech = sum(timing.seconds_for(_text_at(line["max_words"]), FR, provider=spoken_by[line["speaker"]])
                 for line in plan["lines"])
    assert pauses["before_first_line"] + pauses["between_lines"] + speech + floor <= 13.0
    written = _written(scene, plan, {"narrator": "edge", "char_rida": None if native else providers["char_rida"]})
    timed = timing.scene_timing(written, NARRATED, FR, tail_floor=floor)
    assert timed["state"] != "over" and timed["duration_s"] <= 13.0
    assert plan["max_words"] == sum(line["max_words"] for line in plan["lines"])
    assert plan["min_words"] == plan["max_words"] // 2
    if native:
        assert sum(line["clip_s"] for line in plan["lines"]) <= 13


# ------------------------------------------------- (b) the hook is feasible

@pytest.mark.parametrize("native", [False, True])
def test_the_6s_hook_holds_at_most_11_words_in_french_with_an_edge_narrator(native):
    plan = timing.scene_plan(NARRATED, _scene("hook", scene_id="s01"), lang=FR, native=native,
                             narrator_provider="edge", speakers={"char_rida": "edge"})
    assert plan["slot_s"] == [3.0, 6.0]
    assert [line["speaker"] for line in plan["lines"]] == ["narrator"]
    assert plan["max_words"] <= 11


# ------------------------------------------------- (c) native snapping

def test_a_native_character_line_of_6_4_s_snaps_to_a_6s_clip_of_12_words():
    template = copy.deepcopy(CONFRONTATION)  # every boundary a cut: the floor is the template's 0.3
    # (hi - 0.35 - 0.3) x 0.95 = 6.4125 s for the one line.
    template["slots"]["hook"]["duration_s"] = [3.0, 7.4]
    plan = timing.scene_plan(template, _scene("hook", scene_id="s01"), lang=FR, native=True,
                             narrator=False, speakers={"char_rida": None})
    (line,) = plan["lines"]
    assert 6.4 <= plan["allowed_speech_s"] < 6.5
    assert (line["kind"], line["clip_s"], line["max_words"]) == ("character", 6, 12)
    assert line["clip_s"] <= plan["slot_s"][1]


# Plan 24 stage 2 (2026-10-05), expectations moved on purpose: stage 1 split a
# native scene at the middle of the narrator share, which left the character
# line a 4 s clip of 7 words -- too tight for a complete line with its reason.
# The character line is now planned first at its 6 s clip (12 words) and the
# narrator takes the seconds left; off native, the narrator gets the LOW end
# of the template's share (0.6), not its middle.

def test_a_native_13s_body_scene_plans_the_characters_6s_clip_first_and_the_narrator_the_rest():
    plan = timing.scene_plan(NARRATED, _scene("setup"), lang=FR, native=True, narrator_provider="edge",
                             speakers={"char_rida": "gemini"}, tail_floor=timing.plan_tail_floor(NARRATED))
    narrator, character = plan["lines"]
    assert (character["kind"], character["clip_s"], character["max_words"]) == ("character", 6, 12)
    # 11.4 s allowed - 6 s = 5.4 s, but the clips must sum inside 13 s: the narrator's
    # clip is 6 s (not the 8 s 5.4 + 0.7 snaps up to), so it speaks 5.3 s: 11 words at Edge.
    assert (narrator["kind"], narrator["clip_s"], narrator["seconds"], narrator["max_words"]) == (
        "narrator", 6, 5.3, 11)
    assert plan["max_words"] == 23 and plan["min_words"] == 11
    assert narrator["clip_s"] + character["clip_s"] <= plan["slot_s"][1]


def test_a_native_9s_body_scene_steps_the_character_clip_down_to_4s():
    template = copy.deepcopy(NARRATED)
    template["slots"]["body"]["duration_s"] = [6.0, 9.0]
    plan = timing.scene_plan(template, _scene("setup"), lang=FR, native=True, narrator_provider="edge",
                             speakers={"char_rida": None}, tail_floor=timing.plan_tail_floor(template))
    narrator, character = plan["lines"]
    assert character["clip_s"] == 4 and narrator["clip_s"] == 4  # 6 + 4 > 9: the character steps down
    assert sum(line["clip_s"] for line in plan["lines"]) <= 9


def test_off_native_the_narrator_gets_the_low_end_of_the_share():
    plan = timing.scene_plan(NARRATED, _scene("setup"), lang=FR, native=False, narrator_provider="edge",
                             speakers={"char_rida": "edge"}, tail_floor=timing.plan_tail_floor(NARRATED))
    narrator, character = plan["lines"]
    assert NARRATED["narrator_share"][0] == 0.6
    assert narrator["seconds"] == round(plan["allowed_speech_s"] * 0.6, 3)
    assert character["seconds"] == round(plan["allowed_speech_s"] * 0.4, 3)


@pytest.mark.parametrize("template,function,narrator", [
    (NARRATED, "turn", True), (NARRATED, "cliffhanger", True), (CONFRONTATION, "rising", False),
])
def test_native_clips_sum_inside_the_slot(template, function, narrator):
    scene = _scene(function, characters=("char_rida", "char_marie"))
    for hi in (9.0, 11.0, 13.0, 16.0):
        tight = copy.deepcopy(template)
        slot = timing.slot_name(function, tight)
        tight["slots"][slot]["duration_s"] = [3.0, hi]
        plan = timing.scene_plan(tight, scene, lang=FR, native=True, narrator=narrator,
                                 narrator_provider="edge" if narrator else None,
                                 speakers={"char_rida": None, "char_marie": None})
        assert sum(line["clip_s"] for line in plan["lines"]) <= hi, (function, hi, plan)
        for line in plan["lines"]:
            if line["kind"] == "character":
                assert line["max_words"] <= timing._clip_capacity(line["clip_s"])


# ------------------------------------------------- (d) the one clock

def test_seconds_for_applies_the_gemini_overrun_and_is_estimate_line_without_one():
    text = "Marie-Jeanne veut dominer ce contrat tandis que Rida compte séduire le client."
    assert timing.seconds_for(text, FR) == timing.estimate_line(text, FR)
    assert timing.seconds_for(text, FR, provider="edge") == timing.estimate_line(text, FR)
    assert timing.seconds_for(text, FR, provider="gemini") == round(1.35 * timing.estimate_line(text, FR), 3)
    assert timing.seconds_for(text, FR, provider="gemini", factor=1.1) == round(1.1 * timing.estimate_line(text, FR), 3)
    assert voices.SPEECH_OVERRUN is timing.SPEECH_OVERRUN


def test_an_edge_or_unvoiced_estimate_block_is_byte_identical_and_a_gemini_one_records_its_factor():
    legacy = {"source": "estimated", "duration_s": timing.estimate_line("Bonjour", FR),
              "text_hash": timing.text_hash("Bonjour"), "voice": None, "audio": None}
    assert timing.estimated_timing("Bonjour", FR) == legacy
    assert timing.estimated_timing("Bonjour", FR, provider="edge") == legacy
    gemini = timing.estimated_timing("Une réplique assez longue pour dépasser le plancher.", FR, provider="gemini")
    assert gemini["speech_factor"] == 1.35
    line = {"text": "Une réplique assez longue pour dépasser le plancher.", "timing": gemini}
    assert timing.estimate_carries_overrun(line)
    assert not timing.estimate_carries_overrun(dict(line, text="Autre texte."))


def _ec(story=None, *, narrator_provider="edge", character_provider="gemini", template=NARRATED):
    return types.SimpleNamespace(
        language=FR, story=story or {}, template=template, style_lock=None, narrator=True,
        entities={"characters": {"char_rida": {"voice": {"provider": character_provider}}}},
    )


def test_the_script_step_estimates_each_line_at_its_voice_overrun():
    reply_line = {"speaker": "char_rida", "text": "Laisse-moi charmer ce client, car mon bagout scellera tout.",
                  "emotion": "neutral", "delivery": "calm"}
    gemini = script_step._line(_ec(), "s02", 1, reply_line)
    assert gemini["timing"]["duration_s"] == timing.seconds_for(reply_line["text"], FR, provider="gemini")
    edge = script_step._line(_ec(character_provider="edge"), "s02", 1, reply_line)
    assert edge["timing"] == timing.estimated_timing(reply_line["text"], FR)


def test_a_native_character_line_is_estimated_at_no_overrun(monkeypatch):
    monkeypatch.setattr(media_policy, "native_speech", lambda story: True)
    reply_line = {"speaker": "char_rida", "text": "Laisse-moi charmer ce client.", "emotion": "neutral",
                  "delivery": "calm"}
    line = script_step._line(_ec(), "s02", 1, reply_line)
    assert line["timing"] == timing.estimated_timing(reply_line["text"], FR)


def test_expected_scene_seconds_never_adds_the_overrun_twice():
    text = "Laisse-moi charmer ce client, car mon bagout scellera notre victoire."
    scene = _scene("setup", lines=[{"line_id": "l08", "speaker": "char_rida", "text": text, "emotion": "neutral",
                                    "delivery": "calm",
                                    "timing": timing.estimated_timing(text, FR, provider="gemini")}])
    script = {"scenes": [scene], "timing": None}
    ec = _ec()
    assert storyboard_step.expected_scene_seconds(ec, script, scene) == storyboard_step.scene_seconds(
        ec, script, scene)


# ------------------------------------------------- (e) legacy scripts untouched

def test_a_scene_validates_with_and_without_its_plan():
    doc = tes._script()
    assert schemas.episode_script_errors(doc) == []
    planned = copy.deepcopy(doc)
    plan = timing.scene_plan(NARRATED, planned["scenes"][1], lang=FR, native=True, narrator_provider="edge",
                             speakers={"char_mangella": None})
    planned["scenes"][1]["slot_s"] = plan["slot_s"]
    planned["scenes"][1]["line_plan"] = {key: plan[key]
                                         for key in ("allowed_speech_s", "lines", "max_words", "min_words")}
    planned["scenes"][1]["lines"][0]["timing"] = timing.estimated_timing("Hello there.", "en", provider="gemini")
    assert schemas.episode_script_errors(planned) == []


def test_episode_pass_of_an_edge_voiced_script_is_unchanged():
    template = templates.load_episode_template("serial_60s_v1")
    doc = tes._script()
    edge = copy.deepcopy(doc)
    for scene in doc["scenes"]:
        for line in scene["lines"]:
            line["timing"] = None  # the legacy fresh estimate
    for scene in edge["scenes"]:
        for line in scene["lines"]:
            line["timing"] = timing.estimated_timing(line["text"], "en", provider="edge")
    assert timing.episode_pass(edge, template, "en") == timing.episode_pass(doc, template, "en")


def test_store_line_plans_writes_a_valid_plan_on_every_scene(monkeypatch):
    monkeypatch.setattr(media_policy, "native_speech", lambda story: True)
    monkeypatch.setattr(script_step, "_speech_lengths", lambda ec: ((4, 6, 8), (4, 6, 8)))
    scenes = [_scene("hook", scene_id="s01"), _scene("setup", scene_id="s02"),
              _scene("cliffhanger", scene_id="s03", characters=("char_rida", "char_marie"))]
    script = {"scenes": scenes, "cliffhanger": {"cut_to_black": True}}
    script_step.store_line_plans(_ec(NATIVE_STORY), script)
    for scene in scenes:
        assert scene["slot_s"] == list(timing.slot_range(scene, NARRATED))
        assert schemas.validate(scene["line_plan"], schemas._EPISODE_SCRIPT_LINE_PLAN_SCHEMA) == []
    assert scenes[0]["line_plan"]["max_words"] <= 11
    assert timing.plan_budget(scenes[1]["line_plan"], line_lo=5)["words"][1] == scenes[1]["line_plan"]["max_words"]


# ------------------------------------------------- plan 24 stage 5 (D-6): which scenes carry a character line

def _assigned(function, flag, **kw):
    scene = _scene(function, **kw)
    if flag is not None:
        scene["character_line"] = flag
    return scene


def _plan_of(scene, *, native, template=NARRATED, narrator=True):
    return timing.scene_plan(template, scene, lang=FR, native=native, narrator_provider="edge" if narrator else None,
                             narrator=narrator, speakers={"char_rida": "gemini"},
                             tail_floor=timing.plan_tail_floor(template))


def test_a_narrator_only_body_scene_plans_one_narrator_line_taking_the_whole_allowed_speech():
    plan = _plan_of(_assigned("setup", False), native=False)
    (narrator,) = plan["lines"]
    assert narrator["kind"] == "narrator" and narrator["seconds"] == plan["allowed_speech_s"] == 11.637
    # The whole allowed speech at Edge French: 25 words where the two-line split held 14 + 7.
    assert narrator["max_words"] == timing.words_for_seconds(plan["allowed_speech_s"], FR, provider="edge") == 25
    assert plan["max_words"] == 25 and plan["min_words"] == 12


def test_a_native_narrator_only_scene_snaps_its_clip_up_within_the_slot():
    plan = _plan_of(_assigned("setup", False), native=True)
    (narrator,) = plan["lines"]
    # One silent clip holds the narration (at most the longest, 8 s, less its 0.7 s lead): 7.3 s, 15 words.
    assert (narrator["kind"], narrator["clip_s"], narrator["seconds"], narrator["max_words"]) == (
        "narrator", 8, 7.3, 15)
    assert narrator["clip_s"] <= plan["slot_s"][1]
    # The two-line plan of the same scene is unchanged: the character's clip first, the narrator the rest.
    both = _plan_of(_assigned("setup", True), native=True)
    assert [(line["kind"], line["clip_s"], line["max_words"]) for line in both["lines"]] == [
        ("narrator", 6, 11), ("character", 6, 12)]
    assert both == _plan_of(_assigned("setup", None), native=True)  # no assignment: as stage 2 planned it


def test_the_assignment_only_moves_a_body_scene_with_the_narrator_on():
    hook = _plan_of(_assigned("hook", False, scene_id="s01"), native=True)
    assert hook == _plan_of(_assigned("hook", None, scene_id="s01"), native=True)
    # The narrator off: the exchange is the characters' whatever the key says (a stale key is ignored).
    off = _plan_of(_assigned("setup", False), native=True, narrator=False)
    assert [line["kind"] for line in off["lines"]] == ["character", "character"]
    # The confrontation never carries the key; a stray one changes nothing there (no narrator slot in the body).
    conf = _plan_of(_assigned("rising", False), native=True, template=CONFRONTATION, narrator=False)
    assert conf == _plan_of(_assigned("rising", None), native=True, template=CONFRONTATION, narrator=False)


def test_the_planned_narrator_share_is_a_number_over_the_planned_caps_and_only_for_a_narrated_template():
    scenes = []
    for sid, function, flag in (("s01", "hook", None), ("s02", "setup", False), ("s03", "rising", True),
                                ("s04", "cliffhanger", None)):
        scene = _assigned(function, flag, scene_id=sid)
        plan = _plan_of(scene, native=True)
        scene["slot_s"], scene["line_plan"] = plan["slot_s"], {
            key: plan[key] for key in ("allowed_speech_s", "lines", "max_words", "min_words")}
        scenes.append(scene)
    caps = [(line["kind"], line["max_words"]) for scene in scenes for line in scene["line_plan"]["lines"]]
    narrator = sum(words for kind, words in caps if kind == "narrator")
    share = timing.plan_narrator_share({"scenes": scenes}, NARRATED)
    assert share == round(narrator / sum(words for _kind, words in caps), 3)
    assert NARRATED["narrator_share"][0] <= share <= NARRATED["narrator_share"][1]
    assert timing.plan_narrator_share({"scenes": scenes}, CONFRONTATION) is None
    assert timing.plan_narrator_share({"scenes": [_scene("setup")]}, NARRATED) is None  # no plan stored


def test_retime_stores_the_planned_narrator_share_for_a_narrated_script_only():
    from clipping.aistory.steps import episode_common

    ec = _ec(template=NARRATED)
    scenes = []
    for sid, function, flag in (("s01", "hook", None), ("s02", "setup", False), ("s03", "cliffhanger", None)):
        scene = _assigned(function, flag, scene_id=sid)
        plan = _plan_of(scene, native=False)
        scene["slot_s"], scene["line_plan"] = plan["slot_s"], {
            key: plan[key] for key in ("allowed_speech_s", "lines", "max_words", "min_words")}
        scenes.append(_written(scene, plan, {"narrator": "edge"}))
    script = {"scenes": scenes, "cliffhanger": {"scene_id": "s03", "reveal": None, "cut_to_black": True},
              "timing": None}
    episode_common.retime(script, ec)
    # Every planned line is the narrator's here: the share is 1.0, a number in the stored timing.
    assert script["timing"]["narrator_share"] == timing.plan_narrator_share(script, NARRATED) == 1.0
    assert schemas.validate(script["timing"], schemas._EPISODE_SCRIPT_TIMING_SCHEMA) == []
    # A script with no stored plan carries no such key.
    bare = {"scenes": [_written(_scene("setup"), plan, {"narrator": "edge"})],
            "cliffhanger": {"scene_id": None, "reveal": None, "cut_to_black": True}, "timing": None}
    assert "narrator_share" not in episode_common.retime(bare, ec)["timing"]
