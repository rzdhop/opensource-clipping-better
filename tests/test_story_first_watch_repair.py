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


def _wf():
    from clipping.aistory import workflow

    return workflow


def _script_step():
    from clipping.aistory.steps import script

    return script


def _judge():
    from clipping.aistory.steps import judge

    return judge


def _issue(sid, kind, fix):
    return {"scene_id": sid, "kind": kind, "fix": fix}


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


# ============================================================ the messages

def test_the_refusal_names_the_repairs_tried_and_ends_each_fix_once():
    judge = _judge()
    report = {"who_wants_what": "Ana veut la clé.", "what_happens": "Bo la cache.", "why_it_matters": "La porte.",
              "passed": False, "checked_rev": 1, "checked_at": NOW, "stale": False,
              "issues": [_issue("s01", "unclear_goal", "Dire l'enjeu."), _issue(None, "unmotivated", "Montrer pourquoi")]}
    script = {"rev": 1, "first_watch": report}
    assert judge.issues_refusal(script, 2) == (
        "Episode 2's first-watch check found 2 issues: s01 (unclear goal): Dire l'enjeu; the episode (unmotivated): "
        "Montrer pourquoi. A first-time viewer took away: Ana veut la clé. Bo la cache. La porte. Fix them (edit the "
        "script, or regenerate the scenes they name) and check again, or approve anyway.")
    script["repairs"] = [{"pass": 1, "scenes": [], "issues_before": 2, "issues_after": 2, "failed": []}]
    assert judge.issues_refusal(script, 2).startswith(
        "Episode 2's first-watch check: after 1 repair pass, 2 issues remain: s01 (unclear goal): Dire l'enjeu; the "
        "episode (unmotivated): Montrer pourquoi. A first-time viewer took away:")
    script["repairs"].append({"pass": 2, "scenes": [], "issues_before": 2, "issues_after": 1, "failed": []})
    report["issues"] = [_issue("s01", "unclear_goal", "Pourquoi la clé ?")]
    message = judge.issues_refusal(script, 2)
    assert message.startswith("Episode 2's first-watch check: after 2 repair passes, 1 issue remains: s01 (unclear "
                              "goal): Pourquoi la clé ? A first-time viewer took away:")
    assert ".." not in message and "?." not in message
    assert judge.issues_refusal({"rev": 1, "first_watch": dict(report, passed=True, issues=[])}, 2) is None
