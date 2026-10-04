"""Native speech: what an episode costs and the caps (plan 22, stage 4).

The estimate prices the speaking seconds at the speech link's price, the
silent seconds at the silent link's and the retake budget as the
contingency; before a story is ready, the agent run prices episode 1 from
the profile's own figure (no more the profile's ``cap_usd``); a plan over
the per-episode cap is refused whole, before anything is bought, with the
numbers and the two ways out. The shipped caps are unchanged (DEC-223).

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_native_speech_plan as nsp
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_clip_estimate import timings_path  # noqa: F401 - the measured clip timings under tmp_path

NOW = tas.NOW
KEYS = {"GEMINI_PAID_API_KEY": "test-gemini-paid-key", "FAL_KEY": "test-fal-key", "GROQ_API_KEY": "test-groq"}


def test_the_preset_prices_each_speech_model_from_the_table():
    """Fail-first. 40 speaking seconds on the model's link, 12 silent on
    lite, a keyframe a shot and the $1.00 retake budget: lite, fast and
    premium differ by their speech price alone."""
    from clipping.aistory import media_policy

    estimate = media_policy.native_speech_estimate(KEYS)
    keyframes = estimate["keyframes_usd"]
    assert estimate["episode"]["speech_seconds"] == 40 and estimate["episode"]["silent_seconds"] == 12
    for model, price in (("lite", 0.05), ("fast", 0.10), ("premium", 0.40)):
        assert estimate["by_model"][model] == pytest.approx(40 * price + 12 * 0.05 + keyframes + 1.0)
    assert (estimate["speech_model"], estimate["speech_link"], estimate["silent_link"]) == (
        "fast", "gemini/veo-3.1-fast", "gemini/veo-3.1-lite")
    assert estimate["missing_keys"] == [] and estimate["stt_missing_keys"] == []
    assert estimate["summary"].startswith(f"≈ ${estimate['episode_usd']:.2f} an episode (9 speaking clips on "
                                          "gemini/veo-3.1-fast, 3 silent on gemini/veo-3.1-lite")


def test_the_new_story_offer_names_the_keys_the_native_speech_profile_still_needs():
    from clipping.aistory import media_policy

    offer = media_policy.new_story_offer({"FAL_KEY": "test-fal-key"})
    speech = offer["native_speech"]
    assert speech["missing_keys"] == ["GEMINI_PAID_API_KEY"]
    assert speech["stt_missing_keys"] == ["GROQ_API_KEY", "MISTRAL_API_KEY"]
    assert "add GEMINI_PAID_API_KEY and GROQ_API_KEY or MISTRAL_API_KEY (the speech check) in Settings" in speech[
        "summary"]


def test_a_premium_episode_over_the_per_episode_cap_is_refused_before_anything_is_bought(store):
    """The human's caps (a $2 episode here): the plan is shown, priced and
    refused whole with the numbers -- never trimmed to the clips that fit."""
    story_id = nsp.planned_story(store, speech_model="premium")
    settings = tas._settings(**KEYS, ALLOW_PAID="1", PER_EPISODE_CAP_USD="2", DAILY_CAP_USD="4",
                             PER_STORY_CAP_USD="10")
    units = tce._units(store, story_id, settings)
    video = units["video"]
    assert video["speech"]["speech_link"] == "gemini/veo-3.1"
    total = video["est_usd"]
    assert video["over_cap"] == (f"estimated ${total:.2f} over the per-episode cap $2.00; raise PER_EPISODE_CAP_USD "
                                 "or use your own clips")
    # the fast track's check before anything is bought (the hold for the keyframes lifted) refuses the whole
    from clipping.aistory.steps import fast_track

    whole = fast_track.whole_episode_units(tas._ec(store, story_id), units, env=settings)
    speech = video["speech"]
    assert whole["ready"] is False and f"of its $2.00 cap" in whole["over_cap"]
    assert (f"{video['count']} clips ({speech['speech_seconds']} s on gemini/veo-3.1 and {speech['silent_seconds']} s "
            "on gemini/veo-3.1-lite") in whole["over_cap"]


def test_the_agent_estimate_prices_episode_one_from_the_profile_not_its_cap(store):
    """``_agent_episode_usd`` before the story is ready: the native-speech
    figure with the keyframe auto-fix's ceiling, never the profile's
    ``cap_usd`` ($10)."""
    from clipping.aistory import media_policy, workflow

    story = store.get(nsp.native_story(store))
    usd = workflow._agent_episode_usd(story, KEYS)
    expected = media_policy.native_speech_estimate(KEYS, story=story)["episode_usd"] + 0.40
    assert usd == pytest.approx(expected) and usd != 10.0


def test_guard_the_shipped_caps_are_unchanged():
    from clipping.providers import budget

    assert (budget.PER_EPISODE_CAP_USD, budget.DAILY_CAP_USD, budget.PER_STORY_CAP_USD) == (4.0, 12.0, 40.0)
