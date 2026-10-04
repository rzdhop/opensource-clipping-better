"""The manual link: the human as a provider (plan 22, stage 5).

A story on the ``native_speech_manual`` budget profile names
``manual/upload`` for every speaking and silent clip: the app writes a
crafted shot brief per platform (Google Flow, Higgsfield), the human makes
the clips on their own subscription and uploads them; the native take, the
timing and the render run as for any clip. The link never sends a request
and never books a cent (RC-N4); the estimate shows the clips at $0 with
what they cost in the platform's credits; the assets step lists the clips
still to upload and ends ``awaiting_uploads``.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import json

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_native_speech_plan as nsp
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_clip_estimate import timings_path  # noqa: F401 - the measured clip timings under tmp_path

NOW = tas.NOW
MANUAL = "manual/upload"
PROFILE = "native_speech_manual"


def manual_story(store, **kwargs):
    """A planned native-speech story on the manual profile: its storyboard
    built (fast) and approved, ready for the assets step."""
    return nsp.planned_story(store, profile=PROFILE, **kwargs)


# ================================================================ the provider

class _Counting:
    """A transport, a budget check and a limiter that count every call."""

    def __init__(self):
        self.calls = []

    def transport(self, *args, **kwargs):
        self.calls.append(("transport", args))
        raise AssertionError("a manual link never sends a request")

    def check(self, estimate, link):
        self.calls.append(("budget", estimate))

    def acquire(self, provider):
        self.calls.append(("limiter", provider))


def test_the_manual_link_never_sends_and_never_books(tmp_path):
    """Fail-first (RC-N4). ``manual/upload`` is free, keyless, $0; the
    runner asks it nothing: ``AwaitingUpload`` ends the chain before the
    route, the keys, the budget, the limiter or the journal -- and a link
    after it never stands in for the human."""
    from clipping.providers import adapters, gencache, pricing
    from clipping.providers import generation as gen

    adapters.load_all()
    link = gen.parse_generation_chain(gen.VIDEO, [MANUAL])[0]
    assert gen.is_paid(link) is False and gen.missing_keys(link, {}) == [] and gen.is_manual(link)
    assert pricing.estimate(link, 8).est_usd == 0.0
    for kind in (gen.IMAGE, gen.IMAGE_EDIT, gen.VIDEO):
        assert gen.adapter_for(kind, "manual").estimate(link, gen.GenRequest(kind=kind)) == 0.0
    chain = gen.parse_generation_chain(gen.VIDEO, [MANUAL, "fal/seedance-1-pro-fast"])
    counting = _Counting()
    booked = []
    cache = gencache.GenCache(str(tmp_path / "gen"), book=lambda *a, **k: booked.append(a))
    request = gen.GenRequest(kind=gen.VIDEO, prompt="x", duration_s=8, seed=1, references=("k.png",),
                             out_dir=str(tmp_path), extra={"target": "sh03"})
    with pytest.raises(gen.AwaitingUpload) as caught:
        gen.run_generation_chain(gen.VIDEO, chain, request, env={"FAL_KEY": "k"}, allow_paid=True,
                                 on_log=lambda _line: None, budget_check=counting.check, limiter=counting,
                                 transport=counting.transport, cache=cache)
    assert caught.value.target == "sh03" and "nothing was sent or booked" in str(caught.value)
    assert counting.calls == [] and booked == []
    assert not (tmp_path / "gen").exists() or not any((tmp_path / "gen").rglob("*.json"))


def test_the_manual_profile_names_your_own_clips_and_the_image_switch():
    from clipping.aistory import defaults, media_policy
    from clipping.providers import budget

    profile = budget.profile_settings(PROFILE)
    assert profile["label"] == "Native speech — your own clips" and profile["cap_usd"] == 2.0
    assert set(profile["speech_links"].values()) == {MANUAL} and profile["silent_link"] == MANUAL
    assert (profile["speech_model"], profile["tier3_native_audio"], profile["lipsync"]) == ("fast", "speech", "none")
    assert profile["speech_retake"] == {"max_per_shot": 0, "cap_usd": 0}
    story = {"generation_profile": defaults.manual_speech_generation_profile()}
    assert media_policy.native_speech(story) and media_policy.clips_manual(story)
    assert media_policy.images_manual(story) is False
    assert media_policy.speech_retake(story) == {"max_per_shot": 0, "cap_usd": 0.0}
    # The per-story switch: every image role on the human's upload too.
    manual_images = {"generation_profile": dict(story["generation_profile"], images="manual")}
    assert media_policy.images_manual(manual_images)
    for role in media_policy.ROLES:
        for kind in ("image", "image_edit"):
            assert [f"{link.provider}/{link.model}" for link in
                    media_policy.role_chain(role, kind, {}, manual_images)] == [MANUAL]


def test_the_estimate_shows_your_own_clips_at_zero_with_their_flow_credits():
    """The preset's figure: twelve clips the human makes ($0 here, ≈ 240
    Flow credits on AI Pro), the keyframes the app draws."""
    from clipping.aistory import media_policy

    offer = media_policy.new_story_offer({"FAL_KEY": "k"})
    assert offer["manual"] is True and offer["profile"]["budget_profile"] == PROFILE
    manual = offer["native_speech_manual"]
    assert manual["manual"] is True and manual["clips_usd"] == 0.0 and manual["retake_usd"] == 0.0
    assert manual["episode_usd"] == pytest.approx(manual["keyframes_usd"])
    assert "your own clips (12 shots, ≈ 240 Flow credits on AI Pro" in manual["summary"]
    assert manual["missing_keys"] == []
