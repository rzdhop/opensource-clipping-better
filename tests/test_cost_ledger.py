"""The story-level cost ledger (spec 2.11): append-only entries with the
estimate that was charged, totals per episode, an episode view for the bundle."""

import json
import os
import threading

from clipping.aistory.ledger import CostLedger


class Clock:
    def __init__(self):
        self.now = 1_790_000_000.0

    def __call__(self):
        return self.now


def test_entries_are_appended_in_order_with_a_timestamp(tmp_path):
    ledger = CostLedger(str(tmp_path / "cost_ledger.json"), time_fn=Clock())
    first = ledger.append(step="cast", provider="fal", model="fal-ai/bytedance/seedream/v4/edit",
                          unit="image", qty=3, est_usd=0.09, paid=True)
    ledger.append(step="script", provider="gemini", model="gemini-3.5-flash-lite", unit="token",
                  qty=900, est_usd=0.0, paid=False, ep=1)
    assert first["ts"].startswith("2026-")
    entries = ledger.entries()
    assert [e["step"] for e in entries] == ["cast", "script"]
    assert entries[0]["ep"] is None and entries[1]["ep"] == 1
    on_disk = json.loads((tmp_path / "cost_ledger.json").read_text(encoding="utf-8"))
    assert on_disk["$schema"] == "cost_ledger_v1"
    assert "updated_at" in on_disk
    assert sorted(os.listdir(tmp_path)) == ["cost_ledger.json"]


def test_totals_per_story_and_per_episode(tmp_path):
    ledger = CostLedger(str(tmp_path / "cost_ledger.json"))
    ledger.append(step="cast", provider="fal", model="m", unit="image", qty=3, est_usd=0.09, paid=True)
    ledger.append(step="assets", provider="fal", model="m", unit="image", qty=20, est_usd=0.60, paid=True, ep=1)
    ledger.append(step="assets", provider="edge", model="v", unit="char", qty=800, est_usd=0.0, paid=False, ep=1)
    ledger.append(step="assets", provider="fal", model="m", unit="image", qty=10, est_usd=0.30, paid=True, ep=2)
    assert ledger.totals() == {"est_usd": 0.99, "paid_usd": 0.99, "entries": 4}
    assert ledger.totals(ep=1) == {"est_usd": 0.60, "paid_usd": 0.60, "entries": 2}
    assert ledger.totals(ep=3) == {"est_usd": 0.0, "paid_usd": 0.0, "entries": 0}


def test_the_episode_view_is_a_filtered_copy(tmp_path):
    ledger = CostLedger(str(tmp_path / "cost_ledger.json"))
    ledger.append(step="cast", provider="fal", model="m", unit="image", qty=1, est_usd=0.03, paid=True)
    ledger.append(step="assets", provider="fal", model="m", unit="image", qty=1, est_usd=0.03, paid=True, ep=1)
    out = tmp_path / "episodes" / "ep01" / "cost_ledger.json"
    ledger.episode_view(1, str(out))
    view = json.loads(out.read_text(encoding="utf-8"))
    assert view["$schema"] == "cost_ledger_v1" and view["ep"] == 1
    assert [e["step"] for e in view["entries"]] == ["assets"]


def test_a_missing_file_reads_as_empty_and_appends_are_thread_safe(tmp_path):
    ledger = CostLedger(str(tmp_path / "cost_ledger.json"))
    assert ledger.entries() == [] and ledger.totals()["entries"] == 0
    threads = [threading.Thread(target=ledger.append, kwargs=dict(
        step="x", provider="p", model="m", unit="image", qty=1, est_usd=0.01, paid=True)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert ledger.totals()["entries"] == 8


# A discarded episode (the pipeline switch's "Regenerate on v2", 2026-10-02):
# its money was really spent -- the story total keeps it -- but it no longer
# counts toward the per-episode cap of the episode written in its place.

def _discarded_episode(tmp_path):
    ledger = CostLedger(str(tmp_path / "cost_ledger.json"))
    ledger.append(step="cast", provider="fal", model="m", unit="image", qty=3, est_usd=0.09, paid=True)
    ledger.append(step="assets", provider="fal", model="m", unit="image", qty=20, est_usd=0.60, paid=True, ep=1)
    ledger.append(step="clips", provider="fal", model="v", unit="second", qty=30, est_usd=1.20, paid=True, ep=1)
    ledger.append(step="assets", provider="fal", model="m", unit="image", qty=10, est_usd=0.30, paid=True, ep=2)
    return ledger


def test_marking_an_episode_discarded_keeps_the_story_total_and_empties_the_episode(tmp_path):
    ledger = _discarded_episode(tmp_path)

    assert ledger.mark_discarded(1, "ep01-20261002T110000Z") == 2
    assert ledger.mark_discarded(1, "ep01-later") == 0  # a row is marked once, by its first archive

    assert ledger.totals() == {"est_usd": 2.19, "paid_usd": 2.19, "entries": 4}
    assert ledger.totals(ep=1) == {"est_usd": 0.0, "paid_usd": 0.0, "entries": 0}
    assert ledger.totals(ep=2) == {"est_usd": 0.30, "paid_usd": 0.30, "entries": 1}
    rows = ledger.entries()
    assert [row.get("discarded") for row in rows] == [None, "ep01-20261002T110000Z", "ep01-20261002T110000Z", None]
    # The rows are kept whole: still ep 1, still their price.
    assert [row["ep"] for row in rows] == [None, 1, 1, 2]
    # A new row of episode 1 counts for the new episode 1.
    ledger.append(step="assets", provider="fal", model="m", unit="image", qty=1, est_usd=0.03, paid=True, ep=1)
    assert ledger.totals(ep=1) == {"est_usd": 0.03, "paid_usd": 0.03, "entries": 1}
    assert [row["est_usd"] for row in ledger.episode_entries(1)] == [0.03]


def test_the_episode_view_leaves_the_discarded_rows_out(tmp_path):
    ledger = _discarded_episode(tmp_path)
    ledger.mark_discarded(1, "ep01-20261002T110000Z")
    out = tmp_path / "episodes" / "ep01" / "cost_ledger.json"
    assert ledger.episode_view(1, str(out))["entries"] == []


def test_the_per_episode_cap_no_longer_counts_a_discarded_episode_but_the_story_cap_does(tmp_path):
    from clipping.providers import budget as budget_mod

    ledger = _discarded_episode(tmp_path)
    caps = budget_mod.Budget(allow_paid=True, per_episode_cap_usd=1.50, daily_cap_usd=10.0, per_story_cap_usd=2.50,
                             profile="quality")

    def check(usd, day_spent=0.0):
        budget_mod.check(usd, budget=caps, day_spent=day_spent, ep_spent=ledger.totals(ep=1)["est_usd"],
                         story_spent=ledger.totals()["est_usd"])

    try:
        check(0.40)
    except budget_mod.BudgetRefused as exc:
        assert "this episode" in str(exc)
    else:
        raise AssertionError("$1.80 of episode 1 already spent: $0.40 more must be refused by the episode cap")

    ledger.mark_discarded(1, "ep01-20261002T110000Z")
    check(0.20)  # the new episode 1 starts at $0
    try:
        check(0.40)
    except budget_mod.BudgetRefused as exc:
        assert "this story" in str(exc)  # $2.19 + $0.40 is over the story's $2.50: still counted there
    else:
        raise AssertionError("the discarded episode's money still counts toward the story cap")
    try:
        check(0.20, day_spent=9.90)
    except budget_mod.BudgetRefused as exc:
        assert "today" in str(exc)  # the daily spend is its own file: marking changes nothing there
    else:
        raise AssertionError("the daily cap is unchanged by a discard")
