"""Plan 28 F1+F2 (DEC-305 §5, the human: "strict rules to avoid consistency
problems, and all details"): the keyframe judge (J2) is a hard gate.

- A failed or unjudged keyframe check is refused by the keyframe approval
  even with ``approve_anyway``, in plain sentences naming each shot and
  what the judge saw ("Shot sh04 does not match: ... Regenerate it, or
  upload your own.").
- The auto-fix's budget is sized to the episode: its shots x the redraws a
  shot x one keyframe on the episode's image link.
- J2 checks the head, species, skin and material and the outfit, apart from
  the continuity, and sees the set's plate and the props in frame.

(The fast track's stop is ``tests/test_story_fast_track_one_click.py``'s,
re-pinned; the dashboard's, ``tests/test_dashboard_keyframes_approve.py``'s.)

The episode is stage 6b's v2 fixture (``tests/test_story_keyframe_gate.py``).
Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import test_story_keyframe_gate as kg
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_keyframe_gate import built, unpaced  # noqa: F401
from test_story_video_phase import timings_path  # noqa: F401


def _sheet_mismatch(shot_id):
    def answer(request):
        if f"Image 1 is the keyframe of shot {shot_id}." in request.prompt:
            return json.dumps({"shows_beat": True, "missing": [], "continuity_issue": None,
                               "sheet_issues": ["Gaston's head is a pear, the sheet shows a pineapple"]})
        return kg.PASS
    return answer


def test_a_failed_check_is_refused_even_with_approve_anyway_naming_what_the_judge_saw(store, tmp_path, built):
    from clipping.aistory import workflow

    story_id = kg._v2_keyframes(store, tmp_path, built)
    kg._run(store, story_id, vision=kg.FakeVision(_sheet_mismatch("sh02")), params={"animate": False})

    for anyway in (False, True):
        with pytest.raises(workflow.WorkflowError) as caught:
            kg._approve_keyframes(store, story_id, approve_anyway=anyway)
        assert str(caught.value) == ("Episode 1's keyframes are not approved. Shot sh02 does not match: Gaston's "
                                     "head is a pear, the sheet shows a pineapple. Regenerate it, or upload your "
                                     "own.")
    assert "keyframes_approved" not in kg._doc(store, story_id)


def test_an_unjudged_keyframe_is_refused_even_with_approve_anyway(store, tmp_path, built):
    from clipping.aistory import workflow

    story_id = kg._v2_keyframes(store, tmp_path, built)
    kg._run(store, story_id, params={"animate": False})
    _drop_verdict(store, story_id, "sh03")

    with pytest.raises(workflow.WorkflowError) as caught:
        kg._approve_keyframes(store, story_id, approve_anyway=True)
    assert str(caught.value) == ("Episode 1's keyframes are not approved. Shot sh03 has no keyframe check yet: run "
                                 "the assets step again (it checks them, free).")


def _drop_verdict(store, story_id, shot_id):
    doc = kg._doc(store, story_id)
    doc["keyframe_verdicts"].pop(shot_id)
    store.write_episode_doc(story_id, 1, "assets.json", doc, now=kg.LATER)


@pytest.mark.parametrize("shots, link, ceiling", [
    (10, "fal/seedream-4.5-edit", 0.80), (14, "fal/seedream-4.5-edit", 1.12),
    (10, "gemini/nano-banana-2-lite", 0.672), (14, "gemini/nano-banana-2-lite", 0.9408),
])
def test_the_redraw_budget_is_the_episode_s_shots_x_two_redraws_x_the_link_s_price(shots, link, ceiling):
    from clipping.aistory import media_policy
    from clipping.providers import budget, pricing

    for name in ("quality", "native_speech", "native_speech_manual"):
        fix = budget.load_profiles()["profiles"][name]["keyframe_fix"]
        assert fix == {"max_redraws_per_shot": 2, "cap_rule": "shots_x_redraws_x_price"}, name
    story = {"generation_profile": {"pipeline": "v2", "budget_profile": "native_speech_manual"}}
    settings = media_policy.keyframe_fix(story)
    unit = pricing.PRICES[link].usd
    assert media_policy.keyframe_fix_cap(settings, shots=shots, unit_usd=unit) == pytest.approx(ceiling)
    # A profile with a fixed cap keeps it, whatever the shots.
    assert media_policy.keyframe_fix_cap({"max_redraws_per_shot": 2, "cap_usd": 0.4, "cap_rule": None},
                                         shots=shots, unit_usd=unit) == 0.4


def test_j2_asks_the_head_and_the_outfit_and_sees_the_set_s_plate_and_the_props():
    from clipping.aistory import prompts
    from clipping.aistory.steps import judge

    look = {"presentation": "adult man", "build": "stocky", "face": "round eyes", "hair": "none",
            "skin_material": "pear skin", "species": "pear",
            "wardrobe_sets": [{"id": "daily", "context": "every day", "items": "a blue apron"}]}
    ec = SimpleNamespace(entities={"characters": {"char_g": {"name": "Gaston", "descriptor": "A pear", "look": look}},
                                   "places": {"place_k": {"name": "The kitchen"}},
                                   "props": {"prop_k": {"name": "The knife", "descriptor": "a steel knife"}}})
    shot = {"shot_id": "sh04", "framing": "medium_shot", "subject_tags": ["@char_g", "#place_k:night", "%prop_k"],
            "action": "@char_g lifts %prop_k."}
    context = judge.KeyframeContext(sheets={"char_g": "/s/gaston.png"}, scenes={"sh04": "s02", "sh03": "s02"},
                                    plates={"sh04": "/p/kitchen_night.png"},
                                    props={"sh04": [("The knife", "/p/knife.png")]})

    request, has_previous = judge.j2_request(ec, shot, "/k/sh04.png", "sh03", "/k/sh03.png", context)

    assert request.images == ("/k/sh04.png", "/k/sh03.png", "/s/gaston.png", "/p/kitchen_night.png", "/p/knife.png")
    assert ("Image 3 is Gaston's character sheet. Image 4 is the set. Image 5 is The knife."
            in request.prompt)
    assert "- Gaston: Head: pear (a whole fruit/vegetable head, the face carved into it)" in request.prompt
    assert "wearing a blue apron" in request.prompt
    assert "  - the head, species, skin and material (\"Gaston's head is a pear, the sheet shows a pineapple\")\n" \
        in request.prompt
    assert "  - the outfit: the one written above, never the sheet's\n" in request.prompt
    assert has_previous and prompts.J2_PROMPT_VERSION == 3 and len(request.images) <= prompts.J2_MAX_IMAGES
    # A sheet issue fails the verdict and is said first.
    entry = {"shows_beat": True, "missing": [], "continuity_issue": None,
             "sheet_issues": ["Gaston's head is a pear, the sheet shows a pineapple"]}
    assert not judge.verdict_passed(entry)
    assert judge.verdict_text(entry) == "Gaston's head is a pear, the sheet shows a pineapple"


def test_a_keyframe_the_human_uploaded_is_judged_and_warned_about_never_refused(store, tmp_path, built):
    """The human's own keyframe is their consistency decision: J2 still
    judges it, its issues are a warning on the shot ("The check saw: ..."),
    and the approval goes through, recording it as flagged."""
    # The upload path decodes with PIL, which CI does not install: skip there, as test_api_shot_upload does.
    Image = pytest.importorskip("PIL.Image")

    import test_story_fast_track_one_click as oc
    from clipping.aistory import manual_uploads, workflow

    story_id = kg._v2_keyframes(store, tmp_path, built)
    workflow.patch_shot_mode(store, story_id, 1, "sh02", {"image": "manual"}, now=kg.LATER, env=kg.SETTINGS)
    own = tmp_path / "own.png"
    Image.new("RGB", (1080, 1920), (200, 40, 40)).save(own, format="PNG")
    manual_uploads.accept_keyframe(store, story_id, 1, "sh02", str(own), env=kg.SETTINGS)
    kg._run(store, story_id, vision=kg.FakeVision(_sheet_mismatch("sh02")), params={"animate": False})
    assert kg._doc(store, story_id)["keyframe_verdicts"]["sh02"]["sheet_issues"] == [
        "Gaston's head is a pear, the sheet shows a pineapple"]

    approved = kg._approve_keyframes(store, story_id)["keyframes_approved"]

    assert approved["anyway"] is False and approved["flagged"] == ["sh02"]
    shot = next(item for item in workflow.episode_review(oc._page(store, story_id))["shots"]
                if item["shot_id"] == "sh02")
    assert shot["verdict"]["state"] == "flagged"
    assert shot["warning"] == "The check saw: Gaston's head is a pear, the sheet shows a pineapple"
