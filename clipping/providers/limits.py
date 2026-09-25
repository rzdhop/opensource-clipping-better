"""Free-tier etiquette (DEC-098): stay inside the published allowances.

Two limits per provider. **RPM** is paced with ``pacing.Limiter`` (a sliding
window that sleeps at most one window). **RPD** is a counter persisted in
``data/usage.json`` keyed by the UTC day, and it is checked *before* a call:
a spent allowance returns a reason so the chain moves to its next link,
instead of sending the request and earning the 429. The counters count free
calls only -- a paid provider has no entry here and never touches the file,
which is what makes "the free counters are untouched by a paid call" a byte
comparison.

Every number is overridable with ``LIMIT_<PROVIDER>_RPM`` / ``_RPD`` (an
OpenRouter account that ever bought $10 of credit has 1000 RPD, not 50).

Stdlib only; the clock and the sleep are injectable so tests never wait.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections import namedtuple
from datetime import datetime, timezone

from . import pacing
from .generation import GEN_PROVIDERS

Limit = namedtuple("Limit", "rpm rpd")

# Published free-tier numbers (spec 8.4, appendix D). Generation providers come
# from their table; the rest are here so the story page can show every
# allowance in one place. Providers with no free allowance (fal, openai,
# gcloud, elevenlabs) and local links have no entry: nothing to count.
EXTRA_LIMITS = {
    "groq": Limit(rpm=30, rpd=1000),
    "freesound": Limit(rpm=60, rpd=2000),
}


def published_limits() -> dict:
    table = {}
    for name, provider in GEN_PROVIDERS.items():
        if not provider.free_tier or name == "local":
            continue
        table[name] = Limit(rpm=provider.rpm, rpd=provider.rpd)
    table.update(EXTRA_LIMITS)
    return table


def limits_from_env(env=None) -> dict:
    """The published table with ``LIMIT_<PROVIDER>_RPM`` / ``_RPD`` overrides applied."""
    env = os.environ if env is None else env
    table = published_limits()
    for name, limit in list(table.items()):
        rpm, rpd = limit
        for field, value in (("RPM", rpm), ("RPD", rpd)):
            raw = (env.get(f"LIMIT_{name.upper()}_{field}") or "").strip()
            if raw:
                try:
                    value = int(raw)
                except ValueError:
                    continue
                if field == "RPM":
                    rpm = value
                else:
                    rpd = value
        table[name] = Limit(rpm=rpm, rpd=rpd)
    return table


# ---------------------------------------------------------- daily counters

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def default_usage_path() -> str:
    return os.environ.get("USAGE_PATH") or os.path.join(_ROOT, "data", "usage.json")


class DailyUsage:
    """The per-provider call counters of the current UTC day, on disk."""

    SCHEMA = "usage_v1"

    def __init__(self, path, *, time_fn=None):
        self.path = path
        self._time = time_fn or time.time
        self._lock = threading.Lock()

    def today(self) -> str:
        return datetime.fromtimestamp(self._time(), tz=timezone.utc).strftime("%Y-%m-%d")

    def _empty(self) -> dict:
        return {"$schema": self.SCHEMA, "day": self.today(), "providers": {}}

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return self._empty()
        if not isinstance(data, dict) or data.get("day") != self.today():
            return self._empty()
        data.setdefault("providers", {})
        return data

    def _write(self, data: dict) -> None:
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=directory, prefix=".usage-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def snapshot(self) -> dict:
        with self._lock:
            return self._load()

    def calls(self, provider: str) -> int:
        return int(self.snapshot()["providers"].get(provider, {}).get("calls", 0))

    def try_increment(self, provider: str, rpd):
        """Count one call unless the day's allowance is spent. Returns ``(ok, calls)``."""
        with self._lock:
            data = self._load()
            entry = data["providers"].setdefault(provider, {"calls": 0})
            calls = int(entry.get("calls", 0))
            if rpd is not None and calls >= rpd:
                return False, calls
            entry["calls"] = calls + 1
            data["updated_at"] = datetime.fromtimestamp(self._time(), tz=timezone.utc).isoformat()
            self._write(data)
            return True, calls + 1


_DEFAULT_USAGE = None
_DEFAULT_USAGE_LOCK = threading.Lock()


def default_usage() -> DailyUsage:
    global _DEFAULT_USAGE
    with _DEFAULT_USAGE_LOCK:
        path = default_usage_path()
        if _DEFAULT_USAGE is None or _DEFAULT_USAGE.path != path:
            _DEFAULT_USAGE = DailyUsage(path)
        return _DEFAULT_USAGE


# ------------------------------------------------------------- RPM pacing

# One sliding-window limiter per provider, built with indirections so the
# clock and the sleep of the CURRENT call are used -- a cached limiter with
# the first caller's fake clock would spin forever under a later caller.
_RPM = {}
_RPM_LOCK = threading.Lock()
_CALL = threading.local()


def _current_time():
    return (getattr(_CALL, "time_fn", None) or time.monotonic)()


def _current_sleep(seconds):
    (getattr(_CALL, "sleep_fn", None) or time.sleep)(seconds)


def _rpm_limiter(name: str, rpm: int) -> pacing.Limiter:
    with _RPM_LOCK:
        limiter = _RPM.get(name)
        if limiter is None or limiter.rpm != rpm:
            limiter = pacing.Limiter(name, rpm=rpm, time_fn=_current_time, sleep_fn=_current_sleep)
            _RPM[name] = limiter
        return limiter


def reset() -> None:
    """Drop the cached limiters and the default usage store. For tests."""
    global _DEFAULT_USAGE
    with _RPM_LOCK:
        _RPM.clear()
    with _DEFAULT_USAGE_LOCK:
        _DEFAULT_USAGE = None


# ------------------------------------------------------------------ public

def acquire(provider: str, *, usage=None, limits=None, time_fn=None, sleep_fn=None):
    """Take one call on *provider*'s free allowance.

    Returns ``None`` when the call may go out (after pacing the RPM), or a
    printable reason when the day's allowance is spent. A provider without an
    entry in the table -- paid, or local -- is neither counted nor paced.
    """
    table = limits_from_env() if limits is None else limits
    limit = table.get(provider)
    if limit is None:
        return None
    store = usage or default_usage()
    ok, calls = store.try_increment(provider, limit.rpd)
    if not ok:
        return f"daily allowance spent ({calls}/{limit.rpd} today, resets at 00:00 UTC)"
    if limit.rpm:
        _CALL.time_fn = time_fn
        _CALL.sleep_fn = sleep_fn
        try:
            _rpm_limiter(provider, limit.rpm).acquire()
        finally:
            _CALL.time_fn = None
            _CALL.sleep_fn = None
    return None


def budget_left_today(*, usage=None, limits=None) -> dict:
    """``{provider: {calls, rpd, left, rpm}}`` for every provider with a daily limit, plus ``day``."""
    table = limits_from_env() if limits is None else limits
    store = usage or default_usage()
    data = store.snapshot()
    out = {"day": data["day"]}
    for name, limit in sorted(table.items()):
        if limit.rpd is None:
            continue
        calls = int(data["providers"].get(name, {}).get("calls", 0))
        out[name] = {"calls": calls, "rpd": limit.rpd, "left": max(0, limit.rpd - calls), "rpm": limit.rpm}
    return out
