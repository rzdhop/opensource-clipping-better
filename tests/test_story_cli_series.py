"""``python main.py --ai-story step <id> memory|feedback|propose-next --ep N``
and the ``feedback`` top-level command (AI Story phase 5, step 13, stage 5;
spec 9.3; DEC-114: one set of rules, two front ends; DEC-009).

Driven through ``clipping.aistory.cli.main(argv)`` against a story store
under ``tmp_path`` (the hidden ``--outputs-dir``), exactly as
``tests/test_story_cli_phase4.py`` drives phase 4's steps. The CLI's own
``_run_step`` calls a step's ``run(ctx)`` with nothing else, so a fake LLM is
bound by monkeypatching the step module's own ``run`` (``_patch_run``,
duplicated from the phase-4 CLI test so this file stays independently
readable). The fixture stories are stage 4's own step modules, run directly
against ``cli.store`` (``tests/test_story_series_steps.py``), never through
the CLI, to reach "episode 1 approved", "memory approved" and "proposed"
states before the command under test runs. Offline and hermetic: stage 8's
own ``hermetic`` fixture (imported below).

The new CLI surface is imported inside the tests, so on the parent commit
(``86de38d``) each test fails on its own (``memory``/``feedback``/
``propose-next`` are not in ``STEPS`` there, and the ``feedback`` command
does not exist).

Stdlib + pytest only: this file runs in the CI environment (DEC-012).
"""

from __future__ import annotations

import argparse
import importlib
from types import SimpleNamespace

import pytest

import test_story_episode_steps as eps
import test_story_series_steps as tss
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used here as they are

NOW = eps.NOW


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def cli(monkeypatch, store, capsys):
    """``cli.run(*argv)`` -> exit code, against *store*'s own outputs
    directory. ``hermetic`` (imported above, autouse) isolates the whole
    process; this also clears the LLM key gate's own env vars, as
    ``test_story_cli_episode.py``'s ``cli`` fixture does."""
    for name in ("LLM_CHAIN", "ALLOW_SLOW_CHAIN", "ALLOW_PAID"):
        monkeypatch.delenv(name, raising=False)

    def run(*argv):
        module = importlib.import_module("clipping.aistory.cli")
        return module.main([*argv, "--outputs-dir", str(store.outputs_dir)])

    return SimpleNamespace(run=run, store=store, monkeypatch=monkeypatch, capsys=capsys)


def _keyed(cli):
    """A one-link LLM chain with a (test) key: the key gate passes."""
    cli.monkeypatch.setenv("LLM_CHAIN", "gemini/gemini-test")
    cli.monkeypatch.setenv("GOOGLE_API_KEY", "test-gemini-key")


def _patch_run(cli, module, **fixed_kwargs):
    """Bind *fixed_kwargs* to *module*.run (see the module docstring)."""
    real = module.run

    def wrapped(ctx, **_ignored):
        return real(ctx, **fixed_kwargs)

    cli.monkeypatch.setattr(module, "run", wrapped)


def _memory_module():
    from clipping.aistory.steps import memory
    return memory


def _feedback_module():
    from clipping.aistory.steps import feedback
    return feedback


def _propose_next_module():
    from clipping.aistory.steps import propose_next
    return propose_next


def _patch_memory(cli, reply=None):
    _patch_run(cli, _memory_module(), runner=eps.FakeLLM(S3=[reply or tss.S3_REPLY]), time_fn=eps.Clock(100.0))


def _patch_feedback(cli, reply=None):
    _patch_run(cli, _feedback_module(), runner=eps.FakeLLM(F1=[reply or tss.F1_REPLY]), time_fn=eps.Clock(100.0))


def _patch_propose_next(cli, reply=None):
    _patch_run(cli, _propose_next_module(), runner=eps.FakeLLM(N1=[reply or tss.N1_REPLY]), time_fn=eps.Clock(100.0))


def _written_ep1(cli, **kwargs):
    return tss._written_ep1(cli.store, **kwargs)


def _approved_memory_story(cli):
    from clipping.aistory.steps import episode_common, script, storyboard

    m = SimpleNamespace(memory=_memory_module(), feedback=_feedback_module(),
                       propose_next=_propose_next_module(), script=script, storyboard=storyboard,
                       common=episode_common)
    return tss._approved_memory_story(m, cli.store)


def _season(cli, story_id):
    return cli.store.read_doc(story_id, "season.json")


# ----------------------------------------------------- the parser's own lists

def _subparser(parser, name):
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return action.choices[name]


def _option(parser, flag):
    return next(a for a in parser._actions if flag in a.option_strings)


def _positional(parser, dest):
    return next(a for a in parser._actions if a.dest == dest and not a.option_strings)


def test_the_series_steps_are_in_the_parsers_own_lists():
    from clipping.aistory import cli as cli_module, workflow

    assert set(workflow.SERIES_STEPS) <= set(cli_module.STEPS)
    assert {"memory", "feedback"} <= set(cli_module.AUTO_APPROVABLE)
    assert "propose-next" not in cli_module.AUTO_APPROVABLE
    assert "propose-next" in cli_module._NOT_AUTO_APPROVABLE
    assert "human decision" in cli_module._NOT_AUTO_APPROVABLE["propose-next"]
    for step in workflow.SERIES_STEPS:
        assert step in cli_module._KEYED_STEPS

    parser = cli_module.build_parser()
    step = _subparser(parser, "step")
    assert {"memory", "feedback", "propose-next"} <= set(_positional(step, "step").choices)
    assert _subparser(parser, "feedback") is not None  # the top-level 'feedback' command exists


def test_ep_missing_for_a_series_step_is_a_usage_error():
    from clipping.aistory import cli as cli_module

    for step in ("memory", "feedback", "propose-next"):
        code = cli_module.main(["step", "000000000000", step, "--outputs-dir", "/tmp/does-not-matter"])
        assert code == 2


def test_auto_approve_on_propose_next_is_a_usage_error(cli):
    code = cli.run("step", "000000000000", "propose-next", "--ep", "1", "--auto-approve")

    assert code == 2
    err = cli.capsys.readouterr().err
    assert "--auto-approve does not apply to 'propose-next'" in err and "human decision" in err


def test_feedback_command_requires_ep_and_text_file():
    from clipping.aistory import cli as cli_module

    parser = cli_module.build_parser()
    feedback_cmd = _subparser(parser, "feedback")
    assert _option(feedback_cmd, "--ep").required
    assert _option(feedback_cmd, "--text-file").required
    assert not _option(feedback_cmd, "--stats-file").required


# ------------------------------------------------------------------- memory

def test_memory_runs_through_the_cli_and_writes_the_entry(cli):
    story_id = _written_ep1(cli)
    _keyed(cli)
    _patch_memory(cli)

    code = cli.run("step", story_id, "memory", "--ep", "1")

    assert code == 0, cli.capsys.readouterr().err
    out = cli.capsys.readouterr().out
    assert any(line.startswith("🧠 Episode 1's memory") for line in out.splitlines())
    entry = _season(cli, story_id)["series_memory"]["entries"]["ep01"]
    assert entry["recap"] and entry["approved_at"] is None


def test_memory_needs_an_approved_script(cli):
    story_id = eps._ready_story(cli.store)
    _keyed(cli)
    _patch_memory(cli)

    code = cli.run("step", story_id, "memory", "--ep", "1")

    assert code == 1
    err = cli.capsys.readouterr().err
    assert "Episode 1 has no script yet" in err


def test_auto_approve_on_memory_approves_the_entry(cli):
    story_id = _written_ep1(cli)
    _keyed(cli)
    _patch_memory(cli)

    code = cli.run("step", story_id, "memory", "--ep", "1", "--auto-approve")

    assert code == 0, cli.capsys.readouterr().err
    out = cli.capsys.readouterr().out
    assert "✅ Episode 1's memory approved." in out
    entry = _season(cli, story_id)["series_memory"]["entries"]["ep01"]
    assert entry["approved_at"] is not None


# ----------------------------------------------------------------- feedback

def test_feedback_step_digests_an_already_pasted_item(cli):
    from clipping.aistory import workflow

    story_id = eps._ready_story(cli.store)
    workflow.store_feedback(cli.store, story_id, 1, tss.FEEDBACK, now=NOW)
    _keyed(cli)
    _patch_feedback(cli)

    code = cli.run("step", story_id, "feedback", "--ep", "1")

    assert code == 0, cli.capsys.readouterr().err
    out = cli.capsys.readouterr().out
    assert any(line.startswith("💬 Episode 1's feedback digested: 3 direction") for line in out.splitlines())
    item = _season(cli, story_id)["audience_feedback"][0]
    assert item["digest"] and len(item["directions"]) == 3


def test_feedback_step_without_a_paste_is_refused(cli):
    story_id = eps._ready_story(cli.store)
    _keyed(cli)
    _patch_feedback(cli)

    code = cli.run("step", story_id, "feedback", "--ep", "1")

    assert code == 1
    assert "Episode 1 has no audience feedback yet: paste it first." in cli.capsys.readouterr().err


def test_auto_approve_on_feedback_chooses_no_direction(cli):
    from clipping.aistory import workflow

    story_id = eps._ready_story(cli.store)
    workflow.store_feedback(cli.store, story_id, 1, tss.FEEDBACK, now=NOW)
    _keyed(cli)
    _patch_feedback(cli)

    code = cli.run("step", story_id, "feedback", "--ep", "1", "--auto-approve")

    assert code == 0, cli.capsys.readouterr().err
    out = cli.capsys.readouterr().out
    assert "✅ Episode 1's feedback approved (no direction chosen)." in out
    item = _season(cli, story_id)["audience_feedback"][0]
    assert item["chosen_direction"] is None


# -------------------------------------------------------------- propose-next

def test_propose_next_runs_through_the_cli_and_writes_the_proposals(cli):
    story_id = _approved_memory_story(cli)
    _keyed(cli)
    _patch_propose_next(cli)

    code = cli.run("step", story_id, "propose-next", "--ep", "1")

    assert code == 0, cli.capsys.readouterr().err
    out = cli.capsys.readouterr().out
    assert any(line.startswith("💡 Episode 2's proposals") and "2 character(s), 1 twist(s)" in line
              for line in out.splitlines())
    doc = cli.store.read_episode_doc(story_id, 2, "proposals.json")
    assert doc["for_ep"] == 2 and len(doc["characters"]) == 2 and len(doc["twists"]) == 1


def test_propose_next_needs_a_fresh_approved_memory(cli):
    story_id = _written_ep1(cli)
    _keyed(cli)
    _patch_propose_next(cli)

    code = cli.run("step", story_id, "propose-next", "--ep", "1")

    assert code == 1
    assert "Episode 1's series memory is not written yet" in cli.capsys.readouterr().err


# -------------------------------------------------------- the feedback command

def test_feedback_command_pastes_and_digests_from_files(cli, tmp_path):
    story_id = eps._ready_story(cli.store)
    text_file = tmp_path / "comments.txt"
    text_file.write_text(tss.FEEDBACK, encoding="utf-8")
    stats_file = tmp_path / "stats.txt"
    stats_file.write_text(tss.STATS, encoding="utf-8")
    _keyed(cli)
    _patch_feedback(cli)

    code = cli.run("feedback", story_id, "--ep", "1", "--text-file", str(text_file), "--stats-file", str(stats_file))

    assert code == 0, cli.capsys.readouterr().err
    out = cli.capsys.readouterr().out
    assert any(line.startswith("💬 Episode 1's feedback digested: 3 direction") for line in out.splitlines())
    item = _season(cli, story_id)["audience_feedback"][0]
    assert item["text"] == tss.FEEDBACK and item["stats"] == tss.STATS
    assert item["digest"]


def test_feedback_command_without_stats_file(cli, tmp_path):
    story_id = eps._ready_story(cli.store)
    text_file = tmp_path / "comments.txt"
    text_file.write_text(tss.FEEDBACK, encoding="utf-8")
    _keyed(cli)
    _patch_feedback(cli)

    code = cli.run("feedback", story_id, "--ep", "1", "--text-file", str(text_file))

    assert code == 0, cli.capsys.readouterr().err
    item = _season(cli, story_id)["audience_feedback"][0]
    assert item["text"] == tss.FEEDBACK and "stats" not in item


def test_feedback_command_auto_approves_with_no_direction(cli, tmp_path):
    story_id = eps._ready_story(cli.store)
    text_file = tmp_path / "comments.txt"
    text_file.write_text(tss.FEEDBACK, encoding="utf-8")
    _keyed(cli)
    _patch_feedback(cli)

    code = cli.run("feedback", story_id, "--ep", "1", "--text-file", str(text_file), "--auto-approve")

    assert code == 0, cli.capsys.readouterr().err
    assert "✅ Episode 1's feedback approved (no direction chosen)." in cli.capsys.readouterr().out
    assert _season(cli, story_id)["audience_feedback"][0]["chosen_direction"] is None


def test_feedback_command_refuses_a_missing_file_before_anything_is_pasted(cli, tmp_path):
    story_id = eps._ready_story(cli.store)
    missing = tmp_path / "nope.txt"

    code = cli.run("feedback", story_id, "--ep", "1", "--text-file", str(missing))

    assert code == 1
    err = cli.capsys.readouterr().err
    assert "Cannot read" in err and str(missing) in err
    assert _season(cli, story_id)["audience_feedback"] == []


def test_feedback_command_refuses_text_over_the_cap_naming_the_size(cli, tmp_path):
    story_id = eps._ready_story(cli.store)
    text_file = tmp_path / "comments.txt"
    text_file.write_text("x" * 6001, encoding="utf-8")

    code = cli.run("feedback", story_id, "--ep", "1", "--text-file", str(text_file))

    assert code == 1
    err = cli.capsys.readouterr().err
    assert "6001" in err and "6000" in err and "never shortened" in err
    assert _season(cli, story_id)["audience_feedback"] == []


def test_feedback_command_accepts_exactly_the_cap(cli, tmp_path):
    story_id = eps._ready_story(cli.store)
    text_file = tmp_path / "comments.txt"
    text_file.write_text("x" * 6000, encoding="utf-8")
    _keyed(cli)
    _patch_feedback(cli)

    code = cli.run("feedback", story_id, "--ep", "1", "--text-file", str(text_file))

    assert code == 0, cli.capsys.readouterr().err
    assert len(_season(cli, story_id)["audience_feedback"][0]["text"]) == 6000


def test_feedback_command_bounds_the_episode_before_reading_any_file(cli, tmp_path):
    story_id = eps._ready_story(cli.store)
    missing = tmp_path / "nope.txt"  # never opened: the episode bound fails first

    code = cli.run("feedback", story_id, "--ep", "9", "--text-file", str(missing))

    assert code == 1
    assert "there is no episode 9" in cli.capsys.readouterr().err
    assert not missing.exists()
