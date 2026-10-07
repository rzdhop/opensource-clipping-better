"""Talking clips (AI Story, plan 32 stage 8, DEC-315 §6 as amended by the live
test of 2026-10-07: the human heard one S2V line and said "c'est très bien
pour le lipsync").

A story makes talking clips when ``media_policy.talking_clips`` says
``runpod_s2v`` (the ``own_gpu`` budget profile). Then a **speaking shot**'s
clip is made by ``runpod/s2v_wan22`` -- its keyframe and its dialogue track:
the mouth follows the voice -- and every other shot's clip by the episode's
video link (the i2v link), as always.

**Which shot talks** (:func:`verdict`), every rule must hold:

1. the story makes talking clips (``media_policy.talking_clips``);
2. the shot has lines (``shot["lines"]``);
3. every line of it is spoken by ONE speaker;
4. its framing shows one character (``close_up`` or ``medium_single``: a
   two-shot keeps the i2v link -- one face per talking clip);
5. that speaker is the one character in its frame (its ``subject_tags``);
6. the shot's speech ends within one S2V chunk (:data:`CHUNK_S`, 4.8 s of the
   4.875 s a chunk keeps), measured on the episode's timeline -- a longer one
   keeps the i2v link (the extend node is a later stage);
7. every line of it has its audio, so the dialogue track
   (``steps/lipsync.track_spec``: each line at its offset in the shot, 24 kHz
   mono, :data:`TALK_CLIP_S` long) holds them all.

The clip is bought as :data:`TALK_CLIP_S` seconds (one chunk: 78 kept frames,
4.875 s); a shot longer than it is held on its last frame by the render, never
slowed (a slowed mouth would leave its voice). Its record carries ``talk``
(:func:`record`): the track's spec hash, its bytes' sha256, its lines and where
the speech ends -- a line re-voiced or re-timed makes the clip stale
(:func:`track_current`). The clip keeps no sound of its own (the adapter drops
the track the worker muxed in): the render lays the lines, as for every clip.

Stdlib + ffmpeg (the track, ``steps/lipsync.build_track``).
"""

from __future__ import annotations

import contextlib
import os
import tempfile

from .. import media_policy
from . import episode_common
from . import lipsync as lipsync_step

TALKING_LINK = "runpod/s2v_wan22"
# One 77-latent chunk keeps 78 frames at 16 fps: 4.875 s, bought as 5 s.
TALK_CLIP_S = 5
CHUNK_S = 4.8
SOLO_FRAMINGS = ("close_up", "medium_single")

# Why a shot does not talk (``verdict["why"]``).
OFF = "off"
NO_LINES = "no_lines"
SPEAKERS = "speakers"
FRAMING = "framing"
NOT_ALONE = "not_alone"
UNTIMED = "untimed"
TOO_LONG = "too_long"
NO_VOICE = "no_voice"
UNAVAILABLE = "unavailable"

_WHY_TEXT = {
    OFF: "the story makes no talking clips",
    NO_LINES: "it has no line",
    SPEAKERS: "its lines are not all one character's",
    FRAMING: "its framing shows more than one face",
    NOT_ALONE: "its speaker is not the one character in its frame",
    UNTIMED: "its lines cannot be placed on the episode's timeline",
    TOO_LONG: f"its speech runs past one {CHUNK_S:g} s chunk",
    NO_VOICE: "a line of it has no audio yet",
    UNAVAILABLE: f"{TALKING_LINK} cannot run now",
}


def enabled(story) -> bool:
    """Whether *story* makes talking clips (``media_policy.talking_clips``)."""
    return media_policy.talking_clips(story) == media_policy.TALKING_S2V


def why_text(verdict) -> str:
    """The plain words of a *verdict*'s ``why``."""
    return _WHY_TEXT.get((verdict or {}).get("why"), str((verdict or {}).get("why")))


def _verdict(why=None, **fields) -> dict:
    out = {"talks": False, "why": why, "speaker": None, "speech_s": None, "spec": None}
    out.update(fields)
    return out


def verdict(ec, script, storyboard, shot, *, timeline=None, audio_of=None) -> dict:
    """Whether *shot* talks (module docstring's rules, in order)::

        {"talks": bool, "why": None | OFF | NO_LINES | ..., "speaker", "speech_s", "spec"}

    ``spec`` is the dialogue track's (``lipsync.track_spec`` at
    :data:`TALK_CLIP_S`) when it talks. *timeline* is
    ``render.timeline.build_timeline``'s (built when not given), *audio_of(line)*
    the path of a line's audio (default the measured line's). Calls nothing
    but file hashes."""
    if not enabled(getattr(ec, "story", None)):
        return _verdict(OFF)
    line_ids = list(shot.get("lines") or ())
    if not line_ids:
        return _verdict(NO_LINES)
    lines = {line["line_id"]: line for scene in script["scenes"] for line in scene["lines"]}
    speakers = {lines[line_id].get("speaker") for line_id in line_ids if line_id in lines}
    if len(speakers) != 1 or any(line_id not in lines for line_id in line_ids):
        return _verdict(SPEAKERS)
    speaker = next(iter(speakers))
    if shot.get("framing") not in SOLO_FRAMINGS:
        return _verdict(FRAMING, speaker=speaker)
    if lipsync_step.in_frame(shot) != {speaker}:
        return _verdict(NOT_ALONE, speaker=speaker)
    try:
        timeline = timeline if timeline is not None else lipsync_step.episode_timeline(ec, script, storyboard)
        shot_start = next(entry["start_s"] for entry in timeline["shots"] if entry["shot_id"] == shot["shot_id"])
    except (lipsync_step.timeline_mod.TimelineError, KeyError, ValueError, TypeError, StopIteration):
        return _verdict(UNTIMED, speaker=speaker)
    timed = {entry["line_id"]: entry for entry in timeline["lines"]}
    if any(line_id not in timed for line_id in line_ids):
        return _verdict(UNTIMED, speaker=speaker)
    speech_s = round(max(float(timed[line_id]["start_s"]) + float(timed[line_id]["duration_s"]) - float(shot_start)
                         for line_id in line_ids), 3)
    if speech_s > CHUNK_S + 1e-9:
        return _verdict(TOO_LONG, speaker=speaker, speech_s=speech_s)
    spec = lipsync_step.track_spec(ec, script, storyboard, shot, clip={"clip_s": TALK_CLIP_S}, timeline=timeline,
                                   audio_of=audio_of)
    if spec is None or sorted(row["line_id"] for row in spec["lines"]) != sorted(line_ids):
        return _verdict(NO_VOICE, speaker=speaker, speech_s=speech_s)
    return _verdict(None, talks=True, speaker=speaker, speech_s=speech_s, spec=spec)


def verdicts(ec, script, storyboard, *, audio_of=None) -> dict:
    """:func:`verdict` of every shot of *storyboard*, on one timeline:
    ``{shot_id: verdict}``; every shot ``OFF`` when the story makes no
    talking clips (no timeline is built then)."""
    shots = storyboard["shots"]
    if not enabled(getattr(ec, "story", None)):
        return {shot["shot_id"]: _verdict(OFF) for shot in shots}
    try:
        timeline = lipsync_step.episode_timeline(ec, script, storyboard)
    except (lipsync_step.timeline_mod.TimelineError, KeyError, ValueError, TypeError):
        timeline = {"shots": [], "lines": []}
    return {shot["shot_id"]: verdict(ec, script, storyboard, shot, timeline=timeline, audio_of=audio_of)
            for shot in shots}


def verdict_now(ec, script, shot, *, storyboard=None) -> dict:
    """:func:`verdict` of *shot* against the episode's storyboard on disk
    (read unless given): for a caller that holds the shot alone
    (``clips.clip_state``)."""
    if not enabled(getattr(ec, "story", None)):
        return _verdict(OFF)
    if storyboard is None:
        try:
            storyboard = episode_common.read_episode(ec, episode_common.STORYBOARD_DOC)
        except Exception:  # noqa: BLE001 - an unreadable board: the shot cannot be timed
            storyboard = None
    if storyboard is None:
        return _verdict(UNTIMED)
    return verdict(ec, script, storyboard, shot)


def unavailable(found) -> dict:
    """*found* (a talking verdict) turned down because :data:`TALKING_LINK`
    cannot run now: the shot keeps the episode's video link."""
    return dict(found, talks=False, why=UNAVAILABLE) if found.get("talks") else found


# ------------------------------------------------------------------ the track

def build(found, out_path, *, run=None) -> dict:
    """The dialogue track of a talking *found* verdict written to *out_path*
    (``lipsync.build_track``): ``{"path", "sha256", "lines", "hash"}``.
    ``lipsync.LipsyncError`` when ffmpeg is missing or fails."""
    return lipsync_step.build_track(found["spec"], out_path, run=run)


@contextlib.contextmanager
def track_file(found, *, name="talk", run=None):
    """The dialogue track of a talking *found* verdict, built in a folder of
    its own for as long as the block runs (a request key needs its bytes):
    yields its path, or None when it cannot be built (no ffmpeg, a line's
    audio unreadable)."""
    with tempfile.TemporaryDirectory(prefix="talk-track-") as work:
        try:
            track = build(found, os.path.join(work, f"{name}.wav"), run=run)
        except lipsync_step.LipsyncError:
            track = None
        yield None if track is None else track["path"]


def record(found, track) -> dict:
    """A talking clip's ``talk`` record (``schemas._STORYBOARD_CLIP_SCHEMA``)."""
    return {"track_hash": found["spec"]["hash"], "audio_sha256": track["sha256"],
            "lines": [row["line_id"] for row in found["spec"]["lines"]], "speech_s": float(found["speech_s"])}


def track_current(clip, found) -> bool:
    """Whether a talking *clip* record was made from the track *found* (a
    talking verdict) would build now."""
    talk = (clip or {}).get("talk") or {}
    return bool(found.get("spec")) and talk.get("track_hash") == found["spec"]["hash"]


# ------------------------------------------------------------------ the plan

def plan_part(found_by_id, row) -> dict:
    """The estimate's ``talking`` part: the S2V link's *row*
    (``clips.link_row``), the shots that talk, the speaking shots whose
    speech runs past one chunk, and the plain sentence."""
    talks = [shot_id for shot_id, found in found_by_id.items() if found.get("talks")]
    too_long = [shot_id for shot_id, found in found_by_id.items() if found.get("why") == TOO_LONG]
    waiting = [shot_id for shot_id, found in found_by_id.items()
               if found.get("why") == UNAVAILABLE or (found.get("why") == NO_VOICE)]
    part = {"link": TALKING_LINK, "status": row.get("status"), "reason": row.get("reason"),
            "price_per_second": row.get("price_per_second"), "shots": talks, "too_long": too_long,
            "waiting": waiting, "count": 0, "seconds": 0, "est_usd": 0.0}
    part["message"] = sentence(part)
    return part


def sentence(part) -> str:
    """"N shots talk, M too long for one chunk" in plain words."""
    talks, too_long = len(part["shots"]), len(part["too_long"])
    text = (f"{talks} shot{'' if talks == 1 else 's'} talk{'s' if talks == 1 else ''} on {part['link']} (the mouth "
            "follows the line)")
    if too_long:
        text += f", {too_long} too long for one chunk (kept on the video link)"
    if part.get("status") not in (None, "keyed"):
        text += f"; {part['link']} cannot run now ({part.get('reason') or 'not keyed'}): every shot on the video link"
    return text
