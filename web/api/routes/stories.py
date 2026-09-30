"""
web.api.routes.stories — AI Story, steps 1-12 and the fast track (spec 3, 9.1,
9.2; phase-1 plan 2, phase-2 plan 2 "API", phase-3 plan 2 "API", phase-4 plan
2 "API").

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
  (``workflow.image_verdict``, the CLI's gate too: nothing called; 409 naming
  every link's reason).
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

- Phase 3 (steps 8-9): ``script`` and ``storyboard`` work on one episode
  (``ep``) and are step jobs of its documents (``script:<ep>``,
  ``storyboard:<ep>``) with the same key gate, queue cap and one step per
  story; the fast storyboard calls nothing and runs inside the request
  (DEC-109). The episode targets of ``regenerate`` are jobs of the document
  they rewrite. An episode's approvals live on its documents -- approving
  ``script:<ep>`` / ``storyboard:<ep>`` never changes the story's approvals
  or status (RC-E2) -- and complete that document's jobs. ``GET
  /{id}/episodes/{ep}`` is the episode page; its documents are edited inline
  (``PATCH``), never while a step of the story runs; a line's measured audio
  is served by ``GET /{id}/episodes/{ep}/voice/{name}`` behind the token
  (DEC-113).

- Phase 4 (steps 10-12 and the fast track): ``assets``, ``render``,
  ``metadata`` and ``fast-track`` are step jobs of one episode, their
  parameters and preconditions checked before a job exists (the step's own
  sentence), with one step per story and the queue cap; the LLM steps
  (``metadata``, ``fast-track``) meet the key gate, the assets the plan's own
  stop (no image link, no editor in ``references`` mode, a paid part over a
  cap), the render none (it calls nothing). The assets job awaits
  ``assets:<ep>``; the other three end completed (DEC-161). The regenerate
  grammar gains a shot's image ``shot:<ep>:<shid>`` and a line's voice
  ``line:<ep>:<lid>`` (jobs of ``assets:<ep>``) and one platform's metadata
  ``metadata:<ep>:<platform>`` (no document: it ends completed);
  ``shot:<ep>:<shid>:video`` stays a later phase's (DEC-140). Approving
  ``assets:<ep>`` completes the jobs awaiting it; a fast track that approved
  documents in-process has the worker complete the older jobs awaiting them
  (:func:`complete_approved_jobs`). A shot is locked inline (``PATCH
  /{id}/episodes/{ep}/assets``); its image is served by ``GET
  /{id}/episodes/{ep}/shots/{name}`` behind the token (DEC-113). The episode
  page gains the assets, the render, the metadata pack and the episode's
  ledger (``workflow.episode_outputs``). The final video and the cover are
  served by ``GET /{id}/episodes/{ep}/media/{name}``, behind the token too,
  which also opens -- for that one file -- with the signed URL the episode
  page mints for it (DEC-163: a ``<video src>`` and an ``<a download>`` cannot
  send the header).

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
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from clipping.aistory import schemas, templates, workflow
from clipping.aistory import store as story_store
from clipping.aistory import uploads as uploads_mod
from clipping.aistory.steps import bible as bible_step
from clipping.aistory.steps import concepts as concepts_step
from clipping.aistory.steps import entities as entities_step
from clipping.aistory.steps import llm_call
from clipping.aistory.steps import regenerate as regenerate_step
from clipping.aistory.steps import style_preview as preview_step
from clipping.providers import registry

from .. import store, worker
from ..auth import require_token, story_media_url
from ..models import (
    AssetsPatchRequest,
    AssetsStepParams,
    CharacterPatchRequest,
    ConceptChooseRequest,
    FastTrackStepParams,
    JobResponse,
    JobStatus,
    PlacePatchRequest,
    PropPatchRequest,
    RenderStepParams,
    ScriptPatchRequest,
    StoryApproveRequest,
    StoryboardPatchRequest,
    StoryCreateRequest,
    StoryEpisodeFeedbackRequest,
    StoryPatchRequest,
    StoryProposalDecisionRequest,
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

# Phase 3: what GET /{id}/episodes/{ep}/voice/{name} serves -- a line's
# measured take, named by its line (the store's pattern also admits the
# sidecar .json, which is not served) -- and the episode in its path.
_VOICE_NAME = re.compile(r"^line_[0-9]{2}\.(mp3|wav)$")
_EPISODE_IN_PATH = re.compile(r"^[1-9][0-9]?$")
# Phase 4: what GET /{id}/episodes/{ep}/shots/{name} serves -- a shot's image,
# named by its shot (the store's own pattern).
_SHOT_NAME = re.compile(schemas.SHOT_IMAGE_NAME_PATTERN)
# Phase 4: what GET /{id}/episodes/{ep}/media/{name} serves -- the episode's
# final video and its cover, exactly the files a story media signature may
# open (auth.STORY_MEDIA_NAMES, DEC-163) -- and the field each fills in the
# episode page's render.media.
_EPISODE_MEDIA_TYPES = {"episode_final.mp4": "video/mp4", "cover.jpg": "image/jpeg"}
_EPISODE_MEDIA_FIELDS = (("video_url", "episode_final.mp4"), ("cover_url", "cover.jpg"))

# Phase 4: the params each new step's job carries (only what was sent).
_STEP_PARAMS = {"assets": AssetsStepParams, "render": RenderStepParams, "fast-track": FastTrackStepParams}

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

def _job_doc(step, params, ep=None):
    """The story document a step job writes, and so the one whose approval
    completes it: ``bible``, ``concepts`` or ``style`` (the preview strip);
    ``cast``; ``places`` (the places step and its proposal); ``season``; for
    a phase-2 regenerate, its entity -- ``character:<id>``, ``place:<id>``,
    ``prop:<id>`` -- or ``season`` (``season:<ep>``). Phase 3: the job's
    episode document -- ``script:<ep>`` / ``storyboard:<ep>`` for the steps
    (*ep*, the job's), ``script:<ep>`` for ``scene``, ``hook``,
    ``cliffhanger`` and ``teaser`` targets, ``storyboard:<ep>`` for a
    ``shot:<ep>:<shid>:plan``. Phase 4: ``assets:<ep>`` for the assets step
    and for a shot's image (``shot:<ep>:<shid>``) or a line's voice
    (``line:<ep>:<lid>``); none for the render, the metadata, the fast track,
    a ``metadata:<ep>:<platform>`` and -- phase 5 -- the re-render (they end
    completed, DEC-161). Phase 5
    (``workflow.series_job_doc``): ``memory:<ep>``, ``feedback:<ep>`` and --
    the proposals sit in the folder of the episode they are for --
    ``proposals:<ep + 1>`` for ``propose-next``. None for anything else."""
    if step in workflow.EPISODE_APPROVALS:
        return f"{step}:{ep}" if type(ep) is int else None
    if step in workflow.SERIES_STEPS:
        return workflow.series_job_doc(step, ep)
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
        if parsed is not None and parsed[0] in workflow.EPISODE_TARGET_DOCS:
            return f"{workflow.EPISODE_TARGET_DOCS[parsed[0]]}:{parsed[1]}"
        if parsed is not None and parsed[0] in regenerate_step.EPISODE_KINDS:
            return None
        if parsed is not None:
            return "season" if parsed[0] == "season" else f"{parsed[0]}:{parsed[1]}"
    return None


def _doc_of(job) -> Optional[str]:
    """:func:`_job_doc` of a job record."""
    return _job_doc(job.get("step"), job.get("params"), ep=job.get("ep"))


def _episode_of(job) -> Optional[int]:
    """The episode a step job works on: an episode step's ``ep``; a series
    step's (phase 5), the episode of the document it writes
    (``workflow.series_job_doc``: ``propose-next`` of episode N writes N +
    1's proposals, so its job counts as N + 1's -- what ``propose-next``
    would leave busy is the episode its proposals block, not the one its
    memory was read from); an episode regenerate target's episode (phase 3's
    and phase 4's). None for anything else."""
    step = job.get("step")
    if step in workflow.EPISODE_STEPS:
        ep = job.get("ep")
        return ep if type(ep) is int else None
    if step in workflow.SERIES_STEPS:
        doc = workflow.series_job_doc(step, job.get("ep"))
        if doc is None:
            return None
        _word, _sep, rest = doc.partition(":")
        return int(rest) if rest.isdigit() else None
    if step == "regenerate":
        parsed = regenerate_step.parse_episode_target((job.get("params") or {}).get("target"))
        return parsed[1] if parsed is not None else None
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
        if busy and (doc is None or _doc_of(job) == doc):
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
        if _doc_of(job) == doc:
            if store.approve_step_job(job["id"]) == "ok":
                done.append(job["id"])
    return done


def complete_approved_jobs(story_id, ep) -> list:
    """Complete the step jobs of episode *ep* still awaiting the approval of
    a document that is approved now (``workflow.approved_episode_docs``: the
    script, the storyboard, the assets with a current fingerprint), as
    approving the document completes them; returns their ids.

    The fast track approves those documents in-process (DEC-162), so a job
    left awaiting one of them -- an older script job, a shot regenerate -- is
    settled once it ends: the worker calls this after a step that ends
    completed (``worker._execute_story_step``). Nothing awaiting, nothing
    read."""
    if type(ep) is not int:
        return []
    docs = {f"{name}:{ep}": name for name in workflow.EPISODE_APPROVALS}
    waiting = [job for job in store.list_step_jobs(story_id, statuses=[JobStatus.AWAITING_APPROVAL])
               if _doc_of(job) in docs]
    if not waiting:
        return []
    approved = set(workflow.approved_episode_docs(_stories(), story_id, ep))
    return [job["id"] for job in waiting
            if docs[_doc_of(job)] in approved and store.approve_step_job(job["id"]) == "ok"]


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

    doc = _job_doc(step, params, ep=ep)
    if doc is not None:
        for old in store.list_step_jobs(story_id, statuses=[JobStatus.AWAITING_APPROVAL]):
            if old["id"] != job_id and _doc_of(old) == doc:
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


def _refuse_episode_busy(story_id, ep, what_to_do) -> None:
    """409 while a step job of episode *ep* is queued or running: one of its
    documents' jobs, and (phase 4) its render, metadata, fast track or
    metadata regenerate, which read or write them too."""
    busy = [job for job in _in_flight(story_id) if _episode_of(job) == ep]
    if busy:
        raise HTTPException(status_code=409, detail=_busy_detail(busy[0], what_to_do))


# ------------------------------------------------------ phase-2 estimates

def _image_verdict(stories, story, qty, *, env) -> dict:
    """``IMAGE_CHAIN``'s verdict on *qty* reference images for *story*
    (``workflow.image_verdict``, the CLI's gate too). Nothing is called; a
    ledger that cannot be read is a 500 with one sentence."""
    with _answering():
        return workflow.image_verdict(stories, story, qty, env=env)


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


def _generation_estimate(stories, story, step, units, *, env, probe_local=False) -> dict:
    """What a step that makes images would cost and where it would run::

        {"step", "est_usd", "units": {"llm_calls", "images", "edit_images", "tts_chars"},
         "route_class": <IMAGE_CHAIN's>, "link", "links": <IMAGE_CHAIN's rows>,
         "edit": <workflow.edit_readiness for the edit_images>,
         "ready": bool, "message": str}

    ``est_usd`` = the images times the first runnable image link's price
    (0.0 on a free or local link) + the edits times the editor's, when it
    can run (spec 8.1: without one the step stops and asks before any edit).
    Not ``ready``: the LLM chain is refused (when a call is counted), or no
    image link can run (when an image is counted). Nothing is called, but a
    local editor's status probe with *probe_local* (the cast and places
    estimates: ``workflow.edit_readiness``).
    """
    images = _image_verdict(stories, story, units["images"], env=env)
    edit = workflow.edit_readiness(stories, story, env=env, qty=units["edit_images"], probe_local=probe_local)
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
                      "edit_readiness": <the editor's verdict> | null},
         "episodes": [{ep, title, script_state, storyboard_state, total_s, timing_state}, ...],
         "series": [workflow.series_page(...), ...]}

    ``progress`` is derived (``workflow.progress``) and calls nothing but a
    local editor's status probe: a character's ``missing`` is among ``text,
    portrait, turnaround, expressions, voice, sample``, a place's among
    ``text, day``, a prop's among ``text, image``; ``needs_editor`` is spec
    8.1's "stop and ask" (``references`` mode, the portrait there, a sheet
    missing, no editor able to run); ``edit_readiness`` is given while any
    sheet or time variant is missing. A local editor that would run is asked
    whether it is there (``probe_local``: ``GET /system_stats``, 2 s at
    most, remembered per server for a minute -- this page is polled while a
    step runs), so an unreachable ComfyUI shows here, before Continue.

    ``episodes`` (phase 3) summarises each episode folder
    (``workflow.episode_summaries``): its script's title and state (``none``,
    ``writing``, ``complete``, ``approved``), its storyboard's (``none``,
    ``partial``, ``complete``, ``approved``), its length and where it sits
    in the template's window; ``unreadable`` for both states when a document
    does not validate (its episode page says why).

    ``series`` (phase 5, step 13) is one ``workflow.series_page`` per
    episode the season plans (``[]`` before a season): the memory entry and
    its state, the audience feedback item, the proposals made for that
    episode and the gate's current refusal text for the episode after it --
    the episode page (``_episode_page``) shows one entry the same way, for
    the episode it is.
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
        episodes = workflow.episode_summaries(stories, story)
        series = [workflow.series_page(stories, story, ep)
                 for ep in range(1, (season["episodes_planned"] if season else 0) + 1)]
        # Off the event loop: the status probe may wait up to its timeout.
        progress = await run_in_threadpool(workflow.progress, stories, story, env=worker.get_settings_env(),
                                           probe_local=True)
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
        "episodes": episodes,
        "series": series,
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
    ``season`` (phase 2): 201 with the queued job (see ``_phase2_step``).
    ``script``, ``storyboard`` (phase 3, one episode: ``ep``): 201 with the
    queued job, or -- the storyboard with ``params.fast`` -- 200 with the
    episode page, built here (see ``_episode_step``). ``assets``, ``render``,
    ``metadata``, ``fast-track`` (phase 4, one episode: ``ep``): 201 with the
    queued job (see ``_phase4_step``). ``memory``, ``feedback``,
    ``propose-next`` (phase 5, step 13, one episode: ``ep``): 201 with the
    queued job (see ``_series_step``). A step of 9.1 still a later phase's
    (``workflow.LATER_STEPS``): 400. Anything else -- ``rerender`` too, until
    its route is wired (plan 11 stage 9) -- 404.
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
    if step in workflow.PHASE3_STEPS:
        return await _episode_step(stories, story, step, params, ep, response)
    if step in workflow.PHASE4_STEPS:
        return await _phase4_step(stories, story, step, params, ep)
    if step in workflow.SERIES_STEPS:
        return await _series_step(stories, story, step, params, ep)
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


async def _episode_step(stories, story, step, params, ep, response):
    """``script`` or ``storyboard`` of episode *ep*.

    Refused before any job exists, in this order: the episode's
    preconditions (``workflow.episode_context``: 409 for a story that is not
    ready, naming what to approve; 400 without ``ep`` or for one the season
    does not plan; 409 from episode 2 on while the series memory of the one
    before is not written, approved and fresh, naming which -- the gate,
    DEC-130 as amended by plan 11 stage 4), then the parameters (400: ``script``
    ``{measure_voices?}``, ``storyboard`` ``{fast?}``, closed lists), then --
    the storyboard -- a complete script (409 naming what is missing), then
    what every job meets (``_create_step_job``: 409 while a step of the story
    is in flight, the key gate 400, the queue cap 429). The job carries
    *ep*; a newer one supersedes the one awaiting approval for the same
    document (``script:<ep>`` / ``storyboard:<ep>``).

    ``storyboard`` with ``params.fast`` calls nothing (``shots.fast_plan``,
    DEC-109): it is built here, never while a step of the story is in flight
    (409), its notes go to the story's activity log, and the answer is the
    episode page (200) -- no job, no key gate, no queue.
    """
    story_id = story["story_id"]
    with _answering():
        ec = workflow.episode_context(stories, story, ep, step=step)
        if step == "script":
            workflow.script_request(params)
            fast = False
        else:
            fast = workflow.storyboard_request(params)
            workflow.require_complete_script(ec)
    if not fast:
        return await _create_step_job(story_id, step, params, ep=ep)

    _refuse_busy(story_id, "plan the shots once it is done, or cancel it first.")
    with _answering():
        workflow.build_fast_storyboard(stories, story, ep, now=_now(),
                                       on_log=lambda line: stories.append_activity(story_id, line))
    response.status_code = 200
    return _episode_page(stories, story, ep)


def _phase4_checks(stories, story, step, params, ep, env):
    """The checks of ``assets``, ``render``, ``metadata`` or ``fast-track``
    of episode *ep* before a job exists (see ``_phase4_step``); returns the
    job's params and its gate. Blocking (it hashes files), so it runs off the
    event loop."""
    with _answering():
        ec = workflow.episode_context(stories, story, ep, step=step)
        workflow.phase4_request(step, params)
        workflow.require_step_inputs(ec, step)
    model = _STEP_PARAMS.get(step)
    sent = _sent(model(**params)) if model is not None else {}
    if step == "assets":
        def gate():  # the plan's own stop, before the first call
            with _answering():
                workflow.assets_gate(ec, env=env)
    elif step == "render":
        def gate():  # it calls nothing: no gate
            return None
    else:
        gate = None  # an LLM step: the key gate
    return sent, gate


async def _phase4_step(stories, story, step, params, ep) -> JobResponse:
    """``assets``, ``render``, ``metadata`` or ``fast-track`` of episode *ep*
    (phase 4).

    Refused before any job exists, in this order: the episode's
    preconditions (``workflow.episode_context``, as the phase-3 steps: 409 for
    a story that is not ready, 400 without ``ep`` or for one the season does
    not plan; the memory gate for the fast track alone, while it would write
    the script or the storyboard), then the parameters
    (400, the step's closed list: ``assets`` ``{align_words?}``, ``render``
    ``{subtitles?, encoder?}``, ``fast-track`` ``{storyboard?}``, ``metadata``
    none), then what the step is made from (409 with the step's own sentence:
    ``workflow.require_step_inputs``), then what every job meets
    (``_create_step_job``: 409 while a step of the story is in flight; the
    step's gate -- the key gate for ``metadata`` and ``fast-track`` (400), the
    plan's own stop for ``assets`` (409: ``workflow.assets_gate``), none for
    the render, which calls nothing; the queue cap, 429). The job carries
    *ep* and the params sent; an assets job supersedes the one awaiting
    ``assets:<ep>``.
    """
    env = worker.get_settings_env()
    sent, gate = await run_in_threadpool(_phase4_checks, stories, story, step, params, ep, env)
    return await _create_step_job(story["story_id"], step, sent, ep=ep, gate=gate)


async def _series_step(stories, story, step, params, ep) -> JobResponse:
    """``memory``, ``feedback`` or ``propose-next`` of episode *ep* (phase 5,
    step 13).

    Refused before any job exists, in this order: the episode's
    preconditions and the step's own (``workflow.series_context``, calling
    nothing: 409 for a story that is not ready; 400 without ``ep`` or for one
    the season does not plan; ``memory`` 409 without episode *ep*'s script
    approved; ``feedback`` 409 without episode *ep*'s audience feedback
    pasted (``POST .../episodes/{ep}/feedback`` first); ``propose-next`` 409
    without an episode after *ep* in the season, or without episode *ep*'s
    own series memory written, approved and fresh -- DEC-130 as amended by
    plan 11 stage 4, the same gate a script or storyboard job meets), then
    the parameters (400: none of the three take any, ``workflow.
    series_request``), then what every job meets (``_create_step_job``): 409
    while a step of the story is queued or running -- the site's one-step rule
    is global, so it alone already keeps a series step of episode N and a
    script or storyboard job of episode N from overlapping -- the key gate
    (400, every series step calls one free LLM link), the queue cap (429).
    The job carries *ep*; a newer one supersedes the one awaiting approval
    for the same document (``memory:<ep>`` / ``feedback:<ep>`` /
    ``proposals:<ep + 1>``, ``workflow.series_job_doc``).
    """
    story_id = story["story_id"]
    with _answering():
        workflow.series_context(stories, story, ep, step=step)
        workflow.series_request(step, params)
    return await _create_step_job(story_id, step, params, ep=ep)


def _episode_jobs(story_id, ep) -> list:
    """The story's step jobs queued or running on episode *ep* (its
    documents', and its render, metadata and fast track), as ``GET /{id}``
    lists jobs."""
    return [
        jobs_routes._job_to_response(job).model_dump(
            mode="json", exclude={"events", "log", "clips", "config", "progress"})
        for job in _in_flight(story_id) if _episode_of(job) == ep
    ]


def _episode_media(story_id, ep, render) -> dict:
    """The episode's video and cover as the dashboard plays and shows them:
    ``{"video_url", "cover_url"}``, each the signed URL of ``GET
    /{id}/episodes/{ep}/media/{name}`` for that one file
    (``auth.story_media_url``, DEC-163), or None while the file is not there
    as a regular file (``StoryStore.episode_file_path``: a symlink is never
    offered, as it is never served). Minted here, per request, never stored;
    the expiry is bucketed, so the dashboard's poll gets the same URL byte for
    byte and the player never restarts."""
    stories = _stories()
    media = {}
    for field, name in _EPISODE_MEDIA_FIELDS:
        try:
            present = os.path.isfile(stories.episode_file_path(story_id, ep, name))
        except KeyError:
            present = False
        media[field] = story_media_url(story_id, ep, name) if present else None
    return media


def _episode_page(stories, story, ep) -> dict:
    """``GET /{id}/episodes/{ep}``'s answer: ``workflow.episode_view``,
    ``workflow.episode_outputs`` (phase 4: the assets, the render -- with its
    media --, the metadata pack, the ledger), ``workflow.series_page``
    (phase 5: the memory entry and its state, the audience feedback item, the
    proposals made for this episode, and the gate's current refusal text for
    the episode after it) and the episode's jobs in flight."""
    with _answering():
        page = workflow.episode_view(stories, story, ep)
        page.update(workflow.episode_outputs(stories, story, ep))
        page["series"] = workflow.series_page(stories, story, ep)
    if page["render"] is not None:
        page["render"]["media"] = _episode_media(story["story_id"], ep, page["render"])
    page["jobs"] = _episode_jobs(story["story_id"], ep)
    return page


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
async def approve(story_id: str, doc: str, req: Optional[StoryApproveRequest] = None) -> dict:
    """Approve one document of the story; answers the story (an episode
    document: the episode page).

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

    ``script:<ep>``, ``storyboard:<ep>`` (phase 3; body ``{approve_anyway?}``,
    the script's alone -- 400 with any other document): 400 for an episode
    number the season does not plan; 409 while a step of the episode's
    documents is queued or running; then ``workflow.approve_script`` (409
    until every scene and framing part is written and a consistency check of
    this revision passed -- or found issues and ``approve_anyway`` is sent,
    which the approval records) or ``workflow.approve_storyboard`` (409 until
    the script is approved, every scene has shots planned from its current
    version, and the prompts were resolved from the entities as they are);
    the document's ``approved_at`` is set -- never the story's approvals or
    status -- and its jobs awaiting approval are completed.

    ``assets:<ep>`` (phase 4): 400 for an episode number the season does not
    plan; 409 while a step job of the episode is queued or running (its
    documents', its render, metadata or fast track); then
    ``workflow.approve_assets`` (409 until the script is approved and the
    storyboard approved and current, and naming every shot with no current
    image -- a locked shot keeps the one it has -- and every line with no
    audio in its speaker's pinned voice, with the regenerate target that
    finishes it); every shot's ``approved`` is set and ``assets.json`` gains
    ``approved {at, fingerprint}``; the jobs awaiting ``assets:<ep>`` are
    completed. From phase 4 on, every episode approval waits for the
    episode's jobs, the render's and the fast track's included.

    ``memory:<ep>``, ``feedback:<ep>`` (body ``{direction?}``, required and
    only there -- 400 with any other document), ``proposals:<ep>`` (phase 5,
    step 13): 400 for an episode number the season does not plan; 409 while a
    step job of the episode is queued or running; then ``workflow.
    approve_series`` -- ``memory:<ep>`` (409 without an entry, or for a stale
    one), ``feedback:<ep>`` (409 without a digested item; ``direction`` 0, 1
    or 2 chooses one of F1's three, ``null`` chooses none -- either is stored
    as ``chosen_direction``; any other value is 400, exactly as
    ``workflow.approve_feedback`` -- a bool, a float or a text -- checks it)
    or ``proposals:<ep>`` (409 naming every item not yet accepted or
    rejected); nothing here ever moves the story's approvals or status
    (RC-M5); the jobs awaiting the document are completed.

    No approval of the 9.2 grammar is a later phase's any more. Anything
    else: 404. What each approval requires is ``workflow.approve_*``; the
    step jobs are checked here first.
    """
    stories = _stories()
    story = _load(stories, story_id)

    word, sep, eid = doc.partition(":")
    anyway = bool(req is not None and req.approve_anyway)
    if anyway and word != "script":
        raise HTTPException(status_code=400, detail="approve_anyway applies to script:<ep> only.")
    if sep and eid and word in workflow.EPISODE_APPROVALS:
        with _answering():
            ep = workflow.episode_bounds(stories, story, eid)
        _refuse_episode_busy(story_id, ep, f"approve {word}:{ep} once it is done, or cancel it first.")
        with _answering():
            if word == "script":
                workflow.approve_script(stories, story_id, ep, approve_anyway=anyway, now=_now())
            elif word == "storyboard":
                workflow.approve_storyboard(stories, story_id, ep, now=_now())
            else:
                workflow.approve_assets(stories, story_id, ep, now=_now())
        _complete_awaiting(story_id, f"{word}:{ep}")
        # Off the event loop: the page's phase-4 part hashes the files that moved.
        return await run_in_threadpool(_episode_page, stories, story, ep)

    if sep and eid and word in workflow.SERIES_APPROVALS:
        with _answering():
            ep = workflow.episode_bounds(stories, story, eid)
        _refuse_episode_busy(story_id, ep, f"approve {word}:{ep} once it is done, or cancel it first.")
        direction_kwargs = {}
        if req is not None and "direction" in req.model_fields_set:
            direction_kwargs["direction"] = req.direction
        with _answering():
            workflow.approve_series(stories, story_id, doc, now=_now(), **direction_kwargs)
        _complete_awaiting(story_id, doc)
        # Off the event loop: the page's phase-4 part hashes the files that moved.
        return await run_in_threadpool(_episode_page, stories, story, ep)

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

    Phase 3's targets (``scene:<ep>:<sid>``, ``hook:<ep>``,
    ``cliffhanger:<ep>``, ``teaser:<ep>``, ``shot:<ep>:<shid>:plan``) are step
    jobs ``regenerate`` of the episode's ``script:<ep>`` (``storyboard:<ep>``
    for a shot), checked first by ``workflow.check_episode_target``: the
    episode's preconditions but the recap (409 / 400), 409 without its
    script (or storyboard, or when the shot's scene was rewritten since it
    was planned), 404 for a scene or shot it does not have; then the key
    gate (400). ``voice`` is refused with them (400).

    Phase 4's targets: a shot's image ``shot:<ep>:<shid>`` and a line's voice
    ``line:<ep>:<lid>`` are jobs of ``assets:<ep>`` -- 404 for a shot or a line
    the episode does not have; 409 until the script is approved and the
    storyboard approved and current, for a locked shot ("unlock it first")
    and for a line whose speaker has no pinned voice; then the image's gate
    (``IMAGE_CHAIN``'s verdict, 409; the editor's in ``references`` mode,
    409) -- the voice meets none. One platform's metadata
    ``metadata:<ep>:<platform>`` (``tiktok``, ``shorts``, ``reels``) is a job
    of no document (it ends completed, DEC-161): 409 without a finished
    render or with a pack written for another render or script; then the key
    gate (400).

    A later phase's target of the 9.2 grammar
    (``character:<id>:image:extra:<n>``, ``shot:<ep>:<shid>:video`` among
    them): 400. Anything else: 400 naming the valid shapes.
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


def _estimate_message(rows, calls, refusal, *, label=None) -> str:
    if refusal:
        return refusal
    usable = [row for row in rows if row["keyed"] and "skipped" not in row]
    first = usable[0]
    calls_text = f"{label or calls} LLM call{'s' if label or calls != 1 else ''}"
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
                   selected: Optional[list[str]] = Query(None), episodes: Optional[int] = None,
                   place: Optional[list[str]] = Query(None), prop: Optional[list[str]] = Query(None),
                   ep: Optional[int] = None, measure: bool = False, align_words: bool = False,
                   subtitles: Optional[str] = None, encoder: Optional[str] = None,
                   storyboard: Optional[str] = None) -> dict:
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
    ``cast`` (``?selected=<sketch name>``, repeated) and ``places`` (
    ``?place=<name>``/``?prop=<name>``, each repeated -- the list the places
    step would receive; neither given falls back to the saved proposal, as
    the step itself does) answer ``_generation_estimate``: ``units
    {llm_calls, images, edit_images, tts_chars}`` counting only what is
    missing (a new character counts fully), ``est_usd`` = images x the first
    runnable image link's price + edits x the editor's, ``route_class`` and
    ``links`` of ``IMAGE_CHAIN``, ``edit`` the editor's verdict (with the
    story's ledger total against the cap; a local editor that would run the
    counted edits is asked whether it is there, as on the story page). A
    phase-2 ``?target=`` of ``regenerate``: a text or an arc entry as the LLM
    steps (1 call); an image or a voice as ``_generation_estimate``.

    Phase 3 (``?ep=``, the step's own refusals first: see ``_episode_step``):
    ``script`` answers the LLM steps' estimate of what is missing
    (``workflow.script_units``) with ``llm_calls`` (the calls E1's exact ask
    makes; the message names the range), ``llm_calls_range`` (the worst case
    a legal beat sheet can reach), ``calls_breakdown {E1, E2, E3, E4}`` (E2
    from the beat sheet once written, else the episode's own body slots),
    ``skipped_paid [{link, reason}]`` and ``measure`` -- with ``?measure=1``,
    what measuring the lines with the pinned voices would do
    (``script.measure_estimate``), else null. ``est_usd`` stays 0.0: LLM
    spend is not booked (DEC-115). ``storyboard``: ``t1_calls`` (one per
    scene with no plan, a stale one or a fast one), ``fast_calls`` 0,
    ``link``, ``skipped_paid``; not ``ready`` while the script is not
    complete.

    Phase 4 (``?ep=``, the step's own refusals first: see ``_phase4_step``;
    what it is made from missing is the step's 409): ``assets``
    (``?align_words=1``) answers ``workflow.assets_estimate`` --
    ``asset_units``' ``images`` (the shots to make, priced on the story's
    route; a local editor that would run them is asked whether it is
    there), ``voices``, ``alignment``, ``paid_links``, ``caps``, ``est_usd``,
    ``over_cap``, ``ready``, with ``paid`` (the fast track's verdict) and its
    ``message``. ``render`` (``?subtitles=``, ``?encoder=``): ``units
    {llm_calls: 0, shots}``, ``needed`` (false while the last render is the
    one it would make), ``seconds``/``minutes`` (an authored estimate, A-069),
    ``params``, $0 and ``local``. ``metadata``: the LLM steps' estimate of
    one M1 call per platform still to write, with ``platforms``.
    ``fast-track`` (``?storyboard=t1|fast``): ``fast_track.estimate``'s
    ``llm_calls``, ``images``, ``tts``, ``render``, ``est_usd``, ``paid`` and
    ``stops_at``, with the LLM chain's ``route_class``, ``link``, ``links``,
    ``ready`` (the key gate passes and nothing stops it) and ``message``.

    Phase 5 (``?ep=``, step 13, the step's own refusals first:
    ``workflow.series_context``, as ``_series_step``): ``memory``,
    ``feedback``, ``propose-next`` answer the LLM steps' estimate of one call
    (``workflow.series_units``: one S3, F1 or N1 call, on the free chain like
    every other step; ``allow_paid`` enforced the same way), with ``ep`` and
    ``skipped_paid``.

    A later step: 400; anything else: 404.
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
        return await run_in_threadpool(_generation_estimate, stories, story, step, units, env=env,
                                       probe_local=True)
    if step == "places":
        with _answering():
            params = None
            if place is not None or prop is not None:
                params = {"places": [{"name": name} for name in place or []],
                          "props": [{"name": name} for name in prop or []]}
            units = workflow.places_units(stories, story, params)
            listed = (workflow.places_proposal(stories, story_id) is not None
                      or workflow.list_entities(stories, story_id, PLACES)
                      or workflow.list_entities(stories, story_id, PROPS))
        body = await run_in_threadpool(_generation_estimate, stories, story, step, units, env=env,
                                       probe_local=True)
        if not listed:
            body.update(ready=False, message="Propose or list the places first.")
        return body
    if step == "season":
        with _answering():
            count = workflow.season_request({} if episodes is None else {"episodes": episodes})
        return _llm_estimate(step, 1 + count, env=env)
    if step == "places_proposal":
        return _llm_estimate(step, 1, env=env)
    if step in workflow.PHASE3_STEPS:
        return _episode_estimate(stories, story, step, ep, measure=measure, env=env)
    if step in workflow.PHASE4_STEPS:
        options = {"align_words": align_words, "subtitles": subtitles, "encoder": encoder, "storyboard": storyboard}
        return await run_in_threadpool(_phase4_estimate, stories, story, step, ep, env=env, **options)
    if step in workflow.SERIES_STEPS:
        return _series_estimate(stories, story, step, ep, env=env)
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


def _episode_estimate(stories, story, step, ep, *, measure, env) -> dict:
    """The ``script`` / ``storyboard`` estimate of episode *ep* (see
    ``estimate``), after the step's own refusals."""
    with _answering():
        ec = workflow.episode_context(stories, story, ep, step=step)
        if step == "script":
            units = workflow.script_units(ec)
            block = workflow.measure_estimate(ec, env=env) if measure else None
        else:
            units = workflow.storyboard_units(ec)
    if step == "script":
        low, high = units["llm_calls_range"]
        label = None if low == high else f"{low}–{high}"
        body = _llm_estimate(step, units["llm_calls"], env=env, label=label)
        body.update(llm_calls=units["llm_calls"], llm_calls_range=[low, high],
                    calls_breakdown={name: units[name] for name in ("E1", "E2", "E3", "E4")}, measure=block)
    else:
        body = _llm_estimate(step, units["t1_calls"], env=env)
        body.update(t1_calls=units["t1_calls"], fast_calls=0)
        if units["refusal"]:
            body.update(ready=False, message=units["refusal"])
    body.update(ep=ep, skipped_paid=[{"link": row["link"], "reason": row["skipped"]}
                                     for row in body["links"] if "skipped" in row])
    return body


def _phase4_estimate(stories, story, step, ep, *, env, align_words, subtitles, encoder, storyboard) -> dict:
    """The phase-4 estimate of episode *ep* (see ``estimate``), after the
    step's own refusals. Blocking (it hashes files; the assets' asks a local
    editor), so it runs off the event loop."""
    with _answering():
        ec = workflow.episode_context(stories, story, ep, step=step)
        if step == "assets":
            return workflow.assets_estimate(ec, env=env, align_words=align_words, probe_local=True)
        if step == "render":
            params = {name: value for name, value in (("subtitles", subtitles), ("encoder", encoder))
                      if value is not None}
            return workflow.render_estimate(ec, params)
        if step == "metadata":
            units = workflow.metadata_units(ec)
        else:
            body = workflow.fast_track_estimate(ec, env=env, storyboard=storyboard)
    if step == "metadata":
        body = _llm_estimate(step, units["llm_calls"], env=env)
        body.update(ep=ep, platforms=units["platforms"])
    else:
        llm = _llm_estimate(step, body["llm_calls"]["total"], env=env)
        stops = body["stops_at"]
        body.update(step=step, route_class=llm["route_class"], link=llm["link"], links=llm["links"],
                    ready=llm["ready"] and stops is None,
                    message=(llm["message"] if not llm["ready"] else stops["reason"] if stops
                             else f"{llm['message']} {body['paid']['message']}"))
    body["skipped_paid"] = [{"link": row["link"], "reason": row["skipped"]} for row in body["links"]
                            if "skipped" in row]
    return body


def _series_estimate(stories, story, step, ep, *, env) -> dict:
    """The ``memory`` / ``feedback`` / ``propose-next`` estimate of episode
    *ep* (see ``estimate``), after the step's own refusals
    (``workflow.series_context``, calling nothing): one LLM call, on the free
    chain like every other step's (``workflow.series_units``), ``allow_paid``
    enforced by ``_llm_estimate`` the same way."""
    with _answering():
        ec = workflow.series_context(stories, story, ep, step=step)
        units = workflow.series_units(ec, step)
    body = _llm_estimate(step, units["llm_calls"], env=env)
    body.update(ep=ep, skipped_paid=[{"link": row["link"], "reason": row["skipped"]}
                                     for row in body["links"] if "skipped" in row])
    return body


def _llm_estimate(step, calls, *, env, label=None) -> dict:
    """The LLM steps' estimate (see ``estimate``) of *calls* calls; *label*
    says how many in the message when that is a range."""
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
        "message": _estimate_message(rows, calls, refusal, label=label),
    }


# ------------------------------------------------------- episodes (phase 3)

def _sent(model) -> dict:
    """The fields sent in *model* (``model_fields_set``), the items of a list
    of models the same way, as plain values for the workflow."""
    sent = {}
    for name in model.model_fields_set:
        value = getattr(model, name)
        if isinstance(value, list):
            value = [_sent(item) if hasattr(item, "model_fields_set") else item for item in value]
        sent[name] = value
    return sent


def _episode_edit(story_id, ep, req, edit) -> dict:
    """An inline edit of one episode document (``workflow.patch_script`` /
    ``patch_storyboard`` / ``patch_assets`` say what each field does);
    answers the episode page. Blocking (the page hashes the files that
    moved), so the routes run it off the event loop. 404 for an unknown story; 400 for an episode the season does not
    plan; nothing sent: nothing written; 409 while a step of the story is
    queued or running (its writes would race this one); then the workflow's
    answer (409 without the document, 400 with ``{"message", "errors"}`` when
    the rules refuse it)."""
    stories = _stories()
    story = _load(stories, story_id)
    with _answering():
        number = workflow.episode_bounds(stories, story, ep)
    sent = _sent(req)
    if sent:
        _refuse_busy(story_id, "edit the episode once it is done, or cancel it first.")
        with _answering():
            edit(stories, story_id, number, sent, now=_now())
    return _episode_page(stories, story, number)


@router.get("/{story_id}/episodes/{ep}")
async def get_episode(story_id: str, ep: str) -> dict:
    """One episode's page (spec 9.2, phase 3)::

        {"ep", "script": script.json | null, "storyboard": storyboard.json | null,
         "template": {"id", "window_s", "target_s", "tighten_above_s"},
         "state": {"script": none|writing|complete|approved,
                   "storyboard": none|partial|complete|approved,
                   "report": none|passed|issues|stale,
                   "stale_scenes": [scene_id, ...], "prompts_outdated": bool,
                   "missing": [what the script step would still write]},
         "assets": {...} | null, "render": {..., "media": {"video_url", "cover_url"}} | null,
         "metadata": {"pack", "current"} | null, "ledger": {"entries", "totals"},
         "series": {"ep", "memory": {"state", "entry"}, "feedback", "proposals", "next_episode_gate"},
         "jobs": [the episode's step jobs queued or running]}

    (``workflow.episode_view``; phase 4's ``assets``, ``render``,
    ``metadata`` and ``ledger`` are ``workflow.episode_outputs``', which says
    what each holds; ``media`` a signed URL per file that exists:
    ``_episode_media``; ``series`` (phase 5, step 13) is ``workflow.
    series_page``: the memory entry and its state (``none``, ``draft``,
    ``approved``, ``stale``), the audience feedback item (its digest,
    directions and chosen one, once F1 has run), the proposals made *for*
    this episode with their decisions, and ``next_episode_gate`` -- the
    gate's current refusal text for the episode after this one, or null once
    it may be written ("why episode 2 is locked")). 404 for an unknown story;
    400 for an episode number the season does not plan
    (``1`` to ``episodes_planned``; 1 to 99 before a season); 200 with nulls
    before anything is written; 500 with one sentence for a document that
    does not validate. Calls nothing; what it hashes (images, audio, the
    video) is remembered while none of it moves, so the dashboard's poll
    hashes nothing again.
    """
    stories = _stories()
    story = _load(stories, story_id)
    with _answering():
        number = workflow.episode_bounds(stories, story, ep)
    # Off the event loop: the phase-4 part hashes files when they moved.
    return await run_in_threadpool(_episode_page, stories, story, number)


@router.post("/{story_id}/episodes/{ep}/feedback", status_code=201)
async def post_episode_feedback(story_id: str, ep: str, req: StoryEpisodeFeedbackRequest) -> JobResponse:
    """Paste episode *ep*'s audience feedback (``{text, stats?}``) and queue
    the ``feedback`` step; 201 with the queued job.

    ``text`` and ``stats`` are refused whole over 6,000 characters each --
    422 here (``StoryEpisodeFeedbackRequest``'s own cap), and again, for a
    caller that skips this model, by ``workflow.store_feedback`` (400) --
    never trimmed; an empty text is the same function's 400. 400 for an
    episode number the season does not plan. 409 while a step of the story
    is queued or running -- checked before the paste is written, so a
    refused request changes nothing (as ``PATCH .../script`` does): a new
    paste would race a running step that reads or writes this season
    document, the ``feedback`` step included. The paste replaces any earlier
    one of this episode -- its digest, directions and chosen direction with
    it (``workflow.store_feedback``: one item per episode) -- then the
    ``feedback`` step is queued exactly as ``POST /steps/feedback`` would
    (``_create_step_job``: the key gate 400, the queue cap 429; a newer
    feedback job supersedes one still awaiting approval for this episode).
    """
    stories = _stories()
    story = _load(stories, story_id)
    with _answering():
        number = workflow.episode_bounds(stories, story, ep)
    _refuse_busy(story_id, "paste it once that step is done, or cancel it first.")
    with _answering():
        workflow.store_feedback(stories, story_id, number, req.text, req.stats, now=_now())
    return await _create_step_job(story_id, "feedback", {}, ep=number)


@router.post("/{story_id}/episodes/{ep}/proposals/{item_id}")
async def post_proposal_decision(story_id: str, ep: str, item_id: str,
                                 req: StoryProposalDecisionRequest) -> dict:
    """Accept or reject one item of the proposals made *for* episode *ep*
    (``episodes/ep{ep}/proposals.json``, N1's, written by ``propose-next`` of
    the episode before); answers ``workflow.decide_proposal``'s payload --
    ``{ep, item_id, kind, decision, item[, role, folds_cast, cast, message]}``
    -- with ``job`` added once a character was accepted: the queued cast job,
    as ``POST /steps/cast`` answers one.

    ``accept`` is required and reaches the workflow exactly as sent, never
    coerced here, so a non-bool answers the same ``invalid`` (400) the CLI's
    own check would; a ``role`` sent with anything but accepting a character
    is the same 400 (``workflow.proposal_request``). 400 for an episode
    number the season does not plan; 404 for an unknown item; 409 while a
    step job of the episode is queued or running -- checked, like the
    feedback paste, before anything is decided --, without proposals, for an
    item already decided (a decision is final: run ``propose-next`` again for
    new proposals), or -- accepting only -- when the series memory the
    proposals were written from no longer stands, a twist's target episode
    left the arc, or a character's name is already the cast's (reject it
    instead). Accepting a character also meets the cast step's own gates
    before anything is decided (as ``POST /steps/cast`` would meet them for
    the same custom character): the key gate (400), ``IMAGE_CHAIN``'s verdict
    when a portrait or sheets would be made (409) -- then, once decided, the
    cast job is queued (``_create_step_job``: the same gate again, the queue
    cap 429; DEC-123 unchanged -- a lead or support character folds the cast
    approval once the cast step writes them, which ``message`` says).
    Rejecting, and accepting a twist, record the decision alone (twist:
    the target arc entry is amended, the old text kept in its ``history``;
    the acceptance is its own approval, so ``approvals.season`` is untouched)
    -- no job, no gate, no ``job`` key.
    """
    stories = _stories()
    story = _load(stories, story_id)
    env = worker.get_settings_env()
    with _answering():
        number = workflow.episode_bounds(stories, story, ep)
        preview = workflow.proposal_request(stories, story, number, item_id, accept=req.accept, role=req.role)
    gate = None
    if preview.get("cast") is not None:
        units = workflow.cast_units(stories, story, selected=(), custom=preview["cast"]["params"]["custom"])
        gate = _generation_gate(stories, story, units, env=env)
        gate()  # before anything is decided: an accept that cannot be fulfilled records nothing
    _refuse_busy(story_id, "decide it once that step is done, or cancel it first.")
    with _answering():
        result = workflow.decide_proposal(stories, story_id, number, item_id, accept=req.accept, role=req.role,
                                          now=_now())
    if result.get("cast") is not None:
        job = await _create_step_job(story_id, result["cast"]["step"], result["cast"]["params"], gate=gate)
        result = {**result, "job": job.model_dump(mode="json")}
    return result


@router.patch("/{story_id}/episodes/{ep}/script")
async def patch_episode_script(story_id: str, ep: str, req: ScriptPatchRequest) -> dict:
    """Edit an episode's script inline (``ScriptPatchRequest``:
    ``lines [{line_id, text?, speaker?, emotion?, delivery?}]``, ``scenes
    [{scene_id, summary?, on_screen_text?, pays_off?}]``, ``hook_on_screen_text``,
    ``cliffhanger_reveal``, ``next_episode_teaser``); see ``_episode_edit``
    and ``workflow.patch_script``: an edited line gets a fresh estimated
    timing, the script is re-timed, its report goes stale and it loses its
    approval; a text-only edit keeps the storyboard's (its re-timed scenes
    marked ``retime_only``), any other change clears it and stales the
    storyboard's scenes it changed."""
    return await run_in_threadpool(_episode_edit, story_id, ep, req, workflow.patch_script)


@router.patch("/{story_id}/episodes/{ep}/storyboard")
async def patch_episode_storyboard(story_id: str, ep: str, req: StoryboardPatchRequest) -> dict:
    """Edit an episode's storyboard inline (``StoryboardPatchRequest``:
    ``shots [{shot_id, framing?, camera_motion?, modifiers?, action?,
    keep_still?, prompt_override?}]``, ``transitions [{after, type}]``,
    ``refresh_prompts``); see ``_episode_edit`` and
    ``workflow.patch_storyboard``: an edited shot is resolved again (an
    action names nobody, tags only), a transition takes the template's
    duration and re-times the shots, the storyboard loses its approval and
    the script is re-timed with it."""
    return await run_in_threadpool(_episode_edit, story_id, ep, req, workflow.patch_storyboard)


@router.patch("/{story_id}/episodes/{ep}/assets")
async def patch_episode_assets(story_id: str, ep: str, req: AssetsPatchRequest) -> dict:
    """Lock or unlock shots inline (``AssetsPatchRequest``: ``shots
    [{shot_id, locked?}]``); see ``_episode_edit`` and
    ``workflow.patch_assets``: a locked shot keeps its image (the assets step
    skips it, a regenerate of it is 409 "unlock it first"), only a shot with
    an image may be locked, an unknown shot is a 400 naming it; the
    storyboard's revision and approval never move, and the assets approval
    goes stale (its fingerprint covers the locks)."""
    return await run_in_threadpool(_episode_edit, story_id, ep, req, workflow.patch_assets)


@router.get("/{story_id}/episodes/{ep}/shots/{name}")
async def episode_shot(story_id: str, ep: str, name: str):
    """One shot's image, ``shot_NN.<png|jpg|jpeg|webp>`` (the assets step's,
    named by its shot).

    The episode number and the name are checked before a path is built, and
    the file is served only as a regular file in the episode's real
    ``assets/shots/`` folder -- no symlink at any level
    (``StoryStore.episode_asset_path``). Anything else, another story's image
    included, is a 404. Behind the token like every story route (DEC-113:
    fetched as a blob); ``no-store``: a regenerated image reuses its name.
    """
    _check_id(story_id)
    if _EPISODE_IN_PATH.fullmatch(ep) is None or _SHOT_NAME.fullmatch(name) is None:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        path = _stories().episode_asset_path(story_id, int(ep), "shots", name)
    except KeyError:
        raise HTTPException(status_code=404, detail="File not found") from None
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path,
        media_type=_PREVIEW_MEDIA_TYPES[os.path.splitext(name)[1]],
        filename=name,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{story_id}/episodes/{ep}/voice/{name}")
async def episode_voice(story_id: str, ep: str, name: str):
    """One line's measured take, ``line_NN.mp3`` or ``line_NN.wav`` (the
    script step's voice measurement keeps it as phase 4's line audio).

    The episode number and the name are checked before a path is built, and
    the file is served only as a regular file in the episode's real
    ``assets/voice/`` folder -- no symlink at any level
    (``StoryStore.episode_asset_path``). Anything else, the sidecar ``.json``
    and another story's take included, is a 404. Behind the token like every
    story route (DEC-113: fetched as a blob); ``no-store``: a line measured
    again reuses its name.
    """
    _check_id(story_id)
    if _EPISODE_IN_PATH.fullmatch(ep) is None or _VOICE_NAME.fullmatch(name) is None:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        path = _stories().episode_asset_path(story_id, int(ep), "voice", name)
    except KeyError:
        raise HTTPException(status_code=404, detail="File not found") from None
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path,
        media_type=_ENTITY_MEDIA_TYPES[os.path.splitext(name)[1]],
        filename=name,
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{story_id}/episodes/{ep}/media/{name}")
async def episode_media(story_id: str, ep: str, name: str, download: bool = False):
    """The episode's final video or its cover: ``episode_final.mp4`` or
    ``cover.jpg``, nothing else.

    The episode number and the name are checked before a path is built, and
    the file is served only as a regular file directly in the episode's real
    folder -- no symlink at any level (``StoryStore.episode_file_path``).
    Anything else, another story's file included, is a 404. Behind the token
    like every story route; ``require_token`` also lets through, for this one
    file, the signed URL the episode page mints (DEC-163), since a ``<video
    src>`` and an ``<a download>`` cannot send the header.

    Range requests answer 206 (``FileResponse``); ``no-cache`` keeps the
    browser's copy but revalidates it by its ETag, because a render again
    reuses the name. Inline by default; ``?download=1`` makes it an
    attachment -- not part of the signature, it grants nothing (the clip
    route's rule, ``files.serve_output``).
    """
    _check_id(story_id)
    if _EPISODE_IN_PATH.fullmatch(ep) is None or name not in _EPISODE_MEDIA_TYPES:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        path = _stories().episode_file_path(story_id, int(ep), name)
    except KeyError:
        raise HTTPException(status_code=404, detail="File not found") from None
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path,
        media_type=_EPISODE_MEDIA_TYPES[name],
        filename=name,
        content_disposition_type="attachment" if download else "inline",
        headers={"Cache-Control": "no-cache"},
    )


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


@router.get("/{story_id}/characters/{char_id}/voices")
async def character_voices(story_id: str, char_id: str) -> dict:
    """The voice picker's data for one character (``workflow.character_voices``,
    spec 8.1, 11): its pinned voice, up to six alternates of the story's
    language ``TTS_CHAIN`` can reach right now, and the voices already pinned
    by the story's other leads/supports::

        {"pinned": {provider, voice_id} | null,
         "alternates": [{provider, voice_id, lang, gender, age, style_tags, link}],
         "taken": ["provider/voice_id", ...]}

    Calls nothing (``voices.alternates``: no network call). 404 for an
    unknown story or character.
    """
    stories = _stories()
    story = _load(stories, story_id)
    with _answering():
        return workflow.character_voices(stories, story, char_id, env=worker.get_settings_env())


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
