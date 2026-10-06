"""Local ComfyUI image-to-video (AI Story phase 6, stage 4; spec 8.3, 8.7).

Three API-format templates authored from ComfyUI's own published graphs
(Wan 2.2 5B, Wan 2.2 14B with the Lightning 4-step LoRAs, LTX-2), each with
a frame rule that turns whole seconds into the model's frame count. The
adapter refuses before any call what it could not run, validates against
``/object_info`` before queueing, uploads the keyframe, forwards ``/ws``
progress, reads the video out of ``/history`` and writes an ``.mp4``; a
journaled prompt is resumed by its id and never queued twice.

There is no GPU here: every ComfyUI answer is a fake (A-035 stays open).
"""

import json
import pathlib
import urllib.parse

import pytest

from clipping.aistory import hardware, video_plan
from clipping.providers import generation, local_comfyui
from clipping.providers.generation import GenRequest
from clipping.providers.registry import Link
from clipping.providers.transport import Response

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "clipping" / "aistory" / "templates" / "workflows"
LOCAL = Link("local", "comfyui")
BASE = "http://127.0.0.1:8188"
ENV = {"LOCAL_COMFYUI_URL": BASE}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16


class FakeComfy:
    """Answers by URL path prefix (the last answer of a route repeats); records every call."""

    def __init__(self, routes=None):
        self.routes = {prefix: list(answers) for prefix, answers in (routes or {}).items()}
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "body": body})
        path = urllib.parse.urlsplit(url).path
        for prefix, answers in self.routes.items():
            if path.startswith(prefix):
                answer = answers.pop(0) if len(answers) > 1 else answers[0]
                status, payload = answer
                if isinstance(payload, (dict, list)):
                    payload = json.dumps(payload).encode()
                return Response(status, {}, payload)
        raise AssertionError(f"unexpected request {method} {url}")

    def paths(self, method=None):
        return [urllib.parse.urlsplit(c["url"]).path for c in self.calls if method in (None, c["method"])]


def object_info_for(template, *, drop_class=None, drop_file=None):
    info = {}
    for node in template["graph"].values():
        info.setdefault(node["class_type"], {"input": {"required": {}}})
    for req in template["requires"]:
        class_type = template["graph"][req["node"]]["class_type"]
        field = info[class_type]["input"]["required"].setdefault(req["field"], [[]])
        if req["file"] != drop_file and req["file"] not in field[0]:
            field[0].append(req["file"])
    info.pop(drop_class, None)
    return info


def keyframe(tmp_path):
    path = tmp_path / "shot_03.png"
    path.write_bytes(PNG)
    return str(path)


def clip_request(tmp_path, **changes):
    fields = dict(kind="video", prompt="a kiwi waves", negative="flicker", seed=7, duration_s=5.0,
                  references=(keyframe(tmp_path),), out_dir=str(tmp_path / "out"),
                  extra={"template": "i2v_wan22_5b", "name": "shot_03"})
    fields.update(changes)
    return GenRequest(**fields)


# --------------------------------------------------------------- templates

@pytest.mark.parametrize("name, seconds, frames, fps, size", [
    ("i2v_wan22_5b", 5, 121, 24, (704, 1280)),
    ("i2v_wan22_14b_lightning", 5, 81, 16, (480, 832)),
    ("i2v_ltx2", 4, 105, 25, (704, 1280)),
])
def test_each_video_template_takes_the_keyframe_prompt_and_typed_numbers(name, seconds, frames, fps, size):
    template = local_comfyui.load_template(name)
    rule = template["frame_rule"]
    # DEC-310: i2v_wan22_14b_lightning ran live on 2026-10-06 (an RTX 5090 pod, an L40S RunPod worker); the
    # other two are still proven against a fake ComfyUI only (A-035 stays open for them).
    assert template["verified_live"] is (name == "i2v_wan22_14b_lightning") and template["task"] == "i2v"
    assert (rule["fps"], (rule["width"], rule["height"])) == (fps, size)
    assert local_comfyui.frames_for(template, seconds) == frames and (frames - 1) % rule["frame_step"] == 0
    values = {"image_path": "rzdhop/shot_03.png", "prompt": "a kiwi waves", "negative": "flicker", "seed": 7,
              "width": size[0], "height": size[1], "frames": frames, "fps": fps}
    graph = local_comfyui.render_template(template, values)
    assert "{{" not in json.dumps(graph)
    typed = 0
    for node_id, node in template["graph"].items():
        for key, value in node["inputs"].items():
            field = value[2:-2] if isinstance(value, str) and value.startswith("{{") else None
            if field in local_comfyui.TYPED:
                rendered = graph[node_id]["inputs"][key]
                assert type(rendered) is int and rendered == values[field], (node_id, key, rendered)
                typed += 1
    assert typed >= 5, "seed, width, height, frames and fps all reach the graph"
    assert [n["inputs"]["image"] for n in graph.values() if n["class_type"] == "LoadImage"] == ["rzdhop/shot_03.png"]
    texts = [n["inputs"]["text"] for n in graph.values() if n["class_type"] == "CLIPTextEncode"]
    assert "a kiwi waves" in texts and "flicker" in texts
    assert graph[template["output_node"]]["class_type"] == "SaveVideo"


def test_every_shipped_workflow_parses_and_uses_only_known_placeholders():
    names = sorted(p.stem for p in WORKFLOWS.glob("*.json"))
    assert set(local_comfyui.VIDEO_TEMPLATES) <= set(names)
    for name in names:
        template = json.loads((WORKFLOWS / f"{name}.json").read_text(encoding="utf-8"))
        assert template["$schema"] == "comfy_workflow_v1" and template["name"] == name
        assert isinstance(template["verified_live"], bool), name
        assert template["requires"], name
        graph = template["graph"]
        for req in template["requires"]:
            assert set(req) >= {"node", "field", "file", "dir"}, (name, req)
            assert graph[req["node"]]["inputs"][req["field"]] == req["file"], (name, req)
        used = set()
        for node in graph.values():
            for value in node["inputs"].values():
                if isinstance(value, str):
                    used.update(local_comfyui.PLACEHOLDER.findall(value))
        assert used <= local_comfyui.KNOWN_PLACEHOLDERS, (name, used - local_comfyui.KNOWN_PLACEHOLDERS)
        assert used == set(template["placeholders"]) - {"ref_paths"}, (name, used)


# ----------------------------------------------------------------- adapter

def test_a_missing_node_and_model_file_are_named_before_anything_is_queued(tmp_path):
    template = local_comfyui.load_template("i2v_wan22_5b")
    info = object_info_for(template, drop_class="Wan22ImageToVideoLatent", drop_file="wan2.2_vae.safetensors")
    transport = FakeComfy({"/object_info": [(200, info)]})
    with pytest.raises(local_comfyui.ComfyUIError) as excinfo:
        local_comfyui.COMFYUI_VIDEO.generate(LOCAL, clip_request(tmp_path), credentials={}, on_log=lambda *a: None,
                                             transport=transport, env=ENV)
    message = str(excinfo.value)
    assert "Wan22ImageToVideoLatent" in message
    assert "wan2.2_vae.safetensors" in message and "models/vae" in message
    assert transport.paths("POST") == [], "nothing uploaded, nothing queued"


def test_a_clip_is_uploaded_queued_followed_fetched_and_written_as_mp4(tmp_path):
    template = local_comfyui.load_template("i2v_wan22_5b")
    done = {"p9": {"status": {"status_str": "success", "completed": True, "messages": []},
                   "outputs": {template["output_node"]: {
                       "images": [{"filename": "i2v_wan22_5b_00001_.mp4", "subfolder": "rzdhop_ai", "type": "output"}],
                       "animated": [True]}}}}
    transport = FakeComfy({
        "/object_info": [(200, object_info_for(template))],
        "/upload/image": [(200, {"name": "shot_03.png", "subfolder": "rzdhop", "type": "input"})],
        "/prompt": [(200, {"prompt_id": "p9", "number": 1, "node_errors": {}})],
        "/history/p9": [(200, done)],
        "/view": [(200, MP4)],
        "/free": [(200, b"")],
    })

    def events(*a, **kw):
        yield {"type": "progress", "data": {"value": 10, "max": 20, "prompt_id": "p9"}}
        yield {"type": "executing", "data": {"node": None, "prompt_id": "p9"}}

    log, submitted = [], []
    assert generation.adapter_for("video", "local") is local_comfyui.COMFYUI_VIDEO
    result = local_comfyui.COMFYUI_VIDEO.generate(
        LOCAL, clip_request(tmp_path), credentials={}, on_log=log.append, transport=transport, env=ENV,
        sleep_fn=lambda s: None, ws_factory=events, on_submit=submitted.append)
    assert transport.paths() == ["/object_info", "/upload/image", "/prompt", "/history/p9", "/view"]
    graph = json.loads(transport.calls[2]["body"])["prompt"]
    assert graph["55"]["inputs"]["length"] == 121 and graph["57"]["inputs"]["fps"] == 24
    assert [n["inputs"]["image"] for n in graph.values() if n["class_type"] == "LoadImage"] == ["rzdhop/shot_03.png"]
    assert any("10/20" in line for line in log)
    assert [s["request_id"] for s in submitted] == ["p9"]
    assert result.paths[0].endswith("shot_03.mp4") and pathlib.Path(result.paths[0]).read_bytes() == MP4
    assert (result.provider, result.model, result.seed) == ("local", "comfyui", 7)
    assert result.meta["has_audio"] is False and result.meta["seed_honoured"] is True
    assert (result.meta["template"], result.meta["frames"], result.meta["fps"]) == ("i2v_wan22_5b", 121, 24)
    assert local_comfyui.COMFYUI_VIDEO.estimate(LOCAL, clip_request(tmp_path)) is None, "a local clip is free"
    assert "/free" not in transport.paths(), "the adapter never frees ComfyUI by itself"

    local_comfyui.ComfyUIClient(BASE, transport=transport).free()
    frees = [c for c in transport.calls if c["url"].endswith("/free")]
    assert len(frees) == 1 and frees[0]["method"] == "POST"
    assert json.loads(frees[0]["body"]) == {"unload_models": True, "free_memory": True}


def test_resume_polls_the_journaled_prompt_and_never_queues_it_again(tmp_path):
    done = {"p9": {"status": {"status_str": "success", "completed": True},
                   "outputs": {"58": {"images": [{"filename": "clip_00001_.mp4", "subfolder": "", "type": "output"}],
                                      "animated": [True]}}}}
    transport = FakeComfy({"/history/p9": [(200, done)], "/view": [(200, MP4)]})
    entry = {"request": {"request_id": "p9", "comfyui": BASE}, "seed": 7}
    result = local_comfyui.COMFYUI_VIDEO.resume(LOCAL, clip_request(tmp_path), entry, credentials={},
                                                on_log=lambda *a: None, transport=transport, env=ENV,
                                                sleep_fn=lambda s: None)
    assert pathlib.Path(result.paths[0]).read_bytes() == MP4 and result.meta["prompt_id"] == "p9"
    assert transport.paths("POST") == [], "a journaled prompt is never queued twice"

    gone = FakeComfy({"/history/p9": [(200, {})], "/queue": [(200, {"queue_running": [], "queue_pending": []})]})
    with pytest.raises(local_comfyui.ComfyUIError) as excinfo:
        local_comfyui.COMFYUI_VIDEO.resume(LOCAL, clip_request(tmp_path), entry, credentials={},
                                           on_log=lambda *a: None, transport=gone, env=ENV, sleep_fn=lambda s: None)
    assert excinfo.value.status_code == 404, "gone from ComfyUI: the journal marks it lost"
    assert gone.paths("POST") == []


@pytest.mark.parametrize("changes, words", [
    ({"seed": None}, "seed"),
    ({"duration_s": 7.0}, "2, 3, 4, 5"),
    ({"extra": {"template": "i2v_unknown"}}, "i2v_unknown"),
])
def test_what_could_not_be_journaled_or_run_is_refused_before_any_call(tmp_path, changes, words):
    transport = FakeComfy()
    with pytest.raises(ValueError) as excinfo:
        local_comfyui.COMFYUI_VIDEO.generate(LOCAL, clip_request(tmp_path, **changes), credentials={},
                                             on_log=lambda *a: None, transport=transport, env=ENV)
    assert words in str(excinfo.value)
    assert transport.calls == []


# ---------------------------------------------------------------- hardware

def test_the_profile_picks_the_video_workflow_and_its_lengths():
    assert {name: hardware.video_workflow_for(name) for name in hardware.PROFILES} == {
        "cpu_only": None, "low": None, "mid": "i2v_wan22_5b", "high": "i2v_wan22_14b_lightning",
        "pro": "i2v_ltx2", "apple_mps": None, "container_no_gpu": None,
    }
    for name in hardware.PROFILES:
        rows = [r for r in hardware.recommendations_for(name) if r["task"] == "video"]
        assert rows and not any("phase 6" in r["model"] + r["install_hint"] for r in rows), name
        expected = hardware.video_workflow_for(name)
        assert (expected in [r["workflow"] for r in rows]) if expected else not any(r["workflow"] for r in rows), name
    lengths = local_comfyui.video_clip_lengths("i2v_wan22_5b")
    assert lengths == (2, 3, 4, 5)
    assert video_plan.requested_seconds("local/comfyui", 3.2, lengths=lengths) == 4
