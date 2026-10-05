"""Step ``places_proposal``: 2-3 places and a few props, proposed for the user
to edit (spec 3 step 6; phase-2 plan 1.2, DEC-120).

One small call (P0) from the bible, the world and the cast -- each
character's name and signature items, where props usually come from. The
reply is saved as ``places_proposal.json`` (``places_proposal_v1``), a prop's
owner as the character's id (a name that matches no character is printed
and left without an owner). Nothing else is written: the places step makes
the places and props, from this list or from the one the user edited.

Needs the style approved and at least one character.
"""

from __future__ import annotations

import copy
import time

from .. import context, prompts, schemas
from .. import store as store_mod
from . import entities, llm_call
from .entities import CHARACTERS
from .llm_call import StepFailed


def _owners(reply, cast) -> tuple:
    """``(props with owner ids, [names matching no character])``."""
    known = entities.by_name(cast)
    props, dropped = [], []
    for prop in reply["props"]:
        owner = prop.get("owner")
        owner_id = None
        if owner is not None:
            doc = known.get(entities.name_key(owner))
            if doc is None:
                dropped.append((prop["name"], owner))
            else:
                owner_id = doc["char_id"]
        props.append({"name": prop["name"], "one_line": prop["one_line"], "owner": owner_id})
    return props, dropped


def _document(reply, cast, *, now) -> dict:
    props, _ = _owners(reply, cast)
    return {
        "$schema": schemas.PLACES_PROPOSAL_SCHEMA_NAME,
        "places": [{"name": place["name"], "one_line": place["one_line"]} for place in reply["places"]],
        "props": props,
        "updated_at": now,
    }


def run(ctx, *, runner=None, time_fn=time.monotonic) -> dict:
    store, story = llm_call.open_story(ctx)
    entities.require_status(story, "style_approved", "Approve the style first.")
    cast = entities.cast_order(store.list_entities(ctx.story_id, CHARACTERS))
    if not cast:
        raise StepFailed("Write the cast first: places and props are proposed from it.")
    ctx.cancel.check()

    pack = context.build_pack(language=story["language"], story=story,
                              setup=llm_call.setup_block(store, ctx.story_id, story))
    llm_call.announce_trimmed(ctx, pack, set())
    lines = [{"name": doc["name"], "signature_items": list(doc["signature_items"])} for doc in cast]
    system, user, schema = prompts.build_p0(pack, cast=lines)

    def validate(reply):
        errors = schemas.p0_errors(reply)
        if errors:
            return errors
        return schemas.places_proposal_errors(_document(copy.deepcopy(reply), cast, now=llm_call.utc_now()))

    reply = llm_call.call_json(ctx, "P0", system, user, schema, validator=validate, runner=runner,
                               time_fn=time_fn)

    now = llm_call.utc_now()
    _props, dropped = _owners(reply, cast)
    for prop_name, owner in dropped:
        ctx.on_log(f"ℹ️ {prop_name}: owner {owner!r} is no character of the story; proposed without an owner.")
    doc = store.write_doc(ctx.story_id, store_mod.PLACES_PROPOSAL_DOC, _document(reply, cast, now=now), now=now)
    places = [place["name"] for place in doc["places"]]
    props = [prop["name"] for prop in doc["props"]]
    ctx.on_log(f"🗺 Proposed {len(places)} place(s): {' · '.join(places)}"
               + (f"; {len(props)} prop(s): {' · '.join(props)}" if props else "; no prop"))
    return {"places": doc["places"], "props": doc["props"]}
