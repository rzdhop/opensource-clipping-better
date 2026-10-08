"""A thin client for a RunPod Serverless worker-comfyui endpoint (stdlib only).

- :class:`Endpoint` wraps ``POST /run``, ``GET /status``, ``POST /cancel``.
- :func:`submit_template` renders a showrunner template, attaches the input files as
  ``input.images[]`` (the worker uploads any file type to ComfyUI's ``input/`` folder: png,
  wav, mp3 all work) and submits it.
- :func:`save_outputs` writes what came back (``output.images[]`` and, with the repo's patched
  worker handler, ``output.audio[]``: base64 blobs, or S3 URLs when the endpoint has the bucket
  env vars) and returns the local paths.

The API key is read from ``RUNPOD_API_KEY`` or from a ``.env`` line in the repo root; it is never
printed. Payloads must stay under RunPod's 10 MB ``/run`` limit: this client refuses bigger ones.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request

from . import comfy_templates

API = "https://api.runpod.ai/v2"
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT")
RUN_LIMIT_BYTES = 10 * 1024 * 1024
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RunPodError(RuntimeError):
    pass


def api_key(env: dict | None = None, *, name: str = "RUNPOD_API_KEY") -> str:
    """The RunPod key *name* from the environment or a ``.env`` line in the repo root."""
    env = os.environ if env is None else env
    key = (env.get(name) or "").strip()
    dotenv = os.path.join(REPO_ROOT, ".env")
    if not key and os.path.exists(dotenv):
        for line in open(dotenv, encoding="utf-8"):
            if line.startswith(f"{name}="):
                key = line.split("=", 1)[1].split("#", 1)[0].strip().strip('"').strip("'")
    if not key:
        raise RunPodError(f"{name} is not set (environment or .env)")
    return key


class Endpoint:
    def __init__(self, endpoint_id: str, key: str | None = None, *, timeout: int = 60):
        self.id = endpoint_id
        self.key = key or api_key()
        self.timeout = timeout

    # ------------------------------------------------------------ HTTP
    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{API}/{self.id}/{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RunPodError(f"HTTP {exc.code} on {method} {path}: {exc.read().decode('utf-8', 'replace')[:400]}") from exc

    def run(self, payload: dict, *, execution_timeout_s: int | None = None) -> str:
        body: dict = {"input": payload}
        if execution_timeout_s:
            body["policy"] = {"executionTimeout": int(execution_timeout_s) * 1000}
        raw = json.dumps(body).encode("utf-8")
        if len(raw) > RUN_LIMIT_BYTES:
            raise RunPodError(f"payload is {len(raw) / 1e6:.1f} MB, over RunPod's 10 MB /run limit: "
                              "shrink the inputs (smaller png, mono 24 kHz wav)")
        out = self._call("POST", "run", body)
        if "id" not in out:
            raise RunPodError(f"no job id in {out}")
        return out["id"]

    def status(self, job_id: str) -> dict:
        return self._call("GET", f"status/{job_id}")

    def cancel(self, job_id: str) -> dict:
        return self._call("POST", f"cancel/{job_id}")

    def health(self) -> dict:
        return self._call("GET", "health")

    def wait(self, job_id: str, *, poll_s: float = 5.0, timeout_s: float = 1800, on_log=print) -> dict:
        """Poll until the job ends; returns the final status dict (with ``output`` on success)."""
        start = time.monotonic()
        last = None
        while True:
            st = self.status(job_id)
            state = st.get("status")
            if state != last:
                on_log(f"  {job_id}: {state}")
                last = state
            if state in TERMINAL:
                return st
            if time.monotonic() - start > timeout_s:
                raise RunPodError(f"{job_id}: still {state} after {timeout_s:.0f} s")
            time.sleep(poll_s)


# ------------------------------------------------------------------ payloads

def _file_entry(name: str, path: str) -> dict:
    with open(path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode("ascii")
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return {"name": name, "image": f"data:{mime};base64,{data}"}


def build_payload(template_name: str, values: dict, files: dict) -> dict:
    """``{"workflow": <graph>, "images": [...]}`` for *template_name*.

    *files* maps a file placeholder (``image``, ``audio``, ``voice_ref``) to a local path; the
    file is sent under a unique name (``<tag>_<placeholder><ext>``) so parallel jobs never
    overwrite each other in ComfyUI's input folder.
    """
    template = comfy_templates.load_template(template_name)
    tag = values.get("name", "job").replace("/", "_")
    merged = dict(values)
    entries = []
    for placeholder in comfy_templates.file_placeholders(template):
        if placeholder not in files:
            raise ValueError(f"{template_name} needs a file for {placeholder}")
        path = files[placeholder]
        upload_name = f"{tag}_{placeholder}{os.path.splitext(path)[1].lower()}"
        merged[placeholder] = upload_name
        entries.append(_file_entry(upload_name, path))
    graph = comfy_templates.render(template, merged)
    return {"workflow": graph, "images": entries}


def submit_template(endpoint: Endpoint, template_name: str, values: dict, files: dict, *,
                    execution_timeout_s: int | None = 1200) -> str:
    return endpoint.run(build_payload(template_name, values, files), execution_timeout_s=execution_timeout_s)


def save_outputs(status: dict, out_dir: str, *, stem: str) -> list:
    """Write every returned file of a COMPLETED job into *out_dir*: the video as ``<stem>.mp4``,
    audio as ``<stem>.<ext>``, images as ``<stem>_<name>.png``. Returns the local paths (videos
    and audio first). Reads ``images`` (stock handler) and ``audio`` (the repo's patched handler)."""
    if status.get("status") != "COMPLETED":
        raise RunPodError(f"job {status.get('id')} ended {status.get('status')}: "
                          f"{json.dumps(status.get('error') or status.get('output'))[:800]}")
    output = status.get("output") or {}
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    items = [(it, "images") for it in output.get("images") or []] + [(it, "audio") for it in output.get("audio") or []]
    for k, (item, kind) in enumerate(items):
        filename = item.get("filename") or f"out_{k}"
        ext = os.path.splitext(filename)[1] or ".bin"
        whole = ext == ".mp4" or kind == "audio"
        dest = os.path.join(out_dir, f"{stem}{'' if whole else '_' + os.path.splitext(os.path.basename(filename))[0]}{ext}")
        if item.get("type") == "s3_url":
            with urllib.request.urlopen(item["data"], timeout=300) as resp, open(dest, "wb") as fh:
                fh.write(resp.read())
        else:
            with open(dest, "wb") as fh:
                fh.write(base64.b64decode(item["data"]))
        paths.append(dest)
    for err in output.get("errors") or []:
        print("  worker error:", err)
    paths.sort(key=lambda p: (not p.endswith((".mp4", ".flac", ".wav", ".mp3")), p))
    return paths


def billed_seconds(status: dict) -> float:
    """The job's execution time in seconds, the figure we price (DEC-316). The model load of a cold
    worker happens inside the execution; the queue wait (``delayTime``) is shown, never priced."""
    return float(status.get("executionTime") or 0) / 1000.0


def delay_seconds(status: dict) -> float:
    """Queue + worker start before the job ran, in seconds (shown beside the bill, not priced)."""
    return float(status.get("delayTime") or 0) / 1000.0
