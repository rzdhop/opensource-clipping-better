"""The runpod TTS link (plan 32 stage 6): ``runpod/tts_chatterbox`` clones a
character's frozen reference on the RunPod worker through the plan-31
``tts_chatterbox`` template -- the line, the reference uploaded inline under a
real .wav name, the character's fixed seed -- on the audio endpoint, else the
image one, else the video one (``mcp_server/config.py``'s order), and turns
the FLAC the worker answers into the mono 24 kHz WAV the voices module keeps,
with its ``line_timing_v1`` sidecar. Fake transports and a fake ffmpeg only:
nothing leaves the machine and nothing is spent.
"""

import base64
import json
import os
import wave

import pytest

from clipping.providers import generation as gen, pricing, tts
from clipping.providers.errors import ProviderError
from clipping.providers.generation import GenRequest, NoRunnableLink, parse_generation_chain, run_generation_chain
from clipping.providers.registry import Link, describe
from clipping.providers.transport import Response

LINK = Link("runpod", "tts_chatterbox")
FLAC = b"fLaC\x00\x00\x00\x22" + b"\x00" * 40
ENV = {"RUNPOD_API_KEY": "rpa_fake", "RUNPOD_COMFY_ENDPOINT_ID": "vid1", "RUNPOD_GPU_USD_PER_HOUR": "3.49",
       "RUNPOD_IMAGE_ENDPOINT_ID": "img1", "RUNPOD_IMAGE_GPU_USD_PER_HOUR": "1.58",
       "RUNPOD_AUDIO_ENDPOINT_ID": "aud1", "RUNPOD_AUDIO_GPU_USD_PER_HOUR": "0.69"}
TABLE = {("tts", "runpod"): tts.RUNPOD_TTS}
LINE = "Tu crois vraiment que je vais te laisser gagner ce vote ?"


class FakeTransport:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body})
        if not self.answers:
            raise AssertionError(f"unexpected call: {method} {url}")
        status, payload = self.answers.pop(0)
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode()
        return Response(status, {}, payload)

    def json(self, index):
        return json.loads(self.calls[index]["body"].decode())

    def urls(self):
        return [(c["method"], c["url"]) for c in self.calls]


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def time(self):
        return self.now


class FakeFfmpeg:
    """Stands in for ``subprocess.run``: writes a mono 24 kHz WAV of *seconds*
    where the command says, and records the command."""

    def __init__(self, seconds=2.0, returncode=0):
        self.seconds = seconds
        self.returncode = returncode
        self.argv = []

    def __call__(self, argv, **_kwargs):
        self.argv.append(list(argv))
        if self.returncode == 0:
            write_wav(argv[-1], self.seconds)

        class Done:
            returncode = self.returncode
            stdout = ""
            stderr = "" if self.returncode == 0 else "Invalid data found when processing input"

        return Done()


def write_wav(path, seconds, rate=24000):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(seconds * rate))
    return str(path)


def done(**fields):
    item = {"filename": "rzdhop_ai/tts_00001_.flac", "type": "base64", "data": base64.b64encode(FLAC).decode()}
    status = {"id": "job-1", "status": "COMPLETED", "executionTime": 3000, "delayTime": 1000, "workerId": "w1",
              "output": {"audio": [item]}}
    status.update(fields)
    return status


def request(tmp_path, *, seed=1137395016, reference=True, **extra):
    ref = write_wav(tmp_path / "voice_reference.wav", 12.0) if reference else None
    return GenRequest(kind="tts", text=LINE, voice="reference", seed=seed, references=(ref,) if ref else (),
                      out_dir=str(tmp_path / "out"), extra=dict({"name": "L001"}, **extra))


def generate(tmp_path, transport, *, env=ENV, run=None, req=None, log=None):
    clock = Clock()
    return tts.RUNPOD_TTS.generate(LINK, req or request(tmp_path), credentials=env,
                                   on_log=(log.append if log is not None else lambda line: None),
                                   transport=transport, sleep_fn=clock.sleep, time_fn=clock.time,
                                   run=run or FakeFfmpeg())


# ------------------------------------------------------------------ the line

def test_the_template_carries_the_line_the_seed_and_the_reference_under_a_wav_name(tmp_path):
    transport = FakeTransport([(200, {"id": "job-1", "status": "IN_QUEUE"}), (200, done())])
    ffmpeg = FakeFfmpeg(seconds=2.5)
    log = []
    result = generate(tmp_path, transport, run=ffmpeg, log=log)

    assert transport.urls() == [("POST", "https://api.runpod.ai/v2/aud1/run"),
                                ("GET", "https://api.runpod.ai/v2/aud1/status/job-1")]
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer rpa_fake"
    body = transport.json(0)["input"]
    graph, images = body["workflow"], body["images"]
    assert graph["2"]["class_type"] == "FL_ChatterboxMultilingualTTS"
    assert graph["2"]["inputs"]["text"] == LINE and graph["2"]["inputs"]["seed"] == 1137395016
    assert graph["2"]["inputs"]["exaggeration"] == 0.5 and graph["2"]["inputs"]["cfg_weight"] == 0.5
    assert len(images) == 1 and images[0]["name"].startswith("rzdhop_") and images[0]["name"].endswith(".wav")
    assert graph["1"] == {"class_type": "LoadAudio", "inputs": {"audio": images[0]["name"]}}
    assert images[0]["image"].startswith("data:audio/")
    assert graph["3"]["class_type"] == "SaveAudio"
    assert result.provider == "runpod" and result.model == "tts_chatterbox" and result.seed == 1137395016
    assert any("queued tts_chatterbox" in line and "seed 1137395016" in line for line in log)


def test_the_flac_is_converted_to_a_mono_24k_wav_and_the_sidecar_written(tmp_path):
    transport = FakeTransport([(200, {"id": "job-1"}), (200, done())])
    ffmpeg = FakeFfmpeg(seconds=2.5)
    result = generate(tmp_path, transport, run=ffmpeg)

    audio, sidecar = result.paths
    assert audio.endswith("L001.wav") and sidecar.endswith("L001.json")
    argv = ffmpeg.argv[0]
    assert argv[0] == "ffmpeg" and argv[-1] == audio and argv[argv.index("-i") + 1].endswith(".flac")
    assert argv[argv.index("-ac") + 1] == "1" and argv[argv.index("-ar") + 1] == "24000"
    assert argv[argv.index("-c:a") + 1] == "pcm_s16le"
    with wave.open(audio, "rb") as wav:
        assert (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) == (1, 24000, 2)
    # The FLAC the worker answered is not left beside the line.
    assert sorted(os.listdir(tmp_path / "out")) == ["L001.json", "L001.wav"]
    data = json.loads(open(sidecar, encoding="utf-8").read())
    assert data["$schema"] == "line_timing_v1" and data["provider"] == "runpod"
    assert data["duration_s"] == 2.5 and data["source"] == tts.SOURCE_DURATION and data["words"] == []
    assert result.meta["duration_s"] == 2.5 and result.meta["source"] == tts.SOURCE_DURATION
    # What RunPod billed: 4 GPU-s at the audio endpoint's own price.
    assert result.meta["gpu_seconds"] == 4.0 and result.meta["billed_usd"] == round(4 * 0.69 / 3600, 4)
    assert result.meta["endpoint"] == "aud1" and result.meta["job_id"] == "job-1"


def test_a_line_under_output_images_is_read_too_and_a_job_without_audio_fails(tmp_path):
    from clipping.providers.gencache import RequestFailed

    item = {"filename": "tts_00001_.flac", "type": "base64", "data": base64.b64encode(FLAC).decode()}
    transport = FakeTransport([(200, {"id": "job-1"}), (200, done(output={"images": [item]}))])
    assert generate(tmp_path, transport).paths[0].endswith("L001.wav")

    empty = FakeTransport([(200, {"id": "job-2"}), (200, done(id="job-2", output={"images": []}))])
    with pytest.raises(RequestFailed) as failed:
        generate(tmp_path, empty)
    assert "finished without a line" in str(failed.value) and "SaveAudio" in str(failed.value)


def test_ffmpeg_missing_or_failing_is_said_in_a_sentence(tmp_path):
    def missing(argv, **_):
        raise FileNotFoundError(argv[0])

    transport = FakeTransport([(200, {"id": "job-1"}), (200, done())])
    with pytest.raises(ProviderError) as refused:
        generate(tmp_path, transport, run=missing)
    assert "ffmpeg is not installed" in str(refused.value)

    transport = FakeTransport([(200, {"id": "job-1"}), (200, done())])
    with pytest.raises(ProviderError) as refused:
        generate(tmp_path, transport, run=FakeFfmpeg(returncode=1))
    assert "could not convert the line to WAV" in str(refused.value)


def test_a_missing_reference_or_text_is_refused_before_any_call(tmp_path):
    transport = FakeTransport([])
    with pytest.raises(ValueError) as refused:
        generate(tmp_path, transport, req=request(tmp_path, reference=False))
    assert "reference recording is missing" in str(refused.value)
    mp3 = tmp_path / "voice.mp3"
    mp3.write_bytes(b"ID3")
    req = request(tmp_path)
    req.references = (str(mp3),)
    with pytest.raises(ValueError) as refused:
        generate(tmp_path, transport, req=req)
    assert "must be a .wav file" in str(refused.value)
    assert transport.calls == []


def test_without_a_seed_the_reference_decides_it_and_the_character_seed_is_stable(tmp_path):
    assert tts.voice_seed("char_kiwilo") == tts.voice_seed("char_kiwilo") == 1137395016
    assert tts.voice_seed("char_mangella") != tts.voice_seed("char_kiwilo")
    assert 0 < tts.voice_seed("x") < 2**31
    name, _template, values, inline = tts.RUNPOD_TTS.plan(LINK, request(tmp_path, seed=None))
    assert name == "tts_chatterbox" and values["seed"] == tts.voice_seed(inline["name"])


# ------------------------------------------------------------- the endpoints

def test_the_endpoint_falls_back_audio_then_image_then_video_billed_at_the_serving_rate():
    assert (tts.audio_endpoint(ENV), tts.audio_rate(ENV)) == ("aud1", "0.69")
    no_audio = {k: v for k, v in ENV.items() if not k.startswith("RUNPOD_AUDIO")}
    assert (tts.audio_endpoint(no_audio), tts.audio_rate(no_audio)) == ("img1", "1.58")
    video_only = {k: v for k, v in no_audio.items() if not k.startswith("RUNPOD_IMAGE")}
    assert (tts.audio_endpoint(video_only), tts.audio_rate(video_only)) == ("vid1", "3.49")
    # A kind with an endpoint of its own but no price of its own is unpriced, never billed at another's rate.
    assert tts.audio_rate({**ENV, "RUNPOD_AUDIO_GPU_USD_PER_HOUR": ""}) is None
    # The key follows the serving endpoint: the audio endpoint's own (the image endpoint's key is not it),
    # the image endpoint's own when the lines fall back to it (plan 32's first live line: 403 otherwise),
    # the account key when neither has one.
    assert tts.audio_key({**ENV, "RUNPOD_IMAGE_API_KEY": "img-key"}) == "rpa_fake"
    assert tts.audio_key({**ENV, "RUNPOD_AUDIO_API_KEY": "aud-key"}) == "aud-key"
    assert tts.audio_key({**no_audio, "RUNPOD_IMAGE_API_KEY": "img-key"}) == "img-key"
    assert tts.audio_key(no_audio) == "rpa_fake"
    assert tts.audio_key({**video_only, "RUNPOD_IMAGE_API_KEY": "img-key"}) == "rpa_fake"
    assert tts.audio_endpoint({}) == ""


def test_a_line_on_the_video_endpoint_uses_its_url_and_price(tmp_path):
    env = {"RUNPOD_API_KEY": "rpa_fake", "RUNPOD_COMFY_ENDPOINT_ID": "vid1", "RUNPOD_GPU_USD_PER_HOUR": "3.49"}
    transport = FakeTransport([(200, {"id": "job-1"}), (200, done())])
    result = generate(tmp_path, transport, env=env)
    assert transport.urls()[0] == ("POST", "https://api.runpod.ai/v2/vid1/run")
    assert result.meta["billed_usd"] == round(4 * 3.49 / 3600, 4)


def test_a_missing_key_refuses_in_a_plain_sentence(tmp_path):
    transport = FakeTransport([])
    with pytest.raises(ProviderError) as refused:
        generate(tmp_path, transport, env={"RUNPOD_COMFY_ENDPOINT_ID": "vid1"})
    assert "no RunPod key is set; put RUNPOD_API_KEY" in str(refused.value) and transport.calls == []
    with pytest.raises(ProviderError) as refused:
        generate(tmp_path, transport, env={"RUNPOD_API_KEY": "rpa_fake"})
    assert "no RunPod endpoint is configured" in str(refused.value) and transport.calls == []
    # Through the runner: the link is skipped, named, nothing sent.
    lines = []
    with pytest.raises(NoRunnableLink) as failed:
        run_generation_chain("tts", [LINK], request(tmp_path), env={}, allow_paid=True, adapters=TABLE,
                             transport=transport, on_log=lines.append)
    assert "no API key (RUNPOD_API_KEY and RUNPOD_COMFY_ENDPOINT_ID are not set)" in str(failed.value)
    assert transport.calls == []


def test_the_runner_speaks_the_line_once_allow_paid_is_on(tmp_path, monkeypatch):
    monkeypatch.setattr(tts.subprocess, "run", FakeFfmpeg(seconds=1.0))
    transport = FakeTransport([(200, {"id": "job-1"}), (200, done())])
    lines = []
    with pytest.raises(NoRunnableLink):
        run_generation_chain("tts", [LINK], request(tmp_path), env=ENV, allow_paid=False, adapters=TABLE,
                             transport=transport, on_log=lines.append)
    assert transport.calls == [] and any("paid link; allow_paid is off" in line for line in lines)
    result, answered = run_generation_chain("tts", [LINK], request(tmp_path), env=ENV, allow_paid=True,
                                            adapters=TABLE, transport=transport, on_log=lines.append,
                                            sleep_fn=lambda s: None)
    assert describe(answered) == "runpod/tts_chatterbox" and result.paid
    assert result.est_cost == round(len(LINE) * 0.00005, 4)


# ------------------------------------------------------- chain, price, table

def test_runpod_is_a_tts_provider_and_the_link_parses_with_gemini_behind():
    links = parse_generation_chain("tts", "runpod/tts_chatterbox,gemini/flash-lite-tts")
    assert [describe(link) for link in links] == ["runpod/tts_chatterbox", "gemini/flash-lite-tts"]
    assert "runpod" in gen.KIND_PROVIDERS["tts"] and gen.is_paid(LINK)
    assert gen.adapter_for("tts", "runpod") is tts.RUNPOD_TTS
    assert {"RUNPOD_AUDIO_ENDPOINT_ID", "RUNPOD_AUDIO_API_KEY", "RUNPOD_AUDIO_GPU_USD_PER_HOUR"} <= set(
        gen.GEN_PROVIDERS["runpod"].optional_keys)
    creds = gen.credentials_for(LINK, ENV)
    assert creds["RUNPOD_AUDIO_ENDPOINT_ID"] == "aud1" and creds["RUNPOD_AUDIO_GPU_USD_PER_HOUR"] == "0.69"
    # Not in the shipped chain: nothing changes for a story off the own_gpu profile.
    assert "runpod" not in gen.DEFAULT_CHAINS["tts"]


def test_the_price_row_is_per_character_about_four_tenths_of_a_cent_a_line():
    price = pricing.price_for(LINK)
    assert price.unit == "char" and price.usd == 0.00005
    assert pricing.estimate(LINK, 80).est_usd == 0.004
    estimate = tts.RUNPOD_TTS.estimate(LINK, GenRequest(kind="tts", text="x" * 80))
    assert estimate.unit == "char" and estimate.qty == 80 and estimate.est_usd == 0.004 and estimate.paid


def test_the_probe_reads_the_audio_endpoints_health():
    transport = FakeTransport([(200, {"workers": {"idle": 1, "ready": 1, "running": 0, "throttled": 0}})])
    ok, text = tts.RUNPOD_TTS.probe(LINK, credentials=ENV, transport=transport)
    assert ok and "aud1" in text and transport.urls() == [("GET", "https://api.runpod.ai/v2/aud1/health")]


def test_a_journaled_job_is_followed_never_submitted_again(tmp_path):
    transport = FakeTransport([(200, done())])
    clock = Clock()
    entry = {"request": {"request_id": "job-1", "endpoint": "aud1"}}
    result = tts.RUNPOD_TTS.resume(LINK, request(tmp_path), entry, credentials=ENV, on_log=lambda line: None,
                                   transport=transport, sleep_fn=clock.sleep, time_fn=clock.time,
                                   run=FakeFfmpeg())
    assert transport.urls() == [("GET", "https://api.runpod.ai/v2/aud1/status/job-1")]
    assert result.paths[0].endswith("L001.wav")


def test_no_sdk_and_no_mcp_server_import_in_the_tts_module():
    import re

    source = open(tts.__file__, encoding="utf-8").read()
    assert not re.search(r"^\s*(from|import)\s+(mcp_server|runpod|requests)\b", source, re.MULTILINE)
