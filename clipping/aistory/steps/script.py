"""Step ``script``: one episode's script, beat sheet to consistency check
(spec 3 step 8, 2.7, 4.2 rows E1-E4; AI Story phase 3).

``ctx.ep`` is the episode. Needs a ``ready`` story, an episode the season
plans, and -- from episode 2 on -- the previous episode's series memory
written, approved and fresh (the gate, DEC-130 as amended by plan 11 stage
4: ``episode_common.check_episode_preconditions``).

One job fills whatever the episode's ``script.json`` is still missing, in
this order, writing the script after **every** accepted call (atomic,
validated against its schema, its own cross-checks and the story), so what
was paid for survives a failure, a cancel or the step's time budget:

1. no scene yet -> **E1**, the beat sheet: every scene a stub (function,
   place and time variant, characters, props, a summary, an emotion, a
   duration hint clamped into its slot), ids ``s00`` (the recap, from the
   template's ``recap_from_episode``) then ``s01``... in order. From episode
   2 on (phase 5, plan 11 stage 3), E1 is handed the hooks open when the
   episode starts (``series_memory.open_hooks_before``) and the audience
   direction chosen on the previous episode's feedback; a scene keeps the
   open hook it pays off (``pays_off``), at least one body scene naming one.
   E1 is offered the **approved** characters only (:func:`e1_cast`, plan 11
   stage 4): one an N1 proposal just added is written into an episode once
   it is approved;
2. every body scene still a stub, in order -> **E2**, its lines (ids from
   the scene's own block, ``schemas.line_id_for``; an estimated timing
   each), sfx cues anchored to ``start`` or a line id, optional on-screen
   text. A body scene nobody can speak in (no character, no narrator) is
   written silent, without a call. A scene whose E2 fails is printed and
   left a stub; the others go on;
3. the framing parts E2 never writes -- the hook's lines and on-screen text,
   the cliffhanger's reveal and line, the recap (episode >= 2, written from
   the previous episode's recap), the next-episode teaser -> **E3**, in full
   when every one is missing, else one partial E3 per missing part;
4. a complete script whose consistency report is missing, stale or of an
   older revision -> the hook-payoff pre-check (:func:`payoff_issues`, no
   call), then **E4** (analytic), the report
   ``{passed, issues, checked_rev, checked_at, stale: false}`` -- the
   pre-check's ``hook_payoff`` issues first, failing it whatever E4 says.

**A v2 story** (phase 7 stage 6a, A16/A17, DEC-230/231) also gets:

- E2v2/E3v2 replies refused for a line repeating another of the episode,
  and E3v2's for a hook with no on-screen text (``prompts.validate_e2`` /
  ``validate_e3``), so the retry and the chain's next links ask again;
- the **fill pass** (:meth:`_Run.fill`), once the script is complete,
  not approved, and its estimate under the template's window: E2v2 again,
  with a note to write fuller, on the shortest body scenes -- each raised
  to its slot's top first, so its word budget grows -- at most
  :data:`FILL_CALLS_MAX` calls, stopping once the estimate is inside; what
  it did is logged and in the step's result (``fill``). Before E4 and J1,
  so they judge the filled script; on a script checked already, a
  rewritten scene goes through ``episode_common.mark_changed`` like a
  regenerate;
- **J1** after E4 (``steps/judge.check_first_watch``), the first-watch
  report ``script.first_watch``, with the repeated-line and hook-text
  checks merged in -- missing, stale or of an older revision -> one call;
- the **repair pass** (:meth:`_Run.repair`, phase 7 follow-up stage G):
  when J1's fresh report found issues, the scenes they name are written
  again -- E2v2 for a body scene, the partial E3v2 for a framing one --
  with the kind in words and the fix verbatim as the author's note
  (:func:`repair_plan`: ``repeated_line`` on the later line's scene,
  ``no_hook_text`` on the hook, ``unclear_goal`` / ``unmotivated`` on the
  named scene, ``unintroduced`` / ``object_unseen`` on the named scene and
  on the nearest earlier body scene where the character is present / the
  prop is listed; an issue with no scene stays in the report), each like a
  regenerate (``mark_changed``, the slot target kept), then the fill pass
  if the length left the window, E4 and J1 again -- at most
  :data:`REPAIR_PASSES_MAX` passes a run of at most :data:`REPAIR_CALLS_MAX`
  calls each, in scene order; the loop ends when J1 passes, a pass
  repaired nothing, the passes are spent or the budget is. Recorded as
  ``script.repairs`` and in the step's result (``repairs``); never on an
  approved script. J1 version 2 (DEC-248): only the **blocking** issues are
  repaired (the minor ones stay in the report, for the human to read); the
  J1 after a pass is a re-check shown the blocking issues it tried
  (``judge.check_first_watch``'s *previous*), so the blocking set can only
  shrink; each note is fitted to the pack's note cap by shortening the
  fixes, never the asks (:func:`_fitted_note`); and an ``object_unseen``
  whose fix names a prop of the story that the scene does not list gets it
  listed on the scene before the rewrite (``props_added`` in the record) --
  an object that is not one of the story's props is never added to the
  library: the rewrite shows it in the lines.

**Check only** (``params.check_only``, plan 19 stage 3): E4 and, on a v2
story, J1 on the script as it stands, each when its report is missing or
stale -- nothing written (no scene, no framing part, no fill pass), never
the repair pass, never a measurement; a script not complete yet is refused
naming what is missing (:func:`check_only_refusal`). What a human's edit
needs before the approval (``workflow.approve_script`` says so when the
check is out of date): the edit checked, never rewritten.

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
import re
import time

from .. import context, media_policy, prompts, recipes, schemas, series_memory, timing
from . import entities, episode_common, judge, llm_call
from . import places as places_step
from .episode_common import SCRIPT_DOC, STORYBOARD_DOC
from .llm_call import StepFailed
# The voice measurement lives in ``voice_lines`` (lifted unchanged, phase 4
# stage 8, so the assets step speaks a line the same way); its names stay
# reachable here for every caller of phase 3.
from .voice_lines import (  # noqa: F401 -- re-exported
    STORY_TTS_CALL_SECONDS, VOICE_ASSETS, BudgetSpent, LineMeasurement, asset_name, is_measured, lines_to_measure,
    measure_estimate, no_voice_reason, speaker_name, speaker_voice, speech_provider,
)

FRAMING_FUNCTIONS = ("recap", "hook", "cliffhanger")

MEASURE_PARAM = "measure_voices"
# Plan 19 stage 3 (F6): ``params.check_only`` -- E4 and, on v2, J1 on the
# script as it stands; nothing written, nothing repaired (:func:`check_only_refusal`).
CHECK_ONLY_PARAM = "check_only"
CHECK_ONLY_MEASURE_REFUSAL = (f"A check-only run measures nothing: send {CHECK_ONLY_PARAM} without {MEASURE_PARAM}, "
                              "or measure the voices in a run of their own.")

# The v2 fill pass (phase 7 stage 6a, A17): at most this many E2v2 calls a
# run, on the shortest body scenes, with this note.
FILL_CALLS_MAX = 2
FILL_NOTE = ("This scene runs short of the episode's length: write it fuller, close to the top of its word budget, "
             "with one more line if the beat allows it.")
# Plan 24 stage 2 (D-4): a writing-v3 scene has hard caps a line and a scene
# (its line plan), so its fill note asks for words up to them, never past.
FILL_NOTE_V3 = ("This scene runs short of the episode's length: write it fuller, each line close to its cap and "
                "within its caps.")

# The v2 repair pass (phase 7 follow-up, stage G): after J1, the scenes its
# issues name are written again with the fix as the note -- at most this many
# passes a run, each at most this many calls; every note opens with this.
# DEC-248: 4 calls a pass left the late scenes -- the cliffhanger among them --
# never written again (the human's run: s05, s06 and s08 still named after two
# passes); 8 covers six issues and the earlier scenes they send.
REPAIR_PASSES_MAX = 2
REPAIR_CALLS_MAX = 8
REPAIR_NOTE_HEAD = "First-watch check -- "
# DEC-260: an issue of the consistency check (E4) is repaired the same way, its
# note headed so the writer knows which check asks.
CONSISTENCY_NOTE_HEAD = "Consistency check -- "
CONSISTENCY_SOURCE = "consistency"
# DEC-261: E4's kinds by severity (E4 itself grades none). A voice note ("X
# would not say it that way") is a taste the judge finds anew on every rewrite
# -- the live episode got six on each of three passes -- so it is minor: kept
# on the report for the human, never repaired, never a refusal. A continuity,
# place, series-memory or hook-payoff issue is blocking: repaired, and refused
# while it stands.
CONSISTENCY_MINOR_KINDS = ("character", "other")


def is_consistency_blocking(issue) -> bool:
    return issue.get("kind") not in CONSISTENCY_MINOR_KINDS


def consistency_blocking_issues(report) -> list:
    return [issue for issue in (report or {}).get("issues") or [] if is_consistency_blocking(issue)]


def consistency_minor_issues(report) -> list:
    return [issue for issue in (report or {}).get("issues") or [] if not is_consistency_blocking(issue)]


def consistency_passes(report) -> bool:
    """Whether a consistency *report* lets the script through (DEC-261): it
    passed, or every issue it found is minor."""
    return bool(report) and (bool(report.get("passed")) or not consistency_blocking_issues(report))
# A note longer than the pack's cap is cut at its end (``context.build_pack``),
# which loses the last ask; a repair note is fitted to it first (DEC-248).
REPAIR_NOTE_MAX_WORDS = context._NOTE_WORD_LIMIT
# A scene lists at most this many props (the script schema's own bound).
SCENE_PROPS_MAX = schemas.EPISODE_SCRIPT_SCHEMA["properties"]["scenes"]["items"]["properties"]["props"]["maxItems"]


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


def check_only_refusal(script, ep, params=None):
    """Why a check-only run (``params.check_only``, plan 19 stage 3) will not
    run, or None: it writes nothing, so the script must be complete -- a stub
    scene or a missing framing part is never filled by it, the run refuses
    instead, naming them -- and it measures nothing (``measure_voices`` with
    it is refused). The API and the CLI refuse with this sentence before any
    job exists (``workflow.require_checkable_script``); the runner again."""
    if (params or {}).get(MEASURE_PARAM):
        return CHECK_ONLY_MEASURE_REFUSAL
    if script is None or not script["scenes"]:
        return (f"Episode {ep} has no script yet, and a check-only run writes nothing: write it first (the script "
                "step).")
    missing = [scene["scene_id"] for scene in body_scenes(script) if scene["state"] == "stub"]
    missing += [f"the {part}" for part in missing_parts(script, ep)]
    if missing:
        return (f"Episode {ep}'s script is not complete ({_and(missing)} not written yet), and a check-only run "
                "writes nothing: run the script step to finish it (it checks it too).")
    return None


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


# ------------------------------------------------- writing v3 (plan 22 stage 3)

def writes_v3(ec, script=None) -> bool:
    """Whether episode *ec* is written on the v3 prompts (E1v3/E2v3/E3v3,
    J1v3): a v2 story whose ``generation_profile.writing`` is "v3"
    (``media_policy.writing_v3``) -- and, given its *script*, one whose beat
    sheet is E1v3's (it has a ``spine``) or not written yet: an episode
    beat-sheeted before the story turned v3 is finished, and judged, on the
    prompts that began it (``judge.j1_version``). Every other story writes,
    judges and times as before (RC-W3)."""
    return judge.writes_v3(ec.story, script)


def _speech_lengths(ec) -> tuple:
    """``(speaking lengths, silent lengths)`` of a native-speech story's
    clips (``clips.speech_lengths``: its links' own tables, Veo's 4/6/8 s by
    default)."""
    from . import clips as clips_step

    return clips_step.speech_lengths(ec.story)


def line_plan(ec, script, scene, *, tail_floor=None) -> dict:
    """``timing.scene_plan`` of *scene* on this story (plan 24 stage 1,
    D-2): the narrator when it may speak in the scene (:func:`narrator_in`),
    each voice at its provider's overrun (``voice_lines.speech_provider``),
    a native-speech story's clips on its links' lengths, the tail floor the
    Script step's own timing will charge (``timing.plan_tail_floors``)."""
    native = media_policy.native_speech(ec.story)
    speech, silent = _speech_lengths(ec) if native else (timing.PLAN_SPEECH_LENGTHS, None)
    narrator = narrator_in(ec, scene)
    if tail_floor is None:
        tail_floor = timing.plan_tail_floors(script, ec.template).get(scene["scene_id"])
    return timing.scene_plan(
        ec.template, scene, lang=ec.language, native=native,
        narrator_provider=speech_provider(ec, "narrator") if narrator else None, narrator=narrator,
        speakers={cid: speech_provider(ec, cid) for cid in scene["characters"]}, style_lock=ec.style_lock,
        tail_floor=tail_floor, speech_lengths=speech, silent_lengths=silent)


def _store_plan(scene, plan) -> None:
    """*scene*'s stored ``slot_s`` and ``line_plan`` set from *plan* -- on a
    native-speech story with its ``shots`` (plan 27 stage 2: each shot's
    clip, its lines -- one exchange -- and its words), what the storyboard
    builds one shot per exchange from."""
    scene["slot_s"] = list(plan["slot_s"])
    keys = ("allowed_speech_s", "lines", "max_words", "min_words") + (("shots",) if "shots" in plan else ())
    scene["line_plan"] = {key: plan[key] for key in keys}


def store_line_plans(ec, script) -> None:
    """Every scene of *script* (in place) gains its ``slot_s`` and its
    ``line_plan`` (:func:`line_plan`; plan 24 stage 1, D-2). The writer
    never reads the stored plan: E2v3/E3v3 recompute it when they write a
    scene (:func:`current_plan`, plan 24 stage 2).

    Plan 28 stage A2: on a native-speech story the plans are held inside the
    episode's window first (``timing.fit_episode_plans``: a plain body scene
    the narrator alone, then a scene's clips a second under what they sum
    to), and what the fit changed is stored on the scene (``character_line``
    False, ``clip_cap_s``) so :func:`current_plan` recomputes the same plan.
    A plan that still cannot fit is left as cheap as it got: the Script step
    refuses it (:func:`plan_fit_refusal`). A scene no clip of this story's
    links fits at all is refused here, in one plain sentence."""
    floors = timing.plan_tail_floors(script, ec.template)

    def plan_of(scene):
        return line_plan(ec, script, scene, tail_floor=floors[scene["scene_id"]])

    try:
        if not media_policy.native_speech(ec.story):
            for scene in script["scenes"]:
                _store_plan(scene, plan_of(scene))
            return
        fit = timing.fit_episode_plans(ec.template, script["scenes"], plan_of,
                                       window_hi=float(ec.template["window_s"][1]),
                                       end_card_s=timing.plan_end_card_s(script, ec.template))
    except timing.PlanError as exc:
        raise _plan_failure(ec, exc) from None
    for scene in script["scenes"]:
        scene.update(fit["changes"].get(scene["scene_id"], {}))
        _store_plan(scene, fit["plans"][scene["scene_id"]])


def _plan_failure(ec, exc) -> StepFailed:
    """A scene no shape fits (``timing.PlanError``, plan 28 stage A2), as the
    step's one plain sentence."""
    return StepFailed(timing.plan_slot_refusal(ec.ep, exc.function, exc.need_s, exc.slot_hi_s))


def _ends_on_card(ec) -> bool:
    """Whether this episode ends on the end card: its style's cliffhanger cuts to black, or its story's recipe
    asks for the card (plan 32 stage 4, ``recipes.ends_on_card``)."""
    return recipes.ends_on_card(ec.story, ec.episode_defaults.get("cliffhanger_style"))


def beat_sheet_slots(ec) -> list:
    """The slot list E1 is asked to fill (``timing.episode_slots``): on a
    native-speech story (plan 28 stage A2) as many body scenes as the plan's
    floor lets fit the window on this story's links, the narrator and the
    cliffhanger's end card counted -- the template's default whenever it
    fits; every other story's, the template's default."""
    if not media_policy.native_speech(ec.story):
        return timing.episode_slots(ec.template, ec.ep)
    return timing.episode_slots(ec.template, ec.ep, lengths=_speech_lengths(ec), narrator=bool(ec.narrator),
                                lang=ec.language, style_lock=ec.style_lock,
                                end_card=_ends_on_card(ec))


def plan_fit_refusal(ec, script=None, *, log=None):
    """Why this episode's plan cannot fit its window (plan 28 stage A1), as
    one plain sentence, or None when it can -- or when the story's speech is
    not native (a TTS story's scenes are re-timed to the window). With a
    *script* whose scenes carry their plans (an E1v3 beat sheet), the plans'
    own clips (``timing.plan_clip_floor_s``); without one, the cheapest shape
    the template allows on this story's links (``timing.plan_floor_preview``:
    no call made). *log* gets each scene's arithmetic as info lines."""
    if not media_policy.native_speech(ec.story):
        return None
    window_hi = float(ec.template["window_s"][1])
    planned = [scene for scene in (script or {}).get("scenes") or () if (scene.get("line_plan") or {}).get("shots")]
    if planned:
        floor = timing.plan_clip_floor_s(script, ec.template)
        scenes = len(script["scenes"])
        rows = [(scene["scene_id"], scene["function"],
                 sum(float(shot["clip_s"]) for shot in scene["line_plan"]["shots"])) for scene in planned]
        end_card = floor - sum(clip for _sid, _function, clip in rows)
    else:
        cut = _ends_on_card(ec)
        preview = timing.plan_floor_preview(ec.template, ec.ep, _speech_lengths(ec), bool(ec.narrator),
                                            lang=ec.language, style_lock=ec.style_lock, end_card=cut)
        unplannable = preview.get("unplannable")
        if unplannable:
            # Plan 28 stage A2: a part of the format no clip of this story's links fits at all.
            return timing.plan_slot_refusal(ec.ep, unplannable["function"], unplannable["need_s"],
                                            unplannable["slot_hi_s"])
        floor, scenes = preview["floor_s"], preview["scenes"]
        rows = [(f"slot {k + 1}", slot, clip) for k, (slot, clip) in enumerate(preview["per_scene"])]
        end_card = floor - sum(clip for _sid, _function, clip in rows)
    sentence = timing.plan_floor_refusal(ec.ep, scenes, floor, window_hi)
    if sentence and log is not None:
        for sid, function, clip in rows:
            log(f"ℹ️ {sid} ({function}): {clip:g} s of clips")
        log(f"ℹ️ end card: {end_card:g} s; total {floor:g} s of clips against {window_hi:g} s")
    return sentence


# ------------------------------------------- plan 28 stage A3: the remedy on "over"

# The author's note a scene rewritten to its fitted plan is written with.
FIT_NOTE = ("Rewrite this scene to its new line plan: the episode must fit its length, so the scene buys fewer or "
            "shorter clips. Keep what happens and who it happens to; cut words, never the scene's turn.")
# The fields a plan is stored in and the episode's fit sets (restored on a rewrite that fails).
_PLAN_FIELDS = ("slot_s", "line_plan", "character_line", "clip_cap_s")


def _clip_shape(plan) -> list:
    return [(int(shot["clip_s"]), bool(shot["speaks"])) for shot in (plan or {}).get("shots") or ()]


def refit_plans(ec, script, *, only=None) -> list:
    """The episode's fit (``timing.fit_episode_plans``, plan 28 stage A2) on
    *script* as it stands now (plan 28 stage A3): each scene -- each in
    *only* when given -- stores the plan the fit holds it to and the fields
    the fit changed (``character_line``, ``clip_cap_s``), in place. Returns
    the ids of the WRITTEN scenes whose planned clips changed, in script
    order -- the scenes a remedy rewrites (a stub's new plan needs no
    rewrite). ``[]`` off native speech, or for a script a scene of which has
    no stored plan (``timing.plan_board``). A scene no clip fits is refused
    in one plain sentence (:func:`_plan_failure`)."""
    if not media_policy.native_speech(ec.story) or timing.plan_board(script) is None:
        return []
    floors = timing.plan_tail_floors(script, ec.template)

    def plan_of(scene):
        return line_plan(ec, script, scene, tail_floor=floors[scene["scene_id"]])

    try:
        fit = timing.fit_episode_plans(ec.template, script["scenes"], plan_of,
                                       window_hi=float(ec.template["window_s"][1]),
                                       end_card_s=timing.plan_end_card_s(script, ec.template))
    except timing.PlanError as exc:
        raise _plan_failure(ec, exc) from None
    changed = []
    for scene in script["scenes"]:
        sid = scene["scene_id"]
        if only is not None and sid not in only:
            continue
        plan = fit["plans"][sid]
        moved = _clip_shape(scene.get("line_plan")) != _clip_shape(plan)
        scene.update(fit["changes"].get(sid, {}))
        _store_plan(scene, plan)
        if moved and scene["state"] != "stub":
            changed.append(sid)
    return changed


def rewrite_scenes(ctx, ec, script, board, scene_ids, *, tools, note, before_call=None) -> tuple:
    """Each scene of *scene_ids* written again with *note*, one after the
    other, exactly as a regenerate of ``scene:<ep>:<sid>`` writes it
    (``episode_regenerate``: E2 for a body scene, the partial E3 for a
    framing one -- each recomputing its plan from what the scene stores --
    then ``episode_common.mark_changed``, the re-time, the storyboard then
    the script written). *before_call* (no argument) is asked before each
    call (the caller's budget). A rewrite that fails keeps the scene as it
    was, its stored plan too. ``(rewritten ids, [(scene_id, reason)])``."""
    announced = set()
    rewritten, failed = [], []
    for sid in scene_ids:
        scene = scene_of(script, sid)
        if scene is None or sid in rewritten:
            continue
        if before_call is not None:
            before_call()
        kept = {key: copy.deepcopy(scene[key]) for key in _PLAN_FIELDS if key in scene}
        try:
            if scene["function"] in FRAMING_FUNCTIONS:
                touched = write_framing(ctx, ec, script, scene["function"], tools=tools, announced=announced,
                                        note=note)
            else:
                write_body_scene(ctx, ec, script, sid, tools=tools, announced=announced, note=note)
                touched = [sid]
        except BudgetSpent:
            raise
        except StepFailed as exc:
            for key in _PLAN_FIELDS:
                scene.pop(key, None)
            scene.update(kept)
            failed.append((sid, exc.reason))
            continue
        now = llm_call.utc_now()
        episode_common.mark_changed(script, board, scene_ids=touched, now=now)
        episode_common.retime(script, ec, board)
        if board is not None:
            episode_common.write_storyboard(ec, board, script, now=now)
        episode_common.write_script(ec, script, now=now)
        rewritten.extend(touched)
    return rewritten, failed


def current_plan(ec, script, scene) -> dict:
    """*scene*'s line plan as it stands now (:func:`line_plan`: the voices,
    the clip lengths and the neighbours of this moment), stored on the scene
    in place of whatever plan it had (plan 24 stage 2: a stale stored plan is
    never trusted). A scene no shape fits (plan 28 stage A2) is refused in
    one plain sentence."""
    try:
        plan = line_plan(ec, script, scene)
    except timing.PlanError as exc:
        raise _plan_failure(ec, exc) from None
    _store_plan(scene, plan)
    return plan


def over_cap_errors(errors) -> list:
    """The word-cap errors among *errors* (``prompts.is_word_cap_error``: a
    line or a scene over its plan's hard cap, plan 24 stage 2, D-4). A
    writing-v3 reply with any of them is never accepted; plan 24 stage 3's
    trim pass rewrites exactly the lines they name (see
    :func:`write_body_scene`)."""
    return [error for error in errors if prompts.is_word_cap_error(error)]


# Plan 24 stage 3 (D-4): the trim calls an episode may make after a writing-v3
# reply stays over its caps through the retry ladder -- one call per scene (or
# per framing call), counted on the episode context so E2 and E3 share them.
TRIM_CALLS_MAX = 4


def trim_calls_left(ec) -> int:
    """How many trim calls *ec*'s run may still make."""
    return TRIM_CALLS_MAX - getattr(ec, "trim_calls", 0)


def _cap_clause(errors, reply, names, *, scenes=None) -> str:
    """What the over-cap *errors* of *reply* say, in one clause for the failure
    sentence: ``line 2 (Rida) has 15 words, at most 12 (a 6 s shot)`` for each
    line past its cap (at most three), else the total (``the scene has 30
    words in total, at most 23 (a 13 s scene)``; a framing part, with its
    scene: ``the hook (scene s01) has ...``). *scenes* is ``{part: scene_id}``
    for an E3v3 reply, None for a body scene's."""
    parsed = [item for item in (prompts.parse_word_cap_error(error) for error in errors) if item]
    lines = [item for item in parsed if item["index"] is not None or item.get("span") is not None]
    parts = []
    for item in (lines or parsed)[:3]:
        # Plan 27 stage 2: an under-floor item names its range.
        bound = (f"the plan asks for {item['lo']}–{item['cap']}" if item.get("lo") is not None
                 else f"at most {item['cap']}")
        said = f"{item['words']} words, {bound} ({item['why']})"
        if item["index"] is not None:
            rows = (reply or {}).get("lines") or []
            speaker = rows[item["index"]]["speaker"] if item["index"] < len(rows) else None
            parts.append(f"line {item['index'] + 1} ({prompts.trim_speaker_label(names, speaker)}) has {said}")
        elif item.get("span") is not None:
            first, last = item["span"]
            parts.append(f"lines {first + 1}–{last + 1} (one exchange) have {item['words']} words in total, {bound} "
                         f"({item['why']})")
        elif item["part"]:
            parts.append(f"the {item['part']} (scene {(scenes or {}).get(item['part'], '?')}) has "
                         f"{item['words']} words in total, {bound} ({item['why']})")
        else:
            parts.append(f"the scene has {item['words']} words in total, {bound} ({item['why']})")
    return "; ".join(parts)


def _short_of_range(errors) -> bool:
    """Whether a word-cap error among *errors* is a line or part UNDER its
    planned floor (plan 27 stage 2) -- the failure sentence then asks for
    lines inside their ranges, not shorter ones."""
    return any((prompts.parse_word_cap_error(error) or {}).get("lo") is not None for error in errors)


def over_cap_failure(sid, clause, *, trimmed, many=False, short=False) -> StepFailed:
    """The one plain sentence a scene fails with when its lines stay over their
    caps (plan 24 stage 3, D-4): which scene, which lines, how to move on --
    no stack, no JSON. *trimmed*: the trim call was made (else the episode's
    trim calls were spent). *short* (plan 27 stage 2): a line is under its
    planned floor -- the scene is outside its ranges, regenerated so each line
    fits its own."""
    after = "the retry and the trim" if trimmed else "the retry, and this episode's trim calls are spent"
    if short:
        return StepFailed(f"Scene {sid} is still outside its word ranges after {after}: {clause}. Regenerate the "
                          "scene so each line fits its range.")
    fix = "shorter lines" if many else "a shorter line"
    return StepFailed(f"Scene {sid} is still over its caps after {after}: {clause}. Regenerate the scene with {fix} "
                      "or widen its slot.")


def framing_over_cap_failure(clause, *, trimmed, short=False) -> StepFailed:
    """:func:`over_cap_failure`'s sentence for the framing scenes (E3v3)."""
    after = "the retry and the trim" if trimmed else "the retry, and this episode's trim calls are spent"
    if short:
        return StepFailed(f"The framing scenes are still outside their word ranges after {after}: {clause}. "
                          "Regenerate the framing scenes so each part fits its range.")
    return StepFailed(f"The framing scenes are still over their caps after {after}: {clause}. Regenerate the "
                      "framing scenes with shorter lines or widen their slots.")


def narrator_in(ec, scene) -> bool:
    """Whether the narrator may speak in *scene*: the story's narrator is on
    and -- plan 22 stage 3 -- the template's ``narrator_slots``, when it has
    them (the confrontation format: the recap only; amends DEC-231 for that
    format), hold the scene's slot. A template without the key: every slot,
    as before."""
    if not ec.narrator:
        return False
    slots = ec.template.get("narrator_slots")
    return slots is None or timing.slot_name(scene["function"], ec.template) in slots


def narrator_errors(ec, reply, scenes_by_part) -> list:
    """A framing reply's narrator lines in a part whose scene the narrator
    may not speak in (:func:`narrator_in`), one error each: the E3 call's
    speakers are one list for every part it writes, so the slot rule is
    checked here."""
    errors = []
    for part, scene in scenes_by_part.items():
        block = reply.get(part) if isinstance(reply, dict) else None
        if scene is None or not isinstance(block, dict) or narrator_in(ec, scene):
            continue
        for i, line in enumerate(block.get("lines") or []):
            if isinstance(line, dict) and line.get("speaker") == "narrator":
                errors.append(f"$.{part}.lines[{i}].speaker: the narrator speaks only in the "
                              f"{_and(ec.template['narrator_slots'])} on this format, never in the {part}")
    return errors


def _so_far(ec, script, scene=None, *, skip=()) -> list:
    """``[(scene_id, speaker name, text)]``: every line of *script* in scene
    order -- before *scene* when given, else all -- leaving out the scenes
    in *skip* (the ones a call writes again)."""
    out = []
    for other in script["scenes"]:
        if scene is not None and other["scene_id"] == scene["scene_id"]:
            break
        if other["scene_id"] in skip:
            continue
        out.extend((other["scene_id"], speaker_name(ec, line["speaker"]), line["text"]) for line in other["lines"])
    return out


def _speakers(ec, scene) -> list:
    return list(scene["characters"]) + (["narrator"] if narrator_in(ec, scene) else [])


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


def _repair_e1_reply(ec, reply, *, new_objects_offered=False) -> None:
    """Repair a dropped French elision apostrophe in an E1 reply's own free
    text, in place, before its validator runs (spec 4.2, F1): a merged
    elision changes a word count (``"l alliance"`` is 2 "words", "l'alliance"
    is 1), so the repair has to happen before ``validate_e1`` counts them,
    not only when the reply is later applied to the script.

    A story with no props gets every scene's ``props`` emptied first: no
    string can name a prop it does not have, and with no ids to enumerate
    the schema cannot stop the free tier from listing object names there
    (T2-F9). Only lists are touched; a malformed reply is left to the
    validator. Skipped when *new_objects_offered* (phase 7 stage 3c, A11): a
    v2 story's scene may legitimately reference a new object by
    ``%prop_<slug>`` even when ``ec.prop_ids`` is still empty.

    Only a body scene pays a hook off: a recap, hook or cliffhanger scene's
    ``pays_off`` is emptied too (Tier-2 T2-P5-F7 -- the free tier put it on
    the hook scene every time, and E4 then refused the payoff), so the
    validator and E4 only ever see body-scene payoffs."""
    if isinstance(reply, dict) and isinstance(reply.get("scenes"), list):
        for scene in reply["scenes"]:
            if not isinstance(scene, dict):
                continue
            if not ec.prop_ids and not new_objects_offered and isinstance(scene.get("props"), list):
                scene["props"] = []
            if isinstance(scene.get("pays_off"), list) and scene.get("function") not in schemas.BODY_FUNCTIONS:
                scene["pays_off"] = []
            # Plan 24 stage 5 (D-6): only a body scene carries a character line, the same repair.
            if scene.get("character_line") is True and scene.get("function") not in schemas.BODY_FUNCTIONS:
                scene["character_line"] = False
    if ec.language != "fr":
        return
    reply["title"] = prompts.repair_fr_elisions(reply["title"])
    for scene in reply["scenes"]:
        scene["summary"] = prompts.repair_fr_elisions(scene["summary"])
    spine = reply.get("spine")  # E1v3 (plan 22 stage 3)
    if isinstance(spine, dict):
        for key, value in spine.items():
            if isinstance(value, str):
                spine[key] = prompts.repair_fr_elisions(value)


def _repair_e2_reply(ec, reply) -> None:
    """Same as :func:`_repair_e1_reply`, for an E2 reply: every line's text
    (what the word-budget floor below counts) and the on-screen text."""
    if ec.language != "fr":
        return
    # A reply without a strict schema may miss a key or type it wrongly: touch
    # only what is a list / a str and let the schema validator refuse the rest
    # as an ordinary error (a KeyError here would end the step with no retry).
    lines = reply.get("lines")
    if isinstance(lines, list):
        for line in lines:
            if isinstance(line, dict) and isinstance(line.get("text"), str):
                line["text"] = prompts.repair_fr_elisions(line["text"])
    on_screen = reply.get("on_screen_text")
    if isinstance(on_screen, str) and on_screen:
        reply["on_screen_text"] = prompts.repair_fr_elisions(on_screen)


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
        # Plan 24 stage 1 (D-1/D-5): the estimate at the speaking voice's overrun.
        "timing": timing.estimated_timing(text, ec.language, provider=speech_provider(ec, line["speaker"])),
    }


def _keep_cues(scene) -> None:
    """A rewritten scene keeps only the sfx cues still anchored to a line it has."""
    ids = {line["line_id"] for line in scene["lines"]}
    scene["sfx_cues"] = [cue for cue in scene["sfx_cues"] if cue["at"] == "start" or cue["at"] in ids]


def _pack(ec, ctx, announced, note=None):
    pack = context.build_pack(language=ec.language, story=ec.story, note=note)
    llm_call.announce_trimmed(ctx, pack, announced)
    return pack


# ------------------------------------------------- the v2 context (phase 7 stage 5c)

def knowledge_of(ec):
    """A v2 story's knowledge base (``knowledge.json``), or None: a legacy
    story, none written, or one that does not read -- the script step's
    gate refuses a v2 script without an approved one before any call
    (``episode_common.check_episode_preconditions``); a regenerate still
    writes from the dossiers and the ledger it can read."""
    if not media_policy.is_v2(ec.story):
        return None
    try:
        return ec.store.read_knowledge(ec.story_id)
    except schemas.SchemaError:
        return None


def ledger_of(ec):
    """Where every character stands when ``ec.ep`` starts
    (``context.ledger_before``), or None: a legacy story, or no knowledge
    base. The storyboard's resolution reads it (wardrobe sets, holders)."""
    if not media_policy.is_v2(ec.story) or not isinstance(ec.ep, int):
        return None
    return context.ledger_before(knowledge_of(ec), ec.season, ec.ep)


def _framing_slice_scene(script, part):
    """The framing scene whose slice an E3v2 call reads: the cliffhanger's
    (a full E3 or its own part, or the teaser, which follows it), else the
    hook's or the recap's for those parts alone."""
    if part in (None, "cliffhanger", "teaser"):
        return framing_scene(script, "cliffhanger")
    return framing_scene(script, part)


# -------------------------------------------------------------------- E1

def skeleton(ec, *, now) -> dict:
    """A script with nothing written yet (never written itself: E1 comes first)."""
    return {
        "$schema": schemas.EPISODE_SCRIPT_SCHEMA_NAME, "ep": ec.ep, "title": None, "language": ec.language,
        "template_id": ec.template["template_id"], "hook": {"on_screen_text": None}, "scenes": [],
        "cliffhanger": {"scene_id": None, "reveal": None,
                        "cut_to_black": _ends_on_card(ec)},
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


def _new_object_props(ec, new_objects) -> dict:
    """``{name: prop_id}`` for E1's ``new_objects`` (phase 7 stage 3c, A11):
    a prop stub (``places.new_prop`` -- the same creation path the places
    step's own list uses) for every entry whose name is not already one of
    the story's props, idempotent by name like ``places._create`` (a name
    already taken is reused, never duplicated -- this also makes a retried
    E1 call, whose validator runs this more than once, safe). The stub has
    no ``descriptor`` and no image yet: R1 (and, on a v2 story, R1v2) write
    them at the next places step run, the same gate as any other prop with
    no text yet -- this step makes no image or text call, so its own budget
    and estimate are unaffected.

    *ec* gains every prop created (``entities["props"]``, ``prop_ids``), so
    the rest of this reply's validation and application (``trial_errors``,
    ``apply_e1``'s own tag resolution) see it as a real, known id. Nothing
    is created for ``new_objects`` empty."""
    if not new_objects:
        return {}
    existing = entities.by_name(ec.entities["props"].values())
    taken = set(ec.prop_ids)
    ids = {}
    for obj in new_objects:
        key = entities.name_key(obj["name"])
        doc = existing.get(key)
        if doc is None:
            eid = schemas.entity_id(entities.PROPS, obj["name"], taken)
            now = llm_call.utc_now()
            doc = places_step.new_prop(eid, obj["name"], obj["one_line"], obj.get("owner_char_id"), now=now)
            errors = schemas.prop_errors(doc)
            if errors:
                raise StepFailed(f"{obj['name']} cannot be created: {'; '.join(errors[:3])}.")
            ec.store.write_entity(ec.story_id, entities.PROPS, doc, now=now)
            ec.entities["props"][eid] = doc
            ec.prop_ids.append(eid)
            existing[key] = doc
            taken.add(eid)
        ids[obj["name"]] = doc["prop_id"]
    return ids


def apply_e1(ec, script, reply) -> tuple:
    """E1's beat sheet into *script* (in place): one stub per scene, with the
    hooks it pays off (``pays_off``, from episode 2 on) when it names any.

    Phase 7 stage 3c (A11): *reply*'s ``new_objects`` (a v2 story, episode 2
    on) are created first (:func:`_new_object_props`), then every scene's
    ``%prop_<slug>`` tag (``prompts.new_object_tag``) is resolved to the id
    just created -- the story's ``prop_ids`` gains it, and the script never
    stores the transient tag, only real ids, like any other prop.

    Returns :func:`_normalize_episode_targets`'s own ``(before, after)``."""
    new_objects = reply.get("new_objects") or []
    created = _new_object_props(ec, new_objects)
    tag_ids = {prompts.new_object_tag(obj["name"]): created[obj["name"]]
               for obj in new_objects if obj["name"] in created}
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
            "props": [tag_ids.get(pid, pid) for pid in dict.fromkeys(stub["props"])],
            "summary": _fr_text(ec, stub["summary"]),
            "emotion": stub["emotion"], "target_duration_s": 0.0, "lines": [], "sfx_cues": [],
            "on_screen_text": None, "state": "stub", "source": "E1", "rev": 1,
        }
        lo, hi = timing.slot_range(scene, ec.template, ec.style_lock)
        scene["target_duration_s"] = round(min(max(float(stub["target_duration_s"]), lo), hi), 3)
        # Plan 23 stage D5: who wears an appearance variant in the scene (E1v3's states); nothing for none.
        states = {state["char_id"]: state["variant_id"] for state in stub.get("states") or ()
                  if state.get("char_id") in scene["characters"]}
        if states:
            scene["states"] = states
        # Plan 24 stage 5 (D-6): a narrated format's beat sheet says whether the scene carries a character line.
        if isinstance(stub.get("character_line"), bool):
            scene["character_line"] = stub["character_line"]
        # Phase 5 stage 3: the open hooks the scene pays off, verbatim (never
        # repaired: each must stay the exact text of a hook), each once;
        # nothing is stored for none.
        paid = list(dict.fromkeys(stub.get("pays_off") or []))
        if paid:
            scene["pays_off"] = paid
        scenes.append(scene)
    before, after = _normalize_episode_targets(ec, scenes)
    script["title"] = _fr_text(ec, reply["title"])
    script["scenes"] = scenes
    # Plan 22 stage 3: E1v3's spine (an E1/E1v2 reply has none, and leaves none).
    if isinstance(reply.get("spine"), dict):
        script["spine"] = {key: _fr_text(ec, reply["spine"][key]) for key in schemas.SPINE_KEYS}
        # Plan 24 stage 1 (D-2): a v3 beat sheet's scenes carry their line plan.
        store_line_plans(ec, script)
    else:
        script.pop("spine", None)
    return before, after


def episode_open_hooks(ec) -> list:
    """The hooks open when episode ``ec.ep`` starts: the fold of the memory
    entries of the episodes before it (``series_memory.open_hooks_before``)
    -- never the stored ``open_hooks``, which folds later episodes' entries
    too. [] for episode 1, and for a season with no memory entry."""
    return series_memory.open_hooks_before(ec.season, ec.ep)


def audience_direction(ec):
    """The audience direction chosen on the previous episode's feedback
    (``series_memory.chosen_direction``), which steers this episode's E1;
    None for episode 1 or when none was chosen."""
    return series_memory.chosen_direction(ec.season, ec.ep - 1) if ec.ep >= 2 else None


def e1_cast(ec) -> list:
    """The characters E1 may put in a scene: the story's approved ones, in
    cast order (plan 11 stage 4). A character not approved yet -- a guest or
    recurring one an accepted N1 proposal added, whose text and sheets may
    not even exist -- is left out until it is; a ``ready`` story always has
    its leads and support approved."""
    return [doc for doc in ec.cast if doc.get("approved_at")]


def e1_variants(ec, cast) -> list:
    """The appearance variants E1v3's character states block offers (plan 23
    stage D5): the *cast*'s approved variants, ``[{char_id, name, variants:
    [{variant_id, label}]}]``, on a story whose characters may carry them
    (``media_policy.variants_enabled``); [] otherwise -- the block and the
    scenes' ``states`` are then not asked at all (E1v3 byte-identical)."""
    if not media_policy.variants_enabled(ec.story):
        return []
    out = []
    for doc in cast:
        approved = [{"variant_id": variant["variant_id"], "label": variant["label"]}
                    for variant in doc.get("variants") or () if variant.get("approved_at")]
        if approved:
            out.append({"char_id": doc["char_id"], "name": doc["name"], "variants": approved})
    return out


def write_beat_sheet(ctx, ec, script, *, tools, announced) -> None:
    """E1 into *script* (in place; the caller writes it), offered the
    approved characters (:func:`e1_cast`). From episode 2 on,
    E1 is handed the hooks open when the episode starts (:func:`episode_open_hooks`)
    to pay off and the chosen audience direction (:func:`audience_direction`);
    with a hook offered, the call's cap is the payoff variant's
    (``prompts.E1_PAYOFF_MAX_TOKENS``), else the registry's.

    Phase 7 stage 3c (A11): ``media_policy.is_v2(ec.story)`` is resolved
    once and passed to every E1 call (``build_e1``, ``validate_e1``,
    ``_repair_e1_reply``), so they all agree on whether ``new_objects`` is
    offered this call."""
    pack = _pack(ec, ctx, announced)
    slots = beat_sheet_slots(ec)
    hooks = episode_open_hooks(ec)
    cast = e1_cast(ec)
    v2 = media_policy.is_v2(ec.story)
    new_objects_offered = prompts.offers_new_objects(ec.ep, v2)
    kwargs = dict(
        ep=ec.ep, arc_entry=ec.arc_entry, template=ec.template, episode_defaults=ec.episode_defaults,
        cast=[{"char_id": doc["char_id"], "name": doc["name"]} for doc in cast],
        places=[{"place_id": pid, "name": ec.entities["places"][pid]["name"], "time_variants": variants}
                for pid, variants in ec.places.items()],
        props=[{"prop_id": pid, "name": ec.entities["props"][pid]["name"]} for pid in ec.prop_ids],
        memory=ec.season, slots=slots, open_hooks=hooks, audience_direction=audience_direction(ec),
    )
    v3 = writes_v3(ec)  # a new beat sheet: the story's own choice
    variants = e1_variants(ec, cast) if v3 else None
    if v3:
        # Plan 22 stage 3: E1v3 -- the spine first, cause-and-effect summaries.
        prompt_id = "E1v3"
        slice_text = context.slice_for_episode(ec, knowledge=knowledge_of(ec),
                                               char_ids=[doc["char_id"] for doc in cast])
        # Plan 32 stage 2 (DEC-315): a story made with a recipe adds its beats; None on any other story.
        system, user, schema = prompts.build_e1_v3(
            pack, slice_text=slice_text, narration=prompts.narration_of(ec.template, ec.narrator),
            variants=variants, recipe=recipes.for_story(ec.story), **kwargs)
    elif v2:
        # Phase 7 stage 5c (A13): E1v2, with the episode's slice of the knowledge base.
        prompt_id = "E1v2"
        slice_text = context.slice_for_episode(ec, knowledge=knowledge_of(ec),
                                               char_ids=[doc["char_id"] for doc in cast])
        # Plan 20 stage 1: a narrated template, the narrator on, adds its ask line.
        system, user, schema = prompts.build_e1_v2(
            pack, slice_text=slice_text, narration=prompts.narration_of(ec.template, ec.narrator), **kwargs)
    else:
        prompt_id = "E1"
        system, user, schema = prompts.build_e1(pack, v2=v2, **kwargs)
    llm_call.announce_trimmed(ctx, pack, announced)

    def validate(reply):
        _repair_e1_reply(ec, reply, new_objects_offered=new_objects_offered)
        if v3:
            errors = prompts.validate_e1_v3(reply, ep=ec.ep, template=ec.template,
                                            episode_defaults=ec.episode_defaults,
                                            cast_ids=[doc["char_id"] for doc in cast], places=ec.places,
                                            prop_ids=ec.prop_ids, open_hooks=hooks, variants=variants,
                                            narration=prompts.narration_of(ec.template, ec.narrator))
        else:
            errors = prompts.validate_e1(reply, ep=ec.ep, template=ec.template, episode_defaults=ec.episode_defaults,
                                         cast_ids=[doc["char_id"] for doc in cast], places=ec.places,
                                         prop_ids=ec.prop_ids, open_hooks=hooks, v2=v2)
        if errors:
            return errors
        trial = copy.deepcopy(script)
        apply_e1(ec, trial, reply)
        return episode_common.trial_errors(ec, trial)

    # The payoff ask (episode 2 on, a hook offered) has a larger reply, and
    # its own measured cap; every other E1 call keeps the registry's.
    payoff_cap = (prompts.E1V3_PAYOFF_MAX_TOKENS if v3 else prompts.E1V2_PAYOFF_MAX_TOKENS if v2
                  else prompts.E1_PAYOFF_MAX_TOKENS)
    cap = payoff_cap if prompts.offered_hooks(ec.ep, hooks) else None
    if variants:
        # Plan 23 stage D5: the states' room (E1V3_STATES_MAX_TOKENS) on E1v3's cap fits under the payoff
        # variant's (2,570 + 560 <= 3,190), the widest E1v3 call the worst-case booking already prices.
        cap = prompts.E1V3_PAYOFF_MAX_TOKENS
    reply = llm_call.call_json(ctx, prompt_id, system, user, schema, validator=validate, runner=tools.runner,
                               time_fn=tools.time_fn, max_tokens=cap)
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


def _trim_scene(ctx, ec, scene, *, tools, pack, prompt_id, schema, validate, attempt, plan) -> dict:
    """The trimmed reply of body scene *scene* (plan 24 stage 3, D-4): one call
    on the writer's own chain (:func:`llm_call.call_json`, one validated try,
    the same *validate* -- the hard caps -- judging it) asking the last reply
    back with only the lines its word-cap errors name rewritten. The reply
    inside its caps is returned; one still over, or no trim call left in the
    episode (:data:`TRIM_CALLS_MAX`), fails the scene with the one plain
    sentence of :func:`over_cap_failure`. A call that cannot be made at all
    (the chain, a budget) raises as any call of the step does."""
    sid = scene["scene_id"]
    before, over = attempt["reply"], over_cap_errors(attempt["errors"])
    names = ec.names
    many = sum(1 for error in over if (prompts.parse_word_cap_error(error) or {}).get("index") is not None) > 1
    short = _short_of_range(over)
    if trim_calls_left(ec) <= 0:
        raise over_cap_failure(sid, _cap_clause(over, before, names), trimmed=False, many=many,
                               short=short) from None
    system, user = prompts.trim_lines_prompt(pack, scene=sid, reply=before, errors=over, names=names, plan=plan)
    ec.trim_calls = getattr(ec, "trim_calls", 0) + 1
    ctx.on_log(f"✂️ Scene {sid}: still over its caps after the retry; one trim call "
               f"({ec.trim_calls} of {TRIM_CALLS_MAX} this episode).")
    try:
        reply = llm_call.call_json(ctx, prompt_id, system, user, schema, validator=validate, runner=tools.runner,
                                   time_fn=tools.time_fn, single_try=True)
    except llm_call.ReplyRejected:
        again = over_cap_errors(attempt["errors"])
        shown, source = (again, attempt["reply"]) if again else (over, before)
        raise over_cap_failure(sid, _cap_clause(shown, source, names), trimmed=True, many=many,
                               short=_short_of_range(shown)) from None
    rows = reply["lines"]
    items = [prompts.parse_word_cap_error(error) for error in over]
    for item in (item for item in items if item["index"] is not None and item["index"] < len(rows)):
        words = len(rows[item["index"]]["text"].split())
        try:
            where = f"line {schemas.line_id_for(sid, item['index'])}"
        except ValueError:
            where = f"line {item['index'] + 1}"
        if item["lo"] is not None:  # plan 27 stage 2: under its floor, lengthened
            ctx.on_log(f"✂️ Scene {sid}: lengthened {where} to {words} words ({item['lo']}–{item['cap']})")
        else:
            ctx.on_log(f"✂️ Scene {sid}: trimmed {where} to {words} words ({item['cap']} cap)")
    for item in (item for item in items if item["span"] is not None):
        first, last = item["span"]
        words = sum(len(row["text"].split()) for row in rows[first:last + 1])
        ctx.on_log(f"✂️ Scene {sid}: trimmed lines {first + 1}–{last + 1} to {words} words ({item['cap']} cap)")
    for item in (item for item in items if item["index"] is None and item["span"] is None
                 and not any(i["index"] is not None or i["span"] is not None for i in items)):
        words = sum(len(row["text"].split()) for row in rows)
        ctx.on_log(f"✂️ Scene {sid}: trimmed to {words} words in total ({item['cap']} cap)")
    return reply


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
    kwargs = dict(
        scene=scene, scene_number=script["scenes"].index(scene) + 1, outline=script["scenes"],
        previous=_previous_line(ec, script, scene), word_budget=budget, cast=cast,
        place={"place_id": place["place_id"], "name": place["name"], "layout_notes": place["layout_notes"] or ""},
        props=props, sfx_cues=ec.sfx_cues, narrator_enabled=ec.narrator,
        voice_direction=ec.style_lock["audio"]["voice_direction"], note=pack.note,
        # Plan 20 stage 1: a narrated template, the narrator on, adds its ask line.
        narration=prompts.narration_of(ec.template, ec.narrator),
    )
    v2 = media_policy.is_v2(ec.story)
    # Plan 22 stage 3: the narrator only in the template's narrator_slots
    # (none of them a body slot on the confrontation format).
    kwargs["narrator_enabled"] = narrator_in(ec, scene)
    # Phase 7 stage 6a (DEC-231): a v2 reply may not repeat a line the episode
    # already has (its other scenes'); a v1 call is validated as before.
    episode_lines = ([line["text"] for other in script["scenes"] if other["scene_id"] != sid
                      for line in other["lines"]] if v2 else None)
    v3 = writes_v3(ec, script)
    if v3:
        # Plan 22 stage 3: E2v3 -- every line so far, the spine, this scene's
        # and the next scene's summary, the line rule.
        prompt_id = "E2v3"
        # Plan 24 stage 2 (D-3): the scene's line plan, recomputed now, is
        # what the writer is told and what the reply is held to.
        native = media_policy.native_speech(ec.story)
        plan3 = current_plan(ec, script, scene)
        budget3 = timing.plan_budget(plan3, line_lo=timing.line_words_v3(ec.template, native=native)[0])
        index = script["scenes"].index(scene)
        next_scene = script["scenes"][index + 1] if index + 1 < len(script["scenes"]) else None
        system, user, schema = prompts.build_e2_v3(
            pack, scene=scene, outline=script["scenes"], next_scene=next_scene, so_far=_so_far(ec, script, scene),
            spine=script.get("spine"), budget=budget3, cast=cast, place=kwargs["place"], props=props,
            sfx_cues=ec.sfx_cues, narrator_enabled=kwargs["narrator_enabled"],
            voice_direction=kwargs["voice_direction"], note=pack.note, narration=kwargs["narration"],
            slice_text=context.slice_for_scene(ec, scene, knowledge=knowledge_of(ec)),
            native=native, plan=plan3)
    elif v2:
        # Phase 7 stage 5c (A13): E2v2, with the scene's slice of the knowledge base.
        prompt_id = "E2v2"
        system, user, schema = prompts.build_e2_v2(
            pack, slice_text=context.slice_for_scene(ec, scene, knowledge=knowledge_of(ec)), **kwargs)
    else:
        prompt_id = "E2"
        system, user, schema = prompts.build_e2(pack, **kwargs)

    # A reply under half the word budget, or over 1.5x it, is retryable
    # (validate_e2; never both at once -- the floor sits below the ceiling
    # for every budget, so they never stack). If the retry is *still* only
    # off on its word count, the existing "fail after two attempts" path
    # would leave this whole scene a stub over a borderline word count.
    # Instead, the second attempt's word-count error alone (nothing else
    # wrong with the reply) is accepted with a log line (spec 4.2, F3; the
    # human's own choice) -- a real problem (a bad speaker, an over-cap
    # line) still fails the scene exactly as before.
    #
    # Plan 24 stage 2 (D-4, amends DEC-143 on writing v3): the leniency is
    # for UNDER-length replies only -- a v3 reply over its plan's caps is
    # never accepted; once the ladder is spent the scene fails as any broken
    # reply does (stage 3's trim pass hooks in before that, below).
    word_count_prefixes = ((prompts.E2_WORD_FLOOR_PREFIX,) if v3 else
                           (prompts.E2_WORD_FLOOR_PREFIX, prompts.E2_WORD_CEILING_PREFIX))
    attempt = {"n": 0, "reply": None, "errors": []}

    def validate(reply):
        attempt["n"] += 1
        _repair_e2_reply(ec, reply)
        extra = {} if episode_lines is None else {"episode_lines": episode_lines}
        if v3:
            errors = prompts.validate_e2_v3(reply, scene=scene, narrator_enabled=kwargs["narrator_enabled"],
                                            sfx_cues=ec.sfx_cues, budget=budget3,
                                            floor=prompts.line_floor_v3(ec.template), episode_lines=episode_lines,
                                            plan=plan3)
        else:
            errors = prompts.validate_e2(reply, scene=scene, narrator_enabled=kwargs["narrator_enabled"],
                                         sfx_cues=ec.sfx_cues, word_budget=budget, **extra)
        attempt["reply"], attempt["errors"] = reply, list(errors)
        word_count_only = bool(errors) and all(e.startswith(word_count_prefixes) for e in errors)
        if errors and not (word_count_only and attempt["n"] >= 2):
            return errors
        if word_count_only and attempt["n"] >= 2:
            ctx.on_log(f"⚠️ Scene {sid}: accepting a reply after a retry despite its word count ({errors[0]}).")
        trial = copy.deepcopy(script)
        apply_e2(ec, scene_of(trial, sid), reply)
        return episode_common.trial_errors(ec, trial)

    try:
        reply = llm_call.call_json(ctx, prompt_id, system, user, schema, validator=validate, runner=tools.runner,
                                   time_fn=tools.time_fn)
    except llm_call.ReplyRejected:
        over = over_cap_errors(attempt["errors"]) if v3 else []
        if not over:
            raise
        if over != attempt["errors"]:
            # A real problem rides with the cap errors: nothing for a trim to fix alone.
            ctx.on_log(f"✖ Scene {sid}: still over its caps after every try ({over[0]}).")
            raise
        # Plan 24 stage 3 (D-4): the retry ladder is spent and only word caps
        # stand in the way -- one bounded trim call rewrites the lines they name.
        reply = _trim_scene(ctx, ec, scene, tools=tools, pack=pack, prompt_id=prompt_id, schema=schema,
                            validate=validate, attempt=attempt, plan=plan3)
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


def _trim_framing(ctx, ec, parts, *, tools, pack, prompt_id, schema, validate, attempt) -> dict:
    """:func:`_trim_scene` for an E3v3 reply: the framing parts (*parts*,
    ``{part: scene}``) whose lines stay over their plan's total are rewritten
    by one trim call; the sentence of :func:`framing_over_cap_failure` when
    that is still not enough or the episode has no trim call left."""
    before, over = attempt["reply"], over_cap_errors(attempt["errors"])
    names = ec.names
    scenes = {key: scene["scene_id"] for key, scene in parts.items()}
    if trim_calls_left(ec) <= 0:
        raise framing_over_cap_failure(_cap_clause(over, before, names, scenes=scenes), trimmed=False,
                                       short=_short_of_range(over)) from None
    system, user = prompts.trim_lines_prompt(pack, scene=scenes, reply=before, errors=over, names=names)
    ec.trim_calls = getattr(ec, "trim_calls", 0) + 1
    ctx.on_log(f"✂️ Framing scenes: still over their caps after the retry; one trim call "
               f"({ec.trim_calls} of {TRIM_CALLS_MAX} this episode).")
    try:
        reply = llm_call.call_json(ctx, prompt_id, system, user, schema, validator=validate, runner=tools.runner,
                                   time_fn=tools.time_fn, single_try=True)
    except llm_call.ReplyRejected:
        again = over_cap_errors(attempt["errors"]) or over
        raise framing_over_cap_failure(_cap_clause(again, attempt["reply"], names, scenes=scenes),
                                       trimmed=True, short=_short_of_range(again)) from None
    for error in over:
        item = prompts.parse_word_cap_error(error)
        key = item["part"]
        if key in reply and isinstance(reply[key], dict):
            words = sum(len(line["text"].split()) for line in reply[key]["lines"])
            if item["lo"] is not None:  # plan 27 stage 2: under its floor, lengthened
                ctx.on_log(f"✂️ The {key} (scene {scenes.get(key, '?')}): lengthened to {words} words in total "
                           f"({item['lo']}–{item['cap']})")
            else:
                ctx.on_log(f"✂️ The {key} (scene {scenes.get(key, '?')}): trimmed to {words} words in total "
                           f"({item['cap']} cap)")
    return reply


def write_framing(ctx, ec, script, part, *, tools, announced, note=None) -> list:
    """E3 into *script* (in place; the caller writes it): every framing
    part when *part* is None, else that one part alone -- the recap (episode
    2 on) written from the previous recap, with the hooks open when the
    episode starts (:func:`episode_open_hooks`). Returns the ids of the scenes it
    rewrote."""
    keys = e3_parts(ec.ep) if part is None else [part]
    hook, cliff, recap = (framing_scene(script, name) for name in ("hook", "cliffhanger", "recap"))
    speaking = []
    for key, scene in (("hook", hook), ("cliffhanger", cliff), ("recap", recap)):
        if key in keys and scene is not None:
            speaking.extend(cid for cid in scene["characters"] if cid not in speaking)
    budgets = {key: _word_budget(ec, scene) for key, scene in (("hook", hook), ("cliffhanger", cliff),
                                                                  ("recap", recap)) if scene is not None}
    pack = _pack(ec, ctx, announced, note=note)
    kwargs = dict(
        ep=ec.ep, part=part, note=pack.note, hook_scene=hook, cliffhanger_scene=cliff, recap_scene=recap,
        outline=script["scenes"], first_body_line=_body_line(ec, script, first=True),
        last_body_line=_body_line(ec, script, first=False), arc_entry=ec.arc_entry,
        next_arc_entry=ec.next_arc_entry, memory=ec.season, episode_defaults=ec.episode_defaults,
        word_budgets=budgets, cast=_cast_lines(ec, speaking, (hook or cliff or {}).get("scene_id")),
        narrator_enabled=ec.narrator, open_hooks=episode_open_hooks(ec),
    )
    v2 = media_policy.is_v2(ec.story)
    # Plan 22 stage 3: with narrator_slots (the confrontation format) the
    # narrator is offered only when a part this call writes is one of them,
    # and refused in the others (narrator_errors).
    parts = {key: scene for key, scene in (("hook", hook), ("cliffhanger", cliff), ("recap", recap))
             if key in keys and scene is not None}
    if "narrator_slots" in ec.template:
        kwargs["narrator_enabled"] = any(narrator_in(ec, scene) for scene in parts.values())
    # Phase 7 stage 6a (DEC-231): a v2 reply needs the hook's on-screen text and
    # may not repeat a line of the scenes this call does not rewrite.
    rewritten = {scene["scene_id"] for scene in parts.values()}
    v2_checks = ({"v2": True, "episode_lines": [line["text"] for scene in script["scenes"]
                                                 if scene["scene_id"] not in rewritten for line in scene["lines"]]}
                 if v2 else {})
    v3 = writes_v3(ec, script)
    if v3:
        # Plan 22 stage 3: E3v3 -- E3v2, the spine, the lines so far, the line rule.
        prompt_id = "E3v3"
        native = media_policy.native_speech(ec.story)
        line_words = timing.line_words_v3(ec.template, native=native)
        sliced = _framing_slice_scene(script, part)
        slice_text = context.slice_for_scene(ec, sliced, knowledge=knowledge_of(ec)) if sliced else ""
        # Plan 24 stage 2 (D-3): each framing part's line plan, recomputed
        # now, sets its seconds and its hard total.
        plans3 = {key: current_plan(ec, script, scene) for key, scene in (("hook", hook), ("cliffhanger", cliff),
                                                                            ("recap", recap))
                  if scene is not None}
        kwargs["word_budgets"] = {key: plan["max_words"] for key, plan in plans3.items()}
        narrator_parts = ([key for key, scene in parts.items() if narrator_in(ec, scene)]
                          if "narrator_slots" in ec.template else None)
        system, user, schema = prompts.build_e3_v3(
            pack, slice_text=slice_text, so_far=_so_far(ec, script, skip=rewritten), spine=script.get("spine"),
            line_words=line_words, single_place=bool(ec.template.get("single_place")), native=native,
            narrator_parts=narrator_parts, plans=plans3, recipe=recipes.for_story(ec.story), **kwargs)
    elif v2:
        # Phase 7 stage 5c (A13): E3v2, with the slice of the scene it mostly writes.
        prompt_id = "E3v2"
        sliced = _framing_slice_scene(script, part)
        slice_text = context.slice_for_scene(ec, sliced, knowledge=knowledge_of(ec)) if sliced else ""
        system, user, schema = prompts.build_e3_v2(pack, slice_text=slice_text, **kwargs)
    else:
        prompt_id = "E3"
        system, user, schema = prompts.build_e3(pack, **kwargs)
    llm_call.announce_trimmed(ctx, pack, announced)

    attempt = {"reply": None, "errors": []}

    def validate(reply):
        _repair_e3_reply(ec, reply)
        if v3:
            errors = prompts.validate_e3_v3(reply, ep=ec.ep, part=part, hook_scene=hook, cliffhanger_scene=cliff,
                                            recap_scene=recap, narrator_enabled=kwargs["narrator_enabled"],
                                            episode_defaults=ec.episode_defaults, line_words=line_words,
                                            floor=prompts.line_floor_v3(ec.template),
                                            single_place=bool(ec.template.get("single_place")),
                                            episode_lines=v2_checks["episode_lines"], plans=plans3)
        else:
            errors = prompts.validate_e3(reply, ep=ec.ep, part=part, hook_scene=hook, cliffhanger_scene=cliff,
                                         recap_scene=recap, narrator_enabled=kwargs["narrator_enabled"],
                                         episode_defaults=ec.episode_defaults, **v2_checks)
        if "narrator_slots" in ec.template:
            errors = list(errors) + narrator_errors(ec, reply, parts)
        attempt["reply"], attempt["errors"] = reply, list(errors)
        if errors:
            return errors
        trial = copy.deepcopy(script)
        apply_e3(ec, trial, reply)
        return episode_common.trial_errors(ec, trial)

    try:
        reply = llm_call.call_json(ctx, prompt_id, system, user, schema, validator=validate, runner=tools.runner,
                                   time_fn=tools.time_fn)
    except llm_call.ReplyRejected:
        over = over_cap_errors(attempt["errors"]) if v3 else []
        if not over or over != attempt["errors"]:
            raise
        # Plan 24 stage 3 (D-4): the same bounded trim pass as a body scene's.
        reply = _trim_framing(ctx, ec, parts, tools=tools, pack=pack, prompt_id=prompt_id, schema=schema,
                              validate=validate, attempt=attempt)
    return apply_e3(ec, script, reply)


# -------------------------------------------------------------------- E4

# A consistency issue's fix is stored at most this long (episode_script_v1).
_FIX_MAX_CHARS = 300


def scene_payoffs(script, open_hooks) -> dict:
    """``{hook: [scene_id, ...]}``: each of *open_hooks* some scene of
    *script* says it pays off (``pays_off``), with those scenes, in script
    order -- what E4 is asked to judge the lines of. A named hook that is
    not open is left out: :func:`payoff_issues` reports it."""
    wanted = set(open_hooks)
    payoffs = {}
    for scene in script["scenes"]:
        for hook in scene.get("pays_off") or []:
            if hook in wanted:
                payoffs.setdefault(hook, []).append(scene["scene_id"])
    return payoffs


def _stray_fix(sid, stray, ep) -> str:
    if len(stray) == 1:
        return f"Scene {sid} pays off “{stray[0]}”, which is not open before episode {ep}."
    quoted = ", ".join(f"“{hook}”" for hook in stray)
    fix = f"Scene {sid} pays off {len(stray)} hooks that are not open before episode {ep}: {quoted}."
    if len(fix) <= _FIX_MAX_CHARS:
        return fix
    return f"Scene {sid} pays off {len(stray)} hooks that are not open before episode {ep}, the first “{stray[0]}”."


def payoff_issues(script, ep, open_hooks) -> list:
    """The hook-payoff pre-check (plan 11 stage 3, DEC-177): deterministic,
    no call, run before E4 on every consistency check. Its findings are
    ``hook_payoff`` issues for the consistency report, [] when it passes:

    - with a hook open when episode *ep* starts (*open_hooks*,
      :func:`episode_open_hooks`), at least one body scene pays one off --
      one issue for the episode (``scene_id`` null) when none does;
    - every hook a scene names in ``pays_off`` is one of *open_hooks*,
      verbatim -- one issue per scene naming any that is not (the memory of
      an earlier episode written again since E1 ran, say).

    A named scene always exists: ``pays_off`` lives on its scene. The fixes
    are the app's own text (English, like its other messages), each within
    the stored report's 300 characters; at most one issue per scene plus one,
    so with E4's own six a report stays inside its 20. Pure."""
    wanted = set(open_hooks)
    issues = []
    paid = any(hook in wanted for scene in body_scenes(script) for hook in scene.get("pays_off") or [])
    if open_hooks and not paid:
        count = len(open_hooks)
        issues.append({"scene_id": None, "kind": "hook_payoff",
                       "fix": f"No body scene pays off one of the {count} hook{'s' if count != 1 else ''} open "
                              f"before episode {ep}."})
    for scene in script["scenes"]:
        stray = [hook for hook in scene.get("pays_off") or [] if hook not in wanted]
        if stray:
            issues.append({"scene_id": scene["scene_id"], "kind": "hook_payoff",
                           "fix": _stray_fix(scene["scene_id"], stray, ep)})
    return issues


def check_consistency(ctx, ec, script, *, tools, announced, now) -> dict:
    """E4 over the whole script; the report is set on *script* (in place;
    the caller writes it) and returned.

    Phase 5 (plan 11 stage 3): the hook-payoff pre-check
    (:func:`payoff_issues`) runs first; its issues lead the report and fail
    it whatever E4 says, and E4 still runs so the rest is checked. E4 reads
    the hooks open when the episode starts and the recaps of the episodes
    before it and, when scenes pay some off, judges their lines (the
    ``hook_payoff`` kind, :func:`scene_payoffs`). Episode 1 has none of it:
    its E4 is what it was."""
    hooks = episode_open_hooks(ec)
    payoffs = scene_payoffs(script, hooks)
    pre = payoff_issues(script, ec.ep, hooks)
    if pre:
        ctx.on_log(f"🪝 Hook payoff check: {len(pre)} issue{'s' if len(pre) != 1 else ''}, added to the "
                   "consistency report")
    elif hooks:
        scenes = sorted({sid for sids in payoffs.values() for sid in sids})
        ctx.on_log(f"🪝 Hook payoff check: passed ({_and(scenes)} pay{'s' if len(scenes) == 1 else ''} off "
                   "an open hook)")
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
        memory=ec.season, ep=ec.ep, open_hooks=hooks, payoffs=payoffs,
    )
    scene_ids = [scene["scene_id"] for scene in script["scenes"]]

    def report_of(reply, checked_at):
        return {
            "passed": reply["passed"] and not pre,
            "issues": copy.deepcopy(pre) + [
                {"scene_id": issue["scene_id"], "kind": issue["kind"], "fix": issue["fix"].strip()}
                for issue in reply["issues"]],
            "checked_rev": script["rev"], "checked_at": checked_at, "stale": False,
        }

    def validate(reply):
        errors = prompts.validate_e4(reply, scene_ids=scene_ids, hook_payoff=bool(payoffs))
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
    blocking = len(consistency_blocking_issues(report))
    tail = ", none blocking" if not blocking else f", {blocking} blocking"
    return f"🔍 Consistency: {count} issue{'s' if count != 1 else ''}{tail}"


# ------------------------------------- the repair pass (phase 7 follow-up, stage G)

# What the earlier scene is asked, per kind that needs one (module docstring).
_EARLIER_SCENE_ASKS = {"unintroduced": "say their name and who they are here",
                       "object_unseen": "show it on screen here"}
_REPEATED_LINE_TAIL = " Write a different line here that keeps the beat."


def _kind_words(kind) -> str:
    words = judge.KIND_WORDS[kind]
    return words[0].upper() + words[1:]


def _sentence(text) -> str:
    """*text* on one line, ending with a sentence mark (a fix is kept as it
    is, the story language included; only a missing final period is added)."""
    text = " ".join(str(text).split())
    return text if text.endswith((".", "!", "?", "…")) else text + "."


def _named_first(fix, names):
    """The id of *names* (``{id: name}``) whose name *fix* mentions first --
    as a whole word, case aside -- or None when it names none."""
    text = fix.casefold()
    found = []
    for eid, name in names.items():
        match = re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", text)
        if match:
            found.append((match.start(), eid))
    return min(found)[1] if found else None


def _named_entity(ec, scene, kind, fix):
    """``(key, id, name)`` of the character an ``unintroduced`` *fix* names,
    or the story prop an ``object_unseen`` one names -- *scene*'s own cast /
    props first (the fix usually names one of them), else any of the
    story's -- or None: it names none."""
    if kind == "unintroduced":
        key, names = "characters", dict(ec.names)
    else:
        key, names = "props", {pid: (doc or {}).get("name") or pid for pid, doc in ec.entities["props"].items()}
    own = {eid: names[eid] for eid in scene[key] if eid in names}
    eid = _named_first(fix, own) or _named_first(fix, names)
    return None if eid is None else (key, eid, names[eid])


def _earlier_scene(ec, script, scene, kind, fix):
    """``(scene_id, name)`` of the nearest body scene before *scene* where
    the character an ``unintroduced`` *fix* names is present, or the prop
    an ``object_unseen`` one names is listed (:func:`_named_entity`) -- or
    None: the fix names none, or no earlier body scene has them."""
    named = _named_entity(ec, scene, kind, fix)
    if named is None:
        return None
    key, eid, name = named
    index = script["scenes"].index(scene)
    for before in reversed(script["scenes"][:index]):
        if before["function"] in schemas.BODY_FUNCTIONS and eid in before[key]:
            return before["scene_id"], name
    return None


def _fitted_note(items, limit=REPAIR_NOTE_MAX_WORDS, head=REPAIR_NOTE_HEAD) -> str:
    """*head* (:data:`REPAIR_NOTE_HEAD`, or :data:`CONSISTENCY_NOTE_HEAD` for
    E4's issues, DEC-260) then each item ``(ask_before, fix,
    ask_after)`` joined with single spaces -- within *limit* words when it
    can be: the fixes (the judge's words) are shortened, never the asks (the
    app's), a short fix keeping all its words and the longer ones sharing
    what is left evenly, each cut one ending with "…". Pure."""
    fixes = [fix.split() for _before, fix, _after in items]
    asks = len(head.split()) + sum(len(before.split()) + len(after.split())
                                   for before, _fix, after in items)
    room = limit - asks
    if sum(len(words) for words in fixes) > room >= len(fixes):
        caps, left, count = {}, room, len(fixes)
        for index in sorted(range(len(fixes)), key=lambda i: len(fixes[i])):
            caps[index] = min(len(fixes[index]), left // count)
            left, count = left - caps[index], count - 1
        fixes = [words if len(words) <= caps[i] else words[:caps[i] - 1] + [words[caps[i] - 1] + "…"]
                 for i, words in enumerate(fixes)]
    return head + " ".join(" ".join(part for part in (before, " ".join(words), after) if part)
                                       for (before, _fix, after), words in zip(items, fixes))


def repair_plan(ec, script, issues) -> list:
    """The repairs for a first-watch report's *issues* (the module
    docstring's map; the step hands in the blocking ones), in scene order,
    each ``{scene_id, part, kinds, note, add_props}``: *part* the framing
    part the partial E3 writes when the scene is a framing one (None: E2);
    *kinds* the issue kinds that sent the scene, each once; *note* the
    author's note -- :data:`REPAIR_NOTE_HEAD`, then one item per issue: the
    kind in words and the fix as it is (the story language), a
    ``repeated_line`` asking for a different line that keeps the beat, an
    earlier scene's item saying which scene the issue is in and what to do
    here -- fitted to :data:`REPAIR_NOTE_MAX_WORDS` (:func:`_fitted_note`);
    *add_props* the story props an ``object_unseen`` fix names that the
    scene does not list yet, within :data:`SCENE_PROPS_MAX` (DEC-248: never
    an object that is not one of the story's props). Not repairable, left
    out (the issue stays in the report): an issue with no scene, one naming
    a scene the script no longer has, a body scene nobody can speak in.
    Pure."""
    work = {}

    def add(sid, kind, item, source=None):
        scene = scene_of(script, sid)
        part = scene["function"] if scene["function"] in FRAMING_FUNCTIONS else None
        if part is None and not can_speak(ec, scene):
            return None
        entry = work.setdefault(sid, {"part": part, "kinds": [], "items": [], "add_props": [], "sources": []})
        if kind not in entry["kinds"]:
            entry["kinds"].append(kind)
        entry["items"].append(item)
        if source not in entry["sources"]:
            entry["sources"].append(source)
        return entry

    for issue in issues:
        sid, kind = issue["scene_id"], issue["kind"]
        scene = None if sid is None else scene_of(script, sid)
        if scene is None:
            continue
        fix = _sentence(issue["fix"])
        source = issue.get("source")
        entry = add(sid, kind, (f"{_kind_words(kind)}:", fix,
                                _REPEATED_LINE_TAIL.strip() if kind == "repeated_line" else ""), source)
        if entry is not None and kind == "object_unseen":
            named = _named_entity(ec, scene, kind, issue["fix"])
            listed = scene["props"] + entry["add_props"]
            if named is not None and named[1] not in listed and len(listed) < SCENE_PROPS_MAX:
                entry["add_props"].append(named[1])
        if kind in _EARLIER_SCENE_ASKS:
            earlier = _earlier_scene(ec, script, scene, kind, issue["fix"])
            if earlier is not None:
                before_sid, name = earlier
                add(before_sid, kind, (f"{_kind_words(kind)} in scene {sid}:", fix,
                                       f"{name} is in this scene, before {sid}: {_EARLIER_SCENE_ASKS[kind]}."))
    order = {scene["scene_id"]: index for index, scene in enumerate(script["scenes"])}
    return [{"scene_id": sid, "part": entry["part"], "kinds": entry["kinds"],
             "note": _fitted_note(entry["items"], head=_note_head(entry["sources"])), "add_props": entry["add_props"]}
            for sid, entry in sorted(work.items(), key=lambda item: order[item[0]])]


def _note_head(sources) -> str:
    """The note's head for a scene's items: the first-watch check's, the
    consistency check's (DEC-260), or both when both checks sent the scene."""
    watch = any(source != CONSISTENCY_SOURCE for source in sources)  # a J1 issue carries no source
    check = CONSISTENCY_SOURCE in sources
    if check and not watch:
        return CONSISTENCY_NOTE_HEAD
    if check and watch:
        return REPAIR_NOTE_HEAD.rstrip(" -") + " and consistency check -- "
    return REPAIR_NOTE_HEAD


def consistency_issues(script) -> list:
    """The consistency report's blocking issues when it has not passed
    (DEC-260, DEC-261: never a minor one), each marked ``source:
    consistency`` for the repair's note; [] otherwise."""
    report = script.get("consistency_report")
    if not report or report.get("passed") or needs_check(script):
        return []
    return [dict(issue, source=CONSISTENCY_SOURCE) for issue in consistency_blocking_issues(report)]


def repair_issues(script, version=None) -> list:
    """What a repair pass works on (DEC-260): the first-watch report's
    blocking issues, then the consistency check's issues (*version*: the
    story's J1 version, ``judge.j1_version``)."""
    report = script.get(judge.FIRST_WATCH)
    watch = [] if (report is None or judge.needs_first_watch(script, version)) else judge.blocking_issues(report)
    return list(watch) + consistency_issues(script)


def repairable(ec, script) -> list:
    """The scene ids a repair pass would write now: a fresh first-watch
    report that found blocking issues on an unapproved, complete script,
    else []."""
    if script["approved_at"] or not is_complete(script, ec.ep):
        return []
    report = script.get(judge.FIRST_WATCH)
    version = judge.j1_version(ec.story, script)
    if report is None or judge.needs_first_watch(script, version):
        return []
    return [repair["scene_id"] for repair in repair_plan(ec, script, repair_issues(script, version))]


def issues_line(script, issues) -> str:
    """``s01 no hook text, s02 unclear goal, the episode unmotivated``:
    *issues* in scene order, the ones with no scene last."""
    order = {scene["scene_id"]: index for index, scene in enumerate(script["scenes"])}
    issues = sorted(issues, key=lambda issue: (issue["scene_id"] is None, order.get(issue["scene_id"], 0)))
    return ", ".join(f"{issue['scene_id'] or 'the episode'} {judge.KIND_WORDS[issue['kind']]}" for issue in issues)


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
        # Phase 7 stage 6a: the v2 fill pass's record (None: it did not run).
        self.filled = None
        # Phase 7 follow-up, stage G: the v2 repair passes' records (None: none ran).
        self.repairs = None

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
        v2 = media_policy.is_v2(self.ec.story)
        repairs = repairable(self.ec, script) if v2 and not stubs and not missing else []
        if repairs:
            parts.append(f"the first-watch repairs of {_and(repairs)}")
        if stubs or missing or needs_check(script) or repairs:
            parts.append("the consistency check (E4)")
        unjudged = judge.needs_first_watch(script, judge.j1_version(self.ec.story, script))
        if v2 and (stubs or missing or unjudged or repairs):
            parts.append("the first-watch check (J1)")
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
        # Plan 28 stage A1: even the cheapest shape of this template on this story's links cannot fit its
        # window: refused before the first call is paid.
        self.refuse_unfit_plan(None)
        self.before_call()
        self.ctx.on_log(f"🎬 Episode {ec.ep}: beat sheet (E1)")
        write_beat_sheet(self.ctx, ec, self.script, tools=self.tools, announced=self.announced)
        self.calls += 1
        self.save()
        # ... and the beat sheet's own plans, right after it is kept: nothing else is written for them.
        self.refuse_unfit_plan(self.script)
        body = len(body_scenes(self.script))
        self.ctx.on_log(f"🎬 Episode {ec.ep}: “{self.script['title']}” — {len(self.script['scenes'])} scenes, "
                        f"{body} body")

    def refuse_unfit_plan(self, script) -> None:
        """:func:`plan_fit_refusal` as a refusal (plan 28 stage A1): *script*
        None asks the zero-call preview, a beat-sheeted script its own plans."""
        sentence = plan_fit_refusal(self.ec, script, log=self.ctx.on_log)
        if sentence:
            raise StepFailed(sentence)

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

    def fill(self) -> None:
        """The v2 fill pass (module docstring): a complete, unapproved v2
        script whose estimate is under the window gets E2v2 again on its
        shortest body scenes -- each raised to its slot's top first, so its
        word budget grows -- at most :data:`FILL_CALLS_MAX` calls, stopping
        once the estimate is inside. A failed call keeps the scene as it was.
        ``self.filled`` records it: ``{before_s, after_s, window_s, scenes,
        failed}``."""
        ec, script = self.ec, self.script
        if not media_policy.is_v2(ec.story) or script["approved_at"] or not is_complete(script, ec.ep):
            return
        result = script.get("timing") or {}
        if result.get("state") != "under":
            return
        lo, hi = result["window_s"]
        durations = result["scenes"]
        order = {scene["scene_id"]: index for index, scene in enumerate(script["scenes"])}
        candidates = sorted((scene for scene in body_scenes(script) if can_speak(ec, scene)),
                            key=lambda scene: (durations[scene["scene_id"]]["duration_s"], order[scene["scene_id"]]))
        candidates = candidates[:FILL_CALLS_MAX]
        if not candidates:
            return
        before = result["total_s"]
        self.filled = {"before_s": before, "after_s": before, "window_s": [lo, hi], "scenes": [], "failed": []}
        self.ctx.on_log(f"⏱ Fill pass: {before:.1f} s estimated is under {lo:g}–{hi:g} s; writing the shortest "
                        f"scene{'s' if len(candidates) > 1 else ''} again "
                        f"({_and(scene['scene_id'] for scene in candidates)}, E2)")
        for scene in candidates:
            if (script.get("timing") or {}).get("state") != "under":
                break
            sid = scene["scene_id"]
            self.before_call()
            target = scene["target_duration_s"]
            scene["target_duration_s"] = timing.slot_range(scene, ec.template, ec.style_lock)[1]
            try:
                write_body_scene(self.ctx, ec, script, sid, tools=self.tools, announced=self.announced,
                                 note=FILL_NOTE_V3 if writes_v3(ec, script) else FILL_NOTE)
            except StepFailed as exc:
                scene["target_duration_s"] = target
                self.calls += 1
                self.filled["failed"].append(sid)
                self.ctx.on_log(f"✖ Fill pass: scene {sid} failed ({exc.reason}); it keeps its lines")
                continue
            self.calls += 1
            if script.get("consistency_report") is not None or script.get(judge.FIRST_WATCH) is not None:
                # A script checked already: rewritten like a regenerate (its
                # checks go stale; the storyboard, written first, too).
                now = llm_call.utc_now()
                episode_common.mark_changed(script, self.storyboard, scene_ids=[sid], now=now)
                if self.storyboard is not None:
                    episode_common.write_storyboard(ec, self.storyboard, script, now=now)
            self.save()
            self.filled["scenes"].append(sid)
        after = script["timing"]["total_s"]
        self.filled["after_s"] = after
        where = {"ok": "inside", "tightened": "inside", "over": "over", "under": "still under"}[
            script["timing"]["state"]]
        self.ctx.on_log(f"⏱ Fill pass: {len(self.filled['scenes'])} scene"
                        f"{'s' if len(self.filled['scenes']) != 1 else ''} rewritten, {before:.1f} s → {after:.1f} s "
                        f"estimated — {where} {lo:g}–{hi:g} s")

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

    def first_watch(self, previous=None) -> None:
        """J1 on a complete v2 script whose first-watch report is missing,
        stale, of an older revision or of an older J1 (``steps/judge``); a
        failed call is a failure of the step, like E4's. *previous*: the
        blocking issues a repair pass just tried (a re-check, DEC-248)."""
        ec, script = self.ec, self.script
        if (not media_policy.is_v2(ec.story) or not is_complete(script, ec.ep)
                or not judge.needs_first_watch(script, judge.j1_version(ec.story, script))):
            return
        self.before_call()
        self.ctx.on_log("👀 First-watch check (J1)")
        try:
            report = judge.check_first_watch(self.ctx, ec, script, tools=self.tools,
                                             pack=_pack(ec, self.ctx, self.announced), previous=previous)
        except BudgetSpent:
            raise
        except StepFailed as exc:
            self.calls += 1
            self.failed.append(("the first-watch check", None, exc.reason))
            self.ctx.on_log(f"✖ The first-watch check failed: {exc.reason}")
            return
        self.calls += 1
        self.save()
        self.ctx.on_log(judge.first_watch_line(report))

    def repair(self) -> None:
        """The v2 repair pass (module docstring): a complete, unapproved v2
        script whose fresh first-watch report found issues gets the scenes
        they name written again (:func:`repair_plan`: E2 for a body scene,
        the partial E3 for a framing one, the fix as the author's note; the
        slot target kept, as a regenerate keeps it), then the fill pass if
        the length left the window, E4 and J1 again -- at most
        :data:`REPAIR_PASSES_MAX` passes, each at most :data:`REPAIR_CALLS_MAX`
        calls in scene order. The loop ends when J1 passes, a pass repaired
        nothing, the passes are spent, or the budget is (``before_call``, as
        the fill pass: the step ends failed and a run continues). A failed
        call keeps the scene as it was (the props it listed for the call
        too); a rewrite goes through ``episode_common.mark_changed`` like a
        regenerate. ``self.repairs`` -- the script's ``repairs`` too,
        replaced by this run's -- records each pass: ``{pass, scenes:
        [{scene_id, kinds, part, props_added?}], issues_before,
        issues_after, failed}``, the issues counted the blocking ones (every
        issue of a version-1 report). DEC-248: only the blocking issues are
        repaired, and the J1 after a pass is their re-check."""
        ec, script = self.ec, self.script
        if not media_policy.is_v2(ec.story) or script["approved_at"] or not is_complete(script, ec.ep):
            return
        for number in range(1, REPAIR_PASSES_MAX + 1):
            report = script.get(judge.FIRST_WATCH)
            version = judge.j1_version(ec.story, script)
            if report is None or judge.needs_first_watch(script, version):
                return
            # DEC-260: J1's blocking issues and E4's issues, repaired together.
            blocking = repair_issues(script, version)
            if not blocking:
                return
            plan = repair_plan(ec, script, blocking)
            if not plan:
                return
            if self.repairs is None:
                self.repairs = script[judge.REPAIRS] = []
            record = {"pass": number, "scenes": [], "issues_before": len(blocking), "issues_after": None,
                      "failed": []}
            self.repairs.append(record)
            watch_count = len(blocking) - sum(1 for issue in blocking if issue.get("source") == CONSISTENCY_SOURCE)
            found = (f"{len(blocking)} blocking issue{'s' if len(blocking) != 1 else ''}" if watch_count == len(blocking)
                     else f"{len(blocking)} issue{'s' if len(blocking) != 1 else ''}")
            todo = plan[:REPAIR_CALLS_MAX]
            self.ctx.on_log(f"🩹 Repair pass {number}: {found} ({issues_line(script, blocking)}); writing "
                            f"{_and(repair['scene_id'] for repair in todo)} again")
            for repair in todo:
                sid, part = repair["scene_id"], repair["part"]
                self.before_call()
                # An object the story turns on, listed on the scene for its rewrite (and its keyframes).
                scene = scene_of(script, sid)
                added = [pid for pid in repair["add_props"] if pid not in scene["props"]]
                scene["props"].extend(added)
                try:
                    if part is None:
                        write_body_scene(self.ctx, ec, script, sid, tools=self.tools, announced=self.announced,
                                         note=repair["note"])
                        touched = [sid]
                    else:
                        touched = write_framing(self.ctx, ec, script, part, tools=self.tools,
                                                announced=self.announced, note=repair["note"])
                except BudgetSpent:
                    raise
                except StepFailed as exc:
                    self.calls += 1
                    scene["props"] = [pid for pid in scene["props"] if pid not in added]
                    record["failed"].append(sid)
                    self.ctx.on_log(f"✖ Repair pass {number}: scene {sid} failed ({exc.reason}); it keeps its lines")
                    continue
                self.calls += 1
                # Rewritten like a regenerate: the revisions move, the checks go stale, the storyboard too.
                now = llm_call.utc_now()
                episode_common.mark_changed(script, self.storyboard, scene_ids=touched, now=now)
                if self.storyboard is not None:
                    episode_common.write_storyboard(ec, self.storyboard, script, now=now)
                self.save()
                entry = {"scene_id": sid, "kinds": list(repair["kinds"]), "part": part}
                if added:
                    entry["props_added"] = added
                    self.ctx.on_log(f"🩹 Scene {sid} now shows {_and(self._prop_name(pid) for pid in added)}")
                record["scenes"].append(entry)
            count = len(record["scenes"])
            self.ctx.on_log(f"🩹 Repair pass {number}: {count} scene{'s' if count != 1 else ''} rewritten for "
                            f"{found} ({issues_line(script, blocking)})")
            if not record["scenes"]:
                return
            self.fill()
            self.consistency()
            self.first_watch(previous=[issue for issue in blocking if issue.get("source") != CONSISTENCY_SOURCE])
            after = script.get(judge.FIRST_WATCH)
            if after is None or judge.needs_first_watch(script, version):
                return  # J1 failed: the step ends failed naming it (first_watch)
            left_watch = len(judge.blocking_issues(after))
            left_check = len(consistency_issues(script))
            record["issues_after"] = left_watch + left_check
            self.save()
            if after["passed"]:
                self.ctx.on_log(judge.first_watch_line(after).replace("First watch:", "First watch after repair:"))
            else:
                self.ctx.on_log(f"👀 First watch after repair: {left_watch} blocking issue"
                                f"{'s remain' if left_watch != 1 else ' remains'}")
            if left_check:
                self.ctx.on_log(f"🔍 Consistency after repair: {left_check} blocking issue"
                                f"{'s remain' if left_check != 1 else ' remains'}")
            elif any(issue.get("source") == CONSISTENCY_SOURCE for issue in blocking):
                self.ctx.on_log("🔍 Consistency after repair: passed")

    def _prop_name(self, pid) -> str:
        return (self.ec.entities["props"].get(pid) or {}).get("name") or pid

    def check_only(self) -> None:
        """Plan 19 stage 3 (F6): the checks alone on the script as it stands --
        E4 and, on a v2 story, J1, each only when its report is missing,
        stale or of an older revision (as a full run) -- never the writing,
        the fill pass or :meth:`repair`: a human's edit is checked, never
        rewritten. An incomplete script is refused (:func:`check_only_refusal`)."""
        ec = self.ec
        refusal = check_only_refusal(self.script, ec.ep, self.ctx.params)
        if refusal:
            raise StepFailed(refusal)
        self.ctx.on_log(f"🔍 Episode {ec.ep}: check only -- the script is checked as it stands; nothing is written "
                        "or repaired")
        self.consistency()
        self.first_watch()

    def run(self) -> dict:
        ec = self.ec
        self.script = episode_common.read_episode(ec, SCRIPT_DOC)
        checking = bool(self.ctx.params.get(CHECK_ONLY_PARAM))
        if checking:
            self.check_only()
        else:
            if self.script is None:
                self.script = skeleton(ec, now=llm_call.utc_now())
            if not self.script["scenes"]:
                self.beat_sheet()
            elif any(scene["state"] == "stub" for scene in self.script["scenes"]):
                # A beat sheet kept by an earlier run: its plans are checked before any scene is written.
                self.refuse_unfit_plan(self.script)
            self.body()
            self.framing()
            self.fill()
            self.consistency()
            self.first_watch()
            self.repair()

        script = self.script
        if self.calls == 0 and not self.failed:
            self.ctx.on_log(f"✅ Episode {ec.ep}'s script is checked already: nothing to check." if checking
                            else f"✅ Episode {ec.ep}'s script is complete and checked: nothing to write.")
        measuring = bool(self.ctx.params.get(MEASURE_PARAM)) and not checking
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
        if checking:
            summary["check_only"] = True
        if media_policy.is_v2(ec.story):
            # Phase 7 stage 6a: the first-watch report's verdict and the fill pass's record.
            first_watch = script.get(judge.FIRST_WATCH)
            summary["first_watch"] = None if first_watch is None else first_watch["passed"]
            summary["fill"] = self.filled
            # Stage G: the repair passes' records.
            summary["repairs"] = self.repairs
        return summary


def run(ctx, *, runner=None, time_fn=time.monotonic, adapters=None, transport=None, budget=None) -> dict:
    """The step (module docstring). *budget*: an ``episode_common.Budget``
    shared with a caller running this step inside its own (the fast track);
    None gives the step its own."""
    ec = episode_common.load_episode_context(ctx)
    # A v2 story writes its script from an approved, current knowledge base
    # (phase 7 stage 5b, DEC-228); a legacy story is never held back by it.
    episode_common.check_episode_preconditions(ctx, ec, require_knowledge=True)
    ctx.cancel.check()
    return _Run(ctx, ec, runner=runner, time_fn=time_fn, adapters=adapters, transport=transport,
                budget=budget).run()
