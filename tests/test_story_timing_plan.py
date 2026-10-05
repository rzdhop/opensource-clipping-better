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

# Plan 27 stage 1 (2026-10-05) moved the narrated body slot to 10-16 s and the
# hook to 5-8 s; stage 2 re-pinned these tests on those slots (the invariant
# is the same: a scene written at its caps never runs over its slot).

@pytest.mark.parametrize("native", [False, True])
def test_a_16s_body_scene_at_its_caps_pays_every_pause_and_never_flags(native):
    scene = _scene("setup")
    floor = timing.plan_tail_floor(NARRATED)
    providers = {"narrator": "edge", "char_rida": "gemini"}
    plan = timing.scene_plan(NARRATED, scene, lang=FR, native=native, narrator_provider="edge",
                             speakers={"char_rida": "gemini"}, tail_floor=floor)
    assert plan["slot_s"] == [10.0, 16.0]
    assert [line["kind"] for line in plan["lines"]] == ["narrator", "character"]
    pauses = NARRATED["pauses_s"]
    # On a native story the character line is spoken by its clip: no TTS overrun.
    spoken_by = {"narrator": "edge", "char_rida": None if native else "gemini"}
    speech = sum(timing.seconds_for(_text_at(line["max_words"]), FR, provider=spoken_by[line["speaker"]])
                 for line in plan["lines"])
    assert pauses["before_first_line"] + pauses["between_lines"] + speech + floor <= 16.0
    written = _written(scene, plan, {"narrator": "edge", "char_rida": None if native else providers["char_rida"]})
    timed = timing.scene_timing(written, NARRATED, FR, tail_floor=floor)
    assert timed["state"] != "over" and timed["duration_s"] <= 16.0
    assert plan["max_words"] == sum(line["max_words"] for line in plan["lines"])
    if native:
        # Plan 27 stage 2: each line its own shot here, the scene's floor at least the lines' floors.
        assert sum(shot["clip_s"] for shot in plan["shots"]) <= 16
        assert plan["min_words"] == max(plan["max_words"] // 2, sum(line["min_words"] for line in plan["lines"]))
    else:
        assert plan["min_words"] == plan["max_words"] // 2


# ------------------------------------------------- (b) the hook is feasible

@pytest.mark.parametrize("native", [False, True])
def test_the_8s_hook_holds_at_most_14_words_in_french_with_an_edge_narrator(native):
    plan = timing.scene_plan(NARRATED, _scene("hook", scene_id="s01"), lang=FR, native=native,
                             narrator_provider="edge", speakers={"char_rida": "edge"})
    assert plan["slot_s"] == [5.0, 8.0]
    assert [line["speaker"] for line in plan["lines"]] == ["narrator"]
    assert plan["max_words"] <= 14


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

# Plan 27 stage 2 (2026-10-05), moved on purpose: the character's default shot
# is 8 s (17 words, PLAN_CHARACTER_CLIP_S), stepped down while the slot cannot
# hold it beside the narrator's shortest clip; the plan carries one shot a line
# and each line its floor (a character line 0.75 of its clip, the narrator 3).

def test_a_native_16s_body_scene_plans_the_characters_8s_clip_first_and_the_narrator_the_rest():
    plan = timing.scene_plan(NARRATED, _scene("setup"), lang=FR, native=True, narrator_provider="edge",
                             speakers={"char_rida": "gemini"}, tail_floor=timing.plan_tail_floor(NARRATED))
    narrator, character = plan["lines"]
    assert (character["kind"], character["clip_s"], character["min_words"], character["max_words"]) == (
        "character", 8, 13, 17)
    # 14.25 s allowed - 8 s = 6.25 s of narration in an 8 s clip: 13 words at Edge.
    assert (narrator["kind"], narrator["clip_s"], narrator["seconds"], narrator["max_words"]) == (
        "narrator", 8, 6.25, 13)
    assert plan["max_words"] == 30 and plan["min_words"] == 16
    assert [(shot["clip_s"], shot["line_ids"], shot["speaks"]) for shot in plan["shots"]] == [
        (8, ["l08"], False), (8, ["l09"], True)]
    assert narrator["clip_s"] + character["clip_s"] <= plan["slot_s"][1]


def test_a_native_13s_body_scene_steps_the_character_down_to_6s_beside_the_narrator():
    template = copy.deepcopy(NARRATED)
    template["slots"]["body"]["duration_s"] = [9.0, 13.0]
    plan = timing.scene_plan(template, _scene("setup"), lang=FR, native=True, narrator_provider="edge",
                             speakers={"char_rida": "gemini"}, tail_floor=timing.plan_tail_floor(template))
    narrator, character = plan["lines"]
    # 8 + 6 > 13: the character steps down to 6 s (12 words, floor 9); the narrator speaks 5.3 s of its 6 s clip.
    assert (character["clip_s"], character["min_words"], character["max_words"]) == (6, 9, 12)
    assert (narrator["clip_s"], narrator["seconds"], narrator["max_words"]) == (6, 5.3, 11)
    assert plan["max_words"] == 23 and plan["min_words"] == 12


def test_a_native_9s_body_scene_steps_the_character_clip_down_to_4s():
    template = copy.deepcopy(NARRATED)
    template["slots"]["body"]["duration_s"] = [6.0, 9.0]
    plan = timing.scene_plan(template, _scene("setup"), lang=FR, native=True, narrator_provider="edge",
                             speakers={"char_rida": None}, tail_floor=timing.plan_tail_floor(template),
                             speech_lengths=(4, 6, 8))  # a table with 4 s (plan 27 dropped it from Veo's)
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
    # Plan 27 stage 2: a shot's clip counted once (an exchange's lines share it); the 6/8 s
    # table from 12 s up (two clips of 6 s at least), a table with 4 s below.
    for hi, lengths in ((9.0, (4, 6, 8)), (11.0, (4, 6, 8)), (12.0, (6, 8)), (13.0, (6, 8)), (16.0, (6, 8)),
                        (16.0, (8,)), (16.0, (5, 10))):
        tight = copy.deepcopy(template)
        slot = timing.slot_name(function, tight)
        tight["slots"][slot]["duration_s"] = [3.0, hi]
        plan = timing.scene_plan(tight, scene, lang=FR, native=True, narrator=narrator,
                                 narrator_provider="edge" if narrator else None,
                                 speakers={"char_rida": None, "char_marie": None}, speech_lengths=lengths,
                                 silent_lengths=lengths)
        assert sum(shot["clip_s"] for shot in plan["shots"]) <= hi, (function, hi, plan)
        for shot in plan["shots"]:
            if shot["speaks"]:
                assert shot["words_max"] <= timing._clip_capacity(shot["clip_s"])
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
    # Plan 27 stage 2: the shots and each line's floor are optional keys of the stored plan.
    planned["scenes"][1]["line_plan"]["shots"] = plan["shots"]
    assert "min_words" in plan["lines"][0] and schemas.episode_script_errors(planned) == []
    planned["scenes"][1]["line_plan"]["shots"] = [dict(plan["shots"][0], line_ids=["8"])]
    assert schemas.episode_script_errors(planned) != []


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
    assert scenes[0]["line_plan"]["max_words"] <= 14  # plan 27: the 5-8 s hook
    assert all("shots" in scene["line_plan"] for scene in scenes)  # plan 27 stage 2: stored with the plan
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
    # Plan 27 stage 1's 10-16 s body slot: 14.487 s, 31 words at Edge French.
    assert narrator["kind"] == "narrator" and narrator["seconds"] == plan["allowed_speech_s"] == 14.487
    assert narrator["max_words"] == timing.words_for_seconds(plan["allowed_speech_s"], FR, provider="edge") == 31
    assert plan["max_words"] == 31 and plan["min_words"] == 15


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
        ("narrator", 8, 13), ("character", 8, 17)]
    assert both == _plan_of(_assigned("setup", None), native=True)  # no assignment: as stage 2 planned it


def test_the_assignment_only_moves_a_body_scene_with_the_narrator_on():
    hook = _plan_of(_assigned("hook", False, scene_id="s01"), native=True)
    assert hook == _plan_of(_assigned("hook", None, scene_id="s01"), native=True)
    # The narrator off: the exchange is the characters' whatever the key says (a stale key is ignored).
    off = _plan_of(_assigned("setup", False), native=True, narrator=False)
    # Plan 27 stage 2: one character, two exchanges of at most two of its lines each.
    assert [line["kind"] for line in off["lines"]] == ["character"] * 4
    assert [len(shot["line_ids"]) for shot in off["shots"]] == [2, 2]
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


# ------------------------------------------------- plan 27 stage 2: exchanges sized to the shot
#
# The human (2026-10-05): "I don't want only one dialogue line per clip -- more
# story, more lines per shot; see how many fit in 5 s or 10 s". A native-speech
# scene with no narrator groups its character lines into shots of the link's
# lengths, each holding [ceil(0.75 x words(L)), words(L)] words (words(L) =
# floor((L - 0.7) x 2.4): 6 s 12, 8 s 17, 10 s 22) over 1-4 lines in turn.

def _two(function="rising", template=CONFRONTATION, *, characters=("char_rida", "char_marie"), lengths=(6, 8),
         hi=None):
    if hi is not None:
        template = copy.deepcopy(template)
        template["slots"][timing.slot_name(function, template)]["duration_s"] = [3.0, hi]
    return timing.scene_plan(template, _scene(function, characters=characters), lang=FR, native=True,
                             narrator=False, speakers={cid: None for cid in characters},
                             tail_floor=timing.plan_tail_floor(template), speech_lengths=lengths)


def _shape(plan):
    return [(shot["clip_s"], len(shot["line_ids"]), shot["words_min"], shot["words_max"]) for shot in plan["shots"]]


def test_a_two_speaker_body_scene_on_veo_plans_two_exchanges_the_speakers_in_turn():
    plan = _two()
    assert plan["slot_s"] == [10.0, 16.0]
    # 8 s first (17 words), then the longest the slot and the estimate still pay: 6 s (12). Four
    # lines at most a scene: two each, never a shot of one line where two fit.
    assert _shape(plan) == [(8, 2, 13, 17), (6, 2, 9, 12)]
    assert [shot["line_ids"] for shot in plan["shots"]] == [["l08", "l09"], ["l10", "l11"]]
    assert [line["speaker"] for line in plan["lines"]] == ["char_rida", "char_marie", "char_rida", "char_marie"]
    assert [(line["min_words"], line["max_words"]) for line in plan["lines"]] == [(6, 10), (7, 11), (4, 7), (5, 8)]
    assert [line["clip_s"] for line in plan["lines"]] == [8, 8, 6, 6]
    for shot, lines in ((plan["shots"][0], plan["lines"][:2]), (plan["shots"][1], plan["lines"][2:])):
        assert sum(line["min_words"] for line in lines) == shot["words_min"]  # the floor split, last the longest
        assert all(line["min_words"] >= timing.PLAN_LINE_MIN_WORDS for line in lines)
        assert sum(line["seconds"] for line in lines) == pytest.approx(shot["clip_s"], abs=0.002)
    assert plan["max_words"] == 29 and plan["min_words"] == 22
    assert sum(shot["clip_s"] for shot in plan["shots"]) <= 16
    # Plan 24's rule kept: written at its caps the scene never runs over its slot on the Script step's clock.
    # Each exchange written at its shot's words (8 + 9, 6 + 6):
    at_caps = [dict(line, max_words=words) for line, words in zip(plan["lines"], (8, 9, 6, 6))]
    written = _written(_scene("rising", characters=("char_rida", "char_marie")), dict(plan, lines=at_caps), {})
    timed = timing.scene_timing(written, CONFRONTATION, FR, tail_floor=timing.plan_tail_floor(CONFRONTATION))
    assert timed["state"] != "over" and timed["duration_s"] <= 16.0


def test_a_12s_slot_holds_one_8s_exchange_of_three_lines_and_flow_one_of_three():
    assert _shape(_two(hi=12.0)) == [(8, 3, 13, 17)]
    assert [(line["min_words"], line["max_words"]) for line in _two(hi=12.0)["lines"]] == [(4, 8), (4, 8), (5, 9)]
    # Flow sells 8 s only: the 16 s slot cannot pay a second 17-word exchange on the estimate.
    assert _shape(_two(lengths=(8,))) == [(8, 3, 13, 17)]


def test_a_one_speaker_scene_has_at_most_two_lines_a_shot():
    plan = _two(characters=("char_rida",))
    assert all(len(shot["line_ids"]) <= timing.PLAN_ONE_SPEAKER_LINES for shot in plan["shots"])
    assert {line["speaker"] for line in plan["lines"]} == {"char_rida"}
    assert _shape(_two(characters=("char_rida",), hi=12.0)) == [(8, 2, 13, 17)]


def test_a_10s_link_plans_a_22_word_shot_of_four_lines():
    plan = _two(lengths=(5, 10))
    assert _shape(plan) == [(10, 4, 17, 22)]
    assert [(line["min_words"], line["max_words"]) for line in plan["lines"]] == [(4, 9), (4, 9), (4, 9), (5, 10)]
    assert [line["speaker"] for line in plan["lines"]] == ["char_rida", "char_marie"] * 2


def test_the_framing_scenes_keep_their_one_line_sized_to_the_shot():
    # The 8 s hook: the estimate pays 15 of the clip's 17 words -- still more than a 6 s clip's 12.
    hook = _two("hook")
    assert hook["slot_s"] == [5.0, 8.0] and _shape(hook) == [(8, 1, 13, 15)]
    cliff = _two("cliffhanger")
    assert _shape(cliff) == [(8, 1, 13, 17)] and cliff["lines"][0]["min_words"] == 13
    # A 10 s link: the estimate pays 19 of the 10 s clip's 22 -- a 10 s shot, not a 5 s one of 10.
    assert _shape(_two("cliffhanger", lengths=(5, 10))) == [(10, 1, 17, 19)]
    # An 8 s clip the estimate pays only its floor of is no shot (no play left): 8 s then 6 s, never 8 + 8.
    assert _shape(_two()) == [(8, 2, 13, 17), (6, 2, 9, 12)]


def test_exchange_lines_and_the_split_follow_the_capacity_table():
    assert [timing.exchange_line_count(timing._fill_floor(w), w) for w in (10, 12, 17, 22)] == [2, 2, 3, 4]
    assert timing.exchange_line_count(13, 17, one_speaker=True) == 2
    assert timing.split_exchange(13, 17, 3) == [(4, 8), (4, 8), (5, 9)]
    assert timing.split_exchange(9, 12, 2) == [(4, 7), (5, 8)]
    assert timing.split_exchange(13, 17, 1) == [(13, 17)]


# The plans a TTS story got before plan 27 stage 2 (computed on 62bdd31's timing.py, stage 1's slots).
TTS_BEFORE = {
    "narrated": {"slot_s": [10.0, 16.0], "allowed_speech_s": 14.25, "lines": [
        {"kind": "narrator", "speaker": "narrator", "seconds": 8.55, "max_words": 18},
        {"kind": "character", "speaker": "char_rida", "seconds": 5.7, "max_words": 9}],
        "max_words": 27, "min_words": 13},
    "confrontation": {"slot_s": [10.0, 16.0], "allowed_speech_s": 14.345, "lines": [
        {"kind": "character", "speaker": "char_rida", "seconds": 7.172, "max_words": 11},
        {"kind": "character", "speaker": "char_marie", "seconds": 7.172, "max_words": 15}],
        "max_words": 26, "min_words": 13},
}


def test_a_tts_story_plans_exactly_as_before():
    scene = _scene("setup", characters=("char_rida", "char_marie"))
    narrated = timing.scene_plan(NARRATED, scene, lang=FR, native=False, narrator_provider="edge",
                                 speakers={"char_rida": "gemini", "char_marie": "edge"})
    confrontation = timing.scene_plan(CONFRONTATION, dict(scene, function="rising"), lang=FR, native=False,
                                      narrator=False, speakers={"char_rida": "gemini", "char_marie": None})
    assert {"narrated": narrated, "confrontation": confrontation} == TTS_BEFORE
