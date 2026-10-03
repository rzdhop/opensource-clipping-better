"""``python main.py --ai-story voice-tails STORY_ID --ep N``: what the Gemini
tail guard would cut, or cut, at the end of each line of an episode --
printed, nothing changed (``voice_lines.tail_report``).

Driven through ``clipping.aistory.cli.main(argv)`` against the episode of
``tests/test_story_voice_tails.py`` (Gemini lines voiced before the guard,
the others by Edge), with ``tests/test_story_cli_phase4.py``'s ``cli``
fixture and stage 8's ``hermetic`` one. Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

from pathlib import Path

import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_measure as tsm
import test_story_voice_tails as tvt
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures
from test_story_cli_phase4 import cli  # noqa: F401 -- the CLI against the store's outputs


def _episode_bytes(store, story_id) -> dict:
    folder = Path(store.episode_dir(story_id, 1))
    return {str(path.relative_to(folder)): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


def _rows(out, line_ids):
    return {line_id: next(row for row in out.splitlines() if row.startswith(f"{line_id} ")) for line_id in line_ids}


def test_voice_tails_says_what_the_guard_would_cut_and_changes_nothing(cli, tmp_path):
    story_id, theirs = tvt._voiced_before_the_guard(cli.store, tmp_path)
    others = [line["line_id"] for line in tas._lines(eps._script(cli.store, story_id)) if line["line_id"] not in theirs]
    before = _episode_bytes(cli.store, story_id)
    cli.capsys.readouterr()

    code = cli.run("voice-tails", story_id, "--ep", "1")

    out = cli.capsys.readouterr().out
    assert code == 0
    assert _episode_bytes(cli.store, story_id) == before, "nothing is changed"
    rows = _rows(out, theirs + others)
    for line_id in theirs:
        row = rows[line_id]
        assert "Mangella" in row and "gemini/Kore" in row and "1.50 s" in row
        assert "not cleaned yet" in row and "would cut 0.4" in row and "(noise_after_gap)" in row
    for line_id in others:
        assert "edge/" in rows[line_id] and "not checked" in rows[line_id]
    assert f"5 Gemini lines: 0 cleaned, 5 to clean" in out
    assert "the next assets run cleans them" in out


def test_voice_tails_after_the_assets_run_reports_what_was_cut(cli, tmp_path):
    story_id, theirs = tvt._voiced_before_the_guard(cli.store, tmp_path)
    tas._run(cli.store, story_id, adapters=tas._adapters(gemini=tsm.NeverCalled()))
    cli.capsys.readouterr()

    code = cli.run("voice-tails", story_id, "--ep", "1")

    out = cli.capsys.readouterr().out
    assert code == 0
    for line_id, row in _rows(out, theirs).items():
        assert "cleaned (v2): 0.4" in row and "s cut (noise_after_gap)" in row and "nothing more to cut" in row
    assert "5 Gemini lines: 5 cleaned, 0 to clean" in out


def test_voice_tails_needs_an_episode_with_a_script(cli, tmp_path):
    story_id = tas._episode(cli.store, tmp_path)
    cli.capsys.readouterr()

    assert cli.run("voice-tails", story_id) == 2, "--ep is required"
    assert cli.run("voice-tails", "nosuchstory0", "--ep", "1") == 1
    assert cli.run("voice-tails", story_id, "--ep", "2") == 1
    err = cli.capsys.readouterr().err
    assert "episode 2 has no script yet" in err.lower()


def test_voice_tails_is_in_the_help():
    from clipping.aistory import cli as cli_module

    assert "voice-tails" in cli_module.__doc__
    parser = cli_module.build_parser()
    assert "voice-tails" in parser.format_help()
