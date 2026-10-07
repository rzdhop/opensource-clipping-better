"""RunPod Serverless ComfyUI (DEC-310): the local workflow templates on a
rented GPU, as a paid video link ``runpod/<template>``.

The adapter submits the rendered template and the keyframe inline with one
``POST /run``, journals the job the moment RunPod answers, polls ``/status``
until the job ends, decodes the base64 ``.mp4`` the worker returns and keeps
what RunPod billed (GPU seconds) in the result's meta. A journaled job is
resumed by its id and never submitted twice; a job that ends without a clip
is a settled failure. Fake transports only: nothing leaves the machine and
nothing is spent. Stdlib + pytest only (DEC-012).
"""

import base64
import json
import pathlib

import pytest

from clipping.providers import gencache, generation as gen, pricing, prompt_limits, runpod_comfyui, video
from clipping.providers.generation import GenRequest, NoRunnableLink, parse_generation_chain, run_generation_chain
from clipping.providers.local_comfyui import load_template
from clipping.providers.registry import Link, describe
from clipping.providers.transport import Response

ROOT = pathlib.Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16
ENV = {"RUNPOD_API_KEY": "rpa_fake", "RUNPOD_COMFY_ENDPOINT_ID": "ep123", "RUNPOD_GPU_USD_PER_HOUR": "1.58"}
TABLE = {("video", "runpod"): runpod_comfyui.RUNPOD_COMFY}
WAN = "runpod/i2v_wan22_14b_lightning"
RUN = "https://api.runpod.ai/v2/ep123/run"
STATUS = "https://api.runpod.ai/v2/ep123/status/job-1"


class FakeTransport:
    """Answers each request from a queue, in order, and records what was sent."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body})
        if not self.answers:
            raise AssertionError(f"unexpected call: {method} {url}")
        status, payload = self.answers.pop(0)
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode()
        return Response(status, {}, payload)

    def json(self, index):
        return json.loads(self.calls[index]["body"].decode())

    def urls(self):
        return [(c["method"], c["url"]) for c in self.calls]

    def posts(self):
        return [c for c in self.calls if c["method"] == "POST"]


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def time(self):
        return self.now


@pytest.fixture
def keyframe(tmp_path):
    path = tmp_path / "shot_01.png"
    path.write_bytes(PNG)
    return str(path)


def clip(keyframe, **kw):
    kw.setdefault("prompt", "the kiwi turns to the camera, slow push in")
    kw.setdefault("negative", "morphing, flicker")
    kw.setdefault("seed", 11)
    kw.setdefault("duration_s", 5)
    kw.setdefault("fps", 16)
    kw.setdefault("references", (keyframe,))
    kw.setdefault("out_dir", str(pathlib.Path(keyframe).parent / "out"))
    kw.setdefault("extra", {"name": "shot_01"})
    return GenRequest(kind="video", width=720, height=1280, **kw)


def link(spec):
    provider, model = spec.split("/", 1)
    return Link(provider, model)


def done(**fields):
    output = {"images": [{"filename": "clip_00001_.mp4", "type": "base64", "data": base64.b64encode(MP4).decode()}]}
    return (200, {"id": "job-1", "status": "COMPLETED", "output": output, "executionTime": 186000, "delayTime": 2000,
                  "workerId": "w-1", **fields})


def queue(*states):
    return [(200, {"id": "job-1", "status": "IN_QUEUE"})] + [(200, {"id": "job-1", "status": s}) for s in states]


def run_chain(spec, request, *, transport, cache=None, clock=None):
    clock = clock or Clock()
    log = []
    result, answered = run_generation_chain(
        "video", parse_generation_chain("video", spec), request, env=ENV, allow_paid=True, on_log=log.append,
        budget_check=lambda estimate, link: None, adapters=TABLE, transport=transport,
        sleep_fn=clock.sleep, time_fn=clock.time, cache=cache)
    return result, log


# ------------------------------------------------------------------ tables

def test_the_runpod_links_sell_what_their_templates_frame_rules_make():
    for name in runpod_comfyui.TEMPLATES:
        rule = load_template(name)["frame_rule"]
        assert video.CLIP_LENGTHS[f"runpod/{name}"] == tuple(rule["lengths"])
        assert video.supports_aspect(f"runpod/{name}", "9:16") and not video.supports_aspect(f"runpod/{name}", "16:9")
        assert video.AUDIO[f"runpod/{name}"] == "never" and f"runpod/{name}" in video.SEED_HONOURED
        assert pricing.price_for(Link("runpod", name)).unit == "second"


def test_runpod_is_a_paid_video_provider_keyed_by_api_key_and_endpoint_outside_the_shipped_chain():
    runpod = Link("runpod", "i2v_wan22_14b_lightning")
    assert gen.is_paid(runpod) and gen.missing_keys(runpod, {}) == ["RUNPOD_API_KEY", "RUNPOD_COMFY_ENDPOINT_ID"]
    assert gen.credentials_for(runpod, ENV) == ENV  # the optional GPU price rides along
    assert "runpod" in gen.KIND_PROVIDERS[gen.VIDEO] and "runpod" in gen.KIND_PROVIDERS[gen.IMAGE]  # DEC-312: images too
    shipped = [describe(l) for l in parse_generation_chain(gen.VIDEO, gen.DEFAULT_CHAINS[gen.VIDEO])]
    assert not any(label.startswith("runpod/") for label in shipped)
    assert [describe(l) for l in parse_generation_chain(gen.VIDEO, f"local/comfyui,{WAN}")] == ["local/comfyui", WAN]


def test_the_estimate_is_seconds_times_the_table_price_and_the_prompt_window_is_umt5s(keyframe):
    estimate = runpod_comfyui.RUNPOD_COMFY.estimate(link(WAN), clip(keyframe))
    assert estimate.unit == "second" and estimate.qty == 5 and estimate.est_usd == round(5 * 0.02, 4) and estimate.paid
    assert prompt_limits.limit_for(WAN, live={}).window_tokens == 512
    assert video.aspect_refusal("runpod/i2v_ltx2", "16:9").endswith("on RunPod as locally")


# ------------------------------------------------------------------ generate

def test_a_clip_is_submitted_once_journaled_polled_decoded_and_priced(keyframe, tmp_path):
    cache = gencache.GenCache(tmp_path / "gen", book=lambda entry: None)
    transport = FakeTransport(queue("IN_PROGRESS") + [done()])
    request = clip(keyframe)
    result, log = run_chain(WAN, request, transport=transport, cache=cache)

    assert transport.urls() == [("POST", RUN), ("GET", STATUS), ("GET", STATUS)]  # one submit, polled to the end
    assert all(c["headers"]["Authorization"] == "Bearer rpa_fake" for c in transport.calls)
    body = transport.json(0)["input"]
    graph = body["workflow"]
    template = load_template("i2v_wan22_14b_lightning")
    rule = template["frame_rule"]
    assert body["images"][0]["name"].startswith("rzdhop_") and body["images"][0]["image"].startswith("data:image/png;base64,")
    assert graph[template["image_node"]]["inputs"]["image"] == body["images"][0]["name"]
    wan = next(n for n in graph.values() if n["class_type"] == "WanImageToVideo")["inputs"]
    assert (wan["width"], wan["height"], wan["length"]) == (rule["width"], rule["height"], 81)
    assert all(n["inputs"]["noise_seed"] == 11 for n in graph.values()
               if n["class_type"] == "KSamplerAdvanced" and n["inputs"]["add_noise"] == "enable")
    assert "{{" not in json.dumps(graph)

    assert pathlib.Path(result.paths[0]).read_bytes() == MP4 and result.paths[0].endswith("shot_01.mp4")
    assert result.paid and result.est_cost == 0.1  # the ledger keeps the table's estimate
    assert result.meta["gpu_seconds"] == 188.0 and result.meta["billed_usd"] == round(188 * 1.58 / 3600, 4)
    assert result.meta["job_id"] == "job-1" and result.meta["worker_id"] == "w-1" and result.meta["has_audio"] is False
    assert result.meta["seed_honoured"] is True and result.meta["clip_s"] == 5 and result.meta["frames"] == 81
    entry = cache.lookup(cache.key("video", link(WAN), request))
    assert entry["state"] == gencache.DONE and entry["request"]["request_id"] == "job-1"
    assert entry["request"]["status_url"] == STATUS
    assert any("188 GPU-s" in line and "$0.083" in line for line in log)


def test_without_the_gpu_price_the_real_cost_is_unknown_not_guessed(keyframe):
    env = {k: v for k, v in ENV.items() if k != "RUNPOD_GPU_USD_PER_HOUR"}
    log = []
    result = runpod_comfyui.RUNPOD_COMFY.generate(
        link(WAN), clip(keyframe), credentials=env, on_log=log.append, transport=FakeTransport([(200, {"id": "job-1"}), done()]),
        sleep_fn=lambda s: None, time_fn=lambda: 0.0)
    assert result.meta["billed_usd"] is None and any("RUNPOD_GPU_USD_PER_HOUR" in line for line in log)


def test_a_failed_job_is_request_failed_and_stays_booked_without_a_second_submit(keyframe, tmp_path):
    cache = gencache.GenCache(tmp_path / "gen", book=lambda entry: None)
    transport = FakeTransport(queue() + [(200, {"id": "job-1", "status": "FAILED", "error": "CUDA out of memory"})])
    request = clip(keyframe)
    with pytest.raises(NoRunnableLink) as excinfo:
        run_chain(WAN, request, transport=transport, cache=cache)
    assert "CUDA out of memory" in str(excinfo.value) and len(transport.posts()) == 1
    assert cache.lookup(cache.key("video", link(WAN), request))["state"] == gencache.FAILED


def test_a_job_that_ends_without_an_mp4_names_the_save_node(keyframe, tmp_path):
    cache = gencache.GenCache(tmp_path / "gen", book=lambda entry: None)
    answer = done(output={"images": [], "errors": ["Node 108 produced unhandled output keys: ['gifs']"]})
    with pytest.raises(NoRunnableLink) as excinfo:
        run_chain(WAN, clip(keyframe), transport=FakeTransport([(200, {"id": "job-1"}), answer]), cache=cache)
    assert "SaveVideo" in str(excinfo.value) and "gifs" in str(excinfo.value)


def test_a_job_past_its_poll_budget_is_resumed_next_run_without_a_second_submit(keyframe, tmp_path):
    cache = gencache.GenCache(tmp_path / "gen", book=lambda entry: None)
    request = clip(keyframe)
    first = FakeTransport([(200, {"id": "job-1", "status": "IN_QUEUE"})]
                          + [(200, {"id": "job-1", "status": "IN_PROGRESS"})] * 400)
    with pytest.raises(NoRunnableLink) as excinfo:
        run_chain(WAN, request, transport=first, cache=cache)
    assert "kept for the next run" in str(excinfo.value)
    assert cache.lookup(cache.key("video", link(WAN), request))["state"] == gencache.SUBMITTED

    second = FakeTransport([(200, {"id": "job-1", "status": "IN_PROGRESS"}), done()])
    result, log = run_chain(WAN, request, transport=second, cache=cache)
    assert second.posts() == [] and second.answers == [] and second.urls()[0] == ("GET", STATUS)
    assert result.meta["resumed"] is True and pathlib.Path(result.paths[0]).read_bytes() == MP4
    assert any("not submitted again" in line for line in log)


def test_a_journaled_job_runpod_no_longer_knows_is_voided_and_sent_once_more(keyframe, tmp_path):
    """A 404 on the job's own status URL proves it never ran (gencache.resume_verdict): the
    booking is released and the same request is submitted afresh, once."""
    cache = gencache.GenCache(tmp_path / "gen", book=lambda entry: None)
    request = clip(keyframe)
    with pytest.raises(NoRunnableLink):
        run_chain(WAN, request, transport=FakeTransport([(200, {"id": "job-1", "status": "IN_QUEUE"})]
                                                      + [(200, {"id": "job-1", "status": "IN_PROGRESS"})] * 400), cache=cache)
    second = FakeTransport([(404, {"error": "no such job"}), (200, {"id": "job-2", "status": "IN_QUEUE"}),
                            done(id="job-2")])
    result, log = run_chain(WAN, request, transport=second, cache=cache)
    assert [u for u in second.urls() if u[0] == "POST"] == [("POST", RUN)]
    assert result.meta["job_id"] == "job-2"


# ------------------------------------------------------------------- refusal

@pytest.mark.parametrize("spec,change,message", [
    (WAN, {"duration_s": None}, "duration_s"),
    (WAN, {"seed": None}, "seed"),
    (WAN, {"duration_s": 6}, "2, 3, 4, 5"),
    ("runpod/i2v_ltx2", {"duration_s": 5}, "2, 3, 4"),
    (WAN, {"references": ()}, "one keyframe"),
    (WAN, {"fps": 24}, "16 fps"),
    (WAN, {"extra": {"aspect": "16:9"}}, "9:16 clips only"),
    ("runpod/i2v_nope", {}, "no workflow template"),
])
def test_a_clip_that_cannot_be_bought_as_asked_is_refused_before_sending(keyframe, spec, change, message):
    transport = FakeTransport([])
    with pytest.raises(ValueError) as excinfo:
        runpod_comfyui.RUNPOD_COMFY.generate(link(spec), clip(keyframe, **change), credentials=ENV, on_log=print,
                                             transport=transport)
    assert message in str(excinfo.value) and transport.calls == []


def test_a_missing_keyframe_file_is_refused_before_sending(keyframe):
    transport = FakeTransport([])
    with pytest.raises(ValueError, match="not found"):
        runpod_comfyui.RUNPOD_COMFY.generate(link(WAN), clip(keyframe, references=(keyframe + ".missing",)),
                                             credentials=ENV, on_log=print, transport=transport)
    assert transport.calls == []


# ---------------------------------------------------------- probe and key check

def test_the_probe_and_the_key_check_ask_health_and_count_workers():
    health = (200, {"jobs": {}, "workers": {"idle": 0, "ready": 1, "running": 0, "throttled": 2}})
    ok, note = runpod_comfyui.RUNPOD_COMFY.probe(link(WAN), credentials=ENV, transport=FakeTransport([health]))
    assert ok and "ready=1" in note and "throttled=2" in note

    checked = video.check_key(link(WAN), ENV, transport=FakeTransport([health]))
    assert checked["status"] == "ok" and checked["endpoint"] == "ep123" and "ready 1" in checked["text"]
    refused = video.check_key(link(WAN), ENV, transport=FakeTransport([(401, {"error": "invalid api key"})]))
    assert refused["status"] == "bad_key" and "RUNPOD_API_KEY" in refused["text"]
    missing = video.check_key(link(WAN), ENV, transport=FakeTransport([(404, {"error": "endpoint not found"})]))
    assert missing["status"] == "no_model"


def test_no_sdk_is_imported_in_the_adapter():
    source = (ROOT / "clipping" / "providers" / "runpod_comfyui.py").read_text(encoding="utf-8")
    assert "import runpod" not in source and "import requests" not in source


def test_docker_compose_forwards_the_runpod_names_into_the_container():
    """The compose file lists the container's environment name by name: a
    RUNPOD_* line in the host's .env that is not forwarded here never reaches
    the backend (the LLM_CUSTOM_* names were lost that way once)."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    # Plan 32 stage 6: the voice lines' three names too (runpod/tts_chatterbox); plan 32 close-out: the image
    # endpoint's three (DEC-312 -- found missing on the deployed box, every image went to the video endpoint).
    for name in ("RUNPOD_API_KEY", "RUNPOD_COMFY_ENDPOINT_ID", "RUNPOD_GPU_USD_PER_HOUR", "RUNPOD_AUDIO_ENDPOINT_ID",
                 "RUNPOD_AUDIO_API_KEY", "RUNPOD_AUDIO_GPU_USD_PER_HOUR", "RUNPOD_IMAGE_ENDPOINT_ID",
                 "RUNPOD_IMAGE_API_KEY", "RUNPOD_IMAGE_GPU_USD_PER_HOUR"):
        assert f"- {name}=${{{name}:-}}" in compose, f"{name} is not forwarded by docker-compose.yml"
