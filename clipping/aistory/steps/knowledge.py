"""Step ``knowledge``: a v2 story's knowledge base, written before episode 1
and approved in the dashboard (phase 7 stage 5b, A14, DEC-228).

Needs a v2 story (``media_policy.is_v2``; a legacy story is refused with a
sentence: its episodes are written without one) whose season is approved. It
writes ``knowledge.json`` (``story_knowledge_v1``) one artifact per call,
each saved the moment it is accepted, so what was paid for survives a
failure, a cancel or the step's time budget:

- D4, the world notes: geography, period details, visual motifs -- from the
  bible, the world, the places and the props;
- D5, one call per planned episode: up to 8 beats ``{what, place, who,
  objects, knows_after}`` -- from that episode's arc entry, the beats of the
  episode before (when written), the dossiers in short form, the places and
  the props, and -- when the season put the entry on a plot archetype (plan
  20 stage 2) -- one line naming it and the beat the episode plays. A beat
  may name up to 2 new objects an episode (no prop of the story yet), kept
  by name (``new_objects``) for D6;
- D6, the props registry (once the timeline is complete): which props
  matter, and up to 3 new props for the new objects the timeline needs (at
  most 8 registered in all). A new prop is created through the places step's
  own path (``places.new_prop``, idempotent by name, as the script step's
  ``new_objects`` are): its text (R1, R1 v2) and image come with the next
  places run, which this step's last line says, with the image estimate;
- the ledger seed, no call: each character's wardrobe set (the look's first),
  location (``state.location``) and possessions (the props it owns).

A rerun skips every section already written and makes only the missing
calls. A call that fails is recorded and the others go on; the step then
ends failed naming each, keeping everything written. The calls run under the
episode steps' predictive 30-minute budget (``episode_common.Budget``): a
call starts only while it can still finish inside it, else the step ends
failed with "run the step again to continue".

Every write moves the document's ``rev`` (``StoryStore.update_knowledge``),
so an approval given before it no longer holds: ``workflow.approve_knowledge``
records ``approved_rev`` and the episode gate
(``episode_common.knowledge_refusal``) compares it.

Text for the writers, never for an image model: written in the story's
language, names allowed.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy
import time

from clipping.providers import pricing

from .. import context, media_policy, prompts, schemas
from . import entities, episode_common, llm_call
from . import places as places_step
from . import season as season_step
from .entities import CHARACTERS, PLACES, PROPS
from .llm_call import StepFailed

STEP = "knowledge"
# The link a new prop's image is drawn on (the quality preset's prop role,
# DEC-235): the final line's estimate prices it from ``pricing``.
PROP_IMAGE_LINK = "fal/seedream-4.5"

LEGACY_REFUSAL = ("The knowledge base is a v2 story's step: this story is on the legacy pipeline, whose episodes "
                  "are written without one.")
SEASON_REFUSAL = "Approve the season first: the knowledge base is written from the approved season arc."


# ------------------------------------------------------------------ reading

def require_runnable(story) -> None:
    """``StepFailed`` unless *story* is on the v2 pipeline and its season is
    approved (``approvals.season``: props the step itself creates leave the
    places, and so the derived status, unapproved until the places step has
    drawn them -- a rerun must still be possible then)."""
    if not media_policy.is_v2(story):
        raise StepFailed(LEGACY_REFUSAL)
    if not (story.get("approvals") or {}).get("season"):
        raise StepFailed(SEASON_REFUSAL)


def read_knowledge(store, story_id):
    """The story's ``knowledge.json``, or None; ``StepFailed`` for one that
    does not validate (never repaired, never overwritten unread)."""
    try:
        return store.read_knowledge(story_id)
    except schemas.SchemaError as exc:
        raise StepFailed(f"knowledge.json is not a valid {schemas.KNOWLEDGE_SCHEMA_NAME} document "
                         f"({'; '.join(exc.errors[:3])}); fix or remove it first.") from None


def _read_season(store, story_id) -> dict:
    try:
        season = store.read_doc(story_id, "season.json")
    except schemas.SchemaError as exc:
        raise StepFailed(f"season.json does not validate ({'; '.join(exc.errors[:3])}); fix it first.") from None
    if season is None or not season["approved_at"]:
        raise StepFailed(SEASON_REFUSAL)
    return season


def _timeline_eps(doc) -> list:
    return [entry["ep"] for entry in (doc or {}).get("timeline") or []]


def left(doc, planned) -> dict:
    """What a run would still write: ``{"world": bool, "episodes": [ep, ...],
    "registry": bool, "ledger": bool}`` for a document *doc* (None: nothing
    written) and a season of *planned* episodes."""
    doc = doc or {}
    written = set(_timeline_eps(doc))
    return {
        "world": "world" not in doc,
        "episodes": [ep for ep in range(1, planned + 1) if ep not in written],
        "registry": "props_registry" not in doc,
        "ledger": "ledger_seed" not in doc,
    }


def calls_left(doc, planned) -> int:
    """How many LLM calls a run would make (D4, D5 per missing episode, D6);
    the ledger seed makes none."""
    todo = left(doc, planned)
    return int(todo["world"]) + len(todo["episodes"]) + int(todo["registry"])


def describe_left(todo) -> str:
    parts = []
    if todo["world"]:
        parts.append("the world notes (D4)")
    if todo["episodes"]:
        parts.append("the timeline of episode(s) " + ", ".join(str(ep) for ep in todo["episodes"]) + " (D5)")
    if todo["registry"]:
        parts.append("the props registry (D6)")
    if todo["ledger"]:
        parts.append("the ledger seed")
    return "; ".join(parts) or "nothing"


# ------------------------------------------------------------------ writing

def _save(store, story_id, apply, *, now) -> dict:
    """Apply *apply* to ``knowledge.json`` as it is now (a fresh document
    when there is none) under the story lock; ``rev`` moves on."""

    def mutate(current):
        doc = current if current is not None else {
            "$schema": schemas.KNOWLEDGE_SCHEMA_NAME, "rev": 1, "approved_at": None, "updated_at": now}
        apply(doc)
        return doc

    try:
        return store.update_knowledge(story_id, mutate, now=now)
    except schemas.SchemaError as exc:
        raise StepFailed(f"knowledge.json could not be written ({'; '.join(exc.errors[:3])}).") from None


def _owner_names(cast) -> dict:
    return {doc["char_id"]: doc["name"] for doc in cast}


def _prop_lines(props, cast, *, one_line=False) -> list:
    owners = _owner_names(cast)
    lines = []
    for prop in props:
        line = {"name": prop["name"], "owner": owners.get(prop["owner_char_id"])}
        if one_line:
            line["one_line"] = prop["one_line"]
        lines.append(line)
    return lines


class _Story:
    """The story's entities as one call reads them, read fresh per call (a
    prop D6 creates is there for the next)."""

    def __init__(self, store, story_id):
        self.cast = entities.cast_order(store.list_entities(story_id, CHARACTERS))
        self.places = store.list_entities(story_id, PLACES)
        self.props = store.list_entities(story_id, PROPS)


# ---------------------------------------------------------------- D4 (world)

def apply_d4(reply) -> dict:
    return {"geography": reply["geography"], "period_details": reply["period_details"],
            "visual_motifs": list(reply["visual_motifs"])}


def write_world(ctx, store, story, *, tools, announced) -> dict:
    """D4: the world notes, saved into ``knowledge.json``."""
    known = _Story(store, ctx.story_id)
    pack = context.build_pack(language=story["language"], story=story,
                              setup=llm_call.setup_block(store, ctx.story_id, story))
    llm_call.announce_trimmed(ctx, pack, announced)
    system, user, schema = prompts.build_d4(
        pack,
        places=[{key: doc.get(key) for key in ("name", "one_line", "descriptor")} for doc in known.places],
        props=_prop_lines(known.props, known.cast))
    reply = llm_call.call_json(ctx, "D4", system, user, schema, validator=schemas.d4_errors,
                               runner=tools.runner, time_fn=tools.time_fn)
    world = apply_d4(reply)
    saved = _save(store, ctx.story_id, lambda doc: doc.__setitem__("world", world), now=llm_call.utc_now())
    ctx.on_log("📚 Knowledge: world notes written")
    return saved


# ------------------------------------------------------------- D5 (timeline)

def _short_dossier(doc, names) -> dict:
    """A character as D5 reads it: the dossier's goal, need, secrets and
    where each relationship stands now; without a dossier, its one-line."""
    dossier = doc.get("dossier")
    if not dossier:
        return {"name": doc["name"], "role": doc["role"], "one_line": doc["one_line"]}
    return {"name": doc["name"], "role": doc["role"], "goal": dossier["goal"], "need": dossier["need"],
            "secrets": list(dossier["secrets"]),
            "now": [{"with": names.get(item["with"], item["with"]), "now": item["now"]}
                    for item in dossier["relationships"]]}


def _episode_cast(cast, entry) -> tuple:
    """``(in full, by name)``: the arc entry's characters (else the first of
    the cast), at most ``prompts.D5_CAST_DETAILED_MAX`` in full."""
    chosen = [doc for doc in cast if doc["char_id"] in entry["characters"]] or list(cast)
    detailed = chosen[:prompts.D5_CAST_DETAILED_MAX]
    ids = {doc["char_id"] for doc in detailed}
    return detailed, [doc for doc in cast if doc["char_id"] not in ids]


def apply_d5(ep, reply, *, cast, places, props) -> tuple:
    """D5's reply as the timeline entry of episode *ep*, and the names it
    dropped (a character or a place the story does not have). An object that
    is no prop of the story is kept by name in the beat's ``new_objects``
    (for D6)."""
    chars, rooms, things = entities.by_name(cast), entities.by_name(places), entities.by_name(props)
    dropped, beats = [], []

    def char_id(name):
        doc = chars.get(entities.name_key(name))
        if doc is None:
            dropped.append(name)
        return doc and doc["char_id"]

    for item in reply["beats"]:
        who = []
        for name in item["who"]:
            cid = char_id(name)
            if cid and cid not in who:
                who.append(cid)
        place_id = None
        if item["place"]:
            room = rooms.get(entities.name_key(item["place"]))
            if room is None:
                dropped.append(item["place"])
            else:
                place_id = room["place_id"]
        objects, new = [], []
        for name in item["objects"]:
            prop = things.get(entities.name_key(name))
            if prop is not None:
                if prop["prop_id"] not in objects:
                    objects.append(prop["prop_id"])
            elif entities.name_key(name) not in {entities.name_key(other) for other in new}:
                new.append(" ".join(name.split()))
        knows = {}
        for fact in item["knows_after"]:
            cid = char_id(fact["who"])
            if cid and cid not in knows:
                knows[cid] = fact["knows"]
        beat = {"what": item["what"], "place_id": place_id, "who": who, "objects": objects, "knows_after": knows}
        if new:
            beat["new_objects"] = new
        beats.append(beat)
    return {"ep": ep, "beats": beats}, list(dict.fromkeys(dropped))


def _new_object_names(entry) -> list:
    names = []
    for beat in entry["beats"]:
        for name in beat.get("new_objects") or ():
            if entities.name_key(name) not in {entities.name_key(other) for other in names}:
                names.append(name)
    return names


def _with_entry(doc, entry) -> dict:
    timeline = [item for item in doc.get("timeline") or [] if item["ep"] != entry["ep"]] + [entry]
    doc["timeline"] = sorted(timeline, key=lambda item: item["ep"])
    return doc


def write_episode(ctx, store, story, season, ep, *, tools, announced) -> dict:
    """D5 for episode *ep*: its beats, saved into ``knowledge.json``."""
    known = _Story(store, ctx.story_id)
    entry = next(item for item in season["arc"] if item["ep"] == ep)
    current = read_knowledge(store, ctx.story_id) or {}
    before = next((item for item in current.get("timeline") or [] if item["ep"] == ep - 1), None)
    names = _owner_names(known.cast)
    detailed, others = _episode_cast(known.cast, entry)
    pack = context.build_pack(language=story["language"], story=story,
                              setup=llm_call.setup_block(store, ctx.story_id, story))
    system, user, schema = prompts.build_d5(
        pack, ep=ep, planned=season["episodes_planned"], entry=entry,
        previous=[beat["what"] for beat in before["beats"]] if before else None,
        cast=[_short_dossier(doc, names) for doc in detailed],
        others=[{"name": doc["name"], "role": doc["role"]} for doc in others],
        places=[{"name": doc["name"], "one_line": doc["one_line"]} for doc in known.places],
        props=_prop_lines(known.props, known.cast),
        archetype=season_step.entry_archetype(entry, story["language"]))

    def validate(reply):
        errors = schemas.d5_errors(reply)
        if errors:
            return errors
        trial, _ = apply_d5(ep, reply, cast=known.cast, places=known.places, props=known.props)
        new = _new_object_names(trial)
        if len(new) > schemas.D5_NEW_OBJECTS_MAX:
            return [f"$.beats: {len(new)} new objects ({', '.join(new)}), expected at most "
                    f"{schemas.D5_NEW_OBJECTS_MAX}: name a prop of the story instead"]
        return schemas.knowledge_errors(_with_entry(copy.deepcopy(current) or {
            "$schema": schemas.KNOWLEDGE_SCHEMA_NAME, "rev": 1, "approved_at": None, "updated_at": "-"}, trial))

    reply = llm_call.call_json(ctx, "D5", system, user, schema, validator=validate,
                               runner=tools.runner, time_fn=tools.time_fn)
    timeline_entry, dropped = apply_d5(ep, reply, cast=known.cast, places=known.places, props=known.props)
    saved = _save(store, ctx.story_id, lambda doc: _with_entry(doc, timeline_entry), now=llm_call.utc_now())
    for name in dropped:
        ctx.on_log(f"ℹ️ Episode {ep}'s beats: {name!r} is no character or place of the story; left out.")
    new = _new_object_names(timeline_entry)
    extra = f", new object(s): {', '.join(new)}" if new else ""
    ctx.on_log(f"📚 Knowledge: episode {ep}'s timeline written ({len(timeline_entry['beats'])} beats{extra})")
    return saved


# ------------------------------------------------------- D6 (props registry)

def timeline_objects(doc, props) -> list:
    """The objects the timeline names, for D6: each prop it names
    (``{name, new: False, episodes}``) and each new object, by name, with the
    first beat naming it (``{name, new: True, episodes, what}``)."""
    names = {prop["prop_id"]: prop["name"] for prop in props}
    found = {}
    for entry in doc.get("timeline") or []:
        for beat in entry["beats"]:
            for prop_id in beat["objects"]:
                item = found.setdefault(("prop", prop_id), {"name": names.get(prop_id, prop_id), "new": False,
                                                             "episodes": []})
                if entry["ep"] not in item["episodes"]:
                    item["episodes"].append(entry["ep"])
            for name in beat.get("new_objects") or ():
                item = found.setdefault(("new", entities.name_key(name)), {"name": name, "new": True,
                                                                          "episodes": [], "what": beat["what"]})
                if entry["ep"] not in item["episodes"]:
                    item["episodes"].append(entry["ep"])
    return list(found.values())


def _create_props(ctx, store, reply, cast) -> list:
    """The new props of D6's reply, created through the places step's own
    path (``places.new_prop``), idempotent by name: a name already taken is
    reused, never duplicated (so a rerun after a failed save is safe).
    Returns every new prop's document, created or reused."""
    owners = entities.by_name(cast)
    existing = entities.by_name(store.list_entities(ctx.story_id, PROPS))
    taken = set(store.get(ctx.story_id)["prop_ids"])
    docs = []
    for item in reply["new_props"]:
        doc = existing.get(entities.name_key(item["name"]))
        if doc is None:
            owner = owners.get(entities.name_key(item["owner"])) if item["owner"] else None
            eid = schemas.entity_id(PROPS, item["name"], taken)
            now = llm_call.utc_now()
            doc = places_step.new_prop(eid, item["name"], item["one_line"], owner and owner["char_id"], now=now)
            errors = schemas.prop_errors(doc)
            if errors:
                raise StepFailed(f"{item['name']} cannot be created: {'; '.join(errors[:3])}.")
            store.write_entity(ctx.story_id, PROPS, doc, now=now)
            existing[entities.name_key(item["name"])] = doc
            taken.add(eid)
            ctx.on_log(f"🧩 New prop: {item['name']} ({eid})")
        docs.append(doc)
    return docs


def _register(doc, registry, props) -> dict:
    """The registry into *doc*, and every beat's new objects resolved: one
    the story now has a prop for joins the beat's ``objects``; the names are
    then cleared (registered or left out)."""
    by_name = entities.by_name(props)
    doc["props_registry"] = registry
    for entry in doc.get("timeline") or []:
        for beat in entry["beats"]:
            for name in beat.pop("new_objects", None) or ():
                prop = by_name.get(entities.name_key(name))
                if prop is not None and prop["prop_id"] not in beat["objects"]:
                    beat["objects"].append(prop["prop_id"])
    return doc


def write_registry(ctx, store, story, *, tools) -> tuple:
    """D6: the props registry -- new props created first -- saved into
    ``knowledge.json``; returns ``(saved, new props created)``."""
    known = _Story(store, ctx.story_id)
    current = read_knowledge(store, ctx.story_id) or {}
    shown = known.props[:prompts.KNOWLEDGE_PROPS_SHOWN]
    pack = context.build_pack(language=story["language"], story=story,
                              setup=llm_call.setup_block(store, ctx.story_id, story))
    system, user, schema = prompts.build_d6(
        pack, objects=timeline_objects(current, known.props),
        props=_prop_lines(shown, known.cast, one_line=True),
        cast=[doc["name"] for doc in known.cast])
    shown_names = {entities.name_key(doc["name"]) for doc in shown}

    def validate(reply):
        errors = schemas.d6_errors(reply)
        unknown = [name for name in reply.get("keep") or () if entities.name_key(name) not in shown_names] \
            if not errors else []
        if unknown:
            errors = [f"$.keep: {', '.join(map(repr, unknown))} is no prop above"]
        return errors

    reply = llm_call.call_json(ctx, "D6", system, user, schema, validator=validate,
                               runner=tools.runner, time_fn=tools.time_fn)
    before = {doc["prop_id"] for doc in known.props}
    created = _create_props(ctx, store, reply, known.cast)
    props = store.list_entities(ctx.story_id, PROPS)
    by_name = entities.by_name(props)
    registry = []
    for name in list(reply["keep"]) + [item["name"] for item in reply["new_props"]]:
        prop = by_name.get(entities.name_key(name))
        if prop is not None and prop["prop_id"] not in registry:
            registry.append(prop["prop_id"])
    registry = registry[:schemas.KNOWLEDGE_PROPS_MAX]
    saved = _save(store, ctx.story_id, lambda doc: _register(doc, registry, props), now=llm_call.utc_now())
    ctx.on_log(f"📚 Knowledge: props registry written ({len(registry)} prop(s))")
    return saved, [doc for doc in created if doc["prop_id"] not in before]


# ----------------------------------------------------------- the ledger seed

def ledger_seed(cast, props) -> dict:
    """Every character's state before episode 1, from what the story holds
    (no call): the look's first wardrobe set, ``state.location`` and the
    props it owns; no injury, no relationship note yet."""
    owned = {}
    for prop in props:
        if prop["owner_char_id"]:
            owned.setdefault(prop["owner_char_id"], []).append(prop["prop_id"])
    seed = {}
    for doc in cast:
        sets = (doc.get("look") or {}).get("wardrobe_sets") or []
        seed[doc["char_id"]] = {
            "location": (doc.get("state") or {}).get("location"),
            "wardrobe_set": sets[0]["id"] if sets else None,
            "possessions": owned.get(doc["char_id"], []),
            "injuries": None,
            "relationship_notes": None,
        }
    return seed


def write_ledger(ctx, store) -> dict:
    known = _Story(store, ctx.story_id)
    seed = ledger_seed(known.cast, known.props)
    saved = _save(store, ctx.story_id, lambda doc: doc.__setitem__("ledger_seed", seed), now=llm_call.utc_now())
    ctx.on_log(f"📚 Knowledge: ledger seed written ({len(seed)} character(s))")
    return saved


# -------------------------------------------------------------------- the run

def new_props_line(created) -> str:
    """The step's last line when D6 created props: their text and image come
    with the next places run, with the image estimate."""
    names = ", ".join(doc["name"] for doc in created)
    count = len(created)
    usd = pricing.PRICES[PROP_IMAGE_LINK].usd * count
    return (f"🧩 {count} new prop(s) registered: {names}. Their text (R1, R1 v2) and image come with the next places "
            f"step run: about {count} image(s) on {PROP_IMAGE_LINK}, ≈ ${usd:.2f}. Approve them there (the story is "
            "not ready until they are), then approve the knowledge base.")


def run(ctx, *, runner=None, time_fn=time.monotonic, budget=None) -> dict:
    """The step (module docstring). *budget*: an ``episode_common.Budget``;
    None gives the step its own, started here."""
    tools = entities.Tools(runner=runner, time_fn=time_fn)
    budget = budget if budget is not None else episode_common.Budget(tools.time_fn)
    store, story = llm_call.open_story(ctx)
    require_runnable(story)
    season = _read_season(store, ctx.story_id)
    planned = season["episodes_planned"]
    todo = left(read_knowledge(store, ctx.story_id), planned)
    calls = int(todo["world"]) + len(todo["episodes"]) + int(todo["registry"])
    if not any((todo["world"], todo["episodes"], todo["registry"], todo["ledger"])):
        ctx.on_log("📚 Knowledge: every section is written; nothing to do. Review it and approve it.")
        return {"written": [], "calls": 0, "new_props": []}
    ctx.on_log(f"📚 Knowledge base: {describe_left(todo)} ({calls} call(s))")

    def remaining():
        return describe_left(left(read_knowledge(store, ctx.story_id), planned))

    announced, written, failures, created = set(), [], [], []
    if todo["world"]:
        ctx.cancel.check()
        budget.before_call(remaining)
        try:
            write_world(ctx, store, story, tools=tools, announced=announced)
            written.append("world")
        except StepFailed as exc:
            failures.append(("the world notes (D4)", exc.reason))
            ctx.on_log(f"✖ The world notes failed: {exc.reason}")
    for ep in todo["episodes"]:
        ctx.cancel.check()
        budget.before_call(remaining)
        try:
            write_episode(ctx, store, story, season, ep, tools=tools, announced=announced)
            written.append(f"timeline:{ep}")
        except StepFailed as exc:
            failures.append((f"episode {ep}'s timeline (D5)", exc.reason))
            ctx.on_log(f"✖ Episode {ep}'s timeline failed: {exc.reason}")

    doc = read_knowledge(store, ctx.story_id) or {}
    complete = not left(doc, planned)["episodes"]
    if todo["registry"] and complete:
        ctx.cancel.check()
        budget.before_call(remaining)
        try:
            _saved, created = write_registry(ctx, store, story, tools=tools)
            written.append("props_registry")
        except StepFailed as exc:
            failures.append(("the props registry (D6)", exc.reason))
            ctx.on_log(f"✖ The props registry failed: {exc.reason}")
    doc = read_knowledge(store, ctx.story_id) or {}
    if "props_registry" in doc and "ledger_seed" not in doc:
        write_ledger(ctx, store)
        written.append("ledger_seed")

    if created:
        ctx.on_log(new_props_line(created))
    if failures:
        parts = "; ".join(f"{what} failed ({reason})" for what, reason in failures)
        raise StepFailed(f"Knowledge base incomplete: {parts}. Everything written is kept; run the knowledge step "
                         "again to write the rest.")
    return {"written": written, "calls": calls, "new_props": [doc["prop_id"] for doc in created]}
