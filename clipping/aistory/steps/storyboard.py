"""Step ``storyboard``: one episode's shots (spec 3 step 9, 2.8, 4.2 row T1,
5; AI Story phase 3).

Needs what the script step needs (``episode_common.
check_episode_preconditions``) and a complete script: every scene written
and every framing part there; otherwise ``StepFailed`` naming what is
missing.

Two ways to plan the shots, both ending in ``shots.build_storyboard`` (the
cross-scene rule pass, name-free resolved prompts, reference images, motion,
durations and transitions) and a re-timed script:

- :func:`build_fast` -- every scene by ``shots.fast_plan``: deterministic, no
  call at all (DEC-109), so the web layer runs it inline;
- :func:`run` (the job) -- **T1** for each scene with no plan, a stale plan
  (its scene was rewritten since) or a fast one; every other scene keeps its
  plan as it is (``shots.plans_from_storyboard``). The storyboard is written
  after every accepted call; a scene whose T1 fails keeps what it had (a
  stale scene stays marked stale) and the step ends failed naming it.
  Budget and cancel as in the script step. A complete T1 storyboard re-run
  makes no call.

A storyboard written here is never approved (``build_storyboard`` clears
``approved_at``); the script's revision and approval never move (its
``timing`` is derived). ``story.json`` is never written (RC-E2).
"""

from __future__ import annotations

import time

from .. import prompts, shots
from . import entities, episode_common, llm_call
from . import script as script_step
from .episode_common import SCRIPT_DOC, STORYBOARD_DOC
from .llm_call import StepFailed

FAST, T1 = "fast", "t1"


# ----------------------------------------------------------------- helpers

def require_complete_script(ec) -> dict:
    """The episode's script, complete; ``StepFailed`` naming what is missing."""
    script = episode_common.read_episode(ec, SCRIPT_DOC)
    if script is None or not script["scenes"]:
        raise StepFailed(f"Episode {ec.ep} has no script yet: write it first (the script step).")
    stubs = [scene["scene_id"] for scene in script["scenes"]
             if scene["state"] == "stub" and scene["function"] not in script_step.FRAMING_FUNCTIONS]
    parts = script_step.missing_parts(script, ec.ep)
    if stubs or parts:
        missing = []
        if stubs:
            missing.append(f"scene{'s' if len(stubs) > 1 else ''} {', '.join(stubs)}")
        if parts:
            missing.append(f"the {script_step._and(parts)}")
        raise StepFailed(f"Episode {ec.ep}'s script is not complete ({script_step._and(missing)} not written "
                         "yet): run the script step again to finish it before planning shots.")
    return script


def stale_scenes(storyboard, script) -> set:
    """The storyboard's scenes planned from another revision of their scene
    (or marked stale), and those the script no longer has."""
    if storyboard is None:
        return set()
    revs = {scene["scene_id"]: scene["rev"] for scene in script["scenes"]}
    return {sid for sid, entry in storyboard["scenes"].items()
            if entry.get("stale") or revs.get(sid) != entry["script_rev"]}


def current_plans(storyboard, script) -> tuple:
    """``(plans, sources, stale)`` of an existing storyboard (all empty
    without one)."""
    if storyboard is None:
        return {}, {}, set()
    plans = shots.plans_from_storyboard(storyboard, script)
    sources = {sid: entry["source"] for sid, entry in storyboard["scenes"].items() if sid in plans}
    return plans, sources, stale_scenes(storyboard, script) & set(plans)


def scenes_to_plan(script, plans, sources, stale) -> list:
    """The scenes of *script* T1 plans (``current_plans``' three): each with no
    plan, a stale plan or a fast one, in the script's order. The step plans
    them; the estimate counts them."""
    return [scene for scene in script["scenes"]
            if scene["scene_id"] not in plans or scene["scene_id"] in stale or sources.get(scene["scene_id"]) == FAST]


def build(ec, script, plans, sources, previous, *, stale, now) -> tuple:
    """``shots.build_storyboard`` over every scene that has a plan; a scene
    in *stale* (planned from an older revision, not planned again) keeps its
    stale mark and the revision it was planned from. Should those old plans
    no longer fit their scene, they are left out (they are planned again by
    the next run). ``(storyboard, notes)``."""
    def attempt(chosen):
        return shots.build_storyboard(
            script, {sid: plans[sid] for sid in chosen}, {sid: sources[sid] for sid in chosen},
            entities=ec.entities, style_lock=ec.style_lock, template=ec.template, language=ec.language,
            consistency_mode=ec.consistency_mode, now=now, previous=previous)

    try:
        board, notes = attempt(list(plans))
    except ValueError:
        if not stale:
            raise
        board, notes = attempt([sid for sid in plans if sid not in stale])
        notes.append(f"stale scene(s) {', '.join(sorted(stale))} left out: their old shots no longer fit")
    for sid in stale:
        entry = board["scenes"].get(sid)
        if entry is not None and previous is not None and sid in previous["scenes"]:
            entry["stale"] = True
            entry["script_rev"] = previous["scenes"][sid]["script_rev"]
    return board, notes


def save(ec, script, board, *, now) -> dict:
    """Write the storyboard, then the script re-timed with it."""
    episode_common.write_storyboard(ec, board, script, now=now)
    episode_common.retime(script, ec, board)
    episode_common.write_script(ec, script, now=now)
    return board


def _entity(ec, kind, eid, sid):
    return script_step._entity(ec, kind, eid, sid)


def shot_inputs(ec, scene) -> dict:
    """What T1/T1r are given about *scene*, and what their replies are
    checked against."""
    sid = scene["scene_id"]
    characters = [_entity(ec, "characters", cid, sid) for cid in scene["characters"]]
    place = _entity(ec, "places", scene["place_id"], sid)
    props = [_entity(ec, "props", pid, sid) for pid in scene["props"]]
    tags = ([f"@{doc['char_id']}" for doc in characters] + [f"#{place['place_id']}:{scene['time_variant']}"]
            + [f"%{doc['prop_id']}" for doc in props])
    return {
        "lines": [{"speaker": line["speaker"], "text": line["text"], "emotion": line["emotion"]}
                  for line in scene["lines"]],
        "characters": [{"char_id": doc["char_id"], "name": doc["name"], "descriptor": doc["descriptor"] or ""}
                       for doc in characters],
        "place": {"place_id": place["place_id"], "layout_notes": place["layout_notes"] or ""},
        "props": [{"prop_id": doc["prop_id"], "name": doc["name"], "descriptor": doc["descriptor"] or ""}
                  for doc in props],
        "shots_per_scene": tuple(ec.episode_defaults["shots_per_scene"]),
        "camera": ec.style_lock["camera"],
        "modifiers_allowed": list(ec.style_lock["motion_rules"]["tier1"].get("modifiers") or []),
        "hook_style": ec.episode_defaults["hook_style"],
        "tags_allowed": tags,
        # Every name of the cast, not only the scene's: no one is named in an action.
        "names": ec.names,
    }


def _builder_kwargs(inputs) -> dict:
    return {key: inputs[key] for key in ("lines", "characters", "place", "props", "shots_per_scene", "camera",
                                          "modifiers_allowed", "hook_style")}


def _previous_shots(script, plans, sid) -> list:
    """The last two shots planned before scene *sid*, in episode order."""
    before = []
    for scene in script["scenes"]:
        if scene["scene_id"] == sid:
            break
        before.extend(plans.get(scene["scene_id"]) or [])
    return [{"framing": plan["framing"], "camera_motion": plan["camera_motion"]} for plan in before[-2:]]


def plan_scene(ctx, ec, script, plans, scene, *, tools, announced) -> list:
    """T1 for *scene*: its plans (not stored anywhere by this function)."""
    inputs = shot_inputs(ec, scene)
    pack = script_step._pack(ec, ctx, announced)
    system, user, schema = prompts.build_t1(pack, scene=scene, previous_shots=_previous_shots(
        script, plans, scene["scene_id"]), **_builder_kwargs(inputs))

    def validate(reply):
        return prompts.validate_t1(reply, scene=scene, shots_per_scene=inputs["shots_per_scene"],
                                   modifiers_allowed=inputs["modifiers_allowed"],
                                   tags_allowed=inputs["tags_allowed"], n_lines=len(scene["lines"]),
                                   names=inputs["names"])

    reply = llm_call.call_json(ctx, "T1", system, user, schema, validator=validate, runner=tools.runner,
                               time_fn=tools.time_fn)
    return [dict(shot) for shot in reply["shots"]]


def _summary_line(board, how) -> str:
    return (f"🎞 Storyboard: {len(board['shots'])} shots over {len(board['scenes'])} scenes ({how}), "
            f"revision {board['rev']}")


# -------------------------------------------------------------------- fast

def build_fast(stores, story_id, ep, *, now, on_log) -> dict:
    """Every scene of the episode planned by ``shots.fast_plan`` (no call),
    resolved, written; the script re-timed with it. Returns the storyboard
    written. ``StepFailed`` as the step's preconditions, or for a script
    that is not complete."""
    ec = episode_common.load_context(stores, story_id, ep)
    episode_common.check_episode_preconditions(None, ec)
    script = require_complete_script(ec)
    previous = episode_common.read_episode(ec, STORYBOARD_DOC)
    plans, seen = {}, set()
    for scene in script["scenes"]:
        plans[scene["scene_id"]] = shots.fast_plan(
            scene, lines=scene["lines"], entities=ec.entities, episode_defaults=ec.episode_defaults,
            style_lock=ec.style_lock, first_at_place=scene["place_id"] not in seen)
        seen.add(scene["place_id"])
    board, notes = build(ec, script, plans, {sid: FAST for sid in plans}, previous, stale=set(), now=now)
    save(ec, script, board, now=now)
    for note in notes:
        on_log(f"📐 {note}")
    on_log(_summary_line(board, "fast, no call"))
    on_log(episode_common.timing_line(script))
    return board


# --------------------------------------------------------------------- T1

def run(ctx, *, runner=None, time_fn=time.monotonic, budget=None) -> dict:
    """The T1 step (module docstring). *budget*: an ``episode_common.Budget``
    shared with a caller running this step inside its own (the fast track);
    None gives the step its own."""
    ec = episode_common.load_episode_context(ctx)
    episode_common.check_episode_preconditions(ctx, ec)
    script = require_complete_script(ec)
    ctx.cancel.check()
    tools = entities.Tools(runner=runner, time_fn=time_fn)
    budget = budget if budget is not None else episode_common.Budget(time_fn)
    announced = set()

    board = episode_common.read_episode(ec, STORYBOARD_DOC)
    plans, sources, stale = current_plans(board, script)
    todo = scenes_to_plan(script, plans, sources, stale)
    total = len(script["scenes"])
    planned, failed, notes = [], [], []

    def left():
        rest = [scene["scene_id"] for scene in todo if scene["scene_id"] not in planned
                and scene["scene_id"] not in {sid for sid, _ in failed}]
        return f"the shots of scene{'s' if len(rest) > 1 else ''} {', '.join(rest)} (T1)"

    for scene in todo:
        sid = scene["scene_id"]
        ctx.cancel.check()
        budget.before_call(left)
        ctx.on_log(f"🎞 Scene {script['scenes'].index(scene) + 1} of {total} ({sid}, {scene['function']}): "
                   "shots (T1)")
        try:
            scene_plans = plan_scene(ctx, ec, script, plans, scene, tools=tools, announced=announced)
        except StepFailed as exc:
            failed.append((sid, exc.reason))
            ctx.on_log(f"✖ Scene {sid}'s shots failed: {exc.reason}")
            continue
        plans[sid], sources[sid] = scene_plans, T1
        stale.discard(sid)
        planned.append(sid)
        now = llm_call.utc_now()
        board, notes = build(ec, script, plans, sources, board, stale=stale, now=now)
        save(ec, script, board, now=now)

    if board is not None and planned:
        for note in notes:
            ctx.on_log(f"📐 {note}")
        ctx.on_log(_summary_line(board, "T1"))
    elif not todo:
        ctx.on_log(f"✅ Episode {ec.ep}'s shots are all planned: nothing to plan.")
    ctx.on_log(episode_common.timing_line(script))
    if failed:
        parts = "; ".join(f"scene {sid} failed ({reason})" for sid, reason in failed)
        raise StepFailed(f"Episode {ec.ep}'s storyboard is not finished: {parts}. Every other scene keeps its "
                         "shots; run the storyboard step again to plan "
                         f"{'them' if len(failed) > 1 else 'it'}.")
    return {"ep": ec.ep, "planned": planned, "shots": len(board["shots"]) if board else 0}
