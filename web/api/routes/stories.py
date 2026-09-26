"""
web.api.routes.stories — AI Story, steps 1-7 (spec 3, 9.1, 9.2; phase-1 plan 2,
phase-2 plan 2 "API").

A story is a folder under ``outputs/stories/<story_id>/`` kept by
``clipping.aistory.store.StoryStore``; this module is the HTTP face of it.

- Steps that call an LLM (``concepts``, ``bible``, ``regenerate``) are jobs of
  kind ``story_step`` on the ordinary job store and worker: they meet the same
  key gate (DEC-073) and queue cap (429) as a clip job before the job exists,
  and end in ``awaiting_approval``. Unlike a clip job they never call a paid
  LLM link while ``allow_paid`` is off, so a chain whose only keyed links are
  paid is refused at the same gate (400). One step at a time per story: a
  second one while the first is queued or running is a 409, because both
  would write the same documents.
- Steps with no external call run inside the request (the human's answer 2):
  listing the library, choosing a concept, building and locking the style.
- Approval lives on the story (``approvals``; ``status`` is derived from it by
  the store). Approving a document also completes the step jobs that were
  waiting on it; a newer job for the same document supersedes an older one.
- The style preview strip (``style_preview``) is a step job too, of the style:
  it calls an image chain, not an LLM, so it meets no key gate; it is refused
  instead when no link of ``IMAGE_CHAIN`` can run for the story's route (the
  estimate's verdict), and approving the style completes it. Its images are
  served by ``GET /{id}/files/{name}`` behind the token (DEC-113: the
  dashboard fetches them with its header, as blobs).

- Phase 2 (steps 5-7): ``cast``, ``places_proposal``, ``places`` and
  ``season`` are step jobs too, with their parameters and preconditions
  checked before a job exists, the key gate, and -- for the cast and the
  places, when they would make an image -- ``IMAGE_CHAIN``'s verdict
  (``imaging.estimate``, nothing called; 409 naming every link's reason).
  Characters, places and props are approved one by one; the store folds
  those into ``approvals.cast`` / ``approvals.places``, and a group approval
  that is set completes the cast / places jobs awaiting it. The per-item
  regenerate targets of spec 9.2 are step jobs of their entity. A
  character's design references are uploaded here (streamed, capped, never
  held in memory; validated and re-encoded by ``clipping.aistory.uploads``
  in a worker thread). An entity is edited inline (``PATCH``) or deleted,
  never while a step of the story runs. Its images and voice sample are
  served by ``GET /{id}/media/{kind}/{eid}/{name}`` behind the token, like
  the preview images (DEC-113).

Every ``{story_id}`` is checked against the store's id rule before anything
else, so a malformed id is a 404 and never reaches a path; an unknown one is a
404; a story whose files do not validate is a 500 with one short sentence and
no traceback. Every route is under the router's token dependency, which runs
before any request body is read.

The story rules themselves -- choosing a concept, building the style draft,
what approving the bible or the style requires, which fields an edit may set
and which clear an approval, the phase grammar -- are
``clipping.aistory.workflow``, shared with the CLI (``main.py --ai-story``).
This module maps its errors onto status codes (``_answering``) and keeps what
needs the job store: one step at a time per story, the key gate and the queue
cap before a job exists, and the jobs an approval completes or a newer job
supersedes. Each route checks those where it always has, so the status codes,
details and their order are the ones the API has always given.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from clipping.aistory import imaging, refimages, schemas, templates, workflow
from clipping.aistory import store as story_store
from clipping.aistory import uploads as uploads_mod
from clipping.aistory.steps import bible as bible_step
from clipping.aistory.steps import concepts as concepts_step
from clipping.aistory.steps import entities as entities_step
from clipping.aistory.steps import llm_call
from clipping.aistory.steps import regenerate as regenerate_step
from clipping.aistory.steps import style_preview as preview_step
from clipping.providers import generation as gen
from clipping.providers import registry

from .. import store, worker
from ..auth import require_token
from ..models import (
    CharacterPatchRequest,
    ConceptChooseRequest,
    JobResponse,
    JobStatus,
    PlacePatchRequest,
    PropPatchRequest,
    StoryCreateRequest,
    StoryPatchRequest,
    StoryRegenerateRequest,
    StoryStepRequest,
)
from . import jobs as jobs_routes

router = APIRouter(prefix="/api/stories", tags=["stories"], dependencies=[Depends(require_token)])


# ------------------------------------------------------------------ grammar

# Spec 9.1 and 9.2 live in clipping.aistory.workflow with the rules; these two
# name the steps that are jobs here.
LLM_STEPS = workflow.LLM_STEPS
PREVIEW_STEP = workflow.PREVIEW_STEP

_IN_FLIGHT = (JobStatus.QUEUED.value, JobStatus.RUNNING.value)

# What GET /{id}/files/{name} answers a preview image with.
_PREVIEW_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

# Phase 2: the entity folders, and what GET /{id}/media/... answers a file with
# (the store decides which names exist: ``StoryStore.media_path``).
CHARACTERS, PLACES, PROPS = entities_step.CHARACTERS, entities_step.PLACES, entities_step.PROPS
MEDIA_KINDS = (CHARACTERS, PLACES, PROPS)
_ENTITY_MEDIA_TYPES = {**_PREVIEW_MEDIA_TYPES, ".mp3": "audio/mpeg", ".wav": "audio/wav"}

# A design reference arrives as multipart/form-data in this field. The whole
# request may carry the image (``uploads.MAX_UPLOAD_BYTES``) and this much
# more -- the boundaries and part headers -- before it is refused unread.
UPLOAD_FIELD = "file"
UPLOAD_OVERHEAD_BYTES = 64 * 1024
# The bytes all other fields of the form may hold together (none is read).
_UPLOAD_OTHER_FIELDS_BYTES = 4 * 1024


# ------------------------------------------------------------------ helpers

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stories():
    """Per request, so the outputs root is read when the request is served."""
    return story_store.StoryStore(worker.OUTPUTS_ROOT)


def _status_of(job: dict) -> str:
    status = job.get("status")
    return getattr(status, "value", status)


# What each workflow refusal answers.
_STATUS = {
    workflow.NOT_FOUND: 404,
    workflow.CONFLICT: 409,
    workflow.INVALID: 400,
    workflow.LATER_PHASE: 400,
}


@contextmanager
def _answering():
    """A workflow refusal as the HTTP error it has always been: its code's
    status (``_STATUS``) and its detail, as given; an unreadable story
    document is a 500 with one sentence and no traceback."""
    try:
        yield
    except workflow.WorkflowError as exc:
        raise HTTPException(status_code=_STATUS[exc.code], detail=exc.detail) from None
    except workflow.StoryUnreadable as exc:
        raise HTTPException(status_code=500, detail=exc.detail) from None


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Story not found")


def _check_id(story_id) -> None:
    with _answering():
        workflow.check_id(story_id)


def _load(stories, story_id) -> dict:
    """The story, or 404 (malformed or unknown id), or 500 (corrupt)."""
    with _answering():
        return workflow.load(stories, story_id)


def _read_doc(stories, story_id, name, validator):
    """One of the story's documents, validated, or None if it does not exist."""
    with _answering():
        return workflow.read_doc(stories, story_id, name, validator)


def _style_lock(stories, story_id):
    with _answering():
        return workflow.style_lock(stories, story_id)


def _generated_cards(stories, story_id) -> list:
    with _answering():
        return workflow.generated_cards(stories, story_id)


def _cost_total(stories, story_id) -> float:
    """The story ledger's total (spec 2.11), 0.0 while it has none."""
    with _answering():
        return workflow.cost_total(stories, story_id)


# ------------------------------------------------------------- step jobs

def _job_doc(step, params):
    """The story document a step job writes, and so the one whose approval
    completes it: ``bible``, ``concepts`` or ``style`` (the preview strip);
    ``cast``; ``places`` (the places step and its proposal); ``season``; for
    a phase-2 regenerate, its entity -- ``character:<id>``, ``place:<id>``,
    ``prop:<id>`` -- or ``season`` (``season:<ep>``). None for anything
    else."""
    if step in LLM_STEPS:
        return step
    if step == PREVIEW_STEP:
        return "style"
    if step == "cast":
        return "cast"
    if step in ("places_proposal", "places"):
        return "places"
    if step == "season":
        return "season"
    if step == "regenerate":
        target = (params or {}).get("target")
        if target == regenerate_step.CONCEPTS_TARGET:
            return "concepts"
        if isinstance(target, str) and target.startswith(regenerate_step.BIBLE_PREFIX):
            return "bible"
        parsed = regenerate_step.parse_target(target)
        if parsed is not None:
            return "season" if parsed[0] == "season" else f"{parsed[0]}:{parsed[1]}"
    return None


def _in_flight(story_id, *, doc=None) -> list:
    """The story's step jobs that are queued or running, oldest first.

    A job cancelled while it ran counts until its worker has stopped: it may
    still be finishing a request whose reply it writes (DEC-075).
    """
    found = []
    for job in store.list_step_jobs(story_id):
        status = _status_of(job)
        busy = status in _IN_FLIGHT or (
            status == JobStatus.CANCELLED.value and worker.is_active(job.get("id")))
        if busy and (doc is None or _job_doc(job.get("step"), job.get("params")) == doc):
            found.append(job)
    return found


def _busy_detail(job, what_to_do) -> str:
    return (
        f"Story step '{job.get('step')}' (job {job.get('id')}) is {_status_of(job)}: "
        f"{what_to_do}"
    )


def _complete_awaiting(story_id, doc) -> list:
    """Approve every step job of *story_id* awaiting approval for *doc*."""
    done = []
    for job in store.list_step_jobs(story_id, statuses=[JobStatus.AWAITING_APPROVAL]):
        if _job_doc(job.get("step"), job.get("params")) == doc:
            if store.approve_step_job(job["id"]) == "ok":
                done.append(job["id"])
    return done


def _llm_route(env):
    """``(links, keys, skipped, refusal)`` for a story step under the Settings
    values *env*: the chain and keys the step will run with (``llm_call``, the
    same resolution the worker hands it), the paid links it will leave out
    while ``allow_paid`` is off, and why it may not start, or None.

    Refused (``workflow.llm_route``): a chain that cannot be parsed (the step
    would fail on its first call), a chain in which no link has a key, budget
    settings that cannot be read, a chain whose only keyed links are paid
    while ``allow_paid`` is off, and the DEC-073 slow-floor case -- the rule
    ``POST /api/jobs`` applies, from the same function.
    """
    return workflow.llm_route(
        env, readiness=lambda links, _keys: jobs_routes._chain_readiness_refusal(links, env))


def _llm_gate(env):
    """``(links, keys, refusal)``: :func:`_llm_route` without the skipped links."""
    links, keys, _skipped, refusal = _llm_route(env)
    return links, keys, refusal


async def _create_step_job(story_id, step, params, *, ep=None, gate=None) -> JobResponse:
    """Queue one step of *story_id*; 201 with the job.

    Refused before any job exists, in this order: 409 while a step of this
    story is queued or running; the step's gate -- for an LLM step (no
    *gate*) the key gate, 400 (the status ``POST /api/jobs`` uses for the
    same refusal); *gate()* raises its own refusal otherwise -- then 429 when
    the queue is full. A job that awaits approval for the same document is
    superseded by this one.
    """
    busy = _in_flight(story_id)
    if busy:
        raise HTTPException(
            status_code=409,
            detail=_busy_detail(busy[0], "wait for it to finish, or cancel it first."),
        )

    if gate is not None:
        gate()
    else:
        _links, _keys, refusal = _llm_gate(worker.get_settings_env())
        if refusal:
            raise HTTPException(status_code=400, detail=refusal)

    full = jobs_routes._queue_refusal()
    if full:
        raise HTTPException(status_code=429, detail=full)

    try:
        job_id = store.create_job(
            kind=store.KIND_STORY_STEP, story_id=story_id, step=step, ep=ep, params=params,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    doc = _job_doc(step, params)
    if doc is not None:
        for old in store.list_step_jobs(story_id, statuses=[JobStatus.AWAITING_APPROVAL]):
            if old["id"] != job_id and _job_doc(old.get("step"), old.get("params")) == doc:
                store.supersede_step_job(old["id"], job_id)

    await worker.submit_job(job_id, {})
    return jobs_routes._job_to_response(store.get_job(job_id))


def _require_concept(story) -> None:
    with _answering():
        workflow.require_concept(story)


def _preview_estimate(stories, story) -> dict:
    """The preview strip's estimate for *story* under the live Settings: its
    route, and what its ledger already holds against the per-story cap."""
    story_id = story["story_id"]
    return preview_step.estimate(
        worker.get_settings_env(),
        route=story["generation_profile"]["route"],
        story_spent=_cost_total(stories, story_id),
    )


def _refuse_busy(story_id, what_to_do, *, docs=None) -> None:
    """409 while a step of the story (of one of *docs*, when given) is
    queued or running."""
    if docs is None:
        busy = _in_flight(story_id)
    else:
        busy = [job for doc in docs for job in _in_flight(story_id, doc=doc)]
    if busy:
        raise HTTPException(status_code=409, detail=_busy_detail(busy[0], what_to_do))


# ------------------------------------------------------ phase-2 estimates

def _image_verdict(stories, story, qty, *, env) -> dict:
    """``IMAGE_CHAIN``'s verdict on *qty* reference images for *story*
    (``imaging.estimate``: the story's route, keys, ``allow_paid`` and the
    caps with the story's ledger total, the free allowance; a local link is
    "probed when it runs"). Nothing is called."""
    width, height = refimages.PORTRAIT_SIZE
    return imaging.estimate(
        gen.IMAGE, env, route=story["generation_profile"]["route"],
        request=gen.GenRequest(kind=gen.IMAGE, width=width, height=height), qty=qty,
        story_spent=_cost_total(stories, story["story_id"]), step="image",
        what="a reference image", when="the step runs",
    )


def _plural(count, word) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _generation_message(units, images, edit, refusals) -> str:
    if refusals:
        return " ".join(refusals)
    parts = []
    if units["llm_calls"]:
        parts.append(f"{_plural(units['llm_calls'], 'LLM call')} (no LLM price table: not in est_usd).")
    if units["images"]:
        parts.append(images["message"])
    if units["edit_images"]:
        what = _plural(units["edit_images"], "reference image")
        if edit["ready"]:
            parts.append(f"Then {what} edited from the portraits: {edit['message']}")
        else:
            parts.append(f"The {what} need an editor or prompt-only consistency, so the step stops and asks "
                         f"before them: {edit['message']}")
    if units["tts_chars"]:
        parts.append(f"Voice samples: up to {units['tts_chars']} characters of speech, each on its pinned voice.")
    return " ".join(parts) or "Nothing is missing: nothing would be called."


def _generation_estimate(stories, story, step, units, *, env) -> dict:
    """What a step that makes images would cost and where it would run::

        {"step", "est_usd", "units": {"llm_calls", "images", "edit_images", "tts_chars"},
         "route_class": <IMAGE_CHAIN's>, "link", "links": <IMAGE_CHAIN's rows>,
         "edit": <workflow.edit_readiness for the edit_images>,
         "ready": bool, "message": str}

    ``est_usd`` = the images times the first runnable image link's price
    (0.0 on a free or local link) + the edits times the editor's, when it
    can run (spec 8.1: without one the step stops and asks before any edit).
    Not ``ready``: the LLM chain is refused (when a call is counted), or no
    image link can run (when an image is counted). Nothing is called.
    """
    images = _image_verdict(stories, story, units["images"], env=env)
    edit = workflow.edit_readiness(stories, story, env=env, qty=units["edit_images"])
    refusals = []
    if units["llm_calls"]:
        _links, _keys, refusal = _llm_gate(env)
        if refusal:
            refusals.append(refusal)
    if units["images"] and not images["ready"]:
        refusals.append(images["message"])
    est = 0.0
    if units["images"] and images["ready"]:
        est += images["est_usd"]
    if units["edit_images"] and edit["ready"]:
        est += edit["est_usd"]
    return {
        "step": step, "est_usd": round(est, 6), "units": dict(units),
        "route_class": images["route_class"], "link": images["link"], "links": images["links"],
        "edit": edit, "ready": not refusals, "message": _generation_message(units, images, edit, refusals),
    }


def _generation_gate(stories, story, units, *, env, llm=True, needs_editor=False):
    """The gate of a phase-2 job, before it exists (``_create_step_job``):
    the key gate when it calls the LLM (400, as phase 1), then
    ``IMAGE_CHAIN``'s verdict when it makes an image (409, every link's
    reason), then -- for a job that *is* an edit -- the editor's (409)."""

    def gate():
        if llm:
            _links, _keys, refusal = _llm_gate(env)
            if refusal:
                raise HTTPException(status_code=400, detail=refusal)
        if units["images"]:
            verdict = _image_verdict(stories, story, units["images"], env=env)
            if not verdict["ready"]:
                raise HTTPException(status_code=409, detail=verdict["message"])
        if needs_editor:
            edit = workflow.edit_readiness(stories, story, env=env, qty=max(units["edit_images"], 1))
            if not edit["ready"]:
                raise HTTPException(status_code=409, detail=(
                    f"{edit['message']} Start ComfyUI, or allow a paid editor, or switch the story to "
                    "prompt-only consistency."))

    return gate


# -------------------------------------------------------------- stories

@router.get("")
async def list_stories() -> dict:
    """``{"stories": [index entries]}``, most recently updated first. Each
    entry: ``story_id, title, language, style_template_id, status,
    created_at, updated_at``."""
    return {"stories": _stories().list()}


@router.post("", status_code=201)
async def create_story(req: StoryCreateRequest) -> dict:
    """Create a draft story; 201 with the whole ``story.json``.

    ``language`` is required (422 without it). An unknown
    ``style_template_id`` is a 400 naming the shipped ones.
    """
    profile = req.generation_profile.model_dump() if req.generation_profile is not None else None
    try:
        return _stories().create(
            language=req.language,
            seed_text=req.seed_text,
            style_template_id=req.style_template_id,
            generation_profile=profile,
            now=_now(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/styles")
async def list_styles() -> dict:
    """The seven shipped style templates (spec 5), for the style step's
    picker: ``{"styles": [{template_id, version, name, palette, typography,
    episode_defaults}, ...]}``, one entry per ``templates.list_style_ids()``.

    Declared *before* ``GET /{story_id}`` so ``/api/stories/styles`` is
    matched here and never read as a malformed story id (Starlette matches
    routes in declaration order, not by specificity).
    """
    styles = []
    for template_id in templates.list_style_ids():
        template = templates.load_style(template_id)
        styles.append({
            "template_id": template["template_id"],
            "version": template["version"],
            "name": template["name"],
            "palette": template["palette"],
            "typography": {
                "font_family": template["typography"]["font_family"],
                "font_fallback": template["typography"]["font_fallback"],
                "subtitle_mode": template["typography"]["subtitle_mode"],
                "highlight_colour": template["typography"]["highlight_colour"],
            },
            "episode_defaults": {
                "hook_style": template["episode_defaults"]["hook_style"],
                "cliffhanger_style": template["episode_defaults"]["cliffhanger_style"],
            },
        })
    return {"styles": styles}


@router.get("/{story_id}")
async def get_story(story_id: str) -> dict:
    """Everything the story page shows::

        {"story": story.json, "style_lock": style_lock.json | null,
         "style_preview": style_preview.json | null,
         "concepts_generated": <cards in concepts.json>,
         "jobs": [the story's step jobs as GET /api/jobs/{id} answers them,
                  minus events/log/clips/config/progress, oldest first],
         "cost_total_usd": <cost_ledger.json total, 0.0 without one>,
         "route": <generation_profile.route>,
         "characters": [character.json, ... in cast order],
         "places": [place.json, ...], "props": [prop.json, ...],
         "season": season.json | null, "places_proposal": places_proposal.json | null,
         "progress": {"characters": {char_id: {"missing": [...], "needs_editor": bool}},
                      "places": {place_id: {"missing": [...]}},
                      "props": {prop_id: {"missing": [...]}},
                      "pick_voice": [char_id, ...],
                      "edit_readiness": <the editor's verdict> | null}}

    ``progress`` is derived (``workflow.progress``) and calls nothing: a
    character's ``missing`` is among ``text, portrait, turnaround,
    expressions, voice, sample``, a place's among ``text, day``, a prop's
    among ``text, image``; ``needs_editor`` is spec 8.1's "stop and ask"
    (``references`` mode, the portrait there, a sheet missing, no editor
    able to run -- a local one counts as "probed when it runs");
    ``edit_readiness`` is given while any sheet or time variant is missing.
    """
    stories = _stories()
    story = _load(stories, story_id)
    lock = _style_lock(stories, story_id)
    cards = _generated_cards(stories, story_id)
    with _answering():
        characters = entities_step.cast_order(workflow.list_entities(stories, story_id, CHARACTERS))
        places = workflow.list_entities(stories, story_id, PLACES)
        props = workflow.list_entities(stories, story_id, PROPS)
        season = workflow.season(stories, story_id)
        proposal = workflow.places_proposal(stories, story_id)
        progress = workflow.progress(stories, story, env=worker.get_settings_env())
    # A step's feed can hold hundreds of events and has its own stream
    # (GET /api/jobs/{id}/status); this page is polled from a phone.
    step_jobs = [
        jobs_routes._job_to_response(job).model_dump(
            mode="json", exclude={"events", "log", "clips", "config", "progress"})
        for job in store.list_step_jobs(story_id)
    ]
    return {
        "story": story,
        "style_lock": lock,
        "style_preview": _read_doc(stories, story_id, preview_step.DOC_NAME, schemas.style_preview_errors),
        "concepts_generated": len(cards),
        "jobs": step_jobs,
        "cost_total_usd": _cost_total(stories, story_id),
        "route": story["generation_profile"]["route"],
        "characters": characters,
        "places": places,
        "props": props,
        "season": season,
        "places_proposal": proposal,
        "progress": progress,
    }


@router.patch("/{story_id}")
async def patch_story(story_id: str, req: StoryPatchRequest) -> dict:
    """Edit the fields sent (``model_fields_set``); answers the story.

    A bible field sent clears ``approvals.bible`` (a changed bible is an
    unapproved one); ``title``, ``seed_text``, ``narrator`` and
    ``generation_profile`` leave the approvals alone. ``narrator`` and
    ``generation_profile`` are merged onto the current values, the profile
    checked against ``clipping.aistory.defaults`` (``workflow.patch_story``).
    409 while a step of the story is queued or running (its writes would race
    this one); 400 with ``{"message", "errors"}`` when the story would not
    validate.
    """
    stories = _stories()
    story = _load(stories, story_id)
    sent = set(req.model_fields_set)
    if not sent:
        return story

    busy = _in_flight(story_id)
    if busy:
        raise HTTPException(
            status_code=409,
            detail=_busy_detail(busy[0], "edit the story once it is done, or cancel it first."),
        )

    with _answering():
        return workflow.patch_story(
            stories, story_id, {name: getattr(req, name) for name in sent}, now=_now())


@router.delete("/{story_id}")
async def delete_story(story_id: str) -> dict:
    """Delete the story: its folder, its index entry and its step jobs.

    409 while one of its steps is queued or running (cancel it first);
    nothing is removed then. The step jobs own no files, so they are simply
    dropped from the job store, before the folder goes. Answers
    ``{"message", "id", "removed", "kept", "jobs_removed"}``; ``removed`` and
    ``kept`` are the store's report (a symlink in the folder's place is kept,
    never followed).
    """
    _check_id(story_id)
    stories = _stories()
    # What StoryStore.delete deletes: a folder, or an index entry whose folder
    # is gone. A corrupt story is deletable; that is how one gets rid of it.
    has_folder = os.path.lexists(os.path.join(stories.root, story_id))
    if not has_folder and not any(e["story_id"] == story_id for e in stories.list()):
        raise _not_found()

    busy = _in_flight(story_id)
    if busy:
        raise HTTPException(
            status_code=409,
            detail=_busy_detail(busy[0], "cancel it first, then delete the story."),
        )

    jobs_removed = sum(1 for job in store.list_step_jobs(story_id) if store.delete_job(job["id"]))
    try:
        report = stories.delete(story_id)
    except KeyError:
        raise _not_found() from None
    return {"message": "Story deleted", "id": story_id, **report, "jobs_removed": jobs_removed}


# ------------------------------------------------------------- concepts

@router.get("/{story_id}/concepts")
async def list_concepts(story_id: str, language: Optional[str] = None,
                        style: Optional[str] = None) -> dict:
    """``{"library": [cards], "generated": [cards]}``.

    Library cards are the shipped concepts localized to ``language`` (default:
    the story's), each with its ``concept_id``, keeping only those whose
    default or alternative style is ``style`` when one is given. Generated
    cards are the story's ``concepts.json``, as written. 400 for a language or
    style that does not exist.
    """
    stories = _stories()
    story = _load(stories, story_id)

    lang = language or story["language"]
    if lang not in schemas.LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"language must be one of {', '.join(schemas.LANGUAGES)}, not {lang!r}.",
        )
    if style and style not in templates.list_style_ids():
        raise HTTPException(
            status_code=400,
            detail=f"Unknown style {style!r} (shipped: {', '.join(templates.list_style_ids())}).",
        )

    library = []
    for concept in templates.load_concepts():
        fit = concept["style_fit"]
        if style and style != fit["default"] and style not in fit["alternatives"]:
            continue
        card = templates.localize_concept(concept, lang)
        card["concept_id"] = concept["concept_id"]
        library.append(card)

    return {"library": library, "generated": _generated_cards(stories, story_id)}


@router.post("/{story_id}/concepts/generate", status_code=201)
async def generate_concepts(story_id: str) -> JobResponse:
    """"Generate 10 more": a ``concepts`` step job (as ``POST /steps/concepts``)."""
    _load(_stories(), story_id)
    return await _create_step_job(story_id, "concepts", {})


@router.post("/{story_id}/concepts/choose")
async def choose_concept(story_id: str, req: ConceptChooseRequest) -> dict:
    """Choose the story's concept; answers the story.

    Exactly one of ``concept_id`` (a library id, or a generated card's
    ``gen_NN``) and ``concept`` (a card written by the user, checked with the
    generated-card rules) -- 400 otherwise; 404 for an id that is neither.

    Writes a snapshot of the concept in the story's language, ``concept_id``
    (the library id, or ``"custom"``: the snapshot of a generated card keeps
    its ``gen_NN``), the concept's title, and ``approvals.concept``; a
    different concept than before clears the bible approval. The concepts
    jobs awaiting approval are completed: this choice is their approval.

    409 while the bible is being written (it is written from the concept).
    """
    stories = _stories()
    _load(stories, story_id)

    with _answering():
        workflow.check_concept_choice(req.concept_id, req.concept)

    busy = _in_flight(story_id, doc="bible")
    if busy:
        raise HTTPException(
            status_code=409,
            detail=_busy_detail(busy[0], "the bible is written from the current concept; "
                                         "choose once that step is done, or cancel it first."),
        )

    with _answering():
        story = workflow.choose_concept(
            stories, story_id, concept_id=req.concept_id, concept=req.concept, now=_now())
    _complete_awaiting(story_id, "concepts")
    return story


# ---------------------------------------------------------------- steps

@router.post("/{story_id}/steps/{step}", status_code=201)
async def run_step(story_id: str, step: str, response: Response,
                   req: Optional[StoryStepRequest] = None):
    """Run one step of spec 9.1 (body ``{ep?, params?}``).

    ``concepts``, ``bible``: 201 with the queued job (``bible`` needs a chosen
    concept: 409). ``style``: runs here, 200 with ``{"story", "style_lock"}``
    (see ``_style_step``). ``style_preview``: 201 with the queued job; 409
    without a ``style_lock.json`` ("Build the style first."), 400 with
    parameters (it takes none), 409 while a step of the story is in flight,
    409 when no image link can run for the story's route (the estimate's
    message, naming every link's reason), 429 when the queue is full -- no key
    gate: it calls no LLM. ``cast``, ``places_proposal``, ``places``,
    ``season`` (phase 2): 201 with the queued job (see ``_phase2_step``). Any
    other step of 9.1: 400, a later phase. Anything else: 404.
    """
    stories = _stories()
    story = _load(stories, story_id)
    params = dict(req.params) if req is not None and req.params is not None else {}
    ep = req.ep if req is not None else None

    if step == "concepts":
        return await _create_step_job(story_id, step, params, ep=ep)
    if step == "bible":
        _require_concept(story)
        return await _create_step_job(story_id, step, params, ep=ep)
    if step == "style":
        response.status_code = 200
        return _style_step(stories, story, params)
    if step == PREVIEW_STEP:
        with _answering():
            workflow.require_style_draft(stories, story_id)
        if params:
            raise HTTPException(status_code=400, detail=f"'{PREVIEW_STEP}' takes no parameters.")

        def preview_gate():
            verdict = _preview_estimate(stories, story)
            if not verdict["ready"]:
                raise HTTPException(status_code=409, detail=verdict["message"])

        return await _create_step_job(story_id, step, {}, ep=ep, gate=preview_gate)
    if step in workflow.PHASE2_STEPS:
        return await _phase2_step(stories, story, step, params, ep)
    with _answering():
        workflow.refuse_step(step)


async def _phase2_step(stories, story, step, params, ep) -> JobResponse:
    """Queue ``cast``, ``places_proposal``, ``places`` or ``season``.

    Refused before any job exists, in this order: the step's precondition
    (409: the style approved for the cast and the proposal, and a character
    for the proposal; a written character and a saved proposal or a list for
    the places; the cast approved for the season), then its parameters (400:
    ``cast`` ``{selected?, custom?}`` -- sketch names, custom characters with
    a role of the closed list, at most ``workflow.MAX_CAST`` in all;
    ``places`` ``{places?, props?}``, at most six each; ``season``
    ``{episodes?}``, 3 to 12; ``places_proposal`` none), then what every job
    meets (``_create_step_job``): 409 while a step of the story is in flight,
    the key gate (400) and, for the cast and the places when they would make
    an image, ``IMAGE_CHAIN``'s verdict (409, every link's reason), then the
    queue cap (429).
    """
    story_id = story["story_id"]
    env = worker.get_settings_env()
    units = None
    with _answering():
        if step == "cast":
            workflow.require_style_approved(story)
            selected, custom = workflow.cast_request(stories, story, params)
            units = workflow.cast_units(stories, story, selected=selected, custom=custom)
        elif step == "places_proposal":
            workflow.require_places_proposable(stories, story)
            if params:
                raise workflow.WorkflowError(workflow.INVALID, "'places_proposal' takes no parameters.")
        elif step == "places":
            workflow.require_places_ready(stories, story, params)
            workflow.places_request(stories, story, params)
            units = workflow.places_units(stories, story, params)
        else:
            workflow.require_cast_approved(story)
            workflow.season_request(params)
    no_images = {"images": 0, "edit_images": 0}
    gate = _generation_gate(stories, story, units or no_images, env=env)
    return await _create_step_job(story_id, step, params, ep=ep, gate=gate)


def _style_step(stories, story, params) -> dict:
    """The story's draft ``style_lock.json`` (spec 3 step 4), built or edited
    by ``workflow.build_style``, which says what ``params`` may hold and what
    it refuses.

    Here, in the order the route has always answered: 409 before the bible is
    approved, then 409 while the style's preview is queued or running (it is
    made from the draft this would change), then the workflow's answer.
    """
    story_id = story["story_id"]
    with _answering():
        workflow.require_bible_approved(story)
    busy = _in_flight(story_id, doc="style")
    if busy:
        raise HTTPException(
            status_code=409,
            detail=_busy_detail(busy[0], "change the style once its preview is done, or cancel it first."),
        )
    with _answering():
        return workflow.build_style(stories, story_id, params, now=_now())


# -------------------------------------------------------------- approve

@router.post("/{story_id}/approve/{doc}")
async def approve(story_id: str, doc: str) -> dict:
    """Approve one document of the story; answers the story.

    ``bible``: 409 while a bible step is queued or running, without a chosen
    concept, or listing every bible field still missing or empty (and
    ``why_come_back`` needs its three lines); then ``approvals.bible`` is set
    and the bible/regenerate jobs awaiting approval are completed.
    ``style``: 409 while its preview is queued or running, without a
    ``style_lock.json``, or when it is already locked; then the lock is frozen
    (``locked_at``), ``approvals.style`` set, and the preview jobs awaiting
    approval are completed.

    ``character:<id>``, ``place:<id>``, ``prop:<id>`` (phase 2): 404 for an
    unknown one; 409 while a step of its group (``cast``/``places``) or a
    regenerate of it is queued or running, or listing what it still lacks (a
    character: text, portrait, turnaround, expressions, a pinned voice, a
    voice sample; a place: text, day plate; a prop: text, image); then its
    ``approved_at`` is set, the store re-folds ``approvals.cast`` /
    ``approvals.places``, its own regenerate jobs awaiting approval are
    completed, and -- once the group approval is set -- the cast / places
    jobs awaiting it. ``season``: 409 while a season step is in flight,
    until the places are approved, or while the arc lacks an entry; then
    ``approvals.season`` is set (``ready``) and the season jobs awaiting
    approval are completed.

    The later documents of the 9.2 grammar: 400. Anything else: 404. What
    each approval requires is ``workflow.approve_*``; the step jobs are
    checked here first.
    """
    stories = _stories()
    _load(stories, story_id)

    word, sep, eid = doc.partition(":")
    if sep and eid and word in workflow.ENTITY_KINDS_BY_WORD:
        kind = workflow.ENTITY_KINDS_BY_WORD[word]
        group = workflow.GROUP_APPROVAL[kind]
        _entity(stories, story_id, kind, eid)
        _refuse_busy(story_id, f"approve {doc} once it is done, or cancel it first.", docs=(group, doc))
        with _answering():
            story = workflow.approve_entity(stories, story_id, kind, eid, now=_now())
        _complete_awaiting(story_id, doc)
        if story["approvals"].get(group):
            _complete_awaiting(story_id, group)
        return story

    if doc == "season":
        _refuse_busy(story_id, "approve the season once that step is done, or cancel it first.",
                     docs=("season",))
        with _answering():
            story = workflow.approve_season(stories, story_id, now=_now())
        _complete_awaiting(story_id, "season")
        return story

    if doc == "bible":
        busy = _in_flight(story_id, doc="bible")
        if busy:
            raise HTTPException(
                status_code=409,
                detail=_busy_detail(busy[0], "approve the bible once that step is done."),
            )
        with _answering():
            story = workflow.approve_bible(stories, story_id, now=_now())
        _complete_awaiting(story_id, "bible")
        return story

    if doc == "style":
        busy = _in_flight(story_id, doc="style")
        if busy:
            raise HTTPException(
                status_code=409,
                detail=_busy_detail(busy[0], "approve the style once its preview is done, or cancel it first."),
            )
        with _answering():
            story = workflow.approve_style(stories, story_id, now=_now())
        _complete_awaiting(story_id, "style")
        return story

    with _answering():
        workflow.refuse_approval(doc)


# ----------------------------------------------------------- regenerate

@router.post("/{story_id}/regenerate", status_code=201)
async def regenerate(story_id: str, req: StoryRegenerateRequest) -> JobResponse:
    """Regenerate one piece, with an optional note; 201 with the job.

    ``concepts`` (ten more) and ``bible:<field>`` (``field`` one of
    ``prompts.REGENERATE_TARGETS``; needs a chosen concept, 409) are step jobs
    ``regenerate`` with ``params {target, note}``.

    Phase 2's targets (``character:<id>:text|image:<portrait|turnaround|
    expressions>|voice``, ``place:<id>:text|image:<variant>``,
    ``prop:<id>:text|image``, ``season:<ep>``) are step jobs ``regenerate``
    with ``params {target, note, voice}`` -- a job of their entity, so a
    newer one supersedes it and approving the entity completes it. Refused
    first: 404 for an unknown entity or arc entry; 409 for an image or voice
    of an entity not written yet, a sheet without its portrait, a variant
    without its day plate, no arc; ``voice`` (``{provider, voice_id, rate?,
    pitch?}``) only with ``character:<id>:voice`` (400 otherwise), one of the
    story language's voices on ``TTS_CHAIN`` (400, naming them) that no
    other lead or support has (409). Then the gates of what it calls: the
    key gate for a text or an arc entry (400), ``IMAGE_CHAIN``'s verdict
    for an image made from text (409), the editor's for a sheet or a
    variant in ``references`` mode (409).

    A later phase's target of the 9.2 grammar
    (``character:<id>:image:extra:<n>`` among them): 400. Anything else: 400
    naming the valid shapes.
    """
    stories = _stories()
    story = _load(stories, story_id)
    target = req.target
    voice = req.voice if "voice" in req.model_fields_set else None

    with _answering():
        workflow.check_regenerate_target(target)
    parsed = regenerate_step.parse_target(target)
    if parsed is None:
        if voice is not None:
            raise HTTPException(status_code=400,
                                detail="A voice is picked only with the target character:<char_id>:voice.")
        if target.startswith(regenerate_step.BIBLE_PREFIX):
            _require_concept(story)
        return await _create_step_job(story_id, "regenerate", {"target": target, "note": req.note})

    env = worker.get_settings_env()
    with _answering():
        voice = workflow.check_entity_target(stories, story, parsed, voice=voice, env=env)
        units = workflow.target_units(stories, story, parsed)
        needs_editor = workflow.target_needs_editor(story, parsed)
    gate = _generation_gate(stories, story, units, env=env, llm=bool(units["llm_calls"]),
                            needs_editor=needs_editor)
    return await _create_step_job(story_id, "regenerate", {"target": target, "note": req.note, "voice": voice},
                                  gate=gate)


# ------------------------------------------------------------- estimate

def _llm_calls(step, target):
    if step == "concepts":
        return concepts_step.CALLS
    if step == "bible":
        return len(bible_step.PARTS)
    # regenerate: "concepts" is the concepts step itself; a bible field, one prompt.
    if target == regenerate_step.CONCEPTS_TARGET:
        return concepts_step.CALLS
    return 1


def _estimate_message(rows, calls, refusal) -> str:
    if refusal:
        return refusal
    usable = [row for row in rows if row["keyed"] and "skipped" not in row]
    first = usable[0]
    calls_text = f"{calls} LLM call{'s' if calls != 1 else ''}"
    note = "There is no LLM price table, so est_usd stays 0.0."
    if not first["free"]:
        return f"{calls_text} on {first['link']}, which is billed. {note}"
    paid_later = [row["link"] for row in usable[1:] if not row["free"]]
    text = f"{calls_text} on {first['link']} (free tier)."
    if paid_later:
        text += (f" If the free links before it fail, {', '.join(paid_later)} "
                 f"(billed) may be reached. {note}")
    skipped = list(dict.fromkeys(row["link"] for row in rows if row["keyed"] and "skipped" in row))
    if skipped:
        text += f" Not used: {', '.join(skipped)} (billed) -- {llm_call.PAID_SKIP_REASON}."
    return text


@router.get("/{story_id}/estimate/{step}")
async def estimate(story_id: str, step: str, target: Optional[str] = None,
                   selected: Optional[list[str]] = Query(None), episodes: Optional[int] = None) -> dict:
    """What a step would cost and where it would run::

        {"step", "est_usd": 0.0, "units": {"llm_calls": n},
         "route_class": "free" | "paid" | "blocked" | "local",
         "link": <first usable keyed link> | null,
         "links": [{"link", "keyed", "free"[, "skipped": <reason>]}, ...],
         "ready": <the key gate passes>, "message": str}

    ``concepts`` (10 calls, one concept each), ``bible`` (3), ``regenerate``
    (1; 10 with ``?target=concepts``) resolve the chain and keys as the step
    will. A paid link the step leaves out while ``allow_paid`` is off carries
    ``"skipped"`` with the reason (``llm_call.story_chain``); the first keyed
    link that is not skipped decides the class (``free`` when the link is
    free -- its provider's default model is, DEC-088, or it is an OpenRouter
    ``:free`` model). No such link is ``blocked`` with the key gate's
    message. ``style`` runs here: 0 calls, ``local``. ``style_preview``:
    ``units {"images": 3}``, the story's route applied, each link's gates as
    the step will meet them and nothing called (``style_preview.estimate``;
    its links are ``{"link", "status", "reason", "paid", "est_usd"}``).

    Phase 2: ``places_proposal`` (1 call) and ``season`` (1 + N calls,
    ``?episodes=N``, 3 to 12, default 8) answer like the LLM steps above.
    ``cast`` (``?selected=<sketch name>``, repeated) and ``places`` (the
    saved proposal) answer ``_generation_estimate``: ``units {llm_calls,
    images, edit_images, tts_chars}`` counting only what is missing (a new
    character counts fully), ``est_usd`` = images x the first runnable image
    link's price + edits x the editor's, ``route_class`` and ``links`` of
    ``IMAGE_CHAIN``, ``edit`` the editor's verdict (with the story's ledger
    total against the cap). A phase-2 ``?target=`` of ``regenerate``: a text
    or an arc entry as the LLM steps (1 call); an image or a voice as
    ``_generation_estimate``. A later step: 400; anything else: 404.
    """
    stories = _stories()
    story = _load(stories, story_id)
    env = worker.get_settings_env()

    if step == "style":
        return {
            "step": step, "est_usd": 0.0, "units": {"llm_calls": 0},
            "route_class": "local", "link": None, "links": [], "ready": True,
            "message": "The style lock is built on this server from the template; nothing is called.",
        }
    if step == PREVIEW_STEP:
        return _preview_estimate(stories, story)
    if step == "cast":
        with _answering():
            names = list(selected or [])
            workflow.check_sketch_names(story, names)
            units = workflow.cast_units(stories, story, selected=names)
        return _generation_estimate(stories, story, step, units, env=env)
    if step == "places":
        with _answering():
            units = workflow.places_units(stories, story)
            listed = (workflow.places_proposal(stories, story_id) is not None
                      or workflow.list_entities(stories, story_id, PLACES)
                      or workflow.list_entities(stories, story_id, PROPS))
        body = _generation_estimate(stories, story, step, units, env=env)
        if not listed:
            body.update(ready=False, message="Propose or list the places first.")
        return body
    if step == "season":
        with _answering():
            count = workflow.season_request({} if episodes is None else {"episodes": episodes})
        return _llm_estimate(step, 1 + count, env=env)
    if step == "places_proposal":
        return _llm_estimate(step, 1, env=env)
    if step not in LLM_STEPS and step != "regenerate":
        with _answering():
            workflow.refuse_step(step)
    if step == "regenerate" and target is not None and target not in regenerate_step.VALID_TARGETS:
        with _answering():
            workflow.check_regenerate_target(target)
            parsed = regenerate_step.parse_target(target)
            workflow.check_entity_target(stories, story, parsed)
            units = workflow.target_units(stories, story, parsed)
        if not units["llm_calls"]:
            return _generation_estimate(stories, story, step, units, env=env)

    return _llm_estimate(step, _llm_calls(step, target), env=env)


def _llm_estimate(step, calls, *, env) -> dict:
    """The LLM steps' estimate (see ``estimate``) of *calls* calls."""
    links, keys, skipped, refusal = _llm_route(env)
    reasons = {link: reason for link, reason in skipped}
    rows = []
    for link in links:
        row = {
            "link": registry.describe(link),
            "keyed": bool(keys.get(link.provider)),
            "free": llm_call.is_free_link(link),
        }
        if link in reasons:
            row["skipped"] = reasons[link]
        rows.append(row)
    first = next((row for row in rows if row["keyed"] and "skipped" not in row), None)
    if first is None:
        route_class = "blocked"
    else:
        route_class = "free" if first["free"] else "paid"
    return {
        "step": step,
        "est_usd": 0.0,
        "units": {"llm_calls": calls},
        "route_class": route_class,
        "link": first["link"] if first else None,
        "links": rows,
        "ready": refusal is None,
        "message": _estimate_message(rows, calls, refusal),
    }


# ---------------------------------------------------------------- files

@router.get("/{story_id}/files/{name}")
async def story_file(story_id: str, name: str):
    """One image of the style preview strip.

    Only ``preview_<1-9>.<png|jpg|jpeg|webp>``, only as a regular file directly
    inside the story's real ``styles/preview/`` folder -- no symlink at any
    level, the name checked before a path is built (``StoryStore.preview_file``).
    Anything else, another story's image included, is a 404. Behind the token
    like every story route: no signed URL (DEC-113), the dashboard fetches it
    with its header. ``no-store``: a new preview reuses the names.
    """
    _check_id(story_id)
    try:
        path = _stories().preview_file(story_id, name)
    except KeyError:
        raise HTTPException(status_code=404, detail="File not found") from None
    return FileResponse(
        path,
        media_type=_PREVIEW_MEDIA_TYPES[os.path.splitext(name)[1]],
        filename=name,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


# ------------------------------------------------------- entities (phase 2)

def _entity(stories, story_id, kind, eid) -> dict:
    with _answering():
        return workflow.read_entity(stories, story_id, kind, eid)


def _patch_entity(story_id, kind, eid, req) -> dict:
    """The fields sent (``model_fields_set``) into one entity; answers what
    was written (``workflow.patch_entity`` says what each field does). 404
    for an unknown entity; 409 while a step of the story is queued or
    running (its writes would race this one); 400 with ``{"message",
    "errors"}`` when the entity would not validate."""
    stories = _stories()
    _load(stories, story_id)
    current = _entity(stories, story_id, kind, eid)
    sent = set(req.model_fields_set)
    if not sent:
        return current
    _refuse_busy(story_id, "edit it once that step is done, or cancel it first.")
    with _answering():
        return workflow.patch_entity(stories, story_id, kind, eid, {name: getattr(req, name) for name in sent},
                                     now=_now())


def _delete_entity(story_id, kind, eid) -> dict:
    """Remove one entity: its folder, its id from the story; the group
    approval re-folds. 404 for an unknown one; 409 while a step of the story
    is queued or running. Answers ``{"message", "id", "removed", "kept"}``."""
    stories = _stories()
    _load(stories, story_id)
    _entity(stories, story_id, kind, eid)
    _refuse_busy(story_id, "delete it once that step is done, or cancel it first.")
    with _answering():
        report = workflow.delete_entity(stories, story_id, kind, eid, now=_now())
    return {"message": f"{workflow.ENTITY_WORDS[kind].capitalize()} deleted", "id": eid, **report}


@router.patch("/{story_id}/characters/{char_id}")
async def patch_character(story_id: str, char_id: str, req: CharacterPatchRequest) -> dict:
    """Edit a character inline (``CharacterPatchRequest``); see ``_patch_entity``."""
    return _patch_entity(story_id, CHARACTERS, char_id, req)


@router.patch("/{story_id}/places/{place_id}")
async def patch_place(story_id: str, place_id: str, req: PlacePatchRequest) -> dict:
    """Edit a place inline (``PlacePatchRequest``); see ``_patch_entity``."""
    return _patch_entity(story_id, PLACES, place_id, req)


@router.patch("/{story_id}/props/{prop_id}")
async def patch_prop(story_id: str, prop_id: str, req: PropPatchRequest) -> dict:
    """Edit a prop inline (``PropPatchRequest``); see ``_patch_entity``."""
    return _patch_entity(story_id, PROPS, prop_id, req)


@router.delete("/{story_id}/characters/{char_id}")
async def delete_character(story_id: str, char_id: str) -> dict:
    """Delete a character; see ``_delete_entity``."""
    return _delete_entity(story_id, CHARACTERS, char_id)


@router.delete("/{story_id}/places/{place_id}")
async def delete_place(story_id: str, place_id: str) -> dict:
    """Delete a place; see ``_delete_entity``."""
    return _delete_entity(story_id, PLACES, place_id)


@router.delete("/{story_id}/props/{prop_id}")
async def delete_prop(story_id: str, prop_id: str) -> dict:
    """Delete a prop; see ``_delete_entity``."""
    return _delete_entity(story_id, PROPS, prop_id)


# ---------------------------------------------------- design references

def _upload_refused(status, message, reasons=()) -> HTTPException:
    detail = {"message": message}
    if reasons:
        detail["errors"] = list(reasons)
    return HTTPException(status_code=status, detail=detail)


def _too_large() -> HTTPException:
    limit = uploads_mod.MAX_UPLOAD_BYTES
    return _upload_refused(uploads_mod.HTTP_STATUS["too_large"],
                           f"The image is larger than {limit / (1024 * 1024):g} MB.")


def _multipart():
    """``(MultipartParser, parse_options_header)`` of python-multipart (the
    parser Starlette's own form parsing uses; its module was renamed)."""
    try:
        from python_multipart.multipart import MultipartParser, parse_options_header
    except ImportError:  # python-multipart before 0.0.13
        from multipart.multipart import MultipartParser, parse_options_header
    return MultipartParser, parse_options_header


async def _receive_upload(request: Request, folder: str) -> str:
    """The form field ``file`` of a multipart request, streamed chunk by chunk
    into a hidden temp file in *folder* (the character's ``refs/uploads/``);
    returns its path. The body is never held in memory and never read past
    the cap: a ``Content-Length`` over ``uploads.MAX_UPLOAD_BYTES`` (+ the
    multipart envelope) is refused before a byte is read, and the stream is
    left the moment the file passes the cap. 413 then; 400 for a body that is
    not ``multipart/form-data`` with one file in ``file``. The temp file
    never outlives a refusal."""
    MultipartParser, parse_options_header = _multipart()
    limit = uploads_mod.MAX_UPLOAD_BYTES
    ask = f"Send the image as multipart/form-data, in a field named '{UPLOAD_FIELD}'."
    mime, options = parse_options_header(request.headers.get("content-type") or "")
    boundary = options.get(b"boundary")
    if mime.lower() != b"multipart/form-data" or not boundary:
        raise _upload_refused(400, ask)
    length = request.headers.get("content-length") or ""
    if length.isdigit() and int(length) > limit + UPLOAD_OVERHEAD_BYTES:
        raise _too_large()

    part = {"header": b"", "value": b"", "disposition": b"", "file": False}
    state = {"files": 0, "size": 0, "other": 0}
    pending = []

    def on_part_begin():
        part.update(header=b"", value=b"", disposition=b"", file=False)

    def on_header_field(data, start, end):
        part["header"] += data[start:end]

    def on_header_value(data, start, end):
        part["value"] += data[start:end]

    def on_header_end():
        if part["header"].lower() == b"content-disposition":
            part["disposition"] = part["value"]
        part["header"], part["value"] = b"", b""

    def on_headers_finished():
        _kind, params = parse_options_header(part["disposition"])
        part["file"] = params.get(b"name") == UPLOAD_FIELD.encode() and b"filename" in params
        if part["file"]:
            state["files"] += 1
            if state["files"] > 1:
                raise _upload_refused(400, f"Send one image at a time. {ask}")

    def on_part_data(data, start, end):
        if part["file"]:
            state["size"] += end - start
            if state["size"] > limit:
                raise _too_large()
            pending.append(bytes(data[start:end]))
        else:
            state["other"] += end - start
            if state["other"] > _UPLOAD_OTHER_FIELDS_BYTES:
                raise _upload_refused(400, f"The form carries more than the image. {ask}")

    def on_part_end():
        part["file"] = False

    callbacks = {
        "on_part_begin": on_part_begin, "on_header_field": on_header_field,
        "on_header_value": on_header_value, "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished, "on_part_data": on_part_data,
        "on_part_end": on_part_end,
    }
    parser = MultipartParser(boundary, callbacks)
    handle, tmp = tempfile.mkstemp(dir=folder, prefix=".upload-", suffix=".part")
    try:
        with os.fdopen(handle, "wb") as fh:
            received = 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > limit + UPLOAD_OVERHEAD_BYTES:
                    raise _too_large()
                try:
                    parser.write(chunk)
                except HTTPException:
                    raise
                except Exception:  # noqa: BLE001 - whatever the parser raised, the body is not a form
                    raise _upload_refused(400, f"The request body is not valid multipart/form-data. {ask}") from None
                if pending:
                    data = b"".join(pending)
                    pending.clear()
                    await run_in_threadpool(fh.write, data)
            try:
                parser.finalize()
            except Exception:  # noqa: BLE001
                raise _upload_refused(400, f"The request body is not valid multipart/form-data. {ask}") from None
        if not state["files"]:
            raise _upload_refused(400, f"No image was sent. {ask}")
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return tmp


@router.post("/{story_id}/characters/{char_id}/uploads", status_code=201)
async def upload_reference(story_id: str, char_id: str, request: Request) -> dict:
    """Add a design reference to a character (multipart, field ``file``);
    201 with its entry ``{name, description: null, uploaded_at}``.

    Refused before the body is read: 404 for an unknown story or character;
    409 while a step of the story is queued or running (it may be reading the
    references); 400 when the character already has ``uploads.MAX_UPLOADS``;
    409 when its ``refs/uploads/`` is not a real folder; 413 for a declared
    size over the cap. Then the body is streamed (``_receive_upload``: 413 the
    moment it passes the cap) and ``uploads.accept_upload`` decodes,
    re-encodes and stores it in a worker thread; its refusals answer their
    ``UploadError.http_status`` with ``{"message"}`` (415 for anything that
    is not a PNG, JPEG, WebP or GIF image). It is described (vision) the
    next time the character's text is written.
    """
    stories = _stories()
    _load(stories, story_id)
    character = _entity(stories, story_id, CHARACTERS, char_id)
    _refuse_busy(story_id, "add the image once that step is done, or cancel it first.")
    if len(character["refs"]["uploads"]) >= uploads_mod.MAX_UPLOADS:
        raise _upload_refused(uploads_mod.HTTP_STATUS["too_many"], (
            f"{character['name']} already has {uploads_mod.MAX_UPLOADS} design references; remove one first."))
    try:
        folder = stories.uploads_dir(story_id, CHARACTERS, char_id, create=True)
    except KeyError:
        raise _upload_refused(uploads_mod.HTTP_STATUS["storage"], (
            "The character's refs/uploads folder is not a real folder inside it (a symlink is never "
            "followed); remove it and upload again.")) from None

    received = await _receive_upload(request, folder)
    try:
        return await run_in_threadpool(uploads_mod.accept_upload, stories, story_id, char_id, received, now=_now())
    except uploads_mod.UploadError as exc:
        raise _upload_refused(exc.http_status, str(exc), exc.reasons) from None
    except KeyError:
        raise HTTPException(status_code=404, detail=f"This story has no character {char_id!r}.") from None
    finally:
        try:
            os.unlink(received)
        except OSError:
            pass


@router.delete("/{story_id}/characters/{char_id}/uploads/{name}")
async def delete_reference(story_id: str, char_id: str, name: str) -> dict:
    """Remove one design reference (``uploads.delete_upload``): its entry and
    its file. 404 for an unknown character or reference; 400 for a name that
    is not one (``<32 hex>.png``); 409 while a step of the story is queued or
    running, or when a symlink sits in its place (kept, never followed).
    Answers ``{"name", "entry_removed", "file_removed"}``."""
    stories = _stories()
    _load(stories, story_id)
    _entity(stories, story_id, CHARACTERS, char_id)
    _refuse_busy(story_id, "remove the image once that step is done, or cancel it first.")
    try:
        return uploads_mod.delete_upload(stories, story_id, char_id, name, now=_now())
    except uploads_mod.UploadError as exc:
        raise _upload_refused(exc.http_status, str(exc), exc.reasons) from None
    except KeyError:
        raise HTTPException(status_code=404, detail=f"This story has no character {char_id!r}.") from None


# ---------------------------------------------------------------- media

@router.get("/{story_id}/media/{kind}/{eid}/{name}")
async def entity_media(story_id: str, kind: str, eid: str, name: str):
    """One file of a character, place or prop: a reference image, a design
    reference, a voice sample.

    ``kind`` is ``characters``, ``places`` or ``props``; ``eid`` an id of that
    kind; ``name`` one the kind may hold (``StoryStore.media_path``:
    ``portrait|turnaround|expressions|extra_NN``, ``<32 hex>.png``,
    ``voice_sample.mp3|wav``; ``variant_<name>``; ``image``, each ``.png``,
    ``.jpg``, ``.jpeg`` or ``.webp`` for an image), each checked before a path
    is built, and only as a regular file in the entity's real folder -- no
    symlink at any level. Anything else, another story's file included, is
    a 404. Behind the token like every story route (DEC-113: fetched as a
    blob); ``no-store``: a regenerated image reuses its name.
    """
    _check_id(story_id)
    if kind not in MEDIA_KINDS:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        path = _stories().media_path(story_id, kind, eid, name)
    except KeyError:
        raise HTTPException(status_code=404, detail="File not found") from None
    return FileResponse(
        path,
        media_type=_ENTITY_MEDIA_TYPES[os.path.splitext(name)[1]],
        filename=name,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )
