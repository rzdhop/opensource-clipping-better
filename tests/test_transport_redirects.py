"""A redirect never carries a provider's credential to another origin. The
stdlib copies every header onto a 30x's new request, whatever its host, so the
transport's own redirect handler drops the credential headers when the scheme,
host or port changes, and keeps them on a same-origin redirect."""

import contextlib
import http.server
import threading
from types import SimpleNamespace

from clipping.providers import transport

CREDENTIALS = {
    "Authorization": "Key test-fal",
    "Proxy-Authorization": "Basic test-proxy",
    "x-goog-api-key": "test-gemini",
    "X-Api-Key": "test-other",
}
CREDENTIAL_NAMES = {name.lower() for name in CREDENTIALS}


@contextlib.contextmanager
def _server(location=None):
    """A server on 127.0.0.1 that records each request's path and headers
    (lower-cased names) in ``seen``, answers ``/start`` with a 302 to
    ``location`` and anything else with 200 ``ok``."""
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's name
            seen.append((self.path, {name.lower(): value for name, value in self.headers.items()}))
            status, body = (302, b"") if self.path == "/start" else (200, b"ok")
            self.send_response(status)
            if status == 302:
                self.send_header("Location", location)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_address[1]}", seen=seen)
    finally:
        server.shutdown()
        server.server_close()


def test_a_redirect_to_another_origin_drops_the_credentials_and_keeps_the_other_headers():
    with _server() as b, _server(location=f"{b.url}/end") as a:
        response = transport.urllib_transport(
            "GET", f"{a.url}/start", headers={**CREDENTIALS, "X-Trace": "kept"}, timeout=5)
    assert (response.status, response.body) == (200, b"ok")
    [(_, first)] = a.seen
    assert CREDENTIAL_NAMES <= set(first)  # the host they were addressed to got them
    [(path, second)] = b.seen
    assert path == "/end"
    assert second["x-trace"] == "kept"
    assert not CREDENTIAL_NAMES & set(second)


def test_a_same_origin_redirect_keeps_the_credentials():
    with _server(location="/end") as a:
        response = transport.urllib_transport("GET", f"{a.url}/start", headers=dict(CREDENTIALS), timeout=5)
    assert (response.status, response.body) == (200, b"ok")
    [_, (path, second)] = a.seen
    assert path == "/end"
    assert {name.lower(): value for name, value in CREDENTIALS.items()}.items() <= second.items()
