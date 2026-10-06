"""``POST /api/stories/{id}/switch-pipeline`` (the human, 2026-10-02: "make a
Regen button for episodes when: 'This story cannot move to the v2 (quality)
pipeline: episode 1 already has a script ...'").

- ``PATCH /api/stories/{id}``'s refusal of a pipeline switch over written
  episodes is structured -- ``{"message", "code":
  "pipeline_switch_has_scripts", "episodes"}`` -- so the dashboard can offer
  the button; ``message`` is the sentence it always showed, now pointing at
  the button.
- ``switch-pipeline`` with ``regenerate_episodes`` archives those episodes
  (``workflow.switch_pipeline``) and switches the story; 409 while a step of
  the story is queued or running (nothing archived); the step jobs left
  awaiting an approval of an archived document are completed, stamped
  ``discarded`` with the archive; then the story-level step a v2 story
  still needs (``workflow.next_v2_step``: the cast, the places, the
  knowledge base) is queued as ``POST /steps/{step}`` would queue it -- or,
  when its gate refuses, the switch stands and ``next_step.refused`` says
  why.

The fixtures are the phase-2 API tests' own (``tests/test_stories_api_phase2.py``).
The routes need pydantic, fastapi and httpx and skip without them (DEC-012).
"""

from __future__ import annotations

import os

from test_stories_api_phase2 import (  # noqa: F401 -- api and hermetic are fixtures
    BASE,
    _new_character,
    _settings,
    _story,
    _url,
    api,
    hermetic,
)

V2 = {"pipeline": "v2"}
# A v2 story never draws its references on a draft link (pollinations): a local
# one serves the free profile's sheets (a fake; nothing runs here).
LOCAL = dict(BASE, IMAGE_CHAIN="local/comfyui", IMAGE_EDIT_CHAIN="local/comfyui")


def _written(api, settings=LOCAL):
    """A legacy story (style approved, one character not written yet) whose
    episode 1 has a script -- one that cannot even be read counts
    (``workflow.episodes_with_script``) -- and proposals for episode 2."""
    _settings(api, settings)
    story_id = _story(api.store)
    _new_character(api.store, story_id, "char_kiwilo", "Kiwilo")
    for ep, name in ((1, "script.json"), (2, "proposals.json")):
        path = os.path.join(api.store.episode_dir(story_id, ep, create=True), name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{}")
    return story_id


def _awaiting(api, story_id, step, ep=None, params=None):
    from web.api.models import JobStatus

    job_id = api.jobs.create_job(kind="story_step", story_id=story_id, step=step, ep=ep, params=params)
    api.jobs.set_status(job_id, JobStatus.AWAITING_APPROVAL)
    return job_id


def _switch(api, story_id, profile=V2, regenerate=True):
    return api.client.post(_url(story_id, "/switch-pipeline"),
                           json={"generation_profile": profile, "regenerate_episodes": regenerate})


def test_the_patch_answers_a_structured_refusal_naming_the_episodes(api):
    story_id = _written(api)

    response = api.client.patch(_url(story_id), json={"generation_profile": V2})

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "pipeline_switch_has_scripts" and detail["episodes"] == [1]
    assert detail["message"].startswith("This story cannot move to the animated (quality) format: episode 1 already has "
                                        "a script")  # DEC-305 section 9: plain words
    assert "Regenerate the episode on the animated format" in detail["message"]


def test_without_regenerating_the_switch_answers_the_same_refusal(api):
    story_id = _written(api)
    response = _switch(api, story_id, regenerate=False)
    assert response.status_code == 409 and response.json()["detail"]["episodes"] == [1]
    assert api.store.list_episodes(story_id) == [1, 2]


def test_the_switch_waits_for_a_step_of_the_story_in_flight_and_archives_nothing(api):
    story_id = _written(api)
    api.jobs.create_job(kind="story_step", story_id=story_id, step="cast", params={})  # queued

    response = _switch(api, story_id)

    assert response.status_code == 409, response.text
    assert "Story step 'cast'" in response.json()["detail"] and "is queued" in response.json()["detail"]
    assert api.store.list_episodes(story_id) == [1, 2]
    assert "pipeline" not in api.store.get(story_id)["generation_profile"]


def test_the_switch_archives_the_episode_settles_its_waiting_jobs_and_queues_the_cast(api):
    story_id = _written(api)
    script_job = _awaiting(api, story_id, "script", ep=1)
    # propose-next of episode 1 wrote episode 2's proposals (archived with episode 1).
    proposals_job = _awaiting(api, story_id, "propose-next", ep=1)
    other_job = _awaiting(api, story_id, "places_proposal")

    response = _switch(api, story_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["story"]["generation_profile"]["pipeline"] == "v2"
    [report] = body["discarded"]
    assert report["ep"] == 1 and report["proposals_archived"] == [2]
    assert api.store.list_episodes(story_id) == []
    # The jobs that waited on an archived document are settled, stamped with the archive.
    assert sorted(body["jobs_cleared"]) == sorted([script_job, proposals_job])
    for job_id in (script_job, proposals_job):
        job = api.jobs.get_job(job_id)
        assert job["status"] == "completed" and job["discarded"] == report["archive"]
        assert "approved_at" not in job
    assert api.jobs.get_job(other_job)["status"] == "awaiting_approval"
    shown = api.client.get(f"/api/jobs/{script_job}").json()
    assert shown["discarded"] == report["archive"] and shown["approved_at"] is None
    # The character has no dossier and no look: the cast step is queued, as POST /steps/cast queues it.
    step = body["next_step"]
    assert step["step"] == "cast" and step["refused"] is None
    assert step["job"]["step"] == "cast" and step["job"]["status"] == "queued"
    assert api.submitted == [step["job"]["id"]]
    # The proposals of the episode that is gone are no longer shown as approved.
    page = api.client.get(_url(story_id)).json()
    assert all(not row["proposals_approved"] for row in page["series"])


def test_proposals_approved_before_the_switch_are_not_shown_approved_once_archived(api):
    from clipping.aistory import schemas

    story_id = _written(api)
    arc = {"$schema": "season_arc_v1", "episodes_planned": 3,
           "arc": [{"ep": ep, "function": "setup", "summary": "Un épisode.", "open_hooks_in": [],
                    "open_hooks_out": [], "characters": []} for ep in (1, 2, 3)],
           "series_memory": {"recaps": {}, "open_hooks": [], "relationship_state": {}, "introduced": {}},
           "audience_feedback": [], "approved_at": None, "updated_at": "2026-10-02T10:00:00+00:00"}
    assert schemas.season_arc_errors(arc) == []
    api.store.write_doc(story_id, "season.json", arc, now="2026-10-02T10:00:00+00:00")
    approved = _awaiting(api, story_id, "propose-next", ep=1)
    assert api.jobs.approve_step_job(approved) == "ok"

    assert _switch(api, story_id).status_code == 200

    series = api.client.get(_url(story_id)).json()["series"]
    assert series[1]["ep"] == 2 and series[1]["proposals"] is None
    assert series[1]["proposals_approved"] is False


def test_a_next_step_its_gate_refuses_leaves_the_switch_done_and_says_why(api):
    story_id = _written(api, {key: value for key, value in LOCAL.items() if key != "GOOGLE_API_KEY"})

    response = _switch(api, story_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["story"]["generation_profile"]["pipeline"] == "v2" and api.store.list_episodes(story_id) == []
    assert body["next_step"]["step"] == "cast" and body["next_step"]["job"] is None
    assert "None of the writing services has an API key" in body["next_step"]["refused"]
    assert api.submitted == []


def test_a_switch_with_nothing_left_to_run_queues_nothing(api):
    _settings(api, LOCAL)
    story_id = _story(api.store)  # no character, no place: nothing a v2 story needs again

    response = _switch(api, story_id)

    assert response.status_code == 200, response.text
    assert response.json()["discarded"] == [] and response.json()["next_step"] is None
    assert response.json()["jobs_cleared"] == [] and api.submitted == []
