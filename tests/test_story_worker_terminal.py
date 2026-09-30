"""How a story step's job ends (AI Story phase 4, stage 10; DEC-161, amending
DEC-108 and DEC-109).

``steps.ends_completed(step, params)`` decides it: ``render``, ``metadata``,
``fast-track`` and a regenerate of ``metadata:<ep>:<platform>`` leave
nothing to approve and end ``completed``; every other step ends
``awaiting_approval`` exactly as before. The worker asks it once the runner
has returned (``web/api/worker.py``, ``_execute_story_step``).

The decision is stdlib and runs in the pytest-only CI environment; the
worker tests reuse ``tests/test_story_step_jobs.py``'s store and worker
fixtures, which need pydantic and skip without it like every job test
(DEC-012). ``tests/test_stale_jobs.py`` and ``tests/test_job_stream.py`` are
left as they are (RC-S1, RC-S2).
"""

from __future__ import annotations

import pytest

from clipping.aistory import steps
from clipping.cancel import CancelToken
from test_story_step_jobs import _messages, _register, _step_job, job_store, worker  # noqa: F401 -- fixtures

# Phase 5 stage 8 adds the re-render (DEC-161: nothing to approve).
COMPLETED = ("render", "metadata", "fast-track", "rerender")
AWAITING = ("concepts", "bible", "style_preview", "cast", "places_proposal", "places", "season", "script",
            "storyboard", "assets", "regenerate")


# ------------------------------------------------------------ the decision (CI)

@pytest.mark.parametrize("step", COMPLETED)
def test_a_step_with_nothing_to_approve_ends_completed(step):
    assert steps.ends_completed(step, {}) is True
    assert steps.ends_completed(step, None) is True
    assert steps.ends_completed(step) is True


@pytest.mark.parametrize("step", AWAITING)
def test_every_other_step_still_awaits_approval(step):
    assert steps.ends_completed(step, {}) is False


@pytest.mark.parametrize("target, expected", [
    ("metadata:1:tiktok", True),
    ("metadata:12:reels", True),
    ("shot:1:sh01", False),
    ("shot:1:sh01:plan", False),
    ("shot:1:sh01:video", False),
    ("line:1:l01", False),
    ("scene:1:s02", False),
    ("hook:1", False),
    ("teaser:1", False),
    ("season:1", False),
    ("character:char_kiwilo:voice", False),
    ("bible:tone", False),
    ("concepts", False),
    ("metadata:1", False),
    ("metadata:1:tiktok:again", False),
    ("metadata::tiktok", False),
    (None, False),
    (7, False),
])
def test_only_a_metadata_regenerate_ends_completed(target, expected):
    assert steps.ends_completed("regenerate", {"target": target, "note": None}) is expected


def test_the_decision_reads_a_regenerate_target_only_from_its_params():
    assert steps.ends_completed("regenerate", None) is False
    assert steps.ends_completed("regenerate", {}) is False
    assert steps.ends_completed("regenerate", "metadata:1:tiktok") is False
    # the target of another step is not a regenerate's
    assert steps.ends_completed("script", {"target": "metadata:1:tiktok"}) is False


def test_every_step_that_ends_completed_is_a_registered_runner():
    assert set(steps.COMPLETED_STEPS) == set(COMPLETED) and set(COMPLETED) <= set(steps.RUNNERS)
    assert steps.COMPLETED_TARGET_KINDS == ("metadata",)


# --------------------------------------------------------------- the worker

@pytest.mark.parametrize("step", COMPLETED)
def test_the_worker_ends_a_step_with_nothing_to_approve_completed(worker, job_store, monkeypatch, step):
    _register(monkeypatch, step, lambda ctx: {"ep": ctx.ep})
    job_id = _step_job(job_store, step=step, story_id=worker.story_id, ep=1)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    job = job_store.get_job(job_id)
    assert job["status"] == "completed" and job["error"] is None
    assert job.get("approved_at") is None and job.get("superseded_by") is None
    messages = _messages(job_store, job_id)
    assert messages[-1] == f"Story step '{step}' is done."
    assert not any("awaiting your approval" in message for message in messages)
    # finished: a cancel is refused, an approval has nothing to move on
    assert job_store.request_cancel(job_id) == "terminal"
    assert job_store.approve_step_job(job_id) == "not_awaiting"
    assert job_store.get_job(job_id)["status"] == "completed"
    assert worker.clip_runs == []


def test_the_worker_ends_a_metadata_regenerate_completed(worker, job_store, monkeypatch):
    _register(monkeypatch, "regenerate", lambda ctx: {"target": ctx.params["target"]})
    job_id = _step_job(job_store, step="regenerate", story_id=worker.story_id,
                       params={"target": "metadata:1:shorts", "note": "shorter"})

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    assert job_store.get_job(job_id)["status"] == "completed"
    assert _messages(job_store, job_id)[-1] == "Story step 'regenerate' is done."


@pytest.mark.parametrize("step, params", [
    ("script", {}),
    ("storyboard", {}),
    ("assets", {}),
    ("bible", {}),
    ("regenerate", {"target": "shot:1:sh02", "note": None}),
    ("regenerate", {"target": "line:1:l03", "note": None}),
    ("regenerate", {"target": "scene:1:s02", "note": None}),
    ("regenerate", {"target": "shot:1:sh02:plan", "note": None}),
])
def test_the_worker_still_leaves_every_other_step_awaiting_approval(worker, job_store, monkeypatch, step, params):
    _register(monkeypatch, step, lambda ctx: None)
    job_id = _step_job(job_store, step=step, story_id=worker.story_id, params=params)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    assert job_store.get_job(job_id)["status"] == "awaiting_approval"
    assert _messages(job_store, job_id)[-1] == f"Story step '{step}' is ready: awaiting your approval."
    assert job_store.approve_step_job(job_id) == "ok"


def test_a_result_with_nothing_to_approve_that_lands_after_a_cancel_is_cancelled(worker, job_store, monkeypatch):
    def finishes_anyway(ctx):
        job_store.request_cancel(ctx.job_id)
        ctx.cancel.cancel()
        return {"state": "completed"}

    _register(monkeypatch, "render", finishes_anyway)
    job_id = _step_job(job_store, step="render", story_id=worker.story_id, ep=1)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    assert job_store.get_job(job_id)["status"] == "cancelled"
    assert not any(message.endswith("is done.") for message in _messages(job_store, job_id))


def test_a_step_with_nothing_to_approve_that_fails_is_failed(worker, job_store, monkeypatch):
    def broken(ctx):
        raise steps.StepFailed("Fast track stopped at the paid check (step 3 of 6): allow_paid is off.")

    _register(monkeypatch, "fast-track", broken)
    job_id = _step_job(job_store, step="fast-track", story_id=worker.story_id, ep=1)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    job = job_store.get_job(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "StepFailed: Fast track stopped at the paid check (step 3 of 6): allow_paid is off."


def test_a_restart_leaves_a_completed_step_completed(worker, job_store, monkeypatch):
    _register(monkeypatch, "metadata", lambda ctx: None)
    job_id = _step_job(job_store, step="metadata", story_id=worker.story_id, ep=1)
    worker._run_pipeline_sync(job_id, {}, CancelToken())

    assert job_store.fail_stale_jobs() == []
    assert job_store.get_job(job_id)["status"] == "completed"
