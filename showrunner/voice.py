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
