"""The script step's repair pass: what the first-watch check (J1) finds is
written again by the step itself (AI Story phase 7 follow-up, stage G).

On a v2 story, once J1 has reported, the step rewrites the scenes its
issues name -- E2v2 for a body scene, the partial E3v2 for a framing one --
with the kind in words and the fix verbatim as the author's note, then runs
E4 and J1 again: at most 2 passes a run, each at most 4 calls, in scene
order; an ``unintroduced`` / ``object_unseen`` issue also rewrites the
nearest earlier body scene where the character is present / the prop is
listed; an issue with no scene stays in the report. What was tried is
recorded on the script (``repairs``) and in the step's result, and the
approve-script refusal says it. A legacy story and an approved script are
never repaired.

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
BASE_PROMPTS = ["E1v2"] + ["E2v2"] * 8 + ["E3v2", "E4", "J1"]
NEW_HOOK = {"lines": [{"speaker": eps.MANGELLA, "text": "Personne ne dort ce soir.", "emotion": "tension",
                       "delivery": "icy"}], "on_screen_text": "Nuit blanche au parloir"}
NEW_CLIFF = {"reveal": "Le téléphone se tait pour de bon.",
             "lines": [{"speaker": eps.MANGELLA, "text": "Personne ne sortira d'ici.", "emotion": "shocked",
                        "delivery": "cold"}]}


def _wf():
    from clipping.aistory import workflow

    return workflow


def _script_step():
    from clipping.aistory.steps import script

    return script


def _judge():
    from clipping.aistory.steps import judge

    return judge


def _issue(sid, kind, fix, severity="blocking"):
    return {"scene_id": sid, "kind": kind, "severity": severity, "fix": fix}


def _j1(*issues):
    """A J1 reply finding *issues* (J1_PASSED's take-aways)."""
    return dict(eps.J1_PASSED, passed=not issues, issues=list(issues))


def _notes(llm, prompt_id, start=0):
    """The author's note of each *prompt_id* call from *start* on (None without one)."""
    notes = []
    for call in llm.of(prompt_id)[start:]:
        marker = "Follow the author's note: "
        user = call["user"]
        notes.append(user.split(marker, 1)[1].split("\n\n", 1)[0] if marker in user else None)
    return notes


def _written(llm, start=0):
    """The scene id each E2v2 call from *start* on wrote (its stub line's
    summary is E1_REPLY's; the body scenes are s02..)."""
    summaries = {stub["summary"]: f"s{index + 1:02d}" for index, stub in enumerate(eps.E1_REPLY["scenes"])}
    ids = []
    for call in llm.of("E2v2")[start:]:
        stub = call["user"].split("Scene (", 1)[1].split("\n", 1)[0]
        ids.append(next(sid for summary, sid in summaries.items() if summary in stub))
    return ids


def _refused(call):
    wf = _wf()
    with pytest.raises(wf.WorkflowError) as caught:
        call()
    assert caught.value.code == wf.CONFLICT
    return str(caught.value)


# ============================================================ the pass

def test_j1_issues_are_repaired_with_their_fixes_as_notes_and_checked_again(store):
    """Four issues -- the hook's text, a goal, a character met unintroduced in
    s06 (Mangella: present in s04 before it), one with no scene -- give one
    pass of four calls in scene order, then E4 and J1 again, which passes."""
    wf = _wf()
    story_id = eps._ready_story(store, v2=True)
    first = _j1(_issue("s06", "unintroduced", "Dire qui est Mangella avant qu'elle parle."),
                _issue("s02", "unclear_goal", "Dire ce que veut Kiwilo."),
                _issue("s01", "no_hook_text", "Le texte à l'écran doit dire l'enjeu du vote."),
                _issue(None, "unclear_goal", "On ne sait pas ce que veut Broccolia."))
    llm = eps._script_llm(v2=True, E2=[LONG] * 11, E3=[eps.E3_FULL, {"hook": NEW_HOOK}],
                          E4=[eps.E4_PASSED, eps.E4_PASSED], J1=[first, eps.J1_PASSED])

    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)

    assert llm.prompts() == BASE_PROMPTS + ["E3v2", "E2v2", "E2v2", "E2v2", "E4", "J1"]
    # The hook (E3v2, its part alone) and the body scenes (E2v2), each with the kind in words and the fix as it is.
    hook_call = llm.of("E3v2")[1]
    assert hook_call["schema"]["required"] == ["hook"]
    assert _notes(llm, "E3v2", 1) == [
        "First-watch check -- No hook text: Le texte à l'écran doit dire l'enjeu du vote."]
    assert _notes(llm, "E2v2", 8) == [
        "First-watch check -- Unclear goal: Dire ce que veut Kiwilo.",
        "First-watch check -- Unintroduced in scene s06: Dire qui est Mangella avant qu'elle parle. Mangella is in "
        "this scene, before s06: say their name and who they are here.",
        "First-watch check -- Unintroduced: Dire qui est Mangella avant qu'elle parle.",
    ]
    assert _written(llm, 8) == ["s02", "s04", "s06"]
    assert all(note is None for note in _notes(llm, "E2v2")[:8])

    script = eps._script(store, story_id)
    assert script["hook"]["on_screen_text"] == "Nuit blanche au parloir"
    assert script["rev"] == 5  # four rewrites, each like a regenerate
    assert [eps._scene(script, sid)["rev"] for sid in ("s01", "s02", "s03", "s04", "s06")] == [2, 2, 1, 2, 2]
    # A repair keeps every scene's slot target (a regenerate's rule), unlike the fill pass: the same E1 reply
    # written with nothing to repair has the same targets.
    plain = eps._ready_story(store, v2=True)
    eps._run(eps._new().script, store, plain, llm=eps._script_llm(v2=True, E4=[eps.E4_PASSED]))
    assert [scene["target_duration_s"] for scene in script["scenes"]] == \
        [scene["target_duration_s"] for scene in eps._script(store, plain)["scenes"]]
    assert script["first_watch"]["passed"] is True and script["first_watch"]["checked_rev"] == 5
    assert script["consistency_report"]["checked_rev"] == 5 and script["approved_at"] is None
    record = [{"pass": 1, "scenes": [{"scene_id": "s01", "kinds": ["no_hook_text"], "part": "hook"},
                                     {"scene_id": "s02", "kinds": ["unclear_goal"], "part": None},
                                     {"scene_id": "s04", "kinds": ["unintroduced"], "part": None},
                                     {"scene_id": "s06", "kinds": ["unintroduced"], "part": None}],
               "issues_before": 4, "issues_after": 0, "failed": []}]
    assert script["repairs"] == record and summary["repairs"] == record
    assert summary["calls"] == 18 and summary["first_watch"] is True
    assert "🩹 Repair pass 1: 4 scenes rewritten for 4 blocking issues (s01 no hook text, s02 unclear goal, " \
           "s06 unintroduced, the episode unclear goal)" in log
    assert "👀 First watch after repair: passed" in log
    assert wf.approve_script(store, story_id, 1, now=NOW)["approved_anyway"] is None


def test_consistency_issues_are_repaired_like_first_watch_ones_and_checked_again(store):
    """DEC-260 (the episode-2 one click of 2026-10-04 stopped on six E4
    issues the step never touched): the consistency check's issues are
    repaired by the same pass -- the named scene written again with
    "Consistency check -- <kind>: <fix>" as the note -- then E4 and J1 run
    again; an issue with no scene stays in the report; the record names
    the E4 kind and the schema takes it."""
    wf = _wf()
    story_id = eps._ready_story(store, v2=True)
    llm = eps._script_llm(v2=True, E2=[LONG] * 9, E4=[eps.E4_ISSUES, eps.E4_PASSED], J1=[eps.J1_PASSED, eps.J1_PASSED])

    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)

    assert llm.prompts() == BASE_PROMPTS + ["E2v2", "E4", "J1"]
    assert _notes(llm, "E2v2", 8) == ["Consistency check -- Out of character: Broccolia parle trop gentiment ici."]
    assert _written(llm, 8) == ["s03"]
    script = eps._script(store, story_id)
    assert script["consistency_report"]["passed"] is True and script["first_watch"]["passed"] is True
    record = [{"pass": 1, "scenes": [{"scene_id": "s03", "kinds": ["character"], "part": None}],
               "issues_before": 2, "issues_after": 0, "failed": []}]
    assert script["repairs"] == record and summary["repairs"] == record
    assert "🩹 Repair pass 1: 1 scene rewritten for 2 issues (s03 out of character, the episode continuity)" in log
    assert "🔍 Consistency after repair: passed" in log
    assert wf.approve_script(store, story_id, 1, now=NOW)["approved_anyway"] is None


def test_first_watch_and_consistency_issues_on_one_scene_share_a_note_with_both_heads(store):
    """A scene both checks name is written once, its note headed by both."""
    story_id = eps._ready_story(store, v2=True)
    first = _j1(_issue("s03", "unclear_goal", "Dire ce que veut Broccolia."))
    llm = eps._script_llm(v2=True, E2=[LONG] * 9, E4=[eps.E4_ISSUES, eps.E4_PASSED], J1=[first, eps.J1_PASSED])

    eps._run(eps._new().script, store, story_id, llm=llm)

    assert _written(llm, 8) == ["s03"]
    (note,) = _notes(llm, "E2v2", 8)
    assert note.startswith("First-watch check and consistency check -- ")
    assert "Unclear goal: Dire ce que veut Broccolia." in note
    assert "Out of character: Broccolia parle trop gentiment ici." in note
    script = eps._script(store, story_id)
    assert script["repairs"][0]["scenes"] == [{"scene_id": "s03", "kinds": ["unclear_goal", "character"], "part": None}]
    assert script["repairs"][0]["issues_before"] == 3 and script["repairs"][0]["issues_after"] == 0


def test_an_object_unseen_rewrites_the_earlier_scene_that_lists_the_prop_or_the_named_scene_alone(store):
    """object_unseen on s09 (the phone: listed in s04 before it) rewrites s04
    and s09; unintroduced on s03 (Broccolia's first scene) and one whose fix
    names no character of the story rewrite the named scene alone."""
    story_id = eps._ready_story(store, v2=True)
    first = _j1(_issue("s09", "object_unseen", "Montrer le téléphone en noix de coco avant qu'il ne sonne."),
                _issue("s03", "unintroduced", "Présenter Broccolia avant qu'elle n'interroge."),
                _issue("s05", "unintroduced", "Présenter le nouveau venu."))
    llm = eps._script_llm(v2=True, E2=[LONG] * 12, E4=[eps.E4_PASSED, eps.E4_PASSED], J1=[first, eps.J1_PASSED])

    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)

    assert llm.prompts() == BASE_PROMPTS + ["E2v2"] * 4 + ["E4", "J1"]
    assert [entry["scene_id"] for entry in summary["repairs"][0]["scenes"]] == ["s03", "s04", "s05", "s09"]
    assert _written(llm, 8) == ["s03", "s04", "s05", "s09"]
    notes = _notes(llm, "E2v2", 8)
    assert notes[0] == "First-watch check -- Unintroduced: Présenter Broccolia avant qu'elle n'interroge."
    assert notes[1] == ("First-watch check -- Object unseen in scene s09: Montrer le téléphone en noix de coco avant "
                        "qu'il ne sonne. Téléphone en noix de coco is in this scene, before s09: show it on screen "
                        "here.")
    assert notes[2] == "First-watch check -- Unintroduced: Présenter le nouveau venu."
    assert notes[3] == ("First-watch check -- Object unseen: Montrer le téléphone en noix de coco avant qu'il ne "
                        "sonne.")
    assert summary["repairs"][0]["scenes"][1] == {"scene_id": "s04", "kinds": ["object_unseen"], "part": None}
    assert "🩹 Repair pass 1: 4 scenes rewritten for 3 blocking issues (s03 unintroduced, s05 unintroduced, " \
           "s09 object unseen)" in log


def test_a_repeated_line_is_rewritten_on_the_later_scene(store):
    """The deterministic repeated-line check names the later line's scene;
    the repair writes that scene again, asking for a different line."""
    story_id = eps._ready_story(store, v2=True)
    eps._run(eps._new().script, store, story_id, llm=eps._script_llm(v2=True, E4=[eps.E4_PASSED]))
    script = eps._script(store, story_id)
    s02, s05 = eps._scene(script, "s02"), eps._scene(script, "s05")
    s05["lines"][0]["text"] = s02["lines"][0]["text"]
    del script["first_watch"]
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    later, first = s05["lines"][0]["line_id"], s02["lines"][0]["line_id"]

    llm = eps.FakeLLM(E2=[LONG], E4=[eps.E4_PASSED], default={"J1": eps.J1_PASSED})
    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)

    assert llm.prompts() == ["J1", "E2v2", "E4", "J1"]
    assert _written(llm) == ["s05"]
    assert _notes(llm, "E2v2") == [
        f"First-watch check -- Repeated line: Line {later} repeats line {first} "
        f"(“{s02['lines'][0]['text']}”): rewrite one of them. Write a different line here that keeps the beat."]
    script = eps._script(store, story_id)
    assert script["first_watch"]["passed"] is True
    assert eps._scene(script, "s05")["rev"] == 2 and eps._scene(script, "s02")["rev"] == 1
    assert summary["repairs"] == [{"pass": 1, "scenes": [{"scene_id": "s05", "kinds": ["repeated_line"],
                                                           "part": None}],
                                   "issues_before": 1, "issues_after": 0, "failed": []}]
    assert "🩹 Repair pass 1: 1 scene rewritten for 1 blocking issue (s05 repeated line)" in log


# ============================================================ the bounds

def test_a_pass_is_at_most_eight_calls_and_a_run_at_most_two_passes(store):
    """Six issues -- the hook's, four characters met unintroduced (each also
    sending the nearest earlier scene they are in: s02, s04, s06, s08) and
    the cliffhanger's -- plan the ten scenes: pass 1 rewrites the first
    eight in scene order (the hook's part, then s02-s08); J1 still names two,
    pass 2 rewrites them (s09, then the cliffhanger's part); J1 still finds
    one, and the passes are spent -- the refusal says what was tried.
    DEC-248, re-pinned on purpose: four calls a pass left the late scenes --
    the cliffhanger among them -- never written again."""
    wf = _wf()
    script_step = _script_step()
    story_id = eps._ready_story(store, v2=True)
    scenes = [f"s{index:02d}" for index in range(1, 11)]
    six = _j1(_issue("s01", "unclear_goal", "Dire ce que veut Kiwilo dès le hook."),
              _issue("s03", "unintroduced", "Présenter Mangella avant qu'elle parle."),
              _issue("s05", "unintroduced", "Présenter Kiwilo avant qu'il parle."),
              _issue("s07", "unintroduced", "Présenter Mangella avant qu'elle parle."),
              _issue("s09", "unintroduced", "Présenter Kiwilo avant qu'il parle."),
              _issue("s10", "unmotivated", "Dire pourquoi le téléphone désigne Kiwilo."))
    two = _j1(_issue("s09", "unintroduced", "Présenter le nouveau venu."),
              _issue("s10", "unmotivated", "Dire pourquoi le téléphone désigne Kiwilo."))
    one = _j1(_issue("s10", "unmotivated", "Dire pourquoi le téléphone sonne ici."))
    llm = eps._script_llm(v2=True, E2=[LONG] * 18, E3=[eps.E3_FULL, {"hook": NEW_HOOK}, {"cliffhanger": NEW_CLIFF}],
                          E4=[eps.E4_PASSED] * 3, J1=[six, two, one])

    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)

    assert script_step.REPAIR_CALLS_MAX == 8 and script_step.REPAIR_PASSES_MAX == 2
    assert llm.prompts() == (BASE_PROMPTS + ["E3v2"] + ["E2v2"] * 7 + ["E4", "J1"]
                             + ["E2v2", "E3v2"] + ["E4", "J1"])
    assert [entry["scene_id"] for entry in summary["repairs"][0]["scenes"]] == scenes[:8]
    assert [(entry["scene_id"], entry["part"]) for entry in summary["repairs"][1]["scenes"]] == \
        [("s09", None), ("s10", "cliffhanger")]
    assert [(record["pass"], record["issues_before"], record["issues_after"]) for record in summary["repairs"]] \
        == [(1, 6, 2), (2, 2, 1)]
    script = eps._script(store, story_id)
    assert script["cliffhanger"]["reveal"] == NEW_CLIFF["reveal"]
    assert script["rev"] == 11 and script["first_watch"]["checked_rev"] == 11
    assert script["first_watch"]["passed"] is False
    assert script["repairs"] == summary["repairs"] and summary["first_watch"] is False
    assert "🩹 Repair pass 2: 2 scenes rewritten for 2 blocking issues (s09 unintroduced, s10 unmotivated)" in log
    assert "👀 First watch after repair: 1 blocking issue remains" in log

    message = _refused(lambda: wf.approve_script(store, story_id, 1, now=NOW))
    assert message == (
        "Episode 1's first-watch check: after 2 repair passes, 1 blocking issue remains: s10 (unmotivated): Dire "
        "pourquoi le téléphone sonne ici. A first-time viewer took away: Kiwilo veut garder le pouvoir sur l'île. "
        "Le téléphone annonce un vote surprise et désigne Kiwilo. Le perdant du vote quitte l'île. Fix them (edit "
        "the script, or regenerate the scenes they name) and check again, or approve anyway.")
    from clipping.aistory.steps import fast_track

    fast = fast_track.script_refusal(script, 1, v2=True)
    assert fast.startswith("Episode 1's first-watch check (J1): after 2 repair passes, 1 blocking issue remains: "
                           "s10 (unmotivated): Dire pourquoi le téléphone sonne ici. The fast track never approves "
                           "over blocking issues")
    assert ".." not in message and ".." not in fast


def test_a_failed_repair_keeps_the_scene_and_a_pass_that_repairs_nothing_ends_the_loop(store):
    from clipping.providers.errors import ProviderError

    def down():
        return ProviderError("down", [("gemini/gemini-test", "HTTP 503")])

    # One of two repairs fails: the scene keeps its lines, the pass goes on, E4 and J1 run again.
    story_id = eps._ready_story(store, v2=True)
    two = _j1(_issue("s02", "unclear_goal", "Dire ce que veut Kiwilo."),
              _issue("s03", "unmotivated", "Dire pourquoi Broccolia interroge."))
    llm = eps._script_llm(v2=True, E2=[LONG] * 8 + [down(), LONG], E4=[eps.E4_PASSED, eps.E4_PASSED],
                          J1=[two, eps.J1_PASSED])
    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)
    assert llm.prompts() == BASE_PROMPTS + ["E2v2", "E2v2", "E4", "J1"]
    assert summary["repairs"] == [{"pass": 1, "scenes": [{"scene_id": "s03", "kinds": ["unmotivated"], "part": None}],
                                   "issues_before": 2, "issues_after": 0, "failed": ["s02"]}]
    script = eps._script(store, story_id)
    assert eps._scene(script, "s02")["rev"] == 1 and eps._scene(script, "s03")["rev"] == 2
    assert any(line.startswith("✖ Repair pass 1: scene s02 failed") and "keeps its lines" in line for line in log)
    assert "🩹 Repair pass 1: 1 scene rewritten for 2 blocking issues (s02 unclear goal, s03 unmotivated)" in log

    # The only repair fails: nothing was repaired, so the checks are not run again and the loop ends.
    story_id = eps._ready_story(store, v2=True)
    llm = eps._script_llm(v2=True, E2=[LONG] * 8 + [down()], E4=[eps.E4_PASSED],
                          J1=[_j1(_issue("s02", "unclear_goal", "Dire ce que veut Kiwilo."))])
    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)
    assert llm.prompts() == BASE_PROMPTS + ["E2v2"]
    assert summary["repairs"] == [{"pass": 1, "scenes": [], "issues_before": 1, "issues_after": None,
                                   "failed": ["s02"]}]
    script = eps._script(store, story_id)
    assert script["first_watch"]["passed"] is False and script["first_watch"]["stale"] is False
    assert summary["first_watch"] is False and script["rev"] == 1


def test_an_issue_with_no_scene_is_left_in_the_report_without_a_pass(store):
    wf = _wf()
    story_id = eps._ready_story(store, v2=True)
    llm = eps._script_llm(v2=True, E4=[eps.E4_PASSED],
                          J1=[_j1(_issue(None, "unclear_goal", "On ne sait pas ce que veut Mangella."))])

    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)

    assert llm.prompts() == BASE_PROMPTS
    script = eps._script(store, story_id)
    assert "repairs" not in script and summary["repairs"] is None
    assert not any("Repair pass" in line for line in log)
    message = _refused(lambda: wf.approve_script(store, story_id, 1, now=NOW))
    assert message.startswith("Episode 1's first-watch check found 1 blocking issue: the episode (unclear goal): On "
                              "ne sait pas ce que veut Mangella. A first-time viewer took away:")
    assert "repair" not in message and ".." not in message


def test_an_approved_script_and_a_legacy_script_are_never_repaired(store):
    wf = _wf()
    # A v2 script approved anyway over its issues: a run makes no call.
    story_id = eps._ready_story(store, v2=True)
    llm = eps._script_llm(v2=True, E2=[LONG] * 8 + [eps.ProviderError("down", [("gemini/gemini-test", "HTTP 503")])],
                          E4=[eps.E4_PASSED], J1=[eps.J1_ISSUES])
    eps._run(eps._new().script, store, story_id, llm=llm)
    wf.approve_script(store, story_id, 1, approve_anyway=True, now=NOW)
    again = eps.FakeLLM()
    summary, log = eps._run(eps._new().script, store, story_id, llm=again)
    assert again.prompts() == [] and summary["repairs"] is None
    assert "✅ Episode 1's script is complete and checked: nothing to write." in log
    assert eps._script(store, story_id)["approved_at"] == NOW

    # A legacy story: no judge, no repair, the summary as it was.
    legacy = eps._ready_story(store)
    llm = eps._script_llm(E4=[eps.E4_PASSED])
    summary, log = eps._run(eps._new().script, store, legacy, llm=llm)
    assert llm.prompts() == ["E1"] + ["E2"] * 8 + ["E3", "E4"]
    assert "repairs" not in summary and "repairs" not in eps._script(store, legacy)
    assert not any("Repair pass" in line for line in log)


def test_the_step_budget_ends_a_pass_before_a_call_naming_the_repairs_left_and_a_rerun_repairs(store):
    """With room for the twelve writing and checking calls and none more, the
    first repair call is never started: the step ends failed naming the
    repairs and the checks left, the fresh report is kept, and a run with
    a new budget repairs."""
    m = eps._new()
    story_id = eps._ready_story(store, v2=True)
    clock = eps.Clock(0.0)
    six = _j1(*(_issue(sid, "unclear_goal", f"Dire ce que veut Kiwilo en {sid}.")
                for sid in ("s02", "s03", "s05", "s06", "s08", "s09")))
    llm = eps._script_llm(v2=True, E4=[eps.E4_PASSED], J1=[six])
    llm.clock, llm.advance = clock, 300.0
    ctx, log = eps._ctx(store, story_id)
    budget = m.common.Budget(clock, limit=12 * 300)

    with pytest.raises(eps.steps.StepFailed) as caught:
        m.script.run(ctx, runner=llm, time_fn=clock, budget=budget)

    assert llm.prompts() == BASE_PROMPTS
    message = str(caught.value)
    assert "run the step again to continue" in message
    assert message.endswith("Left: the first-watch repairs of s02, s03, s05, s06, s08 and s09, the consistency check "
                            "(E4) and the first-watch check (J1).")
    script = eps._script(store, story_id)
    assert script["first_watch"]["passed"] is False and script["first_watch"]["stale"] is False
    assert "repairs" not in script

    again = eps.FakeLLM(E2=[LONG] * 6, E4=[eps.E4_PASSED], J1=[eps.J1_PASSED])
    summary, _log = eps._run(m.script, store, story_id, llm=again)
    assert again.prompts() == ["E2v2"] * 6 + ["E4", "J1"]  # DEC-248: the six fit one pass of eight
    assert summary["first_watch"] is True and [r["pass"] for r in summary["repairs"]] == [1]


# ============================================================ the map, the record, the messages

def test_the_repair_map_skips_what_e2_cannot_write_and_names_a_framing_part():
    from types import SimpleNamespace

    script_step = _script_step()
    ec = SimpleNamespace(narrator=False, names={"c1": "Ana", "c2": "Bo"},
                         entities={"props": {"p1": {"name": "La clé"}}})
    scenes = [
        {"scene_id": "s01", "function": "hook", "characters": ["c1"], "props": []},
        {"scene_id": "s02", "function": "setup", "characters": [], "props": []},  # silent: nobody can speak
        {"scene_id": "s03", "function": "rising", "characters": ["c1", "c2"], "props": ["p1"]},
        {"scene_id": "s04", "function": "peak", "characters": ["c2"], "props": []},
        {"scene_id": "s05", "function": "cliffhanger", "characters": ["c1", "c2"], "props": ["p1"]},
    ]
    script = {"scenes": scenes}
    issues = [
        _issue("s05", "repeated_line", "Line l9 repeats line l2 (“Non.”): rewrite one of them."),
        _issue("s04", "object_unseen", "Montrer la clé avant qu'il ne la tourne."),
        _issue("s04", "unintroduced", "Dire qui est Bo."),
        _issue("s02", "unclear_goal", "Dire ce que veut Ana."),
        _issue("s01", "unclear_goal", "Dire ce que veut Ana dès le hook."),
        _issue("s99", "unmotivated", "Un identifiant inconnu."),
        _issue(None, "unmotivated", "Sans scène."),
    ]

    plan = script_step.repair_plan(ec, script, issues)

    assert [(r["scene_id"], r["part"], r["kinds"]) for r in plan] == [
        ("s01", "hook", ["unclear_goal"]),
        ("s03", None, ["object_unseen", "unintroduced"]),  # the earlier scene of both s04 issues, once
        ("s04", None, ["object_unseen", "unintroduced"]),
        ("s05", "cliffhanger", ["repeated_line"]),
    ]
    assert plan[1]["note"] == (
        "First-watch check -- Object unseen in scene s04: Montrer la clé avant qu'il ne la tourne. La clé is in this "
        "scene, before s04: show it on screen here. Unintroduced in scene s04: Dire qui est Bo. Bo is in this scene, "
        "before s04: say their name and who they are here.")
    assert plan[2]["note"] == ("First-watch check -- Object unseen: Montrer la clé avant qu'il ne la tourne. "
                               "Unintroduced: Dire qui est Bo.")
    assert plan[3]["note"].endswith("rewrite one of them. Write a different line here that keeps the beat.")
    # A name is matched as a word: "Bo" is not found in "bonjour".
    alone = script_step.repair_plan(ec, script, [_issue("s04", "unintroduced", "Dire bonjour à tous.")])
    assert [r["scene_id"] for r in alone] == ["s04"]


def test_the_repairs_record_validates_on_the_script_and_a_bad_kind_is_refused(store):
    from clipping.aistory import schemas

    story_id = eps._ready_story(store, v2=True)
    eps._run(eps._new().script, store, story_id, llm=eps._script_llm(v2=True, E4=[eps.E4_PASSED]))
    script = eps._script(store, story_id)
    assert schemas.episode_script_errors(script) == []
    script["repairs"] = [{"pass": 1, "scenes": [{"scene_id": "s02", "kinds": ["unclear_goal"], "part": None},
                                                {"scene_id": "s01", "kinds": ["no_hook_text"], "part": "hook"}],
                          "issues_before": 2, "issues_after": None, "failed": ["s03"]}]
    assert schemas.episode_script_errors(script) == []
    script["repairs"][0]["scenes"][0]["kinds"] = ["character"]  # an E4 kind: repaired too since DEC-260
    assert schemas.episode_script_errors(script) == []
    script["repairs"][0]["scenes"][0]["kinds"] = ["bogus"]
    assert schemas.episode_script_errors(script)


def test_the_refusal_names_the_repairs_tried_and_ends_each_fix_once():
    judge = _judge()
    # A version-1 report (no severity): every issue blocks and the sentence says "issues", as it did.
    report = {"who_wants_what": "Ana veut la clé.", "what_happens": "Bo la cache.", "why_it_matters": "La porte.",
              "passed": False, "checked_rev": 1, "checked_at": NOW, "stale": False,
              "issues": [{"scene_id": "s01", "kind": "unclear_goal", "fix": "Dire l'enjeu."},
                         {"scene_id": None, "kind": "unmotivated", "fix": "Montrer pourquoi"}]}
    script = {"rev": 1, "first_watch": report}
    assert judge.issues_refusal(script, 2) == (
        "Episode 2's first-watch check found 2 issues: s01 (unclear goal): Dire l'enjeu; the episode (unmotivated): "
        "Montrer pourquoi. A first-time viewer took away: Ana veut la clé. Bo la cache. La porte. Fix them (edit the "
        "script, or regenerate the scenes they name) and check again, or approve anyway.")
    script["repairs"] = [{"pass": 1, "scenes": [], "issues_before": 2, "issues_after": 2, "failed": []}]
    assert judge.issues_refusal(script, 2).startswith(
        "Episode 2's first-watch check: after 1 repair pass, 2 issues remain: s01 (unclear goal): Dire l'enjeu; the "
        "episode (unmotivated): Montrer pourquoi. A first-time viewer took away:")
    # Version 2 (DEC-248): the blocking issues are listed, the minor ones only counted.
    script["repairs"].append({"pass": 2, "scenes": [], "issues_before": 2, "issues_after": 1, "failed": []})
    report["issues"] = [_issue("s01", "unclear_goal", "Pourquoi la clé ?"),
                        _issue("s03", "unintroduced", "Dire qui est Bo.", severity="minor")]
    message = judge.issues_refusal(script, 2)
    assert message.startswith("Episode 2's first-watch check: after 2 repair passes, 1 blocking issue remains: s01 "
                              "(unclear goal): Pourquoi la clé ? (1 minor issue kept for review). A first-time viewer "
                              "took away:")
    assert "Dire qui est Bo" not in message
    assert ".." not in message and "?." not in message
    assert judge.issues_refusal({"rev": 1, "first_watch": dict(report, passed=True, issues=[])}, 2) is None
