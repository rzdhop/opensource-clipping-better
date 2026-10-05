"""Step ``season``: the season arc, a skeleton then each entry in full (spec 3
step 7, 2.6, 4.2 rows S1/S2).

``params = {"episodes": N}`` -- 3 to 12, 8 when not given. Needs the cast
approved.

S1 writes the skeleton: N entries (function + a short summary), saved as
``season.json`` (``season_arc_v1``) **before** any S2, each entry with empty
hooks and characters; ``series_memory`` and ``audience_feedback`` start empty
(or stay as they were, when a season is written again). A rewritten arc is
unapproved: ``approvals.season`` and the arc's ``approved_at`` are cleared.

Then S2 per entry (the entry, the arc as it stands, the cast): the summary in
full (<= 60 words), the hooks it resolves and leaves open, the characters in
it (K1-style names mapped to ids; an unknown name is dropped and printed).
``season.json`` is written after each entry, so what was paid for survives a
failure or a cancel. An entry whose S2 fails keeps its S1 summary; the step
then ends failed naming it and its ``season:<ep>`` target (DEC-027).

The other entries are shown to S2 as short lines -- each summary at most
S1's own length -- so twelve expanded entries still fit the pack budget; the
cut is printed once (spec 0: never silent).

Plot archetypes (plan 20 stage 2): on a v2 story (``media_policy.is_v2``) S1
is shown the archetype library (``templates/archetypes``) and picks the
season's primary and at most one secondary that pairs with it; each entry
names the one whose beat it plays (prompt id ``S1v2``). ``season.json``
keeps the choice (``archetypes``) and each entry's ``archetype``; S2 is then
told its entry's beat. A legacy story's S1 and S2 are exactly as before.
"""

from __future__ import annotations

import copy
import time

from .. import context, media_policy, prompts, schemas, templates
from .. import store as store_mod
from . import entities, llm_call
from .entities import CHARACTERS, PLACES
from .llm_call import StepFailed

DEFAULT_EPISODES = 8
TARGET_PREFIX = "season:"
# The story status the step needs (``defaults.STATUSES``).
REQUIRED_STATUS = "cast_approved"

_EMPTY_MEMORY = {"recaps": {}, "open_hooks": [], "relationship_state": {}, "introduced": {}}


def read_season(store, story_id):
    """The story's ``season.json``, or None; ``StepFailed`` for one that does
    not validate (never repaired, and never overwritten unread)."""
    try:
        return store.read_doc(story_id, store_mod.SEASON_DOC)
    except schemas.SchemaError as exc:
        raise StepFailed(f"{store_mod.SEASON_DOC} is not a valid {schemas.SEASON_ARC_SCHEMA_NAME} document "
                         f"({'; '.join(exc.errors[:3])}); fix or remove it first.") from None


def _episodes(params) -> int:
    value = params.get("episodes", DEFAULT_EPISODES)
    if value is None:
        value = DEFAULT_EPISODES
    low, high = schemas.EPISODES_PLANNED_MIN, schemas.EPISODES_PLANNED_MAX
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise StepFailed(f"A season has {low} to {high} episodes, not {value!r}.")
    return value


def _cast_lines(cast) -> list:
    return [{key: doc[key] for key in ("name", "role", "one_line")} for doc in cast]


def _clear_approval(store, story_id, *, now) -> None:
    def clear(doc):
        doc["approvals"]["season"] = None

    store.update(story_id, clear, now=now)


# ------------------------------------------------------------------------ S1

def _skeleton(reply, episodes, previous, *, now) -> dict:
    """``season.json`` from S1's *reply*; the archetypes, when the reply
    chose them (a v2 story), on the document and on each entry."""
    previous = previous or {}
    arc = []
    for entry in reply["arc"]:
        item = {"ep": entry["ep"], "function": entry["function"], "summary": entry["summary"],
                "open_hooks_in": [], "open_hooks_out": [], "characters": []}
        if "archetype" in entry:
            item["archetype"] = entry["archetype"]
        arc.append(item)
    doc = {
        "$schema": schemas.SEASON_ARC_SCHEMA_NAME,
        "episodes_planned": episodes,
        "arc": arc,
        "series_memory": copy.deepcopy(previous.get("series_memory") or _EMPTY_MEMORY),
        "audience_feedback": copy.deepcopy(previous.get("audience_feedback") or []),
        "approved_at": None,
        "updated_at": now,
    }
    if "archetypes" in reply:
        doc["archetypes"] = {"primary": reply["archetypes"]["primary"],
                             "secondary": reply["archetypes"]["secondary"]}
    return doc


def _archetype_pick_list(language) -> list:
    """The library as S1 is shown it: id, premise (in *language*), pairs."""
    return [{"id": item["id"], "premise": item["premise"], "pairs_well_with": list(item["pairs_well_with"])}
            for item in (templates.localize_archetype(a, language) for a in templates.load_archetypes())]


def entry_archetype(entry, language):
    """``{label, beat}`` of the plot archetype arc *entry* plays, its beat
    for the entry's function, in *language*; None when the entry names none
    (or one the library no longer ships: the season's validation names it)."""
    archetype_id = entry.get("archetype")
    if not archetype_id:
        return None
    try:
        archetype = templates.localize_archetype(templates.load_archetype(archetype_id), language)
    except KeyError:
        return None
    return {"label": archetype["label"], "beat": templates.archetype_beat(archetype, entry["function"])}


# ------------------------------------------------------------------------ S2

def _arc_view(arc, ep):
    """The arc as S2 is shown it: the entry to expand whole, every other one
    cut to S1's summary length. ``(view, was_cut)``."""
    view, cut = [], False
    for entry in arc:
        summary = entry["summary"]
        if entry["ep"] != ep:
            summary, trimmed = context.trim_words(summary, schemas.S1_SUMMARY_MAX_WORDS)
            cut = cut or trimmed
        view.append({"ep": entry["ep"], "function": entry["function"], "summary": summary})
    return view, cut


def _characters(names, cast) -> tuple:
    """``([char ids], [names matching no character])``, in S2's order, once each."""
    known = entities.by_name(cast)
    ids, dropped = [], []
    for name in names:
        doc = known.get(entities.name_key(name))
        if doc is None:
            dropped.append(name)
        elif doc["char_id"] not in ids:
            ids.append(doc["char_id"])
    return ids, dropped


def apply_s2(season, ep, reply, cast) -> list:
    """S2's reply into entry *ep* of *season* (in place); returns the
    character names that matched no one."""
    ids, dropped = _characters(reply["characters"], cast)
    entry = season["arc"][ep - 1]
    entry["summary"] = reply["summary"]
    entry["open_hooks_in"] = list(reply["open_hooks_in"])
    entry["open_hooks_out"] = list(reply["open_hooks_out"])
    entry["characters"] = ids
    season["approved_at"] = None
    return dropped


def _entry_current(entry, cast) -> dict:
    names = {doc["char_id"]: doc["name"] for doc in cast}
    current = {"summary": entry["summary"]}
    for key in ("open_hooks_in", "open_hooks_out"):
        if entry[key]:
            current[key] = list(entry[key])
    if entry["characters"]:
        current["characters"] = [names.get(cid, cid) for cid in entry["characters"]]
    return current


def expand_entry(ctx, store, ep, *, tools, note=None, regenerate=False, announced=None) -> dict:
    """S2 for arc entry *ep*, written into ``season.json``; returns the entry.

    ``StepFailed`` as ``llm_call.call_json``, or when there is no arc or no
    such entry. With *regenerate*, S2 is shown the entry's current values and
    the *note*.
    """
    announced = set() if announced is None else announced
    story = store.get(ctx.story_id)
    season = read_season(store, ctx.story_id)
    target = f"{TARGET_PREFIX}{ep}"
    if season is None or not season["arc"]:
        raise StepFailed(f"Cannot regenerate {target!r}: there is no season arc yet; write the season first.")
    if not 1 <= ep <= len(season["arc"]):
        raise StepFailed(f"Cannot regenerate {target!r}: the arc has episodes 1 to {len(season['arc'])}.")
    entry = season["arc"][ep - 1]
    cast = entities.cast_order(store.list_entities(ctx.story_id, CHARACTERS))

    pack = context.build_pack(language=story["language"], story=story, note=note,
                              setup=llm_call.setup_block(store, ctx.story_id, story))
    llm_call.announce_trimmed(ctx, pack, announced)
    view, cut = _arc_view(season["arc"], ep)
    if cut and "arc" not in announced:
        announced.add("arc")
        ctx.on_log(f"✂️ The other episodes' summaries were shortened to {schemas.S1_SUMMARY_MAX_WORDS} words "
                   "for the prompt (the context pack is budgeted).")
    regen = {"field": "text", "current": _entry_current(entry, cast), "note": pack.note} if regenerate else None
    system, user, schema = prompts.build_s2(pack, entry=view[ep - 1], arc=view, cast=_cast_lines(cast),
                                            regenerate=regen, archetype=entry_archetype(entry, story["language"]))

    def validate(reply):
        errors = schemas.s2_errors(reply)
        if errors:
            return errors
        trial = copy.deepcopy(season)
        apply_s2(trial, ep, reply, cast)
        return schemas.season_arc_errors(trial)

    reply = llm_call.call_json(ctx, "S2", system, user, schema, validator=validate,
                               runner=tools.runner, time_fn=tools.time_fn)

    # The arc as it now stands: another entry written meanwhile is kept.
    current = read_season(store, ctx.story_id)
    if current is None or len(current["arc"]) < ep:
        raise StepFailed(f"The season arc changed while episode {ep} was being written; write it again.")
    dropped = apply_s2(current, ep, reply, cast)
    now = llm_call.utc_now()
    saved = store.write_doc(ctx.story_id, store_mod.SEASON_DOC, current, now=now)
    for name in dropped:
        ctx.on_log(f"ℹ️ Episode {ep}: {name!r} is no character of the story; left out.")
    ctx.on_log(f"📅 Episode {ep} ({entry['function']}) written")
    return saved["arc"][ep - 1]


# -------------------------------------------------------------------- the run

def run(ctx, *, runner=None, time_fn=time.monotonic) -> dict:
    tools = entities.Tools(runner=runner, time_fn=time_fn)
    store, story = llm_call.open_story(ctx)
    entities.require_status(story, REQUIRED_STATUS, "Approve the cast first.")
    episodes = _episodes(ctx.params or {})
    previous = read_season(store, ctx.story_id)
    cast = entities.cast_order(store.list_entities(ctx.story_id, CHARACTERS))
    places = store.list_entities(ctx.story_id, PLACES)
    ctx.cancel.check()

    announced = set()
    pack = context.build_pack(language=story["language"], story=story,
                              setup=llm_call.setup_block(store, ctx.story_id, story, episodes=episodes))
    llm_call.announce_trimmed(ctx, pack, announced)
    archetypes = _archetype_pick_list(story["language"]) if media_policy.is_v2(story) else None
    system, user, schema = prompts.build_s1(
        pack, episodes=episodes, cast=_cast_lines(cast),
        places=[{"name": doc["name"], "one_line": doc["one_line"]} for doc in places],
        archetypes=archetypes,
    )
    pairs = {item["id"]: item["pairs_well_with"] for item in archetypes or ()}

    def validate(reply):
        if archetypes is None:
            errors = schemas.s1_errors(reply, episodes)
        else:
            errors = schemas.s1_archetype_errors(reply, episodes, pairs)
        if errors:
            return errors
        return schemas.season_arc_errors(_skeleton(reply, episodes, previous, now=llm_call.utc_now()))

    reply = llm_call.call_json(ctx, "S1" if archetypes is None else "S1v2", system, user, schema,
                               validator=validate, runner=runner, time_fn=time_fn)
    now = llm_call.utc_now()
    store.write_doc(ctx.story_id, store_mod.SEASON_DOC, _skeleton(reply, episodes, previous, now=now), now=now)
    _clear_approval(store, ctx.story_id, now=now)
    chosen = reply.get("archetypes")
    if chosen is None:
        ctx.on_log(f"📅 Season arc: {episodes} episodes outlined")
    else:
        spine = chosen["primary"] + (f" + {chosen['secondary']}" if chosen["secondary"] else "")
        ctx.on_log(f"📅 Season arc: {episodes} episodes outlined (plot archetypes: {spine})")

    expanded, failed = [], []
    for ep in range(1, episodes + 1):
        ctx.cancel.check()
        try:
            expand_entry(ctx, store, ep, tools=tools, announced=announced)
        except StepFailed as exc:
            failed.append((ep, exc.reason))
            ctx.on_log(f"✖ Episode {ep} failed: {exc.reason}")
            continue
        expanded.append(ep)

    if failed:
        parts = "; ".join(f"episode {ep} failed ({reason})" for ep, reason in failed)
        targets = [f"{TARGET_PREFIX}{ep}" for ep, _ in failed]
        raise StepFailed(f"Season arc incomplete: {parts}. Each keeps its outline; regenerate "
                         f"{entities.quoted_list(targets)} to finish it.")
    return {"episodes": episodes, "expanded": expanded}
