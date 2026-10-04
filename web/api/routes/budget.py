"""
web.api.routes.budget — today's paid spending and the "allow more for today"
override (plan 23, stage A3).

``GET /api/budget/today`` reports the day's paid total, what was allowed on top
of the daily cap, and who spent it. ``POST /api/budget/today/extra`` raises
today's limit by an amount that ends with the day; ``DELETE`` takes it back.

Security (DEC-173, DEC-263): the app has no token, and ``PUT /api/settings``
can already raise the daily cap, so this is no new power. The limits are the
ceiling (``budget.DAY_EXTRA_MAX_USD``) and the day's own expiry, and
``CrossSiteWriteGuard`` keeps another website from writing here.

A grant is logged in ``spend.json`` (``grants``, by ``DailySpend.add_extra``),
on stdout, and -- when it names a story -- in that story's ``activity.log``.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from clipping.aistory import day_report
from clipping.aistory import store as story_store
from clipping.providers import budget as budget_mod

from .. import worker
from ..auth import require_token
from ..models import BudgetExtraRequest, BudgetTodayResponse

router = APIRouter(prefix="/api/budget", tags=["budget"], dependencies=[Depends(require_token)])

NOTE_MAX_CHARS = 200
CONTRIBUTORS_SHOWN = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _daily_cap(env) -> float:
    """The saved daily cap, read the way the pipeline reads it (DEC-097)."""
    merged = {name: env.get(name, os.environ.get(name, "")) for name in budget_mod.ENV_NAMES}
    return budget_mod.budget_from_env(merged).daily_cap_usd


def today_block(env=None) -> dict:
    """The ``GET /api/budget/today`` body as a plain dict. Never raises on an
    unreadable story: a ledger it cannot read counts for nothing.

    Plan 23 A4: also ``story_count``, how many stories spent today (the
    response model leaves it out; the stories' 409 and estimate carry it)."""
    env = worker.get_settings_env() if env is None else env
    state = budget_mod.day_state()
    cap = _daily_cap(env)
    try:
        sources = day_report.story_sources(story_store.StoryStore(worker.OUTPUTS_ROOT))
        every = day_report.day_contributors(sources, day=state.day, limit=len(sources))
    except Exception:
        every = []
    stories = every[:CONTRIBUTORS_SHOWN]
    return {
        "day": state.day,
        "zone": state.zone,
        # Plan 23 A7: why the configured BUDGET_TIMEZONE is not in force (the day is UTC meanwhile).
        "zone_error": budget_mod.zone_error,
        "spent_usd": round(state.spent, 4),
        "extra_usd": round(state.extra, 4),
        "daily_cap_usd": cap,
        "effective_cap_usd": round(cap + state.extra, 4),
        "cap_below_spend": state.spent > cap,
        "stories": stories,
        "story_count": len(every),
        "other_usd": day_report.other_usd(state.spent, stories),
        "grants_today": budget_mod.default_spend().grants_today(),
        "resets_at": budget_mod.next_reset(),
    }


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def _clean_note(raw) -> str:
    note = " ".join(str(raw or "").split())
    if len(note) > NOTE_MAX_CHARS:
        raise _bad_request(f"the note is at most {NOTE_MAX_CHARS} characters, not {len(note)}")
    return note


def _log_grant(request: BudgetExtraRequest, *, usd: float, note: str, block: dict) -> None:
    """The grant on stdout, and in the story's activity.log when it names one."""
    day, zone = block["day"], block["zone"]
    tail = f"; today ${block['spent_usd']:.2f} of ${block['daily_cap_usd']:.2f}"
    if request.estimate_usd is not None:
        tail += f"; the call needed ${request.estimate_usd:.2f}"
    where = f" for story {request.story_id}" if request.story_id else ""
    print(f"[budget] allowed ${usd:.2f} more for {day} {zone}{where}"
          f" (today's extra ${block['extra_usd']:.2f}){': ' + note if note else ''}{tail}")
    if request.story_id:
        head = f"{_now()} [budget] allowed ${usd:.2f} more for today ({day} {zone})"
        story_store.StoryStore(worker.OUTPUTS_ROOT).append_activity(
            request.story_id, f"{head}{': ' + note if note else ''}{tail}")


@router.get("/today")
def get_today() -> BudgetTodayResponse:
    """Today's paid spending, the extra allowed for it, and who spent it."""
    return BudgetTodayResponse(**today_block())


@router.post("/today/extra")
def allow_extra(request: BudgetExtraRequest) -> BudgetTodayResponse:
    """Allow ``usd`` more for today only (400 over the ceiling, on a bad
    amount, an unknown story or a note over 200 characters)."""
    usd = request.usd
    if not math.isfinite(usd) or not usd > 0:
        raise _bad_request(f"a daily extra is a positive amount, not {usd!r}")
    if request.estimate_usd is not None and (
            not math.isfinite(request.estimate_usd) or request.estimate_usd < 0):
        raise _bad_request(f"estimate_usd is not negative, not {request.estimate_usd!r}")
    note = _clean_note(request.note)
    if request.story_id is not None:
        try:
            story_store.StoryStore(worker.OUTPUTS_ROOT).story_dir(request.story_id)
        except KeyError:
            raise _bad_request(f"no such story: {request.story_id!r}") from None
    try:
        budget_mod.default_spend().add_extra(usd, story_id=request.story_id, note=note)
    except ValueError as exc:
        raise _bad_request(str(exc)) from None
    block = today_block()
    _log_grant(request, usd=usd, note=note, block=block)
    return BudgetTodayResponse(**block)


@router.delete("/today/extra")
def clear_extra() -> BudgetTodayResponse:
    """Take today's extra back; the saved cap applies again."""
    held = budget_mod.default_spend().clear_extra()
    if held:
        print(f"[budget] took back ${held:.2f} allowed for today")
    return BudgetTodayResponse(**today_block())
