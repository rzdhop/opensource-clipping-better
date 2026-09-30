"""Tier-1 camera motion and per-shot overlay filter fragments for the
AI-Story renderer (spec 6.3, 6.5; plan phase 4 stage 4, "Renderer" ->
motion.py; DEC-156).

Every function here returns a small, deterministic ffmpeg filter
*expression* (an eval-syntax string) or a filter *fragment* (one
comma-chain segment of a filtergraph) -- never an argv. ``render/
filtergraph.py`` is the only caller and the only place a full argv is
assembled.

Every ``camera_motion`` (``schemas.CAMERA_MOTIONS``, spec 6.3's closed
list) becomes one ``zoompan`` filter's ``z``/``x``/``y`` triple, eased with
a cosine ease-in-out over the shot's own frame count (``on`` is zoompan's
built-in current-output-frame variable, 0-based; ``frames - 1`` is its last
frame, so ``on/(frames-1)`` is exactly the shot's own elapsed-time fraction:
0 at its first rendered frame, 1 at its last -- spec 6.5's "eased
expressions from motion.type").

**Panning and zoom room.** ``shots.motion_for`` reports ``zoom_from ==
zoom_to == 1.0`` for every ``pan_*`` type (a pure pan, no dolly) -- but
``zoompan`` crops ``iw/zoom`` pixels out of the (pre-scaled) frame, so at
``zoom == 1.0`` there is *no* pixel of room left to slide the crop window
across (the crop already covers the whole frame). A pan shot therefore
holds its own small constant zoom while it renders, :func:`pan_zoom`,
computed so the crop leaves exactly ``pan_pct`` percent of the frame's own
width/height as travel room:  ``room = iw*(1 - 1/zoom)``, solved for
``zoom`` so that ``room == iw*pan_pct/100``. The shot's own reported
``zoom_from``/``zoom_to`` (both 1.0, "no dolly") are left exactly as they
are -- this constant zoom is an internal zoompan detail of the *renderer*,
never a change to the shot's own schema/storyboard document.
:data:`PAN_PCT` (4, spec 5's shipped default for both fruit_drama and
family_3d) is this module's own default when no caller passes a value.
Phase 5 stage 12 (DEC-183) plumbs each style's own
``motion_rules.tier1.pan_pct`` through ``filtergraph``/``plan`` down to
:func:`zoompan_expr`'s ``pan_pct`` keyword, which already existed here --
what stage 12 added is a caller (``plan.py``) that ever passes a value
other than this default.

**Escaping.** None of the expressions built here ever contain ``:``, ``,``
or ``;`` by design (see :func:`_step_sign`'s comma-free ``floor``-based
alternator, used in place of ``mod(a,b)``/``if(a,b,c)``, for
``jitter_stopmotion``'s stepped offset) -- so escaping is never exercised
on the *real* expressions built here. :func:`escape_expr` is still
provided, used by :func:`_quoted` on every value, and unit-tested directly
with a contrived comma/colon-bearing string, matching ffmpeg's own
escaping rule for a special character inside a single-quoted filter option
value (spec 6.5's own convention, ``z='<expr>'``).

Stdlib only (DEC-012). Every expression is deterministic text: the same
inputs always produce the exact same string (golden-testable, spec 13).
"""

from __future__ import annotations

from . import profiles

# --------------------------------------------------------------- constants

PAN_PCT = 4.0        # spec 5: both shipped styles' motion_rules.tier1.pan_pct == 4
HANDHELD_PX = 2.0     # spec 6.3: handheld modifier, +/-2 px sinusoidal crop offsets
JITTER_PX = 1.0       # spec 6.3: jitter_stopmotion modifier, +/-1 px
JITTER_FPS = 12       # spec 6.5: jitter_stopmotion renders at 12 fps, then fps=30
NOISE_STRENGTH = 8.0  # film_grain overlay: ffmpeg noise filter's "alls" strength (0-100)

_CAMERA_MOTIONS = ("hold", "push_in", "pull_out", "pan_lr", "pan_rl", "pan_ud", "pan_du")
_PAN_MOTIONS = ("pan_lr", "pan_rl", "pan_ud", "pan_du")


# ------------------------------------------------------------------ numbers

def _num(value: float) -> str:
    """A deterministic, minimal decimal literal: 6 fixed decimals, then
    trailing zeros and a bare trailing '.' trimmed -- never scientific
    notation, never locale-dependent."""
    text = f"{round(float(value), 6):.6f}".rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0"
    return text


def escape_expr(expr: str) -> str:
    """Escape ffmpeg filtergraph-special characters (``\\``, ``:``, ``,``,
    ``;``) inside one expression -- backslash first, so an
    already-escaped character is never re-escaped."""
    out = expr.replace("\\", "\\\\")
    for ch in (":", ",", ";"):
        out = out.replace(ch, "\\" + ch)
    return out


def _quoted(expr: str) -> str:
    """*expr* as a zoompan/crop option value: escaped, then single-quoted
    (spec 6.5's own convention, ``z='<expr>'``)."""
    return f"'{escape_expr(expr)}'"


# ------------------------------------------------------------------ easing

def _ease_t(frames: int) -> str:
    """Cosine ease-in-out on ``on/(frames-1)``:
    ``0.5-0.5*cos(PI*on/(frames-1))``. A shot of 1 frame or fewer has no
    time axis -- pinned at ``1`` (its end state), so a caller's
    zoom_from/zoom_to interpolation still lands exactly on ``zoom_to`` and
    never divides by zero."""
    if frames <= 1:
        return "1"
    return f"(0.5-0.5*cos(PI*on/{frames - 1}))"


def _eased_value(frm: float, to: float, frames: int) -> str:
    """``frm`` eased to ``to`` over *frames*: a bare constant when the two
    are equal (``hold``, and every ``pan_*`` type reports ``zoom_from ==
    zoom_to`` -- spec: no dolly), else the eased interpolation."""
    if frm == to:
        return _num(frm)
    return f"({_num(frm)}+({_num(to)}-{_num(frm)})*{_ease_t(frames)})"


# ------------------------------------------------------------------ pan room

def pan_zoom(pan_pct: float = PAN_PCT) -> float:
    """The constant zoom a ``pan_*`` shot holds so its crop leaves exactly
    *pan_pct* percent of the frame as travel room (see module docstring):
    ``1 / (1 - pan_pct/100)``."""
    return 1.0 / (1.0 - pan_pct / 100.0)


# ------------------------------------------------------------------ zoompan

def zoompan_expr(motion: dict, frames: int, *, modifiers=(), pan_pct: float = PAN_PCT) -> dict:
    """``{"z", "x", "y"}``: the eased, quoted zoompan option values for one
    shot's ``motion`` (``shots.motion_for``'s ``{type, zoom_from, zoom_to,
    pan}``) over *frames* output frames (the frame count actually passed
    to zoompan's own ``d=``; the caller picks it -- :func:`jitter_stopmotion_frames`
    when ``"jitter_stopmotion" in modifiers``, else the shot's normal 30 fps
    frame count).

    - ``hold``/``push_in``/``pull_out``: ``z`` eases ``zoom_from`` to
      ``zoom_to`` (spec 6.5); ``x``/``y`` stay centred on the current zoom
      (``iw/2-(iw/zoom/2)``, ``ih/2-(ih/zoom/2)`` -- spec 6.5's own
      formula, using zoompan's own ``zoom`` variable so it tracks whatever
      ``z`` evaluates to at each frame).
    - ``pan_lr``/``pan_rl``/``pan_ud``/``pan_du``: ``z`` holds
      :func:`pan_zoom` constant; the panning axis eases from one edge of
      its travel room to the other (``lr``: low to high x; ``rl``: the
      reverse; ``ud``: low to high y; ``du``: the reverse); the other axis
      stays centred with the same formula as above.
    - ``"jitter_stopmotion" in modifiers`` adds a small deterministic
      +/-:data:`JITTER_PX` term (:func:`_step_sign`, alternating every raw
      frame) to both ``x`` and ``y``, on top of whatever the motion itself
      already produced.

    Raises ``ValueError`` naming the motion for anything outside
    ``schemas.CAMERA_MOTIONS``.
    """
    motion_type = motion["type"]
    if motion_type not in _CAMERA_MOTIONS:
        raise ValueError(f"not a Tier-1 camera_motion: {motion_type!r}")

    centred_x = "(iw/2-(iw/zoom/2))"
    centred_y = "(ih/2-(ih/zoom/2))"

    if motion_type not in _PAN_MOTIONS:
        z = _eased_value(motion["zoom_from"], motion["zoom_to"], frames)
        x, y = centred_x, centred_y
    else:
        zoom = pan_zoom(pan_pct)
        ease = _ease_t(frames)
        reverse = f"(1-{ease})"
        room_x, room_y = "(iw-iw/zoom)", "(ih-ih/zoom)"
        z = _num(zoom)
        if motion_type == "pan_lr":
            x, y = f"{room_x}*{ease}", centred_y
        elif motion_type == "pan_rl":
            x, y = f"{room_x}*{reverse}", centred_y
        elif motion_type == "pan_ud":
            x, y = centred_x, f"{room_y}*{ease}"
        else:  # pan_du
            x, y = centred_x, f"{room_y}*{reverse}"

    if "jitter_stopmotion" in modifiers:
        jitter = jitter_offset_expr()
        x = f"({x}+{jitter})"
        y = f"({y}+{jitter})"

    return {"z": _quoted(z), "x": _quoted(x), "y": _quoted(y)}


# --------------------------------------------------------------- modifiers

def handheld_margin_px() -> int:
    """The extra width/height zoompan must render beyond
    ``profiles.WIDTH``x``profiles.HEIGHT`` so a following ``crop`` has room
    for the +/-:data:`HANDHELD_PX` sinusoidal offset on both axes (spec
    6.3's ``handheld`` modifier): ``2*HANDHELD_PX`` per axis (so the crop
    window can sit anywhere from 0 to the full margin without ever asking
    for a negative or an out-of-frame offset)."""
    return int(2 * HANDHELD_PX)


def zoompan_canvas(modifiers) -> tuple:
    """``(width, height)`` zoompan itself renders to (its own ``s=``
    option): the profile's own frame size, or with
    :func:`handheld_margin_px` added on each axis when ``"handheld"`` is
    one of the shot's modifiers -- so the ``crop`` that follows always has
    exactly the room it needs, never negative and never short."""
    if "handheld" in modifiers:
        margin = handheld_margin_px()
        return profiles.WIDTH + margin, profiles.HEIGHT + margin
    return profiles.WIDTH, profiles.HEIGHT


def handheld_crop_expr(frames: int) -> dict:
    """``{"w", "h", "x", "y"}`` for the ``crop`` filter spec 6.3's
    ``handheld`` modifier adds right after zoompan: a static
    ``profiles.WIDTH``x``profiles.HEIGHT`` window sliding +/-
    :data:`HANDHELD_PX` inside the extra canvas :func:`zoompan_canvas`
    rendered, on two independent sinusoids (a single shared one would move
    both axes in lockstep, an unconvincing handheld look) -- one full cycle
    across the shot's own *frames* on x, one and a half cycles on y (a
    classic handheld "figure-8" drift, still fully deterministic and
    bounded, see the module's escaping note).

    ``crop``'s own per-frame variable is ``n`` (its "number of the input
    frame, starting from 0") -- NOT zoompan's ``on``: the two filters are
    never the same expression context, even chained back to back."""
    margin = handheld_margin_px()
    cx = margin / 2.0
    cy = margin / 2.0
    n = max(frames, 1)
    x = f"({_num(cx)}+{_num(HANDHELD_PX)}*sin(2*PI*n/{n}))"
    y = f"({_num(cy)}+{_num(HANDHELD_PX)}*sin(3*PI*n/{n}))"
    return {"w": str(profiles.WIDTH), "h": str(profiles.HEIGHT), "x": _quoted(x), "y": _quoted(y)}


def jitter_stopmotion_frames(duration_s: float) -> int:
    """How many raw zoompan frames to render at :data:`JITTER_FPS` for a
    shot lasting *duration_s* seconds (spec 6.5: "renders at 12 fps then
    duplicates frames to 30") -- rounded to the nearest frame, floored at 1
    so even a very short shot still gets one."""
    return max(1, round(duration_s * JITTER_FPS))


def _step_sign(period_frames: int = 1) -> str:
    """A deterministic +/-1 alternator over zoompan's own ``on``, comma-free
    (module docstring): ``2*(on - period*floor(on/period)) - 1`` -- the
    arithmetic form of ``on mod period``, without the ``mod()``/``if()``
    functions ffmpeg's eval syntax would need a literal comma for. Flips
    every *period_frames* raw frames."""
    p = max(1, int(period_frames))
    return f"(2*(on-{p}*floor(on/{p}))-1)"


def jitter_offset_expr() -> str:
    """The +/-:data:`JITTER_PX` term added to a jittered shot's x AND y
    expressions (:func:`zoompan_expr`), alternating every raw (12 fps)
    frame -- spec 6.3's ``jitter_stopmotion`` modifier."""
    return f"({_num(JITTER_PX)}*{_step_sign(1)})"


# --------------------------------------------------------------- overlays

def noise_fragment() -> str:
    """``film_grain`` overlay: ffmpeg's ``noise`` filter, temporal +
    uniform grain at :data:`NOISE_STRENGTH` (spec 6.3)."""
    return f"noise=alls={_num(NOISE_STRENGTH)}:allf=t+u"


def vignette_fragment() -> str:
    """``vignette`` overlay: ffmpeg's ``vignette`` filter at a fixed,
    named angle (spec 6.3) -- explicit rather than the bare filter name so
    the golden string is self-documenting and never silently follows a
    future ffmpeg default change."""
    return "vignette=angle=PI/5"
