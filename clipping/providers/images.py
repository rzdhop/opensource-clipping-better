"""Image adapters: text-to-image and image-edit-with-references (spec 8.1, 8.7).

One small class per vendor, all with the same shape (``estimate``, ``probe``,
``generate``), registered per (kind, provider) with ``generation``. They speak
through the injected transport (``transport.py``) and write the image where the
request says. No SDK is imported at module scope; the OpenAI one is imported
inside the call that needs it (DEC-012).

Gating is the chain runner's job, not the adapters': ``run_generation_chain``
refuses a paid link without ``allow_paid`` and a budget verdict and asks the
limiter before a free one, so an adapter here is only ever called for a link
that may run (DEC-097). The model ids come from spec section 8.7 and are
verified live only for the keyed providers (A-034).

With a generation cache the runner also passes ``on_submit``: fal reports the
queued request through it, and ``FalAdapter.resume`` finishes a request an
earlier attempt submitted (DEC-151/152). Without one, nothing is passed.
"""

from __future__ import annotations

import base64
import random
import time
import urllib.parse

from . import generation, pricing
from .errors import ProviderError
from .gencache import RequestFailed
from .generation import IMAGE, IMAGE_EDIT, GenResult, register_adapter
from .registry import describe
from .transport import (
    DEFAULT_TIMEOUT, HttpStatusError, data_url, read_b64, request_bytes, request_json,
    urllib_transport, write_output,
)

# ---------------------------------------------------------- model id tables

CLOUDFLARE_MODELS = {"flux-1-schnell": "@cf/black-forest-labs/flux-1-schnell"}
GEMINI_MODELS = {
    "nano-banana-2-lite": "gemini-3.1-flash-lite-image",
    "nano-banana-2": "gemini-3.1-flash-image",
}
FAL_APPS = {
    "flux-schnell": "fal-ai/flux/schnell",
    "seedream-4-edit": "fal-ai/bytedance/seedream/v4/edit",
    "seedream-4.5": "fal-ai/bytedance/seedream/v4.5/text-to-image",
    "seedream-4.5-edit": "fal-ai/bytedance/seedream/v4.5/edit",
    "flux-kontext-pro": "fal-ai/flux-pro/kontext",
    # Video ids: their adapter is video.FalVideoAdapter (phase 6). ltx-2-fast stays
    # listed for the ledger only; the adapter refuses it (16:9 only, A-101).
    "seedance-1-pro-fast": "fal-ai/bytedance/seedance/v1/pro/fast/image-to-video",
    "ltx-2-fast": "fal-ai/ltxv-2/image-to-video/fast",
    "ltx-2.3-fast": "fal-ai/ltx-2.3/image-to-video/fast",
    "ltx-2.5-fast": "fal-ai/ltx-2.5/image-to-video/fast",
    "kling-2.5-turbo-std": "fal-ai/kling-video/v2.5-turbo/standard/image-to-video",
}
OPENAI_MODELS = {"gpt-image-2-low": ("gpt-image-2", "low")}

CLOUDFLARE_BASE = "https://api.cloudflare.com/client/v4"
POLLINATIONS_BASE = "https://image.pollinations.ai"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
FAL_QUEUE = "https://queue.fal.run"

FAL_POLL_INTERVAL_SECONDS = 2.0
FAL_POLL_BUDGET_SECONDS = 300.0

_RATIOS = ("1:1", "9:16", "16:9", "3:4", "4:3", "2:3", "3:2", "4:5", "5:4", "21:9")

# Gemini's ``generationConfig.imageConfig.imageSize`` (phase 7, A-113/A-121):
# the 1K tier the price table's nano-banana rows are read at.
GEMINI_IMAGE_SIZE = "1K"

# fal seedream v4.5, edit and text-to-image alike (schema read 2026-10-01,
# A-111; the text-to-image endpoint's bounds read 2026-10-01, DEC-235): a
# custom ``image_size`` must hold at least 2560x1440 pixels and at most 4096
# a side; a request below that is scaled up keeping its ratio (720x1280 ->
# 1440x2560, an exact 9:16), and the edit endpoint takes at most 10
# reference images.
SEEDREAM45_MIN_PIXELS = 2560 * 1440
SEEDREAM45_MAX_SIDE = 4096
SEEDREAM45_MAX_REFERENCES = 10


# ------------------------------------------------------------------ helpers

def _seed(request) -> int:
    return request.seed if request.seed is not None else random.randrange(1, 2**31 - 1)


def _name(request, link, seed) -> str:
    name = (request.extra or {}).get("name")
    return name or f"{link.provider}_{link.model}_{seed}".replace("/", "_")


def _out_dir(request) -> str:
    if not request.out_dir:
        raise ValueError("GenRequest.out_dir is required: where the image is written")
    return request.out_dir


def _aspect_ratio(width, height) -> str:
    want = (width or 1080) / (height or 1920)
    best = min(_RATIOS, key=lambda r: abs(int(r.split(":")[0]) / int(r.split(":")[1]) - want))
    return best


def _seedream45_size(width, height) -> dict:
    """``{width, height}`` for seedream v4.5: *width* x *height* (the request's
    size, 9:16 when unset) scaled up, ratio kept, to the model's smallest
    custom size; each side a multiple of 16 and at most 4096."""
    width, height = (width or 1080), (height or 1920)
    factor = max(1.0, (SEEDREAM45_MIN_PIXELS / (width * height)) ** 0.5)
    sides = []
    for side in (width, height):
        scaled = side * factor
        rounded = int(-(-round(scaled, 6) // 16) * 16)
        sides.append(min(SEEDREAM45_MAX_SIDE, rounded))
    return {"width": sides[0], "height": sides[1]}


def _unknown_model(link, table):
    """Raised as a 404 so the runner treats it as "model not available" (swap or move on)."""
    raise HttpStatusError(404, describe(link), f"model {link.model} not found in this adapter's table ({', '.join(table)})")


def _ext_for(mime: str, default="png") -> str:
    mime = (mime or "").lower()
    if "jpeg" in mime or "jpg" in mime:
        return "jpg"
    if "webp" in mime:
        return "webp"
    if "png" in mime:
        return "png"
    return default


class _Adapter:
    provider = ""
    # Every request goes out through the injected transport, so a failure raised
    # before its first call provably sent nothing (DEC-153).
    speaks_through_transport = True

    def estimate(self, link, request):
        if not generation.is_paid(link):
            return None
        return pricing.estimate(link, 1, width=request.width, height=request.height)

    def probe(self, link, *, credentials, **_):
        # Hosted image providers are not probed: the real request is the probe,
        # and a paid one would cost money.
        return True, "key set; not probed (a request is the probe)"


# --------------------------------------------------------------- cloudflare

class CloudflareAdapter(_Adapter):
    provider = "cloudflare"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        transport = transport or urllib_transport
        model = CLOUDFLARE_MODELS.get(link.model) or _unknown_model(link, CLOUDFLARE_MODELS)
        account = credentials["CLOUDFLARE_ACCOUNT_ID"]
        token = credentials["CLOUDFLARE_API_TOKEN"]
        seed = _seed(request)
        url = f"{CLOUDFLARE_BASE}/accounts/{account}/ai/run/{model}"
        # The model's schema refuses a seed (HTTP 400): the seed is recorded, not reproducible.
        payload = request_json(
            transport, "POST", url, headers={"Authorization": f"Bearer {token}"},
            json_body={"prompt": request.prompt, "steps": 4}, timeout=DEFAULT_TIMEOUT,
        )
        if not payload.get("success", True):
            messages = "; ".join(str(e.get("message", e)) for e in payload.get("errors", []) if e) or "unsuccessful"
            raise ProviderError(f"{describe(link)}: {messages}")
        image = (payload.get("result") or {}).get("image")
        if not image:
            raise ProviderError(f"{describe(link)}: the answer carried no image")
        path = write_output(_out_dir(request), _name(request, link, seed), base64.b64decode(image), "jpg")
        return GenResult(provider="cloudflare", model=link.model, paths=(path,), seed=seed,
                         meta={"size": "native (the model ignores width/height)", "seed_honoured": False})


# ------------------------------------------------------------- pollinations

class PollinationsAdapter(_Adapter):
    provider = "pollinations"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        transport = transport or urllib_transport
        seed = _seed(request)
        query = urllib.parse.urlencode({
            "model": link.model, "width": request.width, "height": request.height,
            "seed": seed, "nologo": "true",
        })
        url = f"{POLLINATIONS_BASE}/prompt/{urllib.parse.quote(request.prompt, safe='')}?{query}"
        headers = {}
        key = credentials.get("POLLINATIONS_API_KEY")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        data = request_bytes(transport, "GET", url, headers=headers, timeout=DEFAULT_TIMEOUT)
        if not data:
            raise ProviderError(f"{describe(link)}: the answer carried no image")
        ext = "png" if data[:4] == b"\x89PNG" else "jpg"
        path = write_output(_out_dir(request), _name(request, link, seed), data, ext)
        return GenResult(provider="pollinations", model=link.model, paths=(path,), seed=seed)


# ------------------------------------------------------------------ gemini

class GeminiImageAdapter(_Adapter):
    provider = "gemini"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        transport = transport or urllib_transport
        model = GEMINI_MODELS.get(link.model) or _unknown_model(link, GEMINI_MODELS)
        # The link's own variable: GEMINI_PAID_API_KEY for nano-banana
        # (DEC-222), never GOOGLE_API_KEY.
        key = credentials[generation.env_keys_for(link)[0]]
        seed = _seed(request)
        parts = [{"text": request.prompt}]
        for path in request.references or ():
            mime, text = read_b64(path)
            parts.append({"inline_data": {"mime_type": mime, "data": text}})
        body = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": _aspect_ratio(request.width, request.height),
                                "imageSize": GEMINI_IMAGE_SIZE},
            },
        }
        url = f"{GEMINI_BASE}/models/{model}:generateContent"
        payload = request_json(transport, "POST", url, headers={"x-goog-api-key": key}, json_body=body,
                               timeout=DEFAULT_TIMEOUT)
        candidates = payload.get("candidates") or []
        texts, image = [], None
        for candidate in candidates:
            for part in (candidate.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data") and image is None:
                    image = inline
                elif part.get("text"):
                    texts.append(part["text"])
        if image is None:
            reason = (candidates[0].get("finishReason") if candidates else None) \
                or (payload.get("promptFeedback") or {}).get("blockReason") or "unknown"
            raise ProviderError(
                f"{describe(link)}: the answer carried no image (finishReason {reason}; text: {' '.join(texts)[:160]!r})"
            )
        mime = image.get("mimeType") or image.get("mime_type") or "image/png"
        path = write_output(_out_dir(request), _name(request, link, seed), base64.b64decode(image["data"]), _ext_for(mime))
        return GenResult(provider="gemini", model=link.model, paths=(path,), seed=seed,
                         meta={"mime": mime, "text": " ".join(texts)[:200], "seed_honoured": False})


# --------------------------------------------------------------------- fal

class FalAdapter(_Adapter):
    provider = "fal"
    # How long ``_poll`` waits for one request; a video subclass waits longer.
    poll_budget_seconds = FAL_POLL_BUDGET_SECONDS

    def _inputs(self, link, request, seed):
        base = {"prompt": request.prompt, "num_images": 1, "seed": seed}
        if link.model == "flux-schnell":
            return {**base, "image_size": {"width": request.width, "height": request.height}}
        if link.model == "seedream-4-edit":
            if not request.references:
                raise ValueError(f"{describe(link)} needs at least one reference image")
            return {**base, "image_urls": [data_url(p) for p in request.references],
                    "image_size": {"width": request.width, "height": request.height}}
        if link.model == "seedream-4.5-edit":
            # No negative prompt: the model has no such field (A-111).
            if not request.references:
                raise ValueError(f"{describe(link)} needs at least one reference image")
            return {**base, "image_urls": [data_url(p) for p in request.references[:SEEDREAM45_MAX_REFERENCES]],
                    "image_size": _seedream45_size(request.width, request.height)}
        if link.model == "seedream-4.5":
            # Text-to-image sibling of the edit model: no image_urls, no
            # negative prompt (A-111), the same size bounds (stage 2c,
            # DEC-235).
            return {**base, "image_size": _seedream45_size(request.width, request.height)}
        if link.model == "flux-kontext-pro":
            if not request.references:
                raise ValueError(f"{describe(link)} needs a reference image")
            return {**base, "image_url": data_url(request.references[0]),
                    "aspect_ratio": _aspect_ratio(request.width, request.height)}
        raise ProviderError(f"{describe(link)}: not an image model of this adapter (video adapters arrive in phase 6)")

    def _submit(self, link, request, seed, *, app, headers, transport) -> dict:
        """POST the job to the queue: ``{request_id, status_url, response_url}``.
        fal bills it from here on, whatever happens next (A-071)."""
        submitted = request_json(transport, "POST", f"{FAL_QUEUE}/{app}", headers=headers,
                                 json_body=self._inputs(link, request, seed), timeout=DEFAULT_TIMEOUT)
        request_id = submitted.get("request_id")
        if not request_id:
            raise ProviderError(f"{describe(link)}: the queue answered without a request_id: {submitted}")
        return {
            "request_id": request_id,
            "status_url": submitted.get("status_url") or f"{FAL_QUEUE}/{app}/requests/{request_id}/status",
            "response_url": submitted.get("response_url") or f"{FAL_QUEUE}/{app}/requests/{request_id}",
        }

    def _poll(self, link, queued, *, headers, transport, on_log, sleep_fn, time_fn) -> None:
        """Wait, then ask, until the request is COMPLETED. :class:`RequestFailed`
        when fal settles it otherwise; a plain error past the poll budget."""
        label = describe(link)
        request_id = queued["request_id"]
        deadline = time_fn() + self.poll_budget_seconds
        polls = 0
        while True:
            sleep_fn(FAL_POLL_INTERVAL_SECONDS)
            status = request_json(transport, "GET", queued["status_url"], headers=headers, timeout=DEFAULT_TIMEOUT)
            state = str(status.get("status") or "").upper()
            polls += 1
            if state == "COMPLETED":
                return
            if state in ("FAILED", "ERROR", "CANCELLED"):
                raise RequestFailed(f"{label}: request {request_id} {state.lower()}: {status.get('error') or status}")
            if polls % 5 == 0:
                position = status.get("queue_position")
                on_log(f"   ⏳ {label}: {state.lower() or 'waiting'}" + (f", queue position {position}" if position is not None else ""))
            if time_fn() >= deadline:
                raise ProviderError(f"{label}: request {request_id} still {state or 'pending'} after {self.poll_budget_seconds:.0f}s")

    def _fetch(self, link, request, queued, seed, *, headers, transport, on_submit=None):
        """Read the completed answer, report its image URL (so a failed download
        can be resumed), then download it."""
        result = request_json(transport, "GET", queued["response_url"], headers=headers, timeout=DEFAULT_TIMEOUT)
        images = result.get("images") or ([result["image"]] if isinstance(result.get("image"), dict) else [])
        if not images:
            raise RequestFailed(f"{describe(link)}: the answer carried no image: {str(result)[:200]}")
        first = images[0]
        output = {"url": first["url"], "content_type": first.get("content_type"), "width": first.get("width"),
                  "height": first.get("height"), "seed": result.get("seed", seed)}
        if on_submit is not None:
            on_submit({**queued, "output": output})
        return self._download(link, request, queued, output, seed, transport=transport)

    def _download(self, link, request, queued, output, seed, *, transport):
        data = request_bytes(transport, "GET", output["url"], headers={}, timeout=DEFAULT_TIMEOUT)
        ext = _ext_for(output.get("content_type") or "", default=("jpg" if output["url"].lower().endswith((".jpg", ".jpeg")) else "png"))
        path = write_output(_out_dir(request), _name(request, link, seed), data, ext)
        return GenResult(provider="fal", model=link.model, paths=(path,), seed=output["seed"],
                         meta={"request_id": queued["request_id"], "width": output.get("width"), "height": output.get("height")})

    def generate(self, link, request, *, credentials, on_log, transport=None,
                 sleep_fn=time.sleep, time_fn=time.monotonic, on_submit=None, **_):
        transport = transport or urllib_transport
        app = FAL_APPS.get(link.model) or _unknown_model(link, FAL_APPS)
        headers = {"Authorization": f"Key {credentials['FAL_KEY']}"}
        seed = _seed(request)
        queued = self._submit(link, request, seed, app=app, headers=headers, transport=transport)
        if on_submit is not None:
            # Journaled between the queue's answer and the first poll (DEC-151).
            on_submit(dict(queued))
        self._poll(link, queued, headers=headers, transport=transport, on_log=on_log, sleep_fn=sleep_fn,
                   time_fn=time_fn)
        return self._fetch(link, request, queued, seed, headers=headers, transport=transport, on_submit=on_submit)

    def resume(self, link, request, entry, *, credentials, on_log, transport=None,
               sleep_fn=time.sleep, time_fn=time.monotonic, on_submit=None, **_):
        """Finish the request a journal *entry* holds: poll and fetch the same
        ``request_id``, or download its image when the journal already has the
        URL. Never a new submit (DEC-152)."""
        transport = transport or urllib_transport
        queued = dict(entry.get("request") or {})
        output = queued.pop("output", None)
        if not all(queued.get(name) for name in ("request_id", "status_url", "response_url")):
            raise ProviderError(f"{describe(link)}: the journal holds no request to resume")
        headers = {"Authorization": f"Key {credentials['FAL_KEY']}"}
        seed = entry.get("seed") if entry.get("seed") is not None else request.seed
        if output and output.get("url"):
            return self._download(link, request, queued, output, seed, transport=transport)
        self._poll(link, queued, headers=headers, transport=transport, on_log=on_log, sleep_fn=sleep_fn,
                   time_fn=time_fn)
        return self._fetch(link, request, queued, seed, headers=headers, transport=transport, on_submit=on_submit)


# ------------------------------------------------------------------ openai

def _openai_client(**kwargs):
    from openai import OpenAI  # imported here only: absent from the CI host (DEC-012)

    return OpenAI(**kwargs)


class OpenAIImageAdapter(_Adapter):
    provider = "openai"
    speaks_through_transport = False  # the SDK sends: a failure never proves nothing went out

    def generate(self, link, request, *, credentials, on_log, transport=None, client_factory=None, **_):
        model, quality = OPENAI_MODELS.get(link.model) or _unknown_model(link, OPENAI_MODELS)
        factory = client_factory or _openai_client
        client = factory(api_key=credentials["OPENAI_API_KEY"], max_retries=0, timeout=DEFAULT_TIMEOUT)
        if request.width == request.height:
            size = "1024x1024"
        elif request.height > request.width:
            size = "1024x1536"
        else:
            size = "1536x1024"
        seed = _seed(request)
        if request.kind == IMAGE_EDIT and request.references:
            handles = [open(path, "rb") for path in request.references]
            try:
                answer = client.images.edit(model=model, image=handles, prompt=request.prompt,
                                            size=size, quality=quality, n=1)
            finally:
                for handle in handles:
                    handle.close()
        else:
            answer = client.images.generate(model=model, prompt=request.prompt, size=size, quality=quality, n=1)
        data = getattr(answer, "data", None) or []
        b64 = getattr(data[0], "b64_json", None) if data else None
        if not b64:
            raise ProviderError(f"{describe(link)}: the answer carried no image")
        path = write_output(_out_dir(request), _name(request, link, seed), base64.b64decode(b64), "png")
        return GenResult(provider="openai", model=link.model, paths=(path,), seed=seed,
                         meta={"size": size, "quality": quality, "seed_honoured": False})


# ------------------------------------------------------------ registration

CLOUDFLARE = CloudflareAdapter()
POLLINATIONS = PollinationsAdapter()
GEMINI = GeminiImageAdapter()
FAL = FalAdapter()
OPENAI = OpenAIImageAdapter()

register_adapter(IMAGE, "cloudflare", CLOUDFLARE)
register_adapter(IMAGE, "pollinations", POLLINATIONS)
register_adapter(IMAGE, "gemini", GEMINI)
register_adapter(IMAGE_EDIT, "gemini", GEMINI)
register_adapter(IMAGE, "fal", FAL)
register_adapter(IMAGE_EDIT, "fal", FAL)
register_adapter(IMAGE, "openai", OPENAI)
register_adapter(IMAGE_EDIT, "openai", OPENAI)
