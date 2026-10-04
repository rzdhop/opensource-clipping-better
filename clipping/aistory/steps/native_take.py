"""The native take of a speaking clip (plan 22, stage 4): what the clip's own
sound says of its line, and the line's audio cut from it.

A native-speech story (``media_policy.native_speech``) never voices a
character line with TTS: the line's clip speaks it. Once a speaking clip is
kept (``assets._Assets.apply_clip``, or a clip kept from an earlier run whose
take is not current), the assets step takes it -- for free:

1. the clip's sound is extracted (:func:`extract_audio`, ffmpeg) and its
   real length read (:func:`clip_seconds`, ffprobe);
2. the default transcriber (``assets.default_transcriber``: STT_CHAIN's
   keyed hosted links) transcribes it in the story's language;
3. ``native_speech.evaluate_take`` aligns the words heard against the line
   (``wordtiming.align``) and says ``ok``, ``mismatch``, ``no_speech`` or
   ``stt_unavailable``;
4. a take with speech becomes the line's audio: the clip's sound from the
   first word to the last as ``assets/voice/line_NN.wav`` with its
   ``line_timing_v1`` sidecar (``words_source: alignment``, or no words: the
   even split, approximate), the line's ``timing`` measured -- so
   ``voice_lines.is_measured``, the render's line precondition and the
   subtitles read it as they read any line;
5. the take is recorded on the clip (``assets.clip.native_speech``), keyed
   by the clip's sha256: a new clip is taken again, a kept one never.

The shot's length then follows the clip's real length
(``native_speech.shot_seconds``: a speaking clip running more than a second
past its last word is cut 0.3 s after it), so an 8 s clip the human brought
for a four-word line does not drag.

Every process goes through *run* (``subprocess.run`` when None), so a test
can hand in its own. Stdlib only (DEC-012).
"""

from __future__ import annotations

import json
import os
import subprocess

from clipping.providers import tts

from .. import native_speech, timing, voices, wordtiming

# The line's audio as the TTS lines are kept: 24 kHz mono 16-bit WAV.
AUDIO_RATE = 24000
PROVIDER = "native"


class TakeError(Exception):
    """ffmpeg or ffprobe could not read or cut the clip; the message says why."""


def _run(run, argv):
    runner = run or subprocess.run
    try:
        return runner(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    except OSError as exc:
        raise TakeError(f"{argv[0]} could not run ({exc})") from None


def clip_seconds(path, *, run=None) -> float:
    """The clip's real length in seconds (ffprobe's container duration)."""
    result = _run(run, ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                        "default=noprint_wrappers=1:nokey=1", os.fspath(path)])
    try:
        seconds = float((result.stdout or "").strip().splitlines()[0])
    except (IndexError, ValueError):
        raise TakeError(f"ffprobe read no length from the clip ({(result.stderr or '').strip()[-200:]})") from None
    if result.returncode != 0 or seconds <= 0:
        raise TakeError(f"ffprobe read no length from the clip ({(result.stderr or '').strip()[-200:]})")
    return round(seconds, 3)


def extract_audio(path, out_path, *, start_s=None, end_s=None, run=None) -> str:
    """The clip's sound (from *start_s* to *end_s* when given) as a 24 kHz
    mono 16-bit WAV at *out_path*, written by one deterministic ffmpeg argv."""
    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y"]
    if start_s is not None:
        argv += ["-ss", f"{float(start_s):.3f}"]
    argv += ["-i", os.fspath(path)]
    if end_s is not None:
        argv += ["-t", f"{max(0.05, float(end_s) - float(start_s or 0.0)):.3f}"]
    argv += ["-vn", "-ac", "1", "-ar", str(AUDIO_RATE), "-c:a", "pcm_s16le", "-map_metadata", "-1",
             "-fflags", "+bitexact", "-flags:a", "+bitexact", os.fspath(out_path)]
    result = _run(run, argv)
    if result.returncode != 0 or not os.path.isfile(out_path):
        raise TakeError(f"ffmpeg could not extract the clip's sound ({(result.stderr or '').strip()[-300:]})")
    return os.fspath(out_path)


def sidecar(take, *, link, duration_s) -> dict:
    """The ``line_timing_v1`` sidecar of a take's line audio: the aligned
    words (``words_source: alignment``), or none (the even split) for a take
    the STT could not time."""
    words = take.get("words") or []
    data = {"$schema": tts.TIMING_SCHEMA, "provider": PROVIDER, "voice": link, "duration_s": round(duration_s, 3),
            "source": tts.SOURCE_DURATION, "words": [dict(word) for word in words]}
    if words:
        data[wordtiming.SIDECAR_SOURCE_KEY] = wordtiming.ALIGNMENT
        data[wordtiming.SIDECAR_ALIGNED_BY_KEY] = str(take.get("aligned_by") or "stt")[:120]
    return data


def write_json(path, data) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def line_timing(line, *, duration_s, voice, audio_rel) -> dict:
    """The line's ``timing`` once its take is its audio: measured (so
    ``voice_lines.is_measured`` holds while the speaker keeps its pinned
    voice), the take's length, the clip's sound as its file."""
    return {"source": tts.SOURCE_DURATION, "duration_s": round(float(duration_s), 3),
            "text_hash": timing.text_hash(line["text"]), "voice": voice, "audio": audio_rel}


def record(take, *, clip_sha256, clip_real_s, line_id, now, reason=None) -> dict:
    """``assets.clip.native_speech``: the take as the storyboard keeps it."""
    entry = {"state": take["state"], "matched": take.get("matched"), "heard": take.get("heard"),
             "start_s": take.get("start_s"), "end_s": take.get("end_s"), "aligned_by": take.get("aligned_by"),
             "clip_sha256": clip_sha256, "clip_real_s": round(float(clip_real_s), 3), "line_id": line_id,
             "checked_at": now}
    if reason:
        entry["reason"] = reason[:1000]
    return entry


def is_current(take, *, clip_sha256, line_id) -> bool:
    """Whether *take* (``assets.clip.native_speech``) is the take of the clip
    whose sha256 is *clip_sha256*, for line *line_id*."""
    return (isinstance(take, dict) and take.get("clip_sha256") == clip_sha256 and take.get("line_id") == line_id
            and take.get("state") in native_speech.TAKE_STATES)


def pinned_voice(ec, speaker) -> str | None:
    """The speaker's pinned voice label: what a measured line records, so
    ``voice_lines.is_measured`` reads the take as current while the
    speaker's voice is unchanged (the voice line the clip was asked with is
    built from the same character)."""
    from . import voice_lines

    return voices.voice_label(voice_lines.speaker_voice(ec, speaker))


def summary_line(shot_id, take) -> str:
    """The feed's line for a take."""
    state = take["state"]
    if state == native_speech.TAKE_OK:
        return (f"🗣 Shot {shot_id}: its clip speaks its line ({round((take.get('matched') or 0) * 100)} % of the "
                f"words heard, {take['start_s']:.2f}-{take['end_s']:.2f} s, by {take.get('aligned_by') or 'stt'})")
    if state == native_speech.TAKE_STT_UNAVAILABLE:
        return (f"🗣 Shot {shot_id}: its clip's speech could not be checked ({take.get('reason') or 'no STT link'}): "
                "the subtitles are split evenly over the planned window (approximate)")
    if state == native_speech.TAKE_NO_SPEECH:
        return f"⚠️ Shot {shot_id}: its clip speaks no word of its line (nothing heard): flagged for a retake"
    return (f"⚠️ Shot {shot_id}: its clip does not speak its line as written ({round((take.get('matched') or 0) * 100)} "
            f"% of the words heard: \"{(take.get('heard') or '')[:120]}\"): flagged for a retake")
