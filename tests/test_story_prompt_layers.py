"""The richer v2 keyframe and clip prompts, ordered by value (AI Story phase
7 follow-up, stage F2; the human, 2026-10-02: "More prompt context is more
accuracy and details that make the story good").

A v2 keyframe said the reference roles, the beat, the staging, the
composition, the place slice, the style tail and the constraints. It now
also says, when its link's budget allows (``prompting.Budgets``):

- the beat's emotional temperature and the characters' state -- the
  scene's function and emotion, who is mid-sentence (a question, an
  exclamation) and who listens, who is hurt (the ledger);
- what stands between two framed characters (the dossier's relationship);
- what changed since the previous shot of the scene (who moved where, who
  and what came into frame, what happened just before);
- each character's bearing (``look.bearing``: posture, how they hold
  themselves);
- the time of day of the place variant.

The ladder drops them first, the least valuable first (``shots.
_CONTEXT_DROP_ORDER``: between, time, bearing, since, mood), before any look
or place is shortened; at today's 220 words the fixtures' prompts are byte
for byte what they were. The clip prompt gains the beat's emotion, the
characters' micro-actions and the camera's intent, dropped in the reverse
order (intent, micro-actions, emotion) before the motion is cut.

Stdlib + pytest (DEC-012); the fixtures of tests/test_story_shots.py and
tests/test_story_shots_crowded.py. The new behaviour is reached inside the
tests, so on the parent commit each fails on its own.
"""

from __future__ import annotations

import hashlib

import pytest

from clipping.aistory import prompting, shots, templates

import test_story_shots as tss
import test_story_shots_crowded as crowded

NOW = tss.NOW
WIDE = prompting.Budgets(320, 160, "fal/seedream-4.5-edit", "gemini/veo-3.1-lite")
# Room for every layer at once: a two-character shot with its sheets, set and turnarounds runs ~300 words
# of roles, beat, staging, composition and place before any context layer (the drop-order test shows
# what seedream's 320 keeps of them).
ROOMY = prompting.Budgets(400, 160)
SUBJECTS = ["@char_captain_obvious", "@char_miss_overthink", "#place_clocktown:day"]
TALL, SHORT = "the tall yellow geometric cylinder", "the short red triangle character"
# The two fixture prompts as the parent commit builds them at 220 words (tests/test_story_shots.py's
# two-shot, tests/test_story_shots_crowded.py's crowded shot): both run down the ladder to fit, so the
# new layers are all dropped and nothing moves.
TWO_SHOT_AT_220 = "38439b1d0e7675472fb96b8b659f16e74cecf96934c8333812eb1e8a1c278f23"
CROWDED_AT_220 = "45ab6cc961ce4b021948ede55d98690e0f53459a747b3d5551c291891e1d5512"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plan(framing, subjects, **extra):
    plan = {"framing": framing, "action": tss._V2_ACTION, "subjects": subjects, "lines": [1], "camera_motion": "hold",
            "modifiers": []}
    plan.update(extra)
    return plan


def _resolve(plan, *, scene=None, entities=None, budgets=None, ledger=None, previous_plan=None):
    return shots.resolve_shot(plan, scene=scene or tss._v2_scene(), entities=entities or tss._v2_entities(),
                              style_lock=tss.CARTOON_FLAT, consistency_mode="references", v2=True, ledger=ledger,
                              budgets=budgets, previous_plan=previous_plan)


# ================================================================ the keyframe

def test_the_mood_the_faces_and_the_time_of_day_join_the_keyframe_when_the_budget_allows():
    """On a link with room (seedream, 320 words) the keyframe says the
    beat's temperature -- the scene's function and emotion, who is
    mid-sentence and who listens -- right after the beat, and the time of
    day after the place slice; at today's 220 the two-shot fixture is deep
    in the ladder, so neither appears and the prompt is byte for byte the
    parent commit's."""
    wide = _resolve(_plan("medium_single", SUBJECTS), budgets=WIDE)["image_prompt"]
    mood = f"Mood: the opening hook, shocked. {TALL[0].upper()}{TALL[1:]} is mid-sentence, speaking; {SHORT} listens."
    assert mood in wide, wide
    assert wide.index("absolutely certain).") < wide.index("Mood:") < wide.index(f"On the left, {TALL}")
    assert "Time: day." in wide and wide.index("Layout:") < wide.index("Time: day.") < wide.index("Style:")
    assert 220 < len(wide.split()) <= 320 and wide.endswith(prompting.CONSTRAINTS_KEYFRAME)
    tss._no_v2_names(wide)

    narrow = _resolve(_plan("medium_single", SUBJECTS))["image_prompt"]
    assert "Mood:" not in narrow and "Time:" not in narrow and _sha(narrow) == TWO_SHOT_AT_220

    # A question and an exclamation are said as such; a listener only when someone speaks in frame.
    scene = tss._v2_scene()
    scene["lines"][0]["text"] = "Is that object a giant toaster?"
    assert f"{TALL[0].upper()}{TALL[1:]} is mid-sentence, asking a question; {SHORT} listens." in _resolve(
        _plan("medium_single", SUBJECTS), scene=scene, budgets=WIDE)["image_prompt"]
    scene["lines"][0]["text"] = "That object is a giant toaster!"
    alone = _resolve(_plan("close_up", ["@char_captain_obvious", "#place_clocktown:day"]), scene=scene,
                     budgets=WIDE)["image_prompt"]
    assert f"{TALL[0].upper()}{TALL[1:]} is mid-sentence, exclaiming." in alone and "listens" not in alone
    # A scene with no spoken line in the shot: the temperature alone; a neutral scene says its function alone.
    quiet = tss._v2_scene()
    quiet.update(emotion="neutral", function="setup", lines=[])
    establishing = _resolve(_plan("wide_establishing", SUBJECTS, lines=[]), scene=quiet, budgets=WIDE)["image_prompt"]
    assert "Mood: a quiet setup." in establishing and "mid-sentence" not in establishing
    # Without a look the place slice already says the variant's light: no second time-of-day sentence.
    entities = tss._v2_entities()
    del entities["places"]["place_clocktown"]["look"]
    no_look = _resolve(_plan("medium_single", SUBJECTS), entities=entities, budgets=WIDE)["image_prompt"]
    assert "Light: day light." in no_look and "Time: day." not in no_look


def test_who_is_hurt_and_what_stands_between_two_characters_join_the_keyframe():
    """The character's state from the ledger (an injury is drawn), in the
    mood group; the dossier's relationship between two framed characters
    (its ``now``, the writers' words) as its own sentence after the mood,
    only when both are in frame."""
    entities = tss._v2_entities()
    entities["characters"]["char_captain_obvious"]["dossier"] = {
        "backstory": "x", "goal": "x", "need": "x", "fears": "x", "secrets": [], "arc": "x",
        "voice": {"patterns": "x", "vocabulary": "x", "catchphrases": []},
        "relationships": [{"with": "char_miss_overthink", "history": "rivals since the academy",
                           "now": "rivals who pretend to be friends"}]}
    ledger = {"char_miss_overthink": {"location": None, "wardrobe_set": "daily", "possessions": [],
                                      "injuries": "a bandaged left hand", "relationship_notes": None}}
    prompt = _resolve(_plan("medium_single", SUBJECTS), entities=entities, ledger=ledger, budgets=ROOMY)["image_prompt"]
    assert f"{SHORT} listens. {SHORT[0].upper()}{SHORT[1:]} is hurt: a bandaged left hand." in prompt
    between = f"Between {TALL} and {SHORT}: rivals who pretend to be friends."
    assert between in prompt and prompt.index("Mood:") < prompt.index(between) < prompt.index(f"On the left, {TALL}")
    tss._no_v2_names(prompt)
    # At seedream's 320 the same shot keeps the mood and the injury (the state), not what stands between.
    at_320 = _resolve(_plan("medium_single", SUBJECTS), entities=entities, ledger=ledger, budgets=WIDE)["image_prompt"]
    assert "is hurt: a bandaged left hand." in at_320 and "Between" not in at_320
    alone = _resolve(_plan("close_up", ["@char_captain_obvious", "#place_clocktown:day"]), entities=entities,
                     ledger=ledger, budgets=ROOMY)["image_prompt"]
    assert "Between" not in alone and "is hurt" not in alone


def test_what_changed_since_the_previous_shot_joins_the_keyframe():
    """A shot after another of its scene (``previous_plan``) says who moved
    where, who and what came into frame, and what happened just before --
    before the staging, so the model does not copy the previous keyframe's
    positions; the first shot of a scene says nothing of the kind."""
    entities = tss._v2_entities()
    monocle = tss._prop("prop_monocle", "a golden monocle on a thin chain", name="Monocle")
    entities["props"]["prop_monocle"] = monocle
    place = "#place_clocktown:day"
    previous = _plan("medium_two_shot", ["@char_captain_obvious", place],
                     action="@char_captain_obvious squints at the giant toaster in #place_clocktown:day, alone.",
                     staging=[{"subject": "@char_captain_obvious", "position": "left", "facing": "the toaster",
                               "expression": "puzzled"}])
    current = _plan("medium_two_shot", ["@char_captain_obvious", "@char_miss_overthink", "%prop_monocle", place],
                    staging=[{"subject": "@char_captain_obvious", "position": "right", "facing": "her",
                              "expression": "shocked"},
                             {"subject": "@char_miss_overthink", "position": "left", "facing": "him",
                              "expression": "panicked"}])
    prompt = _resolve(current, entities=entities, budgets=ROOMY, previous_plan=previous)["image_prompt"]
    since = (f"Since the previous shot: {TALL} has moved to the right; {SHORT} has come into frame; the golden "
             f"monocle on a thin chain is now in frame. Just before, {TALL} squints at the giant toaster in the "
             "setting, alone.")
    assert since in prompt, prompt
    assert prompt.index("Mood:") < prompt.index("Since the previous shot") < prompt.index(f"On the right, {TALL}")
    tss._no_v2_names(prompt)
    first = _resolve(current, entities=entities, budgets=ROOMY)["image_prompt"]
    assert "Since the previous shot" not in first and "Just before" not in first
    # Nothing moved and nothing new: only what happened just before.
    same = _resolve(current, entities=entities, budgets=ROOMY, previous_plan=current)["image_prompt"]
    assert "has moved" not in same and "into frame" not in same and f"Just before, {TALL} and {SHORT} stand" in same


def test_each_characters_bearing_joins_the_keyframe_after_the_staging():
    """``look.bearing`` (posture, how they hold themselves; D2 writes it,
    optional) is said for each framed character that has one, right after
    the staging; a look without one says nothing."""
    entities = tss._v2_entities()
    entities["characters"]["char_captain_obvious"]["look"]["bearing"] = "Stands rigidly straight, chin up"
    prompt = _resolve(_plan("medium_single", SUBJECTS), entities=entities, budgets=WIDE)["image_prompt"]
    bearing = f"Bearing: {TALL} stands rigidly straight, chin up."
    assert bearing in prompt and prompt.index("They face each other.") < prompt.index(bearing) < prompt.index("Camera:")
    entities["characters"]["char_miss_overthink"]["look"]["bearing"] = "hunched, arms wrapped around herself"
    both = _resolve(_plan("medium_single", SUBJECTS), entities=entities, budgets=WIDE)["image_prompt"]
    assert f"Bearing: {TALL} stands rigidly straight, chin up; {SHORT} hunched, arms wrapped around herself." in both
    assert "Bearing:" not in _resolve(_plan("medium_single", SUBJECTS), budgets=WIDE)["image_prompt"]


def _everything():
    """The crowded shot of test_story_shots_crowded with every context
    layer: a relationship, a bearing, an injury, a previous shot."""
    plan, scene, entities = crowded._crowded()
    captain = entities["characters"]["char_captain_obvious"]
    captain["look"]["bearing"] = "stands rigidly straight, chin up"
    captain["dossier"] = {"backstory": "x", "goal": "x", "need": "x", "fears": "x", "secrets": [], "arc": "x",
                          "voice": {"patterns": "x", "vocabulary": "x", "catchphrases": []},
                          "relationships": [{"with": "char_miss_overthink", "history": "x", "now": "uneasy allies"}]}
    ledger = {"char_grand_mere": {"location": None, "wardrobe_set": "daily", "possessions": [],
                                  "injuries": "a bruised cheek", "relationship_notes": None}}
    previous = dict(plan, subjects=["@char_captain_obvious", "@char_miss_overthink", "#place_clocktown:day"],
                    staging=[{"subject": "@char_captain_obvious", "position": "right", "facing": "left",
                              "expression": "calm"}])
    return plan, scene, entities, ledger, previous


_MARKERS = {"between": "Between ", "when": "Time: day.", "bearing": "Bearing:", "since": "Since the previous shot:",
            "mood": "Mood:"}


def test_the_context_layers_are_dropped_first_in_their_value_order_before_any_look_is_shortened():
    """Sweeping the budget down from 600 (room for all of it on the crowded
    shot: nine images' roles, a long beat, three looks and the five layers
    run to about 590 words) to 200: each layer, once dropped, stays dropped; they go in
    ``shots._CONTEXT_DROP_ORDER`` (what stands between two characters, the
    time of day, the bearing, what changed, the mood last); while any is
    still said, no look has been shortened (the ladder cuts the rendering
    first, as before, then these, then the looks and the place). At 220 the
    crowded shot is byte for byte the parent commit's."""
    plan, scene, entities, ledger, previous = _everything()
    assert shots._CONTEXT_DROP_ORDER == ("between", "when", "bearing", "since", "mood")

    def prompt_at(words):
        return _resolve(plan, scene=scene, entities=entities, ledger=ledger, previous_plan=previous,
                        budgets=prompting.Budgets(words, 160))["image_prompt"]

    at = {}
    lowest_with = {}
    present = set()
    for words in range(600, 199, -4):
        try:
            prompt = prompt_at(words)
        except shots.PromptOverBudget as exc:
            # The crowded shot's floor (its last rung, 215 words): refused, every layer long gone.
            assert exc.budget == words and exc.words > words and not present
            break
        assert len(prompt.split()) <= words
        present = {name for name, marker in _MARKERS.items() if marker in prompt}
        if words == 600:
            assert present == set(_MARKERS)
            assert "Just before, the tall yellow geometric cylinder lifts the golden monocle on a thin chain." in prompt
        for name in present:
            lowest_with[name] = words
        for name in set(_MARKERS) - present:
            assert name not in at or at[name] >= words  # once dropped, dropped for good
            at.setdefault(name, words)
        if present:
            # Every look as whole as at 600 while any context layer is said: the fig's hair and face (the
            # last parts a tighter look budget drops) are still there.
            assert "a curled stem on top" in prompt and "crinkled smile, tiny spectacles" in prompt, words
    assert set(at) == set(_MARKERS)
    order = sorted(_MARKERS, key=lambda name: -at[name])
    assert order == list(shots._CONTEXT_DROP_ORDER), order
    assert lowest_with["mood"] < lowest_with["since"] < lowest_with["bearing"] < lowest_with["when"] \
        < lowest_with["between"]
    assert _sha(crowded._resolve(plan, scene, entities)["image_prompt"]) == CROWDED_AT_220


def test_the_storyboard_threads_the_previous_shot_of_a_scene_and_a_refresh_reproduces_it():
    """``build_storyboard`` gives the second shot of a scene its previous
    plan (the first gets none); ``refresh_prompts`` reads it back from the
    storyboard and reproduces the same prompts."""
    template_v2 = templates.load_episode_template("serial_60s_v2")
    script, plans = tss._v2_script(), tss._v2_plans()
    first = dict(plans["s02"][0], lines=[1])
    second = dict(first, framing="close_up", staging=[tss._stage(tss.M, "left", "him", "smug")], lines=[2],
                  action=f"{tss.M} leans in and whispers the price of her silence to {tss.K}.")
    plans["s02"] = [first, second]
    budgets = WIDE
    doc, _notes = shots.build_storyboard(script, plans, {sid: "t1" for sid in plans}, entities=tss.ENTITIES,
                                         style_lock=tss.FRUIT_DRAMA, template=template_v2, language=tss.EN,
                                         consistency_mode="references", now=NOW, v2=True,
                                         shots_per_scene=template_v2["shots_per_scene"], budgets=budgets)
    s02 = [shot for shot in doc["shots"] if shot["scene_id"] == "s02"]
    assert len(s02) == 2 and "Since the previous shot" not in s02[0]["image_prompt"]
    assert "Since the previous shot: the anthropomorphic mango has moved to the left" in s02[1]["image_prompt"]
    assert "Just before, the anthropomorphic mango corners the anthropomorphic kiwi" in s02[1]["image_prompt"]
    refreshed = shots.refresh_prompts(doc, script, entities=tss.ENTITIES, style_lock=tss.FRUIT_DRAMA,
                                      consistency_mode="references", v2=True, budgets=budgets)
    assert [s["image_prompt"] for s in refreshed["shots"]] == [s["image_prompt"] for s in doc["shots"]]
    assert shots.previous_plan(doc["shots"], 2) == shots.plan_of(doc["shots"][1], v2=True)
    assert shots.previous_plan(doc["shots"], 1) is None


# ==================================================================== the clip

def test_the_clip_prompt_says_the_emotion_the_micro_actions_and_the_cameras_intent_in_their_order():
    """``prompting.layered_clip_prompt``: the emotion after the motion, the
    micro-actions after the secondary motion, the intent inside the camera
    sentence; dropped intent first, then micro-actions, then emotion, and
    only then is the motion cut. With none given, today's text."""
    style = tss.CARTOON_FLAT
    motion = ("the tall yellow geometric cylinder lifts the monocle to his eye, squints, lowers it again and takes one "
              "slow step back toward the newsstand as the clock strikes")
    args = dict(subject="the tall yellow geometric cylinder", motion=motion,
                camera_phrase=prompting.CAMERA_PHRASES["push_in"],
                secondary="the triangle reacts with a small movement", emotion="The mood is shocked",
                micro="Micro-actions: the cylinder breathes visibly, hands shift slightly",
                intent="closing on the emotion")
    full = prompting.layered_clip_prompt(style, budget=160, **args)
    assert full.startswith(f"{motion[0].upper()}{motion[1:]}. The mood is shocked. The triangle reacts with a small "
                           "movement. Micro-actions: the cylinder breathes visibly, hands shift slightly. Slow push-in "
                           "toward the subject, closing on the emotion. ")
    assert prompting.STAYS_STILL in full and full.endswith("Snappy 2D animation, limited frames feel, bouncy motion.")

    def present(prompt):
        return {name for name, marker in (("emotion", "The mood is shocked."), ("micro", "Micro-actions:"),
                                          ("intent", "closing on the emotion")) if marker in prompt}

    assert present(full) == {"emotion", "micro", "intent"}
    # 28 words of motion, 38 of fixed parts, 4 + 8 + 4 of layers: all at 80, the intent gone at 75, the
    # micro-actions at 70, the emotion at 60; only under that is the motion cut.
    at_80 = prompting.layered_clip_prompt(style, **args)
    assert present(at_80) == {"emotion", "micro", "intent"} and len(at_80.split()) <= 80
    at_75 = prompting.layered_clip_prompt(style, budget=75, **args)
    assert present(at_75) == {"emotion", "micro"} and "as the clock strikes" in at_75 and len(at_75.split()) <= 75
    at_70 = prompting.layered_clip_prompt(style, budget=70, **args)
    assert present(at_70) == {"emotion"} and "as the clock strikes" in at_70 and len(at_70.split()) <= 70
    at_55 = prompting.layered_clip_prompt(style, budget=55, **args)
    assert present(at_55) == set() and "as the clock strikes" not in at_55 and len(at_55.split()) <= 55
    seen = []
    for words in range(160, 54, -1):
        kept = present(prompting.layered_clip_prompt(style, budget=words, **args))
        assert not seen or kept <= seen[-1]
        seen.append(kept)
    assert [kept for i, kept in enumerate(seen) if i == 0 or kept != seen[i - 1]] == [
        {"emotion", "micro", "intent"}, {"emotion", "micro"}, {"emotion"}, set()]
    plain = dict(args, emotion="", micro="", intent="")
    assert prompting.layered_clip_prompt(style, **plain) == prompting.layered_clip_prompt(style, budget=80, **plain)
    assert "closing on" not in prompting.layered_clip_prompt(style, **plain)


def test_a_v2_shots_clip_prompt_carries_the_beats_emotion_its_micro_actions_and_the_cameras_intent():
    """``shots.resolve_shot`` writes them from the scene, the staging and
    the camera motion: on Veo (160) all three; with no link known (80) the
    fixture keeps the emotion, drops the rest; a staged glance is said."""
    wide = _resolve(_plan("medium_single", SUBJECTS), budgets=WIDE)["video_prompt"]
    assert "The mood is shocked." in wide
    assert (f"Micro-actions: {TALL} breathes visibly, hands shift slightly; {SHORT} breathes visibly, hands shift "
            "slightly.") in wide
    assert "Static camera, locked-off shot, letting the moment breathe." in wide
    assert prompting.STAYS_STILL in wide and len(wide.split()) <= 160
    tss._no_v2_names(wide)
    narrow = _resolve(_plan("medium_single", SUBJECTS))["video_prompt"]
    assert "The mood is shocked." in narrow and "Micro-actions" not in narrow and "letting the moment" not in narrow
    assert len(narrow.split()) <= 80
    staged = _plan("medium_single", SUBJECTS, camera_motion="push_in",
                   staging=[{"subject": "@char_captain_obvious", "position": "left", "facing": "the toaster",
                             "expression": "shocked"}])
    scene = tss._v2_scene()
    scene["function"] = "peak"
    clip = _resolve(staged, scene=scene, budgets=WIDE)["video_prompt"]
    assert f"Micro-actions: {TALL} breathes visibly, a glance toward the toaster, hands shift slightly;" in clip
    assert "Slow push-in toward the subject, closing on the emotion to land the beat." in clip
    # A line's emotion other than the scene's names the speaker's.
    scene["emotion"] = "tension"
    tense = _resolve(staged, scene=scene, budgets=WIDE)["video_prompt"]
    assert f"The mood is tense; {TALL} looks shocked." in tense


def test_a_legacy_story_resolves_exactly_as_before_whatever_it_is_given():
    """RC-Q1: v1 reads neither the budgets nor the previous plan."""
    plan = {"framing": "medium_two_shot", "action": "@char_captain_obvious waves at @char_miss_overthink.",
            "subjects": ["@char_captain_obvious", "@char_miss_overthink", "#place_city_square:day"]}
    scene = dict(tss._v2_scene(), place_id="place_city_square")
    before = shots.resolve_shot(plan, scene=scene, entities=tss._A_ENTITIES, style_lock=tss.CARTOON_FLAT,
                                consistency_mode="references")
    after = shots.resolve_shot(plan, scene=scene, entities=tss._A_ENTITIES, style_lock=tss.CARTOON_FLAT,
                               consistency_mode="references", budgets=WIDE, previous_plan=plan)
    assert before == after and "Mood:" not in after["image_prompt"]


@pytest.mark.parametrize("framing", ["close_up", "insert_prop"])
def test_a_shot_with_no_character_in_frame_says_no_face_and_no_micro_action(framing):
    plan, scene, entities = crowded._crowded()
    plan.update(framing=framing, subjects=["%prop_monocle", "#place_clocktown:day"], staging=[])
    resolved = _resolve(plan, scene=scene, entities=entities, budgets=WIDE)
    assert "mid-sentence" not in resolved["image_prompt"] and "Bearing:" not in resolved["image_prompt"]
    assert "Mood: the opening hook, shocked." in resolved["image_prompt"]
    assert "Micro-actions" not in resolved["video_prompt"] and "The mood is shocked." in resolved["video_prompt"]
