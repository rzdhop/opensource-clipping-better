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

Plan 32 stage 8 (DEC-315 §6): ``runpod/s2v_wan22`` is a talking clip -- the
keyframe and the shot's dialogue track (``GenRequest.audio``, a WAV) both
travel inline under content-addressed names, as the MCP's job client sends
them (``mcp_server/runpod_jobs.py``), and the mouth follows the voice. The
template is ``served_by: audio``: it runs where the voice lines run (the
audio endpoint, else the image one, else the video one; ``tts.audio_endpoint``),
on that endpoint's key, billed at its rate. Its mp4 carries the track the
worker muxed in; that sound is the dialogue the render lays itself, so the
adapter strips it (ffmpeg, the picture copied) before the clip is kept: the
clip has no sound of its own and is never mixed as ambience.

Stdlib only (ffmpeg on PATH for a talking clip), REST through ``transport.py`` (DEC-012).
"""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import time

from . import video
from .errors import ProviderError
from .gencache import RequestFailed
from .generation import VIDEO, GenResult, register_adapter
from .local_comfyui import frames_for, load_template, render_template, with_default_negative
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
# Plan 32 stage 8: the talking template (a keyframe and a dialogue track); a
# sibling tuple, since it sells one length and runs on the voice lines' endpoint.
S2V_TEMPLATES = ("s2v_wan22",)
FFMPEG_INSTALL = "install ffmpeg (apt-get install ffmpeg)"
_FFMPEG_TIMEOUT_S = 120

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


def talks(name) -> bool:
    """Whether the template *name* makes a talking clip (keyframe + dialogue track)."""
    return name in S2V_TEMPLATES


def serving(name, credentials) -> tuple:
    """``(endpoint, key, rate)`` of the endpoint that runs the template
    *name*: a talking template runs where the voice lines run (its
    ``served_by: audio``, ``tts.audio_endpoint`` / ``audio_key`` /
    ``audio_rate``), every other one on the video endpoint, as always."""
    credentials = credentials or {}
    if talks(name):
        from . import tts  # noqa: PLC0415 - tts imports this module: a cycle at import time

        return tts.audio_endpoint(credentials), tts.audio_key(credentials), tts.audio_rate(credentials)
    return credentials.get(ENV_ENDPOINT, ""), credentials.get(ENV_API_KEY, ""), credentials.get(ENV_RATE) or None


def strip_audio_argv(src, dest) -> list:
    """The ffmpeg command that keeps *src*'s picture alone (copied, never
    re-encoded) in *dest*: a talking clip's muxed dialogue dropped."""
    return ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", os.fspath(src), "-map", "0:v:0", "-c", "copy", "-an",
            "-map_metadata", "-1", "-movflags", "+faststart", os.fspath(dest)]


def strip_audio(label, src, dest, *, run=None) -> str:
    """*src* written to *dest* without its sound (:func:`strip_audio_argv`;
    *run*: ``subprocess.run``, the seam the tests replace). ``RunPodError``
    naming what went wrong."""
    runner = run or subprocess.run
    try:
        result = runner(strip_audio_argv(src, dest), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                        timeout=_FFMPEG_TIMEOUT_S)
    except FileNotFoundError:
        raise RunPodError(f"{label}: the talking clip came back with its dialogue muxed in and ffmpeg is not "
                          f"installed to drop it: {FFMPEG_INSTALL}") from None
    if result.returncode != 0 or not os.path.isfile(dest) or os.path.getsize(dest) == 0:
        detail = (getattr(result, "stderr", "") or "").strip()[:200]
        raise RunPodError(f"{label}: ffmpeg could not drop the talking clip's sound{': ' + detail if detail else ''}")
    return os.fspath(dest)


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
        if talks(link.model):
            # Plan 32 stage 8: a talking clip runs on the voice lines' endpoint, with its key.
            endpoint, key, _rate = serving(link.model, credentials)
            credentials = dict(credentials, **{ENV_ENDPOINT: endpoint, ENV_API_KEY: key})
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
        if name not in TEMPLATES + S2V_TEMPLATES:
            raise ValueError(f"{label}: no workflow template of that name; the runpod links are "
                             + ", ".join(f"runpod/{t}" for t in TEMPLATES + S2V_TEMPLATES))
        seconds = video.clip_seconds(link, request)
        if not os.path.isfile(request.references[0]):
            raise ValueError(f"{label}: keyframe {request.references[0]} not found")
        audio = getattr(request, "audio", None)
        if talks(name):
            if not audio:
                raise ValueError(f"{label} makes a talking clip: its dialogue track is missing (GenRequest.audio)")
            if not os.path.isfile(audio):
                raise ValueError(f"{label}: the dialogue track {audio} is not there")
            if os.path.splitext(audio)[1].lower() != ".wav":
                raise ValueError(f"{label}: the dialogue track must be a .wav file, not {audio}")
        elif audio:
            raise ValueError(f"{label} makes a silent clip from a keyframe: it takes no dialogue track")
        template = load_template(name)
        rule = template["frame_rule"]
        if request.fps is not None and int(request.fps) != int(rule["fps"]):
            raise ValueError(f"{label} renders at {rule['fps']} fps, not {request.fps}")
        return name, template, seconds, frames_for(template, seconds)

    def generate(self, link, request, *, credentials, on_log, transport=None, sleep_fn=time.sleep,
                 time_fn=time.monotonic, on_submit=None, run=None, **_):
        name, template, seconds, frames = self.plan(link, request)  # refused here, before any call
        rule = template["frame_rule"]
        transport = transport or urllib_transport
        endpoint, key, _rate = serving(name, credentials)
        if not endpoint or not key:
            raise ProviderError(f"{describe(link)}: no RunPod endpoint or key serves it; set RUNPOD_AUDIO_ENDPOINT_ID, "
                                f"RUNPOD_IMAGE_ENDPOINT_ID or {ENV_ENDPOINT} with its key in .env.")
        headers = auth_headers(key)
        image = inline_image(request.references[0])
        values = {"image_path": image["name"], "prompt": request.prompt,
                  "negative": with_default_negative(template, request.negative),
                  "seed": request.seed, "width": rule["width"], "height": rule["height"], "frames": frames,
                  "fps": rule["fps"]}
        images = [image]
        if talks(name):
            # The track travels like the keyframe, under a content-addressed .wav name (the MCP's job client).
            track = inline_image(request.audio)
            values["audio_path"] = track["name"]
            images = [track, image]
        body = {"input": {"workflow": render_template(template, values), "images": images}}
        # Billed from here on (once a worker takes it), whatever happens next.
        answer = request_json(transport, "POST", endpoint_url(endpoint, "run"), headers=headers, json_body=body,
                              timeout=DEFAULT_TIMEOUT)
        job_id = answer.get("id")
        if not job_id:
            raise RunPodError(f"{describe(link)}: /run answered without a job id: {str(answer)[:200]}")
        status_url = endpoint_url(endpoint, f"status/{job_id}")
        queued = {"request_id": job_id, "status_url": status_url, "response_url": status_url, "endpoint": endpoint}
        on_log(f"   🔁 RunPod: queued {name} ({seconds} s = {frames} frames at {rule['fps']} fps, "
               f"{rule['width']}x{rule['height']}{', with its dialogue track' if talks(name) else ''}) as job "
               f"{job_id} on endpoint {endpoint}")
        if on_submit is not None:
            # Journaled between RunPod's answer and the first poll (DEC-151).
            on_submit(dict(queued))
        status = self._poll(link, queued, headers=headers, transport=transport, on_log=on_log, sleep_fn=sleep_fn,
                            time_fn=time_fn)
        return self._finish(link, request, credentials, queued, status, name, template, seconds, frames, on_log,
                            run=run)

    def resume(self, link, request, entry, *, credentials, on_log, transport=None, sleep_fn=time.sleep,
               time_fn=time.monotonic, run=None, **_):
        """Follow the job a journal *entry* holds until its clip is there.
        Never a new ``/run``; a job RunPod no longer knows answers 404 on its
        own status URL, which the runner reads as never run (``gencache.resume_verdict``)."""
        name, template, seconds, frames = self.plan(link, request)
        transport = transport or urllib_transport
        queued = dict(entry.get("request") or {})
        if not queued.get("request_id"):
            raise RunPodError(f"{describe(link)}: the journal holds no job to resume")
        endpoint, key, _rate = serving(name, credentials)
        queued.setdefault("endpoint", endpoint)
        headers = auth_headers(key)
        on_log(f"   ↩️ RunPod: following job {queued['request_id']} on endpoint {queued['endpoint']} (not submitted again)")
        status = self._poll(link, queued, headers=headers, transport=transport, on_log=on_log, sleep_fn=sleep_fn,
                            time_fn=time_fn)
        return self._finish(link, request, credentials, queued, status, name, template, seconds, frames, on_log,
                            run=run)

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

    def _finish(self, link, request, credentials, queued, status, name, template, seconds, frames, on_log, *,
                run=None):
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
        if talks(name):
            # The worker muxed the dialogue track in: the render lays the lines itself, so the clip keeps
            # its picture alone (never mixed as ambience: ``clips.clip_has_audio`` finds no sound).
            answered = write_output(request.out_dir, f"{out_name}.answer", data, "mp4")
            try:
                path = strip_audio(label, answered, os.path.join(request.out_dir, f"{out_name}.mp4"), run=run)
            finally:
                try:
                    os.unlink(answered)
                except OSError:
                    pass
        else:
            path = write_output(request.out_dir, out_name, data, "mp4")
        seconds_billed = gpu_seconds(status)
        _endpoint, _key, rate = serving(name, credentials)
        usd = billed_usd(status, {ENV_RATE: rate})
        on_log(f"   💸 RunPod: job {queued['request_id']} took {seconds_billed:.0f} GPU-s"
               f"{f' = ${usd:.3f} at ${rate}/h' if usd is not None else ' (set RUNPOD_GPU_USD_PER_HOUR to price it)'}")
        rule = template["frame_rule"]
        return GenResult(provider="runpod", model=link.model, paths=(path,), seed=request.seed, meta={
            "template": name, "job_id": queued["request_id"], "endpoint": queued["endpoint"],
            "output": clip.get("filename"), "gpu_seconds": seconds_billed, "billed_usd": usd,
            "execution_ms": status.get("executionTime"), "delay_ms": status.get("delayTime"),
            "worker_id": status.get("workerId"),
            "clip_s": seconds, "frames": frames, "fps": int(rule["fps"]),
            "width": int(rule["width"]), "height": int(rule["height"]),
            **({"served_by": "audio", "audio_stripped": True} if talks(name) else {}),
            **video._meta(link, request),
        })


RUNPOD_COMFY = RunPodComfyAdapter()
register_adapter(VIDEO, "runpod", RUNPOD_COMFY)
