"""Plan 27 stage 1: shots are 5-10 s, clamped to the lengths their link sells;
a reaction shot is 6 s; nothing already made turns stale when the window
moves (a shot with a current clip keeps its ``clip_s`` and its length)."""

from __future__ import annotations

import pytest

import test_story_native_speech_plan as nsp
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures

VEO, KLING = "gemini/veo-3.1-lite", "fal/kling-2.5-turbo-std"
SEEDANCE, LTX = "fal/seedance-1-pro-fast", "fal/ltx-2.3-fast"


def test_the_window_clamps_each_link_to_what_it_sells_inside_5_to_10_s():
    from clipping.aistory.steps import clips

    assert clips.link_lengths(VEO) == (6, 8)
    assert clips.link_lengths(KLING) == (5, 10)
    assert clips.link_lengths(SEEDANCE) == (5, 6, 7, 8, 9, 10)
    assert clips.link_lengths(LTX) == (6, 8, 10)
    # A link with no table (a manual upload): the planned 6 and 8 s.
    assert clips.link_lengths("manual/upload") == (6, 8)
    assert clips.link_lengths(None) == (6, 8)


def test_a_link_with_nothing_inside_the_window_keeps_its_nearest_lengths():
    from clipping.aistory import native_speech

    assert native_speech.window_lengths((8,)) == (8,)  # Flow sells 8 s only
    assert native_speech.window_lengths((4,)) == (4,)
    assert native_speech.window_lengths((2, 3, 12, 14)) == (3, 12)
    assert native_speech.window_lengths((12, 14, 16)) == (12,)
    assert native_speech.window_lengths((4, 6, 8)) == (6, 8)
    assert native_speech.window_lengths(()) == ()


def test_the_lengths_a_story_buys_apply_the_window_to_a_native_speech_story_only(monkeypatch):
    from clipping.aistory import media_policy
    from clipping.aistory.steps import clips

    native = nsp._story_doc()
    assert media_policy.native_speech(native)
    assert clips.sold_lengths(native, VEO) == (6, 8)
    assert clips.sold_lengths(native, SEEDANCE) == (5, 6, 7, 8, 9, 10)
    assert clips.sold_lengths(native, KLING) is None  # (5, 10) is already the link's table inside the window
    assert clips.longest_clip_s(SEEDANCE, story=native) == 10
    # Any other story keeps the link's own table, as always.
    other = {"generation_profile": {"tier": 2, "pipeline": "v2"}}
    assert not media_policy.native_speech(other)
    assert clips.sold_lengths(other, SEEDANCE) is None and clips.sold_lengths(other, VEO) is None
    assert clips.longest_clip_s(SEEDANCE, story=other) == 12
    # DEC-258 composes: a lipsyncing story is capped at 10 s, then (if native) windowed.
    monkeypatch.setattr(media_policy, "lipsync", lambda story: True)
    assert clips.sold_lengths(other, SEEDANCE) == (2, 3, 4, 5, 6, 7, 8, 9, 10)
    monkeypatch.setattr(media_policy, "native_speech", lambda story: True)
    assert clips.sold_lengths(other, SEEDANCE) == (5, 6, 7, 8, 9, 10)


def test_a_flow_clip_planned_at_6_s_is_picked_at_8_s():
    from clipping.aistory import platforms

    preset = platforms.load("flow")
    _name, model = platforms.model_of(preset)
    assert platforms.length_for(model, 6) == 8 and platforms.length_for(model, 8) == 8


def test_the_two_native_templates_are_5_to_10_s_with_slots_that_fit_a_5_s_floor_and_a_6_s_reaction():
    from clipping.aistory import native_speech, templates

    for template_id in ("narrated_drama_60s_v2", "confrontation_50s_v2"):
        tpl = templates.load_episode_template(template_id)
        assert (tpl["min_shot_s"], tpl["max_shot_s"]) == native_speech.SHOT_WINDOW_S, template_id
        slots = tpl["slots"]
        assert slots["recap"]["duration_s"][0] >= 5, template_id
        assert slots["hook"]["duration_s"] == [5.0, 8.0] and slots["cliffhanger"]["duration_s"] == [6.0, 10.0]
        assert slots["body"]["duration_s"] == [10.0, 16.0]
        assert native_speech.REACTION_S >= tpl["min_shot_s"]


def _kept(clip_s, duration_s, state="current"):
    return {"clip_s": clip_s, "duration_s": duration_s, "assets": {"clip": {"state": state}}}


def test_a_kept_shot_with_a_current_clip_keeps_its_4_s_and_a_shot_without_one_takes_the_window():
    from clipping.aistory import shots

    plan = {"clip_s": 6, "speaks": True}
    made, fresh = {}, {}
    shots._speech_fields(made, plan, _kept(4, 3.1))
    assert (made["clip_s"], made["duration_s"], made["speaks"]) == (4, 3.1, True)
    shots._speech_fields(fresh, plan, _kept(4, 3.1, state="stale"))
    assert (fresh["clip_s"], fresh["duration_s"]) == (6, 6.0)
    new = {}
    shots._speech_fields(new, plan, None)
    assert (new["clip_s"], new["duration_s"]) == (6, 6.0)
    # A kept shot with no clip recorded at all is a new one.
    bare = {}
    shots._speech_fields(bare, plan, {"clip_s": 4, "duration_s": 4.0, "assets": {"clip": None}})
    assert (bare["clip_s"], bare["duration_s"]) == (6, 6.0)


def test_a_made_shot_under_the_new_floor_validates_and_an_unmade_one_does_not(store):
    """The 5 s floor binds the shots still to be made: a board with a 4 s shot that has a current clip
    (made under the old floor) validates; the same shot without a current clip is refused."""
    from clipping.aistory import schemas

    import test_story_assets_step as tas

    story_id = nsp.planned_story(store)
    board = tas._board(store, story_id)
    shot = next(item for item in board["shots"] if item.get("speaks"))
    shot["duration_s"] = 4.0
    errors = schemas.storyboard_errors(board, min_shot_s=5)
    assert any("minimum shot length" in error for error in errors)
    shot["assets"]["clip"] = {"state": "current", "link": nsp.FAST, "route": "paid", "clip_s": 4, "est_usd": 0.6,
                              "prompt_hash": "0" * 64, "image_sha256": "0" * 64, "cache_key": None,
                              "generated_at": nsp.NOW}
    assert schemas.storyboard_errors(board, min_shot_s=5) == []
    shot["assets"]["clip"]["state"] = "stale"
    assert any("minimum shot length" in error for error in schemas.storyboard_errors(board, min_shot_s=5))
