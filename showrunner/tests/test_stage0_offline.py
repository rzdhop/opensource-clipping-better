"""Offline tests of the stage-0 pieces: template rendering, payload building, the fake endpoint
round trip, and the ffmpeg helpers on synthetic media. No network, no GPU."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from showrunner import comfy_templates, runpod_client as rp, verify  # noqa: E402
from showrunner.stage0 import matrix as M  # noqa: E402

FFMPEG = shutil.which("ffmpeg") is not None


@pytest.fixture
def png(tmp_path):
    path = tmp_path / "kf.png"
    # a 704x1216 png like the production keyframes
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=orange:s=704x1216", "-frames:v", "1",
                    str(path)], check=True, capture_output=True) if FFMPEG else path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    return str(path)


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "line.wav"
    if FFMPEG:
        subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=3.2",
                        "-ac", "1", "-ar", "24000", str(path)], check=True, capture_output=True)
    else:
        path.write_bytes(b"RIFF" + b"0" * 100)
    return str(path)


def test_frames_follow_the_8n_plus_1_rule():
    assert comfy_templates.frames_for(5, 24) == 121
    assert comfy_templates.frames_for(10, 24) == 241
    assert comfy_templates.frames_for(3.9, 24) == 89          # floor, never longer than the audio
    with pytest.raises(ValueError):
        comfy_templates.frames_for(11, 24)


def test_derived_values_need_a_64px_grid():
    d = comfy_templates.derived_values({"width": 704, "height": 1280, "seconds": 5, "fps": 24})
    assert d == {"frames": 121, "half_width": 352, "half_height": 640, "last_index": 120}
    with pytest.raises(ValueError):
        comfy_templates.derived_values({"width": 736, "height": 1280, "seconds": 5})


@pytest.mark.parametrize("name", ["ltx25_i2v_speech", "ltx25_a2v_speech", "ltx23_idlora_speech", "tts_chatterbox_line"])
def test_every_template_renders_without_leftover_placeholders(name):
    tpl = comfy_templates.load_template(name)
    values = {"image": "kf.png", "audio": "l.wav", "voice_ref": "v.wav", "prompt": "p", "negative": "n", "seed": 3,
              "width": 704, "height": 1280, "seconds": 5, "fps": 24, "name": "t", "sampler": "euler",
              "identity_guidance": 3.0, "text": "bonjour", "language": "French (fr)", "exaggeration": 0.5}
    graph = comfy_templates.render(tpl, values)
    dumped = json.dumps(graph)
    assert "{{" not in dumped
    expected = "SaveAudio" if name == "tts_chatterbox_line" else "SaveVideo"
    assert tpl["output_node"] in graph and graph[tpl["output_node"]]["class_type"] == expected
    # typed values land as numbers
    if name != "tts_chatterbox_line":
        assert graph["lat1"]["inputs"] == {"width": 352, "height": 640, "length": 121, "batch_size": 1}
        assert graph["noise1"]["inputs"]["noise_seed"] == 3
        assert graph["video"]["inputs"]["fps"] == 24.0
        assert graph["last"]["inputs"]["batch_index"] == 120


def test_i2v_prompt_lands_in_the_positive_encoder_and_the_negative_elsewhere():
    tpl = comfy_templates.load_template("ltx25_i2v_speech")
    graph = comfy_templates.render(tpl, {"image": "k.png", "prompt": "SAY HI", "negative": "NEG", "seed": 1, "width": 704,
                                         "height": 1280, "seconds": 5, "fps": 24, "name": "x"})
    assert graph["pos"]["inputs"]["text"] == "SAY HI"
    assert graph["neg"]["inputs"]["text"] == "NEG"
    assert graph["save"]["inputs"]["filename_prefix"] == "showrunner/x"
    assert graph["save"]["inputs"]["format"] == "mp4" and graph["save"]["inputs"]["format.codec"] == "h264"


def test_a2v_freezes_the_same_audio_latent_in_both_stages():
    g = comfy_templates.load_template("ltx25_a2v_speech")["graph"]
    assert g["cat1"]["inputs"]["audio_latent"] == ["frozen", 0]
    assert g["cat2"]["inputs"]["audio_latent"] == ["frozen", 0]
    assert g["frozen"]["class_type"] == "SetLatentNoiseMask" and g["mask"]["inputs"]["value"] == 0.0
    assert g["video"]["inputs"]["audio"] == ["trim", 0]            # the original waveform is muxed, not a decode


def test_idlora_wiring_matches_the_official_template():
    g = comfy_templates.load_template("ltx23_idlora_speech")["graph"]
    assert g["guider1"]["inputs"]["model"] == ["ref", 0]           # stage 1: ID-LoRA model + reference audio
    assert g["guider2"]["inputs"]["model"] == ["lora_d", 0]        # stage 2: distilled LoRA only
    assert g["cond"]["inputs"]["positive"] == ["ref", 1]
    assert g["guider2"]["inputs"]["positive"] == ["crop", 0]
    assert g["ref"]["inputs"]["reference_audio"] == ["refaudio", 0]


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_payload_carries_the_files_under_unique_names_and_stays_small(png, wav):
    payload = rp.build_payload("ltx25_a2v_speech", {"prompt": "p", "negative": "n", "seed": 1, "width": 704,
                                                    "height": 1280, "seconds": 4, "fps": 24, "name": "paloma_s1"},
                               {"image": png, "audio": wav})
    names = [e["name"] for e in payload["images"]]
    assert names == ["paloma_s1_image.png", "paloma_s1_audio.wav"]
    assert payload["workflow"]["image"]["inputs"]["image"] == "paloma_s1_image.png"
    assert payload["workflow"]["audio"]["inputs"]["audio"] == "paloma_s1_audio.wav"
    assert all(e["image"].startswith("data:") for e in payload["images"])
    assert len(json.dumps(payload)) < rp.RUN_LIMIT_BYTES


def test_missing_file_placeholder_is_refused_by_name(png):
    with pytest.raises(ValueError, match="voice_ref"):
        rp.build_payload("ltx23_idlora_speech", {"prompt": "p", "negative": "n", "seed": 1, "width": 704,
                                                 "height": 1280, "seconds": 5, "fps": 24, "name": "x"}, {"image": png})


class FakeEndpoint(rp.Endpoint):
    """Answers COMPLETED with one base64 mp4 and one png, like the stock handler."""

    def __init__(self):
        super().__init__("fake", key="k")
        self.calls = []

    def _call(self, method, path, body=None):
        self.calls.append((method, path))
        if path == "run":
            return {"id": "job1"}
        return {"id": "job1", "status": "COMPLETED", "executionTime": 42000, "delayTime": 3000,
                "output": {"images": [{"filename": "showrunner/x_00001_.mp4", "type": "base64",
                                       "data": base64.b64encode(b"MP4DATA").decode()},
                                      {"filename": "showrunner/x_last_00001_.png", "type": "base64",
                                       "data": base64.b64encode(b"PNGDATA").decode()}],
                           "audio": [{"filename": "showrunner/x_00001_.flac", "type": "base64",
                                      "data": base64.b64encode(b"FLACDATA").decode()}], "errors": []}}


def test_fake_round_trip_saves_media_first_and_bills_execution_only(tmp_path):
    ep = FakeEndpoint()
    job = ep.run({"workflow": {}, "images": []})
    status = ep.wait(job, poll_s=0, on_log=lambda s: None)
    paths = rp.save_outputs(status, str(tmp_path), stem="x")
    assert [os.path.basename(p) for p in paths] == ["x.flac", "x.mp4", "x_x_last_00001_.png"]
    assert open(paths[1], "rb").read() == b"MP4DATA" and open(paths[0], "rb").read() == b"FLACDATA"
    # DEC-316: the execution is billed; the queue wait is shown apart, never priced
    assert rp.billed_seconds(status) == 42.0 and rp.delay_seconds(status) == 3.0


def test_oversized_payload_is_refused():
    ep = FakeEndpoint()
    with pytest.raises(rp.RunPodError, match="10 MB"):
        ep.run({"workflow": {}, "images": [{"name": "big", "image": "x" * (11 * 1024 * 1024)}]})


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_verify_helpers_on_a_synthetic_clip(tmp_path, wav):
    clip = str(tmp_path / "clip.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "testsrc=size=352x640:rate=24:duration=3",
                    "-i", wav, "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", clip],
                   check=True, capture_output=True)
    info = verify.report(clip)
    assert info["has_audio"] and not info["silent"] and info["width"] == 352 and 2.5 < info["duration_s"] < 3.5
    sheet = verify.contact_sheet(clip, str(tmp_path / "sheet.jpg"), frames=4)
    assert os.path.getsize(sheet) > 1000
    ref = verify.extract_audio(clip, str(tmp_path / "ref.wav"), max_s=2)
    assert 1.9 < verify.probe(ref)["duration_s"] <= 2.1 and verify.probe(ref)["sample_rate"] == 24000
    joined = verify.concat_audio([wav, ref], str(tmp_path / "joined.wav"), gap_s=0.5)
    assert verify.probe(joined)["duration_s"] > 3.2 + 2 + 0.5 - 0.2


def test_matrix_prompts_quote_the_line_and_name_the_language():
    p = M.prompt_path_a("paloma", "fr")
    assert "\"Tu souris à ton téléphone. C'est qui, le kiwi ?\"" in p and "in French" in p and "mango" in p
    c = M.prompt_path_c("rida", "en")
    assert c.startswith("[VISUAL]:") and "[SPEECH]: Vaulta" in c and "[SOUNDS]:" in c
    ex = M.prompt_exchange_a("two", "fr")
    assert "Marie-Jeanne says in French" in ex and "Rida says in French" in ex
    for char in M.CHARACTERS.values():  # the head rule binds the fruit universe (D8: not every character)
        if char["universe"] == "fruit":
            assert "whole head is a single" in char["head"]


def test_api_key_reads_the_named_key_and_names_it_when_missing():
    assert rp.api_key({"RUNPOD_IMAGE_API_KEY": " img-key "}, name="RUNPOD_IMAGE_API_KEY") == "img-key"
    assert rp.api_key({"RUNPOD_API_KEY": "main", "RUNPOD_IMAGE_API_KEY": "img"}) == "main"
    with pytest.raises(rp.RunPodError, match="SHOWRUNNER_TEST_ABSENT_KEY"):
        rp.api_key({}, name="SHOWRUNNER_TEST_ABSENT_KEY")


def test_each_character_speaks_in_its_own_universe():
    human = M.prompt_path_a("camille", "fr") + M.prompt_path_b("camille", "fr") + M.prompt_path_c("camille", "fr")
    assert "fruit" not in human.lower()
    assert human.count(M.UNIVERSES["cartoon_human"]["medium"]) == 3
    assert M.UNIVERSES["fruit"]["medium"] in M.prompt_path_a("paloma", "fr")
    # the fruit negative bans human faces; the human character must not inherit it
    assert "human face" in M.negative("rida") and "human face" not in M.negative("camille")
    assert "Tu as signé sans moi" in M.prompt_path_a("camille", "fr")


def test_keyframe_prompt_and_exchanges_keep_one_universe():
    kf = M.prompt_keyframe("camille")
    assert kf.startswith(M.UNIVERSES["cartoon_human"]["medium"]) and "Camille" in kf and "fruit" not in kf.lower()
    assert M.exchange_medium("three") == M.UNIVERSES["fruit"]["medium"]
    mixed = dict(M.EXCHANGES["two"], speakers=["rida", "camille"])
    original = M.EXCHANGES["two"]
    M.EXCHANGES["two"] = mixed
    try:
        with pytest.raises(ValueError, match="mixes universes"):
            M.exchange_medium("two")
    finally:
        M.EXCHANGES["two"] = original


def test_a_character_without_keyframe_is_skipped_not_sent(monkeypatch, capsys):
    from showrunner.stage0 import run_stage0 as R
    monkeypatch.setitem(M.CHARACTERS, "camille", dict(M.CHARACTERS["camille"], keyframe="/nonexistent/kf.png"))
    ready = R._ready_characters()
    assert "camille" not in ready and {"paloma", "marie_jeanne", "rida"} <= set(ready)
    assert "keyframe --character camille" in capsys.readouterr().out


def test_preflight_counts_batches_from_the_files_present(monkeypatch, tmp_path, png):
    from showrunner.stage0 import preflight
    monkeypatch.setattr(M, "STAGE0_DIR", str(tmp_path))  # no kf_three.png there
    for cid in M.CHARACTERS:
        monkeypatch.setitem(M.CHARACTERS, cid, dict(M.CHARACTERS[cid], keyframe=png if cid != "camille" else "/no.png"))
    monkeypatch.setitem(M.EXCHANGES, "two", dict(M.EXCHANGES["two"], keyframe=png))
    sizes = preflight.batch_sizes([11, 22, 33])
    # 3 ready characters x 3 seeds + the two-speaker exchange x 2 seeds (no three-speaker keyframe yet)
    assert sizes == {"smoke": 1, "a": 11, "b": 11, "c": 10}


def test_preflight_names_the_key_scope_when_an_endpoint_refuses(monkeypatch):
    from showrunner.stage0 import preflight

    class Refused:
        def __init__(self, *a, **k):
            pass

        def health(self):
            raise rp.RunPodError("HTTP 403 on GET health: forbidden")

    monkeypatch.setattr(rp, "Endpoint", Refused)
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    [(name, ok, detail)] = preflight.check_endpoint("video", "abc", "RUNPOD_API_KEY")
    assert not ok and "key's scope" in detail
    report = preflight.format_report([(name, ok, detail), ("ffmpeg", True, "x")], {"smoke": 1})
    assert "MISSING  video endpoint abc" in report and "1 item(s) missing" in report


def test_every_character_description_is_about_seventy_words():
    # Rida's rule (2026-10-08): about 70 words per character description.
    for cid, char in M.CHARACTERS.items():
        assert 65 <= len(char["head"].split()) <= 80, cid


def test_video_jobs_use_the_showrunner_video_key(monkeypatch):
    from showrunner.stage0 import run_stage0 as R
    seen = {}

    class Fake:
        def __init__(self, endpoint_id, key=None, **kw):
            seen.update(id=endpoint_id, key=key)

    monkeypatch.setattr(rp, "Endpoint", Fake)
    monkeypatch.setenv("RUNPOD_SHOWRUNNER_VIDEO_KEY", "video-key")
    R._video_endpoint(type("A", (), {"video_endpoint": "8o50"})())
    assert seen == {"id": "8o50", "key": "video-key"}


def test_single_speaker_prompts_use_dialogue_framing_not_the_lens():
    # Rida, 2026-10-08: a character rarely speaks to camera in an episode.
    for cid in M.CHARACTERS:
        for build in (M.prompt_path_a, M.prompt_path_b, M.prompt_path_c):
            p = build(cid, "fr")
            assert "talks to someone just off-screen" in p and "never looking into it" in p
            assert "looks toward the camera" not in p
