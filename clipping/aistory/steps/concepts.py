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

**Plan 22 stage 2 (DEC-274), with a non-empty ``seed_text`` and
``generation_profile.writing == "v3"``**: every card is written to tell that
brief, not invented freely -- C1v2 instead of C1 (:func:`prompts.build_c1_v2`),
one of ``prompts.C1_ANGLES`` per call, the story's own cards as the avoid
list (never the library's: they compete with the brief, not with this
story's other cards). The rule check runs inside the call's own validator
(:func:`prompts.c1v2_errors`), so a dropped name is a told-why retry
(DEC-259) before anything is stored. Once a reply is accepted, the brief
judge (C1J, :func:`prompts.build_c1j`) checks the whole card against the
brief; not kept -> the same C1v2 call is asked once more with the judge's
``missing`` as the refusal reasons; still not kept -> the card is stored
anyway with ``brief_fit.kept`` false, never silently. Without a usable link
on the premium chain the judge is skipped (and ``brief_fit`` left off every
card of the batch) -- said once, not once per card. Without the gate, every
card is written exactly as it always was (RC-W2).

**Plan 23 stage D2, a story with a universe** (``media_policy.universe``): the
C1v2 prompt gains one data block, the universe's species pool and this card's
assigned lead species (:func:`universes.species_block`), the ten cards of a
batch rotating through the pool deterministically
(:func:`universes.assign_species`, seeded by the story and the batch); each
cast member names its species, a brand name is a told-why retry
(``schemas.BRAND_DENYLIST``), and the card records ``universe`` (its id and the
assigned lead species). A story without a universe, or one on C1, is untouched.
"""

from __future__ import annotations

import re
import time

from .. import context, media_policy, prompts, schemas, templates, universes
from . import llm_call
from .llm_call import StepFailed

CALLS = prompts.C1_CALLS
CONCEPTS_PER_CALL = prompts.C1_CONCEPTS_PER_CALL
CONCEPTS_FILENAME = "concepts.json"
# Plan 21 stage 1: the step's one parameter, the number of concepts to write.
COUNT_PARAM = "count"
PARAMS = (COUNT_PARAM,)
# Plan 22 stage 2: the brief judge's provenance (``brief_fit.checked_by``) --
# the prompt id, not a link name: ``call_json`` never hands its caller which
# link of the chain answered.
C1J_CHECKED_BY = "C1J"

_GENERATED_ID = re.compile(schemas.GENERATED_CONCEPT_ID_PATTERN)

# The C1 fields a card keeps, in the order a human reads concepts.json in.
_CARD_FIELDS = (
    "title", "logline", "world", "cast_sketch", "hook_formula", "value",
    "retention_mechanics", "style_fit",
)


# Plan 28 stage E2: what a written bible adds to the set-up block's SERIES and AUDIENCE, left out of it for
# the concepts (a re-run after a card was chosen writes from the brief, not from that card).
_BIBLE_SERIES_FIELDS = ("title", "logline", "tone", "genre_tags", "audience")


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


def _writing_gate(story) -> bool:
    """Whether this story writes its concepts from the brief (plan 22 stage
    2, DEC-274): a non-empty ``seed_text`` and ``generation_profile.writing
    == "v3"``. Without either, every card is written exactly as it always
    was (RC-W2)."""
    if not story.get("seed_text"):
        return False
    return media_policy.writing_v3(story)


def _judge_usable(ctx) -> bool:
    """Whether the premium chain (C1J's, DEC-273) has a link the judge could
    actually reach -- the same precondition ``llm_call.call_json`` itself
    checks before a call, read here first so the judge is skipped quietly
    (once, not per card) rather than failing a card's whole generation."""
    try:
        chain, skipped = llm_call.story_chain(ctx.settings_env, premium=True)
    except Exception:
        return False
    if not chain:
        return False
    keys = llm_call.resolve_keys(ctx.settings_env)
    paid = [link for link, _reason in skipped]
    return not (paid and not any(keys.get(link.provider) for link in chain))


def _judge_card(ctx, *, language, brief, card, runner, time_fn, setup=None):
    """One C1J call on *card*; ``StepFailed`` propagates (the caller decides
    what a failed judge call means). *setup*: the set-up block C1v2 was
    given (plan 28 stage E2), shared with the judge."""
    system, user, schema = prompts.build_c1j(language=language, brief=brief, card=card, setup=setup)
    return llm_call.call_json(
        ctx, "C1J", system, user, schema,
        validator=prompts.validate_c1j, runner=runner, time_fn=time_fn,
    )


def _apply_brief_judge(ctx, *, language, brief, concept, system, user, prompt_id, schema, validator,
                       runner, time_fn, judge_state, setup=None) -> tuple:
    """``(concept, brief_fit)`` for one accepted v3 card (plan 22 stage 2):
    judged by C1J; not kept, the same C1v2 call asked once more with the
    judge's ``missing`` as the refusal reasons (DEC-259); still not kept,
    *concept* (the retried reply) is returned with ``brief_fit.kept`` false.
    ``brief_fit`` is None only when the judge is never reached at all: the
    chain is skipped (no usable premium link, said once via *judge_state*)
    or the *first* judge call fails outright (said every time it happens --
    never silent, never fatal to the card). Once a first verdict says the
    card drifted, that finding is never lost: if the retry's re-judge call
    then fails, the retried card still carries the first verdict's
    ``kept: false`` and ``missing`` -- a known drift must never read as
    "never judged" and fall through to an approve-by-rule."""
    if not _judge_usable(ctx):
        if not judge_state["skip_logged"]:
            ctx.on_log("⚖️ C1J (brief judge) skipped: the premium chain has no usable link.")
            judge_state["skip_logged"] = True
        return concept, None

    try:
        verdict = _judge_card(ctx, language=language, brief=brief, card=concept, runner=runner, time_fn=time_fn,
                              setup=setup)
    except StepFailed as exc:
        ctx.on_log(f"⚠️ C1J could not judge this card: {exc.reason}")
        return concept, None

    if verdict["kept"]:
        return concept, {"kept": True, "missing": [], "checked_by": C1J_CHECKED_BY, "checked_at": llm_call.utc_now()}

    retry_user = llm_call.refused_prompt(user, verdict["missing"])
    try:
        reply = llm_call.call_json(
            ctx, prompt_id, system, retry_user, schema,
            validator=validator, runner=runner, time_fn=time_fn,
        )
    except StepFailed as exc:
        ctx.on_log(f"⚠️ C1v2 retry after the brief judge failed: {exc.reason}; keeping the first reply.")
        return concept, {"kept": False, "missing": verdict["missing"], "checked_by": C1J_CHECKED_BY,
                         "checked_at": llm_call.utc_now()}

    retried = reply["concepts"][0]
    try:
        verdict2 = _judge_card(ctx, language=language, brief=brief, card=retried, runner=runner, time_fn=time_fn,
                               setup=setup)
    except StepFailed as exc:
        ctx.on_log(f"⚠️ C1J could not re-judge the retried card: {exc.reason}")
        # A known drift must never vanish into an approve-by-rule: keep the first
        # verdict's "kept: false" on the retried card rather than dropping to None
        # (None means "never judged", not "judged, found drifted, re-judge failed").
        return retried, {"kept": False, "missing": verdict["missing"], "checked_by": C1J_CHECKED_BY,
                         "checked_at": llm_call.utc_now()}

    return retried, {"kept": verdict2["kept"], "missing": verdict2["missing"], "checked_by": C1J_CHECKED_BY,
                     "checked_at": llm_call.utc_now()}


def run(ctx, *, note=None, runner=None, time_fn=time.monotonic) -> dict:
    """Generate up to ``CALLS * CONCEPTS_PER_CALL`` cards for the story.

    *note* is a regenerate's author's note (``regenerate`` target
    ``concepts``); *runner*/*time_fn* are handed to ``llm_call.call_json``.
    ``ctx.params`` may hold ``count`` (:func:`read_count`); a regenerate's
    params are its own (its target and note), so it writes :data:`CALLS`.

    Plan 22 stage 2: with :func:`_writing_gate`, every card is written by
    C1v2 from the story's brief instead of C1 (module docstring); without
    it, this is exactly today's C1 run.
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
    use_brief = _writing_gate(story)
    # Plan 23 stage D2: what the cast is made of -- only on the brief-faithful prompt.
    universe = universes.universe_of(story) if use_brief else None
    # Plan 28 stage E2 (DEC-305 §8): the brief-faithful prompt and its judge read the set-up block --
    # without the bible a chosen card already wrote (its title, logline, tone, genre and audience would
    # steer the new cards away from the brief); the story's chosen look is the only style_fit a card may name.
    unwritten = {key: value for key, value in story.items() if key not in _BIBLE_SERIES_FIELDS}
    setup = context.setup_for(unwritten, lock=template) if use_brief else None
    card_style_ids = [template_id] if setup and template_id else style_ids

    cards = _existing_cards(store, ctx.story_id)
    number = _next_number(cards)
    generated_titles = [card["title"] for card in cards]

    new_ids = []
    failed = []  # (call, reason)
    announced = set()
    judge_state = {"skip_logged": False}

    for call in range(1, calls + 1):
        ctx.cancel.check()
        lead_species = None
        species_text = None
        if universe is not None:
            batch_number, position = universes.card_slot(len(cards), 1)
            batch_species = universes.assign_species(ctx.story_id, batch_number, universe["species"])
            lead_species = batch_species[position]
            species_text = universes.species_block(universe, batch_species=batch_species, position=position)
        if use_brief:
            pack = context.build_pack(
                language=language,
                template=template,
                brief_text=seed,
                # DEC-274: the library's titles compete with the brief, not
                # with this story's own cards -- left out here.
                avoid_titles=generated_titles[::-1],
                universe=species_text,
                setup=setup,
            )
        else:
            pack = context.build_pack(
                language=language,
                template=template,
                seed_text=seed,
                avoid_titles=generated_titles[::-1],
            )
        llm_call.announce_trimmed(ctx, pack, announced)

        if use_brief:
            angle = prompts.C1_ANGLES[(call - 1) % len(prompts.C1_ANGLES)]
            system, user, schema = prompts.build_c1_v2(pack, style_ids=card_style_ids, batch=call, of=calls,
                                                       angle=angle)
            prompt_id = "C1v2"

            def validator(reply, _brief=pack.brief, _universe=universe is not None):
                return prompts.c1v2_errors(reply, style_ids=card_style_ids, brief=_brief, universe=_universe)
        else:
            system, user, schema = prompts.build_c1(pack, style_ids=style_ids, batch=call, of=calls)
            prompt_id = "C1"

            def validator(reply):
                return schemas.c1_errors(reply, style_ids)

        try:
            reply = llm_call.call_json(
                ctx, prompt_id, system, user, schema,
                validator=validator, runner=runner, time_fn=time_fn,
            )
        except StepFailed as exc:
            failed.append((call, exc.reason))
            ctx.on_log(f"✖ {prompt_id} call {call}/{calls} failed: {exc.reason}")
            continue

        now = llm_call.utc_now()
        call_cards = []
        for concept in reply["concepts"]:
            brief_fit = None
            if use_brief:
                concept, brief_fit = _apply_brief_judge(
                    ctx, language=language, brief=pack.brief, concept=concept, system=system, user=user,
                    prompt_id=prompt_id, schema=schema, validator=validator, runner=runner, time_fn=time_fn,
                    judge_state=judge_state, setup=setup,
                )
            card = {
                "concept_id": _concept_id(number),
                "source": "generated",
                "prompt_version": prompts.PROMPT_VERSION,
                "created_at": now,
                "language": language,
            }
            card.update({field: concept[field] for field in _CARD_FIELDS})
            if brief_fit is not None:
                card["brief_fit"] = brief_fit
            if universe is not None:
                card["universe"] = {"id": universe["id"], "lead_species": lead_species}
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
        ctx.on_log(f"💡 {prompt_id} call {call}/{calls}: {titles}")

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
