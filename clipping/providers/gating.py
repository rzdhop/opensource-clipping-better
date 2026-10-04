"""The wiring around a generation chain's gates, written once (DEC-097, DEC-103).

``generation.run_generation_chain`` enforces the gates when it calls, in its
order: route -> adapter -> keys -> local probe -> paid (``allow_paid`` and the
budget verdict) or free (the limiter). Its callers need the same wiring around
it, and it lives here so they cannot drift apart:

- **before** a call, a verdict per link that calls nothing
  (:func:`link_summary`): the Settings chain surface and the story estimate;
- **at** a call, the Settings values over the process environment
  (:func:`merged_env`), the budget they describe (:func:`budget_of`), the
  budget check (:func:`budget_check`) and the free-tier limiter
  (:class:`FreeTierLimiter`): the Settings chain test and the style preview
  step; and the provider's own model id for a ledger line
  (:func:`api_model_id`).

Lifted from ``web/api/routes/settings.py`` unchanged in behaviour (AI Story
phase 1, stage 8): ``clipping`` never imports ``web``, and the preview step is
``clipping`` code. Nothing here books a call -- ``run_generation_chain`` books
nothing either; the caller that made a paid call books it, once.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import os

from . import budget as budget_mod
from . import generation as gen
from . import limits, pricing


def merged_env(settings_env) -> dict:
    """The saved Settings on top of the process environment, as every reader
    sees them: a Settings value replaces the process one, an empty one
    removes it."""
    merged = dict(os.environ)
    for name, value in (settings_env or {}).items():
        if value:
            merged[name] = value
        else:
            merged.pop(name, None)
    return merged


def budget_of(merged) -> budget_mod.Budget:
    """The budget the merged values describe. ``ValueError`` for a cap that is
    not an amount."""
    return budget_mod.budget_from_env({name: merged.get(name, "") for name in budget_mod.ENV_NAMES})


def budget_check(budget_obj, *, story_spent=None):
    """``check(estimate, link)`` for ``run_generation_chain``.

    Today's paid spend is read at each check, so a paid call booked a moment
    ago counts against the next one. *story_spent*, when given, is a callable
    returning the story's total so far, read at each check for the same
    reason; without it the per-story cap is checked against nothing spent.
    """

    def check(estimate, link):
        extra = {} if story_spent is None else {"story_spent": float(story_spent())}
        state = budget_mod.day_state()
        budget_mod.check(estimate, link, budget=budget_obj, day_spent=state.spent, day_extra=state.extra, **extra)

    return check


class FreeTierLimiter:
    """``limiter`` for ``run_generation_chain``: one call on the provider's
    free allowance (``limits.acquire``), or the reason it is spent."""

    def acquire(self, provider):
        return limits.acquire(provider)

    def release(self, provider):
        """Give back a slot :meth:`acquire` counted for a call the provider
        refused for credentials (``run_generation_chain``'s 401/403 handling)."""
        limits.release(provider)


def api_model_id(kind, link) -> str:
    """The provider's own model id behind a chain link (for the ledger)."""
    from . import images, lipsync, tts, video, vision

    tables = {
        "cloudflare": images.CLOUDFLARE_MODELS, "fal": {**images.FAL_APPS, **lipsync.FAL_LIPSYNC_APPS},
        "gemini": {**images.GEMINI_MODELS, **tts.GEMINI_TTS_MODELS, **vision.GEMINI_VISION_MODELS,
                   **video.GEMINI_VIDEO_MODELS},
        "openai": {name: pair[0] for name, pair in images.OPENAI_MODELS.items()},
    }
    return tables.get(link.provider, {}).get(link.model, link.model)


def link_summary(kind, link, merged, budget_obj, request, *, qty=1, story_spent=0.0, adapters=None) -> dict:
    """What the gates say about *link* right now, without calling anything.

    ``est_usd`` is the adapter's own estimate of *request* -- the number the
    runner will check -- times *qty* (0.0 for a free link, and for a paid one
    with no price). ``allowed`` is false without a key, and for a paid link
    while ``allow_paid`` is off or when the budget refuses the estimate;
    ``reason`` then carries the refusal with its numbers. The route, the local
    probe and the free-tier limiter are the caller's to add: they depend on
    where the call would run.
    """
    provider = gen.provider_for(link)
    missing = gen.missing_keys(link, merged)
    paid = gen.is_paid(link)
    adapter = gen.adapter_for(kind, link.provider, adapters)
    est = 0.0
    if paid:
        try:
            # The adapter's own estimate of the request (tokens, size), so the
            # number here is the one the runner will check.
            estimate = adapter.estimate(link, request) if adapter else None
            if estimate is None:
                estimate = pricing.estimate(link, 1, width=request.width, height=request.height)
            est = float(getattr(estimate, "est_usd", estimate) or 0.0)
        except (pricing.PriceUnknown, ValueError):
            est = 0.0
        if qty != 1:
            est = round(est * qty, 6)
    allowed = not missing
    reason = None
    if allowed and paid:
        state = budget_mod.day_state()
        day_spent = state.spent
        if not budget_obj.allow_paid:
            # The runner's first gate (DEC-097), whatever the amount.
            allowed = False
            reason = (f"refused: est ${est:.3f} on {gen.describe(link)}; allow_paid is off "
                      f"(today ${day_spent:.2f} of ${budget_obj.daily_cap_usd:.2f})")
        else:
            try:
                budget_mod.check(est, link, budget=budget_obj, day_spent=day_spent, story_spent=story_spent,
                                 day_extra=state.extra)
            except budget_mod.BudgetRefused as exc:
                allowed, reason = False, str(exc)
    return {
        "label": gen.describe(link), "provider": link.provider, "model": link.model,
        "paid": paid, "keyed": not missing, "missing_keys": missing,
        "adapter": adapter is not None,
        "allowed": allowed, "est_usd": est, "reason": reason,
        "env_keys": list(gen.env_keys_for(link)), "signup_url": provider.signup_url,
    }
