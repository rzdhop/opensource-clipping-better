"""The full cast cost, checked before the job exists (plan 23, stage A5).

``workflow.generation_budget`` sums a step's images and edits and runs one
budget check on the sum; the cast/places estimate shows that sum and the gate
checks it (RC-V6: one function, two callers). A v2 story is refused before any
portrait is bought when the sum goes over a cap or the budget refuses an edit;
a legacy story keeps DEC-117's stop-and-ask before its edits. The gate books
nothing; ``refimages`` checks each image again as it runs and books it once.

The app, the fakes and the hermetic guard are
``tests/test_stories_api_phase2.py``'s; ``spend.json`` lives under
``tmp_path``, the day frozen at 2026-10-04 12:00 UTC. Needs pydantic, fastapi
and httpx (skips in the CI env, like the other route tests).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

import test_stories_api_phase2 as p2
from test_stories_api_phase2 import api, hermetic  # noqa: F401  (fixtures)

NOON = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc).timestamp()
SKETCH = ["Kiwilo", "Mangella", "Broccolia", "Pepperino", "Avocardo"]
# Test values only: every request goes to a fake, and the gates call nothing.
QUALITY = dict(p2.BASE, FAL_KEY="test-fal-key", ALLOW_PAID="1", DAILY_CAP_USD="4.00")
PORTRAITS = "fal/seedream-4.5"
EDITS = "fal/seedream-4.5-edit"
PARTS = [{"what": "portraits", "qty": 5, "usd": 0.2, "link": PORTRAITS},
         {"what": "sheet edits", "qty": 10, "usd": 0.4, "link": EDITS}]


@pytest.fixture
def day(api):
    """``spend.json`` under ``tmp_path`` with the day frozen at noon."""
    from clipping.providers import budget

    spend = budget.DailySpend(os.environ["SPEND_PATH"], time_fn=lambda: NOON)
    api.monkeypatch.setattr(budget, "_DEFAULT_SPEND", spend)
    return spend


def _v2_story(api, settings=QUALITY):
    p2._settings(api, settings)
    story_id = p2._story(api.store)
    response = api.client.patch(p2._url(story_id), json={
        "generation_profile": {"pipeline": "v2", "budget_profile": "quality"}})
    assert response.status_code == 200, response.text
    return story_id


def _cast(api, story_id, names=SKETCH):
    return p2._post_step(api, story_id, "cast", {"selected": list(names)})


def _budget(api, story_id, units, *, env):
    from clipping.aistory import workflow

    story = api.store.get(story_id)
    images = workflow.image_verdict(api.store, story, units["images"], env=env) if units["images"] else None
    edit = (workflow.edit_readiness(api.store, story, env=env, qty=units["edit_images"])
            if units["edit_images"] else None)
    return workflow.generation_budget(api.store, story, units, images, edit, env=env)


def _priced(api):
    """The fake paid editor priced as the real one is (``pricing.estimate``),
    so the runner's own check of each edit sees its dollars."""
    from clipping.providers import pricing

    api.monkeypatch.setattr(api.fakes.paid_editor, "estimate", lambda link, request: pricing.estimate(
        link, 1, width=request.width, height=request.height))


def _nothing_happened(api, day, spent):
    assert api.jobs.list_jobs() == [] and api.submitted == []
    assert day.today_total() == spent


# ------------------------------------------------------------------ tests

def test_cast_gate_refuses_when_portraits_plus_edits_exceed_day_cap(api, day):
    """$3.50 spent of $4.00: the 5 portraits ($0.20) fit, the 10 sheet edits
    ($0.40) fit, together ($0.60) they do not -- refused before the job, with
    the structured daily-cap detail. At $3.70 the edits alone go over: the
    same refusal, Track A's under-cap wording."""
    story_id = _v2_story(api)
    day.add(3.50)

    response = _cast(api, story_id)

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "budget_daily_cap"
    assert detail["message"] == (
        "Today's paid spending is $3.50 of the $4.00 daily cap: this cast (est $0.60: 5 portraits $0.20 + "
        "10 sheet edits $0.40) would bring it to $4.10. Allow $0.10 more for today only, raise the daily cap in "
        "Settings, or wait for the day to reset at 00:00 UTC.")
    assert detail["errors"] == [
        "refused: est $0.600 on 5 reference images and 10 edits would bring today to $4.10 of the $4.00 daily cap"]
    assert detail["estimate"] == {"usd": 0.6, "parts": PARTS, "llm_worst_usd": 0.0}
    assert detail["needed_usd"] == 0.1
    _nothing_happened(api, day, 3.50)

    day.add(0.20)
    detail = _cast(api, story_id).json()["detail"]
    assert detail["message"] == (
        "Today's paid spending is $3.70 of the $4.00 daily cap: this cast (est $0.60: 5 portraits $0.20 + "
        "10 sheet edits $0.40) would bring it to $4.30. Allow $0.30 more for today only, raise the daily cap in "
        "Settings, or wait for the day to reset at 00:00 UTC.")
    assert detail["needed_usd"] == 0.3
    _nothing_happened(api, day, 3.70)

    # Allowed just enough for today: the same cast goes through the gate.
    day.add_extra(0.30)
    assert _cast(api, story_id).status_code == 201


def test_cast_gate_and_estimate_agree_on_combined_total(api, day):
    from clipping.aistory import workflow

    story_id = _v2_story(api)
    units = workflow.cast_units(api.store, api.store.get(story_id), selected=SKETCH)
    assert (units["images"], units["edit_images"]) == (5, 10)  # 1 portrait + 2 sheets a character

    fits = p2._estimate(api, story_id, "cast", selected=SKETCH)
    budget = _budget(api, story_id, units, env=QUALITY)
    assert fits["est_usd"] == budget["usd"] == pytest.approx(0.60)
    assert (budget["images"], budget["edits"]) == (
        {"qty": 5, "usd": 0.2, "link": PORTRAITS}, {"qty": 10, "usd": 0.4, "link": EDITS})
    assert fits["ready"] is True and budget["refusal"] is None and budget["blocks"] is False

    day.add(3.50)
    over = p2._estimate(api, story_id, "cast", selected=SKETCH)
    detail = _cast(api, story_id).json()["detail"]
    assert over["est_usd"] == detail["estimate"]["usd"] == pytest.approx(0.60)
    assert over["ready"] is False
    assert over["message"] == ("The 5 reference images and 10 edits would go over a cap, so nothing would be "
                               "generated or spent: " + detail["errors"][0] + ".")

    # Over the cap already: the estimate still prices what the gate refuses.
    day.add(5.00)
    assert p2._estimate(api, story_id, "cast", selected=SKETCH)["est_usd"] == _cast(
        api, story_id).json()["detail"]["estimate"]["usd"] == pytest.approx(0.60)


def test_v2_edit_budget_refusal_blocks_before_portraits(api, day):
    """The story's cap ($0.30) lets the portraits ($0.20) through and refuses
    the sheet edits ($0.40): today a v2 cast would buy the portraits and stop
    at the sheets; now it is refused before any portrait, in the editor's
    words (no quality link can run, the keys and allow_paid)."""
    story_id = _v2_story(api, settings=dict(QUALITY, PER_STORY_CAP_USD="0.30"))

    response = _cast(api, story_id)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    assert detail.startswith(f"No link of the quality sheet links (quality budget profile) can make a reference "
                             f"image on route auto: {EDITS}: refused: est $0.400 on {EDITS} would bring this story "
                             "to $0.40 of its $0.30 cap.")
    assert "allow paid providers" in detail and "prompt-only" not in detail
    _nothing_happened(api, day, 0.0)
    estimate = p2._estimate(api, story_id, "cast", selected=SKETCH)
    assert estimate["ready"] is False and estimate["message"] == detail
    assert estimate["est_usd"] == pytest.approx(0.20)  # the editor cannot run: its edits are not in the sum


def test_legacy_story_still_stops_and_asks_before_edits(api, day):
    """A legacy story whose paid editor the budget refuses still queues: its
    portraits are free, and the sheets stop and ask (DEC-117) when the step
    reaches them -- never a refusal before the job."""
    settings = dict(p2.PAID_EDITOR, ALLOW_PAID="1", PER_STORY_CAP_USD="0.02")  # under one $0.03 edit
    p2._settings(api, settings)
    _priced(api)
    story_id = p2._story(api.store)

    estimate = p2._estimate(api, story_id, "cast", selected=["Kiwilo", "Mangella"])
    assert estimate["ready"] is True and estimate["est_usd"] == 0.0
    assert "need an editor or prompt-only consistency, so the step stops and asks" in estimate["message"]
    assert "refused: est $0.120 on fal/seedream-4-edit would bring this story to $0.12 of its $0.02 cap" in (
        estimate["message"])

    response = p2._post_step(api, story_id, "cast", p2.CAST_PARAMS)
    assert response.status_code == 201, response.text
    job = p2._run(api, response.json()["id"], p2.FakeLLM(K1=[p2.K1_KIWI, p2.K1_MANGO, p2.K1_FIG]))
    assert job["status"] == "awaiting_approval", job.get("error")
    page = p2._page(api, story_id)
    assert page["progress"]["characters"] == {cid: {"missing": ["turnaround", "expressions"], "needs_editor": True}
                                              for cid in p2.IDS}
    assert api.fakes.paid_editor.requests == [] and day.today_total() == 0.0


def test_gate_books_nothing_and_refimages_checks_each_image(api, day):
    """The gate checks the sum and books nothing; the run checks each paid
    image again as it runs and books it once: $0.03 x 6 sheet edits."""
    from clipping.providers import gating

    p2._settings(api, dict(p2.PAID_EDITOR, ALLOW_PAID="1"))
    _priced(api)
    story_id = p2._story(api.store)
    spend_path = os.environ["SPEND_PATH"]

    response = p2._post_step(api, story_id, "cast", p2.CAST_PARAMS)

    assert response.status_code == 201, response.text
    assert not os.path.exists(spend_path)  # the gate wrote nothing

    checked = []
    real = gating.budget_check

    def counting(budget_obj, **kwargs):
        inner = real(budget_obj, **kwargs)

        def check(estimate, link):
            checked.append(round(float(getattr(estimate, "est_usd", estimate) or 0.0), 6))
            return inner(estimate, link)

        return check

    api.monkeypatch.setattr(gating, "budget_check", counting)
    job = p2._run(api, response.json()["id"], p2.FakeLLM(K1=[p2.K1_KIWI, p2.K1_MANGO, p2.K1_FIG]))

    assert job["status"] == "awaiting_approval", job.get("error")
    assert len(api.fakes.paid_editor.requests) == 6
    assert checked == [p2.SEEDREAM_PER_EDIT] * 6          # each edit checked as it runs
    with open(spend_path, encoding="utf-8") as fh:
        days = json.load(fh)["days"]
    assert days == {"2026-10-04": pytest.approx(6 * p2.SEEDREAM_PER_EDIT)}  # and booked once


def test_places_gate_unchanged(api, day):
    """The places have no edits: the sum is the plates' and props' images,
    the number the image verdict gave before, and the gate refuses exactly
    when it does -- through the same function."""
    from clipping.aistory import workflow

    story_id = _v2_story(api)
    story = api.store.get(story_id)
    params = {"places": [{"name": "La plage"}, {"name": "Le marché"}], "props": [{"name": "Le téléphone"}]}
    units = workflow.places_units(api.store, story, params)
    assert (units["images"], units["edit_images"]) == (3, 0)

    images = workflow.image_verdict(api.store, story, 3, env=QUALITY)
    budget = _budget(api, story_id, units, env=QUALITY)
    assert budget["usd"] == images["est_usd"] == pytest.approx(0.12) and budget["edits"] is None
    body = p2._estimate(api, story_id, "places", place=["La plage", "Le marché"], prop=["Le téléphone"])
    assert body["est_usd"] == pytest.approx(0.12) and body["edit"]["units"] == {"images": 0}

    api.routes._generation_gate(api.store, story, units, env=QUALITY, step="places")()  # passes

    day.add(3.90)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        api.routes._generation_gate(api.store, story, units, env=QUALITY, step="places")()
    detail = caught.value.detail
    assert detail["code"] == "budget_daily_cap"
    assert detail["estimate"]["parts"] == [{"what": "images", "qty": 3, "usd": 0.12, "link": PORTRAITS}]
    assert detail["errors"] == [f"{row['link']}: {row['reason']}"
                                for row in workflow.image_verdict(api.store, story, 3, env=QUALITY)["links"]]
