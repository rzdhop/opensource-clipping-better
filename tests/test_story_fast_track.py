"""The ``fast-track`` step: one episode from its script to its metadata pack
in one job (AI Story phase 4, stage 10; spec 3 "Fast track"; DEC-161,
DEC-162, A-076; RC-A3).

The story is phase 3's French fixture (``tests/test_story_episode_steps.py``)
with no episode yet; its portraits and plates carry seeds as in stage 8's
fixture. Every call answers through a fake: the LLM through phase 3's
``FakeLLM`` (E1..E4, T1, and stage 9's M1 reply), images through stage 8's
recording ``FakeImage``, voices through the measurement's fake Edge, ffmpeg
through stage 7's ``FakeFFmpeg`` and pre-flight, the cover through stage 9's
``FakeCover``. Offline and hermetic: stage 8's own ``hermetic`` fixture.

The step module is imported inside the tests, so on the parent commit
(``ea97344``) each test fails on its own.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path

import pytest

import test_aistory_render_runner as rr
import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_measure as tsm
import test_story_metadata_step as tms
from clipping.aistory import schemas, steps, timing
from clipping.cancel import Cancelled
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used here as they are

NOW = eps.NOW
# A line a little longer than the fixture's, so the script lands inside its
# 55-80 s window (the fixture's own is 54.7 s: "under").
LONGER = "Tu crois vraiment que je vais te suivre ce soir ?"
E2_OK = functools.partial(eps.e2_reply, text=LONGER)
SCRIPT_PROMPTS = ["E1"] + ["E2"] * len(eps.BODY) + ["E3", "E4"]
SCENES = len(eps.ALL_SCENES)


def _ft():
    from clipping.aistory.steps import fast_track

    return fast_track


# ------------------------------------------------------------------ the story

def _story(store):
    """A ready story, no episode written; seeds as stage 8's fixture."""
    story_id = eps._ready_story(store)
    for cid, seed in tas.PORTRAIT_SEEDS.items():
        doc = store.read_entity(story_id, "characters", cid)
        doc["refs"]["portrait"]["seed"] = seed
        store.write_entity(story_id, "characters", doc, now=NOW)
    for pid, seed in tas.PLATE_SEEDS.items():
        doc = store.read_entity(story_id, "places", pid)
        doc["time_variants"]["day"]["seed"] = seed
        store.write_entity(story_id, "places", doc, now=NOW)
    return story_id


def llm(*, e4=None, e2=E2_OK, clock=None, advance=0.0, t1=None, **queues):
    """Every prompt of a fast track: the script's in queues, T1 and M1 for
    every call."""
    script = {"E1": [eps.E1_REPLY], "E2": [e2] * len(eps.BODY), "E3": [eps.E3_FULL],
              "E4": [eps.E4_PASSED if e4 is None else e4]}
    script.update(queues)
    return eps.FakeLLM(clock=clock, advance=advance, default={"T1": t1 or eps.t1_reply, "M1": tms.m1_reply},
                       **script)


def no_llm():
    """An LLM that must not be asked anything."""
    return eps.FakeLLM()


class Fakes:
    """Every seam of a run, recorded."""

    def __init__(self, tmp_path, *, runner=None, image=None, edge=None, fal=None, gemini=None, edit=None,
                 clock=None, ffmpeg=None, cover=None, local=None):
        self.runner = runner if runner is not None else llm()
        self.image = image if image is not None else tas.FakeImage()
        self.edge = edge if edge is not None else tsm.Edge()
        self.fal = fal
        self.adapters = tas._adapters(self.edge, image=self.image, fal=fal, gemini=gemini, edit=edit)
        if local is not None:  # a run that proposes voices (the agent's cast) speaks through a local engine
            self.adapters[("tts", "local")] = local
        self.clock = clock if clock is not None else eps.Clock(0.0)
        self.ffmpeg = ffmpeg if ffmpeg is not None else rr.FakeFFmpeg()
        self.cover = cover if cover is not None else tms.FakeCover()
        self.preflights = []
        self.fonts_dir = tmp_path / "no_custom_fonts"
        real = rr._fake_run()

        def preflight(cmd, **kwargs):
            self.preflights.append(list(cmd))
            return real(cmd, **kwargs)

        self.run_process = preflight

    def kwargs(self):
        return {"runner": self.runner, "time_fn": self.clock, "adapters": self.adapters, "sleep_fn": lambda _s: None,
                "run_process": self.run_process, "popen": self.ffmpeg, "clock": self.ffmpeg.clock,
                "cover_process": self.cover, "custom_fonts_dir": self.fonts_dir}

    def generation_calls(self):
        calls = len(self.image.requests) + len(self.edge.calls)
        return calls + (len(self.fal.requests) if self.fal is not None else 0)


def _ctx(store, story_id, *, params=None, settings=None):
    return eps._ctx(store, story_id, step="fast-track", params=params,
                    settings=tas._settings() if settings is None else settings)


def run(store, story_id, fakes, *, params=None, settings=None):
    ctx, log = _ctx(store, story_id, params=params, settings=settings)
    return _ft().run(ctx, **fakes.kwargs()), log


def stopped(store, story_id, fakes, **kwargs):
    with pytest.raises(steps.StepFailed) as caught:
        run(store, story_id, fakes, **kwargs)
    return " ".join(str(caught.value).split())


def _doc(store, story_id, name):
    return store.read_episode_doc(story_id, 1, name)


def _ep_dir(store, story_id) -> Path:
    return Path(store.story_dir(story_id)) / "episodes" / "ep01"


def _ledger(store, story_id):
    path = Path(store.story_dir(story_id)) / "cost_ledger.json"
    return json.loads(path.read_text(encoding="utf-8"))["entries"] if path.exists() else []


def _ec(store, story_id):
    from clipping.aistory.steps import episode_common

    return episode_common.load_context(store, story_id, 1)


# =============================================================== the whole run

def test_a_full_run_writes_plans_makes_renders_and_packs_one_episode_approving_each_document(store, tmp_path):
    from clipping.aistory.steps import assets

    story_id = _story(store)
    before = eps._story_bytes(store, story_id)
    fakes = Fakes(tmp_path)

    summary, log = run(store, story_id, fakes)

    # the calls: the script's exact ask, one T1 per scene, one M1 per platform
    assert fakes.runner.prompts() == SCRIPT_PROMPTS + ["T1"] * SCENES + ["M1"] * 3
    assert fakes.image.requests and fakes.edge.calls and fakes.ffmpeg.calls and len(fakes.cover.calls) == 1
    # each document auto-approved by its own rule, never anyway
    script, board, doc = _doc(store, story_id, "script.json"), _doc(store, story_id, "storyboard.json"), \
        _doc(store, story_id, "assets.json")
    assert script["approved_at"] and script["approved_anyway"] is None
    assert script["consistency_report"]["passed"] is True
    assert board["approved_at"] and all(entry["source"] == "t1" for entry in board["scenes"].values())
    assert all(shot["assets"]["approved"] is True for shot in board["shots"])
    ec = _ec(store, story_id)
    assert doc["approved"]["fingerprint"] == assets.current_fingerprint(ec, board, script, doc)
    # the render and the pack
    manifest = _doc(store, story_id, "render_manifest.json")
    assert manifest["output"]["path"] == "episode_final.mp4"
    assert (_ep_dir(store, story_id) / "episode_final.mp4").is_file()
    pack = _doc(store, story_id, "metadata_pack.json")
    assert list(pack["platforms"]) == list(schemas.PLATFORMS)
    assert (_ep_dir(store, story_id) / "cover.jpg").is_file()
    # the feed names each sub-step and each auto-approval
    for number, label in enumerate(["script", "storyboard", "paid check", "assets", "render", "metadata"], 1):
        assert f"⏩ Fast track {number}/6: {label}" in log
    approvals = [line for line in log if line.startswith("✅ Fast track: episode 1's ")]
    assert [line.split("'s ", 1)[1].split(" auto-approved")[0] for line in approvals] == [
        "script", "storyboard", "assets"]
    assert "consistency passed" in approvals[0] and "inside 55–80 s" in approvals[0]
    assert any(line.startswith("💲 Episode 1's assets: nothing paid") for line in log)
    assert log[-1].startswith("🏁 Fast track done: episode 1 is rendered with its metadata pack")
    # the summary, per sub-step
    ft = _ft()
    assert summary["ep"] == 1 and summary["storyboard"] == "t1"
    assert list(summary["steps"]) == list(ft.SUB_STEPS)
    assert summary["auto_approved"] == ["script", "storyboard", "assets"]
    assert summary["steps"]["script"]["calls"] == len(SCRIPT_PROMPTS)
    assert summary["steps"]["storyboard"]["calls"] == SCENES
    assert summary["steps"]["paid_check"]["verdict"] == ft.FREE
    assert summary["steps"]["assets"]["complete"] is True
    assert summary["steps"]["render"]["state"] == "completed"
    assert summary["steps"]["metadata"]["asked"] == list(schemas.PLATFORMS)
    # RC-E2: the story itself is never written
    assert eps._story_bytes(store, story_id) == before


def test_continue_after_a_full_run_repeats_nothing_and_approves_nothing_again(store, tmp_path):
    story_id = _story(store)
    run(store, story_id, Fakes(tmp_path))
    stamps = (_doc(store, story_id, "script.json")["approved_at"], _doc(store, story_id, "storyboard.json")[
        "approved_at"], _doc(store, story_id, "assets.json")["approved"])
    final = (_ep_dir(store, story_id) / "episode_final.mp4").read_bytes()
    ledger = _ledger(store, story_id)

    again = Fakes(tmp_path, runner=no_llm(), image=tas.NeverImage())
    summary, log = run(store, story_id, again)

    assert again.runner.calls == [] and again.edge.calls == [] and again.image.requests == []
    assert again.ffmpeg.calls == [] and again.preflights == [] and again.cover.calls == []
    assert (_doc(store, story_id, "script.json")["approved_at"], _doc(store, story_id, "storyboard.json")[
        "approved_at"], _doc(store, story_id, "assets.json")["approved"]) == stamps
    assert (_ep_dir(store, story_id) / "episode_final.mp4").read_bytes() == final
    assert _ledger(store, story_id) == ledger
    assert summary["auto_approved"] == []
    assert all(summary["steps"][name]["kept"] for name in ("script", "storyboard", "paid_check", "assets", "render"))
    assert summary["steps"]["metadata"]["asked"] == [] and summary["steps"]["metadata"]["cover"] is None
    assert "🎬 Episode 1's render is current (the same inputs, commands and text): kept as it is." in log


def test_the_fast_storyboard_makes_no_t1_call_and_is_approved_by_its_own_rule(store, tmp_path):
    story_id = _story(store)
    fakes = Fakes(tmp_path)

    summary, log = run(store, story_id, fakes, params={"storyboard": "fast"})

    assert fakes.runner.prompts() == SCRIPT_PROMPTS + ["M1"] * 3
    board = _doc(store, story_id, "storyboard.json")
    assert board["approved_at"] and all(entry["source"] == "fast" for entry in board["scenes"].values())
    assert summary["storyboard"] == "fast" and summary["steps"]["storyboard"]["calls"] == 0
    assert summary["auto_approved"] == ["script", "storyboard", "assets"]


@pytest.mark.parametrize("params, expected", [
    ({"storyboard": "slow"}, "The fast track's storyboard is one of t1, fast, not 'slow'."),
    # Phase 7 follow-up stage C, re-pinned on purpose: stop_at_keyframes joins the params; plan 19 stage 3,
    # re-pinned on purpose: stop_on_script_issues too.
    ({"subtitles": "none"}, "The fast track takes only storyboard, stop_at_keyframes, stop_on_script_issues; not "
                            "'subtitles'."),
    ({"stop_on_script_issues": "yes"}, "The fast track's stop_on_script_issues is true or false, not 'yes'."),
])
def test_a_bad_param_is_refused_before_anything_runs(store, tmp_path, params, expected):
    story_id = _story(store)
    fakes = Fakes(tmp_path, runner=no_llm())
    assert stopped(store, story_id, fakes, params=params) == expected
    assert _doc(store, story_id, "script.json") is None


# ================================================================ the script

def test_a_consistency_check_with_issues_stops_a_legacy_script_and_is_never_approved_anyway(store, tmp_path):
    """Revised on purpose (plan 19 stage 3): the fast track now approves a
    v2 script anyway once the script step's repair passes are spent
    (:func:`test_the_fast_track_approves_anyway_after_the_repair_passes_are_spent`);
    this story is a legacy one -- no first-watch check, no repair pass -- so
    it still stops, and its sentence now says when the one click would go
    over the issues instead of "never"."""
    story_id = _story(store)
    fakes = Fakes(tmp_path, runner=llm(e4=eps.E4_ISSUES))

    message = stopped(store, story_id, fakes)

    # DEC-261, re-pinned on purpose: the s03 voice note is minor (approved over, named in the detail); the
    # continuity issue with no scene is the blocking one that stops the fast track.
    assert message.startswith("Fast track stopped at the script (step 1 of 6): Episode 1's consistency check "
                              "found 1 blocking issue: the episode (continuity): Le vote surprise n'est jamais "
                              "expliqué. The fast track never approves over blocking issues before the script "
                              "step's repair passes are spent on a v2 story: fix them (edit the script, or "
                              "regenerate the scenes they name) so the check passes, or approve the script anyway "
                              "yourself.")
    assert message.endswith("Then Continue the fast track: it picks up here and repeats nothing already done.")
    script = _doc(store, story_id, "script.json")
    assert script["approved_at"] is None and script["approved_anyway"] is None
    assert fakes.runner.prompts() == SCRIPT_PROMPTS  # no T1, no M1
    assert fakes.generation_calls() == 0 and _doc(store, story_id, "storyboard.json") is None


def test_after_the_user_approves_anyway_continue_keeps_the_script_and_repeats_no_call(store, tmp_path):
    from clipping.aistory import workflow

    story_id = _story(store)
    stopped(store, story_id, Fakes(tmp_path, runner=llm(e4=eps.E4_ISSUES)))
    workflow.approve_script(store, story_id, 1, approve_anyway=True, now=NOW)

    fakes = Fakes(tmp_path, runner=eps.FakeLLM(default={"T1": eps.t1_reply, "M1": tms.m1_reply}))
    summary, log = run(store, story_id, fakes)

    assert fakes.runner.prompts() == ["T1"] * SCENES + ["M1"] * 3
    assert _doc(store, story_id, "script.json")["approved_anyway"] == NOW
    assert summary["steps"]["script"] == {"kept": True, "calls": 0}
    assert summary["auto_approved"] == ["storyboard", "assets"]
    assert "📄 Episode 1's script is approved already: kept as it is." in log


def test_a_script_under_its_window_stops_at_the_script(store, tmp_path):
    story_id = _story(store)
    fakes = Fakes(tmp_path, runner=llm(e2=eps.e2_reply))  # the fixture's own lines: 54.7 s

    message = stopped(store, story_id, fakes)

    assert message.startswith("Fast track stopped at the script (step 1 of 6): Episode 1's script is under its "
                              "length window: ⏱ 54.7 s estimated — under 55–80 s")
    assert "lengthen it (edit it, or regenerate a scene), or approve it yourself" in message
    assert _doc(store, story_id, "script.json")["approved_at"] is None
    assert fakes.runner.prompts() == SCRIPT_PROMPTS and fakes.generation_calls() == 0


def test_a_script_over_its_window_stops_at_the_script(store, tmp_path, monkeypatch):
    real = timing.episode_pass

    def over(*args, **kwargs):
        result, scenes = real(*args, **kwargs)
        return dict(result, state="over"), scenes

    monkeypatch.setattr(timing, "episode_pass", over)
    story_id = _story(store)
    fakes = Fakes(tmp_path)

    message = stopped(store, story_id, fakes)

    assert "Episode 1's script is over its length window: ⏱ 56.9 s estimated — over 55–80 s" in message
    assert "shorten it" in message
    assert _doc(store, story_id, "script.json")["approved_at"] is None and fakes.generation_calls() == 0


def test_the_auto_approval_rule_is_pure_and_approves_anyway_only_over_spent_repairs():
    """Revised on purpose (plan 19 stage 3; was ``..._and_never_approves_anyway``):
    :func:`script_refusal` refuses exactly as before; the new
    :func:`script_anyway_issues` names the blocking issues the one click
    approves over -- only on a v2 story whose run spent the repair passes,
    with both checks fresh and the length inside the window -- else None."""
    ft = _ft()
    script = {"scenes": [{"scene_id": "s01", "function": "hook", "state": "written"}], "rev": 3,
              "next_episode_teaser": "x", "cliffhanger": {"reveal": "y"},
              "consistency_report": {"passed": True, "issues": [], "checked_rev": 3, "stale": False},
              "timing": {"state": "tightened", "total_s": 78.0, "window_s": [55, 80], "measured_lines": 0,
                         "estimated_lines": 2, "flags": []}}
    assert ft.script_refusal(script, 1) is None
    stale = dict(script, consistency_report=dict(script["consistency_report"], stale=True))
    assert "has not run on this revision" in ft.script_refusal(stale, 1)
    # DEC-261, re-pinned on purpose: a blocking issue refuses; a report of minor notes alone (or of none) passes.
    failed = dict(script, consistency_report=dict(script["consistency_report"], passed=False, issues=[
        {"scene_id": "s01", "kind": "continuity", "fix": "Le vote n'est jamais expliqué."}]))
    assert "never approves over blocking issues" in ft.script_refusal(failed, 1)
    notes = dict(script, consistency_report=dict(script["consistency_report"], passed=False, issues=[
        {"scene_id": "s01", "kind": "character", "fix": "Broccolia parle trop gentiment."}]))
    assert ft.script_refusal(notes, 1) is None
    assert "not complete" in ft.script_refusal(None, 1)

    # Plan 19 stage 3: the one exception, pure. A v2 script whose run spent its repair passes.
    spent = [{"pass": 1}, {"pass": 2}]
    report = {"who_wants_what": "a", "what_happens": "b", "why_it_matters": "c", "passed": False,
              "issues": [{"scene_id": "s01", "kind": "unmotivated", "severity": "blocking", "fix": "Dire pourquoi."},
                         {"scene_id": "s01", "kind": "unclear_goal", "severity": "minor", "fix": "Un détail."}],
              "checked_rev": 3, "checked_at": NOW, "stale": False, "version": 2}
    v2 = dict(failed, first_watch=report)
    assert ft.script_refusal(v2, 1, v2=True) is not None  # the refusal itself is unchanged
    assert ft.script_anyway_issues(v2, 1, v2=True, repairs=spent) == [
        {"scene_id": "s01", "kind": "continuity", "fix": "Le vote n'est jamais expliqué.", "check": "consistency"},
        {"scene_id": "s01", "kind": "unmotivated", "fix": "Dire pourquoi.", "check": "first_watch"}]
    # Never for a legacy story, passes not spent, a stale check or a length outside the window.
    assert ft.script_anyway_issues(failed, 1, repairs=spent) is None
    assert ft.script_anyway_issues(v2, 1, v2=False, repairs=spent) is None
    assert ft.script_anyway_issues(v2, 1, v2=True, repairs=spent[:1]) is None
    assert ft.script_anyway_issues(v2, 1, v2=True, repairs=None) is None
    stale_v2 = dict(v2, first_watch=dict(report, stale=True))
    assert ft.script_anyway_issues(stale_v2, 1, v2=True, repairs=spent) is None
    under = dict(v2, timing=dict(v2["timing"], state="under"))
    assert ft.script_anyway_issues(under, 1, v2=True, repairs=spent) is None
    assert ft.script_anyway_issues(None, 1, v2=True, repairs=spent) is None
    # Nothing blocking: nothing to approve over (the plain approval's case).
    passed = dict(script, first_watch=dict(report, passed=True, issues=[]))
    assert ft.script_anyway_issues(passed, 1, v2=True, repairs=spent) is None


def test_a_legacy_first_watch_report_keeps_the_stop_whatever_the_repairs():
    """Plan 19 stage 3: only a report carrying severities (J1 version >= 2,
    DEC-248; E4's by kind, DEC-261) is approved over -- a version-1 report,
    every issue blocking for want of a severity, keeps the stop."""
    ft = _ft()
    script = {"scenes": [{"scene_id": "s01", "function": "hook", "state": "written"}], "rev": 3,
              "next_episode_teaser": "x", "cliffhanger": {"reveal": "y"},
              "consistency_report": {"passed": True, "issues": [], "checked_rev": 3, "stale": False},
              "first_watch": {"who_wants_what": "a", "what_happens": "b", "why_it_matters": "c", "passed": False,
                              "issues": [{"scene_id": "s01", "kind": "unmotivated", "fix": "Dire pourquoi."}],
                              "checked_rev": 3, "checked_at": NOW, "stale": False},
              "timing": {"state": "ok", "total_s": 62.0, "window_s": [55, 75], "measured_lines": 0,
                         "estimated_lines": 2, "flags": []},
              "approved_at": "2026-10-03T10:00:00+00:00"}  # approved: a version-1 report is still current
    spent = [{"pass": 1}, {"pass": 2}]
    assert ft.script_anyway_issues(script, 1, v2=True, repairs=spent) is None
    versioned = dict(script, first_watch=dict(script["first_watch"], version=2, issues=[
        dict(script["first_watch"]["issues"][0], severity="blocking")]))
    assert ft.script_anyway_issues(versioned, 1, v2=True, repairs=spent) == [
        {"scene_id": "s01", "kind": "unmotivated", "fix": "Dire pourquoi.", "check": "first_watch"}]


# Plan 19 stage 3: a v2 episode whose script keeps two blocking issues through both repair passes -- E4's
# continuity issue with no scene (no pass can rewrite it) and J1's s05, named again by each re-check.
_STUCK_J1 = dict(eps.J1_PASSED, passed=False, issues=[
    {"scene_id": "s05", "kind": "unmotivated", "severity": "blocking", "fix": "Montrer pourquoi Kiwilo avoue."}])
_STUCK_PROMPTS = ["E1v2"] + ["E2v2"] * 8 + ["E3v2", "E4", "J1"] + ["E2v2", "E4", "J1"] * 2


def _stuck(store, monkeypatch):
    """A v2 story, the LLM of a script stuck on two blocking issues, and the
    storyboard sub-step stopped (what comes after the script is not this
    test's)."""
    ft = _ft()
    story_id = eps._ready_story(store, v2=True)

    def no_storyboard(self):
        raise steps.StepFailed("the storyboard is not part of this test.")

    monkeypatch.setattr(ft._FastTrack, "storyboard", no_storyboard)
    runner = eps._script_llm(v2=True, E2=[eps.e2_v2_reply] * 14, E4=[eps.E4_ISSUES] * 3, J1=[_STUCK_J1] * 3)
    return story_id, runner


def test_the_fast_track_approves_anyway_after_the_repair_passes_are_spent(store, tmp_path, monkeypatch):
    """Plan 19 stage 3 (F4, amending DEC-162/248 as DEC-246 did for the
    keyframes): the live walk -- the human approved anyway three times on
    one episode over the same two issues. Once the script step spent its
    repair passes and only blocking issues remain, the one click approves
    the script anyway (``by: fast_track``, the issues kept on the script),
    names them in the feed and on the review, and goes on; it never asks a
    third pass."""
    from clipping.aistory import workflow

    story_id, runner = _stuck(store, monkeypatch)
    story_before = store.get(story_id)
    fakes = Fakes(tmp_path, runner=runner)

    ctx, log = _ctx(store, story_id)
    with pytest.raises(steps.StepFailed) as caught:
        _ft().run(ctx, **fakes.kwargs())

    assert str(caught.value).startswith("Fast track stopped at the storyboard (step 2 of 6): the storyboard is not "
                                        "part of this test.")
    assert runner.prompts() == _STUCK_PROMPTS  # two passes, no third
    script = _doc(store, story_id, "script.json")
    assert len(script["repairs"]) == 2 and script["first_watch"]["passed"] is False
    assert script["approved_at"] and script["approved_anyway"] == script["approved_at"]
    over = [{"scene_id": None, "kind": "continuity", "fix": "Le vote surprise n'est jamais expliqué.",
             "check": "consistency"},
            {"scene_id": "s05", "kind": "unmotivated", "fix": "Montrer pourquoi Kiwilo avoue.", "check": "first_watch"}]
    assert script["approved_by"] == "fast_track" and script["approved_over"] == over
    assert ("✅ Fast track: episode 1's script auto-approved (anyway -- after 2 repair passes, 2 blocking issues "
            "remain: the episode (continuity): Le vote surprise n'est jamais expliqué; s05 (unmotivated): Montrer "
            "pourquoi Kiwilo avoue; review them on the finished episode)") in log
    assert store.get(story_id) == story_before  # RC-E2: the story's own document is never written

    # The review names them, as it names the keyframes' "anyway".
    story = store.get(story_id)
    page = workflow.episode_view(store, story, 1)
    page.update(workflow.episode_outputs(store, story, 1))
    approval = workflow.episode_review(page)["approvals"]["script"]
    assert approval == {"approved": True, "at": script["approved_at"], "anyway": True, "by": "fast_track",
                        "issues": over}

    # An edit clears the fast track's record with the approval.
    workflow.patch_script(store, story_id, 1, {"lines": [{"line_id": script["scenes"][1]["lines"][0]["line_id"],
                                                          "text": "Une autre réplique, tout à fait neuve."}]},
                          now=NOW)
    edited = _doc(store, story_id, "script.json")
    assert edited["approved_at"] is None and "approved_by" not in edited and "approved_over" not in edited


def test_stop_on_script_issues_keeps_the_stop(store, tmp_path, monkeypatch):
    """Plan 19 stage 3: ``params.stop_on_script_issues`` keeps today's stop
    at the script, after the same repair passes, naming the param."""
    story_id, runner = _stuck(store, monkeypatch)

    message = stopped(store, story_id, Fakes(tmp_path, runner=runner), params={"stop_on_script_issues": True})

    assert message.startswith("Fast track stopped at the script (step 1 of 6): Episode 1's consistency check "
                              "found 1 blocking issue: the episode (continuity): Le vote surprise n'est jamais "
                              "expliqué. The fast track never approves over blocking issues before")
    assert ("It stops here: stop_on_script_issues is on (without it, once the repair passes are spent, the fast "
            "track approves the script anyway and names the issues for your review).") in message
    assert runner.prompts() == _STUCK_PROMPTS
    script = _doc(store, story_id, "script.json")
    assert script["approved_at"] is None and script["approved_anyway"] is None
    assert "approved_by" not in script and "approved_over" not in script


# ============================================================ the paid check

def test_a_paid_plan_with_allow_paid_off_stops_before_any_generation_call_with_the_numbers(store, tmp_path):
    story_id = _story(store)
    fal = tas.FakeImage(price=tas.FAL_PRICE)
    fakes = Fakes(tmp_path, image=tas.NeverImage(), fal=fal)

    message = stopped(store, story_id, fakes, settings=tas._settings(**tas.PAID_IMAGES))

    shots = len(_doc(store, story_id, "storyboard.json")["shots"])
    est = shots * tas.FAL_PRICE
    assert message.startswith("Fast track stopped at the paid check (step 3 of 6): Episode 1's assets need paid "
                              f"generation -- {shots} shot images on fal/flux-schnell (est ${est:.3f}), est "
                              f"${est:.3f} in all -- and allow_paid is off.")
    # DEC-223 (AI Story phase 7 stage 2a): the default caps are now 2 / 6 / 20 (were 1 / 3 / 10).
    # Stage E (phase 7 follow-up): 4 / 12 / 40.
    assert "Caps: this episode $0.00 of $4.00, today $0.00 of $12.00 and this story $0.00 of $40.00." in message
    assert "Nothing was generated or spent: turn allow_paid on in Settings" in message
    # before ANY generation call: no image, no voice, nothing booked, no spend file
    assert fal.requests == [] and fakes.edge.calls == [] and fakes.generation_calls() == 0
    assert _ledger(store, story_id) == [] and not Path(os.environ["SPEND_PATH"]).exists()
    assert _doc(store, story_id, "assets.json") is None
    # the script and the storyboard were written and approved before it
    assert _doc(store, story_id, "script.json")["approved_at"] and _doc(store, story_id, "storyboard.json")[
        "approved_at"]


@pytest.mark.parametrize("cap, value, words", [
    ("PER_EPISODE_CAP_USD", "0.05", "would bring this episode to ${est:.2f} of its $0.05 cap"),
    ("DAILY_CAP_USD", "0.05", "would bring today to ${est:.2f} of the $0.05 daily cap"),
    ("PER_STORY_CAP_USD", "0.05", "would bring this story to ${est:.2f} of its $0.05 cap"),
])
def test_a_paid_plan_over_any_cap_stops_before_any_generation_call_with_the_numbers(store, tmp_path, cap, value,
                                                                                     words):
    story_id = _story(store)
    fal = tas.FakeImage(price=tas.FAL_PRICE)
    fakes = Fakes(tmp_path, image=tas.NeverImage(), fal=fal)
    settings = tas._settings(**tas.PAID_IMAGES, ALLOW_PAID="1", **{cap: value})

    message = stopped(store, story_id, fakes, settings=settings)

    shots = len(_doc(store, story_id, "storyboard.json")["shots"])
    est = shots * tas.FAL_PRICE
    assert message.startswith("Fast track stopped at the paid check (step 3 of 6): Episode 1's assets would go over "
                              f"a cap -- {shots} shot images on fal/flux-schnell (est ${est:.3f}), est ${est:.3f} "
                              "in all:")
    assert words.format(est=est) in message
    assert "Nothing was generated or spent: raise that cap in Settings, or choose free links." in message
    assert fal.requests == [] and fakes.generation_calls() == 0 and _ledger(store, story_id) == []
    assert not Path(os.environ["SPEND_PATH"]).exists()


def test_a_paid_voice_with_allow_paid_off_stops_before_any_generation_call(store, tmp_path, monkeypatch):
    story_id = _story(store)
    tsm._paid_gemini(monkeypatch)
    tsm._pin(store, story_id, eps.MANGELLA, "gemini", "Kore")
    gemini = tsm.NeverCalled()
    fakes = Fakes(tmp_path, gemini=gemini)

    message = stopped(store, story_id, fakes)

    assert message.startswith("Fast track stopped at the paid check (step 3 of 6): Episode 1's assets need paid "
                              "generation -- ")
    assert "characters on gemini/Kore for Mangella (est $" in message and "allow_paid is off" in message
    assert gemini.calls == 0 and fakes.generation_calls() == 0 and _ledger(store, story_id) == []


def test_references_mode_with_no_editor_stops_at_the_paid_check_having_generated_nothing(store, tmp_path):
    story_id = _story(store)
    store.update(story_id, lambda doc: doc["generation_profile"].update(consistency_mode="references"), now=NOW)
    src = tas._media(tmp_path)
    for cid in (eps.KIWILO, eps.MANGELLA, eps.BROCCOLIA):
        store.write_media(story_id, "characters", cid, "portrait.jpg", src)
    for pid in (eps.PARLOIR, eps.PISCINE):
        for name in ("variant_day.jpg", "variant_night.jpg"):
            store.write_media(story_id, "places", pid, name, src)
    store.write_media(story_id, "props", eps.PHONE, "image.jpg", src)
    edit = tas.FakeImage(reachable=False)
    fakes = Fakes(tmp_path, image=tas.NeverImage(), edit=edit)

    message = stopped(store, story_id, fakes, settings=tas._settings(**tas.LOCAL_EDIT))

    assert message.startswith("Fast track stopped at the paid check (step 3 of 6): Episode 1's assets cannot be made "
                              "as the chains stand: ")
    assert "local/comfyui" in message and "Nothing was generated or spent" in message
    assert edit.probes == ["local/comfyui"] and edit.requests == [] and fakes.generation_calls() == 0


def test_paid_images_within_every_cap_go_on_and_are_booked_on_the_episode(store, tmp_path):
    story_id = _story(store)
    fal = tas.FakeImage(price=tas.FAL_PRICE)
    fakes = Fakes(tmp_path, image=tas.NeverImage(), fal=fal)

    summary, log = run(store, story_id, fakes, settings=tas._settings(**tas.PAID_IMAGES, ALLOW_PAID="1"))

    assert summary["steps"]["paid_check"]["verdict"] == _ft().PAID_WITHIN_CAPS
    assert any(line.startswith("💲 Episode 1's assets: paid within every cap -- ") for line in log)
    rows = [row for row in _ledger(store, story_id) if row["unit"] == "image"]
    assert rows and len(rows) == len(fal.requests)
    assert all(row["paid"] and row["ep"] == 1 and row["step"] == "assets" for row in rows)


def test_the_paid_verdict_is_pure():
    ft = _ft()
    free = {"images": {"count": 3, "route_class": "free", "link": "pollinations/flux", "est_usd": 0.0, "links": [],
                       "ready": True, "message": "ok"},
            "voices": {"voices": [{"voice": "edge/x", "speakers": ["A"], "lines": 2, "chars": 40, "paid": False,
                                   "est_usd": 0.0, "allowed": True, "reason": None}], "unvoiced": [], "chars": 40},
            "caps": {"allow_paid": False}, "est_usd": 0.0, "over_cap": None, "ready": True}
    verdict = ft.paid_verdict(free, ep=2)
    assert (verdict["verdict"], verdict["stop"], verdict["paid"]) == (ft.FREE, None, False)
    assert verdict["message"] == ("Episode 2's assets: nothing paid -- 3 shot images on pollinations/flux and 40 "
                                  "characters on edge/x, $0.00.")
    unvoiced = dict(free, voices=dict(free["voices"], unvoiced=[{"line_id": "l04", "reason": "the narrator has no "
                                                                 "voice yet"}]), ready=False)
    blocked = ft.paid_verdict(unvoiced, ep=2)
    assert blocked["verdict"] == ft.BLOCKED
    assert "l04: the narrator has no voice yet" in blocked["stop"] and "Nothing was generated or spent" in blocked[
        "stop"]


def test_the_render_asks_the_budget_first_and_a_stop_there_names_the_render(store, tmp_path, monkeypatch):
    story_id = _story(store)
    monkeypatch.setattr(_ft(), "render_seconds", lambda shots: 10_000.0)
    fakes = Fakes(tmp_path)

    message = stopped(store, story_id, fakes)

    assert message.startswith("Fast track stopped at the render (step 5 of 6): The step's 60-minute budget is nearly "
                              "spent")
    assert "Left: the render (about 167 min), then the metadata (3 M1 calls)." in message
    assert fakes.ffmpeg.calls == [] and fakes.preflights == []
    assert _doc(store, story_id, "assets.json")["approved"] is not None


# ============================================================ budget, cancel

def test_the_predictive_budget_stop_names_the_sub_step_and_continue_repeats_no_call(store, tmp_path):
    story_id = _story(store)
    clock = eps.Clock(0.0)
    # 11 script calls then T1s, 250 s each: the 4th T1 would start at 3500 s,
    # and could not finish inside the hour (a call may take 300 s).
    fakes = Fakes(tmp_path, runner=llm(clock=clock, advance=250.0), clock=clock)

    message = stopped(store, story_id, fakes)

    assert message.startswith("Fast track stopped at the storyboard (step 2 of 6): The step's 60-minute budget is "
                              "nearly spent (58.3 min used, and one more call may take up to 5 min)")
    assert "Left: the shots of scenes " in message
    assert fakes.runner.prompts() == SCRIPT_PROMPTS + ["T1"] * 3
    assert _doc(store, story_id, "script.json")["approved_at"]  # the script was approved before the stop
    assert fakes.generation_calls() == 0

    again = Fakes(tmp_path, runner=eps.FakeLLM(default={"T1": eps.t1_reply, "M1": tms.m1_reply}))
    summary, _log = run(store, story_id, again)

    assert again.runner.prompts() == ["T1"] * (SCENES - 3) + ["M1"] * 3
    assert summary["steps"]["script"]["kept"] is True and summary["auto_approved"] == ["storyboard", "assets"]


def test_a_cancel_between_calls_ends_cancelled(store, tmp_path):
    story_id = _story(store)
    holder = {}

    def cancelling_t1(call):
        if len([c for c in holder["llm"].calls if c["prompt"] == "T1"]) == 2:
            holder["token"].cancel()
        return eps.t1_reply(call)

    runner = llm(t1=cancelling_t1)
    holder["llm"] = runner
    fakes = Fakes(tmp_path, runner=runner)
    ctx, _log = _ctx(store, story_id)
    holder["token"] = ctx.cancel

    with pytest.raises(Cancelled):
        _ft().run(ctx, **fakes.kwargs())

    assert fakes.runner.prompts() == SCRIPT_PROMPTS + ["T1"] * 2
    assert fakes.generation_calls() == 0 and fakes.ffmpeg.calls == []


def test_a_cancel_during_ffmpeg_ends_cancelled_before_the_metadata(store, tmp_path):
    story_id = _story(store)
    holder = {}

    def on_start(argv, cwd):
        if "xfade" in " ".join(argv) or any(arg.endswith("episode_pre.mkv") for arg in argv):
            holder["token"].cancel()

    fakes = Fakes(tmp_path, ffmpeg=rr.FakeFFmpeg(on_start=on_start))
    ctx, log = _ctx(store, story_id)
    holder["token"] = ctx.cancel

    with pytest.raises(Cancelled):
        _ft().run(ctx, **fakes.kwargs())

    manifest = _doc(store, story_id, "render_manifest.json")
    assert manifest["output"] is None and fakes.cover.calls == []
    assert "M1" not in fakes.runner.prompts()
    assert any(line.startswith("⏹ Episode 1's render was cancelled") for line in log)


# ================================================================ the registry

def test_the_fast_track_is_registered_and_ends_completed():
    assert "fast-track" in steps.RUNNERS
    assert steps.RUNNERS["fast-track"].__name__ == "run_fast_track"
    assert steps.ends_completed("fast-track", {"storyboard": "t1"}) is True


# ================================================================ the estimate

def test_the_estimate_of_a_new_episode_counts_the_exact_llm_calls_and_prices_the_rest(store, tmp_path):
    ft = _ft()
    story_id = _story(store)
    ec = _ec(store, story_id)

    t1 = ft.estimate(ec, env=tas._settings())
    fast = ft.estimate(ec, env=tas._settings(), storyboard="fast")

    # DEC-145: E1's exact ask, 1 + N + 1 + 1; T1 once per scene E1 writes; 3 M1
    assert t1["llm_calls"] == {"script": 1 + len(eps.BODY) + 1 + 1, "storyboard": SCENES, "metadata": 3,
                               "total": 11 + SCENES + 3,
                               "script_breakdown": {"E1": 1, "E2": len(eps.BODY), "E3": 1, "E4": 1}}
    assert fast["llm_calls"]["storyboard"] == 0 and fast["llm_calls"]["total"] == 11 + 3
    # images and voices on their routes, as an upper bound before they exist
    per_scene = ec.episode_defaults["shots_per_scene"][1]
    assert t1["images"]["count"] == SCENES * per_scene and t1["images"]["exact"] is False
    assert (t1["images"]["route_class"], t1["images"]["link"]) == ("free", "pollinations/flux")
    assert t1["tts"]["exact"] is False and t1["tts"]["chars"] == ft.predicted_chars(ec)
    assert {row["voice"] for row in t1["tts"]["voices"]} == {f"edge/{v}" for v in tsm.VOICE_IDS.values()}
    assert t1["render"]["needed"] is True and t1["render"]["shots"] == SCENES * per_scene
    assert t1["render"]["seconds"] == ft.render_seconds(SCENES * per_scene)
    assert t1["est_usd"] == 0.0 and t1["paid"]["verdict"] == ft.FREE and t1["stops_at"] is None
    assert t1["paid"]["message"].startswith(f"Episode 1's assets: nothing paid -- up to {SCENES * per_scene} shot "
                                            "images on pollinations/flux")


def test_the_estimate_says_a_paid_plan_would_stop_before_paid_spending(store, tmp_path):
    ft = _ft()
    story_id = _story(store)
    ec = _ec(store, story_id)
    adapters = tas._adapters(fal=tas.FakeImage(price=tas.FAL_PRICE))
    shots = SCENES * ec.episode_defaults["shots_per_scene"][1]

    off = ft.estimate(ec, env=tas._settings(**tas.PAID_IMAGES), adapters=adapters)
    capped = ft.estimate(ec, env=tas._settings(**tas.PAID_IMAGES, ALLOW_PAID="1", PER_EPISODE_CAP_USD="0.05"),
                         adapters=adapters)
    allowed = ft.estimate(ec, env=tas._settings(**tas.PAID_IMAGES, ALLOW_PAID="1"), adapters=adapters)

    assert off["paid"]["verdict"] == ft.STOPS_BEFORE_PAID and off["stops_at"]["step"] == "paid_check"
    assert f"up to {shots} shot images on fal/flux-schnell (est ${shots * tas.FAL_PRICE:.3f})" in off["paid"]["stop"]
    assert "allow_paid is off" in off["paid"]["stop"]
    assert capped["paid"]["verdict"] == ft.STOPS_BEFORE_PAID and "of its $0.05 cap" in capped["paid"]["stop"]
    assert capped["est_usd"] == round(shots * tas.FAL_PRICE, 4)
    assert allowed["paid"]["verdict"] == ft.PAID_WITHIN_CAPS and allowed["stops_at"] is None
    assert allowed["est_usd"] == round(shots * tas.FAL_PRICE, 4)


def test_the_estimate_after_a_full_run_is_zero_calls_and_no_render(store, tmp_path):
    ft = _ft()
    story_id = _story(store)
    run(store, story_id, Fakes(tmp_path))

    done = ft.estimate(_ec(store, story_id), env=tas._settings(), custom_fonts_dir=tmp_path / "no_custom_fonts")

    assert done["llm_calls"]["total"] == 0
    assert done["images"] == dict(done["images"], count=0, exact=True) and done["tts"]["exact"] is True
    assert done["tts"]["chars"] == 0 and done["render"]["needed"] is False and done["render"]["seconds"] == 0.0
    assert done["est_usd"] == 0.0 and done["paid"]["message"] == "Episode 1's assets: nothing to generate, $0.00."


def test_the_estimate_names_a_script_the_auto_approval_would_refuse(store, tmp_path):
    ft = _ft()
    story_id = _story(store)
    stopped(store, story_id, Fakes(tmp_path, runner=llm(e4=eps.E4_ISSUES)))

    estimate = ft.estimate(_ec(store, story_id), env=tas._settings())

    assert estimate["llm_calls"]["script"] == 0 and estimate["llm_calls"]["storyboard"] == SCENES
    assert estimate["stops_at"]["step"] == "script"
    assert "never approves over blocking issues" in estimate["stops_at"]["reason"]  # DEC-261 wording
    assert estimate["tts"]["exact"] is True and estimate["images"]["exact"] is False


# ========================================================= the assets approval

def _made(store, tmp_path):
    """Stage 8's episode (written, planned fast, approved) with its assets made."""
    story_id = tas._episode(store, tmp_path)
    summary, _log = tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    assert summary["complete"] is True
    return story_id


def _refused(call, *args, **kwargs):
    from clipping.aistory import workflow

    with pytest.raises(workflow.WorkflowError) as caught:
        call(*args, **kwargs)
    assert caught.value.code == workflow.CONFLICT
    return str(caught.value)


def test_approving_the_assets_marks_every_shot_and_stores_the_current_fingerprint(store, tmp_path):
    from clipping.aistory import workflow
    from clipping.aistory.steps import assets, render

    story_id = _made(store, tmp_path)
    before = eps._story_bytes(store, story_id)
    script_before, board_before = _doc(store, story_id, "script.json"), _doc(store, story_id, "storyboard.json")

    written = workflow.approve_assets(store, story_id, 1, now="2026-09-28T20:00:00+00:00")

    script, board, doc = _doc(store, story_id, "script.json"), _doc(store, story_id, "storyboard.json"), \
        _doc(store, story_id, "assets.json")
    assert written == doc
    assert doc["approved"]["at"] == "2026-09-28T20:00:00+00:00"
    assert doc["approved"]["fingerprint"] == assets.current_fingerprint(_ec(store, story_id), board, script, doc)
    assert all(shot["assets"]["approved"] is True for shot in board["shots"])
    # nothing else moves: the approvals, the revisions, the shots' own records, the story
    assert (script["approved_at"], script["rev"]) == (script_before["approved_at"], script_before["rev"])
    assert (board["approved_at"], board["rev"]) == (board_before["approved_at"], board_before["rev"])
    assert [dict(s["assets"], approved=False) for s in board["shots"]] == [s["assets"] for s in board_before["shots"]]
    assert eps._story_bytes(store, story_id) == before
    # and it is what the render needs
    assert render.require_renderable(_ec(store, story_id))[2]["approved"] == doc["approved"]


def test_the_assets_are_not_approved_before_they_are_made_or_their_script_and_storyboard_are(store, tmp_path):
    from clipping.aistory import workflow

    story_id = tas._episode(store, tmp_path)
    assert "Episode 1 has no assets yet: make them first (the assets step)." == _refused(
        workflow.approve_assets, store, story_id, 1, now=NOW)

    script = _doc(store, story_id, "script.json")
    script["approved_at"] = None
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    assert _refused(workflow.approve_assets, store, story_id, 1, now=NOW).startswith(
        "Approve episode 1's script first")


def test_a_shot_without_a_current_image_or_a_line_without_its_voice_is_named(store, tmp_path):
    from clipping.aistory import workflow

    story_id = _made(store, tmp_path)
    board = _doc(store, story_id, "storyboard.json")
    shot = board["shots"][2]
    (_ep_dir(store, story_id) / shot["assets"]["image"]).unlink()

    message = _refused(workflow.approve_assets, store, story_id, 1, now=NOW)
    assert message == (f"Episode 1's shot {shot['shot_id']} has no current image: make it (the assets step, or "
                       f"regenerate shot:1:{shot['shot_id']}) or lock it, then approve.")

    run_again = tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))[0]
    assert run_again["complete"] is True
    script = _doc(store, story_id, "script.json")
    line = script["scenes"][1]["lines"][0]
    (_ep_dir(store, story_id) / line["timing"]["audio"]).unlink()

    message = _refused(workflow.approve_assets, store, story_id, 1, now=NOW)
    assert message == (f"Episode 1's line {line['line_id']} has no audio in the speaker's pinned voice: speak it "
                       f"(the assets step, or regenerate line:1:{line['line_id']}), then approve.")
    assert _doc(store, story_id, "assets.json")["approved"] is None


def test_a_locked_shot_keeps_its_image_even_when_its_prompt_moved_on(store, tmp_path):
    from clipping.aistory import workflow
    from clipping.aistory.steps import assets

    story_id = _made(store, tmp_path)
    board = _doc(store, story_id, "storyboard.json")
    board["shots"][0]["assets"]["locked"] = True
    board["shots"][0]["prompt_override"] = "A different picture of the phone."
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    assert assets.shot_state(_ec(store, story_id), _doc(store, story_id, "storyboard.json")["shots"][0]) == \
        "locked_stale"

    workflow.approve_assets(store, story_id, 1, now=NOW)

    assert _doc(store, story_id, "assets.json")["approved"]["at"] == NOW
