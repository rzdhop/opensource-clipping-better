"""Plan 28 stage F5 (DEC-305 section 5): one image link per story for its character sheets, place plates and props.

An episode's keyframes already keep one link (A-087, DEC-204, ``steps/sticky_link.py``); the sheets, plates and
props only kept a within-job preference (DEC-280), so a later job or one regenerate started from fal again and could
land on gemini -- one story, two ways of drawing its cast. Now the first sheet, plate or prop made records the link
that answered as ``story.json``'s ``links.image``; every later one is asked of that link alone (a like-for-like swap
is that link; the same provider's edit sibling of a text link is too); a link that is gone stops the image with a
plain sentence, and only an explicit story edit (``PATCH {"links": {"image": ...}}``) switches. An episode with no
image yet starts its keyframes on the story's link. A story that mixed providers before the record existed still
loads and regenerates: the lock applies from the first record onward. The human's own images
(``manual/upload``) and a legacy story are never locked.

The fakes and the story are the second-link tests' (``tests/test_story_nano_banana_second_link.py``); offline and
hermetic (the refimages fixtures). Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import test_story_nano_banana_second_link as nb
import test_story_refimages as trf
from test_story_refimages import hermetic, store  # noqa: F401 - the refimages fixtures (hermetic is autouse)

from clipping.aistory import refimages, workflow
from clipping.aistory.steps import assets, sticky_link
from clipping.cancel import CancelToken

FAL, FAL_EDIT, LITE = "fal/seedream-4.5", "fal/seedream-4.5-edit", nb.LITE
ENV = nb.ENV
LATER = "2026-10-06T09:00:00+00:00"


def _job(store, story_id, call, env=ENV, adapters=None, **kwargs):
    """One call of a refimages maker as its own job (no shared ``sticky`` dict)."""
    return call(store, story_id, env=env, on_log=trf.Log(), cancel=CancelToken(), adapters=adapters,
                sleep_fn=trf._no_sleep, time_fn=lambda: 100.0, **kwargs)


def _plate(store, story_id, **kwargs):
    return _job(store, story_id, lambda s, i, **kw: refimages.place_image(s, i, trf.PLACE, "day", **kw), **kwargs)


def _prop(store, story_id, **kwargs):
    return _job(store, story_id, lambda s, i, **kw: refimages.prop_image(s, i, trf.PROP, **kw), **kwargs)


def _link(store, story_id):
    return (store.get(story_id).get("links") or {}).get("image")


# ------------------------------------------------------------ the record

def test_the_first_image_made_records_the_story_link_and_a_later_job_stays_on_it(store):
    story_id = nb._v2(store)
    assert _link(store, story_id) is None
    fal, gemini = nb.PricedImage(), nb.PricedImage()
    ref = _plate(store, story_id, adapters=nb._adapters(fal, gemini))
    assert ref["source"] == FAL
    record = _link(store, story_id)
    assert record["link"] == FAL and record["since"] and "switched_from" not in record

    # A job of its own, fal refusing now (a 500): the lock keeps it on fal -- gemini is never asked.
    broken, gemini2 = nb.FailingFal(), nb.PricedImage()
    with pytest.raises(refimages.RefImageError) as excinfo:
        _prop(store, story_id, adapters=nb._adapters(broken, gemini2))
    assert not isinstance(excinfo.value, refimages.StoryLinkGone)  # a failure of this one request, not a gone link
    assert len(broken.requests) == 1 and gemini2.requests == []
    assert _link(store, story_id) == record  # the record is the first one's, untouched


def test_a_story_whose_first_image_fell_to_gemini_stays_on_gemini(store):
    story_id = nb._v2(store)
    ref = _plate(store, story_id, adapters=nb._adapters(nb.FailingFal(), nb.PricedImage(meta={"seed_honoured": False})))
    assert ref["source"] == LITE and _link(store, story_id)["link"] == LITE
    fal, gemini = nb.PricedImage(), nb.PricedImage(meta={"seed_honoured": False})
    assert _prop(store, story_id, adapters=nb._adapters(fal, gemini))["source"] == LITE
    assert fal.requests == [] and len(gemini.requests) == 1


def test_a_sheet_edit_goes_to_the_edit_sibling_of_the_recorded_text_link(store):
    story_id = nb._v2(store)
    fal, gemini = nb.PricedImage(), nb.PricedImage()
    adapters = nb._adapters(fal, gemini)
    portrait, _log = trf._character_image(store, story_id, "portrait", ENV, adapters=adapters)
    assert portrait["source"] == FAL and _link(store, story_id)["link"] == FAL
    turnaround, _log = trf._character_image(store, story_id, "turnaround", ENV, adapters=adapters)
    assert turnaround["source"] == FAL_EDIT and gemini.requests == []
    assert _link(store, story_id)["link"] == FAL  # the story's link stays the one that served first


def test_a_gone_link_stops_with_a_plain_sentence_and_the_offer_to_switch(store):
    story_id = nb._v2(store)
    _plate(store, story_id, adapters=nb._adapters(nb.PricedImage(), nb.PricedImage()))
    fal, gemini = nb.PricedImage(), nb.PricedImage()
    no_fal = {**trf.GEMINI, **trf.PAID_ON}  # fal's key is gone
    with pytest.raises(refimages.StoryLinkGone) as excinfo:
        _prop(store, story_id, env=no_fal, adapters=nb._adapters(fal, gemini))
    error = excinfo.value
    assert fal.requests == [] and gemini.requests == []  # nothing generated or spent
    assert error.link == FAL and error.next_link == LITE and error.switch == {"links": {"image": LITE}}
    sentence = str(error)
    assert f"The story's image link {FAL} cannot serve now" in sentence and "no API key" in sentence
    assert "so no other link was tried: nothing was generated or spent" in sentence
    assert f'{{"links": {{"image": "{LITE}"}}}}' in sentence
    assert _link(store, story_id)["link"] == FAL  # a switch is the user's, through a story edit

    # A link no provider of the role names any more is gone too, with the same sentence.
    other = nb._v2(store, profile="quality")
    store.update(other, lambda doc: doc.update(links={"image": {"link": "recraft/v3", "since": trf.NOW}}), now=trf.NOW)
    with pytest.raises(refimages.StoryLinkGone, match="not a link of the quality"):
        _prop(store, other, adapters=nb._adapters(fal, gemini))


def test_a_rate_limited_link_is_not_gone(store):
    story_id = nb._v2(store)
    _plate(store, story_id, adapters=nb._adapters(nb.PricedImage(), nb.PricedImage()))

    class Pushing(nb.PricedImage):
        def generate(self, link, request, *, credentials, on_log, transport=None, **_):
            self.requests.append(request)
            raise RuntimeError("HTTP 429 from https://fal.run/x: rate limit exceeded")

    pushing, gemini = Pushing(), nb.PricedImage()
    with pytest.raises(refimages.RefImageError) as excinfo:
        _prop(store, story_id, adapters=nb._adapters(pushing, gemini))
    assert not isinstance(excinfo.value, refimages.StoryLinkGone) and gemini.requests == []


# ----------------------------------------------------------- the explicit switch

def test_only_a_story_edit_switches_the_link_and_the_next_image_follows(store):
    story_id = nb._v2(store)
    _plate(store, story_id, adapters=nb._adapters(nb.PricedImage(), nb.PricedImage()))
    workflow.patch_story(store, story_id, {"links": {"image": LITE}}, now=LATER)
    record = _link(store, story_id)
    assert record["link"] == LITE and record["since"] == LATER and record["switched_from"] == FAL

    fal, gemini = nb.PricedImage(), nb.PricedImage(meta={"seed_honoured": False})
    assert _prop(store, story_id, adapters=nb._adapters(fal, gemini))["source"] == LITE
    assert fal.requests == [] and len(gemini.requests) == 1
    # The same link again writes nothing new.
    workflow.patch_story(store, story_id, {"links": {"image": LITE}}, now="2026-10-07T09:00:00+00:00")
    assert _link(store, story_id) == record


@pytest.mark.parametrize("value,fragment", [
    ({"image": "fal/not-in-the-profile"}, "is not a link of this story's budget profile"),
    ({"image": "cloudflare/flux-1-schnell"}, "makes drafts"),
    ({"video": LITE}, 'expected {"image"'),
    ({"image": 3}, 'expected {"image"'),
])
def test_a_switch_to_a_link_the_story_cannot_use_is_refused_and_writes_nothing(store, value, fragment):
    story_id = nb._v2(store)
    with pytest.raises(workflow.WorkflowError) as excinfo:
        workflow.patch_story(store, story_id, {"links": value}, now=LATER)
    assert excinfo.value.code == workflow.INVALID and fragment in str(excinfo.value.detail)
    assert _link(store, story_id) is None


def test_a_legacy_story_keeps_no_image_link(store):
    story_id = trf._story(store)
    with pytest.raises(workflow.WorkflowError) as excinfo:
        workflow.patch_story(store, story_id, {"links": {"image": LITE}}, now=LATER)
    assert "legacy story" in str(excinfo.value.detail) and _link(store, story_id) is None
    # Its images are made as before: its free chain falls through, and nothing is recorded.
    ref = _plate(store, story_id, env=trf.FREE, adapters={("image", "pollinations"): trf.FakeImage()})
    assert ref["source"] and _link(store, story_id) is None


# ------------------------------------------------------- what the lock never touches

def test_a_story_that_already_mixed_providers_still_regenerates_and_records_from_here(store):
    """Dragon Fruit: sheets from fal, then gemini after fal's balance ran out. No record exists; the lock starts
    with the next image made and refuses nothing already on disk."""
    story_id = nb._v2(store)
    trf._plant_portrait(store, story_id)
    assert _link(store, story_id) is None
    fal, gemini = nb.FailingFal(), nb.PricedImage(meta={"seed_honoured": False})
    ref, _log = trf._character_image(store, story_id, "portrait", ENV, adapters=nb._adapters(fal, gemini))
    assert ref["source"] == LITE and _link(store, story_id)["link"] == LITE
    # And the story still loads and validates with the record on it.
    assert store.get(story_id)["links"]["image"]["link"] == LITE


def test_the_humans_own_images_are_never_locked(store):
    story_id = nb._v2(store, profile="native_speech_manual")
    store.update(story_id, lambda doc: doc["generation_profile"].update(images="manual"), now=trf.NOW)
    with pytest.raises(refimages.RefImageError) as excinfo:
        trf._character_image(store, story_id, "portrait", ENV, adapters=None)
    assert not isinstance(excinfo.value, refimages.StoryLinkGone)
    assert _link(store, story_id) is None
    with pytest.raises(workflow.WorkflowError) as switched:
        workflow.patch_story(store, story_id, {"links": {"image": LITE}}, now=LATER)
    assert "your own" in str(switched.value.detail)


# ------------------------------------------------------ the episode starts from it

def _episode(story):
    return SimpleNamespace(story=story, consistency_mode="references")


def test_an_episode_with_no_image_yet_starts_its_keyframes_on_the_story_link():
    story = {**nb._v2_story(), "links": {"image": {"link": FAL, "since": trf.NOW}}}
    info = assets.episode_image_link(_episode(story), {"shots": []}, doc=None)
    # The keyframe role's chain holds the edit sibling of the story's text link, same provider.
    assert (info["link"], info["source"], info["since"]) == (FAL_EDIT, "story", trf.NOW)
    assert info["mixed"] == [] and info["served"] == {}

    # The episode's own record wins; with no story link nothing changes.
    record = {"links": {"image": sticky_link.record(LITE, now=trf.NOW)}}
    own = assets.episode_image_link(_episode(story), {"shots": []}, doc=record)
    assert (own["link"], own["source"]) == (LITE, "record")
    bare = assets.episode_image_link(_episode(nb._v2_story()), {"shots": []}, doc=None)
    assert (bare["link"], bare["source"]) == (None, None)

    # A story link the keyframe chain does not hold leaves the episode free, never stopped.
    elsewhere = {**nb._v2_story(), "links": {"image": {"link": "recraft/v3", "since": trf.NOW}}}
    assert assets.episode_image_link(_episode(elsewhere), {"shots": []}, doc=None)["link"] is None


def test_an_episode_with_an_image_on_disk_is_never_judged_against_the_story_link(monkeypatch):
    story = {**nb._v2_story(), "links": {"image": {"link": FAL, "since": trf.NOW}}}
    board = {"shots": [{"shot_id": "sh01", "assets": {}}]}
    monkeypatch.setattr(assets, "shot_image_path", lambda ec, shot: "/some/upload.png")
    monkeypatch.setattr(assets, "own_keyframe", lambda story, shot, doc: False)
    monkeypatch.setattr(assets, "shot_state", lambda ec, shot, link=None, doc=None: "stale")
    info = assets.episode_image_link(_episode(story), board, doc=None)
    assert (info["link"], info["source"]) == (None, None)


def test_the_story_link_is_a_v2_notion_only():
    legacy = {"story_id": "0123456789ab", "links": {"image": {"link": FAL, "since": trf.NOW}},
              "generation_profile": {"tier": 1, "route": "auto", "consistency_mode": "references",
                                     "budget_profile": "free"}}
    info = assets.episode_image_link(_episode(legacy), {"shots": []}, doc=None)
    assert (info["link"], info["source"]) == (None, None)


def test_pinning_a_chain_picks_the_link_or_its_swap_or_the_same_provider_sibling():
    assert sticky_link.pin_labels([FAL, LITE], FAL) == [FAL]
    assert sticky_link.pin_labels([FAL_EDIT, LITE], FAL) == [FAL_EDIT]  # the edit chain: fal's sibling
    assert sticky_link.pin_labels([FAL, LITE], LITE) == [LITE]
    assert sticky_link.pin_labels(["gemini/nano-banana-2"], "gemini/nano-banana-2-lite") == ["gemini/nano-banana-2"]
    assert sticky_link.pin_labels([FAL, LITE], "recraft/v3") == []
    assert sticky_link.pin_labels([], FAL) == []


def test_the_story_patch_request_carries_the_links_field():
    pytest.importorskip("pydantic")  # the CI-like env has none
    from web.api.models import StoryPatchRequest

    request = StoryPatchRequest(links={"image": LITE})
    assert request.model_dump(exclude_unset=True) == {"links": {"image": LITE}}
    assert "links" in workflow.PATCH_FIELDS
