"""Plan 28 stage A3: one clock and a remedy loop.

- On a native-speech story the Script step times the script on the clips its
  stored line plans buy (``timing.episode_pass``'s *native_plan*), the clock
  its storyboard is gated on: the length the Script step shows is the one the
  storyboard sums.
- ``native_speech.native_pass`` flags each scene whose clips run past its
  stored slot (``scene_over``), and the ``episode_over`` flag names the scene
  the Trim button rewrites first.
- The fast track, on an episode over its window, tries one remedy before it
  stops: the plans fitted again (plan 28 stage A2), only the scenes whose
  planned clips changed written again, the script checked and approved
  again; still over, A1's plain sentence. "Continue" runs the remedy again.

Stdlib + pytest (DEC-012); offline and hermetic.
"""

from __future__ import annotations

import re

import pytest

import test_story_episode_steps as eps
import test_story_native_speech_plan as nsp
from clipping.aistory import schemas, templates, timing
from clipping.aistory.steps import episode_common, fast_track, gates, script as script_step
from clipping.aistory.steps import storyboard as storyboard_step
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are

NOW = eps.NOW


def _say(sid, k, n):
    return " ".join(f"m{k}{sid}{j}" for j in range(max(1, int(n))))


def _at_plan(ec, script):
    """*script*'s scenes planned (``script.store_line_plans``) and each written at its plan: a line per planned
    line, its planned speaker, its floor's words."""
    script_step.store_line_plans(ec, script)
    for scene in script["scenes"]:
        sid = scene["scene_id"]
        lines = []
        for k, entry in enumerate(scene["line_plan"]["lines"]):
            text = _say(sid, k, entry.get("min_words") or 1)
            lines.append({"line_id": schemas.line_id_for(sid, k), "speaker": entry["speaker"], "text": text,
                          "emotion": "neutral", "delivery": "calm",
                          "timing": timing.estimated_timing(text, ec.language)})
        scene["lines"] = lines
        scene["sfx_cues"] = []
    return script


def test_the_script_step_s_length_is_the_one_its_storyboard_sums(store):
    story_id = nsp.native_story(store)
    ec = episode_common.load_context(store, story_id, 1)
    script = _at_plan(ec, eps._script(store, story_id))

    episode_common.retime(script, ec)
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    words = timing.episode_pass(script, ec.template, ec.language, style_lock=ec.style_lock)[0]["total_s"]
    told = script["timing"]["total_s"]
    assert gates.episode_length(ec, script)["total_s"] == told  # the gate reads the same clock

    board = storyboard_step.build_fast(store, story_id, 1, now=NOW, on_log=lambda line: None)

    assert gates.episode_length(ec, eps._script(store, story_id), board)["total_s"] == told
    assert told != words  # the words' clock said something else



# ---------------------------------------------------- tonight's shape: serial_60s_v2 before A2 re-slotted it

# serial_60s_v2's slots before plan 28 stage A2 (min_shot 3): an 11 s body slot that a narrator clip and a character
# clip (6 + 6 s on Veo) cannot share.
OLD_SLOTS = {"recap": [3.0, 4.0], "hook": [3.0, 6.0], "body": [5.0, 11.0], "cliffhanger": [4.0, 10.0]}
DROPPED = ("s08", "s09")  # tonight's episode had six body scenes


def _old_serial(monkeypatch):
    real = templates.load_episode_template

    def load(template_id):
        template = real(template_id)
        if template_id == "serial_60s_v2":
            for slot, duration in OLD_SLOTS.items():
                template["slots"][slot]["duration_s"] = list(duration)
            template["min_shot_s"] = 3.0
        return template

    monkeypatch.setattr(templates, "load_episode_template", load)


def _line(sid, k, speaker, words):
    text = _say(sid, k, words)
    return {"line_id": schemas.line_id_for(sid, k), "speaker": speaker, "text": text, "emotion": "neutral",
            "delivery": "calm", "timing": timing.estimated_timing(text, "fr")}


def _old_plan(scene, shots):
    """A stored plan of *shots* ``[(kind, speaker, clip_s, words)]``, one line a shot -- as the planner stored it
    before plan 28 stage A2 (a body scene's two clips past its slot)."""
    sid = scene["scene_id"]
    lines, planned = [], []
    for k, (kind, speaker, clip_s, words) in enumerate(shots):
        lines.append({"kind": kind, "speaker": speaker, "seconds": float(clip_s) - 0.7, "clip_s": clip_s,
                      "max_words": words + 2, "min_words": words})
        planned.append({"clip_s": clip_s, "line_ids": [schemas.line_id_for(sid, k)], "words_min": words,
                        "words_max": words + 2, "speaks": kind == "character"})
    scene["slot_s"] = list(OLD_SLOTS[timing.slot_name(scene["function"], templates.load_episode_template(
        "serial_60s_v2"))])
    scene["line_plan"] = {"allowed_speech_s": 9.0, "lines": lines, "max_words": sum(l["max_words"] for l in lines),
                          "min_words": sum(l["min_words"] for l in lines), "shots": planned}
    scene["lines"] = [_line(sid, k, speaker, words) for k, (_kind, speaker, _clip, words) in enumerate(shots)]
    scene["sfx_cues"] = []
    scene.pop("character_line", None)
    scene.pop("clip_cap_s", None)


def _tonight(store, monkeypatch):
    """Job 13bbb11a5896's episode: serial_60s_v2 with its old slots, the narrator on, Veo's 6/8 s clips, a hook, six
    body scenes each planned a narrator clip and a character clip (12 s in an 11 s slot), a cliffhanger: 86 s of
    clips against 55-75 s. Written, its checks to run."""
    _old_serial(monkeypatch)
    story_id = nsp.native_story(store)

    def v3(doc):
        doc["generation_profile"]["writing"] = "v3"
        doc["narrator"] = dict(doc["narrator"], enabled=True)

    store.update(story_id, v3, now=NOW)
    store.write_knowledge(story_id, eps._approved_knowledge(), now=NOW)
    ec = episode_common.load_context(store, story_id, 1)
    script = eps._script(store, story_id)
    script["spine"] = dict(eps.V3_SPINE)
    script["scenes"] = [scene for scene in script["scenes"] if scene["scene_id"] not in DROPPED]
    for scene in script["scenes"]:
        cast = scene["characters"][0]
        if scene["function"] == "hook":
            _old_plan(scene, [("narrator", "narrator", 6, 8)])
        elif scene["function"] == "cliffhanger":
            _old_plan(scene, [("narrator", "narrator", 8, 12)])
        else:
            _old_plan(scene, [("narrator", "narrator", 6, 6), ("character", cast, 6, 9)])
    script["consistency_report"] = None
    script.pop("first_watch", None)
    episode_common.retime(script, ec)
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    return story_id


def _plan_writer(call):
    """E2v3 written inside each planned line's range, its speaker the plan's (read from the prompt's plan lines)."""
    ids = {name: cid for cid, name in eps.NAMES.items()}
    tag = eps._v3_tag(call)
    lines = []
    for number, who, low, high in re.findall(r"Line (\d+) \(([^)]+)\): between (\d+) and (\d+) words",
                                              call["user"]):
        speaker = "narrator" if who == "narrator" else ids[who]
        lines.append({"speaker": speaker, "text": " ".join(f"r{number}{tag}{k}" for k in range((int(low) + int(high)) // 2)),
                      "emotion": "tension", "delivery": "quiet"})
    return {"lines": lines, "sfx_cues": [], "on_screen_text": None}


def _llm(e2v3=_plan_writer):
    return eps.FakeLLM(default={"E2v3": e2v3, "E4": eps.E4_PASSED, "J1v3": eps.J1_PASSED})


def _fast_track(store, story_id, llm, **params):
    ctx, log = eps._ctx(store, story_id, step="fast-track", params=dict({"storyboard": "fast"}, **params))
    run = fast_track._FastTrack(ctx, runner=llm, time_fn=eps.Clock(100.0), adapters=None, transport=None,
                                sleep_fn=lambda _s: None, transcribe=None, run_process=None, popen=None, clock=None,
                                detect=None, cover_process=None, custom_fonts_dir=None, profile="final")
    return run, log


def test_tonight_s_plans_are_timed_on_their_clips_and_each_scene_over_its_slot_is_flagged(store, monkeypatch):
    story_id = _tonight(store, monkeypatch)
    timed = eps._script(store, story_id)["timing"]

    assert (timed["state"], timed["total_s"]) == ("over", 86.0)  # 6 + 6 x 12 + 8: what the storyboard summed
    over = [flag for flag in timed["flags"] if flag["kind"] == "scene_over"]
    assert [flag["scene_id"] for flag in over] == ["s02", "s03", "s04", "s05", "s06", "s07"]
    assert over[0]["message"] == "Scene s02 is 1.0 s over its 11 s slot."
    assert all(timed["scenes"][flag["scene_id"]]["state"] == "over" for flag in over)
    episode = next(flag for flag in timed["flags"] if flag["kind"] == "episode_over")
    assert episode["message"] == "The episode is 11.0 s over 75 s." and episode["scene_id"] == "s02"


def test_the_fast_track_fits_tonight_s_episode_in_one_remedy_and_goes_on(store, monkeypatch):
    story_id = _tonight(store, monkeypatch)
    llm = _llm()
    run, log = _fast_track(store, story_id, llm)

    run.script()
    run.storyboard()

    assert "✂ Fitting episode 1: 6 scenes shortened (s02, s03, s04, s05, s06 and s07)" in log
    assert len(llm.of("E2v3")) == 6  # only the scenes whose plan changed, once each
    script = eps._script(store, story_id)
    assert script["approved_by"] == "fast_track" and script["timing"]["state"] != "over"
    assert all(len(scene["line_plan"]["shots"]) == 1 for scene in script["scenes"])  # the narrator alone
    board = eps._storyboard(store, story_id)
    assert board["approved_at"] is not None
    ec = episode_common.load_context(store, story_id, 1)
    assert gates.episode_length(ec, script, board)["total_s"] == script["timing"]["total_s"] <= 75


def test_a_remedy_that_cannot_write_stops_with_a1_s_sentence_and_continue_runs_it_again(store, monkeypatch):
    from clipping.providers.errors import ProviderError

    story_id = _tonight(store, monkeypatch)
    dead = _llm(e2v3=ProviderError("every provider failed", [("gemini/gemini-test", "HTTP 503")]))
    run, log = _fast_track(store, story_id, dead)

    with pytest.raises(eps.steps.StepFailed) as caught:
        run.script()

    assert str(caught.value) == ("Episode 1 cannot fit: its 8 scenes need at least 86 s of clips on this link, more "
                                 "than the 75 s this format allows. Pick a format that fits, or let the app choose "
                                 "one.")
    assert "✂ Fitting episode 1: 6 scenes shortened (s02, s03, s04, s05, s06 and s07)" in log
    assert any(line.startswith("✖ Fitting episode 1: scene s02 failed") for line in log)
    kept = eps._script(store, story_id)
    assert kept["timing"]["total_s"] == 86.0 and kept["approved_at"] is None  # nothing half-applied

    # Continue: a new run tries the remedy again instead of the same refusal.
    again, log = _fast_track(store, story_id, _llm())
    again.script()

    assert "✂ Fitting episode 1: 6 scenes shortened (s02, s03, s04, s05, s06 and s07)" in log
    assert eps._script(store, story_id)["approved_by"] == "fast_track"
