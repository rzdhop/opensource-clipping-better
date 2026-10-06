"""A written episode pins a story's pipeline (the human, 2026-10-02: a story
moves onto the v2 quality pipeline -- every shot a clip -- only while no
episode has a script; ``workflow._follow_pipeline_switch``) -- unless the
user asks to regenerate the written episodes on the new pipeline (the
human, 2026-10-02: "make a Regen button for episodes when: 'This story
cannot move to the v2 (quality) pipeline: episode 1 already has a script
...'"; ``workflow.switch_pipeline``): they are archived
(``StoryStore.discard_episode``) and the story switches as an unwritten one
does.

The episode is the episode-step tests' own (``tests/test_story_episode_steps.py``:
its hermetic fixture, store and fake writing chain). Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import importlib
import os

import pytest

from test_story_episode_steps import (  # noqa: F401 -- hermetic (autouse) and store are fixtures
    KIWILO,
    _approved_knowledge,
    _new,
    _ready_story,
    _run,
    _script_llm,
    hermetic,
    store,
)

LATER = "2026-10-02T11:00:00+00:00"
QUALITY_V2 = {"tier": 2, "route": "api", "consistency_mode": "references", "budget_profile": "quality",
              "pipeline": "v2"}


def _wf():
    return importlib.import_module("clipping.aistory.workflow")


def _written(store):
    """A ready legacy story whose episode 1 has a script, its memory written
    and approved from it, and proposals written from it for episode 2."""
    story_id = _ready_story(store)
    _run(_new().script, store, story_id, llm=_script_llm())
    from clipping.aistory import series_memory

    entry = {"recap": "Kiwilo ment à Mangella.", "hooks_opened": ["Qui a volé le téléphone ?"],
             "hooks_closed": [], "relationship_deltas": {}, "script_rev": 1, "at": LATER, "approved_at": LATER}
    season = store.read_doc(story_id, "season.json")
    store.write_doc(story_id, "season.json", series_memory.merge_entry(season, 1, entry), now=LATER)
    folder = os.path.join(store.episode_dir(story_id, 2, create=True), "proposals.json")
    with open(folder, "w", encoding="utf-8") as fh:
        fh.write('{"for_ep": 2}')
    return story_id


def test_a_scripted_story_keeps_its_pipeline(store):
    wf = _wf()
    story_id = _ready_story(store)
    _run(_new().script, store, story_id, llm=_script_llm())

    with pytest.raises(wf.WorkflowError) as info:
        wf.patch_story(store, story_id, {"generation_profile": QUALITY_V2}, now=LATER)

    assert info.value.code == wf.CONFLICT
    # 2026-10-02 (the Regen button): re-pinned on purpose -- the refusal is structured, so the
    # dashboard can offer to regenerate the episodes it names; its sentence points at that offer.
    detail = info.value.detail
    assert detail["code"] == "pipeline_switch_has_scripts" and detail["episodes"] == [1]
    assert "episode 1 already has a script" in detail["message"] and "new story" in detail["message"]
    assert ("Regenerate the episode on the animated format (its script, storyboard, images, clips and render are "
            "archived), or create a new story.") in detail["message"]
    assert str(info.value) == detail["message"]  # what the CLI prints
    assert "pipeline" not in store.get(story_id)["generation_profile"]
    # Everything else of the profile still changes freely.
    story = wf.patch_story(store, story_id, {"generation_profile": {"tier": 2, "budget_profile": "quality"}},
                           now=LATER)
    assert story["generation_profile"]["tier"] == 2 and story["generation_profile"]["budget_profile"] == "quality"


def test_the_switch_without_regenerating_is_refused_as_the_patch_is(store):
    wf = _wf()
    story_id = _written(store)

    with pytest.raises(wf.WorkflowError) as info:
        wf.switch_pipeline(store, story_id, QUALITY_V2, regenerate_episodes=False, now=LATER)

    assert info.value.code == wf.CONFLICT and info.value.detail["episodes"] == [1]
    assert store.list_episodes(story_id) == [1, 2] and "pipeline" not in store.get(story_id)["generation_profile"]
    assert not os.path.exists(os.path.join(store.story_dir(story_id), "episodes", "_discarded"))


def test_regenerating_archives_every_written_episode_and_moves_the_story_to_v2(store):
    from clipping.aistory import defaults, series_memory

    wf = _wf()
    story_id = _written(store)

    result = wf.switch_pipeline(store, story_id, QUALITY_V2, regenerate_episodes=True, now=LATER)

    story = result["story"]
    # Re-pinned on purpose (plan 22 stage 2, DEC-274): the story's own "writing" is merged onto the switch's
    # patch, not replaced by it (the patch never names "writing"). Plan 22 stage 3: the phase-3 fixture pins
    # "writing": "v2" (tests/test_story_episode_steps.py::_ready_story), so that is the value kept.
    assert story["generation_profile"] == dict(QUALITY_V2, writing=defaults.WRITING_V2)
    # What an unwritten story's switch does (_follow_pipeline_switch): the template and the narrator follow.
    assert story["episode_template_id"] == defaults.EPISODE_TEMPLATE_ID_V2 and story["narrator"]["enabled"] is True
    [report] = result["discarded"]
    assert report["ep"] == 1 and report["archive"] == "ep01-20261002T110000Z"
    assert report["proposals_archived"] == [2]
    archive = os.path.join(store.story_dir(story_id), "episodes", "_discarded", report["archive"])
    assert os.path.isfile(os.path.join(archive, "script.json"))
    assert store.list_episodes(story_id) == [] and wf.episodes_with_script(store, story_id) == []
    season = store.read_doc(story_id, "season.json")
    assert series_memory.memory_state(season, 1, {"rev": 1}) == "none"
    assert season["approved_at"] is not None  # the season itself stays


def test_a_switch_with_nothing_written_is_the_patch_and_archives_nothing(store):
    from clipping.aistory import defaults

    wf = _wf()
    story_id = _ready_story(store)

    result = wf.switch_pipeline(store, story_id, QUALITY_V2, regenerate_episodes=True, now=LATER)

    # Re-pinned on purpose (plan 22 stage 2, DEC-274): the story's own "writing" is kept -- plan 22 stage 3:
    # "v2", the phase-3 fixture's pin (tests/test_story_episode_steps.py::_ready_story).
    assert result["discarded"] == [] and result["story"]["generation_profile"] == dict(
        QUALITY_V2, writing=defaults.WRITING_V2)
    with pytest.raises(wf.WorkflowError) as info:
        wf.switch_pipeline(store, story_id, {"tier": 9}, regenerate_episodes=True, now=LATER)
    assert info.value.code == wf.INVALID


def test_an_invalid_profile_is_refused_before_any_episode_moves(store):
    wf = _wf()
    story_id = _written(store)
    with pytest.raises(wf.WorkflowError) as info:
        wf.switch_pipeline(store, story_id, dict(QUALITY_V2, consistency_mode="prompt_only"),
                           regenerate_episodes=True, now=LATER)
    assert info.value.code == wf.INVALID
    assert store.list_episodes(story_id) == [1, 2]


# ------------------------------------------------- what a v2 story still needs

def test_the_next_step_after_a_switch_is_the_cast_then_the_places_then_the_knowledge(store):
    from clipping.aistory import schemas

    wf = _wf()
    story_id = _written(store)
    assert wf.next_v2_step(store, store.get(story_id)) is None  # a legacy story needs none of them
    story = wf.switch_pipeline(store, story_id, {"pipeline": "v2", "consistency_mode": "references"},
                               regenerate_episodes=True, now=LATER)["story"]

    # The cast was written without dossiers or looks.
    assert wf.next_v2_step(store, story) == "cast"
    for doc in store.list_entities(story_id, "characters"):
        doc["dossier"] = {"backstory": "Né sur l'île.", "goal": "Gagner.", "need": "Confiance.",
                          "fears": "Perdre.", "secrets": [], "relationships": [],
                          "voice": {"patterns": "Court.", "vocabulary": "Simple.", "catchphrases": []},
                          "arc": "Du menteur au loyal."}
        doc["look"] = schemas.d2_look({
            "build": "lean human body", "silhouette": "upright", "face": "round fruit head", "hair": "none",
            "skin_material": "fruit skin", "height_cm": 170, "palette": ["green"],
            "wardrobe_sets": [{"id": "daily", "context": "every day", "items": "a suit"}], "season_change": ""})
        store.write_entity(story_id, "characters", doc, now=LATER)
    # The places and the prop have no look yet.
    assert wf.next_v2_step(store, story) == "places"
    for kind in ("places", "props"):
        for doc in store.list_entities(story_id, kind):
            if kind == "places":
                doc["look"] = {"layout_map": {"left": "a bar", "right": "the sea", "back": "huts", "foreground": "",
                                              "centre": ""},
                               "scale_note": "wide", "lighting": {variant: "sun" for variant in doc["time_variants"]},
                               "props_here": []}
            else:
                doc["look"] = {"scale_cm": 20, "material": "coconut", "colour": "brown", "scale_phrase": "small",
                               "where_when": []}
            store.write_entity(story_id, kind, doc, now=LATER)
    # The season is approved; the knowledge base is not written yet.
    assert wf.next_v2_step(store, story) == "knowledge"
    store.write_knowledge(story_id, _knowledge_for_every_episode(store, story_id), now=LATER)
    assert wf.next_v2_step(store, story) is None


def _knowledge_for_every_episode(store, story_id):
    doc = _approved_knowledge()
    planned = store.read_doc(story_id, "season.json")["episodes_planned"]
    doc["timeline"] = [{"ep": ep, "beats": [{"what": "Le téléphone sonne.", "place_id": None, "who": [KIWILO],
                                             "objects": [], "knows_after": {}}]} for ep in range(1, planned + 1)]
    return doc
