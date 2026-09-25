"""A small client for a local Ollama server (spec 8.3).

Ollama is reached over its own HTTP API through the shared stdlib transport:
``/api/tags`` says which models are pulled, ``/api/chat`` answers a chat with
optional images and an optional JSON schema (``format``). The URL is resolved
Docker-aware by ``generation.local_url`` (``host.docker.internal`` inside the
container). Nothing here imports beyond the standard library.
"""

from __future__ import annotations

import base64

from .transport import APIConnectionError, APITimeoutError, DEFAULT_TIMEOUT, request_json, urllib_transport

OLLAMA_VISION_DEFAULT_MODEL = "gemma3:4b"


class OllamaClient:
    def __init__(self, base_url: str, *, transport=None, timeout=DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self._transport = transport or urllib_transport
        self.timeout = timeout

    def tags(self) -> list:
        """The names of the pulled models."""
        payload = request_json(self._transport, "GET", f"{self.base_url}/api/tags", headers={}, timeout=15)
        return [str(m.get("name", "")) for m in payload.get("models") or [] if m.get("name")]

    def reachable(self):
        """``(ok, note)``: whether the server answers, and what it has."""
        try:
            names = self.tags()
        except (APIConnectionError, APITimeoutError) as exc:
            return False, f"unreachable at {self.base_url} ({exc})"
        return True, f"{self.base_url}: {len(names)} model(s) pulled"

    def chat(self, model: str, messages: list, *, images=None, format=None, options=None) -> dict:
        """One ``/api/chat`` round trip. *images* (paths) attach to the last user message."""
        messages = [dict(m) for m in messages]
        if images:
            encoded = []
            for path in images:
                with open(path, "rb") as fh:
                    encoded.append(base64.b64encode(fh.read()).decode("ascii"))
            for message in reversed(messages):
                if message.get("role") == "user":
                    message["images"] = encoded
                    break
        body = {"model": model, "messages": messages, "stream": False}
        if format is not None:
            body["format"] = format
        if options:
            body["options"] = options
        return request_json(self._transport, "POST", f"{self.base_url}/api/chat", headers={}, json_body=body,
                            timeout=self.timeout)
