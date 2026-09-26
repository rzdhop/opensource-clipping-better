"""The one way a story step asks the LLM chain for JSON (spec 4.1, DEC-027).

``call_json`` wraps ``clipping.providers.llm.run_chain`` with the rules every
story-writing call shares:

- the prompt's own cap and temperature (``prompts.MAX_TOKENS`` /
  ``TEMPERATURE``), the same on a second try -- a reply is never "fixed" by
  asking for less, and never trimmed after it arrives;
- the pack budget (``context.check_budget``) before anything is sent;
- a reply the post-validator rejects is asked for once more, printed, and a
  second rejection is a :class:`StepFailed` naming the errors (spec 4.1:
  salvage -- ``run_chain`` already parses and salvages -- retry once with the
  same cap, then report);
- a chain where every link failed is a :class:`StepFailed` carrying every
  link's reason;
- the cancel token is checked before each request and handed to the chain;
- one ``✍️`` line per accepted reply, after the chain's own hop lines.

The chain and the keys come from the worker's Settings values first and the
process environment second, the precedence the web layer applies to a clip
job (``web/api/config_adapter.py``). ``clipping`` never imports ``web``, so
it is repeated here rather than shared.

Also here: the few story helpers every step runner needs (open the story,
require a chosen concept, print what the pack trimmed).

Stdlib only (DEC-012); the provider modules it reaches are stdlib at import.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from clipping import cancel as cancel_mod
from clipping.providers import errors as provider_errors
from clipping.providers import pacing, registry

from .. import context, prompts
from ..store import StoryStore
from . import StepFailed

__all__ = [
    "STORY_CALL_BUDGET_SECONDS",
    "StepFailed",
    "announce_trimmed",
    "call_json",
    "open_story",
    "require_concept",
    "resolve_chain",
    "resolve_keys",
    "utc_now",
]

# One story call may wait out the slow free floor (DEC-091): NVIDIA's 180s
# request fits with room for a first link that failed fast. Per call, not per
# step: a bible is three calls, and B3 must not be refused because B1 was slow.
STORY_CALL_BUDGET_SECONDS = 300

# How many validator errors the ⚠️ line quotes; the StepFailed quotes more.
_ERRORS_IN_LOG_LINE = 2
_ERRORS_IN_FAILURE = 5

_TRIMMED_LABELS = {
    "seed": "Seed text",
    "note": "Author's note",
    "avoid": "The list of titles to avoid",
    "bible": "The bible summary",
}


# ------------------------------------------------------------- chain + keys

def resolve_chain(settings_env) -> list:
    """The ``LLM_CHAIN`` to run: Settings, then the process env, then the
    shipped default.

    A blank value at one level falls through to the next, as it does for a
    clip job (the web layer passes ``""`` on and ``chain_from_env`` then
    reads the process env). A malformed chain raises ``ChainError``: that is
    a configuration error, not a failed call, and is not retried.
    """
    env = settings_env or {}
    spec = str(env.get("LLM_CHAIN") or "").strip()
    if not spec:
        spec = os.environ.get("LLM_CHAIN", "").strip()
    return registry.parse_chain(spec or registry.DEFAULT_LLM_CHAIN)


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


# --------------------------------------------------------------- the call

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
) -> dict:
    """One accepted JSON reply for *prompt_id* (``"C1"``, ``"B1"``, ...).

    *validator* returns a list of errors, empty for a usable reply. *runner*
    is ``llm.run_chain`` unless a test hands in a stand-in; it is looked up
    when the call is made, not when this module is imported.

    Raises ``StepFailed`` (the chain failed, or the reply was rejected
    twice), ``Cancelled``, or ``ValueError`` for a prompt over the pack
    budget -- a builder bug, never trimmed here.
    """
    context.check_budget(system, user)

    if runner is None:
        from clipping.providers import llm as llm_mod

        runner = llm_mod.run_chain

    chain = resolve_chain(ctx.settings_env)
    keys = resolve_keys(ctx.settings_env)
    cap = prompts.MAX_TOKENS[prompt_id]
    # No keyword at all for a run without a token (the CLI's NEVER), as the
    # analyzer does it.
    cancel_kwargs = cancel_mod.kwargs_for(ctx.cancel)

    errors = []
    for attempt in (1, 2):
        ctx.cancel.check()
        try:
            value, link = runner(
                chain,
                system=system,
                user=user,
                schema=schema,
                schema_name=prompts.SCHEMA_NAMES[prompt_id],
                max_tokens=cap,
                temperature=prompts.TEMPERATURE[prompt_id],
                keys=keys,
                on_log=ctx.on_log,
                deadline=time_fn() + STORY_CALL_BUDGET_SECONDS,
                time_fn=time_fn,
                **cancel_kwargs,
            )
        except provider_errors.ProviderError as exc:
            reason = _provider_reason(exc)
            raise StepFailed(f"{prompt_id}: {reason}", reason=reason) from exc

        errors = list(validator(value))
        if not errors:
            tokens = pacing.estimate_tokens(json.dumps(value, ensure_ascii=False))
            ctx.on_log(f"✍️ {prompt_id} via {_link_label(link)} ≈{tokens} tokens out (cap {cap})")
            return value

        if attempt == 1:
            shown = "; ".join(errors[:_ERRORS_IN_LOG_LINE])
            ctx.on_log(
                f"⚠️ {prompt_id} reply rejected ({shown}); asking once more with the same cap"
            )

    shown = "; ".join(errors[:_ERRORS_IN_FAILURE])
    more = len(errors) - _ERRORS_IN_FAILURE
    if more > 0:
        shown += f"; and {more} more"
    reason = f"the reply failed validation twice: {shown}"
    raise StepFailed(f"{prompt_id}: {reason}", reason=reason)


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
