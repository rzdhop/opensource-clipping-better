"""The voice tools of plan 31, offline: the four frozen reference voices are
made once (tools/make_voice_refs.py), the 18 lines of episode 1 become one
tts_chatterbox job each with the speaker's reference and seed
(tools/voice_ep01_comfy.py), and render_ep01.py mixes existing voice files
without a Gemini call (--use-existing-voices). Fakes only; nothing is spent."""

import base64
import importlib.util
import json
import pathlib
import sys

import pytest

from clipping.providers.transport import Response
from mcp_server.config import Settings
from mcp_server.runpod_jobs import JobClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
FLAC = b"fLaC" + b"\x00" * 16


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


make_voice_refs = load_tool("make_voice_refs")
voice_ep01 = load_tool("voice_ep01_comfy")
render_ep01 = sys.modules["render_ep01"]


class FakeTransport:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "body": body})
        assert self.answers, f"unexpected call {method} {url}"
        status, payload = self.answers.pop(0)
        return Response(status, {}, json.dumps(payload).encode())


@pytest.fixture
def refs(tmp_path):
    refs_dir = tmp_path / "refs"
    refs_dir.mkdir()
    for character in make_voice_refs.CHARACTERS:
        (refs_dir / f"ref_{character['slug']}.wav").write_bytes(b"RIFF" + b"\x00" * 40)
    return refs_dir


def test_the_reference_voices_are_made_once_and_frozen(tmp_path):
    made = []

    def synth(character, raw):
        made.append(character["slug"])
        raw.write_bytes(b"raw")
        return "fake"

    def finish(raw, out):
        out.write_bytes(raw.read_bytes())

    manifest = make_voice_refs.make_refs(make_voice_refs.CHARACTERS, tmp_path, synth=synth, finish=finish)
    assert made == ["rida", "marie_jeanne", "ananas", "ines"]
    assert sorted(p.name for p in tmp_path.glob("ref_*.wav")) == ["ref_ananas.wav", "ref_ines.wav",
                                                                   "ref_marie_jeanne.wav", "ref_rida.wav"]
    assert manifest["ANANAS"]["engine"] == "fake" and not list(tmp_path.glob("raw_*"))
    make_voice_refs.make_refs(make_voice_refs.CHARACTERS, tmp_path, synth=synth, finish=finish)
    assert len(made) == 4  # frozen: nothing remade
    make_voice_refs.make_refs(make_voice_refs.CHARACTERS[:1], tmp_path, synth=synth, finish=finish, force=True)
    assert made == ["rida", "marie_jeanne", "ananas", "ines", "rida"]
    for character in make_voice_refs.CHARACTERS:  # 10-15 s of French, in character
        assert 30 <= len(character["text"].split()) <= 60, character["name"]
    assert {c["name"] for c in make_voice_refs.CHARACTERS} == set(render_ep01.VOICES)
    assert make_voice_refs.pick_engine("auto", None) == "edge" and make_voice_refs.pick_engine("auto", "k") == "gemini"


def test_each_line_is_one_tts_job_with_the_frozen_reference_and_the_speakers_seed(tmp_path, refs):
    voices_dir = tmp_path / "outputs" / "acces_refuse" / "ep01" / "voices"
    plan = voice_ep01.plan_lines(refs, voices_dir)
    assert len(plan) == 18 and [line["id"] for line in plan][:3] == ["l01", "l02", "l03"]
    assert {line["speaker"]: line["seed"] for line in plan} == voice_ep01.SEEDS
    assert plan[0]["ref"].endswith("ref_ananas.wav") and plan[2]["ref"].endswith("ref_rida.wav")
    assert not any(line["done"] for line in plan)
    assert voice_ep01.estimate_usd(18, 1.58) == (90 + 18 * 8, round((90 + 18 * 8) * 1.58 / 3600, 3))

    settings = Settings(api_key="rpa_fake", endpoints={"video": "vid1", "image": "img1"},
                        rates={"video": 3.49, "image": 1.58}, outputs_dir=str(tmp_path / "outputs"))
    answers = []
    for n in (1, 2):
        answers += [(200, {"id": f"job-{n}", "status": "IN_QUEUE"}),
                    (200, {"id": f"job-{n}", "status": "COMPLETED", "executionTime": 8000, "delayTime": 1000,
                           "output": {"images": [], "audio": [{"filename": f"tts_0000{n}_.flac", "type": "base64",
                                                               "data": base64.b64encode(FLAC).decode()}]}})]
    transport = FakeTransport(answers)
    client = JobClient(settings, transport=transport)
    converted = []

    def convert(src, dst):
        converted.append((pathlib.Path(src).name, pathlib.Path(dst).name))
        pathlib.Path(dst).write_bytes(b"wav")

    records = voice_ep01.voice_lines(client, plan[:2], convert=convert, log=lambda *_: None)
    assert [r["state"] for r in records] == ["COMPLETED", "COMPLETED"]
    assert converted == [("l01.flac", "l01.wav"), ("l02.flac", "l02.wav")]
    assert transport.calls[0]["url"].endswith("/img1/run")  # audio: the image endpoint by default
    body = json.loads(transport.calls[0]["body"].decode())
    graph = body["input"]["workflow"]
    assert graph["2"]["inputs"]["text"] == plan[0]["text"] and graph["2"]["inputs"]["seed"] == voice_ep01.SEEDS["ANANAS"]
    assert graph["2"]["inputs"]["exaggeration"] == 0.3 and graph["2"]["inputs"]["cfg_weight"] == 0.3
    assert body["input"]["images"][0]["name"] == graph["1"]["inputs"]["audio"]
    assert body["input"]["images"][0]["name"].endswith(".wav")
    manifest = json.loads((voices_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["l01"]["speaker"] == "ANANAS" and manifest["l01"]["stamp"]["voice"] == "chatterbox:ananas"
    # Plan 33 (DEC-316): the job client bills the execution time alone (8 s), the 1 s delay is shown beside it.
    assert manifest["l02"]["gpu_seconds"] == 8.0 and manifest["l02"]["billed_usd"] == round(8 * 1.58 / 3600, 4)
    assert voice_ep01.plan_lines(refs, voices_dir)[0]["done"] is True  # the WAV exists: skipped next time


def test_render_ep01_mixes_existing_voices_without_calling_gemini(tmp_path, monkeypatch):
    voices = tmp_path / "voices"
    voices.mkdir()
    monkeypatch.setattr(render_ep01, "voice_lines", lambda **_: pytest.fail("Gemini must not be called"))
    monkeypatch.setattr(render_ep01, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(render_ep01.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    with pytest.raises(SystemExit) as stopped:
        render_ep01.main(["--use-existing-voices", "--voices-dir", str(voices)])
    assert "l01" in str(stopped.value) and "l18" in str(stopped.value) and "voice_ep01_comfy" in str(stopped.value)
    for line in render_ep01.spoken_lines():
        (voices / f"{line['id']}.wav").write_bytes(b"RIFF")
    steps = []
    for name in ("build_ass", "build_video", "build_audio", "run"):
        monkeypatch.setattr(render_ep01, name, lambda *a, _n=name, **k: steps.append(_n))
    assert render_ep01.main(["--use-existing-voices", "--voices-dir", str(voices)]) is None
    assert steps == ["build_ass", "build_video", "build_audio", "run", "run"]
    assert render_ep01.VOICE_DIR == voices
    with pytest.raises(SystemExit):  # the two voice sources exclude each other
        render_ep01.parse_args(["--use-existing-voices", "--no-voices"])
