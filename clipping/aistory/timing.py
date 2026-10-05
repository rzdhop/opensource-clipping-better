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

# How much longer a provider's voices speak a line than the per-character
# rate above says (DEC-250): the French rate was measured on Edge voices;
# Gemini's prebuilt voices ran 1.16-1.80x the estimate over the 18 lines of
# story d0ee5ebd745d's first episode (2026-10-03; mean 1.35, speech alone,
# pauses aside -- A-134). A provider not named speaks at the estimate (1.0).
# Plan 24 stage 1 (D-1, the one clock): the table lives here so the estimate,
# the line plan and the storyboard read one number; ``voices.SPEECH_OVERRUN``
# is this very table.
SPEECH_OVERRUN = {"gemini": 1.35}

# Characters per word of written v3 French (plan 24 stage 1, D-1): measured
# on the scripts of 2026-10-05 at 6.6 (6.2-7.3 a scene) -- the 5.7 of
# CHARS_PER_WORD turned seconds into ~15 % too many words. Used only to turn
# a line plan's seconds into words (:func:`scene_plan`); the v1/v2
# :func:`word_budget` keeps CHARS_PER_WORD.
CHARS_PER_WORD_V3 = {"fr": 6.6, "en": 5.5}

# The render's frame rate (``render.profiles.FPS``: kept here so this module
# stays pure; ``tests/test_story_frame_stable_timing.py`` checks the two
# agree). Timed in whole frames (the ``whole_frames`` keyword below, DEC-142
# as amended by AI Story phase 5 stage 6), every scene and shot lasts a whole
# number of these frames, stored as ``round(n / FPS, 3)`` so that
# ``round(d * FPS) == n``: a change in one scene then moves every later shot
# by whole frames only, and their frame counts (and render cache keys) stay.
FPS = 30


def _to_frames(seconds: float) -> int:
    """The nearest whole frame of *seconds*, ties up (never banker's
    rounding)."""
    return int(math.floor(seconds * FPS + 0.5 + 1e-9))


def _from_frames(frames: int) -> float:
    """*frames* in seconds, as a duration is stored (3 decimals)."""
    return round(frames / FPS, 3)


def _frames_down(seconds: float) -> int:
    """The whole frames that fit in *seconds* (rounded down; a hair of float
    noise under a whole frame still counts it)."""
    return int(math.floor(seconds * FPS + 1e-6))


def board_whole_frames(storyboard) -> bool:
    """Whether a script timed beside *storyboard* -- and the storyboard's
    own shots -- are timed in whole frames: yes for a storyboard that says
    so (``whole_frames: true``, written by ``shots.build_storyboard`` since
    phase 5 stage 6, or by the first full re-time of an older one,
    ``shots.retime_storyboard``) and for no storyboard at all (the next one
    is built in whole frames); no for a storyboard timed before, which keeps
    -- and renders in -- the timing it was cut to."""
    return storyboard is None or bool(storyboard.get("whole_frames"))


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


def speech_factor(provider: str = None, factor: float = None) -> float:
    """How much longer than the per-character rate a line is spoken: *factor*
    when given (a story's measured per-voice rate), else the provider's
    :data:`SPEECH_OVERRUN`, else 1.0 (plan 24 stage 1, D-1)."""
    if factor is not None:
        return float(factor)
    return float(SPEECH_OVERRUN.get(provider, 1.0)) if provider else 1.0


def estimate_line(text: str, language: str, *, provider: str = None, factor: float = None) -> float:
    """A deterministic duration estimate before any audio exists.

    Plan 24 stage 1 (D-1): times the speaking voice's :func:`speech_factor`
    (its *provider*'s overrun, or a measured *factor*); with neither -- every
    call made before -- the result is exactly what it always was."""
    _check_language(language)
    normalised = _normalise(text)
    seconds = len(normalised) * RATE_PER_CHAR[language]
    base = round(max(MIN_LINE_S, seconds), 3)
    over = speech_factor(provider, factor)
    if over == 1.0:
        return base
    return round(base * over, 3)


def seconds_for(text: str, lang: str, *, provider: str = None, factor: float = None) -> float:
    """The one speech clock (plan 24 stage 1, D-1): how long *text* lasts in
    language *lang* spoken by a voice of *provider* (``"edge"``,
    ``"gemini"``, ...; None: the measured Edge rate), or at a measured
    *factor* over the per-character rate. The Script step's estimate
    (:func:`estimated_timing`), the line plan (:func:`scene_plan`) and the
    storyboard read this one number."""
    return estimate_line(text, lang, provider=provider, factor=factor)


def estimated_timing(text: str, language: str, *, provider: str = None, factor: float = None) -> dict:
    """A fresh ``timing`` block for a line that has never been measured.

    Plan 24 stage 1 (D-1, D-5): spoken by a voice whose
    :func:`speech_factor` is not 1.0 (a Gemini voice), the estimate carries
    it and the block records it as ``speech_factor`` -- so nothing adds the
    overrun a second time (:func:`estimate_carries_overrun`). Any other
    block is byte for byte what it always was."""
    over = speech_factor(provider, factor)
    block = {
        "source": "estimated",
        "duration_s": estimate_line(text, language, factor=over),
        "text_hash": text_hash(text),
        "voice": None,
        "audio": None,
    }
    if over != 1.0:
        block["speech_factor"] = over
    return block


def estimate_carries_overrun(line: dict) -> bool:
    """Whether *line*'s current timing is an estimate that already carries
    its voice's overrun (:func:`estimated_timing`'s ``speech_factor``, for
    the line's current text): such a line's estimate needs no overrun added
    (``storyboard.expected_scene_seconds``)."""
    current = line.get("timing") or {}
    return (current.get("source") == "estimated" and current.get("speech_factor") is not None
            and current.get("text_hash") == text_hash(line["text"]))


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
                  tail_floor: float = None, min_duration_s: float = None, whole_frames: bool = True) -> dict:
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

    *min_duration_s* is the length the scene's storyboard shots need
    (``len(shots) * min_shot_s``, :func:`_shot_floors`): both ends of the
    slot are raised to it, so a scene is never shorter than its shots -- the
    shortfall is held like any other (``hold_s``) and a tail is never
    shortened below it. ``line_starts`` never move: a hold is at the end.

    *whole_frames* (the default; phase 5 stage 6): the duration becomes a
    whole number of frames (:data:`FPS`) -- the nearest one, ties up, the
    difference going into what follows the last line (the hold when there
    is one, else the tail), so no line moves; the next frame up instead when
    rounding down would take that hold under zero or that tail under its
    floor (a scene tightened to the floor, or ``"over"``). Off, the scene is timed exactly
    as before phase 5 stage 6 (a storyboard timed then still renders in it,
    :func:`board_whole_frames`).
    """
    lo, hi = slot_range(scene, template, style_lock=style_lock)
    if min_duration_s is not None:
        lo, hi = max(lo, min_duration_s), max(hi, min_duration_s)
    lines = scene["lines"]
    pauses = template["pauses_s"]
    floor = pauses["tail_floor"]
    if tail_floor is not None:
        floor = max(floor, tail_floor)
    tail = tail_for(scene["function"], template)

    if not lines:
        duration = _clamp(scene["target_duration_s"], lo, hi)
        if whole_frames:
            # The slot's ends are whole frames: the nearest one stays inside.
            duration = _from_frames(_to_frames(duration))
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

    if whole_frames:
        frames = _to_frames(duration)
        delta = frames / FPS - duration
        spare = hold if hold > 0.0 else tail_used - floor
        if delta < 0.0 and spare + delta < -1e-9:
            frames += 1
            delta = frames / FPS - duration
        if hold > 0.0:
            hold += delta
        else:
            tail_used += delta
        duration = _from_frames(frames)

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


# ---------------------------------------------------- writing v3 (plan 22 stage 3)
#
# The word budget a writing-v3 call states (E2v3, E3v3): words for the scene,
# lines for it and words a line. :func:`word_budget` above stays byte for
# byte what it was (every v1/v2 call reads it, RC-W3).

# Words a second of speech, the native-speech shot plan's measure
# (``native_speech.SPEECH_WPS``, A-148; kept here as a number so this module
# keeps its imports): 4 s hold 7 words, 6 s 12, 8 s 17.
SPEECH_WPS_V3 = 2.4
# A line's [lo, hi] words when the template names none: a native-speech line
# is one shot of at most 8 s (17 words); any other line keeps the v2 cap.
LINE_WORDS_NATIVE = (5, 17)
LINE_WORDS_DEFAULT = (5, 22)
# A scene's line count: at most the script's four lines a scene.
V3_LINES_MAX = 4
# Below this share of its budget a scene's ask starts (the v2 ask's 0.7).
V3_WORDS_FLOOR_SHARE = 0.75


def line_words_v3(template: dict, *, native: bool = False) -> tuple:
    """A line's ``(lo, hi)`` words on a writing-v3 story: the template's
    ``line_words``, else :data:`LINE_WORDS_NATIVE` on a native-speech story
    (*native*), else :data:`LINE_WORDS_DEFAULT`."""
    pair = (template or {}).get("line_words")
    if pair:
        return int(pair[0]), int(pair[1])
    return LINE_WORDS_NATIVE if native else LINE_WORDS_DEFAULT


def word_budget_v3(scene: dict, template: dict, episode_words=None, *, total_s: float = None,
                   native: bool = False) -> dict:
    """The words a writing-v3 call asks of *scene* (plan 22 stage 3)::

        {"words": [lo, hi], "lines": [lo, hi], "line_words": [lo, hi]}

    With *episode_words* (the template's ``episode_words``, ``[lo, hi]``)
    the episode's spoken words are spread over its scenes by their share of
    its length: this scene's ``target_duration_s`` over *total_s* (the sum
    of every scene's target; the template's ``target_s`` when not given) --
    the low end rounded up and the high end down, so the scenes' budgets
    sum inside *episode_words* whenever the high end leaves a word a scene
    of room. Without it: ``target_duration_s x 2.4 x (1 - silent share)``,
    the silent share the scene's pre-roll and tail over its length (at most
    half), the low end :data:`V3_WORDS_FLOOR_SHARE` of it. The lines follow
    from the words and :func:`line_words_v3`: at least the words over a
    line's high end, at most the words over a line's middle, within
    1-:data:`V3_LINES_MAX`. Never fewer than 3 words. Pure."""
    target = float(scene["target_duration_s"])
    line_lo, line_hi = line_words_v3(template, native=native)
    if episode_words:
        total = float(total_s or template["target_s"]) or 1.0
        share = target / total
        lo = math.ceil(episode_words[0] * share - 1e-9)
        hi = math.floor(episode_words[1] * share + 1e-9)
    else:
        pauses = template["pauses_s"]
        silent = pauses["before_first_line"] + tail_for(scene["function"], template)
        silent_share = min(0.5, silent / target) if target > 0 else 0.5
        hi = math.floor(target * SPEECH_WPS_V3 * (1 - silent_share) + 1e-9)
        lo = math.floor(hi * V3_WORDS_FLOOR_SHARE + 1e-9)
    hi = max(3, hi)
    lo = min(max(3, lo), hi)
    middle = (line_lo + line_hi) / 2
    lines_lo = min(V3_LINES_MAX, max(1, math.ceil(lo / line_hi - 1e-9)))
    lines_hi = min(V3_LINES_MAX, max(lines_lo, math.ceil(hi / middle - 1e-9)))
    return {"words": [lo, hi], "lines": [lines_lo, lines_hi], "line_words": [line_lo, line_hi]}


# ------------------------------------------------- the line plan (plan 24 stage 1)
#
# D-2: before a scene is written, its slot's high end is split into one
# slot a line -- every pause paid, a margin kept -- and each line slot turned
# into a hard word cap with the one clock (:func:`seconds_for`), so a reply
# inside its caps can never run over its slot. Stage 1 computes and stores
# the plan (``slot_s``, ``line_plan`` on the scene); the writer and the
# storyboard read it from stage 2 on. :func:`word_budget_v3` is untouched.

# The share of a scene's speech seconds the plan keeps aside: the estimate's
# noise (a word a little longer than CHARS_PER_WORD_V3, a frame of rounding).
PLAN_MARGIN = 0.05
# The narrator's share of a scene's speech when the template names none and
# the scene has both a narrator and a character line.
PLAN_NARRATOR_SHARE = 0.5
# Speech lengths a native-speech clip is planned at when the caller names
# none (``native_speech.SPEECH_LENGTHS``, Veo's 4/6/8 s; kept as numbers so
# this module keeps its imports) and the seconds of a clip that are not
# speech (``native_speech.SPEECH_OVERHEAD_S``).
PLAN_SPEECH_LENGTHS = (4, 6, 8)
PLAN_SPEECH_OVERHEAD_S = 0.7
# Plan 24 stage 2 (D-2 amended): on a native-speech scene with a narrator, a
# character line is planned first at this clip (snapped down to the link's
# lengths) -- 6 s holds 12 words, room for the reason inside the line; the
# midpoint split left it 4 s / 7 words, too tight for the format.
PLAN_CHARACTER_CLIP_S = 6


def seconds_per_word_v3(lang: str, *, provider: str = None, factor: float = None) -> float:
    """Seconds one written v3 word lasts on the one clock:
    ``CHARS_PER_WORD_V3 x RATE_PER_CHAR x`` the voice's
    :func:`speech_factor` (plan 24 stage 1)."""
    _check_language(lang)
    return CHARS_PER_WORD_V3[lang] * RATE_PER_CHAR[lang] * speech_factor(provider, factor)


def words_for_seconds(seconds: float, lang: str, *, provider: str = None, factor: float = None) -> int:
    """The most words that fit in *seconds* of speech on the one clock
    (rounded down; never below 0)."""
    per_word = seconds_per_word_v3(lang, provider=provider, factor=factor)
    return max(0, math.floor(max(0.0, seconds) / per_word + 1e-9))


def plan_tail_floor(template: dict) -> float:
    """The tail floor a scene's plan pays when the caller knows nothing of
    its neighbours: the template's ``tail_floor``, raised to the longest
    dissolve or fade the predicted grammar may leave it with (spec 6.3) --
    unless the template cuts every boundary (:func:`cuts_between_scenes`)."""
    floor = template["pauses_s"]["tail_floor"]
    if cuts_between_scenes(template):
        return floor
    transitions_s = template["transitions_s"]
    return max([floor] + [transitions_s[kind] for kind in ("dissolve", "fadeblack") if kind in transitions_s])


def plan_tail_floors(script: dict, template: dict) -> dict:
    """``{scene_id: tail floor}`` for every scene of *script* as the Script
    step times it (no storyboard yet: the predicted boundaries) --
    :func:`_effective_tail_floor`, the floor the warning's own pass shrinks a
    tail to, so the plan pays exactly what the warning will charge."""
    scenes = script["scenes"]
    boundary = _boundary_transitions(scenes, template, None)
    return {scene["scene_id"]: _effective_tail_floor(i, scenes, boundary, template, script)
            for i, scene in enumerate(scenes)}


def _plan_speakers(scene: dict, *, narrator: bool, speakers: dict, line_count_hint) -> list:
    """The planned speakers of *scene*, in line order: with the narrator on
    in the scene, the narrator first and -- a body scene naming a character
    -- one character line after it; without it, a body scene's exchange
    between its first two characters (or its one character twice), any
    other scene one line of its first character. *line_count_hint* (1-4)
    sets the count instead, the narrator first when on, then the scene's
    characters in turn. The characters are the scene's own (E1 named
    them), else *speakers*' handles."""
    characters = [cid for cid in scene.get("characters") or () if cid != "narrator"]
    if not characters:
        characters = [handle for handle in (speakers or {}) if handle != "narrator"]
    body = scene["function"] in schemas.BODY_FUNCTIONS
    if line_count_hint is not None:
        count = min(V3_LINES_MAX, max(1, int(line_count_hint)))
        out = ["narrator"] if narrator else []
        k = 0
        while len(out) < count:
            if not characters:
                out.append("narrator")
                continue
            out.append(characters[k % len(characters)])
            k += 1
        return out
    if narrator:
        return ["narrator", characters[0]] if body and characters else ["narrator"]
    if not characters:
        return []
    if body:
        return [characters[0], characters[1] if len(characters) > 1 else characters[0]]
    return [characters[0]]


def _snap_down(seconds: float, lengths) -> int:
    """The longest of *lengths* not past *seconds*, else the shortest."""
    ordered = sorted(lengths)
    fit = [length for length in ordered if length <= seconds + 1e-9]
    return int(fit[-1] if fit else ordered[0])


def _snap_up(seconds: float, lengths) -> int:
    """The shortest of *lengths* at least *seconds* long, else the longest
    (``native_speech.silent_clip_s``)."""
    ordered = sorted(lengths)
    return int(next((length for length in ordered if length >= seconds - 1e-9), ordered[-1]))


def _clip_capacity(clip_s: float) -> int:
    """``native_speech.capacity``: the words a speaking clip of *clip_s*
    seconds holds, ``floor((L - 0.7) x 2.4)`` (4 s: 7, 6 s: 12, 8 s: 17)."""
    return max(0, math.floor((float(clip_s) - PLAN_SPEECH_OVERHEAD_S) * SPEECH_WPS_V3 + 1e-9))


def scene_plan(template: dict, scene: dict, *, lang: str, native: bool, narrator_provider: str = None,
               speakers: dict = None, line_count_hint: int = None, narrator: bool = None,
               style_lock: dict = None, tail_floor: float = None, speech_lengths=PLAN_SPEECH_LENGTHS,
               silent_lengths=None) -> dict:
    """The line plan of *scene* (plan 24 stage 1, D-2)::

        {"slot_s": [lo, hi], "allowed_speech_s": s,
         "lines": [{"kind": "narrator" | "character", "speaker", "seconds", "clip_s" (native only),
                    "max_words"}, ...],
         "max_words": sum of the caps, "min_words": floor(0.5 x it)}

    The slot's high end (:func:`slot_range`, the style lock's clamp
    included) pays the pre-roll, a gap between each pair of lines and the
    tail floor (*tail_floor*: the scene's own, :func:`plan_tail_floors`;
    else :func:`plan_tail_floor`), then keeps :data:`PLAN_MARGIN` of what is
    left aside: ``allowed_speech_s``. The planned lines
    (:func:`_plan_speakers`; *narrator* says whether the narrator speaks in
    the scene -- by default whenever a *narrator_provider* is named) share
    it: the narrator the LOW end of the template's ``narrator_share`` when a
    character line is planned too (plan 24 stage 2: the character keeps room
    for a complete line), each kind's lines evenly. Each line's
    seconds become a hard word cap on the one clock (:func:`words_for_seconds`
    at its voice's provider: *narrator_provider*, ``speakers[handle]``).

    *native* (a native-speech story; *speech_lengths* its speech link's clip
    lengths, *silent_lengths* its silent link's, the same by default): a
    character line is its clip -- its seconds snap DOWN to a speech length
    (never under the shortest), its cap the clip's capacity (spoken by the
    clip, at no TTS overrun); the narrator gets what is left, at most the
    longest silent clip less its 0.7 s, its clip that plus 0.7 s snapped UP
    (as ``shots.speech_shot_plan`` sizes it). While the clips sum past the
    slot's high end, the narrator's clip shrinks to the next length first,
    then a character's. With both a narrator and a character line (plan 24
    stage 2) the character lines are planned FIRST, at
    :data:`PLAN_CHARACTER_CLIP_S` -- stepped down while they and the
    narrator's shortest silent clips cannot sum inside the slot's high end --
    and the narrator takes the seconds left, its clip the longest that still
    fits the slot beside them.

    Pure; a line's cap is never below 1 word."""
    _check_language(lang)
    speakers = dict(speakers or {})
    if narrator is None:
        narrator = narrator_provider is not None
    lo, hi = slot_range(scene, template, style_lock=style_lock)
    planned = _plan_speakers(scene, narrator=narrator, speakers=speakers, line_count_hint=line_count_hint)
    n = len(planned)
    pauses = template["pauses_s"]
    floor = plan_tail_floor(template) if tail_floor is None else float(tail_floor)
    silent = (pauses["before_first_line"] + pauses["between_lines"] * (n - 1) + floor) if n else 0.0
    allowed = max(0.0, (hi - silent) * (1.0 - PLAN_MARGIN)) if n else 0.0

    kinds = ["narrator" if who == "narrator" else "character" for who in planned]
    n_narr, n_char = kinds.count("narrator"), kinds.count("character")
    if n_narr and n_char:
        share = template.get("narrator_share")
        narr_share = float(share[0]) if share else PLAN_NARRATOR_SHARE
    else:
        narr_share = 1.0 if n_narr else 0.0
    narr_s = allowed * narr_share / n_narr if n_narr else 0.0
    char_s = allowed * (1.0 - narr_share) / n_char if n_char else 0.0

    def provider_of(who):
        return narrator_provider if who == "narrator" else speakers.get(who)

    lines = []
    if not native:
        for who, kind in zip(planned, kinds):
            seconds = narr_s if kind == "narrator" else char_s
            cap = max(1, words_for_seconds(seconds, lang, provider=provider_of(who)))
            lines.append({"kind": kind, "speaker": who, "seconds": round(seconds, 3), "max_words": cap})
    else:
        speech = tuple(speech_lengths or PLAN_SPEECH_LENGTHS)
        silent_ls = tuple(silent_lengths or speech)
        narr_max = max(silent_ls) - PLAN_SPEECH_OVERHEAD_S
        ordered_silent, ordered_speech = sorted(silent_ls), sorted(speech)

        def narration(clips):
            return min(narr_max, max(0.0, (allowed - sum(clips)) / n_narr)) if n_narr else 0.0

        def step_down(clips):
            """*clips* with its longest stepped to the next shorter length;
            None when every clip is the shortest already."""
            longest = max(range(len(clips)), key=lambda k: clips[k])
            smaller = [length for length in ordered_speech if length < clips[longest]]
            if not smaller:
                return None
            return clips[:longest] + [smaller[-1]] + clips[longest + 1:]

        if n_narr and n_char:
            # Plan 24 stage 2: the character lines first, at the default clip,
            # while they and the narrator's shortest clips fit the slot.
            char_clips = [_snap_down(PLAN_CHARACTER_CLIP_S, speech)] * n_char
            while sum(char_clips) + n_narr * ordered_silent[0] > hi + 1e-9:
                smaller = step_down(char_clips)
                if smaller is None:
                    break
                char_clips = smaller
            narr_seconds = narration(char_clips)
            room = (hi - sum(char_clips)) / n_narr
            wanted = _snap_up(narr_seconds + PLAN_SPEECH_OVERHEAD_S, silent_ls)
            fitting = [length for length in ordered_silent if length <= room + 1e-9]
            narr_clip = wanted if wanted <= room + 1e-9 else (fitting[-1] if fitting else ordered_silent[0])
            narr_seconds = min(narr_seconds, max(0.0, narr_clip - PLAN_SPEECH_OVERHEAD_S))
            narr_clips = [narr_clip] * n_narr
        else:
            char_clips = [_snap_down(char_s, speech) for _ in range(n_char)]
            narr_seconds = narration(char_clips)
            narr_clips = [_snap_up(narr_seconds + PLAN_SPEECH_OVERHEAD_S, silent_ls)] * n_narr
        while sum(narr_clips) + sum(char_clips) > hi + 1e-9:
            smaller_narr = [length for length in ordered_silent if narr_clips and length < narr_clips[0]]
            if smaller_narr:
                narr_clips = [smaller_narr[-1]] * n_narr
                narr_seconds = min(narr_seconds, smaller_narr[-1] - PLAN_SPEECH_OVERHEAD_S)
                continue
            longest = max(range(len(char_clips)), key=lambda k: char_clips[k]) if char_clips else None
            smaller_char = ([length for length in ordered_speech if length < char_clips[longest]]
                            if longest is not None else [])
            if not smaller_char:
                break
            char_clips[longest] = smaller_char[-1]
        # A clip raised to the shortest length may hold more than the scene
        # can pay on the estimate: the estimate's share then caps the words.
        roomy = sum(char_clips) <= allowed + 1e-9
        c = 0
        for who, kind in zip(planned, kinds):
            if kind == "narrator":
                cap = max(1, words_for_seconds(narr_seconds, lang, provider=provider_of(who)))
                lines.append({"kind": kind, "speaker": who, "seconds": round(narr_seconds, 3),
                              "clip_s": int(narr_clips[0]), "max_words": cap})
            else:
                clip = char_clips[c]
                c += 1
                seconds = float(clip) if roomy else min(float(clip), allowed / n_char)
                cap = max(1, min(_clip_capacity(clip), words_for_seconds(seconds, lang)))
                lines.append({"kind": kind, "speaker": who, "seconds": round(seconds, 3), "clip_s": int(clip),
                              "max_words": cap})

    total = sum(line["max_words"] for line in lines)
    return {"slot_s": [lo, hi], "allowed_speech_s": round(allowed, 3), "lines": lines, "max_words": total,
            "min_words": math.floor(0.5 * total + 1e-9)}


def plan_budget(plan: dict, *, line_lo: int = 1) -> dict:
    """A line plan (:func:`scene_plan`, or a scene's stored ``line_plan``)
    as :func:`word_budget_v3`'s answer -- ``{"words": [min_words,
    max_words], "lines": [n, n], "line_words": [lo, the largest cap],
    "caps": [each line's cap]}`` -- what the writer reads once stage 2
    switches it to the plan (*line_lo*: the template's line floor,
    :func:`line_words_v3`, never above the smallest cap)."""
    caps = [int(line["max_words"]) for line in plan["lines"]]
    n = max(1, len(caps))
    hi = int(plan["max_words"])
    largest = max(caps) if caps else hi
    smallest = min(caps) if caps else hi
    return {"words": [min(int(plan["min_words"]), hi), hi], "lines": [n, n],
            "line_words": [min(int(line_lo), smallest), largest], "caps": caps}


# --------------------------------------------------------------- transitions

def _boundary_kind(prev_place_id: str, next_place_id: str) -> str:
    """The spec 6.3 grammar between two scenes: same place dissolves, a
    place change fades to black."""
    return "dissolve" if prev_place_id == next_place_id else "fadeblack"


def cuts_between_scenes(template: dict) -> bool:
    """Whether *template* cuts at every scene boundary (plan 22 stage 3: its
    ``scene_transition`` is ``"cut"`` -- one continuous scene in real time,
    no time jump to blend). Absent on every other template: their
    boundaries keep the spec 6.3 grammar, byte for byte."""
    return (template or {}).get("scene_transition") == "cut"


def plan_transitions(shots: list, scenes_by_id: dict, template: dict, *, cuts_only: bool = False) -> list:
    """One transition per boundary between consecutive shots (spec 6.3, 6.5).

    A cut inside a scene; between scenes, a dissolve when both scenes share
    a ``place_id`` else a fade to black; durations come from the template's
    ``transitions_s``. The end card (not a shot) is never listed here.
    *cuts_only* (plan 22, a native-speech board): every boundary a cut, so
    every second a clip was bought for is seen and no line plays under a
    blend. A template whose ``scene_transition`` is ``"cut"`` (plan 22 stage
    3, :func:`cuts_between_scenes`) cuts every boundary too.
    """
    transitions_s = template["transitions_s"]
    cuts_only = cuts_only or cuts_between_scenes(template)
    result = []
    for prev_shot, next_shot in zip(shots, shots[1:]):
        if cuts_only or prev_shot["scene_id"] == next_shot["scene_id"]:
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


def covers(storyboard: dict, script: dict) -> bool:
    """Whether *storyboard* has shots for every scene of *script*, in the
    script's order -- the only storyboard whose transitions time the whole
    episode (:func:`_boundary_from_storyboard` refuses any other)."""
    if not storyboard or not script["scenes"]:
        return False
    sequence = []
    for shot in storyboard["shots"]:
        if not sequence or sequence[-1] != shot["scene_id"]:
            sequence.append(shot["scene_id"])
    return sequence == [scene["scene_id"] for scene in script["scenes"]]


def _shot_floors(storyboard: dict, template: dict) -> dict:
    """``{scene_id: seconds}``: how long each scene with shots in
    *storyboard* must last for every one of its shots to get the template's
    ``min_shot_s`` (``len(shots) * min_shot_s``). Every scene the storyboard
    has shots for counts, stale or not, whether or not the storyboard covers
    the script: the shots are what gets rendered."""
    if not storyboard:
        return {}
    counts: dict = {}
    for shot in storyboard["shots"]:
        counts[shot["scene_id"]] = counts.get(shot["scene_id"], 0) + 1
    min_shot = template["min_shot_s"]
    return {sid: round(n * min_shot, 3) for sid, n in counts.items()}


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
        kind = "cut" if cuts_between_scenes(template) else _boundary_kind(prev_scene["place_id"],
                                                                           next_scene["place_id"])
        result.append((kind, transitions_s[kind]))
    return result


# ---------------------------------------------------------------- shots

def allocate_shots(scene: dict, scene_t: dict, shots: list, template: dict, *, whole_frames: bool = True) -> tuple:
    """Split one scene's duration across its shots. Returns ``(durations,
    extra_hold_s)``.

    1. If even the minimum shot length does not fit every shot
       (``len(shots) * min_shot_s > duration``), every shot gets exactly
       ``min_shot_s`` and the shortfall is reported as ``extra_hold_s``.
       A storyboard's own scenes never reach this: they are timed by
       :func:`episode_pass`, whose shot floor already grew every scene to
       what its shots need.
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

    *whole_frames* (the default; phase 5 stage 6; *scene_t* then lasts a
    whole number of frames, :func:`scene_timing`): every shot boundary
    inside the scene lands on the nearest whole frame of where it falls (the
    running sum, so no shot drifts), each shot stored as ``round(frames /
    FPS, 3)``, and the last shot takes the rest, so the shots still sum
    exactly to the scene's stored duration. A shot that rounding would put under ``min_shot_s``
    makes the scene fall back to the even split, as above.
    """
    duration = scene_t["duration_s"]
    min_shot = template["min_shot_s"]
    n = len(shots)
    if n == 0:
        return [], 0.0

    def _round_last(values):
        if whole_frames:
            rounded = []
            cumulative, previous = 0.0, 0
            for value in values[:-1]:
                cumulative += value
                frames = _to_frames(cumulative)
                rounded.append(_from_frames(frames - previous))
                previous = frames
        else:
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

    rounded = _round_last(durations)
    if whole_frames and any(d < min_shot - 1e-9 for d in rounded):
        return _even_split()
    return rounded, 0.0


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
                    storyboard: dict = None, whole_frames: bool = True) -> dict:
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

    Shot floor: with a storyboard given, each scene is never shorter than
    its shots need (``len(shots) * min_shot_s``, :func:`_shot_floors`), the
    shortfall held at its end (see :func:`scene_timing`'s
    ``min_duration_s``) before the window pass below.

    Window handling (spec 6.4, the human's window/tighten answer of
    2026-09-27): above ``tighten_above_s``, tails are shortened toward
    their floors, longest tail first, only as much as needed (never taking
    a scene below its shot floor). If that
    brings the total to ``tighten_above_s`` or below, or leaves it inside
    the window, the state is ``"tightened"``; if the window's high end is
    still exceeded, the state is ``"over"`` and the longest lines in the
    episode are flagged for trimming, longest first, until half the sum of
    the flagged lines' durations covers the excess. Below the window's low
    end, holds are extended (up to ``hold_extension_max_s`` each, counting
    what a scene's shot floor already held beyond its own length; never
    past a scene's own slot maximum) -- first on the cliffhanger scene,
    then on the first scene at each place in the order the places appear;
    if that is not enough the state is ``"under"``. Independently of the
    episode's own state, any scene whose *own* timing came out ``"over"``
    (it did not fit even after its own tail was shrunk to the floor) adds
    a ``scene_over`` flag and a ``trim_line`` flag on its own longest line.

    Whole frames (*whole_frames*, the default; phase 5 stage 6): every
    scene is timed in whole frames (:func:`scene_timing`) and the window
    pass shortens a tail or extends a hold by whole frames only -- a tail's
    room rounded down, so it never goes under its floor -- so every scene
    duration stays a whole number of frames. Off, the episode is timed
    exactly as before phase 5 stage 6, as a storyboard timed then still is
    (:func:`board_whole_frames`).

    The storyboard, when given, must cover every scene of the script
    (:func:`covers`); :func:`episode_pass` is the same computation for any
    storyboard, and also returns each scene's line starts.
    """
    if _native(storyboard):
        return native_pass(script, storyboard, template, language)[0]
    boundary = _boundary_transitions(script["scenes"], template, storyboard)
    result, _scene_timings = _episode_pass(script, template, language, style_lock=style_lock, boundary=boundary,
                                           shot_floors=_shot_floors(storyboard, template), whole_frames=whole_frames)
    return result


def episode_pass(script: dict, template: dict, language: str, *, style_lock: dict = None,
                 storyboard: dict = None, whole_frames: bool = True) -> tuple:
    """``(timing, scenes)``: the one timing a script and its storyboard
    share -- the script's stored ``timing`` is the first, and a storyboard's
    shots are cut to the second (``shots.build_storyboard``,
    ``shots.retime_storyboard``), so the two cannot disagree.

    *storyboard* may be any storyboard (or None): its transitions time the
    scene boundaries only when it covers every scene of *script*
    (:func:`covers`) -- any other is left out of the boundaries and they are
    predicted (spec 6.3) -- while every scene it has shots for gets their
    shot floor (:func:`_shot_floors`). For a covering storyboard or None,
    ``timing`` is exactly :func:`episode_timing`'s.

    ``scenes`` is ``{scene_id: scene_t}`` after the window pass: the same
    ``duration_s``/``tail_s``/``hold_s``/``state`` as ``timing["scenes"]``,
    plus ``speech_s`` and ``line_starts`` (:func:`scene_timing`'s; the
    window pass only moves a tail or a hold, both after the last line, so a
    line never moves) -- what :func:`allocate_shots` splits.

    *whole_frames*: :func:`episode_timing`'s (whole frames by default;
    off, exactly as before phase 5 stage 6) -- :func:`board_whole_frames`
    says which one a script beside a given storyboard is timed with, and
    every caller timing a stored document passes it.
    """
    if _native(storyboard):
        return native_pass(script, storyboard, template, language)
    boundary_board = storyboard if covers(storyboard, script) else None
    boundary = _boundary_transitions(script["scenes"], template, boundary_board)
    return _episode_pass(script, template, language, style_lock=style_lock, boundary=boundary,
                         shot_floors=_shot_floors(storyboard, template), whole_frames=whole_frames)


def _native(storyboard) -> bool:
    """Whether *storyboard* is a native-speech story's (plan 22): its
    ``timing_mode`` says so. Every other board is timed as it always was."""
    return bool(storyboard) and storyboard.get("timing_mode") == "native_speech"


def native_pass(script: dict, storyboard: dict, template: dict, language: str) -> tuple:
    """:func:`episode_pass` of a native-speech board (plan 22,
    ``native_speech.native_pass``): each shot lasts what its clip lasts, a
    speaking shot's line starts where its take heard it, a narrator's line
    in its silent shot. Only a board whose ``timing_mode`` is
    ``native_speech`` is ever timed so."""
    from . import native_speech

    _check_language(language)
    return native_speech.native_pass(script, storyboard, template, lambda line: line_duration(line, language)[0])


def _episode_pass(script: dict, template: dict, language: str, *, style_lock: dict, boundary: list,
                  shot_floors: dict, whole_frames: bool = True) -> tuple:
    """:func:`episode_timing`'s computation, given the scene *boundary*
    transitions and the *shot_floors*; ``(timing, scene_timings)``.

    In whole frames, what the window pass moves is counted in frames: every
    scene duration, slot end, shot floor and the hold cap is a whole number
    of them already (the 3-decimal storage noise of a sum of them stays far
    under half a frame, so the nearest frame is exact), and a tail's room
    -- the one amount that is not -- is rounded down."""
    scenes = script["scenes"]

    scene_timings = {}
    slot_hi_by_scene = {}
    # How much a scene's shot floor lengthened it beyond its own timing: it
    # counts toward the hold extension below, so no scene is held more than
    # hold_extension_max_s beyond its own length by the window pass.
    floor_hold = {}
    for i, scene in enumerate(scenes):
        sid = scene["scene_id"]
        floor = _effective_tail_floor(i, scenes, boundary, template, script)
        shot_floor = shot_floors.get(sid)
        scene_t = scene_timing(scene, template, language, style_lock=style_lock, tail_floor=floor,
                               min_duration_s=shot_floor, whole_frames=whole_frames)
        floor_hold[sid] = 0.0
        if shot_floor is not None:
            natural = scene_timing(scene, template, language, style_lock=style_lock, tail_floor=floor,
                                   whole_frames=whole_frames)
            floor_hold[sid] = max(0.0, scene_t["duration_s"] - natural["duration_s"])
        scene_timings[sid] = scene_t
        slot_hi_by_scene[sid] = slot_range(scene, template, style_lock=style_lock)[1]

    end_card_addition = 0.0
    if script["cliffhanger"]["cut_to_black"]:
        end_card_addition = template["end_card_s"] - template["transitions_s"]["fadeblack"]

    def _current_total() -> float:
        return sum(scene_timings[s["scene_id"]]["duration_s"] for s in scenes) \
            - sum(dur for _kind, dur in boundary) + end_card_addition

    def _beyond(limit, value) -> float:
        """Whether *value* is past *limit* (> 0) or short of it (< 0), for
        the window's decisions -- in whole frames counted in frames: *value*
        is a sum of 3-decimal durations, a few milliseconds of storage noise
        off its whole frame, which must never tip a window state (55.0 s
        held as 54.998 s). A flag still says the plain difference."""
        if whole_frames:
            return (_to_frames(value) - _to_frames(limit)) / FPS
        return value - limit

    total = _current_total()
    window_lo, window_hi = template["window_s"]
    tighten_above = template["tighten_above_s"]
    flags = []
    state = "ok"

    if _beyond(tighten_above, total) > 0:
        need = total - tighten_above
        by_tail_desc = sorted(scenes, key=lambda s: scene_timings[s["scene_id"]]["tail_s"], reverse=True)
        for scene in by_tail_desc:
            if need <= 1e-9:
                break
            sid = scene["scene_id"]
            i = scenes.index(scene)
            floor = _effective_tail_floor(i, scenes, boundary, template, script)
            scene_t = scene_timings[sid]
            if whole_frames:
                room_frames = _frames_down(scene_t["tail_s"] - floor)
                if sid in shot_floors:
                    room_frames = min(room_frames,
                                      _to_frames(scene_t["duration_s"]) - _to_frames(shot_floors[sid]))
                shrink_frames = min(_to_frames(need), room_frames)
                if shrink_frames <= 0:
                    continue
                shrink = shrink_frames / FPS
                duration = _from_frames(_to_frames(scene_t["duration_s"]) - shrink_frames)
            else:
                room = max(0.0, scene_t["tail_s"] - floor)
                if sid in shot_floors:
                    room = max(0.0, min(room, scene_t["duration_s"] - shot_floors[sid]))
                shrink = min(need, room)
                if shrink <= 0:
                    continue
                duration = round(scene_t["duration_s"] - shrink, 3)
            scene_timings[sid] = {
                **scene_t,
                "tail_s": round(scene_t["tail_s"] - shrink, 3),
                "duration_s": duration,
            }
            need -= shrink
        total = _current_total()
        state = "tightened"
        if _beyond(window_hi, total) > 0:
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

    elif _beyond(window_lo, total) < 0:
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
            if whole_frames:
                frames = _to_frames(scene_t["duration_s"])
                room_frames = _frames_down(hi) - frames
                cap_frames = _frames_down(template["hold_extension_max_s"]) - _to_frames(floor_hold[sid])
                extend_frames = min(_to_frames(need), cap_frames, room_frames)
                if extend_frames <= 0:
                    continue
                extend = extend_frames / FPS
                duration = _from_frames(frames + extend_frames)
            else:
                room = max(0.0, hi - scene_t["duration_s"])
                cap = max(0.0, template["hold_extension_max_s"] - floor_hold[sid])
                extend = min(need, cap, room)
                if extend <= 0:
                    continue
                duration = round(scene_t["duration_s"] + extend, 3)
            scene_timings[sid] = {
                **scene_t,
                "hold_s": round(scene_t["hold_s"] + extend, 3),
                "duration_s": duration,
            }
            need -= extend
        total = _current_total()
        if _beyond(window_lo, total) < 0:
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

    result = {
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
    return result, scene_timings


def scene_starts(script: dict, timing: dict, template: dict, *, storyboard: dict = None) -> dict:
    """``{scene_id: start_s}``: every scene's own start on the episode's
    output timeline, from an already-computed :func:`episode_timing`
    result -- the running sum of the *previous* scenes' (already
    window-adjusted) durations, minus the overlap of the transition
    entering it -- the same overlap :func:`episode_timing` subtracted from
    the total (matched to the storyboard's shots by id, see
    :func:`_boundary_from_storyboard`, when *storyboard* is given).

    Factored out of :func:`line_offsets` (which places every *line*
    relative to this same per-scene start) so a caller that needs a scene's
    own start regardless of whether it has any lines -- an ``"at":
    "start"`` SFX anchor, or a wordless scene, spec 6.4/6.5 -- has one to
    read, without re-deriving this arithmetic (plan phase 4 stage 4,
    ``render/timeline.py``). Unrounded, like the running accumulator it
    always was inside :func:`line_offsets` -- a caller rounds if it wants
    a display value.
    """
    scenes = script["scenes"]
    boundary = _boundary_transitions(scenes, template, storyboard)

    starts = {}
    scene_start = 0.0
    for i, scene in enumerate(scenes):
        if i > 0:
            prev_sid = scenes[i - 1]["scene_id"]
            prev_duration = timing["scenes"][prev_sid]["duration_s"]
            scene_start += prev_duration - boundary[i - 1][1]
        starts[scene["scene_id"]] = scene_start

    return starts


def line_offsets(script: dict, timing: dict, template: dict, *, storyboard: dict = None) -> dict:
    """Absolute ``{line_id: (start_s, end_s)}`` for every line in the
    episode, from an already-computed :func:`episode_timing` result.

    Each scene's own start is :func:`scene_starts` (so this reconstructs
    the same timeline). A line's duration is read straight from its
    persisted ``timing`` (the caller is expected to have already re-timed
    the script), not re-estimated: this function only places lines, it
    never times them.
    """
    if _native(storyboard):
        # Plan 22: a native board places each line where its clip speaks it.
        from . import native_speech

        return native_speech.line_offsets(script, storyboard, lambda line: line["timing"]["duration_s"])
    scenes = script["scenes"]
    starts = scene_starts(script, timing, template, storyboard=storyboard)
    pauses = template["pauses_s"]

    offsets = {}
    for scene in scenes:
        scene_start = starts[scene["scene_id"]]
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
