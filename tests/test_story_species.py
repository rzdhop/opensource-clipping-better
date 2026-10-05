"""``look.species`` -- "in a fruit world every head is a fruit" (AI Story plan
26 stage 7a; the human, 2026-10-05).

A character's look may name the fruit or vegetable its head is (``species``,
optional: "pear", "dragon fruit"). Every reader of the look then says the
head: the named cast's anchor and speech look ("Marie-Jeanne, a woman in her
thirties with a pear head, in a charcoal blazer"), ``render_look`` (the head
first, never dropped), the sheets (the head sentence once), the keyframe
judge's brief (a Head line, no "human" skin line beside it) and the master
template (never "is a human"). A look without the field reads byte for byte
as before -- the pre-change outputs are pinned below, and the goldens of
``tests/fixtures`` stay untouched.

Pure and offline; stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from clipping.aistory import context, prompting, prompt_templates as pt, schemas, shots
from clipping.aistory.steps import judge
from test_story_prompt_templates import STORY, STYLE, _entities

# Marie-Jeanne as Dragon Fruit (e7412a3efcc6) wrote her: a human look in a fruit world.
MARIE_JEANNE = {
    "char_id": "char_marie_jeanne", "name": "Marie-Jeanne", "role": "lead",
    "descriptor": ("Light-chestnut curly hair, vivid green eyes, sharp cheekbones, fair skin, athletic build, "
                   "tailored charcoal blazer, silk ivory blouse, slim black trousers, leather loafers"),
    "signature_items": ["Gold pen clipped to blazer pocket", "Vintage mechanical watch on left wrist"],
    "look": {
        "build": "Athletic, toned frame, narrow waist, broad shoulders",
        "silhouette": "Sharp tailored lines, confident upright posture",
        "face": "Sharp cheekbones, vivid green eyes, fair skin",
        "hair": "Light-chestnut curls, voluminous, shoulder-length",
        "skin_material": "Fair human skin, smooth texture",
        "height_cm": 170, "palette": ["charcoal", "ivory", "black", "gold"],
        "wardrobe_sets": [{"id": "daily", "context": "Office sales director daily wear",
                           "items": "Charcoal blazer, ivory silk blouse, black trousers, leather loafers, gold pen"}],
        "season_change": None,
        "presentation": "Woman in her thirties", "bearing": "Stands very straight, chin up, commanding presence",
    },
}
SHEET_LOCK = dict(copy.deepcopy(STYLE), sheet_background="light grey")

# Each reader's output for MARIE_JEANNE before the field existed (computed on main 1d00136).
BEFORE = {
    "anchor": "Marie-Jeanne, a woman in her thirties in a charcoal blazer",
    "named_look": "a woman in her thirties in a charcoal blazer",
    "speech_look": "a woman in her thirties in a charcoal blazer",
    "render_look": ("Woman in her thirties, Athletic, toned frame, narrow waist, broad shoulders, Sharp cheekbones, "
                    "vivid green eyes, fair skin, wearing Charcoal blazer, ivory silk blouse, black trousers, leather "
                    "loafers, gold pen, with gold pen clipped to blazer pocket and vintage mechanical watch on left "
                    "wrist"),
    "render_look_8": "Woman in her thirties, Athletic, toned frame",
    "visual_cues": "Bearing: stands very straight, chin up, commanding presence.",
    "portrait": ("Full-body character reference sheet, head to toe, front three-quarter view, neutral standing pose, "
                 "arms relaxed: Woman in her thirties, Athletic, toned frame, narrow waist, broad shoulders, Sharp "
                 "cheekbones, vivid green eyes, fair skin, wearing Charcoal blazer, ivory silk blouse, black trousers, "
                 "leather loafers, gold pen, with gold pen clipped to blazer pocket and vintage mechanical watch on "
                 "left wrist. Bearing: stands very straight, chin up, commanding presence. Style: photorealistic 3D "
                 "render of anthropomorphic fruits and vegetables with expressive human-like faces (eyes, brows, "
                 "mouths) on realistic fruit heads, human-proportioned bodies in real fabric outfits, subsurface "
                 "scattering on fruit skin. Plain light grey background, even soft studio light. Vertical 9:16. Clean "
                 "frame: no captions, lettering, logos or watermarks; one character."),
    "judge": ("Woman in her thirties, Athletic, toned frame, narrow waist, broad shoulders, Sharp cheekbones, vivid "
              "green eyes, fair…; wearing Charcoal blazer, ivory silk blouse, black trousers, leather loafers, gold "
              "pen"),
}


def _doc(species=None):
    doc = copy.deepcopy(MARIE_JEANNE)
    if species is not None:
        doc["look"]["species"] = species
    return doc


def _readers(doc) -> dict:
    """Every reader of the look, as the pipeline calls it."""
    look_text, cues = shots.render_look(doc), shots.visual_cues(doc)
    return {
        "anchor": shots.character_anchor(doc, "Marie-Jeanne"),
        "named_look": shots.named_look(doc["look"]),
        "speech_look": shots.speech_look(doc, max_words=prompting.SPEECH_LOOK_MAX_WORDS),
        "render_look": look_text,
        "render_look_8": shots.render_look(doc, max_words=8),
        "visual_cues": cues,
        # refimages.character_prompt's composition of a v2 portrait.
        "portrait": prompting.portrait_prompt_v2(SHEET_LOCK, look_text=look_text,
                                                 signature_items=doc["signature_items"], cues=cues),
        "judge": judge._character_look(doc, shots.sheet_wardrobe(doc)),
    }


# ------------------------------------------------------------------ the field

def test_the_look_takes_an_optional_species_of_a_few_words():
    look = copy.deepcopy(MARIE_JEANNE["look"])
    assert schemas.character_look_errors(look) == []
    assert schemas.character_look_errors(dict(look, species="dragon fruit")) == []
    assert schemas.character_look_errors(dict(look, species=" ")) == ["$.look.species: must be a non-empty string"]
    assert schemas.character_look_errors(dict(look, species="a b c d e")) == [
        f"$.look.species: 5 words, expected at most {schemas.LOOK_SPECIES_MAX_WORDS}"]
    assert "species" not in schemas.CHARACTER_LOOK_SCHEMA["required"]


def test_a_patch_sets_the_species_and_it_is_merged_onto_the_look(tmp_path):
    import importlib

    from clipping.aistory.store import StoryStore
    from test_story_edit_looks_knowledge import LATER, LOOK, _refused, _story

    wf = importlib.import_module("clipping.aistory.workflow")
    stories = StoryStore(str(tmp_path / "stories"))
    story_id = _story(stories)
    saved = wf.patch_entity(stories, story_id, "characters", "char_kiwilo", {"look": {"species": "pear"}}, now=LATER)
    assert saved["look"] == {**LOOK, "species": "pear"}
    assert stories.read_entity(story_id, "characters", "char_kiwilo")["look"]["species"] == "pear"
    detail = _refused(wf, wf.INVALID, wf.patch_entity, stories, story_id, "characters", "char_kiwilo",
                      {"look": {"species": ""}}, now=LATER)
    assert any("$.look.species" in error for error in detail["errors"]), detail


# ------------------------------------------------------------------ absent: byte for byte as before

def test_without_the_field_every_reader_is_byte_identical():
    assert _readers(_doc()) == BEFORE
    ec = SimpleNamespace(entities=_entities())
    text = pt.master_prompt(STORY, STYLE, ec.entities, language="fr")["text"]
    assert "CHARACTER: Marie-Jeanne is a human, with an ordinary human head and face;" in text
    assert "Skin: Fair human skin, smooth texture." in text


# ------------------------------------------------------------------ present: every reader says the head

def test_the_anchor_and_the_speech_look_of_a_named_cast_say_the_head():
    out = _readers(_doc(" pear "))
    assert out["anchor"] == "Marie-Jeanne, a woman in her thirties with a pear head, in a charcoal blazer"
    assert out["named_look"] == "a woman in her thirties with a pear head, in a charcoal blazer"
    assert out["speech_look"].startswith("a woman in her thirties with a pear head")
    # The name stays the handle (DEC-302): a species does not make a named cast a creature.
    doc = _doc("avocado")
    assert shots.named_character(doc)
    assert shots.character_anchor(doc, "Marie-Jeanne").startswith("Marie-Jeanne, a woman in her thirties with an "
                                                                  "avocado head")


def test_a_creature_anchor_says_a_head_its_handle_does_not():
    entities = _entities()
    victor = entities["characters"]["char_victor"]
    before = shots.character_anchors(entities["characters"])["char_victor"]
    victor["look"]["species"] = "eggplant"
    assert shots.character_anchors(entities["characters"])["char_victor"] == before  # the handle says it already
    victor["look"]["species"] = "pear"
    assert " character with a pear head in " in shots.character_anchors(entities["characters"])["char_victor"]


def test_render_look_starts_with_the_head_and_keeps_it_under_any_budget():
    doc = _doc("pear")
    assert shots.render_look(doc).startswith("pear head, Woman in her thirties, ")
    assert shots.render_look(doc, max_words=8) == "pear head, Woman in her thirties, Athletic"
    assert shots.render_look(doc, max_words=2) == "pear head"
    for cap in range(2, shots.LOOK_MAX_WORDS + 1):
        assert shots.render_look(doc, max_words=cap).startswith("pear head"), cap


def test_the_sheet_says_the_head_once():
    out = _readers(_doc("pear"))
    assert out["visual_cues"] == ("The head is a whole pear, the face carved into it, never a human head. Bearing: "
                                  "stands very straight, chin up, commanding presence.")
    for sheet in (out["portrait"], prompting.two_view_prompt_v2(SHEET_LOCK, look_text=out["render_look"],
                                                                signature_items=MARIE_JEANNE["signature_items"],
                                                                cues=out["visual_cues"])):
        assert ": pear head, Woman in her thirties" in sheet
        assert sheet.count("The head is a whole pear") == 1


def test_the_judge_brief_says_the_head_and_no_human_skin():
    out = _readers(_doc("pear"))
    assert out["judge"].startswith("Head: pear (a whole fruit/vegetable head, the face carved into it); Woman in "
                                   "her thirties")
    assert "human" not in out["judge"].replace("fruit/vegetable head", "")
    assert out["judge"].endswith("; wearing Charcoal blazer, ivory silk blouse, black trousers, leather loafers, "
                                 "gold pen")


def test_the_judge_brief_with_a_head_keeps_the_worst_case_inside_the_pack_budget():
    """test_story_keyframe_gate's J2 v2 worst case, each look also naming a
    4-word species: the head shares the identity's cap, so it still fits."""
    def name(i):
        return f"{'N' * 59}{i}"

    def words(n):
        return " ".join(["eightchr"] * n)

    look = {"presentation": words(8), "build": words(15), "face": words(15), "hair": words(12),
            "skin_material": words(12), "silhouette": words(12), "height_cm": 100, "palette": ["red"],
            "wardrobe_sets": [{"id": "daily", "context": words(8), "items": words(20)}], "season_change": None,
            "species": "four word species name"}
    chars = {f"char_c{i}": {"name": name(i), "descriptor": words(45), "look": look} for i in range(5)}
    props = {f"prop_p{i}": {"name": name(i), "descriptor": " ".join(["material"] * 45)} for i in range(2)}
    ec = SimpleNamespace(entities={"characters": chars, "places": {"place_set": {"name": name(9)}}, "props": props})
    tags = [f"@{cid}" for cid in chars] + ["#place_set:night"] + [f"%{pid}" for pid in props]
    shot = {"shot_id": "sh10", "framing": "medium_two_shot", "subject_tags": tags,
            "action": ("@char_c0 hands %prop_p0 to @char_c1 " * 20)[:400],
            "staging": [{"subject": f"@char_c{i}", "position": "left", "facing": "f" * 120, "expression": "e" * 120}
                        for i in range(4)]}
    kc = judge.KeyframeContext(sheets={cid: f"/sheets/{cid}.png" for cid in chars},
                               scenes={"sh10": "s04", "sh09": "s03"})
    request, _ = judge.j2_request(ec, shot, "/k/sh10.png", "sh09", "/k/sh09.png", kc)
    assert "Head: four word species name (" in request.prompt
    assert context.estimate_tokens(request.prompt, "") <= context.PACK_TOKEN_BUDGET


@pytest.mark.parametrize("species", ["pear", "Dragon Fruit"])
def test_the_template_says_the_head_and_never_a_human(species):
    entities = _entities()
    entities["characters"]["char_marie_jeanne"]["look"]["species"] = species
    text = pt.master_prompt(STORY, STYLE, entities, language="fr")["text"]
    assert (f"CHARACTER: Marie-Jeanne is an anthropomorphic character whose head is a whole {species}, the face "
            "carved into its surface, never a human head.") in text
    assert "Marie-Jeanne is a human" not in text
    assert "Skin and surface: Fair human skin" in text  # the record's own words, until 7c repairs them
    # The other casts read as before.
    assert "CHARACTER: Sam is a human, with an ordinary human head and face;" in text


def test_a_head_said_in_free_text_never_splices_a_fragment_into_the_species_sentence():
    """Without the field the template still reads the head from the look and
    the descriptor -- the thing that is the head, whole, never the role
    phrase after it ("kiwi fruit serving as a human-scale" head)."""
    kiwi = {"char_id": "char_kiwilo", "name": "Kiwilo",
            "descriptor": ("A fuzzy, dark brown ripe kiwi fruit serving as a human-scale head, set on a human body "
                           "in a linen shirt")}
    human, sentence = pt._species(kiwi, {})
    assert not human
    assert sentence == ("is an anthropomorphic character whose head is a whole dark brown ripe kiwi fruit, the face "
                        "carved into its surface, never a human head")
    assert "serving as" not in sentence and "human-scale" not in sentence
    # With the field, the field alone names the head.
    kiwi["look"] = {"species": "kiwi"}
    assert pt._species(kiwi, {}) == (False, "is an anthropomorphic character whose head is a whole kiwi, the face "
                                            "carved into its surface, never a human head")
