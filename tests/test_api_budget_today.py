"""The override API (plan 23, stage A3): ``web/api/routes/budget.py``.

A throwaway app with the budget and settings routers (and, for the cross-site
case, the real ``CrossSiteWriteGuard``), ``spend.json`` and the stories root
under ``tmp_path``, and the day frozen at 2026-10-04 12:00 UTC so a run near
midnight cannot straddle two days. Needs pydantic, fastapi and httpx; skips
without them, like the other route tests. No token exists in this API
(DEC-173): the limits are the ceiling, the day's expiry and the cross-site guard.
"""

from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOON = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc).timestamp()
YESTERDAY = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc).timestamp()
DAY = "2026-10-04"


def test_the_budget_router_is_included_before_the_dashboard_mount():
    text = (ROOT / "web" / "api" / "app.py").read_text(encoding="utf-8")
    assert "app.include_router(budget.router)" in text
    assert text.index("app.include_router(budget.router)") < text.index("app.mount(")


@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipping.aistory.store import StoryStore
    from clipping.providers import budget
    from web.api import auth, worker
    from web.api.routes import budget as budget_route
    from web.api.routes import settings as settings_route

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "data" / "spend.json"))
    monkeypatch.setenv("USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("DISABLE_AUTH", "1")
    for name in ("DAILY_CAP_USD", "ALLOW_PAID", "API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(worker, "_settings_env", {"DAILY_CAP_USD": "4.00"})
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(worker, "OUTPUTS_ROOT", str(outputs))
    budget.reset()
    spend = budget.DailySpend(str(tmp_path / "data" / "spend.json"), time_fn=lambda: NOON)
    monkeypatch.setattr(budget, "_DEFAULT_SPEND", spend)

    app = FastAPI()
    app.add_middleware(auth.CrossSiteWriteGuard, allowed_origins=())
    app.include_router(budget_route.router)
    app.include_router(settings_route.router)
    stories = StoryStore(str(outputs))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, spend=spend, stories=stories, tmp_path=tmp_path,
                              outputs=outputs, worker=worker, monkeypatch=monkeypatch)
    budget.reset()


def _story(api, title):
    doc = api.stories.create(language="fr", now="2026-10-04T10:00:00+00:00")
    api.stories.update(doc["story_id"], lambda d: d.__setitem__("title", title),
                       now="2026-10-04T10:00:01+00:00")
    return doc["story_id"]


def _book(api, story_id, usd, *, when=NOON, void=None):
    from clipping.aistory.ledger import CostLedger

    path = os.path.join(api.stories.story_dir(story_id), "cost_ledger.json")
    CostLedger(path, time_fn=lambda: when).append(
        step="assets", provider="fal", model="m", unit="second", qty=1, est_usd=usd, paid=True, void=void)
    os.utime(path, (NOON, NOON))


def test_today_reports_spend_extra_cap_and_contributors(api):
    cast = _story(api, "The cast")
    sheet = _story(api, "The sheet")
    _book(api, cast, 5.00)
    _book(api, cast, 1.00)
    _book(api, cast, -0.50, void="unbilled")
    _book(api, sheet, 2.00)
    api.spend.add(8.38)          # 7.50 on the two stories, 0.88 of chain tests
    api.spend.add_extra(1.00, story_id=cast, note="earlier")

    response = api.client.get("/api/budget/today")
    assert response.status_code == 200, response.text
    body = response.json()
    grants = body.pop("grants_today")
    assert body == {
        "day": DAY,
        "zone": "UTC",
        "zone_error": None,
        "spent_usd": 8.38,
        "extra_usd": 1.0,
        "daily_cap_usd": 4.0,
        "effective_cap_usd": 5.0,
        "cap_below_spend": True,
        "stories": [
            {"story_id": cast, "title": "The cast", "usd": 5.5},
            {"story_id": sheet, "title": "The sheet", "usd": 2.0},
        ],
        "other_usd": 0.88,
        "resets_at": "2026-10-05T00:00:00+00:00",
    }
    assert [(g["usd"], g["story_id"], g["note"], g["day"]) for g in grants] == [(1.0, cast, "earlier", DAY)]


def test_allow_extra_adds_to_today_only(api):
    api.spend.add(3.70)
    first = api.client.post("/api/budget/today/extra", json={"usd": 2.5, "note": "the cast"})
    assert first.status_code == 200, first.text
    assert first.json()["extra_usd"] == 2.5 and first.json()["effective_cap_usd"] == 6.5
    second = api.client.post("/api/budget/today/extra", json={"usd": 1.0})
    assert second.json()["extra_usd"] == 3.5
    assert [g["usd"] for g in second.json()["grants_today"]] == [2.5, 1.0]
    # The extra is keyed by day: tomorrow starts with none, and the file keeps the grants.
    api.spend._time = lambda: NOON + 86400
    tomorrow = api.client.get("/api/budget/today").json()
    assert tomorrow["day"] == "2026-10-05" and tomorrow["extra_usd"] == 0.0
    assert tomorrow["effective_cap_usd"] == 4.0 and tomorrow["grants_today"] == []
    assert len(json.loads(pathlib.Path(api.spend.path).read_text())["grants"]) == 2


@pytest.mark.parametrize("body", [
    {"usd": 0},
    {"usd": -1},
    {"usd": 25.01},
    {"usd": 1, "story_id": "0123456789ab"},        # well-formed, no such story
    {"usd": 1, "story_id": "../etc"},
    {"usd": 1, "note": "x" * 201},
    {"usd": 1, "estimate_usd": -0.1},
])
def test_allow_extra_refuses_zero_negative_and_over_ceiling(api, body):
    response = api.client.post("/api/budget/today/extra", json=body)
    assert response.status_code == 400, response.text
    assert api.client.get("/api/budget/today").json()["extra_usd"] == 0.0
    assert not pathlib.Path(api.spend.path).exists()   # nothing was written


def test_the_ceiling_counts_the_whole_day_and_says_so(api):
    assert api.client.post("/api/budget/today/extra", json={"usd": 20}).status_code == 200
    response = api.client.post("/api/budget/today/extra", json={"usd": 6})
    assert response.status_code == 400
    assert "ceiling" in response.json()["detail"]
    assert api.client.get("/api/budget/today").json()["extra_usd"] == 20.0


def test_allow_extra_writes_the_story_activity_log(api, capsys):
    story_id = _story(api, "The cast")
    api.spend.add(8.38)
    response = api.client.post("/api/budget/today/extra",
                               json={"usd": 4.98, "story_id": story_id, "note": "the cast of e741"})
    assert response.status_code == 200, response.text
    log = (api.outputs / "stories" / story_id / "activity.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1
    stamp, _, rest = log[0].partition(" ")
    datetime.fromisoformat(stamp)       # the line starts with an ISO timestamp
    assert rest == ("[budget] allowed $4.98 more for today (2026-10-04 UTC): the cast of e741; "
                    "today $8.38 of $4.00")
    out = capsys.readouterr().out
    assert "[budget] allowed $4.98 more for 2026-10-04 UTC" in out
    grant = json.loads(pathlib.Path(api.spend.path).read_text())["grants"][-1]
    assert (grant["usd"], grant["story_id"], grant["note"]) == (4.98, story_id, "the cast of e741")


def test_a_grant_without_a_story_writes_no_activity_log(api):
    story_id = _story(api, "The cast")
    assert api.client.post("/api/budget/today/extra", json={"usd": 1}).status_code == 200
    assert not (api.outputs / "stories" / story_id / "activity.log").exists()


def test_clear_extra_returns_to_the_saved_cap(api):
    api.spend.add(3.0)
    api.client.post("/api/budget/today/extra", json={"usd": 5})
    cleared = api.client.delete("/api/budget/today/extra")
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert (body["extra_usd"], body["effective_cap_usd"], body["daily_cap_usd"]) == (0.0, 4.0, 4.0)
    assert api.client.delete("/api/budget/today/extra").json()["extra_usd"] == 0.0   # idempotent
    from clipping.providers import budget

    assert budget.day_state(spend=api.spend).extra == 0.0


def test_cross_site_post_is_refused_while_auth_is_off(api):
    for method in ("post", "delete"):
        kwargs = {"json": {"usd": 1}} if method == "post" else {}
        response = getattr(api.client, method)("/api/budget/today/extra",
                                               headers={"Sec-Fetch-Site": "cross-site"}, **kwargs)
        assert response.status_code == 403, method
    assert api.client.get("/api/budget/today").json()["extra_usd"] == 0.0
    # The dashboard (same-origin) and curl (no header) still write.
    for headers in ({"Sec-Fetch-Site": "same-origin"}, {}):
        assert api.client.post("/api/budget/today/extra", json={"usd": 1}, headers=headers).status_code == 200


def test_settings_get_flags_cap_below_spend(api):
    story_id = _story(api, "The cast")
    _book(api, story_id, 8.38)
    api.spend.add(8.38)
    below = api.client.get("/api/settings").json()
    assert below["daily_cap_below_spend"] is True
    assert below["spend_day"] == DAY and below["spend_zone"] == "UTC"
    assert below["day_extra_usd"] == 0.0
    assert below["day_contributors"] == [{"story_id": story_id, "title": "The cast", "usd": 8.38}]
    assert below["spend_today_usd"] == 8.38

    api.client.post("/api/budget/today/extra", json={"usd": 4.98})
    assert api.client.get("/api/settings").json()["day_extra_usd"] == 4.98

    api.worker._settings_env["DAILY_CAP_USD"] = "20"
    assert api.client.get("/api/settings").json()["daily_cap_below_spend"] is False


def test_contributors_ignore_rows_from_other_days(api):
    story_id = _story(api, "The cast")
    _book(api, story_id, 7.00, when=YESTERDAY)
    _book(api, story_id, 1.25)
    api.spend.add(1.25)
    body = api.client.get("/api/budget/today").json()
    assert body["stories"] == [{"story_id": story_id, "title": "The cast", "usd": 1.25}]
    assert body["other_usd"] == 0.0
