"""The job page can cancel and delete a job, and asks first.

JobDetail.jsx imported deleteJob and never called it, and there was no cancel
at all: a job could be stopped or removed only with curl. The page now shows
Cancel while a job is running and Delete once it has finished, each behind a
confirmation, since one stops paid work and the other removes files for good.

The repository has no frontend test runner, so -- like
test_dashboard_payload_contract.py -- this reads the source as text. Stdlib
only; it runs in the pytest-only CI job.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
API_JS = (ROOT / "web" / "dashboard" / "src" / "api.js").read_text(encoding="utf-8")
PAGE = (ROOT / "web" / "dashboard" / "src" / "pages" / "JobDetail.jsx").read_text(encoding="utf-8")
ROUTES = (ROOT / "web" / "api" / "routes" / "jobs.py").read_text(encoding="utf-8")


def test_the_client_can_cancel_a_job():
    body = API_JS[API_JS.index("export async function cancelJob"):]
    body = body[:body.index("\n}\n")]
    assert "`/jobs/${jobId}/cancel`" in body
    assert "method: 'POST'" in body


def test_the_route_the_client_calls_exists():
    assert '@router.post("/{job_id}/cancel"' in ROUTES


def test_a_refusal_reaches_the_user():
    """409 (already finished), 429 and the like carry a `detail`; both calls
    surface it instead of a generic failure."""
    for name in ("cancelJob", "deleteJob"):
        body = API_JS[API_JS.index(f"export async function {name}"):]
        body = body[:body.index("\n}\n")]
        assert "detail" in body or "detailOf(" in body, name


def _handler_calling(call):
    """The source of the arrow function that calls *call*."""
    at = PAGE.index(call)
    start = PAGE.rindex("=> {", 0, at)
    return PAGE[start:at]


def test_both_actions_are_wired_and_confirmed():
    for call in ("cancelJob(jobId)", "deleteJob(jobId)"):
        assert call in PAGE, f"{call} is never made"
        assert "window.confirm(" in _handler_calling(call), f"{call} does not ask first"
    assert re.search(r"onClick=\{onCancel\}", PAGE)
    assert re.search(r"onClick=\{onDelete\}", PAGE)


def test_a_deleted_job_leaves_its_page():
    # The job list lives at /clips since the two-mode shell (DEC-094).
    assert "navigate('/clips')" in _handler_calling("deleteJob(jobId)") + PAGE[PAGE.index("deleteJob(jobId)"):][:200]


def test_the_header_actions_may_wrap():
    """DEC-016: two more buttons must not push the header past a phone's width."""
    header = PAGE[PAGE.index('<div className="page-header">'):]
    header = header[:header.index("</div>\n      </div>")]
    assert "flexWrap: 'wrap'" in header
