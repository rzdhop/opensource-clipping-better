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

**A v2 story's second call, L1** (phase 7 stage 5d, DEC-229, A13/A14):
once S3's reply validates, one more call writes the continuity ledger --
where every character present this episode now stands (location, wardrobe
set, possessions, injuries, a relationship note) -- from the same script
digest S3 read, the ledger as it stood before this episode
(``context.ledger_before``) and the story's places, props and present
cast's own wardrobe set ids. Its reply becomes the entry's own ``ledger``
(``schemas.l1_errors`` plus the memory step's own check: a ``wardrobe_set``
belongs to that specific character, never enumerable at the schema level --
module docstring of ``schemas.l1_schema``), saved in the *same* atomic
write as the rest of the entry -- a legacy story, or a v2 one whose script
names no character, gets no ``ledger`` key at all, and the field stays
optional for every reader already written for stage 5a. Idempotent exactly
as S3 is (DEC-178): re-running the step replaces the whole entry, ledger
included, from what now stands; a script edit that makes the entry stale
(``series_memory.entry_is_stale``) makes its ledger stale with it -- the
same field, the same rule, no separate staleness of its own.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import time

from .. import context, media_policy, prompts, schemas, series_memory
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
    dropped, dropped_ledger = [], []

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
        if "ledger" in entry:
            kept = {cid: state for cid, state in entry["ledger"].items() if cid in cast}
            dropped_ledger.extend(sorted(set(entry["ledger"]) - set(kept)))
            final["ledger"] = kept
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
    for cid in dropped_ledger:
        ctx.on_log(f"ℹ️ Episode {ep}: {cid}'s ledger entry was left out -- it was deleted while the memory "
                   "was being written.")
    return saved


# -------------------------------------------------------------------- L1 (phase 7 stage 5d)

def present_characters(ec, script) -> list:
    """The characters present in episode *ec.ep*'s approved script -- the
    union of every scene's cast, in the story's own cast order (``ec.cast``):
    who L1 writes a ledger entry for (module docstring). [] for a script
    whose scenes name no character (L1 is then skipped: an empty ledger says
    nothing a missing one does not)."""
    present = {cid for scene in script["scenes"] for cid in scene.get("characters") or ()}
    return [doc for doc in ec.cast if doc["char_id"] in present]


def _l1_known_ids(ec, script, previous) -> tuple:
    """``(places, props)``: the ``{place_id/prop_id, name}`` rows L1's
    ``location``/``possessions`` may hold -- the places and props this
    episode's script actually names (bounded the way its own places are,
    ``episode_script_context_errors``'s ``max_places``; nothing bounds its
    props the same way, 5d's own choice of worst case,
    ``test_l1_worst_case_...``) plus whatever *previous* already held for a
    character, so one can be written as keeping something this episode never
    mentions again."""
    place_ids, prop_ids = set(), set()
    for scene in script["scenes"]:
        if scene.get("place_id"):
            place_ids.add(scene["place_id"])
        prop_ids.update(scene.get("props") or ())
    for state in (previous or {}).values():
        if state.get("location"):
            place_ids.add(state["location"])
        prop_ids.update(state.get("possessions") or ())
    places = [{"place_id": pid, "name": doc["name"]}
             for pid, doc in ec.entities["places"].items() if pid in place_ids]
    props = [{"prop_id": pid, "name": doc["name"]}
            for pid, doc in ec.entities["props"].items() if pid in prop_ids]
    return places, props


def _l1_present_rows(present) -> list:
    """*present* (character docs, :func:`present_characters`) as L1 reads
    them: id, name, and that character's own wardrobe set ids and contexts
    (none without a look yet -- a v2 story may write its episodes before D2
    runs; ``wardrobe_set`` can then only ever be null for it)."""
    rows = []
    for doc in present:
        sets = (doc.get("look") or {}).get("wardrobe_sets") or ()
        rows.append({"char_id": doc["char_id"], "name": doc["name"],
                     "wardrobe_sets": [{"id": s["id"], "context": s["context"]} for s in sets]})
    return rows


def _knowledge_of(ec):
    """The story's knowledge base (``knowledge.json``), or None: a legacy
    story, none written, or one that does not read. Mirrors
    ``steps.script.knowledge_of``, duplicated here rather than imported so
    this step's own imports stay its own (no cross-step coupling)."""
    if not media_policy.is_v2(ec.story):
        return None
    try:
        return ec.store.read_knowledge(ec.story_id)
    except schemas.SchemaError:
        return None


def ledger_of(reply) -> dict:
    """L1's reply (``schemas.l1_schema``'s array) as a ledger (``{char_id:
    state}``, the stored shape ``schemas.LEDGER_STATE_SCHEMA`` checks)."""
    return {item["character"]: {key: item[key] for key in
                                ("location", "wardrobe_set", "possessions", "injuries", "relationship_notes")}
            for item in reply["ledger"]}


def run_l1(ctx, ec, tools, *, digest, script, present) -> dict:
    """The L1 call (module docstring): one ledger entry per character of
    *present*, from *digest* (the same ``script_digest`` text S3 read) and
    the ledger as it stood before this episode (``context.ledger_before``).
    Checked by the schema plus one thing it cannot enforce itself -- a
    ``wardrobe_set`` belonging to that specific character
    (``schemas.l1_schema``'s section comment: "Unknown ids refused by the
    validator"). Returns the ledger (:func:`ledger_of`)."""
    previous = context.ledger_before(_knowledge_of(ec), ec.season, ec.ep) or {}
    places, props = _l1_known_ids(ec, script, previous)
    present_rows = _l1_present_rows(present)
    wardrobe_sets = {row["char_id"]: [s["id"] for s in row["wardrobe_sets"]] for row in present_rows}
    char_ids = [row["char_id"] for row in present_rows]
    place_ids = [p["place_id"] for p in places]
    prop_ids = [p["prop_id"] for p in props]

    pack = context.build_pack(language=ec.language, story=ec.story)
    system, user, schema = prompts.build_l1(
        pack, ep=ec.ep, script_digest=digest, previous=previous, present=present_rows, places=places, props=props)

    def validate(reply):
        errors = schemas.l1_errors(reply, char_ids=char_ids, place_ids=place_ids, prop_ids=prop_ids)
        if errors:
            return errors
        errors = []
        for item in reply["ledger"]:
            allowed = wardrobe_sets.get(item["character"]) or ()
            if item["wardrobe_set"] is not None and item["wardrobe_set"] not in allowed:
                errors.append(f"$.ledger: {item['wardrobe_set']!r} is no wardrobe set of {item['character']}")
        return errors

    ctx.on_log(f"🧵 Episode {ec.ep}: continuity ledger (L1), {len(char_ids)} character(s) present")
    reply = llm_call.call_json(ctx, "L1", system, user, schema, validator=validate, runner=tools.runner,
                               time_fn=tools.time_fn)
    return ledger_of(reply)


# -------------------------------------------------------------------- run

def run(ctx, *, runner=None, time_fn=time.monotonic) -> dict:
    """The step (module docstring): S3, then -- a v2 story whose script
    names at least one character -- L1. Returns ``{ep, recap, hooks_opened,
    hooks_closed, relationships, script_rev, replaced}`` (the ledger is not
    in the summary; read it from the entry, as every other reader does)."""
    ec = episode_common.load_episode_context(ctx)
    script = check(ec)
    ctx.cancel.check()
    ep = ec.ep
    tools = entities.Tools(runner=runner, time_fn=time_fn)
    open_hooks = series_memory.open_hooks_before(ec.season, ep)
    char_ids = [doc["char_id"] for doc in ec.cast]
    pairs = series_memory.cast_pairs(char_ids)
    replaced = series_memory.entry_for(ec.season, ep) is not None
    digest = script_digest(ec, script)

    pack = context.build_pack(language=ec.language, story=ec.story)
    llm_call.announce_trimmed(ctx, pack, set())
    system, user, schema = prompts.build_s3(
        pack, ep=ep, script_digest=digest, open_hooks=open_hooks,
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

    # Phase 7 stage 5d (DEC-229): a v2 story's second call, the continuity
    # ledger -- skipped for a legacy story, and for a v2 one whose script
    # names no character (present_characters then returns []).
    ledger = None
    if media_policy.is_v2(ec.story):
        present = present_characters(ec, script)
        if present:
            ledger = run_l1(ctx, ec, tools, digest=digest, script=script, present=present)

    now = llm_call.utc_now()
    entry = entry_of(reply, script, at=now)
    if ledger is not None:
        entry["ledger"] = ledger
    season = save_entry(ctx, ec, entry, now=now)
    entry = season["series_memory"]["entries"][series_memory.memory_key(ep)]
    ctx.on_log(f"🧠 Episode {ep}'s memory {'written again' if replaced else 'written'}: "
               f"{len(entry['hooks_opened'])} hook(s) opened, {len(entry['hooks_closed'])} closed, "
               f"{len(entry['relationship_deltas'])} relationship(s). Approve it (memory:{ep}).")
    return {
        "ep": ep, "recap": entry["recap"], "hooks_opened": list(entry["hooks_opened"]),
        "hooks_closed": list(entry["hooks_closed"]), "relationships": len(entry["relationship_deltas"]),
        "script_rev": entry["script_rev"], "replaced": replaced,
    }
