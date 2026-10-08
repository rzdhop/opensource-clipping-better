"""The story store: layout, slugs, lock states, takes and the cost ledger. Temp folders only."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from showrunner import store as S  # noqa: E402


@pytest.fixture
def story(tmp_path):
    return S.Story.create(str(tmp_path), "faille-d-amour", title="Faille d'amour", language="fr",
                          universe_name="Fruit people")


def test_slugs_and_ids():
    assert S.slugify("Faille d'amour !") == "faille-d-amour"
    assert S.slugify("  Été à Paris ") == "ete-a-paris"
    with pytest.raises(S.StoreError):
        S.slugify("!!!")
    assert (S.shot_id(3), S.take_id(2), S.episode_dir(1)) == ("s03", "v2", "ep01")


def test_create_writes_the_skeleton_and_open_needs_story_json(tmp_path, story):
    for name in ("00-brief.md", "01-universe.md", "04-season.md", "memory.md", "story.json"):
        assert story.exists(name)
    assert os.path.isdir(story.path("02-cast")) and os.path.isdir(story.path("03-places"))
    assert story.meta["language"] == "fr" and story.slug == "faille-d-amour"
    assert "## Medium" in story.read_text("01-universe.md")
    assert S.Story.open(story.root).language == "fr"
    with pytest.raises(S.StoreError):
        S.Story.open(str(tmp_path))
    with pytest.raises(S.StoreError, match="already exists"):
        S.Story.create(str(tmp_path), "faille-d-amour", title="x", language="fr")
    with pytest.raises(S.StoreError, match="language"):
        S.Story.create(str(tmp_path), "other", title="x", language="de")
    with pytest.raises(S.StoreError, match="slug"):
        S.Story.create(str(tmp_path), "Not A Slug", title="x", language="en")


def test_paths_follow_the_layout_and_never_leave_the_story(story):
    assert story.sheet("paloma") == "02-cast/paloma/sheet.md"
    assert story.clip(1, "s02", "v3") == "ep01/clips/s02_v3.mp4"
    assert story.keyframe(1, "s02") == "ep01/keyframes/s02.png"
    assert (story.shots(1), story.takes(1), story.final(1)) == ("ep01/shots.json", "ep01/takes.json", "ep01/final.mp4")
    with pytest.raises(S.StoreError, match="leaves"):
        story.path("../other/file.md")


def test_a_locked_file_refuses_writes_until_unlocked_with_a_reason(story):
    story.write_text(story.sheet("paloma"), "## Head\nv1")
    story.lock(story.sheet("paloma"), "Rida approved the sheet")
    assert story.is_locked("02-cast/paloma/sheet.md")
    with pytest.raises(S.Locked):
        story.write_text(story.sheet("paloma"), "v2")
    with pytest.raises(S.Locked):
        story.write_bytes(story.sheet("paloma"), b"v2")
    with pytest.raises(S.StoreError, match="reason"):
        story.unlock(story.sheet("paloma"), "  ")
    story.unlock(story.sheet("paloma"), "Rida asked for a new outfit")
    story.write_text(story.sheet("paloma"), "## Head\nv2")
    history = story.read_json("locks.json")["history"]
    assert [h["action"] for h in history] == ["lock", "unlock"] and history[1]["note"].startswith("Rida asked")
    with pytest.raises(S.StoreError, match="no such file"):
        story.lock("02-cast/nobody/sheet.md")


def test_takes_approval_picks_one_and_locks_its_clip(story, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"mp4")
    assert story.next_take(1, "s01") == "v1"
    story.copy_in(str(src), story.clip(1, "s01", "v1"))
    story.copy_in(str(src), story.clip(1, "s01", "v2"))
    assert story.add_take(1, "s01", story.clip(1, "s01", "v1"), seed=22) == "v1"
    assert story.add_take(1, "s01", story.clip(1, "s01", "v2"), seed=33, verdict={"state": "ok"}) == "v2"
    assert story.approved_clip(1, "s01") is None
    story.approve_take(1, "s01", "v1", "Rida")
    story.approve_take(1, "s01", "v2", "Rida, better")
    assert story.approved_clip(1, "s01") == "ep01/clips/s01_v2.mp4"
    assert [t["approved"] for t in story.read_json(story.takes(1))["s01"]["takes"]] == [False, True]
    with pytest.raises(S.Locked):
        story.copy_in(str(src), story.clip(1, "s01", "v2"))
    story.set_verdict(1, "s01", "v1", {"state": "mismatch"})
    assert story.read_json(story.takes(1))["s01"]["takes"][0]["verdict"] == {"state": "mismatch"}
    with pytest.raises(S.StoreError, match="no take"):
        story.approve_take(1, "s01", "v9", "x")
    with pytest.raises(S.StoreError, match="no clip"):
        story.add_take(1, "s02", story.clip(1, "s02", "v1"))


def test_the_ledger_sums_execution_dollars_per_episode(story):
    story.add_cost("clip", "ltx25_i2v_speech", "j1", 40.0, 900.0, 0.0212, episode=1)
    story.add_cost("image", "t2i_flux2_klein", "j2", 4.0, 3.0, 0.0018)
    story.add_cost("clip", "ltx25_i2v_speech", "j3", 120.0, 0.0, 0.0636, episode=2)
    assert story.total_usd() == pytest.approx(0.0866)
    assert story.total_usd(1) == pytest.approx(0.0212)
    assert [r["job"] for r in story.costs(2)] == ["j3"]
    assert story.costs(1)[0]["delay_s"] == 900.0
    lines = open(story.path("costs.jsonl"), encoding="utf-8").read().splitlines()
    assert len(lines) == 3 and json.loads(lines[0])["billed_s"] == 40.0


def test_sections_split_on_level_two_headings(story):
    text = "# Paloma\nintro\n## Head\nA mango woman.\n\n## Voice (en)\nbubbly\n### not a section\nmore\n"
    got = S.sections(text)
    assert got[""] == "# Paloma\nintro"
    assert got["Head"] == "A mango woman."
    assert got["Voice (en)"] == "bubbly\n### not a section\nmore"
    story.write_text("02-cast/paloma/sheet.md", text)
    assert story.cast() == ["paloma"] and story.sections(story.sheet("paloma"))["Head"] == "A mango woman."
