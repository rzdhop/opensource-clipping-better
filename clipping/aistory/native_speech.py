"""Native speech: each character line spoken on camera by its own clip (plan
22, stage 4; the ``native_speech`` budget profile, ``media_policy.native_speech``).

On such a story every character line is one shot whose clip speaks it, lips
and voice one performance; the shots without a line (a reaction, a
narrator's voice-over) are silent clips with their ambience. Nothing here
knows which link made a clip -- an API link or a clip the human uploaded:
the shot plan, the take and the timing read the clip and its record only.

- **Lengths.** A speaking shot is planned at the smallest length its link
  sells whose capacity holds the line (:func:`capacity`: the words a clip of
  that length can speak at :data:`SPEECH_WPS`, after
  :data:`SPEECH_OVERHEAD_S` of breath before and after); a line no length
  holds is refused with the fix (:func:`line_refusal`). A reaction shot is
  :data:`REACTION_S`; a narrator's shot is sized to its narration.
- **The take** (:func:`evaluate_take`): the clip's own sound transcribed and
  aligned against the line (``wordtiming.align``): ``ok`` when at least
  :data:`MIN_MATCHED` of the line's words are heard and the last one ends
  :data:`END_MARGIN_S` before the clip's real end; ``mismatch`` otherwise;
  ``no_speech`` when nothing is heard; ``stt_unavailable`` when no STT link
  can run (the words then split evenly over the planned window,
  :func:`planned_window`, labelled approximate).
- **The shot's length** follows the clip's real length, trimmed after the
  last word + :data:`TRIM_PAD_S` only when the clip runs more than
  :data:`TRIM_AFTER_S` past it (:func:`shot_seconds`).
- **The timing** (:func:`native_pass`): a native board's shots last what
  they last (no window pass moves them, no transition overlaps them); a
  speaking shot's line starts where its take heard it, a narrator's line
  :data:`NARRATOR_LEAD_S` into its shot.

Pure: no disk, no clock, no network (DEC-012).
"""

from __future__ import annotations

import difflib
import math

from . import wordtiming

# The storyboard's ``timing_mode`` on a native-speech story's board.
TIMING_MODE = "native_speech"

# Words a second a clip speaks (A-148, until the probe measures it) and the
# seconds of a clip that are not speech (a breath before, a beat after).
SPEECH_WPS = 2.4
SPEECH_OVERHEAD_S = 0.7
# The lengths a speaking clip is planned at when its link has no table
# (Veo's own: 4, 6, 8 s).
SPEECH_LENGTHS = (4, 6, 8)
# A silent reaction shot's length.
REACTION_S = 4
# Where the planned speech starts in its clip (half the overhead), and where
# a narrator's voice-over starts in its silent shot.
PLANNED_LEAD_S = round(SPEECH_OVERHEAD_S / 2, 3)
NARRATOR_LEAD_S = PLANNED_LEAD_S

# The take's checks.
MIN_MATCHED = 0.75
END_MARGIN_S = 0.1
# The length rule: a speaking clip running more than TRIM_AFTER_S past its
# last word is cut TRIM_PAD_S after it.
TRIM_PAD_S = 0.3
TRIM_AFTER_S = 1.0

TAKE_OK, TAKE_MISMATCH, TAKE_NO_SPEECH, TAKE_STT_UNAVAILABLE = "ok", "mismatch", "no_speech", "stt_unavailable"
TAKE_STATES = (TAKE_OK, TAKE_MISMATCH, TAKE_NO_SPEECH, TAKE_STT_UNAVAILABLE)
# The takes the subtitles and the line's audio come from (a mismatch is
# flagged, never silently dropped: its words are what the clip says).
TAKES_WITH_SPEECH = (TAKE_OK, TAKE_MISMATCH, TAKE_STT_UNAVAILABLE)
# The takes that flag the shot for one retake.
TAKES_TO_RETAKE = (TAKE_MISMATCH, TAKE_NO_SPEECH)

FPS = 30


def words_of(text) -> int:
    """The line's words, as the subtitles count them."""
    return len(wordtiming.tokens(text))


def capacity(length_s) -> int:
    """How many words a clip of *length_s* seconds speaks:
    ``floor((L - 0.7) x 2.4)`` -- 4 s: 7, 6 s: 12, 8 s: 17."""
    return max(0, math.floor((float(length_s) - SPEECH_OVERHEAD_S) * SPEECH_WPS + 1e-9))


def speech_clip_s(text, lengths=SPEECH_LENGTHS):
    """The smallest of *lengths* whose :func:`capacity` holds *text*, or
    None when none does."""
    count = words_of(text)
    for length in sorted(lengths or SPEECH_LENGTHS):
        if capacity(length) >= count:
            return int(length)
    return None


def line_refusal(line, lengths=SPEECH_LENGTHS):
    """Why *line* (a script line) cannot be one speaking clip, with the fix,
    or None."""
    longest = max(lengths or SPEECH_LENGTHS)
    if speech_clip_s(line["text"], lengths) is not None:
        return None
    return (f"line {line['line_id']} has {words_of(line['text'])} words, more than a {longest} s clip can speak "
            f"({capacity(longest)} words at most): shorten it, or split it into two lines, in the script")


def silent_clip_s(seconds, lengths=SPEECH_LENGTHS) -> int:
    """The smallest of *lengths* at least *seconds* long, else the longest."""
    ordered = sorted(lengths or SPEECH_LENGTHS)
    return int(next((length for length in ordered if length >= seconds), ordered[-1]))


def narrator_clip_s(narration_s, lengths=SPEECH_LENGTHS) -> int:
    """A narrator's silent shot: its narration with the lead and a beat after."""
    return silent_clip_s(float(narration_s or 0.0) + SPEECH_OVERHEAD_S, lengths)


def planned_window(clip_s) -> tuple:
    """``(start_s, end_s)`` the speech is planned in, inside a *clip_s* clip."""
    return PLANNED_LEAD_S, round(max(PLANNED_LEAD_S, float(clip_s) - PLANNED_LEAD_S), 3)


# ------------------------------------------------------------------ the take

def _matches(text, stt_words) -> tuple:
    """``(spoken, pairs)``: the indexes of *text*'s words that are words
    (punctuation alone is not: a French "?" is no word to hear), and the
    ``(word index, heard index)`` pairs matched, compared as
    ``wordtiming.align`` compares them."""
    tokens = wordtiming.tokens(text)
    ours = [wordtiming.normalise(word) or f"\0{i}" for i, word in enumerate(tokens)]
    spoken = [i for i, word in enumerate(tokens) if wordtiming.normalise(word)]
    theirs = [wordtiming.normalise((word or {}).get("word")) or f"\1{j}" for j, word in enumerate(stt_words or ())]
    matcher = difflib.SequenceMatcher(None, ours, theirs, autojunk=False)
    pairs = [(block.a + k, block.b + k) for block in matcher.get_matching_blocks() for k in range(block.size)]
    return spoken, pairs


def matched_ratio(text, stt_words) -> float:
    """The share of *text*'s words heard in *stt_words* (the transcription)."""
    spoken, pairs = _matches(text, [word for word in stt_words or () if isinstance(word, dict)])
    if not spoken:
        return 0.0
    return round(len(pairs) / len(spoken), 3)


def heard_text(stt_words, limit=400) -> str:
    text = " ".join(str((word or {}).get("word") or "").strip() for word in stt_words or () if isinstance(word, dict))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def evaluate_take(text, stt_words, *, clip_real_s, clip_s, aligned_by=None) -> dict:
    """What the clip's own sound says of its line *text*::

        {"state", "matched", "heard", "start_s", "end_s", "aligned_by", "words"}

    *stt_words* the transcription of the whole clip (``[{word, start,
    end}]`` from its start), or None when no STT link could run
    (``stt_unavailable``: the planned window, words even-split). ``words``
    are the line's words timed from ``start_s`` (the line's audio is the
    clip's sound from ``start_s`` to ``end_s``); None when nothing is heard."""
    clip_real_s = float(clip_real_s)
    if stt_words is None:
        # The planned window inside the clip as it really is (an uploaded clip may run past the plan).
        start, end = planned_window(clip_real_s)
        return {"state": TAKE_STT_UNAVAILABLE, "matched": None, "heard": None, "start_s": start, "end_s": end,
                "aligned_by": None, "words": None}
    heard = [word for word in stt_words if isinstance(word, dict) and str(word.get("word") or "").strip()]
    if not heard:
        return {"state": TAKE_NO_SPEECH, "matched": 0.0, "heard": "", "start_s": None, "end_s": None,
                "aligned_by": aligned_by, "words": None}
    spoken, pairs = _matches(text, heard)
    matched = round(len(pairs) / len(spoken), 3) if spoken else 0.0
    if not pairs:
        return {"state": TAKE_MISMATCH, "matched": matched, "heard": heard_text(heard), "start_s": None,
                "end_s": None, "aligned_by": aligned_by, "words": None}
    # The speech runs from the first word of the line heard to the last one.
    spans = wordtiming._spans(heard, clip_real_s)
    start = spans[pairs[0][1]][0]
    end = max(spans[pairs[-1][1]][1], start + 0.05)
    window = [dict(word, start=max(0.0, float(word["start"]) - start), end=max(0.0, float(word["end"]) - start))
              for word in heard[pairs[0][1]:pairs[-1][1] + 1]]
    aligned = wordtiming.align(text, window, end - start) or []
    # Judged against the clip's REAL length: a clip longer than planned (an 8 s upload for a 4 s line) may
    # speak past the planned length; ``clip_s`` is only what the plan bought.
    ok = matched >= MIN_MATCHED and end <= clip_real_s - END_MARGIN_S + 1e-9
    words = [{"word": word["word"], "start": word["start"], "end": word["end"]} for word in aligned]
    return {"state": TAKE_OK if ok else TAKE_MISMATCH, "matched": matched, "heard": heard_text(heard),
            "start_s": round(start, 3), "end_s": round(end, 3), "aligned_by": aligned_by, "words": words}


def _frames_down(seconds) -> float:
    return math.floor(float(seconds) * FPS + 1e-6) / FPS


def shot_seconds(clip_real_s, *, speaks, end_s=None) -> float:
    """A shot's length from its clip's real length (whole frames, never past
    the clip): a speaking clip running more than :data:`TRIM_AFTER_S` past
    its last word (*end_s*) is cut :data:`TRIM_PAD_S` after it; any other
    clip keeps its length."""
    real = _frames_down(clip_real_s)
    if speaks and end_s is not None and real - float(end_s) > TRIM_AFTER_S:
        return round(_frames_down(float(end_s) + TRIM_PAD_S), 3)
    return round(real, 3)


# ---------------------------------------------------------------- the timing

def is_native_board(storyboard) -> bool:
    return bool(storyboard) and storyboard.get("timing_mode") == TIMING_MODE


def take_of(shot):
    """A shot's take record (``assets.clip.native_speech``), or None."""
    take = ((shot.get("assets") or {}).get("clip") or {}).get("native_speech")
    return take if isinstance(take, dict) else None


def line_placements(script, storyboard, language_duration) -> dict:
    """``{line_id: (scene_offset_s, duration_s)}`` of every line a native
    *storyboard* places: a speaking shot's line at its shot's offset in the
    scene plus its take's ``start_s`` (the planned lead without one), a
    narrator's at its shot's offset plus :data:`NARRATOR_LEAD_S`; never past
    its shot's end. *language_duration(line)* is the line's own length."""
    by_scene = {}
    for shot in sorted(storyboard["shots"], key=lambda item: item["order"]):
        by_scene.setdefault(shot["scene_id"], []).append(shot)
    lines = {line["line_id"]: line for scene in script["scenes"] for line in scene["lines"]}
    placed = {}
    for scene_id, shots in by_scene.items():
        offset = 0.0
        for shot in shots:
            duration = float(shot["duration_s"] or 0.0)
            take = take_of(shot)
            for line_id in shot["lines"]:
                line = lines.get(line_id)
                if line is None:
                    continue
                lead = NARRATOR_LEAD_S
                if shot.get("speaks"):
                    lead = float(take["start_s"]) if take and take.get("start_s") is not None else PLANNED_LEAD_S
                seconds = float(language_duration(line))
                lead = min(lead, max(0.0, duration - 0.05))
                seconds = max(0.05, min(seconds, duration - lead))
                placed[line_id] = (round(offset + lead, 3), round(seconds, 3))
            offset += duration
    return placed


def native_pass(script, storyboard, template, language_duration) -> tuple:
    """``(timing, scenes)`` of a native *storyboard*, in the shape of
    ``timing.episode_pass``'s: each scene lasts its shots' sum (every shot is
    what its clip is: no hold, no tail, no window pass); the scene
    boundaries are cuts (:func:`plan_transitions` makes them so); the total
    adds the end card as the other boards do. The window state is said, a
    native episode is never re-timed to fit it."""
    durations = {}
    for shot in storyboard["shots"]:
        durations[shot["scene_id"]] = durations.get(shot["scene_id"], 0.0) + float(shot["duration_s"] or 0.0)
    placements = line_placements(script, storyboard, language_duration)
    end_card = 0.0
    if script["cliffhanger"]["cut_to_black"]:
        end_card = template["end_card_s"] - template["transitions_s"]["fadeblack"]
    scenes, scene_timings = {}, {}
    for scene in script["scenes"]:
        sid = scene["scene_id"]
        seconds = round(durations.get(sid, 0.0), 3)
        starts = {line["line_id"]: placements[line["line_id"]][0] for line in scene["lines"]
                  if line["line_id"] in placements}
        speech = sum(placements[line_id][1] for line_id in starts)
        entry = {"duration_s": seconds, "tail_s": 0.0, "hold_s": 0.0, "state": "ok"}
        scenes[sid] = entry
        scene_timings[sid] = dict(entry, speech_s=round(speech, 3), line_starts=starts)
    total = round(sum(entry["duration_s"] for entry in scenes.values()) + end_card, 3)
    window_lo, window_hi = template["window_s"]
    state, flags = "ok", []
    if total > window_hi + 1e-6:
        state = "over"
        flags.append({"kind": "episode_over", "scene_id": None, "line_id": None,
                      "seconds": round(total - window_hi, 3),
                      "message": f"The episode is {total - window_hi:.1f} s over {window_hi:g} s."})
    elif total < window_lo - 1e-6:
        state = "under"
        flags.append({"kind": "episode_under", "scene_id": None, "line_id": None,
                      "seconds": round(window_lo - total, 3),
                      "message": f"The episode is {window_lo - total:.1f} s under {window_lo:g} s."})
    measured = estimated = 0
    for scene in script["scenes"]:
        for line in scene["lines"]:
            source = (line.get("timing") or {}).get("source")
            if source in (None, "estimated"):
                estimated += 1
            else:
                measured += 1
    timing = {"total_s": total, "window_s": [window_lo, window_hi], "target_s": template["target_s"],
              "state": state, "scenes": scenes, "flags": flags, "estimated_lines": estimated,
              "measured_lines": measured}
    return timing, scene_timings


def line_offsets(script, storyboard, language_duration) -> dict:
    """Absolute ``{line_id: (start_s, end_s)}`` on a native *storyboard*'s
    timeline (every boundary a cut: a scene starts where the one before
    ends)."""
    placements = line_placements(script, storyboard, language_duration)
    durations = {}
    for shot in storyboard["shots"]:
        durations[shot["scene_id"]] = durations.get(shot["scene_id"], 0.0) + float(shot["duration_s"] or 0.0)
    offsets, start = {}, 0.0
    for scene in script["scenes"]:
        for line in scene["lines"]:
            if line["line_id"] in placements:
                at, seconds = placements[line["line_id"]]
                offsets[line["line_id"]] = (round(start + at, 3), round(start + at + seconds, 3))
        start += durations.get(scene["scene_id"], 0.0)
    return offsets
