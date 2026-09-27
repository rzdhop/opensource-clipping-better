"""Timing engine for AI Story episodes (spec 6.4): turns a script's text into
scene, line, shot and episode durations, and the reverse (a word budget for
the writer prompts, spec 4.2 E2).

The model never writes a duration. A line's real duration comes from TTS
audio once one has been synthesised (``line["timing"]["source"] in
("tts_word_timestamps", "audio_duration_only")``, written by the voice
measurement step, phase 3 stage 7); until then every function here works
from a deterministic character-count estimate. Nothing is a silent
fallback: :func:`line_duration` always reports which source it used, and an
unknown language is a named ``ValueError``, never a default.

``RATE_PER_CHAR`` and ``CHARS_PER_WORD``: the French figures were measured on
2026-09-27 on three Edge fr-FR/fr-CA voice samples (172 characters of
subtitle text, spoken in 12.03 s, 30 words including spaces and
punctuation); the English figures are authored, not measured, pending an
Edge en-US/en-GB sample set. ``MIN_LINE_S`` is a floor so a one-word line
still gets a shot worth of screen time.

Every function is pure and deterministic: no clock, no disk, no network.
Stdlib + ``clipping.aistory`` only (DEC-012; RC-P8), checked by
``tests/test_story_timing.py`` reading this file's own source.
"""

from __future__ import annotations

import hashlib
import math

from . import schemas

# --------------------------------------------------------------- constants

# Seconds per character of the whitespace-normalised line text.
RATE_PER_CHAR = {"fr": 0.070, "en": 0.065}

# Characters per word (including spaces and punctuation), used to turn a
# scene's speech budget in seconds into a word count for the writer prompts.
CHARS_PER_WORD = {"fr": 5.7, "en": 5.5}

# A line never estimates shorter than this, even a single short word.
MIN_LINE_S = 0.5


def _check_language(language: str) -> None:
    if language not in RATE_PER_CHAR:
        raise ValueError(f"unknown language: {language!r}")


def _clamp(value: float, lo: float, hi: float) -> float:
    return min(max(value, lo), hi)


def _normalise(text: str) -> str:
    """Strip and collapse runs of whitespace, the shared text-hash input."""
    return " ".join(text.split())


# ------------------------------------------------------------- line timing

def text_hash(text: str) -> str:
    """First 16 hex chars of the sha256 of *text*, whitespace-normalised.

    Used to detect a stale measured/estimated timing: a persisted
    ``timing.text_hash`` that no longer matches the line's current text
    means the audio (or the estimate) was made for different words.
    """
    normalised = _normalise(text)
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


def estimate_line(text: str, language: str) -> float:
    """A deterministic duration estimate before any audio exists."""
    _check_language(language)
    normalised = _normalise(text)
    seconds = len(normalised) * RATE_PER_CHAR[language]
    return round(max(MIN_LINE_S, seconds), 3)


def estimated_timing(text: str, language: str) -> dict:
    """A fresh ``timing`` block for a line that has never been measured."""
    return {
        "source": "estimated",
        "duration_s": estimate_line(text, language),
        "text_hash": text_hash(text),
        "voice": None,
        "audio": None,
    }


def line_duration(line: dict, language: str) -> tuple:
    """``(seconds, source)`` for one script line.

    The line's persisted ``timing`` (measured or estimated) is trusted only
    when its ``text_hash`` still matches the line's current text; an edited
    line falls back to a fresh estimate, source ``"estimated"`` -- the
    caller (the store's re-timing pass) is the one that labels a *stale*
    measured timing for the UI, this function just never uses it.
    """
    timing = line.get("timing")
    text = line["text"]
    if timing is not None and timing.get("text_hash") == text_hash(text):
        return timing["duration_s"], timing["source"]
    return estimate_line(text, language), "estimated"


# ------------------------------------------------------------- slot lookup

def slot_name(function: str, template: dict) -> str:
    """Which of the episode template's slots holds scene *function*."""
    for name, slot in template["slots"].items():
        if function in slot["functions"]:
            return name
    raise ValueError(f"no slot in the template holds function {function!r}")


def slot_range(scene: dict, template: dict, style_lock: dict = None) -> tuple:
    """The ``(lo, hi)`` duration range for *scene*'s slot.

    A style's ``episode_defaults.scene_clamp_s`` (spec 5, family_3d's
    ``{"peak": 12, "tender": 12}``) raises the upper bound when it names the
    scene's function or its emotion -- never lowers it, and both keys can
    apply to the same scene at once.
    """
    lo, hi = template["slots"][slot_name(scene["function"], template)]["duration_s"]
    if style_lock is not None:
        clamp = (style_lock.get("episode_defaults") or {}).get("scene_clamp_s") or {}
        candidates = [hi]
        for key in (scene["function"], scene.get("emotion")):
            if key in clamp:
                candidates.append(clamp[key])
        hi = max(candidates)
    return lo, hi


def tail_for(function: str, template: dict) -> float:
    """The natural (unshrunk) tail pause after *function*'s last line."""
    pauses = template["pauses_s"]
    if function in template["tail_peak_functions"]:
        return pauses["tail_peak"]
    return pauses["tail"]


# --------------------------------------------------------------- E1 slots

def episode_slots(template: dict, ep: int) -> list:
    """The ordered slot kinds episode *ep*'s beat sheet (E1) must fill
    exactly (spec 6.2, 4.2 row E1): ``"recap"`` (only from the template's own
    ``recap_from_episode`` on), then ``"hook"``, then ``"body"`` repeated *N*
    times, then ``"cliffhanger"``.

    *N* is the template's ``default_body_count`` clamped into the overlap of
    ``slots.body.count`` and whatever the episode's own ``scenes`` range
    leaves once the fixed slots (hook, cliffhanger, and the recap when it
    applies) are paid for -- the same bound ``schemas.episode_template_errors``
    checks the template against (kept local, not shared, the way
    ``prompts._e1_slot_bounds`` used to and ``prompts._slot_duration_range``
    still does, so this module needs no dependency the other way).

    Stage 12b: the live bench found E1 0/3 on both free links when asked for
    a *range* ("8 to 12 scenes") -- a model settled for fewer every time.
    An exact, positional ask (``prompts.build_e1``) and an exact, positional
    check (``prompts.validate_e1``) both key off this list.

    Pure and deterministic, like the rest of this module.
    """
    has_recap = ep >= template["recap_from_episode"]
    fixed = 2 + (1 if has_recap else 0)
    body_lo, body_hi = template["slots"]["body"]["count"]
    scenes_lo, scenes_hi = template["scenes"]
    lo = max(body_lo, scenes_lo - fixed)
    hi = min(body_hi, scenes_hi - fixed)
    n = min(max(template["default_body_count"], lo), hi)
    return (["recap"] if has_recap else []) + ["hook"] + ["body"] * n + ["cliffhanger"]


# ------------------------------------------------------------ scene timing

def scene_timing(scene: dict, template: dict, language: str, *, style_lock: dict = None,
                  tail_floor: float = None) -> dict:
    """One scene's duration, tail, hold and per-line start offsets.

    A scene with no lines (a quiet beat, a text-only recap or hook) just
    clamps its author-given ``target_duration_s`` into the slot range.
    Otherwise the raw layout is ``before_first_line + line durations +
    between_lines gaps + tail`` (see :func:`tail_for`); ``line_starts``
    follows that same layout, seconds from the scene's own start:

    * under the slot's low end -- the shortfall becomes ``hold_s`` (an
      extra beat held at the end), state stays ``"ok"``: landing on the
      slot minimum is normal, not a flag;
    * over the slot's high end -- the tail is shortened first, down to
      whichever is higher of the template's own ``tail_floor`` and the
      *tail_floor* keyword (the episode-level pass raises this per scene so
      a transition's fade never overlaps a line, spec 6.4). If that is
      enough, state is ``"tightened"``; if the tail is already at the floor
      and the scene is still over, state is ``"over"`` and the duration is
      the tightened raw length -- audio is never sped up, lines never cut.
    """
    lo, hi = slot_range(scene, template, style_lock=style_lock)
    lines = scene["lines"]
    pauses = template["pauses_s"]
    floor = pauses["tail_floor"]
    if tail_floor is not None:
        floor = max(floor, tail_floor)
    tail = tail_for(scene["function"], template)

    if not lines:
        duration = _clamp(scene["target_duration_s"], lo, hi)
        return {
            "duration_s": round(duration, 3), "tail_s": 0.0, "hold_s": 0.0,
            "speech_s": 0.0, "state": "ok", "line_starts": {},
        }

    before_first = pauses["before_first_line"]
    between = pauses["between_lines"]

    line_starts = {}
    t = before_first
    speech_s = 0.0
    for i, line in enumerate(lines):
        seconds, _source = line_duration(line, language)
        line_starts[line["line_id"]] = round(t, 3)
        speech_s += seconds
        t += seconds
        if i < len(lines) - 1:
            t += between

    raw = before_first + speech_s + between * (len(lines) - 1) + tail

    if raw < lo:
        hold = lo - raw
        duration = lo
        state = "ok"
        tail_used = tail
    elif raw > hi:
        excess = raw - hi
        max_shrink = max(0.0, tail - floor)
        shrink = min(excess, max_shrink)
        tail_used = tail - shrink
        raw_after = raw - shrink
        hold = 0.0
        if raw_after <= hi + 1e-9:
            duration = hi
            state = "tightened"
        else:
            duration = raw_after
            state = "over"
    else:
        hold = 0.0
        duration = raw
        state = "ok"
        tail_used = tail

    return {
        "duration_s": round(duration, 3), "tail_s": round(tail_used, 3), "hold_s": round(hold, 3),
        "speech_s": round(speech_s, 3), "state": state, "line_starts": line_starts,
    }


# ------------------------------------------------------------- word budget

def word_budget(function: str, target_hint: float, language: str, template: dict, *,
                 style_lock: dict = None) -> int:
    """The dialogue word budget a writer prompt gets for a scene (spec 4.2 E2).

    Mirrors :func:`scene_timing`'s layout in reverse: clamp the hint into
    the slot range, subtract the pre-roll, the tail and the gaps between
    the scene's expected lines (2 for a body function, 1 otherwise -- a
    body scene is written as a short exchange, the others as one line),
    and convert the seconds left over into words at this language's rate.
    Never fewer than 3 words, so a scene is always writable.
    """
    _check_language(language)
    slot = template["slots"][slot_name(function, template)]
    lo, hi = slot["duration_s"]
    if style_lock is not None:
        clamp = (style_lock.get("episode_defaults") or {}).get("scene_clamp_s") or {}
        if function in clamp:
            hi = max(hi, clamp[function])
    target = _clamp(target_hint, lo, hi)

    pauses = template["pauses_s"]
    expected_lines = 2 if function in schemas.BODY_FUNCTIONS else 1
    speech_s = target - pauses["before_first_line"] - tail_for(function, template) \
        - pauses["between_lines"] * (expected_lines - 1)

    seconds_per_word = RATE_PER_CHAR[language] * CHARS_PER_WORD[language]
    words = math.floor(speech_s / seconds_per_word + 1e-9)
    return max(3, words)


# --------------------------------------------------------------- transitions

def _boundary_kind(prev_place_id: str, next_place_id: str) -> str:
    """The spec 6.3 grammar between two scenes: same place dissolves, a
    place change fades to black."""
    return "dissolve" if prev_place_id == next_place_id else "fadeblack"


def plan_transitions(shots: list, scenes_by_id: dict, template: dict) -> list:
    """One transition per boundary between consecutive shots (spec 6.3, 6.5).

    A cut inside a scene; between scenes, a dissolve when both scenes share
    a ``place_id`` else a fade to black; durations come from the template's
    ``transitions_s``. The end card (not a shot) is never listed here.
    """
    transitions_s = template["transitions_s"]
    result = []
    for prev_shot, next_shot in zip(shots, shots[1:]):
        if prev_shot["scene_id"] == next_shot["scene_id"]:
            kind = "cut"
        else:
            prev_place = scenes_by_id[prev_shot["scene_id"]]["place_id"]
            next_place = scenes_by_id[next_shot["scene_id"]]["place_id"]
            kind = _boundary_kind(prev_place, next_place)
        result.append({"after": prev_shot["shot_id"], "type": kind, "duration_s": transitions_s[kind]})
    return result


def _boundary_from_storyboard(scenes: list, storyboard: dict) -> list:
    """``[(kind, duration_s), ...]``, one per scene-to-scene boundary of
    *scenes*, read from *storyboard*'s own shots and transitions -- matched
    by shot id, never by list position (a PATCHed transition, e.g. one
    turned into a ``cut``, changes the length of the non-cut list, so
    positional pairing silently misaligns every later boundary).

    A scene boundary is a pair of consecutive shots whose ``scene_id``
    differs; its transition is whichever entry names the earlier shot as
    ``after`` -- a boundary with none is an explicit ``cut`` (0 s), exactly
    like a shot list still awaiting T1. A non-cut transition whose ``after``
    shot and the next shot are in the *same* scene is refused (spec 6.3:
    only a cut is allowed inside a scene); ``schemas.storyboard_errors``
    carries the same rule so a bad storyboard is caught before it gets here.

    The storyboard's own scene sequence (its shots' scene ids, collapsed to
    one entry per contiguous run) must equal *scenes* filtered down to the
    scenes that actually have shots, in the same order -- otherwise the
    storyboard was built for a different script revision and every boundary
    below would be guessing; that is refused too, naming the first mismatch.
    """
    shots = storyboard["shots"]
    transitions_by_after = {t["after"]: t for t in storyboard["transitions"]}

    storyboard_scene_seq = []
    for shot in shots:
        sid = shot["scene_id"]
        if not storyboard_scene_seq or storyboard_scene_seq[-1] != sid:
            storyboard_scene_seq.append(sid)

    shots_scene_ids = set(storyboard_scene_seq)
    expected_seq = [scene["scene_id"] for scene in scenes if scene["scene_id"] in shots_scene_ids]
    if storyboard_scene_seq != expected_seq:
        for i, (got, want) in enumerate(zip(storyboard_scene_seq, expected_seq)):
            if got != want:
                raise ValueError(
                    f"storyboard scene sequence does not match the script at position {i}: "
                    f"storyboard has {got!r}, script has {want!r}"
                )
        raise ValueError(
            f"storyboard scene sequence {storyboard_scene_seq} does not match the script's "
            f"scenes with shots {expected_seq}"
        )

    boundary_by_pair = {}
    for prev_shot, next_shot in zip(shots, shots[1:]):
        transition = transitions_by_after.get(prev_shot["shot_id"])
        if prev_shot["scene_id"] == next_shot["scene_id"]:
            if transition is not None and transition["type"] != "cut":
                raise ValueError(
                    f"shot {prev_shot['shot_id']!r}: only 'cut' is allowed inside a scene, "
                    f"got {transition['type']!r}"
                )
            continue
        pair = (prev_shot["scene_id"], next_shot["scene_id"])
        if transition is None:
            boundary_by_pair[pair] = ("cut", 0.0)
        else:
            boundary_by_pair[pair] = (transition["type"], transition["duration_s"])

    boundary = []
    for prev_scene, next_scene in zip(scenes, scenes[1:]):
        pair = (prev_scene["scene_id"], next_scene["scene_id"])
        if pair not in boundary_by_pair:
            raise ValueError(
                f"storyboard has no shot boundary between scenes {pair[0]!r} and {pair[1]!r}"
            )
        boundary.append(boundary_by_pair[pair])
    return boundary


def _boundary_transitions(scenes: list, template: dict, storyboard: dict = None) -> list:
    """``[(kind, duration_s), ...]``, one per scene-to-scene boundary, in
    scene order. Read from *storyboard* (``storyboard_v1``) when given, via
    :func:`_boundary_from_storyboard`; otherwise predicted with
    :func:`_boundary_kind`."""
    if storyboard is not None:
        return _boundary_from_storyboard(scenes, storyboard)
    transitions_s = template["transitions_s"]
    result = []
    for prev_scene, next_scene in zip(scenes, scenes[1:]):
        kind = _boundary_kind(prev_scene["place_id"], next_scene["place_id"])
        result.append((kind, transitions_s[kind]))
    return result


# ---------------------------------------------------------------- shots

def allocate_shots(scene: dict, scene_t: dict, shots: list, template: dict) -> tuple:
    """Split one scene's duration across its shots. Returns ``(durations,
    extra_hold_s)``.

    1. If even the minimum shot length does not fit every shot
       (``len(shots) * min_shot_s > duration``), every shot gets exactly
       ``min_shot_s`` and the shortfall is reported as ``extra_hold_s`` (a
       caller holds the last frame, or the episode-level window pass should
       already have grown the scene before this is reached).
    2. If no shot is anchored (every ``lines`` list is empty), the duration
       is split evenly, the last shot absorbing the rounding remainder.
    3. Otherwise an anchored shot (non-empty ``lines``) owns the span from
       its first line's start (``scene_t["line_starts"]``) to the next
       anchored shot's first line start; the scene's first shot always
       starts at 0 and its last shot always ends at the scene's duration,
       so the spans partition ``[0, duration]`` exactly. Every unanchored
       shot is owned by the nearest anchored shot *before* it in the scene
       (an in-between reaction cut, or a trailing shot after the last
       line); a leading shot with no anchor before it yet (an establishing
       shot) is owned by the next one instead. Each unanchored shot then
       takes ``max(min_shot_s, 1.0)`` seconds out of its owner's span; the
       owner keeps the rest. Because an owner only ever gives up time from
       *after* its own start, every anchored shot beyond the first lands
       exactly on its first line's start -- the first one only does too
       when nothing precedes it.

    As a safety net -- an unusually front- or back-loaded line layout could
    in principle leave one owner's span short of even its own dependents'
    minimums, despite the scene as a whole having room -- any allocation
    that would put a shot under ``min_shot_s`` falls back to the even split
    of step 2 instead. Still deterministic, still exactly sums to the scene
    duration, still respects the minimum.
    """
    duration = scene_t["duration_s"]
    min_shot = template["min_shot_s"]
    n = len(shots)
    if n == 0:
        return [], 0.0

    def _round_last(values):
        rounded = [round(v, 3) for v in values[:-1]]
        rounded.append(round(duration - sum(rounded), 3))
        return rounded

    if n * min_shot > duration + 1e-9:
        shortfall = n * min_shot - duration
        return [round(min_shot, 3)] * n, round(shortfall, 3)

    def _even_split():
        share = duration / n
        return _round_last([share] * n), 0.0

    anchor_positions = [i for i, shot in enumerate(shots) if shot.get("lines")]
    if not anchor_positions:
        return _even_split()

    line_starts = scene_t["line_starts"]
    anchor_starts = [line_starts[shots[i]["lines"][0]] for i in anchor_positions]

    spans = []
    for k in range(len(anchor_positions)):
        start = 0.0 if k == 0 else anchor_starts[k]
        end = duration if k == len(anchor_positions) - 1 else anchor_starts[k + 1]
        spans.append((start, end))

    is_anchor = [bool(shot.get("lines")) for shot in shots]
    owner_of = [None] * n
    for k, i in enumerate(anchor_positions):
        owner_of[i] = k

    last_k = None
    for i in range(n):
        if is_anchor[i]:
            last_k = owner_of[i]
        elif last_k is not None:
            owner_of[i] = last_k

    next_k = None
    for i in range(n - 1, -1, -1):
        if is_anchor[i]:
            next_k = owner_of[i]
        elif owner_of[i] is None:
            owner_of[i] = next_k

    carve = max(min_shot, 1.0)
    remaining = [end - start for start, end in spans]
    durations = [None] * n
    for i in range(n):
        if not is_anchor[i]:
            k = owner_of[i]
            durations[i] = carve
            remaining[k] -= carve
    for k, i in enumerate(anchor_positions):
        durations[i] = remaining[k]

    if any(d < min_shot - 1e-9 for d in durations):
        return _even_split()

    return _round_last(durations), 0.0


# ------------------------------------------------------------- episode timing

def _effective_tail_floor(index: int, scenes: list, boundary: list, template: dict, script: dict) -> float:
    """The tail floor for ``scenes[index]``: never below the duration of
    whatever transition leaves it, so a dissolve or fade never eats into a
    line (spec 6.4). The floor for the last scene also covers the fade
    that joins the 1.0 s end card, when the cliffhanger cuts to black."""
    floor = template["pauses_s"]["tail_floor"]
    if index < len(boundary):
        floor = max(floor, boundary[index][1])
    elif index == len(scenes) - 1 and script["cliffhanger"]["cut_to_black"]:
        floor = max(floor, template["transitions_s"]["fadeblack"])
    return floor


def _longest_lines(scenes: list, language: str) -> list:
    """``[(scene_id, line_id, duration_s), ...]`` for every line in the
    episode, longest first; ties keep scene/line order (stable sort)."""
    lines = []
    for scene in scenes:
        for line in scene["lines"]:
            duration, _source = line_duration(line, language)
            lines.append((scene["scene_id"], line["line_id"], duration))
    return sorted(lines, key=lambda item: item[2], reverse=True)


def episode_timing(script: dict, template: dict, language: str, *, style_lock: dict = None,
                    storyboard: dict = None) -> dict:
    """The episode's ``timing`` object (spec 2.7): total length, per-scene
    durations and the window state, computed end to end from the script's
    text (or its already-measured lines).

    Overlaps: with a ``storyboard_v1`` document given, the episode length
    subtracts each scene boundary's transition duration, matched to the
    storyboard's own shots by id (see :func:`_boundary_from_storyboard`);
    without one, the same scene-boundary grammar as :func:`plan_transitions`
    is used to predict them (spec 6.4). The 1.0 s end card is added, minus the
    fadeblack duration that joins it (they overlap), only when
    ``cliffhanger.cut_to_black`` is true; ``hard_stop`` adds nothing.

    Window handling (spec 6.4, the human's window/tighten answer of
    2026-09-27): above ``tighten_above_s``, tails are shortened toward
    their floors, longest tail first, only as much as needed. If that
    brings the total to ``tighten_above_s`` or below, or leaves it inside
    the window, the state is ``"tightened"``; if the window's high end is
    still exceeded, the state is ``"over"`` and the longest lines in the
    episode are flagged for trimming, longest first, until half the sum of
    the flagged lines' durations covers the excess. Below the window's low
    end, holds are extended (up to ``hold_extension_max_s`` each, never
    past a scene's own slot maximum) -- first on the cliffhanger scene,
    then on the first scene at each place in the order the places appear;
    if that is not enough the state is ``"under"``. Independently of the
    episode's own state, any scene whose *own* timing came out ``"over"``
    (it did not fit even after its own tail was shrunk to the floor) adds
    a ``scene_over`` flag and a ``trim_line`` flag on its own longest line.
    """
    scenes = script["scenes"]
    boundary = _boundary_transitions(scenes, template, storyboard)

    scene_timings = {}
    slot_hi_by_scene = {}
    for i, scene in enumerate(scenes):
        floor = _effective_tail_floor(i, scenes, boundary, template, script)
        scene_t = scene_timing(scene, template, language, style_lock=style_lock, tail_floor=floor)
        scene_timings[scene["scene_id"]] = scene_t
        slot_hi_by_scene[scene["scene_id"]] = slot_range(scene, template, style_lock=style_lock)[1]

    end_card_addition = 0.0
    if script["cliffhanger"]["cut_to_black"]:
        end_card_addition = template["end_card_s"] - template["transitions_s"]["fadeblack"]

    def _current_total() -> float:
        return sum(scene_timings[s["scene_id"]]["duration_s"] for s in scenes) \
            - sum(dur for _kind, dur in boundary) + end_card_addition

    total = _current_total()
    window_lo, window_hi = template["window_s"]
    tighten_above = template["tighten_above_s"]
    flags = []
    state = "ok"

    if total > tighten_above:
        need = total - tighten_above
        by_tail_desc = sorted(scenes, key=lambda s: scene_timings[s["scene_id"]]["tail_s"], reverse=True)
        for scene in by_tail_desc:
            if need <= 1e-9:
                break
            sid = scene["scene_id"]
            i = scenes.index(scene)
            floor = _effective_tail_floor(i, scenes, boundary, template, script)
            scene_t = scene_timings[sid]
            room = max(0.0, scene_t["tail_s"] - floor)
            shrink = min(need, room)
            if shrink <= 0:
                continue
            scene_timings[sid] = {
                **scene_t,
                "tail_s": round(scene_t["tail_s"] - shrink, 3),
                "duration_s": round(scene_t["duration_s"] - shrink, 3),
            }
            need -= shrink
        total = _current_total()
        state = "tightened"
        if total > window_hi:
            state = "over"
            excess = total - window_hi
            flags.append({
                "kind": "episode_over", "scene_id": None, "line_id": None, "seconds": round(excess, 3),
                "message": f"The episode is {excess:.1f} s over {window_hi:g} s.",
            })
            longest = _longest_lines(scenes, language)
            flagged_sum = 0.0
            for sid, lid, dur in longest:
                if flagged_sum >= excess / 2:
                    break
                flags.append({
                    "kind": "trim_line", "scene_id": sid, "line_id": lid, "seconds": round(dur, 3),
                    "message": f"Trim this line: the episode is {excess:.1f} s over {window_hi:g} s",
                })
                flagged_sum += dur

    elif total < window_lo:
        need = window_lo - total
        cliff = next((s for s in scenes if s["function"] == "cliffhanger"), None)
        order = []
        if cliff is not None:
            order.append(cliff["scene_id"])
        seen_places = set()
        for scene in scenes:
            if scene["place_id"] not in seen_places:
                seen_places.add(scene["place_id"])
                if cliff is None or scene["scene_id"] != cliff["scene_id"]:
                    order.append(scene["scene_id"])

        for sid in order:
            if need <= 1e-9:
                break
            scene_t = scene_timings[sid]
            hi = slot_hi_by_scene[sid]
            room = max(0.0, hi - scene_t["duration_s"])
            extend = min(need, template["hold_extension_max_s"], room)
            if extend <= 0:
                continue
            scene_timings[sid] = {
                **scene_t,
                "hold_s": round(scene_t["hold_s"] + extend, 3),
                "duration_s": round(scene_t["duration_s"] + extend, 3),
            }
            need -= extend
        total = _current_total()
        if total < window_lo:
            state = "under"
            shortfall = window_lo - total
            flags.append({
                "kind": "episode_under", "scene_id": None, "line_id": None, "seconds": round(shortfall, 3),
                "message": f"The episode is {shortfall:.1f} s under {window_lo:g} s.",
            })
        else:
            state = "ok"

    for scene in scenes:
        sid = scene["scene_id"]
        if scene_timings[sid]["state"] != "over":
            continue
        over = scene_timings[sid]["duration_s"] - slot_hi_by_scene[sid]
        flags.append({
            "kind": "scene_over", "scene_id": sid, "line_id": None, "seconds": round(max(0.0, over), 3),
            "message": f"Scene {sid} is {max(0.0, over):.1f} s over its {slot_hi_by_scene[sid]:g} s slot.",
        })
        scene_lines = sorted(
            ((line["line_id"], line_duration(line, language)[0]) for line in scene["lines"]),
            key=lambda item: item[1], reverse=True,
        )
        if scene_lines:
            longest_line_id, longest_duration = scene_lines[0]
            flags.append({
                "kind": "trim_line", "scene_id": sid, "line_id": longest_line_id,
                "seconds": round(longest_duration, 3),
                "message": f"Trim this line: scene {sid} is {max(0.0, over):.1f} s over its slot.",
            })

    estimated_lines = 0
    measured_lines = 0
    for scene in scenes:
        for line in scene["lines"]:
            _duration, source = line_duration(line, language)
            if source == "estimated":
                estimated_lines += 1
            else:
                measured_lines += 1

    return {
        "total_s": round(total, 3),
        "window_s": [window_lo, window_hi],
        "target_s": template["target_s"],
        "state": state,
        "scenes": {
            sid: {
                "duration_s": scene_timings[sid]["duration_s"],
                "tail_s": scene_timings[sid]["tail_s"],
                "hold_s": scene_timings[sid]["hold_s"],
                "state": scene_timings[sid]["state"],
            }
            for sid in scene_timings
        },
        "flags": flags,
        "estimated_lines": estimated_lines,
        "measured_lines": measured_lines,
    }


def line_offsets(script: dict, timing: dict, template: dict, *, storyboard: dict = None) -> dict:
    """Absolute ``{line_id: (start_s, end_s)}`` for every line in the
    episode, from an already-computed :func:`episode_timing` result.

    A scene's own start is the running sum of the *previous* scenes'
    (already window-adjusted) durations, minus the overlap of the
    transition entering it -- the same overlap :func:`episode_timing`
    subtracted from the total (matched to the storyboard's shots by id, see
    :func:`_boundary_from_storyboard`, when *storyboard* is given), so this
    reconstructs the same timeline. A line's duration is read straight from
    its persisted ``timing`` (the caller is expected to have already
    re-timed the script), not re-estimated: this function only places
    lines, it never times them.
    """
    scenes = script["scenes"]
    boundary = _boundary_transitions(scenes, template, storyboard)
    pauses = template["pauses_s"]

    offsets = {}
    scene_start = 0.0
    for i, scene in enumerate(scenes):
        if i > 0:
            prev_sid = scenes[i - 1]["scene_id"]
            prev_duration = timing["scenes"][prev_sid]["duration_s"]
            scene_start += prev_duration - boundary[i - 1][1]

        t = pauses["before_first_line"]
        lines = scene["lines"]
        for j, line in enumerate(lines):
            duration = line["timing"]["duration_s"]
            start = scene_start + t
            offsets[line["line_id"]] = (round(start, 3), round(start + duration, 3))
            t += duration
            if j < len(lines) - 1:
                t += pauses["between_lines"]

    return offsets
