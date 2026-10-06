"""Story presets (plan 32 stage 1, ``clipping/aistory/presets.py``): a preset
makes a story the store accepts -- the profile, the recipe, the style and
the universe it names -- the caller's own choices win over it, an unknown
one is refused naming the known ones; and ``recipe`` is a story field of
its own (null on a story made without a preset, never a profile key).
"""

import json

import pytest

from clipping.aistory import defaults, presets, schemas, store, workflow

NOW = "2026-10-06T12:00:00+00:00"


@pytest.fixture
def stories(tmp_path):
    return store.StoryStore(str(tmp_path / "outputs"), on_log=lambda *_: None)


def test_the_fruit_drama_preset_makes_a_story_on_the_own_gpu_profile(stories, tmp_path):
    doc = stories.create(language="en", seed_text="A jealous pineapple.", now=NOW, **presets.apply("fruit_drama"))
    profile = doc["generation_profile"]
    assert profile["pipeline"] == defaults.PIPELINE_V2 and profile["tier"] == 3 and profile["route"] == "api"
    assert profile["consistency_mode"] == "references" and profile["budget_profile"] == defaults.OWN_GPU_PROFILE
    assert profile["universe"] == "fruits" and profile["mode"] == defaults.MODE_AGENT
    assert doc["style_template_id"] == "fruit_drama" and doc["recipe"] == "fruit_drama"
    # None for now: the default the profile gets (plan 32 stage 4 names its own format).
    assert doc["episode_template_id"] == defaults.episode_template_for(profile)
    assert schemas.story_bible_errors(doc) == []
    on_disk = json.loads((tmp_path / "outputs" / "stories" / doc["story_id"] / "story.json").read_text())
    assert on_disk["recipe"] == "fruit_drama"
    # Agent mode: the one-run story accepts it.
    workflow.require_agent_mode(stories.get(doc["story_id"]))


def test_the_preset_is_built_from_the_quality_profile_and_reads_no_key(monkeypatch):
    for name in ("FAL_KEY", "RUNPOD_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    profile = presets.apply("fruit_drama")["generation_profile"]
    assert profile == dict(defaults.quality_generation_profile(), budget_profile=defaults.OWN_GPU_PROFILE,
                           universe="fruits", mode=defaults.MODE_AGENT)
    assert set(profile) <= set(store._PROFILE_CHOICES)
    assert "recipe" not in store._PROFILE_CHOICES


def test_the_callers_choices_win_over_the_preset(stories):
    kwargs = presets.merge("fruit_drama", generation_profile={"budget_profile": "quality"},
                           episode_template_id="serial_90s_v2")
    assert kwargs["generation_profile"]["budget_profile"] == "quality"
    assert kwargs["generation_profile"]["universe"] == "fruits"
    assert kwargs["episode_template_id"] == "serial_90s_v2" and kwargs["recipe"] == "fruit_drama"
    # Another style that does not take fruits: the preset's universe is left out, the story is made.
    other = presets.merge("fruit_drama", style_template_id="cinematic_real")
    assert other["style_template_id"] == "cinematic_real" and "universe" not in other["generation_profile"]
    doc = stories.create(language="fr", now=NOW, **other)
    assert doc["style_template_id"] == "cinematic_real" and doc["recipe"] == "fruit_drama"


def test_an_unknown_preset_is_refused_naming_the_known_ones():
    with pytest.raises(presets.UnknownPreset) as caught:
        presets.apply("moon_opera")
    assert "moon_opera" in str(caught.value) and "fruit_drama" in str(caught.value)


def test_the_list_says_what_each_preset_sets():
    listed = presets.list_presets()
    assert [p["id"] for p in listed] == list(presets.PRESETS)
    first = listed[0]
    assert first["label"] and first["summary"] and set(first["sets"]) == set(first["values"])
    for word in ("tier", "pipeline", "consistency_mode"):
        assert word not in first["summary"] and word not in first["label"]


def test_a_story_without_a_preset_has_a_null_recipe_and_the_same_profile(stories):
    doc = stories.create(language="en", now=NOW)
    assert doc["recipe"] is None
    assert doc["generation_profile"] == dict(defaults.default_generation_profile(), writing=defaults.WRITING_V3)


def test_a_recipe_must_be_an_ids_shape(stories):
    for bad in ("Fruit", "1fruit", "fruit-drama", "", 3):
        with pytest.raises(ValueError):
            stories.create(language="en", recipe=bad, now=NOW)
    assert stories.create(language="en", recipe="any_recipe_2", now=NOW)["recipe"] == "any_recipe_2"


def test_a_story_written_before_the_recipe_still_reads(stories, tmp_path):
    doc = stories.create(language="en", now=NOW)
    path = tmp_path / "outputs" / "stories" / doc["story_id"] / "story.json"
    old = json.loads(path.read_text())
    old.pop("recipe")
    path.write_text(json.dumps(old))
    read = stories.get(doc["story_id"])
    assert "recipe" not in read and read.get("recipe") is None
