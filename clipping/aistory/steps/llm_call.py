"""The one way a story step asks the LLM chain for JSON (spec 4.1, DEC-027).

``call_json`` wraps ``clipping.providers.llm.run_chain`` with the rules every
story-writing call shares:

- the prompt's own cap and temperature (``prompts.MAX_TOKENS`` /
  ``TEMPERATURE``; a measured variant's cap when the caller names one, E1's
  payoff ask), the same on a second try -- a reply is never "fixed" by
  asking for less, and never trimmed after it arrives;
- the pack budget (``context.check_budget``) before anything is sent;
- a reply the post-validator rejects is asked for once more from the same
  link, printed; a second rejection moves to the chain's next link rather
  than failing the call (phase 7 stage 2d -- a word-cap overrun on the first
  link must not waste a chain with more links left in it), same prompt, same
  cap, one validated try, no further retry; every link of the chain
  exhausted this way is a :class:`StepFailed` naming the last link's errors
  (spec 4.1: salvage -- ``run_chain`` already parses and salvages -- retry
  the first link once with the same cap, then fall through, then report);
- a chain where every link failed to answer at all (not a validation
  rejection -- ``run_chain`` itself could get no parseable reply) is a
  :class:`StepFailed` carrying every link's reason;
- the cancel token is checked before each request and handed to the chain;
- one ``✍️`` line per accepted reply, after the chain's own hop lines;
- a paid link is never called while ``allow_paid`` is off (``story_chain``):
  it is printed as skipped, one ``⏭`` line per link before each chain run,
  and a chain left with no keyed link is a :class:`StepFailed` naming the
  paid links and the free keys to set. Clip jobs are not affected (DEC-088
  still governs them);
- with ``allow_paid`` on, a keyed paid link is estimated, checked against
  the episode, day and story caps and every reply it bills booked on the
  story's ledger and today's spend (``llm_spend``, phase 6 stage 5); a run
  with no keyed paid link calls ``run_chain`` exactly as before.

The chain and the keys come from the worker's Settings values first and the
process environment second, the precedence the web layer applies to a clip
job (``web/api/config_adapter.py``). ``clipping`` never imports ``web``, so
it is repeated here rather than shared.

Also here: the few story helpers every step runner needs (open the story,
require a chosen concept, print what the pack trimmed).

Stdlib only (DEC-012); the provider modules it reaches are stdlib at import.
"""

from __future__ import annotations

import functools
import json
import os
import re
import time
from datetime import datetime, timezone

from clipping import cancel as cancel_mod
from clipping.providers import errors as provider_errors
from clipping.providers import gating, pacing, registry

from .. import context, prompts
from ..store import StoryStore
from . import StepFailed

__all__ = [
    "PAID_SKIP_REASON",
    "STORY_CALL_BUDGET_SECONDS",
    "ReplyRejected",
    "StepFailed",
    "announce_trimmed",
    "call_json",
    "is_free_link",
    "key_choices",
    "open_story",
    "paid_off_message",
    "require_concept",
    "resolve_chain",
    "resolve_keys",
    "resolve_premium_chain",
    "story_chain",
    "utc_now",
]

# One story call may wait out the slow free floor (DEC-091): NVIDIA's 180s
# request fits with room for a first link that failed fast. Per call, not per
# step: a bible is three calls, and B3 must not be refused because B1 was slow.
STORY_CALL_BUDGET_SECONDS = 300

# The deadline one story call hands run_chain (phase 7, DEC-224): both
# nemotron-3 retry ladders (3 x 60 s and 3 x 40 s, registry.MODEL_TIMEOUTS)
# and still one full 180 s request to the free gemini floor, so a NIM outage
# never leaves a story call without a model. A step's own planning keeps
# STORY_CALL_BUDGET_SECONDS: a call that runs past it is the rare case of
# every NIM attempt timing out.
STORY_CALL_DEADLINE_SECONDS = 480

# Why a story step leaves a link of the chain out (spec 0: printed, never
# silent). The ⏭ line and the estimate's "skipped" carry it.
PAID_SKIP_REASON = "paid link: allow_paid is off (AI Story spends only on opt-in)"

# How many validator errors the ⚠️ line quotes; the StepFailed quotes more.
_ERRORS_IN_LOG_LINE = 2
_ERRORS_IN_FAILURE = 5

_TRIMMED_LABELS = {
    "seed": "Seed text",
    "brief": "The user's brief",
    "note": "Author's note",
    "avoid": "The list of titles to avoid",
    "bible": "The bible summary",
}


# ------------------------------------------------------------- chain + keys

def _first_spec(settings_env, *names) -> str:
    """The first non-blank value of *names*, Settings then the process env,
    checking each name fully before moving to the next -- the precedence
    :func:`resolve_chain` and :func:`resolve_premium_chain` share. A blank
    value at one level falls through to the next, as it does for a clip job
    (the web layer passes ``""`` on and ``chain_from_env`` then reads the
    process env)."""
    env = settings_env or {}
    for name in names:
        spec = str(env.get(name) or "").strip()
        if spec:
            return spec
        spec = os.environ.get(name, "").strip()
        if spec:
            return spec
    return ""


def resolve_chain(settings_env) -> list:
    """The chain a story step runs: ``STORY_LLM_CHAIN`` in Settings, then
    ``STORY_LLM_CHAIN`` in the process env, then ``LLM_CHAIN`` in Settings,
    then ``LLM_CHAIN`` in the process env, then the shipped AI Story default
    (``registry.DEFAULT_STORY_LLM_CHAIN``).

    ``STORY_LLM_CHAIN`` lets AI Story be pointed at a different chain than
    Clips without disturbing a Clips job (DEC-224): an operator who already
    set ``LLM_CHAIN`` keeps exactly today's behaviour (it is checked before
    the new default), and an installation with nothing set at all -- no
    Settings, no .env -- gets the AI Story chain instead of silently
    inheriting Clips' chain (DEFAULT_LLM_CHAIN), which was never benchmarked
    against a story-writing prompt.

    A malformed chain raises ``ChainError``: that is a configuration error,
    not a failed call, and is not retried.
    """
    spec = _first_spec(settings_env, "STORY_LLM_CHAIN", "LLM_CHAIN")
    return registry.parse_chain(spec or registry.DEFAULT_STORY_LLM_CHAIN)


def resolve_premium_chain(settings_env) -> list:
    """The chain a premium prompt runs on (plan 22 stage 1, DEC-273):
    ``STORY_LLM_PREMIUM_CHAIN`` in Settings, then in the process env, then
    whatever :func:`resolve_chain` would already resolve to -- so an
    operator who configured ``STORY_LLM_CHAIN`` or ``LLM_CHAIN`` is not
    disturbed, exactly as a clip job configured on ``LLM_CHAIN`` is not
    disturbed by ``STORY_LLM_CHAIN`` existing at all -- and only once
    nothing at any level is configured does a premium prompt fall to its own
    shipped default (``registry.PREMIUM_STORY_LLM_CHAIN``) rather than the
    non-premium one.
    """
    spec = _first_spec(settings_env, "STORY_LLM_PREMIUM_CHAIN", "STORY_LLM_CHAIN", "LLM_CHAIN")
    return registry.parse_chain(spec or registry.PREMIUM_STORY_LLM_CHAIN)


def resolve_keys(settings_env) -> dict:
    """``{provider: key}`` for every chain provider with a key.

    ``web/api/config_adapter.resolve_provider_keys``'s precedence, exactly:
    the Settings value when Settings has the name at all, else the process
    env. A provider with no key is left out, so its link is skipped with a
    printed line and never contacted.
    """
    # Imported here: clipping.config reads .env and builds argparse defaults
    # at import, which a caller that only wants the registry does not need.
    from clipping.config import PROVIDER_KEYS

    env = settings_env or {}
    keys = {}
    for name, (_attr, env_name) in PROVIDER_KEYS.items():
        value = env.get(env_name, os.environ.get(env_name, ""))
        if value:
            keys[name] = value
    return keys


def is_free_link(link) -> bool:
    """Whether calling *link* costs nothing.

    A link is free when its provider's default model is (``Provider.free_tier``,
    DEC-088), or when it names an OpenRouter ``:free`` model. The LLM registry
    marks free per provider only -- OpenRouter's is ``False`` because its
    default model is billed -- and nothing that reads the LLM registry looks
    at a ``:free`` suffix; OpenRouter does not bill a ``:free`` model (the
    generation registry already counts ``openrouter/...:free`` as free), so a
    link naming one is free here.
    """
    if registry.PROVIDERS[link.provider].free_tier:
        return True
    return link.provider == "openrouter" and str(link.model).endswith(":free")


def story_chain(settings_env, *, premium=False) -> tuple:
    """``(usable, skipped)``: the links of the chain a story step may call, in
    the chain's order, and the links it leaves out, each as ``(link, reason)``.

    *premium* (plan 22 stage 1) resolves ``resolve_premium_chain`` instead of
    ``resolve_chain`` -- the only difference it makes: the filtering below is
    the same for either chain.

    While ``allow_paid`` is off -- the budget of the Settings values over the
    process environment (``gating.merged_env``/``budget_of``, DEC-097) -- every
    link that is not free (:func:`is_free_link`) is skipped with
    :data:`PAID_SKIP_REASON`: AI Story spends only on opt-in. With
    ``allow_paid`` on nothing is skipped here: the caps are checked per call
    by ``call_json`` (``llm_spend``), which leaves a refused link out of that
    call's chain.

    The chain the user configured is not edited, reordered or trimmed
    (DEC-003, DEC-023): this is only which of its links a story step uses.
    Clip jobs never call this.

    Raises ``ChainError`` for a chain that cannot be parsed and ``ValueError``
    for a budget cap that is not an amount.
    """
    links = resolve_premium_chain(settings_env) if premium else resolve_chain(settings_env)
    budget = gating.budget_of(gating.merged_env(settings_env))
    if budget.allow_paid:
        return list(links), []
    usable, skipped = [], []
    for link in links:
        if is_free_link(link):
            usable.append(link)
        else:
            skipped.append((link, PAID_SKIP_REASON))
    return usable, skipped


def key_choices(links) -> str:
    """``"GROQ_API_KEY (https://...), OPENROUTER_API_KEY (https://...) (paid)"``:
    the keys that would put *links* to work, primaries first (the slow floor
    alone would be refused next, DEC-073), one per provider, a billed one
    marked as such (DEC-088)."""
    wanted = [link for link in links if registry.is_primary(link)] or list(links)
    providers = dict.fromkeys(link.provider for link in wanted)
    names = []
    for name in providers:
        provider = registry.PROVIDERS[name]
        free = any(is_free_link(link) for link in wanted if link.provider == name)
        names.append(f"{provider.env_key} ({provider.signup_url or 'your own endpoint'})"
                     + ("" if free else " (paid)"))
    return ", ".join(names)


def paid_off_message(paid_links, free_links, *, where=None) -> str:
    """Why a story step cannot run on this chain while ``allow_paid`` is off:
    its only keyed links are paid (*paid_links*). Names them, ``allow_paid``,
    and the keys of the chain's free links (*free_links*) to set instead --
    or, with none in the chain, a free link to add. *where* says where keys
    are set ("in Settings", "in the environment or in .env"), when known."""
    labels = list(dict.fromkeys(_link_label(link) for link in paid_links))
    many = len(labels) > 1
    text = (
        f"The only keyed link{'s' if many else ''} of the LLM chain "
        f"{'are' if many else 'is'} paid ({', '.join(labels)}), and allow_paid "
        "is off: AI Story spends only on opt-in."
    )
    place = f", {where}" if where else ""
    them = "them" if many else "it"
    if free_links:
        return (f"{text} Set the key of a free link, one of: {key_choices(free_links)}"
                f"{place}; or turn allow_paid on to use {them}.")
    return (f"{text} LLM_CHAIN has no free link: add one, e.g. "
            f"{registry.suggested_link('gemini')} with {registry.PROVIDERS['gemini'].env_key}"
            f"{place}; or turn allow_paid on to use {them}.")


def _link_label(link) -> str:
    """``"gemini/gemini-3.5-flash-lite"`` for a ``Link``; tolerant of a test's
    stand-in, like ``analysis.analyzer._describe_link``."""
    provider = getattr(link, "provider", None)
    model = getattr(link, "model", None)
    if provider is not None and model is not None:
        return registry.describe(link)
    if isinstance(link, (tuple, list)) and len(link) >= 2:
        return f"{link[0]}/{link[1]}"
    return str(link)


def _provider_reason(exc) -> str:
    """Every link's reason on one line (the job's error is one line)."""
    failures = list(getattr(exc, "failures", None) or [])
    if not failures:
        return " ".join(str(exc).split()) or type(exc).__name__
    detail = "; ".join(f"{label}: {' '.join(str(reason).split())}" for label, reason in failures)
    return f"every provider in the chain failed ({len(failures)} tried): {detail}"


# --------------------------------------------------------------- the breaker

# Plan 28 stage A5. A link that failed a whole retry ladder on an outage
# (HTTP 5xx or 408, a timeout, a dropped connection) is not asked again for
# the rest of the job: tonight's nemotron-3-ultra answered 500/503 three times
# in a row for eight scenes in a row, 19 s each. The memory is
# ``StepContext.link_health``; the llm.py ladder is not touched, so the
# ``attempt n/3`` lines ``web/api/signals.py`` reads are unchanged -- what
# failed is read from the two lines ``run_chain`` already prints for it, and
# from ``ProviderError.failures``.
_ATTEMPT_FAILED = re.compile(r"^\s*⚠️\s+(\S+) attempt (\d+) failed \|")
_LINK_FAILED = re.compile(r"^\s*⚠️\s+(\S+) failed \| (.*)$")
_OUTAGE_EXC_NAMES = frozenset({
    "APIConnectionError", "APITimeoutError", "InternalServerError",
    "TimeoutError", "ReadTimeout", "ConnectTimeout", "ConnectError", "ConnectionError",
})
_OUTAGE_STATUS = re.compile(r"(?:Error code:|HTTP|status[ =:]+)\s*(5\d\d|408)\b", re.IGNORECASE)
# The ladder's attempts per link when only the final reason is known.
_DEFAULT_FAILED_ATTEMPTS = 3


def is_outage_reason(reason) -> bool:
    """Whether a link's recorded failure (``"TypeName: message"``, as
    ``run_chain`` writes it) is an outage of the provider: an HTTP 5xx or 408,
    a timeout, a dropped connection. Everything else -- a 429, a 4xx, a reply
    that came but did not parse, a missing key, a time budget too short --
    says nothing about the link being down and never opens the breaker."""
    text = " ".join(str(reason).split())
    name = text.split(":", 1)[0].strip() if ":" in text else ""
    if name == "RateLimitError" or "Error code: 429" in text:
        return False
    return name in _OUTAGE_EXC_NAMES or bool(_OUTAGE_STATUS.search(text))


class _FailureWatch:
    """Wraps a call's ``on_log``: every line goes through unchanged, and the
    lines that say a link failed are remembered -- the whole-ladder failure
    (``⚠️ <label> failed | <reason>``) and each failed attempt, to say how
    many times the link was asked."""

    def __init__(self, on_log):
        self._on_log = on_log
        self.attempts = {}   # label -> failed attempts seen
        self.failed = {}     # label -> reason of the whole-ladder failure

    def __call__(self, line):
        text = str(line)
        match = _ATTEMPT_FAILED.match(text)
        if match:
            self.attempts[match.group(1)] = max(self.attempts.get(match.group(1), 0),
                                                int(match.group(2)))
        else:
            match = _LINK_FAILED.match(text)
            if match:
                self.failed[match.group(1)] = match.group(2)
        self._on_log(line)


def _open_links(ctx, watch, exc, prompt_id):
    """Record in ``ctx.link_health`` every link that failed its whole ladder
    in the call that just ended -- seen in *watch*'s log lines or in the
    ``ProviderError`` *exc* -- on an outage (:func:`is_outage_reason`), and
    print one plain line for each newly recorded."""
    health = getattr(ctx, "link_health", None)
    if health is None:
        return
    failed = dict(watch.failed)
    for label, reason in list(getattr(exc, "failures", None) or []):
        failed.setdefault(str(label), str(reason))
    for label, reason in failed.items():
        if label in health or not is_outage_reason(reason):
            continue
        times = watch.attempts.get(label) or _DEFAULT_FAILED_ATTEMPTS
        health[label] = {"reason": " ".join(str(reason).split())[:200], "times": times,
                         "on": prompt_id}
        short = label.rsplit("/", 1)[-1]
        ctx.on_log(f"   ⏭ {short}: failed {times} {'time' if times == 1 else 'times'} on "
                   f"{prompt_id}, skipped for the rest of this job")


# --------------------------------------------------------------- the call

class ReplyRejected(StepFailed):
    """:func:`call_json` got answers but the validator refused the last one
    (plan 24 stage 3): the one failure a caller may repair -- the script
    step's trim pass -- as opposed to a chain that could not answer, a budget
    refusal or a configuration error, which are plain :class:`StepFailed`."""


def call_json(
    ctx,
    prompt_id,
    system,
    user,
    schema,
    *,
    validator,
    runner=None,
    time_fn=time.monotonic,
    max_tokens=None,
    single_try=False,
) -> dict:
    """One accepted JSON reply for *prompt_id* (``"C1"``, ``"B1"``, ...).

    *validator* returns a list of errors, empty for a usable reply. *runner*
    is ``llm.run_chain`` unless a test hands in a stand-in; it is looked up
    when the call is made, not when this module is imported.

    *max_tokens* is the reply's cap: None (every caller but one) is the
    prompt's own ``prompts.MAX_TOKENS[prompt_id]``; a caller whose ask is a
    measured variant of the prompt hands in that variant's cap instead
    (E1's payoff variant, ``prompts.E1_PAYOFF_MAX_TOKENS``). Either way the
    same cap is sent on the second try.

    *single_try* (plan 24 stage 3, the trim pass): the first reply the
    validator refuses ends the call -- no same-link retry, no further link --
    so one call is one request on the chain's first answering link.

    Raises ``StepFailed`` (the chain failed to answer at all, or every link's
    reply was rejected by *validator* -- the first to answer retried once,
    each further link given one validated try, in the chain's order -- or --
    before anything is sent -- the budget settings cannot be read, or
    ``allow_paid`` is off and no free link has a key), ``Cancelled``,
    ``ChainError`` for a chain that cannot be parsed, or ``ValueError`` for
    a prompt over the pack budget -- a builder bug, never trimmed here.
    """
    context.check_budget(system, user, budget=prompts.input_budget(prompt_id))

    if runner is None:
        from clipping.providers import llm as llm_mod

        runner = llm_mod.run_chain

    # Plan 22 stage 1: the prompts the human singled out as the ones that
    # matter are written on the premium chain; everything else keeps today's
    # STORY_LLM_CHAIN. Deciding this from the prompt id alone, here, means no
    # step runner has to know or care which chain its own prompt is written
    # on.
    premium = prompt_id in prompts.PREMIUM_PROMPT_IDS
    try:
        chain, skipped = story_chain(ctx.settings_env, premium=premium)
    except registry.ChainError:
        raise
    except ValueError as exc:
        reason = f"The budget settings cannot be used: {exc}"
        raise StepFailed(f"{prompt_id}: {reason}", reason=reason) from None
    keys = resolve_keys(ctx.settings_env)
    paid = [link for link, _reason in skipped]
    if paid and not any(keys.get(link.provider) for link in chain):
        # Nothing free could answer: refused before any link is contacted.
        for link in paid:
            ctx.on_log(f"   ⏭ Skipping {_link_label(link)}: paid link, allow_paid is off "
                       "(AI Story spends only on opt-in).")
        keyed = [link for link in paid if keys.get(link.provider)] or paid
        reason = paid_off_message(keyed, chain)
        raise StepFailed(f"{prompt_id}: {reason}", reason=reason)
    # Plan 28 stage A5: a link that failed a whole ladder on an outage earlier
    # in this job is left out -- unless that would leave nothing to call, when
    # the chain is tried as it is (a dead link is better than no call).
    health = getattr(ctx, "link_health", None) or {}
    alive = [link for link in chain if _link_label(link) not in health]
    if alive:
        chain = alive
    cap = prompts.MAX_TOKENS[prompt_id] if max_tokens is None else max_tokens
    # Thinking room (registry.MODEL_OUTPUT_HEADROOM): added only when the
    # resolved chain actually names a link that wants it, so a non-premium
    # call -- and a premium call whose chain fell through to free links only
    # -- sends exactly the cap it always has (every pinned budget test is
    # unaffected). The meter below estimates with the cap already raised.
    # An Anthropic link's room follows its effort (plan 23 stage D1,
    # registry.output_headroom): the link's "@effort", else this prompt's
    # family effort, else the model's default.
    effort = prompts.anthropic_effort(prompt_id)
    headroom = max((registry.output_headroom(link, effort) for link in chain), default=0)
    cap += headroom
    # No keyword at all for a run without a token (the CLI's NEVER), as the
    # analyzer does it.
    cancel_kwargs = cancel_mod.kwargs_for(ctx.cancel)

    # A keyed paid link is in the chain only with allow_paid on: it is
    # estimated, capped and booked (``llm_spend``) when the runner reaches a
    # real client. Without one, nothing below differs from before.
    meter = None
    if any(keys.get(link.provider) and not is_free_link(link) for link in chain):
        from . import llm_spend

        if llm_spend.is_provider_chain(runner):
            meter = llm_spend.open_meter(ctx, prompt_id, system=system, user=user, cap=cap)

    # *pos* is where the next ``runner`` call starts in ``chain``: the first
    # link's one allowed retry keeps it in place (same link again); a link
    # whose reply fails validation for good -- the first link's second
    # failure, or any further link's only try -- advances past it. A link
    # ``runner`` itself could not reach at all is already accounted for by
    # ``run_chain``'s own hop lines and the ``ProviderError`` path below, so
    # it never needs a position of its own here.
    errors = []
    pos = 0
    retry_available = True
    tried_labels = []
    asked = user
    while True:
        attempt_chain = chain[pos:]
        if not attempt_chain:
            break
        ctx.cancel.check()
        # DEC-259: after a refusal the next try -- the same link's retry or the
        # next link -- is told why, under the unchanged prompt, so it fixes that
        # and nothing else (a blind retry gave the same reply on four links).
        asked = user if not errors else refused_prompt(user, errors)
        # The chain's own hop lines follow these: a paid link left out is
        # printed like a keyless one, never silently dropped (spec 0).
        for link in paid:
            ctx.on_log(f"   ⏭ Skipping {_link_label(link)}: paid link, allow_paid is off "
                       "(AI Story spends only on opt-in).")
        run_links, metered = attempt_chain, {}
        if meter is not None:
            # The factory captures this prompt's effort for an Anthropic link
            # (no run_chain signature change); every other link is built
            # exactly as before.
            factory = meter.factory
            if any(registry.PROVIDERS[link.provider].api == "anthropic" for link in attempt_chain):
                factory = functools.partial(meter.factory, effort=effort)
            run_links, metered = meter.plan(attempt_chain, keys), {"client_factory": factory}
        watch = _FailureWatch(ctx.on_log)
        try:
            value, link = runner(
                run_links,
                system=system,
                user=asked,
                schema=schema,
                schema_name=prompts.SCHEMA_NAMES[prompt_id],
                max_tokens=cap,
                temperature=prompts.TEMPERATURE[prompt_id],
                keys=keys,
                on_log=watch,
                deadline=time_fn() + STORY_CALL_DEADLINE_SECONDS,
                time_fn=time_fn,
                **cancel_kwargs,
                **metered,
            )
        except provider_errors.ProviderError as exc:
            _open_links(ctx, watch, exc, prompt_id)
            reason = _provider_reason(exc)
            if paid:
                labels = ", ".join(dict.fromkeys(_link_label(link) for link in paid))
                reason += f"; {labels} not tried: {PAID_SKIP_REASON}"
            if meter is not None:
                reason += "".join(f"; {label} not tried: {why}" for label, why in meter.refused)
            raise StepFailed(f"{prompt_id}: {reason}", reason=reason) from exc

        # The chain answered, perhaps after a link before it failed outright.
        _open_links(ctx, watch, None, prompt_id)
        errors = list(validator(value))
        if not errors:
            tokens = pacing.estimate_tokens(json.dumps(value, ensure_ascii=False))
            ctx.on_log(f"✍️ {prompt_id} via {_link_label(link)} ≈{tokens} tokens out (cap {cap})")
            return value

        answered_idx = chain.index(link, pos)
        label = _link_label(link)
        if label not in tried_labels:
            tried_labels.append(label)

        if single_try:
            break

        if retry_available:
            # The first link to answer gets one retry, same link, same cap
            # -- a reply is never "fixed" by asking for less.
            retry_available = False
            shown = "; ".join(errors[:_ERRORS_IN_LOG_LINE])
            ctx.on_log(
                f"⚠️ {prompt_id} reply rejected ({shown}); asking once more with the same cap"
            )
            pos = answered_idx
            continue

        # This link's one validated try (or the first link's retry) failed
        # for good: fall through to whatever is left of the chain, never
        # back to a link already exhausted.
        pos = answered_idx + 1
        if pos < len(chain):
            ctx.on_log(f"   ↪ {prompt_id}: {label} failed validation; "
                       f"trying the next link: {_link_label(chain[pos])}")

    shown = "; ".join(errors[:_ERRORS_IN_FAILURE])
    more = len(errors) - _ERRORS_IN_FAILURE
    if more > 0:
        shown += f"; and {more} more"
    if single_try:
        reason = f"the reply failed validation: {shown}"
    elif len(tried_labels) <= 1:
        # Legacy wording: a chain of one usable link, tried twice.
        reason = f"the reply failed validation twice: {shown}"
    else:
        tried = ", ".join(tried_labels)
        reason = (f"every link's reply failed validation ({len(tried_labels)} tried: {tried}); "
                  f"the last reply ({tried_labels[-1]}): {shown}")
    raise ReplyRejected(f"{prompt_id}: {reason}", reason=reason)


REFUSED_PROMPT_HEAD = "Your previous reply was refused"
# How many of the refusal's errors the next try is told (the log line's count).
_ERRORS_IN_RETRY = 3


def refused_prompt(user, errors) -> str:
    """*user* with the refusal of the last reply under it (DEC-259): the
    first :data:`_ERRORS_IN_RETRY` *errors*, one sentence each, and the ask
    to fix exactly those. The prompt above is byte-identical, so a budget
    measured on it still holds (a refusal adds a few dozen tokens)."""
    shown = "; ".join(str(error) for error in errors[:_ERRORS_IN_RETRY])
    more = len(errors) - _ERRORS_IN_RETRY
    if more > 0:
        shown += f"; and {more} more"
    return (f"{user}\n\n{REFUSED_PROMPT_HEAD}: {shown}. Answer again in the same format, fixing exactly that and "
            "keeping everything else as it was.")


# ---------------------------------------------------- shared story helpers

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_story(ctx):
    """``(store, story)`` for the context's story; ``StepFailed`` if there is
    none. The store prints through the step's own log, so an index rebuild it
    had to do lands in the job's feed."""
    store = StoryStore(ctx.outputs_dir, on_log=ctx.on_log)
    try:
        story = store.get(ctx.story_id)
    except KeyError:
        raise StepFailed(f"There is no story {ctx.story_id!r}.") from None
    return store, story


def require_concept(story) -> None:
    """Every bible prompt is written from the chosen concept."""
    if not story.get("concept"):
        raise StepFailed("Choose a concept first.")


def announce_trimmed(ctx, pack, already: set) -> None:
    """One ``✂️`` line per section the pack had to cut, once per step run
    (a section trimmed for batch 1 is trimmed for batch 2 too)."""
    for section in pack.trimmed:
        if section in already:
            continue
        already.add(section)
        label = _TRIMMED_LABELS.get(section, section)
        ctx.on_log(f"✂️ {label} was trimmed for the prompt (the context pack is budgeted).")
