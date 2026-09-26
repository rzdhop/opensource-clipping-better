"""Step ``places``: the story's places and props, written and drawn (spec 3
step 6, 2.4, 2.5; phase-2 plan, DEC-120).

``params = {"places": [{name, one_line}], "props": [{name, one_line, owner}]}``
-- the list the user confirmed -- or, when neither is given, the saved
``places_proposal.json`` (none: "Propose or list the places first."). A
prop's owner is a character's id or name (one matching no character is
printed and left out). Every place and prop of the list that does not exist
yet (by name) is created.

Then the step **fills what is missing**, for every place and every prop of
the story:

- a place's text (``descriptor`` is null): P1, shown the places already
  written -- the descriptor, the layout notes, the time variants it proposes
  (each ``null`` until made; ``day`` is always there) and the
  ``prompt_block``; then its master plate (``day``) when it has none. Other
  variants are made on demand (``regenerate place:<id>:image:<variant>``);
- a prop's text: R1 -- the descriptor, the owner (R1's name mapped to an id;
  an owner the user gave is kept) and the ``prompt_block``; then its image.

The plates and the prop images are text to image (never an editor). Each
part is a local failure: printed, recorded, the next still runs; the image
of an entity whose text failed is left for the next run. Anything written
clears the entity's ``approved_at``. With nothing missing it calls nothing.

Returns ``{created, written, images}``; a failed part ends the step with a
``StepFailed`` naming the entity, the part, the reason and the target that
finishes it.
"""

from __future__ import annotations

import copy
import time

from .. import context, prompting, prompts, refimages, schemas
from .. import store as store_mod
from . import entities, llm_call
from .entities import CHARACTERS, PLACES, PROPS
from .llm_call import StepFailed

MASTER_PLATE = schemas.MASTER_PLATE_VARIANT


# -------------------------------------------------------------- the list

def _items(value, what, *, owner=False) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise StepFailed(f"'{what}' must be a list of {{name, one_line{', owner' if owner else ''}}}.")
    items = []
    for entry in value:
        if not isinstance(entry, dict):
            raise StepFailed(f"Every entry of '{what}' must be {{name, one_line{', owner' if owner else ''}}}.")
        name, one_line = entry.get("name"), entry.get("one_line")
        if not (isinstance(name, str) and name.strip()):
            raise StepFailed(f"Every entry of '{what}' needs a name.")
        if not (isinstance(one_line, str) and one_line.strip()):
            raise StepFailed(f"{name.strip()}: a one-line description is needed.")
        item = {"name": name.strip(), "one_line": one_line.strip()}
        if owner:
            value_owner = entry.get("owner")
            if value_owner is not None and not isinstance(value_owner, str):
                raise StepFailed(f"{item['name']}: the owner must be a character's id or name.")
            item["owner"] = value_owner.strip() if isinstance(value_owner, str) and value_owner.strip() else None
        items.append(item)
    return items


def _listed(store, story_id, params) -> tuple:
    """``(places, props)`` asked for: the params, else the saved proposal."""
    if params.get("places") is None and params.get("props") is None:
        try:
            proposal = store.read_doc(story_id, store_mod.PLACES_PROPOSAL_DOC)
        except schemas.SchemaError as exc:
            raise StepFailed(f"{store_mod.PLACES_PROPOSAL_DOC} cannot be read ({'; '.join(exc.errors[:3])}); "
                             "propose the places again.") from None
        if proposal is None:
            raise StepFailed("Propose or list the places first.")
        params = proposal
    return _items(params.get("places"), "places"), _items(params.get("props"), "props", owner=True)


def _owner_id(owner, cast):
    """A character's id for *owner* (an id or a name), or None."""
    if owner is None:
        return None
    for doc in cast:
        if doc["char_id"] == owner:
            return owner
    doc = entities.by_name(cast).get(entities.name_key(owner))
    return doc["char_id"] if doc else None


def new_place(place_id, name, one_line, *, now) -> dict:
    return {
        "$schema": schemas.PLACE_SCHEMA_NAME, "place_id": place_id, "name": name, "one_line": one_line,
        "descriptor": None, "layout_notes": None, "time_variants": {MASTER_PLATE: None},
        "prompt_block": None, "approved_at": None, "created_at": now, "updated_at": now,
    }


def new_prop(prop_id, name, one_line, owner_char_id, *, now) -> dict:
    return {
        "$schema": schemas.PROP_SCHEMA_NAME, "prop_id": prop_id, "name": name, "one_line": one_line,
        "descriptor": None, "owner_char_id": owner_char_id, "image": None, "prompt_block": None,
        "approved_at": None, "created_at": now, "updated_at": now,
    }


def _create(ctx, store, params) -> list:
    """Create the listed places and props that do not exist yet; their ids.
    Every document is checked before the first is written."""
    places, props = _listed(store, ctx.story_id, params)
    cast = store.list_entities(ctx.story_id, CHARACTERS)
    docs = []
    for kind, items in ((PLACES, places), (PROPS, props)):
        existing = store.list_entities(ctx.story_id, kind)
        names = {entities.name_key(doc["name"]) for doc in existing}
        taken = {doc[store_mod.ENTITY_KINDS[kind].id_field] for doc in existing}
        for item in items:
            if entities.name_key(item["name"]) in names:
                continue
            eid = schemas.entity_id(kind, item["name"], taken)
            now = llm_call.utc_now()
            if kind == PLACES:
                doc, errors_of = new_place(eid, item["name"], item["one_line"], now=now), schemas.place_errors
            else:
                owner = _owner_id(item["owner"], cast)
                if item["owner"] is not None and owner is None:
                    ctx.on_log(f"ℹ️ {item['name']}: owner {item['owner']!r} is no character of the story; "
                               "created without an owner.")
                doc = new_prop(eid, item["name"], item["one_line"], owner, now=now)
                errors_of = schemas.prop_errors
            errors = errors_of(doc)
            if errors:
                raise StepFailed(f"{item['name']} cannot be created: {'; '.join(errors[:3])}.")
            docs.append((kind, doc))
            names.add(entities.name_key(item["name"]))
            taken.add(eid)
    if not docs and not store.list_entities(ctx.story_id, PLACES):
        raise StepFailed("Propose or list the places first.")
    for kind, doc in docs:
        store.write_entity(ctx.story_id, kind, doc, now=doc["created_at"])
        ctx.on_log(f"🗺 {doc['name']}: created ({'place' if kind == PLACES else 'prop'})")
    return [doc[store_mod.ENTITY_KINDS[kind].id_field] for kind, doc in docs]


# ------------------------------------------------------------------------ P1

def apply_p1(doc, reply, *, lock) -> None:
    """P1's reply into the place *doc* (in place): the variants it proposes
    join ``day``; a variant that already has an image is kept."""
    doc["descriptor"] = reply["descriptor"]
    doc["layout_notes"] = reply["layout_notes"]
    old = doc["time_variants"]
    variants = {MASTER_PLATE: old.get(MASTER_PLATE)}
    for name in reply["time_variants"]:
        variants.setdefault(name, old.get(name))
    for name, ref in old.items():
        if ref is not None:
            variants.setdefault(name, ref)
    doc["time_variants"] = variants
    doc["prompt_block"] = prompting.place_prompt_block(lock, descriptor=doc["descriptor"],
                                                       layout_notes=doc["layout_notes"])
    doc["approved_at"] = None


def _place_current(place) -> dict:
    current = {}
    for key in ("descriptor", "layout_notes"):
        if place[key]:
            current[key] = place[key]
    current["time_variants"] = list(place["time_variants"])
    return current


def write_place_text(ctx, store, place_id, *, tools, note=None, regenerate=False, announced=None) -> dict:
    """P1 for one place, written into its document; returns the document.
    ``StepFailed`` as ``llm_call.call_json``. With *regenerate*, P1 is shown
    the current values and the *note*; the images stay."""
    story = store.get(ctx.story_id)
    lock = entities.read_lock(store, ctx.story_id)
    place = store.read_entity(ctx.story_id, PLACES, place_id)
    written = [{key: doc[key] for key in ("name", "one_line", "descriptor")}
               for doc in store.list_entities(ctx.story_id, PLACES)
               if doc["place_id"] != place_id and doc["descriptor"]]
    pack = context.build_pack(language=story["language"], story=story, template=lock, note=note)
    llm_call.announce_trimmed(ctx, pack, set() if announced is None else announced)
    regen = {"field": "text", "current": _place_current(place), "note": pack.note} if regenerate else None
    system, user, schema = prompts.build_p1(pack, place={"name": place["name"], "one_line": place["one_line"]},
                                            places_so_far=written, regenerate=regen)

    def validate(reply):
        errors = schemas.p1_errors(reply)
        if errors:
            return errors
        trial = copy.deepcopy(place)
        apply_p1(trial, reply, lock=lock)
        return schemas.place_errors(trial)

    reply = llm_call.call_json(ctx, "P1", system, user, schema, validator=validate,
                               runner=tools.runner, time_fn=tools.time_fn)
    return entities.write_entity(store, ctx.story_id, PLACES, place_id,
                                 lambda doc: apply_p1(doc, reply, lock=lock), now=llm_call.utc_now())


# ------------------------------------------------------------------------ R1

def apply_r1(doc, reply, *, cast, lock, keep_owner):
    """R1's reply into the prop *doc* (in place). With *keep_owner* (the
    places step) an owner the prop already has is kept and R1's is used only
    when it has none; without (a regenerate) R1's owner replaces it, except
    that a name matching no character keeps the current one. Returns that
    unmatched name, or None."""
    doc["descriptor"] = reply["descriptor"]
    owner = reply.get("owner")
    owner_id = _owner_id(owner, cast)
    unknown = owner if owner is not None and owner_id is None else None
    if keep_owner and doc["owner_char_id"]:
        pass
    elif unknown is None:
        doc["owner_char_id"] = owner_id
    elif not keep_owner:
        pass  # an unmatched name keeps the current owner
    else:
        doc["owner_char_id"] = None
    doc["prompt_block"] = prompting.prop_prompt_block(lock, descriptor=doc["descriptor"])
    doc["approved_at"] = None
    return unknown


def _prop_current(prop, cast) -> dict:
    current = {}
    if prop["descriptor"]:
        current["descriptor"] = prop["descriptor"]
    names = {doc["char_id"]: doc["name"] for doc in cast}
    if prop["owner_char_id"]:
        current["owner"] = names.get(prop["owner_char_id"], prop["owner_char_id"])
    return current


def write_prop_text(ctx, store, prop_id, *, tools, note=None, regenerate=False, announced=None) -> dict:
    """R1 for one prop, written into its document; returns the document.
    ``StepFailed`` as ``llm_call.call_json``. With *regenerate*, R1 is shown
    the current values and the *note*, and its owner replaces the current
    one; the image stays."""
    story = store.get(ctx.story_id)
    lock = entities.read_lock(store, ctx.story_id)
    prop = store.read_entity(ctx.story_id, PROPS, prop_id)
    cast = entities.cast_order(store.list_entities(ctx.story_id, CHARACTERS))
    pack = context.build_pack(language=story["language"], story=story, template=lock, note=note)
    llm_call.announce_trimmed(ctx, pack, set() if announced is None else announced)
    regen = {"field": "text", "current": _prop_current(prop, cast), "note": pack.note} if regenerate else None
    system, user, schema = prompts.build_r1(
        pack, prop={"name": prop["name"], "one_line": prop["one_line"]},
        cast=[{"name": doc["name"], "role": doc["role"]} for doc in cast], regenerate=regen,
    )
    keep_owner = not regenerate

    def validate(reply):
        errors = schemas.r1_errors(reply)
        if errors:
            return errors
        trial = copy.deepcopy(prop)
        apply_r1(trial, reply, cast=cast, lock=lock, keep_owner=keep_owner)
        return schemas.prop_errors(trial)

    reply = llm_call.call_json(ctx, "R1", system, user, schema, validator=validate,
                               runner=tools.runner, time_fn=tools.time_fn)
    unknown = []

    def write(doc):
        name = apply_r1(doc, reply, cast=cast, lock=lock, keep_owner=keep_owner)
        if name is not None:
            unknown.append(name)

    saved = entities.write_entity(store, ctx.story_id, PROPS, prop_id, write, now=llm_call.utc_now())
    for name in unknown:
        ctx.on_log(f"ℹ️ {prop['name']}: R1 named the owner {name!r}, no character of the story; not used.")
    return saved


# -------------------------------------------------------------------- the run

class _Run:
    def __init__(self, ctx):
        self.ctx = ctx
        self.written = []
        self.images = {}
        self.failures = []  # (name, part, reason, target)

    def fail(self, doc, part, reason, target):
        self.failures.append((doc["name"], part, reason, target))
        self.ctx.on_log(f"✖ {doc['name']} {part} failed: {reason}")


def _text(run, ctx, doc, eid, kind, write, tools, store, announced) -> bool:
    if doc["descriptor"]:
        return True
    ctx.cancel.check()
    ctx.on_log(f"🗺 {doc['name']}: text")
    try:
        write(ctx, store, eid, tools=tools, announced=announced)
    except StepFailed as exc:
        run.fail(doc, "text", exc.reason, entities.target(kind, eid, "text"))
        return False
    run.written.append(eid)
    return True


def _image(run, ctx, store, doc, kind, eid, slot, make) -> None:
    ctx.cancel.check()
    ctx.on_log(f"🗺 {doc['name']}: {slot}")
    target = entities.target(kind, eid, "image", slot) if kind == PLACES else entities.target(kind, eid, "image")
    try:
        ref = make()
    except refimages.RefImageError as exc:
        run.fail(doc, slot, str(exc), target)
        return
    run.images.setdefault(eid, {})[slot] = ref["consistency"]
    entities.clear_approval(store, ctx.story_id, kind, eid, now=ref["created_at"])


def run(ctx, *, runner=None, time_fn=time.monotonic, sleep_fn=time.sleep, adapters=None, transport=None) -> dict:
    tools = entities.Tools(runner=runner, time_fn=time_fn, sleep_fn=sleep_fn, adapters=adapters,
                           transport=transport)
    store, story = llm_call.open_story(ctx)
    entities.require_status(story, "style_approved", "Approve the style first.")
    entities.read_lock(store, ctx.story_id)
    created = _create(ctx, store, ctx.params or {})

    run_ = _Run(ctx)
    announced = set()
    kwargs = tools.image_kwargs(ctx)

    def fill_place(place, pid):
        if not _text(run_, ctx, place, pid, PLACES, write_place_text, tools, store, announced):
            return
        place = store.read_entity(ctx.story_id, PLACES, pid)
        if not entities.has_file(store, ctx.story_id, PLACES, pid, place["time_variants"].get(MASTER_PLATE)):
            _image(run_, ctx, store, place, PLACES, pid, MASTER_PLATE,
                   lambda: refimages.place_image(store, ctx.story_id, pid, MASTER_PLATE, **kwargs))

    def fill_prop(prop, rid):
        if not _text(run_, ctx, prop, rid, PROPS, write_prop_text, tools, store, announced):
            return
        prop = store.read_entity(ctx.story_id, PROPS, rid)
        if not entities.has_file(store, ctx.story_id, PROPS, rid, prop["image"]):
            _image(run_, ctx, store, prop, PROPS, rid, "image",
                   lambda: refimages.prop_image(store, ctx.story_id, rid, **kwargs))

    for kind, fill in ((PLACES, fill_place), (PROPS, fill_prop)):
        id_field = store_mod.ENTITY_KINDS[kind].id_field
        for doc in store.list_entities(ctx.story_id, kind):
            try:
                fill(doc, doc[id_field])
            except KeyError:
                if entities.exists(store, ctx.story_id, kind, doc[id_field]):
                    raise
                ctx.on_log(f"ℹ️ {doc['name']} was removed while the step ran; skipped.")

    if run_.failures:
        parts = "; ".join(f"{name} {part} failed ({reason})" for name, part, reason, _ in run_.failures)
        targets = list(dict.fromkeys(target for *_, target in run_.failures))
        raise StepFailed(f"Places incomplete: {parts}. Run the places step again to fill what is missing, "
                         f"or regenerate {entities.quoted_list(targets)}.")
    return {"created": created, "written": run_.written, "images": run_.images}
