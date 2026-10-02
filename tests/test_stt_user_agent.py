"""stt's multipart upload names the app in its User-Agent (log 2026-09-30:
Groq answered 403 "error code: 1010" -- Cloudflare's ban of a client
signature -- to urllib's default ``Python-urllib/3.x``, whatever the key).

The upload is the one stt request built outside ``transport.request_json``;
it opens through ``transport._OPENER`` (DEC-196). A local server records the
headers it receives. Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

from clipping.providers import stt, transport

from test_stt_broll_redirects import _server


def test_the_stt_upload_sends_the_apps_user_agent_not_urllibs(tmp_path, monkeypatch):
    monkeypatch.setattr(stt, "REQUEST_TIMEOUT", 5)
    audio = tmp_path / "chunk.flac"
    audio.write_bytes(b"fLaC")
    with _server() as server:
        server.routes["/transcriptions"] = (200, {"Content-Type": "application/json"}, b'{"text": "ok"}')
        result = stt._post_multipart(f"{server.url}/transcriptions", "test-groq", str(audio),
                                     {"model": "whisper-large-v3-turbo"})
    assert result == {"text": "ok"}
    [(_method, _path, headers)] = server.seen
    assert headers["user-agent"] == transport.USER_AGENT
    assert not headers["user-agent"].lower().startswith("python-urllib")
    assert headers["authorization"] == "Bearer test-groq"
