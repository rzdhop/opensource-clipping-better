"""TTS adapters (spec 8.1, 8.3, 8.7).

* ``edge/<voice>`` speaks through the existing voiceover core
  (``clipping.voiceover._synthesize_async`` -- the one place that drives
  ``edge-tts``; not forked, DEC-100), keeping the word timestamps it yields.
* ``gemini/flash-lite-tts`` uses the REST speech generation endpoint and
  writes a WAV from the PCM it returns.
* ``local/piper``, ``local/kokoro`` and ``local/chatterbox`` are probed with
  ``importlib`` and imported only inside the call that synthesises; they are
  the ``[local-tts]`` extras of ``pyproject.toml``. Never XTTS: its licence is
  non-commercial (spec 8.3).

Every line gets a timing file next to its audio (``line_timing_v1``) that
records the duration and the *source* of the timestamps (spec 6.4): real
word timestamps from the engine, or the audio duration alone. Nothing here
is a silent fallback -- an engine that is missing says how to install it.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
import re
import shutil
import subprocess
import wave

from . import generation, pricing
from .errors import ProviderError
from .generation import TTS, GenResult, register_adapter
from .registry import describe
from .transport import DEFAULT_TIMEOUT, HttpStatusError, request_json, urllib_transport, write_output

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_TTS_MODELS = {"flash-lite-tts": "gemini-3.8-flash-lite-tts"}
GEMINI_DEFAULT_VOICE = "Kore"

# link model -> the importable package that provides the engine
LOCAL_ENGINES = {"piper": "piper", "kokoro": "kokoro", "chatterbox": "chatterbox"}
LOCAL_TTS_EXTRA = "pip install 'rzdhop-ai[local-tts]'"
EDGE_INSTALL = "pip install edge-tts"

TIMING_SCHEMA = "line_timing_v1"
SOURCE_WORDS = "tts_word_timestamps"
SOURCE_DURATION = "audio_duration_only"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VOICES_PATH = os.path.join(_ROOT, "clipping", "aistory", "templates", "voices.json")


# ------------------------------------------------------------------ helpers

def _installed(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _text(request) -> str:
    text = (request.text or request.prompt or "").strip()
    if not text:
        raise ValueError("a TTS request needs text")
    return text


def _out_dir(request) -> str:
    if not request.out_dir:
        raise ValueError("GenRequest.out_dir is required: where the audio is written")
    os.makedirs(request.out_dir, exist_ok=True)
    return request.out_dir


def _name(request, link) -> str:
    name = (request.extra or {}).get("name")
    return name or f"{link.provider}_{link.model}".replace("/", "_")


def _write_timing(out_dir, name, *, duration_s, words, source, provider, voice) -> str:
    data = {
        "$schema": TIMING_SCHEMA,
        "provider": provider,
        "voice": voice,
        "duration_s": duration_s,
        "source": source,
        "words": words,
    }
    return write_output(out_dir, name, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"), "json")


def _unknown_model(link, table):
    raise HttpStatusError(404, describe(link), f"model {link.model} not found in this adapter's table ({', '.join(table)})")


def audio_duration(path: str):
    """Seconds of audio in *path* by ffprobe, or ``None`` when ffprobe is missing or fails."""
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout.strip()
        return round(float(out), 3) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class _Adapter:
    provider = ""

    def estimate(self, link, request):
        if not generation.is_paid(link):
            return None
        return pricing.estimate(link, len(_text(request)))

    def probe(self, link, *, credentials, **_):
        return True, "key set; not probed (a request is the probe)"


# --------------------------------------------------------------------- edge

def _edge_synthesize(text, voice, audio_path, subs_path=None):
    """The voiceover core, run to completion. Returns its word-level segments."""
    from clipping import voiceover  # imports edge_tts at ITS module scope, guarded

    return asyncio.run(voiceover._synthesize_async(text, voice, audio_path, subs_path))


class EdgeTtsAdapter(_Adapter):
    provider = "edge"

    def probe(self, link, *, credentials, **_):
        if not _installed("edge_tts"):
            return False, f"edge-tts is not installed: {EDGE_INSTALL}"
        return True, "edge-tts installed (free, unofficial; one voice per request)"

    def generate(self, link, request, *, credentials, on_log, transport=None, synthesize=None, probe_duration=None, **_):
        if synthesize is None:
            if not _installed("edge_tts"):
                raise ProviderError(f"{describe(link)}: edge-tts is not installed: {EDGE_INSTALL}")
            synthesize = _edge_synthesize
        text = _text(request)
        voice = link.model or request.voice
        out_dir = _out_dir(request)
        name = _name(request, link)
        audio_path = os.path.join(out_dir, f"{name}.mp3")
        subs_path = os.path.join(out_dir, f"{name}.srt")
        segments = synthesize(text, voice, audio_path, subs_path) or []
        words = []
        for segment in segments:
            for word in segment.get("words") or []:
                words.append({"word": word["word"], "start": round(float(word["start"]), 3), "end": round(float(word["end"]), 3)})
        if words:
            duration, source = round(max(w["end"] for w in words), 3), SOURCE_WORDS
        else:
            # No word cues: the audio's own length, measured, never a silent 0.0.
            duration, source = (probe_duration or audio_duration)(audio_path), SOURCE_DURATION
            on_log(f"   ⚠️ edge/{voice}: no word timestamps came back; timing is the audio duration only")
        timing_path = _write_timing(out_dir, name, duration_s=duration, words=words, source=source,
                                    provider="edge", voice=voice)
        return GenResult(provider="edge", model=voice, paths=(audio_path, timing_path),
                         meta={"duration_s": duration, "words": len(words), "source": source})


# ------------------------------------------------------------------- gemini

def _pcm_rate(mime: str) -> int:
    match = re.search(r"rate=(\d+)", mime or "")
    return int(match.group(1)) if match else 24000


class GeminiTtsAdapter(_Adapter):
    provider = "gemini"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        transport = transport or urllib_transport
        model = GEMINI_TTS_MODELS.get(link.model) or _unknown_model(link, GEMINI_TTS_MODELS)
        text = _text(request)
        voice = request.voice or GEMINI_DEFAULT_VOICE
        body = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
            },
        }
        payload = request_json(transport, "POST", f"{GEMINI_BASE}/models/{model}:generateContent",
                               headers={"x-goog-api-key": credentials["GOOGLE_API_KEY"]}, json_body=body,
                               timeout=DEFAULT_TIMEOUT)
        candidates = payload.get("candidates") or []
        audio = None
        for candidate in candidates:
            for part in (candidate.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    audio = inline
                    break
            if audio:
                break
        if audio is None:
            reason = (candidates[0].get("finishReason") if candidates else None) or "unknown"
            raise ProviderError(f"{describe(link)}: the answer carried no audio (finishReason {reason})")
        mime = audio.get("mimeType") or audio.get("mime_type") or ""
        pcm = base64.b64decode(audio["data"])
        rate = _pcm_rate(mime)
        out_dir = _out_dir(request)
        name = _name(request, link)
        audio_path = os.path.join(out_dir, f"{name}.wav")
        with wave.open(audio_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(pcm)
        duration = round(len(pcm) / 2 / rate, 3)
        timing_path = _write_timing(out_dir, name, duration_s=duration, words=[], source=SOURCE_DURATION,
                                    provider="gemini", voice=voice)
        return GenResult(provider="gemini", model=link.model, paths=(audio_path, timing_path),
                         meta={"duration_s": duration, "voice": voice, "mime": mime, "source": SOURCE_DURATION})


# -------------------------------------------------------------------- local

def _synthesize_piper(text, voice, out_path, request, on_log):
    from piper import PiperVoice  # [local-tts]

    model_path = (request.extra or {}).get("model_path") or voice
    if not model_path or not os.path.exists(model_path):
        raise ProviderError(
            "local/piper needs a downloaded voice model (.onnx + .json), e.g. fr_FR-siwis-medium from "
            "https://huggingface.co/rhasspy/piper-voices/tree/main/fr/fr_FR; pass its path as the voice"
        )
    engine = PiperVoice.load(model_path)
    with wave.open(out_path, "wb") as wav:
        engine.synthesize_wav(text, wav)


def _synthesize_kokoro(text, voice, out_path, request, on_log):
    from kokoro import KPipeline  # [local-tts]
    import numpy as np

    voice = voice or "ff_siwis"
    lang_code = (request.extra or {}).get("lang_code") or voice[0]
    pipeline = KPipeline(lang_code=lang_code)
    chunks = []
    for _, _, audio in pipeline(text, voice=voice):
        chunks.append(np.asarray(audio, dtype="float32"))
    if not chunks:
        raise ProviderError("local/kokoro produced no audio")
    samples = np.concatenate(chunks)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    with wave.open(out_path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(pcm)


def _synthesize_chatterbox(text, voice, out_path, request, on_log):
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # [local-tts]
    import torchaudio

    device = (request.extra or {}).get("device") or "cpu"
    model = ChatterboxMultilingualTTS.from_pretrained(device=device)
    language = (request.extra or {}).get("language_id") or "fr"
    prompt = voice if voice and os.path.exists(voice) else None
    audio = model.generate(text, language_id=language, audio_prompt_path=prompt)
    torchaudio.save(out_path, audio, model.sr)


_LOCAL_SYNTH = {"piper": _synthesize_piper, "kokoro": _synthesize_kokoro, "chatterbox": _synthesize_chatterbox}


class LocalTtsAdapter(_Adapter):
    provider = "local"

    def probe(self, link, *, credentials, **_):
        package = LOCAL_ENGINES.get(link.model) or _unknown_model(link, LOCAL_ENGINES)
        if not _installed(package):
            return False, f"{link.model} is not installed ({package} package): {LOCAL_TTS_EXTRA}"
        return True, f"{link.model} installed"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        package = LOCAL_ENGINES.get(link.model) or _unknown_model(link, LOCAL_ENGINES)
        if not _installed(package):
            raise ProviderError(f"{describe(link)}: {link.model} is not installed ({package} package): {LOCAL_TTS_EXTRA}")
        text = _text(request)
        out_dir = _out_dir(request)
        name = _name(request, link)
        audio_path = os.path.join(out_dir, f"{name}.wav")
        _LOCAL_SYNTH[link.model](text, request.voice, audio_path, request, on_log)
        with wave.open(audio_path, "rb") as wav:
            duration = round(wav.getnframes() / float(wav.getframerate() or 1), 3)
        timing_path = _write_timing(out_dir, name, duration_s=duration, words=[], source=SOURCE_DURATION,
                                    provider="local", voice=request.voice or link.model)
        return GenResult(provider="local", model=link.model, paths=(audio_path, timing_path),
                         meta={"duration_s": duration, "source": SOURCE_DURATION})


# ------------------------------------------------------------------- voices

def load_voices(path=None) -> dict:
    with open(path or VOICES_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("$schema") != "voices_v1":
        raise ValueError(f"{path or VOICES_PATH}: expected \"$schema\": \"voices_v1\"")
    return data


def voices_for(provider: str, lang: str, path=None) -> list:
    """The catalogued voices of *provider* whose language starts with *lang* (``fr`` matches ``fr-FR``)."""
    entries = load_voices(path)["providers"].get(provider, [])
    return [v for v in entries if str(v.get("lang", "")).lower().startswith(lang.lower())]


# ------------------------------------------------------------- registration

EDGE = EdgeTtsAdapter()
GEMINI_TTS = GeminiTtsAdapter()
LOCAL_TTS = LocalTtsAdapter()

register_adapter(TTS, "edge", EDGE)
register_adapter(TTS, "gemini", GEMINI_TTS)
register_adapter(TTS, "local", LOCAL_TTS)
