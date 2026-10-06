"""The sheet judge on the cast and places steps (plan 28 F3, DEC-305 section 5;
the human: "strict rules to avoid consistency problems, and all details").

Every image the cast step, the places step or a regenerate makes for a v2
story's entity is judged right after it is made (:func:`review`, one J3 call,
``judge.check_sheet``): one head a figure, the head the species named (never
a human head, never a mask), the outfit and the signature items, the style's
forbidden colours, the two views of a two-view sheet, the plate's layout and
light, the prop's look. Its verdict is kept on the entity (``sheet_checks``).

A failed image is drawn again by the regenerate path -- a fresh seed and what
the judge saw as the note -- at most :data:`REDRAWS` times, and judged again;
a portrait drawn again draws the sheets made from it again too. Every redraw
is booked against the story's ceiling (:func:`ceiling`: its images x
:data:`REDRAWS` x one image of its kind on its link); past it, or after the last redraw,
the step says the plain sentence ("Gaston's portrait does not match: the head
is a human head, Gaston is a pineapple. Regenerate it, or upload your own.")
and the entity cannot be approved (``judge.sheet_refusal``) until it is
regenerated or replaced by the human's own image.

The human's own image (``source`` manual/upload) is judged and warned about,
never drawn again or refused. An image made before this rule has no verdict
and is never judged here (an approved cast keeps its approval). A vision
chain that cannot run leaves the image unjudged, said in the feed: the next
run of the step judges it, free.

Stdlib only (DEC-012).
"""

from __future__ import annotations

from clipping.providers import generation as gen

from .. import imaging, media_policy, prompts, refimages, schemas
from . import entities, judge, llm_call
from .entities import CHARACTERS, PLACES, PROPS

# The most times one image is drawn again after a failed check.
REDRAWS = schemas.SHEET_CHECK_REDRAWS_MAX
_NOTE_HEAD = "Fix what the last picture got wrong: "
_SLOT_KINDS = (CHARACTERS, PLACES, PROPS)


def spent(stories, story_id) -> float:
    """What the story's sheet redraws have cost so far (every entity's
    ``sheet_checks[*].redraw_usd``)."""
    total = 0.0
    for kind in _SLOT_KINDS:
        for doc in stories.list_entities(story_id, kind):
            for entry in (doc.get(judge.SHEET_CHECKS) or {}).values():
                total += float(entry.get("redraw_usd") or 0.0)
    return round(total, 4)


def _counts(stories, story) -> dict:
    """The story's images the judge watches, by kind: each character's sheets
    of the story's sheet mode, each place's time variants (at least its day
    plate) and each prop's picture."""
    story_id = story["story_id"]
    places = sum(max(1, sum(1 for ref in (doc.get("time_variants") or {}).values() if ref))
                 for doc in stories.list_entities(story_id, PLACES))
    return {CHARACTERS: len(refimages.character_images(story)) * len(stories.list_entities(story_id, CHARACTERS)),
            PLACES: places, PROPS: len(stories.list_entities(story_id, PROPS))}


def image_count(stories, story) -> int:
    """The story's images the judge watches (:func:`_counts`, all kinds)."""
    return sum(_counts(stories, story).values())


# What one redraw of each kind is priced as (plan 29 stage 3, DEC-308): the role the image is made on and
# the size it is asked at -- a plate on the plate role at the story's plate size, a prop on the prop role.
_ROLES = {CHARACTERS: "sheet", PLACES: "plate", PROPS: "prop"}


def _size(story, kind) -> tuple:
    if kind == PLACES:
        return refimages.plate_size(story)
    return refimages.PROP_SIZE if kind == PROPS else refimages.PORTRAIT_SIZE


def unit_usd(story, env, kind=CHARACTERS) -> float:
    """One image of *kind* on the story's link for it, as the estimate prices
    it: a character sheet on the sheet role, a plate on the plate role at the
    plate size, a prop on the prop role (0 on a free or local link)."""
    width, height = _size(story, kind)
    found = imaging.estimate(gen.IMAGE, env, route=story["generation_profile"]["route"],
                             request=gen.GenRequest(kind=gen.IMAGE, width=width, height=height), qty=1,
                             step="image", what="a reference image", when="the step runs", role=_ROLES[kind],
                             story=story)
    return float(found.get("est_usd") or 0.0)


def ceiling(stories, story, env, kind=CHARACTERS) -> tuple:
    """``(ceiling_usd, unit_usd)``: the story's redraw ceiling -- each kind's
    images (:func:`_counts`) x :data:`REDRAWS` x one image of that kind on its
    link -- and the unit of *kind* (the image about to be drawn again)."""
    total = 0.0
    for each, count in _counts(stories, story).items():
        if count:
            total += count * REDRAWS * unit_usd(story, env, each)
    return round(total, 4), unit_usd(story, env, kind)


_PLACE_NOTE = ("The last picture showed someone or something alive in the set. Draw the set completely empty: "
               "nobody in it, no character, no figure, no fruit person; only the room, its furniture and light.")
_PROP_NOTE = ("The last picture showed a character with the object. Draw the object alone on a plain surface: "
              "no hands, no character, no fruit person near it.")


def _note(issues, kind=CHARACTERS) -> str:
    """The note a redraw carries. A character's is what the judge saw
    (``Fix what the last picture got wrong: ...``); a place's and a prop's
    say what to draw, in positive words, never the fault quoted back (a
    model told "no fruit characters" draws them -- DEC-308). The issues stay
    in the log line, where the human reads them."""
    if kind == PLACES:
        return _PLACE_NOTE
    if kind == PROPS:
        return _PROP_NOTE
    text = _NOTE_HEAD + "; ".join(" ".join(str(item).split()).rstrip(".") for item in issues)
    if len(text) <= refimages.NOTE_MAX_CHARS:
        return text
    cut = text[:refimages.NOTE_MAX_CHARS - 1]
    return cut.rsplit(" ", 1)[0].rstrip(" ,;:")


def _redraw(ctx, store, kind, eid, slot, note, tools) -> None:
    """The regenerate path's image again (a fresh seed, *note*); a portrait
    drawn again draws the sheets made from it again too (from the new one)."""
    kwargs = tools.image_kwargs(ctx)
    seed = entities.fresh_seed()
    if kind == CHARACTERS:
        refimages.character_image(store, ctx.story_id, eid, slot, note=note, seed=seed, **kwargs)
        if slot != "portrait":
            return
        character = store.read_entity(ctx.story_id, CHARACTERS, eid)
        for sheet in refimages.character_sheets(store.get(ctx.story_id)):
            if character["refs"].get(sheet) is not None:
                ctx.cancel.check()
                refimages.character_image(store, ctx.story_id, eid, sheet, note=note, **kwargs)
    elif kind == PLACES:
        refimages.place_image(store, ctx.story_id, eid, slot, note=note, seed=seed, **kwargs)
    else:
        refimages.prop_image(store, ctx.story_id, eid, note=note, seed=seed, **kwargs)


def _store_verdict(store, story_id, kind, eid, slot, image_hash, found, carried) -> bool:
    """Write *found* as ``sheet_checks[slot]`` when the image is still the
    one judged (*image_hash*); True when written."""
    written = []

    def mutate(current):
        entry = (current.get(judge.SHEET_CHECKS) or {}).get(slot)
        if entry is not None and entry.get("image_hash") not in (None, image_hash):
            return  # the image changed while it was judged: its own entry stands
        record = {"version": prompts.J3_PROMPT_VERSION, "passed": found["passed"],
                  "issues": list(found["issues"])[:schemas.SHEET_CHECK_ISSUES_MAX],
                  "judged_at": llm_call.utc_now(), "image_hash": image_hash, "link": found["link"]}
        record.update(carried)
        current.setdefault(judge.SHEET_CHECKS, {})[slot] = record
        written.append(True)

    entities.write_entity(store, story_id, kind, eid, mutate, now=llm_call.utc_now())
    return bool(written)


def _carry(store, story_id, kind, eid, slot, redraws, redraw_usd) -> None:
    """After a redraw (whose fresh entry ``refimages`` wrote), the count and
    the cost so far stay on the new image's entry."""

    def mutate(current):
        entry = (current.get(judge.SHEET_CHECKS) or {}).get(slot)
        if entry is not None:
            entry["redraws"] = redraws
            entry["redraw_usd"] = round(redraw_usd, 4)

    entities.write_entity(store, story_id, kind, eid, mutate, now=llm_call.utc_now())


def _path(store, story_id, kind, eid, ref):
    try:
        return store.media_path(story_id, kind, eid, ref["name"])
    except KeyError:
        return None


def review(ctx, store, kind, eid, *, tools, slots=None, redraw=True) -> list:
    """Judge *eid*'s images not judged yet (*slots*, default every one it
    has), draw a failed one again up to :data:`REDRAWS` times within the
    story's ceiling (*redraw*), and return the plain sentences of the images
    still failing (the step says them; the approval refuses them). Never
    raises for the judge itself: a vision chain that cannot run leaves the
    image unjudged; a redraw that fails is said and stops that image."""
    story = store.get(ctx.story_id)
    if not media_policy.is_v2(story):
        return []
    try:
        lock = imaging.read_lock(store, ctx.story_id, error=llm_call.StepFailed)
        ledger = imaging.open_ledger(store, ctx.story_id, error=llm_call.StepFailed, doing="checking an image")
    except llm_call.StepFailed as exc:
        ctx.on_log(f"👁 Image check skipped: {exc}")
        return []
    doc = store.read_entity(ctx.story_id, kind, eid)
    wanted = [slot for slot, _ref in judge.sheet_slots(story, kind, doc) if slots is None or slot in slots]
    failing = []
    for slot in wanted:
        ctx.cancel.check()
        sentence = _review_slot(ctx, store, story, kind, eid, slot, tools=tools, lock=lock, ledger=ledger,
                                redraw=redraw)
        if sentence:
            failing.append(sentence)
    return failing


def _review_slot(ctx, store, story, kind, eid, slot, *, tools, lock, ledger, redraw):
    while True:
        doc = store.read_entity(ctx.story_id, kind, eid)
        ref = dict(judge.sheet_slots(story, kind, doc)).get(slot)
        if ref is None:
            return None
        state, entry = judge.sheet_state(store, story, kind, doc, slot, ref)
        subject = f"{doc['name']}'s {judge.sheet_word(story, kind, slot)}"
        if state == judge.SHEET_NONE or state == judge.SHEET_PASSED:
            return None
        if state == judge.SHEET_OWN:
            # The human's own image: judged once, warned about, never drawn again nor refused.
            if judge.own_verdict(store, story, kind, doc, slot, ref) is None:
                _judge(ctx, store, story, kind, doc, slot, ref, tools=tools, lock=lock, ledger=ledger, carried={})
                doc = store.read_entity(ctx.story_id, kind, eid)
            verdict = judge.own_verdict(store, story, kind, doc, slot, ref)
            if verdict is not None and not verdict["passed"]:
                ctx.on_log(f"⚠️ {subject}, your own image -- the check saw: {'; '.join(verdict['issues'])}. "
                           "It is yours: kept, never refused.")
            return None
        carried = {key: entry[key] for key in ("redraws", "redraw_usd") if entry and key in entry}
        if state == judge.SHEET_UNJUDGED:
            found = _judge(ctx, store, story, kind, doc, slot, ref, tools=tools, lock=lock, ledger=ledger,
                           carried=carried)
            if found is None or found["passed"]:
                return None
            issues = found["issues"]
        else:
            issues = entry.get("issues") or []
        sentence = judge.sheet_sentence(story, kind, doc, slot, issues)
        redraws = int(carried.get("redraws") or 0)
        if not redraw or redraws >= REDRAWS:
            ctx.on_log(f"✋ {sentence}")
            return sentence
        top, unit = ceiling(store, story, ctx.settings_env, kind)
        so_far = spent(store, ctx.story_id)
        if so_far + unit > top + 1e-9:
            ctx.on_log(f"✋ {sentence} (The redraw budget for this story's images, ${top:.2f}, is spent.)")
            return sentence
        ctx.on_log(f"🔁 {subject} does not match ({'; '.join(issues)}): drawing it again "
                   f"({redraws + 1} of {REDRAWS})")
        before = ledger.totals()["est_usd"]
        try:
            _redraw(ctx, store, kind, eid, slot, _note(issues, kind), tools)
        except refimages.RefImageError as exc:
            ctx.on_log(f"✖ {subject} could not be drawn again: {exc}")
            return sentence
        cost = max(0.0, ledger.totals()["est_usd"] - before)
        _carry(store, ctx.story_id, kind, eid, slot, redraws + 1, float(carried.get("redraw_usd") or 0.0) + cost)


def _judge(ctx, store, story, kind, doc, slot, ref, *, tools, lock, ledger, carried):
    """One J3 call on the image as it is now; its verdict stored. Returns
    the verdict, or None when it could not be judged."""
    path = _path(store, ctx.story_id, kind, judge.doc_id(kind, doc), ref)
    if path is None:
        return None
    image_hash = judge.file_sha(path)
    found = judge.check_sheet(story, doc, slot, kind=kind, path=path, env=ctx.settings_env, ledger=ledger,
                              on_log=ctx.on_log, cancel=ctx.cancel, adapters=tools.adapters,
                              transport=tools.transport, lock=lock)
    if found is None:
        return None
    subject = f"{doc['name']}'s {judge.sheet_word(story, kind, slot)}"
    ctx.on_log(f"👁 {subject}: " + ("passed" if found["passed"] else "; ".join(found["issues"])))
    _store_verdict(store, ctx.story_id, kind, judge.doc_id(kind, doc), slot, image_hash, found, carried)
    return found
