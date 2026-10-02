"""The story-level cost ledger (spec 2.11).

An append-only list of what each step charged -- estimated from the price
table at call time -- kept next to the story as ``cost_ledger.json`` and
filtered per episode into ``episodes/epNN/cost_ledger.json`` for the bundle.
Totals feed the budget caps and the story page. Stdlib only, atomic writes.

A row is never removed or repriced. The one change a row takes is its
``discarded`` mark (:meth:`CostLedger.mark_discarded`): the episode it was
spent on was archived (``StoryStore.discard_episode``, the pipeline switch's
"Regenerate on v2"), so the money still counts for the story -- it was
really spent -- but no longer for the episode written in its place: the
per-episode views (:meth:`CostLedger.totals` with an episode,
:meth:`CostLedger.episode_entries`, the bundle's view) leave it out.
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

    def append(self, *, step, provider, model, unit, qty, est_usd, paid, ep=None, note=None) -> dict:
        """One row. *note* says why a request was booked without an answer (the
        generation journal's conservative rule, DEC-153); a row has no ``note``
        key unless one is given."""
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
        if note is not None:
            entry["note"] = str(note)
        with self._lock:
            data = self._load()
            data["entries"].append(entry)
            data["updated_at"] = entry["ts"]
            self._write(self.path, data)
        return entry

    def entries(self) -> list:
        with self._lock:
            return list(self._load()["entries"])

    def episode_entries(self, ep) -> list:
        """The rows of episode *ep* as it is now: its rows, less those of an
        archived take of it (``discarded``)."""
        return [e for e in self.entries() if e.get("ep") == ep and not e.get("discarded")]

    def totals(self, ep=None) -> dict:
        """The whole ledger's totals, or -- with *ep* -- the episode's
        (:meth:`episode_entries`: what its per-episode cap is held to)."""
        rows = self.entries() if ep is None else self.episode_entries(ep)
        est = round(sum(float(e["est_usd"]) for e in rows), 4)
        paid = round(sum(float(e["est_usd"]) for e in rows if e.get("paid")), 4)
        return {"est_usd": est, "paid_usd": paid, "entries": len(rows)}

    def mark_discarded(self, ep, archive) -> int:
        """Mark every row of episode *ep* not marked yet ``"discarded":
        archive`` (the archive's folder name) -- one atomic rewrite, nothing
        else of a row changed; returns how many. A row keeps its first mark:
        a row of a later take is marked by that take's own discard."""
        marked = 0
        with self._lock:
            data = self._load()
            for entry in data["entries"]:
                if isinstance(entry, dict) and entry.get("ep") == ep and not entry.get("discarded"):
                    entry["discarded"] = str(archive)
                    marked += 1
            if marked:
                data["updated_at"] = self._now()
                self._write(self.path, data)
        return marked

    def episode_view(self, ep, out_path) -> dict:
        """Write the entries of *ep* as their own ledger file (for the episode
        bundle): :meth:`episode_entries`."""
        rows = self.episode_entries(ep)
        data = {"$schema": SCHEMA, "ep": ep, "entries": rows, "updated_at": self._now()}
        self._write(out_path, data)
        return data
