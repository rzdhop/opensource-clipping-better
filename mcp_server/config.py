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
ENV_OUTPUTS = "RZDHOP_OUTPUTS_DIR"
ENV_HOST = "MCP_HOST"
ENV_PORT = "MCP_PORT"
ENV_TOKEN = "MCP_TOKEN"
ENV_PUBLIC_URL = "MCP_PUBLIC_URL"

KINDS = ("image", "video")


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

    def endpoint(self, kind: str) -> str:
        """The endpoint id that serves *kind*; images fall back to the video
        endpoint. ``RuntimeError`` names the missing variable."""
        if kind not in KINDS:
            raise ValueError(f"unknown job kind {kind!r}; one of {', '.join(KINDS)}")
        endpoint = self.endpoints.get(kind) or self.endpoints.get("video")
        if not endpoint:
            raise RuntimeError(f"no RunPod endpoint configured: set {ENV_VIDEO_ENDPOINT}"
                               f"{' or ' + ENV_IMAGE_ENDPOINT if kind == 'image' else ''} in .env")
        return endpoint

    def key(self, kind: str) -> str:
        """The key that opens *kind*'s endpoint: its own, else the account key."""
        return self.keys.get(kind) or self.api_key

    def rate(self, kind: str):
        rate = self.rates.get(kind)
        if rate is None and kind == "image" and not self.endpoints.get("image"):
            rate = self.rates.get("video")
        return rate

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
        keys={k: v for k, v in (("image", (env.get(ENV_IMAGE_KEY) or "").strip()),) if v},
        endpoints={k: v for k, v in (("video", (env.get(ENV_VIDEO_ENDPOINT) or "").strip()),
                                     ("image", (env.get(ENV_IMAGE_ENDPOINT) or "").strip())) if v},
        rates={"video": _rate(env.get(ENV_VIDEO_RATE)), "image": _rate(env.get(ENV_IMAGE_RATE))},
        outputs_dir=os.path.abspath(outputs),
        host=env.get(ENV_HOST) or "127.0.0.1",
        port=int(env.get(ENV_PORT) or 8787),
        token=(env.get(ENV_TOKEN) or "").strip(),
        public_url=(env.get(ENV_PUBLIC_URL) or "").strip().rstrip("/"),
    )
