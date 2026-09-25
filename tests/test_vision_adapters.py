"""Vision adapters (spec 8.1 / 8.7): Gemini and OpenRouter through the same
OpenAI-compatible client the LLM chain builds, Ollama over its own HTTP API.
They describe images and answer text; nothing is written unless asked."""

import base64
import json
import pathlib

import pytest

from clipping.providers import generation, registry, vision
from clipping.providers.generation import GenRequest
from clipping.providers.registry import Link
from clipping.providers.transport import APIConnectionError, Response

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


@pytest.fixture
def frame(tmp_path):
    path = tmp_path / "frame_01.png"
    path.write_bytes(PNG)
    return str(path)


def test_the_adapters_register_for_vision_only():
    assert generation.adapter_for("vision", "gemini") is vision.GEMINI_VISION
    assert generation.adapter_for("vision", "openrouter") is vision.OPENROUTER_VISION
    assert generation.adapter_for("vision", "local") is vision.OLLAMA_VISION
    assert generation.adapter_for("tts", "openrouter") is None


class FakeCompletions:
    def __init__(self, log, text="a kiwi on a beach"):
        self.log, self.text = log, text

    def create(self, **kw):
        self.log.append(kw)
        message = type("M", (), {"content": self.text})()
        choice = type("C", (), {"message": message, "finish_reason": "stop"})()
        usage = type("U", (), {"prompt_tokens": 300, "completion_tokens": 20})()
        return type("R", (), {"choices": [choice], "usage": usage})()


class FakeClient:
    def __init__(self, log):
        self.chat = type("Chat", (), {"completions": FakeCompletions(log)})()


def test_gemini_vision_sends_text_and_image_parts_through_the_compat_client(frame):
    log, built = [], []

    def factory(link, *, api_key, timeout):
        built.append((link, api_key, timeout))
        return FakeClient(log)

    request = GenRequest(kind="vision", prompt="Describe the rendering style.", images=(frame,))
    result = vision.GEMINI_VISION.generate(Link("gemini", "flash-lite"), request, credentials={"GOOGLE_API_KEY": "gk"},
                                           on_log=lambda *a: None, client_factory=factory)
    assert built[0][0] == Link("gemini", registry.GEMINI_DEFAULT_MODEL) and built[0][1] == "gk"
    kw = log[0]
    assert kw["model"] == registry.GEMINI_DEFAULT_MODEL
    content = kw["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Describe the rendering style."}
    assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1]) == PNG
    assert result.meta["text"] == "a kiwi on a beach" and result.paths == ()
    assert result.meta["usage"] == {"prompt_tokens": 300, "completion_tokens": 20}


def test_gemini_flash_maps_to_the_paid_flash_model_and_is_estimated(frame):
    assert vision.GEMINI_VISION.estimate(Link("gemini", "flash-lite"), GenRequest(kind="vision", images=(frame,))) is None
    est = vision.GEMINI_VISION.estimate(Link("gemini", "flash"), GenRequest(kind="vision", prompt="x" * 400, images=(frame, frame)))
    assert est.unit == "token" and est.qty == 100 + 2 * 258 and est.paid is True and est.est_usd > 0
    assert vision.GEMINI_VISION_MODELS["flash"] == "gemini-3.5-flash"


def test_openrouter_passes_its_model_through(frame):
    log, built = [], []

    def factory(link, *, api_key, timeout):
        built.append(link)
        return FakeClient(log)

    vision.OPENROUTER_VISION.generate(Link("openrouter", "qwen/qwen3.8-27b:free"), GenRequest(kind="vision", prompt="p", images=(frame,)),
                                      credentials={"OPENROUTER_API_KEY": "ok"}, on_log=lambda *a: None, client_factory=factory)
    assert built == [Link("openrouter", "qwen/qwen3.8-27b:free")] and log[0]["model"] == "qwen/qwen3.8-27b:free"


def test_an_answer_without_text_is_reported(frame):
    log = []

    class Empty(FakeClient):
        def __init__(self, log):
            self.chat = type("Chat", (), {"completions": FakeCompletions(log, text="")})()

    with pytest.raises(Exception) as excinfo:
        vision.GEMINI_VISION.generate(Link("gemini", "flash-lite"), GenRequest(kind="vision", prompt="p", images=(frame,)),
                                      credentials={"GOOGLE_API_KEY": "gk"}, on_log=lambda *a: None,
                                      client_factory=lambda link, **kw: Empty(log))
    assert "no text" in str(excinfo.value)


# ------------------------------------------------------------------ ollama

class FakeTransport:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "body": body})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, payload = answer
        return Response(status, {}, json.dumps(payload).encode())


def test_ollama_vision_posts_the_images_and_returns_the_text(frame):
    transport = FakeTransport([(200, {"message": {"role": "assistant", "content": "a fruit"}, "done": True,
                                      "prompt_eval_count": 120, "eval_count": 5})])
    request = GenRequest(kind="vision", prompt="What is this?", images=(frame,), extra={"model": "gemma3:4b"})
    result = vision.OLLAMA_VISION.generate(Link("local", "ollama-vision"), request, credentials={}, on_log=lambda *a: None,
                                           transport=transport, env={"LOCAL_OLLAMA_URL": "http://gpu-box:11434"})
    call = transport.calls[0]
    assert call["method"] == "POST" and call["url"] == "http://gpu-box:11434/api/chat"
    body = json.loads(call["body"])
    assert body["model"] == "gemma3:4b" and body["stream"] is False
    assert body["messages"][0]["content"] == "What is this?"
    assert base64.b64decode(body["messages"][0]["images"][0]) == PNG
    assert result.meta["text"] == "a fruit" and result.model == "ollama-vision"


def test_ollama_probe_reports_unreachable_and_missing_models():
    transport = FakeTransport([APIConnectionError("connection refused")])
    ok, note = vision.OLLAMA_VISION.probe(Link("local", "ollama-vision"), credentials={}, transport=transport,
                                          env={"LOCAL_OLLAMA_URL": "http://127.0.0.1:11434"})
    assert ok is False and "unreachable at http://127.0.0.1:11434" in note
    transport = FakeTransport([(200, {"models": [{"name": "llama3:8b"}]})])
    ok, note = vision.OLLAMA_VISION.probe(Link("local", "ollama-vision"), credentials={}, transport=transport,
                                          env={"LOCAL_OLLAMA_URL": "http://127.0.0.1:11434"})
    assert ok is False and "ollama pull gemma3:4b" in note
    transport = FakeTransport([(200, {"models": [{"name": "gemma3:4b"}]})])
    ok, note = vision.OLLAMA_VISION.probe(Link("local", "ollama-vision"), credentials={}, transport=transport,
                                          env={"LOCAL_OLLAMA_URL": "http://127.0.0.1:11434"})
    assert ok is True and "gemma3:4b" in note
