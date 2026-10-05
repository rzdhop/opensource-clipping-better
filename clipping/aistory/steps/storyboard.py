"""Step ``storyboard``: one episode's shots (spec 3 step 9, 2.8, 4.2 row T1,
5; AI Story phase 3).

Needs what the script step needs (``episode_common.
check_episode_preconditions``, the memory gate included) and a complete script: every scene written
and every framing part there; otherwise ``StepFailed`` naming what is
missing. A v2 story also needs, before any call, an approved image for
every prop a scene references (:func:`require_prop_images`, phase 7 stage
3c, A11): a script step's ``new_objects`` can leave a referenced prop with
no image yet, and planning its shots before the image exists would reference
an entity that never renders. Legacy stories are never refused for this.

Two ways to plan the shots, both ending in ``shots.build_storyboard`` (the
cross-scene rule pass, name-free resolved prompts, reference images, motion,
durations and transitions) and a re-timed script:

- :func:`build_fast` -- every scene by ``shots.fast_plan``: deterministic, no
  call at all (DEC-109), so the web layer runs it inline;
- :func:`run` (the job) -- **T1** (**T1 v2** on a v2 story, phase 7 stage 4:
  one beat shot a scene, two past the template's ``max_shot_s`` -- and,
  DEC-252, two for a body scene with two lines or two characters and the
  length for two, :func:`beat_shot_count`) for each
  scene with no plan, a stale plan
  (its scene was rewritten since) or a fast one; every other scene keeps its
  plan as it is (``shots.plans_from_storyboard``) and its shots with it --
  ids, keyframes, clips, verdicts (walk follow-up F5, :func:`build`); a
  scene planned again gets new shots with never-used ids. The storyboard is written
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

from clipping.providers import pricing

from .. import context, media_policy, native_speech, prompts, schemas, shots, timing, voices
from .. import store as store_mod
from . import clips, entities, episode_common, llm_call, voice_lines
from . import script as script_step
from .episode_common import SCRIPT_DOC, STORYBOARD_DOC
from .llm_call import StepFailed

FAST, T1 = "fast", "t1"

# Phase 7 stage 3c (A11): the estimate the prop-image refusal below quotes --
# the shipped quality profile's own `prop` role's first (text-to-image) link
# (templates/budget_profiles.json, media_policy.ROLES; moved to fal by stage
# 2c, DEC-235). Read straight from the price table rather than resolving the
# story's actual chain (media_policy.role_chain), which needs the merged
# env/keys and can raise ChainError: this message is informational, shown
# before any call and any key is even looked at, not a charge.
_PROP_IMAGE_LINK = "fal/seedream-4.5"
_PROP_IMAGE_ESTIMATE_USD = pricing.PRICES[_PROP_IMAGE_LINK].usd


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


def _missing_prop_images(ec, script) -> list:
    """Every prop *script* references that has no approved image yet, once
    each, in first-reference order: a stub ``steps/script.py`` created from
    an episode's ``new_objects`` (phase 7 stage 3c, A11) starts this way, and
    stays this way until the places step draws it."""
    missing, seen = [], set()
    for scene in script["scenes"]:
        for pid in scene["props"]:
            if pid in seen:
                continue
            seen.add(pid)
            doc = ec.entities["props"].get(pid)
            if doc is not None and not entities.has_file(ec.store, ec.story_id, entities.PROPS, pid, doc["image"]):
                missing.append(doc)
    return missing


def require_prop_images(ec, script) -> None:
    """A v2 story only (A11): refuse to plan shots -- before any LLM call --
    while *script* references a prop with no approved image yet. Names every
    such prop and what drawing them would cost (:data:`_PROP_IMAGE_ESTIMATE_USD`
    each, on :data:`_PROP_IMAGE_LINK`) and the step that finishes them. A
    legacy story is never refused for this (RC-M1): v1 stories do not create
    prop stubs from a script (DEC-171 keeps their props always an existing,
    already-drawn id), and nothing else leaves a prop referenced before its
    own image exists."""
    if not media_policy.is_v2(ec.story):
        return
    missing = _missing_prop_images(ec, script)
    if not missing:
        return
    names = entities.quoted_list(sorted({doc["name"] for doc in missing}))
    est = len(missing) * _PROP_IMAGE_ESTIMATE_USD
    plural = len(missing) > 1
    raise StepFailed(
        f"Episode {ec.ep}'s script uses the prop{'s' if plural else ''} {names}, which "
        f"{'have' if plural else 'has'} no approved image yet (about ${est:.3f} on {_PROP_IMAGE_LINK}, "
        f"{len(missing)} image{'s' if plural else ''}): run the places step to draw "
        f"{'them' if plural else 'it'} before planning the shots."
    )


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


def scenes_to_plan(script, plans, sources, stale, short=()) -> list:
    """The scenes of *script* T1 plans (``current_plans``' three): each with no
    plan, a stale plan or a fast one -- or, *short* (:func:`short_of_beats`,
    stage E), too few beat shots for its link's longest clip -- in the
    script's order. The step plans them; the estimate counts them."""
    return [scene for scene in script["scenes"]
            if scene["scene_id"] not in plans or scene["scene_id"] in stale or sources.get(scene["scene_id"]) == FAST
            or scene["scene_id"] in short]


def kept_shots(previous, plans, *, replanned=(), replanned_shots=()) -> dict:
    """``shots.build_storyboard``'s *keep*: for each scene of *plans* that
    *previous* has shots for and that was not planned again (not in
    *replanned*), the ids of those shots, one per plan -- None for a shot
    planned again on its own (*replanned_shots*: T1r). A scene whose plan
    count is not its old shot count is new throughout. ``{}`` with no
    *previous*."""
    if previous is None:
        return {}
    by_scene: dict = {}
    for shot in previous["shots"]:
        by_scene.setdefault(shot["scene_id"], []).append(shot["shot_id"])
    keep = {}
    for sid, scene_plans in plans.items():
        ids = by_scene.get(sid)
        if sid in replanned or ids is None or len(ids) != len(scene_plans):
            continue
        keep[sid] = [None if shot_id in replanned_shots else shot_id for shot_id in ids]
    return keep


def reserved_shot_ids(assets_doc) -> list:
    """The shot ids *assets_doc* (``assets.json``, or None) still keys a
    per-shot record by (``schemas.EPISODE_ASSETS_SHOT_MAPS``): ids a new
    shot is never given, whether or not the storyboard still has them."""
    found = set()
    for name in schemas.EPISODE_ASSETS_SHOT_MAPS:
        found.update((assets_doc or {}).get(name) or {})
    return sorted(found)


def build(ec, script, plans, sources, previous, *, stale, now, env=None, replanned=(), replanned_shots=()) -> tuple:
    """``shots.build_storyboard`` over every scene that has a plan; a scene
    in *stale* (planned from an older revision, not planned again) keeps its
    stale mark and the revision it was planned from. Should those old plans
    no longer fit their scene, they are left out (they are planned again by
    the next run). ``(storyboard, notes)``.

    Shot ids are stable keys (walk follow-up F5): every scene of *previous*
    not in *replanned* (the scenes this build planned again) keeps its shots
    -- ids, keyframes, clips, verdicts, overrides (:func:`kept_shots`; a
    shot in *replanned_shots* alone is new); a scene planned again gets new
    shots whose ids were never used (after the highest of *previous* and of
    the episode's ``assets.json`` records, :func:`reserved_shot_ids`). The
    ``shots`` list follows the script's scene order. A v2 story's shots are resolved
    with the episode's ledger (``script.ledger_of``: wardrobe sets and
    holders, phase 7 stage 5c) and built to the word budgets of the links
    its images and clips go to (``clips.episode_budgets``; *env* the
    Settings values; stage F2): a shot whose prompt cannot fit even at its
    shortest fails the step, named with its link, and nothing is written."""
    ledger = script_step.ledger_of(ec)
    assets_doc = _assets_doc(ec)
    budgets = clips.episode_budgets(ec, env, assets_doc=assets_doc)
    native = media_policy.native_speech(ec.story)
    if native:
        # Plan 22: one shot a character line, planned at the length its clip sells.
        plan_notes = []
        plans = speech_plans(ec, script, plans, notes=plan_notes)
    else:
        plan_notes = []
    keep = kept_shots(previous, plans, replanned=replanned, replanned_shots=replanned_shots)
    reserved = reserved_shot_ids(assets_doc)

    def attempt(chosen):
        return shots.build_storyboard(
            script, {sid: plans[sid] for sid in chosen}, {sid: sources[sid] for sid in chosen},
            entities=ec.entities, style_lock=ec.style_lock, template=ec.template, language=ec.language,
            consistency_mode=ec.consistency_mode, now=now, previous=previous, v2=media_policy.is_v2(ec.story),
            shots_per_scene=(episode_common.NATIVE_SHOTS_PER_SCENE if native
                             else ec.episode_defaults["shots_per_scene"]),
            ledger=ledger, budgets=budgets,
            keep={sid: keep[sid] for sid in chosen if sid in keep}, reserved=reserved,
            timing_mode=native_speech.TIMING_MODE if native else None)

    try:
        board, notes = attempt(list(plans))
    except shots.PromptOverBudget as exc:
        raise StepFailed(f"Episode {ec.ep}'s storyboard was not written: {exc}") from None
    except ValueError:
        if not stale:
            raise
        board, notes = attempt([sid for sid in plans if sid not in stale])
        notes.append(f"stale scene(s) {', '.join(sorted(stale))} left out: their old shots no longer fit")
    notes = plan_notes + notes
    for sid in stale:
        entry = board["scenes"].get(sid)
        if entry is not None and previous is not None and sid in previous["scenes"]:
            entry["stale"] = True
            entry["script_rev"] = previous["scenes"][sid]["script_rev"]
    return board, notes


def speech_plans(ec, script, plans, notes=None) -> dict:
    """*plans* as a native-speech story plans them (plan 22,
    ``shots.speech_shot_plan``): each character line one speaking shot at
    the length its speech link sells, the reactions and narrator shots
    silent on the silent link's lengths, up to the template's
    ``reaction_shots`` (default 0 to 1) a scene. A scene's stored line plan
    sets its clips (plan 24 stage 4); what it could not set is named in
    *notes*. ``StepFailed`` naming a line no clip can speak, with the fix."""
    speech_lengths, silent_lengths = clips.speech_lengths(ec.story)
    reactions = tuple(ec.template.get("reaction_shots") or (0, 1))
    by_id = {scene["scene_id"]: scene for scene in script["scenes"]}
    out = {}
    for sid, scene_plans in plans.items():
        scene = by_id.get(sid)
        if scene is None:
            out[sid] = scene_plans
            continue
        try:
            out[sid] = shots.speech_shot_plan(scene, scene_plans, language=ec.language, speech_lengths=speech_lengths,
                                              silent_lengths=silent_lengths, reaction_shots=reactions,
                                              notes=notes)
        except shots.SpeechLineTooLong as exc:
            raise StepFailed(f"Episode {ec.ep}'s storyboard cannot be planned as speaking clips: {exc}.") from None
    return out


def require_speakable(ec, script) -> None:
    """A native-speech story only (plan 22): refuse to plan shots -- before
    any LLM call -- while a character line is longer than its longest clip
    can speak (``shots.speech_line_refusal``), naming each with the fix. The
    script step's own cap keeps a new line under it."""
    if not media_policy.native_speech(ec.story):
        return
    refusal = shots.speech_line_refusal(script, clips.speech_lengths(ec.story)[0])
    if refusal:
        raise StepFailed(f"Episode {ec.ep}'s storyboard cannot be planned as speaking clips: {refusal}.")


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


def scene_seconds(ec, script, scene) -> float:
    """*scene*'s length as the script is timed now (its stored episode-level
    ``timing``), else its own scene timing on the template."""
    stored = ((script.get("timing") or {}).get("scenes") or {}).get(scene["scene_id"]) or {}
    if stored.get("duration_s") is not None:
        return float(stored["duration_s"])
    return float(timing.scene_timing(scene, ec.template, ec.language, style_lock=ec.style_lock)["duration_s"])


def expected_scene_seconds(ec, script, scene) -> float:
    """*scene*'s length as its voices will measure it (DEC-250):
    :func:`scene_seconds` plus, for each line not measured yet
    (``shots._measured_for_its_words``), what its speaker's TTS provider adds
    over the estimate (``voices.speech_overrun``: Gemini's voices ran 1.35x
    the estimate on the live story -- ``timing.estimate_line`` was measured
    on Edge). A scene whose every line is measured is its measured length.
    What :func:`beat_shot_count` reads: one clip must cover the shot once the
    voices are real, not only on the estimate."""
    seconds = scene_seconds(ec, script, scene)
    entries = shots.planned_line_entries(scene)
    if any(entries):
        return _planned_scene_seconds(ec, seconds, scene, entries)
    for line in scene["lines"]:
        if shots._measured_for_its_words(line):
            continue
        # Plan 24 stage 1 (D-1): an estimate made at its voice's overrun
        # already carries it -- never added twice.
        if timing.estimate_carries_overrun(line):
            continue
        over = voices.speech_overrun(voice_lines.speaker_voice(ec, line["speaker"]))
        if over > 1.0:
            seconds += timing.estimate_line(line["text"], ec.language) * (over - 1.0)
    return round(seconds, 3)


def _planned_scene_seconds(ec, seconds, scene, entries) -> float:
    """:func:`expected_scene_seconds` of a scene with a stored line plan
    (plan 24 stage 4, D-2): every line not measured yet speaks for the
    seconds its plan entry gives it -- the plan already carries its voice's
    overrun (``timing.scene_plan``), so nothing is added -- in place of the
    estimate; a measured line is what was measured, a line with no entry
    keeps today's estimate and overrun. The pauses and the tail around the
    lines are those of *seconds* (:func:`scene_seconds`)."""
    spoken = sum(timing.line_duration(line, ec.language)[0] for line in scene["lines"])
    total = max(0.0, seconds - spoken)
    for line, entry in zip(scene["lines"], entries):
        own = timing.line_duration(line, ec.language)[0]
        if shots._measured_for_its_words(line):
            total += own
        elif entry is not None:
            total += float(entry["seconds"])
        elif timing.estimate_carries_overrun(line):
            total += own
        else:
            over = voices.speech_overrun(voice_lines.speaker_voice(ec, line["speaker"]))
            total += own + (timing.estimate_line(line["text"], ec.language) * (over - 1.0) if over > 1.0 else 0.0)
    return round(total, 3)


def max_shot_s(ec, env=None):
    """The longest a v2 beat shot may run (phase 7 follow-up, stage E): the
    template's ``max_shot_s`` -- and, on a story whose every shot is one clip
    (``media_policy.fully_animated``), no longer than the longest clip its
    planned link sells (``clips.planned_link``: the episode's link, else the
    budget profile's, keys asked then aside; *env* the Settings values):
    Veo 3.1 lite sells 8 s at most, so a scene past 8 s is two beat shots.
    The template's 6-10 shots and 55-75 s still hold: every slot tops out
    at 11 s, two shots of at most 8 s. None: a template without one.
    DEC-258: a lipsyncing story's clips are at most 10 s
    (``clips.longest_clip_s(story=)``), so on seedance a scene past 10 s is
    two shots."""
    template_max = ec.template.get("max_shot_s")
    if template_max is None or not media_policy.fully_animated(ec.story):
        return template_max
    longest = clips.longest_clip_s(clips.planned_link(ec, env, assets_doc=_assets_doc(ec)), story=ec.story)
    return min(template_max, longest) if longest else template_max


def _assets_doc(ec):
    """The episode's ``assets.json`` (its recorded links), or None -- also
    for one that does not validate: a plan is never refused over it."""
    try:
        return episode_common.read_episode(ec, store_mod.EPISODE_ASSETS_DOC)
    except StepFailed:
        return None


def _two_beats(ec, scene, seconds) -> bool:
    """DEC-252's rhythm (the human, 2026-10-03: "boring, no rhythm"): a body
    scene (``schemas.BODY_FUNCTIONS``) with two lines or two characters is
    two beat shots when its *seconds* hold two shots of the template's
    ``min_shot_s`` each. The recap, the hook and the cliffhanger keep one."""
    return (scene["function"] in schemas.BODY_FUNCTIONS
            and (len(scene.get("lines") or ()) >= 2 or len(scene.get("characters") or ()) >= 2)
            and seconds >= 2 * ec.template["min_shot_s"])


def beat_shot_count(ec, script, scene, *, limit_s=None, rhythm=True):
    """T1 v2's ``(lo, hi)`` for *scene* (phase 7 stage 4, A12): two beat
    shots when the scene runs past the template's ``max_shot_s`` (one clip
    sells at most that much, DEC-208) -- or past *limit_s* when given
    (:func:`max_shot_s`, stage E) -- as its voices will measure it
    (:func:`expected_scene_seconds`, DEC-250); and, with *rhythm* (DEC-252,
    the default), a body scene with the lines and the length for two
    (:func:`_two_beats`); one otherwise. ``rhythm=False`` is the clip-length
    rule alone: what :func:`short_of_beats` reads, so a storyboard planned
    before DEC-252 is never planned again for it. A template with no
    ``max_shot_s`` keeps the episode's own pair."""
    max_shot = ec.template.get("max_shot_s")
    if max_shot is None:
        return tuple(ec.episode_defaults["shots_per_scene"])
    if limit_s is not None:
        max_shot = limit_s
    lo, hi = ec.episode_defaults["shots_per_scene"]
    # DEC-250: as the voices will measure it, not as the estimate says.
    seconds = expected_scene_seconds(ec, script, scene)
    n = 2 if seconds > max_shot or (rhythm and _two_beats(ec, scene, seconds)) else 1
    n = min(max(n, lo), hi)
    return n, n


def short_of_beats(ec, script, plans, limit_s) -> set:
    """The scenes of a fully animated v2 story whose plan has fewer beat
    shots than one clip can cover under *limit_s* now (stage E: a
    storyboard planned for a longer clip than its link's, e.g. on seedance
    before the Veo key was set; :func:`beat_shot_count` without its rhythm,
    DEC-252 -- a scene planned as one beat before the two-beat default
    still fits its clip, so it is not planned again for that): the step
    plans them again, the estimate counts them. Empty for any other story,
    or without *limit_s*."""
    if limit_s is None or not media_policy.fully_animated(ec.story):
        return set()
    return {scene["scene_id"] for scene in script["scenes"]
            if plans.get(scene["scene_id"])
            and len(plans[scene["scene_id"]]) < beat_shot_count(ec, script, scene, limit_s=limit_s,
                                                                 rhythm=False)[0]}


def shot_inputs_v2(ec, script, scene, *, limit_s=None) -> dict:
    """:func:`shot_inputs` for T1 v2 / T1r v2 (phase 7 stage 4): the lines
    with their ids and delivery, the place's descriptor, each prop's look
    (``shots.render_prop``), and the scene's own beat-shot count (under
    *limit_s*, :func:`beat_shot_count`)."""
    inputs = shot_inputs(ec, scene)
    sid = scene["scene_id"]
    place = _entity(ec, "places", scene["place_id"], sid)
    props = [_entity(ec, "props", pid, sid) for pid in scene["props"]]
    inputs["lines"] = [{"line_id": line["line_id"], "speaker": line["speaker"], "text": line["text"],
                        "emotion": line["emotion"], "delivery": line.get("delivery") or ""}
                       for line in scene["lines"]]
    inputs["place"] = dict(inputs["place"], descriptor=place.get("descriptor") or "")
    inputs["props"] = [dict(entry, look=shots.render_prop(doc)) if doc.get("descriptor") else entry
                       for entry, doc in zip(inputs["props"], props)]
    inputs["shots_per_scene"] = beat_shot_count(ec, script, scene, limit_s=limit_s)
    return inputs


def t1_v2_plan(shot) -> dict:
    """A T1 v2 (or T1r v2) reply's shot as a plan: its ``motion`` text kept
    as ``clip_motion`` (the storyboard shot's ``motion`` is the Tier-1
    camera motion)."""
    plan = {key: value for key, value in shot.items() if key != "motion"}
    plan["clip_motion"] = shot["motion"]
    return plan


def _builder_kwargs(inputs) -> dict:
    return {key: inputs[key] for key in ("lines", "characters", "place", "props", "shots_per_scene", "camera",
                                          "modifiers_allowed", "hook_style")}


def _previous_shots(script, plans, sid, *, v2=False) -> list:
    """The last two shots planned before scene *sid*, in episode order; *v2*
    (T1 v2): the last one with its action and staging too."""
    before = []
    for scene in script["scenes"]:
        if scene["scene_id"] == sid:
            break
        before.extend(plans.get(scene["scene_id"]) or [])
    out = [{"framing": plan["framing"], "camera_motion": plan["camera_motion"]} for plan in before[-2:]]
    if v2 and out:
        last = before[-1]
        out[-1].update(action=last["action"], staging=list(last.get("staging") or []))
    return out


def plan_scene(ctx, ec, script, plans, scene, *, tools, announced, limit_s=None) -> list:
    """T1 for *scene*: its plans (not stored anywhere by this function). A v2
    story's scene is planned by T1 v2 (:func:`plan_scene_v2`), its beat
    shots under *limit_s* (:func:`max_shot_s`)."""
    if media_policy.is_v2(ec.story):
        return plan_scene_v2(ctx, ec, script, plans, scene, tools=tools, announced=announced, limit_s=limit_s)
    inputs = shot_inputs(ec, scene)
    pack = script_step._pack(ec, ctx, announced)
    system, user, schema = prompts.build_t1(pack, scene=scene, previous_shots=_previous_shots(
        script, plans, scene["scene_id"]), **_builder_kwargs(inputs))

    def validate(reply):
        return prompts.validate_t1(reply, scene=scene, shots_per_scene=inputs["shots_per_scene"],
                                   modifiers_allowed=inputs["modifiers_allowed"],
                                   tags_allowed=inputs["tags_allowed"], n_lines=len(scene["lines"]),
                                   names=inputs["names"], v2=media_policy.is_v2(ec.story))

    reply = llm_call.call_json(ctx, "T1", system, user, schema, validator=validate, runner=tools.runner,
                               time_fn=tools.time_fn)
    return [dict(shot) for shot in reply["shots"]]


# DEC-252: the order a repeated camera motion moves along (:func:`_repair_t1_v2_reply`):
# from a motion to the next one here that neither neighbour has -- a push-in
# becomes a pan, a pan a pull-out, and so on, rather than the same move again.
_CAMERA_ROTATION = ("push_in", "pan_lr", "pull_out", "pan_rl", "hold", "pan_du", "pan_ud")


INSERT_PROP_FALLBACK_FRAMING = "close_up"


def _repair_insert_prop(reply, tags_allowed) -> list:
    """DEC-262's half of :func:`_repair_t1_v2_reply`: a shot framed
    ``insert_prop`` with no prop tag among its subjects gets the scene's
    first prop tag when the scene has one (the insert is on it), else its
    framing becomes :data:`INSERT_PROP_FALLBACK_FRAMING` -- the validator
    would refuse it on every link (the live hook scene listed no prop)."""
    fixed = []
    props = [tag for tag in tags_allowed if isinstance(tag, str) and tag.startswith("%")]
    for i, shot in enumerate(reply["shots"]):
        if not isinstance(shot, dict) or shot.get("framing") != "insert_prop":
            continue
        subjects = shot.get("subjects")
        if not isinstance(subjects, list) or any(isinstance(t, str) and t.startswith("%") for t in subjects):
            continue
        if props:
            subjects.append(props[0])
            fixed.append(f"shot {i + 1}: framing 'insert_prop' listed no prop, now on {props[0]}")
        else:
            shot["framing"] = INSERT_PROP_FALLBACK_FRAMING
            fixed.append(f"shot {i + 1}: framing 'insert_prop' in a scene with no prop, now "
                         f"'{INSERT_PROP_FALLBACK_FRAMING}'")
    return fixed


def _repair_camera(reply, previous_camera) -> list:
    """DEC-252's half of :func:`_repair_t1_v2_reply`: a shot whose
    ``camera_motion`` repeats the shot's before it (the scene's first: the
    episode's shot before the scene, *previous_camera*) moves to the next
    motion of :data:`_CAMERA_ROTATION` that neither neighbour has. A motion
    not in that list is left for the validator."""
    fixed = []
    shots_ = [shot for shot in reply["shots"] if isinstance(shot, dict)]
    before = previous_camera
    for i, shot in enumerate(shots_):
        camera = shot.get("camera_motion")
        if camera in _CAMERA_ROTATION and camera == before:
            after = shots_[i + 1].get("camera_motion") if i + 1 < len(shots_) else None
            start = _CAMERA_ROTATION.index(camera)
            for step in range(1, len(_CAMERA_ROTATION)):
                candidate = _CAMERA_ROTATION[(start + step) % len(_CAMERA_ROTATION)]
                if candidate not in (before, after):
                    shot["camera_motion"] = candidate
                    fixed.append(f"shot {i + 1}: camera_motion {camera!r} repeated the previous shot's, now "
                                 f"{candidate!r}")
                    break
        before = shot.get("camera_motion")
    return fixed


def _repair_t1_v2_reply(reply, *, tags_allowed, previous_camera=None) -> list:
    """Repair a T1 v2 reply in place before its validator runs (fix B,
    found when gemini kept failing T1 v2 twice on one scene): a
    @char/%prop/#place tag used in a shot's ``action``, ``motion`` or
    ``staging`` (an entry's ``subject``, ``facing`` or ``expression``) that
    is allowed in this scene (*tags_allowed*, the scene's own characters,
    props and place) but missing from that shot's own ``subjects`` is
    appended there -- the model names a tag it forgot to list, and
    :func:`clipping.aistory.prompts.validate_t1_v2` then refuses it as
    unlisted ("tag '@char_x' is used but not listed in subjects"). A tag
    not allowed in the scene at all is left untouched, for the validator to
    refuse as it always has. DEC-252: a camera motion repeating the shot's
    before it (*previous_camera* for the scene's first) is moved on
    (:func:`_repair_camera`) rather than refused -- a retry costs a call,
    the next motion costs nothing. Returns one description per repair, for
    the caller to log."""
    added = []
    if not isinstance(reply, dict) or not isinstance(reply.get("shots"), list):
        return added
    added.extend(_repair_camera(reply, previous_camera))
    added.extend(_repair_insert_prop(reply, tags_allowed))
    allowed = set(tags_allowed)
    for i, shot in enumerate(reply["shots"]):
        if not isinstance(shot, dict) or not isinstance(shot.get("subjects"), list):
            continue
        subjects = shot["subjects"]
        present = set(subjects)
        found = []
        for field in ("action", "motion"):
            text = shot.get(field)
            if isinstance(text, str):
                found.extend(prompts._TAG_PATTERN.findall(text))
        for entry in shot.get("staging") or ():
            if not isinstance(entry, dict):
                continue
            for key in ("subject", "facing", "expression"):
                value = entry.get(key)
                if isinstance(value, str):
                    found.extend(prompts._TAG_PATTERN.findall(value))
        for tag in found:
            if tag in allowed and tag not in present:
                subjects.append(tag)
                present.add(tag)
                added.append(f"shot {i + 1}: {tag!r} added to subjects")
    return added


def plan_scene_v2(ctx, ec, script, plans, scene, *, tools, announced, limit_s=None) -> list:
    """T1 v2 for *scene* (phase 7 stage 4, A12): its beat shots as plans,
    each with T1 v2's ``clip_motion`` and ``staging``. Phase 7 stage 5c
    (A13): the call also reads the ledger's wardrobe and holder facts for
    the scene (``context.slice_for_shot``; the previous shots' action and
    staging are T1 v2's own block already, so not repeated). Stage E: two
    beat shots for a scene past *limit_s* (:func:`max_shot_s`); DEC-252:
    two for a body scene with the lines and the length for two, and no
    shot repeating the camera motion of the shot before it."""
    inputs = shot_inputs_v2(ec, script, scene, limit_s=limit_s)
    pack = script_step._pack(ec, ctx, announced)
    continuity = context.slice_for_shot(ec, scene, None, None, ledger=script_step.ledger_of(ec))
    previous = _previous_shots(script, plans, scene["scene_id"], v2=True)
    # DEC-252: the scene's first shot never repeats the camera motion of the shot just before the scene.
    previous_camera = previous[-1]["camera_motion"] if previous else None
    system, user, schema = prompts.build_t1_v2(pack, scene=scene, previous_shots=previous, continuity=continuity,
                                               **_builder_kwargs(inputs))

    def validate(reply):
        added = _repair_t1_v2_reply(reply, tags_allowed=inputs["tags_allowed"], previous_camera=previous_camera)
        if added:
            ctx.on_log("🩹 T1 v2 reply repaired: " + "; ".join(added))
        return prompts.validate_t1_v2(reply, scene=scene, shots_per_scene=inputs["shots_per_scene"],
                                      modifiers_allowed=inputs["modifiers_allowed"],
                                      tags_allowed=inputs["tags_allowed"], n_lines=len(scene["lines"]),
                                      names=inputs["names"], previous_camera=previous_camera)

    reply = llm_call.call_json(ctx, "T1v2", system, user, schema, validator=validate, runner=tools.runner,
                               time_fn=tools.time_fn)
    return [t1_v2_plan(shot) for shot in reply["shots"]]


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
    require_prop_images(ec, script)
    require_speakable(ec, script)
    previous = episode_common.read_episode(ec, STORYBOARD_DOC)
    plans, seen = {}, set()
    for scene in script["scenes"]:
        plans[scene["scene_id"]] = shots.fast_plan(
            scene, lines=scene["lines"], entities=ec.entities, episode_defaults=ec.episode_defaults,
            style_lock=ec.style_lock, first_at_place=scene["place_id"] not in seen)
        seen.add(scene["place_id"])
    board, notes = build(ec, script, plans, {sid: FAST for sid in plans}, previous, stale=set(), now=now,
                         replanned=set(plans))
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
    require_prop_images(ec, script)
    require_speakable(ec, script)
    ctx.cancel.check()
    tools = entities.Tools(runner=runner, time_fn=time_fn)
    budget = budget if budget is not None else episode_common.Budget(time_fn)
    announced = set()

    board = episode_common.read_episode(ec, STORYBOARD_DOC)
    plans, sources, stale = current_plans(board, script)
    # Stage E: a fully animated story's beat shots fit its link's longest clip.
    limit = max_shot_s(ec, ctx.settings_env) if media_policy.is_v2(ec.story) else None
    todo = scenes_to_plan(script, plans, sources, stale, short_of_beats(ec, script, plans, limit))
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
            scene_plans = plan_scene(ctx, ec, script, plans, scene, tools=tools, announced=announced, limit_s=limit)
        except StepFailed as exc:
            failed.append((sid, exc.reason))
            ctx.on_log(f"✖ Scene {sid}'s shots failed: {exc.reason}")
            continue
        plans[sid], sources[sid] = scene_plans, T1
        stale.discard(sid)
        planned.append(sid)
        now = llm_call.utc_now()
        board, notes = build(ec, script, plans, sources, board, stale=stale, now=now, env=ctx.settings_env,
                             replanned={sid})
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
