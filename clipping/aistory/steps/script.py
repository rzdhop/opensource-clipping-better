"""Step ``script``: one episode's script, beat sheet to consistency check
(spec 3 step 8, 2.7, 4.2 rows E1-E4; AI Story phase 3).

``ctx.ep`` is the episode. Needs a ``ready`` story, an episode the season
plans, and -- from episode 2 on -- the previous episode's recap in the
season's memory (``episode_common.check_episode_preconditions``).

One job fills whatever the episode's ``script.json`` is still missing, in
this order, writing the script after **every** accepted call (atomic,
validated against its schema, its own cross-checks and the story), so what
was paid for survives a failure, a cancel or the step's time budget:

1. no scene yet -> **E1**, the beat sheet: every scene a stub (function,
   place and time variant, characters, props, a summary, an emotion, a
   duration hint clamped into its slot), ids ``s00`` (the recap, from the
   template's ``recap_from_episode``) then ``s01``... in order;
2. every body scene still a stub, in order -> **E2**, its lines (ids from
   the scene's own block, ``schemas.line_id_for``; an estimated timing
   each), sfx cues anchored to ``start`` or a line id, optional on-screen
   text. A body scene nobody can speak in (no character, no narrator) is
   written silent, without a call. A scene whose E2 fails is printed and
   left a stub; the others go on;
3. the framing parts E2 never writes -- the hook's lines and on-screen text,
   the cliffhanger's reveal and line, the recap (episode >= 2), the
   next-episode teaser -> **E3**, in full when every one is missing, else
   one partial E3 per missing part;
4. a complete script whose consistency report is missing, stale or of an
   older revision -> **E4** (analytic), the report
   ``{passed, issues, checked_rev, checked_at, stale: false}``.

After each write the script is re-timed (``episode_common.retime``, with the
storyboard when there is one). A complete script re-run makes no call. The
step ends failed, after everything else it could do, naming each part that
failed and the regenerate target that finishes it (DEC-027). Before each
call: the cancel token, and the step budget (``episode_common.Budget``:
a call starts only if it can finish inside 30 minutes; else the step ends
failed naming what is left, and running it again continues).

**Measure with real voices** (opt-in: ``params.measure_voices``; spec 6.4
source (a)). After the writing, whatever was written: every line whose
timing is not a current measurement (:func:`is_measured`: estimated, of
other words, of another voice than its speaker's pinned one, or its audio
gone) is spoken through that voice alone (``voices.synthesize_line``: a
one-link chain, DEC-122 -- a failing voice fails its lines and nothing else
is tried), booked once in the story's ledger (unit ``char``), kept as phase
4's line audio (``assets/voice/line_NN.mp3|.wav`` + its ``.json`` sidecar),
and the line's ``timing`` becomes the measured one. The script is re-timed
and written after every line, and a storyboard's shot durations follow
(``shots.retime_storyboard``). Measuring changes no content: no revision
moves, no approval is cleared, the consistency report stays as it is. Cancel
and the step budget (``STORY_TTS_CALL_SECONDS`` per synthesis) are checked
before each line; the step ends failed naming every line that could not be
measured and whose voice to change. :func:`measure_estimate` says what it
would do, calling nothing. The measurement itself lives in ``voice_lines``
(lifted unchanged at phase 4 stage 8: the assets step speaks a line the same
way); ``_Run`` mixes it in.

The story's own document is never read for writing: episodes never change
the story's approvals or status (RC-E2).
"""

from __future__ import annotations

import copy
import time

from .. import context, prompts, schemas, timing
from . import entities, episode_common, llm_call
from .episode_common import SCRIPT_DOC, STORYBOARD_DOC
from .llm_call import StepFailed
# The voice measurement lives in ``voice_lines`` (lifted unchanged, phase 4
# stage 8, so the assets step speaks a line the same way); its names stay
# reachable here for every caller of phase 3.
from .voice_lines import (  # noqa: F401 -- re-exported
    STORY_TTS_CALL_SECONDS, VOICE_ASSETS, BudgetSpent, LineMeasurement, asset_name, is_measured, lines_to_measure,
    measure_estimate, no_voice_reason, speaker_name, speaker_voice,
)

FRAMING_FUNCTIONS = ("recap", "hook", "cliffhanger")

MEASURE_PARAM = "measure_voices"


# ----------------------------------------------------------------- helpers

def scene_of(script, sid):
    return next((scene for scene in script["scenes"] if scene["scene_id"] == sid), None)


def framing_scene(script, function):
    return next((scene for scene in script["scenes"] if scene["function"] == function), None)


def body_scenes(script) -> list:
    return [scene for scene in script["scenes"] if scene["function"] in schemas.BODY_FUNCTIONS]


def e3_parts(ep) -> list:
    """Every part a full E3 writes for episode *ep*, in its own order."""
    return list(prompts.e3_schema(None, ep, [])["properties"])


def missing_parts(script, ep) -> list:
    """The framing parts not written yet, in E3's order."""
    missing = []
    for part in e3_parts(ep):
        if part == "teaser":
            if script["next_episode_teaser"] is None:
                missing.append(part)
            continue
        scene = framing_scene(script, part)
        if scene is None:
            continue
        if scene["state"] == "stub" or (part == "cliffhanger" and script["cliffhanger"]["reveal"] is None):
            missing.append(part)
    return missing


def is_complete(script, ep) -> bool:
    """Every scene written and every framing part there."""
    return bool(script["scenes"]) and all(scene["state"] == "written" for scene in script["scenes"]) \
        and not missing_parts(script, ep)


def needs_check(script) -> bool:
    report = script.get("consistency_report")
    return report is None or report["stale"] or report["checked_rev"] != script["rev"]


def target_for(ep, sid) -> str:
    return f"scene:{ep}:{sid}"


def part_target(ep, script, part) -> str:
    """The regenerate target that writes *part* again."""
    if part == "recap":
        return target_for(ep, framing_scene(script, "recap")["scene_id"])
    return f"{part}:{ep}"


def _and(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _entity(ec, kind, eid, sid):
    doc = ec.entities[kind].get(eid)
    if doc is None:
        word = entities.TARGET_KINDS[kind]
        raise StepFailed(f"scene {sid} names the {word} {eid!r}, which is no longer in the story",
                         reason=f"scene {sid} names the {word} {eid!r}, which is no longer in the story")
    return doc


def _cast_lines(ec, char_ids, sid) -> list:
    docs = [_entity(ec, "characters", cid, sid) for cid in char_ids]
    return [{"char_id": doc["char_id"], "name": doc["name"], "personality": doc["personality"]} for doc in docs]


def _word_budget(ec, scene) -> int:
    return timing.word_budget(scene["function"], scene["target_duration_s"], ec.language, ec.template,
                              style_lock=ec.style_lock)


def _speakers(ec, scene) -> list:
    return list(scene["characters"]) + (["narrator"] if ec.narrator else [])


def _fr_text(ec, text: str) -> str:
    """*text*, stripped, with a dropped French elision apostrophe repaired
    when the story's language is French (spec 4.2, F1) -- applied again (a
    no-op: :func:`prompts.repair_fr_elisions` never touches text that
    already carries an apostrophe) wherever a reply reaches ``script.json``,
    on top of the early repair the ``_repair_e*_reply`` functions below do
    before a reply's validator ever runs. ``delivery`` never goes through
    this (it is always English, spec 4.2)."""
    text = text.strip()
    if ec.language == "fr":
        text = prompts.repair_fr_elisions(text)
    return text


def _repair_e1_reply(ec, reply) -> None:
    """Repair a dropped French elision apostrophe in an E1 reply's own free
    text, in place, before its validator runs (spec 4.2, F1): a merged
    elision changes a word count (``"l alliance"`` is 2 "words", "l'alliance"
    is 1), so the repair has to happen before ``validate_e1`` counts them,
    not only when the reply is later applied to the script."""
    if ec.language != "fr":
        return
    reply["title"] = prompts.repair_fr_elisions(reply["title"])
    for scene in reply["scenes"]:
        scene["summary"] = prompts.repair_fr_elisions(scene["summary"])


def _repair_e2_reply(ec, reply) -> None:
    """Same as :func:`_repair_e1_reply`, for an E2 reply: every line's text
    (what the word-budget floor below counts) and the on-screen text."""
    if ec.language != "fr":
        return
    for line in reply["lines"]:
        line["text"] = prompts.repair_fr_elisions(line["text"])
    if reply["on_screen_text"]:
        reply["on_screen_text"] = prompts.repair_fr_elisions(reply["on_screen_text"])


def _repair_e3_reply(ec, reply) -> None:
    """Same as :func:`_repair_e1_reply`, for an E3 reply: whichever of
    hook/cliffhanger/recap/teaser it carries (:func:`e3_parts`)."""
    if ec.language != "fr":
        return
    for key in ("hook", "cliffhanger", "recap"):
        part = reply.get(key)
        if part is None:
            continue
        for line in part.get("lines", []):
            line["text"] = prompts.repair_fr_elisions(line["text"])
        if key == "cliffhanger":
            if part.get("reveal"):
                part["reveal"] = prompts.repair_fr_elisions(part["reveal"])
        elif part.get("on_screen_text"):
            part["on_screen_text"] = prompts.repair_fr_elisions(part["on_screen_text"])
    if reply.get("teaser"):
        reply["teaser"] = prompts.repair_fr_elisions(reply["teaser"])


def _line(ec, sid, k, line) -> dict:
    text = _fr_text(ec, line["text"])
    return {
        "line_id": schemas.line_id_for(sid, k), "speaker": line["speaker"], "text": text,
        "emotion": line["emotion"], "delivery": line["delivery"].strip(),
        "timing": timing.estimated_timing(text, ec.language),
    }


def _keep_cues(scene) -> None:
    """A rewritten scene keeps only the sfx cues still anchored to a line it has."""
    ids = {line["line_id"] for line in scene["lines"]}
    scene["sfx_cues"] = [cue for cue in scene["sfx_cues"] if cue["at"] == "start" or cue["at"] in ids]


def _pack(ec, ctx, announced, note=None):
    pack = context.build_pack(language=ec.language, story=ec.story, note=note)
    llm_call.announce_trimmed(ctx, pack, announced)
    return pack


# -------------------------------------------------------------------- E1

def skeleton(ec, *, now) -> dict:
    """A script with nothing written yet (never written itself: E1 comes first)."""
    return {
        "$schema": schemas.EPISODE_SCRIPT_SCHEMA_NAME, "ep": ec.ep, "title": None, "language": ec.language,
        "template_id": ec.template["template_id"], "hook": {"on_screen_text": None}, "scenes": [],
        "cliffhanger": {"scene_id": None, "reveal": None,
                        "cut_to_black": ec.episode_defaults["cliffhanger_style"] == "cut_to_black"},
        "next_episode_teaser": None, "timing": None, "consistency_report": None,
        "approved_anyway": None, "approved_at": None, "rev": 1, "created_at": now, "updated_at": now,
    }


def _normalize_episode_targets(ec, scenes) -> tuple:
    """Raise every scene's own clamped ``target_duration_s`` toward its
    slot's high end, proportionally to the room each one has, until their
    sum reaches the episode template's ``target_s`` or every scene is
    already at its high end (spec 6.2 follow-up, F3).

    E1's own per-scene clamp (the caller, just above) never raises a target,
    only keeps it inside its slot: a model that picks a hint near every
    slot's low end leaves the whole episode short of the template's own
    target, and so short of the window's low end, well before any single
    scene's own range is broken -- this is the deterministic fix-up, no
    call, cannot fail. Never lowers a target, never exceeds a scene's own
    slot high end (the style lock's own clamp included,
    :func:`timing.slot_range`, reused as-is). Returns ``(before, after)`` --
    equal when nothing changed.
    """
    target_s = ec.template["target_s"]
    before = sum(scene["target_duration_s"] for scene in scenes)
    if before >= target_s:
        return before, before
    highs = [timing.slot_range(scene, ec.template, ec.style_lock)[1] for scene in scenes]
    rooms = [high - scene["target_duration_s"] for high, scene in zip(highs, scenes)]
    total_room = sum(rooms)
    if total_room <= 0:
        return before, before
    scale = min(1.0, (target_s - before) / total_room)
    for scene, room in zip(scenes, rooms):
        if room > 0:
            scene["target_duration_s"] = round(scene["target_duration_s"] + room * scale, 3)
    after = sum(scene["target_duration_s"] for scene in scenes)
    return before, after


def apply_e1(ec, script, reply) -> tuple:
    """E1's beat sheet into *script* (in place): one stub per scene. Returns
    :func:`_normalize_episode_targets`'s own ``(before, after)``."""
    scenes, number = [], 1
    for stub in reply["scenes"]:
        if stub["function"] == "recap":
            sid = "s00"
        else:
            sid = f"s{number:02d}"
            number += 1
        scene = {
            "scene_id": sid, "function": stub["function"], "place_id": stub["place_id"],
            "time_variant": stub["time_variant"], "characters": list(dict.fromkeys(stub["characters"])),
            "props": list(dict.fromkeys(stub["props"])), "summary": _fr_text(ec, stub["summary"]),
            "emotion": stub["emotion"], "target_duration_s": 0.0, "lines": [], "sfx_cues": [],
            "on_screen_text": None, "state": "stub", "source": "E1", "rev": 1,
        }
        lo, hi = timing.slot_range(scene, ec.template, ec.style_lock)
        scene["target_duration_s"] = round(min(max(float(stub["target_duration_s"]), lo), hi), 3)
        scenes.append(scene)
    before, after = _normalize_episode_targets(ec, scenes)
    script["title"] = _fr_text(ec, reply["title"])
    script["scenes"] = scenes
    return before, after


def write_beat_sheet(ctx, ec, script, *, tools, announced) -> None:
    """E1 into *script* (in place; the caller writes it)."""
    pack = _pack(ec, ctx, announced)
    slots = timing.episode_slots(ec.template, ec.ep)
    system, user, schema = prompts.build_e1(
        pack, ep=ec.ep, arc_entry=ec.arc_entry, template=ec.template, episode_defaults=ec.episode_defaults,
        cast=[{"char_id": doc["char_id"], "name": doc["name"]} for doc in ec.cast],
        places=[{"place_id": pid, "name": ec.entities["places"][pid]["name"], "time_variants": variants}
                for pid, variants in ec.places.items()],
        props=[{"prop_id": pid, "name": ec.entities["props"][pid]["name"]} for pid in ec.prop_ids],
        memory=ec.season, slots=slots,
    )
    llm_call.announce_trimmed(ctx, pack, announced)

    def validate(reply):
        _repair_e1_reply(ec, reply)
        errors = prompts.validate_e1(reply, ep=ec.ep, template=ec.template, episode_defaults=ec.episode_defaults,
                                     cast_ids=list(ec.entities["characters"]), places=ec.places,
                                     prop_ids=ec.prop_ids)
        if errors:
            return errors
        trial = copy.deepcopy(script)
        apply_e1(ec, trial, reply)
        return episode_common.trial_errors(ec, trial)

    reply = llm_call.call_json(ctx, "E1", system, user, schema, validator=validate, runner=tools.runner,
                               time_fn=tools.time_fn)
    before, after = apply_e1(ec, script, reply)
    if after > before + 1e-9:
        ctx.on_log(f"⏱ scene targets raised from {before:.1f} s to {after:.1f} s")


# -------------------------------------------------------------------- E2

def apply_e2(ec, scene, reply) -> None:
    sid = scene["scene_id"]
    scene["lines"] = [_line(ec, sid, k, line) for k, line in enumerate(reply["lines"])]
    scene["sfx_cues"] = [
        {"at": "start" if cue["at"] == "start" else schemas.line_id_for(sid, int(cue["at"]) - 1), "cue": cue["cue"]}
        for cue in reply["sfx_cues"]
    ]
    scene["on_screen_text"] = _fr_text(ec, reply["on_screen_text"]) if reply["on_screen_text"] else None
    scene["state"] = "written"
    scene["source"] = "E2"


def _previous_line(ec, script, scene):
    """The nearest body scene before *scene* that has lines: its summary and
    its last line, for E2's continuity; None for the first."""
    index = script["scenes"].index(scene)
    for before in reversed(script["scenes"][:index]):
        if before["function"] in schemas.BODY_FUNCTIONS and before["lines"]:
            last = before["lines"][-1]
            return {"summary": before["summary"], "speaker_name": speaker_name(ec, last["speaker"]),
                    "text": last["text"]}
    return None


def can_speak(ec, scene) -> bool:
    return bool(_speakers(ec, scene))


def write_body_scene(ctx, ec, script, sid, *, tools, announced, note=None) -> bool:
    """E2 for body scene *sid* into *script* (in place; the caller writes
    it). A scene nobody can speak in is written silent without a call;
    returns whether a call was made."""
    scene = scene_of(script, sid)
    if not can_speak(ec, scene):
        scene.update(lines=[], sfx_cues=[], on_screen_text=None, state="written", source="E2")
        return False
    cast = _cast_lines(ec, scene["characters"], sid)
    place = _entity(ec, "places", scene["place_id"], sid)
    props = [{"prop_id": pid, "name": _entity(ec, "props", pid, sid)["name"]} for pid in scene["props"]]
    pack = _pack(ec, ctx, announced, note=note)
    budget = _word_budget(ec, scene)
    system, user, schema = prompts.build_e2(
        pack, scene=scene, scene_number=script["scenes"].index(scene) + 1, outline=script["scenes"],
        previous=_previous_line(ec, script, scene), word_budget=budget, cast=cast,
        place={"place_id": place["place_id"], "name": place["name"], "layout_notes": place["layout_notes"] or ""},
        props=props, sfx_cues=ec.sfx_cues, narrator_enabled=ec.narrator,
        voice_direction=ec.style_lock["audio"]["voice_direction"], note=pack.note,
    )

    # A reply under half the word budget, or over 1.5x it, is retryable
    # (validate_e2; never both at once -- the floor sits below the ceiling
    # for every budget, so they never stack). If the retry is *still* only
    # off on its word count, the existing "fail after two attempts" path
    # would leave this whole scene a stub over a borderline word count.
    # Instead, the second attempt's word-count error alone (nothing else
    # wrong with the reply) is accepted with a log line (spec 4.2, F3; the
    # human's own choice) -- a real problem (a bad speaker, an over-cap
    # line) still fails the scene exactly as before.
    attempt = {"n": 0}

    def validate(reply):
        attempt["n"] += 1
        _repair_e2_reply(ec, reply)
        errors = prompts.validate_e2(reply, scene=scene, narrator_enabled=ec.narrator, sfx_cues=ec.sfx_cues,
                                     word_budget=budget)
        word_count_only = bool(errors) and all(
            e.startswith(prompts.E2_WORD_FLOOR_PREFIX) or e.startswith(prompts.E2_WORD_CEILING_PREFIX)
            for e in errors
        )
        if errors and not (word_count_only and attempt["n"] >= 2):
            return errors
        if word_count_only and attempt["n"] >= 2:
            ctx.on_log(f"⚠️ Scene {sid}: accepting a reply after a retry despite its word count ({errors[0]}).")
        trial = copy.deepcopy(script)
        apply_e2(ec, scene_of(trial, sid), reply)
        return episode_common.trial_errors(ec, trial)

    reply = llm_call.call_json(ctx, "E2", system, user, schema, validator=validate, runner=tools.runner,
                               time_fn=tools.time_fn)
    apply_e2(ec, scene, reply)
    return True


# -------------------------------------------------------------------- E3

def apply_e3(ec, script, reply) -> list:
    """The parts E3 answered into *script* (in place): only their own keys.
    Returns the ids of the scenes it rewrote."""
    touched = []
    for part in ("recap", "hook", "cliffhanger"):
        if part not in reply:
            continue
        scene = framing_scene(script, part)
        if scene is None:
            continue
        sid = scene["scene_id"]
        scene["lines"] = [_line(ec, sid, k, line) for k, line in enumerate(reply[part]["lines"])]
        _keep_cues(scene)
        if part == "recap":
            text = reply[part]["on_screen_text"]
            scene["on_screen_text"] = _fr_text(ec, text) if text else None
        elif part == "hook":
            text = reply[part]["on_screen_text"]
            script["hook"]["on_screen_text"] = _fr_text(ec, text) if text else None
        else:
            script["cliffhanger"]["reveal"] = _fr_text(ec, reply[part]["reveal"])
            script["cliffhanger"]["scene_id"] = script["scenes"][-1]["scene_id"]
        scene["state"] = "written"
        scene["source"] = "E3"
        touched.append(sid)
    if "teaser" in reply:
        script["next_episode_teaser"] = _fr_text(ec, reply["teaser"])
    return touched


def _body_line(ec, script, *, first):
    scenes = [scene for scene in body_scenes(script) if scene["lines"]]
    if not scenes:
        return None
    line = scenes[0]["lines"][0] if first else scenes[-1]["lines"][-1]
    return {"speaker_name": speaker_name(ec, line["speaker"]), "text": line["text"]}


def write_framing(ctx, ec, script, part, *, tools, announced, note=None) -> list:
    """E3 into *script* (in place; the caller writes it): every framing
    part when *part* is None, else that one part alone. Returns the ids of
    the scenes it rewrote."""
    keys = e3_parts(ec.ep) if part is None else [part]
    hook, cliff, recap = (framing_scene(script, name) for name in ("hook", "cliffhanger", "recap"))
    speaking = []
    for key, scene in (("hook", hook), ("cliffhanger", cliff), ("recap", recap)):
        if key in keys and scene is not None:
            speaking.extend(cid for cid in scene["characters"] if cid not in speaking)
    budgets = {key: _word_budget(ec, scene) for key, scene in (("hook", hook), ("cliffhanger", cliff),
                                                                  ("recap", recap)) if scene is not None}
    pack = _pack(ec, ctx, announced, note=note)
    system, user, schema = prompts.build_e3(
        pack, ep=ec.ep, part=part, note=pack.note, hook_scene=hook, cliffhanger_scene=cliff, recap_scene=recap,
        outline=script["scenes"], first_body_line=_body_line(ec, script, first=True),
        last_body_line=_body_line(ec, script, first=False), arc_entry=ec.arc_entry,
        next_arc_entry=ec.next_arc_entry, memory=ec.season, episode_defaults=ec.episode_defaults,
        word_budgets=budgets, cast=_cast_lines(ec, speaking, (hook or cliff or {}).get("scene_id")),
        narrator_enabled=ec.narrator,
    )
    llm_call.announce_trimmed(ctx, pack, announced)

    def validate(reply):
        _repair_e3_reply(ec, reply)
        errors = prompts.validate_e3(reply, ep=ec.ep, part=part, hook_scene=hook, cliffhanger_scene=cliff,
                                     recap_scene=recap, narrator_enabled=ec.narrator,
                                     episode_defaults=ec.episode_defaults)
        if errors:
            return errors
        trial = copy.deepcopy(script)
        apply_e3(ec, trial, reply)
        return episode_common.trial_errors(ec, trial)

    reply = llm_call.call_json(ctx, "E3", system, user, schema, validator=validate, runner=tools.runner,
                               time_fn=tools.time_fn)
    return apply_e3(ec, script, reply)


# -------------------------------------------------------------------- E4

def check_consistency(ctx, ec, script, *, tools, announced, now) -> dict:
    """E4 over the whole script; the report is set on *script* (in place;
    the caller writes it) and returned."""
    digest = prompts.script_digest(script, {
        "places": {pid: doc["name"] for pid, doc in ec.entities["places"].items()},
        "cast": ec.names,
    })
    pack = _pack(ec, ctx, announced)
    system, user, schema = prompts.build_e4(
        pack, script_digest=digest,
        cast=[{"char_id": doc["char_id"], "name": doc["name"], "personality": doc["personality"]}
              for doc in ec.cast],
        places=[{"place_id": pid, "name": doc["name"]} for pid, doc in ec.entities["places"].items()],
        memory=ec.season,
    )
    scene_ids = [scene["scene_id"] for scene in script["scenes"]]

    def report_of(reply, checked_at):
        return {
            "passed": reply["passed"],
            "issues": [{"scene_id": issue["scene_id"], "kind": issue["kind"], "fix": issue["fix"].strip()}
                       for issue in reply["issues"]],
            "checked_rev": script["rev"], "checked_at": checked_at, "stale": False,
        }

    def validate(reply):
        errors = prompts.validate_e4(reply, scene_ids=scene_ids)
        if errors:
            return errors
        trial = copy.deepcopy(script)
        trial["consistency_report"] = report_of(reply, now)
        return episode_common.trial_errors(ec, trial)

    reply = llm_call.call_json(ctx, "E4", system, user, schema, validator=validate, runner=tools.runner,
                               time_fn=tools.time_fn)
    script["consistency_report"] = report_of(reply, llm_call.utc_now())
    return script["consistency_report"]


def consistency_line(report) -> str:
    if report["passed"]:
        return "🔍 Consistency: passed"
    count = len(report["issues"])
    return f"🔍 Consistency: {count} issue{'s' if count != 1 else ''}"


# -------------------------------------------------------------------- the run

class _Run(LineMeasurement):
    """One run of the step: the script as it stands, and what failed. The
    voice measurement is :class:`voice_lines.LineMeasurement`'s."""

    def __init__(self, ctx, ec, *, runner, time_fn, adapters=None, transport=None, budget=None):
        self.ctx = ctx
        self.ec = ec
        self.tools = entities.Tools(runner=runner, time_fn=time_fn, adapters=adapters, transport=transport)
        # A caller that runs this step inside a longer one (the fast track)
        # hands in its own budget; otherwise the step's own 30 minutes.
        self.budget = budget if budget is not None else episode_common.Budget(time_fn)
        self.announced = set()
        self.failed = []  # [(what, target or None, reason)]
        self.calls = 0
        self.script = None
        self.storyboard = episode_common.read_episode(ec, STORYBOARD_DOC)
        self.voice_failed = []  # [(line_id, speaker, reason)]
        self.measured = 0
        self.board_refused = False

    # ---------------------------------------------------------- bookkeeping

    def left(self) -> str:
        """What is not written yet, for the budget's message."""
        script, ep = self.script, self.ec.ep
        if script is None or not script["scenes"]:
            return "the beat sheet (E1), then every scene (E2), the framing scenes (E3) and the consistency check (E4)"
        parts = []
        stubs = [scene["scene_id"] for scene in body_scenes(script) if scene["state"] == "stub"]
        if stubs:
            parts.append(f"scene{'s' if len(stubs) > 1 else ''} {', '.join(stubs)} (E2)")
        missing = missing_parts(script, ep)
        if missing:
            parts.append(f"the {_and(missing)} (E3)")
        if stubs or missing or needs_check(script):
            parts.append("the consistency check (E4)")
        return _and(parts) or "nothing"

    def before_call(self) -> None:
        self.ctx.cancel.check()
        try:
            self.budget.before_call(self.left)
        except StepFailed as exc:
            message = str(exc)
            if self.failed:
                message += f" Also failed in this run: {self.failures()}."
            raise BudgetSpent(message) from None

    def save(self) -> None:
        now = llm_call.utc_now()
        episode_common.retime(self.script, self.ec, self.storyboard)
        episode_common.write_script(self.ec, self.script, now=now)

    def fail(self, what, target, exc) -> None:
        self.failed.append((what, target, exc.reason))
        self.ctx.on_log(f"✖ {what[0].upper()}{what[1:]} failed: {exc.reason}")

    def failures(self) -> str:
        return "; ".join(f"{what} failed ({reason})" for what, _target, reason in self.failed)

    # ---------------------------------------------------------------- steps

    def beat_sheet(self) -> None:
        ec = self.ec
        self.before_call()
        self.ctx.on_log(f"🎬 Episode {ec.ep}: beat sheet (E1)")
        write_beat_sheet(self.ctx, ec, self.script, tools=self.tools, announced=self.announced)
        self.calls += 1
        self.save()
        body = len(body_scenes(self.script))
        self.ctx.on_log(f"🎬 Episode {ec.ep}: “{self.script['title']}” — {len(self.script['scenes'])} scenes, "
                        f"{body} body")

    def body(self) -> None:
        ec, script = self.ec, self.script
        total = len(script["scenes"])
        for scene in body_scenes(script):
            if scene["state"] != "stub":
                continue
            sid = scene["scene_id"]
            label = f"Scene {script['scenes'].index(scene) + 1} of {total} ({sid}, {scene['function']})"
            if not can_speak(ec, scene):
                write_body_scene(self.ctx, ec, script, sid, tools=self.tools, announced=self.announced)
                self.save()
                self.ctx.on_log(f"📝 {label}: no one can speak in it (no character, no narrator); written "
                                "without dialogue")
                continue
            self.before_call()
            self.ctx.on_log(f"📝 {label}")
            try:
                write_body_scene(self.ctx, ec, script, sid, tools=self.tools, announced=self.announced)
            except BudgetSpent:
                raise
            except StepFailed as exc:
                self.calls += 1
                self.fail(f"scene {sid}", target_for(ec.ep, sid), exc)
                continue
            self.calls += 1
            self.save()

    def framing(self) -> None:
        ec, script = self.ec, self.script
        missing = missing_parts(script, ec.ep)
        if not missing:
            return
        batches = [None] if missing == e3_parts(ec.ep) else missing
        for part in batches:
            self.before_call()
            what = _and(e3_parts(ec.ep)) if part is None else part
            self.ctx.on_log(f"🪝 Framing scenes (E3): {what}")
            try:
                write_framing(self.ctx, ec, script, part, tools=self.tools, announced=self.announced)
            except BudgetSpent:
                raise
            except StepFailed as exc:
                self.calls += 1
                for name in (missing if part is None else [part]):
                    self.failed.append((f"the {name}", part_target(ec.ep, script, name), exc.reason))
                self.ctx.on_log(f"✖ The {what} failed: {exc.reason}")
                continue
            self.calls += 1
            self.save()

    def consistency(self) -> None:
        ec, script = self.ec, self.script
        if not is_complete(script, ec.ep) or not needs_check(script):
            return
        self.before_call()
        self.ctx.on_log("🔍 Consistency check (E4)")
        try:
            report = check_consistency(self.ctx, ec, script, tools=self.tools, announced=self.announced,
                                       now=llm_call.utc_now())
        except BudgetSpent:
            raise
        except StepFailed as exc:
            self.calls += 1
            self.failed.append(("the consistency check", None, exc.reason))
            self.ctx.on_log(f"✖ The consistency check failed: {exc.reason}")
            return
        self.calls += 1
        self.save()
        self.ctx.on_log(consistency_line(report))

    def run(self) -> dict:
        ec = self.ec
        self.script = episode_common.read_episode(ec, SCRIPT_DOC)
        if self.script is None:
            self.script = skeleton(ec, now=llm_call.utc_now())
        if not self.script["scenes"]:
            self.beat_sheet()
        self.body()
        self.framing()
        self.consistency()

        script = self.script
        if self.calls == 0 and not self.failed:
            self.ctx.on_log(f"✅ Episode {ec.ep}'s script is complete and checked: nothing to write.")
        measuring = bool(self.ctx.params.get(MEASURE_PARAM))
        if measuring:
            self.measure()
        self.ctx.on_log(episode_common.timing_line(script))
        problems = []
        if self.failed:
            targets = [target for _what, target, _reason in self.failed if target]
            finish = []
            if targets:
                finish.append(f"regenerate {entities.quoted_list(targets)}")
            finish.append("run the script step again")
            problems.append(f"Episode {ec.ep}'s script is not finished: {self.failures()}. To finish it, "
                            f"{' or '.join(finish)}.")
        if self.voice_failed:
            problems.append(self.voice_message())
        if problems:
            raise StepFailed(" ".join(problems))
        report = script["consistency_report"]
        summary = {
            "ep": ec.ep, "scenes": len(script["scenes"]), "calls": self.calls,
            "total_s": (script["timing"] or {}).get("total_s"),
            "consistency": None if report is None else report["passed"],
        }
        if measuring:
            summary["measured"] = self.measured
        return summary


def run(ctx, *, runner=None, time_fn=time.monotonic, adapters=None, transport=None, budget=None) -> dict:
    """The step (module docstring). *budget*: an ``episode_common.Budget``
    shared with a caller running this step inside its own (the fast track);
    None gives the step its own."""
    ec = episode_common.load_episode_context(ctx)
    episode_common.check_episode_preconditions(ctx, ec)
    ctx.cancel.check()
    return _Run(ctx, ec, runner=runner, time_fn=time_fn, adapters=adapters, transport=transport,
                budget=budget).run()
