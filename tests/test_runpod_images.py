"""RunPod Serverless ComfyUI for images (DEC-312): the local image templates
as paid image and image-edit links ``runpod/<template>`` on the image
endpoint (``RUNPOD_IMAGE_ENDPOINT_ID``, else the video one), the references
of an edit inline, journaled and resumed like a clip, the returned PNG
written and the GPU seconds priced. Fake transports only: nothing leaves the
machine and nothing is spent.
"""

import base64
import json
import pathlib

import pytest

from clipping.providers import gencache, generation as gen, pricing, runpod_images
from clipping.providers.generation import GenRequest, NoRunnableLink, parse_generation_chain, run_generation_chain
from clipping.providers.registry import Link, describe
from clipping.providers.transport import Response

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
ENV = {"RUNPOD_API_KEY": "rpa_fake", "RUNPOD_COMFY_ENDPOINT_ID": "vid1", "RUNPOD_GPU_USD_PER_HOUR": "3.49",
       "RUNPOD_IMAGE_ENDPOINT_ID": "img1", "RUNPOD_IMAGE_GPU_USD_PER_HOUR": "1.58"}
TABLE = {("image", "runpod"): runpod_images.RUNPOD_IMAGES, ("image_edit", "runpod"): runpod_images.RUNPOD_IMAGES}
T2I = "runpod/t2i_flux2_klein"
EDIT = "runpod/edit_flux2_klein_multiref"


class FakeTransport:
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


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def time(self):
        return self.now


def done(filename="ComfyUI_00001_.png", data=PNG, **fields):
    output = {"images": [{"filename": filename, "type": "base64", "data": base64.b64encode(data).decode()}]}
    return (200, {"id": "job-1", "status": "COMPLETED", "output": output, "executionTime": 6000, "delayTime": 4000,
                  "workerId": "w-1", **fields})


def image(tmp_path, **kw):
    kw.setdefault("prompt", "a kiwi detective, flat colours")
    kw.setdefault("negative", "text, watermark")
    kw.setdefault("seed", 11)
    kw.setdefault("out_dir", str(tmp_path / "out"))
    kw.setdefault("extra", {"name": "kiwi_portrait"})
    return GenRequest(kind=kw.pop("kind", "image"), width=832, height=1216, **kw)


def link(spec):
    provider, model = spec.split("/", 1)
    return Link(provider, model)


def run_chain(kind, spec, request, *, transport, env=ENV, cache=None, clock=None):
    clock = clock or Clock()
    log = []
    result, _answered = run_generation_chain(
        kind, parse_generation_chain(kind, spec), request, env=env, allow_paid=True, on_log=log.append,
        budget_check=lambda estimate, link: None, adapters=TABLE, transport=transport,
        sleep_fn=clock.sleep, time_fn=clock.time, cache=cache)
    return result, log


# ------------------------------------------------------------------ tables

def test_runpod_serves_images_and_edits_priced_per_image_with_the_image_endpoint_optional():
    t2i, edit = link(T2I), link(EDIT)
    assert gen.is_paid(t2i) and gen.is_paid(edit)
    assert gen.missing_keys(t2i, {}) == ["RUNPOD_API_KEY", "RUNPOD_COMFY_ENDPOINT_ID"]  # the image endpoint is optional
    assert gen.credentials_for(t2i, ENV) == ENV
    assert "runpod" in gen.KIND_PROVIDERS[gen.IMAGE] and "runpod" in gen.KIND_PROVIDERS[gen.IMAGE_EDIT]
    assert pricing.price_for(t2i).unit == "image" and pricing.price_for(edit).usd > pricing.price_for(t2i).usd
    estimate = runpod_images.RUNPOD_IMAGES.estimate(t2i, image(pathlib.Path("/tmp")))
    assert estimate.unit == "image" and estimate.qty == 1 and estimate.paid and estimate.est_usd == 0.01
    assert [describe(l) for l in parse_generation_chain(gen.IMAGE, f"{T2I},fal/flux-schnell")] == [T2I, "fal/flux-schnell"]
    assert runpod_images.image_endpoint(ENV) == "img1"
    assert runpod_images.image_endpoint({"RUNPOD_COMFY_ENDPOINT_ID": "vid1"}) == "vid1"
    assert runpod_images.image_key(ENV) == "rpa_fake" and runpod_images.image_key({**ENV, "RUNPOD_IMAGE_API_KEY": "k2"}) == "k2"


# ---------------------------------------------------------------- generate

def test_a_text_to_image_job_goes_to_the_image_endpoint_journaled_polled_and_priced(tmp_path):
    cache = gencache.GenCache(tmp_path / "gen", book=lambda entry: None)
    transport = FakeTransport([(200, {"id": "job-1", "status": "IN_QUEUE"}),
                               (200, {"id": "job-1", "status": "IN_PROGRESS"}), done()])
    request = image(tmp_path)
    result, log = run_chain("image", T2I, request, transport=transport, cache=cache)
    assert transport.urls() == [("POST", "https://api.runpod.ai/v2/img1/run"),
                                ("GET", "https://api.runpod.ai/v2/img1/status/job-1"),
                                ("GET", "https://api.runpod.ai/v2/img1/status/job-1")]
    body = transport.json(0)["input"]
    assert body["images"] == []
    graph = body["workflow"]
    assert "{{" not in json.dumps(graph)
    assert any(n["inputs"].get("text") == "a kiwi detective, flat colours" for n in graph.values())
    assert any(n["inputs"].get("width") == 832 and n["inputs"].get("height") == 1216 for n in graph.values())
    assert pathlib.Path(result.paths[0]).read_bytes() == PNG and result.paths[0].endswith("kiwi_portrait.png")
    assert result.seed == 11 and result.paid and result.est_cost == 0.01
    assert result.meta["gpu_seconds"] == 10.0 and result.meta["billed_usd"] == round(10 * 1.58 / 3600, 4)
    assert result.meta["endpoint"] == "img1" and result.meta["template"] == "t2i_flux2_klein"
    entry = cache.lookup(cache.key("image", link(T2I), request))
    assert entry["state"] == gencache.DONE and entry["request"]["request_id"] == "job-1"
    assert any("10 GPU-s" in line and "$0.004" in line for line in log)


def test_an_edit_inlines_each_reference_once_and_fills_the_four_slots(tmp_path):
    refs = []
    for n, tail in enumerate((b"a", b"b")):
        path = tmp_path / f"ref{n}.png"
        path.write_bytes(PNG + tail)
        refs.append(str(path))
    transport = FakeTransport([(200, {"id": "job-1"}), done()])
    request = image(tmp_path, kind="image_edit", references=tuple(refs), extra={"name": "kiwi_turnaround"})
    result, _log = run_chain("image_edit", EDIT, request, transport=transport)
    body = transport.json(0)["input"]
    names = [i["name"] for i in body["images"]]
    assert len(names) == 2 and all(n.startswith("rzdhop_") for n in names)
    loads = [n["inputs"]["image"] for n in body["workflow"].values() if n["class_type"] == "LoadImage"]
    assert loads == [names[0], names[1], names[1], names[1]]
    assert result.paths[0].endswith("kiwi_turnaround.png") and result.meta["references"] == 2


def test_without_an_image_endpoint_images_run_on_the_video_endpoint_at_its_price(tmp_path):
    env = {k: v for k, v in ENV.items() if not k.startswith("RUNPOD_IMAGE")}
    transport = FakeTransport([(200, {"id": "job-1"}), done()])
    result, _log = run_chain("image", T2I, image(tmp_path), transport=transport, env=env)
    assert transport.urls()[0] == ("POST", "https://api.runpod.ai/v2/vid1/run")
    assert result.meta["billed_usd"] == round(10 * 3.49 / 3600, 4)


def test_an_edit_link_refuses_a_plain_image_request_and_a_missing_reference_before_sending(tmp_path):
    transport = FakeTransport([])
    with pytest.raises(NoRunnableLink, match="not image"):
        run_chain("image", EDIT, image(tmp_path), transport=transport)
    with pytest.raises(NoRunnableLink, match="needs reference images"):
        run_chain("image_edit", EDIT, image(tmp_path, kind="image_edit"), transport=transport)
    with pytest.raises(NoRunnableLink, match="not found"):
        run_chain("image_edit", EDIT, image(tmp_path, kind="image_edit", references=(str(tmp_path / "no.png"),)),
                  transport=transport)
    assert transport.calls == []


def test_a_failed_job_stays_booked_and_a_job_without_an_image_names_the_save_node(tmp_path):
    cache = gencache.GenCache(tmp_path / "gen", book=lambda entry: None)
    transport = FakeTransport([(200, {"id": "job-1"}),
                               (200, {"id": "job-1", "status": "FAILED", "error": "CUDA out of memory"})])
    request = image(tmp_path)
    with pytest.raises(NoRunnableLink, match="CUDA out of memory"):
        run_chain("image", T2I, request, transport=transport, cache=cache)
    assert cache.lookup(cache.key("image", link(T2I), request))["state"] == gencache.FAILED
    assert len([c for c in transport.calls if c["method"] == "POST"]) == 1
    empty = done(output={"images": [], "errors": ["Node 9 produced unhandled output keys: ['gifs']"]})
    with pytest.raises(NoRunnableLink, match="core SaveImage node"):
        run_chain("image", T2I, image(tmp_path, seed=12), transport=FakeTransport([(200, {"id": "job-2"}), empty]))


def test_a_journaled_job_is_resumed_never_submitted_twice(tmp_path):
    cache = gencache.GenCache(tmp_path / "gen", book=lambda entry: None)
    request = image(tmp_path)
    clock = Clock()
    # First run: submitted, then the poll budget runs out (the job stays journaled).
    first = FakeTransport([(200, {"id": "job-1"})] + [(200, {"id": "job-1", "status": "IN_PROGRESS"})] * 400)
    with pytest.raises(NoRunnableLink):
        run_chain("image", T2I, request, transport=first, cache=cache, clock=clock)
    assert cache.lookup(cache.key("image", link(T2I), request))["state"] == gencache.SUBMITTED
    # Second run: no POST, the same job followed to its end.
    second = FakeTransport([done()])
    result, log = run_chain("image", T2I, request, transport=second, cache=cache)
    assert second.urls() == [("GET", "https://api.runpod.ai/v2/img1/status/job-1")]
    assert result.meta["job_id"] == "job-1" and any("not submitted again" in line for line in log)


def test_the_probe_reads_the_image_endpoints_health():
    transport = FakeTransport([(200, {"workers": {"idle": 1, "ready": 1, "running": 0, "throttled": 0}})])
    ok, text = runpod_images.RUNPOD_IMAGES.probe(link(T2I), credentials=ENV, transport=transport)
    assert ok and "img1" in text and transport.urls() == [("GET", "https://api.runpod.ai/v2/img1/health")]


# --------------------------------------------------------------- the profile

def test_the_own_gpu_profile_puts_the_runpod_links_first_for_both_kinds_with_fal_behind():
    from clipping.aistory import defaults, media_policy

    story = {"story_id": "0123456789ab",
             "generation_profile": {**defaults.quality_generation_profile(), "budget_profile": "own_gpu"}}
    labels = lambda chain: [describe(l) for l in chain]  # noqa: E731
    for role in ("sheet", "plate", "prop"):
        assert labels(media_policy.role_chain(role, gen.IMAGE, {}, story)) == [T2I, "fal/seedream-4.5"]
        assert labels(media_policy.role_chain(role, gen.IMAGE_EDIT, {}, story)) == [EDIT, "fal/seedream-4.5-edit"]
    assert labels(media_policy.role_chain("keyframe", gen.IMAGE_EDIT, {}, story)) == [EDIT, "fal/seedream-4.5-edit"]
    assert "own_gpu" in defaults.BUDGET_PROFILES
