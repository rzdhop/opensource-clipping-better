"""The story rules of steps 1-4, shared by the API and the CLI (spec 3, 9.1-9.3).

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
import os
import re

from clipping.providers import registry

from . import defaults, imaging, prompting, refimages, schemas, stylelock, templates, voices
from . import store as story_store
from .ledger import CostLedger
from .steps import concepts as concepts_step
from .steps import entities as entities_step
from .steps import llm_call
from .steps import regenerate as regenerate_step
from .steps import season as season_step

# ------------------------------------------------------------------ grammar

# Spec 9.1. What phase 1 runs, what phase 2 runs, what comes later.
LLM_STEPS = ("concepts", "bible")
INLINE_STEPS = ("style",)
PREVIEW_STEP = "style_preview"
PHASE1_STEPS = LLM_STEPS + INLINE_STEPS + (PREVIEW_STEP,)
# Steps 5-7 (phase 2): jobs, each calling the LLM chain (and, for the cast
# and the places, the image and voice chains). ``places_proposal`` is the
# small P0 step that proposes the list the ``places`` step makes.
PHASE2_STEPS = ("cast", "places_proposal", "places", "season")
LATER_STEPS = (
    "script", "storyboard", "assets", "render", "metadata", "memory", "feedback",
    "propose-next", "rerender", "fast-track", "import",
)

# Spec 9.2, approve grammar: "season" bare, the others "<kind>:<id>". Phase 2
# approves ``character:<id>``, ``place:<id>``, ``prop:<id>`` and ``season``.
LATER_APPROVALS_BARE = ()
LATER_APPROVALS = ("script", "storyboard", "assets")

# Spec 9.2, regenerate grammar: every "<kind>:..." target of a later phase.
# Phase 2's targets are ``regenerate.parse_target``'s; its
# ``character:<id>:image:extra:<n>`` is still a later phase's.
LATER_TARGETS = (
    "scene", "hook", "cliffhanger", "teaser", "shot", "line", "metadata",
)

# What approving the bible requires (spec 2.1, 3 step 3).
BIBLE_FIELDS = (
    "logline", "premise", "tone", "genre_tags", "world", "themes_and_values",
    "audience", "why_come_back",
)
WHY_COME_BACK_LINES = 3

# The story fields an edit may set (the API's StoryPatchRequest). Everything
# else is the rules' to write: approvals, status, the concept, the style.
PATCH_FIELDS = ("title", "seed_text") + BIBLE_FIELDS + ("narrator", "generation_profile")

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
    fixed ones and phase 2's entity shapes)."""
    return WorkflowError(
        INVALID,
        (f"Cannot regenerate {target!r}: the valid targets are "
         f"{', '.join(regenerate_step.TARGET_SHAPES)}."),
    )


def check_regenerate_target(target) -> None:
    """A target this phase regenerates passes -- phase 1's fixed ones and
    phase 2's entity targets (``regenerate.parse_target``: the shape only; the
    entity itself is checked by :func:`check_entity_target`). A later phase's
    target of the 9.2 grammar -- ``character:<id>:image:extra:<n>`` among
    them -- is ``later_phase``; anything else is ``invalid``, naming the
    valid shapes."""
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
    an unapproved one); ``title``, ``seed_text``, ``narrator`` and
    ``generation_profile`` leave the approvals alone. ``narrator`` and
    ``generation_profile`` are merged onto the current values, the profile
    checked against ``clipping.aistory.defaults`` (``invalid``). A story the
    schema would refuse is ``invalid`` with ``{"message", "errors"}``.
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


def edit_readiness(stories, story, *, env, qty):
    """``refimages.edit_readiness`` for *qty* reference images, calling
    nothing (a local link stays "probed when it runs"), with the story's
    ledger total against the per-story cap (``stories=``). A ledger that
    cannot be read makes it blocked, with that reason."""
    try:
        return refimages.edit_readiness(story, env=env, qty=qty, stories=stories)
    except refimages.RefImageError as exc:
        return imaging.blocked(refimages.READINESS_STEP, qty, [], str(exc))


def progress(stories, story, *, env) -> dict:
    """What each entity of the story still lacks, derived, calling nothing::

        {"characters": {char_id: {"missing": [...], "needs_editor": bool}},
         "places": {place_id: {"missing": [...]}},
         "props": {prop_id: {"missing": [...]}},
         "pick_voice": [char_id, ...],
         "edit_readiness": <refimages.edit_readiness> | null}

    ``missing`` is :func:`character_missing` / :func:`place_missing` /
    :func:`prop_missing`. ``edit_readiness`` is given once any sheet or time
    variant is missing (for that many images); ``needs_editor`` is true for a
    character in ``references`` mode that has its portrait, lacks a sheet,
    and whose sheets no editor can make now -- spec 8.1's "stop and ask".
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
    readiness = edit_readiness(stories, story, env=env, qty=sheets + variants) if sheets + variants else None
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
    checked before a job exists (``invalid``): only ``selected`` and
    ``custom``; each selected name one of the concept's cast sketch; each
    custom character ``{name, role, one_line, archetype?}`` with a role of
    ``schemas.CHARACTER_ROLES``; at most :data:`MAX_CAST` characters once the
    new ones join the story's; and at least one character in all."""
    _unknown_keys(params, CAST_PARAMS, "cast")
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
    """What one phase-2 regenerate target (``regenerate.parse_target``'s
    tuple) would make: text and ``season:<ep>`` one LLM call; a portrait one
    image, and again each sheet it already has (they are drawn from it); a
    sheet or a time variant one edit (``references``) or one image
    (``prompt_only``); a day plate or a prop image one image; a voice the
    characters of its sample line."""
    if parsed[0] == "season" or parsed[2] == "text":
        return _units(llm_calls=1)
    kind = ENTITY_KINDS_BY_WORD[parsed[0]]
    doc = read_entity(stories, story["story_id"], kind, parsed[1])
    if parsed[2] == "voice":
        return _units(tts_chars=_sample_chars(doc))
    prompt_only = story["generation_profile"]["consistency_mode"] == refimages.PROMPT_ONLY
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
    recorded, not failed, so a portrait never needs one)."""
    if parsed[0] == "season" or parsed[2] != "image":
        return False
    if story["generation_profile"]["consistency_mode"] == refimages.PROMPT_ONLY:
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
    """A phase-2 regenerate target (``regenerate.parse_target``'s tuple)
    checked against the story before a job exists; returns the voice to pin
    (``check_voice_choice``) or None.

    ``not_found``: no such character, place or prop, or no such arc entry.
    ``conflict``: no season arc yet; an image or voice of an entity not
    written yet; a sheet without its portrait, a time variant without its
    day plate. ``invalid``: a voice sent with any other target."""
    story_id = story["story_id"]
    is_voice = parsed[0] == "character" and parsed[2] == "voice"
    if voice is not None and not is_voice:
        raise WorkflowError(INVALID, "A voice is picked only with the target character:<char_id>:voice.")
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
    ``approved_at`` and ``approvals.season``.
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
    doc["approved_at"] = now
    write_doc(stories, story_id, SEASON_DOC, doc, now=now, validator=schemas.season_arc_errors)

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
    its folder, its id from the story; the group approvals re-fold); returns
    the store's ``{"removed", "kept"}``. ``not_found`` for an unknown one."""
    load(stories, story_id)
    try:
        return stories.delete_entity(story_id, kind, eid, now=now)
    except KeyError:
        raise WorkflowError(NOT_FOUND, f"This story has no {ENTITY_WORDS[kind]} {eid!r}.") from None
    except schemas.SchemaError as exc:
        raise StoryUnreadable(story_id, exc.name, exc.errors) from None
