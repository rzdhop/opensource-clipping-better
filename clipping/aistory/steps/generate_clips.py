"""The Generate button and an honest price before the click (plan 28 stage
A6; DEC-305 section 1, the human's "me + gen button").

The human makes a native-speech story's clips on their own subscriptions by
default; the app buys an API clip only on a click that shows its real price,
and refuses in plain words -- nothing bought -- when it cannot:

- :func:`plan_video_units` -- the one-click estimate's clips before the
  storyboard exists: the shots the script's stored line plans buy (else the
  shots the template's fitted plan would buy, ``timing.plan_floor_preview``),
  each at its planned length times its link's price a second -- a speaking
  shot on the story's speech link, a silent one on its silent link, the
  human's own link at $0 -- plus the retake budget a bought speaking clip
  may spend, in the shape of ``clips.video_units`` so the fast track's paid
  check and the caps read it as they read the storyboard's;
- :func:`quote` -- what "Generate this clip" and "Generate all missing clips"
  buy: the human's shots whose clip is still missing, set to ``auto`` in a
  copy of ``assets.json`` and priced by the assets step's own clip gate
  (``assets._video_units``: each shot on the link its mode puts it on, a
  clip that is current or booked at $0, the keyframes' hold) and the caps
  (``assets.spending_caps``), calling nothing;
- :func:`warnings` -- what makes a click fail or check less, said before
  it: no speech-to-text key (the take is never checked), and the provider's
  own refusal of the story's last image or clip run (a balance spent, an
  account locked), read from the story's activity log.

Stdlib only (DEC-012); reads documents and the activity log, writes nothing.
"""

from __future__ import annotations

import copy
import os
import re

from clipping.providers import gating

from .. import media_policy, recipes, timing
from .. import store as store_mod
from . import assets as assets_step
from . import clips as clips_step

# The pre-click warning of a story whose takes no STT link can check.
STT_WARNING = "No speech check: add a {keys} in Settings (free)"

# The steps whose runs make images or clips (their activity-log lines name the job's step).
_GENERATING_STEPS = ("assets", "fast-track", "story-fast-track", "regenerate", "cast", "places", "style_preview")
# An activity-log line: "<timestamp> [<job id> <step>] <text>".
_ACTIVITY_LINE = re.compile(r"^\S+ \[([0-9a-f]+) ([a-z_-]+)\] (.*)$")
# A provider's own refusal of a spent balance or a locked account (fal: "User is locked. Reason: TOP_UP.",
# "Exhausted balance"; other hosts: "insufficient balance/credits").
_REFUSAL = re.compile(r"(User is locked\. Reason: [A-Za-z_ ]+\.|[Ee]xhausted balance[^;()|]*|"
                      r"[Ii]nsufficient (?:balance|credits?|funds)[^;()|]*)")
_LINK = re.compile(r"\b([a-z0-9][a-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9._-]*)")
# How much of the activity log is read (its tail): the last runs, never the whole history.
_TAIL_BYTES = 64 * 1024


def _and(items) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _s(count) -> str:
    return "" if count == 1 else "s"


def _usd(value) -> str:
    return f"${float(value):.2f}"


# ------------------------------------------------------------------ warnings

def stt_warning(env):
    """:data:`STT_WARNING` when no hosted speech-to-text link of
    ``STT_CHAIN`` has its key (``media_policy.stt_missing_keys``), else None."""
    keys = media_policy.stt_missing_keys(gating.merged_env(env))
    return STT_WARNING.format(keys=" or ".join(keys)) if keys else None


def _activity_tail(store, story_id) -> list:
    """The last lines of *story_id*'s ``activity.log`` (best effort: [] when
    it cannot be read; a symlink is never followed)."""
    try:
        path = os.path.join(store.story_dir(story_id), store_mod.ACTIVITY_LOG)
        if os.path.islink(path) or not os.path.isfile(path):
            return []
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            data = fh.read()
    except (OSError, KeyError, ValueError):
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if len(data) >= _TAIL_BYTES else lines


def provider_refusal(store, story_id):
    """The provider's own refusal of *story_id*'s last image or clip run, as
    a pre-click warning -- ``fal/seedream-4.5-edit refused the last run:
    “User is locked. Reason: TOP_UP.” Top up that account, or pick another
    link, before you generate.`` -- or None: the last run of a generating
    step (:data:`_GENERATING_STEPS`) in the activity log met no spent
    balance and no locked account (:data:`_REFUSAL`)."""
    jobs = []
    for line in _activity_tail(store, story_id):
        match = _ACTIVITY_LINE.match(line)
        if match and match.group(2) in _GENERATING_STEPS:
            jobs.append((match.group(1), match.group(3)))
    if not jobs:
        return None
    last = jobs[-1][0]
    for job_id, text in reversed(jobs):
        if job_id != last:
            continue
        found = _REFUSAL.search(text)
        if found:
            reason = found.group(1).strip()
            link = _LINK.search(text)
            who = link.group(1) if link else "The provider"
            return (f"{who} refused the last run: “{reason}” Top up that account, or pick another link, before you "
                    "generate.")
    return None


def warnings(ec, env) -> list:
    """The pre-click warnings of *ec*'s episode: :func:`stt_warning` on a
    native-speech story, then :func:`provider_refusal`."""
    out = []
    if media_policy.native_speech(ec.story):
        stt = stt_warning(env)
        if stt:
            out.append(stt)
    refused = provider_refusal(ec.store, ec.story_id)
    if refused:
        out.append(refused)
    return out


# --------------------------------------------------- the estimate's clips

def planned_shots(ec, script) -> list:
    """``[(clip_s, speaks), ...]``: the clips *script*'s stored line plans
    buy (``timing.plan_board``), else -- no script, or one without plans --
    the ones the template's fitted plan would buy on this story's links
    (``timing.plan_floor_preview``); [] off native speech, or when no part of
    the format can be planned."""
    board = timing.plan_board(script) if script else None
    if board is not None:
        return [(int(shot["duration_s"]), bool(shot["speaks"])) for shot in board["shots"]]
    if not media_policy.native_speech(ec.story):
        return []
    card = recipes.ends_on_card(ec.story, ec.episode_defaults.get("cliffhanger_style"))
    preview = timing.plan_floor_preview(ec.template, ec.ep, clips_step.speech_lengths(ec.story), bool(ec.narrator),
                                        lang=ec.language, style_lock=ec.style_lock, end_card=card)
    return [(int(clip_s), bool(speaks)) for clip_s, speaks in preview.get("shots") or ()]


def plan_video_units(ec, script, *, env, adapters=None):
    """The clips of a native-speech episode before its storyboard exists,
    priced from its plan (:func:`planned_shots`), in the shape of
    ``clips.video_units`` (``count``, ``seconds``, ``est_usd``, ``link``,
    ``route_class``, ``ready``, ``refused``, ``message``, ``speech``) plus
    ``basis: "plan"``: each speaking shot at the speech link's price a second,
    each silent one at the silent link's, the human's own link at $0, and the
    retake budget when a speaking clip is bought. ``ready`` false (with
    ``refused``) when a link that would be paid cannot run now (no key, no
    adapter, no price). None off native speech or with no plan."""
    if not media_policy.native_speech(ec.story):
        return None
    shots = planned_shots(ec, script)
    if not shots:
        return None
    if adapters is None:
        from clipping.providers import adapters as adapters_mod

        adapters_mod.load_all()
    merged = gating.merged_env(env)
    resolution = media_policy.video_resolution(ec.story)
    speech_label, silent_label = media_policy.speech_link(ec.story), media_policy.silent_link(ec.story)
    rows = {label: clips_step.link_row(label, merged, adapters, resolution=resolution,
                                       aspect=media_policy.aspect(ec.story))
            for label in dict.fromkeys((speech_label, silent_label))}
    prices = {}
    for label, row in rows.items():
        if row.get("manual"):
            prices[label] = 0.0
        else:
            price, _why = media_policy.link_price(label, resolution) if label else (None, None)
            prices[label] = price
    speech = [clip_s for clip_s, speaks in shots if speaks]
    silent = [clip_s for clip_s, speaks in shots if not speaks]
    speech_usd = sum(speech) * float(prices[speech_label] or 0.0)
    silent_usd = sum(silent) * float(prices[silent_label] or 0.0)
    retake = clips_step.retake_budget(ec, None)
    bought_speech = bool(speech) and not rows[speech_label].get("manual")
    retake_usd = retake["left_usd"] if retake["max_per_shot"] > 0 and bought_speech else 0.0
    used = [label for label, count in ((speech_label, speech), (silent_label, silent)) if count]
    manual = all(rows[label].get("manual") for label in used)
    refused = None
    for label in used:
        if rows[label]["status"] != "keyed" or prices[label] is None:
            refused = f"{label or 'no link'}: {rows[label]['reason'] or 'no price a second'}"
            break
    est = round(speech_usd + silent_usd + retake_usd, 4)
    seconds = sum(clip_s for clip_s, _speaks in shots)
    count = len(shots)
    if manual:
        message = f"{count} clip{_s(count)} ({seconds} s) planned, made by you: nothing is bought."
    else:
        message = (f"{count} clip{_s(count)} ({seconds} s) planned: {sum(speech)} s on {speech_label} and "
                   f"{sum(silent)} s on {silent_label}, est {_usd(est)} (from the plan, before the storyboard).")
    return {
        "tier": clips_step.tier_of(ec), "count": count, "seconds": int(seconds), "est_usd": est, "plan": [],
        "route_class": "manual" if manual else "paid", "link": speech_label, "ready": refused is None,
        "refused": refused, "animate": True, "basis": "plan", "message": message,
        "speech": {"speech_link": speech_label, "silent_link": silent_label, "speech_count": len(speech),
                   "silent_count": len(silent), "speech_seconds": int(sum(speech)),
                   "silent_seconds": int(sum(silent)), "speech_price": prices[speech_label],
                   "silent_price": prices[silent_label], "speech_usd": round(speech_usd, 4),
                   "silent_usd": round(silent_usd, 4), "retake_usd": round(retake_usd, 4)},
    }


# ------------------------------------------------------------ the Generate button

def missing_own(handoff) -> list:
    """The shot ids of a handoff document (``brief.handoff``) whose clip is
    the human's own (``mode`` ``manual``) and still missing: what "Generate
    all missing clips" buys."""
    return [shot["shot_id"] for shot in handoff.get("shots") or ()
            if (shot.get("clip") or {}).get("mode") == "manual" and (shot.get("clip") or {}).get("state") == "missing"]


def _refusal(ec, video, rows, est, *, env, ledger) -> str:
    """Why the clips *rows* of *video* (each ``clips.video_units``' plan row)
    cannot be bought for *est* now, in plain words, or None."""
    if video.get("hold"):
        return f"Not yet: {video['hold']}."
    status = {row["link"]: row for row in video.get("links") or ()}
    for row in rows:
        link = status.get(row["link"]) or {}
        if link.get("status") != "keyed":
            return f"{row['link'] or 'No link'} cannot make it now: {link.get('reason') or 'it cannot run'}."
    caps, over = assets_step.spending_caps(ec, est, env=env, ledger=ledger)
    if est > 0 and not caps.get("allow_paid"):
        return "Paid generation is off: turn allow_paid on in Settings, or make it yourself."
    if over:
        return f"Over a cap, so nothing is bought: {over}."
    return None


def quote(ec, script, board, doc, shot_ids, *, env, adapters=None, ledger=None) -> dict:
    """What generating the clips of *shot_ids* (the human's own, still
    missing) would buy now, calling nothing: those shots set to ``auto`` in
    a copy of *doc* (``assets.json``) and priced by the assets step's clip
    gate (``assets._video_units``) and the caps::

        {"shots": {shot_id: {"usd", "link", "allowed", "reason"}},
         "all": {"usd", "count", "shot_ids", "allowed", "reason"}}

    A shot's ``usd`` is its own clip; ``all.usd`` everything the assets step
    would then buy for the clips (every clip it would make, the retake
    budget included). ``reason`` is None when allowed, else one plain
    sentence."""
    ledger = ledger or assets_step._open_ledger(ec)
    trial = copy.deepcopy(doc) if doc else {}
    modes = trial.setdefault("shot_modes", {})
    for shot_id in shot_ids:
        modes[shot_id] = dict(modes.get(shot_id) or {}, clip="auto")
    video = assets_step._video_units(ec, script, board, trial, env=env, ledger=ledger, adapters=adapters,
                                     probe_local=False, transport=None, committed=0.0)
    rows = {row["shot_id"]: row for row in video.get("plan") or () if row["shot_id"] in shot_ids}
    by_id = {shot["shot_id"]: shot for shot in board["shots"]}
    # The clip job's own check of each shot (its keyframe current, not kept still, not still generating).
    blocked = {}
    for shot_id in shot_ids:
        why = assets_step.clip_target_refusal(ec, by_id[shot_id], doc=trial, storyboard=board) \
            if shot_id in by_id else "it is not on the storyboard"
        if why:
            blocked[shot_id] = f"Not yet: {why[0].upper()}{why[1:].rstrip('.')}."
    shots = {}
    for shot_id in shot_ids:
        row = rows.get(shot_id)
        if shot_id in blocked:
            shots[shot_id] = {"usd": round(float((row or {}).get("est_usd") or 0.0), 4),
                              "link": (row or {}).get("link"), "allowed": False, "reason": blocked[shot_id]}
            continue
        if row is None:
            shots[shot_id] = {"usd": 0.0, "link": None, "allowed": False,
                              "reason": video.get("refused") or video.get("message") or "It cannot be planned now."}
            continue
        est = round(float(row["est_usd"]), 4)
        reason = _refusal(ec, video, [row], est, env=env, ledger=ledger)
        shots[shot_id] = {"usd": est, "link": row.get("link"), "allowed": reason is None, "reason": reason}
    total = round(float(video.get("est_usd") or 0.0), 4)
    reason = None
    if blocked:
        reason = next(iter(blocked.values()))
    elif shot_ids:
        reason = _refusal(ec, video, list(rows.values()), total, env=env, ledger=ledger)
        if reason is None and (video.get("refused") or len(rows) < len(shot_ids)):
            reason = f"It cannot be planned now: {video.get('refused') or video.get('message')}."
    return {"shots": shots, "all": {"usd": total, "count": len(shot_ids), "shot_ids": list(shot_ids),
                                    "allowed": bool(shot_ids) and reason is None, "reason": reason}}


def beyond_the_clips(ec, quoted_usd, *, env):
    """Why "Generate all missing clips" may not start the assets step now,
    or None (the money rule: nothing is bought that the button did not
    show): the step's own plan (``assets.asset_units``, the shots already
    switched to auto) would spend more than the *quoted_usd* the button
    showed -- an image, a voice or a redraw to make first -- named with
    its price."""
    script, board = assets_step.require_approved(ec)
    units = assets_step.asset_units(ec, script, board, env=env, animate=True)
    total = float(units.get("est_usd") or 0.0)
    if total <= float(quoted_usd) + 0.005:
        return None
    return (f"Generating every missing clip would also buy {_usd(total - float(quoted_usd))} of keyframes, voices or "
            f"redraws first ({_usd(total)} in all, not the {_usd(quoted_usd)} shown): make those first, then "
            "Generate again.")
