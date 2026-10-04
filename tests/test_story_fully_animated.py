"""Fully animated stories (phase 7, the human's ask of 2026-10-02: "only
fully animated episodes, no diaporama, even if it costs money").

Three defects kept a story made in the dashboard from ever animating:

1. The new-story form always sent a v1 profile (tier 1, ``free``: stills with
   motion), so the quality preset the API picks when FAL_KEY is set
   (``media_policy.new_story_profile``) never applied, and the form offered
   no ``quality`` budget profile at all.
2. An existing story could not be moved onto the v2 pipeline safely: a
   ``pipeline`` patch left the v1 episode template and the narrator off,
   and was accepted even after the cast was drawn without dossiers or looks.
3. A v2 story that animates every shot could still render a shot as a still
   with a zoom when its clip was missing (the "diaporama").

The workflow tests run in the pytest-only CI environment (DEC-012); the route
tests need pydantic, fastapi and httpx and skip without them.
"""

from __future__ import annotations

import importlib
import pathlib
from types import SimpleNamespace

import pytest

from clipping.aistory import defaults
from clipping.aistory.store import StoryStore

ROOT = pathlib.Path(__file__).resolve().parents[1]
WIZARD = ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "NewStoryWizard.jsx"
API_JS = ROOT / "web" / "dashboard" / "src" / "api.js"

NOW = "2026-10-02T10:00:00+00:00"
LATER = "2026-10-02T11:00:00+00:00"

QUALITY_V2 = {"tier": 2, "route": "api", "consistency_mode": "references", "budget_profile": "quality",
              "pipeline": "v2"}


@pytest.fixture
def wf():
    return importlib.import_module("clipping.aistory.workflow")


@pytest.fixture
def stories(tmp_path):
    return StoryStore(str(tmp_path / "stories"))


def _refused(wf, code, call, *args, **kwargs):
    with pytest.raises(wf.WorkflowError) as info:
        call(*args, **kwargs)
    assert info.value.code == code, info.value.detail
    detail = info.value.detail
    return detail if isinstance(detail, str) else detail["message"]


def _add_character(stories, story_id):
    from clipping.aistory.steps import cast

    doc = cast.new_character("char_01", "Lina", "lead", "Lina veut gagner.", archetype=None, source="custom",
                             now=NOW)
    stories.write_entity(story_id, "characters", doc, now=NOW)


# ------------------------------------------------- 2. moving a story onto v2

def test_a_story_switched_to_v2_before_its_cast_takes_the_v2_template_and_the_narrator(wf, stories):
    story_id = stories.create(language="fr", now=NOW)["story_id"]
    assert stories.get(story_id)["episode_template_id"] == defaults.EPISODE_TEMPLATE_ID

    story = wf.patch_story(stories, story_id, {"generation_profile": QUALITY_V2}, now=LATER)

    # Re-pinned on purpose (plan 22 stage 2, DEC-274): the story's own "writing": "v3" (stamped at
    # creation) is merged onto the patch, not replaced by it (the patch never names "writing").
    assert story["generation_profile"] == dict(QUALITY_V2, writing=defaults.WRITING_V3)
    # What store.create gives a v2 story: the v2 template and the narrator on.
    assert story["episode_template_id"] == defaults.EPISODE_TEMPLATE_ID_V2
    assert story["narrator"]["enabled"] is True


def test_a_template_sent_with_the_switch_is_kept(wf, stories):
    story_id = stories.create(language="fr", now=NOW)["story_id"]
    story = wf.patch_story(stories, story_id, {"generation_profile": QUALITY_V2,
                                               "episode_template_id": defaults.EPISODE_TEMPLATE_ID_V2,
                                               "narrator": {"enabled": False}}, now=LATER)
    assert story["episode_template_id"] == defaults.EPISODE_TEMPLATE_ID_V2
    # A narrator choice sent with the switch is the user's: kept as sent.
    assert story["narrator"]["enabled"] is False


def test_a_patch_that_keeps_the_pipeline_moves_nothing_else(wf, stories):
    story_id = stories.create(language="fr", generation_profile=QUALITY_V2, now=NOW)["story_id"]
    before = stories.get(story_id)
    story = wf.patch_story(stories, story_id, {"generation_profile": {"tier": 3}}, now=LATER)
    assert story["episode_template_id"] == before["episode_template_id"]
    assert story["narrator"] == before["narrator"]
    # Re-pinned on purpose (plan 22 stage 2, DEC-274): the story is stamped "writing": "v3" at creation.
    assert story["generation_profile"] == {**QUALITY_V2, "tier": 3, "writing": defaults.WRITING_V3}


def test_a_story_with_a_cast_may_still_switch_before_any_script(wf, stories):
    """The cast stays; the cast step run again on v2 writes the dossiers and
    looks (tests/test_stories_api_phase2.py counts those calls)."""
    story_id = stories.create(language="fr", now=NOW)["story_id"]
    _add_character(stories, story_id)

    story = wf.patch_story(stories, story_id, {"generation_profile": QUALITY_V2}, now=LATER)

    assert story["generation_profile"]["pipeline"] == "v2" and story["cast_ids"] == ["char_01"]
    assert story["episode_template_id"] == defaults.EPISODE_TEMPLATE_ID_V2


# ------------------------------------------------- 1. the new-story form

@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env
    from web.api import store as job_store
    from web.api import worker
    from web.api.routes import stories

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in ("LLM_CHAIN", "ALLOW_PAID", "PER_EPISODE_CAP_USD", "DAILY_CAP_USD", "PER_STORY_CAP_USD"):
        monkeypatch.delenv(name, raising=False)
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
    app.include_router(stories.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, worker=worker, monkeypatch=monkeypatch)


def test_the_new_profile_route_offers_the_quality_preset_only_with_fal_key(api):
    response = api.client.get("/api/stories/new-profile")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profile"] == defaults.default_generation_profile()
    assert body["quality"] is False and body["missing_keys"] == ["FAL_KEY"]
    assert body["allow_paid"] is False

    api.monkeypatch.setattr(api.worker, "_settings_env", {"FAL_KEY": "test-fal-key", "ALLOW_PAID": "true"})
    body = api.client.get("/api/stories/new-profile").json()
    # Re-pinned on purpose (plan 22 stage 5): the default with the keys is the manual native-speech
    # profile -- the quality preset's v2, tier 3, image links, every clip the human's own.
    assert body["profile"] == defaults.manual_speech_generation_profile()
    assert body["quality"] is True and body["missing_keys"] == []
    assert body["allow_paid"] is True
    # The route is a real one, not read as a malformed story id.
    assert "test-fal-key" not in api.client.get("/api/stories/new-profile").text


def test_a_story_created_without_a_profile_is_fully_animated_when_fal_is_keyed(api):
    api.monkeypatch.setattr(api.worker, "_settings_env", {"FAL_KEY": "test-fal-key"})
    story = api.client.post("/api/stories", json={"language": "fr"}).json()
    assert story["generation_profile"]["pipeline"] == "v2"
    # Re-pinned on purpose (plan 22 stage 5): the manual native-speech profile is the default.
    assert story["generation_profile"]["budget_profile"] == "native_speech_manual"
    assert story["generation_profile"]["tier"] >= 2
    assert story["episode_template_id"] == defaults.EPISODE_TEMPLATE_ID_V2


def test_the_wizard_starts_from_the_servers_profile_and_offers_quality():
    """Text contract over NewStoryWizard.jsx (CI: no node): the form asks the
    server what a new story gets, sends no profile until the user changes
    one, and can choose the quality profile and the v2 pipeline."""
    src = WIZARD.read_text(encoding="utf-8")
    assert "fetchNewStoryProfile" in src
    assert "fetchNewStoryProfile" in API_JS.read_text(encoding="utf-8")
    assert '<option value="quality">' in src
    assert "pipeline" in src
    # Untouched, the profile is left to the server (media_policy.new_story_profile).
    assert "generation_profile: profileChosen ?" in src
