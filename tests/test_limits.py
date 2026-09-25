"""Free-tier etiquette (DEC-098): per-provider RPM/RPD, the daily counters
persisted in data/usage.json, checked before a call so a spent allowance moves
the chain on instead of earning a 429 -- and never touched by a paid call."""

import json
import os
import threading

import pytest

from clipping.providers import limits, pacing
from clipping.providers.limits import DailyUsage, Limit, acquire, budget_left_today, limits_from_env


class Clock:
    def __init__(self, epoch=1_790_000_000.0):
        self.now = epoch

    def time(self):
        return self.now

    def monotonic(self):
        return self.now


@pytest.fixture
def usage(tmp_path):
    clock = Clock()
    return DailyUsage(str(tmp_path / "usage.json"), time_fn=clock.time), clock


@pytest.fixture(autouse=True)
def fresh_pacing():
    pacing.reset_limiters()
    limits.reset()
    yield
    pacing.reset_limiters()
    limits.reset()


def test_the_published_limits_are_in_the_table():
    table = limits_from_env({})
    assert table["cloudflare"] == Limit(rpm=10, rpd=170)
    assert table["openrouter"] == Limit(rpm=20, rpd=50)
    assert table["gemini"].rpd == 250
    assert table["groq"] == Limit(rpm=30, rpd=1000)
    assert table["freesound"] == Limit(rpm=60, rpd=2000)
    assert "fal" not in table and "local" not in table and "openai" not in table


def test_env_overrides_a_published_number():
    table = limits_from_env({"LIMIT_OPENROUTER_RPD": "1000", "LIMIT_CLOUDFLARE_RPM": "3"})
    assert table["openrouter"].rpd == 1000
    assert table["cloudflare"] == Limit(rpm=3, rpd=170)


def test_a_spent_daily_allowance_is_refused_without_sleeping(usage):
    store, clock = usage
    table = {"cloudflare": Limit(rpm=None, rpd=2)}
    never = lambda s: (_ for _ in ()).throw(AssertionError("must not sleep"))  # noqa: E731
    assert acquire("cloudflare", usage=store, limits=table, sleep_fn=never) is None
    assert acquire("cloudflare", usage=store, limits=table, sleep_fn=never) is None
    reason = acquire("cloudflare", usage=store, limits=table, sleep_fn=never)
    assert reason == "daily allowance spent (2/2 today, resets at 00:00 UTC)"
    assert store.snapshot()["providers"]["cloudflare"]["calls"] == 2


def test_the_counters_persist_and_reset_on_a_new_utc_day(usage, tmp_path):
    store, clock = usage
    table = {"gemini": Limit(rpm=None, rpd=250)}
    acquire("gemini", usage=store, limits=table)
    on_disk = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert on_disk["$schema"] == "usage_v1"
    assert on_disk["providers"]["gemini"]["calls"] == 1
    assert on_disk["day"] == store.today()
    clock.now += 86_400
    acquire("gemini", usage=store, limits=table)
    on_disk = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert on_disk["providers"]["gemini"]["calls"] == 1
    assert on_disk["day"] != store.snapshot()["day"] or on_disk["day"] == store.today()


def test_a_provider_without_limits_is_neither_counted_nor_refused(usage, tmp_path):
    store, clock = usage
    assert acquire("local", usage=store, limits={}) is None
    assert acquire("fal", usage=store, limits={}) is None
    assert not (tmp_path / "usage.json").exists()


def test_a_paid_provider_never_touches_the_file(usage, tmp_path):
    store, clock = usage
    table = limits_from_env({})
    acquire("cloudflare", usage=store, limits=table, sleep_fn=lambda s: None)
    before = (tmp_path / "usage.json").read_bytes()
    assert acquire("fal", usage=store, limits=table) is None
    assert acquire("openai", usage=store, limits=table) is None
    assert (tmp_path / "usage.json").read_bytes() == before


def test_rpm_is_paced_through_the_sliding_window(usage):
    store, clock = usage
    slept = []
    table = {"edge": Limit(rpm=2, rpd=None)}
    for _ in range(2):
        acquire("edge", usage=store, limits=table, time_fn=clock.monotonic, sleep_fn=slept.append)
    assert slept == []

    def sleep(seconds):
        slept.append(seconds)
        clock.now += seconds

    acquire("edge", usage=store, limits=table, time_fn=clock.monotonic, sleep_fn=sleep)
    assert slept and 0 < slept[0] <= 60


def test_the_write_is_atomic_and_leaves_no_temp_file(usage, tmp_path):
    store, clock = usage
    acquire("gemini", usage=store, limits={"gemini": Limit(rpm=None, rpd=250)})
    assert sorted(os.listdir(tmp_path)) == ["usage.json"]


def test_two_threads_count_two_calls(usage):
    store, clock = usage
    table = {"gemini": Limit(rpm=None, rpd=250)}
    threads = [threading.Thread(target=acquire, args=("gemini",), kwargs={"usage": store, "limits": table}) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.snapshot()["providers"]["gemini"]["calls"] == 8


def test_budget_left_today_reports_every_daily_limit(usage):
    store, clock = usage
    table = limits_from_env({})
    acquire("cloudflare", usage=store, limits=table, sleep_fn=lambda s: None)
    left = budget_left_today(usage=store, limits=table)
    assert left["cloudflare"] == {"calls": 1, "rpd": 170, "left": 169, "rpm": 10}
    assert left["openrouter"] == {"calls": 0, "rpd": 50, "left": 50, "rpm": 20}
    assert "edge" not in left, "no daily limit, nothing to report"
    assert left["day"] == store.today()


def test_the_default_usage_file_lives_in_data(monkeypatch):
    monkeypatch.delenv("USAGE_PATH", raising=False)
    assert limits.default_usage_path().endswith(os.path.join("data", "usage.json"))
    monkeypatch.setenv("USAGE_PATH", "/tmp/elsewhere.json")
    assert limits.default_usage_path() == "/tmp/elsewhere.json"
