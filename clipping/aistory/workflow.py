"""The story rules of steps 1-12, the fast track and the series steps (step
13: memory, feedback, propose-next), shared by the API and the CLI (spec 3,
9.1-9.3).

``web/api/routes/stories.py`` (the HTTP face) and ``clipping/aistory/cli.py``
(``python main.py --ai-story``) both call these functions, so a story is
chosen, written, styled and approved by one set of rules whichever way it is
driven. Each function works on a ``StoryStore`` and raises typed errors; it
knows nothing about HTTP, argparse or the job store:

- :class:`WorkflowError` -- the caller's request cannot be done. ``code`` is
  one of :data:`CODES` (the API answers ``not_found`` 404, ``conflict`` 409,
  ``invalid`` 400, ``later_phase`` 400); ``detail`` is the sentence, or
  ``{"message", "errors"}`` where the API answers with a list.
- :class:`StoryUnreadable` -- a story document on disk does not validate (the
  API answers 500 with this one sentence and no traceback). Never repaired.

What stays with the caller is everything about *step jobs*: "a step of this
story is already queued or running" (409), the key gate and the queue cap
before a job exists, and completing or superseding the jobs an approval or a
new job settles. Those need the job store, which lives in ``web/``; the
caller checks them before or after calling in here, in the order the API has
always answered.

Stdlib only, and never an import from ``web/`` (DEC-012): the CLI must run
where fastapi is not installed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
import types

from clipping.providers import budget as budget_mod
from clipping.providers import gating, gen_timings
from clipping.providers import generation as gen
from clipping.providers import registry

from . import (
    defaults,
    imaging,
    prompting,
    prompts,
    refimages,
    schemas,
    series_memory,
    shots,
    stylelock,
    templates,
    timing,
    video_plan,
    voices,
    wordtiming,
)
from . import store as story_store
from . import uploads as uploads_mod
from .ledger import CostLedger
from .steps import assets as assets_step
from .steps import concepts as concepts_step
from .steps import entities as entities_step
from .steps import clips as clips_step
from .steps import episode_common, llm_call, sticky_link
from .steps import fast_track as fast_track_step
from .steps import feedback as feedback_step
from .steps import memory as memory_step
from .steps import metadata as metadata_step
from .steps import propose_next as propose_next_step
from .steps import regenerate as regenerate_step
from .steps import render as render_step
from .steps import rerender as rerender_step
from .steps import script as script_step
from .steps import season as season_step
from .steps import storyboard as storyboard_step
from .steps import voice_lines
from .steps.llm_call import StepFailed

# ------------------------------------------------------------------ grammar

# Spec 9.1. What phase 1 runs, what phase 2 runs, what phase 3 runs, what
# phase 4 runs, what phase 5 runs, what comes later.
LLM_STEPS = ("concepts", "bible")
INLINE_STEPS = ("style",)
PREVIEW_STEP = "style_preview"
PHASE1_STEPS = LLM_STEPS + INLINE_STEPS + (PREVIEW_STEP,)
# Steps 5-7 (phase 2): jobs, each calling the LLM chain (and, for the cast
# and the places, the image and voice chains). ``places_proposal`` is the
# small P0 step that proposes the list the ``places`` step makes.
PHASE2_STEPS = ("cast", "places_proposal", "places", "season")
# Steps 8-9 (phase 3): one episode's script (a job: E1, E2 per scene, E3, E4)
# and its storyboard (a T1 job, or the fast plan, run inline: DEC-109).
PHASE3_STEPS = ("script", "storyboard")
# Steps 10-12 and the fast track (phase 4), one episode each, all jobs: the
# assets (images, voices, sounds: awaiting approval), the render (minutes of
# ffmpeg, calling no API: DEC-161), the metadata pack, and the fast track from
# the script to the pack (DEC-162). The last three end completed.
PHASE4_STEPS = ("assets", "render", "metadata", "fast-track")
# Step 13 (phase 5, plan 11 stage 4): the series steps, one episode each
# (``ep``, "N"), one LLM call each (S3, F1, N1), all ending awaiting approval
# -- ``memory:<N>``, ``feedback:<N>`` and ``proposals:<N+1>`` (the proposals
# sit in the folder of the episode they are for: :func:`series_job_doc`).
SERIES_STEPS = ("memory", "feedback", "propose-next")
# The re-edit's render (phase 5, plan 11 stage 8): one episode, ending
# completed -- the render again, making only the shot clips that changed since
# the last good render (``steps/rerender.py``). Its route (``run_step``,
# ``estimate``) and its CLI (``step <id> rerender --ep N``) are stage 9's:
# ``require_reedit_inputs``, ``reedit_request`` and ``reedit_changes`` below.
REEDIT_STEPS = ("rerender",)
EPISODE_STEPS = PHASE3_STEPS + PHASE4_STEPS + REEDIT_STEPS
LATER_STEPS = ("import",)

# Spec 9.2, approve grammar: "season" bare, the others "<kind>:<id>". Phase 2
# approves ``character:<id>``, ``place:<id>``, ``prop:<id>`` and ``season``;
# phase 3 ``script:<ep>`` and ``storyboard:<ep>``; phase 4 ``assets:<ep>``;
# phase 5 ``memory:<ep>``, ``feedback:<ep>`` (with a direction) and
# ``proposals:<ep>`` (:data:`SERIES_APPROVALS`, :func:`approve_series`).
# No approval of the grammar is a later phase's any more.
LATER_APPROVALS_BARE = ()
LATER_APPROVALS = ()
# The episode documents an approval names (``<word>:<ep>``), and the step
# jobs that write them (their job's document is ``<step>:<ep>``).
EPISODE_APPROVALS = ("script", "storyboard", "assets")
# The series documents an approval names (``<word>:<ep>``): the memory entry
# and the feedback item live in ``season.json``, the proposals in
# ``episodes/ep<NN>/proposals.json``.
SERIES_APPROVALS = ("memory", "feedback", "proposals")

# Spec 9.2, regenerate grammar: every "<kind>:..." target of a later phase.
# Phase 2's, phase 3's, phase 4's and phase 6's targets are
# ``regenerate.parse_target``'s; its ``character:<id>:image:extra:<n>`` is
# still a later phase's, and so is every other ``shot:`` form (``:frames``
# among them): ``shot:<ep>:<shid>:plan``, the shot's image
# ``shot:<ep>:<shid>`` and -- phase 6 stage 8 -- its clip
# ``shot:<ep>:<shid>:video`` are read before this list is (DEC-140).
LATER_TARGETS = ("shot",)

# What approving the bible requires (spec 2.1, 3 step 3).
BIBLE_FIELDS = (
    "logline", "premise", "tone", "genre_tags", "world", "themes_and_values",
    "audience", "why_come_back",
)
WHY_COME_BACK_LINES = 3

# The story fields an edit may set (the API's StoryPatchRequest). Everything
# else is the rules' to write: approvals, status, the concept, the style.
# ``episode_template_id`` (phase 3) only while no episode has a script.
PATCH_FIELDS = ("title", "seed_text") + BIBLE_FIELDS + ("narrator", "generation_profile", "episode_template_id")

# The keys ``params`` of the inline style step may carry.
STYLE_PARAMS = ("template_id", "overrides", "consistency_mode")

# A generated card's fields the server sets; a custom concept need not send
# them, and any it sends are replaced.
_CARD_BOOKKEEPING = ("concept_id", "source", "prompt_version", "created_at", "language")

# The story_concepts card rules (schemas.STORY_CONCEPT_CARD_SCHEMA) for what a
# user writes: the same fields, the same shapes, none of the bookkeeping.
_CUSTOM_CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        key: value for key, value in schemas.STORY_CONCEPT_CARD_SCHEMA["properties"].items()
        if key not in _CARD_BOOKKEEPING
    },
    "required": [
        key for key in schemas.STORY_CONCEPT_CARD_SCHEMA["required"]
        if key not in _CARD_BOOKKEEPING
    ],
    "additionalProperties": False,
}

TITLE_MAX = 120

STYLE_LOCK_DOC = "style_lock.json"
CONCEPTS_DOC = concepts_step.CONCEPTS_FILENAME
COST_LEDGER = "cost_ledger.json"

# ------------------------------------------------------------------- errors

NOT_FOUND = "not_found"
CONFLICT = "conflict"
INVALID = "invalid"
LATER_PHASE = "later_phase"
CODES = (NOT_FOUND, CONFLICT, INVALID, LATER_PHASE)


class WorkflowError(Exception):
    """A request the story rules refuse. ``code`` says which kind (``CODES``),
    ``detail`` says why: a sentence, or ``{"message", "errors"}``."""

    def __init__(self, code, detail):
        if code not in CODES:
            raise ValueError(f"unknown workflow error code {code!r} (known: {', '.join(CODES)})")
        self.code = code
        self.detail = detail
        super().__init__(code, detail)

    def __str__(self) -> str:
        if isinstance(self.detail, dict):
            lines = [str(self.detail.get("message", ""))]
            lines += [f"  - {error}" for error in self.detail.get("errors") or []]
            return "\n".join(lines)
        return str(self.detail)


class StoryUnreadable(Exception):
    """A story document on disk that does not validate: one sentence, the
    first error, no traceback. Never repaired here."""

    def __init__(self, story_id, name, errors):
        self.story_id = story_id
        self.name = name
        self.errors = list(errors or [])
        first = str(self.errors[0]) if self.errors else "it does not validate"
        if len(first) > 200:
            first = first[:197] + "..."
        self.detail = f"Story {story_id} cannot be read: {name} is invalid ({first})."
        super().__init__(self.detail)


def not_found() -> WorkflowError:
    return WorkflowError(NOT_FOUND, "Story not found")


# ------------------------------------------------------------- the store

def check_id(story_id) -> None:
    """A malformed id is an unknown story; it never reaches a path."""
    if not story_store.is_story_id(story_id):
        raise not_found()


def load(stories, story_id) -> dict:
    """The story; ``not_found`` (malformed or unknown id); ``StoryUnreadable``
    (corrupt)."""
    check_id(story_id)
    try:
        return stories.get(story_id)
    except KeyError:
        raise not_found() from None
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None


def update(stories, story_id, mutate, *, now) -> dict:
    """``StoryStore.update``: ``not_found`` for a story gone meanwhile,
    ``invalid`` when the changed document is refused by the schema (a bad
    value in the request), ``StoryUnreadable`` when the one on disk is."""
    try:
        return stories.update(story_id, mutate, now=now)
    except KeyError:
        raise not_found() from None
    except schemas.SchemaError as exc:
        if exc.name == story_store.STORY_SCHEMA:
            raise WorkflowError(
                INVALID,
                {"message": "The story would not be valid with these values.",
                 "errors": list(exc.errors)},
            ) from None
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None


def read_doc(stories, story_id, name, validator):
    """One of the story's documents, validated, or None if it does not exist."""
    label = f"{story_store.STORIES_DIRNAME}/{story_id}/{name}"
    try:
        doc = stories.read_doc(story_id, name)
    except KeyError:
        raise not_found() from None
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None
    if doc is None:
        return None
    errors = validator(doc)
    if errors:
        raise StoryUnreadable(story_id, label, errors)
    return doc


def write_doc(stories, story_id, name, doc, *, now, validator) -> dict:
    """``StoryStore.write_doc``; ``not_found`` for a story gone meanwhile."""
    try:
        return stories.write_doc(story_id, name, doc, now=now, validator=validator)
    except KeyError:
        raise not_found() from None


def style_lock(stories, story_id):
    """The story's ``style_lock.json``, or None before the style step."""
    return read_doc(stories, story_id, STYLE_LOCK_DOC, schemas.style_lock_errors)


def generated_cards(stories, story_id) -> list:
    """The cards of the story's ``concepts.json`` (none before a concepts step)."""
    doc = read_doc(stories, story_id, CONCEPTS_DOC, schemas.story_concepts_errors)
    return list(doc["concepts"]) if doc is not None else []


def cost_total(stories, story_id) -> float:
    """The story ledger's total (spec 2.11), 0.0 while it has none. A symlink
    in its place is not followed."""
    try:
        path = os.path.join(stories.story_dir(story_id), COST_LEDGER)
    except KeyError:
        raise not_found() from None
    if os.path.islink(path) or not os.path.isfile(path):
        return 0.0
    try:
        return float(CostLedger(path).totals()["est_usd"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StoryUnreadable(story_id, f"{story_store.STORIES_DIRNAME}/{story_id}/{COST_LEDGER}",
                              [f"{type(exc).__name__}: {exc}"]) from None


# ---------------------------------------------------------------- grammar

def is_later_approval(doc) -> bool:
    if doc in LATER_APPROVALS_BARE:
        return True
    kind, sep, rest = doc.partition(":")
    return bool(sep) and bool(rest) and kind in LATER_APPROVALS


def is_later_target(target) -> bool:
    kind, sep, rest = target.partition(":")
    return bool(sep) and bool(rest) and kind in LATER_TARGETS


def refuse_step(step):
    """The refusal of a step this phase does not run: ``later_phase`` for a
    step of the 9.1 grammar, ``not_found`` for anything else. Always raises."""
    if step in LATER_STEPS:
        raise WorkflowError(LATER_PHASE, f"'{step}' arrives in a later phase.")
    raise WorkflowError(NOT_FOUND, f"Unknown step {step!r}.")


def refuse_approval(doc):
    """The refusal of a document this phase does not approve: ``later_phase``
    for one of the 9.2 grammar, ``not_found`` for anything else. Always
    raises."""
    if is_later_approval(doc):
        raise WorkflowError(LATER_PHASE, f"Approving '{doc}' arrives in a later phase.")
    raise WorkflowError(NOT_FOUND, f"Nothing to approve under {doc!r}.")


def invalid_target(target) -> WorkflowError:
    """A malformed target, naming every shape a target may have (phase 1's
    fixed ones, phase 2's entity shapes and phase 3's episode shapes)."""
    return WorkflowError(
        INVALID,
        (f"Cannot regenerate {target!r}: the valid targets are "
         f"{', '.join(regenerate_step.TARGET_SHAPES)}."),
    )


def check_regenerate_target(target) -> None:
    """A target this phase regenerates passes -- phase 1's fixed ones, phase
    2's entity targets and the episode targets of phases 3 and 4
    (``regenerate.parse_target``: the shape only; the entity or the episode
    itself is checked by :func:`check_entity_target`) -- phase 6's
    ``shot:<ep>:<shid>:video`` among them. A later phase's target of the 9.2
    grammar -- ``character:<id>:image:extra:<n>`` and any other
    ``shot:<ep>:<shid>:<word>`` -- is ``later_phase``; anything else is
    ``invalid``, naming the valid shapes."""
    if target in regenerate_step.VALID_TARGETS:
        return
    if regenerate_step.parse_target(target) is not None:
        return
    if regenerate_step.is_extra_target(target) or is_later_target(target):
        raise WorkflowError(LATER_PHASE, f"Regenerating '{target}' arrives in a later phase.")
    raise invalid_target(target)


# ------------------------------------------------------------ preconditions

def require_concept(story) -> None:
    """The bible, and every regeneration of it, is written from the concept."""
    if not story.get("concept"):
        raise WorkflowError(CONFLICT, "Choose a concept first.")


def require_bible_approved(story) -> None:
    if not story["approvals"].get("bible"):
        raise WorkflowError(CONFLICT, "Approve the bible first.")


def require_style_draft(stories, story_id) -> dict:
    """The draft ``style_lock.json`` the preview strip is made from."""
    lock = style_lock(stories, story_id)
    if lock is None:
        raise WorkflowError(CONFLICT, "Build the style first.")
    return lock


# ------------------------------------------------------------- the key gate

def no_key_message(links, *, where="in Settings") -> str:
    """No link of the chain has a key: which ones to set, primaries first
    (the floor alone would be refused next), billed ones marked as such
    (DEC-088). *where* says where keys are set: the dashboard's Settings, or
    the environment for the CLI."""
    return (
        "No link in the LLM chain has an API key, so this step cannot call a "
        f"model. Set one of: {llm_call.key_choices(links)}, {where}."
    )


def llm_route(settings_env, *, readiness, where="in Settings"):
    """``(links, keys, skipped, refusal)`` for an LLM step under
    *settings_env*: the chain as configured and the keys (``llm_call``'s
    resolution: the values given, then the process env), the links the step
    will leave out (``llm_call.story_chain``: a paid link while ``allow_paid``
    is off, as ``(link, reason)``), and why it may not start, or None.

    Refused, in this order: a chain that cannot be parsed (the step would
    fail on its first call); a chain in which no link has a key
    (``no_key_message``); budget settings that cannot be read; a chain whose
    only keyed links are paid while ``allow_paid`` is off
    (``llm_call.paid_off_message``); whatever ``readiness(links, keys)``
    refuses -- the DEC-073 slow-floor rule, which each caller resolves its
    own way (the API from Settings, the CLI from its flag and the
    environment), asked of the chain as configured first, so a story step and
    a clip job give the same refusal for the same values -- and then, when a
    paid link is left out, the same rule asked of the links the step will
    really use: with its paid primary skipped, a chain can be down to its
    slow floor.
    """
    try:
        links = llm_call.resolve_chain(settings_env)
    except registry.ChainError as exc:
        return [], {}, [], f"LLM_CHAIN cannot be used: {exc}"
    keys = llm_call.resolve_keys(settings_env)
    try:
        usable, skipped = llm_call.story_chain(settings_env)
    except ValueError as exc:
        budget_refusal = f"The budget settings cannot be used: {exc}"
        usable, skipped = list(links), []
    else:
        budget_refusal = None

    if not any(keys.get(link.provider) for link in links):
        return links, keys, skipped, no_key_message(links, where=where)
    if budget_refusal:
        return links, keys, skipped, budget_refusal
    if not any(keys.get(link.provider) for link in usable):
        keyed_paid = [link for link, _reason in skipped if keys.get(link.provider)]
        return links, keys, skipped, llm_call.paid_off_message(keyed_paid, usable, where=where)

    refusal = readiness(links, keys)
    if refusal is None and skipped:
        refusal = readiness(usable, keys)
        if refusal:
            labels = ", ".join(dict.fromkeys(registry.describe(link) for link, _reason in skipped))
            refusal = f"{refusal} (Not used by AI Story: {labels}, {llm_call.PAID_SKIP_REASON}.)"
    return links, keys, skipped, refusal


def llm_gate(settings_env, *, readiness, where="in Settings"):
    """``(links, keys, refusal)`` for an LLM step under *settings_env*: the
    chain and keys the step will run with (``llm_call``'s resolution: the
    values given, then the process env), and why it may not start, or None --
    :func:`llm_route` without the skipped links.
    """
    links, keys, _skipped, refusal = llm_route(settings_env, readiness=readiness, where=where)
    return links, keys, refusal


# ---------------------------------------------------------------- concept

def check_concept_choice(concept_id, concept) -> None:
    """Exactly one of a concept id and a concept payload."""
    if (concept_id is not None) == (concept is not None):
        raise WorkflowError(
            INVALID,
            "Send exactly one of 'concept_id' (a library or generated concept) and 'concept' (your own).",
        )


def choose_concept(stories, story_id, *, concept_id=None, concept=None, now) -> dict:
    """Choose the story's concept; returns the story.

    Exactly one of *concept_id* (a library id, or a generated card's
    ``gen_NN``) and *concept* (a card written by the user, checked with the
    generated-card rules) -- ``invalid`` otherwise; ``not_found`` for an id
    that is neither.

    Writes a snapshot of the concept in the story's language, ``concept_id``
    (the library id, or ``"custom"``: the snapshot of a generated card keeps
    its ``gen_NN``), the concept's title, and ``approvals.concept``; a
    different concept than before clears the bible approval.
    """
    story = load(stories, story_id)
    check_concept_choice(concept_id, concept)

    if concept_id is not None:
        if re.fullmatch(schemas.GENERATED_CONCEPT_ID_PATTERN, concept_id):
            card = next((c for c in generated_cards(stories, story_id)
                         if c["concept_id"] == concept_id), None)
            if card is None:
                raise WorkflowError(NOT_FOUND, f"This story has no generated concept {concept_id!r}.")
            snapshot = copy.deepcopy(card)
            story_concept_id = "custom"
        else:
            library = {c["concept_id"]: c for c in templates.load_concepts()}
            if concept_id not in library:
                raise WorkflowError(NOT_FOUND, f"Unknown concept {concept_id!r}.")
            snapshot = templates.localize_concept(library[concept_id], story["language"])
            snapshot["concept_id"] = concept_id
            story_concept_id = concept_id
    else:
        content = {k: v for k, v in concept.items() if k not in _CARD_BOOKKEEPING}
        errors = schemas.validate(content, _CUSTOM_CONCEPT_SCHEMA)
        if errors:
            raise WorkflowError(
                INVALID,
                {"message": "This concept is not a valid concept card.", "errors": errors},
            )
        snapshot = {"concept_id": "custom", "source": "custom", "language": story["language"],
                    **copy.deepcopy(content)}
        story_concept_id = "custom"

    title = str(snapshot.get("title") or "")[:TITLE_MAX]

    def mutate(doc):
        if doc.get("concept") != snapshot or doc.get("concept_id") != story_concept_id:
            doc["approvals"]["bible"] = None
        doc["concept_id"] = story_concept_id
        doc["concept"] = snapshot
        doc["title"] = title
        doc["approvals"]["concept"] = now

    return update(stories, story_id, mutate, now=now)


# ------------------------------------------------------------------ style

def concept_style(concept):
    """The style a chosen concept suggests: a library card's
    ``style_fit.default``, a generated card's ``style_fit``."""
    fit = (concept or {}).get("style_fit")
    if isinstance(fit, dict):
        fit = fit.get("default")
    return fit if isinstance(fit, str) and fit else None


def build_style(stories, story_id, params, *, now) -> dict:
    """Build or edit the story's draft ``style_lock.json`` (spec 3 step 4);
    returns ``{"story", "style_lock"}``.

    ``params = {template_id?, overrides?, consistency_mode?}``. The template
    defaults to the story's ``style_template_id``, else the chosen concept's
    style. A draft on the same template and version takes the new overrides
    on top of its own (``stylelock.apply_overrides``); anything else is built
    fresh. ``conflict`` before the bible is approved or once the style is
    locked; ``invalid`` for an unknown template, a bad parameter, or refused
    overrides (with ``{"message", "errors"}``). The style approval is
    cleared.
    """
    story = load(stories, story_id)
    require_bible_approved(story)

    unknown = sorted(set(params) - set(STYLE_PARAMS))
    if unknown:
        raise WorkflowError(
            INVALID,
            f"Unknown style parameter(s) {', '.join(unknown)} (known: {', '.join(STYLE_PARAMS)}).",
        )

    shipped = templates.list_style_ids()
    template_id = (params.get("template_id") or story.get("style_template_id")
                   or concept_style(story.get("concept")))
    if not template_id:
        raise WorkflowError(
            INVALID,
            f"Name a style in params.template_id (shipped: {', '.join(shipped)}).",
        )
    try:
        template = templates.load_style(template_id)
    except KeyError:
        raise WorkflowError(
            INVALID,
            f"Unknown style template {template_id!r} (shipped: {', '.join(shipped)}).",
        ) from None

    overrides = params.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise WorkflowError(INVALID, "params.overrides must be an object of dotted paths.")

    consistency = params.get("consistency_mode")
    if consistency is not None and consistency not in defaults.CONSISTENCY_MODES:
        raise WorkflowError(
            INVALID,
            (f"consistency_mode must be one of "
             f"{', '.join(defaults.CONSISTENCY_MODES)}, not {consistency!r}."),
        )

    current = style_lock(stories, story_id)
    if current is not None and current.get("locked_at"):
        raise WorkflowError(
            CONFLICT,
            f"The style is locked (since {current['locked_at']}); it cannot change.",
        )

    try:
        if (current is not None and current.get("template_id") == template_id
                and current.get("template_version") == template["version"]):
            lock = stylelock.apply_overrides(current, overrides, now=now)
        else:
            lock = stylelock.build_style_lock(template, overrides, now=now)
    except stylelock.StyleLockError as exc:
        raise WorkflowError(
            INVALID,
            {"message": f"The style was refused: {exc.name}.", "errors": list(exc.errors)},
        ) from None

    written = write_doc(stories, story_id, STYLE_LOCK_DOC, lock, now=now,
                        validator=schemas.style_lock_errors)

    def mutate(doc):
        doc["style_template_id"] = template_id
        if consistency is not None:
            doc["generation_profile"]["consistency_mode"] = consistency
        doc["approvals"]["style"] = None

    return {"story": update(stories, story_id, mutate, now=now), "style_lock": written}


# ---------------------------------------------------------------- approve

def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return all(_is_empty(v) for v in value.values())
    if isinstance(value, list):
        return all(_is_empty(v) for v in value)
    return False


def missing_bible_fields(story) -> list:
    """Every bible field still missing or empty; ``why_come_back`` needs its
    three lines."""
    missing = []
    for field in BIBLE_FIELDS:
        value = story.get(field)
        if field == "why_come_back":
            lines = [v for v in value or [] if isinstance(v, str) and v.strip()]
            if len(lines) != WHY_COME_BACK_LINES:
                missing.append(f"why_come_back (needs {WHY_COME_BACK_LINES} lines, has {len(lines)})")
        elif _is_empty(value):
            missing.append(field)
    return missing


def approve_bible(stories, story_id, *, now) -> dict:
    """Set ``approvals.bible``; returns the story. ``conflict`` without a
    chosen concept, or listing every bible field still missing or empty."""
    story = load(stories, story_id)
    require_concept(story)
    missing = missing_bible_fields(story)
    if missing:
        raise WorkflowError(
            CONFLICT,
            f"The bible is not complete; missing or empty: {', '.join(missing)}.",
        )

    def mutate(doc):
        doc["approvals"]["bible"] = now

    return update(stories, story_id, mutate, now=now)


def approve_style(stories, story_id, *, now) -> dict:
    """Freeze the draft style lock (``locked_at``) and set
    ``approvals.style``; returns the story. ``conflict`` without a
    ``style_lock.json``, or when it is already locked."""
    current = style_lock(stories, story_id)
    if current is None:
        raise WorkflowError(CONFLICT, "There is no style to approve yet: run the style step first.")
    try:
        locked = stylelock.lock_style(current, now=now)
    except stylelock.StyleLockError:
        raise WorkflowError(
            CONFLICT,
            f"The style is already locked (since {current.get('locked_at')}).",
        ) from None
    write_doc(stories, story_id, STYLE_LOCK_DOC, locked, now=now,
              validator=schemas.style_lock_errors)

    def mutate(doc):
        doc["approvals"]["style"] = now

    return update(stories, story_id, mutate, now=now)


# ------------------------------------------------------------------ patch

def patch_story(stories, story_id, fields, *, now) -> dict:
    """Edit the story fields in *fields*, and only those; returns the story.

    Nothing sent, nothing written. A field outside ``PATCH_FIELDS`` is
    ``invalid``. A bible field clears ``approvals.bible`` (a changed bible is
    an unapproved one); ``title``, ``seed_text``, ``narrator``,
    ``generation_profile`` and ``episode_template_id`` leave the approvals
    alone. ``narrator`` and ``generation_profile`` are merged onto the current
    values, the profile checked against ``clipping.aistory.defaults``
    (``invalid``). ``episode_template_id`` is one of
    ``defaults.EPISODE_TEMPLATE_IDS`` (``invalid``) and changes only while no
    episode has a script (``conflict``: :func:`check_episode_template`). A
    story the schema would refuse is ``invalid`` with ``{"message",
    "errors"}``.
    """
    story = load(stories, story_id)
    if not fields:
        return story

    unknown = sorted(set(fields) - set(PATCH_FIELDS))
    if unknown:
        raise WorkflowError(
            INVALID,
            f"These story fields cannot be edited: {', '.join(unknown)} (editable: {', '.join(PATCH_FIELDS)}).",
        )

    values = {name: copy.deepcopy(value) for name, value in fields.items()}

    if "generation_profile" in values:
        partial = values["generation_profile"]
        if partial is None:
            raise WorkflowError(INVALID, "generation_profile must be an object, not null.")
        try:
            # The store's own check, on the current profile with the sent keys over it.
            values["generation_profile"] = story_store._merge_generation_profile(
                {**story["generation_profile"], **partial})
        except ValueError as exc:
            raise WorkflowError(INVALID, str(exc)) from None

    if "episode_template_id" in values:
        check_episode_template(stories, story, values["episode_template_id"])

    clears_bible = bool(set(values) & set(BIBLE_FIELDS))

    def mutate(doc):
        for name, value in values.items():
            if name == "narrator" and isinstance(value, dict):
                doc["narrator"] = {**doc["narrator"], **value}
            else:
                doc[name] = value
        if clears_bible:
            doc["approvals"]["bible"] = None

    return update(stories, story_id, mutate, now=now)


# ================================================================== phase 2
#
# Steps 5-7 (spec 3, 2.3-2.6, 9.2; phase-2 plan 2): what the cast, places and
# season steps need before they may start, the parameters they take, what a
# character, a place, a prop and the season arc need before they are approved,
# the fields an inline edit may set, what is still missing (the story page's
# ``progress``), and how much a step would make (its estimate's units).

CHARACTERS, PLACES, PROPS = entities_step.CHARACTERS, entities_step.PLACES, entities_step.PROPS
SEASON_DOC = story_store.SEASON_DOC
PLACES_PROPOSAL_DOC = story_store.PLACES_PROPOSAL_DOC

# The approve and regenerate grammar's word for each entity kind, and back.
ENTITY_WORDS = dict(entities_step.TARGET_KINDS)
ENTITY_KINDS_BY_WORD = {word: kind for kind, word in ENTITY_WORDS.items()}
# The story approval each kind's per-entity approvals fold into.
GROUP_APPROVAL = {CHARACTERS: "cast", PLACES: "places", PROPS: "places"}

# A cast has at most this many characters (Edge speaks 8 French voices).
MAX_CAST = 8
CAST_PARAMS = ("selected", "custom")
# What a cast job may carry: the user's params, and ``introduced_in`` -- set
# by an accepted N1 character (phase 5, :func:`decide_proposal`), never by
# the cast form.
CAST_JOB_PARAMS = CAST_PARAMS + ("introduced_in",)
CUSTOM_CHARACTER_KEYS = ("name", "role", "one_line", "archetype")
PLACES_PARAMS = ("places", "props")
PLACE_ITEM_KEYS = ("name", "one_line")
PROP_ITEM_KEYS = ("name", "one_line", "owner")
SEASON_PARAMS = ("episodes",)

# The fields an inline edit may set (the API's Character/Place/PropPatchRequest).
CHARACTER_PATCH_FIELDS = (
    "name", "role", "archetype", "one_line", "descriptor", "signature_items", "personality",
    "voice_direction", "sample_line", "rate", "pitch",
)
PLACE_PATCH_FIELDS = ("name", "one_line", "descriptor", "layout_notes")
PROP_PATCH_FIELDS = ("name", "one_line", "descriptor", "owner_char_id")
PATCH_FIELDS_BY_KIND = {CHARACTERS: CHARACTER_PATCH_FIELDS, PLACES: PLACE_PATCH_FIELDS, PROPS: PROP_PATCH_FIELDS}

# The character_v1 caps a request is checked against before anything is written.
NAME_MAX = 60
ONE_LINE_MAX = 200
ARCHETYPE_MAX = 60

# A voice sample not written yet (K1 writes its line) is estimated at the
# line's cap: ``voice.sample_line`` holds at most 120 characters.
SAMPLE_CHARS_ESTIMATE = 120

# What a character, a place and a prop need before they are approved -- and
# what the story page's ``progress`` lists as missing -- as words for a sentence.
MISSING_LABELS = {
    "text": "text", "portrait": "portrait", "turnaround": "turnaround", "expressions": "expressions sheet",
    "voice": "a pinned voice", "sample": "a voice sample", "day": "day plate", "image": "image",
}

MASTER_PLATE = schemas.MASTER_PLATE_VARIANT
SHEETS = ("turnaround", "expressions")


def _invalid_values(message, errors) -> WorkflowError:
    return WorkflowError(INVALID, {"message": message, "errors": list(errors)})


# ------------------------------------------------------------- the story

def reached(story, status) -> bool:
    """Whether the story's derived status has reached *status*
    (``defaults.STATUSES`` order; a contiguous prefix of the approvals)."""
    order = defaults.STATUSES
    return order.index(story_store.derive_status(story.get("approvals"))) >= order.index(status)


def read_entity(stories, story_id, kind, eid) -> dict:
    """One entity of the story; ``not_found`` for an unknown or malformed id,
    ``StoryUnreadable`` for a document that does not validate."""
    try:
        return stories.read_entity(story_id, kind, eid)
    except KeyError:
        raise WorkflowError(NOT_FOUND, f"This story has no {ENTITY_WORDS[kind]} {eid!r}.") from None
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None


def list_entities(stories, story_id, kind) -> list:
    """Every valid entity of *kind* (``StoryStore.list_entities``: an
    unreadable folder is skipped and printed)."""
    try:
        return stories.list_entities(story_id, kind)
    except KeyError:
        raise not_found() from None


def season(stories, story_id):
    """The story's ``season.json``, or None before the season step."""
    return read_doc(stories, story_id, SEASON_DOC, schemas.season_arc_errors)


def places_proposal(stories, story_id):
    """The story's ``places_proposal.json``, or None before P0."""
    return read_doc(stories, story_id, PLACES_PROPOSAL_DOC, schemas.places_proposal_errors)


# ------------------------------------------------------------ what is missing

def _has(stories, story_id, kind, eid, ref) -> bool:
    return entities_step.has_file(stories, story_id, kind, eid, ref)


def character_written(doc) -> bool:
    """K1 has written it: a descriptor and 2-3 signature items."""
    low, high = schemas.SIGNATURE_ITEMS_WRITTEN
    return bool(doc["descriptor"]) and low <= len(doc["signature_items"]) <= high


def character_missing(stories, story_id, doc) -> list:
    """What the character still lacks before it can be approved, in order:
    ``text`` (a descriptor and 2-3 signature items), ``portrait``,
    ``turnaround``, ``expressions`` (whatever their consistency label), ``voice``
    (a pinned voice) and ``sample`` (its voice sample, on disk). An image or a
    sample counts only as a regular file where the store keeps it."""
    cid = doc["char_id"]
    missing = [] if character_written(doc) else ["text"]
    for which in ("portrait",) + SHEETS:
        if not _has(stories, story_id, CHARACTERS, cid, doc["refs"][which]):
            missing.append(which)
    if not doc["voice"]:
        missing.append("voice")
    if not entities_step.has_sample(stories, story_id, cid):
        missing.append("sample")
    return missing


def place_missing(stories, story_id, doc) -> list:
    """``text`` (a descriptor and layout notes) and ``day`` (the master
    plate); the other time variants are made on demand and never block."""
    missing = [] if doc["descriptor"] and doc["layout_notes"] else ["text"]
    if not _has(stories, story_id, PLACES, doc["place_id"], doc["time_variants"].get(MASTER_PLATE)):
        missing.append(MASTER_PLATE)
    return missing


def prop_missing(stories, story_id, doc) -> list:
    """``text`` (a descriptor) and ``image``."""
    missing = [] if doc["descriptor"] else ["text"]
    if not _has(stories, story_id, PROPS, doc["prop_id"], doc["image"]):
        missing.append("image")
    return missing


MISSING = {CHARACTERS: character_missing, PLACES: place_missing, PROPS: prop_missing}


def image_verdict(stories, story, qty, *, env) -> dict:
    """``IMAGE_CHAIN``'s verdict on *qty* reference images for *story*
    (``imaging.estimate``: the story's route, keys, ``allow_paid`` and the
    caps with the story's ledger total, the free allowance; a local link is
    "probed when it runs"). Nothing is called. The API's gate and estimate
    and the CLI's gate ask this same question."""
    width, height = refimages.PORTRAIT_SIZE
    return imaging.estimate(
        gen.IMAGE, env, route=story["generation_profile"]["route"],
        request=gen.GenRequest(kind=gen.IMAGE, width=width, height=height), qty=qty,
        story_spent=cost_total(stories, story["story_id"]), step="image",
        what="a reference image", when="the step runs",
    )


def edit_readiness(stories, story, *, env, qty, probe_local=False):
    """``refimages.edit_readiness`` for *qty* reference images, with the
    story's ledger total against the per-story cap (``stories=``). It calls
    nothing -- a local link stays "probed when it runs" (the gates) -- unless
    *probe_local* (the story page, the cast and places estimates): a local
    editor that would run is then asked whether it is there, a short status
    probe remembered per server for a minute. A ledger that cannot be read
    makes it blocked, with that reason."""
    try:
        return refimages.edit_readiness(story, env=env, qty=qty, stories=stories, probe_local=probe_local)
    except refimages.RefImageError as exc:
        return imaging.blocked(refimages.READINESS_STEP, qty, [], str(exc))


def progress(stories, story, *, env, probe_local=False) -> dict:
    """What each entity of the story still lacks, derived, calling nothing
    (but a local editor's status probe, with *probe_local*)::

        {"characters": {char_id: {"missing": [...], "needs_editor": bool}},
         "places": {place_id: {"missing": [...]}},
         "props": {prop_id: {"missing": [...]}},
         "pick_voice": [char_id, ...],
         "edit_readiness": <refimages.edit_readiness> | null}

    ``missing`` is :func:`character_missing` / :func:`place_missing` /
    :func:`prop_missing`. ``edit_readiness`` is given once any sheet or time
    variant is missing (for that many images; :func:`edit_readiness`, with
    *probe_local*); ``needs_editor`` is true for a character in
    ``references`` mode that has its portrait, lacks a sheet, and whose
    sheets no editor can make now -- spec 8.1's "stop and ask".
    ``pick_voice``: the characters whose text is written but who have no
    pinned voice (no catalogue voice was left for them).
    """
    story_id = story["story_id"]
    characters = entities_step.cast_order(list_entities(stories, story_id, CHARACTERS))
    places = list_entities(stories, story_id, PLACES)
    props = list_entities(stories, story_id, PROPS)

    char_missing = {doc["char_id"]: character_missing(stories, story_id, doc) for doc in characters}
    sheets = sum(1 for missing in char_missing.values() for sheet in SHEETS if sheet in missing)
    variants = sum(1 for doc in places for name, ref in doc["time_variants"].items()
                   if name != MASTER_PLATE and not _has(stories, story_id, PLACES, doc["place_id"], ref))
    readiness = (edit_readiness(stories, story, env=env, qty=sheets + variants, probe_local=probe_local)
                 if sheets + variants else None)
    blocked = readiness is not None and not readiness["ready"]
    references = story["generation_profile"]["consistency_mode"] == refimages.REFERENCES

    out_characters = {}
    for doc in characters:
        missing = char_missing[doc["char_id"]]
        waits = (references and blocked and "portrait" not in missing
                 and any(sheet in missing for sheet in SHEETS))
        out_characters[doc["char_id"]] = {"missing": missing, "needs_editor": waits}
    return {
        "characters": out_characters,
        "places": {doc["place_id"]: {"missing": place_missing(stories, story_id, doc)} for doc in places},
        "props": {doc["prop_id"]: {"missing": prop_missing(stories, story_id, doc)} for doc in props},
        "pick_voice": [doc["char_id"] for doc in characters if doc["voice_hints"] and not doc["voice"]],
        "edit_readiness": readiness,
    }


# ------------------------------------------------------------ preconditions

def require_style_approved(story) -> None:
    """The cast, and the places proposal, are drawn in the locked style."""
    if not reached(story, "style_approved"):
        raise WorkflowError(CONFLICT, "Approve the style first.")


def require_cast_approved(story) -> None:
    """The season arc is written for the approved cast."""
    if not reached(story, "cast_approved"):
        raise WorkflowError(CONFLICT, "Approve the cast first.")


def require_places_proposable(stories, story) -> None:
    """P0 proposes places and props from the bible and the cast."""
    require_style_approved(story)
    if not list_entities(stories, story["story_id"], CHARACTERS):
        raise WorkflowError(CONFLICT, "Write the cast first: places and props are proposed from it.")


def require_places_ready(stories, story, params) -> None:
    """The places step makes the list the user confirmed (*params*) or the
    saved proposal, once at least one character is written."""
    require_style_approved(story)
    story_id = story["story_id"]
    if not any(character_written(doc) for doc in list_entities(stories, story_id, CHARACTERS)):
        raise WorkflowError(CONFLICT, "Write at least one character first: props are drawn for the cast.")
    if params.get("places") is None and params.get("props") is None and places_proposal(stories, story_id) is None:
        raise WorkflowError(CONFLICT, "Propose or list the places first.")


# --------------------------------------------------------------- parameters

def _unknown_keys(params, known, what):
    unknown = sorted(set(params) - set(known))
    if unknown:
        raise WorkflowError(INVALID, f"Unknown {what} parameter(s) {', '.join(unknown)} (known: {', '.join(known)}).")


def _text_error(errors, path, value, *, limit, required=True) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}: expected a non-empty text")
    elif len(value.strip()) > limit:
        errors.append(f"{path}: {len(value.strip())} characters, at most {limit}")


def _sketch(story) -> list:
    concept = story.get("concept") or {}
    sketch = concept.get("cast_sketch") if isinstance(concept, dict) else None
    return [entry for entry in (sketch or []) if isinstance(entry, dict) and isinstance(entry.get("name"), str)]


def sketch_names(story) -> list:
    """The names of the chosen concept's cast sketch, in its order."""
    return [entry["name"] for entry in _sketch(story)]


def check_sketch_names(story, selected) -> None:
    """*selected* is a list of names of the chosen concept's cast sketch
    (``invalid`` otherwise, naming the valid ones)."""
    if not isinstance(selected, list) or not all(isinstance(name, str) for name in selected):
        raise WorkflowError(INVALID, "'selected' must be a list of names from the concept's cast sketch.")
    sketch = _sketch(story)
    known = entities_step.by_name(sketch)
    unknown = [name for name in selected if entities_step.name_key(name) not in known]
    if unknown:
        valid = [entry["name"] for entry in sketch]
        raise WorkflowError(INVALID, (
            f"{entities_step.quoted_list(unknown)} {'is' if len(unknown) == 1 else 'are'} not in the concept's "
            f"cast sketch; pick from {entities_step.quoted_list(valid) or 'none (the concept has no cast sketch)'}."))


def cast_request(stories, story, params) -> tuple:
    """``(selected names, custom entries)`` of a cast step's *params*,
    checked before a job exists (``invalid``): only ``selected``, ``custom``
    and ``introduced_in``; each selected name one of the concept's cast
    sketch; each custom character ``{name, role, one_line, archetype?}`` with
    a role of ``schemas.CHARACTER_ROLES``; ``introduced_in`` (phase 5: an
    accepted N1 proposal) an episode of the season; at most :data:`MAX_CAST`
    characters once the new ones join the story's; and at least one character
    in all."""
    _unknown_keys(params, CAST_JOB_PARAMS, "cast")
    introduced = params.get("introduced_in")
    if introduced is not None:
        arc = season(stories, story["story_id"])
        planned = arc["episodes_planned"] if arc else 0
        if type(introduced) is not int or not 1 <= introduced <= planned:
            raise WorkflowError(INVALID, (f"params.introduced_in is an episode of the season (1 to {planned}), not "
                                          f"{introduced!r}."))
    selected = params.get("selected")
    custom = params.get("custom")
    selected = [] if selected is None else selected
    custom = [] if custom is None else custom
    check_sketch_names(story, selected)
    if not isinstance(custom, list) or not all(isinstance(entry, dict) for entry in custom):
        raise WorkflowError(INVALID, "'custom' must be a list of characters, each {name, role, one_line, archetype?}.")

    errors = []
    for i, entry in enumerate(custom):
        path = f"custom[{i}]"
        extra = sorted(set(entry) - set(CUSTOM_CHARACTER_KEYS))
        if extra:
            errors.append(f"{path}: unknown key(s) {', '.join(extra)} (known: {', '.join(CUSTOM_CHARACTER_KEYS)})")
        _text_error(errors, f"{path}.name", entry.get("name"), limit=NAME_MAX)
        _text_error(errors, f"{path}.one_line", entry.get("one_line"), limit=ONE_LINE_MAX)
        if entry.get("role") not in schemas.CHARACTER_ROLES:
            errors.append(f"{path}.role: {entry.get('role')!r} is not one of {', '.join(schemas.CHARACTER_ROLES)}")
        archetype = entry.get("archetype")
        if archetype is not None and (not isinstance(archetype, str) or len(archetype) > ARCHETYPE_MAX):
            errors.append(f"{path}.archetype: expected a text of at most {ARCHETYPE_MAX} characters")
    if errors:
        raise _invalid_values("These characters cannot be created.", errors)

    existing = list_entities(stories, story["story_id"], CHARACTERS)
    names = {entities_step.name_key(doc["name"]) for doc in existing}
    new = []
    for name in list(selected) + [entry["name"] for entry in custom]:
        key = entities_step.name_key(name)
        if key not in names and key not in new:
            new.append(key)
    if len(existing) + len(new) > MAX_CAST:
        raise WorkflowError(INVALID, (
            f"A cast has at most {MAX_CAST} characters: this story has {len(existing)} and the request "
            f"adds {len(new)}."))
    if not existing and not new:
        raise WorkflowError(INVALID, "Pick characters from the concept's cast sketch or add your own first.")
    return list(selected), [dict(entry) for entry in custom]


def places_request(stories, story, params) -> None:
    """A places step's *params*, checked before a job exists (``invalid``):
    only ``places`` and ``props``, each a list of at most
    ``schemas.PROPOSAL_MAX_ITEMS`` entries ``{name, one_line}`` (a prop also
    ``owner``: a character's id or name, or null)."""
    _unknown_keys(params, PLACES_PARAMS, "places")
    errors = []
    cast = list_entities(stories, story["story_id"], CHARACTERS)
    owners = {doc["char_id"] for doc in cast} | {entities_step.name_key(doc["name"]) for doc in cast}
    for key, keys in (("places", PLACE_ITEM_KEYS), ("props", PROP_ITEM_KEYS)):
        items = params.get(key)
        if items is None:
            continue
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            errors.append(f"{key}: expected a list of {{{', '.join(keys)}}}")
            continue
        if len(items) > schemas.PROPOSAL_MAX_ITEMS:
            errors.append(f"{key}: {len(items)} entries, at most {schemas.PROPOSAL_MAX_ITEMS}")
        for i, item in enumerate(items):
            path = f"{key}[{i}]"
            extra = sorted(set(item) - set(keys))
            if extra:
                errors.append(f"{path}: unknown key(s) {', '.join(extra)} (known: {', '.join(keys)})")
            _text_error(errors, f"{path}.name", item.get("name"), limit=NAME_MAX)
            _text_error(errors, f"{path}.one_line", item.get("one_line"), limit=ONE_LINE_MAX)
            if key == "props":
                owner = item.get("owner")
                if owner is not None and (not isinstance(owner, str) or (
                        owner not in owners and entities_step.name_key(owner) not in owners)):
                    errors.append(f"{path}.owner: {owner!r} is no character of this story")
    if errors:
        raise _invalid_values("These places and props cannot be made.", errors)


def season_request(params) -> int:
    """A season step's episode count (``params = {episodes?}``, 3 to 12,
    ``season.DEFAULT_EPISODES`` when not given); ``invalid`` otherwise."""
    _unknown_keys(params, SEASON_PARAMS, "season")
    value = params.get("episodes")
    if value is None:
        return season_step.DEFAULT_EPISODES
    low, high = schemas.EPISODES_PLANNED_MIN, schemas.EPISODES_PLANNED_MAX
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise WorkflowError(INVALID, f"A season has {low} to {high} episodes, not {value!r}.")
    return value


# -------------------------------------------------------------------- units

def _units(llm_calls=0, images=0, edit_images=0, tts_chars=0) -> dict:
    return {"llm_calls": llm_calls, "images": images, "edit_images": edit_images, "tts_chars": tts_chars}


def _sample_chars(doc) -> int:
    block = doc.get("voice") or doc.get("voice_hints") or {}
    line = (block.get("sample_line") or "").strip()
    return len(line) if line else SAMPLE_CHARS_ESTIMATE


def cast_units(stories, story, *, selected=(), custom=()) -> dict:
    """What a cast step would make, counting only what is missing:
    ``{llm_calls, images, edit_images, tts_chars}``. A character not created
    yet (a selected sketch name or a custom entry the story lacks) counts
    fully: K1, a portrait, two sheets, a sample of up to
    :data:`SAMPLE_CHARS_ESTIMATE` characters. The sheets are edits in
    ``references`` mode and text-to-image in ``prompt_only`` mode."""
    story_id = story["story_id"]
    prompt_only = story["generation_profile"]["consistency_mode"] == refimages.PROMPT_ONLY
    units = _units()

    def sheets(count):
        units["images" if prompt_only else "edit_images"] += count

    existing = list_entities(stories, story_id, CHARACTERS)
    names = {entities_step.name_key(doc["name"]) for doc in existing}
    for doc in existing:
        missing = character_missing(stories, story_id, doc)
        units["llm_calls"] += "text" in missing
        units["images"] += "portrait" in missing
        sheets(sum(sheet in missing for sheet in SHEETS))
        if "sample" in missing:
            units["tts_chars"] += _sample_chars(doc)
    for name in list(selected) + [entry.get("name") for entry in custom]:
        key = entities_step.name_key(name)
        if key in names:
            continue
        names.add(key)
        units["llm_calls"] += 1
        units["images"] += 1
        sheets(len(SHEETS))
        units["tts_chars"] += SAMPLE_CHARS_ESTIMATE
    return units


def places_units(stories, story, params=None) -> dict:
    """What a places step would make: for every place and prop of the story,
    P1/R1 when its text is missing and its day plate or image when missing;
    each item of the list (*params*, else the saved proposal) not created yet
    counts fully (one call, one image). Time variants are made on demand
    (``place:<id>:image:<variant>``) and are not counted."""
    story_id = story["story_id"]
    params = params or {}
    if params.get("places") is None and params.get("props") is None:
        params = places_proposal(stories, story_id) or {}
    units = _units()
    for kind, key in ((PLACES, "places"), (PROPS, "props")):
        existing = list_entities(stories, story_id, kind)
        names = {entities_step.name_key(doc["name"]) for doc in existing}
        for doc in existing:
            missing = MISSING[kind](stories, story_id, doc)
            units["llm_calls"] += "text" in missing
            units["images"] += len(missing) - ("text" in missing)
        for item in params.get(key) or ():
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(name, str) or entities_step.name_key(name) in names:
                continue
            names.add(entities_step.name_key(name))
            units["llm_calls"] += 1
            units["images"] += 1
    return units


def target_units(stories, story, parsed) -> dict:
    """What one regenerate target (``regenerate.parse_target``'s tuple) would
    make: text, ``season:<ep>`` and an episode target (E2, E3, T1r or M1) one
    LLM call; a portrait one image, and again each sheet it already has (they
    are drawn from it); a sheet or a time variant one edit (``references``)
    or one image (``prompt_only``); a day plate or a prop image one image; a
    voice the characters of its sample line. Phase 4: a shot's image one
    image (``prompt_only``) or one edit (``references``: the shot's
    references are sent); a line's voice the characters of its text. Phase
    6: a shot's clip none of these (no LLM call, no image, no voice): its
    one clip is priced by :func:`regenerate_clip_estimate`."""
    prompt_only = story["generation_profile"]["consistency_mode"] == refimages.PROMPT_ONLY
    if parsed[0] == regenerate_step.SHOT_VIDEO_KIND:
        return _units()
    if parsed[0] == regenerate_step.SHOT_IMAGE_KIND:
        return _units(images=1) if prompt_only else _units(edit_images=1)
    if parsed[0] == regenerate_step.LINE_KIND:
        script = read_episode(stories, story["story_id"], parsed[1], SCRIPT_DOC) or {"scenes": []}
        line = next((ln for scene in script["scenes"] for ln in scene["lines"] if ln["line_id"] == parsed[2]), None)
        return _units(tts_chars=len(line["text"]) if line else 0)
    if parsed[0] in regenerate_step.EPISODE_KINDS or parsed[0] == "season" or parsed[2] == "text":
        return _units(llm_calls=1)
    kind = ENTITY_KINDS_BY_WORD[parsed[0]]
    doc = read_entity(stories, story["story_id"], kind, parsed[1])
    if parsed[2] == "voice":
        return _units(tts_chars=_sample_chars(doc))
    slot = parsed[3] if len(parsed) > 3 else "image"
    if slot in ("portrait", MASTER_PLATE, "image"):
        units = _units(images=1)
        if slot == "portrait":
            again = sum(1 for sheet in SHEETS if doc["refs"][sheet] is not None)
            units["images" if prompt_only else "edit_images"] += again
        return units
    return _units(images=1) if prompt_only else _units(edit_images=1)


def target_needs_editor(story, parsed) -> bool:
    """Whether the target *is* an edit: a sheet or a time variant in
    ``references`` mode (a portrait's sheets that cannot be redrawn are
    recorded, not failed, so a portrait never needs one); a shot's image in
    ``references`` mode (phase 4: made from its references)."""
    if story["generation_profile"]["consistency_mode"] == refimages.PROMPT_ONLY:
        return False
    if parsed[0] == regenerate_step.SHOT_IMAGE_KIND:
        return True
    if parsed[0] in regenerate_step.EPISODE_KINDS or parsed[0] == "season" or parsed[2] != "image":
        return False
    slot = parsed[3] if len(parsed) > 3 else "image"
    return slot not in ("portrait", MASTER_PLATE, "image")


# ------------------------------------------------------------------ targets

def _pinned_by_others(stories, story_id, char_id) -> dict:
    """``{(provider, voice_id): name}`` pinned by the other leads/supports."""
    taken = {}
    for doc in list_entities(stories, story_id, CHARACTERS):
        if doc["char_id"] == char_id or doc["role"] not in schemas.CAST_APPROVAL_ROLES or not doc["voice"]:
            continue
        taken.setdefault((doc["voice"]["provider"], doc["voice"]["voice_id"]), doc["name"])
    return taken


def _voice_json(voice) -> dict:
    return {
        "provider": voice.provider, "voice_id": voice.voice_id, "lang": voice.lang,
        "gender": voice.gender, "age": voice.age, "style_tags": list(voice.style_tags),
        "link": registry.describe(voice.link),
    }


def character_voices(stories, story, char_id, *, env) -> dict:
    """The voice picker's data for one character (a phase-2 route, spec 8.1,
    11): ``{"pinned": {provider, voice_id} | None, "alternates": [{provider,
    voice_id, lang, gender, age, style_tags, link}], "taken": ["provider/
    voice_id", ...]}``.

    ``alternates`` is up to :data:`voices.ALTERNATES_LIMIT` other catalogue
    voices (:func:`voices.alternates`: no network call), best first, never
    one already pinned by another lead/support and never the character's own
    current pin (offering it back among "other voices" would be redundant).
    ``taken`` is those other leads'/supports' pinned voices, for display.
    ``not_found`` for an unknown character.
    """
    story_id = story["story_id"]
    doc = read_entity(stories, story_id, CHARACTERS, char_id)
    taken = set(_pinned_by_others(stories, story_id, char_id))
    pinned = {"provider": doc["voice"]["provider"], "voice_id": doc["voice"]["voice_id"]} if doc["voice"] else None
    exclude_own = {(pinned["provider"], pinned["voice_id"])} if pinned else set()
    pool = voices.alternates(doc, story["language"], env=env, taken=taken | exclude_own)
    return {
        "pinned": pinned,
        "alternates": [_voice_json(voice) for voice in pool],
        "taken": sorted(f"{provider}/{voice_id}" for provider, voice_id in taken),
    }


VOICE_KEYS = ("provider", "voice_id", "rate", "pitch")


def check_voice_choice(stories, story, char_id, voice, *, env) -> dict:
    """A voice a user picked for ``character:<id>:voice``: ``{provider,
    voice_id, rate?, pitch?}``, one of ``voices.catalogue`` for the story's
    language (``invalid``, naming those offered), not pinned by another lead
    or support (``conflict``). Returns it as sent."""
    shape = "A voice is {provider, voice_id} (and optionally rate, pitch)."
    if not isinstance(voice, dict):
        raise WorkflowError(INVALID, shape)
    extra = sorted(set(voice) - set(VOICE_KEYS))
    if extra:
        raise WorkflowError(INVALID, f"{shape} Unknown key(s): {', '.join(extra)}.")
    provider, voice_id = voice.get("provider"), voice.get("voice_id")
    if not (isinstance(provider, str) and provider and isinstance(voice_id, str) and voice_id):
        raise WorkflowError(INVALID, shape)
    for key, pattern, example in (("rate", schemas.VOICE_RATE_PATTERN, "+10%"),
                                  ("pitch", schemas.VOICE_PITCH_PATTERN, "-5Hz")):
        value = voice.get(key)
        if value is not None and (not isinstance(value, str) or re.fullmatch(pattern, value) is None):
            raise WorkflowError(INVALID, f"{key} {value!r} is not like {example!r}.")
    language = story["language"]
    catalogue = voices.catalogue(language, env=env)
    if not any((v.provider, v.voice_id) == (provider, voice_id) for v in catalogue):
        offered = ", ".join(f"{v.provider}/{v.voice_id}" for v in catalogue) or "none"
        raise WorkflowError(INVALID, (f"{provider}/{voice_id} is not a {language} voice TTS_CHAIN can reach; "
                                      f"pick one of: {offered}."))
    owner = _pinned_by_others(stories, story["story_id"], char_id).get((provider, voice_id))
    if owner is not None:
        name = read_entity(stories, story["story_id"], CHARACTERS, char_id)["name"]
        raise WorkflowError(CONFLICT, (f"{provider}/{voice_id} is already {owner}'s voice: no two leads or "
                                       f"supports share a voice; pick another for {name}."))
    return {key: voice[key] for key in VOICE_KEYS if key in voice}


def check_entity_target(stories, story, parsed, *, voice=None, env=None):
    """A phase-2 or phase-3 regenerate target (``regenerate.parse_target``'s
    tuple) checked against the story before a job exists; returns the voice
    to pin (``check_voice_choice``) or None.

    ``not_found``: no such character, place or prop, or no such arc entry.
    ``conflict``: no season arc yet; an image or voice of an entity not
    written yet; a sheet without its portrait, a time variant without its
    day plate. ``invalid``: a voice sent with any other target. An episode
    target is :func:`check_episode_target`'s."""
    story_id = story["story_id"]
    is_voice = parsed[0] == "character" and parsed[2] == "voice"
    if voice is not None and not is_voice:
        raise WorkflowError(INVALID, "A voice is picked only with the target character:<char_id>:voice.")
    if parsed[0] in regenerate_step.EPISODE_KINDS:
        check_episode_target(stories, story, parsed)
        return None
    if parsed[0] == "season":
        arc = (season(stories, story_id) or {}).get("arc") or []
        if not arc:
            raise WorkflowError(CONFLICT, "There is no season arc yet: write the season first.")
        if parsed[1] > len(arc):
            raise WorkflowError(NOT_FOUND, f"The season arc has episodes 1 to {len(arc)}; there is no episode "
                                           f"{parsed[1]}.")
        return None

    kind, eid = ENTITY_KINDS_BY_WORD[parsed[0]], parsed[1]
    doc = read_entity(stories, story_id, kind, eid)
    name = doc["name"]
    if is_voice:
        if not (doc["voice_hints"] or doc["voice"]):
            raise WorkflowError(CONFLICT, (f"Write {name} first: the voice's sample line comes from the "
                                           f"character's text (regenerate 'character:{eid}:text')."))
        return None if voice is None else check_voice_choice(stories, story, eid, voice, env=env)
    if parsed[2] != "image":
        return None
    written = character_written(doc) if kind == CHARACTERS else bool(doc["descriptor"])
    if not written:
        raise WorkflowError(CONFLICT, f"Write {name} first: its text makes every image.")
    slot = parsed[3] if len(parsed) > 3 else "image"
    if kind == CHARACTERS and slot in SHEETS and not _has(stories, story_id, kind, eid, doc["refs"]["portrait"]):
        raise WorkflowError(CONFLICT, f"Make {name}'s portrait first: the {slot} is drawn from it.")
    if kind == PLACES and slot != MASTER_PLATE and not _has(
            stories, story_id, kind, eid, doc["time_variants"].get(MASTER_PLATE)):
        raise WorkflowError(CONFLICT, f"Make {name}'s day plate first: every other variant is made from it.")
    return None


# ---------------------------------------------------------------- approvals

def approve_entity(stories, story_id, kind, eid, *, now) -> dict:
    """Approve one character, place or prop; returns the story.

    ``not_found`` for an unknown one; ``conflict`` listing what it still
    lacks (:func:`character_missing` & co.). Its ``approved_at`` becomes
    *now* (the character re-read and written under the uploads' lock), and
    the store re-folds ``approvals.cast`` / ``approvals.places``.
    """
    load(stories, story_id)
    doc = read_entity(stories, story_id, kind, eid)
    missing = MISSING[kind](stories, story_id, doc)
    if missing:
        labels = ", ".join(MISSING_LABELS[item] for item in missing)
        raise WorkflowError(CONFLICT, f"{doc['name']} cannot be approved yet; missing: {labels}.")

    def approve(current):
        current["approved_at"] = now

    try:
        entities_step.write_entity(stories, story_id, kind, eid, approve, now=now)
    except KeyError:
        raise WorkflowError(NOT_FOUND, f"This story has no {ENTITY_WORDS[kind]} {eid!r}.") from None
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None
    return load(stories, story_id)


def approve_season(stories, story_id, *, now) -> dict:
    """Approve the season arc; returns the story, now ``ready``.

    ``conflict`` until the places and props are approved (the status is a
    contiguous prefix), without a ``season.json``, or while the arc does not
    hold ``episodes_planned`` entries each with a summary. Sets the arc's
    ``approved_at`` -- on ``season.json`` re-read under the store lock
    (``StoryStore.update_doc``), so a series step's write landing meanwhile
    is kept -- and ``approvals.season``.
    """
    story = load(stories, story_id)
    if not reached(story, "places_approved"):
        raise WorkflowError(CONFLICT, "Approve the cast, the places and the props first.")
    doc = season(stories, story_id)
    if doc is None:
        raise WorkflowError(CONFLICT, "There is no season arc to approve yet: run the season step first.")
    planned = doc["episodes_planned"]
    written = [entry["ep"] for entry in doc["arc"] if str(entry.get("summary") or "").strip()]
    if len(doc["arc"]) != planned or len(written) != planned:
        raise WorkflowError(CONFLICT, (f"The season arc is not complete: {len(written)} of {planned} episodes "
                                       "have a summary."))

    def approve(current):
        # Re-read under the store lock: a memory entry, a feedback digest or
        # an amended arc entry written meanwhile is kept (plan 11 stage 4).
        if current is None:
            raise WorkflowError(CONFLICT, "There is no season arc to approve yet: run the season step first.")
        current["approved_at"] = now
        return current

    try:
        stories.update_doc(story_id, SEASON_DOC, approve, now=now, validator=schemas.season_arc_errors)
    except KeyError:
        raise not_found() from None
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None

    def mutate(story_doc):
        story_doc["approvals"]["season"] = now

    return update(stories, story_id, mutate, now=now)


# -------------------------------------------------------------------- edits

def _prompt_block(kind, lock, doc):
    """The entity's prompt block from its fields as they now are (the pure
    ``prompting`` builders), or None while a field it needs is empty or the
    story has no style lock."""
    if lock is None:
        return None
    if kind == CHARACTERS:
        items = doc["signature_items"]
        if isinstance(doc["descriptor"], str) and doc["descriptor"] and isinstance(items, list) and items \
                and all(isinstance(item, str) for item in items):
            return prompting.character_prompt_block(lock, descriptor=doc["descriptor"], signature_items=items)
        return None
    if kind == PLACES:
        if all(isinstance(doc[key], str) and doc[key] for key in ("descriptor", "layout_notes")):
            return prompting.place_prompt_block(lock, descriptor=doc["descriptor"], layout_notes=doc["layout_notes"])
        return None
    if isinstance(doc["descriptor"], str) and doc["descriptor"]:
        return prompting.prop_prompt_block(lock, descriptor=doc["descriptor"])
    return None


_BLOCK_FIELDS = {CHARACTERS: ("descriptor", "signature_items"), PLACES: ("descriptor", "layout_notes"),
                 PROPS: ("descriptor",)}
# The fields that change what the voice sample says or how it sounds.
_SAMPLE_FIELDS = ("sample_line", "rate", "pitch")
# Text fields kept without their surrounding spaces, as the steps write them.
_STRIPPED = ("name", "archetype", "one_line", "descriptor", "layout_notes", "voice_direction", "sample_line")


def _apply_character(doc, values) -> None:
    for name in ("name", "role", "archetype", "one_line", "descriptor", "signature_items"):
        if name in values:
            doc[name] = values[name]
    if "personality" in values:
        personality = values["personality"]
        if not isinstance(personality, dict):
            raise _invalid_values("The character would not be valid with these values.",
                                  ["$.personality: expected an object"])
        doc["personality"] = {**doc["personality"], **personality}
    brief = [block for block in (doc["voice"], doc["voice_hints"]) if block]
    for field, key in (("voice_direction", "direction"), ("sample_line", "sample_line")):
        if field in values:
            if not brief:
                raise WorkflowError(CONFLICT, (f"{doc['name']} has no voice brief yet: the cast step writes it "
                                               "with the character's text."))
            for block in brief:
                block[key] = values[field]
    for key in ("rate", "pitch"):
        if key in values:
            if not doc["voice"]:
                raise WorkflowError(CONFLICT, f"{doc['name']} has no pinned voice yet: the cast step pins one.")
            doc["voice"][key] = values[key]


def _apply(kind, doc, values, *, lock, cast_ids) -> None:
    """*values* into the entity *doc* (in place), its prompt block recomputed
    when a field it is made of changed, its approval cleared."""
    if kind == CHARACTERS:
        _apply_character(doc, values)
    else:
        for name, value in values.items():
            doc[name] = value
        if kind == PROPS and "owner_char_id" in values:
            owner = values["owner_char_id"]
            if owner is not None and owner not in cast_ids:
                raise _invalid_values("The prop would not be valid with these values.",
                                      [f"$.owner_char_id: {owner!r} is no character of this story"])
    if set(values) & set(_BLOCK_FIELDS[kind]):
        doc["prompt_block"] = _prompt_block(kind, lock, doc)
    doc["approved_at"] = None


def patch_entity(stories, story_id, kind, eid, fields, *, now) -> dict:
    """Edit the fields sent of one character, place or prop; returns what
    was written.

    Nothing sent, nothing written. A field outside the kind's
    ``*_PATCH_FIELDS`` is ``invalid``; a document the kind's rules refuse is
    ``invalid`` with ``{"message", "errors"}`` (a name another entity of the
    kind already has among them). A character's ``voice_direction`` and
    ``sample_line`` go into its pinned voice and its voice brief (``conflict``
    without either), ``rate`` and ``pitch`` into its pinned voice (``conflict``
    without one); ``personality`` is merged onto the current one. A change to
    a field the prompt block is made of recomputes it; **every** edit clears
    the entity's ``approved_at`` (the store re-folds the group approval). A
    changed sample line, rate or pitch removes the voice sample, which no
    longer says or sounds like that. The entity is re-read (under the
    uploads' lock, for a character) right before it is written.
    """
    load(stories, story_id)
    current = read_entity(stories, story_id, kind, eid)
    if not fields:
        return current
    allowed = PATCH_FIELDS_BY_KIND[kind]
    unknown = sorted(set(fields) - set(allowed))
    if unknown:
        raise WorkflowError(INVALID, (f"These {ENTITY_WORDS[kind]} fields cannot be edited: {', '.join(unknown)} "
                                      f"(editable: {', '.join(allowed)})."))
    values = {name: (value.strip() if name in _STRIPPED and isinstance(value, str) else copy.deepcopy(value))
              for name, value in fields.items()}
    lock = style_lock(stories, story_id)
    cast_ids = {doc["char_id"] for doc in list_entities(stories, story_id, CHARACTERS)}
    word = ENTITY_WORDS[kind]
    validator = story_store.ENTITY_KINDS[kind].validator

    trial = copy.deepcopy(current)
    _apply(kind, trial, values, lock=lock, cast_ids=cast_ids)
    errors = validator(trial)
    if "name" in values and isinstance(values["name"], str):
        id_field = story_store.ENTITY_KINDS[kind].id_field
        key = entities_step.name_key(values["name"])
        if any(entities_step.name_key(doc["name"]) == key and doc[id_field] != eid
               for doc in list_entities(stories, story_id, kind)):
            errors = list(errors) + [f"$.name: another {word} is already named {values['name']!r}"]
    if errors:
        raise _invalid_values(f"The {word} would not be valid with these values.", errors)

    try:
        saved = entities_step.write_entity(
            stories, story_id, kind, eid, lambda doc: _apply(kind, doc, values, lock=lock, cast_ids=cast_ids),
            now=now)
    except KeyError:
        raise WorkflowError(NOT_FOUND, f"This story has no {word} {eid!r}.") from None
    except schemas.SchemaError as exc:
        raise _invalid_values(f"The {word} would not be valid with these values.", exc.errors) from None
    if kind == CHARACTERS and set(values) & set(_SAMPLE_FIELDS):
        entities_step.drop_sample(stories, story_id, eid)
    return saved


def delete_entity(stories, story_id, kind, eid, *, now) -> dict:
    """Remove one character, place or prop (``StoryStore.delete_entity``:
    its folder, its id from the story and from every document that names it
    -- relationships, prop owners, the season arc, the places proposal, a
    character's location -- those keeping their approvals; the group
    approvals re-fold); returns the store's ``{"removed", "kept"}``.
    ``not_found`` for an unknown one.

    Under the uploads' lock, taken before the story's as every character
    writer takes them: the cleanup rewrites characters, and an upload
    appended meanwhile must neither be lost nor bring a removed id back."""
    load(stories, story_id)
    try:
        with uploads_mod._ENTRIES_LOCK:
            return stories.delete_entity(story_id, kind, eid, now=now)
    except KeyError:
        raise WorkflowError(NOT_FOUND, f"This story has no {ENTITY_WORDS[kind]} {eid!r}.") from None
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None


# ================================================================== phase 3
#
# Steps 8-9 (spec 3, 2.7-2.8, 9.1-9.2; phase-3 plan 2): one episode's script
# and storyboard, ``episodes/ep<NN>/script.json`` and ``storyboard.json``.
# Their approvals live on those documents: approving, editing or regenerating
# an episode never changes the story's approvals or status (RC-E2, DEC-129).
# What an episode step needs before it may start is
# ``episode_common.check_episode_preconditions`` -- the runners' own check,
# typed -- answered here with a code each. The edits reuse the runners'
# rules: a script is validated as a step validates what it writes, a line
# is timed as E2 times it, a revision moves as a regenerate moves it
# (``episode_common.mark_changed``), a shot is resolved as the storyboard
# resolves it (``shots``).

SCRIPT_DOC = story_store.EPISODE_SCRIPT_DOC
STORYBOARD_DOC = story_store.EPISODE_STORYBOARD_DOC
ASSETS_DOC = story_store.EPISODE_ASSETS_DOC
MANIFEST_DOC = story_store.EPISODE_RENDER_MANIFEST_DOC
METADATA_PACK_DOC = story_store.EPISODE_METADATA_PACK_DOC

# The keys ``params`` of the episode steps may carry (closed lists).
SCRIPT_PARAMS = (script_step.MEASURE_PARAM,)
STORYBOARD_PARAMS = ("fast",)

# Phase 4's params (closed lists; the runners' own: ``assets.PARAMS``,
# ``render.PARAMS``, ``fast_track.PARAMS``; the metadata takes none) and the
# assets edit (the API's AssetsPatchRequest and its items).
ASSETS_PARAMS = assets_step.PARAMS
RENDER_PARAMS = render_step.PARAMS
METADATA_PARAMS = ()
FAST_TRACK_PARAMS = fast_track_step.PARAMS
PHASE4_PARAMS = {"assets": ASSETS_PARAMS, "render": RENDER_PARAMS, "metadata": METADATA_PARAMS,
                 "fast-track": FAST_TRACK_PARAMS}
# Phase 6 stage 6 (A-087): the switch of the episode's image link -- and,
# stage 11, of its video link -- ``{"links": {"image"?: "<link>", "video"?:
# "<link>"}}``, taken by :func:`patch_assets` (the API's AssetsPatchRequest,
# stage 11).
ASSETS_LINKS_FIELD = "links"
ASSETS_PATCH_FIELDS = ("shots", ASSETS_LINKS_FIELD)
ASSETS_SHOT_PATCH_FIELDS = ("locked",)
# Phase 6 stage 7: a shot item's overrides of what its clip does --
# ``keep_still``, ``animate`` (a pin), ``keep_native_audio``; true, false or
# null (clear) -- taken by :func:`patch_assets` into ``assets.json``'s
# ``shots``; the API's AssetsShotPatch carries them (stage 11).
ASSETS_SHOT_FLAG_FIELDS = schemas.SHOT_OVERRIDE_FLAGS

# The document of each episode regenerate target: the one its job writes,
# and so the one whose approval completes it. A ``metadata:<ep>:<platform>``
# has none: its job ends completed (DEC-161).
EPISODE_TARGET_DOCS = {"scene": "script", "hook": "script", "cliffhanger": "script", "teaser": "script",
                       "shot": "storyboard", regenerate_step.SHOT_IMAGE_KIND: "assets",
                       regenerate_step.SHOT_VIDEO_KIND: "assets", regenerate_step.LINE_KIND: "assets"}

# What an edit may set (the API's ScriptPatchRequest / StoryboardPatchRequest
# and their items). An item names what it edits by its id (``line_id``,
# ``scene_id``, ``shot_id``, ``after``); everything else in the documents is
# the steps' to write.
SCRIPT_PATCH_FIELDS = ("lines", "scenes", "hook_on_screen_text", "cliffhanger_reveal", "next_episode_teaser")
SCRIPT_LINE_PATCH_FIELDS = ("text", "speaker", "emotion", "delivery")
# ``pays_off`` (phase 5 stage 7): a scene's payoff re-planned by hand, checked
# against the hooks open before the episode (``_edit_pays_off``).
SCRIPT_SCENE_PATCH_FIELDS = ("summary", "on_screen_text", "pays_off")
STORYBOARD_PATCH_FIELDS = ("shots", "transitions", "refresh_prompts")
STORYBOARD_SHOT_PATCH_FIELDS = ("framing", "camera_motion", "modifiers", "action", "keep_still", "prompt_override")
STORYBOARD_TRANSITION_PATCH_FIELDS = ("type",)

# An episode number in a URL: 1..99, written as the store writes its folders
# would not (no sign, no leading zero, no other script's digits).
_EPISODE_NUMBER = re.compile(r"^[1-9][0-9]?$")

# How each of episode_common's refusals is answered.
_EPISODE_REFUSALS = {
    episode_common.NOT_READY: CONFLICT,
    episode_common.OUTSIDE_SEASON: INVALID,
    episode_common.NO_ARC_ENTRY: CONFLICT,
    episode_common.MEMORY_MISSING: CONFLICT,
    episode_common.MEMORY_UNAPPROVED: CONFLICT,
    episode_common.MEMORY_STALE: CONFLICT,
}

_TAG_KINDS = {"char": CHARACTERS, "place": PLACES, "prop": PROPS}


def _plural_s(items) -> str:
    return "" if len(items) == 1 else "s"


def _and(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# ------------------------------------------------------------ the episode

def episode_number(value) -> int:
    """*value* -- an ``int``, or the text of a URL's path -- as an episode
    number of the store (1..99); ``invalid`` otherwise, before any path is
    built from it. ``True`` is not an episode, nor is ``"01"``."""
    if type(value) is int:
        number = value
    elif isinstance(value, str) and _EPISODE_NUMBER.fullmatch(value):
        number = int(value)
    else:
        number = None
    if number is None or not story_store.EPISODE_MIN <= number <= story_store.EPISODE_MAX:
        raise WorkflowError(INVALID, (f"{value!r} is not an episode number "
                                      f"({story_store.EPISODE_MIN} to {story_store.EPISODE_MAX})."))
    return number


def episode_bounds(stories, story, value) -> int:
    """:func:`episode_number`, and one the season plans once the story has a
    season (``invalid`` otherwise, in episode_common's words): what reading,
    editing or approving an episode's documents asks of the number."""
    ep = episode_number(value)
    arc = season(stories, story["story_id"])
    if arc is not None and ep > arc["episodes_planned"]:
        raise WorkflowError(INVALID, f"The season plans episodes 1 to {arc['episodes_planned']}; there is no "
                                     f"episode {ep}.")
    return ep


def _episode_refused(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except episode_common.EpisodeRefused as exc:
        raise WorkflowError(_EPISODE_REFUSALS[exc.kind], str(exc)) from None


def _context(stories, story_id, ep) -> episode_common.EpisodeContext:
    """``episode_common.load_context``; what it cannot read (a style that is
    not locked, a season or a script that does not validate, an episode
    folder that is not a real one) is a ``conflict`` with its sentence."""
    try:
        return episode_common.load_context(stories, story_id, ep)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None


def episode_context(stories, story, ep, *, step, require_memory=True) -> episode_common.EpisodeContext:
    """The :class:`episode_common.EpisodeContext` of an episode step's (or
    an episode target's, or a series step's) episode *ep*, once the step may
    run on it -- the runners' own preconditions, in their order:

    ``conflict`` for a story that is not ``ready`` (naming what to approve);
    ``invalid`` without an episode number, or for one the season does not
    plan; ``conflict`` for an arc with no entry for it, or -- the gate
    (DEC-130 as amended by plan 11 stage 4), from episode 2 on, for a *step*
    that writes the episode's script or storyboard
    (``episode_common.needs_memory``: the script and storyboard steps, the
    fast track until both are approved) unless *require_memory* is off (a
    regenerate) -- while the series memory of the episode before it is not
    written, approved and fresh, naming which of the three
    (``episode_common.memory_refusal``). The assets, the render and the
    metadata never meet the gate."""
    _episode_refused(episode_common.check_story_ready, story)
    if type(ep) is not int:
        arc = season(stories, story["story_id"]) or {}
        raise WorkflowError(INVALID, (f"'{step}' works on one episode: send its number as ep (the season plans "
                                      f"1 to {arc.get('episodes_planned', 0)})."))
    ec = _context(stories, story["story_id"], ep)
    _episode_refused(episode_common.check_episode_preconditions, None, ec, require_memory=False)
    if require_memory and _step_refusal(episode_common.needs_memory, ec, step):
        _episode_refused(episode_common.check_episode_preconditions, None, ec, require_memory=True)
    return ec


def _flag(params, key) -> bool:
    value = params.get(key)
    if value is not None and type(value) is not bool:
        raise WorkflowError(INVALID, f"params.{key} is true or false, not {value!r}.")
    return bool(value)


def script_request(params) -> bool:
    """A script step's *params* (``{measure_voices?}``, a closed list;
    ``invalid`` otherwise): whether to measure the lines with real voices."""
    _unknown_keys(params, SCRIPT_PARAMS, "script")
    return _flag(params, script_step.MEASURE_PARAM)


def storyboard_request(params) -> bool:
    """A storyboard step's *params* (``{fast?}``, a closed list; ``invalid``
    otherwise): whether to plan the shots the fast way (inline, no call)."""
    _unknown_keys(params, STORYBOARD_PARAMS, "storyboard")
    return _flag(params, "fast")


def require_complete_script(ec) -> dict:
    """The episode's script, complete (every scene written, every framing
    part there); ``conflict`` naming what is missing
    (``storyboard.require_complete_script``, the step's own check)."""
    try:
        return storyboard_step.require_complete_script(ec)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None


def phase4_request(step, params) -> dict:
    """A phase-4 step's *params*, checked as its runner reads them (closed
    lists; ``invalid`` otherwise, naming the choices): ``assets``
    ``{align_words?, animate?}`` (true or false; ``animate``, phase 6 stage
    8, is on unless sent false), ``render`` ``{subtitles?, encoder?}``
    (``render.SUBTITLE_CHOICES``, ``render.ENCODER_CHOICES``), ``fast-track``
    ``{storyboard?}`` (``fast_track.STORYBOARD_CHOICES``); ``metadata`` takes
    none. Returns them as sent."""
    known = PHASE4_PARAMS[step]
    if not known and params:
        raise WorkflowError(INVALID, f"'{step}' takes no parameters.")
    _unknown_keys(params, known, step)
    if step == "assets":
        _flag(params, assets_step.ALIGN_PARAM)
        _flag(params, assets_step.ANIMATE_PARAM)
    try:
        if step == "render":
            render_step.read_params(params)
        elif step == "fast-track":
            fast_track_step.read_params(params)
    except StepFailed as exc:
        raise WorkflowError(INVALID, str(exc)) from None
    return dict(params)


def require_step_inputs(ec, step, *, params=None) -> None:
    """What a phase-4 step is made from, checked as its runner checks it before
    anything runs, calling nothing (``conflict`` with the runner's own
    sentence): the assets an approved script and an approved, current
    storyboard (``assets.require_approved``); the render the assets approved
    with a current fingerprint, every image and voice on disk
    (``render.require_renderable``) and -- phase 6 stage 11 -- at tier >= 2
    every shot's clip it would cut current, unless *params* (the render's)
    fill the others with motion (``render.require_clips``); the metadata a
    finished render (``metadata.require_render``). The fast track starts
    from what there is."""
    check = {"assets": assets_step.require_approved,
             "render": lambda context: render_step.require_clips(context, params),
             "metadata": metadata_step.require_render}.get(step)
    if check is None:
        return
    try:
        check(ec)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None


def reedit_request(params) -> dict:
    """A rerender step's *params*: none. ``invalid`` otherwise, with the
    runner's own sentence (``rerender.run``) duplicated here since the
    runner never gets to raise it -- the route and the CLI check this before
    a job exists (plan 11 stage 9). Returns ``{}``."""
    if params:
        extra = ", ".join(sorted(params))
        raise WorkflowError(INVALID, (f"A re-render keeps the last render's subtitles and encoder and takes no "
                                      f"parameters (got {extra}); to change them, render the episode (the render "
                                      "step)."))
    return {}


def require_reedit_inputs(ec) -> dict:
    """What a re-render is made from, checked before anything runs or is
    estimated, calling nothing (``conflict`` with the runner's own
    sentence, in the runner's own order): a finished render of the episode to
    re-render (``rerender.require_finished_render``), then every render
    precondition, naming an outdated shot's regenerate target
    (``render.require_renderable`` -- the render's own check, RC-M8: never a
    second implementation). The route and the CLI meet both before a job
    exists; the dry run (:func:`reedit_changes`) and the estimate meet them
    too, so a re-render that would be refused is never estimated as though
    it could run. Returns the baseline manifest a re-render would meet."""
    try:
        baseline = rerender_step.require_finished_render(ec)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None
    try:
        render_step.require_renderable(ec)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None
    return baseline


def reedit_changes(ec) -> dict:
    """The dry run of a re-render of *ec*'s episode right now (``GET
    /estimate/rerender``, ``step ID rerender --dry-run``): ``render.
    render_changes(ec, None)`` -- stage 8's own predicate (RC-M8: the runner
    and the dry run share one implementation, never a second) -- after
    :func:`require_reedit_inputs`. ``conflict`` with the render's own
    sentence when the episode cannot be rendered, or has no finished render
    to re-render, right now. Calls nothing; hashes the cache's clips."""
    require_reedit_inputs(ec)
    try:
        return render_step.render_changes(ec, None)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None


def assets_gate(ec, *, env, animate=True) -> None:
    """The assets step's own stop before its first call
    (``assets.plan_refusal``) checked before a job exists, calling nothing (a
    local editor is not asked: the step asks it): ``conflict`` when the shot
    images cannot run on the story's route -- in ``references`` mode, no
    editor: DEC-117's stop and ask -- the clips cannot (tier >= 2, *animate*
    -- the step's param -- on; clips that wait only on a local ComfyUI's
    answer are the step's to refuse), or a paid part is over a cap, with the
    numbers."""
    try:
        script, board = assets_step.require_approved(ec)
        units = assets_step.asset_units(ec, script, board, env=env, animate=animate)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None
    refusal = assets_step.plan_refusal(ec, units, unprobed=True)
    if refusal is not None:
        raise WorkflowError(CONFLICT, refusal)


def read_episode(stories, story_id, ep, name):
    """One of an episode's documents (``SCRIPT_DOC``, ``STORYBOARD_DOC``) as
    the store validates it, or None; ``StoryUnreadable`` for one that does
    not validate or an episode folder that is not a real directory."""
    try:
        return stories.read_episode_doc(story_id, ep, name)
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None
    except KeyError:
        label = (f"{story_store.STORIES_DIRNAME}/{story_id}/{story_store.EPISODES_DIRNAME}/ep{ep:02d}/"
                 f"{name}")
        raise StoryUnreadable(story_id, label, ["its folder is not a real directory; it is never followed"]) from None


def _no_script(ep) -> WorkflowError:
    return WorkflowError(CONFLICT, f"Episode {ep} has no script yet: write it first (the script step).")


def _no_storyboard(ep) -> WorkflowError:
    return WorkflowError(CONFLICT, f"Episode {ep} has no storyboard yet: plan its shots first (the storyboard step).")


def _write(write, what, *args, now, code=INVALID):
    """``episode_common.write_script``/``write_storyboard``: a document the
    rules refuse is *code* with ``{"message", "errors"}``; one the store may
    not write (a symlink in its place) a ``conflict``."""
    try:
        return write(*args, now=now)
    except schemas.SchemaError as exc:
        raise WorkflowError(code, {"message": f"The {what} would not be valid with these values.",
                                   "errors": list(exc.errors)}) from None
    except (KeyError, ValueError) as exc:
        raise WorkflowError(CONFLICT, f"The {what} cannot be written: {exc}.") from None


def _entities(stories, story_id) -> dict:
    """``{"characters": {id: doc}, "places": {...}, "props": {...}}``."""
    return {kind: {doc[story_store.ENTITY_KINDS[kind].id_field]: doc for doc in list_entities(stories, story_id, kind)}
            for kind in (CHARACTERS, PLACES, PROPS)}


# ---------------------------------------------------------------- the story

def episodes_with_script(stories, story_id) -> list:
    """The episodes that have a ``script.json`` (one that cannot be read
    counts: it is there)."""
    found = []
    try:
        numbers = stories.list_episodes(story_id)
    except KeyError:
        raise not_found() from None
    for ep in numbers:
        try:
            doc = stories.read_episode_doc(story_id, ep, SCRIPT_DOC)
        except (KeyError, schemas.SchemaError):
            found.append(ep)
            continue
        if doc is not None:
            found.append(ep)
    return found


def check_episode_template(stories, story, template_id) -> None:
    """The story's episode length may become *template_id*: one of the
    shipped templates (``invalid``), and -- unless it is the one the story
    has -- only while no episode has a script (``conflict``): an episode
    keeps the template it was written against."""
    shipped = defaults.EPISODE_TEMPLATE_IDS
    if template_id not in shipped:
        raise WorkflowError(INVALID, f"episode_template_id must be one of {', '.join(shipped)}, not {template_id!r}.")
    if template_id == story.get("episode_template_id"):
        return
    written = episodes_with_script(stories, story["story_id"])
    if written:
        raise WorkflowError(CONFLICT, (f"The episode length cannot change once an episode is written: episode "
                                       f"{written[0]} has a script, and each episode keeps the template it was "
                                       "written against."))


# ------------------------------------------------------------------- state

def script_missing(script, ep) -> list:
    """What the script step would still write, in its order:
    ``["beat_sheet"]`` without scenes; else each body scene still a stub, each
    framing part not written (``recap``, ``hook``, ``cliffhanger``,
    ``teaser``), and ``consistency_check`` when the report is missing, stale
    or of an older revision."""
    if not script or not script["scenes"]:
        return ["beat_sheet"]
    missing = [scene["scene_id"] for scene in script_step.body_scenes(script) if scene["state"] == "stub"]
    missing += script_step.missing_parts(script, ep)
    if script_step.needs_check(script):
        missing.append("consistency_check")
    return missing


def script_state(script, ep) -> str:
    """``none`` | ``writing`` | ``complete`` | ``approved``."""
    if not script or not script["scenes"]:
        return "none"
    if script["approved_at"]:
        return "approved"
    return "complete" if script_step.is_complete(script, ep) else "writing"


def storyboard_state(storyboard, script) -> str:
    """``none`` | ``partial`` (a scene without shots, or planned from an
    older version of its scene) | ``complete`` | ``approved``."""
    if not storyboard or not storyboard["shots"]:
        return "none"
    if storyboard["approved_at"]:
        return "approved"
    if script and episode_common.covers(storyboard, script) and not storyboard_step.stale_scenes(storyboard, script):
        return "complete"
    return "partial"


def report_state(script) -> str:
    """``none`` | ``stale`` (stale, or of an older revision) | ``passed`` |
    ``issues``."""
    report = (script or {}).get("consistency_report")
    if report is None:
        return "none"
    if script_step.needs_check(script):
        return "stale"
    return "passed" if report["passed"] else "issues"


def outdated_entities(storyboard, entities) -> list:
    """The entities the storyboard's prompts were resolved from that changed
    since (their ``updated_at`` moved: their names) or are gone (their ids),
    in ``resolved_from``'s order."""
    current = {}
    for docs in entities.values():
        current.update(docs)
    outdated = []
    for eid, stamp in storyboard["resolved_from"].items():
        doc = current.get(eid)
        if doc is None:
            outdated.append(eid)
        elif doc.get("updated_at") != stamp:
            outdated.append(doc["name"])
    return outdated


def _template_view(story, script) -> dict:
    template_id = script["template_id"] if script else story["episode_template_id"]
    try:
        template = templates.load_episode_template(template_id)
    except (KeyError, OSError, schemas.SchemaError) as exc:
        raise StoryUnreadable(story["story_id"], f"templates/episodes/{template_id}.json", [str(exc)]) from None
    return {"id": template["template_id"], "window_s": list(template["window_s"]), "target_s": template["target_s"],
            "tighten_above_s": template["tighten_above_s"]}


def _regenerate_blocked(ec, precondition) -> str | None:
    """*precondition* (``assets.require_approved`` / ``metadata.
    require_render``) applied to *ec* right now: its own refusal sentence,
    single-sourced for the dashboard's regenerate controls (F8, phase 5
    stage 13b) instead of a copied string that can drift from it -- or None
    when it would not refuse."""
    try:
        precondition(ec)
    except StepFailed as exc:
        return str(exc)
    return None


def episode_view(stories, story, ep) -> dict:
    """What the episode page shows (the web layer adds the episode's jobs)::

        {"ep", "script": script.json | null, "storyboard": storyboard.json | null,
         "template": {"id", "window_s", "target_s", "tighten_above_s"},
         "state": {"script": none|writing|complete|approved,
                   "storyboard": none|partial|complete|approved,
                   "report": none|passed|issues|stale,
                   "stale_scenes": [scene_id, ...], "prompts_outdated": bool,
                   "missing": [what the script step would still write],
                   "assets_regenerate_blocked": str | null,
                   "metadata_regenerate_blocked": str | null}}

    ``template`` is the one the script was written against, else the
    story's choice. ``assets_regenerate_blocked``/``metadata_regenerate_
    blocked`` (F8, phase 5 stage 13b) are ``assets.require_approved``'s /
    ``metadata.require_render``'s own refusal sentence right now -- what a
    line's re-voice, a shot's image regenerate or a platform's metadata
    regenerate would 409 with -- or None while neither would refuse; also
    None without a script yet (those controls are not shown then) or when
    the episode's context itself does not read (a style not locked, an
    unready story: a conflict of a different kind, shown elsewhere on the
    page). Calls nothing else; ``StoryUnreadable`` for a document that does
    not validate."""
    story_id = story["story_id"]
    script = read_episode(stories, story_id, ep, SCRIPT_DOC)
    board = read_episode(stories, story_id, ep, STORYBOARD_DOC)
    outdated = outdated_entities(board, _entities(stories, story_id)) if board else []
    assets_blocked = metadata_blocked = None
    if script is not None and script["scenes"]:
        try:
            ec = _context(stories, story_id, ep)
        except WorkflowError:
            ec = None
        if ec is not None:
            assets_blocked = _regenerate_blocked(ec, assets_step.require_approved)
            metadata_blocked = _regenerate_blocked(ec, metadata_step.require_render)
    return {
        "ep": ep, "script": script, "storyboard": board, "template": _template_view(story, script),
        "state": {
            "script": script_state(script, ep),
            "storyboard": storyboard_state(board, script),
            "report": report_state(script),
            "stale_scenes": sorted(storyboard_step.stale_scenes(board, script)) if board and script else [],
            "prompts_outdated": bool(outdated),
            "missing": script_missing(script, ep),
            "assets_regenerate_blocked": assets_blocked,
            "metadata_regenerate_blocked": metadata_blocked,
        },
    }


def episode_summaries(stories, story) -> list:
    """One entry per episode folder of the story (the story page)::

        [{"ep", "title", "script_state", "storyboard_state", "total_s", "timing_state"}]

    An episode whose documents cannot be read is listed with both states
    ``unreadable`` (its own page says why) rather than failing the story's."""
    story_id = story["story_id"]
    try:
        numbers = stories.list_episodes(story_id)
    except KeyError:
        raise not_found() from None
    summaries = []
    for ep in numbers:
        try:
            script = read_episode(stories, story_id, ep, SCRIPT_DOC)
            board = read_episode(stories, story_id, ep, STORYBOARD_DOC)
        except StoryUnreadable:
            summaries.append({"ep": ep, "title": None, "script_state": "unreadable",
                              "storyboard_state": "unreadable", "total_s": None, "timing_state": None})
            continue
        result = (script or {}).get("timing") or {}
        summaries.append({
            "ep": ep, "title": (script or {}).get("title"), "script_state": script_state(script, ep),
            "storyboard_state": storyboard_state(board, script), "total_s": result.get("total_s"),
            "timing_state": result.get("state"),
        })
    return summaries


# ------------------------------------------------------ the page, phase 4

# What the phase-4 part of the episode page derives by hashing files -- each
# shot's image state (its prompt hash; in ``references`` mode, its reference
# images), the assets approval's state (every image and audio file) and
# whether the last render is still the one the step would make
# (``render.current_render``: every input and the final file) -- is
# remembered per (outputs root, story, episode) under a key of what it is
# derived from: the episode documents' ``updated_at`` (the manifest's
# included), the story's, the style lock's and every entity's, and the
# (size, mtime) of each image, take, word sidecar, the final file and the
# manifest. The dashboard polls the page every 4 s while a job runs: nothing
# is hashed again until one of those moves.
_DERIVED_CACHE: dict = {}
_DERIVED_CACHE_MAX = 64
_DERIVED_LOCK = threading.Lock()


def _file_stamp(path):
    if not path:
        return None
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_size, info.st_mtime_ns)


def _store_path(call, *args):
    """A path from the store, or None where it refuses one (a symlink)."""
    try:
        return call(*args)
    except (KeyError, ValueError):
        return None


def _cache_files_stamp(cache_dir) -> tuple:
    """``((name, (size, mtime_ns) | None), ...)`` of a render's cache folder
    (``render/cache/``), sorted by name; ``()`` for no folder yet. Part of
    the "changes since last render" block's cache key (:func:`_derived_key`,
    stage 9), so ``render.render_changes`` -- which hashes those clips
    (``render/partial.py``) -- runs again only when one of them moved."""
    if not cache_dir:
        return ()
    try:
        names = sorted(os.listdir(cache_dir))
    except OSError:
        return ()
    return tuple((name, _file_stamp(os.path.join(cache_dir, name))) for name in names)


def _derived_key(ec, script, board, doc, manifest) -> tuple:
    story_id, ep = ec.story_id, ec.ep
    lines = [line for scene in (script or {}).get("scenes") or [] for line in scene["lines"]]
    files = [_file_stamp(assets_step.shot_image_path(ec, shot)) for shot in (board or {}).get("shots") or []]
    for line in lines:
        files.append(_file_stamp(assets_step.line_audio_path(ec, line)))
        files.append(_file_stamp(assets_step.sidecar_path(ec, line["line_id"])))
        # has_audio (below) reads these same two paths independently of the
        # script's own timing.audio pointer, so its own answer must move the
        # key too -- a stray file appearing, vanishing or being replaced.
        files.extend(_file_stamp(path) for path in voice_lines.audio_path_candidates(ec, line["line_id"]))
    files.append(_file_stamp(_store_path(ec.store.episode_file_path, story_id, ep, render_step.FINAL_FILE)))
    files.append(_file_stamp(_store_path(ec.store.episode_doc_path, story_id, ep, MANIFEST_DOC)))
    entities = tuple(sorted((kind, eid, entity.get("updated_at"))
                            for kind, docs in ec.entities.items() for eid, entity in docs.items()))
    stamps = tuple((current or {}).get("updated_at") for current in (script, board, doc, manifest))
    lock = (ec.style_lock.get("locked_at"), ec.style_lock.get("updated_at"))
    # Stage 9: the reedit block below hashes the render cache's clips
    # (render.render_changes); its own stamp -- the last good render's file
    # and the cache folder's own files -- keeps that from running again on
    # every request when nothing moved.
    known = render_step.last_render(ec)
    last_good_stamp = _file_stamp(_store_path(ec.store.episode_doc_path, story_id, ep, render_step.LAST_GOOD_DOC))
    cache_stamp = _cache_files_stamp(known["cache_dir"])
    return (stamps, ec.story.get("updated_at"), lock, ec.consistency_mode, entities, tuple(files),
            last_good_stamp, cache_stamp)


def _derive(ec, script, board, doc, manifest) -> dict:
    """The hashed part of :func:`episode_outputs` (see ``_DERIVED_CACHE``)."""
    link = assets_step.recorded_image_link(doc)
    shots = {shot["shot_id"]: assets_step.shot_state(ec, shot, link=link)
             for shot in (board or {}).get("shots") or []}
    lines = {}
    for scene in (script or {}).get("scenes") or []:
        for line in scene["lines"]:
            voiced = voice_lines.is_measured(ec, line)
            source, aligned_by = (wordtiming.source_of(assets_step.read_sidecar(ec, line["line_id"])) if voiced
                                  else (None, None))
            lines[line["line_id"]] = {"voiced": voiced, "words_source": source, "aligned_by": aligned_by,
                                       "has_audio": voice_lines.has_audio(ec, line["line_id"])}
    out_of_date = None
    if manifest is not None and manifest.get("output"):
        params = manifest["params"]
        out_of_date = not render_step.current_render(ec, {"subtitles": params["subtitles"],
                                                          "encoder": params["encoder"]})
    reedit = None
    if render_step.last_render(ec)["baseline"] is not None:
        try:
            changes = render_step.render_changes(ec, None)
        except StepFailed as exc:
            # Stage 9: the episode page never 500s over this -- a StepFailed
            # ("cannot be rendered") becomes the block's own sentence.
            reedit = {"blocked": str(exc)}
        else:
            reedit = {
                "blocked": None, "current": changes["current"], "shots_total": changes["shots_total"],
                "rebuild": changes["rebuild"], "reuse": changes["reuse"], "reasons": changes["reasons"],
                "end_card": changes["end_card"], "timing_converted": changes["timing_converted"],
                "summary": changes["summary"], "stages": changes["stages"], "inputs": changes["inputs"],
            }
    return {"shots": shots, "lines": lines, "fingerprint": assets_approval_state(ec, board, script, doc),
            "out_of_date": out_of_date, "reedit": reedit}


def _derived(stories, ec, script, board, doc, manifest) -> dict:
    """:func:`_derive`, remembered while its key holds."""
    where = (os.path.realpath(stories.root), ec.story_id, ec.ep)
    key = _derived_key(ec, script, board, doc, manifest)
    with _DERIVED_LOCK:
        found = _DERIVED_CACHE.get(where)
    if found is not None and found[0] == key:
        return found[1]
    value = _derive(ec, script, board, doc, manifest)
    with _DERIVED_LOCK:
        _DERIVED_CACHE.pop(where, None)
        _DERIVED_CACHE[where] = (key, value)
        while len(_DERIVED_CACHE) > _DERIVED_CACHE_MAX:
            _DERIVED_CACHE.pop(next(iter(_DERIVED_CACHE)))
    return value


def _assets_view(ec, script, board, doc, derived) -> dict:
    ep = ec.ep
    shots = []
    for shot in (board or {}).get("shots") or []:
        assets = shot["assets"]
        state = derived["shots"][shot["shot_id"]]
        on_disk = assets_step.shot_image_path(ec, shot) is not None
        shots.append({
            "shot_id": shot["shot_id"], "scene_id": shot["scene_id"], "state": state,
            "image_name": assets["image"].rpartition("/")[2] if assets.get("image") and on_disk else None,
            "route": assets.get("route"), "consistency": assets.get("consistency"),
            "provider": assets.get("provider"), "model": assets.get("model"), "seed": assets.get("seed"),
            "note": assets.get("note"), "est_usd": assets.get("est_usd"), "generated_at": assets.get("generated_at"),
            "locked": bool(assets.get("locked")), "approved": bool(assets.get("approved")),
            "pending": bool(assets.get("pending")), "target": assets_step.shot_target(ep, shot["shot_id"]),
        })
    lines = []
    for scene in (script or {}).get("scenes") or []:
        for line in scene["lines"]:
            known = derived["lines"][line["line_id"]]
            entry = ((doc or {}).get("lines") or {}).get(line["line_id"]) or {}
            pending, take = entry.get("pending"), entry.get("take")
            lines.append({
                "line_id": line["line_id"], "scene_id": scene["scene_id"], "speaker": line["speaker"],
                "voiced": known["voiced"], "voice": line["timing"].get("voice") if known["voiced"] else None,
                "words_source": known["words_source"], "aligned_by": known["aligned_by"],
                "approximate": known["words_source"] not in (wordtiming.PROVIDER, wordtiming.ALIGNMENT),
                # Phase 5 stage 7's take, and stage 9's own note (a pending
                # regenerate's, else the take's): what EpisodeStudio's
                # "Re-voice this line" needs (plan 11 stage 11). has_audio
                # (browser-check fix round 2): a line the plain assets step
                # voiced and a later text edit made stale (voiced false) but
                # never regenerated (take still null) still has its old
                # audio file on disk -- without this, the dashboard cannot
                # tell that line apart from one never voiced at all.
                "take": take, "note": (pending or take or {}).get("note"), "pending": bool(pending),
                "has_audio": known["has_audio"], "target": assets_step.line_target(ep, line["line_id"]),
            })
    approved = (doc or {}).get("approved")
    return {"doc": doc, "consistency": ec.consistency_mode, "fingerprint": derived["fingerprint"],
            "approved_at": approved["at"] if approved else None, "shots": shots, "lines": lines}


def _render_view(manifest, derived) -> dict:
    output = manifest.get("output")
    stages = manifest["stages"]
    states = {stage["state"] for stage in stages}
    if output:
        state = "completed"
    elif "failed" in states:
        state = "failed"
    elif "cancelled" in states:
        state = "cancelled"
    else:
        state = "incomplete"
    return {
        "state": state, "profile": manifest["profile"], "params": dict(manifest["params"]),
        "duration_s": output["duration_s"] if output else None,
        "loudness": dict(output["loudness"]) if output else None,
        "fps": output["fps"] if output else None,
        "width": output["width"] if output else None, "height": output["height"] if output else None,
        "output": {"file": render_step.FINAL_FILE, "sha256": output["sha256"]} if output else None,
        "stages": {"total": len(stages), "ran": sum(stage["state"] == "done" for stage in stages),
                   "cached": sum(stage["state"] == "cached" for stage in stages),
                   "shots": sum(stage["kind"] == "shot" for stage in stages),
                   "shots_cached": sum(stage["kind"] == "shot" and stage["state"] == "cached" for stage in stages)},
        "seconds": manifest["timings"]["total_s"], "started_at": manifest["timings"]["started_at"],
        "finished_at": manifest["timings"]["finished_at"], "warnings": list(manifest["warnings"]),
        "ffmpeg": dict(manifest["ffmpeg"]), "out_of_date": derived["out_of_date"],
        "reuse": render_step.reuse_view(manifest),
        "changes": derived["reedit"],
    }


def episode_ledger(stories, story_id, ep) -> dict:
    """``{"entries": [the story ledger's rows of episode *ep*], "totals":
    {"est_usd", "paid_usd", "entries"}}`` (``CostLedger.totals(ep)``); empty
    while the story has no ledger. A symlink in its place is not followed."""
    try:
        path = os.path.join(stories.story_dir(story_id), COST_LEDGER)
    except KeyError:
        raise not_found() from None
    if os.path.islink(path) or not os.path.isfile(path):
        return {"entries": [], "totals": {"est_usd": 0.0, "paid_usd": 0.0, "entries": 0}}
    ledger = CostLedger(path)
    try:
        return {"entries": [row for row in ledger.entries() if row.get("ep") == ep], "totals": ledger.totals(ep)}
    except (KeyError, TypeError, ValueError) as exc:
        raise StoryUnreadable(story_id, f"{story_store.STORIES_DIRNAME}/{story_id}/{COST_LEDGER}",
                              [f"{type(exc).__name__}: {exc}"]) from None


def episode_outputs(stories, story, ep) -> dict:
    """The phase-4 part of the episode page (:func:`episode_view` is the
    rest; the web layer adds the jobs and, stage 12, the media URLs)::

        {"assets": {"doc": assets.json | null, "consistency": prompt_only|references,
                    "fingerprint": none|current|stale, "approved_at": ts | null,
                    "shots": [{shot_id, scene_id, state: none|current|stale|locked_stale|failed,
                               image_name: "shot_NN.<ext>" | null (GET .../shots/{image_name}),
                               route: free|local|paid|null, consistency, provider, model, seed, note,
                               est_usd, generated_at, locked, approved, pending, target: "shot:<ep>:<shid>"}],
                    "lines": [{line_id, scene_id, speaker, voiced, voice, words_source:
                               provider|alignment|even_split|null, aligned_by, approximate,
                               take: {id, note, audio_sha256} | null, note, pending: bool,
                               has_audio: bool, target: "line:<ep>:<lid>"}]} | null,
         "render": {"state": completed|failed|cancelled|incomplete, "profile", "params": {subtitles, encoder},
                    "duration_s", "loudness": {i, tp, lra}, "fps", "width", "height",
                    "output": {"file": "episode_final.mp4", "sha256"} | null,
                    "stages": {total, ran, cached, shots, shots_cached},
                    "seconds", "started_at", "finished_at", "warnings": [...], "ffmpeg": {version, machine},
                    "out_of_date": bool | null,
                    "reuse": {baseline_output_sha256, shots_total, shots_rebuilt, shots_reused, reasons,
                              timing_converted, summary: "3 of 11 shots re-rendered"} | null,
                    "changes": {"blocked": str} | {"blocked": null, "current", "shots_total",
                                "rebuild": [shot ids], "reuse": [shot ids], "reasons", "end_card",
                                "timing_converted", "summary", "stages", "inputs"} | null} | null,
         "metadata": {"pack": metadata_pack.json, "current": bool} | null,
         "ledger": {"entries": [...], "totals": {est_usd, paid_usd, entries}}}

    ``assets`` is null before the episode has a script or a storyboard (a
    line's ``take`` is stage 7's persisted take, ``note`` its own or a
    pending regenerate's, whichever is newer, ``has_audio`` whether a
    synthesised file for it exists on disk at all, current text or not
    (``voice_lines.has_audio``) -- together what EpisodeStudio's "Re-voice
    this line" needs to read even a line the plain assets step voiced and a
    later text edit made stale, never regenerated: plan 11 stage 11, its
    browser-check fix round 2); ``render`` before a
    manifest (``out_of_date`` null while the render has no output; else
    whether rendering again with the same params would make another file:
    ``render.current_render``, so an ``encoder: auto`` render is always out
    of date; ``reuse`` the manifest's record of what the render made again
    since the last good render, phase 5 stage 8, null for a render with none
    before it; ``changes`` -- stage 9 -- the dry run of a render right now,
    ``render.render_changes(ec, None)``, null before a finished render to
    compare with, ``blocked`` naming why the episode cannot be rendered
    right now instead of a 500 when it cannot); ``metadata`` before a pack
    (``current``: ``metadata.is_current`` against the render's recorded
    output). Calls nothing; what it hashes -- ``changes`` hashes the render
    cache's clips too -- is remembered while nothing it reads moves
    (``_DERIVED_CACHE``). ``StoryUnreadable`` for a document that does not
    validate, or an episode whose context cannot be read."""
    story_id = story["story_id"]
    script = read_episode(stories, story_id, ep, SCRIPT_DOC)
    board = read_episode(stories, story_id, ep, STORYBOARD_DOC)
    doc = read_episode(stories, story_id, ep, ASSETS_DOC)
    manifest = read_episode(stories, story_id, ep, MANIFEST_DOC)
    pack = read_episode(stories, story_id, ep, METADATA_PACK_DOC)
    view = {"assets": None, "render": None, "metadata": None, "ledger": episode_ledger(stories, story_id, ep)}
    if not any(current is not None for current in (script, board, doc, manifest)):
        if pack is not None:
            view["metadata"] = {"pack": pack, "current": False}
        return view
    try:
        ec = _context(stories, story_id, ep)
    except WorkflowError as exc:
        raise StoryUnreadable(story_id, f"{story_store.STORIES_DIRNAME}/{story_id}/{story_store.EPISODES_DIRNAME}/"
                                        f"ep{ep:02d}", [exc.detail]) from None
    derived = _derived(stories, ec, script, board, doc, manifest)
    if script is not None or board is not None:
        view["assets"] = _assets_view(ec, script, board, doc, derived)
    if manifest is not None:
        view["render"] = _render_view(manifest, derived)
    if pack is not None:
        output = (manifest or {}).get("output")
        current = bool(script and output and metadata_step.is_current(pack, script, output["sha256"]))
        view["metadata"] = {"pack": pack, "current": current}
    return view


# ------------------------------------------------------ the page, phase 6

# What the clip part of the episode page (:func:`episode_clips`) derives --
# each image hashed to tell a clip current, the journal read for a request
# the provider holds, the assets estimate's video part -- is remembered the
# same way as ``_DERIVED_CACHE``, under a key that also holds what the
# estimate reads: the clips' files, the generation journal's files, the
# story's ledger, the day's spend, the measured clip timings, the Settings
# values (hashed) and the UTC day.
_CLIPS_CACHE: dict = {}

# The fields of ``asset_units()["video"]`` the page carries.
_VIDEO_FIELDS = ("tier", "budget_profile", "route", "mode", "route_class", "link", "source", "template", "profile",
                 "price_per_second", "plan", "still", "count", "seconds", "est_usd", "eta_s", "eta_note", "refused",
                 "ready", "message")


def _clips_key(ec, script, board, doc, env) -> tuple:
    shots = (board or {}).get("shots") or []
    clip_files = tuple(_file_stamp(clips_step.shot_clip_path(ec, shot)) for shot in shots)
    journal = _cache_files_stamp(_store_path(ec.store.gen_cache_dir, ec.story_id))
    ledger = _file_stamp(_store_path(lambda: os.path.join(ec.store.story_dir(ec.story_id), COST_LEDGER)))
    merged = gating.merged_env(env)
    settings = hashlib.sha256(json.dumps(sorted(merged.items()), ensure_ascii=False).encode("utf-8")).hexdigest()
    return (_derived_key(ec, script, board, doc, None), clip_files, journal, ledger, settings,
            _file_stamp(budget_mod.default_spend_path()), _file_stamp(gen_timings.default_timings_path()),
            time.strftime("%Y-%m-%d", time.gmtime()))


def _shot_clip(ec, script, shot, doc, *, link, tier, image_sha) -> dict:
    """One shot's clip on the page (see :func:`episode_clips`)."""
    ep, shot_id = ec.ep, shot["shot_id"]
    record = shot["assets"].get("clip") or {}
    flags = clips_step.shot_flags(shot, doc)
    state = "none"
    if record:
        state = clips_step.clip_state(ec, shot, script, link=link, tier=tier, flags=flags, image_sha=image_sha)
    keys = [record.get("cache_key")]
    if record.get("pending"):
        keys += assets_step.pending_clip_keys(ec, script, shot, doc, link=link)
    held = assets_step.open_clip_request(ec, keys)
    target = assets_step.clip_target(ep, shot_id)
    if held is not None:
        blocked = f"Cannot regenerate '{target}': {assets_step.still_generating(shot, held)}"
    else:
        reason = assets_step.clip_target_refusal(ec, shot, doc=doc)
        blocked = f"Cannot regenerate '{target}': {reason}" if reason else None
    return {
        "state": state, "link": record.get("link"), "route": record.get("route"), "clip_s": record.get("clip_s"),
        "est_usd": record.get("est_usd"), "generated_at": record.get("generated_at"),
        "name": clips_step.clip_name(shot_id) if clips_step.shot_clip_path(ec, shot) is not None else None,
        "note": assets_step.clip_note(shot), "reason": record.get("reason"), "pending": bool(record.get("pending")),
        "target": None if held is not None else target, "continue": held is not None, "blocked": blocked,
        "flags": dict(flags), "overrides": dict(video_plan.shot_overrides(doc, shot_id) or {}),
    }


def _image_offer_view(ec, script, board, *, env):
    """The sticky image-link offer (phase 6 stage 12 follow-up: A-087's
    switch applies at tier 1 too, unlike the video one which only exists
    once there are clips): ``assets_step.asset_units``'s own
    ``images.sticky.gone`` (:func:`assets_step.link_gone`), calling nothing
    -- no ``probe_local`` (unlike the on-demand assets estimate, this is a
    page-load computation, read-only). None once every shot has its image
    (nothing left to be gone for) or on any refusal reading the episode."""
    try:
        units = assets_step.asset_units(ec, script or {"scenes": []}, board, env=env)
    except StepFailed:
        return None
    return assets_step.link_gone(units)


def _video_view(ec, script, board, doc, *, env):
    """The assets estimate's video part as the page shows it (see
    :func:`episode_clips`), or None while the script and a current
    storyboard are not approved (the estimate's own refusal is the page's
    ``assets_regenerate_blocked``)."""
    try:
        script, board = assets_step.require_approved(ec)
        units = assets_step.asset_units(ec, script, board, env=env)
    except StepFailed:
        return None
    video = units.get("video") or {}
    view = {name: copy.deepcopy(video.get(name)) for name in _VIDEO_FIELDS}
    try:
        render_step.shot_clips(ec, script, board, doc or {}, fill_failed=False)
        view["render_blocked"] = None
    except StepFailed as exc:
        view["render_blocked"] = str(exc)
    view["offer"] = None
    if video.get("source") == "record" and video.get("refused") and not video.get("plan") and video.get("link"):
        view["offer"] = assets_step.video_offer(ec, board, video["link"], why=video["refused"], env=env).as_dict()
    return view


def _derive_clips(ec, script, board, doc, *, env) -> dict:
    tier = clips_step.tier_of(ec)
    link = (sticky_link.recorded(doc, sticky_link.VIDEO) or {}).get("link")
    shots = {}
    for shot in board["shots"]:
        image = assets_step.shot_image_path(ec, shot)
        sha = assets_step._sha256_file(image) if image is not None and shot["assets"].get("clip") else None
        shots[shot["shot_id"]] = _shot_clip(ec, script, shot, doc, link=link, tier=tier, image_sha=sha)
    return {"shots": shots, "video": _video_view(ec, script, board, doc, env=env)}


def episode_clips(stories, story, ep, *, env=None) -> dict:
    """The clip part of the episode page (phase 6 stage 11; the web layer
    merges it into the page's ``assets``)::

        {"tier": 1 | 2 | 3,
         "links": {"image": {link, since, switched_from?} | None, "video": {...} | None},
         "shots": {shot_id: {"state": none|current|stale|failed, "link", "route": local|paid|None,
                             "clip_s", "est_usd", "generated_at", "name": "shot_NN.mp4" | None,
                             "note", "reason", "pending": bool, "target": "shot:<ep>:<shid>:video" | None,
                             "continue": bool, "blocked": sentence | None,
                             "flags": {keep_still, animate, keep_native_audio}, "overrides": {...}}},
         "image_offer": <the sticky image offer> | None,
         "video": {<the assets estimate's video part: tier, budget_profile, route, mode, route_class, link,
                    source, template, profile, price_per_second, plan, still, count, seconds, est_usd, eta_s,
                    eta_note, refused, ready, message>,
                   "render_blocked": sentence | None, "offer": <the sticky video offer> | None} | None}

    ``links`` is ``assets.json``'s (A-087). At tier 1, or before a
    storyboard, ``shots`` is empty and ``video`` None: nothing is hashed.
    ``image_offer`` is computed at **any** tier once the episode has a
    storyboard (A-087's image stickiness is not a tier >= 2 thing): the
    stop-and-ask of a recorded image link that cannot serve any more shot
    still to make (``assets_step.asset_units``'s own ``images.sticky.gone``),
    None while every shot has its image or the link is fine; its ``switch``
    is the assets edit that takes the next link, the same shape the video
    offer's ``switch`` already uses. At tier >= 2, per shot: ``state`` is
    ``clips.clip_state`` on the episode's video link (``none`` without a
    record); ``name`` the clip file on disk, served by ``GET
    /episodes/{ep}/clips/{name}``; ``continue`` -- the provider holds its
    request (the record's, or a pending re-animate's): only Continue
    collects it, so ``target`` is None; ``blocked`` the F8 pattern -- the
    409 a re-animate would answer now (``Cannot regenerate '<target>':
    ...``), None while it may run; ``flags`` the effective overrides
    (``overrides`` the ones ``assets.json`` sets). ``video``: the assets
    estimate's video part on the story's route (*env* -- the Settings
    values; a local ComfyUI is not asked), ``render_blocked`` the render's
    clip refusal while ``fill_failed_with_motion`` is off, ``offer`` the
    stop-and-ask of a recorded video link that cannot serve (its ``switch``
    is the assets edit that takes the next link); None while the script and
    a current storyboard are not approved. Calls nothing; remembered while
    nothing it reads moves (``_CLIPS_CACHE`` -- ``image_offer`` is computed
    fresh every call, outside that cache, since it is cheap and now runs at
    every tier)."""
    story_id = story["story_id"]
    board = read_episode(stories, story_id, ep, STORYBOARD_DOC)
    doc = read_episode(stories, story_id, ep, ASSETS_DOC)
    tier = int(story["generation_profile"]["tier"])
    view = {"tier": tier, "links": {kind: copy.deepcopy(sticky_link.recorded(doc, kind)) for kind in sticky_link.KINDS},
            "shots": {}, "video": None, "image_offer": None}
    if board is None or not board["shots"]:
        return view
    script = read_episode(stories, story_id, ep, SCRIPT_DOC)
    try:
        ec = _context(stories, story_id, ep)
    except WorkflowError:
        return view
    # Any tier: the image link's own stickiness (A-087) applies at tier 1 too.
    view["image_offer"] = _image_offer_view(ec, script, board, env=env)
    if tier < 2:
        return view
    where = (os.path.realpath(stories.root), story_id, ep)
    key = _clips_key(ec, script, board, doc, env)
    with _DERIVED_LOCK:
        found = _CLIPS_CACHE.get(where)
    if found is not None and found[0] == key:
        value = found[1]
    else:
        value = _derive_clips(ec, script or {"scenes": []}, board, doc, env=env)
        with _DERIVED_LOCK:
            _CLIPS_CACHE.pop(where, None)
            _CLIPS_CACHE[where] = (key, value)
            while len(_CLIPS_CACHE) > _DERIVED_CACHE_MAX:
                _CLIPS_CACHE.pop(next(iter(_CLIPS_CACHE)))
    view.update(copy.deepcopy(value))
    return view


# ------------------------------------------------------------------ targets

def check_episode_target(stories, story, parsed) -> None:
    """An episode regenerate target (``regenerate.parse_target``'s tuple)
    checked against the story before a job exists, as the runner checks it
    (``episode_regenerate.run``): the episode's preconditions without the
    memory gate (:func:`episode_context`); then ``conflict`` without a script (or,
    for a shot, without a storyboard, or when the shot's scene was rewritten
    since it was planned); ``not_found`` for a scene, a framing scene or a
    shot the episode does not have.

    Phase 4: a shot's image (``shot_image``) or a line's voice (``line``) --
    ``not_found`` for a shot or a line the episode does not have; then
    ``conflict`` unless the script is approved and the storyboard approved
    and current (``assets.require_approved``), for a locked shot ("unlock it
    first") and for a line whose speaker has no pinned voice. A platform's
    metadata (``metadata``) -- ``conflict`` without a finished render
    (``metadata.require_render``) or with a pack written for another render
    or script (the metadata step writes every platform again).

    Phase 6: a shot's clip (``shot_video``) -- ``not_found`` for a shot the
    episode does not have; ``conflict`` unless the script and a current
    storyboard are approved, and (``assets.clip_target_refusal``) for a story
    below tier 2, a shot kept still or a keyframe that is not current."""
    kind, ep = parsed[0], parsed[1]
    episode_context(stories, story, ep, step="regenerate", require_memory=False)
    story_id = story["story_id"]
    script = read_episode(stories, story_id, ep, SCRIPT_DOC)
    if script is None or not script["scenes"]:
        raise _no_script(ep)
    if kind == "scene":
        if script_step.scene_of(script, parsed[2]) is None:
            ids = ", ".join(scene["scene_id"] for scene in script["scenes"])
            raise WorkflowError(NOT_FOUND, f"Episode {ep} has no scene {parsed[2]!r} (its scenes: {ids}).")
    elif kind in ("hook", "cliffhanger"):
        if script_step.framing_scene(script, kind) is None:
            raise WorkflowError(NOT_FOUND, f"Episode {ep} has no {kind} scene.")
    elif kind == "shot":
        board = read_episode(stories, story_id, ep, STORYBOARD_DOC)
        if board is None or not board["shots"]:
            raise _no_storyboard(ep)
        shot = next((s for s in board["shots"] if s["shot_id"] == parsed[2]), None)
        if shot is None:
            raise WorkflowError(NOT_FOUND, (f"Episode {ep}'s storyboard has no shot {parsed[2]!r} (it has sh01 to "
                                            f"sh{len(board['shots']):02d})."))
        if shot["scene_id"] in storyboard_step.stale_scenes(board, script):
            raise WorkflowError(CONFLICT, (f"Scene {shot['scene_id']} was rewritten since its shots were planned: "
                                           "plan it again first (the storyboard step)."))
    elif kind in (regenerate_step.SHOT_IMAGE_KIND, regenerate_step.LINE_KIND):
        _check_asset_target(stories, story_id, ep, parsed, script)
    elif kind == regenerate_step.SHOT_VIDEO_KIND:
        _check_clip_target(stories, story_id, ep, parsed)
    elif kind == regenerate_step.METADATA_KIND:
        ec = _context(stories, story_id, ep)
        try:
            script, _manifest, render_sha = metadata_step.require_render(ec)
        except StepFailed as exc:
            raise WorkflowError(CONFLICT, str(exc)) from None
        pack = read_episode(stories, story_id, ep, METADATA_PACK_DOC)
        if pack is not None and not metadata_step.is_current(pack, script, render_sha):
            raise WorkflowError(CONFLICT, (f"Episode {ep}'s metadata pack was written for another render or script: "
                                           "run the metadata step, which writes every platform again."))


def _check_asset_target(stories, story_id, ep, parsed, script) -> None:
    """:func:`check_episode_target` of ``shot:<ep>:<shid>`` (the image) and
    ``line:<ep>:<lid>`` (the voice), in the runners' order
    (``assets.regenerate_shot_image`` / ``regenerate_line_voice``)."""
    kind, what = parsed[0], parsed[2]
    shot = line = None
    if kind == regenerate_step.SHOT_IMAGE_KIND:
        board = read_episode(stories, story_id, ep, STORYBOARD_DOC)
        if board is None or not board["shots"]:
            raise _no_storyboard(ep)
        shot = next((s for s in board["shots"] if s["shot_id"] == what), None)
        if shot is None:
            raise WorkflowError(NOT_FOUND, (f"Episode {ep}'s storyboard has no shot {what!r} (it has sh01 to "
                                            f"sh{len(board['shots']):02d})."))
    else:
        line = next((ln for scene in script["scenes"] for ln in scene["lines"] if ln["line_id"] == what), None)
        if line is None:
            raise WorkflowError(NOT_FOUND, f"Episode {ep}'s script has no line {what!r}.")
    ec = _context(stories, story_id, ep)
    try:
        assets_step.require_approved(ec)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None
    if shot is not None and shot["assets"].get("locked"):
        raise WorkflowError(CONFLICT, f"Shot {what} is locked: unlock it first.")
    if line is not None and voices.voice_label(voice_lines.speaker_voice(ec, line["speaker"])) is None:
        who = "the narrator" if line["speaker"] == "narrator" else voice_lines.speaker_name(ec, line["speaker"])
        raise WorkflowError(CONFLICT, f"{voice_lines.no_voice_reason(ec, line['speaker'])}: pick a voice for {who} "
                                      "first.")


def _clip_shot(stories, story_id, ep, parsed):
    """``(ec, script, board, shot)`` of a ``shot:<ep>:<shid>:video`` target,
    checked as ``assets.regenerate_shot_clip`` checks it before any call."""
    board = read_episode(stories, story_id, ep, STORYBOARD_DOC)
    if board is None or not board["shots"]:
        raise _no_storyboard(ep)
    shot = next((s for s in board["shots"] if s["shot_id"] == parsed[2]), None)
    if shot is None:
        raise WorkflowError(NOT_FOUND, (f"Episode {ep}'s storyboard has no shot {parsed[2]!r} (it has sh01 to "
                                        f"sh{len(board['shots']):02d})."))
    ec = _context(stories, story_id, ep)
    try:
        script, board = assets_step.require_approved(ec)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None
    shot = next(s for s in board["shots"] if s["shot_id"] == parsed[2])
    reason = assets_step.clip_target_refusal(ec, shot)
    if reason is not None:
        raise WorkflowError(CONFLICT, f"Cannot regenerate 'shot:{ep}:{parsed[2]}:video': {reason}")
    return ec, script, board, shot


def _check_clip_target(stories, story_id, ep, parsed) -> None:
    """:func:`check_episode_target` of ``shot:<ep>:<shid>:video`` (phase 6
    stage 8)."""
    _clip_shot(stories, story_id, ep, parsed)


def regenerate_clip_estimate(stories, story, parsed, *, env, adapters=None, probe_local=False) -> dict:
    """What ``shot:<ep>:<shid>:video`` would cost and where it would run
    (``GET /estimate/regenerate?target=``), after its checks
    (:func:`check_episode_target`)::

        {"step": "regenerate", "target", "est_usd", "units": {"clips": 1, "seconds"},
         "route_class": "paid" | "local" | None, "link", "links", "ready", "message"}

    One clip's seconds times the price per second on the episode's video
    link -- its recorded ``links.video``, else the one the planner would take
    (``assets.clip_quote``) -- ``ready`` false when it cannot run now or
    would go over a cap. Calls nothing but, with *probe_local*, a local
    ComfyUI's status."""
    ep = parsed[1]
    episode_context(stories, story, ep, step="regenerate", require_memory=False)
    ec, script, board, shot = _clip_shot(stories, story["story_id"], ep, parsed)
    quote = assets_step.clip_quote(ec, script, board, shot, env=env, adapters=adapters, probe_local=probe_local)
    paid = quote["route_class"] == "paid"
    return {"step": "regenerate", "target": f"shot:{ep}:{shot['shot_id']}:video",
            "est_usd": quote["est_usd"] if paid else 0.0,
            "units": {"clips": 1, "seconds": quote["clip_s"]}, "route_class": quote["route_class"],
            "link": quote["link"], "links": quote["video"]["links"], "ready": quote["ready"],
            "message": quote["message"]}


# ---------------------------------------------------------------- approvals

def approve_script(stories, story_id, ep, *, approve_anyway=False, now) -> dict:
    """Approve episode *ep*'s script; returns it as written.

    ``conflict`` without a script; listing what is not written yet (every
    scene, the framing parts); without a consistency report, or with one that
    is stale or of an older revision (check it again: the script step); and,
    when the report found issues, listing them -- unless *approve_anyway*.
    ``approved_at`` becomes *now*; ``approved_anyway`` too when the approval
    went over issues. Nothing else moves: not the revision, not the report,
    not the story (RC-E2)."""
    story = load(stories, story_id)
    ep = episode_bounds(stories, story, ep)
    script = read_episode(stories, story_id, ep, SCRIPT_DOC)
    if script is None or not script["scenes"]:
        raise _no_script(ep)
    missing = [scene["scene_id"] for scene in script_step.body_scenes(script) if scene["state"] == "stub"]
    missing += [f"the {part}" for part in script_step.missing_parts(script, ep)]
    if missing:
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s script is not complete ({_and(missing)} not written yet): "
                                       "run the script step again to finish it."))
    report = script["consistency_report"]
    if report is None:
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s script has not been checked yet: run the script step "
                                       "again (it checks the script's consistency)."))
    if script_step.needs_check(script):
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s consistency check is out of date (the script changed "
                                       "since it ran): check it again (run the script step)."))
    over_issues = not report["passed"]
    if over_issues and not approve_anyway:
        issues = "; ".join(f"{issue['scene_id'] or 'the episode'} ({issue['kind']}): {issue['fix']}"
                           for issue in report["issues"])
        count = len(report["issues"])
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s consistency check found {count} issue"
                                       f"{'' if count == 1 else 's'}{': ' + issues if issues else ''}. Fix them and "
                                       "check again, or approve anyway."))
    ec = _context(stories, story_id, ep)
    script["approved_at"] = now
    script["approved_anyway"] = now if over_issues else None
    return _write(episode_common.write_script, "script", ec, script, now=now, code=CONFLICT)


def approve_storyboard(stories, story_id, ep, *, now) -> dict:
    """Approve episode *ep*'s storyboard; returns it as written.

    ``conflict`` without a storyboard; before its script is approved; while a
    scene of the script has no shots, or has shots planned from an older
    version of it (``scenes[sid].script_rev``, or marked stale); and while
    an entity its prompts were resolved from has changed since (refresh
    them). ``approved_at`` becomes *now*; nothing else moves."""
    story = load(stories, story_id)
    ep = episode_bounds(stories, story, ep)
    board = read_episode(stories, story_id, ep, STORYBOARD_DOC)
    if board is None or not board["shots"]:
        raise _no_storyboard(ep)
    script = read_episode(stories, story_id, ep, SCRIPT_DOC)
    if not script or not script["approved_at"]:
        raise WorkflowError(CONFLICT, f"Approve episode {ep}'s script first: its storyboard is approved on top of it.")
    planned = {shot["scene_id"] for shot in board["shots"]}
    unplanned = [scene["scene_id"] for scene in script["scenes"] if scene["scene_id"] not in planned]
    if unplanned:
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s storyboard has no shots for scene{_plural_s(unplanned)} "
                                       f"{_and(unplanned)}: plan {'it' if len(unplanned) == 1 else 'them'} "
                                       "(the storyboard step)."))
    if not episode_common.covers(board, script):
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s storyboard does not follow its script's scenes: plan its "
                                       "shots again (the storyboard step)."))
    stale = sorted(storyboard_step.stale_scenes(board, script))
    if stale:
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s storyboard has scene{_plural_s(stale)} {_and(stale)} "
                                       "planned from an older version of the script: plan "
                                       f"{'it' if len(stale) == 1 else 'them'} again (the storyboard step)."))
    ec = _context(stories, story_id, ep)
    outdated = outdated_entities(board, ec.entities)
    if outdated:
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s prompts are outdated: refresh them ({_and(outdated)} "
                                       f"changed since they were resolved)."))
    board["approved_at"] = now
    return _write(episode_common.write_storyboard, "storyboard", ec, board, script, now=now, code=CONFLICT)


def approve_assets(stories, story_id, ep, *, now) -> dict:
    """Approve episode *ep*'s assets (phase 4; plan "API": ``POST
    /approve/assets:<ep>``, and the fast track's auto-approval); returns
    ``assets.json`` as written.

    ``conflict`` before the script is approved and the storyboard approved
    and current (``assets.require_approved``, the step's own check); without
    an ``assets.json`` (run the assets step); while a shot has no image, or
    an unlocked shot's image is not current (``assets.shot_state``: stale,
    failed, none) -- a locked shot keeps the image it has; and while a line
    has no audio in its speaker's pinned voice (``voice_lines.is_measured``).
    Each refusal names the shots or lines and the regenerate target that
    finishes them. Then every shot's ``assets.approved`` is set (the
    storyboard is written, nothing else of it moves) and ``assets.json``
    gains ``approved: {at: now, fingerprint}`` -- the fingerprint of the
    files as they are now (``assets.current_fingerprint``); once it differs,
    the approval is stale, derived, never cleared (DEC-155). The script, the
    storyboard's own approval and the story are untouched (RC-E2)."""
    story = load(stories, story_id)
    ep = episode_bounds(stories, story, ep)
    ec = _context(stories, story_id, ep)
    try:
        script, board = assets_step.require_approved(ec)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None
    doc = read_episode(stories, story_id, ep, ASSETS_DOC)
    if doc is None:
        raise WorkflowError(CONFLICT, f"Episode {ep} has no assets yet: make them first (the assets step).")
    missing = []
    link = assets_step.recorded_image_link(doc)
    for shot in board["shots"]:
        state = assets_step.shot_state(ec, shot, link=link)
        imaged = assets_step.shot_image_path(ec, shot) is not None
        if not imaged or not (state == "current" or (shot["assets"].get("locked") and state == "locked_stale")):
            missing.append(shot["shot_id"])
    if missing:
        targets = [assets_step.shot_target(ep, shot_id) for shot_id in missing]
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s shot{_plural_s(missing)} {_and(missing)} "
                                       f"{'has' if len(missing) == 1 else 'have'} no current image: make "
                                       f"{'it' if len(missing) == 1 else 'them'} (the assets step, or regenerate "
                                       f"{_and(targets)}) or lock {'it' if len(missing) == 1 else 'them'}, then "
                                       "approve."))
    unvoiced = [line["line_id"] for scene in script["scenes"] for line in scene["lines"]
                if not voice_lines.is_measured(ec, line)]
    if unvoiced:
        targets = [assets_step.line_target(ep, line_id) for line_id in unvoiced]
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s line{_plural_s(unvoiced)} {_and(unvoiced)} "
                                       f"{'has' if len(unvoiced) == 1 else 'have'} no audio in the speaker's pinned "
                                       f"voice: speak {'it' if len(unvoiced) == 1 else 'them'} (the assets step, or "
                                       f"regenerate {_and(targets)}), then approve."))
    for shot in board["shots"]:
        shot["assets"]["approved"] = True
    _write(episode_common.write_storyboard, "storyboard", ec, board, script, now=now, code=CONFLICT)
    doc["approved"] = {"at": now, "fingerprint": assets_step.current_fingerprint(ec, board, script, doc)}
    try:
        return stories.write_episode_doc(story_id, ep, ASSETS_DOC, doc, now=now)
    except schemas.SchemaError as exc:
        raise WorkflowError(CONFLICT, {"message": "The assets would not be valid with this approval.",
                                       "errors": list(exc.errors)}) from None
    except (KeyError, ValueError) as exc:
        raise WorkflowError(CONFLICT, f"The assets cannot be written: {exc}.") from None


def assets_approval_state(ec, board, script, doc) -> str:
    """``none`` (no ``assets.json``, or never approved) | ``current`` (approved
    with the fingerprint of the files as they are now) | ``stale`` (an image,
    a voice, a sound or a lock changed since: derived, never cleared,
    DEC-155). Hashes every image and audio file."""
    approved = (doc or {}).get("approved")
    if not approved:
        return "none"
    if not board or not script:
        return "stale"
    current = assets_step.current_fingerprint(ec, board, script, doc) == approved["fingerprint"]
    return "current" if current else "stale"


def approved_episode_docs(stories, story_id, ep) -> list:
    """The episode documents whose approval stands now, of
    :data:`EPISODE_APPROVALS`: the script and the storyboard (their
    ``approved_at``), the assets (approved with a current fingerprint, not a
    stale one). What the web layer completes the older jobs of once the fast
    track approved them in-process (DEC-162)."""
    script = read_episode(stories, story_id, ep, SCRIPT_DOC)
    board = read_episode(stories, story_id, ep, STORYBOARD_DOC)
    doc = read_episode(stories, story_id, ep, ASSETS_DOC)
    approved = [name for name, current in (("script", script), ("storyboard", board))
                if current and current["approved_at"]]
    if (doc or {}).get("approved") and assets_approval_state(_context(stories, story_id, ep), board, script,
                                                             doc) == "current":
        approved.append("assets")
    return approved


# -------------------------------------------------------------------- edits

def _items(errors, fields, key, id_key, editable):
    """``[(path, item)]`` of the well-formed items of ``fields[key]`` (a list
    of objects naming what they edit by *id_key*, each key one of
    *editable*); the others become errors."""
    value = fields.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{key}: expected a list")
        return []
    found = []
    for i, item in enumerate(value):
        path = f"{key}[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: expected an object {{{id_key}, {', '.join(editable)}}}")
            continue
        extra = sorted(set(item) - {id_key} - set(editable))
        if extra:
            errors.append(f"{path}: unknown key(s) {', '.join(extra)} (editable: {', '.join(editable)})")
            continue
        found.append((path, item))
    return found


def _required_text(errors, path, value):
    """*value* without its surrounding spaces, or None (and an error) when it
    is not a non-empty text."""
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}: expected a non-empty text")
        return None
    return value.strip()


def _optional_text(value):
    """A text without its surrounding spaces, an empty one as null; any
    other value as sent (the schema names it)."""
    if isinstance(value, str):
        return value.strip() or None
    return value


def _unknown_fields(fields, editable, what) -> None:
    unknown = sorted(set(fields) - set(editable))
    if unknown:
        raise WorkflowError(INVALID, (f"These {what} fields cannot be edited: {', '.join(unknown)} "
                                      f"(editable: {', '.join(editable)})."))


def _edit_pays_off(ec, path, scene, value, errors) -> None:
    """A scene's new ``pays_off`` (phase 5 stage 7; stage 3 left no way to
    re-plan a payoff): a list of at most ``prompts.E1_PAYS_OFF_PER_SCENE``
    hooks, each one open when the episode starts
    (``series_memory.open_hooks_before``, verbatim: E1's own rule), null or
    an empty list for none -- stored as nothing, as E1 stores it. Whether a
    body scene still pays one off is the consistency check's question (the
    script step's pre-check, ``script.payoff_issues``), asked again since
    the edit stales it."""
    if value is None:
        value = []
    if not isinstance(value, list) or not all(isinstance(hook, str) for hook in value):
        errors.append(f"{path}.pays_off: expected a list of open hooks")
        return
    hooks = list(dict.fromkeys(value))
    most = prompts.E1_PAYS_OFF_PER_SCENE
    if len(hooks) > most:
        errors.append(f"{path}.pays_off: {len(hooks)} hooks, at most {most} a scene")
        return
    open_hooks = series_memory.open_hooks_before(ec.season, ec.ep) if ec.season else []
    shut = [hook for hook in hooks if hook not in open_hooks]
    for hook in shut:
        listed = "; ".join(open_hooks) if open_hooks else "none"
        errors.append(f"{path}.pays_off: {hook!r} is not a hook open before episode {ec.ep} (open: {listed})")
    if shut or hooks == (scene.get("pays_off") or []):
        return
    if hooks:
        scene["pays_off"] = hooks
    else:
        scene.pop("pays_off", None)


def _edit_script(ec, script, fields, errors) -> tuple:
    """*fields* into *script* (in place). Returns ``(touched, plan_kept,
    keep_approval)``: the scene ids whose content changed, in the script's
    order; ``{scene_id: retime}`` of those whose every change keeps their
    shot plan (phase 5 stage 7: a line's words or delivery, the scene's
    ``pays_off`` -- same line ids, speakers and emotions; *retime* when
    words moved); and whether every change of the edit was such a one (the
    storyboard's approval then stands, DEC-129 as amended). A speaker, an
    emotion, a summary, an on-screen text, the hook's text, the
    cliffhanger's reveal or the teaser clears it as before."""
    ep = ec.ep
    touched, structural, retime = set(), set(), set()
    other = False
    lines = {line["line_id"]: (scene, line) for scene in script["scenes"] for line in scene["lines"]}
    for path, item in _items(errors, fields, "lines", "line_id", SCRIPT_LINE_PATCH_FIELDS):
        found = lines.get(item.get("line_id"))
        if found is None:
            errors.append(f"{path}.line_id: {item.get('line_id')!r} is not a line of episode {ep}")
            continue
        scene, line = found
        before = dict(line)
        for key in SCRIPT_LINE_PATCH_FIELDS:
            if key not in item:
                continue
            value = item[key]
            if key == "text":
                value = _required_text(errors, f"{path}.text", value)
                if value is None:
                    continue
            elif key == "delivery" and isinstance(value, str):
                value = value.strip()
            line[key] = value
        # Other words, or another voice: the measured take no longer times
        # the line (its file stays on disk until it is measured again).
        if isinstance(line["text"], str) and (line["text"] != before["text"] or line["speaker"] != before["speaker"]):
            line["timing"] = timing.estimated_timing(line["text"], ec.language)
        if line != before:
            touched.add(scene["scene_id"])
            # The shots were planned from who speaks and how they feel
            # (shots.fast_plan's framings, T1's input): another speaker or
            # emotion re-plans the scene; other words only re-time it.
            if line["speaker"] != before["speaker"] or line["emotion"] != before["emotion"]:
                structural.add(scene["scene_id"])
            elif line["text"] != before["text"]:
                retime.add(scene["scene_id"])

    scenes = {scene["scene_id"]: scene for scene in script["scenes"]}
    for path, item in _items(errors, fields, "scenes", "scene_id", SCRIPT_SCENE_PATCH_FIELDS):
        scene = scenes.get(item.get("scene_id"))
        if scene is None:
            errors.append(f"{path}.scene_id: {item.get('scene_id')!r} is not a scene of episode {ep}")
            continue
        before = copy.deepcopy(scene)
        if "summary" in item:
            value = _required_text(errors, f"{path}.summary", item["summary"])
            if value is not None:
                scene["summary"] = value
        if "on_screen_text" in item:
            scene["on_screen_text"] = _optional_text(item["on_screen_text"])
        if (scene["summary"], scene["on_screen_text"]) != (before["summary"], before["on_screen_text"]):
            structural.add(scene["scene_id"])
        if "pays_off" in item:
            _edit_pays_off(ec, path, scene, item["pays_off"], errors)
        if scene != before:
            touched.add(scene["scene_id"])

    hook = script_step.framing_scene(script, "hook")
    cliff = script_step.framing_scene(script, "cliffhanger")
    if "hook_on_screen_text" in fields:
        value = _optional_text(fields["hook_on_screen_text"])
        if value != script["hook"]["on_screen_text"]:
            script["hook"]["on_screen_text"] = value
            other = True
            if hook is not None:
                touched.add(hook["scene_id"])
                structural.add(hook["scene_id"])
    if "cliffhanger_reveal" in fields:
        value = _required_text(errors, "cliffhanger_reveal", fields["cliffhanger_reveal"])
        if value is not None and value != script["cliffhanger"]["reveal"]:
            script["cliffhanger"]["reveal"] = value
            other = True
            if cliff is not None:
                script["cliffhanger"]["scene_id"] = cliff["scene_id"]
                touched.add(cliff["scene_id"])
                structural.add(cliff["scene_id"])
    if "next_episode_teaser" in fields:
        value = _required_text(errors, "next_episode_teaser", fields["next_episode_teaser"])
        if value is not None and value != script["next_episode_teaser"]:
            script["next_episode_teaser"] = value
            other = True
    ordered = [scene["scene_id"] for scene in script["scenes"] if scene["scene_id"] in touched]
    plan_kept = {sid: sid in retime for sid in ordered if sid not in structural}
    return ordered, plan_kept, not structural and not other


def patch_script(stories, story_id, ep, fields, *, now) -> dict:
    """Edit episode *ep*'s script (``fields``: ``SCRIPT_PATCH_FIELDS``);
    returns it as written.

    ``lines`` ``[{line_id, text?, speaker?, emotion?, delivery?}]``,
    ``scenes`` ``[{scene_id, summary?, on_screen_text?, pays_off?}]``,
    ``hook_on_screen_text`` (null clears it), ``cliffhanger_reveal`` and
    ``next_episode_teaser`` (texts). Checked as the steps check what they
    write -- the script's schema and rules (a speaker of the scene or the
    narrator, at most 22 words a line, a payoff one hook open before the
    episode, ...) and the story's -- and refused whole (``invalid`` with
    every error; nothing written). A line with other words or another
    speaker gets a fresh estimated timing (its measured take stays on disk).
    A change moves the revisions, clears the script's approval and stales
    the consistency report (``episode_common.mark_changed``); the script is
    re-timed; the storyboard is written before the script.

    The storyboard (phase 5 stage 7, DEC-129 as amended): a text-only edit
    -- a line's words or delivery, a scene's ``pays_off`` -- keeps its
    approval and every shot; a scene whose words changed is marked
    ``retime_only`` (planned from its new revision, re-timed in place once
    its lines are re-voiced). Any other change clears its approval and
    stales the scenes it changed, as before. Nothing sent, or nothing
    changed: nothing written. ``conflict`` without a script."""
    story = load(stories, story_id)
    ep = episode_bounds(stories, story, ep)
    _unknown_fields(fields, SCRIPT_PATCH_FIELDS, "script")
    script = read_episode(stories, story_id, ep, SCRIPT_DOC)
    if script is None or not script["scenes"]:
        raise _no_script(ep)
    if not fields:
        return script
    ec = _context(stories, story_id, ep)
    trial = copy.deepcopy(script)
    errors = []
    touched, plan_kept, keep_approval = _edit_script(ec, trial, fields, errors)
    message = "The script would not be valid with these values."
    if errors:
        raise _invalid_values(message, errors)
    if trial == script:
        return script
    for scene in trial["scenes"]:
        if scene["scene_id"] in touched:
            scene["source"] = "edit"
    errors = episode_common.trial_errors(ec, trial)
    if errors:
        raise _invalid_values(message, errors)

    board = read_episode(stories, story_id, ep, STORYBOARD_DOC)
    episode_common.mark_changed(trial, board, scene_ids=touched, now=now, plan_kept=plan_kept,
                                keep_approval=keep_approval)
    episode_common.retime(trial, ec, board)
    # The storyboard first: a failure between the two writes can only leave
    # an approval cleared too early (episode_regenerate's order).
    if board is not None:
        _write(episode_common.write_storyboard, "storyboard", ec, board, trial, now=now)
    return _write(episode_common.write_script, "script", ec, trial, now=now)


def _scene_tags(scene) -> list:
    return ([f"@{cid}" for cid in scene["characters"]] + [f"#{scene['place_id']}:{scene['time_variant']}"]
            + [f"%{pid}" for pid in scene["props"]])


def _stamp_resolved(resolved_from, shot, scene, entities) -> None:
    """Add what *shot* was just resolved from to ``resolved_from`` -- never
    over an older stamp: the other shots were resolved from that one, and
    the prompts stay outdated until they are all refreshed."""
    for tag in shot["subject_tags"]:
        kind, eid, _variant = shots.parse_tag(tag)
        doc = entities[_TAG_KINDS[kind]].get(eid)
        if doc is not None:
            resolved_from.setdefault(eid, doc["updated_at"])
    place = entities[PLACES].get(scene["place_id"])
    if place is not None:
        resolved_from.setdefault(scene["place_id"], place["updated_at"])


def _edit_action(ec, path, shot, scene, value, errors) -> None:
    """A shot's new action: T1's rules (at most 30 words, every tag it uses
    among the shot's subjects, no character named), every tag one of the
    scene's (one the shot did not show yet joins its subjects), and no place
    or prop named either."""
    action = _required_text(errors, f"{path}.action", value)
    if action is None:
        return
    allowed = _scene_tags(scene)
    used = list(dict.fromkeys(prompts._TAG_PATTERN.findall(action)))
    foreign = [tag for tag in used if tag not in allowed]
    for tag in foreign:
        errors.append(f"{path}.action: tag {tag!r} is not one of scene {scene['scene_id']}'s tags "
                      f"({', '.join(allowed)})")
    subjects = list(shot["subject_tags"]) + [tag for tag in used if tag in allowed and tag not in shot["subject_tags"]]
    names = {cid: doc["name"] for cid, doc in ec.entities[CHARACTERS].items()}
    before = len(errors)
    prompts._t1_shot_errors(errors, path, {"action": action, "subjects": subjects + foreign,
                                           "framing": shot["framing"]}, names=names, previous_framing=None)
    lowered = action.lower()
    for kind, word in ((PLACES, "place"), (PROPS, "prop")):
        for doc in ec.entities[kind].values():
            if re.search(rf"\b{re.escape(doc['name'].lower())}\b", lowered):
                errors.append(f"{path}.action: names the {word} {doc['name']!r} instead of using a tag")
    if not foreign and len(errors) == before:
        shot["action"] = action
        shot["subject_tags"] = subjects


def _edit_storyboard(ec, script, board, fields, errors) -> tuple:
    """*fields* into *board* (in place): the shots and transitions edited.
    Returns ``(to_resolve, retime)``: ``{shot_id: (path, camera motion sent
    or None, prompt)}`` of the shots to move and resolve again -- *prompt*
    when their framing, action or subjects changed (a motion swap alone
    keeps the prompt its image was made from, phase 5 stage 7) -- and
    whether a transition changed."""
    ep, lock = ec.ep, ec.style_lock
    scenes = {scene["scene_id"]: scene for scene in script["scenes"]}
    by_id = {shot["shot_id"]: shot for shot in board["shots"]}
    allowed_modifiers = list(lock["motion_rules"]["tier1"].get("modifiers") or [])
    to_resolve = {}
    for path, item in _items(errors, fields, "shots", "shot_id", STORYBOARD_SHOT_PATCH_FIELDS):
        shot = by_id.get(item.get("shot_id"))
        if shot is None:
            errors.append(f"{path}.shot_id: {item.get('shot_id')!r} is not a shot of episode {ep}")
            continue
        scene = scenes.get(shot["scene_id"])
        if scene is None:
            errors.append(f"{path}.shot_id: {shot['shot_id']!r} belongs to scene {shot['scene_id']}, which the "
                          "script no longer has: plan the shots again (the storyboard step)")
            continue
        before = (shot["framing"], shot["action"], list(shot["subject_tags"]))
        if "framing" in item:
            if item["framing"] not in schemas.FRAMINGS:
                errors.append(f"{path}.framing: {item['framing']!r} is not one of {', '.join(schemas.FRAMINGS)}")
            else:
                shot["framing"] = item["framing"]
        if "camera_motion" in item:
            if item["camera_motion"] not in schemas.CAMERA_MOTIONS:
                errors.append(f"{path}.camera_motion: {item['camera_motion']!r} is not one of "
                              f"{', '.join(schemas.CAMERA_MOTIONS)}")
            else:
                to_resolve[shot["shot_id"]] = (path, item["camera_motion"], False)
        if "modifiers" in item:
            value = item["modifiers"]
            if not isinstance(value, list) or not all(isinstance(m, str) for m in value):
                errors.append(f"{path}.modifiers: expected a list of modifiers")
            else:
                refused = [m for m in value if m not in allowed_modifiers]
                for modifier in refused:
                    errors.append(f"{path}.modifiers: {modifier!r} is not a modifier the style allows "
                                  f"({', '.join(allowed_modifiers) or 'none'})")
                if not refused:
                    shot["modifiers"] = list(dict.fromkeys(value))
        if "action" in item:
            _edit_action(ec, path, shot, scene, item["action"], errors)
        if "keep_still" in item:
            if type(item["keep_still"]) is not bool:
                errors.append(f"{path}.keep_still: expected true or false")
            else:
                shot["keep_still"] = item["keep_still"]
        if "prompt_override" in item:
            value = item["prompt_override"]
            if value is not None and not isinstance(value, str):
                errors.append(f"{path}.prompt_override: expected a text or null")
            else:
                shot["prompt_override"] = _optional_text(value)
        if (shot["framing"], shot["action"], shot["subject_tags"]) != before:
            _path, wanted, _prompt = to_resolve.get(shot["shot_id"], (path, None, True))
            to_resolve[shot["shot_id"]] = (path, wanted, True)

    retime = False
    transitions = {transition["after"]: transition for transition in board["transitions"]}
    for path, item in _items(errors, fields, "transitions", "after", STORYBOARD_TRANSITION_PATCH_FIELDS):
        transition = transitions.get(item.get("after"))
        if transition is None:
            errors.append(f"{path}.after: {item.get('after')!r} has no transition (one follows every shot but the "
                          "last)")
            continue
        if "type" not in item:
            continue
        kind = item["type"]
        duration = ec.template["transitions_s"].get(kind) if kind in schemas.TRANSITIONS else None
        if duration is None:
            errors.append(f"{path}.type: {kind!r} is not one of {', '.join(schemas.TRANSITIONS)}")
        elif kind != transition["type"]:
            transition["type"] = kind
            transition["duration_s"] = duration
            retime = True
    return to_resolve, retime


def _resolve_again(ec, script, board, to_resolve, errors) -> None:
    """Each shot of *to_resolve* moved and -- when its framing, action or
    subjects changed -- resolved again, as the storyboard resolves it
    (``shots.motion_for``, ``shots.resolve_shot``). A camera motion sent
    that the style overrides for the shot's framing or scene is an error
    rather than silently replaced (DEC-141). A motion swap alone keeps the
    shot's prompt as it is, so its image stays current (phase 5 stage 7):
    re-resolving it would take in an entity edited since, outdating the
    image for a change it does not show."""
    lock = ec.style_lock
    by_function = lock["motion_rules"]["tier1"]["by_function"]
    scenes = {scene["scene_id"]: scene for scene in script["scenes"]}
    by_id = {shot["shot_id"]: shot for shot in board["shots"]}
    for shot_id, (path, wanted, prompt) in to_resolve.items():
        shot = by_id[shot_id]
        scene = scenes[shot["scene_id"]]
        motion = shots.motion_for(shot["framing"], wanted or shot["camera_motion"], scene["function"], lock)
        if wanted is not None and motion["type"] != wanted:
            what = (f"a {shot['framing']} shot" if shot["framing"] in by_function
                    else f"a {scene['function']} scene")
            errors.append(f"{path}.camera_motion: the style moves {what} with {motion['type']}, not {wanted!r}")
            continue
        if not prompt:
            shot["camera_motion"] = motion["type"]
            shot["motion"] = motion
            continue
        try:
            resolved = shots.resolve_shot({"framing": shot["framing"], "action": shot["action"],
                                           "subjects": shot["subject_tags"]}, scene=scene, entities=ec.entities,
                                          style_lock=lock, consistency_mode=ec.consistency_mode)
        except (KeyError, ValueError) as exc:
            raise WorkflowError(CONFLICT, (f"Shot {shot_id} names something the story no longer has ({exc}): plan "
                                           f"scene {scene['scene_id']} again (the storyboard step).")) from None
        shot["camera_motion"] = motion["type"]
        shot["motion"] = motion
        shot.update(image_prompt=resolved["image_prompt"], video_action=resolved["video_action"],
                    negative_prompt=resolved["negative_prompt"],
                    reference_images=resolved["reference_images"], consistency=resolved["consistency"])
        _stamp_resolved(board["resolved_from"], shot, scene, ec.entities)


def _check_framing_rules(ec, script, board, trial, errors) -> None:
    """A framing edit keeps the storyboard's cross-scene rules
    (``shots.rule_pass``: no two shots in a row share a framing, every
    three scenes hold a close-up) or it is refused, naming the shot the
    rules would move -- the edited one or a neighbour -- instead of that
    shot being moved silently the next time the storyboard is built
    (phase 5 stage 7). Only what the edit breaks counts: a rule a storyboard
    already broke before it (edited by hand before this check) is left to
    whoever made it."""
    if all(new["framing"] == old["framing"] for new, old in zip(trial["shots"], board["shots"])):
        return
    before = shots.rule_moves(board, script, ec.style_lock)
    for shot_id, move in shots.rule_moves(trial, script, ec.style_lock).items():
        if before.get(shot_id) == move:
            continue
        framing, wanted, why = move
        errors.append(f"shots: the framing edit breaks the storyboard's cross-scene rules ({why}): they would move "
                      f"{shot_id} from {framing!r} to {wanted!r}; it is refused rather than moving a shot silently, "
                      "pick another framing")


def patch_storyboard(stories, story_id, ep, fields, *, now) -> dict:
    """Edit episode *ep*'s storyboard (``fields``: ``STORYBOARD_PATCH_FIELDS``);
    returns it as written.

    ``shots`` ``[{shot_id, framing?, camera_motion?, modifiers?, action?,
    keep_still?, prompt_override?}]`` -- a shot whose framing or action
    changed is moved and resolved again (its action tagged as T1 tags one:
    the scene's tags only, no name), so its image goes stale
    (``assets.shot_state``); a framing that breaks the cross-scene rules is
    refused, naming the shot they would move (:func:`_check_framing_rules`);
    a camera motion alone moves the shot and keeps its prompt, so its image
    stays current and only its render changes (a motion the style fixes is
    refused, DEC-141); ``transitions`` ``[{after, type}]``
    -- the template's duration for it, a non-cut type only between two
    scenes, and the shots re-timed around it; ``refresh_prompts: true`` --
    every shot's prompt resolved again from the entities as they are now
    (``shots.refresh_prompts``). Refused whole (``invalid`` with every error;
    nothing written). A change clears the storyboard's approval and moves its
    revision; the script is re-timed with it (its revision and approval
    never move); no shot's ``assets`` moves (phase 5 stage 7: never a
    rebuild). Nothing sent, or nothing changed: nothing written.
    ``conflict`` without a storyboard."""
    story = load(stories, story_id)
    ep = episode_bounds(stories, story, ep)
    _unknown_fields(fields, STORYBOARD_PATCH_FIELDS, "storyboard")
    board = read_episode(stories, story_id, ep, STORYBOARD_DOC)
    if board is None or not board["shots"]:
        raise _no_storyboard(ep)
    if not fields:
        return board
    script = read_episode(stories, story_id, ep, SCRIPT_DOC)
    if script is None or not script["scenes"]:
        raise _no_script(ep)
    ec = _context(stories, story_id, ep)
    trial = copy.deepcopy(board)
    errors = []
    refresh = fields.get("refresh_prompts")
    if refresh is not None and type(refresh) is not bool:
        errors.append("refresh_prompts: expected true or false")
    to_resolve, retime = _edit_storyboard(ec, script, trial, fields, errors)
    message = "The storyboard would not be valid with these values."
    if not errors:
        _resolve_again(ec, script, trial, to_resolve, errors)
    if not errors:
        _check_framing_rules(ec, script, board, trial, errors)
    if errors:
        raise _invalid_values(message, errors)
    if retime:
        shots.retime_storyboard(trial, script, template=ec.template, language=ec.language, style_lock=ec.style_lock)
    if refresh:
        trial = shots.refresh_prompts(trial, script, entities=ec.entities, style_lock=ec.style_lock,
                                      consistency_mode=ec.consistency_mode)
    if trial == board:
        return board
    trial["approved_at"] = None
    trial["rev"] = board["rev"] + 1
    errors = episode_common.storyboard_errors(ec, trial, script)
    if errors:
        raise _invalid_values(message, errors)
    written = _write(episode_common.write_storyboard, "storyboard", ec, trial, script, now=now)
    episode_common.retime(script, ec, written)
    _write(episode_common.write_script, "script", ec, script, now=now)
    return written


def patch_assets(stories, story_id, ep, fields, *, now, env=None) -> dict:
    """Edit episode *ep*'s assets (``fields``: ``ASSETS_PATCH_FIELDS``, and
    :data:`ASSETS_LINKS_FIELD`); returns the storyboard as written.

    ``links`` ``{"image": "<link>"}`` -- phase 6 stage 6, A-087 -- switches
    the link the episode's shot images are made on to that link of its image
    chain (IMAGE_CHAIN, or IMAGE_EDIT_CHAIN in ``references`` mode, as *env*
    -- the Settings values -- names it): ``assets.json``'s ``links.image``
    becomes ``{link, since: now, switched_from}``
    (``assets.switched_assets_doc``). The images another link made are then
    stale, so the next assets run makes exactly those again; the storyboard
    is not written (its revision and approval never move) and the assets
    approval goes stale (the fingerprint covers the links). Nothing ever
    switches on its own; the link the episode already has: nothing written.
    ``{"video": "<link>"}`` (stage 11) does the same for the clips: a link
    of VIDEO_CHAIN (*env*'s) or the local ComfyUI, ``links.video``
    (``assets.switched_video_doc``); the clips another link made go stale
    (``clips.clip_state``), so the next run animates exactly those again --
    the sticky video offer's ``switch`` is this edit.

    ``shots`` ``[{shot_id, locked?}]`` -- a locked shot keeps the image it
    has: the assets step skips it, a regenerate of it is refused ("unlock it
    first") and the assets approval takes it as it is (plan phase 4,
    "Documents"); only a shot with an image on disk may be locked. The lock
    lives in the shot's ``assets`` (DEC-155): the storyboard's revision and
    approval never move, and the fingerprint the assets were approved with
    covers it, so a new lock makes that approval stale (derived, never
    cleared).

    Phase 6 stage 7: a shot item may also set :data:`ASSETS_SHOT_FLAG_FIELDS`
    -- ``keep_still`` (over the storyboard's own), ``animate`` (a pin: the
    planner animates it first, even past the cap) and ``keep_native_audio``
    (tier 3) -- true or false, or null to clear the override. They go to
    ``assets.json``'s ``shots`` (``assets.overridden_assets_doc``; a
    minimal document is started when there is none), never to the
    storyboard, so its bytes, revision and approval never move; the assets
    approval is kept and goes stale exactly when a shot's effective flags
    move (the fingerprint's ``clips`` part). A pin on a shot kept still is
    refused. Refused whole (``invalid`` with every error; nothing written).
    Nothing sent, or nothing changed: nothing written. ``conflict`` without a
    storyboard."""
    story = load(stories, story_id)
    ep = episode_bounds(stories, story, ep)
    _unknown_fields(fields, ASSETS_PATCH_FIELDS, "assets")
    board = read_episode(stories, story_id, ep, STORYBOARD_DOC)
    if board is None or not board["shots"]:
        raise _no_storyboard(ep)
    if not fields:
        return board
    script = read_episode(stories, story_id, ep, SCRIPT_DOC)
    if script is None or not script["scenes"]:
        raise _no_script(ep)
    ec = _context(stories, story_id, ep)
    trial = copy.deepcopy(board)
    by_id = {shot["shot_id"]: shot for shot in trial["shots"]}
    errors = []
    flag_changes, flag_paths = {}, {}
    for path, item in _items(errors, fields, "shots", "shot_id", ASSETS_SHOT_PATCH_FIELDS + ASSETS_SHOT_FLAG_FIELDS):
        shot = by_id.get(item.get("shot_id"))
        if shot is None:
            errors.append(f"{path}.shot_id: {item.get('shot_id')!r} is not a shot of episode {ep}")
            continue
        for name in ASSETS_SHOT_FLAG_FIELDS:
            if name not in item:
                continue
            if item[name] is not None and type(item[name]) is not bool:
                errors.append(f"{path}.{name}: expected true, false or null")
            else:
                flag_changes.setdefault(shot["shot_id"], {})[name] = item[name]
                flag_paths[shot["shot_id"]] = path
        if "locked" not in item:
            continue
        locked = item["locked"]
        if type(locked) is not bool:
            errors.append(f"{path}.locked: expected true or false")
        elif locked and not shot["assets"].get("locked") and assets_step.shot_image_path(ec, shot) is None:
            errors.append(f"{path}.locked: shot {shot['shot_id']} has no image yet: make it first (the assets "
                          "step), then lock it")
        elif locked != bool(shot["assets"].get("locked")):
            shot["assets"]["locked"] = locked
    wanted = {}
    if ASSETS_LINKS_FIELD in fields:
        wanted = assets_step.link_switch(ec, fields[ASSETS_LINKS_FIELD], env=env, errors=errors)
    overridden = None
    if flag_changes and not errors:
        overridden = _overridden_assets(stories, story_id, ep, ec, board, flag_changes, flag_paths, errors, now=now)
    if errors:
        raise _invalid_values("The assets would not be valid with these values.", errors)
    if trial != board:
        board = _write(episode_common.write_storyboard, "storyboard", ec, trial, script, now=now)
    if overridden is not None:
        _write_assets_doc(stories, story_id, ep, overridden, now=now, what="these shot overrides")
    if sticky_link.IMAGE in wanted:
        _switch_image_link(stories, story_id, ep, ec, board, wanted[sticky_link.IMAGE], env=env, now=now)
    if sticky_link.VIDEO in wanted:
        doc = read_episode(stories, story_id, ep, ASSETS_DOC)
        new = assets_step.switched_video_doc(ec, doc, wanted[sticky_link.VIDEO], now=now)
        if new is not None:
            _write_assets_doc(stories, story_id, ep, new, now=now, what="this video link")
    return board


def _overridden_assets(stories, story_id, ep, ec, board, changes, paths, errors, *, now):
    """:func:`patch_assets`' per-shot overrides: ``assets.json`` with
    *changes* applied (``assets.overridden_assets_doc``), or None when
    nothing changes; *errors* gains a pin on a shot that would be kept still
    (the planner drops a kept-still shot first: the pin would do nothing)."""
    doc = read_episode(stories, story_id, ep, ASSETS_DOC)
    new = assets_step.overridden_assets_doc(ec, doc, changes, now=now)
    if new is None:
        return None
    for shot in board["shots"]:
        if shot["shot_id"] not in changes:
            continue
        flags = video_plan.effective_shot_flags(shot, video_plan.shot_overrides(new, shot["shot_id"]))
        if flags["keep_still"] and flags["animate"]:
            errors.append(f"{paths[shot['shot_id']]}.animate: shot {shot['shot_id']} is kept still, so a pin would "
                          "not animate it: send keep_still false with it")
    return new


def _write_assets_doc(stories, story_id, ep, doc, *, now, what) -> None:
    try:
        stories.write_episode_doc(story_id, ep, ASSETS_DOC, doc, now=now)
    except schemas.SchemaError as exc:
        raise WorkflowError(CONFLICT, {"message": f"The assets would not be valid with {what}.",
                                       "errors": list(exc.errors)}) from None
    except (KeyError, ValueError) as exc:
        raise WorkflowError(CONFLICT, f"The assets cannot be written: {exc}.") from None


def _switch_image_link(stories, story_id, ep, ec, board, wanted, *, env, now) -> None:
    """:func:`patch_assets`' ``links``: ``assets.json`` with the episode's
    image link switched to *wanted* (``assets.switched_assets_doc``), written;
    nothing when it is already the episode's link."""
    doc = read_episode(stories, story_id, ep, ASSETS_DOC)
    new = assets_step.switched_assets_doc(ec, board, doc, wanted, env=env, now=now)
    if new is None:
        return
    try:
        stories.write_episode_doc(story_id, ep, ASSETS_DOC, new, now=now)
    except schemas.SchemaError as exc:
        raise WorkflowError(CONFLICT, {"message": "The assets would not be valid with this image link.",
                                       "errors": list(exc.errors)}) from None
    except (KeyError, ValueError) as exc:
        raise WorkflowError(CONFLICT, f"The assets cannot be written: {exc}.") from None


def build_fast_storyboard(stories, story, ep, *, now, on_log) -> dict:
    """The fast storyboard of episode *ep* (``storyboard.build_fast``: no
    call, DEC-109), written with the script re-timed; returns it. The
    storyboard step's preconditions (:func:`episode_context`) and a complete
    script (``conflict`` naming what is missing) first."""
    ec = episode_context(stories, story, ep, step="storyboard")
    require_complete_script(ec)
    try:
        return storyboard_step.build_fast(stories, story["story_id"], ep, now=now, on_log=on_log)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None
    except ValueError as exc:
        raise WorkflowError(CONFLICT, f"The fast storyboard could not be built: {exc}") from None


# -------------------------------------------------------------------- units

def script_units(ec) -> dict:
    """The LLM calls a script step would make now, counting only what is
    missing (the runner's own order)::

        {"E1", "E2", "E3", "E4", "E2_range": [lo, hi] | None,
         "llm_calls", "llm_calls_range": [lo, hi]}

    No beat sheet yet: E1, then E2 once per body scene E1 would actually ask
    for (``timing.episode_slots``: an exact, positional count since stage
    12b, not the template's body range), a full E3 and E4 (``E2``/
    ``llm_calls`` follow that exact count; ``E2_range``/``llm_calls_range``
    keep the template's body range, since a regenerate or a future template
    change could still land anywhere in it). Otherwise E2 per body scene
    still a stub that someone can speak in, one E3 for every framing part
    missing (or one per part when only some are), and E4 when anything is
    written or the report is missing, stale or of an older revision."""
    try:
        script = episode_common.read_episode(ec, SCRIPT_DOC)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None
    if not script or not script["scenes"]:
        low, high = ec.template["slots"]["body"]["count"]
        exact = sum(1 for slot in timing.episode_slots(ec.template, ec.ep) if slot == "body")
        return {"E1": 1, "E2": exact, "E3": 1, "E4": 1, "E2_range": [low, high], "llm_calls": 3 + exact,
                "llm_calls_range": [3 + low, 3 + high]}
    stubs = [scene for scene in script_step.body_scenes(script) if scene["state"] == "stub"]
    e2 = sum(1 for scene in stubs if script_step.can_speak(ec, scene))
    missing = script_step.missing_parts(script, ec.ep)
    e3 = 0 if not missing else (1 if missing == script_step.e3_parts(ec.ep) else len(missing))
    e4 = 1 if stubs or missing or script_step.needs_check(script) else 0
    calls = e2 + e3 + e4
    return {"E1": 0, "E2": e2, "E3": e3, "E4": e4, "E2_range": None, "llm_calls": calls,
            "llm_calls_range": [calls, calls]}


def storyboard_units(ec) -> dict:
    """The T1 calls a storyboard step would make now: ``{"t1_calls",
    "scenes": [scene_id, ...], "refusal": sentence | None}`` -- one per scene
    with no plan, a stale plan or a fast one
    (``storyboard.scenes_to_plan``). While the script is not complete the
    step would be refused (``refusal``) and every scene it has is counted."""
    try:
        script = storyboard_step.require_complete_script(ec)
    except StepFailed as exc:
        try:
            existing = episode_common.read_episode(ec, SCRIPT_DOC)
        except StepFailed:
            existing = None
        scenes = [scene["scene_id"] for scene in (existing or {}).get("scenes") or []]
        return {"t1_calls": len(scenes), "scenes": scenes, "refusal": str(exc)}
    try:
        board = episode_common.read_episode(ec, STORYBOARD_DOC)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None
    plans, sources, stale = storyboard_step.current_plans(board, script)
    todo = [scene["scene_id"] for scene in storyboard_step.scenes_to_plan(script, plans, sources, stale)]
    return {"t1_calls": len(todo), "scenes": todo, "refusal": None}


def measure_estimate(ec, *, env) -> dict:
    """What measuring the episode's lines with real voices would do now
    (``script.measure_estimate``: calling nothing); before a script, nothing
    to measure yet."""
    try:
        script = episode_common.read_episode(ec, SCRIPT_DOC)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None
    return script_step.measure_estimate(ec, script or {"scenes": []}, env=env)


def _step_refusal(call, *args, **kwargs):
    """*call*, its ``StepFailed`` a ``conflict`` with the step's sentence."""
    try:
        return call(*args, **kwargs)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None


def assets_estimate(ec, *, env, align_words=False, probe_local=False, animate=True, route=None) -> dict:
    """What the assets step would do and spend now (``GET
    /estimate/assets``): ``assets.asset_units``' shape -- ``images``,
    ``voices``, ``alignment``, ``paid_links``, ``caps``, ``est_usd``,
    ``over_cap``, ``ready`` -- and ``paid``, the fast track's verdict on it
    (``fast_track.paid_verdict``: free, paid within the caps, stops before
    paid, blocked), whose sentence is ``message``. ``conflict`` with the
    step's own sentence while the script and a current storyboard are not
    approved (it makes nothing then). Calls nothing but, with *probe_local*,
    a local editor's status probe. *animate* is the step's param (phase 6
    stage 8); *route* (stage 11, ``?route=``) prices another route than the
    story's own, writing nothing (``invalid`` for one that is not a route)."""
    if route is not None and route not in defaults.ROUTES:
        raise WorkflowError(INVALID, f"The route must be one of {', '.join(defaults.ROUTES)}, not {route!r}.")
    script, board = _step_refusal(assets_step.require_approved, ec)
    units = _step_refusal(assets_step.asset_units, ec, script, board, env=env, align_words=align_words,
                          probe_local=probe_local, animate=animate, route=route)
    verdict = fast_track_step.paid_verdict(units, ep=ec.ep)
    return dict(units, step="assets", ep=ec.ep, paid=verdict, message=verdict["message"])


def render_estimate(ec, params) -> dict:
    """What the render would do now (``GET /estimate/render``)::

        {"step": "render", "ep", "est_usd": 0.0, "units": {"llm_calls": 0, "shots"},
         "route_class": "local", "params": {"subtitles", "encoder"}, "needed": bool,
         "seconds", "minutes", "basis", "ready": true, "message"}

    *params* as the step reads them (``invalid`` otherwise); at tier >= 2
    the clip refusal the run would meet is the ``conflict`` too
    (``render.require_clips``, phase 6 stage 11). ``needed`` is
    false while the last render is the one it would make
    (``render.current_render``: rendering again would only repeat it); the
    time is the fast track's authored estimate (``fast_track.render_seconds``,
    A-069). ``conflict`` with the step's own sentence while the episode
    cannot be rendered. Calls nothing, starts no process."""
    wanted = render_step.read_params(phase4_request("render", params))
    _script, board, _doc = _step_refusal(render_step.require_clips, ec, wanted)
    current = render_step.current_render(ec, wanted)
    shots = len(board["shots"])
    seconds = 0.0 if current else fast_track_step.render_seconds(shots)
    if current:
        message = f"Episode {ec.ep}'s last render is the one this would make: rendering again repeats it."
    else:
        message = (f"{shots} shot{'s' if shots != 1 else ''} rendered on this server, about {seconds / 60:.1f} min "
                   "(the shots unchanged since the last render come from its cache); nothing is called, $0.00.")
    return {
        "step": "render", "ep": ec.ep, "est_usd": 0.0, "units": {"llm_calls": 0, "shots": shots},
        "route_class": "local", "params": wanted, "needed": not current, "seconds": seconds,
        "minutes": round(seconds / 60, 1),
        "basis": (f"estimate: {fast_track_step.RENDER_SECONDS_PER_SHOT:g} s a shot + "
                  f"{fast_track_step.RENDER_TAIL_SECONDS:g} s (stage-0 bench; A-069 records the measured times)"),
        "ready": True, "message": message,
    }


def reedit_estimate(ec) -> dict:
    """What a re-render would do now (``GET /estimate/rerender``): $0, no
    LLM call, the dry run's own count (:func:`reedit_changes`, stage 8's
    predicate, RC-M8: never a second implementation)::

        {"step": "rerender", "ep", "est_usd": 0.0, "units": {"llm_calls": 0, "shots"},
         "route_class": "local", "ready": true, "current": bool, "shots_total",
         "rebuild": [shot ids], "reuse": [shot ids], "reasons": {shot id: reason},
         "message": "3 of 11 shots re-rendered"}

    ``conflict`` with the render's own sentence while the episode cannot be
    rendered, or has no finished render to re-render, right now -- the same
    two refusals the route meets before a job exists. Calls nothing; hashes
    the cache's clips."""
    changes = reedit_changes(ec)
    total = changes["shots_total"]
    return {
        "step": "rerender", "ep": ec.ep, "est_usd": 0.0, "units": {"llm_calls": 0, "shots": total},
        "route_class": "local", "ready": True, "current": changes["current"],
        "shots_total": total, "rebuild": list(changes["rebuild"]), "reuse": list(changes["reuse"]),
        "reasons": dict(changes["reasons"]), "message": changes["summary"],
    }


def metadata_units(ec) -> dict:
    """``{"platforms": [the platforms the metadata step would ask M1 for],
    "llm_calls"}``: every platform, unless a pack written for this render
    and this script revision has some (the step fills what is missing).
    ``conflict`` with the step's own sentence without a finished render."""
    script, _manifest, render_sha = _step_refusal(metadata_step.require_render, ec)
    pack = read_episode(ec.store, ec.story_id, ec.ep, METADATA_PACK_DOC)
    written = pack["platforms"] if metadata_step.is_current(pack, script, render_sha) else {}
    todo = [platform for platform in metadata_step.PLATFORMS if platform not in written]
    return {"platforms": todo, "llm_calls": len(todo)}


def fast_track_estimate(ec, *, env, storyboard=None) -> dict:
    """``fast_track.estimate`` of episode *ec.ep* (``GET
    /estimate/fast-track``), with ``storyboard`` (``t1`` | ``fast``) checked
    as the step reads it (``invalid``); a document that does not validate is
    a ``conflict``."""
    params = {} if storyboard is None else {fast_track_step.STORYBOARD_PARAM: storyboard}
    mode = fast_track_step.read_params(phase4_request("fast-track", params))[fast_track_step.STORYBOARD_PARAM]
    return _step_refusal(fast_track_step.estimate, ec, env=env, storyboard=mode)


# ================================================================== phase 5
#
# Step 13 (spec 3, 2.6, 9.1-9.2; plan 11 stage 4): the series steps. Each is a
# job of one episode "N" (``ep``) making one LLM call on the free chain
# (``llm_call.call_json``: a paid link is never called while ``allow_paid``
# is off, DEC-115; the front ends' key gate refuses a chain whose only keyed
# links are paid before a job exists) and ending ``awaiting_approval``:
#
# - ``memory`` (S3): episode N's series memory entry from its approved
#   script, merged into ``season.json`` under the store lock; approved by
#   ``memory:<N>`` (:func:`approve_memory`). Its state (:func:`memory_state`)
#   is what the gate of episode N+1 reads (``episode_common.memory_refusal``);
# - ``feedback`` (F1): the audience feedback pasted for episode N
#   (:func:`store_feedback`) digested into three directions; approved by
#   ``feedback:<N>`` with the direction chosen (:func:`approve_feedback`);
# - ``propose-next`` (N1): new characters and twists for episode N+1 from N's
#   fresh approved memory, in ``episodes/ep<N+1>/proposals.json``; each item
#   accepted or rejected (:func:`decide_proposal`), then ``proposals:<N+1>``
#   approved once every item is decided (:func:`approve_proposals`).
#
# None of them, nor any of these functions, writes the story's approvals or
# status (RC-M5); only the cast step an accepted lead or support character is
# queued on folds the cast approval, as it always has (DEC-123).

SERIES_PROMPTS = {"memory": memory_step.PROMPT, "feedback": feedback_step.PROMPT,
                  "propose-next": propose_next_step.PROMPT}
_SERIES_MODULES = {"memory": memory_step, "feedback": feedback_step, "propose-next": propose_next_step}
PROPOSALS_DOC = story_store.EPISODE_PROPOSALS_DOC
# approve_series without a direction (None is a choice: no direction).
_NO_DIRECTION = object()


def series_job_doc(step, ep):
    """The document a series step's job writes, and so the approval that
    completes it: ``memory:<ep>``, ``feedback:<ep>``, and -- the proposals sit
    in the folder of the episode they are for -- ``proposals:<ep + 1>``. None
    without an episode number, or for another step."""
    if step not in SERIES_STEPS or type(ep) is not int:
        return None
    if step == "propose-next":
        return f"proposals:{ep + 1}"
    return f"{step}:{ep}"


def is_series_approval(doc) -> bool:
    """Whether *doc* is ``<word>:<something>`` with *word* one of
    :data:`SERIES_APPROVALS` (the number is checked when it is approved)."""
    word, sep, rest = doc.partition(":")
    return bool(sep) and bool(rest) and word in SERIES_APPROVALS


def series_request(step, params) -> dict:
    """A series step's *params*: it takes none beyond ``ep`` (``invalid``
    otherwise). Returns them."""
    if params:
        raise WorkflowError(INVALID, f"'{step}' takes no parameters.")
    return {}


def series_context(stories, story, ep, *, step) -> episode_common.EpisodeContext:
    """The :class:`episode_common.EpisodeContext` of a series step's episode
    *ep*, once the step may run on it -- its runner's own checks, in its
    order, calling nothing: the episode's preconditions without the memory
    gate (:func:`episode_context`: ``conflict`` for a story that is not
    ``ready``, ``invalid`` without an episode number or for one the season
    does not plan), then ``conflict`` with the runner's sentence: ``memory``
    an approved script (``memory.require_approved_script``); ``feedback`` a
    pasted feedback item (``feedback.require_feedback``); ``propose-next``
    an episode after it in the season and its memory written, approved and
    fresh (``propose_next.check``). Any other *step* is ``refuse_step``'s."""
    if step not in SERIES_STEPS:
        refuse_step(step)
    ec = episode_context(stories, story, ep, step=step, require_memory=False)
    _step_refusal(_SERIES_MODULES[step].check, ec)
    return ec


def series_units(ec, step) -> dict:
    """What a series step calls: ``{"llm_calls": 1, "prompt": "S3"|"F1"|"N1"}``
    -- one call on the free chain, whatever the episode (the estimate's
    ``_llm_estimate`` of one call, as the metadata's)."""
    return {"llm_calls": 1, "prompt": SERIES_PROMPTS[step]}


def _update_season(stories, story_id, mutate, *, now) -> dict:
    """``StoryStore.update_doc`` of ``season.json`` (re-read, *mutate*, write,
    under the store lock): ``not_found`` for a story gone meanwhile;
    ``StoryUnreadable`` for a season on disk that does not validate;
    ``invalid`` with every error when the changed one would not."""
    try:
        return stories.update_doc(story_id, SEASON_DOC, mutate, now=now, validator=schemas.season_arc_errors)
    except KeyError:
        raise not_found() from None
    except schemas.SchemaError as exc:
        if exc.name == SEASON_DOC:  # the write's own validation
            raise WorkflowError(INVALID, {"message": "The season would not be valid with these values.",
                                          "errors": list(exc.errors)}) from None
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None


def _require_season(current):
    if current is None:
        raise WorkflowError(CONFLICT, "There is no season arc yet: run the season step first.")
    return current


# ------------------------------------------------------------------ memory

def memory_state(stories, story, ep) -> str:
    """``none`` | ``draft`` | ``approved`` | ``stale`` of episode *ep*'s series
    memory (``series_memory.memory_state``: stale once the script moved on
    from the revision the entry was written from, whatever its approval).
    What the episode views show; a stale entry never un-approves anything of
    episode *ep* + 1, it only blocks a new script or storyboard there."""
    story_id = story["story_id"]
    ep = episode_bounds(stories, story, ep)
    return series_memory.memory_state(season(stories, story_id), ep, read_episode(stories, story_id, ep, SCRIPT_DOC))


def approve_memory(stories, story_id, ep, *, now) -> dict:
    """Approve episode *ep*'s series memory entry (``memory:<ep>``); returns
    the entry as written.

    ``conflict`` without an entry (run memory first) and for a stale one
    (the script changed since it was written: run memory again). Its
    ``approved_at`` becomes *now*, on ``season.json`` re-read under the store
    lock; nothing else moves -- not the fold, not the season's approval, not
    the story's approvals or status (RC-M5)."""
    story = load(stories, story_id)
    ep = episode_bounds(stories, story, ep)
    key = series_memory.memory_key(ep)
    script = read_episode(stories, story_id, ep, SCRIPT_DOC)

    def approve(current):
        entry = series_memory.entry_map(_require_season(current)).get(key)
        if entry is None:
            raise WorkflowError(CONFLICT, f"Episode {ep} has no series memory yet: run memory for episode {ep} first.")
        if series_memory.entry_is_stale(entry, script):
            run = f"run memory for episode {ep}"
            if not (script and script["approved_at"]):
                run = f"approve episode {ep}'s script, then {run}"
            raise WorkflowError(CONFLICT, (f"Episode {ep}'s series memory is out of date: episode {ep}'s script "
                                           f"changed since it was written. {run[0].upper()}{run[1:]} again, then "
                                           "approve that."))
        entry["approved_at"] = now
        return current

    saved = _update_season(stories, story_id, approve, now=now)
    return copy.deepcopy(saved["series_memory"]["entries"][key])


# ---------------------------------------------------------------- feedback

def feedback_item(stories, story_id, ep):
    """Episode *ep*'s pasted feedback item (a copy), or None."""
    item = feedback_step.feedback_item(season(stories, story_id), ep)
    return copy.deepcopy(item) if item is not None else None


def _pasted(what, value, limit) -> None:
    if len(value) > limit:
        raise WorkflowError(INVALID, (f"The {what} is {len(value)} characters: at most {limit} are taken, and it is "
                                      "never shortened -- paste less (the part that matters most)."))


def store_feedback(stories, story_id, ep, text, stats=None, *, now) -> dict:
    """Store the audience feedback pasted for episode *ep* (stage 5's
    endpoint; the ``feedback`` step reads it); returns the item.

    ``invalid`` for an empty text, and for a text or *stats* over
    ``schemas.FEEDBACK_TEXT_MAX_LENGTH`` / ``FEEDBACK_STATS_MAX_LENGTH``
    (6,000 characters each): refused whole, **never trimmed** -- a paste is
    kept exactly as it was sent. ``conflict`` without a season. One item per
    episode: the new ``{ep, pasted_at: now, text, stats?}`` replaces any
    earlier one of *ep*, its digest, directions and chosen direction with it
    (the item goes last). Written on ``season.json`` re-read under the store
    lock; the season's approval and the story's never move."""
    if not isinstance(text, str) or not text.strip():
        raise WorkflowError(INVALID, "The feedback text is empty: paste the comments first.")
    _pasted("feedback text", text, schemas.FEEDBACK_TEXT_MAX_LENGTH)
    if stats is not None:
        if not isinstance(stats, str):
            raise WorkflowError(INVALID, f"The stats are a text, not {type(stats).__name__}.")
        _pasted("stats text", stats, schemas.FEEDBACK_STATS_MAX_LENGTH)
    story = load(stories, story_id)
    ep = episode_bounds(stories, story, ep)
    item = {"ep": ep, "pasted_at": now, "text": text}
    if stats is not None and stats.strip():
        item["stats"] = stats

    def paste(current):
        current = _require_season(current)
        current["audience_feedback"] = [old for old in current["audience_feedback"] if old.get("ep") != ep]
        current["audience_feedback"].append(copy.deepcopy(item))
        return current

    _update_season(stories, story_id, paste, now=now)
    return item


def approve_feedback(stories, story_id, ep, *, direction, now) -> dict:
    """Approve episode *ep*'s digested feedback (``feedback:<ep>``) with the
    *direction* chosen: 0, 1 or 2 (an index into its three directions), or
    None for none; returns the item as written.

    ``invalid`` for any other *direction* (a bool, a float, a text);
    ``conflict`` without a pasted item, and before F1 has digested it (run
    feedback first). ``chosen_direction`` becomes *direction* on
    ``season.json`` re-read under the store lock; it steers E1 of episode
    *ep* + 1 only (``series_memory.chosen_direction``) and N1."""
    if direction is not None and (type(direction) is not int or direction not in range(schemas.FEEDBACK_DIRECTIONS)):
        raise WorkflowError(INVALID, f"direction is 0, 1, 2 or null (no direction), not {direction!r}.")
    story = load(stories, story_id)
    ep = episode_bounds(stories, story, ep)

    def choose(current):
        item = feedback_step.feedback_item(_require_season(current), ep)
        if item is None:
            raise WorkflowError(CONFLICT, f"Episode {ep} has no audience feedback yet: paste it first.")
        if "directions" not in item:
            raise WorkflowError(CONFLICT, (f"Episode {ep}'s feedback has not been digested yet: run feedback for "
                                           f"episode {ep} first."))
        item["chosen_direction"] = direction
        return current

    saved = _update_season(stories, story_id, choose, now=now)
    return copy.deepcopy(feedback_step.feedback_item(saved, ep))


# --------------------------------------------------------------- proposals

def proposals(stories, story_id, ep):
    """Episode *ep*'s ``proposals.json`` (N1's, for this episode), or None."""
    return read_episode(stories, story_id, ep, PROPOSALS_DOC)


def _require_proposals(stories, story_id, ep) -> dict:
    doc = proposals(stories, story_id, ep)
    if doc is None:
        if ep < 2:
            raise WorkflowError(CONFLICT, "Episode 1 has no proposals: they are made for episode 2 on.")
        raise WorkflowError(CONFLICT, (f"Episode {ep} has no proposals yet: run propose-next for episode {ep - 1} "
                                       "first."))
    return doc


def _proposal_item(doc, item_id):
    """``(item, "character"|"twist")``; ``not_found`` for another id."""
    for kind, key in (("character", "characters"), ("twist", "twists")):
        for item in doc[key]:
            if item["item_id"] == item_id:
                return item, kind
    raise WorkflowError(NOT_FOUND, f"Episode {doc['for_ep']}'s proposals have no item {item_id!r}.")


def _require_proposals_current(stories, story_id, doc) -> None:
    """``conflict`` unless the memory the proposals were written from still
    stands: episode N's entry approved, fresh, and of the script revision
    they record (``based_on``)."""
    memory_ep = doc["based_on"]["memory_ep"]
    arc = season(stories, story_id)
    state = series_memory.memory_state(arc, memory_ep, read_episode(stories, story_id, memory_ep, SCRIPT_DOC))
    entry = series_memory.entry_for(arc, memory_ep)
    if state == "approved" and entry["script_rev"] == doc["based_on"]["script_rev"]:
        return
    why = {"none": "it is gone", "draft": "it is not approved", "stale": f"episode {memory_ep}'s script changed"}.get(
        state, "it was written from another revision of the script")
    raise WorkflowError(CONFLICT, (f"Episode {doc['for_ep']}'s proposals were written from episode {memory_ep}'s "
                                   f"series memory as it was then, and {why} since: run propose-next for episode "
                                   f"{memory_ep} again, or reject them."))


def proposal_request(stories, story, ep, item_id, *, accept, role=None) -> dict:
    """What deciding item *item_id* of episode *ep*'s proposals would do,
    checked as :func:`decide_proposal` checks it, writing nothing -- so a
    front end can run the cast step's gates on ``cast.params`` before the
    decision is recorded::

        {"ep", "item_id", "kind": "character"|"twist", "decision": "accepted"|"rejected", "item",
         # an accepted character:
         "role", "folds_cast", "cast": {"step": "cast", "params": {"custom": [..], "introduced_in": ep}},
         "message"}

    ``invalid`` for *accept* not a bool, a *role* outside
    ``schemas.CHARACTER_ROLES``, or a role with anything but accepting a
    character; ``conflict`` without proposals; ``not_found`` for an unknown
    item; ``conflict`` for an item decided already (a decision is final: run
    propose-next again for new proposals). Accepting, not rejecting, also
    needs the memory the proposals were written from to stand
    (``conflict``); a twist, its target episode still in the arc; a
    character, a name the cast does not have yet (``conflict``: reject it
    instead) and room in the cast (:func:`cast_request`'s rules, ``invalid``).
    The role is the proposal's unless *role* overrides it; a lead or support
    character ``folds_cast``: once the cast step writes it, unapproved, the
    cast approval is cleared and the story leaves ``ready`` until it is
    approved (DEC-123, unchanged) -- ``message`` says so."""
    if type(accept) is not bool:
        raise WorkflowError(INVALID, f"accept is true or false, not {accept!r}.")
    if role is not None and role not in schemas.CHARACTER_ROLES:
        raise WorkflowError(INVALID, f"role is one of {', '.join(schemas.CHARACTER_ROLES)}, not {role!r}.")
    story_id = story["story_id"]
    ep = episode_bounds(stories, story, ep)
    doc = _require_proposals(stories, story_id, ep)
    item, kind = _proposal_item(doc, item_id)
    if role is not None and not (accept and kind == "character"):
        raise WorkflowError(INVALID, "A role is chosen only when accepting a character.")
    decided = doc["decisions"].get(item_id)
    if decided:
        raise WorkflowError(CONFLICT, (f"'{item_id}' is already {decided}: a decision is final (run propose-next for "
                                       f"episode {ep - 1} again for new proposals)."))
    request = {"ep": ep, "item_id": item_id, "kind": kind, "decision": "accepted" if accept else "rejected",
               "item": copy.deepcopy(item)}
    if not accept:
        return request
    _require_proposals_current(stories, story_id, doc)
    if kind == "twist":
        arc = season(stories, story_id)
        if not any(entry["ep"] == item["target_ep"] for entry in (arc or {}).get("arc") or []):
            raise WorkflowError(CONFLICT, (f"The season arc has no episode {item['target_ep']} any more: reject this "
                                           "twist, or run propose-next again."))
        request["message"] = (f"Episode {item['target_ep']}'s arc entry now tells this twist; what it said before is "
                              "kept in its history. Accepting the twist is its approval: the season stays approved.")
        return request

    role = role or item["role"]
    names = {entities_step.name_key(entity["name"]) for entity in list_entities(stories, story_id, CHARACTERS)}
    if entities_step.name_key(item["name"]) in names:
        raise WorkflowError(CONFLICT, f"{item['name']} is already a character of this story: reject this proposal "
                                      "instead.")
    custom = {"name": item["name"], "role": role, "one_line": item["one_line"]}
    if item.get("archetype"):
        custom["archetype"] = item["archetype"]
    params = {"custom": [custom], "introduced_in": ep}
    cast_request(stories, story, params)
    folds = role in schemas.CAST_APPROVAL_ROLES
    message = (f"{item['name']} joins the cast as a {role} character, introduced in episode {ep}: the cast step "
               "writes them (K1, the portrait, the sheets and a voice).")
    if folds:
        message += (f" A {role} character must be approved before the story is ready again: once the cast step "
                    f"writes {item['name']}, the cast approval is cleared (DEC-123) until you approve them.")
    else:
        message += f" A {role} character never holds up the cast approval: the story stays ready."
    request.update(role=role, folds_cast=folds, cast={"step": "cast", "params": params}, message=message)
    return request


def decide_proposal(stories, story_id, ep, item_id, *, accept, role=None, now) -> dict:
    """Accept or reject item *item_id* of episode *ep*'s proposals (*ep* is
    the proposals' ``for_ep``: the folder they sit in); returns
    :func:`proposal_request`'s payload, the decision recorded.

    Checked first by :func:`proposal_request` (every refusal is its). Then,
    under the store lock, on the documents as they are now (the item must
    still be the one checked -- proposals written again meanwhile are a
    ``conflict``):

    - **reject**: the decision is recorded, nothing else;
    - **accept a twist**: the target arc entry's ``summary`` and
      ``open_hooks_out`` become the twist's, the old ones pushed onto its
      ``history`` (``{summary, open_hooks_out, replaced_at: now, source:
      "proposal"}``); the acceptance *is* the approval: ``season.json``'s
      ``approved_at`` and ``approvals.season`` never move;
    - **accept a character**: the decision is recorded and the payload's
      ``cast`` names the job to queue -- the existing cast path, ``params =
      {"custom": [{name, role, one_line, archetype?}], "introduced_in": ep}``
      -- which the caller queues (the job store is the web layer's; the CLI
      runs it): K1, the sheets and a voice, then
      ``series_memory.introduced["epNN"]`` records the new id.

    Neither touches the story's approvals or status here (RC-M5); a lead or
    support character folds the cast approval when the cast step writes it
    (DEC-123)."""
    story = load(stories, story_id)
    request = proposal_request(stories, story, ep, item_id, accept=accept, role=role)
    ep = request["ep"]
    checked = request["item"]

    def amend(current):
        entry = next((entry for entry in _require_season(current)["arc"] if entry["ep"] == checked["target_ep"]),
                     None)
        if entry is None:
            raise WorkflowError(CONFLICT, (f"The season arc has no episode {checked['target_ep']} any more: reject "
                                           "this twist, or run propose-next again."))
        entry.setdefault("history", []).append({
            "summary": entry["summary"], "open_hooks_out": list(entry["open_hooks_out"]), "replaced_at": now,
            "source": "proposal"})
        entry["summary"] = checked["summary"]
        entry["open_hooks_out"] = list(checked["open_hooks_out"])
        return current

    def decide(current):
        if current is None:
            raise WorkflowError(CONFLICT, f"Episode {ep}'s proposals are gone: run propose-next for episode {ep - 1}.")
        try:
            item, _kind = _proposal_item(current, item_id)
        except WorkflowError:
            item = None
        if item != checked or item_id in current["decisions"]:
            raise WorkflowError(CONFLICT, (f"Episode {ep}'s proposals changed meanwhile: look at them again, then "
                                           "decide."))
        if request["kind"] == "twist" and accept:
            _update_season(stories, story_id, amend, now=now)  # the store lock is re-entrant
        current["decisions"][item_id] = request["decision"]
        return current

    try:
        stories.update_episode_doc(story_id, ep, PROPOSALS_DOC, decide, now=now)
    except KeyError:
        raise not_found() from None
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None
    except ValueError as exc:
        raise WorkflowError(CONFLICT, f"The proposals cannot be written: {exc}.") from None
    return request


def approve_proposals(stories, story_id, ep, *, now) -> dict:
    """Approve episode *ep*'s proposals (``proposals:<ep>``, the job of
    ``propose-next`` for *ep* - 1); returns them. Allowed once every item is
    accepted or rejected -- ``conflict`` naming the undecided ones, and
    without proposals. The decisions are the record: nothing is written (the
    document has no approval of its own), so *now* is unused."""
    story = load(stories, story_id)
    ep = episode_bounds(stories, story, ep)
    doc = _require_proposals(stories, story_id, ep)
    ids = [item["item_id"] for item in doc["characters"] + doc["twists"]]
    undecided = [item_id for item_id in ids if item_id not in doc["decisions"]]
    if undecided:
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s proposals are not all decided: accept or reject "
                                       f"{entities_step.quoted_list(undecided)} first."))
    return doc


def approve_series(stories, story_id, doc, *, direction=_NO_DIRECTION, now) -> dict:
    """The series part of the approve grammar: ``memory:<ep>``
    (:func:`approve_memory`), ``feedback:<ep>`` with *direction* -- required,
    None being "no direction" -- (:func:`approve_feedback`) and
    ``proposals:<ep>`` (:func:`approve_proposals`). ``not_found`` for any
    other document, ``invalid`` for a malformed episode, a feedback approval
    without *direction* or another approval with one."""
    if not is_series_approval(doc):
        raise WorkflowError(NOT_FOUND, f"Nothing to approve under {doc!r}.")
    word, _sep, rest = doc.partition(":")
    ep = episode_number(rest)
    if word == "feedback":
        if direction is _NO_DIRECTION:
            raise WorkflowError(INVALID, "Approving feedback:<ep> takes a direction: 0, 1, 2, or null for none.")
        return approve_feedback(stories, story_id, ep, direction=direction, now=now)
    if direction is not _NO_DIRECTION:
        raise WorkflowError(INVALID, f"A direction is chosen only when approving feedback:<ep>, not {doc}.")
    if word == "memory":
        return approve_memory(stories, story_id, ep, now=now)
    return approve_proposals(stories, story_id, ep, now=now)


def series_view(stories, story, ep) -> dict:
    """What an episode page shows of the series (stage 5 wires it)::

        {"ep", "memory": {"state": none|draft|approved|stale, "entry": entry | null},
         "feedback": item | null, "proposals": proposals.json | null}

    ``proposals`` are the ones *for* this episode (N1 of the episode
    before). Calls nothing; ``StoryUnreadable`` for a document that does not
    validate."""
    story_id = story["story_id"]
    ep = episode_bounds(stories, story, ep)
    arc = season(stories, story_id)
    script = read_episode(stories, story_id, ep, SCRIPT_DOC)
    item = feedback_step.feedback_item(arc, ep)
    return {
        "ep": ep,
        "memory": {"state": series_memory.memory_state(arc, ep, script), "entry": series_memory.entry_for(arc, ep)},
        "feedback": copy.deepcopy(item) if item is not None else None,
        "proposals": proposals(stories, story_id, ep),
    }


def next_episode_gate(stories, story, ep):
    """The gate's current refusal text (``episode_common.memory_refusal``'s
    own sentence) blocking episode *ep* + 1's script or storyboard because of
    episode *ep*'s series memory -- None once episode *ep* + 1 may be
    written, or when the season plans no episode after *ep* (stage 5: "why
    episode 2 is locked"). Read-only: episode *ep*'s script and the season as
    they are now; nothing is called and nothing is written."""
    story_id = story["story_id"]
    ep = episode_bounds(stories, story, ep)
    arc = season(stories, story_id)
    planned = arc["episodes_planned"] if arc else 0
    if ep >= planned:
        return None
    ns = types.SimpleNamespace(season=arc, store=stories, story_id=story_id)
    try:
        refusal = episode_common.memory_refusal(ns, ep, before=f"before writing episode {ep + 1}")
    except episode_common.EpisodeRefused as exc:
        return str(exc)
    return str(refusal) if refusal is not None else None


def series_page(stories, story, ep) -> dict:
    """:func:`series_view` plus :func:`next_episode_gate`, under
    ``"next_episode_gate"`` -- what both ``GET /{story_id}`` (once per
    planned episode) and ``GET /{story_id}/episodes/{ep}`` show of the
    series (stage 5). A new key only: :func:`series_view` itself, and its own
    test, are unchanged."""
    view = series_view(stories, story, ep)
    view["next_episode_gate"] = next_episode_gate(stories, story, ep)
    return view
