"""
web.api.cleanup — remove a deleted job's files, and nothing else.

Deleting a job used to drop its record and leave outputs/<id>/ and its upload
on disk forever. This removes them under rules narrow enough that a malformed
record cannot reach anything else:

- A path is only ever a single, plain name directly inside its root: no
  separator, no ``.``/``..``, no drive, not the root itself, not a symlink, and
  the right kind (a directory in outputs/, a file in uploads/). The job id is
  not always ours -- ``reuse_job_id`` comes from the client -- and upload names
  come from the uploader's own file name.
- An upload is removed only when no other job references the same name, and
  the file is not newer than when this job took it. Uploads keep their
  original name, so a later upload of another ``talk.mp4`` replaced this
  job's file: that one belongs to someone else, possibly a job not created yet.
  Without a usable timestamp the upload is kept -- a leaked file is recoverable,
  a deleted one is not.

Stdlib only, so the rules are tested in the pytest-only CI environment.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime

# File-system timestamps are coarse (2s on FAT) and an upload finishes a moment
# before the job that uses it is created.
_MTIME_SLACK_SECONDS = 2.0


def contained(root: str, name, *, want_dir: bool):
    """The real path of *name* directly inside *root*, or None if it is
    anything else."""
    if not isinstance(name, str) or name in ("", ".", ".."):
        return None
    if "/" in name or "\\" in name or os.path.splitdrive(name)[0] or os.path.isabs(name):
        return None
    base = os.path.realpath(root)
    path = os.path.join(base, name)
    if os.path.islink(path):
        return None
    real = os.path.realpath(path)
    if real == base or os.path.dirname(real) != base:
        return None
    if want_dir and not os.path.isdir(real):
        return None
    if not want_dir and not os.path.isfile(real):
        return None
    return real


def upload_names(job: dict) -> set[str]:
    """Every uploads/ name *job* refers to, on the record or in its config."""
    config = job.get("config") or {}
    names = {
        job.get("upload_filename"), job.get("transcript_filename"),
        config.get("upload_filename"), config.get("transcript_filename"),
    }
    return {name for name in names if isinstance(name, str) and name}


def _timestamp(value):
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    return None


def _took_uploads_at(job: dict):
    """When *job* last took an upload: its creation, or a later attached source."""
    stamps = [_timestamp(job.get("created_at")), _timestamp(job.get("source_attached_at"))]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def remove_job_files(job: dict, *, outputs_root: str, uploads_root: str, other_jobs) -> dict:
    """Remove *job*'s output directory and the uploads only it uses.

    Returns ``{"removed": [...], "kept": [...]}``, both human-readable.
    """
    report = {"removed": [], "kept": []}

    job_id = job.get("id")
    out_dir = contained(outputs_root, job_id, want_dir=True)
    if out_dir is not None:
        try:
            shutil.rmtree(out_dir)
            report["removed"].append(f"outputs/{job_id}/")
        except OSError as exc:
            report["kept"].append(f"outputs/{job_id}/ ({exc})")

    shared = set()
    for other in other_jobs:
        shared |= upload_names(other)
    took_at = _took_uploads_at(job)

    for name in sorted(upload_names(job)):
        path = contained(uploads_root, name, want_dir=False)
        if path is None:
            continue
        if name in shared:
            report["kept"].append(f"uploads/{name} (another job uses it)")
            continue
        if took_at is None:
            report["kept"].append(f"uploads/{name} (cannot tell whose it is)")
            continue
        if os.path.getmtime(path) > took_at + _MTIME_SLACK_SECONDS:
            report["kept"].append(f"uploads/{name} (replaced by a newer upload)")
            continue
        try:
            os.remove(path)
            report["removed"].append(f"uploads/{name}")
        except OSError as exc:
            report["kept"].append(f"uploads/{name} ({exc})")

    return report
