"""Dashboard shared pieces: the activity feed, the job statuses and api.js.

Guards the contract of ``components/ActivityFeed.jsx`` (the activity feed
JobDetail renders), ``Dashboard.jsx``'s status labels, and the request
helpers of ``api.js``. The dashboard is Clips only: no story page, no
story-step job, no story function in ``api.js``.

Pure text/AST checks, no npm: a JS test runner would be a new dependency and
would not run in CI, which installs pytest and nothing else (DEC-012) --
the same reasoning and the same pattern as
``tests/test_dashboard_payload_contract.py`` and ``tests/test_auth_token.py``.
"""

import ast
import pathlib
import re

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
DASHBOARD_SRC = PROJECT_ROOT / "web" / "dashboard" / "src"
ACTIVITY_FEED = DASHBOARD_SRC / "components" / "ActivityFeed.jsx"
JOB_DETAIL = DASHBOARD_SRC / "pages" / "JobDetail.jsx"
DASHBOARD = DASHBOARD_SRC / "pages" / "Dashboard.jsx"
API_JS = DASHBOARD_SRC / "api.js"
MODELS = PROJECT_ROOT / "web" / "api" / "models.py"

# The statuses only an AI Story step job had. The dashboard no longer labels,
# filters or waits on them.
STORY_STATUSES = {"running", "awaiting_approval", "awaiting_uploads"}

# Functions of the removed AI Story, budget and generation screens: none may
# come back into api.js.
REMOVED_FUNCTIONS = [
    "fetchStories", "createStory", "fetchStory", "patchStory", "deleteStory",
    "runStoryStep", "approveStoryDoc", "fetchEpisode", "generateClips",
    "fetchBudgetToday", "allowTodayExtra", "clearTodayExtra", "fetchHardware",
    "testGenerationChain", "checkVideoKeys", "checkAnthropicKey", "downloadApiFile",
]


def _job_status_values() -> set[str]:
    """``JobStatus``'s string values, parsed without importing pydantic."""
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "JobStatus":
            values = set()
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                    values.add(stmt.value.value)
            return values
    raise AssertionError("JobStatus not found in web/api/models.py")


def _status_labels() -> set[str]:
    src = DASHBOARD.read_text(encoding="utf-8")
    match = re.search(r"const STATUS_LABELS = \{(.*?)\n\}", src, re.DOTALL)
    assert match, "STATUS_LABELS not found in Dashboard.jsx"
    return set(re.findall(r"^\s*([a-z_]+):", match.group(1), re.MULTILINE))


# --------------------------------------------------------------- non-vacuity

def test_the_readers_see_something():
    """A broken regex would make every assertion below pass for free."""
    assert len(_job_status_values() - STORY_STATUSES) >= 8
    assert len(_status_labels()) >= 8


# --------------------------------------------------------- components/ActivityFeed.jsx

def test_activity_feed_exports_the_three_names():
    src = ACTIVITY_FEED.read_text(encoding="utf-8")
    assert re.search(r"export function ActivityConsole\(", src)
    assert re.search(r"export function LiveActivity\(", src)
    assert re.search(r"export function mergeEvents\(", src)


def test_terminal_is_the_three_ends_of_a_clip_job():
    src = ACTIVITY_FEED.read_text(encoding="utf-8")
    match = re.search(r"export const TERMINAL = \[(.*?)\]", src)
    assert match, "TERMINAL not exported from ActivityFeed.jsx"
    values = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert values == {"completed", "failed", "cancelled"}


def test_job_detail_imports_the_shared_pieces():
    src = JOB_DETAIL.read_text(encoding="utf-8")
    match = re.search(r"import \{([^}]*)\} from '\.\./components/ActivityFeed'", src)
    assert match, "JobDetail.jsx does not import from ../components/ActivityFeed"
    imported = match.group(1)
    for name in ("ActivityConsole", "LiveActivity", "mergeEvents"):
        assert name in imported, f"JobDetail does not import {name}"
    # And it must not define TERMINAL itself -- one definition, shared.
    assert not re.search(r"^const TERMINAL = \[", src, re.MULTILINE)


def test_no_second_copy_of_activity_console_under_src():
    """Only ActivityFeed.jsx may define it; JobDetail used to hold its own,
    and a second copy would drift from the shared one silently."""
    hits = sorted(
        path.resolve() for path in DASHBOARD_SRC.rglob("*.jsx")
        if re.search(r"function ActivityConsole\(", path.read_text(encoding="utf-8"))
    )
    assert hits == [ACTIVITY_FEED.resolve()]


# ------------------------------------------------------------- pages/Dashboard.jsx

def test_status_labels_covers_every_clip_job_status():
    missing = (_job_status_values() - STORY_STATUSES) - _status_labels()
    assert not missing, f"STATUS_LABELS is missing: {sorted(missing)}"


def test_no_story_status_or_story_step_job_is_left_in_the_dashboard():
    labels = _status_labels()
    assert not labels & STORY_STATUSES, sorted(labels & STORY_STATUSES)
    for path in (DASHBOARD, JOB_DETAIL, ACTIVITY_FEED):
        src = path.read_text(encoding="utf-8")
        assert "story_step" not in src, path.name
        assert "awaiting_" not in src, path.name
        assert "/story" not in src, path.name


# -------------------------------------------------------------------- api.js

def test_no_removed_function_is_left_in_api_js():
    src = API_JS.read_text(encoding="utf-8")
    for name in REMOVED_FUNCTIONS:
        assert not re.search(rf"\bfunction {name}\(", src), name
    for route in ("'/stories", "`/stories", "/budget", "/hardware", "test-generation-chain",
                  "check-video-keys", "check-anthropic-key"):
        assert route not in src, route


def test_existing_string_detail_callers_are_unchanged():
    """cancelJob/deleteJob throw new Error(<string>) with the API's own detail."""
    src = API_JS.read_text(encoding="utf-8")
    assert "throw new Error(await detailOf(res, 'Failed to cancel the job'))" in src
    assert "throw new Error(await detailOf(res, 'Failed to delete job'))" in src
