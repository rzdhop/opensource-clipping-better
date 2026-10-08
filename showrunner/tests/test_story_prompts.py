"""The prompts of a story's GPU jobs, built from the story's own files (story_prompts): the batch-a golden
holds from the demo story's files alone, the shot rules, the cast images, the spec of comfy_submit."""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from showrunner import prompts as P, store, story_prompts as SP  # noqa: E402

DEMO = os.path.join(ROOT, "stories", "faille-d-amour")
OLD_CLOSING = "No subtitles, no on-screen text, no black frames."


@pytest.mark.skipif(not os.path.exists(DEMO), reason="the demo story")
def test_the_demo_clips_rebuild_from_the_story_files_as_sent_except_clean_frame():
    """The five batch-a prompts Rida preferred, kept as a record in shots.json, come back word for word from
    01-universe.md, the sheets, the plates and the lines: nothing of them lives in a chat or in code."""
    s = store.Story.open(DEMO)
    shots = s.read_json(s.shots(1))["shots"]
    assert shots and all("clip_prompt" not in sh for sh in shots)        # the record is never taken as a prompt
    for sh in shots:
        sent = sh["clip_prompt_as_sent"]
        assert sent.endswith(OLD_CLOSING)
        built = SP.clip(s, 1, sh["id"])
        assert built["prompt"] == sent[: -len(OLD_CLOSING)] + P.CLEAN_FRAME, sh["id"]
        assert built["budget"]["fits"] and built["template"] == "ltx25_i2v_speech"


def _story(tmp_path):
    s = store.Story.create(str(tmp_path), "demo", title="Demo", language="fr", universe_name="Cartoon")
    s.write_text("01-universe.md", "# Universe — Cartoon\n\n## Medium\nA 3D cartoon, fully\ncomputer-generated.\n\n"
                                   "## Negative\nx\n")
    for cid, name in (("ana", "Ana"), ("bo", "Bo"), ("cy", "Cy")):
        s.write_text(s.sheet(cid), P.render("character_sheet", oneline=False, name=name,
                                            head=f"{name}, a cartoon woman with a red scarf.", voice_en=f"{name}'s warm voice",
                                            voice_fr="voix", colour="#fff"))
    s.write_text(s.plate("cafe"), "# Café\n\n## Setting\na sunny café counter\n")
    s.write_json(s.shots(1), {"shots": [
        {"id": "s01", "seconds": 5, "place": "cafe", "characters": ["ana"],
         "lines": [{"speaker": "ana", "text": "C'est toi ?"}]},
        {"id": "s02", "seconds": 10, "place": "cafe", "characters": ["ana", "bo"],
         "lines": [{"speaker": "ana", "text": "Un."}, {"speaker": "bo", "text": "Deux."}]},
        {"id": "s03", "seconds": 5, "place": "cafe", "characters": ["bo"], "reaction": "a flash of doubt",
         "framing": "three_quarter", "expression": "eyes wide"},
        {"id": "s04", "seconds": 7, "place": "cafe", "characters": ["ana"], "lines": [{"speaker": "ana", "text": "x"}]},
        {"id": "s05", "seconds": 5, "place": "cafe", "characters": ["ana"],
         "lines": [{"speaker": "ana", "text": " ".join(["mot"] * 11)}]},
        {"id": "s06", "seconds": 5, "place": "nowhere", "characters": ["ana"], "lines": [{"speaker": "ana", "text": "x"}]},
        {"id": "s07", "seconds": 5, "place": "cafe", "characters": ["ana", "bo"]},
    ]})
    return s


def test_one_speaker_two_speakers_and_a_silent_shot(tmp_path):
    s = _story(tmp_path)
    cast = {c: SP.member(s, c) for c in ("ana", "bo")}
    one = SP.clip(s, 1, "s01")
    assert one["prompt"] == P.dialogue_clip("A 3D cartoon, fully computer-generated.", cast, "a sunny café counter",
                                            [("ana", "C'est toi ?")], language="fr")
    assert one["files"] == {"image": "ep01/keyframes/s01.png"} and one["keyframe_ready"] is False
    two = SP.clip(s, 1, "s02")
    assert "They speak in turn" in two["prompt"] and two["seconds"] == 10
    silent = SP.clip(s, 1, "s03")
    assert "reacts in silence" in silent["prompt"] and "a flash of doubt" in silent["prompt"]
    assert silent["budget"] == {"words": 0, "max_words": 10, "fits": True}


def test_the_shot_rules_name_what_is_wrong(tmp_path):
    s = _story(tmp_path)
    with pytest.raises(SP.StoryPromptError, match="5 or 10"):
        SP.clip(s, 1, "s04")
    assert SP.clip(s, 1, "s05")["budget"] == {"words": 11, "max_words": 10, "fits": False}
    with pytest.raises(SP.StoryPromptError, match="03-places/nowhere"):
        SP.clip(s, 1, "s06")
    with pytest.raises(SP.StoryPromptError, match="one listener"):
        SP.clip(s, 1, "s07")
    with pytest.raises(SP.StoryPromptError, match="no shot s99"):
        SP.clip(s, 1, "s99")
    s.write_text("01-universe.md", "# Universe\n\n## Medium\n\n## Negative\n")
    with pytest.raises(SP.StoryPromptError, match="Medium"):
        SP.clip(s, 1, "s01")


def test_the_speech_budget():
    assert SP.max_words(5) == 10 and SP.max_words(10) == 22


def test_keyframes_t2i_until_every_body_is_locked_then_the_edit(tmp_path):
    s = _story(tmp_path)
    kf = SP.keyframe(s, 1, "s01")
    assert kf["template"] == "t2i_flux2_klein" and kf["files"] == {} and (kf["width"], kf["height"]) == (704, 1280)
    assert "Ana faces the camera" in kf["prompt"] and "only this one character" in kf["prompt"]
    s.write_bytes("02-cast/ana/full_body.png", b"png")
    assert SP.keyframe(s, 1, "s01")["template"] == "t2i_flux2_klein"             # a draft body is not a reference
    s.lock("02-cast/ana/full_body.png", "Rida: this one")
    kf = SP.keyframe(s, 1, "s01")
    assert kf["template"] == "edit_flux2_klein_multiref"
    assert set(kf["files"].values()) == {"02-cast/ana/full_body.png"} and len(kf["files"]) == 4
    three = SP.keyframe(s, 1, "s03")
    assert "head turned three-quarters" in three["prompt"] and "eyes wide" in three["prompt"]
    assert P.framing_intruders(three["prompt"]) == []
    assert "stand close together" in SP.keyframe(s, 1, "s02")["prompt"]


def test_cast_images_start_from_the_locked_full_body(tmp_path):
    s = _story(tmp_path)
    fb = SP.cast(s, "ana", "full_body")
    assert fb["template"] == "t2i_flux2_klein" and (fb["width"], fb["height"]) == (832, 1216)
    assert fb["prompt"].startswith("A 3D cartoon, fully computer-generated. Ana, a cartoon woman with a red scarf. A full-body")
    with pytest.raises(SP.StoryPromptError, match="locked"):
        SP.cast(s, "ana", "turnaround")
    s.write_bytes("02-cast/ana/full_body.png", b"png")
    s.lock("02-cast/ana/full_body.png", "Rida: this one")
    tr = SP.cast(s, "ana", "turnaround")
    assert tr["template"] == "edit_flux2_klein_multiref" and tr["files"]["ref1"] == "02-cast/ana/full_body.png"
    assert "turnaround" in tr["prompt"] and "six" in SP.cast(s, "ana", "emotions")["prompt"]
    with pytest.raises(SP.StoryPromptError, match="kind"):
        SP.cast(s, "ana", "portrait")
    with pytest.raises(SP.StoryPromptError, match="no character sheet"):
        SP.cast(s, "ghost", "full_body")


def test_specs_and_the_record_in_shots_json(tmp_path):
    s = _story(tmp_path)
    assert SP.from_spec(s, "clip:1:s01")["prompt"] == SP.clip(s, 1, "s01")["prompt"]
    assert SP.from_spec(s, "cast:ana:full_body")["image"] == "full_body"
    for bad in ("clip:1", "clip:one:s01", "video:1:s01", ""):
        with pytest.raises(SP.StoryPromptError):
            SP.from_spec(s, bad)
    built = SP.clip(s, 1, "s01")
    assert SP.record(s, built) is True
    entry = [e for e in s.read_json(s.shots(1))["shots"] if e["id"] == "s01"][0]
    assert entry["clip_prompt"] == built["prompt"]
    s.lock(s.shots(1), "Rida: the shot list")
    assert SP.record(s, SP.keyframe(s, 1, "s01")) is False                     # a locked list is left alone
    assert SP.record(s, SP.cast(s, "ana", "full_body")) is False
    json.dumps(built)                                                             # everything is plain JSON
