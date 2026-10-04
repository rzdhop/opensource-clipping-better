"""``clipping.aistory.day_report``: which stories spent today's paid dollars.

Pure file reads over cost ledgers written by the real ``CostLedger``; stdlib
only, so it runs in the pytest-only CI environment (DEC-012).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from clipping.aistory import day_report
from clipping.aistory.ledger import CostLedger

DAY = "2026-10-04"
NOON = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc).timestamp()
YESTERDAY = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc).timestamp()


def _ledger(tmp_path, name, rows, *, mtime=NOON):
    """A story's ledger of ``(epoch, est_usd, paid, void)`` rows, at *mtime*."""
    path = tmp_path / name / "cost_ledger.json"
    path.parent.mkdir()
    for when, usd, paid, void in rows:
        CostLedger(str(path), time_fn=lambda when=when: when).append(
            step="assets", provider="fal", model="m", unit="second", qty=1, est_usd=usd, paid=paid,
            void=void)
    os.utime(path, (mtime, mtime))
    return {"story_id": name, "title": f"Title {name}", "ledger_path": str(path)}


def test_two_stories_a_void_row_and_an_other_day_row(tmp_path):
    big = _ledger(tmp_path, "aaaaaaaaaaaa", [
        (NOON, 2.00, True, None),
        (NOON, 1.50, True, None),
        (NOON, -0.50, True, "proven never run"),   # a void: negative, cancels
        (YESTERDAY, 9.00, True, None),             # another day: not counted
        (NOON, 0.75, False, None),                 # free: not counted
    ])
    small = _ledger(tmp_path, "bbbbbbbbbbbb", [(NOON, 0.60, True, None)])
    assert day_report.day_contributors([small, big], day=DAY) == [
        {"story_id": "aaaaaaaaaaaa", "title": "Title aaaaaaaaaaaa", "usd": 3.0},
        {"story_id": "bbbbbbbbbbbb", "title": "Title bbbbbbbbbbbb", "usd": 0.6},
    ]


def test_limit_other_usd_and_a_fully_voided_story(tmp_path):
    stories = [
        _ledger(tmp_path, "aaaaaaaaaaaa", [(NOON, 3.0, True, None)]),
        _ledger(tmp_path, "bbbbbbbbbbbb", [(NOON, 2.0, True, None)]),
        _ledger(tmp_path, "cccccccccccc", [(NOON, 1.0, True, None), (NOON, -1.0, True, "unbilled")]),
    ]
    top = day_report.day_contributors(stories, day=DAY, limit=1)
    assert [c["story_id"] for c in top] == ["aaaaaaaaaaaa"]
    # The day total holds the listed story, the unlisted one and a chain test.
    assert day_report.other_usd(5.40, top) == 2.4
    assert day_report.other_usd(2.0, top) == 0.0   # never negative


def test_a_ledger_older_than_the_day_is_not_even_opened(tmp_path):
    stale = _ledger(tmp_path, "aaaaaaaaaaaa", [(NOON, 2.0, True, None)], mtime=YESTERDAY)
    assert day_report.day_contributors([stale], day=DAY) == []


def test_unreadable_or_missing_ledgers_count_for_nothing(tmp_path):
    broken = tmp_path / "bbbbbbbbbbbb"
    broken.mkdir()
    (broken / "cost_ledger.json").write_text("{not json", encoding="utf-8")
    os.utime(broken / "cost_ledger.json", (NOON, NOON))
    sources = [
        {"story_id": "bbbbbbbbbbbb", "title": "", "ledger_path": str(broken / "cost_ledger.json")},
        {"story_id": "cccccccccccc", "title": "", "ledger_path": str(tmp_path / "none" / "cost_ledger.json")},
    ]
    assert day_report.day_contributors(sources, day=DAY) == []
