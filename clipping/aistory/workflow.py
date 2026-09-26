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

from . import defaults, schemas, stylelock, templates
from . import store as story_store
from .ledger import CostLedger
from .steps import concepts as concepts_step
from .steps import llm_call
from .steps import regenerate as regenerate_step

# ------------------------------------------------------------------ grammar

# Spec 9.1. What phase 1 runs, what comes later.
LLM_STEPS = ("concepts", "bible")
INLINE_STEPS = ("style",)
PREVIEW_STEP = "style_preview"
PHASE1_STEPS = LLM_STEPS + INLINE_STEPS + (PREVIEW_STEP,)
LATER_STEPS = (
    "cast", "places", "season", "script", "storyboard", "assets", "render",
    "metadata", "memory", "feedback", "propose-next", "rerender", "fast-track",
    "import",
)

# Spec 9.2, approve grammar: "season" bare, the others "<kind>:<id>".
LATER_APPROVALS_BARE = ("season",)
LATER_APPROVALS = ("character", "place", "prop", "script", "storyboard", "assets")

# Spec 9.2, regenerate grammar: every "<kind>:..." target of a later phase.
LATER_TARGETS = (
    "character", "place", "prop", "season", "scene", "hook", "cliffhanger",
    "teaser", "shot", "line", "metadata",
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
    """The refusal of a step phase 1 does not run: ``later_phase`` for a step
    of the 9.1 grammar, ``not_found`` for anything else. Always raises."""
    if step in LATER_STEPS:
        raise WorkflowError(LATER_PHASE, f"'{step}' arrives in a later phase.")
    raise WorkflowError(NOT_FOUND, f"Unknown step {step!r}.")


def refuse_approval(doc):
    """The refusal of a document phase 1 does not approve: ``later_phase``
    for one of the 9.2 grammar, ``not_found`` for anything else. Always
    raises."""
    if is_later_approval(doc):
        raise WorkflowError(LATER_PHASE, f"Approving '{doc}' arrives in a later phase.")
    raise WorkflowError(NOT_FOUND, f"Nothing to approve under {doc!r}.")


def invalid_target(target) -> WorkflowError:
    return WorkflowError(
        INVALID,
        (f"Cannot regenerate {target!r}: the valid targets are "
         f"{', '.join(regenerate_step.VALID_TARGETS)}."),
    )


def check_regenerate_target(target) -> None:
    """A target phase 1 regenerates passes; a later phase's target of the 9.2
    grammar is ``later_phase``; anything else is ``invalid``, naming the
    valid ones."""
    if target in regenerate_step.VALID_TARGETS:
        return
    if is_later_target(target):
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
