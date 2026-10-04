"""The AI-Story renderer's output timeline (spec 6.4, 6.5; plan phase 4
stage 4, "Renderer" -> timeline.py; DEC-156, DEC-158).

:func:`build_timeline` turns a script + a *covering* (or partial)
storyboard into the one flat, absolute-seconds timeline every later render
stage reads from: which shot occupies which span of the output movie, where
every line and SFX cue lands, and where (or whether) the end card sits.
``timing.py`` stays the single source of truth for durations (DEC-126/142):
this module never re-times a line or a scene, it only lays already-timed
shots/lines/cues end to end, cross-checking the result against
``timing.episode_pass``/``timing.line_offsets`` rather than recomputing
them independently.

**The end card as a virtual shot** (cliffhanger_style ``cut_to_black``,
spec 6.2/6.4). ``timing._effective_tail_floor`` already treats the last
scene's own tail as needing room for "the fade that joins the 1.0 s end
card" -- i.e. the fade is *carved out of* the preceding content's own
runtime, exactly like an ordinary scene-to-scene transition, not added on
top of it. So the end card is timed here the same way any shot is timed:
as one more entry in the ``start = sum_{j<i}(d_j - t_j)`` sequence, with its
own "incoming transition" fixed to ``fadeblack`` (the template's own
duration, 0.4 s) and its own "duration" fixed to the template's
``end_card_s`` (1.0 s). Concretely, with ``X`` the last real shot's own
natural end (``start + duration``): the card's fade begins at ``X -
0.4``, and the card's own 1.0 s span is ``[X - 0.4, X - 0.4 + 1.0] == [X -
0.4, X + 0.6]`` -- ending exactly at ``X + 0.6``, which is
``timing.episode_pass``'s own ``end_card_addition`` (``end_card_s -
fadeblack``) added to the raw total, so the two totals agree by
construction, not by coincidence. ``hard_stop`` adds no card: the last shot
simply ends the file (spec 6.2).

**Frame counts.** Two distinct integer-frame quantities are exposed, and
they are *not* expected to be equal (the difference is exactly the time
transitions eat out of the raw shot sequence):

- each shot's own ``frames`` -- how many frames *that shot's own clip* must
  be rendered for (``render/filtergraph.py``'s ``-frames:v``) -- is the
  shot's ``duration_s`` at ``profiles.FPS``, rounded with *cumulative*
  rounding across the raw (un-overlapped) shot-duration sequence: the
  classic "largest remainder" rule that makes
  ``sum(shot["frames"] for shots) == round(sum(shot durations) * fps)``
  hold exactly, never off by the +/-1 frame naive per-shot rounding can
  accumulate across many shots (see :func:`_cumulative_frames`).
- ``timeline["total_frames"]`` -- the *movie's* own frame count -- is
  simply ``round(timeline["total_s"] * profiles.FPS)``: a single number,
  needing no cumulative machinery of its own, and the one the "Sigma frames
  == round(total * 30)" invariant is about.

Stdlib + this package only (DEC-012; RC-P8), mirroring ``shots.py``'s own
portability guard.
"""

from __future__ import annotations

from . import profiles
from .. import timing as timing_mod


class TimelineError(ValueError):
    """Raised by :func:`build_timeline` when the storyboard/script/timing
    given cannot be turned into one consistent output timeline (spec
    6.4/6.5): a shot-level total that disagrees with
    :func:`timing.episode_pass`'s own total, a covering board's line
    offsets disagreeing with :func:`timing.line_offsets`, or a line that
    starts or ends inside a transition's blend window."""


_EPS = 1e-6


# ------------------------------------------------------------------ frames

def _cumulative_frames(values, fps) -> list:
    """Cumulative-rounded integer frame counts for a sequence of raw
    seconds *values*: each entry's own frame count is chosen so that, after
    every prefix, the running sum of frame counts is always the nearest
    integer to that prefix's own running sum of seconds times *fps* -- so
    ``sum(result) == round(sum(values) * fps)`` exactly, however many
    entries there are and whatever their individual fractional remainders
    (the "largest remainder"/Bresenham rounding rule)."""
    result = []
    cum_s = 0.0
    prev_cum_frames = 0
    for v in values:
        cum_s += v
        cum_frames = round(cum_s * fps)
        result.append(cum_frames - prev_cum_frames)
        prev_cum_frames = cum_frames
    return result


# -------------------------------------------------------------------- shots

def _ordered_shots(storyboard: dict) -> list:
    return sorted(storyboard["shots"], key=lambda shot: shot["order"])


def _shots_timeline(storyboard: dict, *, cut_to_black: bool, fadeblack_s: float, card_s: float, fps: int) -> tuple:
    """``(shot_entries, last_shot_end_s)``: every real shot's ``start_s``
    (``sum_{j<i}(d_j - t_j)``, module docstring), ``duration_s``, cumulative
    ``frames`` (:func:`_cumulative_frames` over the raw duration sequence)
    and ``transition_after`` -- taken from the storyboard's own transitions
    (keyed by ``"after"``, never by position, the same convention
    ``timing._boundary_from_storyboard`` uses), except the very last shot's
    ``transition_after``, which is synthesised as ``fadeblack`` into the end
    card when *cut_to_black* (see module docstring) and left ``None`` for
    ``hard_stop``, overriding whatever (normally nothing) the storyboard
    itself carries for it."""
    shots = _ordered_shots(storyboard)
    transitions_by_after = {t["after"]: t for t in storyboard["transitions"]}

    durations = [shot["duration_s"] for shot in shots]
    frame_counts = _cumulative_frames(durations, fps)

    entries = []
    cursor = 0.0
    for i, shot in enumerate(shots):
        duration = shot["duration_s"]
        start = cursor
        is_last = i == len(shots) - 1
        if is_last and cut_to_black:
            transition_after = {"type": "fadeblack", "duration_s": fadeblack_s}
        elif is_last:
            transition_after = None
        else:
            found = transitions_by_after.get(shot["shot_id"])
            transition_after = (
                {"type": found["type"], "duration_s": found["duration_s"]} if found is not None
                else {"type": "cut", "duration_s": 0.0}
            )

        entries.append({
            "shot_id": shot["shot_id"],
            "scene_id": shot["scene_id"],
            "start_s": round(start, 3),
            "duration_s": duration,
            "frames": frame_counts[i],
            "motion": shot["motion"],
            "modifiers": list(shot["modifiers"]),
            "transition_after": transition_after,
        })

        t_dur = transition_after["duration_s"] if transition_after is not None else 0.0
        cursor += duration - t_dur

    last_shot = entries[-1]
    last_shot_natural_end = last_shot["start_s"] + last_shot["duration_s"]
    return entries, last_shot_natural_end


# --------------------------------------------------------------- end card

def _end_card(cut_to_black: bool, *, last_shot_natural_end: float, fadeblack_s: float, card_s: float) -> dict:
    if not cut_to_black:
        return None
    fade_start_s = last_shot_natural_end - fadeblack_s
    start_s = fade_start_s  # the fade IS the card's own entrance (module docstring)
    return {
        "fade_start_s": round(fade_start_s, 3),
        "fade_duration_s": fadeblack_s,
        "start_s": round(start_s, 3),
        "duration_s": card_s,
        "end_s": round(start_s + card_s, 3),
    }


# -------------------------------------------------------------- sfx anchors

def _sfx_anchors(script: dict, *, scene_starts: dict, line_starts_by_id: dict) -> list:
    """``[{scene_id, at, cue, start_s}, ...]``, in script order: an
    ``"at": "start"`` cue anchors to its own scene's start (even a scene
    with no lines at all); an ``"at": "lNN"`` cue anchors to that line's own
    start on the output timeline (spec 6.5: "SFX cues adelay'ed at their
    anchors")."""
    anchors = []
    for scene in script["scenes"]:
        sid = scene["scene_id"]
        for cue in scene.get("sfx_cues", []):
            at = cue["at"]
            if at == "start":
                start = scene_starts[sid]
            else:
                start = line_starts_by_id[at]
            anchors.append({"scene_id": sid, "at": at, "cue": cue["cue"], "start_s": round(start, 3)})
    return anchors


# ------------------------------------------------------------- invariant

def _assert_no_line_in_a_transition_window(shot_entries, lines) -> None:
    """Spec 6.4's invariant: no line "bleeds" into the transition that
    closes its OWN scene out. A window is ``[start_of_next_shot,
    start_of_next_shot + transition_duration]`` -- the overlap where the
    outgoing and the incoming shot/card are both visible (module
    docstring's xfade-offset algebra); ``cut``/0-duration transitions never
    produce a window.

    Scoped per scene deliberately: a window is only checked against lines
    belonging to its OWN OUTGOING scene (the scene the transition leaves),
    never the incoming one. A scene's own ``before_first_line`` pre-roll
    (0.35 s in ``serial_60s_v1``) is routinely *shorter* than a fadeblack
    (0.4 s), so an incoming scene's first line ordinarily starts a few
    hundredths of a second before its own transition has visually
    finished -- expected, harmless (the line is not on screen for the
    outgoing shot's content), and never what this invariant is about.
    ``timing._effective_tail_floor`` already guarantees an OUTGOING scene's
    own last line ends at or before its own transition's window starts, so
    this only ever fires on data where a scene's rendered shot span
    disagrees with what ``timing.episode_pass`` computed for it (a stale
    storyboard, spec: "a synthetic board")."""
    windows = []  # (win_start, win_end, shot_id, outgoing_scene_id)
    for i in range(len(shot_entries) - 1):
        transition = shot_entries[i]["transition_after"]
        if transition is None or transition["duration_s"] <= 0:
            continue
        next_start = shot_entries[i + 1]["start_s"]
        windows.append((next_start, next_start + transition["duration_s"],
                        shot_entries[i]["shot_id"], shot_entries[i]["scene_id"]))
    # the last real shot's own transition (into the end card) is a window too
    last = shot_entries[-1]
    if last["transition_after"] is not None and last["transition_after"]["duration_s"] > 0:
        card_start = last["start_s"] + last["duration_s"] - last["transition_after"]["duration_s"]
        windows.append((card_start, card_start + last["transition_after"]["duration_s"],
                        last["shot_id"], last["scene_id"]))

    if not windows:
        return
    lines_by_scene = {}
    for line in lines:
        lines_by_scene.setdefault(line["scene_id"], []).append(line)

    for win_start, win_end, shot_id, outgoing_scene_id in windows:
        for line in lines_by_scene.get(outgoing_scene_id, []):
            line_start, line_end = line["start_s"], line["start_s"] + line["duration_s"]
            if line_start < win_end - _EPS and line_end > win_start + _EPS:
                raise TimelineError(
                    f"line {line['line_id']!r} ({line_start}-{line_end}s) falls inside the transition "
                    f"window after shot {shot_id!r} ({win_start}-{win_end}s)"
                )


# ----------------------------------------------------------------- public

def build_timeline(script: dict, storyboard: dict, template: dict, language: str, *, style_lock: dict) -> dict:
    """The episode's output timeline: ``{"total_s", "fps", "total_frames",
    "shots", "lines", "sfx_anchors", "end_card"}``.

    - ``shots``: one entry per storyboard shot, in order --
      ``{shot_id, scene_id, start_s, duration_s, frames, motion,
      modifiers, transition_after}`` (module docstring's two distinct
      frame quantities; ``transition_after`` is ``{type, duration_s}`` or
      ``None`` for the very last shot under ``hard_stop``).
    - ``lines``: one entry per script line, in script order --
      ``{line_id, scene_id, start_s, duration_s}``, placed with
      :func:`timing.line_offsets` itself (never re-derived here), so a
      covering board's line starts equal ``timing.line_offsets``'s by
      construction -- a caller with its own boundary source can still call
      ``timing.line_offsets`` directly and compare, which is exactly what
      this module's own tests do.
    - ``sfx_anchors``: :func:`_sfx_anchors`.
    - ``end_card``: :func:`_end_card`'s dict when
      ``script["cliffhanger"]["cut_to_black"]``, else ``None``
      (``hard_stop``: "the last shot ends the file", spec 6.2).

    Raises :class:`TimelineError` (never a bare ``ValueError``/``KeyError``
    for a *consistency* failure, spec: "a named error") when: the
    shot-level total (summed independently from the storyboard's own shots
    and transitions) disagrees with :func:`timing.episode_pass`'s own
    total by more than a rounding epsilon; or any line falls inside a
    transition's blend window
    (:func:`_assert_no_line_in_a_transition_window`).
    """
    fps = profiles.FPS
    fadeblack_s = template["transitions_s"]["fadeblack"]
    card_s = template["end_card_s"]
    cut_to_black = script["cliffhanger"]["cut_to_black"]

    # The board's own timing: whole frames when it says so, else the timing
    # it was cut to (a board timed before phase 5 stage 6 renders exactly as
    # it did).
    timing_result, _scene_timings = timing_mod.episode_pass(
        script, template, language, style_lock=style_lock, storyboard=storyboard,
        whole_frames=timing_mod.board_whole_frames(storyboard))

    shot_entries, last_shot_natural_end = _shots_timeline(
        storyboard, cut_to_black=cut_to_black, fadeblack_s=fadeblack_s, card_s=card_s, fps=fps)
    end_card = _end_card(cut_to_black, last_shot_natural_end=last_shot_natural_end,
                          fadeblack_s=fadeblack_s, card_s=card_s)

    # cross-check: our own shot-level total must equal episode_pass's.
    real_shots = storyboard["shots"]
    raw_duration_total = sum(shot["duration_s"] for shot in real_shots)
    real_transition_total = sum(t["duration_s"] for t in storyboard["transitions"])
    end_card_addition = (card_s - fadeblack_s) if cut_to_black else 0.0
    computed_total = raw_duration_total - real_transition_total + end_card_addition
    if abs(computed_total - timing_result["total_s"]) > 1e-3:
        raise TimelineError(
            f"timeline total {computed_total:.3f}s disagrees with episode_pass's total "
            f"{timing_result['total_s']:.3f}s"
        )
    total_s = timing_result["total_s"]

    boundary_board = storyboard if timing_mod.covers(storyboard, script) else None
    scene_starts_map = timing_mod.scene_starts(script, timing_result, template, storyboard=boundary_board)
    offsets = timing_mod.line_offsets(script, timing_result, template, storyboard=boundary_board)

    line_id_to_scene = {}
    for scene in script["scenes"]:
        for line in scene["lines"]:
            line_id_to_scene[line["line_id"]] = scene["scene_id"]

    lines = []
    line_starts_by_id = {}
    for scene in script["scenes"]:
        for line in scene["lines"]:
            lid = line["line_id"]
            start, end = offsets[lid]
            lines.append({"line_id": lid, "scene_id": scene["scene_id"], "start_s": start,
                          "duration_s": round(end - start, 3)})
            line_starts_by_id[lid] = start

    sfx_anchors = _sfx_anchors(script, scene_starts=scene_starts_map, line_starts_by_id=line_starts_by_id)

    if storyboard.get("timing_mode") != "native_speech":
        # Plan 22: a native board's boundaries are all cuts, and a clip's own
        # speech may run under the end card's fade-in (the clip is heard to its
        # last frame): only the other boards keep spec 6.4's window rule.
        _assert_no_line_in_a_transition_window(shot_entries, lines)

    return {
        "total_s": total_s,
        "fps": fps,
        "total_frames": round(total_s * fps),
        "shots": shot_entries,
        "lines": lines,
        "sfx_anchors": sfx_anchors,
        "end_card": end_card,
    }
