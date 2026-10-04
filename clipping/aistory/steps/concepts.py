"""Step ``concepts``: "Generate 10 more" (spec 3 step 2, 4.2 row C1).

Ten C1 calls (``prompts.C1_CALLS``), one concept each
(``prompts.C1_CONCEPTS_PER_CALL``), appended as cards to the story's
``concepts.json``. One card per call because two French cards did not fit the
C1 cap: every reply was cut off mid-JSON (2026-09-26). One request, one
artifact (DEC-027): a call that fails is printed and the next still runs; the
card of every accepted call is on disk as soon as that call is (a crash or a
cancel keeps it). Returning, even with some calls failed, means the user has
cards to choose from; no card at all is a failure naming every call's reason.

Every title the story already has -- the library's, in the story's language,
and every card so far -- is sent as "do not repeat", newest cards first, and
each accepted call adds its own before the next one. The context pack caps
that list (``context._AVOID_TITLE_LIMIT``) and says so; the library titles
come first so they are never the ones cut.

``params.count`` (plan 21 stage 1, agent mode: "the idea is the concept")
asks for fewer cards: 1 to ``CALLS``, one call each; absent, ``CALLS``
(:func:`read_count`). Nothing else changes.
"""

from __future__ import annotations

import re
import time

from .. import context, prompts, schemas, templates
from . import llm_call
from .llm_call import StepFailed

CALLS = prompts.C1_CALLS
CONCEPTS_PER_CALL = prompts.C1_CONCEPTS_PER_CALL
CONCEPTS_FILENAME = "concepts.json"
# Plan 21 stage 1: the step's one parameter, the number of concepts to write.
COUNT_PARAM = "count"
PARAMS = (COUNT_PARAM,)

_GENERATED_ID = re.compile(schemas.GENERATED_CONCEPT_ID_PATTERN)

# The C1 fields a card keeps, in the order a human reads concepts.json in.
_CARD_FIELDS = (
    "title", "logline", "world", "cast_sketch", "hook_formula", "value",
    "retention_mechanics", "style_fit",
)


def _next_number(cards) -> int:
    """One past the highest ``gen_NN`` already used (1 for a fresh file)."""
    highest = 0
    for card in cards:
        concept_id = card.get("concept_id")
        if isinstance(concept_id, str) and _GENERATED_ID.fullmatch(concept_id):
            highest = max(highest, int(concept_id[len("gen_"):]))
    return highest + 1


def _concept_id(number: int) -> str:
    # Two digits, then as many as it takes: gen_01 ... gen_99, gen_100.
    return f"gen_{number:02d}"


def _seed_with_note(seed_text, note):
    """The seed the pack gets. The author's note goes first: the pack trims
    the seed from the end, and the note is the newest instruction."""
    parts = []
    if note:
        parts.append(f"Author's note: {note}")
    if seed_text:
        parts.append(seed_text)
    return "\n".join(parts) or None


def _existing_cards(store, story_id) -> list:
    doc = store.read_doc(story_id, CONCEPTS_FILENAME)
    if doc is None:
        return []
    errors = schemas.story_concepts_errors(doc)
    if errors:
        # Refused before anything is spent: the cards could never be written.
        shown = "; ".join(errors[:3])
        raise StepFailed(
            f"{CONCEPTS_FILENAME} is not a valid {schemas.STORY_CONCEPTS_SCHEMA_NAME} "
            f"document ({shown}); fix or remove it, then generate again."
        )
    return list(doc["concepts"])


def read_count(params) -> int:
    """How many concepts the step writes (``params.count``, 1 to
    :data:`CALLS`; absent or null, :data:`CALLS`); ``StepFailed`` for another
    key or value, naming the range."""
    params = params or {}
    unknown = sorted(key for key in params if key not in PARAMS)
    if unknown:
        raise StepFailed(f"The concepts step takes only {', '.join(PARAMS)}; not {', '.join(map(repr, unknown))}.")
    value = params.get(COUNT_PARAM)
    if value is None:
        return CALLS
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= CALLS:
        raise StepFailed(f"The concepts step's count is the number of concepts to write, 1 to {CALLS}, "
                         f"not {value!r}.")
    return value


def _library_titles(language) -> list:
    return [concept["title"][language] for concept in templates.load_concepts()]


def run(ctx, *, note=None, runner=None, time_fn=time.monotonic) -> dict:
    """Generate up to ``CALLS * CONCEPTS_PER_CALL`` cards for the story.

    *note* is a regenerate's author's note (``regenerate`` target
    ``concepts``); *runner*/*time_fn* are handed to ``llm_call.call_json``.
    ``ctx.params`` may hold ``count`` (:func:`read_count`); a regenerate's
    params are its own (its target and note), so it writes :data:`CALLS`.
    """
    calls = CALLS if note is not None or ctx.step == "regenerate" else read_count(ctx.params)
    store, story = llm_call.open_story(ctx)
    language = story["language"]
    template_id = story.get("style_template_id")
    try:
        template = templates.load_style(template_id) if template_id else None
    except KeyError:
        raise StepFailed(
            f"The story's style template {template_id!r} is not shipped "
            f"(shipped: {', '.join(templates.list_style_ids())}); pick another style."
        ) from None
    seed = _seed_with_note(story.get("seed_text"), note)
    style_ids = templates.list_style_ids()

    cards = _existing_cards(store, ctx.story_id)
    number = _next_number(cards)
    library_titles = _library_titles(language)
    generated_titles = [card["title"] for card in cards]

    def validator(reply):
        return schemas.c1_errors(reply, style_ids)

    new_ids = []
    failed = []  # (call, reason)
    announced = set()

    for call in range(1, calls + 1):
        ctx.cancel.check()
        pack = context.build_pack(
            language=language,
            template=template,
            seed_text=seed,
            avoid_titles=library_titles + generated_titles[::-1],
        )
        llm_call.announce_trimmed(ctx, pack, announced)
        system, user, schema = prompts.build_c1(pack, style_ids=style_ids, batch=call, of=calls)

        try:
            reply = llm_call.call_json(
                ctx, "C1", system, user, schema,
                validator=validator, runner=runner, time_fn=time_fn,
            )
        except StepFailed as exc:
            failed.append((call, exc.reason))
            ctx.on_log(f"✖ C1 call {call}/{calls} failed: {exc.reason}")
            continue

        now = llm_call.utc_now()
        call_cards = []
        for concept in reply["concepts"]:
            card = {
                "concept_id": _concept_id(number),
                "source": "generated",
                "prompt_version": prompts.PROMPT_VERSION,
                "created_at": now,
                "language": language,
            }
            card.update({field: concept[field] for field in _CARD_FIELDS})
            call_cards.append(card)
            number += 1

        # Written per call, so what was paid for survives whatever comes next.
        store.write_doc(
            ctx.story_id,
            CONCEPTS_FILENAME,
            {
                "$schema": schemas.STORY_CONCEPTS_SCHEMA_NAME,
                "concepts": cards + call_cards,
                "updated_at": now,
            },
            now=now,
            validator=schemas.story_concepts_errors,
        )
        cards.extend(call_cards)
        generated_titles.extend(card["title"] for card in call_cards)
        new_ids.extend(card["concept_id"] for card in call_cards)
        titles = " · ".join(card["title"] for card in call_cards)
        ctx.on_log(f"💡 C1 call {call}/{calls}: {titles}")

    wanted = calls * CONCEPTS_PER_CALL
    if not new_ids:
        detail = "; ".join(f"call {call}/{calls}: {reason}" for call, reason in failed)
        raise StepFailed(f"No concept was generated: every C1 call failed ({detail}).")

    if failed:
        numbers = ", ".join(str(call) for call, _ in failed)
        ctx.on_log(f"Generated {len(new_ids)} of {wanted} concepts (calls failed: {numbers})")
    else:
        ctx.on_log(f"Generated {len(new_ids)} of {wanted} concepts.")

    return {
        "generated": len(new_ids),
        "concept_ids": new_ids,
        "failed_calls": [call for call, _ in failed],
    }
