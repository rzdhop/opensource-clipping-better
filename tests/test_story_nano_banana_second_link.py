"""Plan 23 stage A8 (DEC-280, amends DEC-235): ``gemini/nano-banana-2-lite`` is the
second link of the quality sheet, plate and prop roles.

fal Seedream 4.5 stays first (``FAL_KEY`` is the only key the preset asks for, and
``preset_estimate`` prices the first links, so its numbers do not move). Lite
($0.0336) answers when fal fails or is refused by a cap a $0.04 call would cross,
is skipped as "no key" without ``GEMINI_PAID_API_KEY``, is sent at most four
reference images by the cast (``refimages.MAX_REFERENCES``) and never more than
fourteen by its adapter (``images.GEMINI_MAX_REFERENCES``), and honours no seed
(``refimages`` records the seed and says so). Within one cast or places job the
provider that answered first is tried first for the rest (the ``sticky`` dict of
``steps.entities.Tools``).

Stdlib + pytest (DEC-012); offline and hermetic (the refimages tests' fixtures).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import test_story_refimages as trf
from test_story_refimages import hermetic, store  # noqa: F401 - the refimages fixtures (hermetic is autouse)

from clipping.aistory import defaults, media_policy, refimages
from clipping.aistory.steps import entities
from clipping.cancel import CancelToken
from clipping.providers import budget as budget_mod
from clipping.providers import generation as gen
from clipping.providers import images, pricing
from clipping.providers import gating
from clipping.providers.registry import Link, describe

LITE = "gemini/nano-banana-2-lite"
ENV = {**trf.FAL, **trf.GEMINI, **trf.PAID_ON}


class FailingFal(trf.FakeImage):
    """fal answering a 500 every time: a request recorded, then an error."""

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.requests.append(request)
        raise RuntimeError("fal answered HTTP 500")

    def estimate(self, link, request):
        return pricing.price_for(link).usd


class PricedImage(trf.FakeImage):
    def estimate(self, link, request):
        return pricing.price_for(link).usd


def _adapters(fal, gemini):
    return {("image", "fal"): fal, ("image_edit", "fal"): fal,
            ("image", "gemini"): gemini, ("image_edit", "gemini"): gemini}


def _v2(store, profile="quality"):
    story_id = trf._story(store)
    store.update(story_id, lambda doc: doc["generation_profile"].update(
        pipeline=defaults.PIPELINE_V2, budget_profile=profile), now=trf.NOW)
    return story_id


def _v2_story(profile="quality"):
    return {"story_id": "0123456789ab", "generation_profile": {**defaults.quality_generation_profile(),
                                                                "budget_profile": profile}}


def _labels(chain):
    return [describe(link) for link in chain]


def _place(store, story_id, env, adapters, **kwargs):
    log = trf.Log()
    ref = refimages.place_image(store, story_id, trf.PLACE, "day", env=env, on_log=log, cancel=CancelToken(),
                                adapters=adapters, sleep_fn=trf._no_sleep, time_fn=lambda: 100.0, **kwargs)
    return ref, log


# ---------------------------------------------------------------- the roles

@pytest.mark.parametrize("profile", ["quality", "native_speech", "native_speech_manual"])
def test_nano_banana_lite_is_second_for_both_kinds(profile):
    story = _v2_story(profile)
    roles = budget_mod.profile_settings(profile)["roles"]
    for role in ("sheet", "plate", "prop"):
        assert roles[role] == ["fal/seedream-4.5", "fal/seedream-4.5-edit", LITE]
        assert _labels(media_policy.role_chain(role, gen.IMAGE, {}, story)) == ["fal/seedream-4.5", LITE]
        assert _labels(media_policy.role_chain(role, gen.IMAGE_EDIT, {}, story)) == ["fal/seedream-4.5-edit", LITE]
    # The keyframe role is the one DEC-235 left it.
    assert roles["keyframe"] == ["fal/seedream-4.5-edit", LITE]
    assert _labels(media_policy.role_chain("keyframe", gen.IMAGE_EDIT, {}, story)) == ["fal/seedream-4.5-edit", LITE]
    # Lite is in none of the filters: it serves a text-to-image and an edit request alike.
    assert LITE not in media_policy.LOW_QUALITY_LINKS | media_policy.EDIT_ONLY_LINKS | media_policy.TEXT_ONLY_LINKS


def test_preset_estimate_unchanged():
    """fal is still first, so the numbers and the keys the preset asks for are what they were."""
    estimate = media_policy.preset_estimate()
    # Re-pinned on purpose (plan 28 stage A2, DEC-305): serial_60s_v2's episode 1 is 6 scenes now, so 6 seedance
    # clips of 62 / 6 s rounded up to 11 s (66 s x $0.022) and 6 keyframes ($0.04): $1.692 (8 x 8 s before, $1.728).
    assert estimate["episode_usd"] == pytest.approx(1.692)
    assert estimate["story_usd"] == pytest.approx(0.56)
    assert estimate["keys"] == ["FAL_KEY"] == list(media_policy.QUALITY_KEYS)
    story = estimate["story"]
    assert story["sheet_links"] == ["fal/seedream-4.5", "fal/seedream-4.5-edit"]
    assert (story["plate_link"], story["prop_link"]) == ("fal/seedream-4.5", "fal/seedream-4.5")


# ------------------------------------------------------------ the fall-through

def test_sheet_falls_to_gemini_when_fal_fails(store):
    story_id = _v2(store)
    fal, gemini = FailingFal(), PricedImage(meta={"seed_honoured": False})
    ref, log = trf._character_image(store, story_id, "portrait", ENV, adapters=_adapters(fal, gemini))
    assert len(fal.requests) == 1 and len(gemini.requests) == 1
    assert (ref["source"], ref["consistency"]) == (LITE, "base")
    assert any("fal/seedream-4.5" in line and "fal answered HTTP 500" in line for line in log)
    # No seed is honoured: the seed is still recorded as the portrait's ref_seed, and said so.
    seed = gemini.requests[0].seed
    assert ref["seed"] == seed
    assert store.read_entity(story_id, "characters", trf.CHAR)["ref_seed"] == seed
    assert any(f"{LITE} does not honour seeds; seed {seed} is recorded, not reproducible." in line for line in log)

    # The turnaround, an edit of that portrait, reuses its seed on fal's edit link, which answers.
    fal_ok = PricedImage()
    ref2, _log = trf._character_image(store, story_id, "turnaround", ENV, adapters=_adapters(fal_ok, gemini))
    assert ref2["source"] == "fal/seedream-4.5-edit" and fal_ok.requests[0].seed == seed
    assert len(gemini.requests) == 1


def test_sheet_falls_to_gemini_when_a_cap_refuses_fal_but_lite_fits(store):
    """fal at $0.04 would cross a $0.035 day; lite at $0.0336 still fits: one image from each side."""
    story_id = _v2(store)
    fal, gemini = PricedImage(), PricedImage(meta={"seed_honoured": False})
    env = {**ENV, "DAILY_CAP_USD": "0.035"}
    ref, log = _place(store, story_id, env, _adapters(fal, gemini))
    assert ref["source"] == LITE and fal.requests == [] and len(gemini.requests) == 1
    assert any("fal/seedream-4.5" in line and "daily cap" in line for line in log)


def test_gemini_skipped_without_paid_key(store):
    """The free GOOGLE_API_KEY never serves a nano-banana link (DEC-222): with fal failing, no link is left."""
    story_id = _v2(store)
    fal, gemini = FailingFal(), PricedImage()
    env = {**trf.FAL, "GOOGLE_API_KEY": "free-key", **trf.PAID_ON}
    with pytest.raises(refimages.RefImageError) as excinfo:
        _place(store, story_id, env, _adapters(fal, gemini))
    assert gemini.requests == [] and len(fal.requests) == 1
    assert f"{LITE}: no API key (GEMINI_PAID_API_KEY is not set)" in excinfo.value.reasons
    summary = gating.link_summary("image", Link("gemini", "nano-banana-2-lite"), {"GOOGLE_API_KEY": "free-key"},
                                  budget_mod.budget_from_env({}), gen.GenRequest(kind="image"), adapters={})
    assert summary["missing_keys"] == ["GEMINI_PAID_API_KEY"] and summary["keyed"] is False


# ------------------------------------------------------------------ references

def test_gemini_edit_receives_at_most_four_references(store):
    story_id = _v2(store)
    trf._plant_portrait(store, story_id)
    for n in range(1, 5):
        trf._plant_upload(store, story_id, n)
    fal, gemini = FailingFal(), PricedImage(meta={"seed_honoured": False})
    ref, _log = trf._character_image(store, story_id, "turnaround", ENV, adapters=_adapters(fal, gemini))
    assert ref["source"] == LITE
    (request,) = gemini.requests
    assert request.kind == gen.IMAGE_EDIT
    assert len(request.references) == refimages.MAX_REFERENCES == 4
    assert request.references[0].endswith("portrait.png")  # the portrait first


def test_the_gemini_adapter_never_sends_more_than_fourteen_references(tmp_path):
    assert images.GEMINI_MAX_REFERENCES == 14
    paths = []
    for n in range(20):
        path = tmp_path / f"ref{n}.png"
        path.write_bytes(trf.PNG + bytes([n]))
        paths.append(str(path))
    transport = trf.FakeTransport(*trf.gemini_rules())
    request = gen.GenRequest(kind=gen.IMAGE_EDIT, prompt="p", width=720, height=1280, seed=1,
                             references=tuple(paths), out_dir=str(tmp_path), extra={"name": "x"})
    images.GEMINI.generate(Link("gemini", "nano-banana-2-lite"), request,
                           credentials={"GEMINI_PAID_API_KEY": "k"}, on_log=lambda line: None, transport=transport)
    (call,) = transport.posts("generativelanguage")
    parts = json.loads(call["body"])["contents"][0]["parts"]
    assert sum(1 for part in parts if "inline_data" in part) == 14 and "text" in parts[0]


# ------------------------------------------------------- one job, one provider

def test_within_one_job_the_first_answering_provider_is_tried_first(store):
    story_id = _v2(store)
    tools = entities.Tools()
    ctx = SimpleNamespace(settings_env=ENV, on_log=lambda line: None, cancel=CancelToken())
    kwargs = tools.image_kwargs(ctx)
    assert kwargs["sticky"] is tools.sticky == {}

    fal, gemini = FailingFal(), PricedImage(meta={"seed_honoured": False})
    adapters = _adapters(fal, gemini)
    log = trf.Log()
    common = dict(env=ENV, on_log=log, cancel=CancelToken(), adapters=adapters, sleep_fn=trf._no_sleep,
                  time_fn=lambda: 100.0, sticky=kwargs["sticky"])
    plate = refimages.place_image(store, story_id, trf.PLACE, "day", **common)
    assert plate["source"] == LITE and len(fal.requests) == 1 and tools.sticky == {"provider": "gemini"}
    # The prop, in the same job: lite first, fal is not asked again.
    prop = refimages.prop_image(store, story_id, trf.PROP, **common)
    assert prop["source"] == LITE and len(fal.requests) == 1 and len(gemini.requests) == 2

    # A job of its own (no dict shared): fal is tried first again.
    refimages.prop_image(store, story_id, trf.PROP, env=ENV, on_log=trf.Log(), cancel=CancelToken(),
                         adapters=adapters, sleep_fn=trf._no_sleep, time_fn=lambda: 100.0)
    assert len(fal.requests) == 2 and len(gemini.requests) == 3


def test_a_job_where_fal_answers_first_keeps_fal_first(store):
    story_id = _v2(store)
    fal, gemini = PricedImage(), PricedImage()
    sticky = {}
    common = dict(env=ENV, on_log=trf.Log(), cancel=CancelToken(), adapters=_adapters(fal, gemini),
                  sleep_fn=trf._no_sleep, time_fn=lambda: 100.0, sticky=sticky)
    refimages.place_image(store, story_id, trf.PLACE, "day", **common)
    refimages.prop_image(store, story_id, trf.PROP, **common)
    assert sticky == {"provider": "fal"} and len(fal.requests) == 2 and gemini.requests == []
