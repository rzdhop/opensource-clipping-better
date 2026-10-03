"""Step ``fast-track``: one episode from its script to its metadata pack in a
single job (spec 3 "Fast track"; AI Story phase 4, stage 10; DEC-161,
DEC-162, A-076).

``ctx.ep`` is the episode; ``params.storyboard`` is ``t1`` (default: one T1
call per scene) or ``fast`` (the deterministic plan, no call). Needs what
every episode step needs (``episode_common.check_episode_preconditions``),
and -- while it would write the script or the storyboard -- the memory of the
episode before (the gate, ``episode_common.needs_memory``).

**One job, not chained jobs** (the DEC-131 precedent): the step runners are
called in-process, in order, under **one predictive budget** of
:data:`FAST_TRACK_BUDGET_SECONDS` (``episode_common.Budget``, handed to each
runner in place of its own), and the job keeps no orchestration state of its
own -- everything it knows is read from the episode's documents, so a job
that stopped is simply run again ("Continue"):

1. **script** -- ``script.run`` fills what is missing. **Auto-approved**
   (``workflow.approve_script``, never ``approve_anyway``) only when
   :func:`script_refusal` finds nothing: the script complete, its
   consistency report (E4) fresh and **passed**, and its timing not
   ``over`` or ``under`` the template's window;
2. **storyboard** -- ``t1``: ``storyboard.run`` fills what is missing;
   ``fast``: ``storyboard.build_fast`` unless the board already is the
   fast plan of this script. Auto-approved by its own rule
   (``workflow.approve_storyboard``);
3. **paid check** -- ``assets.asset_units`` (calling nothing but a local
   editor's status probe): :func:`paid_verdict`. A paid part needs ``allow_paid`` **and** must fit
   under the episode's, the day's and the story's caps; anything that
   cannot run stops too. A stop here is **before any generation call**,
   with the numbers (RC-A3);
4. **assets** -- ``assets.run`` fills what is missing; auto-approved
   (``workflow.approve_assets``: every shot imaged and current or locked,
   every line voiced; the fingerprint stored, each shot's ``approved`` set)
   only when the step reports them complete;
5. **render** -- ``render.run``, unless the last render is still the one it
   would make (``render.current_render``); the budget is asked first for
   :func:`render_seconds`;
6. **metadata** -- ``metadata.run`` (fills only the platforms missing).

A document approved already -- by the user or an earlier run -- is kept as
it is and never approved again; assets approved with a current fingerprint
are kept (no paid check, no call). So **Continue makes no repeated call**:
every sub-step fills only what is missing.

**Every stop** ends the job ``failed`` (``StepFailed``) with a sentence that
names the sub-step, what happened and what to do, then "Continue". A cancel
is checked between the sub-steps and by each runner before each of its calls
(and during ffmpeg, by the render's runner); it raises ``Cancelled``. Every
sub-step and every auto-approval is a line in the activity feed. On success
the job ends ``completed`` (``steps.ends_completed``, DEC-161) and returns
``{ep, storyboard, steps{script, storyboard, paid_check, assets, render,
metadata}, auto_approved[...], seconds}``.

:func:`estimate` is what the whole run would do and spend now, calling
nothing (the fast-track estimate, ``GET /estimate/fast-track``).

The approvals are the workflow's own rules (one set of rules, DEC-114), so
``workflow`` is imported where it is used, never at load: ``workflow`` may
import this module. ``story.json`` is never written (RC-E2).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import dataclasses
import subprocess
import time

from .. import media_policy
from .. import store as store_mod
from .. import timing, voices
from . import assets as assets_step
from . import clips as clips_step
from . import episode_common, llm_call, voice_lines
from . import judge as judge_step
from . import metadata as metadata_step
from . import render as render_step
from . import script as script_step
from . import storyboard as storyboard_step
from .episode_common import SCRIPT_DOC, STORYBOARD_DOC
from .llm_call import StepFailed

STEP = "fast-track"
ASSETS_DOC = store_mod.EPISODE_ASSETS_DOC

# The step's one parameter: how the shots are planned.
STORYBOARD_PARAM = "storyboard"
T1, FAST = storyboard_step.T1, storyboard_step.FAST
STORYBOARD_CHOICES = (T1, FAST)
PARAMS = (STORYBOARD_PARAM,)

# A-076: one hour holds a free-chain episode (about 14 LLM calls, two dozen
# images, a voice per line, a render of a few minutes, 3 M1 calls), and
# never holds the worker slot a clip render waits for longer than that.
FAST_TRACK_BUDGET_SECONDS = 3600

# The sub-steps, in order, and how the feed names them.
SUB_STEPS = ("script", "storyboard", "paid_check", "assets", "render", "metadata")
LABELS = {"script": "script", "storyboard": "storyboard", "paid_check": "paid check", "assets": "assets",
          "render": "render", "metadata": "metadata"}

# A script's timing states the auto-approval refuses (``timing.episode_pass``).
TIMING_REFUSED = ("over", "under")

# Render time, for the budget's predictive check and the estimate. An
# authored estimate until A-069 records the VPS's own numbers: the stage-0
# bench rendered a 3.0 s shot at 4x (libx264 medium) in 7.9 s, and a 10 s
# 1080x1920 final pass in 6.7 s -- so about 8 s a shot, plus about a minute
# for the mix, the final pass of a ~65 s episode, the loudness pass, the
# probe and the framemd5.
RENDER_SECONDS_PER_SHOT = 8.0
RENDER_TAIL_SECONDS = 60.0

# The metadata step asks M1 once per platform (DEC-166).
M1_CALLS = len(metadata_step.PLATFORMS)

# The paid verdicts (:func:`paid_verdict`).
FREE = "free"
PAID_WITHIN_CAPS = "paid_within_caps"
STOPS_BEFORE_PAID = "stops_before_paid"
BLOCKED = "blocked"
VERDICTS = (FREE, PAID_WITHIN_CAPS, STOPS_BEFORE_PAID, BLOCKED)


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


# ------------------------------------------------------------------- params

def read_params(params) -> dict:
    """``{"storyboard": "t1" | "fast"}`` from the step's params (default
    ``t1``); ``StepFailed`` for another key or value, naming the choices."""
    params = params or {}
    unknown = sorted(key for key in params if key not in PARAMS)
    if unknown:
        raise StepFailed(f"The fast track takes only {', '.join(PARAMS)}; not {', '.join(map(repr, unknown))}.")
    mode = params.get(STORYBOARD_PARAM)
    mode = T1 if mode is None else mode
    if mode not in STORYBOARD_CHOICES:
        raise StepFailed(f"The fast track's storyboard is one of {', '.join(STORYBOARD_CHOICES)}, not {mode!r}.")
    return {STORYBOARD_PARAM: mode}


# ------------------------------------------------------------------- rules

def script_refusal(script, ep, *, v2=False):
    """Why the fast track will not approve *script* (DEC-162), or None when
    it may: complete, its consistency report fresh and passed, its timing
    neither over nor under the template's window. Never approve-anyway: a
    report with issues is a refusal, whatever the issues.

    *v2* (``media_policy.is_v2`` of the story; DEC-230, DEC-231 part 2): the
    first-watch report (J1) must be fresh and passed too, and a length
    outside the window is never offered to the user to approve -- a v2
    episode outside its window is never approved."""
    if script is None or not script_step.is_complete(script, ep):
        return (f"Episode {ep}'s script is not complete: run it again (the fast track fills what is missing).")
    report = script.get("consistency_report")
    if report is None or script_step.needs_check(script):
        return (f"Episode {ep}'s consistency check (E4) has not run on this revision of the script: run it again "
                "(the fast track checks it first).")
    if not report["passed"]:
        count = len(report["issues"])
        issues = "; ".join(f"{issue['scene_id'] or 'the episode'} ({issue['kind']}): {issue['fix']}"
                           for issue in report["issues"])
        return (f"Episode {ep}'s consistency check found {count} issue{_s(count)}"
                f"{': ' + issues if issues else ''}. The fast track never approves over issues: fix them (edit the "
                "script, or regenerate the scenes they name) so the check passes, or approve the script anyway "
                "yourself.")
    if v2:
        watch = judge_step.first_watch_state(script)
        if watch in ("none", "stale"):
            return (f"Episode {ep}'s first-watch check (J1) has not run on this revision of the script: run it "
                    "again (the fast track checks it first).")
        if watch == "issues":
            report = script[judge_step.FIRST_WATCH]
            count = len(report["issues"])
            issues = "; ".join(f"{issue['scene_id'] or 'the episode'} ({issue['kind']}): {issue['fix']}"
                               for issue in report["issues"])
            return (f"Episode {ep}'s first-watch check (J1) found {count} issue{_s(count)}"
                    f"{': ' + issues if issues else ''}. The fast track never approves over issues: fix them (edit "
                    "the script, or regenerate the scenes they name) so the check passes, or approve the script "
                    "anyway yourself.")
    state = (script.get("timing") or {}).get("state")
    if state in TIMING_REFUSED:
        how = "shorten" if state == "over" else "lengthen"
        if v2:
            fill = (" -- run the script step again: its fill pass lengthens the shortest scenes --"
                    if state == "under" else "")
            return (f"Episode {ep}'s script is {state} its length window: {episode_common.timing_line(script)}. A "
                    f"v2 episode is never approved outside its window: {how} it{fill} (edit it, or regenerate a "
                    "scene).")
        return (f"Episode {ep}'s script is {state} its length window: {episode_common.timing_line(script)}. The "
                f"fast track approves only a script inside it: {how} it (edit it, or regenerate a scene), or "
                "approve it yourself.")
    return None


def keyframes_wait(ec):
    """Why the fast track stops for the keyframe approval (phase 7 stage 6b,
    DEC-230), or None: a v2 episode at tier >= 2 buys no clip before its
    keyframes are approved (``assets.clip_hold``), and that approval is the
    human's, never the fast track's -- so it never auto-approves assets whose
    clips are held. A legacy episode, or a tier-1 one, never waits."""
    if not media_policy.is_v2(ec.story) or clips_step.tier_of(ec) < 2:
        return None
    board = episode_common.read_episode(ec, STORYBOARD_DOC)
    doc = episode_common.read_episode(ec, ASSETS_DOC)
    return assets_step.clip_hold(ec, board, doc) if board else None


def _caps_line(caps) -> str:
    parts = [f"{label} ${caps[name]['spent_usd']:.2f} of ${caps[name]['cap_usd']:.2f}"
             for name, label in (("episode", "this episode"), ("day", "today"), ("story", "this story"))
             if name in caps]
    return f"Caps: {_and(parts)}." if parts else ""


def paid_verdict(units, *, ep, predicted=False, fully_animated=False) -> dict:
    """The fast track's paid check on an assets estimate (*units*:
    ``assets.asset_units``' shape; *predicted*: the counts are an upper
    bound, :func:`estimate`'s)::

        {"verdict": "free" | "paid_within_caps" | "stops_before_paid" | "blocked",
         "paid": bool, "allow_paid": bool, "est_usd": x, "parts": [sentence, ...],
         "caps": {...}, "over_cap": sentence | None, "stop": sentence | None, "message": sentence}

    A **paid part** -- shot images whose first runnable link is paid, or
    whose only way is a paid link that is refused; a line voice pinned to a
    paid link -- needs ``allow_paid`` on (else ``stops_before_paid``) and
    must fit under every cap, the episode's included (else
    ``stops_before_paid``, with the budget's refusal); anything else that
    cannot run (no image link, a line with no voice, a local editor not
    ready) is ``blocked``. ``stop`` is the sentence the fast track stops
    with -- the numbers, the caps, "nothing was generated or spent" and
    what to do -- or None when it may go on. The episode's image link gone
    for now (``images.sticky.gone``, A-087) stops with its own offer --
    ``stops_before_paid`` for a paid link the gates refuse, else
    ``blocked``.

    Phase 6 stage 7: at tier >= 2 the clips to buy (``units["video"]``, the
    paid ones on a hosted link) are a paid part as a paid image is -- priced
    even while ``allow_paid`` is off, so the check stops on them with their
    numbers; clips that cannot be planned at all are a blocker. A tier-1
    estimate has no ``video``: its verdict is unchanged; nor does one made
    with ``animate`` off count its clips (stage 8).

    *fully_animated* (``media_policy.fully_animated`` of the story, DEC-236):
    every shot must be a clip, so the stop never offers keeping shots still
    or animate off as a way out of the clips' cost."""
    images, voices_est, caps = units["images"], units["voices"], units.get("caps") or {}
    allow = bool(caps.get("allow_paid"))
    upto = "up to " if predicted else ""
    parts, refused, blockers = [], [], []
    images_usd = 0.0
    count = images.get("count") or 0
    if count:
        what = f"{upto}{count} shot image{_s(count)}"
        if images.get("route_class") == "paid":
            images_usd = float(images["est_usd"] or 0.0)
            parts.append(f"{what} on {images['link']} (est ${images_usd:.3f})")
        elif not images.get("ready"):
            # A paid link the gates refuse (allow_paid off, or a cap) is the
            # paid way the images would take; any other reason is a blocker.
            paid_rows = [row for row in images.get("links") or []
                         if row.get("paid") and str(row.get("reason") or "").startswith("refused")]
            if paid_rows:
                row = paid_rows[0]
                images_usd = float(row.get("est_usd") or 0.0)
                parts.append(f"{what} on {row['link']} (est ${images_usd:.3f})")
                refused.append(f"{row['link']}: {row['reason']}")
            else:
                blockers.append(images.get("message") or "the shot images cannot be made")
    voice_rows = voices_est.get("voices") or []
    for row in voice_rows:
        if row["paid"]:
            who = _and(row.get("speakers") or [])
            parts.append(f"{upto}{row['chars']} characters on {row['voice']}{' for ' + who if who else ''} "
                         f"(est ${row['est_usd']:.3f})")
            if not row["allowed"]:
                reason = row.get("reason") or "cannot run"
                (refused if reason.startswith("refused") else blockers).append(f"{row['voice']}: {reason}")
        elif not row["allowed"]:
            blockers.append(f"{row['voice']}: {row.get('reason') or 'cannot run'}")
    for item in voices_est.get("unvoiced") or []:
        blockers.append(f"{item.get('line_id') or item.get('speaker')}: {item['reason']}")
    voices_usd = voices_est.get("paid_usd")
    if voices_usd is None:
        voices_usd = sum(float(row["est_usd"] or 0.0) for row in voice_rows if row["paid"])
    video = units.get("video")
    if video is not None and not video.get("animate", True):
        video = None  # animate off (phase 6 stage 8): no clip is made, none is counted
    video_usd = 0.0
    if video is not None:
        clips = video.get("count") or 0
        if clips and video.get("route_class") == "paid":
            video_usd = float(video["est_usd"] or 0.0)
            parts.append(f"{upto}{clips} clip{_s(clips)} ({video['seconds']} s) on {video['link']} "
                         f"(est ${video_usd:.3f})")
            if not video.get("ready") and video.get("refused"):
                refused.append(f"{video['link']}: {video['refused']}")
        elif not video.get("ready"):
            blockers.append(video.get("message") or "the clips cannot be made")
    total = round(images_usd + float(voices_usd) + video_usd, 4)
    # Phase 6 stage 11: paid clips have their own way out.
    clips_way = ("; for the clips, keep their shots still (the assets edit's keep_still) or run the assets step "
                 "with animate off" if video_usd and not fully_animated else "")
    over = units.get("over_cap")
    caps_line = _caps_line(caps)

    head = f"Episode {ep}'s assets"
    stop = None
    gone = (images.get("sticky") or {}).get("gone") if count else None
    if gone:
        verdict = STOPS_BEFORE_PAID if gone["paid"] else BLOCKED
        stop = gone["message"]
    elif parts and not allow:
        verdict = STOPS_BEFORE_PAID
        stop = (f"{head} need paid generation -- {_and(parts)}, est {upto}${total:.3f} in all -- and allow_paid is "
                f"off. {caps_line} Nothing was generated or spent: turn allow_paid on in Settings (the episode, day "
                f"and story caps must all fit), or choose free links for the images and the voices{clips_way}.")
    elif parts and (over or refused):
        verdict = STOPS_BEFORE_PAID
        why = over or "; ".join(refused)
        stop = (f"{head} would go over a cap -- {_and(parts)}, est {upto}${total:.3f} in all: {why}. {caps_line} "
                f"Nothing was generated or spent: raise that cap in Settings, or choose free links{clips_way}.")
    elif blockers or not units.get("ready", True):
        verdict = BLOCKED
        stop = (f"{head} cannot be made as the chains stand: {'; '.join(blockers) or 'they are not ready'}. "
                "Nothing was generated or spent: fix that first.")
    elif parts:
        verdict = PAID_WITHIN_CAPS
    else:
        verdict = FREE
    stop = " ".join(stop.split()) if stop else None

    if stop is not None:
        message = stop
    elif verdict == PAID_WITHIN_CAPS:
        message = (f"{head}: paid within every cap -- {_and(parts)}, est {upto}${total:.3f}. {caps_line}").strip()
    else:
        chars = voices_est.get("chars") or 0
        made = []
        if count:
            made.append(f"{upto}{count} shot image{_s(count)} on {images.get('link') or 'the image chain'}")
        if voice_rows and chars:
            made.append(f"{upto}{chars} characters on {_and(dict.fromkeys(row['voice'] for row in voice_rows))}")
        if video is not None and video.get("count"):
            made.append(f"{upto}{video['count']} clip{_s(video['count'])} on {video['link']}")
        message = (f"{head}: nothing paid -- {_and(made)}, $0.00." if made
                   else f"{head}: nothing to generate, $0.00.")
    return {"verdict": verdict, "paid": bool(parts), "allow_paid": allow, "est_usd": total, "parts": parts,
            "caps": caps, "over_cap": over, "stop": stop, "message": message}


def render_seconds(shots) -> float:
    """The render's predicted wall time for *shots* shots (the authored
    per-shot estimate, see :data:`RENDER_SECONDS_PER_SHOT`)."""
    return round(RENDER_SECONDS_PER_SHOT * max(0, int(shots)) + RENDER_TAIL_SECONDS, 1)


# ------------------------------------------------------------- the documents

def assets_current(ec, script, board, doc) -> bool:
    """Whether the episode's assets are approved with the fingerprint of
    the files as they are now (nothing to make, nothing to approve)."""
    approved = (doc or {}).get("approved")
    return bool(approved) and assets_step.current_fingerprint(ec, board, script, doc) == approved["fingerprint"]


def fast_board_current(ec, board, script) -> bool:
    """Whether *board* is already the fast plan of *script*: it covers it,
    every scene planned fast from its current revision, no prompt outdated.
    Rebuilding it would change nothing but its revision."""
    if board is None or not board["shots"] or not episode_common.covers(board, script):
        return False
    if storyboard_step.stale_scenes(board, script):
        return False
    if any(entry.get("source") != FAST for entry in board["scenes"].values()):
        return False
    return not _workflow().outdated_entities(board, ec.entities)


# -------------------------------------------------------------------- the run

class _FastTrack:
    """One run: the step's context, its seams, its budget, and what it did."""

    def __init__(self, ctx, *, runner, time_fn, adapters, transport, sleep_fn, transcribe, run_process, popen,
                 clock, detect, cover_process, custom_fonts_dir, profile):
        self.ctx = ctx
        self.params = read_params(ctx.params)
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
        self.cover_process = cover_process if cover_process is not None else run_process
        self.custom_fonts_dir = custom_fonts_dir
        self.profile = profile
        self.budget = episode_common.Budget(time_fn, limit=FAST_TRACK_BUDGET_SECONDS)
        self.auto_approved = []

    # ------------------------------------------------------------ plumbing

    def log(self, line) -> None:
        self.ctx.on_log(line)

    def context(self):
        return episode_common.load_episode_context(self.ctx)

    def sub(self, step, params=None):
        """The context a runner is called with: this job's, under its own
        step name and params (none of the fast track's)."""
        return dataclasses.replace(self.ctx, step=step, params=dict(params or {}))

    def approve(self, ec, what, call, detail) -> None:
        """Approve *what* by the workflow's own rule (*call(workflow, now)*);
        its refusal is a stop."""
        workflow = _workflow()
        try:
            call(workflow, llm_call.utc_now())
        except (workflow.WorkflowError, workflow.StoryUnreadable) as exc:
            raise StepFailed(f"Episode {ec.ep}'s {what} could not be approved: {exc}") from None
        self.auto_approved.append(what)
        self.log(f"✅ Fast track: episode {ec.ep}'s {what} auto-approved ({detail})")

    def stopped(self, number, name, exc) -> StepFailed:
        message = " ".join(str(exc).split())
        if message and message[-1] not in ".!?":
            message += "."
        return StepFailed(
            f"Fast track stopped at the {LABELS[name]} (step {number} of {len(SUB_STEPS)}): {message} Then "
            "Continue the fast track: it picks up here and repeats nothing already done.",
            reason=getattr(exc, "reason", message))

    # ----------------------------------------------------------- sub-steps

    def script(self) -> dict:
        ec = self.context()
        script = episode_common.read_episode(ec, SCRIPT_DOC)
        if script is not None and script["approved_at"]:
            self.log(f"📄 Episode {ec.ep}'s script is approved already: kept as it is.")
            return {"kept": True, "calls": 0}
        summary = script_step.run(self.sub("script"), runner=self.runner, time_fn=self.time_fn,
                                  budget=self.budget)
        ec = self.context()
        script = episode_common.read_episode(ec, SCRIPT_DOC)
        refusal = script_refusal(script, ec.ep, v2=media_policy.is_v2(ec.story))
        if refusal:
            raise StepFailed(refusal)
        self.approve(ec, "script",
                     lambda workflow, now: workflow.approve_script(ec.store, ec.story_id, ec.ep, approve_anyway=False,
                                                                   now=now),
                     f"complete, consistency passed, {episode_common.timing_line(script)}")
        return dict(summary, kept=False)

    def storyboard(self) -> dict:
        ec = self.context()
        board = episode_common.read_episode(ec, STORYBOARD_DOC)
        if board is not None and board["approved_at"]:
            self.log(f"🎞 Episode {ec.ep}'s storyboard is approved already: kept as it is.")
            return {"kept": True, "calls": 0}
        mode = self.params[STORYBOARD_PARAM]
        if mode == FAST:
            script = storyboard_step.require_complete_script(ec)
            if fast_board_current(ec, board, script):
                self.log(f"🎞 Episode {ec.ep}'s fast storyboard is current: kept as it is.")
            else:
                try:
                    board = storyboard_step.build_fast(ec.store, ec.story_id, ec.ep, now=llm_call.utc_now(),
                                                       on_log=self.log)
                except ValueError as exc:
                    raise StepFailed(f"Episode {ec.ep}'s fast storyboard could not be built: {exc}") from None
            summary = {"ep": ec.ep, "mode": FAST, "calls": 0, "shots": len(board["shots"])}
        else:
            summary = dict(storyboard_step.run(self.sub("storyboard"), runner=self.runner, time_fn=self.time_fn,
                                               budget=self.budget), mode=T1)
            summary["calls"] = len(summary.get("planned") or [])
        ec = self.context()
        self.approve(ec, "storyboard",
                     lambda workflow, now: workflow.approve_storyboard(ec.store, ec.story_id, ec.ep, now=now),
                     f"{summary['shots']} shots, {mode}")
        return dict(summary, kept=False)

    def paid_check(self) -> dict:
        ec = self.context()
        script, board = assets_step.require_approved(ec)
        doc = episode_common.read_episode(ec, ASSETS_DOC)
        if assets_current(ec, script, board, doc):
            self.log(f"💲 Episode {ec.ep}'s assets are approved and current: nothing to generate or pay for.")
            return {"kept": True, "verdict": FREE, "est_usd": 0.0}
        # A local editor is asked whether it is there (the DEC-117 status
        # probe, never a generation), as the assets step itself will: the
        # verdict is on the link that would really run.
        units = assets_step.asset_units(ec, script, board, env=self.ctx.settings_env, adapters=self.adapters,
                                        transport=self.transport, probe_local=True)
        verdict = paid_verdict(units, ep=ec.ep, fully_animated=media_policy.fully_animated(ec.story))
        if verdict["stop"]:
            raise StepFailed(verdict["stop"])
        self.log(f"💲 {verdict['message']}")
        return {"kept": False, "verdict": verdict["verdict"], "est_usd": verdict["est_usd"],
                "images": units["images"]["count"], "lines": units["voices"]["lines"]}

    def assets(self) -> dict:
        ec = self.context()
        script, board = assets_step.require_approved(ec)
        doc = episode_common.read_episode(ec, ASSETS_DOC)
        if assets_current(ec, script, board, doc):
            self.log(f"🖼 Episode {ec.ep}'s assets are approved and current: kept as they are.")
            return {"kept": True, "made": 0}
        summary = assets_step.run(self.sub("assets"), adapters=self.adapters, transport=self.transport,
                                  time_fn=self.time_fn, sleep_fn=self.sleep_fn, transcribe=self.transcribe,
                                  budget=self.budget)
        if summary["failed"] or not summary["complete"]:
            failures = summary["failed"]
            if failures:
                what = "; ".join(f"{item['what']} failed ({item['reason']})" for item in failures)
                targets = _and(f"'{item['target']}'" for item in failures if item.get("target"))
                # A clip still generating has no target (phase 6 stage 8): running again collects it.
                redo = f"regenerate {targets}, or " if targets else ""
                raise StepFailed(f"Episode {ec.ep}'s assets are not complete: {what}. Every other image and voice "
                                 f"is kept: {redo}run the fast track again to ask only for what is missing.")
            raise StepFailed(f"Episode {ec.ep}'s assets are not complete (not every shot has a current image and "
                             "every line a voice): run the fast track again to make what is missing.")
        ec = self.context()
        wait = keyframes_wait(ec)
        if wait:
            raise StepFailed(f"Episode {ec.ep}'s keyframes are made and checked (J2), and wait for you: {wait}. "
                             "Look at each keyframe and its check on the storyboard, then Approve keyframes (or "
                             "approve anyway); the clips are bought after that.")
        self.approve(ec, "assets",
                     lambda workflow, now: workflow.approve_assets(ec.store, ec.story_id, ec.ep, now=now),
                     f"{summary['shots']['total']} shots current or locked, every line voiced; fingerprint "
                     f"{summary['fingerprint'][:12]}")
        return dict(summary, kept=False)

    def render(self) -> dict:
        ec = self.context()
        if render_step.current_render(ec, None, profile=self.profile, custom_fonts_dir=self.custom_fonts_dir):
            self.log(f"🎬 Episode {ec.ep}'s render is current (the same inputs, commands and text): kept as it is.")
            return {"kept": True, "ran": [], "cached": []}
        board = episode_common.read_episode(ec, STORYBOARD_DOC)
        seconds = render_seconds(len(board["shots"]) if board else 0)
        self.budget.before_call(lambda: f"the render (about {seconds / 60:.0f} min), then the metadata "
                                        f"({M1_CALLS} M1 calls)", per_call=seconds)
        summary = render_step.run(self.sub("render"), profile=self.profile, run_process=self.run_process,
                                  popen=self.popen, clock=self.clock, detect=self.detect,
                                  custom_fonts_dir=self.custom_fonts_dir)
        return dict(summary, kept=False)

    def metadata(self) -> dict:
        return metadata_step.run(self.sub("metadata"), runner=self.runner, time_fn=self.time_fn,
                                 run_process=self.cover_process, custom_fonts_dir=self.custom_fonts_dir,
                                 budget=self.budget)

    # ------------------------------------------------------------------ run

    def run(self) -> dict:
        ctx = self.ctx
        ec = self.context()
        # The memory gate while it would write the script or the storyboard
        # (plan 11 stage 4); their own runners meet it again when they run.
        gated = episode_common.needs_memory(ec, STEP)
        # And, on a v2 story, the knowledge gate while it would write the
        # script (phase 7 stage 5b, DEC-228).
        episode_common.check_episode_preconditions(ctx, ec, require_memory=gated,
                                                   require_knowledge=episode_common.needs_knowledge(ec, STEP))
        ctx.cancel.check()
        mode = self.params[STORYBOARD_PARAM]
        self.log(f"⏩ Fast track of episode {ec.ep}: script → storyboard ({mode}) → paid check → assets → render → "
                 f"metadata, under a {FAST_TRACK_BUDGET_SECONDS // 60}-minute budget")
        results = {}
        for number, name in enumerate(SUB_STEPS, start=1):
            ctx.cancel.check()
            self.log(f"⏩ Fast track {number}/{len(SUB_STEPS)}: {LABELS[name]}")
            try:
                results[name] = getattr(self, name)()
            except StepFailed as exc:
                raise self.stopped(number, name, exc) from None
        seconds = round(self.budget.elapsed(), 1)
        approved = f"; auto-approved: {_and(self.auto_approved)}" if self.auto_approved else ""
        self.log(f"🏁 Fast track done: episode {ec.ep} is rendered with its metadata pack "
                 f"({seconds / 60:.1f} min{approved}).")
        return {"ep": ec.ep, "storyboard": mode, "steps": results, "auto_approved": list(self.auto_approved),
                "seconds": seconds}


def run(ctx, *, runner=None, time_fn=time.monotonic, adapters=None, transport=None, sleep_fn=time.sleep,
        transcribe=None, run_process=subprocess.run, popen=subprocess.Popen, clock=time.monotonic, detect=None,
        cover_process=None, custom_fonts_dir=None, profile="final") -> dict:
    """The step (module docstring). Every keyword is a seam for tests, handed
    to the runner that uses it: *runner* (the LLM chain: script, T1, M1),
    *time_fn* (the budget and the LLM/image deadlines), *adapters*,
    *transport*, *sleep_fn*, *transcribe* (the assets), *run_process* (the
    render's pre-flight), *popen*, *clock*, *detect* (the render's runner),
    *cover_process* (the cover's ffmpeg; *run_process* when None),
    *custom_fonts_dir*, *profile* (``final`` for a job)."""
    return _FastTrack(ctx, runner=runner, time_fn=time_fn, adapters=adapters, transport=transport,
                      sleep_fn=sleep_fn, transcribe=transcribe, run_process=run_process, popen=popen, clock=clock,
                      detect=detect, cover_process=cover_process, custom_fonts_dir=custom_fonts_dir,
                      profile=profile).run()


# ----------------------------------------------------------------- estimate

def predicted_scenes(ec, script) -> int:
    """The scenes of the episode: the script's own, else the ones E1's exact
    ask will write (``timing.episode_slots``, DEC-145)."""
    if script and script["scenes"]:
        return len(script["scenes"])
    return len(timing.episode_slots(ec.template, ec.ep))


def _predicted_voices(ec, chars, *, env, adapters) -> dict:
    """``voice_lines.measure_estimate``'s shape for an episode not written
    yet: every distinct voice of the cast (and the narrator) priced for all
    *chars* characters -- the upper bound: however the lines fall, no voice
    speaks more than all of them -- and ``paid_usd`` the dearest paid one."""
    speakers = [doc["char_id"] for doc in ec.cast] + (["narrator"] if ec.narrator else [])
    items, unvoiced, seen = [], [], set()
    for speaker in speakers:
        voice = voice_lines.speaker_voice(ec, speaker)
        label = voices.voice_label(voice)
        if label is None:
            unvoiced.append({"line_id": None, "speaker": speaker, "reason": voice_lines.no_voice_reason(ec, speaker)})
            continue
        if label in seen:
            continue
        seen.add(label)
        items.append((voice, "x" * chars, voice_lines.speaker_name(ec, speaker)))
    verdict = voices.estimate_lines(ec.store, ec.story_id, items, env=env, ep=ec.ep, adapters=adapters)
    paid = [row["est_usd"] for row in verdict["voices"] if row["paid"]]
    return {"lines": 0, "chars": chars, "est_usd": max(paid, default=0.0), "paid_usd": max(paid, default=0.0),
            "voices": verdict["voices"], "unvoiced": unvoiced, "paid_links": verdict["paid_links"],
            "allow_paid": verdict["allow_paid"], "free_tier": verdict["free_tier"],
            "ready": verdict["ready"] and not unvoiced}


def predicted_chars(ec) -> int:
    """The most characters an episode can speak: the top of the template's
    length window at the language's reading rate (``timing.RATE_PER_CHAR``)."""
    return int(round(float(ec.template["window_s"][1]) / timing.RATE_PER_CHAR[ec.language]))


def estimate(ec, *, env, storyboard=T1, adapters=None, transport=None, custom_fonts_dir=None) -> dict:
    """What the fast track of episode *ec.ep* would do and spend now,
    calling nothing (``GET /estimate/fast-track``)::

        {"ep", "storyboard": "t1" | "fast",
         "llm_calls": {"script", "storyboard", "metadata", "total",
                       "script_breakdown": {"E1", "E2", "E3", "E4"}},
         "images": {"count", "exact", "route_class", "link", "est_usd", "ready", "message"},
         "tts": {"lines", "chars", "exact", "est_usd", "voices": [...]},
         "video": <the assets estimate's video part> (tier >= 2 only),
         "render": {"needed", "shots", "seconds", "minutes", "basis"},
         "est_usd": x, "paid": <paid_verdict>, "stops_at": {"step", "reason"} | None}

    **LLM calls** are exact (DEC-145): the script's missing calls
    (``workflow.script_units``: E1's exact ask, 1 + N + 1 + 1, for a new
    episode), T1 once per scene to plan (0 for ``fast``, or for an approved
    storyboard), and one M1 per platform still to write. **Images and
    TTS** are priced on their routes: exactly (the shots of an approved
    storyboard neither locked nor current; the lines of a complete script
    not measured) or, before those exist, as an upper bound (every scene at
    the top of ``shots_per_scene``; the top of the template's window at the
    language's reading rate, on the dearest voice) -- ``exact`` says which.
    **Clips** (tier >= 2, phase 6 stage 11): the assets estimate's own video
    part on the approved storyboard (``assets._video_units``), in the paid
    part and the total as the run's paid check counts them; before the
    storyboard is approved, ``video.count`` is None and nothing is priced.
    **Render** minutes from :func:`render_seconds` (an authored estimate
    until A-069), 0 when the last render is current. ``est_usd`` is the paid
    part (a paid link the gates would refuse included: the price of the
    plan, not of what would run); ``paid`` is :func:`paid_verdict` --
    ``stops_before_paid`` is what the fast track would stop with before any
    generation call. ``stops_at`` names a stop the documents already show
    (a script the auto-approval refuses, the paid check). LLM calls are on free links
    unless ``allow_paid`` says otherwise (DEC-115): priced at $0 here, as
    the other LLM estimates are. ``StepFailed`` for a document that does not
    validate; the workflow's ``WorkflowError`` for what ``script_units``
    refuses."""
    workflow = _workflow()
    mode = read_params({STORYBOARD_PARAM: storyboard})[STORYBOARD_PARAM]
    ep = ec.ep
    script = episode_common.read_episode(ec, SCRIPT_DOC)
    board = episode_common.read_episode(ec, STORYBOARD_DOC)
    doc = episode_common.read_episode(ec, ASSETS_DOC)
    script_approved = bool(script and script["approved_at"])
    board_approved = bool(board and board["approved_at"])
    stops_at = None

    # --- LLM calls
    if script_approved:
        breakdown = {"E1": 0, "E2": 0, "E3": 0, "E4": 0}
    else:
        units = workflow.script_units(ec)
        breakdown = {key: units[key] for key in ("E1", "E2", "E3", "E4")}
        if script is not None and script_step.is_complete(script, ep) and not script_step.needs_check(script):
            refusal = script_refusal(script, ep)
            if refusal:
                stops_at = {"step": "script", "reason": refusal}
    script_calls = sum(breakdown.values())
    if board_approved or mode == FAST:
        t1_calls = 0
    elif script and script["scenes"]:
        t1_calls = workflow.storyboard_units(ec, env=env)["t1_calls"]
    else:
        t1_calls = predicted_scenes(ec, script)

    # --- images and voices
    ledger = assets_step._open_ledger(ec)
    story_spent = float(ledger.totals()["est_usd"])
    approved_current = bool(script_approved and board_approved and doc is not None
                            and assets_current(ec, script, board, doc))
    if board_approved and script_approved:
        to_make = assets_step.shots_to_make(ec, board) if not approved_current else []
        count, images_exact, shots_total = len(to_make), True, len(board["shots"])
    else:
        scenes = predicted_scenes(ec, script)
        count = scenes * int(ec.episode_defaults["shots_per_scene"][1])
        images_exact, shots_total = False, count
    if count:
        # The shots of an approved storyboard are priced on the episode's
        # image link when it has one (A-087), as the assets step asks them.
        link_info = assets_step.episode_image_link(ec, board, env=env, doc=doc) if images_exact else None
        quote = assets_step.image_quote(ec, count, env=env, story_spent=story_spent, adapters=adapters,
                                        transport=transport, storyboard=board if images_exact else None,
                                        link_info=link_info)
    else:
        quote = {"est_usd": 0.0, "route_class": None, "link": None, "links": [], "ready": True,
                 "message": "Every shot has its image."}
    images = {"count": count, "exact": images_exact, "route_class": quote["route_class"], "link": quote["link"],
              "est_usd": float(quote["est_usd"] or 0.0), "links": quote["links"], "ready": quote["ready"],
              "message": quote["message"]}
    if quote.get("sticky") is not None:
        images["sticky"] = quote["sticky"]
    if script is not None and script_step.is_complete(script, ep):
        voices_est = voice_lines.measure_estimate(ec, script, env=env, adapters=adapters)
        tts_exact = True
    else:
        voices_est = _predicted_voices(ec, predicted_chars(ec), env=env, adapters=adapters)
        tts_exact = False
    images_paid = images["est_usd"] if images["route_class"] == "paid" else 0.0
    voices_paid = voices_est.get("paid_usd")
    if voices_paid is None:
        voices_paid = sum(row["est_usd"] for row in voices_est["voices"] if row["paid"])
    # Phase 6 stage 11: at tier >= 2 the clips the assets step would animate
    # (its estimate's own video part, on the approved storyboard: the paid
    # check prices them the same way before any is bought); before a
    # storyboard is approved they cannot be planned yet.
    video, video_paid = None, 0.0
    if clips_step.tier_of(ec) >= 2:
        if images_exact:
            video = assets_step._video_units(ec, script, board, doc, env=env, ledger=ledger, adapters=adapters,
                                             probe_local=False, transport=transport,
                                             committed=images_paid + float(voices_paid))
            video["animate"] = True
            if video["route_class"] == "paid" and video["count"] and video["ready"]:
                video_paid = video["est_usd"]
        else:
            video = {"tier": clips_step.tier_of(ec), "count": None, "seconds": None, "est_usd": 0.0, "plan": [],
                     "route_class": None, "link": None, "ready": True, "refused": None, "animate": True,
                     "message": ("The clips are planned once the storyboard is approved; the paid check prices "
                                 "them before any is bought.")}
    total = round(images_paid + float(voices_paid) + video_paid, 4)
    caps, over_cap = assets_step.spending_caps(ec, total, env=env, ledger=ledger,
                                               video=video if video_paid else None)
    units = {"images": images, "voices": voices_est, "caps": caps, "est_usd": total, "over_cap": over_cap,
             "ready": images["ready"] and voices_est["ready"] and over_cap is None}
    if video is not None and video["count"] is not None:
        units["video"] = video
        units["ready"] = bool(units["ready"] and video["ready"])
    verdict = paid_verdict(units, ep=ep, predicted=not (images_exact and tts_exact),
                           fully_animated=media_policy.fully_animated(ec.story))
    if stops_at is None and verdict["stop"]:
        stops_at = {"step": "paid_check", "reason": verdict["stop"]}

    # --- render and metadata
    render_current = approved_current and render_step.current_render(ec, None, custom_fonts_dir=custom_fonts_dir)
    seconds = 0.0 if render_current else render_seconds(shots_total)
    m1_calls = M1_CALLS
    if render_current:
        manifest = episode_common.read_episode(ec, render_step.MANIFEST_DOC)
        pack = episode_common.read_episode(ec, metadata_step.PACK_DOC)
        if metadata_step.is_current(pack, script, manifest["output"]["sha256"]):
            m1_calls = sum(1 for platform in metadata_step.PLATFORMS if platform not in pack["platforms"])

    return {
        "ep": ep, "storyboard": mode,
        "llm_calls": {"script": script_calls, "storyboard": t1_calls, "metadata": m1_calls,
                      "total": script_calls + t1_calls + m1_calls, "script_breakdown": breakdown},
        "images": images,
        "tts": {"lines": voices_est["lines"], "chars": voices_est["chars"], "exact": tts_exact,
                "est_usd": round(float(voices_paid), 4), "voices": voices_est["voices"]},
        **({"video": video} if video is not None else {}),
        "render": {"needed": not render_current, "shots": shots_total, "seconds": seconds,
                   "minutes": round(seconds / 60, 1),
                   "basis": (f"estimate: {RENDER_SECONDS_PER_SHOT:g} s a shot + {RENDER_TAIL_SECONDS:g} s "
                             "(stage-0 bench; A-069 records the measured times)")},
        "est_usd": verdict["est_usd"], "paid": verdict, "stops_at": stops_at,
    }
