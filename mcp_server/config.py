"""Settings of the MCP server, read from the environment (and the repo's ``.env``).

Two RunPod endpoints share one API key: the video one the app already uses
(``RUNPOD_COMFY_ENDPOINT_ID``) and an image one (``RUNPOD_IMAGE_ENDPOINT_ID``)
so a worker never swaps Wan/LTX weights for FLUX weights. When the image
endpoint is not set, image jobs go to the video endpoint (slower, but works).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

ENV_API_KEY = "RUNPOD_API_KEY"
ENV_IMAGE_KEY = "RUNPOD_IMAGE_API_KEY"
ENV_VIDEO_ENDPOINT = "RUNPOD_COMFY_ENDPOINT_ID"
ENV_IMAGE_ENDPOINT = "RUNPOD_IMAGE_ENDPOINT_ID"
ENV_VIDEO_RATE = "RUNPOD_GPU_USD_PER_HOUR"
ENV_IMAGE_RATE = "RUNPOD_IMAGE_GPU_USD_PER_HOUR"
# Audio (text to speech, plan 31): its own endpoint when one runs the TTS worker
# image alone; empty = the image endpoint (then the video one) serves audio too.
ENV_AUDIO_ENDPOINT = "RUNPOD_AUDIO_ENDPOINT_ID"
ENV_AUDIO_KEY = "RUNPOD_AUDIO_API_KEY"
ENV_AUDIO_RATE = "RUNPOD_AUDIO_GPU_USD_PER_HOUR"
ENV_OUTPUTS = "RZDHOP_OUTPUTS_DIR"
ENV_HOST = "MCP_HOST"
ENV_PORT = "MCP_PORT"
ENV_TOKEN = "MCP_TOKEN"
ENV_PUBLIC_URL = "MCP_PUBLIC_URL"

KINDS = ("image", "video", "audio")
# Which configured endpoint serves a kind when it has none of its own, in order.
FALLBACK = {"image": ("video",), "audio": ("image", "video")}


@dataclass
class Settings:
    api_key: str = ""
    keys: dict = field(default_factory=dict)           # kind -> its own API key, when one is set
    endpoints: dict = field(default_factory=dict)      # kind -> endpoint id
    rates: dict = field(default_factory=dict)          # kind -> USD per GPU hour (float) or None
    outputs_dir: str = ""
    host: str = "127.0.0.1"
    port: int = 8787
    token: str = ""
    public_url: str = ""

    def serving_kind(self, kind: str) -> str:
        """The kind whose endpoint serves *kind*: itself when configured, else
        the first configured fallback (images: video; audio: image, then video)."""
        if kind not in KINDS:
            raise ValueError(f"unknown job kind {kind!r}; one of {', '.join(KINDS)}")
        for candidate in (kind, *FALLBACK.get(kind, ())):
            if self.endpoints.get(candidate):
                return candidate
        return "video"

    def endpoint(self, kind: str) -> str:
        """The endpoint id that serves *kind*; images fall back to the video
        endpoint, audio to the image one then the video one. ``RuntimeError``
        names the missing variable."""
        endpoint = self.endpoints.get(self.serving_kind(kind))
        if not endpoint:
            names = {"image": f" or {ENV_IMAGE_ENDPOINT}", "audio": f", {ENV_IMAGE_ENDPOINT} or {ENV_AUDIO_ENDPOINT}"}
            raise RuntimeError(f"no RunPod endpoint configured: set {ENV_VIDEO_ENDPOINT}{names.get(kind, '')} in .env")
        return endpoint

    def key(self, kind: str) -> str:
        """The key that opens the endpoint serving *kind*: that endpoint's own
        key, else the account key. A kind served by a fallback endpoint (audio
        on the image endpoint) needs the fallback's key: found on the first live
        voice line of plan 32, where the image endpoint has a key of its own."""
        serving = self.serving_kind(kind) if kind in KINDS else kind
        return self.keys.get(serving) or self.api_key

    def rate(self, kind: str):
        """The USD-per-hour of the endpoint that serves *kind* (a kind without
        an endpoint of its own is billed at its fallback's rate)."""
        return self.rates.get(self.serving_kind(kind) if kind in KINDS else kind)

    def kind_of_endpoint(self, endpoint_id: str) -> str:
        for kind, value in self.endpoints.items():
            if value == endpoint_id:
                return kind
        return "video"


def _rate(value):
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def load_settings(env=None) -> Settings:
    """Settings from *env* (``os.environ`` by default), after the repo's
    ``.env`` has been loaded into it (python-dotenv, when installed)."""
    if env is None:
        try:
            from dotenv import load_dotenv

            load_dotenv(os.path.join(ROOT, ".env"))
        except ImportError:
            pass
        env = os.environ
    outputs = env.get(ENV_OUTPUTS) or os.path.join(ROOT, "outputs")
    return Settings(
        api_key=(env.get(ENV_API_KEY) or "").strip(),
        keys={k: v for k, v in (("image", (env.get(ENV_IMAGE_KEY) or "").strip()),
                                ("audio", (env.get(ENV_AUDIO_KEY) or "").strip())) if v},
        endpoints={k: v for k, v in (("video", (env.get(ENV_VIDEO_ENDPOINT) or "").strip()),
                                     ("image", (env.get(ENV_IMAGE_ENDPOINT) or "").strip()),
                                     ("audio", (env.get(ENV_AUDIO_ENDPOINT) or "").strip())) if v},
        rates={"video": _rate(env.get(ENV_VIDEO_RATE)), "image": _rate(env.get(ENV_IMAGE_RATE)),
               "audio": _rate(env.get(ENV_AUDIO_RATE))},
        outputs_dir=os.path.abspath(outputs),
        host=env.get(ENV_HOST) or "127.0.0.1",
        port=int(env.get(ENV_PORT) or 8787),
        token=(env.get(ENV_TOKEN) or "").strip(),
        public_url=(env.get(ENV_PUBLIC_URL) or "").strip().rstrip("/"),
    )
