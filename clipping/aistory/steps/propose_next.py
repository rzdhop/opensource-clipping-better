"""Step ``propose-next``: new characters and twists proposed for the episode
after one whose memory is approved (spec 2.6, 4.2 row N1; AI Story phase 5,
plan 11 stage 4).

``ctx.ep`` is the episode the memory was written from, "N"; the proposals are
for episode N+1. Needs what every series step needs (a ``ready`` story, an
episode the season plans: ``episode_common.check_episode_preconditions``
without the memory gate), an episode N+1 in the season
(:func:`require_next_episode`), and episode N's memory **written, approved
and fresh** (:func:`require_fresh_memory`: ``episode_common.memory_refusal``,
the gate's own sentences).

**One N1 call** (``llm_call.call_json``: the free chain, a paid link never
called while ``allow_paid`` is off -- DEC-115), with the bible, the cast, the
whole arc, the memory -- the hooks open when episode N+1 starts
(``series_memory.open_hooks_before(season, N + 1)``) -- and the audience
direction chosen on episode N's feedback, if any
(``series_memory.chosen_direction``). The reply is repaired (French
elisions, DEC-144) and checked: ``schemas.n1_errors`` (at most two
characters and two twists, each twist's ``target_ep`` an arc episode after
N), a proposed name that is already a character of the story -- or proposed
twice -- is refused, and the document it makes must validate; asked for once
more when it fails.

``episodes/ep{N+1}/proposals.json`` (``next_proposals_v1``) is then written
under the store lock, after the memory is checked again (still approved,
still of the revision N1 read) and every twist's ``target_ep`` is still an
episode of the arc: ``for_ep`` N+1, ``based_on {memory_ep: N, script_rev}``,
the characters as ``char_1``, ``char_2`` and the twists as ``twist_1``,
``twist_2`` (positional slugs: a name may hold any letter, an id is a URL
segment), ``decisions {}``. A re-run replaces the document: the decisions
start again (what an earlier acceptance did -- a character queued, an arc
entry amended -- stays done).

The job ends ``awaiting_approval``: each item is accepted or rejected
(``workflow.decide_proposal``), then ``proposals:<N+1>`` is approved once
every item is decided (``workflow.approve_proposals``). The story's own
document is never written (RC-M5).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy
import dataclasses
import time

from .. import context, media_policy, prompts, schemas, series_memory
from .. import store as store_mod
from . import entities, episode_common, llm_call
from .llm_call import StepFailed

STEP = "propose-next"
PROMPT = "N1"
# Plan 23 stage D5: a story whose characters may carry appearance variants
# (``media_policy.variants_enabled``) asks N1v2, whose twists may bring one.
PROMPT_V2 = "N1v2"
PROPOSALS_DOC = store_mod.EPISODE_PROPOSALS_DOC


def before(ep) -> str:
    """How a memory refusal of this step ends its sentence."""
    return f"before proposing what comes next in episode {ep + 1}"


def require_next_episode(ec) -> int:
    """Episode ``ec.ep + 1``, the one the proposals are for; ``StepFailed``
    when ``ec.ep`` is the last the season plans."""
    ep, planned = ec.ep, ec.season["episodes_planned"]
    if ep >= planned:
        raise StepFailed(f"Episode {ep} is the last the season plans: there is no episode {ep + 1} to propose for.")
    return ep + 1


def require_fresh_memory(ec) -> dict:
    """Episode ``ec.ep``'s memory entry, written, approved and fresh; the
    gate's own refusal otherwise (``episode_common.memory_refusal``)."""
    refusal = episode_common.memory_refusal(ec, ec.ep, before=before(ec.ep))
    if refusal is not None:
        raise refusal
    return series_memory.entry_for(ec.season, ec.ep)


def check(ec) -> dict:
    """Every check before the call, in the runner's order, calling nothing:
    the episode's preconditions (no memory gate), a next episode, then the
    fresh approved memory. Returns the memory entry."""
    episode_common.check_episode_preconditions(None, ec, require_memory=False)
    require_next_episode(ec)
    return require_fresh_memory(ec)


def target_episodes(season, ep) -> list:
    """The arc's episodes after *ep*: what a twist may target."""
    return sorted(entry["ep"] for entry in season["arc"] if entry["ep"] > ep)


# -------------------------------------------------------------------- N1

def _repaired(reply, language) -> None:
    """``schemas.repair_n1_reply`` in place (``call_json`` returns the very
    object the validator was handed)."""
    fixed = schemas.repair_n1_reply(reply, language)
    if isinstance(reply, dict) and isinstance(fixed, dict):
        reply.clear()
        reply.update(fixed)


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else value


def proposals_doc(reply, ep, entry, *, now) -> dict:
    """The ``next_proposals_v1`` document of an N1 *reply* for episode
    ``ep + 1``, written from *entry* (episode *ep*'s memory)."""
    characters = []
    for number, item in enumerate(reply["characters"], start=1):
        proposed = {"item_id": f"char_{number}", "name": _text(item["name"]), "role": item["role"],
                    "one_line": _text(item["one_line"]), "why": _text(item["why"])}
        if isinstance(item.get("archetype"), str) and item["archetype"].strip():
            proposed["archetype"] = item["archetype"].strip()
        characters.append(proposed)
    twists = [{"item_id": f"twist_{number}", "target_ep": item["target_ep"], "summary": _text(item["summary"]),
               "open_hooks_out": [_text(hook) for hook in item["open_hooks_out"]], "why": _text(item["why"])}
              for number, item in enumerate(reply["twists"], start=1)]
    for twist, item in zip(twists, reply["twists"]):
        variant = item.get("variant")  # N1v2 (plan 23 stage D5): only when the twist brings one
        if isinstance(variant, dict):
            twist["variant"] = {"char_id": variant["char_id"], "label": " ".join(variant["label"].split()),
                                "delta_text": " ".join(variant["delta_text"].split())}
    return {
        "$schema": schemas.NEXT_PROPOSALS_SCHEMA_NAME, "for_ep": ep + 1,
        "based_on": {"memory_ep": ep, "script_rev": entry["script_rev"]},
        "characters": characters, "twists": twists, "decisions": {},
        "created_at": now, "updated_at": now,
    }


def variant_candidates(cast) -> list:
    """The characters an N1v2 twist may give an appearance variant (plan 23
    stage D5): every written character with a look and room for one more
    (fewer than ``schemas.VARIANTS_MAX``), ``[{char_id, name, variants:
    [label]}]`` in cast order."""
    return [{"char_id": doc["char_id"], "name": doc["name"],
             "variants": [variant["label"] for variant in doc.get("variants") or ()]}
            for doc in cast if doc.get("descriptor") and doc.get("look")
            and len(doc.get("variants") or ()) < schemas.VARIANTS_MAX]


def cast_errors(reply, cast) -> list:
    """A proposed character whose name is one of *cast*'s (or proposed
    twice): accepting it would add no one (the cast path includes an
    existing name as it is)."""
    taken = {entities.name_key(doc["name"]) for doc in cast}
    errors, seen = [], set()
    for i, item in enumerate(reply.get("characters") or []):
        key = entities.name_key(item.get("name", ""))
        if key in taken:
            errors.append(f"$.characters[{i}].name: {item['name']!r} is already a character of the story")
        elif key in seen:
            errors.append(f"$.characters[{i}].name: {item['name']!r} is proposed twice")
        seen.add(key)
    return errors


def save_proposals(ctx, ec, doc, *, now) -> dict:
    """Write *doc* in episode ``ec.ep + 1``'s folder under the store lock,
    once the memory it is based on is checked again against the season as it
    is now; returns what was written."""
    store, ep = ec.store, ec.ep

    def write(_current):
        season = store.read_doc(ctx.story_id, store_mod.SEASON_DOC)
        if season is None:
            raise StepFailed("The season arc is gone: write the season again, then propose again.")
        now_ec = dataclasses.replace(ec, season=season)
        refusal = episode_common.memory_refusal(now_ec, ep, before=before(ep))
        if refusal is not None:
            raise StepFailed(f"Episode {ep}'s memory changed while the proposals were being written. {refusal}")
        if series_memory.entry_for(season, ep)["script_rev"] != doc["based_on"]["script_rev"]:
            raise StepFailed(f"Episode {ep}'s memory changed while the proposals were being written: run "
                             f"propose-next for episode {ep} again.")
        targets = set(target_episodes(season, ep))
        lost = [item["target_ep"] for item in doc["twists"] if item["target_ep"] not in targets]
        if lost:
            raise StepFailed(f"The season arc changed while the proposals were being written (episode "
                             f"{', '.join(str(number) for number in lost)} is no longer in it): run propose-next "
                             f"for episode {ep} again.")
        return copy.deepcopy(doc)

    try:
        return store.update_episode_doc(ctx.story_id, ep + 1, PROPOSALS_DOC, write, now=now)
    except schemas.SchemaError as exc:
        raise StepFailed(f"{exc.name} does not validate ({'; '.join(exc.errors[:3])}); fix it first, then run "
                         f"propose-next for episode {ep} again.") from None


def run(ctx, *, runner=None, time_fn=time.monotonic) -> dict:
    """The step (module docstring). Returns ``{ep, for_ep, characters: [names],
    twists: [target episodes], replaced}``."""
    ec = episode_common.load_episode_context(ctx)
    entry = check(ec)
    ctx.cancel.check()
    ep = ec.ep
    tools = entities.Tools(runner=runner, time_fn=time_fn)
    targets = target_episodes(ec.season, ep)
    replaced = episode_common.read_episode(dataclasses.replace(ec, ep=ep + 1), PROPOSALS_DOC) is not None

    pack = context.build_pack(language=ec.language, story=ec.story)
    llm_call.announce_trimmed(ctx, pack, set())
    n1_kwargs = dict(
        memory_ep=ep, arc=ec.season["arc"],
        cast=[{"name": doc["name"], "role": doc["role"], "one_line": doc["one_line"]} for doc in ec.cast],
        memory=ec.season, direction=series_memory.chosen_direction(ec.season, ep),
        open_hooks=series_memory.open_hooks_before(ec.season, ep + 1),
    )
    variants = media_policy.variants_enabled(ec.story)
    variant_cast = variant_candidates(ec.cast) if variants else []
    variant_ids = [entry["char_id"] for entry in variant_cast] if targets else []
    if variants:
        prompt = PROMPT_V2
        system, user, schema = prompts.build_n1_v2(pack, variant_cast=variant_cast, **n1_kwargs)
    else:
        prompt = PROMPT
        system, user, schema = prompts.build_n1(pack, **n1_kwargs)

    def validate(reply):
        _repaired(reply, ec.language)
        if variants:
            errors = schemas.n1_v2_errors(reply, target_eps=targets, variant_char_ids=variant_ids)
        else:
            errors = schemas.n1_errors(reply, target_eps=targets)
        if errors:
            return errors
        errors = cast_errors(reply, ec.cast)
        if errors:
            return errors
        return schemas.next_proposals_errors(proposals_doc(reply, ep, entry, now="check"))

    ctx.on_log(f"💡 Episode {ep + 1}: new characters and twists ({prompt}), from episode {ep}'s memory")
    reply = llm_call.call_json(ctx, prompt, system, user, schema, validator=validate, runner=tools.runner,
                               time_fn=tools.time_fn)
    now = llm_call.utc_now()
    doc = save_proposals(ctx, ec, proposals_doc(reply, ep, entry, now=now), now=now)
    names = [item["name"] for item in doc["characters"]]
    ctx.on_log(f"💡 Episode {ep + 1}'s proposals {'written again' if replaced else 'written'}: "
               f"{len(doc['characters'])} character(s), {len(doc['twists'])} twist(s). Accept or reject each, "
               f"then approve them (proposals:{ep + 1}).")
    return {"ep": ep, "for_ep": ep + 1, "characters": names,
            "twists": [item["target_ep"] for item in doc["twists"]], "replaced": replaced}
