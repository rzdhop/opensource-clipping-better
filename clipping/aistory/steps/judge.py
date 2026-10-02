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

**J2, the keyframes** (:func:`check_keyframes`, stage 6b): in the assets
step of a v2 story, once the keyframes exist, one vision call per shot on
``VISION_CHAIN`` (free Gemini first; ``uploads.describe_upload``'s pattern:
every gate of the generation runner, each answered call booked in the
story's ledger, a reply that fails validation asked for once more) with the
shot's keyframe, the previous shot's keyframe and what the shot must show
(:func:`keyframe_brief`): ``{shows_beat, missing, continuity_issue}``,
stored per shot in ``assets.json``'s optional ``keyframe_verdicts`` with the
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

**The keyframe approval** (``workflow.approve_keyframes``):
``assets.json``'s ``keyframes_approved {at, anyway, fingerprint}``, the
fingerprint of the keyframe images (:func:`keyframes_fingerprint`): once
it differs, the approval is stale (:func:`keyframes_state`) -- derived,
never cleared (DEC-155's rule). No clip of a v2 episode is bought before it
is current (``assets.clip_hold``, RC-Q3).

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

from .. import context, prompting, prompts, shots
from .. import ledger as ledger_mod
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
# tokens, under the default pack budget's 1,200.
_BRIEF_ACTION_CHARS = 320
_BRIEF_LOOK_CHARS = 100
_BRIEF_IDENTITY_CHARS = 120
_BRIEF_OUTFIT_CHARS = 90
_BRIEF_STAGING_CHARS = 60
_TAG = re.compile(r"[@%#][a-z0-9_]+(?::[a-z][a-z0-9_]*)?")
_IDENTITY_FIELDS = ("presentation", "build", "face", "hair", "skin_material")


class KeyframeContext:
    """What J2 sees beside a shot's keyframes (phase 8 stage B): *sheets*,
    ``{char_id: path}`` of the identity sheets on disk; *ledger*, the
    episode's continuity ledger (``context.ledger_before``: each character's
    wardrobe set; None without one); *scenes*, ``{shot_id: scene_id}`` of
    the storyboard."""

    def __init__(self, *, sheets=None, ledger=None, scenes=None):
        self.sheets = dict(sheets or {})
        self.ledger = ledger
        self.scenes = dict(scenes or {})

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
        found = [(doc.get("name") or cid, self.sheets[cid]) for cid, doc in self.characters(ec, shot)
                 if self.sheets.get(cid)]
        return found[:prompts.J2_MAX_SHEETS]

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
    before."""
    look = doc.get("look")
    if not look or wardrobe is None:
        return _clipped(doc.get("descriptor"), _BRIEF_LOOK_CHARS)
    who = ", ".join(" ".join(str(look[key]).split()).rstrip(".") for key in _IDENTITY_FIELDS if look.get(key))
    return (f"{_clipped(who, _BRIEF_IDENTITY_CHARS)}; wearing "
            f"{_clipped(str(wardrobe['items']).rstrip('.'), _BRIEF_OUTFIT_CHARS)}")


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
    """A verdict passes when the keyframe shows the beat, misses nothing and
    keeps continuity with the shot before it."""
    return bool(entry["shows_beat"]) and not entry["missing"] and not entry["continuity_issue"]


def verdict_text(entry) -> str:
    """One verdict's findings, for a refusal or the feed."""
    found = []
    if not entry["shows_beat"]:
        found.append("does not show the beat")
    if entry["missing"]:
        found.append("missing " + "; ".join(entry["missing"]))
    if entry["continuity_issue"]:
        found.append(f"continuity: {entry['continuity_issue']}")
    return ", ".join(found) or "passed"


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
    return {"shows_beat": value["shows_beat"], "missing": [" ".join(item.split()) for item in value["missing"]],
            "continuity_issue": " ".join(value["continuity_issue"].split()) if value["continuity_issue"] else None}, []


def j2_request(ec, shot, path, prev_id, prev_path, context=None):
    """``(request, has_previous)``: the J2 call of *shot* -- its keyframe
    *path*, the previous one (*prev_path*, None for the first shot), and
    with a *context* (:class:`KeyframeContext`, phase 8 stage B) each
    on-screen character's identity sheet after them, the brief with the
    episode's wardrobe sets, and whether image 2 is in the same scene.
    *has_previous* is whether the reply may name a continuity issue."""
    has_previous = prev_path is not None
    sheets = context.sheets_of(ec, shot) if context is not None else []
    same_scene = context.same_scene(shot["shot_id"], prev_id) if context is not None and has_previous else None
    brief = keyframe_brief(ec, shot, ledger=context.ledger if context is not None else None)
    images = ((path, prev_path) if has_previous else (path,)) + tuple(sheet for _name, sheet in sheets)
    # One continuity ledger an episode: a character wears one wardrobe set in
    # every shot of it, so across a scene change its outfit is compared too.
    text = prompts.j2_prompt_text(shot_id=shot["shot_id"], brief=brief,
                                  previous_shot_id=prev_id if has_previous else None, same_scene=same_scene,
                                  sheets=[name for name, _sheet in sheets], outfit=True)
    request = gen.GenRequest(kind=gen.VISION, prompt=text, images=images,
                             extra={"max_tokens": prompts.MAX_TOKENS[J2], "temperature": prompts.TEMPERATURE[J2]})
    return request, has_previous or bool(sheets)


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
