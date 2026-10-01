"""Partial re-render: which shot clips a render takes from the cache, which it
makes again, and why -- measured against the last good render (AI Story
phase 5, stage 8; plan 11 "Stage 8 -- partial re-render"; DEC-156).

A stale reuse would show an old frame silently, so a clip is reused on proof
only.

**The hit** (:func:`cache_state`, the one predicate: the runner decides every
cached stage with it, :func:`select` predicts with it, so the dry run says
what the runner then does). ``cache/<key>.mp4`` is reused when it is a
regular file -- never a symlink -- that is not empty and whose sha256 is one a
manifest **recorded** for that key (:func:`recorded_shas`): the last good
render's, the last render's (a failed one's clips count: each was hashed when
its process succeeded), the clips a completed render kept in the cache (its
``cache`` map: the last two renders' clips, carried forward so an edit undone
finds its clip again), and, during a render, its own (two shots asking for
the same clip). The key already names the command, every file it reads, the
profile and the ffmpeg (``plan.cache_key``); the recorded sha256 proves the
bytes are the ones that command made. Otherwise the clip is made again:
``missing`` (absent, empty, a symlink, or no recorded sha256 to check it
against) or ``corrupt`` (there, but not the bytes recorded).

**The baseline** (:func:`baseline_of`): the last render that completed --
``render_manifest.last_good.json``, which the runner writes only then -- or,
for an episode rendered before this stage, ``render_manifest.json`` when it
records an output. None when the episode has no finished render: nothing to
compare with, so no ``reuse`` record.

**Why** (:func:`select`'s reasons, ``schemas.RENDER_REUSE_REASONS``): a shot
whose key moved since the baseline is compared with the baseline's command
for the same shot (:func:`shot_change`), in this order -- ``image`` (its
image or video), ``overlay`` (the style's overlays, or the paper texture's
bytes), ``modifiers`` (``handheld``, ``jitter_stopmotion``), ``frames`` (its
frame count: a re-time, a transition, a conversion to whole frames),
``motion`` (its camera motion), else ``settings`` (the ffmpeg, the profile,
the encoder settings, the framing: a still's 9:16 crop, a clip's cover). A
shot the baseline did not have is ``new``; one whose key did not move is
``missing`` or ``corrupt``. One reason a shot, the first that applies: an
image and its frames changed together is an ``image``.

**Conversion.** A storyboard timed before whole frames converts on its first
full re-time (stage 6), and most of its shots' frames move once: the
selection says so (``timing_converted``) when the plan is timed in whole
frames, the baseline was not (a manifest written before this stage records
no flag: every such render was cut from a board timed before whole frames)
and a shot is rebuilt for its frames.

Stdlib + this package (DEC-012). Reads files (the cache, the manifests),
runs no process.
"""

from __future__ import annotations

import hashlib
import os
import re

from .. import schemas
from . import manifest as manifest_mod
from . import plan as plan_mod

REASONS = schemas.RENDER_REUSE_REASONS
HIT, MISSING, CORRUPT = "hit", "missing", "corrupt"
NEW, SETTINGS = "new", "settings"
REUSE, REBUILD = "reuse", "rebuild"

_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_SETTLED = ("done", "cached")
# Filters an overlay adds to a shot's graph (``filtergraph._overlay_fragments``
# and the paper texture's ``movie`` + ``blend``).
_OVERLAY_FILTERS = ("noise", "vignette", "movie", "blend")
_LABELS_RE = re.compile(r"^(?:\[[^\]]*\])*(.*?)(?:\[[^\]]*\])*$", re.S)


def _sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clip_rel(key: str) -> str:
    """A clip's path in ``render/``: ``cache/<key>.mp4`` (the plan's stage
    ``output``)."""
    return f"{plan_mod.CACHE_DIR}/{key}.mp4"


# ------------------------------------------------------------ the evidence

def recorded_shas(*manifests) -> dict:
    """``{cache key: {sha256, ...}}``: every clip the given manifests
    recorded -- a stage ``done`` or ``cached`` whose output is
    ``cache/<key>.mp4`` and whose ``output_sha256`` is a sha256, and the
    clips a completed render kept (its ``cache`` map, carried forward from
    the manifest before it). None, a damaged document or entry adds nothing
    (it can only make more clips again, never reuse one)."""
    found: dict = {}
    for doc in manifests:
        if not isinstance(doc, dict):
            continue
        kept = doc.get("cache")
        for key, shas in (kept.items() if isinstance(kept, dict) else ()):
            if isinstance(key, str) and _KEY_RE.match(key) and isinstance(shas, list):
                for sha in shas:
                    if isinstance(sha, str) and _KEY_RE.match(sha):
                        found.setdefault(key, set()).add(sha)
        if not isinstance(doc.get("stages"), list):
            continue
        for stage in doc["stages"]:
            if not isinstance(stage, dict) or stage.get("state") not in _SETTLED:
                continue
            key, sha = stage.get("cache_key"), stage.get("output_sha256")
            if not (isinstance(key, str) and _KEY_RE.match(key) and isinstance(sha, str) and _KEY_RE.match(sha)):
                continue
            if stage.get("output") != clip_rel(key):
                continue
            found.setdefault(key, set()).add(sha)
    return found


def cache_state(cache_dir, key: str, recorded: dict) -> tuple:
    """``(HIT | MISSING | CORRUPT, sha256 or None)`` of the clip under *key*
    in *cache_dir* (module docstring, "The hit"; None: no cache folder). The
    sha256 is the file's when it was hashed (a hit's, a corrupt one's)."""
    if cache_dir is None:
        return MISSING, None
    path = os.path.join(os.fspath(cache_dir), f"{key}.mp4")
    try:
        if os.path.islink(path) or not os.path.isfile(path) or os.path.getsize(path) == 0:
            return MISSING, None
        known = recorded.get(key)
        if not known:
            return MISSING, None
        sha = _sha256_file(path)
    except OSError:
        return MISSING, None
    return (HIT, sha) if sha in known else (CORRUPT, sha)


def baseline_of(last_good, current):
    """The render a re-render is measured against (module docstring): the
    last good manifest, else the last manifest when it records an output,
    else None."""
    for doc in (last_good, current):
        if isinstance(doc, dict) and doc.get("output"):
            return doc
    return None


def load_state(manifest_path, last_good_path=None) -> dict:
    """What the runner and a dry run know before a render:
    ``{"last_good", "current", "baseline", "recorded"}`` -- the two
    manifests as read (``manifest.read_checked``: a symlink, an unreadable
    or an invalid one is None), the baseline (:func:`baseline_of`) and every
    clip they recorded (:func:`recorded_shas`). *last_good_path* defaults
    to the one beside *manifest_path* (``manifest.last_good_path``)."""
    last_good_path = manifest_mod.last_good_path(manifest_path) if last_good_path is None else last_good_path
    last_good = manifest_mod.read_checked(os.fspath(last_good_path))
    current = manifest_mod.read_checked(os.fspath(manifest_path))
    return {"last_good": last_good, "current": current, "baseline": baseline_of(last_good, current),
            "recorded": recorded_shas(last_good, current)}


# ----------------------------------------------------------------- the why

def _split(text: str, sep: str) -> list:
    """*text* split on *sep* outside single quotes (a filter option's value
    is quoted, ``motion._quoted``) and not escaped by a backslash."""
    parts, buf, quoted, escaped = [], [], False, False
    for ch in text:
        if escaped:
            buf.append(ch)
            escaped = False
        elif ch == "\\":
            buf.append(ch)
            escaped = True
        elif ch == "'":
            buf.append(ch)
            quoted = not quoted
        elif ch == sep and not quoted:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _filters(graph: str) -> list:
    """``[(name, options text)]`` of a filter graph, in order, pad labels
    dropped."""
    found = []
    for chain in _split(graph, ";"):
        for item in _split(chain, ","):
            body = _LABELS_RE.match(item.strip()).group(1)
            if body:
                name, _sep, options = body.partition("=")
                found.append((name, options))
    return found


def _options(text: str) -> dict:
    """A filter's ``key=value`` options (a bare positional one keyed by its
    index)."""
    options = {}
    for index, part in enumerate(_split(text, ":")):
        key, sep, value = part.partition("=")
        options[key if sep else str(index)] = value if sep else key
    return options


def _after(argv: list, flag: str):
    return argv[argv.index(flag) + 1] if flag in argv else None


def shot_facts(argv) -> dict:
    """What a shot's command (``filtergraph.shot_argv`` or
    ``tier2_clip_argv``) says about the shot: ``source`` (its input and
    whether it is a looped still), ``frames`` (``-frames:v`` and zoompan's
    ``d``), ``overlays`` (the overlay filters of its graph), ``modifiers``
    (zoompan's canvas and rate, a ``crop`` or an ``fps`` after zoompan --
    ``handheld`` and ``jitter_stopmotion``; a crop before zoompan or without
    one frames the picture, the still's 9:16 crop and a clip's cover, phase 6
    stage 13b) and ``motion`` (zoompan's ``z``/``x``/``y``). ValueError when
    *argv* is not a shot's command."""
    argv = list(argv)
    if "-i" not in argv or "-frames:v" not in argv:
        raise ValueError("not a shot's command")
    graph = _after(argv, "-filter_complex") or _after(argv, "-vf") or ""
    filters = _filters(graph)
    names = [name for name, _options_text in filters]
    zoompan = next((_options(text) for name, text in filters if name == "zoompan"), {})
    after_zoompan = names[names.index("zoompan") + 1:] if "zoompan" in names else []
    return {
        "source": (_after(argv, "-i"), "-loop" in argv),
        "frames": (_after(argv, "-frames:v"), zoompan.get("d")),
        "overlays": tuple(sorted(name for name in names if name in _OVERLAY_FILTERS)),
        "modifiers": (zoompan.get("s"), zoompan.get("fps"), "crop" in after_zoompan, "fps" in after_zoompan),
        "motion": (zoompan.get("z"), zoompan.get("x"), zoompan.get("y")),
    }


def shot_change(old_argv, new_argv, *, old_overlay=None, new_overlay=None) -> str:
    """Why a shot's command moved from *old_argv* to *new_argv* (module
    docstring, "Why"); *old_overlay*/*new_overlay* are the paper texture's
    sha256 in each render (None without one). Never raises: a command it
    cannot read is ``settings``."""
    try:
        old, new = shot_facts(old_argv), shot_facts(new_argv)
    except (ValueError, IndexError, TypeError, AttributeError):
        return SETTINGS
    if old["source"] != new["source"]:
        return "image"
    if old["overlays"] != new["overlays"] or ("movie" in new["overlays"] and old_overlay != new_overlay):
        return "overlay"
    if old["modifiers"] != new["modifiers"]:
        return "modifiers"
    if old["frames"] != new["frames"]:
        return "frames"
    if old["motion"] != new["motion"]:
        return "motion"
    return SETTINGS


def _overlay_sha(inputs):
    for item in inputs or []:
        if isinstance(item, dict) and item.get("role") == "overlay":
            return item.get("sha256")
    return None


def shot_id_of(stage_id: str) -> str:
    """``S:sh04`` -> ``sh04``."""
    return stage_id[2:] if stage_id.startswith("S:") else stage_id


# ---------------------------------------------------------------- selection

def select(baseline_manifest, new_plan, cache_dir, *, recorded=None) -> dict:
    """What a render of *new_plan* (``plan.build_render_plan``) would make
    again and take from *cache_dir* (``render/cache/``), measured against
    *baseline_manifest* (:func:`baseline_of`; None: no baseline; a
    *cache_dir* of None: no cache folder yet). *recorded*
    is the clips the manifests recorded (:func:`recorded_shas`; default:
    the baseline's alone -- the runner and a dry run pass
    :func:`load_state`'s, which the last render's clips join).

    Returns ``{"rebuild": [shot ids], "reuse": [shot ids], "reasons":
    {rebuilt shot id: reason}, "changed": {shot id: reason}, "shots_total",
    "end_card": "rebuild" | "reuse" | None, "timing_converted": bool}``, the
    shots in the plan's order. ``changed`` names every shot whose clip is
    not the baseline's -- a rebuilt one, or one the cache still held from
    another render (an edit undone). A clip two shots share is made once:
    the second takes it from the cache. Calls nothing; hashes the cached
    clips it would reuse."""
    base = baseline_manifest if isinstance(baseline_manifest, dict) else None
    recorded = recorded_shas(base) if recorded is None else recorded
    base_stages = {}
    for stage in (base or {}).get("stages") or []:
        if isinstance(stage, dict) and isinstance(stage.get("id"), str):
            base_stages[stage["id"]] = stage
    old_overlay = _overlay_sha((base or {}).get("inputs"))
    new_overlay = _overlay_sha(new_plan.get("inputs"))

    rebuild, reuse, reasons, changed = [], [], {}, {}
    end_card = None
    made = set()
    for stage in new_plan["stages"]:
        key = stage.get("cache_key")
        if stage["kind"] not in schemas.RENDER_CACHED_KINDS or key is None:
            continue
        state = HIT if key in made else cache_state(cache_dir, key, recorded)[0]
        if state != HIT:
            made.add(key)
        if stage["kind"] != "shot":
            end_card = REUSE if state == HIT else REBUILD
            continue
        before = base_stages.get(stage["id"])
        if base is None or before is None:
            change = NEW
        elif before.get("cache_key") == key:
            change = None
        else:
            change = shot_change(before.get("argv") or [], stage["argv"], old_overlay=old_overlay,
                                 new_overlay=new_overlay)
        shot_id = shot_id_of(stage["id"])
        if change is not None:
            changed[shot_id] = change
        if state == HIT:
            reuse.append(shot_id)
        else:
            rebuild.append(shot_id)
            reasons[shot_id] = change if change is not None else state

    converted = (bool(new_plan.get("whole_frames")) and base is not None and base.get("whole_frames") is not True
                 and "frames" in reasons.values())
    return {"rebuild": rebuild, "reuse": reuse, "reasons": reasons, "changed": changed,
            "shots_total": len(rebuild) + len(reuse), "end_card": end_card, "timing_converted": converted}


# ------------------------------------------------------------------ record

def reuse_record(baseline, selection) -> dict:
    """The manifest's ``reuse`` record (``schemas._RENDER_REUSE_SCHEMA``) of
    a render measured against *baseline*, as *selection* predicts it."""
    return {
        "baseline_output_sha256": baseline["output"]["sha256"],
        "shots_total": selection["shots_total"],
        "shots_rebuilt": list(selection["rebuild"]),
        "shots_reused": list(selection["reuse"]),
        "reasons": dict(selection["reasons"]),
        "timing_converted": bool(selection["timing_converted"]),
    }


def settle(record, order, shot_id, *, rebuilt: bool, reason=None) -> None:
    """Make *record* say what happened to *shot_id* when it is not what the
    selection predicted (its clip changed on disk in between): moved to
    ``shots_rebuilt`` with *reason*, or to ``shots_reused``. *order* is the
    plan's shot ids, which both lists keep. None: no record."""
    if record is None:
        return
    rebuilt_now = set(record["shots_rebuilt"])
    if rebuilt and shot_id not in rebuilt_now:
        rebuilt_now.add(shot_id)
        record["reasons"][shot_id] = reason
    elif not rebuilt and shot_id in rebuilt_now:
        rebuilt_now.discard(shot_id)
        record["reasons"].pop(shot_id, None)
    else:
        return
    record["shots_rebuilt"] = [sid for sid in order if sid in rebuilt_now]
    record["shots_reused"] = [sid for sid in order if sid not in rebuilt_now]


def summary(record) -> str:
    """``"3 of 11 shots re-rendered"`` (the feed, the episode page), and why
    it was not partial when the timing converted."""
    total = record["shots_total"]
    text = f"{len(record['shots_rebuilt'])} of {total} shot{'' if total == 1 else 's'} re-rendered"
    if record.get("timing_converted"):
        text += " (the shot timing moved to whole frames)"
    return text
