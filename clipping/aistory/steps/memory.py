"""Step ``memory``: one episode's series memory entry, from its approved
script (spec 2.6, 4.2 row S3; AI Story phase 5, plan 11 stage 4; DEC-130 as
amended).

``ctx.ep`` is the episode, "N". Needs what every series step needs (a
``ready`` story, an episode the season plans:
``episode_common.check_episode_preconditions`` without the memory gate) and
episode N's script **approved** (:func:`require_approved_script`): the
memory is written from what was approved, and records the script's ``rev``.

**One S3 call** (``llm_call.call_json``: the free chain, a paid link never
called while ``allow_paid`` is off -- DEC-115), built from the script
rendered the way E4 reads it (``prompts.script_digest``), the hooks open
before N (``series_memory.open_hooks_before``: the only strings
``hooks_closed`` may pick from, verbatim), the arc entry's own
``open_hooks_out`` (suggestions), the relationships as they stand before N
and the cast. The reply is repaired (French elisions, DEC-144) and then
checked -- ``schemas.s3_errors`` and ``series_memory.entry_errors`` -- and
asked for once more when it fails.

Then, **under the store lock** (``StoryStore.update_doc``: re-read, then
write), the entry ``{recap, hooks_opened, hooks_closed, relationship_deltas,
script_rev, at, approved_at: null}`` replaces episode N's in the season as it
is *now* (``series_memory.merge_entry``, which re-folds the derived fields):
a character deleted or the season approved while S3 ran is kept -- a delta
naming a character deleted meanwhile is dropped and printed -- and the
script must still be the approved revision the entry was written from. A
later episode's memory that closes a hook this version no longer opens
(``series_memory.FoldError``) fails the step naming it; nothing is written.

The job ends ``awaiting_approval`` (``memory:<N>``, ``workflow.
approve_memory``). A later edit of the script makes the entry stale
(``series_memory.memory_state``); that blocks a new script or storyboard of
episode N+1 (the gate) and never touches what N+1 already has. The story's
own document is never written: ``story.json``'s approvals and status do not
move (RC-M5).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import time

from .. import context, prompts, schemas, series_memory
from .. import store as store_mod
from . import entities, episode_common, llm_call
from .entities import CHARACTERS
from .episode_common import SCRIPT_DOC
from .llm_call import StepFailed

STEP = "memory"
PROMPT = "S3"


# ------------------------------------------------------------ preconditions

def require_approved_script(ec) -> dict:
    """Episode ``ec.ep``'s script, approved; ``StepFailed`` naming what is
    missing otherwise (no script, or one not approved)."""
    ep = ec.ep
    script = episode_common.read_episode(ec, SCRIPT_DOC)
    why = "its series memory is written from the approved script."
    if script is None or not script["scenes"]:
        raise StepFailed(f"Episode {ep} has no script yet: write it and approve it (script:{ep}) first; {why}")
    if not script["approved_at"]:
        raise StepFailed(f"Episode {ep}'s script is not approved yet: approve it (script:{ep}) first; {why}")
    return script


def check(ec) -> dict:
    """Every check before the call, in the runner's order, calling nothing:
    the episode's preconditions (no memory gate), then the approved script.
    Returns the script."""
    episode_common.check_episode_preconditions(None, ec, require_memory=False)
    return require_approved_script(ec)


# -------------------------------------------------------------------- S3

def script_digest(ec, script) -> str:
    """The script as E4 reads it (``prompts.script_digest`` with the same
    names), so S3 and the consistency check read one text."""
    return prompts.script_digest(script, {
        "places": {pid: doc["name"] for pid, doc in ec.entities["places"].items()},
        "cast": ec.names,
    })


def entry_of(reply, script, *, at) -> dict:
    """The ``series_memory.entries`` value of an S3 *reply*: its deltas as
    the stored ``{pair: text}``, the script's ``rev``, *at*, not approved."""
    return {
        "recap": reply["recap"],
        "hooks_opened": list(reply["hooks_opened"]),
        "hooks_closed": list(reply["hooks_closed"]),
        "relationship_deltas": {item["pair"]: item["text"] for item in reply["relationship_deltas"]},
        "script_rev": script["rev"],
        "at": at,
        "approved_at": None,
    }


def _repaired(reply, language) -> None:
    """``schemas.repair_s3_reply`` in place: ``call_json`` returns the very
    object the validator was handed."""
    fixed = schemas.repair_s3_reply(reply, language)
    if isinstance(reply, dict) and isinstance(fixed, dict):
        reply.clear()
        reply.update(fixed)


def fold_failure(ep, exc) -> str:
    """The sentence of a :class:`series_memory.FoldError` met while saving
    episode *ep*'s entry: which later episodes close which hooks this version
    no longer opens."""
    later = sorted({episode for episode, _hook in exc.closings if episode != ep})
    hooks = list(dict.fromkeys(hook for _episode, hook in exc.closings))
    if not later:
        return f"Episode {ep}'s memory was not saved: {exc}."
    quoted = ", ".join(f"“{hook}”" for hook in hooks)
    episodes = ", ".join(str(number) for number in later)
    whose = f"episode {episodes}'s memory closes" if len(later) == 1 else f"the memory of episodes {episodes} closes"
    it = "it" if len(hooks) == 1 else "them"
    return (f"Episode {ep}'s memory was not saved: this version no longer opens {quoted}, which {whose} "
            f"(a later episode's memory is written on top of this one). Run memory for episode {ep} again so "
            f"that it opens {it}; the memory as it was is kept.")


def save_entry(ctx, ec, entry, *, now) -> dict:
    """Merge *entry* into ``season.json`` as it is now, under the store lock
    (module docstring); returns the season written. ``StepFailed`` when the
    season is gone, the script moved on meanwhile, the entry no longer holds
    against the season or the cast, or the fold breaks."""
    store, ep = ec.store, ec.ep
    dropped = []

    def merge(season):
        if season is None:
            raise StepFailed("The season arc is gone: write the season again, then run memory again.")
        # The store lock is re-entrant: the script and the cast are read as they are now.
        script = store.read_episode_doc(ctx.story_id, ep, SCRIPT_DOC)
        if script is None or script["rev"] != entry["script_rev"] or not script["approved_at"]:
            raise StepFailed(f"Episode {ep}'s script changed while its memory was being written: approve it "
                             f"again (script:{ep}) if needed, then run memory for episode {ep} again.")
        cast = {doc["char_id"] for doc in store.list_entities(ctx.story_id, CHARACTERS)}
        deltas = {}
        for key, text in entry["relationship_deltas"].items():
            if all(cid in cast for cid in series_memory.pair_parts(key)):
                deltas[key] = text
            else:
                dropped.append(key)
        final = dict(entry, relationship_deltas=deltas)
        errors = series_memory.entry_errors(final, open_hooks=series_memory.open_hooks_before(season, ep),
                                            char_ids=cast)
        if errors:
            raise StepFailed(f"Episode {ep}'s memory no longer holds against the season as it is now "
                             f"({'; '.join(errors[:3])}): run memory for episode {ep} again.")
        try:
            return series_memory.merge_entry(season, ep, final)
        except series_memory.FoldError as exc:
            raise StepFailed(fold_failure(ep, exc)) from None

    try:
        saved = store.update_doc(ctx.story_id, store_mod.SEASON_DOC, merge, now=now)
    except schemas.SchemaError as exc:
        raise StepFailed(f"{exc.name} does not validate ({'; '.join(exc.errors[:3])}); fix it first, then run "
                         f"memory for episode {ep} again.") from None
    for key in dropped:
        ctx.on_log(f"ℹ️ Episode {ep}: the relationship {key} was left out -- a character of it was deleted "
                   "while the memory was being written.")
    return saved


# -------------------------------------------------------------------- run

def run(ctx, *, runner=None, time_fn=time.monotonic) -> dict:
    """The step (module docstring). Returns ``{ep, recap, hooks_opened,
    hooks_closed, relationships, script_rev, replaced}``."""
    ec = episode_common.load_episode_context(ctx)
    script = check(ec)
    ctx.cancel.check()
    ep = ec.ep
    tools = entities.Tools(runner=runner, time_fn=time_fn)
    open_hooks = series_memory.open_hooks_before(ec.season, ep)
    char_ids = [doc["char_id"] for doc in ec.cast]
    pairs = series_memory.cast_pairs(char_ids)
    replaced = series_memory.entry_for(ec.season, ep) is not None

    pack = context.build_pack(language=ec.language, story=ec.story)
    llm_call.announce_trimmed(ctx, pack, set())
    system, user, schema = prompts.build_s3(
        pack, ep=ep, script_digest=script_digest(ec, script), open_hooks=open_hooks,
        hooks_out=list(ec.arc_entry["open_hooks_out"]),
        relationship_state=series_memory.relationship_state_before(ec.season, ep),
        cast=[{"char_id": doc["char_id"], "name": doc["name"]} for doc in ec.cast],
    )

    def validate(reply):
        _repaired(reply, ec.language)
        errors = schemas.s3_errors(reply, open_hooks=open_hooks, pairs=pairs)
        if errors:
            return errors
        return series_memory.entry_errors(entry_of(reply, script, at="check"), open_hooks=open_hooks,
                                          char_ids=char_ids)

    ctx.on_log(f"🧠 Episode {ep}: series memory (S3), from the approved script (revision {script['rev']})")
    reply = llm_call.call_json(ctx, PROMPT, system, user, schema, validator=validate, runner=tools.runner,
                               time_fn=tools.time_fn)
    now = llm_call.utc_now()
    season = save_entry(ctx, ec, entry_of(reply, script, at=now), now=now)
    entry = season["series_memory"]["entries"][series_memory.memory_key(ep)]
    ctx.on_log(f"🧠 Episode {ep}'s memory {'written again' if replaced else 'written'}: "
               f"{len(entry['hooks_opened'])} hook(s) opened, {len(entry['hooks_closed'])} closed, "
               f"{len(entry['relationship_deltas'])} relationship(s). Approve it (memory:{ep}).")
    return {
        "ep": ep, "recap": entry["recap"], "hooks_opened": list(entry["hooks_opened"]),
        "hooks_closed": list(entry["hooks_closed"]), "relationships": len(entry["relationship_deltas"]),
        "script_rev": entry["script_rev"], "replaced": replaced,
    }
