"""Step ``assets``: one episode's shot images, line voices, word timings, SFX
and BGM (spec 3 step 10, 6.4, 6.5, 8.1, 11; AI Story phase 4, stage 8;
DEC-151..155, DEC-160, DEC-165).

``ctx.ep`` is the episode. Needs what the script step needs
(``episode_common.check_episode_preconditions``) but the memory gate (plan
11 stage 4: the script and storyboard it is made from met it), an
**approved** script and
an **approved, current** storyboard that covers it: every scene planned from
its current revision, and prompts resolved from the entities as they are
now. Otherwise ``StepFailed`` saying what to do, before anything is sent.

One job, under a predictive 30-minute budget (``episode_common.Budget``: a
call starts only if it can still finish -- an image may take
:data:`STORY_IMAGE_CALL_SECONDS`, a line ``voice_lines.
STORY_TTS_CALL_SECONDS``, an alignment :data:`STORY_STT_CALL_SECONDS`); the
cancel token is checked before every call. It fills only what is missing
(DEC-124): a complete re-run makes no call.

1. **The plan first** (:func:`asset_units`, calling nothing but a local
   editor's status probe). In ``references`` mode with shots to make, the
   DEC-117 readiness check of IMAGE_EDIT_CHAIN: not ready -> stop and ask,
   zero calls. No image link at all -> stop, naming every link's reason. A
   paid plan (images on a paid link, paid pinned voices) over any cap --
   the episode's, the day's, the story's -- stops before the first call,
   with the numbers.
2. **Voices first** (``voice_lines.LineMeasurement``, phase 3's
   measurement, lifted): every line without current audio through its
   speaker's pinned one-link chain alone; a failing voice fails its own
   lines, naming the character, and nothing else is tried (DEC-122). The
   script is re-timed and the storyboard's shot durations follow
   (``shots.retime_storyboard``): no revision moves and **no approval is
   cleared** (DEC-135, DEC-155).
3. **Word timings** (spec 6.4, ``wordtiming``): the provider's words; with
   ``params.align_words`` (opt-in, DEC-165) a line without them is
   transcribed by the STT chain and its words aligned; otherwise the even
   split, labelled approximate. The source is stored per line.
4. **Images**, for every shot neither locked nor current
   (:func:`image_state`): ``prompt_only`` -> IMAGE_CHAIN, no reference sent,
   labelled; ``references`` -> IMAGE_EDIT_CHAIN with the shot's
   ``reference_images``. **The seed is fixed before the call**
   (:func:`shot_seed`), so the request has a cache key: every call goes
   through the story's generation cache (``gencache``, under
   ``cache/gen/``), which journals it and books it through
   ``LineGates.booker`` on the ledger the gates read -- the per-episode cap
   applies to every paid image and voice (RC-A3). The image is kept as
   ``assets/shots/shot_NN.<ext>`` and recorded in the shot's ``assets``
   (``seed, provider, model, consistency, route, prompt_hash, est_usd,
   cache_key, generated_at``); a shot is current while its
   :func:`prompt_hash` still matches.

   **One image link per episode** (A-087, phase 6 stage 6,
   ``sticky_link``): the first link that serves one of the episode's
   images is recorded as ``assets.json``'s ``links.image``, and every later
   image is asked of that link alone (a one-link chain; its DEC-089 model
   swap still applies). Without a record the link is derived -- only when
   every kept image (current, or locked) was made on one link; a legacy
   episode whose images mix links keeps walking the chain per shot, as
   before, with one printed note. A link that pushes back is waited on in
   the paced rounds below, never traded for the next link; a link gone for
   the day (no key, its free allowance spent, ``allow_paid`` off or a cap,
   HTTP 401/403, a local server that does not answer) stops the step before
   any call -- or, mid-run, fails the shots left -- with the offer
   (:class:`sticky_link.StickyLinkGone`): switch to the next link, remaking
   the shots the old one served. Only the user switches
   (``workflow.patch_assets``, :func:`switched_assets_doc`); once switched,
   an image made on another link is stale.
5. **SFX/BGM** (``render.audio_assets``, pure): each cue resolved in the
   style's pack at its scene's or line's start (a missing cue is
   ``missing``, reported, never a failure); the BGM mood from the
   duration-weighted dominant emotion; the track picked deterministically.
   Written with every line's word source into ``assets.json``
   (``episode_assets_v1``), keeping its approval: that is derived stale by
   :func:`assets_fingerprint`, never cleared here.
6. **The clips** (phase 6 stage 8, DEC-202), last, at the story's
   ``generation_profile.tier`` >= 2 with the ``animate`` param on (its
   default; a tier-1 story never reaches this and its run is unchanged,
   RC-V1). The plan is :func:`asset_units`' ``video`` part, derived again
   **once** when the phase starts and animated exactly, in its order; a
   plan that moved since the step's own check (prices, spend or caps)
   stops the phase before any clip with both lists (RC-V6). Each clip is
   asked of the plan's one link -- the episode's recorded ``links.video``,
   written once a clip is served (A-087), never another link (RC-V5) --
   through the generation cache, so a paid clip is journaled at submit and
   booked once in seconds (RC-V3), under the same gates as an image (the
   caps, ``allow_paid``, the free-tier limiter). Its keyframe must be
   current; its seed is the keyframe's own (a re-animate's ``pending`` seed
   first, DEC-154). A clip is kept as ``assets/clips/shot_NN.mp4`` and
   recorded in the shot's ``assets.clip`` next to its image, the storyboard
   approval kept; a clip that fails is recorded ``failed`` with the reason
   and the phase goes on with the next shot; the link gone for now fails
   the clips left with the video offer (:class:`sticky_link.StickyLinkGone`,
   kind ``video``); only the user switches it (stage 11: the offer's
   ``switch``, ``workflow.patch_assets``' ``links.video``,
   :func:`switched_video_doc`), and the clips another link made are then
   stale. A poll that runs out leaves the request ``submitted``:
   the next run collects it, never buying it again (DEC-152). The plan
   cannot run at all (no link, ``allow_paid`` off, a cap): the step stops
   before its first call, images included, unless ``animate`` is off.
7. **The episode's ledger view** (``CostLedger.episode_view``) is written to
   ``cost_ledger.json`` in the episode's folder after the step, whatever
   happened.

**A v2 story** (phase 7 stage 6b, A16, DEC-230): once ``assets.json`` is
written, the keyframe judge (J2, ``judge.check_keyframes``) checks every
shot whose keyframe is current and has no current verdict -- free, one
vision call each -- and the verdicts are written to ``assets.json``'s
``keyframe_verdicts``. No clip is bought before the keyframes' approval
(``workflow.approve_keyframes``) is current (RC-Q3, :func:`clip_hold`):
while it is missing or stale, or a keyframe is still to make in this run,
the clips' plan is held -- shown, priced, but out of this run's total, its
caps and its readiness, exactly like ``animate`` off -- and the run makes
the keyframes and stops before the video phase, saying so; a clip
regenerate is refused the same way (:func:`clip_target_refusal`).

**A v2 shot's continuity** (phase 8 stage B): every v2 shot but the first
of its scene carries a continuity slot among its references
(``shots.CONTINUITY_REFERENCE``), filled at the call with the previous
keyframe of its scene (:func:`continuity_source`: the images are made in
storyboard order, so it is made first) and recorded as the shot's
``assets.continuity``. The slot counts in the prompt hash as one constant
token (:func:`request_parts`), so redrawing the previous keyframe never
makes this shot stale -- no cascade of paid redraws; a shot asked while its
previous keyframe is missing is asked as it resolves without the slot.

**A free tier that pushes back is paced, not failed.** After the images,
the lines and shots a free link held back (:func:`rate_limited_by`: HTTP
429 from a free link, HTTP 402 from ``pollinations`` -- its empty pollen
balance, refilled about one image a minute) are asked again in rounds: a
:data:`RATE_LIMIT_PAUSE_S` pause through the cancel-aware sleep, then each
provider's items in order until that provider pushes back again; one pause
serves every provider held back. A provider whose round after a pause makes
no progress is given up; a pause starts only while the step budget still
fits it and the call after it (else the round stops, naming what is left).
Each item keeps its request -- the same seed, the same pinned voice, the
same cache key -- so a retry never buys twice and a kept answer is reused.
Nothing else is asked again: no key, a paid refusal, another status, an
item whose paid link was sent.

A shot or a line that failed does not fail the step: it ends awaiting
approval (the worker's rule), naming each failure and the regenerate target
that finishes it (``shot:<ep>:<shot_id>``, ``line:<ep>:<line_id>``). A
journal or booking that could not be written after the provider accepted a
request stops everything, naming the request id (``gencache.JournalError``).

:func:`regenerate_shot_image` and :func:`regenerate_line_voice` are the
``shot_image`` and ``line`` targets of ``regenerate``
(``episode_regenerate``). ``story.json`` is never written (RC-E2).
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace

from clipping.providers import budget as budget_mod
from clipping.providers import gating, gen_timings, gencache, local_comfyui
from clipping.providers import generation as gen
from clipping.providers.registry import ChainError, Link, describe

from .. import (defaults, hardware, imaging, media_policy, refimages, schemas, timing, video_plan, voices,
               wordtiming)
from .. import ledger as ledger_mod
from .. import names as names_mod
from .. import shots as shots_mod
from .. import store as store_mod
from ..render import audio_assets, imagesize
from . import clips, entities, episode_common, judge, llm_call, sticky_link, voice_lines
from . import script as script_step
from . import storyboard as storyboard_step
from .episode_common import SCRIPT_DOC, STORYBOARD_DOC
from .llm_call import StepFailed
from .pacing import RATE_LIMIT_PAUSE_S, _paid_sent, is_rate_limit, rate_limited_by
from .sticky_link import StickyLinkGone

STEP = "assets"
ASSETS_DOC = store_mod.EPISODE_ASSETS_DOC
LEDGER_VIEW = "cost_ledger.json"

# The step's parameters (a closed list): opt-in forced alignment (DEC-165)
# and -- at tier >= 2 -- whether the clips are made after the images and
# voices (phase 6 stage 8, DEC-202; on unless sent false: off makes the
# keyframes first).
ALIGN_PARAM = "align_words"
ANIMATE_PARAM = "animate"
PARAMS = (ALIGN_PARAM, ANIMATE_PARAM)

# How long one call may take, for the step budget's predictive check: an
# image on a queued provider polls for up to five minutes; a line's
# transcription is a few seconds of audio on a hosted STT link.
STORY_IMAGE_CALL_SECONDS = 300
STORY_STT_CALL_SECONDS = 60
# A clip polls for up to ten minutes on a hosted link (fal, Veo); one that
# runs longer is kept submitted and collected by the next run.
STORY_CLIP_CALL_SECONDS = 600

# What a clip still generating is offered -- never its regenerate target: a
# new seed would buy a second clip while the first is billed (DEC-152).
CONTINUE_ONLY = "press Continue (the assets step resumes it; nothing is bought again)"

# A local ComfyUI card of at most this many GB is asked ``POST /free`` once
# before the first clip when a shot image ran on it in the same run: the
# image model is unloaded so the video model fits (phase 6 plan, stage 8).
FREE_VRAM_GB = 12

# The pause before a free link that pushed back is asked again
# (:data:`pacing.RATE_LIMIT_PAUSE_S`, re-imported above under this name).

# A shot's image is vertical 9:16, the size of the plates it is composed on.
SHOT_SIZE = refimages.PLATE_SIZE
SHOTS_DIR = schemas.SHOT_IMAGE_DIR
PROMPT_ONLY, REFERENCES = refimages.PROMPT_ONLY, refimages.REFERENCES

# What a shot's image is, derived (never stored): no image yet; current (its
# prompt hash still matches -- or it is locked and does); stale (made from
# another prompt, negative, mode, size or reference, or on another link than
# the episode's recorded image link); locked_stale (locked, and would be
# stale); failed (a regenerate asked for another one and its call did not
# answer: ``pending`` is still set).
IMAGE_STATES = ("none", "current", "stale", "locked_stale", "failed")

# "Read the episode's recorded image link from assets.json" (the default of
# :func:`shot_state` and its callers, which pass it when they already have it).
_READ = object()

_FILE_MODE = 0o644
_SEED_MODULUS = 2**31 - 2
_NEUTRAL_WORDS = {"characters": "the character", "places": "the place", "props": "the object"}
_ENTITY_KINDS = ("characters", "places", "props")


def _and(items) -> str:
    """``a``, ``a and b``, ``a, b and c`` (``script._and``, duplicated)."""
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# What asking an item again came to (``_Assets.pace``), and how long its call
# may take for the step budget's predictive check.
_DONE, _LIMITED, _FAILED = "done", "limited", "failed"
_CALL_SECONDS = {"line": voice_lines.STORY_TTS_CALL_SECONDS, "shot": STORY_IMAGE_CALL_SECONDS}


def _counted(items) -> str:
    """``3 lines and 1 shot`` for ``[(kind, id)]``."""
    parts = []
    for kind in ("line", "shot"):
        count = sum(1 for item_kind, _id in items if item_kind == kind)
        if count:
            parts.append(f"{count} {kind}{'s' if count != 1 else ''}")
    return _and(parts)


def _left(items) -> str:
    """``the voices of lines l37 and l40; the image of shot sh05`` for ``[(kind, id)]``."""
    lines = [item_id for kind, item_id in items if kind == "line"]
    shots = [item_id for kind, item_id in items if kind == "shot"]
    parts = []
    if lines:
        many = len(lines) > 1
        parts.append(f"the voice{'s' if many else ''} of line{'s' if many else ''} {_and(lines)}")
    if shots:
        many = len(shots) > 1
        parts.append(f"the image{'s' if many else ''} of shot{'s' if many else ''} {_and(shots)}")
    return "; ".join(parts)


class ShotFailed(Exception):
    """One shot's image was not made; ``reason`` says why and, where there is
    one, what to do. The step goes on with the next shot. ``failures`` are
    the chain's ``(label, reason)`` pairs when no link answered
    (``NoRunnableLink.failures``, what :func:`rate_limited_by` reads), else
    empty."""

    def __init__(self, reason, failures=()):
        super().__init__(reason)
        self.reason = reason
        self.failures = tuple(failures)


class ClipFailed(Exception):
    """One shot's clip was not made (phase 6 stage 8); ``reason`` says why
    and what to do. The phase goes on with the next planned shot. ``record``
    holds what the request was (link, route, length, estimate, prompt hash,
    keyframe sha256, cache key) for the shot's failed ``assets.clip``;
    ``still`` is true when the provider still holds the request (a poll ran
    out: the next run collects it)."""

    def __init__(self, reason, *, record=None, still=False):
        super().__init__(reason)
        self.reason = reason
        self.record = dict(record or {})
        self.still = still


# ------------------------------------------------------------------ targets

def shot_target(ep, shot_id) -> str:
    return f"shot:{ep}:{shot_id}"


def line_target(ep, line_id) -> str:
    return f"line:{ep}:{line_id}"


def clip_target(ep, shot_id) -> str:
    return f"shot:{ep}:{shot_id}:video"


def animate_param(params) -> bool:
    """The step's ``animate`` param: on unless sent false."""
    value = (params or {}).get(ANIMATE_PARAM)
    return True if value is None else bool(value)


def image_name(shot_id, ext) -> str:
    """``shot_03.png`` for shot ``sh03``: the shot's own number
    (``schemas.SHOT_IMAGE_NAME_PATTERN``)."""
    return f"shot_{shot_id[2:]}.{ext}"


# -------------------------------------------------------------- pure helpers

def _canonical_sha256(payload) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _name_map(entity_docs) -> dict:
    """``{name: neutral word}`` for every character, place and prop; a
    character's word wins on a shared name (``shots._story_name_map``'s rule,
    duplicated: a private helper of another module)."""
    names = {}
    for kind in _ENTITY_KINDS:
        for doc in (entity_docs.get(kind) or {}).values():
            names.setdefault(doc["name"], _NEUTRAL_WORDS[kind])
    return names


def with_note(prompt, note, entity_docs) -> str:
    """*prompt*, then ``Author's note: <note>.`` at its tail -- after the
    locked blocks, never in place of them -- with every entity name in the
    note replaced by a neutral word (no name enters an image prompt, spec
    2.3; ``refimages._with_note``'s rule)."""
    if not note:
        return prompt
    text = " ".join(str(note).split())
    if not text:
        return prompt
    text = names_mod.without_names(text, _name_map(entity_docs))
    if text[-1] not in ".!?":
        text += "."
    return f"{prompt} Author's note: {text}"


def effective_prompt(shot, entity_docs, note=None) -> str:
    """What the image is asked for: the shot's ``prompt_override`` when the
    user wrote one (any entity name in it replaced, as in a note: the
    resolved ``image_prompt`` never carries one), else its ``image_prompt``;
    *note* at the tail."""
    override = shot.get("prompt_override")
    base = names_mod.without_names(override, _name_map(entity_docs)) if override else shot["image_prompt"]
    return with_note(base, note, entity_docs)


def prompt_hash(prompt, negative, consistency, size, ref_shas) -> str:
    """sha256 of what a shot's image is made from: the effective prompt, the
    negative prompt, the consistency mode, the size and the sha256 of each
    reference image actually sent, in order (plan phase 4, "Documents")."""
    return _canonical_sha256({"prompt": prompt, "negative": negative or "", "consistency": consistency,
                              "size": [int(size[0]), int(size[1])], "refs": list(ref_shas)})


def derive_seed(story_id, ep, shot_id) -> int:
    """A shot's own seed in ``references`` mode: the same on every run and in
    every process (not ``hash()``), 1 .. 2**31-2."""
    digest = hashlib.sha256(f"shot:{story_id}:{ep}:{shot_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % _SEED_MODULUS + 1


def _seed_value(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def base_seed(shot, scene, *, story_id, ep, mode, entity_docs) -> int:
    """The seed a shot's image is asked with when no regenerate left one: in
    ``prompt_only`` mode the first character's portrait seed (its recorded
    ``refs.portrait.seed``, else its ``ref_seed``), else the scene's place
    master plate's -- the seed its reference images were drawn with
    (DEC-117); otherwise, and in ``references`` mode, :func:`derive_seed`."""
    if mode == PROMPT_ONLY:
        first = next((tag[1:] for tag in shot["subject_tags"] if tag.startswith("@")), None)
        character = (entity_docs.get("characters") or {}).get(first) if first else None
        if character is not None:
            portrait = (character.get("refs") or {}).get("portrait") or {}
            seed = _seed_value(portrait.get("seed"))
            if seed is None:
                seed = _seed_value(character.get("ref_seed"))
            if seed is not None:
                return seed
        place = (entity_docs.get("places") or {}).get(scene["place_id"]) if scene else None
        if place is not None:
            plate = (place.get("time_variants") or {}).get(schemas.MASTER_PLATE_VARIANT) or {}
            seed = _seed_value(plate.get("seed"))
            if seed is not None:
                return seed
    return derive_seed(story_id, ep, shot["shot_id"])


def shot_seed(shot, scene, *, story_id, ep, mode, entity_docs) -> int:
    """The seed of the next request for *shot*: a regenerate's ``pending``
    seed when one is left (so a retry asks for the same image, DEC-154),
    else :func:`base_seed`."""
    pending = shot["assets"].get("pending")
    if pending:
        return pending["seed"]
    return base_seed(shot, scene, story_id=story_id, ep=ep, mode=mode, entity_docs=entity_docs)


def served_link(assets):
    """The link a shot's image was made on (``provider/model``), or None."""
    return sticky_link.label(assets.get("provider"), assets.get("model"))


def image_state(assets, *, expected_hash, file_ok, link=None) -> str:
    """One of :data:`IMAGE_STATES` for a shot's ``assets``: *expected_hash*
    is the :func:`prompt_hash` it would be made with now, *file_ok* whether
    its recorded image is on disk, *link* the episode's recorded image link
    (``links.image``; None: none) -- an image made on another is not fresh."""
    image = assets.get("image")
    fresh = bool(image) and file_ok and assets.get("prompt_hash") == expected_hash
    if fresh and link is not None and not sticky_link.on_link(served_link(assets), link):
        fresh = False
    if assets.get("locked"):
        if not image:
            return "none"
        return "current" if fresh else "locked_stale"
    if assets.get("pending"):
        return "failed"
    if not image or not file_ok:
        return "none"
    return "current" if fresh else "stale"


def route_of(link) -> str:
    """``free`` | ``local`` | ``paid``: how the link that answered is paid for."""
    if link.provider == "local":
        return "local"
    return "paid" if gen.is_paid(link) else "free"


def assets_fingerprint(storyboard, script, assets_doc, *, image_shas, audio_shas, clip_shas=None, tier=1) -> str:
    """sha256 over what an assets approval approves (plan phase 4,
    "Documents"): every shot's ``(prompt_hash, image sha256, locked)``, every
    line's ``(text hash, voice, audio sha256)``, and the SFX/BGM files
    ``assets.json`` names -- and, only when ``assets.json`` records them
    (phase 6 stage 6), the episode's links (``links.<kind>.link``), so a
    switch makes the approval stale; a document without ``links`` keeps the
    fingerprint it always had. *image_shas* is ``{shot_id: sha256 | None}``,
    *audio_shas* ``{line_id: sha256 | None}`` (the files as they are now,
    :func:`current_fingerprint`). Durations, revisions, approvals and
    timestamps are not in it: re-timing never makes an approval stale.

    Phase 6 stage 7: a ``clips`` part -- the story's *tier*, and every
    shot's effective flags (``keep_still``, ``animate``,
    ``keep_native_audio``: :func:`clips.shot_flags`) with its clip record's
    ``(state, link, prompt_hash, image_sha256)`` and its clip file's sha256
    (*clip_shas*, ``{shot_id: sha256 | None}``) -- only when the story is at
    tier >= 2, a shot has a clip record, or an override moves a shot's
    flags. A tier-1 episode with none of these fingerprints byte for byte
    as before (RC-V1); an override equal to the storyboard's own value
    changes nothing."""
    assets_doc = assets_doc or {}
    bgm = assets_doc.get("bgm")
    payload = {
        "v": 1,
        "shots": [[shot["shot_id"], shot["assets"].get("prompt_hash"), image_shas.get(shot["shot_id"]),
                   bool(shot["assets"].get("locked"))] for shot in storyboard["shots"]],
        "lines": [[line["line_id"], timing.text_hash(line["text"]), line["timing"].get("voice"),
                   audio_shas.get(line["line_id"])]
                  for scene in script["scenes"] for line in scene["lines"]],
        "sfx": [[cue["scene_id"], cue["at"], cue["cue"], cue["pack"], cue["file"], cue["state"]]
                for cue in assets_doc.get("sfx") or []],
        "bgm": None if not bgm else [bgm["mood"], bgm["file"], bgm["sha256"]],
    }
    links = assets_doc.get("links")
    if links:
        payload["links"] = {kind: entry.get("link") for kind, entry in links.items()}
    clips_part = _clips_part(storyboard, assets_doc, clip_shas or {}, tier)
    if clips_part is not None:
        payload["clips"] = clips_part
    return _canonical_sha256(payload)


def _clips_part(storyboard, assets_doc, clip_shas, tier):
    """:func:`assets_fingerprint`'s ``clips`` part, or None when it has none."""
    rows, recorded = [], False
    for shot in storyboard["shots"]:
        flags = clips.shot_flags(shot, assets_doc)
        clip = shot["assets"].get("clip") or {}
        recorded = recorded or bool(clip)
        rows.append([shot["shot_id"], flags["keep_still"], flags["animate"], flags["keep_native_audio"],
                     clip.get("state"), clip.get("link"), clip.get("prompt_hash"), clip.get("image_sha256"),
                     clip_shas.get(shot["shot_id"])])
    if int(tier) < 2 and not recorded and not clips.flags_moved(storyboard, assets_doc):
        return None
    return {"tier": int(tier), "shots": rows}


# ------------------------------------------------------------ files on disk

def _sha256_file(path):
    """The sha256 of a regular file (never through a symlink), or None."""
    if not path or os.path.islink(path) or not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def v2_keyframe_source(story, produced, *, out_dir, run=None) -> str:
    """The file a shot's image is stored from (phase 7, A6): *produced* as it
    came for a legacy story; for a v2 story, centre-cropped to an exact, even
    9:16 (``media_policy.keyframe_crop`` on ``imagesize.image_size``) by one
    single-frame ffmpeg pass into *out_dir*, in the same format, when it is
    not one already -- so the near-9:16 concat edge of DEC-217 never arises.
    An unreadable size keeps the file (the render frames it as before).
    ``ShotFailed`` when ffmpeg is missing or fails: never a silent keep.
    *run* is ``subprocess.run`` (None) or a test's stand-in."""
    if not media_policy.is_v2(story):
        return produced
    crop = media_policy.keyframe_crop(imagesize.image_size(produced))
    if crop is None:
        return produced
    run = run or subprocess.run
    ext = os.path.splitext(produced)[1] or ".png"
    out = os.path.join(out_dir, f"keyframe-9x16{ext}")
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", produced,
            "-vf", f"crop={crop[0]}:{crop[1]}", "-frames:v", "1", out]
    try:
        result = run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    except OSError as exc:
        raise ShotFailed(f"ffmpeg is needed to crop the image to 9:16 and cannot run ({exc}); install ffmpeg "
                         "and run the step again (the call is booked and cached)") from None
    if result.returncode != 0 or not os.path.isfile(out):
        detail = (getattr(result, "stderr", "") or "").strip()[-200:]
        raise ShotFailed(f"ffmpeg could not crop the image to {crop[0]}x{crop[1]} (exit {result.returncode}"
                         f"{': ' + detail if detail else ''}); the call is booked and cached") from None
    return out


def _atomic_copy(src, dest) -> None:
    """Copy *src* to *dest* so a reader sees the old file or the new one
    (``voices._atomic_copy``'s pattern, duplicated: a private helper of
    another module)."""
    handle, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), prefix=".asset-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out, open(src, "rb") as source:
            shutil.copyfileobj(source, out)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, _FILE_MODE)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_json(path, data) -> None:
    handle, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".asset-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, _FILE_MODE)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def shot_image_path(ec, shot):
    """The real path of the image a shot's ``assets.image`` names, or None
    when there is none on disk (a symlink is never followed)."""
    image = shot["assets"].get("image")
    if not image:
        return None
    try:
        path = ec.store.episode_asset_path(ec.story_id, ec.ep, "shots", image.rpartition("/")[2])
    except KeyError:
        return None
    return path if os.path.isfile(path) else None


def line_audio_path(ec, line):
    """The real path of a measured line's audio, or None."""
    audio = line["timing"].get("audio")
    prefix = f"{voice_lines.VOICE_ASSETS}/"
    if not isinstance(audio, str) or not audio.startswith(prefix):
        return None
    try:
        path = ec.store.episode_asset_path(ec.story_id, ec.ep, "voice", audio[len(prefix):])
    except KeyError:
        return None
    return path if os.path.isfile(path) else None


def sidecar_path(ec, line_id):
    """Where line *line_id*'s ``line_timing_v1`` sidecar is kept, or None."""
    try:
        return ec.store.episode_asset_path(ec.story_id, ec.ep, "voice", voice_lines.asset_name(line_id, "json"))
    except KeyError:
        return None


def read_sidecar(ec, line_id):
    """Line *line_id*'s sidecar, or None when it is missing or unreadable."""
    path = sidecar_path(ec, line_id)
    if path is None or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# Phase 7 stage 3b (A9): how many reference images one keyframe request of
# a layered (v2) shot sends, per link. Every value is at least
# shots.V2_MAX_REFERENCES, the most a v2 shot carries, so the images its role
# text names are the images sent.
REFERENCE_LIMITS = {"fal/seedream-4.5-edit": 10, "gemini/nano-banana-2": 14, "gemini/nano-banana-2-lite": 14}


def reference_limit(shot, link=None) -> int:
    """How many of *shot*'s references a request on *link* sends: a layered
    shot (``prompt_layout`` set) up to the link's own limit
    (:data:`REFERENCE_LIMITS`; a link not in it, or none chosen yet, takes
    all ``shots.V2_MAX_REFERENCES`` the shot holds); every other shot the
    first ``refimages.MAX_REFERENCES``, as always."""
    if not shot.get("prompt_layout"):
        return refimages.MAX_REFERENCES
    return REFERENCE_LIMITS.get(link, shots_mod.V2_MAX_REFERENCES)


def reference_paths(ec, shot, *, link=None) -> tuple:
    """``(paths, missing)``: the real paths of the reference images a shot
    sends (its ``reference_images``, the first :func:`reference_limit` on
    *link*: an edit takes no more), and the ones that are not on disk. The
    continuity slot (``shots.CONTINUITY_REFERENCE``, phase 8 stage B) is
    neither: :func:`request_parts` fills it."""
    paths, missing = [], []
    for rel in shot["reference_images"][:reference_limit(shot, link)]:
        if rel == shots_mod.CONTINUITY_REFERENCE:
            continue
        parts = rel.split("/")
        if len(parts) != 4 or parts[0] not in _ENTITY_KINDS or parts[2] != "refs":
            missing.append(rel)
            continue
        try:
            paths.append(ec.store.media_path(ec.story_id, parts[0], parts[1], parts[3]))
        except KeyError:
            missing.append(rel)
    return paths, missing


# What stands for the continuity slot in a prompt hash (phase 8 stage B): the
# same token whatever image fills it, or none -- see :func:`request_parts`.
_CONTINUITY_HASH_TOKEN = "continuity:previous_shot"


def continuity_slot(shot, link=None):
    """The index of *shot*'s continuity reference among the references it
    sends on *link* (``shots.CONTINUITY_REFERENCE``, phase 8 stage B), or
    None when it has none."""
    refs = shot["reference_images"][:reference_limit(shot, link)]
    return refs.index(shots_mod.CONTINUITY_REFERENCE) if shots_mod.CONTINUITY_REFERENCE in refs else None


def continuity_source(ec, storyboard, shot):
    """``(previous shot, its image path)`` -- the keyframe *shot*'s
    continuity reference sends (phase 8 stage B): the shot right before it
    in *storyboard*, in the same scene, whose image is on disk now (made in
    an earlier run, or just before it in this one: the images are made in
    storyboard order) -- or None: *shot* carries no continuity slot, is the
    first of its scene, or that image is not made."""
    if shots_mod.CONTINUITY_REFERENCE not in (shot.get("reference_images") or ()):
        return None
    ids = [item["shot_id"] for item in storyboard["shots"]]
    if shot["shot_id"] not in ids:
        return None
    index = ids.index(shot["shot_id"])
    if not shots_mod.continues_scene(storyboard["shots"], index):
        return None
    previous = storyboard["shots"][index - 1]
    path = shot_image_path(ec, previous)
    return (previous, path) if path is not None else None


def request_parts(ec, shot, *, note, link=None, continuity=None, alone=None) -> dict:
    """What *shot*'s image request is made of in the story's mode now:
    ``{kind, prompt, negative, consistency, size, references, missing,
    hash}`` -- ``prompt_only`` sends no reference (IMAGE_CHAIN),
    ``references`` sends the shot's (IMAGE_EDIT_CHAIN); ``hash`` is
    :func:`prompt_hash` over what is sent (a missing reference counts by its
    path, so the hash is never the one of a complete request). *link*: the
    episode's image link, when it has one (:func:`reference_limit`).

    The continuity slot of a v2 shot (phase 8 stage B,
    :func:`continuity_slot`) is filled with *continuity* -- the path of the
    previous keyframe of its scene (:func:`continuity_source`) -- in its
    place; with none to send, the shot is asked as it resolves without the
    slot (*alone*: ``(image_prompt, reference_images)``, the caller's
    re-resolution, so no role names an image that is not sent; None: the
    slot is left out). Either way ``hash`` counts the slot as one constant
    token (:data:`_CONTINUITY_HASH_TOKEN`) over the stored prompt: which
    image filled it -- or that none did -- never moves the hash, so a
    redrawn previous keyframe never makes this shot stale (no cascade of
    redraws). The record of what was sent is the shot's
    ``assets.continuity``."""
    mode = ec.consistency_mode
    prompt = effective_prompt(shot, ec.entities, note)
    negative = shot["negative_prompt"]
    if mode == PROMPT_ONLY:
        kind, paths, missing, ref_shas = gen.IMAGE, [], [], []
        return {"kind": kind, "prompt": prompt, "negative": negative, "consistency": mode, "size": SHOT_SIZE,
                "references": paths, "missing": missing,
                "hash": prompt_hash(prompt, negative, mode, SHOT_SIZE, ref_shas)}
    kind = gen.IMAGE_EDIT
    paths, missing = reference_paths(ec, shot, link=link)
    ref_shas = [_sha256_file(path) or f"unreadable:{path}" for path in paths] + [f"missing:{rel}"
                                                                                 for rel in missing]
    slot = continuity_slot(shot, link)
    if slot is None:
        return {"kind": kind, "prompt": prompt, "negative": negative, "consistency": mode, "size": SHOT_SIZE,
                "references": paths, "missing": missing,
                "hash": prompt_hash(prompt, negative, mode, SHOT_SIZE, ref_shas)}
    digest = prompt_hash(prompt, negative, mode, SHOT_SIZE, ref_shas + [_CONTINUITY_HASH_TOKEN])
    sent_prompt, sent = prompt, list(paths)
    if continuity is not None:
        sent.insert(min(slot, len(sent)), continuity)
    elif alone is not None and not shot.get("prompt_override"):
        alone_shot = dict(shot, image_prompt=alone[0], reference_images=list(alone[1]))
        sent_prompt = effective_prompt(alone_shot, ec.entities, note)
        sent, missing = reference_paths(ec, alone_shot, link=link)
    return {"kind": kind, "prompt": sent_prompt, "negative": negative, "consistency": mode, "size": SHOT_SIZE,
            "references": sent, "missing": missing, "hash": digest}


def _read_assets_doc(ec):
    """The episode's ``assets.json``, or None -- also for one that does not
    validate: a state is never refused over it (the step says so when it
    writes the document)."""
    try:
        return episode_common.read_episode(ec, ASSETS_DOC)
    except StepFailed:
        return None


def recorded_image_link(doc):
    """The image link *doc* (``assets.json``) records for its episode, or None."""
    entry = sticky_link.recorded(doc, sticky_link.IMAGE)
    return entry["link"] if entry else None


def shot_state(ec, shot, *, link=_READ) -> str:
    """:func:`image_state` of *shot* now: its hash recomputed with the note
    its image was made with; *link* the episode's recorded image link (read
    from ``assets.json`` unless given)."""
    if link is _READ:
        link = recorded_image_link(_read_assets_doc(ec))
    assets = shot["assets"]
    expected = request_parts(ec, shot, note=assets.get("note"), link=link)["hash"]
    return image_state(assets, expected_hash=expected, file_ok=shot_image_path(ec, shot) is not None, link=link)


def outdated_images(ec, storyboard, *, link=_READ) -> list:
    """The ids of the shots whose image on disk is not the one to use now:
    stale (its framing, action or prompt changed since it was made: its
    :func:`prompt_hash` moved; or it was made on another link than the one
    the episode was switched to) or failed (a regenerate asked another one)
    -- a locked shot keeps the image it has (DEC-155, as the assets approval
    takes it). What a render refuses (phase 5 stage 7): an assets approval's
    fingerprint holds each image's *recorded* hash, so it does not see the
    shot change under it."""
    if link is _READ:
        link = recorded_image_link(_read_assets_doc(ec))
    outdated = []
    for shot in storyboard["shots"]:
        if shot_image_path(ec, shot) is None:
            continue
        state = shot_state(ec, shot, link=link)
        if state != "current" and not (shot["assets"].get("locked") and state == "locked_stale"):
            outdated.append(shot["shot_id"])
    return outdated


def shots_to_make(ec, storyboard, *, link=_READ) -> list:
    """The shots the step makes an image for: neither locked nor current."""
    if link is _READ:
        link = recorded_image_link(_read_assets_doc(ec))
    return [shot for shot in storyboard["shots"]
            if not shot["assets"].get("locked") and shot_state(ec, shot, link=link) != "current"]


def image_kind(ec) -> str:
    """The chain the episode's shot images are made on: IMAGE_CHAIN in
    ``prompt_only`` mode, IMAGE_EDIT_CHAIN in ``references`` mode."""
    return gen.IMAGE if ec.consistency_mode == PROMPT_ONLY else gen.IMAGE_EDIT


# The ``media_policy`` role of a shot's image (phase 7, DEC-221).
KEYFRAME_ROLE = "keyframe"


def image_chain(ec, kind, merged) -> list:
    """The chain the episode's shot images are made on: the env chain of
    *kind* for a legacy story, the ``keyframe`` role's quality links for a
    v2 one (``media_policy.role_chain``). ``ChainError`` passes through."""
    return media_policy.role_chain(KEYFRAME_ROLE, kind, merged, ec.story)


def chain_name(ec, kind) -> str:
    """How that chain is named in a message (the env variable when legacy)."""
    return media_policy.chain_name(KEYFRAME_ROLE, kind, ec.story)


def _chain_labels(ec, kind, merged) -> list:
    try:
        return [describe(link) for link in image_chain(ec, kind, merged)]
    except ChainError:
        return []


def episode_image_link(ec, storyboard, *, env=None, doc=_READ) -> dict:
    """Which link the episode's shot images are made on now (A-087; calls
    nothing, writes nothing)::

        {"link": "<provider>/<model>" | None, "source": "record" | "derived" | None,
         "since", "switched_from", "served": {shot_id: link}, "mixed": [link, ...]}

    ``served`` maps each kept image -- current, or locked -- to the chain
    link that made it. The link is ``assets.json``'s ``links.image`` when it
    records one (*doc*, read unless given); else it is **derived** when every
    kept image was made on one link; else there is none: no image yet, or a
    legacy episode whose images mix links (``mixed`` names them), which
    walks the chain per shot as before. A derived link is written as a
    record only once the step serves an image with it."""
    if doc is _READ:
        doc = _read_assets_doc(ec)
    entry = sticky_link.recorded(doc, sticky_link.IMAGE)
    link = entry["link"] if entry else None
    chain = _chain_labels(ec, image_kind(ec), gating.merged_env(env))
    served = {}
    for shot in storyboard["shots"]:
        if shot_image_path(ec, shot) is None:
            continue
        if not shot["assets"].get("locked") and shot_state(ec, shot, link=link) != "current":
            continue
        made_on = served_link(shot["assets"])
        if made_on:
            served[shot["shot_id"]] = sticky_link.head_of(made_on, chain)
    info = {"link": link, "source": "record" if entry else None,
            "since": entry.get("since") if entry else None,
            "switched_from": entry.get("switched_from") if entry else None, "served": served, "mixed": []}
    if entry is None:
        links = list(dict.fromkeys(served.values()))
        if len(links) == 1:
            info.update(link=links[0], source="derived")
        elif len(links) > 1:
            info["mixed"] = links
    return info


def mixed_note(ec, info) -> str:
    """The one line a legacy episode whose images mix links prints."""
    counts = {}
    for made_on in info["served"].values():
        counts[made_on] = counts.get(made_on, 0) + 1
    parts = [f"{made_on} made {count} shot{'s' if count != 1 else ''}" for made_on, count in counts.items()]
    return (f"ℹ️ Episode {ec.ep} mixes image links ({_and(parts)}), so it keeps none: each shot walks "
            f"{chain_name(ec, image_kind(ec))} as before. Choose one with the assets edit "
            "{\"links\": {\"image\": \"<link>\"}}: the shots made on the others are then made again on it.")


def current_fingerprint(ec, storyboard, script, assets_doc) -> str:
    """:func:`assets_fingerprint` over the files as they are now, at the
    story's tier."""
    image_shas = {shot["shot_id"]: _sha256_file(shot_image_path(ec, shot)) for shot in storyboard["shots"]}
    audio_shas = {line["line_id"]: _sha256_file(line_audio_path(ec, line))
                  for scene in script["scenes"] for line in scene["lines"]}
    clip_shas = {shot["shot_id"]: _sha256_file(clips.shot_clip_path(ec, shot)) for shot in storyboard["shots"]
                 if shot["assets"].get("video")}
    return assets_fingerprint(storyboard, script, assets_doc, image_shas=image_shas, audio_shas=audio_shas,
                              clip_shas=clip_shas, tier=clips.tier_of(ec))


# ------------------------------------------------------------- preconditions

def _outdated_entities(storyboard, entity_docs) -> list:
    """The entities the storyboard's prompts were resolved from that changed
    since or are gone (``workflow.outdated_entities``' rule, duplicated: the
    steps never import the workflow)."""
    current = {}
    for docs in entity_docs.values():
        current.update(docs)
    outdated = []
    for eid, stamp in storyboard["resolved_from"].items():
        doc = current.get(eid)
        if doc is None:
            outdated.append(eid)
        elif doc.get("updated_at") != stamp:
            outdated.append(doc["name"])
    return outdated


def require_approved(ec) -> tuple:
    """``(script, storyboard)`` when the script is approved and the storyboard
    approved and current -- it covers the script, no scene is planned from an
    older revision, no prompt is outdated; else ``StepFailed`` saying what to
    do. Nothing is sent before this holds."""
    ep = ec.ep
    script = episode_common.read_episode(ec, SCRIPT_DOC)
    if script is None or not script["scenes"]:
        raise StepFailed(f"Episode {ep} has no script yet: write it first (the script step).")
    if not script["approved_at"] or not script_step.is_complete(script, ep) or script_step.needs_check(script):
        raise StepFailed(f"Approve episode {ep}'s script first: its assets are made from the approved script "
                         "and storyboard.")
    board = episode_common.read_episode(ec, STORYBOARD_DOC)
    if board is None or not board["shots"]:
        raise StepFailed(f"Episode {ep} has no storyboard yet: plan its shots (the storyboard step) and approve "
                         "them first.")
    if not board["approved_at"]:
        raise StepFailed(f"Approve episode {ep}'s storyboard first: its assets are made from the approved "
                         "script and storyboard.")
    if not episode_common.covers(board, script):
        raise StepFailed(f"Episode {ep}'s storyboard does not cover its script: plan its shots again (the "
                         "storyboard step) and approve them.")
    stale = sorted(storyboard_step.stale_scenes(board, script))
    if stale:
        raise StepFailed(f"Episode {ep}'s storyboard has scene{'s' if len(stale) > 1 else ''} "
                         f"{_and(stale)} planned from an older version of the script: plan "
                         f"{'them' if len(stale) > 1 else 'it'} again (the storyboard step) and approve it.")
    outdated = _outdated_entities(board, ec.entities)
    if outdated:
        raise StepFailed(f"Episode {ep}'s shot prompts are outdated ({_and(outdated)} changed since "
                         "they were resolved): refresh them and approve the storyboard again.")
    return script, board


# ----------------------------------------------------------------- estimate

def _open_ledger(ec) -> ledger_mod.CostLedger:
    return ledger_mod.CostLedger(os.path.join(ec.store.story_dir(ec.story_id), voices.LEDGER_NAME))


def _alignment_requests(ec, script, *, align_words) -> int:
    """The lines an opt-in alignment would transcribe: measured lines whose
    sidecar has no words, and lines to be measured on an engine that times
    no words (every pinned provider but Edge)."""
    if not align_words:
        return 0
    count = 0
    for scene in script["scenes"]:
        for line in scene["lines"]:
            if voice_lines.is_measured(ec, line):
                source, _by = wordtiming.source_of(read_sidecar(ec, line["line_id"]))
                count += source == wordtiming.EVEN_SPLIT
            else:
                voice = voice_lines.speaker_voice(ec, line["speaker"]) or {}
                count += bool(voice.get("provider")) and voice.get("provider") != "edge"
    return count


def image_quote(ec, qty, *, env, story_spent, adapters=None, probe_local=False, transport=None, storyboard=None,
                link_info=None) -> dict:
    """What *qty* shot images would cost on the story's route and in its
    consistency mode, calling nothing (a local editor is probed only with
    *probe_local*): ``imaging.estimate``'s answer on IMAGE_CHAIN in
    ``prompt_only`` mode, ``refimages.edit_readiness``' on IMAGE_EDIT_CHAIN in
    ``references`` mode -- ``{est_usd, route_class, link, links, ready,
    message, ...}``. :func:`asset_units` prices the shots to make with it;
    the fast track's estimate prices the shots it predicts.

    With the episode's image link (*link_info*, :func:`episode_image_link`,
    and its *storyboard*) the images are priced on that link alone
    (:func:`_sticky_quote`) and the answer gains ``sticky``; without one,
    it is the chain's answer above, unchanged."""
    if link_info and link_info.get("link") and storyboard is not None:
        return _sticky_quote(ec, qty, link_info, storyboard, env=env, story_spent=story_spent, adapters=adapters,
                             probe_local=probe_local, transport=transport)
    story = ec.story
    if ec.consistency_mode != PROMPT_ONLY:
        return refimages.edit_readiness(story, env=env, qty=qty, story_spent=story_spent, adapters=adapters,
                                        size=SHOT_SIZE, probe_local=probe_local, transport=transport,
                                        role=KEYFRAME_ROLE)
    request = gen.GenRequest(kind=gen.IMAGE, width=SHOT_SIZE[0], height=SHOT_SIZE[1])
    return imaging.estimate(gen.IMAGE, env, route=story["generation_profile"]["route"], request=request, qty=qty,
                            story_spent=story_spent, adapters=adapters, step=STEP, what="the shot images",
                            when="the assets step runs", role=KEYFRAME_ROLE, story=story)


_WHAT, _WHEN = "the shot images", "the assets step runs"


def _chain_rows(ec, qty, *, env, story_spent, adapters) -> dict:
    """``imaging.estimate`` of *qty* shot images on the episode's chain: a
    row per link through the runner's gates, calling nothing."""
    kind = image_kind(ec)
    request = gen.GenRequest(kind=kind, width=SHOT_SIZE[0], height=SHOT_SIZE[1])
    return imaging.estimate(kind, env, route=ec.story["generation_profile"]["route"], request=request, qty=qty,
                            story_spent=story_spent, adapters=adapters, step=STEP, what=_WHAT, when=_WHEN,
                            role=KEYFRAME_ROLE, story=ec.story)


def _local_status(kind, label, env, adapters, transport) -> tuple:
    """A local link's answer to "are you there" (its adapter's cached status
    probe when it has one, else its probe; ``refimages._ask_status``'s rule,
    duplicated: a private helper of another module): ``(ok, note)``."""
    provider, _, model = label.partition("/")
    link = Link(provider, model)
    adapter = gen.adapter_for(kind, provider, adapters)
    if adapter is None:
        return True, None  # nothing to ask: the runner decides
    merged = gating.merged_env(env)
    probe = getattr(adapter, "probe_cached", None) or adapter.probe
    kwargs = {"credentials": gen.credentials_for(link, merged), "env": merged}
    if transport is not None:
        kwargs["transport"] = transport
    try:
        return probe(link, **kwargs)
    except Exception as exc:  # noqa: BLE001 - a probe that breaks is a server that is not there
        return False, f"{type(exc).__name__}: {exc}"


def _sticky_quote(ec, qty, link_info, storyboard, *, env, story_spent, adapters, probe_local, transport) -> dict:
    """:func:`image_quote` on the episode's image link alone: the chain's
    row for that link (the runner's gates, calling nothing; a local link is
    asked whether it is there with *probe_local*) decides; ``links`` is that
    row. A link that cannot run is **gone** for now: ``ready`` false and
    ``sticky.gone`` the offer (:func:`sticky_offer`) -- never the next link."""
    link = link_info["link"]
    kind = image_kind(ec)
    quote = _chain_rows(ec, qty, env=env, story_spent=story_spent, adapters=adapters)
    sticky = {"link": link, "source": link_info["source"], "since": link_info["since"],
              "switched_from": link_info["switched_from"], "gone": None}
    if not quote["links"]:
        return dict(quote, sticky=sticky)  # the chain or the budget cannot be used: its own sentence
    row = next((row for row in quote["links"] if row["link"] == link), None)
    why = None
    if row is None:
        why = f"it is not a link of {chain_name(ec, kind)} any more"
    elif row["status"] != "runnable":
        why = row["reason"]
    elif probe_local and qty and link.startswith("local/"):
        ok, note = _local_status(kind, link, env, adapters, transport)
        if not ok:
            why = note or "not reachable"
    if why is None:
        verdict = imaging.verdict(kind, [row], route=ec.story["generation_profile"]["route"], qty=qty, step=STEP,
                                  what=_WHAT, when=_WHEN)
        return dict(verdict, sticky=sticky)
    gone = sticky_offer(ec, storyboard, link, why=why, env=env, story_spent=story_spent, adapters=adapters,
                        paid=bool(row and row["paid"]))
    sticky["gone"] = gone.as_dict()
    return dict(imaging.blocked(STEP, qty, [row] if row else [], str(gone)), sticky=sticky)


def sticky_offer(ec, storyboard, link, *, why, env, story_spent, adapters=None, paid=False,
                 before_any_call=True) -> StickyLinkGone:
    """The stop-and-ask of an episode whose image link *link* cannot serve
    (*why*), calling nothing: the next runnable link of the chain (in chain
    order, the link's own model swaps left out), the shots a switch makes
    again -- the unlocked current images *link* made -- with the shots still
    to make, and what they would cost on that next link."""
    kind = image_kind(ec)
    todo = [shot["shot_id"] for shot in shots_to_make(ec, storyboard, link=link)]
    redo = [shot["shot_id"] for shot in storyboard["shots"]
            if shot["shot_id"] not in todo and not shot["assets"].get("locked")
            and shot_image_path(ec, shot) is not None and sticky_link.on_link(served_link(shot["assets"]), link)]
    own = sticky_link.family(link)
    rows = _chain_rows(ec, max(1, len(todo) + len(redo)), env=env, story_spent=story_spent,
                       adapters=adapters)["links"]
    others = [row for row in rows if row["link"] not in own]
    nxt = next((row for row in others if row["status"] == "runnable"), None)
    route = None
    if nxt is not None:
        route = "paid" if nxt["paid"] else ("local" if nxt["link"].startswith("local/") else "free")
    return StickyLinkGone(
        ep=ec.ep, link=link, why=str(why).rstrip(". "), chain=chain_name(ec, kind),
        next_link=nxt["link"] if nxt else None, next_route=route,
        next_reason=None if nxt else "; ".join(f"{row['link']}: {row['reason']}" for row in others) or None,
        redo=redo, todo=todo, est_usd=nxt["est_usd"] if nxt and nxt["paid"] else 0.0, paid=paid,
        before_any_call=before_any_call)


def link_gone(units):
    """The offer of an :func:`asset_units` plan whose image link is gone
    (``images.sticky.gone``), or None."""
    return ((units["images"].get("sticky") or {}).get("gone")) if units["images"]["count"] else None


def link_switch(ec, value, *, env, errors) -> dict:
    """``{"image"?: link, "video"?: link}``: the links an assets edit's
    ``links`` (``{"image"?: "<link>", "video"?: "<link>"}``) switches the
    episode to -- empty when nothing is asked, or *errors* gained why not.
    ``image`` must be a link of the episode's image chain (IMAGE_CHAIN, or
    IMAGE_EDIT_CHAIN in ``references`` mode); ``video`` (phase 6 stage 11)
    a link of VIDEO_CHAIN, or the local ComfyUI (``clips.LOCAL_LINK``). The
    chains are the ones *env* -- the Settings values -- names."""
    if not isinstance(value, dict):
        errors.append("links: expected an object {image?, video?}")
        return {}
    extra = sorted(set(map(str, value)) - set(sticky_link.KINDS))
    if extra:
        errors.append(f"links: unknown key(s) {', '.join(extra)} (editable: {', '.join(sticky_link.KINDS)})")
    merged = gating.merged_env(env)
    wanted = {}
    for slot in sticky_link.KINDS:
        if slot not in value:
            continue
        kind = image_kind(ec) if slot == sticky_link.IMAGE else gen.VIDEO
        name = chain_name(ec, kind) if slot == sticky_link.IMAGE else gen.ENV_NAMES[kind]
        try:
            links = image_chain(ec, kind, merged) if slot == sticky_link.IMAGE else gen.chain_from_env(kind, merged)
            chain = [describe(link) for link in links]
        except ChainError as exc:
            errors.append(f"links.{slot}: {name} cannot be used ({exc})")
            continue
        allowed = chain if slot == sticky_link.IMAGE else chain + [link for link in (clips.LOCAL_LINK,)
                                                                   if link not in chain]
        link = value[slot]
        if not isinstance(link, str) or link not in allowed:
            errors.append(f"links.{slot}: {link!r} is not a link of {name} (its links: "
                          f"{', '.join(chain)})")
            continue
        wanted[slot] = link
    return wanted


def switched_assets_doc(ec, storyboard, doc, wanted, *, env, now):
    """*doc* (``assets.json``, or None: a minimal one is started) with the
    episode's image link switched to *wanted* -- ``links.image {link:
    wanted, since: now, switched_from: the link it had}`` -- or None when
    that is already its link (recorded, or derived from its images). Only
    the user switches (A-087): the images another link made are then stale
    (:func:`image_state`), the next run makes exactly those again, and the
    assets approval goes stale with the fingerprint's ``links``; the
    storyboard is not touched."""
    info = episode_image_link(ec, storyboard, env=env, doc=doc)
    if info["link"] == wanted:
        return None
    if doc is None:
        doc = {"$schema": schemas.EPISODE_ASSETS_SCHEMA_NAME, "ep": ec.ep, "lines": {}, "sfx": [], "bgm": None,
               "approved": None, "created_at": now, "updated_at": now}
    new = copy.deepcopy(doc)
    new["links"] = dict(new.get("links") or {})
    new["links"][sticky_link.IMAGE] = sticky_link.record(wanted, now=now, switched_from=info["link"])
    return new


def switched_video_doc(ec, doc, wanted, *, now):
    """*doc* (``assets.json``, or None: a minimal one is started) with the
    episode's video link switched to *wanted* -- ``links.video {link:
    wanted, since: now, switched_from: the link it had}`` -- or None when
    that is already its recorded link (phase 6 stage 11, A-087). Only the
    user switches: the clips another link made are then stale
    (``clips.clip_state`` reads the record), the next run animates exactly
    those again on *wanted*, and the assets approval goes stale with the
    fingerprint's ``links``; the storyboard -- its clip records, its
    approval -- is not touched."""
    entry = sticky_link.recorded(doc, sticky_link.VIDEO)
    current = entry["link"] if entry else None
    if current == wanted:
        return None
    new = copy.deepcopy(doc) if doc is not None else _minimal_assets_doc(ec, now)
    new["links"] = dict(new.get("links") or {})
    new["links"][sticky_link.VIDEO] = sticky_link.record(wanted, now=now, switched_from=current)
    return new


def overridden_assets_doc(ec, doc, changes, *, now):
    """*doc* (``assets.json``, or None: a minimal one is started) with the
    per-shot overrides *changes* (``{shot_id: {flag: True | False | None}}``,
    ``schemas.SHOT_OVERRIDE_FLAGS``; None clears the flag) applied to its
    ``shots`` map -- an entry left empty is dropped, and so is an empty map
    -- or None when nothing changes (phase 6 stage 7,
    ``workflow.patch_assets``). The storyboard is never touched, and the
    approval is kept: the fingerprint's ``clips`` part decides whether it
    is stale (:func:`assets_fingerprint`)."""
    base = doc if doc is not None else {
        "$schema": schemas.EPISODE_ASSETS_SCHEMA_NAME, "ep": ec.ep, "lines": {}, "sfx": [], "bgm": None,
        "approved": None, "created_at": now, "updated_at": now}
    shots = {shot_id: dict(entry) for shot_id, entry in (base.get("shots") or {}).items()}
    for shot_id, flags in changes.items():
        entry = shots.get(shot_id, {})
        for name, value in flags.items():
            if value is None:
                entry.pop(name, None)
            else:
                entry[name] = value
        if entry:
            shots[shot_id] = {name: entry[name] for name in schemas.SHOT_OVERRIDE_FLAGS if name in entry}
        else:
            shots.pop(shot_id, None)
    if shots == (base.get("shots") or {}):
        return None
    new = copy.deepcopy(base)
    new.pop("shots", None)
    if shots:
        new["shots"] = {shot_id: shots[shot_id] for shot_id in sorted(shots)}
    return new


def spending_caps(ec, total, *, env, ledger=None, video=None) -> tuple:
    """``(caps, over_cap)`` for a plan that would spend *total* paid
    dollars on episode *ec.ep*: ``caps`` is ``{"allow_paid", "episode"|"day"|
    "story": {"cap_usd", "spent_usd", "left_usd"}}`` (the three only when the
    budget settings read), ``over_cap`` the budget's refusal of *total* --
    the episode's cap included -- with the numbers, when paid is on; else
    None. Calls nothing. With *video* (``asset_units``' ``video`` part, phase
    6 stage 7) the refusal names the clips' numbers too."""
    ledger = ledger or _open_ledger(ec)
    story_spent = float(ledger.totals()["est_usd"])
    ep_spent = float(ledger.totals(ec.ep)["est_usd"])
    try:
        budget_obj = gating.budget_of(gating.merged_env(env))
    except ValueError:
        budget_obj = None
    day_spent = budget_mod.day_spent()
    caps = {"allow_paid": bool(budget_obj and budget_obj.allow_paid)}
    if budget_obj is not None:
        for name, cap, spent in (("episode", budget_obj.per_episode_cap_usd, ep_spent),
                                 ("day", budget_obj.daily_cap_usd, day_spent),
                                 ("story", budget_obj.per_story_cap_usd, story_spent)):
            caps[name] = {"cap_usd": cap, "spent_usd": round(spent, 4), "left_usd": round(max(0.0, cap - spent), 4)}
    over_cap = None
    if budget_obj is not None and budget_obj.allow_paid and total > 0:
        what = "paid images and voices"
        if video:
            count = video["count"]
            what = (f"paid images, voices and {count} clip{'' if count == 1 else 's'} ({video['seconds']} s on "
                    f"{video['link']}, est ${video['est_usd']:.3f})")
        plan = SimpleNamespace(est_usd=total, link=f"episode {ec.ep}'s {what}")
        try:
            budget_mod.check(plan, None, budget=budget_obj, day_spent=day_spent, ep_spent=ep_spent,
                             story_spent=story_spent)
        except budget_mod.BudgetRefused as exc:
            over_cap = str(exc)
    return caps, over_cap


def on_route(ec, route):
    """*ec* as though its story were on *route* (``auto``, ``local``,
    ``api``): a copy whose story's ``generation_profile.route`` is *route*,
    to price another route without patching the story (``GET
    /estimate/assets?route=``, phase 6 stage 11) -- nothing is written; *ec*
    itself when *route* is None or already the story's. ``ValueError`` for
    any other route."""
    profile = ec.story["generation_profile"]
    if route is None or route == profile["route"]:
        return ec
    if route not in defaults.ROUTES:
        raise ValueError(f"the route must be one of {', '.join(defaults.ROUTES)}, not {route!r}")
    story = dict(ec.story, generation_profile=dict(profile, route=route))
    return dataclasses.replace(ec, story=story)


def asset_units(ec, script, storyboard, *, env, align_words=False, adapters=None, probe_local=False,
                transport=None, ledger=None, animate=True, route=None) -> dict:
    """What the assets step would do and spend now, calling nothing (a local
    editor is asked whether it is there only with *probe_local*: the DEC-117
    status probe, never a generation)::

        {"images": {"shots": [shot_id, ...], "count", "kind", "chain", "consistency",
                    "route_class", "link", "est_usd", "links": [...], "ready", "message"},
         "voices": <voice_lines.measure_estimate>,
         "alignment": {"opted_in": bool, "requests": n},
         "paid_links": [{"kind", "link", "allowed", "reason", "est_usd"}],
         "caps": {"allow_paid", "episode"|"day"|"story": {"cap_usd", "spent_usd", "left_usd"}},
         "est_usd": x, "over_cap": sentence | None, "ready": bool}

    and, only at the story's ``generation_profile.tier`` >= 2 (phase 6 stage
    7; a tier-1 answer is byte for byte what it was), ``"video"``
    (``clips.video_units``: the planner's clips on the episode's video link,
    seconds x price) after ``alignment``.

    ``images`` prices every shot neither locked nor current at the first
    link that would run on the story's route (the image estimate of
    ``imaging``; in ``references`` mode ``refimages.edit_readiness``, whose
    ``ready`` false is DEC-117's "stop and ask") -- or, once the episode has
    an image link (:func:`episode_image_link`), on that link alone, with
    ``images.sticky`` ``{link, source, since, switched_from, gone}``: ``gone``
    is the stop-and-ask offer when the link cannot serve (``ready`` false,
    ``StickyLinkGone.as_dict``). ``voices`` prices the TTS
    characters of every line without current audio at each pinned voice's
    price. ``est_usd`` is the paid part of both; ``over_cap`` is the budget's
    refusal of it -- the episode's cap included -- when paid is on, with the
    numbers. ``ready``: the images can run (or there are none), every line
    has a voice that can run, and nothing is over a cap. At tier >= 2 the
    clips to buy are in ``est_usd`` (when they can run) and ``over_cap``
    (with their numbers), the paid video link in ``paid_links``, and
    ``ready`` needs ``video.ready`` too; what the episode spent and the rest
    of the paid part are committed before the planner spends the episode's
    cap on clips.

    With *animate* off (the step's ``animate`` param, phase 6 stage 8) the
    ``video`` part is still shown -- ``animate`` false, its message saying so
    -- but it is left out of the total, ``over_cap``, ``paid_links`` and
    ``ready``: the run makes no clip. So is a v2 episode's plan held for the
    keyframes' approval (``video.hold``, :func:`clip_hold`; phase 7 stage
    6b, RC-Q3).

    *route* (phase 6 stage 11) prices everything on that route instead of
    the story's own (:func:`on_route`), writing nothing.
    """
    ec = on_route(ec, route)
    ledger = ledger or _open_ledger(ec)
    story_spent = float(ledger.totals()["est_usd"])

    doc = _read_assets_doc(ec)
    todo = shots_to_make(ec, storyboard, link=recorded_image_link(doc))
    mode = ec.consistency_mode
    kind = gen.IMAGE if mode == PROMPT_ONLY else gen.IMAGE_EDIT
    if not todo:
        images = {"est_usd": 0.0, "route_class": None, "link": None, "links": [], "ready": True,
                  "message": "Every shot has its image."}
    else:
        images = image_quote(ec, len(todo), env=env, story_spent=story_spent, adapters=adapters,
                             probe_local=probe_local, transport=transport, storyboard=storyboard,
                             link_info=episode_image_link(ec, storyboard, env=env, doc=doc))
    sticky = images.get("sticky")
    images = {
        "shots": [shot["shot_id"] for shot in todo], "count": len(todo), "kind": kind,
        "chain": chain_name(ec, kind), "consistency": mode, "route_class": images["route_class"],
        "link": images["link"], "est_usd": float(images["est_usd"] or 0.0), "links": images["links"],
        "ready": images["ready"], "message": images["message"],
    }
    if sticky is not None:
        # The episode's image link (A-087): {link, source, since, switched_from, gone}.
        images["sticky"] = sticky
    voices_est = voice_lines.measure_estimate(ec, script, env=env, adapters=adapters)

    paid_links = [{"kind": kind, "link": row["link"], "allowed": row["status"] == "runnable",
                   "reason": row["reason"], "est_usd": row["est_usd"]}
                  for row in images["links"] if row["paid"]]
    for row in voices_est["voices"]:
        if row["paid"]:
            paid_links.append({"kind": gen.TTS, "link": row["link"], "allowed": row["allowed"],
                               "reason": row["reason"], "est_usd": row["est_usd"]})

    images_paid = images["est_usd"] if images["route_class"] == "paid" else 0.0
    voices_paid = sum(row["est_usd"] for row in voices_est["voices"] if row["paid"])
    video, video_paid = None, 0.0
    if clips.tier_of(ec) >= 2:
        video = _video_units(ec, script, storyboard, doc, env=env, ledger=ledger, adapters=adapters,
                             probe_local=probe_local, transport=transport, committed=images_paid + voices_paid)
        video["animate"] = bool(animate)
        if not animate:
            video["message"] = f"Animate off: no clip is made in this run. {video['message']}".strip()
        # A plan held for the keyframes' approval (phase 7 stage 6b, RC-Q3) is out of this run, as animate off.
        elif video["route_class"] == "paid" and video["count"] and not video.get("hold"):
            paid_links.append({"kind": gen.VIDEO, "link": video["link"], "allowed": video["ready"],
                               "reason": video["refused"] or "paid, allowed", "est_usd": video["est_usd"]})
            if video["ready"]:
                video_paid = video["est_usd"]
    total = round(images_paid + voices_paid + video_paid, 4)
    caps, over_cap = spending_caps(ec, total, env=env, ledger=ledger, video=video if video_paid else None)
    units = {
        "images": images, "voices": voices_est,
        "alignment": {"opted_in": bool(align_words),
                      "requests": _alignment_requests(ec, script, align_words=align_words)},
    }
    if video is not None:
        units["video"] = video
    units.update({
        "paid_links": paid_links, "caps": caps, "est_usd": total, "over_cap": over_cap,
        "ready": images["ready"] and voices_est["ready"] and over_cap is None,
    })
    if video is not None and animate and (not video.get("hold") or video.get("too_long")):
        units["ready"] = bool(units["ready"] and video["ready"])
    return units


def _video_units(ec, script, storyboard, doc, *, env, ledger, adapters, probe_local, transport, committed):
    """``clips.video_units`` for :func:`asset_units`: the caps as they stand
    (the episode's bounds the plan), and committed before any clip what the
    episode's ledger already holds -- every kind, as the per-episode cap
    counts it -- plus *committed*, the rest of this estimate's paid part."""
    caps, _over = spending_caps(ec, 0.0, env=env, ledger=ledger)
    spent = float((caps.get("episode") or {}).get("spent_usd") or 0.0)
    return clips.video_units(ec, script, storyboard, doc, env=env, caps=caps, committed_usd=spent + committed,
                             adapters=adapters, probe_local=probe_local, transport=transport,
                             image_sha=lambda shot: _sha256_file(shot_image_path(ec, shot)),
                             booked=_clip_booked(ec, script, doc), hold=clip_hold(ec, storyboard, doc))


# -------------------------------------------------------------- the clips

def _minimal_assets_doc(ec, now) -> dict:
    """An ``assets.json`` with nothing in it yet, for a record that needs one."""
    return {"$schema": schemas.EPISODE_ASSETS_SCHEMA_NAME, "ep": ec.ep, "lines": {}, "sfx": [], "bgm": None,
            "approved": None, "created_at": now, "updated_at": now}


def _video_summary(video, *, animate) -> dict:
    """The step summary's ``video`` before the phase: nothing made yet."""
    return {"animate": bool(animate), "planned": len(video["plan"]) if animate else 0, "made": 0, "reused": 0,
            "failed": [], "seconds": 0, "usd": 0.0, "link": video["link"], "route": video["route_class"]}


def clip_seed(shot, *, story_id, ep) -> int:
    """The seed of the next request for *shot*'s clip: a re-animate's
    ``pending`` seed when one is left (DEC-154), else the seed its keyframe
    was made with -- the shot image's own -- else :func:`derive_seed`."""
    pending = (shot["assets"].get("clip") or {}).get("pending")
    if pending:
        return pending["seed"]
    seed = _seed_value(shot["assets"].get("seed"))
    return seed if seed is not None else derive_seed(story_id, ep, shot["shot_id"])


def clip_request(ec, shot, script, *, link, template, clip_s, seed, note, flags, tier, out_dir=""):
    """``(parts, request)``: the prompt parts of *shot*'s clip
    (``clips.clip_request_parts``) and the ``GenRequest`` the video phase
    sends for it -- its keyframe the shot's image, its length *clip_s*, its
    seed, the local template in ``extra`` -- whose cache key is the one the
    generation journal keeps it under (``out_dir`` and the name are not in
    the key). ``ValueError`` for a prompt that cannot be built."""
    parts = clips.clip_request_parts(ec, shot, script, tier=tier, flags=flags, note=note)
    extra = {"name": f"shot_{shot['shot_id'][2:]}"}
    if template:
        extra["template"] = template
    resolution = media_policy.video_resolution(ec.story)
    if resolution != defaults.VIDEO_RESOLUTION_DEFAULT:
        # The story's 1080p switch (phase 7 stage 4): seedance reads it, the cache
        # keys it; a 720p request is the one it always was (DEC-207).
        extra["resolution"] = resolution
    request = gen.GenRequest(kind=gen.VIDEO, prompt=parts["prompt"], negative=parts["negative"], width=SHOT_SIZE[0],
                             height=SHOT_SIZE[1], seed=seed, references=(shot_image_path(ec, shot),),
                             duration_s=int(clip_s), native_audio=parts["native_audio"], out_dir=out_dir, extra=extra)
    return parts, request


def _clip_booked(ec, script, doc):
    """``booked(shot, *, link, clip_s, template)`` for ``clips.video_units``:
    whether the story's generation journal holds *shot*'s next clip request
    -- the very one the video phase would send (:func:`clip_request`) --
    bought: ``submitted`` and booked (the provider holds it: the next run
    collects it) or ``done`` with its clip kept intact. Such a clip costs
    nothing more (DEC-152), so the plan never charges it twice. Reads the
    journal only: never books, never calls. None without a cache folder."""
    try:
        root = ec.store.gen_cache_dir(ec.story_id)
    except KeyError:
        return None
    cache = gencache.GenCache(root, book=lambda _entry: None)
    tier = clips.tier_of(ec)

    def booked(shot, *, link, clip_s, template):
        if shot_image_path(ec, shot) is None:
            return False
        try:
            _parts, request = clip_request(ec, shot, script, link=link, template=template, clip_s=clip_s,
                                           seed=clip_seed(shot, story_id=ec.story_id, ep=ec.ep),
                                           note=clip_note(shot), flags=clips.shot_flags(shot, doc), tier=tier)
            key = gencache.request_key(gen.VIDEO, link, request)
            entry = cache.lookup(key) if key else None
        except (gencache.JournalError, KeyError, ValueError, OSError):
            return False
        if not entry or not entry.get("booked"):
            return False
        if entry.get("state") == gencache.SUBMITTED:
            return True
        if entry.get("state") != gencache.DONE:
            return False
        files = (entry.get("output") or {}).get("files") or []
        return bool(files) and all(_sha256_file(cache.file_path(item.get("file") or "")) == item.get("sha256")
                                   for item in files)

    return booked


def clip_note(shot):
    """The note *shot*'s next clip is asked with: a re-animate's pending one,
    else the one its clip was made with."""
    clip = shot["assets"].get("clip") or {}
    pending = clip.get("pending")
    return pending.get("note") if pending else clip.get("note")


def keyframe_problem(ec, shot, *, link=_READ):
    """Why *shot*'s image cannot be its clip's first frame now, or None: it
    must be on disk and current (a locked image is taken as it is, as the
    render takes it). *link* the episode's recorded image link."""
    if shot_image_path(ec, shot) is None:
        return "keyframe (the shot's image) is not made yet: make it first (the assets step)"
    state = shot_state(ec, shot, link=link)
    if state == "current" or (shot["assets"].get("locked") and state == "locked_stale"):
        return None
    return (f"keyframe (the shot's image) is {state.replace('_', ' ')}: make it again first (the assets step, or "
            f"regenerate '{shot_target(ec.ep, shot['shot_id'])}')")


# --------------------------------------------- the keyframes (phase 7 stage 6b)

def keyframe_items(ec, storyboard, doc) -> list:
    """J2's items (``judge.check_keyframes``): ``[(shot, path, sha,
    previous_shot_id, previous_path, previous_sha)]`` for every shot whose
    keyframe is current (:func:`keyframe_problem` finds nothing; a locked
    image as it is), in storyboard order. The previous ones are the shot
    before it in the storyboard -- None for the first shot, or one with no
    image on disk. Hashes every image."""
    link = recorded_image_link(doc)
    items, previous = [], (None, None, None)
    for shot in storyboard["shots"]:
        path = shot_image_path(ec, shot)
        sha = _sha256_file(path) if path is not None else None
        if path is not None and keyframe_problem(ec, shot, link=link) is None:
            prev_id, prev_path, prev_sha = previous if previous[1] is not None else (None, None, None)
            items.append((shot, path, sha, prev_id, prev_path, prev_sha))
        previous = (shot["shot_id"], path, sha)
    return items


def keyframe_context(ec, storyboard, *, ledger=None):
    """What J2 is shown beside the keyframes (``judge.KeyframeContext``,
    phase 8 stage B): each character's identity sheet on disk (its
    portrait: the full-body sheet on a v2 story), the episode's continuity
    *ledger* (its wardrobe sets) and the scene of every shot."""
    sheets = {}
    for cid, doc in ec.entities["characters"].items():
        portrait = (doc.get("refs") or {}).get("portrait")
        if not portrait or not portrait.get("name"):
            continue
        try:
            path = ec.store.media_path(ec.story_id, "characters", cid, portrait["name"])
        except KeyError:
            continue
        if path and os.path.isfile(path) and not os.path.islink(path):
            sheets[cid] = path
    return judge.KeyframeContext(sheets=sheets, ledger=ledger,
                                 scenes={shot["shot_id"]: shot["scene_id"] for shot in storyboard["shots"]})


def keyframes_fingerprint(ec, storyboard) -> str:
    """``judge.keyframes_fingerprint`` of the keyframes on disk now, in
    storyboard order."""
    return judge.keyframes_fingerprint([(shot["shot_id"], _sha256_file(shot_image_path(ec, shot)))
                                        for shot in storyboard["shots"]])


def keyframes_state(ec, storyboard, doc) -> str:
    """The keyframe approval's state (``judge.keyframes_state``): ``none`` |
    ``current`` | ``stale``. Hashes every image."""
    return judge.keyframes_state((doc or {}).get(judge.KEYFRAMES_APPROVED), keyframes_fingerprint(ec, storyboard))


def clip_hold(ec, storyboard, doc):
    """Why a v2 episode's clips wait (RC-Q3: no clip is bought before the
    keyframes' approval is current), or None: a legacy story (never held),
    or keyframes approved, current, and none still to make. Calls
    nothing; hashes every image."""
    if not media_policy.is_v2(ec.story):
        return None
    ep = ec.ep
    todo = [shot["shot_id"] for shot in shots_to_make(ec, storyboard, link=recorded_image_link(doc))]
    if todo:
        many = len(todo) > 1
        return (f"shot{'s' if many else ''} {_and(todo)} {'have' if many else 'has'} no current keyframe yet: the "
                f"assets step makes {'them' if many else 'it'}, then approve the keyframes first "
                f"({judge.KEYFRAMES_APPROVAL}:{ep}) and run it again to buy the clips")
    state = keyframes_state(ec, storyboard, doc)
    if state == "none":
        return (f"approve the keyframes first ({judge.KEYFRAMES_APPROVAL}:{ep}): no clip of a v2 episode is bought "
                "before they are approved")
    if state == "stale":
        return (f"the keyframes changed since they were approved: approve the keyframes first "
                f"({judge.KEYFRAMES_APPROVAL}:{ep}), again, before any clip is bought")
    return None


def _plan_text(video) -> str:
    rows = (video or {}).get("plan") or []
    if not rows:
        return f"no clip{' on ' + video['link'] if (video or {}).get('link') else ''}"
    return (", ".join(f"{row['shot_id']} {row['clip_s']} s ${float(row['est_usd']):.3f}" for row in rows)
            + f" on {video['link']}")


def _plan_key(video):
    return ((video or {}).get("link"),
            [(row["shot_id"], int(row["clip_s"]), round(float(row["est_usd"]), 4))
             for row in (video or {}).get("plan") or []])


def plan_moved(ec, before, now):
    """RC-V6: the refusal when the clips' plan *now* (derived once as the
    video phase starts) is not the one the step's check saw (*before*) --
    other shots, lengths, prices or link, because prices, spend or caps
    moved -- or None. A run never buys other clips than its estimate
    listed: it stops, naming both."""
    if _plan_key(before) == _plan_key(now):
        return None
    return (f"Episode {ec.ep}'s clips were not made: their plan moved since this run's estimate (prices, spend or "
            f"caps changed), and a run buys only the clips its estimate listed. The estimate: {_plan_text(before)}. "
            f"Now: {_plan_text(now)}. No clip was asked; the images and voices made in this run are kept. Run the "
            "assets step again: its estimate is the new plan.")


def video_offer(ec, storyboard, link, *, why, env, adapters=None, todo=(), paid=False,
                before_any_call=True) -> StickyLinkGone:
    """The stop-and-ask of an episode whose video link *link* cannot serve
    (*why*), calling nothing (``StickyLinkGone`` kind ``video``): the next
    hosted link of VIDEO_CHAIN that could run (in chain order, keyed, paid
    on; the link's own model swaps left out), the clips a switch would make
    again -- the current ones *link* made -- with *todo*, the ones still to
    make, and what they would cost there."""
    merged = gating.merged_env(env)
    try:
        chain = gen.chain_from_env(gen.VIDEO, merged)
    except ChainError:
        chain = []
    own = sticky_link.family(link)
    rows = [row for row in clips.hosted_rows(chain, merged, adapters,
                                             resolution=media_policy.video_resolution(ec.story))
            if row["link"] not in own]
    try:
        allow = gating.budget_of(merged).allow_paid
    except ValueError:
        allow = False
    keyed = [row for row in rows if row["status"] == "keyed"]
    nxt = keyed[0] if keyed and allow else None
    todo = list(todo)
    redo = [shot["shot_id"] for shot in storyboard["shots"]
            if shot["shot_id"] not in todo and (shot["assets"].get("clip") or {}).get("state") == "current"
            and sticky_link.on_link((shot["assets"].get("clip") or {}).get("link"), link)]
    est = 0.0
    if nxt is not None:
        durations = {shot["shot_id"]: max(float(shot["duration_s"] or 0.0), 0.01) for shot in storyboard["shots"]}
        est = sum(video_plan.requested_seconds(nxt["link"], durations[shot_id]) * nxt["price_per_second"]
                  for shot_id in redo + todo)
    next_reason = None
    if nxt is None:
        next_reason = ("allow_paid is off" if keyed
                       else "; ".join(f"{row['link']}: {row['reason']}" for row in rows) or None)
    return StickyLinkGone(
        ep=ec.ep, link=link, why=str(why).rstrip(". "), chain=gen.ENV_NAMES[gen.VIDEO],
        next_link=nxt["link"] if nxt else None, next_route="paid" if nxt else None, next_reason=next_reason,
        redo=redo, todo=todo, est_usd=est, paid=paid, before_any_call=before_any_call, kind=sticky_link.VIDEO)


def open_clip_request(ec, keys):
    """The generation journal's entry under one of *keys* that is still open
    -- ``sending`` or ``submitted``: the provider may hold it, billed -- or
    None. An entry that cannot be read counts as open (it may hold a paid
    request). Reads the journal only: never books, never calls."""
    try:
        root = ec.store.gen_cache_dir(ec.story_id)
    except KeyError:
        return None
    cache = gencache.GenCache(root, book=lambda _entry: None)
    for key in dict.fromkeys(key for key in keys if key):
        try:
            entry = cache.lookup(key)
        except gencache.JournalError as exc:
            return {"key": key, "state": "unreadable", "note": str(exc)}
        except ValueError:
            continue
        if entry and entry.get("state") in (gencache.SENDING, gencache.SUBMITTED):
            return entry
    return None


def pending_clip_keys(ec, script, shot, doc, *, link=None) -> list:
    """The generation-journal keys a re-animate's ``pending`` request of
    *shot* (DEC-154: its seed and note kept before the call) may have been
    sent under on *link* -- the episode's video link, else the one its clip
    records -- or ``[]`` when none is pending. Its length is the one the
    planner asks for the shot (``video_plan.requested_seconds``) or its
    clip's own; on a local ComfyUI each shipped workflow's
    (``hardware.VIDEO_WORKFLOWS``: which one ran is the server's card, not
    asked here). What the render's refusal checks with
    :func:`open_clip_request` when the storyboard still holds the old clip's
    key -- the process stopped after the provider took the request, before
    its answer was recorded (phase 6 stage 11). Reads files only."""
    clip = shot["assets"].get("clip") or {}
    pending = clip.get("pending")
    link = link or clip.get("link")
    if not pending or not link or shot_image_path(ec, shot) is None:
        return []
    duration = max(float(shot["duration_s"] or 0.0), 0.01)
    options = [(None, None)]
    if link.startswith("local/"):
        options = []
        for template in dict.fromkeys(hardware.VIDEO_WORKFLOWS.values()):
            try:
                options.append((template, local_comfyui.video_clip_lengths(template)))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    flags, tier = clips.shot_flags(shot, doc), clips.tier_of(ec)
    keys = []
    for template, lengths in options:
        seconds = [int(clip["clip_s"])] if clip.get("clip_s") else []
        try:
            seconds.insert(0, video_plan.requested_seconds(link, duration, lengths=lengths))
        except ValueError:
            pass
        for clip_s in dict.fromkeys(seconds):
            try:
                _parts, request = clip_request(ec, shot, script, link=link, template=template, clip_s=clip_s,
                                               seed=pending["seed"], note=pending.get("note"), flags=flags, tier=tier)
                key = gencache.request_key(gen.VIDEO, link, request)
            except (KeyError, ValueError, OSError):
                continue
            if key:
                keys.append(key)
    return keys


def still_generating(shot, entry) -> str:
    """The refusal of a re-animate while *shot*'s clip request *entry* is
    still open (:func:`open_clip_request`): Continue alone collects it."""
    request_id = (entry.get("request") or {}).get("request_id")
    held = f"request {request_id}" if request_id else f"journal entry {str(entry.get('key'))[:12]}"
    return (f"shot {shot['shot_id']}'s clip is still generating ({held} is {entry.get('state')} on "
            f"{entry.get('link') or 'its video link'}): {CONTINUE_ONLY}. Asking again now would buy a second clip "
            "while the first is billed.")


def clip_target_refusal(ec, shot, *, doc=_READ, storyboard=None):
    """Why *shot*'s clip cannot be made again (``shot:<ep>:<shid>:video``,
    phase 6 stage 8), calling nothing, or None: the story is not at tier 2
    or 3, the clip's recorded request is still open in the generation
    journal (still generating: only Continue collects it,
    :func:`still_generating`), the shot is kept still (its effective flag,
    *doc* ``assets.json`` read unless given), or its keyframe is not
    current. A clip whose request the provider settled stays regenerable.
    A v2 story's, also while the keyframes' approval is not current
    (:func:`clip_hold` over *storyboard*, read unless given; RC-Q3)."""
    tier = clips.tier_of(ec)
    if tier < 2:
        return (f"the story is at tier {tier}: a shot is animated only at tier 2 or 3 (set its "
                "generation_profile.tier first).")
    entry = open_clip_request(ec, [(shot["assets"].get("clip") or {}).get("cache_key")])
    if entry is not None:
        return still_generating(shot, entry)
    if doc is _READ:
        doc = _read_assets_doc(ec)
    if clips.shot_flags(shot, doc)["keep_still"]:
        return f"shot {shot['shot_id']} is kept still: clear its keep_still first."
    problem = keyframe_problem(ec, shot, link=recorded_image_link(doc))
    if problem:
        return f"shot {shot['shot_id']}'s {problem}."
    if media_policy.is_v2(ec.story):
        board = storyboard if storyboard is not None else episode_common.read_episode(ec, STORYBOARD_DOC)
        hold = clip_hold(ec, board, doc) if board is not None else None
        if hold:
            return f"{hold}."
    return None


def clip_quote(ec, script, storyboard, shot, *, env, adapters=None, probe_local=False, transport=None,
               ledger=None) -> dict:
    """What one new clip of *shot* would cost now and on which link, calling
    nothing (a local ComfyUI is asked its status only with *probe_local*):
    the episode's video link and length as the planner picks them
    (``clips.video_units`` with that shot pinned and every other kept still;
    a current clip counts as new: a regenerate always asks), then the caps.
    Not ready while the shot's clip request is still open in the journal --
    the one it recorded, or the one the video phase would send next (its
    pending seed): still generating, Continue alone collects it::

        {"video": <video_units>, "link", "route_class", "clip_s", "est_usd", "over_cap", "ready", "message"}
    """
    ledger = ledger or _open_ledger(ec)
    doc = _read_assets_doc(ec)
    trial = copy.deepcopy(doc) if doc else {}
    real = (trial.get("shots") or {})
    trial["shots"] = {other["shot_id"]: {"keep_still": True} for other in storyboard["shots"]
                      if other["shot_id"] != shot["shot_id"]}
    trial["shots"][shot["shot_id"]] = dict(real.get(shot["shot_id"]) or {}, keep_still=False, animate=True)
    caps, _over = spending_caps(ec, 0.0, env=env, ledger=ledger)
    spent = float((caps.get("episode") or {}).get("spent_usd") or 0.0)
    video = clips.video_units(ec, script, storyboard, trial, env=env, caps=caps, committed_usd=spent,
                              adapters=adapters, probe_local=probe_local, transport=transport, image_sha=None,
                              hold=clip_hold(ec, storyboard, doc))
    row = next((item for item in video["plan"] if item["shot_id"] == shot["shot_id"]), None)
    quote = {"video": video, "link": video["link"], "route_class": video["route_class"],
             "clip_s": row["clip_s"] if row else None, "est_usd": round(float(row["est_usd"]), 4) if row else 0.0,
             "over_cap": None, "ready": False, "message": video["message"]}
    if row is None or not video["ready"] or video.get("hold"):
        return quote
    keys = [(shot["assets"].get("clip") or {}).get("cache_key")]
    try:
        _parts, request = clip_request(ec, shot, script, link=video["link"], template=video.get("template"),
                                       clip_s=row["clip_s"], seed=clip_seed(shot, story_id=ec.story_id, ep=ec.ep),
                                       note=clip_note(shot), flags=clips.shot_flags(shot, doc),
                                       tier=clips.tier_of(ec))
        keys.append(gencache.request_key(gen.VIDEO, video["link"], request))
    except (KeyError, ValueError, OSError):
        pass
    entry = open_clip_request(ec, keys)
    if entry is not None:
        quote["message"] = still_generating(shot, entry)
        return quote
    paid = video["route_class"] == "paid"
    _caps, over = spending_caps(ec, quote["est_usd"] if paid else 0.0, env=env, ledger=ledger)
    what = f"1 clip ({row['clip_s']} s) of shot {shot['shot_id']} on {video['link']}"
    if over:
        quote.update(over_cap=over, message=f"{what} would go over a cap, so nothing would be generated or spent: "
                                             f"{over}.")
        return quote
    price = f"paid: est ${quote['est_usd']:.3f}" if paid else "on your own hardware: $0.00"
    quote.update(ready=True, message=f"{what}, {price}.")
    return quote


def needs_editor(units) -> bool:
    """Whether :func:`asset_units`' plan stops at DEC-117's "stop and ask":
    shots to make in ``references`` mode and no editor that can run (not an
    image link that is gone: :func:`link_gone`)."""
    images = units["images"]
    return (bool(images["count"]) and not images["ready"] and images["kind"] == gen.IMAGE_EDIT
            and not link_gone(units))


def plan_refusal(ec, units, *, unprobed=False):
    """Why the step would stop before its first call on the plan *units*
    (:func:`asset_units`), or None: the shot images cannot run (in
    ``references`` mode, no editor: stop and ask), the clips cannot run
    (tier >= 2 with ``animate`` on: never a silent skip of the video part),
    or a paid part is over a cap -- with the numbers. The step's own check
    (``check_plan``), and the web layer's before a job exists (*unprobed*:
    its plan asked no local server, so clips waiting only on the local
    ComfyUI's answer are left to the step). The episode's image link gone
    for now stops first, with its offer (A-087, :class:`StickyLinkGone`).
    A fully animated plan with a shot its link's longest clip cannot cover
    (``video.too_long``, stage E) is refused even while its clips wait for
    the keyframes: planning the shots again changes the keyframes too."""
    video = units.get("video")
    if video is not None and video.get("animate", True) and video.get("too_long"):
        # Stage E: refused first, before any call, keyframes included -- planning the shots again changes them.
        return (f"Episode {ec.ep}'s clips cannot be made: {video['too_long']}. Nothing was generated or spent.")
    images = units["images"]
    if images["count"] and not images["ready"]:
        gone = link_gone(units)
        if gone:
            return gone["message"]
        if needs_editor(units):
            reasons = [f"{row['link']}: {row['reason']}" for row in images["links"]] or [images["message"]]
            readiness = {"message": images["message"], "links": images["links"],
                         "units": {"images": images["count"]}}
            return str(refimages.NeedsEditor(reasons, readiness, subject=f"Every shot of episode {ec.ep}",
                                             story=ec.story))
        return (f"Episode {ec.ep}'s shot images cannot be made: {images['message']} Nothing was generated or "
                "spent.")
    if (video is not None and video.get("animate", True) and not video.get("hold") and not video["ready"]
            and not (unprobed and clips.local_unasked(video))):
        return (f"Episode {ec.ep}'s clips cannot be made now: {video['message'].strip()} Nothing was generated "
                "or spent: run the assets step with animate off to make the keyframes first, or fix that and run "
                "it again.")
    if units["over_cap"]:
        # The quality profile's every-shot plan is refused whole (DEC-227): its numbers too.
        whole = f" {video['over_cap']}." if video is not None and video.get("animate", True) and video.get(
            "over_cap") else ""
        return (f"Episode {ec.ep}'s assets would go over a cap, so nothing was generated or spent: "
                f"{units['over_cap']}.{whole} Raise the cap, or choose free links, then run the assets step again.")
    return None


# ---------------------------------------------------------------- alignment

class AlignError(Exception):
    """No STT link could transcribe the line; the message says why."""


def default_transcriber(env):
    """``transcribe(path, *, language, on_log, cancel) -> (words, aligned_by)``
    through the hosted links of ``STT_CHAIN`` (``clipping.providers.stt``)
    that have a key, in order; or ``(None, reason)`` when none has one."""
    from clipping.providers import stt
    from clipping.providers.registry import PROVIDERS

    merged = gating.merged_env(env)
    try:
        chain = stt.parse_stt_chain(merged.get("STT_CHAIN") or stt.DEFAULT_STT_CHAIN)
    except ChainError as exc:
        return None, f"STT_CHAIN cannot be used: {exc}"
    keys = {name: merged[provider.env_key].strip() for name, provider in PROVIDERS.items()
            if (merged.get(provider.env_key) or "").strip()}
    hosted = [link for link in chain if link.provider != "local" and keys.get(link.provider)]
    if not hosted:
        return None, "no hosted link of STT_CHAIN has a key"

    def transcribe(path, *, language, on_log, cancel):
        failures = []
        for link in hosted:
            try:
                _text, segments, _language = stt.transcribe(path, chain=[link], keys=keys, language=language,
                                                             on_log=on_log, cancel=cancel)
            except Exception as exc:  # noqa: BLE001 - the next link; Cancelled is not an Exception
                failures.append(f"{describe(link)}: {type(exc).__name__}: {exc}")
                continue
            return [word for segment in segments for word in segment.get("words") or []], describe(link)
        raise AlignError("; ".join(failures) or "no link answered")

    return transcribe, None


# --------------------------------------------------------------------- the run

class _Assets(voice_lines.LineMeasurement):
    """One run of the step (or of one of its regenerates): the documents as
    they stand, the gates every paid call meets, and what failed."""

    measure_step = STEP
    # The ffmpeg runner of a v2 keyframe's source crop (A6); None is
    # ``subprocess.run``, read when it runs (a test hands in a fake).
    crop_run = None

    def __init__(self, ctx, ec, *, tools, transcribe=None, budget=None):
        self.ctx = ctx
        self.ec = ec
        self.tools = tools
        # The fast track hands in its own budget; otherwise the step's own.
        self.budget = budget if budget is not None else episode_common.Budget(tools.time_fn)
        self.transcribe = transcribe
        self.failed = []  # [(what, target, reason)]
        self.voice_failed = []  # [(line_id, speaker, reason)]
        self.measured = 0
        self.board_refused = False
        self.script = None
        self.storyboard = None
        self.gates = None
        self.made = []  # [(shot_id, cached)]
        self.aligned = []
        # The chain failures of each shot and line that failed, by id: what
        # the pacing reads (:func:`rate_limited_by`).
        self.shot_failures = {}
        self.line_failures = {}
        # The episode's image link (A-087, :func:`episode_image_link`):
        # ``link`` is the one every image is asked of (None: no link yet --
        # the chain until one serves -- or a legacy episode whose images mix
        # links); ``link_kept`` once ``links.image`` is recorded (or waits in
        # ``link_pending`` for the ``assets.json`` the step writes at its
        # end); ``link_gone`` the offer once the link went away in this run.
        self.link_info = None
        self.link = None
        self.link_kept = False
        self.link_pending = None
        self.link_gone = None
        # The clips (phase 6 stage 8): the ``video`` part the step's check saw
        # (the plan to animate; None: no video phase), the step summary's
        # ``video``, whether ``links.video`` is recorded, the offer once the
        # video link went away, the planned shots not asked yet (the offer's
        # ``todo``), and whether a shot image ran on a local ComfyUI in this run
        # (``POST /free`` before the first clip).
        self.planned_video = None
        self.video = None
        self.video_link_kept = False
        self.video_gone = None
        self.clip_todo = []
        self.local_image_ran = False
        # Phase 7 stage 6b: what the keyframe judge (J2) did in this run (None: not a v2 story).
        self.keyframe_check = None
        # Phase 8 stage B: the episode's continuity ledger, read once (:meth:`ledger_now`).
        self.ledger_read = _READ

    # ---------------------------------------------------------- plumbing

    def voice_refused(self, line, exc) -> None:
        """``voice_lines``' hook: the one-link chain's failures behind a
        line's ``VoiceError`` (none when it did not come from the chain)."""
        self.line_failures[line["line_id"]] = tuple(getattr(exc.__cause__, "failures", None) or ())

    def failures(self) -> str:
        return "; ".join(f"{what} failed ({reason})" for what, _target, reason in self.failed)

    def save(self) -> None:
        episode_common.retime(self.script, self.ec, self.storyboard)
        episode_common.write_script(self.ec, self.script, now=llm_call.utc_now())

    def open_asset_gates(self):
        """``voices.LineGates`` of the episode: the Settings, the budget, the
        story's ledger (checked readable) and the free-tier limiter, shared by
        every image and line of the run."""
        ec = self.ec
        try:
            self.gates = voices.LineGates(ec.store, ec.story_id, env=self.ctx.settings_env, ep=ec.ep)
        except voices.VoiceError as exc:
            raise StepFailed(f"Episode {ec.ep}'s assets cannot be made: {exc}") from None
        return self.gates

    def cache_root(self) -> str:
        ec = self.ec
        try:
            return ec.store.gen_cache_dir(ec.story_id, create=True)
        except KeyError:
            raise StepFailed("The story's generation cache (cache/gen/) is not a real directory; it is never "
                             "followed: move it away first.") from None

    def cache(self, kind, *, unit, qty) -> gencache.GenCache:
        """The story's generation cache, booking through the gates' ledger."""
        return gencache.GenCache(self.cache_root(), book=self.gates.booker(kind, step=STEP, unit=unit, qty=qty))

    def voice_cache(self, gates, line):
        return self.cache(gen.TTS, unit="char", qty=len(line["text"]))

    def write_ledger_view(self) -> None:
        """The episode's rows of the story's ledger, as its own file."""
        ec = self.ec
        if self.gates is None:
            return
        try:
            path = ec.store.episode_file_path(ec.story_id, ec.ep, LEDGER_VIEW, create=True)
            self.gates.ledger.episode_view(ec.ep, path)
        except (KeyError, OSError) as exc:
            self.ctx.on_log(f"⚠️ The episode's cost ledger view could not be written ({exc}).")

    def journal_failed(self, exc) -> StepFailed:
        request = exc.request or {}
        also = self.failures()
        return StepFailed(
            f"Episode {self.ec.ep}'s assets stopped: a request the provider accepted could not be journaled or "
            f"booked, so nothing else was tried. Request {request.get('request_id') or 'unknown'} (status "
            f"{request.get('status_url') or 'unknown'}, response {request.get('response_url') or 'unknown'}): "
            f"{exc}" + (f" Also failed in this run: {also}." if also else ""))

    def before_image(self, remaining) -> None:
        self.ctx.cancel.check()

        def left():
            ids = [shot["shot_id"] for shot in remaining]
            return f"the image{'s' if len(ids) > 1 else ''} of shot{'s' if len(ids) > 1 else ''} {_and(ids)}"

        try:
            self.budget.before_call(left, per_call=STORY_IMAGE_CALL_SECONDS)
        except StepFailed as exc:
            message = str(exc)
            also = "; ".join(part for part in (self.failures(), self.voice_failures()) if part)
            if also:
                message += f" Also failed in this run: {also}."
            raise voice_lines.BudgetSpent(message) from None

    def write_board(self) -> None:
        try:
            episode_common.write_storyboard(self.ec, self.storyboard, self.script, now=llm_call.utc_now())
        except schemas.SchemaError as exc:
            raise StepFailed(f"Episode {self.ec.ep}'s storyboard could not be written "
                             f"({'; '.join(exc.errors[:2])}); every image made is kept and cached: plan the "
                             "shots again, then run the assets step.") from None

    # -------------------------------------------------------------- the plan

    def check_plan(self, units) -> None:
        """Stop before the first call when the images cannot run (the DEC-117
        readiness in ``references`` mode: stop and ask) or a paid part is over
        a cap -- with the numbers (:func:`plan_refusal`)."""
        refusal = plan_refusal(self.ec, units)
        if refusal is None:
            return
        if needs_editor(units) or link_gone(units):
            self.ctx.on_log(f"✋ {refusal}")
        raise StepFailed(refusal)

    # ------------------------------------------------------------- the words

    def align_words(self, only=None) -> None:
        """Opt-in forced alignment of every voiced line whose sidecar has no
        words (``wordtiming.align``) -- of the lines *only* names, when given
        (the ones voiced after a pause); a line that cannot be aligned keeps
        the even split, said, never a failure."""
        ec, ctx = self.ec, self.ctx
        wanted = []
        for scene in self.script["scenes"]:
            for line in scene["lines"]:
                if only is not None and line["line_id"] not in only:
                    continue
                if not voice_lines.is_measured(ec, line):
                    continue
                sidecar = read_sidecar(ec, line["line_id"])
                if sidecar is not None and wordtiming.source_of(sidecar)[0] == wordtiming.EVEN_SPLIT:
                    wanted.append((line, sidecar))
        if not wanted:
            return
        transcribe = self.transcribe
        if transcribe is None:
            transcribe, reason = default_transcriber(ctx.settings_env)
            if transcribe is None:
                ctx.on_log(f"⚠️ Word alignment was asked for, but {reason}: {len(wanted)} line"
                           f"{'s keep' if len(wanted) != 1 else ' keeps'} the even split (approximate timing).")
                return
        ctx.on_log(f"🔤 Aligning the words of {len(wanted)} line{'s' if len(wanted) != 1 else ''} (STT)")
        for index, (line, sidecar) in enumerate(wanted):
            ctx.cancel.check()
            remaining = [item[0]["line_id"] for item in wanted[index:]]

            def left(ids=remaining):
                return f"the word alignment of line{'s' if len(ids) > 1 else ''} {_and(ids)}"

            try:
                self.budget.before_call(left, per_call=STORY_STT_CALL_SECONDS)
            except StepFailed as exc:
                also = "; ".join(part for part in (self.failures(), self.voice_failures()) if part)
                raise voice_lines.BudgetSpent(str(exc) + (f" Also failed in this run: {also}." if also else "")) \
                    from None
            line_id = line["line_id"]
            try:
                words, aligned_by = transcribe(line_audio_path(ec, line), language=ec.language, on_log=ctx.on_log,
                                               cancel=ctx.cancel)
            except Exception as exc:  # noqa: BLE001 - the line keeps the even split
                ctx.on_log(f"⚠️ {line_id}: the words could not be aligned ({exc}); approximate timing kept.")
                continue
            aligned = wordtiming.align(line["text"], words, line["timing"]["duration_s"])
            if aligned is None:
                ctx.on_log(f"⚠️ {line_id}: no transcribed word matched the line; approximate timing kept.")
                continue
            path = sidecar_path(ec, line_id)
            try:
                _atomic_write_json(path, wordtiming.aligned_sidecar(sidecar, aligned, aligned_by))
            except (OSError, TypeError) as exc:
                ctx.on_log(f"⚠️ {line_id}: the aligned words could not be kept ({exc}); approximate timing kept.")
                continue
            self.aligned.append(line_id)
            ctx.on_log(f"🔤 {line_id}: {len(aligned)} words aligned by {aligned_by}")

    # ------------------------------------------------------------ the images

    def make_image(self, shot, *, seed, note) -> dict:
        """One shot's image through the story's chain and the generation
        cache; returns the fields of its ``assets`` record. ``ShotFailed``
        for this shot alone; ``gencache.JournalError`` passes through."""
        ec, ctx, tools, gates = self.ec, self.ctx, self.tools, self.gates
        shot_id = shot["shot_id"]
        # Phase 8 stage B: the previous keyframe of the scene in a v2 shot's continuity slot.
        source, alone = self.continuity_for(shot)
        parts = request_parts(ec, shot, note=note, link=self.link, continuity=source[1] if source else None,
                              alone=alone)
        continuity = ({"shot_id": source[0]["shot_id"], "image_sha256": _sha256_file(source[1])}
                      if source and _sha256_file(source[1]) else None)
        kind = parts["kind"]
        if parts["missing"]:
            raise ShotFailed(f"its reference image{'s' if len(parts['missing']) > 1 else ''} "
                             f"{', '.join(parts['missing'])} {'are' if len(parts['missing']) > 1 else 'is'} not on "
                             "disk: make the images of the cast and places again, then refresh the prompts")
        if kind == gen.IMAGE_EDIT and not parts["references"]:
            raise ShotFailed("it has no reference image to send to an editor")
        try:
            # The role's chain (phase 7), built BEFORE the sticky pin below (DEC-204).
            chain = image_chain(ec, kind, gates.merged)
        except ChainError as exc:
            raise ShotFailed(f"{chain_name(ec, kind)} cannot be used: {exc}") from None
        link = self.link
        if link is not None:
            # A-087: the episode's image link alone -- never the next link.
            if self.link_gone is not None:
                raise ShotFailed(self.gone_reason())
            pinned = [candidate for candidate in chain if describe(candidate) == link][:1]
            if not pinned:
                raise self.gone(f"it is not a link of {chain_name(ec, kind)} any more")
            chain = pinned
        route = ec.story["generation_profile"]["route"]
        cache = self.cache(kind, unit="image", qty=1)
        with tempfile.TemporaryDirectory(prefix="shot-image-") as incoming:
            request = gen.GenRequest(kind=kind, prompt=parts["prompt"], negative=parts["negative"],
                                     width=SHOT_SIZE[0], height=SHOT_SIZE[1], seed=seed,
                                     references=tuple(parts["references"]), out_dir=incoming,
                                     extra={"name": f"shot_{shot_id[2:]}"})
            try:
                result, answered = gen.run_generation_chain(
                    kind, chain, request, env=gates.merged, allow_paid=gates.budget.allow_paid, route=route,
                    on_log=ctx.on_log, budget_check=gates.check, limiter=gates.limiter, adapters=tools.adapters,
                    transport=tools.transport, sleep_fn=tools.sleep_fn, time_fn=tools.time_fn, cancel=ctx.cancel,
                    cache=cache)
            except gencache.JournalError:
                raise
            except gen.NoRunnableLink as exc:
                why = sticky_link.gone_why(exc.failures, link) if link is not None else None
                if why is not None:
                    raise self.gone(why, exc.failures) from None
                reasons = [imaging.explain(kind, label, reason, chain=chain, merged=gates.merged,
                                           budget_obj=gates.budget, request=request, adapters=tools.adapters)
                           for label, reason in exc.failures]
                raise ShotFailed(f"no link of {chain_name(ec, kind)} could make it on route {route}: "
                                 f"{'; '.join(reasons) or exc}", exc.failures) from None
            except Exception as exc:  # noqa: BLE001 - an adapter's bug fails this shot, named
                raise ShotFailed(f"{type(exc).__name__}: {exc}") from None

            meta = result.meta or {}
            label = describe(answered)
            if "booked" not in meta:
                # A request with no key (never one of this step's: the seed is
                # fixed) is booked the old way, on the same ledger.
                est = _book_answer(gates, result, answered, kind)
            else:
                est = round(float((meta.get("booked") or {}).get("est_usd") or 0.0), 4) if result.paid else 0.0
            try:
                produced, ext = imaging.produced_image(result)
            except imaging.NotKept as exc:
                raise ShotFailed(f"{label}: {exc} (the call is booked)") from None
            name = image_name(shot_id, ext)
            try:
                dest = ec.store.episode_asset_path(ec.story_id, ec.ep, "shots", name, create=True)
            except KeyError:
                raise ShotFailed(f"{SHOTS_DIR}/{name} is not a real file or folder; it is never followed "
                                 "(the call is booked and cached: move it away and run the step again)") from None
            # A v2 keyframe is stored as an exact 9:16 (A6); a legacy one as it came.
            produced = v2_keyframe_source(ec.story, produced, out_dir=incoming, run=self.crop_run)
            _atomic_copy(produced, dest)
        self.drop_other_images(shot_id, ext)
        answered_seed = _seed_value(result.seed)
        record = {
            "image": f"{SHOTS_DIR}/{name}", "seed": seed if answered_seed is None else answered_seed,
            "provider": answered.provider, "model": answered.model, "consistency": parts["consistency"],
            "route": route_of(answered), "prompt_hash": parts["hash"], "est_usd": est,
            "cache_key": meta.get("cache_key"), "generated_at": llm_call.utc_now(), "note": note, "pending": None,
            "_cached": bool(meta.get("cached")), "_label": label,
        }
        if shot.get("prompt_layout"):
            # A layered (v2) shot's record of its continuity reference (phase 8
            # stage B): what was sent, never what decides "current".
            record["continuity"] = continuity
        return record

    def ledger_now(self):
        """The episode's continuity ledger (``script.ledger_of``), read once
        a run: the wardrobe sets a shot is resolved with and J2 reads."""
        if self.ledger_read is _READ:
            self.ledger_read = script_step.ledger_of(self.ec)
        return self.ledger_read

    def continuity_for(self, shot):
        """``(source, alone)`` of *shot*'s request (phase 8 stage B):
        *source* the ``(previous shot, image path)`` its continuity slot
        sends (:func:`continuity_source`); with none to send, *alone* the
        shot's ``(image_prompt, reference_images)`` resolved without the slot
        (``shots.resolve_shot``, as the storyboard resolves it), so its roles
        never name an image that is not sent -- None when it cannot be. Both
        None for a shot with no slot, or in ``prompt_only`` mode."""
        ec = self.ec
        if ec.consistency_mode == PROMPT_ONLY or continuity_slot(shot, self.link) is None:
            return None, None
        source = continuity_source(ec, self.storyboard, shot)
        if source is not None:
            return source, None
        scene = next((s for s in self.script["scenes"] if s["scene_id"] == shot["scene_id"]), None)
        if scene is None:
            return None, None
        try:
            resolved = shots_mod.resolve_shot(shots_mod.plan_of(shot, v2=True), scene=scene, entities=ec.entities,
                                              style_lock=ec.style_lock, consistency_mode=ec.consistency_mode,
                                              v2=True, ledger=self.ledger_now())
        except (KeyError, ValueError):
            return None, None
        self.ctx.on_log(f"ℹ️ Shot {shot['shot_id']}: the previous shot of its scene has no keyframe yet, so it is "
                        "asked without its continuity reference.")
        return None, (resolved["image_prompt"], resolved["reference_images"])

    def drop_other_images(self, shot_id, keep_ext) -> None:
        """The shot's image of another extension, left by an earlier take."""
        ec = self.ec
        for ext in imaging.KEPT_EXTENSIONS:
            if ext == keep_ext:
                continue
            try:
                path = ec.store.episode_asset_path(ec.story_id, ec.ep, "shots", image_name(shot_id, ext))
            except KeyError:
                continue
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def apply_image(self, shot, record) -> None:
        """*record* into the shot's ``assets`` (``approved``, ``video`` and
        ``locked`` stay), the storyboard written; its approval never moves."""
        cached, label = record.pop("_cached"), record.pop("_label")
        shot["assets"].update(record)
        self.write_board()
        self.made.append((shot["shot_id"], cached))
        if record["route"] == "local" and not cached:
            self.local_image_ran = True
        paid = " paid" if record["route"] == "paid" else ""
        how = "kept answer, no call" if cached else f"${record['est_usd']:.3f}{paid}"
        self.ctx.on_log(f"🖼 {shot['shot_id']} via {label} ({how}), seed {record['seed']}, consistency: "
                        f"{record['consistency'].replace('_', '-')}")
        self.keep_link(label)

    # ------------------------------------------------- the image link (A-087)

    def resolve_link(self, *, announce=True) -> None:
        """The episode's image link as it stands before the first image
        (:func:`episode_image_link`); with *announce*, one line saying which
        -- or the note of a legacy episode whose images mix links."""
        ec, ctx = self.ec, self.ctx
        info = episode_image_link(ec, self.storyboard, env=ctx.settings_env)
        self.link_info, self.link = info, info["link"]
        self.link_kept = info["source"] == "record"
        if not announce:
            return
        if info["mixed"]:
            ctx.on_log(mixed_note(ec, info))
        elif info["source"] == "record":
            ctx.on_log(f"🔗 Episode {ec.ep}'s shots stay on its image link {info['link']} (since {info['since']}).")
        elif info["source"] == "derived":
            ctx.on_log(f"🔗 Episode {ec.ep}'s shots stay on {info['link']}, the link every image it has was made "
                       "on.")

    def keep_link(self, label) -> None:
        """Record the link that just served an image as the episode's
        ``links.image``, unless one is recorded already or the episode is a
        legacy mix: at once in ``assets.json`` (a small read-modify-write,
        atomic, as the store writes every document), or -- no ``assets.json``
        yet -- when the step writes it at its end. Every later image is asked
        of that link alone."""
        ec, ctx = self.ec, self.ctx
        if self.link_kept or (self.link_info or {}).get("mixed"):
            return
        link = self.link or sticky_link.head_of(label, _chain_labels(ec, image_kind(ec), self.gates.merged))
        entry = sticky_link.record(link, now=llm_call.utc_now())
        self.link, self.link_kept = link, True
        ctx.on_log(f"🔗 Episode {ec.ep}'s image link is now {link}: every other shot of it is made on that link "
                   "alone.")
        try:
            doc = episode_common.read_episode(ec, ASSETS_DOC)
        except StepFailed as exc:
            self.link_pending = entry
            ctx.on_log(f"⚠️ The image link is kept for the end of the step: {exc}")
            return
        if doc is None:
            self.link_pending = entry
            return
        doc["links"] = dict(doc.get("links") or {})
        doc["links"][sticky_link.IMAGE] = entry
        try:
            ec.store.write_episode_doc(ec.story_id, ec.ep, ASSETS_DOC, doc, now=llm_call.utc_now())
        except (schemas.SchemaError, ValueError, KeyError) as exc:
            self.link_pending = entry
            ctx.on_log(f"⚠️ {ASSETS_DOC} could not record the image link now ({exc}); the step writes it at its "
                       "end.")

    def gone(self, why, failures=()) -> ShotFailed:
        """The episode's image link went away in this run (*why*): the offer
        (:func:`sticky_offer`) is made once and printed, and this shot and
        every one left fail with the same reason, calling nothing more."""
        if self.link_gone is None:
            self.link_gone = sticky_offer(self.ec, self.storyboard, self.link, why=why, env=self.ctx.settings_env,
                                          story_spent=self.gates.spent(), adapters=self.tools.adapters,
                                          before_any_call=False)
            self.ctx.on_log(f"✋ {self.link_gone}")
        return ShotFailed(self.gone_reason(), failures)

    def gone_reason(self) -> str:
        gone = self.link_gone
        return f"its image link {gone.link} cannot serve now ({gone.why}); no other link was tried"

    def shot_attempt(self, shot) -> dict:
        """One request for *shot*'s image with the seed and note it is asked
        with now (:func:`shot_seed`: the same on every attempt of the run);
        :meth:`make_image`'s record, or ``ShotFailed`` whose chain failures
        are kept in :attr:`shot_failures`."""
        ec = self.ec
        scene = next((s for s in self.script["scenes"] if s["scene_id"] == shot["scene_id"]), None)
        pending = shot["assets"].get("pending")
        note = pending.get("note") if pending else shot["assets"].get("note")
        seed = shot_seed(shot, scene, story_id=ec.story_id, ep=ec.ep, mode=ec.consistency_mode,
                         entity_docs=ec.entities)
        try:
            return self.make_image(shot, seed=seed, note=note)
        except ShotFailed as exc:
            self.shot_failures[shot["shot_id"]] = exc.failures
            raise

    def images(self) -> None:
        ec, ctx = self.ec, self.ctx
        board = self.storyboard
        todo = shots_to_make(ec, board)
        if not todo:
            ctx.on_log("🖼 Every shot has its image (or is locked): nothing to make.")
            return
        self.resolve_link()
        mode = ec.consistency_mode
        chain = chain_name(ec, gen.IMAGE if mode == PROMPT_ONLY else gen.IMAGE_EDIT)
        locked = sum(1 for shot in board["shots"] if shot["assets"].get("locked"))
        ctx.on_log(f"🖼 Making {len(todo)} shot image{'s' if len(todo) != 1 else ''} on {chain}"
                   + (f" ({locked} locked, kept)" if locked else ""))
        if mode == PROMPT_ONLY:
            ctx.on_log(f"🟡 Episode {ec.ep}'s shots: consistency: prompt-only (no reference image is sent)")
        for index, shot in enumerate(todo):
            self.before_image(todo[index:])
            shot_id = shot["shot_id"]
            try:
                record = self.shot_attempt(shot)
            except ShotFailed as exc:
                self.failed.append((f"shot {shot_id}", shot_target(ec.ep, shot_id), exc.reason))
                ctx.on_log(f"✖ Shot {shot_id} failed: {exc.reason}")
                continue
            self.apply_image(shot, record)

    # ------------------------------------------------------------ the pacing

    def held_back(self) -> list:
        """What a free tier held back in this run (:func:`rate_limited_by`):
        ``[(provider, "line" | "shot", id)]``, the lines in reading order,
        then the shots in storyboard order."""
        ec = self.ec
        items = []
        for line_id, _speaker, _reason in self.voice_failed:
            provider = rate_limited_by(self.line_failures.get(line_id))
            if provider:
                items.append((provider, "line", line_id))
        failed = {target for _what, target, _reason in self.failed}
        for shot in self.storyboard["shots"]:
            shot_id = shot["shot_id"]
            if shot_target(ec.ep, shot_id) in failed:
                provider = rate_limited_by(self.shot_failures.get(shot_id))
                if provider:
                    items.append((provider, "shot", shot_id))
        return items

    def fits(self, seconds) -> bool:
        """Whether the step budget still has *seconds* (``Budget.before_call``'s rule)."""
        try:
            self.budget.before_call(lambda: "", per_call=seconds)
        except StepFailed:
            return False
        return True

    def retry_line(self, gates, line_id) -> str:
        """*line_id* asked again on its pinned voice alone (``measure_line``):
        ``done``, ``limited`` (held back again) or ``failed`` (for another
        reason, now its failure); its place among the failures is kept."""
        line = next(ln for scene in self.script["scenes"] for ln in scene["lines"] if ln["line_id"] == line_id)
        index = next(i for i, entry in enumerate(self.voice_failed) if entry[0] == line_id)
        del self.voice_failed[index]
        self.line_failures.pop(line_id, None)
        before = len(self.voice_failed)
        self.measure_line(gates, line)
        if len(self.voice_failed) == before:
            return _DONE
        self.voice_failed.insert(index, self.voice_failed.pop())
        return _LIMITED if rate_limited_by(self.line_failures.get(line_id)) else _FAILED

    def retry_shot(self, shot_id) -> str:
        """*shot_id*'s image asked again with its request (:meth:`shot_attempt`):
        ``done``, ``limited`` or ``failed``, as :meth:`retry_line`."""
        ec, ctx = self.ec, self.ctx
        shot = next(s for s in self.storyboard["shots"] if s["shot_id"] == shot_id)
        target = shot_target(ec.ep, shot_id)
        index = next(i for i, entry in enumerate(self.failed) if entry[1] == target)
        try:
            record = self.shot_attempt(shot)
        except ShotFailed as exc:
            self.failed[index] = (f"shot {shot_id}", target, exc.reason)
            ctx.on_log(f"✖ Shot {shot_id} failed: {exc.reason}")
            return _LIMITED if rate_limited_by(exc.failures) else _FAILED
        del self.failed[index]
        self.shot_failures.pop(shot_id, None)
        self.apply_image(shot, record)
        return _DONE

    def pace(self, gates) -> list:
        """The rounds over what a free tier held back (module docstring):
        before each, a :data:`RATE_LIMIT_PAUSE_S` pause (cancel-aware) that
        the budget fits together with the call after it; then each provider's
        items in order until it holds one back again. A provider whose round
        makes no progress is given up. Returns the lines voiced here."""
        ctx = self.ctx
        queues = {}
        for provider, kind, item_id in self.held_back():
            queues.setdefault(provider, []).append((kind, item_id))
        sleep = ctx.cancel.sleeper(self.tools.sleep_fn)
        voiced = []
        while queues:
            waiting = [item for items in queues.values() for item in items]
            if not self.fits(RATE_LIMIT_PAUSE_S + _CALL_SECONDS[waiting[0][0]]):
                self.stop_pacing(waiting, f"a {RATE_LIMIT_PAUSE_S} s pause and the call after it")
                return voiced
            ctx.on_log(f"⏳ {_and(queues)} rate-limited: waiting {RATE_LIMIT_PAUSE_S} s before retrying "
                       f"{_counted(waiting)}")
            sleep(RATE_LIMIT_PAUSE_S)
            for provider in list(queues):
                items, progress = queues[provider], False
                while items:
                    kind, item_id = items[0]
                    if not self.fits(_CALL_SECONDS[kind]):
                        self.stop_pacing([item for rest in queues.values() for item in rest], "another call")
                        return voiced
                    ctx.cancel.check()
                    outcome = self.retry_line(gates, item_id) if kind == "line" else self.retry_shot(item_id)
                    if outcome == _LIMITED:
                        break
                    items.pop(0)
                    if outcome == _DONE:
                        progress = True
                        if kind == "line":
                            voiced.append(item_id)
                if items and not progress:
                    ctx.on_log(f"⏳ {provider} is still rate-limited after a {RATE_LIMIT_PAUSE_S} s pause: "
                               f"{_counted(items)} left as failed.")
                if not items or not progress:
                    del queues[provider]
        return voiced

    def stop_pacing(self, waiting, what) -> None:
        """No pause is started that the step budget cannot fit: said, with
        what is left (each keeps its failure and regenerate target)."""
        budget = self.budget
        self.ctx.on_log(f"⏳ Not waiting again: the step's {int(budget.limit // 60)}-minute budget cannot fit "
                        f"{what} ({budget.elapsed() / 60:.1f} min used). Left: {_left(waiting)}.")

    # ------------------------------------------------------------- the clips

    def also_failed(self) -> str:
        """`` Also failed in this run: ...`` for a stop's message, or ''."""
        also = "; ".join(part for part in (self.failures(), self.voice_failures()) if part)
        return f" Also failed in this run: {also}." if also else ""

    def before_clip(self, remaining) -> None:
        """The cancel token, then the step budget: a clip starts only while
        its poll (:data:`STORY_CLIP_CALL_SECONDS`) still fits; *remaining* are
        the shots whose clips are left."""
        self.ctx.cancel.check()

        def left():
            many = len(remaining) > 1
            return f"the clip{'s' if many else ''} of shot{'s' if many else ''} {_and(remaining)}"

        try:
            self.budget.before_call(left, per_call=STORY_CLIP_CALL_SECONDS)
        except StepFailed as exc:
            raise voice_lines.BudgetSpent(str(exc) + self.also_failed()) from None

    def animate_clips(self, doc) -> dict:
        """The video phase (module docstring, 6.): the plan derived again
        once, refused when it moved since the step's check, then each of its
        clips that is not current, in plan order, on its one link. Returns
        ``assets.json`` as it stands after (``links.video`` recorded)."""
        ec, ctx, gates = self.ec, self.ctx, self.gates
        units = asset_units(ec, self.script, self.storyboard, env=ctx.settings_env, adapters=self.tools.adapters,
                            probe_local=True, transport=self.tools.transport, ledger=gates.ledger, animate=True)
        video = units["video"]
        moved = plan_moved(ec, self.planned_video, video)
        if moved is not None:
            raise StepFailed(moved + self.also_failed())
        if not video["ready"]:
            raise StepFailed(f"Episode {ec.ep}'s clips cannot be made now: {video['message'].strip()} No clip was "
                             "asked; everything else made in this run is kept: fix that and run the assets step "
                             "again." + self.also_failed())
        self.video.update(link=video["link"], route=video["route_class"])
        if not video["plan"]:
            ctx.on_log(f"🎬 {video['message'] or 'No clip is planned.'}")
            return doc
        self.video_link_kept = sticky_link.recorded(doc, sticky_link.VIDEO) is not None
        image_link = recorded_image_link(doc)
        tier = clips.tier_of(ec)
        by_id = {shot["shot_id"]: shot for shot in self.storyboard["shots"]}
        todo = []
        for row in video["plan"]:
            shot = by_id[row["shot_id"]]
            state = clips.clip_state(ec, shot, self.script, link=video["link"], tier=tier,
                                     flags=clips.shot_flags(shot, doc),
                                     image_sha=_sha256_file(shot_image_path(ec, shot)))
            if state == "current":
                self.video["reused"] += 1
            else:
                todo.append(row)
        if not todo:
            ctx.on_log(f"🎬 Every planned shot has its current clip on {video['link']}: no clip to make.")
            return doc
        seconds = sum(int(row["clip_s"]) for row in todo)
        price = f", est ${sum(row['est_usd'] for row in todo):.3f} paid" if video["route_class"] == "paid" else ""
        kept = f" ({self.video['reused']} current, kept)" if self.video["reused"] else ""
        ctx.on_log(f"🎬 Animating {len(todo)} shot{'s' if len(todo) != 1 else ''} ({seconds} s) on "
                   f"{video['link']}{price}{kept}")
        if video["route_class"] == "local":
            self.free_comfyui()
        for index, row in enumerate(todo):
            self.clip_todo = [item["shot_id"] for item in todo[index:]]
            shot = by_id[row["shot_id"]]
            self.before_clip(self.clip_todo)
            try:
                record, info = self.make_clip(shot, video=video, clip_s=row["clip_s"], est_usd=row["est_usd"],
                                              seed=clip_seed(shot, story_id=ec.story_id, ep=ec.ep),
                                              note=clip_note(shot), flags=clips.shot_flags(shot, doc), tier=tier,
                                              image_link=image_link)
            except ClipFailed as exc:
                self.fail_clip(shot, exc)
                continue
            self.apply_clip(shot, record, info, video=video)
        self.clip_todo = []
        return episode_common.read_episode(ec, ASSETS_DOC) or doc

    def make_clip(self, shot, *, video, clip_s, est_usd, seed, note, flags, tier, image_link):
        """One clip of *shot* on the plan's one link (*video*: ``asset_units``'
        video part -- its ``link``, ``route_class``, ``template`` and
        ``profile``) through the story's generation cache, the gates of every
        paid call and the free-tier limiter; ``(record, info)``: the shot's
        ``assets.clip``, and what the call was (``cached``, ``resumed``,
        ``fresh``, ``wall_s``, ``label``, ``seed``). :class:`ClipFailed` for
        this shot alone -- never another link; ``gencache.JournalError``
        passes through."""
        ec, ctx, tools, gates = self.ec, self.ctx, self.tools, self.gates
        shot_id = shot["shot_id"]
        link = video["link"]
        image_path = shot_image_path(ec, shot)
        image_sha = (_sha256_file(image_path) if image_path
                     else _canonical_sha256({"missing": shot["assets"].get("image") or shot_id}))
        record = {"state": "failed", "link": link, "route": video["route_class"], "clip_s": int(clip_s),
                  "est_usd": round(float(est_usd or 0.0), 4), "prompt_hash": None, "image_sha256": image_sha,
                  "cache_key": None, "generated_at": None, "note": note}
        try:
            parts = clips.clip_request_parts(ec, shot, self.script, tier=tier, flags=flags, note=note)
        except (KeyError, ValueError) as exc:
            record["prompt_hash"] = _canonical_sha256({"unusable": str(exc)})
            raise ClipFailed(f"its video prompt cannot be built ({exc}); no clip was asked", record=record) from None
        record["prompt_hash"] = parts["hash"]
        template = video.get("template")

        def fail(reason, *, still=False):
            return ClipFailed(reason, record=record, still=still)

        problem = keyframe_problem(ec, shot, link=image_link)
        if problem:
            raise fail(f"its {problem}; no clip was asked")
        if self.video_gone is not None:
            raise fail(self.video_gone_reason())
        try:
            chain = gen.chain_from_env(gen.VIDEO, gates.merged)
        except ChainError as exc:
            raise fail(f"{gen.ENV_NAMES[gen.VIDEO]} cannot be used: {exc}") from None
        # The plan's link alone (A-087): never the next link of the chain.
        pinned = [candidate for candidate in chain if describe(candidate) == link][:1]
        if not pinned:
            raise self.clip_gone(f"it is not a link of {gen.ENV_NAMES[gen.VIDEO]} any more", record)
        route = ec.story["generation_profile"]["route"]
        cache = self.cache(gen.VIDEO, unit="second", qty=int(clip_s))
        with tempfile.TemporaryDirectory(prefix="shot-clip-") as incoming:
            _parts, request = clip_request(ec, shot, self.script, link=link, template=template, clip_s=clip_s,
                                           seed=seed, note=note, flags=flags, tier=tier, out_dir=incoming)
            record["cache_key"] = gencache.request_key(gen.VIDEO, link, request)
            started = tools.time_fn()
            try:
                result, answered = gen.run_generation_chain(
                    gen.VIDEO, pinned, request, env=gates.merged, allow_paid=gates.budget.allow_paid, route=route,
                    on_log=ctx.on_log, budget_check=gates.check, limiter=gates.limiter, adapters=tools.adapters,
                    transport=tools.transport, sleep_fn=tools.sleep_fn, time_fn=tools.time_fn, cancel=ctx.cancel,
                    cache=cache)
            except gencache.JournalError:
                raise
            except gen.NoRunnableLink as exc:
                why = sticky_link.gone_why(exc.failures, link)
                if why is not None:
                    raise self.clip_gone(why, record) from None
                held = [str(reason) for _label, reason in exc.failures if "kept for the next run" in str(reason)]
                if held:
                    raise fail(f"still generating on {link} ({held[0]}): {CONTINUE_ONLY}", still=True) from None
                reasons = "; ".join(f"{label}: {reason}" for label, reason in exc.failures) or str(exc)
                raise fail(f"{reasons}; no other link was tried") from None
            except Exception as exc:  # noqa: BLE001 - an adapter's bug fails this clip, named
                raise fail(f"{type(exc).__name__}: {exc}; no other link was tried") from None
            wall_s = tools.time_fn() - started
            meta = result.meta or {}
            label = describe(answered)
            if "booked" not in meta:
                # Never one of this phase's requests (a clip always has a key):
                # booked the old way, on the same ledger, in seconds.
                est = _book_answer(gates, result, answered, gen.VIDEO, unit="second", qty=int(clip_s))
            else:
                est = round(float((meta.get("booked") or {}).get("est_usd") or 0.0), 4) if result.paid else 0.0
            produced = next((str(path) for path in result.paths if str(path).lower().endswith(".mp4")), None)
            if produced is None:
                raise fail(f"{label} answered without an .mp4 clip (the call is booked)")
            try:
                dest = ec.store.episode_asset_path(ec.story_id, ec.ep, clips.CLIPS_KIND, clips.clip_name(shot_id),
                                                   create=True)
            except KeyError:
                raise fail(f"{clips.clip_rel(shot_id)} is not a real file or folder; it is never followed (the call "
                           "is booked and cached: move it away and run the step again)") from None
            _atomic_copy(produced, dest)
        cached, resumed = bool(meta.get("cached")), bool(meta.get("resumed"))
        record.update(state="current", est_usd=est, cache_key=meta.get("cache_key") or record["cache_key"],
                      generated_at=llm_call.utc_now(), note=note, pending=None)
        info = {"cached": cached, "resumed": resumed, "fresh": not cached and not resumed, "wall_s": wall_s,
                "label": label, "seed": seed}
        return record, info

    def apply_clip(self, shot, record, info, *, video) -> None:
        """*record* as the shot's ``assets.clip`` and its file as
        ``assets.video`` -- next to its image, the storyboard written with
        its approval kept -- the link kept as the episode's video link, the
        wall time measured (a clip made here, not a kept or resumed one)."""
        ec, ctx = self.ec, self.ctx
        shot_id = shot["shot_id"]
        shot["assets"]["clip"] = record
        shot["assets"]["video"] = clips.clip_rel(shot_id)
        self.write_board()
        self.keep_video_link(record["link"])
        summary = self.video
        if info["cached"]:
            summary["reused"] += 1
            how = "kept answer, no call"
        else:
            summary["made"] += 1
            if info["resumed"]:
                how = "collected, booked when it was sent"
            elif record["route"] == "local":
                how = "$0.000, on your own hardware"
            else:
                how = f"${record['est_usd']:.3f} paid"
            if info["fresh"]:
                summary["seconds"] += int(record["clip_s"])
                summary["usd"] = round(summary["usd"] + float(record["est_usd"]), 4)
        if info["fresh"] and info["wall_s"] > 0:
            key = gen_timings.timing_key(video["link"], video.get("template"), video.get("profile"))
            try:
                gen_timings.record(key, info["wall_s"], int(record["clip_s"]))
            except (OSError, ValueError) as exc:
                ctx.on_log(f"⚠️ The clip's wall time could not be kept ({exc}); the ETA does not count it.")
        ctx.on_log(f"🎬 {shot_id} via {info['label']} ({how}), {record['clip_s']} s, seed {info['seed']}")

    def fail_clip(self, shot, exc) -> None:
        """The shot's clip ``failed`` with the reason (a re-animate's
        ``pending`` kept, so a retry asks the same clip), its ``assets.video``
        cleared, the storyboard written; the failure named with its
        regenerate target -- none for a clip still generating (``exc.still``):
        only Continue collects it without buying it again."""
        ec, ctx = self.ec, self.ctx
        shot_id = shot["shot_id"]
        old = shot["assets"].get("clip") or {}
        reason = exc.reason if len(exc.reason) <= 1000 else exc.reason[:997] + "..."
        record = dict(exc.record, state="failed", reason=reason)
        record.pop("pending", None)
        if old.get("pending"):
            record["pending"] = old["pending"]
        shot["assets"]["clip"] = record
        shot["assets"]["video"] = None
        self.write_board()
        self.failed.append((f"clip of shot {shot_id}", None if exc.still else clip_target(ec.ep, shot_id),
                            exc.reason))
        if self.video is not None:
            self.video["failed"].append({"shot_id": shot_id, "reason": exc.reason})
        ctx.on_log(f"✖ Clip {shot_id} failed: {exc.reason}")

    def keep_video_link(self, link) -> None:
        """Record *link*, which just served a clip, as the episode's
        ``links.video`` unless one is recorded (A-087): every later clip is
        asked of it alone. ``assets.json`` is read, changed and written at
        once (a minimal one is started when there is none)."""
        if self.video_link_kept:
            return
        ec, ctx = self.ec, self.ctx
        now = llm_call.utc_now()
        try:
            doc = episode_common.read_episode(ec, ASSETS_DOC)
        except StepFailed as exc:
            ctx.on_log(f"⚠️ The video link could not be recorded ({exc}).")
            return
        if doc is None:
            doc = _minimal_assets_doc(ec, now)
        if sticky_link.recorded(doc, sticky_link.VIDEO) is None:
            doc["links"] = dict(doc.get("links") or {})
            doc["links"][sticky_link.VIDEO] = sticky_link.record(link, now=now)
            try:
                ec.store.write_episode_doc(ec.story_id, ec.ep, ASSETS_DOC, doc, now=now)
            except (schemas.SchemaError, ValueError, KeyError) as exc:
                ctx.on_log(f"⚠️ {ASSETS_DOC} could not record the video link now ({exc}).")
                return
            ctx.on_log(f"🔗 Episode {ec.ep}'s video link is now {link}: every other clip of it is made on that link "
                       "alone.")
        self.video_link_kept = True

    def clip_gone(self, why, record) -> ClipFailed:
        """The plan's video link went away in this run (*why*): the offer
        (:func:`video_offer`) is made once and printed, and this clip and
        every one left fail with the same reason, calling nothing more."""
        if self.video_gone is None:
            self.video_gone = video_offer(self.ec, self.storyboard, record["link"], why=why,
                                          env=self.ctx.settings_env, adapters=self.tools.adapters,
                                          todo=list(self.clip_todo), paid=record["route"] == "paid",
                                          before_any_call=False)
            self.ctx.on_log(f"✋ {self.video_gone}")
        return ClipFailed(self.video_gone_reason(), record=record)

    def video_gone_reason(self) -> str:
        gone = self.video_gone
        return f"its video link {gone.link} cannot serve now ({gone.why}); no other link was tried"

    def free_comfyui(self) -> None:
        """``POST /free`` once before the first clip on a local ComfyUI whose
        card has at most :data:`FREE_VRAM_GB` GB, when a shot image ran on it
        in this run: the image model makes room for the video model. The card
        is read from ``/system_stats``; a failure is said, never fatal."""
        if not self.local_image_ran:
            return
        ctx = self.ctx
        client = local_comfyui.ComfyUIClient(gen.local_url("comfyui", self.gates.merged),
                                             transport=self.tools.transport)
        try:
            _profile, vram, _gpu = hardware.profile_from_system_stats(client.system_stats(),
                                                                      in_container=gen.in_container())
            if not vram or float(vram) > FREE_VRAM_GB:
                return
            client.free()
        except Exception as exc:  # noqa: BLE001 - ComfyUI unloads on its own when it must; the clips go on
            ctx.on_log(f"⚠️ ComfyUI /free could not be asked ({type(exc).__name__}: {exc}); the clips go on.")
            return
        ctx.on_log(f"🧹 ComfyUI /free before the first clip: the image model is unloaded for the video model "
                   f"({float(vram):g} GB card).")

    # ---------------------------------------------------------- sfx, bgm, doc

    def audio_entries(self) -> tuple:
        """``(sfx, bgm)`` of ``assets.json`` from the script as it is timed now."""
        ec, ctx, script, board = self.ec, self.ctx, self.script, self.storyboard
        timing_result, _scenes = timing.episode_pass(script, ec.template, ec.language, style_lock=ec.style_lock,
                                                     storyboard=board, whole_frames=timing.board_whole_frames(board))
        starts = timing.scene_starts(script, timing_result, ec.template, storyboard=board)
        offsets = timing.line_offsets(script, timing_result, ec.template, storyboard=board)
        pack = ec.style_lock["audio"]["sfx_pack"]
        sfx = []
        for scene in script["scenes"]:
            sid = scene["scene_id"]
            for cue in scene["sfx_cues"]:
                at = cue["at"]
                offset = starts[sid] if at == "start" else offsets.get(at, (starts[sid], None))[0]
                resolved = audio_assets.resolve_sfx(pack, cue["cue"])
                sfx.append({
                    "scene_id": sid, "at": at, "cue": cue["cue"], "pack": pack,
                    "file": f"assets/sfx/{resolved['file']}" if resolved else None,
                    "offset_s": round(max(0.0, float(offset)), 3),
                    "state": "resolved" if resolved else "missing",
                })
                if resolved is None:
                    ctx.on_log(f"⚠️ SFX cue {cue['cue']!r} ({sid}, at {at}) is not in the {pack} pack: it is "
                               "skipped at render.")

        weights, scenes = {}, []
        for scene in script["scenes"]:
            seconds = float(timing_result["scenes"][scene["scene_id"]]["duration_s"])
            weights[scene["emotion"]] = weights.get(scene["emotion"], 0.0) + seconds
            scenes.append({"emotion": scene["emotion"], "duration_s": seconds})
        dominant = audio_assets.dominant_emotion(scenes)
        mood = audio_assets.bgm_mood(ec.style_lock["audio"], dominant) if dominant else None
        if dominant is None or not mood:
            return sfx, None
        bgm = {"mood": mood, "dominant_emotion": dominant,
               "weights_s": {emotion: round(seconds, 3) for emotion, seconds in weights.items()},
               "file": None, "sha256": None, "licence": None}
        try:
            track = audio_assets.pick_track(audio_assets.load_bgm_index(), mood, ec.story_id, ec.ep)
        except (OSError, ValueError) as exc:
            track = None
            ctx.on_log(f"⚠️ The BGM index cannot be read ({exc}): the episode has no music bed.")
        sha = _sha256_file(track.get("abs_path")) if track else None
        if track and sha and track.get("licence"):
            bgm.update(file=f"assets/bgm/{track['file']}", sha256=sha, licence=str(track["licence"])[:200])
        else:
            ctx.on_log(f"⚠️ No BGM track carries the mood {mood!r}: the episode has no music bed.")
        return sfx, bgm

    def line_entries(self, previous=None) -> dict:
        """``{line_id: {words_source, aligned_by?, take?, pending?}}`` of every
        voiced line. *previous* (the ``lines`` of the ``assets.json`` written
        before, phase 5 stage 7) hands on each line's voice regenerate
        records: its ``pending`` take -- a line not voiced yet keeps an entry
        holding it alone -- and its ``take`` while the audio on disk is still
        the one that take made (its sha256); a take of another file no
        longer describes the line and is dropped."""
        ec, lines = self.ec, {}
        kept = previous or {}
        for scene in self.script["scenes"]:
            for line in scene["lines"]:
                line_id = line["line_id"]
                old = kept.get(line_id) or {}
                entry = {}
                if voice_lines.is_measured(ec, line):
                    source, aligned_by = wordtiming.source_of(read_sidecar(ec, line_id))
                    entry["words_source"] = source
                    if source == wordtiming.ALIGNMENT:
                        entry["aligned_by"] = str(aligned_by or "stt")[:120]
                    take = old.get("take")
                    if take and take.get("audio_sha256") == _sha256_file(line_audio_path(ec, line)):
                        entry["take"] = take
                if old.get("pending"):
                    entry["pending"] = old["pending"]
                if entry:
                    lines[line_id] = entry
        return lines

    def write_assets_doc(self) -> dict:
        ec = self.ec
        previous = episode_common.read_episode(ec, ASSETS_DOC)
        sfx, bgm = self.audio_entries()
        now = llm_call.utc_now()
        doc = {
            "$schema": schemas.EPISODE_ASSETS_SCHEMA_NAME, "ep": ec.ep,
            "lines": self.line_entries(previous["lines"] if previous else None),
            "sfx": sfx, "bgm": bgm,
        }
        # The episode's links (A-087): carried as they are, with the image
        # link this run recorded while there was no document to hold it.
        links = dict((previous or {}).get("links") or {})
        if self.link_pending is not None:
            links[sticky_link.IMAGE] = self.link_pending
        if links:
            doc["links"] = links
        # The user's per-shot overrides (phase 6 stage 7): carried as they are.
        if (previous or {}).get("shots"):
            doc["shots"] = previous["shots"]
        # A v2 episode's keyframe verdicts and approval (phase 7 stage 6b):
        # carried as they are -- the approval goes stale by its fingerprint,
        # never cleared here.
        for key in (judge.KEYFRAME_VERDICTS, judge.KEYFRAMES_APPROVED):
            if (previous or {}).get(key):
                doc[key] = previous[key]
        doc.update({
            # Kept: an approval is derived stale by the fingerprint, never cleared here.
            "approved": previous["approved"] if previous else None,
            "created_at": previous["created_at"] if previous else now, "updated_at": now,
        })
        try:
            return ec.store.write_episode_doc(ec.story_id, ec.ep, ASSETS_DOC, doc, now=now)
        except (schemas.SchemaError, ValueError, KeyError) as exc:
            raise StepFailed(f"Episode {ec.ep}'s {ASSETS_DOC} could not be written ({exc}).") from None

    # ---------------------------------------------------- the keyframe judge

    def before_vision(self, remaining) -> None:
        """The cancel token, then the step budget: a J2 call starts only while
        it still fits (``judge.STORY_VISION_CALL_SECONDS``); *remaining* are
        the shots not judged yet."""
        self.ctx.cancel.check()

        def left():
            many = len(remaining) > 1
            return f"the keyframe check (J2) of shot{'s' if many else ''} {_and(remaining)}"

        try:
            self.budget.before_call(left, per_call=judge.STORY_VISION_CALL_SECONDS)
        except StepFailed as exc:
            raise voice_lines.BudgetSpent(str(exc) + self.also_failed()) from None

    def judge_keyframes(self, doc) -> dict:
        """J2 on a v2 episode (module docstring): every current keyframe
        without a current verdict, ``keyframe_verdicts`` written after each
        new one (a stop keeps what was judged) and once more at the end when
        it moved. Returns ``assets.json`` as it stands after."""
        ec = self.ec
        written = {"doc": doc}

        def write(verdicts):
            current = written["doc"]
            if verdicts == (current.get(judge.KEYFRAME_VERDICTS) or {}):
                return
            new = copy.deepcopy(current)
            if verdicts:
                new[judge.KEYFRAME_VERDICTS] = verdicts
            else:
                new.pop(judge.KEYFRAME_VERDICTS, None)
            try:
                written["doc"] = ec.store.write_episode_doc(ec.story_id, ec.ep, ASSETS_DOC, new,
                                                            now=llm_call.utc_now())
            except (schemas.SchemaError, ValueError, KeyError) as exc:
                raise StepFailed(f"Episode {ec.ep}'s {ASSETS_DOC} could not be written ({exc}).") from None

        verdicts, summary = judge.check_keyframes(
            self.ctx, ec, keyframe_items(ec, self.storyboard, doc), doc.get(judge.KEYFRAME_VERDICTS) or {},
            env=self.ctx.settings_env, ledger=self.gates.ledger, step=STEP, before_call=self.before_vision,
            on_verdict=write, adapters=self.tools.adapters, transport=self.tools.transport,
            context=keyframe_context(ec, self.storyboard, ledger=self.ledger_now()))
        self.keyframe_check = summary
        write(verdicts)
        return written["doc"]

    # ------------------------------------------------------------------- run

    def run(self) -> dict:
        ec, ctx = self.ec, self.ctx
        self.script, self.storyboard = require_approved(ec)
        align = bool((ctx.params or {}).get(ALIGN_PARAM))
        animate = animate_param(ctx.params)
        gates = self.open_asset_gates()
        try:
            units = asset_units(ec, self.script, self.storyboard, env=ctx.settings_env, align_words=align,
                                adapters=self.tools.adapters, probe_local=True, transport=self.tools.transport,
                                ledger=gates.ledger, animate=animate)
            self.check_plan(units)
            video = units.get("video")
            if video is not None:
                # Tier >= 2 (a tier-1 plan has no video part: nothing below runs).
                self.video = _video_summary(video, animate=animate)
                if animate and video.get("hold"):
                    # Phase 7 stage 6b (RC-Q3): a v2 episode's clips wait for its keyframes' approval.
                    self.video.update(planned=0, hold=video["hold"])
                    ctx.on_log(f"🎬 No clip in this run: {video['hold']}.")
                elif animate:
                    self.planned_video = video
            ctx.cancel.check()
            try:
                self.measure(gates)
                if align:
                    self.align_words()
                self.images()
                voiced = self.pace(gates)
                if align and voiced:
                    self.align_words(only=set(voiced))
            except gencache.JournalError as exc:
                raise self.journal_failed(exc) from None
            doc = self.write_assets_doc()
            if media_policy.is_v2(ec.story):
                doc = self.judge_keyframes(doc)
            if self.planned_video is not None:
                # Last: the keyframes are final, the SFX/BGM and assets.json written.
                try:
                    doc = self.animate_clips(doc)
                except gencache.JournalError as exc:
                    raise self.journal_failed(exc) from None
        finally:
            self.write_ledger_view()
        return self.finish(doc)

    def finish(self, doc) -> dict:
        ec, ctx, board = self.ec, self.ctx, self.storyboard
        link = recorded_image_link(doc)
        states = {shot["shot_id"]: shot_state(ec, shot, link=link) for shot in board["shots"]}
        unvoiced = [line["line_id"] for scene in self.script["scenes"] for line in scene["lines"]
                    if not voice_lines.is_measured(ec, line)]
        missing_cues = sum(1 for cue in doc["sfx"] if cue["state"] == "missing")
        failures = [{"what": what, "target": target, "reason": reason} for what, target, reason in self.failed]
        failures += [{"what": f"line {line_id}", "target": line_target(ec.ep, line_id),
                      "reason": f"{voice_lines.speaker_name(ec, speaker)}: {reason.rstrip('.')}"}
                     for line_id, speaker, reason in self.voice_failed]
        ctx.on_log(episode_common.timing_line(self.script))
        if failures:
            targets = [item["target"] for item in failures if item["target"]]
            advice = f"regenerate {entities.quoted_list(targets)} or run the assets step again" if targets else ""
            held = [item["what"] for item in failures if not item["target"]]
            if held:
                # A clip still generating (phase 6 stage 8): Continue alone, never its regenerate target.
                advice += (("; for " if advice else "") + f"{_and(held)}, still generating: {CONTINUE_ONLY}")
            message = (f"⚠️ Episode {ec.ep}'s assets are not complete: "
                       + "; ".join(f"{item['what']} failed ({item['reason']})" for item in failures)
                       + f". To finish them, {advice}.")
            if self.voice_failed:
                message += f" {self.voice_message()}"
            if self.link_gone is not None:
                message += f" {self.link_gone}"
            if self.video_gone is not None:
                message += f" {self.video_gone}"
            ctx.on_log(message)
        else:
            ctx.on_log(f"✅ Episode {ec.ep}'s assets are ready for your approval.")
        result = {
            "ep": ec.ep,
            "shots": {"total": len(board["shots"]), "made": sum(1 for _sid, cached in self.made if not cached),
                      "cached": sum(1 for _sid, cached in self.made if cached),
                      "locked": sum(1 for shot in board["shots"] if shot["assets"].get("locked")),
                      "states": states},
            "lines": {"measured": self.measured, "unvoiced": unvoiced},
            "aligned": list(self.aligned),
            "sfx": {"resolved": len(doc["sfx"]) - missing_cues, "missing": missing_cues},
            "bgm": None if doc["bgm"] is None else doc["bgm"]["mood"],
            "failed": failures,
            "complete": not unvoiced and all(shot["assets"].get("locked") or states[shot["shot_id"]] == "current"
                                             for shot in board["shots"]),
            "fingerprint": current_fingerprint(ec, board, self.script, doc),
        }
        info = self.link_info or {}
        if self.link is not None or info.get("mixed"):
            # A-087: the link the images were asked of, a legacy mix, or the
            # offer when the link went away in this run.
            result["image_link"] = {"link": self.link, "mixed": list(info.get("mixed") or []),
                                    "gone": self.link_gone.as_dict() if self.link_gone else None}
        if self.video is not None:
            # Tier >= 2 (phase 6 stage 8): what the video phase did.
            result["video"] = dict(self.video, gone=self.video_gone.as_dict() if self.video_gone else None)
        if self.keyframe_check is not None:
            # A v2 story (phase 7 stage 6b): what J2 did, and where the keyframes' approval stands.
            result["keyframes"] = dict(self.keyframe_check, approval=keyframes_state(ec, board, doc))
        return result


def _book_answer(gates, result, answered, kind, *, unit="image", qty=1) -> float:
    """Book an answer no journal booked (``imaging.book``'s row, with the
    episode) on the gates' ledger -- one image, or a clip's *qty* seconds;
    returns what it cost."""
    paid = bool(result.paid)
    est = float(result.est_cost) if paid else 0.0
    gates.ledger.append(step=STEP, provider=answered.provider, model=gating.api_model_id(kind, answered),
                        unit=unit, qty=qty, est_usd=est, paid=paid, ep=gates.ep)
    if paid and result.est_cost > 0:
        budget_mod.record(result.est_cost)
    return round(est, 4)


def run(ctx, *, adapters=None, transport=None, time_fn=time.monotonic, sleep_fn=time.sleep, transcribe=None,
        budget=None) -> dict:
    """The step (module docstring). *adapters*, *transport*, *time_fn*,
    *sleep_fn* and *transcribe* (the STT stand-in: ``(path, *, language,
    on_log, cancel) -> (words, aligned_by)``) are for tests. *budget*: an
    ``episode_common.Budget`` shared with a caller running this step inside
    its own (the fast track); None gives the step its own."""
    ec = episode_common.load_episode_context(ctx)
    # Made from an approved script and storyboard, which met the memory gate
    # when they were written: this step never meets it (plan 11 stage 4).
    episode_common.check_episode_preconditions(ctx, ec, require_memory=False)
    ctx.cancel.check()
    tools = entities.Tools(time_fn=time_fn, sleep_fn=sleep_fn, adapters=adapters, transport=transport)
    return _Assets(ctx, ec, tools=tools, transcribe=transcribe, budget=budget).run()


# ---------------------------------------------------------------- regenerate

def _noted(note) -> str:
    return f" (note: {note})" if note else ""


def regenerate_shot_image(ctx, ec, target, shot_id, note, *, tools, refuse) -> dict:
    """``shot:<ep>:<shot_id>`` (kind ``shot_image``): that shot's image again,
    with *note* at the prompt's tail and a fresh seed (DEC-124) -- persisted
    as the shot's ``pending{seed, note, requested_at}`` **before** the call,
    so a retry (this regenerate again with the same note, or the assets step)
    asks for the same image and the generation cache serves or resumes it
    (DEC-154). A locked shot is refused ("unlock it first"). *refuse(reason)*
    is the caller's ``StepFailed`` builder."""
    host = _Assets(ctx, ec, tools=tools)
    try:
        host.script, host.storyboard = require_approved(ec)
    except StepFailed as exc:
        raise refuse(str(exc)) from None
    board = host.storyboard
    shot = next((s for s in board["shots"] if s["shot_id"] == shot_id), None)
    if shot is None:
        raise refuse(f"episode {ec.ep}'s storyboard has no shot {shot_id!r} (it has sh01 to "
                     f"sh{len(board['shots']):02d}).")
    if shot["assets"].get("locked"):
        raise refuse(f"shot {shot_id} is locked: unlock it first.")
    if note is not None and len(note) > schemas.REGENERATE_NOTE_MAX:
        raise refuse(f"a note is at most {schemas.REGENERATE_NOTE_MAX} characters ({len(note)} given).")
    gates = host.open_asset_gates()
    try:
        # A-087: the episode's image link alone; one gone for now stops here,
        # before any call, with its offer.
        host.resolve_link()
        if host.link is not None:
            quote = image_quote(ec, 1, env=ctx.settings_env, story_spent=gates.spent(), adapters=tools.adapters,
                                probe_local=True, transport=tools.transport, storyboard=board,
                                link_info=host.link_info)
            if quote["sticky"]["gone"]:
                raise refuse(quote["sticky"]["gone"]["message"])
        elif ec.consistency_mode == REFERENCES:
            readiness = refimages.edit_readiness(ec.story, env=ctx.settings_env, qty=1,
                                                 story_spent=gates.spent(), adapters=tools.adapters,
                                                 size=SHOT_SIZE, probe_local=True, transport=tools.transport,
                                                 role=KEYFRAME_ROLE)
            if not readiness["ready"]:
                reasons = [f"{row['link']}: {row['reason']}" for row in readiness["links"]] or [readiness["message"]]
                raise refuse(str(refimages.NeedsEditor(reasons, readiness, subject=f"Shot {shot_id}",
                                                       story=ec.story)))
        pending = shot["assets"].get("pending")
        # The same request asked again (its call did not answer) keeps its
        # seed, so a paid request the provider holds is resumed, not bought
        # twice; a new note is a new request with a fresh seed.
        seed = pending["seed"] if pending and pending.get("note") == note else entities.fresh_seed()
        shot["assets"]["pending"] = {"seed": seed, "note": note, "requested_at": llm_call.utc_now()}
        host.write_board()
        ctx.on_log(f"🖼 Shot {shot_id} again (seed {seed}){_noted(note)}")
        ctx.cancel.check()
        try:
            record = host.make_image(shot, seed=seed, note=note)
        except gencache.JournalError as exc:
            raise host.journal_failed(exc) from None
        except ShotFailed as exc:
            reason = str(host.link_gone).rstrip(".") if host.link_gone is not None else exc.reason
            raise refuse(f"{reason}. The request is kept (seed {seed}): regenerate {target!r} again, or run "
                         "the assets step, to ask for the same image.") from None
        cached = record["_cached"]
        host.apply_image(shot, record)
    finally:
        host.write_ledger_view()
    ctx.on_log(f"🔁 Regenerated {target} (seed {shot['assets']['seed']}){_noted(note)}")
    return {"target": target, "shot": shot_id, "seed": shot["assets"]["seed"], "provider": shot["assets"]["provider"],
            "cached": cached}


def regenerate_shot_clip(ctx, ec, target, shot_id, note, *, tools, refuse) -> dict:
    """``shot:<ep>:<shot_id>:video`` (kind ``shot_video``, phase 6 stage 8):
    that shot's clip again, one clip, with *note* at its prompt's tail and a
    fresh seed -- persisted as the clip's ``pending{seed, note,
    requested_at}`` **before** the call, so a retry (this regenerate again
    with the same note, or the assets step) asks for the same clip and the
    generation cache serves or resumes it (DEC-154). Refused before any
    call: a story below tier 2, a shot kept still, a keyframe that is not
    current (:func:`clip_target_refusal`), a clip that cannot be made now or
    would go over a cap (:func:`clip_quote`). The episode's video link alone
    (the planner's when none is recorded, then recorded), the gates and the
    journal of the video phase; nothing else is tried. *refuse(reason)* is
    the caller's ``StepFailed`` builder."""
    host = _Assets(ctx, ec, tools=tools)
    try:
        host.script, host.storyboard = require_approved(ec)
    except StepFailed as exc:
        raise refuse(str(exc)) from None
    board = host.storyboard
    shot = next((s for s in board["shots"] if s["shot_id"] == shot_id), None)
    if shot is None:
        raise refuse(f"episode {ec.ep}'s storyboard has no shot {shot_id!r} (it has sh01 to "
                     f"sh{len(board['shots']):02d}).")
    if note is not None and len(note) > schemas.REGENERATE_NOTE_MAX:
        raise refuse(f"a note is at most {schemas.REGENERATE_NOTE_MAX} characters ({len(note)} given).")
    doc = _read_assets_doc(ec)
    reason = clip_target_refusal(ec, shot, doc=doc)
    if reason is not None:
        raise refuse(reason)
    gates = host.open_asset_gates()
    try:
        quote = clip_quote(ec, host.script, board, shot, env=ctx.settings_env, adapters=tools.adapters,
                           probe_local=True, transport=tools.transport, ledger=gates.ledger)
        if not quote["ready"]:
            raise refuse(quote["message"])
        video = quote["video"]
        host.video = _video_summary(dict(video, plan=[{"shot_id": shot_id}]), animate=True)
        host.video_link_kept = sticky_link.recorded(doc, sticky_link.VIDEO) is not None
        host.clip_todo = [shot_id]
        tier = clips.tier_of(ec)
        flags = clips.shot_flags(shot, doc)
        clip = shot["assets"].get("clip")
        pending = (clip or {}).get("pending")
        # The same request asked again (its call did not answer) keeps its
        # seed, so a paid clip the provider holds is resumed, not bought
        # twice; a new note is a new request with a fresh seed.
        seed = pending["seed"] if pending and pending.get("note") == note else entities.fresh_seed()
        requested = {"seed": seed, "note": note, "requested_at": llm_call.utc_now()}
        if clip:
            shot["assets"]["clip"] = dict(clip, pending=requested)
        else:
            parts = clips.clip_request_parts(ec, shot, host.script, tier=tier, flags=flags, note=note)
            shot["assets"]["clip"] = {
                "state": "failed", "link": video["link"], "route": video["route_class"],
                "clip_s": int(quote["clip_s"]), "est_usd": quote["est_usd"], "prompt_hash": parts["hash"],
                "image_sha256": _sha256_file(shot_image_path(ec, shot)), "cache_key": None, "generated_at": None,
                "note": note, "pending": requested, "reason": "asked again: not answered yet"}
        host.write_board()
        ctx.on_log(f"🎬 Shot {shot_id}'s clip again (seed {seed}){_noted(note)}")
        ctx.cancel.check()
        try:
            record, info = host.make_clip(shot, video=video, clip_s=quote["clip_s"], est_usd=quote["est_usd"],
                                          seed=seed, note=note, flags=flags, tier=tier,
                                          image_link=recorded_image_link(doc))
        except gencache.JournalError as exc:
            raise host.journal_failed(exc) from None
        except ClipFailed as exc:
            host.fail_clip(shot, exc)
            why = str(host.video_gone).rstrip(".") if host.video_gone is not None else exc.reason
            raise refuse(f"{why}. The request is kept (seed {seed}): regenerate {target!r} again with the same "
                         "note, or run the assets step, to ask for the same clip.") from None
        host.apply_clip(shot, record, info, video=video)
    finally:
        host.write_ledger_view()
    ctx.on_log(f"🔁 Regenerated {target} (seed {seed}){_noted(note)}")
    return {"target": target, "shot": shot_id, "seed": seed, "link": record["link"], "clip_s": record["clip_s"],
            "cached": info["cached"]}


def regenerate_line_voice(ctx, ec, target, line_id, note, *, tools, refuse) -> dict:
    """``line:<ep>:<line_id>`` (kind ``line``): that line spoken again by its
    speaker's pinned voice alone (``voice_lines``: a one-link chain; a
    failure names the voice to change, nothing else is tried), with a new
    ``take`` so the generation cache misses on purpose, and *note* as the
    take's spoken direction (phase 5 stage 7, ``voices.synthesize_line``: an
    engine that cannot follow one says it was recorded, not applied).

    The take is persisted in the line's entry of ``assets.json`` (phase 5
    stage 7; it was lost with the job's result before): as ``pending{take,
    note, requested_at}`` **before** the call -- a retry with the same note
    asks the same take, so a request the provider holds is resumed, never
    bought twice (DEC-154's rule, for voices) -- then, once spoken, as
    ``take{id, note, audio_sha256}``. An episode with no ``assets.json`` yet
    (the assets step never finished) keeps no record, as before. The script
    is re-timed and the storyboard follows -- a scene a text-only edit
    marked ``retime_only`` is re-timed in place -- and no approval is
    cleared."""
    host = _Assets(ctx, ec, tools=tools)
    try:
        host.script, host.storyboard = require_approved(ec)
    except StepFailed as exc:
        raise refuse(str(exc)) from None
    line = next((ln for scene in host.script["scenes"] for ln in scene["lines"] if ln["line_id"] == line_id), None)
    if line is None:
        raise refuse(f"episode {ec.ep}'s script has no line {line_id!r}.")
    if voices.voice_label(voice_lines.speaker_voice(ec, line["speaker"])) is None:
        who = "the narrator" if line["speaker"] == "narrator" else voice_lines.speaker_name(ec, line["speaker"])
        raise refuse(f"{voice_lines.no_voice_reason(ec, line['speaker'])}: pick a voice for {who} first.")
    if note is not None:
        note = " ".join(str(note).split()) or None
    if note is not None and len(note) > schemas.REGENERATE_NOTE_MAX:
        raise refuse(f"a note is at most {schemas.REGENERATE_NOTE_MAX} characters ({len(note)} given).")
    try:
        doc = episode_common.read_episode(ec, ASSETS_DOC)
    except StepFailed as exc:
        raise refuse(str(exc)) from None
    pending = ((doc or {}).get("lines", {}).get(line_id) or {}).get("pending")
    # The same request asked again (its call did not answer) keeps its take;
    # a new note is a new request with a fresh one.
    take = pending["take"] if pending and pending.get("note") == note else secrets.token_hex(8)
    host.voice_take, host.voice_direction = take, note
    if doc is not None:
        entry = dict(doc["lines"].get(line_id) or {})
        entry["pending"] = {"take": take, "note": note, "requested_at": llm_call.utc_now()}
        doc["lines"][line_id] = entry
        try:
            ec.store.write_episode_doc(ec.story_id, ec.ep, ASSETS_DOC, doc, now=llm_call.utc_now())
        except (schemas.SchemaError, ValueError, KeyError) as exc:
            raise refuse(f"its take could not be kept in {ASSETS_DOC} before the call ({exc}); nothing was "
                         "asked.") from None
    gates = host.open_asset_gates()
    try:
        host.before_synthesis([line])
        ctx.on_log(f"🎙 Line {line_id} again (take {take}){_noted(note)}")
        try:
            host.measure_line(gates, line)
        except gencache.JournalError as exc:
            raise host.journal_failed(exc) from None
        if host.voice_failed:
            kept = (f" The take is kept ({take}): regenerate {target!r} again with the same note to ask for it "
                    "again." if doc is not None else "")
            raise refuse(f"{host.voice_message()}{kept}")
        previous = episode_common.read_episode(ec, ASSETS_DOC)
        entry = host.line_entries().get(line_id)
        if previous is not None and entry is not None:
            entry["take"] = {"id": take, "note": note, "audio_sha256": _sha256_file(line_audio_path(ec, line))}
            previous["lines"][line_id] = entry
            try:
                ec.store.write_episode_doc(ec.story_id, ec.ep, ASSETS_DOC, previous, now=llm_call.utc_now())
            except (schemas.SchemaError, ValueError, KeyError) as exc:
                ctx.on_log(f"⚠️ {ASSETS_DOC} could not be updated ({exc}); the assets step writes it again.")
    finally:
        host.write_ledger_view()
    ctx.on_log(f"🔁 Regenerated {target}{_noted(note)}")
    return {"target": target, "line": line_id, "voice": line["timing"]["voice"],
            "duration_s": line["timing"]["duration_s"], "take": take}
