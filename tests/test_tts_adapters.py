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


def test_edge_without_word_cues_measures_the_audio_instead_of_saying_zero(tmp_path):
    def synth_no_cues(text, voice, audio_path, subs_path=None):
        pathlib.Path(audio_path).write_bytes(b"ID3fake-mp3")
        return []

    log = []
    request = GenRequest(kind="tts", text="Bonjour", out_dir=str(tmp_path), extra={"name": "l2"})
    result = tts.EDGE.generate(Link("edge", "fr-FR-HenriNeural"), request, credentials={}, on_log=log.append,
                               synthesize=synth_no_cues, probe_duration=lambda path: 2.5)
    timing = json.loads(pathlib.Path(result.paths[1]).read_text(encoding="utf-8"))
    assert timing["source"] == "audio_duration_only" and timing["duration_s"] == 2.5 and timing["words"] == []
    assert any("no word timestamps" in line for line in log)
    assert tts.audio_duration(str(tmp_path / "missing.mp3")) is None, "an unreadable file measures as unknown, not 0"


def test_the_voiceover_core_asks_edge_tts_for_word_boundaries(monkeypatch):
    """edge-tts 7.2 defaults to sentence boundaries, which left every voice-over
    without word timings (found live on 2026-09-25)."""
    import asyncio
    import datetime

    from clipping import voiceover

    seen = {}

    class Cue:
        def __init__(self, word, start, end):
            self.content, self.start, self.end = word, datetime.timedelta(seconds=start), datetime.timedelta(seconds=end)

    class SubMaker:
        def __init__(self):
            self.cues = []

        def feed(self, chunk):
            self.cues.append(Cue(chunk["text"], chunk["offset"] / 1e7, (chunk["offset"] + chunk["duration"]) / 1e7))

        def get_srt(self):
            return "1\n00:00:00,000 --> 00:00:00,400\nBonjour\n"

    class Communicate:
        def __init__(self, text, voice, *, boundary="SentenceBoundary"):
            seen["boundary"] = boundary

        async def stream(self):
            yield {"type": "audio", "data": b"ID3"}
            yield {"type": "WordBoundary", "offset": 0, "duration": 4_000_000, "text": "Bonjour"}

    fake = type("EdgeTts", (), {"Communicate": Communicate, "SubMaker": SubMaker})
    monkeypatch.setattr(voiceover, "edge_tts", fake)
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        segments = asyncio.run(voiceover._synthesize_async("Bonjour", "fr-FR-HenriNeural", f"{d}/a.mp3", f"{d}/a.srt"))
    assert seen["boundary"] == "WordBoundary"
    assert segments and segments[0]["words"][0]["word"] == "Bonjour"

    class OldCommunicate:
        def __init__(self, text, voice):
            seen["old"] = True

        async def stream(self):
            yield {"type": "audio", "data": b"ID3"}

    monkeypatch.setattr(voiceover, "edge_tts", type("EdgeTts", (), {"Communicate": OldCommunicate, "SubMaker": SubMaker}))
    assert voiceover._word_boundary_kwargs() == {}, "an edge-tts without the argument gets none"


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


def _gemini_answer(pcm, rate=24000):
    return (200, {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": f"audio/L16;codec=pcm;rate={rate}", "data": base64.b64encode(pcm).decode()}}]}}]})


def test_gemini_tts_cuts_the_static_at_the_end_of_a_line_and_says_so(tmp_path):
    """The known fault (a burst of static after the last word): the WAV is
    the cleaned PCM, its duration the cleaned length, and the sidecar and
    the result's meta carry the tail guard's report."""
    import test_tts_tail as ttt

    pcm = ttt.pcm(ttt.voiced(1.4), ttt.silence(0.1), ttt.static(0.5))
    log = []
    request = GenRequest(kind="tts", text="Bonjour", voice="Kore", out_dir=str(tmp_path), extra={"name": "line_03"})
    result = tts.GEMINI_TTS.generate(Link("gemini", "flash-lite-tts"), request, credentials={"GOOGLE_API_KEY": "gk"},
                                     on_log=log.append, transport=FakeTransport([_gemini_answer(pcm)]))

    with wave.open(result.paths[0]) as wav:
        frames = wav.getnframes()
        kept = wav.readframes(frames)
    timing = json.loads(pathlib.Path(result.paths[1]).read_text(encoding="utf-8"))
    guard = timing["tail_guard"]
    assert guard["version"] == 1 and guard["reason"] == "noise_after_gap"
    assert guard["original_s"] == 2.0 and 1.4 <= guard["kept_s"] <= 1.46
    assert timing["duration_s"] == guard["kept_s"] == round(frames / 24000, 3)
    assert result.meta["duration_s"] == timing["duration_s"] and result.meta["tail_guard"] == guard
    assert abs(ttt.samples_of(kept)[-1]) <= 50, "faded out at the new end"
    notes = [line for line in log if "static" in line]
    assert len(notes) == 1 and "gemini/flash-lite-tts" in notes[0] and f"{guard['trimmed_s']:.2f} s" in notes[0]


def test_gemini_tts_a_clean_line_keeps_its_length_and_records_the_guard(tmp_path):
    import test_tts_tail as ttt

    pcm = ttt.pcm(ttt.voiced(1.2), ttt.silence(0.3))
    log = []
    request = GenRequest(kind="tts", text="Bonjour", voice="Kore", out_dir=str(tmp_path), extra={"name": "line_04"})
    result = tts.GEMINI_TTS.generate(Link("gemini", "flash-lite-tts"), request, credentials={"GOOGLE_API_KEY": "gk"},
                                     on_log=log.append, transport=FakeTransport([_gemini_answer(pcm)]))

    timing = json.loads(pathlib.Path(result.paths[1]).read_text(encoding="utf-8"))
    assert timing["duration_s"] == 1.5 and timing["tail_guard"]["reason"] == "none"
    assert timing["tail_guard"]["trimmed_s"] == 0.0
    assert not [line for line in log if "static" in line]


def test_gemini_tts_a_suspect_line_is_kept_whole_and_said(tmp_path):
    import test_tts_tail as ttt

    pcm = ttt.pcm(ttt.static(1.0))
    log = []
    request = GenRequest(kind="tts", text="Ah", voice="Kore", out_dir=str(tmp_path), extra={"name": "line_05"})
    result = tts.GEMINI_TTS.generate(Link("gemini", "flash-lite-tts"), request, credentials={"GOOGLE_API_KEY": "gk"},
                                     on_log=log.append, transport=FakeTransport([_gemini_answer(pcm)]))

    timing = json.loads(pathlib.Path(result.paths[1]).read_text(encoding="utf-8"))
    assert timing["duration_s"] == 1.0 and timing["tail_guard"]["reason"] == "suspect"
    assert any("left whole" in line for line in log)


def test_edge_and_local_lines_carry_no_tail_guard(tmp_path, monkeypatch):
    """Only Gemini has the fault: the other engines' sidecars are what they were."""
    request = GenRequest(kind="tts", text="Bonjour", out_dir=str(tmp_path), extra={"name": "line_06"})
    result = tts.EDGE.generate(Link("edge", "fr-FR-HenriNeural"), request, credentials={}, on_log=lambda *a: None,
                               synthesize=fake_synth)
    assert "tail_guard" not in json.loads(pathlib.Path(result.paths[1]).read_text(encoding="utf-8"))
    assert "tail_guard" not in result.meta

    monkeypatch.setattr(tts, "_installed", lambda name: True)

    def fake_piper(text, voice, out_path, request, on_log):
        with wave.open(out_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\x00\x10" * 2400)

    monkeypatch.setitem(tts._LOCAL_SYNTH, "piper", fake_piper)
    local = tts.LOCAL_TTS.generate(Link("local", "piper"), GenRequest(kind="tts", text="x", out_dir=str(tmp_path),
                                                                     extra={"name": "line_07"}),
                                   credentials={}, on_log=lambda *a: None)
    assert "tail_guard" not in json.loads(pathlib.Path(local.paths[1]).read_text(encoding="utf-8"))
    with wave.open(local.paths[0]) as wav:
        assert wav.readframes(wav.getnframes()) == b"\x00\x10" * 2400, "a local engine's audio is untouched"


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


# ------------------------------------------------- rate/pitch (AI Story phase 2, stage 3, Contract A)

def test_edge_forwards_rate_and_pitch_to_the_injected_synthesize(tmp_path):
    seen = {}

    def synth(text, voice, audio_path, subs_path=None, **kwargs):
        pathlib.Path(audio_path).write_bytes(b"ID3fake-mp3")
        seen["kwargs"] = kwargs
        return []

    request = GenRequest(kind="tts", text="Bonjour", out_dir=str(tmp_path), extra={"rate": "+10%", "pitch": "-5Hz"})
    tts.EDGE.generate(Link("edge", "fr-FR-HenriNeural"), request, credentials={}, on_log=lambda *a: None,
                      synthesize=synth)
    assert seen["kwargs"] == {"rate": "+10%", "pitch": "-5Hz"}


def test_edge_calls_the_injected_synthesize_with_no_extra_kwargs_when_neither_is_given(tmp_path):
    """An old-style stand-in taking only the four original arguments (like
    ``fake_synth`` above) must keep working: RC-T2, exercised on the
    already-existing test double rather than a new one."""
    request = GenRequest(kind="tts", text="Bonjour", out_dir=str(tmp_path), extra={"name": "line_02"})
    result = tts.EDGE.generate(Link("edge", "fr-FR-HenriNeural"), request, credentials={}, on_log=lambda *a: None,
                               synthesize=fake_synth)
    assert result.paths[0].endswith("line_02.mp3")


@pytest.mark.parametrize("field,value", [("rate", "loud"), ("pitch", "high"), ("rate", "10%"), ("pitch", "5Hz")])
def test_edge_refuses_a_malformed_rate_or_pitch(field, value, tmp_path):
    request = GenRequest(kind="tts", text="Bonjour", out_dir=str(tmp_path), extra={field: value})
    with pytest.raises(ValueError, match=field):
        tts.EDGE.generate(Link("edge", "fr-FR-HenriNeural"), request, credentials={}, on_log=lambda *a: None,
                          synthesize=fake_synth)


@pytest.mark.parametrize("value", ["+10%", "-99%", "+0%"])
def test_edge_accepts_a_well_formed_rate(value, tmp_path):
    def synth(text, voice, audio_path, subs_path=None, **kwargs):
        pathlib.Path(audio_path).write_bytes(b"ID3fake-mp3")
        return []

    request = GenRequest(kind="tts", text="Bonjour", out_dir=str(tmp_path), extra={"rate": value})
    tts.EDGE.generate(Link("edge", "fr-FR-HenriNeural"), request, credentials={}, on_log=lambda *a: None,
                      synthesize=synth)


def test_gemini_prints_once_that_rate_pitch_are_not_supported(tmp_path):
    image = {"inlineData": {"mimeType": "audio/L16;rate=24000", "data": base64.b64encode(b"ab").decode()}}
    transport = FakeTransport([(200, {"candidates": [{"content": {"parts": [image]}}]})])
    log = []
    request = GenRequest(kind="tts", text="x", voice="Kore", out_dir=str(tmp_path), extra={"rate": "+10%"})
    tts.GEMINI_TTS.generate(Link("gemini", "flash-lite-tts"), request, credentials={"GOOGLE_API_KEY": "gk"},
                            on_log=log.append, transport=transport)
    warnings = [line for line in log if "not supported" in line]
    assert len(warnings) == 1
    assert "gemini/flash-lite-tts" in warnings[0] and "recorded, not applied" in warnings[0]


def test_gemini_prints_no_warning_when_neither_rate_nor_pitch_is_given(tmp_path):
    image = {"inlineData": {"mimeType": "audio/L16;rate=24000", "data": base64.b64encode(b"ab").decode()}}
    transport = FakeTransport([(200, {"candidates": [{"content": {"parts": [image]}}]})])
    log = []
    request = GenRequest(kind="tts", text="x", voice="Kore", out_dir=str(tmp_path))
    tts.GEMINI_TTS.generate(Link("gemini", "flash-lite-tts"), request, credentials={"GOOGLE_API_KEY": "gk"},
                            on_log=log.append, transport=transport)
    assert not any("not supported" in line for line in log)


def test_local_prints_once_that_rate_pitch_are_not_supported(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "_installed", lambda name: True)

    def fake_piper(text, voice, out_path, request, on_log):
        with wave.open(out_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\x00\x00")

    monkeypatch.setitem(tts._LOCAL_SYNTH, "piper", fake_piper)
    log = []
    request = GenRequest(kind="tts", text="x", out_dir=str(tmp_path), extra={"pitch": "-5Hz"})
    tts.LOCAL_TTS.generate(Link("local", "piper"), request, credentials={}, on_log=log.append)
    warnings = [line for line in log if "not supported" in line]
    assert len(warnings) == 1
    assert "local/piper" in warnings[0]
