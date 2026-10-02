"""A crowded v2 keyframe (phase 7, the W-mid walk's leftovers, 2026-10-02).

The walk's keyframe prompts ran 234 and 243 words against
``KEYFRAME_V2_MAX_WORDS`` (220): the budget ladder of ``shots._layered``
shortens the looks, the place and the rendering, but never the reference
roles (about 12 words per image, up to 10 images) nor the props. A crowded
shot -- three characters with their sheets, the set and two props, a long
action -- measured 308 words. And the prop's own name was stripped from
inside its descriptor ("lifts the golden *the object* on a thin chain"):
names were swept from the action after its tags became descriptor handles,
and a prop's name is usually the noun of its descriptor.

v2 only: a legacy story resolves byte-identically (RC-Q1,
``tests/test_story_shots.py::test_legacy_story_resolves_byte_identical``).
Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import test_story_shots as tss
from clipping.aistory import prompting, shots


def _crowded():
    entities = tss._v2_entities()
    fig = tss._v2_char(
        "char_grand_mere", "Grand-mere Figue", "An old purple fig with a knitted shawl and tiny spectacles",
        ["knitted shawl", "walking cane"],
        tss._v2_look(build="round squat fig body", silhouette="plump teardrop with a shawl",
                     face="crinkled smile, tiny spectacles", hair="a curled stem on top",
                     skin_material="soft wrinkled purple skin", height_cm=60, palette=["purple", "cream"],
                     wardrobe_sets=[{"id": "daily", "context": "every day",
                                     "items": "cream knitted shawl, wooden cane"}]))
    entities["characters"][fig["char_id"]] = fig
    for pid, descriptor, name, material, colour in (
            ("prop_monocle", "a golden monocle on a thin chain", "Monocle", "polished brass", "gold"),
            ("prop_basket", "a wicker basket of ripe figs", "Basket", "woven wicker", "honey brown")):
        prop = tss._prop(pid, descriptor, name=name)
        prop["look"] = {"scale_cm": 30, "material": material, "colour": colour,
                        "scale_phrase": "fits in one hand", "where_when": []}
        entities["props"][pid] = prop
    line = tss._line("l04", "char_captain_obvious", "That object is a giant toaster, and it is ticking.",
                     emotion="shocked")
    line["delivery"] = "Slow, monotone, and absolutely certain, then a sudden sharp gasp at the end."
    scene = tss._scene("s01", "hook", place_id="place_clocktown",
                       characters=["char_captain_obvious", "char_miss_overthink", "char_grand_mere"],
                       props=["prop_monocle", "prop_basket"], lines=[line], emotion="shocked")
    action = ("@char_captain_obvious lifts %prop_monocle to his eye while @char_miss_overthink drops %prop_basket "
              "and @char_grand_mere points her cane at the ticking giant toaster in #place_clocktown:day, everyone "
              "frozen in shock as figs roll across the cobbles toward the newsstand and the clock tower strikes noon")
    plan = {"framing": "medium_two_shot", "action": action, "camera_motion": "hold", "modifiers": [], "lines": [1],
            "subjects": ["@char_captain_obvious", "@char_miss_overthink", "@char_grand_mere", "%prop_monocle",
                         "%prop_basket", "#place_clocktown:day"],
            "clip_motion": "@char_captain_obvious raises %prop_monocle; %prop_basket tips over",
            "staging": [
                {"subject": "@char_captain_obvious", "position": "left", "facing": "right", "expression": "stunned"},
                {"subject": "@char_miss_overthink", "position": "centre", "facing": "camera",
                 "expression": "panicked"},
                {"subject": "@char_grand_mere", "position": "right", "facing": "left", "expression": "stern"}]}
    return plan, scene, entities


def _resolve(plan, scene, entities, *, v2=True):
    return shots.resolve_shot(plan, scene=scene, entities=entities, style_lock=tss.CARTOON_FLAT,
                              consistency_mode="references", v2=v2)


def test_a_crowded_keyframe_fits_the_word_cap_and_keeps_every_layer():
    plan, scene, entities = _crowded()
    resolved = _resolve(plan, scene, entities)
    prompt = resolved["image_prompt"]

    assert len(prompt.split()) <= prompting.KEYFRAME_V2_MAX_WORDS, len(prompt.split())
    assert len(resolved["reference_images"]) == 9
    # Every image is still said, in the order sent (compact roles), and the
    # beat, the staging, the camera and the constraints are all there.
    for number in range(1, 10):
        assert f"{number} " in prompt.split("The tall yellow")[0], number
    assert "ticking giant toaster" in prompt
    assert "On the left, the tall yellow geometric cylinder" in prompt
    assert "Camera: medium group shot" in prompt
    assert prompt.endswith(prompting.CONSTRAINTS_KEYFRAME)


def test_a_descriptive_prop_name_is_kept_inside_its_own_handle():
    plan, scene, entities = _crowded()
    resolved = _resolve(plan, scene, entities)

    for text in (resolved["image_prompt"], resolved["video_prompt"], resolved["video_action"]):
        assert "the object" not in text, text
    assert "lifts the golden monocle on a thin chain" in resolved["video_action"]
    assert "drops the wicker basket of ripe figs" in resolved["video_action"]
    # A proper name is still never said (spec 2.3).
    for name in ("Captain", "Obvious", "Overthink", "Figue", "Clocktown"):
        assert name not in resolved["image_prompt"] and name not in resolved["video_prompt"], name


def test_a_proper_name_written_in_the_action_is_still_stripped():
    plan, scene, entities = _crowded()
    plan["action"] = "Captain Obvious lifts %prop_monocle in #place_clocktown:day while Basket rolls away"
    resolved = _resolve(plan, scene, entities)
    assert "Captain Obvious" not in resolved["video_action"]
    assert resolved["video_action"].startswith("the character lifts the golden monocle on a thin chain")
    # "Basket" is the basket's own noun: said, not swapped for "the object".
    assert "while Basket rolls away" in resolved["video_action"]


def test_a_keyframe_that_fits_is_unchanged_by_the_extra_rungs():
    """The two-character shot of test_story_shots fits on the first rung:
    full role sentences, props and looks as before."""
    resolved = tss._v2_resolve("medium_single", ["@char_captain_obvious", "@char_miss_overthink",
                                                 "#place_clocktown:day"])
    assert resolved["image_prompt"].startswith("Image 1 is the tall yellow geometric cylinder's reference")


def test_the_legacy_path_still_strips_every_name_from_the_action():
    """RC-Q1: v1 keeps its sweep exactly (stored legacy prompts must not move)."""
    plan, scene, entities = _crowded()
    legacy = _resolve(plan, scene, entities, v2=False)
    assert "the golden the object on a thin chain" in legacy["video_action"]
