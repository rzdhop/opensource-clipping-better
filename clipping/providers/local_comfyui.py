"""Local ComfyUI: a client, a workflow-template engine and the image adapter (spec 8.3).

ComfyUI is driven through its HTTP API: ``POST /prompt`` queues an API-format
graph, ``/ws?clientId=`` streams progress, ``/history/{id}`` holds the result,
``/view`` serves the files, ``/system_stats`` says what the box is,
``/object_info`` lists the installed nodes and their choices, and
``/upload/image`` receives the reference images. The websocket is read with a
small RFC 6455 text-frame reader on a plain socket (stdlib, DEC-100); when it
is not available the client polls ``/history`` and says so.

Templates are data (``clipping/aistory/templates/workflows/<name>.json``,
spec 8.7): an API-format graph whose literal inputs are ``{{placeholders}}``,
a ``requires`` list of model files, and the reference slots. Before anything
is queued, every ``class_type`` is checked against ``/object_info`` and every
required file against the loader node's choices; a miss is refused with the
list of what to install -- never a silent fallback to a hosted API.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import mimetypes
import os
import random
import re
import socket
import struct
import time
import urllib.parse
import uuid

from . import generation
from .errors import ProviderError
from .generation import IMAGE, IMAGE_EDIT, GenResult, register_adapter
from .transport import (
    APIConnectionError, APITimeoutError, DEFAULT_TIMEOUT, request_bytes, request_json,
    urllib_transport, write_output,
)

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORKFLOWS_DIR = os.path.join(_ROOT, "clipping", "aistory", "templates", "workflows")
TEMPLATES = ("t2i_flux2_klein", "edit_flux2_klein_multiref", "edit_qwen_image")
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
TYPED = {"seed": int, "width": int, "height": int, "frames": int, "fps": int}
WAIT_BUDGET_SECONDS = 600.0
POLL_INTERVAL_SECONDS = 1.0
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class ComfyUIError(ProviderError):
    """ComfyUI refused or failed the workflow; the message says why."""


# --------------------------------------------------------------- templates

def load_template(name: str) -> dict:
    path = os.path.join(WORKFLOWS_DIR, f"{name}.json")
    with open(path, encoding="utf-8") as fh:
        template = json.load(fh)
    if template.get("$schema") != "comfy_workflow_v1":
        raise ValueError(f"{path}: expected \"$schema\": \"comfy_workflow_v1\"")
    return template


def render_template(template: dict, values: dict) -> dict:
    """Fill the placeholders of *template* with *values*; returns the graph to queue.

    Typed placeholders (seed, width, height, frames, fps) become ints. The
    reference slots (``ref_nodes``, LoadImage nodes) take ``values["ref_paths"]``
    in order; unused slots repeat the last reference so the graph never needs
    rewiring; more references than slots is an error, not a truncation.
    """
    name = template.get("name", "template")
    graph = copy.deepcopy(template["graph"])
    declared = [p for p in template.get("placeholders", []) if p != "ref_paths"]
    missing = [p for p in declared if p not in values]
    if missing:
        raise ValueError(f"{name} needs values for: {', '.join(missing)}")
    for node in graph.values():
        for key, value in list(node["inputs"].items()):
            if not isinstance(value, str) or "{{" not in value:
                continue
            whole = PLACEHOLDER.fullmatch(value)
            if whole:
                field = whole.group(1)
                node["inputs"][key] = TYPED[field](values[field]) if field in TYPED else values[field]
            else:
                node["inputs"][key] = PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), value)
    slots = int(template.get("ref_slots") or 0)
    if slots:
        refs = [str(p) for p in (values.get("ref_paths") or [])]
        if not refs:
            raise ValueError(f"{name} needs at least one reference image (ref_paths)")
        if len(refs) > slots:
            raise ValueError(f"{name} supports {slots} reference images, got {len(refs)}")
        refs = refs + [refs[-1]] * (slots - len(refs))
        for node_id, path in zip(template["ref_nodes"], refs):
            graph[node_id]["inputs"]["image"] = path
    return graph


def _choices(info: dict, field: str):
    for section in ("required", "optional"):
        spec = ((info.get("input") or {}).get(section) or {}).get(field)
        if isinstance(spec, list) and spec and isinstance(spec[0], list):
            return [str(c) for c in spec[0]]
    return None


def validate_template(template: dict, object_info: dict) -> list:
    """What this ComfyUI lacks to run *template*: custom nodes and model files, by name."""
    problems = []
    graph = template["graph"]
    for node_id, node in graph.items():
        class_type = node["class_type"]
        if class_type not in object_info:
            problems.append(
                f"custom node {class_type} is not installed (needed by node {node_id}); "
                f"add it under ComfyUI/custom_nodes"
            )
    for req in template.get("requires", []):
        class_type = graph[req["node"]]["class_type"]
        info = object_info.get(class_type)
        if info is None:
            continue
        choices = _choices(info, req["field"])
        if choices is None or req["file"] in choices:
            continue
        known = ", ".join(choices[:5]) or "none"
        problems.append(
            f"model file {req['file']} is not installed (node {req['node']} {class_type}, field {req['field']}); "
            f"ComfyUI knows: {known}"
        )
    return problems


# ---------------------------------------------------------------- websocket

def _recv_exact(sock, length: int) -> bytes:
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            return data
        data += chunk
    return data


def _send_frame(sock, opcode: int, payload: bytes) -> None:
    """A client frame (masked, as RFC 6455 requires of clients)."""
    mask = os.urandom(4)
    head = bytes([0x80 | opcode])
    length = len(payload)
    if length < 126:
        head += bytes([0x80 | length])
    elif length < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", length)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", length)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(head + mask + masked)


def ws_frames(base_url: str, client_id: str, *, timeout=WAIT_BUDGET_SECONDS, sock_factory=None):
    """Yield each JSON text frame of ``/ws?clientId=<id>``; stop on the close frame."""
    parsed = urllib.parse.urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    factory = sock_factory or (lambda: socket.create_connection((host, port), timeout=timeout))
    try:
        sock = factory()
    except socket.timeout as exc:
        raise APITimeoutError(f"websocket to {base_url} timed out") from exc
    except OSError as exc:
        raise APIConnectionError(f"websocket to {base_url}: {exc}") from exc
    try:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET /ws?clientId={urllib.parse.quote(client_id)} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(handshake.encode("ascii"))
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise APIConnectionError(f"websocket to {base_url}: closed during the handshake")
            head += chunk
        header, _, rest = head.partition(b"\r\n\r\n")
        status_line = header.split(b"\r\n", 1)[0]
        if b" 101 " not in status_line:
            raise APIConnectionError(f"websocket to {base_url} refused: {status_line.decode('latin-1')}")
        expected = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")
        accept = ""
        for line in header.split(b"\r\n")[1:]:
            name, _, value = line.decode("latin-1").partition(":")
            if name.strip().lower() == "sec-websocket-accept":
                accept = value.strip()
        if accept != expected:
            raise APIConnectionError(f"websocket to {base_url}: bad Sec-WebSocket-Accept")

        buffer = rest
        text = b""

        def take(n):
            nonlocal buffer
            while len(buffer) < n:
                chunk = sock.recv(65536)
                if not chunk:
                    return None
                buffer += chunk
            out, buffer = buffer[:n], buffer[n:]
            return out

        while True:
            head = take(2)
            if head is None or len(head) < 2:
                return
            fin = head[0] & 0x80
            opcode = head[0] & 0x0F
            masked = head[1] & 0x80
            length = head[1] & 0x7F
            if length == 126:
                length = struct.unpack(">H", take(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", take(8))[0]
            mask = take(4) if masked else None
            payload = take(length) if length else b""
            if payload is None:
                return
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 8:
                return
            if opcode == 9:
                _send_frame(sock, 10, payload)
                continue
            if opcode in (2, 10):
                continue  # binary previews and pongs
            if opcode in (0, 1):
                text += payload
                if fin:
                    raw, text = text, b""
                    try:
                        event = json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if isinstance(event, dict):
                        yield event
    except socket.timeout as exc:
        raise APITimeoutError(f"websocket to {base_url} timed out") from exc
    except OSError as exc:
        raise APIConnectionError(f"websocket to {base_url}: {exc}") from exc
    finally:
        try:
            sock.close()
        except OSError:
            pass


# ------------------------------------------------------------------ client

class ComfyUIClient:
    def __init__(self, base_url: str, *, transport=None, timeout=DEFAULT_TIMEOUT, client_id=None):
        self.base_url = base_url.rstrip("/")
        self._transport = transport or urllib_transport
        self.timeout = timeout
        self.client_id = client_id or uuid.uuid4().hex
        self._object_info = None
        self.last_prompt_id = None

    def _get(self, path: str, *, timeout=None) -> dict:
        return request_json(self._transport, "GET", f"{self.base_url}{path}", headers={}, timeout=timeout or self.timeout)

    def system_stats(self) -> dict:
        return self._get("/system_stats", timeout=15)

    def object_info(self) -> dict:
        if self._object_info is None:
            self._object_info = self._get("/object_info", timeout=60)
        return self._object_info

    def reachable(self):
        try:
            stats = self.system_stats()
        except (APIConnectionError, APITimeoutError) as exc:
            return False, f"unreachable at {self.base_url} ({exc})"
        version = (stats.get("system") or {}).get("comfyui_version", "?")
        devices = ", ".join(str(d.get("name", "")) for d in stats.get("devices") or [] if d.get("name"))
        return True, f"ComfyUI {version} at {self.base_url}; devices: {devices or 'none reported'}"

    def queue_prompt(self, graph: dict) -> str:
        payload = request_json(self._transport, "POST", f"{self.base_url}/prompt", headers={},
                               json_body={"prompt": graph, "client_id": self.client_id}, timeout=self.timeout)
        node_errors = payload.get("node_errors") or {}
        if node_errors:
            parts = []
            for node_id, info in node_errors.items():
                messages = []
                for error in (info or {}).get("errors") or []:
                    message = str(error.get("message", ""))
                    if error.get("details"):
                        message += f" ({error['details']})"
                    messages.append(message)
                parts.append(f"node {node_id}: " + ("; ".join(messages) or "invalid"))
            raise ComfyUIError("ComfyUI refused the workflow: " + " | ".join(parts))
        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"ComfyUI answered without a prompt_id: {str(payload)[:200]}")
        self.last_prompt_id = prompt_id
        return prompt_id

    def upload_image(self, path: str, *, subfolder: str = "", overwrite: bool = True) -> str:
        """``POST /upload/image``; returns the name to put in a LoadImage node."""
        boundary = uuid.uuid4().hex
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            data = fh.read()
        body = bytearray()
        for name, value in (("subfolder", subfolder), ("overwrite", "true" if overwrite else "false"), ("type", "input")):
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8")
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{os.path.basename(path)}\"\r\nContent-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
        body += data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        payload = request_json(self._transport, "POST", f"{self.base_url}/upload/image",
                               headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                               body=bytes(body), timeout=self.timeout)
        name = payload.get("name") or os.path.basename(path)
        sub = payload.get("subfolder") or subfolder
        return f"{sub}/{name}" if sub else name

    def history(self, prompt_id: str) -> dict:
        return self._get(f"/history/{prompt_id}", timeout=30)

    def view(self, file: dict) -> bytes:
        query = urllib.parse.urlencode({
            "filename": file["filename"], "subfolder": file.get("subfolder", ""), "type": file.get("type", "output"),
        })
        return request_bytes(self._transport, "GET", f"{self.base_url}/view?{query}", headers={}, timeout=self.timeout)

    @staticmethod
    def _outputs(entry: dict) -> list:
        files = []
        for node_output in (entry.get("outputs") or {}).values():
            for kind in ("images", "gifs", "videos", "audio"):
                for file in node_output.get(kind) or []:
                    if file.get("filename"):
                        files.append({"filename": file["filename"], "subfolder": file.get("subfolder", ""),
                                      "type": file.get("type", "output"), "kind": kind})
        return files

    @staticmethod
    def _error_message(prompt_id: str, status: dict) -> str:
        for message in status.get("messages") or []:
            if isinstance(message, (list, tuple)) and len(message) == 2 and message[0] == "execution_error":
                data = message[1] or {}
                return (f"prompt {prompt_id} failed in {data.get('node_type', 'a node')}: "
                        f"{data.get('exception_message') or data.get('exception_type') or 'unknown error'}")
        return f"prompt {prompt_id} failed: {status.get('status_str', 'error')}"

    def _follow_ws(self, prompt_id, *, on_log, ws_factory, time_fn, budget):
        deadline = time_fn() + budget
        factory = ws_factory or ws_frames
        for event in factory(self.base_url, self.client_id, timeout=budget):
            kind = event.get("type")
            data = event.get("data") or {}
            if kind == "progress" and data.get("prompt_id") in (None, prompt_id):
                on_log(f"   ⏳ ComfyUI: {data.get('value')}/{data.get('max')}")
            elif kind == "executing" and data.get("prompt_id") == prompt_id and data.get("node") is None:
                return
            elif kind == "execution_error" and data.get("prompt_id") == prompt_id:
                raise ComfyUIError(f"prompt {prompt_id} failed in {data.get('node_type', 'a node')}: "
                                   f"{data.get('exception_message') or 'unknown error'}")
            if time_fn() >= deadline:
                raise ComfyUIError(f"prompt {prompt_id} not finished after {budget:.0f}s")

    def wait(self, prompt_id: str, *, on_log=print, sleep_fn=time.sleep, time_fn=time.monotonic,
             budget=WAIT_BUDGET_SECONDS, use_ws=True, ws_factory=None, poll_interval=POLL_INTERVAL_SECONDS) -> list:
        """Block until *prompt_id* is done; return its output files (``filename``, ``subfolder``, ``type``)."""
        if use_ws:
            try:
                self._follow_ws(prompt_id, on_log=on_log, ws_factory=ws_factory, time_fn=time_fn, budget=budget)
            except (APIConnectionError, APITimeoutError, OSError) as exc:
                on_log(f"   ↩ websocket unavailable ({exc}); polling /history")
        deadline = time_fn() + budget
        while True:
            entry = (self.history(prompt_id) or {}).get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                if status.get("completed") or status.get("status_str") == "success":
                    return self._outputs(entry)
                if status.get("status_str") == "error":
                    raise ComfyUIError(self._error_message(prompt_id, status))
            if time_fn() >= deadline:
                raise ComfyUIError(f"prompt {prompt_id} not finished after {budget:.0f}s")
            sleep_fn(poll_interval)

    def run(self, graph: dict, *, on_log=print, sleep_fn=time.sleep, time_fn=time.monotonic,
            budget=WAIT_BUDGET_SECONDS, ws_factory=None) -> list:
        prompt_id = self.queue_prompt(graph)
        on_log(f"   🔁 ComfyUI: queued prompt {prompt_id} on {self.base_url}")
        return self.wait(prompt_id, on_log=on_log, sleep_fn=sleep_fn, time_fn=time_fn, budget=budget,
                         use_ws=True, ws_factory=ws_factory)


# ----------------------------------------------------------------- adapter

DEFAULT_TEMPLATES = {IMAGE: "t2i_flux2_klein", IMAGE_EDIT: "edit_flux2_klein_multiref"}


class ComfyUIImageAdapter:
    provider = "local"

    def estimate(self, link, request):
        return None

    def probe(self, link, *, credentials, transport=None, env=None):
        return ComfyUIClient(generation.local_url("comfyui", env), transport=transport).reachable()

    def generate(self, link, request, *, credentials, on_log, transport=None, env=None,
                 sleep_fn=time.sleep, time_fn=time.monotonic, ws_factory=None, **_):
        if not request.out_dir:
            raise ValueError("GenRequest.out_dir is required: where the image is written")
        client = ComfyUIClient(generation.local_url("comfyui", env), transport=transport)
        template_name = (request.extra or {}).get("template") or DEFAULT_TEMPLATES.get(request.kind, "t2i_flux2_klein")
        template = load_template(template_name)
        problems = validate_template(template, client.object_info())
        if problems:
            raise ComfyUIError(f"{template_name} cannot run on {client.base_url}:\n  " + "\n  ".join(problems))
        seed = request.seed if request.seed is not None else random.randrange(1, 2**31 - 1)
        values = {"prompt": request.prompt, "negative": request.negative or "", "seed": seed,
                  "width": request.width, "height": request.height}
        if template.get("ref_slots"):
            if not request.references:
                raise ValueError(f"{template_name} needs reference images")
            values["ref_paths"] = [client.upload_image(path, subfolder="rzdhop") for path in request.references]
        graph = render_template(template, values)
        files = client.run(graph, on_log=on_log, sleep_fn=sleep_fn, time_fn=time_fn, ws_factory=ws_factory)
        if not files:
            raise ComfyUIError(f"{template_name}: ComfyUI finished without an output image")
        data = client.view(files[0])
        ext = os.path.splitext(files[0]["filename"])[1].lstrip(".").lower() or "png"
        name = (request.extra or {}).get("name") or f"comfyui_{template_name}_{seed}"
        path = write_output(request.out_dir, name, data, ext)
        return GenResult(provider="local", model=link.model, paths=(path,), seed=seed,
                         meta={"template": template_name, "prompt_id": client.last_prompt_id,
                               "comfyui": client.base_url, "output": files[0]["filename"]})


COMFYUI = ComfyUIImageAdapter()
register_adapter(IMAGE, "local", COMFYUI)
register_adapter(IMAGE_EDIT, "local", COMFYUI)
