"""Discarding an episode (the human, 2026-10-02: "make a Regen button for
episodes" when a story cannot move to the v2 pipeline because an episode
already has a script).

``StoryStore.discard_episode`` moves ``episodes/ep<NN>/`` -- its script,
storyboard, images, clips and render -- into
``episodes/_discarded/ep<NN>-<UTC stamp>/`` (recoverable by hand, never
deleted), and clears what lives outside the folder and would otherwise speak
for the episode that is gone:

- its series-memory entry and recap in ``season.json`` (a new script starts
  at rev 1, so an old entry written from rev 1 would look current against
  it: ``series_memory.entry_is_stale``), the derived fields folded again;
- its audience feedback items;
- the proposals written from it (``episodes/ep<N+1>/proposals.json``) while
  episode N+1 has no script -- moved into the archive too;
- its rows of the story's cost ledger stay (the money was spent: the story
  total keeps them) but are marked ``discarded``, so they no longer count
  toward the per-episode cap of the episode written in its place.

``list_episodes`` (every episode scan) never lists the archive.

Stdlib + pytest only (DEC-012). Every file lives under ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from clipping.aistory import ledger as ledger_mod
from clipping.aistory import series_memory, store

NOW = "2026-10-02T10:00:00+00:00"
LATER = "2026-10-02T11:00:00+00:00"
STAMP = "20261002T110000Z"

KIWI, MANGO = "char_kiwilo", "char_mangella"


# ---------------------------------------------------------------- builders

def _entry(recap, *, opened=(), closed=(), rev=1):
    return {"recap": recap, "hooks_opened": list(opened), "hooks_closed": list(closed),
            "relationship_deltas": {f"{KIWI}|{MANGO}": "Ils se méfient."}, "script_rev": rev, "at": NOW,
            "approved_at": NOW}


def _season(entries=None, feedback=()):
    season = {
        "$schema": "season_arc_v1", "episodes_planned": 3,
        "arc": [{"ep": ep, "function": function, "summary": f"Épisode {ep} : l'île tremble.",
                 "open_hooks_in": [], "open_hooks_out": [], "characters": [KIWI]}
                for ep, function in ((1, "setup"), (2, "escalation"), (3, "climax_and_reset"))],
        "series_memory": {"recaps": {}, "open_hooks": [], "relationship_state": {}, "introduced": {}},
        "audience_feedback": list(feedback), "approved_at": NOW, "updated_at": NOW,
    }
    for ep, entry in sorted((entries or {}).items()):
        season = series_memory.merge_entry(season, ep, entry)
    return season


def _feedback(ep, text):
    return {"ep": ep, "pasted_at": NOW, "text": text}


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def outputs(tmp_path):
    path = tmp_path / "outputs"
    path.mkdir()
    return path


@pytest.fixture
def logs():
    return []


@pytest.fixture
def stories(outputs, logs):
    return store.StoryStore(str(outputs), on_log=logs.append)


@pytest.fixture
def story_id(stories):
    return stories.create(language="fr", now=NOW)["story_id"]


def _story_dir(outputs, story_id):
    return outputs / "stories" / story_id


def _episodes(outputs, story_id):
    return _story_dir(outputs, story_id) / "episodes"


def _write(path: pathlib.Path, text="{}"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _written_episode(outputs, story_id, ep, *, proposals=False):
    """An episode folder as the steps leave it: a script, a storyboard, a
    shot image, a clip, a render folder and the final video (the store moves
    the folder without reading any of them)."""
    folder = _episodes(outputs, story_id) / f"ep{ep:02d}"
    _write(folder / "script.json", json.dumps({"ep": ep, "rev": 1}))
    _write(folder / "storyboard.json", json.dumps({"ep": ep}))
    _write(folder / "assets" / "shots" / "shot_01.png", "png")
    _write(folder / "assets" / "clips" / "shot_01.mp4", "mp4")
    _write(folder / "render" / "logs" / "ffmpeg.log", "log")
    _write(folder / "episode_final.mp4", "video")
    if proposals:
        _write(folder / "proposals.json", json.dumps({"for_ep": ep}))
    return folder


def _snapshot(folder):
    return {str(p.relative_to(folder)): (None if p.is_dir() else p.read_bytes())
            for p in sorted(folder.rglob("*"))}


def _ledger(outputs, story_id):
    return ledger_mod.CostLedger(str(_story_dir(outputs, story_id) / "cost_ledger.json"))


# ---------------------------------------------------------------- the folder

def test_the_episode_folder_is_moved_whole_into_the_archive(stories, outputs, story_id):
    folder = _written_episode(outputs, story_id, 1)
    before = _snapshot(folder)

    report = stories.discard_episode(story_id, 1, now=LATER)

    archive = _episodes(outputs, story_id) / "_discarded" / f"ep01-{STAMP}"
    assert not folder.exists()
    assert _snapshot(archive) == before  # moved, byte for byte: recoverable by hand
    assert report["ep"] == 1 and report["archive"] == f"ep01-{STAMP}"
    assert report["moved_to"] == f"outputs/stories/{story_id}/episodes/_discarded/ep01-{STAMP}/"
    # Every episode scan ignores the archive.
    assert stories.list_episodes(story_id) == []
    assert stories.read_episode_doc(story_id, 1, "script.json") is None


def test_list_episodes_ignores_the_archive_beside_live_episodes(stories, outputs, story_id, logs):
    _written_episode(outputs, story_id, 1)
    _written_episode(outputs, story_id, 2)
    stories.discard_episode(story_id, 2, now=LATER)
    logs.clear()

    assert stories.list_episodes(story_id) == [1]
    assert logs == []  # the archive is not "skipped and printed": it is not an episode at all


def test_a_second_discard_of_the_same_episode_in_the_same_second_gets_its_own_archive(stories, outputs, story_id):
    _written_episode(outputs, story_id, 1)
    first = stories.discard_episode(story_id, 1, now=LATER)
    _written_episode(outputs, story_id, 1)
    second = stories.discard_episode(story_id, 1, now=LATER)

    assert first["archive"] == f"ep01-{STAMP}" and second["archive"] == f"ep01-{STAMP}-2"
    archived = sorted(os.listdir(_episodes(outputs, story_id) / "_discarded"))
    assert archived == [f"ep01-{STAMP}", f"ep01-{STAMP}-2"]


def test_an_episode_with_no_folder_is_a_key_error_and_nothing_moves(stories, outputs, story_id):
    _written_episode(outputs, story_id, 1)
    with pytest.raises(KeyError):
        stories.discard_episode(story_id, 2, now=LATER)
    with pytest.raises(KeyError):
        stories.discard_episode("0123456789ab", 1, now=LATER)
    assert stories.list_episodes(story_id) == [1]
    assert not (_episodes(outputs, story_id) / "_discarded").exists()


def test_a_symlink_in_the_episodes_place_is_refused_never_followed(stories, outputs, story_id, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    _write(elsewhere / "script.json")
    episodes = _episodes(outputs, story_id)
    episodes.mkdir(parents=True)
    try:
        os.symlink(elsewhere, episodes / "ep01", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a symlink here")

    with pytest.raises(KeyError):
        stories.discard_episode(story_id, 1, now=LATER)
    assert (episodes / "ep01").is_symlink() and (elsewhere / "script.json").exists()


# ---------------------------------------------------------------- season.json

def test_the_memory_entry_recap_and_feedback_of_the_episode_leave_the_season(stories, outputs, story_id):
    _written_episode(outputs, story_id, 1)
    _written_episode(outputs, story_id, 2)
    entries = {1: _entry("Kiwilo ment à Mangella.", opened=["Qui a volé le téléphone ?"]),
               2: _entry("Mangella découvre le mensonge.", opened=["Kiwilo partira-t-il ?"])}
    feedback = [_feedback(1, "On adore Kiwilo."), _feedback(2, "Trop lent."), _feedback(1, "Encore !")]
    stories.write_doc(story_id, "season.json", _season(entries, feedback), now=NOW)

    report = stories.discard_episode(story_id, 2, now=LATER)

    season = stories.read_doc(story_id, "season.json")
    memory = season["series_memory"]
    assert list(memory["entries"]) == ["ep01"]  # ep01 untouched
    # The derived fields are the fold of what is left (the validator holds them to it).
    assert memory["recaps"] == {"ep01": "Kiwilo ment à Mangella."}
    assert memory["open_hooks"] == ["Qui a volé le téléphone ?"]
    assert [item["text"] for item in season["audience_feedback"]] == ["On adore Kiwilo.", "Encore !"]
    assert season["updated_at"] == LATER and season["approved_at"] == NOW  # the arc stays approved
    assert any("series_memory.entries.ep02" in line for line in report["cleared"])
    assert any("1 audience feedback item" in line for line in report["cleared"])


def test_an_old_entry_can_never_look_current_against_the_new_script(stories, outputs, story_id):
    """The reason the entry must go: a new script starts at rev 1, the entry
    was written from rev 1 -- left in place it would read as approved."""
    _written_episode(outputs, story_id, 1)
    stories.write_doc(story_id, "season.json", _season({1: _entry("Un récit ancien.")}), now=NOW)

    stories.discard_episode(story_id, 1, now=LATER)

    season = stories.read_doc(story_id, "season.json")
    assert series_memory.memory_state(season, 1, {"rev": 1}) == "none"
    assert season["series_memory"]["recaps"] == {} and season["series_memory"]["open_hooks"] == []


def test_a_discard_that_would_leave_a_later_entry_closing_its_hook_is_refused_whole(stories, outputs, story_id):
    folder = _written_episode(outputs, story_id, 1)
    _written_episode(outputs, story_id, 2)
    hook = "Qui a volé le téléphone ?"
    entries = {1: _entry("Le téléphone disparaît.", opened=[hook]),
               2: _entry("Kiwilo avoue le vol.", closed=[hook])}
    stories.write_doc(story_id, "season.json", _season(entries), now=NOW)
    season_before = (_story_dir(outputs, story_id) / "season.json").read_bytes()
    before = _snapshot(folder)

    with pytest.raises(ValueError, match="ep02 closes"):
        stories.discard_episode(story_id, 1, now=LATER)

    # Nothing moved, nothing written: discard the later episode first.
    assert _snapshot(folder) == before
    assert (_story_dir(outputs, story_id) / "season.json").read_bytes() == season_before
    assert stories.list_episodes(story_id) == [1, 2]


def test_a_story_without_a_season_discards_its_episode_all_the_same(stories, outputs, story_id):
    _written_episode(outputs, story_id, 1)
    report = stories.discard_episode(story_id, 1, now=LATER)
    assert stories.read_doc(story_id, "season.json") is None
    assert report["proposals_archived"] == [] and report["ledger_rows"] == 0


# ---------------------------------------------------------------- proposals

def test_the_proposals_written_from_the_episode_go_into_its_archive(stories, outputs, story_id):
    _written_episode(outputs, story_id, 1)
    next_folder = _episodes(outputs, story_id) / "ep02"
    _write(next_folder / "proposals.json", json.dumps({"for_ep": 2}))

    report = stories.discard_episode(story_id, 1, now=LATER)

    archive = _episodes(outputs, story_id) / "_discarded" / f"ep01-{STAMP}"
    assert json.loads((archive / "proposals_for_ep02.json").read_text(encoding="utf-8")) == {"for_ep": 2}
    assert report["proposals_archived"] == [2]
    # Episode 2's folder held nothing else: it goes, so it is no longer listed.
    assert not next_folder.exists() and stories.list_episodes(story_id) == []


def test_the_proposals_of_a_written_next_episode_stay(stories, outputs, story_id):
    _written_episode(outputs, story_id, 1)
    _written_episode(outputs, story_id, 2, proposals=True)

    report = stories.discard_episode(story_id, 1, now=LATER)

    assert (_episodes(outputs, story_id) / "ep02" / "proposals.json").exists()
    assert report["proposals_archived"] == []


def test_the_episodes_own_proposals_written_from_the_one_before_stay_in_place(stories, outputs, story_id):
    """Episode 2's proposals were written from episode 1, which stays: they
    are not episode 2's work, so they wait in a fresh ep02/ for the episode
    written in its place."""
    _written_episode(outputs, story_id, 1)
    _written_episode(outputs, story_id, 2, proposals=True)

    stories.discard_episode(story_id, 2, now=LATER)

    folder = _episodes(outputs, story_id) / "ep02"
    assert sorted(os.listdir(folder)) == ["proposals.json"]
    archive = _episodes(outputs, story_id) / "_discarded" / f"ep02-{STAMP}"
    assert "proposals.json" not in os.listdir(archive) and (archive / "script.json").exists()
    assert stories.list_episodes(story_id) == [1, 2]


def test_discarding_latest_first_archives_every_proposal_of_the_discarded_run(stories, outputs, story_id):
    """What the pipeline switch does (``workflow.switch_pipeline``): every
    written episode, the latest first."""
    _written_episode(outputs, story_id, 1)
    _written_episode(outputs, story_id, 2, proposals=True)
    _write(_episodes(outputs, story_id) / "ep03" / "proposals.json", json.dumps({"for_ep": 3}))

    archived = [stories.discard_episode(story_id, ep, now=LATER)["proposals_archived"] for ep in (2, 1)]

    assert archived == [[3], [2]]
    assert sorted(os.listdir(_episodes(outputs, story_id))) == ["_discarded"]
    assert stories.list_episodes(story_id) == []


# ---------------------------------------------------------------- the ledger

def test_the_episodes_ledger_rows_are_marked_and_still_count_for_the_story(stories, outputs, story_id):
    _written_episode(outputs, story_id, 1)
    ledger = _ledger(outputs, story_id)
    ledger.append(step="cast", provider="fal", model="m", unit="image", qty=3, est_usd=0.09, paid=True)
    ledger.append(step="assets", provider="fal", model="m", unit="image", qty=10, est_usd=0.30, paid=True, ep=1)
    ledger.append(step="clips", provider="fal", model="v", unit="second", qty=5, est_usd=0.25, paid=True, ep=1)
    ledger.append(step="assets", provider="fal", model="m", unit="image", qty=2, est_usd=0.06, paid=True, ep=2)

    report = stories.discard_episode(story_id, 1, now=LATER)

    assert report["ledger_rows"] == 2
    marked = [row for row in ledger.entries() if row.get("discarded")]
    assert [row["discarded"] for row in marked] == [f"ep01-{STAMP}"] * 2
    # Really spent: the story total keeps it; the new episode 1 starts at $0.
    assert ledger.totals() == {"est_usd": 0.70, "paid_usd": 0.70, "entries": 4}
    assert ledger.totals(ep=1) == {"est_usd": 0.0, "paid_usd": 0.0, "entries": 0}
    assert ledger.totals(ep=2)["est_usd"] == 0.06
    # What the episode page and the story page show of it.
    from clipping.aistory import workflow

    assert workflow.episode_ledger(stories, story_id, 1) == {
        "entries": [], "totals": {"est_usd": 0.0, "paid_usd": 0.0, "entries": 0}}
    assert workflow.cost_total(stories, story_id) == 0.70
