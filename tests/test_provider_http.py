"""The one stdlib transport every generation adapter speaks through: a response
is (status, headers, bytes); an HTTP error carries its status code so
``errors.classify`` and ``errors.is_model_unavailable`` read it; a connection
failure or a timeout is named so the classifier retries it."""

import json
import socket
import urllib.error

import pytest

from clipping.providers import errors, transport
from clipping.providers.transport import (
    APIConnectionError, APITimeoutError, HttpStatusError, Response, data_url, request_json,
)


def fake(status, payload):
    def send(method, url, *, headers=None, body=None, timeout=60):
        return Response(status, {}, payload if isinstance(payload, bytes) else json.dumps(payload).encode())
    return send


def test_a_json_answer_is_parsed():
    payload = request_json(fake(200, {"ok": True}), "GET", "https://x/y", headers={})
    assert payload == {"ok": True}


def test_an_http_error_carries_its_status_and_is_classified_like_an_sdk_error():
    with pytest.raises(HttpStatusError) as excinfo:
        request_json(fake(404, {"error": {"message": "model gemini-x is not found"}}), "POST", "https://x/y", headers={})
    exc = excinfo.value
    assert exc.status_code == 404
    assert "HTTP 404 from https://x/y" in str(exc) and "not found" in str(exc)
    assert errors.status_code(exc) == 404
    assert errors.is_model_unavailable(exc) is True
    assert errors.classify(exc) == errors.FATAL

    with pytest.raises(HttpStatusError) as excinfo:
        request_json(fake(503, b"upstream busy"), "GET", "https://x/y", headers={})
    assert errors.classify(excinfo.value) == errors.RETRY
    with pytest.raises(HttpStatusError) as excinfo:
        request_json(fake(401, b"bad key"), "GET", "https://x/y", headers={})
    assert errors.classify(excinfo.value) == errors.FATAL
    assert errors.is_model_unavailable(excinfo.value) is False


def test_connection_failures_and_timeouts_are_retryable_by_name():
    assert errors.classify(APIConnectionError("refused")) == errors.RETRY
    assert errors.classify(APITimeoutError("60s")) == errors.RETRY


def test_the_urllib_transport_maps_errors(monkeypatch):
    def refuse(request, timeout):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(transport.urllib.request, "urlopen", refuse)
    with pytest.raises(APIConnectionError):
        transport.urllib_transport("GET", "https://x/y", headers={}, timeout=1)

    def slow(request, timeout):
        raise socket.timeout("timed out")
    monkeypatch.setattr(transport.urllib.request, "urlopen", slow)
    with pytest.raises(APITimeoutError):
        transport.urllib_transport("GET", "https://x/y", headers={}, timeout=1)

    class Body:
        def __init__(self, data, status):
            self._data, self.status, self.headers = data, status, {"Content-Type": "application/json"}
        def read(self):
            return self._data
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(transport.urllib.request, "urlopen", lambda request, timeout: Body(b'{"a":1}', 200))
    assert transport.urllib_transport("GET", "https://x/y", headers={}, timeout=1).status == 200

    def http_error(request, timeout):
        raise urllib.error.HTTPError("https://x/y", 429, "Too Many", {"Retry-After": "7"}, None)
    monkeypatch.setattr(transport.urllib.request, "urlopen", http_error)
    response = transport.urllib_transport("GET", "https://x/y", headers={}, timeout=1)
    assert response.status == 429


def test_a_data_url_wraps_a_file(tmp_path):
    png = tmp_path / "ref.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nxxxx")
    url = data_url(str(png))
    assert url.startswith("data:image/png;base64,")
