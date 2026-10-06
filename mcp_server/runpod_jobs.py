"""RunPod Serverless ComfyUI jobs of any kind (image or video), journaled.

The app's adapter (``clipping/providers/runpod_comfyui.py``) makes one clip
from one keyframe through the generation chain. The MCP needs the same
endpoint for anything a template can make -- a cast sheet, a prop, a plate,
a clip -- submitted now and fetched later by its id, several at a time
across the endpoint's workers. So this module is the kind-agnostic layer
under both: pick a template, fill its placeholders, inline the images,
``POST /run``, remember the job in a journal, read ``/status`` on demand and
write what the worker returns (every file, images and ``.mp4`` alike) into
the folder the caller named.

Shared with the adapter: the endpoint URLs, the auth header, the inline
image naming, the billed-seconds arithmetic (``runpod_comfyui``), the
templates and their rendering (``local_comfyui``) and the REST transport
(``transport``). Stdlib only (DEC-012).
"""

from __future__ import annotations

import base64
import glob
import json
import os
import random
import tempfile
import threading
import time
from datetime import datetime, timezone

from clipping.providers.local_comfyui import WORKFLOWS_DIR, frames_for, load_template, render_template
from clipping.providers.runpod_comfyui import TERMINAL, auth_headers, endpoint_url, gpu_seconds, inline_image
from clipping.providers.transport import (
    APIConnectionError, APITimeoutError, DEFAULT_TIMEOUT, HttpStatusError, request_json, urllib_transport,
    write_output,
)

from .config import ROOT, Settings

# A template's ``task`` decides which endpoint runs it.
TASK_KIND = {"t2i": "image", "edit": "image", "i2v": "video", "t2v": "video", "ia2v": "video", "flf2v": "video"}
# Image templates have no frame rule, so a default frame: 9:16 at FLUX's comfortable size.
IMAGE_DEFAULT = {"width": 832, "height": 1216}
JOURNAL_REL = os.path.join("mcp", "jobs.json")
JOURNAL_KEEP = 500
HEALTH_TIMEOUT = 15.0
# Everything the worker returns is read from ``output.images`` (DEC-310: the
# worker collects only that key; a core SaveVideo reports its .mp4 there).
OUTPUT_KEY = "images"


class JobError(Exception):
    """The job could not be submitted, read or fetched; the message says why."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------- templates

def list_templates() -> list:
    """Every ``comfy_workflow_v1`` template in the repo, summarised for a
    tool listing: what it makes, which endpoint kind runs it, the values it
    takes, the clip lengths it sells, the model files it needs."""
    rows = []
    for path in sorted(glob.glob(os.path.join(WORKFLOWS_DIR, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            template = json.load(fh)
        if template.get("$schema") != "comfy_workflow_v1":
            continue
        task = template.get("task", "")
        row = {
            "name": template.get("name") or os.path.splitext(os.path.basename(path))[0],
            "task": task,
            "kind": TASK_KIND.get(task, "video"),
            "placeholders": list(template.get("placeholders", [])),
            "verified_live": bool(template.get("verified_live")),
            "ref_slots": int(template.get("ref_slots") or 0),
            "requires": [req.get("file") for req in template.get("requires", [])],
            "description": (template.get("description") or "").split(". ")[0],
        }
        rule = template.get("frame_rule")
        if rule:
            row["frame_rule"] = {k: rule[k] for k in ("fps", "width", "height", "lengths", "max_frames") if k in rule}
        rows.append(row)
    return rows


def template_kind(template: dict) -> str:
    return TASK_KIND.get(template.get("task", ""), "video")


# ------------------------------------------------------------------- journal

class Journal:
    """The jobs this server submitted, newest last, in ``outputs/mcp/jobs.json``.

    Written atomically under a lock; a record is a plain dict keyed by
    ``job_id``. Only the last :data:`JOURNAL_KEEP` are kept on disk."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()

    def _read(self) -> list:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return []
        except ValueError:
            return []
        return list(data.get("jobs") or []) if isinstance(data, dict) else []

    def _write(self, jobs: list) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix=".jobs-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump({"jobs": jobs[-JOURNAL_KEEP:]}, fh, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def all(self) -> list:
        with self._lock:
            return self._read()

    def get(self, job_id: str):
        with self._lock:
            for record in self._read():
                if record.get("job_id") == job_id:
                    return record
        return None

    def put(self, record: dict) -> dict:
        with self._lock:
            jobs = self._read()
            for i, existing in enumerate(jobs):
                if existing.get("job_id") == record["job_id"]:
                    jobs[i] = record
                    break
            else:
                jobs.append(record)
            self._write(jobs)
        return record

    def update(self, job_id: str, **fields) -> dict:
        with self._lock:
            record = self.get(job_id)
            if record is None:
                raise JobError(f"job {job_id} is not in the journal")
            record.update(fields)
            return self.put(record)


# ------------------------------------------------------------------- client

class JobClient:
    """Submit, read and fetch jobs on the configured endpoints."""

    def __init__(self, settings: Settings, *, transport=None, journal=None, rng=None):
        self.settings = settings
        self.transport = transport or urllib_transport
        self.journal = journal or Journal(os.path.join(settings.outputs_dir, JOURNAL_REL))
        self.rng = rng or random.Random()

    # -- helpers

    def _headers(self, kind: str = "video") -> dict:
        key = self.settings.key(kind)
        if not key:
            raise JobError("RUNPOD_API_KEY is not set in .env")
        return auth_headers(key)

    def resolve_path(self, path: str) -> str:
        """An input file the caller named: absolute, or relative to the
        outputs folder, then to the repo. Must exist."""
        candidates = [path] if os.path.isabs(path) else [
            os.path.join(self.settings.outputs_dir, path), os.path.join(ROOT, path)]
        for candidate in candidates:
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
        raise JobError(f"input file not found: {path}")

    def dest_dir(self, dest: str | None) -> str:
        """Where a job's files go: *dest* (absolute, or relative to the
        outputs folder), else ``outputs/mcp/<date>``."""
        if not dest:
            return os.path.join(self.settings.outputs_dir, "mcp", datetime.now().strftime("%Y-%m-%d"))
        return os.path.abspath(dest if os.path.isabs(dest) else os.path.join(self.settings.outputs_dir, dest))

    def _request(self, method: str, url: str, *, json_body=None, timeout=DEFAULT_TIMEOUT, kind: str = "video") -> dict:
        try:
            return request_json(self.transport, method, url, headers=self._headers(kind), json_body=json_body,
                                timeout=timeout)
        except HttpStatusError as exc:
            if exc.status_code in (401, 403):
                raise JobError(f"RunPod refused RUNPOD_API_KEY (HTTP {exc.status_code})") from exc
            if exc.status_code == 404:
                raise JobError(f"RunPod: not found (HTTP 404) at {url.split('/v2/')[-1]}") from exc
            raise JobError(f"RunPod answered HTTP {exc.status_code}: {exc}") from exc
        except (APIConnectionError, APITimeoutError) as exc:
            raise JobError(f"RunPod unreachable: {exc}") from exc

    # -- health

    def health(self, kind: str | None = None) -> dict:
        """``GET /health`` of one kind's endpoint, or of every configured one."""
        kinds = [kind] if kind else sorted(set(self.settings.endpoints) or {"video"})
        report = {}
        for k in kinds:
            endpoint = self.settings.endpoint(k)
            try:
                answer = self._request("GET", endpoint_url(endpoint, "health"), timeout=HEALTH_TIMEOUT, kind=k)
                report[k] = {"endpoint": endpoint, "ok": True, "workers": answer.get("workers") or {},
                             "jobs": answer.get("jobs") or {}}
            except JobError as exc:
                report[k] = {"endpoint": endpoint, "ok": False, "error": str(exc)}
        return report

    # -- submit

    def plan(self, template_name: str, *, prompt: str, negative: str = "", seed=None, width=None, height=None,
             seconds=None, image_path=None, ref_paths=None) -> dict:
        """Everything a submit needs, checked before anything is sent:
        ``{"template", "kind", "values", "images", "frames", "seconds"}``.
        ``JobError`` names what is wrong."""
        try:
            template = load_template(template_name)
        except (FileNotFoundError, ValueError) as exc:
            known = ", ".join(t["name"] for t in list_templates())
            raise JobError(f"no template {template_name!r}; known: {known}") from exc
        kind = template_kind(template)
        placeholders = set(template.get("placeholders", []))
        seed = int(seed) if seed is not None else self.rng.randrange(1, 2**31 - 1)
        values = {"prompt": prompt, "negative": negative or "", "seed": seed}
        images = []
        frames = None
        rule = template.get("frame_rule")
        if rule:
            if seconds is None:
                seconds = int(rule["lengths"][-1])
            lengths = [int(n) for n in rule["lengths"]]
            if int(seconds) not in lengths:
                raise JobError(f"{template_name} makes clips of {', '.join(map(str, lengths))} s, not {seconds}")
            frames = frames_for(template, int(seconds))
            values.update({"width": int(width or rule["width"]), "height": int(height or rule["height"]),
                           "frames": frames, "fps": int(rule["fps"])})
        else:
            values.update({"width": int(width or IMAGE_DEFAULT["width"]),
                           "height": int(height or IMAGE_DEFAULT["height"])})
        if "image_path" in placeholders:
            if not image_path:
                raise JobError(f"{template_name} needs image_path (the keyframe)")
            item = inline_image(self.resolve_path(image_path))
            images.append(item)
            values["image_path"] = item["name"]
        if template.get("ref_slots"):
            refs = [self.resolve_path(p) for p in (ref_paths or [])]
            if not refs:
                raise JobError(f"{template_name} needs ref_paths (1-{template['ref_slots']} reference images)")
            names = []
            for path in refs:
                item = inline_image(path)
                if item["name"] not in {i["name"] for i in images}:
                    images.append(item)
                names.append(item["name"])
            values["ref_paths"] = names
        try:
            graph = render_template(template, values)
        except ValueError as exc:
            raise JobError(str(exc)) from exc
        return {"template": template, "name": template_name, "kind": kind, "values": values, "images": images,
                "graph": graph, "frames": frames, "seconds": int(seconds) if seconds is not None else None}

    def submit(self, template_name: str, *, prompt: str, negative: str = "", seed=None, width=None, height=None,
               seconds=None, image_path=None, ref_paths=None, name=None, dest=None, note=None) -> dict:
        """One ``POST /run``; the journal record, state ``IN_QUEUE``."""
        plan = self.plan(template_name, prompt=prompt, negative=negative, seed=seed, width=width, height=height,
                         seconds=seconds, image_path=image_path, ref_paths=ref_paths)
        endpoint = self.settings.endpoint(plan["kind"])
        body = {"input": {"workflow": plan["graph"], "images": plan["images"]}}
        answer = self._request("POST", endpoint_url(endpoint, "run"), json_body=body, kind=plan["kind"])
        job_id = answer.get("id")
        if not job_id:
            raise JobError(f"/run answered without a job id: {str(answer)[:200]}")
        values = plan["values"]
        record = {
            "job_id": job_id, "endpoint": endpoint, "kind": plan["kind"], "template": template_name,
            "name": name or f"{template_name}_{values['seed']}", "dest_dir": self.dest_dir(dest),
            "note": note or "", "submitted_at": utc_now(), "state": answer.get("status") or "IN_QUEUE",
            "prompt": (prompt or "")[:300], "seed": values["seed"], "width": values["width"],
            "height": values["height"], "seconds": plan["seconds"], "frames": plan["frames"],
            "fps": values.get("fps"), "inputs": [i["name"] for i in plan["images"]],
            "gpu_seconds": None, "billed_usd": None, "outputs": [], "error": "", "finished_at": None,
            "worker_id": None,
        }
        return self.journal.put(record)

    # -- status and outputs

    def _billed(self, record: dict, status: dict) -> dict:
        seconds = gpu_seconds(status)
        rate = self.settings.rate(record.get("kind", "video"))
        usd = round(seconds * rate / 3600.0, 4) if rate else None
        return {"gpu_seconds": round(seconds, 1), "billed_usd": usd, "worker_id": status.get("workerId"),
                "execution_ms": status.get("executionTime"), "delay_ms": status.get("delayTime")}

    def _save_outputs(self, record: dict, output: dict) -> list:
        """Write every file the worker returned into the record's folder as
        ``<name>_<n>.<ext>`` (``<name>.<ext>`` for a single file)."""
        files = (output or {}).get(OUTPUT_KEY) or []
        if not files:
            errors = "; ".join(str(e) for e in (output or {}).get("errors") or [])
            keys = ", ".join(sorted(k for k in (output or {}) if k != OUTPUT_KEY))
            raise JobError(f"job {record['job_id']} finished without a file"
                           f"{' (output keys: ' + keys + ')' if keys else ''}{'; ' + errors if errors else ''}; "
                           "a template must end in a core SaveImage/SaveVideo node")
        paths = []
        for n, item in enumerate(files, 1):
            if item.get("type") != "base64":
                raise JobError(f"job {record['job_id']}: the worker returned a {item.get('type')!r} output; "
                               "this server reads base64 (unset BUCKET_ENDPOINT_URL on the endpoint)")
            try:
                data = base64.b64decode(item.get("data") or "")
            except (ValueError, TypeError) as exc:
                raise JobError(f"job {record['job_id']}: {item.get('filename')} is not valid base64") from exc
            if not data:
                raise JobError(f"job {record['job_id']}: {item.get('filename')} came back empty")
            ext = os.path.splitext(str(item.get("filename") or ""))[1].lstrip(".").lower() or "bin"
            stem = record["name"] if len(files) == 1 else f"{record['name']}_{n}"
            paths.append(write_output(record["dest_dir"], stem, data, ext))
        return paths

    def status(self, job_id: str) -> dict:
        """The journal record after one ``GET /status``. A job that ended is
        settled here: its files written (``outputs``) or its error kept; a
        later call reads the journal only."""
        record = self.journal.get(job_id)
        if record is None:
            raise JobError(f"job {job_id} is not in the journal (not submitted by this server)")
        if record["state"] in TERMINAL:
            return record
        try:
            status = self._request("GET", endpoint_url(record["endpoint"], f"status/{job_id}"),
                                   kind=record.get("kind", "video"))
        except JobError as exc:
            if "404" in str(exc):
                return self.journal.update(job_id, state="GONE", error="RunPod no longer knows this job",
                                           finished_at=utc_now())
            raise
        state = status.get("status") or "UNKNOWN"
        if state == "COMPLETED":
            fields = {"state": state, "finished_at": utc_now(), **self._billed(record, status)}
            try:
                fields["outputs"] = self._save_outputs(record, status.get("output") or {})
            except JobError as exc:
                fields.update(state="FAILED", error=str(exc))
            return self.journal.update(job_id, **fields)
        if state in TERMINAL:
            detail = status.get("error") or (status.get("output") or {}).get("errors") or ""
            return self.journal.update(job_id, state=state, error=str(detail)[:500], finished_at=utc_now(),
                                       **self._billed(record, status))
        return self.journal.update(job_id, state=state)

    def wait(self, job_id: str, *, timeout_s: float, poll_s: float = 5.0, sleep_fn=time.sleep,
             time_fn=time.monotonic) -> dict:
        """:meth:`status` until the job ends or *timeout_s* passes; the
        record either way (its ``state`` says which)."""
        deadline = time_fn() + max(0.0, float(timeout_s))
        while True:
            record = self.status(job_id)
            remaining = deadline - time_fn()
            if record["state"] in TERMINAL or record["state"] == "GONE" or remaining <= 0:
                return record
            sleep_fn(min(poll_s, remaining))

    def cancel(self, job_id: str) -> dict:
        record = self.journal.get(job_id)
        if record is None:
            raise JobError(f"job {job_id} is not in the journal")
        if record["state"] in TERMINAL:
            return record
        self._request("POST", endpoint_url(record["endpoint"], f"cancel/{job_id}"), kind=record.get("kind", "video"))
        return self.journal.update(job_id, state="CANCELLED", finished_at=utc_now(), error="cancelled by request")

    def recent(self, limit: int = 20, *, refresh: bool = False) -> list:
        """The last *limit* records; with *refresh*, each unfinished one is
        read from RunPod first."""
        jobs = self.journal.all()[-max(1, int(limit)):]
        if refresh:
            jobs = [self.status(j["job_id"]) if j["state"] not in TERMINAL and j["state"] != "GONE" else j
                    for j in jobs]
        return list(reversed(jobs))

    def ledger(self, since: str | None = None) -> dict:
        """GPU seconds and dollars of the finished jobs, per kind and in all,
        from *since* (ISO date) when given."""
        totals = {"jobs": 0, "gpu_seconds": 0.0, "billed_usd": 0.0, "unpriced_jobs": 0, "by_kind": {}}
        for record in self.journal.all():
            if record.get("gpu_seconds") is None:
                continue
            if since and (record.get("finished_at") or "") < since:
                continue
            kind = record.get("kind", "video")
            bucket = totals["by_kind"].setdefault(kind, {"jobs": 0, "gpu_seconds": 0.0, "billed_usd": 0.0})
            for b in (totals, bucket):
                b["jobs"] += 1
                b["gpu_seconds"] += float(record["gpu_seconds"] or 0)
                if record.get("billed_usd") is not None:
                    b["billed_usd"] += float(record["billed_usd"])
            if record.get("billed_usd") is None:
                totals["unpriced_jobs"] += 1
        for b in [totals, *totals["by_kind"].values()]:
            b["gpu_seconds"] = round(b["gpu_seconds"], 1)
            b["billed_usd"] = round(b["billed_usd"], 3)
        return totals
