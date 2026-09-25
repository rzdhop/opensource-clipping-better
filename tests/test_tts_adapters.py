"""TTS adapters (spec 8.1 / 8.3): Edge through the existing voiceover core (no
fork), Gemini Flash-Lite TTS over REST, the local engines probed and imported
only inside the call that needs them. Word timestamps when the engine gives
them, the source recorded per line (spec 6.4)."""

import ast
import base64
import json
import pathlib
import sys
import wave

import pytest

from clipping.providers import errors, generation, tts
from clipping.providers.generation import GenRequest
from clipping.providers.registry import Link
from clipping.providers.transport import Response

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_adapters_register_for_tts_only():
    assert generation.adapter_for("tts", "edge") is tts.EDGE
    assert generation.adapter_for("tts", "gemini") is tts.GEMINI_TTS
    assert generation.adapter_for("tts", "local") is tts.LOCAL_TTS
    assert generation.adapter_for("image", "edge") is None


def test_no_optional_package_is_imported_at_module_scope():
    tree = ast.parse((ROOT / "clipping" / "providers" / "tts.py").read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top.add(node.module.split(".")[0])
    for name in ("edge_tts", "piper", "kokoro", "chatterbox", "torch", "numpy", "google", "openai"):
        assert name not in top, name
    for name in ("piper", "kokoro", "chatterbox"):
        assert name not in sys.modules, f"{name} must never be imported at startup"


# -------------------------------------------------------------------- edge

def fake_synth(text, voice, audio_path, subs_path=None):
    pathlib.Path(audio_path).write_bytes(b"ID3fake-mp3")
    words = text.split()
    return [{"start": i * 0.4, "end": i * 0.4 + 0.35, "text": w,
             "words": [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35, "probability": 1.0}]}
            for i, w in enumerate(words)]


def test_edge_synthesises_through_the_voiceover_core_and_keeps_word_timestamps(tmp_path):
    request = GenRequest(kind="tts", text="Attends quoi c'est une erreur", out_dir=str(tmp_path), extra={"name": "line_01"})
    result = tts.EDGE.generate(Link("edge", "fr-FR-HenriNeural"), request, credentials={}, on_log=lambda *a: None,
                               synthesize=fake_synth)
    assert result.provider == "edge" and result.model == "fr-FR-HenriNeural"
    assert result.paths[0].endswith("line_01.mp3") and pathlib.Path(result.paths[0]).read_bytes() == b"ID3fake-mp3"
    timing = json.loads(pathlib.Path(result.paths[1]).read_text(encoding="utf-8"))
    assert timing["$schema"] == "line_timing_v1"
    assert timing["source"] == "tts_word_timestamps"
    assert [w["word"] for w in timing["words"]] == ["Attends", "quoi", "c'est", "une", "erreur"]
    assert timing["duration_s"] == 1.95 and result.meta["duration_s"] == 1.95
    assert tts.EDGE.estimate(Link("edge", "fr-FR-HenriNeural"), request) is None


def test_edge_without_the_package_says_how_to_install_it(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "_installed", lambda name: False)
    ok, note = tts.EDGE.probe(Link("edge", "fr-FR-HenriNeural"), credentials={})
    assert ok is False and "pip install edge-tts" in note
    with pytest.raises(errors.ProviderError) as excinfo:
        tts.EDGE.generate(Link("edge", "fr-FR-HenriNeural"), GenRequest(kind="tts", text="x", out_dir=str(tmp_path)),
                          credentials={}, on_log=lambda *a: None)
    assert "pip install edge-tts" in str(excinfo.value)


def test_edge_uses_the_voiceover_core_not_a_copy():
    src = (ROOT / "clipping" / "providers" / "tts.py").read_text(encoding="utf-8")
    assert "voiceover._synthesize_async" in src or "from clipping.voiceover import _synthesize_async" in src
    assert "edge_tts.Communicate" not in src, "the Communicate call lives in voiceover.py only"


# ------------------------------------------------------------------ gemini

class FakeTransport:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body})
        status, payload = self.answers.pop(0)
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode()
        return Response(status, {}, payload)


def test_gemini_tts_asks_for_audio_and_writes_a_wav(tmp_path):
    pcm = b"\x00\x01" * 24000  # one second of 16-bit mono at 24 kHz
    transport = FakeTransport([(200, {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(pcm).decode()}}]}}]})])
    request = GenRequest(kind="tts", text="Bonjour", voice="Kore", out_dir=str(tmp_path), extra={"name": "l1"})
    result = tts.GEMINI_TTS.generate(Link("gemini", "flash-lite-tts"), request, credentials={"GOOGLE_API_KEY": "gk"},
                                     on_log=lambda *a: None, transport=transport)
    call = transport.calls[0]
    assert call["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash-lite-tts:generateContent"
    body = json.loads(call["body"])
    assert body["contents"][0]["parts"] == [{"text": "Bonjour"}]
    assert body["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert body["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore"
    with wave.open(result.paths[0]) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) == (1, 2, 24000, 24000)
    timing = json.loads(pathlib.Path(result.paths[1]).read_text(encoding="utf-8"))
    assert timing["source"] == "audio_duration_only" and timing["duration_s"] == 1.0 and timing["words"] == []
    assert tts.GEMINI_TTS.estimate(Link("gemini", "flash-lite-tts"), request) is None


def test_gemini_tts_reports_an_answer_without_audio(tmp_path):
    transport = FakeTransport([(200, {"candidates": [{"content": {"parts": [{"text": "no"}]}, "finishReason": "OTHER"}]})])
    with pytest.raises(errors.ProviderError) as excinfo:
        tts.GEMINI_TTS.generate(Link("gemini", "flash-lite-tts"), GenRequest(kind="tts", text="x", out_dir=str(tmp_path)),
                                credentials={"GOOGLE_API_KEY": "gk"}, on_log=lambda *a: None, transport=transport)
    assert "no audio" in str(excinfo.value)


# ------------------------------------------------------------------- local

@pytest.mark.parametrize("model,package", [("piper", "piper"), ("kokoro", "kokoro"), ("chatterbox", "chatterbox")])
def test_a_missing_local_engine_is_probed_and_reported_with_the_extras_hint(model, package, tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "_installed", lambda name: False)
    ok, note = tts.LOCAL_TTS.probe(Link("local", model), credentials={})
    assert ok is False and "rzdhop-ai[local-tts]" in note and package in note
    with pytest.raises(errors.ProviderError) as excinfo:
        tts.LOCAL_TTS.generate(Link("local", model), GenRequest(kind="tts", text="x", out_dir=str(tmp_path)),
                               credentials={}, on_log=lambda *a: None)
    assert "rzdhop-ai[local-tts]" in str(excinfo.value)


def test_an_unknown_local_engine_is_a_404_so_the_runner_moves_on():
    with pytest.raises(Exception) as excinfo:
        tts.LOCAL_TTS.probe(Link("local", "xtts"), credentials={})
    assert getattr(excinfo.value, "status_code", None) == 404
    assert errors.is_model_unavailable(excinfo.value)


def test_xtts_is_never_offered():
    src = (ROOT / "clipping" / "providers" / "tts.py").read_text(encoding="utf-8").lower()
    assert "xtts" not in src.replace("never xtts", "").replace("not xtts", "")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "local-tts" in pyproject and "xtts" not in pyproject.lower()
    extras = pyproject[pyproject.index("local-tts"):]
    for package in ("piper-tts", "kokoro", "chatterbox-tts"):
        assert package in extras, package


# ------------------------------------------------------------------ voices

def test_the_voice_catalogue_covers_the_default_chain_in_both_languages():
    voices = tts.load_voices()
    assert voices["$schema"] == "voices_v1"
    for provider in ("edge", "gemini", "piper", "kokoro"):
        assert voices["providers"][provider], provider
    ids = {v["voice_id"] for v in voices["providers"]["edge"]}
    assert "fr-FR-HenriNeural" in ids and "en-US-GuyNeural" in ids
    for provider, entries in voices["providers"].items():
        for entry in entries:
            assert set(entry) >= {"voice_id", "lang", "gender", "age", "style_tags"}, (provider, entry)
    assert len(tts.voices_for("edge", "fr")) >= 4
    assert len(tts.voices_for("edge", "en")) >= 4
    assert tts.voices_for("kokoro", "fr") == [v for v in voices["providers"]["kokoro"] if v["lang"].startswith("fr")]
