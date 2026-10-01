"""Pure ffmpeg argv builders for the AI-Story renderer (spec 6.5; plan
phase 4 stage 4, "Renderer" -> filtergraph.py's Shot / Tier >= 2 clip / End
card bullets; stage 6, its Audio mix / Final pass bullets; DEC-156,
DEC-157, DEC-158; stage 9, its Cover bullet: :func:`cover_argv`).

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


def _base_chain(image_rel, shot, profile, *, pan_pct=motion_mod.PAN_PCT) -> tuple:
    """``(chain_fragments, zoompan_frames, zoompan_fps)`` for one shot:
    ``scale`` (per *profile*'s upscale), then ``zoompan`` (eased per
    ``shot["motion"]``/``shot["modifiers"]``, its pan travel room per
    *pan_pct* -- phase 5 stage 12, DEC-183), then the ``handheld`` crop
    when present. Does not include overlays or ``format`` -- those are
    layered on by :func:`shot_argv` itself, since ``paper_texture``
    branches the graph (module docstring)."""
    motion = shot["motion"]
    modifiers = shot["modifiers"]
    duration_s = shot["duration_s"]

    jittered = "jitter_stopmotion" in modifiers
    zoompan_fps = motion_mod.JITTER_FPS if jittered else profiles.FPS
    zoompan_frames = motion_mod.jitter_stopmotion_frames(duration_s) if jittered else shot["frames"]

    z = motion_mod.zoompan_expr(motion, zoompan_frames, modifiers=modifiers, pan_pct=pan_pct)
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

def shot_argv(image_rel, shot, profile, style_overlays, out_rel, *, pan_pct=motion_mod.PAN_PCT) -> list:
    """The argv for one shot's image -> clip render (spec 6.5): a looped
    still image, scaled by *profile*'s own upscale factor, an eased
    zoompan driven by ``shot["motion"]`` and ``shot["modifiers"]``
    (``handheld``, ``jitter_stopmotion`` -- :mod:`motion`), *style_overlays*
    (``film_grain``, ``vignette``, ``paper_texture`` -- spec 6.3, a style's
    own ``motion_rules.tier1.overlays``, applied in a fixed order), a final
    ``format=<profile.pix_fmt>``, and *profile*'s own encode settings.

    *pan_pct* (phase 5 stage 12, DEC-183) is the style's own
    ``motion_rules.tier1.pan_pct`` -- how much of the frame a ``pan_*``
    shot's crop leaves as travel room (:func:`motion.pan_zoom`); it affects
    only ``pan_lr``/``pan_rl``/``pan_ud``/``pan_du`` shots, never
    ``hold``/``push_in``/``pull_out``. Defaults to :data:`motion.PAN_PCT`
    (4), so every existing caller that does not pass it is byte-for-byte
    unaffected -- both shipped MVP styles (fruit_drama, family_3d) carry
    exactly that value too (asserted in
    ``tests/test_aistory_render_runner.py``), so plumbing the template's own
    value through the renderer (``plan.py``) never moves their argv.

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

    chain, _zoompan_frames, _zoompan_fps = _base_chain(image_rel, shot, profile, pan_pct=pan_pct)
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
    =decrease`` + a centred ``pad``), resampled to a 30 fps CFR stream,
    held on its last frame (``tpad=stop_mode=clone``, phase 6 stage 9) and
    trimmed to the shot's own ``duration_s``; ``-frames:v`` pins the shot's
    exact frames and ``-an`` drops the clip's own sound (tier 2 never keeps
    it; tier 3's native audio is a stem of the audio mix, never this clip's
    track).

    **The hold.** A clip is sold in whole seconds, at least the shot's
    length -- but a clip may still come back shorter (a model's own frame
    rule, a local workflow's frame cap): without the hold its clip would end
    early, and every later shot, the subtitles and the audio would drift
    against the final pass's frame clock. ``stop_duration`` is the shot's own
    duration -- always enough, whatever the clip's length, so the argv never
    depends on probing the file -- and the ``trim`` after it cuts whatever
    is too long, the hold included.

    *shot* is a ``render.timeline`` entry, read here for ``duration_s`` and
    ``frames`` only (a Tier >= 2 clip carries no Tier-1 ``motion``).
    """
    _assert_relative(video_rel, what="video_rel")
    _assert_relative(out_rel, what="out_rel")

    duration = _num(shot["duration_s"])
    vf = (
        f"scale={profiles.WIDTH}:{profiles.HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={profiles.WIDTH}:{profiles.HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={profiles.FPS},"
        f"tpad=stop_mode=clone:stop_duration={duration},"
        f"trim=duration={duration},"
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


# ================================================================ stage 6
#
# The sequence (final pass) and the audio mix (spec 6.5 "Sequence" and
# "Audio graph"; plan phase 4 stage 6; DEC-157, DEC-158).
#
# **One clock: integer frames.** Every shot clip is rendered to exactly its
# timeline ``frames`` (``shot_argv``'s ``-frames:v``) and the end card to
# ``round(duration_s * FPS)``, so the final pass never works in float
# seconds. Shots joined by ``cut`` are one *segment* (``concat``); segments
# are joined by ``xfade``. With ``F`` the frame count of the stream
# accumulated so far and ``T`` the transition's own frame count, the join's
# offset is ``O = F - T`` and the joined stream has ``O + frames(next
# segment)`` frames (ffmpeg's xfade: frame ``O`` of the accumulated stream is
# the blend's first frame, pure outgoing picture; the outgoing stream ends
# exactly at ``O + T``; the incoming segment's frame ``T`` onwards follows
# unchanged). Every transition lasts a whole number of frames (the template's
# 0.4 s / 0.3 s are 12 / 9 frames; anything else is refused), so a shot's
# first output frame is ``round(sum(raw durations before it) * FPS) -
# sum(earlier transition frames)`` == ``round(start_s * FPS)``: within half
# a frame of the timeline's own ``start_s``, however many joins precede it,
# and the whole movie is exactly ``timeline["total_frames"]`` long (asserted,
# :func:`sequence_plan`).
#
# **The audio is one absolute timeline** (DEC-158): every stem starts from a
# silent base of exactly ``total_s`` and every amix runs ``duration=first``
# with that base (or the dialogue stem built on it) first, so the mix is
# ``total_s`` long whatever the inputs. The final pass therefore needs no
# ``-shortest``: video == ``total_frames / FPS`` and audio == ``total_s``,
# and ``total_frames == round(total_s * FPS)`` (``build_timeline``), so the
# two differ by less than half a frame.
#
# ffmpeg behaviours this graph depends on (measured on 6.1.1, stage-6 scratch
# proof; the golden render of stage 7 pins them on every ffmpeg it knows):
#
# - ``concat`` outputs a 1/1000000 time base whatever its inputs', and
#   ``xfade`` refuses two inputs whose time bases differ, so every
#   multi-shot segment is followed by ``settb=1/FPS``. That also turns the
#   concat's microsecond timestamps back into exact frame numbers, so an
#   offset of ``k/FPS`` s always lands on frame ``k`` (xfade rescales the
#   offset to the link's time base, rounding to the nearest).
# - ``-stream_loop -1`` on an mp3 bed produces overlapping timestamps at
#   each loop seam; ``atrim=duration=`` on those timestamps comes out ~50 ms
#   short, so the bed is re-stamped by sample count (``asetpts=N/SR/TB``)
#   before it is trimmed.
# - ``sidechaincompress`` ends when EITHER input ends, so the sidechain (the
#   dialogue stem) must be as long as the bed: it is, being built on the
#   ``total_s`` base.
# - ``xfade=fadeblack`` is not black at the window's midpoint: it takes the
#   outgoing picture to black over the first ~20 % of the window, holds
#   black briefly, then fades the incoming picture in.


class GraphError(ValueError):
    """Raised by the stage-6 builders when a timeline (or the inputs handed
    in with it) cannot be turned into a frame-exact sequence or an exact-
    length mix: a transition that is not a whole number of frames, a
    sequence whose frame count disagrees with ``timeline["total_frames"]``,
    a line inside a transition window, a missing or unexpected input, an
    ``ending`` that disagrees with the timeline's end card."""


# spec 6.3's transition closed list (schemas.TRANSITIONS) -> ffmpeg's xfade
# transition names. ``cut`` is not an xfade: runs of cuts are concatenated.
XFADE_TRANSITIONS = {
    "dissolve": "fade",
    "fadeblack": "fadeblack",
    "fadewhite": "fadewhite",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    "slideup": "slideup",
}

END_CARD_ID = "end_card"
STEM_KINDS = ("dialogue", "bgm", "sfx")

_FRAME_EPS = 1e-6


def _transition_frames(transition, *, after, fps) -> int:
    """The transition's own length in whole frames: 0 for ``cut`` (which
    must last 0 s), ``duration_s * fps`` for every xfade transition, which
    must be a whole number of frames and at least one (an offset/duration
    that is not frame-exact is what drifts a stream by a frame)."""
    kind = transition["type"]
    duration_s = float(transition["duration_s"])
    if kind == "cut":
        if abs(duration_s) > _FRAME_EPS:
            raise GraphError(f"the cut after {after!r} lasts {duration_s}s; a cut must last 0s")
        return 0
    if kind not in XFADE_TRANSITIONS:
        raise GraphError(f"unknown transition {kind!r} after {after!r}")
    exact = duration_s * fps
    frames = round(exact)
    if frames < 1 or abs(exact - frames) > _FRAME_EPS:
        raise GraphError(
            f"the {kind} after {after!r} lasts {duration_s}s = {exact:g} frames at {fps} fps; "
            f"a transition must last a whole number of frames (at least 1)"
        )
    return frames


def _end_card_frames(end_card) -> int:
    """The end card clip's own frame count -- the same rule as
    :func:`end_card_argv`'s ``-frames:v``."""
    return max(1, round(end_card["duration_s"] * profiles.FPS))


def sequence_plan(timeline) -> dict:
    """The final pass's frame arithmetic, pure (module section above):
    ``{"fps", "segments", "joins", "start_frames", "total_frames"}``.

    - ``segments``: ``[{"items": [shot_id, ...], "frames": n}, ...]`` --
      each a maximal run of shots joined by ``cut`` (the end card, id
      :data:`END_CARD_ID`, is a segment of its own under ``cut_to_black``).
    - ``joins``: one per xfade, in order -- ``{"after", "into",
      "outgoing_scene_id", "type", "xfade", "offset_frames",
      "duration_frames", "offset", "duration"}`` (``offset``/``duration``
      are the exact argv strings, seconds).
    - ``start_frames``: ``{shot_id or END_CARD_ID: first output frame}``.
    - ``total_frames``: the sequence's own frame count, asserted equal to
      ``timeline["total_frames"]``.

    Raises :class:`GraphError` when the timeline's transitions are not
    frame-exact, a transition would be longer than a shot it joins, the
    last shot's ``transition_after`` disagrees with the ending, or the
    frame total disagrees with the timeline's.
    """
    fps = profiles.FPS
    if timeline["fps"] != fps:
        raise GraphError(f"timeline fps {timeline['fps']} != the renderer's {fps}")
    shots = timeline["shots"]
    if not shots:
        raise GraphError("the timeline has no shots")
    end_card = timeline["end_card"]

    items = [(shot["shot_id"], shot["frames"], shot["scene_id"]) for shot in shots]
    links = [shot["transition_after"] for shot in shots[:-1]]
    last_transition = shots[-1]["transition_after"]
    if end_card is not None:
        if last_transition is None or last_transition["type"] != "fadeblack":
            raise GraphError(f"cut_to_black: the last shot must fade to black into the end card, "
                             f"got {last_transition!r}")
        items.append((END_CARD_ID, _end_card_frames(end_card), None))
        links.append(last_transition)
    elif last_transition is not None:
        raise GraphError(f"hard_stop: the last shot ends the file, but it carries {last_transition!r}")

    for i, link in enumerate(links):
        if link is None:
            raise GraphError(f"shot {items[i][0]!r} has no transition_after but is not the last shot")

    segments = [{"items": [items[0][0]], "frames": items[0][1]}]
    joins = []
    start_frames = {items[0][0]: 0}
    accumulated = items[0][1]
    for i, link in enumerate(links):
        after_id, after_frames, after_scene = items[i]
        into_id, into_frames, _into_scene = items[i + 1]
        t_frames = _transition_frames(link, after=after_id, fps=fps)
        if t_frames == 0:
            start_frames[into_id] = accumulated
            segments[-1]["items"].append(into_id)
            segments[-1]["frames"] += into_frames
            accumulated += into_frames
            continue
        if t_frames > after_frames or t_frames > into_frames:
            raise GraphError(
                f"the {link['type']} between {after_id!r} ({after_frames} frames) and {into_id!r} "
                f"({into_frames} frames) lasts {t_frames} frames, longer than a shot it joins"
            )
        offset_frames = accumulated - t_frames
        joins.append({
            "after": after_id,
            "into": into_id,
            "outgoing_scene_id": after_scene,
            "type": link["type"],
            "xfade": XFADE_TRANSITIONS[link["type"]],
            "offset_frames": offset_frames,
            "duration_frames": t_frames,
            "offset": _num(offset_frames / fps),
            "duration": _num(t_frames / fps),
        })
        start_frames[into_id] = offset_frames
        segments.append({"items": [into_id], "frames": into_frames})
        accumulated = offset_frames + into_frames

    if accumulated != timeline["total_frames"]:
        raise GraphError(
            f"the sequence is {accumulated} frames long but the timeline says {timeline['total_frames']} "
            f"(total_s {timeline['total_s']}): a shot clip's frame count disagrees with the timeline"
        )
    return {"fps": fps, "segments": segments, "joins": joins, "start_frames": start_frames,
            "total_frames": accumulated}


def xfade_offsets(timeline) -> list:
    """The xfade joins of :func:`sequence_plan`, in order: every offset and
    duration in whole frames (``offset_frames``/``duration_frames``) and as
    the exact argv seconds strings (``offset``/``duration``). An all-cut,
    ``hard_stop`` timeline has none (one ``concat``)."""
    return sequence_plan(timeline)["joins"]


def _assert_lines_clear_of_joins(timeline, plan) -> None:
    """The stage-4 invariant (``timeline._assert_no_line_in_a_transition_
    window``), re-checked on the windows the graph actually renders:
    ``[offset_frames, offset_frames + duration_frames] / fps`` for every
    join, against the lines of the join's OUTGOING scene only (same scoping,
    same reason). The frame-exact window can sit up to half a frame away
    from the timeline's float window, so each side gets that much slack --
    a line ending exactly where the timeline's window starts is not a
    violation here either."""
    fps = plan["fps"]
    slack = 0.5 / fps + _FRAME_EPS
    by_scene = {}
    for line in timeline["lines"]:
        by_scene.setdefault(line["scene_id"], []).append(line)
    for join in plan["joins"]:
        win_start = join["offset_frames"] / fps
        win_end = (join["offset_frames"] + join["duration_frames"]) / fps
        for line in by_scene.get(join["outgoing_scene_id"], []):
            line_start = line["start_s"]
            line_end = line["start_s"] + line["duration_s"]
            if line_start < win_end - slack and line_end > win_start + slack:
                raise GraphError(
                    f"line {line['line_id']!r} ({line_start}-{line_end:.3f}s) falls inside the rendered "
                    f"{join['type']} window after {join['after']!r} ({win_start:.3f}-{win_end:.3f}s)"
                )


def _checked_inputs(inputs, expected_ids, *, what) -> dict:
    """*inputs* must map exactly *expected_ids* to relative paths."""
    if not isinstance(inputs, dict):
        raise GraphError(f"{what} must be a dict of id -> relative path")
    missing = [i for i in expected_ids if i not in inputs]
    extra = sorted(set(inputs) - set(expected_ids))
    if missing or extra:
        raise GraphError(f"{what}: missing inputs for {missing}, unexpected inputs {extra}")
    for key in expected_ids:
        _assert_relative(inputs[key], what=f"{what}[{key!r}]")
    return inputs


def _ending_of(timeline) -> str:
    return "cut_to_black" if timeline["end_card"] is not None else "hard_stop"


# -------------------------------------------------------------- final pass

def final_pass_argv(timeline, *, shot_inputs, end_card_input, ass_rel, fontsdir_rel, mix_rel, profile,
                    out_rel) -> list:
    """The final pass (spec 6.5 "Sequence"; plan: "Final pass"): every
    shot clip (and the end card under ``cut_to_black``) normalised with
    ``settb=AVTB,fps=30,format=<pix_fmt>``; runs of ``cut`` joined by
    ``concat`` (+ ``settb=1/30``, module section above); every other
    transition an ``xfade`` at :func:`sequence_plan`'s frame-exact offset;
    the end card joined by ``fadeblack`` after the last shot; then
    ``ass=<ass_rel>:fontsdir=<fontsdir_rel>`` burns every text layer; the
    PCM mix (:func:`audio_mix_argv`'s output) is muxed unchanged next to
    the video encoded per *profile* into *out_rel* (``episode_pre.mkv``).

    No ``-shortest``: the two lengths already agree to within half a frame
    (module section above), and :func:`sequence_plan` raises rather than
    let a frame-count mismatch through.

    *shot_inputs* maps every timeline ``shot_id`` to its clip;
    *end_card_input* is the end card clip, required under ``cut_to_black``
    and refused under ``hard_stop``. Every path must be relative.
    """
    plan = sequence_plan(timeline)
    _assert_lines_clear_of_joins(timeline, plan)

    shot_ids = [shot["shot_id"] for shot in timeline["shots"]]
    _checked_inputs(shot_inputs, shot_ids, what="shot_inputs")
    if timeline["end_card"] is not None:
        if end_card_input is None:
            raise GraphError("cut_to_black: end_card_input is required")
        _assert_relative(end_card_input, what="end_card_input")
    elif end_card_input is not None:
        raise GraphError("hard_stop: the timeline has no end card, end_card_input must be None")
    for value, what in ((ass_rel, "ass_rel"), (fontsdir_rel, "fontsdir_rel"), (mix_rel, "mix_rel"),
                        (out_rel, "out_rel")):
        _assert_relative(value, what=what)

    fps = plan["fps"]
    video_inputs = [shot_inputs[shot_id] for shot_id in shot_ids]
    item_ids = list(shot_ids)
    if end_card_input is not None:
        video_inputs.append(end_card_input)
        item_ids.append(END_CARD_ID)
    input_index = {item_id: i for i, item_id in enumerate(item_ids)}
    mix_index = len(video_inputs)

    graph = [f"[{i}:v]settb=AVTB,fps={fps},format={profile.pix_fmt}[v{i}]" for i in range(len(video_inputs))]

    segment_labels = []
    for k, segment in enumerate(plan["segments"]):
        labels = [f"[v{input_index[item_id]}]" for item_id in segment["items"]]
        if len(labels) == 1:
            segment_labels.append(labels[0])
        else:
            graph.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0,settb=1/{fps}[seg{k}]")
            segment_labels.append(f"[seg{k}]")

    current = segment_labels[0]
    for k, join in enumerate(plan["joins"]):
        label = f"[x{k + 1}]"
        graph.append(f"{current}{segment_labels[k + 1]}xfade=transition={join['xfade']}:"
                     f"duration={join['duration']}:offset={join['offset']}{label}")
        current = label

    ass_value = motion_mod.escape_expr(ass_rel)
    fontsdir_value = motion_mod.escape_expr(fontsdir_rel)
    graph.append(f"{current}ass={ass_value}:fontsdir={fontsdir_value}[vout]")

    argv = list(_ARGV_PREFIX)
    argv += profile.global_bitexact_args()
    for rel in video_inputs:
        argv += ["-i", rel]
    argv += ["-i", mix_rel]
    argv += ["-filter_complex", ";".join(graph)]
    argv += ["-map", "[vout]", "-map", f"{mix_index}:a"]
    argv += profile.video_encode_args()
    argv += ["-r", str(fps), "-c:a", profiles.MIX_CODEC, out_rel]
    return argv


# --------------------------------------------------------------- audio mix

def _ms(seconds) -> int:
    """A timeline instant as adelay's integer milliseconds (the timeline's
    own seconds carry 3 decimals, so this is exact)."""
    return int(round(float(seconds) * 1000))


# A tier-3 clip's sound fades in and out over this at its shot's edges (phase
# 6 stage 10), so a cut never clicks.
NATIVE_FADE_S = 0.01


def _native_stems(timeline, native_audio) -> list:
    """The tier-3 clip sounds of *native_audio* (phase 6 stage 10, DEC-201)
    -- ``{shot_id: {"input": rel, "lines": [line_id, ...]}}``, each shot's
    clip and the lines its sound is heard in place of -- in timeline order:
    ``[{"shot_id", "input", "lines", "start_sample", "samples"}]``. A stem
    starts at the first sample of its shot's first video frame
    (:func:`sequence_plan`'s ``start_frames``: the frame the final pass puts
    it on) and lasts exactly its shot's ``frames``, at
    ``profiles.AUDIO_RATE``. :class:`GraphError` for a shot or a line the
    timeline does not have, a line claimed by two shots, a path that is not
    relative, or a shot too short for its two fades."""
    if not isinstance(native_audio, dict):
        raise GraphError("native_audio must be a dict of shot_id -> {input, lines}")
    shots = [shot for shot in timeline["shots"] if shot["shot_id"] in native_audio]
    unknown = sorted(set(native_audio) - {shot["shot_id"] for shot in shots})
    if unknown:
        raise GraphError(f"native_audio: {unknown} are not shots of the timeline")
    line_ids = {line["line_id"] for line in timeline["lines"]}
    start_frames = sequence_plan(timeline)["start_frames"]
    rate, fps = profiles.AUDIO_RATE, profiles.FPS
    fade = round(NATIVE_FADE_S * rate)
    stems, owner = [], {}
    for shot in shots:
        shot_id = shot["shot_id"]
        entry = native_audio[shot_id]
        if not isinstance(entry, dict) or not isinstance(entry.get("lines"), (list, tuple)):
            raise GraphError(f"native_audio[{shot_id!r}] must be {{input, lines}}")
        _assert_relative(entry.get("input"), what=f"native_audio[{shot_id!r}]")
        for line_id in entry["lines"]:
            if line_id not in line_ids:
                raise GraphError(f"native_audio[{shot_id!r}]: line {line_id!r} is not in the timeline")
            if line_id in owner:
                raise GraphError(f"line {line_id!r} is heard in place of both {owner[line_id]!r} and {shot_id!r}")
            owner[line_id] = shot_id
        samples = round(shot["frames"] * rate / fps)
        if samples <= 2 * fade:
            raise GraphError(f"shot {shot_id!r} ({shot['frames']} frames) is too short for its clip's sound")
        stems.append({"shot_id": shot_id, "input": entry["input"], "lines": list(entry["lines"]),
                      "start_sample": round(start_frames[shot_id] * rate / fps), "samples": samples})
    return stems


def _native_dialogue(argv, graph, lines, line_inputs, natives, *, sidechain, normalise, silence) -> int:
    """The dialogue section of :func:`audio_mix_argv` at tier 3 (its
    docstring, "Tier 3"), appended to *argv* and *graph*; returns the next
    input index. What is heard -- ``[dlg_mix]``, ``[dlg_stem]`` -- is every
    line no stem replaces plus the stems (*natives*, :func:`_native_stems`);
    the sidechain ``[dlg_sc]`` (with a bed: *sidechain*) is every line, in
    timeline order over the same silent base, so the bed ducks exactly as it
    would with every line heard.

    A stem's clip is read inside the graph (``amovie``, as a shot's paper
    texture is read with ``movie``), never as an ``-i`` input: on ffmpeg
    7.1.5 (the app image) an mp4 among the mix's inputs -- even one left
    unread -- ended the ducked bed's stem early in most runs, a different
    length each time, while the other outputs ran to the end (measured
    during phase 6 stage 10; never on 6.1.1). Read with ``amovie``, 20 runs
    out of 20 were whole and identical."""
    dropped = {line_id for stem in natives for line_id in stem["lines"]}
    heard, side = [], []
    index = 0
    for k, line in enumerate(lines):
        kept = line["line_id"] not in dropped
        if not kept and not sidechain:
            continue
        argv += ["-i", line_inputs[line["line_id"]]]
        chain = f"[{index}:a]{normalise},adelay=delays={_ms(line['start_s'])}:all=1"
        if kept and sidechain:
            graph.append(f"{chain},asplit=2[l{k}][s{k}]")
        else:
            graph.append(f"{chain}[{'l' if kept else 's'}{k}]")
        if kept:
            heard.append(f"[l{k}]")
        if sidechain:
            side.append(f"[s{k}]")
        index += 1
    fade = round(NATIVE_FADE_S * profiles.AUDIO_RATE)
    for j, stem in enumerate(natives):
        samples = stem["samples"]
        graph.append(f"amovie={motion_mod.escape_expr(stem['input'])},{normalise},asetpts=N/SR/TB,"
                     f"atrim=end_sample={samples},afade=t=in:ss=0:ns={fade},"
                     f"afade=t=out:ss={samples - fade}:ns={fade},adelay=delays={stem['start_sample']}S:all=1[n{j}]")
        heard.append(f"[n{j}]")
    graph.append(f"{silence}[dlg_base]")
    graph.append(f"[dlg_base]{''.join(heard)}amix=inputs={len(heard) + 1}:normalize=0:duration=first,"
                 f"asplit=2[dlg_mix][dlg_stem]")
    if sidechain and side:
        graph.append(f"{silence}[sc_base]")
        graph.append(f"[sc_base]{''.join(side)}amix=inputs={len(side) + 1}:normalize=0:duration=first[dlg_sc]")
    elif sidechain:
        graph.append(f"{silence}[dlg_sc]")
    return index


def _wav_output_args() -> list:
    """Per-WAV output options: 48 kHz stereo ``profiles.MIX_CODEC`` (32-bit
    float, so a sum above full scale is not clipped on write), no metadata
    carried over from an input (a BGM mp3's ID3 tags) and no encoder-version
    INFO chunk (``bitexact``), so the bytes depend on the audio alone."""
    return ["-c:a", profiles.MIX_CODEC, "-ar", str(profiles.AUDIO_RATE), "-ac", "2",
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact"]


def audio_mix_argv(timeline, *, line_inputs, sfx_inputs, bgm_input, ending, out_rel, stems_rel,
                   native_audio=None) -> list:
    """The episode's audio mix (spec 6.5 "Audio graph"; plan: "Audio mix";
    DEC-157, DEC-158): one absolute timeline of exactly ``total_s``.

    - Every input is resampled to 48 kHz stereo float
      (``aresample=48000,aformat=...``).
    - Dialogue: each line ``adelay``ed to its timeline start (integer ms,
      ``all=1``), summed (lines never overlap -- asserted) over a silent
      base of exactly ``total_s`` (``anullsrc`` + ``atrim``), ``amix
      normalize=0:duration=first`` -- so the stem is exactly ``total_s``.
    - SFX: each anchor whose cue has an input ``adelay``ed to its anchor,
      summed the same way. An anchor whose cue is absent from *sfx_inputs*
      is skipped: the caller resolved it (``audio_assets.resolve_sfx``) and
      reports it (spec 11: "skipped and reported, never a crash").
    - BGM (optional): ``-stream_loop -1``, re-stamped by sample count,
      ``atrim=duration=total_s``, faded out over the last
      ``BED_FADE_OUT_S[ending]``, then ducked by ``sidechaincompress`` with
      the dialogue as the sidechain (DEC-157's exact values). No BGM: a
      silent bed of the same length.
    - ``amix=inputs=3:weights=<dialogue bgm sfx>:normalize=0:duration=first``
      with the dialogue stem first.

    Outputs: the mix to *out_rel*, and the three stems (dialogue, SFX and
    the ducked -- pre-weight -- BGM) to ``stems_rel["dialogue"|"sfx"|"bgm"]``,
    each exactly ``total_s`` of 48 kHz stereo float WAV (a stem with no
    input is silence, so Tier-2's ducking check always has three files).

    *line_inputs* maps every timeline ``line_id`` to its audio file;
    *sfx_inputs* maps cue names to files; *bgm_input* is a file or
    ``None``; *ending* is ``"cut_to_black"``/``"hard_stop"`` and must agree
    with the timeline's end card. Every path must be relative.

    **Tier 3** (phase 6 stage 10, DEC-201): *native_audio* --
    ``{shot_id: {"input": clip, "lines": [line_id, ...]}}``, None or empty
    at tiers 1 and 2, whose argv is then exactly the one above -- makes each
    shot's clip sound one more stem of the same absolute timeline
    (:func:`_native_stems`): its best audio stream, read inside the graph
    (``amovie``, :func:`_native_dialogue` says why), resampled like every
    input, re-stamped from 0, trimmed to its shot's frames, faded in and out
    over :data:`NATIVE_FADE_S` and ``adelay``ed to the sample of its shot's
    first frame. It is heard in place of that shot's *lines*: the dialogue
    stem (and what the mix takes of it) is the other lines plus these stems.
    The ducking does not move: the sidechain is still every line's TTS
    audio, summed as above (a line heard is split to both; a line not heard
    feeds the sidechain alone, and is not read at all without a bed).
    """
    if ending not in profiles.ENDINGS:
        raise GraphError(f"unknown ending {ending!r}, expected one of {profiles.ENDINGS}")
    if ending != _ending_of(timeline):
        raise GraphError(f"ending {ending!r} disagrees with the timeline, which is {_ending_of(timeline)!r}")

    total_s = float(timeline["total_s"])
    lines = timeline["lines"]
    _checked_inputs(line_inputs, [line["line_id"] for line in lines], what="line_inputs")
    if not isinstance(sfx_inputs, dict):
        raise GraphError("sfx_inputs must be a dict of cue -> relative path")
    anchors = [a for a in timeline["sfx_anchors"] if a["cue"] in sfx_inputs]
    for anchor in anchors:
        _assert_relative(sfx_inputs[anchor["cue"]], what=f"sfx_inputs[{anchor['cue']!r}]")
    if bgm_input is not None:
        _assert_relative(bgm_input, what="bgm_input")
    if not isinstance(stems_rel, dict) or set(stems_rel) != set(STEM_KINDS):
        raise GraphError(f"stems_rel must map exactly {STEM_KINDS} to relative paths")
    for kind in STEM_KINDS:
        _assert_relative(stems_rel[kind], what=f"stems_rel[{kind!r}]")
    _assert_relative(out_rel, what="out_rel")
    natives = _native_stems(timeline, native_audio) if native_audio else []

    ordered = sorted(lines, key=lambda line: line["start_s"])
    for line in ordered:
        if line["start_s"] < 0 or line["start_s"] + line["duration_s"] > total_s + _FRAME_EPS:
            raise GraphError(f"line {line['line_id']!r} ({line['start_s']}s + {line['duration_s']}s) "
                             f"is outside the episode (0-{total_s}s)")
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt["start_s"] < prev["start_s"] + prev["duration_s"] - _FRAME_EPS:
            raise GraphError(f"lines {prev['line_id']!r} and {nxt['line_id']!r} overlap")
    for anchor in anchors:
        if not 0 <= anchor["start_s"] < total_s:
            raise GraphError(f"sfx {anchor['cue']!r} at {anchor['start_s']}s is outside the episode (0-{total_s}s)")

    rate = profiles.AUDIO_RATE
    layout = profiles.AUDIO_CHANNEL_LAYOUT
    normalise = (f"aresample={rate},aformat=sample_fmts={profiles.AUDIO_SAMPLE_FMT}:sample_rates={rate}:"
                 f"channel_layouts={layout}")
    total = _num(total_s)
    silence = f"anullsrc=r={rate}:cl={layout},atrim=duration={total}"

    argv = list(_ARGV_PREFIX)
    graph = []
    index = 0

    # dialogue
    if natives:
        index = _native_dialogue(argv, graph, lines, line_inputs, natives, sidechain=bgm_input is not None,
                                 normalise=normalise, silence=silence)
    else:
        line_labels = []
        for k, line in enumerate(lines):
            argv += ["-i", line_inputs[line["line_id"]]]
            graph.append(f"[{index}:a]{normalise},adelay=delays={_ms(line['start_s'])}:all=1[l{k}]")
            line_labels.append(f"[l{k}]")
            index += 1
        dialogue_outputs = "[dlg_mix][dlg_stem][dlg_sc]" if bgm_input is not None else "[dlg_mix][dlg_stem]"
        n_split = 3 if bgm_input is not None else 2
        if line_labels:
            graph.append(f"{silence}[dlg_base]")
            graph.append(f"[dlg_base]{''.join(line_labels)}amix=inputs={len(line_labels) + 1}:normalize=0:"
                         f"duration=first,asplit={n_split}{dialogue_outputs}")
        else:
            graph.append(f"{silence},asplit={n_split}{dialogue_outputs}")

    # sfx
    sfx_labels = []
    for k, anchor in enumerate(anchors):
        argv += ["-i", sfx_inputs[anchor["cue"]]]
        graph.append(f"[{index}:a]{normalise},adelay=delays={_ms(anchor['start_s'])}:all=1[x{k}]")
        sfx_labels.append(f"[x{k}]")
        index += 1
    if sfx_labels:
        graph.append(f"{silence}[sfx_base]")
        graph.append(f"[sfx_base]{''.join(sfx_labels)}amix=inputs={len(sfx_labels) + 1}:normalize=0:"
                     f"duration=first,asplit=2[sfx_mix][sfx_stem]")
    else:
        graph.append(f"{silence},asplit=2[sfx_mix][sfx_stem]")

    # bgm bed, ducked under the dialogue
    if bgm_input is not None:
        fade = profiles.BED_FADE_OUT_S[ending]
        argv += ["-stream_loop", "-1", "-i", bgm_input]
        graph.append(f"[{index}:a]{normalise},asetpts=N/SR/TB,atrim=duration={total},"
                     f"afade=t=out:st={_num(total_s - fade)}:d={_num(fade)}[bed]")
        graph.append(f"[bed][dlg_sc]sidechaincompress=threshold={_num(profiles.DUCK_THRESHOLD)}:"
                     f"ratio={_num(profiles.DUCK_RATIO)}:attack={_num(profiles.DUCK_ATTACK_MS)}:"
                     f"release={_num(profiles.DUCK_RELEASE_MS)},asplit=2[bgm_mix][bgm_stem]")
        index += 1
    else:
        graph.append(f"{silence},asplit=2[bgm_mix][bgm_stem]")

    weights = dict(profiles.MIX_WEIGHTS)
    order = [kind for kind, _weight in profiles.MIX_WEIGHTS]
    mix_inputs = "".join({"dialogue": "[dlg_mix]", "bgm": "[bgm_mix]", "sfx": "[sfx_mix]"}[kind] for kind in order)
    weight_text = " ".join(_num(weights[kind]) for kind in order)
    graph.append(f"{mix_inputs}amix=inputs=3:weights={weight_text}:normalize=0:duration=first[mix]")

    argv += ["-filter_complex", ";".join(graph)]
    argv += ["-map", "[mix]"] + _wav_output_args() + [out_rel]
    for kind, label in (("dialogue", "[dlg_stem]"), ("sfx", "[sfx_stem]"), ("bgm", "[bgm_stem]")):
        argv += ["-map", label] + _wav_output_args() + [stems_rel[kind]]
    return argv


# ================================================================ stage 9
#
# The cover (spec 2.10, 6.2; plan phase 4 "Metadata step (12)": "the hook
# shot + ass=cover.ass, one frame, saved as jpg"; DEC-159: every burned text
# is libass from ASS built in Python, no PIL in the render path).

# The cover's JPEG quality (mjpeg's qscale: 2 is near the best it does).
COVER_JPEG_QSCALE = 2


def cover_argv(image_rel, ass_rel, fontsdir_rel, out_rel) -> list:
    """The argv that makes ``cover.jpg`` (plan: "the hook shot +
    ``ass=cover.ass``, one frame, saved as jpg"): the hook scene's first
    shot image, scaled to fill ``profiles.WIDTH``x``profiles.HEIGHT`` and
    centre-cropped (never letterboxed, never stretched), square pixels,
    the cover text of *ass_rel* (``subtitles.cover_ass``) burned with the
    staged font of *fontsdir_rel*, then exactly one frame written as a
    single JPEG (``-update 1``: one file, not an image sequence). Every
    path must be relative (the render folder is the working directory)."""
    for value, what in ((image_rel, "image_rel"), (ass_rel, "ass_rel"), (fontsdir_rel, "fontsdir_rel"),
                        (out_rel, "out_rel")):
        _assert_relative(value, what=what)
    w, h = profiles.WIDTH, profiles.HEIGHT
    ass_value = motion_mod.escape_expr(ass_rel)
    fontsdir_value = motion_mod.escape_expr(fontsdir_rel)
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
          f"ass={ass_value}:fontsdir={fontsdir_value}")
    argv = list(_ARGV_PREFIX)
    argv += ["-i", image_rel, "-vf", vf, "-frames:v", "1", "-update", "1", "-q:v", str(COVER_JPEG_QSCALE),
             out_rel]
    return argv
