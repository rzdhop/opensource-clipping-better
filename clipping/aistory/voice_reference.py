"""A character's voice reference: one short recording, accepted safely and
cloned locally (plan 23 stage B4; DEC-281, which amends DEC-118).

A user may give a character the recording of a voice -- their own, or one
they have the speaker's permission to use -- and pin it as the character's
voice (``{"provider": "chatterbox", "voice_id": "reference"}``): the local
chatterbox engine then speaks every line of the character in that voice
(``voices.synthesize_sample`` / ``voices.synthesize_line`` hand the file to the
adapter as ``GenRequest.references``, and the generation cache's key follows
the file's bytes). Nothing is sent to a provider and nothing is billed.

The recording is untrusted input, so :func:`accept_voice_reference`, on the
pattern of ``uploads.accept_upload``:

- refuses a missing **consent** before reading a byte of the body: the upload
  says "this is my voice, or I have the speaker's permission" or it is
  refused (DEC-281). The character's document records ``consent: true``;
- streams the body in 1 MiB chunks into a hidden temp file inside the
  character's own folder and stops as soon as it passes
  :data:`MAX_UPLOAD_BYTES` (10 MB) -- never more than one byte past the cap,
  and the partial file is removed;
- asks ffprobe what it is: there must be an audio stream, lasting
  :data:`MIN_SECONDS` to :data:`MAX_SECONDS` (5 to 30 s). Anything ffprobe
  cannot read is ``not_audio``; a video file's picture is never kept;
- re-encodes it with ffmpeg to ``voice_reference.wav`` -- mono, 24 kHz, 16-bit
  PCM, no metadata of the original -- at the root of the character's folder
  (``store.MEDIA_NAME_PATTERNS``: the media route can play it back). The
  original file name is never used on disk, nor anywhere else;
- records ``voice_reference: {name, sha256, duration_s, uploaded_at,
  consent: true}`` on the character (``StoryStore.write_entity``). A new upload
  replaces the previous one; if the voice is pinned to the reference, its
  sample goes (it was spoken with the old recording) and the cast approval with
  it.

:func:`delete_voice_reference` is refused while the voice is pinned to the
reference: pin another voice first.

Stdlib only (DEC-012): ffprobe and ffmpeg run through *run* (``subprocess.run``
by default), the seam the tests replace.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
import wave
from datetime import datetime, timezone

KIND = "characters"
MEDIA_NAME = "voice_reference.wav"

# The pin that means "this character's own recording, cloned by chatterbox".
REFERENCE_PROVIDER = "chatterbox"
REFERENCE_VOICE_ID = "reference"

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
MIN_SECONDS = 5.0
MAX_SECONDS = 30.0
SAMPLE_RATE = 24000
_TOOL_TIMEOUT_S = 120

CONSENT_REFUSAL = "Confirm that this is your voice, or that you have the speaker's permission to use it."

_TEMP_PREFIX = ".upload-"

HTTP_STATUS = {
    "no_consent": 400,    # the consent box was not ticked
    "too_large": 413,     # the body is over MAX_UPLOAD_BYTES
    "not_audio": 415,     # ffprobe sees no audio stream, or cannot read it
    "bad_duration": 400,  # shorter than MIN_SECONDS or longer than MAX_SECONDS
    "not_found": 404,     # nothing to remove
    "pinned": 409,        # the character's voice is the reference
    "storage": 409,       # the file, or the folder it is in, is not real (a symlink...)
    "unavailable": 503,   # ffmpeg or ffprobe is not installed
}


class VoiceReferenceError(Exception):
    """A voice reference refused. ``str()`` is a short sentence for the user;
    ``code`` is one of :data:`HTTP_STATUS`, ``http_status`` its status;
    ``reasons`` lists more when there is more than the sentence."""

    def __init__(self, message, *, code, reasons=()):
        super().__init__(message)
        self.code = code
        self.reasons = list(reasons)

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 400)


# Every read-modify-write of a character's voice_reference goes through this
# lock, so an upload and a delete in the same process cannot lose each other.
_ENTRY_LOCK = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unlink_quietly(path) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def _megabytes(size) -> str:
    return f"{size / (1024 * 1024):g} MB"


def is_reference_voice(voice) -> bool:
    """Whether a pinned ``voice`` block is the character's own recording."""
    return bool(voice) and (voice.get("provider"), voice.get("voice_id")) == (REFERENCE_PROVIDER, REFERENCE_VOICE_ID)


def reference_path(stories, story_id, char_id):
    """The real path of the character's ``voice_reference.wav``, or None when
    there is none (a symlink or a non-file in its place counts as none).
    KeyError for an unknown story or character."""
    try:
        return stories.media_path(story_id, KIND, char_id, MEDIA_NAME)
    except KeyError:
        stories.entity_dir(story_id, KIND, char_id)  # KeyError again for an unknown character
        return None


# ------------------------------------------------------------------ reading

def _stream_to(source, fh, max_bytes) -> int:
    """Copy *source* into *fh* in chunks; ``VoiceReferenceError(too_large)`` as
    soon as it passes *max_bytes*. Never asks for more than one byte past it."""
    total = 0
    while True:
        want = min(CHUNK_BYTES, max_bytes + 1 - total)
        chunk = source.read(want)
        if not chunk:
            return total
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError(f"an upload is read as bytes, not {type(chunk).__name__}")
        total += len(chunk)
        if total > max_bytes:
            raise VoiceReferenceError(f"The recording is larger than {_megabytes(max_bytes)}.", code="too_large")
        fh.write(chunk)


def _receive(source, folder, max_bytes) -> str:
    """The body of *source* (a binary file object, or a path) in a hidden temp
    file inside *folder*; the temp file never outlives a failure."""
    handle, tmp = tempfile.mkstemp(dir=folder, prefix=_TEMP_PREFIX, suffix=".part")
    try:
        with os.fdopen(handle, "wb") as fh:
            if isinstance(source, (str, os.PathLike)):
                with open(source, "rb") as src:
                    size = _stream_to(src, fh, max_bytes)
            else:
                size = _stream_to(source, fh, max_bytes)
        if size == 0:
            raise VoiceReferenceError("The file is empty, not a recording.", code="not_audio")
    except BaseException:
        _unlink_quietly(tmp)
        raise
    return tmp


# ----------------------------------------------------------------- the tools

def probe_argv(path) -> list:
    return ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration",
            "-of", "json", os.fspath(path)]


def reencode_argv(src, dest) -> list:
    """The ffmpeg command that makes the stored file: the first audio stream
    only (a video's picture is dropped), at most :data:`MAX_SECONDS`, mono,
    24 kHz, 16-bit PCM in a WAV that carries no metadata of the original (no
    tags, no chapters, no encoder string)."""
    return ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", os.fspath(src), "-map", "0:a:0",
            "-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
            "-t", f"{MAX_SECONDS:g}", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
            "-f", "wav", os.fspath(dest)]


def _run(run, argv):
    runner = run or subprocess.run
    try:
        return runner(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=_TOOL_TIMEOUT_S)
    except FileNotFoundError:
        raise VoiceReferenceError(f"Voice references are not available: {argv[0]} is not installed on this "
                                  "server.", code="unavailable") from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise VoiceReferenceError(f"The recording could not be checked ({argv[0]}: {exc}).",
                                  code="not_audio") from None


def probe_audio(path, *, run=None) -> float:
    """The seconds of audio in *path*, by ffprobe. ``not_audio`` when ffprobe
    cannot read it or finds no audio stream; ``unavailable`` without ffprobe."""
    result = _run(run, probe_argv(path))
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        data = {}
    streams = data.get("streams") if isinstance(data, dict) else None
    if result.returncode != 0 or not isinstance(streams, list):
        raise VoiceReferenceError("The file is not a recording ffprobe can read (send a WAV, MP3, M4A, OGG or "
                                  "FLAC file).", code="not_audio")
    if not any(isinstance(s, dict) and s.get("codec_type") == "audio" for s in streams):
        raise VoiceReferenceError("The file has no audio track.", code="not_audio")
    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return round(duration, 3)


def check_duration(duration) -> None:
    """``bad_duration`` unless *duration* is :data:`MIN_SECONDS` to
    :data:`MAX_SECONDS` (both included)."""
    if not (MIN_SECONDS <= duration <= MAX_SECONDS):
        raise VoiceReferenceError(
            f"The recording lasts {duration:g} s; a voice reference is {MIN_SECONDS:g} to {MAX_SECONDS:g} seconds "
            "of one voice, speaking clearly.", code="bad_duration")


def _wav_seconds(path) -> float:
    try:
        with wave.open(os.fspath(path), "rb") as wav:
            return round(wav.getnframes() / float(wav.getframerate() or 1), 3)
    except (OSError, EOFError, wave.Error):
        raise VoiceReferenceError("The recording could not be converted to a WAV file.", code="not_audio") from None


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _reencode(src, dest, *, run=None) -> None:
    result = _run(run, reencode_argv(src, dest))
    if result.returncode != 0 or not os.path.isfile(dest) or os.path.getsize(dest) == 0:
        raise VoiceReferenceError("The recording could not be converted to a WAV file; it may be damaged.",
                                  code="not_audio")


# ------------------------------------------------------------------- accept

def accept_voice_reference(stories, story_id, char_id, source, *, consent, now, run=None,
                           max_bytes=MAX_UPLOAD_BYTES) -> dict:
    """Accept a voice reference for the character, replacing the previous
    one; returns the new ``voice_reference`` entry ``{name, sha256,
    duration_s, uploaded_at, consent: true}``.

    *consent* must be ``True`` (the box was ticked): checked first, before the
    body is read. *source* is a binary file object or a path. KeyError for an
    unknown story or character; :class:`VoiceReferenceError` for a refusal
    (``no_consent``, ``too_large``, ``not_audio``, ``bad_duration``,
    ``storage``, ``unavailable``). A refusal leaves the character, and its
    folder, as they were.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError(f"max_bytes must be a positive number of bytes, not {max_bytes!r}")
    if consent is not True:
        raise VoiceReferenceError(CONSENT_REFUSAL, code="no_consent")

    stories.read_entity(story_id, KIND, char_id)  # KeyError before anything is read
    try:
        folder = stories.entity_dir(story_id, KIND, char_id)
    except KeyError:
        raise VoiceReferenceError("The character's folder is not a real folder (a symlink is never followed).",
                                  code="storage") from None

    received = cleaned = None
    try:
        received = _receive(source, folder, max_bytes)
        check_duration(probe_audio(received, run=run))
        handle, cleaned = tempfile.mkstemp(dir=folder, prefix=_TEMP_PREFIX, suffix=".wav")
        os.close(handle)
        _reencode(received, cleaned, run=run)
        duration = _wav_seconds(cleaned)
        check_duration(duration)
        entry = {"name": MEDIA_NAME, "sha256": _sha256(cleaned), "duration_s": duration,
                 "uploaded_at": now, "consent": True}
        with _ENTRY_LOCK:
            try:
                stories.write_media(story_id, KIND, char_id, MEDIA_NAME, cleaned)
            except (KeyError, ValueError) as exc:
                raise VoiceReferenceError(f"The recording could not be stored ({exc}).", code="storage") from None
            current = stories.read_entity(story_id, KIND, char_id)
            current["voice_reference"] = dict(entry)
            if is_reference_voice(current.get("voice")):
                # Spoken with the old recording: its sample is stale, and the
                # cast must be approved again (``regenerate`` clears both).
                current["approved_at"] = None
                _drop_sample(folder)
            stories.write_entity(story_id, KIND, current, now=now)
        return dict(entry)
    finally:
        _unlink_quietly(received)
        _unlink_quietly(cleaned)


def _drop_sample(folder) -> None:
    """The character's voice sample files (regular files only; a symlink is
    left alone, never followed)."""
    for ext in ("mp3", "wav"):
        path = os.path.join(folder, f"voice_sample.{ext}")
        if os.path.isfile(path) and not os.path.islink(path):
            _unlink_quietly(path)


# ------------------------------------------------------------------- delete

def delete_voice_reference(stories, story_id, char_id, *, now) -> dict:
    """Remove the character's voice reference: its entry, then its file.

    ``pinned`` while the character's voice is the reference: pin another voice
    first. ``not_found`` when it has neither the entry nor the file; ``storage``
    for a symlink or a non-file in the file's place (kept, never followed).
    KeyError for an unknown story or character. Returns ``{"name",
    "entry_removed", "file_removed"}``.
    """
    with _ENTRY_LOCK:
        character = stories.read_entity(story_id, KIND, char_id)
        if is_reference_voice(character.get("voice")):
            raise VoiceReferenceError(
                f"{character['name']}'s voice is this recording: pick another voice first, then remove it.",
                code="pinned")
        folder = stories.entity_dir(story_id, KIND, char_id)
        path = os.path.join(folder, MEDIA_NAME)
        if os.path.lexists(path) and (os.path.islink(path) or not os.path.isfile(path)):
            raise VoiceReferenceError(f"{MEDIA_NAME} is not a regular file (a symlink is never followed); "
                                      "nothing was removed.", code="storage")
        listed = bool(character.get("voice_reference"))
        present = os.path.lexists(path)
        if not listed and not present:
            raise VoiceReferenceError(f"{character['name']} has no voice reference.", code="not_found")
        if listed:
            del character["voice_reference"]
            stories.write_entity(story_id, KIND, character, now=now)
    file_removed = False
    if present:
        try:
            os.unlink(path)
            file_removed = True
        except FileNotFoundError:
            pass
    return {"name": MEDIA_NAME, "entry_removed": listed, "file_removed": file_removed}

