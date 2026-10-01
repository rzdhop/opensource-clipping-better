"""Hosted image-to-video adapters (AI Story phase 6, stage 3; spec 8.1, 8.7).

The image adapters' shape (``estimate``, ``probe``, ``generate``, ``resume``),
registered for ``(VIDEO, "fal")`` and ``(VIDEO, "gemini")``:

* :class:`FalVideoAdapter` -- seedance 1 pro fast, LTX-2.3 fast and kling 2.5
  turbo std on fal's queue. It is ``images.FalAdapter`` with per-model inputs,
  a ``video.url`` answer and a longer poll budget; submit, poll, journal and
  resume are the image path's, unchanged (DEC-151/152).
* :class:`GeminiVeoAdapter` -- Veo 3.1 lite over REST ``predictLongRunning``,
  billed on ``GEMINI_PAID_API_KEY`` only (RC-V4). The operation name is
  journaled the moment Google answers the POST; ``resume`` polls that
  operation and never posts again.

Every clip is paid. A request is refused with ``ValueError`` before any call
when it could not be keyed and journaled (no clip length, no seed) or not
bought as asked: not exactly one keyframe, a length the model does not sell,
no ``out_dir``, or ``fal/ltx-2-fast`` (16:9 only, A-101). A poll budget spent
is a plain error, so the runner keeps the request ``submitted`` for the next
run; an answer that settles it without a clip is :class:`RequestFailed`.

:data:`CLIP_LENGTHS` is the one table of sellable lengths; the planner
(``clipping.aistory.video_plan``) imports it.

Nothing here strips audio: a tier-2 request asks for none where it is optional
(LTX), Veo always returns some and the renderer discards it at tier 2.
``GenResult.meta`` records ``has_audio`` and ``seed_honoured``.

Stdlib only, REST through ``transport.py`` (DEC-012). The model facts are
A-100..A-103, re-read on 2026-09-30.
"""

from __future__ import annotations

import time
import urllib.parse

from . import images, pricing
from .errors import ProviderError
from .gencache import RequestFailed
from .generation import VIDEO, GenResult, register_adapter
from .registry import describe
from .transport import (
    DEFAULT_TIMEOUT, data_url, read_b64, request_bytes, request_json, urllib_transport, write_output,
)

# ------------------------------------------------------------ model tables

# The whole-second lengths each hosted link sells. A request for any other
# length is refused before sending; the planner rounds up to one of these.
CLIP_LENGTHS: dict[str, tuple[int, ...]] = {
    "fal/seedance-1-pro-fast": tuple(range(2, 13)),  # "2".."12" (A-100)
    # 12..20 s exist too, at 25 fps and 1080p only (fal schema, 2026-09-30): not offered.
    "fal/ltx-2.3-fast": (6, 8, 10),
    "fal/kling-2.5-turbo-std": (5, 10),  # A-102
    "gemini/veo-3.1-lite": (4, 6, 8),  # A-103
}

# Links kept parseable (an existing .env) and priced, but never sent: no silent swap.
REFUSED_LINKS = {
    "fal/ltx-2-fast": "fal/ltx-2-fast renders 16:9 only; use fal/ltx-2.3-fast",
}

# Whether the clip carries the model's own sound: never, when asked, or always.
AUDIO = {
    "fal/seedance-1-pro-fast": "never",
    "fal/ltx-2.3-fast": "optional",
    "fal/kling-2.5-turbo-std": "never",
    "gemini/veo-3.1-lite": "always",
}
# Only seedance takes a seed; kling and LTX have no field, Veo is "not deterministic".
SEED_HONOURED = frozenset({"fal/seedance-1-pro-fast"})

FAL_VIDEO_POLL_BUDGET_SECONDS = 600.0

GEMINI_VIDEO_MODELS = {"veo-3.1-lite": "veo-3.1-lite-generate-preview"}
GEMINI_API_HOST = "generativelanguage.googleapis.com"
VEO_POLL_INTERVAL_SECONDS = 10.0
VEO_POLL_BUDGET_SECONDS = 600.0

# A-103 is unconfirmed until the live Veo shot; each is one line to flip.
# The image as the google-genai SDK sends it to the Gemini API; ai.google.dev's
# REST examples write reference images as "inlineData" {mimeType, data} instead.
VEO_IMAGE_SHAPE = "bytesBase64Encoded"  # or "inlineData"
# The SDK sends an int; ai.google.dev's parameter table lists "4", "6", "8" quoted.
VEO_DURATION_TYPE = int  # or str


def veo_image(mime: str, data: str) -> dict:
    """The keyframe as ``instances[0].image`` (:data:`VEO_IMAGE_SHAPE`)."""
    if VEO_IMAGE_SHAPE == "inlineData":
        return {"inlineData": {"mimeType": mime, "data": data}}
    return {"bytesBase64Encoded": data, "mimeType": mime}


# ------------------------------------------------------------------ helpers

def _whole(value):
    return int(value) if isinstance(value, float) and value.is_integer() else value


def clip_seconds(link, request) -> int:
    """The clip's length as sold, after every check a paid clip must pass
    before anything is sent. ``ValueError`` for a request that is refused; a
    model this module does not know is a 404, as for the image adapters."""
    label = describe(link)
    if label in REFUSED_LINKS:
        raise ValueError(REFUSED_LINKS[label])
    lengths = CLIP_LENGTHS.get(label)
    if lengths is None:
        images._unknown_model(link, [spec.split("/", 1)[1] for spec in CLIP_LENGTHS
                                     if spec.startswith(f"{link.provider}/")])
    if request.duration_s is None:
        raise ValueError(f"{label}: a clip needs duration_s, its length as bought; without it the request "
                         "has no key and could not be journaled")
    if request.seed is None:
        raise ValueError(f"{label}: a clip needs a seed (it keys the request, sent or not); without it the "
                         "request could not be journaled")
    count = len(request.references or ())
    if count != 1:
        raise ValueError(f"{label}: a clip is made from exactly one keyframe, got {count}")
    seconds = _whole(request.duration_s)
    if seconds not in lengths:
        raise ValueError(f"{label} sells clips of {', '.join(str(n) for n in lengths)} s, "
                         f"not {request.duration_s!r}")
    if not request.out_dir:
        raise ValueError(f"{label}: GenRequest.out_dir is required: where the clip is written")
    return seconds


def _resolution(request):
    """The clip size *request* asks (``GenRequest.extra["resolution"]``, phase
    7 stage 4), or None: the link's own default."""
    return (request.extra or {}).get("resolution")


def _estimate(link, request):
    """Seconds as bought times the link's price per second (at the size the
    request asks, ``pricing.price_key``)."""
    if request.duration_s is None:
        raise ValueError(f"{describe(link)}: a clip is priced by its length; the request has no duration_s")
    return pricing.estimate(link, _whole(request.duration_s), resolution=_resolution(request))


def _meta(link, request) -> dict:
    label = describe(link)
    audio = AUDIO.get(label, "never")
    return {
        "has_audio": audio == "always" or (audio == "optional" and bool(request.native_audio)),
        "seed_honoured": label in SEED_HONOURED,
    }


def _write_clip(link, request, seed, data: bytes) -> str:
    if not data:
        raise ProviderError(f"{describe(link)}: the clip downloaded empty")
    return write_output(images._out_dir(request), images._name(request, link, seed), data, "mp4")


# --------------------------------------------------------------------- fal

class FalVideoAdapter(images.FalAdapter):
    """fal's queue for a clip. Submit, poll, journal and resume are
    ``images.FalAdapter``'s; the inputs, the answer and the budget are a clip's."""

    poll_budget_seconds = FAL_VIDEO_POLL_BUDGET_SECONDS

    def estimate(self, link, request):
        return _estimate(link, request)

    def _inputs(self, link, request, seed):
        seconds = clip_seconds(link, request)
        base = {"prompt": request.prompt, "image_url": data_url(request.references[0])}
        if link.model == "seedance-1-pro-fast":
            # 720p explicitly unless the request asks 1080p (a story's switch, phase 7
            # stage 4): the endpoint's default is 1080p, 2.2x the price (A-100).
            resolution = _resolution(request) or "720p"
            if resolution not in ("720p", "1080p"):
                raise ValueError(f"{describe(link)}: clips of 720p or 1080p, not {resolution!r}")
            return {**base, "duration": str(seconds), "resolution": resolution, "aspect_ratio": "9:16", "seed": seed}
        if link.model == "ltx-2.3-fast":
            # 1080p is its smallest size; audio only when the clip is to keep it.
            return {**base, "duration": seconds, "aspect_ratio": "9:16", "resolution": "1080p",
                    "generate_audio": bool(request.native_audio)}
        if link.model == "kling-2.5-turbo-std":
            # No aspect or size field: the output follows the 9:16 keyframe (A-102); cfg_scale keeps its default.
            inputs = {**base, "duration": str(seconds)}
            if request.negative:
                inputs["negative_prompt"] = request.negative
            return inputs
        raise ProviderError(f"{describe(link)}: not a video model of this adapter")

    def generate(self, link, request, *, credentials, on_log, transport=None, **kwargs):
        clip_seconds(link, request)  # refused here, before the key is read or anything is sent
        return super().generate(link, request, credentials=credentials, on_log=on_log, transport=transport,
                                **kwargs)

    def _fetch(self, link, request, queued, seed, *, headers, transport, on_submit=None):
        """Read the completed answer, journal its clip URL (a failed download
        resumes from there), then download it."""
        result = request_json(transport, "GET", queued["response_url"], headers=headers, timeout=DEFAULT_TIMEOUT)
        clip = result.get("video") if isinstance(result.get("video"), dict) else {}
        if not clip.get("url"):
            raise RequestFailed(f"{describe(link)}: the answer carried no video: {str(result)[:200]}")
        output = {"url": clip["url"], "content_type": clip.get("content_type"), "seed": result.get("seed", seed)}
        if on_submit is not None:
            on_submit({**queued, "output": output})
        return self._download(link, request, queued, output, seed, transport=transport)

    def _download(self, link, request, queued, output, seed, *, transport):
        data = request_bytes(transport, "GET", output["url"], headers={}, timeout=DEFAULT_TIMEOUT)
        seed = output.get("seed", seed)
        path = _write_clip(link, request, seed, data)
        return GenResult(provider="fal", model=link.model, paths=(path,), seed=seed,
                         meta={"request_id": queued["request_id"], **_meta(link, request)})


# ------------------------------------------------------------------- veo

class GeminiVeoAdapter:
    """Veo over REST: POST ``predictLongRunning``, journal the operation, poll
    it until ``done``, download the clip with the key."""

    provider = "gemini"
    # Every request goes out through the injected transport, so a failure raised
    # before its first call provably sent nothing (DEC-153).
    speaks_through_transport = True
    poll_interval_seconds = VEO_POLL_INTERVAL_SECONDS
    poll_budget_seconds = VEO_POLL_BUDGET_SECONDS

    def estimate(self, link, request):
        return _estimate(link, request)

    def probe(self, link, *, credentials, **_):
        # Not probed: the real request is the probe, and it costs money.
        return True, "key set; not probed (a request is the probe)"

    def _body(self, request, seconds) -> dict:
        mime, data = read_b64(request.references[0])
        # No negativePrompt (undocumented for 3.1 lite, A-103) and no seed (not deterministic).
        return {
            "instances": [{"prompt": request.prompt, "image": veo_image(mime, data)}],
            "parameters": {"aspectRatio": "9:16", "resolution": "720p", "durationSeconds": VEO_DURATION_TYPE(seconds)},
        }

    def generate(self, link, request, *, credentials, on_log, transport=None,
                 sleep_fn=time.sleep, time_fn=time.monotonic, on_submit=None, **_):
        seconds = clip_seconds(link, request)
        transport = transport or urllib_transport
        model = GEMINI_VIDEO_MODELS[link.model]
        headers = {"x-goog-api-key": credentials["GEMINI_PAID_API_KEY"]}
        body = self._body(request, seconds)
        # Billed from here on, whatever happens next.
        answer = request_json(transport, "POST", f"{images.GEMINI_BASE}/models/{model}:predictLongRunning",
                              headers=headers, json_body=body, timeout=DEFAULT_TIMEOUT)
        name = answer.get("name")
        if not name:
            raise ProviderError(f"{describe(link)}: predictLongRunning answered without an operation: {str(answer)[:200]}")
        url = f"{images.GEMINI_BASE}/{name}"
        queued = {"request_id": name, "status_url": url, "response_url": url}
        if on_submit is not None:
            # Journaled between Google's answer and the first poll (DEC-151).
            on_submit(dict(queued))
        operation = self._poll(link, queued, headers=headers, transport=transport, on_log=on_log,
                               sleep_fn=sleep_fn, time_fn=time_fn)
        return self._fetch(link, request, queued, operation, request.seed, headers=headers, transport=transport,
                           on_submit=on_submit)

    def resume(self, link, request, entry, *, credentials, on_log, transport=None,
               sleep_fn=time.sleep, time_fn=time.monotonic, on_submit=None, **_):
        """Finish the operation a journal *entry* holds: poll it, or download
        its clip when the journal already has the URI. Never a new POST (DEC-152)."""
        transport = transport or urllib_transport
        queued = dict(entry.get("request") or {})
        output = queued.pop("output", None)
        if not queued.get("request_id"):
            raise ProviderError(f"{describe(link)}: the journal holds no operation to resume")
        headers = {"x-goog-api-key": credentials["GEMINI_PAID_API_KEY"]}
        seed = entry.get("seed") if entry.get("seed") is not None else request.seed
        if output and output.get("url"):
            return self._download(link, request, queued, output, seed, headers=headers, transport=transport)
        operation = self._poll(link, queued, headers=headers, transport=transport, on_log=on_log,
                               sleep_fn=sleep_fn, time_fn=time_fn)
        return self._fetch(link, request, queued, operation, seed, headers=headers, transport=transport,
                           on_submit=on_submit)

    def _poll(self, link, queued, *, headers, transport, on_log, sleep_fn, time_fn) -> dict:
        """Wait, then ask, until the operation is ``done``. :class:`RequestFailed`
        when it ends in an error; a plain error past the poll budget (the
        request stays journaled for the next run)."""
        label = describe(link)
        name = queued["request_id"]
        url = f"{images.GEMINI_BASE}/{name}"  # rebuilt from the name: the key goes to Google's host only
        deadline = time_fn() + self.poll_budget_seconds
        polls = 0
        while True:
            sleep_fn(self.poll_interval_seconds)
            operation = request_json(transport, "GET", url, headers=headers, timeout=DEFAULT_TIMEOUT)
            polls += 1
            if operation.get("done"):
                error = operation.get("error")
                if error:
                    message = error.get("message") if isinstance(error, dict) else None
                    raise RequestFailed(f"{label}: operation {name} failed: {message or error}")
                return operation
            if polls % 6 == 0:
                on_log(f"   ⏳ {label}: generating ({polls * self.poll_interval_seconds:.0f}s)")
            if time_fn() >= deadline:
                raise ProviderError(f"{label}: operation {name} still running after {self.poll_budget_seconds:.0f}s")

    def _fetch(self, link, request, queued, operation, seed, *, headers, transport, on_submit=None):
        response = (operation.get("response") or {}).get("generateVideoResponse") or {}
        samples = response.get("generatedSamples") or []
        first = samples[0] if samples and isinstance(samples[0], dict) else {}
        uri = (first.get("video") or {}).get("uri")
        if not uri:
            reasons = "; ".join(str(r) for r in response.get("raiMediaFilteredReasons") or [])
            raise RequestFailed(f"{describe(link)}: operation {queued['request_id']} finished without a video: "
                                f"{reasons or str(operation)[:200]}")
        output = {"url": uri}
        if on_submit is not None:
            on_submit({**queued, "output": output})
        return self._download(link, request, queued, output, seed, headers=headers, transport=transport)

    def _download(self, link, request, queued, output, seed, *, headers, transport):
        uri = output["url"]
        # The key rides along only to the API's own host.
        sent = headers if urllib.parse.urlsplit(uri).hostname == GEMINI_API_HOST else {}
        data = request_bytes(transport, "GET", uri, headers=sent, timeout=DEFAULT_TIMEOUT)
        path = _write_clip(link, request, seed, data)
        return GenResult(provider="gemini", model=link.model, paths=(path,), seed=seed,
                         meta={"operation": queued["request_id"], **_meta(link, request)})


# ------------------------------------------------------------ registration

FAL_VIDEO = FalVideoAdapter()
VEO = GeminiVeoAdapter()

register_adapter(VIDEO, "fal", FAL_VIDEO)
register_adapter(VIDEO, "gemini", VEO)
