"""Plan 28 stage A6: an honest price and the Generate button (DEC-305 section 1).

- The one-click estimate prices a native-speech episode's clips from its plan
  before the storyboard exists (planned shots x their length x the link's
  price a second, speaking and silent apart), so the card shows the real total
  and the cap verdict before the click.
- The handoff carries ``generate_price`` on each of the human's own clips
  still missing and for all of them; ``POST .../clips/generate`` switches the
  shot(s) to auto and starts the clip job -- refused in plain words, nothing
  switched and nothing booked, over a cap or without a key.
- Pre-click warnings: no speech-to-text key, a provider's own refusal of the
  story's last run.

Stdlib + pytest (DEC-012); the API tests skip without fastapi.
"""

from __future__ import annotations

import os

import pytest

import test_story_assets_step as tas
import test_story_native_speech_plan as nsp
from test_api_shot_upload import api  # noqa: F401 - the upload routes' client
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_plan_feasibility import _native_story
from test_story_shot_modes import PAID, _keyframe

NOW = tas.NOW
CAPS = {"ALLOW_PAID": "1", "PER_EPISODE_CAP_USD": "40", "DAILY_CAP_USD": "80", "PER_STORY_CAP_USD": "200"}
KEYS = {"GEMINI_PAID_API_KEY": "test-gemini-paid-key", "FAL_KEY": "test-fal-key"}
FAST_PRICE, LITE_PRICE = 0.10, 0.05  # gemini/veo-3.1-fast (speaking) and -lite (silent), a second at 720p
HANDOFF = "/api/stories/{}/episodes/1/handoff"
GENERATE = "/api/stories/{}/episodes/1/clips/generate"
STT = "No speech check: add a GROQ_API_KEY or MISTRAL_API_KEY in Settings (free)"


def _ft():
    from clipping.aistory.steps import fast_track

    return fast_track


# ---------------------------------------------------- (a) the estimate prices the clips from the plan

def test_the_one_click_estimate_prices_the_clips_from_the_plan_before_the_storyboard(store):
    from clipping.aistory.steps import episode_common, generate_clips

    story_id = _native_story(store, "confrontation_50s_v2", narrator=False)
    ec = episode_common.load_context(store, story_id, 1)
    settings = tas._settings(**KEYS, **CAPS)

    estimate = _ft().estimate(ec, env=settings)

    video = estimate["video"]
    shots = generate_clips.planned_shots(ec, None)
    speech = sum(clip_s for clip_s, speaks in shots if speaks)
    silent = sum(clip_s for clip_s, speaks in shots if not speaks)
    assert video["basis"] == "plan" and video["count"] == len(shots) > 0 and video["seconds"] == speech + silent
    retake = video["speech"]["retake_usd"]
    assert video["est_usd"] == pytest.approx(speech * FAST_PRICE + silent * LITE_PRICE + retake)
    assert video["ready"] and video["route_class"] == "paid"
    assert estimate["paid"]["est_usd"] >= video["est_usd"] > 0  # in the total the card shows
    # The cap verdict before the click: a $2 episode cannot buy them.
    tight = _ft().estimate(ec, env=dict(settings, PER_EPISODE_CAP_USD="2"))
    assert tight["stops_at"]["step"] == "paid_check" and "clip" in tight["stops_at"]["reason"]


def test_the_estimate_warns_before_the_click_without_a_speech_check_key(store):
    from clipping.aistory.steps import episode_common

    story_id = _native_story(store, "confrontation_50s_v2", narrator=False)
    ec = episode_common.load_context(store, story_id, 1)

    assert STT in _ft().estimate(ec, env=tas._settings(**KEYS, **CAPS))["warnings"]
    keyed = _ft().estimate(ec, env=tas._settings(**KEYS, **CAPS, GROQ_API_KEY="test-groq"))
    assert STT not in keyed["warnings"]


def test_a_provider_s_own_refusal_of_the_last_run_is_a_warning(store):
    from clipping.aistory import store as store_mod
    from clipping.aistory.steps import generate_clips

    story_id = _native_story(store, "confrontation_50s_v2", narrator=False)
    path = os.path.join(store.story_dir(story_id), store_mod.ACTIVITY_LOG)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("2026-10-05T20:01:00+00:00 [aaaa00000001 assets] ⚠️ fal/seedream-4.5-edit failed | HttpStatusError: "
                 "HTTP 403 from https://queue.fal.run/x: User is locked. Reason: Exhausted balance.; kept\n")
    assert generate_clips.provider_refusal(store, story_id) == (
        "fal/seedream-4.5-edit refused the last run: “User is locked. Reason: Exhausted balance.” Top up that "
        "account, or pick another link, before you generate.")
    # A later run of a generating step that met no refusal clears it.
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("2026-10-05T21:00:00+00:00 [aaaa00000002 assets] 🖼 Shot sh01's keyframe made\n")
    assert generate_clips.provider_refusal(store, story_id) is None


# ---------------------------------------------------- (b) the handoff and the Generate route

def _manual(api, monkeypatch, settings):
    """The manual profile (every clip the human's), its keyframes approved (the clips' hold lifted), *settings*."""
    from clipping.aistory.steps import assets
    from web.api import worker

    monkeypatch.setattr(assets, "clip_hold", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(assets, "keyframe_problem", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker, "_settings_env", tas._settings(**settings))
    return nsp.planned_story(api.store, profile="native_speech_manual")


def _handoff(api, story_id):
    response = api.client.get(HANDOFF.format(story_id))
    assert response.status_code == 200, response.text
    return response.json()


def _own(body):
    return [shot for shot in body["shots"]
            if shot["clip"] and shot["clip"]["mode"] == "manual" and shot["clip"]["state"] == "missing"]


def test_the_handoff_carries_the_generate_price_of_each_own_clip_and_of_all(api, monkeypatch):
    story_id = _manual(api, monkeypatch, PAID)

    body = _handoff(api, story_id)

    own = _own(body)
    assert own
    for shot in own:
        price = shot["clip"]["generate_price"]
        rate = FAST_PRICE if shot["speaks"] else LITE_PRICE
        assert price["usd"] == pytest.approx(shot["clip_s"] * rate) and price["allowed"] is True
        assert price["reason"] is None
    every = body["generate_price"]
    assert every["count"] == len(own) and every["shot_ids"] == [shot["shot_id"] for shot in own]
    assert every["usd"] >= sum(shot["clip"]["generate_price"]["usd"] for shot in own) - 1e-9
    assert every["allowed"] is True
    assert STT in body["warnings"]


def test_generate_switches_the_shot_to_auto_and_starts_its_clip_job(api, monkeypatch):
    from web.api import store as job_store

    story_id = _manual(api, monkeypatch, PAID)
    shot_id = _own(_handoff(api, story_id))[0]["shot_id"]
    _keyframe(api.store, story_id, shot_id)

    response = api.client.post(GENERATE.format(story_id), json={"shot_id": shot_id})

    assert response.status_code == 201, response.text
    job = job_store.get_job(response.json()["id"])
    assert (job["step"], job["params"]["target"]) == ("regenerate", f"shot:1:{shot_id}:video")
    clip = next(shot for shot in _handoff(api, story_id)["shots"] if shot["shot_id"] == shot_id)["clip"]
    assert clip["mode"] == "auto"


def test_generate_over_a_cap_is_refused_in_plain_words_with_nothing_switched_or_booked(api, monkeypatch):
    from web.api import store as job_store

    story_id = _manual(api, monkeypatch, dict(PAID, PER_EPISODE_CAP_USD="0.10"))
    body = _handoff(api, story_id)
    shot_id = _own(body)[0]["shot_id"]
    _keyframe(api.store, story_id, shot_id)
    price = _own(body)[0]["clip"]["generate_price"]
    assert price["allowed"] is False and "cap" in price["reason"]  # said before the click

    response = api.client.post(GENERATE.format(story_id), json={"shot_id": shot_id})

    assert response.status_code == 409 and "cap" in response.json()["detail"]
    assert job_store._jobs == {}
    clip = next(shot for shot in _handoff(api, story_id)["shots"] if shot["shot_id"] == shot_id)["clip"]
    assert clip["mode"] == "manual"
    assert not api.store.read_episode_doc(story_id, 1, "assets.json") or "shot_modes" not in api.store.read_episode_doc(
        story_id, 1, "assets.json")


def test_generate_all_buys_nothing_beyond_its_price_then_switches_every_own_clip_and_starts_the_assets_step(
        api, monkeypatch):
    from clipping.aistory import media_policy
    from clipping.aistory.steps import assets
    from web.api import store as job_store

    story_id = _manual(api, monkeypatch, PAID)
    monkeypatch.setattr(assets, "shots_to_make", lambda *_args, **_kwargs: [])  # every keyframe made already
    body = _handoff(api, story_id)
    own = [shot["shot_id"] for shot in _own(body)]
    # The keyframes' redraw ceiling is still open: the assets step would spend it too -- refused, nothing switched.
    refused = api.client.post(GENERATE.format(story_id), json={})
    assert refused.status_code == 409 and "would also buy $" in refused.json()["detail"]
    assert f"not the ${body['generate_price']['usd']:.2f} shown" in refused.json()["detail"]
    assert job_store._jobs == {} and all(shot["clip"]["mode"] == "manual" for shot in _own(_handoff(api, story_id)))

    monkeypatch.setattr(media_policy, "keyframe_fix", lambda *_args, **_kwargs: None)  # the keyframes are checked
    response = api.client.post(GENERATE.format(story_id), json={})

    assert response.status_code == 201, response.text
    job = job_store.get_job(response.json()["id"])
    assert (job["step"], job["ep"]) == ("assets", 1)
    modes = {shot["shot_id"]: shot["clip"]["mode"] for shot in _handoff(api, story_id)["shots"] if shot["clip"]}
    assert all(modes[sid] == "auto" for sid in own)
