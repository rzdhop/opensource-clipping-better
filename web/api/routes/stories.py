"""
web.api.routes.stories — AI Story, steps 1-4 (spec 3, 9.1, 9.2; phase-1 plan 2).

A story is a folder under ``outputs/stories/<story_id>/`` kept by
``clipping.aistory.store.StoryStore``; this module is the HTTP face of it.

- Steps that call an LLM (``concepts``, ``bible``, ``regenerate``) are jobs of
  kind ``story_step`` on the ordinary job store and worker: they meet the same
  key gate (DEC-073) and queue cap (429) as a clip job before the job exists,
  and end in ``awaiting_approval``. One step at a time per story: a second one
  while the first is queued or running is a 409, because both would write the
  same documents.
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

Every ``{story_id}`` is checked against the store's id rule before anything
else, so a malformed id is a 404 and never reaches a path; an unknown one is a
404; a story whose files do not validate is a 500 with one short sentence and
no traceback.

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
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse

from clipping.aistory import schemas, templates, workflow
from clipping.aistory import store as story_store
from clipping.aistory.steps import bible as bible_step
from clipping.aistory.steps import concepts as concepts_step
from clipping.aistory.steps import regenerate as regenerate_step
from clipping.aistory.steps import style_preview as preview_step
from clipping.providers import registry

from .. import store, worker
from ..auth import require_token
from ..models import (
    ConceptChooseRequest,
    JobResponse,
    JobStatus,
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
    None for anything else."""
    if step in LLM_STEPS:
        return step
    if step == PREVIEW_STEP:
        return "style"
    if step == "regenerate":
        target = (params or {}).get("target")
        if target == regenerate_step.CONCEPTS_TARGET:
            return "concepts"
        if isinstance(target, str) and target.startswith(regenerate_step.BIBLE_PREFIX):
            return "bible"
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


def _llm_gate(env):
    """``(links, keys, refusal)`` for a story step under the Settings values
    *env*: the chain and keys the step will run with (``llm_call``, the same
    resolution the worker hands it), and why it may not start, or None.

    Refused (``workflow.llm_gate``): a chain that cannot be parsed (the step
    would fail on its first call), a chain in which no link has a key, and the
    DEC-073 slow-floor case -- the rule ``POST /api/jobs`` applies, from the
    same function.
    """
    return workflow.llm_gate(
        env, readiness=lambda links, _keys: jobs_routes._chain_readiness_refusal(links, env))


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
         "route": <generation_profile.route>}
    """
    stories = _stories()
    story = _load(stories, story_id)
    lock = _style_lock(stories, story_id)
    cards = _generated_cards(stories, story_id)
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
    gate: it calls no LLM. Any other step of 9.1: 400, a later phase.
    Anything else: 404.
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
    with _answering():
        workflow.refuse_step(step)


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
    approval are completed. The later documents of the 9.2 grammar: 400.
    Anything else: 404. What each approval requires is
    ``workflow.approve_bible`` / ``approve_style``; the step jobs are checked
    here first.
    """
    stories = _stories()
    _load(stories, story_id)

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
    ``regenerate`` with ``params {target, note}``. A later phase's target of
    the 9.2 grammar: 400. Anything else: 400 naming the valid targets.
    """
    stories = _stories()
    story = _load(stories, story_id)
    target = req.target

    with _answering():
        workflow.check_regenerate_target(target)
    if target.startswith(regenerate_step.BIBLE_PREFIX):
        _require_concept(story)

    return await _create_step_job(story_id, "regenerate", {"target": target, "note": req.note})


# ------------------------------------------------------------- estimate

def _llm_calls(step, target):
    if step == "concepts":
        return concepts_step.BATCHES
    if step == "bible":
        return len(bible_step.PARTS)
    # regenerate: "concepts" is the concepts step itself; a bible field, one prompt.
    if target == regenerate_step.CONCEPTS_TARGET:
        return concepts_step.BATCHES
    return 1


def _estimate_message(rows, calls, refusal) -> str:
    if refusal:
        return refusal
    keyed = [row for row in rows if row["keyed"]]
    first = keyed[0]
    calls_text = f"{calls} LLM call{'s' if calls != 1 else ''}"
    note = "There is no LLM price table, so est_usd stays 0.0."
    if not first["free"]:
        return f"{calls_text} on {first['link']}, which is billed. {note}"
    paid_later = [row["link"] for row in keyed[1:] if not row["free"]]
    text = f"{calls_text} on {first['link']} (free tier)."
    if paid_later:
        text += (f" If the free links before it fail, {', '.join(paid_later)} "
                 f"(billed) may be reached. {note}")
    return text


@router.get("/{story_id}/estimate/{step}")
async def estimate(story_id: str, step: str, target: Optional[str] = None) -> dict:
    """What a step would cost and where it would run::

        {"step", "est_usd": 0.0, "units": {"llm_calls": n},
         "route_class": "free" | "paid" | "blocked" | "local",
         "link": <first keyed link> | null,
         "links": [{"link", "keyed", "free"}, ...],
         "ready": <the key gate passes>, "message": str}

    ``concepts`` (5 calls), ``bible`` (3), ``regenerate`` (1; 5 with
    ``?target=concepts``) resolve the chain and keys as the step will; the
    first keyed link decides the class (``free`` when its provider's default
    model is free, DEC-088). No keyed link is ``blocked`` with the key gate's
    message. ``style`` runs here: 0 calls, ``local``. ``style_preview``:
    ``units {"images": 3}``, the story's route applied, each link's gates as
    the step will meet them and nothing called (``style_preview.estimate``;
    its links are ``{"link", "status", "reason", "paid", "est_usd"}``). A
    later step: 400; anything else: 404.
    """
    stories = _stories()
    story = _load(stories, story_id)

    if step == "style":
        return {
            "step": step, "est_usd": 0.0, "units": {"llm_calls": 0},
            "route_class": "local", "link": None, "links": [], "ready": True,
            "message": "The style lock is built on this server from the template; nothing is called.",
        }
    if step == PREVIEW_STEP:
        return _preview_estimate(stories, story)
    if step not in LLM_STEPS and step != "regenerate":
        with _answering():
            workflow.refuse_step(step)
    if step == "regenerate" and target is not None and target not in regenerate_step.VALID_TARGETS:
        with _answering():
            raise workflow.invalid_target(target)

    calls = _llm_calls(step, target)
    links, keys, refusal = _llm_gate(worker.get_settings_env())
    rows = [
        {
            "link": registry.describe(link),
            "keyed": bool(keys.get(link.provider)),
            "free": bool(registry.PROVIDERS[link.provider].free_tier),
        }
        for link in links
    ]
    first = next((row for row in rows if row["keyed"]), None)
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
