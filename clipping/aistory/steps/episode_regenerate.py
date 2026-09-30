"""The episode targets of ``regenerate`` (spec 9.2; AI Story phase 3).

``regenerate.run`` hands these here:

- ``scene:<ep>:<scene_id>`` -- a body scene: **E2** again with the note; the
  recap, hook or cliffhanger scene: the matching partial **E3** with the note;
- ``hook:<ep>``, ``cliffhanger:<ep>``, ``teaser:<ep>`` -- that partial E3;
- ``shot:<ep>:<shot_id>:plan`` -- **T1r**: that shot planned again with the
  rest of its scene fixed, then the storyboard built again with the scene's
  plans updated;
- phase 4 (``assets.regenerate_shot_image`` / ``regenerate_line_voice``):
  kind ``shot_image`` (``shot:<ep>:<shot_id>``) -- that shot's image again,
  the note at the prompt's tail, a fresh seed persisted as ``pending``
  before the call; a locked shot is refused -- and kind ``line``
  (``line:<ep>:<line_id>``) -- that line spoken again by its pinned voice
  alone, a new take so the generation cache misses on purpose, the note its
  spoken direction and the take persisted as ``pending`` in the line's
  ``assets.json`` entry before the call (phase 5 stage 7). Neither
  clears an approval. (``regenerate.parse_target`` reads these two targets.)
- phase 4 (``metadata.regenerate_platform``): kind ``metadata``
  (``metadata:<ep>:<platform>``, the tuple ``("metadata", ep, platform)``)
  -- that platform's M1 again with the note; the other platforms, the cover
  and every approval stay (its job ends completed, DEC-161).

Each applies only its own keys and reuses its scene's line block
(``schemas.line_id_for``). A script rewrite goes through
``episode_common.mark_changed`` -- the revisions move, both documents lose
their approval, the consistency report and the storyboard scenes planned
from the old text become stale -- and the storyboard is written **before**
the script, so a failure between the two writes can only leave an approval
cleared too early, never one standing over text it did not approve. The
script is re-timed. A shot re-plan rebuilds the storyboard (never approved
after it) and re-times the script without moving its revision.

An unknown episode, scene or shot is refused naming it, before any call.
``story.json`` is never written (RC-E2).
"""

from __future__ import annotations

import time

from .. import prompts
from .. import store as store_mod
from . import entities, episode_common, llm_call
from . import script as script_step
from . import storyboard as storyboard_step
from .episode_common import SCRIPT_DOC, STORYBOARD_DOC
from .llm_call import StepFailed

# The grammar is ``regenerate``'s, shared with the web layer and the CLI
# (``regenerate.parse_target`` reads these targets with every other one).
# Phase 4's episode kinds (the plan's tuple kinds: ``shot:<ep>:<shot_id>`` is
# ``("shot_image", ep, shot_id)``, ``line:<ep>:<line_id>`` is ``("line", ep,
# line_id)``: ``assets`` runs them; ``metadata:<ep>:<platform>`` is
# ``("metadata", ep, platform)``: ``metadata`` runs it).
from .regenerate import (  # noqa: F401 -- re-exported
    FRAMING_TARGETS,
    LINE_KIND,
    METADATA_KIND,
    SHOT_IMAGE_KIND,
    parse_episode_target,
)

ASSET_KINDS = (SHOT_IMAGE_KIND, LINE_KIND)


def _noted(note) -> str:
    return f" (note: {note})" if note else ""


def run(ctx, target, parsed, note, *, runner=None, time_fn=time.monotonic, sleep_fn=time.sleep, adapters=None,
        transport=None) -> dict:
    """Regenerate the episode target *target* (``parse_episode_target``'s
    *parsed*), with the optional *note*. *sleep_fn*, *adapters* and
    *transport* reach the generation chains of the phase-4 kinds (tests)."""
    ep = parsed[1]

    def refuse(reason):
        return StepFailed(f"Cannot regenerate {target!r}: {reason}", reason=reason)

    stores = store_mod.StoryStore(ctx.outputs_dir, on_log=ctx.on_log)
    try:
        ec = episode_common.load_context(stores, ctx.story_id, ep)
        episode_common.check_episode_preconditions(ctx, ec, require_memory=False)
        script = episode_common.read_episode(ec, SCRIPT_DOC)
    except StepFailed as exc:
        raise refuse(str(exc)) from None
    if script is None or not script["scenes"]:
        raise refuse(f"episode {ep} has no script yet; write it first (the script step).")
    board = episode_common.read_episode(ec, STORYBOARD_DOC)
    tools = entities.Tools(runner=runner, time_fn=time_fn, sleep_fn=sleep_fn, adapters=adapters,
                           transport=transport)
    ctx.cancel.check()

    if parsed[0] in ASSET_KINDS:
        # Imported here: the image and voice chains, which the text targets
        # never need.
        from . import assets as assets_step

        regenerate = (assets_step.regenerate_shot_image if parsed[0] == SHOT_IMAGE_KIND
                      else assets_step.regenerate_line_voice)
        return regenerate(ctx, ec, target, parsed[2], note, tools=tools, refuse=refuse)

    if parsed[0] == METADATA_KIND:
        # Imported here, like the asset kinds: the renderer's modules.
        from . import metadata as metadata_step

        return metadata_step.regenerate_platform(ctx, ec, target, parsed[2], note, tools=tools, refuse=refuse)

    if parsed[0] == "shot":
        return _replan_shot(ctx, ec, target, parsed[2], note, script, board, tools, refuse)

    if parsed[0] == "scene":
        sid = parsed[2]
        scene = script_step.scene_of(script, sid)
        if scene is None:
            ids = ", ".join(s["scene_id"] for s in script["scenes"])
            raise refuse(f"episode {ep} has no scene {sid!r} (its scenes: {ids}).")
        part = scene["function"] if scene["function"] in script_step.FRAMING_FUNCTIONS else None
    else:
        part = parsed[0]
        scene = None if part == "teaser" else script_step.framing_scene(script, part)
        if part != "teaser" and scene is None:
            raise refuse(f"episode {ep} has no {part} scene.")
        sid = None if scene is None else scene["scene_id"]

    announced = set()
    try:
        if part is None:
            ctx.on_log(f"📝 Scene {sid} ({scene['function']}) again (E2){_noted(note)}")
            script_step.write_body_scene(ctx, ec, script, sid, tools=tools, announced=announced, note=note)
            touched = [sid]
        else:
            ctx.on_log(f"🪝 The {part} again (E3){_noted(note)}")
            touched = script_step.write_framing(ctx, ec, script, part, tools=tools, announced=announced, note=note)
    except StepFailed as exc:
        raise refuse(str(exc)) from None

    now = llm_call.utc_now()
    episode_common.mark_changed(script, board, scene_ids=touched, now=now)
    episode_common.retime(script, ec, board)
    if board is not None:
        episode_common.write_storyboard(ec, board, script, now=now)
    episode_common.write_script(ec, script, now=now)
    ctx.on_log(f"🔁 Regenerated {target}{_noted(note)}")
    ctx.on_log(episode_common.timing_line(script))
    if board is not None and touched:
        ctx.on_log(f"🎞 The shots of {', '.join(touched)} are stale: plan them again (the storyboard step).")
    return {"target": target, "scenes": touched, "rev": script["rev"]}


def _replan_shot(ctx, ec, target, shot_id, note, script, board, tools, refuse) -> dict:
    if board is None:
        raise refuse(f"episode {ec.ep} has no storyboard yet; plan its shots first (the storyboard step).")
    shot = next((s for s in board["shots"] if s["shot_id"] == shot_id), None)
    if shot is None:
        raise refuse(f"episode {ec.ep}'s storyboard has no shot {shot_id!r} (it has sh01 to "
                     f"sh{len(board['shots']):02d}).")
    sid = shot["scene_id"]
    plans, sources, stale = storyboard_step.current_plans(board, script)
    if sid in stale:
        raise refuse(f"scene {sid} was rewritten since its shots were planned: plan it again first (the "
                     "storyboard step).")
    scene = script_step.scene_of(script, sid)
    scene_shots = [s["shot_id"] for s in board["shots"] if s["scene_id"] == sid]
    index = scene_shots.index(shot_id)
    scene_plans = plans[sid]

    try:
        inputs = storyboard_step.shot_inputs(ec, scene)
    except StepFailed as exc:
        raise refuse(str(exc)) from None
    announced = set()
    pack = script_step._pack(ec, ctx, announced, note=note)
    system, user, schema = prompts.build_t1r(
        pack, scene=scene, shots=scene_plans, index=index, note=pack.note,
        **storyboard_step._builder_kwargs(inputs))

    def validate(reply):
        return prompts.validate_t1r(reply, scene=scene, shots=scene_plans, index=index,
                                    modifiers_allowed=inputs["modifiers_allowed"],
                                    tags_allowed=inputs["tags_allowed"], n_lines=len(scene["lines"]),
                                    names=inputs["names"])

    ctx.on_log(f"🎞 Shot {shot_id} of scene {sid} again (T1r){_noted(note)}")
    try:
        reply = llm_call.call_json(ctx, "T1r", system, user, schema, validator=validate, runner=tools.runner,
                                   time_fn=tools.time_fn)
    except StepFailed as exc:
        raise refuse(str(exc)) from None
    plans[sid] = scene_plans[:index] + [dict(reply["shot"])] + scene_plans[index + 1:]

    now = llm_call.utc_now()
    new_board, notes = storyboard_step.build(ec, script, plans, sources, board, stale=stale, now=now)
    storyboard_step.save(ec, script, new_board, now=now)
    for line in notes:
        ctx.on_log(f"📐 {line}")
    ctx.on_log(f"🔁 Regenerated {target}{_noted(note)}")
    return {"target": target, "shot": shot_id, "rev": new_board["rev"]}
