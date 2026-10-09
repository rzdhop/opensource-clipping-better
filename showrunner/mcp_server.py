"""The showrunner MCP server (plan 36 stage 2): the chat drives the stage-1 toolbox.

    .venv/bin/python -m showrunner.mcp_server            # streamable HTTP on SHOWRUNNER_MCP_HOST:PORT, path /mcp
    .venv/bin/python -m showrunner.mcp_server --stdio    # a local client

Claude is the writer and the director; these tools are the hands: the story folder (``showrunner.store``),
GPU jobs on the showrunner endpoints, the clip check, the voice conversion, the assembly. No tool calls an
LLM and no tool decides anything creative. Every tool that spends money says so in its first line.

It replaced the rzdhop-story server (``mcp_server/``, deleted 2026-10-08, DEC-325): same port, same public URL.

Settings (environment, else the repo's ``.env``): ``SHOWRUNNER_MCP_HOST`` (127.0.0.1), ``SHOWRUNNER_MCP_PORT``
(8787), ``SHOWRUNNER_MCP_PUBLIC_URL`` (the Funnel URL, no ``/mcp``), ``MCP_TOKEN`` (the secret of both doors),
``SHOWRUNNER_STORIES_DIR`` (``stories/``),
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

from showrunner import assemble as asm  # noqa: E402
from showrunner import comfy_templates  # noqa: E402
from showrunner import jobs  # noqa: E402
from showrunner import mcp_auth  # noqa: E402
from showrunner import runpod_client as rp  # noqa: E402
from showrunner import store as st  # noqa: E402
from showrunner import verify  # noqa: E402
from showrunner import voice  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PORT = 8787
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
what is unwanted in a positive prompt (the models ignore the negative prompt). You write every prompt; these tools
only make the pictures, the clips and the sound, check them and cut the episode. A character's voice is locked from
a take Rida picked (voice_ref_from_take)."""


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
    forbidden: set = field(default_factory=set)     # endpoint ids never used: the live app's video endpoint


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
        forbidden={v for v in (get("RUNPOD_COMFY_ENDPOINT_ID"),) if v},   # read only to refuse it
    )


def template_kind(template_name: str) -> str:
    """Which endpoint serves *template_name*: ``images`` (Flux 2 Klein) or ``video`` (LTX, Chatterbox)."""
    return "images" if comfy_templates.load_template(template_name).get("task") in IMAGE_TASKS else "video"


class Backend:
    """What the tools share. Tests pass ``endpoint_factory`` to fake RunPod and ``sleep``/``clock`` to wait."""

    def __init__(self, settings: Settings | None = None, *, endpoint_factory=None, sleep=None, clock=None):
        import time as _time

        self.settings = settings or load_settings()
        self.sleep = sleep or _time.sleep
        self.clock = clock or _time.monotonic
        self._endpoint_factory = endpoint_factory or (lambda kind, eid, key: rp.Endpoint(eid, key=key))

    def endpoint(self, kind: str) -> rp.Endpoint:
        eid = self.settings.endpoints.get(kind)
        if not eid:
            raise ToolError(f"no {kind} endpoint configured (RUNPOD_SHOWRUNNER_VIDEO_ENDPOINT_ID / RUNPOD_IMAGE_ENDPOINT_ID)")
        if eid in self.settings.forbidden:
            raise ToolError(f"endpoint {eid} is the live app's video endpoint (RUNPOD_COMFY_ENDPOINT_ID): never used here")
        return self._endpoint_factory(kind, eid, self.settings.keys.get(kind))

    def rate(self, kind: str) -> float:
        return RATES.get(kind, RATES["video"])


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


def shot_entry(story: st.Story, ep: int, shot: str) -> dict:
    sheet = story.read_json(story.shots(ep)) or {}
    for entry in sheet.get("shots") or []:
        if entry.get("id") == shot:
            return entry
    raise ToolError(f"no shot {shot} in {story.shots(ep)}")


def take_entry(story: st.Story, ep: int, shot: str, take: str) -> dict:
    for t in (story.read_json(story.takes(ep)) or {}).get(shot, {}).get("takes", []):
        if t["take"] == take:
            return t
    raise ToolError(f"no take {shot}/{take} in {story.takes(ep)}")


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
    def story_list() -> dict:
        """Free. The stories: slug, title, language, cast, episodes, and what each one has spent."""
        rows = []
        root = s.stories_dir
        for slug in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            if not os.path.exists(os.path.join(root, slug, st.STORY_FILE)):
                continue
            story = st.Story(os.path.join(root, slug))
            eps = sorted(d for d in os.listdir(story.root) if d.startswith("ep") and os.path.isdir(story.path(d)))
            rows.append({**story.meta, "cast": story.cast(), "episodes": eps, "spent_usd": story.total_usd()})
        return {"count": len(rows), "stories": rows}

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

    # ------------------------------------------------------------ GPU jobs (stage 2.2)

    def _job_error(exc: Exception):
        raise ToolError(str(exc)) from exc

    @mcp.tool
    def comfy_submit(story: str, template: str, values: dict, files: dict | None = None, dest: str = "",
                     episode: int | None = None, shot: str | None = None) -> dict:
        """COSTS MONEY (one GPU job; the answer gives its warm and cold estimate). Only after Rida's go in the chat
        for this batch, with the count and the cost said first. Sends *template* (templates_list) with *values*
        (prompt — written by Claude in the chat —, seed, width, height, seconds ...) and *files* ({placeholder:
        story path}, e.g. {"image": "ep01/keyframes/s03.png"}). Where the result goes: *episode* + *shot* for a clip
        (it becomes the shot's next take, epNN/clips/sNN_vK.mp4) or *dest*, a story path without extension (e.g.
        "02-cast/ana/candidates/full_body_c1"). The prompt is kept in the story's job journal. Returns at once with
        the job id: then comfy_fetch."""
        sto = open_story(s, story)
        try:
            kind = template_kind(template)
        except (OSError, ValueError) as exc:
            raise ToolError(f"no template {template!r} (templates_list)") from exc
        ep = backend.endpoint(kind)
        try:
            row = jobs.submit(sto, ep, kind, template, values or {}, files or {}, dest=dest, episode=episode, shot=shot,
                              rate_per_s=backend.rate(kind))
        except jobs.JobError as exc:
            _job_error(exc)
        return {k: row[k] for k in ("job", "template", "kind", "dest", "episode", "shot", "estimate")}

    @mcp.tool
    def comfy_fetch(story: str, job: str, wait_s: int = 0):
        """Free (the job was paid at submit). The job's state; once done its outputs are saved into the story (a
        clip as its shot's next take), its cost written in the ledger, and you see the result (a picture, or a
        clip's contact sheet with its numbers). Waits up to *wait_s* (≤ 240) for it. A queue of 20–35 min is
        normal when GPUs are short; fetch again later. Fetching a finished job again bills nothing."""
        sto = open_story(s, story)
        try:
            row = jobs.find(sto, job)
            ep = backend.endpoint(row["kind"])
            got = jobs.fetch(sto, ep, job, wait_s=max(0, min(int(wait_s), 240)), rate_per_s=backend.rate(row["kind"]),
                             sleep=backend.sleep, clock=backend.clock)
        except jobs.JobError as exc:
            _job_error(exc)
        summary = {k: got.get(k) for k in ("job", "state", "template", "episode", "shot", "take", "outputs",
                                            "billed_s", "delay_s", "usd", "error", "waiting", "delay_s_so_far") if
                   got.get(k) is not None}
        outs = got.get("outputs") or []
        if got.get("state") == "COMPLETED" and outs and outs[0].endswith(IMAGE_EXTS + VIDEO_EXTS):
            return [*look(story_file(sto, outs[0]))[:1], summary]
        return summary

    @mcp.tool
    def comfy_jobs(story: str, open_only: bool = False) -> dict:
        """Free. The story's GPU jobs (newest last): state, template, shot or destination, cost. *open_only*: the
        ones still waiting or running (fetch them)."""
        rows = jobs.journal(open_story(s, story))
        keep = ("job", "state", "template", "kind", "episode", "shot", "dest", "take", "usd", "submitted_at",
                "settled_at", "estimate")
        rows = [{k: r[k] for k in keep if k in r} for r in rows]
        rows = [r for r in rows if not open_only or r["state"] == "SUBMITTED"]
        return {"count": len(rows), "jobs": rows}

    @mcp.tool
    def cost_ledger(story: str, episode: int | None = None) -> dict:
        """Free. What the story (or one episode) has spent on GPUs: execution time billed (the queue is free),
        per kind of job, with the rows."""
        sto = open_story(s, story)
        rows = sto.costs(episode)
        by_kind: dict = {}
        for r in rows:
            by_kind[r["kind"]] = round(by_kind.get(r["kind"], 0.0) + r["usd"], 4)
        return {"story": sto.slug, "episode": episode, "total_usd": sto.total_usd(episode), "by_kind": by_kind,
                "gpu_seconds": round(sum(r["billed_s"] for r in rows), 1), "rows": rows}

    # ------------------------------------------------------------ gates (stage 2.3)

    @mcp.tool
    def verify_take(story: str, episode: int, shot: str, take: str) -> dict:
        """Free (this server's CPU, ≈ 35 s). The clip check of a take: speech-to-text of the clip's own sound,
        aligned to the shot's scripted lines (shots.json) — share of each line heard, where it starts and ends,
        lines in order, the last word before the clip's end. The verdict is stored in takes.json (the assembly
        trims and subtitles from it). States: ok · mismatch · late · no_speech. Show Rida the clip either way."""
        sto = open_story(s, story)
        entry = shot_entry(sto, episode, shot)
        t = take_entry(sto, episode, shot, take)
        lines = [(line["speaker"], line["text"]) for line in entry.get("lines") or []]
        clip = story_file(sto, t["path"])
        if not lines:
            verdict = {"state": "no_lines", "duration_s": verify.probe(clip)["duration_s"], "lines": []}
        else:
            try:
                verdict = verify.verify_take(clip, lines, sto.language)
            except (RuntimeError, ImportError) as exc:
                raise ToolError(f"the clip check failed: {exc}") from exc
        sto.set_verdict(episode, shot, take, verdict)
        return {"shot": shot, "take": take, **{k: verdict.get(k) for k in ("state", "matched", "in_order", "start_s",
                                                                             "end_s", "duration_s", "extra_after_s",
                                                                             "heard_text")},
                "lines": [{k: r.get(k) for k in ("speaker", "heard", "words", "start_s", "end_s")}
                          for r in verdict.get("lines") or []]}

    def _vc_plan_path(ep: int, shot: str, take: str) -> str:
        return f"{st.episode_dir(ep)}/vc/{shot}_{take}.json"

    @mcp.tool
    def vc_clip(story: str, episode: int, shot: str, take: str) -> dict:
        """COSTS MONEY (one tiny GPU job per line, ≈ $0.001 each warm; a cold worker ≈ $0.15 once). Only after
        Rida's go. Re-voices a checked take with its speakers' locked voices (02-cast/<char>/voice_ref.wav):
        the clip is cut between its lines (from verify_take's timings), each part converted to its own
        speaker's voice; picture and timing untouched (DEC-323). Returns the job ids; then vc_fetch."""
        sto = open_story(s, story)
        t = take_entry(sto, episode, shot, take)
        verdict = t.get("verdict") or {}
        if not verdict.get("lines"):
            raise ToolError(f"{shot}/{take} has no clip check with line timings: run verify_take first")
        try:
            parts = voice.parts_plan(verdict, verdict.get("duration_s") or verify.probe(sto.path(t["path"]))["duration_s"])
        except ValueError as exc:
            raise ToolError(f"{shot}/{take} cannot be split by speaker: {exc}") from exc
        missing = sorted({p["speaker"] for p in parts if not sto.exists(f"{sto.cast_dir(p['speaker'])}/voice_ref.wav")})
        if missing:
            raise ToolError(f"no locked voice (02-cast/<char>/voice_ref.wav) for: {', '.join(missing)}")
        ep_dir = st.episode_dir(episode)
        endpoint = backend.endpoint("video")
        clip = story_file(sto, t["path"])
        for p in parts:
            stem = f"{ep_dir}/vc/{shot}_{take}_l{p['k']}_{p['speaker']}"
            voice.cut_part(clip, p, sto.writable(stem + "_in.wav"))
            try:
                row = jobs.submit(sto, endpoint, "video", voice.VC_TEMPLATE, {"seed": 0},
                                  {"input": stem + "_in.wav", "target_voice": f"{sto.cast_dir(p['speaker'])}/voice_ref.wav"},
                                  dest=stem + "_vc", episode=episode, rate_per_s=backend.rate("video"))
            except jobs.JobError as exc:
                raise ToolError(str(exc)) from exc
            p["job"] = row["job"]
        sto.write_json(_vc_plan_path(episode, shot, take), {"shot": shot, "take": take, "clip": t["path"], "parts": parts})
        return {"shot": shot, "take": take, "jobs": [p["job"] for p in parts],
                "parts": [{k: p[k] for k in ("speaker", "start_s", "end_s")} for p in parts]}

    @mcp.tool
    def vc_fetch(story: str, episode: int, shot: str, take: str, wait_s: int = 0):
        """Free (paid at vc_clip). Collects the voice conversion of a take; once every part is back, the parts are
        joined and laid under the original picture as the shot's next take (same timing, the clip check's
        timings carried over), and you see it. Waits up to *wait_s* (≤ 240)."""
        sto = open_story(s, story)
        rel = _vc_plan_path(episode, shot, take)
        if not sto.exists(rel):
            raise ToolError(f"no voice conversion started for {shot}/{take}: vc_clip first")
        plan = sto.read_json(rel)
        if plan.get("result_take"):
            new = take_entry(sto, episode, shot, plan["result_take"])
            return [*look(story_file(sto, new["path"]))[:1], {"shot": shot, "take": plan["result_take"], "from": take}]
        endpoint = backend.endpoint("video")
        deadline = backend.clock() + max(0, min(int(wait_s), 240))
        states = []
        for p in plan["parts"]:
            try:
                got = jobs.fetch(sto, endpoint, p["job"], wait_s=max(0.0, deadline - backend.clock()),
                                 rate_per_s=backend.rate("video"), sleep=backend.sleep, clock=backend.clock)
            except jobs.JobError as exc:
                raise ToolError(str(exc)) from exc
            states.append(got)
        if any(g.get("state") in ("FAILED", "CANCELLED", "TIMED_OUT") for g in states):
            raise ToolError("a part failed: " + "; ".join(f"{g['job']} {g['state']} {g.get('error', '')}" for g in states))
        if not all(g.get("state") == "COMPLETED" for g in states):
            return {"shot": shot, "take": take, "waiting": [g["job"] for g in states if g.get("state") != "COMPLETED"]}
        src = take_entry(sto, episode, shot, take)
        new_take = sto.next_take(episode, shot)
        clip_rel = sto.clip(episode, shot, new_take)
        try:
            voice.rebuild(story_file(sto, src["path"]),
                          [(story_file(sto, g["outputs"][0]), p["seconds"]) for g, p in zip(states, plan["parts"])],
                          sto.writable(f"{st.episode_dir(episode)}/vc/{shot}_{take}_vc.wav"), sto.writable(clip_rel))
        except (RuntimeError, st.StoreError) as exc:
            raise ToolError(f"the converted clip could not be rebuilt: {exc}") from exc
        verdict = dict(src.get("verdict") or {}, verdict_from=take)
        sto.add_take(episode, shot, clip_rel, seed=src.get("seed"), verdict=verdict,
                     note=f"{take} with every line in its speaker's locked voice")
        plan["result_take"] = new_take
        sto.write_json(rel, plan)
        return [*look(story_file(sto, clip_rel))[:1], {"shot": shot, "take": new_take, "from": take, "clip": clip_rel}]

    @mcp.tool
    def approve_take(story: str, episode: int, shot: str, take: str, note: str) -> dict:
        """Free. Rida approved this take: it becomes the shot's clip and its file is locked. Call it ONLY after
        Rida said so in the chat; *note* quotes Rida's words. Nothing is assembled from an unapproved clip."""
        if not note.strip():
            raise ToolError("approve_take needs a note quoting Rida's approval")
        sto = open_story(s, story)
        take_entry(sto, episode, shot, take)
        try:
            path = sto.approve_take(episode, shot, take, note)
        except st.StoreError as exc:
            raise ToolError(str(exc)) from exc
        return {"shot": shot, "take": take, "clip": path, "approved": True}

    @mcp.tool
    def voice_ref_from_take(story: str, episode: int, shot: str, take: str, speaker: str, note: str) -> dict:
        """Free (this server's CPU). Locks a character's voice for the whole series, the stage-0 way: the
        *speaker*'s line(s) of a take Rida APPROVED (approve_take), cut by the clip check's timings, silence
        trimmed (2-10 s), saved as 02-cast/<speaker>/voice_ref.wav and locked; vc_clip then re-voices every clip
        with it. The casting take is usually an ep00 shot of that character alone. *note* quotes Rida choosing this
        voice. An already locked voice is refused: store_unlock it with a reason first. Hand Rida the file to hear
        (file_download) before going on."""
        if not note.strip():
            raise ToolError("voice_ref_from_take needs a note quoting Rida choosing this voice")
        sto = open_story(s, story)
        t = take_entry(sto, episode, shot, take)
        if not t.get("approved"):
            raise ToolError(f"{shot}/{take} is not approved: the voice comes from a take Rida picked (approve_take)")
        if not (t.get("verdict") or {}).get("lines"):
            raise ToolError(f"{shot}/{take} has no clip check with line timings: run verify_take first")
        if not sto.exists(sto.sheet(speaker)):
            raise ToolError(f"no character sheet {sto.sheet(speaker)}")
        dest = f"{sto.cast_dir(speaker)}/voice_ref.wav"
        if sto.is_locked(dest):
            raise ToolError(f"{dest} is locked; store_unlock it with a reason to cast the voice again")
        try:
            got = voice.reference_from_take(story_file(sto, t["path"]), t["verdict"], speaker, sto.writable(dest))
        except (voice.VoiceRefError, RuntimeError) as exc:
            raise ToolError(str(exc)) from exc
        source = {"episode": episode, "shot": shot, "take": take, "clip": t["path"]}
        sto.write_json(f"{sto.cast_dir(speaker)}/voice_ref.json", {**got, "from": source, "note": note.strip()})
        sto.lock(dest, f"voice of {speaker} from {st.episode_dir(episode)} {shot}/{take}: {note.strip()}")
        return {"path": dest, "locked": True, **got, "from": source}

    @mcp.tool
    def assemble_episode(story: str, episode: int, preset: str = "medium"):
        """Free (this server's CPU, ≈ 1 min for 30 s). The episode from its approved takes only: each clip cut after
        its last word, never slowed; subtitles from the clip check; the music bed of shots.json ducked under the
        voices; the end card. Refused, naming them, while a shot has no approved take. You see a contact sheet
        and the numbers; hand Rida the mp4 with file_download (epNN/final.mp4)."""
        sto = open_story(s, story)
        try:
            report = asm.assemble(sto, episode, preset=preset if preset in ("ultrafast", "veryfast", "fast", "medium") else "medium")
        except (asm.AssemblyError, st.StoreError) as exc:
            raise ToolError(str(exc)) from exc
        sheet = story_file(sto, report["sheet"])
        return [Image(data=open(sheet, "rb").read(), format="jpeg"), report]

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
