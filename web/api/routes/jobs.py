"""
web.api.routes.jobs — Job management endpoints.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

from fastapi import Depends, APIRouter, File, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse

from ..auth import media_url, require_token
from .files import save_upload
from ..models import (
    JobCreateRequest,
    JobEvent,
    JobListResponse,
    JobResponse,
    JobStatus,
    ClipDetail,
)
from .. import store
from .. import worker

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[Depends(require_token)])


def _derive_missing_urls(clip: ClipDetail, job_id: str) -> ClipDetail:
    """Fill thumbnail_url / srt_url from the manifest blob when they are absent.

    Derived on read rather than migrated, so the jobs already in
    outputs/jobs.json work with zero writes. Every persisted clip carries its
    whole manifest entry in ``metadata``, which already contains
    ``thumbnail_path`` and ``srt_path`` -- verified against job 2773bd83c7b6,
    whose seven clips predate both fields.

    The alternative was a migration that rewrites outputs/jobs.json -- the single
    unbacked record of every job this system has ever run -- on process start, to
    add fields already recoverable from the file, and that would have to be
    written again for the next field. worker.py still populates both at write
    time so new records are self-describing, but the read path never depends on
    it.
    """
    meta = clip.metadata if isinstance(clip.metadata, dict) else {}
    updates = {}

    if not clip.thumbnail_url:
        name = os.path.basename(meta.get("thumbnail_path") or "")
        if name:
            updates["thumbnail_url"] = f"/api/outputs/{job_id}/{name}"

    if not clip.srt_url:
        name = os.path.basename(meta.get("srt_path") or "")
        if name:
            updates["srt_url"] = f"/api/outputs/{job_id}/{name}"

    return clip.model_copy(update=updates) if updates else clip


MEDIA_URL_FIELDS = ("download_url", "thumbnail_url", "srt_url")


def _sign_clip_urls(clip: ClipDetail, job_id: str) -> ClipDetail:
    """Replace the clip's media URLs with signed, expiring ones.

    Done at SERIALIZATION time, not at render time, for two reasons. The stored
    URLs in outputs/jobs.json must stay unsigned, or every persisted record would
    carry an expiry that outlives it; and holding a valid token is then exactly
    what mints a playable URL, because this is only reached through an
    authenticated response.

    The signature is stable inside its bucket (see auth.media_expiry), so two
    consecutive requests for the same job return byte-identical URLs. That is
    what keeps a re-render of the page from tearing down playback.
    """
    updates = {}
    for field in MEDIA_URL_FIELDS:
        url = getattr(clip, field, None)
        # A URL that already has a query string has already been signed. Cannot
        # happen through the store, which holds unsigned values, but signing a
        # signed URL would produce a broken one silently.
        if not url or "?" in url:
            continue
        name = url.rsplit("/", 1)[-1]
        if name:
            updates[field] = media_url(job_id, name)

    return clip.model_copy(update=updates) if updates else clip


def _clip_for_response(clip: ClipDetail, job_id: str) -> ClipDetail:
    """A clip as the browser needs it: missing URLs derived, all of them signed."""
    return _sign_clip_urls(_derive_missing_urls(clip, job_id), job_id)


def _job_to_response(job: dict) -> JobResponse:
    """Convert internal job dict to API response model."""
    clips = job.get("clips", [])
    job_id = job.get("id", "")
    clip_list = []
    for c in clips:
        if isinstance(c, ClipDetail):
            clip_list.append(_clip_for_response(c, job_id))
        elif isinstance(c, dict):
            clip_list.append(_clip_for_response(ClipDetail(**c), job_id))

    progress = job.get("progress")
    if progress and isinstance(progress, dict):
        from ..models import JobProgressEvent
        # Convert timestamp strings back to datetime
        if isinstance(progress.get("timestamp"), str):
            progress["timestamp"] = datetime.fromisoformat(progress["timestamp"])
        progress = JobProgressEvent(**progress)

    return JobResponse(
        id=job["id"],
        status=job.get("status", JobStatus.QUEUED),
        created_at=job.get("created_at", datetime.utcnow()),
        updated_at=job.get("updated_at", datetime.utcnow()),
        url=job.get("url"),
        transcript_filename=job.get("transcript_filename"),
        source_url=job.get("source_url"),
        upload_filename=job.get("upload_filename"),
        config=job.get("config", {}),
        progress=progress,
        clips=clip_list,
        error=job.get("error"),
        log=job.get("log", []),
        events=[JobEvent(**e) for e in job.get("events", []) if isinstance(e, dict)],
    )


# How many jobs may wait for a worker at once; MAX_QUEUED_JOBS, 0 for no limit.
DEFAULT_MAX_QUEUED_JOBS = 20


def _queue_refusal() -> str | None:
    """Why a job cannot join the queue right now, or None.

    Read per request, like the other runtime settings. Each waiting job holds
    a record the store re-serializes on every write and an output directory,
    so a client retrying in a loop could otherwise stack them without end.
    """
    raw = os.environ.get("MAX_QUEUED_JOBS", "").strip()
    try:
        limit = int(raw) if raw else DEFAULT_MAX_QUEUED_JOBS
    except ValueError:
        limit = DEFAULT_MAX_QUEUED_JOBS
    if limit <= 0:
        return None
    waiting = store.get_queued_count()
    if waiting < limit:
        return None
    return (
        f"The queue is full: {waiting} job(s) are already waiting "
        f"(MAX_QUEUED_JOBS={limit}). Try again once one has started, or cancel one."
    )


def _slow_chain_refusal(payload) -> str | None:
    """The chain-readiness refusal for a job about to be created, or None.

    Resolved from the same places the job's config will be -- the per-job chain,
    then Settings, then the process env -- without building that config.
    """
    from clipping.config import WEB_SLOW_CHAIN_HINT, chain_readiness
    from ..config_adapter import env_flag, resolve_provider_keys

    env = worker.get_settings_env()
    chain = payload.get("llm_chain") or env.get(
        "LLM_CHAIN", os.environ.get("LLM_CHAIN", ""))
    readiness = chain_readiness(
        chain,
        resolve_provider_keys(env),
        ai_provider=payload.get("ai_provider") or "chain",
        allow_slow=env_flag(env, "ALLOW_SLOW_CHAIN"),
        hint=WEB_SLOW_CHAIN_HINT,
    )
    return None if readiness.ready else readiness.message


@router.post("", status_code=201)
async def create_job(req: JobCreateRequest) -> JobResponse:
    """Create a new clipping job and submit it to the background queue."""
    # A job needs a source: an upload, a previous job to reuse, or a URL the
    # server can attempt to fetch. The URL is explicitly best-effort -- sites
    # refuse datacenter IPs often -- and a refusal parks the job in
    # `needs_upload` rather than failing it.
    if not req.upload_filename and not req.reuse_job_id and not req.source_url:
        raise HTTPException(
            status_code=400,
            detail=(
                "A job needs one of 'upload_filename', 'reuse_job_id' or "
                "'source_url'. Uploading the video (and optionally a "
                ".vtt/.srt transcript) is the reliable path; 'source_url' asks "
                "this server to try the download itself, which sites often "
                "refuse from a datacenter IP."
            ),
        )

    payload = req.model_dump()
    # Convert enums to string values for JSON serialization
    for key, value in payload.items():
        if hasattr(value, "value"):
            payload[key] = value.value

    reuse_job_id = payload.pop("reuse_job_id", None)

    # A rerun reuses the job's id and output directory. Two workers on one
    # directory would overwrite each other's files, and the old one's late
    # writes would land on the new record.
    if reuse_job_id and worker.is_active(reuse_job_id):
        raise HTTPException(
            status_code=409,
            detail="That job is still running. Cancel it, or wait for it to finish, then rerun it.",
        )

    # Reusing a job means rerunning it against the AI output it already has, so
    # default to loading that instead of paying for the analysis again.
    #
    # This must test model_fields_set, not the payload: load_gemini_json is a
    # declared field, so model_dump() always includes the key (as False), and the
    # old `"load_gemini_json" not in payload` check could never fire. Declaring
    # the field is what silently disabled this -- a rerun then demanded an API
    # key it did not need and failed with NVIDIA_API_KEY not found.
    # model_fields_set contains only what the client actually sent, so an
    # explicit `false` still wins.
    if reuse_job_id and "load_gemini_json" not in req.model_fields_set:
        payload["load_gemini_json"] = True

    # 429, not 503: "come back later" is the truth, and a 5xx reads as the
    # server being broken to a proxy's health logic.
    full = _queue_refusal()
    if full:
        raise HTTPException(status_code=429, detail=full)

    # Refuse, before a job exists, a chain that would run on the slow floor
    # alone (DEC-073). The worker checks again, but by then the job is on the
    # list, and the old failure took 93s of preflight to arrive. A render-only
    # rerun calls no provider, so it is exempt here as it is everywhere else --
    # the same definition the dashboard uses.
    render_only = bool(reuse_job_id and payload.get("load_gemini_json"))
    if not render_only:
        slow = _slow_chain_refusal(payload)
        if slow:
            raise HTTPException(status_code=400, detail=slow)

    job_id = store.create_job(
        transcript_filename=req.transcript_filename,
        source_url=req.source_url,
        upload_filename=req.upload_filename,
        config=payload,
        job_id=reuse_job_id
    )

    # Submit to background worker
    await worker.submit_job(job_id, payload)

    job = store.get_job(job_id)
    return _job_to_response(job)


@router.post("/{job_id}/source", status_code=200)
async def attach_source(
    job_id: str,
    file: UploadFile = File(...),
    subtitle: UploadFile | None = File(None),
) -> JobResponse:
    """Attach a video to a job waiting in ``needs_upload`` and resume it.

    The job keeps its id, its settings and its output directory, so anything it
    already produced -- notably a saved transcript (DEC-022) -- is still there.
    Creating a new job instead would throw all of that away to fix a download
    the user has already worked around.
    """
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    status = job.get("status")
    status = getattr(status, "value", status)
    if status != JobStatus.NEEDS_UPLOAD.value:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This job is '{status}', not '{JobStatus.NEEDS_UPLOAD.value}'. "
                f"Only a job waiting for its source can have one attached."
            ),
        )

    # Before the upload is stored: a refused request must not leave a file.
    full = _queue_refusal()
    if full:
        raise HTTPException(status_code=429, detail=full)

    video_name = await save_upload(file)
    subtitle_name = await save_upload(subtitle) if subtitle is not None else None

    config = dict(job.get("config") or {})
    config["upload_filename"] = video_name
    if subtitle_name:
        config["transcript_filename"] = subtitle_name

    # source_attached_at: when this job took its upload, which is what lets a
    # later delete tell this file from a newer upload that reused the name.
    store.update_job(job_id, config=config, upload_filename=video_name,
                     error=None, status=JobStatus.QUEUED,
                     source_attached_at=datetime.now(timezone.utc))
    await worker.submit_job(job_id, config)

    return _job_to_response(store.get_job(job_id))


@router.get("")
async def list_jobs() -> JobListResponse:
    """List all jobs (newest first)."""
    jobs = store.list_jobs()
    return JobListResponse(
        jobs=[_job_to_response(j) for j in jobs],
        total=len(jobs),
    )


@router.get("/{job_id}")
async def get_job(job_id: str) -> JobResponse:
    """Get job detail."""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_response(job)


@router.post("/{job_id}/cancel", status_code=202)
async def cancel_job(job_id: str) -> JobResponse:
    """Stop a queued or running job.

    It is marked cancelled at once, starts no further step, and its ffmpeg is
    killed now. A provider request or transcription chunk already in flight
    finishes or times out first (clipping/cancel.py). Its output directory is
    kept, so a saved transcript is still there for a rerun (DEC-022).
    """
    outcome = store.request_cancel(job_id)
    if outcome == "missing":
        raise HTTPException(status_code=404, detail="Job not found")
    if outcome == "terminal":
        status = store.get_job(job_id).get("status")
        raise HTTPException(
            status_code=409,
            detail=f"This job is already '{getattr(status, 'value', status)}'.",
        )
    worker.cancel(job_id)
    return _job_to_response(store.get_job(job_id))


@router.delete("/{job_id}")
async def delete_job(job_id: str, response: Response) -> dict:
    """Delete a job, its output directory and the uploads only it uses.

    A running job is cancelled first; its worker removes it once it stops, and
    this answers 202. Nothing here awaits, so the worker's own cleanup -- also
    on the event loop -- either sees the flag set below or has already gone.
    """
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    store.update_job(job_id, delete_requested=True)
    store.request_cancel(job_id)  # no-op for a job that already finished
    if worker.cancel(job_id):
        response.status_code = 202
        return {
            "message": "Cancelling. The job and its files are removed once it stops.",
            "id": job_id,
        }

    report = worker.remove_job(job_id)
    return {"message": "Job deleted", "id": job_id, **report}


@router.get("/{job_id}/status")
async def job_status_sse(job_id: str):
    """
    Server-Sent Events endpoint for real-time job progress.

    The client connects to this endpoint and receives progress updates
    as SSE events until the job completes or fails.
    """
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_stream():
        last_progress = None
        # Cursor into the activity feed. Starts at 0 and replays what the store
        # still holds: the client also loads the job over REST, and the two race,
        # so the client dedupes on `seq` rather than us guessing where it is.
        last_seq = 0
        # Nothing may be happening for minutes at a time (one AI call, one clip
        # rendering). Say so periodically, or an idle stream is indistinguishable
        # from a dead one -- to the user and to any proxy in between.
        ticks_since_output = 0
        HEARTBEAT_TICKS = 15
        terminal_states = {
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
        }

        def feed_frame():
            """Events recorded since the cursor, as one frame. None if there are none."""
            nonlocal last_seq
            new_events = store.get_events_since(job_id, last_seq)
            if not new_events:
                return None
            last_seq = new_events[-1].get("seq", last_seq)
            return json.dumps({"type": "events", "events": new_events}, default=str)

        while True:
            current_job = store.get_job(job_id)
            if current_job is None:
                yield f"data: {json.dumps({'type': 'deleted'})}\n\n"
                break

            status = current_job.get("status", "")
            progress = current_job.get("progress")

            # Build event data
            progress_data = None
            if progress:
                if hasattr(progress, "model_dump"):
                    progress_data = progress.model_dump()
                    if isinstance(progress_data.get("timestamp"), datetime):
                        progress_data["timestamp"] = progress_data["timestamp"].isoformat()
                elif isinstance(progress, dict):
                    progress_data = progress

            event = {
                "type": "progress",
                "status": status,
                "progress": progress_data,
                "error": current_job.get("error"),
            }

            sent = False

            # Only send if something changed
            event_json = json.dumps(event, default=str)
            if event_json != last_progress:
                yield f"data: {event_json}\n\n"
                last_progress = event_json
                sent = True

            frame = feed_frame()
            if frame is not None:
                yield f"data: {frame}\n\n"
                sent = True

            ticks_since_output = 0 if sent else ticks_since_output + 1
            if ticks_since_output >= HEARTBEAT_TICKS:
                ticks_since_output = 0
                heartbeat = {
                    "type": "heartbeat",
                    "status": status,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                yield f"data: {json.dumps(heartbeat)}\n\n"

            # Stop streaming on terminal states
            if status in terminal_states:
                # Send final event with clips if completed
                if status == JobStatus.COMPLETED.value:
                    clips = current_job.get("clips", [])
                    clip_data = []
                    # Decorated exactly like the HTTP response: derived URLs,
                    # then signed. The dashboard currently throws these away and
                    # re-fetches the job, so this is inert today -- but a
                    # serialization site that emits UNSIGNED media URLs is what
                    # the next consumer adopts by accident, and it is the same
                    # one-line call.
                    for c in clips:
                        if isinstance(c, ClipDetail):
                            clip_data.append(_clip_for_response(c, job_id).model_dump())
                        elif isinstance(c, dict):
                            clip_data.append(
                                _clip_for_response(ClipDetail(**c), job_id).model_dump()
                            )
                    final_event = {
                        "type": "completed",
                        "status": status,
                        "clips": clip_data,
                    }
                    yield f"data: {json.dumps(final_event, default=str)}\n\n"
                # The last lines the pipeline printed -- often the ones that say
                # WHY it failed -- are recorded after the status flips.
                tail = feed_frame()
                if tail is not None:
                    yield f"data: {tail}\n\n"
                break

            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
