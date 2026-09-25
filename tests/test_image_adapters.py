"""The image adapters (spec 8.1 / 8.7): each speaks to its provider through
the injected transport, writes the image where it was told, never imports an
SDK at module scope, and raises errors the chain runner can classify. Paid
calls are gated by the runner (DEC-097), so with allow_paid off the transport
is never touched."""

import ast
import base64
import json
import pathlib

import pytest

from clipping.providers import errors, generation, images
from clipping.providers.generation import GenRequest, NoRunnableLink, parse_generation_chain, run_generation_chain
from clipping.providers.registry import Link
from clipping.providers.transport import APIConnectionError, Response

ROOT = pathlib.Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


class FakeTransport:
    """Answers each request from a queue and records what was sent."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}),
                           "body": body, "timeout": timeout})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, payload = answer
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode()
        return Response(status, {}, payload)

    def json(self, index):
        return json.loads(self.calls[index]["body"].decode())


@pytest.fixture
def ref(tmp_path):
    path = tmp_path / "portrait.png"
    path.write_bytes(PNG)
    return str(path)


def request(kind="image", **kw):
    kw.setdefault("prompt", "an anthropomorphic kiwi in a linen shirt")
    kw.setdefault("out_dir", kw.pop("out_dir", ""))
    return GenRequest(kind=kind, **kw)


# ---------------------------------------------------------------- registry

def test_the_adapters_register_for_their_kinds_only():
    assert generation.adapter_for("image", "cloudflare") is images.CLOUDFLARE
    assert generation.adapter_for("image", "pollinations") is images.POLLINATIONS
    assert generation.adapter_for("image", "gemini") is images.GEMINI
    assert generation.adapter_for("image_edit", "gemini") is images.GEMINI
    assert generation.adapter_for("image", "fal") is images.FAL
    assert generation.adapter_for("image_edit", "fal") is images.FAL
    assert generation.adapter_for("image", "openai") is images.OPENAI
    assert generation.adapter_for("image_edit", "openai") is images.OPENAI
    assert generation.adapter_for("video", "fal") is None, "video adapters arrive in phase 6"
    assert generation.adapter_for("image_edit", "cloudflare") is None


def test_no_sdk_is_imported_at_module_scope():
    tree = ast.parse((ROOT / "clipping" / "providers" / "images.py").read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top.add(node.module.split(".")[0])
    assert "openai" not in top and "google" not in top and "requests" not in top and "httpx" not in top


def test_the_model_ids_are_the_ones_the_spec_lists():
    assert images.GEMINI_MODELS == {"nano-banana-2-lite": "gemini-3.1-flash-lite-image", "nano-banana-2": "gemini-3.1-flash-image"}
    assert images.FAL_APPS["flux-schnell"] == "fal-ai/flux/schnell"
    assert images.FAL_APPS["seedream-4-edit"] == "fal-ai/bytedance/seedream/v4/edit"
    assert images.FAL_APPS["flux-kontext-pro"] == "fal-ai/flux-pro/kontext"
    assert images.FAL_APPS["seedance-1-pro-fast"] == "fal-ai/bytedance/seedance/v1/pro/fast/image-to-video"
    assert images.CLOUDFLARE_MODELS["flux-1-schnell"] == "@cf/black-forest-labs/flux-1-schnell"
    assert images.OPENAI_MODELS["gpt-image-2-low"] == ("gpt-image-2", "low")


# --------------------------------------------------------------- cloudflare

def test_cloudflare_posts_the_prompt_and_writes_the_decoded_image(tmp_path):
    transport = FakeTransport([(200, {"success": True, "result": {"image": base64.b64encode(JPG).decode()}})])
    result = images.CLOUDFLARE.generate(
        Link("cloudflare", "flux-1-schnell"), request(out_dir=str(tmp_path), seed=42, extra={"name": "test"}),
        credentials={"CLOUDFLARE_API_TOKEN": "tok", "CLOUDFLARE_ACCOUNT_ID": "acc"}, on_log=lambda *a: None,
        transport=transport)
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.cloudflare.com/client/v4/accounts/acc/ai/run/@cf/black-forest-labs/flux-1-schnell"
    assert call["headers"]["Authorization"] == "Bearer tok"
    assert transport.json(0) == {"prompt": "an anthropomorphic kiwi in a linen shirt", "steps": 4, "seed": 42}
    assert result.provider == "cloudflare" and result.model == "flux-1-schnell" and result.seed == 42
    assert pathlib.Path(result.paths[0]).read_bytes() == JPG
    assert result.paths[0].endswith("test.jpg")
    assert images.CLOUDFLARE.estimate(Link("cloudflare", "flux-1-schnell"), request()) is None


def test_cloudflare_refuses_an_unsuccessful_answer_with_its_message(tmp_path):
    transport = FakeTransport([(200, {"success": False, "errors": [{"message": "prompt too long"}]})])
    with pytest.raises(errors.ProviderError) as excinfo:
        images.CLOUDFLARE.generate(Link("cloudflare", "flux-1-schnell"), request(out_dir=str(tmp_path)),
                                   credentials={"CLOUDFLARE_API_TOKEN": "t", "CLOUDFLARE_ACCOUNT_ID": "a"},
                                   on_log=lambda *a: None, transport=transport)
    assert "prompt too long" in str(excinfo.value)


# ------------------------------------------------------------- pollinations

def test_pollinations_builds_the_url_and_sends_the_key_only_when_present(tmp_path):
    transport = FakeTransport([(200, JPG), (200, JPG)])
    result = images.POLLINATIONS.generate(Link("pollinations", "flux"), request(out_dir=str(tmp_path), seed=7, width=1080, height=1920),
                                          credentials={}, on_log=lambda *a: None, transport=transport)
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"].startswith("https://image.pollinations.ai/prompt/an%20anthropomorphic%20kiwi")
    assert "model=flux" in call["url"] and "width=1080" in call["url"] and "height=1920" in call["url"]
    assert "seed=7" in call["url"] and "nologo=true" in call["url"]
    assert "Authorization" not in call["headers"]
    assert pathlib.Path(result.paths[0]).read_bytes() == JPG
    images.POLLINATIONS.generate(Link("pollinations", "flux"), request(out_dir=str(tmp_path)),
                                 credentials={"POLLINATIONS_API_KEY": "pk"}, on_log=lambda *a: None, transport=transport)
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer pk"


# ------------------------------------------------------------------ gemini

def gemini_answer(mime="image/png", data=PNG):
    return {"candidates": [{"content": {"parts": [
        {"text": "here you go"},
        {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}},
    ]}}]}


def test_gemini_edits_with_reference_images_over_rest(tmp_path, ref):
    transport = FakeTransport([(200, gemini_answer())])
    result = images.GEMINI.generate(
        Link("gemini", "nano-banana-2-lite"), request("image_edit", out_dir=str(tmp_path), references=(ref,), width=1080, height=1920),
        credentials={"GOOGLE_API_KEY": "gk"}, on_log=lambda *a: None, transport=transport)
    call = transport.calls[0]
    assert call["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite-image:generateContent"
    assert call["headers"]["x-goog-api-key"] == "gk"
    body = transport.json(0)
    parts = body["contents"][0]["parts"]
    assert parts[0] == {"text": "an anthropomorphic kiwi in a linen shirt"}
    assert parts[1]["inline_data"]["mime_type"] == "image/png"
    assert base64.b64decode(parts[1]["inline_data"]["data"]) == PNG
    assert body["generationConfig"]["responseModalities"] == ["IMAGE"]
    assert body["generationConfig"]["imageConfig"]["aspectRatio"] == "9:16"
    assert pathlib.Path(result.paths[0]).read_bytes() == PNG and result.paths[0].endswith(".png")
    assert result.model == "nano-banana-2-lite"
    est = images.GEMINI.estimate(Link("gemini", "nano-banana-2-lite"), request())
    assert est.est_usd == 0.0336 and est.paid is True


def test_gemini_reports_an_answer_without_an_image(tmp_path):
    transport = FakeTransport([(200, {"candidates": [{"content": {"parts": [{"text": "I cannot draw that"}]}, "finishReason": "SAFETY"}]})])
    with pytest.raises(errors.ProviderError) as excinfo:
        images.GEMINI.generate(Link("gemini", "nano-banana-2-lite"), request(out_dir=str(tmp_path)),
                               credentials={"GOOGLE_API_KEY": "gk"}, on_log=lambda *a: None, transport=transport)
    assert "no image" in str(excinfo.value) and "SAFETY" in str(excinfo.value)


def test_a_retired_gemini_image_model_is_swapped_inside_gemini_by_the_runner(tmp_path):
    transport = FakeTransport([
        (404, {"error": {"message": "models/gemini-3.1-flash-lite-image is not found for API version v1beta"}}),
        (200, gemini_answer()),
    ])
    (result, answered), log = run_edit("gemini/nano-banana-2-lite", transport, tmp_path, allow_paid=True)
    assert answered == Link("gemini", "nano-banana-2")
    assert transport.calls[1]["url"].endswith("gemini-3.1-flash-image:generateContent")
    assert any("↪" in line for line in log)


def run_edit(chain, transport, tmp_path, *, allow_paid, env=None, budget_check=lambda e, l: None):
    log = []
    env = env if env is not None else {"GOOGLE_API_KEY": "gk", "FAL_KEY": "fk", "OPENAI_API_KEY": "ok",
                                       "LOCAL_COMFYUI_URL": "http://127.0.0.1:8188"}
    out = run_generation_chain("image_edit", parse_generation_chain("image_edit", chain),
                               request("image_edit", out_dir=str(tmp_path)), env=env, allow_paid=allow_paid,
                               on_log=log.append, budget_check=budget_check, transport=transport,
                               sleep_fn=lambda s: None)
    return out, log


# --------------------------------------------------------------------- fal

def fal_queue(app, images_url="https://v3.fal.media/files/x/out.png", seed=998877):
    base = f"https://queue.fal.run/{app}/requests/req-1"
    return [
        (200, {"request_id": "req-1", "status_url": base + "/status", "response_url": base}),
        (200, {"status": "IN_QUEUE", "queue_position": 2}),
        (200, {"status": "IN_PROGRESS"}),
        (200, {"status": "COMPLETED"}),
        (200, {"images": [{"url": images_url, "width": 1080, "height": 1920, "content_type": "image/png"}], "seed": seed}),
        (200, PNG),
    ]


def test_fal_seedream_edit_submits_polls_and_fetches_the_image(tmp_path, ref):
    transport = FakeTransport(fal_queue("fal-ai/bytedance/seedream/v4/edit"))
    slept = []
    result = images.FAL.generate(
        Link("fal", "seedream-4-edit"), request("image_edit", out_dir=str(tmp_path), references=(ref,), seed=5),
        credentials={"FAL_KEY": "fk"}, on_log=lambda *a: None, transport=transport, sleep_fn=slept.append)
    submit = transport.calls[0]
    assert submit["method"] == "POST" and submit["url"] == "https://queue.fal.run/fal-ai/bytedance/seedream/v4/edit"
    assert submit["headers"]["Authorization"] == "Key fk"
    body = transport.json(0)
    assert body["prompt"] == "an anthropomorphic kiwi in a linen shirt"
    assert body["image_urls"][0].startswith("data:image/png;base64,")
    assert body["image_size"] == {"width": 1080, "height": 1920} and body["num_images"] == 1 and body["seed"] == 5
    assert [c["url"] for c in transport.calls[1:4]] == ["https://queue.fal.run/fal-ai/bytedance/seedream/v4/edit/requests/req-1/status"] * 3
    assert transport.calls[4]["url"] == "https://queue.fal.run/fal-ai/bytedance/seedream/v4/edit/requests/req-1"
    assert transport.calls[5]["url"] == "https://v3.fal.media/files/x/out.png"
    assert pathlib.Path(result.paths[0]).read_bytes() == PNG
    assert result.seed == 998877 and result.meta["request_id"] == "req-1"
    assert len(slept) == 3


def test_fal_flux_schnell_and_kontext_send_their_own_inputs(tmp_path, ref):
    transport = FakeTransport(fal_queue("fal-ai/flux/schnell"))
    images.FAL.generate(Link("fal", "flux-schnell"), request(out_dir=str(tmp_path), width=1080, height=1920),
                        credentials={"FAL_KEY": "fk"}, on_log=lambda *a: None, transport=transport, sleep_fn=lambda s: None)
    body = transport.json(0)
    assert body["image_size"] == {"width": 1080, "height": 1920} and "image_urls" not in body
    transport = FakeTransport(fal_queue("fal-ai/flux-pro/kontext"))
    images.FAL.generate(Link("fal", "flux-kontext-pro"), request("image_edit", out_dir=str(tmp_path), references=(ref,)),
                        credentials={"FAL_KEY": "fk"}, on_log=lambda *a: None, transport=transport, sleep_fn=lambda s: None)
    body = transport.json(0)
    assert body["image_url"].startswith("data:image/png;base64,") and body["aspect_ratio"] == "9:16"


def test_fal_gives_up_after_its_polling_budget(tmp_path):
    answers = [(200, {"request_id": "r", "status_url": "https://queue.fal.run/a/requests/r/status", "response_url": "https://queue.fal.run/a/requests/r"})]
    answers += [(200, {"status": "IN_PROGRESS"})] * 200
    transport = FakeTransport(answers)
    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    with pytest.raises(errors.ProviderError) as excinfo:
        images.FAL.generate(Link("fal", "flux-schnell"), request(out_dir=str(tmp_path)),
                            credentials={"FAL_KEY": "fk"}, on_log=lambda *a: None, transport=transport,
                            sleep_fn=sleep, time_fn=lambda: clock["now"])
    assert "still IN_PROGRESS" in str(excinfo.value)


def test_fal_reports_a_failed_request_with_the_providers_message(tmp_path):
    transport = FakeTransport([
        (200, {"request_id": "r", "status_url": "https://queue.fal.run/a/requests/r/status", "response_url": "https://queue.fal.run/a/requests/r"}),
        (200, {"status": "FAILED", "error": "content policy"}),
    ])
    with pytest.raises(errors.ProviderError) as excinfo:
        images.FAL.generate(Link("fal", "flux-schnell"), request(out_dir=str(tmp_path)),
                            credentials={"FAL_KEY": "fk"}, on_log=lambda *a: None, transport=transport, sleep_fn=lambda s: None)
    assert "content policy" in str(excinfo.value)


def test_fal_estimates_from_the_price_table_and_the_size():
    est = images.FAL.estimate(Link("fal", "flux-schnell"), request(width=1080, height=1920))
    assert abs(est.est_usd - 0.0062) < 0.0001
    assert images.FAL.estimate(Link("fal", "seedream-4-edit"), request("image_edit")).est_usd == 0.03


# ------------------------------------------------------------------ openai

class FakeImages:
    def __init__(self, log):
        self.log = log

    def generate(self, **kw):
        self.log.append(("generate", kw))
        return type("R", (), {"data": [type("D", (), {"b64_json": base64.b64encode(PNG).decode()})()]})()

    def edit(self, **kw):
        kw = dict(kw)
        kw["image"] = [getattr(f, "name", f) for f in kw["image"]]
        self.log.append(("edit", kw))
        return type("R", (), {"data": [type("D", (), {"b64_json": base64.b64encode(PNG).decode()})()]})()


class FakeOpenAI:
    def __init__(self, log):
        self.images = FakeImages(log)


def test_openai_generates_and_edits_through_the_sdk_with_retries_off(tmp_path, ref):
    log = []
    factories = []

    def factory(**kw):
        factories.append(kw)
        return FakeOpenAI(log)

    result = images.OPENAI.generate(Link("openai", "gpt-image-2-low"), request(out_dir=str(tmp_path)),
                                    credentials={"OPENAI_API_KEY": "ok"}, on_log=lambda *a: None, client_factory=factory)
    assert factories[0]["api_key"] == "ok" and factories[0]["max_retries"] == 0
    assert log[0] == ("generate", {"model": "gpt-image-2", "prompt": "an anthropomorphic kiwi in a linen shirt",
                                   "size": "1024x1536", "quality": "low", "n": 1})
    assert pathlib.Path(result.paths[0]).read_bytes() == PNG
    images.OPENAI.generate(Link("openai", "gpt-image-2-low"), request("image_edit", out_dir=str(tmp_path), references=(ref,)),
                           credentials={"OPENAI_API_KEY": "ok"}, on_log=lambda *a: None, client_factory=factory)
    kind, kw = log[1]
    assert kind == "edit" and kw["image"] == [ref] and kw["quality"] == "low"


# ----------------------------------------------------------- through the runner

def test_with_allow_paid_off_no_paid_transport_is_ever_touched(tmp_path):
    """The only request is the local ComfyUI reachability probe (stage 9 gave
    local/comfyui its adapter); every paid link is refused before any call."""
    transport = FakeTransport([APIConnectionError("connection refused")])
    with pytest.raises(NoRunnableLink) as excinfo:
        run_edit(generation.DEFAULT_CHAINS["image_edit"], transport, tmp_path, allow_paid=False)
    assert [c["url"] for c in transport.calls] == ["http://127.0.0.1:8188/system_stats"]
    reasons = dict(excinfo.value.failures)
    assert "allow_paid is off" in reasons["gemini/nano-banana-2-lite"]
    assert "allow_paid is off" in reasons["fal/seedream-4-edit"]
    assert "unreachable at http://127.0.0.1:8188" in reasons["local/comfyui"]


def test_with_allow_paid_on_the_first_runnable_paid_link_answers(tmp_path, ref):
    transport = FakeTransport([APIConnectionError("connection refused"), (200, gemini_answer())])
    (result, answered), log = run_edit("local/comfyui,gemini/nano-banana-2-lite,fal/seedream-4-edit", transport, tmp_path, allow_paid=True)
    assert answered == Link("gemini", "nano-banana-2-lite")
    assert result.paid is True and result.est_cost == 0.0336
    assert [c["url"].split("/")[2] for c in transport.calls] == ["127.0.0.1:8188", "generativelanguage.googleapis.com"]
