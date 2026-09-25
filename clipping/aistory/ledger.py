"""The story-level cost ledger (spec 2.11).

An append-only list of what each step charged -- estimated from the price
table at call time -- kept next to the story as ``cost_ledger.json`` and
filtered per episode into ``episodes/epNN/cost_ledger.json`` for the bundle.
Totals feed the budget caps and the story page. Stdlib only, atomic writes.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone

SCHEMA = "cost_ledger_v1"
UNITS = ("image", "second", "char", "token")


class CostLedger:
    def __init__(self, path, *, time_fn=None):
        self.path = path
        self._time = time_fn or time.time
        self._lock = threading.Lock()

    # ------------------------------------------------------------ storage

    def _now(self) -> str:
        return datetime.fromtimestamp(self._time(), tz=timezone.utc).isoformat()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict) or data.get("$schema") != SCHEMA:
            data = {"$schema": SCHEMA, "entries": []}
        data.setdefault("entries", [])
        return data

    @staticmethod
    def _write(path: str, data: dict) -> None:
        directory = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(directory, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=directory, prefix=".ledger-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------- public

    def append(self, *, step, provider, model, unit, qty, est_usd, paid, ep=None) -> dict:
        if unit not in UNITS:
            raise ValueError(f"unit must be one of {', '.join(UNITS)}, not {unit!r}")
        entry = {
            "ts": self._now(),
            "ep": ep,
            "step": str(step),
            "provider": str(provider),
            "model": str(model),
            "unit": unit,
            "qty": qty,
            "est_usd": round(float(est_usd), 4),
            "paid": bool(paid),
        }
        with self._lock:
            data = self._load()
            data["entries"].append(entry)
            data["updated_at"] = entry["ts"]
            self._write(self.path, data)
        return entry

    def entries(self) -> list:
        with self._lock:
            return list(self._load()["entries"])

    def totals(self, ep=None) -> dict:
        rows = [e for e in self.entries() if ep is None or e.get("ep") == ep]
        est = round(sum(float(e["est_usd"]) for e in rows), 4)
        paid = round(sum(float(e["est_usd"]) for e in rows if e.get("paid")), 4)
        return {"est_usd": est, "paid_usd": paid, "entries": len(rows)}

    def episode_view(self, ep, out_path) -> dict:
        """Write the entries of *ep* as their own ledger file (for the episode bundle)."""
        rows = [e for e in self.entries() if e.get("ep") == ep]
        data = {"$schema": SCHEMA, "ep": ep, "entries": rows, "updated_at": self._now()}
        self._write(out_path, data)
        return data
