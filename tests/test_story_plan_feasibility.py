"""Plan 28 stage A1: a native-speech episode whose line plan cannot fit its
window is refused before a writer call spends money.

``timing.plan_clip_floor_s`` is the sum of a script's planned clips plus the
end card; the script step refuses right after the beat sheet when it passes
the window's top (no E2 call), ``timing.plan_floor_preview`` refuses before
E1 when even the cheapest shape cannot fit, and ``fast_track.estimate`` says
so before the click. A template that fits is untouched.

Stdlib + pytest (DEC-012); offline and hermetic (the fixtures of
``tests/test_story_assets_step.py``).
"""

from __future__ import annotations

import copy
import re

import pytest

import test_story_assets_step as tas
import test_story_episode_steps as eps
from clipping.aistory import defaults, media_policy, templates, timing
from clipping.aistory.steps import episode_common, script as script_step
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are

NOW = eps.NOW
SERIAL = templates.load_episode_template("serial_60s_v2")


def _native_story(store, template_id, *, narrator=True):
    """A ready v2 story at tier 3 on the native-speech profile, writing v3, on *template_id*."""
    story_id = eps._ready_story(store, v2=True, writing="v3")

    def setup(doc):
        doc["generation_profile"].update(tier=3, route="api", budget_profile=defaults.NATIVE_SPEECH_PROFILE)
        doc["episode_template_id"] = template_id
        doc["narrator"] = dict(doc["narrator"], enabled=narrator)

    store.update(story_id, setup, now=NOW)
    assert media_policy.native_speech(store.get(story_id))
    return story_id


def _e1v3(template_id):
    template = templates.load_episode_template(template_id)
    return dict(eps._e1_reply_for(template), spine=dict(eps.V3_SPINE))


# ---------------------------------------------------- (a) the plan's floor, and the refusal after E1

def test_a_plan_s_clip_floor_is_its_shots_and_the_end_card():
    # Re-pinned on purpose (plan 28 stage A2, DEC-305): the narrator and a character clip (6 + 6 s) in an 11 s
    # slot is no longer planned at all -- scene_plan plans that scene the narrator alone (one 8 s clip, pinned in
    # tests/test_story_plan_fit.py) -- so the 12 s plan the floor's arithmetic is checked on is built by hand.
    template = copy.deepcopy(SERIAL)
    template["window_s"] = [3, 11]
    shots = [{"clip_s": 6, "line_ids": ["l08"], "words_min": 3, "words_max": 11, "speaks": False},
             {"clip_s": 6, "line_ids": ["l09"], "words_min": 9, "words_max": 12, "speaks": True}]
    script = {"scenes": [{"scene_id": "s02", "function": "setup", "line_plan": {"shots": shots}}],
              "cliffhanger": {"cut_to_black": False}}

    assert timing.plan_clip_floor_s(script, template) == 12.0
    assert timing.plan_floor_refusal(1, 1, 12.0, 11) is not None
    # The end card the native clock adds (1.0 s less the 0.4 s fade) only when the cliffhanger cuts to black.
    assert timing.plan_clip_floor_s(dict(script, cliffhanger={"cut_to_black": True}), template) == pytest.approx(12.6)
    assert timing.plan_floor_refusal(1, 1, 11.0, 11) is None


def _tight_narrated(monkeypatch):
    """narrated_drama_60s_v2 with an 11 s top to its body slot and a 40 s window. Re-pinned on purpose (plan 28
    stage A2, DEC-305): a narrator and a character clip (6 + 6 s) no longer fit an 11 s body scene, which is planned
    the narrator alone (8 s), and the episode's fit holds the clips inside the window -- four body scenes fit
    (6 + 4 x 6 + 6 = 36 s at the cheapest, 40 s as fitted), five cannot (7 x 6 = 42 s)."""
    real = templates.load_episode_template

    def load(template_id):
        template = real(template_id)
        if template_id == "narrated_drama_60s_v2":
            template["slots"]["body"]["duration_s"] = [7.0, 11.0]
            template.update(window_s=[20, 40], target_s=34, tighten_above_s=38)
        return template

    monkeypatch.setattr(templates, "load_episode_template", load)


def _five_body_e1v3():
    """The narrated beat sheet with five body scenes (the format allows 3-5; E1 is asked for 4), each with a
    character line but the last two."""
    template = copy.deepcopy(templates.load_episode_template("narrated_drama_60s_v2"))
    template["default_body_count"] = 5
    reply = eps._e1_reply_for(template)
    body = iter([True, True, True, False, False])
    scenes = [dict(scene, character_line=next(body) if scene["function"] in ("setup", "rising", "peak", "turn")
                   else False) for scene in reply["scenes"]]
    return dict(reply, spine=dict(eps.V3_SPINE), scenes=scenes)


def test_the_script_step_refuses_the_beat_sheet_that_cannot_fit_and_writes_no_scene(store, monkeypatch):
    _tight_narrated(monkeypatch)
    story_id = _native_story(store, "narrated_drama_60s_v2")
    # Four body scenes is what E1 is asked for, and they fit the 40 s window as fitted...
    ctx, _log = eps._ctx(store, story_id)
    ec = episode_common.load_episode_context(ctx)
    assert script_step.plan_fit_refusal(ec) is None
    assert script_step.beat_sheet_slots(ec).count("body") == 4
    # ... but this beat sheet writes five (legal for the format): 7 scenes of one 6 s clip at the cheapest, 42 s.
    llm = eps.FakeLLM(E1v3=[_five_body_e1v3()], E2v3=[], E3v3=[], E4=[], default={})

    message, log = eps._failed(eps._new().script, store, story_id, llm=llm)

    assert llm.prompts() == ["E1v3"]  # the beat sheet was bought; no scene, no framing, no check
    assert message == ("Episode 1 cannot fit: its 7 scenes need at least 42 s of clips on this link, more than "
                       "the 40 s this format allows. Pick a format that fits, or let the app choose one.")
    assert any("(setup): 6 s of clips" in line for line in log)  # the arithmetic, scene by scene
    assert eps._script(store, story_id)["scenes"]  # the beat sheet is kept
    # A run again on the kept beat sheet is refused the same way, before a scene is written.
    again = eps.FakeLLM(E2v3=[], E3v3=[], E4=[], default={})
    assert eps._failed(eps._new().script, store, story_id, llm=again)[0] == message
    assert again.calls == []


def _tight_serial(monkeypatch):
    """serial_60s_v2 with a 28 s window: even its fewest scenes (a hook, 3 body scenes, a cliffhanger) at one 6 s
    clip each need 30 s."""
    real = templates.load_episode_template

    def load(template_id):
        template = real(template_id)
        if template_id == "serial_60s_v2":
            template.update(window_s=[20, 28], target_s=24, tighten_above_s=26)
        return template

    monkeypatch.setattr(templates, "load_episode_template", load)


def test_a_plan_whose_cheapest_shape_cannot_fit_is_refused_before_the_first_call(store, monkeypatch):
    # Re-pinned on purpose (plan 28 stage A2, DEC-305): tonight's story (serial_60s_v2, the narrator on, Veo:
    # 86 s against 75 s) now fits -- the format is re-slotted and the episode's fit drops a character line --
    # so the refusal is checked on a window no plan of the format can fit.
    fits = _native_story(store, "serial_60s_v2")
    ctx, _log = eps._ctx(store, fits)
    assert script_step.plan_fit_refusal(episode_common.load_episode_context(ctx)) is None

    _tight_serial(monkeypatch)
    story_id = _native_story(store, "serial_60s_v2")
    llm = eps.FakeLLM(default={})

    message, _log = eps._failed(eps._new().script, store, story_id, llm=llm)

    assert llm.calls == []
    assert message == ("Episode 1 cannot fit: its 5 scenes need at least 30 s of clips on this link, more than "
                       "the 28 s this format allows. Pick a format that fits, or let the app choose one.")
    assert not re.search(r"T1|native|speech|_v2|manual/|E1", message)


# ---------------------------------------------------- (b) a plan that fits passes unchanged

@pytest.mark.parametrize("template_id", ["narrated_drama_60s_v2", "confrontation_50s_v2"])
def test_a_native_template_that_fits_is_not_refused_before_E1(store, template_id):
    story_id = _native_story(store, template_id, narrator=template_id != "confrontation_50s_v2")
    ctx, _log = eps._ctx(store, story_id)
    ec = episode_common.load_episode_context(ctx)
    assert script_step.plan_fit_refusal(ec) is None


def test_a_narrated_beat_sheet_that_fits_goes_on_to_its_scenes(store):
    story_id = _native_story(store, "narrated_drama_60s_v2")
    e1 = dict(_e1v3("narrated_drama_60s_v2"))
    body = iter([True, False, True, False])
    e1["scenes"] = [dict(scene, character_line=next(body) if scene["function"] in ("setup", "rising", "peak", "turn")
                         else False) for scene in e1["scenes"]]
    llm = eps.FakeLLM(E1v3=[e1], default={})
    ctx, _log = eps._ctx(store, story_id)
    run = script_step._Run(ctx, episode_common.load_episode_context(ctx), runner=llm, time_fn=eps.Clock(100.0))
    run.script = script_step.skeleton(run.ec, now=NOW)

    run.beat_sheet()  # no refusal

    assert llm.prompts() == ["E1v3"]
    assert timing.plan_clip_floor_s(run.script, run.ec.template) <= run.ec.template["window_s"][1]
    assert script_step.plan_fit_refusal(run.ec, run.script) is None


# ---------------------------------------------------- (c) the estimate says so before the click

def test_the_fast_track_estimate_reports_the_refusal_before_any_call(store, monkeypatch):
    from clipping.aistory.steps import fast_track

    # Re-pinned on purpose (plan 28 stage A2, DEC-305): serial_60s_v2 fits now; a 28 s window cannot.
    _tight_serial(monkeypatch)
    story_id = _native_story(store, "serial_60s_v2")
    ec = episode_common.load_context(store, story_id, 1)

    estimate = fast_track.estimate(ec, env=tas._settings())

    assert estimate["stops_at"]["step"] == "script"
    reason = estimate["stops_at"]["reason"]
    assert re.fullmatch(r"Episode 1 cannot fit: its 5 scenes need at least \d+ s of clips on this link, more than "
                        r"the 28 s this format allows\. Pick a format that fits, or let the app choose one\.", reason)

    fits = _native_story(store, "narrated_drama_60s_v2")
    stop = fast_track.estimate(episode_common.load_context(store, fits, 1), env=tas._settings())["stops_at"]
    assert stop is None or stop["step"] != "script"  # (this fixture's keys stop it at the paid check, not here)
