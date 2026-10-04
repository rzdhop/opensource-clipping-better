"""The story rules of steps 1-4, shared by the API and the CLI
(``clipping/aistory/workflow.py``; AI Story phase 1, stage 9).

The functions are called directly on a ``StoryStore`` under ``tmp_path``; the
API's own tests (``test_stories_api.py``, unedited) prove the routes still
answer exactly as before on top of them. Stdlib + pytest only: this file runs
in the CI environment (DEC-012). ``workflow`` is imported through a fixture,
so against the parent commit every test fails on its own.
"""

from __future__ import annotations

import importlib
import json
import pathlib

import pytest

from clipping.aistory import defaults, schemas, templates
from clipping.aistory.store import StoryStore
from clipping.providers import registry
from clipping.providers.registry import Link

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = "2026-09-26T10:00:00+00:00"
LATER = "2026-09-26T11:00:00+00:00"
TENTAFRUIT = next(c for c in templates.load_concepts() if c["concept_id"] == "tentafruit_island")

BIBLE = {
    "logline": "Des fruits en couple survivent au vote hebdomadaire d'une île de téléréalité.",
    "premise": "Chaque semaine, les couples de fruits affrontent le vote du public.",
    "tone": "mélodramatique, rapide",
    "genre_tags": ["soap", "comédie"],
    "world": {
        "setting_summary": "Une île tropicale de téléréalité.",
        "rules": ["Un vote public élimine un couple chaque semaine."],
        "time_period": "contemporain",
        "recurring_motifs": ["le téléphone-coco"],
    },
    "themes_and_values": ["la loyauté contre l'ambition"],
    "audience": {"age": "13+", "platforms": ["tiktok"]},
    "why_come_back": ["Le vote.", "Une alliance se brise.", "Le téléphone-coco."],
}


@pytest.fixture
def wf():
    return importlib.import_module("clipping.aistory.workflow")


@pytest.fixture
def stories(tmp_path):
    return StoryStore(tmp_path / "outputs", on_log=lambda line: None)


def _refused(wf, code, call, *args, **kwargs):
    with pytest.raises(wf.WorkflowError) as info:
        call(*args, **kwargs)
    assert info.value.code == code
    return info.value.detail


def _story(stories, language="fr"):
    return stories.create(language=language, now=NOW)["story_id"]


def _chosen(wf, stories):
    story_id = _story(stories)
    wf.choose_concept(stories, story_id, concept_id="tentafruit_island", now=NOW)
    return story_id


def _approved_bible(wf, stories):
    story_id = _chosen(wf, stories)
    wf.patch_story(stories, story_id, BIBLE, now=NOW)
    wf.approve_bible(stories, story_id, now=NOW)
    return story_id


# ------------------------------------------------------------------- errors

def test_a_workflow_error_carries_one_of_four_codes_and_its_detail(wf):
    assert wf.CODES == ("not_found", "conflict", "invalid", "later_phase")
    error = wf.WorkflowError("invalid", {"message": "Refused.", "errors": ["$.a: bad", "$.b: bad"]})
    assert (error.code, error.detail) == ("invalid", {"message": "Refused.", "errors": ["$.a: bad", "$.b: bad"]})
    assert str(error) == "Refused.\n  - $.a: bad\n  - $.b: bad"
    assert str(wf.WorkflowError("conflict", "Approve the bible first.")) == "Approve the bible first."
    with pytest.raises(ValueError):
        wf.WorkflowError("teapot", "x")


@pytest.mark.parametrize("story_id", ["0123456789ab", "../x", "ABC", None])
def test_a_malformed_or_unknown_story_is_not_found(wf, stories, story_id):
    assert _refused(wf, "not_found", wf.load, stories, story_id) == "Story not found"
    assert not pathlib.Path(stories.outputs_dir).exists()


def test_a_corrupt_story_is_unreadable_in_one_sentence(wf, stories):
    story_id = _story(stories)
    pathlib.Path(stories.root, story_id, "story.json").write_text('{"not": "a story"}', encoding="utf-8")

    with pytest.raises(wf.StoryUnreadable) as info:
        wf.load(stories, story_id)
    assert info.value.detail.startswith(f"Story {story_id} cannot be read: stories/{story_id}/story.json is invalid (")
    assert str(info.value) == info.value.detail and len(info.value.detail) < 400


# ------------------------------------------------------------------ concept

def test_a_library_concept_is_snapshotted_in_the_story_language(wf, stories):
    story_id = _story(stories, "en")

    story = wf.choose_concept(stories, story_id, concept_id="tentafruit_island", now=LATER)

    assert (story["concept_id"], story["title"], story["status"]) == (
        "tentafruit_island", TENTAFRUIT["title"]["en"], "concept_chosen")
    assert story["concept"]["world"] == TENTAFRUIT["world"]["en"]
    assert story["approvals"]["concept"] == LATER


@pytest.mark.parametrize("kwargs,code", [
    ({}, "invalid"),
    ({"concept_id": "tentafruit_island", "concept": {"title": "x"}}, "invalid"),
    ({"concept_id": "nope"}, "not_found"),
    ({"concept_id": "gen_01"}, "not_found"),
])
def test_choosing_needs_exactly_one_known_concept(wf, stories, kwargs, code):
    story_id = _story(stories)
    _refused(wf, code, wf.choose_concept, stories, story_id, now=NOW, **kwargs)
    assert stories.get(story_id)["status"] == "draft"


def test_a_custom_concept_is_checked_with_the_card_rules(wf, stories):
    story_id = _story(stories)
    detail = _refused(wf, "invalid", wf.choose_concept, stories, story_id, concept={"title": "x"}, now=NOW)
    assert detail["message"] == "This concept is not a valid concept card." and detail["errors"]


def test_a_different_concept_clears_the_bible_approval_and_the_same_one_does_not(wf, stories):
    story_id = _approved_bible(wf, stories)

    wf.choose_concept(stories, story_id, concept_id="tentafruit_island", now=LATER)
    assert stories.get(story_id)["approvals"]["bible"] == NOW

    story = wf.choose_concept(stories, story_id, concept_id="midnight_fridge", now=LATER)
    assert story["approvals"]["bible"] is None and story["status"] == "concept_chosen"


# -------------------------------------------------------------------- bible

def test_the_bible_approval_lists_every_missing_field(wf, stories):
    story_id = _chosen(wf, stories)
    wf.patch_story(stories, story_id, {"logline": "Une ligne.", "why_come_back": ["Un.", "Deux."]}, now=NOW)

    detail = _refused(wf, "conflict", wf.approve_bible, stories, story_id, now=NOW)

    assert detail.startswith("The bible is not complete; missing or empty: premise, tone, genre_tags, world")
    assert "why_come_back (needs 3 lines, has 2)" in detail and "logline" not in detail
    assert wf.missing_bible_fields(stories.get(story_id)) == [
        "premise", "tone", "genre_tags", "world", "themes_and_values", "audience",
        "why_come_back (needs 3 lines, has 2)"]


def test_the_bible_approval_needs_a_concept_and_then_sets_its_timestamp(wf, stories):
    story_id = _story(stories)
    assert _refused(wf, "conflict", wf.approve_bible, stories, story_id, now=NOW) == "Choose a concept first."

    story_id = _approved_bible(wf, stories)
    story = stories.get(story_id)
    assert story["approvals"]["bible"] == NOW and story["status"] == "bible_approved"


# -------------------------------------------------------------------- patch

def test_a_bible_field_clears_the_bible_approval_and_a_title_does_not(wf, stories):
    story_id = _approved_bible(wf, stories)

    story = wf.patch_story(stories, story_id, {"title": "Autre titre", "narrator": {"enabled": True}}, now=LATER)
    assert story["approvals"]["bible"] == NOW
    assert story["narrator"] == {"enabled": True, "voice": None}

    story = wf.patch_story(stories, story_id, {"tone": "plus sombre"}, now=LATER)
    assert story["approvals"]["bible"] is None and story["status"] == "concept_chosen"


def test_nothing_sent_writes_nothing(wf, stories):
    story_id = _story(stories)
    before = stories.get(story_id)
    assert wf.patch_story(stories, story_id, {}, now=LATER) == before
    assert stories.get(story_id)["updated_at"] == NOW


def test_the_generation_profile_is_merged_and_checked(wf, stories):
    story_id = _story(stories)

    story = wf.patch_story(stories, story_id, {"generation_profile": {"route": "local"}}, now=LATER)
    # Re-pinned on purpose (plan 22 stage 2, DEC-274): _story's own story is stamped "writing": "v3" at creation.
    assert story["generation_profile"] == {
        **defaults.default_generation_profile(), "route": "local", "writing": defaults.WRITING_V3}

    assert "route" in _refused(wf, "invalid", wf.patch_story, stories, story_id,
                               {"generation_profile": {"route": "cloud"}}, now=LATER)
    assert _refused(wf, "invalid", wf.patch_story, stories, story_id,
                    {"generation_profile": None}, now=LATER) == "generation_profile must be an object, not null."
    assert stories.get(story_id)["generation_profile"]["route"] == "local"


@pytest.mark.parametrize("field", ["approvals", "status", "concept", "story_id", "style_template_id"])
def test_only_the_editable_fields_may_be_patched(wf, stories, field):
    story_id = _story(stories)
    detail = _refused(wf, "invalid", wf.patch_story, stories, story_id, {field: None}, now=LATER)
    assert field in detail
    assert stories.get(story_id)["updated_at"] == NOW


def test_a_value_the_story_schema_refuses_is_invalid_with_its_errors(wf, stories):
    story_id = _story(stories)
    detail = _refused(wf, "invalid", wf.patch_story, stories, story_id, {"genre_tags": "soap"}, now=LATER)
    assert detail["message"] == "The story would not be valid with these values." and detail["errors"]


def test_the_editable_fields_are_the_api_patch_model_fields(wf):
    pytest.importorskip("pydantic")
    from web.api.models import StoryPatchRequest

    assert set(wf.PATCH_FIELDS) == set(StoryPatchRequest.model_fields)


# -------------------------------------------------------------------- style

def test_the_style_needs_an_approved_bible(wf, stories):
    story_id = _chosen(wf, stories)
    assert _refused(wf, "conflict", wf.build_style, stories, story_id, {}, now=NOW) == "Approve the bible first."


def test_the_style_defaults_to_the_concept_and_takes_overrides_on_top(wf, stories):
    story_id = _approved_bible(wf, stories)

    result = wf.build_style(stories, story_id, {"overrides": {"palette.accents": ["#FFD400"]}}, now=LATER)
    assert result["style_lock"]["template_id"] == "fruit_drama"  # tentafruit_island's style
    assert result["story"]["style_template_id"] == "fruit_drama"

    result = wf.build_style(stories, story_id, {"overrides": {"typography.ai_label": False}}, now=LATER)
    assert result["style_lock"]["overrides"] == {"palette.accents": ["#FFD400"], "typography.ai_label": False}

    result = wf.build_style(stories, story_id, {"template_id": "anime"}, now=LATER)
    assert result["style_lock"]["template_id"] == "anime" and result["style_lock"]["overrides"] == {}


@pytest.mark.parametrize("params,needle", [
    ({"nope": 1}, "Unknown style parameter(s) nope"),
    ({"template_id": "vaporwave"}, "Unknown style template 'vaporwave'"),
    ({"overrides": ["palette.accents"]}, "params.overrides must be an object"),
    ({"consistency_mode": "maybe"}, "consistency_mode must be one of"),
])
def test_a_bad_style_parameter_is_invalid_and_writes_nothing(wf, stories, params, needle):
    story_id = _approved_bible(wf, stories)
    assert needle in _refused(wf, "invalid", wf.build_style, stories, story_id, params, now=LATER)
    assert wf.style_lock(stories, story_id) is None


def test_refused_overrides_list_every_error(wf, stories):
    story_id = _approved_bible(wf, stories)
    detail = _refused(wf, "invalid", wf.build_style, stories, story_id,
                      {"overrides": {"palette.accents": ["red"], "nope.path": 1}}, now=LATER)
    assert detail["message"].startswith("The style was refused")
    assert any("palette.accents" in e for e in detail["errors"]) and any("nope.path" in e for e in detail["errors"])


def test_approving_the_style_locks_it_once(wf, stories):
    story_id = _approved_bible(wf, stories)
    assert _refused(wf, "conflict", wf.approve_style, stories, story_id, now=LATER) == (
        "There is no style to approve yet: run the style step first.")
    assert _refused(wf, "conflict", wf.require_style_draft, stories, story_id) == "Build the style first."

    wf.build_style(stories, story_id, {}, now=LATER)
    story = wf.approve_style(stories, story_id, now=LATER)

    assert story["status"] == "style_approved" and story["approvals"]["style"] == LATER
    assert wf.style_lock(stories, story_id)["locked_at"] == LATER
    assert "already locked" in _refused(wf, "conflict", wf.approve_style, stories, story_id, now=LATER)
    assert "locked" in _refused(wf, "conflict", wf.build_style, stories, story_id, {}, now=LATER)


# ------------------------------------------------------------------ grammar

def test_the_phase_one_steps_and_the_later_ones(wf):
    assert wf.PHASE1_STEPS == ("concepts", "bible", "style", "style_preview")
    assert not set(wf.PHASE1_STEPS) & set(wf.LATER_STEPS)
    assert _refused(wf, "later_phase", wf.refuse_step, "import") == "'import' arrives in a later phase."
    assert _refused(wf, "not_found", wf.refuse_step, "nope") == "Unknown step 'nope'."


@pytest.mark.parametrize("doc,code", [
    ("assets:1", "not_found"),  # phase 4 approves assets:<ep> (its route): no approval is a later phase's
    ("character:", "not_found"), ("concept", "not_found"), ("nope", "not_found"),
])
def test_the_approval_grammar(wf, doc, code):
    _refused(wf, code, wf.refuse_approval, doc)


@pytest.mark.parametrize("target,code", [
    ("bible:world", None), ("concepts", None),
    ("shot:1:sh03:frames", "later_phase"),
    ("bible:genre_tags", "invalid"), ("nope", "invalid"), ("", "invalid"),
])
def test_the_regenerate_grammar(wf, target, code):
    if code is None:
        assert wf.check_regenerate_target(target) is None
    else:
        detail = _refused(wf, code, wf.check_regenerate_target, target)
        if code == "invalid":
            assert "bible:logline" in detail and "concepts" in detail


# ----------------------------------------------------------------- key gate

def test_the_no_key_message_names_the_primaries_and_where_to_set_them(wf):
    links = registry.parse_chain(registry.DEFAULT_LLM_CHAIN)
    message = wf.no_key_message(links)
    assert message.startswith("No link in the LLM chain has an API key")
    assert message.endswith(", in Settings.")
    assert "OPENROUTER_API_KEY (https://openrouter.ai/keys) (paid)" in message
    assert wf.no_key_message(links, where="in the environment").endswith(", in the environment.")


def test_the_llm_gate_resolves_the_chain_and_keys_then_asks_readiness(wf, monkeypatch):
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.delenv("LLM_CHAIN", raising=False)
    asked = []

    def readiness(links, keys):
        asked.append((links, keys))
        return "slow"

    links, keys, refusal = wf.llm_gate({"LLM_CHAIN": "gemini/gemini-test"}, readiness=readiness)
    assert (links, keys, asked) == ([Link("gemini", "gemini-test")], {}, [])
    assert "GOOGLE_API_KEY" in refusal

    env = {"LLM_CHAIN": "gemini/gemini-test", "GOOGLE_API_KEY": "test-gemini-key"}
    assert wf.llm_gate(env, readiness=readiness)[2] == "slow"
    assert asked == [([Link("gemini", "gemini-test")], {"gemini": "test-gemini-key"})]

    assert wf.llm_gate({"LLM_CHAIN": "nope/x"}, readiness=readiness)[2].startswith("LLM_CHAIN cannot be used:")


def test_the_llm_route_leaves_paid_links_out_while_allow_paid_is_off(wf, monkeypatch):
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in ("LLM_CHAIN", "ALLOW_PAID"):
        monkeypatch.delenv(name, raising=False)
    gemini, openrouter = Link("gemini", "gemini-test"), Link("openrouter", "test-model")
    reason = "paid link: allow_paid is off (AI Story spends only on opt-in)"
    both = {"LLM_CHAIN": "gemini/gemini-test,openrouter/test-model",
            "GOOGLE_API_KEY": "test-gemini-key", "OPENROUTER_API_KEY": "test-openrouter-key"}
    asked = []

    def readiness(links, keys):
        asked.append(list(links))
        return None

    # Asked of the chain as configured (a clip job's answer), then of the links used.
    assert wf.llm_route(both, readiness=readiness) == (
        [gemini, openrouter], {"gemini": "test-gemini-key", "openrouter": "test-openrouter-key"},
        [(openrouter, reason)], None)
    assert asked == [[gemini, openrouter], [gemini]]

    asked.clear()
    assert wf.llm_route(dict(both, ALLOW_PAID="1"), readiness=readiness)[2:] == ([], None)
    assert asked == [[gemini, openrouter]]

    asked.clear()
    paid_only = {"LLM_CHAIN": "gemini/gemini-test,openrouter/test-model", "OPENROUTER_API_KEY": "k"}
    links, keys, refusal = wf.llm_gate(paid_only, readiness=readiness, where="in the environment")
    assert refusal.startswith("The only keyed link of the LLM chain is paid (openrouter/test-model)")
    assert "allow_paid" in refusal and "GOOGLE_API_KEY" in refusal and ", in the environment;" in refusal
    assert asked == []

    refusal = wf.llm_gate(dict(both, DAILY_CAP_USD="lots"), readiness=readiness)[2]
    assert refusal.startswith("The budget settings cannot be used: DAILY_CAP_USD")


# ----------------------------------------------------------------- boundary

def test_the_workflow_never_imports_web():
    source = (ROOT / "clipping" / "aistory" / "workflow.py").read_text(encoding="utf-8")
    assert "from web" not in source and "import web" not in source
    cli = (ROOT / "clipping" / "aistory" / "cli.py").read_text(encoding="utf-8")
    assert "from web" not in cli and "import web" not in cli


def test_the_story_on_disk_stays_valid_through_every_rule(wf, stories):
    story_id = _approved_bible(wf, stories)
    wf.build_style(stories, story_id, {"overrides": {"palette.accents": ["#FFD400"]}}, now=LATER)
    wf.approve_style(stories, story_id, now=LATER)

    folder = pathlib.Path(stories.root, story_id)
    assert schemas.story_bible_errors(json.loads((folder / "story.json").read_text(encoding="utf-8"))) == []
    assert schemas.style_lock_errors(json.loads((folder / "style_lock.json").read_text(encoding="utf-8"))) == []
