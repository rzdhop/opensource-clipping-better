"""Stock cutaways: an opt-in per story that FILLS establishing shots with a stock clip,
never replacing one already there (AI Story plan 23, stage B8).

``generation_profile.stock_cutaways: "on"`` (``media_policy.stock_cutaways``; absent
is off, every story as before) lets the assets step, at its start and before any
keyframe is made, fill each **eligible** shot with a clip of a stock source
(``clipping.stock``: the local folder, Pexels, Pixabay, in the order the settings
name) instead of a generated image and a bought clip:

* :func:`eligible` -- a ``wide_establishing`` shot with no character (``@char_``) or
  prop (``%prop_``) in its subject tags that is not a speaking shot (``speaks``);
  a narrator's or a voice-over line over it is fine. (A storyboard shot has no
  ``type`` field: the framing, the tags and ``speaks`` are what say it.)
* :func:`query_for` -- the deterministic query: the place's name, a few keywords of
  its descriptor and the time of day of the scene's variant (day or night).
* :func:`fill` -- per eligible shot with no current keyframe and no clip: search
  (the story's frame, a length of the shot's ``duration_s`` + 0.3 s), download to
  ``assets/clips/shot_NN.stock.mp4``, cut a frame at 0.5 s as the shot's keyframe
  (route ``stock``, ``source: stock/<provider>``, so the cover, the brief and the
  thumbnails keep working) and record the clip ``{link: "stock/<provider>", route:
  "stock", est_usd: 0, prompt_hash: sha256(query + aspect), source: <credit>}``.
  A shot with nothing found is generated as usual, with a log line. **A clip that
  is there is never replaced**: a generated, bought or uploaded clip, and a shot
  whose keyframe is locked or was replaced by hand, are left alone.
* :func:`clip_is_current` -- what ``clips.clip_state`` asks of a stock clip: current
  only while the switch is on, the shot is still eligible and the query hash
  still matches (a place renamed, a variant changed, another frame).
* :func:`write_credits` / :func:`credits_of` -- ``assets/stock_credits.{json,txt}``,
  what the metadata pack and the dashboard credit (Pexels asks for its link).

Nothing here bills anything (a stock clip is free: ``est_usd: 0``) and nothing
calls a model. Stdlib only (DEC-012); ``clipping.stock`` and ``steps`` are imported
where they are used so that ``steps.clips`` can import this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import subprocess
from datetime import datetime, timezone

from . import media_policy, schemas

logger = logging.getLogger("clipping.aistory.stock_cutaways")

ROUTE = schemas.STOCK_ROUTE
LINK_PREFIX = "stock/"
FRAMING = "wide_establishing"
# The shot's tags that make a cutaway no cutaway: a character, a prop.
BLOCKING_TAG_PREFIXES = ("@char_", "%prop_")
# A clip must run this long past the shot (a hold covers a shorter one; this keeps it rare).
MARGIN_S = 0.3
# The frame cut from the clip as the shot's keyframe.
FRAME_AT_S = 0.5
KEYWORDS = 4
CREDITS_JSON = "stock_credits.json"
CREDITS_TXT = "stock_credits.txt"
CREDITS_SCHEMA = "stock_credits_v1"
FRAME_TIMEOUT_S = 120

_STOP = frozenset(
    "a an the of and or in on at to with by for from is are was were its it this that into over under near beside "
    "behind between his her their our your very".split())
_WORD = re.compile(r"[^\W_][\w'-]*", re.UNICODE)
_TIMES = {"day": "day", "night": "night"}


def _words(text) -> list:
    return _WORD.findall(str(text or ""))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------- the eligibility

def eligible(shot, script=None) -> bool:
    """Whether *shot* may be a stock cutaway: framing ``wide_establishing``, no
    ``@char_`` or ``%prop_`` among its subject tags, and not a speaking shot
    (``speaks``). The shot's own fields say all that: *script* is accepted so a
    caller holds one signature for the checks of a shot, and is not read (the
    lines over a cutaway are a narrator's or a voice-over)."""
    if (shot or {}).get("framing") != FRAMING or shot.get("speaks"):
        return False
    return not any(str(tag).startswith(BLOCKING_TAG_PREFIXES) for tag in shot.get("subject_tags") or ())


def is_stock_clip(shot) -> bool:
    """Whether *shot*'s clip record is a stock one."""
    return ((shot.get("assets") or {}).get("clip") or {}).get("route") == ROUTE


def keyframe_is_stock(shot) -> bool:
    """Whether *shot*'s image is a frame of a stock clip (route ``stock``)."""
    return (shot.get("assets") or {}).get("route") == ROUTE


# ------------------------------------------------------------------ the query

def time_of_day(variant) -> str:
    """"day", "night", or the variant's own words ("golden hour"); "" for none."""
    variant = str(variant or "").strip().lower()
    return _TIMES.get(variant, variant.replace("_", " "))


def query_for(place, variant="") -> str:
    """The stock query of an establishing shot of *place* (its ``place_v1`` document, or
    a dict with a ``name`` and a ``descriptor``) at its scene's time-of-day *variant*:
    the name, up to :data:`KEYWORDS` keywords of the descriptor (stop words and the
    words of the name left out, in the order written, each once), then the time of
    day. The same inputs always give the same words."""
    place = place or {}
    name = " ".join(str(place.get("name") or "").split())
    seen = {word.lower() for word in _words(name)}
    keywords = []
    for word in _words(place.get("descriptor")):
        key = word.lower()
        if len(key) < 3 or key in _STOP or key in seen:
            continue
        seen.add(key)
        keywords.append(key)
        if len(keywords) == KEYWORDS:
            break
    parts = [name, *keywords, time_of_day(variant)]
    return " ".join(part for part in parts if part)


def query_hash(query, aspect) -> str:
    """sha256 of the query and the frame it was searched for: a clip's (and its
    keyframe's) ``prompt_hash``, so a place renamed or another frame moves it."""
    return hashlib.sha256(f"{query}\n{aspect}".encode("utf-8")).hexdigest()


def _scene_of(script, shot):
    return next((item for item in (script or {}).get("scenes") or () if item["scene_id"] == shot["scene_id"]), None)


def query_of(ec, shot, script):
    """The query *shot*'s scene asks (:func:`query_for` of its place and variant), or None
    when the scene or its place is not there."""
    scene = _scene_of(script, shot)
    if scene is None:
        return None
    place = ((getattr(ec, "entities", None) or {}).get("places") or {}).get(scene.get("place_id"))
    if not place:
        return None
    return query_for(place, scene.get("time_variant") or "")


def expected_hash(ec, shot, script):
    """The ``prompt_hash`` a stock clip of *shot* has now, or None."""
    query = query_of(ec, shot, script)
    if query is None:
        return None
    return query_hash(query, media_policy.aspect(getattr(ec, "story", None)))


def clip_is_current(ec, shot, script, clip, image_sha) -> bool:
    """What ``clips.clip_state`` answers for a stock *clip* record (its file is there):
    current only while the story's switch is on, *shot* is still eligible, the
    query hash still matches and the keyframe on disk (*image_sha*) is the frame
    it was cut with."""
    if not media_policy.stock_cutaways(getattr(ec, "story", None)) or not eligible(shot, script):
        return False
    expected = expected_hash(ec, shot, script)
    if expected is None or clip.get("prompt_hash") != expected:
        return False
    return image_sha is not None and clip.get("image_sha256") == image_sha


def keyframe_state(ec, shot, *, file_ok) -> str:
    """``assets.shot_state`` of a stock keyframe: ``none`` without its file, ``current``
    while the switch is on and the shot is still eligible (a keyframe cut from a
    clip is never made again by a call), else ``stale`` -- the assets step then
    makes the shot's image as it would any other."""
    if not file_ok:
        return "none"
    if media_policy.stock_cutaways(getattr(ec, "story", None)) and eligible(shot):
        return "current"
    return "stale"


# ---------------------------------------------------------------------- credits

def credits_of(storyboard) -> list:
    """``[{"shot_id", **credit record}]`` of the shots whose clip is a current stock one,
    in storyboard order (``clipping.stock.credits.credit_record``'s fields)."""
    out = []
    for shot in (storyboard or {}).get("shots") or ():
        clip = (shot.get("assets") or {}).get("clip") or {}
        if clip.get("route") == ROUTE and clip.get("state") == "current" and shot["assets"].get("video"):
            out.append(dict(clip.get("source") or {}, shot_id=shot["shot_id"]))
    return out


def credit_text(credits) -> str:
    """The "Stock footage: ..." line a description carries: each credit once, in order."""
    lines = list(dict.fromkeys(item.get("credit") for item in credits or () if item.get("credit")))
    return f"Stock footage: {'; '.join(lines)}" if lines else ""


def providers_of(credits) -> list:
    """The sites to credit with a link ("Videos provided by Pexels"), in order, once each."""
    return list(dict.fromkeys(item["provider"] for item in credits or () if item.get("provider") in PROVIDER_SITES))


PROVIDER_SITES = {"pexels": ("Pexels", "https://www.pexels.com"), "pixabay": ("Pixabay", "https://pixabay.com")}


def _write_atomic(path, text) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)


def write_credits(ec, storyboard) -> list:
    """``assets/stock_credits.json`` and ``.txt`` of the episode's current stock clips
    (:func:`credits_of`); with none, a file of an earlier run is removed. Returns the
    credits. A path the store refuses (a symlink) is skipped, said in the log."""
    credits = credits_of(storyboard)
    store = ec.store
    paths = {}
    for name in (CREDITS_JSON, CREDITS_TXT):
        try:
            paths[name] = store.episode_stock_credits_path(ec.story_id, ec.ep, name, create=bool(credits))
        except KeyError:
            logger.warning("stock credits: %s is not a real file; left alone", name)
    if not credits:
        for path in paths.values():
            if os.path.isfile(path):
                os.remove(path)
        return credits
    body = {"$schema": CREDITS_SCHEMA, "ep": ec.ep, "credits": credits}
    lines = [f"{item['shot_id']}: {item.get('credit') or item.get('key')}" for item in credits]
    if CREDITS_JSON in paths:
        _write_atomic(paths[CREDITS_JSON], json.dumps(body, ensure_ascii=False, indent=2) + "\n")
    if CREDITS_TXT in paths:
        _write_atomic(paths[CREDITS_TXT], "\n".join(lines) + "\n")
    return credits


# ------------------------------------------------------------------- the files

def clip_name(shot_id) -> str:
    """``shot_03.stock.mp4`` for shot ``sh03``."""
    return f"shot_{shot_id[2:]}{schemas.SHOT_STOCK_SUFFIX}.mp4"


def clip_rel(shot_id) -> str:
    return f"{schemas.SHOT_CLIP_DIR}/{clip_name(shot_id)}"


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_frame(clip_path, out_path, size, *, run=None) -> bool:
    """One frame of *clip_path* at :data:`FRAME_AT_S` seconds, filled to *size* (the
    shot image's size at the story's frame: scaled up to cover it, then centre-cropped)
    into *out_path* (a ``.jpg``). False when ffmpeg is missing or answers no file. *run*
    is ``subprocess.run`` (None) or a test's stand-in."""
    run = run or subprocess.run
    width, height = size
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{FRAME_AT_S:g}", "-i", clip_path,
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}",
            "-frames:v", "1", "-q:v", "2", out_path]
    try:
        result = run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=FRAME_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0


def _remove(path) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _drop_other_images(ec, shot_id, keep_ext) -> None:
    from . import imaging

    for ext in imaging.KEPT_EXTENSIONS:
        if ext == keep_ext:
            continue
        try:
            path = ec.store.episode_asset_path(ec.story_id, ec.ep, "shots", f"shot_{shot_id[2:]}.{ext}")
        except KeyError:
            continue
        if os.path.isfile(path):
            _remove(path)


# ------------------------------------------------------------------------ fill

def wants_fill(ec, shot, script, assets_doc=None) -> bool:
    """Whether :func:`fill` would look a stock clip up for *shot* now: the switch is on,
    the shot is eligible and has neither a current keyframe nor a clip -- a stock
    keyframe whose clip went stale (a query that moved) is looked up again; a locked
    image, a pending redraw, a clip bought or uploaded (current as recorded, its file
    there), and a keyframe someone replaced under a stock clip are never touched."""
    from .steps import assets as assets_step
    from .steps import clips as clips_step

    if not media_policy.stock_cutaways(getattr(ec, "story", None)) or not eligible(shot, script):
        return False
    assets = shot["assets"]
    if assets.get("locked") or assets.get("pending"):
        return False
    clip = assets.get("clip") or {}
    if clip.get("pending"):
        return False
    if clip and clip.get("route") != ROUTE and clip.get("state") == "current" \
            and clips_step.shot_clip_path(ec, shot) is not None:
        return False
    image = assets_step.shot_image_path(ec, shot)
    if keyframe_is_stock(shot):
        sha = assets_step._sha256_file(image) if image is not None else None
        if (clip.get("route") == ROUTE and clip.get("state") == "current" and sha is not None
                and clips_step.shot_clip_path(ec, shot) is not None
                and clip_is_current(ec, shot, script, clip, sha)):
            return False
        return True
    if clip.get("route") == ROUTE:
        return False
    if image is not None and assets_step.shot_state(ec, shot) == "current":
        return False
    return True


def drop_stock(ec, shot) -> None:
    """Take a stock cutaway off *shot* (its record, its clip file and its keyframe file),
    so that the assets step makes the shot's image as usual."""
    assets = shot["assets"]
    shot_id = shot["shot_id"]
    if (assets.get("clip") or {}).get("route") == ROUTE:
        assets["clip"] = None
        if assets.get("video") == clip_rel(shot_id):
            assets["video"] = None
    try:
        _remove(ec.store.episode_asset_path(ec.story_id, ec.ep, "clips", clip_name(shot_id)))
    except KeyError:
        pass
    if keyframe_is_stock(shot):
        if assets.get("image"):
            try:
                _remove(ec.store.episode_asset_path(ec.story_id, ec.ep, "shots", assets["image"].rpartition("/")[2]))
            except KeyError:
                pass
        assets["image"], assets["provider"] = None, None
        for key in ("model", "route", "prompt_hash", "source", "cache_key", "generated_at", "est_usd"):
            assets.pop(key, None)


def fill(ec, script, storyboard, assets_doc=None, *, env=None, sources=None, pool=None, run=None, download=None,
         on_log=None, now=None) -> dict:
    """Fill *storyboard*'s eligible shots with stock cutaways (module docstring), in place:
    each shot filled has its keyframe file and its ``.stock.mp4`` written and its
    ``assets`` updated (``image``, ``video``, ``route``, ``clip``); the caller writes
    the storyboard. Returns ``{"filled": [shot ids], "kept": [shot ids already stock and
    current], "missing": [{"shot_id", "query", "reason"}], "credits": [...]}``.

    *env* the Settings' values (the sources' keys, ``BROLL_SOURCES``, ``BROLL_LOCAL_DIR``);
    *sources* a list of source names or objects in place of the configured order, *pool* a
    ``StockPool``; *run* the ffmpeg runner of the frame, *download* ``clipping.stock.download``'s
    stand-in. Nothing here raises for a shot: a failure is a line in the log and the shot is
    generated as usual."""
    from clipping.providers import gating
    from clipping.stock import base as stock_base
    from clipping.stock import clips as stock_clips
    from clipping.stock.credits import credit_record

    on_log = on_log or (lambda _line: None)
    now = now or _utc_now()
    story = getattr(ec, "story", None)
    summary = {"filled": [], "kept": [], "missing": [], "credits": []}
    if not media_policy.stock_cutaways(story):
        return summary
    aspect = media_policy.aspect(story)
    merged = stock_clips.environment(gating.merged_env(env or {}))
    chosen = stock_clips.sources_of(merged) if sources is None else sources
    pool = pool if pool is not None else stock_base.StockPool()
    download = download or stock_base.download
    size = media_policy.image_size(story)
    for shot in storyboard["shots"]:
        if not eligible(shot, script):
            continue
        shot_id = shot["shot_id"]
        if not wants_fill(ec, shot, script, assets_doc):
            if is_stock_clip(shot) and keyframe_is_stock(shot):
                summary["kept"].append(shot_id)
            continue
        query = query_of(ec, shot, script)
        if not query:
            summary["missing"].append({"shot_id": shot_id, "query": "", "reason": "its place has no name"})
            continue
        duration = float(shot.get("duration_s") or 0.0)
        try:
            found = stock_base.search(query, aspect=aspect, min_duration_s=duration + MARGIN_S, sources=chosen,
                                      env=merged, pool=pool)
        except Exception as exc:  # noqa: BLE001 - a stock search never stops the assets step
            found, why = None, f"the search failed ({stock_base.redact(exc)})"
        else:
            why = "no stock clip matched"
        if found is None:
            summary["missing"].append({"shot_id": shot_id, "query": query, "reason": why})
            if keyframe_is_stock(shot) or is_stock_clip(shot):
                drop_stock(ec, shot)
            on_log(f"🎞 Shot {shot_id}: {why} for “{query}” ({duration + MARGIN_S:g} s, {aspect}): it is generated as usual.")
            continue
        try:
            dest = ec.store.episode_asset_path(ec.story_id, ec.ep, "clips", clip_name(shot_id), create=True)
            frame = ec.store.episode_asset_path(ec.story_id, ec.ep, "shots", f"shot_{shot_id[2:]}.jpg", create=True)
        except KeyError:
            summary["missing"].append({"shot_id": shot_id, "query": query, "reason": "its file is not a real file"})
            on_log(f"🎞 Shot {shot_id}: its stock files are not real files (a symlink is never followed): "
                   "it is generated as usual.")
            continue
        try:
            download(found, dest)
        except stock_base.StockError as exc:
            summary["missing"].append({"shot_id": shot_id, "query": query, "reason": str(exc)})
            on_log(f"🎞 Shot {shot_id}: {stock_base.redact(exc)}: it is generated as usual.")
            continue
        part = os.path.join(os.path.dirname(frame), f".stock-{shot_id[2:]}.jpg")
        if not extract_frame(dest, part, size, run=run):
            _remove(part)
            _remove(dest)
            summary["missing"].append({"shot_id": shot_id, "query": query, "reason": "no frame could be cut"})
            on_log(f"🎞 Shot {shot_id}: a frame of the stock clip could not be cut (is ffmpeg installed?): "
                   "it is generated as usual.")
            continue
        os.replace(part, frame)
        _drop_other_images(ec, shot_id, "jpg")
        digest = query_hash(query, aspect)
        source = credit_record(found)
        assets = shot["assets"]
        for key in ("consistency", "continuity"):
            assets.pop(key, None)
        assets.update({
            "image": f"{schemas.SHOT_IMAGE_DIR}/shot_{shot_id[2:]}.jpg", "seed": None, "provider": "stock",
            "model": found.provider, "route": ROUTE, "prompt_hash": digest, "est_usd": 0.0, "cache_key": None,
            "generated_at": now, "note": None, "pending": None, "source": f"{LINK_PREFIX}{found.provider}",
            "video": clip_rel(shot_id),
            "clip": {
                "state": "current", "link": f"{LINK_PREFIX}{found.provider}", "route": ROUTE,
                "clip_s": max(1, int(round(found.duration_s or 0)) or int(math.ceil(duration)) or 1), "est_usd": 0.0,
                "prompt_hash": digest, "image_sha256": _sha256_file(frame), "cache_key": None,
                "generated_at": now, "note": None, "sha256": _sha256_file(dest), "source": source,
            },
        })
        summary["filled"].append(shot_id)
        on_log(f"🎞 Shot {shot_id}: stock footage from {found.provider} for “{query}” "
               f"(by {found.author or 'an unnamed contributor'}, free): no image or clip is generated.")
    summary["credits"] = credits_of(storyboard)
    return summary
