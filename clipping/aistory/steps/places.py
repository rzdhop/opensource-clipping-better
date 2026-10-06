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

On a v2 story (phase 7, A14) each entity's ``look`` is written between its
text and its image, when it has none: D3 for a place (layout map, scale,
light per time variant, the props that live there), R1v2 for a prop (real
size against its owner's height, material, colour). The plate and the prop
image are then drawn from the look (``refimages``) -- drawn again when they
were drawn before it (a legacy story moved onto v2: :func:`apply_d3`,
:func:`apply_r1v2` drop them as the look is written). A look that fails is a
local failure like the text, finished by regenerating the text.

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

from .. import context, media_policy, prompting, prompts, refimages, schemas, universes
from .. import store as store_mod
from . import entities, llm_call, sheet_gate
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
    pack = context.build_pack(language=story["language"], story=story, template=lock, note=note,
                              setup=llm_call.setup_block(store, ctx.story_id, story, lock=lock))
    llm_call.announce_trimmed(ctx, pack, set() if announced is None else announced)
    regen = {"field": "text", "current": _place_current(place), "note": pack.note} if regenerate else None
    system, user, schema = prompts.build_p1(pack, place={"name": place["name"], "one_line": place["one_line"]},
                                            places_so_far=written, regenerate=regen)

    def validate(reply):
        errors = schemas.p1_errors(reply) or universes.brand_gate(story, reply)
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
    pack = context.build_pack(language=story["language"], story=story, template=lock, note=note,
                              setup=llm_call.setup_block(store, ctx.story_id, story, lock=lock))
    llm_call.announce_trimmed(ctx, pack, set() if announced is None else announced)
    regen = {"field": "text", "current": _prop_current(prop, cast), "note": pack.note} if regenerate else None
    system, user, schema = prompts.build_r1(
        pack, prop={"name": prop["name"], "one_line": prop["one_line"]},
        cast=[{"name": doc["name"], "role": doc["role"]} for doc in cast], regenerate=regen,
    )
    keep_owner = not regenerate

    def validate(reply):
        errors = schemas.r1_errors(reply) or universes.brand_gate(story, reply)
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


# --------------------------------------------------- D3 / R1v2 (v2: the look)

def apply_d3(doc, reply, *, props) -> None:
    """D3's reply into the place *doc* (in place) as its ``look`` -- the
    props named by D3 mapped to their ids (*props*: the story's prop
    documents) -- and clear its approval.

    A place that had no look yet loses its plates' refs (the ``cast.apply_d2``
    rule: drawn without a look, before a legacy story moved onto v2): the run
    that writes the look draws the day plate again from it, and the other
    time variants, made from the old plate, are made again on demand (a shot
    falls back to the day plate meanwhile). The variant names stay."""
    first = not doc.get("look")
    if first:
        doc["time_variants"] = {name: None for name in doc["time_variants"]}
    ids = entities.by_name(props)
    doc["look"] = {
        "layout_map": {key: reply["layout_map"][key] for key in schemas.LAYOUT_MAP_KEYS},
        "scale_note": reply["scale_note"],
        "lighting": dict(reply["lighting"]),
        "props_here": [ids[entities.name_key(name)]["prop_id"] for name in reply["props_here"]
                       if entities.name_key(name) in ids],
    }
    doc["approved_at"] = None


def write_place_look(ctx, store, place_id, *, tools, note=None, regenerate=False, announced=None) -> dict:
    """D3 for one place of a v2 story, written into its ``look``; returns the
    document. D3 reads P1's text, the style's environment rule and the
    story's props (``props_here`` is chosen among them). With *regenerate*,
    D3 is shown the current look and the *note*. ``StepFailed`` as
    ``llm_call.call_json``."""
    story = store.get(ctx.story_id)
    lock = entities.read_lock(store, ctx.story_id)
    place = store.read_entity(ctx.story_id, PLACES, place_id)
    if not (place["descriptor"] and place["layout_notes"]):
        raise StepFailed(f"{place['name']}: write the place first -- D3 reads its descriptor and layout notes.")
    props = store.list_entities(ctx.story_id, PROPS)
    cast = store.list_entities(ctx.story_id, CHARACTERS)
    pack = context.build_pack(language=story["language"], story=story, template=lock, note=note,
                              setup=llm_call.setup_block(store, ctx.story_id, story, lock=lock, hexes=True))
    llm_call.announce_trimmed(ctx, pack, set() if announced is None else announced)
    regen = None
    if regenerate and place.get("look"):
        regen = {"field": "look", "current": place["look"], "note": pack.note}
    variants = list(place["time_variants"])
    system, user, schema = prompts.build_d3(
        pack, place={"name": place["name"], "descriptor": place["descriptor"],
                     "layout_notes": place["layout_notes"], "time_variants": variants},
        environment_rules=lock["environment_rules"],
        props=[{"name": doc["name"], "one_line": doc["one_line"]} for doc in props], regenerate=regen,
    )
    prop_names = [doc["name"] for doc in props]
    names = [doc["name"] for doc in cast] + [place["name"]]

    def validate(reply):
        errors = schemas.d3_errors(reply, variants, prop_names, names) or universes.brand_gate(story, reply)
        if errors:
            return errors
        trial = copy.deepcopy(place)
        apply_d3(trial, reply, props=props)
        return schemas.place_errors(trial)

    reply = llm_call.call_json(ctx, "D3", system, user, schema, validator=validate,
                               runner=tools.runner, time_fn=tools.time_fn)
    return entities.write_entity(store, ctx.story_id, PLACES, place_id,
                                 lambda doc: apply_d3(doc, reply, props=props), now=llm_call.utc_now())


def apply_r1v2(doc, reply, *, cast, places) -> None:
    """R1v2's reply into the prop *doc* (in place) as its ``look`` -- the
    holders and places named mapped to their ids, a name matching none
    left null -- and clear its approval. A prop that had no look yet loses
    its image's ref (the ``cast.apply_d2`` rule): drawn again from the look
    by the run that wrote it."""
    if not doc.get("look"):
        doc["image"] = None
    holders, rooms = entities.by_name(cast), entities.by_name(places)

    def mapped(index, name, field):
        if name is None:
            return None
        doc_ = index.get(entities.name_key(name))
        return doc_[field] if doc_ else None

    doc["look"] = {
        "scale_cm": reply["scale_cm"],
        "material": reply["material"],
        "colour": reply["colour"],
        "scale_phrase": reply["scale_phrase"],
        "where_when": [{"ep": entry["ep"], "holder_char_id": mapped(holders, entry["holder"], "char_id"),
                        "place_id": mapped(rooms, entry["place"], "place_id"), "note": entry["note"]}
                       for entry in reply["where_when"]],
    }
    doc["approved_at"] = None


def write_prop_look(ctx, store, prop_id, *, tools, note=None, regenerate=False, announced=None) -> dict:
    """R1v2 for one prop of a v2 story, written into its ``look``; returns
    the document. R1v2 reads R1's text and its owner's build and height (when
    the owner's look is written), so its size matches the cast's scale. With
    *regenerate*, it is shown the current look and the *note*. ``StepFailed``
    as ``llm_call.call_json``."""
    story = store.get(ctx.story_id)
    lock = entities.read_lock(store, ctx.story_id)
    prop = store.read_entity(ctx.story_id, PROPS, prop_id)
    if not prop["descriptor"]:
        raise StepFailed(f"{prop['name']}: write the prop first -- R1v2 reads its descriptor.")
    cast = entities.cast_order(store.list_entities(ctx.story_id, CHARACTERS))
    places_ = store.list_entities(ctx.story_id, PLACES)
    owner_doc = next((doc for doc in cast if doc["char_id"] == prop["owner_char_id"]), None)
    owner = None
    if owner_doc is not None:
        look = owner_doc.get("look") or {}
        owner = {"name": owner_doc["name"], "build": look.get("build"), "height_cm": look.get("height_cm")}
    pack = context.build_pack(language=story["language"], story=story, template=lock, note=note,
                              setup=llm_call.setup_block(store, ctx.story_id, story, lock=lock, hexes=True))
    llm_call.announce_trimmed(ctx, pack, set() if announced is None else announced)
    regen = None
    if regenerate and prop.get("look"):
        # Its size and surface; where it has been is the knowledge's, not shown again.
        current = {key: prop["look"][key] for key in ("scale_cm", "material", "colour", "scale_phrase")}
        regen = {"field": "look", "current": current, "note": pack.note}
    system, user, schema = prompts.build_r1v2(
        pack, prop={"name": prop["name"], "one_line": prop["one_line"], "descriptor": prop["descriptor"]},
        owner=owner, cast=cast, places=places_, regenerate=regen,
    )
    names = [doc["name"] for doc in cast]

    def validate(reply):
        errors = schemas.r1v2_errors(reply, names) or universes.brand_gate(story, reply)
        if errors:
            return errors
        trial = copy.deepcopy(prop)
        apply_r1v2(trial, reply, cast=cast, places=places_)
        return schemas.prop_errors(trial)

    reply = llm_call.call_json(ctx, "R1v2", system, user, schema, validator=validate,
                               runner=tools.runner, time_fn=tools.time_fn)
    return entities.write_entity(store, ctx.story_id, PROPS, prop_id,
                                 lambda doc: apply_r1v2(doc, reply, cast=cast, places=places_),
                                 now=llm_call.utc_now())


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


def _look(run, ctx, store, eid, kind, write, tools, announced) -> bool:
    """A v2 story only (A14): D3 (a place) or R1v2 (a prop) when the entity
    has no look yet; True when it has one afterwards. The look is written
    before the image, which is drawn from it."""
    doc = store.read_entity(ctx.story_id, kind, eid)
    if doc.get("look"):
        return True
    ctx.cancel.check()
    ctx.on_log(f"🗺 {doc['name']}: look")
    try:
        write(ctx, store, eid, tools=tools, announced=announced)
    except StepFailed as exc:
        # Regenerating the text writes the look again (``regenerate``).
        run.fail(doc, "look", exc.reason, entities.target(kind, eid, "text"))
        return False
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
    v2 = media_policy.is_v2(story)

    def fill_place(place, pid):
        if not _text(run_, ctx, place, pid, PLACES, write_place_text, tools, store, announced):
            return
        if v2 and not _look(run_, ctx, store, pid, PLACES, write_place_look, tools, announced):
            return
        place = store.read_entity(ctx.story_id, PLACES, pid)
        if not entities.has_file(store, ctx.story_id, PLACES, pid, place["time_variants"].get(MASTER_PLATE)):
            _image(run_, ctx, store, place, PLACES, pid, MASTER_PLATE,
                   lambda: refimages.place_image(store, ctx.story_id, pid, MASTER_PLATE, **kwargs))

    def fill_prop(prop, rid):
        if not _text(run_, ctx, prop, rid, PROPS, write_prop_text, tools, store, announced):
            return
        if v2 and not _look(run_, ctx, store, rid, PROPS, write_prop_look, tools, announced):
            return
        prop = store.read_entity(ctx.story_id, PROPS, rid)
        if not entities.has_file(store, ctx.story_id, PROPS, rid, prop["image"]):
            _image(run_, ctx, store, prop, PROPS, rid, "image",
                   lambda: refimages.prop_image(store, ctx.story_id, rid, **kwargs))

    sheet_issues = []
    for kind, fill in ((PLACES, fill_place), (PROPS, fill_prop)):
        id_field = store_mod.ENTITY_KINDS[kind].id_field
        for doc in store.list_entities(ctx.story_id, kind):
            try:
                fill(doc, doc[id_field])
                if v2:
                    # Plan 28 F3: each image not judged yet is judged (and drawn again when it fails).
                    sheet_issues.extend(sheet_gate.review(ctx, store, kind, doc[id_field], tools=tools))
            except KeyError:
                if entities.exists(store, ctx.story_id, kind, doc[id_field]):
                    raise
                ctx.on_log(f"ℹ️ {doc['name']} was removed while the step ran; skipped.")

    if run_.failures:
        parts = "; ".join(f"{name} {part} failed ({reason})" for name, part, reason, _ in run_.failures)
        targets = list(dict.fromkeys(target for *_, target in run_.failures))
        raise StepFailed(f"Places incomplete: {parts}. Run the places step again to fill what is missing, "
                         f"or regenerate {entities.quoted_list(targets)}.")
    summary = {"created": created, "written": run_.written, "images": run_.images}
    if sheet_issues:
        summary["sheet_issues"] = sheet_issues  # plan 28 F3: the images the sheet judge still fails
    return summary
