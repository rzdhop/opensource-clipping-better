"""TTS adapters (spec 8.1, 8.3, 8.7).

* ``edge/<voice>`` speaks through the existing voiceover core
  (``clipping.voiceover._synthesize_async`` -- the one place that drives
  ``edge-tts``; not forked, DEC-100), keeping the word timestamps it yields.
* ``gemini/flash-lite-tts`` uses the REST speech generation endpoint and
  writes a WAV from the PCM it returns -- cleaned first by the tail guard
  (``tts_tail``): Gemini appends a burst of static after the last word (a
  known fault of its TTS), which is cut, the line faded in and out, and the
  guard's report kept in the sidecar and the result's meta (``tail_guard``).
  Edge and the local engines have no such fault and are not touched.
* ``elevenlabs/<model>`` (plan 23 stage B3) is PAID on every link: the
  ``with-timestamps`` REST endpoint answers the MP3 and a per-character
  alignment, grouped here into words, so the sidecar says ``SOURCE_WORDS``
  and the subtitles are exact. The voice id travels as ``request.voice``.
  Like Gemini it cannot apply a rate, a pitch or a spoken direction: they are
  recorded on the result (``meta["not_applied"]``) with a warning line.
* ``local/piper``, ``local/kokoro`` and ``local/chatterbox`` are probed with
  ``importlib`` and imported only inside the call that synthesises; they are
  the ``[local-tts]`` extras of ``pyproject.toml``. Never XTTS: its licence is
  non-commercial (spec 8.3).

Every line gets a timing file next to its audio (``line_timing_v1``) that
records the duration and the *source* of the timestamps (spec 6.4): real
word timestamps from the engine, or the audio duration alone. Nothing here
is a silent fallback -- an engine that is missing says how to install it.

A request may carry a spoken direction (``extra["direction"]``, how the
line should be said: an AI Story voice regenerate's note). No engine here
applies it: every one says it was recorded (with the take), not applied --
as for rate and pitch -- and sends the line alone. Gemini was tried with its
own style form ("Say <note>: <line>") and read a French note aloud on the
free TTS (Tier-2 T2-P5-F9, 2026-09-30); a note must never reach the audio.
A request without one is exactly what it always was.
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
import tempfile
import urllib.parse
import wave

from . import generation, pricing, tts_tail
from .errors import ProviderError
from .generation import TTS, GenResult, register_adapter
from .registry import describe
from .transport import DEFAULT_TIMEOUT, HttpStatusError, request_json, urllib_transport, write_output

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_TTS_MODELS = {"flash-lite-tts": "gemini-3.8-flash-lite-tts"}
GEMINI_DEFAULT_VOICE = "Kore"

ELEVENLABS_BASE = "https://api.elevenlabs.io"
ELEVENLABS_MODELS = {"flash": "eleven_flash_v2_5", "multilingual-v2": "eleven_multilingual_v2"}
ELEVENLABS_OUTPUT_FORMAT = "mp3_44100_128"
# The link models whose API accepts ``language_code`` (Flash v2.5, Turbo v2.5):
# Multilingual v2 reads the language off the text, and a request that names
# one is refused (A-161: from ElevenLabs' docs, not yet exercised on a key).
ELEVENLABS_LANGUAGE_MODELS = frozenset({"flash"})

# link model -> the importable package that provides the engine
LOCAL_ENGINES = {"piper": "piper", "kokoro": "kokoro", "chatterbox": "chatterbox"}
LOCAL_TTS_EXTRA = "pip install 'rzdhop-ai[local-tts]'"
EDGE_INSTALL = "pip install edge-tts"

TIMING_SCHEMA = "line_timing_v1"
SOURCE_WORDS = "tts_word_timestamps"
SOURCE_DURATION = "audio_duration_only"

# The character_v1 patterns (clipping.aistory.schemas.VOICE_RATE_PATTERN /
# VOICE_PITCH_PATTERN), duplicated here rather than imported: this is a
# provider module and must not import clipping.aistory (the dependency runs
# the other way -- aistory builds on providers, DEC-012-style layering).
RATE_PATTERN = re.compile(r"^[+-][0-9]{1,3}%$")
PITCH_PATTERN = re.compile(r"^[+-][0-9]{1,3}Hz$")

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


def _write_timing(out_dir, name, *, duration_s, words, source, provider, voice, tail_guard=None) -> str:
    data = {
        "$schema": TIMING_SCHEMA,
        "provider": provider,
        "voice": voice,
        "duration_s": duration_s,
        "source": source,
        "words": words,
    }
    if tail_guard is not None:
        # The tail guard's report (``tts_tail.clean``): Gemini only.
        data["tail_guard"] = dict(tail_guard)
    return write_output(out_dir, name, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"), "json")


def tail_guard_due(timing) -> bool:
    """Whether a kept line's sidecar (``line_timing_v1``) names a Gemini take
    the tail guard has not cleaned: made before the guard (a line voiced
    then, or a cached answer kept then), or by an older guard version."""
    if not isinstance(timing, dict) or timing.get("provider") != "gemini":
        return False
    guard = timing.get("tail_guard")
    version = guard.get("version") if isinstance(guard, dict) else None
    return not isinstance(version, int) or isinstance(version, bool) or version < tts_tail.TAIL_GUARD_VERSION


def _atomic_json(path, data) -> None:
    """*data* written to *path* so a reader sees the old file or the new one."""
    handle, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".timing-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def guard_kept_take(audio_path, timing_path, timing=None, *, speech_end_s=None):
    """A kept take :func:`tail_guard_due` names, cleaned where it stands, for
    free -- never spoken again, so the voice cannot change: the WAV cut and
    faded in place (``tts_tail.clean_wav``), then its sidecar rewritten with
    the cleaned ``duration_s``, the guard's report (``tail_guard``) and any
    aligned word clamped into the shorter line. *timing* is the sidecar
    already read (None: read here); *speech_end_s* the last aligned word's
    end, when there is one. Returns the new sidecar, or None when nothing was
    due or the take cannot be read (no sidecar, not a 16-bit mono WAV): left
    exactly as it is. ``OSError`` when a write fails (the audio is written
    first: a sidecar left behind is cleaned again next time)."""
    if timing is None:
        try:
            with open(timing_path, encoding="utf-8") as fh:
                timing = json.load(fh)
        except (OSError, ValueError):
            return None
    if not tail_guard_due(timing):
        return None
    report = tts_tail.clean_wav(audio_path, speech_end_s=speech_end_s)
    if report is None:
        return None
    kept = report["kept_s"]
    words = [dict(word, start=min(word["start"], kept), end=min(word["end"], kept))
             if isinstance(word, dict) and isinstance(word.get("start"), (int, float))
             and isinstance(word.get("end"), (int, float)) else word
             for word in timing.get("words") or []]
    new = dict(timing, duration_s=kept, tail_guard=report, words=words)
    _atomic_json(timing_path, new)
    return new


def _unknown_model(link, table):
    raise HttpStatusError(404, describe(link), f"model {link.model} not found in this adapter's table ({', '.join(table)})")


def _rate_pitch(request) -> tuple:
    """``(rate, pitch)`` from ``request.extra``, each ``None`` or a string
    matching the character_v1 pattern. ``ValueError`` for a value that does
    not match -- a bad rate/pitch is never silently dropped (spec 8.1/11)."""
    extra = request.extra or {}
    rate = extra.get("rate")
    if rate is not None and not RATE_PATTERN.match(str(rate)):
        raise ValueError(f"rate {rate!r} does not match {RATE_PATTERN.pattern} (e.g. '+10%', '-5%')")
    pitch = extra.get("pitch")
    if pitch is not None and not PITCH_PATTERN.match(str(pitch)):
        raise ValueError(f"pitch {pitch!r} does not match {PITCH_PATTERN.pattern} (e.g. '+5Hz', '-10Hz')")
    return rate, pitch


def _warn_unsupported_rate_pitch(request, link, on_log) -> None:
    """Gemini and the local engines cannot apply rate/pitch (spec 8.1): print
    once that the preference was recorded, not applied -- never a silent
    change of what the character asked for."""
    extra = request.extra or {}
    if extra.get("rate") is not None or extra.get("pitch") is not None:
        on_log(f"   ⚠️ rate/pitch are not supported by {describe(link)}; recorded, not applied.")


def _direction(request) -> str:
    """The request's spoken direction (``extra["direction"]``: how the line
    should be said -- an AI Story voice regenerate's note), whitespace
    collapsed, its closing punctuation dropped; "" without one."""
    return " ".join(str((request.extra or {}).get("direction") or "").split()).rstrip(" .:;")


def _warn_unsupported_direction(request, link, on_log) -> None:
    """An engine that cannot follow a spoken direction says so once -- the
    direction was recorded (with its take), not applied -- never a silent
    drop of what the note asked for."""
    if _direction(request):
        on_log(f"   ⚠️ a spoken direction is not supported by {describe(link)}; recorded, not applied.")


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

def _edge_synthesize(text, voice, audio_path, subs_path=None, *, rate=None, pitch=None):
    """The voiceover core, run to completion. Returns its word-level segments."""
    from clipping import voiceover  # imports edge_tts at ITS module scope, guarded

    return asyncio.run(voiceover._synthesize_async(text, voice, audio_path, subs_path, rate=rate, pitch=pitch))


class EdgeTtsAdapter(_Adapter):
    provider = "edge"

    def probe(self, link, *, credentials, **_):
        if not _installed("edge_tts"):
            return False, f"edge-tts is not installed: {EDGE_INSTALL}"
        return True, "edge-tts installed (free, unofficial; one voice per request)"

    def generate(self, link, request, *, credentials, on_log, transport=None, synthesize=None, probe_duration=None, **_):
        text = _text(request)
        rate, pitch = _rate_pitch(request)
        _warn_unsupported_direction(request, link, on_log)
        if synthesize is None:
            if not _installed("edge_tts"):
                raise ProviderError(f"{describe(link)}: edge-tts is not installed: {EDGE_INSTALL}")
            synthesize = _edge_synthesize
        voice = link.model or request.voice
        out_dir = _out_dir(request)
        name = _name(request, link)
        audio_path = os.path.join(out_dir, f"{name}.mp3")
        subs_path = os.path.join(out_dir, f"{name}.srt")
        # rate/pitch only when given, so a plain call (no story preference) is
        # byte-for-byte the request a fake synthesize() saw before this stage
        # (RC-T2): existing test doubles taking only 4 positional args still work.
        prosody = {}
        if rate is not None:
            prosody["rate"] = rate
        if pitch is not None:
            prosody["pitch"] = pitch
        segments = synthesize(text, voice, audio_path, subs_path, **prosody) or []
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


def _log_tail_guard(link, guard, on_log) -> None:
    """One line when the tail guard cut something, or left whole a line its
    rules would have cut too much of; nothing for a clean line."""
    if guard["trimmed_s"] > 0:
        on_log(f"   🔇 {describe(link)}: {guard['trimmed_s']:.2f} s of static cut from the line's end "
               f"({guard['reason']}, {guard['kept_s']:.2f} s kept)")
    elif guard["reason"] == "suspect":
        on_log(f"   ⚠️ {describe(link)}: the line's end looks odd (noise where the rules would cut more than "
               f"{tts_tail.MAX_CUT_S:g} s or half the line): left whole")


class GeminiTtsAdapter(_Adapter):
    provider = "gemini"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        transport = transport or urllib_transport
        model = GEMINI_TTS_MODELS.get(link.model) or _unknown_model(link, GEMINI_TTS_MODELS)
        text = _text(request)
        voice = request.voice or GEMINI_DEFAULT_VOICE
        _warn_unsupported_rate_pitch(request, link, on_log)
        _warn_unsupported_direction(request, link, on_log)
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
        rate = _pcm_rate(mime)
        # The static after the last word is cut and the edges faded before
        # anything is written: the duration, the sidecar and a cached answer
        # are the cleaned line's.
        pcm, guard = tts_tail.clean(base64.b64decode(audio["data"]), rate)
        _log_tail_guard(link, guard, on_log)
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
                                    provider="gemini", voice=voice, tail_guard=guard)
        return GenResult(provider="gemini", model=link.model, paths=(audio_path, timing_path),
                         meta={"duration_s": duration, "voice": voice, "mime": mime, "source": SOURCE_DURATION,
                               "tail_guard": guard})


# --------------------------------------------------------------- elevenlabs

def _eleven_words(alignment):
    """Words from an ElevenLabs character alignment (``characters`` with
    ``character_start_times_seconds`` / ``character_end_times_seconds``): a
    word is a run of non-space characters, from its first character's start
    to its last character's end. ``[]`` for an alignment that is missing or
    whose three lists disagree in length (never a guessed timing)."""
    if not isinstance(alignment, dict):
        return []
    chars = alignment.get("characters")
    starts = alignment.get("character_start_times_seconds")
    ends = alignment.get("character_end_times_seconds")
    if not (isinstance(chars, list) and isinstance(starts, list) and isinstance(ends, list)):
        return []
    if not (len(chars) == len(starts) == len(ends)):
        return []
    words, current, start, end = [], [], 0.0, 0.0
    for char, begin, finish in zip(chars, starts, ends):
        char = str(char)
        if not char.strip():
            if current:
                words.append({"word": "".join(current), "start": round(float(start), 3), "end": round(float(end), 3)})
                current = []
            continue
        if not current:
            start = begin
        current.append(char)
        end = finish
    if current:
        words.append({"word": "".join(current), "start": round(float(start), 3), "end": round(float(end), 3)})
    return words


def _eleven_language(request):
    """The two-letter language of the request (``extra["language"]``: ``fr``,
    ``fr-FR``), or None."""
    language = str((request.extra or {}).get("language") or "").strip().lower()
    return language.split("-")[0].split("_")[0] or None


def _eleven_refusal(link, exc):
    """*exc* (a 4xx/5xx of ElevenLabs) with what it means named in its detail,
    the status kept (``errors.classify`` and the free-slot release read it):
    a refused key, a spent character quota (ElevenLabs answers it 401 with
    ``quota_exceeded``), a rate or concurrency limit."""
    detail = exc.detail or ""
    lowered = detail.lower()
    status = exc.status_code
    if "quota_exceeded" in lowered or ("quota" in lowered and status in (401, 402, 429)):
        why = (f"the character quota of this ElevenLabs account is used up ({detail}); wait for the reset or "
               "top the plan up")
    elif status in (401, 403):
        why = (f"ELEVENLABS_API_KEY was refused (HTTP {status}); check the key and that it may use "
               f"text-to-speech{': ' + detail if detail else ''}")
    elif status == 429:
        why = (f"ElevenLabs is rate limiting this key (HTTP 429, too many requests or concurrent requests for "
               f"the plan){': ' + detail if detail else ''}")
    else:
        return exc
    return HttpStatusError(status, exc.url, why, error_types=exc.error_types)


class ElevenLabsTtsAdapter(_Adapter):
    provider = "elevenlabs"

    def generate(self, link, request, *, credentials, on_log, transport=None, probe_duration=None, **_):
        transport = transport or urllib_transport
        model = ELEVENLABS_MODELS.get(link.model) or _unknown_model(link, ELEVENLABS_MODELS)
        text = _text(request)
        voice = (request.voice or "").strip()
        if not voice:
            raise ValueError(f"{describe(link)} needs a voice id (GenRequest.voice): none is chosen for it")
        rate, pitch = _rate_pitch(request)
        _warn_unsupported_rate_pitch(request, link, on_log)
        _warn_unsupported_direction(request, link, on_log)
        body = {"text": text, "model_id": model}
        language = _eleven_language(request)
        if language and link.model in ELEVENLABS_LANGUAGE_MODELS:
            body["language_code"] = language
        url = (f"{ELEVENLABS_BASE}/v1/text-to-speech/{urllib.parse.quote(voice, safe='')}/with-timestamps"
               f"?output_format={ELEVENLABS_OUTPUT_FORMAT}")
        try:
            payload = request_json(transport, "POST", url, headers={"xi-api-key": credentials["ELEVENLABS_API_KEY"]},
                                   json_body=body, timeout=DEFAULT_TIMEOUT)
        except HttpStatusError as exc:
            raise _eleven_refusal(link, exc) from exc
        encoded = payload.get("audio_base64") or payload.get("audio_base_64")
        if not encoded:
            raise ProviderError(f"{describe(link)}: the answer carried no audio")
        try:
            audio = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ProviderError(f"{describe(link)}: the audio in the answer is not valid base64") from exc
        if not audio:
            raise ProviderError(f"{describe(link)}: the answer carried no audio")
        out_dir = _out_dir(request)
        name = _name(request, link)
        audio_path = write_output(out_dir, name, audio, "mp3")
        words = _eleven_words(payload.get("alignment") or payload.get("normalized_alignment"))
        measured = (probe_duration or audio_duration)(audio_path)
        if words:
            duration, source = round(max(max(w["end"] for w in words), measured or 0.0), 3), SOURCE_WORDS
        else:
            duration, source = measured, SOURCE_DURATION
            on_log(f"   ⚠️ {describe(link)}: no character alignment came back; timing is the audio duration only")
        timing_path = _write_timing(out_dir, name, duration_s=duration, words=words, source=source,
                                    provider="elevenlabs", voice=voice)
        meta = {"duration_s": duration, "voice": voice, "words": len(words), "source": source}
        not_applied = {key: value for key, value in (("rate", rate), ("pitch", pitch), ("direction", _direction(request)))
                       if value}
        if not_applied:
            meta["not_applied"] = not_applied
        return GenResult(provider="elevenlabs", model=link.model, paths=(audio_path, timing_path), meta=meta)


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
    # A reference recording (plan 23 stage B4: ``GenRequest.references``, the
    # character's own voice) is cloned; without one, the voice is a path to a
    # prompt file, as it was, or none (the engine's default voice).
    references = tuple(getattr(request, "references", None) or ())
    prompt = references[0] if references else (voice if voice and os.path.exists(voice) else None)
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
        _warn_unsupported_rate_pitch(request, link, on_log)
        _warn_unsupported_direction(request, link, on_log)
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
ELEVENLABS_TTS = ElevenLabsTtsAdapter()
LOCAL_TTS = LocalTtsAdapter()

register_adapter(TTS, "edge", EDGE)
register_adapter(TTS, "gemini", GEMINI_TTS)
register_adapter(TTS, "elevenlabs", ELEVENLABS_TTS)
register_adapter(TTS, "local", LOCAL_TTS)
