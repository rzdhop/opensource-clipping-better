"""Tier-3 ambience (AI Story phase 7 follow-up, stage E; the human's choice,
2026-10-02): every clip brings its own ambience and sound effects -- the
video model's sound is used for AMBIENCE + SFX ONLY, never for dialogue
(each character keeps its pinned TTS voice in every shot) -- on Veo 3.1
lite (``gemini/veo-3.1-lite``, billed on ``GEMINI_PAID_API_KEY``).

This file holds the parts that need no ffmpeg:

- the budget profile's values (``tier3_native_audio: ambience``,
  ``video_link_policy: first_with_audio``) and their validation;
- which story is in ambience mode (``media_policy.ambience``: v2, tier 3,
  its profile says ``ambience``) and the clip link it buys on: the first
  keyed link whose clips always carry sound, else the first keyed link,
  silent, said in the estimate -- never a silent switch.

The new behaviour is reached inside the tests, so on the parent commit each
test fails on its own. Offline and hermetic (the assets step's fixtures);
stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import json

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_clip_estimate import timings_path  # noqa: F401 - the measured clip timings under tmp_path

NOW = tas.NOW
SEEDANCE = "fal/seedance-1-pro-fast"
LTX = "fal/ltx-2.3-fast"
KLING = "fal/kling-2.5-turbo-std"
VEO = "gemini/veo-3.1-lite"
# The shipped default VIDEO_CHAIN's hosted links, in its order.
CHAIN = ",".join((SEEDANCE, LTX, KLING, VEO))
GEMINI = {"GEMINI_PAID_API_KEY": "test-gemini-paid-key"}


def _story(store, tmp_path, *, tier=3, v2=True, budget_profile="quality"):
    """The assets step's story (episode 1 written, planned, approved) at
    *tier* on *budget_profile*, on the v2 pipeline when *v2*."""
    story_id = tas._episode(store, tmp_path)
    tce._tier(store, story_id, tier=tier, budget_profile=budget_profile)
    if v2:
        store.update(story_id, lambda doc: doc["generation_profile"].update(pipeline="v2"), now=NOW)
    return story_id


def _video(store, story_id, **keys):
    settings = tas._settings(VIDEO_CHAIN=CHAIN, ALLOW_PAID="1", PER_EPISODE_CAP_USD="40", **keys)
    return tce._units(store, story_id, settings)["video"]


def _profiles_file(tmp_path, **quality):
    """A copy of the shipped budget profiles with *quality*'s keys over the
    quality profile's."""
    from clipping.providers import budget as budget_mod

    data = budget_mod.load_profiles()
    data["profiles"]["quality"].update(quality)
    path = tmp_path / "budget_profiles.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# =================================================== the profile's values

def test_the_quality_profile_buys_clips_with_sound_and_keeps_their_ambience():
    """The shipped quality profile: ``tier3_native_audio: ambience`` (the
    clip's sound under the dialogue, never in place of it) and
    ``video_link_policy: first_with_audio``; the one-dollar profile keeps
    the cheapest link."""
    from clipping.aistory.steps import clips
    from clipping.providers import budget as budget_mod

    quality = budget_mod.profile_settings("quality")
    assert (quality["tier3_native_audio"], quality["video_link_policy"]) == ("ambience", "first_with_audio")
    assert budget_mod.profile_settings("one_dollar")["video_link_policy"] == "cheapest_available"
    assert budget_mod.TIER3_AUDIO_MODES == ("opt_in", "ambience")
    assert budget_mod.VIDEO_LINK_POLICIES == clips.LINK_POLICIES == (
        "cheapest_available", "first_in_chain", "first_with_audio")


@pytest.mark.parametrize("key,value", [("tier3_native_audio", "dialogue"), ("tier3_native_audio", True),
                                       ("video_link_policy", "loudest"), ("video_link_policy", None)])
def test_a_profile_with_an_unknown_audio_mode_or_link_policy_is_refused(tmp_path, key, value):
    from clipping.providers import budget as budget_mod

    path = _profiles_file(tmp_path, **{key: value})
    with pytest.raises(ValueError) as caught:
        budget_mod.load_profiles(str(path))
    assert key in str(caught.value) and "quality" in str(caught.value)


def test_a_profile_may_name_each_known_audio_mode_and_link_policy(tmp_path):
    from clipping.providers import budget as budget_mod

    for mode in budget_mod.TIER3_AUDIO_MODES:
        for policy in budget_mod.VIDEO_LINK_POLICIES:
            path = _profiles_file(tmp_path, tier3_native_audio=mode, video_link_policy=policy)
            assert budget_mod.load_profiles(str(path))["profiles"]["quality"]["video_link_policy"] == policy


# ====================================================== ambience mode

def test_ambience_mode_is_a_v2_tier_3_story_whose_profile_says_ambience(monkeypatch, tmp_path):
    """Only a v2 story at tier 3 on a profile whose ``tier3_native_audio``
    is ``ambience``: tier 1 and 2 never keep a clip's sound, a legacy story
    keeps today's opt-in, and a profile at ``opt_in`` keeps today's
    behaviour (the clip's sound in place of a shot's lines, per shot)."""
    from clipping.aistory import defaults, media_policy
    from clipping.providers import budget as budget_mod

    def story(**profile):
        base = {"tier": 3, "route": "api", "consistency_mode": "references", "budget_profile": "quality",
                "pipeline": defaults.PIPELINE_V2}
        base.update(profile)
        return {"generation_profile": {key: value for key, value in base.items() if value is not None}}

    assert media_policy.ambience(story()) is True
    assert media_policy.ambience(story(tier=2)) is False
    assert media_policy.ambience(story(tier=1)) is False
    assert media_policy.ambience(story(pipeline=None)) is False
    assert media_policy.ambience(story(budget_profile="one_dollar")) is False
    assert media_policy.ambience(None) is False

    monkeypatch.setattr(budget_mod, "PROFILES_PATH", str(_profiles_file(tmp_path, tier3_native_audio="opt_in")))
    assert media_policy.ambience(story()) is False


def test_the_new_story_preset_is_tier_3_so_its_clips_keep_their_ambience():
    """The quality preset a new story gets with the keys (phase 7, DEC-221)
    is tier 3 now -- "animated + model sound" -- so its clips bring their
    ambience (the human's choice, 2026-10-02); still v2, still every shot a
    clip (``media_policy.fully_animated``)."""
    from clipping.aistory import defaults, media_policy

    profile = defaults.quality_generation_profile()
    assert profile == {"tier": 3, "route": "api", "consistency_mode": "references", "budget_profile": "quality",
                       "pipeline": "v2"}
    story = {"generation_profile": profile}
    assert media_policy.ambience(story) and media_policy.fully_animated(story)


# ======================================================== the clip link

def _row(link, status="keyed", price=0.05):
    return {"link": link, "status": status, "reason": "keyed" if status == "keyed" else "no key",
            "price_per_second": price if status == "keyed" else None}


def test_first_with_audio_picks_the_first_keyed_link_whose_clips_always_carry_sound():
    """``pick_hosted``: with sound wanted, the first keyed link whose clips
    always carry sound (Veo), past cheaper silent ones and past LTX (its
    sound only when asked); none keyed: the first keyed link, as
    ``first_in_chain`` -- the caller says the clips are silent. Without
    sound wanted (tier 2, a legacy story) it is ``first_in_chain``."""
    from clipping.aistory.steps import clips

    rows = [_row(SEEDANCE, price=0.022), _row(LTX, price=0.06), _row(KLING, price=0.042), _row(VEO)]
    assert clips.pick_hosted(rows, clips.FIRST_WITH_AUDIO, want_sound=True)["link"] == VEO
    assert clips.pick_hosted(rows, clips.FIRST_WITH_AUDIO)["link"] == SEEDANCE
    assert clips.pick_hosted(rows, clips.FIRST)["link"] == SEEDANCE
    assert clips.pick_hosted(rows, clips.CHEAPEST, want_sound=True)["link"] == SEEDANCE
    unkeyed = rows[:3] + [_row(VEO, status="skipped")]
    assert clips.pick_hosted(unkeyed, clips.FIRST_WITH_AUDIO, want_sound=True)["link"] == SEEDANCE
    assert clips.pick_hosted([_row(VEO, status="skipped")], clips.FIRST_WITH_AUDIO, want_sound=True) is None


def test_an_ambience_story_buys_its_clips_on_veo_when_its_key_is_set(store, tmp_path):
    """Fail-first. A v2 tier-3 quality story, FAL_KEY and GEMINI_PAID_API_KEY
    set, the shipped chain's order (seedance first): every shot's clip is
    planned on Veo at its price, and the estimate says the clips bring
    their own ambience and sound effects under the dialogue."""
    from clipping.providers import pricing

    story_id = _story(store, tmp_path)
    video = _video(store, story_id, **tas.FAL, **GEMINI)

    assert video["link"] == VEO and video["price_per_second"] == pricing.PRICES[VEO].usd
    assert video["ambience"] == {"sound": True, "note": None}
    assert "own ambience and sound effects" in video["message"] and "No ambience" not in video["message"]
    assert "rendered as at tier 2" not in video["message"]


def test_without_the_veo_key_the_clips_fall_back_to_the_first_keyed_link_and_say_there_is_no_ambience(
        store, tmp_path):
    """No GEMINI_PAID_API_KEY: the first keyed link (seedance, no sound),
    and the estimate says so -- no ambience, and the key that brings it --
    never a silent switch. The plan stays ready: the clips are still made."""
    story_id = _story(store, tmp_path)
    video = _video(store, story_id, **tas.FAL)

    assert video["link"] == SEEDANCE and video["ready"] is True
    assert video["ambience"]["sound"] is False
    note = video["ambience"]["note"]
    assert note.startswith("No ambience:") and "GEMINI_PAID_API_KEY" in note and VEO in note
    assert note in video["message"]
    # Not the opt-in's sentence (A-108): no shot's lines were ever to be replaced.
    assert "rendered as at tier 2" not in video["message"]


def test_an_episode_whose_clips_are_on_a_silent_link_keeps_it_and_says_why_there_is_no_ambience(store, tmp_path):
    """The sticky link (DEC-204, A-087) still holds per episode: an episode
    whose clips are recorded on seedance stays there with the Veo key set,
    and the estimate says the episode has no ambience and why."""
    from clipping.aistory import workflow

    story_id = _story(store, tmp_path)
    settings = tas._settings(VIDEO_CHAIN=CHAIN, ALLOW_PAID="1", **tas.FAL, **GEMINI)
    workflow.patch_assets(store, story_id, 1, {"links": {"video": SEEDANCE}}, now=tce.LATER, env=settings)

    video = _video(store, story_id, **tas.FAL, **GEMINI)
    assert (video["link"], video["source"]) == (SEEDANCE, "record")
    assert video["ambience"]["sound"] is False
    assert "keeps its clips on one link" in video["ambience"]["note"]


@pytest.mark.parametrize("tier,v2", [(2, True), (3, False)])
def test_guard_outside_ambience_mode_the_quality_profile_still_buys_the_first_link_in_the_chain(
        store, tmp_path, tier, v2):
    """Tier 2 (the clip's sound is discarded) and a legacy tier-3 story (the
    opt-in): ``first_with_audio`` is ``first_in_chain``, and the units carry
    no ``ambience`` key -- byte for byte the shape they had."""
    story_id = _story(store, tmp_path, tier=tier, v2=v2)
    video = _video(store, story_id, **tas.FAL, **GEMINI)
    assert video["link"] == SEEDANCE and "ambience" not in video
    if tier == 3:
        assert "rendered as at tier 2" in video["message"]  # A-108's opt-in sentence, as it was
