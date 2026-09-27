"""The Live activity clocks stop where a finished job stopped.

A completed clip job read "33h 56m on this step · 34h 50m total" a day and a
half after it finished: ``LiveActivity`` measured both clocks to the reader's
``Date.now()`` on every render, whatever the job's status. The one-second
ticker did stop, so the figure only moved on a reload -- by a day.

The clock math is ``jobClocks`` in ``web/dashboard/src/time.js``. These tests
run it with the system Node instead of pinning its text: the dashboard has no
JS test runner and adding one would be a new dependency. CI installs pytest
and nothing else (DEC-012), so the Node tests skip where ``node`` or the
dashboard's ``node_modules`` (time.js imports React) is missing; the text
guard at the end runs everywhere.
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
from datetime import datetime

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "web" / "dashboard"
TIME_JS = DASHBOARD_ROOT / "src" / "time.js"
ACTIVITY_FEED = DASHBOARD_ROOT / "src" / "components" / "ActivityFeed.jsx"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(
    NODE is None or not (DASHBOARD_ROOT / "node_modules" / "react").is_dir(),
    reason="needs node and web/dashboard/node_modules (time.js imports React)",
)

# Reads a JSON list of cases on stdin, answers a JSON list of {inStep, inJob}.
_RUNNER = """
import { jobClocks } from %s;
let input = '';
for await (const chunk of process.stdin) input += chunk;
const answers = JSON.parse(input).map(
  c => jobClocks(c.job, c.events, c.running, c.now));
process.stdout.write(JSON.stringify(answers));
"""


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def _clocks(*cases: dict) -> list[dict]:
    """``jobClocks`` for each case, in one Node process.

    East of UTC on purpose: a zoneless timestamp read as local time would then
    land hours away from the UTC it means, instead of on it by coincidence.
    """
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", _RUNNER % json.dumps(TIME_JS.as_uri())],
        input=json.dumps(list(cases)),
        env={**os.environ, "TZ": "Asia/Jakarta"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _case(job: dict, events: list, running: bool, now: str) -> dict:
    return {"job": job, "events": events, "running": running, "now": _ms(now)}


# The job of the report, reduced to the fields the clocks read: created at
# 08:00, done at 08:54:10 (set_clips and the "Done!" progress line land in the
# same instant), the "done" step started then too.
COMPLETED_CLIP = {
    "status": "completed",
    "created_at": "2026-09-26T08:00:00+00:00",
    "updated_at": "2026-09-26T08:54:10.120000+00:00",
    "progress": {"step": "done", "step_started_at": "2026-09-26T08:54:10.118000+00:00"},
}
COMPLETED_CLIP_EVENTS = [
    {"seq": 411, "ts": "2026-09-26T08:54:09.900000+00:00", "message": "Rendered clip 7 of 7"},
    {"seq": 412, "ts": "2026-09-26T08:54:10.119000+00:00",
     "message": "Done! 7 clips rendered successfully."},
]


@needs_node
def test_a_completed_job_reads_the_same_total_a_day_later():
    soon, day_later = _clocks(
        _case(COMPLETED_CLIP, COMPLETED_CLIP_EVENTS, False, "2026-09-26T09:00:00Z"),
        _case(COMPLETED_CLIP, COMPLETED_CLIP_EVENTS, False, "2026-09-27T18:50:00Z"),
    )
    assert soon == day_later == {"inStep": None, "inJob": "54m 10s"}


@needs_node
def test_a_finished_job_has_no_step_clock():
    # "done", "error" and "awaiting approval" are where a job ends, not steps
    # it spends time on.
    failed = dict(COMPLETED_CLIP, status="failed",
                  progress={"step": "error", "step_started_at": "2026-09-26T08:54:10+00:00"})
    awaiting = dict(COMPLETED_CLIP, status="awaiting_approval")
    for answer in _clocks(
        _case(failed, COMPLETED_CLIP_EVENTS, False, "2026-09-27T18:50:00Z"),
        _case(awaiting, COMPLETED_CLIP_EVENTS, False, "2026-09-27T18:50:00Z"),
    ):
        assert answer["inStep"] is None
        assert answer["inJob"] == "54m 10s"


@needs_node
def test_an_approved_story_step_stops_at_its_last_line_not_at_the_approval():
    # Approving moves updated_at to the click, hours after the step finished;
    # its last feed line is the worker's "awaiting your approval".
    approved = {
        "status": "completed",
        "created_at": "2026-09-27T10:00:00+00:00",
        "updated_at": "2026-09-27T13:12:00+00:00",
        "approved_at": "2026-09-27T13:12:00+00:00",
        "progress": None,
    }
    events = [
        {"seq": 1, "ts": "2026-09-27T10:00:01+00:00", "message": "Story step 'bible' started."},
        {"seq": 9, "ts": "2026-09-27T10:00:20+00:00",
         "message": "Story step 'bible' is ready: awaiting your approval."},
    ]
    [answer] = _clocks(_case(approved, events, False, "2026-09-27T20:00:00Z"))
    assert answer == {"inStep": None, "inJob": "20s"}


@needs_node
def test_a_cancelled_job_stops_at_the_cancel_not_at_its_last_words():
    # The worker keeps printing until its next checkpoint; the job stopped
    # when the user asked.
    cancelled = {
        "status": "cancelled",
        "created_at": "2026-09-27T10:00:00+00:00",
        "updated_at": "2026-09-27T10:05:00+00:00",
        "progress": {"step": "rendering", "step_started_at": "2026-09-27T10:03:00+00:00"},
    }
    events = [
        {"seq": 50, "ts": "2026-09-27T10:05:00+00:00", "message": "Cancel requested."},
        {"seq": 51, "ts": "2026-09-27T10:05:03+00:00",
         "message": "Cancelled. The job stopped at its next checkpoint."},
    ]
    [answer] = _clocks(_case(cancelled, events, False, "2026-09-28T10:00:00Z"))
    assert answer == {"inStep": None, "inJob": "5m 00s"}


@needs_node
def test_a_finished_job_without_a_feed_stops_at_updated_at():
    [answer] = _clocks(_case(COMPLETED_CLIP, [], False, "2026-09-27T18:50:00Z"))
    assert answer == {"inStep": None, "inJob": "54m 10s"}


@needs_node
def test_a_zoneless_feed_timestamp_is_read_as_utc():
    # JobProgressEvent-era lines serialize without a zone (see parseTime); read
    # as local time they would move the end by the reader's UTC offset.
    events = [{"seq": 412, "ts": "2026-09-26T08:54:10", "message": "Done!"}]
    job = dict(COMPLETED_CLIP, updated_at="2026-09-26T09:30:00+00:00")
    [answer] = _clocks(_case(job, events, False, "2026-09-27T18:50:00Z"))
    assert answer == {"inStep": None, "inJob": "54m 10s"}


@needs_node
def test_a_finished_job_with_no_known_end_shows_no_total():
    job = {"status": "completed", "created_at": "2026-09-26T08:00:00+00:00", "progress": None}
    [answer] = _clocks(_case(job, [], False, "2026-09-27T18:50:00Z"))
    assert answer == {"inStep": None, "inJob": None}


@needs_node
def test_a_running_job_keeps_both_clocks_ticking():
    running = {
        "status": "rendering",
        "created_at": "2026-09-27T10:00:00+00:00",
        "updated_at": "2026-09-27T10:10:00+00:00",
        "progress": {"step": "rendering", "step_started_at": "2026-09-27T10:10:00+00:00"},
    }
    events = [{"seq": 3, "ts": "2026-09-27T10:10:00+00:00", "message": "Rendering clip 1 of 7"}]
    first, later = _clocks(
        _case(running, events, True, "2026-09-27T10:12:12Z"),
        _case(running, events, True, "2026-09-27T10:12:13Z"),
    )
    assert first == {"inStep": "2m 12s", "inJob": "12m 12s"}
    assert later == {"inStep": "2m 13s", "inJob": "12m 13s"}


def test_live_activity_takes_its_clocks_from_job_clocks():
    src = ACTIVITY_FEED.read_text(encoding="utf-8")
    assert re.search(r"import \{[^}]*\bjobClocks\b[^}]*\} from '\.\./time'", src), (
        "ActivityFeed.jsx does not import jobClocks from ../time"
    )
    live = src[src.index("export function LiveActivity("):]
    assert re.search(r"jobClocks\(\s*job\s*,\s*events\s*,\s*running\b", live), (
        "LiveActivity does not compute its clocks with jobClocks(job, events, running)"
    )
    # The reader-clock arithmetic the bug came from.
    assert "created.getTime()" not in live
    assert "stepStarted.getTime()" not in live
