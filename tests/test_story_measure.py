"""Opt-in "measure with real voices" of an episode script (AI Story phase 3,
stage 7; spec 6.4 source (a), 8.1; DEC-106, DEC-122).

``params.measure_voices`` on the ``script`` step synthesises every line whose
timing is not a current measurement through its speaker's pinned voice alone
(a one-link chain: never another provider, never silently), keeps the audio
and its ``line_timing_v1`` sidecar under ``episodes/ep01/assets/voice/``,
books each synthesis once in the story's ledger (unit ``char``), and re-times
the script -- and the storyboard's shot durations when there is one --
without moving a revision or an approval.

The story, the written script and the LLM stand-in are the stage-6 ones
(``tests/test_story_episode_steps.py``). TTS is ``tts.EDGE`` itself with its
``synthesize`` replaced, and ``tts.GEMINI_TTS`` itself behind a fake
transport, so the sidecars are the adapters' own. Offline and hermetic, like
the stage-6 tests: no key, chain, cap or limit of the machine reaches a test,
no request leaves the process, and ``data/`` and ``outputs/stories`` are
fingerprinted before and after every test.

Stdlib + pytest (the CI environment, DEC-012).
"""

from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path

import pytest

import test_story_episode_steps as eps
from clipping.aistory import schemas, shots, steps, templates, timing
from clipping.cancel import Cancelled
from clipping.providers import generation, pricing, tts
from clipping.providers.errors import ProviderError
from clipping.providers.registry import Link
from clipping.providers.transport import Response

NOW = eps.NOW
KIWILO, MANGELLA, BROCCOLIA = eps.KIWILO, eps.MANGELLA, eps.BROCCOLIA
VOICE_IDS = {KIWILO: "fr-FR-HenriNeural", MANGELLA: "fr-FR-DeniseNeural", BROCCOLIA: "fr-FR-VivienneMultilingualNeural"}
NAMES = eps.NAMES
GEMINI_LINK = "gemini/flash-lite-tts"
MEASURE = {"measure_voices": True}
WORD_STEP = 0.5
PCM_RATE = 24000
GEMINI_SECONDS_PER_CHAR = 0.05


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, tmp_path):
    """The stage-6 fixture's rules, plus the TTS adapters and the free-tier
    pacing reset: nothing of the machine reaches a test, nothing leaves it."""
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env (A-049)
    from clipping.providers import adapters, budget, limits, llm, pacing, transport

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in eps.CHAIN_VARS + ("TTS_CHAIN",):
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("LIMIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USAGE_PATH", str(tmp_path / "data" / "usage.json"))
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "data" / "spend.json"))
    limits.reset()
    budget.reset()
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    adapters.load_all()

    def no_network(method, url, **_kwargs):
        raise AssertionError(f"a real request was attempted: {method} {url}")

    monkeypatch.setattr(transport, "urllib_transport", no_network)

    before = {path: eps._fingerprint(path) for path in eps.REAL_FILES + eps.REAL_STORIES}
    yield
    limits.reset()
    budget.reset()
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    assert {path: eps._fingerprint(path) for path in eps.REAL_FILES + eps.REAL_STORIES} == before


@pytest.fixture
def store(tmp_path):
    return eps.StoryStore(tmp_path / "outputs", on_log=lambda line: None)


@pytest.fixture
def chains(monkeypatch):
    """Every chain handed to ``run_generation_chain``, in order."""
    seen = []
    real = generation.run_generation_chain

    def spy(kind, chain, request, **kwargs):
        seen.append((kind, list(chain), request.text))
        return real(kind, chain, request, **kwargs)

    monkeypatch.setattr(generation, "run_generation_chain", spy)
    return seen


# ------------------------------------------------------------------ the fakes

def edge_seconds(text) -> float:
    return round((len(text.split()) - 1) * WORD_STEP + 0.3, 3)


def gemini_samples(text) -> int:
    return int(PCM_RATE * GEMINI_SECONDS_PER_CHAR * len(text))


def gemini_seconds(text) -> float:
    return round(gemini_samples(text) / PCM_RATE, 3)


class Edge:
    """``tts.EDGE`` itself with its ``synthesize`` replaced (the real one
    needs ``edge-tts`` and the network). Records every call. A voice in
    *failing* raises; a voice in *no_cues* answers without word timestamps;
    *on_call(edge)* runs before each answer."""

    def __init__(self, *, failing=(), no_cues=(), on_call=None):
        self.failing = set(failing)
        self.no_cues = set(no_cues)
        self.on_call = on_call
        self.calls = []

    def estimate(self, link, request):
        return tts.EDGE.estimate(link, request)

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.calls.append({"link": link, "text": request.text, "voice": request.voice, "extra": dict(request.extra)})
        if self.on_call is not None:
            self.on_call(self)
        if link.model in self.failing:
            raise ProviderError(f"edge/{link.model}: synthetic failure")
        return tts.EDGE.generate(link, request, credentials=credentials, on_log=on_log, synthesize=self._synth)

    def _synth(self, text, voice, audio_path, subs_path=None, **prosody):
        Path(audio_path).write_bytes(b"ID3fake-mp3")
        if voice in self.no_cues:
            return []
        return [{"start": i * WORD_STEP, "end": i * WORD_STEP + 0.3, "text": word,
                 "words": [{"word": word, "start": i * WORD_STEP, "end": i * WORD_STEP + 0.3}]}
                for i, word in enumerate(text.split())]

    def texts(self):
        return [call["text"] for call in self.calls]


class NeverCalled:
    """Registered on the providers a pinned voice must never fall back to."""

    def __init__(self):
        self.calls = 0

    def estimate(self, link, request):
        return None

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, **_):
        self.calls += 1
        raise AssertionError(f"{link.provider}/{link.model} must never be called")


class GeminiTransport:
    """The Gemini speech endpoint: PCM whose length follows the text, or
    *status* for every request. Records every request."""

    def __init__(self, status=200):
        self.status = status
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=None):
        payload = json.loads(body)
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "json": payload})
        if self.status != 200:
            return Response(self.status, {}, json.dumps({"error": {"message": "overloaded"}}).encode())
        text = payload["contents"][0]["parts"][0]["text"]
        pcm = b"\x00\x00" * gemini_samples(text)
        answer = {"candidates": [{"content": {"parts": [{"inlineData": {
            "mimeType": f"audio/L16;codec=pcm;rate={PCM_RATE}", "data": base64.b64encode(pcm).decode("ascii")}}]}}]}
        return Response(200, {}, json.dumps(answer).encode("utf-8"))

    def voices(self):
        return [call["json"]["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"]
                for call in self.calls]


def _adapters(edge, *, gemini=None, local=None):
    return {("tts", "edge"): edge, ("tts", "gemini"): gemini or NeverCalled(), ("tts", "local"): local or NeverCalled()}


# ------------------------------------------------------------------ helpers

def _measure(store, story_id, *, adapters, transport=None, settings=None, clock=None, llm=None, params=MEASURE):
    m = eps._new()
    ctx, log = eps._ctx(store, story_id, params=dict(params), settings=settings)
    summary = m.script.run(ctx, runner=llm or eps.FakeLLM(), time_fn=clock or eps.Clock(100.0),
                           adapters=adapters, transport=transport)
    return summary, log


def _measure_failed(store, story_id, **kwargs):
    with pytest.raises(steps.StepFailed) as caught:
        _measure(store, story_id, **kwargs)
    return str(caught.value)


def _lines(script):
    return [line for scene in script["scenes"] for line in scene["lines"]]


def _of(script, char_id):
    return [line for line in _lines(script) if line["speaker"] == char_id]


def _ledger(store, story_id):
    path = Path(store.story_dir(story_id)) / "cost_ledger.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["entries"]


def _asset(store, story_id, name):
    return Path(store.episode_dir(story_id, 1)) / "assets" / "voice" / name


def _nn(line):
    return line["line_id"][1:]


def _pin(store, story_id, char_id, provider, voice_id):
    doc = store.read_entity(story_id, "characters", char_id)
    doc["voice"] = dict(doc["voice"], provider=provider, voice_id=voice_id)
    store.write_entity(story_id, "characters", doc, now=NOW)


def _paid_gemini(monkeypatch, usd_per_char=0.001):
    """Gemini TTS made a paid link priced per character (test values only)."""
    monkeypatch.setattr(generation, "PAID_LINKS", generation.PAID_LINKS | {GEMINI_LINK})
    monkeypatch.setitem(pricing.PRICES, GEMINI_LINK, pricing.Price("char", usd_per_char, "test price"))


def _template():
    return templates.load_episode_template("serial_60s_v1")


def _lock(store, story_id):
    return store.read_doc(story_id, "style_lock.json")


# ================================================================ measuring

def test_every_line_is_measured_through_its_pinned_voice_alone(store, chains):
    story_id = eps._written_script(store)
    story_before = eps._story_bytes(store, story_id)
    edge, gemini, local = Edge(), NeverCalled(), NeverCalled()
    llm = eps.FakeLLM()

    summary, log = _measure(store, story_id, adapters=_adapters(edge, gemini=gemini, local=local), llm=llm)

    script = eps._script(store, story_id)
    lines = _lines(script)
    assert len(lines) == 14
    # Exactly one link per line: the speaker's pinned voice, nothing else.
    assert [(kind, chain) for kind, chain, _text in chains] == [
        ("tts", [Link("edge", VOICE_IDS[line["speaker"]])]) for line in lines]
    assert edge.texts() == [line["text"] for line in lines]
    assert all(call["extra"]["rate"] == "+0%" and call["extra"]["pitch"] == "+0Hz" for call in edge.calls)
    assert gemini.calls == 0 and local.calls == 0 and llm.calls == []
    for line in lines:
        nn = _nn(line)
        assert line["timing"] == {
            "source": "tts_word_timestamps", "duration_s": edge_seconds(line["text"]),
            "text_hash": timing.text_hash(line["text"]), "voice": f"edge/{VOICE_IDS[line['speaker']]}",
            "audio": f"assets/voice/line_{nn}.mp3",
        }
        assert _asset(store, story_id, f"line_{nn}.mp3").read_bytes() == b"ID3fake-mp3"
        sidecar = json.loads(_asset(store, story_id, f"line_{nn}.json").read_text(encoding="utf-8"))
        assert sidecar["$schema"] == "line_timing_v1" and sidecar["source"] == "tts_word_timestamps"
        assert sidecar["duration_s"] == edge_seconds(line["text"])
    assert schemas.episode_script_errors(script) == []
    assert script["timing"]["measured_lines"] == 14 and script["timing"]["estimated_lines"] == 0
    assert script["timing"] == timing.episode_timing(script, _template(), "fr", style_lock=_lock(store, story_id))
    assert summary["measured"] == 14
    assert "🎙 Measuring 14 lines with the pinned voices" in log
    first = lines[0]
    assert f"🔊 {first['line_id']} Kiwilo: {edge_seconds(first['text']):.2f} s (edge/fr-FR-HenriNeural, word timings)" \
        in log
    assert log[-1].startswith("⏱ ") and " s measured — " in log[-1]
    # RC-E2: the story itself is never touched.
    assert eps._story_bytes(store, story_id) == story_before


def test_each_synthesis_is_booked_once_in_characters(store):
    story_id = eps._written_script(store)

    _measure(store, story_id, adapters=_adapters(Edge()))

    lines = _lines(eps._script(store, story_id))
    entries = _ledger(store, story_id)
    assert [(e["step"], e["ep"], e["provider"], e["model"], e["unit"], e["qty"], e["est_usd"], e["paid"])
            for e in entries] == [
        ("voice_measure", 1, "edge", VOICE_IDS[line["speaker"]], "char", len(line["text"]), 0.0, False)
        for line in lines]


def test_without_the_param_nothing_is_synthesised(store, chains):
    story_id = eps._written_script(store)
    edge = Edge()

    _measure(store, story_id, adapters=_adapters(edge), params={})

    assert chains == [] and edge.calls == [] and _ledger(store, story_id) == []
    assert all(line["timing"]["source"] == "estimated" for line in _lines(eps._script(store, story_id)))


def test_a_gemini_voice_is_measured_by_its_audio_length_and_kept_as_a_wav(store, chains):
    story_id = eps._written_script(store)
    _measure(store, story_id, adapters=_adapters(Edge()))
    before = eps._script(store, story_id)
    _pin(store, story_id, MANGELLA, "gemini", "Kore")
    edge, transport = Edge(), GeminiTransport()
    del chains[:]

    _, log = _measure(store, story_id, adapters=_adapters(edge, gemini=tts.GEMINI_TTS), transport=transport)

    script = eps._script(store, story_id)
    theirs = _of(script, MANGELLA)
    assert len(theirs) == 5
    # Only her lines: her voice changed, nobody else's did.
    assert edge.calls == []
    assert [chain for _kind, chain, _text in chains] == [[Link("gemini", "flash-lite-tts")]] * 5
    assert transport.voices() == ["Kore"] * 5
    assert all(call["headers"]["x-goog-api-key"] == "test-gemini-key" for call in transport.calls)
    for line in theirs:
        nn = _nn(line)
        assert line["timing"] == {
            "source": "audio_duration_only", "duration_s": gemini_seconds(line["text"]),
            "text_hash": timing.text_hash(line["text"]), "voice": "gemini/Kore", "audio": f"assets/voice/line_{nn}.wav",
        }
        assert _asset(store, story_id, f"line_{nn}.wav").exists()
        # The Edge take of the same line is replaced, not left beside it.
        assert not _asset(store, story_id, f"line_{nn}.mp3").exists()
        sidecar = json.loads(_asset(store, story_id, f"line_{nn}.json").read_text(encoding="utf-8"))
        assert sidecar["source"] == "audio_duration_only" and sidecar["voice"] == "Kore"
    others = [line for line in _lines(script) if line["speaker"] != MANGELLA]
    assert others == [line for line in _lines(before) if line["speaker"] != MANGELLA]
    first = theirs[0]
    assert f"🔊 {first['line_id']} Mangella: {gemini_seconds(first['text']):.2f} s (gemini/Kore, audio duration)" in log
    gemini_rows = [e for e in _ledger(store, story_id) if e["provider"] == "gemini"]
    assert [(e["model"], e["unit"], e["qty"], e["paid"]) for e in gemini_rows] == [
        (tts.GEMINI_TTS_MODELS["flash-lite-tts"], "char", len(line["text"]), False) for line in theirs]


def test_a_failing_voice_fails_only_its_lines_and_no_other_provider_is_tried(store, chains):
    story_id = eps._written_script(store)
    edge, gemini, local = Edge(failing={VOICE_IDS[BROCCOLIA]}), NeverCalled(), NeverCalled()

    message = _measure_failed(store, story_id, adapters=_adapters(edge, gemini=gemini, local=local))

    script = eps._script(store, story_id)
    hers = _of(script, BROCCOLIA)
    assert [line["line_id"] for line in hers] == ["l13", "l17", "l28"]
    for line in hers:
        assert line["line_id"] in message
        assert line["timing"]["source"] == "estimated"
        assert not _asset(store, story_id, f"line_{_nn(line)}.mp3").exists()
    assert "pick another voice for Broccolia" in message and "synthetic failure" in message
    assert "Kiwilo" not in message and "Mangella" not in message
    assert all(line["timing"]["source"] == "tts_word_timestamps" for line in _lines(script)
               if line["speaker"] != BROCCOLIA)
    # Every line was tried once, in reading order; hers went to her voice
    # alone, and nothing else was contacted.
    assert len(chains) == 14
    assert [chain for (_kind, chain, _text), line in zip(chains, _lines(script)) if line["speaker"] == BROCCOLIA] \
        == [[Link("edge", VOICE_IDS[BROCCOLIA])]] * 3
    assert gemini.calls == 0 and local.calls == 0
    # A failed synthesis is not booked.
    assert len(_ledger(store, story_id)) == 14 - 3
    assert script["timing"]["measured_lines"] == 11 and script["timing"]["estimated_lines"] == 3
    assert schemas.episode_script_errors(script) == []


def test_a_line_the_engine_answered_but_could_not_time_fails_and_is_still_booked(store, monkeypatch):
    # Edge without word cues falls back to ffprobe; with no duration either, the
    # line cannot be measured -- never a silent 0.0, never an estimate labelled measured.
    monkeypatch.setattr(tts, "audio_duration", lambda path: None)
    story_id = eps._written_script(store)
    edge = Edge(no_cues={VOICE_IDS[BROCCOLIA]})

    message = _measure_failed(store, story_id, adapters=_adapters(edge))

    script = eps._script(store, story_id)
    assert all(line["timing"]["source"] == "estimated" for line in _of(script, BROCCOLIA))
    assert "l13" in message and "duration" in message and "pick another voice for Broccolia" in message
    # The provider answered, so each call is on the ledger, once.
    assert len(_ledger(store, story_id)) == 14 and len(edge.calls) == 14


def test_a_paid_pinned_voice_is_refused_while_allow_paid_is_off_and_nothing_is_sent(store, monkeypatch):
    _paid_gemini(monkeypatch)
    story_id = eps._written_script(store)
    _pin(store, story_id, MANGELLA, "gemini", "Kore")
    transport = GeminiTransport()

    message = _measure_failed(store, story_id, adapters=_adapters(Edge(), gemini=tts.GEMINI_TTS), transport=transport)

    assert transport.calls == []
    script = eps._script(store, story_id)
    for line in _of(script, MANGELLA):
        assert line["line_id"] in message and line["timing"]["source"] == "estimated"
    assert "allow_paid is off" in message and "refused: est $" in message and "pick another voice for Mangella" \
        in message
    assert [e for e in _ledger(store, story_id) if e["provider"] == "gemini"] == []
    assert not (Path(os.environ["SPEND_PATH"])).exists()
    assert all(line["timing"]["source"] == "tts_word_timestamps" for line in _lines(script)
               if line["speaker"] != MANGELLA)


def test_a_paid_pinned_voice_with_allow_paid_is_booked_once_per_line_at_its_price(store, monkeypatch):
    from clipping.providers import budget

    _paid_gemini(monkeypatch)
    story_id = eps._written_script(store)
    _pin(store, story_id, MANGELLA, "gemini", "Kore")
    transport = GeminiTransport()
    settings = dict(eps.SETTINGS, ALLOW_PAID="1")

    _, log = _measure(store, story_id, adapters=_adapters(Edge(), gemini=tts.GEMINI_TTS), transport=transport,
                      settings=settings)

    theirs = _of(eps._script(store, story_id), MANGELLA)
    assert len(transport.calls) == len(theirs) == 5
    rows = [e for e in _ledger(store, story_id) if e["provider"] == "gemini"]
    prices = [round(0.001 * len(line["text"]), 4) for line in theirs]
    assert [(e["step"], e["ep"], e["unit"], e["qty"], e["est_usd"], e["paid"]) for e in rows] == [
        ("voice_measure", 1, "char", len(line["text"]), price, True) for line, price in zip(theirs, prices)]
    assert budget.day_spent() == pytest.approx(sum(prices))
    assert sum(line.startswith(f"   💸 {GEMINI_LINK}: est $") for line in log) == 5
    assert all(line["timing"]["source"] == "audio_duration_only" for line in theirs)


def test_a_paid_voice_that_fails_is_tried_once_and_never_booked(store, monkeypatch):
    # DEC-106: a paid request may be billed once accepted, so it is never retried.
    _paid_gemini(monkeypatch)
    story_id = eps._written_script(store)
    _pin(store, story_id, MANGELLA, "gemini", "Kore")
    transport = GeminiTransport(status=503)
    settings = dict(eps.SETTINGS, ALLOW_PAID="1")

    message = _measure_failed(store, story_id, adapters=_adapters(Edge(), gemini=tts.GEMINI_TTS),
                              transport=transport, settings=settings)

    theirs = _of(eps._script(store, story_id), MANGELLA)
    assert len(transport.calls) == len(theirs) == 5
    assert all(line["line_id"] in message for line in theirs)
    assert [e for e in _ledger(store, story_id) if e["provider"] == "gemini"] == []


def test_the_episode_cap_refuses_a_paid_line_with_its_numbers(store, monkeypatch):
    _paid_gemini(monkeypatch, usd_per_char=0.01)
    story_id = eps._written_script(store)
    _pin(store, story_id, MANGELLA, "gemini", "Kore")
    transport = GeminiTransport()
    settings = dict(eps.SETTINGS, ALLOW_PAID="1", PER_EPISODE_CAP_USD="0.40")

    message = _measure_failed(store, story_id, adapters=_adapters(Edge(), gemini=tts.GEMINI_TTS),
                              transport=transport, settings=settings)

    rows = [e for e in _ledger(store, story_id) if e["provider"] == "gemini"]
    assert 0 < len(rows) < 5 and len(transport.calls) == len(rows)
    assert sum(e["est_usd"] for e in rows) <= 0.40
    assert "this episode" in message and "$0.40 cap" in message


# =========================================================== what re-measures

def test_a_complete_re_measure_makes_no_call(store):
    story_id = eps._written_script(store)
    _measure(store, story_id, adapters=_adapters(Edge()))
    before = eps._script(store, story_id)
    rows = len(_ledger(store, story_id))
    edge = Edge()

    summary, log = _measure(store, story_id, adapters=_adapters(edge))

    assert edge.calls == [] and len(_ledger(store, story_id)) == rows
    assert eps._script(store, story_id) == before
    assert summary["measured"] == 0
    assert any(line.startswith("🎙 ") and "nothing to synthesise" in line for line in log)


def test_an_edited_line_and_a_new_voice_are_measured_again_and_nothing_else(store, chains):
    story_id = eps._written_script(store)
    _measure(store, story_id, adapters=_adapters(Edge()))
    script = eps._script(store, story_id)
    edited = next(line for line in _lines(script) if line["line_id"] == "l09")
    edited["text"] = "Je ne te suivrai jamais, Kiwilo."
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    # An edit falls back to the estimate and keeps the audio on disk.
    assert timing.line_duration(edited, "fr")[1] == "estimated"
    assert _asset(store, story_id, "line_09.mp3").exists()
    _pin(store, story_id, BROCCOLIA, "edge", "fr-CA-SylvieNeural")
    rows = len(_ledger(store, story_id))
    edge = Edge()
    del chains[:]

    _measure(store, story_id, adapters=_adapters(edge))

    script = eps._script(store, story_id)
    wanted = [line for line in _lines(script) if line["line_id"] == "l09" or line["speaker"] == BROCCOLIA]
    assert [line["line_id"] for line in wanted] == ["l09", "l13", "l17", "l28"]
    assert edge.texts() == [line["text"] for line in wanted]
    assert [chain for _kind, chain, _text in chains] == [
        [Link("edge", VOICE_IDS[MANGELLA])]] + [[Link("edge", "fr-CA-SylvieNeural")]] * 3
    assert len(_ledger(store, story_id)) == rows + 4
    edited = next(line for line in _lines(script) if line["line_id"] == "l09")
    assert edited["timing"]["text_hash"] == timing.text_hash("Je ne te suivrai jamais, Kiwilo.")
    assert edited["timing"]["duration_s"] == edge_seconds("Je ne te suivrai jamais, Kiwilo.")
    assert all(line["timing"]["voice"] == "edge/fr-CA-SylvieNeural" for line in _of(script, BROCCOLIA))
    assert script["timing"]["estimated_lines"] == 0


def test_a_line_whose_audio_is_gone_is_measured_again(store):
    story_id = eps._written_script(store)
    _measure(store, story_id, adapters=_adapters(Edge()))
    _asset(store, story_id, "line_16.mp3").unlink()
    edge = Edge()

    _measure(store, story_id, adapters=_adapters(edge))

    line = next(line for line in _lines(eps._script(store, story_id)) if line["line_id"] == "l16")
    assert edge.texts() == [line["text"]]
    assert _asset(store, story_id, "line_16.mp3").exists()


# ===================================================== what measuring leaves

def test_measurement_changes_no_content_revision_approval_or_report(store):
    m = eps._new()
    story_id = eps._written_script(store)
    m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=eps.Log())
    script = eps._script(store, story_id)
    script["approved_at"] = NOW
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    board = eps._storyboard(store, story_id)
    board["approved_at"] = NOW
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    script_before, board_before = eps._script(store, story_id), eps._storyboard(store, story_id)
    story_before = eps._story_bytes(store, story_id)

    _measure(store, story_id, adapters=_adapters(Edge()))

    script, board = eps._script(store, story_id), eps._storyboard(store, story_id)
    assert script["rev"] == script_before["rev"] and script["approved_at"] == NOW
    assert script["approved_anyway"] == script_before["approved_anyway"]
    assert script["consistency_report"] == script_before["consistency_report"]
    assert script["consistency_report"]["stale"] is False

    def without_timing(doc):
        doc = copy.deepcopy(doc)
        for line in _lines(doc):
            line.pop("timing")
        return {key: value for key, value in doc.items() if key not in ("timing", "updated_at")}

    assert without_timing(script) == without_timing(script_before)
    assert all(scene["rev"] == 1 for scene in script["scenes"])
    assert board["approved_at"] == NOW and board["rev"] == board_before["rev"]
    assert all(not entry["stale"] for entry in board["scenes"].values())
    assert eps._story_bytes(store, story_id) == story_before


def test_the_storyboard_durations_follow_the_measured_lines_and_nothing_else_moves(store):
    m = eps._new()
    story_id = eps._written_script(store)
    m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=eps.Log())
    before = eps._storyboard(store, story_id)

    _measure(store, story_id, adapters=_adapters(Edge()))

    script, board = eps._script(store, story_id), eps._storyboard(store, story_id)
    assert [shot["duration_s"] for shot in board["shots"]] != [shot["duration_s"] for shot in before["shots"]]
    for old, new in zip(before["shots"], board["shots"]):
        assert {k: v for k, v in new.items() if k != "duration_s"} == {k: v for k, v in old.items() if k != "duration_s"}
    for key in ("transitions", "scenes", "resolved_from", "rev", "approved_at", "created_at", "ep"):
        assert board[key] == before[key], key
    # The durations are the ones the same plans would be built with now.
    ec = m.common.load_context(store, story_id, 1)
    plans = shots.plans_from_storyboard(before, script)
    expected, _notes = shots.build_storyboard(
        script, plans, {sid: entry["source"] for sid, entry in before["scenes"].items()}, entities=ec.entities,
        style_lock=ec.style_lock, template=ec.template, language=ec.language, consistency_mode=ec.consistency_mode,
        now=NOW)
    assert [shot["duration_s"] for shot in board["shots"]] == [shot["duration_s"] for shot in expected["shots"]]
    assert schemas.storyboard_errors(board, min_shot_s=ec.template["min_shot_s"]) == []
    # The script is timed with the storyboard's own transitions.
    assert script["timing"] == timing.episode_timing(script, _template(), "fr", style_lock=_lock(store, story_id),
                                                     storyboard=board)


def test_retime_storyboard_leaves_a_stale_scene_as_it_was(store):
    m = eps._new()
    story_id = eps._written_script(store)
    m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=eps.Log())
    board = eps._storyboard(store, story_id)
    board["scenes"]["s03"]["stale"] = True
    before = copy.deepcopy(board)
    script = eps._script(store, story_id)
    for line in _lines(script):
        line["timing"] = dict(line["timing"], source="tts_word_timestamps", duration_s=edge_seconds(line["text"]))
    ec = m.common.load_context(store, story_id, 1)

    changed = shots.retime_storyboard(board, script, template=ec.template, language=ec.language,
                                      style_lock=ec.style_lock)

    assert changed is True
    stale = [shot for shot in board["shots"] if shot["scene_id"] == "s03"]
    assert [shot["duration_s"] for shot in stale] == [
        shot["duration_s"] for shot in before["shots"] if shot["scene_id"] == "s03"]
    assert board["scenes"] == before["scenes"] and board["rev"] == before["rev"]
    assert shots.retime_storyboard(board, script, template=ec.template, language=ec.language,
                                   style_lock=ec.style_lock) is False


# ============================================================ cancel, budget

def test_a_cancel_between_lines_keeps_what_was_measured(store):
    story_id = eps._written_script(store)
    ctx, log = eps._ctx(store, story_id, params=MEASURE)

    def cancel_on_third(edge):
        if len(edge.calls) == 3:
            ctx.cancel.cancel()

    edge = Edge(on_call=cancel_on_third)
    m = eps._new()
    with pytest.raises(Cancelled):
        m.script.run(ctx, runner=eps.FakeLLM(), time_fn=eps.Clock(100.0), adapters=_adapters(edge))

    assert len(edge.calls) == 3
    script = eps._script(store, story_id)
    assert schemas.episode_script_errors(script) == []
    assert [line["timing"]["source"] for line in _lines(script)] == ["tts_word_timestamps"] * 3 + ["estimated"] * 11
    assert len(_ledger(store, story_id)) == 3


def test_the_step_budget_refuses_a_synthesis_that_could_not_finish(store):
    story_id = eps._written_script(store)
    clock = eps.Clock(0.0)

    def slow(edge):
        clock.now += 600.0

    edge = Edge(on_call=slow)
    message = _measure_failed(store, story_id, adapters=_adapters(edge), clock=clock)

    # Syntheses start at 0, 600 and 1200 (1200 + 60 fits in 1800); the 4th
    # would start at 1800 and could not finish: it is never started.
    assert len(edge.calls) == 3
    assert "30-minute" in message and "run the step again to continue" in message and "1 min" in message
    script = eps._script(store, story_id)
    lines = _lines(script)
    assert "Left: the voice measurement of lines " in message and lines[3]["line_id"] in message
    assert [line["timing"]["source"] for line in lines] == ["tts_word_timestamps"] * 3 + ["estimated"] * 11

    again = Edge()
    _measure(store, story_id, adapters=_adapters(again), clock=eps.Clock(0.0))
    assert again.texts() == [line["text"] for line in lines[3:]]


# ================================================================= narrator

def test_a_narrator_line_without_a_voice_fails_by_name(store):
    story_id = eps._written_script(store)
    store.update(story_id, lambda doc: doc["narrator"].update(enabled=True), now=NOW)
    script = eps._script(store, story_id)
    hook_line = script["scenes"][0]["lines"][0]
    assert hook_line["line_id"] == "l04"
    hook_line["speaker"] = "narrator"
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    edge = Edge()

    message = _measure_failed(store, story_id, adapters=_adapters(edge))

    assert "l04" in message and "narrator" in message and "pick a voice for the narrator" in message
    script = eps._script(store, story_id)
    assert script["scenes"][0]["lines"][0]["timing"]["source"] == "estimated"
    assert len(edge.calls) == 13
    assert all(line["timing"]["source"] == "tts_word_timestamps" for line in _lines(script)[1:])

    store.update(story_id, lambda doc: doc["narrator"].update(
        voice={"provider": "edge", "voice_id": "fr-FR-RemyMultilingualNeural"}), now=NOW)
    again = Edge()
    _, log = _measure(store, story_id, adapters=_adapters(again))

    assert [call["link"] for call in again.calls] == [Link("edge", "fr-FR-RemyMultilingualNeural")]
    narrated = eps._script(store, story_id)["scenes"][0]["lines"][0]
    assert narrated["timing"]["voice"] == "edge/fr-FR-RemyMultilingualNeural"
    assert any(line.startswith("🔊 l04 Narrator: ") for line in log)


# ================================================================= estimate

def test_measure_estimate_counts_lines_characters_and_allowances_without_calling_anything(store, monkeypatch):
    m = eps._new()
    story_id = eps._written_script(store)
    _pin(store, story_id, MANGELLA, "gemini", "Kore")
    ec = m.common.load_context(store, story_id, 1)
    script = eps._script(store, story_id)
    lines = _lines(script)

    estimate = m.script.measure_estimate(ec, script, env=eps.SETTINGS)

    assert _ledger(store, story_id) == [] and not Path(os.environ["USAGE_PATH"]).exists()
    assert estimate["lines"] == 14 and estimate["chars"] == sum(len(line["text"]) for line in lines)
    assert [row["voice"] for row in estimate["voices"]] == [
        "edge/fr-FR-HenriNeural", "gemini/Kore", "edge/fr-FR-VivienneMultilingualNeural"]
    for row, char_id in zip(estimate["voices"], (KIWILO, MANGELLA, BROCCOLIA)):
        assert row["paid"] is False and row["est_usd"] == 0.0 and row["allowed"] is True and row["reason"] is None
        assert row["lines"] == len(_of(script, char_id))
        assert row["chars"] == sum(len(line["text"]) for line in _of(script, char_id))
        assert row["speakers"] == [NAMES[char_id]]
    assert estimate["voices"][1]["link"] == GEMINI_LINK
    assert estimate["est_usd"] == 0.0 and estimate["paid_links"] == [] and estimate["allow_paid"] is False
    assert estimate["free_tier"] == {
        "edge": {"rpm": 30, "rpd": None, "calls": 0, "left": None, "needed": 9},
        "gemini": {"rpm": 15, "rpd": 250, "calls": 0, "left": 250, "needed": 5},
    }
    assert estimate["unvoiced"] == [] and estimate["ready"] is True

    # A day's allowance too small for the lines is named before anything is
    # sent (the limiter reads LIMIT_* from the process environment).
    monkeypatch.setenv("LIMIT_GEMINI_RPD", "3")
    tight = m.script.measure_estimate(ec, script, env=eps.SETTINGS)
    kore = next(row for row in tight["voices"] if row["voice"] == "gemini/Kore")
    assert kore["allowed"] is False and "3 of 3 gemini calls left today, 5 needed" in kore["reason"]
    assert tight["ready"] is False


def test_measure_estimate_prices_a_paid_voice_and_says_what_blocks_it(store, monkeypatch):
    _paid_gemini(monkeypatch)
    m = eps._new()
    story_id = eps._written_script(store)
    _pin(store, story_id, MANGELLA, "gemini", "Kore")
    transport = GeminiTransport()
    ec = m.common.load_context(store, story_id, 1)
    script = eps._script(store, story_id)
    theirs = _of(script, MANGELLA)
    price = round(sum(round(0.001 * len(line["text"]), 4) for line in theirs), 4)

    estimate = m.script.measure_estimate(ec, script, env=eps.SETTINGS)

    assert transport.calls == [] and _ledger(store, story_id) == []
    kore = next(row for row in estimate["voices"] if row["voice"] == "gemini/Kore")
    assert kore["link"] == GEMINI_LINK and kore["paid"] is True and kore["lines"] == 5
    assert kore["chars"] == sum(len(line["text"]) for line in theirs)
    assert kore["est_usd"] == price and kore["allowed"] is False
    assert kore["reason"] == f"refused: est ${price:.3f} on {GEMINI_LINK}; allow_paid is off (today $0.00 of $3.00)"
    assert estimate["est_usd"] == price and estimate["allow_paid"] is False
    assert estimate["paid_links"] == [{"link": GEMINI_LINK, "allowed": False, "reason": kore["reason"]}]
    # A paid link never draws on the free allowance.
    assert set(estimate["free_tier"]) == {"edge"} and estimate["ready"] is False

    # Measured lines drop out: what is left is Mangella's, allowed once paid is on.
    _measure_failed(store, story_id, adapters=_adapters(Edge(), gemini=tts.GEMINI_TTS), transport=transport)
    later = m.script.measure_estimate(ec, eps._script(store, story_id), env=dict(eps.SETTINGS, ALLOW_PAID="1"))
    assert later["lines"] == 5 and [row["voice"] for row in later["voices"]] == ["gemini/Kore"]
    assert later["voices"][0]["allowed"] is True and later["ready"] is True and later["allow_paid"] is True
    assert later["paid_links"] == [{"link": GEMINI_LINK, "allowed": True, "reason": None}]
    capped = m.script.measure_estimate(ec, eps._script(store, story_id),
                                       env=dict(eps.SETTINGS, ALLOW_PAID="1", PER_EPISODE_CAP_USD="0.05"))
    assert capped["voices"][0]["allowed"] is False and "$0.05 cap" in capped["voices"][0]["reason"]


def test_measure_estimate_names_a_line_nobody_can_voice(store):
    m = eps._new()
    story_id = eps._written_script(store)
    store.update(story_id, lambda doc: doc["narrator"].update(enabled=True), now=NOW)
    script = eps._script(store, story_id)
    script["scenes"][0]["lines"][0]["speaker"] = "narrator"
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    ec = m.common.load_context(store, story_id, 1)

    estimate = m.script.measure_estimate(ec, eps._script(store, story_id), env=eps.SETTINGS)

    assert estimate["unvoiced"] == [{"line_id": "l04", "speaker": "narrator", "reason": "the narrator has no voice yet"}]
    assert estimate["lines"] == 13 and estimate["est_usd"] == 0.0 and estimate["ready"] is False
