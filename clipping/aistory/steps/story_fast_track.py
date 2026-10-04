"""Step ``story-fast-track``: agent mode -- one job takes a story created in
agent mode from its seed to episode 1 rendered with its metadata pack,
approving each document by rule (plan 21 stage 1; U3 of the competitive
brief; DEC-263's task C).

Allowed only on a story whose ``generation_profile.mode`` is ``agent``
(``workflow.require_agent_mode``); a Studio story keeps every gate it had.
It takes no parameters and works on episode 1.

**One job, nine parts, the fast track's pattern** (``fast_track.py``, the
DEC-131 precedent): each step's runner is called in-process, in order, and
each document is approved through the workflow's own rule
(``workflow.approve_*`` with ``by: agent``, :data:`AGENT_APPROVED`, recorded
on the document). The job keeps no state of its own: every part reads what
the story's documents already hold, so a run that stopped is simply run
again ("Continue"), and a part whose document is approved is **kept as it
is** -- no call, no approval again:

1. **concept** -- the concepts step asked for **one** card
   (``params.count: 1``) from the story's ``seed_text`` (the idea is the
   concept: no ranking is invented), then ``workflow.choose_concept`` of it
   -- the newest generated card when the story already has some;
2. **bible** -- the bible step's parts (B1, B2, B3) whose fields are still
   missing (``bible.missing_parts``), then ``approve_bible``;
3. **style** -- ``workflow.build_style`` (the story's ``style_template_id``,
   else the concept's ``style_fit``), the preview strip (``style_preview``,
   unless one is made), then ``approve_style``;
4. **cast** -- the pick (``workflow.agent_cast_pick``: the concept's
   ``cast_sketch`` names, at most :data:`CAST_PICK_MAX`, then
   ``workflow.MAX_CAST`` with the story's own), the cast step (it pins the
   voices itself), then each complete character approved;
5. **places proposal** -- P0, unless a proposal or a place exists;
6. **places** -- the places step on the saved proposal, then each complete
   place and prop approved;
7. **season** -- the season step with ``season.DEFAULT_EPISODES``
   (or, on a season already outlined, S2 for each entry not expanded yet),
   then ``approve_season``;
8. **knowledge** (a v2 story only) -- the knowledge step, then
   ``approve_knowledge``; the props its D6 creates are drawn and approved
   by the places step again, so the story is ``ready``;
9. **episode 1** -- ``fast_track.run`` on episode 1 (the story's own
   ``episode_template_id``) with ``stop_at_keyframes`` and
   ``stop_on_script_issues`` off: the fast track's own rules, judges and
   approvals (``by: fast_track``, DEC-162/246/265).

Before the first part the run asks ``workflow.story_fast_track_estimate``
-- every part still to do, summed, with the caps -- and stops there **before
anything is called or bought** when a part cannot run: a key gate, a part's
own refusal, a paid part while ``allow_paid`` is off or over a cap (RC-A3).
Each part then meets its own gate again (the LLM key gate, the image chain's
verdict, the editor's) right before it runs, and episode 1 meets the fast
track's paid check.

The looks are approved on completeness, with no taste check (plan 21
decision 4): the style, the portraits and the plates are approved as soon
as they are complete; the human reviews them in Studio afterwards, where
every regenerate stays available.

**Every stop** ends the job ``failed`` with "Agent run stopped at <part>
(n of 9): <reason>. Continue the agent run: it picks up here and repeats
nothing already done." Each part is a feed line "⏩ Agent n/9: <part>" and
the job record's ``sub_step`` (``ctx.on_sub_step``, set before it starts).
A cancel is checked between the parts and by each runner. On success the
job ends ``completed`` (``steps.ends_completed``) and returns ``{ep, parts:
{name: summary}, approved: [...], episode: <the fast track's summary>,
seconds}``.

The approvals are the workflow's own rules, so ``workflow`` is imported
where it is used, never at load: ``workflow`` imports this module.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import dataclasses
import subprocess
import time

from .. import media_policy, schemas
from .. import steps as steps_pkg
from . import bible as bible_step
from . import cast as cast_step
from . import concepts as concepts_step
from . import entities, llm_call
from . import fast_track as fast_track_step
from . import knowledge as knowledge_step
from . import places as places_step
from . import places_proposal as places_proposal_step
from . import season as season_step
from . import style_preview as preview_step
from .entities import CHARACTERS, PLACES, PROPS
from .llm_call import StepFailed

STEP = "story-fast-track"
# The episode the agent run makes.
EPISODE = 1
# The parts, in order (the job record's ``sub_step`` values, the rail's
# keys), how the feed names them, and how a stop names them.
PARTS = ("concepts", "bible", "style", "cast", "places_proposal", "places", "season", "knowledge", "episode")
LABELS = {"concepts": "concept", "bible": "bible", "style": "style", "cast": "cast",
          "places_proposal": "places proposal", "places": "places", "season": "season", "knowledge": "knowledge",
          "episode": "episode 1"}
STOP_LABELS = {name: ("episode 1" if name == "episode" else f"the {label}") for name, label in LABELS.items()}
# The concept is the idea: one card (plan 21 decision 1).
CONCEPT_COUNT = 1
# The cast: the concept's sketch, at most five (plan 21 decision 2).
CAST_PICK_MAX = 5
# Episode 1 as the one click makes it: never stopped at the keyframes or at
# the script's leftover issues (the fast track's usual params, DEC-265).
FAST_TRACK_PARAMS = {fast_track_step.STOP_PARAM: False, fast_track_step.SCRIPT_STOP_PARAM: False}
# The time budget the estimate shows (plan 21 decision 3): a part of
# pre-production is given a quarter of an hour; episode 1 the fast track's
# own budget; the whole never more than the fast track's ceiling for the
# episode plus the pre-production's own (the eight parts' quarters).
PART_BUDGET_SECONDS = 15 * 60
PRE_PRODUCTION_PARTS = PARTS[:-1]
STORY_BUDGET_CEILING_SECONDS = (fast_track_step.FAST_TRACK_BUDGET_CEILING_SECONDS
                                + len(PRE_PRODUCTION_PARTS) * PART_BUDGET_SECONDS)
CONTINUE = "Continue the agent run: it picks up here and repeats nothing already done."
# What a fast-track stop says last, replaced by the agent run's own.
_FAST_TRACK_CONTINUE = " Then Continue the fast track: it picks up here and repeats nothing already done."
AGENT_APPROVED = schemas.AGENT_APPROVED


def _workflow():
    """``clipping.aistory.workflow``, imported when first used (see the
    module docstring)."""
    from .. import workflow

    return workflow


def _and(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _s(count) -> str:
    return "" if count == 1 else "s"


def read_params(params) -> dict:
    """The step takes no parameter: ``{}``; ``StepFailed`` otherwise."""
    if params:
        raise StepFailed(f"The agent run takes no parameters; not {', '.join(map(repr, sorted(params)))}.")
    return {}


def stop_message(number, name, message) -> str:
    """The sentence a stop at part *number* (*name*) ends the job with."""
    message = " ".join(str(message).split())
    if message.endswith(_FAST_TRACK_CONTINUE.strip()):
        message = message[: -len(_FAST_TRACK_CONTINUE.strip())].rstrip()
    if message and message[-1] not in ".!?":
        message += "."
    return f"Agent run stopped at {STOP_LABELS[name]} ({number} of {len(PARTS)}): {message} {CONTINUE}"


# -------------------------------------------------------------------- the run

class _AgentRun:
    """One run: the step's context, its seams, and what it did."""

    def __init__(self, ctx, *, runner, time_fn, adapters, transport, sleep_fn, transcribe, run_process, popen,
                 clock, detect, cover_process, custom_fonts_dir, profile):
        self.ctx = ctx
        read_params(ctx.params)
        self.runner = runner
        self.time_fn = time_fn
        self.adapters = adapters
        self.transport = transport
        self.sleep_fn = sleep_fn
        self.transcribe = transcribe
        self.run_process = run_process
        self.popen = popen
        self.clock = clock
        self.detect = detect
        self.cover_process = cover_process
        self.custom_fonts_dir = custom_fonts_dir
        self.profile = profile
        self.started = time_fn()
        self.approved = []

    # ------------------------------------------------------------ plumbing

    def log(self, line) -> None:
        self.ctx.on_log(line)

    def open(self):
        """``(store, story)`` as they stand now."""
        return llm_call.open_story(self.ctx)

    def sub(self, step, params=None, *, ep=None):
        """The context a runner is called with: this job's, under its own
        step name, params and episode."""
        return dataclasses.replace(self.ctx, step=step, params=dict(params or {}), ep=ep)

    def call(self, fn, *args, **kwargs):
        """A workflow function; its refusal is a stop with its sentence."""
        workflow = _workflow()
        try:
            return fn(*args, **kwargs)
        except (workflow.WorkflowError, workflow.StoryUnreadable) as exc:
            raise StepFailed(str(exc)) from None

    def approve(self, what, call, detail) -> None:
        """Approve *what* by the workflow's own rule (*call(workflow, now)*,
        recorded ``by: agent``); its refusal is a stop."""
        workflow = _workflow()
        try:
            call(workflow, llm_call.utc_now())
        except (workflow.WorkflowError, workflow.StoryUnreadable) as exc:
            raise StepFailed(f"{what[0].upper()}{what[1:]} could not be approved: {exc}") from None
        self.approved.append(what)
        self.log(f"✅ Agent: {what} approved by rule ({detail})")

    def kept(self, what) -> dict:
        self.log(f"📄 {what[0].upper()}{what[1:]} is approved already: kept as it is.")
        return {"kept": True}

    def gate_llm(self) -> None:
        """The key gate (``workflow.llm_route``), before a part that calls
        the LLM chain: the route met it when the job was queued; the chain or
        the keys may have changed since."""
        refusal = _workflow().llm_route(self.ctx.settings_env, readiness=lambda _links, _keys: None)[3]
        if refusal:
            raise StepFailed(refusal)

    def gate_generation(self, units) -> None:
        """A part that makes images meets the gates the route's job would
        (``workflow.agent_generation_refusal``): the key gate when it calls
        the LLM chain, the image chain's verdict, the editor's -- before its
        first call."""
        store, story = self.open()
        refusal = self.call(_workflow().agent_generation_refusal, store, story, units,
                            env=self.ctx.settings_env)
        if refusal:
            raise StepFailed(refusal)

    def image_kwargs(self) -> dict:
        return {"runner": self.runner, "time_fn": self.time_fn, "sleep_fn": self.sleep_fn,
                "adapters": self.adapters, "transport": self.transport}

    def approve_entities(self, kinds, what) -> int:
        """Approve every complete, unapproved entity of *kinds*; a stop naming
        what each one still lacks when one is not complete. Returns how many
        were approved."""
        workflow = _workflow()
        store, story = self.open()
        count, lacking = 0, []
        for kind in kinds:
            for doc in self.call(workflow.list_entities, store, story["story_id"], kind):
                if doc["approved_at"]:
                    continue
                missing = workflow.MISSING[kind](store, story["story_id"], doc)
                if missing:
                    labels = ", ".join(workflow.MISSING_LABELS[item] for item in missing)
                    lacking.append(f"{doc['name']} (missing: {labels})")
                    continue
                eid = doc[{CHARACTERS: "char_id", PLACES: "place_id", PROPS: "prop_id"}[kind]]
                self.approve(f"{workflow.ENTITY_WORDS[kind]} {doc['name']}",
                             lambda wf, now, k=kind, e=eid: wf.approve_entity(store, story["story_id"], k, e,
                                                                             now=now, by=AGENT_APPROVED),
                             "complete")
                count += 1
        if lacking:
            raise StepFailed(f"The {what} cannot be approved yet: {'; '.join(lacking)}. Make what is missing "
                             "(the step again, or a regenerate in Studio).")
        return count

    # ---------------------------------------------------------------- parts

    def concepts(self) -> dict:
        workflow = _workflow()
        store, story = self.open()
        if story["approvals"].get("concept"):
            return self.kept("the concept")
        cards = self.call(workflow.generated_cards, store, story["story_id"])
        calls = 0
        if not cards:
            self.gate_llm()
            summary = concepts_step.run(self.sub("concepts", {concepts_step.COUNT_PARAM: CONCEPT_COUNT}),
                                        runner=self.runner, time_fn=self.time_fn)
            calls = CONCEPT_COUNT - len(summary["failed_calls"])
            cards = self.call(workflow.generated_cards, store, story["story_id"])
        card = cards[-1]
        self.approve("the concept",
                     lambda wf, now: wf.choose_concept(store, story["story_id"], concept_id=card["concept_id"],
                                                       now=now, by=AGENT_APPROVED),
                     f"{card['concept_id']}: {card['title']}")
        return {"kept": False, "concept_id": card["concept_id"], "calls": calls}

    def bible(self) -> dict:
        workflow = _workflow()
        store, story = self.open()
        if story["approvals"].get("bible"):
            return self.kept("the bible")
        calls = 0
        parts = bible_step.missing_parts(story)
        if parts:
            # Only the parts still missing: a Continue repeats none already written.
            self.gate_llm()
            summary = bible_step.run(self.sub("bible"), runner=self.runner, time_fn=self.time_fn, parts=parts)
            calls = len(summary["written"])
        self.approve("the bible",
                     lambda wf, now: wf.approve_bible(store, story["story_id"], now=now, by=AGENT_APPROVED),
                     "every field written")
        return {"kept": False, "calls": calls}

    def style(self) -> dict:
        workflow = _workflow()
        store, story = self.open()
        if story["approvals"].get("style"):
            return self.kept("the style")
        story_id = story["story_id"]
        lock = self.call(workflow.style_lock, store, story_id)
        built = False
        if lock is None:
            # The story's own style, else the concept's (``build_style``'s own default).
            result = self.call(workflow.build_style, store, story_id, {}, now=llm_call.utc_now())
            lock, built = result["style_lock"], True
            whose = "the story's" if story.get("style_template_id") else "the concept's"
            self.log(f"🎨 Style: {lock['template_id']} ({whose}), built from its template.")
        preview = None
        if self.call(workflow.agent_preview_needed, store, story_id):
            store, story = self.open()
            refusal = self.call(workflow.agent_preview_refusal, store, story, env=self.ctx.settings_env)
            if refusal:
                raise StepFailed(refusal)
            preview = preview_step.run(self.sub(preview_step.STEP), transport=self.transport,
                                       adapters=self.adapters, sleep_fn=self.sleep_fn, time_fn=self.time_fn)
        self.approve("the style",
                     lambda wf, now: wf.approve_style(store, story_id, now=now, by=AGENT_APPROVED),
                     f"{lock['template_id']} locked, no taste check")
        return {"kept": False, "built": built, "preview": preview}

    def cast(self) -> dict:
        workflow = _workflow()
        store, story = self.open()
        if story["approvals"].get("cast"):
            return self.kept("the cast")
        pick = self.call(workflow.agent_cast_pick, store, story)
        params = {"selected": pick}
        selected, custom = self.call(workflow.cast_request, store, story, params)
        units = self.call(workflow.cast_units, store, story, selected=selected, custom=custom)
        summary = None
        if any(units.values()):
            self.gate_generation(units)
            self.log(f"🎭 Cast: {_and(pick)} (the concept's sketch, at most {CAST_PICK_MAX}).")
            summary = cast_step.run(self.sub("cast", params), **self.image_kwargs())
        approved = self.approve_entities((CHARACTERS,), "cast")
        store, story = self.open()
        if not story["approvals"].get("cast"):
            raise StepFailed("The cast is not approved: every lead and support character must be complete and "
                             "approved.")
        return {"kept": False, "pick": pick, "approved": approved, "summary": summary}

    def places_proposal(self) -> dict:
        workflow = _workflow()
        store, story = self.open()
        story_id = story["story_id"]
        if story["approvals"].get("places"):
            return self.kept("the places")
        if (self.call(workflow.places_proposal, store, story_id) is not None
                or self.call(workflow.list_entities, store, story_id, PLACES)):
            self.log("📄 The places are proposed already: kept as they are.")
            return {"kept": True}
        self.call(workflow.require_places_proposable, store, story)
        self.gate_llm()
        summary = places_proposal_step.run(self.sub("places_proposal"), runner=self.runner, time_fn=self.time_fn)
        return {"kept": False, "summary": summary}

    def fill_places(self) -> dict:
        """The places step on the saved proposal -- or, a story with no
        proposal (its places listed by hand), on the places and props it has
        -- making what is missing only, then every complete place and prop
        approved."""
        workflow = _workflow()
        store, story = self.open()
        has_proposal = self.call(workflow.places_proposal, store, story["story_id"]) is not None
        params = {} if has_proposal else {"places": [], "props": []}
        self.call(workflow.require_places_ready, store, story, params)
        units = self.call(workflow.places_units, store, story, params)
        summary = None
        if any(units.values()):
            self.gate_generation(units)
            summary = places_step.run(self.sub("places", params), **self.image_kwargs())
        approved = self.approve_entities((PLACES, PROPS), "places")
        store, story = self.open()
        if not story["approvals"].get("places"):
            raise StepFailed("The places are not approved: every place and prop must be complete and approved.")
        return {"kept": False, "approved": approved, "summary": summary}

    def places(self) -> dict:
        store, story = self.open()
        if story["approvals"].get("places"):
            return self.kept("the places")
        return self.fill_places()

    def season(self) -> dict:
        workflow = _workflow()
        store, story = self.open()
        story_id = story["story_id"]
        if story["approvals"].get("season"):
            return self.kept("the season")
        current = self.call(workflow.season, store, story_id)
        calls = 0
        if current is None:
            self.call(workflow.require_cast_approved, story)
            self.gate_llm()
            summary = season_step.run(self.sub("season", {"episodes": season_step.DEFAULT_EPISODES}),
                                      runner=self.runner, time_fn=self.time_fn)
            calls = 1 + len(summary["expanded"])
        else:
            todo = workflow.agent_season_todo(current)
            if todo:
                self.gate_llm()
                tools = entities.Tools(runner=self.runner, time_fn=self.time_fn)
                ctx = self.sub("season", {"episodes": current["episodes_planned"]})
                failed = []
                for ep in todo:
                    self.ctx.cancel.check()
                    try:
                        season_step.expand_entry(ctx, store, ep, tools=tools)
                    except StepFailed as exc:
                        failed.append(f"episode {ep} failed ({exc.reason})")
                        continue
                    calls += 1
                if failed:
                    raise StepFailed(f"Season arc incomplete: {'; '.join(failed)}.")
        store, story = self.open()
        planned = self.call(workflow.season, store, story_id)["episodes_planned"]
        self.approve("the season",
                     lambda wf, now: wf.approve_season(store, story_id, now=now, by=AGENT_APPROVED),
                     f"{planned} episodes, each written")
        return {"kept": False, "calls": calls}

    def knowledge(self) -> dict:
        workflow = _workflow()
        store, story = self.open()
        story_id = story["story_id"]
        if not media_policy.is_v2(story):
            self.log("📚 A legacy story has no knowledge base: nothing to do.")
            return {"kept": True, "legacy": True}
        doc = self.call(workflow.knowledge, store, story_id)
        result = {"kept": True}
        if workflow.knowledge_current(doc):
            self.kept("the knowledge base")
        else:
            self.call(workflow.require_knowledge_runnable, story)
            summary = None
            if self.call(workflow.knowledge_calls, store, story):
                self.gate_llm()
                summary = knowledge_step.run(self.sub("knowledge"), runner=self.runner, time_fn=self.time_fn)
            self.approve("the knowledge base",
                         lambda wf, now: wf.approve_knowledge(store, story_id, now=now, by=AGENT_APPROVED),
                         "every section written")
            result = {"kept": False, "summary": summary}
        store, story = self.open()
        if not story["approvals"].get("places"):
            # D6 registered props of its own: the places step draws them, then they are approved.
            self.log("🗺 The knowledge base added props: the places step draws them now.")
            result["places"] = self.fill_places()
        return result

    def episode(self) -> dict:
        ctx = self.sub(fast_track_step.STEP, FAST_TRACK_PARAMS, ep=EPISODE)
        return fast_track_step.run(ctx, runner=self.runner, time_fn=self.time_fn, adapters=self.adapters,
                                   transport=self.transport, sleep_fn=self.sleep_fn, transcribe=self.transcribe,
                                   run_process=self.run_process, popen=self.popen, clock=self.clock,
                                   detect=self.detect, cover_process=self.cover_process,
                                   custom_fonts_dir=self.custom_fonts_dir, profile=self.profile)

    # ------------------------------------------------------------------ run

    def sub_step(self, name) -> None:
        if self.ctx.on_sub_step is not None:
            self.ctx.on_sub_step(name)

    def run(self) -> dict:
        ctx = self.ctx
        workflow = _workflow()
        store, story = self.open()
        self.call(workflow.require_agent_mode, story)
        ctx.cancel.check()
        # The whole run, summed, before anything is called or bought (RC-A3).
        estimate = self.call(workflow.story_fast_track_estimate, store, story, env=ctx.settings_env)
        stop = estimate["stops_at"]
        if stop is not None:
            raise StepFailed(stop_message(stop["number"], stop["part"], stop["reason"]), reason=stop["reason"])
        todo = [row["label"] for row in estimate["parts"] if not row["kept"]]
        paid = f" (paid: {_and(estimate['paid'])})" if estimate["paid"] else ""
        self.log(f"⏩ Agent run: {' → '.join(LABELS[name] for name in PARTS)} -- {len(todo)} part{_s(len(todo))} "
                 f"to do, est {'up to ' if not estimate['exact'] else ''}${estimate['est_usd']:.2f}{paid}, under a "
                 f"{int(estimate['budget']['seconds'] // 60)}-minute budget. {estimate['caps_line']}".rstrip())
        self.log("⚠️ Agent mode approves the style, the cast and the places as soon as they are complete -- no "
                 "taste check; review them in Studio afterwards (every regenerate stays available).")
        results = {}
        for number, name in enumerate(PARTS, start=1):
            ctx.cancel.check()
            self.sub_step(name)
            self.log(f"⏩ Agent {number}/{len(PARTS)}: {LABELS[name]}")
            try:
                results[name] = getattr(self, name)()
            except StepFailed as exc:
                raise StepFailed(stop_message(number, name, exc), reason=getattr(exc, "reason", str(exc))) from None
            if steps_pkg.awaiting_uploads(results[name]):
                # Plan 22 stage 5: episode 1 waits for the human's own clips -- the run pauses (the job ends
                # awaiting_uploads) and an upload that leaves nothing missing starts it again.
                uploads = results[name]["uploads"]
                self.log(f"⏸ Agent run paused at {STOP_LABELS[name]} ({number} of {len(PARTS)}): "
                         f"{uploads.get('message')}. It goes on by itself once every clip is uploaded; nothing "
                         "done so far is repeated.")
                return {"ep": EPISODE, "state": steps_pkg.AWAITING_UPLOADS, "uploads": uploads, "paused_at": name,
                        "parts": {key: results[key] for key in PRE_PRODUCTION_PARTS if key in results},
                        "episode": results[name], "approved": list(self.approved),
                        "seconds": round(self.time_fn() - self.started, 1)}
        seconds = round(self.time_fn() - self.started, 1)
        approved = f"; approved by rule: {_and(self.approved)}" if self.approved else ""
        self.log(f"🏁 Agent run done: episode {EPISODE} is rendered with its metadata pack ({seconds / 60:.1f} min"
                 f"{approved}). Review the story in Studio: every document stays editable and every regenerate "
                 "available.")
        return {"ep": EPISODE, "parts": {name: results[name] for name in PRE_PRODUCTION_PARTS},
                "episode": results["episode"], "approved": list(self.approved), "seconds": seconds}


def run(ctx, *, runner=None, time_fn=time.monotonic, adapters=None, transport=None, sleep_fn=time.sleep,
        transcribe=None, run_process=subprocess.run, popen=subprocess.Popen, clock=time.monotonic, detect=None,
        cover_process=None, custom_fonts_dir=None, profile="final") -> dict:
    """The step (module docstring). Every keyword is a seam for tests, handed
    to the runners that use it, as ``fast_track.run`` takes them: *runner*
    (the LLM chain), *time_fn*, *adapters*, *transport*, *sleep_fn* (the
    images, voices and clips), *transcribe*, *run_process*, *popen*, *clock*,
    *detect*, *cover_process*, *custom_fonts_dir*, *profile* (the render and
    the metadata of episode 1)."""
    return _AgentRun(ctx, runner=runner, time_fn=time_fn, adapters=adapters, transport=transport,
                     sleep_fn=sleep_fn, transcribe=transcribe, run_process=run_process, popen=popen, clock=clock,
                     detect=detect, cover_process=cover_process, custom_fonts_dir=custom_fonts_dir,
                     profile=profile).run()
