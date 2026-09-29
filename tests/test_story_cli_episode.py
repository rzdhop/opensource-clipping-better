"""``python main.py --ai-story step <id> script|storyboard --ep N`` (AI Story
phase 3, stage 9; spec 9.3, DEC-114: one set of rules, two front ends).

``clipping/aistory/cli.py`` is driven through its ``main(argv)`` against a
story store under ``tmp_path`` (the hidden ``--outputs-dir``; never the
repository's ``outputs/``), exactly as ``tests/test_story_cli.py`` drives
phases 1 and 2: an LLM step runs in-process through the worker's registry
with ``llm.run_chain`` replaced by a fake that answers from a queue -- no
network, and the keys set here are test values that never leave the
process. The ``ready`` story fixture and the fake LLM builders are the
stage-6 ones (``tests/test_story_episode_steps.py``, reused as stage 7 did),
imported as a plain module so the two files share nothing but that import.

Stdlib + pytest only: this file runs in the CI environment (DEC-012). The new
CLI surface is imported inside the tests, so against the parent commit every
test fails on its own rather than the whole file failing to collect.
"""

from __future__ import annotations

import importlib
import json
import os
import pathlib
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

import test_story_episode_steps as eps

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def cli(monkeypatch, tmp_path, capsys):
    """``cli.run(*argv)`` -> exit code, against a story store under
    ``tmp_path``; no key, chain or slow-chain switch from the machine
    running the tests (the same harness ``test_story_cli.py`` uses)."""
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in ("LLM_CHAIN", "ALLOW_SLOW_CHAIN", "ALLOW_PAID"):
        monkeypatch.delenv(name, raising=False)
    outputs = tmp_path / "outputs"

    def run(*argv):
        module = importlib.import_module("clipping.aistory.cli")
        return module.main([*argv, "--outputs-dir", str(outputs)])

    def episode_doc(story_id, ep, name):
        path = outputs / "stories" / story_id / "episodes" / f"ep{ep:02d}" / name
        return json.loads(path.read_text(encoding="utf-8"))

    return SimpleNamespace(run=run, outputs=outputs, episode_doc=episode_doc,
                           monkeypatch=monkeypatch, capsys=capsys)


def _store(cli):
    from clipping.aistory.store import StoryStore

    return StoryStore(cli.outputs, on_log=lambda line: None)


def _ready(cli, **kwargs):
    """A ``ready`` story (the stage-6 fixture) under the CLI's own outputs
    directory: a later ``cli.run(...)`` sees it, ``StoryStore`` being a
    folder on disk, not an in-process cache."""
    return eps._ready_story(_store(cli), **kwargs)


def _written(cli, *, e4=None):
    """:func:`_ready`, with episode 1's script already fully written (a
    complete story for the storyboard tests), through the stage-6 runner
    directly -- not through the CLI, so a storyboard test does not depend on
    the script step's own CLI wiring."""
    store = _store(cli)
    story_id = eps._ready_story(store)
    m = eps._new()
    llm = eps._script_llm(E4=[e4]) if e4 is not None else eps._script_llm()
    eps._run(m.script, store, story_id, llm=llm)
    return story_id


def _keyed(cli):
    """A one-link chain with a (test) key: the key gate passes."""
    cli.monkeypatch.setenv("LLM_CHAIN", "gemini/gemini-test")
    cli.monkeypatch.setenv("GOOGLE_API_KEY", "test-gemini-key")


def _fake_llm(cli, llm_obj):
    from clipping.providers import llm

    cli.monkeypatch.setattr(llm, "run_chain", llm_obj)
    return llm_obj


def _no_llm_call(cli):
    """Replaces ``llm.run_chain`` with something that fails the test the
    moment it is called: proves a run made none."""
    def boom(*_args, **_kwargs):
        raise AssertionError("no LLM call was expected")

    return _fake_llm(cli, boom)


def _main_py(*argv, tmp_path):
    return subprocess.run(
        [sys.executable, str(MAIN), *argv], cwd=tmp_path, capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")]))},
    )


# ------------------------------------------------------------------ script

def test_a_script_run_is_one_e1_e2_per_body_scene_e3_and_e4_with_a_summary(cli):
    story_id = _ready(cli)
    _keyed(cli)
    fake = _fake_llm(cli, eps._script_llm())  # E4 -> two issues, the default

    code = cli.run("step", story_id, "script", "--ep", "1")

    assert code == 0
    assert fake.prompts() == ["E1"] + ["E2"] * len(eps.BODY) + ["E3", "E4"]
    out = cli.capsys.readouterr().out
    lines = out.splitlines()
    assert f"📄 Episode 1: {len(eps.ALL_SCENES)} scenes written, report issues." in lines
    assert any(line.startswith("⏱ ") and "estimated" in line for line in lines)
    assert lines[-1].startswith(story_id)  # the story line, still, at the end (RC-E2)
    script = cli.episode_doc(story_id, 1, "script.json")
    assert script["approved_at"] is None and len(script["scenes"]) == len(eps.ALL_SCENES)


def test_ep_missing_for_script_is_a_usage_error(cli):
    code = cli.run("step", "000000000000", "script")

    assert code == 2
    err = cli.capsys.readouterr().err
    # Not a loose "--ep" substring check: --episodes already contains "--ep".
    assert "--ep is required for 'script'" in err
    assert not cli.outputs.exists()  # refused before anything was read


def test_ep_on_bible_is_a_usage_error(cli):
    code = cli.run("step", "000000000000", "bible", "--ep", "1")

    assert code == 2
    err = cli.capsys.readouterr().err
    assert "--ep applies to" in err and "not to 'bible'" in err


def test_fast_on_script_is_a_usage_error(cli):
    code = cli.run("step", "000000000000", "script", "--ep", "1", "--fast")

    assert code == 2
    err = cli.capsys.readouterr().err
    assert "--fast applies to 'storyboard'" in err and "not to 'script'" in err


def test_measure_voices_on_storyboard_is_a_usage_error(cli):
    code = cli.run("step", "000000000000", "storyboard", "--ep", "1", "--measure-voices")

    assert code == 2
    err = cli.capsys.readouterr().err
    assert "--measure-voices applies to 'script'" in err and "not to 'storyboard'" in err


def test_a_story_that_is_not_ready_is_refused(cli):
    assert cli.run("new", "--lang", "fr") == 0
    story_id = cli.capsys.readouterr().out.split()[0]
    _keyed(cli)

    code = cli.run("step", story_id, "script", "--ep", "1")

    assert code == 1
    assert "not ready" in cli.capsys.readouterr().err


def test_episode_2_is_refused_naming_the_memory_step(cli):
    story_id = _ready(cli)  # no memory for episode 1 yet
    _keyed(cli)

    code = cli.run("step", story_id, "script", "--ep", "2")

    assert code == 1
    err = cli.capsys.readouterr().err
    assert ("Episode 1's series memory is not written yet: approve episode 1's script, then run memory for "
            "episode 1 and approve it, before writing episode 2.") in err


def test_auto_approve_approves_a_passed_script(cli):
    story_id = _ready(cli)
    _keyed(cli)
    _fake_llm(cli, eps._script_llm(E4=[eps.E4_PASSED]))

    code = cli.run("step", story_id, "script", "--ep", "1", "--auto-approve")

    assert code == 0
    out = cli.capsys.readouterr().out
    assert "✅ Script approved." in out
    script = cli.episode_doc(story_id, 1, "script.json")
    assert script["approved_at"] is not None and script["approved_anyway"] is None


def test_auto_approve_refuses_a_script_with_issues_and_approves_nothing(cli):
    story_id = _ready(cli)
    _keyed(cli)
    _fake_llm(cli, eps._script_llm())  # E4 -> two issues, the default

    code = cli.run("step", story_id, "script", "--ep", "1", "--auto-approve")

    assert code == 1
    err = cli.capsys.readouterr().err
    # The issues (scene id, kind, fix) and the sentence the API would answer,
    # both in the one message workflow.approve_script raises.
    assert "s03" in err and "character" in err and "continuity" in err
    assert "approve anyway" in err
    script = cli.episode_doc(story_id, 1, "script.json")
    assert script["approved_at"] is None


# --------------------------------------------------------------- storyboard

def test_a_storyboard_before_a_complete_script_is_refused(cli):
    story_id = _ready(cli)
    _keyed(cli)

    code = cli.run("step", story_id, "storyboard", "--ep", "1")

    assert code == 1
    assert "write it first" in cli.capsys.readouterr().err


def test_the_fast_storyboard_makes_no_call_and_needs_no_key(cli):
    story_id = _written(cli)
    _no_llm_call(cli)  # no _keyed(cli): no key is set at all

    code = cli.run("step", story_id, "storyboard", "--ep", "1", "--fast")

    assert code == 0
    board = cli.episode_doc(story_id, 1, "storyboard.json")
    assert board["shots"] and {entry["source"] for entry in board["scenes"].values()} == {"fast"}
    out = cli.capsys.readouterr().out
    assert f"🎞 Episode 1: {len(board['shots'])} shots over {len(board['scenes'])} scenes." in out.splitlines()


def test_the_t1_storyboard_is_one_call_per_scene(cli):
    story_id = _written(cli)
    _keyed(cli)
    fake = _fake_llm(cli, eps.FakeLLM(default={"T1": eps.t1_reply}))

    code = cli.run("step", story_id, "storyboard", "--ep", "1")

    assert code == 0
    assert fake.prompts() == ["T1"] * len(eps.ALL_SCENES)
    board = cli.episode_doc(story_id, 1, "storyboard.json")
    assert {entry["source"] for entry in board["scenes"].values()} == {"t1"}
    out = cli.capsys.readouterr().out
    assert f"🎞 Episode 1: {len(board['shots'])} shots over {len(board['scenes'])} scenes." in out.splitlines()


# ------------------------------------------------------------------- list

def test_list_shows_the_episode_summary_once_there_is_one(cli):
    story_id = _ready(cli)
    _keyed(cli)
    _fake_llm(cli, eps._script_llm(E4=[eps.E4_PASSED]))
    assert cli.run("step", story_id, "script", "--ep", "1", "--auto-approve") == 0
    cli.capsys.readouterr()
    _no_llm_call(cli)
    assert cli.run("step", story_id, "storyboard", "--ep", "1", "--fast", "--auto-approve") == 0
    cli.capsys.readouterr()

    code = cli.run("list")

    assert code == 0
    out = cli.capsys.readouterr().out
    assert re.search(r"^\S+  ready .*; ep1 script approved, storyboard approved, \d+\.\d+ s$", out.strip())


def test_list_says_nothing_extra_for_a_story_with_no_episode(cli):
    story_id = _ready(cli)

    assert cli.run("list") == 0
    out = cli.capsys.readouterr().out.strip()
    assert out.startswith(story_id) and ";" not in out


# ------------------------------------------------------------------- help

def test_the_help_lists_the_phase_3_step_options(cli):
    from clipping.aistory import cli as cli_module

    assert cli_module.main(["step", "--help"]) == 0
    out = cli.capsys.readouterr().out
    for option in ("--ep", "--fast", "--measure-voices"):
        assert option in out
    for step in ("script", "storyboard"):
        assert step in out


def test_the_clip_parsers_help_is_unaffected_by_the_new_phase_3_options(tmp_path):
    # The clip parser (main.py without --ai-story) is untouched by this
    # stage; only clipping/aistory/cli.py changed. As
    # test_story_cli.py::test_the_clip_help_still_works_and_points_at_ai_story
    # does for phase 1, this checks the clip parser never saw the new
    # options or steps, and that its pointer line is exactly what it was.
    from clipping import config

    result = _main_py("--help", tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    out = result.stdout
    for leaked in ("--ep", "--fast", "--measure-voices"):
        assert leaked not in out
    assert out.rstrip().splitlines()[-1] == config.AI_STORY_POINTER
    assert not any(name in ("--ep", "--fast", "--measure-voices")
                   for action in config._build_parser()._actions for name in action.option_strings)
