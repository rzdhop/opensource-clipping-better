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
    """``assets.clip.native_speech``: the take as the storyboard keeps it.
    An exchange's take (plan 27 stage 4: it carries ``lines``) adds the
    per-line ``{line_id, speaker, matched, heard, start_s, end_s}``; its
    ``line_id`` is the first line's, ``matched`` the minimum of the lines'.
    A one-line take keeps its shape exactly."""
    entry = {"state": take["state"], "matched": take.get("matched"), "heard": take.get("heard"),
             "start_s": take.get("start_s"), "end_s": take.get("end_s"), "aligned_by": take.get("aligned_by"),
             "clip_sha256": clip_sha256, "clip_real_s": round(float(clip_real_s), 3), "line_id": line_id,
             "checked_at": now}
    if take.get("lines"):
        entry["lines"] = [{key: row.get(key) for key in ("line_id", "speaker", "matched", "heard", "start_s",
                                                         "end_s")} for row in take["lines"]]
    if reason:
        entry["reason"] = reason[:1000]
    return entry


def is_current(take, *, clip_sha256, line_id, line_ids=None) -> bool:
    """Whether *take* (``assets.clip.native_speech``) is the take of the clip
    whose sha256 is *clip_sha256*, for line *line_id* (an exchange's first;
    *line_ids* all of them, in order: a take of another set of lines is not
    this one's)."""
    if not (isinstance(take, dict) and take.get("clip_sha256") == clip_sha256 and take.get("line_id") == line_id
            and take.get("state") in native_speech.TAKE_STATES):
        return False
    if line_ids is not None and len(line_ids) > 1:
        return [row.get("line_id") for row in take.get("lines") or ()] == list(line_ids)
    return True


def pinned_voice(ec, speaker) -> str | None:
    """The speaker's pinned voice label: what a measured line records, so
    ``voice_lines.is_measured`` reads the take as current while the
    speaker's voice is unchanged (the voice line the clip was asked with is
    built from the same character)."""
    from . import voice_lines

    return voices.voice_label(voice_lines.speaker_voice(ec, speaker))


def _percent(value) -> int:
    return round((value or 0) * 100)


def summary_line(shot_id, take) -> str:
    """The feed's line for a take. An exchange's names every line the clip
    does not speak as written, with what was heard in its place."""
    state = take["state"]
    rows = take.get("lines") or []
    if state == native_speech.TAKE_OK:
        if rows:
            return (f"🗣 Shot {shot_id}: its clip speaks its {len(rows)} lines in turn "
                    f"({native_speech.exchange_summary(take)}, {take['start_s']:.2f}-{take['end_s']:.2f} s, by "
                    f"{take.get('aligned_by') or 'stt'})")
        return (f"🗣 Shot {shot_id}: its clip speaks its line ({_percent(take.get('matched'))} % of the "
                f"words heard, {take['start_s']:.2f}-{take['end_s']:.2f} s, by {take.get('aligned_by') or 'stt'})")
    if state == native_speech.TAKE_STT_UNAVAILABLE:
        what = f"its {len(rows)} lines" if rows else "its clip's speech"
        return (f"🗣 Shot {shot_id}: {what} could not be checked ({take.get('reason') or 'no STT link'}): "
                "the subtitles are split evenly over the planned window (approximate)")
    if rows:
        missing = native_speech.missing_lines(take) or rows
        names = "; ".join(line_heard(row) for row in missing)
        if state == native_speech.TAKE_NO_SPEECH:
            return (f"⚠️ Shot {shot_id}: its clip speaks no word of its {len(rows)} lines (nothing heard): flagged "
                    "for a retake")
        return (f"⚠️ Shot {shot_id}: its clip does not speak {_and_lines(missing)} as written "
                f"({native_speech.exchange_summary(take)}: {names}): flagged for a retake")
    if state == native_speech.TAKE_NO_SPEECH:
        return f"⚠️ Shot {shot_id}: its clip speaks no word of its line (nothing heard): flagged for a retake"
    return (f"⚠️ Shot {shot_id}: its clip does not speak its line as written ({_percent(take.get('matched'))} "
            f"% of the words heard: \"{(take.get('heard') or '')[:120]}\"): flagged for a retake")


def line_heard(row) -> str:
    """"l09 heard as 'bonjour'" (or "heard as nothing") for an exchange's line record."""
    heard = (row.get("heard") or "").strip()
    return f"{row['line_id']} heard as '{heard[:120]}'" if heard else f"{row['line_id']} heard as nothing"


def _and_lines(rows) -> str:
    ids = [row["line_id"] for row in rows]
    return f"line {ids[0]}" if len(ids) == 1 else "lines " + ", ".join(ids[:-1]) + f" and {ids[-1]}"


def retake_reason(take) -> str | None:
    """What an exchange's mismatch says to the retake (the record's
    ``reason``, the brief's take): each line below the threshold named with
    what the clip said in its place; None for a one-line take or a take that
    speaks every line."""
    rows = native_speech.missing_lines(take)
    return "; ".join(line_heard(row) for row in rows) or None
