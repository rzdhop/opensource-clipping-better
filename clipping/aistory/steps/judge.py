"""The judges of a v2 story (AI Story phase 7, stage 6a; A16, DEC-230).

**J1, the first watch** (:func:`check_first_watch`): after E4 in the script
step, one call on the story's writing chain (``llm_call.call_json``, the
analytic temperature) reads the episode as a viewer who knows only what it
shows and says -- and the previous episode's recap -- would on one watch,
and says what they took away (who wants what, what happens, why it
matters) and what kept them from following, from a closed list of kinds
(``schemas.FIRST_WATCH_ISSUE_KINDS``). Two deterministic checks run first,
with no call, and lead the same report, failing it whatever J1 says:

- a **repeated line** (:func:`duplicate_issues`): any two lines of the
  episode whose normalised-token Jaccard is at least 0.7
  (``prompts.near_duplicate``) -- story A's climax line was spoken twice
  verbatim, and a judge can miss it;
- **no hook text** (:func:`hook_text_issues`): the hook has no on-screen
  text (story B shipped one).

The report is ``script.json``'s optional ``first_watch`` (``{who_wants_what,
what_happens, why_it_matters, passed, issues, checked_rev, checked_at,
stale, version}``), stale like the consistency report: once the script is
rewritten (``episode_common.mark_changed``) or its revision moved
(:func:`needs_first_watch`). ``workflow.approve_script`` refuses a v2
script while the report is missing or stale (never approvable) or failed
(unless "approve anyway"): :func:`unjudged_refusal`,
:func:`issues_refusal`.

**J1 version 2** (``prompts.J1_PROMPT_VERSION``, DEC-248): J1 is told the
format (the episode's length and spoken words) and gives each issue a
severity, ``blocking`` or ``minor``; the deterministic checks' issues are
blocking. The report **passes exactly when no issue is blocking**
(:func:`blocking_issues`; an issue without a severity, a version-1 report's,
reads as blocking), so the minor ones are kept for the human to read and
neither the approval nor the fast track stops on them. After a repair pass
the call is a **re-check** (*previous*): it is shown the blocking issues the
pass tried to fix, and a blocking issue it names that is not one of them
(the same scene and kind) is kept as minor -- the blocking set can only
shrink, so the repair loop converges. An unapproved script whose report is
of an older version is judged again (:func:`needs_first_watch`); an
approved one keeps its report.

**J2, the keyframes** (:func:`check_keyframes`, stage 6b): in the assets
step of a v2 story, once the keyframes exist, one vision call per shot on
``VISION_CHAIN`` (free Gemini first; ``uploads.describe_upload``'s pattern:
every gate of the generation runner, each answered call booked in the
story's ledger, a reply that fails validation asked for once more) with the
shot's keyframe, the previous shot's keyframe and what the shot must show
(:func:`keyframe_brief`): ``{shows_beat, missing, continuity_issue}`` --
plus ``framing_issue`` when the keyframe's framing is not the one asked
(plan 19 stage 3; absent otherwise, and on every older verdict) -- stored per shot in ``assets.json``'s optional ``keyframe_verdicts`` with the
sha256 of both images. A verdict stays current while both images are the
ones it saw (:func:`verdict_current`): a shot judged already is never asked
again. A vision chain that cannot run skips J2 with a line in the feed.

Phase 8 stage B (J2 version 2, ``prompts.J2_PROMPT_VERSION``): J2 also
sees each on-screen character's identity sheet (:class:`KeyframeContext`),
reads each character as its look and the wardrobe set it wears in this
shot -- not its descriptor's first words -- and is told whether the
previous shot is in the same scene: across a scene change it compares only
who the characters are, never the set or the light. Each verdict carries
the version that judged it; one of an older version stays readable and is
asked again.

Plan 28 F2 (J2 version 3, DEC-305 §5): the sheet check is asked apart --
``sheet_issues``, one item per character that does not match its sheet and
its written look, the head, species, skin and material first, then the
outfit written for the shot -- and J2 also sees the set's plate and the
props in frame (:meth:`KeyframeContext.refs_of`, within
``prompts.J2_MAX_IMAGES``). A sheet issue fails the verdict
(:func:`verdict_passed`) and is said first (:func:`verdict_text`).

**The keyframe approval** (``workflow.approve_keyframes``):
``assets.json``'s ``keyframes_approved {at, anyway, fingerprint}``, the
fingerprint of the keyframe images (:func:`keyframes_fingerprint`): once
it differs, the approval is stale (:func:`keyframes_state`) -- derived,
never cleared (DEC-155's rule). No clip of a v2 episode is bought before it
is current (``assets.clip_hold``, RC-Q3). Plan 28 F1: a hard gate -- a
failed or missing verdict is refused in plain sentences
(:func:`keyframe_refusal`), with no "anyway".

A legacy story is never judged: nothing here runs for it, its script never
has ``first_watch`` and its ``assets.json`` neither key.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

from clipping.providers import adapters as adapters_mod
from clipping.providers import budget as budget_mod
from clipping.providers import gating, jsonx
from clipping.providers import generation as gen
from clipping.providers.registry import ChainError, describe

from .. import context, media_policy, prompting, prompts, schemas, shots, timing
from .. import ledger as ledger_mod
from . import episode_common, llm_call

FIRST_WATCH = "first_watch"
J1 = "J1"
# Plan 22 stage 3 (writing v3): the judge of a writing-v3 episode.
J1V3 = "J1v3"

# The deterministic repeated-line issues one report holds at most (the stored
# report holds 20; J1 adds at most 6, the hook check 1).
DUPLICATE_ISSUES_MAX = 6
# The kinds the deterministic checks find (they run before every J1 call).
DETERMINISTIC_KINDS = ("repeated_line", "no_hook_text")
# Plan 22 stage 3: the line checks a writing-v3 episode's J1 runs first --
# a line past the format's words a line (``line_too_long``) or short of them
# (``incomplete_sentence``), at most this many issues (the stored report
# holds 20: 6 repeated lines, 1 hook text, these 6 and J1's 6 fit).
LINE_ISSUES_MAX = 6

# Each first-watch issue kind in words, for the messages and the repair notes.
KIND_WORDS = {
    "unclear_goal": "unclear goal", "unmotivated": "unmotivated", "unintroduced": "unintroduced",
    "object_unseen": "object unseen", "repeated_line": "repeated line", "no_hook_text": "no hook text",
    # The consistency check's (E4) kinds, repaired too since DEC-260.
    "continuity": "continuity", "character": "out of character", "place": "place", "series_memory": "series memory",
    "hook_payoff": "hook payoff", "other": "consistency",
    # Plan 22 stage 3 (J1v3's kinds and the line-length check).
    "line_no_progress": "line adds nothing", "incomplete_sentence": "incomplete sentence",
    "logline_mismatch": "logline not told", "line_too_long": "line too long",
}

# Phase 7 follow-up, stage G: the script step's repair passes on the script
# (``script.repairs``, ``steps/script.py``), read by the refusals.
REPAIRS = "repairs"


def _and(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# ------------------------------------------------------------- the report

def writes_v3(story, script=None) -> bool:
    """Whether an episode of *story* is written and judged on writing v3
    (plan 22 stage 3): a v2 story whose ``generation_profile.writing`` is
    "v3" (``media_policy.writing_v3``) and -- given its *script* -- one
    whose beat sheet is E1v3's (``spine``) or not written yet. An episode
    beat-sheeted before the story turned v3 keeps the prompts and the judge
    it began with."""
    if not (media_policy.is_v2(story) and media_policy.writing_v3(story)):
        return False
    return script is None or not script.get("scenes") or script.get("spine") is not None


def j1_version(story, script=None) -> int:
    """The J1 version that judges *script* of *story*: 3 on writing v3
    (:func:`writes_v3`), else 2 (``prompts.J1_PROMPT_VERSION``)."""
    return prompts.J1_V3_PROMPT_VERSION if writes_v3(story, script) else prompts.J1_PROMPT_VERSION


def needs_first_watch(script, version=None) -> bool:
    """Whether *script*'s first-watch report is missing, marked stale, or of
    an older revision (the consistency report's rule,
    ``script.needs_check``) -- or, on a script not approved yet, judged by
    an older J1 (``version``, absent: 1; DEC-248) than *version* (the
    story's, :func:`j1_version`; None: ``prompts.J1_PROMPT_VERSION``): an
    approved script keeps the report it was approved on."""
    report = (script or {}).get(FIRST_WATCH)
    if report is None or report["stale"] or report["checked_rev"] != script["rev"]:
        return True
    wanted = prompts.J1_PROMPT_VERSION if version is None else version
    return not script.get("approved_at") and report.get("version", 1) < wanted


def is_blocking(issue) -> bool:
    """Whether a first-watch *issue* fails the check: a blocking one, or one
    without a severity (a version-1 report's: version 1 failed on any)."""
    return issue.get("severity", "blocking") == "blocking"


def blocking_issues(report) -> list:
    return [issue for issue in (report or {}).get("issues") or [] if is_blocking(issue)]


def minor_issues(report) -> list:
    return [issue for issue in (report or {}).get("issues") or [] if not is_blocking(issue)]


def first_watch_state(script, version=None) -> str:
    """``none`` | ``stale`` | ``passed`` | ``issues`` (``workflow.
    report_state``'s, for the first-watch report; *version* as
    :func:`needs_first_watch`'s)."""
    report = (script or {}).get(FIRST_WATCH)
    if report is None:
        return "none"
    if needs_first_watch(script, version):
        return "stale"
    return "passed" if report["passed"] else "issues"


def _count(count, what) -> str:
    return f"{count} {what} issue{'s' if count != 1 else ''}"


def first_watch_line(report) -> str:
    """``👀 First watch: passed`` -- with ``(2 minor issues)`` when it kept
    some -- or ``👀 First watch: 3 blocking issues, 1 minor``."""
    minor = len(minor_issues(report))
    if report["passed"]:
        return "👀 First watch: passed" + (f" ({_count(minor, 'minor')})" if minor else "")
    blocking = len(blocking_issues(report))
    return f"👀 First watch: {_count(blocking, 'blocking')}" + (f", {minor} minor" if minor else "")


# ------------------------------------------------------ deterministic checks

def duplicate_issues(script) -> list:
    """The repeated-line pre-check (module docstring): one ``repeated_line``
    issue per line that repeats an earlier line of the episode
    (``prompts.near_duplicate``), on the later line's scene, in script order,
    at most :data:`DUPLICATE_ISSUES_MAX`. The fixes are the app's own text
    (English, as the hook-payoff pre-check's), each within the stored
    report's 300 characters. Pure."""
    issues = []
    earlier = []  # [(line_id, text)]
    for scene in script["scenes"]:
        for line in scene["lines"]:
            for first_id, first_text in earlier:
                if prompts.near_duplicate(first_text, line["text"]):
                    issues.append({
                        "scene_id": scene["scene_id"], "kind": "repeated_line", "severity": "blocking",
                        "fix": (f"Line {line['line_id']} repeats line {first_id} "
                                f"({prompts.quoted_line(first_text)}): rewrite one of them."),
                    })
                    break
            earlier.append((line["line_id"], line["text"]))
            if len(issues) >= DUPLICATE_ISSUES_MAX:
                return issues
    return issues


def hook_text_issues(script, ep) -> list:
    """``[no_hook_text issue]`` when the hook has no on-screen text, else
    []. Pure."""
    if (script.get("hook") or {}).get("on_screen_text"):
        return []
    hook = next((scene for scene in script["scenes"] if scene["function"] == "hook"), None)
    return [{"scene_id": hook["scene_id"] if hook else None, "kind": "no_hook_text", "severity": "blocking",
             "fix": f"The hook has no on-screen text: write it again (regenerate hook:{ep}) so it states the "
                    "premise on screen."}]


def line_issues(script, template, *, native=False) -> list:
    """The line checks of a writing-v3 episode (plan 22 stage 3; pure): for
    each character line, in script order, at most :data:`LINE_ISSUES_MAX`
    issues --

    - a line past the format's words a line (``timing.line_words_v3``: the
      template's ``line_words``, else 17 on a native-speech story, else 22):
      blocking ``line_too_long`` -- one shot cannot speak it;
    - on a template with ``line_words``, a line under its low end: blocking
      ``incomplete_sentence``; on any other, a line under
      ``prompts.LINE_FLOOR_WORDS`` (3) words: minor ``incomplete_sentence``
      (an interjection a free format may keep).

    A narrator line is never checked here (it is heard over the picture)."""
    lo, hi = timing.line_words_v3(template, native=native)
    strict = bool((template or {}).get("line_words"))
    issues = []
    for scene in script["scenes"]:
        for line in scene["lines"]:
            if line["speaker"] == "narrator":
                continue
            count = len(line["text"].split())
            quoted = prompts.quoted_line(line["text"])
            if count > hi:
                issues.append({"scene_id": scene["scene_id"], "kind": "line_too_long", "severity": "blocking",
                               "fix": (f"Line {line['line_id']} {quoted} has {count} words; a line here holds at "
                                       f"most {hi}: say it shorter or split it in two lines.")})
            elif strict and count < lo:
                issues.append({"scene_id": scene["scene_id"], "kind": "incomplete_sentence", "severity": "blocking",
                               "fix": (f"Line {line['line_id']} {quoted} has {count} word{'s' if count != 1 else ''}; "
                                       f"a line here is a complete sentence of {lo} to {hi} words: write it whole.")})
            elif not strict and count < prompts.LINE_FLOOR_WORDS:
                issues.append({"scene_id": scene["scene_id"], "kind": "incomplete_sentence", "severity": "minor",
                               "fix": (f"Line {line['line_id']} {quoted} is a fragment: make it a sentence a person "
                                       "would say.")})
            if len(issues) >= LINE_ISSUES_MAX:
                return issues
    return issues


# ------------------------------------------------------------------ J1

def _objects(ec, script) -> list:
    """``[(prop name, [scene_id, ...])]``: each prop the episode shows once,
    with the scenes that show it, in order of first appearance."""
    shown = {}
    for scene in script["scenes"]:
        for prop_id in scene["props"]:
            shown.setdefault(prop_id, []).append(scene["scene_id"])
    names = []
    for prop_id, scene_ids in shown.items():
        doc = ec.entities["props"].get(prop_id)
        names.append(((doc or {}).get("name") or prop_id, scene_ids))
    return names


def spoken_words(script) -> int:
    """The words of every line of *script*, for J1's format sentence."""
    return sum(len(line["text"].split()) for scene in script["scenes"] for line in scene["lines"])


def check_first_watch(ctx, ec, script, *, tools, pack, previous=None) -> dict:
    """J1 over the whole *script* with the deterministic checks merged in
    (module docstring); the report is set on *script* (in place; the caller
    writes it) and returned. *pack* is the step's own
    (``context.build_pack``: the language). *previous*: the blocking issues
    a repair pass just tried to fix (a re-check, module docstring); a
    blocking issue of the reply that is not one of them is kept as minor."""
    v3 = writes_v3(ec.story, script)
    pre = duplicate_issues(script) + hook_text_issues(script, ec.ep)
    if pre:
        ctx.on_log(f"👀 Repeated lines and hook text: {len(pre)} issue{'s' if len(pre) != 1 else ''}, added to "
                   "the first-watch report")
    if v3:
        # Plan 22 stage 3: each character line's words against the format's.
        lines = line_issues(script, ec.template, native=media_policy.native_speech(ec.story))
        if lines:
            ctx.on_log(f"👀 Line lengths: {len(lines)} issue{'s' if len(lines) != 1 else ''}, added to the "
                       "first-watch report")
        pre += lines
    digest = prompts.script_digest(script, {
        "places": {pid: doc["name"] for pid, doc in ec.entities["places"].items()},
        "cast": ec.names,
    })
    recap = context.previous_recap(ec.season, ec.ep)
    seconds = (script.get("timing") or {}).get("total_s") or ec.template.get("target_s")
    previous = [{"scene_id": issue["scene_id"], "kind": issue["kind"], "fix": issue["fix"]}
                for issue in previous or []]
    # The deterministic checks run again on their own: J1 is shown its own kinds.
    deterministic = DETERMINISTIC_KINDS + (schemas.FIRST_WATCH_DETERMINISTIC_KINDS_V3 if v3 else ())
    shown = [issue for issue in previous if issue["kind"] not in deterministic]
    j1_kwargs = dict(ep=ec.ep, script_digest=digest, objects=_objects(ec, script),
                     hook_text=(script.get("hook") or {}).get("on_screen_text"),
                     reveal=script["cliffhanger"]["reveal"], previous_recap=recap, seconds=seconds,
                     words=spoken_words(script), previous_issues=shown or None)
    if v3:
        system, user, schema = prompts.build_j1_v3(pack, spine=script.get("spine"), **j1_kwargs)
    else:
        system, user, schema = prompts.build_j1(pack, **j1_kwargs)
    scene_ids = [scene["scene_id"] for scene in script["scenes"]]
    pre_kinds = {issue["kind"] for issue in pre}
    # Plan 22 stage 3: a scene the line check flagged a fragment in is not flagged again by the judge.
    pre_fragments = {issue["scene_id"] for issue in pre if issue["kind"] == "incomplete_sentence"}
    tried = {(issue["scene_id"], issue["kind"]) for issue in previous}

    def severity_of(issue):
        # A re-check keeps blocking only what the repair pass tried to fix.
        if issue["severity"] == "blocking" and previous and (issue["scene_id"], issue["kind"]) not in tried:
            return "minor"
        return issue["severity"]

    def report_of(reply, checked_at):
        # The model's own no_hook_text is left out when the check found it.
        found = [{"scene_id": issue["scene_id"], "kind": issue["kind"], "severity": severity_of(issue),
                  "fix": issue["fix"].strip()}
                 for issue in reply["issues"]
                 if not (issue["kind"] == "no_hook_text" and "no_hook_text" in pre_kinds)
                 and not (issue["kind"] == "incomplete_sentence" and issue["scene_id"] in pre_fragments)]
        issues = copy.deepcopy(pre) + found
        return {
            "who_wants_what": reply["who_wants_what"].strip(), "what_happens": reply["what_happens"].strip(),
            "why_it_matters": reply["why_it_matters"].strip(),
            "passed": not any(is_blocking(issue) for issue in issues), "issues": issues,
            "checked_rev": script["rev"], "checked_at": checked_at, "stale": False,
            "version": prompts.J1_V3_PROMPT_VERSION if v3 else prompts.J1_PROMPT_VERSION,
        }

    def validate(reply):
        errors = (prompts.validate_j1_v3 if v3 else prompts.validate_j1)(reply, scene_ids=scene_ids)
        if errors:
            return errors
        trial = copy.deepcopy(script)
        trial[FIRST_WATCH] = report_of(reply, llm_call.utc_now())
        return episode_common.trial_errors(ec, trial)

    reply = llm_call.call_json(ctx, J1V3 if v3 else J1, system, user, schema, validator=validate,
                               runner=tools.runner, time_fn=tools.time_fn)
    script[FIRST_WATCH] = report_of(reply, llm_call.utc_now())
    return script[FIRST_WATCH]


# --------------------------------------------------------------- refusals

def unjudged_refusal(script, ep, version=None):
    """``approve_script``'s refusal of a v2 script whose first-watch report
    is missing or stale -- never approvable, "anyway" or not -- or None
    (*version* as :func:`needs_first_watch`'s)."""
    state = first_watch_state(script, version)
    if state == "none":
        return (f"Episode {ep}'s script has not had its first-watch check yet (J1, a v2 story's judge): run "
                "the script step again (it checks the script), then approve.")
    if state == "stale":
        report = script[FIRST_WATCH]
        if not report["stale"] and report["checked_rev"] == script["rev"]:
            # Only its version is older (DEC-248): the judge changed, not the script.
            return (f"Episode {ep}'s first-watch check was made by an older version of the judge: check it again "
                    "(run the script step), then approve.")
        return (f"Episode {ep}'s first-watch check is out of date (the script changed since it ran): check it "
                "again (run the script step), then approve.")
    return None


def issues_sentence(script, *, words=True) -> str:
    """The clause a refusal hangs on ``Episode N's first-watch check``: what
    the report found -- `` found 2 issues: s01 (unclear goal): A; s02
    (unmotivated): B.`` -- or, when the step's repair passes ran on this
    script (``script.repairs``, stage G), what was tried first: ``: after 2
    repair passes, 2 issues remain: ...``. Each fix is listed without its
    own final period, so the sentence ends once (``urgent..`` was shipped);
    one ending with ``!`` or ``?`` keeps it. *words*: each kind in words
    (the approval's message), else as its id (the fast track's).

    J1 version 2 (DEC-248): only the blocking issues are listed and counted
    -- ``2 blocking issues`` -- and the minor ones, which never refuse, are
    counted after them: `` (1 minor issue kept for review)``."""
    report = script[FIRST_WATCH]
    blocking, minor = blocking_issues(report), minor_issues(report)
    what = "blocking issue" if minor or any("severity" in issue for issue in blocking) else "issue"

    def item(issue):
        kind = KIND_WORDS[issue["kind"]] if words else issue["kind"]
        return f"{issue['scene_id'] or 'the episode'} ({kind}): {issue['fix'].strip().rstrip('.')}"

    issues = "; ".join(item(issue) for issue in blocking)
    count = len(blocking)
    passes = len(script.get(REPAIRS) or [])
    if passes:
        head = (f": after {passes} repair pass{'' if passes == 1 else 'es'}, {count} {what}"
                f"{' remains' if count == 1 else 's remain'}")
    else:
        head = f" found {count} {what}{'' if count == 1 else 's'}"
    text = head + (f": {issues}" if issues else "")
    if minor:
        text = text.rstrip(".") + f" ({_count(len(minor), 'minor')} kept for review)"
    return text if text.endswith(("!", "?")) else text + "."


def issues_refusal(script, ep):
    """``approve_script``'s refusal of a v2 script whose first-watch report
    found issues -- unless "approve anyway" -- naming them, the repair
    passes that ran first (:func:`issues_sentence`) and what a viewer took
    away; None when it passed."""
    report = (script or {}).get(FIRST_WATCH)
    if report is None or report["passed"]:
        return None
    took = (f" A first-time viewer took away: {report['who_wants_what']} {report['what_happens']} "
            f"{report['why_it_matters']}").rstrip()
    return (f"Episode {ep}'s first-watch check{issues_sentence(script)}{took} Fix them (edit the script, or "
            "regenerate the scenes they name) and check again, or approve anyway.")


# ------------------------------------------------------------------ J2

J2 = "J2"
KEYFRAME_VERDICTS = "keyframe_verdicts"
KEYFRAMES_APPROVED = "keyframes_approved"
KEYFRAMES_APPROVAL = "keyframes"  # the approval's word: keyframes:<ep>

# How long one vision call may take, for the assets step's predictive budget.
STORY_VISION_CALL_SECONDS = 120

# The brief's parts are capped in characters, cut at a word (DEC-138: its
# worst case is measured, tests/test_story_keyframe_gate.py): the action once
# its tags are named, a character's or a prop's look (its descriptor's
# start), a staging entry's facing and expression; a name keeps its own cap.
# Phase 8 stage B: a character with a look is said as who it is (its
# presentation, build, face, hair and skin) and what it wears in this shot
# (its wardrobe set's items), each with its own cap -- the J2 text of version
# 2 at its worst case (5 such characters, 4 sheets, a scene change) is 1,145
# tokens, under the default pack budget's 1,200 (version 3, plan 28 F2: 1,303,
# under ``prompts.J2_TEXT_BUDGET``).
_BRIEF_ACTION_CHARS = 320
_BRIEF_LOOK_CHARS = 100
_BRIEF_IDENTITY_CHARS = 120
_BRIEF_OUTFIT_CHARS = 90
_BRIEF_STAGING_CHARS = 60
_TAG = re.compile(r"[@%#][a-z0-9_]+(?::[a-z][a-z0-9_]*)?")
_IDENTITY_FIELDS = ("presentation", "build", "face", "hair", "skin_material")
# Plan 26 stage 7a: a look that names its species (``look.species``) is
# judged against its head, said first in its own words, inside the identity's
# own cap (the head and the rest of who it is together stay within
# _BRIEF_IDENTITY_CHARS, so the worst case above is unchanged); a skin line
# that still says "human" is not said beside it (the sheet shows a fruit skin).
_BRIEF_HEAD_CHARS = 90
_HUMAN_WORD = re.compile(r"\bhuman\b", re.IGNORECASE)


class KeyframeContext:
    """What J2 sees beside a shot's keyframes (phase 8 stage B): *sheets*,
    ``{char_id: path}`` of the identity sheets on disk; *ledger*, the
    episode's continuity ledger (``context.ledger_before``: each character's
    wardrobe set; None without one); *scenes*, ``{shot_id: scene_id}`` of
    the storyboard. *variant_sheets* (plan 23 stage D5): ``{(char_id,
    variant_id): path}`` of the appearance variants' sheets the shots name
    (a shot's ``variants``): such a character is judged against its
    variant's sheet, labelled with the variant. *plates* (plan 28 F2):
    ``{shot_id: path}`` of the set's plate each shot's keyframe was drawn
    with (the scene's time variant when it has one); *props*: ``{shot_id:
    [(name, path)]}`` of the images of the props in frame -- shown after the
    sheets while the call has room (``prompts.J2_MAX_IMAGES``)."""

    def __init__(self, *, sheets=None, ledger=None, scenes=None, variant_sheets=None, plates=None, props=None):
        self.sheets = dict(sheets or {})
        self.ledger = ledger
        self.scenes = dict(scenes or {})
        self.variant_sheets = dict(variant_sheets or {})
        self.plates = dict(plates or {})
        self.props = {shot_id: list(found) for shot_id, found in (props or {}).items()}

    def characters(self, ec, shot) -> list:
        """``[(char_id, doc)]`` of *shot*'s character tags, each once, in
        subject order (the ones the story still has)."""
        out = []
        for tag in shot.get("subject_tags") or ():
            cid = tag[1:]
            if tag.startswith("@") and cid in ec.entities["characters"] and cid not in dict(out):
                out.append((cid, ec.entities["characters"][cid]))
        return out

    def sheets_of(self, ec, shot) -> list:
        """``[(name, path)]``: the identity sheet of each character on
        screen, in subject order, at most ``prompts.J2_MAX_SHEETS``."""
        worn = shot.get("variants") or {}
        found = []
        for cid, doc in self.characters(ec, shot):
            variant_id = worn.get(cid)
            if variant_id and self.variant_sheets.get((cid, variant_id)):
                label = next((variant["label"] for variant in doc.get("variants") or ()
                              if variant["variant_id"] == variant_id), variant_id)
                found.append((f"{doc.get('name') or cid} ({label})", self.variant_sheets[(cid, variant_id)]))
            elif self.sheets.get(cid):
                found.append((doc.get("name") or cid, self.sheets[cid]))
        return found[:prompts.J2_MAX_SHEETS]

    def refs_of(self, shot, room) -> tuple:
        """``(plate path | None, [(name, path)])``: the set's plate and the
        props' images shown with *shot*, within *room* images (the plate
        first, then the props in frame, in subject order)."""
        plate = self.plates.get(shot["shot_id"]) if room > 0 else None
        left = room - (1 if plate else 0)
        props = self.props.get(shot["shot_id"]) or []
        return plate, props[:max(0, left)]

    def same_scene(self, shot_id, previous_shot_id):
        """Whether the two shots are in one scene; None when it is not known."""
        here, there = self.scenes.get(shot_id), self.scenes.get(previous_shot_id)
        return None if here is None or there is None else here == there


def _clipped(text, limit) -> str:
    """*text* on one line, cut at the last whole word within *limit*
    characters (one ellipsis added), or as it is when it fits."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.") + "…"


def _tag_name(ec, tag) -> str:
    """A ``@char``/``%prop``/``#place:variant`` tag as its entity's name."""
    body = tag[1:]
    if tag[0] == "@":
        doc = ec.entities["characters"].get(body)
    elif tag[0] == "%":
        doc = ec.entities["props"].get(body)
    else:
        doc = ec.entities["places"].get(body.partition(":")[0])
    return (doc or {}).get("name") or body


def _character_look(doc, wardrobe) -> str:
    """A character as J2 reads it: with a look (phase 8 stage B), who it is
    -- its presentation, build, face, hair and skin -- then what it wears in
    this shot (*wardrobe*, the set ``shots.shot_wardrobe`` dresses it in),
    each capped; without one, the first words of its descriptor, as
    before. A look that names its species (plan 26 stage 7a) is said with
    its head first ("Head: pear (a whole fruit/vegetable head, the face
    carved into it)") and without a skin line that says "human"."""
    look = doc.get("look")
    if not look or wardrobe is None:
        return _clipped(doc.get("descriptor"), _BRIEF_LOOK_CHARS)
    species = shots.look_species(look)
    fields = [key for key in _IDENTITY_FIELDS
              if not (species and key == "skin_material" and _HUMAN_WORD.search(str(look.get(key) or "")))]
    who = ", ".join(" ".join(str(look[key]).split()).rstrip(".") for key in fields if look.get(key))
    outfit = _clipped(str(wardrobe['items']).rstrip('.'), _BRIEF_OUTFIT_CHARS)
    if not species:
        return f"{_clipped(who, _BRIEF_IDENTITY_CHARS)}; wearing {outfit}"
    head = _clipped(f"Head: {species} (a whole fruit/vegetable head, the face carved into it)", _BRIEF_HEAD_CHARS)
    who = _clipped(who, _BRIEF_IDENTITY_CHARS - len(head) - 2)
    return f"{head}; {who}; wearing {outfit}" if who else f"{head}; wearing {outfit}"


def keyframe_brief(ec, shot, *, ledger=None) -> str:
    """What *shot* must show, for J2: its action with every tag named, its
    framing, the place and its time, each character with its look and the
    staging T1 v2 gave it, and each prop -- the storyboard shot's own text,
    no prompt layer (the judge checks the frame against the plan, not the
    prompt against itself). Each part is capped (the ``_BRIEF_*_CHARS``
    above). A character with a look is said as who it is and the wardrobe
    set it wears in this shot -- *ledger*'s (``context.ledger_before``),
    else its first (phase 8 stage B, :func:`_character_look`)."""
    tags = list(shot.get("subject_tags") or [])
    action = _TAG.sub(lambda match: _tag_name(ec, match.group(0)), shot["action"])
    lines = [f"What happens: {_clipped(action, _BRIEF_ACTION_CHARS)}"]
    phrase = prompting.FRAMING_PHRASES.get(shot["framing"])
    if phrase:
        lines.append(f"Framing: {phrase}")
    for tag in tags:
        if tag.startswith("#"):
            variant = tag.partition(":")[2]
            lines.append(f"Where: {_tag_name(ec, tag)}" + (f", {variant}" if variant else ""))
    staging = {entry["subject"]: entry for entry in shot.get("staging") or []}
    people = [tag for tag in tags if tag.startswith("@")]
    if people:
        lines.append("Who is in it:")
        for tag in people:
            doc = ec.entities["characters"].get(tag[1:]) or {}
            line = f"- {_tag_name(ec, tag)}: {_character_look(doc, shots.shot_wardrobe(doc, ledger, tag[1:]))}"
            variant = shots.variant_record(doc, (shot.get("variants") or {}).get(tag[1:]))
            if variant is not None:
                # Plan 23 stage D5: the character wears an appearance variant in this shot.
                line += f"; now {_clipped(variant['delta_text'], _BRIEF_LOOK_CHARS)} ({variant['label']})"
            place = staging.get(tag)
            if place:
                line += (f" -- {place['position']}, facing {_clipped(place['facing'], _BRIEF_STAGING_CHARS)}, "
                         f"{_clipped(place['expression'], _BRIEF_STAGING_CHARS)}")
            lines.append(line)
    objects = [tag for tag in tags if tag.startswith("%")]
    if objects:
        lines.append("Objects that must be seen:")
        for tag in objects:
            doc = ec.entities["props"].get(tag[1:]) or {}
            look = doc.get("descriptor") or doc.get("one_line")
            lines.append(f"- {_tag_name(ec, tag)}: {_clipped(look, _BRIEF_LOOK_CHARS)}")
    return "\n".join(lines)


def verdict_version(entry) -> int:
    """The J2 prompt version that judged *entry*: its ``prompt_version``, 1
    for a verdict of stage 6b (written before the stamp existed)."""
    return int(entry.get("prompt_version") or 1)


def verdict_current(entry, image_sha, previous_sha) -> bool:
    """Whether a stored verdict judged these two images (the shot's
    keyframe, the previous shot's -- None for the first shot) with today's
    J2 (``prompts.J2_PROMPT_VERSION``, phase 8 stage B: a verdict of an
    older prompt stays readable, and is asked again)."""
    return (entry is not None and image_sha is not None and entry.get("image_sha256") == image_sha
            and entry.get("previous_sha256") == previous_sha
            and verdict_version(entry) == prompts.J2_PROMPT_VERSION)


def verdict_passed(entry) -> bool:
    """A verdict passes when the keyframe shows the beat, misses nothing, has
    the framing asked (``framing_issue``, plan 19 stage 3: absent on an older
    verdict, which reads as none), matches each character's sheet and look
    (``sheet_issues``, plan 28 F2: absent reads as none) and keeps
    continuity with the shot before it."""
    return (bool(entry["shows_beat"]) and not entry["missing"] and not entry.get("framing_issue")
            and not entry.get("sheet_issues") and not entry["continuity_issue"])


def verdict_text(entry) -> str:
    """One verdict's findings, for a refusal or the feed: each character
    that does not match its sheet first (plan 28 F2, J2's own words), then
    the rest."""
    found = [issue.rstrip(".") for issue in entry.get("sheet_issues") or ()]
    if not entry["shows_beat"]:
        found.append("does not show the beat")
    if entry["missing"]:
        found.append("missing " + "; ".join(entry["missing"]))
    if entry.get("framing_issue"):
        found.append(f"framing: {entry['framing_issue']}")
    if entry["continuity_issue"]:
        found.append(f"continuity: {entry['continuity_issue']}")
    return ", ".join(found) or "passed"


def keyframe_refusal(failed, unjudged) -> str:
    """Plan 28 F2 (DEC-305 §5): why keyframes cannot be approved, in plain
    sentences -- each failed shot (*failed*: ``[(shot_id, verdict)]``) with
    what the judge saw ("Shot sh04 does not match: Gaston's head is a pear,
    the sheet shows a pineapple. Regenerate it, or upload your own."), then
    the shots with no current check (*unjudged*). The keyframe approval's
    refusal and the fast track's stop say it alike; nothing goes over it."""
    sentences = [f"Shot {shot_id} does not match: {verdict_text(entry)}." for shot_id, entry in failed]
    if failed:
        sentences.append("Regenerate it, or upload your own." if len(failed) == 1
                         else "Regenerate them, or upload your own.")
    if unjudged:
        many = len(unjudged) > 1
        names = ", ".join(unjudged[:-1]) + f" and {unjudged[-1]}" if many else unjudged[0]
        sentences.append(f"Shot{'s' if many else ''} {names} {'have' if many else 'has'} no keyframe check yet: run "
                         "the assets step again (it checks them, free).")
    return " ".join(sentences)


def keyframes_fingerprint(shas) -> str:
    """sha256 over the keyframes a keyframe approval approves: *shas* is
    ``[(shot_id, image sha256 | None)]`` in storyboard order."""
    payload = {"v": 1, "keyframes": [[shot_id, sha] for shot_id, sha in shas]}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def keyframes_state(approved, fingerprint) -> str:
    """``none`` (never approved) | ``current`` (approved with *fingerprint*,
    the keyframes as they are now) | ``stale`` (a keyframe changed since:
    derived, never cleared)."""
    if not approved:
        return "none"
    return "current" if approved["fingerprint"] == fingerprint else "stale"


def _vision_units(answered, request, adapters):
    """``(unit, qty)`` for the ledger: the adapter's own estimate when it has
    one (a paid link), else the token count of the text and the images
    (``uploads._units``' rule)."""
    from clipping.providers import vision

    adapter = gen.adapter_for(gen.VISION, answered.provider, adapters)
    try:
        estimate = adapter.estimate(answered, request) if adapter is not None else None
    except Exception:  # noqa: BLE001 - a missing price must not lose the booking
        estimate = None
    unit, qty = getattr(estimate, "unit", None), getattr(estimate, "qty", None)
    if unit in ledger_mod.UNITS and isinstance(qty, (int, float)):
        return unit, qty
    return "token", len(request.prompt or "") // 4 + vision.TOKENS_PER_IMAGE * len(request.images or ())


def _reply_of(result, *, has_previous):
    """``(verdict fields, errors)`` from one answer (``GenResult.meta["text"]``)."""
    text = (result.meta or {}).get("text") or ""
    try:
        value = jsonx.extract_json(text)
    except ValueError as exc:
        return None, [str(exc)]
    errors = prompts.validate_j2(value, has_previous=has_previous)
    if errors:
        return None, errors
    found = {"shows_beat": value["shows_beat"], "missing": [" ".join(item.split()) for item in value["missing"]],
             "continuity_issue": " ".join(value["continuity_issue"].split()) if value["continuity_issue"] else None}
    if value.get("framing_issue"):
        # Plan 19 stage 3: kept only when J2 names one (a verdict without it reads as none).
        found["framing_issue"] = " ".join(value["framing_issue"].split())
    if value.get("sheet_issues"):
        # Plan 28 F2: each character that does not match its sheet, kept only when J2 names one.
        found["sheet_issues"] = [" ".join(item.split()) for item in value["sheet_issues"]]
    return found, []


def j2_request(ec, shot, path, prev_id, prev_path, context=None):
    """``(request, has_previous)``: the J2 call of *shot* -- its keyframe
    *path*, the previous one (*prev_path*, None for the first shot), and
    with a *context* (:class:`KeyframeContext`, phase 8 stage B) each
    on-screen character's identity sheet after them, the brief with the
    episode's wardrobe sets, and whether image 2 is in the same scene --
    and (plan 28 F2) the set's plate and the props in frame after the
    sheets, within ``prompts.J2_MAX_IMAGES``. *has_previous* is whether the
    reply may name a continuity issue (an image 2; the sheets are asked
    apart, in ``sheet_issues``)."""
    has_previous = prev_path is not None
    sheets = context.sheets_of(ec, shot) if context is not None else []
    same_scene = context.same_scene(shot["shot_id"], prev_id) if context is not None and has_previous else None
    brief = keyframe_brief(ec, shot, ledger=context.ledger if context is not None else None)
    images = ((path, prev_path) if has_previous else (path,)) + tuple(sheet for _name, sheet in sheets)
    # Plan 28 F2: the set's plate and the props in frame, while the call has room.
    plate, props = (context.refs_of(shot, prompts.J2_MAX_IMAGES - len(images)) if context is not None
                    else (None, []))
    images += ((plate,) if plate else ()) + tuple(image for _name, image in props)
    # One continuity ledger an episode: a character wears one wardrobe set in
    # every shot of it, so across a scene change its outfit is compared too.
    text = prompts.j2_prompt_text(shot_id=shot["shot_id"], brief=brief,
                                  previous_shot_id=prev_id if has_previous else None, same_scene=same_scene,
                                  sheets=[name for name, _sheet in sheets], outfit=True,
                                  two_view=media_policy.two_view(getattr(ec, "story", None)),
                                  plate=bool(plate), props=[name for name, _image in props])
    request = gen.GenRequest(kind=gen.VISION, prompt=text, images=images,
                             extra={"max_tokens": prompts.MAX_TOKENS[J2], "temperature": prompts.TEMPERATURE[J2]})
    return request, has_previous


def check_keyframes(ctx, ec, items, verdicts, *, env, ledger, step, before_call, on_verdict=None, adapters=None,
                    transport=None, context=None):
    """J2 over *items* (``[(shot, path, sha, previous_shot_id, previous_path,
    previous_sha)]``, every shot with a current keyframe, in storyboard
    order; the previous ones None for the first shot), keeping each verdict
    of *verdicts* that is still current. Returns ``(verdicts, summary)``:
    every current verdict by shot id, and ``{"judged": [...], "kept": [...],
    "failed": [...], "unavailable": reason | None}``. *before_call(left)*
    is the caller's cancel and budget check before each call (*left*: the
    shot ids not judged yet); *on_verdict(verdicts)*, when given, is handed
    every current verdict after each new one, so the caller keeps what was
    judged should the run stop; each answered call is booked on *ledger*
    (step *step*, the episode). Calls nothing for a shot judged already.
    *context* (:class:`KeyframeContext`, phase 8 stage B): the sheets, the
    wardrobe sets and the scenes J2 is shown (:func:`j2_request`)."""
    kept, todo = {}, []
    for item in items:
        shot, _path, sha, _prev_id, _prev_path, prev_sha = item
        entry = (verdicts or {}).get(shot["shot_id"])
        if verdict_current(entry, sha, prev_sha):
            kept[shot["shot_id"]] = entry
        else:
            todo.append(item)
    summary = {"judged": [], "kept": list(kept), "failed": [], "unavailable": None}
    if not todo:
        return kept, summary

    if adapters is None:
        adapters_mod.load_all()
    merged = gating.merged_env(env)
    try:
        chain = gen.chain_from_env(gen.VISION, merged)
        budget_obj = gating.budget_of(merged)
    except (ChainError, ValueError) as exc:
        summary["unavailable"] = f"{gen.ENV_NAMES[gen.VISION]} cannot be used: {exc}"
        ctx.on_log(f"👁 Keyframe check (J2) skipped: {summary['unavailable']}")
        return kept, summary
    check = gating.budget_check(budget_obj, story_spent=lambda: ledger.totals()["est_usd"])
    limiter = gating.FreeTierLimiter()
    route = ec.story["generation_profile"]["route"]
    ctx.on_log(f"👁 Keyframe check (J2): {len(todo)} shot{'s' if len(todo) != 1 else ''}"
               + (f" ({len(kept)} judged already, kept)" if kept else ""))
    for index, (shot, path, sha, prev_id, prev_path, prev_sha) in enumerate(todo):
        shot_id = shot["shot_id"]
        before_call([item[0]["shot_id"] for item in todo[index:]])
        request, has_previous = j2_request(ec, shot, path, prev_id, prev_path, context)
        found, errors, answered = None, [], None
        for attempt in (1, 2):
            try:
                result, answered = gen.run_generation_chain(
                    gen.VISION, chain, request, env=merged, allow_paid=budget_obj.allow_paid, route=route,
                    on_log=ctx.on_log, budget_check=check, limiter=limiter, adapters=adapters,
                    transport=transport, cancel=ctx.cancel)
            except gen.NoRunnableLink as exc:
                reasons = "; ".join(f"{label}: {reason}" for label, reason in exc.failures) or str(exc)
                summary["unavailable"] = f"no vision link could judge the keyframes ({reasons})"
                ctx.on_log(f"👁 Keyframe check (J2) stopped: {summary['unavailable']}")
                return dict(kept, **{sid: verdicts[sid] for sid in summary["judged"]}), summary
            paid = bool(result.paid)
            est = float(result.est_cost) if paid else 0.0
            unit, qty = _vision_units(answered, request, adapters)
            ledger.append(step=step, provider=answered.provider, model=gating.api_model_id(gen.VISION, answered),
                          unit=unit, qty=qty, est_usd=est, paid=paid, ep=ec.ep)
            if paid and result.est_cost > 0:
                budget_mod.record(result.est_cost)
            found, errors = _reply_of(result, has_previous=has_previous)
            if found is not None:
                break
            if attempt == 1:
                ctx.on_log(f"⚠️ {J2} reply for shot {shot_id} rejected ({'; '.join(errors[:2])}); asking once more")
        if found is None:
            summary["failed"].append(shot_id)
            ctx.on_log(f"✖ Keyframe check of shot {shot_id} failed: the replies failed validation twice")
            continue
        verdicts = dict(verdicts or {})
        verdicts[shot_id] = dict(found, image_sha256=sha, previous_sha256=prev_sha, link=describe(answered),
                                 checked_at=llm_call.utc_now(), prompt_version=prompts.J2_PROMPT_VERSION)
        summary["judged"].append(shot_id)
        entry = verdicts[shot_id]
        ctx.on_log(f"👁 Shot {shot_id}: {'passed' if verdict_passed(entry) else verdict_text(entry)}")
        if on_verdict is not None:
            on_verdict(dict(kept, **{sid: verdicts[sid] for sid in summary["judged"]}))
    result = dict(kept)
    result.update({sid: verdicts[sid] for sid in summary["judged"]})
    return result, summary
