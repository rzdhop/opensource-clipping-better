"""The sound-to-video workflow template (plan 32 stage 7): Wan 2.2 S2V on core
ComfyUI nodes, a keyframe and a spoken line in, a talking clip out. Rendering and
validation only -- there is no GPU here, so ``verified_live`` stays false -- plus
the Dockerfile link the audio encoder folder needs on the worker image."""

import pathlib

import pytest

from clipping.providers import local_comfyui
from clipping.providers.local_comfyui import frames_for, load_template, render_template, validate_template
from mcp_server.config import Settings
from mcp_server.runpod_jobs import JobClient, JobError, list_templates

ROOT = pathlib.Path(__file__).resolve().parent.parent
VALUES = {"prompt": "a pear says hello", "negative": "blurry", "seed": 7, "image_path": "rzdhop_key.png",
          "audio_path": "rzdhop_line.wav", "width": 480, "height": 832, "frames": 81, "fps": 16}
FILES = {"wan2.2_s2v_14B_fp8_scaled.safetensors", "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
         "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "wan_2.1_vae.safetensors",
         "wav2vec2_large_english_fp16.safetensors"}


def object_info_for(template, *, drop_class=None, choices=None):
    """A fake /object_info: every node class of the graph, the model combos filled from ``requires``."""
    info = {node["class_type"]: {"input": {"required": {}}} for node in template["graph"].values()}
    for req in template["requires"]:
        if req["field"]:
            wanted = (choices or {}).get(req["file"], [req["file"]])
            info[template["graph"][req["node"]]["class_type"]]["input"]["required"][req["field"]] = [wanted]
    info.pop(drop_class, None)
    return info


def test_the_s2v_template_renders_a_keyframe_and_a_voice_line_into_one_77_frame_chunk():
    template = load_template("s2v_wan22")
    assert template["task"] == "s2v" and template["kind"] == "video" and template["verified_live"] is False
    assert template["core_nodes_only"] is True
    graph = render_template(template, VALUES)
    assert template["audio_node"] == "7" and template["image_node"] == "9" and template["output_node"] == "25"
    assert graph["7"] == {"class_type": "LoadAudio", "inputs": {"audio": "rzdhop_line.wav"}}
    assert graph["9"] == {"class_type": "LoadImage", "inputs": {"image": "rzdhop_key.png"}}
    assert graph["25"]["class_type"] == "SaveVideo"
    assert graph["25"]["inputs"]["video"] == ["24", 0]
    s2v = graph["12"]
    assert s2v["class_type"] == "WanSoundImageToVideo"
    assert s2v["inputs"]["length"] == 77 and s2v["inputs"]["batch_size"] == 1  # fixed: the model needs >= 73
    assert s2v["inputs"]["width"] == 480 and s2v["inputs"]["height"] == 832
    assert s2v["inputs"]["audio_encoder_output"] == ["8", 0] and s2v["inputs"]["ref_image"] == ["9", 0]
    assert graph["8"]["inputs"] == {"audio_encoder": ["6", 0], "audio": ["7", 0]}
    sampler = graph["13"]["inputs"]
    assert sampler["seed"] == 7 and sampler["steps"] == 4 and sampler["cfg"] == 1.0
    assert (sampler["sampler_name"], sampler["scheduler"]) == ("uni_pc", "simple")
    assert graph["3"]["inputs"]["shift"] == 8.0 and graph["3"]["inputs"]["model"] == ["2", 0]
    # the first-frame hack: double the first latent frame, drop the first decoded frames
    assert graph["20"]["inputs"] == {"samples": ["13", 0], "dim": "t", "index": 0, "amount": 1}
    assert graph["21"]["inputs"] == {"samples1": ["20", 0], "samples2": ["13", 0], "dim": "t"}
    assert graph["22"]["inputs"]["samples"] == ["21", 0]
    assert graph["23"]["inputs"]["batch_index"] == 3 and graph["23"]["inputs"]["length"] == 81
    assert graph["24"]["inputs"]["images"] == ["23", 0] and graph["24"]["inputs"]["audio"] == ["7", 0]
    assert graph["24"]["inputs"]["fps"] == 16 and isinstance(graph["24"]["inputs"]["fps"], int)
    texts = {n["inputs"]["text"] for n in graph.values() if n["class_type"] == "CLIPTextEncode"}
    assert texts == {"a pear says hello", "blurry"}
    for missing in ("audio_path", "image_path"):
        with pytest.raises(ValueError) as refused:
            render_template(template, {k: v for k, v in VALUES.items() if k != missing})
        assert missing in str(refused.value)


def test_the_frame_rule_keeps_the_sampler_length_fixed_and_only_trims_the_decoded_batch():
    template = load_template("s2v_wan22")
    rule = template["frame_rule"]
    assert (rule["fps"], rule["frame_step"], rule["max_frames"], rule["width"], rule["height"]) == (16, 4, 81, 480, 832)
    assert rule["lengths"] == [2, 3, 4, 5]
    assert [frames_for(template, s) for s in rule["lengths"]] == [33, 49, 65, 81]
    for seconds in rule["lengths"]:
        graph = render_template(template, {**VALUES, "frames": frames_for(template, seconds)})
        assert graph["12"]["inputs"]["length"] == 77  # never the clip length
        assert graph["23"]["inputs"]["length"] == frames_for(template, seconds)


def test_validation_names_the_missing_audio_encoder_file_and_the_older_comfyui():
    template = load_template("s2v_wan22")
    assert validate_template(template, object_info_for(template)) == []
    problems = validate_template(template, object_info_for(template, drop_class="WanSoundImageToVideo"))
    assert len(problems) == 1 and "WanSoundImageToVideo" in problems[0] and "update ComfyUI" in problems[0]
    problems = validate_template(template, object_info_for(
        template, choices={"wav2vec2_large_english_fp16.safetensors": ["other.safetensors"]}))
    assert len(problems) == 1 and "wav2vec2_large_english_fp16.safetensors" in problems[0]
    assert "models/audio_encoders" in problems[0]
    assert {req["file"] for req in template["requires"]} == FILES
    sizes = {req["file"]: req.get("size") for req in template["requires"]}
    assert sizes["wan2.2_s2v_14B_fp8_scaled.safetensors"] == "16.4 GB"


def test_the_mcp_plans_an_s2v_job_with_the_keyframe_and_the_wav_both_uploaded(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "key.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    (outputs / "line.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 16)
    settings = Settings(api_key="rpa_fake", endpoints={"video": "vid1", "image": "img1"},
                        rates={"video": 3.49, "image": 1.58}, outputs_dir=str(outputs))
    client = JobClient(settings, transport=lambda *a, **k: pytest.fail("nothing is sent by plan()"))
    row = next(r for r in list_templates() if r["name"] == "s2v_wan22")
    assert row["task"] == "s2v" and row["kind"] == "video" and row["verified_live"] is False
    assert {"image_path", "audio_path"} <= set(row["placeholders"])
    plan = client.plan("s2v_wan22", prompt="a pear says hello", seed=3, image_path="key.png", audio_path="line.wav")
    # The clip is a video but runs where the voice lines run (the worker image with the audio_encoders mapping):
    # found on the first live job, which the video endpoint's base image could not load.
    assert (plan["kind"], plan["served_by"]) == ("video", "audio")
    assert plan["kind"] == "video" and plan["seconds"] == 5 and plan["frames"] == 81
    names = [item["name"] for item in plan["images"]]
    assert len(names) == 2 and names[0].endswith(".wav") and names[1].endswith(".png")
    assert plan["graph"]["7"]["inputs"]["audio"] == names[0] and plan["graph"]["9"]["inputs"]["image"] == names[1]
    assert plan["graph"]["12"]["inputs"]["length"] == 77 and plan["graph"]["23"]["inputs"]["length"] == 81
    with pytest.raises(JobError, match="audio_path"):
        client.plan("s2v_wan22", prompt="x", image_path="key.png")
    with pytest.raises(JobError, match="image_path"):
        client.plan("s2v_wan22", prompt="x", audio_path="line.wav")


def test_no_link_table_knows_s2v_yet():
    # stage 8 wires it into the clips pipeline, after the human's live test
    assert "s2v_wan22" not in local_comfyui.VIDEO_TEMPLATES and "s2v_wan22" not in local_comfyui.TEMPLATES


def test_the_worker_image_links_the_audio_encoders_folder_to_the_volume():
    docker = ROOT / "docker" / "worker-comfyui-tts"
    dockerfile = (docker / "Dockerfile").read_text(encoding="utf-8")
    assert "ln -s /runpod-volume/models/audio_encoders /comfyui/models/audio_encoders" in dockerfile
    assert "ln -s /runpod-volume/models/chatterbox /comfyui/models/chatterbox" in dockerfile  # still there
    fetch = (docker / "fetch_weights_s2v.sh").read_text(encoding="utf-8")
    for f in ("wan2.2_s2v_14B_fp8_scaled.safetensors", "wav2vec2_large_english_fp16.safetensors",
              "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors"):
        assert f in fetch
    assert "t3_mtl23ls_v2.safetensors" not in fetch  # the Chatterbox list stays in fetch_weights.sh
    assert "t3_mtl23ls_v2.safetensors" in (docker / "fetch_weights.sh").read_text(encoding="utf-8")
    readme = (docker / "README.md").read_text(encoding="utf-8")
    assert "audio_encoders" in readme and "Unverified on cartoon faces and on French" in readme
