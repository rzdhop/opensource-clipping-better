"""The structured daily-cap refusal (plan 23, stage A4).

A job the daily cap alone refuses answers 409 with ``detail`` an object --
``{message, code: "budget_daily_cap", errors, today, estimate, cap,
needed_usd, other_cap_refusal}`` -- whose message is Track A's Wording; every
other refusal keeps its plain sentence. ``GET /estimate/{step}`` carries
today's block. ``budget.check``'s own text is byte-identical (DEC-097).

The throwaway app, the fakes and the hermetic guard are
``tests/test_stories_api_phase2.py``'s; ``spend.json`` lives under
``tmp_path`` with the day frozen at 2026-10-04 12:00 UTC, and today's other
stories book their ledgers at that instant. The route tests need pydantic,
fastapi and httpx and skip without them (the CI env); the text and CLI tests
run everywhere.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

import test_stories_api_phase2 as p2
from test_stories_api_phase2 import api, hermetic  # noqa: F401  (fixtures)
from test_story_cli import _styled, cli, studio  # noqa: F401  (fixtures and a helper)

NOON = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc).timestamp()
DAY = "2026-10-04"
SKETCH = ["Kiwilo", "Mangella", "Broccolia", "Pepperino", "Avocardo"]
# Test values only: every request goes to a fake, and the gates call nothing.
QUALITY = dict(p2.BASE, FAL_KEY="test-fal-key", ALLOW_PAID="1", DAILY_CAP_USD="4.00")
PORTRAITS = "fal/seedream-4.5"
EDITS = "fal/seedream-4.5-edit"
LITE = "gemini/nano-banana-2-lite"  # the sheet role's third link since plan 23 A8 (DEC-280)


# ------------------------------------------------------------------ helpers

@pytest.fixture
def day(api):
    """``spend.json`` under ``tmp_path`` with the day frozen at noon."""
    from clipping.providers import budget

    spend = budget.DailySpend(os.environ["SPEND_PATH"], time_fn=lambda: NOON)
    api.monkeypatch.setattr(budget, "_DEFAULT_SPEND", spend)
    return spend


def _v2_story(api, settings=QUALITY):
    """A v2 story on the Quality (billed APIs) profile, style approved, no cast yet."""
    p2._settings(api, settings)
    story_id = p2._story(api.store)
    response = api.client.patch(p2._url(story_id), json={
        "generation_profile": {"pipeline": "v2", "budget_profile": "quality"}})
    assert response.status_code == 200, response.text
    return story_id


def _spender(api, title, usd):
    """Another story that spent *usd* paid dollars today (its ledger, written at noon)."""
    from clipping.aistory.ledger import CostLedger

    story_id = api.store.create(language="fr", now=p2.NOW)["story_id"]
    api.store.update(story_id, lambda doc: doc.__setitem__("title", title), now=p2.NOW)
    path = os.path.join(api.store.story_dir(story_id), "cost_ledger.json")
    CostLedger(path, time_fn=lambda: NOON).append(step="assets", provider="fal", model="m", unit="image", qty=1,
                                                  est_usd=usd, paid=True)
    os.utime(path, (NOON, NOON))
    return story_id


def _today(api, day, spent):
    """Today at *spent*: two other stories booked $5.50 and $2.00, the rest is chain tests."""
    beach = _spender(api, "La plage", 5.50)
    market = _spender(api, "Le marché", 2.00)
    day.add(spent)
    return beach, market


def _cast(api, story_id, names=SKETCH):
    return p2._post_step(api, story_id, "cast", {"selected": list(names)})


# ------------------------------------------------------------------ the 409

def test_cast_409_daily_cap_detail_has_today_estimate_and_cap(api, day):
    story_id = _v2_story(api)
    beach, market = _today(api, day, 8.38)

    response = _cast(api, story_id)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {
        "message": ("Today's paid spending is already $8.38, over the $4.00 daily cap: this cast (est $0.60: "
                    "5 portraits $0.20 + 10 sheet edits $0.40) would bring it to $8.98. Allow $4.98 more for "
                    "today only, raise the daily cap in Settings, or wait for the day to reset at 00:00 UTC."),
        "code": "budget_daily_cap",
        # Re-pinned 2026-10-05 (plan 23 A8, DEC-280): the sheet role's third link is
        # gemini/nano-banana-2-lite, keyless here, so its reason follows fal's.
        "errors": [f"{PORTRAITS}: refused: est $0.200 on {PORTRAITS} would bring today to $8.58 of the $4.00 "
                   "daily cap", f"{LITE}: no API key (GEMINI_PAID_API_KEY is not set)"],
        "today": {"day": DAY, "zone": "UTC", "spent_usd": 8.38, "extra_usd": 0.0,
                  "stories": [{"story_id": beach, "title": "La plage", "usd": 5.5},
                              {"story_id": market, "title": "Le marché", "usd": 2.0}],
                  "story_count": 2, "other_usd": 0.88},
        "estimate": {"usd": 0.6, "llm_worst_usd": 0.0, "parts": [
            {"what": "portraits", "qty": 5, "usd": 0.2, "link": PORTRAITS},
            {"what": "sheet edits", "qty": 10, "usd": 0.4, "link": EDITS}]},
        "cap": {"daily_usd": 4.0, "effective_usd": 4.0},
        "needed_usd": 4.98,
        "other_cap_refusal": None,
    }
    # Nothing exists, nothing was spent.
    assert api.jobs.list_jobs() == [] and api.submitted == []
    assert day.today_total() == 8.38


def test_the_409_names_the_configured_zone_s_midnight(api, day):
    """Plan 23 A7: with BUDGET_TIMEZONE set in Settings the message, ``today``
    and the day key follow it (noon UTC is 14:00 in Paris: the same day)."""
    from clipping.providers import budget

    story_id = _v2_story(api, dict(QUALITY, BUDGET_TIMEZONE="Europe/Paris"))
    budget.set_settings_reader(api.worker.get_settings_env)
    _today(api, day, 8.38)

    detail = _cast(api, story_id).json()["detail"]

    assert detail["message"].endswith(
        "Allow $4.98 more for today only, raise the daily cap in Settings, or wait for the day to reset at "
        "00:00 Europe/Paris.")
    assert (detail["today"]["day"], detail["today"]["zone"]) == (DAY, "Europe/Paris")
    assert detail["today"]["spent_usd"] == 8.38 and detail["today"]["story_count"] == 2


def test_under_cap_message_variant(api, day):
    """Under the cap the portraits alone go over it: "is $X of the cap"; an
    extra already allowed for today is named next to the saved cap."""
    story_id = _v2_story(api)
    _today(api, day, 3.85)

    detail = _cast(api, story_id).json()["detail"]

    assert detail["message"] == (
        "Today's paid spending is $3.85 of the $4.00 daily cap: this cast (est $0.60: 5 portraits $0.20 + "
        "10 sheet edits $0.40) would bring it to $4.45. Allow $0.45 more for today only, raise the daily cap in "
        "Settings, or wait for the day to reset at 00:00 UTC.")
    assert detail["needed_usd"] == 0.45

    day.add(0.30)            # $4.15 spent ...
    day.add_extra(0.10)      # ... of the $4.00 cap + $0.10 allowed today: over the effective cap
    detail = _cast(api, story_id).json()["detail"]
    assert detail["message"].startswith(
        "Today's paid spending is already $4.15, over the $4.00 daily cap + $0.10 allowed today: this cast")
    assert detail["cap"] == {"daily_usd": 4.0, "effective_usd": 4.1}
    assert detail["needed_usd"] == 0.65 and detail["today"]["extra_usd"] == 0.1


def test_needed_usd_rounds_up_and_includes_llm_worst(api, day):
    """On a billed first LLM link the step's calls' worst case is added, and
    the sum is rounded up to the cent (never down)."""
    from clipping.aistory.steps import llm_spend
    from clipping.providers.budget import ceil_cent
    from clipping.providers.registry import Link

    billed = dict(QUALITY, LLM_CHAIN="openrouter/mistralai/mistral-medium-3.1", OPENROUTER_API_KEY="test-or-key")
    story_id = _v2_story(api, settings=billed)
    day.add(8.3801)

    detail = _cast(api, story_id, names=["Kiwilo"]).json()["detail"]

    worst = 3 * llm_spend.worst_call_usd(Link("openrouter", "mistralai/mistral-medium-3.1"))  # D1 + D2 + K1
    assert worst > 0
    assert detail["estimate"]["llm_worst_usd"] == pytest.approx(worst)
    assert detail["estimate"]["usd"] == pytest.approx(0.12)   # 1 portrait + 2 sheet edits
    assert detail["needed_usd"] == ceil_cent(8.3801 + 0.12 + worst - 4.00)
    assert detail["needed_usd"] >= 8.3801 + 0.12 + worst - 4.00
    # The rounding itself: up to the cent, float noise excepted.
    assert (ceil_cent(4.98), ceil_cent(8.98 - 4.00), ceil_cent(4.9801), ceil_cent(-1.0)) == (4.98, 4.98, 4.99, 0.0)


def test_story_cap_refusal_is_not_offered_a_day_extra(api, day):
    """The story's cap refuses: the plain sentence of today, no code, no
    extra offered. Over the day *and* the story's cap: the day's detail,
    with what an extra would not lift in ``other_cap_refusal``."""
    story_id = _v2_story(api, settings=dict(QUALITY, PER_STORY_CAP_USD="0.10"))

    response = _cast(api, story_id)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    # Re-pinned 2026-10-05 (plan 23 A8, DEC-280): the keyless lite link's reason joins the sentence.
    assert detail == (f"No link of the quality sheet links (quality budget profile) can make a reference image on route auto: {PORTRAITS}: "
                      f"refused: est $0.200 on {PORTRAITS} would bring this story to $0.20 of its $0.10 cap; "
                      f"{LITE}: no API key (GEMINI_PAID_API_KEY is not set).")

    day.add(8.38)
    detail = _cast(api, story_id).json()["detail"]
    assert detail["code"] == "budget_daily_cap"
    assert detail["other_cap_refusal"] == (
        "refused: est $0.600 on this cast would bring this story to $0.60 of its $0.10 cap")


def test_allow_paid_off_and_no_link_keep_their_plain_sentences(api, day):
    story_id = _v2_story(api, settings=dict(QUALITY, ALLOW_PAID=""))
    detail = _cast(api, story_id).json()["detail"]
    assert isinstance(detail, str) and "allow_paid is off" in detail

    p2._settings(api, dict(QUALITY, FAL_KEY=""))
    detail = _cast(api, story_id).json()["detail"]
    assert isinstance(detail, str) and "no API key (FAL_KEY is not set)" in detail


def test_estimate_carries_today_block(api, day):
    from web.api.routes import budget as budget_route

    story_id = _v2_story(api)
    beach, _market = _today(api, day, 8.38)

    body = p2._estimate(api, story_id, "cast", selected=SKETCH)

    assert body["ready"] is False
    assert body["today"] == budget_route.today_block(api.worker.get_settings_env())
    assert (body["today"]["spent_usd"], body["today"]["daily_cap_usd"], body["today"]["story_count"]) == (
        8.38, 4.0, 2)
    assert body["today"]["stories"][0]["story_id"] == beach
    # An LLM step's estimate carries it too: one place in the route.
    assert p2._estimate(api, story_id, "places_proposal")["today"]["spent_usd"] == 8.38
    # The verdict's day block: the first refused link's numbers.
    assert body["edit"]["budget"] == {"cap": "day", "spent": 8.38, "cap_usd": 4.0, "extra": 0.0,
                                      "needed_usd_for_this_call": 4.78}


def test_link_reasons_unchanged_in_errors(api, day):
    """``errors`` are the links' reasons as the estimate and the message of
    today name them: ``budget.check``'s text, byte for byte."""
    from clipping.providers import budget

    story_id = _v2_story(api)
    day.add(8.38)

    detail = _cast(api, story_id).json()["detail"]
    rows = p2._estimate(api, story_id, "cast", selected=SKETCH)["links"]

    assert detail["errors"] == [f"{row['link']}: {row['reason']}" for row in rows]
    with pytest.raises(budget.BudgetRefused) as caught:
        budget.check(0.2, budget=budget.budget_from_env(QUALITY), day_spent=8.38)
    # Re-pinned 2026-10-05 (plan 23 A8, DEC-280): lite's keyless reason follows fal's refusal.
    assert detail["errors"] == [f"{PORTRAITS}: {str(caught.value).replace('this call', PORTRAITS)}",
                                f"{LITE}: no API key (GEMINI_PAID_API_KEY is not set)"]
    assert (caught.value.cap, caught.value.usd, caught.value.spent, caught.value.cap_usd, caught.value.extra) == (
        "day", 0.2, 8.38, 4.0, 0.0)


def test_cli_prints_the_message_of_an_object_detail(studio):
    """The CLI runs the gates in-process (no HTTP, no ``detail`` to read): a
    cast the daily cap refuses prints the verdict's one sentence -- never the
    structured block the API builds from it."""
    from clipping.aistory import cli as cli_module
    from clipping.aistory import workflow
    from clipping.aistory.store import StoryStore

    source = open(cli_module.__file__, encoding="utf-8").read()
    assert "detail" not in source  # nothing in the CLI reads a 409 body

    story_id = _styled(studio)
    for name, value in {"IMAGE_CHAIN": "fal/flux-schnell", "FAL_KEY": "test-fal-key", "ALLOW_PAID": "1",
                        "DAILY_CAP_USD": "0.001"}.items():
        studio.cli.monkeypatch.setenv(name, value)
    stories = StoryStore(str(studio.cli.outputs))
    verdict = workflow.image_verdict(stories, stories.get(story_id), 1, env={})
    assert verdict.get("budget", {}).get("cap") == "day", verdict  # the block the API turns into an object

    code = studio.run("step", story_id, "cast", "--characters", "Kiwilo")

    err = studio.capsys.readouterr().err.strip()
    assert code == 1
    assert err.startswith("No link of IMAGE_CHAIN can make a reference image on route auto: fal/flux-schnell: "
                          "refused: est $")
    assert "daily cap" in err and "{" not in err and "budget_daily_cap" not in err


def test_clip_gate_day_refusal_is_structured_and_other_caps_stay_plain(api, day):
    """A shot's clip the daily cap refuses (``regenerate_clip_estimate``'s
    ``refusal``) meets the same structured 409; another cap's, its sentence."""
    from fastapi import HTTPException

    story_id = _v2_story(api)
    story = api.store.get(story_id)
    day.add(3.90)
    message = "1 clip (6 s) of shot sh01 on fal/seedance would go over a cap, so nothing would be generated or spent."
    estimate = {"step": "regenerate", "target": "shot:1:sh01:video", "est_usd": 0.36, "link": "fal/seedance",
                "units": {"clips": 1, "seconds": 6}, "ready": False, "message": message,
                "refusal": {"cap": "day", "usd": 0.36, "spent": 3.9, "cap_usd": 4.0, "extra": 0.0}}

    with pytest.raises(HTTPException) as caught:
        api.routes._clip_gate(estimate, api.store, story, env=QUALITY)()
    detail = caught.value.detail
    assert caught.value.status_code == 409 and detail["code"] == "budget_daily_cap"
    assert detail["message"] == (
        "Today's paid spending is $3.90 of the $4.00 daily cap: this clip (est $0.36: 1 clip $0.36) would bring it "
        "to $4.26. Allow $0.26 more for today only, raise the daily cap in Settings, or wait for the day to reset "
        "at 00:00 UTC.")
    assert detail["errors"] == [message] and detail["needed_usd"] == 0.26

    estimate["refusal"] = dict(estimate["refusal"], cap="episode")
    with pytest.raises(HTTPException) as caught:
        api.routes._clip_gate(estimate, api.store, story, env=QUALITY)()
    assert caught.value.detail == message
