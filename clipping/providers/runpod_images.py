"""RunPod Serverless ComfyUI for images: the local image templates on a rented
GPU, by the second (DEC-312, the image half of DEC-310).

The link names the template, as the video links do -- ``runpod/t2i_flux2_klein``
runs ``templates/workflows/t2i_flux2_klein.json``, ``runpod/edit_flux2_klein_
multiref`` the four-reference edit, ``runpod/edit_qwen_image`` the Qwen edit --
on the **image endpoint** (``RUNPOD_IMAGE_ENDPOINT_ID``), so a worker that
holds the FLUX weights never swaps them for Wan's; without one, the video
endpoint serves images too (``RUNPOD_COMFY_ENDPOINT_ID``). Same job queue,
same journal and resume, same billed-seconds line as the video adapter
(``runpod_comfyui``), whose poll loop this reuses. The references of an edit
travel inline under content-addressed names, like the keyframe of a clip.

Priced per image (``pricing.PRICES``), as the hosted image links are: a
conservative figure a 5090 beats warm, with the cold start folded in.
Stdlib only (DEC-012).
"""

from __future__ import annotations

import base64
import os
import random
import time

from . import images, pricing
from .gencache import RequestFailed
from .generation import IMAGE, IMAGE_EDIT, GenResult, is_paid, register_adapter
from .local_comfyui import load_template, render_template
from .registry import describe
from .runpod_comfyui import (
    ENV_API_KEY, ENV_ENDPOINT, RunPodComfyAdapter, RunPodError, auth_headers, billed_usd, endpoint_url,
    gpu_seconds, inline_image,
)
from .transport import DEFAULT_TIMEOUT, request_json, urllib_transport, write_output

ENV_IMAGE_ENDPOINT = "RUNPOD_IMAGE_ENDPOINT_ID"
ENV_IMAGE_KEY = "RUNPOD_IMAGE_API_KEY"        # optional: a key of its own for the image endpoint
ENV_IMAGE_RATE = "RUNPOD_IMAGE_GPU_USD_PER_HOUR"

# The image templates a runpod link may name, by the kind of request each serves.
TEMPLATES = {IMAGE: ("t2i_flux2_klein",), IMAGE_EDIT: ("edit_flux2_klein_multiref", "edit_qwen_image")}
ALL_TEMPLATES = TEMPLATES[IMAGE] + TEMPLATES[IMAGE_EDIT]
# An image is seconds of GPU once the worker is warm; a cold start reads the
# weights from the volume (FLUX.2 klein ≈ 15 GB: 1-3 min).
POLL_BUDGET_SECONDS = 900.0


def image_endpoint(credentials: dict) -> str:
    return credentials.get(ENV_IMAGE_ENDPOINT) or credentials[ENV_ENDPOINT]


def image_key(credentials: dict) -> str:
    """The image endpoint's key when one is set, else the account key."""
    return credentials.get(ENV_IMAGE_KEY) or credentials[ENV_API_KEY]


def image_rate(credentials: dict):
    """The GPU price that prices an image job: the image endpoint's own, else
    -- when images run on the video endpoint -- the video one's."""
    if credentials.get(ENV_IMAGE_ENDPOINT):
        return credentials.get(ENV_IMAGE_RATE)
    return credentials.get(ENV_IMAGE_RATE) or credentials.get("RUNPOD_GPU_USD_PER_HOUR")


class RunPodImageAdapter(RunPodComfyAdapter):
    """One image (text to image, or an edit from references) on the RunPod
    image endpoint."""

    provider = "runpod"
    poll_budget_seconds = POLL_BUDGET_SECONDS

    def estimate(self, link, request):
        if not is_paid(link):
            return None
        return pricing.estimate(link, 1, width=request.width, height=request.height)

    def probe(self, link, *, credentials, transport=None, **_):
        creds = dict(credentials, **{ENV_ENDPOINT: image_endpoint(credentials), ENV_API_KEY: image_key(credentials)})
        return super().probe(link, credentials=creds, transport=transport)

    @staticmethod
    def plan(link, request):
        """``(name, template, values, images)`` after every check an image
        must pass before anything is sent; ``ValueError`` names what is wrong."""
        label = describe(link)
        name = link.model
        if name not in ALL_TEMPLATES:
            images._unknown_model(link, list(ALL_TEMPLATES))
        if name not in TEMPLATES.get(request.kind, ()):
            raise ValueError(f"{label} serves {', '.join(k for k, names in TEMPLATES.items() if name in names)}, "
                             f"not {request.kind}")
        if not request.out_dir:
            raise ValueError("GenRequest.out_dir is required: where the image is written")
        template = load_template(name)
        seed = request.seed if request.seed is not None else random.randrange(1, 2**31 - 1)
        values = {"prompt": request.prompt, "negative": request.negative or "", "seed": seed,
                  "width": int(request.width or 1080), "height": int(request.height or 1920)}
        inline = []
        if template.get("ref_slots"):
            refs = list(request.references or ())
            if not refs:
                raise ValueError(f"{label} needs reference images")
            if len(refs) > int(template["ref_slots"]):
                raise ValueError(f"{label} takes {template['ref_slots']} references, got {len(refs)}")
            names = []
            for path in refs:
                if not os.path.isfile(path):
                    raise ValueError(f"{label}: reference {path} not found")
                item = inline_image(path)
                if item["name"] not in {i["name"] for i in inline}:
                    inline.append(item)
                names.append(item["name"])
            values["ref_paths"] = names
        return name, template, values, inline

    def generate(self, link, request, *, credentials, on_log, transport=None, sleep_fn=time.sleep,
                 time_fn=time.monotonic, on_submit=None, **_):
        name, template, values, inline = self.plan(link, request)  # refused here, before any call
        transport = transport or urllib_transport
        endpoint = image_endpoint(credentials)
        headers = auth_headers(image_key(credentials))
        body = {"input": {"workflow": render_template(template, values), "images": inline}}
        # Billed from here on (once a worker takes it), whatever happens next.
        answer = request_json(transport, "POST", endpoint_url(endpoint, "run"), headers=headers, json_body=body,
                              timeout=DEFAULT_TIMEOUT)
        job_id = answer.get("id")
        if not job_id:
            raise RunPodError(f"{describe(link)}: /run answered without a job id: {str(answer)[:200]}")
        status_url = endpoint_url(endpoint, f"status/{job_id}")
        queued = {"request_id": job_id, "status_url": status_url, "response_url": status_url, "endpoint": endpoint}
        on_log(f"   🔁 RunPod: queued {name} ({values['width']}x{values['height']}, seed {values['seed']}"
               f"{', ' + str(len(inline)) + ' reference(s)' if inline else ''}) as job {job_id} on endpoint {endpoint}")
        if on_submit is not None:
            on_submit(dict(queued))
        status = self._poll(link, queued, headers=headers, transport=transport, on_log=on_log, sleep_fn=sleep_fn,
                            time_fn=time_fn)
        return self._finish_image(link, request, credentials, queued, status, name, values, on_log)

    def resume(self, link, request, entry, *, credentials, on_log, transport=None, sleep_fn=time.sleep,
               time_fn=time.monotonic, **_):
        name, _template, values, _inline = self.plan(link, request)
        transport = transport or urllib_transport
        queued = dict(entry.get("request") or {})
        if not queued.get("request_id"):
            raise RunPodError(f"{describe(link)}: the journal holds no job to resume")
        queued.setdefault("endpoint", image_endpoint(credentials))
        headers = auth_headers(image_key(credentials))
        on_log(f"   ↩️ RunPod: following job {queued['request_id']} on endpoint {queued['endpoint']} (not submitted again)")
        status = self._poll(link, queued, headers=headers, transport=transport, on_log=on_log, sleep_fn=sleep_fn,
                            time_fn=time_fn)
        return self._finish_image(link, request, credentials, queued, status, name, values, on_log)

    @staticmethod
    def _image(output: dict):
        """The first image file among the worker's returned files, else None."""
        files = (output or {}).get("images") or []
        stills = [f for f in files if not str(f.get("filename", "")).lower().endswith(".mp4")]
        return (stills or [None])[0]

    def _finish_image(self, link, request, credentials, queued, status, name, values, on_log):
        label = describe(link)
        output = status.get("output") or {}
        item = self._image(output)
        if item is None:
            got = ", ".join(str(f.get("filename")) for f in output.get("images") or []) or "nothing"
            errors = "; ".join(str(e) for e in output.get("errors") or [])
            raise RequestFailed(f"{label}: job {queued['request_id']} finished without an image (got {got}"
                                f"{'; ' + errors if errors else ''}); the template must end in a core SaveImage node")
        if item.get("type") != "base64":
            raise RunPodError(f"{label}: the worker returned a {item.get('type')!r} output; this adapter reads base64 "
                              "(unset BUCKET_ENDPOINT_URL on the endpoint)")
        try:
            data = base64.b64decode(item.get("data") or "")
        except (ValueError, TypeError) as exc:
            raise RunPodError(f"{label}: the image's base64 could not be decoded ({exc})") from exc
        if not data:
            raise RunPodError(f"{label}: {item.get('filename')} came back empty")
        ext = os.path.splitext(str(item.get("filename") or ""))[1].lstrip(".").lower() or "png"
        path = write_output(request.out_dir, images._name(request, link, values["seed"]), data, ext)
        seconds_billed = gpu_seconds(status)
        rate = image_rate(credentials)
        usd = billed_usd(status, {"RUNPOD_GPU_USD_PER_HOUR": rate}) if rate else None
        on_log(f"   💸 RunPod: job {queued['request_id']} took {seconds_billed:.0f} GPU-s"
               f"{f' = ${usd:.3f} at ${rate}/h' if usd is not None else ' (set RUNPOD_IMAGE_GPU_USD_PER_HOUR to price it)'}")
        return GenResult(provider="runpod", model=link.model, paths=(path,), seed=values["seed"], meta={
            "template": name, "job_id": queued["request_id"], "endpoint": queued["endpoint"],
            "output": item.get("filename"), "gpu_seconds": seconds_billed, "billed_usd": usd,
            "execution_ms": status.get("executionTime"), "delay_ms": status.get("delayTime"),
            "worker_id": status.get("workerId"), "width": values["width"], "height": values["height"],
            "references": len(request.references or ()),
        })


RUNPOD_IMAGES = RunPodImageAdapter()
register_adapter(IMAGE, "runpod", RUNPOD_IMAGES)
register_adapter(IMAGE_EDIT, "runpod", RUNPOD_IMAGES)
