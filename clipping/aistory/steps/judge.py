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
stale}``), stale like the consistency report: once the script is rewritten
(``episode_common.mark_changed``) or its revision moved
(:func:`needs_first_watch`). ``workflow.approve_script`` refuses a v2
script while the report is missing or stale (never approvable) or failed
(unless "approve anyway"): :func:`unjudged_refusal`,
:func:`issues_refusal`.

A legacy story is never judged: nothing here runs for it, and its script
never has the key.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy

from .. import context, prompts
from . import episode_common, llm_call

FIRST_WATCH = "first_watch"
J1 = "J1"

# The deterministic repeated-line issues one report holds at most (the stored
# report holds 20; J1 adds at most 6, the hook check 1).
DUPLICATE_ISSUES_MAX = 6

_KIND_WORDS = {
    "unclear_goal": "unclear goal", "unmotivated": "unmotivated", "unintroduced": "unintroduced",
    "object_unseen": "object unseen", "repeated_line": "repeated line", "no_hook_text": "no hook text",
}


def _and(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# ------------------------------------------------------------- the report

def needs_first_watch(script) -> bool:
    """Whether *script*'s first-watch report is missing, marked stale, or of
    an older revision (the consistency report's rule,
    ``script.needs_check``)."""
    report = (script or {}).get(FIRST_WATCH)
    return report is None or report["stale"] or report["checked_rev"] != script["rev"]


def first_watch_state(script) -> str:
    """``none`` | ``stale`` | ``passed`` | ``issues`` (``workflow.
    report_state``'s, for the first-watch report)."""
    report = (script or {}).get(FIRST_WATCH)
    if report is None:
        return "none"
    if needs_first_watch(script):
        return "stale"
    return "passed" if report["passed"] else "issues"


def first_watch_line(report) -> str:
    if report["passed"]:
        return "👀 First watch: passed"
    count = len(report["issues"])
    return f"👀 First watch: {count} issue{'s' if count != 1 else ''}"


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
                        "scene_id": scene["scene_id"], "kind": "repeated_line",
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
    return [{"scene_id": hook["scene_id"] if hook else None, "kind": "no_hook_text",
             "fix": f"The hook has no on-screen text: write it again (regenerate hook:{ep}) so it states the "
                    "premise on screen."}]


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


def check_first_watch(ctx, ec, script, *, tools, pack) -> dict:
    """J1 over the whole *script* with the deterministic checks merged in
    (module docstring); the report is set on *script* (in place; the caller
    writes it) and returned. *pack* is the step's own
    (``context.build_pack``: the language)."""
    pre = duplicate_issues(script) + hook_text_issues(script, ec.ep)
    if pre:
        ctx.on_log(f"👀 Repeated lines and hook text: {len(pre)} issue{'s' if len(pre) != 1 else ''}, added to "
                   "the first-watch report")
    digest = prompts.script_digest(script, {
        "places": {pid: doc["name"] for pid, doc in ec.entities["places"].items()},
        "cast": ec.names,
    })
    recap = context.previous_recap(ec.season, ec.ep)
    system, user, schema = prompts.build_j1(
        pack, ep=ec.ep, script_digest=digest, objects=_objects(ec, script),
        hook_text=(script.get("hook") or {}).get("on_screen_text"), reveal=script["cliffhanger"]["reveal"],
        previous_recap=recap)
    scene_ids = [scene["scene_id"] for scene in script["scenes"]]
    pre_kinds = {issue["kind"] for issue in pre}

    def report_of(reply, checked_at):
        # The model's own no_hook_text is left out when the check found it.
        found = [{"scene_id": issue["scene_id"], "kind": issue["kind"], "fix": issue["fix"].strip()}
                 for issue in reply["issues"]
                 if not (issue["kind"] == "no_hook_text" and "no_hook_text" in pre_kinds)]
        return {
            "who_wants_what": reply["who_wants_what"].strip(), "what_happens": reply["what_happens"].strip(),
            "why_it_matters": reply["why_it_matters"].strip(),
            "passed": reply["passed"] and not pre, "issues": copy.deepcopy(pre) + found,
            "checked_rev": script["rev"], "checked_at": checked_at, "stale": False,
        }

    def validate(reply):
        errors = prompts.validate_j1(reply, scene_ids=scene_ids)
        if errors:
            return errors
        trial = copy.deepcopy(script)
        trial[FIRST_WATCH] = report_of(reply, llm_call.utc_now())
        return episode_common.trial_errors(ec, trial)

    reply = llm_call.call_json(ctx, J1, system, user, schema, validator=validate, runner=tools.runner,
                               time_fn=tools.time_fn)
    script[FIRST_WATCH] = report_of(reply, llm_call.utc_now())
    return script[FIRST_WATCH]


# --------------------------------------------------------------- refusals

def unjudged_refusal(script, ep):
    """``approve_script``'s refusal of a v2 script whose first-watch report
    is missing or stale -- never approvable, "anyway" or not -- or None."""
    state = first_watch_state(script)
    if state == "none":
        return (f"Episode {ep}'s script has not had its first-watch check yet (J1, a v2 story's judge): run "
                "the script step again (it checks the script), then approve.")
    if state == "stale":
        return (f"Episode {ep}'s first-watch check is out of date (the script changed since it ran): check it "
                "again (run the script step), then approve.")
    return None


def issues_refusal(script, ep):
    """``approve_script``'s refusal of a v2 script whose first-watch report
    found issues -- unless "approve anyway" -- naming them and what a
    viewer took away; None when it passed."""
    report = (script or {}).get(FIRST_WATCH)
    if report is None or report["passed"]:
        return None
    issues = "; ".join(f"{issue['scene_id'] or 'the episode'} ({_KIND_WORDS[issue['kind']]}): {issue['fix']}"
                       for issue in report["issues"])
    count = len(report["issues"])
    took = (f" A first-time viewer took away: {report['who_wants_what']} {report['what_happens']} "
            f"{report['why_it_matters']}").rstrip()
    return (f"Episode {ep}'s first-watch check found {count} issue{'' if count == 1 else 's'}"
            f"{': ' + issues if issues else ''}.{took} Fix them (edit the script, or regenerate the scenes they "
            "name) and check again, or approve anyway.")
