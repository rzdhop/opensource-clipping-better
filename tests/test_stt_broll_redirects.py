"""The two callers that build their own request instead of going through the
transport never carry a credential to another origin on a redirect either:
stt's multipart upload (Groq and Mistral send ``Authorization: Bearer``) and the
Pexels search in studio/broll.py (the key is the ``Authorization`` value). Both
open through ``transport._OPENER``, whose redirect handler drops the credential
headers when the scheme, host or port changes (DEC-195, DEC-196)."""

import contextlib
import http.server
import importlib
import json
import threading
from types import SimpleNamespace

import pytest

from clipping.providers import stt


@contextlib.contextmanager
def _server():
    """A server on 127.0.0.1 that records each request's method, path (without
    its query) and headers (lower-cased names) in ``seen``, and answers from
    ``routes``: path -> ``(status, headers, body)``, filled in by the test once
    every server's URL is known."""
    seen, routes = [], {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            path = self.path.partition("?")[0]
            seen.append((self.command, path, {name.lower(): value for name, value in self.headers.items()}))
            status, headers, body = routes.get(path, (404, {}, b""))
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _answer

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_address[1]}", seen=seen, routes=routes)
    finally:
        server.shutdown()
        server.server_close()


def test_the_stt_upload_does_not_carry_the_bearer_key_to_another_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(stt, "REQUEST_TIMEOUT", 5)
    audio = tmp_path / "chunk.flac"
    audio.write_bytes(b"fLaC")
    with _server() as b, _server() as a:
        a.routes["/transcriptions"] = (302, {"Location": f"{b.url}/moved"}, b"")
        b.routes["/moved"] = (200, {"Content-Type": "application/json"}, b'{"text": "ok"}')
        result = stt._post_multipart(
            f"{a.url}/transcriptions", "test-groq", str(audio), {"model": "whisper-large-v3-turbo"})
    assert result == {"text": "ok"}
    [(_, _, first)] = a.seen
    assert first["authorization"] == "Bearer test-groq"  # the host it was addressed to got it
    [(method, path, second)] = b.seen
    assert (method, path) == ("GET", "/moved")  # the stdlib follows a 302'd POST with a GET
    assert "authorization" not in second


@pytest.fixture
def broll(render_stack_stubbed, monkeypatch):
    module = importlib.import_module("clipping.studio.broll")
    monkeypatch.setattr(module, "USED_PEXELS_IDS", set())
    return module


def test_the_pexels_search_does_not_carry_the_key_to_another_origin(broll, tmp_path, monkeypatch):
    out = tmp_path / "broll.mp4"
    with _server() as b, _server() as a:
        monkeypatch.setattr(broll, "PEXELS_VIDEO_SEARCH_URL", f"{a.url}/videos/search")
        a.routes["/videos/search"] = (302, {"Location": f"{b.url}/moved"}, b"")
        video = {"id": 7, "video_files": [
            {"file_type": "video/mp4", "quality": "hd", "width": 1080, "height": 1920, "link": f"{b.url}/clip.mp4"}]}
        b.routes["/moved"] = (200, {"Content-Type": "application/json"}, json.dumps({"videos": [video]}).encode())
        b.routes["/clip.mp4"] = (200, {"Content-Type": "video/mp4"}, b"mp4 bytes")
        assert broll.download_pexels_broll("city", "9:16", str(out), "test-pexels") is True
    assert out.read_bytes() == b"mp4 bytes"
    [(_, _, first)] = a.seen
    assert first["authorization"] == "test-pexels"  # the host it was addressed to got it
    assert [(method, path) for method, path, _ in b.seen] == [("GET", "/moved"), ("GET", "/clip.mp4")]
    assert not [headers for _, _, headers in b.seen if "authorization" in headers]
