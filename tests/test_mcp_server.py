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
    from mcp_server.director import Director
    from mcp_server.story_tools import StoryBackend

    settings = Settings(api_key="rpa_fake", endpoints={"video": "vid1", "image": "img1"},
                        rates={"video": 3.49, "image": 1.58}, outputs_dir=str(tmp_path / "outputs"))
    transport = FakeTransport([])
    outputs = str(tmp_path / "outputs")
    story = StoryBackend(outputs, director=Director(outputs, settings_env={}, event_wait=5.0), settings_env={})
    backend = Backend(settings, client=JobClient(settings, transport=transport), story=story)
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


def test_the_tools_are_listed(backend):
    server = build_server(backend)
    names = tool_names(server)
    for name in ("comfy_cancel", "comfy_fetch", "comfy_jobs", "comfy_status", "comfy_submit", "cost_ledger",
                 "list_files", "runpod_health", "templates_list", "view_file", "comfy_download"):
        assert name in names, name
    for name in ("story_list", "story_create", "story_options", "story_get", "story_doc", "story_entities",
                 "story_entity", "episode_get", "episode_doc", "story_step_start", "story_step_answer",
                 "story_step_status", "story_step_cancel", "story_runs", "story_approve", "story_approve_all",
                 "story_choose_concept", "story_patch", "entity_patch", "episode_patch",
                 # Plan 32 stage 1.
                 "story_make_episode", "story_estimate"):
        assert name in names, name


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


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")
def test_a_voice_line_is_fetched_with_its_duration(backend, tmp_path):
    import wave

    def wav_bytes(seconds):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * int(24000 * seconds))
        return buf.getvalue()

    ref = tmp_path / "outputs" / "ref_rida.wav"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(wav_bytes(8))
    line = base64.b64encode(wav_bytes(1.5)).decode()
    backend.transport.answers = [
        (200, {"id": "job-1", "status": "IN_QUEUE"}),
        (200, {"id": "job-1", "status": "COMPLETED", "executionTime": 8000, "delayTime": 1000, "workerId": "w-1",
               "output": {"audio": [{"filename": "tts_00001_.wav", "type": "base64", "data": line}]}})]
    server = build_server(backend)
    rows = {t["name"]: t for t in payload(call(server, "templates_list"))}
    assert rows["tts_chatterbox"]["kind"] == "audio"
    submitted = payload(call(server, "comfy_submit", template="tts_chatterbox", prompt="Par où ?", seed=3,
                             audio_path=str(ref), name="l03", dest="ep01/voices"))
    assert submitted["kind"] == "audio" and submitted["state"] == "IN_QUEUE"
    result = call(server, "comfy_fetch", job_id="job-1", wait_s=0)
    assert all(type(b).__name__ == "TextContent" for b in result.content)  # nothing to look at: a sound
    texts = [json.loads(b.text) for b in result.content]
    if len(texts) == 1 and isinstance(texts[0], list):  # all-text answers travel as one JSON list
        texts = texts[0]
    assert texts[0]["path"].endswith("ep01/voices/l03.wav") and texts[0]["duration_s"] == 1.5
    assert texts[0]["sample_rate"] == 24000 and texts[0]["channels"] == 1 and texts[0]["size_bytes"] > 44
    assert texts[-1]["state"] == "COMPLETED" and texts[-1]["gpu_seconds"] == 9.0


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


def test_comfy_download_hands_back_the_bytes_and_stays_inside_the_roots(backend, tmp_path):
    out = tmp_path / "outputs" / "cast"
    out.mkdir(parents=True)
    png = png_bytes(size=(64, 64))
    (out / "kiwi.png").write_bytes(png)
    (out / "big.bin").write_bytes(b"\0" * (1024 * 1024 + 1))
    server = build_server(backend)
    got = call(server, "comfy_download", path="cast/kiwi.png")
    block = got.content[0]
    assert type(block).__name__ == "EmbeddedResource"
    assert base64.b64decode(block.resource.blob) == png
    assert block.resource.model_dump(by_alias=True)["mimeType"] == "image/png"
    assert block.resource.model_dump(by_alias=True)["uri"] == "file://" + str(out / "kiwi.png")
    for bad in ("/etc/passwd", "../../etc/passwd", "cast/missing.png"):
        refused = call(server, "comfy_download", path=bad)
        assert refused.is_error, bad
        assert "outside" in refused.content[0].text or "no such file" in refused.content[0].text
    too_big = call(server, "comfy_download", path="cast/big.bin", max_mib=1)
    assert too_big.is_error and "over the 1 MiB limit" in too_big.content[0].text


def test_a_bad_submit_is_a_tool_error_with_the_reason(backend):
    server = build_server(backend)
    result = call(server, "comfy_submit", template="nope", prompt="x")
    assert result.is_error and "no template 'nope'" in result.content[0].text
    assert backend.transport.calls == []
    gone = call(server, "comfy_status", job_id="zzz")
    assert gone.is_error and "not in the journal" in gone.content[0].text


# ------------------------------------------------------------- stories

def test_a_story_is_created_read_patched_and_its_approvals_refused_with_the_reason(backend):
    server = build_server(backend)
    options = payload(call(server, "story_options"))
    assert "fruit_drama" in [s["id"] for s in options["styles"]] and "concepts" in options["steps"]
    story = payload(call(server, "story_create", language="fr", seed_text="Un kiwi détective dans un frigo.",
                         style="fruit_drama"))
    sid = story["story_id"]
    assert story["language"] == "fr" and story["status"] == "draft"
    listed = payload(call(server, "story_list"))
    assert [s["story_id"] for s in listed] == [sid]
    page = payload(call(server, "story_get", story_id=sid))
    assert page["story"]["story_id"] == sid and page["characters"] == [] and page["list_progress"]["next"]
    patched = payload(call(server, "story_patch", story_id=sid, fields={"title": "Frigo noir"}))
    assert patched["title"] == "Frigo noir"
    doc = payload(call(server, "story_doc", story_id=sid, name="story.json"))
    assert doc["title"] == "Frigo noir"
    refused = call(server, "story_approve", story_id=sid, doc="bible")
    assert refused.is_error and "concept" in refused.content[0].text.lower()
    nothing = call(server, "story_approve", story_id=sid, doc="moon")
    assert nothing.is_error
    missing = call(server, "story_doc", story_id=sid, name="season.json")
    assert missing.is_error and "does not exist yet" in missing.content[0].text
    unknown = call(server, "story_get", story_id="000000000000")
    assert unknown.is_error and "not_found" in unknown.content[0].text


def test_the_concepts_step_parks_its_real_prompt_for_the_chat(backend):
    server = build_server(backend)
    story = payload(call(server, "story_create", language="en", seed_text="A kiwi detective in a fridge."))
    sid = story["story_id"]
    run = payload(call(server, "story_step_start", story_id=sid, step="concepts", params={"count": 5}))
    assert run["state"] == "waiting", run
    pending = run["pending"]
    assert pending["schema_name"] and "kiwi" in pending["user"].lower()
    assert isinstance(pending["schema"], dict) and pending["max_tokens"] > 0
    runs = payload(call(server, "story_runs", story_id=sid))
    assert runs[0]["run_id"] == run["run_id"] and runs[0]["state"] == "waiting"
    busy = call(server, "story_step_start", story_id=sid, step="bible")
    assert busy.is_error and "already has a concepts run" in busy.content[0].text
    cancelled = payload(call(server, "story_step_cancel", run_id=run["run_id"]))
    assert cancelled["state"] == "cancelled"
    bad = call(server, "story_step_answer", handle=pending["handle"], answer={"x": 1})
    assert bad.is_error


# ------------------------------------------------------------- plan 32 stage 1

def test_a_preset_story_shows_its_recipe_and_the_own_gpu_profile(backend):
    server = build_server(backend)
    story = payload(call(server, "story_create", language="en", seed_text="A jealous pineapple.",
                         preset="fruit_drama"))
    assert story["recipe"] == "fruit_drama" and story["style_template_id"] == "fruit_drama"
    page = payload(call(server, "story_get", story_id=story["story_id"]))
    assert page["recipe"] == "fruit_drama" and page["story"]["recipe"] == "fruit_drama"
    profile = page["story"]["generation_profile"]
    assert profile["budget_profile"] == "own_gpu" and profile["universe"] == "fruits" and profile["tier"] == 3
    # What the caller names wins, key by key.
    own = payload(call(server, "story_create", language="fr", preset="fruit_drama",
                       generation_profile={"budget_profile": "quality"}))
    assert own["generation_profile"]["budget_profile"] == "quality"
    assert own["generation_profile"]["universe"] == "fruits"
    plain = payload(call(server, "story_create", language="en"))
    assert plain["recipe"] is None and plain["generation_profile"]["budget_profile"] == "free"
    unknown = call(server, "story_create", language="en", preset="moon_opera")
    assert unknown.is_error and "fruit_drama" in unknown.content[0].text


def test_the_options_list_presets_budget_profiles_and_formats(backend):
    server = build_server(backend)
    options = payload(call(server, "story_options"))
    fruit = next(p for p in options["presets"] if p["id"] == "fruit_drama")
    assert fruit["label"] and fruit["summary"] and fruit["sets"]
    caps = {p["id"]: p["cap_usd"] for p in options["budget_profiles"]}
    assert caps["own_gpu"] == 2.0 and caps["free"] == 0.0
    formats = {f["id"]: f for f in options["episode_formats"]}
    assert formats["serial_60s_v2"]["window_s"] == [55, 75] and formats["serial_60s_v2"]["scenes"]
    assert "style" in options["steps"] and "fast-track" in options["steps"]


def test_estimates_answer_on_a_fresh_story_without_keys(backend):
    server = build_server(backend)
    sid = payload(call(server, "story_create", language="en", seed_text="A jealous pineapple.",
                       preset="fruit_drama"))["story_id"]
    cast = payload(call(server, "story_estimate", story_id=sid, what="cast"))
    assert cast["ready"] is False and "No cost before the pictures" in cast["message"]
    episode = payload(call(server, "story_estimate", story_id=sid, what="episode", episode=1))
    assert episode["ready"] is False and "cannot be made yet" in episode["message"]
    render = payload(call(server, "story_estimate", story_id=sid, what="render", episode=1))
    assert render["ready"] is False and render["est_usd"] == 0.0
    whole = payload(call(server, "story_estimate", story_id=sid, what="story"))
    assert whole["what"] == "story" and isinstance(whole["est_usd"], float) and whole["message"]
    assert whole["details"]["parts"]
    # The chat is the writer: the run does not stop on a missing writing key (the concept is part 1).
    assert (whole["details"]["stops_at"] or {}).get("part") != "concept", whole["details"]["stops_at"]
    studio = payload(call(server, "story_estimate", story_id=payload(call(server, "story_create",
                                                                           language="en"))["story_id"], what="story"))
    assert studio["ready"] is False and "agent" in studio["message"]
    assert call(server, "story_estimate", story_id=sid, what="moon").is_error
    assert call(server, "story_estimate", story_id=sid, what="episode").is_error


def test_the_style_step_is_started_from_the_chat_and_approved(backend):
    from clipping.aistory import workflow

    server = build_server(backend)
    sid = payload(call(server, "story_create", language="en", seed_text="A jealous pineapple.",
                       preset="fruit_drama"))["story_id"]
    early = payload(call(server, "story_step_start", story_id=sid, step="style"))
    assert early["state"] == "failed" and "bible" in early["error"].lower()

    def approved(doc):
        doc["approvals"]["concept"] = doc["approvals"]["bible"] = "2026-10-06T12:00:00+00:00"

    workflow.update(backend.story.stories, sid, approved, now="2026-10-06T12:00:00+00:00")
    run = payload(call(server, "story_step_start", story_id=sid, step="style"))
    assert run["state"] == "done", run
    assert run["result"]["template_id"] == "fruit_drama" and "pending" not in run
    locked = payload(call(server, "story_approve", story_id=sid, doc="style"))
    assert locked["approvals"]["style"]


def test_make_episode_is_the_fast_track_run_of_one_episode(backend):
    server = build_server(backend)
    sid = payload(call(server, "story_create", language="en", seed_text="A jealous pineapple.",
                       preset="fruit_drama"))["story_id"]
    run = payload(call(server, "story_make_episode", story_id=sid, episode=1))
    assert run["step"] == "fast-track" and run["ep"] == 1
    # A story not ready yet: the run ends at once with the step's own sentence (what comes first).
    assert run["state"] == "failed" and "first" in run["error"].lower(), run

