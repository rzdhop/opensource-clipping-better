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
