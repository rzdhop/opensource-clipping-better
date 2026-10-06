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
    # Re-pinned on purpose (DEC-223, AI Story phase 7 stage 2a; the human's
    # CLARIFY answer 1): the caps were 1 / 3 / 10, they are 2 / 6 / 20 so one
    # episode on the Quality (billed APIs) preset fits. allow_paid stays off.
    # Re-pinned again (phase 7 follow-up, stage E: clips with their own sound on
    # Veo 3.1 lite, about $3.3-3.5 an episode): 4 / 12 / 40, the 1:3:10 ratio kept.
    # 1. the config constants (imported from budget.py, so one definition)
    assert config.ALLOW_PAID is False
    assert config.PER_EPISODE_CAP_USD == budget.PER_EPISODE_CAP_USD == 4.0
    assert config.DAILY_CAP_USD == budget.DAILY_CAP_USD == 12.0
    assert config.PER_STORY_CAP_USD == budget.PER_STORY_CAP_USD == 40.0
    assert config.BUDGET_PROFILE == budget.BUDGET_PROFILE == ""
    assert "from clipping.providers.budget import" in read("clipping/config.py")

    # 2. the CLI
    parser = config._build_parser()
    assert parser.get_default("allow_paid") is False
    assert parser.get_default("per_episode_cap_usd") == 4.0
    assert parser.get_default("daily_cap_usd") == 12.0
    assert parser.get_default("per_story_cap_usd") == 40.0
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
                 "allow_paid: bool = False", "per_episode_cap_usd: float = 4.0", "daily_cap_usd: float = 12.0",
                 "per_story_cap_usd: float = 40.0", 'budget_profile: str = ""', 'effective_budget_profile: str = "free"'):
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
    # Re-pinned on purpose (plan 22, stage 4): the native-speech profile joins the three;
    # (stage 5) and its manual twin, every clip the human's own upload;
    # (DEC-312) and own_gpu: the quality roles on the author's RunPod endpoints.
    assert set(profiles["profiles"]) == {"free", "one_dollar", "quality", "native_speech", "native_speech_manual",
                                         "own_gpu"}
    own = profiles["profiles"]["own_gpu"]
    assert own["images"] == "quality_roles" and own["cap_usd"] == 2.0 and own["video_link_policy"] == "first_in_chain"
    assert own["roles"]["sheet"][:2] == ["runpod/t2i_flux2_klein", "runpod/edit_flux2_klein_multiref"]
    assert own["roles"]["keyframe"][0] == "runpod/edit_flux2_klein_multiref"
    one = profiles["profiles"]["one_dollar"]
    assert one["cap_usd"] == 1.0
    assert one["animate_priority"] == ["hook", "cliffhanger", "peak", "turn", "longest_dialogue"]
    assert profiles["profiles"]["free"]["cap_usd"] == 0.0
    # DEC-221 (AI Story phase 7 stage 2a): the shipped quality profile is no longer a stub -- a
    # real cap, the quality_roles images policy with its roles table, and 720p video.
    # Re-pinned (stage 2c, DEC-235: "fal only", no Google billing): sheet/plate/prop moved from
    # gemini/nano-banana-2 to fal (its text-to-image link, then its edit sibling).
    # Re-pinned again (plan 23 stage A8, DEC-280): gemini/nano-banana-2-lite is their second link.
    quality = profiles["profiles"]["quality"]
    assert quality["cap_usd"] == 4.0  # stage E: Veo's clips with sound, about $3.3-3.5 an episode
    assert quality["images"] == "quality_roles"
    assert quality["roles"] == {
        "sheet": ["fal/seedream-4.5", "fal/seedream-4.5-edit", "gemini/nano-banana-2-lite"],
        "plate": ["fal/seedream-4.5", "fal/seedream-4.5-edit", "gemini/nano-banana-2-lite"],
        "prop": ["fal/seedream-4.5", "fal/seedream-4.5-edit", "gemini/nano-banana-2-lite"],
        "keyframe": ["fal/seedream-4.5-edit", "gemini/nano-banana-2-lite"],
    }
    assert quality["video_resolution"] == "720p"


def test_only_the_quality_profile_redraws_flagged_keyframes_and_its_key_is_checked(tmp_path):
    """Phase 8 stage B (the human's choice: up to 2 redraws a flagged shot,
    at most $0.40 an episode): ``keyframe_fix`` is the quality profile's
    alone; a profile without it never redraws. Present, it is checked.
    Plan 28 F1, re-pinned on purpose: the shipped profiles size the
    episode's budget by a rule (``cap_rule``: its shots x the redraws x one
    keyframe's price) instead of $0.40; a fixed ``cap_usd`` is still read."""
    profiles = load_profiles()
    assert profiles["profiles"]["quality"]["keyframe_fix"] == {"max_redraws_per_shot": 2,
                                                               "cap_rule": "shots_x_redraws_x_price"}
    assert "keyframe_fix" not in profiles["profiles"]["free"]
    assert "keyframe_fix" not in profiles["profiles"]["one_dollar"]

    path = tmp_path / "budget_profiles.json"
    for fix, keyword in (
        ([2, 0.4], "must be an object"),
        ({"max_redraws_per_shot": 2}, "must hold exactly max_redraws_per_shot, cap_usd"),
        ({"max_redraws_per_shot": 2, "cap_usd": 0.4, "per_shot_usd": 1}, "must hold exactly"),
        ({"max_redraws_per_shot": -1, "cap_usd": 0.4}, "max_redraws_per_shot must be a whole number from 0 to 5"),
        ({"max_redraws_per_shot": 2.0, "cap_usd": 0.4}, "max_redraws_per_shot must be a whole number"),
        ({"max_redraws_per_shot": True, "cap_usd": 0.4}, "max_redraws_per_shot must be a whole number"),
        ({"max_redraws_per_shot": 2, "cap_usd": -0.1}, "cap_usd must be an amount in USD (0 or more)"),
        ({"max_redraws_per_shot": 2, "cap_usd": "0.40"}, "cap_usd must be an amount"),
        ({"max_redraws_per_shot": 2, "cap_rule": "per_shot"}, "cap_rule must be one of shots_x_redraws_x_price"),
        ({"max_redraws_per_shot": 2, "cap_usd": 0.4, "cap_rule": "shots_x_redraws_x_price"}, "must hold exactly"),
    ):
        broken = load_profiles()
        broken["profiles"]["one_dollar"]["keyframe_fix"] = fix
        path.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(ValueError) as excinfo:
            load_profiles(str(path))
        assert "profile 'one_dollar': keyframe_fix" in str(excinfo.value) and keyword in str(excinfo.value), fix
    fine = load_profiles()
    fine["profiles"]["free"]["keyframe_fix"] = {"max_redraws_per_shot": 0, "cap_usd": 0}
    path.write_text(json.dumps(fine), encoding="utf-8")
    assert load_profiles(str(path))["profiles"]["free"]["keyframe_fix"]["max_redraws_per_shot"] == 0


def test_a_broken_profiles_file_is_refused_not_patched(tmp_path):
    bad = tmp_path / "budget_profiles.json"
    bad.write_text(json.dumps({"$schema": "budget_profiles_v1", "profiles": {"free": {"cap_usd": 0}}}), encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        load_profiles(str(bad))
    assert "one_dollar" in str(excinfo.value)


# ------------------------------------------------------------ the env read

def test_budget_from_env_defaults_and_overrides():
    # DEC-223 (AI Story phase 7 stage 2a): the caps default to 2 / 6 / 20, up from 1 / 3 / 10;
    # stage E (phase 7 follow-up): 4 / 12 / 40.
    assert budget_from_env({}) == Budget(False, 4.0, 12.0, 40.0, "free")
    got = budget_from_env({"ALLOW_PAID": "1", "PER_EPISODE_CAP_USD": "0.50", "DAILY_CAP_USD": "", "BUDGET_PROFILE": ""})
    assert got == Budget(True, 0.5, 12.0, 40.0, "one_dollar")


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


def test_a_release_gives_back_the_booked_day_and_never_drives_it_below_zero(tmp_path):
    clock = Clock()
    spend = DailySpend(str(tmp_path / "spend.json"), time_fn=clock)
    record(Est(0.03), spend=spend)
    record(Est(0.07), spend=spend)
    booked_day = spend.today()
    assert budget.release(Est(0.03), spend=spend) == 0.07  # today's spend drops back
    assert budget.day_spent(spend=spend) == 0.07
    # A booking of yesterday is given back to yesterday, never to today.
    clock.now += 86_400
    record(Est(0.05), spend=spend)
    assert budget.release(Est(0.07), day=booked_day, spend=spend) == 0.0
    assert budget.day_spent(spend=spend) == 0.05
    on_disk = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))
    assert on_disk["days"] == {booked_day: 0.0, spend.today(): 0.05}
    # Never below zero, never a day it holds nothing for, never a free estimate.
    assert budget.release(Est(1.0), spend=spend) == 0.0
    assert budget.release(Est(0.5), day="2020-01-01", spend=spend) == 0.0
    assert budget.release(Est(0.0), spend=spend) == 0.0
    assert json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))["days"] == {booked_day: 0.0,
                                                                                         spend.today(): 0.0}
    with pytest.raises(ValueError):
        spend.release(-0.01)


def test_a_free_result_records_nothing(tmp_path):
    spend = DailySpend(str(tmp_path / "spend.json"))
    assert record(Est(0.0), spend=spend) == 0.0
    assert not (tmp_path / "spend.json").exists()


def test_the_default_spend_file_lives_in_data(monkeypatch):
    monkeypatch.delenv("SPEND_PATH", raising=False)
    assert budget.default_spend_path().endswith(os.path.join("data", "spend.json"))
    assert budget.default_spend_path() != budget.__dict__.get("USAGE_PATH", "")


# ------------------------------------------- the daily extra (plan 23, stage A1)

def _day_message(**kwargs):
    on = Budget(True, 4.0, 4.0, 40.0, "one_dollar")
    with pytest.raises(BudgetRefused) as excinfo:
        check(Est(0.30), budget=on, day_spent=3.90, **kwargs)
    return str(excinfo.value)


def test_check_with_no_extra_is_byte_identical():
    expected = ("refused: est $0.300 on fal/seedream-4-edit would bring today to "
                "$4.20 of the $4.00 daily cap")
    assert _day_message() == expected
    assert _day_message(day_extra=0.0) == expected
    assert _day_message(day_extra=-1.0) == expected  # a negative extra is no extra


def test_day_extra_raises_the_effective_daily_cap_only():
    on = Budget(True, 4.0, 4.0, 40.0, "one_dollar")
    check(Est(0.30), budget=on, day_spent=3.90, day_extra=0.30)  # $4.20 <= $4.30 effective: fits
    with pytest.raises(BudgetRefused):
        check(Est(0.30), budget=on, day_spent=3.90, day_extra=0.19)  # $4.20 > $4.19 effective
    # the per-episode and per-story clauses do not move, and keep their text
    with pytest.raises(BudgetRefused) as ep:
        check(Est(0.30), budget=on, ep_spent=3.90, day_extra=5.0)
    assert "episode to $4.20 of its $4.00 cap" in str(ep.value)
    with pytest.raises(BudgetRefused) as story:
        check(Est(0.30), budget=on, story_spent=39.90, day_extra=5.0)
    assert "story to $40.20 of its $40.00 cap" in str(story.value)
    # allow_paid off still refuses, extra or not
    with pytest.raises(BudgetRefused) as off:
        check(Est(0.30), budget=Budget(False, 4.0, 4.0, 40.0, "free"), day_extra=5.0)
    assert "allow_paid is off" in str(off.value)


def test_extra_refusal_names_the_allowed_amount():
    assert _day_message(day_extra=0.10) == (
        "refused: est $0.300 on fal/seedream-4-edit would bring today to "
        "$4.20 of the $4.00 daily cap + $0.10 allowed today")


def test_extra_is_keyed_by_day_and_expires_at_the_next_day(tmp_path):
    clock = Clock()
    spend = DailySpend(str(tmp_path / "spend.json"), time_fn=clock)
    assert spend.extra_today() == 0.0
    assert spend.add_extra(5.0, story_id="s1", note="finish the cast") == 5.0
    assert spend.extra_today() == 5.0
    record(Est(0.5), spend=spend)
    state = budget.day_state(spend=spend)
    assert state == budget.DayState(spend.today(), "UTC", 0.5, 5.0)
    granted_day = spend.today()
    clock.now += 86_400
    assert spend.extra_today() == 0.0
    assert budget.day_state(spend=spend) == budget.DayState(spend.today(), "UTC", 0.0, 0.0)
    on_disk = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))
    assert on_disk["extra"] == {granted_day: 5.0}          # the old day keeps its own key
    assert on_disk["zone"] == "UTC"
    assert [(g["day"], g["usd"], g["story_id"], g["note"]) for g in on_disk["grants"]] == [
        (granted_day, 5.0, "s1", "finish the cast")]
    assert "at" in on_disk["grants"][0]
    spend.add_extra(1.0)
    assert spend.clear_extra() == 1.0                       # clears today only
    assert spend.clear_extra() == 0.0
    assert json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))["extra"] == {granted_day: 5.0}


def test_add_extra_accumulates_and_is_capped(tmp_path):
    spend = DailySpend(str(tmp_path / "spend.json"), time_fn=Clock())
    assert spend.add_extra(4.98) == 4.98
    assert spend.add_extra(0.02) == 5.0                     # the same day accumulates
    for bad in (0, -1.0):
        with pytest.raises(ValueError):
            spend.add_extra(bad)
    assert spend.add_extra(budget.DAY_EXTRA_MAX_USD - 5.0) == budget.DAY_EXTRA_MAX_USD
    with pytest.raises(ValueError):
        spend.add_extra(0.01)                               # over the ceiling: refused, nothing written
    assert spend.extra_today() == budget.DAY_EXTRA_MAX_USD == 25.0
    # the grants log keeps the last 200
    other = DailySpend(str(tmp_path / "many.json"), time_fn=Clock())
    for _ in range(budget.GRANTS_KEPT + 5):
        other.add_extra(0.01)
    assert len(json.loads((tmp_path / "many.json").read_text(encoding="utf-8"))["grants"]) == 200


def test_old_reader_keeps_extra_keys_on_add(tmp_path):
    clock = Clock()
    spend = DailySpend(str(tmp_path / "spend.json"), time_fn=clock)
    spend.add_extra(3.0, story_id="s9")
    before = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))
    assert record(Est(0.25), spend=spend) == 0.25
    assert budget.release(Est(0.05), spend=spend) == 0.2
    after = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))
    assert after["$schema"] == "spend_v1"
    for key in ("extra", "grants", "zone"):
        assert after[key] == before[key]
    assert after["days"][spend.today()] == 0.2


def test_the_own_gpu_profile_voices_on_the_runpod_clone_first_with_gemini_behind(tmp_path):
    """Plan 32 stage 6: own_gpu names its TTS chain -- the RunPod clone of each
    character's frozen reference, the Gemini voice it was made from behind --
    and no other shipped profile names one. A tts_chain is checked for its shape."""
    profiles = load_profiles()["profiles"]
    assert profiles["own_gpu"]["tts_chain"] == ["runpod/tts_chatterbox", "gemini/flash-lite-tts"]
    assert [name for name, profile in profiles.items() if "tts_chain" in profile] == ["own_gpu"]
    path = tmp_path / "budget_profiles.json"
    for chain in ([], "runpod/tts_chatterbox", ["runpod tts"], [3]):
        broken = load_profiles()
        broken["profiles"]["own_gpu"]["tts_chain"] = chain
        path.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(ValueError) as excinfo:
            load_profiles(str(path))
        assert "profile 'own_gpu': tts_chain must be a non-empty list of provider/model links" in str(excinfo.value)
