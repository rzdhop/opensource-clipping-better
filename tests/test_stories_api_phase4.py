"""The AI Story API, steps 10-12 and the fast track: assets, render, metadata
(phase 4, stage 11; spec 3, 9.1, 9.2; DEC-140, DEC-155, DEC-161, DEC-162).

``web/api/routes/stories.py`` on a throwaway app -- the stories router and the
jobs router, never the singleton ``web.api.app.app`` -- with the job store on
an empty table under ``tmp_path``, ``worker.OUTPUTS_ROOT`` under ``tmp_path``
and ``worker.submit_job`` replaced by a recorder, as phase 3's API tests have
it. The episode is stage 8's French fixture (``tests/test_story_assets_step.py``:
written, planned fast, script and storyboard approved), then -- built once per
session and copied per test -- its assets made by the fake image adapter and
the fake Edge voice, approved, rendered with stage 7's fake ffmpeg
(``tests/test_story_render_step.py``) and given its metadata pack
(``tests/test_story_metadata_step.py``). A job that has to *run* goes through
the real worker path (``worker._execute_story_step``) with its handler faked:
no network, no real key (the Settings values are test values).

Covered: the regenerate grammar (``shot:<ep>:<shid>:plan`` vs the shot's image
``shot:<ep>:<shid>`` -- its clip ``shot:<ep>:<shid>:video`` is phase 6's
(``tests/test_story_video_phase.py``); any other ``shot:<ep>:<shid>:<word>``,
``:frames`` among them, is still a later phase's; ``line:``/``metadata:``
reaching their handlers), the four step routes (their
parameters, preconditions, ep bounds, key gate, one step per story, queue
cap -- each refusal before any job), the assets approval (what is missing is
named; the jobs it completes), a fast track's end (completed, or stopped)
completing the older jobs whose documents it approved, the shot lock (PATCH,
and a locked regenerate refused), the estimates' shapes, the shot image route,
and the episode page's phase-4 sections with the render's out-of-date answer
remembered between two polls. ``story.json`` is never changed (RC-E2).

The grammar and the request models' text guards are stdlib (the CI
environment); everything else needs pydantic, fastapi and httpx and skips
without them, like the other route tests. The routes are reached through the
client only, so on the parent commit (``c0e3f1f``) each test fails on its own.
"""

from __future__ import annotations

import ast
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_metadata_step as tms
import test_story_render_step as trs
from clipping.aistory import steps
from clipping.cancel import CancelToken
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used here as they are

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "web" / "api" / "models.py"
NOW = eps.NOW
UNKNOWN_ID = "0123456789ab"
NOT_READY = "The story is not ready yet: approve the cast, the places and the season first."
PHASE4_STEPS = ("assets", "render", "metadata", "fast-track")


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


def test_the_phase_4_request_models_declare_exactly_the_workflows_closed_lists():
    from clipping.aistory import workflow

    assert workflow.ASSETS_PARAMS == ("align_words", "animate")
    assert workflow.RENDER_PARAMS == ("subtitles", "encoder")
    assert workflow.METADATA_PARAMS == ()
    assert workflow.FAST_TRACK_PARAMS == ("storyboard",)
    assert _class_fields("AssetsStepParams") == list(workflow.ASSETS_PARAMS)
    assert _class_fields("RenderStepParams") == list(workflow.RENDER_PARAMS)
    assert _class_fields("FastTrackStepParams") == list(workflow.FAST_TRACK_PARAMS)
    assert _class_fields("AssetsPatchRequest") == list(workflow.ASSETS_PATCH_FIELDS) == ["shots"]
    assert _class_fields("AssetsShotPatch") == ["shot_id", *workflow.ASSETS_SHOT_PATCH_FIELDS]
    assert workflow.ASSETS_SHOT_PATCH_FIELDS == ("locked",)
    # Default-strict like the other story models: no extra= override, "sent" is model_fields_set.
    for name in ("AssetsStepParams", "RenderStepParams", "FastTrackStepParams", "AssetsPatchRequest",
                 "AssetsShotPatch"):
        assert "extra" not in _class_source(name), name


# ------------------------------------------------------------------ grammar (CI)

def test_the_phase_4_steps_approvals_and_targets_left_the_later_phases():
    from clipping.aistory import workflow
    from clipping.aistory.steps import regenerate

    assert workflow.PHASE4_STEPS == PHASE4_STEPS
    # Phase 5 stage 4 registered memory, feedback and propose-next, stage 8 rerender: import is still later.
    assert workflow.LATER_STEPS == ("import",)
    assert not set(workflow.PHASE4_STEPS) & set(workflow.LATER_STEPS)
    assert workflow.LATER_APPROVALS == () and workflow.LATER_APPROVALS_BARE == ()
    assert not workflow.is_later_approval("assets:1")
    # DEC-140: only the shot's video is still a later phase's; the rest of shot: is read first.
    assert workflow.LATER_TARGETS == ("shot",)
    assert {"shot_image", "line", "metadata"} <= set(regenerate.EPISODE_KINDS)
    assert set(regenerate.EPISODE_TARGETS) <= set(regenerate.ENTITY_TARGETS)
    for shape in ("shot:<ep>:<shot_id>:plan", "shot:<ep>:<shot_id>", "line:<ep>:<line_id>",
                  "metadata:<ep>:tiktok|shorts|reels"):
        assert shape in regenerate.EPISODE_TARGETS, shape


@pytest.mark.parametrize("target, parsed", [
    ("shot:1:sh05:plan", ("shot", 1, "sh05")),
    ("shot:1:sh05", ("shot_image", 1, "sh05")),
    ("shot:12:sh40", ("shot_image", 12, "sh40")),
    ("line:2:l04", ("line", 2, "l04")),
    ("metadata:3:tiktok", ("metadata", 3, "tiktok")),
    ("metadata:1:shorts", ("metadata", 1, "shorts")),
    ("metadata:1:reels", ("metadata", 1, "reels")),
])
def test_a_shots_plan_its_image_a_line_and_a_platform_are_targets(target, parsed):
    from clipping.aistory import workflow
    from clipping.aistory.steps import regenerate

    assert regenerate.parse_target(target) == parsed
    assert regenerate.parse_episode_target(target) == parsed
    assert workflow.check_regenerate_target(target) is None


@pytest.mark.parametrize("target", ["shot:1:sh05:frames"])
def test_any_other_shot_word_such_as_frames_is_still_a_later_phase(target):
    from clipping.aistory import workflow
    from clipping.aistory.steps import regenerate

    assert regenerate.parse_target(target) is None
    with pytest.raises(workflow.WorkflowError) as caught:
        workflow.check_regenerate_target(target)
    assert caught.value.code == "later_phase" and "later phase" in caught.value.detail


@pytest.mark.parametrize("target", [
    "line:1:l4", "line:0:l04", "line:1", "line:1:l04:x", "line:1:s04",
    "metadata:1:facebook", "metadata:1", "metadata:1:tiktok:x", "metadata:0:tiktok", "metadata:1:TikTok",
])
def test_a_malformed_line_or_platform_target_is_invalid_naming_the_shapes(target):
    from clipping.aistory import workflow

    with pytest.raises(workflow.WorkflowError) as caught:
        workflow.check_regenerate_target(target)
    assert caught.value.code == "invalid"
    for shape in ("shot:<ep>:<shot_id>:plan", "shot:<ep>:<shot_id>", "line:<ep>:<line_id>",
                  "metadata:<ep>:tiktok|shorts|reels"):
        assert shape in caught.value.detail, shape


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
    monkeypatch.setenv("DISABLE_AUTH", "1")

    app = FastAPI()
    app.include_router(stories.router)
    app.include_router(jobs.router)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, jobs=job_store, worker=worker, outputs=outputs, submitted=submitted,
            monkeypatch=monkeypatch, tmp_path=tmp_path, routes=stories, store=store,
        )


@pytest.fixture(scope="session")
def episodes(tmp_path_factory):
    """``{kind: (outputs copy, story_id)}``, filled by the first test that
    needs each kind of episode (each kind is built from the one before)."""
    return {"root": tmp_path_factory.mktemp("api_phase4_episodes"), "kinds": {}}


def _make_assets(api, story_id):
    summary, _log = tas._run(api.store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    assert summary["complete"] is True


def _approve_assets(api, story_id):
    from clipping.aistory import workflow

    workflow.approve_assets(api.store, story_id, 1, now=NOW)


def _render(api, story_id):
    trs.render(api.store, story_id, tmp_path=api.tmp_path)


def _publish(api, story_id):
    tms.run_step(api.store, story_id, tmp_path=api.tmp_path)


# planned: script and storyboard approved; made: its assets made; approved:
# them approved; rendered: episode_final.mp4 and its manifest; published: the
# metadata pack and the cover.
KINDS = ("planned", "made", "approved", "rendered", "published")
BUILD = {"made": _make_assets, "approved": _approve_assets, "rendered": _render, "published": _publish}


def episode(api, episodes, kind):
    """A copy of the session's *kind* episode in this test's ``outputs/``;
    its story id."""
    if kind in episodes["kinds"]:
        copy, story_id = episodes["kinds"][kind]
        shutil.copytree(copy, api.store.outputs_dir, symlinks=True, dirs_exist_ok=True)
        return story_id
    index = KINDS.index(kind)
    if index == 0:
        story_id = tas._episode(api.store, api.tmp_path)
    else:
        story_id = episode(api, episodes, KINDS[index - 1])
        BUILD[kind](api, story_id)
    copy = episodes["root"] / kind
    shutil.copytree(api.store.outputs_dir, copy, symlinks=True)
    episodes["kinds"][kind] = (copy, story_id)
    return story_id


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


def _job(api, story_id, step, status=None, params=None, ep=None):
    from web.api.models import JobStatus

    job_id = api.jobs.create_job(kind="story_step", story_id=story_id, step=step, ep=ep, params=params)
    if status is not None:
        api.jobs.set_status(job_id, JobStatus(status))
    return job_id


def _status(api, job_id):
    return api.jobs.get_job(job_id)["status"]


def _story_file(api, story_id):
    return (api.outputs / "stories" / story_id / "story.json").read_bytes()


def _episode(api, story_id, ep=1):
    response = api.client.get(_url(story_id, f"/episodes/{ep}"))
    assert response.status_code == 200, response.text
    return response.json()


def _approve(api, story_id, doc):
    return api.client.post(_url(story_id, f"/approve/{doc}"))


def _regenerate(api, story_id, target, **body):
    return api.client.post(_url(story_id, "/regenerate"), json={"target": target, **body})


def _board(api, story_id):
    return api.store.read_episode_doc(story_id, 1, "storyboard.json")


def _doc(api, story_id, name):
    return api.store.read_episode_doc(story_id, 1, name)


def _ep_dir(api, story_id) -> Path:
    return Path(api.store.story_dir(story_id)) / "episodes" / "ep01"


def _image(api, story_id, shot_id) -> Path:
    shot = next(s for s in _board(api, story_id)["shots"] if s["shot_id"] == shot_id)
    return _ep_dir(api, story_id) / shot["assets"]["image"]


def _run(api, job_id):
    """Run a queued step job the way the worker does (its runner as registered
    now); returns the job as it ended."""
    job = api.jobs.get_job(job_id)
    api.worker._execute_story_step(job_id, job, CancelToken())
    return api.jobs.get_job(job_id)


# ============================================================ jobs and documents

def test_each_phase_4_step_and_target_is_a_job_of_its_document(api):
    doc = api.routes._job_doc
    assert doc("assets", {}, ep=1) == "assets:1" and doc("assets", {"align_words": True}, ep=4) == "assets:4"
    assert doc("assets", {}) is None
    for step in ("render", "metadata", "fast-track"):
        assert doc(step, {}, ep=1) is None, step
    for target, expected in (("shot:1:sh02:plan", "storyboard:1"), ("shot:1:sh02", "assets:1"),
                             ("line:2:l04", "assets:2"), ("metadata:1:reels", None), ("shot:1:sh02:video", "assets:1"),
                             ("scene:1:s03", "script:1")):
        assert doc("regenerate", {"target": target}) == expected, target


# ================================================================ the steps

def test_each_step_queues_one_job_of_its_episode_with_its_params(api, episodes):
    from web.api.models import JobStatus

    story_id = episode(api, episodes, "rendered")
    before = _story_file(api, story_id)
    for step, params in (("assets", {"align_words": True}), ("render", {"subtitles": "two_line", "encoder": "libx264"}),
                         ("metadata", None), ("fast-track", {"storyboard": "fast"})):
        response = _post_step(api, story_id, step, ep=1, params=params)
        assert response.status_code == 201, (step, response.text)
        job = response.json()
        assert (job["step"], job["ep"], job["params"], job["status"]) == (step, 1, params or {}, "queued")
        assert api.submitted[-1] == job["id"]
        assert [j["id"] for j in _episode(api, story_id)["jobs"]] == [job["id"]], step  # shown on its episode
        api.jobs.set_status(job["id"], JobStatus.FAILED)
    assert _story_file(api, story_id) == before


@pytest.mark.parametrize("step", PHASE4_STEPS)
def test_a_step_is_refused_before_any_job_outside_its_episode(api, episodes, step):
    story_id = episode(api, episodes, "rendered")
    # Plan 11 stage 4: of these four, only the fast track meets the memory gate (it would write episode 2's
    # script); the assets, the render and the metadata are refused by what they are made from.
    ep2 = "Episode 1's series memory is not written yet" if step == "fast-track" else "Episode 2 has no script yet"
    for body, status, needle in (
            ({}, 400, "send its number as ep"),
            ({"ep": 0}, 400, "The season plans episodes 1 to 8; there is no episode 0."),
            ({"ep": 9}, 400, "there is no episode 9"),
            ({"ep": 2}, 409, ep2),
    ):
        response = api.client.post(_url(story_id, f"/steps/{step}"), json=body)
        assert response.status_code == status, (body, response.text)
        assert needle in response.json()["detail"], (body, response.text)

    api.store.update(story_id, lambda doc: doc["approvals"].update(season=None), now=NOW)
    response = _post_step(api, story_id, step, ep=1)
    assert response.status_code == 409 and response.json()["detail"] == NOT_READY
    assert api.jobs.list_jobs() == [] and api.submitted == []


@pytest.mark.parametrize("step, params, needle", [
    ("assets", {"nope": 1}, "Unknown assets parameter(s) nope (known: align_words, animate)."),
    ("assets", {"align_words": "yes"}, "params.align_words is true or false, not 'yes'."),
    ("render", {"nope": 1}, "Unknown render parameter(s) nope (known: subtitles, encoder)."),
    ("render", {"subtitles": "karaoke"},
     "The subtitles must be one of style, word_pop, two_line, none, not 'karaoke'."),
    ("render", {"encoder": "nvenc"}, "The encoder must be one of libx264, auto, not 'nvenc'."),
    ("metadata", {"platforms": ["tiktok"]}, "'metadata' takes no parameters."),
    ("fast-track", {"nope": 1}, "Unknown fast-track parameter(s) nope (known: storyboard)."),
    ("fast-track", {"storyboard": "slow"}, "The fast track's storyboard is one of t1, fast, not 'slow'."),
])
def test_a_steps_params_are_its_closed_list(api, episodes, step, params, needle):
    story_id = episode(api, episodes, "rendered")
    response = _post_step(api, story_id, step, ep=1, params=params)
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == needle
    assert api.jobs.list_jobs() == []


@pytest.mark.parametrize("kind, step, needle", [
    ("planned", "render", "Episode 1 has no assets yet: make them (the assets step), approve them, then render."),
    ("made", "render", "Approve episode 1's assets first"),
    ("approved", "metadata", "Episode 1 is not rendered yet: render it first (the render step)."),
])
def test_a_step_waits_for_what_it_is_made_from(api, episodes, kind, step, needle):
    story_id = episode(api, episodes, kind)
    response = _post_step(api, story_id, step, ep=1)
    assert response.status_code == 409, response.text
    assert needle in response.json()["detail"]
    assert api.jobs.list_jobs() == []


def test_the_assets_wait_for_an_approved_storyboard_and_a_changed_image_blocks_the_render(api, episodes):
    story_id = episode(api, episodes, "approved")
    _image(api, story_id, "sh02").write_bytes(b"\x89PNG another picture")
    response = _post_step(api, story_id, "render", ep=1)
    assert response.status_code == 409 and "changed since they were approved" in response.json()["detail"]

    board = _board(api, story_id)
    board["approved_at"] = None
    api.store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    response = _post_step(api, story_id, "assets", ep=1)
    assert response.status_code == 409
    assert response.json()["detail"].startswith("Approve episode 1's storyboard first")
    assert api.jobs.list_jobs() == []


def test_one_step_at_a_time_then_the_gates_then_the_queue(api, episodes):
    from web.api.models import JobStatus

    story_id = episode(api, episodes, "rendered")
    queued = _post_step(api, story_id, "render", ep=1).json()
    board = _board(api, story_id)
    for method, path, body in (
            ("POST", "/steps/assets", {"ep": 1}),
            ("POST", "/steps/render", {"ep": 1}),
            ("POST", "/steps/metadata", {"ep": 1}),
            ("POST", "/steps/fast-track", {"ep": 1}),
            ("PATCH", "/episodes/1/assets", {"shots": [{"shot_id": "sh02", "locked": True}]}),
            ("POST", "/regenerate", {"target": "shot:1:sh02"}),
            ("POST", "/regenerate", {"target": "line:1:l04"}),
            ("POST", "/regenerate", {"target": "metadata:1:tiktok"}),
            ("POST", "/approve/assets:1", None),
            ("POST", "/approve/script:1", None),
            ("POST", "/approve/storyboard:1", None),
    ):
        response = api.client.request(method, _url(story_id, path), json=body)
        assert response.status_code == 409, (method, path, response.text)
        assert queued["id"] in response.json()["detail"], (method, path)
    assert _board(api, story_id) == board

    api.jobs.set_status(queued["id"], JobStatus.FAILED)
    # The LLM steps meet the key gate; the assets and the render call no LLM.
    _settings(api, {**tas._settings(), "GOOGLE_API_KEY": ""})
    for step in ("metadata", "fast-track"):
        response = _post_step(api, story_id, step, ep=1)
        assert response.status_code == 400 and "No link in the LLM chain has an API key" in response.json()["detail"]
    response = _post_step(api, story_id, "render", ep=1)
    assert response.status_code == 201, response.text
    api.jobs.set_status(response.json()["id"], JobStatus.FAILED)

    _settings(api, tas._settings())
    api.monkeypatch.setenv("MAX_QUEUED_JOBS", "1")
    _job(api, eps._ready_story(api.store), "concepts")  # another story's job fills the queue
    for step in PHASE4_STEPS:
        assert _post_step(api, story_id, step, ep=1).status_code == 429, step


def test_the_assets_gate_refuses_a_plan_that_cannot_run_before_any_job(api, episodes):
    story_id = episode(api, episodes, "planned")
    _settings(api, {**tas._settings(), "IMAGE_CHAIN": "fal/flux-schnell"})  # paid, no key, allow_paid off
    response = _post_step(api, story_id, "assets", ep=1)
    assert response.status_code == 409, response.text
    assert "Episode 1's shot images cannot be made" in response.json()["detail"]
    assert "Nothing was generated or spent" in response.json()["detail"]
    assert api.jobs.list_jobs() == []


def test_an_assets_job_supersedes_the_one_awaiting_and_the_approval_completes_the_new_one(api, episodes):
    story_id = episode(api, episodes, "made")
    older = _job(api, story_id, "assets", status="awaiting_approval", ep=1)
    response = _post_step(api, story_id, "assets", ep=1)
    assert response.status_code == 201, response.text
    job = response.json()
    assert api.jobs.get_job(older)["superseded_by"] == job["id"]

    from clipping.aistory.steps import assets

    image = tas.FakeImage()

    def run_assets(ctx):
        return assets.run(ctx, adapters=tas._adapters(image=image), time_fn=eps.Clock(0.0), sleep_fn=lambda _s: None)

    api.monkeypatch.setitem(steps.RUNNERS, "assets", run_assets)
    assert _run(api, job["id"])["status"] == "awaiting_approval"
    assert image.requests == []  # a complete re-run makes no call

    response = _approve(api, story_id, "assets:1")
    assert response.status_code == 200, response.text
    assert _status(api, job["id"]) == "completed"


# ============================================================== regenerate

def test_a_shot_image_a_line_and_a_platform_reach_their_handlers_as_jobs(api, episodes):
    from clipping.aistory.steps import assets, metadata

    story_id = episode(api, episodes, "rendered")
    before = _story_file(api, story_id)
    calls = []

    def recorder(kind, answer):
        def handler(ctx, ec, target, what, note, *, tools, refuse):
            calls.append((kind, ctx.story_id, ec.ep, target, what, note))
            return dict(answer, target=target)
        return handler

    api.monkeypatch.setattr(assets, "regenerate_shot_image", recorder("shot_image", {"shot": "sh02"}))
    api.monkeypatch.setattr(assets, "regenerate_line_voice", recorder("line", {"line": "l04"}))
    api.monkeypatch.setattr(metadata, "regenerate_platform", recorder("metadata", {"platform": "reels"}))

    ended = {}
    for target, note, kind, what, status in (("shot:1:sh02", "plus sombre", "shot_image", "sh02", "awaiting_approval"),
                                             ("line:1:l04", None, "line", "l04", "awaiting_approval"),
                                             ("metadata:1:reels", "court", "metadata", "reels", "completed")):
        response = _regenerate(api, story_id, target, note=note)
        assert response.status_code == 201, (target, response.text)
        job = response.json()
        assert job["step"] == "regenerate" and job["params"] == {"target": target, "note": note, "voice": None}
        ended[target] = _run(api, job["id"])
        assert ended[target]["status"] == status, (target, ended[target].get("error"))
        assert calls[-1] == (kind, story_id, 1, target, what, note)
    assert [call[0] for call in calls] == ["shot_image", "line", "metadata"]
    assert _story_file(api, story_id) == before

    # Both asset targets are jobs of assets:1: the newer supersedes the one awaiting, and the
    # assets approval completes the newer one.
    shot_job, line_job = ended["shot:1:sh02"]["id"], ended["line:1:l04"]["id"]
    assert api.jobs.get_job(shot_job)["superseded_by"] == line_job
    assert _approve(api, story_id, "assets:1").status_code == 200
    assert _status(api, line_job) == "completed" and api.jobs.get_job(line_job)["approved_at"]


def test_the_phase_4_targets_are_checked_against_the_episode_before_any_job(api, episodes):
    story_id = episode(api, episodes, "approved")
    for body, status, needle in (
            ({"target": "shot:1:sh99"}, 404, "Episode 1's storyboard has no shot 'sh99'"),
            ({"target": "line:1:l99"}, 404, "Episode 1's script has no line 'l99'"),
            ({"target": "metadata:1:tiktok"}, 409, "Episode 1 is not rendered yet"),
            ({"target": "shot:1:sh02", "voice": {"provider": "edge", "voice_id": "x"}}, 400, "voice"),
            ({"target": "line:1:l04", "voice": {"provider": "edge", "voice_id": "x"}}, 400, "voice"),
            ({"target": "shot:9:sh02"}, 400, "there is no episode 9"),
            ({"target": "shot:1:sh02:frames"}, 400, "later phase"),
            ({"target": "metadata:1:facebook"}, 400, "metadata:<ep>:tiktok|shorts|reels"),
    ):
        response = api.client.post(_url(story_id, "/regenerate"), json=body)
        assert response.status_code == status, (body, response.text)
        assert needle in response.json()["detail"], (body, response.text)

    board = _board(api, story_id)
    board["approved_at"] = None
    api.store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    for target in ("shot:1:sh02", "line:1:l04"):
        response = _regenerate(api, story_id, target)
        assert response.status_code == 409 and "Approve episode 1's storyboard first" in response.json()["detail"]
    assert api.jobs.list_jobs() == []


@pytest.mark.parametrize("target", ["shot:1:sh02", "line:1:l04", "metadata:1:tiktok"])
def test_a_phase_4_target_waits_for_a_ready_story(api, target):
    story_id = eps._ready_story(api.store)
    api.store.update(story_id, lambda doc: doc["approvals"].update(season=None), now=NOW)
    response = _regenerate(api, story_id, target)
    assert response.status_code == 409 and response.json()["detail"] == NOT_READY
    assert api.jobs.list_jobs() == []


def test_a_shot_image_meets_the_image_chains_gate(api, episodes):
    story_id = episode(api, episodes, "approved")
    _settings(api, {**tas._settings(), "IMAGE_CHAIN": "fal/flux-schnell"})
    response = _regenerate(api, story_id, "shot:1:sh02")
    assert response.status_code == 409, response.text
    assert "fal/flux-schnell" in response.json()["detail"]
    assert api.jobs.list_jobs() == []


# ================================================================== the lock

def test_a_shot_is_locked_and_unlocked_inline_and_a_locked_one_is_not_regenerated(api, episodes):
    story_id = episode(api, episodes, "approved")
    before = _story_file(api, story_id)
    board = _board(api, story_id)

    response = api.client.patch(_url(story_id, "/episodes/1/assets"),
                                json={"shots": [{"shot_id": "sh02", "locked": True}, {"shot_id": "sh03"}]})

    assert response.status_code == 200, response.text
    page = response.json()
    after = _board(api, story_id)
    assert after["shots"][1]["assets"]["locked"] is True
    assert "locked" not in after["shots"][2]["assets"] or after["shots"][2]["assets"]["locked"] is False
    # the lock is the assets', not the storyboard's content: its approval and revision stay
    assert (after["approved_at"], after["rev"]) == (board["approved_at"], board["rev"])
    assert [dict(s["assets"], locked=False) for s in after["shots"]] == [dict(s["assets"], locked=False)
                                                                       for s in board["shots"]]
    assert page["storyboard"]["shots"][1]["assets"]["locked"] is True
    assert page["assets"]["shots"][1]["locked"] is True and page["assets"]["shots"][1]["state"] == "current"
    # the fingerprint covers the locks (DEC-155): the approval is derived stale, never cleared
    assert page["assets"]["fingerprint"] == "stale" and _doc(api, story_id, "assets.json")["approved"]

    response = _regenerate(api, story_id, "shot:1:sh02", note="plus sombre")
    assert response.status_code == 409 and response.json()["detail"] == "Shot sh02 is locked: unlock it first."
    assert api.jobs.list_jobs() == []

    response = api.client.patch(_url(story_id, "/episodes/1/assets"),
                                json={"shots": [{"shot_id": "sh02", "locked": False}]})
    assert response.status_code == 200
    assert _board(api, story_id)["shots"][1]["assets"]["locked"] is False
    assert response.json()["assets"]["fingerprint"] == "current"
    assert _regenerate(api, story_id, "shot:1:sh02").status_code == 201
    assert _story_file(api, story_id) == before


def test_a_lock_is_refused_whole_for_an_unknown_shot_a_shot_without_an_image_or_a_non_boolean(api, episodes):
    story_id = episode(api, episodes, "planned")
    board = _board(api, story_id)
    for body, needles in (
            ({"shots": [{"shot_id": "sh02", "locked": True}, {"shot_id": "sh99", "locked": True}]},
             ["shots[1].shot_id: 'sh99' is not a shot of episode 1"]),
            ({"shots": [{"shot_id": "sh02", "locked": True}]}, ["shots[0].locked: shot sh02 has no image yet"]),
            ({"shots": [{"shot_id": "sh02", "locked": None}]}, ["shots[0].locked: expected true or false"]),
    ):
        response = api.client.patch(_url(story_id, "/episodes/1/assets"), json=body)
        assert response.status_code == 400, (body, response.text)
        detail = response.json()["detail"]
        assert detail["message"] == "The assets would not be valid with these values."
        for needle in needles:
            assert any(needle in error for error in detail["errors"]), (needle, detail)
    assert _board(api, story_id) == board

    # nothing sent: the page, nothing written; no storyboard: 409; an episode the season does not plan: 400
    assert api.client.patch(_url(story_id, "/episodes/1/assets"), json={}).status_code == 200
    assert _board(api, story_id) == board
    response = api.client.patch(_url(story_id, "/episodes/2/assets"), json={"shots": [{"shot_id": "sh01",
                                                                                          "locked": True}]})
    assert response.status_code == 409 and "Episode 2 has no storyboard yet" in response.json()["detail"]
    assert api.client.patch(_url(story_id, "/episodes/9/assets"), json={"shots": []}).status_code == 400


# ============================================================== the approval

def test_the_assets_approval_names_what_is_missing_then_completes_the_jobs_awaiting_it(api, episodes):
    story_id = episode(api, episodes, "planned")
    for doc in ("assets:9", "assets:x", "assets:01"):
        assert _approve(api, story_id, doc).status_code == 400, doc
    response = _approve(api, story_id, "assets:1")
    assert response.status_code == 409
    assert response.json()["detail"] == "Episode 1 has no assets yet: make them first (the assets step)."

    story_id = episode(api, episodes, "made")
    image = _image(api, story_id, "sh03")
    kept = image.read_bytes()
    image.unlink()
    response = _approve(api, story_id, "assets:1")
    assert response.status_code == 409
    assert response.json()["detail"] == ("Episode 1's shot sh03 has no current image: make it (the assets step, or "
                                         "regenerate shot:1:sh03) or lock it, then approve.")
    image.write_bytes(kept)

    assets_job = _job(api, story_id, "assets", status="awaiting_approval", ep=1)
    line_job = _job(api, story_id, "regenerate", status="awaiting_approval", params={"target": "line:1:l04"})
    script_job = _job(api, story_id, "script", status="awaiting_approval", ep=1)
    other_ep = _job(api, story_id, "assets", status="awaiting_approval", ep=2)

    response = _approve(api, story_id, "assets:1")

    assert response.status_code == 200, response.text
    page = response.json()
    assert page["ep"] == 1 and page["assets"]["fingerprint"] == "current"
    assert page["assets"]["doc"]["approved"]["at"] and page["assets"]["approved_at"] == page["assets"]["doc"][
        "approved"]["at"]
    assert all(shot["assets"]["approved"] is True for shot in page["storyboard"]["shots"])
    assert [_status(api, job) for job in (assets_job, line_job, script_job, other_ep)] == [
        "completed", "completed", "awaiting_approval", "awaiting_approval"]
    assert api.jobs.get_job(assets_job)["approved_at"]


def test_the_assets_approval_leaves_the_story_alone(api, episodes):
    story_id = episode(api, episodes, "made")
    before = _story_file(api, story_id)
    assert _approve(api, story_id, "assets:1").status_code == 200
    assert _story_file(api, story_id) == before


# ============================================================== the fast track

def _fast_track_ran(api, story_id, runner=None, status="completed"):
    """A fast-track job run to its end with its runner faked: the documents it
    would have approved in-process are the ones on disk."""
    job = _post_step(api, story_id, "fast-track", ep=1).json()
    api.monkeypatch.setitem(steps.RUNNERS, "fast-track",
                            runner or (lambda ctx: {"ep": ctx.ep, "auto_approved": []}))
    ended = _run(api, job["id"])
    assert ended["status"] == status, ended.get("error")
    return job["id"]


def test_a_finished_fast_track_completes_the_older_jobs_whose_documents_it_approved(api, episodes):
    story_id = episode(api, episodes, "approved")
    waiting = {
        "script": _job(api, story_id, "script", status="awaiting_approval", ep=1),
        "storyboard": _job(api, story_id, "storyboard", status="awaiting_approval", ep=1),
        "assets": _job(api, story_id, "assets", status="awaiting_approval", ep=1),
        "scene": _job(api, story_id, "regenerate", status="awaiting_approval", params={"target": "scene:1:s03"}),
        "shot": _job(api, story_id, "regenerate", status="awaiting_approval", params={"target": "shot:1:sh02"}),
    }
    others = {
        "ep2": _job(api, story_id, "script", status="awaiting_approval", ep=2),
        "cast": _job(api, story_id, "cast", status="awaiting_approval"),
    }

    _fast_track_ran(api, story_id)

    for name, job_id in waiting.items():
        assert _status(api, job_id) == "completed", name
        assert api.jobs.get_job(job_id)["approved_at"], name
    for name, job_id in others.items():
        assert _status(api, job_id) == "awaiting_approval", name


def test_a_stale_assets_approval_does_not_complete_the_jobs_awaiting_it(api, episodes):
    story_id = episode(api, episodes, "approved")
    _image(api, story_id, "sh02").write_bytes(b"\x89PNG changed after the approval")
    script_job = _job(api, story_id, "script", status="awaiting_approval", ep=1)
    assets_job = _job(api, story_id, "assets", status="awaiting_approval", ep=1)

    _fast_track_ran(api, story_id)

    assert _status(api, script_job) == "completed"
    assert _status(api, assets_job) == "awaiting_approval"


def test_a_fast_track_that_stopped_still_completes_the_jobs_of_what_it_approved(api, episodes):
    story_id = episode(api, episodes, "made")  # script and storyboard approved, the assets not yet
    script_job = _job(api, story_id, "script", status="awaiting_approval", ep=1)
    assets_job = _job(api, story_id, "assets", status="awaiting_approval", ep=1)

    def stopped(ctx):
        raise steps.StepFailed("Fast track stopped at the paid check (step 3 of 6): ...")

    _fast_track_ran(api, story_id, runner=stopped, status="failed")

    assert _status(api, script_job) == "completed"
    assert _status(api, assets_job) == "awaiting_approval"


def test_another_step_that_ends_leaves_the_jobs_awaiting_as_they_are(api, episodes):
    story_id = episode(api, episodes, "approved")
    script_job = _job(api, story_id, "script", status="awaiting_approval", ep=1)
    job = _post_step(api, story_id, "render", ep=1).json()
    api.monkeypatch.setitem(steps.RUNNERS, "render", lambda ctx: {"ep": ctx.ep})
    assert _run(api, job["id"])["status"] == "completed"
    assert _status(api, script_job) == "awaiting_approval"


def test_the_worker_hook_never_fails_the_finished_step(api, episodes):
    story_id = episode(api, episodes, "approved")
    script_job = _job(api, story_id, "script", status="awaiting_approval", ep=1)

    def broken(*_args, **_kwargs):
        raise RuntimeError("the hook broke")

    api.monkeypatch.setattr(api.routes, "complete_approved_jobs", broken)
    _fast_track_ran(api, story_id)
    assert _status(api, script_job) == "awaiting_approval"


# ================================================================= estimates

def _estimate(api, story_id, step, **params):
    response = api.client.get(_url(story_id, f"/estimate/{step}"), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_assets_estimate_is_the_steps_own_plan(api, episodes):
    story_id = episode(api, episodes, "planned")
    body = _estimate(api, story_id, "assets", ep=1)
    assert body["step"] == "assets" and body["ep"] == 1
    for key in ("images", "voices", "alignment", "paid_links", "caps", "est_usd", "over_cap", "ready", "paid",
                "message"):
        assert key in body, key
    assert body["images"]["count"] == len(_board(api, story_id)["shots"]) and body["images"]["route_class"] == "free"
    assert body["alignment"] == {"opted_in": False, "requests": 0}
    assert body["est_usd"] == 0.0 and body["ready"] is True and body["paid"]["verdict"] == "free"
    assert body["message"] == body["paid"]["message"]
    assert _estimate(api, story_id, "assets", ep=1, align_words=True)["alignment"]["opted_in"] is True

    story_id = episode(api, episodes, "approved")
    body = _estimate(api, story_id, "assets", ep=1)
    assert body["images"]["count"] == 0 and body["est_usd"] == 0.0


def test_the_render_metadata_and_fast_track_estimates(api, episodes):
    story_id = episode(api, episodes, "rendered")
    shots = len(_board(api, story_id)["shots"])

    body = _estimate(api, story_id, "render", ep=1)
    assert (body["step"], body["ep"], body["est_usd"], body["route_class"], body["ready"]) == (
        "render", 1, 0.0, "local", True)
    assert body["units"] == {"llm_calls": 0, "shots": shots}
    assert body["params"] == {"subtitles": "style", "encoder": "libx264"}
    assert body["needed"] is False and body["seconds"] == 0.0  # the last render is the one it would make
    changed = _estimate(api, story_id, "render", ep=1, subtitles="none")
    assert changed["needed"] is True and changed["seconds"] > 0 and changed["minutes"] > 0
    assert changed["params"]["subtitles"] == "none" and "basis" in changed
    response = api.client.get(_url(story_id, "/estimate/render"), params={"ep": 1, "encoder": "nvenc"})
    assert response.status_code == 400

    body = _estimate(api, story_id, "metadata", ep=1)
    assert (body["step"], body["ep"], body["est_usd"], body["units"]) == ("metadata", 1, 0.0, {"llm_calls": 3})
    assert body["platforms"] == ["tiktok", "shorts", "reels"] and body["link"] == "gemini/gemini-test"
    assert body["ready"] is True and body["skipped_paid"] == []

    body = _estimate(api, story_id, "fast-track", ep=1)
    assert body["step"] == "fast-track" and body["ep"] == 1 and body["storyboard"] == "t1"
    for key in ("llm_calls", "images", "tts", "render", "est_usd", "paid", "stops_at", "ready", "message", "link"):
        assert key in body, key
    assert body["llm_calls"]["total"] == 3 and body["render"]["needed"] is False and body["ready"] is True
    assert _estimate(api, story_id, "fast-track", ep=1, storyboard="fast")["storyboard"] == "fast"
    response = api.client.get(_url(story_id, "/estimate/fast-track"), params={"ep": 1, "storyboard": "slow"})
    assert response.status_code == 400

    story_id = episode(api, episodes, "published")
    assert _estimate(api, story_id, "metadata", ep=1)["units"] == {"llm_calls": 0}


def test_an_estimate_is_refused_as_its_step_is(api, episodes):
    story_id = episode(api, episodes, "made")
    for step, status, needle in (("render", 409, "Approve episode 1's assets first"),
                                 ("metadata", 409, "Episode 1 is not rendered yet")):
        response = api.client.get(_url(story_id, f"/estimate/{step}"), params={"ep": 1})
        assert response.status_code == status, (step, response.text)
        assert needle in response.json()["detail"], step
    for step in PHASE4_STEPS:
        assert api.client.get(_url(story_id, f"/estimate/{step}")).status_code == 400, step  # no ep
        assert api.client.get(_url(story_id, f"/estimate/{step}"), params={"ep": 9}).status_code == 400, step
    assert api.client.get(_url(story_id, "/estimate/import"), params={"ep": 1}).status_code == 400
    assert api.jobs.list_jobs() == []


# ================================================================ the page

def test_the_episode_page_before_and_after_the_assets(api, episodes):
    story_id = episode(api, episodes, "planned")
    page = _episode(api, story_id)
    board = page["storyboard"]
    assert (page["render"], page["metadata"]) == (None, None)
    assert page["ledger"] == {"entries": [], "totals": {"est_usd": 0.0, "paid_usd": 0.0, "entries": 0}}
    assets = page["assets"]
    assert (assets["doc"], assets["fingerprint"], assets["approved_at"], assets["consistency"]) == (
        None, "none", None, "prompt_only")
    assert [shot["shot_id"] for shot in assets["shots"]] == [shot["shot_id"] for shot in board["shots"]]
    first = assets["shots"][0]
    assert (first["state"], first["image_name"], first["route"], first["locked"], first["target"]) == (
        "none", None, None, False, "shot:1:sh01")
    lines = [line for scene in page["script"]["scenes"] for line in scene["lines"]]
    assert [line["line_id"] for line in assets["lines"]] == [line["line_id"] for line in lines]
    assert all(line["voiced"] is False and line["approximate"] is True and line["words_source"] is None
               for line in assets["lines"])

    story_id = episode(api, episodes, "made")
    page = _episode(api, story_id)
    assets = page["assets"]
    assert assets["doc"] == _doc(api, story_id, "assets.json") and assets["fingerprint"] == "none"
    first = assets["shots"][0]
    assert (first["state"], first["image_name"], first["route"], first["consistency"], first["locked"],
            first["approved"], first["pending"]) == ("current", "shot_01.png", "free", "prompt_only", False, False,
                                                     False)
    assert first["provider"] == "pollinations" and first["target"] == "shot:1:sh01"
    for line in assets["lines"]:
        assert line["voiced"] is True and line["voice"], line
        assert line["words_source"] in ("provider", "alignment", "even_split")
        assert line["approximate"] is (line["words_source"] == "even_split")
        assert line["target"] == f"line:1:{line['line_id']}"
    rows = page["ledger"]["entries"]
    assert rows and all(row["ep"] == 1 for row in rows)
    assert page["ledger"]["totals"]["entries"] == len(rows)

    _image(api, story_id, "sh02").unlink()
    page = _episode(api, story_id)
    assert page["assets"]["shots"][1]["state"] == "none" and page["assets"]["shots"][1]["image_name"] is None


def test_the_episode_page_after_the_render_and_the_metadata(api, episodes):
    story_id = episode(api, episodes, "published")
    page = _episode(api, story_id)
    manifest = _doc(api, story_id, "render_manifest.json")
    output = manifest["output"]

    render = page["render"]
    assert render["state"] == "completed" and render["out_of_date"] is False
    assert (render["duration_s"], render["loudness"], render["fps"], render["width"], render["height"]) == (
        output["duration_s"], output["loudness"], output["fps"], output["width"], output["height"])
    assert render["output"] == {"file": "episode_final.mp4", "sha256": output["sha256"]}
    assert render["params"] == manifest["params"] and render["profile"] == "final"
    assert render["seconds"] == manifest["timings"]["total_s"] and render["warnings"] == manifest["warnings"]
    stages = manifest["stages"]
    assert render["stages"] == {
        "total": len(stages), "ran": sum(s["state"] == "done" for s in stages),
        "cached": sum(s["state"] == "cached" for s in stages), "shots": sum(s["kind"] == "shot" for s in stages),
        "shots_cached": sum(s["kind"] == "shot" and s["state"] == "cached" for s in stages)}
    # The episode media route, signed only when a token is set (DEC-163, DEC-173). This api runs
    # with auth off, so the page hands out the plain path; the signed page URLs are pinned with a
    # token in test_story_media_serving.py.
    for field, name in (("video_url", "episode_final.mp4"), ("cover_url", "cover.jpg")):
        assert render["media"][field] == f"/api/stories/{story_id}/episodes/1/media/{name}"
        assert api.client.get(render["media"][field]).content == (_ep_dir(api, story_id) / name).read_bytes()

    metadata = page["metadata"]
    assert metadata["pack"] == _doc(api, story_id, "metadata_pack.json") and metadata["current"] is True
    assert page["assets"]["fingerprint"] == "current"

    # Another render: the pack was written for the one before.
    tms.rerender(api.store, story_id)
    page = _episode(api, story_id)
    assert page["metadata"]["current"] is False
    assert page["render"]["output"]["sha256"] == _doc(api, story_id, "render_manifest.json")["output"]["sha256"]


def test_the_renders_out_of_date_answer_is_remembered_while_nothing_changes(api, episodes):
    from clipping.aistory.steps import assets, render

    story_id = episode(api, episodes, "rendered")
    counts = {"render": 0, "fingerprint": 0}
    real_render, real_fingerprint = render.current_render, assets.current_fingerprint

    def counted_render(*args, **kwargs):
        counts["render"] += 1
        return real_render(*args, **kwargs)

    def counted_fingerprint(*args, **kwargs):
        counts["fingerprint"] += 1
        return real_fingerprint(*args, **kwargs)

    api.monkeypatch.setattr(render, "current_render", counted_render)
    api.monkeypatch.setattr(assets, "current_fingerprint", counted_fingerprint)

    first = _episode(api, story_id)
    after_first = dict(counts)
    second = _episode(api, story_id)

    assert after_first["render"] == 1 and after_first["fingerprint"] >= 1
    assert counts == after_first  # the 4 s poll: nothing hashed again
    assert first["render"]["out_of_date"] is False and second == first

    image = _image(api, story_id, "sh02")
    image.write_bytes(image.read_bytes() + b" another picture")
    third = _episode(api, story_id)
    assert counts["render"] == 2 and counts["fingerprint"] > after_first["fingerprint"]
    assert third["render"]["out_of_date"] is True and third["assets"]["fingerprint"] == "stale"
    assert _episode(api, story_id) == third and counts["render"] == 2


def test_the_page_lists_every_job_in_flight_on_its_episode(api, episodes):
    story_id = episode(api, episodes, "rendered")
    mine = [_job(api, story_id, step, ep=1) for step in ("render", "metadata", "fast-track", "assets")]
    mine.append(_job(api, story_id, "regenerate", params={"target": "metadata:1:tiktok"}))
    mine.append(_job(api, story_id, "regenerate", params={"target": "line:1:l04"}))
    _job(api, story_id, "render", ep=2)
    _job(api, story_id, "cast")
    assert [job["id"] for job in _episode(api, story_id)["jobs"]] == mine


# ============================================================ the shot images

def test_the_shots_route_serves_a_shots_image_and_nothing_else(api, episodes, tmp_path):
    story_id = episode(api, episodes, "made")
    other = eps._ready_story(api.store)
    image = _image(api, story_id, "sh02")
    shots = image.parent
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"\x89PNG not a shot")
    (shots / "shot_03.png").unlink()
    os.symlink(outside, shots / "shot_03.png")

    response = api.client.get(_url(story_id, "/episodes/1/shots/shot_02.png"))
    assert response.status_code == 200 and response.content == image.read_bytes()
    assert response.headers["content-type"] == "image/png" and response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].startswith("inline")

    for path in ("/episodes/1/shots/shot_2.png", "/episodes/1/shots/shot_00.png", "/episodes/1/shots/shot_02.gif",
                 "/episodes/1/shots/shot_02.PNG", "/episodes/1/shots/line_02.mp3", "/episodes/1/shots/shot_03.png",
                 "/episodes/1/shots/shot_59.png", "/episodes/1/shots/..%2Fstoryboard.json",
                 "/episodes/1/shots/..%2F..%2Fstoryboard.json", "/episodes/0/shots/shot_02.png",
                 "/episodes/01/shots/shot_02.png", "/episodes/x/shots/shot_02.png",
                 "/episodes/..%2Fep01/shots/shot_02.png"):
        assert api.client.get(_url(story_id, path)).status_code == 404, path
    assert api.client.get(_url(other, "/episodes/1/shots/shot_02.png")).status_code == 404
    assert api.client.get(_url("ABC", "/episodes/1/shots/shot_02.png")).status_code == 404
    assert api.client.get(_url(UNKNOWN_ID, "/episodes/1/shots/shot_02.png")).status_code == 404

    # a symlinked shots folder is never followed
    moved = tmp_path / "elsewhere"
    shutil.move(str(shots), moved)
    os.symlink(moved, shots)
    assert api.client.get(_url(story_id, "/episodes/1/shots/shot_02.png")).status_code == 404


# ================================================================== token

NEW_ROUTES = [
    ("POST", "/steps/assets", {"ep": 1}),
    ("POST", "/steps/render", {"ep": 1}),
    ("POST", "/steps/metadata", {"ep": 1}),
    ("POST", "/steps/fast-track", {"ep": 1}),
    ("POST", "/approve/assets:1", None),
    ("PATCH", "/episodes/1/assets", {"shots": [{"shot_id": "sh02", "locked": True}]}),
    ("POST", "/regenerate", {"target": "shot:1:sh02"}),
    ("GET", "/episodes/1", None),
    ("GET", "/episodes/1/shots/shot_02.png", None),
    ("GET", "/estimate/assets?ep=1", None),
    ("GET", "/estimate/render?ep=1", None),
    ("GET", "/estimate/metadata?ep=1", None),
    ("GET", "/estimate/fast-track?ep=1", None),
]


def test_the_phase_4_routes_are_under_the_routers_token(api):
    from web.api.auth import require_token

    paths = {route.path for route in api.routes.router.routes}
    for path in ("/api/stories/{story_id}/episodes/{ep}/assets", "/api/stories/{story_id}/episodes/{ep}/shots/{name}"):
        assert path in paths
    for route in api.routes.router.routes:
        assert any(dep.call is require_token for dep in route.dependant.dependencies), route.path


def test_the_phase_4_routes_refuse_a_request_without_the_token_before_reading_it(api, episodes, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from web.api import auth

    story_id = episode(api, episodes, "made")
    board = _board(api, story_id)
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
        assert client.get(_url(story_id, "/episodes/1/shots/shot_02.png"), headers=headers).status_code == 200
    assert _board(api, story_id) == board and _doc(api, story_id, "assets.json")["approved"] is None
    assert api.jobs.list_jobs() == []
    monkeypatch.setattr(auth, "_TOKEN", None)
