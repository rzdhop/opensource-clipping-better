"""The script step's check-only run (plan 19 stage 3, F6).

The live walk: once the human edited a line, the approval refused the
script ("the consistency check is out of date"), and the only way to check
it again was the script step -- whose repair passes then rewrote the scenes
the checks named, the human's edit among them. ``params.check_only`` runs
the checks alone on the script as it stands:

- E4 and, on a v2 story, J1 -- each when its report is missing or stale --
  never the writing, the fill pass or the repair pass: no scene's ``rev``,
  ``source`` or text moves;
- a script not complete yet is refused (a run that writes nothing never
  fills a stub), by the runner and -- before any job exists -- by the API
  (409) and the workflow;
- ``measure_voices`` with it is refused (400);
- ``approve_script``'s stale refusal names it.

The story and the fake LLM are phase 3's (``tests/test_story_episode_steps.py``);
the API test is ``tests/test_stories_api_episode.py``'s throwaway app (it
skips without fastapi). Offline and hermetic. Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import pytest

import test_stories_api_episode as tae
import test_story_episode_steps as eps
from clipping.providers.errors import ProviderError
from test_stories_api_episode import api  # noqa: F401 -- the throwaway app (skips without fastapi)
from test_story_episode_steps import hermetic, store  # noqa: F401 -- phase 3's fixtures, used as they are

NOW = eps.NOW
LATER = "2026-09-29T09:00:00+00:00"
CHECK_ONLY = {"check_only": True}
DOWN = ProviderError("down", [("gemini/gemini-test", "HTTP 503")])
# A script stopped at s04 (its E2 failed): every other scene written.
PARTIAL_E2 = [eps.e2_reply, eps.e2_reply, DOWN] + [eps.e2_reply] * 5


def _wf():
    from clipping.aistory import workflow

    return workflow


def _script_step():
    from clipping.aistory.steps import script

    return script


def _written(script):
    """What a check must never move: each scene's revision, source, state and
    lines, the framing parts, the script's own revision and repairs."""
    return {"rev": script["rev"], "repairs": script.get("repairs"),
            "hook": script["hook"], "cliffhanger": script["cliffhanger"],
            "teaser": script["next_episode_teaser"],
            "scenes": [(scene["scene_id"], scene["rev"], scene.get("source"), scene["state"],
                        [(line["line_id"], line["speaker"], line["text"], line["emotion"]) for line in scene["lines"]])
                       for scene in script["scenes"]]}


def _refused(call, *args, **kwargs):
    wf = _wf()
    with pytest.raises(wf.WorkflowError) as caught:
        call(*args, **kwargs)
    return caught.value.code, str(caught.value)


def test_a_check_only_run_refreshes_both_reports_and_rewrites_nothing(store):
    wf, script_step = _wf(), _script_step()
    from clipping.aistory.steps import judge

    story_id = eps._ready_story(store, v2=True)
    eps._run(script_step, store, story_id, llm=eps._script_llm(v2=True, E4=[eps.E4_PASSED]))
    line = eps._scene(eps._script(store, story_id), "s02")["lines"][0]
    wf.patch_script(store, story_id, 1, {"lines": [{"line_id": line["line_id"],
                                                    "text": "Une réplique écrite à la main, sans détour."}]},
                    now=LATER)
    edited = eps._script(store, story_id)
    assert script_step.needs_check(edited) and judge.first_watch_state(edited) == "stale"
    code, message = _refused(wf.approve_script, store, story_id, 1, approve_anyway=True, now=LATER)
    assert code == "conflict" and message == (
        "Episode 1's consistency check is out of date (the script changed since it ran): check it again (run the "
        "script step with check only).")
    story_before = store.get(story_id)

    # E4 and J1 find blocking issues: a full run would repair them; a check-only run only reports them.
    llm = eps.FakeLLM(E4=[eps.E4_ISSUES], J1=[eps.J1_ISSUES])
    summary, log = eps._run(script_step, store, story_id, llm=llm, params=CHECK_ONLY)

    assert llm.prompts() == ["E4", "J1"]
    checked = eps._script(store, story_id)
    assert _written(checked) == _written(edited)
    assert eps._scene(checked, "s02")["lines"][0]["text"] == "Une réplique écrite à la main, sans détour."
    report = checked["consistency_report"]
    assert not script_step.needs_check(checked) and report["checked_rev"] == checked["rev"]
    assert report["issues"] == eps.E4_ISSUES["issues"] and report["passed"] is False
    assert judge.first_watch_state(checked) == "issues" and checked["first_watch"]["checked_rev"] == checked["rev"]
    assert summary["check_only"] is True and summary["repairs"] is None and summary["fill"] is None
    assert summary["consistency"] is False and summary["first_watch"] is False
    assert "🔍 Episode 1: check only -- the script is checked as it stands; nothing is written or repaired" in log
    assert not any(line.startswith("🩹") for line in log)
    assert store.get(story_id) == story_before  # RC-E2

    # Fresh again: the human's own call now -- approve anyway, or edit and check again.
    approved = wf.approve_script(store, story_id, 1, approve_anyway=True, now=LATER)
    assert approved["approved_at"] == approved["approved_anyway"] == LATER
    # A second check-only run has nothing to check and calls nothing.
    idle = eps.FakeLLM()
    _summary, log = eps._run(script_step, store, story_id, llm=idle, params=CHECK_ONLY)
    assert idle.prompts() == [] and "✅ Episode 1's script is checked already: nothing to check." in log
    assert eps._script(store, story_id)["approved_at"] == LATER


def test_a_check_only_run_refuses_an_incomplete_script_and_writes_nothing(store):
    wf, script_step = _wf(), _script_step()

    story_id = eps._ready_story(store)
    message, _log = eps._failed(script_step, store, story_id, llm=eps.FakeLLM(), params=CHECK_ONLY)
    assert message == ("Episode 1 has no script yet, and a check-only run writes nothing: write it first (the "
                       "script step).")
    assert eps._script(store, story_id) is None

    eps._failed(script_step, store, story_id, llm=eps._script_llm(E2=list(PARTIAL_E2)))
    partial = eps._script(store, story_id)
    assert eps._scene(partial, "s04")["state"] == "stub"
    llm = eps.FakeLLM()
    message, _log = eps._failed(script_step, store, story_id, llm=llm, params=CHECK_ONLY)
    assert message == ("Episode 1's script is not complete (s04 not written yet), and a check-only run writes "
                       "nothing: run the script step to finish it (it checks it too).")
    assert llm.prompts() == [] and eps._script(store, story_id) == partial

    # The workflow refuses the same way before any job exists (the API and the CLI ask it).
    ec = wf.episode_context(store, store.get(story_id), 1, step="script")
    code, refused = _refused(wf.require_checkable_script, ec)
    assert (code, refused) == ("conflict", message)
    # And the params: a closed list of booleans, never with measure_voices.
    assert wf.SCRIPT_PARAMS == ("measure_voices",) and wf.SCRIPT_CHECK_PARAMS == ("check_only",)
    assert wf.script_request(CHECK_ONLY) is False and wf.script_check_only(CHECK_ONLY) is True
    assert wf.script_check_only({}) is False and wf.script_check_only({"check_only": False}) is False
    assert _refused(wf.script_request, {"check_only": "yes"}) == (
        "invalid", "params.check_only is true or false, not 'yes'.")
    assert _refused(wf.script_request, {"check_only": True, "measure_voices": True}) == (
        "invalid", "A check-only run measures nothing: send check_only without measure_voices, or measure the "
                   "voices in a run of their own.")


def test_the_api_takes_check_only_and_refuses_an_incomplete_script_before_any_job(api):
    story_id = tae._ready_story(api.store)
    response = tae._post_step(api, story_id, "script", ep=1, params=CHECK_ONLY)
    assert response.status_code == 409, response.text
    assert response.json()["detail"].startswith("Episode 1 has no script yet, and a check-only run writes nothing")
    response = tae._post_step(api, story_id, "script", ep=1, params={"check_only": True, "measure_voices": True})
    assert response.status_code == 400 and "measures nothing" in response.json()["detail"]
    assert api.jobs.list_jobs() == [] and api.submitted == []

    job = tae._post_step(api, story_id, "script", ep=1).json()
    assert tae._run(api, job["id"], tae._script_llm(E4=[tae.E4_PASSED]))["status"] == "awaiting_approval"
    response = tae._post_step(api, story_id, "script", ep=1, params=CHECK_ONLY)
    assert response.status_code == 201, response.text
    assert (response.json()["step"], response.json()["params"]) == ("script", CHECK_ONLY)
    # The report is current: the job checks nothing, writes nothing, and ends awaiting approval.
    ended = tae._run(api, response.json()["id"], tae.FakeLLM())
    assert ended["status"] == "awaiting_approval", ended.get("error")


def test_the_cli_step_takes_check_only_for_the_script_alone():
    import argparse

    from clipping.aistory import cli

    parser = cli.build_parser()
    step = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices["step"]
    assert next(a for a in step._actions if "--check-only" in a.option_strings).dest == "check_only"
    assert ("check_only", "--check-only", ("script",)) in cli._STEP_ONLY
    fast_track = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices["fast-track"]
    assert next(a for a in fast_track._actions
                if "--stop-on-script-issues" in a.option_strings).dest == "stop_on_script_issues"
