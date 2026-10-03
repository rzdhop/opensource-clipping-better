"""Pure planning for AI Story's Tier-2/3 video (spec 2.8, 8.1-8.7; phase 6
plan stage 1): the fixed camera-phrase and negative-prompt vocabulary an
image-to-video model is driven with, the clip-length table of the hosted
chain's links, and the greedy per-episode animate planner the ``one_dollar``
and ``quality`` budget profiles need (``budget_profiles.json``'s ``animate``/
``animate_priority`` fields, validated by ``schemas.py`` but read nowhere
until this module).

Nothing here does I/O or touches the network. Its one provider import is
the clip-length table the video adapters enforce
(``clipping.providers.video.CLIP_LENGTHS``), so a plan never asks for a
length they refuse. Every function takes its inputs explicit (the style
lock, the per-second price, the episode's shots and scene functions, ...)
and returns a plain value, so the same inputs always produce the same
output. This keeps the estimate shown before a run and the run itself
provably in agreement
(DEC-203: the planner's selection is derived, never stored) and keeps the
module testable with pytest alone (DEC-012).

Stdlib only.
"""

from __future__ import annotations

from typing import Mapping, NamedTuple, Sequence

from ..providers.video import CLIP_LENGTHS
from . import prompting, schemas

# ------------------------------------------------------------ camera phrases

# Exactly ``schemas.CAMERA_MOTIONS`` (spec 6.3's closed list), each mapped to
# a short English phrase a hosted or local image-to-video model is prompted
# with; and exactly ``schemas.MODIFIERS``, a phrase each (a shot's
# ``modifiers`` array is empty on most shots, so this only ever adds to the
# prompt when the storyboard actually asked for one of these). Both tables
# live in the pure ``prompting`` (phase 7 stage 3b: a v2 shot's clip prompt
# is written at resolve time, by ``shots``); these are the same dicts.
CAMERA_PHRASES: dict[str, str] = prompting.CAMERA_PHRASES
MODIFIER_PHRASES: dict[str, str] = prompting.MODIFIER_PHRASES

# Appended to every Tier-2/3 negative prompt on top of the style's own
# (``build_video_prompt``): the failure modes generic to image-to-video
# models rather than to any one style.
MOTION_NEGATIVE = "morphing, warping, extra limbs, flicker, sudden cuts, text, watermark"

# The default priority order the "one_dollar" budget profile ships
# (``templates/budget_profiles.json``'s ``profiles.one_dollar.animate_priority``);
# kept here as a constant so a caller does not have to load that file just to
# get the everyday order, and a test asserts the two stay equal.
ANIMATE_PRIORITY: tuple[str, ...] = ("hook", "cliffhanger", "peak", "turn", "longest_dialogue")

# ``budget_profiles.json``'s three ``animate`` values (spec 8.1).
_ANIMATE_MODES = ("none", "key_shots_within_cap", "all_shots")

# Float comparisons on dollar amounts computed by summing per-shot costs;
# large enough to absorb binary floating-point noise, small enough never to
# let a real cent through.
_CAP_EPSILON = 1e-9


def _clean_sentence(text: str) -> str:
    """Strip surrounding whitespace and any trailing "."s, so joining
    sentences with ``". "`` never doubles a terminal period."""
    text = text.strip()
    while text.endswith("."):
        text = text[:-1].rstrip()
    return text


def _join_sentences(parts: Sequence[str]) -> str:
    cleaned = [_clean_sentence(part) for part in parts if part]
    cleaned = [part for part in cleaned if part]
    return ". ".join(cleaned)


def _dedup_terms(*negatives: str) -> str:
    """Comma-split every argument and join the terms back with ``", "``,
    keeping only the first occurrence of each (exact string match, in the
    order first seen) -- so appending ``MOTION_NEGATIVE`` to a style's own
    negative prompt never repeats a term the style already named."""
    seen: list[str] = []
    for negative in negatives:
        for chunk in (negative or "").split(","):
            term = chunk.strip()
            if term and term not in seen:
                seen.append(term)
    return ", ".join(seen)


def build_video_prompt(
    shot: dict,
    style_lock: dict,
    *,
    tier: int,
    lines: Sequence[str] = (),
    note: str | None = None,
    audio: Mapping | None = None,
    audio_budget: int | None = None,
) -> tuple[str, str]:
    """The (prompt, negative) pair an image-to-video adapter is called with
    for one shot.

    *audio* (phase 7 follow-up, stage E: an ambience story,
    ``media_policy.ambience``; tier 3 only) is the shot's sound brief --
    ``{"place", "sfx", "speakers"}`` -- and the prompt is then
    ``prompting.clip_prompt_with_audio`` of the visual part below and the
    note: what the shot sounds like, inside the clip prompt's own word cap
    (*audio_budget*, stage F2: the link's own, ``prompt_budgets.clip_audio_words``;
    None: ``prompting.CLIP_AUDIO_MAX_WORDS``), no word voiced. It never
    takes *lines* (``ValueError``): such a clip's sound is heard under the
    shot's TTS lines, never in place of them. Without it, everything below
    is as it was.

    A shot whose ``video_prompt`` is a non-empty string (a v2 shot, phase 7
    stage 3b: ``prompting.layered_clip_prompt``, written at resolve time)
    sends it in place of the four parts below; the speech cue and the note
    follow it the same way. Every other shot: the prompt is, in order: the
    shot's own action sentence (``shot
    ["video_action"]`` when present -- the tag-resolved, name-swept action
    ``shots.resolve_shot`` stores on a storyboard shot, phase 7 D1 -- else
    the raw ``shot["action"]``, spec 2.8's English grammar; a shot with no
    ``video_action`` key, from a storyboard built before this field existed,
    keeps its old, byte-identical prompt so an already-stored clip stays
    "current"), the style's ``motion_rules.tier2_prompt_suffix`` (the style
    lock is a deep copy of
    its style template -- ``stylelock.build_style_lock`` -- so it carries
    the same ``motion_rules``/``negative_prompt`` fields the template ships,
    ``templates/styles/*.json``), then the camera phrase for
    ``shot["camera_motion"]``, then a phrase per entry of ``shot["modifiers"]``
    in the shot's own order (only for modifiers :data:`MODIFIER_PHRASES`
    covers). At ``tier=3`` only, the shot's spoken line text (already
    resolved by the caller from the episode script -- see
    :func:`plan_animation`'s docstring for where a shot's lines live) is
    appended as a speech cue, because Tier 2 always gets its dialogue from
    our own TTS, never from the video model (DEC-201). A truthy ``note``
    (a user's re-animate direction) is appended last, at either tier.

    The negative prompt is the shot's own resolved ``negative_prompt`` when
    it is a non-empty string, else the style's, followed by
    :data:`MOTION_NEGATIVE`, with any term already present kept only once.

    Raises ``ValueError`` when ``tier`` is not 2 or 3, or when
    ``shot["camera_motion"]`` is not one of :data:`CAMERA_PHRASES`'s keys
    (``schemas.CAMERA_MOTIONS``).
    """
    if tier not in (2, 3):
        raise ValueError(f"tier must be 2 or 3, got {tier!r}")
    if audio is not None and (tier != 3 or lines):
        raise ValueError("a clip's sound brief is a tier-3 ambience clip's, which never voices a line")

    camera_motion = shot["camera_motion"]
    if camera_motion not in CAMERA_PHRASES:
        raise ValueError(f"not a Tier-1 camera_motion: {camera_motion!r}")

    video_prompt = shot.get("video_prompt")
    if isinstance(video_prompt, str) and video_prompt.strip():
        # A v2 shot (phase 7 stage 3b): its clip prompt was written whole at
        # resolve time (prompting.layered_clip_prompt) -- action, camera,
        # modifiers, the stays-still clause and the motion suffix in it.
        parts = [video_prompt]
    else:
        parts = [shot.get("video_action") or shot["action"],
                 style_lock["motion_rules"]["tier2_prompt_suffix"], CAMERA_PHRASES[camera_motion]]
        for modifier in shot.get("modifiers", ()):
            phrase = MODIFIER_PHRASES.get(modifier)
            if phrase:
                parts.append(phrase)

    if tier == 3 and lines:
        speech = " ".join(line.strip() for line in lines if line and line.strip())
        if speech:
            parts.append(f'The character says: "{speech}"')

    if audio is not None:
        prompt = prompting.clip_prompt_with_audio(_join_sentences(parts), note=note or "", place=audio["place"],
                                                  sfx=audio.get("sfx") or (), speakers=audio.get("speakers") or (),
                                                  budget=audio_budget or prompting.CLIP_AUDIO_MAX_WORDS)
    else:
        if note:
            parts.append(note)
        prompt = _join_sentences(parts)

    shot_negative = shot.get("negative_prompt") or ""
    base_negative = shot_negative if shot_negative.strip() else style_lock.get("negative_prompt", "")
    negative = _dedup_terms(base_negative, MOTION_NEGATIVE)

    return prompt, negative


# --------------------------------------------------------------- clip lengths

# ``CLIP_LENGTHS`` (imported above) holds the supported whole-second clip
# lengths per hosted link of ``clipping.providers.generation``'s default
# ``VIDEO_CHAIN``: defined once in ``clipping.providers.video``, whose
# adapters refuse any other length (A-100..A-103, read 2026-09-30).
# ``local/comfyui`` is deliberately absent: its templates set their own frame
# rule (stage 4), which is not a fixed per-second table.


def requested_seconds(link: str, duration_s: float, *, lengths: Sequence[int] | None = None) -> int:
    """The smallest length of ``lengths`` (or :data:`CLIP_LENGTHS[link]`)
    that is ``>= duration_s``, else the longest -- the render then holds the
    clip's last frame to cover the remainder (spec 8's clip-length table,
    DEC-208).

    ``lengths`` lets a caller plan against a local ComfyUI template's own
    frame rule instead of the hosted table; it is required for any ``link``
    :data:`CLIP_LENGTHS` does not cover.

    Raises ``ValueError`` when ``duration_s`` is not positive, or when
    ``link`` is unknown and no ``lengths`` override is given.
    """
    if duration_s <= 0:
        raise ValueError(f"duration_s must be > 0, got {duration_s!r}")

    supported = lengths if lengths is not None else CLIP_LENGTHS.get(link)
    if supported is None:
        raise ValueError(f"no supported clip lengths for link {link!r}; pass lengths= for a local template")

    supported = sorted(supported)
    for length in supported:
        if length >= duration_s:
            return length
    return supported[-1]


# ------------------------------------------------------- per-shot overrides

# The per-shot overrides the user sets in ``assets.json`` (``shots``,
# ``workflow.patch_assets``; phase 6 stage 7).
SHOT_FLAGS = schemas.SHOT_OVERRIDE_FLAGS


def shot_overrides(assets_doc, shot_id):
    """Shot *shot_id*'s override entry in *assets_doc* (``assets.json``'s
    ``shots``), or None."""
    entry = ((assets_doc or {}).get("shots") or {}).get(shot_id)
    return entry if isinstance(entry, dict) else None


def effective_shot_flags(board_shot: Mapping, assets_shot: Mapping | None = None) -> dict:
    """``{"keep_still", "animate", "keep_native_audio"}`` of one shot, as the
    planner, the video phase and the renderer must all read them: each is
    the override *assets_shot* sets (``assets.json``'s ``shots[shot_id]``,
    :func:`shot_overrides`) when it sets one, else the storyboard's own
    ``keep_still`` for ``keep_still``, else False. ``animate`` True is a pin;
    a shot kept still is never animated, pin or not (:func:`plan_animation`
    drops it first)."""
    flags = {"keep_still": bool(board_shot.get("keep_still")), "animate": False, "keep_native_audio": False}
    for name in SHOT_FLAGS:
        value = (assets_shot or {}).get(name)
        if isinstance(value, bool):
            flags[name] = value
    return flags


# ------------------------------------------------------------- the planner


class VideoPlan(NamedTuple):
    """The result of :func:`plan_animation`: which shots would be animated,
    which stay still and why, and how that measures up against the
    episode's cap. Plain and immutable, and never itself persisted
    (DEC-203) -- a caller re-derives it whenever it needs a fresh estimate."""

    selected: list  # ordered [{shot_id, clip_s, est_usd, why}, ...]
    still: list  # shot-order [{shot_id, reason}, ...]
    seconds: int  # sum of clip_s across NEW (non-"current") selected clips
    video_usd: float  # sum of est_usd, rounded to 4 dp for display only
    image_usd: float  # == the committed_usd argument, passed through
    cap_usd: float
    left_usd: float  # cap_usd - image_usd - the full-precision video total
    over_cap: bool  # True when image_usd + the video total exceeds cap_usd


def plan_animation(
    shots: Sequence[dict],
    scene_function: Mapping[str, str],
    dialogue_seconds: Mapping[str, float],
    *,
    link: str,
    price_per_second: float,
    cap_usd: float,
    committed_usd: float,
    current_shot_ids: Sequence[str] = (),
    mode: str,
    priority: Sequence[str] = ANIMATE_PRIORITY,
    lengths: Sequence[int] | None = None,
) -> VideoPlan:
    """Greedily decide which of an episode's shots get an animated clip.

    ``shots`` is the storyboard's own shot list: each needs ``shot_id``,
    ``order``, ``scene_id``, ``duration_s``, ``keep_still`` and, optionally,
    an ``animate`` pin (``True`` marks a shot the human explicitly asked to
    animate; spec 2.8's ``assets.clip`` carries the result, not this pin).

    ``scene_function`` maps a ``scene_id`` to its ``EpisodeScript`` scene's
    ``function`` (``schemas.SCENE_FUNCTIONS`` -- ``recap, hook, setup,
    rising, peak, turn, cliffhanger``, ``schemas.py:140``; the episode
    script's own scene document carries this at ``schemas.py:1969``'s
    ``_EPISODE_SCRIPT_SCENE_SCHEMA["function"]``, one entry per
    ``EpisodeScript.scenes[i]``). ``templates/episodes/serial_60s_v1.json``'s
    ``slots`` cover every function exactly once per episode
    (``episode_template_errors``), which is what makes "one shot per
    priority function" a meaningful ask.

    ``dialogue_seconds`` maps a ``shot_id`` to how many seconds of spoken
    line audio that shot covers. A shot's lines are its own ``lines`` array
    of line ids (``schemas.py``'s ``_STORYBOARD_SHOT_SCHEMA["lines"]``,
    matching ``LINE_ID_PATTERN``); each id's duration lives on the episode
    script's matching line's ``timing.duration_s``
    (``_EPISODE_SCRIPT_LINE_TIMING_SCHEMA``, ``schemas.py:1931-1945``). This
    function takes the already-summed mapping rather than the two documents
    themselves, so it never has to know how a shot's lines are resolved.

    ``link`` and ``price_per_second`` are the sticky video link's id and its
    per-second price (a ``float`` the caller reads from
    ``clipping.providers.pricing`` -- this module never imports pricing, so
    a price change is never a side effect of a planning change).
    ``current_shot_ids`` names shots whose clip is already current (spec
    8.1's ``assets.clip.state == "current"``): those cost $0 and are always
    included when the planner reaches them.

    ``mode`` is one of ``budget_profiles.json``'s ``animate`` values:
    ``"none"`` selects only pinned shots; ``"key_shots_within_cap"`` (the
    ``one_dollar`` profile) adds shots in priority order while
    ``committed_usd`` plus the running video total stays ``<= cap_usd``,
    skipping a shot that does not fit and continuing to the next (a cheaper
    shot further down may still fit); ``"all_shots"`` (the ``quality``
    profile) selects every non-``keep_still`` shot regardless of the cap and
    reports ``over_cap`` rather than trimming anything -- such a plan is
    refused whole (:func:`all_shots_refusal`, DEC-227).

    ``priority`` defaults to :data:`ANIMATE_PRIORITY`. Every entry that
    names a scene function pulls that function's not-yet-placed shots (shot
    order) into their own group, in the order the entries appear;
    ``"longest_dialogue"`` (or simply reaching the end of a custom
    ``priority`` with shots still unplaced) pulls everything still unplaced,
    ordered by ``dialogue_seconds`` descending and shot order on a tie.

    ``keep_still`` shots are excluded outright, before pins or priority are
    considered. A pinned (``animate: True``) shot is always selected, shot
    order, ahead of everything else, even past the cap -- ``over_cap`` then
    reports it rather than the plan silently trimming a shot the human
    asked for by name.

    Raises ``ValueError`` for an unknown ``mode``, and propagates
    :func:`requested_seconds`'s ``ValueError`` for an unknown ``link``
    without a ``lengths`` override, or a non-positive ``duration_s``.
    """
    if mode not in _ANIMATE_MODES:
        raise ValueError(f"unknown animate mode {mode!r}; expected one of {_ANIMATE_MODES}")

    ordered_shots = sorted(shots, key=lambda shot: shot["order"])
    order_by_id = {shot["shot_id"]: shot["order"] for shot in ordered_shots}
    current_ids = set(current_shot_ids)

    still: list = []
    pool = []
    for shot in ordered_shots:
        if shot.get("keep_still"):
            still.append({"shot_id": shot["shot_id"], "reason": "keep_still"})
        else:
            pool.append(shot)

    pinned = [shot for shot in pool if shot.get("animate") is True]
    pinned_ids = {shot["shot_id"] for shot in pinned}
    rest = [shot for shot in pool if shot["shot_id"] not in pinned_ids]

    # (shot, why) pairs in the exact order they are considered; "why" is
    # overridden to "current" below for a shot already in current_ids
    # (except a pinned one, which keeps "pinned" -- the stronger reason --
    # even though its cost is still $0).
    ordering: list[tuple[dict, str]] = [(shot, "pinned") for shot in pinned]

    if mode == "none":
        for shot in rest:
            still.append({"shot_id": shot["shot_id"], "reason": "mode_none"})
    else:
        placed_ids = set(pinned_ids)
        for token in priority:
            if token == "longest_dialogue":
                continue
            bucket = [
                shot for shot in rest
                if shot["shot_id"] not in placed_ids and scene_function.get(shot["scene_id"]) == token
            ]
            for shot in bucket:
                ordering.append((shot, token))
                placed_ids.add(shot["shot_id"])

        leftover = [shot for shot in rest if shot["shot_id"] not in placed_ids]
        leftover.sort(key=lambda shot: (-dialogue_seconds.get(shot["shot_id"], 0.0), shot["order"]))
        remainder_why = "all_shots" if mode == "all_shots" else "longest_dialogue"
        for shot in leftover:
            ordering.append((shot, remainder_why))
            placed_ids.add(shot["shot_id"])

    selected: list = []
    running_usd = 0.0
    new_seconds = 0
    for shot, why in ordering:
        shot_id = shot["shot_id"]
        clip_s = requested_seconds(link, shot["duration_s"], lengths=lengths)
        is_current = shot_id in current_ids
        cost = 0.0 if is_current else clip_s * price_per_second
        entry_why = "current" if (is_current and why != "pinned") else why

        if why == "pinned" or is_current or mode == "all_shots":
            fits = True
        else:
            fits = committed_usd + running_usd + cost <= cap_usd + _CAP_EPSILON

        if fits:
            selected.append({"shot_id": shot_id, "clip_s": clip_s, "est_usd": cost, "why": entry_why})
            running_usd += cost
            if not is_current:
                new_seconds += clip_s
        else:
            still.append({"shot_id": shot_id, "reason": "over_cap"})

    still.sort(key=lambda entry: order_by_id[entry["shot_id"]])

    over_cap = (committed_usd + running_usd) > cap_usd + _CAP_EPSILON
    left_usd = cap_usd - committed_usd - running_usd

    return VideoPlan(
        selected=selected,
        still=still,
        seconds=new_seconds,
        video_usd=round(running_usd, 4),
        image_usd=committed_usd,
        cap_usd=cap_usd,
        left_usd=left_usd,
        over_cap=over_cap,
    )


def all_shots_refusal(plan: VideoPlan, *, link: str, mode: str) -> str | None:
    """Why an ``all_shots`` plan (the quality profile: every shot animated,
    phase 7 stage 4, DEC-227) cannot run, or None: it is over the episode's
    cap (``plan.over_cap``). Such a plan is refused whole -- never trimmed to
    the shots that fit -- so the sentence names what the whole plan needs:
    its clips, planned seconds and estimate, what the episode has already
    spent or committed (``plan.image_usd``), and the cap. Any other *mode*
    trims to its cap instead (or reports a pin past it), so it is never
    refused here."""
    if mode != "all_shots" or not plan.over_cap:
        return None
    clips = sum(1 for entry in plan.selected if entry["est_usd"] > 0)
    total = plan.image_usd + plan.video_usd
    return (f"Animating every shot needs {clips} clip{'' if clips == 1 else 's'}, {plan.seconds} s on {link}, "
            f"est ${plan.video_usd:.3f}; with ${plan.image_usd:.2f} already spent or committed that is "
            f"${total:.2f} of the ${plan.cap_usd:.2f} episode cap. The quality profile animates every shot, so "
            "the whole plan is refused, never a partial pick")


# ---------------------------------------------------------------- routing


def video_route(
    route: str,
    *,
    local_ready: bool,
    api_ready: bool,
    allow_paid: bool,
) -> tuple[str | None, str]:
    """Decide which video route actually runs: ``(route_name, "")`` on
    success, or ``(None, reason)`` -- a plain-English sentence a dashboard
    or CLI can show verbatim.

    ``route`` is the user's own choice, one of ``"auto"``, ``"local"`` or
    ``"api"`` (``ValueError`` otherwise). ``"local"`` needs ``local_ready``
    (a reachable, validated local ComfyUI workflow); ``"api"`` needs both
    ``api_ready`` (the sticky hosted link has its key and is not otherwise
    refused) and ``allow_paid``. ``"auto"`` prefers local, then falls back
    to the API when it is both ready and allowed, naming whichever
    conditions are missing when neither path works.
    """
    if route not in ("auto", "local", "api"):
        raise ValueError(f"unknown route {route!r}; expected 'auto', 'local' or 'api'")

    def api_reason() -> str:
        if not allow_paid:
            return "allow_paid is off"
        return "no API video link is ready"

    if route == "local":
        if local_ready:
            return "local", ""
        return None, "no local ComfyUI is ready"

    if route == "api":
        if api_ready and allow_paid:
            return "api", ""
        return None, api_reason()

    # route == "auto"
    if local_ready:
        return "local", ""
    if api_ready and allow_paid:
        return "api", ""
    return None, f"no local ComfyUI is ready and {api_reason()}"


# Touch schemas.CAMERA_MOTIONS/MODIFIERS at import time so a future edit to
# either closed list fails here first, loudly, rather than silently
# desyncing CAMERA_PHRASES/MODIFIER_PHRASES from schemas.py.
assert set(CAMERA_PHRASES) == set(schemas.CAMERA_MOTIONS)
assert set(MODIFIER_PHRASES) == set(schemas.MODIFIERS)
