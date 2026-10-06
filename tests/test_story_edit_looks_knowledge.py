"""AI Story phase 7 stage 7 (A19): what phase 7's writers produce can be
edited -- a character's look and dossier, a place's look, a prop's look
(``workflow.patch_entity``) and the knowledge base (``workflow.
patch_knowledge``) -- through the same rules every other edit follows.

- An entity edit is checked by the entity's own schema (the optional blocks
  of ``schemas.py``) and against the story's ids, refused whole with every
  error, and otherwise behaves exactly as a descriptor edit: the entity's
  approval is cleared and the storyboard prompts resolved from it read
  outdated (``outdated_entities``; ``refresh_prompts`` re-resolves them).
  A look or a dossier belongs to a v2 story only (RC-M3: a legacy story is
  never given one).
- A knowledge edit (the world, a timeline beat, the props registry, a
  ledger-seed entry) is checked as the knowledge step's writes are (every
  id against the story) and moves ``rev``, so an approved base reads stale
  until it is approved again (DEC-228 part 2).

The workflow tests run in the pytest-only CI environment (DEC-012); the route
tests need fastapi and httpx and skip without them.
"""

from __future__ import annotations

import ast
import copy
import importlib
import pathlib
from types import SimpleNamespace

import pytest

from clipping.aistory import defaults, schemas
from clipping.aistory.steps import episode_common
from clipping.aistory.store import StoryStore

NOW = "2026-10-02T10:00:00+00:00"
LATER = "2026-10-02T11:00:00+00:00"
ROOT = pathlib.Path(__file__).resolve().parents[1]

LOOK = {"build": "lean and wiry", "silhouette": "upright, long arms", "face": "round, dot eyes", "hair": "green fuzz",
        "skin_material": "fuzzy brown skin", "height_cm": 170, "palette": ["brown", "green"],
        "wardrobe_sets": [{"id": "daily", "context": "every day", "items": "linen shirt, rope belt"}],
        "season_change": None}
DOSSIER = {"backstory": "Arrivé sur l'île pour gagner, il a triché au premier vote.", "goal": "Gagner l'île.",
           "need": "Faire confiance.", "fears": "Être trahi.", "secrets": ["Il a truqué le premier vote."],
           "relationships": [], "voice": {"patterns": "Phrases courtes.", "vocabulary": "Argot de plage.",
                                          "catchphrases": ["Tranquille."]},
           "arc": "Du tricheur au leader loyal."}
PLACE_LOOK = {"layout_map": {"left": "a palm tree", "right": "the confession hut", "back": "the lagoon",
                             "foreground": "", "centre": "a fire pit"},
              "scale_note": "a small beach, ten steps across", "lighting": {"day": "hard noon sun"},
              "props_here": ["prop_coco"]}
PROP_LOOK = {"scale_cm": 15, "material": "cracked coconut shell", "colour": "brown", "scale_phrase": "fits in a hand",
             "where_when": [{"ep": 1, "holder_char_id": "char_kiwilo", "place_id": "place_plage",
                             "note": "found under the sand"}]}


@pytest.fixture
def wf():
    return importlib.import_module("clipping.aistory.workflow")


@pytest.fixture
def stories(tmp_path):
    return StoryStore(str(tmp_path / "stories"))


def _story(stories, *, v2=True, approved=True):
    """A story with two characters (looks and dossiers on v2), a place and a
    prop -- each approved -- and, on v2, an approved knowledge base."""
    from clipping.aistory.steps import cast, places

    profile = defaults.quality_generation_profile() if v2 else None
    story_id = stories.create(language="fr", generation_profile=profile, now=NOW)["story_id"]
    for char_id, name in (("char_kiwilo", "Kiwilo"), ("char_mangella", "Mangella")):
        doc = cast.new_character(char_id, name, "lead", "Une ligne.", archetype="", source="custom", now=NOW)
        doc["descriptor"] = "a small brown kiwi fruit with a green fuzz"
        doc["signature_items"] = ["a rope belt", "a cracked coconut"]
        if v2:
            doc["look"] = copy.deepcopy(LOOK)
            doc["dossier"] = copy.deepcopy(DOSSIER)
        doc["approved_at"] = NOW if approved else None
        stories.write_entity(story_id, "characters", doc, now=NOW)
    place = places.new_place("place_plage", "Plage", "La plage.", now=NOW)
    place.update(descriptor="a small tropical beach", layout_notes="palm left, hut right", approved_at=NOW)
    prop = places.new_prop("prop_coco", "Coco", "Le téléphone.", None, now=NOW)
    prop.update(descriptor="a cracked coconut used as a phone", approved_at=NOW)
    if v2:
        place["look"] = copy.deepcopy(PLACE_LOOK)
        prop["look"] = copy.deepcopy(PROP_LOOK)
    stories.write_entity(story_id, "places", place, now=NOW)
    stories.write_entity(story_id, "props", prop, now=NOW)
    if v2:
        stories.write_knowledge(story_id, _knowledge(), now=NOW)
    return story_id


def _state(**changes):
    state = {"location": "place_plage", "wardrobe_set": "daily", "possessions": ["prop_coco"],
             "injuries": None, "relationship_notes": "se méfie de Mangella depuis le vote"}
    state.update(changes)
    return state


def _knowledge():
    return {
        "$schema": schemas.KNOWLEDGE_SCHEMA_NAME, "rev": 3, "approved_at": NOW, "approved_rev": 3, "updated_at": NOW,
        "world": {"geography": "La plage fait face au parloir, de l'autre côté de la lagune.",
                  "period_details": "Une téléréalité tropicale, caméras partout.",
                  "visual_motifs": ["des noix de coco fêlées"]},
        "timeline": [{"ep": 1, "beats": [
            {"what": "Kiwilo trouve le coco-téléphone sous le sable.", "place_id": "place_plage",
             "who": ["char_kiwilo"], "objects": ["prop_coco"],
             "knows_after": {"char_kiwilo": "Le téléphone appelle quelqu'un hors de l'île."}},
            {"what": "Mangella le surprend.", "place_id": "place_plage", "who": ["char_kiwilo", "char_mangella"],
             "objects": [], "knows_after": {}},
        ]}],
        "props_registry": ["prop_coco"],
        "ledger_seed": {"char_kiwilo": _state(), "char_mangella": _state(possessions=[], wardrobe_set=None)},
    }


def _refused(wf, code, call, *args, **kwargs):
    with pytest.raises(wf.WorkflowError) as info:
        call(*args, **kwargs)
    assert info.value.code == code, info.value.detail
    return info.value.detail


def _board(stories, story_id):
    """A storyboard's ``resolved_from``, stamped from the entities as they are."""
    entities = _entities(stories, story_id)
    return {"resolved_from": {eid: doc["updated_at"] for docs in entities.values() for eid, doc in docs.items()}}


def _entities(stories, story_id):
    return {kind: {doc[id_field]: doc for doc in stories.list_entities(story_id, kind)}
            for kind, id_field in (("characters", "char_id"), ("places", "place_id"), ("props", "prop_id"))}


# ------------------------------------------------------------- the field lists

def test_the_editable_fields_include_the_phase_7_blocks(wf):
    assert {"look", "dossier"} <= set(wf.CHARACTER_PATCH_FIELDS)
    assert "look" in wf.PLACE_PATCH_FIELDS and "look" in wf.PROP_PATCH_FIELDS
    assert set(wf.KNOWLEDGE_PATCH_FIELDS) == {"world", "beats", "props_registry", "ledger_seed"}
    assert set(wf.KNOWLEDGE_BEAT_PATCH_FIELDS) == {"what", "place_id", "who", "objects", "knows_after"}
    # The API's request models declare exactly these (read as text: CI has no pydantic).
    tree = ast.parse((ROOT / "web" / "api" / "models.py").read_text(encoding="utf-8"))
    declared = {node.name: {stmt.target.id for stmt in node.body if isinstance(stmt, ast.AnnAssign)}
                for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert declared["KnowledgePatchRequest"] == set(wf.KNOWLEDGE_PATCH_FIELDS)
    assert declared["KnowledgeBeatPatch"] == {"ep", "beat"} | set(wf.KNOWLEDGE_BEAT_PATCH_FIELDS)


# ------------------------------------------------------------- a character

def test_a_look_edit_is_merged_clears_the_approval_and_outdates_the_prompts_as_a_descriptor_edit_does(wf, stories):
    story_id = _story(stories)
    board = _board(stories, story_id)

    saved = wf.patch_entity(stories, story_id, "characters", "char_kiwilo",
                            {"look": {"build": "tall and gangly", "height_cm": 185}}, now=LATER)

    assert saved["look"] == {**LOOK, "build": "tall and gangly", "height_cm": 185}
    assert saved["approved_at"] is None and saved["updated_at"] == LATER
    # The prompts resolved from Kiwilo are outdated; nobody else's.
    assert wf.outdated_entities(board, _entities(stories, story_id)) == ["Kiwilo"]

    # Exactly what a descriptor edit does to Mangella.
    wf.patch_entity(stories, story_id, "characters", "char_mangella", {"descriptor": "a tall mango"}, now=LATER)
    assert wf.outdated_entities(board, _entities(stories, story_id)) == ["Kiwilo", "Mangella"]
    assert stories.read_entity(story_id, "characters", "char_mangella")["approved_at"] is None


def test_a_dossier_edit_is_merged_and_its_relationships_name_characters_of_the_story(wf, stories):
    story_id = _story(stories)
    relationship = {"with": "char_mangella", "history": "Rivaux depuis le premier vote.", "now": "Alliés forcés."}

    saved = wf.patch_entity(stories, story_id, "characters", "char_kiwilo",
                            {"dossier": {"goal": "Quitter l'île.", "relationships": [relationship]}}, now=LATER)

    assert saved["dossier"] == {**DOSSIER, "goal": "Quitter l'île.", "relationships": [relationship]}
    assert saved["approved_at"] is None

    stranger = dict(relationship, **{"with": "char_nobody"})
    detail = _refused(wf, wf.INVALID, wf.patch_entity, stories, story_id, "characters", "char_kiwilo",
                      {"dossier": {"relationships": [stranger]}}, now=LATER)
    assert any("char_nobody" in error for error in detail["errors"]), detail
    assert stories.read_entity(story_id, "characters", "char_kiwilo")["dossier"] == saved["dossier"]


def test_a_look_the_schema_refuses_is_refused_whole_with_every_error(wf, stories):
    story_id = _story(stories)
    before = stories.read_entity(story_id, "characters", "char_kiwilo")

    detail = _refused(wf, wf.INVALID, wf.patch_entity, stories, story_id, "characters", "char_kiwilo",
                      {"look": {"build": " ".join(["very"] * 30), "height_cm": 9000}}, now=LATER)

    assert detail["message"] == "The character would not be valid with these values."
    assert any("height_cm" in error for error in detail["errors"]), detail
    assert stories.read_entity(story_id, "characters", "char_kiwilo") == before
    _refused(wf, wf.INVALID, wf.patch_entity, stories, story_id, "characters", "char_kiwilo",
             {"look": "tall"}, now=LATER)


def test_a_wardrobe_set_the_ledger_seed_wears_cannot_be_dropped(wf, stories):
    story_id = _story(stories)
    other = [{"id": "gala", "context": "the finale", "items": "white suit"}]

    detail = _refused(wf, wf.INVALID, wf.patch_entity, stories, story_id, "characters", "char_kiwilo",
                      {"look": {"wardrobe_sets": other}}, now=LATER)

    assert any("daily" in error and "ledger" in error for error in detail["errors"]), detail
    # Kept alongside, it may be added.
    saved = wf.patch_entity(stories, story_id, "characters", "char_kiwilo",
                            {"look": {"wardrobe_sets": LOOK["wardrobe_sets"] + other}}, now=LATER)
    assert [item["id"] for item in saved["look"]["wardrobe_sets"]] == ["daily", "gala"]


def test_a_legacy_story_is_never_given_a_look_or_a_dossier(wf, stories):
    story_id = _story(stories, v2=False)
    before = stories.read_entity(story_id, "characters", "char_kiwilo")
    for fields in ({"look": LOOK}, {"dossier": DOSSIER}):
        detail = _refused(wf, wf.CONFLICT, wf.patch_entity, stories, story_id, "characters", "char_kiwilo",
                          fields, now=LATER)
        assert "animated story" in detail  # DEC-305 section 9: plain words
    _refused(wf, wf.CONFLICT, wf.patch_entity, stories, story_id, "places", "place_plage",
             {"look": PLACE_LOOK}, now=LATER)
    _refused(wf, wf.CONFLICT, wf.patch_entity, stories, story_id, "props", "prop_coco",
             {"look": PROP_LOOK}, now=LATER)
    assert stories.read_entity(story_id, "characters", "char_kiwilo") == before
    # Its other fields stay editable, exactly as before.
    assert wf.patch_entity(stories, story_id, "characters", "char_kiwilo", {"descriptor": "a kiwi"},
                           now=LATER)["descriptor"] == "a kiwi"


# ------------------------------------------------------------- a place, a prop

def test_a_place_look_edits_the_layout_and_the_light_per_variant(wf, stories):
    story_id = _story(stories)
    board = _board(stories, story_id)
    layout = dict(PLACE_LOOK["layout_map"], left="a leaning palm tree")

    saved = wf.patch_entity(stories, story_id, "places", "place_plage",
                            {"look": {"layout_map": layout, "lighting": {"day": "soft morning haze"}}}, now=LATER)

    assert saved["look"] == {**PLACE_LOOK, "layout_map": layout, "lighting": {"day": "soft morning haze"}}
    assert saved["approved_at"] is None
    assert wf.outdated_entities(board, _entities(stories, story_id)) == ["Plage"]

    # The schema first (a light keyed by a variant name), then the story's ids.
    for look, needle in (({"lighting": {"Not A Variant": "x"}}, "Not A Variant"),
                         ({"props_here": ["prop_coco", "prop_nowhere"]}, "props_here[1]: 'prop_nowhere'")):
        detail = _refused(wf, wf.INVALID, wf.patch_entity, stories, story_id, "places", "place_plage",
                          {"look": look}, now=LATER)
        assert any(needle in error for error in detail["errors"]), detail
    assert stories.read_entity(story_id, "places", "place_plage")["look"] == saved["look"]


def test_a_prop_look_names_characters_and_places_of_the_story(wf, stories):
    story_id = _story(stories)

    saved = wf.patch_entity(stories, story_id, "props", "prop_coco",
                            {"look": {"material": "polished coconut shell", "scale_cm": 18}}, now=LATER)
    assert saved["look"] == {**PROP_LOOK, "material": "polished coconut shell", "scale_cm": 18}
    assert saved["approved_at"] is None

    where = [{"ep": 2, "holder_char_id": "char_ghost", "place_id": "place_moon", "note": "lost"}]
    detail = _refused(wf, wf.INVALID, wf.patch_entity, stories, story_id, "props", "prop_coco",
                      {"look": {"where_when": where}}, now=LATER)
    assert any("char_ghost" in error for error in detail["errors"]), detail
    assert any("place_moon" in error for error in detail["errors"]), detail


# ------------------------------------------------------------- the knowledge base

def test_a_knowledge_edit_moves_rev_so_the_approval_reads_stale(wf, stories):
    story_id = _story(stories)
    assert episode_common.knowledge_state(stories.read_knowledge(story_id)) == "approved"

    saved = wf.patch_knowledge(stories, story_id, {"world": {"geography": "Une île ronde, la lagune au centre."}},
                               now=LATER)

    assert saved["world"] == {**_knowledge()["world"], "geography": "Une île ronde, la lagune au centre."}
    assert (saved["rev"], saved["approved_rev"]) == (4, 3)
    assert episode_common.knowledge_state(saved) == "stale"
    assert stories.read_knowledge(story_id) == saved


def test_a_beat_a_registry_and_a_ledger_entry_are_edited_by_their_ids(wf, stories):
    story_id = _story(stories)

    saved = wf.patch_knowledge(stories, story_id, {
        "beats": [{"ep": 1, "beat": 2, "what": "Mangella le surprend et lui vole le coco.",
                   "objects": ["prop_coco"], "knows_after": {"char_mangella": "Kiwilo cache un téléphone."}}],
        "props_registry": [],
        "ledger_seed": {"char_mangella": {"injuries": "une cheville foulée"}},
    }, now=LATER)

    first, second = saved["timeline"][0]["beats"]
    assert first == _knowledge()["timeline"][0]["beats"][0]
    assert second["what"] == "Mangella le surprend et lui vole le coco." and second["objects"] == ["prop_coco"]
    assert second["who"] == ["char_kiwilo", "char_mangella"]
    assert second["knows_after"] == {"char_mangella": "Kiwilo cache un téléphone."}
    assert saved["props_registry"] == []
    assert saved["ledger_seed"]["char_mangella"] == _state(possessions=[], wardrobe_set=None,
                                                          injuries="une cheville foulée")
    assert saved["ledger_seed"]["char_kiwilo"] == _state()
    assert saved["rev"] == 4


def test_a_knowledge_edit_is_checked_against_the_story_and_refused_whole(wf, stories):
    story_id = _story(stories)
    before = stories.read_knowledge(story_id)

    for fields, needle in (
        ({"beats": [{"ep": 1, "beat": 1, "place_id": "place_moon"}]}, "place_moon"),
        ({"beats": [{"ep": 1, "beat": 1, "who": ["char_ghost"]}]}, "char_ghost"),
        ({"beats": [{"ep": 1, "beat": 9, "what": "x"}]}, "beat 9"),
        ({"beats": [{"ep": 4, "beat": 1, "what": "x"}]}, "episode 4"),
        ({"beats": [{"ep": 1, "beat": 1, "new_objects": ["a bell"]}]}, "new_objects"),
        ({"props_registry": ["prop_nowhere"]}, "prop_nowhere"),
        ({"ledger_seed": {"char_ghost": _state()}}, "char_ghost"),
        ({"ledger_seed": {"char_kiwilo": {"wardrobe_set": "tuxedo"}}}, "tuxedo"),
        ({"world": {"geography": " ".join(["mot"] * 80)}}, "geography"),
    ):
        detail = _refused(wf, wf.INVALID, wf.patch_knowledge, stories, story_id, fields, now=LATER)
        assert detail["message"] == "The knowledge base would not be valid with these values."
        assert any(needle in error for error in detail["errors"]), (fields, detail)
    assert stories.read_knowledge(story_id) == before

    _refused(wf, wf.INVALID, wf.patch_knowledge, stories, story_id, {"approved_at": None}, now=LATER)
    # Nothing sent: nothing written, rev unmoved.
    assert wf.patch_knowledge(stories, story_id, {}, now=LATER) == before


def test_there_is_no_knowledge_base_to_edit_on_a_legacy_story_or_before_the_step(wf, stories):
    legacy = _story(stories, v2=False)
    _refused(wf, wf.CONFLICT, wf.patch_knowledge, stories, legacy, {"props_registry": []}, now=LATER)

    fresh = stories.create(language="fr", generation_profile=defaults.quality_generation_profile(),
                           now=NOW)["story_id"]
    detail = _refused(wf, wf.CONFLICT, wf.patch_knowledge, stories, fresh, {"props_registry": []}, now=LATER)
    assert "knowledge step" in detail


# ------------------------------------------------------------- the routes

@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from web.api import store as job_store
    from web.api import worker
    from web.api.routes import stories as stories_route

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(worker, "_settings_env", {})
    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "PERSIST_PATH", str(tmp_path / "jobs.json"))
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(worker, "OUTPUTS_ROOT", str(outputs))
    monkeypatch.setattr(worker, "UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setenv("DISABLE_AUTH", "1")

    app = FastAPI()
    app.include_router(stories_route.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, stories=StoryStore(str(outputs)), jobs=job_store)


def test_the_routes_edit_a_look_a_dossier_and_the_knowledge_base(api):
    story_id = _story(api.stories)
    base = f"/api/stories/{story_id}"

    response = api.client.patch(f"{base}/characters/char_kiwilo", json={"look": {"height_cm": 160},
                                                                       "dossier": {"arc": "Du solitaire au chef."}})
    assert response.status_code == 200, response.text
    assert response.json()["look"]["height_cm"] == 160 and response.json()["dossier"]["arc"] == "Du solitaire au chef."
    assert api.client.patch(f"{base}/places/place_plage",
                            json={"look": {"scale_note": "a wide beach"}}).json()["look"]["scale_note"] == "a wide beach"
    assert api.client.patch(f"{base}/props/prop_coco", json={"look": {"colour": "dark brown"}}).status_code == 200

    response = api.client.patch(f"{base}/knowledge", json={"beats": [{"ep": 1, "beat": 1, "place_id": None}]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["timeline"][0]["beats"][0]["place_id"] is None and body["rev"] == 4
    assert api.client.get(base).json()["knowledge"]["rev"] == 4

    response = api.client.patch(f"{base}/knowledge", json={"props_registry": ["prop_nowhere"]})
    assert response.status_code == 400
    assert any("prop_nowhere" in error for error in response.json()["detail"]["errors"])
    # rev is no field of the request (pydantic drops it): nothing sent, nothing written.
    response = api.client.patch(f"{base}/knowledge", json={"rev": 1})
    assert response.status_code == 200 and response.json()["rev"] == 4


def test_the_knowledge_route_waits_for_a_knowledge_job(api):
    story_id = _story(api.stories)
    job_id = api.jobs.create_job(kind=api.jobs.KIND_STORY_STEP, story_id=story_id, step="knowledge", ep=None,
                                 params={})
    assert api.jobs.get_job(job_id)["status"] in ("queued", "running")

    response = api.client.patch(f"/api/stories/{story_id}/knowledge", json={"props_registry": []})

    assert response.status_code == 409
    assert api.stories.read_knowledge(story_id)["rev"] == 3
