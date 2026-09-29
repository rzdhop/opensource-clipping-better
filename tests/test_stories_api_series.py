"""The AI Story API, step 13 -- memory, feedback and propose-next (phase 5,
stage 5; plan 11; spec 3, 9.1, 9.2; DEC-130 as amended, DEC-173).

``web/api/routes/stories.py`` on a throwaway app -- the stories router and the
jobs router, never the singleton ``web.api.app.app`` -- with the job store on
an empty table under ``tmp_path``, ``worker.OUTPUTS_ROOT`` under ``tmp_path``
and ``worker.submit_job`` replaced by a recorder, exactly as the phase-4 API
tests have it. The story and its episode-1 fixtures are stage 4's own
(``tests/test_story_series_steps.py``): the step modules run directly against
``api.store`` to build "episode 1 approved", "memory approved" and "proposed"
stories, never through a job -- the routes under test are the ones stage 5
adds: ``POST /steps/memory|feedback|propose-next`` through the generic
dispatch, ``POST .../episodes/{ep}/feedback``, ``POST .../episodes/{ep}/
proposals/{item_id}``, the series part of ``POST .../approve/{doc}``, the
``GET`` payloads' new ``series``/``next_episode_gate`` fields, and the three
steps' estimates. A job that has to *run* goes through the real worker path
(``worker._execute_story_step``) with a fake LLM swapped into
``steps.RUNNERS``, as the phase-4 tests do for the assets step.

Covered: each step queued (its own precondition, its params, the key gate,
the global one-job-per-story busy rule, the queue cap), the estimates; the
feedback paste (stored, queued, the 6,000-character cap on text and stats --
exactly 6,000 accepted, 6,001 refused, never trimmed -- busy checked before
anything is written); the proposal decision (accept a recurring character
keeps the story ready, accept a lead folds the cast approval and queues the
cast job, a twist keeps the season approval, reject records only, a second
decision is 409, an unknown item is 404, a non-bool ``accept`` and a
misplaced ``role`` are 400 -- exactly as the CLI's own check would answer);
approving ``memory:<ep>``/``feedback:<ep>``/``proposals:<ep>`` (``direction``
required only for feedback, absent told apart from null); the story and
episode pages' ``series``/``next_episode_gate`` fields; every new route
answering with no ``Authorization`` header (``API_TOKEN`` unset, RC-M9).

Every test needs fastapi (``web/api/routes/stories.py`` imports it at module
level, unlike ``web/api/models.py``'s plain pydantic classes) and skips
without it, like the other route tests. The routes are reached through the
client only, so on the parent commit (``86de38d``) each test fails on its own
(``POST /steps/memory`` etc. answer 404 there).
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_series_steps as tss
from clipping.aistory import steps
from clipping.cancel import CancelToken
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used here as they are
from test_story_series_steps import m  # noqa: F401 -- stage 4's fixture: the step modules

ROOT = Path(__file__).resolve().parents[1]
NOW = eps.NOW
LATER = tss.LATER
UNKNOWN_ID = "0123456789ab"


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def api(monkeypatch, tmp_path, store):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from web.api import store as job_store
    from web.api import worker
    from web.api.routes import jobs, stories

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(worker, "_settings_env", tas._settings())
    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "PERSIST_PATH", str(tmp_path / "jobs.json"))
    outputs = tmp_path / "outputs"
    outputs.mkdir(exist_ok=True)
    monkeypatch.setattr(worker, "OUTPUTS_ROOT", str(outputs))
    monkeypatch.setattr(worker, "UPLOADS_ROOT", str(tmp_path / "uploads"))

    submitted = []

    async def fake_submit(job_id, payload):
        submitted.append(job_id)

    monkeypatch.setattr(worker, "submit_job", fake_submit)
    monkeypatch.setenv("DISABLE_AUTH", "1")  # belt and braces: API_TOKEN is never set either (RC-M9)
    monkeypatch.delenv("API_TOKEN", raising=False)

    app = FastAPI()
    app.include_router(stories.router)
    app.include_router(jobs.router)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, jobs=job_store, worker=worker, outputs=outputs, submitted=submitted,
            monkeypatch=monkeypatch, tmp_path=tmp_path, routes=stories, store=store,
        )


def _url(story_id, suffix=""):
    return f"/api/stories/{story_id}{suffix}"


def _post_step(api, story_id, step, ep=None, params=None):
    body = {}
    if ep is not None:
        body["ep"] = ep
    if params is not None:
        body["params"] = params
    return api.client.post(_url(story_id, f"/steps/{step}"), json=body)


def _story(api, story_id):
    response = api.client.get(_url(story_id))
    assert response.status_code == 200, response.text
    return response.json()


def _episode(api, story_id, ep=1):
    response = api.client.get(_url(story_id, f"/episodes/{ep}"))
    assert response.status_code == 200, response.text
    return response.json()


def _approve(api, story_id, doc, json=None):
    if json is None:
        return api.client.post(_url(story_id, f"/approve/{doc}"))
    return api.client.post(_url(story_id, f"/approve/{doc}"), json=json)


def _feedback(api, story_id, ep, text, stats=None):
    body = {"text": text}
    if stats is not None:
        body["stats"] = stats
    return api.client.post(_url(story_id, f"/episodes/{ep}/feedback"), json=body)


def _decide(api, story_id, ep, item_id, accept, role=None):
    body = {"accept": accept}
    if role is not None:
        body["role"] = role
    return api.client.post(_url(story_id, f"/episodes/{ep}/proposals/{item_id}"), json=body)


def _job(api, story_id, step, status=None, params=None, ep=None):
    from web.api.models import JobStatus

    job_id = api.jobs.create_job(kind="story_step", story_id=story_id, step=step, ep=ep, params=params)
    if status is not None:
        api.jobs.set_status(job_id, JobStatus(status))
    return job_id


def _run(api, job_id):
    """Run a queued step job the way the worker does (its runner as registered
    now); returns the job as it ended."""
    job = api.jobs.get_job(job_id)
    api.worker._execute_story_step(job_id, job, CancelToken())
    return api.jobs.get_job(job_id)


def _settle(api, job_id):
    """Mark a queued job failed, out of the way of the global one-job-per-
    story busy rule, once its side effect (a paste, say) is the only thing
    the test cares about."""
    from web.api.models import JobStatus

    api.jobs.set_status(job_id, JobStatus.FAILED)


def _season_bytes(api, story_id):
    return (Path(api.store.story_dir(story_id)) / "season.json").read_bytes()


def _fake_step(api, step, module, **run_kwargs):
    """Swap *step*'s runner for one that answers from a fake LLM, as the
    worker path would call it; returns the queue of calls made (none until a
    job of *step* runs)."""
    def runner(ctx):
        return module.run(ctx, **run_kwargs)

    api.monkeypatch.setitem(steps.RUNNERS, step, runner)


def _digest_feedback(api, job_id, reply=None):
    """Run a queued ``feedback`` job to completion on F1's fixture reply (the
    real chain is never reached: ``steps.RUNNERS["feedback"]`` is swapped for
    the step's own ``run()`` on a fake LLM, as the worker would call it)."""
    from clipping.aistory.steps import feedback as feedback_step

    _fake_step(api, "feedback", feedback_step, runner=eps.FakeLLM(F1=[reply or tss.F1_REPLY]),
              time_fn=eps.Clock(100.0))
    return _run(api, job_id)


# ------------------------------------------------------------ series fixtures

def _written_ep1(api, **kwargs):
    return tss._written_ep1(api.store, **kwargs)


def _with_memory(api, m, story_id=None, **kwargs):
    story_id = story_id or _written_ep1(api)
    tss._with_memory(m, api.store, story_id, **kwargs)
    return story_id


def _approved_memory_story(api, m):
    return tss._approved_memory_story(m, api.store)


def _proposed(api, m, **kwargs):
    return tss._proposed(m, api.store, **kwargs)


# ============================================================ grammar (CI)

def test_series_steps_are_episode_jobs_of_their_own_document():
    """``_job_doc``/``_episode_of`` cover the series steps (already wired in
    stage 4's own edit of ``_job_doc``; stage 5 extends ``_episode_of``):
    ``propose-next`` of episode N counts as N + 1's -- its document is
    N + 1's proposals -- so the episode page of N + 1 lists it as busy, not
    N's. Needs fastapi (``web/api/routes/stories.py`` imports it at module
    level), unlike the request models' own text/AST guards -- skips in the
    CI environment."""
    pytest.importorskip("fastapi")
    from web.api.routes import stories as routes

    assert routes._job_doc("memory", {}, ep=3) == "memory:3"
    assert routes._job_doc("feedback", {}, ep=3) == "feedback:3"
    assert routes._job_doc("propose-next", {}, ep=3) == "proposals:4"
    assert routes._job_doc("memory", {}) is None  # no ep: nothing to name

    assert routes._episode_of({"step": "memory", "ep": 3}) == 3
    assert routes._episode_of({"step": "feedback", "ep": 3}) == 3
    assert routes._episode_of({"step": "propose-next", "ep": 3}) == 4
    assert routes._episode_of({"step": "propose-next", "ep": None}) is None


# ================================================================ steps

def test_each_series_step_queues_one_job_of_its_episode(api, m):
    story_id = _approved_memory_story(api, m)
    _feedback(api, story_id, 1, tss.FEEDBACK)  # feedback needs a pasted item first
    _settle(api, api.submitted[-1])  # settle the job the paste queued

    for step, ep in (("memory", 1), ("feedback", 1), ("propose-next", 1)):
        response = _post_step(api, story_id, step, ep=ep)
        assert response.status_code == 201, (step, response.text)
        job = response.json()
        assert (job["step"], job["ep"], job["params"], job["status"]) == (step, ep, {}, "queued")
        assert api.submitted[-1] == job["id"]
        busy_ep = ep + 1 if step == "propose-next" else ep
        assert [j["id"] for j in _episode(api, story_id, busy_ep)["jobs"]] == [job["id"]], step
        _settle(api, job["id"])


def test_a_series_step_is_refused_before_any_job_by_its_own_precondition(api):
    story_id = eps._ready_story(api.store)
    for body, status, needle in (
            ({}, 400, "send its number as ep"),
            ({"ep": 0}, 400, "The season plans episodes 1 to 8; there is no episode 0."),
            ({"ep": 9}, 400, "there is no episode 9"),
    ):
        for step in ("memory", "feedback", "propose-next"):
            response = api.client.post(_url(story_id, f"/steps/{step}"), json=body)
            assert response.status_code == status, (step, body, response.text)
            assert needle in response.json()["detail"], (step, body, response.text)

    response = _post_step(api, story_id, "memory", ep=1)
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Episode 1 has no script yet: write it and approve it (script:1) first; its series memory is written "
        "from the approved script.")

    response = _post_step(api, story_id, "feedback", ep=1)
    assert response.status_code == 409
    assert response.json()["detail"] == "Episode 1 has no audience feedback yet: paste it first."

    response = _post_step(api, story_id, "propose-next", ep=1)
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Episode 1's series memory is not written yet: approve episode 1's script, then run memory for episode 1 "
        "and approve it, before proposing what comes next in episode 2.")

    response = _post_step(api, story_id, "propose-next", ep=8)
    assert response.status_code == 409
    assert response.json()["detail"] == "Episode 8 is the last the season plans: there is no episode 9 to propose for."

    api.store.update(story_id, lambda doc: doc["approvals"].update(season=None), now=NOW)
    response = _post_step(api, story_id, "memory", ep=1)
    assert response.status_code == 409
    assert response.json()["detail"] == "The story is not ready yet: approve the cast, the places and the season first."
    assert api.jobs.list_jobs() == [] and api.submitted == []


@pytest.mark.parametrize("step", ["memory", "feedback", "propose-next"])
def test_a_series_steps_params_are_a_closed_empty_list(api, m, step):
    story_id = _approved_memory_story(api, m)
    if step == "feedback":
        _feedback(api, story_id, 1, tss.FEEDBACK)
        _settle(api, api.submitted[-1])  # settle the job the paste queued

    response = _post_step(api, story_id, step, ep=1, params={"nope": 1})

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == f"'{step}' takes no parameters."
    assert all(j["status"] == "failed" for j in api.jobs.list_jobs())


def test_series_steps_meet_the_global_one_job_busy_rule(api, m):
    """The site's one-step-per-story rule is global, so it alone already
    keeps a series step of episode N from overlapping a script/storyboard job
    of episode N -- there is no separate per-episode series check to add.
    Every step's own precondition is satisfied first (a paste for feedback,
    directly on the season -- no job -- so the busy job is the only thing
    left to refuse any of the three)."""
    story_id = _approved_memory_story(api, m)
    from clipping.aistory import workflow

    workflow.store_feedback(api.store, story_id, 1, tss.FEEDBACK, now=NOW)
    job_id = _job(api, story_id, "script", status="queued", ep=1)
    for step in ("memory", "feedback", "propose-next"):
        response = _post_step(api, story_id, step, ep=1)
        assert response.status_code == 409, (step, response.text)
        assert job_id in response.json()["detail"], step
    _settle(api, job_id)


def test_a_series_step_meets_the_key_gate(api, m):
    from clipping.aistory import workflow

    story_id = _approved_memory_story(api, m)
    workflow.store_feedback(api.store, story_id, 1, tss.FEEDBACK, now=NOW)
    api.monkeypatch.setattr(api.worker, "_settings_env", {**tas._settings(), "GOOGLE_API_KEY": ""})
    for step in ("memory", "feedback", "propose-next"):
        response = _post_step(api, story_id, step, ep=1)
        assert response.status_code == 400 and "No link in the LLM chain has an API key" in response.json()["detail"]
    assert api.jobs.list_jobs() == []


def test_one_step_at_a_time_then_the_queue_cap_for_series_steps(api, m):
    from clipping.aistory import workflow

    story_id = _approved_memory_story(api, m)
    workflow.store_feedback(api.store, story_id, 1, tss.FEEDBACK, now=NOW)
    api.monkeypatch.setenv("MAX_QUEUED_JOBS", "1")
    _job(api, eps._ready_story(api.store), "concepts")  # another story's job fills the queue
    for step in ("memory", "feedback", "propose-next"):
        assert _post_step(api, story_id, step, ep=1).status_code == 429, step
    assert not any(j.get("story_id") == story_id for j in api.jobs.list_jobs())


def test_a_newer_series_job_supersedes_the_one_awaiting_approval(api, m):
    story_id = _approved_memory_story(api, m)
    older = _job(api, story_id, "memory", status="awaiting_approval", ep=1)

    response = _post_step(api, story_id, "memory", ep=1)

    assert response.status_code == 201, response.text
    assert api.jobs.get_job(older)["superseded_by"] == response.json()["id"]


# ============================================================== estimates

def test_series_estimates_answer_one_llm_call(api, m):
    from clipping.aistory import workflow

    story_id = _approved_memory_story(api, m)
    workflow.store_feedback(api.store, story_id, 1, tss.FEEDBACK, now=NOW)

    for step in ("memory", "feedback", "propose-next"):
        response = api.client.get(_url(story_id, f"/estimate/{step}"), params={"ep": 1})
        assert response.status_code == 200, (step, response.text)
        body = response.json()
        assert body["step"] == step and body["units"] == {"llm_calls": 1} and body["ep"] == 1
        assert body["ready"] is True and body["est_usd"] == 0.0


def test_a_series_estimate_is_refused_before_anything_by_its_own_precondition(api):
    story_id = eps._ready_story(api.store)
    response = api.client.get(_url(story_id, "/estimate/memory"), params={"ep": 1})
    assert response.status_code == 409
    assert "has no script yet" in response.json()["detail"]

    response = api.client.get(_url(story_id, "/estimate/propose-next"), params={"ep": 9})
    assert response.status_code == 400


# ============================================================ feedback paste

def test_posting_feedback_stores_it_and_queues_the_step(api):
    story_id = _written_ep1(api)

    response = _feedback(api, story_id, 1, tss.FEEDBACK, tss.STATS)

    assert response.status_code == 201, response.text
    job = response.json()
    assert (job["step"], job["ep"], job["status"]) == ("feedback", 1, "queued")
    season = api.store.read_doc(story_id, "season.json")
    assert season["audience_feedback"] == [{"ep": 1, "pasted_at": season["audience_feedback"][0]["pasted_at"],
                                            "text": tss.FEEDBACK, "stats": tss.STATS}]


def test_a_second_paste_replaces_the_first(api):
    story_id = _written_ep1(api)
    _feedback(api, story_id, 1, "Premier jet.")
    _settle(api, api.submitted[-1])

    response = _feedback(api, story_id, 2, "Un autre épisode.")
    _settle(api, api.submitted[-1])
    response = _feedback(api, story_id, 1, "Nouveaux commentaires.")

    assert response.status_code == 201, response.text
    season = api.store.read_doc(story_id, "season.json")
    assert [item["ep"] for item in season["audience_feedback"]] == [2, 1]
    assert season["audience_feedback"][1]["text"] == "Nouveaux commentaires."


def test_feedback_over_the_cap_is_refused_and_never_stored(api):
    story_id = _written_ep1(api)
    before = _season_bytes(api, story_id)

    response = _feedback(api, story_id, 1, "x" * 6001)
    assert response.status_code == 422, response.text

    response = _feedback(api, story_id, 1, tss.FEEDBACK, "y" * 6001)
    assert response.status_code == 422, response.text
    assert _season_bytes(api, story_id) == before
    assert api.jobs.list_jobs() == []

    response = _feedback(api, story_id, 1, "x" * 6000, "y" * 6000)
    assert response.status_code == 201, response.text
    season = api.store.read_doc(story_id, "season.json")
    assert len(season["audience_feedback"][0]["text"]) == 6000
    assert len(season["audience_feedback"][0]["stats"]) == 6000


def test_an_empty_feedback_text_is_refused(api):
    story_id = _written_ep1(api)

    response = _feedback(api, story_id, 1, "   ")

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "The feedback text is empty: paste the comments first."
    assert api.jobs.list_jobs() == []


def test_a_feedback_paste_is_refused_while_a_step_of_the_story_is_in_flight(api):
    story_id = _written_ep1(api)
    job_id = _job(api, story_id, "concepts", status="running")
    before = _season_bytes(api, story_id)

    response = _feedback(api, story_id, 1, tss.FEEDBACK)

    assert response.status_code == 409
    assert job_id in response.json()["detail"]
    assert _season_bytes(api, story_id) == before  # refused before anything was written


def test_feedback_paste_bounds_the_episode_first(api):
    story_id = eps._ready_story(api.store)

    response = _feedback(api, story_id, 9, tss.FEEDBACK)

    assert response.status_code == 400
    assert "there is no episode 9" in response.json()["detail"]


# ============================================================= proposals

def test_deciding_an_unknown_item_is_not_found(api, m):
    story_id, _llm = _proposed(api, m, feedback=False)

    response = _decide(api, story_id, 2, "char_9", accept=True)

    assert response.status_code == 404
    assert "no item 'char_9'" in response.json()["detail"]


def test_rejecting_records_the_decision_only(api, m):
    story_id, _llm = _proposed(api, m, feedback=False)

    response = _decide(api, story_id, 2, "char_1", accept=False)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "rejected" and "job" not in body and "cast" not in body
    assert api.store.read_episode_doc(story_id, 2, "proposals.json")["decisions"] == {"char_1": "rejected"}


def test_deciding_the_same_item_twice_is_a_conflict(api, m):
    story_id, _llm = _proposed(api, m, feedback=False)
    assert _decide(api, story_id, 2, "twist_1", accept=False).status_code == 200

    response = _decide(api, story_id, 2, "twist_1", accept=True)

    assert response.status_code == 409
    assert "already rejected" in response.json()["detail"]


def test_accepting_a_twist_keeps_the_season_approval_and_carries_no_job(api, m):
    story_id, _llm = _proposed(api, m, feedback=False)
    story_before = _story(api, story_id)["story"]

    response = _decide(api, story_id, 2, "twist_1", accept=True)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "accepted" and body["kind"] == "twist" and "job" not in body
    arc = api.store.read_doc(story_id, "season.json")["arc"][2]
    assert arc["summary"] == tss.N1_REPLY["twists"][0]["summary"]
    assert _story(api, story_id)["story"]["approvals"] == story_before["approvals"]


@pytest.mark.parametrize("role, folds", [(None, False), ("guest", False), ("lead", True), ("support", True)])
def test_accepting_a_character_queues_the_cast_job_and_dec_123_folds_only_lead_or_support(api, m, role, folds):
    story_id, _llm = _proposed(api, m, feedback=False)

    response = _decide(api, story_id, 2, "char_1", accept=True, role=role)

    assert response.status_code == 200, response.text
    body = response.json()
    final_role = role or "recurring"
    assert body["decision"] == "accepted" and body["kind"] == "character" and body["role"] == final_role
    assert body["folds_cast"] is folds
    assert ("cast approval is cleared" in body["message"]) is folds
    job = body["job"]
    assert job["step"] == "cast" and job["status"] == "queued"
    assert job["params"] == {
        "custom": [{"name": "Figuette", "role": final_role, "one_line": "Une figue timide qui voit tout.",
                    "archetype": "témoin discret"}],
        "introduced_in": 2,
    }
    assert api.submitted[-1] == job["id"]
    assert api.store.read_episode_doc(story_id, 2, "proposals.json")["decisions"] == {"char_1": "accepted"}


def test_a_non_bool_accept_or_a_misplaced_role_is_invalid(api, m):
    story_id, _llm = _proposed(api, m, feedback=False)

    for body, needle in (
            ({"accept": "yes"}, "accept is true or false, not 'yes'."),
            ({"accept": 1}, "accept is true or false, not 1."),
            ({"accept": True, "role": "hero"}, "role is one of"),
    ):
        response = api.client.post(_url(story_id, "/episodes/2/proposals/char_1"), json=body)
        assert response.status_code == 400, (body, response.text)
        assert needle in response.json()["detail"], (body, response.text)

    response = api.client.post(_url(story_id, "/episodes/2/proposals/twist_1"), json={"accept": True, "role": "lead"})
    assert response.status_code == 400
    assert "A role is chosen only when accepting a character." in response.json()["detail"]
    assert api.store.read_episode_doc(story_id, 2, "proposals.json")["decisions"] == {}


def test_deciding_a_character_the_cast_cannot_take_is_a_conflict(api, m):
    story_id, _llm = _proposed(api, m, feedback=False)
    doc = copy.deepcopy(eps.CHARACTERS[2])
    doc.update(char_id="char_figuette", name="Figuette", role="guest", approved_at=None)
    api.store.write_entity(story_id, "characters", doc, now=NOW)

    response = _decide(api, story_id, 2, "char_1", accept=True)

    assert response.status_code == 409
    assert response.json()["detail"] == "Figuette is already a character of this story: reject this proposal instead."
    assert api.store.read_episode_doc(story_id, 2, "proposals.json")["decisions"] == {}


def test_a_decision_is_refused_while_a_step_of_the_story_is_in_flight(api, m):
    story_id, _llm = _proposed(api, m, feedback=False)
    job_id = _job(api, story_id, "concepts", status="running")

    response = _decide(api, story_id, 2, "char_1", accept=False)

    assert response.status_code == 409
    assert job_id in response.json()["detail"]
    assert api.store.read_episode_doc(story_id, 2, "proposals.json")["decisions"] == {}


def test_the_cast_gate_refuses_an_accept_before_anything_is_decided(api, m):
    story_id, _llm = _proposed(api, m, feedback=False)
    api.monkeypatch.setattr(api.worker, "_settings_env", {**tas._settings(), "GOOGLE_API_KEY": ""})

    response = _decide(api, story_id, 2, "char_1", accept=True)

    assert response.status_code == 400
    assert "No link in the LLM chain has an API key" in response.json()["detail"]
    assert api.store.read_episode_doc(story_id, 2, "proposals.json")["decisions"] == {}
    assert api.jobs.list_jobs() == []


def test_deciding_bounds_the_episode_first(api):
    story_id = eps._ready_story(api.store)

    response = _decide(api, story_id, 9, "char_1", accept=False)

    assert response.status_code == 400
    assert "there is no episode 9" in response.json()["detail"]


# ============================================================== approve

def test_approving_memory_needs_an_entry_then_it_shows_on_the_episode_page(api, m):
    story_id = _written_ep1(api)

    response = _approve(api, story_id, "memory:1")
    assert response.status_code == 409
    assert "no series memory yet" in response.json()["detail"]

    _with_memory(api, m, story_id, approve=False)
    response = _approve(api, story_id, "memory:1")

    assert response.status_code == 200, response.text
    page = response.json()
    assert page["series"]["memory"]["state"] == "approved"
    assert page["series"]["memory"]["entry"]["approved_at"]
    assert page["series"]["next_episode_gate"] is None


def test_approving_feedback_needs_a_direction_told_apart_from_absent_and_null(api):
    story_id = _written_ep1(api)
    _feedback(api, story_id, 1, tss.FEEDBACK)
    _digest_feedback(api, api.submitted[-1])

    response = _approve(api, story_id, "feedback:1")  # no body at all
    assert response.status_code == 400
    assert "takes a direction" in response.json()["detail"]

    response = _approve(api, story_id, "feedback:1", json={})  # a body, but direction left out
    assert response.status_code == 400
    assert "takes a direction" in response.json()["detail"]

    response = _approve(api, story_id, "feedback:1", json={"direction": None})  # explicit null: a choice
    assert response.status_code == 200, response.text
    assert response.json()["series"]["feedback"]["chosen_direction"] is None

    response = _approve(api, story_id, "feedback:1", json={"direction": 2})
    assert response.status_code == 200, response.text
    assert response.json()["series"]["feedback"]["chosen_direction"] == 2


@pytest.mark.parametrize("bad", [True, "1", 1.0, 3, -1])
def test_a_bad_feedback_direction_is_invalid(api, bad):
    story_id = _written_ep1(api)
    _feedback(api, story_id, 1, tss.FEEDBACK)
    _digest_feedback(api, api.submitted[-1])

    response = _approve(api, story_id, "feedback:1", json={"direction": bad})

    assert response.status_code == 400
    assert f"not {bad!r}" in response.json()["detail"]


def test_a_direction_is_only_for_feedback(api, m):
    story_id = _written_ep1(api)
    _with_memory(api, m, story_id, approve=False)

    response = _approve(api, story_id, "memory:1", json={"direction": 1})

    assert response.status_code == 400
    assert "A direction is chosen only when approving feedback:<ep>" in response.json()["detail"]


def test_approving_proposals_needs_every_item_decided(api, m):
    story_id, _llm = _proposed(api, m, feedback=False)

    response = _approve(api, story_id, "proposals:2")
    assert response.status_code == 409
    assert "not all decided" in response.json()["detail"]

    for item_id in ("char_1", "char_2", "twist_1"):
        _decide(api, story_id, 2, item_id, accept=False)
    response = _approve(api, story_id, "proposals:2")

    assert response.status_code == 200, response.text
    assert response.json()["series"]["proposals"]["decisions"] == {
        "char_1": "rejected", "char_2": "rejected", "twist_1": "rejected"}


def test_approving_a_series_document_is_refused_while_its_episode_is_busy(api, m):
    story_id = _written_ep1(api)
    _with_memory(api, m, story_id, approve=False)
    job_id = _job(api, story_id, "storyboard", status="running", ep=1)

    response = _approve(api, story_id, "memory:1")

    assert response.status_code == 409
    assert job_id in response.json()["detail"]


def test_approving_memory_completes_its_awaiting_job(api, m):
    story_id = _written_ep1(api)
    _with_memory(api, m, story_id, approve=False)
    job_id = _job(api, story_id, "memory", status="awaiting_approval", ep=1)

    response = _approve(api, story_id, "memory:1")

    assert response.status_code == 200, response.text
    from web.api.models import JobStatus

    assert api.jobs.get_job(job_id)["status"] == JobStatus.COMPLETED.value


# =========================================================== GET payloads

def test_the_story_and_episode_pages_expose_the_series_fields(api, m):
    story_id, _llm = _proposed(api, m, feedback=True)

    story = _story(api, story_id)
    series = story["series"]
    assert [entry["ep"] for entry in series] == list(range(1, 9))
    ep1, ep2 = series[0], series[1]
    assert ep1["memory"]["state"] == "approved" and ep1["memory"]["entry"]["script_rev"] == 1
    assert ep1["feedback"]["chosen_direction"] == 1
    assert ep1["proposals"] is None  # episode 1's own: nothing proposed for it
    assert ep1["next_episode_gate"] is None  # episode 1's memory is approved and fresh
    assert ep2["proposals"]["for_ep"] == 2
    assert ep2["memory"]["state"] == "none"
    assert ep2["next_episode_gate"] is not None and "Episode 2's series memory is not written yet" in \
        ep2["next_episode_gate"]
    assert series[7]["next_episode_gate"] is None  # the last episode: nothing after it to gate

    page = _episode(api, story_id, 1)
    assert page["series"] == ep1
    page2 = _episode(api, story_id, 2)
    assert page2["series"] == ep2

    # Existing fields keep their shape (RC-A4/A5's spirit: only new keys were added).
    for key in ("ep", "script", "storyboard", "template", "state", "assets", "render", "metadata", "jobs"):
        assert key in page


def test_a_season_with_no_episodes_planned_yet_has_an_empty_series_list(api):
    story_id = api.store.create(language="fr", seed_text=None, now=NOW)["story_id"]

    story = _story(api, story_id)

    assert story["series"] == []


# ================================================================ no auth

def test_the_new_routes_answer_with_no_authorization_header(api, m):
    """RC-M9: with ``API_TOKEN`` unset, every new route is open -- never a
    401 -- with no ``Authorization`` header sent at all (the ``api``
    fixture's client sends none; a 401 could only come from ``require_token``,
    nothing else these routes do)."""
    story_id, _llm = _proposed(api, m, feedback=True)

    for response in (
            api.client.get(_url(story_id, "/estimate/memory"), params={"ep": 1}),
            _feedback(api, story_id, 1, "Sans jeton."),
            _decide(api, story_id, 2, "char_2", accept=False),
            _approve(api, story_id, "proposals:2"),
            _post_step(api, story_id, "memory", ep=1),
    ):
        assert response.status_code != 401, response.text
