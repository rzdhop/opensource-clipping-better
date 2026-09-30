"""Tests for clipping.aistory.video_plan (phase 6 plan stage 1: the pure
video-planning module -- camera phrases, the clip-length table, the greedy
per-episode animate planner and the route decision).

The module is brand new, so this file first fails with ``ModuleNotFoundError``
before ``video_plan.py`` exists (the expected fail-first state); every test
below is essential-only (DEC-192): one fail-first behaviour per group plus a
guard of the working path. Stdlib and pytest only (DEC-012).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipping.aistory import schemas, video_plan

ROOT = Path(__file__).resolve().parents[1]
STYLES_DIR = ROOT / "clipping" / "aistory" / "templates" / "styles"
BUDGET_PROFILES_PATH = ROOT / "clipping" / "aistory" / "templates" / "budget_profiles.json"


def _load_style_json(template_id: str) -> dict:
    with open(STYLES_DIR / f"{template_id}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _shot(shot_id, order, scene_id, duration_s, *, action="A character reacts.", camera_motion="hold",
          modifiers=(), lines=(), negative_prompt="", keep_still=False, animate=None):
    shot = {
        "shot_id": shot_id,
        "order": order,
        "scene_id": scene_id,
        "duration_s": duration_s,
        "action": action,
        "camera_motion": camera_motion,
        "modifiers": list(modifiers),
        "lines": list(lines),
        "negative_prompt": negative_prompt,
        "keep_still": keep_still,
    }
    if animate is not None:
        shot["animate"] = animate
    return shot


# ============================================================ camera phrases

def test_camera_phrase_keys_match_the_closed_list_and_every_phrase_is_non_empty():
    assert set(video_plan.CAMERA_PHRASES) == set(schemas.CAMERA_MOTIONS)
    for phrase in video_plan.CAMERA_PHRASES.values():
        assert isinstance(phrase, str) and phrase.strip()


def test_modifier_phrase_keys_match_the_closed_list_and_every_phrase_is_non_empty():
    assert set(video_plan.MODIFIER_PHRASES) == set(schemas.MODIFIERS)
    for phrase in video_plan.MODIFIER_PHRASES.values():
        assert isinstance(phrase, str) and phrase.strip()


# ========================================================== build_video_prompt

def test_tier2_prompt_orders_action_then_suffix_then_camera_and_excludes_lines():
    style = _load_style_json("cartoon_flat")
    shot = _shot("sh01", 1, "s01", 3.0, action="A cat pounces onto the table",
                  camera_motion="push_in", lines=("l00",))
    prompt, negative = video_plan.build_video_prompt(shot, style, tier=2, lines=("Hello there!",))

    action_i = prompt.index("A cat pounces onto the table")
    suffix_i = prompt.index(style["motion_rules"]["tier2_prompt_suffix"])
    camera_i = prompt.index(video_plan.CAMERA_PHRASES["push_in"])
    assert action_i < suffix_i < camera_i
    assert "Hello there" not in prompt
    assert video_plan.MOTION_NEGATIVE in negative
    assert not prompt.endswith(" ") and not prompt.endswith("..")


def test_tier3_prompt_includes_the_line_text_as_a_speech_cue():
    style = _load_style_json("cartoon_flat")
    shot = _shot("sh02", 2, "s01", 3.0, camera_motion="hold", lines=("l00",))
    prompt, _negative = video_plan.build_video_prompt(shot, style, tier=3, lines=("Hello there!",))
    assert "Hello there!" in prompt
    assert prompt.index(style["motion_rules"]["tier2_prompt_suffix"]) < prompt.index("Hello there!")


def test_negative_prefers_the_shots_own_negative_prompt_and_dedupes_motion_negative():
    style = _load_style_json("cartoon_flat")
    shot = _shot("sh03", 3, "s01", 3.0, negative_prompt=f"blurry, {video_plan.MOTION_NEGATIVE}")
    _prompt, negative = video_plan.build_video_prompt(shot, style, tier=2)
    assert negative.count("watermark") == 1
    assert "blurry" in negative

    shot_no_negative = _shot("sh04", 4, "s01", 3.0, negative_prompt="")
    _prompt, negative2 = video_plan.build_video_prompt(shot_no_negative, style, tier=2)
    assert style["negative_prompt"] in negative2 or all(
        term.strip() in negative2 for term in style["negative_prompt"].split(",")
    )
    assert video_plan.MOTION_NEGATIVE.split(",")[0].strip() in negative2


def test_build_video_prompt_rejects_bad_tier_and_unknown_camera_motion():
    style = _load_style_json("cartoon_flat")
    shot = _shot("sh05", 5, "s01", 3.0, camera_motion="hold")
    with pytest.raises(ValueError):
        video_plan.build_video_prompt(shot, style, tier=1)
    with pytest.raises(ValueError):
        video_plan.build_video_prompt(shot, style, tier=4)

    bad_motion_shot = _shot("sh06", 6, "s01", 3.0, camera_motion="zoom_spin")
    with pytest.raises(ValueError):
        video_plan.build_video_prompt(bad_motion_shot, style, tier=2)


def test_note_is_appended_as_a_final_direction_sentence():
    style = _load_style_json("cartoon_flat")
    shot = _shot("sh07", 7, "s01", 3.0, camera_motion="hold")
    prompt, _negative = video_plan.build_video_prompt(shot, style, tier=2, note="Make it snappier")
    assert prompt.endswith("Make it snappier")


# =============================================================== clip lengths

@pytest.mark.parametrize("link,duration_s,expected", [
    ("fal/seedance-1-pro-fast", 1.2, 2),
    ("fal/seedance-1-pro-fast", 5.0, 5),
    ("fal/seedance-1-pro-fast", 13, 12),
    ("fal/ltx-2.3-fast", 5, 6),
    ("fal/ltx-2.3-fast", 9, 10),
    ("fal/ltx-2.3-fast", 11, 10),
    ("fal/kling-2.5-turbo-std", 3, 5),
    ("fal/kling-2.5-turbo-std", 6, 10),
    ("gemini/veo-3.1-lite", 3, 4),
    ("gemini/veo-3.1-lite", 4.1, 6),
    ("gemini/veo-3.1-lite", 9, 8),
])
def test_requested_seconds_table(link, duration_s, expected):
    assert video_plan.requested_seconds(link, duration_s) == expected


def test_requested_seconds_lengths_override_and_error_cases():
    assert video_plan.requested_seconds("local/comfyui-template", 5, lengths=(4, 8, 12)) == 8
    with pytest.raises(ValueError):
        video_plan.requested_seconds("not/a-real-link", 5)
    with pytest.raises(ValueError):
        video_plan.requested_seconds("fal/seedance-1-pro-fast", 0)
    with pytest.raises(ValueError):
        video_plan.requested_seconds("fal/seedance-1-pro-fast", -1)


# =================================================================== planner

LINK = "fal/seedance-1-pro-fast"
PRICE = 0.10

SCENE_FUNCTION = {
    "s01": "hook",
    "s02": "rising",
    "s03": "peak",
    "s04": "turn",
    "s05": "cliffhanger",
}


def _fixture_shots():
    return [
        _shot("sh01", 1, "s01", 2.0),                      # hook
        _shot("sh02", 2, "s02", 4.0),                       # rising, longest dialogue
        _shot("sh03", 3, "s02", 2.0, keep_still=True),       # excluded outright
        _shot("sh04", 4, "s03", 3.0),                        # peak
        _shot("sh05", 5, "s04", 6.0),                        # turn, expensive: skipped over cap
        _shot("sh06", 6, "s05", 2.0),                        # cliffhanger
        _shot("sh07", 7, "s02", 3.0),                        # rising, already current -> $0
        _shot("sh08", 8, "s02", 1.0),                        # rising, cheap: fits after sh05/sh02 skip
    ]


DIALOGUE_SECONDS = {"sh02": 5.0, "sh07": 1.0, "sh08": 0.5}


def test_plan_animation_priority_order_skip_and_continue_and_current_clip():
    plan = video_plan.plan_animation(
        _fixture_shots(), SCENE_FUNCTION, DIALOGUE_SECONDS,
        link=LINK, price_per_second=PRICE, cap_usd=1.00, committed_usd=0.05,
        current_shot_ids=("sh07",), mode="key_shots_within_cap",
    )

    assert [(e["shot_id"], e["why"]) for e in plan.selected] == [
        ("sh01", "hook"),
        ("sh06", "cliffhanger"),
        ("sh04", "peak"),
        ("sh07", "current"),
        ("sh08", "longest_dialogue"),
    ]
    current_entry = next(e for e in plan.selected if e["shot_id"] == "sh07")
    assert current_entry["est_usd"] == 0.0

    assert [(e["shot_id"], e["reason"]) for e in plan.still] == [
        ("sh02", "over_cap"),
        ("sh03", "keep_still"),
        ("sh05", "over_cap"),
    ]

    assert plan.seconds == 9  # sh01(2) + sh06(2) + sh04(3) + sh08(2); sh07 excluded (current)
    assert plan.video_usd == pytest.approx(0.90)
    assert plan.image_usd == 0.05
    assert plan.left_usd == pytest.approx(0.05)
    assert plan.over_cap is False


def test_plan_animation_pinned_shot_is_always_selected_even_over_cap():
    shots = [_shot("p1", 1, "s03", 10.0, animate=True)]
    plan = video_plan.plan_animation(
        shots, {"s03": "peak"}, {},
        link=LINK, price_per_second=PRICE, cap_usd=0.30, committed_usd=0.0,
        mode="key_shots_within_cap",
    )
    assert plan.selected == [{"shot_id": "p1", "clip_s": 10, "est_usd": pytest.approx(1.0), "why": "pinned"}]
    assert plan.still == []
    assert plan.over_cap is True
    assert plan.left_usd == pytest.approx(-0.70)


def test_plan_animation_counts_only_new_clips_in_seconds():
    # A pinned shot whose clip is already current keeps its "pinned" label but buys nothing.
    shots = [_shot("p1", 1, "s03", 5.0, animate=True), _shot("sh02", 2, "s01", 2.0)]
    plan = video_plan.plan_animation(
        shots, {"s01": "hook", "s03": "peak"}, {},
        link=LINK, price_per_second=PRICE, cap_usd=1.00, committed_usd=0.0,
        current_shot_ids={"p1"}, mode="key_shots_within_cap",
    )
    assert [entry["why"] for entry in plan.selected] == ["pinned", "hook"]
    assert plan.selected[0]["est_usd"] == 0.0
    assert plan.seconds == 2


def test_plan_animation_mode_none_selects_only_pins():
    shots = _fixture_shots()
    plan = video_plan.plan_animation(
        shots, SCENE_FUNCTION, DIALOGUE_SECONDS,
        link=LINK, price_per_second=PRICE, cap_usd=1.00, committed_usd=0.0,
        current_shot_ids=("sh07",), mode="none",
    )
    assert plan.selected == []
    still_reasons = {e["shot_id"]: e["reason"] for e in plan.still}
    assert still_reasons["sh03"] == "keep_still"
    assert all(reason == "mode_none" for shot_id, reason in still_reasons.items() if shot_id != "sh03")
    assert set(still_reasons) == {shot["shot_id"] for shot in shots}


def test_plan_animation_mode_all_shots_selects_everything_and_reports_over_cap():
    plan = video_plan.plan_animation(
        _fixture_shots(), SCENE_FUNCTION, DIALOGUE_SECONDS,
        link=LINK, price_per_second=PRICE, cap_usd=1.00, committed_usd=0.05,
        current_shot_ids=("sh07",), mode="all_shots",
    )
    selected_ids = {e["shot_id"] for e in plan.selected}
    assert selected_ids == {"sh01", "sh02", "sh04", "sh05", "sh06", "sh07", "sh08"}
    assert plan.still == [{"shot_id": "sh03", "reason": "keep_still"}]
    assert plan.over_cap is True
    current_entry = next(e for e in plan.selected if e["shot_id"] == "sh07")
    assert current_entry["why"] == "current"
    assert current_entry["est_usd"] == 0.0


def test_animate_priority_default_matches_budget_profiles_json():
    with open(BUDGET_PROFILES_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    assert list(video_plan.ANIMATE_PRIORITY) == data["profiles"]["one_dollar"]["animate_priority"]


def test_plan_animation_rejects_unknown_mode():
    with pytest.raises(ValueError):
        video_plan.plan_animation([], {}, {}, link=LINK, price_per_second=PRICE,
                                    cap_usd=1.0, committed_usd=0.0, mode="sometimes")


# ================================================================= video_route

@pytest.mark.parametrize("route,local_ready,api_ready,allow_paid,expected", [
    ("local", True, False, False, ("local", "")),
    ("local", False, True, True, (None, "no local ComfyUI is ready")),
    ("api", False, True, True, ("api", "")),
    ("api", False, False, True, (None, "no API video link is ready")),
    ("api", False, True, False, (None, "allow_paid is off")),
    ("auto", True, False, False, ("local", "")),
    ("auto", False, True, True, ("api", "")),
])
def test_video_route_decision_table(route, local_ready, api_ready, allow_paid, expected):
    assert video_plan.video_route(
        route, local_ready=local_ready, api_ready=api_ready, allow_paid=allow_paid,
    ) == expected


def test_video_route_auto_names_both_missing_conditions_and_rejects_unknown_route():
    result, reason = video_plan.video_route("auto", local_ready=False, api_ready=False, allow_paid=False)
    assert result is None
    assert "local" in reason and ("api" in reason or "allow_paid" in reason)

    with pytest.raises(ValueError):
        video_plan.video_route("sideways", local_ready=True, api_ready=True, allow_paid=True)
