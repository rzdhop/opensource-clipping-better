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
import pathlib
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


def wav_bytes(seconds, *, rate=24000, tone=False):
    """A mono 16-bit WAV of *seconds*: silence, or a 220 Hz tone when *tone*."""
    import math
    import wave

    buf = io.BytesIO()
    n = int(rate * seconds)
    if tone:
        frames = b"".join(int(12000 * math.sin(2 * math.pi * 220 * i / rate)).to_bytes(2, "little", signed=True)
                          for i in range(n))
    else:
        frames = b"\x00\x00" * n
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


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
                 "list_files", "runpod_health", "templates_list", "view_file", "comfy_download",
                 # Plan 33 stages 2 and 3.
                 "file_upload", "tts_line", "tts_batch", "voice_ref_make"):
        assert name in names, name
    for name in ("story_list", "story_create", "story_options", "story_get", "story_doc", "story_entities",
                 "story_entity", "episode_get", "episode_doc", "story_step_start", "story_step_answer",
                 "story_step_status", "story_step_cancel", "story_runs", "story_approve", "story_approve_all",
                 "story_choose_concept", "story_patch", "entity_patch", "episode_patch",
                 # Plan 32 stage 1.
                 "story_make_episode", "story_estimate",
                 # Plan 32 stage 5.
                 "episode_sheet", "episode_export"):
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
    assert record["state"] == "COMPLETED" and record["gpu_seconds"] == 9.0 and record["billed_usd"] > 0
    assert record["delay_seconds"] == 1.0 and record["wall_seconds"] == 10.0  # plan 33: shown, not billed


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
    assert texts[-1]["state"] == "COMPLETED" and texts[-1]["gpu_seconds"] == 8.0


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


def test_file_upload_writes_under_the_outputs_dir_only(backend, tmp_path):
    """Plan 33 stage 2: a reference voice or a line list from the chat lands under outputs/; '..',
    an absolute path elsewhere, a sibling of the outputs dir, a bad extension and an oversized
    payload are refused; an existing file needs overwrite."""
    server = build_server(backend)
    wav = wav_bytes(seconds=0.2)
    got = payload(call(server, "file_upload", dest_path="faille_damour/ep01/voices/ref.wav",
                       content_base64=base64.b64encode(wav).decode("ascii"), kind="audio"))
    saved = tmp_path / "outputs" / "faille_damour" / "ep01" / "voices" / "ref.wav"
    assert saved.read_bytes() == wav and got["path"] == str(saved) and got["kind"] == "audio"
    assert got["size_bytes"] == len(wav) and got["relative"] == "faille_damour/ep01/voices/ref.wav"
    if shutil.which("ffprobe"):
        assert abs(got["duration_s"] - 0.2) < 0.05
    again = call(server, "file_upload", dest_path="faille_damour/ep01/voices/ref.wav",
                 content_base64=base64.b64encode(wav).decode("ascii"))
    assert again.is_error and "overwrite=true" in again.content[0].text
    replaced = payload(call(server, "file_upload", dest_path="faille_damour/ep01/voices/ref.wav",
                            content_base64=base64.b64encode(b"RIFF" + wav[4:]).decode("ascii"), overwrite=True))
    assert replaced["overwritten"] is True
    lines = base64.b64encode(b'[{"id": "l01"}]').decode("ascii")
    assert payload(call(server, "file_upload", dest_path="x/lines.json", content_base64=lines))["kind"] == "json"
    outputs_evil = tmp_path / "outputs_evil"
    for bad, why in (("../escape.wav", "outside"), ("/etc/evil.wav", "outside"),
                     (str(outputs_evil / "a.wav"), "outside"), ("x/run.sh", "not allowed"),
                     ("x/.hidden.wav", "dotfile"), ("x/a.wav", "takes")):
        refused = call(server, "file_upload", dest_path=bad, content_base64=lines,
                       kind="image" if why == "takes" else None)
        assert refused.is_error and why in refused.content[0].text, (bad, refused.content[0].text)
    assert not outputs_evil.exists()
    big = "A" * (25 * 1024 * 1024 * 4 // 3 + 100)
    too_big = call(server, "file_upload", dest_path="x/big.wav", content_base64=big)
    assert too_big.is_error and "25 MiB" in too_big.content[0].text
    junk = call(server, "file_upload", dest_path="x/junk.wav", content_base64="not base64!!")
    assert junk.is_error and "base64" in junk.content[0].text


def test_the_voice_tools_answer_through_the_server_and_the_ledger_sums_them(backend, tmp_path):
    """Plan 33 stage 3: tts_line through the MCP client with a fake Gemini adapter; a bad provider
    is a tool error with the reason; cost_ledger carries the voice lines beside the GPU jobs."""
    from clipping.providers.generation import GenResult
    from mcp_server.voice_tools import VoiceTools

    class FakeGemini:
        def generate(self, link, request, *, credentials, on_log, **_):
            wav = str(pathlib.Path(request.out_dir) / f"{request.extra['name']}.wav")
            pathlib.Path(wav).write_bytes(wav_bytes(1.1, tone=True))
            return GenResult(provider="gemini", model="flash-lite-tts", paths=(wav, wav), meta={"duration_s": 1.1})

    backend.voice = VoiceTools(backend.client, outputs_dir=backend.settings.outputs_dir,
                               env={"GOOGLE_API_KEY": "k"}, gemini=FakeGemini())
    server = build_server(backend)
    got = payload(call(server, "tts_line", text="Pardon !", provider="gemini", voice="Kore",
                       dest="faille_damour/ep01/voices", name="l02_marie_jeanne"))
    assert got["duration_s"] == 1.1 and got["path"].endswith("ep01/voices/l02_marie_jeanne.wav")
    bad = call(server, "tts_line", text="x", provider="polly", voice="Kore", dest="v", name="l")
    assert bad.is_error and "provider must be one of" in bad.content[0].text
    ledger = payload(call(server, "cost_ledger"))
    assert ledger["voice_lines"]["lines"] == 1 and ledger["voice_lines"]["by_provider"]["gemini"]["seconds"] == 1.1
    batch = payload(call(server, "tts_batch", dest="faille_damour/ep01/voices",
                         lines=[{"id": "l02", "who": "marie_jeanne", "text": "Pardon !"},
                                {"id": "l06", "who": "paloma", "text": "Tu souris."}],
                         provider="gemini", voices={"paloma": "Aoede"}))
    assert batch["skipped"] == ["l02_marie_jeanne"] and [m["name"] for m in batch["made"]] == ["l06_paloma"]
    assert json.loads(pathlib.Path(batch["durations_json"]).read_text()) == {"l02_marie_jeanne": 1.1, "l06_paloma": 1.1}


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



# ------------------------------------------------------------- plan 32 stage 5

NOW = "2026-10-06T12:00:00+00:00"


def _storyboard(ep, *, clip_shot=None, keyframe_shot=None):
    """A valid three-shot storyboard (the minimal documents the store checks); the keyframe and the
    clip are recorded on the shots named, the others have neither."""
    sha = "a" * 64

    def shot(n):
        shot_id = f"sh{n:02d}"
        assets = {"image": None, "video": None, "seed": None, "provider": None, "approved": False}
        if shot_id == keyframe_shot:
            assets["image"] = f"assets/shots/shot_{n:02d}.png"
        if shot_id == clip_shot:
            assets["video"] = f"assets/clips/shot_{n:02d}.mp4"
            assets["clip"] = {"state": "current", "link": "fal/test", "route": "paid", "clip_s": 2, "est_usd": 0.1,
                              "prompt_hash": sha, "image_sha256": sha, "cache_key": None, "generated_at": NOW}
        return {"shot_id": shot_id, "scene_id": "s01", "order": n, "framing": "medium_single",
                "camera_motion": "hold", "modifiers": [], "subject_tags": ["@char_kiwilo"],
                "action": "Something happens on screen.", "lines": [f"l{n:02d}"], "image_prompt": "a prompt",
                "negative_prompt": "no text", "prompt_override": None, "reference_images": [],
                "consistency": "references", "duration_s": 2.0, "keep_still": False,
                "motion": {"type": "hold", "zoom_from": 1.0, "zoom_to": 1.0, "pan": "none"}, "video_prompt": None,
                "assets": assets}

    return {"$schema": "storyboard_v1", "ep": ep, "shots": [shot(1), shot(2), shot(3)],
            "transitions": [{"after": "sh01", "type": "cut", "duration_s": 0.0}],
            "scenes": {"s01": {"source": "t1", "script_rev": 1, "stale": False}},
            "resolved_from": {}, "approved_at": None, "rev": 1, "created_at": NOW, "updated_at": NOW}


def _episode(backend, *, board=True, clip_shot=None, keyframe_shot=None):
    """A story with episode 1 in the store; returns (story_id, the episode's folder)."""
    stories = backend.story.stories
    story_id = payload(call(build_server(backend), "story_create", language="en", seed_text="A kiwi."))["story_id"]
    if board:
        stories.write_episode_doc(story_id, 1, "storyboard.json",
                                  _storyboard(1, clip_shot=clip_shot, keyframe_shot=keyframe_shot), now=NOW)
    return story_id, stories.episode_dir(story_id, 1, create=True)


def _clip(path, *, seconds=2):
    import subprocess

    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size=180x320:rate=16:duration={seconds}", "-pix_fmt", "yuv420p", str(path)], check=True)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed")
def test_the_episode_sheet_shows_a_clip_a_keyframe_and_a_grey_tile_for_nothing(backend):
    story_id, folder = _episode(backend, clip_shot="sh02", keyframe_shot="sh01")
    stories = backend.story.stories
    with open(stories.episode_asset_path(story_id, 1, "shots", "shot_01.png", create=True), "wb") as fh:
        fh.write(png_bytes(size=(180, 320)))
    _clip(stories.episode_asset_path(story_id, 1, "clips", "shot_02.mp4", create=True))
    server = build_server(backend)
    result = call(server, "episode_sheet", story_id=story_id, episode=1, columns=3, max_px=900)
    assert not result.is_error, result
    assert type(result.content[0]).__name__ == "ImageContent"
    answer = json.loads(result.content[1].text)
    assert [(s["id"], s["state"]) for s in answer["shots"]] == [("sh01", "keyframe"), ("sh02", "clip"),
                                                                 ("sh03", "missing")]
    assert answer["shots"][0]["source"] == "assets/shots/shot_01.png"
    assert answer["shots"][1]["source"] == "assets/clips/shot_02.mp4" and answer["shots"][2]["source"] is None
    assert max(answer["width"], answer["height"]) <= 900
    from PIL import Image

    sheet = backend.settings.outputs_dir + "/" + answer["path"]
    assert answer["path"].endswith("episodes/ep01/episode_sheet.png")
    with Image.open(sheet) as im:
        assert im.size == (answer["width"], answer["height"])
        # Three tiles on one row: the keyframe is red, the clip's frame is a test pattern, the third is grey.
        tile_w = (im.width - 6 * 4) // 3
        mid_y = 6 + (im.height - 12) // 3
        reds = im.getpixel((6 + tile_w // 2, mid_y))
        grey = im.getpixel((6 * 3 + 2 * tile_w + tile_w // 2, mid_y))
    assert reds[0] > 150 > reds[1] and grey == (70, 70, 70)


def test_the_episode_sheet_without_a_keyframe_or_clip_is_all_grey_and_refuses_without_a_storyboard(backend):
    story_id, folder = _episode(backend)
    server = build_server(backend)
    answer = json.loads(call(server, "episode_sheet", story_id=story_id, episode=1).content[-1].text)
    assert [s["state"] for s in answer["shots"]] == ["missing"] * 3
    bare, _folder = _episode(backend, board=False)
    refused = call(server, "episode_sheet", story_id=bare, episode=1)
    assert refused.is_error and "no storyboard yet" in refused.content[0].text
    unknown = call(server, "episode_sheet", story_id="000000000000", episode=1)
    assert unknown.is_error


def test_the_episode_export_refuses_a_missing_video_and_a_limit_over_the_ceiling(backend):
    story_id, folder = _episode(backend)
    server = build_server(backend)
    absent = call(server, "episode_export", story_id=story_id, episode=1)
    assert absent.is_error and "no episode_final.mp4 yet" in absent.content[0].text
    with open(folder + "/episode_final.mp4", "wb") as fh:
        fh.write(b"x" * 1000)
    over = call(server, "episode_export", story_id=story_id, episode=1, max_mib=51)
    assert over.is_error and "ceiling" in over.content[0].text


def test_a_video_under_the_limit_is_exported_as_it_is(backend):
    import os

    story_id, folder = _episode(backend)
    with open(folder + "/episode_final.mp4", "wb") as fh:
        fh.write(b"x" * 5000)
    answer = payload(call(build_server(backend), "episode_export", story_id=story_id, episode=1))
    assert answer["crf"] is None and answer["path"].endswith("episodes/ep01/episode_final.mp4")
    assert answer["download_hint"] == f"comfy_download({answer['path']})"
    assert "no copy needed" in answer["message"]
    assert not os.path.exists(folder + "/episode_share.mp4")


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed")
def test_a_big_video_is_re_encoded_down_the_ladder_and_the_original_is_kept(backend):
    import os
    import subprocess

    story_id, folder = _episode(backend)
    final = folder + "/episode_final.mp4"
    # Near-lossless and so a few MiB, though a plain test pattern: the first step of the ladder fits 1 MiB.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=25:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "0", "-pix_fmt", "yuv444p", "-c:a", "aac", "-shortest", final], check=True)
    original = os.path.getsize(final)
    assert original > 1024 * 1024
    answer = payload(call(build_server(backend), "episode_export", story_id=story_id, episode=1, max_mib=1))
    assert answer["crf"] == 23 and answer["size_mib"] <= 1.0
    assert answer["path"].endswith("episodes/ep01/episode_share.mp4")
    assert answer["download_hint"] == f"comfy_download({answer['path']})"
    share = backend.settings.outputs_dir + "/" + answer["path"]
    assert os.path.getsize(share) <= 1024 * 1024 and os.path.getsize(final) == original
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height", "-of",
                           "csv=p=0", share], capture_output=True, text=True, check=True).stdout
    assert "h264" in info and "aac" in info and "360,640" in info
    assert not [f for f in os.listdir(folder) if f.endswith(".part.mp4")]


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed")
def test_a_video_that_never_fits_keeps_the_smallest_copy_and_says_so(backend):
    import os
    import subprocess

    story_id, folder = _episode(backend)
    final = folder + "/episode_final.mp4"
    # Noise does not compress: even the lowest step stays over 1 MiB.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "nullsrc=s=640x640:r=25:d=2,geq=random(1)*255:128:128", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "5", "-pix_fmt", "yuv420p", final], check=True)
    answer = payload(call(build_server(backend), "episode_export", story_id=story_id, episode=1, max_mib=1))
    assert answer["crf"] == 32 and answer["size_mib"] > 1.0 and "Even the lowest quality step" in answer["message"]
    assert os.path.getsize(backend.settings.outputs_dir + "/" + answer["path"]) < os.path.getsize(final)
