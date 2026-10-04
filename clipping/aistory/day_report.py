"""Who spent today's paid dollars (plan 23, stage A3).

The day's total lives in ``spend.json`` (``providers.budget.DailySpend``); this
module says which stories it went to, from the stories' own cost ledgers
(``ledger.CostLedger``, ``cost_ledger.json``). Read-only, stdlib only, and it
never raises on a file it cannot read: a missing, half-written or foreign
ledger simply counts for nothing.

A ledger row's ``ts`` is an ISO UTC timestamp, so a row belongs to the day its
first ten characters name -- the same ``YYYY-MM-DD`` key ``spend.json`` uses. A
booking given back after the fact is a row of its own with a negative
``est_usd`` (``void`` says why), so summing the paid rows of a day lets a void
cancel the booking it mirrors when both fall on that day.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

LEDGER_FILENAME = "cost_ledger.json"


def day_start_epoch(day: str) -> float:
    """The epoch second at which UTC day *day* (``YYYY-MM-DD``) began."""
    return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def story_sources(store) -> list:
    """The ``{story_id, title, ledger_path}`` of every story of *store* (a
    ``StoryStore``) that has a folder; the input of :func:`day_contributors`."""
    out = []
    try:
        entries = store.list()
    except Exception:
        return out
    for entry in entries:
        try:
            story_id = entry["story_id"]
            folder = store.story_dir(story_id)
        except Exception:
            continue
        out.append({
            "story_id": story_id,
            "title": str(entry.get("title") or ""),
            "ledger_path": os.path.join(folder, LEDGER_FILENAME),
        })
    return out


def _paid_usd_on(path: str, day: str) -> float:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        rows = data.get("entries") or []
    except (OSError, ValueError, AttributeError):
        return 0.0
    total = 0.0
    for row in rows:
        if not isinstance(row, dict) or not row.get("paid"):
            continue
        if str(row.get("ts") or "")[:10] != day:
            continue
        try:
            total += float(row.get("est_usd") or 0.0)
        except (TypeError, ValueError):
            continue
    return round(total, 4)


def day_contributors(stories, *, day: str, limit: int = 5) -> list:
    """The stories that spent paid dollars on *day*, biggest first.

    *stories* is an iterable of ``{story_id, title, ledger_path}`` (see
    :func:`story_sources`). A ledger last written before the day began cannot
    hold a row of that day, so it is skipped on its mtime alone. A story whose
    day sums to nothing (or less, a void with no booking that day) is left
    out. Returns at most *limit* ``{story_id, title, usd}``.
    """
    began = day_start_epoch(day)
    rows = []
    for story in stories:
        path = story.get("ledger_path")
        try:
            if os.stat(path).st_mtime < began:
                continue
        except (OSError, TypeError):
            continue
        usd = _paid_usd_on(path, day)
        if usd > 0:
            rows.append({"story_id": story["story_id"], "title": str(story.get("title") or ""), "usd": usd})
    rows.sort(key=lambda r: (-r["usd"], r["story_id"]))
    return rows[: max(0, int(limit))]


def other_usd(day_total: float, contributors) -> float:
    """What the day's total holds beyond the listed stories (chain tests,
    stories past the limit, rounding). Never negative."""
    listed = sum(float(c["usd"]) for c in contributors)
    return round(max(0.0, float(day_total) - listed), 4)
