"""AI Story phase 7, stage 5a: the story knowledge document and the ledger.

A v2 story gains a story-level ``knowledge.json`` (``story_knowledge_v1``:
world geography, a per-episode timeline of beats, the props registered by the
knowledge step, and each character's starting ledger), and a series-memory
entry gains an optional ``ledger`` of the same per-character shape. Both are
checked when present and never required: a stored story has no knowledge.json
and its memory entries no ledger, and every one of them still validates (the
plan's schema rule, RC-M3). A knowledge document naming a character, place
or prop the story does not have is refused when it is written.

Stdlib + pytest (DEC-012). Offline: nothing leaves ``tmp_path``.
"""

from __future__ import annotations

import copy

import pytest

from clipping.aistory import schemas
from clipping.aistory import store as store_mod
from clipping.aistory.store import StoryStore

NOW = "2026-10-01T10:00:00+00:00"


def _words(n, word="mot"):
    return " ".join([word] * n)


def _state(**changes):
    state = {"location": "place_plage", "wardrobe_set": "daily", "possessions": ["prop_coco"],
             "injuries": None, "relationship_notes": "se méfie de Mangella depuis le vote"}
    state.update(changes)
    return state


def _knowledge(**changes):
    doc = {
        "$schema": "story_knowledge_v1",
        "rev": 1,
        "approved_at": None,
        "updated_at": NOW,
        "world": {"geography": "La plage fait face au parloir, de l'autre côté de la lagune.",
                  "period_details": "Une téléréalité tropicale, caméras partout.",
                  "visual_motifs": ["des noix de coco fêlées", "des torches au crépuscule"]},
        "timeline": [{"ep": 1, "beats": [{
            "what": "Kiwilo trouve le coco-téléphone sous le sable.",
            "place_id": "place_plage", "who": ["char_kiwilo"], "objects": ["prop_coco"],
            "knows_after": {"char_kiwilo": "Le téléphone appelle quelqu'un hors de l'île."},
        }]}],
        "props_registry": ["prop_coco"],
        "ledger_seed": {"char_kiwilo": _state(), "char_mangella": _state(possessions=[], wardrobe_set=None)},
    }
    doc.update(changes)
    return doc


def _memory_entry(**changes):
    entry = {"recap": "Kiwilo trouve un téléphone.", "hooks_opened": [], "hooks_closed": [],
             "relationship_deltas": {}, "script_rev": 1, "at": NOW, "approved_at": None}
    entry.update(changes)
    return entry


def _story_with_entities(store):
    from clipping.aistory.steps import cast, places

    story_id = store.create(language="fr", now=NOW)["story_id"]
    for char_id, name in (("char_kiwilo", "Kiwilo"), ("char_mangella", "Mangella")):
        doc = cast.new_character(char_id, name, "lead", "Une ligne.", archetype="", source="custom", now=NOW)
        doc["look"] = {"build": "lean", "silhouette": "upright", "face": "round", "hair": "fuzz",
                       "skin_material": "fuzzy skin", "height_cm": 170, "palette": ["brown"],
                       "wardrobe_sets": [{"id": "daily", "context": "every day", "items": "linen shirt"}],
                       "season_change": None}
        store.write_entity(story_id, "characters", doc, now=NOW)
    store.write_entity(story_id, "places", places.new_place("place_plage", "Plage", "La plage.", now=NOW), now=NOW)
    store.write_entity(story_id, "props", places.new_prop("prop_coco", "Coco", "Le téléphone.", None, now=NOW),
                       now=NOW)
    return story_id


def test_legacy_docs_validate_and_v2_blocks_checked(tmp_path):
    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)

    # A stored (legacy) story: no knowledge.json, and a memory entry without a ledger.
    legacy_id = store.create(language="fr", now=NOW)["story_id"]
    assert store_mod.KNOWLEDGE_DOC == "knowledge.json"
    assert store.read_knowledge(legacy_id) is None
    assert schemas.memory_entry_errors(_memory_entry()) == []
    assert "ledger" not in schemas._MEMORY_ENTRY_SCHEMA["required"]

    # The memory entry's ledger, when present, is checked: the same per-character shape.
    assert schemas.memory_entry_errors(_memory_entry(ledger={"char_kiwilo": _state()})) == []
    assert schemas.memory_entry_errors(_memory_entry(ledger={"char_kiwilo": _state(injuries=_words(11))})) == [
        "$.ledger.char_kiwilo.injuries: 11 words, expected at most 10"]
    assert schemas.memory_entry_errors(_memory_entry(ledger={"kiwilo": _state()}))
    assert schemas.memory_entry_errors(_memory_entry(ledger={"char_kiwilo": _state(possessions=["char_x"])}))

    # The knowledge document: a minimal one (sections not written yet) and a full one validate.
    minimal = {"$schema": "story_knowledge_v1", "rev": 1, "approved_at": None, "updated_at": NOW}
    assert schemas.knowledge_errors(minimal) == []
    assert schemas.knowledge_errors(_knowledge()) == []

    # Over a cap, or out of shape: refused, with the path named.
    over = _knowledge()
    over["timeline"][0]["beats"][0]["what"] = _words(26)
    assert schemas.knowledge_errors(over) == ["$.timeline[0].beats[0].what: 26 words, expected at most 25"]
    nine = _knowledge()
    nine["timeline"][0]["beats"] *= 9
    assert schemas.knowledge_errors(nine)
    knows = _knowledge()
    knows["timeline"][0]["beats"][0]["knows_after"] = {"char_kiwilo": _words(16)}
    assert schemas.knowledge_errors(knows) == [
        "$.timeline[0].beats[0].knows_after.char_kiwilo: 16 words, expected at most 15"]
    assert schemas.knowledge_errors(_knowledge(world=dict(_knowledge()["world"], geography=_words(61))))
    assert schemas.knowledge_errors(_knowledge(world=dict(_knowledge()["world"], visual_motifs=["a"] * 5)))
    twice = _knowledge()
    twice["timeline"].append(copy.deepcopy(twice["timeline"][0]))
    assert schemas.knowledge_errors(twice) == ["$.timeline: episodes [1, 1] must be in increasing order, each once"]
    assert schemas.knowledge_errors(_knowledge(props_registry=["prop_coco", "prop_coco"]))
    assert schemas.knowledge_errors(_knowledge(ledger_seed={"kiwilo": _state()}))
    assert schemas.knowledge_errors(_knowledge(extra=1))
    # An approval needs every section written.
    assert schemas.knowledge_errors(dict(minimal, approved_at=NOW)) == [
        "$.approved_at: the knowledge is approved with world, timeline, props_registry, ledger_seed not written"]

    # Through the store: written atomically, read back; an id the story lacks is refused.
    story_id = _story_with_entities(store)
    written = store.write_knowledge(story_id, _knowledge(), now=NOW)
    assert store.read_knowledge(story_id) == written
    unknown = _knowledge()
    unknown["timeline"][0]["beats"][0]["who"] = ["char_nobody"]
    with pytest.raises(schemas.SchemaError) as refused:
        store.write_knowledge(story_id, unknown, now=NOW)
    assert refused.value.errors == ["$.timeline[0].beats[0].who[0]: 'char_nobody' is no character of the story"]
    for bad in (_knowledge(ledger_seed={"char_nobody": _state()}),
                _knowledge(ledger_seed={"char_kiwilo": _state(possessions=["prop_nothing"])}),
                _knowledge(ledger_seed={"char_kiwilo": _state(wardrobe_set="gala")}),
                _knowledge(props_registry=["prop_nothing"])):
        with pytest.raises(schemas.SchemaError):
            store.write_knowledge(story_id, bad, now=NOW)
    assert store.read_knowledge(story_id) == written  # nothing refused was written
