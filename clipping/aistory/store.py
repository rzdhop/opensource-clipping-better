"""The on-disk story workspace and its index (spec 2, 2.1-2.11; phase-1 plan 2,
phase-2 plan 2, phase-3 plan 3, phase-4 plan 2 "Documents").

Layout, under the same ``outputs/`` directory the job store uses::

    outputs/stories.json            # the index (stories_index_v1)
    outputs/stories/<story_id>/
        story.json                  # StoryBible (story_bible_v1)
        style_lock.json             # StyleLock (style_lock_v1)
        concepts.json               # generated concept cards
        style_preview.json          # the style preview strip (style_preview_v1)
        styles/preview/preview_<n>.<ext>   # its images
        season.json                 # SeasonArc (season_arc_v1)
        places_proposal.json        # the proposed places and props (places_proposal_v1)
        characters/<char_id>/
            character.json          # Character (character_v1)
            refs/portrait.png, turnaround.png, expressions.png, extra_<NN>.png
            refs/uploads/<32 hex>.png      # the user's design references
            voice_sample.mp3
            voice_reference.wav     # the character's own voice recording (plan 23 stage B4)
        places/<place_id>/
            place.json              # Place (place_v1)
            refs/variant_<name>.png # variant_day.png is the master plate
        props/<prop_id>/
            prop.json               # Prop (prop_v1)
            refs/image.png
        episodes/ep<NN>/            # NN = 01..99
            script.json             # EpisodeScript (episode_script_v1)
            storyboard.json         # Storyboard (storyboard_v1), each shot's image record included
            assets.json             # word sources, SFX, BGM, the grid approval (episode_assets_v1)
            render_manifest.json    # RenderManifest (render_manifest_v1)
            render_manifest.last_good.json  # the last render that completed (render_manifest_v1)
            metadata_pack.json      # MetadataPack (metadata_pack_v1)
            proposals.json          # N1's proposals for this episode (next_proposals_v1)
            episode_final.mp4, subtitles.ass, cover.jpg, cost_ledger.json  # EPISODE_FILE_NAMES
            assets/voice/line_<NN>.mp3|.wav|.json  # the opt-in voice measurement
            assets/shots/shot_<NN>.png|.jpg|.jpeg|.webp  # each shot's image
            assets/clips/shot_<NN>.mp4  # a shot's clip (tier >= 2, phase 6)
            render/                 # the renderer's working folder: in/, fonts/, cache/, stems/, logs/
        episodes/_discarded/ep<NN>-<UTC stamp>/  # an archived episode (discard_episode), never listed
        cache/gen/                  # the generation cache and journal (providers/gencache.py)
        cost_ledger.json            # what each call cost (ledger.CostLedger)
        activity.log                # one line per thing a step printed

Rules this module keeps:

- A story id is 12 lowercase hex characters and is checked **before** any path
  is built from it, so ``../x`` or an absolute path never reaches the
  filesystem. The per-story folder must also be a real directory directly
  inside ``outputs/stories/`` -- never a symlink, never through one -- which
  is the same rule ``web/api/cleanup.py::contained`` applies to job folders
  (re-implemented here: ``clipping/`` does not import ``web/``).
- Below the story folder the same holds one level at a time: an entity kind,
  an entity id (``schemas.CHAR_ID_PATTERN``...) and a media name
  (``MEDIA_NAME_PATTERNS``) are each checked before they are joined onto a
  path, and every level must be a real directory directly inside the one
  above it. A symlink is kept and never followed.
- An episode number is a real ``int`` in 1..99 (``check_episode``: not a bool,
  not ``"1"``, not ``1.0``), checked before ``ep<NN>`` is built; an episode
  document, asset, file or render-folder name is checked the same way, and
  the ``episodes/`` levels follow the rule above. So do ``cache/gen/``.
- Every JSON write is atomic: a temp file in the same directory, then
  ``os.replace``; a failure leaves the previous file byte-identical and no
  temp file behind. (``outputs/jobs.json`` is written in place; this does not
  copy that.)
- ``status`` is derived from ``approvals`` on every save and never taken from
  the caller, so an edit that clears an approval cannot leave a stale status.
  ``approvals.cast`` and ``approvals.places`` are folded from the entities'
  own ``approved_at`` whenever an entity is written or deleted
  (``recompute_group_approvals``), so they cannot go stale either.
- Episode documents never read or write story.json: writing, reading or
  listing an episode changes neither the story's approvals, its status nor
  its index entry. Deleting the story removes its episodes (their render/
  folders included) and its cache/ with its folder. Discarding an episode
  (``discard_episode``) moves its folder into ``episodes/_discarded/``,
  never deletes it, and clears what outside it speaks for it: its memory
  entry and feedback in ``season.json``, the proposals written from it, its
  ledger rows' share of the per-episode cap.
- Deleting an entity leaves no id pointing at it: a deleted character leaves
  the other characters' ``relationships``, its props' ``owner_char_id``, the
  season arc (its series memory's relationship keys too) and the places
  proposal; a deleted place leaves the characters' ``state.location``. The
  documents touched keep their approvals (bookkeeping, not content).
- A phase-1 ``story.json`` (``approvals`` without ``cast``/``places``/
  ``season``) is read as if those were null and saved with them.
- The index is a cache of the folders. Missing, torn or foreign, it is rebuilt
  from them and the rebuild is printed; a folder that does not hold a valid
  story is skipped and printed, never deleted.
- One re-entrant lock per resolved ``stories/`` root, shared by every
  ``StoryStore`` in the process: the API and the worker thread each build
  their own instance, and the index is a read-modify-write. Processes (the CLI
  next to the server) do not coordinate; writes stay atomic and the index can
  be rebuilt. ``update_doc``/``update_episode_doc`` re-read a document and
  write it back under that lock (phase 5: a step merges its LLM reply into
  ``season.json`` as it is at the write, never as it was before the call).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, NamedTuple

from . import defaults, format_fit, media_policy, schemas, series_memory, subtitle_style, templates
from .ledger import CostLedger

STORY_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")

STORIES_DIRNAME = "stories"
INDEX_FILENAME = "stories.json"
INDEX_SCHEMA = "stories_index_v1"
STORY_SCHEMA = "story_bible_v1"
STORY_FILENAME = "story.json"
ACTIVITY_LOG = "activity.log"

SEASON_DOC = "season.json"
PLACES_PROPOSAL_DOC = "places_proposal.json"
# Phase 7 (A14): a v2 story's knowledge base (``schemas.KNOWLEDGE_SCHEMA``),
# written by the knowledge step; absent on every other story.
KNOWLEDGE_DOC = "knowledge.json"

# The JSON documents of a story that read_doc/write_doc may name. Nothing else:
# a name is never joined onto a path unless it is one of these.
DOC_NAMES = (
    STORY_FILENAME, "style_lock.json", "concepts.json", "style_preview.json",
    SEASON_DOC, PLACES_PROPOSAL_DOC, KNOWLEDGE_DOC,
)

# The documents the store validates itself, on every read and every write
# (on top of any validator a caller passes to write_doc).
DOC_VALIDATORS = {
    SEASON_DOC: schemas.season_arc_errors,
    PLACES_PROPOSAL_DOC: schemas.places_proposal_errors,
    KNOWLEDGE_DOC: schemas.knowledge_errors,
}

# The preview strip's folder, one level at a time, and the files in it.
PREVIEW_DIRS = ("styles", "preview")
PREVIEW_PREFIX = "preview_"
PREVIEW_IMAGE_NAME = re.compile(schemas.PREVIEW_IMAGE_NAME_PATTERN)


class EntityKind(NamedTuple):
    """One kind of entity folder: ``<story>/<kind>/<id>/<filename>``."""

    pattern: "re.Pattern"       # the id, checked (fullmatch) before any path is built
    filename: str               # the document in the entity's folder
    validator: Callable         # schemas.<kind>_errors
    id_field: str               # the document's own id key
    story_list: str             # the story.json list that names the entity


ENTITY_KINDS = {
    "characters": EntityKind(re.compile(schemas.CHAR_ID_PATTERN), "character.json",
                             schemas.character_errors, "char_id", "cast_ids"),
    "places": EntityKind(re.compile(schemas.PLACE_ID_PATTERN), "place.json",
                         schemas.place_errors, "place_id", "place_ids"),
    "props": EntityKind(re.compile(schemas.PROP_ID_PATTERN), "prop.json",
                        schemas.prop_errors, "prop_id", "prop_ids"),
}

# The files an entity keeps besides its document, by where they live, and the
# only names each place may hold (the entity media route serves these and
# nothing else). The patterns of one kind never overlap, so a name alone says
# where its file is.
MEDIA_NAME_PATTERNS = {
    "characters": {
        "refs": re.compile(schemas.CHARACTER_REF_NAME_PATTERN),
        "uploads": re.compile(schemas.UPLOAD_NAME_PATTERN),
        "voice": re.compile(schemas.VOICE_SAMPLE_NAME_PATTERN),
        "voice_reference": re.compile(schemas.VOICE_REFERENCE_NAME_PATTERN),
    },
    "places": {"refs": re.compile(schemas.PLACE_REF_NAME_PATTERN)},
    "props": {"refs": re.compile(schemas.PROP_REF_NAME_PATTERN)},
}
# Each place's folder below the entity's own, one level at a time.
MEDIA_DIRS = {"refs": ("refs",), "uploads": ("refs", "uploads"), "voice": (), "voice_reference": ()}

# An episode's folder is <story>/episodes/ep<NN>/, NN two digits: the "ep"
# bounds of episode_script_v1 and storyboard_v1.
EPISODES_DIRNAME = "episodes"
EPISODE_MIN = 1
EPISODE_MAX = 99
# [0-9], not \d: int() also reads other scripts' digits ("ep٠٥" would be 5).
EPISODE_DIR_NAME = re.compile(r"^ep[0-9]{2}$")
# Where a discarded episode goes (``discard_episode``): episodes/_discarded/
# ep<NN>-<UTC stamp>/, a name no episode scan reads as an episode.
DISCARDED_DIRNAME = "_discarded"
# The proposals written from a discarded episode, kept in its archive.
_ARCHIVED_PROPOSALS = "proposals_for_ep{:02d}.json"
# The story's cost ledger (``ledger.CostLedger``), whose rows of a discarded
# episode are marked.
COST_LEDGER_FILENAME = "cost_ledger.json"

# The documents of an episode that read_episode_doc/write_episode_doc may
# name, and the checks the store runs on every read and every write: the
# self-contained ones only. The checks against the story (cast, places, the
# script a storyboard follows) need the story, so the caller runs them.
EPISODE_SCRIPT_DOC = "script.json"
EPISODE_STORYBOARD_DOC = "storyboard.json"
EPISODE_ASSETS_DOC = "assets.json"
EPISODE_RENDER_MANIFEST_DOC = "render_manifest.json"
EPISODE_METADATA_PACK_DOC = "metadata_pack.json"
EPISODE_PROPOSALS_DOC = "proposals.json"
# Phase 5 stage 8: a copy of the manifest of the last render that completed,
# written by the render runner only then -- the baseline a partial re-render
# is measured against, and the manifest of the episode_final.mp4 on disk.
EPISODE_RENDER_LAST_GOOD_DOC = "render_manifest.last_good.json"
EPISODE_DOC_NAMES = (
    EPISODE_SCRIPT_DOC, EPISODE_STORYBOARD_DOC,
    EPISODE_ASSETS_DOC, EPISODE_RENDER_MANIFEST_DOC, EPISODE_METADATA_PACK_DOC,
    EPISODE_PROPOSALS_DOC, EPISODE_RENDER_LAST_GOOD_DOC,
)
EPISODE_DOC_VALIDATORS = {
    EPISODE_SCRIPT_DOC: schemas.episode_script_errors,
    EPISODE_STORYBOARD_DOC: schemas.storyboard_errors,
    EPISODE_ASSETS_DOC: schemas.episode_assets_errors,
    EPISODE_RENDER_MANIFEST_DOC: schemas.render_manifest_errors,
    EPISODE_METADATA_PACK_DOC: schemas.metadata_pack_errors,
    EPISODE_PROPOSALS_DOC: schemas.next_proposals_errors,
    EPISODE_RENDER_LAST_GOOD_DOC: schemas.render_manifest_errors,
}
# The episode documents that carry created_at/updated_at: every one of them
# (spec 2: every JSON document carries an updated_at).
EPISODE_DOCS_WITH_TIMESTAMPS = (
    EPISODE_SCRIPT_DOC, EPISODE_STORYBOARD_DOC,
    EPISODE_ASSETS_DOC, EPISODE_RENDER_MANIFEST_DOC, EPISODE_METADATA_PACK_DOC,
    EPISODE_PROPOSALS_DOC, EPISODE_RENDER_LAST_GOOD_DOC,
)
# The field that names the episode a document belongs to, which must be its
# folder's: ``ep``, except N1's proposals, which sit in the folder of the
# episode they are *for*.
_EPISODE_FIELDS = {EPISODE_PROPOSALS_DOC: "for_ep"}

# The files an episode keeps in assets/<kind>/, and the only names each kind
# may hold. A shot's image is named by the shot's own number (sh03 ->
# shot_03.<ext>), and so is its clip (phase 6 stage 7: shot_03.mp4). SFX
# and BGM are not copied: assets.json names the shipped files, and the
# renderer stages what it uses into render/in/.
EPISODE_ASSETS_DIRNAME = "assets"
EPISODE_ASSET_KINDS = ("voice", "shots", "clips")
EPISODE_ASSET_NAME_PATTERNS = {
    "voice": re.compile(r"^line_[0-9]{2}\.(mp3|wav|json)$"),
    "shots": re.compile(schemas.SHOT_IMAGE_NAME_PATTERN),
    "clips": re.compile(schemas.SHOT_CLIP_NAME_PATTERN),
}

# Plan 22 stage 5: the shot brief of an episode whose clips are the human's
# own (``steps/brief.py``), in assets/brief/, and the uploaded clips a new
# upload replaced, kept in assets/clips/takes/ -- each folder with its own
# closed list of names (``episode_brief_path``, ``episode_take_path``).
EPISODE_BRIEF_DIR = ("assets", "brief")
EPISODE_BRIEF_NAMES = ("shot_brief.json", "shot_brief.md", "image_brief.json", "image_brief.md")
# Plan 23 stage B8: the credits of an episode's stock cutaways, in assets/ next to the
# folders above (``episode_stock_credits_path``: a closed list of two names).
EPISODE_STOCK_CREDITS_DIR = ("assets",)
EPISODE_STOCK_CREDITS_NAMES = ("stock_credits.json", "stock_credits.txt")
EPISODE_TAKES_DIR = ("assets", "clips", "takes")
EPISODE_TAKE_NAME_PATTERN = re.compile(r"^shot_(0[1-9]|[1-9][0-9]{1,2})\.manual\.[0-9]{8}T[0-9]{6}Z(-[0-9]+)?\.mp4$")

# The files at the top of an episode's folder besides its documents -- the
# renderer's and the metadata step's outputs and the episode's view of the
# cost ledger -- and nothing else: a name is checked against this list
# before any path is built (episode_file_path).
EPISODE_FILE_NAMES = ("episode_final.mp4", "subtitles.ass", "cover.jpg", "cost_ledger.json")

# The renderer's working folder, episodes/ep<NN>/render/, and the only
# folders below it the store hands out: staged inputs (named by their
# content hash), fonts, the per-shot cache, the mix's stems, and each
# command's stdout/stderr (the runner's logs/, phase 4 stage 9).
EPISODE_RENDER_DIRNAME = "render"
EPISODE_RENDER_SUBDIRS = ("in", "fonts", "cache", "stems", "logs")

# The story's generation cache and submit journal, <story>/cache/gen/, one
# level at a time (clipping/providers/gencache.py takes it as its root).
GEN_CACHE_DIRS = ("cache", "gen")

INDEX_FIELDS = ("story_id", "title", "language", "style_template_id", "status", "created_at", "updated_at")

# story.json keys a caller may never change once the story exists.
_FROZEN_KEYS = ("$schema", "story_id", "created_at")

# Each approval moves the story one step, but only on top of the previous one.
_APPROVAL_STEPS = (
    ("concept", "concept_chosen"),
    ("bible", "bible_approved"),
    ("style", "style_approved"),
    ("cast", "cast_approved"),
    ("places", "places_approved"),
    ("season", "ready"),
)

# The approvals phase 2 added. A story.json written by phase 1 has none of
# them; it is read as if they were null and saved with them.
PHASE2_APPROVALS = ("cast", "places", "season")

_PROFILE_CHOICES = {
    "tier": defaults.TIERS,
    "route": defaults.ROUTES,
    "consistency_mode": defaults.CONSISTENCY_MODES,
    "budget_profile": defaults.BUDGET_PROFILES,
    "pipeline": defaults.PIPELINES,
    "video_resolution": defaults.VIDEO_RESOLUTIONS,
    # Plan 21 stage 1: Studio (absent) or agent mode.
    "mode": defaults.STORY_MODES,
    # Plan 22: a native-speech story's speaking-clip model (stage 4), and (stage 5)
    # the switch that makes its images the user's own uploads.
    "speech_model": defaults.SPEECH_MODELS,
    "images": defaults.IMAGE_MODES,
    # Plan 22 stage 2 (DEC-274): v2 (absent) or the brief-faithful v3 prompts.
    "writing": defaults.WRITING_VERSIONS,
    # Plan 23 stage D4: how a character's sheets and body are drawn (absent: three sheets,
    # the style's own body rules).
    "sheet_mode": defaults.SHEET_MODES,
    "body_rule": defaults.BODY_RULES,
    # Plan 23 stage D2: what the cast is made of (a universe of templates/universes.json).
    "universe": defaults.UNIVERSES,
    # Plan 23 stage A9: which provider the image roles try first (absent: fal first).
    "image_preference": defaults.IMAGE_PREFERENCES,
    # Plan 23 stage D6: how a clip's prompt is written (absent: studio, today's prompts).
    "prompt_style": defaults.PROMPT_STYLES,
    # Plan 23 stage D5: the explicit opt-in to appearance variants (absent: sheet_mode decides).
    "variants": defaults.VARIANTS_MODES,
    # Plan 28 stage B1 (DEC-305): "none" -- no generated voice, no narrator (native speech only);
    # absent is "tts". Listed before the frame: aspect and stock_cutaways stay the last two keys.
    "voices": defaults.VOICES_MODES,
    # Plan 23 stage B7: the output frame (absent: 9:16), chosen at creation only -- never clearable.
    "aspect": defaults.ASPECTS,
    # Plan 23 stage B8: the opt-in to stock cutaways (absent: off), patchable any time.
    "stock_cutaways": defaults.STOCK_CUTAWAYS_MODES,
}
# Plan 22: the optional keys a partial profile may clear by sending null.
_PROFILE_CLEARABLE = ("speech_model", "images", "sheet_mode", "body_rule", "universe", "image_preference", "prompt_style",
                      "variants", "stock_cutaways")

_INDEX_ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "story_id": {"type": "string", "pattern": schemas.STORY_ID_PATTERN},
        "title": {"type": "string"},
        "language": {"type": "string"},
        "style_template_id": {"type": ["string", "null"]},
        "status": {"type": "string"},
        "created_at": {"type": "string"},
        "updated_at": {"type": "string"},
    },
    "required": list(INDEX_FIELDS),
}

# mkstemp creates its file 0600. A story workspace sits in a bind mount the
# human reads and edits from the host, as they do outputs/jobs.json (written
# with a plain open(), so the usual 0644).
_FILE_MODE = 0o644

_CREATE_ATTEMPTS = 8

_INDEX_MISSING = "the index is missing"

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def default_outputs_dir() -> str:
    """``<repo>/outputs`` -- the directory ``web/api/store.py`` keeps jobs.json in."""
    return os.path.join(_PROJECT_ROOT, "outputs")


def new_story_id() -> str:
    return uuid.uuid4().hex[:12]


def is_story_id(value) -> bool:
    """Whether *value* is a well-formed story id. ``fullmatch``, not ``match``:
    ``$`` also matches before a trailing newline."""
    return isinstance(value, str) and STORY_ID_PATTERN.fullmatch(value) is not None


def check_episode(ep) -> int:
    """*ep* itself once it is an episode number: an ``int`` in
    ``EPISODE_MIN..EPISODE_MAX``. ``type() is int``, not ``isinstance``: True
    is an int equal to 1. ``"1"`` and ``1.0`` are refused too. KeyError
    otherwise, like a malformed story id, and before any path is built."""
    if type(ep) is not int or not EPISODE_MIN <= ep <= EPISODE_MAX:
        raise KeyError(ep)
    return ep


def derive_status(approvals) -> str:
    """The story status that *approvals* amount to.

    ``draft -> concept_chosen -> bible_approved -> style_approved ->
    cast_approved -> places_approved -> ready``. Only a contiguous prefix
    counts: a style approval without a bible approval is not an approval (the
    bible it was given against no longer stands), and an approved season on
    top of a cast that is no longer approved is not ``ready``.
    """
    status = defaults.STATUSES[0]
    if not isinstance(approvals, dict):
        return status
    for key, reached in _APPROVAL_STEPS:
        value = approvals.get(key)
        if not (isinstance(value, str) and value):
            break
        status = reached
    return status


# ----------------------------------------------------------------- helpers

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _contained(root: str, name, *, want_dir: bool):
    """The real path of *name* directly inside *root*, or None if it is
    anything else. The rules of ``web/api/cleanup.py::contained``."""
    if not isinstance(name, str) or name in ("", ".", ".."):
        return None
    if "/" in name or "\\" in name or os.path.splitdrive(name)[0] or os.path.isabs(name):
        return None
    base = os.path.realpath(root)
    path = os.path.join(base, name)
    if os.path.islink(path):
        return None
    real = os.path.realpath(path)
    if real == base or os.path.dirname(real) != base:
        return None
    if want_dir and not os.path.isdir(real):
        return None
    if not want_dir and not os.path.isfile(real):
        return None
    return real


def _atomic_write_json(path: str, data) -> None:
    """Write *data* to *path* so a reader sees the old file or the new one.

    The ledger's pattern (``ledger.CostLedger._write``): a temp file in the
    same directory, then ``os.replace``; the temp file is removed on any
    failure. Keys keep their insertion order -- story.json is meant to be read
    by a human, top to bottom.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=directory, prefix=".story-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
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


def _atomic_copy(src: str, dest: str) -> None:
    """Copy *src* to *dest* so a reader sees the old file or the new one: the
    pattern of ``_atomic_write_json`` for bytes (temp file in *dest*'s own
    directory, fsync, 0644, ``os.replace``; the temp file never outlives a
    failure)."""
    handle, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), prefix=".story-", suffix=".tmp")
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


def _descend(parent: str, parts, *, create: bool, label: str) -> str:
    """The real path of ``parent/parts[0]/parts[1]/...``, each level a real
    directory directly inside the one above it (``_contained``). *create*
    makes a missing level with ``os.mkdir``, one at a time, and checks it like
    any other. KeyError(*label*) for a missing level (without *create*) or a
    level that is anything but a real directory."""
    for part in parts:
        if create and not os.path.lexists(os.path.join(parent, part)):
            try:
                os.mkdir(os.path.join(parent, part))
            except FileExistsError:
                pass
        real = _contained(parent, part, want_dir=True)
        if real is None:
            raise KeyError(label)
        parent = real
    return parent


def _upgrade_phase1(doc) -> None:
    """In memory, before validation: a phase-1 story.json -- ``approvals``
    holding none of ``PHASE2_APPROVALS`` -- gains them as null. Anything else
    is left exactly as it is, for the validator to judge."""
    approvals = doc.get("approvals") if isinstance(doc, dict) else None
    if isinstance(approvals, dict) and not any(key in approvals for key in PHASE2_APPROVALS):
        for key in PHASE2_APPROVALS:
            approvals[key] = None


def _entity_kind(kind, eid) -> EntityKind:
    """The kind's rules, once *kind* is one of ``ENTITY_KINDS`` and *eid* a
    well-formed id of that kind; KeyError otherwise. Touches nothing."""
    spec = ENTITY_KINDS.get(kind) if isinstance(kind, str) else None
    if spec is None:
        raise KeyError(kind)
    if not isinstance(eid, str) or spec.pattern.fullmatch(eid) is None:
        raise KeyError(eid)
    return spec


def _media_location(kind, name) -> str:
    """Where a file named *name* lives in an entity of *kind* (a key of
    ``MEDIA_DIRS``); KeyError for any name the kind may not hold."""
    patterns = MEDIA_NAME_PATTERNS.get(kind) if isinstance(kind, str) else None
    if patterns is not None and isinstance(name, str):
        for location, pattern in patterns.items():
            if pattern.fullmatch(name):
                return location
    raise KeyError(name)


def _episode_folder(ep) -> str:
    """``ep<NN>`` for an episode number already checked by ``check_episode``."""
    return f"ep{ep:02d}"


def _episode_number(name):
    """The episode a folder named *name* holds, or None for any other name
    (``_discarded`` among them)."""
    if not isinstance(name, str) or EPISODE_DIR_NAME.fullmatch(name) is None:
        return None
    ep = int(name[2:])
    return ep if EPISODE_MIN <= ep <= EPISODE_MAX else None


def _archive_stamp(now) -> str:
    """*now* (an ISO timestamp) as a folder-name stamp in UTC,
    ``20261002T110000Z``; the clock's own time when it cannot be read."""
    try:
        moment = datetime.fromisoformat(str(now))
    except ValueError:
        moment = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _is_regular(path) -> bool:
    return not os.path.islink(path) and os.path.isfile(path)


def _episode_doc_errors(name, doc, ep) -> list:
    """The episode document's own checks (``EPISODE_DOC_VALIDATORS``), then
    its ``ep`` (``for_ep`` for the proposals, ``_EPISODE_FIELDS``) against
    the folder it is read from or written to."""
    errors = EPISODE_DOC_VALIDATORS[name](doc)
    field = _EPISODE_FIELDS.get(name, "ep")
    if not errors and doc[field] != ep:
        errors = [f"$.{field}: {doc[field]!r} does not match its folder {_episode_folder(ep)!r}"]
    return errors


def _still_or_now(value, now):
    """An approval that still holds keeps its timestamp; a new one is *now*."""
    return value if isinstance(value, str) and value else now


def _merge_generation_profile(partial) -> dict:
    profile = defaults.default_generation_profile()
    if partial is None:
        return profile
    if not isinstance(partial, dict):
        raise ValueError(f"generation_profile must be an object, not {type(partial).__name__}")
    for key, value in partial.items():
        choices = _PROFILE_CHOICES.get(key)
        if choices is None:
            raise ValueError(
                f"generation_profile has no field {key!r} (known: {', '.join(_PROFILE_CHOICES)})")
        if value is None and key in _PROFILE_CLEARABLE:
            profile.pop(key, None)
            continue
        # type() as well as membership: True == 1 and 1.0 == 1 in Python.
        if type(value) is not type(choices[0]) or value not in choices:
            raise ValueError(
                f"generation_profile.{key} must be one of {list(choices)}, not {value!r}")
        profile[key] = value
    if profile.get("pipeline") == defaults.PIPELINE_V2 and profile["consistency_mode"] != "references":
        # DEC-221: a v2 story's images are edits of its references; it has no
        # prompt-only mode to fall back to.
        raise ValueError(
            "generation_profile.consistency_mode must be references on a v2 story (pipeline v2), "
            f"not {profile['consistency_mode']!r}: it never falls back to prompt-only consistency")
    if profile.get("voices") == defaults.VOICES_NONE and not defaults.speaks_natively(profile):
        # Plan 28 stage B1 (DEC-305): only a story whose clips speak its lines can go without voices.
        raise ValueError(
            "generation_profile.voices can be none only on a native-speech story (the characters speak in "
            "their own clips); this story's lines are read by generated voices, so it needs voices tts")
    return profile


def check_universe(profile, style_template_id) -> None:
    """``ValueError`` naming both when *profile*'s ``universe`` is not one the
    style *style_template_id* lists (plan 23 stage D2: ``universes`` of its
    template; a style that lists none, or no style yet, accepts none). No
    ``universe`` in the profile: nothing to check."""
    chosen = (profile or {}).get("universe")
    if chosen is None:
        return
    allowed = []
    if style_template_id:
        try:
            allowed = list(templates.load_style(style_template_id).get("universes") or [])
        except KeyError:
            allowed = []
    if chosen not in allowed:
        shown = ", ".join(allowed) if allowed else "none"
        raise ValueError(
            f"universe {chosen!r} does not fit the style {style_template_id or '(no style chosen)'!r}, "
            f"which accepts: {shown}; pick a universe of that list or another style")


def _index_entry(doc: dict) -> dict:
    return {key: doc[key] for key in INDEX_FIELDS}


# ------------------------------------------------------------------- locks

_locks_guard = threading.Lock()
_locks: dict = {}


def _lock_for(root: str) -> threading.RLock:
    key = os.path.realpath(root)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.RLock()
        return lock


# ------------------------------------------------------------------- store

def _drop_from_knowledge(doc, kind, eid):
    """Remove the deleted entity *eid* of *kind* from a ``knowledge.json``
    document (in place) and move its ``rev`` on; a sentence of what was
    removed, or None when the document does not name it (nothing changes)."""
    where = set()
    for entry in doc.get("timeline") or []:
        for beat in entry["beats"]:
            if kind == "characters":
                if eid in beat["who"]:
                    beat["who"] = [cid for cid in beat["who"] if cid != eid]
                    where.add("timeline")
                if eid in beat["knows_after"]:
                    del beat["knows_after"][eid]
                    where.add("timeline")
            elif kind == "places" and beat["place_id"] == eid:
                beat["place_id"] = None
                where.add("timeline")
            elif kind == "props" and eid in beat["objects"]:
                beat["objects"] = [pid for pid in beat["objects"] if pid != eid]
                where.add("timeline")
    if kind == "props" and eid in (doc.get("props_registry") or []):
        doc["props_registry"] = [pid for pid in doc["props_registry"] if pid != eid]
        where.add("props_registry")
    ledger = doc.get("ledger_seed") or {}
    if kind == "characters" and eid in ledger:
        del ledger[eid]
        where.add("ledger_seed")
    for state in ledger.values():
        if kind == "places" and state["location"] == eid:
            state["location"] = None
            where.add("ledger_seed")
        elif kind == "props" and eid in state["possessions"]:
            state["possessions"] = [pid for pid in state["possessions"] if pid != eid]
            where.add("ledger_seed")
    if not where:
        return None
    doc["rev"] += 1
    return f"{eid} removed from {', '.join(sorted(where))}; the knowledge base must be approved again"


class StoryStore:
    def __init__(self, outputs_dir, *, id_factory=new_story_id, on_log=print):
        self.outputs_dir = os.path.abspath(str(outputs_dir))
        self.root = os.path.join(self.outputs_dir, STORIES_DIRNAME)
        self.index_path = os.path.join(self.outputs_dir, INDEX_FILENAME)
        self._id_factory = id_factory
        self._on_log = on_log
        self._lock = _lock_for(self.root)

    # -------------------------------------------------------------- paths

    @staticmethod
    def _check_id(story_id) -> None:
        if not is_story_id(story_id):
            raise KeyError(story_id)

    @staticmethod
    def _check_doc_name(name) -> None:
        if not isinstance(name, str) or name not in DOC_NAMES:
            raise ValueError(f"not a story document: {name!r} (allowed: {', '.join(DOC_NAMES)})")

    @staticmethod
    def _check_episode_doc_name(name) -> None:
        if not isinstance(name, str) or name not in EPISODE_DOC_NAMES:
            raise ValueError(
                f"not an episode document: {name!r} (allowed: {', '.join(EPISODE_DOC_NAMES)})")

    def story_dir(self, story_id) -> str:
        """The real path of the story's folder; KeyError if the id is malformed
        or the folder is not a real directory directly inside ``stories/``."""
        self._check_id(story_id)
        real = _contained(self.root, story_id, want_dir=True)
        if real is None:
            raise KeyError(story_id)
        return real

    def _label(self, story_id) -> str:
        return f"outputs/{STORIES_DIRNAME}/{story_id}/"

    # ----------------------------------------------------------- previews

    def preview_dir(self, story_id, *, create=False) -> str:
        """The real path of the story's ``styles/preview/`` folder.

        Each level must be a real directory directly inside the one above it
        -- never a symlink, never reached through one (``_contained``, the
        rule of the story folder itself). *create* makes a missing level with
        ``os.mkdir``, one level at a time, and checks it like any other.
        KeyError for an unknown story, a missing level (without *create*), or
        a level that is anything but a real directory.
        """
        with self._lock:
            return _descend(self.story_dir(story_id), PREVIEW_DIRS, create=create,
                            label=f"{STORIES_DIRNAME}/{story_id}/{'/'.join(PREVIEW_DIRS)}")

    def preview_file(self, story_id, name) -> str:
        """The real path of one preview image, to serve it.

        KeyError for anything but an existing regular file named like a
        preview image (``schemas.PREVIEW_IMAGE_NAME_PATTERN``), directly inside
        the story's real ``styles/preview/`` folder, and not a symlink. The
        name is checked before any path is built from it.
        """
        if not isinstance(name, str) or PREVIEW_IMAGE_NAME.fullmatch(name) is None:
            raise KeyError(name)
        self._check_id(story_id)
        with self._lock:
            real = _contained(self.preview_dir(story_id), name, want_dir=False)
        if real is None:
            raise KeyError(name)
        return real

    def clear_previews(self, story_id) -> int:
        """Remove the previous preview's files; returns how many went.

        Every ``preview_*`` entry directly inside the story's ``styles/preview/``
        that is a file or a symlink -- a symlink is unlinked, its target is
        never touched -- and nothing else: not a directory, not a file of
        another name, nothing outside that folder.
        """
        with self._lock:
            folder = self.preview_dir(story_id)
            removed = 0
            with os.scandir(folder) as entries:
                for entry in entries:
                    if not entry.name.startswith(PREVIEW_PREFIX):
                        continue
                    if entry.is_symlink() or entry.is_file(follow_symlinks=False):
                        os.unlink(entry.path)
                        removed += 1
            return removed

    # ------------------------------------------------------------ logging

    def _log(self, messages) -> None:
        # Called after the lock is released: on_log may be a tee into a job's
        # activity feed, which takes locks of its own.
        for message in messages:
            try:
                self._on_log(message)
            except Exception:
                pass

    # -------------------------------------------------------------- story

    def _read_story(self, story_id: str) -> dict:
        """Load and validate story.json; KeyError if there is no story."""
        directory = self.story_dir(story_id)
        path = os.path.join(directory, STORY_FILENAME)
        name = f"{STORIES_DIRNAME}/{story_id}/{STORY_FILENAME}"
        if os.path.islink(path):
            raise schemas.SchemaError(name, ["story.json is a symlink; it is never followed"])
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            raise KeyError(story_id) from None
        except ValueError as exc:
            raise schemas.SchemaError(name, [f"not valid JSON: {exc}"]) from None
        _upgrade_phase1(doc)
        errors = schemas.story_bible_errors(doc)
        if not errors and doc["story_id"] != story_id:
            errors = [f"$.story_id: {doc['story_id']!r} does not match its folder {story_id!r}"]
        if errors:
            raise schemas.SchemaError(name, errors)
        return doc

    def _save_story(self, doc: dict, *, now) -> list:
        """Validate, write story.json atomically, update the index entry.
        Returns the log lines to print once the lock is released."""
        errors = schemas.story_bible_errors(doc)
        if errors:
            raise schemas.SchemaError(STORY_SCHEMA, errors)
        directory = self.story_dir(doc["story_id"])
        _atomic_write_json(os.path.join(directory, STORY_FILENAME), doc)
        return self._upsert_index(doc, now=now)

    def create(self, *, language, seed_text=None, style_template_id=None, generation_profile=None,
               episode_template_id=None, recipe=None, now) -> dict:
        """Create a draft story. There is no default language (``defaults``).

        *episode_template_id* (plan 20 stage 1): the story's own episode
        template, one of ``defaults.EPISODE_TEMPLATE_IDS`` (else
        ``ValueError``); None, the pipeline's default
        (``defaults.episode_template_for``). Plan 28 stage A4: on a story
        that speaks in its own clips, a format no plan of its clips fits is
        a ``ValueError`` (``format_fit.FORMAT_REFUSAL``), nothing created;
        named none, the default when it fits, else the first that does
        (``format_fit.choose_format``).

        *recipe* (plan 32 stage 1): the recipe the story is made with (a
        preset names it, ``presets.apply``), stored as ``recipe``; None
        (the default) is stored as null. An id of the shape
        ``schemas.RECIPE_ID_PATTERN`` (else ``ValueError``); which recipes
        exist is checked from plan 32 stage 2 on."""
        if not isinstance(language, str) or language not in schemas.LANGUAGES:
            raise ValueError(f"language must be one of {list(schemas.LANGUAGES)}, not {language!r}")
        if seed_text is not None and not isinstance(seed_text, str):
            raise ValueError(f"seed_text must be a string or null, not {type(seed_text).__name__}")
        if style_template_id is not None and style_template_id not in templates.list_style_ids():
            raise ValueError(
                f"unknown style template {style_template_id!r} "
                f"(shipped: {', '.join(templates.list_style_ids())})")
        if episode_template_id is not None and episode_template_id not in defaults.EPISODE_TEMPLATE_IDS:
            raise ValueError(
                f"unknown episode template {episode_template_id!r} "
                f"(shipped: {', '.join(defaults.EPISODE_TEMPLATE_IDS)})")
        if recipe is not None and not (isinstance(recipe, str) and re.fullmatch(schemas.RECIPE_ID_PATTERN, recipe)):
            raise ValueError(f"recipe must be an id of lowercase letters, digits and underscores, not {recipe!r}")
        profile = _merge_generation_profile(generation_profile)
        # Plan 23 stage D2: the universe is one the style accepts.
        check_universe(profile, style_template_id)
        # Plan 23 stage B7: a frame the profile's links can make (a v2 story; no 1:1 on Veo or Flow).
        refusal = media_policy.aspect_refusal(profile)
        if refusal:
            raise ValueError(refusal)
        # Plan 22 stage 2 (DEC-274): every story created from now on writes
        # on the brief-faithful v3 prompts (the concepts/bible steps still
        # gate on a non-empty seed_text too) -- unless the caller named a
        # writing version of its own (a test fixture, or a future explicit
        # "v2" choice).
        profile.setdefault("writing", defaults.WRITING_V3)
        # Plan 28 stage B1 (DEC-305): a native-speech story created from now on has no generated
        # voice -- its characters speak in their own clips -- unless the caller named "tts".
        if defaults.speaks_natively(profile):
            profile.setdefault("voices", defaults.VOICES_NONE)
        # Plan 28 stage A4 (DEC-305): the format is one this story's clips can fit -- checked with the
        # zero-call oracle (format_fit, timing.plan_floor_preview) on a story that speaks in its own
        # clips; an impossible pick is refused in one sentence and nothing is created.
        episode_template_id = format_fit.choose_format(profile, episode_template_id, language=language,
                                                       style_template_id=style_template_id)

        approvals = {key: None for key, _ in _APPROVAL_STEPS}
        doc = {
            "$schema": STORY_SCHEMA,
            "story_id": None,
            "title": "",
            "language": language,
            "seed_text": seed_text,
            "concept_id": None,
            "concept": None,
            "logline": None,
            "premise": None,
            "tone": None,
            "genre_tags": [],
            "world": None,
            "themes_and_values": [],
            "audience": None,
            "why_come_back": [],
            "cast_ids": [],
            "place_ids": [],
            "prop_ids": [],
            "style_template_id": style_template_id,
            # The one chosen at creation (plan 20 stage 1), else a v2 story
            # starts on the v2 template (DEC-227); a legacy one as before;
            # a native-speech one on the confrontation (plan 28 stage A4: one
            # that fits, format_fit.choose_format).
            "episode_template_id": episode_template_id,
            # Plan 32 stage 1: the recipe a preset named, else null.
            "recipe": recipe,
            "generation_profile": profile,
            # Plan 28 stage B1 (DEC-305): every new story opens with the
            # narrator off, whatever its pipeline (phase 7 stage 6c's "on for
            # v2" is now opt-in, a PATCH; never on a no-voice story).
            "narrator": {"enabled": False, "voice": None},
            "approvals": approvals,
            "status": derive_status(approvals),
            "created_at": now,
            "updated_at": now,
        }

        with self._lock:
            for _ in range(_CREATE_ATTEMPTS):
                story_id = self._id_factory()
                if not is_story_id(story_id):
                    raise ValueError(f"id_factory returned a malformed story id: {story_id!r}")
                doc["story_id"] = story_id
                # Before anything exists on disk, so a refused story leaves nothing.
                errors = schemas.story_bible_errors(doc)
                if errors:
                    raise schemas.SchemaError(STORY_SCHEMA, errors)
                os.makedirs(self.root, exist_ok=True)
                try:
                    os.mkdir(os.path.join(self.root, story_id))
                except FileExistsError:
                    continue
                break
            else:
                raise RuntimeError(f"no free story id after {_CREATE_ATTEMPTS} attempts")
            try:
                messages = self._save_story(doc, now=now)
            except BaseException:
                self._discard_new_folder(story_id)
                raise
        self._log(messages)
        return copy.deepcopy(doc)

    def _discard_new_folder(self, story_id: str) -> None:
        """Undo the mkdir of a create whose story.json never landed. rmdir, not
        rmtree: only a folder left empty goes. One whose story.json did land
        (the index write failed) is kept for the next rebuild."""
        try:
            os.rmdir(os.path.join(self.root, story_id))
        except OSError:
            pass

    def get(self, story_id) -> dict:
        """The validated story. KeyError for a malformed or unknown id;
        ``SchemaError`` for a story.json that does not validate (never repaired)."""
        self._check_id(story_id)
        with self._lock:
            return self._read_story(story_id)

    def update(self, story_id, mutate, *, now) -> dict:
        """Apply *mutate* (it edits the dict in place) and save.

        ``status`` is re-derived from ``approvals`` whatever *mutate* did to it,
        ``updated_at`` becomes *now*, and ``$schema``/``story_id``/``created_at``
        may not change. Nothing is written unless the result validates.
        """
        self._check_id(story_id)
        with self._lock:
            current = self._read_story(story_id)
            doc = copy.deepcopy(current)
            mutate(doc)
            for key in _FROZEN_KEYS:
                if key not in doc or doc[key] != current[key]:
                    raise ValueError(f"{key} cannot be changed ({current[key]!r})")
            doc["status"] = derive_status(doc.get("approvals"))
            doc["updated_at"] = now
            messages = self._save_story(doc, now=now)
        self._log(messages)
        return copy.deepcopy(doc)

    def set_subtitle_style(self, story_id, style, *, now) -> dict:
        """Set (or clear) the story's own subtitle look (plan 23 stage B5): a
        ``subtitle_style`` object, or ``None`` / ``{}`` to remove the key.

        Checked first (``subtitle_style.validate``: the ranges, the shipped
        fonts, the 4.5 contrast between the text and its outline or box):
        ``SchemaError`` named ``"subtitle_style"`` with every error, nothing
        written. Written under the story lock like every story mutation
        (:meth:`update`): atomic, ``updated_at`` moved, the approvals left
        alone -- the look is render-only, editable after the style lock
        froze. Returns the story."""
        if style is not None:
            errors = subtitle_style.validate(style)
            if errors:
                raise schemas.SchemaError("subtitle_style", errors)
        stored = subtitle_style.normalise(style)

        def mutate(doc):
            if stored is None:
                doc.pop("subtitle_style", None)
            else:
                doc["subtitle_style"] = stored

        return self.update(story_id, mutate, now=now)

    # ----------------------------------------------------- other documents

    def read_doc(self, story_id, name):
        """One of the story's JSON documents, or None if it does not exist yet.
        A document of ``DOC_VALIDATORS`` that does not validate raises
        ``SchemaError`` (never repaired)."""
        self._check_doc_name(name)
        self._check_id(story_id)
        if name == STORY_FILENAME:
            return self.get(story_id)
        with self._lock:
            path = os.path.join(self.story_dir(story_id), name)
            label = f"{STORIES_DIRNAME}/{story_id}/{name}"
            if os.path.islink(path):
                raise schemas.SchemaError(label, [f"{name} is a symlink; it is never followed"])
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except FileNotFoundError:
                return None
            except ValueError as exc:
                raise schemas.SchemaError(label, [f"not valid JSON: {exc}"]) from None
        if not isinstance(doc, dict):
            raise schemas.SchemaError(label, ["$: expected a JSON object"])
        own = DOC_VALIDATORS.get(name)
        if own is not None:
            errors = own(doc)
            if errors:
                raise schemas.SchemaError(label, errors)
        return doc

    def write_doc(self, story_id, name, doc, *, now, validator=None) -> dict:
        """Write one of the story's documents (not story.json: use ``update``).

        ``updated_at`` becomes *now*; the document's own validator
        (``DOC_VALIDATORS``) and *validator*, when given, each return a list of
        errors and a non-empty one refuses the write. The story's own
        ``updated_at`` (story.json and the index) moves to *now* as well.
        """
        self._check_doc_name(name)
        if name == STORY_FILENAME:
            raise ValueError("story.json is written through update(), which derives its status")
        self._check_id(story_id)
        if not isinstance(doc, dict) or not isinstance(doc.get("$schema"), str) or not doc["$schema"]:
            raise ValueError(f"{name} must be an object with a '$schema' string")
        new = copy.deepcopy(doc)
        new["updated_at"] = now
        for check in (DOC_VALIDATORS.get(name), validator):
            if check is not None:
                errors = check(new)
                if errors:
                    raise schemas.SchemaError(name, errors)
        with self._lock:
            directory = self.story_dir(story_id)
            story = self._read_story(story_id)  # a document belongs to a valid story
            _atomic_write_json(os.path.join(directory, name), new)
            story["status"] = derive_status(story["approvals"])
            story["updated_at"] = now
            messages = self._save_story(story, now=now)
        self._log(messages)
        return copy.deepcopy(new)

    def update_doc(self, story_id, name, mutate, *, now, validator=None):
        """Re-read one of the story's documents and write it back changed,
        all under the story lock (re-read-then-write, phase 5 stage 4).

        *mutate* is handed a copy of the document as it is on disk now
        (None when there is none yet) and returns the document to write, or
        None to write nothing; it may raise to refuse. The write is
        :meth:`write_doc`'s (validated, atomic, the story's ``updated_at``
        moves). Every other writer that takes the lock -- another
        ``update_doc``, an entity delete's cleanup (``delete_entity``) -- is
        serialised with this one, so neither loses the other's change: a
        caller that spent minutes on an LLM reply merges it into the document
        as it is *now*, never into the one it read before the call. Returns
        what was written, or the document as read when nothing was. The lock
        is re-entrant: *mutate* may read (or update) another document."""
        with self._lock:
            current = self.read_doc(story_id, name)
            new = mutate(copy.deepcopy(current) if current is not None else None)
            if new is None:
                return current
            return self.write_doc(story_id, name, new, now=now, validator=validator)

    # ------------------------------------------------- the knowledge base

    def read_knowledge(self, story_id):
        """The story's ``knowledge.json``, or None (a story without a
        knowledge base: every stored story). ``SchemaError`` as ``read_doc``."""
        return self.read_doc(story_id, KNOWLEDGE_DOC)

    def _knowledge_references(self, story_id):
        """The validator a knowledge write runs on top of the document's own:
        every id it names is one of the story's entities, each ledger's
        wardrobe set one of that character's look (none without a look)."""
        characters = self.list_entities(story_id, "characters")
        refs = {
            "char_ids": [doc["char_id"] for doc in characters],
            "place_ids": [doc["place_id"] for doc in self.list_entities(story_id, "places")],
            "prop_ids": [doc["prop_id"] for doc in self.list_entities(story_id, "props")],
            "wardrobe_sets": {doc["char_id"]: [item["id"] for item in (doc.get("look") or {}).get("wardrobe_sets", ())]
                              for doc in characters},
        }
        return lambda doc: schemas.knowledge_reference_errors(doc, **refs)

    def write_knowledge(self, story_id, doc, *, now) -> dict:
        """Write ``knowledge.json`` (atomically, as :meth:`write_doc`): its own
        checks (``schemas.knowledge_errors``) and every id against the story's
        entities; either refused raises ``SchemaError`` and writes nothing."""
        with self._lock:
            return self.write_doc(story_id, KNOWLEDGE_DOC, doc, now=now,
                                  validator=self._knowledge_references(story_id))

    def update_knowledge(self, story_id, mutate, *, now, bump_rev=True):
        """:meth:`update_doc` for ``knowledge.json`` (re-read, then write under
        the story lock), checked as :meth:`write_knowledge`.

        Every write moves ``rev`` on by one (1 for the first), whatever
        *mutate* set (stage 5b, DEC-228): an approval names the ``rev`` it was
        given at (``approved_rev``), so any later write leaves it out of date.
        The approval itself writes with ``bump_rev=False``."""

        def write(current):
            before = current["rev"] if current is not None else 0
            new = mutate(current)
            if new is not None and bump_rev:
                new["rev"] = before + 1
            return new

        with self._lock:
            return self.update_doc(story_id, KNOWLEDGE_DOC, write, now=now,
                                   validator=self._knowledge_references(story_id))

    def update_episode_doc(self, story_id, ep, name, mutate, *, now, validator=None):
        """:meth:`update_doc` for an episode document (``read_episode_doc`` /
        ``write_episode_doc``), under the same story lock."""
        with self._lock:
            current = self.read_episode_doc(story_id, ep, name)
            new = mutate(copy.deepcopy(current) if current is not None else None)
            if new is None:
                return current
            return self.write_episode_doc(story_id, ep, name, new, now=now, validator=validator)

    def append_activity(self, story_id, line) -> None:
        """Append one line to the story's activity.log. Best effort: never
        raises (like the job store's persistence), and a malformed id or a
        missing story is ignored."""
        try:
            if not is_story_id(story_id):
                return
            text = " ".join(str(line).splitlines())
            with self._lock:
                path = os.path.join(self.story_dir(story_id), ACTIVITY_LOG)
                if os.path.islink(path):
                    return
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(text + "\n")
        except Exception:
            pass

    # ----------------------------------------------------------- entities

    def _entity_label(self, story_id, kind, eid) -> str:
        return f"{self._label(story_id)}{kind}/{eid}/"

    def entity_dir(self, story_id, kind, eid, *, create=False) -> str:
        """The real path of ``<story>/<kind>/<eid>/``.

        The story id, the kind (``ENTITY_KINDS``) and the entity id (its kind's
        pattern) are checked before any path is built; each level must be a
        real directory directly inside the one above it (``_descend``).
        *create* makes the missing levels. KeyError otherwise.
        """
        self._check_id(story_id)
        _entity_kind(kind, eid)
        with self._lock:
            return _descend(self.story_dir(story_id), (kind, eid), create=create,
                            label=self._entity_label(story_id, kind, eid))

    def _media_dir(self, story_id, kind, eid, location, *, create) -> str:
        self._check_id(story_id)
        _entity_kind(kind, eid)
        if location not in MEDIA_NAME_PATTERNS[kind]:
            raise KeyError(f"{kind} keep no {location}")
        with self._lock:
            # The entity itself must exist: only the folders below it are made.
            entity = self.entity_dir(story_id, kind, eid)
            label = self._entity_label(story_id, kind, eid) + "".join(f"{p}/" for p in MEDIA_DIRS[location])
            return _descend(entity, MEDIA_DIRS[location], create=create, label=label)

    def refs_dir(self, story_id, kind, eid, *, create=False) -> str:
        """The real path of the entity's ``refs/`` (``entity_dir`` rules; the
        entity folder must already exist, *create* makes ``refs/`` only)."""
        return self._media_dir(story_id, kind, eid, "refs", create=create)

    def uploads_dir(self, story_id, kind, eid, *, create=False) -> str:
        """The real path of the entity's ``refs/uploads/`` (characters only)."""
        return self._media_dir(story_id, kind, eid, "uploads", create=create)

    def _load_entity(self, directory, story_id, kind, eid, spec) -> dict:
        """The validated document in *directory*. KeyError if there is none;
        SchemaError for a symlink, unreadable or invalid JSON, a document that
        does not validate, or one whose id is not its folder's."""
        label = f"{self._entity_label(story_id, kind, eid)}{spec.filename}"
        path = os.path.join(directory, spec.filename)
        if os.path.islink(path):
            raise schemas.SchemaError(label, [f"{spec.filename} is a symlink; it is never followed"])
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            raise KeyError(eid) from None
        except ValueError as exc:
            raise schemas.SchemaError(label, [f"not valid JSON: {exc}"]) from None
        except OSError as exc:
            raise schemas.SchemaError(label, [f"unreadable ({type(exc).__name__}: {exc})"]) from None
        errors = spec.validator(doc)
        if not errors and doc[spec.id_field] != eid:
            errors = [f"$.{spec.id_field}: {doc[spec.id_field]!r} does not match its folder {eid!r}"]
        if errors:
            raise schemas.SchemaError(label, errors)
        return doc

    def read_entity(self, story_id, kind, eid) -> dict:
        """One entity's validated document. KeyError for a malformed id, an
        unknown story or entity, or a missing document; ``SchemaError`` for one
        that does not validate (never repaired)."""
        self._check_id(story_id)
        spec = _entity_kind(kind, eid)
        with self._lock:
            return self._load_entity(self.entity_dir(story_id, kind, eid), story_id, kind, eid, spec)

    def _list_entities_locked(self, story_id, kind):
        """``(documents sorted by created_at, log lines)``. A folder named like
        an id whose document is missing or invalid is skipped and reported,
        never deleted; other names are ignored."""
        spec = ENTITY_KINDS[kind]
        parent = self.story_dir(story_id)
        if not os.path.lexists(os.path.join(parent, kind)):
            return [], []
        kind_dir = _contained(parent, kind, want_dir=True)
        if kind_dir is None:
            return [], [f"Skipped {self._label(story_id)}{kind}/: not a real directory, never followed"]
        docs, messages = [], []
        for name in sorted(os.listdir(kind_dir)):
            if spec.pattern.fullmatch(name) is None:
                continue
            label = self._entity_label(story_id, kind, name)
            directory = _contained(kind_dir, name, want_dir=True)
            if directory is None:
                messages.append(f"Skipped {label}: not a real directory inside {kind}/")
                continue
            try:
                docs.append(self._load_entity(directory, story_id, kind, name, spec))
            except KeyError:
                messages.append(f"Skipped {label}: no {spec.filename}")
            except schemas.SchemaError as exc:
                detail = "; ".join(exc.errors)
                if len(detail) > 300:
                    detail = detail[:297] + "..."
                messages.append(f"Skipped {label}: {spec.filename} is invalid ({detail})")
        docs.sort(key=lambda doc: (doc["created_at"], doc[spec.id_field]))
        return docs, messages

    def list_entities(self, story_id, kind) -> list:
        """Every valid entity of *kind*, oldest first (``created_at``). A folder
        whose document does not validate is skipped and printed (``on_log``),
        never deleted. KeyError for a malformed id, an unknown story or kind."""
        self._check_id(story_id)
        if not isinstance(kind, str) or kind not in ENTITY_KINDS:
            raise KeyError(kind)
        with self._lock:
            docs, messages = self._list_entities_locked(story_id, kind)
        self._log(messages)
        return docs

    def _fold_group_approvals(self, story_id, story, *, now) -> list:
        """Set ``approvals.cast`` and ``approvals.places`` on *story* (in place)
        from the entities on disk; ``season`` is left alone. Returns the log
        lines of any entity skipped while reading them.

        ``cast``: at least one lead or support character, and every one of
        them approved (guests and recurring characters never block, and never
        approve a cast on their own). ``places``: at least one place, and every
        place and every prop approved. A folder that could not be read blocks
        its group: it may be exactly the lead or place nobody approved. A group
        that still qualifies keeps its timestamp; one that newly does gets
        *now*; one that no longer does is cleared.
        """
        listed, unreadable, messages = {}, {}, []
        for kind in ENTITY_KINDS:
            listed[kind], found = self._list_entities_locked(story_id, kind)
            unreadable[kind] = bool(found)
            messages.extend(found)
        core = [doc for doc in listed["characters"] if doc["role"] in schemas.CAST_APPROVAL_ROLES]
        cast = bool(core) and not unreadable["characters"] and all(doc["approved_at"] for doc in core)
        places = (bool(listed["places"]) and not (unreadable["places"] or unreadable["props"])
                  and all(doc["approved_at"] for doc in listed["places"] + listed["props"]))
        approvals = story["approvals"]
        approvals["cast"] = _still_or_now(approvals.get("cast"), now) if cast else None
        approvals["places"] = _still_or_now(approvals.get("places"), now) if places else None
        return messages

    def recompute_group_approvals(self, story_id, *, now) -> dict:
        """Re-fold ``approvals.cast``/``approvals.places`` from the entities
        (``_fold_group_approvals``) through ``update``, so ``status`` is
        re-derived; returns the saved story. ``write_entity`` and
        ``delete_entity`` already do this in their own save."""
        messages = []

        def fold(doc):
            messages.extend(self._fold_group_approvals(story_id, doc, now=now))

        saved = self.update(story_id, fold, now=now)
        self._log(messages)
        return saved

    def _save_story_after_entity(self, story, *, now) -> list:
        """Fold the group approvals, re-derive status, bump and save the story
        (under the lock). Returns the log lines."""
        messages = self._fold_group_approvals(story["story_id"], story, now=now)
        story["status"] = derive_status(story["approvals"])
        story["updated_at"] = now
        return messages + self._save_story(story, now=now)

    def write_entity(self, story_id, kind, doc, *, now, validator=None) -> dict:
        """Write one entity's document; returns what was written.

        The id is the document's own (``char_id``/``place_id``/``prop_id``),
        checked against its kind's pattern before any path is built;
        ``updated_at`` becomes *now*; the kind's validator and *validator*
        (when given) must both pass, or nothing is written. The write is
        atomic. The story -- which must itself be valid -- gains the id in
        ``cast_ids``/``place_ids``/``prop_ids`` if missing, its group
        approvals are re-folded and its ``updated_at`` becomes *now*.
        """
        self._check_id(story_id)
        spec = ENTITY_KINDS.get(kind) if isinstance(kind, str) else None
        if spec is None:
            raise KeyError(kind)
        if not isinstance(doc, dict):
            raise ValueError(f"a {kind} document must be an object, not {type(doc).__name__}")
        eid = doc.get(spec.id_field)
        if not isinstance(eid, str) or spec.pattern.fullmatch(eid) is None:
            raise schemas.SchemaError(spec.filename, [f"$.{spec.id_field}: {eid!r} is not a {kind} id"])
        new = copy.deepcopy(doc)
        new["updated_at"] = now
        for check in (spec.validator, validator):
            if check is not None:
                errors = check(new)
                if errors:
                    raise schemas.SchemaError(f"{kind}/{eid}/{spec.filename}", errors)
        with self._lock:
            story = self._read_story(story_id)  # an entity belongs to a valid story
            directory = self.entity_dir(story_id, kind, eid, create=True)
            _atomic_write_json(os.path.join(directory, spec.filename), new)
            if eid not in story[spec.story_list]:
                story[spec.story_list].append(eid)
            messages = self._save_story_after_entity(story, now=now)
        self._log(messages)
        return copy.deepcopy(new)

    def delete_entity(self, story_id, kind, eid, *, now=None) -> dict:
        """Remove one entity's folder, its id from the story, and every
        reference to it from the story's other documents.

        Returns ``{"removed": [...], "kept": [...]}`` like ``delete``. The
        folder is removed only as a real directory directly inside the story's
        real ``<kind>/``; a symlink (or anything else) in its place is kept and
        reported, never followed. The id leaves ``cast_ids``/``place_ids``/
        ``prop_ids``, the documents that name it are cleaned
        (:meth:`_drop_references_locked`, one line printed per document), and
        the group approvals are re-folded in the one save of the story, so
        deleting the last place (say) clears ``approvals.places``. KeyError
        for a malformed id, an unknown story, or an entity with neither a
        folder nor a place in the story's list.

        A character document is rewritten here under the story's lock only;
        a caller that may race an upload holds ``uploads._ENTRIES_LOCK``
        around this call (``workflow.delete_entity`` does), the order every
        character writer takes the two locks in.
        """
        self._check_id(story_id)
        spec = _entity_kind(kind, eid)
        now = now or _utc_now()
        report = {"removed": [], "kept": []}
        label = self._entity_label(story_id, kind, eid)
        with self._lock:
            story = self._read_story(story_id)
            parent = self.story_dir(story_id)
            kind_path = os.path.join(parent, kind)
            kind_dir = _contained(parent, kind, want_dir=True)
            if kind_dir is None and os.path.lexists(kind_path):
                report["kept"].append(f"{self._label(story_id)}{kind}/ (not a real directory, never followed)")
            path = os.path.join(kind_dir, eid) if kind_dir is not None else None
            has_folder = path is not None and os.path.lexists(path)
            listed = eid in story[spec.story_list]
            if not (has_folder or listed or report["kept"]):
                raise KeyError(eid)

            real = _contained(kind_dir, eid, want_dir=True) if kind_dir is not None else None
            if real is not None:
                try:
                    shutil.rmtree(real)
                    report["removed"].append(label)
                except OSError as exc:
                    report["kept"].append(f"{label} ({exc})")
            elif has_folder:
                why = "a symlink, never followed" if os.path.islink(path) else "not a directory"
                report["kept"].append(f"{label} ({why})")

            story[spec.story_list] = [item for item in story[spec.story_list] if item != eid]
            messages = self._drop_references_locked(story_id, kind, eid, now=now)
            messages += self._save_story_after_entity(story, now=now)
        self._log(messages)
        return report

    def _drop_references_locked(self, story_id, kind, eid, *, now) -> list:
        """Remove every reference to the deleted entity *eid* of *kind* from
        the story's other documents (under the lock); returns the log lines.

        A character: its id leaves every other character's ``relationships``;
        a prop it owned has no owner (``owner_char_id`` null); it leaves
        ``season.json`` (each arc entry's ``characters``, each list of
        ``series_memory.introduced``, every relationship key naming it in
        ``series_memory.relationship_state`` and in each memory entry's
        ``relationship_deltas``, the derived fields then re-folded from the
        entries -- ``series_memory.drop_character``) and
        ``places_proposal.json`` (a proposed prop's ``owner`` becomes null).
        A place: a character located there (``state.location``) is located
        nowhere. And, any kind (stage 5b, DEC-228), ``knowledge.json``: the id
        leaves every beat (``who``, ``objects``, ``knows_after``, a
        ``place_id`` becomes null), the props registry and the ledger seed (a
        character's entry, a location, a possession) -- and, unlike the
        others, the knowledge base's ``rev`` moves, so its approval no longer
        holds (the episode gate asks for it again): what was approved named
        something that no longer exists.

        This is bookkeeping, not content: a touched document **keeps its
        approval** (``approved_at``, the season's too) -- what was approved
        is unchanged, only a pointer to something that no longer exists is
        gone -- and its ``updated_at`` becomes *now*. Each touched document is
        validated and written atomically, one line printed for it ("Cleaned
        <path>: ..."); one that would not validate is left as it is and
        reported. A document that cannot be read (invalid, a symlink) is never
        repaired or overwritten: it is reported ("Kept <path> as it is: ...")
        and left. A document that does not name *eid* is not written.
        """
        messages = []

        def entities(of_kind, edit):
            spec = ENTITY_KINDS[of_kind]
            # A folder that cannot be read is reported by the fold that follows.
            docs, _skipped = self._list_entities_locked(story_id, of_kind)
            for doc in docs:
                what = edit(doc)
                if what:
                    oid = doc[spec.id_field]
                    folder = self.entity_dir(story_id, of_kind, oid)
                    label = f"{self._entity_label(story_id, of_kind, oid)}{spec.filename}"
                    write(os.path.join(folder, spec.filename), label, doc, spec.validator, what)

        def document(name, edit):
            label = f"{self._label(story_id)}{name}"
            try:
                doc = self.read_doc(story_id, name)
            except schemas.SchemaError as exc:
                first = str(exc.errors[0]) if exc.errors else "it does not validate"
                messages.append(f"Kept {label} as it is: it cannot be read ({first}), so it may still name {eid}")
                return
            if doc is None:
                return
            what = edit(doc)
            if what:
                write(os.path.join(self.story_dir(story_id), name), label, doc, DOC_VALIDATORS[name], what)

        def write(path, label, doc, validator, what):
            doc["updated_at"] = now
            errors = validator(doc)
            if errors:
                messages.append(f"Kept {label} as it is: without {eid} it would not validate ({errors[0]})")
                return
            _atomic_write_json(path, doc)
            messages.append(f"Cleaned {label}: {what}")

        if kind == "characters":
            def relationships(doc):
                if eid in doc["relationships"]:
                    del doc["relationships"][eid]
                    return f"relationship with {eid} removed"
                return None

            def owner(doc):
                if doc["owner_char_id"] == eid:
                    doc["owner_char_id"] = None
                    return f"owner {eid} cleared"
                return None

            def season(doc):
                episodes = []
                for entry in doc["arc"]:
                    if eid in entry["characters"]:
                        entry["characters"] = [cid for cid in entry["characters"] if cid != eid]
                        episodes.append(str(entry["ep"]))
                introduced = []
                for key, value in (doc["series_memory"].get("introduced") or {}).items():
                    if isinstance(value, list) and eid in value:
                        doc["series_memory"]["introduced"][key] = [cid for cid in value if cid != eid]
                        introduced.append(key)
                doc["series_memory"], pairs = series_memory.drop_character(doc["series_memory"], eid)
                where = []
                if episodes:
                    where.append(f"episode(s) {', '.join(episodes)}")
                if introduced:
                    where.append(f"series_memory.introduced {', '.join(introduced)}")
                if pairs["relationship_state"]:
                    where.append(f"series_memory.relationship_state {', '.join(pairs['relationship_state'])}")
                for key, removed in pairs["entries"].items():
                    where.append(f"series_memory.entries.{key}.relationship_deltas {', '.join(removed)}")
                return f"{eid} removed from {' and '.join(where)}" if where else None

            def proposal(doc):
                props = [prop for prop in doc["props"] if prop.get("owner") == eid]
                for prop in props:
                    prop["owner"] = None
                if not props:
                    return None
                return f"owner {eid} cleared on {', '.join(repr(prop['name']) for prop in props)}"

            entities("characters", relationships)
            entities("props", owner)
            document(SEASON_DOC, season)
            document(PLACES_PROPOSAL_DOC, proposal)
        elif kind == "places":
            def location(doc):
                if doc["state"]["location"] == eid:
                    doc["state"]["location"] = None
                    return f"location {eid} cleared"
                return None

            entities("characters", location)
        document(KNOWLEDGE_DOC, lambda doc: _drop_from_knowledge(doc, kind, eid))
        return messages

    # -------------------------------------------------------------- media

    def write_media(self, story_id, kind, eid, name, src_path) -> str:
        """Copy *src_path* into the entity as *name*; returns the real path.

        *name* must be one the kind may hold (``MEDIA_NAME_PATTERNS``), which
        also says its folder (``refs/``, ``refs/uploads/`` or the entity's own
        for a voice sample); it is checked before any path is built. The
        entity folder must exist; the folders below it are made. The copy is
        atomic (``_atomic_copy``): a failure leaves the previous file
        byte-identical and no temp file. A symlink or a directory in the
        file's place is refused, never followed or replaced.
        """
        self._check_id(story_id)
        _entity_kind(kind, eid)
        location = _media_location(kind, name)
        with self._lock:
            folder = self._media_dir(story_id, kind, eid, location, create=True)
            dest = os.path.join(folder, name)
            if os.path.islink(dest) or (os.path.lexists(dest) and not os.path.isfile(dest)):
                label = self._entity_label(story_id, kind, eid) + "".join(
                    f"{part}/" for part in MEDIA_DIRS[location]) + name
                raise ValueError(f"{label} is not a regular file; it is never followed or replaced")
            _atomic_copy(str(src_path), dest)
        return dest

    def media_path(self, story_id, kind, eid, name) -> str:
        """The real path of one entity file, to serve it.

        KeyError for anything but an existing regular file named as the kind
        may hold (``MEDIA_NAME_PATTERNS``), directly inside its real folder, and
        not a symlink -- the ``preview_file`` rules. The name is checked
        before any path is built from it.
        """
        location = _media_location(kind, name)
        self._check_id(story_id)
        _entity_kind(kind, eid)
        with self._lock:
            real = _contained(self._media_dir(story_id, kind, eid, location, create=False),
                              name, want_dir=False)
        if real is None:
            raise KeyError(name)
        return real

    # ----------------------------------------------------------- episodes
    #
    # Nothing in this section reads or writes story.json or the index: an
    # episode document changes neither the story's approvals, its status nor
    # its index entry. Only the story's folder has to exist.

    def _episode_label(self, story_id, ep) -> str:
        return f"{self._label(story_id)}{EPISODES_DIRNAME}/{_episode_folder(ep)}/"

    def episode_dir(self, story_id, ep, *, create=False) -> str:
        """The real path of ``<story>/episodes/ep<NN>/``.

        The story id and the episode number (``check_episode``) are checked
        before any path is built; each level must be a real directory directly
        inside the one above it (``_descend``, the ``preview_dir`` rule).
        *create* makes the missing levels, one at a time. KeyError for an
        unknown story, a missing level (without *create*), or a level that is
        anything but a real directory.
        """
        self._check_id(story_id)
        ep = check_episode(ep)
        with self._lock:
            return _descend(self.story_dir(story_id), (EPISODES_DIRNAME, _episode_folder(ep)),
                            create=create, label=self._episode_label(story_id, ep))

    def _existing_episode_dir(self, story_id, ep):
        """``episode_dir`` without *create*, but None when a level does not
        exist yet (under the lock). A level that exists as anything but a real
        directory still raises KeyError: absent is not the same as refused."""
        parent = self.story_dir(story_id)
        for part in (EPISODES_DIRNAME, _episode_folder(ep)):
            if not os.path.lexists(os.path.join(parent, part)):
                return None
            parent = _descend(parent, (part,), create=False, label=self._episode_label(story_id, ep))
        return parent

    def read_episode_doc(self, story_id, ep, name):
        """One episode document (``EPISODE_DOC_NAMES``), or None if it does not
        exist yet.

        The name, the episode number and the story id are checked before any
        path is built. SchemaError for a document that is a symlink (never
        followed), unreadable, not valid JSON, fails its own checks
        (``EPISODE_DOC_VALIDATORS``) or names another episode -- never
        repaired. KeyError for an unknown story, or an ``episodes/`` or
        ``ep<NN>/`` that is there but is not a real directory.
        """
        self._check_episode_doc_name(name)
        ep = check_episode(ep)
        self._check_id(story_id)
        label = f"{self._episode_label(story_id, ep)}{name}"
        with self._lock:
            directory = self._existing_episode_dir(story_id, ep)
            if directory is None:
                return None
            path = os.path.join(directory, name)
            if os.path.islink(path):
                raise schemas.SchemaError(label, [f"{name} is a symlink; it is never followed"])
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except FileNotFoundError:
                return None
            except ValueError as exc:
                raise schemas.SchemaError(label, [f"not valid JSON: {exc}"]) from None
            except OSError as exc:
                raise schemas.SchemaError(label, [f"unreadable ({type(exc).__name__}: {exc})"]) from None
        errors = _episode_doc_errors(name, doc, ep)
        if errors:
            raise schemas.SchemaError(label, errors)
        return doc

    def write_episode_doc(self, story_id, ep, name, doc, *, now, validator=None) -> dict:
        """Write one episode document; returns what was written.

        The name (``EPISODE_DOC_NAMES``), the episode number and the story id
        are checked before any path is built. A document that carries
        timestamps (``EPISODE_DOCS_WITH_TIMESTAMPS``) has its ``updated_at``
        become *now* and keeps the ``created_at`` it is given, as
        ``write_doc`` does. Its own checks (``EPISODE_DOC_VALIDATORS``), its
        ``ep`` against *ep*, and *validator* when given (the checks against
        the story, which the caller runs) must all pass, or nothing is written
        and no folder is made. The folders are made one level at a time
        (``episode_dir``); the write is atomic; a symlink or anything but a
        regular file in the document's place is refused, never followed or
        replaced. story.json is neither read nor written.
        """
        self._check_episode_doc_name(name)
        ep = check_episode(ep)
        self._check_id(story_id)
        if not isinstance(doc, dict):
            raise ValueError(f"{name} must be an object, not {type(doc).__name__}")
        label = f"{self._episode_label(story_id, ep)}{name}"
        new = copy.deepcopy(doc)
        if name in EPISODE_DOCS_WITH_TIMESTAMPS:
            new["updated_at"] = now
        errors = _episode_doc_errors(name, new, ep)
        if not errors and validator is not None:
            errors = validator(new)
        if errors:
            raise schemas.SchemaError(label, errors)
        with self._lock:
            dest = os.path.join(self.episode_dir(story_id, ep, create=True), name)
            if os.path.islink(dest) or (os.path.lexists(dest) and not os.path.isfile(dest)):
                raise ValueError(f"{label} is not a regular file; it is never followed or replaced")
            _atomic_write_json(dest, new)
        return copy.deepcopy(new)

    def list_episodes(self, story_id) -> list:
        """The story's episode numbers, in order: every ``ep<NN>`` (01..99)
        that is a real directory directly inside a real ``episodes/``, whatever
        it holds. Any other name is ignored; a symlink or a file named like an
        episode (or in place of ``episodes/``) is skipped and printed
        (``on_log``) -- never followed, never deleted. KeyError for a malformed
        id or an unknown story.
        """
        self._check_id(story_id)
        numbers, messages = [], []
        with self._lock:
            parent = self.story_dir(story_id)
            if os.path.lexists(os.path.join(parent, EPISODES_DIRNAME)):
                folder = _contained(parent, EPISODES_DIRNAME, want_dir=True)
                if folder is None:
                    messages.append(f"Skipped {self._label(story_id)}{EPISODES_DIRNAME}/: "
                                    "not a real directory, never followed")
                else:
                    for name in sorted(os.listdir(folder)):
                        ep = _episode_number(name)
                        if ep is None:
                            continue
                        if _contained(folder, name, want_dir=True) is None:
                            messages.append(f"Skipped {self._episode_label(story_id, ep)}: "
                                            f"not a real directory inside {EPISODES_DIRNAME}/")
                            continue
                        numbers.append(ep)
        self._log(messages)
        return sorted(numbers)

    def discard_episode(self, story_id, ep, *, now) -> dict:
        """Archive episode *ep*: move its folder -- script, storyboard,
        images, clips, render, final video -- to
        ``episodes/_discarded/ep<NN>-<UTC stamp of now>/`` (``-2``, ``-3``...
        when that name is taken), and clear what speaks for it outside the
        folder, so the episode written in its place starts clean (the
        pipeline switch's "Regenerate on v2", ``workflow.switch_pipeline``).
        Nothing is deleted: the archive is recoverable by hand, and no
        episode scan reads it (:meth:`list_episodes` lists ``ep<NN>`` only).

        Outside the folder, under the store lock:

        - ``season.json`` loses the episode's memory entry and recap, its
          derived fields folded again (``series_memory.drop_episode``), and
          the episode's audience feedback items; the arc and its approval
          stay. Checked before anything moves: a later episode's entry that
          closes a hook this one opened refuses the discard whole
          (``series_memory.FoldError``, a ValueError -- discard the later
          episode first), and a season that cannot be read is never
          rewritten (``SchemaError``);
        - the proposals written from it, ``episodes/ep<N+1>/proposals.json``,
          join the archive (as ``proposals_for_ep<N+1>.json``) while episode
          N+1 has no script; a folder left empty by them goes. Its own
          proposals (written from episode N-1, not its work) wait in a fresh
          ``ep<NN>/`` for the episode written in its place;
        - its rows of the story's cost ledger are marked ``discarded``
          (``CostLedger.mark_discarded``): the story total keeps them, the
          per-episode cap of the new episode no longer counts them.

        Every write is atomic (``os.rename`` for the folders, the JSON
        writes' own rule). Returns ``{"ep", "archive", "moved_to",
        "proposals_archived": [eps], "ledger_rows": n, "cleared":
        [sentences]}``. KeyError for a malformed id, an unknown story, or an
        episode with no real folder (a symlink in its place is never
        followed).
        """
        self._check_id(story_id)
        ep = check_episode(ep)
        name = _episode_folder(ep)
        label = self._episode_label(story_id, ep)
        cleared, messages = [], []
        with self._lock:
            self._read_story(story_id)
            parent = self.story_dir(story_id)
            episodes = _contained(parent, EPISODES_DIRNAME, want_dir=True)
            source = _contained(episodes, name, want_dir=True) if episodes is not None else None
            if source is None:
                raise KeyError(label)

            # Everything that can refuse is checked before anything moves.
            season = self.read_doc(story_id, SEASON_DOC)
            new_season = None
            if season is not None:
                new_season = copy.deepcopy(season)
                new_season["series_memory"], removed = series_memory.drop_episode(season["series_memory"], ep)
                key = series_memory.memory_key(ep)
                if removed["entry"]:
                    cleared.append(f"series_memory.entries.{key} removed (the derived fields folded again)")
                elif removed["recap"]:
                    cleared.append(f"series_memory.recaps.{key} removed")
                kept = [item for item in season["audience_feedback"] if item.get("ep") != ep]
                gone = len(season["audience_feedback"]) - len(kept)
                new_season["audience_feedback"] = kept
                if gone:
                    cleared.append(f"{gone} audience feedback item{'' if gone == 1 else 's'} of episode {ep} removed")
                if new_season == season:
                    new_season = None

            archive_root = _descend(episodes, (DISCARDED_DIRNAME,), create=True,
                                    label=f"{self._label(story_id)}{EPISODES_DIRNAME}/{DISCARDED_DIRNAME}/")
            archive = f"{name}-{_archive_stamp(now)}"
            taken = 1
            while os.path.lexists(os.path.join(archive_root, archive)):
                taken += 1
                archive = f"{name}-{_archive_stamp(now)}-{taken}"
            target = os.path.join(archive_root, archive)
            os.rename(source, target)
            moved_to = f"{self._label(story_id)}{EPISODES_DIRNAME}/{DISCARDED_DIRNAME}/{archive}/"
            messages.append(f"Archived {label} to {moved_to}")

            own = os.path.join(target, EPISODE_PROPOSALS_DOC)
            if _is_regular(own):
                os.mkdir(source)
                os.rename(own, os.path.join(source, EPISODE_PROPOSALS_DOC))
                messages.append(f"Kept {label}{EPISODE_PROPOSALS_DOC}: written from episode {ep - 1}, which stays")

            proposals_archived = []
            if ep < EPISODE_MAX:
                following = _contained(episodes, _episode_folder(ep + 1), want_dir=True)
                if following is not None and not os.path.lexists(os.path.join(following, EPISODE_SCRIPT_DOC)):
                    written = os.path.join(following, EPISODE_PROPOSALS_DOC)
                    if _is_regular(written):
                        os.rename(written, os.path.join(target, _ARCHIVED_PROPOSALS.format(ep + 1)))
                        proposals_archived.append(ep + 1)
                        cleared.append(f"the proposals for episode {ep + 1} (written from episode {ep}) archived")
                        if not os.listdir(following):
                            os.rmdir(following)

            if new_season is not None:
                self.write_doc(story_id, SEASON_DOC, new_season, now=now)

            ledger_rows = 0
            ledger_path = os.path.join(parent, COST_LEDGER_FILENAME)
            if _is_regular(ledger_path):
                ledger_rows = CostLedger(ledger_path).mark_discarded(ep, archive)
                if ledger_rows:
                    cleared.append(f"{ledger_rows} cost ledger row{'' if ledger_rows == 1 else 's'} of episode "
                                   f"{ep} marked discarded (still in the story total, no longer in the "
                                   "episode's)")
        self._log(messages + [f"Cleared for {label}: {line}" for line in cleared])
        return {"ep": ep, "archive": archive, "moved_to": moved_to, "proposals_archived": proposals_archived,
                "ledger_rows": ledger_rows, "cleared": cleared}

    def episode_asset_path(self, story_id, ep, kind, filename, *, create=False) -> str:
        """The path of ``<story>/episodes/ep<NN>/assets/<kind>/<filename>``, to
        write the file or to serve it.

        *kind* (``EPISODE_ASSET_KINDS``), *filename* (its kind's
        ``EPISODE_ASSET_NAME_PATTERNS``), the episode number and the story id
        are checked before any path is built. Every folder must be a real
        directory directly inside the one above it (``_descend``); *create*
        makes the missing ones. The file need not exist, but whatever is in
        its place must be a regular file. KeyError for all of these -- a
        symlink at any level, the file's own included, is refused and never
        followed.
        """
        if not isinstance(kind, str) or kind not in EPISODE_ASSET_KINDS:
            raise KeyError(kind)
        if not isinstance(filename, str) or EPISODE_ASSET_NAME_PATTERNS[kind].fullmatch(filename) is None:
            raise KeyError(filename)
        ep = check_episode(ep)
        self._check_id(story_id)
        label = f"{self._episode_label(story_id, ep)}{EPISODE_ASSETS_DIRNAME}/{kind}/"
        with self._lock:
            folder = _descend(self.episode_dir(story_id, ep, create=create),
                              (EPISODE_ASSETS_DIRNAME, kind), create=create, label=label)
            path = os.path.join(folder, filename)
            if os.path.islink(path) or (os.path.lexists(path) and not os.path.isfile(path)):
                raise KeyError(f"{label}{filename}")
        return path

    def episode_stock_credits_path(self, story_id, ep, name, *, create=False) -> str:
        """The path of ``<story>/episodes/ep<NN>/assets/<name>``, one of
        :data:`EPISODE_STOCK_CREDITS_NAMES` (plan 23 stage B8: the stock
        cutaways' credits), with :meth:`episode_asset_path`'s rules: the name,
        episode and story id checked first, every folder a real one, nothing
        a symlink."""
        if not isinstance(name, str) or name not in EPISODE_STOCK_CREDITS_NAMES:
            raise KeyError(name)
        return self._episode_sub_path(story_id, ep, EPISODE_STOCK_CREDITS_DIR, name, create=create)

    def episode_brief_path(self, story_id, ep, name, *, create=False) -> str:
        """The path of ``<story>/episodes/ep<NN>/assets/brief/<name>``, one of
        :data:`EPISODE_BRIEF_NAMES` (plan 22 stage 5: the shot brief), with
        :meth:`episode_asset_path`'s rules: the name, episode and story id
        checked first, every folder a real one, nothing a symlink."""
        if not isinstance(name, str) or name not in EPISODE_BRIEF_NAMES:
            raise KeyError(name)
        return self._episode_sub_path(story_id, ep, EPISODE_BRIEF_DIR, name, create=create)

    def episode_take_path(self, story_id, ep, name, *, create=False) -> str:
        """The path of ``<story>/episodes/ep<NN>/assets/clips/takes/<name>``
        (:data:`EPISODE_TAKE_NAME_PATTERN`: an uploaded clip a later upload
        replaced, kept), with :meth:`episode_asset_path`'s rules."""
        if not isinstance(name, str) or EPISODE_TAKE_NAME_PATTERN.fullmatch(name) is None:
            raise KeyError(name)
        return self._episode_sub_path(story_id, ep, EPISODE_TAKES_DIR, name, create=create)

    def _episode_sub_path(self, story_id, ep, folders, name, *, create) -> str:
        ep = check_episode(ep)
        self._check_id(story_id)
        label = f"{self._episode_label(story_id, ep)}{'/'.join(folders)}/"
        with self._lock:
            folder = _descend(self.episode_dir(story_id, ep, create=create), folders, create=create, label=label)
            path = os.path.join(folder, name)
            if os.path.islink(path) or (os.path.lexists(path) and not os.path.isfile(path)):
                raise KeyError(f"{label}{name}")
        return path

    def episode_file_path(self, story_id, ep, name, *, create=False) -> str:
        """The path of ``<story>/episodes/ep<NN>/<name>``, one of the files at
        the top of the episode's folder (``EPISODE_FILE_NAMES``: the final
        video, its subtitles, the cover, the episode's cost-ledger view), to
        write the file or to serve it.

        *name* is checked against that list before any path is built (an
        episode document goes through ``read_episode_doc``/
        ``write_episode_doc``, and any other name is refused), then the
        episode number and the story id. The ``episode_asset_path`` rules
        follow: every folder a real directory (``_descend``), *create* makes
        the missing ones, the file need not exist but whatever is in its place
        must be a regular file. KeyError for all of these -- a symlink at any
        level, the file's own included, is refused and never followed.
        """
        if not isinstance(name, str) or name not in EPISODE_FILE_NAMES:
            raise KeyError(name)
        ep = check_episode(ep)
        self._check_id(story_id)
        with self._lock:
            path = os.path.join(self.episode_dir(story_id, ep, create=create), name)
            if os.path.islink(path) or (os.path.lexists(path) and not os.path.isfile(path)):
                raise KeyError(f"{self._episode_label(story_id, ep)}{name}")
        return path

    def episode_doc_path(self, story_id, ep, name, *, create=False) -> str:
        """The path of the episode document *name* (``EPISODE_DOC_NAMES``),
        for the one writer that is not this store: the render runner keeps
        ``render_manifest.json`` itself, rewriting it at every change of a
        stage's state, and ``render_manifest.last_good.json`` once a render
        completes (``render/manifest.py``, validated and atomic on every
        write). Everything else reads and writes documents through
        ``read_episode_doc``/``write_episode_doc``.

        The ``episode_file_path`` rules: the name, the episode number and the
        story id are checked before any path is built; every folder a real
        directory (``_descend``), *create* makes the missing ones; the file
        need not exist, but whatever is in its place must be a regular file.
        ValueError for a name that is not an episode document; KeyError for
        the rest -- a symlink at any level, the document's own included, is
        refused and never followed.
        """
        self._check_episode_doc_name(name)
        ep = check_episode(ep)
        self._check_id(story_id)
        with self._lock:
            path = os.path.join(self.episode_dir(story_id, ep, create=create), name)
            if os.path.islink(path) or (os.path.lexists(path) and not os.path.isfile(path)):
                raise KeyError(f"{self._episode_label(story_id, ep)}{name}")
        return path

    def episode_render_dir(self, story_id, ep, sub=None, *, create=False) -> str:
        """The real path of ``<story>/episodes/ep<NN>/render/``, or of the
        folder *sub* below it (``EPISODE_RENDER_SUBDIRS``).

        *sub*, the episode number and the story id are checked before any
        path is built. Every level must be a real directory directly inside
        the one above it (``_descend``): a symlink at any level -- ``render/``
        and *sub* included -- is refused and never followed. *create* makes
        the missing levels, one at a time. KeyError for an unknown story, a
        missing level (without *create*), or a level that is anything but a
        real directory. Story delete removes it with the story's folder.
        """
        if sub is not None and (not isinstance(sub, str) or sub not in EPISODE_RENDER_SUBDIRS):
            raise KeyError(sub)
        ep = check_episode(ep)
        self._check_id(story_id)
        parts = (EPISODE_RENDER_DIRNAME,) if sub is None else (EPISODE_RENDER_DIRNAME, sub)
        label = self._episode_label(story_id, ep) + "".join(f"{part}/" for part in parts)
        with self._lock:
            return _descend(self.episode_dir(story_id, ep, create=create), parts, create=create, label=label)

    # -------------------------------------------------------- generation cache

    def gen_cache_dir(self, story_id, *, create=False) -> str:
        """The real path of the story's generation cache, ``<story>/cache/gen/``
        -- the root ``clipping/providers/gencache.py`` is given, which checks
        nothing about it itself.

        The story id is checked before any path is built; ``cache/`` and
        ``gen/`` must each be a real directory directly inside the one above
        it (``_descend``): a symlink at either level is refused and never
        followed. *create* makes the missing levels, one at a time. KeyError
        for an unknown story, a missing level (without *create*), or a level
        that is anything but a real directory. Like the episodes, the cache
        never touches story.json, and story delete removes it with the story's
        folder.
        """
        self._check_id(story_id)
        label = self._label(story_id) + "".join(f"{part}/" for part in GEN_CACHE_DIRS)
        with self._lock:
            return _descend(self.story_dir(story_id), GEN_CACHE_DIRS, create=create, label=label)

    # -------------------------------------------------------------- index

    def _read_index(self):
        """``(stories, None)``, or ``(None, reason)`` when the index is unusable."""
        try:
            with open(self.index_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return None, _INDEX_MISSING
        except (OSError, ValueError) as exc:
            return None, f"the index is unreadable ({type(exc).__name__}: {exc})"
        if not isinstance(data, dict):
            return None, "the index is not a JSON object"
        if data.get("$schema") != INDEX_SCHEMA:
            return None, f"the index $schema is {data.get('$schema')!r}, not {INDEX_SCHEMA!r}"
        stories = data.get("stories")
        if not isinstance(stories, dict):
            return None, "the index has no 'stories' object"
        for key, entry in stories.items():
            if not is_story_id(key) or schemas.validate(entry, _INDEX_ENTRY_SCHEMA) \
                    or entry["story_id"] != key:
                return None, f"the index entry {key!r} is malformed"
        return stories, None

    def _write_index(self, stories: dict, *, now=None) -> None:
        _atomic_write_json(self.index_path, {
            "$schema": INDEX_SCHEMA,
            "stories": stories,
            "updated_at": now or _utc_now(),
        })

    def _other_story_folders(self, story_id) -> bool:
        try:
            return any(is_story_id(n) and n != story_id for n in os.listdir(self.root))
        except OSError:
            return False

    def _upsert_index(self, doc: dict, *, now) -> list:
        stories, reason = self._read_index()
        messages = []
        if stories is None:
            if reason == _INDEX_MISSING and not self._other_story_folders(doc["story_id"]):
                stories = {}  # the first story: there is nothing to rebuild from
            else:
                # story.json is already written, so the rebuild includes this story.
                _, messages = self._rebuild_locked(reason, now=now)
                stories = self._read_index()[0] or {}
        stories[doc["story_id"]] = _index_entry(doc)
        self._write_index(stories, now=now)
        return messages

    def _rebuild_locked(self, reason, *, now=None):
        entries = {}
        messages = []
        if os.path.isdir(self.root):
            for name in sorted(os.listdir(self.root)):
                if not is_story_id(name):
                    continue
                label = self._label(name)
                if _contained(self.root, name, want_dir=True) is None:
                    messages.append(f"Skipped {label}: not a real directory inside outputs/{STORIES_DIRNAME}/")
                    continue
                try:
                    doc = self._read_story(name)
                except KeyError:
                    messages.append(f"Skipped {label}: no {STORY_FILENAME}")
                    continue
                except schemas.SchemaError as exc:
                    detail = "; ".join(exc.errors)
                    if len(detail) > 300:
                        detail = detail[:297] + "..."
                    messages.append(f"Skipped {label}: {STORY_FILENAME} is invalid ({detail})")
                    continue
                entries[name] = _index_entry(doc)
        self._write_index(entries, now=now)
        messages.append(
            f"Rebuilt outputs/{INDEX_FILENAME} from {len(entries)} story folder(s): {reason}")
        return len(entries), messages

    def rebuild_index(self, reason) -> int:
        """Rewrite the index from the story folders; returns how many it holds."""
        with self._lock:
            count, messages = self._rebuild_locked(reason)
        self._log(messages)
        return count

    def list(self) -> list:
        """Index entries, most recently updated first."""
        messages = []
        with self._lock:
            stories, reason = self._read_index()
            if stories is None:
                if reason == _INDEX_MISSING and not os.path.lexists(self.root):
                    # A fresh install: nothing to rebuild, nothing was lost.
                    return []
                _, messages = self._rebuild_locked(reason)
                stories = self._read_index()[0] or {}
            entries = [dict(entry) for entry in stories.values()]
        self._log(messages)
        entries.sort(key=lambda e: (e["updated_at"], e["created_at"], e["story_id"]), reverse=True)
        return entries

    # ------------------------------------------------------------- delete

    def delete(self, story_id) -> dict:
        """Remove the story's folder and its index entry.

        Returns ``{"removed": [...], "kept": [...]}`` like
        ``cleanup.remove_job_files``. The folder is removed only as a real
        directory directly inside ``stories/``; a symlink in its place is kept
        and reported, never followed. The index entry goes whenever the id is
        well-formed. KeyError for a malformed id, or for an id with neither a
        folder nor an index entry. Knows nothing about jobs: the caller checks
        for a running step first.
        """
        self._check_id(story_id)
        report = {"removed": [], "kept": []}
        messages = []
        label = self._label(story_id)
        try:
            with self._lock:
                stories, reason = self._read_index()
                if stories is None:
                    _, rebuilt = self._rebuild_locked(reason)
                    messages.extend(rebuilt)
                    stories = self._read_index()[0] or {}
                path = os.path.join(self.root, story_id)
                has_folder = os.path.lexists(path)
                if not has_folder and story_id not in stories:
                    raise KeyError(story_id)

                real = _contained(self.root, story_id, want_dir=True)
                if real is not None:
                    try:
                        shutil.rmtree(real)
                        report["removed"].append(label)
                    except OSError as exc:
                        report["kept"].append(f"{label} ({exc})")
                elif has_folder:
                    why = "a symlink, never followed" if os.path.islink(path) else "not a directory"
                    report["kept"].append(f"{label} ({why})")

                if story_id in stories:
                    del stories[story_id]
                    self._write_index(stories)
        finally:
            self._log(messages)
        return report
