"""``python main.py --ai-story ...`` -- AI Story steps 1-7 from a terminal (spec 9.3).

``main.py`` hands everything after ``--ai-story`` to :func:`main` before the
clip parser sees it (DEC-114): this is a parser of its own, and the clip CLI's
options and defaults are untouched. Three commands::

    main.py --ai-story new --lang fr [--concept ID] [--style ID] [--seed-text TEXT]
                           [--tier N] [--route R] [--consistency-mode M] [--budget-profile P]
    main.py --ai-story step <story_id> concepts|bible|style|style_preview [options]
    main.py --ai-story step <story_id> cast|places_proposal|places|season [options]
    main.py --ai-story list

The story rules are ``clipping.aistory.workflow``'s, the ones the API applies,
and a step runs in this process through the runner the web worker calls
(``clipping.aistory.steps.run``), printing the same lines. There is no default
language: ``--lang`` is required. The generation-profile options default to
``clipping.aistory.defaults`` (the agreement test holds them to the API model).

Keys and ``LLM_CHAIN`` come from the process environment, and from the
``.env`` file ``clipping.config`` loads for the clip CLI -- never from the
dashboard's Settings: this module does not import ``web/`` (fastapi need not
be installed). Before an LLM step the API's key gate applies: no keyed link
in the chain, only paid keyed links while ``ALLOW_PAID`` is off (a story step
never calls a paid LLM link without it), or the DEC-073 slow floor alone
(unless ``--allow-slow-chain`` or ``ALLOW_SLOW_CHAIN=1``), is refused before
anything is called.

Exit codes: 0 done; 1 refused or failed (the reason on stderr); 2 a usage
error (argparse's, or an option given to a step it does not apply to); 130
interrupted (Ctrl-C cancels the step's token; what the step already wrote
stays, every write being atomic).

Phase 2 (steps 5-7): ``cast`` (``--characters NAME`` from the concept's cast
sketch -- all of it for a story with no character yet, none once it has a
cast -- and ``--custom "Name|role|one line"``), ``places_proposal``,
``places`` (``--place "Name|one line"`` / ``--prop "Name|one line|Owner"``,
else the saved proposal) and ``season`` (``--episodes N``). Before anything is
written or called they meet the API's rules in the API's order: the step's
precondition, its parameters, the key gate, then -- for the cast and the
places, when they would make an image -- ``IMAGE_CHAIN``'s verdict
(``workflow.image_verdict``). ``--prompt-only`` (``cast``, ``places``) is the
user's explicit switch to prompt-only consistency: set on the story once those
pass, before the run, and printed; nothing else ever switches it. After a cast
run -- failed or not -- what each character still lacks is printed
(``workflow.progress``), with the prompt-only hint when a sheet waits for an
editor. ``--auto-approve`` approves by the API's rules
(``workflow.approve_entity`` / ``approve_season``): every character (``cast``)
or place and prop (``places``) that has everything, naming the others and
what they lack; the season once its arc is complete. A proposal is not
approved: its places are chosen with the ``places`` step.

Limitation: the CLI and a running server do not coordinate step runs on the
same story. The server's one-step-per-story rule lives in its job store
(``web/api/store.py``), which the CLI does not read, so running a step here
while the dashboard runs one for the same story lets both write its files.
Every write is atomic (a reader sees the old file or the new one, never a torn
one) and ``stories.json`` is rebuilt from the folders when needed, but the
last write wins.

``--outputs-dir`` (hidden) points the story store at another ``outputs/``
directory, for tests; the default is the repository's ``outputs/``, the one
the server uses.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone

from . import defaults, refimages, schemas, templates, workflow
from . import store as story_store
from .steps import StepFailed
from .steps import entities as entities_step

PROG = "main.py --ai-story"

# Where a CLI user sets a key: the refusal's "Set one of: ..., <where>."
KEYS_WHERE = "in the environment or in .env"

# The StepContext.job_id of a step run here: there is no job.
CLI_JOB_ID = "cli"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

# Every step `step` runs, phase 1 then phase 2.
STEPS = workflow.PHASE1_STEPS + workflow.PHASE2_STEPS

# The steps --auto-approve approves, and what to do for the others.
AUTO_APPROVABLE = ("bible", "style", "cast", "places", "season")
_NOT_AUTO_APPROVABLE = {
    "concepts": (
        "a concept is approved by choosing it: in the dashboard's concept step, or, for a "
        f"library concept, with '{PROG} new --lang LANG --concept ID'."
    ),
    "style_preview": (
        "the preview is approved with the style it shows: run "
        f"'{PROG} step STORY_ID style --auto-approve' to lock the current draft."
    ),
    "places_proposal": (
        "a proposal is a list to choose from, not a document to approve: choose the places with "
        f"'{PROG} step STORY_ID places' (the saved proposal, or your own --place/--prop list), "
        "with --auto-approve there."
    ),
}

# The steps that call the LLM chain, and so meet the key gate.
_KEYED_STEPS = workflow.LLM_STEPS + workflow.PHASE2_STEPS

# The options of `step` that only some steps take: (dest, flag, steps).
_STEP_ONLY = (
    ("note", "--note", ("concepts",)),
    ("template", "--template", ("style",)),
    ("override", "--override", ("style",)),
    ("consistency_mode", "--consistency-mode", ("style",)),
    ("characters", "--characters", ("cast",)),
    ("custom", "--custom", ("cast",)),
    ("prompt_only", "--prompt-only", ("cast", "places")),
    ("place", "--place", ("places",)),
    ("prop", "--prop", ("places",)),
    ("episodes", "--episodes", ("season",)),
    ("allow_slow_chain", "--allow-slow-chain", _KEYED_STEPS),
)

# The list options' items, split on "|": (shape, keys, how many are required).
_ITEM_SHAPES = {
    "--custom": ("Name|role|one line", ("name", "role", "one_line"), 3),
    "--place": ("Name|one line", ("name", "one_line"), 2),
    "--prop": ("Name|one line|Owner", ("name", "one_line", "owner"), 2),
}

# Printed after a cast run when a sheet waits for an editor.
PROMPT_ONLY_HINT = "re-run with --prompt-only to continue with prompt-only consistency (labelled)"


# ------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "AI Story, steps 1-7: create a story, write its concepts and bible, build\n"
            "and lock its style, then make its cast, places and props, and season arc.\n"
            "Keys, LLM_CHAIN and the image and voice chains come from the environment\n"
            "(or .env), not from the dashboard's Settings."
        ),
        epilog=(
            "examples:\n"
            f"  {PROG} new --lang fr --concept tentafruit_island --style fruit_drama\n"
            f"  {PROG} step STORY_ID bible --auto-approve\n"
            f"  {PROG} step STORY_ID style --override palette.accents='[\"#FFD400\"]' --auto-approve\n"
            f"  {PROG} step STORY_ID cast --characters Kiwilo --custom 'Figuette|support|A shy fig.' --auto-approve\n"
            f"  {PROG} step STORY_ID places_proposal\n"
            f"  {PROG} step STORY_ID places --auto-approve\n"
            f"  {PROG} step STORY_ID season --episodes 8 --auto-approve\n"
            f"  {PROG} list"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Hidden, for tests: another outputs/ directory than the repository's.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--outputs-dir", default=None, help=argparse.SUPPRESS)

    commands = parser.add_subparsers(dest="command", metavar="{new,step,list}", required=True)

    # ---- new
    new = commands.add_parser(
        "new", parents=[common], help="create a story (and choose a library concept)",
        description="Create a draft story; with --concept, choose that library concept too.",
    )
    new.add_argument(
        "--lang", required=True, choices=schemas.LANGUAGES,
        help="the story's language; required, there is no default",
    )
    new.add_argument("--seed-text", default=None, metavar="TEXT",
                     help="a few words the concepts start from")
    styles = templates.list_style_ids()
    new.add_argument("--style", default=None, choices=styles, metavar="ID",
                     help=f"a shipped style template: {', '.join(styles)}")
    concepts = [concept["concept_id"] for concept in templates.load_concepts()]
    new.add_argument("--concept", default=None, choices=concepts, metavar="ID",
                     help=f"a library concept to choose at once: {', '.join(concepts)}")
    new.add_argument("--tier", type=int, choices=defaults.TIERS, default=defaults.DEFAULT_TIER,
                     help="generation tier (default: %(default)s)")
    new.add_argument("--route", choices=defaults.ROUTES, default=defaults.DEFAULT_ROUTE,
                     help="where images are made (default: %(default)s)")
    new.add_argument("--consistency-mode", choices=defaults.CONSISTENCY_MODES,
                     default=defaults.DEFAULT_CONSISTENCY_MODE,
                     help="how characters are kept consistent (default: %(default)s)")
    new.add_argument("--budget-profile", choices=defaults.BUDGET_PROFILES,
                     default=defaults.DEFAULT_BUDGET_PROFILE,
                     help="the story's budget profile (default: %(default)s)")

    # ---- step
    step = commands.add_parser(
        "step", parents=[common], help=f"run one step of a story: {', '.join(STEPS)}",
        description=(
            "Run one step in this process. concepts and bible call the LLM chain; style "
            "builds the draft style lock here; style_preview calls the image chain; cast, "
            "places_proposal, places and season call the LLM chain, and the cast and the "
            "places the image and voice chains too."
        ),
    )
    step.add_argument("story_id", help="the story's id (see 'list')")
    step.add_argument("step", choices=STEPS, help="the step to run")
    step.add_argument("--note", default=None, metavar="TEXT",
                      help="concepts only: an author's note the ten new concepts follow")
    step.add_argument("--template", default=None, choices=styles, metavar="ID",
                      help="style only: the style template (default: the story's, else its concept's)")
    step.add_argument(
        "--override", action="extend", nargs="+", default=None, metavar="PATH=VALUE",
        help=("style only: an override of the draft, VALUE parsed as JSON when it is JSON and "
              "taken as text otherwise, e.g. palette.accents='[\"#FFD400\"]'"),
    )
    step.add_argument("--consistency-mode", choices=defaults.CONSISTENCY_MODES, default=None,
                      help="style only: set the story's consistency mode with the style")
    step.add_argument("--characters", action="append", default=None, metavar="NAME",
                      help=("cast only, repeatable: a character of the concept's cast sketch to create "
                            "(default: the whole sketch for a story with no character yet, none once "
                            "it has a cast)"))
    step.add_argument("--custom", action="append", default=None, metavar="'NAME|ROLE|ONE LINE'",
                      help=(f"cast only, repeatable: a character of your own; ROLE one of "
                            f"{', '.join(schemas.CHARACTER_ROLES)}"))
    step.add_argument("--place", action="append", default=None, metavar="'NAME|ONE LINE'",
                      help="places only, repeatable: a place to make (default: the saved proposal)")
    step.add_argument("--prop", action="append", default=None, metavar="'NAME|ONE LINE|OWNER'",
                      help=("places only, repeatable: a prop to make; OWNER a character's name or id, "
                            "or left out (default: the saved proposal)"))
    step.add_argument("--episodes", type=int, default=None, metavar="N",
                      help=(f"season only: the episodes of the arc, {schemas.EPISODES_PLANNED_MIN} to "
                            f"{schemas.EPISODES_PLANNED_MAX} (default: 8)"))
    step.add_argument("--prompt-only", action="store_true",
                      help=("cast, places: switch the story to prompt-only consistency before the run "
                            "(sheets and time variants drawn from text, not edited from a reference "
                            "image, and labelled prompt_only)"))
    step.add_argument("--auto-approve", action="store_true",
                      help=("bible, style, season: approve the result once the step is done; cast, "
                            "places: approve every character, place and prop that has everything"))
    step.add_argument("--allow-slow-chain", action="store_true",
                      help=("the steps that call the LLM chain: run on the chain's slow floor alone; "
                            "also settable as ALLOW_SLOW_CHAIN=1"))

    # ---- list
    commands.add_parser("list", parents=[common], help="list the stories",
                        description="One line per story: id, status, language, title.")
    return parser


# ----------------------------------------------------------------- helpers

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _err(message) -> None:
    print(message, file=sys.stderr)


def _usage_error(command, message) -> int:
    _err(f"{PROG} {command}: error: {message}")
    return EXIT_USAGE


def _line(story) -> str:
    """One story: id, status, language, title."""
    title = story.get("title") or "(untitled)"
    return f"{story['story_id']}  {story['status']:<15}  {story['language']}  {title}"


def _quoted(names) -> str:
    return " and ".join(f"'{name}'" for name in names)


def _given(value) -> bool:
    """Whether an option of `step` was given: not its default (None, or False
    for a switch). ``--episodes 0`` is given."""
    return value is not None and value is not False


def _env_flag(name) -> bool:
    """``clipping.config``'s spelling of a boolean setting: 1/true/yes."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def parse_overrides(items) -> dict:
    """``["palette.accents=[\"#FFD400\"]", ...]`` -> ``{path: value}``.

    VALUE is JSON when it parses as JSON, text otherwise; a later PATH
    replaces an earlier one. ``ValueError`` for an item without ``=``.
    """
    overrides = {}
    for item in items or []:
        path, sep, raw = item.partition("=")
        path = path.strip()
        if not sep or not path:
            raise ValueError(f"--override takes PATH=VALUE, not {item!r}")
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        overrides[path] = value
    return overrides


def parse_items(flag, items) -> list:
    """``--custom``/``--place``/``--prop`` values -> ``[{key: value}]``.

    Each value is split on ``|`` into exactly the fields of its shape
    (``_ITEM_SHAPES``; a prop's owner may be left out, or empty for none),
    each stripped. ``ValueError`` naming the shape otherwise. What the
    fields hold is the workflow's to check, as the API's request is."""
    shape, keys, required = _ITEM_SHAPES[flag]
    parsed = []
    for item in items or []:
        parts = [part.strip() for part in item.split("|")]
        if not required <= len(parts) <= len(keys):
            raise ValueError(f"{flag} takes \"{shape}\", not {item!r}")
        entry = dict(zip(keys, parts))
        if "owner" in keys:
            entry["owner"] = entry.get("owner") or None
        parsed.append(entry)
    return parsed


def _llm_refusal(allow_slow_chain) -> str | None:
    """The API's key gate for an LLM step, from the process environment: no
    keyed link (naming the keys to set), only paid keyed links while
    ``ALLOW_PAID`` is off (naming them and the free keys to set), or the
    DEC-073 slow floor alone."""
    from clipping import config

    allow_slow = allow_slow_chain or _env_flag("ALLOW_SLOW_CHAIN")

    def readiness(links, keys):
        verdict = config.chain_readiness(links, keys, allow_slow=allow_slow,
                                         hint=config.CLI_SLOW_CHAIN_HINT)
        return None if verdict.ready else verdict.message

    _links, _keys, refusal = workflow.llm_gate({}, readiness=readiness, where=KEYS_WHERE)
    return refusal


def _run_step(stories, story_id, step, params):
    """Run *step* through the worker's registry, in this process, with a real
    cancel token: Ctrl-C cancels it. Returns ``EXIT_INTERRUPTED`` when it was
    interrupted, else None; ``StepFailed`` propagates."""
    from clipping.cancel import Cancelled, CancelToken

    from . import steps

    token = CancelToken()
    ctx = steps.StepContext(
        job_id=CLI_JOB_ID,
        story_id=story_id,
        step=step,
        ep=None,
        params=params,
        cancel=token,
        # Keys and chains from the process environment (llm_call's fallback).
        settings_env={},
        outputs_dir=stories.outputs_dir,
        on_log=print,
    )
    try:
        steps.run(step, ctx)
    except (KeyboardInterrupt, Cancelled):
        token.cancel()
        _err("Cancelled. What the step had already written stays.")
        return EXIT_INTERRUPTED
    return None


# ---------------------------------------------------------------- commands

def _cmd_new(args, stories) -> int:
    profile = {
        "tier": args.tier,
        "route": args.route,
        "consistency_mode": args.consistency_mode,
        "budget_profile": args.budget_profile,
    }
    try:
        story = stories.create(
            language=args.lang, seed_text=args.seed_text, style_template_id=args.style,
            generation_profile=profile, now=_now(),
        )
    except ValueError as exc:
        _err(str(exc))
        return EXIT_FAILED
    if args.concept:
        story = workflow.choose_concept(stories, story["story_id"], concept_id=args.concept, now=_now())
    print(_line(story))
    return EXIT_OK


def _cmd_list(args, stories) -> int:
    entries = stories.list()
    if not entries:
        _err(f"No stories in {stories.root}.")
    for entry in entries:
        print(_line(entry))
    return EXIT_OK


def _cmd_step(args, stories) -> int:
    step = args.step

    # Every refusal of the command line comes before anything is read or spent.
    if args.auto_approve and step not in AUTO_APPROVABLE:
        return _usage_error("step", f"--auto-approve does not apply to '{step}': {_NOT_AUTO_APPROVABLE[step]}")
    for dest, flag, applies in _STEP_ONLY:
        if _given(getattr(args, dest)) and step not in applies:
            return _usage_error("step", f"{flag} applies to {_quoted(applies)} only, not to '{step}'.")
    try:
        overrides = parse_overrides(args.override)
        items = {flag: parse_items(flag, getattr(args, flag[2:])) for flag in _ITEM_SHAPES}
    except ValueError as exc:
        return _usage_error("step", str(exc))

    story_id = args.story_id
    story = workflow.load(stories, story_id)

    if step in workflow.PHASE2_STEPS:
        return _phase2_step(args, stories, story, items)

    if step == "style":
        params = {}
        if args.template:
            params["template_id"] = args.template
        if overrides:
            params["overrides"] = overrides
        if args.consistency_mode:
            params["consistency_mode"] = args.consistency_mode
        result = workflow.build_style(stories, story_id, params, now=_now())
        lock = result["style_lock"]
        story = result["story"]
        applied = ", ".join(f"{path}={json.dumps(value, ensure_ascii=False)}"
                            for path, value in (lock.get("overrides") or {}).items())
        print(f"🎨 Style draft: {lock['template_id']} v{lock['template_version']}"
              + (f" ({applied})" if applied else ""))
        if args.auto_approve:
            story = workflow.approve_style(stories, story_id, now=_now())
            locked = workflow.style_lock(stories, story_id)
            print(f"🔒 Style locked at {locked['locked_at']}.")
        print(_line(story))
        return EXIT_OK

    if step in workflow.LLM_STEPS:
        if step == "bible":
            workflow.require_concept(story)
        refusal = _llm_refusal(args.allow_slow_chain)
        if refusal:
            _err(refusal)
            return EXIT_FAILED
        runner_step, params = step, {}
        if step == "concepts" and args.note is not None:
            # "Ten more, with a note" is the regenerate target of the API.
            runner_step = "regenerate"
            params = {"target": "concepts", "note": args.note}
        interrupted = _run_step(stories, story_id, runner_step, params)
        if interrupted:
            return interrupted
        if args.auto_approve:
            workflow.approve_bible(stories, story_id, now=_now())
            print("✅ Bible approved.")
        print(_line(workflow.load(stories, story_id)))
        return EXIT_OK

    # style_preview: the image chain, gated as the API gates it (no key gate:
    # it calls no LLM).
    from .steps import style_preview as preview_step

    workflow.require_style_draft(stories, story_id)
    verdict = preview_step.estimate(
        {}, route=story["generation_profile"]["route"],
        story_spent=workflow.cost_total(stories, story_id),
    )
    if not verdict["ready"]:
        _err(verdict["message"])
        return EXIT_FAILED
    interrupted = _run_step(stories, story_id, step, {})
    if interrupted:
        return interrupted
    print(_line(workflow.load(stories, story_id)))
    return EXIT_OK


# ------------------------------------------------------------------ phase 2

def _phase2_params(args, stories, story, items):
    """``(params, checked story, units, the default cast or None)`` of a
    phase-2 step, every rule of the API checked in the API's order -- the
    step's precondition, then its parameters -- before anything is written
    or called (``workflow.WorkflowError`` otherwise).

    *checked* is the story as the step will run it: in prompt-only mode when
    ``--prompt-only`` switches it, so the sheets count as the pictures they
    will be. *units* is what the cast or the places would make (None for the
    others)."""
    step, story_id = args.step, story["story_id"]
    checked = story
    if args.prompt_only:
        checked = copy.deepcopy(story)
        checked["generation_profile"]["consistency_mode"] = refimages.PROMPT_ONLY
    params, units, default_cast = {}, None, None

    if step == "cast":
        workflow.require_style_approved(story)
        selected = args.characters
        if selected is None and not workflow.list_entities(stories, story_id, workflow.CHARACTERS):
            selected = default_cast = workflow.sketch_names(story)
        if selected:
            params["selected"] = list(selected)
        if items["--custom"]:
            params["custom"] = items["--custom"]
        selected, custom = workflow.cast_request(stories, story, params)
        units = workflow.cast_units(stories, checked, selected=selected, custom=custom)
    elif step == "places_proposal":
        workflow.require_places_proposable(stories, story)
    elif step == "places":
        if args.place is not None:
            params["places"] = items["--place"]
        if args.prop is not None:
            params["props"] = items["--prop"]
        workflow.require_places_ready(stories, story, params)
        workflow.places_request(stories, story, params)
        units = workflow.places_units(stories, checked, params)
    else:
        workflow.require_cast_approved(story)
        if args.episodes is not None:
            params["episodes"] = args.episodes
        workflow.season_request(params)
    return params, checked, units, default_cast


def _set_prompt_only(stories, story) -> None:
    """``--prompt-only``: the user's explicit switch to prompt-only
    consistency, set on the story and said. Never implied."""
    if story["generation_profile"]["consistency_mode"] == refimages.PROMPT_ONLY:
        print("🟡 Consistency mode: already prompt_only (--prompt-only).")
        return
    workflow.patch_story(stories, story["story_id"],
                         {"generation_profile": {"consistency_mode": refimages.PROMPT_ONLY}}, now=_now())
    print("🟡 Consistency mode: prompt_only (--prompt-only). Sheets and time variants are drawn from text, "
          "not edited from a reference image, and labelled prompt_only.")


def _print_cast_progress(stories, story_id) -> None:
    """What each character still lacks (``workflow.progress``, the story
    page's own), and -- when a sheet waits for an editor -- the editor's
    verdict and the prompt-only hint."""
    story = workflow.load(stories, story_id)
    progress = workflow.progress(stories, story, env={})
    docs = {doc["char_id"]: doc for doc in workflow.list_entities(stories, story_id, workflow.CHARACTERS)}
    for char_id, item in progress["characters"].items():
        doc = docs.get(char_id) or {"name": char_id, "approved_at": None}
        if not item["missing"]:
            print(f"👤 {doc['name']}: nothing missing, "
                  + ("approved" if doc["approved_at"] else "ready to approve"))
            continue
        labels = ", ".join(workflow.MISSING_LABELS[missing] for missing in item["missing"])
        print(f"👤 {doc['name']}: still missing {labels}"
              + (" (waiting for an editor)" if item["needs_editor"] else ""))
    if any(item["needs_editor"] for item in progress["characters"].values()):
        print(f"🟡 {progress['edit_readiness']['message']}")
        print(f"➡️ {PROMPT_ONLY_HINT}")


def _approve_complete(stories, story_id, kinds) -> None:
    """``--auto-approve`` of the cast or the places: every entity of *kinds*
    that has everything is approved by the API's rule
    (``workflow.approve_entity``: the store re-folds the group approval);
    each one that lacks something is named with what it lacks; one already
    approved is left as it is."""
    for kind in kinds:
        id_field = story_store.ENTITY_KINDS[kind].id_field
        docs = workflow.list_entities(stories, story_id, kind)
        if kind == workflow.CHARACTERS:
            docs = entities_step.cast_order(docs)
        for doc in docs:
            if doc["approved_at"]:
                print(f"✅ {doc['name']}: already approved.")
                continue
            try:
                workflow.approve_entity(stories, story_id, kind, doc[id_field], now=_now())
            except workflow.WorkflowError as exc:
                if exc.code != workflow.CONFLICT:
                    raise
                print(f"⏸ {exc}")
                continue
            print(f"✅ {doc['name']} approved.")


def _phase2_step(args, stories, story, items) -> int:
    """``cast``, ``places_proposal``, ``places`` or ``season``, in this
    process: the API's rules (``_phase2_params``), the key gate, the image
    chain's verdict when a picture would be made, then ``--prompt-only``,
    the run, what the cast still lacks, and ``--auto-approve``."""
    step, story_id = args.step, story["story_id"]
    params, checked, units, default_cast = _phase2_params(args, stories, story, items)

    refusal = _llm_refusal(args.allow_slow_chain)
    if refusal:
        _err(refusal)
        return EXIT_FAILED
    if units is not None and units["images"]:
        verdict = workflow.image_verdict(stories, checked, units["images"], env={})
        if not verdict["ready"]:
            _err(verdict["message"])
            return EXIT_FAILED

    if args.prompt_only:
        _set_prompt_only(stories, story)
    if default_cast:
        names = ", ".join(default_cast[:-1]) + (" and " if len(default_cast) > 1 else "") + default_cast[-1]
        print(f"👥 No --characters: the concept's cast sketch, {names}.")

    failure = None
    try:
        interrupted = _run_step(stories, story_id, step, params)
    except StepFailed as exc:
        if step != "cast":
            raise
        failure, interrupted = exc, None  # what each character lacks is said first
    if interrupted:
        return interrupted
    if step == "cast":
        _print_cast_progress(stories, story_id)
    if failure is not None:
        raise failure

    if args.auto_approve:
        if step == "cast":
            _approve_complete(stories, story_id, (workflow.CHARACTERS,))
        elif step == "places":
            _approve_complete(stories, story_id, (workflow.PLACES, workflow.PROPS))
        else:
            workflow.approve_season(stories, story_id, now=_now())
            print("✅ Season approved.")
    print(_line(workflow.load(stories, story_id)))
    return EXIT_OK


_COMMANDS = {"new": _cmd_new, "step": _cmd_step, "list": _cmd_list}


def main(argv=None) -> int:
    """Run one command; returns the exit code (see the module docstring)."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help, or a usage error argparse has printed
        return exc.code if isinstance(exc.code, int) else (EXIT_OK if exc.code is None else EXIT_USAGE)

    # The clip CLI's .env loading (clipping.config reads it at import), so a
    # key or LLM_CHAIN set there counts here too. Already done when main.py
    # dispatched to us; this covers any other caller.
    import clipping.config  # noqa: F401

    outputs_dir = args.outputs_dir or story_store.default_outputs_dir()
    stories = story_store.StoryStore(outputs_dir)
    try:
        return _COMMANDS[args.command](args, stories)
    except (workflow.WorkflowError, workflow.StoryUnreadable, StepFailed) as exc:
        _err(str(exc))
        return EXIT_FAILED
    except KeyboardInterrupt:
        _err("Interrupted.")
        return EXIT_INTERRUPTED


if __name__ == "__main__":  # pragma: no cover - main.py is the entry point
    sys.exit(main())
