"""The lipsync post-process of a made clip (AI Story, DEC-258).

One adapter, registered for ``(LIPSYNC, "fal")``:
:class:`FalLipsyncAdapter` -- Kling LipSync audio-to-video on fal's queue
(``fal-ai/kling-video/lipsync/audio-to-video``): a clip and a dialogue track
in, the same clip with its characters' lips moving to the track out. It is
``images.FalAdapter`` with two inputs uploaded to fal's storage first (a
6 MB clip as a ``data:`` URL is too big for the queue's JSON input), a
``video.url`` answer and a clip's poll budget; poll, journal and resume are
the image path's, unchanged (DEC-151/152): the submit is billed, so a
journaled request is resumed, never submitted again.

**The uploads** (fal storage, read from the live probe of 2026-10-03):
``POST {FAL_STORAGE_INITIATE}`` with ``{content_type, file_name}`` and the
``Authorization: Key`` header answers ``{upload_url, file_url}``; the bytes
are ``PUT`` to ``upload_url`` with their content type and **no key** (a
signed URL on another host); ``file_url`` is the input. Both go through the
injected transport. An upload bills nothing, so it is sent through the
counting transport's uncounted twin (``generation._Sent.free``): a failed
upload is proven unbilled, only the queue's submit counts (DEC-153).

**The model's bounds** (fal's page, read 2026-10-03): a video of 2 to 10 s
and 720 to 1920 px a side; an audio of 2 to 60 s and at most 5 MB, mp3 or
wav. $0.014 per 5 s of input video, rounded up to the next 5 s
(:func:`billed_seconds`; ``pricing.PRICES["fal/kling-lipsync"]`` holds the
price per second). The orchestrator's probe: a 7 s seedance clip at
720x1280 with a 5.4 s line completed in 72 s, h264 + aac 720x1280.

A request is refused with ``ValueError`` before any call when it could not
be keyed and journaled or not bought as asked: no clip length, a length
outside 2-10 s, not exactly one clip, no dialogue track, a track over 5 MB,
no ``out_dir``. Stdlib only, REST through ``transport.py`` (DEC-012).
"""

from __future__ import annotations

import math
import os
import time

from . import images, pricing
from .errors import ProviderError
from .gencache import RequestFailed
from .generation import LIPSYNC, GenResult, register_adapter
from .registry import describe
from .transport import DEFAULT_TIMEOUT, request_bytes, request_json, urllib_transport, write_output

FAL_LIPSYNC_APPS = {"kling-lipsync": "fal-ai/kling-video/lipsync/audio-to-video"}

FAL_STORAGE_INITIATE = "https://rest.alpha.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3"
UPLOAD_TIMEOUT = 300.0

# fal bills the input video per started 5 s.
BILLING_STEP_S = 5
VIDEO_SECONDS = (2, 10)
MAX_AUDIO_BYTES = 5 * 1024 * 1024

FAL_LIPSYNC_POLL_BUDGET_SECONDS = 600.0

_CONTENT_TYPES = {".mp4": "video/mp4", ".wav": "audio/wav", ".mp3": "audio/mpeg"}


def billed_seconds(clip_s) -> int:
    """The seconds fal bills a *clip_s* clip: rounded up to the next 5 s."""
    if clip_s is None or float(clip_s) <= 0:
        raise ValueError(f"a lipsync is priced by its clip's length, not {clip_s!r}")
    return int(math.ceil(float(clip_s) / BILLING_STEP_S - 1e-9) * BILLING_STEP_S)


def estimate_for(link, clip_s):
    """``pricing.estimate`` of a lipsync of a *clip_s* clip on *link*."""
    return pricing.estimate(link, billed_seconds(clip_s))


def content_type(path) -> str:
    return _CONTENT_TYPES.get(os.path.splitext(str(path))[1].lower(), "application/octet-stream")


def check_request(link, request) -> int:
    """The clip's length after every check a paid lipsync must pass before
    anything is sent; ``ValueError`` for a request that is refused. A model
    this module does not know is a 404, as for the image adapters."""
    label = describe(link)
    if link.model not in FAL_LIPSYNC_APPS:
        images._unknown_model(link, FAL_LIPSYNC_APPS)
    if request.duration_s is None:
        raise ValueError(f"{label}: a lipsync needs duration_s, its clip's length; without it the request has no "
                         "key and could not be journaled")
    seconds = float(request.duration_s)
    low, high = VIDEO_SECONDS
    if not low <= seconds <= high:
        raise ValueError(f"{label} takes clips of {low} to {high} s, not {request.duration_s!r}")
    count = len(request.references or ())
    if count != 1:
        raise ValueError(f"{label}: a lipsync moves the lips of exactly one clip, got {count}")
    audio = (request.extra or {}).get("audio")
    if not audio:
        raise ValueError(f"{label}: a lipsync needs its dialogue track (extra['audio'])")
    for path in (request.references[0], audio):
        if not os.path.isfile(path):
            raise ValueError(f"{label}: {os.path.basename(str(path))} is not a file")
    size = os.path.getsize(audio)
    if size > MAX_AUDIO_BYTES:
        raise ValueError(f"{label} takes a dialogue track of at most {MAX_AUDIO_BYTES} bytes, not {size}")
    if not request.out_dir:
        raise ValueError(f"{label}: GenRequest.out_dir is required: where the lip-synced clip is written")
    return int(seconds) if seconds.is_integer() else seconds


def upload(path, *, headers, transport) -> str:
    """*path* uploaded to fal's storage; its public ``file_url``. The key
    rides on the initiate call only, never on the PUT to the signed URL."""
    ctype = content_type(path)
    started = request_json(transport, "POST", FAL_STORAGE_INITIATE, headers=headers,
                           json_body={"content_type": ctype, "file_name": os.path.basename(str(path))},
                           timeout=DEFAULT_TIMEOUT)
    upload_url, file_url = started.get("upload_url"), started.get("file_url")
    if not upload_url or not file_url:
        raise ProviderError(f"fal storage answered without an upload_url and a file_url: {str(started)[:200]}")
    with open(path, "rb") as handle:
        data = handle.read()
    request_bytes(transport, "PUT", upload_url, headers={"Content-Type": ctype}, body=data, timeout=UPLOAD_TIMEOUT)
    return file_url


class FalLipsyncAdapter(images.FalAdapter):
    """fal's queue for a lipsync. Poll, journal and resume are
    ``images.FalAdapter``'s; the uploads, the inputs, the answer and the
    budget are a lipsync's."""

    poll_budget_seconds = FAL_LIPSYNC_POLL_BUDGET_SECONDS

    def estimate(self, link, request):
        return estimate_for(link, request.duration_s)

    def _inputs(self, link, request, seed):
        raise ProviderError(f"{describe(link)}: a lipsync's inputs are uploaded first (generate builds them)")

    def generate(self, link, request, *, credentials, on_log, transport=None,
                 sleep_fn=time.sleep, time_fn=time.monotonic, on_submit=None, **_):
        check_request(link, request)  # refused here, before the key is read or anything is sent
        transport = transport or urllib_transport
        app = FAL_LIPSYNC_APPS[link.model]
        headers = {"Authorization": f"Key {credentials['FAL_KEY']}"}
        # Free: the uploads never bill, so they go through the uncounted twin
        # of the runner's counting transport when it offers one (DEC-153).
        free = getattr(transport, "free", None) or transport
        video_url = upload(request.references[0], headers=headers, transport=free)
        audio_url = upload(request.extra["audio"], headers=headers, transport=free)
        on_log(f"   ⬆️ {describe(link)}: clip and dialogue track uploaded to fal storage")
        submitted = request_json(transport, "POST", f"{images.FAL_QUEUE}/{app}", headers=headers,
                                 json_body={"video_url": video_url, "audio_url": audio_url}, timeout=DEFAULT_TIMEOUT)
        request_id = submitted.get("request_id")
        if not request_id:
            raise ProviderError(f"{describe(link)}: the queue answered without a request_id: {submitted}")
        queued = {
            "request_id": request_id,
            "status_url": submitted.get("status_url") or f"{images.FAL_QUEUE}/{app}/requests/{request_id}/status",
            "response_url": submitted.get("response_url") or f"{images.FAL_QUEUE}/{app}/requests/{request_id}",
        }
        if on_submit is not None:
            # Journaled between the queue's answer and the first poll (DEC-151).
            on_submit(dict(queued))
        self._poll(link, queued, headers=headers, transport=transport, on_log=on_log, sleep_fn=sleep_fn,
                   time_fn=time_fn)
        return self._fetch(link, request, queued, request.seed, headers=headers, transport=transport,
                           on_submit=on_submit)

    def _fetch(self, link, request, queued, seed, *, headers, transport, on_submit=None):
        """Read the completed answer, journal its clip URL (a failed download
        resumes from there), then download it."""
        result = request_json(transport, "GET", queued["response_url"], headers=headers, timeout=DEFAULT_TIMEOUT)
        clip = result.get("video") if isinstance(result.get("video"), dict) else {}
        if not clip.get("url"):
            raise RequestFailed(f"{describe(link)}: the answer carried no video: {str(result)[:200]}")
        output = {"url": clip["url"], "content_type": clip.get("content_type"), "seed": seed}
        if on_submit is not None:
            on_submit({**queued, "output": output})
        return self._download(link, request, queued, output, seed, transport=transport)

    def _download(self, link, request, queued, output, seed, *, transport):
        # The answer's URL is fal's CDN: no key rides along.
        data = request_bytes(transport, "GET", output["url"], headers={}, timeout=DEFAULT_TIMEOUT)
        if not data:
            raise ProviderError(f"{describe(link)}: the lip-synced clip downloaded empty")
        path = write_output(images._out_dir(request), images._name(request, link, seed), data, "mp4")
        return GenResult(provider="fal", model=link.model, paths=(path,), seed=seed,
                         meta={"request_id": queued["request_id"], "seed_honoured": False})


# ------------------------------------------------------------ registration

FAL_LIPSYNC = FalLipsyncAdapter()

register_adapter(LIPSYNC, "fal", FAL_LIPSYNC)
