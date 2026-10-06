"""The MCP server's tools (``mcp_server/server.py``) through an in-process
MCP client: a job submitted, fetched and shown as an image; a clip shown as
a contact sheet; a file viewed; errors said as tool errors. Fake RunPod
transport; ``fastmcp`` is optional in the repo, so the module is skipped
where it is not installed (``pip install .[mcp]``).
"""

import asyncio
import base64
import io
import json
import shutil

import pytest

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402

from clipping.providers.transport import Response  # noqa: E402
from mcp_server.config import Settings  # noqa: E402
from mcp_server.runpod_jobs import JobClient  # noqa: E402
from mcp_server.server import Backend, build_server  # noqa: E402


class FakeTransport:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append((method, url))
        if not self.answers:
            raise AssertionError(f"unexpected call: {method} {url}")
        status, payload = self.answers.pop(0)
        return Response(status, {}, json.dumps(payload).encode())


def png_bytes(color=(200, 40, 40), size=(640, 1152)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def completed(files):
    return (200, {"id": "job-1", "status": "COMPLETED", "output": {"images": files}, "executionTime": 9000,
                  "delayTime": 1000, "workerId": "w-1"})


@pytest.fixture
def backend(tmp_path):
    settings = Settings(api_key="rpa_fake", endpoints={"video": "vid1", "image": "img1"},
                        rates={"video": 3.49, "image": 1.58}, outputs_dir=str(tmp_path / "outputs"))
    transport = FakeTransport([])
    backend = Backend(settings, client=JobClient(settings, transport=transport))
    backend.transport = transport
    return backend


def call(server, tool, **args):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(tool, args, raise_on_error=False)
    return asyncio.run(go())


def payload(result):
    """The JSON a tool answered (its first text block)."""
    return json.loads(result.content[0].text)


def tool_names(server):
    async def go():
        async with Client(server) as client:
            return sorted(t.name for t in await client.list_tools())
    return asyncio.run(go())


def test_the_stage_1_tools_are_listed(backend):
    server = build_server(backend)
    assert tool_names(server) == ["comfy_cancel", "comfy_fetch", "comfy_jobs", "comfy_status", "comfy_submit",
                                  "cost_ledger", "list_files", "runpod_health", "templates_list", "view_file"]


def test_templates_and_health_answer_from_the_endpoints(backend):
    backend.transport.answers = [(200, {"workers": {"idle": 1, "ready": 1}, "jobs": {"inQueue": 0}}),
                                 (200, {"workers": {"throttled": 2}, "jobs": {}})]
    server = build_server(backend)
    names = [t["name"] for t in payload(call(server, "templates_list"))]
    assert "t2i_flux2_klein" in names and "i2v_wan22_14b_lightning" in names
    health = payload(call(server, "runpod_health"))
    assert health["image"]["ok"] and health["image"]["workers"]["idle"] == 1
    assert health["video"]["workers"]["throttled"] == 2
    assert backend.transport.calls == [("GET", "https://api.runpod.ai/v2/img1/health"),
                                       ("GET", "https://api.runpod.ai/v2/vid1/health")]


def test_an_image_job_is_submitted_then_fetched_as_a_thumbnail(backend):
    data = base64.b64encode(png_bytes()).decode()
    backend.transport.answers = [(200, {"id": "job-1", "status": "IN_QUEUE"}),
                                 completed([{"filename": "ComfyUI_00001_.png", "type": "base64", "data": data}])]
    server = build_server(backend)
    submitted = payload(call(server, "comfy_submit", template="t2i_flux2_klein", prompt="a kiwi", name="kiwi",
                              dest="cast"))
    assert submitted["job_id"] == "job-1" and submitted["state"] == "IN_QUEUE" and "prompt" not in submitted
    result = call(server, "comfy_fetch", job_id="job-1", wait_s=0)
    kinds = [type(block).__name__ for block in result.content]
    assert kinds[0] == "ImageContent" and getattr(result.content[0], "mime_type", None) == "image/jpeg"
    shown = base64.b64decode(result.content[0].data)
    from PIL import Image

    with Image.open(io.BytesIO(shown)) as im:
        assert im.size == (569, 1024)  # scaled to max_px on the long side
    texts = [json.loads(b.text) for b in result.content if type(b).__name__ == "TextContent"]
    assert texts[0]["shown_at"] == "569x1024" and texts[0]["path"].endswith("cast/kiwi.png")
    record = texts[-1]
    assert record["state"] == "COMPLETED" and record["gpu_seconds"] == 10.0 and record["billed_usd"] > 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_a_clip_is_fetched_as_a_contact_sheet(backend, tmp_path):
    clip = tmp_path / "clip.mp4"
    import subprocess

    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=180x320:rate=16:duration=2",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    keyframe = tmp_path / "outputs" / "still.png"
    keyframe.parent.mkdir(parents=True)
    keyframe.write_bytes(png_bytes(size=(180, 320)))
    data = base64.b64encode(clip.read_bytes()).decode()
    backend.transport.answers = [(200, {"id": "job-1", "status": "IN_QUEUE"}),
                                 completed([{"filename": "clip_00001_.mp4", "type": "base64", "data": data}])]
    server = build_server(backend)
    call(server, "comfy_submit", template="i2v_wan22_14b_lightning", prompt="x", seconds=2, image_path="still.png",
         name="sh01")
    result = call(server, "comfy_fetch", job_id="job-1", wait_s=0)
    assert type(result.content[0]).__name__ == "ImageContent"
    info = json.loads(result.content[1].text)
    assert info["duration_s"] == 2.0 and info["width"] == 180 and info["has_audio"] is False
    assert "8 frames" in info["contact_sheet"]


def test_a_still_running_job_comes_back_as_its_record_only(backend):
    backend.transport.answers = [(200, {"id": "job-1", "status": "IN_QUEUE"}),
                                 (200, {"id": "job-1", "status": "IN_PROGRESS"})]
    server = build_server(backend)
    call(server, "comfy_submit", template="t2i_flux2_klein", prompt="x")
    result = call(server, "comfy_fetch", job_id="job-1", wait_s=0)
    assert [type(b).__name__ for b in result.content] == ["TextContent"]
    assert json.loads(result.content[0].text)["state"] == "IN_PROGRESS"


def test_view_file_and_list_files_see_the_outputs_dir(backend, tmp_path):
    out = tmp_path / "outputs" / "cast"
    out.mkdir(parents=True)
    (out / "kiwi.png").write_bytes(png_bytes(size=(64, 64)))
    (out / "notes.txt").write_text("x")
    server = build_server(backend)
    shown = call(server, "view_file", path="cast/kiwi.png", max_px=32)
    assert type(shown.content[0]).__name__ == "ImageContent"
    assert json.loads(shown.content[1].text)["shown_at"] == "32x32"
    rows = payload(call(server, "list_files", folder="cast"))
    assert sorted(r["path"] for r in rows) == ["cast/kiwi.png", "cast/notes.txt"]
    other = call(server, "view_file", path="cast/notes.txt")
    assert json.loads(other.content[0].text)["note"] == "not an image or a video"


def test_a_bad_submit_is_a_tool_error_with_the_reason(backend):
    server = build_server(backend)
    result = call(server, "comfy_submit", template="nope", prompt="x")
    assert result.is_error and "no template 'nope'" in result.content[0].text
    assert backend.transport.calls == []
    gone = call(server, "comfy_status", job_id="zzz")
    assert gone.is_error and "not in the journal" in gone.content[0].text
