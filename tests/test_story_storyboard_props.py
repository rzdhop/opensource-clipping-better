"""Tests for the storyboard step's v2 prop-image gate (AI Story phase 7,
stage 3c, A11): :func:`clipping.aistory.steps.storyboard.require_prop_images`
refuses to plan shots -- before any LLM call -- while a scene references a
prop with no approved image yet. A new-object prop a v2 episode's script
just created (``steps/script.py``'s ``new_objects`` handling, stage 3c) has
no image until the places step draws it; the storyboard must wait rather
than plan shots around an entity that will never render. A legacy story is
never refused for this (RC-M1): v1 never creates a prop with no image a
scene can already reference (DEC-171 keeps its props always an existing,
already-drawn id).

Reuses ``tests/test_story_episode_steps.py``'s fixtures (the same
``_continuity_story``/``_script_llm``/``FakeLLM`` machinery every other
phase-3 step test is built on), the established cross-module test import
pattern in this suite (see ``test_aistory_render_partial.py``,
``test_story_assets_pacing.py``).

Stdlib + pytest (DEC-012). Offline and hermetic (``hermetic``, imported
below): no key, chain, cap or limit of the machine reaches a test, no
request leaves the process, nothing is written outside ``tmp_path``.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from clipping.aistory import prompts, steps
from clipping.aistory.steps import storyboard

import test_story_episode_steps as eps
import test_story_prompts_episode as tpe
from test_story_episode_steps import hermetic, store  # noqa: F401 -- fixtures

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
NEW_PROP_ID = "prop_giant_toaster"
NEW_PROP_NAME = "Giant Toaster"
NEW_PROP_ONE_LINE = "The runaway toaster chasing the whole cast."


def _plant_image(store, story_id, kind, eid, name=".".join(("image", "jpg")), data=PNG) -> str:
    """A real file on disk for *eid*'s ``refs/<name>`` (``entities.has_file``
    needs more than a document reference -- the file itself, never through a
    symlink): the same plant-a-file pattern ``tests/test_story_refimages.py``
    uses."""
    src = Path(store.outputs_dir).parent / f"plant-{kind}-{eid}-{name}"
    src.write_bytes(data)
    return store.write_media(story_id, kind, eid, name, str(src))


def _v2_script_with_new_prop(store):
    """A v2, ready story (episode 1 already written and its memory approved,
    :func:`test_story_episode_steps._continuity_story`) whose episode 2
    script references a brand-new object -- ``new_objects`` in E1's reply,
    the scene tagging it ``%prop_giant_toaster`` -- resolved by the script
    step to a real prop stub with no image yet."""
    story_id = eps._continuity_story(store, v2=True)
    # The fixture prop (PHONE) carries an ``image`` reference but, like every
    # other fixture in this suite, no real file behind it (nothing needed
    # ``entities.has_file`` before this stage) -- plant one so only the new
    # object the test is about is ever missing its image.
    _plant_image(store, story_id, "props", eps.PHONE)
    e1 = eps._e1_ep2_paying({"s04": [eps.HOOK_PHONE]})
    e1["new_objects"] = [{"name": NEW_PROP_NAME, "one_line": NEW_PROP_ONE_LINE, "owner_char_id": None}]
    rising = e1["scenes"][(["s00"] + eps.ALL_SCENES).index("s03")]
    assert rising["props"] == []  # the base fixture's s03 (rising) has no prop
    rising["props"] = [f"%{NEW_PROP_ID}"]
    # Phase 7 stage 6a (DEC-230/231), re-pinned on purpose: the v2 fixture (no repeated line, J1 answered).
    llm = eps._script_llm(v2=True, E1=[e1], E3=[eps.E3_EP2], E4=[eps.E4_PASSED])
    eps._run(eps._new().script, store, story_id, llm=llm, ep=2)

    script = eps._script(store, story_id, 2)
    assert eps._scene(script, "s03")["props"] == [NEW_PROP_ID]
    prop = store.read_entity(story_id, "props", NEW_PROP_ID)
    assert prop["image"] is None  # not drawn yet: the places step's job

    # Creating the prop leaves it unapproved, which drops the story's status
    # below "ready" (store.write_entity re-folds the "places" group approval
    # from every place and prop, DEC-123's own invariant) -- the real
    # ``check_episode_preconditions`` gate would then refuse the whole
    # episode with a generic "not ready" message before this stage's own,
    # more specific one is ever reached. To isolate what this stage adds
    # (the storyboard naming the prop and its cost, not the pre-existing
    # readiness gate), the fixture restores "ready" the same way
    # ``_ready_story`` seeds every other entity's approval: writing
    # ``approved_at`` on the document directly, bypassing
    # ``workflow.approve_entity`` (which would itself refuse an unimaged
    # prop) -- a human could reach the same state by approving out of order,
    # or a future workflow may not require it; either way the storyboard
    # must still catch an unapproved-for-render prop on its own.
    prop["approved_at"] = eps.NOW
    store.write_entity(story_id, "props", prop, now=eps.NOW)
    assert store.get(story_id)["status"] == "ready"
    return story_id


def test_storyboard_waits_for_new_prop_image(store):
    m = eps._new()
    story_id = _v2_script_with_new_prop(store)

    message, _ = eps._failed(m.storyboard, store, story_id, llm=eps.FakeLLM(), step="storyboard", ep=2)
    assert NEW_PROP_NAME in message
    assert "no approved image yet" in message
    assert "$0.040" in message  # re-pinned (DEC-235): pricing.py fal/seedream-4.5, the quality prop role's own link
    assert "places step" in message

    # The fast path (no LLM call at all) refuses too, before it plans anything.
    with pytest.raises(steps.StepFailed) as caught:
        m.storyboard.build_fast(store, story_id, 2, now=eps.NOW, on_log=eps.Log())
    assert NEW_PROP_NAME in str(caught.value)
    assert eps._storyboard(store, story_id, 2) is None  # nothing was written

    # Once the prop has an approved image, the storyboard proceeds -- the
    # fast path, no call, same as any other complete episode.
    _plant_image(store, story_id, "props", NEW_PROP_ID)
    prop = store.read_entity(story_id, "props", NEW_PROP_ID)
    prop["image"] = {"name": "image.jpg", "consistency": "base", "source": "gemini/nano-banana-2", "seed": 7,
                     "created_at": eps.NOW}
    prop["descriptor"] = "A chrome runaway toaster the size of a car"
    store.write_entity(story_id, "props", prop, now=eps.NOW)

    board = m.storyboard.build_fast(store, story_id, 2, now=eps.NOW, on_log=eps.Log())
    assert sorted(board["scenes"]) == ["s00"] + eps.ALL_SCENES
    for shot in board["shots"]:
        assert NEW_PROP_NAME not in shot["image_prompt"]  # name-free, like every other entity (RC)


def test_a_legacy_story_never_refuses_for_a_missing_prop_image(store):
    """A v1 story is never refused by this gate, whatever a prop's image
    looks like: DEC-171 means a legacy episode's script can only ever
    reference a prop that already existed, already drawn, before the script
    was written -- but the gate itself is unconditionally v2-only, proven
    here even against a prop whose image reference was removed by hand."""
    m = eps._new()
    story_id = eps._written_script(store)
    prop = store.read_entity(story_id, "props", eps.PHONE)
    prop["image"] = None
    store.write_entity(story_id, "props", prop, now=eps.NOW)

    board = m.storyboard.build_fast(store, story_id, 1, now=eps.NOW, on_log=eps.Log())

    assert sorted(board["scenes"]) == eps.ALL_SCENES


# ======================================================== T1 v2 (phase 7 stage 4, DEC-227)

def t1_v2_reply(call):
    """A T1 v2 answer: as many beat shots as the ask names, the scene's lines
    split across them, each with its motion and staging."""
    import re

    tags = call["schema"]["properties"]["shots"]["items"]["properties"]["subjects"]["items"]["enum"]
    place = next(tag for tag in tags if tag.startswith("#"))
    who = [tag for tag in tags if tag.startswith("@")] or [place]
    n_lines = eps._numbered_lines(call["user"])
    count = int(re.search(r"Give 'shots': exactly (\d) entr", call["user"]).group(1))
    framings = ["close_up", "medium_two_shot"] if "frame one of this scene's shots close_up" in call["user"] \
        else ["medium_two_shot", "close_up"]
    if "\n- medium_two_shot / " in call["user"].split("Previous shots", 1)[-1][-200:]:
        framings.reverse()
    # DEC-252: never the previous shot's camera motion (the last row of "Previous shots", then each other).
    before = re.findall(r"\n- [a-z_]+ / ([a-z_]+)", call["user"].split("Previous shots", 1)[-1]) \
        if "Previous shots" in call["user"] else []
    cameras = ["hold", "push_in"] if before and before[-1] == "push_in" else ["push_in", "hold"]
    cut = (n_lines + 1) // 2 if count == 2 else n_lines
    spans = [list(range(1, cut + 1)), list(range(cut + 1, n_lines + 1))][:count]
    staged = [{"subject": tag, "position": pos, "facing": "the others", "expression": "tense"}
              for tag, pos in zip(who, ("left", "right", "centre", "back")) if tag.startswith("@")]
    return {"shots": [
        {"framing": framings[i % 2], "camera_motion": cameras[i % 2], "modifiers": [],
         "action": " and ".join(who) + " settle the matter, and the stakes rise.",
         "motion": f"{who[0]} steps forward and points while the others turn", "staging": staged,
         "subjects": who, "lines": span} for i, span in enumerate(spans)]}


def two_beats(scene, seconds, *, cap_s=12, min_shot_s=5.0):
    """How many beat shots T1 v2 is asked for *scene* of *seconds*
    (``storyboard.beat_shot_count``): two past the clip's *cap_s*, and --
    DEC-252's rhythm -- two for a body scene with two lines or two
    characters and room for two shots of *min_shot_s*; one otherwise.

    Re-pinned on purpose (plan 28 stage A2, DEC-305): *min_shot_s* is
    serial_60s_v2's own, 5 s since its re-slot (3 s before)."""
    from clipping.aistory import schemas

    rhythm = (scene["function"] in schemas.BODY_FUNCTIONS
              and (len(scene["lines"]) >= 2 or len(scene["characters"]) >= 2) and seconds >= 2 * min_shot_s)
    return 2 if seconds > cap_s or rhythm else 1


def test_a_v2_story_plans_its_shots_with_t1_v2_one_beat_shot_a_scene(store):
    """The storyboard step on a v2 story written on serial_60s_v2: one T1 v2
    call per scene asking one beat shot (two only for a scene past 12 s),
    the stored shots carrying T1 v2's ``clip_motion`` and ``staging``, the
    effective 1-2 range validated (not the style's 2-4).

    DEC-252 re-pin: a body scene with two lines or two characters and room
    for two 3 s shots is asked two beat shots too (:func:`two_beats`)."""
    m = eps._new()
    story_id = eps._written_script(store)
    store.update(story_id, lambda doc: (doc["generation_profile"].update(pipeline="v2"),
                                        doc.update(episode_template_id="serial_60s_v2")), now=eps.NOW)
    script = eps._script(store, story_id)
    script["template_id"] = "serial_60s_v2"
    store.write_episode_doc(story_id, 1, "script.json", script, now=eps.NOW)
    _plant_image(store, story_id, "props", eps.PHONE)  # the v2 prop-image gate (stage 3c)

    llm = eps.FakeLLM(default={"T1v2": t1_v2_reply})
    eps._run(m.storyboard, store, story_id, llm=llm, step="storyboard")

    assert llm.prompts() == ["T1v2"] * len(eps.ALL_SCENES)
    board = eps._storyboard(store, story_id)
    script = eps._script(store, story_id)
    per_scene = {}
    for shot in board["shots"]:
        per_scene[shot["scene_id"]] = per_scene.get(shot["scene_id"], 0) + 1
        assert shot["clip_motion"].startswith("@char_") and shot["staging"]
        assert "@" not in shot["video_prompt"] and shot["prompt_layout"] == "layered_v1"
    for call, scene in zip(llm.calls, script["scenes"]):
        asked = two_beats(scene, script["timing"]["scenes"][scene["scene_id"]]["duration_s"])
        assert per_scene[scene["scene_id"]] == asked, scene["scene_id"]
        assert ("exactly 2 entries" if asked == 2 else "exactly 1 entry") in call["user"]
        assert "- motion (English)" in call["user"]
    assert all(shot["duration_s"] <= 12 for shot in board["shots"])

    # Re-planning one of its shots goes through T1r v2 and keeps a motion and a staging.
    def t1r_v2_reply(call):
        import json
        import re

        found = re.search(r"- shot \d+ <- replace this one: ([a-z_]+) / ([a-z_]+), lines (\[[0-9, ]*\])",
                          call["user"])
        return {"shot": {"framing": found.group(1), "camera_motion": found.group(2), "modifiers": [],
                         "action": "@char_kiwilo turns the vote around in one sentence.",
                         "motion": "@char_kiwilo slams a hand on the table", "subjects": ["@char_kiwilo"],
                         "staging": [{"subject": "@char_kiwilo", "position": "centre", "facing": "the camera",
                                      "expression": "defiant"}],
                         "lines": json.loads(found.group(3))}}

    replan = eps.FakeLLM(T1rv2=[t1r_v2_reply])
    eps._regenerate(store, story_id, "shot:1:sh02:plan", llm=replan)
    assert replan.prompts() == ["T1rv2"]
    # Walk follow-up F5: the shot planned again is a new shot in sh02's place
    # (a fresh id, never a used one); every other shot keeps its id.
    after = eps._storyboard(store, story_id)["shots"]
    sh02 = after[1]
    assert sh02["shot_id"] == f"sh{len(board['shots']) + 1:02d}"
    others = [s["shot_id"] for s in board["shots"][:1] + board["shots"][2:]]
    assert [s["shot_id"] for s in after[:1] + after[2:]] == others
    assert sh02["clip_motion"] == "@char_kiwilo slams a hand on the table" and "slams a hand" in sh02["video_prompt"]


# ============================================= T1 v2 tag repair (phase 7, fix B)
#
# Found when the walk's storyboard failed twice on one scene: gemini keeps
# naming a @char/%prop/#place tag in a shot's action/motion/staging that it
# forgot to also list in that shot's own ``subjects``, which
# ``prompts.validate_t1_v2`` then refuses ("tag '@char_x' is used but not
# listed in subjects"). ``storyboard._repair_t1_v2_reply`` runs before that
# validator (``plan_scene_v2``'s ``validate`` closure) and adds the tag to
# ``subjects`` when it is one the scene actually allows; a tag of a
# character this scene never cast at all is left for the validator, exactly
# as before the repair existed.

def _t1_v2_check(**extra):
    check = dict(scene=tpe.V2_SCENE, shots_per_scene=(1, 2), modifiers_allowed=tpe.MODIFIERS_ALLOWED,
                 tags_allowed=tpe.TAGS_T1 + ["@char_mangella"], n_lines=1,
                 names=dict(tpe.NAMES_T1, char_mangella="Mangella"))
    check.update(extra)
    return check


def test_t1_v2_reply_repairs_an_allowed_tag_missing_from_subjects():
    check = _t1_v2_check()
    shot1 = tpe._good_t1_v2_shot()
    shot2 = tpe._good_t1_v2_shot(
        framing="close_up",
        action="@char_kiwilo confronts @char_mangella by the pool, demanding answers once and for all.",
        lines=[],
        camera_motion="hold",  # DEC-252 re-pin: not shot 1's camera (that repair has its own test)
    )

    # Unrepaired, shot 2's tag is refused as unlisted.
    errors = prompts.validate_t1_v2({"shots": [copy.deepcopy(shot1), copy.deepcopy(shot2)]}, **check)
    assert any("@char_mangella" in e and "not listed in subjects" in e for e in errors)

    # deepcopy: the reply's own lists must not alias shot1/shot2's, or the repair's in-place
    # append would also mutate the fixtures the assertions below compare against.
    reply = {"shots": [copy.deepcopy(shot1), copy.deepcopy(shot2)]}
    added = storyboard._repair_t1_v2_reply(reply, tags_allowed=check["tags_allowed"])
    assert reply["shots"][1]["subjects"] == shot2["subjects"] + ["@char_mangella"]
    assert added and "shot 2" in added[0] and "@char_mangella" in added[0]
    assert reply["shots"][0]["subjects"] == shot1["subjects"]  # shot 1 had nothing to repair

    # Repaired, the same reply now passes.
    assert prompts.validate_t1_v2(reply, **check) == []


def test_t1_v2_reply_repairs_an_insert_prop_shot_without_a_prop():
    """DEC-262 (the live hook scene listed no prop and every link's reply was
    refused): an insert_prop shot with no prop tag among its subjects gets
    the scene's first prop when the scene has one, else becomes a close_up;
    either way the validator then passes."""
    check = _t1_v2_check()
    shot = tpe._good_t1_v2_shot(framing="insert_prop")
    shot["subjects"] = [t for t in shot["subjects"] if not t.startswith("%")]
    assert any("needs a prop tag" in e for e in prompts.validate_t1_v2({"shots": [copy.deepcopy(shot)]}, **check))

    with_prop = {"shots": [copy.deepcopy(shot)]}
    props = [t for t in check["tags_allowed"] if t.startswith("%")]
    assert props
    added = storyboard._repair_t1_v2_reply(with_prop, tags_allowed=check["tags_allowed"])
    assert with_prop["shots"][0]["framing"] == "insert_prop" and props[0] in with_prop["shots"][0]["subjects"]
    assert any("listed no prop, now on " + props[0] in line for line in added)
    assert not any("needs a prop tag" in e for e in prompts.validate_t1_v2(with_prop, **check))

    no_prop_tags = [t for t in check["tags_allowed"] if not t.startswith("%")]
    without = {"shots": [copy.deepcopy(shot)]}
    added = storyboard._repair_t1_v2_reply(without, tags_allowed=no_prop_tags)
    assert without["shots"][0]["framing"] == storyboard.INSERT_PROP_FALLBACK_FRAMING == "close_up"
    assert any("in a scene with no prop, now 'close_up'" in line for line in added)
    assert not any("insert_prop" in e for e in prompts.validate_t1_v2(without, **dict(check, tags_allowed=no_prop_tags)))


def test_t1_v2_reply_leaves_a_tag_outside_the_scene_for_the_validator():
    check = _t1_v2_check()
    shot = tpe._good_t1_v2_shot(
        action="@char_kiwilo glances toward @char_broccolia offscreen, then turns back to the pool.",
    )
    reply = {"shots": [copy.deepcopy(shot)]}
    added = storyboard._repair_t1_v2_reply(reply, tags_allowed=check["tags_allowed"])
    assert added == [] and reply["shots"][0]["subjects"] == shot["subjects"]

    errors = prompts.validate_t1_v2(reply, **check)
    assert any("@char_broccolia" in e and "not listed in subjects" in e for e in errors)


# Plan 25 DEC-302 shows the writer the human-named casts by name, so a reply writes "Rida" or "Marie-Jeanne"
# in action/motion; the validator refused it and the step paid for a retry per scene.
# ``_repair_t1_v2_reply(names=...)`` maps a cast name to its tag first.

def test_t1_v2_reply_maps_a_cast_name_to_its_tag_instead_of_a_retry():
    names = dict(tpe.NAMES_T1, char_rida="Rida", char_marie_jeanne="Marie-Jeanne")
    tags = tpe.TAGS_T1 + ["@char_rida", "@char_marie_jeanne"]
    check = _t1_v2_check(tags_allowed=tags, names=names)
    shot = tpe._good_t1_v2_shot(
        action="Rida leans over the pool rail while Marie-Jeanne's pen taps %prop_phone, daring @char_kiwilo to talk.",
        motion="rida steps back as MARIE-JEANNE lifts %prop_phone slowly",
    )
    errors = prompts.validate_t1_v2({"shots": [copy.deepcopy(shot)]}, **check)
    assert any("names the character 'Rida'" in e and ".action" in e for e in errors)
    assert any("names the character 'Marie-Jeanne'" in e and ".motion" in e for e in errors)

    reply = {"shots": [copy.deepcopy(shot)]}
    added = storyboard._repair_t1_v2_reply(reply, tags_allowed=tags, names=names)
    fixed = reply["shots"][0]
    assert fixed["action"] == ("@char_rida leans over the pool rail while @char_marie_jeanne's pen taps %prop_phone, "
                               "daring @char_kiwilo to talk.")
    assert fixed["motion"] == "@char_rida steps back as @char_marie_jeanne lifts %prop_phone slowly"
    assert fixed["subjects"] == shot["subjects"] + ["@char_rida", "@char_marie_jeanne"]
    assert "shot 1: 'Rida' -> '@char_rida' in action" in added
    assert "shot 1: 'Marie-Jeanne' -> '@char_marie_jeanne' in action" in added
    assert "shot 1: 'Rida' -> '@char_rida' in motion" in added
    assert "shot 1: 'Marie-Jeanne' -> '@char_marie_jeanne' in motion" in added
    assert any("'@char_rida' added to subjects" in line for line in added)
    assert prompts.validate_t1_v2(reply, **check) == []


def test_t1_v2_reply_still_refuses_a_name_that_is_not_a_scene_cast_member():
    names = dict(tpe.NAMES_T1, char_broccolia="Broccolia")
    check = _t1_v2_check(names=names)
    shot = tpe._good_t1_v2_shot(
        action="@char_kiwilo glances toward Broccolia offscreen, then turns back to the pool.",
    )
    reply = {"shots": [copy.deepcopy(shot)]}
    added = storyboard._repair_t1_v2_reply(reply, tags_allowed=check["tags_allowed"], names=names)
    assert added == [] and reply["shots"][0]["action"] == shot["action"]
    errors = prompts.validate_t1_v2(reply, **check)
    assert any("names the character 'Broccolia'" in e for e in errors)


# DEC-305 section 5 (plan 28 stage F4): a shot that carries a character's line must show that character. The repair
# adds the speaker's tag to ``subjects`` (and says so); a scene character no shot shows is the validator's, a retry.

def test_t1_v2_reply_adds_the_speaker_of_a_carried_line_to_subjects_and_says_so():
    scene = tpe._F4_SCENE
    check = _t1_v2_check(scene=scene, n_lines=3)
    shot = tpe._good_t1_v2_shot(lines=[1, 2, 3])
    errors = prompts.validate_t1_v2({"shots": [copy.deepcopy(shot)]}, **check)
    assert any("'@char_mangella'" in e and "line 2" in e for e in errors)

    reply = {"shots": [copy.deepcopy(shot)]}
    added = storyboard._repair_t1_v2_reply(reply, tags_allowed=check["tags_allowed"], lines=scene["lines"])
    assert reply["shots"][0]["subjects"] == shot["subjects"] + ["@char_mangella"]
    assert added == ["shot 1: '@char_mangella' speaks line 2, added to subjects"]
    assert prompts.validate_t1_v2(reply, **check) == []

    # No lines given (every earlier caller): nothing added. A narrator or an unknown speaker is never a tag.
    plain = {"shots": [copy.deepcopy(shot)]}
    assert storyboard._repair_t1_v2_reply(plain, tags_allowed=check["tags_allowed"]) == []
    odd = {"shots": [copy.deepcopy(shot)]}
    lines = [{"speaker": "narrator"}, {"speaker": "char_nobody"}, {"speaker": "narrator"}]
    assert storyboard._repair_t1_v2_reply(odd, tags_allowed=check["tags_allowed"], lines=lines) == []


def test_t1_v2_reply_with_a_scene_character_no_shot_shows_is_left_for_a_told_why_retry():
    scene = dict(tpe._F4_SCENE, lines=tpe._F4_SCENE["lines"][:1])
    check = _t1_v2_check(scene=scene, n_lines=1)
    reply = {"shots": [tpe._good_t1_v2_shot()]}
    added = storyboard._repair_t1_v2_reply(reply, tags_allowed=check["tags_allowed"], lines=scene["lines"])
    assert added == [] and "@char_mangella" not in reply["shots"][0]["subjects"]
    assert any("'@char_mangella'" in e and "no shot" in e for e in prompts.validate_t1_v2(reply, **check))


# ============================================= plan 27 stage 3 (the exchange)

def test_a_t1_v2_shot_listing_a_planned_exchange_s_lines_is_valid_and_stays_one_shot():
    """A T1 v2 shot that lists every line of one planned exchange passes the
    repair and the validator as it is, and the native plan
    (``storyboard.speech_plans``' ``shots.speech_shot_plan``, reading the
    scene's ``line_plan.shots``) keeps it ONE speaking shot -- no longer split
    one shot a line."""
    from clipping.aistory import shots

    import test_story_native_speech_plan as nsp

    check = _t1_v2_check(n_lines=2)
    shot = tpe._good_t1_v2_shot(lines=[1, 2], subjects=["@char_kiwilo", "@char_mangella", "%prop_phone",
                                                        "#place_pool:day"])
    reply = {"shots": [copy.deepcopy(shot)]}
    storyboard._repair_t1_v2_reply(reply, tags_allowed=check["tags_allowed"])
    assert prompts.validate_t1_v2(reply, **check) == []
    scene = nsp._with_shots(nsp._scene([("char_kiwilo", "Tu caches la clé."), ("char_mangella", "Et alors ?")],
                                       characters=("char_kiwilo", "char_mangella")), [(8, [1, 2], True)])
    planned = shots.speech_shot_plan(scene, [storyboard.t1_v2_plan(reply["shots"][0])], language="fr")
    assert [(plan["lines"], plan["clip_s"], plan["speakers"]) for plan in planned] == [
        ([1, 2], 8, ["char_kiwilo", "char_mangella"])]
    assert planned[0]["clip_motion"] == shot["motion"]
