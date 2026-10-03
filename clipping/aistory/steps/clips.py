"""An episode's Tier-2/3 clips before any is made: where a clip lives, when
it is current, and what animating would cost -- the ``video`` part of the
assets estimate (AI Story phase 6, stage 7; spec 2.8, 8.1, 8.5; DEC-202,
DEC-203, DEC-208, DEC-211).

Nothing here generates a clip, ever. With *probe_local*, a local ComfyUI is
asked ``GET /system_stats`` (its card picks the hardware profile, hence the
workflow) and ``GET /object_info`` (can that workflow run) -- the Settings
video test's no-call check (RC-V8); without it the local link is unknown,
so not ready.

**Where things live** -- what the video phase (stage 8) writes and the
renderer (stage 9) reads:

* A shot's clip is recorded **next to its image**, in the storyboard shot's
  ``assets`` (DEC-155; ``schemas._STORYBOARD_CLIP_SCHEMA``)::

      "clip": {"state": "current" | "stale" | "failed", "link", "route": "local" | "paid",
               "clip_s", "est_usd", "prompt_hash", "image_sha256", "cache_key", "generated_at",
               "note"?, "pending"?, "reason"?}

  and its file is ``assets/clips/shot_NN.mp4`` (store kind ``clips``), named
  by the shot's ``assets.video`` -- set only while the clip is current. The
  renderer reads ``assets.video`` from the storyboard as it reads
  ``assets.image`` (``render/plan.py``). The assets step writes the
  storyboard with its approval kept, as it does for an image.
* The user's per-shot overrides -- ``keep_still``, ``animate`` (a pin),
  ``keep_native_audio`` -- live in ``assets.json``'s ``shots`` map
  (``workflow.patch_assets``), so choosing which shots move never clears
  the storyboard's approval; :func:`shot_flags`
  (``video_plan.effective_shot_flags``) resolves them.

**The estimate** (:func:`video_units`), at ``generation_profile.tier >= 2``
only: the episode's video link -- its recorded one (``assets.json``'s
``links.video``, A-087), else the route decision (``video_plan.
video_route``) and the budget profile's ``video_link_policy`` among the
keyed hosted links -- then ``video_plan.plan_animation`` with the board's
shots and effective flags, the script's scene functions and line timings,
the link's price per second, the episode's cap and what is committed
already, and the budget profile's ``animate`` mode (the ``free`` profile
animates only on a local ComfyUI, at $0: DEC-203). The ETA is the measured
history's (``gen_timings``), never a guess.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import hashlib
import json
import os
import struct

from clipping.providers import adapters as adapters_mod
from clipping.providers import budget as budget_mod
from clipping.providers import gating, gen_timings, local_comfyui, pricing
from clipping.providers import generation as gen
from clipping.providers import video as video_providers
from clipping.providers.registry import ChainError, describe

from .. import hardware, imaging, media_policy, prompt_budgets, prompting, schemas, video_plan
from .. import shots as shots_mod
from . import episode_common, sticky_link
from .llm_call import StepFailed

CLIPS_KIND = "clips"
CLIPS_DIR = schemas.SHOT_CLIP_DIR
LOCAL_LINK = "local/comfyui"

# What a shot's clip is, derived: no clip; current (its record is current,
# its file on disk, made from the shot's image and video prompt as they are
# now, on the episode's video link); stale (any of those moved); failed (its
# call did not answer, or a re-animate is pending).
CLIP_DERIVED_STATES = ("none", "current", "stale", "failed")

# budget_profiles.json's ``video_link_policy`` values; a profile that names
# none (``free``) takes the cheapest. ``first_with_audio`` (phase 7 follow-up,
# stage E): an ambience story's (``media_policy.ambience``) first keyed link
# whose clips always carry their own sound, else ``first_in_chain`` said.
CHEAPEST, FIRST, FIRST_WITH_AUDIO = "cheapest_available", "first_in_chain", "first_with_audio"
LINK_POLICIES = (CHEAPEST, FIRST, FIRST_WITH_AUDIO)
assert LINK_POLICIES == budget_mod.VIDEO_LINK_POLICIES

ETA_NONE = "no measured history"

# Why the local link is not ready in an estimate made without *probe_local*
# (a check before a job exists asks no server): the assets step asks it.
LOCAL_NOT_ASKED = "not asked before the assets step runs"

# A shot not timed yet (0 s) is asked at the link's shortest length.
_UNTIMED_S = 0.01


def _and(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _s(count) -> str:
    return "" if count == 1 else "s"


def _canonical_sha256(payload) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------- the files

def clip_name(shot_id) -> str:
    """``shot_03.mp4`` for shot ``sh03`` (``schemas.SHOT_CLIP_NAME_PATTERN``)."""
    return f"shot_{shot_id[2:]}.mp4"


def clip_rel(shot_id) -> str:
    """What a current clip's ``assets.video`` holds: ``assets/clips/shot_NN.mp4``."""
    return f"{CLIPS_DIR}/{clip_name(shot_id)}"


def shot_clip_path(ec, shot):
    """The real path of the clip a shot's ``assets.video`` names, or None
    when there is none on disk (a symlink is never followed)."""
    video = shot["assets"].get("video")
    if not video:
        return None
    try:
        path = ec.store.episode_asset_path(ec.story_id, ec.ep, CLIPS_KIND, video.rpartition("/")[2])
    except KeyError:
        return None
    return path if os.path.isfile(path) else None


# The boxes from an MP4's top level down to a track's handler (ISO/IEC
# 14496-12): moov > trak > mdia > hdlr, whose handler type names the track's
# kind -- ``soun`` for a sound track.
_TRACK_PATH = (b"moov", b"trak", b"mdia")
_SOUND_HANDLER = b"soun"


def _mp4_has_sound(handle, start, end, depth) -> bool:
    offset = start
    while offset + 8 <= end:
        handle.seek(offset)
        header = handle.read(8)
        if len(header) < 8:
            return False
        size, kind = struct.unpack(">I4s", header)
        head = 8
        if size == 1:
            large = handle.read(8)
            if len(large) < 8:
                return False
            size, head = struct.unpack(">Q", large)[0], 16
        elif size == 0:
            size = end - offset
        if size < head or offset + size > end:
            return False
        if depth < len(_TRACK_PATH) and kind == _TRACK_PATH[depth]:
            if _mp4_has_sound(handle, offset + head, offset + size, depth + 1):
                return True
        elif depth == len(_TRACK_PATH) and kind == b"hdlr":
            handle.seek(offset + head + 8)  # version and flags, pre_defined
            if handle.read(4) == _SOUND_HANDLER:
                return True
        offset += size
    return False


def clip_has_audio(path) -> bool:
    """Whether the clip at *path* carries a sound track: an MP4 track whose
    handler is ``soun`` (phase 6 stage 10). Read from the file's own boxes
    in pure Python -- no process, so a dry run (the render's ``current`` and
    ``changes``) decides as the render does -- whatever the link said it
    would make. False for a file that is not such an MP4 or cannot be read:
    the shot then keeps its lines."""
    try:
        with open(path, "rb") as handle:
            return _mp4_has_sound(handle, 0, os.fstat(handle.fileno()).st_size, 0)
    except (OSError, TypeError, ValueError):
        return False


# ---------------------------------------------------------------- the flags

def tier_of(ec) -> int:
    """The story's tier (``generation_profile.tier``): 1 renders stills with
    motion; 2 and 3 animate shots."""
    return int(ec.story["generation_profile"]["tier"])


def shot_flags(shot, assets_doc) -> dict:
    """``video_plan.effective_shot_flags`` of *shot* with its overrides in
    *assets_doc* (``assets.json``)."""
    return video_plan.effective_shot_flags(shot, video_plan.shot_overrides(assets_doc, shot["shot_id"]))


def flags_moved(storyboard, assets_doc) -> bool:
    """Whether an override in *assets_doc* changes any shot's flags from the
    storyboard's own (an override equal to it changes nothing)."""
    return any(shot_flags(shot, assets_doc) != video_plan.effective_shot_flags(shot)
               for shot in storyboard["shots"])


# ------------------------------------------------------------ a clip's state

def clip_prompt_hash(prompt, negative, *, native_audio=False, resolution="720p") -> str:
    """sha256 of what a clip is asked for: the video prompt, its negative,
    whether the model's own sound is kept (tier 3) and, only when it is not
    720p, the size it is bought at (phase 7 stage 4, DEC-227: a story switched
    to 1080p re-buys its clips; a 720p request hashes exactly as before, so
    every stored clip stays current). The keyframe is the record's
    ``image_sha256``, the link its ``link``."""
    payload = {"prompt": prompt, "negative": negative or "", "native_audio": bool(native_audio)}
    if resolution and resolution != "720p":
        payload["resolution"] = resolution
    return _canonical_sha256(payload)


# The cues of ``schemas.SFX_PACKS`` a clip is never asked for (stage E): a
# stinger or a transition whoosh is not a sound of the place (and reads as
# music), a crowd's gasp is voices -- each would contradict the prompt's "no
# music, no voices". They stay the shipped sound's alone, at their anchors.
CLIP_SFX_EXCLUDED = frozenset({"dramatic_sting", "record_scratch", "slide_whistle", "twinkle", "whoosh_sharp",
                               "whoosh_soft", "gasp_crowd"})


def audio_brief(ec, shot, script) -> dict:
    """What an ambience story's *shot* should sound like (stage E;
    ``video_plan.build_video_prompt``'s *audio*)::

        {"place": "<its place's descriptor> (<time of day>, <its light>)",
         "sfx": ["door slam", ...], "speakers": [handle, ...]}

    The sound effects are its scene's cues (``sfx_cues``) that fall in the
    shot: a cue at a line the shot holds, and a cue at the scene's start on
    the shot holding the scene's first line (every shot of a scene with no
    line) -- a shot alone does not say whether it is its scene's first, so
    an establishing shot before the first line leaves it to the next; the
    shipped sound still plays at its exact anchor -- but never one of
    :data:`CLIP_SFX_EXCLUDED`. The speakers are the
    shot's lines' characters that are in its frame (``subject_tags``), by
    their handles, never a name (spec 2.3)."""
    scene = next((item for item in script["scenes"] if item["scene_id"] == shot["scene_id"]), None) or {}
    entities = getattr(ec, "entities", None) or {}
    place = (entities.get("places") or {}).get(scene.get("place_id")) or {}
    variant = scene.get("time_variant") or ""
    light = ((place.get("look") or {}).get("lighting") or {}).get(variant) or ""
    when = ", ".join(part.strip().rstrip(".") for part in (variant.replace("_", " "), light) if part.strip())
    where = shots_mod._lower_first((place.get("descriptor") or "the place").strip().rstrip("."))
    lines = scene.get("lines") or []
    first = shot["lines"][:1] == [lines[0]["line_id"]] if lines else True
    sfx = [cue["cue"].replace("_", " ") for cue in scene.get("sfx_cues") or ()
           if ((cue["at"] == "start" and first) or cue["at"] in shot["lines"]) and cue["cue"] not in CLIP_SFX_EXCLUDED]
    in_frame = set()
    for tag in shot.get("subject_tags") or ():
        try:
            kind, entity_id, _variant = shots_mod.parse_tag(tag)
        except ValueError:
            continue
        if kind == "char":
            in_frame.add(entity_id)
    characters = entities.get("characters") or {}
    handles = shots_mod.character_handles(characters) if characters else {}
    speakers = [handles[line["speaker"]] for line in lines
                if line["line_id"] in shot["lines"] and line["speaker"] in in_frame and line["speaker"] in handles]
    return {"place": f"{where} ({when})" if when else where, "sfx": list(dict.fromkeys(sfx)),
            "speakers": list(dict.fromkeys(speakers))}


def clip_request_parts(ec, shot, script, *, tier, flags, note=None, link=None) -> dict:
    """``{prompt, negative, native_audio, hash, over}`` of *shot*'s clip
    request now (``video_plan.build_video_prompt``): at tier 3 a shot that
    keeps its native audio (``keep_native_audio``) also says its lines;
    otherwise the model's sound is discarded and the prompt carries none
    (DEC-201).

    An ambience story (stage E, ``media_policy.ambience``) asks every clip
    for its sound (``native_audio``: a link whose sound is optional makes
    it) with the shot's sound brief in its prompt (:func:`audio_brief`) and
    never voices a line: ``keep_native_audio`` is not read -- every line is
    heard in its pinned TTS voice.

    *link* (stage F2: the label of the link the clip goes to, when known):
    the brief takes its share of that link's budget
    (``prompt_budgets.clip_audio_words``) rather than a fixed 140 words, and
    ``over`` says why a layered shot's prompt cannot be sent to it now
    (``prompt_budgets.over_sentence``: over the link's limit, or over its
    budget because it was built to another link's) -- None when it can, or
    with no link known."""
    story = getattr(ec, "story", None)
    ambient = tier == 3 and media_policy.ambience(story)
    native = tier == 3 and bool(flags.get("keep_native_audio")) and not ambient
    lines = ()
    if native:
        texts = {line["line_id"]: line["text"] for scene in script["scenes"] for line in scene["lines"]}
        lines = [texts[line_id] for line_id in shot["lines"] if line_id in texts]
    prompt, negative = video_plan.build_video_prompt(shot, ec.style_lock, tier=tier if tier in (2, 3) else 2,
                                                     lines=lines, note=note,
                                                     audio=audio_brief(ec, shot, script) if ambient else None,
                                                     audio_budget=prompt_budgets.clip_audio_words(link)
                                                     if ambient and link else None)
    resolution = media_policy.video_resolution(story)
    asked = native or ambient
    over, refit, sent = None, None, prompt
    if link and shot.get("prompt_layout"):
        budget = prompt_budgets.clip_audio_words(link) if ambient else prompt_budgets.clip_words(link)
        over = prompt_budgets.over_sentence("clip", shot["shot_id"], link, prompt, budget=budget)
        if over and not ambient:
            # DEC-249: over with its note at its tail (a re-animate's, appended after the stored prompt was
            # built to its budget), or built to another link's budget: resolved again to the room left. The
            # ambience prompt fits its brief and note itself (prompting.clip_prompt_with_audio).
            sent, over, refit = _fitted_clip(ec, shot, script, prompt, note=note, link=link, budget=budget,
                                             tier=tier, lines=lines, over=over)
    return {"prompt": sent, "negative": negative, "native_audio": asked,
            "hash": clip_prompt_hash(prompt, negative, native_audio=asked, resolution=resolution), "over": over,
            "refit": refit}


def _fitted_clip(ec, shot, script, prompt, *, note, link, budget, tier, lines, over) -> tuple:
    """``(sent prompt, over, info)`` of a layered *shot*'s clip on *link*
    when its stored ``video_prompt`` with *note* (*prompt*) is over *budget*
    (DEC-249): the shot resolved again (``shots.resolve_stored``) to the
    room the note leaves -- the clip's context layers go first, then the
    motion is cut; the note is kept whole -- and the prompt built again
    from it; *over* why nothing can be sent when even that fails; *info*
    ``{"from", "to", "note", "budget"}`` in words. The hash is never made
    of this prompt: it stays the stored one's."""
    from . import script as script_step  # the step imports this module: a cycle at import time

    tier = tier if tier in (2, 3) else 2
    bare, _negative = video_plan.build_video_prompt(shot, ec.style_lock, tier=tier, lines=lines, note=None)
    note_words = len(prompt.split()) - len(bare.split())
    try:
        board = episode_common.read_episode(ec, episode_common.STORYBOARD_DOC)
    except StepFailed:
        board = None
    budgets = prompt_budgets.for_links(None, link)._replace(keyframe=prompt_budgets.KEYFRAME_CEILING_WORDS,
                                                             clip=budget - note_words)
    try:
        resolved = shots_mod.resolve_stored(shot, script=script, storyboard=board, entities=ec.entities,
                                            style_lock=ec.style_lock, consistency_mode=ec.consistency_mode,
                                            ledger=script_step.ledger_of(ec), budgets=budgets)
    except shots_mod.PromptOverBudget as exc:
        if note_words:
            over = prompt_budgets.note_over_sentence("clip", shot["shot_id"], link, len(prompt.split()), note_words,
                                                     budget=budget, shortest=exc.words)
        return prompt, over, None
    except (KeyError, ValueError):
        return prompt, over, None
    sent, _negative = video_plan.build_video_prompt(dict(shot, video_prompt=resolved["video_prompt"]), ec.style_lock,
                                                    tier=tier, lines=lines, note=note)
    over = prompt_budgets.over_sentence("clip", shot["shot_id"], link, sent, budget=budget)
    return sent, over, {"from": len(prompt.split()), "to": len(sent.split()), "note": note_words, "budget": budget}


def clip_state(ec, shot, script, *, link, tier, flags, image_sha) -> str:
    """One of :data:`CLIP_DERIVED_STATES` for *shot* now: *link* is the
    episode's video link (None: any), *image_sha* the sha256 of the shot's
    image on disk (None: none)."""
    clip = shot["assets"].get("clip")
    if not clip:
        return "none"
    if clip.get("pending") or clip["state"] == "failed":
        return "failed"
    if clip["state"] != "current" or shot_clip_path(ec, shot) is None:
        return "stale" if clip["state"] == "stale" else "none"
    if link is not None and not sticky_link.on_link(clip["link"], link):
        return "stale"
    if image_sha is None or clip["image_sha256"] != image_sha:
        return "stale"
    expected = clip_request_parts(ec, shot, script, tier=tier, flags=flags, note=clip.get("note"), link=link)["hash"]
    return "current" if clip["prompt_hash"] == expected else "stale"


# ------------------------------------------------------------ the links

def local_video_status(env, *, transport=None) -> dict:
    """Whether the local ComfyUI can make a clip now, asking it only
    ``GET /system_stats`` and ``GET /object_info`` (never a generation; the
    Settings video test's check, ``routes/settings.py::_check_local_video``,
    duplicated: ``clipping/`` does not import ``web/``)::

        {"ok": bool, "template": name | None, "profile": name | None, "note": sentence}
    """
    merged = gating.merged_env(env)
    client = local_comfyui.ComfyUIClient(gen.local_url("comfyui", merged), transport=transport)
    try:
        stats = client.system_stats()
    except Exception as exc:  # noqa: BLE001 - any failure to answer is a server that is not there
        return {"ok": False, "template": None, "profile": None,
                "note": f"ComfyUI unreachable at {client.base_url} ({type(exc).__name__}: {exc})"}
    profile, vram, gpu = hardware.profile_from_system_stats(stats, in_container=gen.in_container())
    name = hardware.video_workflow_for(profile)
    card = f"{gpu}, {vram:g} GB" if vram else "no GPU reported"
    if name is None:
        return {"ok": False, "template": None, "profile": profile,
                "note": f"ComfyUI at {client.base_url} ({card}) is profile {profile}: no local video workflow for it"}
    try:
        problems = local_comfyui.validate_template(local_comfyui.load_template(name), client.object_info())
    except Exception as exc:  # noqa: BLE001 - reported, never raised from an estimate
        return {"ok": False, "template": name, "profile": profile, "note": f"{type(exc).__name__}: {exc}"}
    if problems:
        return {"ok": False, "template": name, "profile": profile,
                "note": local_comfyui.install_message(name, client.base_url, problems)}
    return {"ok": True, "template": name, "profile": profile,
            "note": f"{name} can run on ComfyUI at {client.base_url} ({card}, profile {profile})"}


def hosted_rows(chain, merged, adapters, *, resolution=None) -> list:
    """One row per hosted link of VIDEO_CHAIN, calling nothing:
    ``{"link", "status": "keyed" | "skipped", "reason", "price_per_second"}``
    -- keyed: it has an adapter, a table of sellable lengths, its key and a
    price per second (every hosted clip is paid), at *resolution* when the
    table prices that size apart (``pricing.price_key``, phase 7 stage 4)."""
    rows = []
    for link in chain:
        if link.provider == "local":
            continue
        label = describe(link)
        row = {"link": label, "status": "skipped", "reason": None, "price_per_second": None}
        if gen.adapter_for(gen.VIDEO, link.provider, adapters) is None:
            row["reason"] = f"no adapter yet for {link.provider} video"
        elif label in video_providers.REFUSED_LINKS:
            row["reason"] = video_providers.REFUSED_LINKS[label]
        elif label not in video_providers.CLIP_LENGTHS:
            row["reason"] = "no table of the clip lengths it sells"
        elif gen.missing_keys(link, merged):
            row["reason"] = imaging.missing_keys_reason(gen.missing_keys(link, merged))
        else:
            try:
                price = pricing.price_for(link, resolution)
            except pricing.PriceUnknown as exc:
                row["reason"] = str(exc)
            else:
                if price.unit != "second":
                    row["reason"] = f"priced per {price.unit}, not per second"
                else:
                    row.update(status="keyed", reason="keyed", price_per_second=float(price.usd))
        rows.append(row)
    return rows


def makes_sound(link, *, asked=True) -> bool:
    """Whether a clip of *link* (a label) carries the model's own sound
    (``video.AUDIO``): always, or -- an optional one (LTX) -- when *asked*."""
    audio = video_providers.AUDIO.get(link, "never")
    return audio == "always" or (audio == "optional" and asked)


def pick_hosted(rows, policy, *, want_sound=False):
    """The keyed hosted row *policy* picks: :data:`FIRST` the first in chain
    order; :data:`CHEAPEST` (the default) the lowest price per second, chain
    order breaking a tie; :data:`FIRST_WITH_AUDIO` with *want_sound* (an
    ambience story, stage E) the first whose clips always carry sound
    (``video.AUDIO`` ``always``: Veo), else -- none keyed -- the first in
    chain order, which the estimate then says is silent; without
    *want_sound* it is :data:`FIRST`. An optional sound (LTX) is not looked
    for: its smallest size is 1080p, dearer than the episode's cap allows
    for a whole episode, and its sound is unproven; once it is the
    episode's link, its clips ask for sound (``clip_request_parts``). None
    when no row is keyed."""
    keyed = [(index, row) for index, row in enumerate(rows) if row["status"] == "keyed"]
    if not keyed:
        return None
    if policy == FIRST_WITH_AUDIO and want_sound:
        sounding = [row for _index, row in keyed if video_providers.AUDIO.get(row["link"]) == "always"]
        return sounding[0] if sounding else keyed[0][1]
    if policy in (FIRST, FIRST_WITH_AUDIO):
        return keyed[0][1]
    return min(keyed, key=lambda pair: (pair[1]["price_per_second"], pair[0]))[1]


def longest_clip_s(link):
    """The longest clip *link* (a label) sells (``video.CLIP_LENGTHS``), or
    None: a local link, or one with no table."""
    lengths = video_providers.CLIP_LENGTHS.get(link or "")
    return max(lengths) if lengths else None


# A placeholder key: what a hosted link would be picked as once its key is
# set (:func:`planned_link`). Only ever read by ``hosted_rows``; never sent.
_ASSUMED_KEY = "assumed-for-planning"


def planned_link(ec, env, *, assets_doc=None, adapters=None):
    """The hosted link *ec*'s episode will buy its clips on, as
    :func:`video_units` picks it, calling nothing (stage E: what a fully
    animated storyboard plans its shots for): the episode's recorded link
    (*assets_doc*'s ``links.video``); else the budget profile's
    ``video_link_policy`` among the keyed hosted links of VIDEO_CHAIN (with
    sound wanted on an ambience story); none keyed: the same pick with the
    keys assumed set -- the link the profile takes once its key is there.
    None: a local link or route, a profile that buys no clip, a chain that
    cannot be read, no hosted link at all."""
    recorded = sticky_link.recorded(assets_doc, sticky_link.VIDEO)
    if recorded is not None:
        return None if recorded["link"].startswith("local/") else recorded["link"]
    profile = ec.story["generation_profile"]
    if profile.get("route") == "local":
        return None
    try:
        settings = budget_mod.profile_settings(profile["budget_profile"])
    except (OSError, ValueError, KeyError):
        return None
    if settings.get("animate") == "none":
        return None
    if adapters is None:
        adapters_mod.load_all()
    merged = gating.merged_env(env)
    try:
        chain = gen.chain_from_env(gen.VIDEO, merged)
    except ChainError:
        return None
    resolution = media_policy.video_resolution(ec.story)
    policy = settings.get("video_link_policy") or CHEAPEST
    want = int(profile.get("tier") or 1) == 3 and media_policy.ambience(ec.story)
    row = pick_hosted(hosted_rows(chain, merged, adapters, resolution=resolution), policy, want_sound=want)
    if row is None:
        assumed = dict(merged)
        for link in chain:
            assumed.update((name, _ASSUMED_KEY) for name in gen.missing_keys(link, merged))
        row = pick_hosted(hosted_rows(chain, assumed, adapters, resolution=resolution), policy, want_sound=want)
    return row["link"] if row is not None else None


def planned_image_link(ec, env, *, assets_doc=None):
    """The link episode *ec*'s keyframes are made on, as a label, calling
    nothing (stage F2: what the storyboard builds each keyframe prompt to):
    the episode's recorded image link (*assets_doc*'s ``links.image``,
    A-087), else the first link of its keyframe chain
    (``media_policy.role_chain`` for the story's consistency mode: the
    role's quality links on a v2 story, the env chain -- *env* the Settings
    values -- on a legacy one). None when that chain cannot be read: the
    assets step says why when it runs."""
    recorded = sticky_link.recorded(assets_doc, sticky_link.IMAGE)
    if recorded is not None:
        return recorded["link"]
    # ``refimages.PROMPT_ONLY``: a text-to-image chain; every other mode edits with references.
    kind = gen.IMAGE if ec.consistency_mode == "prompt_only" else gen.IMAGE_EDIT
    try:
        chain = media_policy.role_chain("keyframe", kind, gating.merged_env(env), ec.story)
    except ChainError:
        return None
    return describe(chain[0]) if chain else None


def episode_budgets(ec, env, *, assets_doc=None) -> prompting.Budgets:
    """The word budgets episode *ec*'s v2 prompts are built to (stage F2,
    ``prompt_budgets.for_links``): the keyframe's from
    :func:`planned_image_link`, the clip's from :func:`planned_link` (the
    episode's recorded link, else the profile's pick, keys asked then
    aside). A legacy story's prompts have no budget: the defaults, nothing
    read."""
    if not media_policy.is_v2(ec.story):
        return prompting.Budgets()
    return prompt_budgets.for_links(planned_image_link(ec, env, assets_doc=assets_doc),
                                    planned_link(ec, env, assets_doc=assets_doc))


# ----------------------------------------------------------- the estimate

# How much longer than its clip a shot of a fully animated story may run, its
# clip held on its last frame (DEC-208); past it, the clip cannot cover the
# shot and the plan is refused (stage E, ``too_long``).
HOLD_TOLERANCE_S = 0.5


def _too_long(rows, link) -> str | None:
    """The refusal of a fully animated story's plan whose shots *rows*
    (``plan`` rows) run longer than *link*'s longest clip by more than
    :data:`HOLD_TOLERANCE_S`, naming them and the fix, or None."""
    long = [row for row in rows if (row.get("held_s") or 0.0) > HOLD_TOLERANCE_S]
    if not long:
        return None
    longest = longest_clip_s(link)
    named = _and([f"{row['shot_id']} ({row['clip_s'] + row['held_s']:g} s)" for row in long])
    return (f"shot{_s(len(long))} {named} {'runs' if len(long) == 1 else 'run'} longer than the {longest} s clip "
            f"{link} sells, and every shot of this story is one clip: plan the storyboard again (the storyboard step "
            f"plans a scene past {longest} s as two shots), approve it, then run the assets step again")


def _local_note(note) -> str:
    """*note* about the local link, naming it once."""
    return note if note.startswith(LOCAL_LINK) else f"{LOCAL_LINK}: {note}"


def _still_rows(shots, flags, reason) -> list:
    """Every shot still for want of a plan: kept still, else *reason*."""
    return [{"shot_id": shot["shot_id"], "reason": "keep_still" if flags[shot["shot_id"]]["keep_still"] else reason}
            for shot in shots]


def video_units(ec, script, storyboard, assets_doc, *, env, caps, committed_usd, adapters=None, probe_local=False,
                transport=None, image_sha=None, booked=None, hold=None) -> dict:
    """The ``video`` part of the assets estimate at tier >= 2, calling
    nothing but -- with *probe_local* -- a local ComfyUI's status::

        {"tier", "budget_profile", "route", "mode", "route_class": "local" | "paid" | None,
         "link", "source": "record" | "policy" | None, "template", "profile", "price_per_second",
         "plan": [{"shot_id", "clip_s", "est_usd", "why"}], "still": [{"shot_id", "reason"}],
         "count", "seconds", "est_usd", "eta_s": s | None, "eta_note", "links": [...],
         "refused": sentence | None, "over_cap": sentence | None, "ready": bool, "message"}

    ``plan`` is the planner's selection (``video_plan.plan_animation``) in
    plan order; ``count``/``seconds``/``est_usd`` are the clips to make (a
    current clip costs nothing). *caps* are ``assets.spending_caps``' (the
    episode's cap bounds the plan), *committed_usd* what the episode has
    spent plus the rest of this estimate's paid part, *image_sha(shot)* the
    sha256 of a shot's image on disk (for a recorded clip's state).
    *booked(shot, *, link, clip_s, template)* (phase 6 stage 8) says whether
    the story's generation journal already holds a shot's next clip request
    bought -- submitted (the next run collects it) or kept: such a clip is
    planned at $0, ``why`` ``booked``, and not counted as one to buy
    (DEC-152: a clip is never charged twice).

    A route refused only because ``allow_paid`` is off still shows the plan
    on the hosted link it would take, with its price (``refused`` says why,
    ``ready`` false) -- as a paid image refused is priced; a route with no
    link at all has no plan. ``ready`` is false while clips are to be
    bought and cannot be.

    Phase 7 stage 4 (DEC-227): the clips are priced at the story's size
    (``media_policy.video_resolution``: 720p unless the story or its budget
    profile says 1080p); an ``all_shots`` plan over the episode's cap carries
    ``over_cap`` (``video_plan.all_shots_refusal``: the whole plan is
    refused, ``assets.plan_refusal``); a shot longer than the longest clip
    the link sells is planned at that length, ``held_s`` on its row and in
    the message (the render holds the clip's last frame, DEC-208).

    Phase 7 follow-up, stage E: on a story that animates every shot
    (``media_policy.fully_animated``) a shot longer than that by more than
    :data:`HOLD_TOLERANCE_S` refuses the plan -- ``too_long``, the sentence
    naming each such shot and the fix (plan the storyboard again: it plans a
    scene past the link's longest clip as two shots), ``ready`` false --
    instead of buying a clip that cannot cover it. On an ambience story
    (``media_policy.ambience``) the units carry ``ambience``: ``{"sound":
    bool, "note": sentence | None}`` -- whether the link's clips bring their
    own sound, and if not, why and what would.

    Phase 7 stage 6b (RC-Q3): *hold* (``assets.clip_hold``: a v2 episode
    whose keyframes' approval is not current) is the sentence a plan with
    clips to buy is held by -- ``hold`` on the units and in the message; it
    is shown and priced, never bought (the assets step makes no clip while
    it holds). None (every legacy episode): no such key, the units byte for
    byte as before."""
    tier = tier_of(ec)
    profile_name = ec.story["generation_profile"]["budget_profile"]
    route = ec.story["generation_profile"]["route"]
    shots = sorted(storyboard["shots"], key=lambda shot: shot["order"])
    flags = {shot["shot_id"]: shot_flags(shot, assets_doc) for shot in shots}
    units = {"tier": tier, "budget_profile": profile_name, "route": route, "mode": None, "route_class": None,
             "link": None, "source": None, "template": None, "profile": None, "price_per_second": None,
             "plan": [], "still": [], "count": 0, "seconds": 0, "est_usd": 0.0, "eta_s": None,
             "eta_note": ETA_NONE, "links": [], "refused": None, "over_cap": None, "ready": True, "message": ""}

    def stop(message, *, reason, still="no_link"):
        units.update(still=_still_rows(shots, flags, still), refused=reason, ready=False, message=message)
        return units

    if adapters is None:
        adapters_mod.load_all()
    merged = gating.merged_env(env)
    try:
        chain = gen.chain_from_env(gen.VIDEO, merged)
    except ChainError as exc:
        return stop(f"No clip can be planned: {gen.ENV_NAMES[gen.VIDEO]} cannot be used ({exc}).", reason=str(exc))
    try:
        budget_obj = gating.budget_of(merged)
    except ValueError as exc:
        return stop(f"No clip can be planned: the budget settings cannot be used ({exc}).", reason=str(exc))
    try:
        settings = budget_mod.profile_settings(profile_name)
    except (OSError, ValueError, KeyError) as exc:
        return stop(f"No clip can be planned: the {profile_name} budget profile cannot be read ({exc}).",
                    reason=str(exc))

    if all(entry["keep_still"] for entry in flags.values()):
        units.update(still=_still_rows(shots, flags, "keep_still"),
                     message="Every shot is kept still: no clip to make.")
        return units

    # --- the link: the episode's own, else the route and the profile's policy
    resolution = media_policy.video_resolution(ec.story)
    rows = hosted_rows(chain, merged, adapters, resolution=resolution)
    local_listed = any(link.provider == "local" for link in chain)
    local_adapter = gen.adapter_for(gen.VIDEO, "local", adapters) is not None
    local_info = {}

    def local_status():
        if not local_info:
            if not local_listed:
                local_info.update(ok=False, template=None, profile=None,
                                  note=f"{LOCAL_LINK} is not a link of {gen.ENV_NAMES[gen.VIDEO]}")
            elif not local_adapter:
                local_info.update(ok=False, template=None, profile=None, note="no adapter yet for local video")
            elif not probe_local:
                local_info.update(ok=False, template=None, profile=None, note=LOCAL_NOT_ASKED)
            else:
                local_info.update(local_video_status(merged, transport=transport))
        return local_info

    policy = settings.get("video_link_policy") or CHEAPEST
    # Stage E: an ambience story's clips are bought where they make sound.
    ambient = tier == 3 and media_policy.ambience(ec.story)
    # DEC-203: a profile that animates nothing paid (``free``) buys no clip:
    # it animates on a local ComfyUI only, at $0, and never plans on a hosted link.
    local_only = settings["animate"] == "none"
    recorded = sticky_link.recorded(assets_doc, sticky_link.VIDEO)
    link, refusal, row = None, None, None
    if recorded is not None and (recorded["link"].startswith("local/") or not local_only):
        link, units["source"] = recorded["link"], "record"
        if link.startswith("local/"):
            status = local_status() if route != "api" else None
            if status is None:
                refusal = "it is local and the story's route is api"
            elif not status["ok"]:
                refusal = status["note"]
            units.update(template=(status or {}).get("template"), profile=(status or {}).get("profile"))
        else:
            row = next((item for item in rows if item["link"] == link), None)
            if route == "local":
                refusal = "it is hosted and the story's route is local"
            elif row is None:
                refusal = f"it is not a link of {gen.ENV_NAMES[gen.VIDEO]} any more"
            elif row["status"] != "keyed":
                refusal = row["reason"]
            elif not budget_obj.allow_paid:
                refusal = "allow_paid is off"
        if refusal is not None and not (row is not None and row["status"] == "keyed" and route != "local"):
            units.update(link=link, links=rows)
            return stop(f"Episode {ec.ep}'s video link {link} cannot serve now: {refusal}. An episode keeps its "
                        "clips on one link, so no other link is planned.", reason=refusal)
    elif local_only:
        status = local_status() if route != "api" else {"ok": False, "note": "the story's route is api"}
        if status["ok"]:
            link, units["source"] = LOCAL_LINK, "policy"
            units.update(template=status["template"], profile=status["profile"])
        elif recorded is not None:
            refusal = f"the episode's video link {recorded['link']} is hosted"
        else:
            refusal = status["note"] if route == "api" else _local_note(status["note"])
    else:
        local_ready = False
        if route in ("auto", "local"):
            local_ready = bool(local_status()["ok"])
        row = pick_hosted(rows, policy, want_sound=ambient)
        decided, why = video_plan.video_route(route, local_ready=local_ready, api_ready=row is not None,
                                              allow_paid=budget_obj.allow_paid)
        if decided == "local":
            link = LOCAL_LINK
            units.update(template=local_info["template"], profile=local_info["profile"])
        elif row is not None and route != "local":
            link, refusal = row["link"], (None if decided == "api" else why)
        else:
            refusal = why
            if route in ("auto", "local") and local_info and not local_info.get("ok"):
                refusal = f"{why} ({_local_note(local_info['note'])})"
        units["source"] = "policy" if link else None

    local = bool(link) and link.startswith("local/")
    if local_listed:
        info = local_info or {"ok": False, "note": "not asked: the route or the episode's link is hosted"}
        rows = [{"link": LOCAL_LINK, "status": "ready" if info.get("ok") else "skipped",
                 "reason": info.get("note"), "price_per_second": 0.0}] + rows
    units["links"] = rows

    # --- the mode: the budget profile's; on a local ComfyUI the free profile
    # animates what fits a $0 cap -- every clip there is free (DEC-203)
    mode = settings["animate"]
    cap = float((caps.get("episode") or {}).get("cap_usd", budget_obj.per_episode_cap_usd))
    committed = float(committed_usd or 0.0)
    if mode == "none" and local:
        mode, cap, committed = "key_shots_within_cap", 0.0, 0.0
    priority = tuple(settings.get("animate_priority") or video_plan.ANIMATE_PRIORITY)
    units["mode"] = mode

    if link is None:
        if local_only:
            pinned = [shot["shot_id"] for shot in shots
                      if flags[shot["shot_id"]]["animate"] and not flags[shot["shot_id"]]["keep_still"]]
            message = (f"No clip is planned: the {profile_name} budget profile animates a shot only on your own "
                       f"hardware, at $0 ({refusal}).")
            if not pinned:
                units.update(still=_still_rows(shots, flags, "mode_none"), message=message)
                return units
            return stop(f"{message} Pinned shot{_s(len(pinned))} {_and(pinned)} cannot be animated: bring a local "
                        "ComfyUI, or choose the one_dollar or quality budget profile.", reason=refusal)
        return stop(f"No clip can be made on route {route}: {refusal}.", reason=refusal)

    # --- the plan
    lengths = None
    price = 0.0
    if local:
        try:
            lengths = local_comfyui.video_clip_lengths(units["template"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return stop(f"No clip can be planned on {link}: its workflow cannot be read ({exc}).", reason=str(exc))
    else:
        price = float(row["price_per_second"])
    units.update(link=link, route_class="local" if local else "paid", price_per_second=price)

    tier_for_prompt = tier if tier in (2, 3) else 2
    current_ids = []
    for shot in shots:
        if shot["assets"].get("clip") and image_sha is not None:
            state = clip_state(ec, shot, script, link=link, tier=tier_for_prompt, flags=flags[shot["shot_id"]],
                               image_sha=image_sha(shot))
            if state == "current":
                current_ids.append(shot["shot_id"])
    booked_ids = []
    if booked is not None:
        for shot in shots:
            shot_id = shot["shot_id"]
            if shot_id in current_ids or flags[shot_id]["keep_still"]:
                continue
            try:
                clip_s = video_plan.requested_seconds(link, max(float(shot["duration_s"] or 0.0), _UNTIMED_S),
                                                      lengths=lengths)
            except ValueError:
                continue
            if booked(shot, link=link, clip_s=clip_s, template=units["template"]):
                booked_ids.append(shot_id)
    seconds_of = {line["line_id"]: float((line.get("timing") or {}).get("duration_s") or 0.0)
                  for scene in script["scenes"] for line in scene["lines"]}
    planned = [{"shot_id": shot["shot_id"], "order": shot["order"], "scene_id": shot["scene_id"],
                "duration_s": max(float(shot["duration_s"] or 0.0), _UNTIMED_S),
                "keep_still": flags[shot["shot_id"]]["keep_still"], "animate": flags[shot["shot_id"]]["animate"]}
               for shot in shots]
    try:
        plan = video_plan.plan_animation(
            planned, {scene["scene_id"]: scene["function"] for scene in script["scenes"]},
            {shot["shot_id"]: sum(seconds_of.get(line_id, 0.0) for line_id in shot["lines"]) for shot in shots},
            link=link, price_per_second=price, cap_usd=cap, committed_usd=committed,
            current_shot_ids=current_ids + booked_ids, mode=mode, priority=priority, lengths=lengths)
    except ValueError as exc:
        return stop(f"No clip can be planned on {link}: {exc}.", reason=str(exc))

    new = [entry for entry in plan.selected if entry["shot_id"] not in current_ids + booked_ids]
    count, seconds, est = len(new), int(plan.seconds), round(float(plan.video_usd), 4)
    durations = {row["shot_id"]: row["duration_s"] for row in planned}
    rows_out = []
    for entry in plan.selected:
        row = {"shot_id": entry["shot_id"], "clip_s": entry["clip_s"], "est_usd": round(entry["est_usd"], 4),
               "why": "booked" if entry["shot_id"] in booked_ids and entry["why"] != "pinned" else entry["why"]}
        held = round(durations[entry["shot_id"]] - entry["clip_s"], 3)
        if held > 0:
            # Longer than the longest clip the link sells: the render holds its last frame (DEC-208).
            row["held_s"] = held
        rows_out.append(row)
    units.update(plan=rows_out, still=list(plan.still), count=count, seconds=seconds, est_usd=est,
                 over_cap=video_plan.all_shots_refusal(plan, link=link, mode=mode))
    if seconds:
        key = gen_timings.timing_key(link, units["template"], units["profile"])
        eta = gen_timings.eta_s(key, seconds)
        if eta is not None:
            measured = len(gen_timings.samples(key))
            units.update(eta_s=eta, eta_note=f"the median of the last {measured} clip{_s(measured)} measured on "
                                             f"{link}{' (' + units['template'] + ')' if units['template'] else ''}")
    else:
        units.update(eta_s=0.0, eta_note="no clip to make")

    # Stage E: a story that animates every shot buys no clip that cannot cover its shot.
    too_long = _too_long(rows_out, link) if not local and media_policy.fully_animated(ec.story) else None
    if too_long and count:
        units["too_long"] = too_long
    units["refused"] = (too_long or refusal) if count else None
    units["ready"] = units["refused"] is None
    if ambient:
        sound = makes_sound(link)
        units["ambience"] = {"sound": sound, "note": None if sound else _silent_note(
            ec, link, source=units["source"], chain=chain, merged=merged)}
    units["message"] = _message(units, plan, current_ids, profile_name, booked_ids, resolution=resolution)
    if hold and count:
        units["hold"] = hold
        units["message"] += f" Held: {hold}."
    return units


def _silent_note(ec, link, *, source, chain, merged) -> str:
    """Why an ambience story's clips on *link* carry no sound of their own,
    and what would bring it (stage E: never a silent switch). *source* is
    the units' (``record``: the episode's sticky link), *chain* the parsed
    VIDEO_CHAIN, *merged* the settings over the environment."""
    if source == "record":
        return (f"No ambience: episode {ec.ep}'s clips are on {link}, which makes clips with no sound, and an "
                "episode keeps its clips on one link; its lines are heard without it")
    sounding = [item for item in chain if video_providers.AUDIO.get(describe(item)) == "always"]
    if not sounding:
        return (f"No ambience: {link} makes clips with no sound and no link of {gen.ENV_NAMES[gen.VIDEO]} makes "
                "clips that always have it; add gemini/veo-3.1-lite to it (with GEMINI_PAID_API_KEY) for clips "
                "with their own sound")
    first = sounding[0]
    missing = gen.missing_keys(first, merged)
    if missing:
        return (f"No ambience: {link} makes clips with no sound; add {' and '.join(missing)} for "
                f"{describe(first)}'s sound")
    return f"No ambience: {link} makes clips with no sound, and {describe(first)}, which has it, cannot serve now"


def local_unasked(video) -> bool:
    """Whether the ``video`` part *video* (:func:`video_units`) left the local
    ComfyUI unasked (made without *probe_local*): its readiness is then the
    assets step's to decide, when it asks the server."""
    return any(row.get("link") == LOCAL_LINK and row.get("reason") == LOCAL_NOT_ASKED
               for row in (video or {}).get("links") or [])


def _message(units, plan, current_ids, profile_name, booked_ids=(), *, resolution=None) -> str:
    link, count, seconds, est = units["link"], units["count"], units["seconds"], units["est_usd"]
    kept = [f"{len(current_ids)} current clip{_s(len(current_ids))} kept"] if current_ids else []
    if booked_ids:
        kept.append(f"{len(booked_ids)} bought already, collected at $0")
    still = len(plan.still)
    if still:
        kept.append(f"{still} shot{_s(still)} still")
    tail = f" ({_and(kept)})" if kept else ""
    if not count:
        if plan.selected:
            return f"Every planned shot has its current clip on {link}: $0.00{tail}."
        return (f"No clip is planned: the {profile_name} budget profile animates no shot on {link} "
                f"(mode {units['mode']}); pin one to animate it{tail}.")
    clips = f"{count} clip{_s(count)} ({seconds} s) on {link}"
    if units["route_class"] == "local":
        where = f" ({units['template']}, profile {units['profile']})" if units["template"] else ""
        text = f"{clips}{where}, on your own hardware: $0.00{tail}."
    else:
        size = f" at {resolution}" if resolution and resolution != pricing.DEFAULT_RESOLUTION else ""
        text = f"{clips}{size}, paid: est ${est:.3f}{tail}."
    held = [row for row in units["plan"]
            if row.get("held_s") and not (units.get("too_long") and row["held_s"] > HOLD_TOLERANCE_S)]
    if held:
        text += " " + " ".join(f"{row['shot_id']} runs {row['clip_s'] + row['held_s']:g} s: its {row['clip_s']} s "
                               f"clip is held on its last frame for {row['held_s']:g} s." for row in held)
    ambience = units.get("ambience")
    if ambience is not None:
        # Stage E: the clips' own sound is ambience under the lines, or there is none, said.
        text += (" Each clip brings its own ambience and sound effects, heard under the dialogue (every line in its "
                 "own voice)." if ambience["sound"] else f" {ambience['note']}.")
    elif units["tier"] == 3 and video_providers.AUDIO.get(link) == "never":
        # A-108: before any clip is bought, not only at the render's note.
        text += (f" Tier 3 keeps a clip's own sound, but {link} makes clips with none: every shot is rendered as at "
                 "tier 2, its lines spoken.")
    if units["over_cap"]:
        text += f" Over the cap: {units['over_cap']}."
    if units["refused"]:
        text += f" Not now: {units['refused']}."
    if units["eta_s"]:
        text += f" About {units['eta_s'] / 60:.0f} min ({units['eta_note']})."
    return text
