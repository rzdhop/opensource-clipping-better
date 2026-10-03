"""The first-watch judge of a v2 script, J1 (AI Story phase 7, stage 6a; A16,
DEC-230).

The script step of a v2 story runs J1 after E4 and stores its report on the
script (``first_watch``), the repeated-line and hook-text checks merged in;
``workflow.approve_script`` refuses a v2 script while the report is missing
or stale (never approvable) or failed (unless "approve anyway"). A legacy
story is never judged and approves exactly as before.

The story and the fake LLM are phase 3's (``tests/test_story_episode_steps.py``);
offline and hermetic, its own ``hermetic`` fixture. The modules are imported
inside the tests, so on the parent commit each test fails on its own.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import copy

import pytest

import test_story_episode_steps as eps
from test_story_episode_steps import hermetic, store  # noqa: F401 -- phase 3's fixtures, used as they are

NOW = eps.NOW


def _wf():
    from clipping.aistory import workflow

    return workflow


def _judge():
    from clipping.aistory.steps import judge

    return judge


def _written(store, *, v2=True, **queues):
    """A ready story (v2 unless *v2* is False) with its episode 1 written by
    the script step; ``(story_id, llm, summary, log)``."""
    story_id = eps._ready_story(store, v2=v2)
    llm = eps._script_llm(v2=v2, **queues)
    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)
    return story_id, llm, summary, log


def _refused(call):
    wf = _wf()
    with pytest.raises(wf.WorkflowError) as caught:
        call()
    assert caught.value.code == wf.CONFLICT
    return str(caught.value)


# ============================================================ the approval

def test_failed_first_watch_blocks_approval_unless_anyway(store):
    wf = _wf()
    # Stage G: the step repairs s05 (its two passes, one call each) and J1 still finds the issue: the report stands.
    story_id, llm, summary, _log = _written(store, E4=[eps.E4_PASSED] * 3, J1=[eps.J1_ISSUES] * 3)
    assert llm.prompts()[12:] == ["E2v2", "E4", "J1"] * 2
    script = eps._script(store, story_id)
    report = script["first_watch"]
    assert report["passed"] is False and report["checked_rev"] == script["rev"] and report["stale"] is False
    assert report["issues"] == eps.J1_ISSUES["issues"]
    assert report["who_wants_what"] == eps.J1_ISSUES["who_wants_what"]
    assert summary["first_watch"] is False and summary["consistency"] is True

    message = _refused(lambda: wf.approve_script(store, story_id, 1, now=NOW))
    assert message.startswith("Episode 1's first-watch check: after 2 repair passes, 1 blocking issue remains: s05 "
                              "(unmotivated): Montrez pourquoi Kiwilo avoue son plan.")
    assert "A first-time viewer took away: Kiwilo veut garder le pouvoir sur l'île." in message
    assert message.endswith("Fix them (edit the script, or regenerate the scenes they name) and check again, or "
                            "approve anyway.")
    assert eps._script(store, story_id)["approved_at"] is None

    # The episode page says where the report stands (a v2 story's page only).
    assert wf.episode_view(store, store.get(story_id), 1)["state"]["first_watch"] == "issues"

    approved = wf.approve_script(store, story_id, 1, approve_anyway=True, now=NOW)
    assert approved["approved_at"] == NOW and approved["approved_anyway"] == NOW
    assert eps._script(store, story_id)["first_watch"] == report  # the report itself never moves


def test_a_passed_first_watch_approves_and_records_no_anyway(store):
    wf = _wf()
    story_id, _llm, summary, _log = _written(store, E4=[eps.E4_PASSED])
    assert summary["first_watch"] is True
    approved = wf.approve_script(store, story_id, 1, now=NOW)
    assert approved["approved_at"] == NOW and approved["approved_anyway"] is None


def test_a_missing_or_stale_first_watch_is_never_approvable(store):
    wf = _wf()
    # Stage G: the step's two repair passes do not clear the issue (J1 finds it each time).
    story_id, _llm, _summary, _log = _written(store, E4=[eps.E4_PASSED] * 3, J1=[eps.J1_ISSUES] * 3)

    # Stale: an edit rewrites the script (episode_common.mark_changed) -- E4's report goes stale too, and
    # is refused first; once it is fresh again, J1's staleness still refuses, "anyway" or not.
    script = eps._script(store, story_id)
    edited = copy.deepcopy(script)
    eps._new().common.mark_changed(edited, None, scene_ids=["s05"], now=NOW)
    assert edited["first_watch"]["stale"] is True and edited["consistency_report"]["stale"] is True
    edited["consistency_report"].update(stale=False, checked_rev=edited["rev"])
    store.write_episode_doc(story_id, 1, "script.json", edited, now=NOW)
    for anyway in (False, True):
        message = _refused(lambda: wf.approve_script(store, story_id, 1, approve_anyway=anyway, now=NOW))
        assert message == ("Episode 1's first-watch check is out of date (the script changed since it ran): check "
                           "it again (run the script step), then approve.")
    assert _judge().first_watch_state(eps._script(store, story_id)) == "stale"

    # Missing: a v2 script written before the judge existed.
    del edited["first_watch"]
    store.write_episode_doc(story_id, 1, "script.json", edited, now=NOW)
    for anyway in (False, True):
        message = _refused(lambda: wf.approve_script(store, story_id, 1, approve_anyway=anyway, now=NOW))
        assert message.startswith("Episode 1's script has not had its first-watch check yet (J1")

    # Running the script step again judges it again (one J1 call) -- E4 is current, so it is not asked.
    llm = eps.FakeLLM(J1=[eps.J1_PASSED])
    eps._run(eps._new().script, store, story_id, llm=llm)
    assert llm.prompts() == ["J1"]
    assert wf.approve_script(store, story_id, 1, now=NOW)["approved_anyway"] is None


def test_a_legacy_script_is_never_judged_and_approves_as_before(store):
    wf = _wf()
    story_id, llm, summary, _log = _written(store, v2=False, E4=[eps.E4_PASSED])
    assert "J1" not in llm.prompts() and "E2v2" not in llm.prompts()
    script = eps._script(store, story_id)
    assert "first_watch" not in script
    assert "first_watch" not in summary and "fill" not in summary
    assert "first_watch" not in wf.episode_view(store, store.get(story_id), 1)["state"]
    assert wf.approve_script(store, story_id, 1, now=NOW)["approved_anyway"] is None


# ============================================================ the report

def test_j1_reads_what_a_first_time_viewer_would(store):
    from clipping.aistory import prompts

    _story_id, llm, _summary, _log = _written(store, E4=[eps.E4_PASSED])
    call = llm.of("J1")[0]
    assert call["schema_name"] == "first_watch_check"
    assert call["temperature"] is prompts.ANALYTIC_TEMPERATURE
    assert call["max_tokens"] == prompts.MAX_TOKENS["J1"] == 970  # DEC-248: J1 version 2's severities
    user = call["user"]
    assert "Scene s01 (hook) -- Le Parloir des Secrets, day -- characters: Kiwilo, Mangella" in user
    assert "Objects, and the scenes that show them:\n- Téléphone en noix de coco: s01, s04, s09, s10\n" in user
    assert "Hook on-screen text: Vote surprise ce soir\nCliffhanger reveal: Le téléphone affiche le nom de Kiwilo.\n" \
        in user
    assert "Previously" not in user  # episode 1
    # A first-time viewer: no bible, no cast notes, no memory.
    assert "Garder le pouvoir sur l'île." not in user and "Series memory" not in user
    assert "- unintroduced: a character speaks or matters before the viewer learns who they are" in user
    assert "Write every field in French." in call["system"]


def test_repeated_lines_and_a_missing_hook_text_fail_the_report_whatever_j1_says(store):
    """The deterministic checks (no call) lead the report: a line repeating
    an earlier one (normalised-token Jaccard >= 0.7) even when J1 misses it,
    and a hook with no on-screen text -- J1's own no_hook_text is then left
    out, so it is said once."""
    from clipping.providers.errors import ProviderError

    story_id, _llm, _summary, _log = _written(store, E4=[eps.E4_PASSED])
    script = eps._script(store, story_id)
    s02, s05 = eps._scene(script, "s02"), eps._scene(script, "s05")
    s05["lines"][0]["text"] = s02["lines"][0]["text"].upper().replace(".", " !")  # case and punctuation aside
    script["hook"]["on_screen_text"] = None
    del script["first_watch"]
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)

    # Stage G: the step then tries to repair the hook (E3v2) and s05 (E2v2); both calls fail here, so the
    # report under test is kept as J1 and the checks wrote it.
    def down():
        return ProviderError("down", [("gemini/gemini-test", "HTTP 503")])

    llm = eps.FakeLLM(E2=[down()], E3=[down()], J1=[dict(eps.J1_PASSED, passed=False, issues=[
        {"scene_id": "s01", "kind": "no_hook_text", "severity": "blocking", "fix": "Ajoutez un texte."},
        {"scene_id": None, "kind": "unclear_goal", "severity": "blocking", "fix": "Dites ce que veut Mangella."}])])
    _summary, log = eps._run(eps._new().script, store, story_id, llm=llm)
    assert llm.prompts() == ["J1", "E3v2", "E2v2"]

    report = eps._script(store, story_id)["first_watch"]
    line, first = s05["lines"][0]["line_id"], s02["lines"][0]["line_id"]
    assert report["passed"] is False
    assert [(issue["scene_id"], issue["kind"]) for issue in report["issues"]] == [
        ("s05", "repeated_line"), ("s01", "no_hook_text"), (None, "unclear_goal")]
    assert report["issues"][0]["fix"].startswith(f"Line {line} repeats line {first} (“")
    assert report["issues"][1]["fix"] == ("The hook has no on-screen text: write it again (regenerate hook:1) so it "
                                          "states the premise on screen.")
    assert "👀 Repeated lines and hook text: 2 issues, added to the first-watch report" in log
    assert "Hook on-screen text: none\n" in llm.of("J1")[0]["user"]

    # And with J1 passing outright, the deterministic findings still fail it.
    script = eps._script(store, story_id)
    del script["first_watch"]
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    eps._run(eps._new().script, store, story_id, llm=eps.FakeLLM(E2=[down()], E3=[down()], J1=[eps.J1_PASSED]))
    report = eps._script(store, story_id)["first_watch"]
    assert report["passed"] is False and [issue["kind"] for issue in report["issues"]] == ["repeated_line",
                                                                                          "no_hook_text"]


def test_duplicate_issues_are_pure_and_bounded():
    judge = _judge()
    lines = ["Le téléphone désigne Kiwilo ce soir.", "Le telephone designe Kiwilo, ce soir !",
             "Tout le monde se tait.", "Non.", "Non."]
    script = {"scenes": [{"scene_id": f"s{i:02d}", "lines": [{"line_id": f"l{i:02d}", "text": text}]}
                         for i, text in enumerate(lines, start=1)]}
    issues = judge.duplicate_issues(script)
    assert issues == [{"scene_id": "s02", "kind": "repeated_line", "severity": "blocking",
                       "fix": "Line l02 repeats line l01 (“Le téléphone désigne Kiwilo ce soir.”): rewrite one of "
                              "them."}]  # a two-word "Non." said twice is speech, not a repeated line
    many = {"scenes": [{"scene_id": "s01", "lines": [{"line_id": f"l{i:02d}", "text": lines[0]}
                                                     for i in range(10)]}]}
    assert len(judge.duplicate_issues(many)) == judge.DUPLICATE_ISSUES_MAX == 6


def test_a_failed_j1_fails_the_step_naming_it_and_keeps_the_script(store):
    from clipping.providers.errors import ProviderError

    story_id = eps._ready_story(store, v2=True)
    llm = eps._script_llm(v2=True, E4=[eps.E4_PASSED], J1=[ProviderError("down", [("gemini/gemini-test", "HTTP 503")])])
    message, log = eps._failed(eps._new().script, store, story_id, llm=llm)
    assert "the first-watch check failed" in message and "run the script step again" in message
    script = eps._script(store, story_id)
    assert script["consistency_report"]["passed"] is True and "first_watch" not in script
    assert any(line.startswith("✖ The first-watch check failed") for line in log)
