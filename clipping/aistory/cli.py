"""``python main.py --ai-story ...`` -- AI Story steps 1-13 from a terminal (spec 9.3).

``main.py`` hands everything after ``--ai-story`` to :func:`main` before the
clip parser sees it (DEC-114): this is a parser of its own, and the clip CLI's
options and defaults are untouched. Commands::

    main.py --ai-story new --lang fr [--concept ID] [--style ID] [--seed-text TEXT]
                           [--tier N] [--route R] [--consistency-mode M] [--budget-profile P]
    main.py --ai-story step <story_id> concepts|bible|style|style_preview [options]
    main.py --ai-story step <story_id> cast|places_proposal|places|season|knowledge [options]
    main.py --ai-story step <story_id> script|storyboard --ep N [options]
    main.py --ai-story step <story_id> assets|render|metadata --ep N [options]
    main.py --ai-story step <story_id> assets --ep N [--tier T] [--route R] [--no-animate] [--estimate]
    main.py --ai-story step <story_id> memory|feedback|propose-next --ep N [options]
    main.py --ai-story step <story_id> rerender --ep N [--dry-run]
    main.py --ai-story render <story_id> --ep N [options]
    main.py --ai-story fast-track <story_id> --ep N [options]
    main.py --ai-story feedback <story_id> --ep N --text-file F [--stats-file F] [options]
    main.py --ai-story approve <story_id> keyframes:N [--anyway]
    main.py --ai-story voice-tails <story_id> --ep N
    main.py --ai-story list
    main.py --ai-story prompt-limits

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
approved: its places are chosen with the ``places`` step. ``knowledge``
(phase 7 stage 5b, a v2 story whose season is approved) writes the knowledge
base; ``--auto-approve`` approves it once complete (``approve_knowledge``).

Phase 3 (steps 8-9): ``script`` and ``storyboard`` write one episode
(``--ep N``, required for these two steps; the season decides which numbers
exist). Preconditions, parameters and approvals are ``workflow``'s alone
(DEC-114): the story ``ready``, the episode one the season plans, and -- from
episode 2 on -- the previous episode's series memory written, approved and
fresh (the memory step; DEC-130 as amended by plan 11 stage 4). ``script`` takes
``--measure-voices`` (after writing, every line is spoken through its
character's pinned voice and the audio kept) or ``--check-only`` (plan 19
stage 3: the consistency check and, on v2, the first-watch check on the
script as it stands -- nothing written or repaired; a script not complete
is refused); ``storyboard`` needs a complete
script and takes ``--fast`` (every scene's shots planned deterministically,
in this process, with no LLM call and so no key gate -- otherwise the step
runs through the worker's registry like any other LLM step). A short summary
of the episode follows the run: scenes written, the timing line, the
consistency report's state, and -- a storyboard -- its shots over its
scenes. ``--auto-approve`` applies the workflow's own approval rule and never
"approves anyway": a script needs a passing, current consistency check; a
storyboard needs an approved script with shots for every scene, none stale.

Phase 4 (steps 10-12 and the fast track): ``assets``, ``render`` and
``metadata`` write one episode (``--ep``, as phase 3); ``render`` and
``fast-track`` are also their own top-level commands, ``render`` an alias of
``step <id> render --ep N`` and ``fast-track`` the DEC-162 job from the
script to the metadata pack. Preconditions and parameters are ``workflow``'s
(DEC-114): ``assets`` takes ``--align-words`` (opt-in forced-alignment word
timings, the STT chain, instead of an even split); ``render`` takes
``--subtitles`` and ``--encoder`` (``clipping.aistory.steps.render``'s own
closed lists); ``metadata`` takes no parameters; ``fast-track`` takes
``--storyboard`` (``t1`` or ``fast``). The key gate applies to ``metadata``
and ``fast-track`` (both call the LLM chain); ``render`` calls no API, and
``assets`` meets the image and voice chains' own gates inside the step,
stopping before its first call when a paid part is over a cap (with the
numbers) -- never a wasted call. ``--auto-approve`` applies to ``assets``
alone (``AUTO_APPROVABLE``): it approves the grid (``workflow.approve_assets``)
only once every shot is current or locked and every line voiced, refused
with its own reason otherwise (never 'approve anyway'); ``render``,
``metadata`` and ``fast-track`` have nothing to approve -- their job ends
completed. A short summary follows every run (images made and cached, lines
voiced, render duration and loudness, metadata platforms, the fast track's
sub-steps).

Phase 6 (tiers 2 and 3, stage 11): ``assets`` takes ``--tier 1|2|3`` and
``--route auto|local|api`` -- they patch the story's ``generation_profile``
(``workflow.patch_story``, the API's ``PATCH /stories/{id}``) and print it
before anything runs; there is no run-level override -- ``--no-animate``
(the step's ``animate`` param off: no clip in this run) and ``--estimate``
(``workflow.assets_estimate`` printed as JSON -- images, voices and, at tier 2
or 3, the clips -- calling nothing and running no step; a local ComfyUI is
not asked). ``render`` (and the ``render`` command) takes
``--fill-failed-with-motion``. A clip regenerate with a note
(``shot:<ep>:<shid>:video``) is the dashboard's or the API's -- the CLI has
no ``regenerate`` command; running ``assets`` again makes a failed or stale
clip again.

Phase 5 (step 13, plan 11 stage 5): ``memory``, ``feedback`` and
``propose-next`` each write one episode (``--ep N``), one free-chain LLM
call (S3, F1, N1); preconditions, parameters and approvals are
``workflow``'s (DEC-114), the same as the API's (``workflow.series_context``
/ ``series_request``). ``memory`` needs episode N's script approved;
``feedback`` needs its audience comments pasted first, with the ``feedback``
command (its own top-level command, since pasting a file is not a ``step``
option: ``--ep N --text-file F [--stats-file F]``, refused whole, never
trimmed, over 6,000 characters each, naming the size) -- it also runs the
``feedback`` step once the paste is stored, so it is the one-command way to
go from a comments file to a digest; ``propose-next`` needs episode N's own
series memory written, approved and fresh (the same gate episode N + 1's
script or storyboard meets, DEC-130 as amended). ``--auto-approve``
(``AUTO_APPROVABLE``) applies to ``memory`` (``workflow.approve_memory``)
and to ``feedback`` (``workflow.approve_feedback`` with no direction chosen
-- choosing one of F1's three digested directions needs the dashboard or the
API, which show them first); ``propose-next`` never takes it, because each
proposed character and twist needs a human decision (``POST .../episodes/
{ep}/proposals/{item_id}``, from the dashboard or the API only -- there is
no CLI command for it). A short summary follows every run (hooks opened and
closed, directions digested, characters and twists proposed).

Phase 5, plan 11 stage 9: ``rerender`` renders one episode (``--ep N``)
again from its documents as they are now, making again only the shot clips
that changed since its last good render; preconditions are ``workflow``'s,
the same as the API's (``workflow.episode_context`` then ``workflow.
require_reedit_inputs``: a finished render to re-render, then every render
precondition, naming an outdated shot's regenerate target). It takes no
other parameter and calls no API, so there is no key gate; ``--auto-approve``
does not apply to it (it ends completed, nothing to approve). ``--dry-run``
prints the same count the run would make -- how many shots, which ones and
why (``workflow.reedit_changes``, also ``GET /estimate/rerender``'s own) --
without rendering anything. A short summary follows a real run (how many of
the episode's shots were made again versus reused, the render's own
numbers).

Phase 7 stage 6b: ``approve <story_id> keyframes:N`` approves a v2 story's
episode N keyframes (``workflow.approve_keyframes``, the API's own rule:
every shot with a current keyframe, every keyframe check (J2) passed --
``--anyway`` goes over a failed or missing one, and the approval records
it); no clip of a v2 episode is bought before it is current. It is the one
document approved by a command of its own: a person approves keyframes
after looking at them, so no step's ``--auto-approve`` ever does. Any other
document is refused, pointing at ``--auto-approve``.

``voice-tails <story_id> --ep N`` (phase 7 follow-up) prints, per line of
the episode, what the Gemini tail guard would cut -- or cut -- at the end of
its kept audio (``voice_lines.tail_report``: the file's length, the cut, its
reason), and changes nothing: the assets step is what cleans a line voiced
before the guard, for free. A way for a person to check the "crshhh" fix.

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

from clipping.providers import tts_tail

from . import defaults, media_policy, refimages, schemas, templates, workflow
from . import store as story_store
from .steps import StepFailed
from .steps import assets as assets_step
from .steps import entities as entities_step
from .steps import episode_common
from .steps import fast_track as fast_track_step
from .steps import render as render_step
from .steps import script as script_step
from .steps import voice_lines

PROG = "main.py --ai-story"

# Where a CLI user sets a key: the refusal's "Set one of: ..., <where>."
KEYS_WHERE = "in the environment or in .env"

# ``--settings`` (the log sweep, 2026-10-02): the dashboard's stored Settings
# over the process environment, as the API reads them -- the VPS keeps its
# keys there. Empty without the flag: the process environment alone, as
# before. The file is the one ``web.api.settings_store`` writes, read here
# without importing ``web`` (the CLI never does);
# tests/test_story_cli_settings.py pins the two to the same path and reading.
_SETTINGS_ENV: dict = {}
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                             "data", "settings.json")


def settings_file() -> str:
    """Where the dashboard's Settings are stored: ``WEB_SETTINGS_FILE``, else
    ``data/settings.json`` at the repository's root."""
    return os.environ.get("WEB_SETTINGS_FILE") or SETTINGS_FILE


def stored_settings(path=None) -> dict:
    """The stored Settings as ``{name: value}`` (strings, empty values left
    out), or ``{}`` when the file is missing or unreadable -- never raises,
    like the dashboard's own reader."""
    try:
        with open(path or settings_file(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if value not in (None, "")}


def _settings_env() -> dict:
    """The Settings values this run's steps, estimates and gates read: the
    stored ones with ``--settings``, else none."""
    return dict(_SETTINGS_ENV)


def _load_settings() -> None:
    """Read the stored Settings into :data:`_SETTINGS_ENV` and say how many
    values from where -- never a value."""
    stored = stored_settings()
    _SETTINGS_ENV.clear()
    _SETTINGS_ENV.update(stored)
    print(f"Using the stored Settings: {len(stored)} value{'' if len(stored) == 1 else 's'} from "
          f"{settings_file()}, over the environment (no value is printed).")

# The StepContext.job_id of a step run here: there is no job.
CLI_JOB_ID = "cli"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

# Phase 4's steps the `step` command runs (spec 3 steps 10-12); `fast-track`
# is its own top-level command, not one of `step`'s (module docstring).
_PHASE4_JOB_STEPS = ("assets", "render", "metadata")

# Every step `step` runs, phase 1 then phase 2 then phase 3 then phase 4 then
# phase 5's series steps (step 13: memory, feedback, propose-next) then its
# re-edit render (plan 11 stage 9: rerender).
STEPS = workflow.PHASE1_STEPS + workflow.PHASE2_STEPS + workflow.PHASE3_STEPS + _PHASE4_JOB_STEPS + \
    workflow.SERIES_STEPS + workflow.REEDIT_STEPS

# The steps --auto-approve approves, and what to do for the others. `memory`
# approves like `bible`/`season` (workflow.approve_memory, unconditionally);
# `feedback` approves with no direction chosen (workflow.approve_feedback,
# direction=None) -- choosing one needs the dashboard or the API, which read
# the three digested directions first; `propose-next` is never auto-approved
# (below): each item needs a human decision.
AUTO_APPROVABLE = ("bible", "style", "cast", "places", "season", "knowledge") + workflow.PHASE3_STEPS + \
    ("assets", "memory", "feedback")
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
    "render": "the render ends completed once it is done: there is nothing to approve.",
    "metadata": "the metadata pack ends completed once it is written: there is nothing to approve.",
    "rerender": "the re-render ends completed once it is done: there is nothing to approve.",
    "propose-next": (
        "each proposed character and twist needs a human decision, from the dashboard or the API "
        "(POST .../episodes/{ep}/proposals/{item_id}): accept or reject it, then approve "
        "proposals:<ep> once every item is decided."
    ),
}

# The steps that call the LLM chain, and so meet the key gate (the storyboard
# with --fast calls nothing, but the option applies to the step, not the flag
# combination: the gate itself is skipped for --fast in _phase3_step; `assets`
# and `render` call no LLM, so they are not here -- `assets` meets the image
# and voice chains' own gates instead, inside the step). `memory`, `feedback`
# and `propose-next` each make one free-chain call (S3, F1, N1).
_KEYED_STEPS = workflow.LLM_STEPS + workflow.PHASE2_STEPS + workflow.PHASE3_STEPS + ("metadata",) + \
    workflow.SERIES_STEPS

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
    ("ep", "--ep", workflow.PHASE3_STEPS + _PHASE4_JOB_STEPS + workflow.SERIES_STEPS + workflow.REEDIT_STEPS),
    ("fast", "--fast", ("storyboard",)),
    ("measure_voices", "--measure-voices", ("script",)),
    ("check_only", "--check-only", ("script",)),
    ("align_words", "--align-words", ("assets",)),
    ("tier", "--tier", ("assets",)),
    ("route", "--route", ("assets",)),
    ("no_animate", "--no-animate", ("assets",)),
    ("estimate", "--estimate", ("assets",)),
    ("subtitles", "--subtitles", ("render",)),
    ("encoder", "--encoder", ("render",)),
    ("fill_failed_with_motion", "--fill-failed-with-motion", ("render",)),
    ("allow_slow_chain", "--allow-slow-chain", _KEYED_STEPS),
    ("dry_run", "--dry-run", workflow.REEDIT_STEPS),
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

class _ProfileFlag(argparse.Action):
    """A ``new`` profile option that also notes it was given: a story created
    with none of them gets the quality preset when the keys are set (phase 7,
    ``media_policy.new_story_profile``); one that names any is honoured as
    sent."""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        given = set(getattr(namespace, "profile_given", None) or ())
        given.add(self.dest)
        namespace.profile_given = given


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "AI Story, steps 1-9: create a story, write its concepts and bible, build\n"
            "and lock its style, then make its cast, places and props, and season arc,\n"
            "then write and storyboard its episodes.\n"
            "Keys, LLM_CHAIN and the image and voice chains come from the environment\n"
            "(or .env); add --settings to read the dashboard's stored Settings over it."
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
            f"  {PROG} step STORY_ID script --ep 1 --auto-approve\n"
            f"  {PROG} step STORY_ID storyboard --ep 1 --fast\n"
            f"  {PROG} step STORY_ID assets --ep 1 --auto-approve\n"
            f"  {PROG} step STORY_ID metadata --ep 1\n"
            f"  {PROG} render STORY_ID --ep 1 --subtitles word_pop\n"
            f"  {PROG} fast-track STORY_ID --ep 1 --storyboard fast\n"
            f"  {PROG} step STORY_ID memory --ep 1 --auto-approve\n"
            f"  {PROG} feedback STORY_ID --ep 1 --text-file comments.txt --auto-approve\n"
            f"  {PROG} step STORY_ID propose-next --ep 1\n"
            f"  {PROG} list\n"
            f"  {PROG} prompt-limits"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Hidden, for tests: another outputs/ directory than the repository's.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--outputs-dir", default=None, help=argparse.SUPPRESS)
    common.add_argument("--settings", action="store_true",
                        help="read the keys, chains, caps and allow_paid stored by the dashboard's Settings "
                             "(data/settings.json, or WEB_SETTINGS_FILE) over the environment")

    commands = parser.add_subparsers(dest="command",
                                     metavar="{new,step,render,fast-track,feedback,approve,list,prompt-limits}", required=True)

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
    # Without any of the four below, a story is created on the quality preset
    # (v2, tier 2, api, references, quality) when FAL_KEY is set (stage 2c,
    # DEC-235: media_policy.QUALITY_KEYS), else on these defaults (phase 7).
    new.add_argument("--tier", type=int, choices=defaults.TIERS, default=defaults.DEFAULT_TIER,
                     action=_ProfileFlag, help="generation tier (default: %(default)s)")
    new.add_argument("--route", choices=defaults.ROUTES, default=defaults.DEFAULT_ROUTE,
                     action=_ProfileFlag, help="where images are made (default: %(default)s)")
    new.add_argument("--consistency-mode", choices=defaults.CONSISTENCY_MODES,
                     default=defaults.DEFAULT_CONSISTENCY_MODE, action=_ProfileFlag,
                     help="how characters are kept consistent (default: %(default)s)")
    new.add_argument("--budget-profile", choices=defaults.BUDGET_PROFILES,
                     default=defaults.DEFAULT_BUDGET_PROFILE, action=_ProfileFlag,
                     help="the story's budget profile (default: %(default)s)")

    # ---- step
    step = commands.add_parser(
        "step", parents=[common], help=f"run one step of a story: {', '.join(STEPS)}",
        description=(
            "Run one step in this process. concepts and bible call the LLM chain; style "
            "builds the draft style lock here; style_preview calls the image chain; cast, "
            "places_proposal, places and season call the LLM chain, and the cast and the "
            "places the image and voice chains too; knowledge (a v2 story, once its season "
            "is approved) writes the knowledge base, one LLM call at a time; script and storyboard write one "
            "episode (--ep) and call the LLM chain too, unless storyboard is run --fast; "
            "assets makes one episode's images, voices and sounds (--ep), meeting the image "
            "and voice chains' own gates; render turns them into episode_final.mp4 (--ep), "
            "calling no API; metadata writes the publishing pack (--ep, one M1 call per "
            "platform); memory (--ep) writes episode ep's series memory from its approved "
            "script (S3), which episode ep + 1 needs, approved, before it can be written; "
            "feedback (--ep) digests the audience comments pasted for episode ep (F1; paste "
            "them first with the 'feedback' command); propose-next (--ep) proposes new "
            "characters and twists for episode ep + 1 from episode ep's approved memory (N1) "
            "-- each item is decided from the dashboard or the API, never here; rerender "
            "(--ep) renders episode ep again from its documents as they are now, making again "
            "only the shot clips that changed since its last good render, calling no API -- "
            "--dry-run prints what it would re-render without rendering."
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
    step.add_argument("--ep", type=int, default=None, metavar="N",
                      help=("script, storyboard, assets, render, metadata, memory, feedback, propose-next, "
                            "rerender: the episode number to run it on (required for these steps; the "
                            "season plans which numbers exist; propose-next writes for episode N + 1)"))
    step.add_argument("--fast", action="store_true",
                      help=("storyboard only: plan every scene's shots deterministically, in this "
                            "process, with no LLM call and so no key gate"))
    step.add_argument("--measure-voices", action="store_true",
                      help=("script only: after writing, measure every line with its speaker's pinned "
                            "voice and keep the audio"))
    step.add_argument("--check-only", action="store_true",
                      help=("script only: check the script as it stands (the consistency check, and on a v2 "
                            "story the first-watch check) -- nothing written or repaired; refused for a script "
                            "not complete yet"))
    step.add_argument("--align-words", action="store_true",
                      help=("assets only: opt-in forced-alignment word timings (the STT chain) instead "
                            "of an even split, stored per line"))
    step.add_argument("--tier", type=int, choices=defaults.TIERS, default=None,
                      help=("assets only: set the story's generation tier (1 stills with motion, 2 and 3 "
                            "animated shots) before the run -- the story's profile, not a run-level override"))
    step.add_argument("--route", choices=defaults.ROUTES, default=None,
                      help=("assets only: set the story's route (where images and clips are made) before "
                            "the run -- the story's profile, not a run-level override"))
    step.add_argument("--no-animate", action="store_true",
                      help="assets only: make no clip in this run (tier 2 and 3 animate by default)")
    step.add_argument("--estimate", action="store_true",
                      help=("assets only: print what the step would make and spend (images, voices and, at "
                            "tier 2 or 3, clips) and run nothing: no call, no step"))
    step.add_argument(
        "--subtitles", choices=render_step.SUBTITLE_CHOICES, default=None, metavar="MODE",
        help=(f"render only: the subtitle mode, one of {', '.join(render_step.SUBTITLE_CHOICES)} "
              f"(default: {render_step.STYLE_SUBTITLES}, the style lock's own)"),
    )
    step.add_argument(
        "--encoder", choices=render_step.ENCODER_CHOICES, default=None, metavar="ENC",
        help=(f"render only: the final pass's encoder, one of {', '.join(render_step.ENCODER_CHOICES)} "
              f"(default: {render_step.DEFAULT_ENCODER})"),
    )
    step.add_argument("--fill-failed-with-motion", action="store_true",
                      help=("render only: at tier 2 or 3, give a shot whose clip failed, went stale or is still "
                            "generating Tier-1 motion instead of refusing the render"))
    step.add_argument("--dry-run", action="store_true",
                      help=("rerender only: print what a re-render would make again now -- how many of "
                            "the episode's shots, which ones and why -- without rendering anything"))
    step.add_argument("--auto-approve", action="store_true",
                      help=("bible, style, season: approve the result once the step is done; cast, "
                            "places: approve every character, place and prop that has everything; "
                            "script, storyboard: approve it once the workflow's own rule passes (never "
                            "'approve anyway'); assets: approve the grid once every shot is current or "
                            "locked and every line voiced (workflow.approve_assets, also never 'approve "
                            "anyway'); memory: approve the entry once it is written; feedback: approve "
                            "the digest with no direction chosen (choosing one needs the dashboard or the "
                            "API); propose-next does not take it -- each item needs a human decision"))
    step.add_argument("--allow-slow-chain", action="store_true",
                      help=("the steps that call the LLM chain: run on the chain's slow floor alone; "
                            "also settable as ALLOW_SLOW_CHAIN=1"))

    # ---- render (alias of 'step ID render --ep N')
    render_cmd = commands.add_parser(
        "render", parents=[common], help="render one episode (alias of 'step ID render --ep N')",
        description="Render episode --ep to episode_final.mp4; exactly 'step ID render --ep N', spelled shorter.",
    )
    render_cmd.add_argument("story_id", help="the story's id (see 'list')")
    render_cmd.add_argument("--ep", type=int, required=True, metavar="N", help="the episode number to render")
    render_cmd.add_argument(
        "--subtitles", choices=render_step.SUBTITLE_CHOICES, default=None, metavar="MODE",
        help=(f"the subtitle mode, one of {', '.join(render_step.SUBTITLE_CHOICES)} "
              f"(default: {render_step.STYLE_SUBTITLES}, the style lock's own)"),
    )
    render_cmd.add_argument(
        "--encoder", choices=render_step.ENCODER_CHOICES, default=None, metavar="ENC",
        help=(f"the final pass's encoder, one of {', '.join(render_step.ENCODER_CHOICES)} "
              f"(default: {render_step.DEFAULT_ENCODER})"),
    )
    render_cmd.add_argument("--fill-failed-with-motion", action="store_true",
                            help=("at tier 2 or 3, give a shot whose clip failed, went stale or is still "
                                  "generating Tier-1 motion instead of refusing the render"))

    # ---- fast-track
    fast_track_cmd = commands.add_parser(
        "fast-track", parents=[common], help="one episode, script through metadata, in a single job",
        description=(
            "Run episode --ep from its script to its metadata pack in a single job (DEC-162): "
            "auto-approves what passes the workflow's own rule -- on a v2 story also the keyframes, and the "
            "script once its repair passes are spent, 'anyway', naming what is still flagged -- and "
            "stops before any paid generation call unless allow_paid is on and every cap fits."
        ),
    )
    fast_track_cmd.add_argument("story_id", help="the story's id (see 'list')")
    fast_track_cmd.add_argument("--ep", type=int, required=True, metavar="N",
                                help="the episode number to fast-track")
    fast_track_cmd.add_argument(
        "--storyboard", choices=fast_track_step.STORYBOARD_CHOICES, default=None, metavar="MODE",
        help=(f"how the shots are planned, one of {', '.join(fast_track_step.STORYBOARD_CHOICES)} "
              f"(default: {fast_track_step.T1}, one T1 call per scene)"),
    )
    fast_track_cmd.add_argument(
        "--stop-at-keyframes", action="store_true", default=False,
        help=("on a v2 story at tier 2 or 3: stop once the keyframes are made and checked (J2), for your own "
              "'approve ID keyframes:N' (default: the fast track approves them itself and goes up to the render)"),
    )
    fast_track_cmd.add_argument(
        "--stop-on-script-issues", action="store_true", default=False,
        help=("on a v2 story: stop at the script over blocking issues its repair passes could not fix (default: "
              "once the repair passes are spent, the fast track approves the script anyway and names the issues)"),
    )

    # ---- feedback (phase 5, step 13: paste, then 'step ID feedback --ep N')
    feedback_cmd = commands.add_parser(
        "feedback", parents=[common], help="paste one episode's audience feedback, then digest it (F1)",
        description=(
            "Paste episode --ep's audience comments (and, optionally, its stats) from a file -- exactly "
            "what 'POST .../episodes/{ep}/feedback' stores, refused whole over 6,000 characters each, "
            "never trimmed -- replacing any earlier paste of that episode, then run the feedback step "
            "(F1) on it, meeting the key gate as any other LLM step."
        ),
    )
    feedback_cmd.add_argument("story_id", help="the story's id (see 'list')")
    feedback_cmd.add_argument("--ep", type=int, required=True, metavar="N",
                              help="the episode number the feedback is for")
    feedback_cmd.add_argument("--text-file", required=True, metavar="FILE",
                              help="a text file (UTF-8) of the audience comments to paste, at most 6,000 characters")
    feedback_cmd.add_argument("--stats-file", default=None, metavar="FILE",
                              help=("a text file (UTF-8) of the episode's stats to paste alongside it, at most "
                                    "6,000 characters; optional"))
    feedback_cmd.add_argument("--auto-approve", action="store_true",
                              help="approve the digest once it is done, with no direction chosen")

    # ---- approve (phase 7 stage 6b: a v2 episode's keyframes)
    approve_cmd = commands.add_parser(
        "approve", parents=[common], help="approve a v2 episode's keyframes (keyframes:N)",
        description=(
            "Approve episode N's keyframes on a v2 story (keyframes:N), by the API's own rule: every shot "
            "with a current keyframe and every keyframe check (J2) passed; --anyway goes over a failed or "
            "missing check, and the approval records it. No clip of a v2 episode is bought before its "
            "keyframes are approved. The other documents are approved with 'step ... --auto-approve'."
        ),
    )
    approve_cmd.add_argument("story_id", help="the story's id (see 'list')")
    approve_cmd.add_argument("doc", metavar="keyframes:N", help="the document to approve: keyframes:N")
    approve_cmd.add_argument("--anyway", action="store_true",
                             help="approve over keyframe checks (J2) that failed or have not run")

    # ---- voice-tails (phase 7 follow-up: the Gemini tail guard, read only)
    tails_cmd = commands.add_parser(
        "voice-tails", parents=[common], help="what the Gemini tail guard cuts from an episode's lines (read only)",
        description=(
            "Print, per line of episode --ep, its kept audio's length and what the Gemini tail guard would cut "
            "(or cut) from its end -- the static Gemini TTS adds after the last word -- and why. Changes "
            "nothing: the assets step cleans a line voiced before the guard, for free."
        ),
    )
    tails_cmd.add_argument("story_id", help="the story's id (see 'list')")
    tails_cmd.add_argument("--ep", type=int, required=True, metavar="N", help="the episode number to check")

    # ---- list
    commands.add_parser("list", parents=[common], help="list the stories",
                        description="One line per story: id, status, language, title.")

    # ---- prompt-limits
    commands.add_parser(
        "prompt-limits", parents=[common], help="list each generation link's prompt size limit",
        description="One line per image, video and voice link: the prompt size it accepts (a longer prompt "
                    "is refused before it is sent), the word budget that fits it, and where the number comes "
                    "from -- the vendor's own, or our estimate where none is published.",
    )
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

    _links, _keys, refusal = workflow.llm_gate(_settings_env(), readiness=readiness, where=KEYS_WHERE)
    return refusal


def _run_step(stories, story_id, step, params, ep=None):
    """Run *step* through the worker's registry, in this process, with a real
    cancel token: Ctrl-C cancels it. *ep* is the episode number for a phase-3
    or phase-4 step (``StepContext.ep``), None for every other step. Returns
    ``(EXIT_INTERRUPTED, None)`` when it was interrupted, else ``(None,
    result)`` with what the runner returned -- phase 4's summaries read it;
    phase 1-3 read their summary from disk instead (``workflow.episode_view``)
    and leave it unused. ``StepFailed`` propagates."""
    from clipping.cancel import Cancelled, CancelToken

    from . import steps

    token = CancelToken()
    ctx = steps.StepContext(
        job_id=CLI_JOB_ID,
        story_id=story_id,
        step=step,
        ep=ep,
        params=params,
        cancel=token,
        # Keys and chains from the process environment (llm_call's fallback),
        # under the stored Settings with --settings.
        settings_env=_settings_env(),
        outputs_dir=stories.outputs_dir,
        on_log=print,
    )
    try:
        result = steps.run(step, ctx)
    except (KeyboardInterrupt, Cancelled):
        token.cancel()
        _err("Cancelled. What the step had already written stays.")
        return EXIT_INTERRUPTED, None
    return None, result


# ---------------------------------------------------------------- commands

def _cmd_new(args, stories) -> int:
    profile = {
        "tier": args.tier,
        "route": args.route,
        "consistency_mode": args.consistency_mode,
        "budget_profile": args.budget_profile,
    }
    if not getattr(args, "profile_given", None):
        # No profile option given: the quality preset when this process's
        # environment holds both quality keys (the API's rule, on its Settings).
        profile = media_policy.new_story_profile(_settings_env()) or profile
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


def _episode_summary_line(entry) -> str:
    """One episode of ``workflow.episode_summaries``'s list, e.g. ``"ep1
    script approved, storyboard complete, 62.4 s"``."""
    parts = [f"script {entry['script_state']}", f"storyboard {entry['storyboard_state']}"]
    if entry["total_s"] is not None:
        parts.append(f"{entry['total_s']:.1f} s")
    return f"ep{entry['ep']} " + ", ".join(parts)


def _cmd_list(args, stories) -> int:
    entries = stories.list()
    if not entries:
        _err(f"No stories in {stories.root}.")
    for entry in entries:
        line = _line(entry)
        episodes = workflow.episode_summaries(stories, entry)
        if episodes:
            line += "; " + "; ".join(_episode_summary_line(item) for item in episodes)
        print(line)
    return EXIT_OK


def _cmd_step(args, stories) -> int:
    step = args.step

    # Every refusal of the command line comes before anything is read or spent.
    if args.auto_approve and step not in AUTO_APPROVABLE:
        return _usage_error("step", f"--auto-approve does not apply to '{step}': {_NOT_AUTO_APPROVABLE[step]}")
    if args.auto_approve and args.estimate:
        return _usage_error("step", "--auto-approve does not apply with --estimate: it runs nothing to approve.")
    for dest, flag, applies in _STEP_ONLY:
        if _given(getattr(args, dest)) and step not in applies:
            return _usage_error("step", f"{flag} applies to {_quoted(applies)} only, not to '{step}'.")
    if (step in workflow.PHASE3_STEPS + _PHASE4_JOB_STEPS + workflow.SERIES_STEPS + workflow.REEDIT_STEPS
            and args.ep is None):
        return _usage_error("step", f"--ep is required for '{step}': which episode to run it on.")
    try:
        overrides = parse_overrides(args.override)
        items = {flag: parse_items(flag, getattr(args, flag[2:])) for flag in _ITEM_SHAPES}
    except ValueError as exc:
        return _usage_error("step", str(exc))

    story_id = args.story_id
    story = workflow.load(stories, story_id)

    if step in workflow.PHASE2_STEPS:
        return _phase2_step(args, stories, story, items)

    if step in workflow.PHASE3_STEPS:
        return _phase3_step(args, stories, story)

    if step in _PHASE4_JOB_STEPS:
        return _phase4_step(args, stories, story)

    if step in workflow.SERIES_STEPS:
        return _series_step(args, stories, story)

    if step in workflow.REEDIT_STEPS:
        return _reedit_step(args, stories, story)

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
        interrupted, _result = _run_step(stories, story_id, runner_step, params)
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
    interrupted, _result = _run_step(stories, story_id, step, {})
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
    if args.prompt_only and media_policy.is_v2(story):
        raise workflow.WorkflowError(
            workflow.INVALID,
            "--prompt-only cannot be used on a v2 story: its images run on quality links with their reference "
            "images and never fall back to prompt-only consistency (DEC-221).")
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
    elif step == "knowledge":
        workflow.require_knowledge_runnable(story)
        workflow.knowledge_request(params)
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
    progress = workflow.progress(stories, story, env=_settings_env())
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
        if media_policy.is_v2(story):
            print(f"➡️ {refimages.quality_advice(progress['edit_readiness'])}")
        else:
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
    """``cast``, ``places_proposal``, ``places``, ``season`` or ``knowledge``, in this
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
        verdict = workflow.image_verdict(stories, checked, units["images"], env=_settings_env())
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
        interrupted, _result = _run_step(stories, story_id, step, params)
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
        elif step == "knowledge":
            workflow.approve_knowledge(stories, story_id, now=_now())
            print("✅ Knowledge base approved.")
        else:
            workflow.approve_season(stories, story_id, now=_now())
            print("✅ Season approved.")
    print(_line(workflow.load(stories, story_id)))
    return EXIT_OK


# ------------------------------------------------------------------ phase 3

def _print_episode_summary(stories, story, ep, step) -> None:
    """After ``script`` or ``storyboard``: a short summary of what is on
    disk now (``workflow.episode_view``, the same read the API's episode
    page uses) -- scenes written, the timing line (seconds, estimated or
    measured, where it sits in the template's window, its flags) and the
    consistency report's state; for a storyboard, its shots over its
    scenes. Printed whatever the run's outcome, so it also covers a rerun
    that wrote nothing new."""
    view = workflow.episode_view(stories, story, ep)
    script, board, state = view["script"], view["storyboard"], view["state"]
    if script and script["scenes"]:
        count = len(script["scenes"])
        print(f"📄 Episode {ep}: {count} scene{'' if count == 1 else 's'} written, report {state['report']}.")
        print(episode_common.timing_line(script))
    if step == "storyboard" and board and board["shots"]:
        print(f"🎞 Episode {ep}: {len(board['shots'])} shots over {len(board['scenes'])} scenes.")


def _phase3_step(args, stories, story) -> int:
    """``script`` or ``storyboard`` of episode ``args.ep``: exactly the
    rules ``workflow`` applies for the API (DEC-114, one set of rules, two
    front ends).

    In the workflow's order: the episode's preconditions
    (``workflow.episode_context`` -- the story ready, the episode one the
    season plans, and from episode 2 on the previous episode's series memory
    written, approved and fresh, naming which), then the step's own parameters
    (``workflow.script_request``/``storyboard_request``), then -- a
    storyboard, fast or not -- a complete script
    (``workflow.require_complete_script``). ``--fast`` builds the storyboard
    here, in this process, with no LLM call and so no key gate
    (``workflow.build_fast_storyboard``); otherwise the key gate applies as
    it does for any other LLM step and the step runs through the worker's
    registry, carrying *ep*. A short summary of the episode follows
    (:func:`_print_episode_summary`), then ``--auto-approve`` -- the
    workflow's own approval rule, never 'approve anyway': a refusal (a
    consistency report with issues for a script, an unfinished or outdated
    storyboard) is a ``WorkflowError`` left to propagate, exactly as the
    CLI already answers a refused bible or season approval."""
    step, story_id, ep = args.step, story["story_id"], args.ep
    ec = workflow.episode_context(stories, story, ep, step=step)
    if step == "script":
        params = {script_step.MEASURE_PARAM: True} if args.measure_voices else {}
        if args.check_only:
            params[script_step.CHECK_ONLY_PARAM] = True
        workflow.script_request(params)
        if workflow.script_check_only(params):
            workflow.require_checkable_script(ec)
        fast = False
    else:
        params = {"fast": True} if args.fast else {}
        fast = workflow.storyboard_request(params)
        workflow.require_complete_script(ec)

    if fast:
        workflow.build_fast_storyboard(stories, story, ep, now=_now(), on_log=print)
    else:
        refusal = _llm_refusal(args.allow_slow_chain)
        if refusal:
            _err(refusal)
            return EXIT_FAILED
        interrupted, _result = _run_step(stories, story_id, step, params, ep=ep)
        if interrupted:
            return interrupted

    _print_episode_summary(stories, story, ep, step)

    if args.auto_approve:
        if step == "script":
            workflow.approve_script(stories, story_id, ep, now=_now())
            print("✅ Script approved.")
        else:
            workflow.approve_storyboard(stories, story_id, ep, now=_now())
            print("✅ Storyboard approved.")
    print(_line(workflow.load(stories, story_id)))
    return EXIT_OK


# ------------------------------------------------------------------ phase 4

def _and(items) -> str:
    """``a``, ``a and b``, ``a, b and c`` (``assets._and``, duplicated for
    the CLI's own summaries)."""
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _render_params(subtitles, encoder, fill=False) -> dict:
    """``render``'s params from the CLI's own ``--subtitles``/``--encoder``/
    ``--fill-failed-with-motion`` flags: only what was given, so
    :func:`render_step.read_params` fills the rest with its own defaults."""
    params = {}
    if subtitles is not None:
        params[render_step.SUBTITLES_PARAM] = subtitles
    if encoder is not None:
        params[render_step.ENCODER_PARAM] = encoder
    if fill:
        params[render_step.FILL_PARAM] = True
    return params


def _phase4_params(args, step) -> dict:
    """*step*'s params from the CLI's own flags (``assets``, ``render``;
    ``metadata`` takes none): the modules' own closed lists reach argparse as
    ``choices=`` (DEC-009), so nothing here is re-typed. A flag sends only a
    change from the step's own default (``--no-animate``: ``animate``
    false; ``--fill-failed-with-motion``: the fill on)."""
    if step == "assets":
        params = {assets_step.ALIGN_PARAM: True} if args.align_words else {}
        if args.no_animate:
            params[assets_step.ANIMATE_PARAM] = False
        return params
    if step == "render":
        return _render_params(args.subtitles, args.encoder, args.fill_failed_with_motion)
    return {}


def _set_profile(stories, story, args) -> dict:
    """``--tier``/``--route`` (assets): the story's ``generation_profile``
    patched through ``workflow.patch_story`` -- the API's ``PATCH
    /stories/{id}`` -- and printed; the story as it stands after. Nothing
    is patched when neither is given."""
    partial = {name: value for name, value in (("tier", args.tier), ("route", args.route)) if value is not None}
    if partial:
        story = workflow.patch_story(stories, story["story_id"], {"generation_profile": partial}, now=_now())
        profile = story["generation_profile"]
        print(f"⚙️ Generation profile: tier {profile['tier']}, route {profile['route']}, consistency "
              f"{profile['consistency_mode']}, budget {profile['budget_profile']}")
    return story


def _print_assets_estimate(stories, story, args) -> None:
    """``--estimate`` (assets): ``workflow.assets_estimate`` -- the assets
    step's own estimate (``assets.asset_units``: images, voices and, at tier
    2 or 3, the clips) with the paid check's verdict -- as JSON. Keys and
    chains from the process environment (DEC-114); a local ComfyUI is not
    asked, nothing is called, no step runs."""
    ec = workflow.episode_context(stories, story, args.ep, step="assets")
    estimate = workflow.assets_estimate(ec, env=_settings_env(), align_words=args.align_words,
                                        animate=not args.no_animate)
    print(json.dumps(estimate, indent=2, ensure_ascii=False))


def _print_assets_summary(result) -> None:
    """After ``assets``: shots made/cached/locked, lines voiced (and, opted
    in, aligned), SFX/BGM -- ``assets.run``'s own return."""
    shots, lines = result["shots"], result["lines"]
    parts = [f"{shots['made']} image{'' if shots['made'] == 1 else 's'} made", f"{shots['cached']} cached"]
    if shots["locked"]:
        parts.append(f"{shots['locked']} locked")
    parts.append(f"{lines['measured']} line{'' if lines['measured'] == 1 else 's'} voiced")
    if result["aligned"]:
        parts.append(f"{len(result['aligned'])} aligned")
    if lines["unvoiced"]:
        parts.append(f"{len(lines['unvoiced'])} unvoiced")
    if result["sfx"]["missing"]:
        parts.append(f"{result['sfx']['missing']} SFX cue{'' if result['sfx']['missing'] == 1 else 's'} missing")
    if result["bgm"]:
        parts.append(f"BGM {result['bgm']}")
    state = "complete" if result["complete"] else "not complete"
    print(f"🖼 Episode {result['ep']}'s assets ({state}): {_and(parts)}.")


def _print_render_summary(result) -> None:
    """After ``render``: duration, size and fps, loudness, stages run versus
    cached, and any warning (a length or loudness outside the window is a
    warning, never a failure) -- ``render.run``'s own return."""
    loud, out = result["loudness"], result["output"]
    print(f"🎬 Episode {result['ep']} rendered: {result['duration_s']:.1f} s, {out['width']}x{out['height']} at "
          f"{out['fps']} fps, {loud['i']:.1f} LUFS (true peak {loud['tp']:.1f} dBTP); "
          f"{len(result['ran'])} stage{'' if len(result['ran']) == 1 else 's'} run, "
          f"{len(result['cached'])} from the cache, {result['seconds']:.1f} s total.")
    for warning in result["warnings"]:
        print(f"⚠️ {warning}")


def _print_metadata_summary(result) -> None:
    """After ``metadata``: the platforms written, which were asked versus
    kept, and whether the cover was made again -- ``metadata.run``'s own
    return."""
    platforms = [name for name in schemas.PLATFORMS if name in result["platforms"]]
    cover = " + cover" if result["cover"] else ""
    asked = f"{len(result['asked'])} asked" if result["asked"] else "every platform kept"
    print(f"🏷 Episode {result['ep']}'s metadata ({asked}{cover}): {_and(platforms)}.")


def _phase4_step(args, stories, story) -> int:
    """``assets``, ``render`` or ``metadata`` of episode ``args.ep`` (module
    docstring; DEC-114, the same rules ``workflow`` applies for the API): the
    episode's preconditions (``workflow.episode_context``), then -- for
    ``metadata``, the only one of the three the ``step`` command runs that
    calls the LLM chain (``render`` calls no API; ``assets`` meets the image
    and voice chains' own gates inside the step, stopping before its first
    call when a paid part is over a cap) -- the key gate, then the run
    through the worker's registry, a summary, and -- ``assets``, the one
    phase-4 step ``AUTO_APPROVABLE`` gains -- ``--auto-approve``
    (``workflow.approve_assets``, refused with its own reason for a stale or
    incomplete grid; never 'approve anyway')."""
    step, story_id, ep = args.step, story["story_id"], args.ep
    workflow.episode_context(stories, story, ep, step=step)
    if step == "assets":
        story = _set_profile(stories, story, args)
        if args.estimate:
            _print_assets_estimate(stories, story, args)
            return EXIT_OK
    if step == "metadata":
        refusal = _llm_refusal(args.allow_slow_chain)
        if refusal:
            _err(refusal)
            return EXIT_FAILED
    interrupted, result = _run_step(stories, story_id, step, _phase4_params(args, step), ep=ep)
    if interrupted:
        return interrupted
    {"assets": _print_assets_summary, "render": _print_render_summary,
     "metadata": _print_metadata_summary}[step](result)
    if args.auto_approve:  # only 'assets' reaches here: AUTO_APPROVABLE gates the rest out
        workflow.approve_assets(stories, story_id, ep, now=_now())
        print("✅ Assets approved.")
    print(_line(workflow.load(stories, story_id)))
    return EXIT_OK


# ------------------------------------------------------------------ phase 5

def _print_series_summary(step, result) -> None:
    """After ``memory``, ``feedback`` or ``propose-next``: what the step just
    wrote, from its own ``run()`` return -- the step's own log lines (printed
    as it ran, ``on_log``) already named the document to approve or, for
    ``propose-next``, that each item needs a decision."""
    if step == "memory":
        print(f"🧠 Episode {result['ep']}'s memory ({'written again' if result['replaced'] else 'written'}): "
              f"{len(result['hooks_opened'])} hook(s) opened, {len(result['hooks_closed'])} closed, "
              f"{result['relationships']} relationship(s).")
    elif step == "feedback":
        print(f"💬 Episode {result['ep']}'s feedback digested: {len(result['directions'])} direction(s).")
    else:
        print(f"💡 Episode {result['for_ep']}'s proposals ({'written again' if result['replaced'] else 'written'}): "
              f"{len(result['characters'])} character(s), {len(result['twists'])} twist(s).")


def _series_step(args, stories, story) -> int:
    """``memory``, ``feedback`` or ``propose-next`` of episode ``args.ep``
    (phase 5, step 13): exactly the rules ``workflow`` applies for the API
    (DEC-114): the episode's preconditions and the step's own
    (``workflow.series_context``, calling nothing -- ``memory`` an approved
    script, ``feedback`` a pasted item (paste it first with the ``feedback``
    command), ``propose-next`` an episode after this one and this one's own
    memory written, approved and fresh: the same gate a script or storyboard
    job meets, DEC-130 as amended by plan 11 stage 4), then the parameters
    (``workflow.series_request``: none of the three take any), the key gate
    (every one calls one free-chain LLM link), then the run through the
    worker's registry, a short summary, then ``--auto-approve``
    (``AUTO_APPROVABLE``, checked before this runs): ``memory``
    (``workflow.approve_memory``) and ``feedback`` with no direction chosen
    (``workflow.approve_feedback(..., direction=None)`` -- choosing one of
    F1's three digested directions needs the dashboard or the API, which show
    them first); ``propose-next`` never reaches here with ``--auto-approve``
    (each item needs a human decision, from the dashboard or the API)."""
    step, story_id, ep = args.step, story["story_id"], args.ep
    workflow.series_context(stories, story, ep, step=step)
    params = workflow.series_request(step, {})

    refusal = _llm_refusal(args.allow_slow_chain)
    if refusal:
        _err(refusal)
        return EXIT_FAILED
    interrupted, result = _run_step(stories, story_id, step, params, ep=ep)
    if interrupted:
        return interrupted

    _print_series_summary(step, result)

    if args.auto_approve:
        if step == "memory":
            workflow.approve_memory(stories, story_id, ep, now=_now())
            print(f"✅ Episode {ep}'s memory approved.")
        else:  # feedback: AUTO_APPROVABLE excludes propose-next
            workflow.approve_feedback(stories, story_id, ep, direction=None, now=_now())
            print(f"✅ Episode {ep}'s feedback approved (no direction chosen).")
    print(_line(workflow.load(stories, story_id)))
    return EXIT_OK


# ------------------------------------------------------------------ re-edit

def _print_rerender_summary(result) -> None:
    """After ``rerender``: what changed since the last good render, first
    (the same sentence the feed already printed while it ran), then the
    render's own numbers -- ``rerender.run``'s own return, ``render.
    summary_of`` with ``reuse``."""
    reuse, loud, out = result["reuse"], result["loudness"], result["output"]
    reused = f" · {len(reuse['shots_reused'])} reused" if reuse else ""
    print(f"🎬 Episode {result['ep']} re-rendered: {reuse['summary'] if reuse else 'every shot made again'}"
          f"{reused}, {result['duration_s']:.1f} s, {out['width']}x{out['height']} at {out['fps']} fps, "
          f"{loud['i']:.1f} LUFS (true peak {loud['tp']:.1f} dBTP); {result['seconds']:.1f} s total.")
    for warning in result["warnings"]:
        print(f"⚠️ {warning}")


def _print_reedit_dry_run(ep, changes) -> None:
    """``--dry-run``: what a re-render of episode *ep* would make again right
    now (``workflow.reedit_changes``'s own shape -- stage 8's predicate,
    RC-M8: the same one the run then uses, and ``GET /estimate/rerender``'s),
    without rendering anything: the count, then one line per shot to be made
    again and why."""
    total, rebuild = changes["shots_total"], changes["rebuild"]
    print(f"🔎 Episode {ep}: {changes['summary']} ({len(changes['reuse'])} reused).")
    for shot_id in rebuild:
        print(f"   {shot_id}: {changes['reasons'][shot_id]}")


def _reedit_step(args, stories, story) -> int:
    """``rerender`` of episode ``args.ep`` (phase 5, plan 11 stage 9): the
    episode's preconditions (``workflow.episode_context``, as every episode
    step), then, before anything runs or is estimated, a finished render to
    re-render and every render precondition (``workflow.
    require_reedit_inputs``: ``rerender.require_finished_render`` then
    ``render.require_renderable``, the same two refusals the route meets
    before a job exists). ``--dry-run`` prints the selection alone
    (``workflow.reedit_changes``) and renders nothing; otherwise the run
    goes through the worker's registry, its own feed lines print as it runs,
    and a short summary follows. Takes no other parameter; never
    ``--auto-approve`` (``AUTO_APPROVABLE`` excludes it: it ends completed,
    nothing to approve)."""
    step, story_id, ep = args.step, story["story_id"], args.ep
    ec = workflow.episode_context(stories, story, ep, step=step)
    workflow.require_reedit_inputs(ec)
    if args.dry_run:
        _print_reedit_dry_run(ep, workflow.reedit_changes(ec))
        return EXIT_OK
    interrupted, result = _run_step(stories, story_id, step, {}, ep=ep)
    if interrupted:
        return interrupted
    _print_rerender_summary(result)
    print(_line(workflow.load(stories, story_id)))
    return EXIT_OK


def _read_text_file(path) -> str:
    """A file's contents exactly as they are (UTF-8, nothing trimmed): the
    ``feedback`` command's ``--text-file``/``--stats-file``. ``OSError`` for
    a path that cannot be read (the caller answers it)."""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _cmd_feedback(args, stories) -> int:
    """``feedback``: paste episode ``--ep``'s audience comments (and,
    optionally, its stats) from a file, then run the ``feedback`` step (F1)
    on them -- module docstring; exactly what ``POST .../episodes/{ep}/
    feedback`` does, in one process. The episode's own bounds are checked
    first (``workflow.episode_bounds``), before either file is read; the
    paste meets ``workflow.store_feedback``'s own cap (6,000 characters each,
    refused whole, never trimmed, naming the size) and replaces any earlier
    paste of this episode; the run meets the key gate as any other LLM step.
    A file that cannot be read is ``EXIT_FAILED``, naming it, before anything
    is pasted. ``--auto-approve`` approves the digest with no direction
    chosen (choosing one needs the dashboard or the API)."""
    story = workflow.load(stories, args.story_id)
    story_id = story["story_id"]
    ep = workflow.episode_bounds(stories, story, args.ep)

    try:
        text = _read_text_file(args.text_file)
    except OSError as exc:
        _err(f"Cannot read {args.text_file}: {exc.strerror or exc}.")
        return EXIT_FAILED
    stats = None
    if args.stats_file is not None:
        try:
            stats = _read_text_file(args.stats_file)
        except OSError as exc:
            _err(f"Cannot read {args.stats_file}: {exc.strerror or exc}.")
            return EXIT_FAILED

    workflow.store_feedback(stories, story_id, ep, text, stats, now=_now())

    refusal = _llm_refusal(False)  # no --allow-slow-chain here, like render/fast-track; ALLOW_SLOW_CHAIN=1 still works
    if refusal:
        _err(refusal)
        return EXIT_FAILED
    interrupted, result = _run_step(stories, story_id, "feedback", {}, ep=ep)
    if interrupted:
        return interrupted
    _print_series_summary("feedback", result)

    if args.auto_approve:
        workflow.approve_feedback(stories, story_id, ep, direction=None, now=_now())
        print(f"✅ Episode {ep}'s feedback approved (no direction chosen).")
    print(_line(workflow.load(stories, story_id)))
    return EXIT_OK


def _run_render_step(stories, story, ep, *, subtitles, encoder, fill=False) -> int:
    """``render`` of episode *ep*, shared by ``step ID render --ep N`` and
    the ``render`` command (its alias)."""
    story_id = story["story_id"]
    workflow.episode_context(stories, story, ep, step="render")
    interrupted, result = _run_step(stories, story_id, "render", _render_params(subtitles, encoder, fill), ep=ep)
    if interrupted:
        return interrupted
    _print_render_summary(result)
    print(_line(workflow.load(stories, story_id)))
    return EXIT_OK


def _cmd_render(args, stories) -> int:
    story = workflow.load(stories, args.story_id)
    return _run_render_step(stories, story, args.ep, subtitles=args.subtitles, encoder=args.encoder,
                            fill=args.fill_failed_with_motion)


def _print_fast_track_summary(result) -> None:
    """After the fast track: each sub-step kept as it was or run, in order
    (``fast_track.run``'s own return: ``steps[name]`` is minimal when a
    sub-step was kept, its own summary otherwise), then the total wall
    time and what was auto-approved."""
    notes = []
    for name in fast_track_step.SUB_STEPS:
        info = result["steps"].get(name) or {}
        label = fast_track_step.LABELS[name]
        if info.get("kept"):
            notes.append(f"{label} kept")
        elif name == "storyboard":
            notes.append(f"{label} {info.get('shots', '?')} shots ({info.get('mode', '')})")
        elif name == "paid_check":
            notes.append(f"{label} {info.get('verdict', '')}")
        elif name == "assets":
            shots = info.get("shots") or {}
            notes.append(f"{label} {shots.get('made', 0)} made/{shots.get('cached', 0)} cached")
        elif name == "render":
            duration = info.get("duration_s")
            notes.append(f"{label} {duration:.1f} s" if duration is not None else label)
        elif name == "metadata":
            notes.append(f"{label} {len(info.get('platforms') or {})} platforms")
        else:
            notes.append(f"{label} written")
    approved = f"; auto-approved {_and(result['auto_approved'])}" if result["auto_approved"] else ""
    keyframes = result.get("keyframes") or {}
    if keyframes.get("auto_approved") and keyframes.get("flagged"):
        # Stage C: the one click went over these -- the review screen shows each.
        approved += f" (keyframes anyway: {_and(keyframes['flagged'])} still flagged, review them)"
    print(f"⏩ Fast track of episode {result['ep']} done in {result['seconds'] / 60:.1f} min: "
          + ", ".join(notes) + approved + ".")


def _cmd_fast_track(args, stories) -> int:
    """``fast-track``: one episode, script through metadata, in one job
    (module docstring; DEC-162). Meets the LLM key gate as the API does for
    every fast-track job (it always writes or checks the script, and may
    reach T1 and M1 too); there is no ``--allow-slow-chain`` on this command
    (unlike ``step``'s LLM steps), but ``ALLOW_SLOW_CHAIN=1`` still applies."""
    story = workflow.load(stories, args.story_id)
    story_id, ep = story["story_id"], args.ep
    workflow.episode_context(stories, story, ep, step="fast-track")
    refusal = _llm_refusal(False)
    if refusal:
        _err(refusal)
        return EXIT_FAILED
    params = {fast_track_step.STORYBOARD_PARAM: args.storyboard} if args.storyboard is not None else {}
    if args.stop_at_keyframes:
        params[fast_track_step.STOP_PARAM] = True
    if args.stop_on_script_issues:
        params[fast_track_step.SCRIPT_STOP_PARAM] = True
    interrupted, result = _run_step(stories, story_id, "fast-track", params, ep=ep)
    if interrupted:
        return interrupted
    _print_fast_track_summary(result)
    print(_line(workflow.load(stories, story_id)))
    return EXIT_OK


def _cmd_approve(args, stories) -> int:
    """``approve``: a v2 episode's keyframes (``keyframes:N``,
    ``workflow.approve_keyframes``; module docstring). Any other document
    is a usage error naming ``--auto-approve``."""
    word, sep, ep = args.doc.partition(":")
    if word != workflow.KEYFRAMES_APPROVAL or not sep or not ep.isdigit():
        return _usage_error("approve", f"{args.doc!r} is not keyframes:N -- the one document approved by this "
                                       "command; approve the others with 'step STORY_ID <step> --auto-approve'")
    story = workflow.load(stories, args.story_id)
    story_id = story["story_id"]
    doc = workflow.approve_keyframes(stories, story_id, int(ep), approve_anyway=args.anyway, now=_now())
    approved = doc["keyframes_approved"]
    print(f"✅ Episode {int(ep)}'s keyframes approved{' anyway' if approved['anyway'] else ''} "
          f"(fingerprint {approved['fingerprint'][:12]}): its clips can be made (the assets step).")
    print(_line(workflow.load(stories, story_id)))
    return EXIT_OK


def _cmd_prompt_limits(args, stories) -> int:
    """Every link's prompt limit and its source (``prompt_limits.listing``),
    with the live values fal's schemas published, read from the file the
    dashboard's video key check writes (by path: the CLI never imports web)."""
    from clipping.providers import prompt_limits

    path = prompt_limits.live_path()
    live = prompt_limits.read_live(path)
    print("Prompt size limits per generation link: a prompt over its link's limit is refused before it is "
          "sent, and the chain moves on.")
    print(f"Live values ({len(live)} read) are kept in {path}; Settings > Video > 'Ask the providers (free)' "
          "reads fal's schemas.")
    for line in prompt_limits.listing(live=live):
        print(f"  {line}")
    return EXIT_OK


def _tail_row(row) -> str:
    """One line of ``voice-tails``: the line, its voice and file, and what
    the tail guard did or would do to it."""
    head = f"{row['line_id']} {row['speaker']}  {row['voice'] or '-'}"
    if row["file"] is None:
        return f"{head}  no kept audio (not voiced yet, or its text changed since)"
    seconds = f"{row['seconds']:.2f} s" if isinstance(row["seconds"], (int, float)) else "? s"
    head += f"  {row['file']} {seconds}"
    if row["provider"] != "gemini":
        return f"{head}  not checked: only Gemini adds static (the mix fades every line's edges)"
    plan = row["analysis"]
    if plan is None:
        return f"{head}  not a 16-bit mono WAV: left as it is"
    now = plan["report"]
    if now["reason"] == "suspect":
        would = f"left whole: its end looks odd (the rules would cut {plan['would_cut_s']:.2f} s, more than " \
                f"{tts_tail.MAX_CUT_S:g} s or half the line)"
    elif now["trimmed_s"] > 0:
        would = f"would cut {now['trimmed_s']:.2f} s ({now['reason']}), {now['kept_s']:.2f} s kept"
    else:
        would = None
    guard = row["guard"]
    if row["due"] or guard is None:
        return f"{head}  not cleaned yet: {would or 'nothing to cut'}"
    done = (f"{guard['trimmed_s']:.2f} s cut ({guard['reason']})" if guard.get("trimmed_s") else
            f"nothing cut ({guard.get('reason')})")
    return f"{head}  cleaned (v{guard.get('version')}): {done}; now: {would or 'nothing more to cut'}"


def _cmd_voice_tails(args, stories) -> int:
    """``voice-tails``: what the Gemini tail guard would cut, or cut, from
    each line of episode ``--ep`` (module docstring) -- read only."""
    story = workflow.load(stories, args.story_id)
    story_id = story["story_id"]
    ep = workflow.episode_bounds(stories, story, args.ep)
    try:
        script = stories.read_episode_doc(story_id, ep, episode_common.SCRIPT_DOC)
    except (KeyError, schemas.SchemaError) as exc:
        _err(f"Episode {ep}'s script cannot be read ({exc}).")
        return EXIT_FAILED
    if script is None:
        _err(f"Episode {ep} has no script yet: nothing is voiced.")
        return EXIT_FAILED
    rows = voice_lines.tail_report(stories, story_id, ep, script)
    print(f"Episode {ep}'s line endings (Gemini tail guard v{tts_tail.TAIL_GUARD_VERSION}):")
    for row in rows:
        print(_tail_row(row))
    gemini = [row for row in rows if row["provider"] == "gemini" and row["file"] is not None]
    cleaned = [row for row in gemini if not row["due"] and row["guard"]]
    todo = [row for row in gemini if row["due"]]
    to_cut = sum(row["analysis"]["report"]["trimmed_s"] for row in todo if row["analysis"])
    summary = f"{len(gemini)} Gemini line{'s' if len(gemini) != 1 else ''}: {len(cleaned)} cleaned, {len(todo)} to clean"
    if todo:
        summary += (f" ({to_cut:.2f} s of static to cut) -- the next assets run cleans "
                    f"{'them' if len(todo) != 1 else 'it'}, for free, speaking nothing again")
    print(summary + ".")
    return EXIT_OK


_COMMANDS = {"new": _cmd_new, "step": _cmd_step, "render": _cmd_render, "fast-track": _cmd_fast_track,
             "feedback": _cmd_feedback, "approve": _cmd_approve, "voice-tails": _cmd_voice_tails,
             "list": _cmd_list, "prompt-limits": _cmd_prompt_limits}


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

    if getattr(args, "settings", False):
        _load_settings()
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
