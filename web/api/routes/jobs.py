"""
web.api.routes.jobs — Job management endpoints.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

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

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _job_to_response(job: dict) -> JobResponse:
    """Convert internal job dict to API response model."""
    clips = job.get("clips", [])
    clip_list = []
    for c in clips:
        if isinstance(c, ClipDetail):
            clip_list.append(c)
        elif isinstance(c, dict):
            clip_list.append(ClipDetail(**c))

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


@router.post("", status_code=201)
async def create_job(req: JobCreateRequest) -> JobResponse:
    """Create a new clipping job and submit it to the background queue."""
    # Local-first: a job needs a video on disk. `url` is no longer an input --
    # nothing downloads it.
    if not req.upload_filename and not req.reuse_job_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Either 'upload_filename' or 'reuse_job_id' must be provided. "
                "This pipeline does not download: upload the video (and "
                "optionally a .vtt transcript) first."
            ),
        )

    payload = req.model_dump()
    # Convert enums to string values for JSON serialization
    for key, value in payload.items():
        if hasattr(value, "value"):
            payload[key] = value.value

    reuse_job_id = payload.pop("reuse_job_id", None)

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


@router.delete("/{job_id}")
async def delete_job(job_id: str) -> dict:
    """Cancel/delete a job."""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # If running, mark as cancelled first
    running_states = {
        JobStatus.QUEUED.value,
        JobStatus.DOWNLOADING.value,
        JobStatus.TRANSCRIBING.value,
        JobStatus.ANALYZING.value,
        JobStatus.RENDERING.value,
    }
    if job.get("status") in running_states:
        store.set_status(job_id, JobStatus.CANCELLED)

    store.delete_job(job_id)
    return {"message": "Job deleted", "id": job_id}


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
        terminal_states = {
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
        }

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

            # Only send if something changed
            event_json = json.dumps(event, default=str)
            if event_json != last_progress:
                yield f"data: {event_json}\n\n"
                last_progress = event_json

            # Stop streaming on terminal states
            if status in terminal_states:
                # Send final event with clips if completed
                if status == JobStatus.COMPLETED.value:
                    clips = current_job.get("clips", [])
                    clip_data = []
                    for c in clips:
                        if hasattr(c, "model_dump"):
                            clip_data.append(c.model_dump())
                        elif isinstance(c, dict):
                            clip_data.append(c)
                    final_event = {
                        "type": "completed",
                        "status": status,
                        "clips": clip_data,
                    }
                    yield f"data: {json.dumps(final_event, default=str)}\n\n"
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
