"""Paid spending is opt-in and capped (DEC-097).

``allow_paid`` is off until the user turns it on; three caps bound what a
paid call may add -- per episode, per budget day, per story -- and a refusal
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

The budget day (plan 23 A7). The daily cap's day runs from 00:00 to 24:00 in
``BUDGET_TIMEZONE`` (an IANA name such as ``Europe/Paris``; UTC when unset,
empty or invalid -- an invalid name is reported in :data:`zone_error`, never
raised). The value is read from the Settings through a registered reader
(:func:`set_settings_reader`: the web worker's and the story CLI's stored
Settings), else from the process environment. It is kept out of
:data:`ENV_NAMES` and :class:`Budget` on purpose: it decides which day a
dollar belongs to, not whether it may be spent.

``spend.json`` is not migrated when the zone changes: it holds one total per
day, not the bookings' instants, so its history cannot be re-split by hour.
The first write under a new zone records ``zone`` and appends
``zone_changes: [{at, from, to}]``; the days already stored stay as they
were. So at most one boundary moves (two hours, for Paris): the switch day
may hold a little more or less than one local day's spending. A release of a
booking made before the switch, in the hours the two zones' days disagree
(22:00-24:00 UTC for Paris in summer), is keyed by :func:`day_key_at` in the
new zone and may land on the neighbour day; :meth:`DailySpend.release` never
takes a day below zero, nor gives back to a day it holds nothing for.

Left on UTC on purpose: the free-tier counters (``limits.py``: the providers
reset their own quotas at 00:00 UTC), ``pricing``'s ``date.today()``, and the
ledger / generation-cache ``ts`` and ``at`` stamps (instants, not day keys).

Stdlib only (``zoneinfo``; the ``tzdata`` wheel supplies the zone files where
the OS has none).
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
# Plan 23 A1: what one day may be raised by, in total, on top of the daily cap
# (an "allow more for today" grant; it ends with the day).
DAY_EXTRA_MAX_USD = 25.00
GRANTS_KEPT = 200

PROFILE_WHEN_FREE = "free"
PROFILE_WHEN_PAID = "one_dollar"
PROFILE_NAMES = ("free", "one_dollar", "quality", "native_speech", "native_speech_manual", "own_gpu")

ENV_NAMES = ("ALLOW_PAID", "PER_EPISODE_CAP_USD", "DAILY_CAP_USD", "PER_STORY_CAP_USD", "BUDGET_PROFILE")

# Plan 23 A7: the zone of the budget day. Not one of ENV_NAMES (module docstring).
TIMEZONE_ENV = "BUDGET_TIMEZONE"
DEFAULT_TIMEZONE = "UTC"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PROFILES_PATH = os.path.join(_ROOT, "clipping", "aistory", "templates", "budget_profiles.json")

Budget = namedtuple("Budget", "allow_paid per_episode_cap_usd daily_cap_usd per_story_cap_usd profile")
DayState = namedtuple("DayState", "day zone spent extra")


class BudgetRefused(Exception):
    """A paid call the caps or the switch do not allow. The message has the numbers.

    Plan 23 A4: the numbers are attributes too, so a caller can act on them
    without reading the text back. ``cap`` names what refused (``"allow_paid"``,
    ``"episode"``, ``"day"`` or ``"story"``; None for a refusal raised without
    them), ``usd`` the estimate, ``spent`` what that limit already holds
    (today's total for ``allow_paid`` and ``day``), ``cap_usd`` the limit
    (the saved daily cap for ``allow_paid`` and ``day``, never with the extra
    added) and ``extra`` today's allowance on top of the daily cap (0.0 when
    none). The text is DEC-097's, unchanged.
    """

    CAPS = ("allow_paid", "episode", "day", "story")

    def __init__(self, message="", *, cap=None, usd=0.0, spent=0.0, cap_usd=0.0, extra=0.0):
        super().__init__(message)
        self.cap = cap
        self.usd = float(usd or 0.0)
        self.spent = float(spent or 0.0)
        self.cap_usd = float(cap_usd or 0.0)
        self.extra = float(extra or 0.0)

    def as_dict(self) -> dict:
        """``{cap, usd, spent, cap_usd, extra}``, rounded for a JSON body."""
        return {"cap": self.cap, "usd": round(self.usd, 6), "spent": round(self.spent, 4),
                "cap_usd": round(self.cap_usd, 4), "extra": round(self.extra, 4)}


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
# Plan 22 (the native-speech profile): ``speech`` -- each character line is
# spoken by its own clip, the clip's sound heard in place of the line, the
# narrator's TTS voice-over over the silent shots (``media_policy.native_speech``),
# with ``video_link_policy: speech_by_shot`` -- a speaking shot on the
# profile's ``speech_links[speech_model]``, a silent shot on its
# ``silent_link``. Kept apart from the two tuples above (their values are
# pinned where the phase-7 modes are tested); a profile may name any value of
# :data:`TIER3_AUDIO_VALUES` / :data:`VIDEO_LINK_POLICY_VALUES`.
SPEECH_AUDIO_MODE = "speech"
SPEECH_LINK_POLICY = "speech_by_shot"
TIER3_AUDIO_VALUES = TIER3_AUDIO_MODES + (SPEECH_AUDIO_MODE,)
VIDEO_LINK_POLICY_VALUES = VIDEO_LINK_POLICIES + (SPEECH_LINK_POLICY,)
# The speaking clips' model of a native-speech profile (``speech_model``, and
# the story's own ``generation_profile.speech_model``): a key of its
# ``speech_links``. Each link is a ``provider/model`` label resolved by name
# when a clip is planned -- never checked against the providers here, so a
# link added later (``manual/upload``) is named before it exists.
SPEECH_MODELS = ("lite", "fast", "premium")
SPEECH_RETAKE_KEYS = ("max_per_shot", "cap_usd")
SPEECH_RETAKE_MAX = 3
_LINK_LABEL = re.compile(r"^[a-z][a-z0-9_-]*/[^\s,*]+$")
# ``lipsync`` (DEC-258): whether a fully animated v2 story's clips get their
# characters' lips moved to the dialogue after they are bought -- ``none``, or
# ``kling`` (Kling LipSync on fal, LIPSYNC_CHAIN). A profile without the key
# never lipsyncs.
LIPSYNC_MODES = ("none", "kling")
# Plan 32 stage 8 (DEC-315 §6): ``talking_clips`` -- whether a speaking shot's
# clip is made by a talking-clip model (a keyframe and the shot's dialogue
# track: the mouth follows the voice) instead of the episode's video link:
# ``none``, or ``runpod_s2v`` (``runpod/s2v_wan22`` on the human's own GPU).
# A profile without the key never makes one (``media_policy.talking_clips``).
TALKING_CLIPS_MODES = ("none", "runpod_s2v")
# Phase 8 stage B: ``keyframe_fix`` -- a v2 story's keyframes flagged by the
# keyframe check (J2) are redrawn by the assets step, at most
# ``max_redraws_per_shot`` times a shot and ``cap_usd`` an episode. A profile
# without it never redraws on its own. Plan 28 F1 (DEC-305 §5, the keyframe
# check a hard gate): ``cap_rule`` instead of ``cap_usd`` sizes the episode's
# budget at run time -- ``shots_x_redraws_x_price``: the episode's shots x
# ``max_redraws_per_shot`` x one keyframe on the episode's image link
# (``media_policy.keyframe_fix_cap``), so every shot can use its redraws
# whatever the shot count. One of the two, never both.
KEYFRAME_FIX_KEYS = ("max_redraws_per_shot", "cap_usd")
KEYFRAME_FIX_CAP_RULE = "shots_x_redraws_x_price"
KEYFRAME_FIX_CAP_RULES = (KEYFRAME_FIX_CAP_RULE,)
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
    for key, known in (("video_link_policy", VIDEO_LINK_POLICY_VALUES), ("tier3_native_audio", TIER3_AUDIO_VALUES),
                       ("lipsync", LIPSYNC_MODES), ("talking_clips", TALKING_CLIPS_MODES)):
        if key in profile and profile[key] not in known:
            errors.append(f"profile {name!r}: {key} must be one of {', '.join(known)}, not {profile[key]!r}")
    if "keyframe_fix" in profile:
        errors.extend(_keyframe_fix_errors(name, profile["keyframe_fix"]))
    chain = profile.get("tts_chain")
    if chain is not None and (not isinstance(chain, list) or not chain or not all(_is_link(link) for link in chain)):
        # Plan 32 stage 6: the TTS links a story on this profile is voiced on, in order (the cast step pins the
        # first that can run); checked for their shape only, like the speech links.
        errors.append(f"profile {name!r}: tts_chain must be a non-empty list of provider/model links")
    errors.extend(_speech_errors(name, profile))
    return errors


def _is_link(value) -> bool:
    return isinstance(value, str) and _LINK_LABEL.match(value) is not None


def _speech_errors(name, profile) -> list:
    """What is wrong with profile *name*'s native-speech keys (plan 22):
    ``speech_links`` (each of :data:`SPEECH_MODELS` a ``provider/model``
    label), ``speech_model`` (one of them), ``silent_link`` (a label) and
    ``speech_retake`` (``max_per_shot`` 0 to :data:`SPEECH_RETAKE_MAX`,
    ``cap_usd`` >= 0); a ``speech_by_shot`` profile needs the first three.
    The labels are checked for their shape only: the links are resolved by
    name when a clip is planned."""
    where = f"profile {name!r}"
    errors = []
    links = profile.get("speech_links")
    if links is not None:
        if not isinstance(links, dict) or sorted(links) != sorted(SPEECH_MODELS):
            errors.append(f"{where}: speech_links must map exactly {', '.join(SPEECH_MODELS)} to links")
        else:
            bad = [model for model in SPEECH_MODELS if not _is_link(links[model])]
            if bad:
                errors.append(f"{where}: speech_links.{bad[0]} must be a provider/model link, not {links[bad[0]]!r}")
    if "speech_model" in profile and profile["speech_model"] not in SPEECH_MODELS:
        errors.append(f"{where}: speech_model must be one of {', '.join(SPEECH_MODELS)}, not "
                      f"{profile['speech_model']!r}")
    if "silent_link" in profile and not _is_link(profile["silent_link"]):
        errors.append(f"{where}: silent_link must be a provider/model link, not {profile['silent_link']!r}")
    if profile.get("video_link_policy") == SPEECH_LINK_POLICY:
        missing = [key for key in ("speech_links", "speech_model", "silent_link") if key not in profile]
        if missing:
            errors.append(f"{where}: video_link_policy {SPEECH_LINK_POLICY!r} needs {', '.join(missing)}")
    retake = profile.get("speech_retake")
    if retake is not None:
        if not isinstance(retake, dict) or sorted(retake) != sorted(SPEECH_RETAKE_KEYS):
            errors.append(f"{where}: speech_retake must hold exactly {', '.join(SPEECH_RETAKE_KEYS)}")
        else:
            count, cap = retake["max_per_shot"], retake["cap_usd"]
            if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= SPEECH_RETAKE_MAX:
                errors.append(f"{where}: speech_retake.max_per_shot must be a whole number from 0 to "
                              f"{SPEECH_RETAKE_MAX}, not {count!r}")
            if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap < 0:
                errors.append(f"{where}: speech_retake.cap_usd must be an amount in USD (0 or more), not {cap!r}")
    return errors


def _keyframe_fix_errors(name, fix) -> list:
    """What is wrong with profile *name*'s optional ``keyframe_fix``:
    exactly ``max_redraws_per_shot`` (a whole number, 0 to
    :data:`KEYFRAME_FIX_MAX_REDRAWS`) and ``cap_usd`` (an amount >= 0) --
    or, plan 28 F1, ``cap_rule`` (one of :data:`KEYFRAME_FIX_CAP_RULES`)
    in place of ``cap_usd``."""
    where = f"profile {name!r}: keyframe_fix"
    if not isinstance(fix, dict):
        return [f"{where} must be an object {{max_redraws_per_shot, cap_usd}}"]
    errors = []
    ruled = ("max_redraws_per_shot", "cap_rule")
    if sorted(fix) not in (sorted(KEYFRAME_FIX_KEYS), sorted(ruled)):
        errors.append(f"{where} must hold exactly {', '.join(KEYFRAME_FIX_KEYS)} (it holds "
                      f"{', '.join(sorted(map(str, fix))) or 'nothing'})")
    if "cap_rule" in fix and fix["cap_rule"] not in KEYFRAME_FIX_CAP_RULES:
        errors.append(f"{where}.cap_rule must be one of {', '.join(KEYFRAME_FIX_CAP_RULES)}, not {fix['cap_rule']!r}")
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


def check(estimate, link=None, *, budget: Budget, day_spent=0.0, ep_spent=0.0, story_spent=0.0,
          day_extra=0.0) -> None:
    """Raise :class:`BudgetRefused` unless *estimate* may be spent right now.

    A free call (``$0.00``) is never refused: the budget governs money, not
    requests. Every refusal names the estimate, the cap and what is already
    spent, so the user can decide with the numbers in front of them.

    *day_extra* is today's allowance on top of the daily cap (plan 23 A1); with
    none, the refusal text is exactly the one DEC-097 pinned.
    """
    usd = _usd(estimate)
    if usd <= 0:
        return
    label = _label(estimate, link)
    day_extra = max(0.0, float(day_extra or 0.0))
    day = {"spent": day_spent, "cap_usd": budget.daily_cap_usd}
    if not budget.allow_paid:
        raise BudgetRefused(
            f"refused: est ${usd:.3f} on {label}; allow_paid is off "
            f"(today ${day_spent:.2f} of ${budget.daily_cap_usd:.2f})",
            cap="allow_paid", usd=usd, extra=day_extra, **day,
        )
    if ep_spent + usd > budget.per_episode_cap_usd:
        raise BudgetRefused(
            f"refused: est ${usd:.3f} on {label} would bring this episode to "
            f"${ep_spent + usd:.2f} of its ${budget.per_episode_cap_usd:.2f} cap",
            cap="episode", usd=usd, spent=ep_spent, cap_usd=budget.per_episode_cap_usd, extra=day_extra,
        )
    if day_spent + usd > budget.daily_cap_usd + day_extra:
        allowed = f" + ${day_extra:.2f} allowed today" if day_extra > 0 else ""
        raise BudgetRefused(
            f"refused: est ${usd:.3f} on {label} would bring today to "
            f"${day_spent + usd:.2f} of the ${budget.daily_cap_usd:.2f} daily cap{allowed}",
            cap="day", usd=usd, extra=day_extra, **day,
        )
    if story_spent + usd > budget.per_story_cap_usd:
        raise BudgetRefused(
            f"refused: est ${usd:.3f} on {label} would bring this story to "
            f"${story_spent + usd:.2f} of its ${budget.per_story_cap_usd:.2f} cap",
            cap="story", usd=usd, spent=story_spent, cap_usd=budget.per_story_cap_usd, extra=day_extra,
        )


def ceil_cent(usd) -> float:
    """*usd* rounded up to the next cent (never below 0.00): what to allow so
    a refused amount fits. Float noise below a millionth of a cent does not
    round up (``8.98 - 4.00`` is $4.98, not $4.99)."""
    cents = math.ceil(round(float(usd) * 100, 6))
    return max(0, cents) / 100


# -------------------------------------------------------------- the day

_settings_reader = None
# (name, tzinfo, error) of the last zone resolved; None until the first read.
_zone_cache = None
_ZONE_LOCK = threading.Lock()
# Why the configured BUDGET_TIMEZONE could not be used (None when it could, or
# when none is set); the day is UTC meanwhile. The API reports it.
zone_error = None


def set_settings_reader(fn) -> None:
    """Register *fn*, a callable returning the Settings mapping (name ->
    value) the running process uses: the web worker's ``get_settings_env``,
    the story CLI's ``_settings_env``. :func:`day_zone` reads
    ``BUDGET_TIMEZONE`` from it first, then from the process environment.
    None unregisters."""
    global _settings_reader
    _settings_reader = fn


def zone_setting() -> str:
    """The configured ``BUDGET_TIMEZONE``, stripped: the Settings value (via
    the registered reader), else the process environment's, else ``""``."""
    value = None
    reader = _settings_reader
    if reader is not None:
        try:
            value = (reader() or {}).get(TIMEZONE_ENV)
        except Exception:
            value = None
    if value in (None, ""):
        value = os.environ.get(TIMEZONE_ENV, "")
    return str(value or "").strip()


def check_zone_name(name) -> str:
    """*name* stripped when it is an IANA time zone (``ZoneInfo`` knows it),
    ``""`` for an empty one; ``ValueError`` naming the variable otherwise."""
    text = str(name or "").strip()
    if not text:
        return ""
    try:
        ZoneInfo(text)
    except Exception:
        raise ValueError(f"{TIMEZONE_ENV} must be an IANA time zone such as Europe/Paris, not {text!r}") from None
    return text


def _resolve_zone(name: str):
    """``(name in force, tzinfo, error)`` for the configured *name*."""
    if not name or name == DEFAULT_TIMEZONE:
        return DEFAULT_TIMEZONE, timezone.utc, None
    try:
        return name, ZoneInfo(check_zone_name(name)), None
    except ValueError as exc:
        return DEFAULT_TIMEZONE, timezone.utc, f"{exc}; the budget day is UTC until it is fixed"


def _zone():
    """``(name, tzinfo)`` of the budget day, resolved once per setting value."""
    global _zone_cache, zone_error
    name = zone_setting()
    cached = _zone_cache
    if cached is not None and cached[0] == name:
        return cached[1], cached[2]
    with _ZONE_LOCK:
        resolved, tzinfo, error = _resolve_zone(name)
        _zone_cache = (name, resolved, tzinfo)
        zone_error = error
        if error:
            print(f"[budget] {error}")
        return resolved, tzinfo


def day_zone():
    """The tzinfo of the budget day: ``ZoneInfo(BUDGET_TIMEZONE)``, UTC when
    unset or invalid (see :data:`zone_error`). Cached per setting value."""
    return _zone()[1]


def zone_name() -> str:
    """The name of the budget day's zone in force (``"UTC"`` by default and
    while the configured one is invalid)."""
    return _zone()[0]


def _instant(value):
    """*value* (an ISO timestamp, an epoch second or a datetime) as an aware
    datetime; a naive one is UTC. None when it cannot be read."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, (int, float)):
        try:
            moment = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def day_key_at(when):
    """The budget day (``YYYY-MM-DD`` in the configured zone) the instant
    *when* belongs to -- an ISO timestamp (naive = UTC) or an epoch second.
    None for an empty or unreadable value (a caller then means today)."""
    moment = _instant(when)
    if moment is None:
        return None
    return moment.astimezone(day_zone()).strftime("%Y-%m-%d")


def day_began_at(day: str) -> float:
    """The epoch second at which the budget day *day* (``YYYY-MM-DD``) began
    in the configured zone."""
    began = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=day_zone())
    return began.timestamp()


def _next_midnight(now: float) -> str:
    zone = day_zone()
    local = datetime.fromtimestamp(now, tz=zone)
    following = local.date() + timedelta(days=1)
    return datetime(following.year, following.month, following.day, tzinfo=zone).isoformat()


# ------------------------------------------------------------ daily spend

def default_spend_path() -> str:
    return os.environ.get("SPEND_PATH") or os.path.join(_ROOT, "data", "spend.json")


class DailySpend:
    """Paid dollars per budget day (``BUDGET_TIMEZONE``, UTC by default), on
    disk (``spend_v1``). Never the free counters' file. *time_fn* is the clock
    (epoch seconds), a seam for tests."""

    SCHEMA = "spend_v1"

    def __init__(self, path, *, time_fn=None):
        self.path = path
        self._time = time_fn or time.time
        self._lock = threading.Lock()

    def today(self) -> str:
        """Today's key, ``YYYY-MM-DD`` in the budget day's zone."""
        return datetime.fromtimestamp(self._time(), tz=day_zone()).strftime("%Y-%m-%d")

    def next_reset(self) -> str:
        """When today ends: the next local midnight in the budget day's zone, ISO with its offset."""
        return _next_midnight(self._time())

    def _note_zone(self, data: dict) -> None:
        """Record the zone this write is keyed in (module docstring): the
        first write under a new zone sets ``zone`` and appends one
        ``zone_changes`` entry; the days already stored are left as they are.
        A file without ``zone`` was written on UTC days."""
        zone = zone_name()
        before = str(data.get("zone") or DEFAULT_TIMEZONE)
        if before == zone:
            return
        changes = data.get("zone_changes")
        changes = changes if isinstance(changes, list) else []
        changes.append({"at": self._now_iso(), "from": before, "to": zone})
        data["zone_changes"] = changes
        data["zone"] = zone

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
            self._note_zone(data)
            data["updated_at"] = self._now_iso()
            self._write(data)
            return total

    def _now_iso(self) -> str:
        return datetime.fromtimestamp(self._time(), tz=timezone.utc).isoformat()

    @staticmethod
    def _extra_map(data: dict) -> dict:
        extra = data.get("extra")
        return extra if isinstance(extra, dict) else {}

    def extra_today(self) -> float:
        """What today may be raised by, on top of the daily cap (0.0 when nothing was allowed)."""
        with self._lock:
            return float(self._extra_map(self._load()).get(self.today(), 0.0) or 0.0)

    def day_state(self) -> DayState:
        """Today's day, the budget day's zone in force, the paid total and the
        extra, read from the file once."""
        with self._lock:
            data = self._load()
            day = self.today()
            return DayState(
                day,
                zone_name(),
                float(data["days"].get(day, 0.0)),
                float(self._extra_map(data).get(day, 0.0) or 0.0),
            )

    def add_extra(self, usd: float, *, story_id=None, note="") -> float:
        """Allow *usd* more for today only, on top of any earlier grant of the
        same day. Refuses ``usd <= 0`` and a day total over ``DAY_EXTRA_MAX_USD``
        (``ValueError``, nothing written). Logs the grant (last ``GRANTS_KEPT``).
        Returns today's extra."""
        usd = float(usd)
        if not usd > 0:
            raise ValueError(f"a daily extra is a positive amount, not {usd!r}")
        with self._lock:
            data = self._load()
            day = self.today()
            extra = self._extra_map(data)
            total = round(float(extra.get(day, 0.0) or 0.0) + usd, 4)
            if total > DAY_EXTRA_MAX_USD:
                raise ValueError(
                    f"today's extra would be ${total:.2f}, over the ${DAY_EXTRA_MAX_USD:.2f} ceiling"
                )
            extra[day] = total
            data["extra"] = extra
            self._note_zone(data)
            data.setdefault("zone", zone_name())
            grants = data.get("grants")
            grants = grants if isinstance(grants, list) else []
            grants.append({"day": day, "at": self._now_iso(), "usd": round(usd, 4),
                           "story_id": story_id, "note": str(note or "")})
            data["grants"] = grants[-GRANTS_KEPT:]
            data["updated_at"] = self._now_iso()
            self._write(data)
            return total

    def grants_today(self) -> list:
        """The grants logged for today, oldest first (copies)."""
        with self._lock:
            grants = self._load().get("grants")
            day = self.today()
        if not isinstance(grants, list):
            return []
        return [dict(g) for g in grants if isinstance(g, dict) and g.get("day") == day]

    def clear_extra(self) -> float:
        """Take today's extra back (the grants log keeps what was allowed).
        Returns the amount that was cleared."""
        with self._lock:
            data = self._load()
            extra = self._extra_map(data)
            day = self.today()
            held = float(extra.pop(day, 0.0) or 0.0)
            if held == 0.0:
                return 0.0
            data["extra"] = extra
            self._note_zone(data)
            data["updated_at"] = self._now_iso()
            self._write(data)
            return held

    def release(self, usd: float, *, day=None) -> float:
        """Give back *usd* booked on *day* (``YYYY-MM-DD``, a budget day --
        :func:`day_key_at` of the booking's instant; today when None): a
        booking proven unbilled. Never below zero -- a day is never given back
        more than it holds, nor anything when it holds nothing (a pre-switch
        booking keyed onto the neighbour day, module docstring). Returns that
        day's total."""
        usd = float(usd)
        if usd < 0:
            raise ValueError(f"a release gives back a booked amount, not {usd!r}")
        with self._lock:
            data = self._load()
            day = day or self.today()
            held = float(data["days"].get(day, 0.0))
            if day not in data["days"] or usd == 0:
                return round(held, 4)
            total = round(max(0.0, held - usd), 4)
            data["days"][day] = total
            self._note_zone(data)
            data["updated_at"] = self._now_iso()
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
    """Drop the cached default spend store, the settings reader and the
    resolved zone (and its error). For tests: ``tests/conftest.py`` calls it
    around every test so no module state leaks between them."""
    global _DEFAULT_SPEND, _settings_reader, _zone_cache, zone_error
    with _DEFAULT_SPEND_LOCK:
        _DEFAULT_SPEND = None
    with _ZONE_LOCK:
        _settings_reader = None
        _zone_cache = None
        zone_error = None


def today(*, spend=None) -> str:
    """Today's budget day key (``YYYY-MM-DD`` in ``BUDGET_TIMEZONE``), on
    the spend store's clock."""
    return (spend or default_spend()).today()


def next_reset(*, spend=None) -> str:
    """When the budget day ends: the next local midnight in its zone, ISO
    with the zone's offset, on the spend store's clock."""
    return (spend or default_spend()).next_reset()


def day_spent(*, spend=None) -> float:
    return (spend or default_spend()).today_total()


def day_state(*, spend=None) -> DayState:
    """Today's day, zone, paid total and extra, from one read of the spend file."""
    return (spend or default_spend()).day_state()


def record(estimate, *, spend=None) -> float:
    """Book a paid estimate against today. Returns today's total; a free one books nothing."""
    usd = _usd(estimate)
    store = spend or default_spend()
    if usd <= 0:
        return store.today_total() if os.path.exists(store.path) else 0.0
    return store.add(usd)


def release(estimate, *, day=None, spend=None) -> float:
    """Give back a paid estimate :func:`record` booked on *day* (a budget day
    ``YYYY-MM-DD``, :func:`day_key_at` of the booking; today when None) once
    the request is proven unbilled
    (the generation journal's ``void``). Never below zero. Returns that day's
    total; a free one gives back nothing."""
    usd = _usd(estimate)
    store = spend or default_spend()
    if usd <= 0:
        return store.today_total() if os.path.exists(store.path) else 0.0
    return store.release(usd, day=day)
