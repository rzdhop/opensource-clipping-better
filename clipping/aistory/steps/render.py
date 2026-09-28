"""Step ``render``: one episode rendered to ``episode_final.mp4`` (spec 3 step
11, 2.9, 6.5, 13; AI Story phase 4, stage 9; DEC-156..159, DEC-161,
DEC-164).

``ctx.ep`` is the episode. Calls no API: the render is minutes of ffmpeg on
the worker's slot, which is why it is a job (DEC-161, amending DEC-109).

**Preconditions**, each refused with what to do before any process starts:
the script and the storyboard approved and current
(``assets.require_approved``); ``assets.json`` written; every shot's image
and every line's audio (in its speaker's pinned voice) on disk; the assets
**approved** with a fingerprint that is still the current one
(``assets.current_fingerprint``: an image, a voice or a sound changed since
the approval makes it stale); then ffmpeg and ffprobe present with every
filter the renderer uses (``runner.preflight``).

**Params** (:data:`PARAMS`): ``subtitles`` -- ``style`` (default: the style
lock's own mode), ``word_pop``, ``two_line`` or ``none`` (a per-episode
render parameter, DEC-164); ``encoder`` -- ``libx264`` (default) or
``auto``, opt-in: ``clipping.studio.ffmpeg_utils.detect_video_encoder``
(imported lazily, only then) picks a hardware encoder for the final pass
(``profiles.HARDWARE_ENCODERS``); anything else it answers keeps libx264.

**The render** (``render/plan.py``, ``render/runner.py``): the plan is
built from the episode's documents and the files as they are now -- each
shot's image (``assets/shots``), each line's audio and the words of its
sidecar (``wordtiming.line_words``: the provider's or an alignment's; an
even split is left to the subtitles, labelled approximate), the SFX and the
BGM ``assets.json`` names (shipped files, repo-relative), the paper texture,
the font (``fonts.resolve_font``). Every path comes from the store, so its
symlink checks apply: the working folder ``render/`` and its subfolders
(``episode_render_dir``), ``render_manifest.json`` (``episode_doc_path``;
the runner rewrites it before every process), ``episode_final.mp4``
(``episode_file_path``). Shots and the end card come from the render cache
when their inputs did not change, so switching the subtitles re-runs only
the audio mix, the final pass and what follows (DEC-164).

**Afterwards**: ``render/subtitles.ass`` is copied, atomically, to the
episode's ``subtitles.ass``. A length outside the template's window, a
loudness outside -14 +/- 1 LUFS or a true peak above -1 dBTP is a warning in
the feed and the manifest, never a failure. A cancel raises ``Cancelled``
(the manifest says where it stopped; partial files are kept); a failed
stage is a ``StepFailed`` naming the stage and its stderr tail. The step
returns a summary (:func:`run`); ``story.json`` is never written (RC-E2).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import os
import subprocess
import time

from clipping.cancel import Cancelled

from .. import schemas, wordtiming
from .. import store as store_mod
from ..render import audio_assets, fonts, profiles
from ..render import plan as plan_mod
from ..render import runner as runner_mod
from . import assets as assets_step
from . import episode_common, voice_lines
from .llm_call import StepFailed

STEP = "render"
ASSETS_DOC = store_mod.EPISODE_ASSETS_DOC
MANIFEST_DOC = store_mod.EPISODE_RENDER_MANIFEST_DOC
FINAL_FILE = plan_mod.FINAL_REL
SUBTITLES_FILE = plan_mod.SUBTITLES_REL

SUBTITLES_PARAM = "subtitles"
ENCODER_PARAM = "encoder"
PARAMS = (SUBTITLES_PARAM, ENCODER_PARAM)
# "style" is the style lock's own mode; the rest are the closed list.
STYLE_SUBTITLES = "style"
SUBTITLE_CHOICES = (STYLE_SUBTITLES,) + schemas.SUBTITLE_MODES
ENCODER_CHOICES = schemas.RENDER_ENCODERS
DEFAULT_ENCODER = "libx264"
AUTO_ENCODER = "auto"

# How much of a failed stage's stderr the failure sentence quotes (the
# manifest keeps up to schemas.STDERR_TAIL_MAX of it).
STDERR_IN_FAILURE = 600

_SFX_PREFIX = "assets/sfx/"
_BGM_PREFIX = "assets/bgm/"


def _and(items) -> str:
    """``a``, ``a and b``, ``a, b and c`` (``assets._and``, duplicated)."""
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _plural(items, one, many) -> str:
    return one if len(items) == 1 else many


# ------------------------------------------------------------------- params

def read_params(params) -> dict:
    """``{"subtitles", "encoder"}`` from the step's params, defaults filled in
    (``style``, ``libx264``); ``StepFailed`` naming the choices for a value
    that is not one of them. Other keys are not the render's and are left
    alone."""
    params = params or {}
    subtitles = params.get(SUBTITLES_PARAM)
    subtitles = STYLE_SUBTITLES if subtitles is None else subtitles
    if subtitles not in SUBTITLE_CHOICES:
        raise StepFailed(f"The subtitles must be one of {', '.join(SUBTITLE_CHOICES)}, not {subtitles!r}.")
    encoder = params.get(ENCODER_PARAM)
    encoder = DEFAULT_ENCODER if encoder is None else encoder
    if encoder not in ENCODER_CHOICES:
        raise StepFailed(f"The encoder must be one of {', '.join(ENCODER_CHOICES)}, not {encoder!r}.")
    return {"subtitles": subtitles, "encoder": encoder}


# ------------------------------------------------------------ preconditions

def require_renderable(ec) -> tuple:
    """``(script, storyboard, assets_doc)`` when episode *ec.ep* can be
    rendered (module docstring); else ``StepFailed`` saying what to do.
    Nothing runs before this holds."""
    ep = ec.ep
    script, board = assets_step.require_approved(ec)
    doc = episode_common.read_episode(ec, ASSETS_DOC)
    if doc is None:
        raise StepFailed(f"Episode {ep} has no assets yet: make them (the assets step), approve them, then render.")

    no_image = [shot["shot_id"] for shot in board["shots"] if assets_step.shot_image_path(ec, shot) is None]
    if no_image:
        targets = [assets_step.shot_target(ep, shot_id) for shot_id in no_image]
        raise StepFailed(f"Episode {ep} cannot be rendered: {_plural(no_image, 'shot', 'shots')} {_and(no_image)} "
                         f"{_plural(no_image, 'has', 'have')} no image. Make "
                         f"{_plural(no_image, 'it', 'them')} (the assets step, or regenerate {_and(targets)}), "
                         "approve the assets again, then render.")

    lines = [line for scene in script["scenes"] for line in scene["lines"]]
    unvoiced = [line["line_id"] for line in lines if not voice_lines.is_measured(ec, line)]
    if unvoiced:
        raise StepFailed(f"Episode {ep} cannot be rendered: {_plural(unvoiced, 'line', 'lines')} {_and(unvoiced)} "
                         f"{_plural(unvoiced, 'has', 'have')} no audio in the speaker's pinned voice. Speak "
                         f"{_plural(unvoiced, 'it', 'them')} (the assets step, or regenerate "
                         f"{_and(assets_step.line_target(ep, lid) for lid in unvoiced)}), approve the assets "
                         "again, then render.")

    approved = doc.get("approved")
    if not approved:
        raise StepFailed(f"Approve episode {ep}'s assets first: the render is made from the approved images, voices "
                         "and sounds.")
    if assets_step.current_fingerprint(ec, board, script, doc) != approved["fingerprint"]:
        raise StepFailed(f"Episode {ep}'s assets changed since they were approved (an image, a voice or a sound is "
                         "not the one approved): look at them, approve them again, then render.")
    return script, board, doc


# ------------------------------------------------------------------- inputs

def _shipped_file(rel, prefix, base):
    """The real path of a shipped file ``assets.json`` names
    (``assets/sfx/...``, ``assets/bgm/...``), or None when it is not under
    its own folder, is missing, or passes through a symlink."""
    if not isinstance(rel, str) or not rel.startswith(prefix):
        return None
    found = audio_assets._safe_join(base, rel[len(prefix):])
    return None if found is None else os.fspath(found)


def render_inputs(ec, script, board, assets_doc, *, custom_fonts_dir=None) -> dict:
    """The plan's ``inputs`` (``plan.build_render_plan``): every file the
    render reads, resolved through the store and hashed now
    (``runner.file_record``); ``source`` is the path the manifest shows --
    relative to the episode's folder for its own files, to the repository
    for the shipped ones. ``StepFailed`` for a shipped file that is gone or
    changed since the assets step named it."""
    ep = ec.ep
    shots = {shot["shot_id"]: runner_mod.file_record(assets_step.shot_image_path(ec, shot), shot["assets"]["image"])
             for shot in board["shots"]}

    lines, word_timings = {}, {}
    for scene in script["scenes"]:
        for line in scene["lines"]:
            line_id = line["line_id"]
            lines[line_id] = runner_mod.file_record(assets_step.line_audio_path(ec, line), line["timing"]["audio"])
            words, source = wordtiming.line_words(line["text"], line["timing"]["duration_s"],
                                                  assets_step.read_sidecar(ec, line_id))
            if source != wordtiming.EVEN_SPLIT:
                word_timings[line_id] = words

    sfx = {}
    for entry in assets_doc.get("sfx") or []:
        if entry["state"] != "resolved" or entry["cue"] in sfx:
            continue
        path = _shipped_file(entry["file"], _SFX_PREFIX, audio_assets.SFX_DIR)
        if path is None:
            raise StepFailed(f"Episode {ep}'s SFX cue {entry['cue']!r} names {entry['file']}, which is not a shipped "
                             "sound any more: run the assets step again and approve the assets.")
        sfx[entry["cue"]] = runner_mod.file_record(path, entry["file"])

    bgm = None
    bed = assets_doc.get("bgm")
    if bed and bed.get("file"):
        path = _shipped_file(bed["file"], _BGM_PREFIX, audio_assets.BGM_DIR)
        record = runner_mod.file_record(path, bed["file"]) if path is not None else None
        if record is None or record["sha256"] != bed["sha256"]:
            raise StepFailed(f"Episode {ep}'s music bed {bed['file']} is missing or changed since the assets step "
                             "picked it: run the assets step again and approve the assets.")
        bgm = record

    font = fonts.resolve_font(ec.style_lock["typography"]["font_family"], custom_fonts_dir=custom_fonts_dir)
    return {"shots": shots, "lines": lines, "sfx": sfx, "bgm": bgm, "overlay": runner_mod.paper_texture_record(),
            "font": font, "word_timings": word_timings}


def plan_args(ec, script, board, assets_doc, inputs, *, subtitles, encoder, video_encoder=None) -> dict:
    """The keyword arguments of ``plan.build_render_plan`` (but ``ffmpeg``
    and ``profile``) for this episode: its documents, the story's id, title
    and language (the end card's title), the resolved *inputs* and the
    step's params."""
    return {
        "script": script, "storyboard": board, "assets": assets_doc, "style_lock": ec.style_lock,
        "template": ec.template, "ep": ec.ep, "inputs": inputs,
        "story": {"story_id": ec.story_id, "title": ec.story["title"], "language": ec.language},
        "subtitles": subtitles, "encoder": encoder, "video_encoder": video_encoder,
    }


# ------------------------------------------------------------------ encoder

def _default_detector():
    # Imported here, and only for the opt-in "auto": the clip studio's
    # package imports cv2 and numpy at load (RC-A1: imported, never changed).
    from clipping.studio.ffmpeg_utils import detect_video_encoder

    return detect_video_encoder


def detect_encoder(ctx, *, detect=None):
    """``{"name", "args"}`` of the encoder ``encoder="auto"`` found for the
    final pass (``detect_video_encoder``, or the *detect* stand-in of a
    test). A probe that cannot run leaves libx264 in place, said, never a
    failure: libx264 is what ``auto`` falls back to anyway."""
    ctx.on_log("🔎 encoder auto: probing the hardware encoders for the final pass (shots stay on libx264)")
    try:
        detector = detect if detect is not None else _default_detector()
        # The frame's short side: a 1080x1920 frame has the pixels of 1080p.
        found = detector(None, target_h=min(profiles.WIDTH, profiles.HEIGHT))
    except Exception as exc:  # noqa: BLE001 - the probe is optional; libx264 always works
        ctx.on_log(f"⚠️ encoder auto: the hardware probe could not run ({type(exc).__name__}: {exc}); "
                   "the final pass stays on libx264.")
        return {"name": DEFAULT_ENCODER, "args": []}
    name = (found or {}).get("name")
    if name in profiles.HARDWARE_ENCODERS:
        ctx.on_log(f"🚀 encoder auto: {name} encodes the final pass")
    elif name == "h264_vaapi":
        ctx.on_log("ℹ️ encoder auto: h264_vaapi answered, but it needs its own upload filter, which the final "
                   "pass's filter graph cannot take; the final pass stays on libx264.")
    else:
        ctx.on_log(f"ℹ️ encoder auto: no hardware encoder answered ({name or 'nothing'}); the final pass stays on "
                   "libx264.")
    return found or {"name": DEFAULT_ENCODER, "args": []}


# -------------------------------------------------------------------- paths

def render_paths(ec) -> dict:
    """``{render_dir, manifest, final, subtitles}``, each from the store (its
    symlink checks: a link anywhere is refused, never followed), the folders
    made. ``StepFailed`` when one is refused."""
    store, story_id, ep = ec.store, ec.story_id, ec.ep
    try:
        render_dir = store.episode_render_dir(story_id, ep, create=True)
        for sub in plan_mod.WORK_DIRS:
            store.episode_render_dir(story_id, ep, sub, create=True)
        return {
            "render_dir": render_dir,
            "manifest": store.episode_doc_path(story_id, ep, MANIFEST_DOC, create=True),
            "final": store.episode_file_path(story_id, ep, FINAL_FILE, create=True),
            "subtitles": store.episode_file_path(story_id, ep, SUBTITLES_FILE, create=True),
        }
    except KeyError as exc:
        raise StepFailed(f"Episode {ep}'s render cannot be written: {exc.args[0] if exc.args else exc} is not a real "
                         "file or folder; it is never followed: move it away first.") from None


# ------------------------------------------------------------------ results

def _stage_entry(manifest, stage_id):
    return next((stage for stage in (manifest or {}).get("stages") or [] if stage["id"] == stage_id), None)


def failure_message(ep, result) -> str:
    """The sentence of a failed render: the stage, what happened, the end of
    its stderr, and where the rest is."""
    stage_id = result.get("failed_stage")
    entry = _stage_entry(result.get("manifest"), stage_id) if stage_id else None
    where = f"stage {stage_id} ({entry['kind']})" if entry else (f"stage {stage_id}" if stage_id else
                                                                  "before its first stage")
    message = f"Episode {ep}'s render failed at {where}: {result.get('error') or 'unknown error'}."
    tail = " ".join(((entry or {}).get("stderr_tail") or "").split())
    if tail:
        if len(tail) > STDERR_IN_FAILURE:
            tail = "…" + tail[-STDERR_IN_FAILURE:]
        message += f" stderr: {tail}"
    if result.get("manifest") is not None:
        message += (f" Partial files are kept in render/; {MANIFEST_DOC} has every command and the full stderr "
                    "tail. Render again once it is fixed: the shots already made come from the cache.")
    return message


def _publish_subtitles(paths) -> None:
    src = os.path.join(paths["render_dir"], plan_mod.SUBTITLES_REL)
    store_mod._atomic_copy(src, paths["subtitles"])


def summary_of(ec, result, *, profile, fingerprint) -> dict:
    manifest, output = result["manifest"], result["output"]
    return {
        "ep": ec.ep,
        "state": result["state"],
        "profile": profile,
        "params": dict(manifest["params"]),
        "ran": list(result["ran"]),
        "cached": list(result["cached"]),
        "duration_s": output["duration_s"],
        "loudness": dict(output["loudness"]),
        "warnings": list(result["warnings"]),
        "output": {"file": FINAL_FILE, "sha256": output["sha256"], "width": output["width"],
                   "height": output["height"], "fps": output["fps"]},
        "subtitles_file": SUBTITLES_FILE,
        "manifest": MANIFEST_DOC,
        "seconds": manifest["timings"]["total_s"],
        "fingerprint": fingerprint,
    }


# --------------------------------------------------------------------- run

def run(ctx, *, profile="final", run_process=subprocess.run, popen=subprocess.Popen, clock=time.monotonic,
        detect=None, custom_fonts_dir=None) -> dict:
    """The step (module docstring). Returns ``{ep, state "completed",
    profile, params{subtitles, encoder}, ran[stage ids], cached[stage ids],
    duration_s, loudness{i, tp, lra}, warnings[], output{file, sha256,
    width, height, fps}, subtitles_file, manifest, seconds, fingerprint}``.

    Not user parameters (tests and the scratch proof only): *profile* --
    ``"final"`` (always, for a job) or ``"golden"`` (``render/profiles.py``'s
    tiny deterministic profile, to prove a whole episode renders in
    seconds); *run_process*/*popen*/*clock* -- the runner's own seams;
    *detect* -- a stand-in for ``detect_video_encoder``; *custom_fonts_dir*
    -- where the template's own font is looked for (default the repo's
    ``custom_fonts/``).
    """
    if profile not in schemas.RENDER_PROFILES:
        raise StepFailed(f"Unknown render profile {profile!r} (one of {', '.join(schemas.RENDER_PROFILES)}).")
    ec = episode_common.load_episode_context(ctx)
    episode_common.check_episode_preconditions(ctx, ec)
    params = read_params(ctx.params)
    ep = ec.ep
    script, board, assets_doc = require_renderable(ec)
    fingerprint = assets_doc["approved"]["fingerprint"]
    ctx.cancel.check()

    try:
        info = runner_mod.preflight(run=run_process)
    except runner_mod.PreflightError as exc:
        raise StepFailed(f"Episode {ep} cannot be rendered: ffmpeg pre-flight: {exc}. Install ffmpeg with libass "
                         "(it needs zoompan, xfade, sidechaincompress, loudnorm and ass), then render again.") from None
    video_encoder = detect_encoder(ctx, detect=detect) if params["encoder"] == AUTO_ENCODER else None
    inputs = render_inputs(ec, script, board, assets_doc, custom_fonts_dir=custom_fonts_dir)
    try:
        plan = plan_mod.build_render_plan(**plan_args(ec, script, board, assets_doc, inputs,
                                                      subtitles=params["subtitles"], encoder=params["encoder"],
                                                      video_encoder=video_encoder),
                                          ffmpeg=info, profile=profile)
    except plan_mod.PlanError as exc:
        raise StepFailed(f"Episode {ep} cannot be rendered: {exc}") from None
    ctx.cancel.check()

    paths = render_paths(ec)
    shots = sum(1 for stage in plan["stages"] if stage["kind"] == "shot")
    ctx.on_log(f"🎬 Rendering episode {ep}: {shots} shot{'s' if shots != 1 else ''}, "
               f"{plan['timeline']['total_s']:.1f} s, subtitles {plan['params']['subtitles']}, "
               f"font {plan['font']['family']}, ffmpeg {info['version']} ({profile})")
    try:
        result = runner_mod.run_render(plan, render_dir=paths["render_dir"], manifest_path=paths["manifest"],
                                       final_path=paths["final"], cancel=ctx.cancel, popen=popen, clock=clock,
                                       on_log=lambda line: ctx.on_log(f"   {line}"))
    except (runner_mod.RunnerError, ValueError) as exc:
        raise StepFailed(f"Episode {ep}'s render could not start: {exc}") from None

    if result["state"] == "cancelled":
        ctx.on_log(f"⏹ Episode {ep}'s render was cancelled ({result['error']}); {MANIFEST_DOC} says where, and "
                   "the partial files are kept.")
        raise Cancelled(f"The render of episode {ep} was cancelled ({result['error']}).")
    if result["state"] != "completed":
        raise StepFailed(failure_message(ep, result))

    try:
        _publish_subtitles(paths)
    except OSError as exc:
        raise StepFailed(f"Episode {ep} rendered, but {SUBTITLES_FILE} could not be copied beside it ({exc}); "
                         "render again.") from None
    for warning in result["warnings"]:
        ctx.on_log(f"⚠️ {warning}")
    output = result["output"]
    loud = output["loudness"]
    ctx.on_log(f"✅ Episode {ep} rendered: {output['duration_s']:.1f} s, {output['width']}x{output['height']} at "
               f"{output['fps']} fps, {loud['i']:.1f} LUFS (true peak {loud['tp']:.1f} dBTP); "
               f"{len(result['ran'])} stage{'s' if len(result['ran']) != 1 else ''} run, "
               f"{len(result['cached'])} from the cache.")
    return summary_of(ec, result, profile=profile, fingerprint=fingerprint)
