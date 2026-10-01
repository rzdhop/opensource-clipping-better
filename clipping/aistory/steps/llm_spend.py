"""Paid LLM calls of a story step: estimated, capped and booked (AI Story
phase 6, stage 5; DEC-115's follow-up, "LLM spend is not estimated or
booked").

DEC-115 keeps every paid link of the LLM chain out of a story step while
``allow_paid`` is off. With it on, ``llm_call.call_json`` hands a chain that
holds a keyed paid link to a :class:`Meter`, which treats it as a paid image
is treated (``voices.LineGates``: the Settings over the environment, the
budget they describe, the story's ledger, the episode / day / story caps read
at each check, and today's spend):

- **before each chain run** (:meth:`Meter.plan`) every keyed paid link is
  estimated -- the prompt's tokens by ``pacing.estimate_tokens``, the chain
  limiter's own estimate, plus the reply cap, at ``pricing.LLM_PRICES`` --
  and checked against the caps. A link refused, or with no price, is left
  out of the chain handed to ``run_chain`` with a printed ``⏭`` line giving
  the numbers; an allowed one gets a ``💸`` line; free links run as before.
  Nothing left to run is a ``StepFailed`` naming every refusal.
- **at each request** ``run_chain`` builds its clients through
  :meth:`Meter.factory`, its own ``client_factory`` seam (``llm.py`` is not
  edited). A free link gets ``llm.build_client``'s client untouched. A paid
  one is priced again (a DEC-089 fallback model with no price is refused
  before a client exists); each request it sends is checked against the caps
  first -- a retry and the second ask count -- and booked the moment it
  returns: at the reply's own ``usage.cost`` when the provider reports one,
  else its token usage at the table's price, else (no usage, or a failure
  DEC-153 does not prove unbilled) at the estimate, with a note. The reply
  itself is returned unchanged.

The ledger keeps four decimals (``ledger.CostLedger``) and a call costs a
fraction of a cent, so every booking and every checked estimate is rounded UP
to them (:func:`ledger_usd`): the ledger may over-report by less than $0.0001
a request, never under-report.

:func:`is_provider_chain` says when any of this applies: only when the runner
is ``llm.run_chain`` itself (or a ``functools.wraps`` wrapper of it), the
chain that reaches a real client. A test's stand-in runner answers by itself,
bills nothing and may carry its own client factory, so it is called exactly
as before.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import math

from clipping.providers import budget as budget_mod
from clipping.providers import gencache, pacing, pricing, registry

from .. import context, prompts
from ..store import StoryStore
from . import StepFailed
from .llm_call import is_free_link

__all__ = ["Meter", "UNIT", "is_provider_chain", "ledger_usd", "open_meter", "worst_call_usd"]

UNIT = "token"

# The ledger's precision: four decimals of a dollar.
_LEDGER_STEPS_PER_USD = 10_000

NO_USAGE_NOTE = "no usage in the reply: booked at the estimate (DEC-153)"


def ledger_usd(usd) -> float:
    """*usd* rounded UP to the ledger's four decimals ($0.0001)."""
    # Rounded to 6 places first, so 0.0003's float error is not a whole step.
    return math.ceil(round(float(usd) * _LEDGER_STEPS_PER_USD, 6)) / _LEDGER_STEPS_PER_USD


def is_provider_chain(runner) -> bool:
    """Whether *runner* is ``llm.run_chain`` -- the chain that reaches a real
    client -- or a wrapper made with ``functools.wraps`` from it, rather than
    a stand-in that answers by itself."""
    return (getattr(runner, "__module__", None) == "clipping.providers.llm"
            and getattr(runner, "__qualname__", None) == "run_chain")


def worst_call_usd(link) -> float:
    """The most one story call on the paid *link* is estimated at: the widest
    input budget a prompt may be sent with (``context.check_budget`` refuses
    more) and the widest reply cap (E1's payoff variant counted), at its
    price, rounded up as a booking is. ``pricing.PriceUnknown`` without one."""
    pairs = [(prompts.INPUT_BUDGET.get(prompt_id, context.PACK_TOKEN_BUDGET), cap)
             for prompt_id, cap in prompts.MAX_TOKENS.items()]
    pairs.append((prompts.INPUT_BUDGET.get("E1", context.PACK_TOKEN_BUDGET), prompts.E1_PAYOFF_MAX_TOKENS))
    pairs.append((prompts.INPUT_BUDGET["E1v2"], prompts.E1V2_PAYOFF_MAX_TOKENS))  # its v2 twin (stage 5c)
    return ledger_usd(max(pricing.llm_cost(link, tokens_in, tokens_out) for tokens_in, tokens_out in pairs))


def open_meter(ctx, prompt_id, *, system, user, cap) -> "Meter":
    """The :class:`Meter` of one ``call_json`` on the context's story: its
    gates opened once (the story's ledger checked readable: its totals feed
    the caps). ``StepFailed`` when they cannot be: nothing paid may run."""
    from .. import voices

    stories = StoryStore(ctx.outputs_dir, on_log=ctx.on_log)
    try:
        gates = voices.LineGates(stories, ctx.story_id, env=ctx.settings_env, ep=ctx.ep)
    except KeyError:
        raise StepFailed(f"There is no story {ctx.story_id!r}.") from None
    except voices.VoiceError as exc:
        reason = f"the story's spending cannot be checked, so no paid LLM link may run: {exc}"
        raise StepFailed(f"{prompt_id}: {reason}", reason=reason) from None
    return Meter(ctx, prompt_id, gates, tokens_in=pacing.estimate_tokens(system, user), cap=cap)


def _count(value):
    """A token count from a usage field, or None."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _amount(value):
    """A dollar amount from a usage field, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


class _Proxy:
    """*real* with some attributes replaced; every other one is *real*'s."""

    def __init__(self, real, **replaced):
        self._real = real
        self.__dict__.update(replaced)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _MeteredCompletions:
    """``client.chat.completions`` of a paid link: each ``create`` checked
    against the caps before it is sent and booked when it returns."""

    def __init__(self, meter, link, real):
        self._meter = meter
        self._link = link
        self._real = real

    def create(self, **body):
        meter, link = self._meter, self._link
        usd = meter.estimate(link)
        # Before sending: a refusal fails this link (unbilled), as a refused
        # image does, and run_chain moves to the next one.
        meter.gates.check(usd, link)
        try:
            response = self._real.create(**body)
        except Exception as exc:
            billed, note = gencache.billing_verdict(exc, sent=None)
            if billed:
                meter.book(link, qty=meter.tokens_in + meter.cap, usd=usd, note=note)
            raise
        meter.book_reply(link, response, usd)
        return response


class Meter:
    """One ``call_json``'s paid links: planned, metered and booked (see the
    module docstring). *tokens_in* is the prompt's estimate, *cap* the reply
    cap every request of the call is sent with."""

    def __init__(self, ctx, prompt_id, gates, *, tokens_in, cap):
        self.ctx = ctx
        self.prompt_id = prompt_id
        self.gates = gates
        self.tokens_in = tokens_in
        self.cap = cap
        # ``(label, reason)`` of each link the last plan left out.
        self.refused = []

    def estimate(self, link) -> float:
        """One request on the paid *link*, rounded up to the ledger's
        precision; ``pricing.PriceUnknown`` without a price."""
        return ledger_usd(pricing.llm_cost(link, self.tokens_in, self.cap))

    def _numbers(self, usd) -> str:
        return f"≈{self.tokens_in} tokens in + {self.cap} out = ${usd:.4f}"

    def plan(self, chain, keys) -> list:
        """The links of *chain* to hand ``run_chain``, in its order: each
        keyed paid link estimated and checked against the caps, left out with
        a printed line when refused or unpriced. ``StepFailed`` when no keyed
        link is left."""
        runnable, self.refused = [], []
        for link in chain:
            if is_free_link(link) or not keys.get(link.provider):
                runnable.append(link)
                continue
            label = registry.describe(link)
            try:
                usd = self.estimate(link)
            except pricing.PriceUnknown as exc:
                self.ctx.on_log(f"   ⏭ Skipping {label}: {exc}")
                self.refused.append((label, str(exc)))
                continue
            try:
                self.gates.check(usd, link)
            except budget_mod.BudgetRefused as exc:
                reason = f"{exc} ({self._numbers(usd)})"
                self.ctx.on_log(f"   ⏭ Skipping {label}: {reason}.")
                self.refused.append((label, reason))
                continue
            self.ctx.on_log(f"   💸 {label}: est ${usd:.4f} (paid, allowed; ≈{self.tokens_in} tokens in "
                            f"+ {self.cap} out)")
            runnable.append(link)
        if not any(keys.get(link.provider) for link in runnable):
            reason = "no keyed link of the LLM chain may run: " + "; ".join(why for _label, why in self.refused)
            raise StepFailed(f"{self.prompt_id}: {reason}", reason=reason)
        return runnable

    def factory(self, link, *, api_key, timeout):
        """``client_factory`` for ``run_chain``: ``llm.build_client``'s client,
        metered when *link* is paid (the link that answers: a DEC-089 fallback
        model is priced on its own, refused without a price)."""
        from clipping.providers import llm as llm_mod

        if is_free_link(link):
            return llm_mod.build_client(link, api_key=api_key, timeout=timeout)
        self.estimate(link)
        client = llm_mod.build_client(link, api_key=api_key, timeout=timeout)
        chat = client.chat
        return _Proxy(client, chat=_Proxy(chat, completions=_MeteredCompletions(self, link, chat.completions)))

    def book_reply(self, link, response, estimate_usd) -> None:
        """Book the reply a paid request returned: the provider's own cost,
        else its usage at the table's price, else the estimate with a note."""
        usage = getattr(response, "usage", None)
        tokens_in = _count(getattr(usage, "prompt_tokens", None))
        tokens_out = _count(getattr(usage, "completion_tokens", None))
        total = _count(getattr(usage, "total_tokens", None))
        if total is None and tokens_in is not None and tokens_out is not None:
            total = tokens_in + tokens_out
        qty = total if total is not None else self.tokens_in + self.cap
        cost = _amount(getattr(usage, "cost", None))
        if cost is not None:
            self.book(link, qty=qty, usd=ledger_usd(cost))
        elif tokens_in is not None and tokens_out is not None:
            self.book(link, qty=qty, usd=ledger_usd(pricing.llm_cost(link, tokens_in, tokens_out)))
        else:
            self.book(link, qty=qty, usd=estimate_usd, note=NO_USAGE_NOTE)

    def book(self, link, *, qty, usd, note=None) -> None:
        """One ledger row of the story (the step, its episode), and today's
        spend -- as a paid image is booked."""
        self.gates.ledger.append(step=self.ctx.step, provider=link.provider, model=link.model, unit=UNIT,
                                 qty=qty, est_usd=usd, paid=True, ep=self.gates.ep, note=note)
        if usd > 0:
            budget_mod.record(usd)
