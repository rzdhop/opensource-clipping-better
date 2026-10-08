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


@pytest.mark.parametrize("name", ["ltx25_i2v_speech", "ltx25_a2v_speech", "ltx23_idlora_speech", "tts_chatterbox_line",
                                  "vc_chatterbox"])
def test_every_template_renders_without_leftover_placeholders(name):
    tpl = comfy_templates.load_template(name)
    values = {"image": "kf.png", "audio": "l.wav", "voice_ref": "v.wav", "prompt": "p", "negative": "n", "seed": 3,
              "width": 704, "height": 1280, "seconds": 5, "fps": 24, "name": "t", "sampler": "euler",
              "identity_guidance": 3.0, "text": "bonjour", "language": "French (fr)", "exaggeration": 0.5,
              "input": "clip.wav", "target_voice": "v.wav"}
    graph = comfy_templates.render(tpl, values)
    dumped = json.dumps(graph)
    assert "{{" not in dumped
    audio_only = name in ("tts_chatterbox_line", "vc_chatterbox")
    expected = "SaveAudio" if audio_only else "SaveVideo"
    assert tpl["output_node"] in graph and graph[tpl["output_node"]]["class_type"] == expected
    # typed values land as numbers
    if not audio_only:
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


def test_positive_prompts_never_name_text_or_subtitles():
    # cfg 1.0 ignores the negative; naming captions in the positive primed burned-in subtitles (batch a)
    prompts = [build(c, "fr") for c in M.CHARACTERS for build in (M.prompt_path_a, M.prompt_path_b, M.prompt_path_c)]
    prompts += [build(k, "fr") for k in M.EXCHANGES for build in (M.prompt_exchange_a, M.prompt_exchange_b)]
    for p in prompts:
        low = p.lower()
        assert "subtitle" not in low and "caption" not in low and "on-screen" not in low, p[-200:]
    assert all(M.CLEAN_FRAME in p for p in prompts if not p.startswith("[VISUAL]"))


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _Scripted(rp.Endpoint):
    """Answers the given states in order, the last one forever."""

    def __init__(self, states):
        super().__init__("fake", key="k")
        self.states = list(states)

    def status(self, job_id):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"id": job_id, "status": state}


def test_wait_is_patient_in_the_queue_and_strict_once_running():
    clock = _Clock()
    # 40 min queued (a throttled datacenter, 2026-10-08), then it runs and completes: no error
    ep = _Scripted(["IN_QUEUE"] * 480 + ["IN_PROGRESS", "COMPLETED"])
    st = ep.wait("j", poll_s=5, on_log=lambda s: None, clock=clock, sleep=clock.sleep)
    assert st["status"] == "COMPLETED" and clock.t >= 2400
    clock = _Clock()
    with pytest.raises(rp.RunPodError, match="IN_PROGRESS 1800 s after it started"):
        _Scripted(["IN_QUEUE"] * 10 + ["IN_PROGRESS"]).wait("j", poll_s=60, on_log=lambda s: None,
                                                             clock=clock, sleep=clock.sleep)
    clock = _Clock()
    with pytest.raises(rp.RunPodError, match="no GPU free"):
        _Scripted(["IN_QUEUE"]).wait("j", poll_s=600, queue_timeout_s=3600, on_log=lambda s: None,
                                     clock=clock, sleep=clock.sleep)


def test_a_stuck_job_does_not_sink_the_batch_and_ids_are_kept(tmp_path, monkeypatch, capsys):
    from showrunner.stage0 import run_stage0 as R

    class Batch(rp.Endpoint):
        def __init__(self):
            super().__init__("fake", key="k")
            self.n = 0

        def run(self, payload, **kw):
            self.n += 1
            return f"job{self.n}"

        def wait(self, job_id, **kw):
            if job_id == "job1":
                raise rp.RunPodError("job1: still IN_QUEUE after 10800 s (no GPU free)")
            return {"id": job_id, "status": "COMPLETED", "executionTime": 40000, "delayTime": 1000, "output": {}}

    monkeypatch.setattr(rp, "submit_template", lambda ep, *a, **k: ep.run({}))
    monkeypatch.setattr(rp, "save_outputs", lambda status, out_dir, stem: [f"{stem}.mp4"])
    results = R.run_batch(Batch(), [("a1", "t", {}, {}), ("a2", "t", {}, {})], str(tmp_path), poll_s=0)
    assert [(stem, st) for stem, _, _, st in results] == [("a1", "LOST"), ("a2", "COMPLETED")]
    kept = [json.loads(l)["job"] for l in open(tmp_path / "submitted.jsonl")]
    assert kept == ["job1", "job2"] and "FAILED a1" in capsys.readouterr().out


def test_line_guard_cuts_at_the_first_pause_after_most_of_the_line():
    # the real numbers of 2026-10-08: Paloma's 9-word line came back 40 s long, the line in 0-2.9 s
    paloma = [(2.877, 4.343), (8.637, 9.245), (13.43, 14.436)]
    assert verify.line_cut_s("Tu souris à ton téléphone. C'est qui, le kiwi ?", paloma, 40.0) == 3.027
    # a pause inside the line (after "R…") is too early to be its end
    mj = [(0.582, 0.988), (3.008, 4.901)]
    assert verify.line_cut_s("R… comme Rida ? Non. Non, non, non.", mj, 26.2) == 3.158
    # no pause late enough: capped at 1.6 x the expected length
    assert verify.line_cut_s("un deux trois quatre cinq six", [(0.5, 0.9)], 30.0) == round(1.6 * 6 / 2.4 + 0.15, 3)
    # a short clean line is never lengthened
    assert verify.line_cut_s("Personne ? Sympa.", [], 1.4) == 1.4


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg")
def test_silences_finds_a_pause_and_one_running_to_the_end(tmp_path):
    path = str(tmp_path / "t.wav")
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=220:duration=1,apad=pad_dur=1,volume=1", "-f", "lavfi", "-i",
                    "sine=frequency=330:duration=1", "-filter_complex", "[0][1]concat=n=2:v=0:a=1,apad=pad_dur=0.6",
                    "-ar", "24000", "-ac", "1", path], check=True, capture_output=True)
    pauses = verify.silences(path)
    assert len(pauses) == 2 and abs(pauses[0][0] - 1.0) < 0.05 and abs(pauses[1][1] - verify.probe(path)["duration_s"]) < 0.05


def test_vc_converts_the_clip_audio_to_the_locked_voice_and_keeps_the_seed_typed(tmp_path, wav):
    target = tmp_path / "voice.wav"
    shutil.copyfile(wav, target)  # two distinct files (one file given twice is sent once, see build_payload)
    payload = rp.build_payload("vc_chatterbox", {"seed": 0, "name": "paloma_fr_s33_vc"},
                               {"input": wav, "target_voice": str(target)})
    g = payload["workflow"]
    assert [e["name"] for e in payload["images"]] == ["paloma_fr_s33_vc_input.wav", "paloma_fr_s33_vc_target_voice.wav"]
    assert g["vc"]["class_type"] == "FL_ChatterboxVC"
    assert g["vc"]["inputs"]["input_audio"] == ["source", 0] and g["vc"]["inputs"]["target_voice"] == ["target", 0]
    assert g["source"]["inputs"]["audio"] == "paloma_fr_s33_vc_input.wav"
    assert g["vc"]["inputs"]["seed"] == 0 and g["save"]["class_type"] == "SaveAudio"


def test_vc_jobs_never_convert_a_take_to_the_voice_cut_from_it(tmp_path, monkeypatch):
    from showrunner.stage0 import run_stage0 as R
    out = tmp_path / "_stage0"
    (out / "a").mkdir(parents=True)
    (out / "voices").mkdir()
    for c in M.CHARACTERS:
        (out / "voices" / f"{c}.wav").write_bytes(b"RIFF")
        for seed in (22, 33):
            (out / "a" / f"{c}_fr_s{seed}.mp4").write_bytes(b"mp4")
    monkeypatch.setattr(R, "OUT", str(out))
    monkeypatch.setattr(R, "VOICES", str(out / "voices"))
    monkeypatch.setattr(R, "VC_DIR", str(out / "vc"))
    jobs = {j[0]: j for j in R._vc_jobs(33, "fr")}
    assert set(jobs) == {"paloma_fr_s33_vc", "marie_jeanne_fr_s33_vc", "rida_fr_s33_vc", "camille_fr_s22_vc"}
    stem, template, values, files, clip = jobs["camille_fr_s22_vc"]
    assert template == "vc_chatterbox" and clip.endswith("a/camille_fr_s22.mp4")
    assert files["target_voice"].endswith("voices/camille.wav") and files["input"].endswith("vc/camille_fr_s22_in.wav")


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg")
def test_remux_keeps_the_picture_and_swaps_the_sound(tmp_path, wav):
    from showrunner.stage0 import run_stage0 as R
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "testsrc=s=704x1280:r=24:d=3",
                    "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "3", "-c:v", "libx264", "-c:a", "aac",
                    "-shortest", str(clip)], check=True, capture_output=True)
    dest = R.remux(str(clip), wav, str(tmp_path / "out.mp4"))
    info, src = verify.probe(dest), verify.probe(str(clip))
    assert info["has_audio"] and info["width"] == 704 and info["frames"] == src["frames"]
    assert not verify.loudness(dest)["silent"]


def test_image_templates_render_and_the_multiref_slots_repeat_the_last_reference(tmp_path, png):
    t2i = comfy_templates.render(comfy_templates.load_template("t2i_flux2_klein"),
                                 {"prompt": "p", "seed": 4, "name": "kf_x_s4"})
    assert t2i["5"]["inputs"] == {"width": 832, "height": 1216, "batch_size": 1}   # the cast image default
    assert t2i["3"]["inputs"]["seed"] == 4 and t2i["3"]["inputs"]["cfg"] == 1.0
    assert t2i["9"]["inputs"]["filename_prefix"] == "showrunner/kf_x_s4" and "{{" not in json.dumps(t2i)
    assert comfy_templates.multiref_files(["a.png", "b.png"]) == {"ref1": "a.png", "ref2": "b.png", "ref3": "b.png",
                                                                  "ref4": "b.png"}
    for bad in ([], ["a"] * 5):
        with pytest.raises(ValueError):
            comfy_templates.multiref_files(bad)
    other = tmp_path / "other.png"
    shutil.copyfile(png, other)
    payload = rp.build_payload("edit_flux2_klein_multiref", {"prompt": "p", "seed": 1, "width": 704, "height": 1280,
                                                             "name": "kf_three"},
                               comfy_templates.multiref_files([png, str(other)]))
    g = payload["workflow"]
    assert [e["name"] for e in payload["images"]] == ["kf_three_ref1.png", "kf_three_ref2.png"]   # sent once each
    assert [g[n]["inputs"]["image"] for n in ("20", "21", "22", "23")] == \
        ["kf_three_ref1.png", "kf_three_ref2.png", "kf_three_ref2.png", "kf_three_ref2.png"]
    assert g["3"]["inputs"]["positive"] == ["43", 0] and g["43"]["inputs"]["conditioning"] == ["42", 0]


def test_keyframe3_job_uses_showrunners_own_template():
    from showrunner.stage0 import run_stage0 as R
    stem, template, values, files = R._keyframe3_job()
    assert (stem, template) == ("kf_three", "edit_flux2_klein_multiref")
    assert files["ref1"] == M.CHARACTERS["paloma"]["ref"] and files["ref4"] == M.CHARACTERS["rida"]["ref"]
    assert (values["width"], values["height"]) == (M.WIDTH, M.HEIGHT)


def test_nothing_in_showrunner_imports_clipping_or_the_live_mcp_server():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if f.endswith(".py"):
                path = os.path.join(dirpath, f)
                for k, line in enumerate(open(path, encoding="utf-8"), 1):
                    if line.lstrip().startswith(("import clipping", "from clipping", "import mcp_server",
                                                 "from mcp_server")):
                        offenders.append(f"{os.path.relpath(path, root)}:{k}")
    assert offenders == []


def test_vc_exchanges_convert_each_line_to_its_own_speaker(tmp_path, monkeypatch):
    from showrunner.stage0 import run_stage0 as R
    out = tmp_path / "_stage0"
    (out / "a").mkdir(parents=True)
    (out / "voices").mkdir()
    for c in M.CHARACTERS:
        (out / "voices" / f"{c}.wav").write_bytes(b"RIFF")
    for key in ("two", "three"):
        (out / "a" / f"ex_{key}_fr_s22.mp4").write_bytes(b"mp4")
    monkeypatch.setattr(R, "OUT", str(out))
    monkeypatch.setattr(R, "VOICES", str(out / "voices"))
    monkeypatch.setattr(R, "VC_DIR", str(out / "vc"))
    verdicts = {
        "ex_two_fr_s22": {"duration_s": 10.042, "lines": [{"speaker": "marie_jeanne", "start_s": 0.0, "end_s": 4.52},
                                                          {"speaker": "rida", "start_s": 5.34, "end_s": 7.46}]},
        "ex_three_fr_s22": {"duration_s": 10.042, "lines": [{"speaker": "paloma", "start_s": 0.0, "end_s": 2.3},
                                                            {"speaker": "marie_jeanne", "start_s": None, "end_s": None},
                                                            {"speaker": "rida", "start_s": 6.88, "end_s": 8.46}]},
    }
    plans = R._vc_exchange_jobs(22, "fr", verdicts=verdicts)
    assert [stem for _, stem, _ in plans] == ["ex_two_fr_s22"]          # a line not heard: that take is skipped
    parts = plans[0][2]
    assert [(p[0], p[4]) for p in parts] == [("ex_two_fr_s22_l0_marie_jeanne_vc", (0.0, 4.93)),
                                             ("ex_two_fr_s22_l1_rida_vc", (4.93, 10.042))]
    assert parts[0][3]["target_voice"].endswith("voices/marie_jeanne.wav")
    assert parts[1][3]["target_voice"].endswith("voices/rida.wav") and parts[1][1] == "vc_chatterbox"
