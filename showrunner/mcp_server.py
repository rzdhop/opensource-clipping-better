"""The showrunner MCP server (plan 36 stage 2): the chat drives the stage-1 toolbox.

    .venv/bin/python -m showrunner.mcp_server            # streamable HTTP on SHOWRUNNER_MCP_HOST:PORT, path /mcp
    .venv/bin/python -m showrunner.mcp_server --stdio    # a local client

Claude is the writer and the director; these tools are the hands: the story folder (``showrunner.store``),
GPU jobs on the showrunner endpoints, the clip check, the voice conversion, the assembly. No tool calls an
LLM and no tool decides anything creative. Every tool that spends money says so in its first line.

Settings (environment, else the repo's ``.env``; never the live server's ``MCP_HOST/PORT/PUBLIC_URL``):
``SHOWRUNNER_MCP_HOST`` (127.0.0.1), ``SHOWRUNNER_MCP_PORT`` (8788), ``SHOWRUNNER_MCP_PUBLIC_URL`` (its own
Funnel port), ``MCP_TOKEN`` (the same secret as the live server), ``SHOWRUNNER_STORIES_DIR`` (``stories/``),
the endpoints ``RUNPOD_SHOWRUNNER_VIDEO_ENDPOINT_ID`` + ``RUNPOD_SHOWRUNNER_VIDEO_KEY`` and
``RUNPOD_IMAGE_ENDPOINT_ID`` + ``RUNPOD_IMAGE_API_KEY``. The live app's ``RUNPOD_COMFY_ENDPOINT_ID`` is never read.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastmcp import FastMCP  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

from showrunner import comfy_templates  # noqa: E402
from showrunner import mcp_auth  # noqa: E402
from showrunner import runpod_client as rp  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PORT = 8788
IMAGE_TASKS = {"t2i", "edit"}           # templates served by the images endpoint; every other one by showrunner-video
# RunPod Serverless list prices per second (check against the invoice); billed = execution time (DEC-316).
RATES = {"video": 0.00053, "images": 1.58 / 3600}

INSTRUCTIONS = """showrunner: the AI Story toolbox (plan 36). You (Claude) are the writer and the director; these
tools are the hands. A story is a folder (stories/<slug>/): 00-brief.md, 01-universe.md (the art, agreed in chat
first), 02-cast/<char>/sheet.md (+ full_body.png, voice_ref.wav), 03-places/, epNN/script.md, shots.json,
takes.json. Rules: every GPU job costs money — say the count and the cost and wait for Rida's go before submitting;
every clip is shown to Rida and approved by Rida before it is used; never a still, never a slowed clip; never name
what is unwanted in a positive prompt (the models ignore the negative prompt)."""


def env_value(name: str, default: str = "", env: dict | None = None) -> str:
    """*name* from the process environment, else a line of the repo's ``.env``, else *default*. An explicit
    *env* (tests) is the only source: the host's ``.env`` never leaks into it."""
    if env is not None:
        return (env.get(name) or "").strip() or default
    try:
        return rp.api_key(name=name)
    except rp.RunPodError:
        return default


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    token: str = ""
    public_url: str = ""
    stories_dir: str = os.path.join(REPO_ROOT, "stories")
    state_dir: str = os.path.join(REPO_ROOT, "outputs")
    endpoints: dict = field(default_factory=dict)   # kind -> endpoint id ("video", "images")
    keys: dict = field(default_factory=dict)        # kind -> RunPod key


def load_settings(env: dict | None = None) -> Settings:
    get = lambda name, default="": env_value(name, default, env)  # noqa: E731
    return Settings(
        host=get("SHOWRUNNER_MCP_HOST", "127.0.0.1"),
        port=int(get("SHOWRUNNER_MCP_PORT", str(DEFAULT_PORT))),
        token=get("MCP_TOKEN").strip(),
        public_url=get("SHOWRUNNER_MCP_PUBLIC_URL").strip().rstrip("/"),
        stories_dir=os.path.abspath(get("SHOWRUNNER_STORIES_DIR", os.path.join(REPO_ROOT, "stories"))),
        endpoints={k: v for k, v in (("video", get("RUNPOD_SHOWRUNNER_VIDEO_ENDPOINT_ID")),
                                     ("images", get("RUNPOD_IMAGE_ENDPOINT_ID"))) if v},
        keys={k: v for k, v in (("video", get("RUNPOD_SHOWRUNNER_VIDEO_KEY") or get("RUNPOD_API_KEY")),
                                ("images", get("RUNPOD_IMAGE_API_KEY") or get("RUNPOD_API_KEY"))) if v},
    )


def template_kind(template_name: str) -> str:
    """Which endpoint serves *template_name*: ``images`` (Flux 2 Klein) or ``video`` (LTX, Chatterbox)."""
    return "images" if comfy_templates.load_template(template_name).get("task") in IMAGE_TASKS else "video"


class Backend:
    """What the tools share. Tests pass ``endpoint_factory`` to fake RunPod."""

    def __init__(self, settings: Settings | None = None, *, endpoint_factory=None):
        self.settings = settings or load_settings()
        self._endpoint_factory = endpoint_factory or (lambda kind, eid, key: rp.Endpoint(eid, key=key))

    def endpoint(self, kind: str) -> rp.Endpoint:
        eid = self.settings.endpoints.get(kind)
        if not eid:
            raise ToolError(f"no {kind} endpoint configured (RUNPOD_SHOWRUNNER_VIDEO_ENDPOINT_ID / RUNPOD_IMAGE_ENDPOINT_ID)")
        return self._endpoint_factory(kind, eid, self.settings.keys.get(kind))


def build_server(backend: Backend | None = None) -> FastMCP:
    backend = backend or Backend()
    s = backend.settings
    auth, provider = mcp_auth.build_auth(token=s.token, public_url=s.public_url,
                                         state_path=os.path.join(s.state_dir, mcp_auth.STATE_REL))
    mcp = FastMCP("showrunner", instructions=INSTRUCTIONS, version="0.1.0", auth=auth)
    if provider is not None:
        mcp_auth.register_login_routes(mcp, provider)

    @mcp.tool
    def runpod_health() -> dict:
        """Free. The showrunner endpoints' workers and queue (idle, running, throttled; jobs waiting). A queue of
        20–35 minutes is normal when the datacenter is short of GPUs, and waiting is not billed."""
        out = {}
        for kind in ("video", "images"):
            try:
                out[kind] = {"endpoint": s.endpoints.get(kind), **backend.endpoint(kind).health()}
            except (ToolError, rp.RunPodError) as exc:
                out[kind] = {"endpoint": s.endpoints.get(kind), "error": str(exc)}
        return out

    @mcp.tool
    def templates_list() -> list:
        """Free. The ComfyUI templates the GPU tools can run: name, task, the endpoint that serves it, the values it
        needs, the files it takes (story paths) and their meaning."""
        rows = []
        for f in sorted(os.listdir(comfy_templates.WORKFLOWS_DIR)):
            if not f.endswith(".json"):
                continue
            t = comfy_templates.load_template(f[:-5])
            rows.append({"name": t["name"], "task": t.get("task"), "endpoint": template_kind(t["name"]),
                         "values": [p for p in t.get("placeholders", []) if p not in (t.get("files") or {})],
                         "defaults": t.get("defaults", {}), "files": t.get("files", {}),
                         "description": t.get("description", "")})
        return rows

    return mcp


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stdio", action="store_true", help="serve over stdio (a local client)")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()
    backend = Backend()
    server = build_server(backend)
    if args.stdio:
        server.run(transport="stdio")
    else:
        server.run(transport="http", host=args.host or backend.settings.host, port=args.port or backend.settings.port)


if __name__ == "__main__":
    main()
