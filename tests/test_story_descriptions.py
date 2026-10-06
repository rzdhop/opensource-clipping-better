"""Plan 29 stage 4 (DEC-308 point 4): one written description of 80 to 120
words on every character, place and prop.

The human: "Each prompt, description etc must be around 100 words each time
for each element describing the story element". D2 / D3 / R1v2 write it in
their own call; it is said first in the element's master paragraph and first
in its own image core (after the plate's and the prop's opening exclusion).
An element without one renders byte for byte as before.
"""

from __future__ import annotations

import copy

import pytest

import test_story_assets_step as tas
import test_story_look as tsl
from test_story_assets_step import hermetic  # noqa: F401 - the step's fixtures (hermetic is autouse)
from clipping.aistory import prompt_templates, prompting, prompts, refimages, schemas


def _words(n, word="amber"):
    return " ".join([word] * n)


CHARACTER_TEXT = ("A lean figure with a round fuzzy kiwi head, brown fuzz cropped short, " + _words(80)
                  + ", a white linen shirt and a thin gold chain.")
PLACE_TEXT = "A small booth of bamboo and carved wood, a lamp in the centre, " + _words(85) + ", soft daylight."
PROP_TEXT = "A polished half coconut shell with a curly cord and a brass dial, " + _words(80) + ", worn smooth."

_D3_REPLY = {"layout_map": {"left": "palm-leaf huts", "right": "the turquoise sea", "back": "a bonfire ring",
                            "foreground": "", "centre": ""},
             "scale_note": "a wide beach, huts twice a person's height",
             "lighting": {"day": "hard tropical sun"}, "props_here": []}
_R1V2_REPLY = {"scale_cm": 18, "material": "coconut shell and brass", "colour": "brown and gold",
               "scale_phrase": "fits in one hand", "where_when": []}


# ---------------------------------------------------------------- (a) the asks

def test_each_look_writer_asks_for_a_description_of_80_to_120_words_and_its_schema_requires_one():
    for ask in (prompts._D2_ASK, prompts._d2_ask(True), prompts._D3_ASK, prompts._R1V2_ASK):
        assert "- description: one paragraph of 80 to 120 words a painter could work from, in English:" in ask
    assert "never write the words person, people, character, figure or someone" in prompts._D3_ASK
    assert "never a hand" in prompts._R1V2_ASK
    for schema in (schemas.d2_schema(), schemas.d2_schema(species=True), schemas.d3_schema(["day"], []),
                   schemas.r1v2_schema((), ())):
        assert "description" in schema["required"] and schema["properties"]["description"]["type"] == "string"


# ---------------------------------------------------------------- (b) validation

def test_a_100_word_description_validates_50_and_170_do_not_and_a_set_never_names_a_person():
    reply = tsl._d2(175)
    assert schemas.d2_errors(reply, []) == []  # a reply without one is still a look
    assert schemas.d2_errors(dict(reply, description=_words(100)), []) == []
    assert schemas.d2_errors(dict(reply, description=_words(50)), []) == [
        "$.description: 50 words, expected 80 to 120"]
    assert schemas.d2_errors(dict(reply, description=_words(170)), []) == [
        "$.description: 170 words, expected 80 to 120"]
    assert schemas.d2_errors(dict(reply, description=_words(99) + " Kiwilo"), ["Kiwilo"]) == [
        "$.description: must not mention a name ('Kiwilo')"]

    assert schemas.d3_errors(dict(_D3_REPLY, description=_words(100)), ["day"], [], []) == []
    assert schemas.d3_errors(dict(_D3_REPLY, description=_words(98) + " a person"), ["day"], [], []) == [
        "$.description: the set or the object is shown empty -- never mention 'person'"]
    assert schemas.r1v2_errors(dict(_R1V2_REPLY, description=_words(100)), []) == []
    assert schemas.r1v2_errors(dict(_R1V2_REPLY, description=_words(98) + " someone's"), []) == [
        "$.description: the set or the object is shown empty -- never mention 'someone'"]

    # A stored one (the human may edit it): 60 to 160 words, or none at all.
    stored = dict(tsl.TALL, description=_words(150))
    assert schemas.character_errors(stored) == []
    assert schemas.character_errors(dict(tsl.TALL, description=None)) == []
    assert schemas.character_errors(dict(tsl.TALL, description=_words(170))) == [
        "$.description: 170 words, expected 60 to 160"]

    doc = {"description": "old"}
    schemas.store_description(doc, {"description": "  two\n words  "})
    assert doc == {"description": "two words"}
    schemas.store_description(doc, {})
    assert doc == {"description": "two words"}


# ---------------------------------------------------------------- the fixture story

@pytest.fixture
def described(tmp_path):
    """The native-speech fixture story (looks on every entity): its story, lock, env and documents, plus a
    function giving the documents with each one's description set to *value* (``absent`` deletes it)."""
    import test_story_episode_steps as eps
    import test_story_variant_shots as tvs
    from clipping.aistory import imaging

    store = eps.StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = tvs._speech_story(store, sheet_mode="three_sheet")
    place = store.read_entity(story_id, "places", eps.PARLOIR)
    place["look"] = {"layout_map": {"left": "a bamboo chair", "right": "a curtain", "back": "a carved wall",
                                    "foreground": "", "centre": "a lamp"},
                     "scale_note": "a small booth", "lighting": {"day": "soft daylight"}, "props_here": []}
    store.write_entity(story_id, "places", place, now=tas.NOW)
    prop = store.read_entity(story_id, "props", eps.PHONE)
    prop["look"] = {"scale_cm": 15, "material": "polished coconut shell", "colour": "brown",
                    "scale_phrase": "fits in a hand", "where_when": []}
    store.write_entity(story_id, "props", prop, now=tas.NOW)
    docs = {"character": store.read_entity(story_id, "characters", eps.KIWILO), "place": place, "prop": prop}
    texts = {"character": CHARACTER_TEXT, "place": PLACE_TEXT, "prop": PROP_TEXT}
    entities = refimages._story_entities(store, story_id)

    def with_value(value):
        out = {}
        for kind, doc in docs.items():
            doc = copy.deepcopy(doc)
            doc.pop("description", None)
            if value == "absent":
                pass
            elif value == "text":
                doc["description"] = texts[kind]
            else:
                doc["description"] = value
            out[kind] = doc
        return out

    return {"store": store, "story": store.get(story_id), "lock": imaging.read_lock(store, story_id,
                                                                                   error=refimages.RefImageError),
            "env": tas._settings(**tas.FAL), "entities": entities, "texts": texts, "with": with_value,
            "ids": {"character": eps.KIWILO, "place": eps.PARLOIR, "prop": eps.PHONE}}


def _master(case, docs) -> dict:
    """``{kind: its master paragraph}`` of the story's master with *docs* in place of the fixture's."""
    entities = copy.deepcopy(case["entities"])
    for kind, plural in (("character", "characters"), ("place", "places"), ("prop", "props")):
        entities[plural][case["ids"][kind]] = docs[kind]
    sections = prompt_templates.master_sections(case["story"], case["lock"], entities, language="fr")
    keys = {"character": f"characters:{case['ids']['character']}", "place": f"places:{case['ids']['place']}",
            "prop": f"props:{case['ids']['prop']}"}
    return {kind: next(section.text for section in sections if section.key == key) for kind, key in keys.items()}


def _cores(case, docs) -> dict:
    """``{name: its v2 core}``: the portrait, the turnaround, the day plate and the prop's picture."""
    story, lock, env = case["story"], case["lock"], case["env"]
    return {
        "portrait": refimages.character_prompt(story, docs["character"], "portrait", env=env, lock=lock),
        "turnaround": refimages.character_prompt(story, docs["character"], "turnaround", env=env, lock=lock),
        "plate": refimages.place_prompt(case["store"], story, docs["place"], "day", env=env, lock=lock),
        "prop": refimages.prop_prompt(story, docs["prop"], env=env, lock=lock),
    }


# ---------------------------------------------------------------- (c) the master paragraph

def test_the_master_paragraph_of_each_element_opens_on_its_description(described):
    texts = described["texts"]
    master = _master(described, described["with"]("text"))
    assert master["character"].startswith(f"CHARACTER: {texts['character']} ")
    assert master["place"].startswith(f"PLACE: {texts['place']} ")
    assert master["prop"].startswith(f"PROP: {texts['prop']} ")


# ---------------------------------------------------------------- (d) the cores

def test_each_v2_core_opens_on_the_description_after_the_plate_and_prop_exclusion(described):
    texts = described["texts"]
    cores = _cores(described, described["with"]("text"))
    assert cores["portrait"].startswith(texts["character"] + " " + prompting._PORTRAIT_HEAD)
    assert cores["turnaround"].startswith(f"{prompting.ROLE_TEXT_PORTRAIT} {texts['character']} "
                                          f"{prompting._TURNAROUND_HEAD}")
    assert cores["plate"].startswith(f"{prompting.PLATE_EMPTY} {texts['place']} Establishing wide shot")
    assert cores["prop"].startswith(f"{prompting.PROP_ALONE} {texts['prop']} Reference image of the object")
    # The rest of each core follows, as before: the look's own lines are still there.
    plain = _cores(described, described["with"]("absent"))
    for name in ("plate", "prop"):
        head = plain[name].split(" Style: ")[0].replace(prompting.PLATE_EMPTY if name == "plate"
                                                        else prompting.PROP_ALONE, "").strip()
        assert head in cores[name]
    # The core grows by the description's room, no more (portrait on its link: the 300-word ceiling).
    assert len(cores["portrait"].split()) <= 300 and len(cores["prop"].split()) > len(plain["prop"].split())


# ---------------------------------------------------------------- (e) byte-identical without the field

def test_without_a_description_the_master_and_the_cores_are_byte_identical(described):
    absent, none, empty = (described["with"](value) for value in ("absent", None, ""))
    assert _master(described, absent) == _master(described, none) == _master(described, empty)
    assert _cores(described, absent) == _cores(described, none) == _cores(described, empty)
    # And the builders themselves: no description is the call they always took.
    lock = described["lock"]
    look = "lean body, round kiwi head"
    assert (prompting.portrait_prompt_v2(lock, look_text=look, signature_items=["gold chain"])
            == prompting.portrait_prompt_v2(lock, look_text=look, signature_items=["gold chain"],
                                            budget=prompting.SHEET_V2_MAX_WORDS, description=""))
    assert (prompting.plate_prompt_v2(lock, place_text="a small booth", variant="day")
            == prompting.plate_prompt_v2(lock, place_text="a small booth", variant="day",
                                         budget=prompting.PLATE_V2_MAX_WORDS, description=None))
    assert (prompting.prop_prompt_v2(lock, prop_text="a coconut shell")
            == prompting.prop_prompt_v2(lock, prop_text="a coconut shell", budget=prompting.PROP_V2_MAX_WORDS,
                                        description="   "))
