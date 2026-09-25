"""Paid spending is opt-in and capped (DEC-097): ``allow_paid`` is off, the
caps are $1.00 per episode, $3.00 per day and $10.00 per story, the profile
is ``free`` and becomes ``one_dollar`` once paid is on -- in all five places
(config constant, request/response models, config_adapter, CLI, Settings
form), and every refusal carries the numbers."""

import json
import os
import pathlib
import re

import pytest

from clipping import config
from clipping.providers import budget
from clipping.providers.budget import (
    Budget, BudgetRefused, DailySpend, budget_from_env, check, load_profiles, record, resolve_profile,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------- the five places

def test_the_defaults_agree_in_all_five_places():
    # 1. the config constants (imported from budget.py, so one definition)
    assert config.ALLOW_PAID is False
    assert config.PER_EPISODE_CAP_USD == budget.PER_EPISODE_CAP_USD == 1.0
    assert config.DAILY_CAP_USD == budget.DAILY_CAP_USD == 3.0
    assert config.PER_STORY_CAP_USD == budget.PER_STORY_CAP_USD == 10.0
    assert config.BUDGET_PROFILE == budget.BUDGET_PROFILE == ""
    assert "from clipping.providers.budget import" in read("clipping/config.py")

    # 2. the CLI
    parser = config._build_parser()
    assert parser.get_default("allow_paid") is False
    assert parser.get_default("per_episode_cap_usd") == 1.0
    assert parser.get_default("daily_cap_usd") == 3.0
    assert parser.get_default("per_story_cap_usd") == 10.0
    assert parser.get_default("budget_profile") is None
    src = read("clipping/config.py")
    for flag in ("--allow-paid", "--per-episode-cap-usd", "--daily-cap-usd", "--per-story-cap-usd", "--budget-profile"):
        assert f'"{flag}"' in src, flag
    assert "allow_paid=args.allow_paid or ALLOW_PAID" in src

    # 3. the request and response models
    models = read("web/api/models.py")
    for line in ("allow_paid: Optional[bool] = None", "per_episode_cap_usd: Optional[float] = None",
                 "daily_cap_usd: Optional[float] = None", "per_story_cap_usd: Optional[float] = None",
                 "budget_profile: Optional[str] = None",
                 "allow_paid: bool = False", "per_episode_cap_usd: float = 1.0", "daily_cap_usd: float = 3.0",
                 "per_story_cap_usd: float = 10.0", 'budget_profile: str = ""', 'effective_budget_profile: str = "free"'):
        assert line in models, line

    # 4. the web config adapter and the settings route
    adapter = read("web/api/config_adapter.py")
    assert 'allow_paid=env_flag(env, "ALLOW_PAID")' in adapter
    for name in ("PER_EPISODE_CAP_USD", "DAILY_CAP_USD", "PER_STORY_CAP_USD"):
        assert f'env_float(env, "{name}", {name})' in adapter, name
    route = read("web/api/routes/settings.py")
    assert 'env_updates["ALLOW_PAID"] = "1" if req.allow_paid else ""' in route
    assert "budget_from_env(" in route

    # 5. the Settings form
    page = read("web/dashboard/src/pages/Settings.jsx")
    assert "const [allowPaid, setAllowPaid] = useState(false)" in page
    for field in ("allow_paid", "per_episode_cap_usd", "daily_cap_usd", "per_story_cap_usd", "budget_profile"):
        assert f"payload.{field} =" in page, field

    # and what makes them survive a restart
    store = read("web/api/settings_store.py")
    persisted = store[store.index("PERSISTED_KEYS"):store.index("SECRET_KEYS")]
    secrets = store[store.index("SECRET_KEYS = "):]
    for name in ("ALLOW_PAID", "PER_EPISODE_CAP_USD", "DAILY_CAP_USD", "PER_STORY_CAP_USD", "BUDGET_PROFILE"):
        assert f'"{name}"' in persisted, name
        assert f'"{name}"' not in secrets, name
    env_example = read(".env.example")
    for name in ("ALLOW_PAID", "PER_EPISODE_CAP_USD", "DAILY_CAP_USD", "PER_STORY_CAP_USD", "BUDGET_PROFILE"):
        assert re.search(rf"^{name}=", env_example, re.M), name


# ------------------------------------------------------------- the profile

@pytest.mark.parametrize("setting,allow_paid,expected", [
    ("", False, "free"), ("", True, "one_dollar"), (None, True, "one_dollar"),
    ("quality", False, "quality"), ("free", True, "free"), (" One_Dollar ", False, "one_dollar"),
])
def test_the_profile_is_free_until_paid_is_on_unless_chosen(setting, allow_paid, expected):
    assert resolve_profile(setting, allow_paid) == expected


def test_an_unknown_profile_is_refused():
    with pytest.raises(ValueError) as excinfo:
        resolve_profile("cheap", True)
    assert "cheap" in str(excinfo.value) and "one_dollar" in str(excinfo.value)


def test_the_shipped_profiles_match_the_spec():
    profiles = load_profiles()
    assert profiles["$schema"] == "budget_profiles_v1"
    assert set(profiles["profiles"]) == {"free", "one_dollar", "quality"}
    one = profiles["profiles"]["one_dollar"]
    assert one["cap_usd"] == 1.0
    assert one["animate_priority"] == ["hook", "cliffhanger", "peak", "turn", "longest_dialogue"]
    assert profiles["profiles"]["free"]["cap_usd"] == 0.0
    assert profiles["profiles"]["quality"]["cap_usd"] is None


def test_a_broken_profiles_file_is_refused_not_patched(tmp_path):
    bad = tmp_path / "budget_profiles.json"
    bad.write_text(json.dumps({"$schema": "budget_profiles_v1", "profiles": {"free": {"cap_usd": 0}}}), encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        load_profiles(str(bad))
    assert "one_dollar" in str(excinfo.value)


# ------------------------------------------------------------ the env read

def test_budget_from_env_defaults_and_overrides():
    assert budget_from_env({}) == Budget(False, 1.0, 3.0, 10.0, "free")
    got = budget_from_env({"ALLOW_PAID": "1", "PER_EPISODE_CAP_USD": "0.50", "DAILY_CAP_USD": "", "BUDGET_PROFILE": ""})
    assert got == Budget(True, 0.5, 3.0, 10.0, "one_dollar")


def test_a_garbage_cap_is_refused_naming_the_variable():
    with pytest.raises(ValueError) as excinfo:
        budget_from_env({"DAILY_CAP_USD": "three dollars"})
    assert "DAILY_CAP_USD" in str(excinfo.value)
    with pytest.raises(ValueError):
        budget_from_env({"PER_STORY_CAP_USD": "-1"})


# ------------------------------------------------------------- the check

class Est:
    def __init__(self, usd, link="fal/seedream-4-edit"):
        self.est_usd = usd
        self.link = link


def test_a_free_call_is_never_the_budgets_business():
    check(Est(0.0), budget=Budget(False, 1.0, 3.0, 10.0, "free"))
    check(0.0, budget=Budget(False, 1.0, 3.0, 10.0, "free"))


def test_paid_is_refused_while_allow_paid_is_off_with_the_numbers():
    with pytest.raises(BudgetRefused) as excinfo:
        check(Est(0.03), budget=Budget(False, 1.0, 3.0, 10.0, "free"), day_spent=0.0)
    message = str(excinfo.value)
    assert "est $0.030" in message and "fal/seedream-4-edit" in message
    assert "allow_paid is off" in message and "today $0.00 of $3.00" in message


def test_each_cap_refuses_with_its_own_numbers():
    on = Budget(True, 1.0, 3.0, 10.0, "one_dollar")
    with pytest.raises(BudgetRefused) as ep:
        check(Est(0.30), budget=on, ep_spent=0.80)
    assert "episode to $1.10 of its $1.00 cap" in str(ep.value)
    with pytest.raises(BudgetRefused) as day:
        check(Est(0.30), budget=on, day_spent=2.90)
    assert "today to $3.20 of the $3.00 daily cap" in str(day.value)
    with pytest.raises(BudgetRefused) as story:
        check(Est(0.30), budget=on, story_spent=9.90)
    assert "story to $10.20 of its $10.00 cap" in str(story.value)
    check(Est(0.30), budget=on, ep_spent=0.60, day_spent=2.60, story_spent=9.60)


def test_a_plain_number_is_an_estimate_too():
    with pytest.raises(BudgetRefused):
        check(0.5, budget=Budget(False, 1.0, 3.0, 10.0, "free"))


# ------------------------------------------------------------- the spend

class Clock:
    def __init__(self):
        self.now = 1_790_000_000.0

    def __call__(self):
        return self.now


def test_paid_spend_accumulates_per_utc_day_and_is_atomic(tmp_path):
    clock = Clock()
    spend = DailySpend(str(tmp_path / "spend.json"), time_fn=clock)
    assert record(Est(0.03), spend=spend) == 0.03
    assert record(Est(0.07), spend=spend) == 0.10
    on_disk = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))
    assert on_disk["$schema"] == "spend_v1"
    assert on_disk["days"][spend.today()] == 0.10
    assert sorted(os.listdir(tmp_path)) == ["spend.json"]
    clock.now += 86_400
    assert spend.today_total() == 0.0
    assert record(Est(0.05), spend=spend) == 0.05
    assert budget.day_spent(spend=spend) == 0.05


def test_a_free_result_records_nothing(tmp_path):
    spend = DailySpend(str(tmp_path / "spend.json"))
    assert record(Est(0.0), spend=spend) == 0.0
    assert not (tmp_path / "spend.json").exists()


def test_the_default_spend_file_lives_in_data(monkeypatch):
    monkeypatch.delenv("SPEND_PATH", raising=False)
    assert budget.default_spend_path().endswith(os.path.join("data", "spend.json"))
    assert budget.default_spend_path() != budget.__dict__.get("USAGE_PATH", "")
