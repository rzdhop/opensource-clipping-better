"""The cast writers learn the species -- "in a fruit world every head must be a
fruit" (AI Story plan 26 stage 7b; the human, 2026-10-05).

Root cause: K1 and D2 were given the style's design rule but never the
universe, so Dragon Fruit's cast came out human ("Fair human skin"). In a
species world (``media_policy.species_world``: a fruit, vegetable or creature
head, from the story's universe or its style's default) K1 and D2 now carry a
species block -- the pool, the species the rest of the cast already has and
the rule -- D2 also names the head's ``species`` (required there, a human face
or skin refused), and ``apply_d2`` stores ``look.species``. A story outside a
species world (a human-cast style) is written byte for byte as before.

Pure and offline; stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import pytest

from clipping.aistory import context, media_policy, prompts, schemas, steps, templates, universes
from clipping.aistory.steps import cast
from clipping.cancel import CancelToken
from test_story_look import (SETTINGS, Events, FakeImage, FakeLLM, FakeTTS, StoryStore, _d1, _d2, _k1,  # noqa: F401
                             _story, hermetic)

FRUIT_DRAMA = templates.load_style("fruit_drama")
CINEMATIC = templates.load_style("cinematic_real")
FRUIT_STORY = {"style_template_id": "fruit_drama", "language": "en", "generation_profile": {}}
REAL_STORY = {"style_template_id": "cinematic_real", "language": "en", "generation_profile": {}}

SKETCH = {"name": "Dragon Fruit", "role": "lead", "archetype": "closer", "one_line": "Sells everything.",
          "signature_hint": "a gold pen", "species": "dragon fruit"}
PEAR_LOOK = {"build": "lean", "silhouette": "narrow", "face": "pear head, sharp eyes", "hair": "a green stalk",
             "skin_material": "waxy green pear skin", "height_cm": 175, "palette": ["green"],
             "wardrobe_sets": [{"id": "daily", "context": "every day", "items": "grey suit"}],
             "season_change": "", "species": "pear"}
HUMAN_LOOK = dict(PEAR_LOOK, face="sharp cheekbones, vivid green eyes", skin_material="fair human skin",
                  species="pear")


def _pack(style, world_text=None):
    return context.build_pack(language="en", story=FRUIT_STORY, template=style, universe=world_text)


def _d2_character():
    return {"name": "Dragon Fruit", "role": "lead", "archetype": "closer", "one_line": "Sells everything.",
            "descriptor": "a sharp salesman in a grey suit", "signature_items": ["gold pen"]}


# -------------------------------------------------------------------- (a) the signal

def test_species_world_is_the_fruit_universe_of_a_fruit_style_even_when_the_story_chose_none():
    world = media_policy.species_world(FRUIT_STORY)
    assert world is not None and world["id"] == "fruits" and world["head_kind"] == "fruit"
    assert "pear" in world["species"]
    # The concepts' explicit-only reading is untouched: this story still has no universe of its own.
    assert media_policy.universe(FRUIT_STORY, explicit=True) is None
    assert universes.universe_of(FRUIT_STORY) is None


def test_species_world_follows_the_story_choice_and_is_none_for_a_human_cast_style():
    veg = {"style_template_id": "fruit_drama", "generation_profile": {"universe": "vegetables"}}
    assert media_policy.species_world(veg)["id"] == "vegetables"
    assert media_policy.species_world(REAL_STORY) is None
    assert media_policy.species_world({}) is None
    # A universe whose things have no head to speak of (a bottle) in a human-cast style is no species world.
    bottles = {"style_template_id": "cinematic_real", "generation_profile": {"universe": "bottles"}}
    assert media_policy.species_world(bottles) is None


# -------------------------------------------------------------------- (b) the prompts

def test_k1_and_d2_carry_the_species_block_in_a_species_world_and_nothing_else_changes():
    world = media_policy.species_world(FRUIT_STORY)
    block = universes.cast_species_block(world, taken=["pear", "mango"], own="dragon fruit")
    assert block.splitlines()[:3] == [
        "Species world: Fruits -- every character is an anthropomorphic fruit.",
        f"Species pool: {', '.join(world['species'])}.",
        "Species already taken by the other characters: pear, mango.",
    ]
    assert ("Every character's head is one whole fruit at human head scale; never a human head. "
            "Give each character one species, distinct from the ones taken.") in block
    assert "The concept already made this character a dragon fruit: keep it." in block

    plain_pack, world_pack = _pack(FRUIT_DRAMA), _pack(FRUIT_DRAMA, block)
    _, k1_plain, k1_schema_plain = prompts.build_k1(plain_pack, character=SKETCH, cast_so_far=[])
    _, k1_world, k1_schema_world = prompts.build_k1(world_pack, character=SKETCH, cast_so_far=[])
    assert block in k1_world and block not in k1_plain
    assert k1_world.replace(block + "\n\n", "", 1) == k1_plain and k1_schema_world == k1_schema_plain
    # The block follows the style's design rule, before the character's own lines.
    assert k1_world.index("Character design rule:") < k1_world.index(block) < k1_world.index("Character to write:")

    d2_args = dict(character=_d2_character(), others=[], rendering="photoreal")
    _, d2_plain, d2_schema_plain = prompts.build_d2(plain_pack, **d2_args)
    _, d2_world, d2_schema_world = prompts.build_d2(world_pack, species=True, **d2_args)
    assert block in d2_world
    assert d2_world.replace(block + "\n\n", "", 1).replace(prompts._D2_SPECIES_LINE, "", 1) == d2_plain
    assert "- species: the one fruit or vegetable this character's head is" in d2_world
    assert "species" not in d2_schema_plain["properties"] and prompts._D2_ASK in d2_plain
    assert d2_schema_world["properties"]["species"]["type"] == "string"
    assert "species" in d2_schema_world["required"]


def test_a_human_cast_story_is_written_byte_for_byte_as_before():
    pack = _pack(CINEMATIC)
    assert pack.universe is None
    _, k1, _ = prompts.build_k1(pack, character=SKETCH, cast_so_far=[])
    assert "Species" not in k1 and "universe" not in k1.lower()
    _, d2, schema = prompts.build_d2(pack, character=_d2_character(), others=[], rendering="photoreal")
    assert d2.endswith(prompts._D2_ASK) and "Species" not in d2 and "species" not in schema["properties"]
    assert schemas.d2_schema() == schemas.d2_schema(species=False)


def test_the_species_already_taken_come_from_the_look_the_face_or_the_sketch():
    cast_docs = [{"look": {"species": "Pear", "face": "mango head"}},
                 {"look": {"face": "A whole kiwi head, carved eyes"}},
                 {"look": {"face": "sharp cheekbones"}},
                 {"look": None},
                 {"look": {"species": "pear"}}]
    assert universes.taken_species(cast_docs, sketches=["dragon fruit", "Kiwi"]) == ["pear", "kiwi", "dragon fruit"]


# -------------------------------------------------------------------- (c) the validator

def test_d2_errors_in_a_species_world_need_a_head_and_refuse_a_human_one():
    assert schemas.d2_errors(PEAR_LOOK, ["Dragon Fruit"], species_world=True) == []
    human = schemas.d2_errors(HUMAN_LOOK, [], species_world=True)
    assert "$.face: a species world needs a fruit or vegetable head, not a human one" not in human  # no "human" there
    assert "$.skin_material: a species world needs a fruit or vegetable head, not a human one" in human
    no_species = {key: value for key, value in PEAR_LOOK.items() if key != "species"}
    assert schemas.d2_errors(no_species, [], species_world=True)
    assert schemas.d2_errors(dict(PEAR_LOOK, species="   "), [], species_world=True) == [
        "$.species: a species world needs the head's fruit or vegetable"]
    assert schemas.d2_errors(dict(PEAR_LOOK, species="a very long whole green pear"), [], species_world=True)


def test_d2_errors_outside_a_species_world_pass_a_human_look_as_before():
    no_species = {key: value for key, value in HUMAN_LOOK.items() if key != "species"}
    assert schemas.d2_errors(no_species, ["Dragon Fruit"]) == []
    assert schemas.d2_errors(no_species, ["Dragon Fruit"], species_world=False) == []
    # A species the model volunteers is still a valid look field there (optional, at most 4 words).
    assert schemas.d2_errors(HUMAN_LOOK, []) == []
    assert schemas.d2_errors(dict(HUMAN_LOOK, species="one two three four five"), [])


# -------------------------------------------------------------------- (d) the stored look

def test_apply_d2_stores_look_species_and_a_reply_without_one_stores_none():
    doc = cast.new_character("char_dragon_fruit", "Dragon Fruit", "lead", "One line.", archetype="",
                             source="custom", now="2026-10-05T10:00:00+00:00")
    cast.apply_d2(doc, dict(PEAR_LOOK, species="  dragon fruit "))
    assert doc["look"]["species"] == "dragon fruit"
    assert schemas.character_look_errors(doc["look"]) == []
    cast.apply_d2(doc, {key: value for key, value in PEAR_LOOK.items() if key != "species"})
    assert "species" not in doc["look"]


def test_k1_character_keeps_the_concept_sketch_species_only_when_it_carries_one():
    story = {"concept": {}, "cast_sketch": []}
    character = {"name": "Dragon Fruit", "role": "lead", "archetype": "", "one_line": "x", "source": "custom"}
    assert "species" not in cast._k1_character(story, character)
    entry = {"name": "Dragon Fruit", "role": "lead", "one_line": "x", "archetype": "closer", "species": "dragon fruit"}
    original = cast.sketch_entry
    cast.sketch_entry = lambda _story, _character: entry
    try:
        assert cast._k1_character(story, character)["species"] == "dragon fruit"
    finally:
        cast.sketch_entry = original


# -------------------------------------------------------------------- through the cast step

def test_a_v2_fruit_cast_run_tells_k1_and_d2_the_species_taken_and_notes_a_species_outside_the_pool(tmp_path, hermetic):  # noqa: F811
    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store, v2=True)
    events = Events()
    second = dict(_d2(160), species="dragon fruit", face="dragon fruit head, bright eyes")
    llm = FakeLLM(events, K1=[_k1("a fuzzy kiwi", ["gold chain", "linen shirt"]),
                              _k1("a sly mango", ["red dress", "crown clip"])],
                  D1=[_d1("Né sur la plage.", with_="Mangella"), _d1("Reine du parloir.", with_="Kiwilo")],
                  D2=[_d2(175), second])
    logs = []
    ctx = steps.StepContext(job_id="job000000001", story_id=story_id, step="cast", ep=None,
                            params={"selected": ["Kiwilo", "Mangella"]}, cancel=CancelToken(),
                            settings_env=dict(SETTINGS), outputs_dir=store.outputs_dir, on_log=logs.append)
    image = FakeImage(events)
    adapters = {("image", "local"): image, ("image_edit", "local"): image, ("tts", "edge"): FakeTTS()}
    cast.run(ctx, runner=llm, time_fn=lambda: 100.0, sleep_fn=lambda seconds: None, adapters=adapters)
    assert [line for line in logs if "outside the fruits pool" in line] == [
        "ℹ️ Mangella: species 'dragon fruit' is outside the fruits pool; kept."]
    first_k1, second_k1 = (call["user"] for call in llm.of("K1"))
    first_d2, second_d2 = (call["user"] for call in llm.of("D2"))
    assert "Species world: Fruits" in first_k1 and "taken by the other characters: none yet." in first_k1
    assert "taken by the other characters: kiwi." in second_k1 and "taken by the other characters: kiwi." in second_d2
    assert "taken by the other characters: none yet." in first_d2
    assert llm.of("D2")[0]["schema"]["properties"]["species"]["type"] == "string"
    chars = {doc["char_id"]: doc for doc in store.list_entities(story_id, "characters")}
    assert chars["char_kiwilo"]["look"]["species"] == "kiwi"
    assert chars["char_mangella"]["look"]["species"] == "dragon fruit"  # outside the pool: kept, never refused
