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

**One talking clip per line** (plan 35, DEC-318: the human watched a
two-character shot carry two lines on one silent 5-s clip, stretched, the
mouths still). A shot of two lines or more that cannot talk as one clip --
two speakers, a framing or frame with more than one face, its speech past one
chunk (:data:`SPLIT_REASONS`) -- is cut into one part per line
(:func:`split`, through :func:`shot_verdict`): each part a close-up of its
line's speaker (a multi-reference edit of the speaker's sheet and the shot's
keyframe, :func:`closeup_prompt`) animated on :data:`TALKING_LINK` with that
line alone on its track; the parts cover the shot end to end
(:func:`_cuts`: a cut in each pause between two lines, each part at most one
chunk plus :data:`PART_HOLD_S`), so the render cuts them back to back and the
shot keeps its length -- never slowed. A one-line shot is the single talking
clip above, or the i2v link.

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
# Plan 35 (DEC-318): a shot of two lines or more that cannot talk as one clip is cut into one talking
# part per line. One chunk keeps 78 frames at 16 fps (TALK_KEPT_S); a part may hold its last frame
# PART_HOLD_S more (DEC-208's tolerance) -- never slowed. A single talking clip may still hold its last
# frame MAX_HOLD_S after its speech (what plan 32 stage 8 allowed: a quarter of its 5 s).
TALK_KEPT_S = 4.875
PART_HOLD_S = 0.5
MAX_PART_S = TALK_KEPT_S + PART_HOLD_S
MAX_HOLD_S = 1.25

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
NO_FACE = "no_face"

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
    NO_FACE: "a line of it is not spoken by a character of the story (no face to close up on)",
}
# The single verdict's refusals a per-line split can lift (plan 35).
SPLIT_REASONS = (SPEAKERS, FRAMING, NOT_ALONE, TOO_LONG)


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


def shot_verdict(ec, script, storyboard, shot, *, timeline=None, audio_of=None) -> dict:
    """How *shot*'s clip talks (plan 35, DEC-318): :func:`verdict` (one
    talking clip, plan 32 stage 8) when it talks so; else, for a shot of two
    lines or more turned down for its speakers, its framing, its frame or its
    length (:data:`SPLIT_REASONS`), the per-line split (:func:`split`) when
    every part of it fits -- a verdict that talks, with ``parts`` and
    ``single`` (why one clip could not); else the single verdict, its
    ``why`` the split's when the split was tried (the shot keeps the i2v
    link). A one-line shot is :func:`verdict` alone."""
    if timeline is None and enabled(getattr(ec, "story", None)):
        try:
            timeline = lipsync_step.episode_timeline(ec, script, storyboard)
        except (lipsync_step.timeline_mod.TimelineError, KeyError, ValueError, TypeError):
            timeline = None
    found = verdict(ec, script, storyboard, shot, timeline=timeline, audio_of=audio_of)
    if found["talks"] or found["why"] not in SPLIT_REASONS or len(shot.get("lines") or ()) < 2:
        return found
    if timeline is None:
        return found
    cut = split(ec, script, shot, timeline=timeline, audio_of=audio_of)
    if cut.get("talks"):
        return dict(cut, single=found["why"])
    return dict(found, why=cut["why"] or found["why"])


def verdicts(ec, script, storyboard, *, audio_of=None) -> dict:
    """:func:`shot_verdict` of every shot of *storyboard*, on one timeline:
    ``{shot_id: verdict}``; every shot ``OFF`` when the story makes no
    talking clips (no timeline is built then)."""
    shots = storyboard["shots"]
    if not enabled(getattr(ec, "story", None)):
        return {shot["shot_id"]: _verdict(OFF) for shot in shots}
    try:
        timeline = lipsync_step.episode_timeline(ec, script, storyboard)
    except (lipsync_step.timeline_mod.TimelineError, KeyError, ValueError, TypeError):
        timeline = {"shots": [], "lines": []}
    return {shot["shot_id"]: shot_verdict(ec, script, storyboard, shot, timeline=timeline, audio_of=audio_of)
            for shot in shots}


def verdict_now(ec, script, shot, *, storyboard=None) -> dict:
    """:func:`shot_verdict` of *shot* against the episode's storyboard on
    disk (read unless given): for a caller that holds the shot alone
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
    return shot_verdict(ec, script, storyboard, shot)


# ------------------------------------------------------------------ the parts (plan 35)

def _part_spec(line, path, sha, at_s, from_s) -> dict:
    """One part's dialogue track (``lipsync.build_track``'s spec): its one
    line at *at_s* in a :data:`TALK_CLIP_S` track, cut by *from_s* at its
    head, never sped up."""
    row = {"line_id": line["line_id"], "speaker": line["speaker"], "path": path, "sha256": sha,
           "at_s": round(at_s, 3), "from_s": round(from_s, 3)}
    payload = {"v": lipsync_step.TRACK_VERSION, "rate": lipsync_step.SAMPLE_RATE, "clip_s": TALK_CLIP_S,
               "tempo": 1.0, "lines": [[row["line_id"], row["sha256"], row["at_s"], row["from_s"]]]}
    return {"clip_s": TALK_CLIP_S, "tempo": 1.0, "lines": [row], "hash": lipsync_step._canonical_sha256(payload)}


def _cuts(spans, duration):
    """Where the parts of a shot of *duration* seconds start, one per line
    span ``(start, end)`` (seconds into the shot, in order), or None when no
    cut fits. The first part starts with the shot; a cut falls between the
    line before it ends and its own line starts -- at the middle of that
    pause, moved inside it when a part would run past :data:`MAX_PART_S` --
    and late enough that its line ends inside one chunk (:data:`CHUNK_S`)."""
    if spans[0][1] > CHUNK_S + 1e-9:
        return None
    count = len(spans)
    lows = [0.0] + [max(spans[k - 1][1], spans[k][1] - CHUNK_S) for k in range(1, count)]
    highs = [0.0] + [spans[k][0] for k in range(1, count)]
    if any(lows[k] > highs[k] + 1e-9 for k in range(count)):
        return None
    cuts = [0.0] + [min(max((spans[k - 1][1] + spans[k][0]) / 2, lows[k]), highs[k]) for k in range(1, count)]
    for k in range(1, count):  # a part too long: its end moved earlier, inside the pause
        if cuts[k] - cuts[k - 1] > MAX_PART_S:
            cuts[k] = max(lows[k], cuts[k - 1] + MAX_PART_S)
    for k in range(count - 1, 0, -1):  # the part after it too long: its start moved later, inside the pause
        end = cuts[k + 1] if k + 1 < count else duration
        if end - cuts[k] > MAX_PART_S:
            cuts[k] = min(highs[k], end - MAX_PART_S)
    ends = cuts[1:] + [duration]
    if any(end - start > MAX_PART_S + 1e-9 or end - start <= 0 for start, end in zip(cuts, ends)):
        return None
    if any(spans[k][1] - cuts[k] > CHUNK_S + 1e-9 for k in range(count)):
        return None
    return cuts


def split(ec, script, shot, *, timeline, audio_of=None) -> dict:
    """*shot* cut into one talking part per line (plan 35, DEC-318)::

        {"talks": bool, "why": None | UNTIMED | NO_FACE | TOO_LONG | NO_VOICE, "speaker": None,
         "speech_s", "spec": {"hash", "lines"}, "parts": [{"line_id", "speaker", "start_s",
         "duration_s", "spec"}]}

    Each part is a close-up of its line's speaker (a character of the story:
    the narrator has no face) made from the shot's keyframe, animated by
    :data:`TALKING_LINK` with that line alone on its track, at its place in
    the part; the parts cover the shot end to end (:func:`_cuts`), so the
    render cuts them back to back and the shot keeps its length -- never
    slowed. ``spec["hash"]`` covers every part's track and its span: a line
    re-voiced or re-timed makes the clip stale (:func:`track_current`).
    Calls nothing but file hashes."""
    lines = {line["line_id"]: line for scene in script["scenes"] for line in scene["lines"]}
    line_ids = list(shot.get("lines") or ())
    characters = (getattr(ec, "entities", None) or {}).get("characters") or {}
    if any(line_id not in lines or lines[line_id].get("speaker") not in characters for line_id in line_ids):
        return _verdict(NO_FACE)
    try:
        entry = next(item for item in timeline["shots"] if item["shot_id"] == shot["shot_id"])
    except (KeyError, StopIteration):
        return _verdict(UNTIMED)
    timed = {item["line_id"]: item for item in timeline["lines"]}
    if any(line_id not in timed for line_id in line_ids):
        return _verdict(UNTIMED)
    shot_start, duration = float(entry["start_s"]), float(entry["duration_s"])
    spans = []
    for line_id in line_ids:
        start = float(timed[line_id]["start_s"]) - shot_start
        end = start + float(timed[line_id]["duration_s"])
        spans.append((min(max(start, 0.0), duration), min(max(end, 0.0), duration)))
    speech_s = round(max(end for _start, end in spans), 3)
    cuts = _cuts(spans, duration)
    if cuts is None:
        return _verdict(TOO_LONG, speech_s=speech_s)
    audio_of = audio_of or (lambda line: lipsync_step.line_audio(ec, line))
    parts = []
    ends = cuts[1:] + [duration]
    for line_id, start, end in zip(line_ids, cuts, ends):
        line = lines[line_id]
        path = audio_of(line)
        sha = lipsync_step.sha256_file(path)
        if sha is None:
            return _verdict(NO_VOICE, speech_s=speech_s)
        at = float(timed[line_id]["start_s"]) - shot_start - start
        spec = _part_spec(line, path, sha, max(at, 0.0), max(-at, 0.0))
        parts.append({"line_id": line_id, "speaker": line["speaker"], "start_s": round(start, 3),
                      "duration_s": round(end - start, 3), "spec": spec})
    parts[-1]["duration_s"] = round(duration - parts[-1]["start_s"], 3)
    payload = {"v": 1, "parts": [[part["line_id"], part["start_s"], part["duration_s"], part["spec"]["hash"]]
                                 for part in parts]}
    spec = {"hash": lipsync_step._canonical_sha256(payload),
            "lines": [row for part in parts for row in part["spec"]["lines"]]}
    return _verdict(None, talks=True, speech_s=speech_s, spec=spec, parts=parts)


def is_split(found) -> bool:
    """Whether a talking verdict *found* is cut per line (:func:`split`)."""
    return bool((found or {}).get("talks") and (found or {}).get("parts"))


def part_name(shot_id, line_id) -> str:
    """``shot_03.l04.mp4``: the talking part of line *line_id* in shot
    *shot_id* (``schemas.SHOT_CLIP_FILE_PATTERN``)."""
    return f"shot_{shot_id[2:]}.{line_id}.mp4"


def closeup_name(shot_id, line_id, ext="png") -> str:
    """``shot_03.l04.png``: the close-up keyframe of that part
    (``schemas.SHOT_IMAGE_FILE_PATTERN``), beside the shot's keyframe."""
    return f"shot_{shot_id[2:]}.{line_id}.{ext}"


def closeup_reference(ec, speaker):
    """The real path of *speaker*'s identity image (its portrait sheet), or
    None when it has none on disk."""
    doc = ((getattr(ec, "entities", None) or {}).get("characters") or {}).get(speaker) or {}
    name = ((doc.get("refs") or {}).get("portrait") or {}).get("name")
    if not name:
        return None
    try:
        path = ec.store.media_path(ec.story_id, "characters", speaker, name)
    except KeyError:
        return None
    return path if os.path.isfile(path) and not os.path.islink(path) else None


def closeup_prompt(ec, speaker) -> str:
    """The close-up keyframe's prompt (a send-layer prompt, never a hashed
    core): the story's look, the speaker as written, then the frame -- head
    and shoulders, the face centred, looking at the camera, the mouth
    slightly open, in the place and light of the shot's keyframe (the second
    reference; the first is the speaker's own sheet)."""
    doc = ((getattr(ec, "entities", None) or {}).get("characters") or {}).get(speaker) or {}
    name = doc.get("name") or speaker
    described = " ".join(str(doc.get("description") or doc.get("descriptor") or "").split()).rstrip(".")
    rendering = str((getattr(ec, "style_lock", None) or {}).get("rendering") or "").strip().rstrip(".")
    who = f"{name}, {described}" if described else name
    text = (f"Close-up of {who}, exactly as in the first reference. Head and shoulders, face centred, looking at "
            "the camera, mouth slightly open, same place and lighting as the second reference.")
    return f"{rendering}. {text}" if rendering else text


def record_parts(found, tracks) -> dict:
    """A split clip's ``talk`` record (:func:`record`'s shape): the combined
    hash, the parts' track bytes (*tracks*, each ``build``'s, in order)
    hashed together, every line and where the speech ends."""
    shas = [track["sha256"] for track in tracks]
    return {"track_hash": found["spec"]["hash"], "audio_sha256": lipsync_step._canonical_sha256({"parts": shas}),
            "lines": [part["line_id"] for part in found["parts"]], "speech_s": float(found["speech_s"])}


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
    # Plan 35: the shots cut into one talking part per line, and how many parts (one close-up each).
    split_ids = [shot_id for shot_id, found in found_by_id.items() if is_split(found)]
    lines = sum(len(found_by_id[shot_id]["parts"]) for shot_id in split_ids)
    part = {"link": TALKING_LINK, "status": row.get("status"), "reason": row.get("reason"),
            "price_per_second": row.get("price_per_second"), "shots": talks, "too_long": too_long,
            "waiting": waiting, "count": 0, "seconds": 0, "est_usd": 0.0, "split": split_ids,
            "split_lines": lines, "closeups": 0, "closeup_usd": 0.0}
    part["message"] = sentence(part)
    return part


def sentence(part) -> str:
    """"N shots talk, K cut per line, M too long for one chunk" in plain
    words."""
    talks, too_long = len(part["shots"]), len(part["too_long"])
    text = (f"{talks} shot{'' if talks == 1 else 's'} talk{'s' if talks == 1 else ''} on {part['link']} (the mouth "
            "follows the line)")
    split_ids = part.get("split") or []
    if split_ids:
        lines = part.get("split_lines") or 0
        text += (f", {len(split_ids)} of them cut into one talking clip per line ({lines} clips, each from a "
                 "close-up of its speaker)")
    if too_long:
        text += f", {too_long} too long for one chunk (kept on the video link)"
    if part.get("status") not in (None, "keyed"):
        text += f"; {part['link']} cannot run now ({part.get('reason') or 'not keyed'}): every shot on the video link"
    return text
