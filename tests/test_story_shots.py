"""Tests for clipping.aistory.shots (AI Story phase 3, stage 5; spec 5, 2.8,
6.3, 6.4): turning per-scene shot plans -- T1's reply shape, or the
deterministic fast path -- into a complete, valid ``storyboard_v1`` document.

Fixtures are plain dicts, shaped like the live story ``b1104ec66b05``
(three fruit characters, two places with day/night variants and layout
notes, one prop) but written locally so every test controls its own handle
collisions and reference images. Style locks are loaded with
``templates.load_style`` (spec: a ``style_template_v1`` document has every
prose field a ``style_lock_v1`` document has, so it serves fine as one --
the same convention ``tests/test_aistory_prompting.py`` uses).

Stdlib + pytest only (DEC-012): this file runs in the CI environment.
"""

from __future__ import annotations

import ast
import copy
import sys

import pytest

from clipping.aistory import schemas, shots, templates, timing

EN = "en"
NOW = "2026-09-27T10:00:00+00:00"

FRUIT_DRAMA = templates.load_style("fruit_drama")
FAMILY_3D = templates.load_style("family_3d")
TEMPLATE = templates.load_episode_template("serial_60s_v1")


# ======================================================== 0. portability guard

def test_it_imports_nothing_outside_the_standard_library_and_aistory():
    """RC-P8: shots.py must stay pure. A relative import (``from . import
    x``) or an absolute ``clipping.aistory.x`` import is allowed --
    everything else must resolve inside the standard library."""
    import pathlib

    path = pathlib.Path(shots.__file__)
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
                continue
            if node.module and node.module.split(".")[0] == "clipping":
                continue
            top = (node.module or "").split(".")[0]
            if top not in sys.stdlib_module_names:
                offenders.append(node.module)
    assert offenders == [], f"non-stdlib, non-aistory imports: {offenders}"


# ======================================================== 1. fixtures

def _ref(name, seed=1, consistency="base"):
    return {"name": name, "consistency": consistency, "source": "test", "seed": seed, "created_at": NOW}


def _char(cid, descriptor, signature_items, *, name, portrait="portrait.png", updated_at=NOW):
    return {
        "char_id": cid, "name": name, "descriptor": descriptor, "signature_items": list(signature_items),
        "refs": {
            "portrait": _ref(portrait) if portrait else None,
            "turnaround": None, "expressions": None, "extra": [], "uploads": [],
        },
        "updated_at": updated_at,
    }


def _place(pid, descriptor, layout_notes, *, name, variants, updated_at=NOW):
    return {
        "place_id": pid, "name": name, "descriptor": descriptor, "layout_notes": layout_notes,
        "time_variants": variants, "updated_at": updated_at,
    }


def _prop(pid, descriptor, *, name, image="image.png", updated_at=NOW):
    return {
        "prop_id": pid, "name": name, "descriptor": descriptor,
        "image": _ref(image) if image else None, "updated_at": updated_at,
    }


CHAR_KIWILO, CHAR_MANGELLA, CHAR_BROCCOLIA = "char_kiwilo", "char_mangella", "char_broccolia"
PLACE_PARLOIR, PLACE_PISCINE = "place_parloir", "place_piscine"
PROP_PHONE = "prop_phone"

KIWILO_DESCRIPTOR = "an anthropomorphic kiwi with fuzzy brown skin and bright green flesh visible at the mouth"
KIWILO_ITEMS = ["thin gold chain", "white linen shirt", "left-eyebrow scar"]
MANGELLA_DESCRIPTOR = "an anthropomorphic mango with smooth red-blushed skin and a faint tropical sheen"
MANGELLA_ITEMS = ["gold statement necklace", "emerald green blazer"]
BROCCOLIA_DESCRIPTOR = "an anthropomorphic broccoli with tightly clustered dark green florets and a sturdy stalk"
BROCCOLIA_ITEMS = ["crystal rhinestone headband", "emerald velvet gown"]

CHARACTERS = {
    CHAR_KIWILO: _char(CHAR_KIWILO, KIWILO_DESCRIPTOR, KIWILO_ITEMS, name="Kiwilo", portrait="portrait.png"),
    CHAR_MANGELLA: _char(CHAR_MANGELLA, MANGELLA_DESCRIPTOR, MANGELLA_ITEMS, name="Mangella", portrait="portrait.jpg"),
    CHAR_BROCCOLIA: _char(CHAR_BROCCOLIA, BROCCOLIA_DESCRIPTOR, BROCCOLIA_ITEMS, name="Broccolia", portrait="portrait.jpg"),
}

PARLOIR_DESCRIPTOR = "a dimly lit tropical wooden confession booth with a carved bamboo chair and banana leaf curtains"
PARLOIR_LAYOUT = (
    "A rustic wooden stool sits center. Dried coconut husks hang to the left. A hidden-camera slit is cut "
    "into the wall on the right. Dark green palm leaves cover the back wall."
)
PISCINE_DESCRIPTOR = "a luxurious turquoise swimming pool surrounded by white wooden loungers and glowing tiki torches"
PISCINE_LAYOUT = (
    "Crystal-clear water fills the foreground. Bamboo loungers sit to the left. A tiki bar stands to the "
    "right. Dense jungle foliage closes the background."
)

PLACES = {
    PLACE_PARLOIR: _place(
        PLACE_PARLOIR, PARLOIR_DESCRIPTOR, PARLOIR_LAYOUT, name="Le Parloir des Secrets",
        variants={"day": _ref("variant_day.jpg"), "night": _ref("variant_night.jpg")},
    ),
    PLACE_PISCINE: _place(
        PLACE_PISCINE, PISCINE_DESCRIPTOR, PISCINE_LAYOUT, name="La Piscine de la Trahison",
        variants={"day": _ref("variant_day.jpg"), "dusk": None},
    ),
}

PHONE_DESCRIPTOR = "a coconut shell telephone with glowing tropical flower buttons"

PROPS = {
    PROP_PHONE: _prop(PROP_PHONE, PHONE_DESCRIPTOR, name="Telephone en noix de coco"),
}

ENTITIES = {"characters": CHARACTERS, "places": PLACES, "props": PROPS}


def _line(line_id, speaker, text, *, emotion="neutral"):
    return {
        "line_id": line_id, "speaker": speaker, "text": text, "emotion": emotion, "delivery": "calm",
        "timing": timing.estimated_timing(text, EN),
    }


def _scene(scene_id, function, *, place_id, time_variant="day", characters=None, props=None, lines=None,
          summary="Something happens.", emotion="neutral", target_duration_s=5.0):
    return {
        "scene_id": scene_id, "function": function, "place_id": place_id, "time_variant": time_variant,
        "characters": characters if characters is not None else [], "props": props if props is not None else [],
        "summary": summary, "emotion": emotion, "target_duration_s": target_duration_s,
        "lines": lines if lines is not None else [], "sfx_cues": [], "on_screen_text": None,
        "state": "written", "source": "E2", "rev": 1,
    }


def _build_script():
    """ep 1: hook, 6 body scenes (with lines, one quiet), cliffhanger."""
    s01 = _scene(
        "s01", "hook", place_id=PLACE_PARLOIR, characters=[CHAR_KIWILO, CHAR_MANGELLA], props=[PROP_PHONE],
        lines=[_line("l04", CHAR_KIWILO, "A shocking secret is about to come out.", emotion="shocked")],
        emotion="shocked", target_duration_s=2.5,
    )
    s02 = _scene(
        "s02", "setup", place_id=PLACE_PARLOIR, characters=[CHAR_MANGELLA],
        lines=[_line("l08", CHAR_MANGELLA, "I have been waiting for this all week.")],
        target_duration_s=5.0,
    )
    s03 = _scene(
        "s03", "rising", place_id=PLACE_PISCINE, characters=[CHAR_KIWILO, CHAR_BROCCOLIA],
        lines=[
            _line("l12", CHAR_KIWILO, "You always take the biggest lounger."),
            _line("l13", CHAR_KIWILO, "It is getting a little old."),
            _line("l14", CHAR_BROCCOLIA, "Maybe stop counting my loungers.", emotion="angry"),
        ],
        target_duration_s=6.0,
    )
    s04 = _scene(
        "s04", "peak", place_id=PLACE_PISCINE, characters=[CHAR_KIWILO, CHAR_MANGELLA, CHAR_BROCCOLIA],
        lines=[_line("l16", CHAR_MANGELLA, "The phone is ringing again!", emotion="shocked")],
        emotion="shocked", target_duration_s=6.0,
    )
    s05 = _scene(
        "s05", "turn", place_id=PLACE_PARLOIR, characters=[CHAR_BROCCOLIA],
        lines=[_line("l20", CHAR_BROCCOLIA, "Something is not right here.", emotion="tension")],
        emotion="tension", target_duration_s=5.0,
    )
    s06 = _scene("s06", "setup", place_id=PLACE_PARLOIR, characters=[], lines=[], target_duration_s=4.0)
    s07 = _scene(
        "s07", "rising", place_id=PLACE_PARLOIR, characters=[CHAR_KIWILO],
        lines=[_line("l28", CHAR_KIWILO, "One more secret and I am done.")],
        target_duration_s=5.0,
    )
    s08 = _scene(
        "s08", "cliffhanger", place_id=PLACE_PARLOIR, time_variant="night",
        characters=[CHAR_KIWILO, CHAR_MANGELLA, CHAR_BROCCOLIA],
        lines=[_line("l32", CHAR_KIWILO, "Nobody is leaving this island.", emotion="shocked")],
        emotion="shocked", target_duration_s=3.0,
    )
    scenes = [s01, s02, s03, s04, s05, s06, s07, s08]
    return {
        "$schema": "episode_script_v1", "ep": 1, "title": "Test Episode", "language": EN,
        "template_id": "serial_60s_v1", "hook": {"on_screen_text": None},
        "scenes": scenes,
        "cliffhanger": {"scene_id": "s08", "reveal": "A shocking reveal.", "cut_to_black": True},
        "next_episode_teaser": "A short teaser for next time.",
        "timing": None, "consistency_report": None,
        "approved_anyway": None, "approved_at": None, "rev": 1,
        "created_at": NOW, "updated_at": NOW,
    }


SCRIPT = _build_script()


def test_the_fixture_script_itself_validates():
    assert schemas.episode_script_errors(SCRIPT) == []


# ======================================================== 2. parse_tag

def test_parse_tag_character():
    assert shots.parse_tag("@char_kiwilo") == ("char", "char_kiwilo", None)


def test_parse_tag_prop():
    assert shots.parse_tag("%prop_phone") == ("prop", "prop_phone", None)


def test_parse_tag_place_with_variant():
    assert shots.parse_tag("#place_parloir:night") == ("place", "place_parloir", "night")


@pytest.mark.parametrize("bad", ["char_kiwilo", "@Kiwilo", "#place_parloir", "%", "@char_", "not a tag", ""])
def test_parse_tag_rejects_bad_grammar(bad):
    with pytest.raises(ValueError):
        shots.parse_tag(bad)


# ======================================================== 3. handles

def test_character_handles_worked_example():
    """The task's own worked example: an anthropomorphic X with ... -> the
    anthropomorphic X."""
    handles = shots.character_handles({CHAR_KIWILO: CHARACTERS[CHAR_KIWILO]})
    assert handles[CHAR_KIWILO] == "the anthropomorphic kiwi"


def test_character_handles_are_distinct_for_the_fixture_cast():
    handles = shots.character_handles(CHARACTERS)
    assert handles[CHAR_KIWILO] == "the anthropomorphic kiwi"
    assert handles[CHAR_MANGELLA] == "the anthropomorphic mango"
    assert handles[CHAR_BROCCOLIA] == "the anthropomorphic broccoli"
    assert len(set(handles.values())) == 3


def test_character_handles_collision_falls_back_to_signature_item():
    twins = {
        "char_a": _char("char_a", "an anthropomorphic kiwi with fuzzy brown skin", ["red scarf"], name="A"),
        "char_b": _char("char_b", "an anthropomorphic kiwi with smooth green skin", ["blue hat"], name="B"),
    }
    handles = shots.character_handles(twins)
    assert handles["char_a"] == "the anthropomorphic kiwi wearing red scarf"
    assert handles["char_b"] == "the anthropomorphic kiwi wearing blue hat"


def test_character_handles_still_colliding_appends_cast_order_index():
    triplets = {
        "char_a": _char("char_a", "an anthropomorphic kiwi with fuzzy brown skin", ["gold chain"], name="A"),
        "char_b": _char("char_b", "an anthropomorphic kiwi with smooth green skin", ["gold chain"], name="B"),
        "char_c": _char("char_c", "an anthropomorphic kiwi with dark ridged skin", ["gold chain"], name="C"),
    }
    handles = shots.character_handles(triplets)
    assert handles["char_a"] == "the anthropomorphic kiwi wearing gold chain (1)"
    assert handles["char_b"] == "the anthropomorphic kiwi wearing gold chain (2)"
    assert handles["char_c"] == "the anthropomorphic kiwi wearing gold chain (3)"


def test_character_handles_deterministic():
    assert shots.character_handles(CHARACTERS) == shots.character_handles(CHARACTERS)


def test_prop_handles_collision_skips_straight_to_cast_order_index():
    props = {
        "prop_a": _prop("prop_a", "a coconut shell telephone with a woven cord", name="A"),
        "prop_b": _prop("prop_b", "a coconut shell telephone with a beaded cord", name="B"),
    }
    handles = shots.prop_handles(props)
    assert handles["prop_a"] == "the coconut shell telephone (1)"
    assert handles["prop_b"] == "the coconut shell telephone (2)"


def test_prop_handles_no_collision():
    handles = shots.prop_handles(PROPS)
    assert handles[PROP_PHONE] == "the coconut shell telephone"


# -------------------------------------------- handles on the live descriptors

# The story's own written descriptors (b1104ec66b05, read 2026-09-27), pinned
# here as a golden case. A K1-written descriptor usually opens with a
# comma-separated list of adjectives before its head noun ("A fuzzy, dark
# brown ripe kiwi fruit..."); the handle rule takes the descriptor's first
# sentence, cuts at the earliest of " serving as "/" with "/" wearing "/
# " who "/" that "/";", drops a leading article, then keeps only the text
# after that phrase's LAST comma -- dropping the adjective list, keeping the
# noun -- so the handle still names the species, not just an adjective.

LIVE_KIWILO_DESCRIPTOR = (
    "A fuzzy, dark brown ripe kiwi fruit serving as a human-scale head with expressive carved facial "
    "features. The body is human, wearing a sharp tailored charcoal suit with an open collar and a thin "
    "gold chain."
)
LIVE_BROCCOLIA_DESCRIPTOR = (
    "A dense, dark green cluster of tight broccoli florets serving as a human-scale head with carved "
    "facial features, set on a human body wearing an elegant emerald velvet evening gown with crystal "
    "embellishments."
)
LIVE_MANGELLA_DESCRIPTOR = (
    "A smooth, deep red ripe mango serving as a human-scale head with a subtle tropical flush, featuring "
    "carved expressive eyes and a sharp smirk, set atop an elegant human body wearing a tailored emerald "
    "green pantsuit."
)
LIVE_PHONE_DESCRIPTOR = (
    "A polished half coconut shell shaped like a vintage telephone with glowing tropical flower buttons "
    "and a screen made of woven palm leaves"
)


def test_character_handles_on_the_live_story_descriptors():
    live = {
        "char_kiwilo": _char("char_kiwilo", LIVE_KIWILO_DESCRIPTOR, ["a thin gold chain"], name="Kiwilo"),
        "char_broccolia": _char("char_broccolia", LIVE_BROCCOLIA_DESCRIPTOR, ["a rhinestone headband"], name="Broccolia"),
        "char_mangella": _char("char_mangella", LIVE_MANGELLA_DESCRIPTOR, ["a gold necklace"], name="Mangella"),
    }
    handles = shots.character_handles(live)
    assert handles == {
        "char_kiwilo": "the dark brown ripe kiwi fruit",
        "char_broccolia": "the dark green cluster of tight broccoli florets",
        "char_mangella": "the deep red ripe mango",
    }


def test_prop_handles_on_the_live_story_descriptor():
    live = {"prop_phone": _prop("prop_phone", LIVE_PHONE_DESCRIPTOR, name="Telephone en noix de coco")}
    handles = shots.prop_handles(live)
    assert handles["prop_phone"] == "the polished half coconut shell shaped like a vintage telephone"


def test_worked_example_from_the_spec_unchanged_by_the_2026_09_27_revision():
    handles = shots.character_handles({CHAR_KIWILO: CHARACTERS[CHAR_KIWILO]})
    assert handles[CHAR_KIWILO] == "the anthropomorphic kiwi"


@pytest.mark.parametrize("marker,descriptor,expected", [
    (" serving as ", "A greasy wrench serving as a keychain for the whole crew.", "greasy wrench"),
    (" with ", "A tall lamp with a cracked glass shade in the corner.", "tall lamp"),
    (" wearing ", "A quiet dog wearing a tiny raincoat by the door.", "quiet dog"),
    (" who ", "A stern teacher who never smiles at recess.", "stern teacher"),
    (" that ", "A rusty gate that squeaks every single morning.", "rusty gate"),
    (";", "A cold stove; the kitchen has not been used in years.", "cold stove"),
])
def test_leading_phrase_cuts_at_each_marker(marker, descriptor, expected):
    assert shots._leading_phrase(descriptor) == expected


# ======================================================== 4. resolve_action

CHAR_HANDLES = shots.character_handles(CHARACTERS)
PROP_HANDLES = shots.prop_handles(PROPS)
PLACE_NAMES = {pid: doc["name"] for pid, doc in PLACES.items()}


def test_resolve_action_replaces_character_and_prop_tags():
    text = f"@{CHAR_KIWILO} holds up %{PROP_PHONE} and stares at @{CHAR_MANGELLA}."
    resolved = shots.resolve_action(text, char_handles=CHAR_HANDLES, prop_handles=PROP_HANDLES, place_names=PLACE_NAMES)
    assert resolved == (
        "the anthropomorphic kiwi holds up the coconut shell telephone and stares at "
        "the anthropomorphic mango."
    )


def test_resolve_action_replaces_place_tag_with_the_setting():
    text = f"Wide view of #{PLACE_PARLOIR}:night, empty."
    resolved = shots.resolve_action(text, char_handles=CHAR_HANDLES, prop_handles=PROP_HANDLES, place_names=PLACE_NAMES)
    assert resolved == "Wide view of the setting, empty."


def test_resolve_action_unknown_character_tag_raises():
    with pytest.raises(ValueError):
        shots.resolve_action("@char_nobody speaks.", char_handles=CHAR_HANDLES, prop_handles=PROP_HANDLES,
                             place_names=PLACE_NAMES)


def test_resolve_action_unknown_prop_tag_raises():
    with pytest.raises(ValueError):
        shots.resolve_action("%prop_nothing sits there.", char_handles=CHAR_HANDLES, prop_handles=PROP_HANDLES,
                             place_names=PLACE_NAMES)


def test_resolve_action_unknown_place_tag_raises():
    with pytest.raises(ValueError):
        shots.resolve_action("Wide view of #place_nowhere:day.", char_handles=CHAR_HANDLES,
                             prop_handles=PROP_HANDLES, place_names=PLACE_NAMES)


# ======================================================== 5. resolve_shot: golden prompts

def test_resolve_shot_golden_two_shot_fruit_drama():
    scene = SCRIPT["scenes"][2]  # s03, place_piscine, day
    plan = {
        "framing": "medium_two_shot",
        "action": f"@{CHAR_KIWILO} speaks, angry, facing @{CHAR_BROCCOLIA}.",
        "subjects": [f"@{CHAR_KIWILO}", f"@{CHAR_BROCCOLIA}", f"#{PLACE_PISCINE}:day"],
    }
    resolved = shots.resolve_shot(plan, scene=scene, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                  consistency_mode="references")
    expected = (
        "an anthropomorphic kiwi with fuzzy brown skin and bright green flesh visible at the mouth, wearing "
        "thin gold chain, white linen shirt, left-eyebrow scar. an anthropomorphic broccoli with tightly "
        "clustered dark green florets and a sturdy stalk, wearing crystal rhinestone headband, emerald "
        "velvet gown. the anthropomorphic kiwi speaks, angry, facing the anthropomorphic broccoli. "
        "Setting: a luxurious turquoise swimming pool surrounded by white wooden "
        "loungers and glowing tiki torches. Crystal-clear water fills the foreground. Bamboo loungers sit "
        "to the left. A tiki bar stands to the right. Dense jungle foliage closes the background, day. "
        "Style: photorealistic 3D render of anthropomorphic fruits and vegetables with expressive "
        "human-like faces (eyes, brows, mouths) on realistic fruit heads, human-proportioned bodies in "
        "real fabric outfits, subsurface scattering on fruit skin, visible pores and fuzz, glossy "
        "highlights, high-end CGI commercial quality, Octane-style render. Palette: saturated natural "
        "fruit colours against warm neutral sets. The head is one recognisable whole fruit or vegetable "
        "at human head scale; the face (eyes, brows, mouth with teeth) is carved into its surface, not "
        "pasted on. Bodies are human, dressed in realistic contemporary clothes that carry the character's "
        "signature items and tell their social status (a torn tee and backpack vs a black suit and tie). "
        "No hands as fruit — hands are human. Keep exact fruit species, ripeness, colour and outfit "
        "identical in every image. Camera: medium two-shot, both characters waist-up, 50mm look, shallow "
        "depth of field. Lighting: warm key light with a soft cool fill, golden-hour or practical interior "
        "lamps, dramatic rim light on reveals. Vertical 9:16 composition, subject kept in the central safe "
        "area (leave the bottom 22% free of faces for subtitles). ultra detailed, 8k, sharp focus"
    )
    assert resolved["image_prompt"] == expected


def test_resolve_shot_golden_close_up_family_3d():
    scene = SCRIPT["scenes"][3]  # s04, place_piscine, day
    plan = {
        "framing": "close_up",
        "action": f"@{CHAR_MANGELLA} reacts, shocked.",
        "subjects": [f"@{CHAR_MANGELLA}"],
    }
    resolved = shots.resolve_shot(plan, scene=scene, entities=ENTITIES, style_lock=FAMILY_3D,
                                  consistency_mode="prompt_only")
    expected = (
        "an anthropomorphic mango with smooth red-blushed skin and a faint tropical sheen, wearing gold "
        "statement necklace, emerald green blazer. the anthropomorphic mango reacts, shocked. "
        "Setting: a luxurious turquoise swimming pool "
        "surrounded by white wooden loungers and glowing tiki torches. Crystal-clear water fills the "
        "foreground. Bamboo loungers sit to the left. A tiki bar stands to the right. Dense jungle foliage "
        "closes the background, day. Style: high-end 3D animated feature film look, stylised proportions "
        "with large expressive eyes, soft rounded shapes, subsurface skin, detailed fabric and fur "
        "textures, global illumination, cinematic depth of field. Palette: muted grey-blue environment "
        "with one warm saturated accent on the character, soft pastel shadows. Each character has one "
        "silhouette-defining shape, one primary colour and 2–3 signature items that never change. Eyes "
        "are large and readable at phone size. Expressions are broad and clear. The character carries the "
        "only warm, saturated colour in the frame; the environment stays muted. Camera: tight close-up on "
        "the face, 85mm look, shallow depth of field. Lighting: soft warm key, bounced fill, glowing rim "
        "light, volumetric sunlight through windows or leaves. Vertical 9:16 composition, subject kept in "
        "the central safe area (leave the bottom 22% free of faces for subtitles). highly detailed, "
        "rendered in 4k"
    )
    assert resolved["image_prompt"] == expected
    assert resolved["consistency"] == "prompt_only"


def test_resolve_shot_character_design_rules_appear_exactly_once():
    scene = SCRIPT["scenes"][2]
    plan = {
        "framing": "medium_two_shot",
        "action": f"@{CHAR_KIWILO} speaks, angry, facing @{CHAR_BROCCOLIA}.",
        "subjects": [f"@{CHAR_KIWILO}", f"@{CHAR_BROCCOLIA}"],
    }
    resolved = shots.resolve_shot(plan, scene=scene, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                  consistency_mode="references")
    rules = FRUIT_DRAMA["character_design_rules"].strip()
    assert resolved["image_prompt"].count(rules) == 1


def test_resolve_shot_no_character_no_prop_says_no_people_in_frame():
    scene = SCRIPT["scenes"][5]  # s06, quiet, no characters, no props
    plan = {"framing": "wide_establishing", "action": f"Wide view of #{PLACE_PARLOIR}:day, empty.",
            "subjects": [f"#{PLACE_PARLOIR}:day"]}
    resolved = shots.resolve_shot(plan, scene=scene, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                  consistency_mode="references")
    assert resolved["image_prompt"].startswith("No people in frame.")


def test_resolve_shot_props_only_subjects_block():
    scene = SCRIPT["scenes"][0]  # s01, hook, has prop
    plan = {"framing": "insert_prop", "action": f"%{PROP_PHONE} rings.", "subjects": [f"%{PROP_PHONE}"]}
    resolved = shots.resolve_shot(plan, scene=scene, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                  consistency_mode="references")
    assert resolved["image_prompt"].startswith(f"{PHONE_DESCRIPTOR}.")


def test_resolve_shot_strips_every_entity_name_even_from_a_wrongly_named_action():
    scene = SCRIPT["scenes"][2]
    plan = {
        "framing": "close_up",
        # Wrongly spells the name out instead of using a tag -- must still vanish.
        "action": "Kiwilo glares at the coconut phone near Le Parloir des Secrets.",
        "subjects": [f"@{CHAR_KIWILO}"],
    }
    resolved = shots.resolve_shot(plan, scene=scene, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                  consistency_mode="references")
    for doc in list(CHARACTERS.values()) + list(PLACES.values()) + list(PROPS.values()):
        assert doc["name"] not in resolved["image_prompt"]
    assert "the character" in resolved["image_prompt"]


def test_no_entity_name_survives_across_a_whole_fast_storyboard():
    plans, sources = _fast_plans_for_script(SCRIPT, FRUIT_DRAMA)
    # sabotage one action with a name that should never reach the model.
    plans["s03"][0]["action"] = "Kiwilo glares at Broccolia near the tiki bar."
    doc, _notes = shots.build_storyboard(SCRIPT, plans, sources, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                         template=TEMPLATE, language=EN, consistency_mode="references", now=NOW)
    all_names = [d["name"] for d in list(CHARACTERS.values()) + list(PLACES.values()) + list(PROPS.values())]
    for shot in doc["shots"]:
        for name in all_names:
            assert name not in shot["image_prompt"]


def test_place_name_in_its_own_descriptor_survives():
    """D2 fix (phase 7 stage 1): a place's own name recurring as ordinary
    words inside its own descriptor (e.g. a place named "City square" whose
    descriptor opens with "a bustling urban city square") must not be
    corrupted by the name sweep -- the sweep now runs on the resolved action
    only (D1/D2), never on the whole assembled image_prompt."""
    place_citysquare = _place(
        "place_citysquare",
        "A bustling urban city square featuring cobblestone paths and a central fountain",
        "Market stalls ring the edges. A stone fountain sits at the center.",
        name="City square", variants={"day": _ref("variant_day.jpg")},
    )
    entities = {
        "characters": CHARACTERS,
        "places": {**PLACES, "place_citysquare": place_citysquare},
        "props": PROPS,
    }
    scene = {"place_id": "place_citysquare", "time_variant": "day"}
    plan = {
        "framing": "wide_establishing",
        "action": f"@{CHAR_KIWILO} glares at #place_citysquare:day near %{PROP_PHONE}",
        "subjects": [f"@{CHAR_KIWILO}"],
    }
    resolved = shots.resolve_shot(plan, scene=scene, entities=entities, style_lock=FRUIT_DRAMA,
                                  consistency_mode="references")
    assert "urban city square" in resolved["image_prompt"]
    assert "the place featuring" not in resolved["image_prompt"]

    video_action = resolved["video_action"]
    assert "@" not in video_action and "#" not in video_action and "%" not in video_action
    for doc in list(CHARACTERS.values()) + [place_citysquare] + list(PROPS.values()):
        assert doc["name"] not in video_action


# ======================================================== 6. reference_images

def test_reference_images_order_and_paths():
    scene = SCRIPT["scenes"][2]  # place_piscine, day
    plan = {
        "framing": "medium_two_shot", "action": "x",
        "subjects": [f"@{CHAR_KIWILO}", f"@{CHAR_BROCCOLIA}", f"#{PLACE_PISCINE}:day", f"%{PROP_PHONE}"],
    }
    resolved = shots.resolve_shot(plan, scene=scene, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                  consistency_mode="references")
    assert resolved["reference_images"] == [
        "characters/char_kiwilo/refs/portrait.png",
        "characters/char_broccolia/refs/portrait.jpg",
        "places/place_piscine/refs/variant_day.jpg",  # via _ref("variant_day.jpg")
        "props/prop_phone/refs/image.png",
    ]
    for path in resolved["reference_images"]:
        assert not path.startswith("/")
        assert ".." not in path


def test_reference_images_falls_back_to_day_when_the_variant_has_no_image():
    scene = dict(SCRIPT["scenes"][2])
    scene["time_variant"] = "dusk"  # place_piscine has "dusk": None
    plan = {"framing": "wide_establishing", "action": "x", "subjects": [f"#{PLACE_PISCINE}:dusk"]}
    resolved = shots.resolve_shot(plan, scene=scene, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                  consistency_mode="references")
    assert resolved["reference_images"] == ["places/place_piscine/refs/variant_day.jpg"]


def test_reference_images_skips_an_entity_with_no_image():
    no_portrait = copy.deepcopy(CHARACTERS)
    no_portrait[CHAR_KIWILO]["refs"]["portrait"] = None
    entities = {"characters": no_portrait, "places": PLACES, "props": PROPS}
    scene = SCRIPT["scenes"][2]
    plan = {"framing": "medium_two_shot", "action": "x", "subjects": [f"@{CHAR_KIWILO}", f"@{CHAR_BROCCOLIA}"]}
    resolved = shots.resolve_shot(plan, scene=scene, entities=entities, style_lock=FRUIT_DRAMA,
                                  consistency_mode="references")
    assert resolved["reference_images"] == [
        "characters/char_broccolia/refs/portrait.jpg",
        "places/place_piscine/refs/variant_day.jpg",
    ]


def test_reference_images_capped_at_eight():
    many_chars = {f"char_{i}": _char(f"char_{i}", f"an anthropomorphic fruit number {i} with plain skin",
                                     [f"item {i}"], name=f"Fruit{i}", portrait=f"portrait_{i}.png")
                  for i in range(10)}
    entities = {"characters": many_chars, "places": PLACES, "props": PROPS}
    scene = dict(SCRIPT["scenes"][2])
    subjects = [f"@char_{i}" for i in range(10)]
    plan = {"framing": "wide_establishing", "action": "x", "subjects": subjects}
    resolved = shots.resolve_shot(plan, scene=scene, entities=entities, style_lock=FRUIT_DRAMA,
                                  consistency_mode="references")
    assert len(resolved["reference_images"]) == 8


# ======================================================== 7. motion_for

def test_motion_for_wide_establishing_uses_by_function_framing_key_fruit_drama():
    motion = shots.motion_for("wide_establishing", "hold", "setup", FRUIT_DRAMA)
    assert motion == {"type": "pan_lr", "zoom_from": 1.0, "zoom_to": 1.0, "pan": "lr"}


def test_motion_for_peak_scene_function_fruit_drama():
    motion = shots.motion_for("medium_single", "hold", "peak", FRUIT_DRAMA)
    assert motion["type"] == "push_in"
    assert motion["zoom_from"] == 1.0
    assert motion["zoom_to"] == 1.18  # zoom.peak[1]
    assert motion["pan"] == "none"


def test_motion_for_dialogue_zoom_when_not_a_peak_scene_fruit_drama():
    # by_function has no entry for "medium_single"/"setup": the given camera_motion wins.
    motion = shots.motion_for("medium_single", "push_in", "setup", FRUIT_DRAMA)
    assert motion["type"] == "push_in"
    assert motion["zoom_from"] == 1.0
    assert motion["zoom_to"] == 1.1  # zoom.dialogue[1]


def test_motion_for_falls_back_to_given_camera_motion():
    motion = shots.motion_for("close_up", "pan_ud", "setup", FRUIT_DRAMA)
    assert motion == {"type": "pan_ud", "zoom_from": 1.0, "zoom_to": 1.0, "pan": "ud"}


def test_motion_for_falls_back_to_default_motion_with_no_camera_motion_given():
    motion = shots.motion_for("close_up", None, "setup", FRUIT_DRAMA)
    assert motion["type"] == FRUIT_DRAMA["motion_rules"]["tier1"]["default_motion"]


def test_motion_for_pull_out_is_the_reverse_of_push_in():
    motion = shots.motion_for("medium_single", "pull_out", "setup", FRUIT_DRAMA)
    assert motion["type"] == "pull_out"
    assert motion["zoom_from"] == 1.1
    assert motion["zoom_to"] == 1.0


def test_motion_for_hold():
    motion = shots.motion_for("medium_single", "hold", "setup", FRUIT_DRAMA)
    # by_function has no "medium_single"/"setup" entry, so the given camera_motion (hold) wins.
    assert motion == {"type": "hold", "zoom_from": 1.0, "zoom_to": 1.0, "pan": "none"}


def test_motion_for_family_3d_wide_establishing_pans_ud():
    motion = shots.motion_for("wide_establishing", "hold", "setup", FAMILY_3D)
    assert motion["type"] == "pan_ud"
    assert motion["pan"] == "ud"


def test_motion_for_clamps_to_zoom_max():
    tight_style = copy.deepcopy(FRUIT_DRAMA)
    tight_style["motion_rules"]["tier1"]["zoom"] = {"dialogue": [1.0, 1.2], "peak": [1.0, 1.2], "max": 1.2}
    motion = shots.motion_for("medium_single", "hold", "peak", tight_style)
    assert motion["zoom_to"] <= 1.2


# ======================================================== 8. fast_plan

FRUIT_DEFAULTS = FRUIT_DRAMA["episode_defaults"]
FAMILY_DEFAULTS = FAMILY_3D["episode_defaults"]


def _fast_plans_for_script(script, style_lock):
    episode_defaults = style_lock["episode_defaults"]
    seen_places = set()
    plans, sources = {}, {}
    for scene in script["scenes"]:
        first_at_place = scene["place_id"] not in seen_places
        seen_places.add(scene["place_id"])
        plans[scene["scene_id"]] = shots.fast_plan(
            scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=episode_defaults,
            style_lock=style_lock, first_at_place=first_at_place,
        )
        sources[scene["scene_id"]] = "fast"
    return plans, sources


def test_fast_plan_hook_with_insert_prop_style_gets_an_insert_prop_shot_first():
    scene = SCRIPT["scenes"][0]  # s01, hook, has a prop
    plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FRUIT_DEFAULTS,
                           style_lock=FRUIT_DRAMA, first_at_place=True)
    assert plan[0]["framing"] == "insert_prop"
    assert plan[0]["subjects"] == [f"%{PROP_PHONE}"]
    assert plan[0]["lines"] == []


def test_fast_plan_hook_without_insert_prop_style_has_no_insert_prop_shot():
    scene = SCRIPT["scenes"][0]
    plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FAMILY_DEFAULTS,
                           style_lock=FAMILY_3D, first_at_place=True)
    assert all(p["framing"] != "insert_prop" for p in plan)


def test_fast_plan_first_at_place_opens_with_a_wordless_wide_establishing():
    scene = SCRIPT["scenes"][2]  # s03, first at place_piscine
    plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FRUIT_DEFAULTS,
                           style_lock=FRUIT_DRAMA, first_at_place=True)
    assert plan[0]["framing"] == "wide_establishing"
    assert plan[0]["lines"] == []
    assert f"#{PLACE_PISCINE}:day" in plan[0]["subjects"]


def test_fast_plan_not_first_at_place_opens_with_the_first_speaking_turn():
    scene = SCRIPT["scenes"][1]  # s02, not first at place_parloir
    plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FRUIT_DEFAULTS,
                           style_lock=FRUIT_DRAMA, first_at_place=False)
    assert plan[0]["framing"] == "medium_single"  # 1 character present
    assert plan[0]["lines"] == [1]


def test_fast_plan_merges_consecutive_same_speaker_lines_into_one_turn():
    scene = SCRIPT["scenes"][2]  # s03: kiwilo, kiwilo, broccolia
    plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FRUIT_DEFAULTS,
                           style_lock=FRUIT_DRAMA, first_at_place=False)
    speaking_shots = [p for p in plan if p["lines"]]
    assert speaking_shots[0]["lines"] == [1, 2]  # kiwilo's two consecutive lines
    assert speaking_shots[1]["lines"] == [3]  # broccolia


def test_fast_plan_close_up_on_a_tight_emotion_line():
    # first_at_place=True: the opening shot is the wordless wide_establishing, so the
    # (only) speaking turn is free to follow the emotion rule rather than being forced
    # into the opening framing.
    scene = _scene(
        "s91", "turn", place_id=PLACE_PARLOIR, characters=[CHAR_BROCCOLIA],
        lines=[_line("l91", CHAR_BROCCOLIA, "Something is not right here.", emotion="tension")],
    )
    plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FRUIT_DEFAULTS,
                           style_lock=FRUIT_DRAMA, first_at_place=True)
    speaking = [p for p in plan if p["lines"]][0]
    assert speaking["framing"] == "close_up"


def test_fast_plan_medium_single_on_a_neutral_line():
    scene = SCRIPT["scenes"][6]  # s07, neutral line, not first at place, one char
    plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FRUIT_DEFAULTS,
                           style_lock=FRUIT_DRAMA, first_at_place=False)
    speaking = [p for p in plan if p["lines"]][0]
    assert speaking["framing"] == "medium_single"


def test_fast_plan_reaction_shot_on_a_peak_scene():
    scene = SCRIPT["scenes"][3]  # s04, peak, mangella speaks
    plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FRUIT_DEFAULTS,
                           style_lock=FRUIT_DRAMA, first_at_place=False)
    reaction = plan[-1]
    assert reaction["framing"] == "close_up"
    assert reaction["lines"] == []
    assert reaction["subjects"] != [f"@{CHAR_MANGELLA}"]  # someone other than the last speaker


def test_fast_plan_reaction_shot_falls_back_to_last_speaker_when_alone():
    scene = _scene("s90", "peak", place_id=PLACE_PARLOIR, characters=[CHAR_KIWILO],
                   lines=[_line("l90", CHAR_KIWILO, "I am completely alone here.")])
    plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FRUIT_DEFAULTS,
                           style_lock=FRUIT_DRAMA, first_at_place=False)
    assert plan[-1]["subjects"] == [f"@{CHAR_KIWILO}"]


def test_fast_plan_no_reaction_shot_on_a_setup_scene():
    scene = SCRIPT["scenes"][1]  # s02, setup
    plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FRUIT_DEFAULTS,
                           style_lock=FRUIT_DRAMA, first_at_place=False)
    assert len(plan) == 2  # opening speaking-turn shot, clamped up to lo=2 (see below)


def test_fast_plan_quiet_scene_gets_establishing_plus_one_shot():
    scene = SCRIPT["scenes"][5]  # s06, no lines
    plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FRUIT_DEFAULTS,
                           style_lock=FRUIT_DRAMA, first_at_place=False)
    assert plan[0]["framing"] == "wide_establishing"
    assert plan[0]["lines"] == []
    assert len(plan) == 2


def test_fast_plan_clamps_up_to_the_minimum_shot_count():
    scene = SCRIPT["scenes"][1]  # s02: a single speaking-turn shot alone would be 1 shot
    defaults = {**FRUIT_DEFAULTS, "shots_per_scene": [3, 4]}
    plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=defaults,
                           style_lock=FRUIT_DRAMA, first_at_place=False)
    assert len(plan) == 3


def test_fast_plan_clamps_down_to_the_maximum_shot_count():
    scene = SCRIPT["scenes"][3]  # s04: peak with reaction => several shots
    defaults = {**FRUIT_DEFAULTS, "shots_per_scene": [2, 2]}
    plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=defaults,
                           style_lock=FRUIT_DRAMA, first_at_place=True)
    assert len(plan) <= 2


def test_fast_plan_modifiers_applied_only_when_the_style_lists_them():
    stopmotion_style = copy.deepcopy(FRUIT_DRAMA)
    stopmotion_style["motion_rules"]["tier1"]["modifiers"] = ["jitter_stopmotion", "handheld"]
    scene = SCRIPT["scenes"][4]  # s05, emotion "tension"
    plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FRUIT_DEFAULTS,
                           style_lock=stopmotion_style, first_at_place=False)
    assert all("jitter_stopmotion" in p["modifiers"] for p in plan)
    assert all("handheld" in p["modifiers"] for p in plan)

    # the base fixture styles list no modifiers at all: never applied.
    plain_plan = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FRUIT_DEFAULTS,
                                 style_lock=FRUIT_DRAMA, first_at_place=False)
    assert all(p["modifiers"] == [] for p in plain_plan)


def test_fast_plan_is_deterministic():
    scene = SCRIPT["scenes"][2]
    a = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FRUIT_DEFAULTS,
                        style_lock=FRUIT_DRAMA, first_at_place=True)
    b = shots.fast_plan(scene, lines=scene["lines"], entities=ENTITIES, episode_defaults=FRUIT_DEFAULTS,
                        style_lock=FRUIT_DRAMA, first_at_place=True)
    assert a == b


# ======================================================== 9. rule_pass

def _plans_by_scene(script, style_lock):
    plans, _sources = _fast_plans_for_script(script, style_lock)
    return [(scene, plans[scene["scene_id"]]) for scene in script["scenes"]]


def test_rule_pass_no_two_consecutive_shots_share_a_framing():
    plans_by_scene = _plans_by_scene(SCRIPT, FRUIT_DRAMA)
    new_plans_by_scene, _notes = shots.rule_pass(plans_by_scene, FRUIT_DRAMA)
    framings = [p["framing"] for _scene, plans in new_plans_by_scene for p in plans]
    for a, b in zip(framings, framings[1:]):
        assert a != b


def test_rule_pass_never_changes_an_insert_prop_shot():
    scene_a = _scene("sA", "hook", place_id=PLACE_PARLOIR, props=[PROP_PHONE], lines=[])
    scene_b = _scene("sB", "setup", place_id=PLACE_PARLOIR, characters=[CHAR_KIWILO], lines=[])
    plan_a = [{"framing": "insert_prop", "camera_motion": "hold", "modifiers": [], "action": "x",
              "subjects": [f"%{PROP_PHONE}"], "lines": []}]
    plan_b = [{"framing": "insert_prop", "camera_motion": "hold", "modifiers": [], "action": "y",
              "subjects": [f"%{PROP_PHONE}"], "lines": []}]
    new_plans_by_scene, notes = shots.rule_pass([(scene_a, plan_a), (scene_b, plan_b)], FRUIT_DRAMA)
    # both stay insert_prop: neither an insert_prop shot is ever the one that changes.
    assert new_plans_by_scene[0][1][0]["framing"] == "insert_prop"
    assert new_plans_by_scene[1][1][0]["framing"] == "insert_prop"
    # both shots in the colliding pair are protected: left unresolved, but reported.
    assert any("left unresolved" in n for n in notes)


def test_rule_pass_never_changes_a_scene_opening_wide_establishing():
    scene_a = _scene("sA", "setup", place_id=PLACE_PARLOIR, lines=[])
    scene_b = _scene("sB", "setup", place_id=PLACE_PISCINE, lines=[])
    # scene_a's wide_establishing is its LAST shot (not its opener, so unprotected);
    # scene_b's is its FIRST shot (its opener, protected).
    plan_a = [{"framing": "medium_single", "camera_motion": "hold", "modifiers": [], "action": "w",
              "subjects": [], "lines": []},
             {"framing": "wide_establishing", "camera_motion": "hold", "modifiers": [], "action": "x",
              "subjects": [], "lines": []}]
    plan_b = [{"framing": "wide_establishing", "camera_motion": "hold", "modifiers": [], "action": "y",
              "subjects": [], "lines": []},
             {"framing": "medium_single", "camera_motion": "hold", "modifiers": [], "action": "z",
              "subjects": [], "lines": []}]
    new_plans_by_scene, _notes = shots.rule_pass([(scene_a, plan_a), (scene_b, plan_b)], FRUIT_DRAMA)
    # scene_b's own opening wide_establishing is protected: scene_a's shot changes instead.
    assert new_plans_by_scene[1][1][0]["framing"] == "wide_establishing"
    assert new_plans_by_scene[0][1][1]["framing"] != "wide_establishing"


def test_rule_pass_close_up_window_forces_a_close_up_every_three_scenes():
    # three scenes with no lines at all: no shot would naturally be a close_up.
    no_char_scenes = [_scene(f"sW{i}", "setup", place_id=PLACE_PARLOIR, characters=[CHAR_KIWILO], lines=[])
                      for i in range(3)]
    plans = [[{"framing": "wide_establishing", "camera_motion": "hold", "modifiers": [], "action": "x",
              "subjects": [], "lines": []},
             {"framing": "medium_single", "camera_motion": "hold", "modifiers": [], "action": "y",
              "subjects": [], "lines": []}] for _ in no_char_scenes]
    plans_by_scene = list(zip(no_char_scenes, plans))
    new_plans_by_scene, notes = shots.rule_pass(plans_by_scene, FRUIT_DRAMA)
    all_framings = [p["framing"] for _scene, plans in new_plans_by_scene for p in plans]
    assert any(f in ("close_up", "extreme_close_up") for f in all_framings)
    assert any("no close_up/extreme_close_up" in n for n in notes)


def test_rule_pass_camera_motion_follows_motion_for_precedence():
    plans_by_scene = _plans_by_scene(SCRIPT, FRUIT_DRAMA)
    new_plans_by_scene, _notes = shots.rule_pass(plans_by_scene, FRUIT_DRAMA)
    for scene, plans in new_plans_by_scene:
        for plan in plans:
            expected = shots.motion_for(plan["framing"], plan["camera_motion"], scene["function"], FRUIT_DRAMA)
            assert plan["camera_motion"] == expected["type"]


def test_rule_pass_does_not_mutate_its_input():
    plans_by_scene = _plans_by_scene(SCRIPT, FRUIT_DRAMA)
    before = copy.deepcopy(plans_by_scene)
    shots.rule_pass(plans_by_scene, FRUIT_DRAMA)
    for (scene_before, plans_before), (scene_after, plans_after) in zip(before, plans_by_scene):
        assert plans_before == plans_after


def test_rule_pass_is_deterministic():
    plans_by_scene = _plans_by_scene(SCRIPT, FRUIT_DRAMA)
    a, notes_a = shots.rule_pass(plans_by_scene, FRUIT_DRAMA)
    b, notes_b = shots.rule_pass(plans_by_scene, FRUIT_DRAMA)
    assert a == b
    assert notes_a == notes_b


# ======================================================== 10. build_storyboard

def test_build_storyboard_fast_path_validates_fruit_drama():
    plans, sources = _fast_plans_for_script(SCRIPT, FRUIT_DRAMA)
    doc, _notes = shots.build_storyboard(SCRIPT, plans, sources, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                         template=TEMPLATE, language=EN, consistency_mode="references", now=NOW)
    assert schemas.storyboard_errors(doc, min_shot_s=TEMPLATE["min_shot_s"]) == []
    assert schemas.storyboard_context_errors(
        doc, SCRIPT, shots_per_scene=FRUIT_DRAMA["episode_defaults"]["shots_per_scene"],
    ) == []
    assert doc["ep"] == 1
    assert doc["rev"] == 1
    assert doc["created_at"] == NOW
    assert doc["resolved_from"]  # at least one entity referenced


def test_build_storyboard_fast_path_validates_family_3d():
    plans, sources = _fast_plans_for_script(SCRIPT, FAMILY_3D)
    doc, _notes = shots.build_storyboard(SCRIPT, plans, sources, entities=ENTITIES, style_lock=FAMILY_3D,
                                         template=TEMPLATE, language=EN, consistency_mode="prompt_only", now=NOW)
    assert schemas.storyboard_errors(doc, min_shot_s=TEMPLATE["min_shot_s"]) == []
    assert schemas.storyboard_context_errors(
        doc, SCRIPT, shots_per_scene=FAMILY_3D["episode_defaults"]["shots_per_scene"],
    ) == []


def test_build_storyboard_is_deterministic():
    plans, sources = _fast_plans_for_script(SCRIPT, FRUIT_DRAMA)
    doc1, notes1 = shots.build_storyboard(SCRIPT, plans, sources, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                          template=TEMPLATE, language=EN, consistency_mode="references", now=NOW)
    doc2, notes2 = shots.build_storyboard(SCRIPT, plans, sources, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                          template=TEMPLATE, language=EN, consistency_mode="references", now=NOW)
    assert doc1 == doc2
    assert notes1 == notes2


def test_build_storyboard_accepts_t1_shaped_plans_too():
    """T1's reply shape and fast_plan's are the same: build_storyboard does
    not care which produced a scene's plans."""
    plans, sources = _fast_plans_for_script(SCRIPT, FRUIT_DRAMA)
    # Hand-author scene s02 as if a T1 reply had been converted to plans.
    plans["s02"] = [
        {"framing": "medium_single", "camera_motion": "push_in", "modifiers": [],
         "action": f"@{CHAR_MANGELLA} speaks, neutral.", "subjects": [f"@{CHAR_MANGELLA}"], "lines": [1]},
        {"framing": "close_up", "camera_motion": "hold", "modifiers": [],
         "action": f"@{CHAR_MANGELLA} reacts silently.", "subjects": [f"@{CHAR_MANGELLA}"], "lines": []},
    ]
    sources["s02"] = "t1"
    doc, _notes = shots.build_storyboard(SCRIPT, plans, sources, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                         template=TEMPLATE, language=EN, consistency_mode="references", now=NOW)
    assert doc["scenes"]["s02"]["source"] == "t1"
    assert doc["scenes"]["s01"]["source"] == "fast"
    assert schemas.storyboard_errors(doc, min_shot_s=TEMPLATE["min_shot_s"]) == []


def test_build_storyboard_only_covers_scenes_present_in_plans():
    plans, sources = _fast_plans_for_script(SCRIPT, FRUIT_DRAMA)
    del plans["s06"]
    del sources["s06"]
    doc, _notes = shots.build_storyboard(SCRIPT, plans, sources, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                         template=TEMPLATE, language=EN, consistency_mode="references", now=NOW)
    assert "s06" not in doc["scenes"]
    assert all(shot["scene_id"] != "s06" for shot in doc["shots"])


def test_build_storyboard_shot_ids_and_order_sequential():
    plans, sources = _fast_plans_for_script(SCRIPT, FRUIT_DRAMA)
    doc, _notes = shots.build_storyboard(SCRIPT, plans, sources, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                         template=TEMPLATE, language=EN, consistency_mode="references", now=NOW)
    for i, shot in enumerate(doc["shots"], start=1):
        assert shot["shot_id"] == f"sh{i:02d}"
        assert shot["order"] == i


def test_build_storyboard_durations_sum_to_scene_durations_or_report_extra_hold():
    plans, sources = _fast_plans_for_script(SCRIPT, FRUIT_DRAMA)
    doc, notes = shots.build_storyboard(SCRIPT, plans, sources, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                        template=TEMPLATE, language=EN, consistency_mode="references", now=NOW)
    by_scene: dict = {}
    for shot in doc["shots"]:
        by_scene.setdefault(shot["scene_id"], []).append(shot)

    # F7: a scene's duration is the EPISODE-LEVEL one (after the window pass),
    # the one the script is stored with -- not the scene's own scene_timing.
    episode = timing.episode_timing(SCRIPT, TEMPLATE, EN, style_lock=FRUIT_DRAMA, storyboard=doc)
    for sid, scene_shots in by_scene.items():
        scene_t = episode["scenes"][sid]
        total = sum(shot["duration_s"] for shot in scene_shots)
        has_note = any(f"scene {sid}:" in n and "extra_hold_s" in n for n in notes)
        assert total == pytest.approx(scene_t["duration_s"], abs=0.01) or has_note


def test_build_storyboard_transitions_follow_the_scene_boundary_grammar():
    plans, sources = _fast_plans_for_script(SCRIPT, FRUIT_DRAMA)
    doc, _notes = shots.build_storyboard(SCRIPT, plans, sources, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                         template=TEMPLATE, language=EN, consistency_mode="references", now=NOW)
    shots_by_id = {shot["shot_id"]: shot for shot in doc["shots"]}
    for transition in doc["transitions"]:
        after_shot = shots_by_id[transition["after"]]
        idx = doc["shots"].index(after_shot)
        next_shot = doc["shots"][idx + 1]
        if after_shot["scene_id"] == next_shot["scene_id"]:
            assert transition["type"] == "cut"
        else:
            prev_place = next(s for s in SCRIPT["scenes"] if s["scene_id"] == after_shot["scene_id"])["place_id"]
            next_place = next(s for s in SCRIPT["scenes"] if s["scene_id"] == next_shot["scene_id"])["place_id"]
            expected = "dissolve" if prev_place == next_place else "fadeblack"
            assert transition["type"] == expected


def test_build_storyboard_previous_bumps_rev_and_keeps_created_at():
    plans, sources = _fast_plans_for_script(SCRIPT, FRUIT_DRAMA)
    first, _notes = shots.build_storyboard(SCRIPT, plans, sources, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                           template=TEMPLATE, language=EN, consistency_mode="references", now=NOW)
    later = "2026-09-28T10:00:00+00:00"
    second, _notes = shots.build_storyboard(SCRIPT, plans, sources, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                            template=TEMPLATE, language=EN, consistency_mode="references",
                                            now=later, previous=first)
    assert second["rev"] == 2
    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] == later


def test_build_storyboard_raises_valueerror_on_an_invalid_result():
    plans, sources = _fast_plans_for_script(SCRIPT, FRUIT_DRAMA)
    # An out-of-range shots_per_scene on the style makes storyboard_context_errors fail.
    broken_style = copy.deepcopy(FRUIT_DRAMA)
    broken_style["episode_defaults"]["shots_per_scene"] = [99, 99]
    with pytest.raises(ValueError):
        shots.build_storyboard(SCRIPT, plans, sources, entities=ENTITIES, style_lock=broken_style,
                               template=TEMPLATE, language=EN, consistency_mode="references", now=NOW)


# ======================================================== 11. refresh_prompts

def test_refresh_prompts_changes_only_prompts_refs_and_resolved_from():
    plans, sources = _fast_plans_for_script(SCRIPT, FRUIT_DRAMA)
    doc, _notes = shots.build_storyboard(SCRIPT, plans, sources, entities=ENTITIES, style_lock=FRUIT_DRAMA,
                                         template=TEMPLATE, language=EN, consistency_mode="references", now=NOW)

    changed_entities = copy.deepcopy(ENTITIES)
    changed_entities["characters"][CHAR_KIWILO]["descriptor"] = "an anthropomorphic kiwi wearing a brand new outfit"
    changed_entities["characters"][CHAR_KIWILO]["updated_at"] = "2026-09-29T00:00:00+00:00"

    refreshed = shots.refresh_prompts(doc, SCRIPT, entities=changed_entities, style_lock=FRUIT_DRAMA,
                                      consistency_mode="references")

    assert refreshed["resolved_from"][CHAR_KIWILO] == "2026-09-29T00:00:00+00:00"
    changed_any_prompt = False
    for before, after in zip(doc["shots"], refreshed["shots"]):
        for key in ("shot_id", "scene_id", "order", "framing", "camera_motion", "modifiers", "subject_tags",
                    "action", "lines", "duration_s", "keep_still", "motion", "video_prompt", "assets",
                    "prompt_override"):
            assert before[key] == after[key]
        if before["image_prompt"] != after["image_prompt"]:
            changed_any_prompt = True
    assert changed_any_prompt
    assert refreshed["transitions"] == doc["transitions"]
    assert refreshed["scenes"] == doc["scenes"]


# ======================================================== 12. layered prompts (phase 7 stage 3b)
#
# A v2 story's shot is resolved into the layered keyframe prompt (A8): the
# reference roles, the beat, the staging, the composition coerced to the
# subject count, the place slice, the style tail and the constraints clause,
# in that order. A legacy story resolves exactly as before (RC-Q1).

CARTOON_FLAT = templates.load_style("cartoon_flat")
_V2_CONSTRAINTS = "Clean frame: no captions, lettering, logos or watermarks; each character appears once."
_STAYS_STILL = "The set, the lighting and every character's look stay exactly as in the first frame."


def _v2_look(**changes):
    look = {
        "build": "tall narrow cylinder body", "silhouette": "upright tube with a square cape",
        "face": "dot eyes, flat line mouth, round glasses", "hair": "bald flat top",
        "skin_material": "smooth matte yellow plastic", "height_cm": 180, "palette": ["yellow", "blue"],
        "wardrobe_sets": [{"id": "daily", "context": "every day",
                           "items": "stiff rectangular blue cape, round glasses"}],
        "season_change": None,
    }
    look.update(changes)
    return look


def _v2_char(cid, name, descriptor, items, look):
    doc = _char(cid, descriptor, items, name=name, portrait="portrait.jpg")
    doc["refs"]["turnaround"] = _ref("turnaround.jpg")
    doc["refs"]["expressions"] = _ref("expressions.jpg")
    doc["look"] = look
    return doc


def _v2_entities():
    tall = _v2_char("char_captain_obvious", "Captain Obvious", "A tall yellow geometric cylinder with a blue cape",
                    ["Stiff rectangular blue cape", "Comically oversized magnifying glass"], _v2_look())
    short = _v2_char("char_miss_overthink", "Miss Overthink", "A short red triangle character with tiny dot eyes",
                     ["bulging utility belt", "shaking stopwatch"],
                     _v2_look(build="small sharp triangle body", silhouette="pointed red wedge", face="tiny dot eyes",
                              hair="none", skin_material="glossy red card", height_cm=90, palette=["red"],
                              wardrobe_sets=[{"id": "daily", "context": "every day",
                                              "items": "bulging utility belt with index cards"}]))
    place = _place("place_clocktown", "a busy square with a digital clock tower and colourful storefronts",
                   "newsstand left, lamppost right", name="Clocktown Plaza",
                   variants={"day": _ref("variant_day.jpg"), "night": None})
    place["look"] = {
        "layout_map": {"left": "a bright yellow newsstand", "right": "a tall lamppost", "back": "the clock tower",
                       "foreground": "", "centre": "an empty park bench"},
        "scale_note": "a wide square, the tower ten people high",
        "lighting": {"day": "flat bright noon light", "night": "orange street lamps"},
        "props_here": [],
    }
    return {"characters": {tall["char_id"]: tall, short["char_id"]: short},
            "places": {place["place_id"]: place}, "props": {}}


def _v2_scene():
    line = _line("l04", "char_captain_obvious", "That object is a giant toaster.", emotion="shocked")
    line["delivery"] = "Slow, monotone, and absolutely certain."
    return _scene("s01", "hook", place_id="place_clocktown", characters=["char_captain_obvious", "char_miss_overthink"],
                  lines=[line], emotion="shocked")


_V2_ACTION = ("@char_captain_obvious and @char_miss_overthink stand in #place_clocktown:day looking shocked at a "
              "giant toaster.")


def _v2_resolve(framing, subjects, *, consistency_mode="references"):
    plan = {"framing": framing, "action": _V2_ACTION, "subjects": subjects, "lines": [1], "camera_motion": "hold",
            "modifiers": []}
    return shots.resolve_shot(plan, scene=_v2_scene(), entities=_v2_entities(), style_lock=CARTOON_FLAT,
                              consistency_mode=consistency_mode, v2=True)


def _no_v2_names(text):
    for name in ("Captain", "Obvious", "Overthink", "Clocktown", "Plaza"):
        assert name not in text, name


def test_layered_prompt_beat_before_place_and_roles_first():
    subjects = ["@char_captain_obvious", "@char_miss_overthink", "#place_clocktown:day"]
    # medium_single with two characters in it: the composition reads as a two-shot.
    resolved = _v2_resolve("medium_single", subjects)
    prompt = resolved["image_prompt"]

    assert resolved["prompt_layout"] == "layered_v1"
    # (1) the reference roles open it, in the order the images are sent.
    assert prompt.startswith("Image 1 is the tall yellow geometric cylinder's reference")
    assert resolved["reference_images"] == [
        "characters/char_captain_obvious/refs/portrait.jpg", "characters/char_miss_overthink/refs/portrait.jpg",
        "places/place_clocktown/refs/variant_day.jpg", "characters/char_captain_obvious/refs/turnaround.jpg",
        "characters/char_miss_overthink/refs/turnaround.jpg"]
    assert "Image 3 is the set (keep layout and light)." in prompt
    # (2) the beat (the resolved action, then the line's delivery) comes before the place.
    beat = prompt.index("The tall yellow geometric cylinder and the short red triangle character stand")
    assert "slow, monotone, and absolutely certain" in prompt.lower()
    assert prompt.index("Image 5") < beat < prompt.index("on the left a bright yellow newsstand")
    # (3) staging by position and relative height; (4) the framing coerced to two subjects.
    assert "On the left, the tall yellow geometric cylinder" in prompt and "They face each other." in prompt
    assert "about twice as tall as the short red triangle character" in prompt
    assert "two-shot" in prompt and "single character" not in prompt
    # (6) the style tail is the rendering and palette only; (7) the constraints end it.
    assert "Palette: flat saturated primaries" in prompt
    assert CARTOON_FLAT["character_design_rules"].split(".")[0] not in prompt and "9:16" not in prompt
    assert prompt.endswith(_V2_CONSTRAINTS)
    assert 130 <= len(prompt.split()) <= 220, len(prompt.split())
    assert ".," not in prompt and ".." not in prompt and "@" not in prompt and "#" not in prompt
    _no_v2_names(prompt)
    # The clip prompt of the same shot (A8): at most 80 words, the stays-still clause in it.
    assert len(resolved["video_prompt"].split()) <= 80 and _STAYS_STILL in resolved["video_prompt"]
    _no_v2_names(resolved["video_prompt"])


def test_close_up_omits_layout():
    resolved = _v2_resolve("close_up", ["@char_miss_overthink", "#place_clocktown:day"])
    prompt = resolved["image_prompt"]

    # The lighting of the variant and one background element, never the layout map.
    assert "flat bright noon light" in prompt
    assert "newsstand" not in prompt and "lamppost" not in prompt and "Layout:" not in prompt
    # No forced 85mm shallow focus on a style whose camera has no depth of field.
    assert "85mm" not in prompt and "shallow depth of field" not in prompt and "skin detail" not in prompt
    # A close-up's identity image is the expression sheet.
    assert resolved["reference_images"][0] == "characters/char_miss_overthink/refs/expressions.jpg"
    assert prompt.startswith("Image 1 is the short red triangle character's expression sheet")
    assert prompt.endswith(_V2_CONSTRAINTS)
    assert ".," not in prompt and ".." not in prompt
    _no_v2_names(prompt)


# Story A (979c8376e43e) as stored in the main checkout's outputs/ on
# 2026-10-01: its two characters and its place, the fields resolve_shot reads.
_A_CHARACTERS = {
    "char_captain_obvious": dict(_char(
        "char_captain_obvious",
        "A tall yellow geometric cylinder with a solid rectangular blue cape, wearing oversized round glasses, "
        "sporting simple dot eyes and a permanent flat line mouth.",
        ["Comically oversized magnifying glass", "Stiff rectangular blue cape"],
        name="Captain Obvious", portrait="portrait.jpg")),
    "char_miss_overthink": dict(_char(
        "char_miss_overthink",
        "A short red triangle character with tiny dot eyes, wearing a thick utility belt overflowing with colorful "
        "index cards and clutching a shaking stopwatch.",
        ["bulging utility belt with index cards", "shaking stopwatch", "stack of color-coded contingency charts"],
        name="Miss Overthink", portrait="portrait.jpg")),
}
_A_PLACES = {"place_city_square": _place(
    "place_city_square",
    "A bustling urban city square featuring a giant digital countdown clock tower in the center, surrounded by "
    "colorful storefronts, paved stone ground, and an empty park bench.",
    "In the foreground is a crack in the pavement. To the left stands a bright yellow newsstand. To the right is a "
    "tall lamppost with a green street sign. In the background looms the massive red digital countdown clock tower.",
    name="City square", variants={"day": _ref("variant_day.jpg"), "dusk": None, "rain": None})}
_A_ENTITIES = {"characters": _A_CHARACTERS, "places": _A_PLACES, "props": {}}
_A_SH01 = {"framing": "medium_two_shot", "camera_motion": "hold", "modifiers": [], "lines": [1],
           "subjects": ["@char_captain_obvious", "@char_miss_overthink", "#place_city_square:day"],
           "action": ("@char_captain_obvious and @char_miss_overthink stand in #place_city_square:day looking "
                      "shocked at a giant toaster.")}
# RC-Q1: story A sh01 resolved by the code of HEAD 7567458 (after stage 1),
# from a copy of the main checkout's outputs/stories/979c8376e43e read through
# the store into a scratch directory (episode_common.load_context): the
# sha256 of its image_prompt (257 words) and its exact negative_prompt.
_A_SH01_IMAGE_SHA256 = "71348b926de831d606660c2ed57dacff4d3bbe3d156f5af3e69a517c41f27e62"
_A_SH01_NEGATIVE = (
    "text, watermark, logo, signature, extra limbs, extra fingers, deformed hands, duplicated character, cropped "
    "face, blurry, low resolution, jpeg artifacts, out of frame, split screen, collage, frame border, caption, 3D, "
    "photorealistic, gradients, painterly, sketchy lines, anime")


def test_legacy_story_resolves_byte_identical():
    import hashlib

    scene = _scene("s01", "hook", place_id="place_city_square",
                   characters=["char_captain_obvious", "char_miss_overthink"],
                   lines=[_line("l04", "char_captain_obvious", "That object is a giant toaster.", emotion="shocked")],
                   emotion="shocked")
    by_default = shots.resolve_shot(_A_SH01, scene=scene, entities=_A_ENTITIES, style_lock=CARTOON_FLAT,
                                    consistency_mode="prompt_only")
    legacy = shots.resolve_shot(_A_SH01, scene=scene, entities=_A_ENTITIES, style_lock=CARTOON_FLAT,
                                consistency_mode="prompt_only", v2=False)

    assert legacy == by_default
    assert hashlib.sha256(legacy["image_prompt"].encode("utf-8")).hexdigest() == _A_SH01_IMAGE_SHA256
    assert legacy["negative_prompt"] == _A_SH01_NEGATIVE
    assert "prompt_layout" not in legacy and "video_prompt" not in legacy
    assert legacy["reference_images"] == ["characters/char_captain_obvious/refs/portrait.jpg",
                                          "characters/char_miss_overthink/refs/portrait.jpg",
                                          "places/place_city_square/refs/variant_day.jpg"]


# ======================================================== v2: one beat shot per scene (phase 7 stage 4, DEC-227)

def _v2_script():
    """An 8-scene v2 episode 1 (serial_60s_v2's slot list: hook, 6 body
    scenes, cliffhanger), lines that fit their slots."""
    def two(scene_id, n, a, b, speaker_a, speaker_b, **kwargs):
        return [_line(f"l{n:02d}", speaker_a, a, **kwargs), _line(f"l{n + 1:02d}", speaker_b, b)]

    scenes = [
        _scene("s01", "hook", place_id=PLACE_PARLOIR, characters=[CHAR_KIWILO, CHAR_MANGELLA], props=[PROP_PHONE],
               lines=[_line("l01", CHAR_KIWILO, "A shocking secret is about to come out.", emotion="shocked")],
               emotion="shocked"),
        _scene("s02", "setup", place_id=PLACE_PARLOIR, characters=[CHAR_MANGELLA, CHAR_KIWILO],
               lines=two("s02", 2, "I have been waiting for this all week long, Kiwilo.",
                         "Then sit down and listen to every single word.", CHAR_MANGELLA, CHAR_KIWILO)),
        _scene("s03", "rising", place_id=PLACE_PISCINE, characters=[CHAR_KIWILO, CHAR_BROCCOLIA],
               lines=two("s03", 4, "You always take the biggest lounger by the pool.",
                         "Maybe stop counting my loungers every morning.", CHAR_KIWILO, CHAR_BROCCOLIA,
                         emotion="angry")),
        _scene("s04", "peak", place_id=PLACE_PISCINE, characters=[CHAR_KIWILO, CHAR_MANGELLA, CHAR_BROCCOLIA],
               props=[PROP_PHONE],
               lines=two("s04", 6, "The phone is ringing again, who is calling now?",
                         "Nobody answers that phone, nobody at all.", CHAR_MANGELLA, CHAR_BROCCOLIA,
                         emotion="shocked"), emotion="shocked"),
        _scene("s05", "turn", place_id=PLACE_PARLOIR, characters=[CHAR_BROCCOLIA, CHAR_KIWILO],
               lines=two("s05", 8, "Something is not right here and you know it.",
                         "I know exactly who moved that phone last night.", CHAR_BROCCOLIA, CHAR_KIWILO,
                         emotion="tension"), emotion="tension"),
        _scene("s06", "setup", place_id=PLACE_PARLOIR, characters=[CHAR_MANGELLA],
               lines=[_line("l10", CHAR_MANGELLA, "Alone at last, I can finally read the message.")]),
        _scene("s07", "rising", place_id=PLACE_PISCINE, characters=[CHAR_KIWILO, CHAR_MANGELLA],
               lines=two("s07", 11, "One more secret and I am done with this island.",
                         "Then you will love what I found in your bag.", CHAR_KIWILO, CHAR_MANGELLA)),
        _scene("s08", "cliffhanger", place_id=PLACE_PARLOIR, time_variant="night",
               characters=[CHAR_KIWILO, CHAR_MANGELLA, CHAR_BROCCOLIA],
               lines=[_line("l13", CHAR_KIWILO, "Nobody is leaving this island tonight.", emotion="shocked")],
               emotion="shocked"),
    ]
    return dict(_build_script(), template_id="serial_60s_v2", scenes=scenes,
                cliffhanger={"scene_id": "s08", "reveal": "A shocking reveal.", "cut_to_black": True})


def _v2_plan(framing, subjects, action, *, lines, motion, staging, camera_motion="push_in"):
    return {"framing": framing, "camera_motion": camera_motion, "modifiers": [], "action": action,
            "subjects": subjects, "lines": lines, "clip_motion": motion, "staging": staging}


def _stage(tag, position, facing="the other", expression="tense"):
    return {"subject": tag, "position": position, "facing": facing, "expression": expression}


K, M, B, P = f"@{CHAR_KIWILO}", f"@{CHAR_MANGELLA}", f"@{CHAR_BROCCOLIA}", f"%{PROP_PHONE}"


def _v2_plans():
    """One T1 v2-shaped plan per scene, framed the way T1 v2 asks: never the
    previous shot's framing, and a close-up when the two shots before hold
    none (s03, s06)."""
    return {
        "s01": [_v2_plan("insert_prop", [P], f"{P} lights up on the booth's stool as the secret starts to leak.",
                         lines=[1], motion=f"{P} buzzes and slides a little on the stool", staging=[])],
        "s02": [_v2_plan("wide_establishing", [f"#{PLACE_PARLOIR}:day", M, K],
                         f"{M} corners {K} inside the booth, ready to make him confess.", lines=[1, 2],
                         motion=f"{M} leans in over {K}, who sinks onto the stool",
                         staging=[_stage(M, "right", "the stool", "smug"), _stage(K, "left", "the curtain")])],
        "s03": [_v2_plan("close_up", [K, B], f"{K} accuses {B} of stealing the lounger, the rivalry flares.",
                         lines=[1, 2], motion=f"{K} jabs a finger, {B} folds her arms and turns away",
                         staging=[_stage(K, "left", "her", "angry"), _stage(B, "right", "him", "scornful")])],
        "s04": [_v2_plan("medium_single", [M], f"{M} hears the phone ring again and freezes in fear.", lines=[1, 2],
                         motion=f"{M} stops mid-step and slowly turns her head toward the sound",
                         staging=[_stage(M, "centre", "the sound", "shocked")])],
        "s05": [_v2_plan("over_shoulder", [B, K], f"{K} reveals he knows who moved the phone.", lines=[1, 2],
                         motion=f"{K} steps closer and {B} backs into the wall",
                         staging=[_stage(B, "left", "him", "wary"), _stage(K, "right", "her", "cold")])],
        "s06": [_v2_plan("close_up", [M], f"{M}, finally alone, reads the secret message.", lines=[1],
                         motion=f"{M} unfolds a note and her eyes widen as she reads",
                         staging=[_stage(M, "centre", "the note", "stunned")])],
        "s07": [_v2_plan("medium_two_shot", [K, M], f"{M} reveals what she found in {K}'s bag.", lines=[1, 2],
                         motion=f"{M} pulls something from a bag, {K} lunges for it",
                         staging=[_stage(K, "left", "her", "panicked"), _stage(M, "right", "him", "triumphant")])],
        "s08": [_v2_plan("close_up", [K], f"{K} declares nobody leaves the island as the lights die.", lines=[1],
                         motion=f"{K} slowly raises his head and stares straight ahead",
                         staging=[_stage(K, "centre", "the camera", "menacing")])],
    }


def test_v2_storyboard_of_one_shot_per_scene_passes_rule_pass_without_moving_a_protected_shot():
    """A representative 8-scene v2 episode, one T1 v2 plan per scene: it
    builds a valid storyboard on serial_60s_v2's own shot range (1-2 per
    scene, not the style's 2-4), every shot a clip can cover (<= 12 s, the
    hook 3-6 s), the rule pass leaves the protected insert_prop and opening
    wide shots alone, and each shot keeps T1 v2's motion (``clip_motion``,
    the clip prompt's action) and staging (the keyframe's positions)."""
    template_v2 = templates.load_episode_template("serial_60s_v2")
    script = _v2_script()
    plans = _v2_plans()
    pair = template_v2["shots_per_scene"]

    ordered = [(scene, plans[scene["scene_id"]]) for scene in script["scenes"]]
    _moved, notes = shots.rule_pass(ordered, FRUIT_DRAMA)
    # Only the style's motion rules move anything (fruit_drama pans a wide establishing shot).
    assert notes == ["rule_pass: scene s02: camera motion changed 'push_in' -> 'pan_lr'"]

    doc, notes = shots.build_storyboard(script, plans, {sid: "t1" for sid in plans}, entities=ENTITIES,
                                        style_lock=FRUIT_DRAMA, template=template_v2, language=EN,
                                        consistency_mode="references", now=NOW, v2=True, shots_per_scene=pair)
    assert schemas.storyboard_errors(doc, min_shot_s=template_v2["min_shot_s"]) == []
    assert schemas.storyboard_context_errors(doc, script, shots_per_scene=pair) == []
    assert [shot["scene_id"] for shot in doc["shots"]] == [scene["scene_id"] for scene in script["scenes"]]
    assert [shot["framing"] for shot in doc["shots"]] == [plans[sid][0]["framing"] for sid in plans]
    durations = {shot["scene_id"]: shot["duration_s"] for shot in doc["shots"]}
    assert all(d <= 12 for d in durations.values()) and 3.0 <= durations["s01"] <= 6.0, durations

    by_scene = {shot["scene_id"]: shot for shot in doc["shots"]}
    s03 = by_scene["s03"]
    assert s03["clip_motion"] == plans["s03"][0]["clip_motion"] and s03["staging"] == plans["s03"][0]["staging"]
    assert "jabs a finger" in s03["video_prompt"] and "@" not in s03["video_prompt"]
    assert "Kiwilo" not in s03["video_prompt"] and "Broccolia" not in s03["video_prompt"]
    assert "On the left, the anthropomorphic kiwi (angry, facing her)" in s03["image_prompt"]
    assert "On the right, the anthropomorphic broccoli (scornful, facing him)" in s03["image_prompt"]
    assert "They face each other." not in s03["image_prompt"]
    # Read back, the plans keep both (a later T1 run rebuilds the board from them).
    assert shots.plans_from_storyboard(doc, script)["s03"][0]["staging"] == plans["s03"][0]["staging"]

    # A model that ignores both asks (no close-up in s01-s03, s05-s07): with one shot a scene the
    # close-up window forces the third scene's shot, and that cascades into the next scene's
    # close-up (now a repeat) -- the rules hold again afterwards, and the protected insert_prop hook
    # and opening wide never move.
    loose = _v2_plans()
    for sid, framing in (("s03", "medium_two_shot"), ("s04", "close_up"), ("s06", "medium_single")):
        loose[sid][0]["framing"] = framing
    moved, notes = shots.rule_pass([(scene, loose[scene["scene_id"]]) for scene in script["scenes"]], FRUIT_DRAMA)
    framings = [plan["framing"] for _scene, scene_plans in moved for plan in scene_plans]
    assert framings == ["insert_prop", "wide_establishing", "close_up", "medium_single", "over_shoulder",
                        "medium_single", "close_up", "medium_single"]
    assert [note for note in notes if "camera motion" not in note] == [
        "rule_pass: scene s03: no close_up/extreme_close_up in this 3-scene window, its last shot was forced "
        "to close_up",
        "rule_pass: scene s07: no close_up/extreme_close_up in this 3-scene window, its last shot was forced "
        "to close_up",
        "rule_pass: scene s04 shot 1: framing changed 'close_up' -> 'medium_single' (repeated the previous "
        "shot's framing)",
        "rule_pass: scene s08 shot 1: framing changed 'close_up' -> 'medium_single' (repeated the previous "
        "shot's framing)",
    ]


def test_v2_shot_wears_the_ledger_s_wardrobe_set():
    """Phase 7 stage 5c (A13): a v2 shot dresses each character in its
    current wardrobe set from the continuity ledger (``ledger``, the
    episode's ``context.ledger_before``), not always the look's first; no
    ledger (a story without a knowledge base) keeps the first set."""
    entities = _v2_entities()
    short = entities["characters"]["char_miss_overthink"]
    short["look"]["wardrobe_sets"].append({"id": "gala", "context": "the gala night",
                                           "items": "shimmering silver sash and tiny top hat"})
    plan = {"framing": "medium_two_shot", "action": _V2_ACTION, "lines": [1], "camera_motion": "hold",
            "modifiers": [], "subjects": ["@char_captain_obvious", "@char_miss_overthink", "#place_clocktown:day"]}

    def prompt(ledger):
        return shots.resolve_shot(plan, scene=_v2_scene(), entities=entities, style_lock=CARTOON_FLAT,
                                  consistency_mode="references", v2=True, ledger=ledger)["image_prompt"]

    state = {"location": None, "wardrobe_set": "gala", "possessions": [], "injuries": None,
             "relationship_notes": None}
    assert "shimmering silver sash and tiny top hat" in prompt({"char_miss_overthink": state})
    assert "index cards" not in prompt({"char_miss_overthink": state})
    assert "index cards" in prompt(None) and "silver sash" not in prompt(None)
