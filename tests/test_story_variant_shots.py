"""Appearance variants on the shots (plan 23 stage D5): a character's named
variant ("ghost version") picked per shot -- inherited from its scene's
``states``, overridden by the human per shot -- sends the variant's sheet as
the identity image, appends its delta to the look, reaches J2's sheets, the
brief and the speech prompt; a shot naming an unapproved variant refuses its
keyframe with one sentence.

**The byte-identity record.** ``tests/fixtures/aistory_variants/
before_d5.json`` holds the outputs of :func:`_identity_cases` captured from
``main`` at 9d01548, before variants existed: every v2 resolution of the
shots fixtures (three-sheet and two-view, with and without the continuity
slot), and a whole native-speech episode's keyframe request parts (prompt,
hash, references), clip request parts (prompt, hash), brief references and
J2 sheets, in references mode with every image on disk. It is never
re-recorded: a story without variants must give exactly these (a keyframe
hash or a clip hash that moves makes a stored image or clip stale, and a
gencache key that moves buys it again), so a difference is a bug in the
change, not a new pin.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_native_speech_plan as nsp
import test_story_shots as tss
import test_story_storyboard_props as tsp
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_clip_estimate import timings_path  # noqa: F401 - the measured clip timings under tmp_path

NOW = tas.NOW
FAST = nsp.FAST
FIXTURE = Path(__file__).parent / "fixtures" / "aistory_variants" / "before_d5.json"
TALL, SHORT = "char_captain_obvious", "char_miss_overthink"
PLACE_TAG = "#place_clocktown:day"

# The looks the native-speech story's characters are given (the fixture's characters have none).
LOOKS = {
    eps.KIWILO: tss._v2_look(build="small round kiwi body", silhouette="fuzzy brown oval", face="wide green eyes",
                             hair="short brown fuzz", skin_material="fuzzy brown kiwi skin", height_cm=120,
                             palette=["brown", "green"], presentation="a young woman",
                             wardrobe_sets=[{"id": "daily", "context": "every day",
                                             "items": "green sundress, straw sandals"}]),
    eps.MANGELLA: tss._v2_look(build="tall pear-shaped mango body", silhouette="smooth golden teardrop",
                               face="sharp amber eyes", hair="leafy green crown", skin_material="glossy mango skin",
                               height_cm=170, palette=["gold", "red"], presentation="an adult woman",
                               wardrobe_sets=[{"id": "daily", "context": "every day",
                                               "items": "red silk blouse, gold hoops"}]),
    eps.BROCCOLIA: tss._v2_look(build="stocky broccoli body", silhouette="green tree-like crown",
                                face="small dark eyes", hair="dense green florets",
                                skin_material="matte green stalk", height_cm=160, palette=["green"],
                                presentation="an adult man",
                                wardrobe_sets=[{"id": "daily", "context": "every day",
                                                "items": "white tank top, swim shorts"}]),
}


# ================================================================ the byte-identity record

def _pure_cases() -> dict:
    """Every v2 resolution of the shots fixtures (``shots.resolve_shot``):
    the framings, one and two characters, the set alone, with and without
    the continuity slot, three-sheet and two-view identity images."""
    from clipping.aistory import shots

    plans = {
        "two_shot": ("medium_two_shot", [TALL, SHORT]),
        "close_tall": ("close_up", [TALL]),
        "ecu_short": ("extreme_close_up", [SHORT]),
        "single_short": ("medium_single", [SHORT]),
        "wide": ("wide_establishing", [TALL, SHORT]),
        "set_alone": ("wide_establishing", []),
    }
    out = {}
    for sheet_mode in (None, "two_view"):
        entities = tss._v2_entities()
        if sheet_mode:
            entities["sheet_mode"] = sheet_mode
        for name, (framing, chars) in plans.items():
            for continuity in (False, True):
                plan = {"framing": framing, "action": tss._V2_ACTION,
                        "subjects": [f"@{cid}" for cid in chars] + [PLACE_TAG], "lines": [1],
                        "camera_motion": "hold", "modifiers": []}
                resolved = shots.resolve_shot(plan, scene=tss._v2_scene(), entities=copy.deepcopy(entities),
                                              style_lock=tss.CARTOON_FLAT, consistency_mode="references", v2=True,
                                              continuity=continuity)
                out[f"{sheet_mode or 'three_sheet'}:{name}:{'cont' if continuity else 'first'}"] = resolved
    return out


def _speech_story(store, *, sheet_mode=None) -> str:
    """The native-speech fixture story with looks, in references mode, every
    sheet, plate and prop image on disk, its storyboard planned and both
    documents approved; *sheet_mode* set before planning."""
    from clipping.aistory.steps import storyboard

    story_id = nsp.native_story(store)

    def profile(doc):
        doc["generation_profile"]["consistency_mode"] = "references"
        if sheet_mode:
            doc["generation_profile"]["sheet_mode"] = sheet_mode

    store.update(story_id, profile, now=NOW)
    for cid, look in LOOKS.items():
        doc = store.read_entity(story_id, "characters", cid)
        doc["look"] = copy.deepcopy(look)
        if sheet_mode is None:
            doc["refs"]["expressions"] = {"name": "expressions.png", "consistency": "references",
                                          "source": "fal/seedream-4.5-edit", "seed": 7, "created_at": NOW}
        store.write_entity(story_id, "characters", doc, now=NOW)
        tsp._plant_image(store, story_id, "characters", cid, "portrait.jpg", data=f"portrait {cid}".encode())
        if sheet_mode is None:
            tsp._plant_image(store, story_id, "characters", cid, "expressions.png",
                             data=f"expressions {cid}".encode())
    for pid in (eps.PARLOIR, eps.PISCINE):
        for name in ("variant_day.jpg", "variant_night.jpg"):
            tsp._plant_image(store, story_id, "places", pid, name, data=f"{pid} {name}".encode())
    storyboard.build_fast(store, story_id, 1, now=NOW, on_log=lambda _line: None)
    tas._approve(store, story_id)
    return story_id


def _relative(store, story_id, path):
    return os.path.relpath(path, store.story_dir(story_id)) if path else path


def _episode_cases(store, *, sheet_mode=None) -> dict:
    """A whole episode's keyframe and clip request parts, brief references
    and J2 sheets (module docstring)."""
    from clipping.aistory.steps import assets, brief, clips

    story_id = _speech_story(store, sheet_mode=sheet_mode)
    ec = tas._ec(store, story_id)
    script = eps._script(store, story_id)
    board = tas._board(store, story_id)
    shots_out = {}
    for shot in board["shots"]:
        parts = assets.request_parts(ec, shot, note=None, link=None)
        clip = clips.clip_request_parts(ec, shot, script, tier=3, flags={}, link=FAST)
        shots_out[shot["shot_id"]] = {
            "image_prompt": shot["image_prompt"], "video_prompt": shot.get("video_prompt"),
            "reference_images": shot["reference_images"],
            "keyframe": {"prompt": parts["prompt"], "hash": parts["hash"], "kind": parts["kind"],
                         "references": [_relative(store, story_id, path) for path in parts["references"]],
                         "missing": parts["missing"]},
            "clip": {"prompt": clip["prompt"], "hash": clip["hash"], "native_audio": clip["native_audio"]},
            "brief": [{key: ref[key] for key in ("kind", "label", "path")}
                      for ref in brief.references(ec, script, shot)],
        }
    context = assets.keyframe_context(ec, board)
    return {"shots": shots_out,
            "j2_sheets": {cid: _relative(store, story_id, path) for cid, path in sorted(context.sheets.items())},
            "j2_of": {shot["shot_id"]: [name for name, _path in context.sheets_of(ec, shot)]
                      for shot in board["shots"]}}


def _identity_cases(store_factory) -> dict:
    """:func:`_pure_cases` and :func:`_episode_cases` in both sheet modes
    (*store_factory()* gives a fresh store each)."""
    return {"pure": _pure_cases(),
            "episode_three_sheet": _episode_cases(store_factory()),
            "episode_two_view": _episode_cases(store_factory(), sheet_mode="two_view")}


def test_a_story_without_variants_resolves_requests_and_hashes_byte_for_byte_as_before(tmp_path):
    """The byte-identity record (module docstring): with no variant anywhere,
    every keyframe prompt, hash and reference, every clip prompt and hash,
    every brief reference and J2 sheet is what ``main`` gave before D5."""
    counter = iter(range(10))

    def factory():
        return eps.StoryStore(tmp_path / f"outputs{next(counter)}", on_log=lambda line: None)

    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert json.loads(json.dumps(_identity_cases(factory))) == recorded


# ================================================================ a story whose kiwi has a ghost version

GHOST = {"label": "Ghost version", "delta_text": "a translucent pale-blue glowing ghost, feet fading into mist"}
GHOST_REF = "characters/char_kiwilo/refs/portrait_ghost_version.png"
KIWI_BASE = "characters/char_kiwilo/refs/portrait.jpg"


def _variant_story(store, *, approve=True, states=True) -> str:
    """The native-speech story (three sheets, references mode, every image
    on disk) whose kiwi has a "Ghost version" with its sheets made (and
    approved when *approve*), every scene she is in saying she wears it
    (*states*), the storyboard planned from that script and approved."""
    from clipping.aistory import workflow
    from clipping.aistory.steps import storyboard

    story_id = _speech_story(store, sheet_mode="three_sheet")
    workflow.add_variant(store, story_id, eps.KIWILO, GHOST, now=NOW)
    doc = store.read_entity(story_id, "characters", eps.KIWILO)
    for slot in ("portrait", "turnaround", "expressions"):
        name = f"{slot}_ghost_version.png"
        tsp._plant_image(store, story_id, "characters", eps.KIWILO, name, data=f"ghost {slot}".encode())
        doc["variants"][0]["refs"][slot] = {"name": name, "consistency": "references",
                                            "source": "fal/seedream-4.5-edit", "seed": 5, "created_at": NOW}
    store.write_entity(story_id, "characters", doc, now=NOW)
    if approve:
        workflow.approve_variant(store, story_id, eps.KIWILO, "ghost_version", now=NOW)
    if states:
        script = eps._script(store, story_id)
        for scene in script["scenes"]:
            if eps.KIWILO in scene["characters"]:
                scene["states"] = {eps.KIWILO: "ghost_version"}
        store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    storyboard.build_fast(store, story_id, 1, now=NOW, on_log=lambda _line: None)
    tas._approve(store, story_id)
    return story_id


def _framing_kiwi(board):
    return [shot for shot in board["shots"] if f"@{eps.KIWILO}" in shot["subject_tags"]]


def test_the_shots_of_a_scene_with_states_inherit_the_variant_and_send_its_sheet_with_the_delta(store):
    story_id = _variant_story(store)
    board = tas._board(store, story_id)
    kiwi_shots = _framing_kiwi(board)
    assert kiwi_shots
    for shot in board["shots"]:
        if shot in kiwi_shots:
            assert shot["variants"] == {eps.KIWILO: "ghost_version"}
            assert KIWI_BASE not in shot["reference_images"]
            assert any(ref.startswith("characters/char_kiwilo/refs/") and "_ghost_version." in ref
                       for ref in shot["reference_images"]), shot["reference_images"]
            assert "now a translucent pale-blue glowing ghost, feet fading into mist" in shot["image_prompt"]
        else:
            assert "variants" not in shot
            assert "ghost" not in shot["image_prompt"]
    from clipping.aistory import schemas

    assert schemas.storyboard_errors(board) == []


def test_the_reference_choice_follows_the_variants_own_sheets():
    """``_reference_images_v2`` on a variant view: its portrait as the
    identity image, its expressions on a close-up, and a slot the variant
    has not made sends nothing -- never the base's sheet."""
    from clipping.aistory import shots

    entities = tss._v2_entities()
    tall = entities["characters"][TALL]
    tall["variants"] = [{"variant_id": "ghost", "label": "Ghost", "delta_text": "a glowing ghost",
                         "refs": {"portrait": tss._ref("portrait_ghost.jpg"), "turnaround": None,
                                  "expressions": tss._ref("expressions_ghost.jpg")},
                         "source": "human", "created_at": NOW, "approved_at": NOW}]

    def refs(framing, variants):
        plan = {"framing": framing, "action": tss._V2_ACTION, "subjects": [f"@{TALL}", f"@{SHORT}", PLACE_TAG],
                "lines": [1], "camera_motion": "hold", "modifiers": [], "variants": variants}
        return shots.resolve_shot(plan, scene=tss._v2_scene(), entities=entities, style_lock=tss.CARTOON_FLAT,
                                  consistency_mode="references", v2=True)

    wide = refs("medium_two_shot", {TALL: "ghost"})
    assert wide["reference_images"][0] == f"characters/{TALL}/refs/portrait_ghost.jpg"
    assert f"characters/{TALL}/refs/turnaround.jpg" not in wide["reference_images"]
    assert f"characters/{SHORT}/refs/turnaround.jpg" in wide["reference_images"]
    assert "now a glowing ghost" in wide["image_prompt"]
    close = refs("close_up", {TALL: "ghost"})
    assert close["reference_images"][0] == f"characters/{TALL}/refs/expressions_ghost.jpg"
    # No variants: exactly the base resolution (the byte-identity record holds the rest).
    plain = refs("medium_two_shot", {})
    assert plain["reference_images"][0] == f"characters/{TALL}/refs/portrait.jpg"
    assert "ghost" not in plain["image_prompt"]


def test_j2_judges_the_variant_against_its_own_sheet_and_says_its_delta(store):
    from clipping.aistory.steps import assets, judge

    story_id = _variant_story(store)
    ec = tas._ec(store, story_id)
    board = tas._board(store, story_id)
    context = assets.keyframe_context(ec, board)
    shot = _framing_kiwi(board)[0]
    sheets = dict(context.sheets_of(ec, shot))
    path = sheets["Kiwilo (Ghost version)"]
    assert path.endswith("portrait_ghost_version.png")
    assert "Kiwilo" not in sheets
    assert "now a translucent pale-blue glowing ghost" in judge.keyframe_brief(ec, shot)


def test_the_brief_lists_the_variant_sheet_with_its_label(store):
    from clipping.aistory.steps import brief

    story_id = _variant_story(store)
    ec = tas._ec(store, story_id)
    script = eps._script(store, story_id)
    shot = _framing_kiwi(tas._board(store, story_id))[0]
    labels = {ref["label"]: ref["path"] for ref in brief.references(ec, script, shot)}
    assert labels["Kiwilo (Ghost version) — character sheet (portrait)"] == GHOST_REF


def test_the_speech_prompt_carries_the_delta_and_only_then_moves_the_clip_hash(store):
    from clipping.aistory.steps import clips

    story_id = _variant_story(store)
    ec = tas._ec(store, story_id)
    script = eps._script(store, story_id)
    board = tas._board(store, story_id)
    texts = {line["line_id"]: line for scene in script["scenes"] for line in scene["lines"]}
    shot = next(shot for shot in _framing_kiwi(board)
                if shot.get("speaks") and texts[shot["lines"][0]]["speaker"] == eps.KIWILO)
    worn = clips.clip_request_parts(ec, shot, script, tier=3, flags={}, link=FAST)
    assert ", now a translucent pale-blue glowing ghost, feet fading into mist, " in worn["prompt"]
    base = clips.clip_request_parts(ec, {k: v for k, v in shot.items() if k != "variants"}, script, tier=3, flags={},
                                    link=FAST)
    assert "ghost" not in base["prompt"] and base["hash"] != worn["hash"]


def test_the_human_overrides_a_shots_variant_and_its_keyframe_goes_stale_on_purpose(store):
    from clipping.aistory import shots, workflow
    from clipping.aistory.steps import assets

    story_id = _variant_story(store)
    shot = _framing_kiwi(tas._board(store, story_id))[0]
    sid = shot["shot_id"]
    before = assets.request_parts(tas._ec(store, story_id), shot, note=None, link=None)["hash"]

    written = workflow.patch_storyboard(store, story_id, 1,
                                        {"shots": [{"shot_id": sid, "variants": {eps.KIWILO: None}}]}, now=NOW)
    back = next(item for item in written["shots"] if item["shot_id"] == sid)
    assert back["variants"] == {}, "the base look on purpose: kept as the shot's own"
    assert KIWI_BASE in back["reference_images"] and "ghost" not in back["image_prompt"]
    assert written["approved_at"] is None
    assert assets.request_parts(tas._ec(store, story_id), back, note=None, link=None)["hash"] != before

    again = workflow.patch_storyboard(store, story_id, 1,
                                      {"shots": [{"shot_id": sid, "variants": {eps.KIWILO: "ghost_version"}}]}, now=NOW)
    worn = next(item for item in again["shots"] if item["shot_id"] == sid)
    assert worn["variants"] == {eps.KIWILO: "ghost_version"}
    assert worn["image_prompt"] == shot["image_prompt"] and worn["reference_images"] == shot["reference_images"]

    for variants, words in (({eps.KIWILO: "burned"}, "is not one of Kiwilo's variants"),
                            ({"char_nobody": "ghost_version"}, "is not a character shot")):
        with pytest.raises(workflow.WorkflowError) as caught:
            workflow.patch_storyboard(store, story_id, 1, {"shots": [{"shot_id": sid, "variants": variants}]}, now=NOW)
        assert caught.value.code == workflow.INVALID
        assert words in str(caught.value), str(caught.value)

    # A re-plan keeps the human's choice: the variants are the shot's own record, like its assets.
    kept = shots._carried_shot(dict(back), {k: v for k, v in shot.items() if k != "variants"})
    assert kept["variants"] == {}


def test_a_shot_naming_an_unapproved_variant_refuses_its_keyframe_with_one_sentence(store):
    from types import SimpleNamespace

    from clipping.aistory import shots
    from clipping.aistory.steps import assets

    story_id = _variant_story(store, approve=False)
    ec = tas._ec(store, story_id)
    shot = _framing_kiwi(tas._board(store, story_id))[0]
    reason = shots.variant_refusal(shot, ec.entities["characters"])
    assert reason == (f"shot {shot['shot_id']} shows Kiwilo as 'Ghost version', a variant not approved yet: make its "
                      "sheets and approve it (variant:char_kiwilo:ghost_version), or set the shot back to the base "
                      "look")
    host = assets._Assets(SimpleNamespace(), ec, tools=SimpleNamespace(time_fn=lambda: 0.0))
    with pytest.raises(assets.ShotFailed) as caught:
        host.make_image(shot, seed=1, note=None)
    assert str(caught.value) == reason
    unknown = dict(shot, variants={eps.KIWILO: "burned"})
    assert "a variant Kiwilo does not have" in shots.variant_refusal(unknown, ec.entities["characters"])
    plain = {k: v for k, v in shot.items() if k != "variants"}
    assert shots.variant_refusal(plain, ec.entities["characters"]) is None


# ================================================================ E1v3's character states

def test_e1v3_offers_the_states_block_only_with_approved_variants_and_the_script_keeps_them():
    import test_story_prompts_v3 as v3

    from clipping.aistory import prompts

    plain = v3._e1v3()
    assert v3._e1v3(variants=None) == plain and v3._e1v3(variants=[]) == plain
    offered = [{"char_id": "char_nude", "name": "Nude", "variants": [{"variant_id": "unveiled", "label": "Unveiled"}]}]
    _system, user, schema = v3._e1v3(variants=offered)
    assert user.startswith(plain[1]) and "- char_nude (Nude): unveiled = Unveiled" in user
    assert prompts.E1V3_STATES_LINE in user
    scene = schema["properties"]["scenes"]["items"]
    assert "states" in scene["required"] and scene["properties"]["states"]["items"]["properties"]["variant_id"][
        "enum"] == ["unveiled"]

    reply = v3._e1v3_reply()
    for stub in reply["scenes"]:
        stub["states"] = []
    reply["scenes"][-1]["states"] = [{"char_id": "char_nude", "variant_id": "unveiled"}]
    check = dict(ep=1, template=v3.CONFRONTATION, episode_defaults=dict(v3.DEFAULTS, max_places=1),
                 cast_ids=["char_rouge", "char_nude"], places={"place_hall": ["day"]}, prop_ids=[])
    assert prompts.validate_e1_v3(reply, variants=offered, **check) == []
    wrong = copy.deepcopy(reply)
    wrong["scenes"][0]["characters"] = ["char_rouge"]
    wrong["scenes"][0]["states"] = [{"char_id": "char_nude", "variant_id": "unveiled"}]
    assert any("is not one of this scene's characters" in error
               for error in prompts.validate_e1_v3(wrong, variants=offered, **check))
    twice = copy.deepcopy(reply)
    twice["scenes"][1]["states"] = [{"char_id": "char_nude", "variant_id": "unveiled"}] * 2
    assert any("is listed twice" in error for error in prompts.validate_e1_v3(twice, variants=offered, **check))
    # Without variants a reply carrying states is refused by the plain schema (nothing else changed).
    assert prompts.validate_e1_v3(reply, **check)


def test_the_script_step_offers_approved_variants_and_writes_the_scenes_states(store):
    from types import SimpleNamespace

    from clipping.aistory.steps import episode_common
    from clipping.aistory.steps import script as script_step

    story_id = _variant_story(store, approve=False, states=False)
    ec = episode_common.load_context(store, story_id, 1)
    assert script_step.e1_variants(ec, ec.cast) == [], "a variant not approved yet is never offered"
    from clipping.aistory import workflow

    workflow.approve_variant(store, story_id, eps.KIWILO, "ghost_version", now=NOW)
    ec = episode_common.load_context(store, story_id, 1)
    assert script_step.e1_variants(ec, ec.cast) == [
        {"char_id": eps.KIWILO, "name": "Kiwilo",
         "variants": [{"variant_id": "ghost_version", "label": "Ghost version"}]}]
    plain = SimpleNamespace(story={"generation_profile": {"pipeline": "v2"}})
    assert script_step.e1_variants(plain, ec.cast) == [], "no sheet mode, no switch: no block"

    script = eps._script(store, story_id)
    reply = {"title": script["title"], "scenes": [
        {"function": scene["function"], "place_id": scene["place_id"], "time_variant": scene["time_variant"],
         "characters": scene["characters"], "props": scene["props"], "summary": scene["summary"],
         "emotion": scene["emotion"], "target_duration_s": scene["target_duration_s"],
         "states": ([{"char_id": eps.KIWILO, "variant_id": "ghost_version"}] if eps.KIWILO in scene["characters"]
                    else [])}
        for scene in script["scenes"]]}
    trial = copy.deepcopy(script)
    script_step.apply_e1(ec, trial, reply)
    for scene in trial["scenes"]:
        if eps.KIWILO in scene["characters"]:
            assert scene["states"] == {eps.KIWILO: "ghost_version"}
        else:
            assert "states" not in scene
    assert episode_common.trial_errors(ec, trial) == []


def test_the_action_style_says_the_delta_with_the_variant_characters_anchor(store):
    """Plan 23 D6's action prompts name a character by its colour/species
    anchor at every mention: a character wearing a variant in the shot says
    its delta with it (``shots.worn_anchors``), in the speaking and the
    silent clips alike; without variants the anchors are D6's own."""
    from clipping.aistory import media_policy, shots
    from clipping.aistory.steps import clips

    story_id = _variant_story(store)
    store.update(story_id, lambda doc: doc["generation_profile"].update(prompt_style="action"), now=NOW)
    ec = tas._ec(store, story_id)
    assert media_policy.action_prompts(ec.story)
    script = eps._script(store, story_id)
    board = tas._board(store, story_id)
    texts = {line["line_id"]: line for scene in script["scenes"] for line in scene["lines"]}
    delta = "(now a translucent pale-blue glowing ghost, feet fading into mist)"
    speaking = next(shot for shot in _framing_kiwi(board)
                    if shot.get("speaks") and texts[shot["lines"][0]]["speaker"] == eps.KIWILO)
    worn = clips.clip_request_parts(ec, speaking, script, tier=3, flags={}, link=FAST)
    assert delta in worn["prompt"]
    assert f"Audio: only {shots.worn_anchors(ec.entities['characters'], speaking['variants'])[eps.KIWILO]}'s voice" \
        in worn["prompt"]
    base = clips.clip_request_parts(ec, {k: v for k, v in speaking.items() if k != "variants"}, script, tier=3,
                                    flags={}, link=FAST)
    assert "ghost" not in base["prompt"] and base["hash"] != worn["hash"]
    assert shots.worn_anchors(ec.entities["characters"], None) == shots.character_anchors(ec.entities["characters"])
    assert shots.worn_anchors(ec.entities["characters"], {}) == shots.character_anchors(ec.entities["characters"])


# ================================================================ the image brief (D5 follow-up)

IMAGE_BRIEF_FIXTURE = Path(__file__).parent / "fixtures" / "aistory_variants" / "image_brief_before_d5follow.json"
GHOST_ID = "ghost_version"


def _image_brief_cases(store_factory) -> dict:
    """The image brief (its JSON, and its markdown's sha256, with the
    episode's keyframes; the random story id written ``<story_id>``) of the
    variant-free native-speech story in each sheet mode: absent (variants
    off), ``three_sheet`` and ``two_view`` (variants on, none added).
    *store_factory()* gives a fresh store each."""
    from clipping.aistory.steps import brief

    out = {}
    for name, mode in (("absent", None), ("three_sheet", "three_sheet"), ("two_view", "two_view")):
        store = store_factory()
        story_id = _speech_story(store, sheet_mode=mode)
        doc = brief.image_brief(store, store.get(story_id), ec=tas._ec(store, story_id))
        markdown = brief.render_image_markdown(doc).replace(story_id, "<story_id>")
        out[name] = {"json": json.loads(json.dumps(doc).replace(story_id, "<story_id>")),
                     "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest()}
    return out


def test_a_story_without_variants_gets_the_image_brief_byte_for_byte_as_before(tmp_path):
    """The image brief's record (``image_brief_before_d5follow.json``,
    captured from ``main`` at cc1d734 before variant sheets entered the
    brief): with no variant, every entry, label, prompt, size and upload
    slot -- and the markdown -- is what it was. Re-recorded by plan 26 H1
    alone, a brief-layer pin (never a hash): stage 4a, each keyframe's
    prompt is the image master and scene template before the same core,
    with its ``fit``; stage 4b, each sheet drawn from a look the series,
    the style and its character before the same core, and every entity
    entry its ``fit``."""
    counter = iter(range(10))

    def factory():
        return eps.StoryStore(tmp_path / f"outputs{next(counter)}", on_log=lambda line: None)

    recorded = json.loads(IMAGE_BRIEF_FIXTURE.read_text(encoding="utf-8"))
    assert json.loads(json.dumps(_image_brief_cases(factory))) == recorded


def test_the_image_brief_lists_each_variant_sheet_with_its_prompt_reference_and_upload_slot(store):
    """A variant's sheets follow their character's own: ``kind: sheet``
    with ``variant_id``/``variant_label``, ``id`` ``<cid>:<vid>``, the
    shot brief's label, the prompt the app would ask (an edit of the base
    portrait, named as the ``reference``), the variant slot's state and the
    sheet route's ``&variant=`` upload slot; the base entries are untouched."""
    from clipping.aistory import imaging, refimages, workflow
    from clipping.aistory.steps import brief

    story_id = _speech_story(store, sheet_mode="three_sheet")
    before = brief.image_brief(store, store.get(story_id))
    workflow.add_variant(store, story_id, eps.KIWILO, GHOST, now=NOW)
    doc = store.read_entity(story_id, "characters", eps.KIWILO)
    tsp._plant_image(store, story_id, "characters", eps.KIWILO, f"portrait_{GHOST_ID}.png", data=b"ghost portrait")
    doc["variants"][0]["refs"]["portrait"] = {"name": f"portrait_{GHOST_ID}.png", "consistency": "references",
                                              "source": "manual/upload", "seed": None, "created_at": NOW}
    store.write_entity(story_id, "characters", doc, now=NOW)
    story = store.get(story_id)
    after = brief.image_brief(store, story)

    worn = [entry for entry in after["images"] if entry.get("variant_id")]
    assert [entry["slot"] for entry in worn] == ["portrait", "turnaround", "expressions"]
    lock = imaging.read_lock(store, story_id, error=refimages.RefImageError)
    character = store.read_entity(story_id, "characters", eps.KIWILO)
    names = refimages._entity_names(store, story_id)
    for entry in worn:
        which = entry["slot"]
        assert (entry["kind"], entry["entity"], entry["id"]) == ("sheet", "characters", f"{eps.KIWILO}:{GHOST_ID}")
        assert entry["variant_label"] == "Ghost version"
        assert entry["label"] == f"Kiwilo (Ghost version) — character sheet ({which})"
        # Plan 26 H1: the brief's prompt ends with the core the variant sheet is asked, after the master.
        assert entry["prompt"].endswith(refimages.variant_prompt(story, character, character["variants"][0], which,
                                                                 env={}, lock=lock, names=names))
        assert entry["prompt"].startswith("SERIES:") and entry["fit"]["limit"] is None
        assert entry["reference"]["path"] == KIWI_BASE
        assert entry["upload_slot"] == (f"/api/stories/{story_id}/cast/{eps.KIWILO}/sheet?which={which}"
                                        f"&variant={GHOST_ID}")
        assert entry["state"] == ("uploaded" if which == "portrait" else "missing")
    # Right after the kiwi's own sheets; take them away and the brief is what it was.
    kiwi = [index for index, entry in enumerate(after["images"]) if entry["id"].startswith(eps.KIWILO)]
    assert kiwi == list(range(kiwi[0], kiwi[0] + 6)) and all(after["images"][i].get("variant_id") for i in kiwi[3:])
    assert [entry for entry in after["images"] if not entry.get("variant_id")] == before["images"]
    assert after["counts"] == {"total": before["counts"]["total"] + 3,
                               "uploaded": before["counts"]["uploaded"] + 1,
                               "missing": before["counts"]["missing"] + 2}
    markdown = brief.render_image_markdown(after)
    assert "Kiwilo (Ghost version) — character sheet (turnaround) — missing" in markdown
    assert f"`{KIWI_BASE}`" in markdown
    # Variants switched off (no sheet mode): the variant record stays, the brief does not list it.
    store.update(story_id, lambda d: d["generation_profile"].pop("sheet_mode"), now=NOW)
    off = brief.image_brief(store, store.get(story_id))
    assert not [entry for entry in off["images"] if entry.get("variant_id")]
