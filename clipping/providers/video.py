"""Hosted image-to-video adapters (AI Story phase 6, stage 3; spec 8.1, 8.7).

The image adapters' shape (``estimate``, ``probe``, ``generate``, ``resume``),
registered for ``(VIDEO, "fal")`` and ``(VIDEO, "gemini")``:

* :class:`FalVideoAdapter` -- seedance 1 pro fast, LTX-2.3 fast, LTX-2.5 fast
  and kling 2.5 turbo std on fal's queue. It is ``images.FalAdapter`` with per-model inputs,
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
A-100..A-103, re-read on 2026-09-30; LTX-2.5 fast's (plan 23 stage C1,
A-151) are fal's OpenAPI schema of 2026-10-04.
"""

from __future__ import annotations

import time
import urllib.parse

from . import images, pricing, prompt_limits
from .errors import ProviderError
from .gencache import RequestFailed
from .generation import VIDEO, GenResult, register_adapter
from .registry import describe
from .transport import (
    DEFAULT_TIMEOUT, HttpStatusError, data_url, read_b64, request_bytes, request_json, urllib_transport, write_output,
)

# ------------------------------------------------------------ model tables

# The whole-second lengths each hosted link sells. A request for any other
# length is refused before sending; the planner rounds up to one of these.
CLIP_LENGTHS: dict[str, tuple[int, ...]] = {
    "fal/seedance-1-pro-fast": tuple(range(2, 13)),  # "2".."12" (A-100)
    # 12..20 s exist too, at 25 fps and 1080p only (fal schema, 2026-09-30): not offered.
    "fal/ltx-2.3-fast": (6, 8, 10),
    # Plan 23 stage C1: "6".."20" step 2 (fal schema, 2026-10-04); 20 s only at 720p/1080p
    # (the sizes this adapter sends). Its shortest clip is 6 s: a 4 s request is refused.
    "fal/ltx-2.5-fast": (6, 8, 10, 12, 14, 16, 18, 20),
    "fal/kling-2.5-turbo-std": (5, 10),  # A-102
    "gemini/veo-3.1-lite": (4, 6, 8),  # A-103
    # Plan 22 (the native-speech links, reachable by name from a budget
    # profile's ``speech_links``; never in the default chain): 4, 6 or 8 s,
    # 8 s forced at 1080p (ai.google.dev, read 2026-10-04).
    "gemini/veo-3.1-fast": (4, 6, 8),
    "gemini/veo-3.1": (4, 6, 8),
    # DEC-310: the local templates on RunPod Serverless. The lengths are the
    # templates' own ``frame_rule.lengths`` (tests/test_runpod_comfyui.py pins
    # them to the JSON): Wan 2.2 makes 2-5 s, LTX-2 2-4 s at its 121-frame cap.
    "runpod/i2v_wan22_5b": (2, 3, 4, 5),
    "runpod/i2v_wan22_14b_lightning": (2, 3, 4, 5),
    "runpod/i2v_ltx2": (2, 3, 4),
}

# Links kept parseable (an existing .env) and priced, but never sent: no silent swap.
REFUSED_LINKS = {
    "fal/ltx-2-fast": "fal/ltx-2-fast renders 16:9 only; use fal/ltx-2.3-fast",
}

# Whether the clip carries the model's own sound: never, when asked, or always.
AUDIO = {
    "fal/seedance-1-pro-fast": "never",
    "fal/ltx-2.3-fast": "optional",
    "fal/ltx-2.5-fast": "optional",
    "fal/kling-2.5-turbo-std": "never",
    "gemini/veo-3.1-lite": "always",
    "gemini/veo-3.1-fast": "always",
    "gemini/veo-3.1": "always",
    # Plan 22 stage 5: an uploaded clip may carry sound or not (a speaking
    # shot's must: the upload route refuses it otherwise). It has no entry in
    # CLIP_LENGTHS: the plan's lengths are targets (native_speech.SPEECH_LENGTHS),
    # the clip's real length is the shot's.
    "manual/upload": "optional",
    # DEC-310: the templates render a silent clip (the local adapter says has_audio False; i2v_ltx2 keeps
    # no audio node), so no runpod link ever carries the model's own sound.
    "runpod/i2v_wan22_5b": "never",
    "runpod/i2v_wan22_14b_lightning": "never",
    "runpod/i2v_ltx2": "never",
}
# Plan 23 stage B7: the frames each link makes (``GenRequest.extra["aspect"]``,
# absent = 9:16). Veo makes 9:16 and 16:9 (ai.google.dev; Higgsfield's Veo 3.1
# guide likewise), LTX-2.3/2.5 fast sell ``aspect_ratio`` 9:16 or 16:9 (fal's
# schemas), seedance 1 pro fast also sells 1:1 (A-100's enum), kling has no
# aspect field -- its clip follows the keyframe, so it makes whatever frame the
# keyframe is. The human's own upload follows the default platform (Google
# Flow: 9:16 and 16:9). A local ComfyUI workflow renders 9:16 only in v1.
PORTRAIT = "9:16"
ASPECTS = {
    "fal/seedance-1-pro-fast": ("9:16", "16:9", "1:1"),
    "fal/ltx-2.3-fast": ("9:16", "16:9"),
    "fal/ltx-2.5-fast": ("9:16", "16:9"),
    "fal/kling-2.5-turbo-std": ("9:16", "16:9", "1:1"),
    "gemini/veo-3.1-lite": ("9:16", "16:9"),
    "gemini/veo-3.1-fast": ("9:16", "16:9"),
    "gemini/veo-3.1": ("9:16", "16:9"),
    "manual/upload": ("9:16", "16:9"),
    # DEC-310: the same 9:16-only workflow templates as local/comfyui, run on RunPod.
    "runpod/i2v_wan22_5b": ("9:16",),
    "runpod/i2v_wan22_14b_lightning": ("9:16",),
    "runpod/i2v_ltx2": ("9:16",),
}
LOCAL_ASPECTS = ("9:16",)
# Why a link cannot make a frame, by the kind of link.
_ASPECT_REASONS = {
    "gemini": "Veo makes 9:16 and 16:9 clips only",
    "ltx": "LTX makes 9:16 and 16:9 clips only",
    "manual": "Google Flow (the shot brief's platform) makes 9:16 and 16:9 clips only",
    "local": "a local ComfyUI workflow renders 9:16 clips only (v1)",
    "runpod": "the ComfyUI workflow templates render 9:16 clips only (v1), on RunPod as locally",
}

# Only seedance takes a seed; kling and LTX (2.3, 2.5) have no field, Veo is "not deterministic".
SEED_HONOURED = frozenset({"fal/seedance-1-pro-fast", "runpod/i2v_wan22_5b", "runpod/i2v_wan22_14b_lightning",
                           "runpod/i2v_ltx2"})

FAL_VIDEO_POLL_BUDGET_SECONDS = 600.0

# LTX-2.5 fast (plan 23 stage C1): the sizes sold (1440p and 2160p exist on fal but are not
# priced here, and 20 s is not offered there), and the type of ``duration`` (fal's schema
# lists the strings "6".."20"; never "auto").
LTX25_RESOLUTIONS = ("720p", "1080p")
LTX25_DURATION_TYPE = str  # or int

GEMINI_VIDEO_MODELS = {"veo-3.1-lite": "veo-3.1-lite-generate-preview",
                       "veo-3.1-fast": "veo-3.1-fast-generate-preview",
                       "veo-3.1": "veo-3.1-generate-preview"}
# The speaking links (plan 22): their body says the size the request asks, an
# adult-only person policy and what must never be drawn or burned in. Veo 3.1
# lite keeps the body it always sent, byte for byte (RC-N1: stored clips' keys).
VEO_SPEECH_MODELS = frozenset({"veo-3.1-fast", "veo-3.1"})
VEO_RESOLUTIONS = ("720p", "1080p")
VEO_PERSON_GENERATION = "allow_adult"
VEO_NEGATIVE_PROMPT = "subtitles, captions, on-screen text, watermark"
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
    refusal = aspect_refusal(label, _aspect(request))
    if refusal:
        raise ValueError(refusal)
    return seconds


def _label(link) -> str:
    return link if isinstance(link, str) else describe(link)


def supports_aspect(link, aspect) -> bool:
    """Whether a clip of *link* (a ``Link`` or its label) can be made at the
    frame *aspect* (``"9:16"``, ``"16:9"``, ``"1:1"``; None is 9:16). Every
    link makes 9:16; a link this module has no table for makes 9:16 only."""
    if aspect in (None, PORTRAIT):
        return True
    label = _label(link)
    if label.startswith("local/"):
        return aspect in LOCAL_ASPECTS
    return aspect in ASPECTS.get(label, (PORTRAIT,))


def aspect_refusal(link, aspect) -> str | None:
    """Why *link* cannot make *aspect* clips, in one sentence, or None when
    it can (:func:`supports_aspect`)."""
    if supports_aspect(link, aspect):
        return None
    label = _label(link)
    provider, _, model = label.partition("/")
    kind = "ltx" if model.startswith("ltx") and provider != "runpod" else provider
    reason = _ASPECT_REASONS.get(kind, f"{label} makes 9:16 clips only")
    return f"{label} cannot make {aspect} clips: {reason}"


def _aspect(request):
    """The frame *request* asks (``GenRequest.extra["aspect"]``, plan 23
    stage B7), or None: 9:16, the frame every clip was bought at before."""
    aspect = (request.extra or {}).get("aspect")
    return None if aspect in (None, PORTRAIT) else aspect


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
            return {**base, "duration": str(seconds), "resolution": resolution,
                    "aspect_ratio": _aspect(request) or PORTRAIT, "seed": seed}
        if link.model == "ltx-2.3-fast":
            # 1080p is its smallest size; audio only when the clip is to keep it.
            return {**base, "duration": seconds, "aspect_ratio": _aspect(request) or PORTRAIT, "resolution": "1080p",
                    "generate_audio": bool(request.native_audio)}
        if link.model == "ltx-2.5-fast":
            # 720p unless the request asks 1080p (1440p+ is not sold or priced). The server's
            # generate_audio default is true, so it is always sent: a silent clip is not paid for
            # as a sounding one. No seed, negative_prompt, fps, camera_motion or end_image_url.
            resolution = _resolution(request) or "720p"
            if resolution not in LTX25_RESOLUTIONS:
                raise ValueError(f"{describe(link)}: clips of {' or '.join(LTX25_RESOLUTIONS)}, not {resolution!r}")
            return {**base, "duration": LTX25_DURATION_TYPE(seconds), "aspect_ratio": _aspect(request) or PORTRAIT,
                    "resolution": resolution, "generate_audio": bool(request.native_audio)}
        if link.model == "kling-2.5-turbo-std":
            # No aspect or size field: the output follows the keyframe's frame (A-102; a 16:9 or 1:1
            # story's keyframe is made at its frame, plan 23 stage B7); cfg_scale keeps its default.
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

    def _body(self, request, seconds, link=None) -> dict:
        mime, data = read_b64(request.references[0])
        if link is not None and link.model in VEO_SPEECH_MODELS:
            return self._speech_body(link, request, seconds, mime, data)
        # No negativePrompt (undocumented for 3.1 lite, A-103) and no seed (not deterministic);
        # a 9:16 request's body is the one it always was (RC-N1), a 16:9 one says so (B7).
        return {
            "instances": [{"prompt": request.prompt, "image": veo_image(mime, data)}],
            "parameters": {"aspectRatio": _aspect(request) or PORTRAIT, "resolution": "720p",
                           "durationSeconds": VEO_DURATION_TYPE(seconds)},
        }

    def _speech_body(self, link, request, seconds, mime, data) -> dict:
        """A speaking link's body (plan 22): the size the request asks
        (``extra["resolution"]``, else 720p), adults only, and the negative
        prompt that keeps captions and watermarks out of the clip."""
        resolution = _resolution(request) or "720p"
        if resolution not in VEO_RESOLUTIONS:
            raise ValueError(f"{describe(link)}: clips of {' or '.join(VEO_RESOLUTIONS)}, not {resolution!r}")
        return {
            "instances": [{"prompt": request.prompt, "image": veo_image(mime, data)}],
            "parameters": {"aspectRatio": _aspect(request) or PORTRAIT, "resolution": resolution,
                           "durationSeconds": VEO_DURATION_TYPE(seconds),
                           "personGeneration": VEO_PERSON_GENERATION, "negativePrompt": VEO_NEGATIVE_PROMPT},
        }

    def generate(self, link, request, *, credentials, on_log, transport=None,
                 sleep_fn=time.sleep, time_fn=time.monotonic, on_submit=None, **_):
        seconds = clip_seconds(link, request)
        transport = transport or urllib_transport
        model = GEMINI_VIDEO_MODELS[link.model]
        headers = {"x-goog-api-key": credentials["GEMINI_PAID_API_KEY"]}
        body = self._body(request, seconds, link)
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


# ------------------------------------------------------------ key check

# fal's Platform API (not the queue): reading a model's price needs the key
# and buys nothing, so it answers "is this key good, and is this model there".
FAL_PLATFORM = "https://api.fal.ai/v1"
KEY_CHECK_TIMEOUT = 20.0


def _fal_price(answer, endpoint) -> dict | None:
    """The ``{unit_price, unit, currency}`` fal's pricing answer gives
    *endpoint*, or None when it lists none (read leniently: only these three
    fields are used)."""
    prices = answer.get("prices") if isinstance(answer, dict) else None
    rows = [row for row in prices or [] if isinstance(row, dict)]
    row = next((row for row in rows if row.get("endpoint_id") == endpoint), rows[0] if rows else None)
    if row is None or row.get("unit_price") is None:
        return None
    return {"unit_price": row.get("unit_price"), "unit": row.get("unit"), "currency": row.get("currency") or "USD"}


def check_key(link, credentials, *, transport=None) -> dict:
    """Ask *link*'s provider whether its key is accepted and its model is
    there, **without generating anything** (free; RC-V8 holds)::

        {"status": "ok" | "bad_key" | "no_model" | "unreachable" | "failed",
         "text": sentence, "endpoint": model id asked about, "price": {...} | None,
         "prompt_limit": {...} | None}

    fal: ``GET /v1/models/pricing?endpoint_id=`` on the Platform API with
    ``Authorization: Key`` (the price comes back with it). Gemini Veo:
    ``GET /v1beta/models/{model}`` with ``x-goog-api-key`` -- it proves the
    key and the model, not the billing, which only a paid request shows.
    Through *transport* (default ``urllib_transport``: the key never follows
    a redirect off its origin). Never raises for an answer; ``ValueError``
    for a link this check does not know.

    A fal link that answered also has its endpoint's public OpenAPI schema
    read for the prompt limit it publishes (``prompt_limits.read_fal_schema``,
    no key sent): ``prompt_limit`` is that read and the sentence ends with
    it ("· prompt ≤ 2500 chars (fal's schema)", or the limit the app keeps
    when fal publishes none). Storing it is the caller's
    (``prompt_limits.record_live``). Veo publishes no schema: None."""
    transport = transport or urllib_transport
    result = _check_key(link, credentials, transport=transport)
    result["prompt_limit"] = None
    if link.provider == "fal" and result["status"] != "unreachable":
        read = prompt_limits.read_fal_schema(result["endpoint"], transport=transport)
        result["prompt_limit"] = read
        result["text"] = f"{result['text'].rstrip('.')} · {_prompt_limit_text(link, read)}"
    return result


def _prompt_limit_text(link, read) -> str:
    """The key check's words for a schema *read*: what fal publishes, or the
    limit the app keeps instead (the table's when fal publishes none; the
    one in force, perhaps an earlier live read, when the schema failed)."""
    if read["status"] == prompt_limits.PUBLISHED:
        return read["text"]
    kept = prompt_limits.table_limit(link) if read["status"] == prompt_limits.NOT_PUBLISHED \
        else prompt_limits.limit_for(link)
    if kept is None:
        return read["text"]
    origin = "published" if kept.verified else "its own estimate"
    return f"{read['text']}; the app keeps {prompt_limits.describe_limit(kept)} ({origin})"


def _check_key(link, credentials, *, transport) -> dict:
    """:func:`check_key` without the schema read."""
    label = describe(link)
    if link.provider == "fal":
        endpoint = images.FAL_APPS.get(link.model)
        if endpoint is None:
            raise ValueError(f"{label}: no fal endpoint known for this model")
        url = f"{FAL_PLATFORM}/models/pricing?endpoint_id={urllib.parse.quote(endpoint, safe='/')}"
        headers = {"Authorization": f"Key {credentials['FAL_KEY']}"}
        key_name = "FAL_KEY"
    elif link.provider == "gemini" and link.model in GEMINI_VIDEO_MODELS:
        endpoint = GEMINI_VIDEO_MODELS[link.model]
        url = f"{images.GEMINI_BASE}/models/{endpoint}"
        headers = {"x-goog-api-key": credentials["GEMINI_PAID_API_KEY"]}
        key_name = "GEMINI_PAID_API_KEY"
    elif link.provider == "runpod":
        # DEC-310: the endpoint's /health (free): 401 is a refused key, 404 an endpoint id that is not
        # this account's; the answer counts the workers (ready / idle / throttled), never a job.
        from . import runpod_comfyui  # noqa: PLC0415 - sibling module; imported here to keep video.py's imports flat
        endpoint = credentials["RUNPOD_COMFY_ENDPOINT_ID"]
        url = runpod_comfyui.endpoint_url(endpoint, "health")
        headers = runpod_comfyui.auth_headers(credentials["RUNPOD_API_KEY"])
        key_name = "RUNPOD_API_KEY"
    else:
        raise ValueError(f"{label}: no key check for this video link")
    result = {"status": "failed", "text": "", "endpoint": endpoint, "price": None}
    try:
        answer = request_json(transport, "GET", url, headers=headers, timeout=KEY_CHECK_TIMEOUT)
    except HttpStatusError as exc:
        # Gemini answers a bad key with 400 (reason API_KEY_INVALID, message "API key not valid").
        refused = any(marker in (exc.detail or "") for marker in ("API_KEY_INVALID", "API key not valid"))
        detail = (exc.detail or "")[:160]
        if exc.status_code in (401, 403) or refused:
            result.update(status="bad_key", text=f"{label}: the provider refused {key_name} (HTTP {exc.status_code}"
                                                 f"{': ' + detail if detail else ''}). Check the key in Settings.")
        elif exc.status_code == 404:
            result.update(status="no_model", text=f"{label}: the key was accepted but {endpoint} was not found "
                                                  f"(HTTP 404): the model may be retired or renamed.")
        else:
            result["text"] = f"{label}: HTTP {exc.status_code} while checking ({detail or 'no detail'})."
        return result
    except Exception as exc:  # noqa: BLE001 - any failure to answer is reported, never raised
        result.update(status="unreachable", text=f"{label}: the provider could not be reached "
                                                 f"({type(exc).__name__}: {str(exc)[:160]}).")
        return result
    if link.provider == "fal":
        price = _fal_price(answer, endpoint)
        if price is None:
            result.update(status="no_model", text=f"{label}: fal accepted FAL_KEY but lists no price for "
                                                  f"{endpoint}: the model may be retired or renamed.")
            return result
        unit = f" per {price['unit']}" if price.get("unit") else ""
        result.update(status="ok", price=price, text=(f"{label}: fal accepted FAL_KEY; {endpoint} is live at "
                                                      f"{price['unit_price']} {price['currency']}{unit}."))
        return result
    if link.provider == "runpod":
        workers = answer.get("workers") if isinstance(answer, dict) else None
        counts = ", ".join(f"{k} {workers[k]}" for k in ("ready", "idle", "running", "throttled")
                           if isinstance(workers, dict) and k in workers)
        result.update(status="ok", text=(f"{label}: RunPod accepted RUNPOD_API_KEY and endpoint {endpoint} answers"
                                         f"{' (workers ' + counts + ')' if counts else ''}; whether the models are "
                                         "on its volume shows only on a request."))
        return result
    methods = answer.get("supportedGenerationMethods") if isinstance(answer, dict) else None
    result.update(status="ok", text=(f"{label}: Google accepted GEMINI_PAID_API_KEY and lists {endpoint}"
                                     f"{' (' + ', '.join(methods) + ')' if methods else ''}; whether the key's "
                                     "project is billed shows only on a paid request."))
    return result


# ------------------------------------------------------------ registration

FAL_VIDEO = FalVideoAdapter()
VEO = GeminiVeoAdapter()

register_adapter(VIDEO, "fal", FAL_VIDEO)
register_adapter(VIDEO, "gemini", VEO)
