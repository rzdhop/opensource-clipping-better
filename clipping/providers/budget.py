"""Paid spending is opt-in and capped (DEC-097).

``allow_paid`` is off until the user turns it on; three caps bound what a
paid call may add -- per episode, per UTC day, per story -- and a refusal
always carries the numbers. The budget *profile* decides where the money
goes (``budget_profiles.json``); the caps decide whether it is spent at all.
``free`` is the profile until paid is on, ``one_dollar`` from then on, unless
the user picked one.

The defaults are defined HERE, once. ``clipping/config.py`` imports them for
the CLI, the web layer reads them through ``budget_from_env``, and the
five-place test asserts the request model, the config adapter and the Settings
form agree. Daily paid spend is kept in ``data/spend.json``, apart from the
free-tier counters of ``limits.py`` (``data/usage.json``), so "a paid call
never touches the free counters" is a byte comparison.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections import namedtuple
from datetime import datetime, timezone

ALLOW_PAID = False
# DEC-223 (AI Story phase 7): 2 / 6 / 20, up from 1 / 3 / 10, so one episode
# on the Quality (billed APIs) preset fits its cap. Phase 7 follow-up, stage E
# (the human's choice of 2026-10-02: clips with their own sound on Veo 3.1
# lite, about $3.3-3.5 a 60 s episode): 4 / 12 / 40, the 1:3:10 ratio kept.
# The saved Settings still override them. allow_paid stays off.
PER_EPISODE_CAP_USD = 4.00   # the author's ceiling (spec 8.5)
DAILY_CAP_USD = 12.00
PER_STORY_CAP_USD = 40.00
BUDGET_PROFILE = ""          # "" = resolved from allow_paid

PROFILE_WHEN_FREE = "free"
PROFILE_WHEN_PAID = "one_dollar"
PROFILE_NAMES = ("free", "one_dollar", "quality")

ENV_NAMES = ("ALLOW_PAID", "PER_EPISODE_CAP_USD", "DAILY_CAP_USD", "PER_STORY_CAP_USD", "BUDGET_PROFILE")

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PROFILES_PATH = os.path.join(_ROOT, "clipping", "aistory", "templates", "budget_profiles.json")

Budget = namedtuple("Budget", "allow_paid per_episode_cap_usd daily_cap_usd per_story_cap_usd profile")


class BudgetRefused(Exception):
    """A paid call the caps or the switch do not allow. The message has the numbers."""


# ------------------------------------------------------------- resolution

def _flag(raw) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes"}


def _cap(name: str, raw, default: float) -> float:
    text = str(raw if raw is not None else "").strip()
    if not text:
        return float(default)
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{name} must be an amount in USD, not {text!r}") from None
    if value < 0:
        raise ValueError(f"{name} cannot be negative ({text!r})")
    return value


def resolve_profile(setting, allow_paid: bool) -> str:
    """The profile in force: the user's choice, else free / one_dollar by the switch."""
    name = str(setting or "").strip().lower()
    if not name:
        return PROFILE_WHEN_PAID if allow_paid else PROFILE_WHEN_FREE
    if name not in PROFILE_NAMES:
        raise ValueError(f"Unknown budget profile {name!r}. Known: {', '.join(PROFILE_NAMES)}.")
    return name


def budget_from_env(env=None) -> Budget:
    """The budget the five settings describe (a mapping of variable name -> value)."""
    env = os.environ if env is None else env
    allow_paid = _flag(env.get("ALLOW_PAID"))
    return Budget(
        allow_paid,
        _cap("PER_EPISODE_CAP_USD", env.get("PER_EPISODE_CAP_USD"), PER_EPISODE_CAP_USD),
        _cap("DAILY_CAP_USD", env.get("DAILY_CAP_USD"), DAILY_CAP_USD),
        _cap("PER_STORY_CAP_USD", env.get("PER_STORY_CAP_USD"), PER_STORY_CAP_USD),
        resolve_profile(env.get("BUDGET_PROFILE"), allow_paid),
    )


# --------------------------------------------------------------- profiles

_REQUIRED_PROFILE_KEYS = ("cap_usd", "images", "tts", "animate")

# Optional keys (phase 7, DEC-221). ``roles``: the image links of each role of
# a v2 story (``clipping/aistory/media_policy.py``), required by the
# ``quality_roles`` images policy; ``video_resolution``: the clips' size.
PROFILE_ROLES = ("sheet", "plate", "prop", "keyframe")
QUALITY_ROLES_POLICY = "quality_roles"
VIDEO_RESOLUTIONS = ("720p", "1080p")
# ``video_link_policy``: which keyed hosted link of VIDEO_CHAIN an episode's
# clips are bought on (``steps/clips.pick_hosted``) -- the cheapest, the first
# in chain order, or (phase 7 follow-up, stage E) the first whose clips carry
# their own sound when the story keeps it as ambience.
VIDEO_LINK_POLICIES = ("cheapest_available", "first_in_chain", "first_with_audio")
# ``tier3_native_audio``: what a tier-3 story does with a clip's own sound.
# ``opt_in`` (phase 6 stage 10, DEC-201): a shot that keeps it hears it in
# place of its lines; ``ambience`` (stage E, the human's choice of
# 2026-10-02): every shot's clip sound is heard UNDER its lines, ducked, the
# lines always in their pinned TTS voices (``media_policy.ambience``).
TIER3_AUDIO_MODES = ("opt_in", "ambience")
# Phase 8 stage B: ``keyframe_fix`` -- a v2 story's keyframes flagged by the
# keyframe check (J2) are redrawn by the assets step, at most
# ``max_redraws_per_shot`` times a shot and ``cap_usd`` an episode. A profile
# without it never redraws on its own.
KEYFRAME_FIX_KEYS = ("max_redraws_per_shot", "cap_usd")
KEYFRAME_FIX_MAX_REDRAWS = 5


def _profile_errors(name, profile) -> list:
    """What is wrong with the optional keys of profile *name*."""
    errors = []
    roles = profile.get("roles")
    if roles is not None:
        if not isinstance(roles, dict) or not roles:
            errors.append(f"profile {name!r}: \"roles\" must be an object of role -> links")
        else:
            unknown = sorted(set(map(str, roles)) - set(PROFILE_ROLES))
            if unknown:
                errors.append(f"profile {name!r}: unknown role(s) {', '.join(unknown)} "
                              f"(known: {', '.join(PROFILE_ROLES)})")
            for role, links in roles.items():
                if (not isinstance(links, list) or not links
                        or not all(isinstance(link, str) and link.strip() for link in links)):
                    errors.append(f"profile {name!r}: roles.{role} must be a non-empty list of links")
    if profile.get("images") == QUALITY_ROLES_POLICY and not isinstance(roles, dict):
        errors.append(f"profile {name!r}: images {QUALITY_ROLES_POLICY!r} needs a \"roles\" table")
    resolution = profile.get("video_resolution")
    if resolution is not None and resolution not in VIDEO_RESOLUTIONS:
        errors.append(f"profile {name!r}: video_resolution must be one of {', '.join(VIDEO_RESOLUTIONS)}, "
                      f"not {resolution!r}")
    for key, known in (("video_link_policy", VIDEO_LINK_POLICIES), ("tier3_native_audio", TIER3_AUDIO_MODES)):
        if key in profile and profile[key] not in known:
            errors.append(f"profile {name!r}: {key} must be one of {', '.join(known)}, not {profile[key]!r}")
    if "keyframe_fix" in profile:
        errors.extend(_keyframe_fix_errors(name, profile["keyframe_fix"]))
    return errors


def _keyframe_fix_errors(name, fix) -> list:
    """What is wrong with profile *name*'s optional ``keyframe_fix``:
    exactly ``max_redraws_per_shot`` (a whole number, 0 to
    :data:`KEYFRAME_FIX_MAX_REDRAWS`) and ``cap_usd`` (an amount >= 0)."""
    where = f"profile {name!r}: keyframe_fix"
    if not isinstance(fix, dict):
        return [f"{where} must be an object {{max_redraws_per_shot, cap_usd}}"]
    errors = []
    if sorted(fix) != sorted(KEYFRAME_FIX_KEYS):
        errors.append(f"{where} must hold exactly {', '.join(KEYFRAME_FIX_KEYS)} (it holds "
                      f"{', '.join(sorted(map(str, fix))) or 'nothing'})")
    redraws = fix.get("max_redraws_per_shot")
    if "max_redraws_per_shot" in fix and (isinstance(redraws, bool) or not isinstance(redraws, int)
                                          or not 0 <= redraws <= KEYFRAME_FIX_MAX_REDRAWS):
        errors.append(f"{where}.max_redraws_per_shot must be a whole number from 0 to {KEYFRAME_FIX_MAX_REDRAWS}, "
                      f"not {redraws!r}")
    cap = fix.get("cap_usd")
    if "cap_usd" in fix and (isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap < 0):
        errors.append(f"{where}.cap_usd must be an amount in USD (0 or more), not {cap!r}")
    return errors


def load_profiles(path=None) -> dict:
    """The shipped ``budget_profiles.json`` (spec 8.5.1), validated. A bad file raises."""
    path = path or PROFILES_PATH
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or data.get("$schema") != "budget_profiles_v1":
        raise ValueError(f"{path}: expected \"$schema\": \"budget_profiles_v1\"")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError(f"{path}: \"profiles\" must be an object")
    missing = [name for name in PROFILE_NAMES if name not in profiles]
    if missing:
        raise ValueError(f"{path}: missing profile(s) {', '.join(missing)}")
    for name, profile in profiles.items():
        for key in _REQUIRED_PROFILE_KEYS:
            if key not in profile:
                raise ValueError(f"{path}: profile {name!r} lacks {key!r}")
        errors = _profile_errors(name, profile)
        if errors:
            raise ValueError(f"{path}: {'; '.join(errors)}")
    return data


def profile_settings(name: str, path=None) -> dict:
    return load_profiles(path)["profiles"][name]


# ------------------------------------------------------------------ check

def _usd(estimate) -> float:
    if estimate is None:
        return 0.0
    if isinstance(estimate, (int, float)):
        return float(estimate)
    return float(getattr(estimate, "est_usd", 0.0) or 0.0)


def _label(estimate, link) -> str:
    label = getattr(estimate, "link", None)
    if label:
        return str(label)
    if link is not None:
        return f"{link.provider}/{link.model}"
    return "this call"


def check(estimate, link=None, *, budget: Budget, day_spent=0.0, ep_spent=0.0, story_spent=0.0) -> None:
    """Raise :class:`BudgetRefused` unless *estimate* may be spent right now.

    A free call (``$0.00``) is never refused: the budget governs money, not
    requests. Every refusal names the estimate, the cap and what is already
    spent, so the user can decide with the numbers in front of them.
    """
    usd = _usd(estimate)
    if usd <= 0:
        return
    label = _label(estimate, link)
    if not budget.allow_paid:
        raise BudgetRefused(
            f"refused: est ${usd:.3f} on {label}; allow_paid is off "
            f"(today ${day_spent:.2f} of ${budget.daily_cap_usd:.2f})"
        )
    if ep_spent + usd > budget.per_episode_cap_usd:
        raise BudgetRefused(
            f"refused: est ${usd:.3f} on {label} would bring this episode to "
            f"${ep_spent + usd:.2f} of its ${budget.per_episode_cap_usd:.2f} cap"
        )
    if day_spent + usd > budget.daily_cap_usd:
        raise BudgetRefused(
            f"refused: est ${usd:.3f} on {label} would bring today to "
            f"${day_spent + usd:.2f} of the ${budget.daily_cap_usd:.2f} daily cap"
        )
    if story_spent + usd > budget.per_story_cap_usd:
        raise BudgetRefused(
            f"refused: est ${usd:.3f} on {label} would bring this story to "
            f"${story_spent + usd:.2f} of its ${budget.per_story_cap_usd:.2f} cap"
        )


# ------------------------------------------------------------ daily spend

def default_spend_path() -> str:
    return os.environ.get("SPEND_PATH") or os.path.join(_ROOT, "data", "spend.json")


class DailySpend:
    """Paid dollars per UTC day, on disk (``spend_v1``). Never the free counters' file."""

    SCHEMA = "spend_v1"

    def __init__(self, path, *, time_fn=None):
        self.path = path
        self._time = time_fn or time.time
        self._lock = threading.Lock()

    def today(self) -> str:
        return datetime.fromtimestamp(self._time(), tz=timezone.utc).strftime("%Y-%m-%d")

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict) or data.get("$schema") != self.SCHEMA:
            data = {"$schema": self.SCHEMA, "days": {}}
        data.setdefault("days", {})
        return data

    def _write(self, data: dict) -> None:
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=directory, prefix=".spend-", suffix=".tmp")
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

    def today_total(self) -> float:
        with self._lock:
            return float(self._load()["days"].get(self.today(), 0.0))

    def add(self, usd: float) -> float:
        with self._lock:
            data = self._load()
            day = self.today()
            total = round(float(data["days"].get(day, 0.0)) + float(usd), 4)
            data["days"][day] = total
            data["updated_at"] = datetime.fromtimestamp(self._time(), tz=timezone.utc).isoformat()
            self._write(data)
            return total


_DEFAULT_SPEND = None
_DEFAULT_SPEND_LOCK = threading.Lock()


def default_spend() -> DailySpend:
    global _DEFAULT_SPEND
    with _DEFAULT_SPEND_LOCK:
        path = default_spend_path()
        if _DEFAULT_SPEND is None or _DEFAULT_SPEND.path != path:
            _DEFAULT_SPEND = DailySpend(path)
        return _DEFAULT_SPEND


def reset() -> None:
    """Drop the cached default spend store. For tests."""
    global _DEFAULT_SPEND
    with _DEFAULT_SPEND_LOCK:
        _DEFAULT_SPEND = None


def day_spent(*, spend=None) -> float:
    return (spend or default_spend()).today_total()


def record(estimate, *, spend=None) -> float:
    """Book a paid estimate against today. Returns today's total; a free one books nothing."""
    usd = _usd(estimate)
    store = spend or default_spend()
    if usd <= 0:
        return store.today_total() if os.path.exists(store.path) else 0.0
    return store.add(usd)
