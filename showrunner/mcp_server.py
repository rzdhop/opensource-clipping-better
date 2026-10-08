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
import base64
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastmcp import FastMCP  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402
from fastmcp.utilities.types import Image  # noqa: E402
from mcp.types import BlobResourceContents, EmbeddedResource  # noqa: E402

from showrunner import comfy_templates  # noqa: E402
from showrunner import mcp_auth  # noqa: E402
from showrunner import runpod_client as rp  # noqa: E402
from showrunner import store as st  # noqa: E402
from showrunner import verify  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PORT = 8788
TEXT_EXTS = (".md", ".json", ".jsonl", ".txt", ".ass")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTS = (".mp4", ".mov", ".webm")
DOWNLOAD_MAX_MIB, DOWNLOAD_CEILING_MIB = 25, 50   # base64 adds a third; one JSON message (the live server's rule)
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


# ------------------------------------------------------------------ story helpers

def open_story(settings: Settings, slug: str) -> st.Story:
    """The story *slug* under the stories folder; a slug is one folder name, never a path."""
    if not slug or slug != os.path.basename(slug) or slug.startswith((".", "_")):
        raise ToolError(f"{slug!r} is not a story slug (story_list shows them)")
    try:
        return st.Story.open(os.path.join(settings.stories_dir, slug))
    except st.StoreError as exc:
        raise ToolError(str(exc)) from exc


def story_file(story: st.Story, relpath: str, *, must_exist: bool = True) -> str:
    """The absolute path of *relpath* inside *story*, links followed; never outside the story."""
    try:
        full = story.path(relpath)
    except st.StoreError as exc:
        raise ToolError(str(exc)) from exc
    real = os.path.realpath(full)
    if not (real == os.path.realpath(story.root) or real.startswith(os.path.realpath(story.root) + os.sep)):
        raise ToolError(f"{relpath!r} leaves the story folder")
    if must_exist and not os.path.exists(real):
        raise ToolError(f"no file {relpath} in {story.slug}")
    return real


def thumbnail(path: str, max_px: int = 1024) -> bytes:
    """A JPEG of the image *path*, its long side at most *max_px* (ffmpeg; no Pillow needed)."""
    vf = f"scale='if(gt(iw,ih),min({max_px},iw),-2)':'if(gt(iw,ih),-2,min({max_px},ih))'"
    out = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-i", path, "-vf", vf, "-frames:v", "1",
                          "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-"], capture_output=True, check=False)
    if out.returncode or not out.stdout:
        raise ToolError(f"cannot read {os.path.basename(path)} as an image: {out.stderr.decode(errors='replace')[-200:]}")
    return out.stdout


def look(path: str, *, max_px: int = 1024) -> list:
    """What the chat sees of a file: an image as a thumbnail; a clip as a contact sheet + its numbers."""
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return [Image(data=thumbnail(path, max_px), format="jpeg"), {"path": path, "size_bytes": os.path.getsize(path)}]
    if ext in VIDEO_EXTS:
        info = verify.report(path)
        with tempfile.TemporaryDirectory() as tmp:
            sheet = verify.contact_sheet(path, os.path.join(tmp, "sheet.jpg"), frames=8, width=240)
            data = open(sheet, "rb").read()
        return [Image(data=data, format="jpeg"), {"path": path, **info}]
    raise ToolError(f"view_file shows images and clips; read text with store_read ({os.path.basename(path)})")


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

    # ------------------------------------------------------------ stories (stage 2.1)

    @mcp.tool
    def story_list() -> list:
        """Free. The stories: slug, title, language, cast, episodes, and what each one has spent."""
        rows = []
        root = s.stories_dir
        for slug in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            if not os.path.exists(os.path.join(root, slug, st.STORY_FILE)):
                continue
            story = st.Story(os.path.join(root, slug))
            eps = sorted(d for d in os.listdir(story.root) if d.startswith("ep") and os.path.isdir(story.path(d)))
            rows.append({**story.meta, "cast": story.cast(), "episodes": eps, "spent_usd": story.total_usd()})
        return rows

    @mcp.tool
    def story_create(title: str, language: str, universe_name: str = "", slug: str = "") -> dict:
        """Free. A new story folder: story.json + the skeletons of 00-brief.md, 01-universe.md, 04-season.md,
        memory.md. *language* "fr" or "en" (the episode's spoken language; prompts stay in English)."""
        try:
            story = st.Story.create(s.stories_dir, slug or st.slugify(title), title=title, language=language,
                                    universe_name=universe_name)
        except st.StoreError as exc:
            raise ToolError(str(exc)) from exc
        return {**story.meta, "files": sorted(os.listdir(story.root))}

    @mcp.tool
    def store_read(story: str, path: str = "") -> dict:
        """Free. A text file of the story (markdown, json, jsonl, ass) — or, with no *path*, the story's file
        tree with what is locked. Images and clips: view_file."""
        sto = open_story(s, story)
        if not path:
            locked = sto.locked()
            tree = []
            for dirpath, _, files in os.walk(sto.root):
                for f in sorted(files):
                    rel = os.path.relpath(os.path.join(dirpath, f), sto.root)
                    if not f.endswith(".tmp"):
                        tree.append({"path": rel, "locked": rel in locked})
            return {"story": sto.slug, "files": sorted(tree, key=lambda r: r["path"])}
        full = story_file(sto, path)
        if not full.endswith(TEXT_EXTS):
            raise ToolError(f"{path} is not text; view_file shows images and clips")
        text = open(full, encoding="utf-8").read()
        return {"path": path, "locked": sto.is_locked(path), "text": text}

    @mcp.tool
    def store_write(story: str, path: str, text: str) -> dict:
        """Free. Write a text file of the story (markdown, json, jsonl, ass), creating its folders. A locked file
        is refused: unlock it first with store_unlock and a reason. JSON is checked before it is written."""
        sto = open_story(s, story)
        if not path.endswith(TEXT_EXTS):
            raise ToolError(f"store_write writes text files only ({', '.join(TEXT_EXTS)})")
        story_file(sto, path, must_exist=False)
        if path.endswith(".json"):
            try:
                json.loads(text)
            except ValueError as exc:
                raise ToolError(f"{path} is not valid JSON: {exc}") from exc
        try:
            sto.write_text(path, text)
        except st.StoreError as exc:
            raise ToolError(str(exc)) from exc
        return {"path": path, "bytes": len(text.encode("utf-8"))}

    @mcp.tool
    def store_copy(story: str, src: str, dest: str) -> dict:
        """Free. Copy a file inside the story (a chosen candidate image to 02-cast/<char>/full_body.png ...).
        A locked destination is refused."""
        sto = open_story(s, story)
        full = story_file(sto, src)
        story_file(sto, dest, must_exist=False)
        try:
            sto.copy_in(full, dest)
        except st.StoreError as exc:
            raise ToolError(str(exc)) from exc
        return {"src": src, "dest": dest}

    @mcp.tool
    def store_lock(story: str, path: str, note: str) -> dict:
        """Free. Lock a file Rida approved (a sheet, the canonical image, a voice): it is never overwritten
        afterwards. *note*: who approved it and when, in Rida's words."""
        sto = open_story(s, story)
        story_file(sto, path)
        try:
            sto.lock(path, note)
        except st.StoreError as exc:
            raise ToolError(str(exc)) from exc
        return {"path": path, "locked": True}

    @mcp.tool
    def store_unlock(story: str, path: str, reason: str) -> dict:
        """Free. Unlock a locked file so it can change; *reason* is required and kept in locks.json."""
        sto = open_story(s, story)
        try:
            sto.unlock(path, reason)
        except st.StoreError as exc:
            raise ToolError(str(exc)) from exc
        return {"path": path, "locked": False}

    @mcp.tool
    def view_file(story: str, path: str, max_px: int = 1024):
        """Free. Look at a file of the story: an image as a picture; a clip as a contact sheet of 8 frames with
        its duration, size, fps, audio and loudness. To hand Rida the file itself: file_download."""
        sto = open_story(s, story)
        try:
            return look(story_file(sto, path), max_px=max(128, min(int(max_px), 2048)))
        except (RuntimeError, OSError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def file_download(story: str, path: str, max_mib: int = DOWNLOAD_MAX_MIB) -> EmbeddedResource:
        """Free. The file itself (a clip, an episode, an image, a voice), base64 in the answer, so the client can
        save it and show it to Rida. Refused over *max_mib* (default 25, ceiling 50)."""
        sto = open_story(s, story)
        full = story_file(sto, path)
        limit = max(1, min(int(max_mib), DOWNLOAD_CEILING_MIB))
        size = os.path.getsize(full)
        if size > limit * 1024 * 1024:
            raise ToolError(f"{path} is {size / 1048576:.1f} MiB, over the {limit} MiB limit")
        mime = mimetypes.guess_type(full)[0] or {".mp4": "video/mp4", ".wav": "audio/wav",
                                                 ".flac": "audio/flac"}.get(os.path.splitext(full)[1].lower(),
                                                                            "application/octet-stream")
        with open(full, "rb") as fh:
            blob = base64.b64encode(fh.read()).decode("ascii")
        return EmbeddedResource(type="resource", resource=BlobResourceContents(uri=f"file://{full}", mimeType=mime,
                                                                               blob=blob))

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
