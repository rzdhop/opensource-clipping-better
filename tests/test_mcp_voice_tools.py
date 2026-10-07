"""Plan 33 stage 3: the MCP voice tools (``mcp_server/voice_tools.py``) with
every engine faked -- no network, no GPU, no ffmpeg: a Gemini adapter that
writes a tone, an edge synth that writes a file, a converter that copies,
a RunPod transport that answers a FLAC. What is pinned: the WAV lands at
outputs/<dest>/<name>.wav, the duration and cost come back and are booked,
a runaway and a silent line are refused, a batch skips what exists and
keeps going past one bad line, a reference is one take inside 6-30 s.
"""

import base64
import io
import json
import math
import os
import pathlib
import shutil
import wave

import pytest

from clipping.providers.generation import GenResult
from clipping.providers.transport import HttpStatusError, Response
from mcp_server.config import Settings
from mcp_server.runpod_jobs import JobClient
from mcp_server.voice_tools import VoiceError, VoiceTools, expected_seconds, runaway_limit


def wav_bytes(seconds, *, tone=True, rate=24000):
    buf = io.BytesIO()
    n = int(rate * seconds)
    if tone:
        frames = b"".join(int(12000 * math.sin(2 * math.pi * 220 * i / rate)).to_bytes(2, "little", signed=True)
                          for i in range(n))
    else:
        frames = b"\x00\x00" * n
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


class FakeGemini:
    """Writes a tone of *seconds* as the adapter would; fails *fail_first* times with HTTP 429."""

    def __init__(self, seconds=1.5, fail_first=0):
        self.seconds, self.fail_first, self.calls = seconds, fail_first, []

    def generate(self, link, request, *, credentials, on_log, **_):
        self.calls.append((request.text, request.voice, credentials["GOOGLE_API_KEY"]))
        if self.fail_first:
            self.fail_first -= 1
            raise HttpStatusError(429, "https://x", "quota")
        on_log("gemini/flash-lite-tts: direction recorded, not applied")
        wav = os.path.join(request.out_dir, f"{request.extra['name']}.wav")
        pathlib.Path(wav).write_bytes(wav_bytes(self.seconds))
        sidecar = os.path.join(request.out_dir, f"{request.extra['name']}.json")
        pathlib.Path(sidecar).write_text("{}")
        return GenResult(provider="gemini", model="flash-lite-tts", paths=(wav, sidecar),
                         meta={"duration_s": self.seconds, "tail_guard": {"reason": "none", "trimmed_s": 0.0}})


class FakeTransport:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append((method, url))
        status, payload = self.answers.pop(0)
        return Response(status, {}, json.dumps(payload).encode())


def copy_convert(seconds=None):
    """A converter that copies (or writes a tone of *seconds*, a runaway when long)."""
    def convert(src, dest):
        if seconds is None:
            shutil.copy(src, dest)
        else:
            pathlib.Path(dest).write_bytes(wav_bytes(seconds))
    return convert


@pytest.fixture
def tools(tmp_path):
    settings = Settings(api_key="rpa_fake", endpoints={"video": "vid1", "image": "img1"},
                        rates={"video": 3.49, "image": 1.58}, outputs_dir=str(tmp_path / "outputs"))
    transport = FakeTransport([])
    client = JobClient(settings, transport=transport)
    sleeps = []
    gemini = FakeGemini()
    edge_calls = []

    def edge(text, voice, rate, pitch, mp3_path):
        edge_calls.append((text, voice, rate, pitch))
        pathlib.Path(mp3_path).write_bytes(wav_bytes(1.2))

    vt = VoiceTools(client, outputs_dir=settings.outputs_dir, env={"GOOGLE_API_KEY": "k-test"}, gemini=gemini,
                    edge=edge, convert=copy_convert(), shape=copy_convert(), sleep=sleeps.append)
    vt.transport, vt.sleeps, vt.fake_gemini, vt.edge_calls = transport, sleeps, gemini, edge_calls
    return vt


def test_a_gemini_line_lands_as_a_wav_with_its_duration_and_is_booked(tools, tmp_path):
    got = tools.line("Vaulta… t'as laissé la porte grande ouverte.", "gemini", "Charon", "faille_damour/ep01/voices",
                     "l01_rida", style="coldly")
    path = tmp_path / "outputs" / "faille_damour" / "ep01" / "voices" / "l01_rida.wav"
    assert got["path"] == str(path) and path.exists()
    assert got["duration_s"] == 1.5 and got["provider"] == "gemini" and got["voice"] == "Charon"
    assert got["cost_usd"] == 0.0 and got["paid"] is False  # the free tier, per the price table
    assert got["style_applied"] is False and got["style"] == "coldly"  # recorded, never spoken
    assert tools.fake_gemini.calls == [("Vaulta… t'as laissé la porte grande ouverte.", "Charon", "k-test")]
    rows = tools.ledger.all()
    assert len(rows) == 1 and rows[0]["name"] == "l01_rida" and rows[0]["duration_s"] == 1.5
    summary = tools.ledger.summary()
    assert summary["lines"] == 1 and summary["by_provider"]["gemini"]["seconds"] == 1.5


def test_gemini_is_retried_on_a_quota_answer_and_the_key_is_required(tools):
    tools.fake_gemini.fail_first = 2
    got = tools.line("Pardon !", "gemini", "Kore", "v", "l02_marie_jeanne")
    assert got["attempts"] == 3 and tools.sleeps == [15.0, 30.0]
    tools.env = {}
    with pytest.raises(VoiceError, match="GOOGLE_API_KEY"):
        tools.line("Pardon !", "gemini", "Kore", "v", "l03")


def test_an_edge_line_goes_through_the_converter_and_checks_its_rate_and_pitch(tools, tmp_path):
    got = tools.line("Rida. Le mien aussi.", "edge", "fr-FR-HenriNeural", "v", "l05", rate="-5%", pitch="-10Hz")
    assert tools.edge_calls == [("Rida. Le mien aussi.", "fr-FR-HenriNeural", "-5%", "-10Hz")]
    assert got["duration_s"] == 1.2 and got["cost_usd"] == 0.0
    assert not list((tmp_path / "outputs" / "v").glob(".*"))  # the MP3 is gone
    with pytest.raises(VoiceError, match="rate/pitch"):
        tools.line("x", "edge", "fr-FR-HenriNeural", "v", "l06", rate="slow")


def test_a_chatterbox_line_is_a_gpu_job_whose_bill_is_the_cost(tools, tmp_path):
    ref = tmp_path / "outputs" / "refs" / "ref_rida.wav"
    ref.parent.mkdir(parents=True)
    ref.write_bytes(wav_bytes(8))
    tools.transport.answers = [
        (200, {"id": "job-1", "status": "IN_QUEUE"}),
        (200, {"id": "job-1", "status": "COMPLETED", "executionTime": 4400, "delayTime": 600, "workerId": "w-1",
               "output": {"audio": [{"filename": "rzdhop_ai/tts_00001_.flac", "type": "base64",
                                     "data": base64.b64encode(wav_bytes(2.0)).decode()}]}}),
    ]
    got = tools.line("Et vous êtes en retard même sur le café ?", "chatterbox", "refs/ref_rida.wav", "v", "l03_rida",
                     exaggeration=0.4)
    assert got["job_id"] == "job-1" and got["gpu_seconds"] == 4.4 and got["cost_usd"] == round(4.4 * 1.58 / 3600, 4)
    assert got["paid"] is True and got["reference"] == str(ref) and got["duration_s"] == 2.0
    assert sorted(p.name for p in (tmp_path / "outputs" / "v").iterdir()) == ["l03_rida.wav"]  # the FLAC is gone
    assert tools.ledger.summary()["chatterbox_usd_in_audio_jobs"] == got["cost_usd"]
    assert tools.ledger.summary()["cost_usd"] == 0.0  # counted in the jobs' audio bucket, not twice
    with pytest.raises(VoiceError, match="reference WAV"):
        tools.line("x", "chatterbox", "refs/missing.wav", "v", "l04")


def test_a_runaway_is_kept_aside_and_refused_a_silent_line_refused(tools, tmp_path):
    ref = tmp_path / "outputs" / "ref.wav"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(wav_bytes(8))
    text = "Non, non, non… pas maintenant !"
    assert expected_seconds(text) == round(len(text) * 0.070, 3) and runaway_limit(text) == round(3 * len(text) * 0.070 + 1, 3)
    tools.convert = copy_convert(seconds=40.0)
    tools.transport.answers = [
        (200, {"id": "job-9", "status": "IN_QUEUE"}),
        (200, {"id": "job-9", "status": "COMPLETED", "executionTime": 23300, "delayTime": 0,
               "output": {"audio": [{"filename": "t.flac", "type": "base64",
                                     "data": base64.b64encode(wav_bytes(0.1)).decode()}]}}),
    ]
    with pytest.raises(VoiceError, match="ran away: 40.0 s") as err:
        tools.line(text, "chatterbox", "ref.wav", "v", "l09")
    assert "ONE continuous take" in str(err.value) and "voice_ref_make" in str(err.value)
    assert (tmp_path / "outputs" / "v" / "l09.runaway.wav").exists()
    assert not (tmp_path / "outputs" / "v" / "l09.wav").exists()
    assert tools.ledger.all() == []  # nothing booked for a refused line
    tools.fake_gemini.seconds = 1.0
    tools.fake_gemini.generate = _silent(tools.fake_gemini)
    with pytest.raises(VoiceError, match="silent"):
        tools.line("Ok.", "gemini", "Kore", "v", "l10")
    assert not (tmp_path / "outputs" / "v" / "l10.wav").exists()


def _silent(fake):
    def generate(link, request, *, credentials, on_log, **_):
        wav = os.path.join(request.out_dir, f"{request.extra['name']}.wav")
        pathlib.Path(wav).write_bytes(wav_bytes(1.0, tone=False))
        return GenResult(provider="gemini", model="flash-lite-tts", paths=(wav, wav), meta={"duration_s": 1.0})
    return generate


def test_a_batch_skips_what_exists_redoes_what_is_asked_and_keeps_going(tools, tmp_path):
    voices = tmp_path / "outputs" / "ep01" / "voices"
    voices.mkdir(parents=True)
    (voices / "l01_rida.wav").write_bytes(wav_bytes(0.9))
    lines = [{"id": "l01", "who": "rida", "text": "Vaulta…"},
             {"id": "l02", "who": "marie_jeanne", "text": "Pardon !"},
             {"id": "l03", "who": "rida", "text": "Et vous ?"},
             {"id": "l04", "who": "paloma", "text": "C'est qui ?"},
             {"id": "l05", "who": "nobody", "text": "…"}]
    got = tools.batch(lines, "ep01/voices", provider="gemini",
                      voices={"rida": "Charon", "marie_jeanne": "Kore", "paloma": "Aoede"}, redo=["l03"])
    assert got["skipped"] == ["l01_rida"] and got["durations"]["l01_rida"] == 0.9
    assert [m["name"] for m in got["made"]] == ["l02_marie_jeanne", "l03_rida", "l04_paloma"]
    assert "l05_nobody" in got["errors"] and "no provider/voice" in got["errors"]["l05_nobody"]
    assert [c[1] for c in tools.fake_gemini.calls] == ["Kore", "Charon", "Aoede"]
    durations = json.loads((voices / "durations.json").read_text())
    assert durations == {"l01_rida": 0.9, "l02_marie_jeanne": 1.5, "l03_rida": 1.5, "l04_paloma": 1.5}
    assert got["cost_usd"] == 0.0


def test_a_reference_is_one_take_shaped_inside_six_to_thirty_seconds(tools, tmp_path):
    tools.shape = copy_convert(seconds=12.0)
    got = tools.reference("ref_rida", "edge", "fr-FR-HenriNeural", "refs", rate="-5%", pitch="-10Hz")
    assert got["path"] == str(tmp_path / "outputs" / "refs" / "ref_rida.wav") and got["duration_s"] == 12.0
    assert got["take"] == "one continuous take" and len(tools.edge_calls) == 1 and got["chars"] > 150
    assert sorted(p.name for p in (tmp_path / "outputs" / "refs").iterdir()) == ["ref_rida.wav"]
    tools.shape = copy_convert(seconds=3.0)
    with pytest.raises(VoiceError, match="outside 6-30 s"):
        tools.reference("ref_short", "edge", "fr-FR-HenriNeural", "refs")
    assert not (tmp_path / "outputs" / "refs" / "ref_short.wav").exists()
    with pytest.raises(VoiceError, match="outside the outputs dir"):
        tools.reference("ref_x", "edge", "fr-FR-HenriNeural", "../elsewhere")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_the_real_converter_and_shaper_make_the_wav_shape(tmp_path):
    from mcp_server.voice_tools import ffmpeg_reference, ffmpeg_to_wav, wav_stats

    src = tmp_path / "src.wav"
    src.write_bytes(wav_bytes(7.0, rate=44100))
    ffmpeg_to_wav(str(src), str(tmp_path / "out.wav"))
    stats = wav_stats(str(tmp_path / "out.wav"))
    assert stats["rate"] == 24000 and stats["channels"] == 1 and abs(stats["duration_s"] - 7.0) < 0.05
    ffmpeg_reference(str(src), str(tmp_path / "ref.wav"))
    ref = wav_stats(str(tmp_path / "ref.wav"))
    assert ref["rate"] == 24000 and 6.0 <= ref["duration_s"] <= 7.1 and ref["peak"] > 300
