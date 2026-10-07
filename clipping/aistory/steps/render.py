"""Step ``render``: one episode rendered to ``episode_final.mp4`` (spec 3 step
11, 2.9, 6.5, 13; AI Story phase 4, stage 9; DEC-156..159, DEC-161,
DEC-164). Its re-render (``steps/rerender.py``, phase 5 stage 8) is the same
render (:func:`render_episode`) with the last good render's own params.

``ctx.ep`` is the episode. Calls no API: the render is minutes of ffmpeg on
the worker's slot, which is why it is a job (DEC-161, amending DEC-109).

**Preconditions**, each refused with what to do before any process starts:
the script and the storyboard approved and current
(``assets.require_approved``); ``assets.json`` written; every shot's image
on disk and current -- a shot edited since its image was made is refused,
naming it, unless it is locked (``assets.outdated_images``, phase 5 stage
7) -- and every line's audio (in its speaker's pinned voice) on disk; the assets
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
``fill_failed_with_motion`` is retired (plan 33 stage 4, "Clips" below): sent
true it is refused; an old manifest's ``true`` is dropped on a re-render.

**Clips** (phase 6 stage 9; :func:`shot_clips`). At tier 1 the render reads
no clip: its inputs, plan and manifest are the ones it always made. At tier
2 or 3 each shot is cut from what it has now: its clip when the clip is
current (``clips.clip_state`` on the episode's video link: made from the
shot's image and video prompt as they are, its file on disk) and the shot's
**effective** flags (``clips.shot_flags``: an ``assets.json`` override over
the storyboard's own) do not keep it still; else, only for a shot kept still
(DEC-236's one exemption), its image with Tier-1 motion. Plan 33 stage 4,
every story at tier >= 2: every shot is a video clip, never a still with
motion -- a shot not kept still with no clip record, or whose clip record is
not current (failed, stale, a re-animate pending, a file gone, or still
generating), refuses the render before any process, naming each with what to
do (its regenerate target ``shot:<ep>:<shot_id>:video``; only Continue for
one still generating). Nothing fills such a shot with motion any more. The
manifest's ``shot_modes`` (``schemas.RENDER_SHOT_MODES``) records which shot
got what, whenever one is not plain motion.

**Native audio** (phase 6 stage 10, DEC-201): a model's own sound is
discarded at tier 2 -- dialogue comes from our TTS, for voice consistency --
and kept only at tier 3, for a shot cut from its current clip whose
effective flags keep it (``keep_native_audio``) and whose clip has a sound
track (``clips.clip_has_audio``, the file's own boxes): that sound is a stem
of the audio mix at the shot's first frame, heard in place of the shot's
lines (``video_native_audio``); the subtitles and the ducking still follow
every line's TTS timing. A clip with no sound track leaves its shot as at
tier 2, its lines spoken, and the feed says so.

**Ambience** (phase 7 follow-up, stage E; the human's choice of 2026-10-02:
the video model's sound is AMBIENCE + SFX ONLY): on a tier-3 ambience story
(``media_policy.ambience``) every shot cut from its current clip whose clip
has a sound track hears it UNDER its lines -- a stem at the shot's first
frame, ducked by the dialogue, on the SFX bus (``video_ambience``) -- and
every line is heard in its pinned TTS voice (``keep_native_audio`` is not
read). A clip with no sound track adds nothing, and the feed says so.

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

**Partial re-render** (phase 5 stage 8, ``render/partial.py``): a clip is
taken from the cache only when its bytes are the ones a manifest recorded;
a render that completes becomes the episode's last good render
(``render_manifest.last_good.json``, ``store.EPISODE_RENDER_LAST_GOOD_DOC``),
and one measured against a last good render records in its manifest's
``reuse`` what it made again and why -- "3 of 11 shots re-rendered" in the
feed and the summary. :func:`render_changes` is the dry run: what a render
would change since the last good render, stage by stage, starting nothing.

**Afterwards**: ``render/subtitles.ass`` is copied, atomically, to the
episode's ``subtitles.ass``. A length outside the template's window, a
loudness outside -14 +/- 1 LUFS or a true peak above -1 dBTP is a warning in
the feed and the manifest, never a failure -- but a v2 story's episode whose
measured length is outside the window is refused before anything runs
(``gates.require_length``, the last precondition; phase 7 stage 6a,
DEC-231). A cancel raises ``Cancelled`` (the manifest says where it stopped;
partial files are kept); a failed stage is a ``StepFailed`` naming the stage
and its stderr tail. The step returns a summary (:func:`run`); ``story.json``
is never written (RC-E2).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import os
import subprocess
import time

from clipping.cancel import Cancelled

from .. import media_policy, schemas, stock_cutaways, subtitle_style, wordtiming
from .. import store as store_mod
from ..render import audio_assets, fonts, profiles
from ..render import partial
from ..render import plan as plan_mod
from ..render import runner as runner_mod
from . import assets as assets_step
from . import clips, episode_common, gates, sticky_link, voice_lines
from .llm_call import StepFailed

STEP = "render"
ASSETS_DOC = store_mod.EPISODE_ASSETS_DOC
MANIFEST_DOC = store_mod.EPISODE_RENDER_MANIFEST_DOC
LAST_GOOD_DOC = store_mod.EPISODE_RENDER_LAST_GOOD_DOC
FINAL_FILE = plan_mod.FINAL_REL
SUBTITLES_FILE = plan_mod.SUBTITLES_REL

SUBTITLES_PARAM = "subtitles"
ENCODER_PARAM = "encoder"
# Plan 33 stage 4: retired -- every shot is a video clip. No longer a param
# (sent true it is refused, :func:`read_params`); an old manifest may still
# record it (``schemas.RENDER_FILL_PARAM``), dropped by :func:`rerender_params`.
FILL_PARAM = schemas.RENDER_FILL_PARAM
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

def fill_refusal() -> str:
    """The refusal of ``fill_failed_with_motion`` sent true (plan 33 stage 4)."""
    return (f"{FILL_PARAM} is no longer a render option: every shot of an episode is a video clip, never a still "
            "with camera motion. A missing or failed clip stops the render and names its regenerate target "
            "(shot:<ep>:<shot_id>:video): make that clip again, approve the assets again, then render.")


def read_params(params) -> dict:
    """``{"subtitles", "encoder"}`` from the step's params, defaults filled in
    (``style``, ``libx264``); ``StepFailed`` naming the choices for a value
    that is not one of them, and for ``fill_failed_with_motion`` sent true
    (:func:`fill_refusal`, plan 33 stage 4: retired; false, the old
    default, changes nothing). Other keys are not the render's and are
    left alone."""
    params = params or {}
    subtitles = params.get(SUBTITLES_PARAM)
    subtitles = STYLE_SUBTITLES if subtitles is None else subtitles
    if subtitles not in SUBTITLE_CHOICES:
        raise StepFailed(f"The subtitles must be one of {', '.join(SUBTITLE_CHOICES)}, not {subtitles!r}.")
    encoder = params.get(ENCODER_PARAM)
    encoder = DEFAULT_ENCODER if encoder is None else encoder
    if encoder not in ENCODER_CHOICES:
        raise StepFailed(f"The encoder must be one of {', '.join(ENCODER_CHOICES)}, not {encoder!r}.")
    if params.get(FILL_PARAM):
        raise StepFailed(fill_refusal())
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

    # Phase 5 stage 7: a shot whose framing, action or prompt changed since its
    # image was made keeps its old image on disk, and the assets approval's
    # fingerprint (the recorded hashes) does not move -- never rendered.
    outdated = assets_step.outdated_images(ec, board)
    if outdated:
        targets = [assets_step.shot_target(ep, shot_id) for shot_id in outdated]
        raise StepFailed(f"Episode {ep} cannot be rendered: {_plural(outdated, 'shot', 'shots')} {_and(outdated)} "
                         f"{_plural(outdated, 'has an image', 'have images')} out of date (the shot changed since "
                         f"{_plural(outdated, 'it was', 'they were')} made, or a regenerate of "
                         f"{_plural(outdated, 'it', 'them')} has not answered yet). Make "
                         f"{_plural(outdated, 'it', 'them')} again (the assets step, or regenerate {_and(targets)}) "
                         f"or lock {_plural(outdated, 'it', 'them')}, approve the assets again, then render.")

    lines = [line for scene in script["scenes"] for line in scene["lines"]]
    untaken = [line["line_id"] for line in lines
               if voice_lines.spoken_by_clip(ec, line) and not voice_lines.is_measured(ec, line)]
    if untaken:
        # Plan 22: a native-speech story's character line is its clip's own speech (the native take).
        raise StepFailed(f"Episode {ep} cannot be rendered: {_plural(untaken, 'line', 'lines')} {_and(untaken)} "
                         f"{_plural(untaken, 'has', 'have')} no take of {_plural(untaken, 'its', 'their')} clip yet "
                         "(the clip is missing, or speaks no word of it). Run the assets step (it takes every "
                         "speaking clip) or regenerate the shot's clip, approve the assets again, then render.")
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
    gates.require_length(ec, script, board, stage="render")
    return script, board, doc


# -------------------------------------------------------------------- clips

def _clip_problem(ec, shot, state, *, script=None, doc=None, link=None):
    """``(what, still_generating)``: why *shot*'s clip record is not the
    clip the render may cut the shot from (*state*: ``clips.clip_state``'s),
    and whether its request is still open in the generation journal (only
    Continue collects it: ``assets.open_clip_request``) -- the one its
    record holds, or (with *script*) a pending re-animate's
    (``assets.pending_clip_keys`` on *link*, the episode's video link): the
    provider may hold it while the record still has the old clip's key."""
    clip = shot["assets"].get("clip") or {}
    keys = [clip.get("cache_key")]
    if script is not None and clip.get("pending"):
        keys += assets_step.pending_clip_keys(ec, script, shot, doc, link=link)
    if assets_step.open_clip_request(ec, keys) is not None:
        return "is still generating", True
    if clip.get("pending"):
        return "waits for its re-animate", False
    if state == "failed":
        return "failed", False
    if state == "stale":
        return "is out of date (its image, its video prompt or the episode's video link changed)", False
    return "file is missing", False


def clip_refusal(ec, blocked, unmade=(), *, script=None, doc=None, link=None) -> str:
    """The render's refusal of *blocked* (``[(shot, state)]``: shots not kept
    still whose clip record is not current) and *unmade* (``[shot id]``:
    shots not kept still with no clip record, plan 33 stage 4): each shot
    named with what went wrong and its regenerate target
    (``shot:<ep>:<shot_id>:video``) -- or, still generating, only Continue
    (``assets.CONTINUE_ONLY``, DEC-152: a new seed would buy a second clip;
    a pending re-animate the provider holds included, :func:`_clip_problem`).
    Never offers motion in place of a clip: every shot is a video clip."""
    ep = ec.ep
    said, targets, held = [], [], []
    if unmade:
        said.append(f"{_plural(unmade, 'shot', 'shots')} {_and(unmade)} {_plural(unmade, 'has', 'have')} no clip")
    for shot, state in blocked:
        shot_id = shot["shot_id"]
        what, still = _clip_problem(ec, shot, state, script=script, doc=doc, link=link)
        said.append(f"shot {shot_id}'s clip {what}")
        if still:
            held.append(shot_id)
        else:
            targets.append(assets_step.clip_target(ep, shot_id))
    makes = []
    if unmade:
        # A shot its plan never animated: only its own regenerate buys it (an assets run follows the plan).
        new = [assets_step.clip_target(ep, shot_id) for shot_id in unmade]
        makes.append(f"make {_plural(new, 'its clip', 'their clips')} (regenerate {_and(new)})")
    if targets:
        makes.append(f"make {_plural(targets, 'it', 'them')} again (regenerate {_and(targets)}, or run the assets "
                     "step again)")
    fixes = [f"{' and '.join(makes)}, approve the assets again, then render"] if makes else []
    if held:
        fixes.append(f"for {_and(held)}, still generating: {assets_step.CONTINUE_ONLY}")
    shots = list(unmade) + [shot["shot_id"] for shot, _state in blocked]
    return (f"Episode {ep} cannot be rendered: {_and(said)}. Every shot is a video clip, never a still with "
            f"camera motion: to cut {_plural(shots, 'that shot', 'those shots')} from "
            f"{_plural(shots, 'its clip', 'their clips')}, {'; '.join(fixes)}.")


def fully_animated_refusal(ec, blocked, unmade, *, action="rendered", script=None, doc=None, link=None) -> str:
    """The refusal of a fully animated story's episode
    (``media_policy.fully_animated``) while a shot not kept still has no
    current clip: *blocked* ``[(shot, state)]`` whose clip record is not
    current, *unmade* ``[shot id]`` never given a clip. Names each shot with
    what to do; never offers Tier-1 motion (no shot of such a story is ever a
    still with a zoom)."""
    ep = ec.ep
    said, fixes, held, targets = [], [], [], []
    if unmade:
        said.append(f"{_plural(unmade, 'shot', 'shots')} {_and(unmade)} {_plural(unmade, 'has', 'have')} no clip yet")
        fixes.append("run the assets step (it buys every shot's clip, or collects one already bought, after the "
                     "keyframes are approved), or regenerate "
                     f"{_and([assets_step.clip_target(ep, shot_id) for shot_id in unmade])}")
    for shot, state in blocked:
        shot_id = shot["shot_id"]
        what, still = _clip_problem(ec, shot, state, script=script, doc=doc, link=link)
        said.append(f"shot {shot_id}'s clip {what}")
        if still:
            held.append(shot_id)
        else:
            targets.append(assets_step.clip_target(ep, shot_id))
    if targets:
        fixes.append(f"make {_plural(targets, 'it', 'them')} again (regenerate {_and(targets)})")
    if held:
        fixes.append(f"for {_and(held)}, still generating: {assets_step.CONTINUE_ONLY}")
    then = "approve the assets again, then render" if action == "rendered" else "then approve"
    return (f"Episode {ep} cannot be {action}: every shot of this story is a video clip (fully animated) and "
            f"{_and(said)}. To finish it, {'; '.join(fixes)}; {then}. Only a shot you keep still (keep_still) "
            "may stay without a clip.")


def unanimated_shots(ec, script, board, assets_doc) -> tuple:
    """``(blocked, unmade)`` of *board*'s shots not kept still by their
    effective flags (``clips.shot_flags``): *blocked* ``[(shot, state)]``
    whose clip record is not current on the episode's recorded video link
    (``clips.clip_state``), *unmade* the ids of those with no clip record.
    What a fully animated story refuses to approve or render while either is
    not empty. Hashes the shots' images; starts nothing."""
    tier = clips.tier_of(ec)
    link = (sticky_link.recorded(assets_doc, sticky_link.VIDEO) or {}).get("link")
    blocked, unmade = [], []
    for shot in board["shots"]:
        flags = clips.shot_flags(shot, assets_doc)
        if flags["keep_still"]:
            continue
        if not shot["assets"].get("clip"):
            unmade.append(shot["shot_id"])
            continue
        image = assets_step.shot_image_path(ec, shot)
        state = clips.clip_state(ec, shot, script, link=link, tier=tier if tier in (2, 3) else 2, flags=flags,
                                 image_sha=assets_step._sha256_file(image) if image is not None else None)
        if state != "current":
            blocked.append((shot, state))
    return blocked, unmade


def silent_clip_note(shot) -> str:
    """The feed's note for a tier-3 shot that keeps its native audio but
    whose clip has no sound track: rendered as at tier 2, its lines spoken
    (never a line silenced without a word)."""
    link = (shot["assets"].get("clip") or {}).get("link") or "its link"
    return (f"ℹ️ Shot {shot['shot_id']} keeps its native audio, but its clip (from {link}) has no sound track: "
            "it is rendered with its lines spoken by their voices instead.")


def silent_ambience_note(shot) -> str:
    """The feed's note for an ambience story's shot whose clip has no sound
    track (stage E): it adds no ambience; its lines are heard as always."""
    link = (shot["assets"].get("clip") or {}).get("link") or "its link"
    return (f"ℹ️ Shot {shot['shot_id']}'s clip (from {link}) has no sound track: no ambience under it; its lines are "
            "heard as always.")


def shot_clips(ec, script, board, assets_doc):
    """What each shot is cut from (module docstring, "Clips"), or None at
    tier 1 -- the render reads no clip there, but a stock cutaway's
    (:func:`stock_shot_clips`, plan 23 stage B8)::

        {"videos": {shot_id: file record of its current clip},
         "keep_still": {shot_id: effective flag}, "filled": [shot ids],
         "native_audio": [shot ids], "ambience": [shot ids], "notes": [sentences]}

    ``ambience`` (phase 7 follow-up, stage E: a tier-3 ambience story,
    ``media_policy.ambience``): every shot cut from its current clip whose
    clip has a sound track -- heard under its lines; a clip with none adds
    nothing, with a note (:func:`silent_ambience_note`). Such a story never
    reads ``keep_native_audio``: ``native_audio`` stays empty.

    ``filled``: always empty (plan 33 stage 4: every shot is a video clip):
    a shot not kept still with no clip record, or whose clip record is not
    current, refuses the render (``StepFailed``, :func:`clip_refusal`; a
    fully animated story's own sentence, :func:`fully_animated_refusal`).
    ``native_audio`` (tier 3 only, DEC-201): the shots cut from their clip
    whose effective flags keep its native audio and whose clip has a sound
    track (``clips.clip_has_audio``) -- heard in place of their lines; a
    clip with none leaves its shot as at tier 2, with a note
    (:func:`silent_clip_note`) in ``notes``. Reads the storyboard's clip
    records, ``assets.json``'s overrides and links, the shots' images
    (hashed), the clips' boxes and the generation journal; starts
    nothing."""
    tier = clips.tier_of(ec)
    if tier < 2:
        return stock_shot_clips(ec, script, board, assets_doc)
    link = (sticky_link.recorded(assets_doc, sticky_link.VIDEO) or {}).get("link")
    if media_policy.fully_animated(ec.story):
        # Every shot a clip: a shot never animated, or one whose clip is not
        # current, refuses the render (its own sentence; any other tier-2+ story's below).
        blocked, unmade = unanimated_shots(ec, script, board, assets_doc)
        if blocked or unmade:
            raise StepFailed(fully_animated_refusal(ec, blocked, unmade, script=script, doc=assets_doc, link=link))
    ambient = tier == 3 and media_policy.ambience(ec.story)
    speech = tier == 3 and media_policy.native_speech(ec.story)
    videos, keep_still, blocked, unmade, native, ambience, notes = {}, {}, [], [], [], [], []
    parts = {}
    for shot in board["shots"]:
        shot_id = shot["shot_id"]
        flags = clips.shot_flags(shot, assets_doc)
        keep_still[shot_id] = bool(flags["keep_still"])
        if keep_still[shot_id]:
            continue
        if not shot["assets"].get("clip"):
            # Plan 33 stage 4: a shot the plan never animated is no still with motion either.
            unmade.append(shot_id)
            continue
        image = assets_step.shot_image_path(ec, shot)
        state = clips.clip_state(ec, shot, script, link=clips.class_link(ec.story, shot, assets_doc, link), tier=tier,
                                 flags=flags, image_sha=assets_step._sha256_file(image) if image is not None else None)
        if state == "current":
            path = clips.shot_clip_path(ec, shot)
            videos[shot_id] = runner_mod.file_record(path, shot["assets"]["video"])
            if (shot["assets"].get("clip") or {}).get("parts"):
                # Plan 35 (DEC-318): a shot cut into one talking clip per line: every part, in order.
                parts[shot_id] = [runner_mod.file_record(clips.part_clip_path(ec, shot_id, part["line_id"]),
                                                         part["video"])
                                  for part in shot["assets"]["clip"]["parts"]]
            if stock_cutaways.is_stock_clip(shot):
                # Plan 23 stage B8: a stock cutaway is plain video at every tier -- its own sound (if
                # any) is never the shot's, ambience or native.
                continue
            if speech:
                # Plan 22: a speaking clip's sound in place of its line, a silent clip's as ambience
                # under the narrator's voice-over.
                if not clips.clip_has_audio(path):
                    notes.append(silent_ambience_note(shot))
                elif shot.get("speaks"):
                    native.append(shot_id)
                else:
                    ambience.append(shot_id)
            elif ambient:
                if clips.clip_has_audio(path):
                    ambience.append(shot_id)
                else:
                    notes.append(silent_ambience_note(shot))
            elif tier == 3 and flags["keep_native_audio"]:
                if clips.clip_has_audio(path):
                    native.append(shot_id)
                else:
                    notes.append(silent_clip_note(shot))
        else:
            blocked.append((shot, state))
    if blocked or unmade:
        raise StepFailed(clip_refusal(ec, blocked, unmade, script=script, doc=assets_doc, link=link))
    resolved = {"videos": videos, "keep_still": keep_still, "filled": [], "native_audio": native,
                "ambience": ambience, "notes": notes}
    if parts:
        resolved["video_parts"] = parts
    return resolved


def stock_shot_clips(ec, script, board, assets_doc):
    """:func:`shot_clips` at tier 1 (plan 23 stage B8): the render reads no clip there but a
    stock cutaway's -- ``{"videos": {shot_id: file record}, "keep_still": {shot_id:
    flag}, "filled": [], "native_audio": [], "ambience": [], "notes": []}`` for the shots whose
    stock clip is current (``clips.clip_state``) and not kept still; None when there is none
    (every render without stock cutaways: its inputs, plan and manifest are the ones they
    always were). A stock clip that is not current is not an error at tier 1: the shot's
    image is cut as always (and its keyframe, a frame of the clip, is the outdated image the
    render refuses by itself)."""
    videos, keep_still = {}, {}
    for shot in board["shots"]:
        if not stock_cutaways.is_stock_clip(shot):
            continue
        shot_id = shot["shot_id"]
        flags = clips.shot_flags(shot, assets_doc)
        keep_still[shot_id] = bool(flags["keep_still"])
        image = assets_step.shot_image_path(ec, shot)
        state = clips.clip_state(ec, shot, script, link=None, tier=1, flags=flags,
                                 image_sha=assets_step._sha256_file(image) if image is not None else None)
        if state == "current" and not keep_still[shot_id]:
            videos[shot_id] = runner_mod.file_record(clips.shot_clip_path(ec, shot), shot["assets"]["video"])
    if not videos:
        return None
    return {"videos": videos, "keep_still": keep_still, "filled": [], "native_audio": [], "ambience": [], "notes": []}


def require_clips(ec, params=None) -> tuple:
    """*params* read (:func:`read_params`), :func:`require_renderable`, then
    the render's clip refusal (:func:`shot_clips`) -- what the run meets, in
    its order, checked
    before a job exists (phase 6 stage 11: the API's 409, the render
    estimate): ``StepFailed`` with the run's own sentence, else
    ``(script, storyboard, assets_doc)``. Nothing more at tier 1. Reads
    files only."""
    read_params(params)
    script, board, doc = require_renderable(ec)
    shot_clips(ec, script, board, doc)
    return script, board, doc


# ------------------------------------------------------------------- inputs

_RESOLVE = object()


def _shipped_file(rel, prefix, base):
    """The real path of a shipped file ``assets.json`` names
    (``assets/sfx/...``, ``assets/bgm/...``), or None when it is not under
    its own folder, is missing, or passes through a symlink."""
    if not isinstance(rel, str) or not rel.startswith(prefix):
        return None
    found = audio_assets._safe_join(base, rel[len(prefix):])
    return None if found is None else os.fspath(found)


def render_inputs(ec, script, board, assets_doc, *, custom_fonts_dir=None, shot_clips_now=_RESOLVE) -> dict:
    """The plan's ``inputs`` (``plan.build_render_plan``): every file the
    render reads, resolved through the store and hashed now
    (``runner.file_record``); ``source`` is the path the manifest shows --
    relative to the episode's folder for its own files, to the repository
    for the shipped ones. ``StepFailed`` for a shipped file that is gone or
    changed since the assets step named it. At tier >= 2 also ``videos``,
    ``keep_still`` and ``filled`` (:func:`shot_clips`, resolved now unless
    the caller did: *shot_clips_now*; its refusal as it says); at tier 1
    exactly the inputs a render always had."""
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

    # One font for every text layer (DEC-159): the story's own pick when it has
    # a subtitle look (plan 23 stage B5), else the style lock's.
    look = subtitle_style.look_for(ec.style_lock, ec.story)
    family = look.font_family if look is not None else ec.style_lock["typography"]["font_family"]
    font = fonts.resolve_font(family, custom_fonts_dir=custom_fonts_dir)
    inputs = {"shots": shots, "lines": lines, "sfx": sfx, "bgm": bgm, "overlay": runner_mod.paper_texture_record(),
              "font": font, "word_timings": word_timings}
    resolved = shot_clips(ec, script, board, assets_doc) if shot_clips_now is _RESOLVE else shot_clips_now
    if resolved is not None:
        inputs.update(videos=resolved["videos"], keep_still=resolved["keep_still"], filled=resolved["filled"])
        if resolved.get("video_parts"):
            # Plan 35: the talking parts of the shots cut per line (render.plan cuts them back to back).
            inputs["video_parts"] = dict(resolved["video_parts"])
        if resolved.get("native_audio"):
            inputs["native_audio"] = list(resolved["native_audio"])
        if resolved.get("ambience"):
            inputs["ambience"] = list(resolved["ambience"])
    return inputs


def plan_args(ec, script, board, assets_doc, inputs, *, subtitles, encoder, video_encoder=None) -> dict:
    """The keyword arguments of ``plan.build_render_plan`` (but ``ffmpeg``
    and ``profile``) for this episode: its documents, the story's id, title
    and language (the end card's title), the resolved *inputs* and the
    step's params (never ``fill_failed_with_motion``: retired, plan 33
    stage 4) and, for
    a story with a ``subtitle_style``, its resolved look (``look``; absent
    otherwise, so the plan is what it always was), and for a 16:9 or 1:1
    story its frame (``aspect``, plan 23 stage B7; absent at 9:16)."""
    args = {
        "script": script, "storyboard": board, "assets": assets_doc, "style_lock": ec.style_lock,
        "template": ec.template, "ep": ec.ep, "inputs": inputs,
        "story": {"story_id": ec.story_id, "title": ec.story["title"], "language": ec.language},
        "subtitles": subtitles, "encoder": encoder, "video_encoder": video_encoder,
    }
    if ec.story.get("recipe"):
        # Plan 32 stage 4: the end card's line and the hook's text follow the story's recipe.
        args["story"]["recipe"] = ec.story["recipe"]
    look = subtitle_style.look_for(ec.style_lock, ec.story)
    if look is not None:
        args["look"] = look
    frame = media_policy.aspect(ec.story)
    if frame != profiles.PORTRAIT.name:
        # Plan 23 stage B7: a 16:9 or 1:1 story renders in its frame (B6's geometry); a 9:16
        # story's arguments are the ones they always were.
        args["aspect"] = frame
    return args


# ------------------------------------------------------------------ encoder

def _default_detector():
    # Imported here, and only for the opt-in "auto": the clip studio's
    # package imports cv2 and numpy at load (RC-A1: imported, never changed).
    from clipping.studio.ffmpeg_utils import detect_video_encoder

    return detect_video_encoder


def detect_encoder(ctx, *, detect=None, aspect=None):
    """``{"name", "args"}`` of the encoder ``encoder="auto"`` found for the
    final pass (``detect_video_encoder``, or the *detect* stand-in of a
    test). A probe that cannot run leaves libx264 in place, said, never a
    failure: libx264 is what ``auto`` falls back to anyway."""
    ctx.on_log("🔎 encoder auto: probing the hardware encoders for the final pass (shots stay on libx264)")
    try:
        detector = detect if detect is not None else _default_detector()
        # The frame's short side: a 1080x1920 frame has the pixels of 1080p (as do 1920x1080 and
        # 1080x1080: plan 23 stage B7 asks the story's own frame).
        geometry = profiles.GEOMETRIES.get(aspect or profiles.PORTRAIT.name, profiles.PORTRAIT)
        found = detector(None, target_h=min(geometry.width, geometry.height))
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
    """``{render_dir, manifest, last_good, final, subtitles}``, each from the
    store (its symlink checks: a link anywhere is refused, never followed),
    the folders made. ``StepFailed`` when one is refused."""
    store, story_id, ep = ec.store, ec.story_id, ec.ep
    try:
        render_dir = store.episode_render_dir(story_id, ep, create=True)
        for sub in plan_mod.WORK_DIRS:
            store.episode_render_dir(story_id, ep, sub, create=True)
        return {
            "render_dir": render_dir,
            "manifest": store.episode_doc_path(story_id, ep, MANIFEST_DOC, create=True),
            "last_good": store.episode_doc_path(story_id, ep, LAST_GOOD_DOC, create=True),
            "final": store.episode_file_path(story_id, ep, FINAL_FILE, create=True),
            "subtitles": store.episode_file_path(story_id, ep, SUBTITLES_FILE, create=True),
        }
    except KeyError as exc:
        raise StepFailed(f"Episode {ep}'s render cannot be written: {exc.args[0] if exc.args else exc} is not a real "
                         "file or folder; it is never followed: move it away first.") from None


# ------------------------------------------------------------------ results

def _stage_entry(manifest, stage_id):
    return next((stage for stage in (manifest or {}).get("stages") or [] if stage["id"] == stage_id), None)


def failure_message(ep, result, *, what="render") -> str:
    """The sentence of a failed render (*what*: ``render`` or ``re-render``):
    the stage, what happened, the end of its stderr, and where the rest is."""
    stage_id = result.get("failed_stage")
    entry = _stage_entry(result.get("manifest"), stage_id) if stage_id else None
    where = f"stage {stage_id} ({entry['kind']})" if entry else (f"stage {stage_id}" if stage_id else
                                                                  "before its first stage")
    message = f"Episode {ep}'s {what} failed at {where}: {result.get('error') or 'unknown error'}."
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


def reuse_view(manifest):
    """The manifest's ``reuse`` record with its sentence (``summary``: "3 of
    11 shots re-rendered"), or None when the render had no last good render
    to be measured against."""
    record = (manifest or {}).get("reuse")
    return dict(record, summary=partial.summary(record)) if record else None


def summary_of(ec, result, *, profile, fingerprint, step=STEP) -> dict:
    manifest, output = result["manifest"], result["output"]
    return {
        "step": step,
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
        "reuse": reuse_view(manifest),
    }


# -------------------------------------------------------------- currency

# What the manifest keeps of each input (``manifest.new_manifest``).
_MANIFEST_INPUT_KEYS = ("role", "id", "source", "staged", "sha256")


def _plan_now(ec, wanted, script, board, assets_doc, ffmpeg, *, profile, custom_fonts_dir) -> dict:
    """The plan :func:`run` would build now, keyed with *ffmpeg* (no
    pre-flight): the files as they are, hashed. ``PlanError``/``StepFailed``
    as the render meets them."""
    inputs = render_inputs(ec, script, board, assets_doc, custom_fonts_dir=custom_fonts_dir)
    return plan_mod.build_render_plan(**plan_args(ec, script, board, assets_doc, inputs,
                                                  subtitles=wanted["subtitles"], encoder=wanted["encoder"]),
                                      ffmpeg=ffmpeg, profile=profile)


def _same_as(plan, manifest, render_dir) -> bool:
    """Whether *manifest* is the render of *plan*: its params, every input
    (role, id, sha256), every stage and its command (all ``done`` or
    ``cached``), and the generated texts in *render_dir* the ones the plan
    writes."""
    if manifest["params"] != plan["params"]:
        return False
    if manifest["inputs"] != [{key: item[key] for key in _MANIFEST_INPUT_KEYS} for item in plan["inputs"]]:
        return False
    done = manifest["stages"]
    if [(stage["id"], stage["kind"]) for stage in done] != [(stage["id"], stage["kind"]) for stage in plan["stages"]]:
        return False
    for entry, stage in zip(done, plan["stages"]):
        if entry["state"] not in ("done", "cached"):
            return False
        # L2's command is built from L1's measurement when it runs.
        if stage["argv"] is not None and entry["argv"] != stage["argv"]:
            return False
    if render_dir is None:
        return False
    for item in plan["files"]:
        path = os.path.join(render_dir, item["path"])
        try:
            if os.path.islink(path):
                return False
            with open(path, encoding="utf-8", newline="") as handle:
                if handle.read() != item["text"]:
                    return False
        except (OSError, UnicodeDecodeError):
            return False
    return True


def current_render(ec, params=None, *, profile="final", custom_fonts_dir=None) -> bool:
    """Whether the episode's last render is the one :func:`run` would make
    now -- so running it again would only repeat minutes of ffmpeg (the fast
    track keeps it; plan phase 4, "Fast track": Continue repeats nothing).
    Starts no process: the plan is built as :func:`run` builds it, against
    the ffmpeg the manifest recorded, and must match the manifest -- its
    profile and params, every input file (role, id, sha256), every stage and
    its command (all ``done`` or ``cached``) -- and the generated texts in
    ``render/`` (the subtitles, the end card) must be the ones it would
    write; the final file must be the one the manifest records. *params* as
    the step reads them (``subtitles``, ``encoder``; ``encoder: auto`` is
    never current -- it depends on the machine). Anything unreadable or
    refused is simply not current."""
    try:
        wanted = read_params(params)
        script, board, assets_doc = require_renderable(ec)
        manifest = episode_common.read_episode(ec, MANIFEST_DOC)
    except StepFailed:
        return False
    if wanted["encoder"] != DEFAULT_ENCODER or manifest is None or not manifest.get("output"):
        return False
    if manifest["profile"] != profile:
        return False
    try:
        final = ec.store.episode_file_path(ec.story_id, ec.ep, FINAL_FILE)
        render_dir = ec.store.episode_render_dir(ec.story_id, ec.ep)
    except KeyError:
        return False
    if assets_step._sha256_file(final) != manifest["output"]["sha256"]:
        return False
    try:
        plan = _plan_now(ec, wanted, script, board, assets_doc, manifest["ffmpeg"], profile=profile,
                         custom_fonts_dir=custom_fonts_dir)
    except (StepFailed, plan_mod.PlanError, OSError, ValueError, KeyError):
        return False
    return _same_as(plan, manifest, render_dir)


# ------------------------------------------------------- changes (dry run)

def _existing(call, *args):
    """A path the store hands out for reading, or None where there is none
    or it refuses one (a symlink is never followed)."""
    try:
        return call(*args)
    except (KeyError, ValueError):
        return None


def last_render(ec) -> dict:
    """``{"last_good", "current", "baseline", "recorded", "render_dir",
    "cache_dir", "final"}``: what the episode's renders left, read through
    the store without making anything (``partial.load_state`` -- the
    runner's own reading -- over the store's checked paths; a folder or file
    that is missing or refused is None)."""
    store, story_id, ep = ec.store, ec.story_id, ec.ep
    manifest = _existing(store.episode_doc_path, story_id, ep, MANIFEST_DOC)
    last_good = _existing(store.episode_doc_path, story_id, ep, LAST_GOOD_DOC)
    known = partial.load_state(manifest, last_good) if manifest is not None else {
        "last_good": None, "current": None, "baseline": None, "recorded": {}}
    render_dir = _existing(store.episode_render_dir, story_id, ep)
    cache_dir = _existing(store.episode_render_dir, story_id, ep, plan_mod.CACHE_DIR) if render_dir else None
    final = _existing(store.episode_file_path, story_id, ep, FINAL_FILE)
    return dict(known, render_dir=render_dir, cache_dir=cache_dir,
                final=final if final is not None and os.path.isfile(final) else None)


def _input_changes(baseline, plan) -> list:
    """``[{role, id, change: added|changed|removed}]``: the plan's inputs
    against the baseline's, in the plan's order, the removed ones last."""
    old = {(item["role"], item["id"]): item["sha256"] for item in (baseline or {}).get("inputs") or []}
    new = {(item["role"], item["id"]): item["sha256"] for item in plan["inputs"]}
    changes = []
    for (role, item_id), sha in new.items():
        if (role, item_id) not in old:
            changes.append({"role": role, "id": item_id, "change": "added"})
        elif old[(role, item_id)] != sha:
            changes.append({"role": role, "id": item_id, "change": "changed"})
    changes += [{"role": role, "id": item_id, "change": "removed"}
                for role, item_id in old if (role, item_id) not in new]
    return changes


def _stage_changes(baseline, plan, selection) -> list:
    """``[{id, kind, change, runs}]``, one per stage of *plan*: a shot's
    change is the selection's reason (``partial.select``) and it runs when it
    is rebuilt; the end card runs when its clip is not in the cache, and
    changed when its command did (``command``); every other stage runs, and
    changed when its command did. ``new`` for a stage the baseline did not
    have."""
    before = {stage["id"]: stage for stage in (baseline or {}).get("stages") or []}
    changes = []
    for stage in plan["stages"]:
        old = before.get(stage["id"])
        if stage["kind"] == "shot":
            shot_id = partial.shot_id_of(stage["id"])
            change = selection["reasons"].get(shot_id) or selection["changed"].get(shot_id)
            runs = shot_id in selection["reasons"]
        else:
            if old is None:
                change = partial.NEW
            elif stage["kind"] == "end_card":
                change = "command" if old["cache_key"] != stage["cache_key"] else None
            else:
                change = "command" if stage["argv"] is not None and old["argv"] != stage["argv"] else None
            runs = selection["end_card"] == partial.REBUILD if stage["kind"] == "end_card" else True
        changes.append({"id": stage["id"], "kind": stage["kind"], "change": change, "runs": runs})
    return changes


def rerender_params(baseline) -> dict:
    """The params a re-render renders with: the last good render's own, less
    a retired ``fill_failed_with_motion`` an old manifest records (plan 33
    stage 4: the re-render then meets the clip refusal like any render)."""
    return read_params({key: value for key, value in baseline["params"].items() if key != FILL_PARAM})


def render_changes(ec, params=None, *, profile="final", custom_fonts_dir=None, ffmpeg=None) -> dict:
    """What a render of the episode would change now, measured against its
    last good render: "changes since last render", stage by stage (plan 11
    stage 8; the API's dry run is stage 9). Starts no process: the plan is
    built as :func:`run` builds it and handed to ``partial.select`` with the
    clips the manifests recorded -- the runner's own reading -- so the count
    is the one the render then makes.

    *params*: as the step reads them; None -- a re-render -- is the last
    good render's own (:func:`rerender_params`), or the defaults without
    one. The commands are keyed with *ffmpeg* (``{"version", "machine"}``;
    default: the last good render's, else the last manifest's -- a dry run
    assumes the ffmpeg did not change). The dry run keys an ``auto`` encoder
    as libx264 (the shots never depend on it); such a render is never
    current.

    Returns ``{"params", "baseline": {output_sha256, finished_at, params} |
    null, "current": bool, "shots_total", "rebuild": [shot ids], "reuse":
    [shot ids], "reasons": {shot id: reason}, "changed": {shot id: reason},
    "end_card", "timing_converted", "summary": "3 of 11 shots re-rendered" |
    null, "stages": [{id, kind, change, runs}], "inputs": [{role, id,
    change}]}`` -- ``current``: the last good render is the one this would
    make, and ``episode_final.mp4`` is its file. ``StepFailed`` with the
    render's own sentence when the episode cannot be rendered."""
    known = last_render(ec)
    baseline = known["baseline"]
    wanted = rerender_params(baseline) if params is None and baseline is not None else read_params(params)
    script, board, assets_doc = require_renderable(ec)
    keyed = ffmpeg or (baseline or known["current"] or {}).get("ffmpeg") or {"version": "unknown",
                                                                             "machine": "unknown"}
    try:
        plan = _plan_now(ec, dict(wanted, encoder=DEFAULT_ENCODER), script, board, assets_doc, keyed,
                         profile=profile, custom_fonts_dir=custom_fonts_dir)
    except plan_mod.PlanError as exc:
        raise StepFailed(f"Episode {ec.ep} cannot be rendered: {exc}") from None
    selection = partial.select(baseline, plan, known["cache_dir"], recorded=known["recorded"])
    current = bool(
        baseline is not None and wanted["encoder"] == DEFAULT_ENCODER and baseline["profile"] == profile
        and known["final"] is not None and assets_step._sha256_file(known["final"]) == baseline["output"]["sha256"]
        and _same_as(plan, baseline, known["render_dir"]))
    record = partial.reuse_record(baseline, selection) if baseline is not None else None
    return {
        "params": wanted,
        "baseline": ({"output_sha256": baseline["output"]["sha256"],
                      "finished_at": baseline["timings"]["finished_at"],
                      "params": dict(baseline["params"])} if baseline is not None else None),
        "current": current,
        "shots_total": selection["shots_total"],
        "rebuild": selection["rebuild"],
        "reuse": selection["reuse"],
        "reasons": selection["reasons"],
        "changed": selection["changed"],
        "end_card": selection["end_card"],
        "timing_converted": selection["timing_converted"],
        "summary": partial.summary(record) if record is not None else None,
        "stages": _stage_changes(baseline, plan, selection),
        "inputs": _input_changes(baseline, plan),
    }


# --------------------------------------------------------------------- run

def run(ctx, *, profile="final", run_process=subprocess.run, popen=subprocess.Popen, clock=time.monotonic,
        detect=None, custom_fonts_dir=None) -> dict:
    """The step (module docstring). Returns ``{step "render", ep, state
    "completed", profile, params{subtitles, encoder}, ran[stage ids],
    cached[stage ids], duration_s, loudness{i, tp, lra}, warnings[],
    output{file, sha256, width, height, fps}, subtitles_file, manifest,
    seconds, fingerprint, reuse}`` -- ``reuse``: the manifest's record with
    its ``summary`` sentence (:func:`reuse_view`), null for a render with no
    last good render before it.

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
    # Made from an approved script and storyboard, which met the memory gate
    # when they were written: this step never meets it (plan 11 stage 4).
    episode_common.check_episode_preconditions(ctx, ec, require_memory=False)
    params = read_params(ctx.params)
    return render_episode(ctx, ec, params, profile=profile, run_process=run_process, popen=popen, clock=clock,
                          detect=detect, custom_fonts_dir=custom_fonts_dir)


def render_episode(ctx, ec, params, *, step=STEP, profile="final", run_process=subprocess.run,
                   popen=subprocess.Popen, clock=time.monotonic, detect=None, custom_fonts_dir=None) -> dict:
    """The render itself, for :func:`run` and the re-render
    (``steps/rerender.py``, *step* ``rerender``): the render's
    preconditions (:func:`require_renderable`), the pre-flight, the plan, the
    runner, the subtitles published beside the video; the feed says what it
    did -- a re-render, "3 of 11 shots re-rendered". *params* as
    :func:`read_params` returns them. Returns :func:`summary_of`."""
    rerender = step != STEP
    noun = "re-render" if rerender else "render"
    ep = ec.ep
    script, board, assets_doc = require_renderable(ec)
    fingerprint = assets_doc["approved"]["fingerprint"]
    # Tier >= 2: a shot without a current clip refuses before any process.
    resolved = shot_clips(ec, script, board, assets_doc)
    for note in (resolved or {}).get("notes") or ():
        ctx.on_log(note)
    ctx.cancel.check()

    try:
        info = runner_mod.preflight(run=run_process)
    except runner_mod.PreflightError as exc:
        raise StepFailed(f"Episode {ep} cannot be rendered: ffmpeg pre-flight: {exc}. Install ffmpeg with libass "
                         "(it needs zoompan, xfade, sidechaincompress, loudnorm and ass), then render again.") from None
    video_encoder = (detect_encoder(ctx, detect=detect, aspect=media_policy.aspect(ec.story))
                     if params["encoder"] == AUTO_ENCODER else None)
    inputs = render_inputs(ec, script, board, assets_doc, custom_fonts_dir=custom_fonts_dir, shot_clips_now=resolved)
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
    ctx.on_log(f"🎬 {'Re-rendering' if rerender else 'Rendering'} episode {ep}: "
               f"{shots} shot{'s' if shots != 1 else ''}, "
               f"{plan['timeline']['total_s']:.1f} s, subtitles {plan['params']['subtitles']}, "
               f"font {plan['font']['family']}, ffmpeg {info['version']} ({profile})")
    try:
        result = runner_mod.run_render(plan, render_dir=paths["render_dir"], manifest_path=paths["manifest"],
                                       final_path=paths["final"], last_good_path=paths["last_good"],
                                       cancel=ctx.cancel, popen=popen, clock=clock,
                                       on_log=lambda line: ctx.on_log(f"   {line}"))
    except (runner_mod.RunnerError, ValueError) as exc:
        raise StepFailed(f"Episode {ep}'s {noun} could not start: {exc}") from None

    if result["state"] == "cancelled":
        ctx.on_log(f"⏹ Episode {ep}'s {noun} was cancelled ({result['error']}); {MANIFEST_DOC} says where, and "
                   "the partial files are kept.")
        raise Cancelled(f"The {noun} of episode {ep} was cancelled ({result['error']}).")
    if result["state"] != "completed":
        raise StepFailed(failure_message(ep, result, what=noun))

    try:
        _publish_subtitles(paths)
    except OSError as exc:
        raise StepFailed(f"Episode {ep} rendered, but {SUBTITLES_FILE} could not be copied beside it ({exc}); "
                         "render again.") from None
    for warning in result["warnings"]:
        ctx.on_log(f"⚠️ {warning}")
    output = result["output"]
    loud = output["loudness"]
    reuse = reuse_view(result["manifest"])
    what = (f"{output['duration_s']:.1f} s, {output['width']}x{output['height']} at {output['fps']} fps, "
            f"{loud['i']:.1f} LUFS (true peak {loud['tp']:.1f} dBTP)")
    if rerender:
        done = f"{reuse['summary']} · {len(reuse['shots_reused'])} reused; " if reuse else ""
        ctx.on_log(f"✅ Episode {ep} re-rendered: {done}{what}.")
    else:
        ctx.on_log(f"✅ Episode {ep} rendered: {what}; "
                   f"{len(result['ran'])} stage{'s' if len(result['ran']) != 1 else ''} run, "
                   f"{len(result['cached'])} from the cache.")
        if reuse:
            ctx.on_log(f"♻️ {reuse['summary']} since the last good render, {len(reuse['shots_reused'])} reused.")
    return summary_of(ec, result, profile=profile, fingerprint=fingerprint, step=step)
