"""Native speech: the profile and the shot plan (plan 22, stage 4).

A story on the ``native_speech`` budget profile (v2, tier 3) has each
character line spoken on camera by its own clip: one shot a line, planned at
the smallest length its speech link sells that can speak it (2.4 words a
second after 0.7 s of breath: 4 s holds 7 words, 6 s 12, 8 s 17), silent
reaction and narrator shots on its silent link, every boundary a cut, every
shot exactly its clip. The links are named by the profile and resolved by
name; a line no clip can speak is refused before any call, with the fix.
Every other story's storyboard is built as it always was.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import json

import pytest

import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_storyboard_props as tsp
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)

NOW = tas.NOW
FAST, LITE, PREMIUM = "gemini/veo-3.1-fast", "gemini/veo-3.1-lite", "gemini/veo-3.1"


def native_story(store, *, profile="native_speech", speech_model=None, mode=None, lines=None):
    """A written v2 episode at tier 3 on *profile* (``native_speech``), its
    prop drawn: ready for the storyboard step. *lines* (``{line_id: text}``)
    rewrites those lines first."""
    story_id = eps._written_script(store)

    def v2(doc):
        doc["generation_profile"].update(pipeline="v2", tier=3, budget_profile=profile, route="api")
        if speech_model is not None:
            doc["generation_profile"]["speech_model"] = speech_model
        if mode is not None:
            doc["generation_profile"]["mode"] = mode
        doc.update(episode_template_id="serial_60s_v2")

    store.update(story_id, v2, now=NOW)
    script = eps._script(store, story_id)
    script["template_id"] = "serial_60s_v2"
    for scene in script["scenes"]:
        for line in scene["lines"]:
            if lines and line["line_id"] in lines:
                line["text"] = lines[line["line_id"]]
    from clipping.aistory.steps import episode_common

    episode_common.retime(script, episode_common.load_context(store, story_id, 1))
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    tsp._plant_image(store, story_id, "props", eps.PHONE)
    return story_id


def planned_story(store, **kwargs):
    """:func:`native_story` with its storyboard planned (fast) and both
    documents approved: ready for the assets step."""
    from clipping.aistory.steps import storyboard

    story_id = native_story(store, **kwargs)
    storyboard.build_fast(store, story_id, 1, now=NOW, on_log=lambda _line: None)
    tas._approve(store, story_id)
    return story_id


def _story_doc(**profile):
    base = {"tier": 3, "route": "api", "consistency_mode": "references", "budget_profile": "native_speech",
            "pipeline": "v2"}
    base.update(profile)
    return {"generation_profile": {key: value for key, value in base.items() if value is not None}}


# ================================================================ the profile

def test_the_native_speech_profile_names_its_links_by_name_and_its_speech_model_switch():
    from clipping.aistory import media_policy
    from clipping.providers import budget

    profile = budget.profile_settings("native_speech")
    assert profile["speech_links"] == {"lite": LITE, "fast": FAST, "premium": PREMIUM}
    assert (profile["speech_model"], profile["silent_link"], profile["tier3_native_audio"]) == ("fast", LITE, "speech")
    assert profile["speech_retake"] == {"max_per_shot": 1, "cap_usd": 1.0} and profile["cap_usd"] == 10.0
    story = _story_doc()
    assert media_policy.native_speech(story) is True
    assert (media_policy.speech_link(story), media_policy.silent_link(story)) == (FAST, LITE)
    assert media_policy.speech_link(_story_doc(speech_model="premium")) == PREMIUM
    assert media_policy.speech_link(story, "lite") == LITE
    # never lipsynced, never an ambience story, fully animated
    assert media_policy.lipsync(_story_doc(lipsync="kling")) is False
    assert media_policy.ambience(story) is False and media_policy.fully_animated(story) is True
    # only a v2 story at tier 3
    assert media_policy.native_speech(_story_doc(tier=2)) is False
    assert media_policy.native_speech(_story_doc(pipeline=None)) is False
    assert media_policy.native_speech(_story_doc(budget_profile="quality")) is False


@pytest.mark.parametrize("key,value,words", [
    ("speech_links", {"lite": LITE, "fast": "not a link"}, "speech_links"),
    ("speech_links", {"lite": LITE, "fast": FAST, "premium": "two words"}, "speech_links.premium"),
    ("speech_model", "ultra", "speech_model"),
    ("silent_link", 42, "silent_link"),
    ("speech_retake", {"max_per_shot": 9, "cap_usd": 1.0}, "speech_retake.max_per_shot"),
])
def test_a_bad_native_speech_key_is_refused_at_load(tmp_path, key, value, words):
    from clipping.providers import budget

    data = budget.load_profiles()
    data["profiles"]["native_speech"][key] = value
    path = tmp_path / "budget_profiles.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        budget.load_profiles(str(path))
    assert words in str(caught.value) and "native_speech" in str(caught.value)


def test_a_link_that_does_not_exist_yet_is_accepted_by_name(tmp_path):
    """Stage 5's ``manual/upload`` is named before its provider exists: the
    profile loads, and only the clip plan says it cannot run."""
    from clipping.providers import budget

    data = budget.load_profiles()
    data["profiles"]["native_speech"].update(speech_links={"lite": "manual/upload", "fast": "manual/upload",
                                                           "premium": "manual/upload"}, silent_link="manual/upload")
    path = tmp_path / "budget_profiles.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert budget.load_profiles(str(path))["profiles"]["native_speech"]["silent_link"] == "manual/upload"


# ================================================================ the lengths

@pytest.mark.parametrize("words,clip_s", [(1, 6), (7, 6), (8, 6), (12, 6), (13, 8), (17, 8), (18, None)])
def test_a_speaking_clip_is_the_smallest_sold_length_that_can_speak_its_line(words, clip_s):
    """Plan 27 stage 1: the default lengths are 6 and 8 s (the 4 s clip is out of the 5-10 s window)."""
    from clipping.aistory import native_speech

    assert native_speech.SPEECH_LENGTHS == (6, 8)
    assert [native_speech.capacity(length) for length in (5, 6, 8, 10)] == [10, 12, 17, 22]
    assert native_speech.SPEECH_WPS == 2.4
    assert native_speech.speech_clip_s(" ".join(["mot"] * words)) == clip_s


@pytest.mark.parametrize("words,clip_s", [(1, 5), (10, 5), (11, 10), (22, 10), (23, None)])
def test_a_10s_sold_length_holds_22_words_and_a_5s_one_10(words, clip_s):
    """Seedance and kling sell 5 and 10 s: 5 s holds 10 words, 10 s holds 22."""
    from clipping.aistory import native_speech

    assert native_speech.speech_clip_s(" ".join(["mot"] * words), (5, 10)) == clip_s


def test_a_reaction_shot_is_6s_and_a_silent_clip_is_never_under_the_window():
    from clipping.aistory import native_speech

    assert native_speech.REACTION_S == 6 and native_speech.SHOT_WINDOW_S == (5, 10)
    assert native_speech.silent_clip_s(native_speech.REACTION_S, (6, 8)) == 6
    assert native_speech.silent_clip_s(native_speech.REACTION_S, (5, 10)) == 10
    assert native_speech.narrator_clip_s(1.0, (6, 8)) == 6


def _scene(lines, *, characters=("char_a", "char_b")):
    return {"scene_id": "s02", "place_id": "place_x", "time_variant": "day", "characters": list(characters),
            "props": [], "function": "rising", "target_duration_s": 6.0,
            "lines": [{"line_id": f"l{8 + i:02d}", "speaker": speaker, "text": text, "emotion": "tension",
                       "timing": None} for i, (speaker, text) in enumerate(lines)]}


def _beat(lines, framing="medium_two_shot"):
    return {"framing": framing, "camera_motion": "push_in", "modifiers": [], "action": "@char_a leans in",
            "subjects": ["@char_a", "@char_b", "#place_x:day"], "lines": list(lines)}


def test_each_character_line_is_one_speaking_shot_and_a_narrator_line_a_silent_one():
    """Fail-first. Two beat plans holding three lines and one beat with none
    become: one shot a line -- the speaker its subject, whom it answers its
    secondary one; the narrator's line a silent shot sized to its narration
    -- and one silent reaction shot (the template's ``reaction_shots`` at
    most one). Planned again from its own plans, nothing moves."""
    from clipping.aistory import shots

    scene = _scene([("char_a", "Tu caches la clé depuis lundi, je le sais."),
                    ("char_b", "Rends-la moi tout de suite parce que je dois partir avant que la nuit tombe sur nous."),
                    ("narrator", "Personne ne bouge.")])
    plans = [_beat([1, 2]), _beat([], framing="close_up"), _beat([3], framing="wide_establishing")]
    planned = shots.speech_shot_plan(scene, plans, language="fr")
    assert [(plan["lines"], plan["speaks"], plan["clip_s"]) for plan in planned] == [
        ([1], True, 6), ([2], True, 8), ([], False, 6), ([3], False, 6)]
    assert planned[0]["subjects"][:2] == ["@char_a", "@char_b"]
    assert planned[1]["subjects"][:2] == ["@char_b", "@char_a"]
    assert planned[3]["framing"] == "wide_establishing"  # a silent shot keeps its framing
    assert shots.speech_shot_plan(scene, planned, language="fr") == planned


def _with_plan(scene, entries):
    """*scene* carrying a stored line plan (plan 24 stage 1's shape) of *entries*
    ``(kind, speaker, clip_s)``."""
    scene["line_plan"] = {"allowed_speech_s": 12.0, "max_words": 20, "min_words": 10,
                          "lines": [{"kind": kind, "speaker": speaker, "seconds": float(clip) - 0.7, "clip_s": clip,
                                     "max_words": 12} for kind, speaker, clip in entries]}
    return scene


def test_the_shots_are_the_scenes_line_plan_when_it_has_one():
    """Plan 24 stage 4, fail-first. A character line of 7 words would be a 6 s
    clip and a one-word narration a 6 s one (plan 27: no 4 s clip); the stored
    plan says 8 s and 8 s, so the two shots are exactly 8 s then 8 s, in line order."""
    from clipping.aistory import shots

    lines = [("char_a", "Tu caches la clé depuis lundi."), ("narrator", "Personne ne bouge.")]
    plans = [_beat([1]), _beat([2], framing="wide_establishing")]
    today = shots.speech_shot_plan(_scene(lines), plans, language="fr")
    assert [plan["clip_s"] for plan in today] == [6, 6]

    notes = []
    scene = _with_plan(_scene(lines), [("character", "char_a", 8), ("narrator", "narrator", 8)])
    planned = shots.speech_shot_plan(scene, plans, language="fr", notes=notes)
    assert [(plan["lines"], plan["speaks"], plan["clip_s"]) for plan in planned] == [([1], True, 8), ([2], False, 8)]
    assert notes == []
    assert shots.speech_shot_plan(scene, planned, language="fr") == planned


def test_a_written_line_that_outgrew_its_planned_clip_keeps_the_smallest_clip_that_holds_it_and_is_named():
    from clipping.aistory import shots

    long_line = "Rends-la moi tout de suite parce que je dois partir ce soir avant la nuit."
    scene = _with_plan(_scene([("char_a", long_line)]), [("character", "char_a", 6)])
    notes = []
    planned = shots.speech_shot_plan(scene, [_beat([1])], language="fr", notes=notes)
    assert [plan["clip_s"] for plan in planned] == [8]
    assert len(notes) == 1 and "s02 line 1" in notes[0] and "planned 6 s" in notes[0]


def test_a_scene_with_no_plan_is_planned_as_it_always_was_and_says_nothing():
    """The guard: the plan of ``test_each_character_line_is_one_speaking_shot...`` with the
    optional keys absent or empty -- same clips, no note."""
    from clipping.aistory import shots

    lines = [("char_a", "Tu caches la clé depuis lundi, je le sais."),
             ("char_b", "Rends-la moi tout de suite parce que je dois partir avant que la nuit tombe sur nous."),
             ("narrator", "Personne ne bouge.")]
    plans = [_beat([1, 2]), _beat([], framing="close_up"), _beat([3], framing="wide_establishing")]
    notes = []
    bare = shots.speech_shot_plan(_scene(lines), plans, language="fr", notes=notes)
    assert [plan["clip_s"] for plan in bare] == [6, 8, 6, 6] and notes == []
    empty = _scene(lines)
    empty["line_plan"] = {"lines": []}
    assert shots.speech_shot_plan(empty, plans, language="fr", notes=notes) == bare and notes == []


def test_a_plan_whose_line_count_differs_from_the_written_lines_is_ignored_and_logged_once():
    from clipping.aistory import shots

    lines = [("char_a", "Tu caches la clé depuis lundi."), ("narrator", "Personne ne bouge.")]
    plans = [_beat([1]), _beat([2], framing="wide_establishing")]
    scene = _with_plan(_scene(lines), [("character", "char_a", 8)])
    notes = []
    planned = shots.speech_shot_plan(scene, plans, language="fr", notes=notes)
    assert [plan["clip_s"] for plan in planned] == [6, 6]
    assert len(notes) == 1 and "ignored" in notes[0]


def test_a_line_longer_than_the_longest_clip_is_refused_with_the_fix():
    from clipping.aistory import shots

    scene = _scene([("char_a", " ".join(["mot"] * 18))])
    with pytest.raises(shots.SpeechLineTooLong) as caught:
        shots.speech_shot_plan(scene, [_beat([1])], language="fr")
    assert "l08 has 18 words, more than a 8 s clip can speak (17 words at most)" in str(caught.value)
    assert "shorten it, or split it into two lines" in str(caught.value)


# ================================================================ the storyboard

def test_the_storyboard_of_a_native_speech_story_is_one_clip_a_line_every_shot_its_clip(store):
    """Fail-first, through the storyboard step: every character line is the
    one line of one ``speaks`` shot; every shot lasts exactly its planned
    clip; every boundary is a cut; the board says ``timing_mode:
    native_speech`` and the script's timing is the shots' sum (plus the end
    card), never cut to the window."""
    story_id = planned_story(store)
    board, script = tas._board(store, story_id), eps._script(store, story_id)
    assert board["timing_mode"] == "native_speech"
    speaking = [shot for shot in board["shots"] if shot["speaks"]]
    lines = [line for scene in script["scenes"] for line in scene["lines"] if line["speaker"] != "narrator"]
    assert sorted(line_id for shot in speaking for line_id in shot["lines"]) == sorted(
        line["line_id"] for line in lines)
    assert all(len(shot["lines"]) == 1 for shot in speaking)
    assert all(shot["duration_s"] == float(shot["clip_s"]) and shot["clip_s"] in (4, 6, 8)
               for shot in board["shots"])
    assert {transition["type"] for transition in board["transitions"]} == {"cut"}
    total = sum(shot["duration_s"] for shot in board["shots"])
    card = 0.6 if script["cliffhanger"]["cut_to_black"] else 0.0  # the end card's 1.0 s minus its 0.4 s fade
    assert script["timing"]["total_s"] == pytest.approx(total + card)
    for shot in speaking:
        line = next(line for line in lines if line["line_id"] == shot["lines"][0])
        assert shot["subject_tags"][0] == f"@{line['speaker']}"


def test_the_storyboard_step_refuses_a_line_no_clip_can_speak_before_any_call(store):
    from clipping.aistory.steps import storyboard
    from clipping.aistory.steps.llm_call import StepFailed

    story_id = native_story(store, lines={"l08": " ".join(["mot"] * 20)})
    with pytest.raises(StepFailed) as caught:
        storyboard.build_fast(store, story_id, 1, now=NOW, on_log=lambda _line: None)
    assert "l08 has 20 words" in str(caught.value) and "split it into two lines" in str(caught.value)
    assert store.read_episode_doc(story_id, 1, "storyboard.json") is None


def test_guard_a_quality_story_plans_its_shots_as_it_always_did(store):
    """RC-N2: the same story on the quality profile -- no ``timing_mode``,
    no ``speaks``, no ``clip_s``, its shots cut to the episode's timing."""
    story_id = planned_story(store, profile="quality")
    board = tas._board(store, story_id)
    assert "timing_mode" not in board
    assert not any("speaks" in shot or "clip_s" in shot for shot in board["shots"])
