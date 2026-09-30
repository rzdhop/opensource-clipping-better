"""What the phase-3 episode steps share (AI Story phase 3, stage 6).

The ``script`` and ``storyboard`` steps and the episode targets of
``regenerate`` all work on one episode of a ``ready`` story: its
``script.json`` and ``storyboard.json`` under ``episodes/ep<NN>/``
(``store.read_episode_doc``/``write_episode_doc``). The rules they have in
common live here, once:

- :func:`load_episode_context` -- everything an episode call is written
  from: the story, its style lock, its season, the episode template (the one
  its script was written against, else the story's choice), and its
  characters, places (with their existing variant names) and props;
- :func:`check_episode_preconditions` -- the story is ``ready``; the episode
  is one the season plans; and -- the gate, DEC-130 as amended by plan 11
  stage 4 -- a step that writes an episode's script or storyboard from
  episode 2 on needs the series memory of the episode before it written,
  approved and fresh (:func:`memory_refusal`, :func:`needs_memory`). Its
  refusals are :class:`EpisodeRefused`, typed so ``workflow`` (the API and
  the CLI) answers each with its own code from this one implementation;
- :class:`Budget` -- a step's own time budget, checked **predictively**: a
  call starts only when it could still finish inside the budget, else the
  step ends failed naming what is left (DEC-053/054's rule, applied to a
  whole step; a script is up to 14 calls on free links whose latency swings
  by 100 s, DEC-091);
- :func:`retime` -- the script's ``timing`` is derived (``timing.
  episode_timing``): re-timing never moves a revision and never clears an
  approval;
- :func:`mark_changed` -- what a rewrite of part of a script does to both
  documents: the revisions move, both approvals and ``approved_anyway`` are
  cleared, the consistency report and every storyboard scene planned from an
  older revision of its scene become stale. An approval never outlives what
  it approved (DEC-123, extended to episodes). A text-only edit (phase 5
  stage 7, DEC-129 as amended) keeps the storyboard's approval and its
  scenes' plans: a scene whose words moved is marked ``retime_only``.

Nothing here reads or writes ``story.json``: an episode never changes the
story's approvals or status (RC-E2).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from .. import schemas, series_memory, templates, timing
from .. import store as store_mod
from . import entities
from .entities import CHARACTERS, PLACES, PROPS
from .llm_call import STORY_CALL_BUDGET_SECONDS, StepFailed

# A script is one job (E1 -> E2 x N -> E3 -> E4, the human's answer): 30
# minutes holds 14 calls at a free link's median latency with room for the
# slow ones, and never holds the single worker slot a clip render waits for
# for longer than that.
EPISODE_STEP_BUDGET_SECONDS = 1800

REQUIRED_STATUS = "ready"
SCRIPT_DOC = store_mod.EPISODE_SCRIPT_DOC
STORYBOARD_DOC = store_mod.EPISODE_STORYBOARD_DOC
_NOT_A_FOLDER = ("Episode {ep}'s folder (episodes/ep{ep:02d}/) is not a real directory; it is never followed: "
                 "move it away first.")


# Why an episode request is refused (:class:`EpisodeRefused`), for a caller
# that answers each its own way -- the web layer: 409 or 400.
NOT_READY = "not_ready"
OUTSIDE_SEASON = "outside_season"
NO_ARC_ENTRY = "no_arc_entry"
# The gate (plan 11 stage 4, amending DEC-130's "no recap"): the memory of
# the episode before is missing, written but not approved, or out of date.
MEMORY_MISSING = "memory_missing"
MEMORY_UNAPPROVED = "memory_unapproved"
MEMORY_STALE = "memory_stale"
MEMORY_REFUSALS = (MEMORY_MISSING, MEMORY_UNAPPROVED, MEMORY_STALE)

# The steps that write an episode's script or storyboard, and so meet the
# gate: always the script and the storyboard (T1 or fast); the fast track
# while it would write one of them (:func:`needs_memory`). The assets, the
# render and the metadata are made from an approved script and storyboard,
# which met the gate when they were written: they never meet it themselves,
# so a memory going stale later un-approves nothing downstream.
MEMORY_GATED_STEPS = ("script", "storyboard")


class EpisodeRefused(StepFailed):
    """:func:`check_episode_preconditions` refuses the episode; ``kind`` says
    why (``NOT_READY``, ``OUTSIDE_SEASON``, ``NO_ARC_ENTRY``, or one of
    :data:`MEMORY_REFUSALS`). A runner treats it as any other ``StepFailed``."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def recap_key(ep) -> str:
    """The ``series_memory.recaps`` key of episode *ep*: ``"ep01"`` (spec 2.6)."""
    return f"ep{ep:02d}"


@dataclass
class EpisodeContext:
    """One episode of one story, as every episode call reads it."""

    store: object
    story_id: str
    ep: Optional[int]
    story: dict
    style_lock: dict
    season: Optional[dict]
    template: dict
    # {"characters": {id: doc}, "places": {id: doc}, "props": {id: doc}}
    entities: dict
    # The characters in cast order (leads, support, recurring, guest).
    cast: list
    # {place_id: [its existing variant names]}
    places: dict
    prop_ids: list
    narrator: bool
    language: str
    consistency_mode: str
    arc_entry: Optional[dict]
    next_arc_entry: Optional[dict]

    @property
    def episode_defaults(self) -> dict:
        return self.style_lock["episode_defaults"]

    @property
    def sfx_cues(self) -> list:
        return list(self.style_lock["audio"]["sfx_cues"])

    @property
    def names(self) -> dict:
        return {doc["char_id"]: doc["name"] for doc in self.cast}


# ------------------------------------------------------------------ loading

def _entry(season, ep):
    if season is None or ep is None:
        return None
    return next((entry for entry in season["arc"] if entry["ep"] == ep), None)


def load_context(stores, story_id, ep) -> EpisodeContext:
    """The :class:`EpisodeContext` of episode *ep* of *story_id* in
    *stores*. ``StepFailed`` for a story that does not exist, a style that is
    not locked, a season or an episode template that cannot be read."""
    try:
        story = stores.get(story_id)
    except KeyError:
        raise StepFailed(f"There is no story {story_id!r}.") from None
    style_lock = entities.read_lock(stores, story_id)
    try:
        season = stores.read_doc(story_id, store_mod.SEASON_DOC)
    except schemas.SchemaError as exc:
        raise StepFailed(f"{store_mod.SEASON_DOC} is not a valid {schemas.SEASON_ARC_SCHEMA_NAME} document "
                         f"({'; '.join(exc.errors[:3])}); fix or remove it first.") from None
    # An episode keeps the template it was written against: the story's
    # choice applies to an episode with no script yet.
    template_id = story["episode_template_id"]
    if type(ep) is int and store_mod.EPISODE_MIN <= ep <= store_mod.EPISODE_MAX:
        try:
            existing = stores.read_episode_doc(story_id, ep, SCRIPT_DOC)
        except schemas.SchemaError as exc:
            raise StepFailed(f"Episode {ep}'s {SCRIPT_DOC} does not validate ({'; '.join(exc.errors[:3])}); fix "
                             "or remove it first.") from None
        except KeyError:
            raise StepFailed(_NOT_A_FOLDER.format(ep=ep)) from None
        if existing is not None:
            template_id = existing["template_id"]
    try:
        template = templates.load_episode_template(template_id)
    except (KeyError, OSError, schemas.SchemaError) as exc:
        raise StepFailed(f"The story's episode template {template_id!r} cannot be read ({exc}).") from None

    characters = entities.cast_order(stores.list_entities(story_id, CHARACTERS))
    places = stores.list_entities(story_id, PLACES)
    props = stores.list_entities(story_id, PROPS)
    return EpisodeContext(
        store=stores, story_id=story_id, ep=ep, story=story, style_lock=style_lock, season=season,
        template=template,
        entities={"characters": {doc["char_id"]: doc for doc in characters},
                  "places": {doc["place_id"]: doc for doc in places},
                  "props": {doc["prop_id"]: doc for doc in props}},
        cast=characters,
        places={doc["place_id"]: list(doc["time_variants"]) for doc in places},
        prop_ids=[doc["prop_id"] for doc in props],
        narrator=bool((story.get("narrator") or {}).get("enabled")),
        language=story["language"],
        consistency_mode=story["generation_profile"]["consistency_mode"],
        arc_entry=_entry(season, ep),
        next_arc_entry=_entry(season, ep + 1) if isinstance(ep, int) else None,
    )


def load_episode_context(ctx) -> EpisodeContext:
    """:func:`load_context` for the step's own story and ``ctx.ep``."""
    stores = store_mod.StoryStore(ctx.outputs_dir, on_log=ctx.on_log)
    return load_context(stores, ctx.story_id, ctx.ep)


def check_story_ready(story) -> None:
    """:class:`EpisodeRefused` (``NOT_READY``) unless *story*'s derived status
    is ``ready``: episodes are written for an approved cast, places and
    season. The first check of :func:`check_episode_preconditions`, callable
    before an :class:`EpisodeContext` exists (a story that is not ready may
    not have what one reads)."""
    try:
        entities.require_status(story, REQUIRED_STATUS,
                                "The story is not ready yet: approve the cast, the places and the season first.")
    except StepFailed as exc:
        raise EpisodeRefused(NOT_READY, str(exc)) from None


def check_episode_preconditions(ctx, ec, *, require_memory=True) -> None:
    """:class:`EpisodeRefused` (a ``StepFailed``) with what to do, before
    anything is sent, unless the story is ``ready`` (its derived status),
    *ec*'s episode is one of ``1..season.episodes_planned``, the arc has an
    entry for it, and -- from episode 2 on, with *require_memory* -- the
    series memory of the episode before it is written, approved and fresh
    (:func:`memory_refusal`: DEC-130 as amended by plan 11 stage 4; a recap
    no memory step wrote does not count). The script and storyboard steps
    keep the default; a step that writes neither -- the assets, the render,
    the metadata, the series steps, a regenerate of a document that already
    exists, the fast track once both are approved (:func:`needs_memory`) --
    passes ``require_memory=False``. *ctx* is not read (the web layer and the
    fast storyboard call this without one)."""
    check_story_ready(ec.story)
    planned = ec.season["episodes_planned"] if ec.season else 0
    ep = ec.ep
    if type(ep) is not int or not 1 <= ep <= planned:
        raise EpisodeRefused(OUTSIDE_SEASON, f"The season plans episodes 1 to {planned}; there is no episode {ep!r}.")
    if ec.arc_entry is None:
        raise EpisodeRefused(NO_ARC_ENTRY, f"The season arc has no entry for episode {ep}; write the season again.")
    if ep >= 2 and require_memory:
        refusal = memory_refusal(ec, ep - 1, before=f"before writing episode {ep}")
        if refusal is not None:
            raise refusal


def _script_of(ec, ep):
    """Episode *ep*'s script (another episode than ``ec.ep``, maybe), or
    None; :class:`EpisodeRefused` (``MEMORY_STALE``) for one that cannot be
    read: its memory cannot be checked against it."""
    try:
        return ec.store.read_episode_doc(ec.story_id, ep, SCRIPT_DOC)
    except schemas.SchemaError as exc:
        reason = "; ".join(exc.errors[:3])
    except KeyError:
        reason = f"episodes/ep{ep:02d}/ is not a real directory"
    raise EpisodeRefused(MEMORY_STALE, (f"Episode {ep}'s {SCRIPT_DOC} cannot be read ({reason}), so its series "
                                        "memory cannot be checked against it: fix or remove it first."))


def memory_refusal(ec, ep, *, before) -> Optional[EpisodeRefused]:
    """Why episode *ep*'s series memory does not stand -- as an
    :class:`EpisodeRefused` to raise -- or None when its entry is written,
    approved and fresh (its ``script_rev`` the script's ``rev``). The gate of
    episode ``ep + 1`` and the precondition of ``propose-next`` for *ep*.
    Each refusal names the missing piece and what to do; *before* ends the
    sentence ("before writing episode 2"). Read from ``ec.season`` and
    episode *ep*'s script as they are now; the story is not read."""
    script = _script_of(ec, ep)
    state = series_memory.memory_state(ec.season, ep, script)
    if state == "approved":
        return None
    if state == "draft":
        return EpisodeRefused(MEMORY_UNAPPROVED, (f"Episode {ep}'s series memory is written but not approved: "
                                                  f"approve it (memory:{ep}), {before}."))
    run = f"run memory for episode {ep}"
    if not (script and script["approved_at"]):
        run = f"approve episode {ep}'s script, then {run}"
    if state == "none":
        return EpisodeRefused(MEMORY_MISSING, (f"Episode {ep}'s series memory is not written yet: {run} and "
                                               f"approve it, {before}."))
    return EpisodeRefused(MEMORY_STALE, (f"Episode {ep}'s series memory is out of date: episode {ep}'s script "
                                         f"changed since it was written. {run[0].upper()}{run[1:]} again and "
                                         f"approve it, {before}."))


def needs_memory(ec, step) -> bool:
    """Whether *step* on ``ec.ep`` writes the episode's script or storyboard,
    and so meets the gate (:data:`MEMORY_GATED_STEPS`): the script and the
    storyboard steps always; the fast track unless both documents are
    approved already (it keeps an approved document as it is, writing
    neither); any other step never."""
    if step in MEMORY_GATED_STEPS:
        return True
    if step != "fast-track":
        return False
    script = read_episode(ec, SCRIPT_DOC)
    board = read_episode(ec, STORYBOARD_DOC)
    return not (script and script["approved_at"] and board and board["approved_at"])


# ------------------------------------------------------------------- budget

class Budget:
    """A step's time budget, checked before each call.

    :meth:`before_call` refuses to *start* a call that could not finish
    inside the budget: one call may take up to ``STORY_CALL_BUDGET_SECONDS``
    (its own deadline, ``llm_call.call_json``), so a call starts only while
    ``elapsed + STORY_CALL_BUDGET_SECONDS <= EPISODE_STEP_BUDGET_SECONDS``.
    What was written stays written; the step is run again to continue.
    *clock* is ``time.monotonic`` unless a test hands in its own.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic, *,
                 limit: float = EPISODE_STEP_BUDGET_SECONDS, per_call: float = STORY_CALL_BUDGET_SECONDS):
        self.clock = clock
        self.limit = limit
        self.per_call = per_call
        self.started = clock()

    def elapsed(self) -> float:
        return self.clock() - self.started

    def before_call(self, left: Callable[[], str], *, per_call: Optional[float] = None) -> None:
        """``StepFailed`` when a call started now could overrun the budget;
        *left* describes the work not done yet (called only then). *per_call*
        is how long this call may take when it is not an LLM call (one
        synthesis of the voice measurement: ``script.STORY_TTS_CALL_SECONDS``)."""
        per_call = self.per_call if per_call is None else per_call
        elapsed = self.elapsed()
        if elapsed + per_call <= self.limit:
            return
        minutes = int(self.limit // 60)
        raise StepFailed(
            f"The step's {minutes}-minute budget is nearly spent ({elapsed / 60:.1f} min used, and one more call "
            f"may take up to {per_call / 60:g} min); everything written so far is kept, run the step again "
            f"to continue. Left: {left()}."
        )


# ------------------------------------------------------------ the documents

def read_episode(ec, name) -> Optional[dict]:
    """The episode's *name* document, or None; ``StepFailed`` for one that
    does not validate (never repaired, never overwritten unread)."""
    try:
        return ec.store.read_episode_doc(ec.story_id, ec.ep, name)
    except schemas.SchemaError as exc:
        raise StepFailed(f"Episode {ec.ep}'s {name} does not validate ({'; '.join(exc.errors[:3])}); fix or "
                         "remove it first.") from None
    except KeyError:
        raise StepFailed(_NOT_A_FOLDER.format(ep=ec.ep)) from None


def script_errors(ec, doc) -> list:
    """The checks of a script against the story (the store runs the
    document's own): cast, places and their variants, props, sfx cues, the
    narrator, the places-per-episode cap, and the template it follows."""
    errors = schemas.episode_script_context_errors(
        doc, cast_ids=list(ec.entities["characters"]), places=ec.places, prop_ids=ec.prop_ids,
        sfx_cues=ec.sfx_cues, narrator_enabled=ec.narrator, max_places=ec.episode_defaults["max_places"],
    )
    if doc["template_id"] != ec.template["template_id"]:
        errors.append(f"$.template_id: {doc['template_id']!r} is not the story's {ec.template['template_id']!r}")
    return errors


def trial_errors(ec, doc) -> list:
    """Every check a script would meet on its way to disk, for a reply's
    validator: the document's own, then the story's."""
    errors = schemas.episode_script_errors(doc)
    return errors or script_errors(ec, doc)


def write_script(ec, script, *, now) -> dict:
    """Write the script (atomic, validated: its own checks and the story's);
    *script* gets the ``updated_at`` written. Returns it."""
    saved = ec.store.write_episode_doc(ec.story_id, ec.ep, SCRIPT_DOC, script, now=now,
                                       validator=lambda doc: script_errors(ec, doc))
    script["updated_at"] = saved["updated_at"]
    return script


def storyboard_errors(ec, doc, script) -> list:
    """A storyboard's checks against its template and its script. A scene
    marked stale was planned from an older revision of its scene (its lines
    may have moved since): it is checked against the script again once it
    is planned again, not before."""
    errors = schemas.storyboard_errors(doc, min_shot_s=ec.template["min_shot_s"])
    if errors:
        return errors
    stale = {sid for sid, entry in doc["scenes"].items() if entry.get("stale")}
    current = dict(doc, shots=[shot for shot in doc["shots"] if shot["scene_id"] not in stale],
                   scenes={sid: entry for sid, entry in doc["scenes"].items() if sid not in stale})
    return schemas.storyboard_context_errors(current, script,
                                             shots_per_scene=ec.episode_defaults["shots_per_scene"])


def write_storyboard(ec, storyboard, script, *, now) -> dict:
    """Write the storyboard (atomic, validated against *script*)."""
    saved = ec.store.write_episode_doc(ec.story_id, ec.ep, STORYBOARD_DOC, storyboard, now=now,
                                       validator=lambda doc: storyboard_errors(ec, doc, script))
    storyboard["updated_at"] = saved["updated_at"]
    return storyboard


# ------------------------------------------------------------------- timing

def covers(storyboard, script) -> bool:
    """Whether *storyboard* has shots for every scene of *script*, in the
    script's order -- the only storyboard whose transitions time the whole
    episode (``timing.covers``: ``timing.episode_timing`` refuses any
    other)."""
    return timing.covers(storyboard, script)


def retime(script, ec, storyboard=None) -> dict:
    """Set ``script["timing"]`` from its text (or its measured lines) and
    returns *script*. A storyboard that covers every scene
    (:func:`covers`) gives the scene boundaries their real transitions; any
    other is left out of them and the boundaries are predicted by the same
    grammar (spec 6.3) until it does. Every scene with shots is never
    shorter than they need. The one computation (``timing.episode_pass``)
    the storyboard's shot durations are cut to as well
    (``shots.build_storyboard``, ``shots.retime_storyboard``), so the two
    agree -- in whole frames with no storyboard or one timed in them, in
    the old timing beside a storyboard timed before
    (``timing.board_whole_frames``; phase 5 stage 6). Derived: the revision
    and the approvals never move. A script with no scene yet has no
    timing."""
    if not script["scenes"]:
        script["timing"] = None
        return script
    script["timing"], _scenes = timing.episode_pass(script, ec.template, ec.language, style_lock=ec.style_lock,
                                                    storyboard=storyboard,
                                                    whole_frames=timing.board_whole_frames(storyboard))
    return script


def timing_line(script) -> str:
    """``⏱ 63.2 s estimated — inside 55–80 s``: the episode's length, how it
    was measured, and where it sits in the template's window."""
    result = script.get("timing")
    if not result:
        return "⏱ No timing yet"
    lo, hi = result["window_s"]
    measured, estimated = result["measured_lines"], result["estimated_lines"]
    how = "measured" if measured and not estimated else ("partly measured" if measured else "estimated")
    where = {"ok": "inside", "tightened": "tightened to fit", "over": "over", "under": "under"}[result["state"]]
    flags = len(result["flags"])
    tail = f" ({flags} flag{'s' if flags != 1 else ''})" if flags else ""
    return f"⏱ {result['total_s']:.1f} s {how} — {where} {lo:g}–{hi:g} s{tail}"


# ------------------------------------------------------------------ changes

def mark_changed(script, storyboard, *, scene_ids, now, plan_kept=None, keep_approval=False) -> None:
    """What rewriting part of a script does to both documents (in place; the
    caller writes them): ``script.rev`` and each rewritten scene's ``rev``
    move on, ``approved_at`` and ``approved_anyway`` are cleared, a
    consistency report becomes stale; a storyboard loses its approval and
    every one of its scenes planned from another revision of its scene --
    or from a scene the script no longer has -- becomes stale. *now*
    becomes both documents' ``updated_at`` (the store sets it again on
    write).

    A text-only edit (phase 5 stage 7, DEC-129 as amended): *plan_kept*
    ``{scene_id: retime}`` names the rewritten scenes whose shot plan still
    holds -- a line's words or delivery, a scene's ``pays_off``: same line
    ids, speakers and emotions. Such a scene, when its storyboard entry was
    current before the edit, follows the new revision instead of going
    stale, marked ``retime_only`` when *retime* (its words moved: its shots
    are re-timed in place once its lines are measured again,
    ``shots.retime_storyboard``); one planned from an older revision stays
    stale. *keep_approval* keeps the storyboard's approval: the caller says
    every change was one of those. The script's side never changes: its
    words changed, so its approval goes and E4 must be fresh again."""
    wanted = set(scene_ids)
    plan_kept = dict(plan_kept or {})
    before = {scene["scene_id"]: scene["rev"] for scene in script["scenes"]}
    script["rev"] += 1
    for scene in script["scenes"]:
        if scene["scene_id"] in wanted:
            scene["rev"] += 1
    script["approved_at"] = None
    script["approved_anyway"] = None
    if script.get("consistency_report") is not None:
        script["consistency_report"]["stale"] = True
    script["updated_at"] = now
    if storyboard is None:
        return
    revs = {scene["scene_id"]: scene["rev"] for scene in script["scenes"]}
    for sid, entry in storyboard["scenes"].items():
        if sid in plan_kept and sid in wanted and not entry.get("stale") and entry["script_rev"] == before.get(sid):
            entry["script_rev"] = revs[sid]
            if plan_kept[sid]:
                entry["retime_only"] = True
        elif revs.get(sid) != entry["script_rev"]:
            entry["stale"] = True
            entry.pop("retime_only", None)
            # A stale scene is never approved over, whatever the caller said.
            keep_approval = False
    if not keep_approval:
        storyboard["approved_at"] = None
    storyboard["updated_at"] = now
