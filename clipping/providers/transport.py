"""One stdlib transport for every generation adapter (DEC-100).

A transport is a callable ``(method, url, *, headers, body, timeout) ->
Response(status, headers, body_bytes)``. Tests inject a fake; production uses
``urllib_transport``. An HTTP error becomes :class:`HttpStatusError`, which
carries ``status_code`` so ``errors.classify`` and ``errors.is_model_unavailable``
read it exactly as they read an SDK error. A refused connection or a timeout
is raised under the names the classifier already retries
(``APIConnectionError`` / ``APITimeoutError``): the classifier works off class
names on purpose (DEC-012), and these two are the names it knows.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import socket
import tempfile
import urllib.error
import urllib.request
from collections import namedtuple

Response = namedtuple("Response", "status headers body")

DEFAULT_TIMEOUT = 120.0


class HttpStatusError(Exception):
    """A 4xx/5xx answer. ``status_code`` is what the classifier reads."""

    def __init__(self, status_code, url, detail=""):
        self.status_code = int(status_code)
        self.url = url
        self.detail = detail or ""
        message = f"HTTP {self.status_code} from {url}"
        super().__init__(f"{message}: {detail}" if detail else message)


class APIConnectionError(Exception):
    """Could not reach the host. Named for ``errors.RETRYABLE_EXC_NAMES``."""


class APITimeoutError(Exception):
    """The host did not answer in time. Named for ``errors.RETRYABLE_EXC_NAMES``."""


def urllib_transport(method, url, *, headers=None, body=None, timeout=DEFAULT_TIMEOUT) -> Response:
    request = urllib.request.Request(url, data=body, headers=dict(headers or {}), method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return Response(int(getattr(response, "status", 200)), dict(response.headers or {}), response.read())
    except urllib.error.HTTPError as exc:
        try:
            data = exc.read()
        except Exception:  # noqa: BLE001 - an error without a body
            data = b""
        return Response(int(exc.code), dict(exc.headers or {}), data or b"")
    except socket.timeout as exc:
        raise APITimeoutError(f"{method} {url} timed out after {timeout:.0f}s") from exc
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), socket.timeout):
            raise APITimeoutError(f"{method} {url} timed out after {timeout:.0f}s") from exc
        raise APIConnectionError(f"{method} {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise APITimeoutError(f"{method} {url} timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise APIConnectionError(f"{method} {url}: {exc}") from exc


def _detail(text: str) -> str:
    """The provider's own error message when the body is JSON, else the body's head."""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except ValueError:
        return text[:300]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:300]
        if isinstance(error, str):
            return error[:300]
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict) and first.get("message"):
                return str(first["message"])[:300]
        for key in ("detail", "message"):
            if payload.get(key):
                return str(payload[key])[:300]
    return text[:300]


def request_json(transport, method, url, *, headers=None, json_body=None, body=None, timeout=DEFAULT_TIMEOUT) -> dict:
    """Send and parse a JSON answer; a 4xx/5xx raises :class:`HttpStatusError`."""
    headers = dict(headers or {})
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json")
    response = transport(method, url, headers=headers, body=body, timeout=timeout)
    text = response.body.decode("utf-8", "replace") if response.body else ""
    if response.status >= 400:
        raise HttpStatusError(response.status, url, _detail(text))
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError(f"{url} answered something that is not JSON: {text[:200]!r}") from exc


def request_bytes(transport, method, url, *, headers=None, body=None, timeout=DEFAULT_TIMEOUT) -> bytes:
    response = transport(method, url, headers=dict(headers or {}), body=body, timeout=timeout)
    if response.status >= 400:
        raise HttpStatusError(response.status, url, _detail(response.body.decode("utf-8", "replace") if response.body else ""))
    return response.body or b""


def read_b64(path):
    """``(mime_type, base64_text)`` of a file, for inline image parts."""
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        return mime, base64.b64encode(fh.read()).decode("ascii")


def data_url(path) -> str:
    mime, text = read_b64(path)
    return f"data:{mime};base64,{text}"


def write_output(out_dir, name, data: bytes, ext: str) -> str:
    """Write *data* as ``<out_dir>/<name>.<ext>`` atomically and return the path."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.{ext}")
    handle, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{name}-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
