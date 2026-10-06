"""RunPod Serverless ComfyUI: the local workflow templates on a rented GPU, by the second (DEC-310).

The link names the template -- ``runpod/i2v_wan22_14b_lightning`` runs
``templates/workflows/i2v_wan22_14b_lightning.json`` -- and the endpoint is
RunPod's ``worker-comfyui`` image with the model files on a network volume
(runbook ``11-INFRA``). The adapter has the shape of the hosted video
adapters (``video.py``): ``estimate``, ``probe``, ``generate``, ``resume``,
journaled the moment RunPod answers the submit, resumed by its job id and
never submitted twice (DEC-151/152).

The API is RunPod's job queue, not ComfyUI's:

* ``POST /run`` takes ``{"input": {"workflow": <API graph>, "images":
  [{"name", "image": data URL}]}}`` and answers a job id at once;
* ``GET /status/{id}`` is polled until it ends; a finished job carries the
  worker's ``executionTime`` and ``delayTime`` (milliseconds) and its output;
* the output files come back base64-encoded under ``output.images`` -- also
  the ``.mp4``, because the worker only collects the ``images`` key of the
  ComfyUI history, which the core ``SaveVideo`` node reports under (read in
  the worker's ``handler.py`` on 2026-10-06; a ``VHS_VideoCombine`` output
  would be dropped, so the templates end in ``CreateVideo -> SaveVideo``).

The keyframe travels inline under a content-addressed name, so a warm
worker never confuses two shots' stills. What RunPod bills -- the GPU
seconds, cold start included -- is logged per clip and kept in
``GenResult.meta`` next to the table's estimate (A-194); the ledger keeps
the estimate, as for every paid link.

Stdlib only, REST through ``transport.py`` (DEC-012).
"""

from __future__ import annotations

import base64
import hashlib
import os
import time

from . import video
from .errors import ProviderError
from .gencache import RequestFailed
from .generation import VIDEO, GenResult, register_adapter
from .local_comfyui import frames_for, load_template, render_template
from .registry import describe
from .transport import (
    APIConnectionError, APITimeoutError, DEFAULT_TIMEOUT, HttpStatusError, data_url, request_json,
    urllib_transport, write_output,
)

API_BASE = "https://api.runpod.ai/v2"
ENV_API_KEY = "RUNPOD_API_KEY"
ENV_ENDPOINT = "RUNPOD_COMFY_ENDPOINT_ID"
ENV_RATE = "RUNPOD_GPU_USD_PER_HOUR"   # optional: the GPU tier's flex price, for the "really billed" line

# The templates a runpod link may name (the link's model). The same files
# local/comfyui runs; ``local_comfyui.VIDEO_TEMPLATES`` is the source of truth.
TEMPLATES = ("i2v_wan22_5b", "i2v_wan22_14b_lightning", "i2v_ltx2")

POLL_INTERVAL_SECONDS = 5.0
# Cold start (a worker boots and reads 35 GB of weights from the volume, 2-5
# min) plus a queue wait when the datacenter is short of GPUs, plus the clip.
POLL_BUDGET_SECONDS = 1800.0
HEALTH_TIMEOUT = 15.0
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT")
RUNNING = ("IN_QUEUE", "IN_PROGRESS")


class RunPodError(ProviderError):
    """RunPod refused or lost the job; the message says why."""


def endpoint_url(endpoint_id: str, path: str = "") -> str:
    base = f"{API_BASE}/{endpoint_id}"
    return f"{base}/{path}" if path else base


def auth_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def inline_image(path: str) -> dict:
    """The keyframe as the worker's ``images[]`` item: a content-addressed
    name (the worker saves it under that name in ComfyUI's input folder; two
    shots on one warm worker never collide) and a data URL."""
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()[:16]
    ext = os.path.splitext(path)[1].lower() or ".png"
    return {"name": f"rzdhop_{digest}{ext}", "image": data_url(path)}


def gpu_seconds(status: dict) -> float:
    """What RunPod bills for a finished job: the worker's execution time plus
    the delay before it picked the job up (a cold start counts; a queue wait
    with no worker up does not show here), in seconds."""
    return (float(status.get("executionTime") or 0) + float(status.get("delayTime") or 0)) / 1000.0


def billed_usd(status: dict, credentials: dict):
    """The clip's real cost when the endpoint's GPU price is configured
    (``RUNPOD_GPU_USD_PER_HOUR``), else None."""
    rate = (credentials or {}).get(ENV_RATE)
    if not rate:
        return None
    try:
        return round(gpu_seconds(status) * float(rate) / 3600.0, 4)
    except (TypeError, ValueError):
        return None


class RunPodComfyAdapter:
    """One clip from one keyframe on a RunPod Serverless ComfyUI endpoint."""

    provider = "runpod"
    speaks_through_transport = True  # a refusal before the submit is booked unbilled (DEC-153)
    poll_interval_seconds = POLL_INTERVAL_SECONDS
    poll_budget_seconds = POLL_BUDGET_SECONDS

    def estimate(self, link, request):
        return video._estimate(link, request)

    def probe(self, link, *, credentials, transport=None, **_):
        """``GET /health``: the key and the endpoint id, and how many workers
        are ready, idle or throttled. Free; never a job."""
        transport = transport or urllib_transport
        label = describe(link)
        try:
            answer = request_json(transport, "GET", endpoint_url(credentials[ENV_ENDPOINT], "health"),
                                  headers=auth_headers(credentials[ENV_API_KEY]), timeout=HEALTH_TIMEOUT)
        except HttpStatusError as exc:
            if exc.status_code in (401, 403):
                return False, f"{label}: RunPod refused {ENV_API_KEY} (HTTP {exc.status_code})"
            if exc.status_code == 404:
                return False, f"{label}: endpoint {credentials[ENV_ENDPOINT]} not found (HTTP 404)"
            return False, f"{label}: HTTP {exc.status_code} from the endpoint's /health"
        except (APIConnectionError, APITimeoutError) as exc:
            return False, f"{label}: endpoint unreachable ({exc})"
        workers = answer.get("workers") or {}
        return True, (f"{label}: endpoint {credentials[ENV_ENDPOINT]} answers; workers ready={workers.get('ready', 0)} "
                      f"idle={workers.get('idle', 0)} running={workers.get('running', 0)} "
                      f"throttled={workers.get('throttled', 0)}")

    @staticmethod
    def plan(link, request):
        """``(name, template, seconds, frames)`` after every check a clip must
        pass before anything is sent; ``ValueError`` names what is wrong.
        The sellable lengths, the seed, the one keyframe, ``out_dir`` and the
        9:16 frame are ``video.clip_seconds``'s checks; the frame count and
        the fps are the template's ``frame_rule``."""
        label = describe(link)
        name = link.model
        if name not in TEMPLATES:
            raise ValueError(f"{label}: no workflow template of that name; the runpod links are "
                             + ", ".join(f"runpod/{t}" for t in TEMPLATES))
        seconds = video.clip_seconds(link, request)
        if not os.path.isfile(request.references[0]):
            raise ValueError(f"{label}: keyframe {request.references[0]} not found")
        template = load_template(name)
        rule = template["frame_rule"]
        if request.fps is not None and int(request.fps) != int(rule["fps"]):
            raise ValueError(f"{label} renders at {rule['fps']} fps, not {request.fps}")
        return name, template, seconds, frames_for(template, seconds)

    def generate(self, link, request, *, credentials, on_log, transport=None, sleep_fn=time.sleep,
                 time_fn=time.monotonic, on_submit=None, **_):
        name, template, seconds, frames = self.plan(link, request)  # refused here, before any call
        rule = template["frame_rule"]
        transport = transport or urllib_transport
        endpoint = credentials[ENV_ENDPOINT]
        headers = auth_headers(credentials[ENV_API_KEY])
        image = inline_image(request.references[0])
        values = {"image_path": image["name"], "prompt": request.prompt, "negative": request.negative or "",
                  "seed": request.seed, "width": rule["width"], "height": rule["height"], "frames": frames,
                  "fps": rule["fps"]}
        body = {"input": {"workflow": render_template(template, values), "images": [image]}}
        # Billed from here on (once a worker takes it), whatever happens next.
        answer = request_json(transport, "POST", endpoint_url(endpoint, "run"), headers=headers, json_body=body,
                              timeout=DEFAULT_TIMEOUT)
        job_id = answer.get("id")
        if not job_id:
            raise RunPodError(f"{describe(link)}: /run answered without a job id: {str(answer)[:200]}")
        status_url = endpoint_url(endpoint, f"status/{job_id}")
        queued = {"request_id": job_id, "status_url": status_url, "response_url": status_url, "endpoint": endpoint}
        on_log(f"   🔁 RunPod: queued {name} ({seconds} s = {frames} frames at {rule['fps']} fps, "
               f"{rule['width']}x{rule['height']}) as job {job_id} on endpoint {endpoint}")
        if on_submit is not None:
            # Journaled between RunPod's answer and the first poll (DEC-151).
            on_submit(dict(queued))
        status = self._poll(link, queued, headers=headers, transport=transport, on_log=on_log, sleep_fn=sleep_fn,
                            time_fn=time_fn)
        return self._finish(link, request, credentials, queued, status, name, template, seconds, frames, on_log)

    def resume(self, link, request, entry, *, credentials, on_log, transport=None, sleep_fn=time.sleep,
               time_fn=time.monotonic, **_):
        """Follow the job a journal *entry* holds until its clip is there.
        Never a new ``/run``; a job RunPod no longer knows answers 404 on its
        own status URL, which the runner reads as never run (``gencache.resume_verdict``)."""
        name, template, seconds, frames = self.plan(link, request)
        transport = transport or urllib_transport
        queued = dict(entry.get("request") or {})
        if not queued.get("request_id"):
            raise RunPodError(f"{describe(link)}: the journal holds no job to resume")
        queued.setdefault("endpoint", credentials[ENV_ENDPOINT])
        headers = auth_headers(credentials[ENV_API_KEY])
        on_log(f"   ↩️ RunPod: following job {queued['request_id']} on endpoint {queued['endpoint']} (not submitted again)")
        status = self._poll(link, queued, headers=headers, transport=transport, on_log=on_log, sleep_fn=sleep_fn,
                            time_fn=time_fn)
        return self._finish(link, request, credentials, queued, status, name, template, seconds, frames, on_log)

    def _poll(self, link, queued, *, headers, transport, on_log, sleep_fn, time_fn) -> dict:
        """Ask ``/status`` until the job ends. :class:`RequestFailed` when it
        ends without a clip (resuming cannot help; the journal lets a later
        run submit afresh); a plain :class:`RunPodError` past the poll budget
        (the job stays journaled for the next run). A 404 on the status URL
        propagates as it is: the runner reads it as a job RunPod never ran."""
        label = describe(link)
        job_id = queued["request_id"]
        # Rebuilt from the id (the same string the journal holds as status_url): the key goes to RunPod's host only.
        url = endpoint_url(queued["endpoint"], f"status/{job_id}")
        deadline = time_fn() + self.poll_budget_seconds
        last = None
        while True:
            status = request_json(transport, "GET", url, headers=headers, timeout=DEFAULT_TIMEOUT)
            state = status.get("status")
            if state != last:
                on_log(f"   ⏳ RunPod: job {job_id} {state}")
                last = state
            if state == "COMPLETED":
                return status
            if state in TERMINAL:
                detail = status.get("error") or (status.get("output") or {}).get("errors") or ""
                raise RequestFailed(f"{label}: job {job_id} {state}: {str(detail)[:300]}")
            if time_fn() >= deadline:
                raise RunPodError(f"{label}: job {job_id} still {state or 'unknown'} after "
                                  f"{self.poll_budget_seconds:.0f} s; it stays journaled and is followed next run")
            sleep_fn(self.poll_interval_seconds)

    @staticmethod
    def _clip(output: dict):
        """The ``.mp4`` among the worker's returned files, else None."""
        files = (output or {}).get("images") or []
        clips = [f for f in files if str(f.get("filename", "")).lower().endswith(".mp4")]
        return (clips or [None])[0]

    def _finish(self, link, request, credentials, queued, status, name, template, seconds, frames, on_log):
        label = describe(link)
        output = status.get("output") or {}
        clip = self._clip(output)
        if clip is None:
            got = ", ".join(str(f.get("filename")) for f in output.get("images") or []) or "nothing"
            errors = "; ".join(str(e) for e in output.get("errors") or [])
            raise RequestFailed(f"{label}: job {queued['request_id']} finished without an .mp4 (got {got}"
                                f"{'; ' + errors if errors else ''}); the template must end in a core SaveVideo node")
        if clip.get("type") != "base64":
            raise RunPodError(f"{label}: the worker returned a {clip.get('type')!r} output; this adapter reads base64 "
                              "(unset BUCKET_ENDPOINT_URL on the endpoint)")
        try:
            data = base64.b64decode(clip.get("data") or "")
        except (ValueError, TypeError) as exc:
            raise RunPodError(f"{label}: the clip's base64 could not be decoded ({exc})") from exc
        if not data:
            raise RunPodError(f"{label}: {clip.get('filename')} came back empty")
        out_name = (request.extra or {}).get("name") or f"runpod_{name}_{request.seed}"
        path = write_output(request.out_dir, out_name, data, "mp4")
        seconds_billed = gpu_seconds(status)
        usd = billed_usd(status, credentials)
        on_log(f"   💸 RunPod: job {queued['request_id']} took {seconds_billed:.0f} GPU-s"
               f"{f' = ${usd:.3f} at ${credentials[ENV_RATE]}/h' if usd is not None else ' (set RUNPOD_GPU_USD_PER_HOUR to price it)'}")
        rule = template["frame_rule"]
        return GenResult(provider="runpod", model=link.model, paths=(path,), seed=request.seed, meta={
            "template": name, "job_id": queued["request_id"], "endpoint": queued["endpoint"],
            "output": clip.get("filename"), "gpu_seconds": seconds_billed, "billed_usd": usd,
            "execution_ms": status.get("executionTime"), "delay_ms": status.get("delayTime"),
            "worker_id": status.get("workerId"),
            "clip_s": seconds, "frames": frames, "fps": int(rule["fps"]),
            "width": int(rule["width"]), "height": int(rule["height"]),
            **video._meta(link, request),
        })


RUNPOD_COMFY = RunPodComfyAdapter()
register_adapter(VIDEO, "runpod", RUNPOD_COMFY)
