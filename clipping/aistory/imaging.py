"""The wiring every story image shares (AI Story phase 2, stage 5).

Lifted from ``steps/style_preview.py`` (phase 1, stage 8) unchanged in
behaviour, so the style preview strip and the reference images of the cast,
places and props (``refimages.py``) meet the same gates in the same order and
say the same words:

- :func:`estimate` -- a verdict per link of any generation chain, for *qty*
  requests of one size, that calls nothing: the story's route, the adapter,
  the keys; a local link is "probed when it runs"; a paid link needs
  ``allow_paid`` and the caps' verdict (with the story's total so far); a
  free link needs its daily allowance (:func:`verdict` reads the answer off
  those rows, again when a caller learnt more -- a local server that does not
  answer);
- :func:`read_lock`, :func:`resolve`, :func:`open_ledger` -- what must hold
  before anything is spent (a valid style lock, a chain and a budget that
  parse, a ledger that can be read);
- :func:`run_one` -- one request through ``run_generation_chain`` with every
  gate: ``(GenResult, link)``, or :class:`NoImage` carrying each link's
  reason (a paid link refused while paid is off gets the numbers);
- :func:`book` -- the one booking of an answered call: a ledger row, and
  today's spend for a paid one (``run_generation_chain`` books nothing);
- :func:`produced_image` / :func:`keep` -- the answer's file, checked, and an
  atomic copy of it.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time

from clipping.providers import adapters as adapters_mod
from clipping.providers import budget as budget_mod
from clipping.providers import gating, limits
from clipping.providers import generation as gen
from clipping.providers.registry import ChainError

from . import ledger as ledger_mod
from . import media_policy, schemas

LOCK_NAME = "style_lock.json"
LEDGER_NAME = "cost_ledger.json"
KEPT_EXTENSIONS = ("png", "jpg", "jpeg", "webp")

# The runner's own refusal of a paid link while paid is off; ``explain`` adds
# the numbers the runner does not have.
PAID_OFF = "paid link; allow_paid is off"


class NoImage(Exception):
    """No link of the chain made the image; ``reasons`` has one line per link.

    ``failures`` is the chain's raw ``(label, reason)`` pairs
    (``NoRunnableLink.failures``, one per link tried or skipped) -- empty for
    a caller that built this directly, without a chain run behind it."""

    def __init__(self, reasons, failures=()):
        self.reasons = list(reasons)
        self.failures = tuple(failures)
        super().__init__("; ".join(self.reasons))


class NotKept(Exception):
    """The answer carried no image of a kept type; the message says why."""


# ------------------------------------------------------ before anything is spent

def read_lock(stories, story_id, *, error) -> dict:
    """The story's style lock, validated; *error* (an exception class taking
    one message) when there is none or it cannot be used."""
    try:
        lock = stories.read_doc(story_id, LOCK_NAME)
    except schemas.SchemaError as exc:
        first = exc.errors[0] if exc.errors else str(exc)
        raise error(f"{LOCK_NAME} cannot be read ({first}); build the style again.") from None
    if lock is None:
        raise error("Build the style first.")
    errors = schemas.style_lock_errors(lock)
    if errors:
        raise error(f"{LOCK_NAME} is not a valid style lock ({'; '.join(errors[:3])}); "
                    "build the style again.")
    return lock


def resolve(kind, settings_env, *, error, role=None, story=None):
    """``(merged env, chain, budget)`` for *kind*: the Settings values over the
    process environment, the chain they name and the budget they describe.
    *error* when the chain or a cap cannot be used.

    With *role* and *story* (phase 7, DEC-221) the chain is
    ``media_policy.role_chain``'s: the env chain for a legacy story, the
    role's quality links for a v2 one. Without them it is the env chain."""
    merged = gating.merged_env(settings_env)
    try:
        chain = (media_policy.role_chain(role, kind, merged, story) if role is not None
                 else gen.chain_from_env(kind, merged))
    except ChainError as exc:
        raise error(f"{media_policy.chain_name(role, kind, story)} cannot be used: {exc}") from None
    try:
        budget_obj = gating.budget_of(merged)
    except ValueError as exc:
        raise error(f"The budget settings cannot be used: {exc}") from None
    return merged, chain, budget_obj


def open_ledger(stories, story_id, *, error, doing) -> ledger_mod.CostLedger:
    """The story's ledger, checked before anything is spent: its total feeds
    the per-story cap, and ``CostLedger`` would start a torn file afresh on
    the first append -- losing what the story already spent. *doing* ends the
    refusal's sentence ("fix it before <doing>")."""
    path = os.path.join(stories.story_dir(story_id), LEDGER_NAME)
    if os.path.islink(path):
        raise error(f"{LEDGER_NAME} is a symlink, which is never followed, so this story's "
                    "spending cannot be checked; replace it with the file itself.")
    if os.path.lexists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if (not isinstance(data, dict) or data.get("$schema") != ledger_mod.SCHEMA
                    or not isinstance(data.get("entries"), list)):
                raise ValueError(f"not a {ledger_mod.SCHEMA} document")
            sum(float(entry["est_usd"]) for entry in data["entries"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise error(f"{LEDGER_NAME} cannot be read ({type(exc).__name__}: {exc}), so this "
                        f"story's spending cannot be checked; fix it before {doing}.") from None
    return ledger_mod.CostLedger(path)


# --------------------------------------------------------------------- estimate

def allowance_spent(provider):
    """``limits.acquire``'s refusal for *provider*, read without counting a call."""
    limit = limits.limits_from_env().get(provider)
    if limit is None or limit.rpd is None:
        return None
    calls = limits.default_usage().calls(provider)
    if calls >= limit.rpd:
        return f"daily allowance spent ({calls}/{limit.rpd} today, resets at 00:00 UTC)"
    return None


def missing_keys_reason(missing) -> str:
    # The runner's wording, so the estimate and the feed say the same thing.
    return f"no API key ({' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set)"


def estimate_row(kind, link, merged, budget_obj, request, *, qty, route, story_spent, adapters) -> dict:
    """One link, through the runner's gates in the runner's order (route,
    adapter, keys, then local / paid / free), calling nothing."""
    summary = gating.link_summary(kind, link, merged, budget_obj, request, qty=qty,
                                  story_spent=story_spent, adapters=adapters)
    local = link.provider == "local"
    paid = summary["paid"]
    row = {"link": summary["label"], "status": "skipped", "reason": None, "paid": paid,
           "est_usd": summary["est_usd"] if paid else 0.0}
    if route == "local" and not local:
        row["reason"] = "route is local"
    elif route == "api" and local:
        row["reason"] = "route is api"
    elif not summary["adapter"]:
        row["reason"] = f"no adapter yet for {link.provider} {kind}"
    elif summary["missing_keys"]:
        row["reason"] = missing_keys_reason(summary["missing_keys"])
    elif local:
        # Probing is a request; an estimate sends none. The caller probes.
        row.update(status="runnable", reason="probed when it runs")
    elif paid:
        if summary["allowed"]:
            row.update(status="runnable", reason="paid, allowed")
        else:
            row["reason"] = summary["reason"]
            if (summary.get("refusal") or {}).get("cap") == "day":
                # Plan 23 A4: only a daily-cap refusal carries its numbers in the
                # row (the one "allow more for today" can lift); every other row
                # keeps its shape.
                row["refusal"] = summary["refusal"]
    else:
        spent = allowance_spent(link.provider)
        if spent:
            row["reason"] = spent
        else:
            row.update(status="runnable", reason="free")
    return row


def blocked(step, qty, rows, why) -> dict:
    """An estimate no link can satisfy: ``ready`` false, ``message`` *why*."""
    return {
        "step": step, "est_usd": 0.0, "units": {"images": qty}, "route_class": "blocked",
        "link": None, "links": rows, "ready": False, "message": why,
    }


def no_link_message(kind, rows, *, what, route, chain_name=None) -> str:
    """"No link of <CHAIN> can make <what> on route <route>: <each link's reason>."
    *chain_name* names the chain instead of *kind*'s env variable (a v2
    story's role, ``media_policy.chain_name``)."""
    detail = "; ".join(f"{row['link']}: {row['reason']}" for row in rows) or "the chain is empty"
    return f"No link of {chain_name or gen.ENV_NAMES[kind]} can make {what} on route {route}: {detail}."


def _images(qty) -> str:
    return f"{qty} image{'' if qty == 1 else 's'}"


def estimate(kind, settings_env, *, route, request, qty=1, story_spent=0.0, adapters=None,
             step, what, when, role=None, story=None) -> dict:
    """What *qty* requests like *request* on *kind*'s chain would cost and
    where they would run; nothing is called::

        {"step", "est_usd", "units": {"images": qty},
         "route_class": "local" | "free" | "paid" | "blocked",
         "link": <the first runnable link> | null,
         "links": [{"link", "status": "runnable" | "skipped", "reason", "paid", "est_usd"}],
         "ready": bool, "message": str}

    A link is ``runnable`` when the runner would try it: allowed by *route*,
    with an adapter and its keys; a local link is then runnable (its server is
    probed when *when*, never here); a paid link needs ``allow_paid`` and the
    budget's verdict on all *qty* images (with *story_spent* already on the
    story); a free link needs its daily allowance. ``est_usd`` is *qty* times
    the first runnable link's per-image estimate -- 0.0 when it is free or
    local -- and each row's ``est_usd`` is the same for that link.
    ``blocked``: no link can run; ``message`` then names every link's reason
    ("No link of <CHAIN> can make <what> on route <route>: ...").

    *role* and *story* (phase 7): the chain is ``media_policy.role_chain``'s,
    the one :func:`resolve` hands the run (the env chain for a legacy story).
    """
    if adapters is None:
        adapters_mod.load_all()
    merged = gating.merged_env(settings_env)
    name = media_policy.chain_name(role, kind, story)
    try:
        chain = (media_policy.role_chain(role, kind, merged, story) if role is not None
                 else gen.chain_from_env(kind, merged))
    except ChainError as exc:
        return blocked(step, qty, [], f"{name} cannot be used: {exc}")
    try:
        budget_obj = gating.budget_of(merged)
    except ValueError as exc:
        return blocked(step, qty, [], f"The budget settings cannot be used: {exc}")

    rows = [estimate_row(kind, link, merged, budget_obj, request, qty=qty, route=route,
                         story_spent=story_spent, adapters=adapters) for link in chain]
    return verdict(kind, rows, route=route, qty=qty, step=step, what=what, when=when,
                   chain_name=None if name == gen.ENV_NAMES[kind] else name)


def day_refused_rows(rows) -> list:
    """The rows :func:`estimate_row` skipped on the daily cap alone (they
    carry ``refusal``), in chain order."""
    return [row for row in rows if (row.get("refusal") or {}).get("cap") == "day"]


def day_cap_block(rows):
    """``{cap: "day", spent, cap_usd, extra, needed_usd_for_this_call}`` when
    every link the budget refused was refused by the daily cap (and at least
    one was), else None. ``needed_usd_for_this_call`` is what today would
    have to be raised by, to the cent above, for the first such link's
    estimate to fit (plan 23 A4). A link refused by another cap, or while
    ``allow_paid`` is off, is one an extra for today would not let run."""
    day = day_refused_rows(rows)
    other = [row for row in rows if row not in day and row["status"] == "skipped"
             and str(row.get("reason") or "").startswith("refused: ")]
    if not day or other:
        return None
    refusal = day[0]["refusal"]
    needed = refusal["spent"] + refusal["usd"] - refusal["cap_usd"] - refusal["extra"]
    return {"cap": "day", "spent": refusal["spent"], "cap_usd": refusal["cap_usd"], "extra": refusal["extra"],
            "needed_usd_for_this_call": budget_mod.ceil_cent(needed)}


def verdict(kind, rows, *, route, qty, step, what, when, chain_name=None) -> dict:
    """:func:`estimate`'s answer from its rows (:func:`estimate_row`): the
    first runnable link decides the class, the price and the message; none
    is ``blocked``. A caller that learns more than the estimate knew -- a
    local server that does not answer its probe -- marks that row
    ``skipped`` and asks again.

    Plan 23 A4: a ``blocked`` answer whose links were all refused by the
    daily cap also carries ``budget`` (:func:`day_cap_block`); the message is
    :func:`no_link_message`'s, unchanged."""
    first = next((row for row in rows if row["status"] == "runnable"), None)
    if first is None:
        body = blocked(step, qty, rows, no_link_message(kind, rows, what=what, route=route,
                                                        chain_name=chain_name))
        budget = day_cap_block(rows)
        if budget is not None:
            body["budget"] = budget
        return body

    label = first["link"]
    if first["paid"]:
        route_class = "paid"
        message = f"{_images(qty)} on {label}, paid: est ${first['est_usd']:.3f}."
    elif label.startswith("local/"):
        route_class = "local"
        message = (f"{_images(qty)} on {label}, on your own hardware: $0.00. "
                   f"Its server is probed when {when}.")
    else:
        route_class = "free"
        message = f"{_images(qty)} on {label} (free): $0.00."
    later_paid = [row for row in rows[rows.index(first) + 1:] if row["status"] == "runnable" and row["paid"]]
    if not first["paid"] and later_paid:
        most = max(row["est_usd"] for row in later_paid)
        message += (f" If it fails, {', '.join(row['link'] for row in later_paid)} (paid, est up to "
                    f"${most:.3f}) may be reached.")
    return {
        "step": step, "est_usd": first["est_usd"], "units": {"images": qty},
        "route_class": route_class, "link": label, "links": rows, "ready": True, "message": message,
    }


# -------------------------------------------------------------------------- run

def explain(kind, label, reason, *, chain, merged, budget_obj, request, adapters) -> str:
    """A chain reason, verbatim; a paid link refused while paid is off also
    gets the numbers, which the runner does not have."""
    link = next((link for link in chain if gen.describe(link) == label), None)
    if reason == PAID_OFF and link is not None:
        est = gating.link_summary(kind, link, merged, budget_obj, request, adapters=adapters)["est_usd"]
        return (f"{label}: {reason} (est ${est:.3f} per image; today ${budget_mod.day_spent():.2f} "
                f"of the ${budget_obj.daily_cap_usd:.2f} daily cap)")
    return f"{label}: {reason}"


def run_one(kind, chain, request, *, merged, budget_obj, route, budget_check, limiter, on_log,
            cancel=None, adapters=None, transport=None, sleep_fn=time.sleep, time_fn=time.monotonic):
    """One request through *chain* with every gate the runner has: *route*,
    ``allow_paid`` (from *budget_obj*), *budget_check* (one attempt per paid
    link, DEC-106) and the free-tier *limiter*. ``(GenResult, link)``;
    :class:`NoImage` with every link's reason (:func:`explain`) when no link
    answered. Books nothing: the caller books the answer (:func:`book`)."""
    try:
        return gen.run_generation_chain(
            kind, chain, request, env=merged, allow_paid=budget_obj.allow_paid, route=route,
            on_log=on_log, budget_check=budget_check, limiter=limiter, adapters=adapters,
            transport=transport, sleep_fn=sleep_fn, time_fn=time_fn, cancel=cancel,
        )
    except gen.NoRunnableLink as exc:
        reasons = [explain(kind, label, reason, chain=chain, merged=merged, budget_obj=budget_obj,
                           request=request, adapters=adapters) for label, reason in exc.failures]
        raise NoImage(reasons or [str(exc)], failures=exc.failures) from None


def book(ledger, result, answered, *, kind, step) -> float:
    """Book one answered call; returns what it cost."""
    paid = bool(result.paid)
    est = float(result.est_cost) if paid else 0.0
    ledger.append(step=step, provider=answered.provider, model=gating.api_model_id(kind, answered),
                  unit="image", qty=1, est_usd=est, paid=paid)
    # Today's spend, as the Settings chain test books a paid call. The runner
    # books nothing itself: this is the one booking of this call.
    if result.paid and result.est_cost > 0:
        budget_mod.record(result.est_cost)
    return round(est, 4)


# ------------------------------------------------------------------------- keep

def produced_image(result):
    """``(path, ext)`` of the image the answer carried; :class:`NotKept` when
    there is none, it is not of a kept type, or it is not a regular file."""
    paths = [str(p) for p in (result.paths or ()) if p]
    if not paths:
        raise NotKept("the answer carried no file")
    produced = paths[0]
    ext = os.path.splitext(produced)[1].lstrip(".").lower()
    if ext not in KEPT_EXTENSIONS:
        kept = f"{', '.join(KEPT_EXTENSIONS[:-1])} and {KEPT_EXTENSIONS[-1]}"
        raise NotKept(f"the answer is a .{ext or '?'} file; only {kept} are kept")
    if os.path.islink(produced) or not os.path.isfile(produced):
        raise NotKept("the answer's file is missing, or a symlink (never followed)")
    return produced, ext


def keep(result, folder, stem):
    """Copy the produced image into *folder* as ``<stem>.<ext>``;
    ``(name, None)``, or ``(None, why)`` when it is not an image we keep."""
    try:
        produced, ext = produced_image(result)
    except NotKept as exc:
        return None, str(exc)
    name = f"{stem}.{ext}"
    handle, tmp = tempfile.mkstemp(dir=folder, prefix=f".{name}-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out, open(produced, "rb") as src:
            shutil.copyfileobj(src, out)
        os.chmod(tmp, 0o644)
        os.replace(tmp, os.path.join(folder, name))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return name, None
