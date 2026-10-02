"""A written episode pins a story's pipeline (the human, 2026-10-02: a story
moves onto the v2 quality pipeline -- every shot a clip -- only while no
episode has a script; ``workflow._follow_pipeline_switch``).

The episode is the episode-step tests' own (``tests/test_story_episode_steps.py``:
its hermetic fixture, store and fake writing chain). Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import importlib

import pytest

from test_story_episode_steps import (  # noqa: F401 -- hermetic (autouse) and store are fixtures
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


def test_a_scripted_story_keeps_its_pipeline(store):
    wf = importlib.import_module("clipping.aistory.workflow")
    story_id = _ready_story(store)
    _run(_new().script, store, story_id, llm=_script_llm())

    with pytest.raises(wf.WorkflowError) as info:
        wf.patch_story(store, story_id, {"generation_profile": QUALITY_V2}, now=LATER)

    assert info.value.code == wf.CONFLICT
    assert "episode 1 already has a script" in info.value.detail and "new story" in info.value.detail
    assert "pipeline" not in store.get(story_id)["generation_profile"]
    # Everything else of the profile still changes freely.
    story = wf.patch_story(store, story_id, {"generation_profile": {"tier": 2, "budget_profile": "quality"}},
                           now=LATER)
    assert story["generation_profile"]["tier"] == 2 and story["generation_profile"]["budget_profile"] == "quality"
