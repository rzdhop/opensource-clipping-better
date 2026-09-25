"""Vision adapters (spec 8.1, 8.7): describe frames, answer text.

Gemini and OpenRouter speak through the same OpenAI-compatible client the LLM
chain builds (``llm.build_client``, retries off), with the frames as
``image_url`` data URLs; Ollama through its own local API
(``local_ollama.py``). The answer is text in ``GenResult.meta["text"]`` --
the reference-video import of phase 7 turns it into style fragments.
"""

from __future__ import annotations

from . import generation, pricing, registry
from .errors import ProviderError
from .generation import VISION, GenResult, register_adapter
from .local_ollama import OLLAMA_VISION_DEFAULT_MODEL, OllamaClient
from .registry import Link, describe
from .transport import APIConnectionError, APITimeoutError, data_url

GEMINI_VISION_MODELS = {"flash-lite": registry.GEMINI_DEFAULT_MODEL, "flash": "gemini-3.5-flash"}
TOKENS_PER_IMAGE = 258  # Gemini's published per-image cost, used for estimates only


def _llm_build_client(link, *, api_key, timeout):
    from . import llm  # keeps the SDK import inside llm.build_client

    return llm.build_client(link, api_key=api_key, timeout=timeout)


class _CompatVision:
    provider = ""
    env_key = ""

    def _model_id(self, link) -> str:
        return link.model

    def estimate(self, link, request):
        if not generation.is_paid(link):
            return None
        tokens = len(request.prompt or "") // 4 + TOKENS_PER_IMAGE * len(request.images or ())
        return pricing.estimate(link, tokens)

    def probe(self, link, *, credentials):
        return True, "key set; not probed (a request is the probe)"

    def generate(self, link, request, *, credentials, on_log, transport=None, client_factory=None, **_):
        model_id = self._model_id(link)
        client_link = Link(self.provider, model_id)
        factory = client_factory or _llm_build_client
        client = factory(client_link, api_key=credentials[self.env_key], timeout=registry.effective_timeout(client_link))
        content = [{"type": "text", "text": request.prompt or "Describe this image."}]
        for path in request.images or ():
            content.append({"type": "image_url", "image_url": {"url": data_url(path)}})
        extra = request.extra or {}
        answer = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": content}],
            max_tokens=int(extra.get("max_tokens", 600)),
            temperature=float(extra.get("temperature", 0.2)),
        )
        choices = getattr(answer, "choices", None) or []
        text = (getattr(getattr(choices[0], "message", None), "content", None) or "").strip() if choices else ""
        if not text:
            raise ProviderError(f"{describe(link)}: the answer carried no text")
        usage = getattr(answer, "usage", None)
        meta = {
            "text": text,
            "model_id": model_id,
            "finish_reason": getattr(choices[0], "finish_reason", None),
            "usage": {"prompt_tokens": getattr(usage, "prompt_tokens", None),
                      "completion_tokens": getattr(usage, "completion_tokens", None)} if usage else None,
        }
        return GenResult(provider=self.provider, model=link.model, paths=(), meta=meta)


class GeminiVisionAdapter(_CompatVision):
    provider = "gemini"
    env_key = "GOOGLE_API_KEY"

    def _model_id(self, link):
        return GEMINI_VISION_MODELS.get(link.model, link.model)


class OpenRouterVisionAdapter(_CompatVision):
    provider = "openrouter"
    env_key = "OPENROUTER_API_KEY"


class OllamaVisionAdapter:
    provider = "local"

    def _client(self, transport, env):
        return OllamaClient(generation.local_url("ollama", env), transport=transport)

    def _model(self, request):
        return (request.extra or {}).get("model") or OLLAMA_VISION_DEFAULT_MODEL

    def estimate(self, link, request):
        return None

    def probe(self, link, *, credentials, transport=None, env=None, model=None):
        client = self._client(transport, env)
        try:
            names = client.tags()  # one request: reachability and the model list at once
        except (APIConnectionError, APITimeoutError) as exc:
            return False, f"unreachable at {client.base_url} ({exc})"
        model = model or OLLAMA_VISION_DEFAULT_MODEL
        if model not in names:
            return False, f"model {model} is not pulled on {client.base_url}: ollama pull {model}"
        return True, f"{client.base_url} has {model}"

    def generate(self, link, request, *, credentials, on_log, transport=None, env=None, **_):
        client = self._client(transport, env)
        model = self._model(request)
        payload = client.chat(model, [{"role": "user", "content": request.prompt or "Describe this image."}],
                              images=list(request.images or ()))
        text = ((payload.get("message") or {}).get("content") or "").strip()
        if not text:
            raise ProviderError(f"{describe(link)}: the answer carried no text")
        return GenResult(provider="local", model=link.model, paths=(),
                         meta={"text": text, "model_id": model,
                               "usage": {"prompt_tokens": payload.get("prompt_eval_count"),
                                         "completion_tokens": payload.get("eval_count")}})


GEMINI_VISION = GeminiVisionAdapter()
OPENROUTER_VISION = OpenRouterVisionAdapter()
OLLAMA_VISION = OllamaVisionAdapter()

register_adapter(VISION, "gemini", GEMINI_VISION)
register_adapter(VISION, "openrouter", OPENROUTER_VISION)
register_adapter(VISION, "local", OLLAMA_VISION)
