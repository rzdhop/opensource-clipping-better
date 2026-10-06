"""The MCP server: tools over the RunPod jobs (stage 1), the story store
(stage 2), the render pipeline (stage 4).

Tool docstrings are what the model reads; they say what a tool costs and
what it returns. Every tool that spends says so in its first line.
"""

from __future__ import annotations

import argparse
import os
from typing import Optional

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.utilities.types import Image

from . import auth as auth_mod, media, story_tools
from .config import Settings, load_settings
from .runpod_jobs import JobClient, JobError, list_templates

INSTRUCTIONS = """rzdhop story backend. You (Claude) are the writer and director; these tools are the
muscle: RunPod Serverless ComfyUI for images and clips, the story store on disk, the renderer.
Jobs are asynchronous: comfy_submit returns a job id at once; comfy_fetch waits (bounded) and
returns the files, with a thumbnail or a contact sheet you can look at. Every GPU job costs money
(a few cents); say what you are about to generate before submitting a batch."""


class Backend:
    """What the tools share: settings and the job client. Built once at
    startup; tests build their own with a fake transport."""

    def __init__(self, settings: Optional[Settings] = None, *, client: Optional[JobClient] = None,
                 story: Optional[story_tools.StoryBackend] = None):
        self.settings = settings or load_settings()
        self.client = client or JobClient(self.settings)
        self.story = story or story_tools.StoryBackend(self.settings.outputs_dir)


def _public(record: dict) -> dict:
    """A journal record as a tool answer: no graph, no base64, short prompt."""
    keys = ("job_id", "kind", "template", "name", "state", "dest_dir", "outputs", "error", "submitted_at",
            "finished_at", "gpu_seconds", "billed_usd", "seed", "width", "height", "seconds", "frames", "fps",
            "note", "worker_id", "endpoint")
    return {k: record.get(k) for k in keys if k in record}


def _look(path: str, max_px: int):
    """The content blocks that show *path*: an Image (thumbnail or contact
    sheet) and a dict describing it. A file of another type: the dict only."""
    if media.is_image(path):
        data, w, h = media.thumbnail(path, max_px)
        return [Image(data=data, format="jpeg"), {"path": path, "shown_at": f"{w}x{h}",
                                                  "size_bytes": os.path.getsize(path)}]
    if media.is_video(path):
        info = media.probe_video(path)
        sheet = media.contact_sheet(path, probe=info)
        return [Image(data=sheet, format="jpeg"), {"path": path, "contact_sheet": "8 frames, left to right, "
                                                  "top row then bottom row, evenly spaced across the clip", **info}]
    return [{"path": path, "size_bytes": os.path.getsize(path), "note": "not an image or a video"}]


def _blocks(blocks: list):
    """A list of content blocks (an image and its text) as the tool answer;
    a lone dict is answered as itself, so a plain answer is always one JSON
    object rather than a one-element array."""
    return blocks if len(blocks) > 1 else blocks[0]


def build_server(backend: Optional[Backend] = None) -> FastMCP:
    backend = backend or Backend()
    settings = backend.settings
    # MCP_TOKEN opens the bearer door (Claude Code); with MCP_PUBLIC_URL too, the
    # OAuth door for claude.ai's connector, behind a login page asking the same token.
    auth, provider = auth_mod.build_auth(token=settings.token, public_url=settings.public_url,
                                         state_path=os.path.join(settings.outputs_dir, auth_mod.STATE_REL))
    mcp = FastMCP("rzdhop-story", instructions=INSTRUCTIONS, version="0.1.0", auth=auth)
    if provider is not None:
        auth_mod.register_login_routes(mcp, provider)
    client = backend.client

    # ------------------------------------------------------------ RunPod

    @mcp.tool
    def runpod_health(kind: Optional[str] = None) -> dict:
        """Free. The RunPod endpoints' state: workers ready/idle/running/throttled and jobs queued,
        per kind ('image', 'video'); one kind when given. A throttled count means the datacenter is
        short of that GPU; a cold start (30-120 s) follows any job sent while no worker is idle."""
        return client.health(kind)

    @mcp.tool
    def templates_list() -> list:
        """Free. The ComfyUI workflow templates this server can submit: name, task (t2i, edit, i2v, ...),
        the endpoint kind that runs it, the values it takes, its clip lengths (video), the model files
        it needs and whether it was verified live. Use the name as comfy_submit's template."""
        return list_templates()

    @mcp.tool
    def comfy_submit(template: str, prompt: str, negative: str = "", seed: Optional[int] = None,
                     width: Optional[int] = None, height: Optional[int] = None, seconds: Optional[int] = None,
                     image_path: Optional[str] = None, ref_paths: Optional[list[str]] = None,
                     name: Optional[str] = None, dest: Optional[str] = None, note: Optional[str] = None) -> dict:
        """COSTS MONEY (GPU seconds: an image a cent or two, a clip 5-10 cents). Submit one ComfyUI job to
        RunPod and return at once with its job_id (state IN_QUEUE). Several submits in a row run in
        parallel on the endpoint's workers. Arguments: template (templates_list), prompt, negative;
        seed (random when omitted; reuse it to regenerate the same thing); width/height (video: the
        template's frame; image: 832x1216 portrait by default); seconds (video only, one of the
        template's lengths); image_path (the keyframe, for i2v templates); ref_paths (1-4 reference
        images, for edit templates); name (the output file's stem); dest (folder for the outputs,
        relative to the outputs dir, default outputs/mcp/<date>); note (free text kept in the journal).
        Paths are on the server: absolute, or relative to the outputs dir or the repo."""
        try:
            return _public(client.submit(template, prompt=prompt, negative=negative, seed=seed, width=width,
                                         height=height, seconds=seconds, image_path=image_path,
                                         ref_paths=ref_paths, name=name, dest=dest, note=note))
        except JobError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def comfy_status(job_id: str) -> dict:
        """Free. One look at a job: state (IN_QUEUE, IN_PROGRESS, COMPLETED, FAILED, CANCELLED, TIMED_OUT,
        GONE), and once it ended its files (outputs), GPU seconds and cost, or its error. A completed
        job's files are already saved on the server when this returns."""
        try:
            return _public(client.status(job_id))
        except JobError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def comfy_fetch(job_id: str, wait_s: int = 240, max_px: int = media.DEFAULT_MAX_PX):
        """Free. Wait up to wait_s seconds for a job to end, then show its result: for an image its
        thumbnail; for a clip a contact sheet (8 frames across the clip) with duration, size and
        whether it has sound; plus the record (paths, cost). If it is still running after wait_s,
        the record alone comes back (call again). Keep wait_s under ~280 s (the chat's tool timeout);
        a cold clip can take 5 min: fetch twice rather than once with a long wait."""
        try:
            record = client.wait(job_id, timeout_s=min(int(wait_s), 280))
        except JobError as exc:
            raise ToolError(str(exc)) from exc
        blocks = []
        if record["state"] == "COMPLETED":
            for path in record.get("outputs") or []:
                try:
                    blocks.extend(_look(path, max_px))
                except media.MediaError as exc:
                    blocks.append({"path": path, "error": str(exc)})
        blocks.append(_public(record))
        return _blocks(blocks)

    @mcp.tool
    def comfy_cancel(job_id: str) -> dict:
        """Free. Cancel a queued or running job (what already ran is still billed)."""
        try:
            return _public(client.cancel(job_id))
        except JobError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def comfy_jobs(limit: int = 20, refresh: bool = False) -> list:
        """Free. The last jobs this server submitted, newest first; refresh=true asks RunPod about the
        unfinished ones first (and saves the files of those that completed meanwhile)."""
        try:
            return [_public(r) for r in client.recent(limit, refresh=refresh)]
        except JobError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def cost_ledger(since: Optional[str] = None) -> dict:
        """Free. GPU seconds and dollars of the finished jobs (all, and per kind), from an ISO date
        when given (e.g. '2026-10-01'). Dollars need RUNPOD_GPU_USD_PER_HOUR (and the image rate) in .env;
        unpriced_jobs counts the ones without a rate."""
        return client.ledger(since)

    # ------------------------------------------------------------- media

    @mcp.tool
    def view_file(path: str, max_px: int = media.DEFAULT_MAX_PX):
        """Free. Look at a file on the server: an image comes back as a thumbnail (max_px on its longer
        side), a clip as a contact sheet of 8 frames with its duration/size/sound. Paths: absolute, or
        relative to the outputs dir or the repo."""
        try:
            full = client.resolve_path(path)
            return _blocks(_look(full, max_px))
        except (JobError, media.MediaError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def list_files(folder: str = "mcp", limit: int = 100) -> list:
        """Free. The files under a folder of the outputs dir (recursively, newest first): path, size,
        modified time."""
        root = os.path.abspath(os.path.join(backend.settings.outputs_dir, folder))
        if not root.startswith(backend.settings.outputs_dir):
            raise ToolError("folder must be inside the outputs dir")
        rows = []
        for base, _dirs, files in os.walk(root):
            for f in files:
                if f.startswith("."):
                    continue
                p = os.path.join(base, f)
                st = os.stat(p)
                rows.append({"path": os.path.relpath(p, backend.settings.outputs_dir), "size_bytes": st.st_size,
                             "modified": st.st_mtime})
        rows.sort(key=lambda r: r["modified"], reverse=True)
        return rows[:max(1, int(limit))]

    story_tools.register(mcp, backend.story)
    return mcp


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m mcp_server", description="rzdhop story MCP server")
    parser.add_argument("--stdio", action="store_true", help="speak MCP on stdin/stdout (a local client)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    backend = Backend()
    server = build_server(backend)
    if args.stdio:
        server.run(transport="stdio")
        return
    server.run(transport="http", host=args.host or backend.settings.host, port=args.port or backend.settings.port)
