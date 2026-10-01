"""AI Story phase 7, stage 2a: the quality image links.

- Nano-banana bills the separate, billing-enabled Google project: it reads
  ``GEMINI_PAID_API_KEY`` and never ``GOOGLE_API_KEY`` (DEC-222, amends
  DEC-205 / RC-V4), and asks for a 1K image next to the aspect ratio. A v2
  base image (portrait, plate, prop) is a text-only request on IMAGE_CHAIN.
- ``fal/seedream-4.5-edit`` (the v2 keyframe link) sends its references,
  a 9:16 size inside the model's pixel bounds and the seed, and no
  negative prompt -- the model has no such field (A-111).

Stdlib + pytest only (DEC-012); no request leaves the process.
"""

from __future__ import annotations

import base64
import json

import pytest

from clipping.providers import images, pricing
from clipping.providers.generation import GenRequest, NoRunnableLink, parse_generation_chain, run_generation_chain
from clipping.providers.registry import Link
from clipping.providers.transport import Response

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class FakeTransport:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body})
        status, payload = self.answers.pop(0)
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode()
        return Response(status, {}, payload)

    def json(self, index):
        return json.loads(self.calls[index]["body"].decode())


def gemini_answer():
    return {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()}}]}}]}


def run(kind, chain, transport, env, tmp_path, **request):
    return run_generation_chain(
        kind, parse_generation_chain(kind, chain),
        GenRequest(kind=kind, prompt="a kiwi in a linen shirt", width=720, height=1280, out_dir=str(tmp_path),
                   **request),
        env=env, allow_paid=True, transport=transport, on_log=lambda *a: None, sleep_fn=lambda s: None)


def test_nano_banana_reads_paid_key_only(tmp_path):
    env = {"GOOGLE_API_KEY": "free-project-key", "GEMINI_PAID_API_KEY": "paid-project-key"}
    # A v2 base image: text to image on IMAGE_CHAIN, no reference.
    transport = FakeTransport([(200, gemini_answer())])
    _result, answered = run("image", "gemini/nano-banana-2", transport, env, tmp_path)
    assert answered == Link("gemini", "nano-banana-2")
    call = transport.calls[0]
    assert call["headers"]["x-goog-api-key"] == "paid-project-key"
    assert "free-project-key" not in json.dumps(call["headers"])
    body = transport.json(0)
    assert body["contents"][0]["parts"] == [{"text": "a kiwi in a linen shirt"}]
    assert body["generationConfig"]["imageConfig"] == {"aspectRatio": "9:16", "imageSize": "1K"}

    # An edit on the lite model: the same key.
    ref = tmp_path / "ref.png"
    ref.write_bytes(PNG)
    transport = FakeTransport([(200, gemini_answer())])
    run("image_edit", "gemini/nano-banana-2-lite", transport, env, tmp_path, references=(str(ref),))
    assert transport.calls[0]["headers"]["x-goog-api-key"] == "paid-project-key"
    assert transport.json(0)["generationConfig"]["imageConfig"]["imageSize"] == "1K"

    # With only the free project's key, nano-banana is never contacted.
    transport = FakeTransport([])
    with pytest.raises(NoRunnableLink) as excinfo:
        run("image", "gemini/nano-banana-2", transport, {"GOOGLE_API_KEY": "free-project-key"}, tmp_path)
    assert transport.calls == []
    assert excinfo.value.failures == [("gemini/nano-banana-2", "no API key (GEMINI_PAID_API_KEY is not set)")]


def test_seedream45_inputs_carry_refs_and_size(tmp_path):
    refs = []
    for index in range(12):
        path = tmp_path / f"ref{index}.png"
        path.write_bytes(PNG)
        refs.append(str(path))
    app = "fal-ai/bytedance/seedream/v4.5/edit"
    base = f"https://queue.fal.run/{app}/requests/req-1"
    transport = FakeTransport([
        (200, {"request_id": "req-1", "status_url": base + "/status", "response_url": base}),
        (200, {"status": "COMPLETED"}),
        (200, {"images": [{"url": "https://v3.fal.media/files/x/out.png", "width": 1440, "height": 2560,
                           "content_type": "image/png"}], "seed": 7}),
        (200, PNG),
    ])
    result = images.FAL.generate(
        Link("fal", "seedream-4.5-edit"),
        GenRequest(kind="image_edit", prompt="shot", negative="blurry, watermark", width=720, height=1280, seed=7,
                   references=tuple(refs), out_dir=str(tmp_path)),
        credentials={"FAL_KEY": "fk"}, on_log=lambda *a: None, transport=transport, sleep_fn=lambda s: None)
    assert transport.calls[0]["url"] == f"https://queue.fal.run/{app}"
    body = transport.json(0)
    assert len(body["image_urls"]) == 10 and all(u.startswith("data:image/png;base64,") for u in body["image_urls"])
    # 9:16 exactly, at the model's smallest custom size (2560x1440 pixels in all).
    assert body["image_size"] == {"width": 1440, "height": 2560}
    assert body["seed"] == 7 and body["num_images"] == 1
    assert "negative_prompt" not in body
    assert result.seed == 7
    est = pricing.estimate(Link("fal", "seedream-4.5-edit"), 1, width=720, height=1280)
    assert est.est_usd == 0.04 and est.paid is True
