"""Locked voices on a clip (DEC-323): every line of a checked clip re-voiced as its speaker's locked voice.

The clip is cut in the middle of the pauses between its lines (:func:`showrunner.verify.speaker_parts`, from
the clip check's timings), each part converted by ``vc_chatterbox`` to its own speaker's ``voice_ref.wav``,
the parts rejoined at their exact lengths (:func:`showrunner.verify.join_parts`) and laid back under the
untouched picture (:func:`remux`). Timing is kept to the frame, so the lips stay in sync.

Used by ``run_stage0 vc`` and by the MCP server's ``vc_clip`` / ``vc_fetch`` (through :mod:`showrunner.jobs`).
"""

from __future__ import annotations

import os

from showrunner import verify

VC_TEMPLATE = "vc_chatterbox"


def remux(clip: str, audio: str, dest: str) -> str:
    """The picture of *clip* with *audio* as its only sound track (video stream copied, never re-timed)."""
    out = verify._run(["ffmpeg", "-hide_banner", "-y", "-i", clip, "-i", audio, "-map", "0:v:0", "-map", "1:a:0",
                       "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", dest])
    if out.returncode:
        raise RuntimeError(f"remux failed: {out.stderr.strip()[-300:]}")
    return dest


def parts_plan(verdict: dict, duration_s: float) -> list:
    """``[{"k", "speaker", "start_s", "end_s", "seconds"}]``: one part per line, tiling the clip."""
    return [{"k": k, "speaker": who, "start_s": a, "end_s": b, "seconds": round(b - a, 3)}
            for k, (who, a, b) in enumerate(verify.speaker_parts(verdict, duration_s))]


def cut_part(clip: str, part: dict, dest: str) -> str:
    """The audio of one part of *clip* as a mono 24 kHz wav (the converter's input)."""
    return verify.extract_audio(clip, dest, start_s=part["start_s"], max_s=part["seconds"])


def rebuild(clip: str, converted: list, dest_audio: str, dest_clip: str) -> str:
    """*converted*: ``[(audio path, seconds)]`` in order -> the clip with that sound. Returns *dest_clip*."""
    verify.join_parts(converted, dest_audio)
    return remux(clip, dest_audio, dest_clip)


# ------------------------------------------------------------------ the locked voice of a character

# Stage 0 cast every voice from a path-(a) take Rida liked: its line cut out, the silence trimmed
# (Camille: 2.4 s of speech in 4.0 s; Paloma 6.5 s, Marie-Jeanne 6.4 s, Rida 4.7 s), and the converter
# heard it as the target. Same rule here, from the clip check's timings instead of a hand-picked start.
REF_MIN_SPEECH_S, REF_MAX_S = 2.0, 10.0
REF_PAD_BEFORE_S, REF_PAD_AFTER_S = 0.15, 0.3


class VoiceRefError(ValueError):
    pass


def reference_plan(verdict: dict, duration_s: float, speaker: str) -> list:
    """``[(start_s, end_s)]`` of *speaker*'s lines in a checked take, each padded a little but never past the
    middle of the pause next to it (:func:`showrunner.verify.speaker_parts`), so no other voice gets in. Kept
    in order until :data:`REF_MAX_S`; refused under :data:`REF_MIN_SPEECH_S` of speech."""
    try:
        parts = verify.speaker_parts(verdict, duration_s)
    except ValueError as exc:
        raise VoiceRefError(str(exc)) from exc
    lines = verdict["lines"]
    if speaker not in {r["speaker"] for r in lines}:
        raise VoiceRefError(f"{speaker} has no line in this take ({', '.join(sorted({r['speaker'] for r in lines}))})")
    out, total, speech = [], 0.0, 0.0
    for (who, lo, hi), r in zip(parts, lines):
        if who != speaker:
            continue
        a = max(lo, r["start_s"] - REF_PAD_BEFORE_S)
        b = min(hi, r["end_s"] + REF_PAD_AFTER_S)
        if total + (b - a) > REF_MAX_S:
            if not out:                           # one long line: keep its first REF_MAX_S
                out.append((round(a, 3), round(a + REF_MAX_S, 3)))
                speech += min(REF_MAX_S, r["end_s"] - r["start_s"])
            break
        out.append((round(a, 3), round(b, 3)))
        total += b - a
        speech += r["end_s"] - r["start_s"]
    if speech < REF_MIN_SPEECH_S:
        raise VoiceRefError(f"{speaker} speaks {speech:.1f} s in this take; a voice reference needs at least "
                            f"{REF_MIN_SPEECH_S:.0f} s (pick a take with a longer line)")
    return out


def reference_from_take(clip: str, verdict: dict, speaker: str, dest: str) -> dict:
    """Write *speaker*'s voice from a checked take to *dest* (mono 24 kHz wav, what Chatterbox hears as the
    target). Returns where it was cut from and how loud it is; refuses a silent result."""
    duration = verdict.get("duration_s") or verify.probe(clip)["duration_s"]
    plan = reference_plan(verdict, duration, speaker)
    tmp_dir = os.path.dirname(dest) or "."
    pieces = []
    try:
        for k, (a, b) in enumerate(plan):
            piece = os.path.join(tmp_dir, f".voice_ref_part{k}.wav")
            verify.extract_audio(clip, piece, start_s=a, max_s=round(b - a, 3))
            pieces.append(piece)
        if len(pieces) == 1:
            os.replace(pieces[0], dest)
            pieces = []
        else:
            verify.concat_audio(pieces, dest)
    finally:
        for p in pieces:
            if os.path.exists(p):
                os.remove(p)
    level = verify.loudness(dest)
    if level["silent"]:
        os.remove(dest)
        raise VoiceRefError(f"the cut of {speaker}'s line is silent (mean {level['mean_db']} dB): pick another take")
    return {"speaker": speaker, "parts": plan, "duration_s": verify.probe(dest)["duration_s"],
            "speech_s": round(sum(min(b, r["end_s"]) - max(a, r["start_s"]) for (a, b), r in
                                  zip(plan, [r for r in verdict["lines"] if r["speaker"] == speaker])), 2),
            "mean_db": level["mean_db"], "max_db": level["max_db"]}
