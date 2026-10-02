"""AI Story phase 7 stage 7 (A18): what the Quality (billed APIs) preset
costs, from the price table, the quality budget profile and the v2 episode
template -- never a hard-coded dollar.

``media_policy.preset_estimate`` feeds the weak-host advice
(``hardware.recommendations_for``) and the new-story form
(``media_policy.new_story_offer``'s ``estimate``), so the three always say
the same numbers, and the numbers move when ``pricing.py`` moves. Stdlib +
pytest only (DEC-012): nothing is called.
"""

from __future__ import annotations

import pytest

from clipping.aistory import defaults, hardware, media_policy, templates, video_plan
from clipping.providers import budget as budget_mod
from clipping.providers import gating, pricing

SEEDANCE = "fal/seedance-1-pro-fast"


def _profile():
    return budget_mod.profile_settings(defaults.quality_generation_profile()["budget_profile"])


def test_the_episode_is_the_v2_templates_shots_on_the_profiles_links_at_the_tables_prices():
    estimate = media_policy.preset_estimate()
    template = templates.load_episode_template(defaults.EPISODE_TEMPLATE_ID_V2)
    roles = _profile()["roles"]
    episode = estimate["episode"]

    # Episode 1: the hook, the template's default body scenes and the
    # cliffhanger, one shot each (T1 v2), over the template's target length.
    assert episode["shots"] == 1 + template["default_body_count"] + 1
    assert episode["seconds"] == template["target_s"]
    # The quality profile buys the first hosted video link (seedance) at its
    # size (720p), each shot a clip of whole seconds, rounded up as the
    # planner rounds it (video_plan.requested_seconds, DEC-208).
    assert (episode["video_link"], episode["resolution"]) == (SEEDANCE, _profile()["video_resolution"])
    per_clip = video_plan.requested_seconds(SEEDANCE, template["target_s"] / episode["shots"])
    assert episode["billed_seconds"] == episode["shots"] * per_clip > episode["seconds"]
    assert episode["keyframe_link"] == roles["keyframe"][0]
    expected = (episode["billed_seconds"] * pricing.PRICES[SEEDANCE].usd
                + episode["shots"] * pricing.PRICES[roles["keyframe"][0]].usd)
    assert estimate["episode_usd"] == pytest.approx(expected)

    # Once per story: each character's portrait on the sheet role's
    # text-to-image link and its two sheets on the edit link, one plate a
    # place and one image a prop (DEC-235: fal Seedream 4.5).
    story = estimate["story"]
    assert story["sheet_links"] == roles["sheet"] and story["plate_link"] == roles["plate"][0]
    assert story["prop_link"] == roles["prop"][0]
    expected = (story["characters"] * (pricing.PRICES[roles["sheet"][0]].usd + 2 * pricing.PRICES[roles["sheet"][1]].usd)
                + story["places"] * pricing.PRICES[roles["plate"][0]].usd
                + story["props"] * pricing.PRICES[roles["prop"][0]].usd)
    assert estimate["story_usd"] == pytest.approx(expected)
    assert story["images"] == 3 * story["characters"] + story["places"] + story["props"]

    # The assumptions are stated, in words, with the table's date.
    words = estimate["assumptions"]
    for part in (f"{episode['shots']} shots", f"{episode['seconds']} s", f"{episode['billed_seconds']} s billed",
                 SEEDANCE, roles["keyframe"][0], f"{story['characters']} characters", pricing.PRICES_AS_OF):
        assert part in words, (part, words)
    assert estimate["keys"] == list(media_policy.QUALITY_KEYS)


def test_the_numbers_move_when_the_price_table_moves(monkeypatch):
    before = media_policy.preset_estimate()
    video = pricing.PRICES[SEEDANCE]
    sheet = pricing.PRICES["fal/seedream-4.5"]
    monkeypatch.setitem(pricing.PRICES, SEEDANCE, video._replace(usd=video.usd * 2))
    monkeypatch.setitem(pricing.PRICES, "fal/seedream-4.5", sheet._replace(usd=sheet.usd * 3))

    after = media_policy.preset_estimate()

    billed = before["episode"]["billed_seconds"]
    assert after["episode_usd"] == pytest.approx(before["episode_usd"] + billed * video.usd)
    story = before["story"]
    # The text-to-image link draws each portrait, plate and prop.
    drawn = story["characters"] + story["places"] + story["props"]
    assert after["story_usd"] == pytest.approx(before["story_usd"] + drawn * 2 * sheet.usd)
    # The weak-host advice says the new numbers, never the old ones.
    text = hardware.recommendations_for("cpu_only")[0]["install_hint"]
    assert f"${after['episode_usd']:.2f} an episode" in text and f"${before['episode_usd']:.2f}" not in text
    assert f"${after['story_usd']:.2f} once per story" in text


def test_the_video_link_follows_the_video_chain_as_the_profile_picks_it():
    """``video_link_policy: first_in_chain``: the first hosted link of
    VIDEO_CHAIN that sells clips by the second (keys are not asked: the
    advice is what to add)."""
    estimate = media_policy.preset_estimate({"VIDEO_CHAIN": "local/comfyui,fal/kling-2.5-turbo-std," + SEEDANCE})
    episode = estimate["episode"]
    assert episode["video_link"] == "fal/kling-2.5-turbo-std"
    per_clip = video_plan.requested_seconds("fal/kling-2.5-turbo-std", episode["seconds"] / episode["shots"])
    assert episode["billed_seconds"] == episode["shots"] * per_clip
    assert estimate["episode_usd"] == pytest.approx(
        episode["billed_seconds"] * pricing.PRICES["fal/kling-2.5-turbo-std"].usd
        + episode["shots"] * pricing.PRICES[episode["keyframe_link"]].usd)
    # A chain the profile cannot buy a clip on falls back to the shipped one.
    assert media_policy.preset_estimate({"VIDEO_CHAIN": "local/comfyui"})["episode"]["video_link"] == SEEDANCE
    assert media_policy.preset_estimate({"VIDEO_CHAIN": "not a chain"})["episode"]["video_link"] == SEEDANCE


def test_the_new_story_offer_carries_the_same_estimate_with_or_without_the_key():
    without = media_policy.new_story_offer({"FAL_KEY": ""})
    assert without["quality"] is False and without["missing_keys"] == ["FAL_KEY"]
    # The preset's price is shown whether or not it is the default yet.
    assert without["estimate"] == media_policy.preset_estimate(gating.merged_env({"FAL_KEY": ""}))
    keyed = media_policy.new_story_offer({"FAL_KEY": "test-fal-key"})
    assert keyed["quality"] is True
    assert keyed["estimate"] == media_policy.preset_estimate(gating.merged_env({"FAL_KEY": "test-fal-key"}))
    assert "test-fal-key" not in repr(keyed["estimate"])
