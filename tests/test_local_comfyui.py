"""The local ComfyUI client and its workflow templates (spec 8.3, 8.7).

A template is data: an API-format graph with ``{{placeholders}}`` and a
``requires`` list of model files; at run time every class_type is checked
against ``/object_info`` and every required file against the loader node's
choices, and a miss is refused with the list of what to install -- never a
silent fallback to an API. Progress comes over ``/ws`` through a stdlib
frame reader, with ``/history`` polling when the socket is not available.
"""

import base64
import hashlib
import json
import pathlib
import socket
import struct
import threading

import pytest

from clipping.providers import generation, local_comfyui
from clipping.providers.generation import GenRequest
from clipping.providers.local_comfyui import (
    ComfyUIClient, ComfyUIError, TEMPLATES, load_template, render_template, validate_template, ws_frames,
)
from clipping.providers.registry import Link
from clipping.providers.transport import APIConnectionError, Response

ROOT = pathlib.Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


# --------------------------------------------------------------- templates

@pytest.mark.parametrize("name", TEMPLATES)
def test_every_shipped_template_is_well_formed(name):
    template = load_template(name)
    assert template["$schema"] == "comfy_workflow_v1"
    assert template["name"] == name
    assert template["task"] in ("t2i", "edit", "i2v")
    assert template["min_profile"] in ("cpu_only", "low", "mid", "high", "pro", "apple_mps")
    graph = template["graph"]
    assert isinstance(graph, dict) and graph
    for node_id, node in graph.items():
        assert "class_type" in node and "inputs" in node, node_id
    # every placeholder used in the graph is declared, and vice versa
    used = set()
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, str):
                used.update(local_comfyui.PLACEHOLDER.findall(value))
    assert used == set(template["placeholders"]) - {"ref_paths"}, (used, template["placeholders"])
    for req in template["requires"]:
        assert req["node"] in graph and req["field"] in graph[req["node"]]["inputs"], req
        assert graph[req["node"]]["inputs"][req["field"]] == req["file"], req
    if template["task"] == "edit":
        assert template["ref_slots"] >= 1 and len(template["ref_nodes"]) == template["ref_slots"]
        for node_id in template["ref_nodes"]:
            assert graph[node_id]["class_type"] == "LoadImage"
    assert graph[template["output_node"]]["class_type"] == "SaveImage"


def test_the_three_templates_of_phase_0_are_shipped():
    assert set(TEMPLATES) == {"t2i_flux2_klein", "edit_flux2_klein_multiref", "edit_qwen_image"}
    assert load_template("edit_flux2_klein_multiref")["ref_slots"] == 4
    assert any(r["file"] == "flux2-klein-4b.safetensors" for r in load_template("t2i_flux2_klein")["requires"])
    assert any(r["file"] == "qwen_image_edit_2509_q4.gguf" for r in load_template("edit_qwen_image")["requires"])


def test_rendering_substitutes_typed_values_and_pads_reference_slots():
    template = load_template("edit_flux2_klein_multiref")
    graph = render_template(template, {"prompt": "a kiwi", "negative": "blurry", "seed": 7, "width": 1080, "height": 1920,
                                       "ref_paths": ["a.png", "b.png"]})
    sampler = next(n for n in graph.values() if n["class_type"] == "KSampler")
    assert sampler["inputs"]["seed"] == 7 and isinstance(sampler["inputs"]["seed"], int)
    loads = [graph[i]["inputs"]["image"] for i in template["ref_nodes"]]
    assert loads == ["a.png", "b.png", "b.png", "b.png"], "unused slots repeat the last reference"
    texts = [n["inputs"].get("text") for n in graph.values() if n["class_type"] == "CLIPTextEncode"]
    assert "a kiwi" in texts and "blurry" in texts
    latent = next(n for n in graph.values() if n["class_type"].startswith("Empty"))
    assert latent["inputs"]["width"] == 1080 and latent["inputs"]["height"] == 1920
    assert "{{" not in json.dumps(graph)


def test_rendering_refuses_missing_values_and_too_many_references():
    template = load_template("edit_flux2_klein_multiref")
    with pytest.raises(ValueError) as excinfo:
        render_template(template, {"prompt": "x", "ref_paths": ["a.png"]})
    assert "seed" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        render_template(template, {"prompt": "x", "negative": "", "seed": 1, "width": 8, "height": 8, "ref_paths": ["1"] * 5})
    assert "4" in str(excinfo.value)
    with pytest.raises(ValueError):
        render_template(template, {"prompt": "x", "negative": "", "seed": 1, "width": 8, "height": 8, "ref_paths": []})


def object_info_for(template, *, drop_class=None, drop_file=None):
    info = {}
    for node in template["graph"].values():
        entry = info.setdefault(node["class_type"], {"input": {"required": {}}})
        for req in template["requires"]:
            if template["graph"][req["node"]]["class_type"] == node["class_type"]:
                choices = [req["file"]] if req["file"] != drop_file else []
                entry["input"]["required"][req["field"]] = [choices]
    if drop_class:
        info.pop(drop_class, None)
    return info


def test_validation_names_the_missing_custom_node_and_model_file():
    template = load_template("edit_qwen_image")
    assert validate_template(template, object_info_for(template)) == []
    problems = validate_template(template, object_info_for(template, drop_class="UnetLoaderGGUF"))
    assert any("UnetLoaderGGUF" in p and "not installed" in p for p in problems)
    problems = validate_template(template, object_info_for(template, drop_file="qwen_image_edit_2509_q4.gguf"))
    assert any("qwen_image_edit_2509_q4.gguf" in p and "unet_name" in p for p in problems)


# ------------------------------------------------------------------ client

class FakeTransport:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, payload = answer
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode()
        return Response(status, {}, payload)


def test_queue_prompt_sends_the_graph_and_surfaces_node_errors():
    transport = FakeTransport([(200, {"prompt_id": "p1", "number": 3, "node_errors": {}})])
    client = ComfyUIClient("http://127.0.0.1:8188", transport=transport, client_id="cid")
    assert client.queue_prompt({"1": {"class_type": "X", "inputs": {}}}) == "p1"
    assert transport.calls[0]["url"] == "http://127.0.0.1:8188/prompt"
    assert json.loads(transport.calls[0]["body"]) == {"prompt": {"1": {"class_type": "X", "inputs": {}}}, "client_id": "cid"}
    transport = FakeTransport([(200, {"prompt_id": "p2", "node_errors": {"3": {"errors": [{"message": "Value not in list", "details": "ckpt_name: 'x' not in []"}]}}})])
    with pytest.raises(ComfyUIError) as excinfo:
        ComfyUIClient("http://127.0.0.1:8188", transport=transport).queue_prompt({})
    assert "node 3" in str(excinfo.value) and "Value not in list" in str(excinfo.value)


def test_upload_image_is_multipart_and_returns_the_stored_name(tmp_path):
    ref = tmp_path / "portrait.png"
    ref.write_bytes(PNG)
    transport = FakeTransport([(200, {"name": "portrait.png", "subfolder": "rzdhop", "type": "input"})])
    client = ComfyUIClient("http://127.0.0.1:8188", transport=transport)
    assert client.upload_image(str(ref), subfolder="rzdhop") == "rzdhop/portrait.png"
    call = transport.calls[0]
    assert call["url"] == "http://127.0.0.1:8188/upload/image"
    assert call["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="image"; filename="portrait.png"' in call["body"] and PNG in call["body"]
    assert b'name="subfolder"' in call["body"] and b"rzdhop" in call["body"]


def history_done(prompt_id="p1"):
    return {prompt_id: {"status": {"status_str": "success", "completed": True, "messages": []},
                        "outputs": {"9": {"images": [{"filename": "rzdhop_ai_00001_.png", "subfolder": "", "type": "output"}]}}}}


def test_polling_history_waits_then_fetches_the_outputs():
    transport = FakeTransport([(200, {}), (200, {}), (200, history_done()), (200, PNG)])
    client = ComfyUIClient("http://127.0.0.1:8188", transport=transport)
    slept = []
    outputs = client.wait("p1", on_log=lambda *a: None, sleep_fn=slept.append, use_ws=False)
    assert [f["filename"] for f in outputs] == ["rzdhop_ai_00001_.png"]
    assert len(slept) == 2
    data = client.view(outputs[0])
    assert data == PNG
    assert transport.calls[-1]["url"] == "http://127.0.0.1:8188/view?filename=rzdhop_ai_00001_.png&subfolder=&type=output"


def test_a_failed_prompt_is_reported_with_comfyuis_message():
    failed = {"p1": {"status": {"status_str": "error", "completed": False,
                                "messages": [["execution_error", {"exception_message": "CUDA out of memory", "node_type": "KSampler"}]]},
                     "outputs": {}}}
    transport = FakeTransport([(200, failed)])
    client = ComfyUIClient("http://127.0.0.1:8188", transport=transport)
    with pytest.raises(ComfyUIError) as excinfo:
        client.wait("p1", on_log=lambda *a: None, sleep_fn=lambda s: None, use_ws=False)
    assert "CUDA out of memory" in str(excinfo.value) and "KSampler" in str(excinfo.value)


def test_waiting_gives_up_after_its_budget():
    transport = FakeTransport([(200, {})] * 50)
    client = ComfyUIClient("http://127.0.0.1:8188", transport=transport)
    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    with pytest.raises(ComfyUIError) as excinfo:
        client.wait("p1", on_log=lambda *a: None, sleep_fn=sleep, time_fn=lambda: clock["now"], budget=10, use_ws=False)
    assert "10s" in str(excinfo.value)


def test_reachable_reads_system_stats():
    transport = FakeTransport([(200, {"system": {"comfyui_version": "0.3.9", "os": "posix"}, "devices": [{"name": "cuda:0 NVIDIA RTX 4090", "vram_total": 25757220864}]})])
    ok, note = ComfyUIClient("http://127.0.0.1:8188", transport=transport).reachable()
    assert ok is True and "0.3.9" in note and "RTX 4090" in note
    transport = FakeTransport([APIConnectionError("refused")])
    ok, note = ComfyUIClient("http://host.docker.internal:8188", transport=transport).reachable()
    assert ok is False and "unreachable at http://host.docker.internal:8188" in note


# ------------------------------------------------------------ websocket

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def frame(opcode, payload):
    head = bytes([0x80 | opcode])
    if len(payload) < 126:
        return head + bytes([len(payload)]) + payload
    return head + bytes([126]) + struct.pack(">H", len(payload)) + payload


def serve_once(messages, ready, port_holder):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port_holder.append(server.getsockname()[1])
    ready.set()
    conn, _ = server.accept()
    request = b""
    while b"\r\n\r\n" not in request:
        request += conn.recv(4096)
    key = next(line.split(b":", 1)[1].strip() for line in request.split(b"\r\n") if line.lower().startswith(b"sec-websocket-key"))
    accept = base64.b64encode(hashlib.sha1(key + GUID.encode()).digest()).decode()
    conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode())
    for opcode, payload in messages:
        conn.sendall(frame(opcode, payload))
    conn.close()
    server.close()


def test_the_frame_reader_yields_text_frames_and_skips_binary_previews():
    long_text = json.dumps({"type": "progress", "data": {"value": 3, "max": 20, "prompt_id": "p1", "pad": "x" * 200}})
    messages = [
        (1, json.dumps({"type": "status", "data": {"status": {"exec_info": {"queue_remaining": 1}}}}).encode()),
        (2, b"\x00\x01preview-bytes"),
        (1, long_text.encode()),
        (1, json.dumps({"type": "executing", "data": {"node": None, "prompt_id": "p1"}}).encode()),
        (8, struct.pack(">H", 1000)),
    ]
    ready, port = threading.Event(), []
    thread = threading.Thread(target=serve_once, args=(messages, ready, port), daemon=True)
    thread.start()
    ready.wait(5)
    events = list(ws_frames("http://127.0.0.1:%d" % port[0], "cid", timeout=5))
    thread.join(5)
    assert [e["type"] for e in events] == ["status", "progress", "executing"]
    assert events[1]["data"]["max"] == 20


def test_run_falls_back_to_polling_when_the_socket_is_unavailable():
    transport = FakeTransport([(200, {"prompt_id": "p1", "node_errors": {}}), (200, history_done()), (200, PNG)])
    client = ComfyUIClient("http://127.0.0.1:8188", transport=transport, client_id="cid")
    log = []

    def no_socket(*a, **kw):
        raise APIConnectionError("no websocket here")
        yield  # noqa: unreachable, makes this a generator factory

    files = client.run({"1": {"class_type": "X", "inputs": {}}}, on_log=log.append, sleep_fn=lambda s: None, ws_factory=no_socket)
    assert [f["filename"] for f in files] == ["rzdhop_ai_00001_.png"]
    assert any("↩" in line and "polling /history" in line for line in log)


def test_run_reports_progress_from_the_socket():
    transport = FakeTransport([(200, {"prompt_id": "p1", "node_errors": {}}), (200, history_done())])
    client = ComfyUIClient("http://127.0.0.1:8188", transport=transport, client_id="cid")
    log = []

    def events(*a, **kw):
        yield {"type": "progress", "data": {"value": 10, "max": 20, "prompt_id": "p1"}}
        yield {"type": "executing", "data": {"node": None, "prompt_id": "p1"}}

    files = client.run({"1": {"class_type": "X", "inputs": {}}}, on_log=log.append, sleep_fn=lambda s: None, ws_factory=events)
    assert files and any("10/20" in line for line in log)


# ----------------------------------------------------------------- adapter

def test_the_comfyui_adapter_is_registered_for_image_and_edit():
    assert generation.adapter_for("image", "local") is local_comfyui.COMFYUI
    assert generation.adapter_for("image_edit", "local") is local_comfyui.COMFYUI
    assert generation.adapter_for("video", "local") is None
    assert local_comfyui.COMFYUI.estimate(Link("local", "comfyui"), GenRequest(kind="image")) is None


def test_the_adapter_validates_before_queueing_and_lists_what_to_install(tmp_path):
    template = load_template("t2i_flux2_klein")
    transport = FakeTransport([(200, object_info_for(template, drop_file="flux2-klein-4b.safetensors"))])
    request = GenRequest(kind="image", prompt="a kiwi", out_dir=str(tmp_path))
    with pytest.raises(ComfyUIError) as excinfo:
        local_comfyui.COMFYUI.generate(Link("local", "comfyui"), request, credentials={}, on_log=lambda *a: None,
                                       transport=transport, env={"LOCAL_COMFYUI_URL": "http://127.0.0.1:8188"})
    assert "flux2-klein-4b.safetensors" in str(excinfo.value)
    assert all("/prompt" not in c["url"] for c in transport.calls)


def test_the_adapter_uploads_references_renders_runs_and_writes(tmp_path):
    ref = tmp_path / "portrait.png"
    ref.write_bytes(PNG)
    template = load_template("edit_flux2_klein_multiref")
    transport = FakeTransport([
        (200, object_info_for(template)),
        (200, {"name": "portrait.png", "subfolder": "rzdhop", "type": "input"}),
        (200, {"prompt_id": "p1", "node_errors": {}}),
        (200, history_done()),
        (200, PNG),
    ])
    request = GenRequest(kind="image_edit", prompt="a kiwi", negative="blurry", seed=3, references=(str(ref),),
                         out_dir=str(tmp_path / "out"), extra={"name": "shot_01"})
    result = local_comfyui.COMFYUI.generate(Link("local", "comfyui"), request, credentials={}, on_log=lambda *a: None,
                                            transport=transport, env={"LOCAL_COMFYUI_URL": "http://127.0.0.1:8188"},
                                            sleep_fn=lambda s: None, ws_factory=lambda *a, **kw: iter(()))
    graph = json.loads(transport.calls[2]["body"])["prompt"]
    loads = [graph[i]["inputs"]["image"] for i in template["ref_nodes"]]
    assert loads == ["rzdhop/portrait.png"] * 4
    assert result.paths[0].endswith("shot_01.png") and pathlib.Path(result.paths[0]).read_bytes() == PNG
    assert result.provider == "local" and result.model == "comfyui" and result.seed == 3
    assert result.meta["template"] == "edit_flux2_klein_multiref" and result.meta["prompt_id"] == "p1"


def test_the_adapter_probe_reports_reachability():
    transport = FakeTransport([APIConnectionError("refused")])
    ok, note = local_comfyui.COMFYUI.probe(Link("local", "comfyui"), credentials={}, transport=transport,
                                           env={"LOCAL_COMFYUI_URL": "http://host.docker.internal:8188"})
    assert ok is False and "unreachable at http://host.docker.internal:8188" in note


def test_ollama_chat_can_ask_for_a_json_schema():
    from clipping.providers.local_ollama import OllamaClient

    transport = FakeTransport([(200, {"message": {"content": "{\"a\": 1}"}})])
    OllamaClient("http://127.0.0.1:11434", transport=transport).chat("qwen3:4b", [{"role": "user", "content": "x"}],
                                                                   format={"type": "object"})
    assert json.loads(transport.calls[0]["body"])["format"] == {"type": "object"}
