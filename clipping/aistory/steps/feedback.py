"""Step ``feedback``: the audience feedback pasted for one episode, digested
into three directions the next episode could take (spec 2.6, 4.2 row F1; AI
Story phase 5, plan 11 stage 4).

``ctx.ep`` is the episode, "N". Needs what every series step needs (a
``ready`` story, an episode the season plans:
``episode_common.check_episode_preconditions`` without the memory gate) and
episode N's ``audience_feedback`` item (:func:`require_feedback`): the text
-- and the optional stats -- the user pasted, stored by
``workflow.store_feedback``, one item per episode, each at most 6,000
characters, refused over the cap and never trimmed.

**One F1 call** (``llm_call.call_json``: the free chain, a paid link never
called while ``allow_paid`` is off -- DEC-115). The pasted text is fenced as
untrusted data in the ask (``prompts.build_f1``), with the next episode's arc
entry when the season has one. The reply is repaired (French elisions,
DEC-144) and checked (``schemas.f1_errors``: the digest's 60 words, exactly
three directions), and asked for once more when it fails.

Then, **under the store lock** (``StoryStore.update_doc``), the digest and
the three directions are written on the item as it is now -- the item must
still be the paste F1 read (a paste replaced meanwhile fails the step and
keeps the new one) -- and any direction chosen on an earlier digest is
cleared: the indexes would point at other text.

The job ends ``awaiting_approval``: approving ``feedback:<N>`` with
``{direction: 0|1|2|null}`` stores ``chosen_direction``
(``workflow.approve_feedback``), which steers E1 of episode N+1 only
(``series_memory.chosen_direction``, read by the script step) and N1. The
story's own document is never written (RC-M5).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import time

from .. import context, prompts, schemas
from .. import store as store_mod
from . import entities, episode_common, llm_call
from .llm_call import StepFailed

STEP = "feedback"
PROMPT = "F1"


def feedback_item(season, ep):
    """Episode *ep*'s ``audience_feedback`` item (the last one: a paste
    replaces the one before, ``workflow.store_feedback``), or None."""
    for item in reversed((season or {}).get("audience_feedback") or []):
        if item.get("ep") == ep:
            return item
    return None


def require_feedback(ec) -> dict:
    """Episode ``ec.ep``'s pasted feedback; ``StepFailed`` without one."""
    item = feedback_item(ec.season, ec.ep)
    if item is None:
        raise StepFailed(f"Episode {ec.ep} has no audience feedback yet: paste it first.")
    return item


def check(ec) -> dict:
    """Every check before the call, in the runner's order, calling nothing:
    the episode's preconditions (no memory gate), then the pasted feedback.
    Returns the item."""
    episode_common.check_episode_preconditions(None, ec, require_memory=False)
    return require_feedback(ec)


def _repaired(reply, language) -> None:
    """``schemas.repair_f1_reply`` in place (``call_json`` returns the very
    object the validator was handed)."""
    fixed = schemas.repair_f1_reply(reply, language)
    if isinstance(reply, dict) and isinstance(fixed, dict):
        reply.clear()
        reply.update(fixed)


def save_digest(ctx, ec, item, reply, *, now) -> dict:
    """The digest and directions onto episode ``ec.ep``'s item as it is now,
    under the store lock; its chosen direction cleared. ``StepFailed`` when
    the item is not the paste F1 read any more. Returns the item written."""
    ep = ec.ep

    def write(season):
        current = feedback_item(season, ep)
        if current is None or (current["pasted_at"], current["text"]) != (item["pasted_at"], item["text"]):
            raise StepFailed(f"Episode {ep}'s feedback was pasted again while it was being read: run feedback for "
                             f"episode {ep} again.")
        current["digest"] = reply["digest"]
        current["directions"] = list(reply["directions"])
        current.pop("chosen_direction", None)
        return season

    try:
        season = ec.store.update_doc(ctx.story_id, store_mod.SEASON_DOC, write, now=now)
    except schemas.SchemaError as exc:
        raise StepFailed(f"{exc.name} does not validate ({'; '.join(exc.errors[:3])}); fix it first, then run "
                         f"feedback for episode {ep} again.") from None
    return feedback_item(season, ep)


def run(ctx, *, runner=None, time_fn=time.monotonic) -> dict:
    """The step (module docstring). Returns ``{ep, digest, directions}``."""
    ec = episode_common.load_episode_context(ctx)
    item = check(ec)
    ctx.cancel.check()
    tools = entities.Tools(runner=runner, time_fn=time_fn)
    pack = context.build_pack(language=ec.language, story=ec.story)
    llm_call.announce_trimmed(ctx, pack, set())
    system, user, schema = prompts.build_f1(pack, text=item["text"], stats=item.get("stats"),
                                            arc_entry=ec.next_arc_entry)

    def validate(reply):
        _repaired(reply, ec.language)
        return schemas.f1_errors(reply)

    ctx.on_log(f"💬 Episode {ec.ep}: audience feedback digest (F1), {len(item['text'])} characters pasted")
    reply = llm_call.call_json(ctx, PROMPT, system, user, schema, validator=validate, runner=tools.runner,
                               time_fn=tools.time_fn)
    saved = save_digest(ctx, ec, item, reply, now=llm_call.utc_now())
    ctx.on_log(f"💬 Episode {ec.ep}'s feedback digested: 3 directions. Choose one, or none, when approving it "
               f"(feedback:{ec.ep}).")
    return {"ep": ec.ep, "digest": saved["digest"], "directions": list(saved["directions"])}
