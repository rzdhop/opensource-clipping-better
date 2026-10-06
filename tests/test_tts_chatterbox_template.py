"""The text-to-speech workflow template (plan 31): Chatterbox Multilingual in
French on the RunPod ComfyUI worker, the reference voice through LoadAudio, the
line out of core SaveAudio. Rendering and validation only -- there is no GPU here."""

import pytest

from clipping.providers import local_comfyui
from clipping.providers.local_comfyui import load_template, render_template, validate_template

VALUES = {"prompt": "Par où ?", "seed": 7, "audio_path": "rzdhop_abc.wav", "exaggeration": "0.4", "cfg_weight": 0.5,
          # what the MCP's planner always adds, declared or not
          "negative": "", "width": 832, "height": 1216}
WEIGHTS = {"ve.pt", "t3_mtl23ls_v2.safetensors", "s3gen.pt", "grapheme_mtl_merged_expanded_v1.json", "conds.pt",
           "Cangjie5_TC.json"}


def object_info_for(template, *, drop_class=None):
    info = {node["class_type"]: {"input": {"required": {}}} for node in template["graph"].values()}
    info.pop(drop_class, None)
    return info


def test_the_tts_template_renders_a_french_line_with_the_reference_voice():
    template = load_template("tts_chatterbox")
    assert template["task"] == "tts" and template["kind"] == "audio"
    graph = render_template(template, VALUES)
    tts = graph["2"]["inputs"]
    assert graph["2"]["class_type"] == "FL_ChatterboxMultilingualTTS"
    assert tts["text"] == "Par où ?" and tts["language"] == "French (fr)"
    assert tts["seed"] == 7 and tts["exaggeration"] == 0.4 and tts["cfg_weight"] == 0.5
    assert isinstance(tts["exaggeration"], float) and tts["audio_prompt"] == ["1", 0]
    assert graph[template["audio_node"]] == {"class_type": "LoadAudio", "inputs": {"audio": "rzdhop_abc.wav"}}
    assert graph[template["output_node"]]["class_type"] == "SaveAudio"
    assert graph["3"]["inputs"]["audio"] == ["2", 0]
    with pytest.raises(ValueError) as refused:
        render_template(template, {k: v for k, v in VALUES.items() if k != "audio_path"})
    assert "audio_path" in str(refused.value)


def test_validation_names_the_missing_chatterbox_node_and_lists_the_weights_it_loads_itself():
    template = load_template("tts_chatterbox")
    assert validate_template(template, object_info_for(template)) == []
    problems = validate_template(template, object_info_for(template, drop_class="FL_ChatterboxMultilingualTTS"))
    assert len(problems) == 1 and "FL_ChatterboxMultilingualTTS" in problems[0] and "not installed" in problems[0]
    # the weights are declared for the install message, with the folder the node reads
    assert {req["file"] for req in template["requires"]} == WEIGHTS
    assert all(req["field"] == "" and req["dir"] == "models/chatterbox/chatterbox_multilingual"
               for req in template["requires"])
    assert {"audio_path", "exaggeration", "cfg_weight"} <= local_comfyui.KNOWN_PLACEHOLDERS
