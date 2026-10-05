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
   cleared** (DEC-135, DEC-155). Before them, a Gemini line voiced before
   the tail guard (``providers.tts_tail``: the static Gemini appends after
   the last word) is cleaned in place, for free, never spoken again, and
   re-timed the same way (``voice_lines.LineMeasurement.guard_tails``); the
   summary's ``tails`` (only when the guard saw a line) and one log line say
   how many line endings were cleaned and how many seconds were cut.
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
from clipping.providers import gating, gen_timings, gencache, local_comfyui, prompt_limits
from clipping.providers import generation as gen
from clipping.providers import lipsync as lipsync_providers
from clipping.providers.registry import ChainError, Link, describe

from .. import (defaults, hardware, imaging, media_policy, native_speech, prompt_budgets, prompt_templates, prompting,
               refimages, schemas, stock_cutaways, timing, video_plan, voices, wordtiming)
from .. import ledger as ledger_mod
from .. import names as names_mod
from .. import shots as shots_mod
from .. import steps as steps_pkg
from .. import store as store_mod
from ..render import audio_assets, imagesize
from . import brief as brief_step
from . import clips, entities, episode_common, judge, llm_call, sticky_link, voice_lines
from . import lipsync as lipsync_step
from . import native_take as take_mod
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
# DEC-258: a lipsync's poll budget is a clip's (``lipsync.FAL_LIPSYNC_POLL_BUDGET_SECONDS``).
STORY_LIPSYNC_CALL_SECONDS = 600

# What a clip still generating is offered -- never its regenerate target: a
# new seed would buy a second clip while the first is billed (DEC-152).
CONTINUE_ONLY = "press Continue (the assets step resumes it; nothing is bought again)"

# A local ComfyUI card of at most this many GB is asked ``POST /free`` once
# before the first clip when a shot image ran on it in the same run: the
# image model is unloaded so the video model fits (phase 6 plan, stage 8).
FREE_VRAM_GB = 12

# The pause before a free link that pushed back is asked again
# (:data:`pacing.RATE_LIMIT_PAUSE_S`, re-imported above under this name).

# A shot's image is vertical 9:16, the size of the plates it is composed on
# (a 16:9 or 1:1 story's is its frame's: :func:`shot_size`, plan 23 stage B7).
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


class _FixStopped(Exception):
    """The keyframe auto-fix stops here (phase 8 stage B): the step budget
    cannot fit its next check. The step itself goes on."""


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


def with_note(prompt, note, entity_docs, *, layered=False) -> str:
    """*prompt*, then ``Author's note: <note>.`` at its tail -- after the
    locked blocks, never in place of them -- with every entity name in the
    note replaced by a neutral word (no name enters an image prompt, spec
    2.3; ``refimages._with_note``'s rule). On a *layered* (v2) shot, the
    v2 prompt's own name map (``shots.name_map``: a descriptive name, a
    prop's own noun, is kept), so a note that says who it is about by a
    handle -- the keyframe auto-fix's (phase 8 stage B) -- is never
    garbled."""
    if not note:
        return prompt
    text = " ".join(str(note).split())
    if not text:
        return prompt
    names = shots_mod.name_map(entity_docs, v2=True) if layered else _name_map(entity_docs)
    text = names_mod.without_names(text, names)
    if text[-1] not in ".!?":
        text += "."
    return f"{prompt} Author's note: {text}"


def effective_prompt(shot, entity_docs, note=None) -> str:
    """What the image is asked for: the shot's ``prompt_override`` when the
    user wrote one (any entity name in it replaced, as in a note: the
    resolved ``image_prompt`` never carries one), else its ``image_prompt``;
    *note* at the tail."""
    layered = bool(shot.get("prompt_layout"))
    names = shots_mod.name_map(entity_docs, v2=True) if layered else _name_map(entity_docs)
    override = shot.get("prompt_override")
    base = names_mod.without_names(override, names) if override else shot["image_prompt"]
    return with_note(base, note, entity_docs, layered=layered)


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
    """The link a shot's image was made on (``provider/model``), or None -- for a frame cut from
    a stock clip too (plan 23 stage B8: no link made it, so it is none of the episode's image link)."""
    if assets.get("route") == schemas.STOCK_ROUTE:
        return None
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
    modes = assets_doc.get("shot_modes")
    if modes:
        # Plan 25 stage 1: who makes each shot is approved too; a document without modes keeps its fingerprint.
        payload["shot_modes"] = {shot_id: dict(entry) for shot_id, entry in sorted(modes.items())}
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
    9:16 -- its own frame on a 16:9 or 1:1 story (plan 23 stage B7) --
    (``media_policy.keyframe_crop`` on ``imagesize.image_size``) by one
    single-frame ffmpeg pass into *out_dir*, in the same format, when it is
    not one already -- so the near-9:16 concat edge of DEC-217 never arises.
    An unreadable size keeps the file (the render frames it as before).
    ``ShotFailed`` when ffmpeg is missing or fails: never a silent keep.
    *run* is ``subprocess.run`` (None) or a test's stand-in."""
    if not media_policy.is_v2(story):
        return produced
    frame = media_policy.aspect(story)
    crop = media_policy.keyframe_crop(imagesize.image_size(produced), frame)
    if crop is None:
        return produced
    run = run or subprocess.run
    ext = os.path.splitext(produced)[1] or ".png"
    out = os.path.join(out_dir, f"keyframe-{frame.replace(':', 'x')}{ext}")
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", produced,
            "-vf", f"crop={crop[0]}:{crop[1]}", "-frames:v", "1", out]
    try:
        result = run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    except OSError as exc:
        raise ShotFailed(f"ffmpeg is needed to crop the image to {frame} and cannot run ({exc}); install ffmpeg "
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


def _words(text) -> int:
    return len(text.split())


def fit_to_budget(ec, shot, note, link, *, budget, continuity=None) -> tuple:
    """``(prompt, info)`` -- the prompt a layered *shot* is sent on *link*
    when its stored prompt with *note* at its tail is over *budget* (DEC-249):
    the shot resolved again (``shots.resolve_stored``) to the room the note
    leaves, so the context layers make room and the note is kept whole; a
    stored prompt built to another link's budget is fitted to this link's
    the same way. *info*: ``{"from", "to", "note", "budget"}`` in words.
    ``(None, shortest)`` when even the ladder's last rung is over with the
    note (*shortest*: the words it got down to; None when the shot cannot be
    resolved again -- the script or storyboard unreadable, the scene gone:
    the stored prompt's own refusal stands). *continuity*: as
    ``shots.resolve_stored``'s (None: as stored). The prompt hash is never
    made of this prompt: it stays the stored one's, so what is current never
    moves with the room a link leaves."""
    stored = effective_prompt(shot, ec.entities, note)
    note_words = _words(stored) - _words(effective_prompt(shot, ec.entities, None))
    try:
        script = episode_common.read_episode(ec, SCRIPT_DOC)
        board = episode_common.read_episode(ec, STORYBOARD_DOC)
    except StepFailed:
        return None, None
    if script is None:
        return None, None
    budgets = prompt_budgets.for_links(link)._replace(keyframe=budget - note_words)
    try:
        resolved = shots_mod.resolve_stored(shot, script=script, storyboard=board, entities=ec.entities,
                                            style_lock=ec.style_lock, consistency_mode=ec.consistency_mode,
                                            ledger=script_step.ledger_of(ec), budgets=budgets, continuity=continuity)
    except shots_mod.PromptOverBudget as exc:
        return None, exc.words
    except (KeyError, ValueError):
        return None, None
    prompt = effective_prompt(dict(shot, image_prompt=resolved["image_prompt"]), ec.entities, note)
    return prompt, {"from": _words(stored), "to": _words(prompt), "note": note_words, "budget": budget}


def _fitted(ec, shot, note, link, *, budget, continuity=None) -> tuple:
    """``(sent prompt, over, info)`` of a layered *shot* on *link*: its stored
    prompt with *note* when it fits *budget* (``prompt_budgets.over_sentence``),
    else :func:`fit_to_budget`'s; *over* why nothing can be sent when even
    that fails, *info* the re-fit's when one was made (else None)."""
    prompt = effective_prompt(shot, ec.entities, note)
    override = bool(shot.get("prompt_override"))
    over = prompt_budgets.over_sentence("keyframe", shot["shot_id"], link, prompt, budget=budget, override=override)
    if not over or override:
        return prompt, over, None
    fitted, info = fit_to_budget(ec, shot, note, link, budget=budget, continuity=continuity)
    if fitted is None:
        note_words = _words(prompt) - _words(effective_prompt(shot, ec.entities, None))
        if note_words:
            over = prompt_budgets.note_over_sentence("keyframe", shot["shot_id"], link, _words(prompt), note_words,
                                                     budget=budget, shortest=info)
        return prompt, over, None
    # By construction within the budget; the link's own limit (characters) is checked once more.
    over = prompt_budgets.over_sentence("keyframe", shot["shot_id"], link, fitted, budget=budget)
    return fitted, over, info


def shot_size(story) -> tuple:
    """``(width, height)`` *story*'s shot images (and the clips made from
    them) are asked at: :data:`SHOT_SIZE` on a 9:16 story, every request and
    prompt hash as it always was; its frame's on a 16:9 or 1:1 one (plan 23
    stage B7, ``media_policy.image_size``)."""
    return media_policy.image_size(story, SHOT_SIZE)


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
    ``assets.continuity``.

    ``over`` (stage F2): why a layered shot's prompt cannot be sent to
    *link* now (``prompt_budgets.over_sentence``: over the link's limit, or
    over its word budget), else None; the hash never moves with it.
    :meth:`_Assets.make_image` refuses such a shot, calling nothing; the
    dispatch check (F1) is the backstop. Before refusing, a stored prompt
    that is over with its *note* at its tail (the keyframe auto-fix's, a
    regenerate's: appended after the prompt was built to its budget), or
    built to another link's budget, is resolved again to the room the link
    leaves (DEC-249, :func:`fit_to_budget`): ``prompt`` is then the fitted
    one, ``refit`` says from and to how many words (else None), and the
    ``hash`` stays the stored prompt's, so nothing made before reads stale."""
    mode = ec.consistency_mode
    size = shot_size(ec.story)
    prompt = effective_prompt(shot, ec.entities, note)
    negative = shot["negative_prompt"]
    layered = link is not None and bool(shot.get("prompt_layout"))
    budget = prompt_budgets.keyframe_words(link) if layered else None
    # DEC-249: the stored prompt is what the hash is made of; what is sent is
    # fitted to the link when the stored one, with its note, is over.
    sent_prompt, over, refit = _fitted(ec, shot, note, link, budget=budget) if layered else (prompt, None, None)
    if mode == PROMPT_ONLY:
        kind, paths, missing, ref_shas = gen.IMAGE, [], [], []
        return {"kind": kind, "prompt": sent_prompt, "negative": negative, "consistency": mode, "size": size,
                "references": paths, "missing": missing,
                "hash": prompt_hash(prompt, negative, mode, size, ref_shas), "over": over, "refit": refit}
    kind = gen.IMAGE_EDIT
    paths, missing = reference_paths(ec, shot, link=link)
    ref_shas = [_sha256_file(path) or f"unreadable:{path}" for path in paths] + [f"missing:{rel}"
                                                                                 for rel in missing]
    slot = continuity_slot(shot, link)
    if slot is None:
        return {"kind": kind, "prompt": sent_prompt, "negative": negative, "consistency": mode, "size": size,
                "references": paths, "missing": missing,
                "hash": prompt_hash(prompt, negative, mode, size, ref_shas), "over": over, "refit": refit}
    digest = prompt_hash(prompt, negative, mode, size, ref_shas + [_CONTINUITY_HASH_TOKEN])
    sent = list(paths)
    if continuity is not None:
        sent.insert(min(slot, len(sent)), continuity)
    elif alone is not None and not shot.get("prompt_override"):
        alone_shot = dict(shot, image_prompt=alone[0], reference_images=list(alone[1]))
        # Asked alone (no previous keyframe to send): fitted the same way, without the slot.
        sent_prompt, over, refit = _fitted(ec, alone_shot, note, link, budget=budget, continuity=False)
        sent, missing = reference_paths(ec, alone_shot, link=link)
    return {"kind": kind, "prompt": sent_prompt, "negative": negative, "consistency": mode, "size": size,
            "references": sent, "missing": missing, "hash": digest, "over": over, "refit": refit}


def sent_image_prompt(ec, shot, parts, *, link, live=None, wardrobe=None, script=None) -> dict:
    """The keyframe prompt sent for *shot* (plan 26 H1): on a v2 story the
    image master and the scene template (``prompt_templates.shot_keyframe_prompt``)
    before *parts*' prompt -- the core :func:`request_parts` built, its note
    and any DEC-249 refit included, unchanged and last -- fitted to *link*'s
    whole-prompt words (``prompt_budgets.link_words``: none on a manual link)
    and its own check (``prompt_limits.fits``); *link* may be a role chain
    (a list of labels, no link recorded yet): fitted to its smallest bound
    (``prompt_budgets.chain_words``) and every bounded link's check, so no
    fallback refuses it. On a v1 story, or a shot whose ``prompt_override``
    the user wrote (:func:`effective_prompt` sends it as written), the core
    alone. ``{text, words, full_words, limit, dropped: [label]}``. *parts*
    (its ``prompt`` and ``hash``) is never touched: the hash stays the
    core's, so nothing made turns stale. *wardrobe* as
    ``clips.sent_clip_prompt``'s; *script* the episode's (None: read)."""
    core = parts["prompt"]
    if not media_policy.is_v2(getattr(ec, "story", None)) or shot.get("prompt_override"):
        words = len(core.split())
        return {"text": core, "words": words, "full_words": words, "limit": None, "dropped": []}
    if isinstance(link, (list, tuple)):
        labels = [label if isinstance(label, str) else describe(label) for label in link]
    else:
        labels = [link if isinstance(link, str) or link is None else describe(link)]
    labels = [label for label in labels if label]
    limit = prompt_budgets.chain_words(labels, live=live)
    bounded = [label for label in labels
               if not label.startswith(prompt_budgets.UNBOUNDED_PREFIXES)
               and prompt_limits.limit_for(label, live=live) is not None]
    fits = (lambda text: all(prompt_limits.fits(label, text, live=live)[0] for label in bounded)) if bounded else None
    if script is None:
        try:
            script = episode_common.read_episode(ec, SCRIPT_DOC)
        except StepFailed:
            script = None
    return prompt_templates.shot_keyframe_prompt(ec, shot, script, core, limit_words=limit, fits=fits,
                                                 wardrobe=wardrobe if wardrobe is not None else clips.wardrobe_of(ec))


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


def own_keyframe(story, shot, doc) -> bool:
    """Whether *shot*'s keyframe is the human's own upload by its own mode
    (plan 25 stage 1: ``shot_modes[shot_id].image`` ``manual``) on a story
    whose keyframes the app draws -- on a story whose images are all the
    human's (``media_policy.images_manual``) every keyframe is, as before."""
    return (not media_policy.images_manual(story)
            and video_plan.shot_mode(doc, shot["shot_id"], "image") == video_plan.MANUAL)


def shot_image_link(story, shot, link, doc):
    """The image link *shot*'s keyframe is held to: ``manual/upload`` when it
    is the human's own by its mode (:func:`own_keyframe`), else *link* (the
    episode's recorded one), as before."""
    return gen.MANUAL_LINK if own_keyframe(story, shot, doc) else link


def image_mode(story, shot, doc) -> str:
    """Who makes *shot*'s keyframe now (plan 25 stage 1): ``manual`` on a
    story whose images are the human's or for a shot set to ``manual``,
    else ``auto``."""
    if media_policy.images_manual(story) or own_keyframe(story, shot, doc):
        return video_plan.MANUAL
    return video_plan.AUTO


def shot_state(ec, shot, *, link=_READ, doc=_READ) -> str:
    """:func:`image_state` of *shot* now: its hash recomputed with the note
    its image was made with; *link* the episode's recorded image link (read
    from ``assets.json`` unless given) -- ``manual/upload`` for a shot whose
    keyframe is the human's own by its mode (plan 25 stage 1,
    :func:`shot_image_link`; *doc* ``assets.json``, read unless given)."""
    if shot["assets"].get("route") == schemas.STOCK_ROUTE:
        # Plan 23 stage B8: a frame cut from a stock clip is never made again by a call.
        return stock_cutaways.keyframe_state(ec, shot, file_ok=shot_image_path(ec, shot) is not None)
    if doc is _READ:
        doc = _read_assets_doc(ec)
    if link is _READ:
        link = recorded_image_link(doc)
    link = shot_image_link(ec.story, shot, link, doc)
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
    doc = _read_assets_doc(ec)
    if link is _READ:
        link = recorded_image_link(doc)
    outdated = []
    for shot in storyboard["shots"]:
        if shot_image_path(ec, shot) is None:
            continue
        state = shot_state(ec, shot, link=link, doc=doc)
        if state != "current" and not (shot["assets"].get("locked") and state == "locked_stale"):
            outdated.append(shot["shot_id"])
    return outdated


def shots_to_make(ec, storyboard, *, link=_READ, doc=_READ) -> list:
    """The shots whose image is to make: neither locked nor current (the
    human's own by their mode too: :func:`app_shots` leaves those out)."""
    if doc is _READ:
        doc = _read_assets_doc(ec)
    if link is _READ:
        link = recorded_image_link(doc)
    return [shot for shot in storyboard["shots"]
            if not shot["assets"].get("locked") and shot_state(ec, shot, link=link, doc=doc) != "current"]


def app_shots(ec, shots, doc) -> list:
    """*shots* less those whose keyframe is the human's own by their mode
    (plan 25 stage 1, :func:`own_keyframe`): what the step draws and prices."""
    return [shot for shot in shots if not own_keyframe(ec.story, shot, doc)]


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
        if shot_image_path(ec, shot) is None or own_keyframe(ec.story, shot, doc):
            # Plan 25 stage 1: a keyframe the human's own by its mode is no link's the episode keeps.
            continue
        if not shot["assets"].get("locked") and shot_state(ec, shot, link=link, doc=doc) != "current":
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
                                        size=shot_size(story), probe_local=probe_local, transport=transport,
                                        role=KEYFRAME_ROLE)
    width, height = shot_size(story)
    request = gen.GenRequest(kind=gen.IMAGE, width=width, height=height)
    return imaging.estimate(gen.IMAGE, env, route=story["generation_profile"]["route"], request=request, qty=qty,
                            story_spent=story_spent, adapters=adapters, step=STEP, what="the shot images",
                            when="the assets step runs", role=KEYFRAME_ROLE, story=story)


_WHAT, _WHEN = "the shot images", "the assets step runs"


def _chain_rows(ec, qty, *, env, story_spent, adapters) -> dict:
    """``imaging.estimate`` of *qty* shot images on the episode's chain: a
    row per link through the runner's gates, calling nothing."""
    kind = image_kind(ec)
    width, height = shot_size(ec.story)
    request = gen.GenRequest(kind=kind, width=width, height=height)
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
    doc = _read_assets_doc(ec)
    todo = [shot["shot_id"] for shot in app_shots(ec, shots_to_make(ec, storyboard, link=link, doc=doc), doc)]
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
    native = media_policy.native_speech(ec.story)
    editable = sticky_link.KINDS + ((sticky_link.VIDEO_SPEECH,) if native else ())
    extra = sorted(set(map(str, value)) - set(editable))
    if extra:
        errors.append(f"links: unknown key(s) {', '.join(extra)} (editable: {', '.join(editable)})")
    merged = gating.merged_env(env)
    wanted = {}
    if native:
        # Plan 22: a native-speech episode's two video links, one a class of shot,
        # each switched to a link its budget profile names (resolved by name).
        _speech_link_switch(ec, value, wanted, errors)
        return wanted
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


def _speech_link_switch(ec, value, wanted, errors) -> None:
    """:func:`link_switch` of a native-speech episode (plan 22): ``image`` as
    any story's is refused here only when asked (an image switch keeps its
    own path); ``video_speech`` one of the budget profile's
    ``speech_links``, ``video`` its ``silent_link`` or one of those."""
    settings = media_policy._profile_of(ec.story)
    speech = [link for link in (settings.get("speech_links") or {}).values() if isinstance(link, str)]
    silent = list(dict.fromkeys([link for link in (settings.get("silent_link"),) if isinstance(link, str)] + speech))
    for slot, allowed in ((sticky_link.VIDEO_SPEECH, speech), (sticky_link.VIDEO, silent)):
        if slot not in value:
            continue
        link = value[slot]
        if not isinstance(link, str) or link not in allowed:
            errors.append(f"links.{slot}: {link!r} is not a link of the {ec.story['generation_profile']['budget_profile']}"
                          f" budget profile (its links: {', '.join(allowed)})")
            continue
        wanted[slot] = link
    if sticky_link.IMAGE in value:
        errors.append("links.image: switch a native-speech episode's image link on its own")


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


def switched_video_doc(ec, doc, wanted, *, now, kind=sticky_link.VIDEO):
    """*doc* (``assets.json``, or None: a minimal one is started) with the
    episode's video link switched to *wanted* -- ``links.video {link:
    wanted, since: now, switched_from: the link it had}`` -- or None when
    that is already its recorded link (phase 6 stage 11, A-087). Only the
    user switches: the clips another link made are then stale
    (``clips.clip_state`` reads the record), the next run animates exactly
    those again on *wanted*, and the assets approval goes stale with the
    fingerprint's ``links``; the storyboard -- its clip records, its
    approval -- is not touched."""
    entry = sticky_link.recorded(doc, kind)
    current = entry["link"] if entry else None
    if current == wanted:
        return None
    new = copy.deepcopy(doc) if doc is not None else _minimal_assets_doc(ec, now)
    new["links"] = dict(new.get("links") or {})
    new["links"][kind] = sticky_link.record(wanted, now=now, switched_from=current)
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


def moded_assets_doc(ec, doc, shot_id, changes, *, now):
    """*doc* (``assets.json``, or None: a minimal one is started) with shot
    *shot_id*'s modes *changes* (``{"clip"|"image": "auto" | "manual" |
    None}``; None clears that kind) applied to its ``shot_modes`` map -- an
    entry left empty is dropped, and so is an empty map, so a mode set then
    cleared leaves the document as it was -- or None when nothing changes
    (plan 25 stage 1, ``workflow.patch_shot_mode``). The storyboard is never
    touched; the assets approval goes stale with the fingerprint's
    ``shot_modes``."""
    base = doc if doc is not None else _minimal_assets_doc(ec, now)
    modes = {sid: dict(entry) for sid, entry in (base.get("shot_modes") or {}).items()}
    entry = modes.get(shot_id, {})
    for kind, value in changes.items():
        if value is None:
            entry.pop(kind, None)
        else:
            entry[kind] = value
    if entry:
        modes[shot_id] = {kind: entry[kind] for kind in schemas.SHOT_MODE_KINDS if kind in entry}
    else:
        modes.pop(shot_id, None)
    if modes == (base.get("shot_modes") or {}):
        return None
    new = copy.deepcopy(base)
    new.pop("shot_modes", None)
    if modes:
        new["shot_modes"] = {sid: modes[sid] for sid in sorted(modes)}
    return new


def handed_assets_doc(ec, doc, platform, model, *, now):
    """*doc* (``assets.json``, or None: a minimal one is started) with the
    handoff's memory ``{"platform", "model"?}`` (plan 25 stage 2: the
    platform the human makes the episode's clips on, and the model on it,
    ``brief.handoff``) -- or None when it is already that. Never in the
    assets fingerprint: a platform choice approves nothing."""
    base = doc if doc is not None else _minimal_assets_doc(ec, now)
    wanted = {"platform": platform}
    if model:
        wanted["model"] = model
    if base.get("handoff") == wanted:
        return None
    new = copy.deepcopy(base)
    new["handoff"] = wanted
    return new


def keyframe_verdict(ec, storyboard, doc, *, env) -> dict:
    """The image quote of one keyframe on the episode's image link (or its
    chain's first runnable one), as a mode verdict (plan 25 stage 1; the
    handoff's gate of an ``auto`` keyframe): ``{link, est_usd, allowed,
    reason}``, calling nothing."""
    ledger = _open_ledger(ec)
    quote = image_quote(ec, 1, env=env, story_spent=float(ledger.totals()["est_usd"]), storyboard=storyboard,
                        link_info=episode_image_link(ec, storyboard, env=env, doc=doc))
    return {"link": quote.get("link"), "est_usd": round(float(quote.get("est_usd") or 0.0), 4),
            "allowed": bool(quote.get("ready")), "reason": quote.get("message")}


def shot_mode_verdict(ec, script, storyboard, shot, *, env, adapters=None, ledger=None) -> dict:
    """The gate's dry run on one new clip of *shot* on the link its mode puts
    it on (plan 25 stage 1), calling nothing: ``{"link", "est_usd",
    "allowed", "reason"}`` -- the plan of :func:`clip_quote` (that shot
    alone, a current clip counted as new), its refusal (the link cannot run:
    no key, no adapter, ``allow_paid`` off -- the sentence names the shot),
    then the caps. Not held by the keyframes' approval: a verdict on the
    link and the money, not on when the step runs."""
    ledger = ledger or _open_ledger(ec)
    doc = _read_assets_doc(ec)
    trial = copy.deepcopy(doc) if doc else {}
    real = trial.get("shots") or {}
    trial["shots"] = {other["shot_id"]: {"keep_still": True} for other in storyboard["shots"]
                      if other["shot_id"] != shot["shot_id"]}
    trial["shots"][shot["shot_id"]] = dict(real.get(shot["shot_id"]) or {}, keep_still=False, animate=True)
    caps, _over = spending_caps(ec, 0.0, env=env, ledger=ledger)
    spent = float((caps.get("episode") or {}).get("spent_usd") or 0.0)
    video = clips.video_units(ec, script, storyboard, trial, env=env, caps=caps, committed_usd=spent,
                              adapters=adapters, image_sha=None)
    row = next((item for item in video["plan"] if item["shot_id"] == shot["shot_id"]), None)
    if row is None:
        return {"link": video.get("link"), "est_usd": 0.0, "allowed": False,
                "reason": video.get("refused") or video["message"]}
    link, est = row.get("link") or video["link"], round(float(row["est_usd"]), 4)
    if not video["ready"]:
        return {"link": link, "est_usd": est, "allowed": False, "reason": video["refused"] or video["message"]}
    if gen.is_manual(link or ""):
        return {"link": link, "est_usd": 0.0, "allowed": True, "reason": "your own clip: nothing is sent or bought"}
    _caps, over = spending_caps(ec, est, env=env, ledger=ledger)
    if over:
        return {"link": link, "est_usd": est, "allowed": False, "reason": over}
    return {"link": link, "est_usd": est, "allowed": True, "reason": "paid, allowed" if est else "free"}


def spending_caps(ec, total, *, env, ledger=None, video=None, fix_usd=0.0, with_refusal=False) -> tuple:
    """``(caps, over_cap)`` for a plan that would spend *total* paid
    dollars on episode *ec.ep*: ``caps`` is ``{"allow_paid", "episode"|"day"|
    "story": {"cap_usd", "spent_usd", "left_usd"}}`` (the three only when the
    budget settings read), ``over_cap`` the budget's refusal of *total* --
    the episode's cap included -- with the numbers, when paid is on; else
    None. Calls nothing. With *video* (``asset_units``' ``video`` part, phase
    6 stage 7) the refusal names the clips' numbers too; with *fix_usd* (the
    keyframe auto-fix's ceiling in *total*, phase 8 stage B), that too.
    With *with_refusal* (plan 23 A4) a third item: the refusal's numbers
    (``BudgetRefused.as_dict``), None when nothing refused."""
    ledger = ledger or _open_ledger(ec)
    story_spent = float(ledger.totals()["est_usd"])
    ep_spent = float(ledger.totals(ec.ep)["est_usd"])
    try:
        budget_obj = gating.budget_of(gating.merged_env(env))
    except ValueError:
        budget_obj = None
    state = budget_mod.day_state()
    day_spent, day_extra = state.spent, state.extra
    caps = {"allow_paid": bool(budget_obj and budget_obj.allow_paid)}
    if budget_obj is not None:
        for name, cap, spent, extra in (("episode", budget_obj.per_episode_cap_usd, ep_spent, 0.0),
                                        ("day", budget_obj.daily_cap_usd, day_spent, day_extra),
                                        ("story", budget_obj.per_story_cap_usd, story_spent, 0.0)):
            caps[name] = {"cap_usd": cap, "spent_usd": round(spent, 4),
                          "left_usd": round(max(0.0, cap + extra - spent), 4)}
        if day_extra > 0:
            # Plan 23 A2: today's allowance counts in the day's left_usd; the saved cap_usd stays what it is.
            caps["day"]["extra_usd"] = round(day_extra, 4)
    over_cap = refusal = None
    if budget_obj is not None and budget_obj.allow_paid and total > 0:
        what = "paid images and voices"
        if video:
            count = video["count"]
            # DEC-258: the lipsync's price, inside the video part's, said apart.
            clips_usd = video["est_usd"] - lipsync_step.counted_usd(video)
            what = (f"paid images, voices and {count} clip{'' if count == 1 else 's'} ({video['seconds']} s on "
                    f"{video['link']}, est ${clips_usd:.3f}{lipsync_step.clause(video)})")
            part = video.get("speech")
            if part is not None:
                # Plan 22: a native-speech plan's two links and its retake budget.
                what = (f"paid images, voices and {count} clip{'' if count == 1 else 's'} ({part['speech_seconds']} s "
                        f"on {part['speech_link']} and {part['silent_seconds']} s on {part['silent_link']}, est "
                        f"${clips_usd:.3f} with up to ${part['retake_usd']:.2f} of retakes)")
        if fix_usd > 0:
            what += f", with up to ${fix_usd:.2f} to redraw flagged keyframes"
        plan = SimpleNamespace(est_usd=total, link=f"episode {ec.ep}'s {what}")
        try:
            budget_mod.check(plan, None, budget=budget_obj, day_spent=day_spent, day_extra=day_extra,
                             ep_spent=ep_spent, story_spent=story_spent)
        except budget_mod.BudgetRefused as exc:
            over_cap, refusal = str(exc), exc.as_dict()
    if with_refusal:
        return caps, over_cap, refusal
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

    A story whose budget profile redraws flagged keyframes (phase 8 stage B,
    ``media_policy.keyframe_fix``) also has ``"keyframe_fix"``
    (:func:`keyframe_fix_units`, after ``video``): its ceiling ("up to
    $0.40 to redraw flagged keyframes") is in ``est_usd`` and ``over_cap``
    like the rest of the paid part, and committed before the clips are
    planned, so they never take the money it may spend.
    """
    ec = on_route(ec, route)
    ledger = ledger or _open_ledger(ec)
    story_spent = float(ledger.totals()["est_usd"])

    doc = _read_assets_doc(ec)
    todo = app_shots(ec, shots_to_make(ec, storyboard, link=recorded_image_link(doc), doc=doc), doc)
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
    # Phase 8 stage B: the keyframe auto-fix's ceiling is part of the paid plan
    # and its caps, committed before any clip (None: a story with no auto-fix,
    # its estimate byte for byte what it was).
    fix, fix_paid = None, 0.0
    if media_policy.keyframe_fix(ec.story) is not None:
        fix = keyframe_fix_units(ec, storyboard, doc, env=env, story_spent=story_spent, adapters=adapters,
                                 transport=transport, link_info=episode_image_link(ec, storyboard, env=env, doc=doc))
        fix_paid = fix["est_usd"]
    video, video_paid = None, 0.0
    if clips.tier_of(ec) >= 2:
        video = _video_units(ec, script, storyboard, doc, env=env, ledger=ledger, adapters=adapters,
                             probe_local=probe_local, transport=transport,
                             committed=images_paid + voices_paid + fix_paid)
        video["animate"] = bool(animate)
        if not animate:
            video["message"] = f"Animate off: no clip is made in this run. {video['message']}".strip()
        # A plan held for the keyframes' approval (phase 7 stage 6b, RC-Q3) is out of this run, as animate off.
        elif video["route_class"] == "paid" and clips.to_buy(video) and not video.get("hold"):
            lip_usd = lipsync_step.counted_usd(video)
            if video["count"] and video.get("speech") is not None:
                # Plan 22: a native-speech episode's two links, each with its own price.
                part = video["speech"]
                for entry in part["classes"]:
                    if (entry.get("row") or {}).get("manual"):
                        # Plan 25 stage 1: the human's own clips beside bought ones are no paid link.
                        continue
                    usd = entry["usd"] + (part["retake_usd"] if entry["class"] == "speech" else 0.0)
                    if entry["count"] or usd:
                        paid_links.append({"kind": gen.VIDEO, "link": entry["link"], "allowed": video["ready"],
                                           "reason": video["refused"] or "paid, allowed", "est_usd": round(usd, 4)})
            elif video["count"]:
                paid_links.append({"kind": gen.VIDEO, "link": video["link"], "allowed": video["ready"],
                                   "reason": video["refused"] or "paid, allowed",
                                   "est_usd": round(video["est_usd"] - lip_usd, 4)})
            if lip_usd:
                # DEC-258: the clips' lipsync, a paid link of its own.
                lip = video["lipsync"]
                paid_links.append({"kind": gen.LIPSYNC, "link": lip["link"],
                                   "allowed": bool(video["ready"] and lip["ready"]),
                                   "reason": video["refused"] or lip["reason"] or "paid, allowed", "est_usd": lip_usd})
            if video["ready"]:
                video_paid = video["est_usd"]
    total = round(images_paid + voices_paid + fix_paid + video_paid, 4)
    caps, over_cap = spending_caps(ec, total, env=env, ledger=ledger, video=video if video_paid else None,
                                   fix_usd=fix_paid)
    units = {
        "images": images, "voices": voices_est,
        "alignment": {"opted_in": bool(align_words),
                      "requests": _alignment_requests(ec, script, align_words=align_words)},
    }
    if video is not None:
        units["video"] = video
    if fix is not None:
        units["keyframe_fix"] = fix
    stock = stock_units(ec, script, storyboard, doc, images=images, video=video, env=env)
    if stock is not None:
        units["stock"] = stock
    units.update({
        "paid_links": paid_links, "caps": caps, "est_usd": total, "over_cap": over_cap,
        "ready": images["ready"] and voices_est["ready"] and over_cap is None,
    })
    if video is not None and animate and (not video.get("hold") or video.get("too_long")):
        units["ready"] = bool(units["ready"] and video["ready"])
    return units


def stock_units(ec, script, storyboard, doc, *, images, video, env):
    """Plan 23 stage B8: what a story's stock cutaways may save, or None (the switch is off, or
    nothing is eligible and nothing is stock): ``{"count", "shots", "stock", "saves_usd",
    "sources", "message"}``. The estimate stays at the generated price until the fill has run
    (it runs at the start of the assets step); ``count`` is the eligible shots with no keyframe
    and no clip yet -- "up to N shots may be stock (free, saves ≈ $x)" -- ``saves_usd`` the
    generated price of those shots' images and of their clips in *video*'s plan; once the fill
    ran those shots are current and cost nothing (``stock`` counts them)."""
    if not media_policy.stock_cutaways(ec.story):
        return None
    from clipping.providers import gating
    from clipping.stock import clips as stock_clips

    wanted = [shot["shot_id"] for shot in storyboard["shots"] if stock_cutaways.wants_fill(ec, shot, script, doc)]
    held = [shot["shot_id"] for shot in storyboard["shots"] if stock_cutaways.is_stock_clip(shot)
            and stock_cutaways.keyframe_is_stock(shot)]
    if not wanted and not held:
        return None
    saves = 0.0
    if wanted and images["count"] and images["est_usd"]:
        saves += float(images["est_usd"]) * len([sid for sid in wanted if sid in images["shots"]]) / images["count"]
    if wanted and video is not None:
        saves += sum(float(row["est_usd"]) for row in video.get("plan") or () if row["shot_id"] in wanted)
    sources = stock_clips.available_sources(gating.merged_env(env or {}))
    saves = round(saves, 4)
    if not wanted:
        message = f"{len(held)} shot{'s' if len(held) != 1 else ''} filled with stock footage (free)."
    elif not sources:
        message = (f"Stock cutaways are on, but no stock source is configured (a Pexels or Pixabay key, or a local "
                   f"B-roll folder): {len(wanted)} shot{'s' if len(wanted) != 1 else ''} generated as usual.")
    else:
        message = (f"up to {len(wanted)} shot{'s' if len(wanted) != 1 else ''} may be stock "
                   f"(free, saves ≈ ${saves:.2f})")
    return {"count": len(wanted), "shots": wanted, "stock": held, "saves_usd": saves, "sources": sources,
            "message": message}


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
    """The step summary's ``video`` before the phase: nothing made yet --
    with ``lipsync`` (DEC-258) on a story whose clips are lipsynced."""
    summary = {"animate": bool(animate), "planned": len(video["plan"]) if animate else 0, "made": 0, "reused": 0,
               "failed": [], "seconds": 0, "usd": 0.0, "link": video["link"], "route": video["route_class"]}
    if video.get("lipsync") is not None:
        summary["lipsync"] = _lipsync_summary(video["lipsync"])
    return summary


def _lipsync_summary(part) -> dict:
    """The step summary's ``video.lipsync`` before the phase (DEC-258): the
    shots lipsynced in this run (``done``), kept current (``reused``), with
    no in-frame line (``skipped``), failed (``{shot_id, reason}``), whose
    clip the link finds no face in (``no_face``: final for that clip, its
    plain clip the take), the seconds billed and the dollars;
    ``unavailable`` the link's reason when it cannot run (nothing is
    lipsynced then)."""
    return {"link": part.get("link"), "done": [], "reused": 0, "skipped": [], "failed": [], "no_face": [],
            "seconds": 0, "usd": 0.0,
            "unavailable": None if part.get("available") else (part.get("reason") or "cannot run")}


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
    the key). ``ValueError`` for a prompt that cannot be built.

    Plan 26 H1: the request's prompt is ``clips.sent_clip_prompt``'s text --
    on a v2 story the master and the scene template before the core, fitted
    to *link* (and a local *template*'s window) -- and that fit is
    ``parts["sent"]`` (``{text, words, full_words, limit, dropped}``);
    ``parts["prompt"]`` and ``parts["hash"]`` stay the core's."""
    parts = clips.clip_request_parts(ec, shot, script, tier=tier, flags=flags, note=note, link=link)
    parts["sent"] = clips.sent_clip_prompt(ec, shot, script, parts, link=link, template=template)
    extra = {"name": f"shot_{shot['shot_id'][2:]}"}
    if template:
        extra["template"] = template
    resolution = media_policy.video_resolution(ec.story)
    if resolution != defaults.VIDEO_RESOLUTION_DEFAULT:
        # The story's 1080p switch (phase 7 stage 4): seedance reads it, the cache
        # keys it; a 720p request is the one it always was (DEC-207).
        extra["resolution"] = resolution
    frame = media_policy.aspect(ec.story)
    if frame != defaults.ASPECT_PORTRAIT:
        # Plan 23 stage B7: a 16:9 or 1:1 story's clip says its frame (the adapters send it, the
        # cache keys it); a 9:16 request is the one it always was, byte for byte.
        extra["aspect"] = frame
    width, height = shot_size(ec.story)
    request = gen.GenRequest(kind=gen.VIDEO, prompt=parts["sent"]["text"], negative=parts["negative"], width=width,
                             height=height, seed=seed, references=(shot_image_path(ec, shot),),
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
        if stock_cutaways.keyframe_is_stock(shot):
            # Plan 23 stage B8: a frame of a stock clip is no generated image: not judged, no continuity either way.
            previous = (None, None, None)
            continue
        path = shot_image_path(ec, shot)
        sha = _sha256_file(path) if path is not None else None
        if path is not None and keyframe_problem(ec, shot, link=link) is None:
            prev_id, prev_path, prev_sha = previous if previous[1] is not None else (None, None, None)
            items.append((shot, path, sha, prev_id, prev_path, prev_sha))
        previous = (shot["shot_id"], path, sha)
    return items


def keyframe_item(ec, storyboard, index, *, link=None):
    """:func:`keyframe_items`' item of shot *index* alone (phase 8 stage B:
    the keyframe auto-fix judges a redrawn shot and the one after it without
    hashing the whole episode), or None: no such shot, or no current
    keyframe. *link*: the episode's recorded image link."""
    if not 0 <= index < len(storyboard["shots"]):
        return None
    shot = storyboard["shots"][index]
    path = shot_image_path(ec, shot)
    if stock_cutaways.keyframe_is_stock(shot) or path is None or keyframe_problem(ec, shot, link=link) is not None:
        return None
    previous = storyboard["shots"][index - 1] if index else None
    if previous is not None and stock_cutaways.keyframe_is_stock(previous):
        previous = None
    prev_path = shot_image_path(ec, previous) if previous is not None else None
    if prev_path is None:
        return shot, path, _sha256_file(path), None, None, None
    return shot, path, _sha256_file(path), previous["shot_id"], prev_path, _sha256_file(prev_path)


# --------------------------------------- the keyframe auto-fix (phase 8 stage B)
#
# On a v2 story whose budget profile has ``keyframe_fix`` (the quality
# preset: up to 2 redraws a flagged shot, at most $0.40 an episode), the run
# redraws each keyframe J2 flagged after its check (``_Assets.fix_keyframes``)
# -- with a fresh seed and a note made of the verdict -- and checks it again,
# with the shot after it, until it passes or the shot has used its redraws.
# What it did is ``assets.json``'s ``keyframe_fixes`` (per shot) and
# ``keyframe_fix_budget`` (the episode).

KEYFRAME_FIXES = "keyframe_fixes"
KEYFRAME_FIX_BUDGET = "keyframe_fix_budget"
# Plan 22: a native-speech episode's retakes (``_Assets.retake_shot``).
SPEECH_RETAKES = "speech_retakes"
_ISSUE_MAX = 1000


def _cut(text, limit) -> str:
    """*text* within *limit* characters, cut at a word (one ellipsis)."""
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.") + "…"


def _note_names(entity_docs) -> dict:
    """``{entity name: how the keyframe prompt says it}``: a character or a
    prop by its handle (``shots.character_handles``/``prop_handles``, the
    prompt's own words for it), a place as "the set"."""
    names = {}
    characters = entity_docs.get("characters") or {}
    props = entity_docs.get("props") or {}
    for cid, handle in shots_mod.character_handles(characters).items():
        names.setdefault(characters[cid]["name"], handle)
    for pid, handle in shots_mod.prop_handles(props).items():
        names.setdefault(props[pid]["name"], handle)
    for doc in (entity_docs.get("places") or {}).values():
        names.setdefault(doc["name"], "the set")
    return names


def framing_order(shot):
    """The imperative a redrawn keyframe ends with (plan 19 stage 3, F3):
    the framing the shot asks, in the prompt's own words
    (``prompting.FRAMING_PHRASES``) -- ``Frame this as tight close-up on the
    face, nothing wider.`` -- or None for a shot without a known framing.
    "nothing wider" is left off the wide establishing shot, the widest there
    is. Built from the shot's own data, never from J2's prose: the live walk
    flagged 7 of 15 keyframes for their framing, and 10 redraws whose note
    only echoed J2 ("Framing is medium shot instead of tight close-up")
    fixed one."""
    framing = (shot or {}).get("framing")
    phrase = prompting.FRAMING_PHRASES.get(framing)
    if not phrase:
        return None
    return f"Frame this as {phrase}." if framing == "wide_establishing" else f"Frame this as {phrase}, nothing wider."


def correction_note(entity_docs, verdict, shot=None) -> str:
    """The note a flagged keyframe is redrawn with: what J2 found -- the
    beat not shown, what is missing, the continuity issue -- with every
    entity name it used (its brief names them) said the way the prompt says
    it (:func:`_note_names`): a note's names otherwise become "the
    character" (:func:`with_note`), and the note no longer says who it is
    about. Within the regenerate note's cap (``schemas.REGENERATE_NOTE_MAX``).

    Plan 19 stage 3 (F3): with the flagged *shot*, the note always ends with
    :func:`framing_order` -- whatever J2 found, its framing ``framing_issue``
    included (never echoed: the order says it in the prompt's words). Always,
    not only on a framing mismatch: a redraw asked to show more ("the frame
    must show the coconut phone") drifts wider, and J2 often names a framing
    miss in ``missing`` or ``continuity_issue`` instead of its own field, so
    no test of its prose would catch every one. The order is the shot's own
    plan, so it never contradicts the rest; the J2 part is cut first, so the
    order is never lost to the cap. Only the auto-fix of a v2 (layered) shot
    calls this (J2 judges no legacy keyframe): no v1 prompt changes."""
    parts = []
    if not verdict["shows_beat"]:
        parts.append("show this shot's action clearly")
    if verdict["missing"]:
        parts.append("the frame must show " + _and(verdict["missing"]))
    if verdict["continuity_issue"]:
        parts.append(f"correct this: {verdict['continuity_issue']}")
    if not parts and verdict.get("framing_issue") and shot is not None:
        parts.append("draw it again with the framing asked")
    text = " ".join(f"Keyframe check: {'; '.join(parts or ['draw it again'])}".split())
    text = names_mod.without_names(text, _note_names(entity_docs))
    order = framing_order(shot)
    if order is None:
        return _cut(text, schemas.REGENERATE_NOTE_MAX)
    head = _cut(text, schemas.REGENERATE_NOTE_MAX - len(order) - 2)
    return f"{head} {order}" if head.endswith(("…", ".", "!", "?")) else f"{head}. {order}"


def fix_history_entry(sha, verdict, *, note, at, unchecked=None) -> dict:
    """One ``keyframe_fixes`` history entry: the keyframe *sha*, whether its
    *verdict* passed and what J2 found (``judge.verdict_text``), the *note*
    it was drawn with; *unchecked* (no verdict yet: why) reads as not
    passed."""
    if verdict is None:
        return {"image_sha256": sha, "passed": False, "issue": _cut(f"not checked: {unchecked}", _ISSUE_MAX),
                "note": note, "at": at}
    passed = judge.verdict_passed(verdict)
    return {"image_sha256": sha, "passed": passed, "issue": None if passed else _cut(judge.verdict_text(verdict),
                                                                                     _ISSUE_MAX),
            "note": note, "at": at}


def fix_message(summary, settings) -> str:
    """What the auto-fix did, in one line: ``2 keyframes redrawn and fixed,
    1 still flagged after 2 redraws, $0.12``."""
    parts = []
    fixed, gave_up, flagged = len(summary["fixed"]), len(summary["gave_up"]), len(summary["flagged"])
    if fixed:
        parts.append(f"{fixed} keyframe{'s' if fixed != 1 else ''} redrawn and fixed")
    if gave_up:
        most = settings["max_redraws_per_shot"]
        parts.append(f"{gave_up} still flagged after {most} redraw{'s' if most != 1 else ''}")
    if flagged:
        parts.append(f"{flagged} still flagged")
    if not parts:
        parts.append("no keyframe redrawn")
    text = f"{', '.join(parts)}, ${summary['spent_usd']:.2f}"
    return f"{text} (stopped: {summary['stopped']})" if summary["stopped"] else text


def keyframe_fix_units(ec, storyboard, doc, *, env, story_spent, adapters=None, transport=None, link_info=None,
                       shots=None):
    """What the keyframe auto-fix may spend in a run, calling nothing (phase
    8 stage B), or None when the story has none (a legacy story, a profile
    without ``keyframe_fix``)::

        {"max_redraws_per_shot", "cap_usd", "spent_usd", "est_usd", "route_class", "link", "message"}

    ``est_usd`` is its ceiling on a paid image link: what is left of the
    episode's fix budget, or every shot (*storyboard*'s, else *shots*)
    redrawn its most at one image's price (:func:`image_quote`), the lower
    -- counted in the paid plan and its caps like the rest of it, so the
    clips planned after it never take the money it may spend. Nothing while
    the keyframes are approved and current (the run never redraws them)."""
    settings = media_policy.keyframe_fix(ec.story)
    if settings is None:
        return None
    most, cap = settings["max_redraws_per_shot"], settings["cap_usd"]
    spent = float(((doc or {}).get(KEYFRAME_FIX_BUDGET) or {}).get("spent_usd") or 0.0)
    base = {"max_redraws_per_shot": most, "cap_usd": cap, "spent_usd": round(spent, 4), "est_usd": 0.0,
            "route_class": None, "link": None}
    count = len(storyboard["shots"]) if storyboard is not None else int(shots or 0)
    if storyboard is not None and doc is not None and keyframes_state(ec, storyboard, doc) == "current":
        return dict(base, message="The keyframes are approved: none is redrawn on its own.")
    if not most or not count:
        return dict(base, message="No keyframe is redrawn on its own.")
    quote = image_quote(ec, 1, env=env, story_spent=story_spent, adapters=adapters, transport=transport,
                        storyboard=storyboard if link_info else None, link_info=link_info)
    paid = quote["route_class"] == "paid"
    each = float(quote["est_usd"] or 0.0) if paid else 0.0
    est = round(min(max(0.0, cap - spent), each * most * count), 4) if paid else 0.0
    message = (f"up to ${est:.2f} to redraw flagged keyframes (at most {most} redraws a shot, ${cap:.2f} an "
               "episode)" if paid else f"flagged keyframes are redrawn free (at most {most} redraws a shot)")
    return dict(base, est_usd=est, route_class=quote["route_class"], link=quote["link"], message=message)


def keyframe_context(ec, storyboard, *, ledger=None):
    """What J2 is shown beside the keyframes (``judge.KeyframeContext``,
    phase 8 stage B): each character's identity sheet on disk (its
    portrait: the full-body sheet on a v2 story), the episode's continuity
    *ledger* (its wardrobe sets) and the scene of every shot."""
    def on_disk(cid, ref):
        if not ref or not ref.get("name"):
            return None
        try:
            path = ec.store.media_path(ec.story_id, "characters", cid, ref["name"])
        except KeyError:
            return None
        return path if path and os.path.isfile(path) and not os.path.islink(path) else None

    sheets = {}
    for cid, doc in ec.entities["characters"].items():
        path = on_disk(cid, (doc.get("refs") or {}).get("portrait"))
        if path:
            sheets[cid] = path
    # Plan 23 stage D5: a shot whose character wears a variant is judged against the variant's sheet.
    variant_sheets = {}
    for shot in storyboard["shots"]:
        for cid, variant_id in (shot.get("variants") or {}).items():
            variant = shots_mod.variant_record(ec.entities["characters"].get(cid), variant_id)
            path = on_disk(cid, ((variant or {}).get("refs") or {}).get("portrait"))
            if path:
                variant_sheets[(cid, variant_id)] = path
    return judge.KeyframeContext(sheets=sheets, ledger=ledger,
                                 scenes={shot["shot_id"]: shot["scene_id"] for shot in storyboard["shots"]},
                                 variant_sheets=variant_sheets)


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
    todo = [shot["shot_id"] for shot in shots_to_make(ec, storyboard, link=recorded_image_link(doc), doc=doc)]
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
    if media_policy.native_speech(ec.story):
        # Plan 22: a native-speech episode's links are its budget profile's, one a class of shot: no link of
        # VIDEO_CHAIN is offered in their place; the Video card switches either among the profile's own.
        chain = []
    own = sticky_link.family(link)
    rows = [row for row in clips.hosted_rows(chain, merged, adapters,
                                             resolution=media_policy.video_resolution(ec.story),
                                             aspect=media_policy.aspect(ec.story))
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
        if media_policy.native_speech(ec.story):
            next_reason = ("a native-speech episode switches its speaking or silent clips' link only among its "
                           "budget profile's (the assets edit's links.video_speech or links.video)")
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
            seconds.insert(0, video_plan.requested_seconds(link, duration,
                                                           lengths=lengths or clips.sold_lengths(ec.story, link)))
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
    if row is not None and row.get("link"):
        # Plan 22: a native-speech shot's clip is on its class's link.
        video = dict(video, link=row["link"])
    quote = {"video": video, "link": video["link"], "route_class": video["route_class"],
             "clip_s": row["clip_s"] if row else None, "est_usd": round(float(row["est_usd"]), 4) if row else 0.0,
             "cover": row.get("cover") if row else None, "over_cap": None, "ready": False,
             "message": video["message"]}
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
    _caps, over, refusal = spending_caps(ec, quote["est_usd"] if paid else 0.0, env=env, ledger=ledger,
                                         with_refusal=True)
    what = f"1 clip ({row['clip_s']} s) of shot {shot['shot_id']} on {video['link']}"
    if over:
        quote.update(over_cap=over, refusal=refusal,
                     message=f"{what} would go over a cap, so nothing would be generated or spent: {over}.")
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
    # The ffmpeg runner of the lipsync's dialogue track and take (DEC-258); None is ``subprocess.run``.
    lipsync_run = None
    # The ffmpeg/ffprobe runner of the native take (plan 22); None is ``subprocess.run``.
    native_run = None

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
        # Plan 22: a native-speech episode's speaking clips' link, recorded on its own.
        self.speech_link_kept = False
        self.video_gone = None
        self.clip_todo = []
        self.local_image_ran = False
        # Phase 7 stage 6b: what the keyframe judge (J2) did in this run (None: not a v2 story).
        self.keyframe_check = None
        # Plan 22 stage 5: the human's clips still missing (None: nothing awaited), and the
        # keyframes still missing on a story whose images are the human's own.
        self.uploads = None
        self.missing_keyframes = []
        # Phase 8 stage B: the episode's continuity ledger, read once (:meth:`ledger_now`),
        # and what the keyframe auto-fix did (None: it did not run).
        self.ledger_read = _READ
        self.keyframe_fix = None
        # Plan 23 stage B8: what the stock fill did in this run (None: the story has no stock cutaways).
        self.stock = None
        # The Gemini tail guard's report of every line it saw in this run
        # (spoken, or cleaned in place), by line id.
        self.tails = {}

    # ---------------------------------------------------------- plumbing

    def voice_refused(self, line, exc) -> None:
        """``voice_lines``' hook: the one-link chain's failures behind a
        line's ``VoiceError`` (none when it did not come from the chain)."""
        self.line_failures[line["line_id"]] = tuple(getattr(exc.__cause__, "failures", None) or ())

    def tail_guarded(self, line, report) -> None:
        """``voice_lines``' hook: what the Gemini tail guard did to *line*."""
        self.tails[line["line_id"]] = report

    def guard_kept_tails(self) -> None:
        """``voice_lines``' :meth:`guard_tails` -- the lines voiced before the
        Gemini tail guard, cleaned in place -- keeping each voice regenerate's
        take on the file it made: ``assets.json`` holds a take only while the
        line's audio is that file (its sha256, :meth:`line_entries`), so a
        take whose audio was cleaned moves to the cleaned file's sha256 at
        once, before anything else can stop the run."""
        ec = self.ec
        doc = _read_assets_doc(ec)
        before = {}
        for scene in self.script["scenes"]:
            for line in scene["lines"]:
                if ((doc or {}).get("lines", {}).get(line["line_id"]) or {}).get("take"):
                    before[line["line_id"]] = (line, _sha256_file(line_audio_path(ec, line)))
        moved = False
        for line_id in self.guard_tails():
            if line_id not in before:
                continue
            line, sha = before[line_id]
            take = doc["lines"][line_id]["take"]
            if sha is not None and take.get("audio_sha256") == sha:
                take["audio_sha256"] = _sha256_file(line_audio_path(ec, line))
                moved = True
        if moved:
            try:
                ec.store.write_episode_doc(ec.story_id, ec.ep, ASSETS_DOC, doc, now=llm_call.utc_now())
            except (schemas.SchemaError, ValueError, KeyError) as exc:
                self.ctx.on_log(f"⚠️ {ASSETS_DOC} could not follow the cleaned lines' takes ({exc}); a voice "
                                "regenerate's record of them is dropped.")

    def tail_summary(self) -> dict:
        """What the Gemini tail guard did in this run: ``{"checked", "cleaned",
        "cut_s", "suspect"}`` -- the lines it saw, the ones it cut static
        from, the seconds cut, and the ones it left whole (``suspect``)."""
        cut = [report["trimmed_s"] for report in self.tails.values() if report["trimmed_s"] > 0]
        return {"checked": len(self.tails), "cleaned": len(cut), "cut_s": round(sum(cut), 3),
                "suspect": [line_id for line_id, report in self.tails.items() if report["reason"] == "suspect"]}

    def tail_message(self, tails):
        """The run's one line about the Gemini tail guard, or None."""
        checked, cleaned = tails["checked"], tails["cleaned"]
        if not checked:
            return None
        if cleaned:
            message = (f"🔇 {cleaned} Gemini line ending{'s' if cleaned != 1 else ''} cleaned "
                       f"({tails['cut_s']:.1f} s of static cut)")
            if checked > cleaned:
                message += f", {checked - cleaned} had none"
        else:
            message = f"🔇 {checked} Gemini line ending{'s' if checked != 1 else ''} checked: no static to cut"
        if tails["suspect"]:
            ids = tails["suspect"]
            message += (f"; {_and(ids)} left whole, {'its end looks' if len(ids) == 1 else 'their ends look'} odd "
                        f"('voice-tails {self.ec.story_id} --ep {self.ec.ep}' shows why)")
        return message

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
        """The story's generation cache, booking through the gates' ledger --
        and giving a booking back there when its request is proven never run."""
        return gencache.GenCache(self.cache_root(), book=self.gates.booker(kind, step=STEP, unit=unit, qty=qty),
                                 release=self.gates.releaser(kind, step=STEP, unit=unit, qty=qty))

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
        # Plan 23 stage D5: a shot naming a variant not approved yet is refused, never drawn in the base look.
        refusal = shots_mod.variant_refusal(shot, ec.entities["characters"])
        if refusal:
            raise ShotFailed(refusal)
        # Phase 8 stage B: the previous keyframe of the scene in a v2 shot's continuity slot.
        source, alone = self.continuity_for(shot)
        parts = request_parts(ec, shot, note=note, link=self.link, continuity=source[1] if source else None,
                              alone=alone)
        continuity = ({"shot_id": source[0]["shot_id"], "image_sha256": _sha256_file(source[1])}
                      if source and _sha256_file(source[1]) else None)
        kind = parts["kind"]
        if parts.get("refit"):
            ctx.on_log(_refit_line(shot_id, "keyframe", self.link, parts["refit"]))
        if parts.get("over"):
            # Stage F2: a prompt the link cannot take is refused here, never sent to be refused there.
            raise ShotFailed(parts["over"])
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
        # Plan 26 H1: the master and the scene template before the core, fitted to the link -- or, with none
        # recorded yet, to the chain's smallest bound (a fallback never refuses it); the hash stays the core's.
        parts["sent"] = sent_image_prompt(ec, shot, parts, link=[describe(candidate) for candidate in chain],
                                          script=self.script)
        if parts["sent"]["dropped"]:
            ctx.on_log(_fit_line(shot_id, link or chain_name(ec, kind), parts["sent"], kind="keyframe"))
        route = ec.story["generation_profile"]["route"]
        cache = self.cache(kind, unit="image", qty=1)
        with tempfile.TemporaryDirectory(prefix="shot-image-") as incoming:
            request = gen.GenRequest(kind=kind, prompt=parts["sent"]["text"], negative=parts["negative"],
                                     width=shot_size(ec.story)[0], height=shot_size(ec.story)[1], seed=seed,
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
        ordered = self.storyboard["shots"]
        index = next((i for i, item in enumerate(ordered) if item["shot_id"] == shot["shot_id"]), None)
        try:
            resolved = shots_mod.resolve_shot(shots_mod.plan_of(shot, v2=True), scene=scene, entities=ec.entities,
                                              style_lock=ec.style_lock, consistency_mode=ec.consistency_mode,
                                              v2=True, ledger=self.ledger_now(), budgets=self.budgets(),
                                              previous_plan=shots_mod.previous_plan(ordered, index)
                                              if index is not None else None)
        except (KeyError, ValueError):
            return None, None
        self.ctx.on_log(f"ℹ️ Shot {shot['shot_id']}: the previous shot of its scene has no keyframe yet, so it is "
                        "asked without its continuity reference.")
        return None, (resolved["image_prompt"], resolved["reference_images"])

    def budgets(self):
        """The word budgets a shot resolved again in this run is built to
        (stage F2, ``prompt_budgets.for_links``): its episode's image link,
        else the one its keyframes are planned on -- what the storyboard
        built the stored prompt to, so the two agree."""
        link = self.link or clips.planned_image_link(self.ec, self.ctx.settings_env)
        return prompt_budgets.for_links(link)

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
        doc = _read_assets_doc(ec)
        todo = shots_to_make(ec, board, doc=doc)
        if not todo:
            ctx.on_log("🖼 Every shot has its image (or is locked): nothing to make.")
            return
        own = [shot for shot in todo if own_keyframe(ec.story, shot, doc)]
        if own:
            # Plan 25 stage 1: the keyframes the human makes by their shot's mode are listed, never asked.
            todo = [shot for shot in todo if shot not in own]
            self.missing_keyframes = brief_step.missing_keyframes(ec, board, doc)
            ctx.on_log(f"✋ {len(own)} keyframe{'s' if len(own) != 1 else ''} to upload (your own, "
                       f"{gen.MANUAL_LINK}): {_and([shot['shot_id'] for shot in own])}.")
            if not todo:
                return
        if media_policy.images_manual(ec.story):
            # Plan 22 stage 5: the keyframes are the user's own uploads -- none is asked of anything.
            self.missing_keyframes = brief_step.missing_keyframes(ec, board, _read_assets_doc(ec))
            ctx.on_log(f"✋ {len(self.missing_keyframes)} keyframe{'s' if len(self.missing_keyframes) != 1 else ''} "
                       f"to upload (your own images, {gen.MANUAL_LINK}): nothing is sent or bought.")
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
        self.speech_link_kept = sticky_link.recorded(doc, sticky_link.VIDEO_SPEECH) is not None
        image_link = recorded_image_link(doc)
        tier = clips.tier_of(ec)
        by_id = {shot["shot_id"]: shot for shot in self.storyboard["shots"]}
        native = video.get("speech") is not None
        todo, kept_ids = [], []
        for row in video["plan"]:
            shot = by_id[row["shot_id"]]
            state = clips.clip_state(ec, shot, self.script, link=row.get("link") or video["link"], tier=tier,
                                     flags=clips.shot_flags(shot, doc),
                                     image_sha=_sha256_file(shot_image_path(ec, shot)))
            if state == "current":
                self.video["reused"] += 1
                kept_ids.append(row["shot_id"])
            else:
                todo.append(row)
        # DEC-258: the lipsync of a kept clip that has none current yet (the step's
        # earlier run, a line re-voiced or re-timed since); a new clip's follows it.
        lip = video.get("lipsync")
        lip_link = lip["link"] if lip is not None and lip.get("available") else None
        lip_todo = self.lipsync_todo(kept_ids, by_id, lip_link) if lip is not None else set()
        if lip is not None and lip_link is None and lip.get("count"):
            ctx.on_log(f"👄 No lip-sync in this run: {lip.get('link') or gen.ENV_NAMES[gen.LIPSYNC]}: "
                       f"{lip.get('reason') or 'cannot run'}; the clips are kept as they are.")
        if native:
            # Plan 22: a kept speaking clip whose take is not current is taken now (free).
            for shot_id in kept_ids:
                self.native_take_shot(by_id[shot_id])
        # Plan 22 stage 5: a clip on manual/upload is the human's to make -- never asked of anything; the
        # step lists it and ends awaiting the uploads (RC-N4: nothing sent, nothing booked).
        manual_rows = [row for row in todo if gen.is_manual(row.get("link") or video["link"])]
        if manual_rows:
            todo = [row for row in todo if row not in manual_rows]
            self.await_uploads(doc)
        if not todo and not lip_todo:
            ctx.on_log(f"🎬 Every planned shot has its current clip on {video['link']}: no clip to make.")
            self.lipsync_total()
            self.take_total()
            return doc
        if todo:
            seconds = sum(int(row["clip_s"]) for row in todo)
            price = f", est ${sum(row['est_usd'] for row in todo):.3f} paid" if video["route_class"] == "paid" else ""
            kept = f" ({self.video['reused']} current, kept)" if self.video["reused"] else ""
            on = video["link"]
            if any(row.get("mode") for row in todo):
                # Plan 25 stage 1: the links the shots' modes put the clips on.
                on = _and(list(dict.fromkeys(row["link"] for row in todo)))
            ctx.on_log(f"🎬 Animating {len(todo)} shot{'s' if len(todo) != 1 else ''} ({seconds} s) on "
                       f"{on}{price}{kept}")
            for row in todo:
                if row.get("cover") == "stretch":
                    # DEC-250: said in the feed, as the estimate says it.
                    ctx.on_log(f"🎬 {clips.cover_sentence(row)}")
            if video["route_class"] == "local":
                self.free_comfyui()
        todo_ids = [row["shot_id"] for row in todo]
        for row in video["plan"]:
            shot = by_id[row["shot_id"]]
            if row["shot_id"] in lip_todo:
                self.lipsync_shot(shot, link=lip_link)
                continue
            if row["shot_id"] not in todo_ids:
                continue
            self.clip_todo = todo_ids[todo_ids.index(row["shot_id"]):]
            self.before_clip(self.clip_todo)
            shot_video = dict(video, link=row["link"]) if native else video
            try:
                record, info = self.make_clip(shot, video=shot_video, clip_s=row["clip_s"], est_usd=row["est_usd"],
                                              seed=clip_seed(shot, story_id=ec.story_id, ep=ec.ep),
                                              note=clip_note(shot), flags=clips.shot_flags(shot, doc), tier=tier,
                                              image_link=image_link, cover=row.get("cover"))
            except ClipFailed as exc:
                self.fail_clip(shot, exc)
                continue
            self.apply_clip(shot, record, info, video=shot_video)
            if native:
                # Plan 22: the clip's own sound becomes its line (the native take), retaken once when it misses.
                self.native_take_shot(shot, video=shot_video, row=row, image_link=image_link)
            if lip_link is not None:
                self.lipsync_shot(shot, link=lip_link)
        self.clip_todo = []
        self.lipsync_total()
        self.take_total()
        return episode_common.read_episode(ec, ASSETS_DOC) or doc

    def await_uploads(self, doc) -> None:
        """Plan 22 stage 5: the clips on ``manual/upload`` still missing
        (``brief.missing_clips``), recorded on the run's summary as its
        ``uploads`` and said once; the step then ends ``awaiting_uploads``
        (:meth:`finish`)."""
        ec, ctx = self.ec, self.ctx
        missing = brief_step.missing_clips(ec, self.script, self.storyboard, doc)
        if not missing:
            return
        self.uploads = uploads_record(ec, self.missing_keyframes + missing)
        speaking = sum(1 for item in missing if item["speaks"])
        ctx.on_log(f"✋ {self.uploads['message']}: {len(missing)} of your own clip{'s' if len(missing) != 1 else ''} "
                   f"({speaking} speaking, {len(missing) - speaking} silent) on {gen.MANUAL_LINK} -- nothing is sent "
                   f"or bought. Make them from the shot brief ({self.uploads['brief']}) and upload each on its shot: "
                   "the run continues once every clip is there.")

    # ------------------------------------------------------------ the lipsync

    def lipsync_todo(self, kept_ids, by_id, link) -> set:
        """The kept clips of *kept_ids* whose lipsync (DEC-258) is to make on
        the link labelled *link*: an in-frame line and no current lipsync;
        the current ones are counted ``reused``, the ones with no in-frame
        line ``skipped``, the ones whose very clip the link found no face in
        ``no_face`` (never sent again). Nothing when *link* is None (the
        link cannot run: :meth:`animate_clips` says so once)."""
        summary = self.video.get("lipsync")
        if summary is None or link is None:
            return set()
        try:
            timeline = lipsync_step.episode_timeline(self.ec, self.script, self.storyboard)
        except (lipsync_step.timeline_mod.TimelineError, KeyError, ValueError):
            timeline = None
        todo = set()
        for shot_id in kept_ids:
            shot = by_id[shot_id]
            if not lipsync_step.spoken_lines(self.script, shot):
                summary["skipped"].append(shot_id)
            elif lipsync_step.is_no_face(self.ec, shot, link=link):
                summary.setdefault("no_face", []).append(shot_id)
            elif timeline is not None and lipsync_step.is_current(self.ec, self.script, self.storyboard, shot,
                                                                  link=link, timeline=timeline):
                summary["reused"] += 1
            else:
                todo.add(shot_id)
        return todo

    def before_lipsync(self, shot_id) -> None:
        """The cancel token, then the step budget: a lipsync starts only while
        its poll (:data:`STORY_LIPSYNC_CALL_SECONDS`) still fits."""
        self.ctx.cancel.check()
        try:
            self.budget.before_call(lambda: f"the lipsync of shot {shot_id}", per_call=STORY_LIPSYNC_CALL_SECONDS)
        except StepFailed as exc:
            raise voice_lines.BudgetSpent(str(exc) + self.also_failed()) from None

    def lipsync_shot(self, shot, *, link) -> None:
        """*shot*'s current clip lipsynced on the link labelled *link*
        (``lipsync`` module docstring): its dialogue track built, the clip and
        the track sent through the story's generation cache and the gates of
        every paid call (the caps, ``allow_paid``, the ledger in seconds,
        rounded up to 5 s), the answer kept as ``assets/clips/shot_NN.lipsync.mp4``
        with the clip's own sound, and ``assets.video`` naming it. A shot
        with no in-frame line keeps its plain clip, unrecorded; a failure
        records ``lipsync.state: failed`` with the reason and keeps the plain
        clip as ``assets.video`` -- never a missing video, never another
        link; a link that finds no face in the clip (a 422
        ``face_detection_error``) records ``no_face`` instead
        (:meth:`no_face_lipsync`): final for that clip, its booking released
        by the journal. ``gencache.JournalError`` passes through."""
        ec, ctx, tools, gates = self.ec, self.ctx, self.tools, self.gates
        shot_id = shot["shot_id"]
        summary = self.video.setdefault("lipsync", _lipsync_summary({"link": link, "available": True}))
        clip = shot["assets"].get("clip") or {}
        plain = lipsync_step.plain_clip_path(ec, shot)
        if clip.get("state") != "current" or plain is None:
            return
        try:
            timeline = lipsync_step.episode_timeline(ec, self.script, self.storyboard)
            spec = lipsync_step.track_spec(ec, self.script, self.storyboard, shot, timeline=timeline)
        except (lipsync_step.timeline_mod.TimelineError, KeyError, ValueError, StopIteration) as exc:
            self.fail_lipsync(shot, {"link": link, "clip_sha256": _sha256_file(plain), "track_hash": None},
                              f"the episode's timeline cannot place its lines ({exc}); nothing was asked")
            return
        if spec is None:
            # No line spoken by a character in its frame: the plain clip, no lipsync.
            summary["skipped"].append(shot_id)
            if clip.get("lipsync") is not None or shot["assets"].get("video") != clips.clip_rel(shot_id):
                shot["assets"]["clip"] = {key: value for key, value in clip.items() if key != "lipsync"}
                shot["assets"]["video"] = clips.clip_rel(shot_id)
                self.write_board()
            return
        if lipsync_step.is_current(ec, self.script, self.storyboard, shot, link=link, spec=spec):
            summary["reused"] += 1
            return
        if lipsync_step.is_no_face(ec, shot, link=link):
            if shot_id not in summary.setdefault("no_face", []):
                summary["no_face"].append(shot_id)
            return
        clip_s = int(clip["clip_s"])
        billed = lipsync_providers.billed_seconds(clip_s)
        record = {"state": "failed", "link": link, "clip_sha256": _sha256_file(plain), "track_hash": spec["hash"],
                  "audio_sha256": None, "cache_key": None, "est_usd": 0.0, "generated_at": None,
                  "lines": [row["line_id"] for row in spec["lines"]], "billed_s": billed}
        self.before_lipsync(shot_id)
        try:
            pinned = [lipsync_step.lipsync_link(gates.merged)]
        except ChainError as exc:
            self.fail_lipsync(shot, record, f"{gen.ENV_NAMES[gen.LIPSYNC]} cannot be used: {exc}")
            return
        if describe(pinned[0]) != link:
            self.fail_lipsync(shot, record, f"{link} is not the link of {gen.ENV_NAMES[gen.LIPSYNC]} any more")
            return
        route = ec.story["generation_profile"]["route"]
        name = f"shot_{shot_id[2:]}{schemas.SHOT_LIPSYNC_SUFFIX}"
        with tempfile.TemporaryDirectory(prefix="shot-lipsync-") as work:
            try:
                track = lipsync_step.build_track(spec, os.path.join(work, f"shot_{shot_id[2:]}.dialogue.wav"),
                                                 run=self.lipsync_run)
            except lipsync_step.LipsyncError as exc:
                self.fail_lipsync(shot, record, f"its dialogue track could not be built ({exc}); nothing was asked")
                return
            record["audio_sha256"] = track["sha256"]
            incoming = os.path.join(work, "answer")
            request = gen.GenRequest(kind=gen.LIPSYNC, references=(plain,), duration_s=clip_s, out_dir=incoming,
                                     extra={"audio": track["path"], "name": name})
            record["cache_key"] = gencache.request_key(gen.LIPSYNC, pinned[0], request)
            cache = self.cache(gen.LIPSYNC, unit="second", qty=billed)
            try:
                result, answered = gen.run_generation_chain(
                    gen.LIPSYNC, pinned, request, env=gates.merged, allow_paid=gates.budget.allow_paid, route=route,
                    on_log=ctx.on_log, budget_check=gates.check, limiter=gates.limiter, adapters=tools.adapters,
                    transport=tools.transport, sleep_fn=tools.sleep_fn, time_fn=tools.time_fn, cancel=ctx.cancel,
                    cache=cache)
            except gencache.JournalError:
                raise
            except gen.NoRunnableLink as exc:
                no_face = lipsync_step.no_face_reason(exc.failures)
                if no_face is not None:
                    self.no_face_lipsync(shot, record, no_face)
                    return
                reasons = "; ".join(f"{label}: {reason}" for label, reason in exc.failures) or str(exc)
                self.fail_lipsync(shot, record, f"{reasons}; no other link was tried")
                return
            except Exception as exc:  # noqa: BLE001 - an adapter's bug fails this lipsync, named
                self.fail_lipsync(shot, record, f"{type(exc).__name__}: {exc}; no other link was tried")
                return
            meta = result.meta or {}
            if "booked" not in meta:
                est = _book_answer(gates, result, answered, gen.LIPSYNC, unit="second", qty=billed)
            else:
                est = round(float((meta.get("booked") or {}).get("est_usd") or 0.0), 4) if result.paid else 0.0
            record.update(est_usd=est, cache_key=meta.get("cache_key") or record["cache_key"])
            produced = next((str(path) for path in result.paths if str(path).lower().endswith(".mp4")), None)
            if produced is None:
                self.fail_lipsync(shot, record, f"{describe(answered)} answered without an .mp4 clip (the call is "
                                                "booked)")
                return
            try:
                dest = ec.store.episode_asset_path(ec.story_id, ec.ep, clips.CLIPS_KIND,
                                                   lipsync_step.lipsync_name(shot_id), create=True)
            except KeyError:
                self.fail_lipsync(shot, record, f"{lipsync_step.lipsync_rel(shot_id)} is not a real file or folder; it "
                                                "is never followed (the call is booked and cached: move it away and "
                                                "run the step again)")
                return
            try:
                synced = lipsync_step.remux(produced, plain, os.path.join(work, "synced.mp4"),
                                            plain_has_audio=clips.clip_has_audio(plain), run=self.lipsync_run)
            except lipsync_step.LipsyncError as exc:
                self.fail_lipsync(shot, record, f"{exc} (the call is booked and cached)")
                return
            _atomic_copy(synced, dest)
        cached, resumed = bool(meta.get("cached")), bool(meta.get("resumed"))
        record.update(state="current", generated_at=llm_call.utc_now())
        shot["assets"]["clip"] = dict(clip, lipsync=record)
        shot["assets"]["video"] = lipsync_step.lipsync_rel(shot_id)
        self.write_board()
        summary["done"].append(shot_id)
        if not cached and not resumed:
            summary["seconds"] += billed
            summary["usd"] = round(summary["usd"] + est, 4)
        count = len(record["lines"])
        how = ("kept answer, no call" if cached else "collected, booked when it was sent" if resumed
               else f"${est:.3f}")
        ctx.on_log(f"👄 Shot {shot_id}: lips synced to {count} line{'' if count == 1 else 's'} ({clip_s} s, {how})")

    def fail_lipsync(self, shot, record, reason) -> None:
        """*shot*'s lipsync ``failed`` with *reason*: the plain clip stays its
        ``assets.video``, the storyboard written, the shot named in the
        summary and the feed (DEC-258)."""
        shot_id = shot["shot_id"]
        reason = self.end_lipsync(shot, record, reason, state="failed")
        summary = self.video.setdefault("lipsync", _lipsync_summary({"link": record.get("link"), "available": True}))
        summary["failed"].append({"shot_id": shot_id, "reason": reason})
        self.ctx.on_log(f"✖ Lip-sync {shot_id} failed: {reason}. Its plain clip is kept.")

    def no_face_lipsync(self, shot, record, reason) -> None:
        """*shot*'s lipsync ended ``no_face``: the link found no face in this
        clip (``lipsync_step.no_face_reason``). Final for this clip on this
        link -- never retried, never priced again, until a new clip -- the
        plain clip the take (DEC-258); the refusal was unbilled and the
        journal released its booking, so the record holds $0."""
        shot_id = shot["shot_id"]
        self.end_lipsync(shot, record, reason, state=lipsync_step.NO_FACE)
        summary = self.video.setdefault("lipsync", _lipsync_summary({"link": record.get("link"), "available": True}))
        summary.setdefault("no_face", []).append(shot_id)
        self.ctx.on_log(f"👄 Shot {shot_id}: no face for the lip-sync (its head is not a face to "
                        f"{record.get('link')}); its plain clip is the take, and this clip is not sent again")

    def end_lipsync(self, shot, record, reason, *, state) -> str:
        """*shot*'s lipsync recorded *state* (``failed`` or ``no_face``) with
        *reason* (cut to 1000 characters, returned): the plain clip stays its
        ``assets.video``, the storyboard written."""
        shot_id = shot["shot_id"]
        reason = reason if len(reason) <= 1000 else reason[:997] + "..."
        clip = shot["assets"].get("clip") or {}
        est = 0.0 if state == lipsync_step.NO_FACE else float(record.get("est_usd") or 0.0)
        base = {"state": state, "link": record.get("link"), "clip_sha256": record.get("clip_sha256"),
                "track_hash": record.get("track_hash"), "audio_sha256": record.get("audio_sha256"),
                "cache_key": record.get("cache_key"), "est_usd": est, "generated_at": None}
        for key in ("lines", "billed_s"):
            if key in record:
                base[key] = record[key]
        base["reason"] = reason
        if base["clip_sha256"] is None or base["track_hash"] is None or base["link"] is None:
            # Nothing to record against (the clip or its lines unreadable): the plain clip alone.
            shot["assets"]["clip"] = {key: value for key, value in clip.items() if key != "lipsync"}
        else:
            shot["assets"]["clip"] = dict(clip, lipsync=base)
        shot["assets"]["video"] = clips.clip_rel(shot_id) if clip.get("state") == "current" else None
        self.write_board()
        return reason

    def lipsync_total(self) -> None:
        """The run's one line about the lipsync (DEC-258), when it did anything."""
        summary = (self.video or {}).get("lipsync")
        no_face = (summary.get("no_face") or []) if summary else []
        if not summary or not (summary["done"] or summary["failed"] or summary["reused"] or no_face):
            return
        parts = []
        if summary["done"]:
            count = len(summary["done"])
            parts.append(f"{count} clip{'' if count == 1 else 's'} synced (${summary['usd']:.3f}, "
                         f"{summary['seconds']} s billed)")
        if summary["reused"]:
            parts.append(f"{summary['reused']} kept current")
        if summary["skipped"]:
            count = len(summary["skipped"])
            parts.append(f"{count} without an on-screen speaker")
        if summary["failed"]:
            failed = [item["shot_id"] for item in summary["failed"]]
            parts.append(f"{len(failed)} failed ({_and(failed)}, plain clip kept)")
        if no_face:
            parts.append(f"{len(no_face)} with no face to sync ({_and(no_face)}, plain clip kept)")
        self.ctx.on_log(f"👄 Lip-sync on {summary['link']}: {', '.join(parts)}.")

    def make_clip(self, shot, *, video, clip_s, est_usd, seed, note, flags, tier, image_link, cover=None):
        """One clip of *shot* on the plan's one link (*video*: ``asset_units``'
        video part -- its ``link``, ``route_class``, ``template`` and
        ``profile``) through the story's generation cache, the gates of every
        paid call and the free-tier limiter; ``(record, info)``: the shot's
        ``assets.clip``, and what the call was (``cached``, ``resumed``,
        ``fresh``, ``wall_s``, ``label``, ``seed``). *cover* (the plan row's,
        DEC-250): ``"stretch"`` when the clip is slowed at render to cover a
        shot longer than it, recorded on the clip so the render knows. :class:`ClipFailed` for
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
        if cover:
            record["cover"] = cover
        try:
            parts = clips.clip_request_parts(ec, shot, self.script, tier=tier, flags=flags, note=note, link=link)
        except (KeyError, ValueError) as exc:
            record["prompt_hash"] = _canonical_sha256({"unusable": str(exc)})
            raise ClipFailed(f"its video prompt cannot be built ({exc}); no clip was asked", record=record) from None
        record["prompt_hash"] = parts["hash"]
        template = video.get("template")

        def fail(reason, *, still=False):
            return ClipFailed(reason, record=record, still=still)

        if parts.get("refit"):
            ctx.on_log(_refit_line(shot_id, "clip", link, parts["refit"]))
        if parts.get("over"):
            # Stage F2: a prompt the link cannot take is refused here, never sent to be refused there.
            raise fail(parts["over"])
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
        if not pinned and media_policy.native_speech(ec.story):
            # Plan 22: a native-speech episode's links are its budget profile's, by name.
            try:
                pinned = gen.parse_generation_chain(gen.VIDEO, [link])[:1]
            except ChainError:
                pinned = []
        if not pinned:
            raise self.clip_gone(f"it is not a link of {gen.ENV_NAMES[gen.VIDEO]} any more", record)
        route = ec.story["generation_profile"]["route"]
        cache = self.cache(gen.VIDEO, unit="second", qty=int(clip_s))
        with tempfile.TemporaryDirectory(prefix="shot-clip-") as incoming:
            sent_parts, request = clip_request(ec, shot, self.script, link=link, template=template, clip_s=clip_s,
                                               seed=seed, note=note, flags=flags, tier=tier, out_dir=incoming)
            if sent_parts["sent"]["dropped"]:
                ctx.on_log(_fit_line(shot_id, link, sent_parts["sent"]))
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
        speaking = bool(shot.get("speaks")) and media_policy.native_speech(ec.story)
        if not clips.off_class(ec.story, shot, _read_assets_doc(ec)):
            # Plan 25 stage 1: a shot its mode moved to another link never becomes its class's sticky link.
            self.keep_video_link(record["link"], kind=sticky_link.VIDEO_SPEECH if speaking else sticky_link.VIDEO)
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

    def keep_video_link(self, link, kind=sticky_link.VIDEO) -> None:
        """Record *link*, which just served a clip, as the episode's
        ``links.video`` unless one is recorded (A-087): every later clip is
        asked of it alone. ``assets.json`` is read, changed and written at
        once (a minimal one is started when there is none). Plan 22: *kind*
        ``video_speech`` records a native-speech episode's speaking clips'
        link the same way, on its own."""
        if kind == sticky_link.VIDEO_SPEECH:
            if self.speech_link_kept:
                return
            self._keep_link(link, kind)
            self.speech_link_kept = True
            return
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

    def _keep_link(self, link, kind) -> None:
        """:meth:`keep_video_link`'s record of a native-speech episode's
        speaking clips' link (``links.video_speech``)."""
        ec, ctx = self.ec, self.ctx
        now = llm_call.utc_now()
        try:
            doc = episode_common.read_episode(ec, ASSETS_DOC)
        except StepFailed as exc:
            ctx.on_log(f"⚠️ The speaking clips' link could not be recorded ({exc}).")
            return
        if doc is None:
            doc = _minimal_assets_doc(ec, now)
        if sticky_link.recorded(doc, kind) is not None:
            return
        doc["links"] = dict(doc.get("links") or {})
        doc["links"][kind] = sticky_link.record(link, now=now)
        try:
            ec.store.write_episode_doc(ec.story_id, ec.ep, ASSETS_DOC, doc, now=now)
        except (schemas.SchemaError, ValueError, KeyError) as exc:
            ctx.on_log(f"⚠️ {ASSETS_DOC} could not record the speaking clips' link now ({exc}).")
            return
        ctx.on_log(f"🔗 Episode {ec.ep}'s speaking clips are on {link}: every other speaking clip of it is made on "
                   "that link alone.")

    # ------------------------------------------------- the native take (plan 22)

    def take_summary(self) -> dict:
        summary = self.video.setdefault("takes", {"ok": [], "flagged": [], "approximate": [], "retaken": []}) \
            if self.video is not None else {"ok": [], "flagged": [], "approximate": [], "retaken": []}
        return summary

    def native_take_shot(self, shot, *, video=None, row=None, image_link=None, sha=None) -> None:
        """*shot*'s clip taken (``native_take`` module docstring), free, after
        it is kept: a speaking shot's sound transcribed and aligned against
        its line, the line's audio and timing written from it and the take
        recorded on the clip (``assets.clip.native_speech``); any shot's
        length then follows its clip's real length
        (``native_speech.shot_seconds``). A take that misses its line
        (``mismatch``, ``no_speech``) is flagged; with *video* and *row* (the
        clip just bought) an agent story buys it once more within its
        budget's ``speech_retake`` (:meth:`retake_shot`). Nothing is asked of
        a take already current for this very clip. *sha* (plan 22 stage 5:
        an upload hashed the clip as it stored it) spares hashing it again."""
        ec, ctx = self.ec, self.ctx
        shot_id = shot["shot_id"]
        clip = shot["assets"].get("clip") or {}
        path = clips.shot_clip_path(ec, shot)
        if clip.get("state") != "current" or path is None:
            return
        if not shot.get("speaks"):
            self._native_length(shot, path)
            return
        _scene, line = clips.speech_line(self.script, shot)
        if line is None:
            return
        sha = sha or _sha256_file(path)
        if take_mod.is_current(clip.get("native_speech"), clip_sha256=sha, line_id=line["line_id"]) and (
                clip["native_speech"]["state"] not in native_speech.TAKES_WITH_SPEECH
                or voice_lines.is_measured(ec, line)):
            return
        take = self._take(shot, line, path, sha)
        if take is None:
            return
        summary = self.take_summary()
        if take["state"] == native_speech.TAKE_OK:
            summary["ok"].append(shot_id)
        elif take["state"] == native_speech.TAKE_STT_UNAVAILABLE:
            summary["approximate"].append(shot_id)
        else:
            summary["flagged"].append({"shot_id": shot_id, "state": take["state"], "matched": take.get("matched"),
                                       "heard": take.get("heard")})
            if video is not None and row is not None:
                self.retake_shot(shot, video=video, row=row, image_link=image_link)

    def _native_floor_s(self) -> float:
        """The shortest a native shot is cut to (plan 27): the template's
        ``min_shot_s``, at least the shot window's 5 s."""
        return max(float(self.ec.template["min_shot_s"]), float(native_speech.SHOT_WINDOW_S[0]))

    def _native_length(self, shot, path) -> None:
        """A silent clip's shot lasts its clip's real length."""
        try:
            real = take_mod.clip_seconds(path, run=self.native_run)
        except take_mod.TakeError as exc:
            self.ctx.on_log(f"⚠️ Shot {shot['shot_id']}: its clip's length could not be read ({exc}); its planned "
                            "length is kept.")
            return
        seconds = max(self._native_floor_s(), native_speech.shot_seconds(real, speaks=False))
        seconds = round(min(seconds, real), 3)
        if abs(float(shot["duration_s"]) - seconds) > 1e-6:
            shot["duration_s"] = seconds
            self.write_board()
            self.save()

    def _take(self, shot, line, path, sha):
        """The take itself (:meth:`native_take_shot`); the record, or None
        when the clip cannot be read (said)."""
        ec, ctx = self.ec, self.ctx
        shot_id, line_id = shot["shot_id"], line["line_id"]
        clip = shot["assets"]["clip"]
        try:
            real = take_mod.clip_seconds(path, run=self.native_run)
        except take_mod.TakeError as exc:
            ctx.on_log(f"⚠️ Shot {shot_id}: its clip cannot be taken ({exc}); its line has no audio yet.")
            return None
        reason = None
        words = aligned_by = None
        with tempfile.TemporaryDirectory(prefix="native-take-") as work:
            full = None
            if clips.clip_has_audio(path):
                try:
                    full = take_mod.extract_audio(path, os.path.join(work, "clip.wav"), run=self.native_run)
                except take_mod.TakeError as exc:
                    reason = str(exc)
            if full is None:
                words = []
                reason = reason or "the clip has no sound track"
            else:
                transcribe = self.transcribe
                if transcribe is None:
                    transcribe, why = default_transcriber(ctx.settings_env)
                    if transcribe is None:
                        reason = f"{why}: add {' or '.join(media_policy.stt_missing_keys(gating.merged_env(ctx.settings_env))) or 'an STT key'} in Settings"
                if transcribe is not None:
                    ctx.cancel.check()
                    try:
                        words, aligned_by = transcribe(full, language=ec.language, on_log=ctx.on_log,
                                                       cancel=ctx.cancel)
                    except Exception as exc:  # noqa: BLE001 - an STT link that fails leaves the take approximate
                        words, reason = None, f"the transcription failed ({exc})"
            take = native_speech.evaluate_take(line["text"], words, clip_real_s=real, clip_s=clip["clip_s"],
                                               aligned_by=aligned_by)
            if take["state"] == native_speech.TAKE_NO_SPEECH and reason is None and full is not None:
                reason = "nothing was heard in the clip's sound"
            if (take["state"] in native_speech.TAKES_WITH_SPEECH and full is not None
                    and take.get("start_s") is not None and take.get("end_s") is not None):
                written = self._take_audio(shot, line, path, take, work)
                if written is not None:
                    reason = written
        now = llm_call.utc_now()
        if take["state"] == native_speech.TAKE_OK:
            reason = None
        record = take_mod.record(take, clip_sha256=sha, clip_real_s=real, line_id=line_id, now=now, reason=reason)
        shot["assets"]["clip"] = dict(clip, native_speech=record)
        end = take["end_s"] if take["state"] in (native_speech.TAKE_OK, native_speech.TAKE_MISMATCH) else None
        seconds = native_speech.shot_seconds(real, speaks=True, end_s=end, floor_s=self._native_floor_s())
        seconds = round(min(real, max(self._native_floor_s(), seconds)), 3)
        shot["duration_s"] = seconds
        self.write_board()
        self.save()
        ctx.on_log(take_mod.summary_line(shot_id, dict(take, reason=reason)))
        return dict(take, reason=reason)

    def _take_audio(self, shot, line, path, take, work):
        """The take's speech as the line's audio (``assets/voice/line_NN.wav``)
        and its sidecar, the line's timing measured from it; None, or why it
        could not be written (the line then keeps no audio)."""
        ec = self.ec
        line_id = line["line_id"]
        start, end = float(take["start_s"]), float(take["end_s"])
        try:
            segment = take_mod.extract_audio(path, os.path.join(work, "line.wav"), start_s=start, end_s=end,
                                             run=self.native_run)
            dest = ec.store.episode_asset_path(ec.story_id, ec.ep, "voice", voice_lines.asset_name(line_id, "wav"),
                                               create=True)
            side = sidecar_path(ec, line_id)
        except take_mod.TakeError as exc:
            return f"its speech could not be cut from the clip ({exc})"
        except KeyError:
            return f"{voice_lines.VOICE_ASSETS}/{voice_lines.asset_name(line_id, 'wav')} is not a real file"
        voice = take_mod.pinned_voice(ec, line["speaker"])
        if voice is None:
            return f"{voice_lines.speaker_name(ec, line['speaker'])} has no pinned voice: the take cannot be its line"
        _atomic_copy(segment, dest)
        take_mod.write_json(side, take_mod.sidecar(take, link=(shot["assets"].get("clip") or {}).get("link"),
                                                   duration_s=end - start))
        line["timing"] = take_mod.line_timing(line, duration_s=end - start, voice=voice,
                                              audio_rel=f"{voice_lines.VOICE_ASSETS}/"
                                                        f"{voice_lines.asset_name(line_id, 'wav')}")
        self.drop_other_take(line_id, "wav")
        return None

    def retake_shot(self, shot, *, video, row, image_link=None) -> None:
        """One more take of a speaking clip whose take missed its line (plan
        22): an agent story buys it -- a fresh seed, the same link, the same
        gates and journal as any clip, booked -- when the shot has retakes
        left (``speech_retake.max_per_shot``) and the episode's retake budget
        (``speech_retake.cap_usd``, ``assets.json``'s ``speech_retakes``)
        holds its price; the new clip is taken again (never retaken past
        that). A Studio story's flagged shot waits for its human: the feed
        names its regenerate target."""
        ec, ctx = self.ec, self.ctx
        shot_id = shot["shot_id"]
        target = clip_target(ec.ep, shot_id)
        if (ec.story.get("generation_profile") or {}).get("mode") != defaults.MODE_AGENT:
            ctx.on_log(f"🔁 Shot {shot_id}: regenerate {target!r} for another take, or keep this one.")
            return
        doc = _read_assets_doc(ec)
        budget = clips.retake_budget(ec, doc)
        count = int(budget["shots"].get(shot_id) or 0)
        price = float(((video.get("speech") or {}).get("speech_price")) or 0.0)
        est = round(int(row["clip_s"]) * price, 4)
        if count >= budget["max_per_shot"]:
            ctx.on_log(f"🔁 Shot {shot_id}: no retake left ({count} of {budget['max_per_shot']}): regenerate "
                       f"{target!r} for another take.")
            return
        if budget["spent_usd"] + est > budget["cap_usd"] + 1e-9:
            ctx.on_log(f"🔁 Shot {shot_id}: a retake (est ${est:.3f}) would bring the retakes to "
                       f"${budget['spent_usd'] + est:.2f} of their ${budget['cap_usd']:.2f}: regenerate {target!r} "
                       "for another take.")
            return
        previous = copy.deepcopy(shot["assets"].get("clip") or {})
        seed = entities.fresh_seed()
        shot["assets"]["clip"] = dict(previous, pending={"seed": seed, "note": None,
                                                         "requested_at": llm_call.utc_now()})
        self.write_board()
        ctx.on_log(f"🔁 Shot {shot_id}: retake {count + 1} of {budget['max_per_shot']} (seed {seed}, est ${est:.3f})")
        flags = clips.shot_flags(shot, doc)
        try:
            self.before_clip([shot_id])
            record, info = self.make_clip(shot, video=video, clip_s=row["clip_s"], est_usd=est, seed=seed, note=None,
                                          flags=flags, tier=clips.tier_of(ec), image_link=image_link)
        except ClipFailed as exc:
            shot["assets"]["clip"] = previous
            shot["assets"]["video"] = clips.clip_rel(shot_id)
            self.write_board()
            ctx.on_log(f"✖ Shot {shot_id}'s retake failed ({exc.reason}); its first take is kept.")
            return
        record["retakes"] = count + 1
        self.apply_clip(shot, record, info, video=video)
        spent = float(record["est_usd"]) if info.get("fresh") else 0.0
        self._book_retake(shot_id, count + 1, spent, budget)
        self.take_summary()["retaken"].append(shot_id)
        self.native_take_shot(shot)

    def _book_retake(self, shot_id, count, spent, budget) -> None:
        """``assets.json``'s ``speech_retakes``: one more retake of *shot_id*
        and what it cost."""
        ec = self.ec
        now = llm_call.utc_now()
        doc = episode_common.read_episode(ec, ASSETS_DOC) or _minimal_assets_doc(ec, now)
        record = dict(doc.get("speech_retakes") or {})
        record.update(max_per_shot=budget["max_per_shot"], cap_usd=budget["cap_usd"],
                      spent_usd=round(float(record.get("spent_usd") or 0.0) + spent, 4))
        record["shots"] = dict(record.get("shots") or {}, **{shot_id: count})
        doc["speech_retakes"] = record
        try:
            ec.store.write_episode_doc(ec.story_id, ec.ep, ASSETS_DOC, doc, now=now)
        except (schemas.SchemaError, ValueError, KeyError) as exc:
            self.ctx.on_log(f"⚠️ {ASSETS_DOC} could not record the retake ({exc}).")

    def take_total(self) -> None:
        """The run's one line about the native takes (plan 22), when it took any."""
        takes = (self.video or {}).get("takes")
        if not takes or not (takes["ok"] or takes["flagged"] or takes["approximate"]):
            return
        parts = []
        if takes["ok"]:
            parts.append(f"{len(takes['ok'])} speak their line")
        if takes["flagged"]:
            parts.append(f"{len(takes['flagged'])} flagged ({_and(item['shot_id'] for item in takes['flagged'])})")
        if takes["approximate"]:
            parts.append(f"{len(takes['approximate'])} unchecked, subtitles approximate")
        if takes["retaken"]:
            parts.append(f"{len(takes['retaken'])} retaken")
        self.ctx.on_log(f"🗣 Native takes: {', '.join(parts)}.")

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
        # The user's per-shot overrides (phase 6 stage 7) and modes (plan 25 stage 1): carried as they are.
        # Plan 25 stage 2: and the platform and model the handoff remembers.
        for key in ("shots", "shot_modes", "handoff"):
            if (previous or {}).get(key):
                doc[key] = previous[key]
        # A v2 episode's keyframe verdicts and approval (phase 7 stage 6b) and
        # its keyframe auto-fix records (phase 8 stage B): carried as they are
        # -- the approval goes stale by its fingerprint, never cleared here.
        for key in (judge.KEYFRAME_VERDICTS, judge.KEYFRAMES_APPROVED, KEYFRAME_FIXES, KEYFRAME_FIX_BUDGET,
                    SPEECH_RETAKES):
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

    def updated_doc(self, doc, changes) -> dict:
        """*doc* (``assets.json`` as last written) with *changes* (``{key:
        value}``; an empty or None value drops the key) written at once;
        returns it as written (or *doc* when nothing moved)."""
        ec = self.ec
        new = copy.deepcopy(doc)
        for key, value in changes.items():
            if value:
                new[key] = value
            else:
                new.pop(key, None)
        if new == doc:
            return doc
        try:
            return ec.store.write_episode_doc(ec.story_id, ec.ep, ASSETS_DOC, new, now=llm_call.utc_now())
        except (schemas.SchemaError, ValueError, KeyError) as exc:
            raise StepFailed(f"Episode {ec.ep}'s {ASSETS_DOC} could not be written ({exc}).") from None

    def check_keyframe_items(self, items, held, *, before_call):
        """``judge.check_keyframes`` over *items*, every verdict written into
        ``held["doc"]`` (``assets.json`` as last written) after each new one
        and once more at the end -- the episode's other verdicts kept.
        Returns J2's summary."""
        def write(found):
            verdicts = dict(held["doc"].get(judge.KEYFRAME_VERDICTS) or {})
            verdicts.update(found)
            held["doc"] = self.updated_doc(held["doc"], {judge.KEYFRAME_VERDICTS: verdicts})

        found, summary = judge.check_keyframes(
            self.ctx, self.ec, items, held["doc"].get(judge.KEYFRAME_VERDICTS) or {},
            env=self.ctx.settings_env, ledger=self.gates.ledger, step=STEP, before_call=before_call,
            on_verdict=write, adapters=self.tools.adapters, transport=self.tools.transport,
            context=keyframe_context(self.ec, self.storyboard, ledger=self.ledger_now()))
        write(found)
        return summary

    def judge_keyframes(self, doc) -> dict:
        """J2 on a v2 episode (module docstring): every current keyframe
        without a current verdict, ``keyframe_verdicts`` written after each
        new one (a stop keeps what was judged) and once more at the end when
        it moved. Returns ``assets.json`` as it stands after."""
        held = {"doc": doc}
        self.keyframe_check = self.check_keyframe_items(keyframe_items(self.ec, self.storyboard, doc), held,
                                                        before_call=self.before_vision)
        return held["doc"]

    def judge_regenerated(self, shot_id):
        """J2 after a shot-image regenerate of a v2 episode (phase 8 stage B,
        DEC-239's follow-up): that shot and the one after it (its previous
        keyframe changed), their verdicts written to ``assets.json``; never
        an auto-fix -- the human asked for this very note. Returns J2's
        summary, or None when the episode has no ``assets.json`` yet (the
        assets step checks them). A step budget that cannot fit a check
        leaves it to the next assets run, said."""
        ec = self.ec
        doc = _read_assets_doc(ec)
        if doc is None:
            return None
        ids = [shot["shot_id"] for shot in self.storyboard["shots"]]
        index = ids.index(shot_id)
        link = recorded_image_link(doc)
        items = [item for item in (keyframe_item(ec, self.storyboard, position, link=link)
                                   for position in (index, index + 1)) if item is not None]
        try:
            return self.check_keyframe_items(items, {"doc": doc}, before_call=self.before_fix_vision)
        except _FixStopped as exc:
            self.ctx.on_log(f"👁 Keyframe check (J2) left to the next assets run: {exc}.")
            return None

    # ------------------------------------------- the keyframe auto-fix (stage B)

    def before_fix_vision(self, remaining) -> None:
        """A J2 call of the auto-fix starts only while it still fits the step
        budget; else the fix stops (:class:`_FixStopped`), the step goes on.
        The cancel token first."""
        self.ctx.cancel.check()
        if not self.fits(judge.STORY_VISION_CALL_SECONDS):
            raise _FixStopped(f"the step's {int(self.budget.limit // 60)}-minute budget cannot fit the keyframe "
                              f"check (J2) of {_and(remaining)}")

    def fix_link_info(self):
        """The episode's image link as :func:`image_quote` takes it, or None."""
        if self.link is None:
            return None
        info = self.link_info or {}
        return {"link": self.link, "source": "record" if self.link_kept else "derived",
                "since": info.get("since"), "switched_from": info.get("switched_from")}

    def fix_gate(self, settings, spent):
        """Why the auto-fix asks for no other redraw now, or None (the
        cancel token raises): the step budget cannot fit a redraw and its two
        checks; the episode's image link is gone; the image cannot be made
        (``allow_paid`` off for a paid link...); its estimate would go past
        the episode's fix budget (*spent* of ``cap_usd``) or a cap -- the
        gates every image of the run meets (:func:`image_quote`,
        :func:`spending_caps`), then ``make_image``'s own at the call."""
        ec, ctx = self.ec, self.ctx
        ctx.cancel.check()
        if not self.fits(STORY_IMAGE_CALL_SECONDS + 2 * judge.STORY_VISION_CALL_SECONDS):
            return (f"the step's {int(self.budget.limit // 60)}-minute budget cannot fit another redraw and its "
                    "check")
        if self.link_gone is not None:
            return self.gone_reason()
        quote = image_quote(ec, 1, env=ctx.settings_env, story_spent=self.gates.spent(), adapters=self.tools.adapters,
                            transport=self.tools.transport, storyboard=self.storyboard,
                            link_info=self.fix_link_info())
        if not quote["ready"]:
            gone = (quote.get("sticky") or {}).get("gone")
            return str((gone or {}).get("message") or quote["message"]).strip().rstrip(".")
        est = float(quote["est_usd"] or 0.0) if quote["route_class"] == "paid" else 0.0
        if spent + est > settings["cap_usd"] + 1e-9:
            return (f"the episode's keyframe fix budget is spent (${spent:.2f} of ${settings['cap_usd']:.2f}; a "
                    f"redraw is est ${est:.3f})")
        _caps, over = spending_caps(ec, est, env=ctx.settings_env, ledger=self.gates.ledger)
        return over

    def fix_keyframes(self, doc) -> dict:
        """The keyframe auto-fix of a v2 episode (phase 8 stage B): when the
        story's budget profile has ``keyframe_fix`` and the keyframes are not
        approved (an approval -- "anyway" included -- is the human's: never
        redrawn under it), each shot whose current verdict fails, in
        storyboard order, is redrawn with a fresh seed and a note made of its
        verdict (:func:`correction_note`) and checked again with the shot
        after it (whose previous keyframe changed), until it passes or used
        its ``max_redraws_per_shot``. A locked shot, or one made with a note
        of the user's (a regenerate), is left as it is. Stops -- the step
        goes on -- once the episode's fix budget (``cap_usd``), a cap or the
        paid gates refuse, the step budget cannot fit a redraw, or J2 cannot
        check (:meth:`fix_gate`); a cancel stops the step. ``keyframe_fixes``
        and ``keyframe_fix_budget`` are written after each redraw. Returns
        ``assets.json`` as it stands after."""
        ec, ctx, board = self.ec, self.ctx, self.storyboard
        settings = media_policy.keyframe_fix(ec.story)
        check = self.keyframe_check
        if (settings is None or not settings["max_redraws_per_shot"] or check is None or check["unavailable"]
                or keyframes_state(ec, board, doc) == "current"):
            return doc
        if self.link_info is None:
            self.resolve_link(announce=False)
        held = {"doc": doc, "fixes": copy.deepcopy(doc.get(KEYFRAME_FIXES) or {}),
                "spent": float((doc.get(KEYFRAME_FIX_BUDGET) or {}).get("spent_usd") or 0.0)}
        summary = {"fixed": [], "gave_up": [], "flagged": [], "redraws": 0, "spent_usd": 0.0, "stopped": None,
                   "redrawn": []}
        for index in range(len(board["shots"])):
            self.fix_shot(index, settings, held, summary)
            if summary["stopped"]:
                break
        doc = held["doc"]
        verdicts = doc.get(judge.KEYFRAME_VERDICTS) or {}
        for shot, _path, sha, _prev_id, _prev_path, prev_sha in keyframe_items(ec, board, doc):
            shot_id, entry = shot["shot_id"], verdicts.get(shot["shot_id"])
            if not judge.verdict_current(entry, sha, prev_sha):
                continue
            if judge.verdict_passed(entry):
                if shot_id in summary["redrawn"]:
                    summary["fixed"].append(shot_id)
            elif (held["fixes"].get(shot_id) or {}).get("gave_up"):
                summary["gave_up"].append(shot_id)
            else:
                summary["flagged"].append(shot_id)
        summary["spent_usd"] = round(summary["spent_usd"], 4)
        summary["message"] = fix_message(summary, settings)
        self.keyframe_fix = summary
        if summary["redrawn"] or summary["gave_up"] or summary["flagged"]:
            ctx.on_log(f"🛠 Keyframe auto-fix: {summary['message']}")
        return doc

    def save_fixes(self, held, settings) -> None:
        """``keyframe_fixes`` and ``keyframe_fix_budget`` written as they stand."""
        budget = {"max_redraws_per_shot": settings["max_redraws_per_shot"], "cap_usd": settings["cap_usd"],
                  "spent_usd": round(held["spent"], 4)} if held["fixes"] else None
        held["doc"] = self.updated_doc(held["doc"], {KEYFRAME_FIXES: held["fixes"], KEYFRAME_FIX_BUDGET: budget})

    def fix_shot(self, index, settings, held, summary) -> None:
        """:meth:`fix_keyframes` on shot *index*: redrawn and checked again
        while its current verdict fails and it has redraws left."""
        ec, ctx = self.ec, self.ctx
        shot = self.storyboard["shots"][index]
        shot_id = shot["shot_id"]
        most = settings["max_redraws_per_shot"]
        if own_keyframe(ec.story, shot, held["doc"]):
            # Plan 25 stage 1: a keyframe the human makes is never redrawn by the app.
            return
        while True:
            item = keyframe_item(ec, self.storyboard, index, link=recorded_image_link(held["doc"]))
            if item is None or shot["assets"].get("locked"):
                return
            sha, prev_sha = item[2], item[5]
            entry = (held["doc"].get(judge.KEYFRAME_VERDICTS) or {}).get(shot_id)
            if not judge.verdict_current(entry, sha, prev_sha) or judge.verdict_passed(entry):
                return
            record = held["fixes"].get(shot_id)
            if record is None or sha not in [step["image_sha256"] for step in record["history"]]:
                # Another keyframe than the fix judged: a new start -- unless a note of the user's made it.
                if shot["assets"].get("note"):
                    return
                record = {"redraws": 0, "spent_usd": 0.0, "gave_up": False,
                          "history": [fix_history_entry(sha, entry, note=None, at=entry["checked_at"])]}
                held["fixes"][shot_id] = record
            if record["redraws"] >= most:
                if not record["gave_up"]:
                    record["gave_up"] = True
                    self.save_fixes(held, settings)
                return
            stop = self.fix_gate(settings, held["spent"])
            if stop:
                summary["stopped"] = stop
                self.save_fixes(held, settings)
                return
            note = correction_note(ec.entities, entry, shot)
            seed = entities.fresh_seed()
            shot["assets"]["pending"] = {"seed": seed, "note": note, "requested_at": llm_call.utc_now()}
            self.write_board()
            ctx.on_log(f"🛠 Shot {shot_id} flagged by the keyframe check ({judge.verdict_text(entry)}): redraw "
                       f"{record['redraws'] + 1} of {most} (seed {seed})")
            try:
                made = self.make_image(shot, seed=seed, note=note)
            except ShotFailed as exc:
                # The request is kept (pending): the next run asks for the same image.
                self.shot_failures[shot_id] = exc.failures
                self.failed.append((f"shot {shot_id}", shot_target(ec.ep, shot_id), exc.reason))
                ctx.on_log(f"✖ Shot {shot_id}'s redraw failed: {exc.reason}")
                if self.link_gone is not None:
                    summary["stopped"] = self.gone_reason()
                self.save_fixes(held, settings)
                return
            cost = 0.0 if made["_cached"] else float(made["est_usd"] or 0.0)
            self.apply_image(shot, made)
            new_sha = _sha256_file(shot_image_path(ec, shot))
            record["redraws"] += 1
            record["spent_usd"] = round(record["spent_usd"] + cost, 4)
            record["history"].append(fix_history_entry(new_sha, None, note=note, at=llm_call.utc_now(),
                                                       unchecked="not checked yet"))
            record["history"] = record["history"][-schemas.KEYFRAME_FIX_HISTORY_MAX:]
            held["spent"] = round(held["spent"] + cost, 4)
            summary["redraws"] += 1
            summary["spent_usd"] += cost
            if shot_id not in summary["redrawn"]:
                summary["redrawn"].append(shot_id)
            self.save_fixes(held, settings)
            # Checked again, with the shot after it: its previous keyframe changed.
            items = [found for found in (keyframe_item(ec, self.storyboard, position,
                                                       link=recorded_image_link(held["doc"]))
                                         for position in (index, index + 1)) if found is not None]
            try:
                checked = self.check_keyframe_items(items, held, before_call=self.before_fix_vision)
            except _FixStopped as exc:
                summary["stopped"] = str(exc)
                return
            entry = (held["doc"].get(judge.KEYFRAME_VERDICTS) or {}).get(shot_id)
            current = judge.verdict_current(entry, new_sha, prev_sha)
            why = checked["unavailable"] or ("its replies failed validation twice" if shot_id in checked["failed"]
                                            else None)
            record["history"][-1] = fix_history_entry(new_sha, entry if current else None, note=note,
                                                      at=record["history"][-1]["at"], unchecked=why)
            self.save_fixes(held, settings)
            if checked["unavailable"]:
                summary["stopped"] = checked["unavailable"]
                return
            if not current:
                return

    # ------------------------------------------------------------------- run

    def stock_fill(self) -> None:
        """Plan 23 stage B8, at the start of the run and before any keyframe: the eligible
        establishing shots of a story with ``stock_cutaways`` on are filled with a stock
        clip (:func:`stock_cutaways.fill`: free, a clip already there is never replaced,
        a shot with no match is generated as usual), the storyboard and the credits
        written when it changed. Free and local: nothing is booked."""
        ec, ctx = self.ec, self.ctx
        if not media_policy.stock_cutaways(ec.story):
            return
        before = copy.deepcopy(self.storyboard["shots"])
        self.stock = stock_cutaways.fill(ec, self.script, self.storyboard, _read_assets_doc(ec),
                                         env=ctx.settings_env, run=self.crop_run, on_log=ctx.on_log)
        if self.storyboard["shots"] != before:
            self.write_board()
        stock_cutaways.write_credits(ec, self.storyboard)
        if self.stock["filled"] or self.stock["missing"]:
            ctx.on_log(f"🎞 Stock cutaways: {len(self.stock['filled'])} shot(s) filled with stock footage, "
                       f"{len(self.stock['missing'])} generated as usual.")

    def run(self) -> dict:
        ec, ctx = self.ec, self.ctx
        self.script, self.storyboard = require_approved(ec)
        align = bool((ctx.params or {}).get(ALIGN_PARAM))
        animate = animate_param(ctx.params)
        gates = self.open_asset_gates()
        try:
            # Before the estimate and the keyframes: a shot filled with stock is neither drawn nor bought.
            self.stock_fill()
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
            # The lines voiced before the Gemini tail guard lose their static
            # first, for free: the measurement, the words and the timing below
            # read the cleaned lines.
            self.guard_kept_tails()
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
                # Phase 8 stage B: the flagged keyframes redrawn (the quality profile's keyframe_fix).
                try:
                    doc = self.fix_keyframes(doc)
                except gencache.JournalError as exc:
                    raise self.journal_failed(exc) from None
            if self.planned_video is not None:
                # Last: the keyframes are final, the SFX/BGM and assets.json written.
                try:
                    doc = self.animate_clips(doc)
                except gencache.JournalError as exc:
                    raise self.journal_failed(exc) from None
                if (self.video or {}).get("takes"):
                    # Plan 22: the takes timed the characters' lines: their words and the SFX follow.
                    doc = self.write_assets_doc()
        finally:
            self.write_ledger_view()
        return self.finish(doc)

    def finish(self, doc) -> dict:
        ec, ctx, board = self.ec, self.ctx, self.storyboard
        if self.uploads is None and self.missing_keyframes:
            # Plan 22 stage 5: the user's own keyframes first; their clips are asked once they are approved.
            self.uploads = uploads_record(ec, self.missing_keyframes)
        link = recorded_image_link(doc)
        states = {shot["shot_id"]: shot_state(ec, shot, link=link, doc=doc) for shot in board["shots"]}
        native = media_policy.native_speech(ec.story)
        unvoiced = [line["line_id"] for scene in self.script["scenes"] for line in scene["lines"]
                    if not voice_lines.is_measured(ec, line) and not (native and voice_lines.spoken_by_clip(ec, line))]
        # Plan 22: a character line of a native-speech story is its clip's take: missing while its clip is
        # still to buy (held for the keyframes' approval: not this run's), a failure once its clip is there.
        untaken = [line["line_id"] for scene in self.script["scenes"] for line in scene["lines"]
                   if native and voice_lines.spoken_by_clip(ec, line) and not voice_lines.is_measured(ec, line)]
        held = bool((self.video or {}).get("hold")) or not animate_param(self.ctx.params)
        for shot in board["shots"] if native else ():
            clip = shot["assets"].get("clip") or {}
            missing = [line_id for line_id in shot["lines"] if line_id in untaken]
            if missing and clip.get("state") == "current" and clip.get("native_speech"):
                self.failed.append((f"take of shot {shot['shot_id']}", clip_target(ec.ep, shot["shot_id"]),
                                    f"its clip speaks no word of line {missing[0]}"))
        missing_cues = sum(1 for cue in doc["sfx"] if cue["state"] == "missing")
        failures = [{"what": what, "target": target, "reason": reason} for what, target, reason in self.failed]
        failures += [{"what": f"line {line_id}", "target": line_target(ec.ep, line_id),
                      "reason": f"{voice_lines.speaker_name(ec, speaker)}: {reason.rstrip('.')}"}
                     for line_id, speaker, reason in self.voice_failed]
        tails = self.tail_summary()
        tail_message = self.tail_message(tails)
        if tail_message:
            ctx.on_log(tail_message)
        ctx.on_log(episode_common.timing_line(self.script))
        if self.uploads is not None and not failures:
            ctx.on_log(f"⏸ Episode {ec.ep}'s assets wait for your clips: {self.uploads['message']}.")
        elif failures:
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
            "lines": dict({"measured": self.measured, "unvoiced": unvoiced}, **({"untaken": untaken} if native else {})),
            "aligned": list(self.aligned),
            "sfx": {"resolved": len(doc["sfx"]) - missing_cues, "missing": missing_cues},
            "bgm": None if doc["bgm"] is None else doc["bgm"]["mood"],
            "failed": failures,
            "complete": not unvoiced and (held or not untaken)
                        and all(shot["assets"].get("locked") or states[shot["shot_id"]] == "current"
                                for shot in board["shots"]),
            "fingerprint": current_fingerprint(ec, board, self.script, doc),
        }
        if tails["checked"]:
            # The Gemini tail guard in this run (lines spoken now, and lines
            # voiced before it, cleaned in place): what it saw and what it cut.
            # A run it saw no line of keeps the summary it always had.
            result["tails"] = tails
        info = self.link_info or {}
        if self.link is not None or info.get("mixed"):
            # A-087: the link the images were asked of, a legacy mix, or the
            # offer when the link went away in this run.
            result["image_link"] = {"link": self.link, "mixed": list(info.get("mixed") or []),
                                    "gone": self.link_gone.as_dict() if self.link_gone else None}
        if self.video is not None:
            # Tier >= 2 (phase 6 stage 8): what the video phase did.
            result["video"] = dict(self.video, gone=self.video_gone.as_dict() if self.video_gone else None)
        if self.uploads is not None:
            # Plan 22 stage 5: the step ends awaiting the human's clips.
            result["state"] = steps_pkg.AWAITING_UPLOADS
            result["uploads"] = dict(self.uploads)
        if self.stock is not None and (self.stock["filled"] or self.stock["missing"] or self.stock["kept"]):
            # Plan 23 stage B8: the shots filled with stock footage, kept from an earlier run, and
            # those with no match (generated as usual).
            result["stock"] = {"filled": list(self.stock["filled"]), "kept": list(self.stock["kept"]),
                               "missing": [dict(item) for item in self.stock["missing"]]}
        if self.keyframe_check is not None:
            # A v2 story (phase 7 stage 6b): what J2 did, and where the keyframes' approval stands.
            result["keyframes"] = dict(self.keyframe_check, approval=keyframes_state(ec, board, doc))
            if self.keyframe_fix is not None:
                # Phase 8 stage B: what the keyframe auto-fix did, with its one-line message.
                result["keyframes"]["fix"] = dict(self.keyframe_fix)
        return result


def uploads_record(ec, missing, *, platform=None) -> dict:
    """``{"state", "count", "missing", "message", "brief", "brief_md"}``: what
    an episode waits for from the human (plan 22 stage 5) -- the shots
    ``brief.missing_clips`` lists, the sentence ("Waiting for 5 clips --
    download the brief") and where the brief is."""
    base = f"/api/stories/{ec.story_id}/episodes/{ec.ep}/brief"
    query = f"?platform={platform}" if platform else ""
    keyframes = sum(1 for item in missing if item.get("kind") == "keyframe")
    return {"state": steps_pkg.AWAITING_UPLOADS, "count": len(missing), "missing": list(missing),
            "clips": len(missing) - keyframes, "keyframes": keyframes,
            "message": brief_step.waiting_sentence(len(missing) - keyframes, keyframes=keyframes),
            "brief": f"{base}.zip{query}", "brief_json": f"{base}{query}"}


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


def _refit_line(shot_id, kind, link, info) -> str:
    """The feed line of a prompt fitted to its link at request time (DEC-249)."""
    label = link if isinstance(link, str) else describe(link)
    cause = (f"its note ({info['note']} words) takes it to {info['from']} words"
             if info["note"] else f"it was built to another budget ({info['from']} words)")
    return (f"ℹ️ Shot {shot_id}'s {kind} prompt: {cause}, over {label}'s budget of {info['budget']}; resolved again "
            f"to {info['to']} words -- its context shortened, nothing of the note cut.")


def _fit_line(shot_id, link, sent, *, kind="clip") -> str:
    """The feed line of a clip (or *kind* ``keyframe``) prompt whose template
    was fitted to its link (plan 26): "Veo accepts 630 words: 812 → 618,
    dropped Chloe, the props"; *link* may be a chain's name."""
    label = link if isinstance(link, str) else describe(link)
    accepts = f"{label} accepts {sent['limit']} words" if sent.get("limit") else f"{label}'s limit"
    return (f"ℹ️ Shot {shot_id}'s {kind} prompt: {accepts}: {sent['full_words']} → {sent['words']}, dropped "
            f"{', '.join(sent['dropped'])}.")


def regenerate_shot_image(ctx, ec, target, shot_id, note, *, tools, refuse) -> dict:
    """``shot:<ep>:<shot_id>`` (kind ``shot_image``): that shot's image again,
    with *note* at the prompt's tail and a fresh seed (DEC-124) -- persisted
    as the shot's ``pending{seed, note, requested_at}`` **before** the call,
    so a retry (this regenerate again with the same note, or the assets step)
    asks for the same image and the generation cache serves or resumes it
    (DEC-154). A locked shot is refused ("unlock it first"). *refuse(reason)*
    is the caller's ``StepFailed`` builder.

    On a v2 story (phase 8 stage B) the new keyframe is checked by J2 with
    the shot after it (:meth:`_Assets.judge_regenerated`; the result's
    ``keyframes``), and never auto-fixed: the human asked for this note."""
    host = _Assets(ctx, ec, tools=tools)
    try:
        host.script, host.storyboard = require_approved(ec)
    except StepFailed as exc:
        raise refuse(str(exc)) from None
    board = host.storyboard
    shot = next((s for s in board["shots"] if s["shot_id"] == shot_id), None)
    if shot is None:
        raise refuse(f"episode {ec.ep}'s storyboard has no shot {shot_id!r} (it has "
                     f"{shots_mod.shot_ids_phrase(board['shots'])}).")
    if shot["assets"].get("locked"):
        raise refuse(f"shot {shot_id} is locked: unlock it first.")
    if own_keyframe(ec.story, shot, _read_assets_doc(ec)):
        raise refuse(f"shot {shot_id}'s keyframe is your own (its mode is 'my own'): upload it, or switch it to "
                     "auto first.")
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
                                                 size=shot_size(ec.story), probe_local=True, transport=tools.transport,
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
        # Phase 8 stage B: a v2 keyframe is checked (J2) with the shot after it -- never auto-fixed.
        checked = host.judge_regenerated(shot_id) if media_policy.is_v2(ec.story) else None
    finally:
        host.write_ledger_view()
    ctx.on_log(f"🔁 Regenerated {target} (seed {shot['assets']['seed']}){_noted(note)}")
    result = {"target": target, "shot": shot_id, "seed": shot["assets"]["seed"],
              "provider": shot["assets"]["provider"], "cached": cached}
    if checked is not None:
        result["keyframes"] = checked
    return result


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
        raise refuse(f"episode {ec.ep}'s storyboard has no shot {shot_id!r} (it has "
                     f"{shots_mod.shot_ids_phrase(board['shots'])}).")
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
        host.speech_link_kept = sticky_link.recorded(doc, sticky_link.VIDEO_SPEECH) is not None
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
            parts = clips.clip_request_parts(ec, shot, host.script, tier=tier, flags=flags, note=note,
                                             link=video["link"])
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
                                          cover=quote.get("cover"),
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
        if media_policy.native_speech(ec.story):
            # Plan 22: the new clip is taken (free); a regenerate is the human's own retake.
            host.native_take_shot(shot)
            if (host.video or {}).get("takes"):
                host.write_assets_doc()
        if media_policy.lipsync(ec.story):
            # DEC-258: a new clip is lipsynced again, on the same gates.
            status = lipsync_step.link_status(gates.merged, tools.adapters, allow_paid=gates.budget.allow_paid)
            host.video["lipsync"] = _lipsync_summary(status)
            if status["available"]:
                host.lipsync_shot(shot, link=status["link"])
            else:
                ctx.on_log(f"👄 No lip-sync: {status['link'] or gen.ENV_NAMES[gen.LIPSYNC]}: {status['reason']}; the "
                           "clip is kept as it is.")
    finally:
        host.write_ledger_view()
    ctx.on_log(f"🔁 Regenerated {target} (seed {seed}){_noted(note)}")
    result = {"target": target, "shot": shot_id, "seed": seed, "link": record["link"], "clip_s": record["clip_s"],
              "cached": info["cached"]}
    lipsync = (shot["assets"].get("clip") or {}).get("lipsync")
    if media_policy.lipsync(ec.story):
        result["lipsync"] = None if lipsync is None else lipsync["state"]
    return result


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
    if voice_lines.spoken_by_clip(ec, line):
        # Plan 22: a native-speech story's character line is its clip's own speech.
        shot = next((item for item in host.storyboard["shots"] if line_id in item["lines"]), None)
        how = f"regenerate {clip_target(ec.ep, shot['shot_id'])!r}" if shot else "regenerate its shot's clip"
        raise refuse(f"line {line_id} is spoken by its own clip, never by a voice: {how} for another take.")
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
