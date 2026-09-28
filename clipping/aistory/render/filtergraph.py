"""Pure ffmpeg argv builders for the AI-Story renderer (spec 6.5; plan
phase 4 stage 4, "Renderer" -> filtergraph.py's Shot / Tier >= 2 clip / End
card bullets; DEC-156). The audio mix, the final pass and the cover are
stage 6/9's own additions to this module, not here.

Every builder returns ``list[str]``: a full ``ffmpeg`` argv, always
starting ``["ffmpeg", "-hide_banner", "-nostdin", "-y"]`` and never
containing an absolute path (the render runner's own working directory is
the episode's ``render/`` folder, spec: "every argv uses relative paths" --
:func:`_assert_relative` enforces this on every path argument a caller
hands in, not just documents it).

These builders touch no filesystem and run no subprocess -- "pure" in the
same sense ``clipping.aistory.shots``/``timing`` are: same inputs, same
argv, every time (golden-testable, spec 13).
"""

from __future__ import annotations

from . import motion as motion_mod
from . import profiles

# The bundled paper_texture.png is a single shared asset (not per-episode
# content), so unlike a shot's own image it is not staged under its content
# hash (store.EPISODE_RENDER_SUBDIRS's "in/" convention) -- it is staged by
# the render runner (stage 7) at this fixed, human-readable relative name.
# Stage 7 must honour this constant (or this constant must move with it).
PAPER_TEXTURE_REL = "in/paper_texture.png"

# spec 6.5: overlays "blended last from bundled PNG/noise filters" -- the
# paper_texture PNG is composited onto the shot with a plain `blend` filter
# at a fixed mode/opacity, applied after every other filter but before the
# final format conversion.
PAPER_TEXTURE_BLEND_MODE = "overlay"
PAPER_TEXTURE_OPACITY = 0.25

_ARGV_PREFIX = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]


# ------------------------------------------------------------------ helpers

def _num(value) -> str:
    """A deterministic, minimal decimal literal (matches
    ``motion._num``'s own formatting so a duration/opacity reads the same
    way anywhere in a graph)."""
    text = f"{round(float(value), 6):.6f}".rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0"
    return text


def _assert_relative(path, *, what: str = "path") -> None:
    """Refuse an absolute path (spec: "no absolute path may appear in any
    argv") -- unix-rooted, a bare backslash-rooted path, or a Windows drive
    letter, so a caller's mistake is a loud, named error here rather than a
    silent path leak into a committed golden string."""
    if not isinstance(path, str) or not path:
        raise ValueError(f"{what} must be a non-empty relative path, got {path!r}")
    if path.startswith("/") or path.startswith("\\"):
        raise ValueError(f"{what} must be relative, not absolute: {path!r}")
    if len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        raise ValueError(f"{what} must be relative, not a Windows absolute path: {path!r}")


def _base_chain(image_rel, shot, profile) -> tuple:
    """``(chain_fragments, zoompan_frames, zoompan_fps)`` for one shot:
    ``scale`` (per *profile*'s upscale), then ``zoompan`` (eased per
    ``shot["motion"]``/``shot["modifiers"]``), then the ``handheld`` crop
    when present. Does not include overlays or ``format`` -- those are
    layered on by :func:`shot_argv` itself, since ``paper_texture``
    branches the graph (module docstring)."""
    motion = shot["motion"]
    modifiers = shot["modifiers"]
    duration_s = shot["duration_s"]

    jittered = "jitter_stopmotion" in modifiers
    zoompan_fps = motion_mod.JITTER_FPS if jittered else profiles.FPS
    zoompan_frames = motion_mod.jitter_stopmotion_frames(duration_s) if jittered else shot["frames"]

    z = motion_mod.zoompan_expr(motion, zoompan_frames, modifiers=modifiers)
    canvas_w, canvas_h = motion_mod.zoompan_canvas(modifiers)
    scaled_w = profiles.WIDTH * profile.upscale

    chain = [f"scale={scaled_w}:-2"]
    chain.append(
        f"zoompan=z={z['z']}:x={z['x']}:y={z['y']}:d={zoompan_frames}:s={canvas_w}x{canvas_h}:fps={zoompan_fps}"
    )
    if jittered:
        chain.append(f"fps={profiles.FPS}")
    if "handheld" in modifiers:
        # crop always runs on the FINAL 30 fps stream (after the optional
        # jitter fps=30 conversion above), so its own frame count is the
        # shot's normal 30 fps target -- never the raw 12 fps zoompan_frames
        # jitter_stopmotion may have used upstream (crop's own "n" variable
        # counts frames on ITS OWN input, not zoompan's "on").
        crop = motion_mod.handheld_crop_expr(shot["frames"])
        chain.append(f"crop={crop['w']}:{crop['h']}:x={crop['x']}:y={crop['y']}")

    return chain, zoompan_frames, zoompan_fps


def _overlay_fragments(style_overlays) -> list:
    """``film_grain``/``vignette`` fragments, in that fixed order (spec
    6.3's own listing order) so two shots with the same overlay set always
    produce the same graph. ``paper_texture`` is handled separately by
    :func:`shot_argv` -- it needs a second filter-graph source, not a plain
    chained fragment."""
    fragments = []
    if "film_grain" in style_overlays:
        fragments.append(motion_mod.noise_fragment())
    if "vignette" in style_overlays:
        fragments.append(motion_mod.vignette_fragment())
    return fragments


# -------------------------------------------------------------------- shot

def shot_argv(image_rel, shot, profile, style_overlays, out_rel) -> list:
    """The argv for one shot's image -> clip render (spec 6.5): a looped
    still image, scaled by *profile*'s own upscale factor, an eased
    zoompan driven by ``shot["motion"]`` and ``shot["modifiers"]``
    (``handheld``, ``jitter_stopmotion`` -- :mod:`motion`), *style_overlays*
    (``film_grain``, ``vignette``, ``paper_texture`` -- spec 6.3, a style's
    own ``motion_rules.tier1.overlays``, applied in a fixed order), a final
    ``format=<profile.pix_fmt>``, and *profile*'s own encode settings.

    *shot* is a ``render.timeline`` shot entry: ``{"duration_s", "frames",
    "motion", "modifiers", ...}`` (``build_timeline``'s per-shot dict --
    ``frames`` there is the shot's own exact 30 fps frame count). The graph
    itself may run at a different internal fps/frame-count when
    ``jitter_stopmotion`` is one of the modifiers (12 fps, then an ffmpeg
    ``fps=30`` conversion whose own frame count is only approximately
    exact), so the argv always finishes with ``-frames:v shot["frames"]``
    to pin the exact target regardless of that internal rounding.

    *image_rel*/*out_rel* (and, when ``"paper_texture" in style_overlays``,
    :data:`PAPER_TEXTURE_REL`) must be relative to the render runner's own
    working directory -- asserted, not just documented
    (:func:`_assert_relative`).
    """
    _assert_relative(image_rel, what="image_rel")
    _assert_relative(out_rel, what="out_rel")

    chain, _zoompan_frames, _zoompan_fps = _base_chain(image_rel, shot, profile)
    chain.extend(_overlay_fragments(style_overlays))
    main_chain = ",".join(chain)

    if "paper_texture" in style_overlays:
        filter_complex = (
            f"[0:v]{main_chain}[base];"
            f"movie={PAPER_TEXTURE_REL},scale={profiles.WIDTH}:{profiles.HEIGHT}[tex];"
            f"[base][tex]blend=all_mode={PAPER_TEXTURE_BLEND_MODE}:all_opacity={_num(PAPER_TEXTURE_OPACITY)}[blended];"
            f"[blended]format={profile.pix_fmt}[out]"
        )
    else:
        filter_complex = f"[0:v]{main_chain},format={profile.pix_fmt}[out]"

    argv = list(_ARGV_PREFIX)
    argv += profile.global_bitexact_args()
    argv += ["-loop", "1", "-i", image_rel]
    argv += ["-filter_complex", filter_complex, "-map", "[out]"]
    argv += profile.video_encode_args()
    argv += ["-r", str(profiles.FPS), "-frames:v", str(shot["frames"]), "-an", out_rel]
    return argv


# ------------------------------------------------------------- tier >= 2

def tier2_clip_argv(video_rel, shot, profile, out_rel) -> list:
    """The argv for a Tier >= 2 shot that already has its own ``.mp4``
    (spec 6.5): scaled and padded to ``profiles.WIDTH``x``profiles.HEIGHT``
    (letterboxed, never cropped or stretched -- ``force_original_aspect_ratio
    =decrease`` + a centred ``pad``), resampled to a 30 fps CFR stream, and
    trimmed to the shot's own ``duration_s``. A golden-string-only builder
    at this stage (no Tier >= 2 source exists yet to sanity-run it against,
    DEC-158/phase 6): the argv is still exact and relative-paths-only.

    *shot* is a ``render.timeline`` entry, read here for ``duration_s`` and
    ``frames`` only (a Tier >= 2 clip carries no Tier-1 ``motion``).
    """
    _assert_relative(video_rel, what="video_rel")
    _assert_relative(out_rel, what="out_rel")

    vf = (
        f"scale={profiles.WIDTH}:{profiles.HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={profiles.WIDTH}:{profiles.HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={profiles.FPS},"
        f"trim=duration={_num(shot['duration_s'])},"
        f"format={profile.pix_fmt}"
    )

    argv = list(_ARGV_PREFIX)
    argv += profile.global_bitexact_args()
    argv += ["-i", video_rel]
    argv += ["-vf", vf]
    argv += profile.video_encode_args()
    argv += ["-r", str(profiles.FPS), "-frames:v", str(shot["frames"]), "-an", out_rel]
    return argv


# ------------------------------------------------------------------ end card

def end_card_argv(ass_rel, fontsdir_rel, duration_s, profile, out_rel) -> list:
    """The argv for the 1.0 s end card (spec 6.2/6.4/6.5): a black
    ``color`` source at ``profiles.WIDTH``x``profiles.HEIGHT``/30 fps for
    *duration_s* seconds, with the card's ``ass_rel`` burned in via
    ``fontsdir_rel`` (``render/fonts.py``'s staged font directory, stage
    5). ``-frames:v`` is the exact ``round(duration_s * fps)`` (floored at
    1), matching ``render.timeline``'s own end-card ``duration_s``.
    """
    _assert_relative(ass_rel, what="ass_rel")
    _assert_relative(fontsdir_rel, what="fontsdir_rel")
    _assert_relative(out_rel, what="out_rel")

    frames = max(1, round(duration_s * profiles.FPS))
    ass_value = motion_mod.escape_expr(ass_rel)
    fontsdir_value = motion_mod.escape_expr(fontsdir_rel)
    vf = f"ass={ass_value}:fontsdir={fontsdir_value},format={profile.pix_fmt}"

    argv = list(_ARGV_PREFIX)
    argv += profile.global_bitexact_args()
    argv += ["-f", "lavfi", "-i",
             f"color=black:s={profiles.WIDTH}x{profiles.HEIGHT}:r={profiles.FPS}:d={_num(duration_s)}"]
    argv += ["-vf", vf]
    argv += profile.video_encode_args()
    argv += ["-r", str(profiles.FPS), "-frames:v", str(frames), "-an", out_rel]
    return argv
