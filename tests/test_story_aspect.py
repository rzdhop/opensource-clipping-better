"""The story's frame, ``generation_profile.aspect`` (plan 23 stage B7).

``"16:9"`` or ``"1:1"`` (absent = the vertical 9:16 every story had), chosen
when the story is made and never after: PATCH and the pipeline switch answer
409 "the frame is chosen when the story is made". Which links make which
frame (``video.supports_aspect``), which profiles can take one
(``media_policy.aspect_refusal``: refused on creation, disabled in the
wizard), and the clip estimate skipping a link that cannot make the
story's frame, with the reason.

Offline and hermetic; stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import pathlib

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_native_speech_plan as nsp
from test_stories_api import api  # noqa: F401 - the API fixture (routes mounted over the tmp outputs)
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_clip_estimate import timings_path  # noqa: F401 - the measured clip timings under tmp_path

from clipping.aistory import defaults, media_policy, schemas, workflow
from clipping.aistory import store as store_mod
from clipping.providers import video

NOW = tas.NOW
ROOT = pathlib.Path(__file__).resolve().parents[1]
STORY_SRC = ROOT / "web" / "dashboard" / "src" / "pages" / "story"

V2 = {"pipeline": "v2", "tier": 1, "route": "auto", "consistency_mode": "references", "budget_profile": "quality"}
NATIVE = {"pipeline": "v2", "tier": 3, "route": "api", "consistency_mode": "references",
          "budget_profile": "native_speech"}
MANUAL = dict(NATIVE, budget_profile="native_speech_manual")


# ================================================================ the field

def test_the_field_is_optional_create_only_and_absent_is_9_16():
    assert defaults.ASPECTS == ("16:9", "1:1") and defaults.FRAME_ASPECTS == ("9:16", "16:9", "1:1")
    assert "aspect" not in defaults.default_generation_profile()
    assert schemas._GENERATION_PROFILE_SCHEMA["properties"]["aspect"] == {"type": "string", "enum": ["16:9", "1:1"]}
    assert store_mod._merge_generation_profile(dict(V2, aspect="16:9"))["aspect"] == "16:9"
    for bad in ({"aspect": "9:16"}, {"aspect": "4:3"}, {"aspect": 1}, {"aspect": None}):
        with pytest.raises(ValueError):
            store_mod._merge_generation_profile(bad)
    assert "aspect" not in store_mod._PROFILE_CLEARABLE
    # the key comes after the others in every enumeration of the profile's keys
    assert list(store_mod._PROFILE_CHOICES)[-1] == "aspect"


def test_media_policy_reads_the_frame_and_its_sizes():
    assert media_policy.aspect(None) == "9:16" and media_policy.aspect({}) == "9:16"
    for frame, size, image in (("16:9", (1920, 1080), (1280, 720)), ("1:1", (1080, 1080), (1024, 1024))):
        story = {"generation_profile": dict(V2, aspect=frame)}
        assert media_policy.aspect(story) == frame and media_policy.portrait(story) is False
        assert media_policy.frame_size(story) == size and media_policy.image_size(story, (720, 1280)) == image
    story = {"generation_profile": dict(V2)}
    assert media_policy.frame_size(story) == (1080, 1920) and media_policy.portrait(story) is True
    assert media_policy.image_size(story, (111, 222)) == (111, 222)
    assert media_policy.aspect({"generation_profile": {"aspect": "4:3"}}) == "9:16"


def test_a_story_is_created_with_its_frame_and_refused_one_it_cannot_make(store):
    story = store.create(language="fr", generation_profile=dict(V2, aspect="16:9"), now=NOW)
    assert story["generation_profile"]["aspect"] == "16:9"
    plain = store.create(language="fr", generation_profile=dict(V2), now=NOW)
    assert "aspect" not in plain["generation_profile"]
    with pytest.raises(ValueError, match="v2 pipeline"):
        store.create(language="fr", generation_profile={"aspect": "16:9"}, now=NOW)
    with pytest.raises(ValueError, match="Veo makes 9:16 and 16:9"):
        store.create(language="fr", generation_profile=dict(NATIVE, aspect="1:1"), now=NOW)
    with pytest.raises(ValueError, match="Google Flow"):
        store.create(language="fr", generation_profile=dict(MANUAL, aspect="1:1"), now=NOW)
    assert store.create(language="fr", generation_profile=dict(NATIVE, aspect="16:9"), now=NOW)


def test_the_api_model_sends_the_frame_only_when_chosen():
    pytest.importorskip("pydantic")
    from web.api.models import GenerationProfileModel

    assert "aspect" not in GenerationProfileModel().model_dump()
    assert GenerationProfileModel(aspect="1:1").model_dump()["aspect"] == "1:1"
    with pytest.raises(Exception):
        GenerationProfileModel(aspect="4:3")


# ================================================================ create-only (409)

def test_patch_and_the_switch_cannot_change_the_frame(store):
    story_id = store.create(language="fr", generation_profile=dict(V2, aspect="16:9"), now=NOW)["story_id"]
    for partial in ({"aspect": "1:1"}, {"aspect": None}):
        with pytest.raises(workflow.WorkflowError) as caught:
            workflow.patch_story(store, story_id, {"generation_profile": partial}, now=NOW)
        assert caught.value.code == workflow.CONFLICT and "the frame is chosen when the story is made" in str(
            caught.value.detail)
    with pytest.raises(workflow.WorkflowError) as caught:
        workflow.switch_pipeline(store, story_id, {"aspect": "1:1"}, regenerate_episodes=True, now=NOW)
    assert caught.value.code == workflow.CONFLICT
    # a 16:9 story stays on the pipeline it was made on
    with pytest.raises(workflow.WorkflowError) as caught:
        workflow.switch_pipeline(store, story_id, {"pipeline": None}, regenerate_episodes=True, now=NOW)
    assert caught.value.code == workflow.CONFLICT
    # resending the frame it has, or another key, changes nothing about it
    same = workflow.patch_story(store, story_id, {"generation_profile": {"aspect": "16:9", "tier": 2}}, now=NOW)
    assert same["generation_profile"]["aspect"] == "16:9" and same["generation_profile"]["tier"] == 2
    # a profile that can no longer make the frame is refused (400), e.g. the local route at tier 2
    with pytest.raises(workflow.WorkflowError) as caught:
        workflow.patch_story(store, story_id, {"generation_profile": {"route": "local"}}, now=NOW)
    assert caught.value.code == workflow.INVALID and "local ComfyUI" in str(caught.value.detail)
    # a 9:16 story may say 9:16 (null), never pick another frame
    plain = store.create(language="fr", generation_profile=dict(V2), now=NOW)["story_id"]
    assert "aspect" not in workflow.patch_story(store, plain, {"generation_profile": {"aspect": None}},
                                                now=NOW)["generation_profile"]
    with pytest.raises(workflow.WorkflowError) as caught:
        workflow.patch_story(store, plain, {"generation_profile": {"aspect": "16:9"}}, now=NOW)
    assert caught.value.code == workflow.CONFLICT


def test_the_api_answers_409_and_creates_with_the_frame(api):
    created = api.client.post("/api/stories", json={"language": "fr", "generation_profile": dict(V2, aspect="1:1")})
    assert created.status_code == 201, created.text
    story_id = created.json()["story_id"]
    assert created.json()["generation_profile"]["aspect"] == "1:1"
    reply = api.client.patch(f"/api/stories/{story_id}", json={"generation_profile": {"aspect": "16:9"}})
    assert reply.status_code == 409 and "the frame is chosen when the story is made" in reply.text
    switched = api.client.post(f"/api/stories/{story_id}/switch-pipeline",
                               json={"generation_profile": {"aspect": "16:9"}})
    assert switched.status_code == 409
    refused = api.client.post("/api/stories", json={"language": "fr", "generation_profile": dict(NATIVE, aspect="1:1")})
    assert refused.status_code == 400 and "Veo" in refused.text
    offer = api.client.get("/api/stories/new-profile").json()
    assert set(offer["aspect_reasons"]) == {"pipeline", "local", "free", "veo_square", "manual_square"}


# ================================================================ the support table

SUPPORT = {
    "fal/seedance-1-pro-fast": {"9:16", "16:9", "1:1"},
    "fal/kling-2.5-turbo-std": {"9:16", "16:9", "1:1"},
    "fal/ltx-2.3-fast": {"9:16", "16:9"},
    "fal/ltx-2.5-fast": {"9:16", "16:9"},
    "gemini/veo-3.1-lite": {"9:16", "16:9"},
    "gemini/veo-3.1-fast": {"9:16", "16:9"},
    "gemini/veo-3.1": {"9:16", "16:9"},
    "manual/upload": {"9:16", "16:9"},
    "local/comfyui": {"9:16"},
}


@pytest.mark.parametrize("link", sorted(SUPPORT))
@pytest.mark.parametrize("frame", ["9:16", "16:9", "1:1"])
def test_which_link_makes_which_frame(link, frame):
    ok = frame in SUPPORT[link]
    assert video.supports_aspect(link, frame) is ok
    refusal = video.aspect_refusal(link, frame)
    assert (refusal is None) is ok
    if not ok:
        assert refusal.startswith(f"{link} cannot make {frame} clips: ")
    assert video.supports_aspect(link, None) is True


def test_the_profiles_that_cannot_take_a_frame_say_why():
    def refusal(profile, frame):
        return media_policy.aspect_refusal(profile, frame)

    for frame in ("9:16", None):
        assert refusal({}, frame) is None
    assert "v2 pipeline" in refusal({"tier": 1}, "16:9")
    assert refusal(dict(V2, tier=1), "1:1") is None  # tier 1: nothing is bought
    assert refusal(dict(V2, tier=2), "1:1") is None  # the estimate picks a link that makes it
    assert "local ComfyUI" in refusal(dict(V2, tier=2, route="local"), "16:9")
    assert "free profile" in refusal(dict(V2, tier=2, budget_profile="free"), "16:9")
    assert refusal(NATIVE, "16:9") is None and "Veo" in refusal(NATIVE, "1:1")
    assert refusal(MANUAL, "16:9") is None and "Flow" in refusal(MANUAL, "1:1")
    options = {row["id"]: row for row in media_policy.aspect_options(NATIVE)}
    assert options["9:16"]["ok"] and options["16:9"]["ok"] and not options["1:1"]["ok"]
    assert options["1:1"]["reason"] == media_policy.aspect_reasons()["veo_square"]


# ================================================================ the estimate

def _quality_story(store, tmp_path, frame):
    story_id = tas._episode(store, tmp_path)
    tce._tier(store, story_id, budget_profile="quality")
    store.update(story_id, lambda doc: doc["generation_profile"].update(pipeline="v2", aspect=frame), now=NOW)
    return story_id


def _settings(chain):
    return tas._settings(VIDEO_CHAIN=chain, GEMINI_PAID_API_KEY="paid-key", ALLOW_PAID="1",
                         PER_EPISODE_CAP_USD="20", **tas.FAL)


def test_the_estimate_skips_a_link_that_cannot_make_the_frame_with_the_reason(store, tmp_path):
    story_id = _quality_story(store, tmp_path, "1:1")
    refused = tce._units(store, story_id, _settings("gemini/veo-3.1-lite,fal/ltx-2.5-fast"))["video"]
    assert refused["ready"] is False and refused["link"] is None
    rows = {row["link"]: row for row in refused["links"]}
    assert rows["gemini/veo-3.1-lite"]["status"] == "skipped"
    assert "cannot make 1:1 clips: Veo" in rows["gemini/veo-3.1-lite"]["reason"]
    planned = tce._units(store, story_id, _settings("gemini/veo-3.1-lite,fal/seedance-1-pro-fast"))["video"]
    assert planned["link"] == "fal/seedance-1-pro-fast" and planned["count"] > 0


def test_a_9_16_estimate_is_unchanged_by_the_frame_rule(store, tmp_path):
    story_id = tas._episode(store, tmp_path)
    tce._tier(store, story_id, budget_profile="quality")
    store.update(story_id, lambda doc: doc["generation_profile"].update(pipeline="v2"), now=NOW)
    video_units = tce._units(store, story_id, _settings("gemini/veo-3.1-lite"))["video"]
    assert video_units["link"] == "gemini/veo-3.1-lite"
    assert all(row["status"] == "keyed" for row in video_units["links"] if row["link"] == "gemini/veo-3.1-lite")


def test_a_local_link_is_never_asked_for_another_frame(store, tmp_path):
    story_id = _quality_story(store, tmp_path, "16:9")
    units = tce._units(store, story_id, _settings("local/comfyui"), probe_local=True)["video"]
    local = next(row for row in units["links"] if row["link"] == "local/comfyui")
    assert local["status"] == "skipped" and "renders 9:16 clips only" in local["reason"]


def test_a_native_speech_estimate_refuses_a_speech_link_that_cannot_make_the_frame(store):
    story_id = nsp.planned_story(store)
    # Never through create (it refuses it): a document written by hand.
    store.update(story_id, lambda doc: doc["generation_profile"].update(aspect="1:1"), now=NOW)
    from clipping.aistory.steps import clips

    row = clips.link_row("gemini/veo-3.1-fast", {}, None, aspect="1:1")
    assert row["status"] == "skipped" and "Veo makes 9:16 and 16:9" in row["reason"]
    assert clips.link_row("manual/upload", {}, None, aspect="16:9")["status"] == "keyed"
    assert "Flow" in clips.link_row("manual/upload", {}, None, aspect="1:1")["reason"]


# ================================================================ the dashboard

def test_the_wizard_offers_the_frame_and_the_card_shows_it_read_only():
    wizard = (STORY_SRC / "NewStoryWizard.jsx").read_text(encoding="utf-8")
    card = (STORY_SRC / "GenerationProfileCard.jsx").read_text(encoding="utf-8")
    assert 'id="new-story-frame"' in wizard and "Vertical 9:16 (default)" in wizard
    assert "Landscape 16:9" in wizard and "Square 1:1" in wizard
    # 9:16 sends nothing; a disabled frame carries the server's reason
    assert "shownFrame !== '9:16' ? { aspect: shownFrame }" in wizard
    assert "offer.aspect_reasons" in wizard and "disabled={Boolean(reason)}" in wizard
    assert "stay 9:16" in wizard and "not Shorts" in wizard
    assert "Frame" in card and "profile.aspect || '9:16'" in card
    assert "story-profile-frame" in card and "patchStory({ aspect" not in card
