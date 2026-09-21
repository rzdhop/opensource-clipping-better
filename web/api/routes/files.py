"""
web.api.routes.files — File upload and output serving endpoints.
"""

from __future__ import annotations

import os
import shutil

from fastapi import Depends, APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse

from ..auth import require_token

router = APIRouter(tags=["files"], dependencies=[Depends(require_token)])

# Resolve absolute path to the project root (2 levels up from web/api/routes)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

UPLOAD_DIR = os.path.join(PROJECT_ROOT, "uploads")
OUTPUTS_DIR = os.path.join(PROJECT_ROOT, "outputs")

# Ensure directories exist
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUTS_DIR, exist_ok=True)

# Max upload size: 2GB
MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024


# Transcripts ride the same endpoint: they are tiny, and a second endpoint would
# duplicate the extension allow-list, the path-traversal sanitizing and the size
# cap for no benefit.
ALLOWED_UPLOAD_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".ts",
    ".vtt", ".srt", ".json3",
}


def sanitize_upload_name(filename):
    """The on-disk name for *filename*: no spaces, no path separators."""
    safe = filename.replace(" ", "_")
    for ch in '<>:"/\\|?*#':
        safe = safe.replace(ch, "")
    return safe


async def save_upload(file) -> str:
    """Stream *file* into uploads/ and return its stored name.

    Extracted from the endpoint so `POST /api/jobs/{id}/source` stores a file
    exactly the same way -- same allow-list, same sanitizing, same size cap --
    instead of growing a second, subtly different implementation.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type: {ext}. "
                f"Allowed: {', '.join(sorted(ALLOWED_UPLOAD_EXTS))}"
            ),
        )

    safe_name = sanitize_upload_name(file.filename)
    dest = os.path.join(UPLOAD_DIR, safe_name)

    total_written = 0
    with open(dest, "wb") as handle:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total_written += len(chunk)
            if total_written > MAX_UPLOAD_SIZE:
                handle.close()
                os.remove(dest)
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"File too large. Maximum size is "
                        f"{MAX_UPLOAD_SIZE // (1024**3)}GB."
                    ),
                )
            handle.write(chunk)
    return safe_name


@router.post("/api/upload")
async def upload_video(file: UploadFile = File(...)) -> dict:
    """
    Upload a video file for processing.

    Returns the stored filename that can be used in ``upload_filename``
    when creating a job.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    safe_name = await save_upload(file)
    size_mb = os.path.getsize(os.path.join(UPLOAD_DIR, safe_name)) / (1024 * 1024)
    return {
        "filename": safe_name,
        "size_mb": round(size_mb, 2),
        "message": f"Upload successful: {safe_name} ({size_mb:.1f} MB)",
    }


def resolve_output_path(job_id, filename=None):
    """A path inside OUTPUTS_DIR, or raise 400. Never trusts the input.

    The previous guard was ``if ".." in job_id or ".." in filename``. Two
    problems with that, and the second is the dangerous one:

    * it is a substring test, so it also rejects a legitimate name containing
      two dots, and
    * it does not address the actual escape, which is ``os.path.join``
      discarding everything before an **absolute** component:
      ``os.path.join("/app/outputs", "/etc", "passwd")`` is ``/etc/passwd``.

    Starlette's router happens to prevent that today, because a path parameter
    does not match ``/``. That is an accident of routing, not a defence:
    changing the route to ``{filename:path}`` -- the obvious edit the first time
    someone wants nested outputs -- would make it exploitable immediately, and
    this API is now reachable over a network.

    So the path is resolved and checked for containment, which holds whatever
    the router does. Symlinks are resolved too, since an output directory is a
    bind mount the user controls.
    """
    parts = [job_id] if filename is None else [job_id, filename]
    for part in parts:
        if not part or part in (".", "..") or os.path.isabs(part):
            raise HTTPException(status_code=400, detail="Invalid path")

    base = os.path.realpath(OUTPUTS_DIR)
    target = os.path.realpath(os.path.join(base, *parts))

    if target != base and not target.startswith(base + os.sep):
        raise HTTPException(status_code=400, detail="Invalid path")
    return target


@router.get("/api/outputs/{job_id}/{filename}")
async def serve_output(job_id: str, filename: str):
    """Serve a rendered clip or other output file."""
    file_path = resolve_output_path(job_id, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    # Determine media type
    ext = os.path.splitext(filename)[1].lower()
    media_types = {
        ".mp4": "video/mp4",
        ".mkv": "video/x-matroska",
        ".avi": "video/x-msvideo",
        ".webm": "video/webm",
        ".json": "application/json",
        ".ass": "text/plain",
        ".srt": "text/plain",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(
        file_path,
        media_type=media_type,
        filename=filename,
    )


@router.get("/api/outputs/{job_id}")
async def list_outputs(job_id: str) -> dict:
    """List all output files for a job."""
    job_dir = resolve_output_path(job_id)
    if not os.path.isdir(job_dir):
        raise HTTPException(status_code=404, detail="Job output directory not found")

    files = []
    for fname in sorted(os.listdir(job_dir)):
        fpath = os.path.join(job_dir, fname)
        if os.path.isfile(fpath):
            size = os.path.getsize(fpath)
            files.append({
                "filename": fname,
                "size_bytes": size,
                "size_mb": round(size / (1024 * 1024), 2),
                "download_url": f"/api/outputs/{job_id}/{fname}",
            })

    return {"job_id": job_id, "files": files, "total": len(files)}
