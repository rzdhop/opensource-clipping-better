"""The hard length gate of a v2 story and the script step's fill pass (AI
Story phase 7, stage 6a; A17, DEC-231).

A v2 episode is approved and rendered only inside its template's window,
never "anyway": ``approve_script`` and ``approve_storyboard`` refuse an
estimated length outside it, ``approve_assets`` and the render a measured
one (``steps/gates.length_refusal``). A legacy story keeps today's warning.
Story B shipped 9.9 s under the floor: :func:`_story_b_shape` measures the
render fixture's episode at that length.

The fill pass: a complete, unapproved v2 script whose estimate is under the
window gets E2v2 again on its shortest body scenes, at most 2 calls a run.

The fixtures are phase 3's and phase 4's (``tests/test_story_episode_steps.py``,
``tests/test_story_render_step.py``); offline and hermetic. The modules are
imported inside the tests, so on the parent commit each test fails on its own.

Plan 28 stage S2 (DEC-305 section 9): the refusals no longer say "v2" (an episode, a storyboard, the
assets); every number and every way out is unchanged.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import functools

import pytest

import test_story_episode_steps as eps
import test_story_render_step as trs
from clipping.aistory import defaults
from test_story_render_step import built, hermetic, store  # noqa: F401 -- phase 4's fixtures, used as they are

NOW = eps.NOW

# Every line of the render fixture's episode measured at this length: it runs
# 45.1 s, 9.9 s under serial_60s_v1's 55 s floor -- story B's own shape.
STORY_B_LINE_S = 1.81

SHORT = functools.partial(eps.e2_v2_reply, short=True)
LONG = eps.e2_v2_reply


def _wf():
    from clipping.aistory import workflow

    return workflow


def _make_v2(store, story_id):
    store.update(story_id, lambda doc: doc["generation_profile"].update(pipeline=defaults.PIPELINE_V2), now=NOW)


def _story_b_shape(store, story_id):
    """Every line measured at :data:`STORY_B_LINE_S`, the storyboard and the
    script re-timed beside it -- what the assets step does after a
    measurement: no approval moves (durations are not in the assets
    fingerprint)."""
    from clipping.aistory import shots
    from clipping.aistory.steps import assets, episode_common

    ec = trs._ec(store, story_id)
    script = store.read_episode_doc(story_id, 1, "script.json")
    board = store.read_episode_doc(story_id, 1, "storyboard.json")
    doc = store.read_episode_doc(story_id, 1, "assets.json")
    for scene in script["scenes"]:
        for line in scene["lines"]:
            line["timing"]["duration_s"] = STORY_B_LINE_S
    shots.retime_storyboard(board, script, template=ec.template, language=ec.language, style_lock=ec.style_lock)
    episode_common.retime(script, ec, board)
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    assert script["timing"]["total_s"] == pytest.approx(45.131) and script["timing"]["state"] == "under"
    assert assets.current_fingerprint(ec, board, script, doc) == doc["approved"]["fingerprint"]


# ============================================================ the render and the assets (measured)

def test_v2_render_refused_outside_window_v1_warns(store, tmp_path, built):
    story_id = trs.episode(store, tmp_path, built)
    _story_b_shape(store, story_id)

    # v1: rendered, the length a warning only (today's rule; the fake probe answers 4.8 s).
    summary, _log, fake = trs.render(store, story_id, tmp_path=tmp_path)
    assert summary["state"] == "completed" and fake.calls
    assert "The episode is 4.8 s long, outside 55-75 s." in summary["warnings"]

    # v2: refused before any process, with the numbers and what to do.
    _make_v2(store, story_id)
    message, fake = trs.refused(store, story_id, tmp_path=tmp_path)
    assert message == (
        "Episode 1 cannot be rendered: it runs 45.1 s with its voices (measured), 9.9 s under its 55–80 s "
        "window, and an episode is rendered only inside it. To lengthen it, lengthen its script (regenerate or "
        "edit its shortest scenes), approve it and its storyboard again, make and approve the assets again, then "
        "render.")
    assert fake.calls == []
    # The render estimate's pre-check (render.require_clips) refuses with the same sentence.
    from clipping.aistory.steps import render

    with pytest.raises(eps.steps.StepFailed) as caught:
        render.require_clips(trs._ec(store, story_id))
    assert str(caught.value) == message


def test_v2_assets_approval_refused_on_the_measured_length_v1_approves(store, tmp_path, built):
    wf = _wf()
    story_id = trs.episode(store, tmp_path, built)
    _story_b_shape(store, story_id)
    assert wf.approve_assets(store, story_id, 1, now=NOW)["approved"]["at"] == NOW

    _make_v2(store, story_id)
    with pytest.raises(wf.WorkflowError) as caught:
        wf.approve_assets(store, story_id, 1, now=NOW)
    assert caught.value.code == wf.CONFLICT
    assert str(caught.value).startswith("Episode 1 runs 45.1 s with its voices (measured), 9.9 s under its 55–80 s "
                                        "window: the assets are approved only inside it, never anyway.")


def test_a_v2_episode_inside_the_window_renders(store, tmp_path, built):
    story_id = trs.episode(store, tmp_path, built)  # 67.3 s measured, inside 55-80 s
    _make_v2(store, story_id)
    summary, _log, _fake = trs.render(store, story_id, tmp_path=tmp_path)
    assert summary["state"] == "completed"


def test_the_gate_states_an_over_length_too():
    from types import SimpleNamespace

    from clipping.aistory import templates
    from clipping.aistory.steps import gates

    template = templates.load_episode_template("serial_60s_v2")
    ec = SimpleNamespace(story={"generation_profile": {"pipeline": "v2"}}, template=template, ep=3, language="fr",
                         style_lock=None)
    timing = {"total_s": 79.26, "measured_lines": 0, "estimated_lines": 9}
    real = gates.episode_length
    try:
        gates.episode_length = lambda *_args, **_kwargs: timing
        message = gates.length_refusal(ec, {"scenes": [{}]}, stage="script")
        assert message == ("Episode 3's script runs 79.3 s (estimated), 4.3 s over its 55–75 s window: an episode "
                           "is approved only inside it, never anyway. To shorten it, edit or regenerate its longest "
                           "scenes (the timing flags name the lines to trim), then approve.")
        timing.update(total_s=45.1)
        assert gates.length_refusal(ec, {"scenes": [{}]}, stage="script").startswith(
            "Episode 3's script runs 45.1 s (estimated), 9.9 s under its 55–75 s window")
        timing.update(total_s=55.0)
        assert gates.length_refusal(ec, {"scenes": [{}]}, stage="script") is None
        legacy = SimpleNamespace(**dict(vars(ec), story={"generation_profile": {}}))
        timing.update(total_s=45.1)
        assert gates.length_refusal(legacy, {"scenes": [{}]}, stage="render") is None
    finally:
        gates.episode_length = real


# ============================================================ the script and the storyboard (estimated)

def test_v2_script_approval_refused_outside_the_window_never_anyway(store):
    wf = _wf()
    story_id = eps._ready_story(store, v2=True)
    eps._run(eps._new().script, store, story_id,
             llm=eps._script_llm(v2=True, E2=[SHORT] * 10, E4=[eps.E4_PASSED]))
    script = eps._script(store, story_id)
    assert script["timing"]["total_s"] == pytest.approx(42.436) and script["first_watch"]["passed"] is True
    for anyway in (False, True):
        with pytest.raises(wf.WorkflowError) as caught:
            wf.approve_script(store, story_id, 1, approve_anyway=anyway, now=NOW)
        assert str(caught.value) == (
            "Episode 1's script runs 42.4 s (estimated), 12.6 s under its 55–80 s window: an episode is approved "
            "only inside it, never anyway. To lengthen it, run the script step again (its fill pass rewrites the "
            "shortest scenes), or regenerate or edit the shortest scenes with more lines, then approve.")
    assert eps._script(store, story_id)["approved_at"] is None

    # A legacy story's script of the same length approves, as before.
    legacy = eps._ready_story(store)
    eps._run(eps._new().script, store, legacy, llm=eps._script_llm(E2=[SHORT] * 8, E4=[eps.E4_PASSED]))
    assert eps._script(store, legacy)["timing"]["state"] == "under"
    assert wf.approve_script(store, legacy, 1, now=NOW)["approved_at"] == NOW


def test_v2_storyboard_approval_refused_outside_the_window(store):
    wf = _wf()
    m = eps._new()
    ids = []
    for _ in range(2):
        story_id = eps._ready_story(store)
        eps._run(m.script, store, story_id, llm=eps._script_llm(E2=[SHORT] * 8, E4=[eps.E4_PASSED]))
        wf.approve_script(store, story_id, 1, now=NOW)
        m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=eps.Log())
        ids.append(story_id)
    v2_id, legacy_id = ids
    _make_v2(store, v2_id)  # its script was approved before it became v2: only the storyboard is weighed here

    with pytest.raises(wf.WorkflowError) as caught:
        wf.approve_storyboard(store, v2_id, 1, now=NOW)
    assert str(caught.value).startswith("Episode 1 runs 42.4 s with its storyboard (estimated), 12.6 s under its "
                                        "55–80 s window: a storyboard is approved only inside it, never anyway.")
    assert wf.approve_storyboard(store, legacy_id, 1, now=NOW)["approved_at"] == NOW


# ============================================================ the fill pass

def test_the_fill_pass_is_bounded_at_two_calls(store):
    from clipping.aistory.steps import script as script_step

    story_id = eps._ready_story(store, v2=True)
    llm = eps._script_llm(v2=True, E2=[SHORT] * 8 + [SHORT, SHORT, LONG], E4=[eps.E4_PASSED])
    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)

    # After the framing, before E4 and J1 (they judge the filled script); the third fill reply is not asked.
    assert llm.prompts() == ["E1v2"] + ["E2v2"] * 8 + ["E3v2"] + ["E2v2"] * 2 + ["E4", "J1"]
    fills = llm.of("E2v2")[8:]
    assert all(f"Follow the author's note: {script_step.FILL_NOTE}" in call["user"] for call in fills)
    assert not any(script_step.FILL_NOTE in call["user"] for call in llm.of("E2v2")[:8])
    assert summary["fill"] == {"before_s": 42.436, "after_s": 42.436, "window_s": [55, 80], "scenes": ["s02", "s05"],
                               "failed": []}
    assert summary["calls"] == 14
    script = eps._script(store, story_id)
    # Each rewritten scene was raised to its slot's top first (serial_60s_v1's body: 8 s): a larger budget.
    assert [eps._scene(script, sid)["target_duration_s"] for sid in ("s02", "s05")] == [8.0, 8.0]
    assert script["rev"] == 1  # written before any check: no revision moves
    assert "⏱ Fill pass: 42.4 s estimated is under 55–80 s; writing the shortest scenes again (s02 and s05, E2)" in log
    assert "⏱ Fill pass: 2 scenes rewritten, 42.4 s → 42.4 s estimated — still under 55–80 s" in log
    assert script_step.FILL_CALLS_MAX == 2

    # Run again on the checked, still-short script: two more calls at most, rewritten like a regenerate (the
    # revision moves, so E4 and J1 judge it again).
    llm = eps.FakeLLM(E2=[SHORT, SHORT], E4=[eps.E4_PASSED], default={"J1": eps.J1_PASSED})
    summary, _log = eps._run(eps._new().script, store, story_id, llm=llm)
    assert llm.prompts() == ["E2v2", "E2v2", "E4", "J1"]
    script = eps._script(store, story_id)
    assert script["rev"] == 3 and script["consistency_report"]["checked_rev"] == 3
    assert script["first_watch"]["checked_rev"] == 3 and len(summary["fill"]["scenes"]) == 2


def test_the_fill_pass_stops_once_the_estimate_is_inside(store):
    story_id = eps._ready_story(store, v2=True)
    llm = eps._script_llm(v2=True, E2=[LONG] * 4 + [SHORT] * 4 + [LONG, LONG], E4=[eps.E4_PASSED])
    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)
    assert len(llm.of("E2v2")) == 9
    assert summary["fill"]["scenes"] == ["s06"] and summary["fill"]["before_s"] == pytest.approx(54.302)
    assert summary["fill"]["after_s"] == pytest.approx(56.169)
    assert "⏱ Fill pass: 1 scene rewritten, 54.3 s → 56.2 s estimated — inside 55–80 s" in log
    assert _wf().approve_script(store, story_id, 1, now=NOW)["approved_at"] == NOW


def test_a_failed_fill_call_keeps_the_scene_and_the_step_goes_on(store):
    from clipping.providers.errors import ProviderError

    story_id = eps._ready_story(store, v2=True)
    down = ProviderError("down", [("gemini/gemini-test", "HTTP 503")])
    llm = eps._script_llm(v2=True, E2=[SHORT] * 8 + [down, SHORT], E4=[eps.E4_PASSED])
    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)
    assert summary["fill"]["failed"] == ["s02"] and summary["fill"]["scenes"] == ["s05"]
    script = eps._script(store, story_id)
    assert eps._scene(script, "s02")["target_duration_s"] != 8.0  # put back as it was
    assert any(line.startswith("✖ Fill pass: scene s02 failed") for line in log)


def test_a_legacy_script_under_the_window_is_never_filled(store):
    story_id = eps._ready_story(store)
    llm = eps._script_llm(E2=[SHORT] * 8, E4=[eps.E4_PASSED])
    summary, log = eps._run(eps._new().script, store, story_id, llm=llm)
    assert llm.prompts() == ["E1"] + ["E2"] * 8 + ["E3", "E4"]
    assert eps._script(store, story_id)["timing"]["state"] == "under"
    assert "fill" not in summary and not any("Fill pass" in line for line in log)
