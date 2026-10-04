"""The budget day resets at the human's midnight (plan 23, stage A7).

``BUDGET_TIMEZONE`` (an IANA name; UTC when unset, empty or invalid) decides
which day a paid dollar belongs to: ``DailySpend.today()``, a release's day
(``budget.day_key_at`` of the booking), the clips cache's day, today's
contributors and the next reset. ``spend.json`` is not migrated: the first
write under a new zone records it once and leaves the stored days alone.
Stdlib + pytest (DEC-012); the zone files come from the OS or the ``tzdata``
wheel.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from clipping.providers import budget

ROOT = pathlib.Path(__file__).resolve().parents[1]
PARIS = "Europe/Paris"
# 22:30 UTC on 4 Oct 2026 is 00:30 on 5 Oct in Paris (CEST, UTC+2).
LATE = datetime(2026, 10, 4, 22, 30, tzinfo=timezone.utc).timestamp()


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def _paris(monkeypatch=None, *, name=PARIS):
    """Configure the zone through a settings reader, as the worker and the CLI do."""
    budget.set_settings_reader(lambda: {budget.TIMEZONE_ENV: name})


# ------------------------------------------------------------- the default

def test_no_setting_means_the_utc_day(tmp_path):
    spend = budget.DailySpend(str(tmp_path / "spend.json"), time_fn=lambda: LATE)
    assert budget.zone_setting() == ""
    assert budget.zone_name() == "UTC" and budget.zone_error is None
    assert spend.today() == "2026-10-04"
    assert spend.next_reset() == "2026-10-05T00:00:00+00:00"
    spend.add(0.5)
    assert budget.day_state(spend=spend) == budget.DayState("2026-10-04", "UTC", 0.5, 0.0)
    on_disk = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))
    assert "zone" not in on_disk and "zone_changes" not in on_disk   # a UTC write is byte-for-byte as before


def test_an_empty_setting_and_utc_are_the_utc_day():
    for name in ("", "  ", "UTC"):
        _paris(name=name)
        assert budget.zone_name() == "UTC" and budget.day_zone() is timezone.utc and budget.zone_error is None


# ------------------------------------------------------------ a local day

def test_paris_midnight_starts_the_next_day(tmp_path):
    _paris()
    spend = budget.DailySpend(str(tmp_path / "spend.json"), time_fn=lambda: LATE)
    assert budget.zone_name() == PARIS
    assert spend.today() == "2026-10-05"
    assert budget.day_state(spend=spend).zone == PARIS
    assert budget.day_state(spend=spend).day == "2026-10-05"


def test_next_reset_is_the_next_local_midnight_in_paris(tmp_path):
    _paris()
    assert budget.DailySpend("x", time_fn=lambda: LATE).next_reset() == "2026-10-06T00:00:00+02:00"
    noon = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc).timestamp()
    spend = budget.DailySpend(str(tmp_path / "spend.json"), time_fn=lambda: noon)
    assert budget.next_reset(spend=spend) == "2026-10-05T00:00:00+02:00"
    assert budget.today(spend=spend) == "2026-10-04"
    # After the clocks go back (25 Oct) the offset is +01:00.
    later = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc).timestamp()
    assert budget.DailySpend("x", time_fn=lambda: later).next_reset() == "2026-11-03T00:00:00+01:00"


def test_dst_change_day_keys_are_contiguous():
    """25 Oct 2026, Paris: 03:00 CEST becomes 02:00 CET, so that day lasts
    25 hours; every half hour across it maps to one day, in order, with no
    gap and no day twice."""
    _paris()
    start = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)
    keys = [budget.day_key_at((start + timedelta(minutes=30 * i)).timestamp()) for i in range(70)]
    runs = [key for i, key in enumerate(keys) if i == 0 or keys[i - 1] != key]
    assert runs == ["2026-10-24", "2026-10-25", "2026-10-26"]
    assert keys.count("2026-10-25") == 50                     # 25 hours of half hours
    assert budget.day_key_at("2026-10-24T21:59:59+00:00") == "2026-10-24"
    assert budget.day_key_at("2026-10-24T22:00:00+00:00") == "2026-10-25"   # 00:00 CEST
    assert budget.day_key_at("2026-10-25T22:59:59+00:00") == "2026-10-25"
    assert budget.day_key_at("2026-10-25T23:00:00+00:00") == "2026-10-26"   # 00:00 CET
    assert budget.day_began_at("2026-10-26") - budget.day_began_at("2026-10-25") == 25 * 3600
    on_the_day = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc).timestamp()
    assert budget.DailySpend("x", time_fn=lambda: on_the_day).next_reset() == "2026-10-26T00:00:00+01:00"


def test_day_key_at_reads_an_iso_timestamp_and_an_epoch():
    _paris()
    assert budget.day_key_at("2026-10-04T22:30:00+00:00") == "2026-10-05"
    assert budget.day_key_at("2026-10-04T22:30:00Z") == "2026-10-05"
    assert budget.day_key_at("2026-10-04T22:30:00") == "2026-10-05"          # naive = UTC
    assert budget.day_key_at("2026-10-05T00:30:00+02:00") == "2026-10-05"
    assert budget.day_key_at(LATE) == "2026-10-05"
    assert budget.day_key_at(int(LATE)) == "2026-10-05"
    for nothing in (None, "", "   ", "not a time", True):
        assert budget.day_key_at(nothing) is None
    budget.set_settings_reader(None)
    assert budget.day_key_at(LATE) == "2026-10-04"                           # UTC again


# ------------------------------------------------------- the invalid zone

def test_an_invalid_zone_falls_back_to_utc_and_says_why(tmp_path, capsys):
    _paris(name="Mars/Olympus")
    spend = budget.DailySpend(str(tmp_path / "spend.json"), time_fn=lambda: LATE)
    assert spend.today() == "2026-10-04"
    assert budget.zone_name() == "UTC" and budget.day_zone() is timezone.utc
    assert budget.zone_error.startswith(
        "BUDGET_TIMEZONE must be an IANA time zone such as Europe/Paris, not 'Mars/Olympus'")
    assert "[budget] BUDGET_TIMEZONE must be" in capsys.readouterr().out
    # Fixed: the error goes, the zone applies.
    _paris()
    assert budget.zone_name() == PARIS and budget.zone_error is None
    budget.reset()
    assert budget.zone_error is None and budget.zone_name() == "UTC"


@pytest.mark.parametrize("bad", ["Mars/Olympus", "../../etc/passwd", "/etc/localtime", "Europe/", "paris"])
def test_check_zone_name_refuses_what_zoneinfo_does_not_know(bad):
    with pytest.raises(ValueError) as excinfo:
        budget.check_zone_name(bad)
    assert str(excinfo.value) == f"BUDGET_TIMEZONE must be an IANA time zone such as Europe/Paris, not {bad!r}"


def test_check_zone_name_keeps_a_known_zone_and_clears_an_empty_one():
    assert budget.check_zone_name(" Europe/Paris ") == PARIS
    assert budget.check_zone_name("America/New_York") == "America/New_York"
    assert budget.check_zone_name("") == "" and budget.check_zone_name(None) == ""


# ------------------------------------------------------- where it is read

def test_the_settings_reader_wins_over_the_process_environment(monkeypatch):
    monkeypatch.setenv(budget.TIMEZONE_ENV, "America/New_York")
    assert budget.zone_name() == "America/New_York"         # no reader: the process env
    budget.set_settings_reader(lambda: {budget.TIMEZONE_ENV: PARIS})
    assert budget.zone_name() == PARIS                        # the Settings value
    budget.set_settings_reader(lambda: {})
    assert budget.zone_name() == "America/New_York"         # a Settings without it: the process env
    budget.set_settings_reader(lambda: 1 / 0)
    assert budget.zone_name() == "America/New_York"         # a broken reader never stops a booking


def test_the_zone_is_resolved_once_per_value(monkeypatch):
    built = []
    real = budget.ZoneInfo

    def counting(name):
        built.append(name)
        return real(name)

    monkeypatch.setattr(budget, "ZoneInfo", counting)
    _paris()
    for _ in range(5):
        budget.day_zone()
    assert built == [PARIS, PARIS]       # check_zone_name's probe + the zone itself, once


# ---------------------------------------------------------------- releases

def test_a_release_gives_back_to_the_configured_zone_s_day_of_the_booking(tmp_path):
    _paris()
    clock = Clock(LATE)
    spend = budget.DailySpend(str(tmp_path / "spend.json"), time_fn=clock)
    booked_at = datetime.fromtimestamp(LATE, tz=timezone.utc).isoformat()  # gencache's "at": UTC ISO
    budget.record(0.50, spend=spend)
    clock.now += 86_400                                       # the next Paris day
    budget.record(0.20, spend=spend)
    assert budget.release(0.30, day=budget.day_key_at(booked_at), spend=spend) == 0.2
    days = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))["days"]
    assert days == {"2026-10-05": 0.2, "2026-10-06": 0.2}    # the UTC date (10-04) was never touched


def test_the_voice_releaser_keys_the_release_by_the_zone_day(tmp_path, monkeypatch):
    from clipping.aistory import voices

    _paris()
    clock = Clock(LATE)
    spend = budget.DailySpend(str(tmp_path / "spend.json"), time_fn=clock)
    monkeypatch.setattr(budget, "_DEFAULT_SPEND", spend)
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "spend.json"))
    monkeypatch.setattr(voices.gating, "api_model_id", lambda kind, link: link.model)
    rows = []
    gates = voices.LineGates.__new__(voices.LineGates)
    gates.ledger = type("Ledger", (), {"append": lambda self, **row: rows.append(row)})()
    gates.ep = 1
    budget.record(0.50)
    clock.now += 86_400
    budget.record(0.10)

    release = gates.releaser("tts", step="voices", unit="char", qty=100)
    release({"link": "elevenlabs/v1", "paid": True, "est_usd": 0.30,
             "booked": {"at": datetime.fromtimestamp(LATE, tz=timezone.utc).isoformat()}})

    days = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))["days"]
    assert days == {"2026-10-05": 0.2, "2026-10-06": 0.1}
    assert rows[0]["est_usd"] == -0.30
    # A journal entry without a booking stamp gives back to today.
    release({"link": "elevenlabs/v1", "paid": True, "est_usd": 0.05, "booked": {}})
    assert json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))["days"]["2026-10-06"] == 0.05


def test_voices_no_longer_keys_a_release_by_the_utc_date():
    src = read("clipping/aistory/voices.py")
    assert "booked_at[:10]" not in src
    assert "budget_mod.release(est, day=budget_mod.day_key_at(booked_at))" in src


# ------------------------------------------------------ the zone change

def test_a_zone_change_is_recorded_once_and_history_is_untouched(tmp_path):
    path = tmp_path / "spend.json"
    utc_clock = Clock(datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc).timestamp())
    before = budget.DailySpend(str(path), time_fn=utc_clock)
    before.add(1.25)
    before.add_extra(2.0)
    history = json.loads(path.read_text(encoding="utf-8"))
    assert history["zone"] == "UTC" and "zone_changes" not in history

    _paris()
    spend = budget.DailySpend(str(path), time_fn=lambda: LATE)
    spend.add(0.40)
    spend.add(0.10)
    spend.release(0.05)
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["zone"] == PARIS
    assert len(after["zone_changes"]) == 1
    change = after["zone_changes"][0]
    assert (change["from"], change["to"]) == ("UTC", PARIS) and change["at"].startswith("2026-10-04T22:30")
    assert after["days"] == {"2026-10-03": 1.25, "2026-10-05": 0.45}   # the stored day kept its total
    assert after["extra"] == history["extra"] and after["grants"] == history["grants"]

    budget.set_settings_reader(None)                          # back to UTC: one more entry
    budget.DailySpend(str(path), time_fn=lambda: LATE).add(0.01)
    back = json.loads(path.read_text(encoding="utf-8"))
    assert back["zone"] == "UTC"
    assert [(c["from"], c["to"]) for c in back["zone_changes"]] == [("UTC", PARIS), (PARIS, "UTC")]


def test_a_file_without_a_zone_is_taken_as_utc(tmp_path):
    path = tmp_path / "spend.json"
    path.write_text(json.dumps({"$schema": "spend_v1", "days": {"2026-10-01": 3.0}}), encoding="utf-8")
    _paris()
    budget.DailySpend(str(path), time_fn=lambda: LATE).add_extra(1.0)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["zone"] == PARIS and [(c["from"], c["to"]) for c in data["zone_changes"]] == [("UTC", PARIS)]
    assert data["days"] == {"2026-10-01": 3.0} and data["extra"] == {"2026-10-05": 1.0}


def test_reads_never_write_the_zone(tmp_path):
    path = tmp_path / "spend.json"
    budget.DailySpend(str(path), time_fn=lambda: LATE).add(0.5)
    raw = path.read_bytes()
    _paris()
    spend = budget.DailySpend(str(path), time_fn=lambda: LATE)
    spend.today_total(), spend.day_state(), spend.extra_today(), spend.grants_today()
    assert path.read_bytes() == raw


def test_the_module_docstring_writes_down_the_no_migration_rule():
    doc = " ".join(budget.__doc__.split())
    for phrase in ("not migrated", "zone_changes", "neighbour day", "never takes a day below zero",
                   "free-tier counters", "``ts``"):
        assert phrase in doc, phrase


# --------------------------------------------- who spent today, the clips day

def test_day_report_counts_a_row_by_the_zone_day(tmp_path):
    from clipping.aistory import day_report
    from clipping.aistory.ledger import CostLedger

    _paris()
    path = tmp_path / "cost_ledger.json"
    ledger = CostLedger(str(path), time_fn=lambda: LATE)        # 22:30 UTC: Paris's 5 Oct
    ledger.append(step="assets", provider="fal", model="m", unit="image", qty=1, est_usd=0.40, paid=True)
    os.utime(path, (LATE, LATE))
    story = [{"story_id": "s1", "title": "T", "ledger_path": str(path)}]
    assert day_report.day_start_epoch("2026-10-05") == datetime(2026, 10, 4, 22, 0, tzinfo=timezone.utc).timestamp()
    assert day_report.day_contributors(story, day="2026-10-05") == [{"story_id": "s1", "title": "T", "usd": 0.4}]
    assert day_report.day_contributors(story, day="2026-10-04") == []


def test_clips_cache_key_uses_budget_day(monkeypatch):
    from types import SimpleNamespace

    from clipping.aistory import workflow

    for name in ("_derived_key", "_cache_files_stamp", "_file_stamp"):
        monkeypatch.setattr(workflow, name, lambda *a, **k: name)
    monkeypatch.setattr(workflow, "_store_path", lambda *a, **k: "p")
    monkeypatch.setattr(workflow.budget_mod, "today", lambda **k: "2026-10-05")
    ec = SimpleNamespace(store=SimpleNamespace(gen_cache_dir=None, story_dir=None), story_id="s")

    key = workflow._clips_key(ec, {}, {"shots": []}, {}, {})

    assert key[-1] == "2026-10-05"
    src = read("clipping/aistory/workflow.py")
    assert "time.gmtime()" not in src[src.index("def _clips_key"):src.index("def _shot_clip")]


# ---------------------------------------------- the key's own agreement test

def test_budget_timezone_agrees_everywhere_but_the_five_budget_fields():
    """Kept OUT of ENV_NAMES and the Budget namedtuple (the five-field
    agreement test stays as it is); persisted, never secret, documented,
    validated by the route, edited on the Settings budget card."""
    assert budget.TIMEZONE_ENV == "BUDGET_TIMEZONE"
    assert budget.TIMEZONE_ENV not in budget.ENV_NAMES
    assert len(budget.ENV_NAMES) == 5 and len(budget.Budget._fields) == 5
    assert "timezone" not in " ".join(budget.Budget._fields)

    store = read("web/api/settings_store.py")
    persisted = store[store.index("PERSISTED_KEYS"):store.index("SECRET_KEYS")]
    secrets = store[store.index("SECRET_KEYS = "):]
    assert '"BUDGET_TIMEZONE"' in persisted and '"BUDGET_TIMEZONE"' not in secrets
    assert re.search(r"^BUDGET_TIMEZONE=\s*$", read(".env.example"), re.M)

    models = read("web/api/models.py")
    assert "budget_timezone: Optional[str] = None" in models
    assert 'budget_timezone: str = ""' in models
    route = read("web/api/routes/settings.py")
    assert 'env_updates["BUDGET_TIMEZONE"] = check_zone_name(req.budget_timezone)' in route
    page = read("web/dashboard/src/pages/Settings.jsx")
    assert "payload.budget_timezone =" in page
    assert "IANA name, e.g. Europe/Paris; empty = UTC" in page
    assert 'id="settings-budget-timezone"' in page

    # Both processes hand the budget their Settings.
    assert "budget_mod.set_settings_reader(get_settings_env)" in read("web/api/worker.py")
    assert "budget_mod.set_settings_reader(_settings_env)" in read("clipping/aistory/cli.py")


def test_the_settings_store_persists_the_zone_and_never_redacts_it():
    from web.api import settings_store

    assert "BUDGET_TIMEZONE" in settings_store.PERSISTED_KEYS
    assert "BUDGET_TIMEZONE" not in settings_store.SECRET_KEYS
    assert settings_store.redact({"BUDGET_TIMEZONE": PARIS}) == {"BUDGET_TIMEZONE": PARIS}


def test_tzdata_is_pinned_and_installed_in_the_image():
    pins = [line for line in read("requirements.txt").splitlines() if line.startswith("tzdata")]
    assert len(pins) == 1 and re.match(r"^tzdata==\d{4}\.\d+\b", pins[0])
    docker = read("Dockerfile")
    final = docker[docker.index("# ---- Final stage ----"):]
    apt = final[final.index("apt-get install"):final.index("rm -rf /var/lib/apt/lists/*")]
    assert re.search(r"^\s+tzdata \\$", apt, re.M)


def test_the_free_tier_counters_stay_on_the_utc_day(tmp_path):
    """Left UTC on purpose: the providers reset their own quotas at 00:00 UTC."""
    from clipping.providers import limits

    _paris()
    assert budget.DailySpend("x", time_fn=lambda: LATE).today() == "2026-10-05"
    assert limits.DailyUsage(str(tmp_path / "usage.json"), time_fn=lambda: LATE).today() == "2026-10-04"
    src = read("clipping/providers/limits.py")
    assert "day_zone" not in src and "BUDGET_TIMEZONE" not in src
