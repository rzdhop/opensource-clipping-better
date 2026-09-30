"""The AI Story API, re-edit (phase 5, plan 11 "Stage 9 -- re-edit API and
CLI"; spec 3, 9.1, 9.2; DEC-156, DEC-161, DEC-164, DEC-173; RC-M8, RC-M9).

``web/api/routes/stories.py`` on a throwaway app -- the stories router and the
jobs router, never the singleton ``web.api.app.app`` -- with the job store on
an empty table under ``tmp_path``, ``worker.OUTPUTS_ROOT`` under ``tmp_path``
and ``worker.submit_job`` replaced by a recorder, exactly as the phase-4 and
phase-5 API tests have it. The fixture episode is stage 8's French story
(``tests/test_story_assets_step.py``: written, planned fast, its images made
by the fake image adapter and its lines by the fake Edge voice, approved) and
its own ``rendered`` copy (``tests/test_story_render_step.py``: rendered once
with stage 7's fake ffmpeg, ``tests/test_aistory_render_runner.py``), built
and cached per kind exactly as ``tests/test_stories_api_phase4.py`` does (its
own copy here, not imported, so this file stays independently readable).
A job that has to *run* goes through the real worker path
(``worker._execute_story_step``) with its handler bound to the same fakes
(the real ``rerender``/``regenerate`` runners with stage 7/8's fake ffmpeg and
Edge, never a stub), as the phase-4 tests do for the assets job.

Covered:
- ``POST /steps/rerender``: queued and completed (no key gate, the manifest's
  ``reuse`` record and the episode page agree), each refusal before any job
  exists (no finished render, an outdated shot's image naming it, a
  parameter), the queue rules every step meets, no ``Authorization`` header
  needed (``API_TOKEN`` unset, RC-M9);
- ``GET /estimate/rerender``: $0, no LLM call, the same count the route then
  runs (RC-M8), the same two refusals;
- ``GET /{id}/episodes/{ep}``'s "changes since last render" block
  (``render.changes``): matches the estimate's own count, cached between two
  polls with nothing changed, invalidated by an edit, ``blocked`` (never a
  500) when the episode cannot be rendered right now;
- the stage-7 edit targets exercised through the API: a text-only line edit
  (``PATCH .../script``) keeps the storyboard's approval and marks its scene
  ``retime_only``; ``pays_off`` through the same route; a line regenerated
  with a note (``POST /regenerate line:<ep>:<lid>``) persists its take,
  exposed by the episode view (``assets.lines[].take``/``note``/``pending``);
  a motion swap (``PATCH .../storyboard``) keeps the image and only moves the
  shot's render key; a framing edit outdates the image and the render (so
  the re-render) refuses it, naming the shot, everywhere that would render.

The grammar and the request models' text guards are stdlib (the CI
environment); everything else needs pydantic, fastapi and httpx and skips
without them, like the other route tests. The routes are reached through the
client only, so on the parent commit (``ecd94aa``) each test fails on its own
(``POST /steps/rerender`` answers 404 there, ``GET /estimate/rerender`` 404,
``GET /{id}/episodes/{ep}``'s ``render`` carries no ``changes`` and
``assets.lines[]`` no ``take``/``note``/``pending``).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_metadata_step as tms
import test_story_reedit as tre
import test_story_render_step as trs
from clipping.aistory import steps
from clipping.cancel import CancelToken
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used here as they are

NOW = eps.NOW


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


@pytest.fixture(scope="session")
def episodes(tmp_path_factory):
    """``{kind: (outputs copy, story_id)}``, filled by the first test that
    needs each kind of episode (the phase-4 API test's own pattern,
    duplicated here under its own root so this file stays independently
    runnable)."""
    return {"root": tmp_path_factory.mktemp("api_reedit_episodes"), "kinds": {}}


def _make_assets(api, story_id):
    summary, _log = tas._run(api.store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    assert summary["complete"] is True


def _approve_assets_kind(api, story_id):
    from clipping.aistory import workflow

    workflow.approve_assets(api.store, story_id, 1, now=NOW)


def _render_kind(api, story_id):
    trs.render(api.store, story_id, tmp_path=api.tmp_path)


KINDS = ("planned", "made", "approved", "rendered")
BUILD = {"made": _make_assets, "approved": _approve_assets_kind, "rendered": _render_kind}


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


def _url(story_id, suffix=""):
    return f"/api/stories/{story_id}{suffix}"


def _post_step(api, story_id, step, ep=None, params=None):
    body = {}
    if ep is not None:
        body["ep"] = ep
    if params is not None:
        body["params"] = params
    return api.client.post(_url(story_id, f"/steps/{step}"), json=body)


def _episode(api, story_id, ep=1):
    response = api.client.get(_url(story_id, f"/episodes/{ep}"))
    assert response.status_code == 200, response.text
    return response.json()


def _approve(api, story_id, doc):
    return api.client.post(_url(story_id, f"/approve/{doc}"))


def _regenerate(api, story_id, target, **body):
    return api.client.post(_url(story_id, "/regenerate"), json={"target": target, **body})


def _patch_script(api, story_id, ep, **body):
    return api.client.patch(_url(story_id, f"/episodes/{ep}/script"), json=body)


def _patch_storyboard(api, story_id, ep, **body):
    return api.client.patch(_url(story_id, f"/episodes/{ep}/storyboard"), json=body)


def _estimate_response(api, story_id, step, **params):
    return api.client.get(_url(story_id, f"/estimate/{step}"), params=params)


def _estimate(api, story_id, step, **params):
    response = _estimate_response(api, story_id, step, **params)
    assert response.status_code == 200, response.text
    return response.json()


def _doc(api, story_id, name, ep=1):
    return api.store.read_episode_doc(story_id, ep, name)


def _board(api, story_id, ep=1):
    return _doc(api, story_id, "storyboard.json", ep)


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


def _patch_rerender_runner(api, fake=None):
    """Bind stage 7's fake ffmpeg onto the real ``rerender`` runner for a job
    run through the worker path (as the phase-4 API test's own
    ``run_assets`` binds the fake image adapter onto ``assets.run``)."""
    from clipping.aistory.steps import rerender as rerender_step

    import test_aistory_render_runner as rr

    fake = fake or rr.FakeFFmpeg()
    fonts_dir = api.tmp_path / "no_custom_fonts"

    def run_rerender(ctx):
        return rerender_step.run(ctx, run_process=rr._fake_run(), popen=fake, clock=fake.clock,
                                 custom_fonts_dir=fonts_dir)

    api.monkeypatch.setitem(steps.RUNNERS, "rerender", run_rerender)
    return fake


def _patch_regenerate_runner(api):
    """Bind stage 7's fake Edge onto the real ``regenerate`` runner for a job
    run through the worker path (a line's take, persisted for real, never a
    stub recorder)."""
    from clipping.aistory.steps import regenerate as regenerate_step

    def run_regenerate(ctx):
        return regenerate_step.run(ctx, adapters=tas._adapters(), sleep_fn=lambda _s: None, time_fn=eps.Clock(0.0))

    api.monkeypatch.setitem(steps.RUNNERS, "regenerate", run_regenerate)


def _motion_swap(api, story_id, *, shot_id="sh05", camera_motion="pan_rl"):
    response = _patch_storyboard(api, story_id, 1, shots=[{"shot_id": shot_id, "camera_motion": camera_motion}])
    assert response.status_code == 200, response.text
    assert _approve(api, story_id, "storyboard:1").status_code == 200


def _framing_edit(api, story_id, *, shot_id="sh04", framing="low_angle"):
    response = _patch_storyboard(api, story_id, 1, shots=[{"shot_id": shot_id, "framing": framing}])
    assert response.status_code == 200, response.text
    assert _approve(api, story_id, "storyboard:1").status_code == 200


# ================================================================ 1. the route

def test_post_steps_rerender_queues_a_job_that_ends_completed(api, episodes):
    story_id = episode(api, episodes, "rendered")
    total = len(_board(api, story_id)["shots"])
    before = _doc(api, story_id, "render_manifest.json")

    response = _post_step(api, story_id, "rerender", ep=1)

    assert response.status_code == 201, response.text
    job = response.json()
    assert job["step"] == "rerender" and job["ep"] == 1 and job["params"] == {}
    assert job["status"] == "queued"
    assert api.submitted == [job["id"]]

    _patch_rerender_runner(api)
    ended = _run(api, job["id"])
    assert ended["status"] == "completed", ended.get("error")

    after = _doc(api, story_id, "render_manifest.json")
    assert after["reuse"]["shots_rebuilt"] == [] and len(after["reuse"]["shots_reused"]) == total
    assert after["output"]["sha256"] == before["output"]["sha256"]
    last_good = _doc(api, story_id, "render_manifest.last_good.json")
    assert last_good == after

    page = _episode(api, story_id)
    assert page["render"]["reuse"]["summary"] == f"0 of {total} shots re-rendered"
    assert page["render"]["reuse"]["shots_reused"] == after["reuse"]["shots_reused"]


def test_post_steps_rerender_takes_no_parameters(api, episodes):
    story_id = episode(api, episodes, "rendered")

    response = _post_step(api, story_id, "rerender", ep=1, params={"subtitles": "none"})

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "takes no parameters" in detail and "subtitles" in detail
    assert api.jobs.list_jobs() == []


@pytest.mark.parametrize("kind, needle", [
    ("planned", "no finished render to re-render"),
    ("made", "no finished render to re-render"),
    ("approved", "no finished render to re-render"),
])
def test_post_steps_rerender_refuses_without_a_finished_render(api, episodes, kind, needle):
    story_id = episode(api, episodes, kind)

    response = _post_step(api, story_id, "rerender", ep=1)

    assert response.status_code == 409, response.text
    assert needle in response.json()["detail"]
    assert api.jobs.list_jobs() == []


def test_post_steps_rerender_refuses_an_outdated_shot_image_naming_it(api, episodes):
    story_id = episode(api, episodes, "rendered")
    _framing_edit(api, story_id)

    response = _post_step(api, story_id, "rerender", ep=1)

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "sh04" in detail and "out of date" in detail and "shot:1:sh04" in detail
    assert api.jobs.list_jobs() == []


def test_post_steps_rerender_meets_the_busy_rule_and_the_queue_cap_like_any_step(api, episodes):
    story_id = episode(api, episodes, "rendered")
    older = _job(api, story_id, "assets", status="queued", ep=1)

    response = _post_step(api, story_id, "rerender", ep=1)

    assert response.status_code == 409, response.text
    assert older in response.json()["detail"] or "wait for it to finish" in response.json()["detail"]


def test_rerender_needs_no_key_gate(api, episodes, monkeypatch):
    """It calls no API (like ``render``): an empty ``LLM_CHAIN`` (which would
    400 every LLM step) does not refuse it."""
    story_id = episode(api, episodes, "rendered")
    monkeypatch.setattr(api.worker, "_settings_env", dict(tas._settings(), LLM_CHAIN=""))

    response = _post_step(api, story_id, "rerender", ep=1)

    assert response.status_code == 201, response.text


# ============================================================= 2. the estimate

def test_estimate_rerender_is_free_and_matches_the_dry_run_count(api, episodes):
    story_id = episode(api, episodes, "rendered")
    total = len(_board(api, story_id)["shots"])

    body = _estimate(api, story_id, "rerender", ep=1)

    assert body["step"] == "rerender" and body["ep"] == 1 and body["est_usd"] == 0.0
    assert body["units"] == {"llm_calls": 0, "shots": total} and body["route_class"] == "local"
    assert body["ready"] is True and body["current"] is True
    assert body["shots_total"] == total and body["rebuild"] == [] and len(body["reuse"]) == total
    assert body["reasons"] == {}
    assert body["message"] == f"0 of {total} shots re-rendered"


def test_estimate_rerender_refuses_without_a_finished_render(api, episodes):
    story_id = episode(api, episodes, "made")

    response = _estimate_response(api, story_id, "rerender", ep=1)

    assert response.status_code == 409, response.text
    assert "no finished render to re-render" in response.json()["detail"]


def test_estimate_rerender_refuses_an_outdated_shot_image_naming_it(api, episodes):
    story_id = episode(api, episodes, "rendered")
    _framing_edit(api, story_id)

    response = _estimate_response(api, story_id, "rerender", ep=1)

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "sh04" in detail and "out of date" in detail


def test_estimate_rerender_reflects_a_motion_swap(api, episodes):
    story_id = episode(api, episodes, "rendered")
    _motion_swap(api, story_id)

    body = _estimate(api, story_id, "rerender", ep=1)

    assert body["current"] is False and body["rebuild"] == ["sh05"] and body["reasons"] == {"sh05": "motion"}
    assert body["message"] == f"1 of {body['shots_total']} shots re-rendered"


# ==================================================== 3. the episode view's block

def test_the_changes_block_matches_the_estimate_and_is_cached(api, episodes, monkeypatch):
    from clipping.aistory.steps import render as render_step

    story_id = episode(api, episodes, "rendered")
    _motion_swap(api, story_id)
    estimate = _estimate(api, story_id, "rerender", ep=1)

    calls = {"n": 0}
    real = render_step.render_changes

    def counted(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(render_step, "render_changes", counted)

    first = _episode(api, story_id)
    changes = first["render"]["changes"]
    assert changes["blocked"] is None
    assert changes["shots_total"] == estimate["shots_total"] == estimate["units"]["shots"]
    assert changes["rebuild"] == estimate["rebuild"] == ["sh05"]
    assert changes["reuse"] == estimate["reuse"]
    assert changes["reasons"] == estimate["reasons"] == {"sh05": "motion"}
    assert changes["summary"] == estimate["message"]
    # The motion swap's own PATCH/approve calls may already have computed and
    # cached this (each returns the full episode page): however many calls
    # that took, a poll with nothing changed since must add none.
    settled = calls["n"]

    second = _episode(api, story_id)
    assert second == first and calls["n"] == settled  # nothing changed: not hashed again


def test_the_changes_block_is_invalidated_by_an_edit(api, episodes, monkeypatch):
    from clipping.aistory.steps import render as render_step

    story_id = episode(api, episodes, "rendered")
    calls = {"n": 0}
    real = render_step.render_changes

    def counted(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(render_step, "render_changes", counted)

    before = _episode(api, story_id)
    assert before["render"]["changes"]["current"] is True
    after_before = calls["n"]
    assert after_before >= 1

    _motion_swap(api, story_id)  # the edit: at least one more computation along the way
    assert calls["n"] > after_before

    after = _episode(api, story_id)
    settled = calls["n"]
    assert after["render"]["changes"]["current"] is False
    assert after["render"]["changes"]["rebuild"] == ["sh05"]
    assert after != before

    assert _episode(api, story_id) == after and calls["n"] == settled  # settled: no more, nothing moved


def test_the_changes_block_is_blocked_not_500_when_the_episode_cannot_render(api, episodes):
    story_id = episode(api, episodes, "rendered")
    _framing_edit(api, story_id)

    page = _episode(api, story_id)

    changes = page["render"]["changes"]
    assert set(changes) == {"blocked"}
    assert "sh04" in changes["blocked"] and "out of date" in changes["blocked"]


def test_the_changes_block_is_null_before_any_render(api, episodes):
    story_id = episode(api, episodes, "approved")

    page = _episode(api, story_id)

    assert page["render"] is None  # no manifest at all yet: unrelated to stage 9, sanity only


# ============================================================== 4. stage-7 edits

def test_a_text_only_line_edit_through_the_api_keeps_the_storyboard_approval(api, episodes):
    story_id = episode(api, episodes, "rendered")
    board_before = _board(api, story_id)
    assert board_before["approved_at"] is not None

    response = _patch_script(api, story_id, 1, lines=[{"line_id": "l08", "text": tre.NEW_WORDS}])

    assert response.status_code == 200, response.text
    page = response.json()
    assert page["script"]["approved_at"] is None  # the script's own approval always goes
    assert page["storyboard"]["approved_at"] == board_before["approved_at"]  # kept (DEC-129 amended)
    scene_id = next(scene["scene_id"] for scene in page["script"]["scenes"]
                    for line in scene["lines"] if line["line_id"] == "l08")
    assert page["storyboard"]["scenes"][scene_id]["retime_only"] is True
    assert page["storyboard"]["shots"] == board_before["shots"]  # every shot kept


def test_pays_off_through_the_api(api):
    story_id = tre._episode_2(api.store)
    before = _doc(api, story_id, "script.json", ep=2)
    board_before = _doc(api, story_id, "storyboard.json", ep=2)
    scene_before = next(s for s in before["scenes"] if s["scene_id"] == "s04")
    assert scene_before["pays_off"] == [eps.HOOK_PHONE]

    response = _patch_script(api, story_id, 2, scenes=[{"scene_id": "s04", "pays_off": []},
                                                       {"scene_id": "s05", "pays_off": [eps.HOOK_BETRAY]}])

    assert response.status_code == 200, response.text
    page = response.json()
    s04 = next(s for s in page["script"]["scenes"] if s["scene_id"] == "s04")
    s05 = next(s for s in page["script"]["scenes"] if s["scene_id"] == "s05")
    assert "pays_off" not in s04  # none is stored as nothing
    assert s05["pays_off"] == [eps.HOOK_BETRAY]
    assert page["script"]["approved_at"] is None  # a pays_off edit stales the check, never a shot
    assert page["storyboard"]["approved_at"] == board_before["approved_at"]  # untouched: no shot moved
    assert page["storyboard"]["shots"] == board_before["shots"]


def test_pays_off_is_checked_against_the_hooks_open_before_the_episode(api):
    story_id = tre._episode_2(api.store)

    response = _patch_script(api, story_id, 2, scenes=[{"scene_id": "s05", "pays_off": [eps.HOOK_VOTE]}])

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    errors = detail["errors"] if isinstance(detail, dict) else [detail]
    assert any("not a hook open before episode 2" in error for error in errors)


def test_a_line_regenerate_with_a_note_persists_a_take_exposed_by_the_episode_view(api, episodes):
    story_id = episode(api, episodes, "rendered")

    response = _regenerate(api, story_id, "line:1:l08", note="plus fort")
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["params"] == {"target": "line:1:l08", "note": "plus fort", "voice": None}

    _patch_regenerate_runner(api)
    ended = _run(api, job["id"])
    assert ended["status"] == "awaiting_approval", ended.get("error")

    doc = _doc(api, story_id, "assets.json")
    entry = doc["lines"]["l08"]
    assert entry["take"]["note"] == "plus fort" and "pending" not in entry

    assert _approve(api, story_id, "assets:1").status_code == 200, _approve(api, story_id, "assets:1").text

    page = _episode(api, story_id)
    line = next(item for item in page["assets"]["lines"] if item["line_id"] == "l08")
    assert line["take"] == entry["take"]
    assert line["note"] == "plus fort"
    assert line["pending"] is False


def test_a_line_voiced_by_the_plain_assets_step_then_edited_still_shows_has_audio(api, episodes):
    """Browser-check finding (plan 11 stage 11, fix round 2): a line voiced
    by the assets step (never regenerated, so it has no ``take``) whose
    words are then edited answers ``voiced: false`` (assets.is_measured: the
    text hash no longer matches) with its audio file still on disk -- the
    common case, and exactly what stage 13's walk would hit on every edited
    line. The dashboard needs ``has_audio`` to tell it apart from a line
    that was never voiced at all (both otherwise look identical: ``voiced``
    false, ``take`` null)."""
    story_id = episode(api, episodes, "rendered")

    before = next(item for item in _episode(api, story_id)["assets"]["lines"] if item["line_id"] == "l08")
    assert before["voiced"] is True and before["take"] is None
    assert before["has_audio"] is True  # a currently-measured line always has its audio on disk

    response = _patch_script(api, story_id, 1, lines=[{"line_id": "l08", "text": tre.NEW_WORDS}])
    assert response.status_code == 200, response.text

    page = _episode(api, story_id)
    line = next(item for item in page["assets"]["lines"] if item["line_id"] == "l08")
    assert line["voiced"] is False and line["take"] is None  # never regenerated: no take either
    assert line["has_audio"] is True, "the file line_08.mp3/.wav is still on disk; the edit only cleared the pointer"


def test_a_never_voiced_line_has_no_audio(api, episodes):
    story_id = episode(api, episodes, "planned")  # before the assets step ever ran

    page = _episode(api, story_id)
    assert page["assets"]["doc"] is None
    for line in page["assets"]["lines"]:
        assert line["voiced"] is False and line["take"] is None
        assert line["has_audio"] is False, line


def test_has_audio_is_cached_like_every_other_derived_field(api, episodes, monkeypatch):
    """The view is memoised while nothing that feeds it moves (_derived_key,
    stage 9); has_audio reads two paths _derived_key did not stamp before
    (voice_lines.audio_path_candidates, independent of timing.audio) -- a
    stray file's own appearance must still invalidate the cache."""
    from clipping.aistory import workflow

    story_id = episode(api, episodes, "rendered")
    _patch_script(api, story_id, 1, lines=[{"line_id": "l08", "text": tre.NEW_WORDS}])

    calls = {"n": 0}
    real = workflow._derive

    def counted(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(workflow, "_derive", counted)

    first = _episode(api, story_id)
    line = next(item for item in first["assets"]["lines"] if item["line_id"] == "l08")
    assert line["has_audio"] is True
    settled = calls["n"]

    assert _episode(api, story_id) == first and calls["n"] == settled  # nothing moved: not derived again

    # The audio file that made has_audio true disappears: the next poll must
    # notice (the cache key must stamp the candidate paths, not only
    # timing.audio's, which this edit already cleared).
    from clipping.aistory import workflow as wf

    ec = wf._context(api.store, story_id, 1)
    from clipping.aistory.steps import voice_lines as voice_lines_step

    removed = False
    for path in voice_lines_step.audio_path_candidates(ec, "l08"):
        if path and Path(path).is_file():
            Path(path).unlink()
            removed = True
    assert removed, "l08's audio file was not found on disk to remove"

    after = _episode(api, story_id)
    assert calls["n"] > settled
    line = next(item for item in after["assets"]["lines"] if item["line_id"] == "l08")
    assert line["has_audio"] is False


def test_a_motion_swap_through_the_api_keeps_the_image_and_only_moves_the_render_key(api, episodes):
    story_id = episode(api, episodes, "rendered")
    board_before = _board(api, story_id)
    shot_before = next(s for s in board_before["shots"] if s["shot_id"] == "sh05")

    response = _patch_storyboard(api, story_id, 1, shots=[{"shot_id": "sh05", "camera_motion": "pan_rl"}])

    assert response.status_code == 200, response.text
    page = response.json()
    shot_after = next(s for s in page["storyboard"]["shots"] if s["shot_id"] == "sh05")
    assert shot_after["camera_motion"] == "pan_rl"
    assert shot_after["assets"]["image"] == shot_before["assets"]["image"]  # the image is kept

    assert _approve(api, story_id, "storyboard:1").status_code == 200

    body = _estimate(api, story_id, "rerender", ep=1)
    assert body["rebuild"] == ["sh05"] and body["reasons"]["sh05"] == "motion"


def test_a_framing_edit_refuses_the_rerender_naming_the_shot_everywhere(api, episodes):
    """The render refusal (stage 7's ``render.require_renderable``) is the
    same sentence wherever a caller reaches it: the route, the estimate and
    the episode page's ``changes`` block (never a 500 there)."""
    story_id = episode(api, episodes, "rendered")

    response = _patch_storyboard(api, story_id, 1, shots=[{"shot_id": "sh04", "framing": "low_angle"}])
    assert response.status_code == 200, response.text
    assert _approve(api, story_id, "storyboard:1").status_code == 200

    route = _post_step(api, story_id, "rerender", ep=1)
    estimate = _estimate_response(api, story_id, "rerender", ep=1)
    view = _episode(api, story_id)["render"]["changes"]

    assert route.status_code == 409 and estimate.status_code == 409
    for detail in (route.json()["detail"], estimate.json()["detail"], view["blocked"]):
        assert "sh04" in detail and "out of date" in detail
    assert api.jobs.list_jobs() == []


# ==================================================================== 5. RC-M9

def test_the_new_routes_answer_with_no_authorization_header(api, episodes):
    """RC-M9: with ``API_TOKEN`` unset, every new route is open -- never a
    401 -- with no ``Authorization`` header sent at all (the ``api``
    fixture's client sends none; a 401 could only come from
    ``require_token``, nothing else these routes do)."""
    story_id = episode(api, episodes, "rendered")

    for response in (
            api.client.get(_url(story_id, "/episodes/1")),
            _estimate_response(api, story_id, "rerender", ep=1),
            _post_step(api, story_id, "rerender", ep=1),
    ):
        assert response.status_code != 401, response.text
