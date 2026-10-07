"""The voice tools of the MCP server (plan 33 stage 3): ``tts_line``,
``tts_batch`` and ``voice_ref_make``.

Three ways to speak a line, one WAV shape out (mono, 24 kHz, 16-bit PCM,
``outputs/<dest>/<name>.wav``):

* ``gemini`` -- the app's :class:`GeminiTtsAdapter` (``GOOGLE_API_KEY`` from
  ``.env``), prebuilt voices (Kore, Charon, Fenrir, Aoede, Algenib...), the
  tail-noise guard applied before the file is written. A spoken direction is
  read aloud by this model, so ``style`` is recorded, never spoken.
* ``edge`` -- ``edge-tts`` (free, unofficial) fr-FR voices with ``rate`` /
  ``pitch``; MP3 in, ffmpeg makes the WAV.
* ``chatterbox`` -- the ``tts_chatterbox`` workflow on the RunPod worker
  through the job client (a reference WAV of ONE continuous take, 6-30 s:
  A-211); the FLAC that comes back is converted, the job's bill is the cost.

Every line is checked once written: its duration against the speech clock
(a runaway -- more than three times the estimate plus a second -- is kept
aside as ``<name>.runaway.wav`` and refused, A-217), and silence-only files
are refused. Each line is booked in ``outputs/mcp/voice_ledger.json``, which
``cost_ledger`` sums beside the GPU jobs. Stdlib plus the repo's own modules;
``edge_tts`` is imported only when an edge line is asked for.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave
from datetime import datetime, timezone

from clipping.providers import tts as tts_mod, tts_tail
from clipping.providers.generation import GenRequest
from clipping.providers.pricing import estimate as price_estimate
from clipping.providers.registry import Link
from clipping.providers.transport import APIConnectionError, APITimeoutError, HttpStatusError

from .runpod_jobs import JobError, utc_now

PROVIDERS = ("gemini", "edge", "chatterbox")
GEMINI_LINK = Link("gemini", "flash-lite-tts")
RATE = 24000
# The speech clock (clipping/aistory/timing.py, measured on Edge voices) and
# Gemini's overrun, copied rather than imported: the MCP must not pull the
# story package in for two numbers.
SECONDS_PER_CHAR = {"fr": 0.070, "en": 0.065}
MIN_LINE_S = 0.5
SPEECH_OVERRUN = {"gemini": 1.35}
RUNAWAY_FACTOR = 3.0
RUNAWAY_MARGIN_S = 1.0
# A line whose loudest sample is below this (of 32767) said nothing.
SILENCE_PEAK = 300
REF_MIN_S, REF_MAX_S = 6.0, 30.0
REF_TRIM_DB = -45
REF_LOUDNESS = "loudnorm=I=-18:TP=-2"
# The French text a reference is spoken from when the caller gives none:
# neutral, about 45 words, one continuous take.
REF_DEFAULT_TEXT = ("Bonjour. Je vais vous raconter une petite histoire, calmement, sans me presser. "
                    "Ce matin, la ville s'est réveillée sous un ciel clair, et chacun a repris sa route "
                    "comme si de rien n'était. Pourtant, quelque chose avait changé, et nous allions "
                    "bientôt le découvrir ensemble.")
GEMINI_RETRY_STATUSES = (429, 500, 503)
GEMINI_ATTEMPTS = 4
CHATTERBOX_TIMEOUT_S = 600.0
VOICE_LEDGER_REL = os.path.join("mcp", "voice_ledger.json")


class VoiceError(Exception):
    """A line could not be made; the message says why and what to do."""


# -------------------------------------------------------------------- checks

def expected_seconds(text: str, *, language: str = "fr", provider: str | None = None) -> float:
    """What the speech clock says *text* lasts: characters times the
    per-character rate (French by default), at least :data:`MIN_LINE_S`,
    times the provider's overrun (Gemini speaks slower)."""
    rate = SECONDS_PER_CHAR.get(language, SECONDS_PER_CHAR["fr"])
    normalised = " ".join((text or "").split())
    base = max(MIN_LINE_S, len(normalised) * rate)
    return round(base * SPEECH_OVERRUN.get(provider or "", 1.0), 3)


def runaway_limit(text: str, *, language: str = "fr", provider: str | None = None) -> float:
    """The duration past which a line is a runaway (A-217)."""
    return round(RUNAWAY_FACTOR * expected_seconds(text, language=language, provider=provider) + RUNAWAY_MARGIN_S, 3)


def wav_stats(path: str) -> dict:
    """``duration_s``, ``peak`` (0-32767), ``rate``, ``channels`` of a 16-bit
    PCM WAV; ``VoiceError`` for anything else."""
    try:
        with wave.open(path, "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            pcm = w.readframes(frames)
    except (wave.Error, EOFError, OSError) as exc:
        raise VoiceError(f"{path} is not a readable WAV: {exc}") from exc
    if width != 2:
        raise VoiceError(f"{path} is not 16-bit PCM")
    count = len(pcm) // 2
    peak = 0
    if count:
        samples = struct.unpack(f"<{count}h", pcm[:count * 2])
        peak = max(abs(s) for s in samples)
    return {"duration_s": round(frames / rate, 3) if rate else 0.0, "peak": peak, "rate": rate,
            "channels": channels}


def check_line(path: str, text: str, *, language: str, provider: str) -> dict:
    """The checks every written line passes: readable WAV, not silence-only,
    not a runaway. The runaway file is renamed ``<name>.runaway.wav`` and
    the error names it; the answer carries the stats otherwise."""
    stats = wav_stats(path)
    if stats["peak"] < SILENCE_PEAK:
        os.unlink(path)
        raise VoiceError(f"{os.path.basename(path)} came back silent (peak {stats['peak']} of 32767); nothing was "
                         f"kept -- another voice, or another seed for chatterbox")
    limit = runaway_limit(text, language=language, provider=provider)
    expected = expected_seconds(text, language=language, provider=provider)
    if stats["duration_s"] > limit:
        kept = os.path.splitext(path)[0] + ".runaway.wav"
        os.replace(path, kept)
        hint = (" The reference must be ONE continuous take of 6-30 s (never lines glued together): rebuild it with "
                "voice_ref_make, or retry with another seed." if provider == "chatterbox" else "")
        raise VoiceError(f"{provider} ran away: {stats['duration_s']} s for a line expected about {expected} s "
                         f"(limit {limit} s); the sound is kept at {kept} for a listen, no WAV was written.{hint}")
    return {"duration_s": stats["duration_s"], "expected_s": expected, "peak": stats["peak"],
            "sample_rate": stats["rate"], "channels": stats["channels"]}


# -------------------------------------------------------------------- ledger

class VoiceLedger:
    """``outputs/mcp/voice_ledger.json``: one row per line spoken through the
    voice tools (provider, voice, characters, seconds, dollars), summed by
    ``cost_ledger``. Chatterbox lines are GPU jobs already in the job
    journal; their rows carry the job id so nothing is counted twice."""

    def __init__(self, outputs_dir: str):
        self.path = os.path.join(outputs_dir, VOICE_LEDGER_REL)
        self._lock = threading.RLock()

    def all(self) -> list:
        try:
            with open(self.path, encoding="utf-8") as fh:
                rows = json.load(fh).get("lines") or []
        except (OSError, ValueError, AttributeError):
            return []
        return rows if isinstance(rows, list) else []

    def append(self, row: dict) -> dict:
        with self._lock:
            rows = self.all()
            rows.append(row)
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            handle, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix=".voice_ledger-", suffix=".tmp")
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump({"lines": rows}, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        return row

    def summary(self, since: str | None = None) -> dict:
        """Lines, seconds and dollars per provider; ``cost_usd`` sums the
        providers billed here (gemini, edge); chatterbox's dollars are the
        jobs' audio bucket, shown apart."""
        out = {"lines": 0, "seconds": 0.0, "cost_usd": 0.0, "chatterbox_usd_in_audio_jobs": 0.0, "by_provider": {}}
        for row in self.all():
            if since and (row.get("at") or "") < since:
                continue
            provider = row.get("provider") or "?"
            bucket = out["by_provider"].setdefault(provider, {"lines": 0, "seconds": 0.0, "cost_usd": 0.0})
            cost = float(row.get("cost_usd") or 0)
            for b in (out, bucket):
                b["lines"] += 1
                b["seconds"] += float(row.get("duration_s") or 0)
            bucket["cost_usd"] += cost
            if provider == "chatterbox":
                out["chatterbox_usd_in_audio_jobs"] += cost
            else:
                out["cost_usd"] += cost
        for b in [out, *out["by_provider"].values()]:
            b["seconds"] = round(b["seconds"], 1)
            b["cost_usd"] = round(b["cost_usd"], 4)
        out["chatterbox_usd_in_audio_jobs"] = round(out["chatterbox_usd_in_audio_jobs"], 4)
        return out


# ------------------------------------------------------------------- engines

def edge_synthesize(text: str, voice: str, rate: str | None, pitch: str | None, mp3_path: str) -> None:
    """One edge-tts take into *mp3_path*. Run on its own thread with its own
    event loop: a tool may be called from inside the server's loop."""
    try:
        import edge_tts  # noqa: WPS433  (optional dependency, imported here only)
    except ImportError as exc:
        raise VoiceError("edge-tts is not installed in the server's environment (uv sync)") from exc
    import asyncio

    kwargs = {}
    if rate:
        kwargs["rate"] = rate
    if pitch:
        kwargs["pitch"] = pitch

    async def go():
        await edge_tts.Communicate(text, voice, **kwargs).save(mp3_path)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, go()).result()


def ffmpeg_to_wav(src: str, dest: str, *, run=None) -> None:
    """*src* (MP3, FLAC, any audio) to the WAV shape; the app's own command."""
    try:
        tts_mod._convert_to_wav(os.path.basename(src), src, dest, run=run)
    except tts_mod.ProviderError as exc:
        raise VoiceError(str(exc)) from exc


def ffmpeg_reference(raw: str, out: str, *, run=None) -> None:
    """The reference shape (tools/make_voice_refs.finish): silence trimmed at
    both ends, levelled, mono 24 kHz 16-bit, capped at :data:`REF_MAX_S`."""
    if shutil.which("ffmpeg") is None:
        raise VoiceError(tts_mod.FFMPEG_INSTALL)
    trim = f"silenceremove=start_periods=1:start_threshold={REF_TRIM_DB}dB"
    argv = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-i", raw, "-af",
            f"{trim},areverse,{trim},areverse,{REF_LOUDNESS}", "-ar", str(RATE), "-ac", "1", "-sample_fmt", "s16",
            "-t", str(REF_MAX_S), out]
    done = (run or subprocess.run)(argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    if done.returncode != 0:
        raise VoiceError(f"ffmpeg could not shape the reference: {(done.stderr or b'')[-300:]!r}")


class VoiceTools:
    """The engines behind the three tools, with the seams a test replaces:
    *gemini* (an adapter with ``generate``), *edge* (``edge_synthesize``),
    *convert* / *shape* (ffmpeg), *sleep* (the Gemini retry wait), *env*
    (where ``GOOGLE_API_KEY`` is read)."""

    def __init__(self, client, *, outputs_dir: str, env=None, gemini=None, edge=None, convert=None, shape=None,
                 sleep=time.sleep, run=None):
        self.client = client
        self.outputs_dir = outputs_dir
        self.env = env if env is not None else os.environ
        self.gemini = gemini or tts_mod.GEMINI_TTS
        self.edge = edge or edge_synthesize
        self.convert = convert or (lambda src, dest: ffmpeg_to_wav(src, dest, run=run))
        self.shape = shape or (lambda raw, out: ffmpeg_reference(raw, out, run=run))
        self.sleep = sleep
        self.ledger = VoiceLedger(outputs_dir)

    # -- paths

    def dest_dir(self, dest: str) -> str:
        """``outputs/<dest>``, inside the outputs dir only (the unit can write
        nowhere else)."""
        if not dest or not str(dest).strip():
            raise VoiceError("dest is required: a folder under the outputs dir, e.g. 'faille_damour/ep01/voices'")
        root = os.path.realpath(self.outputs_dir)
        candidate = dest if os.path.isabs(dest) else os.path.join(self.outputs_dir, dest)
        real = os.path.realpath(candidate)
        if not (real == root or real.startswith(root + os.sep)):
            raise VoiceError(f"dest {dest!r} resolves outside the outputs dir")
        os.makedirs(real, exist_ok=True)
        return real

    @staticmethod
    def check_name(name: str) -> str:
        name = (name or "").strip()
        if not name or "/" in name or "\\" in name or name.startswith(".") or name != os.path.basename(name):
            raise VoiceError(f"name {name!r} must be a plain file stem (no folder, no dot first)")
        return name

    def reference_path(self, voice: str) -> str:
        """The chatterbox reference *voice* names: a WAV on this server
        (absolute, or relative to the outputs dir or the repo), resolved the
        way comfy_submit resolves audio_path."""
        try:
            path = self.client.resolve_path(voice)
        except JobError as exc:
            raise VoiceError(f"chatterbox needs voice = the reference WAV's path: {exc} (file_upload it, or "
                             f"voice_ref_make one)") from exc
        if not path.lower().endswith(".wav"):
            raise VoiceError(f"the chatterbox reference must be a WAV: {voice}")
        return path

    # -- the three engines, each writing <dest>/<name>.wav and answering its facts

    def gemini_line(self, text: str, voice: str, dest_dir: str, name: str, *, style=None) -> dict:
        key = (self.env.get("GOOGLE_API_KEY") or self.env.get("GEMINI_API_KEY") or "").strip()
        if not key:
            raise VoiceError("gemini needs GOOGLE_API_KEY in .env (the server's environment)")
        request = GenRequest(kind="tts", text=text, voice=voice, out_dir=dest_dir, extra={"name": name})
        logs = []
        last = None
        for attempt in range(1, GEMINI_ATTEMPTS + 1):
            try:
                result = self.gemini.generate(GEMINI_LINK, request, credentials={"GOOGLE_API_KEY": key},
                                              on_log=logs.append)
                break
            except HttpStatusError as exc:
                last = exc
                if exc.status_code not in GEMINI_RETRY_STATUSES or attempt == GEMINI_ATTEMPTS:
                    raise VoiceError(f"gemini answered HTTP {exc.status_code} on {name}: {str(exc)[:200]}") from exc
                self.sleep(15.0 * attempt)
            except (APIConnectionError, APITimeoutError) as exc:
                last = exc
                if attempt == GEMINI_ATTEMPTS:
                    raise VoiceError(f"gemini unreachable on {name}: {exc}") from exc
                self.sleep(15.0 * attempt)
            except tts_mod.ProviderError as exc:
                raise VoiceError(f"gemini could not speak {name}: {exc}") from exc
        else:  # pragma: no cover - the loop breaks or raises
            raise VoiceError(f"gemini failed on {name}: {last}")
        path = os.path.join(dest_dir, f"{name}.wav")
        if os.path.abspath(result.paths[0]) != path:
            os.replace(result.paths[0], path)
        price = price_estimate(GEMINI_LINK, qty=float(result.meta.get("duration_s") or 0))
        return {"path": path, "cost_usd": float(price.est_usd), "paid": bool(price.paid),
                "tail_guard": result.meta.get("tail_guard"), "model": result.model,
                "style_applied": False, "style": style, "attempts": attempt, "sidecar": result.paths[1],
                "notes": [line for line in logs if "not applied" in line][:1]}

    def edge_line(self, text: str, voice: str, dest_dir: str, name: str, *, rate=None, pitch=None,
                  style=None) -> dict:
        for value, pattern in ((rate, tts_mod.RATE_PATTERN), (pitch, tts_mod.PITCH_PATTERN)):
            if value and not pattern.match(value):
                raise VoiceError(f"edge rate/pitch look like '+10%' and '-5Hz', not {value!r}")
        mp3 = os.path.join(dest_dir, f".{name}-edge.mp3")
        path = os.path.join(dest_dir, f"{name}.wav")
        try:
            self.edge(text, voice, rate, pitch, mp3)
            if not os.path.exists(mp3) or os.path.getsize(mp3) == 0:
                raise VoiceError(f"edge-tts wrote nothing for {name} (voice {voice!r} unknown, or no network)")
            self.convert(mp3, path)
        finally:
            try:
                os.unlink(mp3)
            except OSError:
                pass
        guard = tts_tail.clean_wav(path)
        return {"path": path, "cost_usd": 0.0, "paid": False, "tail_guard": guard, "model": voice,
                "style_applied": False, "style": style, "rate": rate, "pitch": pitch}

    def chatterbox_line(self, text: str, voice: str, dest_dir: str, name: str, *, seed=None, exaggeration=None,
                        cfg_weight=None, style=None, timeout_s: float = CHATTERBOX_TIMEOUT_S) -> dict:
        reference = self.reference_path(voice)
        seed = int(seed) if seed is not None else tts_mod.voice_seed(os.path.basename(reference))
        try:
            record = self.client.submit("tts_chatterbox", prompt=text, seed=seed, audio_path=reference,
                                        exaggeration=exaggeration, cfg_weight=cfg_weight, name=name, dest=dest_dir,
                                        note=f"tts_line {name}")
            record = self.client.wait(record["job_id"], timeout_s=timeout_s)
        except JobError as exc:
            raise VoiceError(f"chatterbox job failed on {name}: {exc}") from exc
        if record["state"] != "COMPLETED":
            raise VoiceError(f"chatterbox job {record['job_id']} ended {record['state']} on {name}: "
                             f"{record.get('error') or 'no detail'} (comfy_status {record['job_id']} to follow it)")
        outputs = record.get("outputs") or []
        if not outputs:
            raise VoiceError(f"chatterbox job {record['job_id']} completed without a sound file")
        raw = outputs[0]
        path = os.path.join(dest_dir, f"{name}.wav")
        if os.path.abspath(raw) == path:
            raw = os.path.join(dest_dir, f".{name}-raw.wav")
            os.replace(path, raw)
        self.convert(raw, path)
        try:
            os.unlink(raw)
        except OSError:
            pass
        guard = tts_tail.clean_wav(path)
        return {"path": path, "cost_usd": float(record.get("billed_usd") or 0.0),
                "paid": record.get("billed_usd") is not None, "job_id": record["job_id"],
                "gpu_seconds": record.get("gpu_seconds"), "seed": seed, "reference": reference,
                "tail_guard": guard, "model": "tts_chatterbox", "style_applied": False, "style": style}

    # -- the tools

    def line(self, text: str, provider: str, voice: str, dest: str, name: str, *, style=None, language="fr",
             rate=None, pitch=None, seed=None, exaggeration=None, cfg_weight=None) -> dict:
        provider = (provider or "").strip().lower()
        if provider not in PROVIDERS:
            raise VoiceError(f"provider must be one of {', '.join(PROVIDERS)}, not {provider!r}")
        text = " ".join((text or "").split())
        if not text:
            raise VoiceError("text is required: the words to speak, nothing else (no spoken direction)")
        if not (voice or "").strip():
            raise VoiceError("voice is required (gemini: Kore, Charon...; edge: fr-FR-DeniseNeural...; "
                             "chatterbox: the reference WAV's path)")
        name = self.check_name(name)
        dest_dir = self.dest_dir(dest)
        if provider == "gemini":
            made = self.gemini_line(text, voice, dest_dir, name, style=style)
        elif provider == "edge":
            made = self.edge_line(text, voice, dest_dir, name, rate=rate, pitch=pitch, style=style)
        else:
            made = self.chatterbox_line(text, voice, dest_dir, name, seed=seed, exaggeration=exaggeration,
                                        cfg_weight=cfg_weight, style=style)
        checks = check_line(made["path"], text, language=language, provider=provider)
        row = {"at": utc_now(), "provider": provider, "voice": voice, "name": name, "path": made["path"],
               "chars": len(text), "duration_s": checks["duration_s"], "cost_usd": round(made["cost_usd"], 5),
               "paid": made["paid"], "job_id": made.get("job_id"), "gpu_seconds": made.get("gpu_seconds")}
        self.ledger.append(row)
        return {**made, **checks, "provider": provider, "voice": voice, "name": name, "chars": len(text),
                "cost_usd": round(made["cost_usd"], 5)}

    def batch(self, lines: list, dest: str, *, provider=None, voices=None, redo=None, language="fr") -> dict:
        """*lines*: ``[{id, who?, text, provider?, voice?, style?}]``; the file
        is ``<id>_<who>.wav`` (``<id>.wav`` without a who). A line already on
        disk is skipped unless its id (or name) is in *redo*. Lines are made
        one after the other (a Chatterbox worker warms once). The durations
        map is also written as ``durations.json`` in *dest*."""
        dest_dir = self.dest_dir(dest)
        voices = voices or {}
        redo = set(redo or [])
        out = {"dest": dest_dir, "durations": {}, "made": [], "skipped": [], "errors": {}, "cost_usd": 0.0}
        for i, line in enumerate(lines or []):
            if not isinstance(line, dict):
                out["errors"][f"#{i}"] = "a line is an object {id, who, text, provider?, voice?}"
                continue
            line_id = str(line.get("id") or f"l{i + 1:02d}")
            who = str(line.get("who") or "").strip()
            name = f"{line_id}_{who}" if who else line_id
            path = os.path.join(dest_dir, f"{name}.wav")
            if os.path.exists(path) and line_id not in redo and name not in redo:
                try:
                    out["durations"][name] = wav_stats(path)["duration_s"]
                except VoiceError as exc:
                    out["errors"][name] = str(exc)
                    continue
                out["skipped"].append(name)
                continue
            line_provider = line.get("provider") or provider
            line_voice = line.get("voice") or voices.get(who) or (voices.get("*") if voices else None)
            if not line_provider or not line_voice:
                out["errors"][name] = "no provider/voice: set them on the line, or provider= and voices={who: voice}"
                continue
            try:
                made = self.line(line.get("text") or "", line_provider, line_voice, dest, name,
                                 style=line.get("style"), language=line.get("language") or language,
                                 rate=line.get("rate"), pitch=line.get("pitch"), seed=line.get("seed"),
                                 exaggeration=line.get("exaggeration"), cfg_weight=line.get("cfg_weight"))
            except VoiceError as exc:
                out["errors"][name] = str(exc)
                continue
            out["durations"][name] = made["duration_s"]
            out["cost_usd"] = round(out["cost_usd"] + made["cost_usd"], 5)
            out["made"].append({k: made.get(k) for k in ("name", "path", "provider", "voice", "duration_s",
                                                         "expected_s", "cost_usd", "job_id", "tail_guard")})
        durations_path = os.path.join(dest_dir, "durations.json")
        existing = {}
        try:
            with open(durations_path, encoding="utf-8") as fh:
                existing = json.load(fh) or {}
        except (OSError, ValueError):
            existing = {}
        existing.update(out["durations"])
        with open(durations_path, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, ensure_ascii=False, indent=1, sort_keys=True)
        out["durations_json"] = durations_path
        return out

    def reference(self, name: str, provider: str, voice: str, dest: str, *, rate=None, pitch=None,
                  text=None) -> dict:
        """A 6-30 s reference WAV for Chatterbox cloning: ONE synthetic take
        (edge or gemini), silence trimmed, levelled, capped at 30 s."""
        provider = (provider or "edge").strip().lower()
        if provider not in ("edge", "gemini"):
            raise VoiceError("a reference is made with provider 'edge' or 'gemini' (a synthetic voice)")
        name = self.check_name(name)
        dest_dir = self.dest_dir(dest)
        text = " ".join((text or REF_DEFAULT_TEXT).split())
        raw_name = f".{name}-raw"
        if provider == "edge":
            made = self.edge_line(text, voice, dest_dir, raw_name, rate=rate, pitch=pitch)
        else:
            made = self.gemini_line(text, voice, dest_dir, raw_name)
        raw = made["path"]
        path = os.path.join(dest_dir, f"{name}.wav")
        try:
            self.shape(raw, path)
        finally:
            for leftover in (raw, made.get("sidecar")):
                try:
                    if leftover:
                        os.unlink(leftover)
                except OSError:
                    pass
        stats = wav_stats(path)
        if stats["peak"] < SILENCE_PEAK:
            os.unlink(path)
            raise VoiceError(f"the reference came back silent (voice {voice!r}?); nothing was kept")
        if not REF_MIN_S <= stats["duration_s"] <= REF_MAX_S:
            os.unlink(path)
            raise VoiceError(f"the reference lasts {stats['duration_s']} s, outside {REF_MIN_S:.0f}-{REF_MAX_S:.0f} s: "
                             f"give a longer (or shorter) text, about 40-60 French words")
        return {"path": path, "duration_s": stats["duration_s"], "provider": provider, "voice": voice,
                "rate": rate, "pitch": pitch, "chars": len(text), "take": "one continuous take",
                "sample_rate": stats["rate"], "channels": stats["channels"], "cost_usd": made["cost_usd"],
                "note": "use it as tts_line(provider='chatterbox', voice=<this path>)"}


# ------------------------------------------------------------- registration

def read_lines_file(path: str, client) -> list:
    """The lines of a JSON file on the server: a list, or ``{"lines": [...]}``."""
    try:
        full = client.resolve_path(path)
        with open(full, encoding="utf-8") as fh:
            data = json.load(fh)
    except JobError as exc:
        raise VoiceError(str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise VoiceError(f"{path} is not a readable JSON file: {exc}") from exc
    lines = data.get("lines") if isinstance(data, dict) else data
    if not isinstance(lines, list):
        raise VoiceError(f"{path} must hold a list of lines or {{'lines': [...]}}")
    return lines


def register_voice_tools(mcp, tools: VoiceTools) -> None:
    """Register ``tts_line``, ``tts_batch`` and ``voice_ref_make``."""
    from fastmcp.exceptions import ToolError

    @mcp.tool()
    def tts_line(text: str, provider: str, voice: str, dest: str, name: str, style: str | None = None,
                 language: str = "fr", rate: str | None = None, pitch: str | None = None, seed: int | None = None,
                 exaggeration: float | None = None, cfg_weight: float | None = None) -> dict:
        """COSTS MONEY with provider='chatterbox' (a GPU job, cents; a cold worker loads 3.2 GB first);
        free with 'gemini' (GOOGLE_API_KEY, free tier) and 'edge' (edge-tts). Speak one line into
        outputs/<dest>/<name>.wav (mono 24 kHz 16-bit) and answer path, duration_s, provider, voice,
        cost_usd (booked in the voice ledger that cost_ledger sums). text = the words only, no spoken
        direction (style is recorded, never spoken). voice: gemini = a prebuilt voice (Kore, Charon,
        Fenrir, Aoede, Algenib, Puck, Leda, Zephyr, Orus...); edge = a voice id (fr-FR-DeniseNeural,
        fr-FR-HenriNeural, fr-FR-RemyMultilingualNeural, fr-FR-VivienneMultilingualNeural,
        fr-FR-EloiseNeural) with rate '+10%' / pitch '-5Hz'; chatterbox = the path of a reference WAV
        on this server (ONE continuous take of 6-30 s: file_upload it or voice_ref_make it), with
        seed / exaggeration (0-2) / cfg_weight (0-1). A silent file is refused; a line longer than three
        times its speech estimate is a runaway: kept aside as <name>.runaway.wav and refused."""
        try:
            return tools.line(text, provider, voice, dest, name, style=style, language=language, rate=rate,
                              pitch=pitch, seed=seed, exaggeration=exaggeration, cfg_weight=cfg_weight)
        except VoiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def tts_batch(dest: str, lines: list | None = None, lines_json_path: str | None = None,
                  provider: str | None = None, voices: dict | None = None, redo: list | None = None,
                  language: str = "fr") -> dict:
        """COSTS MONEY when a line uses chatterbox (one GPU job per line, sent one after the other);
        free with gemini / edge. A whole episode's lines in one call: lines = [{id, who, text, provider?,
        voice?, style?}] or lines_json_path = such a list in a JSON file on this server (file_upload it).
        Each line lands as outputs/<dest>/<id>_<who>.wav; provider= and voices={who: voice} fill the
        lines that name none. A line already on disk is skipped unless its id is in redo. Answers
        durations {name: seconds} (also written as <dest>/durations.json), made, skipped, errors per
        line (a runaway or a silent line does not stop the others) and the cost."""
        try:
            rows = list(lines or [])
            if lines_json_path:
                rows.extend(read_lines_file(lines_json_path, tools.client))
            if not rows:
                raise VoiceError("no lines: give lines=[...] or lines_json_path")
            return tools.batch(rows, dest, provider=provider, voices=voices, redo=redo, language=language)
        except VoiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def voice_ref_make(name: str, voice: str, dest: str, provider: str = "edge", rate: str | None = None,
                       pitch: str | None = None, text: str | None = None) -> dict:
        """Free. Build a reference WAV for Chatterbox cloning from a synthetic voice: ONE continuous
        take of a neutral French text (yours, 40-60 words, or the built-in one) by edge (voice id,
        rate, pitch) or gemini (a prebuilt voice), silence trimmed, levelled, mono 24 kHz 16-bit,
        6-30 s, at outputs/<dest>/<name>.wav. Never glue lines together into a reference (that made
        a 40-s runaway). Only synthetic voices or a consented recording (file_upload) are references."""
        try:
            return tools.reference(name, provider, voice, dest, rate=rate, pitch=pitch, text=text)
        except VoiceError as exc:
            raise ToolError(str(exc)) from exc
