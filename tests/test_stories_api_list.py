"""The stories list's cards (dashboard overhaul stage 2, DEC-254).

``GET /api/stories`` keeps every index field as ``StoryStore.list()`` gives
it and adds, per story, what a card shows: ``cover`` (the API path of the
first character's portrait on disk, leads first), ``progress`` (the steps
done in order, out of six -- seven on a v2 story, which has the knowledge
step -- and the next one), ``episodes`` (how many, and the latest one's
furthest point), ``style_label`` and ``pipeline``. All of it is read from the
documents (``workflow.list_cards``), calling nothing; a story whose documents
cannot be read keeps no cover and zeros, and never fails the list.

The route tests use the app fixture of ``tests/test_stories_api.py`` (they
skip where fastapi is not installed, DEC-012); the rule-level tests at the
bottom run everywhere.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import test_stories_api as tsa
from clipping.aistory import schemas, workflow
from clipping.aistory.steps import cast as cast_step
from test_stories_api import api  # noqa: F401 -- the route tests' app fixture, used as it is

NOW = "2026-10-03T10:00:00+00:00"
LATER = "2026-10-03T11:00:00+00:00"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
INDEX_FIELDS = ("story_id", "title", "language", "style_template_id", "status", "created_at", "updated_at")
CARD_FIELDS = ("cover", "progress", "episodes", "style_label", "pipeline")


def _portrait_ref(name="portrait.png"):
    return {"name": name, "consistency": "base", "source": "pollinations/flux", "seed": 7, "created_at": NOW}


def _character(store, story_id, char_id, role, *, portrait=None, plant=False, created_at=NOW):
    """A character written through the store; *portrait* a ref name, *plant*
    whether its file is put on disk."""
    doc = cast_step.new_character(char_id, char_id.split("_", 1)[1].title(), role, "One line.",
                                  archetype=None, source="custom", now=created_at)
    if portrait:
        doc["refs"]["portrait"] = _portrait_ref(portrait)
    store.write_entity(story_id, "characters", doc, now=created_at)
    if portrait and plant:
        refs = store.refs_dir(story_id, "characters", char_id, create=True)
        with open(f"{refs}/{portrait}", "wb") as fh:
            fh.write(PNG)
    return doc


def _cards(api):
    response = api.client.get("/api/stories")
    assert response.status_code == 200, response.text
    return {card["story_id"]: card for card in response.json()["stories"]}


# ------------------------------------------------------------------ the route

def test_every_index_field_is_kept_as_the_store_lists_it_and_the_card_fields_are_added(api):
    first = tsa._create(api, "fr", style_template_id="fruit_drama")["story_id"]
    second = tsa._create(api, "en")["story_id"]
    listed = tsa._story_store(api).list()

    body = api.client.get("/api/stories").json()["stories"]

    assert [card["story_id"] for card in body] == [entry["story_id"] for entry in listed]
    assert {first, second} == {card["story_id"] for card in body}
    for card, entry in zip(body, listed):
        assert {key: card[key] for key in INDEX_FIELDS} == entry
        assert set(card) == set(INDEX_FIELDS) | set(CARD_FIELDS)


def test_a_story_with_a_portrait_has_the_first_lead_portrait_on_disk_as_its_cover(api):
    story_id = tsa._chosen(api, style_template_id="fruit_drama")
    store = tsa._story_store(api)
    # A support character made first, with its portrait on disk: a lead comes before it.
    _character(store, story_id, "char_mango", "support", portrait="portrait.png", plant=True)
    # A lead whose portrait is named but not on disk: skipped.
    _character(store, story_id, "char_fig", "lead", portrait="portrait.png", plant=False, created_at=NOW)
    # A lead with its portrait on disk, made after the fig: the cover.
    _character(store, story_id, "char_kiwi", "lead", portrait="portrait.webp", plant=True, created_at=LATER)

    card = _cards(api)[story_id]

    assert card["cover"] == f"/stories/{story_id}/media/characters/char_kiwi/portrait.webp"
    # The path is the media route's, under /api: it serves the file.
    served = api.client.get(f"/api{card['cover']}")
    assert served.status_code == 200 and served.content == PNG
    assert card["style_label"] == "Fruit Drama"
    assert card["progress"] == {"steps_done": 1, "steps_total": 6, "next": "bible"}
    assert card["episodes"] == {"count": 0, "latest": None}
    assert card["pipeline"] is None


def test_a_story_without_a_portrait_has_no_cover_and_starts_at_the_concepts_step(api):
    story_id = tsa._create(api, "en")["story_id"]
    _character(tsa._story_store(api), story_id, "char_kiwi", "lead")

    card = _cards(api)[story_id]

    assert card["cover"] is None
    assert card["progress"] == {"steps_done": 0, "steps_total": 6, "next": "concepts"}
    assert card["episodes"] == {"count": 0, "latest": None}
    assert card["style_label"] is None


def test_a_v2_story_counts_the_knowledge_step_and_names_its_pipeline(api):
    story_id = tsa._create(api, "fr", generation_profile={"pipeline": "v2", "budget_profile": "quality"})["story_id"]

    card = _cards(api)[story_id]

    assert card["pipeline"] == "v2"
    assert card["progress"] == {"steps_done": 0, "steps_total": 7, "next": "concepts"}


def test_the_episodes_are_counted_and_the_latest_one_reads_as_a_draft_with_no_document(api):
    story_id = tsa._create(api, "fr")["story_id"]
    store = tsa._story_store(api)
    store.episode_dir(story_id, 1, create=True)
    store.episode_dir(story_id, 3, create=True)

    assert _cards(api)[story_id]["episodes"] == {"count": 2, "latest": {"ep": 3, "state": "draft"}}


def test_a_story_whose_documents_cannot_be_read_keeps_no_cover_and_zeros(api):
    good = tsa._create(api, "en", style_template_id="anime")["story_id"]
    broken = tsa._create(api, "fr", style_template_id="fruit_drama")["story_id"]
    store = tsa._story_store(api)
    _character(store, broken, "char_kiwi", "lead", portrait="portrait.png", plant=True)
    with open(f"{store.story_dir(broken)}/story.json", "w", encoding="utf-8") as fh:
        fh.write("{not json")

    cards = _cards(api)

    assert cards[broken]["cover"] is None
    assert cards[broken]["progress"] == {"steps_done": 0, "steps_total": 0, "next": None}
    assert cards[broken]["episodes"] == {"count": 0, "latest": None}
    assert cards[broken]["pipeline"] is None
    # The index entry itself is kept, and its style is still named.
    assert cards[broken]["style_label"] == "Fruit Drama"
    assert cards[good]["progress"] == {"steps_done": 0, "steps_total": 6, "next": "concepts"}


def test_an_unreadable_character_never_fails_the_cover_of_its_story(api):
    story_id = tsa._create(api, "en")["story_id"]
    store = tsa._story_store(api)
    _character(store, story_id, "char_kiwi", "lead", portrait="portrait.png", plant=True)
    _character(store, story_id, "char_fig", "lead", portrait="portrait.png", plant=True)
    with open(f"{store.entity_dir(story_id, 'characters', 'char_fig')}/character.json", "w",
              encoding="utf-8") as fh:
        fh.write("[]")

    assert _cards(api)[story_id]["cover"] == f"/stories/{story_id}/media/characters/char_kiwi/portrait.png"


# ------------------------------------------------------------- the rules

class _Episodes:
    """``read_episode_doc`` answered from a dict of documents."""

    def __init__(self, docs, *, broken=()):
        self.docs = docs
        self.broken = set(broken)

    def read_episode_doc(self, story_id, ep, name):
        if name in self.broken:
            raise schemas.SchemaError(name, ["not valid JSON"])
        return self.docs.get(name)


@pytest.mark.parametrize("docs, state", [
    ({}, "draft"),
    ({"script.json": {"approved_at": None}}, "draft"),
    ({"script.json": {"approved_at": NOW}}, "written"),
    ({"script.json": {"approved_at": NOW}, "storyboard.json": {"approved_at": NOW}}, "planned"),
    ({"storyboard.json": {"approved_at": NOW}, "assets.json": {"approved": {"at": NOW}}}, "assets"),
    ({"assets.json": {"approved": None}, "render_manifest.json": {"output": {"path": "x"}}}, "rendered"),
    ({"script.json": {"approved_at": NOW}, "render_manifest.json": {"output": None}}, "written"),
])
def test_an_episode_reads_as_its_furthest_point(docs, state):
    assert workflow.list_episode_state(_Episodes(docs), "abc", 1) == state
    assert state in workflow.LIST_EPISODE_STATES


def test_an_episode_whose_document_cannot_be_read_is_a_draft():
    stories = _Episodes({"script.json": {"approved_at": NOW}}, broken={"render_manifest.json"})
    assert workflow.list_episode_state(stories, "abc", 1) == "draft"


def test_the_progress_is_the_contiguous_prefix_of_the_approvals():
    story = {"story_id": "abc", "generation_profile": {},
             "approvals": {"concept": NOW, "bible": NOW, "style": None, "cast": NOW}}
    assert workflow.list_progress(None, story) == {"steps_done": 2, "steps_total": 6, "next": "style"}
    story["approvals"].update(style=NOW, places=NOW, season=NOW)
    assert workflow.list_progress(None, story) == {"steps_done": 6, "steps_total": 6, "next": None}


@pytest.mark.parametrize("knowledge, done", [
    (None, 6),
    ({"rev": 2, "approved_at": None, "approved_rev": None}, 6),
    ({"rev": 2, "approved_at": NOW, "approved_rev": 1}, 6),
    ({"rev": 2, "approved_at": NOW, "approved_rev": 2}, 7),
])
def test_a_v2_story_is_done_once_its_knowledge_base_is_approved_and_current(knowledge, done):
    stories = SimpleNamespace(read_knowledge=lambda story_id: knowledge)
    story = {"story_id": "abc", "generation_profile": {"pipeline": "v2"},
             "approvals": {key: NOW for key in ("concept", "bible", "style", "cast", "places", "season")}}
    progress = workflow.list_progress(stories, story)
    assert progress["steps_total"] == 7 and progress["steps_done"] == done
    assert progress["next"] == (None if done == 7 else "knowledge")


def test_an_unreadable_knowledge_base_is_a_knowledge_step_not_done():
    def broken(story_id):
        raise schemas.SchemaError("knowledge.json", ["not valid JSON"])

    story = {"story_id": "abc", "generation_profile": {"pipeline": "v2"},
             "approvals": {key: NOW for key in ("concept", "bible", "style", "cast", "places", "season")}}
    progress = workflow.list_progress(SimpleNamespace(read_knowledge=broken), story)
    assert progress == {"steps_done": 6, "steps_total": 7, "next": "knowledge"}


def test_the_style_label_is_the_shipped_name_else_the_id():
    assert workflow.style_label("fruit_drama") == "Fruit Drama"
    assert workflow.style_label("storybook_watercolor") == "Storybook Watercolor"
    assert workflow.style_label("vaporwave") == "vaporwave"
    assert workflow.style_label(None) is None
