"""The AI Story API, steps 8-9: one episode's script and storyboard (phase 3,
stage 8; spec 3, 9.1, 9.2).

``web/api/routes/stories.py`` on a throwaway app -- the stories router and the
jobs router, never the singleton ``web.api.app.app`` -- with the job store on
an empty table under ``tmp_path``, ``worker.OUTPUTS_ROOT`` under ``tmp_path``
and ``worker.submit_job`` replaced by a recorder. The story is the stage-6
fixture (``test_story_episode_steps``: French, ready, three characters, two
places, a prop, an eight-episode arc, a locked fruit_drama style). A job that
has to *run* goes through the real worker path
(``worker._execute_story_step``) with its runner answered by the stage-6 fake
LLM: no network, no real key (the Settings values are test values).

Covered: the script and storyboard steps (a job with its episode, the key
gate, the queue cap, one step per story; the fast storyboard inline, with no
call and no job), their refusals before any job, the approvals of both
documents and the jobs they complete, the edits, the episode length, the
episode targets of ``regenerate`` and the document each is a job of, the
estimates, the episode page and the story page's summary, the line audio
route, and the token in front of every new route. ``story.json`` is never
changed by any of them (RC-E2).

Needs pydantic, fastapi and httpx; skips without them, like the other route
tests. The routes are reached through the client only, so against the parent
commit each test fails on its own.
"""

from __future__ import annotations

import ast
import importlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipping.aistory import steps
from clipping.aistory.store import StoryStore
from clipping.cancel import CancelToken
from clipping.providers.errors import ProviderError

from test_story_episode_steps import (  # noqa: F401 -- hermetic is an autouse fixture
    ALL_SCENES,
    BROCCOLIA,
    E4_ISSUES,
    E4_PASSED,
    KIWILO,
    MANGELLA,
    NOW,
    SETTINGS,
    FakeLLM,
    _ready_story,
    _scene,
    _script_llm,
    e2_reply,
    hermetic,
    t1_reply,
    t1r_reply,
)

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "web" / "api" / "models.py"
UNKNOWN_ID = "0123456789ab"
DOWN = ProviderError("every provider failed", [("gemini/gemini-test", "HTTP 503")])


# ------------------------------------------------------------ text guards (CI)

def _class_fields(name: str) -> list:
    """A pydantic model's field names, in order, read without importing pydantic."""
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return [stmt.target.id for stmt in node.body
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)]
    raise AssertionError(f"{name} not found in models.py")


def _class_source(name: str) -> str:
    text = MODELS.read_text(encoding="utf-8")
    body = text[text.index(f"class {name}("):]
    return body[: body.index("\nclass ")] if "\nclass " in body else body


def test_the_episode_request_models_declare_exactly_the_workflows_closed_lists():
    from clipping.aistory import workflow

    assert _class_fields("ScriptPatchRequest") == list(workflow.SCRIPT_PATCH_FIELDS)
    assert _class_fields("ScriptLinePatch") == ["line_id", *workflow.SCRIPT_LINE_PATCH_FIELDS]
    assert _class_fields("ScriptScenePatch") == ["scene_id", *workflow.SCRIPT_SCENE_PATCH_FIELDS]
    assert _class_fields("StoryboardPatchRequest") == list(workflow.STORYBOARD_PATCH_FIELDS)
    assert _class_fields("StoryboardShotPatch") == ["shot_id", *workflow.STORYBOARD_SHOT_PATCH_FIELDS]
    assert _class_fields("StoryboardTransitionPatch") == ["after", *workflow.STORYBOARD_TRANSITION_PATCH_FIELDS]
    # Phase 5, stage 5: "direction" (feedback:<ep> only, required there) joined "approve_anyway".
    assert _class_fields("StoryApproveRequest") == ["approve_anyway", "direction"]
    assert "episode_template_id" in _class_fields("StoryPatchRequest")
    # Default-strict like the other story models: no extra= override, "sent" is model_fields_set.
    for name in ("ScriptPatchRequest", "ScriptLinePatch", "ScriptScenePatch", "StoryboardPatchRequest",
                 "StoryboardShotPatch", "StoryboardTransitionPatch", "StoryApproveRequest"):
        assert "extra" not in _class_source(name), name


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from web.api import store as job_store
    from web.api import worker
    from web.api.routes import jobs, stories

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(worker, "_settings_env", dict(SETTINGS))
    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "PERSIST_PATH", str(tmp_path / "jobs.json"))
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(worker, "OUTPUTS_ROOT", str(outputs))
    monkeypatch.setattr(worker, "UPLOADS_ROOT", str(tmp_path / "uploads"))

    submitted = []

    async def fake_submit(job_id, payload):
        submitted.append(job_id)

    monkeypatch.setattr(worker, "submit_job", fake_submit)
    monkeypatch.setenv("DISABLE_AUTH", "1")

    app = FastAPI()
    app.include_router(stories.router)
    app.include_router(jobs.router)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, jobs=job_store, worker=worker, outputs=outputs, submitted=submitted,
            monkeypatch=monkeypatch, tmp_path=tmp_path, routes=stories,
            store=StoryStore(outputs, on_log=lambda line: None),
        )


def _settings(api, env):
    api.monkeypatch.setattr(api.worker, "_settings_env", dict(env))


def _url(story_id, suffix=""):
    return f"/api/stories/{story_id}{suffix}"


def _post_step(api, story_id, step, ep=None, params=None):
    body = {}
    if ep is not None:
        body["ep"] = ep
    if params is not None:
        body["params"] = params
    return api.client.post(_url(story_id, f"/steps/{step}"), json=body)


def _run(api, job_id, llm):
    """Run a queued step job the way the worker does, its LLM answered by
    *llm*. Returns the job as it ended."""
    job = api.jobs.get_job(job_id)
    step = job["step"]
    module = importlib.import_module(f"clipping.aistory.steps.{step}")
    api.monkeypatch.setitem(steps.RUNNERS, step, lambda ctx: module.run(ctx, runner=llm, time_fn=lambda: 100.0))
    api.worker._execute_story_step(job_id, job, CancelToken())
    return api.jobs.get_job(job_id)


def _job(api, story_id, step, status=None, params=None, ep=None):
    from web.api.models import JobStatus

    job_id = api.jobs.create_job(kind="story_step", story_id=story_id, step=step, ep=ep, params=params)
    if status is not None:
        api.jobs.set_status(job_id, JobStatus(status))
    return job_id


def _story_file(api, story_id):
    return (api.outputs / "stories" / story_id / "story.json").read_bytes()


def _episode(api, story_id, ep=1):
    response = api.client.get(_url(story_id, f"/episodes/{ep}"))
    assert response.status_code == 200, response.text
    return response.json()


def _approve(api, story_id, doc, body=None):
    return api.client.post(_url(story_id, f"/approve/{doc}"), json=body)


def _written(api, e4=E4_PASSED):
    """A ready story whose episode 1 was written by a script job, now
    awaiting approval. ``(story_id, job_id)``."""
    story_id = _ready_story(api.store)
    response = _post_step(api, story_id, "script", ep=1)
    assert response.status_code == 201, response.text
    job = _run(api, response.json()["id"], _script_llm(E4=[e4]))
    assert job["status"] == "awaiting_approval", job.get("error")
    return story_id, job["id"]


# ============================================================= the steps

def test_writing_an_episode_is_a_job_that_the_script_approval_completes(api):
    story_id = _ready_story(api.store)
    before = _story_file(api, story_id)

    response = _post_step(api, story_id, "script", ep=1, params={"measure_voices": False})

    assert response.status_code == 201, response.text
    job = response.json()
    assert (job["step"], job["ep"], job["params"], job["status"]) == (
        "script", 1, {"measure_voices": False}, "queued")
    assert api.submitted == [job["id"]]
    assert _episode(api, story_id)["jobs"][0]["id"] == job["id"]  # in flight: shown on its episode
    assert _run(api, job["id"], _script_llm(E4=[E4_PASSED]))["status"] == "awaiting_approval"
    page = _episode(api, story_id)
    assert page["state"]["script"] == "complete" and page["state"]["report"] == "passed" and page["jobs"] == []

    response = _approve(api, story_id, "script:1")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ep"] == 1 and body["state"]["script"] == "approved" and body["script"]["approved_at"]
    assert api.jobs.get_job(job["id"])["status"] == "completed"
    assert _story_file(api, story_id) == before


@pytest.mark.parametrize("step", ["script", "storyboard"])
def test_an_episode_step_is_refused_before_any_job(api, step):
    story_id = _ready_story(api.store)
    for body, status, needle in (
            ({}, 400, "send its number as ep"),
            ({"ep": 0}, 400, "The season plans episodes 1 to 8; there is no episode 0."),
            ({"ep": 9}, 400, "there is no episode 9"),
            ({"ep": 2}, 409, "Episode 1's series memory is not written yet"),
            ({"ep": 1, "params": {"nope": True}}, 400, f"Unknown {step} parameter(s) nope"),
    ):
        response = api.client.post(_url(story_id, f"/steps/{step}"), json=body)
        assert response.status_code == status, (body, response.text)
        assert needle in response.json()["detail"], (body, response.text)

    api.store.update(story_id, lambda doc: doc["approvals"].update(season=None), now=NOW)
    response = _post_step(api, story_id, step, ep=1)
    assert response.status_code == 409
    assert response.json()["detail"] == "The story is not ready yet: approve the cast, the places and the season first."
    assert api.jobs.list_jobs() == [] and api.submitted == []


def test_the_storyboard_waits_for_a_complete_script(api):
    story_id = _ready_story(api.store)
    assert "has no script yet" in _post_step(api, story_id, "storyboard", ep=1).json()["detail"]
    job = _post_step(api, story_id, "script", ep=1).json()
    queue = [e2_reply, e2_reply, DOWN, e2_reply, e2_reply, e2_reply, e2_reply, e2_reply]
    assert _run(api, job["id"], _script_llm(E2=queue))["status"] == "failed"
    before = len(api.jobs.list_jobs())

    for params in (None, {"fast": True}):
        response = _post_step(api, story_id, "storyboard", ep=1, params=params)
        assert response.status_code == 409, response.text
        assert "not complete" in response.json()["detail"] and "s04" in response.json()["detail"]
    assert len(api.jobs.list_jobs()) == before
    assert _episode(api, story_id)["storyboard"] is None


def test_the_fast_storyboard_runs_here_with_no_call_and_no_job(api):
    from clipping.providers import llm as llm_mod

    story_id, _job_id = _written(api)
    before = _story_file(api, story_id)
    jobs = len(api.jobs.list_jobs())

    def no_call(*args, **kwargs):
        raise AssertionError("the fast storyboard called the LLM chain")

    api.monkeypatch.setattr(llm_mod, "run_chain", no_call)
    response = _post_step(api, story_id, "storyboard", ep=1, params={"fast": True})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ep"] == 1 and body["state"]["storyboard"] == "complete"
    board = body["storyboard"]
    assert {entry["source"] for entry in board["scenes"].values()} == {"fast"}
    assert list(board["scenes"]) == ALL_SCENES
    assert len(api.jobs.list_jobs()) == jobs and len(api.submitted) == 1
    assert _story_file(api, story_id) == before
    activity = (api.outputs / "stories" / story_id / "activity.log").read_text(encoding="utf-8")
    assert "fast, no call" in activity


def test_planning_shots_is_a_t1_job_that_the_storyboard_approval_completes(api):
    story_id, script_job = _written(api)
    assert _approve(api, story_id, "script:1").status_code == 200

    response = _post_step(api, story_id, "storyboard", ep=1, params={"fast": False})

    assert response.status_code == 201, response.text
    job = response.json()
    assert (job["step"], job["ep"]) == ("storyboard", 1)
    ran = _run(api, job["id"], FakeLLM(default={"T1": t1_reply}))
    assert ran["status"] == "awaiting_approval", ran.get("error")
    assert {entry["source"] for entry in _episode(api, story_id)["storyboard"]["scenes"].values()} == {"t1"}

    response = _approve(api, story_id, "storyboard:1")

    assert response.status_code == 200, response.text
    assert response.json()["state"]["storyboard"] == "approved"
    assert api.jobs.get_job(job["id"])["status"] == "completed"
    assert api.jobs.get_job(script_job)["status"] == "completed"


def test_one_step_at_a_time_then_the_key_gate_then_the_queue(api):
    from web.api.models import JobStatus

    story_id, first = _written(api)
    queued = _post_step(api, story_id, "script", ep=1).json()
    # A newer job for the same document supersedes the one awaiting approval.
    assert api.jobs.get_job(first)["superseded_by"] == queued["id"]
    script = api.store.read_episode_doc(story_id, 1, "script.json")

    for method, path, body in (
            ("POST", "/steps/storyboard", {"ep": 1}),
            ("POST", "/steps/storyboard", {"ep": 1, "params": {"fast": True}}),
            ("POST", "/steps/script", {"ep": 1}),
            ("PATCH", "/episodes/1/script", {"next_episode_teaser": "Demain."}),
            ("PATCH", "/episodes/1/storyboard", {"refresh_prompts": True}),
            ("POST", "/regenerate", {"target": "scene:1:s03"}),
            ("POST", "/approve/script:1", None),
            ("POST", "/approve/storyboard:1", None),
            ("PATCH", "", {"episode_template_id": "serial_90s_v1"}),
    ):
        response = api.client.request(method, _url(story_id, path), json=body)
        assert response.status_code == 409, (method, path, response.text)
        assert queued["id"] in response.json()["detail"], (method, path)
    assert api.store.read_episode_doc(story_id, 1, "script.json") == script

    api.jobs.set_status(queued["id"], JobStatus.FAILED)
    _settings(api, {"LLM_CHAIN": "gemini/gemini-test"})
    response = _post_step(api, story_id, "script", ep=1)
    assert response.status_code == 400 and "No link in the LLM chain has an API key" in response.json()["detail"]
    _settings(api, SETTINGS)
    api.monkeypatch.setenv("MAX_QUEUED_JOBS", "1")
    _job(api, _ready_story(api.store), "concepts")  # another story's job fills the queue
    assert _post_step(api, story_id, "storyboard", ep=1).status_code == 429


# ============================================================== approvals

def test_the_script_approval_rules_through_the_api(api):
    story_id, job = _written(api, e4=E4_ISSUES)
    before = _story_file(api, story_id)

    response = _approve(api, story_id, "script:1")
    assert response.status_code == 409
    assert "2 issues" in response.json()["detail"] and "approve anyway" in response.json()["detail"]
    assert api.jobs.get_job(job)["status"] == "awaiting_approval"

    response = _approve(api, story_id, "script:1", {"approve_anyway": True})

    assert response.status_code == 200, response.text
    script = response.json()["script"]
    assert script["approved_at"] and script["approved_anyway"] == script["approved_at"]
    assert api.jobs.get_job(job)["status"] == "completed"
    for doc, body, status in (("storyboard:1", {"approve_anyway": True}, 400), ("bible", {"approve_anyway": True}, 400),
                              ("script:x", None, 400), ("script:9", None, 400), ("script:01", None, 400),
                              ("storyboard:1", None, 409), ("assets:1", None, 409)):
        response = _approve(api, story_id, doc, body)
        assert response.status_code == status, (doc, response.text)
    assert _story_file(api, story_id) == before


def test_the_storyboard_approval_needs_fresh_prompts(api):
    story_id, _job_id = _written(api)
    assert _approve(api, story_id, "script:1").status_code == 200
    assert _post_step(api, story_id, "storyboard", ep=1, params={"fast": True}).status_code == 200
    kiwi = api.store.read_entity(story_id, "characters", KIWILO)
    api.store.write_entity(story_id, "characters", kiwi, now="2026-09-27T12:00:00+00:00")
    assert _episode(api, story_id)["state"]["prompts_outdated"] is True

    response = _approve(api, story_id, "storyboard:1")
    assert response.status_code == 409 and "prompts are outdated: refresh them" in response.json()["detail"]

    response = api.client.patch(_url(story_id, "/episodes/1/storyboard"), json={"refresh_prompts": True})
    assert response.status_code == 200, response.text
    assert response.json()["state"]["prompts_outdated"] is False
    assert _approve(api, story_id, "storyboard:1").status_code == 200


# ================================================================== edits

def test_editing_the_script_through_the_api(api):
    story_id, _job_id = _written(api)
    assert _approve(api, story_id, "script:1").status_code == 200
    before = _story_file(api, story_id)
    url = _url(story_id, "/episodes/1/script")

    response = api.client.patch(url, json={"lines": [{"line_id": "l08", "text": "Tu me trahis déjà ?"}]})

    assert response.status_code == 200, response.text
    body = response.json()
    line = _scene(body["script"], "s02")["lines"][0]
    assert line["text"] == "Tu me trahis déjà ?" and line["timing"]["source"] == "estimated"
    assert body["script"]["approved_at"] is None and body["state"]["report"] == "stale"
    assert body["script"]["rev"] == 2

    response = api.client.patch(url, json={"lines": [{"line_id": "l08", "speaker": BROCCOLIA},
                                                     {"line_id": "l09", "text": " ".join(["mot"] * 23)}]})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["message"] == "The script would not be valid with these values."
    assert any("char_broccolia" in error for error in detail["errors"])
    assert any("23 words" in error for error in detail["errors"])
    assert api.client.patch(url, json={}).json()["script"]["rev"] == 2
    assert api.client.patch(url, json={"lines": [{"text": "sans id"}]}).status_code == 422
    assert api.client.patch(_url(story_id, "/episodes/9/script"), json={"next_episode_teaser": "x"}).status_code == 400
    assert api.client.patch(_url(story_id, "/episodes/2/script"), json={"next_episode_teaser": "x"}).status_code == 409
    assert _story_file(api, story_id) == before


def test_editing_the_storyboard_through_the_api(api):
    story_id, _job_id = _written(api)
    before = _story_file(api, story_id)
    board = _post_step(api, story_id, "storyboard", ep=1, params={"fast": True}).json()["storyboard"]
    shot = next(s for s in board["shots"] if s["scene_id"] == "s02" and s["framing"] != "wide_establishing")
    url = _url(story_id, "/episodes/1/storyboard")

    response = api.client.patch(url, json={"shots": [{"shot_id": shot["shot_id"],
                                                      "action": f"Kiwilo whispers to @{MANGELLA}."}]})
    assert response.status_code == 400
    assert any("names the character 'Kiwilo'" in error for error in response.json()["detail"]["errors"])

    framing = "low_angle" if shot["framing"] != "low_angle" else "high_angle"
    response = api.client.patch(url, json={"shots": [{"shot_id": shot["shot_id"], "framing": framing}]})

    assert response.status_code == 200, response.text
    after = response.json()["storyboard"]
    assert after["rev"] == board["rev"] + 1
    assert [s for s in after["shots"] if s["shot_id"] != shot["shot_id"]] == [
        s for s in board["shots"] if s["shot_id"] != shot["shot_id"]]
    assert _story_file(api, story_id) == before


def test_the_episode_length_is_a_story_edit_until_an_episode_is_written(api):
    story_id = _ready_story(api.store)
    response = api.client.patch(_url(story_id), json={"episode_template_id": "serial_90s_v1"})
    assert response.status_code == 200 and response.json()["episode_template_id"] == "serial_90s_v1"
    assert response.json()["status"] == "ready"
    assert api.client.patch(_url(story_id), json={"episode_template_id": "serial_45s_v1"}).status_code == 400
    assert api.client.patch(_url(story_id), json={"episode_template_id": "serial_60s_v1"}).status_code == 200

    job = _post_step(api, story_id, "script", ep=1).json()
    assert _run(api, job["id"], _script_llm())["status"] == "awaiting_approval"
    response = api.client.patch(_url(story_id), json={"episode_template_id": "serial_90s_v1"})
    assert response.status_code == 409 and "episode 1 has a script" in response.json()["detail"]


# ============================================================= regenerate

def test_each_episode_step_and_target_is_a_job_of_its_document(api):
    doc = api.routes._job_doc
    assert doc("script", {}, ep=1) == "script:1" and doc("storyboard", {"fast": False}, ep=3) == "storyboard:3"
    assert doc("script", {}) is None and doc("cast", {}) == "cast"
    for target, expected in (("scene:1:s03", "script:1"), ("hook:2", "script:2"), ("cliffhanger:1", "script:1"),
                             ("teaser:1", "script:1"), ("shot:1:sh02:plan", "storyboard:1"),
                             ("shot:1:sh02", "assets:1"), ("line:1:l04", "assets:1"), ("season:2", "season")):
        assert doc("regenerate", {"target": target}) == expected, target


def test_regenerating_a_scene_or_a_shot_reaches_its_runner_as_a_job_of_its_document(api):
    story_id, script_job = _written(api)
    before = _story_file(api, story_id)

    response = api.client.post(_url(story_id, "/regenerate"), json={"target": "scene:1:s03", "note": "plus sec"})

    assert response.status_code == 201, response.text
    job = response.json()
    assert job["step"] == "regenerate" and job["params"] == {"target": "scene:1:s03", "note": "plus sec",
                                                              "voice": None}
    assert api.jobs.get_job(script_job)["superseded_by"] == job["id"]
    llm = FakeLLM(E2=[e2_reply])
    assert _run(api, job["id"], llm)["status"] == "awaiting_approval"
    assert llm.prompts() == ["E2"] and "plus sec" in llm.of("E2")[0]["user"]
    assert _scene(_episode(api, story_id)["script"], "s03")["rev"] == 2

    for body, status, needle in (
            ({"target": "scene:1:s42"}, 404, "has no scene 's42'"),
            ({"target": "shot:1:sh01:plan"}, 409, "has no storyboard yet"),
            ({"target": "hook:1", "voice": {"provider": "edge", "voice_id": "x"}}, 400, "voice"),
            ({"target": "scene:9:s03"}, 400, "there is no episode 9"),
            ({"target": "teaser:2"}, 409, "Episode 2 has no script yet"),
            ({"target": "shot:1:sh01:frames"}, 400, "later phase"),
    ):
        response = api.client.post(_url(story_id, "/regenerate"), json=body)
        assert response.status_code == status, (body, response.text)
        assert needle in response.json()["detail"], (body, response.text)

    assert _post_step(api, story_id, "storyboard", ep=1, params={"fast": True}).status_code == 200
    response = api.client.post(_url(story_id, "/regenerate"), json={"target": "shot:1:sh01:plan"})
    assert response.status_code == 201, response.text
    assert api.routes._job_doc("regenerate", response.json()["params"]) == "storyboard:1"
    llm = FakeLLM(T1r=[t1r_reply])
    assert _run(api, response.json()["id"], llm)["status"] == "awaiting_approval"
    assert llm.prompts() == ["T1r"]
    assert _story_file(api, story_id) == before


# ============================================================== estimates

def _estimate(api, story_id, step, **params):
    response = api.client.get(_url(story_id, f"/estimate/{step}"), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_script_estimate_counts_only_what_is_missing(api):
    story_id = _ready_story(api.store)

    body = _estimate(api, story_id, "script", ep=1)

    assert body["step"] == "script" and body["ep"] == 1 and body["est_usd"] == 0.0
    # llm_calls (and the chip's units.llm_calls) is the count E1's exact ask
    # makes -- 1 + 8 body + 1 + 1, the sum of calls_breakdown; the range
    # keeps the worst case a legal beat sheet can reach (9 body scenes), and
    # the message names that range.
    assert body["llm_calls"] == 11 and body["units"]["llm_calls"] == 11
    assert body["llm_calls"] == sum(body["calls_breakdown"].values())
    assert body["llm_calls_range"] == [8, 12]
    assert body["calls_breakdown"] == {"E1": 1, "E2": 8, "E3": 1, "E4": 1}
    assert body["link"] == "gemini/gemini-test" and body["skipped_paid"] == [] and body["ready"] is True
    assert body["measure"] is None and "8–12 LLM calls" in body["message"]
    assert _estimate(api, story_id, "script", ep=1, measure=1)["measure"]["lines"] == 0
    assert api.client.get(_url(story_id, "/estimate/script")).status_code == 400
    response = api.client.get(_url(story_id, "/estimate/script"), params={"ep": 2})
    assert response.status_code == 409 and "Episode 1's series memory is not written yet" in response.json()["detail"]

    job = _post_step(api, story_id, "script", ep=1).json()
    _run(api, job["id"], _script_llm(E4=[E4_PASSED]))
    body = _estimate(api, story_id, "script", ep=1, measure="true")
    assert body["llm_calls"] == 0 and body["calls_breakdown"] == {"E1": 0, "E2": 0, "E3": 0, "E4": 0}
    script = _episode(api, story_id)["script"]
    lines = [line for scene in script["scenes"] for line in scene["lines"]]
    assert body["measure"]["lines"] == len(lines) and body["measure"]["chars"] == sum(len(l["text"]) for l in lines)

    _settings(api, {"LLM_CHAIN": "gemini/gemini-test,openrouter/test-model", "GOOGLE_API_KEY": "test-gemini-key",
                    "OPENROUTER_API_KEY": "test-openrouter-key"})
    skipped = _estimate(api, story_id, "script", ep=1)["skipped_paid"]
    assert [row["link"] for row in skipped] == ["openrouter/test-model"]
    assert "allow_paid is off" in skipped[0]["reason"]


def test_the_storyboard_estimate_counts_the_scenes_t1_would_plan(api):
    story_id = _ready_story(api.store)
    body = _estimate(api, story_id, "storyboard", ep=1)
    assert body["t1_calls"] == 0 and body["fast_calls"] == 0 and body["ready"] is False
    assert "has no script yet" in body["message"]

    job = _post_step(api, story_id, "script", ep=1).json()
    _run(api, job["id"], _script_llm(E4=[E4_PASSED]))
    body = _estimate(api, story_id, "storyboard", ep=1)
    assert body["t1_calls"] == len(ALL_SCENES) and body["fast_calls"] == 0 and body["ready"] is True
    assert body["link"] == "gemini/gemini-test" and body["skipped_paid"] == [] and body["est_usd"] == 0.0


# ================================================================== pages

def test_the_episode_page_and_the_story_pages_summary(api):
    story_id = _ready_story(api.store)
    page = _episode(api, story_id)
    assert page == {
        "ep": 1, "script": None, "storyboard": None,
        "template": {"id": "serial_60s_v1", "window_s": [55, 80], "target_s": 60, "tighten_above_s": 75},
        # F8, phase 5 stage 13b: no script yet -- neither regenerate control
        # is shown, so both read null rather than a precondition sentence.
        "state": {"script": "none", "storyboard": "none", "report": "none", "stale_scenes": [],
                  "prompts_outdated": False, "missing": ["beat_sheet"],
                  "assets_regenerate_blocked": None, "metadata_regenerate_blocked": None},
        "assets": None, "render": None, "metadata": None,
        "ledger": {"entries": [], "totals": {"est_usd": 0.0, "paid_usd": 0.0, "entries": 0}},
        # Phase 7 follow-up stage C, re-pinned on purpose: the review block (workflow.episode_review),
        # null before the episode has a storyboard to review.
        "review": None,
        # Phase 5, stage 5: workflow.series_page's own fields (no memory, feedback or proposals yet;
        # the gate blocking episode 2 until episode 1's script is approved and its memory written).
        # F5, stage 13b: no propose-next job on record for this episode either -- proposals_approved
        # is False, not an error, exactly like proposals itself being None.
        "series": {"ep": 1, "memory": {"state": "none", "entry": None}, "feedback": None, "proposals": None,
                  "proposals_approved": False,
                  "next_episode_gate": ("Episode 1's series memory is not written yet: approve episode 1's "
                                        "script, then run memory for episode 1 and approve it, before writing "
                                        "episode 2.")},
        "jobs": [],
    }
    for ep, status in (("9", 400), ("0", 400), ("x", 400), ("01", 400)):
        assert api.client.get(_url(story_id, f"/episodes/{ep}")).status_code == status, ep
    assert api.client.get(_url(UNKNOWN_ID, "/episodes/1")).status_code == 404
    assert api.client.get(_url(story_id)).json()["episodes"] == []

    job = _post_step(api, story_id, "script", ep=1).json()
    _run(api, job["id"], _script_llm(E4=[E4_PASSED]))
    script = _episode(api, story_id)["script"]
    assert api.client.get(_url(story_id)).json()["episodes"] == [{
        "ep": 1, "title": script["title"], "script_state": "complete", "storyboard_state": "none",
        "total_s": script["timing"]["total_s"], "timing_state": script["timing"]["state"]}]


# ================================================================ line audio

def test_the_voice_route_serves_a_lines_take_and_nothing_else(api, tmp_path):
    story_id = _ready_story(api.store)
    other = _ready_story(api.store)
    mp3 = Path(api.store.episode_asset_path(story_id, 1, "voice", "line_08.mp3", create=True))
    mp3.write_bytes(b"ID3 take of l08")
    Path(api.store.episode_asset_path(story_id, 1, "voice", "line_09.wav", create=True)).write_bytes(b"RIFF l09")
    Path(api.store.episode_asset_path(story_id, 1, "voice", "line_08.json", create=True)).write_text("{}")
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"not a take")
    os.symlink(outside, mp3.parent / "line_10.mp3")

    response = api.client.get(_url(story_id, "/episodes/1/voice/line_08.mp3"))
    assert response.status_code == 200 and response.content == b"ID3 take of l08"
    assert response.headers["content-type"] == "audio/mpeg" and response.headers["cache-control"] == "no-store"
    response = api.client.get(_url(story_id, "/episodes/1/voice/line_09.wav"))
    assert response.status_code == 200 and response.headers["content-type"] == "audio/wav"

    for path in ("/episodes/1/voice/line_1.mp3", "/episodes/1/voice/line_08.json", "/episodes/1/voice/line_08.MP3",
                 "/episodes/1/voice/line_07.mp3", "/episodes/1/voice/line_10.mp3", "/episodes/1/voice/..%2Fstory.json",
                 "/episodes/0/voice/line_08.mp3", "/episodes/01/voice/line_08.mp3",
                 "/episodes/x/voice/line_08.mp3"):
        assert api.client.get(_url(story_id, path)).status_code == 404, path
    assert api.client.get(_url(other, "/episodes/1/voice/line_08.mp3")).status_code == 404
    assert api.client.get(_url("ABC", "/episodes/1/voice/line_08.mp3")).status_code == 404


# ================================================================== token

NEW_ROUTES = [
    ("POST", "/steps/script", {"ep": 1}),
    ("POST", "/steps/storyboard", {"ep": 1, "params": {"fast": True}}),
    ("GET", "/episodes/1", None),
    ("PATCH", "/episodes/1/script", {"next_episode_teaser": "Demain."}),
    ("PATCH", "/episodes/1/storyboard", {"refresh_prompts": True}),
    ("POST", "/approve/script:1", {"approve_anyway": True}),
    ("POST", "/approve/storyboard:1", None),
    ("POST", "/regenerate", {"target": "scene:1:s03"}),
    ("GET", "/estimate/script?ep=1&measure=1", None),
    ("GET", "/estimate/storyboard?ep=1", None),
    ("GET", "/episodes/1/voice/line_08.mp3", None),
]


def test_the_episode_routes_are_under_the_routers_token(api):
    from web.api.auth import require_token

    paths = {route.path for route in api.routes.router.routes}
    for path in ("/api/stories/{story_id}/episodes/{ep}", "/api/stories/{story_id}/episodes/{ep}/script",
                 "/api/stories/{story_id}/episodes/{ep}/storyboard",
                 "/api/stories/{story_id}/episodes/{ep}/voice/{name}"):
        assert path in paths
    for route in api.routes.router.routes:
        assert any(dep.call is require_token for dep in route.dependant.dependencies), route.path


def test_the_episode_routes_refuse_a_request_without_the_token_before_reading_it(api, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from web.api import auth

    story_id, _job_id = _written(api)
    Path(api.store.episode_asset_path(story_id, 1, "voice", "line_08.mp3", create=True)).write_bytes(b"ID3")
    script = api.store.read_episode_doc(story_id, 1, "script.json")
    jobs = len(api.jobs.list_jobs())
    monkeypatch.setenv("API_TOKEN", "test-token-12345")
    monkeypatch.delenv("DISABLE_AUTH", raising=False)
    monkeypatch.setattr(auth, "_TOKEN", None)
    app = FastAPI()
    app.include_router(api.routes.router)
    with TestClient(app) as client:
        for method, path, body in NEW_ROUTES:
            response = client.request(method, _url(story_id, path), json=body)
            assert response.status_code == 401, (method, path)
        headers = {"Authorization": "Bearer test-token-12345"}
        assert client.get(_url(story_id, "/episodes/1/voice/line_08.mp3"), headers=headers).status_code == 200
    assert api.store.read_episode_doc(story_id, 1, "script.json") == script
    assert api.store.read_episode_doc(story_id, 1, "storyboard.json") is None
    assert len(api.jobs.list_jobs()) == jobs
    monkeypatch.setattr(auth, "_TOKEN", None)
