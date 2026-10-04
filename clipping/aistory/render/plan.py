"""The render plan: every ffmpeg/ffprobe command of one episode render, in
order, decided before anything runs (spec 6.5, 2.9; plan phase 4 stage 7,
"Renderer" -> runner.py/manifest.py/plan.py; phase 5 stage 12; DEC-156..159,
DEC-183).

:func:`build_render_plan` is **pure**: documents and already-hashed input
files in, a JSON-able plan out -- no clock, no subprocess, and no disk but
the header of each still a shot is drawn from (its pixel size,
``imagesize.image_size``: a still that is not 9:16 is centre-cropped, phase 6
stage 13b -- the size is the bytes' own, so the sha256 the cache key holds
still decides it). The caller resolves where each file is and hashes it
(``runner.file_record``), runs the ffmpeg pre-flight (``runner.preflight``)
for the version the cache keys need, and hands the plan to
``runner.run_render``.

**Stages**, in the order they run (``schemas.RENDER_STAGE_KINDS``):

- ``S:<shot_id>`` (``shot``) -- one per timeline shot:
  ``filtergraph.shot_argv`` on the shot's image, or
  ``filtergraph.tier2_clip_argv`` when the storyboard shot carries a video
  (``assets.video``), is not kept still and the caller resolved that video
  (phase 6 stage 9: the render step resolves only a current clip, at tier
  >= 2, and hands each shot's **effective** ``keep_still`` -- its
  ``assets.json`` override over the storyboard's own -- in the inputs; the
  planner reads no file). Each shot's mode (``schemas.RENDER_SHOT_MODES``)
  is the plan's ``shot_modes``, present only when some shot is not plain
  ``motion``. A still shot's zoompan takes the style's own
  ``motion_rules.tier1.pan_pct`` (phase 5 stage 12, DEC-183), defaulting to
  ``motion.PAN_PCT`` when the style_lock carries none (a hand-built fixture
  literal, e.g. ``render/golden.py``) -- both shipped MVP styles' own
  ``pan_pct`` already equals that constant, so their argv never moves.
- ``E`` (``end_card``) -- only under ``cut_to_black``:
  ``filtergraph.end_card_argv`` over ``end_card.ass``; the card carries the
  call to action under "PART N" only when the episode template says
  ``end_card_cta: true`` (read defensively: absent or anything else is off,
  so every template without it renders the card it always did).
- ``A`` (``audio_mix``) -- ``filtergraph.audio_mix_argv`` -> ``mix.wav`` and
  ``stems/``; at tier 3 a shot that keeps its clip's sound (the inputs'
  ``native_audio``, phase 6 stage 10) gives it one more stem there, in place
  of its lines, and is labelled ``video_native_audio``; on an ambience story
  (the inputs' ``ambience``, stage E) a shot cut from a clip with a sound
  track gives one more stem UNDER its lines, ducked, and is labelled
  ``video_ambience``.
- ``L1`` (``loudness_measure``) -- ``loudness.measure_cmd(mix.wav,
  target=profiles.LOUDNORM_TARGET)``; the runner parses its stderr into
  ``loudness_mix.json``.
- ``F`` (``final``) -- ``filtergraph.final_pass_argv`` -> ``episode_pre.mkv``.
- ``L2`` (``loudness_apply``) -- ``loudness.apply_cmd(episode_pre.mkv,
  episode_final.mp4, <L1's measurement>, 48000, target=...)``: video copied,
  AAC 192k with ``profiles.AAC_ENCODER_ARGS``, ``+faststart``. Its argv
  needs L1's numbers, so the plan carries its parameters (``apply``) and
  :func:`loudness_apply_argv` builds it.
- ``P`` and ``P:loudness`` (``probe``) -- ``ffprobe`` JSON of the final
  file (``probe.json``), and a loudness measurement of it
  (``loudness_final.json``).
- ``M`` (``framemd5``) -- ``-map 0:v -f framemd5`` of the final file.

**Paths.** Every argv is relative to the runner's working directory, the
episode's ``render/`` folder. Inputs are staged into ``in/`` under their
content hash (``in/<sha256><ext>``); the bundled paper texture sits at
``filtergraph.PAPER_TEXTURE_REL``; the font is staged into ``fonts/``.

**Cache.** S and E write into ``cache/``. A stage's key is the sha256 of the
canonical JSON of its argv (an argv token naming an input replaced by that
input's sha256, the output replaced by a placeholder), the sha256 of every
file it reads (so a file named inside a filter string -- ``end_card.ass``,
the paper texture, the font -- counts too), the render profile and the
ffmpeg version. The process writes ``cache/<key>.part.mp4``; the runner
renames it to ``cache/<key>.mp4`` only once the process succeeded, so a
killed or crashed render never leaves a half file under a key.

Stdlib + this package (DEC-012).
"""

from __future__ import annotations

import hashlib
import json
import os
import re

from ... import loudness
from .. import schemas
from .. import timing as timing_mod
from . import filtergraph, imagesize, motion, profiles
from . import subtitles as subtitles_mod
from . import timeline as timeline_mod


class PlanError(ValueError):
    """The documents or inputs cannot be turned into a render: a timeline
    that disagrees with its timing, a graph that is not frame-exact, a line
    without audio, a bad parameter. The message names what and where."""


# Names inside render/ (the runner's working directory).
IN_DIR = "in"
FONTS_DIR = "fonts"
CACHE_DIR = "cache"
STEMS_DIR = "stems"
LOGS_DIR = "logs"
WORK_DIRS = (IN_DIR, FONTS_DIR, CACHE_DIR, STEMS_DIR, LOGS_DIR)

SUBTITLES_REL = "subtitles.ass"
END_CARD_ASS_REL = "end_card.ass"
MIX_REL = "mix.wav"
STEMS_REL = {"dialogue": f"{STEMS_DIR}/dialogue.wav", "bgm": f"{STEMS_DIR}/bgm.wav", "sfx": f"{STEMS_DIR}/sfx.wav"}
LOUDNESS_MIX_REL = "loudness_mix.json"
PRE_REL = "episode_pre.mkv"
FINAL_REL = "episode_final.mp4"
PROBE_REL = "probe.json"
LOUDNESS_FINAL_REL = "loudness_final.json"
FRAMEMD5_REL = "episode_final.framemd5"

# The bundled overlay (filtergraph.PAPER_TEXTURE_REL is where it is staged).
PAPER_TEXTURE_ID = "paper_texture"
PAPER_TEXTURE_SOURCE = "assets/overlays/paper_texture.png"

CACHE_KEY_VERSION = 1
_OUT_TOKEN = f"{CACHE_DIR}/__output__.mp4"

# render profile -> (shot/tier-2 profile, end-card profile, final-pass profile)
_STAGE_PROFILES = {
    "final": (profiles.SHOT, profiles.SHOT, profiles.FINAL),
    "golden": (profiles.GOLDEN, profiles.GOLDEN, profiles.GOLDEN),
}

_SHA256_RE = re.compile(schemas.SHA256_PATTERN)
_RELATIVE_RE = re.compile(schemas.REFERENCE_IMAGE_PATH_PATTERN)
_EXT_RE = re.compile(r"^\.[a-z0-9]{1,5}$")


# ------------------------------------------------------------------ helpers

def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def staged_name(sha256: str, source: str) -> str:
    """``in/<sha256><ext>``: an input's copy under ``render/``, named by its
    content (the extension kept, lowercased, so ffmpeg probes it as what it
    is)."""
    ext = os.path.splitext(source)[1].lower()
    if not _EXT_RE.match(ext):
        ext = ""
    return f"{IN_DIR}/{sha256}{ext}"


def cache_key(argv, *, output_token: str, input_shas: dict, profile: str, ffmpeg_version: str) -> str:
    """The cache key of a shot or end-card command (module docstring).
    *input_shas* maps each relative path the command reads to its sha256."""
    canonical = []
    for token in argv:
        if token == output_token:
            canonical.append("<output>")
        elif token in input_shas:
            canonical.append("sha256:" + input_shas[token])
        else:
            canonical.append(token)
    material = {
        "v": CACHE_KEY_VERSION,
        "argv": canonical,
        "inputs": sorted(input_shas.values()),
        "profile": profile,
        "ffmpeg": ffmpeg_version,
    }
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _checked_file(entry, *, what: str) -> dict:
    """An input file as the caller resolved it: ``{path, source, sha256}``."""
    if not isinstance(entry, dict):
        raise PlanError(f"{what}: expected {{path, source, sha256}}, got {entry!r}")
    path, source, sha = entry.get("path"), entry.get("source"), entry.get("sha256")
    if not isinstance(path, str) or not path:
        raise PlanError(f"{what}: no file path")
    if not isinstance(source, str) or not _RELATIVE_RE.match(source) or len(source) > 300:
        raise PlanError(f"{what}: source {source!r} must be a relative path")
    if not isinstance(sha, str) or not _SHA256_RE.match(sha):
        raise PlanError(f"{what}: sha256 {sha!r} is not a sha256")
    return {"path": path, "source": source, "sha256": sha}


def _cached_stage(stage_id, kind, argv0, *, input_shas, render_profile, ffmpeg_version) -> dict:
    key = cache_key(argv0, output_token=_OUT_TOKEN, input_shas=input_shas, profile=render_profile,
                    ffmpeg_version=ffmpeg_version)
    write = f"{CACHE_DIR}/{key}.part.mp4"
    argv = [write if token == _OUT_TOKEN else token for token in argv0]
    return {"id": stage_id, "kind": kind, "argv": argv, "output": f"{CACHE_DIR}/{key}.mp4", "write": write,
            "cache_key": key, "capture": None}


def _stage(stage_id, kind, argv, output, *, capture=None) -> dict:
    return {"id": stage_id, "kind": kind, "argv": argv, "output": output, "write": output,
            "cache_key": None, "capture": capture}


def probe_argv(path: str) -> list:
    """``ffprobe`` of a finished file, as JSON on stdout."""
    return ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]


def framemd5_argv(path: str, out: str) -> list:
    """The decoded video frames' checksums (spec 13's parity record)."""
    return ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", path, "-map", "0:v", "-f", "framemd5", out]


def loudness_apply_argv(stage: dict, measured: dict) -> list:
    """L2's argv, once L1 has *measured* the mix (``loudness.parse_measurement``):
    ``loudness.apply_cmd`` with the AI-Story target, and the AAC encoder's own
    options (``profiles.AAC_ENCODER_ARGS``) right after its bitrate."""
    apply = stage["apply"]
    argv = loudness.apply_cmd(apply["src"], apply["dst"], measured, apply["sample_rate"], target=apply["target"])
    at = argv.index("-b:a") + 2
    return argv[:at] + list(apply["aac_args"]) + argv[at:]


# ------------------------------------------------------------------- public

def build_render_plan(*, script: dict, storyboard: dict, assets: dict, style_lock: dict, template: dict,
                      story: dict, ep: int, inputs: dict, ffmpeg: dict, profile: str = "final",
                      subtitles=None, encoder: str = "libx264", video_encoder=None,
                      fill_failed_with_motion: bool = False) -> dict:
    """The plan of one render (module docstring).

    - *story*: ``{"story_id", "title", "language"}`` (the end card's title).
    - *inputs*: the files the caller resolved and hashed, each ``{path
      (absolute), source (relative, for the manifest), sha256}``:
      ``shots{shot_id: ...}``, optional ``videos{shot_id: ...}``,
      ``lines{line_id: ...}``, ``sfx{cue: ...}`` (the cues ``assets`` marks
      resolved), ``bgm`` (or None), ``overlay`` (the paper texture, needed
      only when the style asks for it), ``font`` (``fonts.resolve_font``'s
      record) and optional ``word_timings{line_id: [...]}`` (the TTS
      sidecars' words). At tier >= 2 (phase 6 stage 9) also
      ``keep_still{shot_id: bool}`` -- each shot's effective flag, read in
      place of the storyboard's own -- and ``filled[shot_id]``: the shots
      whose clip failed, went stale or is still generating, rendered with
      Tier-1 motion (``motion_fill``). Without ``keep_still`` the storyboard's
      flag is read and no shot is labelled kept still (tier 1). At tier 3
      (phase 6 stage 10, DEC-201) also ``native_audio[shot_id]``: shots cut
      from their clip whose clip's sound is heard in place of the storyboard
      shot's ``lines`` (``video_native_audio``; the clip staged again as the
      A stage's ``clip_audio`` input, ``filtergraph.audio_mix_argv``'s
      *native_audio*). Without it, no clip's sound is ever read (tiers 1
      and 2: ``tier2_clip_argv`` keeps ``-an``). On an ambience story
      (phase 7 follow-up, stage E) ``ambience[shot_id]`` instead: shots cut
      from their clip whose sound is heard under their lines
      (``video_ambience``; the clip staged again as ``clip_audio``,
      ``filtergraph.audio_mix_argv``'s *ambience*).
    - *fill_failed_with_motion*: the render step's param, recorded in the
      plan's ``params`` only when it is on.
    - *ffmpeg*: ``runner.preflight``'s ``{"version", "machine"}``.
    - *subtitles*: ``None``/``"style"`` (the style lock's mode) or one of
      ``schemas.SUBTITLE_MODES``. *encoder*: ``"libx264"``, or ``"auto"``
      with *video_encoder* -- the ``{"name", "args"}`` the render step
      detected (``clipping.studio.ffmpeg_utils.detect_video_encoder``,
      opt-in): a hardware encoder of ``profiles.HARDWARE_ENCODERS`` encodes
      the final pass with its own argv, anything else (libx264 itself)
      keeps the profile's libx264. Shots and the end card always stay on
      libx264, so their cache keys never depend on the machine's GPU; a
      golden render never takes ``"auto"`` (parity is libx264's).

    Returns ``{ep, profile, params, ffmpeg, font, timeline, expected,
    length_window_s, inputs, files, stages, warnings, approx_line_ids,
    whole_frames}`` -- ``whole_frames``: whether the storyboard's shots are
    timed in whole frames (``timing.board_whole_frames``; the manifest
    records it, so a re-render can say when its timing converted, phase 5
    stage 8) -- and ``shot_modes{shot_id: mode}`` only when some shot is not
    plain ``motion`` (module docstring). Raises :class:`PlanError` naming
    the problem.
    """
    try:
        return _build(script=script, storyboard=storyboard, assets=assets, style_lock=style_lock,
                      template=template, story=story, ep=ep, inputs=inputs, ffmpeg=ffmpeg, profile=profile,
                      subtitles=subtitles, encoder=encoder, video_encoder=video_encoder,
                      fill=bool(fill_failed_with_motion))
    except PlanError:
        raise
    except (ValueError, KeyError) as exc:
        # TimelineError, GraphError and SubtitleError are ValueErrors; a
        # KeyError is a document missing a field the render reads.
        label = type(exc).__name__
        raise PlanError(f"{label}: {exc}") from exc


def _final_encoder(encoder, video_encoder, profile):
    """The hardware encoder's argv the final pass takes, or None (libx264)."""
    if encoder == "libx264":
        return None
    if video_encoder is None:
        raise PlanError(f"encoder {encoder!r} is not available in this renderer yet without the encoder the "
                        "render step detected (video_encoder); use 'libx264'")
    if profile == "golden":
        raise PlanError("a golden render is libx264 only (its framemd5 parity); encoder 'auto' is refused")
    name = video_encoder.get("name") if isinstance(video_encoder, dict) else None
    args = video_encoder.get("args") if isinstance(video_encoder, dict) else None
    if name not in profiles.HARDWARE_ENCODERS:
        return None
    if not isinstance(args, (list, tuple)) or not args or not all(isinstance(a, str) and a for a in args):
        raise PlanError(f"the detected encoder {name!r} has no usable argv")
    if "-vf" in args or "-filter_complex" in args:
        raise PlanError(f"the detected encoder {name!r} carries its own filter, which the final pass cannot take")
    return list(args)


def _build(*, script, storyboard, assets, style_lock, template, story, ep, inputs, ffmpeg, profile, subtitles,
           encoder, video_encoder, fill) -> dict:
    if profile not in _STAGE_PROFILES:
        raise PlanError(f"unknown render profile {profile!r}, expected one of {list(_STAGE_PROFILES)}")
    if encoder not in schemas.RENDER_ENCODERS:
        raise PlanError(f"unknown encoder {encoder!r}, expected one of {list(schemas.RENDER_ENCODERS)}")
    hardware_args = _final_encoder(encoder, video_encoder, profile)
    if not isinstance(ffmpeg, dict) or not ffmpeg.get("version") or not ffmpeg.get("machine"):
        raise PlanError("the ffmpeg version and machine are required (runner.preflight)")
    typography_doc = style_lock["typography"]
    mode = typography_doc["subtitle_mode"] if subtitles in (None, "style") else subtitles
    if mode not in schemas.SUBTITLE_MODES:
        raise PlanError(f"unknown subtitles mode {subtitles!r}, expected 'style' or one of "
                        f"{list(schemas.SUBTITLE_MODES)}")
    language = story["language"]
    if language not in schemas.LANGUAGES:
        raise PlanError(f"unknown story language {language!r}")
    font = inputs.get("font")
    if not isinstance(font, dict) or not all(font.get(k) for k in ("family", "file", "sha256", "reason")):
        raise PlanError("no font record: every render burns text (fonts.resolve_font)")

    shot_profile, card_profile, final_profile = _STAGE_PROFILES[profile]
    if hardware_args is not None:
        final_profile = profiles.with_encoder(final_profile, hardware_args)
    version = ffmpeg["version"]
    overlays = list(style_lock["motion_rules"]["tier1"].get("overlays") or [])
    # phase 5 stage 12, DEC-183: the style's own pan_pct, plumbed into every
    # shot's zoompan (filtergraph.shot_argv). Every real template carries
    # this field (schemas.py's motion_rules.tier1 is required), so the
    # ``.get`` default only matters for a hand-built style_lock that omits
    # it (render/golden.py's own fixture literal) -- there it falls back to
    # exactly motion.PAN_PCT, the constant every shot already used before
    # this stage, so the golden digest never moves.
    pan_pct = style_lock["motion_rules"]["tier1"].get("pan_pct", motion.PAN_PCT)

    if not timing_mod.covers(storyboard, script):
        raise PlanError("the storyboard does not cover the script: every scene needs its shots, in script order")
    timeline = timeline_mod.build_timeline(script, storyboard, template, language, style_lock=style_lock)
    ending = "cut_to_black" if timeline["end_card"] is not None else "hard_stop"

    records = []   # manifest inputs, in stage order
    warnings = []

    def add_input(role, item_id, entry, staged=None) -> str:
        checked = _checked_file(entry, what=f"{role} {item_id or ''}".strip())
        rel = staged or staged_name(checked["sha256"], checked["source"])
        records.append({"role": role, "id": item_id, "path": checked["path"], "source": checked["source"],
                        "staged": rel, "sha256": checked["sha256"]})
        return rel

    # the bundled overlay
    overlay_sha = None
    if "paper_texture" in overlays:
        overlay = inputs.get("overlay")
        if overlay is None:
            raise PlanError("the style asks for paper_texture but no overlay file was given")
        add_input("overlay", PAPER_TEXTURE_ID, overlay, staged=filtergraph.PAPER_TEXTURE_REL)
        overlay_sha = overlay["sha256"]

    # S: one per shot
    board_shots = {shot["shot_id"]: shot for shot in storyboard["shots"]}
    shot_inputs = inputs.get("shots") or {}
    video_inputs = inputs.get("videos") or {}
    # tier >= 2 (phase 6 stage 9): the step's effective flags, and the shots
    # whose clip is filled with motion; tier 1 hands neither.
    keep_still_of = inputs.get("keep_still")
    filled = set(inputs.get("filled") or ())
    # tier 3 (phase 6 stage 10): the shots whose clip's sound is heard in
    # place of their lines; tiers 1 and 2 hand none.
    native = list(inputs.get("native_audio") or ())
    # tier 3, an ambience story (stage E): the shots whose clip's sound is
    # heard under their lines; every other render hands none.
    ambience = list(inputs.get("ambience") or ())
    stages = []
    shot_outputs = {}
    shot_modes = {}
    for tl_shot in timeline["shots"]:
        shot_id = tl_shot["shot_id"]
        board_shot = board_shots[shot_id]
        video = (board_shot.get("assets") or {}).get("video")
        if keep_still_of is not None and shot_id in keep_still_of:
            still = bool(keep_still_of[shot_id])
        else:
            still = bool(board_shot.get("keep_still"))
        if video and not still and shot_id in video_inputs:
            rel = add_input("shot", shot_id, video_inputs[shot_id])
            # DEC-250: a clip recorded ``cover: stretch`` is slowed to its shot's length.
            clip = (board_shot.get("assets") or {}).get("clip") or {}
            stretched = clip.get("clip_s") if clip.get("cover") == "stretch" else None
            argv0 = filtergraph.tier2_clip_argv(rel, tl_shot, shot_profile, _OUT_TOKEN, clip_s=stretched)
            input_shas = {rel: video_inputs[shot_id]["sha256"]}
            shot_modes[shot_id] = ("video_native_audio" if shot_id in native
                                   else "video_ambience" if shot_id in ambience else "video")
        else:
            if shot_id not in shot_inputs:
                raise PlanError(f"shot {shot_id!r} has no image")
            rel = add_input("shot", shot_id, shot_inputs[shot_id])
            # phase 6 stage 13b: a still that is not 9:16 is centre-cropped
            # (an unreadable size keeps the argv it always had)
            size = imagesize.image_size(shot_inputs[shot_id].get("path"))
            argv0 = filtergraph.shot_argv(rel, tl_shot, shot_profile, overlays, _OUT_TOKEN, pan_pct=pan_pct,
                                          image_size=size)
            input_shas = {rel: shot_inputs[shot_id]["sha256"]}
            if overlay_sha is not None:
                input_shas[filtergraph.PAPER_TEXTURE_REL] = overlay_sha
            if keep_still_of is not None and still:
                shot_modes[shot_id] = "motion_keep_still"
            elif shot_id in filled:
                shot_modes[shot_id] = "motion_fill"
            else:
                shot_modes[shot_id] = "motion"
        stage = _cached_stage(f"S:{shot_id}", "shot", argv0, input_shas=input_shas, render_profile=profile,
                              ffmpeg_version=version)
        stages.append(stage)
        shot_outputs[shot_id] = stage["output"]

    # the text layers (DEC-159): the resolved font's family is the ASS Fontname
    typography = dict(typography_doc)
    typography["font_family"] = font["family"]
    font_rel = f"{FONTS_DIR}/{os.path.basename(font['file'])}"
    files = []

    # E: the end card
    end_card_output = None
    if timeline["end_card"] is not None:
        card_s = timeline["end_card"]["duration_s"]
        card_text = subtitles_mod.end_card_ass(language, ep + 1, story["title"], typography, card_s,
                                               cta=template.get("end_card_cta") is True)
        files.append({"path": END_CARD_ASS_REL, "text": card_text})
        argv0 = filtergraph.end_card_argv(END_CARD_ASS_REL, FONTS_DIR, card_s, card_profile, _OUT_TOKEN)
        stage = _cached_stage("E", "end_card", argv0,
                              input_shas={END_CARD_ASS_REL: _sha256_text(card_text), font_rel: font["sha256"]},
                              render_profile=profile, ffmpeg_version=version)
        stages.append(stage)
        end_card_output = stage["output"]

    # A: the audio mix
    line_files = inputs.get("lines") or {}
    line_inputs = {}
    for line in timeline["lines"]:
        line_id = line["line_id"]
        if line_id not in line_files:
            raise PlanError(f"line {line_id!r} has no audio")
        line_inputs[line_id] = add_input("line", line_id, line_files[line_id])

    # tier 3: each native shot's clip, staged again (the same file, the same
    # name) as the mix's input, heard in place of the storyboard shot's lines
    not_cut = [shot_id for shot_id in native if shot_modes.get(shot_id) != "video_native_audio"]
    if not_cut:
        raise PlanError(f"shot(s) {not_cut} keep their clip's sound but are not cut from a clip")
    native_audio = {}
    for tl_shot in timeline["shots"]:
        shot_id = tl_shot["shot_id"]
        if shot_id in native:
            native_audio[shot_id] = {"input": add_input("clip_audio", shot_id, video_inputs[shot_id]),
                                     "lines": list(board_shots[shot_id].get("lines") or [])}

    # stage E: each ambience shot's clip, staged again the same way, heard
    # under the shot's lines
    not_cut = [shot_id for shot_id in ambience if shot_modes.get(shot_id) != "video_ambience"]
    if not_cut:
        raise PlanError(f"shot(s) {not_cut} keep their clip's sound as ambience but are not cut from a clip")
    ambience_inputs = {}
    for tl_shot in timeline["shots"]:
        shot_id = tl_shot["shot_id"]
        if shot_id in ambience:
            ambience_inputs[shot_id] = add_input("clip_audio", shot_id, video_inputs[shot_id])

    # SFX: the cues the assets step resolved; a missing one is skipped and
    # reported (spec 11), never a failure.
    sfx_files = inputs.get("sfx") or {}
    resolved, missing = set(), set()
    for entry in assets.get("sfx") or []:
        (resolved if entry["state"] == "resolved" else missing).add(entry["cue"])
    sfx_inputs = {}
    for anchor in timeline["sfx_anchors"]:
        cue = anchor["cue"]
        if cue in sfx_inputs:
            continue
        if cue in resolved:
            if cue not in sfx_files:
                raise PlanError(f"SFX cue {cue!r} is resolved in the assets but its file was not given")
            sfx_inputs[cue] = add_input("sfx", cue, sfx_files[cue])
            continue
        reason = "is missing" if cue in missing else "is not in the episode's assets"
        warning = f"SFX cue {cue!r} ({anchor['scene_id']}, at {anchor['at']}) {reason}; skipped."
        if warning not in warnings:
            warnings.append(warning)

    bgm_doc = assets.get("bgm")
    bgm_input = None
    if bgm_doc is not None and bgm_doc.get("file"):
        if inputs.get("bgm") is None:
            raise PlanError(f"the BGM track {bgm_doc['file']!r} was not given")
        bgm_input = add_input("bgm", None, inputs["bgm"])

    mix_args = {"line_inputs": line_inputs, "sfx_inputs": sfx_inputs, "bgm_input": bgm_input, "ending": ending,
                "out_rel": MIX_REL, "stems_rel": dict(STEMS_REL)}
    if native_audio:
        mix_args["native_audio"] = native_audio
    if ambience_inputs:
        mix_args["ambience"] = ambience_inputs
    stages.append(_stage("A", "audio_mix", filtergraph.audio_mix_argv(timeline, **mix_args), MIX_REL))

    # L1: measure the mix
    stages.append(_stage("L1", "loudness_measure", loudness.measure_cmd(MIX_REL, target=profiles.LOUDNORM_TARGET),
                         LOUDNESS_MIX_REL, capture="loudnorm"))

    # F: the final pass, every text layer burned
    word_sources = assets.get("lines") or {}
    word_timings = {
        line_id: words for line_id, words in (inputs.get("word_timings") or {}).items()
        if (word_sources.get(line_id) or {}).get("words_source") in ("provider", "alignment")
    }
    subtitles_text, meta = subtitles_mod.build_subtitles_ass(
        timeline=timeline, script=script, subtitle_mode=mode, language=language,
        hook_style=style_lock["episode_defaults"]["hook_style"], ai_label_enabled=bool(typography_doc.get("ai_label")),
        palette=style_lock["palette"], typography=typography, word_timings=word_timings)
    files.insert(0, {"path": SUBTITLES_REL, "text": subtitles_text})
    stages.append(_stage("F", "final", filtergraph.final_pass_argv(
        timeline, shot_inputs=shot_outputs, end_card_input=end_card_output, ass_rel=SUBTITLES_REL,
        fontsdir_rel=FONTS_DIR, mix_rel=MIX_REL, profile=final_profile, out_rel=PRE_REL), PRE_REL))

    # L2: level it (argv built by the runner from L1's measurement)
    level = _stage("L2", "loudness_apply", None, FINAL_REL)
    level["apply"] = {"src": PRE_REL, "dst": FINAL_REL, "sample_rate": profiles.AUDIO_RATE,
                      "target": profiles.LOUDNORM_TARGET, "aac_args": list(profiles.AAC_ENCODER_ARGS)}
    stages.append(level)

    # P: what came out; M: its frames
    stages.append(_stage("P", "probe", probe_argv(FINAL_REL), PROBE_REL, capture="stdout"))
    stages.append(_stage("P:loudness", "probe", loudness.measure_cmd(FINAL_REL, target=profiles.LOUDNORM_TARGET),
                         LOUDNESS_FINAL_REL, capture="loudnorm"))
    stages.append(_stage("M", "framemd5", framemd5_argv(FINAL_REL, FRAMEMD5_REL), FRAMEMD5_REL))

    window = template["window_s"]
    params = {"subtitles": mode, "encoder": encoder}
    if fill:
        params[schemas.RENDER_FILL_PARAM] = True
    plan = {
        "ep": ep,
        "profile": profile,
        "params": params,
        "ffmpeg": {"version": ffmpeg["version"], "machine": ffmpeg["machine"]},
        "font": {key: font[key] for key in ("family", "file", "sha256", "reason")},
        "timeline": timeline,
        "expected": {"duration_s": timeline["total_s"], "width": profiles.WIDTH, "height": profiles.HEIGHT,
                     "fps": f"{profiles.FPS}/1"},
        "length_window_s": [window[0], template.get("tighten_above_s", window[1])],
        "inputs": records,
        "files": files,
        "stages": stages,
        "warnings": warnings,
        "approx_line_ids": meta["approx_line_ids"],
        "whole_frames": timing_mod.board_whole_frames(storyboard),
    }
    if any(value != "motion" for value in shot_modes.values()):
        plan["shot_modes"] = shot_modes
    return plan
