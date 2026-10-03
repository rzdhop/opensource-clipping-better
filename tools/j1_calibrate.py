#!/usr/bin/env python3
"""Ask the first-watch judge (J1) about a written episode several times and
print what it finds each time: the verdict, the blocking and the minor issues.

J1 version 2 (DEC-248) gives each issue a severity and passes when no issue
is blocking; version 1 failed on any issue and, asked for "at most 6", found
6 every time, so the fast track always stopped at the script. This tool
measures, on a real episode, how often version 2 passes and what it still
calls blocking -- and how stable that is from one call to the next -- with
the same call the script step makes (``steps/judge.check_first_watch``, the
story's writing chain, the same keys and gates).

    python tools/j1_calibrate.py --story 782ee78899b0 --ep 1 --runs 5
    python tools/j1_calibrate.py --story 782ee78899b0 --ep 1 --runs 5 --settings
    docker exec rzc-backend python tools/j1_calibrate.py --story 782ee78899b0 --settings

``--settings`` reads the dashboard's stored Settings (keys, chains,
``allow_paid``) over the environment, as the CLI's ``--settings`` does;
without it, the environment alone. No key is printed.

Read-only: each call judges a copy of the script, and nothing is written to
the story -- except what a paid link costs, booked in the story's ledger as
any paid call is (only with ``allow_paid`` on; the free links cost nothing).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import argparse
import collections
import copy
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipping.aistory import steps  # noqa: E402
from clipping.aistory import store as story_store  # noqa: E402
from clipping.aistory.steps import entities, episode_common, judge  # noqa: E402
from clipping.aistory.steps import script as script_step  # noqa: E402
from clipping.cancel import CancelToken  # noqa: E402


def _issue_line(issue) -> str:
    severity = issue.get("severity", "blocking (version 1)")
    return f"    - [{severity}] {issue['scene_id'] or 'the episode'} ({issue['kind']}): {issue['fix']}"


def _print_report(title, report) -> None:
    blocking, minor = judge.blocking_issues(report), judge.minor_issues(report)
    print(f"{title}: {'PASSED' if report['passed'] else 'FAILED'} -- {len(blocking)} blocking, {len(minor)} minor "
          f"(J1 version {report.get('version', 1)})")
    print(f"    who wants what: {report['who_wants_what']}")
    print(f"    what happens:   {report['what_happens']}")
    print(f"    why it matters: {report['why_it_matters']}")
    for issue in blocking + minor:
        print(_issue_line(issue))


def main(argv=None, *, runner=None) -> int:
    """The command; *runner* stands in for the LLM chain (a test's)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--story", required=True, help="the story id (outputs/stories/<id>/)")
    parser.add_argument("--ep", type=int, default=1, help="the episode (default 1)")
    parser.add_argument("--runs", type=int, default=5, help="how many J1 calls (default 5)")
    parser.add_argument("--outputs-dir", default=None, help="the outputs directory (default: the app's)")
    parser.add_argument("--settings", action="store_true",
                        help="read the dashboard's stored Settings over the environment")
    parser.add_argument("--verbose", action="store_true", help="print the call's own log lines")
    args = parser.parse_args(argv)

    settings_env = {}
    if args.settings:
        from clipping.aistory.cli import settings_file, stored_settings

        settings_env = stored_settings()
        print(f"Using the stored Settings: {len(settings_env)} values from {settings_file()} (no value printed).")

    stories = story_store.StoryStore(args.outputs_dir or story_store.default_outputs_dir())
    ctx = steps.StepContext(job_id="j1-calibrate", story_id=args.story, step="script", ep=args.ep, params={},
                            cancel=CancelToken(), settings_env=settings_env, outputs_dir=stories.outputs_dir,
                            on_log=print if args.verbose else (lambda _line: None))
    ec = episode_common.load_episode_context(ctx)
    script = episode_common.read_episode(ec, episode_common.SCRIPT_DOC)
    if script is None or not script_step.is_complete(script, ec.ep):
        print(f"Episode {ec.ep}'s script is not complete: write it first (the script step).")
        return 1

    stored = script.get(judge.FIRST_WATCH)
    if stored is not None:
        _print_report("Stored report", stored)
        print(f"    repair passes recorded: {len(script.get(judge.REPAIRS) or [])}")
    print(f"\nAsking J1 version 2 {args.runs} time{'s' if args.runs != 1 else ''} about episode {ec.ep} "
          f"({len(script['scenes'])} scenes, {judge.spoken_words(script)} spoken words)...\n")

    tools = entities.Tools(runner=runner, time_fn=time.monotonic)
    passed, kinds, failures = 0, collections.Counter(), 0
    blocking_counts, minor_counts = [], []
    for run in range(1, args.runs + 1):
        trial = copy.deepcopy(script)
        started = time.monotonic()
        try:
            report = judge.check_first_watch(ctx, ec, trial, tools=tools,
                                             pack=script_step._pack(ec, ctx, set()))
        except steps.StepFailed as exc:
            failures += 1
            print(f"Run {run}: the call failed -- {exc.reason}")
            continue
        _print_report(f"Run {run} ({time.monotonic() - started:.0f} s)", report)
        passed += report["passed"]
        blocking_counts.append(len(judge.blocking_issues(report)))
        minor_counts.append(len(judge.minor_issues(report)))
        kinds.update(f"{issue['kind']} ({issue['severity']})" for issue in report["issues"])

    answered = len(blocking_counts)
    print("\nSummary")
    print(f"    answered: {answered} of {args.runs} (failed calls: {failures})")
    if answered:
        print(f"    passed: {passed} of {answered}")
        print(f"    blocking issues a run: {sum(blocking_counts) / answered:.1f} (min {min(blocking_counts)}, max "
              f"{max(blocking_counts)})")
        print(f"    minor issues a run:    {sum(minor_counts) / answered:.1f}")
        for kind, count in kinds.most_common():
            print(f"    {kind}: {count}")
    return 0 if answered else 1


if __name__ == "__main__":
    sys.exit(main())
