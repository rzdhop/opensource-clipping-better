"""The first-watch judge, version 2: blocking and minor issues, the re-check
after a repair pass, the fitted repair notes and the props a repair lists
(the fast-track fix after the phase 7 follow-up wave; DEC-248).

The human's fast track stopped at the script on J1 version 1: asked for
"at most 6" issues it found 6 every time (a 3-6 s hook asked to explain the
premise, the cliffhanger's reveal called "unintroduced"), any issue failed
the check, each J1 after a repair pass was a fresh critique, and four calls
a pass never reached the late scenes. Version 2:

- J1 is told the format (the episode's length and spoken words, what a
  scene's summary is, the questions a serial keeps open on purpose) and
  gives each issue a severity; the report passes when no issue is
  blocking. The minor ones are kept for the human (the episode review), are
  never repaired, and neither the approval nor the fast track stops on them;
- after a repair pass J1 is a re-check: it is shown the blocking issues the
  pass tried, and a blocking issue it names that is not one of them is kept
  as minor, so the blocking set can only shrink;
- a report of version 1 on a script not approved yet is judged again; an
  approved script keeps the report it was approved on;
- a repair note is fitted to the pack's 60-word note cap by shortening J1's
  fixes, never the app's asks;
- an ``object_unseen`` naming a story prop the scene does not list gets it
  listed on the scene for its rewrite (``props_added``); an object that is
  not one of the story's props is never added.

The story and the fake LLM are phase 3's (``tests/test_story_episode_steps.py``);
offline and hermetic. The modules are imported inside the tests, so on the
parent commit each test fails on its own.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import pytest

import test_story_episode_steps as eps
from test_story_episode_steps import hermetic, store  # noqa: F401 -- phase 3's fixtures, used as they are

NOW = eps.NOW
LONG = eps.e2_v2_reply


def _wf():
    from clipping.aistory import workflow

    return workflow


def _prompts():
    from clipping.aistory import prompts

    return prompts


def _judge():
    from clipping.aistory.steps import judge

    return judge


def _script_step():
    from clipping.aistory.steps import script

    return script


def _issue(sid, kind, fix, severity="blocking"):
    return {"scene_id": sid, "kind": kind, "severity": severity, "fix": fix}


def _j1(*issues):
    """A J1 reply (J1_PASSED's take-aways) finding *issues*, passed when none blocks."""
    return dict(eps.J1_PASSED, passed=not any(issue["severity"] == "blocking" for issue in issues),
                issues=list(issues))


def _notes(llm, prompt_id, start=0):
    notes = []
    for call in llm.of(prompt_id)[start:]:
        marker = "Follow the author's note: "
        user = call["user"]
        notes.append(user.split(marker, 1)[1].split("\n\n", 1)[0] if marker in user else None)
    return notes


MINOR = _issue("s03", "unintroduced", "Dire que Broccolia est la juge du vote.", severity="minor")


# ============================================================ the prompt and its reply

def test_j1_v2_is_told_the_format_and_asks_a_severity_per_issue(store):
    prompts = _prompts()
    story_id = eps._ready_story(store, v2=True)
    llm = eps._script_llm(v2=True, E4=[eps.E4_PASSED])
    eps._run(eps._new().script, store, story_id, llm=llm)

    call = llm.of("J1")[0]
    user = call["user"]
    script = eps._script(store, story_id)
    words = sum(len(line["text"].split()) for scene in script["scenes"] for line in scene["lines"])
    assert user.startswith(f"The format: about {round(script['timing']['total_s'])} s and {words} spoken words in "
                           "all. Under a scene's header, its first line is what is on screen, then what is heard.")
    assert "the cliffhanger's reveal (someone or something first seen there is its point)" in user
    assert "A detail the format has no room for is minor at most." in user
    assert "- passed: true exactly when no issue is blocking" in user
    assert ("Severity: blocking when, without the fix, a first-time viewer cannot follow who the main character "
            "is") in user
    assert "earlier check" not in user  # a first check, not a re-check
    issue = call["schema"]["properties"]["issues"]["items"]
    assert issue["properties"]["severity"]["enum"] == ["blocking", "minor"] and "severity" in issue["required"]
    # The verdict comes after the issues, so it is decided once they are listed.
    assert list(call["schema"]["properties"])[-2:] == ["issues", "passed"]
    assert prompts.J1_PROMPT_VERSION == 2
    assert script["first_watch"]["version"] == 2


def test_validate_j1_passes_exactly_when_no_issue_is_blocking():
    prompts = _prompts()
    ids = ["s01", "s02"]
    minor = _j1(_issue("s02", "unclear_goal", "Dire ce que veut Ana.", severity="minor"))
    assert minor["passed"] is True and prompts.validate_j1(minor, scene_ids=ids) == []
    assert prompts.validate_j1(dict(minor, passed=False), scene_ids=ids) == [
        "$.passed: False does not agree with 0 blocking issue(s)"]
    blocking = _j1(_issue("s02", "unclear_goal", "Dire ce que veut Ana."))
    assert prompts.validate_j1(dict(blocking, passed=True), scene_ids=ids) == [
        "$.passed: True does not agree with 1 blocking issue(s)"]
    missing = dict(blocking, issues=[{"scene_id": "s02", "kind": "unclear_goal", "fix": "x"}])
    assert any("severity" in error for error in prompts.validate_j1(missing, scene_ids=ids))


# ============================================================ minor issues

def test_minor_issues_pass_are_never_repaired_and_approve_without_anyway(store):
    wf = _wf()
    story_id = eps._ready_story(store, v2=True)
    llm = eps._script_llm(v2=True, E4=[eps.E4_PASSED], J1=[_j1(MINOR)])

    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)

    assert llm.prompts() == ["E1v2"] + ["E2v2"] * 8 + ["E3v2", "E4", "J1"]  # no repair pass
    script = eps._script(store, story_id)
    report = script["first_watch"]
    assert report["passed"] is True and report["issues"] == [MINOR] and "repairs" not in script
    assert summary["first_watch"] is True and summary["repairs"] is None
    assert "👀 First watch: passed (1 minor issue)" in log
    assert _judge().first_watch_state(script) == "passed"
    approved = wf.approve_script(store, story_id, 1, now=NOW)
    assert approved["approved_at"] == NOW and approved["approved_anyway"] is None

    # The episode review keeps them for the human to read.
    story = store.get(story_id)
    page = wf.episode_view(store, story, 1)
    page.update(wf.episode_outputs(store, story, 1))
    review = wf.episode_review(page)
    assert review["script_minor_issues"] == [{"scene_id": "s03", "kind": "unintroduced",
                                              "fix": "Dire que Broccolia est la juge du vote."}]


def test_the_fast_track_approves_over_minor_issues_and_names_them_never_over_blocking_ones():
    from clipping.aistory.steps import fast_track

    base = {"scenes": [{"scene_id": "s01", "function": "hook", "state": "written"}], "rev": 3,
            "next_episode_teaser": "x", "cliffhanger": {"reveal": "y"},
            "consistency_report": {"passed": True, "issues": [], "checked_rev": 3, "stale": False},
            "timing": {"state": "ok", "total_s": 62.0, "window_s": [55, 75], "measured_lines": 0,
                       "estimated_lines": 2, "flags": []}}
    report = {"who_wants_what": "a", "what_happens": "b", "why_it_matters": "c", "passed": True,
              "issues": [MINOR], "checked_rev": 3, "checked_at": NOW, "stale": False, "version": 2}
    minor = dict(base, first_watch=report)
    assert fast_track.script_refusal(minor, 1, v2=True) is None
    assert fast_track.script_detail(minor, v2=True).startswith(
        "complete, consistency passed, first watch passed with 1 minor issue kept for review (s03 (unintroduced): "
        "Dire que Broccolia est la juge du vote), ⏱ 62.0 s")
    assert fast_track.script_detail(dict(base, first_watch=dict(report, issues=[])), v2=True).startswith(
        "complete, consistency passed, first watch passed, ⏱")
    assert fast_track.script_detail(base).startswith("complete, consistency passed, ⏱")  # a legacy story's line

    blocking = dict(base, first_watch=dict(report, passed=False, issues=[
        _issue("s02", "unmotivated", "Dire pourquoi Kiwilo avoue."), MINOR]))
    message = fast_track.script_refusal(blocking, 1, v2=True)
    assert message.startswith("Episode 1's first-watch check (J1) found 1 blocking issue: s02 (unmotivated): Dire "
                              "pourquoi Kiwilo avoue (1 minor issue kept for review). The fast track never approves "
                              "over blocking issues")
    assert "Broccolia" not in message


# ============================================================ the re-check

def test_the_recheck_is_shown_what_the_pass_tried_and_keeps_only_those_blocking(store):
    """Pass 1 repairs s02 and s05; the re-check names s05 again (still
    blocking), a new blocking issue on s07 (kept as minor) and a minor one;
    pass 2 repairs s05 alone, and its re-check passes: the loop converges
    instead of chasing a new critique each time."""
    story_id = eps._ready_story(store, v2=True)
    first = _j1(_issue("s02", "unclear_goal", "Dire ce que veut Kiwilo."),
                _issue("s05", "unmotivated", "Montrer pourquoi Kiwilo avoue son plan."))
    second = _j1(_issue("s05", "unmotivated", "Montrer pourquoi Kiwilo avoue encore."),
                 _issue("s07", "unclear_goal", "Dire ce que veut Broccolia."),
                 MINOR)
    third = _j1(_issue("s07", "unclear_goal", "Dire ce que veut Broccolia."))
    llm = eps._script_llm(v2=True, E2=[LONG] * 14, E4=[eps.E4_PASSED] * 3, J1=[first, second, third])

    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)

    base = ["E1v2"] + ["E2v2"] * 8 + ["E3v2", "E4", "J1"]
    assert llm.prompts() == base + ["E2v2", "E2v2", "E4", "J1", "E2v2", "E4", "J1"]
    first_call, recheck, last = llm.of("J1")
    assert "earlier check" not in first_call["user"]
    # Each earlier fix is cut to J1_RECHECK_FIX_MAX_WORDS (5) words.
    assert ("Blocking issues an earlier check found, their scenes since written again:\n- s02 (unclear_goal): Dire "
            "ce que veut Kiwilo.\n- s05 (unmotivated): Montrer pourquoi Kiwilo avoue son…\nKeep blocking only those "
            "still there (same scene_id and kind); anything else is minor.\n\n") in recheck["user"]
    assert "- s05 (unmotivated): Montrer pourquoi Kiwilo avoue encore." in last["user"]
    assert "s07" not in last["user"].split("earlier check", 1)[1].split("Watch this episode", 1)[0]

    script = eps._script(store, story_id)
    report = script["first_watch"]
    # The re-check's s07 was not tried, so it stays minor -- in the third report too.
    assert report["passed"] is True and report["issues"] == [
        _issue("s07", "unclear_goal", "Dire ce que veut Broccolia.", severity="minor")]
    assert [(r["pass"], [e["scene_id"] for e in r["scenes"]], r["issues_before"], r["issues_after"])
            for r in summary["repairs"]] == [(1, ["s02", "s05"], 2, 1), (2, ["s05"], 1, 0)]
    assert "👀 First watch after repair: 1 blocking issue remains" in log
    assert "👀 First watch after repair: passed (1 minor issue)" in log
    assert summary["first_watch"] is True


# ============================================================ older reports

def test_a_version_1_report_is_judged_again_unless_the_script_is_approved(store):
    wf = _wf()
    judge = _judge()
    story_id = eps._ready_story(store, v2=True)
    eps._run(eps._new().script, store, story_id, llm=eps._script_llm(v2=True, E4=[eps.E4_PASSED]))
    script = eps._script(store, story_id)
    # The human's episode: a version-1 report, every issue blocking, two repair passes spent.
    script["first_watch"].update(passed=False, issues=[{"scene_id": "s08", "kind": "unintroduced",
                                                        "fix": "Présenter la juge avant le cliffhanger."}])
    del script["first_watch"]["version"]
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    script = eps._script(store, story_id)
    assert judge.needs_first_watch(script) and judge.first_watch_state(script) == "stale"
    for anyway in (False, True):  # never approvable, as a stale report
        with pytest.raises(wf.WorkflowError) as caught:
            wf.approve_script(store, story_id, 1, approve_anyway=anyway, now=NOW)
        assert str(caught.value) == ("Episode 1's first-watch check was made by an older version of the judge: "
                                     "check it again (run the script step), then approve.")

    # Continue: J1 version 2 judges it again (one call) and the episode goes on.
    llm = eps.FakeLLM(J1=[_j1(MINOR)])
    summary, _log = eps._run(eps._new().script, store, story_id, llm=llm)
    assert llm.prompts() == ["J1"] and summary["first_watch"] is True
    report = eps._script(store, story_id)["first_watch"]
    assert report["version"] == 2 and report["issues"] == [MINOR]

    # An approved script keeps the report it was approved on: never stale for its version, never asked again.
    approved = eps._ready_story(store, v2=True)
    eps._run(eps._new().script, store, approved, llm=eps._script_llm(v2=True, E4=[eps.E4_PASSED]))
    wf.approve_script(store, approved, 1, now=NOW)
    script = eps._script(store, approved)
    del script["first_watch"]["version"]
    store.write_episode_doc(approved, 1, "script.json", script, now=NOW)
    assert not judge.needs_first_watch(eps._script(store, approved))
    again = eps.FakeLLM()
    eps._run(eps._new().script, store, approved, llm=again)
    assert again.prompts() == []


# ============================================================ the repairs

def test_an_object_unseen_lists_the_story_prop_on_the_scene_never_an_object_of_its_own(store):
    """s05 turns on the coconut phone, a prop of the story that s05 does not
    list: it is listed on s05 for its rewrite (E2v2 is told it is present),
    and s04 -- the nearest earlier scene listing it -- shows it too. s06's
    cufflinks are no prop of the story: nothing is added to the library or
    the scene; the rewrite shows them in its lines."""
    story_id = eps._ready_story(store, v2=True)
    first = _j1(_issue("s05", "object_unseen", "Montrer le téléphone en noix de coco avant qu'il ne sonne."),
                _issue("s06", "object_unseen", "Montrer les boutons de manchette avant le verrouillage."))
    llm = eps._script_llm(v2=True, E2=[LONG] * 13, E4=[eps.E4_PASSED] * 2, J1=[first, eps.J1_PASSED])
    props_before = sorted(store.list_entities(story_id, "props"), key=lambda doc: doc["prop_id"])

    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)

    scenes = summary["repairs"][0]["scenes"]
    assert [(entry["scene_id"], entry.get("props_added")) for entry in scenes] == [
        ("s04", None), ("s05", [eps.PHONE]), ("s06", None)]
    script = eps._script(store, story_id)
    assert eps._scene(script, "s05")["props"] == [eps.PHONE]
    assert eps._scene(script, "s06")["props"] == []
    assert sorted(store.list_entities(story_id, "props"), key=lambda doc: doc["prop_id"]) == props_before
    s05_call = llm.of("E2v2")[9]
    assert "Props present:\n" in s05_call["user"] and "Téléphone en noix de coco" in s05_call["user"]
    assert "🩹 Scene s05 now shows Téléphone en noix de coco" in log
    assert script["first_watch"]["passed"] is True


def test_a_failed_repair_unlists_the_prop_it_listed(store):
    from clipping.providers.errors import ProviderError

    story_id = eps._ready_story(store, v2=True)
    first = _j1(_issue("s05", "object_unseen", "Montrer le téléphone en noix de coco avant qu'il ne sonne."))
    down = ProviderError("down", [("gemini/gemini-test", "HTTP 503")])
    llm = eps._script_llm(v2=True, E2=[LONG] * 8 + [LONG, down], E4=[eps.E4_PASSED] * 2, J1=[first, eps.J1_PASSED])

    summary, _log = eps._run(eps._new().script, store, story_id, llm=llm)

    assert summary["repairs"][0]["failed"] == ["s05"]
    assert eps._scene(eps._script(store, story_id), "s05")["props"] == []


def test_a_repair_note_is_fitted_to_the_note_cap_by_its_fixes_never_its_asks():
    from types import SimpleNamespace

    script_step = _script_step()
    ec = SimpleNamespace(narrator=False, names={"c1": "Ana", "c2": "Bo"}, entities={"props": {}})
    script = {"scenes": [
        {"scene_id": "s01", "function": "setup", "characters": ["c1", "c2"], "props": []},
        {"scene_id": "s02", "function": "rising", "characters": ["c1", "c2"], "props": []},
    ]}
    long_fix = " ".join(["mot"] * 29) + " Bo."
    issues = [_issue("s02", "unclear_goal", long_fix), _issue("s02", "unintroduced", "Dire qui est Bo, " + long_fix),
              _issue("s02", "unmotivated", "Pourquoi.")]

    plan = script_step.repair_plan(ec, script, issues)

    assert [entry["scene_id"] for entry in plan] == ["s01", "s02"]
    for entry in plan:
        assert len(entry["note"].split()) <= script_step.REPAIR_NOTE_MAX_WORDS == 60
    # The asks are whole; J1's fixes are shortened, the short one kept whole.
    assert plan[0]["note"].endswith("Bo is in this scene, before s02: say their name and who they are here.")
    assert plan[0]["note"].startswith("First-watch check -- Unintroduced in scene s02: Dire qui est Bo,")
    note = plan[1]["note"]
    assert note.startswith("First-watch check -- Unclear goal: mot") and "Unmotivated: Pourquoi." in note
    assert "… Unintroduced: Dire qui est Bo," in note
    # A note within the cap is the plain one, as before.
    short = script_step.repair_plan(ec, script, [_issue("s02", "unmotivated", "Pourquoi.")])
    assert short[0]["note"] == "First-watch check -- Unmotivated: Pourquoi."


# ============================================================ the calibration tool

def test_the_calibration_tool_judges_a_copy_and_counts_the_verdicts(store, monkeypatch, capsys):
    """``tools/j1_calibrate.py`` (the human runs it where the keys and the
    story live): J1 on a copy of the script, once a run, the verdicts
    counted -- the script on disk never moves."""
    import importlib

    tool = importlib.import_module("tools.j1_calibrate")
    for name, value in eps.SETTINGS.items():
        monkeypatch.setenv(name, value)
    story_id = eps._ready_story(store, v2=True)
    eps._run(eps._new().script, store, story_id, llm=eps._script_llm(v2=True, E4=[eps.E4_PASSED]))
    before = eps._script(store, story_id)
    llm = eps.FakeLLM(J1=[_j1(MINOR), _j1(_issue("s05", "unmotivated", "Montrer pourquoi Kiwilo avoue."))])

    code = tool.main(["--story", story_id, "--ep", "1", "--runs", "2", "--outputs-dir", store.outputs_dir],
                     runner=llm)

    out = capsys.readouterr().out
    assert code == 0 and llm.prompts() == ["J1", "J1"]
    assert "Stored report: PASSED -- 0 blocking, 0 minor (J1 version 2)" in out
    assert "Run 1 (" in out and "): PASSED -- 0 blocking, 1 minor" in out
    assert "    - [minor] s03 (unintroduced): Dire que Broccolia est la juge du vote." in out
    assert "): FAILED -- 1 blocking, 0 minor" in out
    assert "    passed: 1 of 2" in out and "    unintroduced (minor): 1" in out
    assert eps._script(store, story_id) == before
