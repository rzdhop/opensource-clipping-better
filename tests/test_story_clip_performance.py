"""Clips that perform, and two beats a scene (AI Story, DEC-252; the human on
the first fully animated v2 episode, 2026-10-03: "no lipsync, the video does
not say things or do movements, boring, no rhythm").

A v2 shot's clip prompt asked for idleness: "X reacts with a small natural
movement", "Micro-actions: X breathes visibly, hands shift slightly", "The
set, the lighting and every character's look stay exactly as in the first
frame", and a style suffix of "subtle natural head and shoulder movement ...
camera slowly pushes in". The model did exactly that. Now:

- the clip says a performance built from what the shot knows: whoever speaks
  one of its lines in frame speaks with the mouth moving on the words (never
  the words: DEC-201, they could be drawn as lettering), the others react
  visibly with a reaction drawn from the line's emotion (else the scene's,
  else its function); then the staging's turns and faces and who handles
  which prop;
- an identity-only clause keeps the looks, the set and the light, and lets
  the characters move (``prompting.IDENTITY_KEEPS``);
- each style ends a v2 clip on its own ``tier2_prompt_suffix_v2`` (the old
  key stays as it is: v1 prompts and their clips' hashes are pinned, RC-Q1);
- T1 v2 asks a clear physical action per character and the speaker's mouth
  and face, and never the previous shot's camera motion (a repeat is
  repaired to the next motion before the validator, which refuses it);
- a body scene with two lines or two characters, long enough for two shots
  of ``min_shot_s``, is planned as two beat shots; the re-plan of scenes
  short of beats keeps reading the clip-length rule alone, so a storyboard
  already planned is never planned again for this.

Stdlib + pytest (DEC-012); the fixtures of tests/test_story_shots.py,
tests/test_story_prompt_layers.py, tests/test_story_prompts_episode.py and
tests/test_story_ambience.py. Each test reaches the new behaviour, so on the
parent commit it fails on its own.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

import test_story_ambience as amb
import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_prompt_layers as tpl
import test_story_prompts_episode as tpe
import test_story_shots as tss
import test_story_storyboard_props as tsp
from clipping.aistory import prompt_budgets, prompting, prompts, schemas, shots, templates, video_plan
from clipping.aistory.steps import episode_common, storyboard
from test_story_assets_step import hermetic, store  # noqa: F401 -- the step's fixtures (hermetic is autouse)

TALL, SHORT = tpl.TALL, tpl.SHORT
SEEDANCE = amb.SEEDANCE
ON_SEEDANCE = prompt_budgets.for_links(None, SEEDANCE, live={})
IDLE_WORDS = ("idle", "breathes visibly", "hands shift slightly", "stay exactly", "small natural",
              "Micro-actions")
STYLES_DIR = Path(templates.__file__).parent / "templates" / "styles"
# Words a v2 motion suffix never says: stillness, and the camera (the camera phrase says it).
STILL_OR_CAMERA = ("subtle", "gentle", "slow", "slowly", "camera", "push", "pan", "dolly", "lip flapping")


def _clip(plan=None, *, scene=None, budgets=ON_SEEDANCE, style_lock=None, entities=None):
    plan = plan or tpl._plan("medium_single", tpl.SUBJECTS, camera_motion="push_in")
    return shots.resolve_shot(plan, scene=scene or tss._v2_scene(), entities=entities or tss._v2_entities(),
                              style_lock=style_lock or tss.CARTOON_FLAT, consistency_mode="references", v2=True,
                              budgets=budgets)["video_prompt"]


# ================================================================ the clip prompt

def test_a_v2_clip_says_the_speaker_speaks_and_the_listener_reacts_and_nothing_idle():
    """The fixture's line is the cylinder's, said shocked: the cylinder
    speaks with the mouth moving (its words never), the triangle steps back,
    eyes widening; no idle wording, the identity clause in place of the
    stays-still one, the style's v2 suffix last, within seedance's 160."""
    clip = _clip()
    lowered = clip.lower()
    assert f"{TALL} speaks with the mouth moving on the words, face and brows carrying the emotion" in lowered
    assert f"{SHORT} reacts visibly, stepping back, eyes widening" in lowered
    for word in IDLE_WORDS:
        assert word.lower() not in lowered, word
    assert "giant toaster." in lowered and "that object is a giant toaster" not in lowered and '"' not in clip
    assert prompting.IDENTITY_KEEPS in clip and prompting.STAYS_STILL not in clip
    suffix_v2 = tss.CARTOON_FLAT["motion_rules"]["tier2_prompt_suffix_v2"]
    assert clip.endswith(prompting.as_sentence(suffix_v2))
    assert tss.CARTOON_FLAT["motion_rules"]["tier2_prompt_suffix"] not in clip
    assert ON_SEEDANCE.clip == prompt_budgets.clip_words(SEEDANCE, live={}) == 160
    assert len(clip.split()) <= ON_SEEDANCE.clip
    tss._no_v2_names(clip)
    # The order: the motion, the emotion, the performance, the gestures, the camera, the clause, the suffix.
    assert (lowered.index("giant toaster") < lowered.index("the mood is shocked") < lowered.index("speaks with")
            < lowered.index("slow push-in") < clip.index(prompting.IDENTITY_KEEPS))


def test_the_staging_turns_faces_and_held_props_are_said_as_gestures():
    """T1 v2's staging entry (facing, expression) becomes the character's
    turn and face; a prop a framed character holds is in its hands."""
    staged = tpl._plan("medium_single", tpl.SUBJECTS, camera_motion="push_in",
                       staging=[{"subject": "@char_captain_obvious", "position": "left", "facing": "the toaster",
                                 "expression": "wide-eyed disbelief"}])
    clip = _clip(staged)
    assert f"Gestures: {TALL} turns toward the toaster, face wide-eyed disbelief." in clip

    plan = tss._v2_plans()["s02"][0]
    scene = next(scene for scene in tss._v2_script()["scenes"] if scene["scene_id"] == "s02")
    clip = shots.resolve_shot(plan, scene=scene, entities=tss.ENTITIES, style_lock=tss.FRUIT_DRAMA,
                              consistency_mode="references", v2=True, budgets=ON_SEEDANCE)["video_prompt"]
    assert len(clip.split()) <= 160 and prompting.IDENTITY_KEEPS in clip, clip
    # Two framed speakers speak in turn; each staged one turns where it faces, its expression on the face.
    assert "speak in turn, mouths moving on the words, faces and brows carrying the emotion" in clip
    assert "Gestures: the anthropomorphic mango turns toward the stool, face smug; the anthropomorphic kiwi turns " \
           "toward the curtain, face tense." in clip

    # A prop a framed character holds (the ledger's possessions, else the look's where_when) is in its hands.
    frame = [("char_a", {}), ("char_b", {})]
    handles = {"char_a": "the kiwi", "char_b": "the mango"}
    phone = {"prop_id": "prop_phone", "look": {"where_when": [{"holder_char_id": "char_b"}]}}
    said = shots._gestures(frame, [("prop_phone", phone)], (), handles, {"prop_phone": "the phone"}, lambda t: t)
    assert said == "Gestures: the mango's hands work the phone"
    held = shots._gestures(frame, [("prop_phone", phone)], (), handles, {"prop_phone": "the phone"}, lambda t: t,
                           ledger={"char_a": {"possessions": ["prop_phone"]}})
    assert held == "Gestures: the kiwi's hands work the phone"
    assert shots._gestures(frame, [], (), handles, {}, lambda t: t) == ""


@pytest.mark.parametrize("lines,expected", [
    # No line in the shot: the framed characters act the moment out.
    ([], f"{TALL} and {SHORT} act the moment out with clear gestures, stepping back, eyes widening"),
    # A narrator's line (no one in frame speaks): both react to it.
    ([{"line_id": "l09", "speaker": "narrator", "text": "Il ment.", "emotion": "scheming"}],
     f"{TALL} and {SHORT} react visibly, narrowing the eyes, a slow smile"),
])
def test_with_no_speaker_in_frame_everyone_in_it_still_performs(lines, expected):
    scene = tss._v2_scene()
    scene["lines"] = lines
    plan = tpl._plan("medium_single", tpl.SUBJECTS, camera_motion="push_in", lines=[1] if lines else [])
    clip = _clip(plan, scene=scene)
    assert expected in clip.lower()
    assert "speaks with the mouth" not in clip


def test_every_style_and_framing_fits_its_budget_and_keeps_the_performance_on_seedance():
    """Every shipped style, every framing, a 25-word clip motion: within 160
    with the performance said; within 80 (no link known) still a prompt."""
    motion = ("@char_captain_obvious slams the newspaper down, jabs a finger at the toaster and shouts while "
              "@char_miss_overthink stumbles back, clutching her clipboard, eyes darting")
    for template_id in templates.list_style_ids():
        lock = templates.load_style(template_id)
        for framing in ("wide_establishing", "medium_single", "medium_two_shot", "close_up", "over_shoulder"):
            plan = tpl._plan(framing, tpl.SUBJECTS, camera_motion="pan_lr", clip_motion=motion)
            wide = _clip(plan, style_lock=lock)
            assert len(wide.split()) <= 160 and "speaks with the mouth moving" in wide, (template_id, framing)
            narrow = _clip(plan, style_lock=lock, budgets=None)
            assert len(narrow.split()) <= prompting.CLIP_V2_MAX_WORDS, (template_id, framing)
            assert narrow.endswith(prompting.as_sentence(lock["motion_rules"]["tier2_prompt_suffix_v2"]))


def test_the_performance_outlives_the_cameras_intent_the_gestures_and_the_emotion():
    """``prompting.layered_clip_prompt``'s ladder: the intent goes first,
    then the gestures, then the emotion, then the performance; only then is
    the motion cut."""
    style = tss.CARTOON_FLAT
    args = dict(subject=TALL, motion=f"{TALL} lifts the monocle, squints and lowers it again",
                camera_phrase=prompting.CAMERA_PHRASES["push_in"], emotion="The mood is shocked",
                performance=f"{TALL} speaks with the mouth moving on the words",
                micro=f"Gestures: {TALL} turns toward the toaster", intent="closing on the emotion")

    def present(prompt):
        return {name for name, marker in (("emotion", "The mood is shocked."), ("performance", "speaks with"),
                                          ("micro", "Gestures:"), ("intent", "closing on the emotion"))
                if marker in prompt}

    seen = []
    for words in range(160, 30, -1):
        prompt = prompting.layered_clip_prompt(style, budget=words, **args)
        kept = present(prompt)
        if not seen or kept != seen[-1]:
            seen.append(kept)
    assert seen == [{"emotion", "performance", "micro", "intent"}, {"emotion", "performance", "micro"},
                    {"emotion", "performance"}, {"performance"}, set()]
    assert prompting._CLIP_DROP_ORDER == ("intent", "micro", "emotion", "performance")


def test_a_v1_shot_keeps_the_old_suffix_and_no_performance():
    """RC-Q1: the legacy clip prompt reads ``tier2_prompt_suffix`` as it
    always did (the byte pins of tests/test_story_video_plan.py hold it);
    the v2 suffix and the performance never reach it."""
    shot = {"shot_id": "sh01", "camera_motion": "push_in", "modifiers": [], "action": "@char_x waves.",
            "video_action": "the cylinder waves.", "video_prompt": None, "negative_prompt": ""}
    prompt, _negative = video_plan.build_video_prompt(shot, tss.FRUIT_DRAMA, tier=2)
    assert tss.FRUIT_DRAMA["motion_rules"]["tier2_prompt_suffix"] in prompt
    assert tss.FRUIT_DRAMA["motion_rules"]["tier2_prompt_suffix_v2"] not in prompt
    resolved = shots.resolve_shot(tpl._plan("medium_single", tpl.SUBJECTS), scene=tss._v2_scene(),
                                  entities=tss._v2_entities(), style_lock=tss.CARTOON_FLAT,
                                  consistency_mode="references")
    assert "video_prompt" not in resolved and "speaks with" not in json.dumps(resolved)


# ================================================================ the styles

def test_every_style_has_a_performance_suffix_for_v2_and_keeps_its_old_one():
    """Each shipped style carries ``tier2_prompt_suffix_v2`` -- no stillness,
    no camera -- beside the unchanged ``tier2_prompt_suffix``; the schemas
    take it; a lock without it ends on the old one; a story's lock built
    before it existed reads its shipped template's (in memory)."""
    old = {"anime": "limited animation feel, hair and cloth sway, slow camera push, no lip flapping",
           "fruit_drama": "subtle natural head and shoulder movement, realistic blinking, breathing, cloth sway, "
                          "camera slowly pushes in, no morphing, no extra characters entering"}
    for path in sorted(STYLES_DIR.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        rules = doc["motion_rules"]
        assert schemas.style_template_errors(doc) == [], path.name
        suffix = rules["tier2_prompt_suffix_v2"]
        assert suffix and suffix != rules["tier2_prompt_suffix"], path.name
        for word in STILL_OR_CAMERA:
            assert not re.search(rf"\b{word}\b", suffix.lower()), (path.name, word)
        if doc["template_id"] in old:
            assert rules["tier2_prompt_suffix"] == old[doc["template_id"]]
    lock = copy.deepcopy(tss.FRUIT_DRAMA)
    assert prompting.clip_motion_suffix(lock) == lock["motion_rules"]["tier2_prompt_suffix_v2"]
    del lock["motion_rules"]["tier2_prompt_suffix_v2"]
    assert prompting.clip_motion_suffix(lock) == lock["motion_rules"]["tier2_prompt_suffix"]
    lock["template_id"] = "fruit_drama"
    filled = episode_common.with_clip_suffix(lock)
    assert filled["motion_rules"]["tier2_prompt_suffix_v2"] == tss.FRUIT_DRAMA["motion_rules"]["tier2_prompt_suffix_v2"]
    assert "tier2_prompt_suffix_v2" not in lock["motion_rules"]  # the lock handed in is never changed
    assert episode_common.with_clip_suffix(dict(lock, template_id="no_such_style")) == dict(
        lock, template_id="no_such_style")


# ================================================================ T1 v2

def test_t1_v2_asks_a_performance_and_a_new_camera_motion():
    _system, user, _schema = tpe._t1_v2()
    assert "one clear physical action for each character in the frame" in user
    assert "the mouth moving on the words" in user
    assert "never 'subtle', 'small' or 'slight'" in user
    assert "never the previous shot's camera_motion" in user
    assert "never the previous shot's framing" in user


def test_the_validator_refuses_a_repeated_camera_motion_and_the_repair_moves_it():
    check = dict(scene=tpe.V2_SCENE, shots_per_scene=(1, 2), modifiers_allowed=tpe.MODIFIERS_ALLOWED,
                 tags_allowed=tpe.TAGS_T1, n_lines=1, names=tpe.NAMES_T1)
    first = tpe._good_t1_v2_shot()
    second = tpe._good_t1_v2_shot(framing="close_up", lines=[])
    reply = {"shots": [first, second]}
    errors = prompts.validate_t1_v2(copy.deepcopy(reply), **check)
    assert any("camera_motion" in e and "repeats the previous shot's camera motion" in e for e in errors), errors
    # The scene's first shot against the episode's shot before it.
    alone = {"shots": [tpe._good_t1_v2_shot()]}
    assert prompts.validate_t1_v2(copy.deepcopy(alone), **check) == []
    assert any("camera_motion" in e for e in prompts.validate_t1_v2(copy.deepcopy(alone), previous_camera="push_in",
                                                                    **check))

    fixed = copy.deepcopy(reply)
    notes = storyboard._repair_t1_v2_reply(fixed, tags_allowed=tpe.TAGS_T1, previous_camera="push_in")
    cameras = [shot["camera_motion"] for shot in fixed["shots"]]
    assert cameras[0] != "push_in" and cameras[1] != cameras[0] and all(c in schemas.CAMERA_MOTIONS for c in cameras)
    assert any("camera_motion" in note for note in notes)
    assert prompts.validate_t1_v2(fixed, previous_camera="push_in", **check) == []
    # T1r v2 (an author's re-plan of one shot) is asked, never refused, for the camera.
    plans = [{"framing": "medium_two_shot", "camera_motion": "push_in", "lines": []},
             {"framing": "close_up", "camera_motion": "hold", "lines": [1]}]
    t1r = {"shot": tpe._good_t1_v2_shot(lines=[1])}
    assert prompts.validate_t1r_v2(t1r, scene=tpe.V2_SCENE, shots=plans, index=1,
                                   modifiers_allowed=tpe.MODIFIERS_ALLOWED, tags_allowed=tpe.TAGS_T1, n_lines=1,
                                   names=tpe.NAMES_T1) == []


def test_a_v2_shots_camera_is_its_own_before_the_styles_push_in_on_peaks():
    """fruit_drama pushes in on every hook, peak and cliffhanger: on a v2
    story the shot's own camera motion (T1 v2's, never the previous shot's)
    comes first; a framing's own rule (the wide shot's pan) still wins; a
    legacy story is as it was."""
    lock = tss.FRUIT_DRAMA
    assert lock["motion_rules"]["tier1"]["by_function"]["peak"] == "push_in"
    assert shots.motion_for("close_up", "hold", "peak", lock)["type"] == "push_in"
    assert shots.motion_for("close_up", "hold", "peak", lock, v2=True)["type"] == "hold"
    assert shots.motion_for("wide_establishing", "hold", "peak", lock, v2=True)["type"] == "pan_lr"
    assert shots.motion_for("close_up", None, "peak", lock, v2=True)["type"] == "push_in"
    scene = dict(tss._v2_scene(), function="peak")
    plans = [(scene, [{"framing": "medium_two_shot", "camera_motion": "hold"},
                      {"framing": "close_up", "camera_motion": "pan_rl"}])]
    moved, _notes = shots.rule_pass(plans, lock, v2=True)
    assert [plan["camera_motion"] for plan in moved[0][1]] == ["hold", "pan_rl"]
    legacy, _notes = shots.rule_pass(plans, lock)
    assert [plan["camera_motion"] for plan in legacy[0][1]] == ["push_in", "push_in"]


# ================================================================ two beats a scene

def _ec_script(store):
    story_id = amb._v2_storyboard_story(store)
    return story_id, episode_common.load_context(store, story_id, 1), eps._script(store, story_id)


def _two_beats(ec, scene, seconds):
    return (scene["function"] in schemas.BODY_FUNCTIONS
            and (len(scene["lines"]) >= 2 or len(scene["characters"]) >= 2)
            and seconds >= 2 * ec.template["min_shot_s"])


def test_a_body_scene_with_two_lines_is_two_beat_shots_and_a_hook_one(store):
    _story_id, ec, script = _ec_script(store)
    scene = copy.deepcopy(next(s for s in script["scenes"] if s["function"] in schemas.BODY_FUNCTIONS))
    line = copy.deepcopy(scene["lines"][0])
    scene["lines"] = [line, dict(copy.deepcopy(line), line_id="l99")]
    seconds = storyboard.expected_scene_seconds(ec, script, scene)
    assert 2 * ec.template["min_shot_s"] <= seconds <= 12, seconds
    assert storyboard.beat_shot_count(ec, script, scene) == (2, 2)
    assert storyboard.beat_shot_count(ec, script, scene, rhythm=False) == (1, 1)  # the clip-length rule alone
    hook = copy.deepcopy(dict(scene, function="hook"))
    assert storyboard.beat_shot_count(ec, script, hook) == (1, 1)
    # Past the cap, a hook is still two (the clip-length rule).
    assert storyboard.beat_shot_count(ec, script, hook, limit_s=seconds - 0.5) == (2, 2)
    # One line and one character: one beat.
    alone = dict(scene, lines=[line], characters=scene["characters"][:1])
    assert storyboard.beat_shot_count(ec, script, alone) == (1, 1)
    # Two lines too short for two shots of min_shot_s (no stored timing: the estimate): one beat.
    brief = dict(scene, scene_id="s99", lines=[dict(line, text="Non.", line_id="l98", timing=None),
                                               dict(line, text="Si.", line_id="l99", timing=None)])
    brief_seconds = storyboard.expected_scene_seconds(ec, script, brief)
    assert brief_seconds < 2 * ec.template["min_shot_s"], brief_seconds
    assert storyboard.beat_shot_count(ec, script, brief) == (1, 1)


def test_the_storyboard_plans_two_beats_for_body_scenes_and_never_plans_an_old_storyboard_again(store, monkeypatch):
    """A fresh plan: two beat shots for every body scene with two lines or
    two characters long enough for two, one for the rest, consecutive
    cameras never repeated. A storyboard planned before (one beat a scene)
    is complete: a re-run plans nothing, the estimate counts nothing."""
    from clipping.aistory import workflow

    m = eps._new()
    story_id, ec, script = _ec_script(store)
    settings = amb._plan_settings(**tas.FAL)
    llm = eps.FakeLLM(default={"T1v2": tsp.t1_v2_reply})
    eps._run(m.storyboard, store, story_id, llm=llm, step="storyboard", settings=settings)

    script = eps._script(store, story_id)
    ec = episode_common.load_context(store, story_id, 1)
    board = eps._storyboard(store, story_id)
    per_scene = {}
    for shot in board["shots"]:
        per_scene[shot["scene_id"]] = per_scene.get(shot["scene_id"], 0) + 1
    twos = 0
    for call, scene in zip(llm.calls, script["scenes"]):
        seconds = storyboard.expected_scene_seconds(ec, script, scene)
        asked = 2 if seconds > 12 or _two_beats(ec, scene, seconds) else 1
        twos += asked == 2
        assert per_scene[scene["scene_id"]] == asked, (scene["scene_id"], scene["function"], seconds)
        assert ("exactly 2 entries" if asked == 2 else "exactly 1 entry") in call["user"]
    assert twos >= 2
    assert all(shot["duration_s"] >= ec.template["min_shot_s"] for shot in board["shots"])
    # The clips' cameras (the plan's, as T1 v2 gave them) never repeat from one shot to the next.
    cameras = [next(motion for motion, phrase in prompting.CAMERA_PHRASES.items()
                    if prompting.as_sentence(phrase)[:-1] in shot["video_prompt"]) for shot in board["shots"]]
    assert all(a != b for a, b in zip(cameras, cameras[1:])), cameras
    for shot in board["shots"]:
        prompt = shot["video_prompt"]
        assert prompting.IDENTITY_KEEPS in prompt and "small natural" not in prompt
        assert any(said in prompt for said in ("mouth moving on the words", "visibly", "the moment out")), prompt

    # Another story planned one beat a scene (as before DEC-252) is never planned again for the new default.
    other_id = amb._v2_storyboard_story(store)
    with monkeypatch.context() as patch:
        patch.setattr(storyboard, "_two_beats", lambda *args, **kwargs: False)
        eps._run(m.storyboard, store, other_id, llm=eps.FakeLLM(default={"T1v2": tsp.t1_v2_reply}),
                 step="storyboard", settings=settings)
    old = eps._storyboard(store, other_id)
    assert len(old["shots"]) == len(old["scenes"])
    again = eps.FakeLLM(default={"T1v2": tsp.t1_v2_reply})
    eps._run(m.storyboard, store, other_id, llm=again, step="storyboard", settings=settings)
    assert again.calls == []
    other_ec = workflow.episode_context(store, store.get(other_id), 1, step="storyboard")
    assert workflow.storyboard_units(other_ec, env=settings)["scenes"] == []
