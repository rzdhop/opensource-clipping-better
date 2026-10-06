"""Plan 28 F7 (DEC-305 section 5, the human: "strict rules to avoid
consistency problems, and all details"): the Handoff gate.

1. Each shot's row says its keyframe's check (J2) in plain words -- the
   card, the brief's markdown and the shot's zip ("The check saw: ...") --
   and its clip is not taken until an app-made keyframe is current and
   passed (the human's own keyframe: warned, allowed).
2. A shot's references favour identity -- the keyframe, the speaker's
   sheet, the other characters' sheets, the plate, the props -- cut to the
   platform's cap with a plain line saying what was left out.
3. The first frame of an uploaded clip is compared with its keyframe on the
   free vision chain: a warning kept on the clip and said on the card, never
   a refusal.

Stdlib + pytest (DEC-012); the stories are the keyframe gate's v2 episode and
the manual-link native-speech story, every call a fake.
"""

from __future__ import annotations

import io
import json
import pathlib
import zipfile

import pytest

import test_story_assets_step as tas
import test_story_keyframe_gate as kg
import test_story_keyframe_hard_gate as khg
import test_story_manual_link as tml
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_keyframe_gate import built, unpaced  # noqa: F401
from test_story_video_phase import timings_path  # noqa: F401

NOW = tas.NOW


# ------------------------------------------------------------ 1. the keyframe check

def test_each_shot_says_its_keyframe_check_and_an_app_keyframe_gates_its_clip(store, tmp_path, built):
    from clipping.aistory import workflow
    from clipping.aistory.steps import assets, brief

    story_id = kg._v2_keyframes(store, tmp_path, built)
    kg._run(store, story_id, vision=kg.FakeVision(khg._sheet_mismatch("sh02")), params={"animate": False})
    ec = tas._ec(store, story_id)
    board = tas._board(store, story_id)
    doc = assets._read_assets_doc(ec)
    by_id = {shot["shot_id"]: shot for shot in board["shots"]}

    assert brief.keyframe_check(ec, board, by_id["sh01"], doc) == {
        "state": "passed", "own": False, "line": "The keyframe check passed.", "upload_refusal": None}
    failed = brief.keyframe_check(ec, board, by_id["sh02"], doc)
    assert failed == {
        "state": "failed", "own": False,
        "line": "The check saw: Gaston's head is a pear, the sheet shows a pineapple.",
        "upload_refusal": ("Shot sh02's keyframe does not match (the check saw: Gaston's head is a pear, the sheet "
                           "shows a pineapple): regenerate it, or upload your own, before its clip.")}
    # No current check: the keyframe as it is now was never judged.
    khg._drop_verdict(store, story_id, "sh03")
    unjudged = brief.keyframe_check(ec, board, by_id["sh03"], assets._read_assets_doc(ec))
    assert unjudged["state"] == "unjudged" and unjudged["upload_refusal"] == (
        "Shot sh03's keyframe has no check yet: run the assets step again (it checks it, free), then upload its "
        "clip.")
    # The human's own keyframe: said, never a refusal.
    workflow.patch_shot_mode(store, story_id, 1, "sh02", {"image": "manual"}, now=kg.LATER, env=kg.SETTINGS)
    own = brief.keyframe_check(ec, board, by_id["sh02"], assets._read_assets_doc(ec))
    assert own["own"] is True and own["upload_refusal"] is None


def test_a_clip_is_not_taken_before_its_app_made_keyframe_exists_and_passed(store, tmp_path):
    import shutil

    import test_story_native_take as tnt
    from clipping.aistory import manual_uploads

    tnt._require_ffmpeg()
    story_id = tml.manual_story(store)
    shot = tas._board(store, story_id)["shots"][0]
    received = f"{manual_uploads.clips_folder(store, story_id, 1)}/.upload.part"
    shutil.copyfile(tnt.make_clip(tmp_path / "take.mp4", 8), received)
    with pytest.raises(manual_uploads.UploadRefused) as caught:
        manual_uploads.accept_clip(store, story_id, 1, shot["shot_id"], received, filename="t.mp4",
                                   env=tas._settings())
    assert caught.value.status == 409
    assert str(caught.value) == (f"Shot {shot['shot_id']} has no keyframe yet: make it first (the assets step), "
                                 "then upload its clip.")
    assert tas._board(store, story_id)["shots"][0]["assets"].get("clip") is None


# ------------------------------------------------------------- 2. the references

def test_the_cut_says_what_the_platform_left_out_in_plain_words():
    from clipping.aistory.steps import brief

    flow = {"platform": "flow", "name": "Google Flow (Veo 3.1)"}
    plate = {"kind": "plate", "label": "La Piscine — place plate (day)"}
    assert brief.references_cut(flow, {"max_references": 3}, [plate]) == (
        "Flow takes 3 images: the plate was left out, the prompt describes it.")
    sheet = {"kind": "sheet", "label": "Broccolia — character sheet (portrait)"}
    phone = {"kind": "prop", "label": "Téléphone en noix de coco — object"}
    assert brief.references_cut(flow, {"max_references": 3}, [sheet, plate, phone]) == (
        "Flow takes 3 images: Broccolia's sheet, the plate and Téléphone en noix de coco were left out, the prompt "
        "describes them.")
    assert brief.references_cut(flow, {"max_references": 3}, []) is None


def test_the_references_put_identity_first_then_the_set_then_the_props(store):
    import test_story_storyboard_props as tsp

    story_id = tml.manual_story(store)
    tml._planted(store, story_id)
    tsp._plant_image(store, story_id, "props", "prop_telephone_en_noix_de_coco", "image.jpg")
    board = tas._board(store, story_id)
    shot = next(item for item in board["shots"]
                if any(tag.startswith("%") for tag in item["subject_tags"])
                and sum(tag.startswith("@") for tag in item["subject_tags"]) >= 2)
    tml._keyframe(store, story_id, shot["shot_id"])

    flow = next(entry for entry in tml._brief(store, story_id, "flow")["shots"] if entry["shot_id"] == shot["shot_id"])
    assert [ref["kind"] for ref in flow["references"]] == ["keyframe", "sheet", "sheet"]
    assert flow["references_cut"].startswith("Flow takes 3 images: ")
    assert "the plate" in flow["references_cut"] and flow["references_cut"].endswith("describes them.")
    # Uncut, the order is identity first (the keyframe, the speaker's sheet, the others'), the set, the props.
    from clipping.aistory.steps import brief

    ordered = brief.references(tas._ec(store, story_id), tas.eps._script(store, story_id),
                               next(item for item in tas._board(store, story_id)["shots"]
                                    if item["shot_id"] == shot["shot_id"]))
    kinds = [ref["kind"] for ref in ordered]
    assert kinds == ["keyframe"] + ["sheet"] * (len(kinds) - 3) + ["plate", "prop"], kinds
    assert ordered[-1]["label"] == "Téléphone en noix de coco — object"


def test_the_brief_and_the_shot_zip_say_the_keyframe_check_and_the_cut():
    from clipping.aistory.steps import brief

    data = brief.shot_references_zip(None, [], notes=("The check saw: Gaston's head is a pear.",
                                                       "Flow takes 3 images: the plate was left out, the prompt "
                                                       "describes it.", None))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.read("check.txt").decode("utf-8") == (
            "The check saw: Gaston's head is a pear.\nFlow takes 3 images: the plate was left out, the prompt "
            "describes it.\n")
    with zipfile.ZipFile(io.BytesIO(brief.shot_references_zip(None, []))) as archive:
        assert archive.namelist() == []  # no notes: the files alone, as before
    source = pathlib.Path(brief.__file__).read_text(encoding="utf-8")
    assert 'body += [f"**Keyframe check:** {entry[\'keyframe_check\'][\'line\']}", ""]' in source


# --------------------------------------------------------- 3. the first frame

def test_an_uploaded_clip_s_first_frame_is_checked_against_its_keyframe_and_warned_never_refused(store, tmp_path,
                                                                                                 unpaced):
    import shutil

    import test_story_native_take as tnt
    from clipping.aistory import manual_uploads, workflow

    tnt._require_ffmpeg()
    story_id = tml.manual_story(store)
    shot = tas._board(store, story_id)["shots"][0]
    shot_id = shot["shot_id"]
    # The human's own keyframe (allowed whatever its check), on disk.
    workflow.patch_shot_mode(store, story_id, 1, shot_id, {"image": "manual"}, now=NOW, env=tas._settings())
    tml._keyframe(store, story_id, shot_id)
    answer = json.dumps({"passed": False, "issues": ["the kiwi's head is a human head in image 1"]})
    vision = kg.FakeVision(lambda request: answer)
    received = f"{manual_uploads.clips_folder(store, story_id, 1)}/.upload.part"
    shutil.copyfile(tnt.make_clip(tmp_path / "take.mp4", 8), received)

    out = manual_uploads.accept_clip(store, story_id, 1, shot_id, received, filename="t.mp4",
                                     env=dict(tas._settings(), VISION_CHAIN="gemini/flash-lite"),
                                     adapters={("vision", "gemini"): vision})

    assert len(vision.requests) == 1 and len(vision.requests[0].images) == 2
    assert vision.requests[0].prompt.split("\n\n")[1].startswith(
        f"Image 1 is the first frame of shot {shot_id}'s clip. Image 2 is the keyframe the clip was made from.")
    found = out["clip"]["first_frame"]
    assert found["passed"] is False and found["issues"] == ["the kiwi's head is a human head in image 1"]
    assert found["version"] == 1 and found["link"] == "gemini/flash-lite"
    stored = tas._board(store, story_id)["shots"][0]["assets"]["clip"]
    assert stored["first_frame"] == found  # kept on the shot
    entry = next(item for item in tml._brief(store, story_id, "flow")["shots"] if item["shot_id"] == shot_id)
    assert entry["first_frame"] == ("The first frame does not match its keyframe: the kiwi's head is a human head "
                                    "in image 1. It is your clip: kept, your call.")


# ------------------------------------------------------------------ the card

def test_the_card_says_the_checks_and_holds_the_upload_until_the_keyframe_passed():
    src = pathlib.Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src" / "pages" / "story" / "episode"
    checks = (src / "HandoffChecks.jsx").read_text(encoding="utf-8")
    card = (src / "HandoffCard.jsx").read_text(encoding="utf-8")
    assert "block.keyframe_check" in checks and "check.line" in checks
    assert "block.references_cut" in checks and "block.first_frame" in checks
    assert "keyframe_check.upload_refusal" in checks
    assert "{clip && <HandoffChecks block={block} />}" in card
    assert "{clip && uploadRefusal(block) ? (" in card and "{uploadRefusal(block)}</p>" in card
