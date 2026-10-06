"""Plan 28 F6 (DEC-305 section 5, the human: "strict rules to avoid
consistency problems, and all details"): time variants and wardrobe enforced.

1. A keyframe whose scene's time of day has no plate is refused in a plain
   sentence naming the place and the time -- never drawn on the day plate in
   silence; the assets step makes the plates an episode lacks before its
   keyframes (booked like any plate, judged like any plate) and its shots
   then carry the scene's own plate.
2. A shot showing a character in a variant not approved, or in an outfit
   the story so far gives it that its look does not have, is refused before
   any keyframe is bought (the storyboard's approval and the assets step).
3. The Handoff's reference of a character whose outfit in the episode is
   not the one its sheet shows says so.

The episode is ``tests/test_story_keyframe_consistency.py``'s v2 references
episode. Offline and hermetic. Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import json

import pytest

import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_keyframe_consistency as tkc
import test_story_keyframe_gate as kg
from clipping.aistory import shots
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_keyframe_gate import unpaced  # noqa: F401 -- the free Gemini tier's pacing lifted

NOW = eps.NOW
PISCINE = eps.PISCINE
J3_PASS = json.dumps({"passed": True, "issues": []})


def _vision():
    """Answers J3 (the sheet judge) and J2 (the keyframe judge), each passing."""
    return kg.FakeVision(lambda request: J3_PASS if "every check above holds" in request.prompt else kg.PASS)


def _without_night_plate(store, story_id):
    """La Piscine's night plate gone (never made); the board resolved again
    from the places as they are and approved, as a storyboard built before
    the rule would be."""
    from clipping.aistory.steps import script as script_step

    doc = store.read_entity(story_id, "places", PISCINE)
    doc["time_variants"]["night"] = None
    store.write_entity(story_id, "places", doc, now=NOW)
    ec = tas._ec(store, story_id)
    board = shots.refresh_prompts(tas._board(store, story_id), eps._script(store, story_id), entities=ec.entities,
                                  style_lock=ec.style_lock, consistency_mode="references", v2=True,
                                  ledger=script_step.ledger_of(ec))
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    tas._approve(store, story_id)


def _night_at_the_pool(store, story_id):
    script = eps._script(store, story_id)
    scenes = {scene["scene_id"] for scene in script["scenes"]
              if scene["place_id"] == PISCINE and scene["time_variant"] == "night"}
    return [shot for shot in tas._board(store, story_id)["shots"] if shot["scene_id"] in scenes]


SENTENCE = ("La Piscine de la Trahison has no night plate, and a keyframe is never drawn on another one: run the "
            "assets step again (it makes the missing plates first), or make it on the places step (regenerate "
            f"'place:{PISCINE}:image:night')")


def test_a_keyframe_whose_scene_has_no_plate_for_its_time_is_refused_never_drawn_on_the_day_plate(store, tmp_path):
    from types import SimpleNamespace

    from clipping.aistory.steps import assets

    story_id = tkc._layered(store, tmp_path)
    _without_night_plate(store, story_id)
    shot = _night_at_the_pool(store, story_id)[0]
    # The silent fallback the rule closes: the stored shot names the day plate for a night scene.
    assert any(ref.endswith("variant_day.jpg") and PISCINE in ref for ref in shot["reference_images"])

    host = assets._Assets(SimpleNamespace(), tas._ec(store, story_id), tools=SimpleNamespace(time_fn=lambda: 0.0))
    with pytest.raises(assets.ShotFailed) as caught:
        host.make_image(shot, seed=1, note=None)
    assert str(caught.value) == SENTENCE


def test_the_assets_step_makes_the_missing_plates_first_and_the_night_shots_carry_them(store, tmp_path):
    story_id = tkc._layered(store, tmp_path)
    _without_night_plate(store, story_id)
    edit = tas.FakeImage()

    summary, log = tkc._run(store, story_id, edit=edit, vision=_vision())

    assert "🗺 Before the keyframes: the missing plate La Piscine de la Trahison (night), made now." in log
    names = [request.extra["name"] for request in edit.requests]
    assert names[0] == "variant_night" and names.count("variant_night") == 1  # before any keyframe, once
    place = store.read_entity(story_id, "places", PISCINE)
    plate = place["time_variants"]["night"]
    assert plate and place["sheet_checks"]["night"]["passed"] is True
    assert place["approved_at"]  # the place stays approved: a plate was added, nothing it approved changed
    for shot in _night_at_the_pool(store, story_id):
        assert f"places/{PISCINE}/refs/{plate['name']}" in shot["reference_images"], shot["reference_images"]
        assert not any(ref.endswith("variant_day.jpg") and PISCINE in ref for ref in shot["reference_images"])
    assert summary["complete"] is True
    # The storyboard stays approved and current: a run again neither refuses nor makes a plate.
    edit2 = tas.FakeImage()
    _summary, log2 = tkc._run(store, story_id, edit=edit2, vision=_vision())
    assert "variant_night" not in [request.extra["name"] for request in edit2.requests]
    assert not any(line.startswith("🗺 Before the keyframes") for line in log2)


def test_an_outfit_the_look_does_not_have_is_refused_before_any_keyframe_is_bought(store, tmp_path):
    from clipping.aistory import workflow

    story_id = tkc._layered(store, tmp_path, looks=True, gala=True)
    doc = store.read_entity(story_id, "characters", eps.KIWILO)
    doc["look"]["wardrobe_sets"] = doc["look"]["wardrobe_sets"][:1]  # the ledger still says "gala"
    store.write_entity(story_id, "characters", doc, now=NOW)
    ec = tas._ec(store, story_id)
    from clipping.aistory.steps import script as script_step

    board = shots.refresh_prompts(tas._board(store, story_id), eps._script(store, story_id), entities=ec.entities,
                                  style_lock=ec.style_lock, consistency_mode="references", v2=True,
                                  ledger=script_step.ledger_of(ec))
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    first = next(shot for shot in board["shots"] if f"@{eps.KIWILO}" in shot["subject_tags"])
    sentence = (f"Shot {first['shot_id']} shows Kiwilo in the outfit 'gala' the story so far gives Kiwilo, but "
                "Kiwilo's look has no such outfit: add it to Kiwilo's look, or correct the story's continuity -- a "
                "shot is never drawn in another outfit.")

    with pytest.raises(workflow.WorkflowError) as caught:
        workflow.approve_storyboard(store, story_id, 1, now=NOW)
    assert str(caught.value) == f"Episode 1's storyboard cannot be approved. {sentence}"

    tas._approve(store, story_id)  # approved before the rule
    edit = tas.FakeImage()
    with pytest.raises(Exception) as caught:
        tkc._run(store, story_id, edit=edit, vision=_vision())
    assert str(caught.value) == f"Episode 1's keyframes are not drawn. {sentence}"
    assert edit.requests == []


def test_a_variant_not_approved_is_refused_by_the_storyboard_s_approval():
    from clipping.aistory.steps import assets

    characters = {"char_kiwilo": {"name": "Kiwilo", "variants": [
        {"variant_id": "ghost_version", "label": "Ghost version", "approved_at": None}]}}
    ec = type("EC", (), {"story": {"generation_profile": {"pipeline": "v2"}}, "entities": {"characters": characters},
                         "ep": 1})()
    board = {"shots": [{"shot_id": "sh03", "subject_tags": ["@char_kiwilo"],
                        "variants": {"char_kiwilo": "ghost_version"}}]}
    import unittest.mock as mock

    with mock.patch("clipping.aistory.steps.script.ledger_of", return_value=None):
        assert assets.wardrobe_refusal(ec, board) == (
            "Shot sh03 shows Kiwilo as 'Ghost version', a variant not approved yet: make its sheets and approve it "
            "(variant:char_kiwilo:ghost_version), or set the shot back to the base look.")


def test_the_handoff_reference_says_the_outfit_when_the_episode_s_set_differs(store, tmp_path):
    from clipping.aistory.steps import brief

    story_id = tkc._layered(store, tmp_path, looks=True, gala=True)
    ec = tas._ec(store, story_id)
    script = eps._script(store, story_id)
    shot = next(shot for shot in tas._board(store, story_id)["shots"] if f"@{eps.KIWILO}" in shot["subject_tags"])
    labels = [ref["label"] for ref in brief.references(ec, script, shot) if ref["kind"] == "sheet"]
    kiwi = next(label for label in labels if label.startswith("Kiwilo"))
    assert kiwi == ("Kiwilo — character sheet (portrait); the sheet shows another outfit, here: "
                    f"{tkc.GALA_ITEMS}")
    others = [label for label in labels if not label.startswith("Kiwilo")]
    assert all("another outfit" not in label for label in others)
