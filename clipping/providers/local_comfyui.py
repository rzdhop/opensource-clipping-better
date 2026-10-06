"""Local ComfyUI: a client, a workflow-template engine and the image and video adapters (spec 8.3).

ComfyUI is driven through its HTTP API: ``POST /prompt`` queues an API-format
graph, ``/ws?clientId=`` streams progress, ``/history/{id}`` holds the result,
``/view`` serves the files, ``/system_stats`` says what the box is,
``/object_info`` lists the installed nodes and their choices,
``/upload/image`` receives the reference images, ``/queue`` lists what is
waiting and ``POST /free`` unloads the models. The websocket is read with a
small RFC 6455 text-frame reader on a plain socket (stdlib, DEC-100); when it
is not available the client polls ``/history`` and says so.

Templates are data (``clipping/aistory/templates/workflows/<name>.json``,
spec 8.7): an API-format graph whose literal inputs are ``{{placeholders}}``,
a ``requires`` list of model files, and the reference slots (image edit) or a
``frame_rule`` (image to video). Before anything is queued, every
``class_type`` is checked against ``/object_info`` and every required file
against the loader node's choices; a miss is refused with the list of what to
install -- never a silent fallback to a hosted API.

The video templates and :class:`ComfyUIVideoAdapter` (phase 6, stage 4) are
proven against a fake ComfyUI; ``"verified_live"`` on each template says
whether its graph has also run on a GPU. ``i2v_wan22_14b_lightning`` has
(2026-10-06, on an RTX 5090 pod and through the RunPod adapter, DEC-310);
the other two have not (A-035 stays open for them).
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import mimetypes
import os
import random
import re
import socket
import struct
import threading
import time
import urllib.parse
import uuid

from . import generation
from .errors import ProviderError
from .gencache import RequestFailed
from .generation import IMAGE, IMAGE_EDIT, VIDEO, GenResult, register_adapter
from .transport import (
    APIConnectionError, APITimeoutError, DEFAULT_TIMEOUT, request_bytes, request_json,
    urllib_transport, write_output,
)

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORKFLOWS_DIR = os.path.join(_ROOT, "clipping", "aistory", "templates", "workflows")
TEMPLATES = ("t2i_flux2_klein", "edit_flux2_klein_multiref", "edit_qwen_image")
# Image to video, one per hardware profile (``hardware.VIDEO_WORKFLOWS``).
VIDEO_TEMPLATES = ("i2v_wan22_5b", "i2v_wan22_14b_lightning", "i2v_ltx2")
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
TYPED = {"seed": int, "width": int, "height": int, "frames": int, "fps": int,
         # text to speech (plan 31): the Chatterbox knobs, floats
         "exaggeration": float, "cfg_weight": float}
# Every placeholder a template may use (spec 8.7); ``ref_paths`` fills the edit slots;
# ``audio_path`` is the reference voice a TTS template reads through LoadAudio.
KNOWN_PLACEHOLDERS = frozenset({"prompt", "negative", "image_path", "audio_path", "ref_paths", *TYPED})
WAIT_BUDGET_SECONDS = 600.0
# A clip on a local GPU takes minutes; past this the prompt stays journaled and a
# later run follows it again (never queues it twice).
VIDEO_WAIT_BUDGET_SECONDS = 1800.0
POLL_INTERVAL_SECONDS = 1.0
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class ComfyUIError(ProviderError):
    """ComfyUI refused or failed the workflow; the message says why."""


class PromptFailed(ComfyUIError):
    """ComfyUI ran the prompt and it ended in an execution error."""


class PromptGone(ComfyUIError):
    """A journaled prompt is neither in ComfyUI's history nor in its queue
    (the server restarted, or its history was cleared): the journal marks it
    lost (a 404 to the runner) and a later run queues the clip afresh."""

    status_code = 404


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

    Typed placeholders (seed, width, height, frames, fps) become ints, the
    TTS knobs (exaggeration, cfg_weight) floats. The
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
    """The choices of a combo input: ``[[...], {...}]`` (classic nodes) or
    ``["COMBO", {"options": [...]}]`` (nodes written against ComfyUI's v3 API)."""
    for section in ("required", "optional"):
        spec = ((info.get("input") or {}).get(section) or {}).get(field)
        if not isinstance(spec, list) or not spec:
            continue
        if isinstance(spec[0], list):
            return [str(c) for c in spec[0]]
        if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict) and isinstance(spec[1].get("options"), list):
            return [str(c) for c in spec[1]["options"]]
    return None


def validate_template(template: dict, object_info: dict) -> list:
    """What this ComfyUI lacks to run *template*: nodes and model files, by name,
    each model file with the ComfyUI folder it goes in. A template marked
    ``core_nodes_only`` names a missing node as a ComfyUI too old for it. A
    ``requires`` entry with an empty ``field`` is a file the node loads on its
    own (not a combo input): listed for the install message, never checked here."""
    problems = []
    graph = template["graph"]
    missing = {}
    for node_id, node in graph.items():
        if node["class_type"] not in object_info:
            missing.setdefault(node["class_type"], []).append(node_id)
    for class_type, node_ids in missing.items():
        needed = f"needed by node{'s' if len(node_ids) > 1 else ''} {', '.join(node_ids)}"
        if template.get("core_nodes_only"):
            problems.append(f"core node {class_type} is missing ({needed}); this ComfyUI is older than the "
                            "template: update ComfyUI")
        else:
            problems.append(f"custom node {class_type} is not installed ({needed}); add it under ComfyUI/custom_nodes")
    reported = set()
    for req in template.get("requires", []):
        class_type = graph[req["node"]]["class_type"]
        info = object_info.get(class_type)
        if info is None or req["file"] in reported or not req.get("field"):
            # An empty ``field``: the node loads the file itself from ``dir`` (no
            # combo to check against; Chatterbox's weights) -- a missing file
            # shows at run time, by the node's own message.
            continue
        choices = _choices(info, req["field"])
        if choices is None or req["file"] in choices:
            continue
        reported.add(req["file"])
        known = ", ".join(choices[:5]) or "none"
        where = f"; put it in ComfyUI/{req['dir']}" if req.get("dir") else ""
        problems.append(
            f"model file {req['file']} is not installed (node {req['node']} {class_type}, field {req['field']})"
            f"{where}; ComfyUI knows: {known}"
        )
    return problems


def install_message(name: str, base_url: str, problems: list) -> str:
    """The refusal of a template this ComfyUI cannot run: what to install, one line each."""
    return f"{name} cannot run on {base_url}:\n  " + "\n  ".join(problems)


def frames_for(template: dict, seconds) -> int:
    """The frame count of a *seconds*-long clip under the template's
    ``frame_rule``: ``fps x seconds`` rounded up to a multiple of
    ``frame_step``, plus one (Wan 4n+1, LTX 8n+1). ``ValueError`` past
    ``max_frames``."""
    rule = template["frame_rule"]
    fps, step = int(rule["fps"]), int(rule["frame_step"])
    frames = math.ceil(fps * seconds / step) * step + 1
    if frames > int(rule["max_frames"]):
        raise ValueError(f"{template.get('name', 'template')}: {seconds} s is {frames} frames at {fps} fps, "
                         f"more than its {rule['max_frames']}")
    return int(frames)


def video_clip_lengths(name: str) -> tuple:
    """The whole-second clip lengths a video template makes (its ``frame_rule``):
    the ``lengths=`` a planner passes to ``video_plan.requested_seconds`` for
    ``local/comfyui``."""
    return tuple(int(n) for n in load_template(name)["frame_rule"]["lengths"])


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

    def system_stats(self, *, timeout=15) -> dict:
        return self._get("/system_stats", timeout=timeout)

    def object_info(self) -> dict:
        if self._object_info is None:
            self._object_info = self._get("/object_info", timeout=60)
        return self._object_info

    def reachable(self, *, timeout=15):
        try:
            stats = self.system_stats(timeout=timeout)
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

    def in_queue(self, prompt_id: str) -> bool:
        """Whether *prompt_id* is running or waiting (``GET /queue``)."""
        queue = self._get("/queue", timeout=30)
        for key in ("queue_running", "queue_pending"):
            for item in queue.get(key) or []:
                if isinstance(item, (list, tuple)) and len(item) > 1 and item[1] == prompt_id:
                    return True
        return False

    def free(self, *, unload_models: bool = True, free_memory: bool = True) -> None:
        """``POST /free``: unload the models and release the memory ComfyUI
        holds, for the next model to fit. No adapter calls it by itself; the
        caller decides when (between an image pass and a clip pass)."""
        request_json(self._transport, "POST", f"{self.base_url}/free", headers={},
                     json_body={"unload_models": bool(unload_models), "free_memory": bool(free_memory)},
                     timeout=self.timeout)

    def view(self, file: dict) -> bytes:
        query = urllib.parse.urlencode({
            "filename": file["filename"], "subfolder": file.get("subfolder", ""), "type": file.get("type", "output"),
        })
        return request_bytes(self._transport, "GET", f"{self.base_url}/view?{query}", headers={}, timeout=self.timeout)

    @staticmethod
    def _outputs(entry: dict) -> list:
        """Every output file, with the node that saved it. A video saved by the
        core ``SaveVideo`` node is listed under ``images`` with ``animated``
        true; VideoHelperSuite's combine node lists it under ``gifs``."""
        files = []
        for node_id, node_output in (entry.get("outputs") or {}).items():
            for kind in ("images", "gifs", "videos", "audio"):
                for file in node_output.get(kind) or []:
                    if file.get("filename"):
                        files.append({"filename": file["filename"], "subfolder": file.get("subfolder", ""),
                                      "type": file.get("type", "output"), "kind": kind, "node": str(node_id)})
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
                raise PromptFailed(f"prompt {prompt_id} failed in {data.get('node_type', 'a node')}: "
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
                    raise PromptFailed(self._error_message(prompt_id, status))
            if time_fn() >= deadline:
                raise ComfyUIError(f"prompt {prompt_id} not finished after {budget:.0f}s")
            sleep_fn(poll_interval)

    def run(self, graph: dict, *, on_log=print, sleep_fn=time.sleep, time_fn=time.monotonic,
            budget=WAIT_BUDGET_SECONDS, ws_factory=None) -> list:
        prompt_id = self.queue_prompt(graph)
        on_log(f"   🔁 ComfyUI: queued prompt {prompt_id} on {self.base_url}")
        return self.wait(prompt_id, on_log=on_log, sleep_fn=sleep_fn, time_fn=time_fn, budget=budget,
                         use_ws=True, ws_factory=ws_factory)


# ------------------------------------------------------------ status probe

# The story page's "is ComfyUI there?" (spec 8.1's stop and ask, shown before a
# step runs, not after): ``GET /system_stats`` -- a status request, never a
# generation -- with a short deadline, and its answer remembered per base URL
# for a minute, since the page is polled every few seconds while a step runs.
# The generation path never reads this: the runner's pre-check and
# ``generate`` ask the server afresh.
STATUS_PROBE_TIMEOUT_SECONDS = 2.0
STATUS_CACHE_SECONDS = 60.0
_now = time.monotonic           # the cache's clock (tests replace it)
_status_cache = {}              # base URL -> (checked at, ok, note)
_status_lock = threading.Lock()


def reset_status_cache() -> None:
    """Forget every remembered status (tests: one process runs them all)."""
    with _status_lock:
        _status_cache.clear()


def _status_within(base_url, *, transport, timeout):
    """``ComfyUIClient.reachable`` held to *timeout* seconds in all: the
    request runs in a daemon thread that is given that long, so a name lookup
    that hangs -- which no socket timeout covers -- cannot hold the caller
    longer. A thread still waiting is abandoned; its answer is dropped."""
    answer = []

    def ask():
        try:
            answer.append(ComfyUIClient(base_url, transport=transport).reachable(timeout=timeout))
        except Exception as exc:  # noqa: BLE001 - a status that breaks is a server that is not there
            answer.append((False, f"unreachable at {base_url} ({type(exc).__name__}: {exc})"))

    worker = threading.Thread(target=ask, name="comfyui-status", daemon=True)
    worker.start()
    worker.join(timeout)
    if not answer:
        return False, f"unreachable at {base_url} (no answer within {timeout:g}s)"
    return answer[0]


def probe_status(base_url, *, transport=None):
    """``(ok, note)``: whether ComfyUI at *base_url* answers ``GET
    /system_stats`` within :data:`STATUS_PROBE_TIMEOUT_SECONDS` -- never
    longer. The answer, either way, is remembered for
    :data:`STATUS_CACHE_SECONDS` per base URL; a remembered one is returned
    without a request."""
    key = base_url.rstrip("/")
    with _status_lock:
        hit = _status_cache.get(key)
        if hit is not None and _now() - hit[0] < STATUS_CACHE_SECONDS:
            return hit[1], hit[2]
    ok, note = _status_within(key, transport=transport, timeout=STATUS_PROBE_TIMEOUT_SECONDS)
    with _status_lock:
        _status_cache[key] = (_now(), ok, note)
    return ok, note


# ----------------------------------------------------------------- adapter

DEFAULT_TEMPLATES = {IMAGE: "t2i_flux2_klein", IMAGE_EDIT: "edit_flux2_klein_multiref"}


class ComfyUIImageAdapter:
    provider = "local"

    def estimate(self, link, request):
        return None

    def probe(self, link, *, credentials, transport=None, env=None):
        return ComfyUIClient(generation.local_url("comfyui", env), transport=transport).reachable()

    def probe_cached(self, link, *, credentials=None, transport=None, env=None):
        """The story page's probe (:func:`probe_status`): short, and
        remembered per server for a minute. The runner uses :meth:`probe`."""
        return probe_status(generation.local_url("comfyui", env), transport=transport)

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


# ------------------------------------------------------------ video adapter

class ComfyUIVideoAdapter:
    """One clip from one keyframe on the local ComfyUI (phase 6, stage 4).

    The template is the request's ``extra["template"]``, one of
    :data:`VIDEO_TEMPLATES`, which the caller picks from the hardware profile
    (``hardware.video_workflow_for``). Its ``frame_rule`` turns the clip's
    whole seconds into frames at the model's own fps and fixes the 9:16 size.
    The ``/prompt`` id is journaled the moment ComfyUI answers (``on_submit``),
    so a run that stops while the GPU works is followed again by
    :meth:`resume`, never queued twice (the DEC-152 shape; the clip is free,
    the GPU time is not). ``POST /free`` is never called here.

    Proven against a fake ComfyUI only: no graph of this adapter has run on a
    GPU (A-035).
    """

    provider = "local"

    def estimate(self, link, request):
        return None  # your own hardware: free, like the image adapter

    def probe(self, link, *, credentials, transport=None, env=None):
        return ComfyUIClient(generation.local_url("comfyui", env), transport=transport).reachable()

    def probe_cached(self, link, *, credentials=None, transport=None, env=None):
        return probe_status(generation.local_url("comfyui", env), transport=transport)

    @staticmethod
    def plan(request):
        """``(name, template, seconds, frames)`` after every check a clip must
        pass before anything is sent; ``ValueError`` names what is wrong."""
        name = (request.extra or {}).get("template")
        if name not in VIDEO_TEMPLATES:
            raise ValueError(f"local/comfyui: a clip needs extra['template'], one of {', '.join(VIDEO_TEMPLATES)} "
                             f"(hardware.video_workflow_for picks it from the profile), not {name!r}")
        if request.duration_s is None:
            raise ValueError(f"local/comfyui {name}: a clip needs duration_s, its length; without it the request "
                             "has no key and could not be journaled")
        if request.seed is None:
            raise ValueError(f"local/comfyui {name}: a clip needs a seed (it keys the request); without it the "
                             "request could not be journaled")
        count = len(request.references or ())
        if count != 1:
            raise ValueError(f"local/comfyui {name}: a clip is made from exactly one keyframe, got {count}")
        if not os.path.isfile(request.references[0]):
            raise ValueError(f"local/comfyui {name}: keyframe {request.references[0]} not found")
        if not request.out_dir:
            raise ValueError(f"local/comfyui {name}: GenRequest.out_dir is required: where the clip is written")
        aspect = (request.extra or {}).get("aspect")
        if aspect not in (None, "9:16"):
            # Plan 23 stage B7: the shipped workflows render a 9:16 frame only (v1).
            raise ValueError(f"local/comfyui {name} renders 9:16 clips only (v1), not {aspect}")
        template = load_template(name)
        rule = template["frame_rule"]
        duration = request.duration_s
        seconds = int(duration) if float(duration).is_integer() else duration
        if seconds not in rule["lengths"]:
            raise ValueError(f"local/comfyui {name} makes clips of {', '.join(str(n) for n in rule['lengths'])} s, "
                             f"not {duration!r}")
        if request.fps is not None and int(request.fps) != int(rule["fps"]):
            raise ValueError(f"local/comfyui {name} renders at {rule['fps']} fps, not {request.fps}")
        return name, template, seconds, frames_for(template, seconds)

    def generate(self, link, request, *, credentials, on_log, transport=None, env=None, sleep_fn=time.sleep,
                 time_fn=time.monotonic, ws_factory=None, on_submit=None, **_):
        name, template, seconds, frames = self.plan(request)  # refused here, before any call
        rule = template["frame_rule"]
        client = ComfyUIClient(generation.local_url("comfyui", env), transport=transport)
        problems = validate_template(template, client.object_info())
        if problems:
            raise ComfyUIError(install_message(name, client.base_url, problems))
        image = client.upload_image(request.references[0], subfolder="rzdhop")
        values = {"image_path": image, "prompt": request.prompt, "negative": request.negative or "",
                  "seed": request.seed, "width": rule["width"], "height": rule["height"], "frames": frames,
                  "fps": rule["fps"]}
        prompt_id = client.queue_prompt(render_template(template, values))
        on_log(f"   🔁 ComfyUI: queued {name} ({seconds} s = {frames} frames at {rule['fps']} fps, "
               f"{rule['width']}x{rule['height']}) as prompt {prompt_id} on {client.base_url}")
        if on_submit is not None:
            history = f"{client.base_url}/history/{prompt_id}"
            on_submit({"request_id": prompt_id, "status_url": history, "response_url": history,
                       "comfyui": client.base_url, "client_id": client.client_id})
        files = self._wait(client, prompt_id, name, on_log=on_log, sleep_fn=sleep_fn, time_fn=time_fn,
                           use_ws=True, ws_factory=ws_factory)
        return self._finish(link, request, client, prompt_id, name, template, seconds, frames, files)

    def resume(self, link, request, entry, *, credentials, on_log, transport=None, env=None, sleep_fn=time.sleep,
               time_fn=time.monotonic, **_):
        """Follow the prompt a journal *entry* holds, on the server that took
        it, until its clip is there. Never a new ``/prompt``; a prompt ComfyUI
        no longer knows is :class:`PromptGone`."""
        name, template, seconds, frames = self.plan(request)
        queued = dict(entry.get("request") or {})
        prompt_id = queued.get("request_id")
        if not prompt_id:
            raise ComfyUIError("local/comfyui: the journal holds no prompt to resume")
        client = ComfyUIClient(queued.get("comfyui") or generation.local_url("comfyui", env), transport=transport)
        on_log(f"   ↩️ ComfyUI: following prompt {prompt_id} on {client.base_url} (not queued again)")
        if not (client.history(prompt_id) or {}).get(prompt_id) and not client.in_queue(prompt_id):
            raise PromptGone(f"prompt {prompt_id} is neither in the history nor in the queue of {client.base_url}")
        files = self._wait(client, prompt_id, name, on_log=on_log, sleep_fn=sleep_fn, time_fn=time_fn,
                           use_ws=False, ws_factory=None)
        return self._finish(link, request, client, prompt_id, name, template, seconds, frames, files)

    @staticmethod
    def _wait(client, prompt_id, name, *, on_log, sleep_fn, time_fn, use_ws, ws_factory):
        try:
            return client.wait(prompt_id, on_log=on_log, sleep_fn=sleep_fn, time_fn=time_fn,
                               budget=VIDEO_WAIT_BUDGET_SECONDS, use_ws=use_ws, ws_factory=ws_factory)
        except PromptFailed as exc:
            # Settled: resuming cannot help, and the journal lets a later run queue it afresh.
            raise RequestFailed(f"local/comfyui {name}: {exc}") from exc

    @staticmethod
    def _video(files, output_node):
        """The ``.mp4`` the template's save node wrote (``images`` + ``animated``
        for core ``SaveVideo``, ``videos`` or ``gifs`` for other savers)."""
        clips = [f for f in files if f["kind"] != "audio" and f["filename"].lower().endswith(".mp4")]
        own = [f for f in clips if f.get("node") == str(output_node)]
        return (own or clips or [None])[0]

    def _finish(self, link, request, client, prompt_id, name, template, seconds, frames, files):
        rule = template["frame_rule"]
        video = self._video(files, template["output_node"])
        if video is None:
            got = ", ".join(f["filename"] for f in files) or "nothing"
            raise RequestFailed(f"local/comfyui {name}: prompt {prompt_id} finished without an .mp4 (got {got})")
        data = client.view(video)
        if not data:
            raise ComfyUIError(f"local/comfyui {name}: {video['filename']} downloaded empty")
        out_name = (request.extra or {}).get("name") or f"comfyui_{name}_{request.seed}"
        path = write_output(request.out_dir, out_name, data, "mp4")
        return GenResult(provider="local", model=link.model, paths=(path,), seed=request.seed, meta={
            "template": name, "prompt_id": prompt_id, "comfyui": client.base_url, "output": video["filename"],
            "clip_s": seconds, "frames": frames, "fps": int(rule["fps"]),
            "width": int(rule["width"]), "height": int(rule["height"]),
            "has_audio": False, "seed_honoured": True,
        })


COMFYUI_VIDEO = ComfyUIVideoAdapter()
register_adapter(VIDEO, "local", COMFYUI_VIDEO)
