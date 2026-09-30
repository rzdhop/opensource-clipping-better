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

from clipping.providers import adapters as adapters_mod
from clipping.providers import budget as budget_mod
from clipping.providers import gating, gen_timings, local_comfyui, pricing
from clipping.providers import generation as gen
from clipping.providers import video as video_providers
from clipping.providers.registry import ChainError, describe

from .. import hardware, imaging, schemas, video_plan
from . import sticky_link

CLIPS_KIND = "clips"
CLIPS_DIR = schemas.SHOT_CLIP_DIR
LOCAL_LINK = "local/comfyui"

# What a shot's clip is, derived: no clip; current (its record is current,
# its file on disk, made from the shot's image and video prompt as they are
# now, on the episode's video link); stale (any of those moved); failed (its
# call did not answer, or a re-animate is pending).
CLIP_DERIVED_STATES = ("none", "current", "stale", "failed")

# budget_profiles.json's ``video_link_policy`` values; a profile that names
# none (``free``) takes the cheapest.
CHEAPEST, FIRST = "cheapest_available", "first_in_chain"
LINK_POLICIES = (CHEAPEST, FIRST)

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

def clip_prompt_hash(prompt, negative, *, native_audio=False) -> str:
    """sha256 of what a clip is asked for: the video prompt, its negative,
    and whether the model's own sound is kept (tier 3). The keyframe is the
    record's ``image_sha256``, the link its ``link``."""
    return _canonical_sha256({"prompt": prompt, "negative": negative or "", "native_audio": bool(native_audio)})


def clip_request_parts(ec, shot, script, *, tier, flags, note=None) -> dict:
    """``{prompt, negative, native_audio, hash}`` of *shot*'s clip request
    now (``video_plan.build_video_prompt``): at tier 3 a shot that keeps its
    native audio (``keep_native_audio``) also says its lines; otherwise the
    model's sound is discarded and the prompt carries none (DEC-201)."""
    native = tier == 3 and bool(flags.get("keep_native_audio"))
    lines = ()
    if native:
        texts = {line["line_id"]: line["text"] for scene in script["scenes"] for line in scene["lines"]}
        lines = [texts[line_id] for line_id in shot["lines"] if line_id in texts]
    prompt, negative = video_plan.build_video_prompt(shot, ec.style_lock, tier=tier if tier in (2, 3) else 2,
                                                     lines=lines, note=note)
    return {"prompt": prompt, "negative": negative, "native_audio": native,
            "hash": clip_prompt_hash(prompt, negative, native_audio=native)}


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
    expected = clip_request_parts(ec, shot, script, tier=tier, flags=flags, note=clip.get("note"))["hash"]
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


def hosted_rows(chain, merged, adapters) -> list:
    """One row per hosted link of VIDEO_CHAIN, calling nothing:
    ``{"link", "status": "keyed" | "skipped", "reason", "price_per_second"}``
    -- keyed: it has an adapter, a table of sellable lengths, its key and a
    price per second (every hosted clip is paid)."""
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
                price = pricing.price_for(link)
            except pricing.PriceUnknown as exc:
                row["reason"] = str(exc)
            else:
                if price.unit != "second":
                    row["reason"] = f"priced per {price.unit}, not per second"
                else:
                    row.update(status="keyed", reason="keyed", price_per_second=float(price.usd))
        rows.append(row)
    return rows


def pick_hosted(rows, policy):
    """The keyed hosted row *policy* picks: :data:`FIRST` the first in chain
    order; :data:`CHEAPEST` (the default) the lowest price per second, chain
    order breaking a tie. None when no row is keyed."""
    keyed = [(index, row) for index, row in enumerate(rows) if row["status"] == "keyed"]
    if not keyed:
        return None
    if policy == FIRST:
        return keyed[0][1]
    return min(keyed, key=lambda pair: (pair[1]["price_per_second"], pair[0]))[1]


# ----------------------------------------------------------- the estimate

def _local_note(note) -> str:
    """*note* about the local link, naming it once."""
    return note if note.startswith(LOCAL_LINK) else f"{LOCAL_LINK}: {note}"


def _still_rows(shots, flags, reason) -> list:
    """Every shot still for want of a plan: kept still, else *reason*."""
    return [{"shot_id": shot["shot_id"], "reason": "keep_still" if flags[shot["shot_id"]]["keep_still"] else reason}
            for shot in shots]


def video_units(ec, script, storyboard, assets_doc, *, env, caps, committed_usd, adapters=None, probe_local=False,
                transport=None, image_sha=None, booked=None) -> dict:
    """The ``video`` part of the assets estimate at tier >= 2, calling
    nothing but -- with *probe_local* -- a local ComfyUI's status::

        {"tier", "budget_profile", "route", "mode", "route_class": "local" | "paid" | None,
         "link", "source": "record" | "policy" | None, "template", "profile", "price_per_second",
         "plan": [{"shot_id", "clip_s", "est_usd", "why"}], "still": [{"shot_id", "reason"}],
         "count", "seconds", "est_usd", "eta_s": s | None, "eta_note", "links": [...],
         "refused": sentence | None, "ready": bool, "message"}

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
    bought and cannot be."""
    tier = tier_of(ec)
    profile_name = ec.story["generation_profile"]["budget_profile"]
    route = ec.story["generation_profile"]["route"]
    shots = sorted(storyboard["shots"], key=lambda shot: shot["order"])
    flags = {shot["shot_id"]: shot_flags(shot, assets_doc) for shot in shots}
    units = {"tier": tier, "budget_profile": profile_name, "route": route, "mode": None, "route_class": None,
             "link": None, "source": None, "template": None, "profile": None, "price_per_second": None,
             "plan": [], "still": [], "count": 0, "seconds": 0, "est_usd": 0.0, "eta_s": None,
             "eta_note": ETA_NONE, "links": [], "refused": None, "ready": True, "message": ""}

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
    rows = hosted_rows(chain, merged, adapters)
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
        row = pick_hosted(rows, policy)
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
    units.update(plan=[{"shot_id": entry["shot_id"], "clip_s": entry["clip_s"], "est_usd": round(entry["est_usd"], 4),
                        "why": "booked" if entry["shot_id"] in booked_ids and entry["why"] != "pinned"
                        else entry["why"]} for entry in plan.selected],
                 still=list(plan.still), count=count, seconds=seconds, est_usd=est)
    if seconds:
        key = gen_timings.timing_key(link, units["template"], units["profile"])
        eta = gen_timings.eta_s(key, seconds)
        if eta is not None:
            measured = len(gen_timings.samples(key))
            units.update(eta_s=eta, eta_note=f"the median of the last {measured} clip{_s(measured)} measured on "
                                             f"{link}{' (' + units['template'] + ')' if units['template'] else ''}")
    else:
        units.update(eta_s=0.0, eta_note="no clip to make")

    units["refused"] = refusal if count else None
    units["ready"] = units["refused"] is None
    units["message"] = _message(units, plan, current_ids, profile_name, booked_ids)
    return units


def local_unasked(video) -> bool:
    """Whether the ``video`` part *video* (:func:`video_units`) left the local
    ComfyUI unasked (made without *probe_local*): its readiness is then the
    assets step's to decide, when it asks the server."""
    return any(row.get("link") == LOCAL_LINK and row.get("reason") == LOCAL_NOT_ASKED
               for row in (video or {}).get("links") or [])


def _message(units, plan, current_ids, profile_name, booked_ids=()) -> str:
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
        text = f"{clips}, paid: est ${est:.3f}{tail}."
    if units["refused"]:
        text += f" Not now: {units['refused']}."
    if units["eta_s"]:
        text += f" About {units['eta_s'] / 60:.0f} min ({units['eta_note']})."
    return text
