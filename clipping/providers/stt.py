"""Hosted speech-to-text with word-level timestamps.

Replaces in-process Whisper for the machines this tool actually runs on. On the
ARM host it was written for, `faster-whisper` with `large-v3` on CPU was
measured at **4.6x realtime** — 94 minutes for a 20-minute video — and that is
the whole reason the local-first refactor happened. Groq transcribes the same
audio in about a minute, free, with word timestamps.

Both supported providers return OpenAI-shaped `verbose_json`, so one parser
covers them:

* **Groq** `whisper-large-v3-turbo` — 25 MB per file on the free tier, and
  `language` improves accuracy when known. That cap is the binding one:
  measured on the real 20-minute video this was built for, 16 kHz mono FLAC
  came to **39.3 MB**, half again over it, so `audio.plan_chunks` splits it.
* **Mistral** `voxtral-mini-latest` — up to 3 hours per request, but
  `timestamp_granularities` and `language` are **mutually exclusive**, so its
  link sends only the former. Word timings matter more than a language hint.

The output is fed through `transcript._chunk_into_segments`, the same function
the VTT and JSON3 parsers use, so the `data_segmen` contract (RC-1) holds by
construction rather than by a second implementation agreeing with the first.

Uses urllib rather than the openai SDK: this is one multipart POST, and keeping
it stdlib means the module imports in the pytest-only CI environment.
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid

from .. import transcript as transcript_mod
from . import audio as audio_mod
from .registry import PROVIDERS

DEFAULT_STT_CHAIN = "groq/whisper-large-v3-turbo,mistral/voxtral-mini-latest"
LOCAL_LINK = "local/faster-whisper"

# Providers that reject `language` alongside word timestamps.
NO_LANGUAGE_WITH_WORDS = {"mistral"}

REQUEST_TIMEOUT = 300


class SttError(RuntimeError):
    """Transcription failed on every provider in the chain."""


def transcribe(
    video_path,
    *,
    chain,
    keys,
    max_words_per_subtitle=5,
    language=None,
    on_log=print,
    post=None,
    cancel=None,
):
    """``(transkrip_lengkap, data_segmen, language)`` for *video_path*.

    Each link is tried in order; a link with no key is skipped with a printed
    reason, exactly as the LLM chain does. *cancel* stops it before the next
    link or chunk; a chunk already uploading finishes or times out.
    """
    audio_path = None
    chunk_paths = []
    try:
        on_log("   🎧 Extracting audio (16 kHz mono FLAC)...")
        audio_path = audio_mod.extract(video_path)
        size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        bounds = audio_mod.plan_chunks(audio_path)
        on_log(
            f"   🎧 {size_mb:.1f} MB of audio in {len(bounds)} chunk(s)."
            + ("" if len(bounds) == 1 else " Boundaries are snapped to silence.")
        )

        failures = []
        for link in chain:
            if cancel is not None:
                cancel.check()
            label = f"{link.provider}/{link.model}"
            key = keys.get(link.provider) or ""
            if not key:
                env = PROVIDERS[link.provider].env_key
                on_log(f"   ⏭ Skipping {label}: no API key ({env} is not set).")
                failures.append((label, "no API key"))
                continue

            try:
                return _transcribe_with(
                    link, key, audio_path, bounds, chunk_paths,
                    max_words_per_subtitle=max_words_per_subtitle,
                    language=language, on_log=on_log, post=post, cancel=cancel,
                )
            except Exception as exc:  # noqa: BLE001 - recorded, then the next link
                reason = f"{type(exc).__name__}: {exc}"
                on_log(f"   ⚠️ {label} failed | {reason}")
                failures.append((label, reason))

        detail = "\n".join(f"  {name}: {why}" for name, why in failures)
        raise SttError(f"No transcription provider succeeded:\n{detail}")
    finally:
        for path in chunk_paths:
            _unlink(path)
        if audio_path:
            _unlink(audio_path)


def _transcribe_with(
    link, key, audio_path, bounds, chunk_paths, *,
    max_words_per_subtitle, language, on_log, post, cancel=None,
):
    sender = post or _post_multipart
    provider = PROVIDERS[link.provider]
    url = provider.base_url.rstrip("/") + "/audio/transcriptions"

    all_words = []
    reported = language

    for index, (start, end) in enumerate(bounds, start=1):
        if cancel is not None:
            cancel.check()
        if len(bounds) == 1:
            chunk_path = audio_path
        else:
            chunk_path = audio_mod.cut(audio_path, start, end)
            chunk_paths.append(chunk_path)

        on_log(
            f"   🗣 {link.provider}/{link.model}: chunk {index}/{len(bounds)} "
            f"({start:.0f}s → {end:.0f}s)..."
        )

        fields = {
            "model": link.model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
        }
        if language and link.provider not in NO_LANGUAGE_WITH_WORDS:
            fields["language"] = language

        payload = sender(url, key, chunk_path, fields)
        reported = reported or payload.get("language")
        words = _words_from(payload, offset=start)
        all_words = _stitch(all_words, words)

    if not all_words:
        raise SttError("The transcription came back with no words.")

    transcript_mod._deoverlap(all_words)
    transkrip, segmen = transcript_mod._chunk_into_segments(
        all_words, max_words_per_subtitle
    )
    on_log(
        f"   ✅ {len(segmen)} segments, {len(all_words)} words "
        f"({segmen[0]['start']:.1f}s → {segmen[-1]['end']:.1f}s)"
    )
    return transkrip, segmen, (reported or "").lower() or None


def _words_from(payload, *, offset=0.0):
    """Flatten a verbose_json body into the word contract, rebased by *offset*.

    Falls back to segment-level timings when a provider returns no word array,
    spreading each segment's words evenly — the same approximation the VTT
    parser makes, and better than failing the run.
    """
    words = []
    for raw in payload.get("words") or []:
        text = str(raw.get("word") or raw.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(raw["start"]) + offset
            end = float(raw["end"]) + offset
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            end = start + 0.05
        words.append({"word": text, "start": start, "end": end})

    if words:
        return words

    for segment in payload.get("segments") or []:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(segment["start"]) + offset
            end = float(segment["end"]) + offset
        except (KeyError, TypeError, ValueError):
            continue
        words.extend(transcript_mod._expand_run_to_words(text, start, end))
    return words


def _stitch(existing, incoming, *, tolerance=0.4):
    """Append *incoming*, dropping the overlap duplicated by the previous chunk.

    Chunks overlap by a few seconds so no word is cut in half; those seconds are
    transcribed twice and one copy has to go. A word is a duplicate when it sits
    within *tolerance* of the tail's last word and reads the same.
    """
    if not existing:
        return list(incoming)
    if not incoming:
        return existing

    last_end = existing[-1]["end"]
    tail = {
        (w["word"].lower(), round(w["start"], 1))
        for w in existing[-40:]
    }

    out = list(existing)
    for word in incoming:
        if word["start"] < last_end - tolerance:
            continue  # squarely inside what the previous chunk already covered
        if (word["word"].lower(), round(word["start"], 1)) in tail:
            continue
        if word["start"] < last_end:
            # Nudge forward so the contract's strict ordering holds.
            shift = last_end - word["start"]
            word = {
                "word": word["word"],
                "start": word["start"] + shift,
                "end": word["end"] + shift,
            }
        out.append(word)
        last_end = max(last_end, word["end"])
    return out


def _post_multipart(url, api_key, file_path, fields):
    """One multipart/form-data POST. Returns the parsed JSON body."""
    boundary = uuid.uuid4().hex
    content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    body = bytearray()
    for name, value in fields.items():
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    with open(file_path, "rb") as handle:
        payload = handle.read()
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{os.path.basename(file_path)}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    body += payload
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")

    request = urllib.request.Request(
        url,
        data=bytes(body),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SttError(f"HTTP {exc.code} from {url}: {detail}") from exc


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def uses_hosted(chain):
    """Whether *chain*'s first link is a hosted provider rather than local."""
    return bool(chain) and chain[0].provider in PROVIDERS


def parse_stt_chain(spec):
    """Parse an STT chain, tolerating the ``local/...`` pseudo-provider.

    ``local/faster-whisper`` is not a hosted endpoint, so it is not in the
    registry; it is returned as a marker the caller interprets.
    """
    from .registry import Link, parse_spec

    out = []
    for part in str(spec or "").split(","):
        text = part.strip()
        if not text:
            continue
        if text.startswith("local/") or text == "local":
            out.append(Link("local", text.partition("/")[2] or "faster-whisper"))
            continue
        out.append(parse_spec(text))
    return out
