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
  ``{"message", "errors"}`` where the API answers with a list, or
  ``{"message", "code", ...}`` where a client acts on the refusal (the
  pipeline switch over written episodes: :func:`pipeline_switch_refusal`).
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
import dataclasses
import hashlib
import json
import os
import re
import threading
import types

from clipping.providers import budget as budget_mod
from clipping.providers import gating, gen_timings
from clipping.providers import generation as gen
from clipping.providers import registry

from . import (
    defaults,
    imaging,
    media_policy,
    platforms,
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
    voice_reference,
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
from .steps import gates as gates_step
from .steps import judge as judge_step
from .steps import knowledge as knowledge_step
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
from .steps import bible as bible_step
from .steps import story_fast_track as agent_step
from .steps import style_preview as preview_step
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
# small P0 step that proposes the list the ``places`` step makes. Phase 7
# stage 5b (DEC-228): ``knowledge``, a v2 story's knowledge base, after the
# season (D4, D5 per episode, D6; ending awaiting its approval).
PHASE2_STEPS = ("cast", "places_proposal", "places", "season", "knowledge")
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
# ``proposals:<ep>`` (:data:`SERIES_APPROVALS`, :func:`approve_series`);
# phase 7 stage 5b ``knowledge`` bare (:func:`approve_knowledge`, a v2 story);
# phase 7 stage 6b ``keyframes:<ep>`` (:func:`approve_keyframes`, a v2 story:
# no step job writes it, so it is not one of :data:`EPISODE_APPROVALS`).
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
# A v2 episode's keyframe approval (phase 7 stage 6b, DEC-230).
KEYFRAMES_APPROVAL = judge_step.KEYFRAMES_APPROVAL
# A character's appearance variant's own approval (plan 23 stage D5):
# ``variant:<char_id>:<variant_id>`` (:func:`approve_variant`); the variant's
# sheets job (``character:<id>:variant:<vid>``) awaits it.
VARIANT_APPROVAL = "variant"
# Who recorded an approval of the assets or the keyframes (stage C,
# ``schemas.APPROVED_BY``): the human, or the fast track's one click.
USER_APPROVED, FAST_TRACK_APPROVED = schemas.APPROVED_BY
# Plan 21 stage 1 (agent mode): the agent run's own mark on the story
# documents it approves (``schemas.AGENT_APPROVED``: story.json's
# ``approved_by`` map, and ``approved_by`` on a character, a place, a prop,
# the season arc and the knowledge base). The episode documents of its
# episode 1 are approved by the fast track it hands over to, as
# ``FAST_TRACK_APPROVED``.
AGENT_APPROVED = schemas.AGENT_APPROVED
STORY_APPROVERS = (USER_APPROVED, AGENT_APPROVED)

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
    ``detail`` says why: a sentence, or ``{"message", "errors"}``, or
    ``{"message", "code", ...}`` (a refusal a client acts on)."""

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


# ------------------------------------------------------- who approved (plan 21)

def _check_approver(by) -> None:
    """*by* is the human (``USER_APPROVED``) or the agent run
    (``AGENT_APPROVED``); a programming error otherwise."""
    if by not in STORY_APPROVERS:
        raise ValueError(f"a story approval is given by one of {', '.join(STORY_APPROVERS)}, not {by!r}")


def _mark_story_approval(doc, key, by) -> None:
    """story.json's ``approved_by[key]``: set for the agent run, removed for
    the human (the map itself removed once empty, so a Studio story's
    story.json never gains the key). In place."""
    marks = dict(doc.get("approved_by") or {})
    if by == AGENT_APPROVED:
        marks[key] = AGENT_APPROVED
    else:
        marks.pop(key, None)
    if marks:
        doc["approved_by"] = marks
    else:
        doc.pop("approved_by", None)


def _mark_doc_approval(doc, by) -> None:
    """A document's own ``approved_by``: set for the agent run, removed for
    the human. In place."""
    if by == AGENT_APPROVED:
        doc["approved_by"] = AGENT_APPROVED
    else:
        doc.pop("approved_by", None)


def approved_by(doc, key=None):
    """Who gave the approval that stands now: ``AGENT_APPROVED`` when the
    agent run recorded it, ``USER_APPROVED`` otherwise, None while there is
    none. *doc* is story.json with *key* (``concept``, ``bible``, ``style``,
    ``season``), or a document with its own ``approved_at`` (a character, a
    place, a prop, the season arc, the knowledge base) with no *key*."""
    if doc is None:
        return None
    if key is None:
        if not doc.get("approved_at"):
            return None
        return AGENT_APPROVED if doc.get("approved_by") == AGENT_APPROVED else USER_APPROVED
    if not (doc.get("approvals") or {}).get(key):
        return None
    return AGENT_APPROVED if (doc.get("approved_by") or {}).get(key) == AGENT_APPROVED else USER_APPROVED


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


def choose_concept(stories, story_id, *, concept_id=None, concept=None, now, by=USER_APPROVED) -> dict:
    """Choose the story's concept; returns the story.

    Exactly one of *concept_id* (a library id, or a generated card's
    ``gen_NN``) and *concept* (a card written by the user, checked with the
    generated-card rules) -- ``invalid`` otherwise; ``not_found`` for an id
    that is neither.

    Writes a snapshot of the concept in the story's language, ``concept_id``
    (the library id, or ``"custom"``: the snapshot of a generated card keeps
    its ``gen_NN``), the concept's title, and ``approvals.concept``; a
    different concept than before clears the bible approval. *by* (plan 21):
    the human (default) or the agent run, recorded beside the approval
    (:func:`_mark_story_approval`).
    """
    _check_approver(by)
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
        _mark_story_approval(doc, "concept", by)

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

    if consistency == refimages.PROMPT_ONLY and media_policy.is_v2(story):
        raise WorkflowError(
            INVALID,
            "consistency_mode must stay references on a v2 story: it never falls back to prompt-only "
            "consistency (DEC-221).",
        )

    current = style_lock(stories, story_id)
    if current is not None and current.get("locked_at"):
        raise WorkflowError(
            CONFLICT,
            f"The style is locked (since {current['locked_at']}); it cannot change.",
        )

    v2 = media_policy.is_v2(story)
    # A draft stays on the same template/version takes the new overrides on
    # top of its own; anything else is built fresh and its old overrides are
    # discarded (the docstring above) -- the v2 subtitle default below only
    # applies on a fresh build, so it never clobbers a user's own choice on
    # an otherwise-unrelated edit (e.g. a palette tweak).
    fresh = not (current is not None and current.get("template_id") == template_id
                 and current.get("template_version") == template["version"])
    if v2 and fresh:
        # Two-line subtitles by default on a v2 story (phase 7 stage 6c, the
        # human's CLARIFY answer 11); an explicit override in this same call
        # wins (``setdefault``), and legacy keeps the template's own default.
        overrides = dict(overrides)
        overrides.setdefault("typography.subtitle_mode", "two_line")

    try:
        if fresh:
            lock = stylelock.build_style_lock(template, overrides, now=now)
        else:
            lock = stylelock.apply_overrides(current, overrides, now=now)
    except stylelock.StyleLockError as exc:
        raise WorkflowError(
            INVALID,
            {"message": f"The style was refused: {exc.name}.", "errors": list(exc.errors)},
        ) from None

    if v2:
        # Not a user-facing override (CLARIFY answer 11's 150 ms floor has
        # no entry in ``stylelock.OVERRIDABLE``): kept on every v2 build, so
        # an edit to another override never silently drops it. Read by
        # render/subtitles.py's word_pop_dialogue only when present (RC-M2:
        # the golden fixture's STYLE_LOCK has none, so it stays untouched).
        lock["typography"]["word_min_card_ms"] = 150

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


def approve_bible(stories, story_id, *, now, by=USER_APPROVED) -> dict:
    """Set ``approvals.bible``; returns the story. ``conflict`` without a
    chosen concept, or listing every bible field still missing or empty.
    *by* (plan 21): the human (default) or the agent run."""
    _check_approver(by)
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
        _mark_story_approval(doc, "bible", by)

    return update(stories, story_id, mutate, now=now)


def approve_style(stories, story_id, *, now, by=USER_APPROVED) -> dict:
    """Freeze the draft style lock (``locked_at``) and set
    ``approvals.style``; returns the story. ``conflict`` without a
    ``style_lock.json``, or when it is already locked. *by* (plan 21): the
    human (default) or the agent run."""
    _check_approver(by)
    current = style_lock(stories, story_id)
    if current is None:
        raise WorkflowError(CONFLICT, "There is no style to approve yet: run the style step first.")
    if current.get("locked_at"):
        raise WorkflowError(
            CONFLICT,
            f"The style is already locked (since {current.get('locked_at')}).",
        )
    # Plan 23 stage D4: "all skin is the character's matter" is written into the lock now, once
    # (a lock already frozen is never touched); any other rule leaves the template's own.
    story = load(stories, story_id)
    body = media_policy.body_rule(story, current["template_id"])
    # Plan 23 stage D2: a story with a universe records it in the lock, and its material rule
    # fills the all_matter rule's slot.
    chosen = media_policy.universe(story, current["template_id"], explicit=True)
    universe = templates.universe(chosen) if chosen else None
    try:
        locked = stylelock.lock_style(current, now=now, body_rule=body, universe=universe,
                                      material=universe["material_rule"] if universe else None)
    except stylelock.StyleLockError as exc:
        if body == defaults.BODY_ALL_MATTER:
            raise WorkflowError(
                CONFLICT,
                (f"This story draws every body in the character's own matter (Bodies: all skin is the "
                 f"character's matter), which the {current['template_id']} style does not define: pick a style "
                 "that does, or set Bodies back to as the style draws them in the generation profile.")) from None
        raise WorkflowError(
            CONFLICT,
            f"The style is already locked (since {current.get('locked_at')}).",
        ) from None
    write_doc(stories, story_id, STYLE_LOCK_DOC, locked, now=now,
              validator=schemas.style_lock_errors)

    def mutate(doc):
        doc["approvals"]["style"] = now
        _mark_story_approval(doc, "style", by)

    return update(stories, story_id, mutate, now=now)


# ------------------------------------------------------------------ patch

# Plan 23 stage B7: the frame is set once, when the story is made.
ASPECT_FROZEN = ("the frame is chosen when the story is made: its plates, keyframes and clips depend on it "
                 "(make a new story for another frame)")


def check_aspect_unchanged(story, partial) -> None:
    """``conflict`` (:data:`ASPECT_FROZEN`) when the profile patch *partial*
    changes *story*'s frame (``generation_profile.aspect``: absent, null and
    ``"9:16"`` all mean 9:16), or moves a 16:9 / 1:1 story off the v2
    pipeline it was made on; resending the frame it has changes nothing."""
    if not isinstance(partial, dict):
        return
    current = media_policy.aspect(story)
    if "aspect" in partial:
        sent = partial["aspect"]
        sent = defaults.ASPECT_PORTRAIT if sent is None else sent
        if sent != current:
            raise WorkflowError(CONFLICT, f"The story's frame is {current}: {ASPECT_FROZEN}.")
    if current != defaults.ASPECT_PORTRAIT and "pipeline" in partial and partial["pipeline"] != defaults.PIPELINE_V2:
        raise WorkflowError(CONFLICT, f"A {current} story stays on the v2 pipeline: {ASPECT_FROZEN}.")


def _without_null_aspect(partial):
    """*partial* without an ``aspect: null`` (a 9:16 story saying it is 9:16,
    :func:`check_aspect_unchanged` passed): nothing to merge."""
    if isinstance(partial, dict) and "aspect" in partial and partial["aspect"] is None:
        return {key: value for key, value in partial.items() if key != "aspect"}
    return partial


def patch_story(stories, story_id, fields, *, now) -> dict:
    """Edit the story fields in *fields*, and only those; returns the story.

    Nothing sent, nothing written. A field outside ``PATCH_FIELDS`` is
    ``invalid``. A bible field clears ``approvals.bible`` (a changed bible is
    an unapproved one); ``title``, ``seed_text``, ``narrator``,
    ``generation_profile`` and ``episode_template_id`` leave the approvals
    alone. ``narrator`` and ``generation_profile`` are merged onto the current
    values, the profile checked against ``clipping.aistory.defaults``
    (``invalid``); a profile moving the story onto or off the v2 pipeline
    takes the template and the narrator with it, and is ``conflict`` while
    an episode has a script (:func:`_follow_pipeline_switch`;
    :func:`switch_pipeline` can archive them). ``episode_template_id`` is one of
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
        check_aspect_unchanged(story, partial)
        partial = _without_null_aspect(partial)
        try:
            # The store's own check, on the current profile with the sent keys over it.
            values["generation_profile"] = story_store._merge_generation_profile(
                {**story["generation_profile"], **partial})
            # Plan 23 stage D2: a universe the story's style accepts.
            story_store.check_universe(values["generation_profile"], story.get("style_template_id"))
            # Plan 23 stage B7: a profile that can still make the story's frame (9:16: always).
            refusal = media_policy.aspect_refusal(values["generation_profile"])
            if refusal:
                raise ValueError(f"this story's frame is {media_policy.aspect(story)}: {refusal}")
        except ValueError as exc:
            raise WorkflowError(INVALID, str(exc)) from None
        _follow_pipeline_switch(stories, story, values)

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


def prompt_style_change(stories, story_id, fields) -> dict | None:
    """What a story PATCH *fields* that changes ``generation_profile.prompt_style``
    does to the clips already made (plan 23 stage D6), asked BEFORE the patch is
    applied: ``{"stale_clips": N, "warning": sentence | None}``, N the clips that
    are current now (an uploaded one too: ``clips.clip_state`` compares the
    prompt's hash) and would be stale under the new style; None when the patch
    does not change the style (or does not name a valid profile: the patch
    itself refuses that). Reads only; nothing is written."""
    story = load(stories, story_id)
    partial = (fields or {}).get("generation_profile")
    if not isinstance(partial, dict) or "prompt_style" not in partial:
        return None
    try:
        profile = story_store._merge_generation_profile({**story["generation_profile"], **partial})
    except ValueError:
        return None
    changed = dict(story, generation_profile=profile)
    before, after = media_policy.prompt_style(story), media_policy.prompt_style(changed)
    if before == after:
        return None
    stale = 0
    for ep in stories.list_episodes(story_id):
        board = read_episode(stories, story_id, ep, STORYBOARD_DOC)
        script = read_episode(stories, story_id, ep, SCRIPT_DOC)
        if board is None or script is None or not board.get("shots"):
            continue
        try:
            ec = _context(stories, story_id, ep)
        except WorkflowError:
            continue
        doc = read_episode(stories, story_id, ep, ASSETS_DOC)
        tier = clips_step.tier_of(ec)
        link = (sticky_link.recorded(doc, sticky_link.VIDEO) or {}).get("link")
        later = dataclasses.replace(ec, story=changed)
        for shot in board["shots"]:
            if not (shot["assets"].get("clip") or {}).get("prompt_hash"):
                continue
            image = assets_step.shot_image_path(ec, shot)
            sha = assets_step._sha256_file(image) if image is not None else None
            kwargs = dict(link=clips_step.class_link(ec.story, shot, doc, link), tier=tier,
                          flags=clips_step.shot_flags(shot, doc), image_sha=sha)
            try:
                if (clips_step.clip_state(ec, shot, script, **kwargs) == "current"
                        and clips_step.clip_state(later, shot, script, **kwargs) != "current"):
                    stale += 1
            except (KeyError, ValueError):
                continue
    if not stale:
        return {"stale_clips": 0, "warning": None}
    noun = "clip" if stale == 1 else "clips"
    return {"stale_clips": stale,
            "warning": (f"Switching the clip prompts to {after} rewrites every clip's prompt: {stale} current {noun} "
                        f"(uploads included) will be marked stale and must be made again.")}


def set_subtitle_style(stories, story_id, style, *, now) -> dict:
    """Set the story's own subtitle look, or clear it with ``None`` (plan 23
    stage B5; ``PATCH /api/stories/{id}/subtitle-style``); returns the story.

    A render-only setting, so it is allowed at any time -- also after the
    style lock froze -- and clears no approval; the caller refuses it while a
    render of the story runs. ``invalid`` with ``{"message", "errors"}`` when
    ``subtitle_style.validate`` refuses it (a range, a font the app does not
    ship, a text/outline or text/box contrast below 4.5 -- the message names
    the ratio); nothing is written then."""
    load(stories, story_id)
    try:
        return stories.set_subtitle_style(story_id, style, now=now)
    except KeyError:
        raise not_found() from None
    except schemas.SchemaError as exc:
        if exc.name == "subtitle_style":
            raise _invalid_values("The subtitle style is not valid: " + "; ".join(exc.errors), exc.errors) from None
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None


def _follow_pipeline_switch(stories, story, values) -> None:
    """When the patch *values* move *story* onto or off the v2 pipeline
    (``generation_profile.pipeline``), make the rest of the story what
    ``store.create`` gives a story of that pipeline, in place: the episode
    template (``defaults.episode_template_for``) unless the patch names one,
    and ``narrator.enabled`` (on for v2) unless the patch sets it.

    ``conflict`` once an episode has a script: it was written for the other
    pipeline's shot layout and keeps its template (:func:`check_episode_template`)
    -- a structured refusal (:func:`pipeline_switch_refusal`) naming the
    episodes, which :func:`switch_pipeline` can archive instead.

    The cast, places and props made before the switch stay. On v2 the cast
    step run again writes each character's dossier and look (DEC-226,
    DEC-228) and, a character whose images were drawn before it had a look,
    draws its portrait and sheets again from the look (``cast.apply_d2``);
    the places step does the same for a place's plate and a prop's image
    (``places.apply_d3``, ``places.apply_r1v2``). Each estimate counts those
    images first (:func:`cast_units`, :func:`places_units`). Nothing is drawn
    by the switch itself. A patch that keeps the pipeline changes nothing
    here."""
    profile = values["generation_profile"]
    v2 = media_policy.is_v2({"generation_profile": profile})
    if v2 == media_policy.is_v2(story):
        return
    written = episodes_with_script(stories, story["story_id"])
    if written:
        raise pipeline_switch_refusal(v2, written)
    values.setdefault("episode_template_id", defaults.episode_template_for(profile))
    narrator = values.get("narrator")
    if not (isinstance(narrator, dict) and "enabled" in narrator):
        values["narrator"] = {**(narrator if isinstance(narrator, dict) else {}), "enabled": v2}


# The code of the structured refusal of a pipeline switch over written
# episodes (the dashboard offers to regenerate them: switch_pipeline).
PIPELINE_SWITCH_HAS_SCRIPTS = "pipeline_switch_has_scripts"


def pipeline_switch_refusal(v2, written) -> WorkflowError:
    """``conflict`` with ``{"message", "code": PIPELINE_SWITCH_HAS_SCRIPTS,
    "episodes": [...]}``: the move onto (*v2*) or off the v2 pipeline is
    refused while the episodes *written* have a script; the sentence names
    them and the two ways out -- regenerate them on the new pipeline
    (:func:`switch_pipeline`, which archives them), or a new story."""
    written = sorted(written)
    target = "the v2 (quality) pipeline" if v2 else "the legacy pipeline"
    pipeline = "the v2 pipeline" if v2 else "the legacy pipeline"
    if len(written) == 1:
        has, regenerate, its = f"episode {written[0]} already has a script", "Regenerate the episode", "its"
    else:
        has = f"episodes {_and(str(ep) for ep in written)} already have a script"
        regenerate, its = "Regenerate those episodes", "their"
    message = (f"This story cannot move to {target}: {has}, written for the other pipeline's shot layout. "
               f"{regenerate} on {pipeline} ({its} script, storyboard, images, clips and render are archived), "
               "or create a new story.")
    return WorkflowError(CONFLICT, {"message": message, "code": PIPELINE_SWITCH_HAS_SCRIPTS,
                                    "episodes": list(written)})


def switch_pipeline(stories, story_id, profile_patch, *, regenerate_episodes, now) -> dict:
    """Patch the story's ``generation_profile`` with *profile_patch* as
    :func:`patch_story` does (merged onto the current profile, checked, the
    template and the narrator following a pipeline switch), with one way
    past its refusal: when the patch moves the story onto or off the v2
    pipeline while episodes have a script, and *regenerate_episodes* is
    true, each of those episodes is archived first
    (``StoryStore.discard_episode``: its folder moved into
    ``episodes/_discarded/``, its memory entry, feedback and proposals
    cleared, its spend kept for the story but no longer for the episode),
    the latest first -- so no later memory entry is left closing a hook
    an archived one opened -- and the story then switches as an unwritten
    one does. The cast, places, props, season and music stay.

    Without *regenerate_episodes* the refusal is :func:`patch_story`'s
    (:func:`pipeline_switch_refusal`). The profile is checked before any
    episode moves (``invalid``). Returns ``{"story", "discarded":
    [discard_episode's reports, by episode]}``. The step jobs of the
    archived episodes, and the one that comes next, are the caller's
    (``POST /{id}/switch-pipeline``)."""
    story = load(stories, story_id)
    if not isinstance(profile_patch, dict):
        raise WorkflowError(INVALID, "generation_profile must be an object.")
    check_aspect_unchanged(story, profile_patch)
    profile_patch = _without_null_aspect(profile_patch)
    try:
        profile = story_store._merge_generation_profile({**story["generation_profile"], **profile_patch})
    except ValueError as exc:
        raise WorkflowError(INVALID, str(exc)) from None
    discarded = []
    v2 = media_policy.is_v2({"generation_profile": profile})
    if v2 != media_policy.is_v2(story):
        written = episodes_with_script(stories, story_id)
        if written and not regenerate_episodes:
            raise pipeline_switch_refusal(v2, written)
        for ep in sorted(written, reverse=True):
            discarded.append(_discard_episode(stories, story_id, ep, now=now))
    story = patch_story(stories, story_id, {"generation_profile": copy.deepcopy(profile_patch)}, now=now)
    return {"story": story, "discarded": sorted(discarded, key=lambda report: report["ep"])}


def _discard_episode(stories, story_id, ep, *, now) -> dict:
    """``StoryStore.discard_episode``, its refusals as the workflow's."""
    try:
        return stories.discard_episode(story_id, ep, now=now)
    except KeyError:
        raise WorkflowError(NOT_FOUND, f"Episode {ep} has no folder to archive.") from None
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None
    except ValueError as exc:
        raise WorkflowError(CONFLICT, f"Episode {ep} cannot be archived: {exc}") from None


# What a v2 story needs, in order, before its first episode can be written
# (a legacy story moved onto v2: next_v2_step).
V2_STORY_STEPS = ("cast", "places", "knowledge")


def next_v2_step(stories, story):
    """The story-level step a v2 story still needs before an episode can be
    written, the first of :data:`V2_STORY_STEPS` -- what a legacy story moved
    onto v2 must run again (``POST /{id}/switch-pipeline`` queues it) -- or
    None: ``cast`` while a character has no dossier or no look (its images
    drawn before the look are drawn again from it: ``cast.apply_d2``);
    ``places`` while a place or a prop has no look; ``knowledge`` while the
    season is approved and the knowledge base is not complete
    (``knowledge.left``: approving it is the user's, no step). None for a
    legacy story. Calls nothing."""
    if not media_policy.is_v2(story):
        return None
    story_id = story["story_id"]
    if any(not doc.get("dossier") or not doc.get("look") for doc in list_entities(stories, story_id, CHARACTERS)):
        return "cast"
    if any(not doc.get("look") for kind in (PLACES, PROPS) for doc in list_entities(stories, story_id, kind)):
        return "places"
    if (story.get("approvals") or {}).get("season"):
        arc = season(stories, story_id)
        todo = knowledge_step.left(knowledge(stories, story_id), arc["episodes_planned"] if arc else 0)
        if any(todo.values()):
            return "knowledge"
    return None


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
KNOWLEDGE_DOC = story_store.KNOWLEDGE_DOC

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
KNOWLEDGE_PARAMS = ()

# The fields an inline edit may set (the API's Character/Place/PropPatchRequest).
# ``look`` and ``dossier`` (phase 7 stage 7, A19): the optional blocks the v2
# writers produce (D1, D2, D3, R1v2), edited on a v2 story only.
CHARACTER_PATCH_FIELDS = (
    "name", "role", "archetype", "one_line", "descriptor", "signature_items", "personality",
    "voice_direction", "sample_line", "rate", "pitch", "look", "dossier",
)
PLACE_PATCH_FIELDS = ("name", "one_line", "descriptor", "layout_notes", "look")
PROP_PATCH_FIELDS = ("name", "one_line", "descriptor", "owner_char_id", "look")
PATCH_FIELDS_BY_KIND = {CHARACTERS: CHARACTER_PATCH_FIELDS, PLACES: PLACE_PATCH_FIELDS, PROPS: PROP_PATCH_FIELDS}
# The v2 blocks among them: each merged onto the entity's current block, as
# ``personality`` is.
V2_BLOCK_FIELDS = ("look", "dossier")

# What a knowledge-base edit may set (the API's KnowledgePatchRequest, phase 7
# stage 7): the world (merged), timeline beats named by episode and 1-based
# position (each a ``KNOWLEDGE_BEAT_PATCH_FIELDS`` subset), the props registry
# (the list as a whole) and ledger-seed entries (merged per character).
KNOWLEDGE_PATCH_FIELDS = ("world", "beats", "props_registry", "ledger_seed")
KNOWLEDGE_BEAT_PATCH_FIELDS = ("what", "place_id", "who", "objects", "knows_after")

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


def knowledge(stories, story_id):
    """The story's ``knowledge.json`` (phase 7 stage 5b), or None before the
    knowledge step (every legacy story)."""
    return read_doc(stories, story_id, KNOWLEDGE_DOC, schemas.knowledge_errors)


# ------------------------------------------------------------ what is missing

def _has(stories, story_id, kind, eid, ref) -> bool:
    return entities_step.has_file(stories, story_id, kind, eid, ref)


def character_written(doc) -> bool:
    """K1 has written it: a descriptor and 2-3 signature items."""
    low, high = schemas.SIGNATURE_ITEMS_WRITTEN
    return bool(doc["descriptor"]) and low <= len(doc["signature_items"]) <= high


def character_missing(stories, story_id, doc, *, story=None) -> list:
    """What the character still lacks before it can be approved, in order:
    ``text`` (a descriptor and 2-3 signature items), ``portrait``,
    ``turnaround``, ``expressions`` (whatever their consistency label), ``voice``
    (a pinned voice) and ``sample`` (its voice sample, on disk). An image or a
    sample counts only as a regular file where the store keeps it. Plan 23
    stage D4: only the images the story's ``sheet_mode`` draws
    (``refimages.character_images``; read from *story*, else from the store)."""
    cid = doc["char_id"]
    missing = [] if character_written(doc) else ["text"]
    if story is None:
        story = stories.get(story_id)
    for which in refimages.character_images(story):
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
    and the CLI's gate ask this same question. A v2 story's portraits, plates
    and props are asked of the ``sheet`` role's quality links (phase 7: the
    three roles share one table row today); a legacy story's of IMAGE_CHAIN."""
    width, height = refimages.PORTRAIT_SIZE
    return imaging.estimate(
        gen.IMAGE, env, route=story["generation_profile"]["route"],
        request=gen.GenRequest(kind=gen.IMAGE, width=width, height=height), qty=qty,
        story_spent=cost_total(stories, story["story_id"]), step="image",
        what="a reference image", when="the step runs", role="sheet", story=story,
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


def _would_cost(verdict):
    """``{qty, usd, link}`` an image verdict (:func:`image_verdict`,
    :func:`edit_readiness`) would spend if the budget let it run: its first
    runnable link's estimate; for a verdict blocked by the daily cap alone
    (``imaging.verdict``'s ``budget`` block), its first refused link's; else
    None -- the step stops another way (no link, ``allow_paid`` off, another
    cap) and that refusal is the verdict's own."""
    qty = (verdict.get("units") or {}).get("images", 0)
    if verdict.get("ready"):
        return {"qty": qty, "usd": round(float(verdict.get("est_usd") or 0.0), 6), "link": verdict.get("link")}
    if verdict.get("budget"):
        row = imaging.day_refused_rows(verdict.get("links") or [])[0]
        return {"qty": qty, "usd": round(float(row["est_usd"] or 0.0), 6), "link": row["link"]}
    return None


def _refused_row(verdict) -> bool:
    """Whether a link of *verdict* was refused by the budget (a ``refused: ``
    reason: ``allow_paid`` off or a cap)."""
    return any(row.get("status") == "skipped" and str(row.get("reason") or "").startswith("refused: ")
               for row in verdict.get("links") or ())


def generation_budget(stories, story, units, images, edit, *, env) -> dict:
    """What a step that makes images would spend, in one sum, and the one
    budget check of that sum (plan 23 A5) -- the total the estimate shows
    and the gate checks before the job exists (RC-V6: one function, two
    callers)::

        {"usd", "images": {qty, usd, link} | None, "edits": {qty, usd, link} | None,
         "refusal": <BudgetRefused.as_dict() + "message"> | None,
         "edit_refused": bool, "blocks": bool, "message": str | None}

    *units* are :func:`cast_units` / :func:`places_units` /
    :func:`target_units`; *images* the image chain's verdict on
    ``units["images"]`` (:func:`image_verdict`), *edits* the editor's on
    ``units["edit_images"]`` (:func:`edit_readiness`), either None when the
    step makes none. The images count when their link can run (or the daily
    cap alone refuses it); the edits when the editor can run -- and, on a v2
    story, when the daily cap alone refuses it too, since a v2 cast buys its
    portraits and its sheets as one. ``refusal`` is ``budget.check`` on the
    sum with the story's ledger total and today's spend and extra.

    ``edit_refused``: a v2 story whose editor has a ``refused: `` row (the
    budget refuses an edit). ``blocks``: a v2 story the gate refuses before
    any portrait is bought -- ``refusal`` or ``edit_refused``; ``message``
    then the sentence of a refusal other than the daily cap's. A legacy story
    never blocks here: DEC-117's stop-and-ask before its edits stays the
    step's. Books nothing, calls nothing; ``refimages`` still checks each
    image as it runs, so every dollar is checked twice and booked once."""
    v2 = media_policy.is_v2(story)
    image_part = _would_cost(images) if images is not None and units.get("images") else None
    edit_part = None
    if edit is not None and units.get("edit_images"):
        if edit.get("ready") or (v2 and edit.get("budget")):
            edit_part = _would_cost(edit)
    usd = round(sum(part["usd"] for part in (image_part, edit_part) if part), 6)

    things = []
    if image_part:
        things.append(f"{image_part['qty']} reference image{'' if image_part['qty'] == 1 else 's'}")
    if edit_part:
        things.append(f"{edit_part['qty']} edit{'' if edit_part['qty'] == 1 else 's'}")
    label = " and ".join(things) or "this step's images"
    refusal = None
    try:
        budget_obj = gating.budget_of(gating.merged_env(env))
    except ValueError:
        budget_obj = None  # the verdicts already say why; nothing more to check
    if budget_obj is not None and usd > 0:
        state = budget_mod.day_state()
        try:
            budget_mod.check(types.SimpleNamespace(est_usd=usd, link=label), None, budget=budget_obj,
                             day_spent=state.spent, day_extra=state.extra,
                             story_spent=cost_total(stories, story["story_id"]))
        except budget_mod.BudgetRefused as exc:
            refusal = dict(exc.as_dict(), message=str(exc))

    edit_refused = bool(v2 and edit is not None and units.get("edit_images") and not edit.get("ready")
                        and _refused_row(edit))
    blocks = bool(v2 and (refusal or edit_refused))
    message = None
    if v2 and refusal:
        message = (f"The {label} would go over a cap, so nothing would be generated or spent: "
                   f"{refusal['message']}.")
    return {"usd": usd, "images": image_part, "edits": edit_part, "refusal": refusal,
            "edit_refused": edit_refused, "blocks": blocks, "message": message}


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

    char_missing = {doc["char_id"]: character_missing(stories, story_id, doc, story=story) for doc in characters}
    sheet_slots = refimages.character_sheets(story)
    sheets = sum(1 for missing in char_missing.values() for sheet in sheet_slots if sheet in missing)
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
                 and any(sheet in missing for sheet in sheet_slots))
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


def require_knowledge_runnable(story) -> None:
    """The knowledge step (phase 7 stage 5b) runs on a v2 story whose season
    is approved (``knowledge.require_runnable``'s rule): ``conflict``
    otherwise, with its sentence."""
    _step_refusal(knowledge_step.require_runnable, story)


def knowledge_request(params) -> None:
    """A knowledge step's *params*: none (``invalid`` otherwise)."""
    _unknown_keys(params, KNOWLEDGE_PARAMS, "knowledge")


def knowledge_calls(stories, story) -> int:
    """How many LLM calls the knowledge step would make now (D4, D5 per
    episode not written, D6): the estimate's count."""
    arc = season(stories, story["story_id"])
    planned = arc["episodes_planned"] if arc else 0
    return knowledge_step.calls_left(knowledge(stories, story["story_id"]), planned)


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
    ``references`` mode and text-to-image in ``prompt_only`` mode. A v2
    story (phase 7) also counts D1 and D2 for each character with no
    dossier and no look yet -- and, for one with no look, its portrait and
    both sheets whether they are there or not: drawn without a look (a
    legacy story moved onto v2), they are drawn again once it is written
    (``cast.apply_d2``)."""
    story_id = story["story_id"]
    prompt_only = story["generation_profile"]["consistency_mode"] == refimages.PROMPT_ONLY
    v2 = media_policy.is_v2(story)
    units = _units()
    # Plan 23 stage D4: the sheets the story's sheet_mode draws from the portrait (none in two_view).
    sheet_slots = refimages.character_sheets(story)

    def sheets(count):
        units["images" if prompt_only else "edit_images"] += count

    existing = list_entities(stories, story_id, CHARACTERS)
    names = {entities_step.name_key(doc["name"]) for doc in existing}
    for doc in existing:
        missing = character_missing(stories, story_id, doc, story=story)
        redrawn = v2 and not doc.get("look")
        units["llm_calls"] += "text" in missing
        units["llm_calls"] += v2 and not doc.get("dossier")
        units["llm_calls"] += redrawn
        units["images"] += redrawn or "portrait" in missing
        sheets(sum(redrawn or sheet in missing for sheet in sheet_slots))
        if "sample" in missing:
            units["tts_chars"] += _sample_chars(doc)
    for name in list(selected) + [entry.get("name") for entry in custom]:
        key = entities_step.name_key(name)
        if key in names:
            continue
        names.add(key)
        units["llm_calls"] += 3 if v2 else 1
        units["images"] += 1
        sheets(len(sheet_slots))
        units["tts_chars"] += SAMPLE_CHARS_ESTIMATE
    return units


def places_units(stories, story, params=None) -> dict:
    """What a places step would make: for every place and prop of the story,
    P1/R1 when its text is missing and its day plate or image when missing;
    each item of the list (*params*, else the saved proposal) not created yet
    counts fully (one call, one image). Time variants are made on demand
    (``place:<id>:image:<variant>``) and are not counted. A v2 story (phase
    7) also counts D3 / R1v2 for each place and prop with no look yet, and
    its day plate or image whether it is there or not: drawn without a look
    (a legacy story moved onto v2), it is drawn again once the look is
    written (``places.apply_d3``, ``places.apply_r1v2``)."""
    story_id = story["story_id"]
    v2 = media_policy.is_v2(story)
    params = params or {}
    if params.get("places") is None and params.get("props") is None:
        params = places_proposal(stories, story_id) or {}
    units = _units()
    for kind, key in ((PLACES, "places"), (PROPS, "props")):
        existing = list_entities(stories, story_id, kind)
        names = {entities_step.name_key(doc["name"]) for doc in existing}
        for doc in existing:
            missing = MISSING[kind](stories, story_id, doc)
            redrawn = v2 and not doc.get("look")
            units["llm_calls"] += "text" in missing
            units["llm_calls"] += redrawn
            units["images"] += 1 if redrawn else len(missing) - ("text" in missing)
        for item in params.get(key) or ():
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(name, str) or entities_step.name_key(name) in names:
                continue
            names.add(entities_step.name_key(name))
            units["llm_calls"] += 2 if v2 else 1
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
    if parsed[0] in regenerate_step.EPISODE_KINDS or parsed[0] == "season":
        return _units(llm_calls=1)
    if parsed[2] == "text":
        # A v2 story's entity text is written again with its look, a character's
        # with its dossier too (phase 7).
        if not media_policy.is_v2(story):
            return _units(llm_calls=1)
        return _units(llm_calls=3 if ENTITY_KINDS_BY_WORD[parsed[0]] == CHARACTERS else 2)
    if parsed[2] == regenerate_step.VARIANT_WORD:
        return variant_units(story)
    kind = ENTITY_KINDS_BY_WORD[parsed[0]]
    doc = read_entity(stories, story["story_id"], kind, parsed[1])
    if parsed[2] == "voice":
        return _units(tts_chars=_sample_chars(doc))
    slot = parsed[3] if len(parsed) > 3 else "image"
    if slot in ("portrait", MASTER_PLATE, "image"):
        units = _units(images=1)
        if slot == "portrait":
            again = sum(1 for sheet in refimages.character_sheets(story) if doc["refs"][sheet] is not None)
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
    if parsed[0] == "character" and parsed[2] == regenerate_step.VARIANT_WORD:
        return True  # plan 23 stage D5: every variant sheet is an edit of the base portrait
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
        if voice_reference.is_reference_voice(doc["voice"]):
            continue  # a character's own recording is its alone: never "taken" (plan 23 stage B4)
        taken.setdefault((doc["voice"]["provider"], doc["voice"]["voice_id"]), doc["name"])
    return taken


def _voice_json(voice, *, env=None) -> dict:
    body = {
        "provider": voice.provider, "voice_id": voice.voice_id, "lang": voice.lang,
        "gender": voice.gender, "age": voice.age, "style_tags": list(voice.style_tags),
        "link": registry.describe(voice.link),
    }
    if voice.paid:
        # Plan 23 stage B3: a paid voice carries its price for a reference
        # episode and the gates' verdict (allow_paid, caps), for the cast step's
        # badge; a free voice's payload is exactly what it was.
        body.update(paid=True, **voices.paid_voice_summary(voice, env=env))
    return body


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
    pool = voices.alternates(doc, story["language"], env=env, taken=taken | exclude_own,
                             v2=media_policy.is_v2(story))
    return {
        "pinned": pinned,
        "alternates": [_voice_json(voice, env=env) for voice in pool],
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
    if (provider, voice_id) == (voice_reference.REFERENCE_PROVIDER, voice_reference.REFERENCE_VOICE_ID):
        # The character's own recording (plan 23 stage B4): not a catalogue
        # voice and never shared, but it needs the recording and chatterbox.
        try:
            voices.reference_voice(stories, story["story_id"], read_entity(stories, story["story_id"],
                                                                           CHARACTERS, char_id))
        except voices.VoiceError as exc:
            raise WorkflowError(INVALID, str(exc)) from None
        return {key: voice[key] for key in VOICE_KEYS if key in voice}
    language = story["language"]
    catalogue = voices.catalogue(language, env=env, v2=media_policy.is_v2(story))
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
    if parsed[2] == regenerate_step.VARIANT_WORD:
        _check_variant_target(stories, story, doc, parsed[3])
        return None
    if parsed[2] != "image":
        return None
    written = character_written(doc) if kind == CHARACTERS else bool(doc["descriptor"])
    if not written:
        raise WorkflowError(CONFLICT, f"Write {name} first: its text makes every image.")
    slot = parsed[3] if len(parsed) > 3 else "image"
    if kind == CHARACTERS and slot in refimages.CHARACTER_IMAGES and slot not in refimages.character_images(story):
        raise WorkflowError(CONFLICT, (f"{name}'s {slot} is not drawn in this story's sheet mode "
                                       f"({media_policy.sheet_mode(story)}: "
                                       f"{', '.join(refimages.character_images(story))})."))
    if kind == CHARACTERS and slot in SHEETS and not _has(stories, story_id, kind, eid, doc["refs"]["portrait"]):
        raise WorkflowError(CONFLICT, f"Make {name}'s portrait first: the {slot} is drawn from it.")
    if kind == PLACES and slot != MASTER_PLATE and not _has(
            stories, story_id, kind, eid, doc["time_variants"].get(MASTER_PLATE)):
        raise WorkflowError(CONFLICT, f"Make {name}'s day plate first: every other variant is made from it.")
    return None


# ------------------------------------------------------- appearance variants (plan 23 stage D5)

def require_variants_enabled(story) -> None:
    """``conflict`` unless *story*'s characters may carry appearance variants
    (``media_policy.variants_enabled``)."""
    if not media_policy.variants_enabled(story):
        raise WorkflowError(CONFLICT, ("This story's characters carry no appearance variants: they come with a v2 "
                                       "story's character sheets mode (or generation_profile.variants \"on\")."))


def variant_units(story) -> dict:
    """What one appearance variant's sheets job makes: every sheet the
    story's sheet mode draws (``refimages.character_images``: one in
    ``two_view``), each an edit of the base portrait in ``references`` mode
    -- priced like the sheets -- or an image from text in ``prompt_only``."""
    count = len(refimages.character_images(story))
    if story["generation_profile"]["consistency_mode"] == refimages.PROMPT_ONLY:
        return _units(images=count)
    return _units(edit_images=count)


def variant_sheets_phrase(story) -> str:
    """"N variant sheet(s)": what the estimate calls a variant's job."""
    count = len(refimages.character_images(story))
    return f"{count} variant sheet{'' if count == 1 else 's'}"


def _check_variant_target(stories, story, doc, variant_id) -> None:
    """``character:<id>:variant:<vid>`` checked before its job exists:
    variants enabled (``conflict``), the variant (``not_found``), the
    character written with a look and its base portrait on disk
    (``conflict``)."""
    require_variants_enabled(story)
    name = doc["name"]
    if shots.variant_record(doc, variant_id) is None:
        raise WorkflowError(NOT_FOUND, f"{name} has no appearance variant {variant_id!r}.")
    if not character_written(doc) or not doc.get("look"):
        raise WorkflowError(CONFLICT, f"Write {name} first: a variant's sheets are drawn from its look.")
    if not _has(stories, story["story_id"], CHARACTERS, doc["char_id"], doc["refs"]["portrait"]):
        raise WorkflowError(CONFLICT, f"Make {name}'s portrait first: every variant sheet is an edit of it.")


def variant_target(char_id, variant_id) -> str:
    """The regenerate target of a variant's sheets job."""
    return f"character:{char_id}:{regenerate_step.VARIANT_WORD}:{variant_id}"


def _variant_id(label, taken) -> str:
    """A new variant id from *label*: its slug (``schemas.slugify``) cut to
    the pattern, ``v_`` first when it would start with a digit, then ``_2``,
    ``_3``... until it is not in *taken*."""
    slug = schemas.slugify(label)
    if not slug[0].isalpha():
        slug = f"v_{slug}"
    slug = slug[:24].rstrip("_")
    candidate, n = slug, 1
    while candidate in taken:
        n += 1
        suffix = f"_{n}"
        candidate = slug[:24 - len(suffix)].rstrip("_") + suffix
    return candidate


def check_variant_fields(stories, story, char_id, fields, *, doc=None) -> dict:
    """*fields* (``{label, delta_text}``) checked for a new variant of
    *char_id*; returns them cleaned. ``invalid`` for anything else sent, a
    label that is empty or over :data:`schemas.VARIANT_LABEL_MAX_CHARS`
    characters, a delta that is empty, over
    :data:`schemas.VARIANT_DELTA_MAX_WORDS` words or naming a character, a
    place or a prop of the story (it describes the look only: no name enters
    an image prompt, spec 2.3); ``conflict`` without variants on the story,
    for a character not written yet, or one with
    :data:`schemas.VARIANTS_MAX` variants already; ``not_found`` for an
    unknown character."""
    require_variants_enabled(story)
    if not isinstance(fields, dict):
        raise WorkflowError(INVALID, "A variant is {label, delta_text}.")
    _unknown_keys(fields, ("label", "delta_text"), "a variant")
    errors = []
    label, delta = fields.get("label"), fields.get("delta_text")
    _text_error(errors, "label", label, limit=schemas.VARIANT_LABEL_MAX_CHARS)
    if not isinstance(delta, str) or not delta.strip():
        errors.append("delta_text: required -- what changes in the character's appearance")
    elif len(delta.split()) > schemas.VARIANT_DELTA_MAX_WORDS:
        errors.append(f"delta_text: {len(delta.split())} words, at most {schemas.VARIANT_DELTA_MAX_WORDS}")
    else:
        lowered = delta.lower()
        for kind in (CHARACTERS, PLACES, PROPS):
            for entity in list_entities(stories, story["story_id"], kind):
                if re.search(rf"\b{re.escape(entity['name'].lower())}\b", lowered):
                    errors.append(f"delta_text: names {entity['name']!r} -- describe the look only, never a name")
    if errors:
        raise _invalid_values("The variant would not be valid with these values.", errors)
    doc = doc if doc is not None else read_entity(stories, story["story_id"], CHARACTERS, char_id)
    if not character_written(doc):
        raise WorkflowError(CONFLICT, f"Write {doc['name']} first: a variant is an edit of its sheets.")
    if len(doc.get("variants") or ()) >= schemas.VARIANTS_MAX:
        raise WorkflowError(CONFLICT, (f"{doc['name']} has {schemas.VARIANTS_MAX} appearance variants already, the "
                                       "most a character carries."))
    return {"label": " ".join(label.split()), "delta_text": " ".join(delta.split())}


def add_variant(stories, story_id, char_id, fields, *, now, source="human") -> dict:
    """Create an appearance variant of character *char_id* (*fields*:
    ``{label, delta_text}``, :func:`check_variant_fields`) with no image
    yet; returns ``{"character", "variant", "target"}`` -- *target* the
    regenerate target that makes its sheets (``character:<id>:variant:<vid>``,
    queued behind the estimate gate like any regenerate). Its
    ``variant_id`` is a slug of the label, fixed now and never renamed
    (:func:`_variant_id`); *source* ``human`` or ``twist`` (an accepted N1v2
    proposal). The character's approval is never touched; the variant
    waits for its own (:func:`approve_variant`)."""
    story = load(stories, story_id)
    clean = check_variant_fields(stories, story, char_id, fields)
    created = {}

    def write(current):
        if len(current.get("variants") or ()) >= schemas.VARIANTS_MAX:
            raise WorkflowError(CONFLICT, (f"{current['name']} has {schemas.VARIANTS_MAX} appearance variants "
                                           "already, the most a character carries."))
        taken = {variant["variant_id"] for variant in current.get("variants") or ()}
        variant = {"variant_id": _variant_id(clean["label"], taken), "label": clean["label"],
                   "delta_text": clean["delta_text"],
                   "refs": {slot: None for slot in refimages.character_images(story)},
                   "source": source, "created_at": now, "approved_at": None}
        current.setdefault("variants", []).append(variant)
        created.update(variant)

    try:
        doc = entities_step.write_character(stories, story_id, char_id, write, now=now)
    except KeyError:
        raise WorkflowError(NOT_FOUND, f"This story has no character {char_id!r}.") from None
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None
    return {"character": doc, "variant": copy.deepcopy(created),
            "target": variant_target(char_id, created["variant_id"])}


def variant_missing(stories, story, doc, variant) -> list:
    """The sheets of *variant* the story's sheet mode draws that are not
    made (or not on disk), in order."""
    return [which for which in refimages.character_images(story)
            if not _has(stories, story["story_id"], CHARACTERS, doc["char_id"], (variant["refs"] or {}).get(which))]


def approve_variant(stories, story_id, char_id, variant_id, *, now) -> dict:
    """Approve one appearance variant (``variant:<char_id>:<variant_id>``);
    returns the character. ``not_found`` for an unknown character or
    variant; ``conflict`` naming the sheets it still lacks. Its
    ``approved_at`` becomes *now*; the character's own approval and the
    story's approvals never move."""
    story = load(stories, story_id)
    doc = read_entity(stories, story_id, CHARACTERS, char_id)
    variant = shots.variant_record(doc, variant_id)
    if variant is None:
        raise WorkflowError(NOT_FOUND, f"{doc['name']} has no appearance variant {variant_id!r}.")
    missing = variant_missing(stories, story, doc, variant)
    if missing:
        raise WorkflowError(CONFLICT, (f"{doc['name']} ({variant['label']}) cannot be approved yet; missing: "
                                       f"{', '.join(missing)} (regenerate '{variant_target(char_id, variant_id)}')."))

    def approve(current):
        record = shots.variant_record(current, variant_id)
        if record is None:
            raise WorkflowError(NOT_FOUND, f"{current['name']} has no appearance variant {variant_id!r}.")
        record["approved_at"] = now

    try:
        return entities_step.write_character(stories, story_id, char_id, approve, now=now)
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None


def parse_variant_approval(doc):
    """``(char_id, variant_id)`` of an approval ``variant:<cid>:<vid>``, or None."""
    if not isinstance(doc, str):
        return None
    parts = doc.split(":")
    if (len(parts) != 3 or parts[0] != VARIANT_APPROVAL
            or story_store.ENTITY_KINDS[CHARACTERS].pattern.fullmatch(parts[1]) is None
            or re.fullmatch(schemas.VARIANT_ID_PATTERN, parts[2]) is None):
        return None
    return parts[1], parts[2]


# ---------------------------------------------------------------- approvals

def approve_entity(stories, story_id, kind, eid, *, now, by=USER_APPROVED) -> dict:
    """Approve one character, place or prop; returns the story.

    ``not_found`` for an unknown one; ``conflict`` listing what it still
    lacks (:func:`character_missing` & co.). Its ``approved_at`` becomes
    *now* (the character re-read and written under the uploads' lock), and
    the store re-folds ``approvals.cast`` / ``approvals.places``. *by* (plan
    21): the human (default) or the agent run, recorded on the entity.
    """
    _check_approver(by)
    load(stories, story_id)
    doc = read_entity(stories, story_id, kind, eid)
    missing = MISSING[kind](stories, story_id, doc)
    if missing:
        labels = ", ".join(MISSING_LABELS[item] for item in missing)
        raise WorkflowError(CONFLICT, f"{doc['name']} cannot be approved yet; missing: {labels}.")

    def approve(current):
        current["approved_at"] = now
        _mark_doc_approval(current, by)

    try:
        entities_step.write_entity(stories, story_id, kind, eid, approve, now=now)
    except KeyError:
        raise WorkflowError(NOT_FOUND, f"This story has no {ENTITY_WORDS[kind]} {eid!r}.") from None
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None
    return load(stories, story_id)


def approve_season(stories, story_id, *, now, by=USER_APPROVED) -> dict:
    """Approve the season arc; returns the story, now ``ready``.

    ``conflict`` until the places and props are approved (the status is a
    contiguous prefix), without a ``season.json``, or while the arc does not
    hold ``episodes_planned`` entries each with a summary. Sets the arc's
    ``approved_at`` -- on ``season.json`` re-read under the store lock
    (``StoryStore.update_doc``), so a series step's write landing meanwhile
    is kept -- and ``approvals.season``. *by* (plan 21): the human (default)
    or the agent run, recorded on both.
    """
    _check_approver(by)
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
        _mark_doc_approval(current, by)
        return current

    try:
        stories.update_doc(story_id, SEASON_DOC, approve, now=now, validator=schemas.season_arc_errors)
    except KeyError:
        raise not_found() from None
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None

    def mutate(story_doc):
        story_doc["approvals"]["season"] = now
        _mark_story_approval(story_doc, "season", by)

    return update(stories, story_id, mutate, now=now)


def approve_knowledge(stories, story_id, *, now, by=USER_APPROVED) -> dict:
    """Approve the story's knowledge base (phase 7 stage 5b, DEC-228); returns
    ``knowledge.json`` as written.

    ``conflict`` for a legacy story, without a ``knowledge.json``, until all
    four sections (world, timeline, props registry, ledger seed) are written
    -- the timeline one entry for each episode the season plans -- or while
    a beat still names a new object D6 has not registered. Sets
    ``approved_at`` and ``approved_rev`` (the ``rev`` approved), re-read
    under the store lock, without moving ``rev``: a later write moves it, and
    the episode gate then asks for the approval again. Never the story's
    approvals or status (no seventh approval key, A14). *by* (plan 21): the
    human (default) or the agent run, recorded on the knowledge base."""
    _check_approver(by)
    story = load(stories, story_id)
    _step_refusal(knowledge_step.require_runnable, story)
    arc = season(stories, story_id)
    planned = arc["episodes_planned"] if arc else 0

    def approve(current):
        if current is None:
            raise WorkflowError(CONFLICT, "There is no knowledge base to approve yet: run the knowledge step first.")
        todo = knowledge_step.left(current, planned)
        if any((todo["world"], todo["episodes"], todo["registry"], todo["ledger"])):
            raise WorkflowError(CONFLICT, (f"The knowledge base is not complete: {knowledge_step.describe_left(todo)} "
                                           "still to write; run the knowledge step again first."))
        pending = [name for entry in current["timeline"] for beat in entry["beats"]
                   for name in beat.get("new_objects") or ()]
        if pending:
            raise WorkflowError(CONFLICT, (f"The timeline still names unregistered objects ({', '.join(pending)}); "
                                           "run the knowledge step again first."))
        current["approved_at"] = now
        current["approved_rev"] = current["rev"]
        _mark_doc_approval(current, by)
        return current

    try:
        return stories.update_knowledge(story_id, approve, now=now, bump_rev=False)
    except KeyError:
        raise not_found() from None
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _edit_beats(doc, beats, errors) -> None:
    """``beats`` ``[{ep, beat, <KNOWLEDGE_BEAT_PATCH_FIELDS>...}]`` into *doc*'s
    timeline (in place): ``beat`` is the 1-based position in episode ``ep``'s
    entry; each key sent replaces the beat's own. Shape errors into *errors*."""
    if not isinstance(beats, list):
        errors.append("beats: expected a list")
        return
    entries = {entry["ep"]: entry for entry in doc.get("timeline") or ()}
    editable = ", ".join(KNOWLEDGE_BEAT_PATCH_FIELDS)
    for i, item in enumerate(beats):
        path = f"beats[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: expected an object {{ep, beat, {editable}}}")
            continue
        extra = sorted(set(item) - {"ep", "beat"} - set(KNOWLEDGE_BEAT_PATCH_FIELDS))
        if extra:
            errors.append(f"{path}: unknown key(s) {', '.join(extra)} (editable: {editable})")
            continue
        ep, position = item.get("ep"), item.get("beat")
        entry = entries.get(ep) if _is_int(ep) else None
        if entry is None:
            errors.append(f"{path}.ep: the timeline has no episode {ep!r}")
            continue
        count = len(entry["beats"])
        if not (_is_int(position) and 1 <= position <= count):
            errors.append(f"{path}.beat: episode {ep} has no beat {position!r} (1-{count})")
            continue
        beat = entry["beats"][position - 1]
        for key in KNOWLEDGE_BEAT_PATCH_FIELDS:
            if key in item:
                value = copy.deepcopy(item[key])
                beat[key] = value.strip() if isinstance(value, str) else value


def _edit_knowledge(doc, fields, errors) -> None:
    """*fields* (``KNOWLEDGE_PATCH_FIELDS``) into the knowledge document *doc*,
    in place; what is not even the right shape goes into *errors* (the rest
    is the document's own rules, checked when it is written)."""
    if "world" in fields:
        world = fields["world"]
        if isinstance(world, dict):
            doc["world"] = {**(doc.get("world") or {}),
                            **{key: value.strip() if isinstance(value, str) else copy.deepcopy(value)
                               for key, value in world.items()}}
        else:
            errors.append("world: expected an object {geography?, period_details?, visual_motifs?}")
    if "beats" in fields:
        _edit_beats(doc, fields["beats"], errors)
    if "props_registry" in fields:
        doc["props_registry"] = copy.deepcopy(fields["props_registry"])
    if "ledger_seed" in fields:
        seed = fields["ledger_seed"]
        if not isinstance(seed, dict):
            errors.append("ledger_seed: expected an object {char_id: state}")
            return
        merged = dict(doc.get("ledger_seed") or {})
        for char_id, state in seed.items():
            if isinstance(state, dict):
                merged[char_id] = {**(merged.get(char_id) or {}), **copy.deepcopy(state)}
            else:
                errors.append(f"ledger_seed.{char_id}: expected an object")
        doc["ledger_seed"] = merged


def patch_knowledge(stories, story_id, fields, *, now) -> dict:
    """Edit a v2 story's knowledge base (phase 7 stage 7, A19; the API's
    KnowledgePatchRequest); returns ``knowledge.json`` as written.

    ``world`` is merged onto the current world; ``beats`` ``[{ep, beat,
    what?, place_id?, who?, objects?, knows_after?}]`` names a beat by its
    episode and 1-based position, each key sent replacing the beat's own
    (``new_objects`` stays the knowledge step's); ``props_registry`` is the
    registry as a whole; ``ledger_seed`` ``{char_id: {...}}`` is merged onto
    that character's starting state (one not in the seed yet needs every
    key). Checked as the knowledge step's writes are -- the document's rules
    and every id against the story (``StoryStore.update_knowledge``) -- and
    refused whole (``invalid`` with every error; nothing written). A write
    moves ``rev``, so an approved base reads stale until it is approved again
    (DEC-228 part 2); nothing sent, or nothing changed: nothing written.
    ``conflict`` for a legacy story, or before the knowledge step wrote the
    document."""
    story = load(stories, story_id)
    _unknown_fields(fields, KNOWLEDGE_PATCH_FIELDS, "knowledge base")
    if not media_policy.is_v2(story):
        raise WorkflowError(CONFLICT, "Only a v2 story has a knowledge base; this story is on the legacy pipeline.")
    current = knowledge(stories, story_id)
    if current is None:
        raise WorkflowError(CONFLICT, "There is no knowledge base to edit yet: run the knowledge step first.")
    if not fields:
        return current
    message = "The knowledge base would not be valid with these values."

    def edit(doc):
        if doc is None:
            raise WorkflowError(CONFLICT, "There is no knowledge base to edit yet: run the knowledge step first.")
        before = copy.deepcopy(doc)
        errors = []
        _edit_knowledge(doc, fields, errors)
        if errors:
            raise _invalid_values(message, errors)
        return None if doc == before else doc

    try:
        return stories.update_knowledge(story_id, edit, now=now)
    except KeyError:
        raise not_found() from None
    except schemas.SchemaError as exc:
        raise _invalid_values(message, exc.errors) from None


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


def _merge_block(doc, name, value, word) -> None:
    """A v2 block (``look``, ``dossier``) sent as an object, merged onto the
    entity's current one as ``personality`` is (a block not written yet is
    started from what is sent: the schema then asks for every key)."""
    if not isinstance(value, dict):
        raise _invalid_values(f"The {word} would not be valid with these values.", [f"$.{name}: expected an object"])
    doc[name] = {**(doc.get(name) or {}), **value}


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
    for name in V2_BLOCK_FIELDS:
        if name in values:
            _merge_block(doc, name, values[name], ENTITY_WORDS[CHARACTERS])
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
            if name in V2_BLOCK_FIELDS:
                _merge_block(doc, name, value, ENTITY_WORDS[kind])
            else:
                doc[name] = value
        if kind == PROPS and "owner_char_id" in values:
            owner = values["owner_char_id"]
            if owner is not None and owner not in cast_ids:
                raise _invalid_values("The prop would not be valid with these values.",
                                      [f"$.owner_char_id: {owner!r} is no character of this story"])
    if set(values) & set(_BLOCK_FIELDS[kind]):
        doc["prompt_block"] = _prompt_block(kind, lock, doc)
    doc["approved_at"] = None


def _block_reference_errors(stories, story_id, kind, eid, doc, blocks, cast_ids) -> list:
    """What the v2 *blocks* of *doc* (already valid on their own) name that
    the story does not have: a dossier's relationships (its characters), a
    place look's resident props, a prop look's holders and places -- the
    store checks a knowledge write's ids the same way -- and a look that
    drops the wardrobe set the knowledge base's ledger seed dresses the
    character in (every later knowledge write would be refused)."""
    errors = []
    if kind == CHARACTERS and "dossier" in blocks:
        for i, relationship in enumerate(doc["dossier"]["relationships"]):
            if relationship["with"] not in cast_ids:
                errors.append(f"$.dossier.relationships[{i}].with: {relationship['with']!r} is no character of "
                              "this story")
    if kind == CHARACTERS and "look" in blocks:
        seed = ((knowledge(stories, story_id) or {}).get("ledger_seed") or {}).get(eid) or {}
        worn = seed.get("wardrobe_set")
        if worn is not None and worn not in {item["id"] for item in doc["look"]["wardrobe_sets"]}:
            errors.append(f"$.look.wardrobe_sets: {worn!r} is what the knowledge base's ledger seed dresses "
                          f"{doc['name']} in before episode 1; change the ledger seed first, or keep the set")
    if kind == PLACES and "look" in blocks:
        prop_ids = {item["prop_id"] for item in list_entities(stories, story_id, PROPS)}
        for i, prop_id in enumerate(doc["look"]["props_here"]):
            if prop_id not in prop_ids:
                errors.append(f"$.look.props_here[{i}]: {prop_id!r} is no prop of this story")
    if kind == PROPS and "look" in blocks:
        place_ids = {item["place_id"] for item in list_entities(stories, story_id, PLACES)}
        for i, entry in enumerate(doc["look"]["where_when"]):
            path = f"$.look.where_when[{i}]"
            if entry["holder_char_id"] is not None and entry["holder_char_id"] not in cast_ids:
                errors.append(f"{path}.holder_char_id: {entry['holder_char_id']!r} is no character of this story")
            if entry["place_id"] is not None and entry["place_id"] not in place_ids:
                errors.append(f"{path}.place_id: {entry['place_id']!r} is no place of this story")
    return errors


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

    Phase 7 stage 7 (A19): a character's ``look`` and ``dossier``, a place's
    and a prop's ``look`` -- the blocks the v2 writers produce -- are merged
    onto the current ones (as ``personality`` is) and checked by the kind's
    schema and against the story (:func:`_block_reference_errors`); an edit
    clears the approval and moves ``updated_at`` like any other, so the
    storyboard prompts resolved from the entity read outdated
    (:func:`outdated_entities`). ``conflict`` on a legacy story, which is
    never given one (RC-M3).
    """
    story = load(stories, story_id)
    current = read_entity(stories, story_id, kind, eid)
    if not fields:
        return current
    allowed = PATCH_FIELDS_BY_KIND[kind]
    unknown = sorted(set(fields) - set(allowed))
    if unknown:
        raise WorkflowError(INVALID, (f"These {ENTITY_WORDS[kind]} fields cannot be edited: {', '.join(unknown)} "
                                      f"(editable: {', '.join(allowed)})."))
    blocks = [name for name in V2_BLOCK_FIELDS if name in fields]
    if blocks and not media_policy.is_v2(story):
        raise WorkflowError(CONFLICT, (f"A {' or '.join(blocks)} belongs to a v2 story (the quality pipeline): "
                                       "this story is on the legacy pipeline, whose prompts never read one."))
    values = {name: (value.strip() if name in _STRIPPED and isinstance(value, str) else copy.deepcopy(value))
              for name, value in fields.items()}
    lock = style_lock(stories, story_id)
    cast_ids = {doc["char_id"] for doc in list_entities(stories, story_id, CHARACTERS)}
    word = ENTITY_WORDS[kind]
    validator = story_store.ENTITY_KINDS[kind].validator

    trial = copy.deepcopy(current)
    _apply(kind, trial, values, lock=lock, cast_ids=cast_ids)
    errors = validator(trial)
    if blocks and not errors:
        errors = _block_reference_errors(stories, story_id, kind, eid, trial, blocks, cast_ids)
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
# Plan 19 stage 3 (F6): the script step's check-only run, a closed list of
# its own (the dashboard's "Check again" sends it, ``checkParams``); the
# script step takes SCRIPT_PARAMS + SCRIPT_CHECK_PARAMS.
SCRIPT_CHECK_PARAMS = (script_step.CHECK_ONLY_PARAM,)

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
STORYBOARD_SHOT_PATCH_FIELDS = ("framing", "camera_motion", "modifiers", "action", "keep_still", "prompt_override",
                               "variants")
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
    episode_common.KNOWLEDGE_MISSING: CONFLICT,
    episode_common.KNOWLEDGE_UNAPPROVED: CONFLICT,
    episode_common.KNOWLEDGE_STALE: CONFLICT,
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
    (``episode_common.memory_refusal``); and -- the knowledge gate (phase 7
    stage 5b), a v2 story only -- for a *step* that writes the script
    (``episode_common.needs_knowledge``) unless *require_memory* is off, while
    its knowledge base is missing, not approved or changed since its approval
    (``episode_common.knowledge_refusal``). The assets, the render and the
    metadata never meet either gate."""
    _episode_refused(episode_common.check_story_ready, story)
    if type(ep) is not int:
        arc = season(stories, story["story_id"]) or {}
        raise WorkflowError(INVALID, (f"'{step}' works on one episode: send its number as ep (the season plans "
                                      f"1 to {arc.get('episodes_planned', 0)})."))
    ec = _context(stories, story["story_id"], ep)
    _episode_refused(episode_common.check_episode_preconditions, None, ec, require_memory=False)
    if require_memory and _step_refusal(episode_common.needs_memory, ec, step):
        _episode_refused(episode_common.check_episode_preconditions, None, ec, require_memory=True)
    if require_memory and _step_refusal(episode_common.needs_knowledge, ec, step):
        _episode_refused(episode_common.check_episode_preconditions, None, ec, require_memory=False,
                         require_knowledge=True)
    return ec


def _flag(params, key) -> bool:
    value = params.get(key)
    if value is not None and type(value) is not bool:
        raise WorkflowError(INVALID, f"params.{key} is true or false, not {value!r}.")
    return bool(value)


def script_request(params) -> bool:
    """A script step's *params* (``{measure_voices?, check_only?}``, a closed
    list; ``invalid`` otherwise, and for both at once: a check-only run
    measures nothing): whether to measure the lines with real voices."""
    _unknown_keys(params, SCRIPT_PARAMS + SCRIPT_CHECK_PARAMS, "script")
    measure = _flag(params, script_step.MEASURE_PARAM)
    if _flag(params, script_step.CHECK_ONLY_PARAM) and measure:
        raise WorkflowError(INVALID, script_step.CHECK_ONLY_MEASURE_REFUSAL)
    return measure


def script_check_only(params) -> bool:
    """Whether a script step's *params* ask the check-only run (plan 19
    stage 3; :func:`script_request` checks them)."""
    return params.get(script_step.CHECK_ONLY_PARAM) is True


def require_checkable_script(ec) -> dict:
    """The episode's script, complete, for a check-only run (plan 19 stage
    3): ``conflict`` with ``script.check_only_refusal``'s sentence -- a run
    that writes nothing never fills a stub."""
    try:
        script = episode_common.read_episode(ec, SCRIPT_DOC)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None
    refusal = script_step.check_only_refusal(script, ec.ep)
    if refusal:
        raise WorkflowError(CONFLICT, refusal)
    return script


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
    story's choice. A v2 story's ``state`` also has ``first_watch``: none |
    passed | issues | stale (its J1 report, ``judge.first_watch_state``;
    phase 7 stage 6a) -- a legacy story's page has no such key.
    ``assets_regenerate_blocked``/``metadata_regenerate_
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
    view = {
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
    if media_policy.is_v2(story):
        view["state"]["first_watch"] = judge_step.first_watch_state(script,
                                                                    judge_step.j1_version(story, script))
    return view


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


# ------------------------------------------------------ the stories list
#
# What a card of the stories list shows beside the index entry (dashboard
# overhaul stage 2, DEC-254): a cover, the story's progress through its steps,
# its episodes and its style's label -- all read from the documents, calling
# nothing, so the list stays a cheap read. A story whose documents cannot be
# read keeps the empty values (no cover, zeros); the list never fails for one.

# The story steps in the dashboard's order (NewStoryWizard's STEPS keys), each
# with the approval that marks it done; a v2 story has the knowledge step too
# (done while its knowledge base is approved and current).
LIST_STORY_STEPS = (("concepts", "concept"), ("bible", "bible"), ("style", "style"), ("cast", "cast"),
                    ("places", "places"), ("season", "season"))
LIST_KNOWLEDGE_STEP = "knowledge"
# An episode's furthest point: a render with an output, approved assets, an
# approved storyboard, an approved script, else a draft.
LIST_EPISODE_STATES = ("draft", "written", "planned", "assets", "rendered")
_LIST_UNREADABLE = (KeyError, OSError, ValueError, TypeError, schemas.SchemaError, WorkflowError, StoryUnreadable)


def style_label(template_id):
    """The shipped style's human name (English, else French), the id itself
    for a template that is not shipped, or None for a story with no style."""
    if not template_id:
        return None
    try:
        name = templates.load_style(template_id).get("name") or {}
    except (KeyError, OSError, ValueError, schemas.SchemaError):
        return template_id
    return name.get("en") or name.get("fr") or template_id


def list_cover(stories, story_id):
    """The API path (under ``/api``) of the first character's portrait that
    is on disk, in cast order (leads first: ``entities.cast_order``), or None."""
    for doc in entities_step.cast_order(stories.list_entities(story_id, CHARACTERS)):
        ref = (doc.get("refs") or {}).get("portrait")
        if ref and _has(stories, story_id, CHARACTERS, doc["char_id"], ref):
            return f"/stories/{story_id}/media/{CHARACTERS}/{doc['char_id']}/{ref['name']}"
    return None


def _knowledge_approved(stories, story_id) -> bool:
    try:
        return episode_common.knowledge_state(stories.read_knowledge(story_id)) == "approved"
    except _LIST_UNREADABLE:
        return False


def list_progress(stories, story) -> dict:
    """``{"steps_done", "steps_total", "next"}``: how many steps are done in
    order (a contiguous prefix, as ``store.derive_status`` counts approvals)
    and the first one that is not (a step id, None once every one is). An
    unreadable knowledge base is a knowledge step not done."""
    approvals = story.get("approvals") or {}
    steps = [step for step, _key in LIST_STORY_STEPS]
    done = [bool(approvals.get(key)) for _step, key in LIST_STORY_STEPS]
    if media_policy.is_v2(story):
        steps.append(LIST_KNOWLEDGE_STEP)
        done.append(all(done) and _knowledge_approved(stories, story["story_id"]))
    count = 0
    while count < len(steps) and done[count]:
        count += 1
    return {"steps_done": count, "steps_total": len(steps), "next": steps[count] if count < len(steps) else None}


def list_episode_state(stories, story_id, ep) -> str:
    """One of :data:`LIST_EPISODE_STATES` for episode *ep* (four document
    reads at most; ``draft`` when one of them cannot be read)."""
    try:
        manifest = stories.read_episode_doc(story_id, ep, MANIFEST_DOC)
        if manifest and manifest.get("output"):
            return "rendered"
        assets = stories.read_episode_doc(story_id, ep, ASSETS_DOC)
        if assets and assets.get("approved"):
            return "assets"
        board = stories.read_episode_doc(story_id, ep, STORYBOARD_DOC)
        if board and board.get("approved_at"):
            return "planned"
        script = stories.read_episode_doc(story_id, ep, SCRIPT_DOC)
        if script and script.get("approved_at"):
            return "written"
    except _LIST_UNREADABLE:
        pass
    return "draft"


def list_episodes(stories, story_id) -> dict:
    """``{"count", "latest": {"ep", "state"} | null}`` (the highest episode)."""
    numbers = stories.list_episodes(story_id)
    if not numbers:
        return {"count": 0, "latest": None}
    latest = numbers[-1]
    return {"count": len(numbers), "latest": {"ep": latest, "state": list_episode_state(stories, story_id, latest)}}


def list_card(stories, entry) -> dict:
    """*entry* (an index entry, its fields kept as they are) with ``cover``,
    ``progress``, ``episodes``, ``style_label``, ``pipeline`` (the story's
    ``generation_profile.pipeline``, None for a legacy story) and ``mode``
    (plan 21 stage 3: ``studio`` or ``agent``, :func:`story_mode`'s default)
    added. Each part is read on its own: one that cannot be read keeps its
    empty value."""
    card = dict(entry)
    card.update({
        "cover": None,
        "progress": {"steps_done": 0, "steps_total": 0, "next": None},
        "episodes": {"count": 0, "latest": None},
        "style_label": style_label(entry.get("style_template_id")),
        "pipeline": None,
        "mode": defaults.MODE_STUDIO,
    })
    story_id = entry.get("story_id")
    try:
        story = stories.get(story_id)
    except _LIST_UNREADABLE:
        return card
    card["pipeline"] = (story.get("generation_profile") or {}).get("pipeline") or None
    card["mode"] = story_mode(story)
    for field, read in (("cover", lambda: list_cover(stories, story_id)),
                        ("progress", lambda: list_progress(stories, story)),
                        ("episodes", lambda: list_episodes(stories, story_id))):
        try:
            card[field] = read()
        except _LIST_UNREADABLE:
            pass
    return card


def list_cards(stories, entries) -> list:
    """:func:`list_card` for each index entry, in their order."""
    return [list_card(stories, entry) for entry in entries]


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
    shots = {shot["shot_id"]: assets_step.shot_state(ec, shot, link=link, doc=doc)
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
    derived = {"shots": shots, "lines": lines, "fingerprint": assets_approval_state(ec, board, script, doc),
               "out_of_date": out_of_date, "reedit": reedit}
    if media_policy.is_v2(ec.story):
        # Phase 7 stage 6b: a v2 episode's keyframe approval (hashes every image).
        derived["keyframes"] = keyframes_approval_state(ec, board, doc)
        # Phase 8 stage B: which shot's J2 verdict judged the keyframes on disk now.
        verdicts = (doc or {}).get(judge_step.KEYFRAME_VERDICTS) or {}
        current = {}
        if board:
            for shot, _path, sha, _prev_id, _prev_path, prev_sha in assets_step.keyframe_items(ec, board, doc):
                current[shot["shot_id"]] = judge_step.verdict_current(verdicts.get(shot["shot_id"]), sha, prev_sha)
        derived["keyframe_current"] = current
    return derived


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
        if "keyframes" in derived:
            # Phase 8 stage B, a v2 episode's page only: the shot's J2 verdict
            # (current: it judged the keyframes on disk now) and what the
            # keyframe auto-fix did for it; each null when there is none.
            shots[-1]["keyframe_verdict"] = _keyframe_verdict_view(
                (doc or {}).get(judge_step.KEYFRAME_VERDICTS, {}).get(shot["shot_id"]),
                derived["keyframe_current"].get(shot["shot_id"], False))
            shots[-1]["keyframe_fix"] = ((doc or {}).get(assets_step.KEYFRAME_FIXES) or {}).get(shot["shot_id"])
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
    view = {"doc": doc, "consistency": ec.consistency_mode, "fingerprint": derived["fingerprint"],
            "approved_at": approved["at"] if approved else None, "shots": shots, "lines": lines}
    if "keyframes" in derived:
        # Phase 7 stage 6b, a v2 episode's page only: the keyframe approval
        # (none | current | stale), when and whether "anyway"; the J2 verdicts
        # are the document's own ``keyframe_verdicts``.
        keyframes = (doc or {}).get(judge_step.KEYFRAMES_APPROVED) or {}
        view["keyframes"] = {"approval": derived["keyframes"], "approved_at": keyframes.get("at"),
                             "anyway": keyframes.get("anyway"), "target": f"{KEYFRAMES_APPROVAL}:{ep}",
                             # Phase 8 stage B: the episode's keyframe auto-fix budget, or null.
                             "fix_budget": (doc or {}).get(assets_step.KEYFRAME_FIX_BUDGET)}
    return view


def _keyframe_verdict_view(entry, current):
    """A shot's J2 verdict as the episode page shows it (phase 8 stage B):
    ``{passed, current, shows_beat, missing, continuity_issue, checked_at}``
    -- and ``framing_issue`` when J2 named one (plan 19 stage 3; the review's
    verdict text reads it, ``judge.verdict_text``) -- or None when the shot
    has none."""
    if entry is None:
        return None
    view = {"passed": judge_step.verdict_passed(entry), "current": bool(current),
            "shows_beat": entry["shows_beat"], "missing": list(entry["missing"]),
            "continuity_issue": entry["continuity_issue"], "checked_at": entry["checked_at"]}
    if entry.get("framing_issue"):
        view["framing_issue"] = entry["framing_issue"]
    return view


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
    {"est_usd", "paid_usd", "entries"}}`` (``CostLedger.episode_entries`` and
    ``totals(ep)``: an archived take's rows, ``discarded``, are left out);
    empty while the story has no ledger. A symlink in its place is not
    followed."""
    try:
        path = os.path.join(stories.story_dir(story_id), COST_LEDGER)
    except KeyError:
        raise not_found() from None
    if os.path.islink(path) or not os.path.isfile(path):
        return {"entries": [], "totals": {"est_usd": 0.0, "paid_usd": 0.0, "entries": 0}}
    ledger = CostLedger(path)
    try:
        return {"entries": ledger.episode_entries(ep), "totals": ledger.totals(ep)}
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
                               has_audio: bool, target: "line:<ep>:<lid>"}],
                    "keyframes": {"approval": none|current|stale, "approved_at", "anyway",
                                  "target": "keyframes:<ep>"} (a v2 story's only)} | null,
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
# values (hashed) and the budget day (``budget.today()``, BUDGET_TIMEZONE).
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
            budget_mod.today())


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
    # DEC-258: the file the shot plays is ``assets.video`` -- its lip-synced take once that is current.
    playing = clips_step.shot_clip_path(ec, shot)
    lipsync = record.get("lipsync") or None
    view = {
        "state": state, "link": record.get("link"), "route": record.get("route"), "clip_s": record.get("clip_s"),
        "est_usd": record.get("est_usd"), "generated_at": record.get("generated_at"),
        "name": os.path.basename(playing) if playing is not None else None,
        "note": assets_step.clip_note(shot), "reason": record.get("reason"), "pending": bool(record.get("pending")),
        "target": None if held is not None else target, "continue": held is not None, "blocked": blocked,
        "flags": dict(flags), "overrides": dict(video_plan.shot_overrides(doc, shot_id) or {}),
    }
    if record.get("route") == schemas.STOCK_ROUTE:
        # Plan 23 stage B8: a stock cutaway's credit (who made it, where, under what licence).
        source = record.get("source") or {}
        view["stock"] = {key: source.get(key) for key in ("provider", "author", "author_url", "page_url", "licence",
                                                          "credit")}
    if lipsync is not None:
        view["lipsync"] = {"state": lipsync.get("state"), "link": lipsync.get("link"),
                           "lines": list(lipsync.get("lines") or []), "est_usd": lipsync.get("est_usd"),
                           "reason": lipsync.get("reason"), "generated_at": lipsync.get("generated_at")}
    if "speaks" in shot:
        # Plan 22: a native-speech shot -- whether its clip speaks its line, and its take.
        take = record.get("native_speech") or None
        view["speaks"] = bool(shot["speaks"])
        view["take"] = None if take is None else {key: take.get(key) for key in (
            "state", "matched", "heard", "start_s", "end_s", "aligned_by", "reason")}
    return view


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
    if video.get("lipsync") is not None:
        # DEC-258: the clips' lipsync part, only on a story that lipsyncs.
        view["lipsync"] = copy.deepcopy(video["lipsync"])
    if video.get("speech") is not None:
        # Plan 22: a native-speech episode's two links, their prices and the retake budget.
        view["speech"] = {key: copy.deepcopy(value) for key, value in video["speech"].items() if key != "classes"}
        view["over_cap"] = video.get("over_cap")
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
        shots[shot["shot_id"]] = _shot_clip(ec, script, shot, doc, link=clips_step.class_link(ec.story, shot, doc, link),
                                            tier=tier, image_sha=sha)
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
    if (doc or {}).get("shot_modes"):
        # Plan 25 stage 1: who makes each shot, as assets.json sets it (absent: the story's profile).
        view["shot_modes"] = copy.deepcopy(doc["shot_modes"])
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


# ---------------------------------------------------- the page, the review (stage C)

# A shot's keyframe on the review screen: its check passed; it passed after
# the auto-fix redrew it; it is still flagged (a current verdict that
# failed); it has no verdict yet; or its verdict judged other images.
REVIEW_VERDICT_STATES = ("passed", "fixed", "flagged", "unchecked", "not_current")
# Where the episode stands for the human: nothing made yet; the keyframes
# wait for their approval; the assets wait for theirs; approved but not
# rendered (or rendered before a change); rendered with the files as they
# are -- ready for review.
REVIEW_STATUSES = ("not_made", "keyframes_pending", "assets_pending", "render_needed", "ready")


def _review_verdict(shot) -> dict:
    """One shot's keyframe verdict as the review shows it (see
    :func:`episode_review`): ``{"state", "issue", "redraws", "gave_up"}``
    from the per-shot assets view's ``keyframe_verdict`` and ``keyframe_fix``
    (phase 8 stage B), each absent on a legacy episode."""
    verdict = shot.get("keyframe_verdict")
    fix = shot.get("keyframe_fix") or {}
    redraws, gave_up = int(fix.get("redraws") or 0), bool(fix.get("gave_up"))
    if verdict is None:
        state, issue = "unchecked", None
    elif not verdict["current"]:
        state, issue = "not_current", None
    elif verdict["passed"]:
        state, issue = ("fixed" if redraws else "passed"), None
    else:
        state, issue = "flagged", judge_step.verdict_text(verdict)
    return {"state": state, "issue": issue, "redraws": redraws, "gave_up": gave_up}


def _review_spend(page) -> dict:
    """The episode's spend by kind from its ledger rows (``episode_ledger``):
    the shot images (the auto-fix's redraws apart, from
    ``keyframe_fix_budget``), the voices, the clips, the rest, the total."""
    by_unit = {}
    for row in page["ledger"]["entries"]:
        by_unit[row.get("unit")] = by_unit.get(row.get("unit"), 0.0) + float(row.get("est_usd") or 0.0)
    doc = (page["assets"] or {}).get("doc") or {}
    fixes = float((doc.get(assets_step.KEYFRAME_FIX_BUDGET) or {}).get("spent_usd") or 0.0)
    images = max(0.0, by_unit.get("image", 0.0) - fixes)
    voices, clips = by_unit.get("char", 0.0), by_unit.get("second", 0.0)
    total = float(page["ledger"]["totals"]["est_usd"])
    return {"images_usd": round(images, 4), "fixes_usd": round(fixes, 4), "voices_usd": round(voices, 4),
            "clips_usd": round(clips, 4), "other_usd": round(max(0.0, total - images - fixes - voices - clips), 4),
            "total_usd": round(total, 4)}


def _review_headline(status, *, flagged, keyframes, render) -> str:
    """One sentence for the top of the review screen."""
    count = len(flagged)
    still = f"{count} still flagged" if count else ""
    if status == "not_made":
        return "Nothing made yet: Generate episode makes the whole episode, up to the finished render."
    if status == "keyframes_pending":
        if keyframes and keyframes["approval"] == "stale":
            head = "The keyframes changed since they were approved: approve them again, then the assets, then render"
        else:
            head = "Keyframes made and checked: approve them, then the assets, then render"
        return f"{head} ({still})." if still else f"{head}."
    if status == "assets_pending":
        return "Keyframes approved: approve the assets, then render."
    if status == "render_needed":
        if render and render.get("out_of_date"):
            return "Rendered, but something changed since: render again."
        return "Everything is approved: render the episode."
    return f"Ready for review — {still}." if still else "Ready for review."


def episode_review(page) -> dict:
    """The review block of the episode page (stage C: one screen to check
    and approve what the fast track's one click made), read from the page
    as the web layer assembled it (:func:`episode_view`,
    :func:`episode_outputs`, each shot's ``clip`` merged from
    :func:`episode_clips`): a pure function over what is computed already,
    so it costs the page nothing more::

        {"status": <REVIEW_STATUSES>, "ready": bool, "headline": sentence,
         "auto_approved": ["keyframes", "assets"] (what the fast track approved: by == fast_track),
         "pending": ["keyframes", "assets"] (what is still to approve, in this order),
         "approvals": {"script": {"approved", "at", "anyway", "by", "issues"}, "storyboard": {"approved", "at"},
                       "keyframes": {"approval", "at", "anyway", "by", "flagged", "target"} | None (legacy),
                       "assets": {"approval", "at", "by", "target"}},
         "flagged": [shot ids whose current check failed], "unchecked": [no current check],
         "fixed": [redrawn by the auto-fix and passing now],
         "spend": {"images_usd", "fixes_usd", "voices_usd", "clips_usd", "other_usd", "total_usd"},
         "render": {"state", "out_of_date", "duration_s", "finished_at", "file"} | None,
         "metadata_current": bool | None,
         "script_repairs": script.repairs | None (stage G; absent on older scripts),
         "script_minor_issues": [{"scene_id", "kind", "fix"}] (DEC-248: the minor issues of a
                                passed first-watch report, approved over and kept for the human to read),
         "shots": [{"shot_id", "scene_id", "order", "image_name", "image_state", "locked", "target",
                    "clip": {"name", "url", "state", "current", "target", "blocked", "continue"} | None,
                    "verdict": {"state": <REVIEW_VERDICT_STATES>, "issue", "redraws", "gave_up"},
                    "fix": keyframe_fixes[shot] | None,
                    "lines": [{"line_id", "speaker", "text"}]}]}

    ``pending`` lists the keyframes (a v2 episode's, while their approval is
    not current) then the assets (while their fingerprint is not current);
    ``status`` is the first of those, else the render's state. The clips
    are the page's own (None at tier 1, or before the clips were merged)."""
    script, board, assets = page.get("script"), page.get("storyboard"), page.get("assets") or {}
    doc = assets.get("doc") or {}
    keyframes_view = assets.get("keyframes")
    approved = doc.get("approved") or {}
    recorded = doc.get(judge_step.KEYFRAMES_APPROVED) or {}
    approvals = {
        "script": {"approved": bool(script and script.get("approved_at")),
                   "at": (script or {}).get("approved_at"), "anyway": bool((script or {}).get("approved_anyway")),
                   # Plan 19 stage 3: who approved, and the blocking issues the fast track approved over.
                   "by": (script or {}).get("approved_by") or (USER_APPROVED if (script or {}).get("approved_at")
                                                              else None),
                   "issues": list((script or {}).get("approved_over") or [])},
        "storyboard": {"approved": bool(board and board.get("approved_at")), "at": (board or {}).get("approved_at")},
        "keyframes": None,
        "assets": {"approval": assets.get("fingerprint") or "none", "at": approved.get("at"),
                   "by": approved.get("by"), "target": f"assets:{page['ep']}"},
    }
    if keyframes_view is not None:
        approvals["keyframes"] = {"approval": keyframes_view["approval"], "at": keyframes_view["approved_at"],
                                  "anyway": keyframes_view["anyway"], "by": recorded.get("by"),
                                  "flagged": list(recorded.get("flagged") or []), "target": keyframes_view["target"]}
    auto = [name for name in ("keyframes", "assets")
            if approvals[name] and approvals[name]["by"] == FAST_TRACK_APPROVED
            and approvals[name]["approval"] == "current"]
    pending = []
    if approvals["keyframes"] is not None and approvals["keyframes"]["approval"] != "current":
        pending.append("keyframes")
    if approvals["assets"]["approval"] != "current":
        pending.append("assets")

    lines = {line["line_id"]: {"line_id": line["line_id"], "speaker": line["speaker"], "text": line["text"]}
             for scene in (script or {}).get("scenes") or [] for line in scene["lines"]}
    fixes = doc.get(assets_step.KEYFRAME_FIXES) or {}
    by_id = {shot["shot_id"]: shot for shot in assets.get("shots") or []}
    shots, flagged, unchecked, fixed = [], [], [], []
    for shot in sorted((board or {}).get("shots") or [], key=lambda item: item["order"]):
        view = by_id.get(shot["shot_id"]) or {}
        verdict = _review_verdict(view)
        if verdict["state"] == "flagged":
            flagged.append(shot["shot_id"])
        elif verdict["state"] in ("unchecked", "not_current") and keyframes_view is not None:
            unchecked.append(shot["shot_id"])
        elif verdict["state"] == "fixed":
            fixed.append(shot["shot_id"])
        clip = view.get("clip")
        shots.append({
            "shot_id": shot["shot_id"], "scene_id": shot["scene_id"], "order": shot["order"],
            "image_name": view.get("image_name"), "image_state": view.get("state") or "none",
            "locked": bool(view.get("locked")), "target": assets_step.shot_target(page["ep"], shot["shot_id"]),
            "clip": None if clip is None else {
                "name": clip.get("name"), "url": clip.get("url"), "state": clip.get("state"),
                "current": clip.get("state") == "current", "target": clip.get("target"),
                "blocked": clip.get("blocked"), "continue": bool(clip.get("continue"))},
            "verdict": verdict, "fix": fixes.get(shot["shot_id"]),
            "lines": [lines[line_id] for line_id in shot["lines"] if line_id in lines],
        })

    render = page.get("render")
    render_view = None
    if render is not None:
        render_view = {"state": render["state"], "out_of_date": render.get("out_of_date"),
                       "duration_s": render.get("duration_s"), "finished_at": render.get("finished_at"),
                       "file": (render.get("output") or {}).get("file")}
    rendered = bool(render_view and render_view["state"] == "completed" and render_view["out_of_date"] is False)
    if not doc:
        status = "not_made"
    elif "keyframes" in pending:
        status = "keyframes_pending"
    elif "assets" in pending:
        status = "assets_pending"
    elif not rendered:
        status = "render_needed"
    else:
        status = "ready"
    metadata = page.get("metadata")
    return {
        "status": status, "ready": status == "ready",
        "headline": _review_headline(status, flagged=flagged, keyframes=approvals["keyframes"], render=render_view),
        "auto_approved": auto, "pending": pending, "approvals": approvals,
        "flagged": flagged, "unchecked": unchecked, "fixed": fixed,
        "spend": _review_spend(page), "render": render_view,
        "metadata_current": None if metadata is None else bool(metadata.get("current")),
        "script_repairs": (script or {}).get(judge_step.REPAIRS) or None,
        "script_minor_issues": [{"scene_id": issue["scene_id"], "kind": issue["kind"], "fix": issue["fix"]}
                                for issue in judge_step.minor_issues((script or {}).get(judge_step.FIRST_WATCH))],
        "shots": shots,
    }


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
            raise WorkflowError(NOT_FOUND, (f"Episode {ep}'s storyboard has no shot {parsed[2]!r} (it has "
                                            f"{shots.shot_ids_phrase(board['shots'])})."))
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
            raise WorkflowError(NOT_FOUND, (f"Episode {ep}'s storyboard has no shot {what!r} (it has "
                                            f"{shots.shot_ids_phrase(board['shots'])})."))
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
        raise WorkflowError(NOT_FOUND, (f"Episode {ep}'s storyboard has no shot {parsed[2]!r} (it has "
                                        f"{shots.shot_ids_phrase(board['shots'])})."))
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
    would go over a cap, then with ``refusal`` (the cap's numbers, plan 23
    A4). Calls nothing but, with *probe_local*, a local ComfyUI's status."""
    ep = parsed[1]
    episode_context(stories, story, ep, step="regenerate", require_memory=False)
    ec, script, board, shot = _clip_shot(stories, story["story_id"], ep, parsed)
    quote = assets_step.clip_quote(ec, script, board, shot, env=env, adapters=adapters, probe_local=probe_local)
    paid = quote["route_class"] == "paid"
    body = {"step": "regenerate", "target": f"shot:{ep}:{shot['shot_id']}:video",
            "est_usd": quote["est_usd"] if paid else 0.0,
            "units": {"clips": 1, "seconds": quote["clip_s"]}, "route_class": quote["route_class"],
            "link": quote["link"], "links": quote["video"]["links"], "ready": quote["ready"],
            "message": quote["message"]}
    if quote.get("refusal"):
        # Plan 23 A4: a cap's refusal with its numbers (``BudgetRefused.as_dict``).
        body["refusal"] = quote["refusal"]
    return body


# ---------------------------------------------------------------- approvals

def _refuse_length(ec, script, board, stage) -> None:
    """The v2 hard length gate (``steps/gates.length_refusal``, phase 7
    stage 6a, DEC-231): ``conflict`` with its sentence -- never "anyway" --
    when a v2 episode's length is outside its template's window."""
    refusal = gates_step.length_refusal(ec, script, board, stage=stage)
    if refusal is not None:
        raise WorkflowError(CONFLICT, refusal)


def script_blocking_issues(script) -> list:
    """The blocking issues an approval of *script* goes over (plan 19 stage
    3): E4's blocking ones (DEC-261) then, on a failed first-watch report,
    J1's (DEC-248) -- ``[{scene_id, kind, fix, check}]``, ``check`` naming
    the report (``schemas.SCRIPT_APPROVED_OVER_CHECKS``)."""
    found = [{"scene_id": issue["scene_id"], "kind": issue["kind"], "fix": issue["fix"], "check": "consistency"}
             for issue in script_step.consistency_blocking_issues(script.get("consistency_report"))]
    report = script.get(judge_step.FIRST_WATCH)
    if report is not None and not report["passed"]:
        found += [{"scene_id": issue["scene_id"], "kind": issue["kind"], "fix": issue["fix"], "check": "first_watch"}
                  for issue in judge_step.blocking_issues(report)]
    return found


def approve_script(stories, story_id, ep, *, approve_anyway=False, now, by=USER_APPROVED) -> dict:
    """Approve episode *ep*'s script; returns it as written.

    ``conflict`` without a script; listing what is not written yet (every
    scene, the framing parts); without a consistency report, or with one that
    is stale or of an older revision (check it again: the script step); and,
    when the report found issues, listing them -- unless *approve_anyway*.
    ``approved_at`` becomes *now*; ``approved_anyway`` too when the approval
    went over issues. Nothing else moves: not the revision, not the report,
    not the story (RC-E2).

    A v2 story (phase 7 stage 6a, DEC-230/231) also needs, before the
    issues are weighed: its first-watch report (J1) written and current --
    never approvable without (``judge.unjudged_refusal``) -- and its
    estimated length inside the template's window (:func:`_refuse_length`,
    never "anyway"); then a first-watch report that found issues refuses,
    naming them, unless *approve_anyway* -- which ``approved_anyway``
    records as it does over E4's. J1 version 2 (DEC-248): a report passes
    when none of its issues is blocking, so minor issues alone never refuse
    and never make the approval an "anyway".

    *by* (plan 19 stage 3, ``schemas.APPROVED_BY``): the fast track's one
    click records ``approved_by: "fast_track"`` and, over an "anyway",
    ``approved_over`` -- the blocking issues it went over
    (:func:`script_blocking_issues`), which the review names; the human's
    approval records neither (and drops a fast track's record)."""
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
                                       "since it ran): check it again (run the script step with check only)."))
    v2 = media_policy.is_v2(story)
    if v2:
        unjudged = judge_step.unjudged_refusal(script, ep, judge_step.j1_version(story, script))
        if unjudged:
            raise WorkflowError(CONFLICT, unjudged)
        _refuse_length(_context(stories, story_id, ep), script,
                       read_episode(stories, story_id, ep, STORYBOARD_DOC), "script")
    # DEC-261: a minor consistency note (a voice taste) never refuses; a blocking issue does.
    blocking = script_step.consistency_blocking_issues(report)
    over_issues = bool(blocking)
    if over_issues and not approve_anyway:
        issues = "; ".join(f"{issue['scene_id'] or 'the episode'} ({issue['kind']}): {issue['fix']}"
                           for issue in blocking)
        count = len(blocking)
        minor = len(script_step.consistency_minor_issues(report))
        notes = f" ({minor} minor note{'' if minor == 1 else 's'} kept for review)" if minor else ""
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s consistency check found {count} issue"
                                       f"{'' if count == 1 else 's'}{': ' + issues if issues else ''}{notes}. Fix "
                                       "them and check again, or approve anyway."))
    over_first_watch = v2 and not script[judge_step.FIRST_WATCH]["passed"]
    if over_first_watch and not approve_anyway:
        raise WorkflowError(CONFLICT, judge_step.issues_refusal(script, ep))
    ec = _context(stories, story_id, ep)
    script["approved_at"] = now
    anyway = over_issues or over_first_watch
    script["approved_anyway"] = now if anyway else None
    script.pop("approved_by", None)
    script.pop("approved_over", None)
    if by == FAST_TRACK_APPROVED:
        script["approved_by"] = FAST_TRACK_APPROVED
        if anyway:
            script["approved_over"] = script_blocking_issues(script)
    return _write(episode_common.write_script, "script", ec, script, now=now, code=CONFLICT)


def approve_storyboard(stories, story_id, ep, *, now) -> dict:
    """Approve episode *ep*'s storyboard; returns it as written.

    ``conflict`` without a storyboard; before its script is approved; while a
    scene of the script has no shots, or has shots planned from an older
    version of it (``scenes[sid].script_rev``, or marked stale); and while
    an entity its prompts were resolved from has changed since (refresh
    them); a v2 story's, while its estimated length with the storyboard is
    outside the template's window (:func:`_refuse_length`, phase 7 stage 6a,
    never "anyway"). ``approved_at`` becomes *now*; nothing else moves."""
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
    _refuse_length(ec, script, board, "storyboard")
    board["approved_at"] = now
    return _write(episode_common.write_storyboard, "storyboard", ec, board, script, now=now, code=CONFLICT)


def approve_assets(stories, story_id, ep, *, now, by=USER_APPROVED) -> dict:
    """Approve episode *ep*'s assets (phase 4; plan "API": ``POST
    /approve/assets:<ep>``, and the fast track's auto-approval); returns
    ``assets.json`` as written. *by* (``schemas.APPROVED_BY``, stage C) is
    who approves: the human, or the fast track's one click on their behalf.

    ``conflict`` before the script is approved and the storyboard approved
    and current (``assets.require_approved``, the step's own check); without
    an ``assets.json`` (run the assets step); while a shot has no image, or
    an unlocked shot's image is not current (``assets.shot_state``: stale,
    failed, none) -- a locked shot keeps the image it has; and while a line
    has no audio in its speaker's pinned voice (``voice_lines.is_measured``).
    Each refusal names the shots or lines and the regenerate target that
    finishes them. A v2 story's, also while its measured length is outside
    the template's window (:func:`_refuse_length`, phase 7 stage 6a, never
    "anyway"). Then every shot's ``assets.approved`` is set (the
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
        state = assets_step.shot_state(ec, shot, link=link, doc=doc)
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
    _require_every_clip(ec, script, board, doc)
    _refuse_length(ec, script, board, "assets")
    for shot in board["shots"]:
        shot["assets"]["approved"] = True
    _write(episode_common.write_storyboard, "storyboard", ec, board, script, now=now, code=CONFLICT)
    doc["approved"] = {"at": now, "fingerprint": assets_step.current_fingerprint(ec, board, script, doc), "by": by}
    try:
        return stories.write_episode_doc(story_id, ep, ASSETS_DOC, doc, now=now)
    except schemas.SchemaError as exc:
        raise WorkflowError(CONFLICT, {"message": "The assets would not be valid with this approval.",
                                       "errors": list(exc.errors)}) from None
    except (KeyError, ValueError) as exc:
        raise WorkflowError(CONFLICT, f"The assets cannot be written: {exc}.") from None


def _require_every_clip(ec, script, board, doc) -> None:
    """A fully animated story's assets approval (``media_policy.fully_animated``:
    v2, tier >= 2, every shot animated): ``conflict`` while a shot not kept
    still has no current clip (``render.unanimated_shots``), naming each with
    what to do (``render.fully_animated_refusal``). Any other story: nothing."""
    if not media_policy.fully_animated(ec.story):
        return
    blocked, unmade = render_step.unanimated_shots(ec, script, board, doc)
    if blocked or unmade:
        link = (sticky_link.recorded(doc, sticky_link.VIDEO) or {}).get("link")
        raise WorkflowError(CONFLICT, render_step.fully_animated_refusal(
            ec, blocked, unmade, action="approved", script=script, doc=doc, link=link))


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


def keyframe_findings(ec, board, doc) -> dict:
    """What stands between episode *ec.ep*'s keyframes and their approval,
    shot by shot (the keyframe approval's own reading, shared with the fast
    track's one click, stage C): ``{"failed": [(shot_id, what J2 found)],
    "unjudged": [shot_id, ...]}`` -- a current verdict (J2) that failed, and
    a shot with no current verdict (none, or one of other images:
    ``judge.verdict_current``). Hashes every image."""
    verdicts = (doc or {}).get(judge_step.KEYFRAME_VERDICTS) or {}
    unjudged, failed = [], []
    for shot, _path, sha, _prev_id, _prev_path, prev_sha in assets_step.keyframe_items(ec, board, doc):
        entry = verdicts.get(shot["shot_id"])
        if not judge_step.verdict_current(entry, sha, prev_sha):
            unjudged.append(shot["shot_id"])
        elif not judge_step.verdict_passed(entry):
            failed.append((shot["shot_id"], judge_step.verdict_text(entry)))
    return {"failed": failed, "unjudged": unjudged}


def approve_keyframes(stories, story_id, ep, *, approve_anyway=False, now, by=USER_APPROVED) -> dict:
    """Approve episode *ep*'s keyframes (phase 7 stage 6b, A16, DEC-230;
    ``POST /approve/keyframes:<ep>``, CLI ``approve ID keyframes:<ep>``);
    returns ``assets.json`` as written. *by* (``schemas.APPROVED_BY``, stage
    C) is who approves: the human, or the fast track's one click on their
    behalf -- the record then carries ``by`` and ``flagged``, the shots it
    went over, so the review screen can show them.

    A v2 story's alone (``conflict`` for a legacy one: its assets approval
    is the one it has). ``conflict`` before the script is approved and the
    storyboard approved and current (``assets.require_approved``); without
    an ``assets.json``; while a shot has no current keyframe (its image on
    disk and current, or locked: ``assets.keyframe_problem``), naming each
    and its regenerate target; and -- unless *approve_anyway* -- while a
    shot's keyframe check (J2) failed or has no current verdict (none, or
    one of other images: ``judge.verdict_current``), naming each with what
    J2 found. Then ``assets.json`` gains ``keyframes_approved {at, anyway,
    fingerprint}`` -- ``anyway`` true when it went over a failed or missing
    verdict, the fingerprint of the keyframes as they are now
    (``assets.keyframes_fingerprint``): once a keyframe changes, the
    approval is stale (:func:`keyframes_approval_state`), derived, never
    cleared (DEC-155). Until it is current no clip is bought (RC-Q3,
    ``assets.clip_hold``). Nothing else moves: not the assets approval, not
    the storyboard, not the story (RC-E2)."""
    story = load(stories, story_id)
    ep = episode_bounds(stories, story, ep)
    if not media_policy.is_v2(story):
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s keyframes have no approval of their own: the keyframe "
                                       "approval and its check (J2) are a v2 story's. Approve the assets "
                                       f"(assets:{ep})."))
    ec = _context(stories, story_id, ep)
    try:
        _script, board = assets_step.require_approved(ec)
    except StepFailed as exc:
        raise WorkflowError(CONFLICT, str(exc)) from None
    doc = read_episode(stories, story_id, ep, ASSETS_DOC)
    if doc is None:
        raise WorkflowError(CONFLICT, f"Episode {ep} has no keyframes yet: make them first (the assets step).")
    link = assets_step.recorded_image_link(doc)
    missing = [shot["shot_id"] for shot in board["shots"]
               if assets_step.keyframe_problem(ec, shot, link=link) is not None]
    if missing:
        targets = [assets_step.shot_target(ep, shot_id) for shot_id in missing]
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s shot{_plural_s(missing)} {_and(missing)} "
                                       f"{'has' if len(missing) == 1 else 'have'} no current keyframe: make "
                                       f"{'it' if len(missing) == 1 else 'them'} (the assets step, or regenerate "
                                       f"{_and(targets)}) or lock {'it' if len(missing) == 1 else 'them'}, then "
                                       "approve the keyframes."))
    findings = keyframe_findings(ec, board, doc)
    unjudged = findings["unjudged"]
    failed = [f"{shot_id} ({text})" for shot_id, text in findings["failed"]]
    if (unjudged or failed) and not approve_anyway:
        found = []
        if failed:
            found.append(f"the keyframe check (J2) found issues in shot{_plural_s(failed)} {'; '.join(failed)}")
        if unjudged:
            found.append(f"shot{_plural_s(unjudged)} {_and(unjudged)} {'has' if len(unjudged) == 1 else 'have'} "
                         "no current keyframe check (J2): run the assets step again (it checks them, free)")
        raise WorkflowError(CONFLICT, (f"Episode {ep}'s keyframes are not approved: {'; and '.join(found)}. "
                                       "Make the shots again (regenerate them, with a note) and check again, or "
                                       "approve anyway."))
    # The shots it goes over, in storyboard order (the failed and the unjudged are disjoint).
    flagged = [shot["shot_id"] for shot in board["shots"]
               if shot["shot_id"] in unjudged or any(shot_id == shot["shot_id"] for shot_id, _text in
                                                      findings["failed"])]
    doc[judge_step.KEYFRAMES_APPROVED] = {"at": now, "anyway": bool(unjudged or failed),
                                          "fingerprint": assets_step.keyframes_fingerprint(ec, board),
                                          "by": by, "flagged": flagged}
    try:
        return stories.write_episode_doc(story_id, ep, ASSETS_DOC, doc, now=now)
    except schemas.SchemaError as exc:
        raise WorkflowError(CONFLICT, {"message": "The assets would not be valid with this keyframe approval.",
                                       "errors": list(exc.errors)}) from None
    except (KeyError, ValueError) as exc:
        raise WorkflowError(CONFLICT, f"The assets cannot be written: {exc}.") from None


def keyframes_approval_state(ec, board, doc) -> str:
    """``none`` (no ``assets.json``, or the keyframes never approved) |
    ``current`` (approved with the fingerprint of the keyframes as they are
    now) | ``stale`` (a keyframe changed since: derived, never cleared,
    DEC-155). Hashes every image."""
    if not board:
        return "none" if not (doc or {}).get(judge_step.KEYFRAMES_APPROVED) else "stale"
    return assets_step.keyframes_state(ec, board, doc)


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
            # Plan 24 stage 1 (D-1/D-5): at the speaking voice's overrun.
            line["timing"] = timing.estimated_timing(
                line["text"], ec.language, provider=voice_lines.speech_provider(ec, line["speaker"]))
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


def _edit_shot_variants(ec, path, shot, value, errors) -> None:
    """A shot's appearance variants, the human's override of its scene's
    states (plan 23 stage D5): ``{char_id: variant_id | null}`` -- each a
    character the shot frames, each variant one that character has (null:
    its base look). Merged over what the shot wears, and kept as the shot's
    own (a re-plan keeps it, like its assets); its keyframe goes stale on
    purpose (the prompt and the identity image change). A variant not
    approved yet is accepted here and refused at the keyframe, with the
    sentence that says so (``shots.variant_refusal``)."""
    if not media_policy.variants_enabled(ec.story):
        errors.append(f"{path}.variants: this story's characters carry no appearance variants (set its character "
                      "sheets mode first)")
        return
    if not isinstance(value, dict):
        errors.append(f"{path}.variants: expected {{char_id: variant_id or null}}")
        return
    framed = {tag[1:] for tag in shot["subject_tags"] if tag.startswith("@")}
    wanted = dict(shot.get("variants") or {})
    before = len(errors)
    for cid, variant_id in value.items():
        if cid not in framed:
            errors.append(f"{path}.variants: {cid!r} is not a character shot {shot['shot_id']} frames")
            continue
        if variant_id is None:
            wanted.pop(cid, None)
            continue
        doc = ec.entities[CHARACTERS].get(cid) or {}
        if not isinstance(variant_id, str) or shots.variant_record(doc, variant_id) is None:
            have = ", ".join(variant["variant_id"] for variant in doc.get("variants") or ()) or "none"
            errors.append(f"{path}.variants.{cid}: {variant_id!r} is not one of {doc.get('name', cid)}'s variants "
                          f"({have})")
            continue
        wanted[cid] = variant_id
    if len(errors) == before:
        shot["variants"] = wanted


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
                    changed = shot["modifiers"] != list(dict.fromkeys(value))
                    shot["modifiers"] = list(dict.fromkeys(value))
                    if changed and media_policy.is_v2(ec.story) and shot["shot_id"] not in to_resolve:
                        # A v2 shot's clip prompt says its modifiers (phase 7 stage 3b).
                        to_resolve[shot["shot_id"]] = (path, None, False)
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
        variants_before = copy.deepcopy(shot.get("variants"))
        if "variants" in item:
            _edit_shot_variants(ec, path, shot, item["variants"], errors)
        if "variants" in shot:
            # A character the shot no longer frames wears nothing in it.
            framed = {tag[1:] for tag in shot["subject_tags"] if tag.startswith("@")}
            shot["variants"] = {cid: vid for cid, vid in shot["variants"].items() if cid in framed}
        if (shot["framing"], shot["action"], shot["subject_tags"]) != before or shot.get("variants") != variants_before:
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


def _prompt_budgets(ec):
    """The word budgets a v2 shot resolved again by an edit is built to
    (stage F2, ``clips.episode_budgets``): the episode's links as the
    storyboard step plans them -- its recorded ones, else the chains' first
    -- read with the process environment alone (no Settings reach an edit;
    every hosted link's budget is its kind's ceiling anyway)."""
    try:
        doc = episode_common.read_episode(ec, ASSETS_DOC)
    except llm_call.StepFailed:
        doc = None
    return clips_step.episode_budgets(ec, None, assets_doc=doc)


def _budget_link(budgets, exc):
    """The link *exc*'s kind of prompt was built for, from *budgets*."""
    if budgets is None:
        return None
    return budgets.image_link if exc.kind == "keyframe" else budgets.video_link


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
    v2 = media_policy.is_v2(ec.story)
    ledger = script_step.ledger_of(ec)
    budgets = _prompt_budgets(ec) if v2 else None
    by_function = lock["motion_rules"]["tier1"]["by_function"]
    scenes = {scene["scene_id"]: scene for scene in script["scenes"]}
    by_id = {shot["shot_id"]: shot for shot in board["shots"]}
    for shot_id, (path, wanted, prompt) in to_resolve.items():
        shot = by_id[shot_id]
        scene = scenes[shot["scene_id"]]
        # DEC-252: on a v2 story the shot's own camera comes before the scene function's rule.
        motion = shots.motion_for(shot["framing"], wanted or shot["camera_motion"], scene["function"], lock, v2=v2)
        if wanted is not None and motion["type"] != wanted:
            what = (f"a {shot['framing']} shot" if shot["framing"] in by_function
                    else f"a {scene['function']} scene")
            errors.append(f"{path}.camera_motion: the style moves {what} with {motion['type']}, not {wanted!r}")
            continue
        # A v2 shot's clip prompt names its camera (phase 7 stage 3b): a motion
        # swap writes it again, and only it -- the image prompt stays as it is.
        if not prompt and not v2:
            shot["camera_motion"] = motion["type"]
            shot["motion"] = motion
            continue
        plan = {"framing": shot["framing"], "action": shot["action"], "subjects": shot["subject_tags"]}
        if v2:
            plan.update(lines=list(shot["lines"]), camera_motion=motion["type"], modifiers=list(shot["modifiers"]))
            if shot.get("variants"):
                # Plan 23 stage D5: the appearance variants the shot's characters wear.
                plan["variants"] = dict(shot["variants"])
        try:
            # Phase 8 stage B: a v2 shot after another of its scene keeps its continuity reference.
            index = board["shots"].index(shot)
            continuity = v2 and shots.continues_scene(board["shots"], index)
            resolved = shots.resolve_shot(plan, scene=scene, entities=ec.entities, style_lock=lock,
                                          consistency_mode=ec.consistency_mode, v2=v2, ledger=ledger,
                                          continuity=continuity, budgets=budgets,
                                          previous_plan=shots.previous_plan(board["shots"], index) if v2 else None)
        except shots.PromptOverBudget as exc:
            # Stage F2: the edit would make a prompt its link cannot take; refused, nothing written.
            errors.append(f"{path}: {exc.named(shot_id, _budget_link(budgets, exc))}")
            continue
        except (KeyError, ValueError) as exc:
            raise WorkflowError(CONFLICT, (f"Shot {shot_id} names something the story no longer has ({exc}): plan "
                                           f"scene {scene['scene_id']} again (the storyboard step).")) from None
        shot["camera_motion"] = motion["type"]
        shot["motion"] = motion
        if v2:
            shot["video_prompt"] = resolved["video_prompt"]
            if not prompt:
                continue
            shot["prompt_layout"] = resolved["prompt_layout"]
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
        v2 = media_policy.is_v2(ec.story)
        try:
            trial = shots.refresh_prompts(trial, script, entities=ec.entities, style_lock=ec.style_lock,
                                          consistency_mode=ec.consistency_mode, v2=v2,
                                          ledger=script_step.ledger_of(ec), budgets=_prompt_budgets(ec) if v2 else None)
        except shots.PromptOverBudget as exc:
            raise _invalid_values(message, [f"refresh_prompts: {exc}"])
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
    for kind in sticky_link.VIDEO_KINDS:
        # Plan 22: a native-speech episode switches either of its two video links.
        if kind not in wanted:
            continue
        doc = read_episode(stories, story_id, ep, ASSETS_DOC)
        new = assets_step.switched_video_doc(ec, doc, wanted[kind], now=now, kind=kind)
        if new is not None:
            _write_assets_doc(stories, story_id, ep, new, now=now, what="this video link")
    return board


def _shot_mode_states(ec, script, shot, *, env_link):
    """``{"clip", "image"}``: the states of *shot*'s clip (``clips.clip_state``
    on the link its mode puts it on; ``none`` without a record) and keyframe
    (``assets.shot_state``) as ``assets.json`` stands on disk now."""
    doc = read_episode(ec.store, ec.story_id, ec.ep, ASSETS_DOC)
    clip = "none"
    if shot["assets"].get("clip"):
        image = assets_step.shot_image_path(ec, shot)
        try:
            clip = clips_step.clip_state(ec, shot, script, link=clips_step.class_link(ec.story, shot, doc, env_link),
                                         tier=clips_step.tier_of(ec), flags=clips_step.shot_flags(shot, doc),
                                         image_sha=assets_step._sha256_file(image) if image is not None else None)
        except (KeyError, ValueError):
            clip = "stale"
    return {"clip": clip, "image": assets_step.shot_state(ec, shot, doc=doc)}


def patch_shot_mode(stories, story_id, ep, shot_id, fields, *, now, env=None) -> dict:
    """Who makes shot *shot_id*'s clip and keyframe (plan 25 stage 1, D-1):
    *fields* ``{"clip"?: "auto" | "manual" | None, "image"?: ...}`` (None
    clears that kind: back to the story's profile) into ``assets.json``'s
    ``shot_modes`` (``assets.moded_assets_doc``); the storyboard is never
    written, the assets approval goes stale with the fingerprint. Returns::

        {"shot_id", "modes": {"clip": "auto"|"manual"|None, "image": "auto"|"manual"},
         "set": {the kinds assets.json sets}, "link": {"clip", "image"},
         "states": {"clip", "image"}, "stale": ["clip"|"image", ...],
         "verdict": {"clip": {link, est_usd, allowed, reason} | None, "image": ... | None}}

    ``stale`` names what was current and is not any more: a clip or a
    keyframe made on the other link -- the link-change rule
    (``clips.clip_state``, ``assets.image_state``), nothing written on the
    shot. ``verdict``: for a kind sent whose mode is now ``auto``, the
    gate's dry run (``assets.shot_mode_verdict``; the image quote for one
    keyframe), calling nothing. Refused: ``not_found`` for a shot the
    storyboard does not have; ``invalid`` for nothing sent, a value that is
    not ``auto``/``manual``/null, a clip mode on a story that is not native
    speech (its clips are one link an episode), an image mode on a legacy
    story, an image mode ``auto`` on a story whose images are all the
    human's (switch the story's images first); ``conflict`` without a
    storyboard or a script."""
    story = load(stories, story_id)
    ep = episode_bounds(stories, story, ep)
    board = read_episode(stories, story_id, ep, STORYBOARD_DOC)
    if board is None or not board["shots"]:
        raise _no_storyboard(ep)
    script = read_episode(stories, story_id, ep, SCRIPT_DOC)
    if script is None or not script["scenes"]:
        raise _no_script(ep)
    shot = next((item for item in board["shots"] if item["shot_id"] == shot_id), None)
    if shot is None:
        raise WorkflowError(NOT_FOUND, f"Episode {ep}'s storyboard has no shot {shot_id!r}.")
    errors = []
    unknown = sorted(set(fields) - set(schemas.SHOT_MODE_KINDS))
    if unknown:
        errors.append(f"{', '.join(unknown)}: not a mode (send clip or image)")
    if not set(fields) & set(schemas.SHOT_MODE_KINDS):
        errors.append("send clip or image: 'auto' or 'manual' (or null to clear)")
    for kind in schemas.SHOT_MODE_KINDS:
        if kind in fields and fields[kind] is not None and fields[kind] not in schemas.SHOT_MODES:
            errors.append(f"{kind}: expected 'auto' or 'manual' (or null to clear), not {fields[kind]!r}")
    if fields.get("clip") is not None and not media_policy.native_speech(story):
        errors.append(f"clip: shot {shot_id}'s clip is made by the app on the episode's one video link: a per-shot "
                      "mode needs a native-speech story (Native speech, or Native speech — your own clips)")
    if fields.get("image") is not None and not media_policy.is_v2(story):
        errors.append(f"image: shot {shot_id}'s keyframe follows the story: a per-shot mode needs a v2 story")
    elif fields.get("image") == video_plan.AUTO and media_policy.images_manual(story):
        errors.append(f"image: shot {shot_id}'s keyframe cannot be set to auto: this story's images are all your "
                      "own uploads (Images: manual); switch the story's images to the app first, then set the shots "
                      "you make yourself to 'my own'")
    if errors:
        raise _invalid_values(f"Shot {shot_id}'s mode would not be valid with these values.", errors)
    ec = _context(stories, story_id, ep)
    env_link = (sticky_link.recorded(read_episode(stories, story_id, ep, ASSETS_DOC), sticky_link.VIDEO) or {}).get(
        "link")
    before = _shot_mode_states(ec, script, shot, env_link=env_link)
    doc = read_episode(stories, story_id, ep, ASSETS_DOC)
    changes = {kind: fields[kind] for kind in schemas.SHOT_MODE_KINDS if kind in fields}
    new = assets_step.moded_assets_doc(ec, doc, shot_id, changes, now=now)
    if new is not None:
        _write_assets_doc(stories, story_id, ep, new, now=now, what="this shot's mode")
        doc = new
    after = _shot_mode_states(ec, script, shot, env_link=env_link)
    verdict = {"clip": None, "image": None}
    modes = {"clip": clips_step.clip_mode(ec.story, shot, doc),
             "image": assets_step.image_mode(ec.story, shot, doc)}
    if "clip" in changes and modes["clip"] == video_plan.AUTO:
        verdict["clip"] = assets_step.shot_mode_verdict(ec, script, board, shot, env=env)
    if "image" in changes and modes["image"] == video_plan.AUTO:
        verdict["image"] = _keyframe_verdict(ec, board, doc, env=env)
    image_link = assets_step.shot_image_link(ec.story, shot, assets_step.recorded_image_link(doc), doc)
    return {
        "shot_id": shot_id, "modes": modes, "set": dict(((doc or {}).get("shot_modes") or {}).get(shot_id) or {}),
        "link": {"clip": clips_step.class_link(ec.story, shot, doc, env_link) if modes["clip"] else None,
                 "image": image_link or clips_step.planned_image_link(ec, env, assets_doc=doc)},
        "states": after,
        "stale": [kind for kind in schemas.SHOT_MODE_KINDS if before[kind] == "current" and after[kind] != "current"],
        "verdict": verdict,
    }


def patch_handoff(stories, story_id, ep, fields, *, now) -> dict:
    """Remember where the human makes episode *ep*'s clips (plan 25 stage 2,
    D-2): *fields* ``{"platform": "flow" | "higgsfield", "model"?: <a model
    of that platform> | None}`` into ``assets.json``'s ``handoff`` (None or
    no model: the platform's default again), which ``GET .../handoff`` reads
    when its query names none. Returns ``{"handoff": {"platform", "model"?},
    "platform", "model"}`` -- the memory and the model it makes (the
    platform's default when none is saved). The assets approval is untouched
    (the fingerprint never reads it). Refused: ``invalid`` for no platform,
    an unknown platform or a model the platform does not list."""
    story = load(stories, story_id)
    ep = episode_bounds(stories, story, ep)
    errors = []
    unknown = sorted(set(fields) - {"platform", "model"})
    if unknown:
        errors.append(f"{', '.join(unknown)}: not a handoff setting (send platform and model)")
    platform, model = fields.get("platform"), fields.get("model")
    preset = None
    try:
        preset = platforms.load(platform) if platform else None
    except platforms.PresetError as exc:
        errors.append(f"platform: {exc}")
    if not platform:
        errors.append(f"platform: send one of {', '.join(platforms.PLATFORMS)}")
    elif preset is not None and model is not None and model not in preset["models"]:
        errors.append(f"model: unknown model {model!r} on {preset['name']} (one of {', '.join(preset['models'])})")
    if errors:
        raise _invalid_values("The handoff's platform would not be valid with these values.", errors)
    ec = _context(stories, story_id, ep)
    doc = read_episode(stories, story_id, ep, ASSETS_DOC)
    new = assets_step.handed_assets_doc(ec, doc, platform, model, now=now)
    if new is not None:
        _write_assets_doc(stories, story_id, ep, new, now=now, what="this handoff setting")
        doc = new
    return {"handoff": dict(doc["handoff"]), "platform": platform, "model": model or preset["default_model"]}


def _keyframe_verdict(ec, board, doc, *, env) -> dict:
    """The image quote of one keyframe as a mode verdict (``assets.keyframe_verdict``)."""
    return assets_step.keyframe_verdict(ec, board, doc, env=env)


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


def storyboard_units(ec, env=None) -> dict:
    """The T1 calls a storyboard step would make now: ``{"t1_calls",
    "scenes": [scene_id, ...], "refusal": sentence | None}`` -- one per scene
    with no plan, a stale plan or a fast one
    (``storyboard.scenes_to_plan``), or -- a fully animated v2 story, *env*
    the Settings values the step would run with (phase 7 follow-up, stage E)
    -- too few beat shots for its link's longest clip
    (``storyboard.short_of_beats``). While the script is not complete the
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
    limit = storyboard_step.max_shot_s(ec, env) if media_policy.is_v2(ec.story) else None
    short = storyboard_step.short_of_beats(ec, script, plans, limit)
    todo = [scene["scene_id"] for scene in storyboard_step.scenes_to_plan(script, plans, sources, stale, short)]
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
    verdict = fast_track_step.paid_verdict(units, ep=ec.ep, fully_animated=media_policy.fully_animated(ec.story))
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
        if item.get("variant"):
            _twist_variant_request(stories, story, item["variant"], request)
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


def _twist_variant_request(stories, story, proposed, request) -> None:
    """An N1v2 twist's appearance variant (plan 23 stage D5) checked as
    :func:`add_variant` will create it (:func:`check_variant_fields`; the
    character gone is a ``conflict``: reject the twist), and the payload's
    ``variant`` (``{char_id, variant_id, label}``) and ``variant_job`` (the
    regenerate job of its sheets, which the caller queues behind the
    estimate gate) added."""
    char_id = proposed["char_id"]
    try:
        doc = read_entity(stories, story["story_id"], CHARACTERS, char_id)
    except WorkflowError:
        raise WorkflowError(CONFLICT, (f"This twist gives {char_id!r} an appearance variant, and the story has no "
                                       "such character any more: reject it, or run propose-next again.")) from None
    clean = check_variant_fields(stories, story, char_id, {"label": proposed["label"],
                                                           "delta_text": proposed["delta_text"]}, doc=doc)
    variant_id = _variant_id(clean["label"], {variant["variant_id"] for variant in doc.get("variants") or ()})
    request["variant"] = {"char_id": char_id, "variant_id": variant_id, "label": clean["label"]}
    request["variant_job"] = {"step": "regenerate",
                              "params": {"target": variant_target(char_id, variant_id), "note": None}}
    many = len(refimages.character_images(story)) > 1
    request["message"] += (f" {doc['name']} gains the appearance variant '{clean['label']}': its "
                           f"{variant_sheets_phrase(story)} {'are' if many else 'is'} made next, then approve it "
                           f"({VARIANT_APPROVAL}:{char_id}:{variant_id}).")


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
      ``approved_at`` and ``approvals.season`` never move; a twist with an
      appearance variant (N1v2, plan 23 stage D5) first creates it
      (:func:`add_variant`, ``source: "twist"``) and the payload's
      ``variant_job`` names the job of its sheets, which the caller queues
      behind the estimate gate;
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
    if request.get("variant"):
        # Plan 23 stage D5: the twist's appearance variant, once the decision is recorded (outside the
        # store lock: a character is written under the uploads' lock first, never the other way round).
        proposed = checked["variant"]
        try:
            made = add_variant(stories, story_id, proposed["char_id"],
                               {"label": proposed["label"], "delta_text": proposed["delta_text"]}, now=now,
                               source="twist")
        except WorkflowError as exc:
            raise WorkflowError(CONFLICT, (f"The twist is accepted, but its appearance variant could not be "
                                           f"created: {exc} Add it by hand on the cast page.")) from None
        request["variant"]["variant_id"] = made["variant"]["variant_id"]
        request["variant_job"]["params"]["target"] = made["target"]
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


# ================================================================ agent mode
#
# Plan 21 stage 1 (U3; DEC-263's task C): a story created with ``mode:
# "agent"`` (``generation_profile.mode``) can be taken from its seed to
# episode 1 rendered by one job, ``story-fast-track``
# (``steps/story_fast_track.py``), which approves each document by the rules
# above, ``by: agent``. A Studio story (the default; no ``mode`` key) is
# untouched: every function here refuses it or is never called for it.

AGENT_STEP = agent_step.STEP
AGENT_STEPS = (AGENT_STEP,)
CONCEPTS_PARAMS = concepts_step.PARAMS


def story_mode(story) -> str:
    """The story's mode: ``agent``, or ``studio`` (no ``mode`` key: every
    story created before plan 21, and every Studio story)."""
    return (story.get("generation_profile") or {}).get("mode") or defaults.MODE_STUDIO


def require_agent_mode(story) -> None:
    """``conflict`` unless the story was created (or patched) in agent mode."""
    if story_mode(story) != defaults.MODE_AGENT:
        raise WorkflowError(CONFLICT, (
            "The agent run is for a story in agent mode: this story is in Studio mode, where each step waits for "
            "your approval. Run its steps one by one, or create a story with mode agent."))


# The story documents a step job awaits approval for (the web layer's
# ``_job_doc``), by the approval that settles each.
AGENT_JOB_DOCS = (("concepts", "concept"), ("bible", "bible"), ("style", "style"), ("cast", "cast"),
                  ("places", "places"), ("season", "season"))


def agent_approved_docs(stories, story_id) -> list:
    """The story documents whose approval stands now -- ``concepts``,
    ``bible``, ``style``, ``cast``, ``places``, ``season``, and
    ``knowledge`` once approved at its revision: the jobs awaiting them are
    settled once an agent run, which approves them in-process, has ended."""
    story = load(stories, story_id)
    docs = [doc for doc, key in AGENT_JOB_DOCS if story["approvals"].get(key)]
    if knowledge_current(knowledge(stories, story_id)):
        docs.append("knowledge")
    return docs


def agent_request(params) -> dict:
    """The agent run's params: none (``invalid`` otherwise)."""
    if params:
        raise WorkflowError(INVALID, f"'{AGENT_STEP}' takes no parameters.")
    return {}


def concepts_request(params) -> int:
    """A concepts step's *params* (``{count?}``, 1 to
    ``concepts.CALLS``; absent, ``concepts.CALLS``): the number of concepts
    it writes; ``invalid`` otherwise (``concepts.read_count``'s rule)."""
    _unknown_keys(params, CONCEPTS_PARAMS, "concepts")
    return _step_refusal_as(INVALID, concepts_step.read_count, params)


def _step_refusal_as(code, call, *args, **kwargs):
    """*call*, its ``StepFailed`` a *code* refusal with the step's sentence."""
    try:
        return call(*args, **kwargs)
    except StepFailed as exc:
        raise WorkflowError(code, str(exc)) from None


def agent_cast_pick(stories, story) -> list:
    """The cast the agent run picks (plan 21 decision 2): the chosen
    concept's ``cast_sketch`` names in its order, at most
    ``story_fast_track.CAST_PICK_MAX``, and never more new characters than
    :data:`MAX_CAST` leaves room for beside the story's own (a name the story
    has is included at no cost). Empty for a concept with no sketch:
    :func:`cast_request` then refuses it."""
    existing = list_entities(stories, story["story_id"], CHARACTERS)
    have = {entities_step.name_key(doc["name"]) for doc in existing}
    room = MAX_CAST - len(existing)
    pick, seen = [], set()
    for name in sketch_names(story):
        key = entities_step.name_key(name)
        if key in seen:
            continue
        if len(pick) >= agent_step.CAST_PICK_MAX:
            break
        if key not in have:
            if room <= 0:
                continue
            room -= 1
        seen.add(key)
        pick.append(name)
    return pick


def agent_preview_needed(stories, story_id) -> bool:
    """Whether the style still needs its preview strip: no
    ``style_preview.json``, or one with no image made."""
    doc = read_doc(stories, story_id, preview_step.DOC_NAME, schemas.style_preview_errors)
    return doc is None or not doc["images"]


def _preview_verdict(stories, story, *, env) -> dict:
    return preview_step.estimate(env, route=story["generation_profile"]["route"],
                                 story_spent=cost_total(stories, story["story_id"]))


def agent_preview_refusal(stories, story, *, env):
    """Why the preview strip cannot be made now (its estimate's sentence:
    the route's gate of ``style_preview``), or None."""
    verdict = _preview_verdict(stories, story, env=env)
    return None if verdict["ready"] else verdict["message"]


def agent_season_todo(doc) -> list:
    """The arc entries of season.json *doc* S2 has not expanded yet (S1's
    skeleton leaves their ``open_hooks_out`` empty; S2 writes one to three),
    in order."""
    return [entry["ep"] for entry in (doc or {}).get("arc") or () if not entry.get("open_hooks_out")]


def knowledge_current(doc) -> bool:
    """Whether the knowledge base *doc* is approved at its current
    revision (the episode gate's rule, ``episode_common.knowledge_refusal``)."""
    return bool(doc and doc.get("approved_at") and doc.get("approved_rev") == doc.get("rev"))


def _verdict_money(verdict) -> tuple:
    """``(est_usd, paid)`` of an image verdict (``imaging.estimate``'s
    shape): the first runnable link's paid price, or -- while it is not
    ready -- the price of the paid link the gates refuse (the paid way the
    images would take: ``fast_track.paid_verdict``'s rule)."""
    if verdict["ready"]:
        paid = verdict.get("route_class") == "paid"
        return (float(verdict.get("est_usd") or 0.0) if paid else 0.0), paid
    refused = [row for row in verdict.get("links") or []
               if row.get("paid") and str(row.get("reason") or "").startswith("refused")]
    if refused:
        return float(refused[0].get("est_usd") or 0.0), True
    return 0.0, False


def _agent_generation(stories, story, units, *, env, probe_local=False) -> dict:
    """A part's images and edits as the route's gate and estimate read them
    (``_generation_gate``, ``_generation_estimate``)::

        {"est_usd", "paid": bool, "refusal": sentence | None, "message": sentence}

    -- ``IMAGE_CHAIN``'s verdict on the images (a v2 story's quality links),
    then the editor's on the edits (``edit_readiness``, with the advice the
    route gives). Calls nothing but, with *probe_local*, a local editor's
    status probe."""
    est, paid, refusals, words = 0.0, False, [], []
    if units.get("images"):
        verdict = image_verdict(stories, story, units["images"], env=env)
        usd, is_paid = _verdict_money(verdict)
        est, paid = est + usd, paid or is_paid
        (words if verdict["ready"] else refusals).append(verdict["message"])
    if units.get("edit_images"):
        edit = edit_readiness(stories, story, env=env, qty=units["edit_images"], probe_local=probe_local)
        usd, is_paid = _verdict_money(edit)
        est, paid = est + usd, paid or is_paid
        if edit["ready"]:
            words.append(f"Then {units['edit_images']} reference image{'' if units['edit_images'] == 1 else 's'} "
                         f"edited from the portraits: {edit['message']}")
        else:
            refusals.append(f"{edit['message']} {refimages.editor_advice(story, edit)}")
    return {"est_usd": round(est, 4), "paid": paid, "refusal": refusals[0] if refusals else None,
            "message": " ".join(words)}


def agent_generation_refusal(stories, story, units, *, env, readiness=None, probe_local=False):
    """Why a part of the agent run that makes *units* (``_units``' shape)
    cannot start now, or None: the key gate when it calls the LLM chain
    (:func:`llm_route`; *readiness* the caller's DEC-073 rule, none by
    default), then the image chain's verdict and the editor's
    (:func:`_agent_generation`) -- the gates the route's job of that step
    meets before it exists."""
    if units.get("llm_calls"):
        refusal = llm_route(env, readiness=readiness or (lambda _links, _keys: None))[3]
        if refusal:
            return refusal
    return _agent_generation(stories, story, units, env=env, probe_local=probe_local)["refusal"]


def _agent_episode_usd(story, env) -> float:
    """What episode 1 may spend before the story is ready to price it
    exactly: nothing on the free budget profile; the Quality preset's own
    figure (``media_policy.preset_estimate``, with the keyframe auto-fix's
    ceiling) on the quality one; the profile's ``cap_usd`` otherwise."""
    profile = (story.get("generation_profile") or {}).get("budget_profile")
    if profile == defaults.DEFAULT_BUDGET_PROFILE:
        return 0.0
    try:
        cap = float(budget_mod.profile_settings(profile).get("cap_usd") or 0.0)
    except (OSError, ValueError, KeyError, TypeError):
        cap = 0.0
    if profile in defaults.NATIVE_SPEECH_PROFILES:
        # Plan 22: a native-speech story's own figure (its speech model, links and retake budget).
        try:
            usd = float(media_policy.native_speech_estimate(gating.merged_env(env), story=story)["episode_usd"])
        except Exception:  # noqa: BLE001 - a predicted figure falls back to the profile's own
            usd = cap
        fix = media_policy.keyframe_fix(story)
        return round(usd + (float(fix["cap_usd"]) if fix else 0.0), 4)
    if profile != "quality":
        return round(cap, 4)
    try:
        usd = float(media_policy.preset_estimate(gating.merged_env(env))["episode_usd"])
    except Exception:  # noqa: BLE001 - a predicted figure falls back to the profile's own
        usd = cap
    fix = media_policy.keyframe_fix(story)
    return round(usd + (float(fix["cap_usd"]) if fix else 0.0), 4)


def _agent_episode_calls(story) -> int:
    """The most LLM calls episode 1's fast track makes before its script
    exists: E1, an E2 a body scene, E3, E4, a T1 a scene, an M1 a platform."""
    try:
        slots = timing.episode_slots(templates.load_episode_template(story["episode_template_id"]), agent_step.EPISODE)
    except (KeyError, ValueError, TypeError, OSError):
        return 0
    body = sum(1 for slot in slots if slot == "body")
    return 1 + body + 2 + len(slots) + fast_track_step.M1_CALLS


def _agent_episode_seconds(story, ft=None) -> float:
    """Episode 1's time budget: the fast track's own (``budget_seconds``,
    from its estimate *ft* when there is one), else its free-chain hour, or
    its ceiling once clips are bought (tier >= 2)."""
    tier = int((story.get("generation_profile") or {}).get("tier") or 1)
    if ft is None:
        return float(fast_track_step.FAST_TRACK_BUDGET_CEILING_SECONDS if tier >= 2
                     else fast_track_step.FAST_TRACK_BUDGET_SECONDS)
    shots = int(ft["render"]["shots"] or 0)
    clips = (ft.get("video") or {}).get("count")
    clips = (shots if tier >= 2 else 0) if clips is None else int(clips)
    v2 = media_policy.is_v2(story)
    fix = media_policy.keyframe_fix(story) if v2 else None
    redraws = int(fix["max_redraws_per_shot"]) * shots if fix else 0
    return fast_track_step.budget_seconds(shots=shots, clips=clips, v2=v2, redraws=redraws)


def _agent_caps(stories, story, *, env) -> tuple:
    """``(caps, budget, spent)``: the fast track's ``caps`` shape
    (``{"allow_paid", "episode"|"day"|"story": {"cap_usd", "spent_usd",
    "left_usd"}}``, episode 1's ledger rows for the episode), the budget
    settings (None when they cannot be read) and ``{"episode", "day",
    "story", "day_extra"}`` already spent (and today's extra on the daily cap)."""
    story_id = story["story_id"]
    try:
        budget_obj = gating.budget_of(gating.merged_env(env))
    except ValueError:
        budget_obj = None
    story_spent = cost_total(stories, story_id)
    ep_spent = 0.0
    path = os.path.join(stories.story_dir(story_id), COST_LEDGER)
    if os.path.isfile(path) and not os.path.islink(path):
        try:
            ep_spent = float(CostLedger(path).totals(agent_step.EPISODE)["est_usd"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StoryUnreadable(story_id, f"{story_store.STORIES_DIRNAME}/{story_id}/{COST_LEDGER}",
                                  [f"{type(exc).__name__}: {exc}"]) from None
    state = budget_mod.day_state()
    spent = {"episode": ep_spent, "day": state.spent, "story": story_spent, "day_extra": state.extra}
    caps = {"allow_paid": bool(budget_obj and budget_obj.allow_paid)}
    if budget_obj is not None:
        for name, cap, extra in (("episode", budget_obj.per_episode_cap_usd, 0.0),
                                 ("day", budget_obj.daily_cap_usd, state.extra),
                                 ("story", budget_obj.per_story_cap_usd, 0.0)):
            caps[name] = {"cap_usd": cap, "spent_usd": round(spent[name], 4),
                          "left_usd": round(max(0.0, cap + extra - spent[name]), 4)}
        if state.extra > 0:
            caps["day"]["extra_usd"] = round(state.extra, 4)
    return caps, budget_obj, spent


def _agent_row(name, *, kept, units=None, est_usd=0.0, paid=False, exact=True, refusal=None, message="",
               seconds=None) -> dict:
    """One part of the agent run's estimate (see
    :func:`story_fast_track_estimate`)."""
    counted = _units()
    counted.update(units or {})
    return {
        "part": name, "number": agent_step.PARTS.index(name) + 1, "label": agent_step.LABELS[name],
        "kept": kept, "exact": exact, "units": counted,
        "est_usd": 0.0 if kept else round(float(est_usd), 4), "paid": bool(paid) and not kept,
        "seconds": 0.0 if kept else float(agent_step.PART_BUDGET_SECONDS if seconds is None else seconds),
        "refusal": None if kept else refusal, "message": message,
    }


def _agent_generation_row(stories, story, name, units, *, env, llm_refusal, probe_local, exact=True,
                          message="") -> dict:
    """A part still to do that makes *units*: the key gate when it calls the
    LLM chain, then :func:`_agent_generation`'s price and refusal."""
    gen_part = _agent_generation(stories, story, units, env=env, probe_local=probe_local)
    refusal = (llm_refusal if units.get("llm_calls") else None) or gen_part["refusal"]
    text = " ".join(part for part in (message, gen_part["message"]) if part)
    return _agent_row(name, kept=False, units=units, est_usd=gen_part["est_usd"], paid=gen_part["paid"],
                      exact=exact, refusal=refusal, message=text)


def _agent_llm_row(name, calls, *, llm_refusal, message="", exact=True) -> dict:
    """A part still to do that calls only the LLM chain (*calls* calls):
    refused by the key gate when it calls it at all."""
    return _agent_row(name, kept=False, units={"llm_calls": calls}, exact=exact,
                      refusal=llm_refusal if calls else None, message=message)


# Plan 22 stage 1 (DEC-273): which parts of the agent run's own rows write
# at least one premium prompt (prompts.PREMIUM_PROMPT_IDS) -- the concept
# (C1), the bible (B1, among B1/B2/B3) and episode 1's script (E1/E2/E3/J1,
# among episode 1's own llm_calls total, which also counts E4 and T1).
_PREMIUM_TEXT_PARTS = ("concepts", "bible", "episode")


def _premium_text_estimate(pending_rows, env) -> dict:
    """``{"usd", "calls", "message"}``: the extra cost of writing on
    ``STORY_LLM_PREMIUM_CHAIN`` rather than the free chain, for
    :func:`story_fast_track_estimate`'s summary line.

    *calls* counts every ``llm_calls`` unit of the concept, bible and
    episode-1 rows still pending -- an upper bound, not a per-prompt count:
    those rows do not say which of their calls are the premium ids (C1; B1;
    E1, E2, E3, J1) versus their free-tier neighbours (B2, B3; E4, T1), so
    every call of the three parts is counted. 0 once none of them is still
    pending. ``usd`` is 0 and ``message`` says why a call would still happen
    but cannot be priced (no keyed paid link in the premium chain, or
    ``allow_paid`` off) -- the same reasons :func:`llm_call.call_json` would
    skip the link for, read ahead of time rather than guessed."""
    calls = sum((row.get("units") or {}).get("llm_calls", 0)
                for row in pending_rows if row.get("part") in _PREMIUM_TEXT_PARTS)
    if not calls:
        return {"usd": 0.0, "calls": 0, "message": ""}

    chain = llm_call.resolve_premium_chain(env)
    keys = llm_call.resolve_keys(env)
    link = next((candidate for candidate in chain
                 if keys.get(candidate.provider) and not llm_call.is_free_link(candidate)), None)
    if link is None:
        paid = next((candidate for candidate in chain if not llm_call.is_free_link(candidate)), None)
        reason = (f"no key for {registry.PROVIDERS[paid.provider].env_key}" if paid is not None
                  else "the premium chain has no paid link")
        return {"usd": 0.0, "calls": calls, "message": f"No premium writing: {reason}."}
    if not gating.budget_of(gating.merged_env(env)).allow_paid:
        return {"usd": 0.0, "calls": calls, "message": "No premium writing: allow_paid is off."}

    from clipping.providers import pricing

    from .steps import llm_spend

    try:
        pricing.llm_price_for(link)  # PriceUnknown without a row
        per_call = llm_spend.worst_call_usd(link)
    except pricing.PriceUnknown as exc:
        return {"usd": 0.0, "calls": calls, "message": f"No premium writing: {exc}"}
    # The thinking room a call on *link* is sent with; an Anthropic link's
    # follows its effort (plan 23 stage D1), so the widest of the premium
    # families' efforts is counted -- an upper bound, as this whole sum is.
    efforts = set(prompts.ANTHROPIC_EFFORT.values()) | {None}
    headroom = max(registry.output_headroom(link, effort) for effort in efforts)
    if headroom:
        per_call = llm_spend.ledger_usd(per_call + pricing.llm_estimate_cost(link, 0, headroom))
    usd = llm_spend.ledger_usd(per_call * calls)
    return {"usd": usd, "calls": calls,
            "message": f"+ ${usd:.2f} writing ({calls} premium call{'' if calls == 1 else 's'} on "
                       f"{registry.describe(link)})."}


def story_fast_track_estimate(stories, story, *, env, readiness=None, probe_local=False) -> dict:
    """What the agent run would do and spend now, calling nothing (``GET
    /estimate/story-fast-track``; the run asks it before its first part)::

        {"step": "story-fast-track", "mode": "agent", "ep": 1,
         "parts": [{"part", "number", "label", "kept", "exact", "units": {llm_calls, images, edit_images,
                    tts_chars}, "est_usd", "paid", "seconds", "refusal", "message"}, ...],
         "llm_calls", "est_usd", "exact", "paid": [part labels], "caps", "caps_line",
         "budget": {"seconds", "minutes", "ceiling_seconds", "basis"},
         "episode": <fast_track.estimate of episode 1> | None,
         "text_usd": {"usd", "calls", "message"},
         "ready", "stops_at": {"part", "number", "reason"} | None, "message"}

    One row per part (``story_fast_track.PARTS``), each **kept** when its
    document is approved already (the run keeps it as it is). The units are
    the steps' own counts of what is missing: the concept 1 C1 call (0 when a
    generated card waits to be chosen), the bible its 3 calls (0 once
    written), the style's preview strip (``style_preview.estimate``: 3
    images, unless one is made), the cast (:func:`cast_units` for
    :func:`agent_cast_pick`), the places proposal (1 call), the places
    (:func:`places_units` on the saved proposal), the season (1 + 8 calls,
    or S2 for the entries not expanded), the knowledge base on a v2 story
    (:func:`knowledge_calls`, and the props its D6 may add: up to
    ``schemas.D6_NEW_PROPS_MAX``, each written and drawn) and episode 1
    (:func:`fast_track_estimate`). What cannot be counted exactly yet is an
    upper bound, ``exact`` false: before the concept, the cast is five new
    characters; before the proposal, the places are P0's most (3 places, 3
    props); before the pre-production is approved, episode 1 is priced from
    the budget profile (:func:`_agent_episode_usd`) -- its exact price, and
    the fast track's paid check, come once the story is ready.

    Images are priced as the route's estimates price them (the image chain
    on the story's route, the editor for the sheets); LLM calls at $0 as
    every LLM estimate counts them (free links first, DEC-115). ``text_usd``
    (plan 22 stage 1, :func:`_premium_text_estimate`) is the extra cost of
    writing the concept, bible and episode-1 script on
    ``STORY_LLM_PREMIUM_CHAIN`` instead -- folded into ``est_usd`` and the
    cap check below like any other paid part, named once in the summary
    line ("est $x.xx incl. $y.yy writing") rather than given its own row,
    since it is not its own part of the run. ``est_usd``
    is the parts' sum; ``paid`` names the parts with a paid price. Not
    ``ready`` -- ``stops_at`` the first part that cannot run, with its
    reason: the key gate (*readiness*, the caller's DEC-073 rule; none by
    default) for a part that calls the LLM, a part's own refusal (a concept
    with no style or no cast sketch, the image chain, the editor, the fast
    track's ``stops_at``), and then the sum (RC-A3): paid parts while
    ``allow_paid`` is off, or over the day's or the story's cap (episode 1's
    predicted price against its episode cap too) -- named, with the numbers
    and the fast track's caps line. ``budget`` is the sum of the parts'
    budgets: ``story_fast_track.PART_BUDGET_SECONDS`` a part of
    pre-production, the fast track's own for episode 1, never more than
    ``story_fast_track.STORY_BUDGET_CEILING_SECONDS``. ``conflict`` for a
    Studio story."""
    require_agent_mode(story)
    story_id = story["story_id"]
    approvals = story["approvals"]
    v2 = media_policy.is_v2(story)
    llm_refusal = llm_route(env, readiness=readiness or (lambda _links, _keys: None))[3]
    rows = []

    # 1. the concept
    if approvals.get("concept"):
        rows.append(_agent_row("concepts", kept=True, message=f"Chosen: {story.get('title') or 'the concept'}."))
    elif generated_cards(stories, story_id):
        rows.append(_agent_row("concepts", kept=False, message="The newest generated concept is chosen."))
    else:
        rows.append(_agent_llm_row("concepts", agent_step.CONCEPT_COUNT, llm_refusal=llm_refusal,
                                   message="One concept written from the seed (C1), then chosen."))

    # 2. the bible
    if approvals.get("bible"):
        rows.append(_agent_row("bible", kept=True))
    else:
        calls = len(bible_step.missing_parts(story))
        rows.append(_agent_llm_row("bible", calls, llm_refusal=llm_refusal,
                                   message="Approved once every field is written."))

    # 3. the style and its preview strip
    if approvals.get("style"):
        rows.append(_agent_row("style", kept=True))
    else:
        refusal = None
        template_id = story.get("style_template_id") or concept_style(story.get("concept"))
        if story.get("concept") and style_lock(stories, story_id) is None and (
                not template_id or template_id not in templates.list_style_ids()):
            refusal = ("The concept suggests no shipped style and the story has none: build the style in Studio "
                       f"(shipped: {', '.join(templates.list_style_ids())}), then continue the agent run.")
        units, est, paid, message = {}, 0.0, False, "The style lock is built from its template."
        if agent_preview_needed(stories, story_id):
            verdict = _preview_verdict(stories, story, env=env)
            est, paid = _verdict_money(verdict)
            units = {"images": preview_step.SAMPLES}
            message += f" Its preview strip: {verdict['message']}"
            refusal = refusal or (None if verdict["ready"] else verdict["message"])
        rows.append(_agent_row("style", kept=False, units=units, est_usd=est, paid=paid, refusal=refusal,
                               message=message + " Approved with no taste check."))

    # 4. the cast
    if approvals.get("cast"):
        rows.append(_agent_row("cast", kept=True))
    elif story.get("concept"):
        pick = agent_cast_pick(stories, story)
        try:
            cast_request(stories, story, {"selected": pick})
        except WorkflowError as exc:
            rows.append(_agent_row("cast", kept=False, refusal=str(exc)))
        else:
            units = cast_units(stories, story, selected=pick)
            rows.append(_agent_generation_row(
                stories, story, "cast", units, env=env, llm_refusal=llm_refusal, probe_local=probe_local,
                message=f"{len(pick)} character{'' if len(pick) == 1 else 's'} from the concept's sketch: "
                        f"{_and(pick)}; approved with no taste check."))
    else:
        names = [f"Character {n}" for n in range(1, agent_step.CAST_PICK_MAX + 1)]
        units = cast_units(stories, story, selected=names)
        rows.append(_agent_generation_row(
            stories, story, "cast", units, env=env, llm_refusal=llm_refusal, probe_local=probe_local, exact=False,
            message=f"Up to {agent_step.CAST_PICK_MAX} characters from the concept's sketch, once it is written."))

    # 5. the places proposal, 6. the places
    proposal = places_proposal(stories, story_id)
    listed = (proposal is not None or list_entities(stories, story_id, PLACES)
              or list_entities(stories, story_id, PROPS))
    if approvals.get("places") or listed:
        rows.append(_agent_row("places_proposal", kept=True))
    else:
        rows.append(_agent_llm_row("places_proposal", 1, llm_refusal=llm_refusal,
                                   message="P0 proposes the places and props from the bible and the cast."))
    if approvals.get("places"):
        rows.append(_agent_row("places", kept=True))
    elif listed:
        units = places_units(stories, story)
        rows.append(_agent_generation_row(stories, story, "places", units, env=env, llm_refusal=llm_refusal,
                                          probe_local=probe_local,
                                          message="The saved proposal, made; approved with no taste check."))
    else:
        items = schemas.P0_PLACES_RANGE[1] + schemas.P0_PROPS_MAX
        units = dict(_units(), llm_calls=items * (2 if v2 else 1), images=items)
        rows.append(_agent_generation_row(
            stories, story, "places", units, env=env, llm_refusal=llm_refusal, probe_local=probe_local, exact=False,
            message=(f"Up to {schemas.P0_PLACES_RANGE[1]} places and {schemas.P0_PROPS_MAX} props, as P0 "
                     "proposes them.")))

    # 7. the season
    arc = season(stories, story_id)
    if approvals.get("season"):
        rows.append(_agent_row("season", kept=True))
    else:
        calls = 1 + season_step.DEFAULT_EPISODES if arc is None else len(agent_season_todo(arc))
        planned = season_step.DEFAULT_EPISODES if arc is None else arc["episodes_planned"]
        rows.append(_agent_llm_row("season", calls, llm_refusal=llm_refusal,
                                   message=f"{planned} episodes, each written; then approved."))

    # 8. the knowledge base (v2)
    if not v2:
        rows.append(_agent_row("knowledge", kept=True, message="A legacy story has no knowledge base."))
    else:
        doc = knowledge(stories, story_id)
        if knowledge_current(doc):
            rows.append(_agent_row("knowledge", kept=True))
        else:
            planned = arc["episodes_planned"] if arc else season_step.DEFAULT_EPISODES
            calls = knowledge_step.calls_left(doc, planned)
            units = dict(_units(), llm_calls=calls)
            exact = True
            message = "D4, D5 per episode, D6; then approved."
            if doc is None or "props_registry" not in doc:
                new = schemas.D6_NEW_PROPS_MAX
                units.update(llm_calls=calls + 2 * new, images=new)
                exact = False
                message += f" D6 may add up to {new} props, each written and drawn by the places step."
            rows.append(_agent_generation_row(stories, story, "knowledge", units, env=env, llm_refusal=llm_refusal,
                                              probe_local=probe_local, exact=exact, message=message))

    # 9. episode 1
    ft = None
    if all(row["kept"] for row in rows):
        try:
            ec = episode_context(stories, story, agent_step.EPISODE, step=fast_track_step.STEP)
            ft = fast_track_estimate(ec, env=env)
        except WorkflowError as exc:
            rows.append(_agent_row("episode", kept=False, refusal=str(exc),
                                   seconds=_agent_episode_seconds(story)))
        else:
            calls = ft["llm_calls"]["total"]
            kept = not ft["render"]["needed"] and not calls
            reason = (ft["stops_at"] or {}).get("reason")
            rows.append(_agent_row("episode", kept=kept, units={"llm_calls": calls, "images": ft["images"]["count"]},
                                   est_usd=ft["est_usd"], paid=ft["paid"]["paid"], refusal=reason,
                                   message=ft["paid"]["message"], seconds=_agent_episode_seconds(story, ft)))
    else:
        usd = _agent_episode_usd(story, env)
        profile = story["generation_profile"]["budget_profile"]
        rows.append(_agent_row(
            "episode", kept=False, units={"llm_calls": _agent_episode_calls(story)}, est_usd=usd, paid=usd > 0,
            exact=False, seconds=_agent_episode_seconds(story),
            message=(f"Priced from the {profile} budget profile until the pre-production is approved (up to "
                     f"${usd:.2f}); the fast track's paid check prices it exactly before anything of episode "
                     f"{agent_step.EPISODE} is bought.")))

    # --- the sum, the caps (RC-A3)
    pending = [row for row in rows if not row["kept"]]
    paid_rows = [row for row in pending if row["paid"]]
    caps, budget_obj, spent = _agent_caps(stories, story, env=env)
    caps_line = fast_track_step._caps_line(caps)
    # Plan 22 stage 1 (folded in on review): the premium chain's own cost is
    # a real spend like any paid image, so it is summed and capped the same
    # way -- an unchecked cost here is exactly the bug RC-A3 exists to
    # prevent (a run that passes the image-only check hitting the daily cap
    # on its first C1 call and silently falling to the free writer
    # mid-story). ``calls`` is itself an upper bound (see
    # :func:`_premium_text_estimate`), so a nonzero amount here makes the
    # whole estimate inexact too.
    text_usd = _premium_text_estimate(pending, env)
    total = round(sum(row["est_usd"] for row in pending) + text_usd["usd"], 4)
    paid_total = round(sum(row["est_usd"] for row in paid_rows) + text_usd["usd"], 4)
    exact = all(row["exact"] for row in pending) and not text_usd["usd"]
    paid_labels = [f"{row['label']} (est {'' if row['exact'] else 'up to '}${row['est_usd']:.3f})"
                   for row in paid_rows]
    if text_usd["usd"] > 0:
        paid_labels.append(f"writing (est ${text_usd['usd']:.2f})")
    named = _and(paid_labels)
    sum_refusal = None
    if paid_total > 0:
        if budget_obj is None:
            sum_refusal = "The budget settings cannot be used: fix them in Settings first."
        elif not budget_obj.allow_paid:
            sum_refusal = (f"The agent run needs paid generation -- {named}, est ${paid_total:.3f} in all -- and "
                           f"allow_paid is off. {caps_line} Nothing was generated or spent: turn allow_paid on in "
                           "Settings (the episode, day and story caps must all fit), or choose free links.")
        else:
            over = []
            episode_row = rows[-1]
            if episode_row["paid"] and not episode_row["exact"] and (
                    spent["episode"] + episode_row["est_usd"] > budget_obj.per_episode_cap_usd):
                over.append(f"episode {agent_step.EPISODE} to ${spent['episode'] + episode_row['est_usd']:.2f} "
                            f"of its ${budget_obj.per_episode_cap_usd:.2f} cap")
            if spent["day"] + paid_total > budget_obj.daily_cap_usd + spent["day_extra"]:
                allowed = f" + ${spent['day_extra']:.2f} allowed today" if spent["day_extra"] > 0 else ""
                over.append(f"today to ${spent['day'] + paid_total:.2f} of the ${budget_obj.daily_cap_usd:.2f} "
                            f"daily cap{allowed}")
            if spent["story"] + paid_total > budget_obj.per_story_cap_usd:
                over.append(f"this story to ${spent['story'] + paid_total:.2f} of its "
                            f"${budget_obj.per_story_cap_usd:.2f} cap")
            if over:
                sum_refusal = (f"The agent run would go over a cap -- {named}, est ${paid_total:.3f} in all: it "
                               f"would bring {_and(over)}. {caps_line} Nothing was generated or spent: raise that "
                               "cap in Settings, or choose free links.")
    candidates = [(row["number"], row["part"], row["refusal"]) for row in pending if row["refusal"]]
    if sum_refusal:
        # paid_rows can be empty while the cap is still over it -- the
        # premium writing cost alone (text_usd) is enough (plan 22 stage 1).
        # Anchored on the first part that writes on the premium chain, else
        # (defensively) the first pending part at all.
        anchor = paid_rows[0] if paid_rows else next(
            (row for row in pending if row["part"] in _PREMIUM_TEXT_PARTS), pending[0])
        candidates.append((anchor["number"], anchor["part"], sum_refusal))
    stops_at = None
    if candidates:
        number, part, reason = min(candidates, key=lambda item: item[0])
        stops_at = {"part": part, "number": number, "reason": " ".join(str(reason).split())}

    raw = sum(row["seconds"] for row in pending)
    seconds = float(min(raw, agent_step.STORY_BUDGET_CEILING_SECONDS))
    budget = {"seconds": seconds, "minutes": round(seconds / 60, 1),
              "ceiling_seconds": agent_step.STORY_BUDGET_CEILING_SECONDS,
              "basis": (f"{agent_step.PART_BUDGET_SECONDS // 60} min a part of pre-production still to do, the "
                        f"fast track's own budget for episode {agent_step.EPISODE}, never more than "
                        f"{agent_step.STORY_BUDGET_CEILING_SECONDS // 3600} h")}
    llm_calls = sum(row["units"]["llm_calls"] for row in pending)

    if stops_at is not None:
        message = stops_at["reason"]
    elif not pending:
        message = f"Nothing left to do: episode {agent_step.EPISODE} is rendered with its metadata pack."
    else:
        upto = "" if exact else "up to "
        money = f"paid: {named}" if paid_labels else "nothing paid"
        labels = _and(row["label"] for row in pending)
        # Named once, in the total (plan 22 stage 1): `named`/`money` above
        # already lists "writing" among the paid parts when it costs
        # something, so this is only the short form for the common case of
        # one line rather than a second sentence repeating it.
        incl = f" incl. ${text_usd['usd']:.2f} writing" if text_usd["usd"] > 0 else ""
        message = (f"{len(pending)} part{'' if len(pending) == 1 else 's'} to do ({labels}): "
                   f"{llm_calls} LLM calls on the free links first ($0 here), est {upto}${total:.2f}{incl} -- "
                   f"{money}; about {budget['minutes']:g} min. {caps_line}").strip()
        if text_usd["calls"] and not text_usd["usd"]:
            # Calls are still pending but nothing could be priced (no keyed
            # paid link, or allow_paid off): say why, since the total above
            # says nothing about them.
            message = f"{message} {text_usd['message']}"
    return {
        "step": AGENT_STEP, "mode": defaults.MODE_AGENT, "ep": agent_step.EPISODE, "parts": rows,
        "llm_calls": llm_calls, "est_usd": total, "exact": exact, "paid": [row["label"] for row in paid_rows],
        "caps": caps, "caps_line": caps_line, "budget": budget, "episode": ft, "text_usd": text_usd,
        "ready": stops_at is None, "stops_at": stops_at, "message": message,
    }
