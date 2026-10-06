"""Plan 28 F1+F2 (DEC-305 §5, the human: "strict rules to avoid consistency
problems, and all details"): the keyframe judge (J2) -- since DEC-311 a
warning, never a gate (the DEC-307 rule applied to shots; the human: "We
want fun videos, not exactly the right things").

- A failed or unjudged keyframe check no longer refuses the keyframe
  approval: the shot is approved with its issues kept as a warning
  (``keyframes_approved.shots``: what the check saw and the very image
  approved), counted only while the keyframe is that image.
- The auto-fix's budget is sized to the episode: its shots x the redraws a
  shot x one keyframe on the episode's image link.
- J2 checks the head, species, skin and material and the outfit, apart from
  the continuity, and sees the set's plate and the props in frame.

(The fast track's own is ``tests/test_story_fast_track_one_click.py``'s,
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


def test_a_failed_check_is_approved_with_its_warning_naming_what_the_judge_saw(store, tmp_path, built):
    """DEC-311, re-pinned on purpose (it was refused, plan 28 F1): the shot
    is approved with what the judge saw kept on the approval, and
    ``approve_anyway`` is accepted and ignored -- the same record either way."""
    story_id = kg._v2_keyframes(store, tmp_path, built)
    kg._run(store, story_id, vision=kg.FakeVision(_sheet_mismatch("sh02")), params={"animate": False})
    sha = kg._doc(store, story_id)["keyframe_verdicts"]["sh02"]["image_sha256"]

    for anyway in (False, True):
        approved = kg._approve_keyframes(store, story_id, approve_anyway=anyway)["keyframes_approved"]
        assert approved["anyway"] is True and approved["flagged"] == ["sh02"]
        assert approved["shots"] == {"sh02": {"issues": ["Gaston's head is a pear, the sheet shows a pineapple"],
                                              "image_hash": sha}}
    assert kg._doc(store, story_id)["keyframes_approved"]["shots"]["sh02"]["image_hash"] == sha


def test_an_unjudged_keyframe_is_approved_with_the_warning_no_keyframe_check_yet(store, tmp_path, built):
    """DEC-311, re-pinned on purpose (it was refused, plan 28 F1): unlike an
    entity's image, a shot never checked is approved too, its warning "no
    keyframe check yet"."""
    story_id = kg._v2_keyframes(store, tmp_path, built)
    kg._run(store, story_id, params={"animate": False})
    _drop_verdict(store, story_id, "sh03")

    approved = kg._approve_keyframes(store, story_id, approve_anyway=True)["keyframes_approved"]

    assert approved["anyway"] is True and approved["flagged"] == ["sh03"]
    assert approved["shots"]["sh03"]["issues"] == ["no keyframe check yet"]
    assert set(approved["shots"]) == {"sh03"}


def test_a_keyframe_approved_with_its_warning_counts_only_while_it_is_that_very_image(store, tmp_path, built):
    """DEC-311 (``judge.keyframe_approved_anyway``, the entities' rule of
    ``tests/test_story_sheet_gate.py``): the record of a shot approved with
    the check's warning counts while its keyframe is the image approved; a
    regenerated keyframe clears it, and approving again records the new one."""
    import hashlib

    import test_story_fast_track_one_click as oc
    from clipping.aistory import workflow
    from clipping.aistory.steps import assets, judge

    story_id = kg._v2_keyframes(store, tmp_path, built)
    kg._run(store, story_id, vision=kg.FakeVision(_sheet_mismatch("sh02")), params={"animate": False})
    approved = kg._approve_keyframes(store, story_id)["keyframes_approved"]
    sha = approved["shots"]["sh02"]["image_hash"]
    assert judge.keyframe_approved_anyway(approved, "sh02", sha) is True
    assert judge.keyframe_approved_anyway(approved, "sh01", sha) is False  # a shot it never went over
    shots = workflow.episode_review(oc._page(store, story_id))["approvals"]["keyframes"]["shots"]
    assert shots == {"sh02": {"issues": ["Gaston's head is a pear, the sheet shows a pineapple"], "image_hash": sha,
                              "current": True}}

    # What a regenerate leaves: another file for sh02's keyframe -- the record no longer counts.
    ec = kg.tas._ec(store, story_id)
    shot = next(item for item in kg.tas._board(store, story_id)["shots"] if item["shot_id"] == "sh02")
    path = assets.shot_image_path(ec, shot)
    with open(path, "ab") as handle:
        handle.write(b"a new drawing")
    with open(path, "rb") as handle:
        fresh = hashlib.sha256(handle.read()).hexdigest()
    assert fresh != sha and judge.keyframe_approved_anyway(approved, "sh02", fresh) is False
    review = workflow.episode_review(oc._page(store, story_id))
    assert review["approvals"]["keyframes"]["approval"] == "stale"
    assert review["approvals"]["keyframes"]["shots"]["sh02"]["current"] is False

    # Approved again, the record follows the new image (not checked yet: its warning says so).
    again = kg._approve_keyframes(store, story_id, now="2026-10-06T10:00:00+00:00")["keyframes_approved"]
    assert again["shots"]["sh02"] == {"issues": ["no keyframe check yet"], "image_hash": fresh}
    assert judge.keyframe_approved_anyway(again, "sh02", fresh) is True


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
