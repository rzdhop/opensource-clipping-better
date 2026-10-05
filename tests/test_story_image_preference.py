"""Plan 23 stage A9: a per-story image provider preference.

``generation_profile.image_preference`` (``defaults.IMAGE_PREFERENCES``, the single
value ``gemini_first``; absent = fal first, today's chains byte for byte) re-sorts
the four image roles' chains (sheet, plate, prop, keyframe) so the ``gemini/*``
links come first (``media_policy.role_chain``): a stable sort after the existing
filters, on a v2 story whose images are not manual, never adding or removing a
link -- so an episode's recorded ``links.image`` (DEC-204) stays a link of its
chain and keeps serving, and only new or unpinned episodes pick the preference
up. The estimate prices the first link, so a gemini-first story's cast is priced
on nano-banana-2-lite (``preset_estimate(story=)``); the preset without a story
is unchanged. The wizard and the story's profile card carry the select.

Stdlib + pytest (DEC-012); offline.
"""

from __future__ import annotations

import pathlib
import re
from types import SimpleNamespace

import pytest

from test_stories_api import api, _create  # noqa: F401 -- the route tests' app fixture, used as it is

from clipping.aistory import defaults, media_policy, schemas
from clipping.aistory import store as store_mod
from clipping.aistory.steps import assets, sticky_link
from clipping.providers import generation as gen
from clipping.providers import pricing
from clipping.providers.registry import describe

ROOT = pathlib.Path(__file__).resolve().parents[1]
STORY_SRC = ROOT / "web" / "dashboard" / "src" / "pages" / "story"

FAL, FAL_EDIT, LITE = "fal/seedream-4.5", "fal/seedream-4.5-edit", "gemini/nano-banana-2-lite"
PROFILES = ("quality", "native_speech", "native_speech_manual")
# (role, kind) -> the labels today's chain has (fal first), then the gemini-first order.
TODAY = {
    ("sheet", gen.IMAGE): [FAL, LITE], ("sheet", gen.IMAGE_EDIT): [FAL_EDIT, LITE],
    ("plate", gen.IMAGE): [FAL, LITE], ("plate", gen.IMAGE_EDIT): [FAL_EDIT, LITE],
    ("prop", gen.IMAGE): [FAL, LITE], ("prop", gen.IMAGE_EDIT): [FAL_EDIT, LITE],
    ("keyframe", gen.IMAGE_EDIT): [FAL_EDIT, LITE],
}


def _story(profile="quality", **extra):
    return {"story_id": "0123456789ab",
            "generation_profile": {**defaults.quality_generation_profile(), "budget_profile": profile, **extra}}


def _labels(role, kind, story):
    return [describe(link) for link in media_policy.role_chain(role, kind, {}, story)]


# ============================================================ the chains

@pytest.mark.parametrize("profile", PROFILES)
def test_gemini_first_reorders_every_role(profile):
    story = _story(profile, image_preference="gemini_first")
    assert media_policy.image_preference(story) == "gemini_first"
    for (role, kind), today in TODAY.items():
        assert _labels(role, kind, story) == [LITE] + [label for label in today if label != LITE], (role, kind)


def test_the_resort_is_stable_among_gemini_links_and_among_the_others(monkeypatch):
    mixed = [FAL, "gemini/nano-banana-2", FAL_EDIT, LITE]
    monkeypatch.setattr(media_policy, "_role_links", lambda role, story: list(mixed))
    plain = _labels("sheet", gen.IMAGE_EDIT, _story())
    preferred = _labels("sheet", gen.IMAGE_EDIT, _story(image_preference="gemini_first"))
    # (the text-to-image link is not a link of an edit chain.)
    assert plain == ["gemini/nano-banana-2", FAL_EDIT, LITE]
    assert preferred == ["gemini/nano-banana-2", LITE, FAL_EDIT]  # the same links, gemini ones first, in order


@pytest.mark.parametrize("profile", PROFILES)
def test_absent_preference_is_byte_identical(profile):
    plain = _story(profile)
    assert "image_preference" not in plain["generation_profile"]
    assert media_policy.image_preference(plain) == ""
    for (role, kind), today in TODAY.items():
        assert _labels(role, kind, plain) == today, (role, kind)
        # The same Link objects, built twice, in the budget profile's own order.
        assert media_policy.role_chain(role, kind, {}, plain) == media_policy.role_chain(role, kind, {}, _story(profile))
    # Re-sorting never changes WHICH links a role has: gemini first is a permutation of today's chain.
    preferred = _story(profile, image_preference="gemini_first")
    for (role, kind), today in TODAY.items():
        assert sorted(_labels(role, kind, preferred)) == sorted(today)


def test_preference_ignored_on_legacy_and_manual_images():
    legacy = {"generation_profile": {"tier": 1, "route": "auto", "consistency_mode": "references",
                                     "budget_profile": "free", "image_preference": "gemini_first"}}
    bare = {"generation_profile": {k: v for k, v in legacy["generation_profile"].items() if k != "image_preference"}}
    assert media_policy.image_preference(legacy) == ""
    for kind in (gen.IMAGE, gen.IMAGE_EDIT):
        assert media_policy.role_chain("sheet", kind, {}, legacy) == media_policy.role_chain("sheet", kind, {}, bare)

    manual = _story("native_speech_manual", images="manual", image_preference="gemini_first")
    assert media_policy.images_manual(manual) and media_policy.image_preference(manual) == ""
    for role, kind in TODAY:
        assert _labels(role, kind, manual) == [gen.MANUAL_LINK]
    for story in (None, {}, {"generation_profile": {}}):
        assert media_policy.image_preference(story) == ""


def test_pinned_episode_link_survives_preference_switch():
    """DEC-204: an episode's recorded ``links.image`` is picked out of the chain by its label, wherever the
    link stands, so putting Gemini first removes nothing a pin names and the pinned episode keeps serving."""
    kind = gen.IMAGE_EDIT
    plain, preferred = _story(), _story(image_preference="gemini_first")
    for story in (plain, preferred):
        ec = SimpleNamespace(story=story, consistency_mode="references")
        chain = assets.image_chain(ec, kind, {})
        labels = [describe(link) for link in chain]
        assert FAL_EDIT in labels and LITE in labels  # nothing is removed, whichever comes first
        # The step's pin (assets.py: the chain link whose label is the recorded one, alone):
        pinned = [link for link in chain if describe(link) == FAL_EDIT][:1]
        assert [describe(link) for link in pinned] == [FAL_EDIT]
        assert sticky_link.head_of(FAL_EDIT, labels) == FAL_EDIT
    assert [describe(link) for link in assets.image_chain(
        SimpleNamespace(story=preferred, consistency_mode="references"), kind, {})][0] == LITE  # a new episode
    assert [describe(link) for link in assets.image_chain(
        SimpleNamespace(story=plain, consistency_mode="references"), kind, {})][0] == FAL_EDIT


# ============================================================ the profile key

def test_the_profile_key_and_its_schema_and_model():
    pytest.importorskip("pydantic")
    from web.api.models import GenerationProfileModel

    assert defaults.IMAGE_PREFERENCES == ("gemini_first",)
    assert "image_preference" not in defaults.default_generation_profile()
    assert store_mod._PROFILE_CHOICES["image_preference"] == defaults.IMAGE_PREFERENCES
    assert "image_preference" in store_mod._PROFILE_CLEARABLE
    enum = schemas._GENERATION_PROFILE_SCHEMA["properties"]["image_preference"]["enum"]
    assert enum == ["gemini_first"]
    assert "image_preference" not in GenerationProfileModel().model_dump()
    assert GenerationProfileModel(image_preference="gemini_first").model_dump()["image_preference"] == "gemini_first"
    with pytest.raises(ValueError):
        store_mod._merge_generation_profile({"image_preference": "fal_first"})


def test_create_and_patch_accept_and_clear_image_preference(api):
    profile = {"tier": 3, "route": "api", "consistency_mode": "references", "budget_profile": "quality",
               "pipeline": "v2", "image_preference": "gemini_first"}
    story = _create(api, generation_profile=profile)
    assert story["generation_profile"]["image_preference"] == "gemini_first"
    story_id = story["story_id"]

    bare = _create(api, generation_profile={k: v for k, v in profile.items() if k != "image_preference"})
    assert "image_preference" not in bare["generation_profile"]
    patched = api.client.patch(f"/api/stories/{bare['story_id']}",
                               json={"generation_profile": {"image_preference": "gemini_first"}})
    assert patched.status_code == 200, patched.text
    assert patched.json()["generation_profile"]["image_preference"] == "gemini_first"

    cleared = api.client.patch(f"/api/stories/{story_id}", json={"generation_profile": {"image_preference": None}})
    assert cleared.status_code == 200, cleared.text
    assert "image_preference" not in cleared.json()["generation_profile"]
    assert "image_preference" not in api.client.get(f"/api/stories/{story_id}").json()["story"]["generation_profile"]

    refused = api.client.patch(f"/api/stories/{story_id}", json={"generation_profile": {"image_preference": "fal"}})
    assert refused.status_code in (400, 422), refused.text
    unknown = api.client.post("/api/stories", json={"language": "fr", "generation_profile": {
        **profile, "image_preference": "openai_first"}})
    assert unknown.status_code in (400, 422), unknown.text


# ============================================================ the estimate

def test_estimate_prices_gemini_first_on_lite():
    lite = float(pricing.price_for(media_policy.role_chain("sheet", gen.IMAGE, {}, _story("quality",
                                    image_preference="gemini_first"))[0]).usd)
    assert lite == 0.0336
    default = media_policy.preset_estimate()
    assert default["story"]["sheet_links"] == [FAL, FAL_EDIT]
    # Without a story, and for a story without the key, the preset is what it was.
    assert media_policy.preset_estimate(story=None) == default
    assert media_policy.preset_estimate(story={"generation_profile": {"pipeline": "v2"}}) == default

    story = {"generation_profile": {"pipeline": "v2", "image_preference": "gemini_first"}}
    est = media_policy.preset_estimate(story=story)
    assert est["story"]["sheet_links"] == [LITE, LITE]
    assert est["story"]["plate_link"] == LITE and est["story"]["prop_link"] == LITE
    assert est["episode"]["keyframe_link"] == LITE and est["episode"]["keyframe_usd"] == lite
    modes = est["story"]["sheet_usd_by_mode"]
    assert modes == {"three_sheet": round(3 * lite, 4), "two_view": round(lite, 4),
                     "two_view_expressions": round(2 * lite, 4)}
    assert est["story"]["plates_usd"] == round(media_policy.PRESET_STORY_PLACES * lite, 4)
    assert est["story"]["props_usd"] == round(media_policy.PRESET_STORY_PROPS * lite, 4)
    assert est["story"]["sheets_usd"] == round(media_policy.PRESET_STORY_CHARACTERS * modes["three_sheet"], 4)
    assert est["story_usd"] < default["story_usd"]
    assert est["keys"] == default["keys"]  # the preset still asks for FAL_KEY only
    assert "nano-banana-2-lite" in est["assumptions"]

    # Ignored on a legacy or manual-images story: the same numbers as the preset.
    manual = {"generation_profile": {"pipeline": "v2", "images": "manual", "image_preference": "gemini_first"}}
    assert media_policy.preset_estimate(story=manual) == default
    legacy = {"generation_profile": {"image_preference": "gemini_first"}}
    assert media_policy.preset_estimate(story=legacy) == default


# ============================================================ the dashboard

def _read(name):
    return (STORY_SRC / name).read_text(encoding="utf-8")


def test_the_wizard_and_the_profile_card_carry_the_image_provider_select():
    wizard, card = _read("NewStoryWizard.jsx"), _read("GenerationProfileCard.jsx")
    for src in (wizard, card):
        assert "Image provider" in src and "fal first (default)" in src and "Gemini first" in src
        assert "gemini_first" in src and "image_preference" in src
        assert "gemini_paid_api_key_set" in src and "GEMINI_PAID_API_KEY" in src  # the Settings payload's flag
        assert "fetchSettings" in src
    # Wizard: the key is sent only when chosen (v2), and the select is disabled without the key.
    assert re.search(r"image_preference: imagePreference", wizard)
    assert re.search(r'id="new-story-image-preference"[^>]*\n?[^>]*disabled=\{!geminiKeySet && imagePreference === \'\'\}',
                     wizard)
    # Card: null clears it (a PATCH), disabled without the key while it is unset.
    assert "save({ image_preference: value || null })" in card
    assert re.search(r"id=\"story-profile-image-preference\"[\s\S]{0,200}disabled=\{saving \|\| \(!geminiKeySet && "
                     r"imagePreference === ''\)\}", card)
