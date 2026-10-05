"""``python main.py --ai-story new --mode agent`` / ``step <id> story-fast-track`` / ``agent`` -- the CLI
surface of agent mode (plan 21 stage 2; the backend is ``clipping/aistory/steps/story_fast_track.py``, plan 21
stage 1, DEC-270).

Driven through ``clipping.aistory.cli.main(argv)`` exactly as ``tests/test_story_cli_phase4.py`` drives
``fast-track`` through the CLI: the LLM chain is faked at ``clipping.providers.llm.run_chain`` (reached by
every LLM call the CLI makes, including every part of the agent run); the image, voice, ffmpeg and cover
chains have no such global seam, so a full run binds ``test_story_fast_track.Fakes`` to
``steps.story_fast_track.run`` the same way (``_patch_run``, imported from ``test_story_cli_phase4``) that
file binds them to ``fast_track.run``. The replies are ``test_story_fast_track_story.py``'s own proven set (a
French, prompt-only, fruit_drama seed story on the legacy pipeline -- no ``pipeline`` key on
``generation_profile``, so the agent run's ``knowledge`` part is skipped as "legacy": concept -> bible -> style
-> cast -> places proposal -> places -> season -> episode 1). Those fakes take the runner all the way to
episode 1 rendered when it is called directly (that file proves it); the same fakes, bound through the CLI's
own seam, take the ``agent`` command there too.

Stdlib + pytest only (DEC-012); the new CLI surface is imported inside the tests, so on the parent commit each
test fails on its own instead of the whole file failing to collect.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_story_fast_track as tft
import test_story_fast_track_story as tfts
from test_story_fast_track_story import local_speech  # noqa: F401 -- piper faked: the agent's cast speaks locally
from clipping.aistory import defaults
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_cli import PHASE2_ENV  # noqa: F401 -- the free route of this machine, as the CLI reads it
from test_story_cli_phase4 import _patch_run  # noqa: F401 -- the CLI's seam for a step's test-only kwargs

SEED = tfts.SEED


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def cli(monkeypatch, store, capsys):
    """``cli.run(*argv)`` -> exit code, against *store*'s own outputs
    directory; no key or chain switch from the machine running the tests
    (``hermetic``, imported above, clears them first)."""

    def run(*argv):
        module = importlib.import_module("clipping.aistory.cli")
        return module.main([*argv, "--outputs-dir", str(store.outputs_dir)])

    def story(story_id):
        path = Path(store.story_dir(story_id)) / "story.json"
        return json.loads(path.read_text(encoding="utf-8"))

    return SimpleNamespace(run=run, store=store, story=story, monkeypatch=monkeypatch, capsys=capsys)


def _keyed(cli):
    """A one-link chain with a (test) key: the key gate passes."""
    cli.monkeypatch.setenv("LLM_CHAIN", "gemini/gemini-test")
    cli.monkeypatch.setenv("GOOGLE_API_KEY", "test-gemini-key")


def _free_route(cli):
    """The free route of this machine (test values): a free text-to-image
    link, an editor (unused under prompt-only), Gemini speech for the voices --
    ``test_story_cli.py``'s own ``PHASE2_ENV``, reused as it is."""
    for name, value in PHASE2_ENV.items():
        cli.monkeypatch.setenv(name, value)
    cli.monkeypatch.setenv("TTS_CHAIN", "local/piper")  # the agent's cast speaks locally (plan 28 stage B2)


def _new_agent(cli, *extra):
    """An agent-mode French story through the CLI, the fast-track story
    fixture's seed, fruit_drama, prompt-only; its id."""
    code = cli.run("new", "--lang", "fr", "--mode", "agent", "--seed-text", SEED, "--style", "fruit_drama",
                   "--consistency-mode", "prompt_only", *extra)
    out, err = cli.capsys.readouterr()
    assert code == 0, err
    return out.split()[0]


def _new_studio(cli, *extra):
    """A plain Studio story through the CLI; its id."""
    code = cli.run("new", "--lang", "fr", *extra)
    out, err = cli.capsys.readouterr()
    assert code == 0, err
    return out.split()[0]


def _pin_writing_v2(cli):
    """Pin every story this test creates to "writing": "v2" (plan 22 stage 2,
    DEC-274): ``store.create`` stamps "v3" on a new story by default, which
    (with this file's non-empty seed) would switch the concepts step from C1
    to the brief-faithful C1v2/C1J -- this file drives the CLI surface of
    the agent run end to end, not concept fidelity (covered by
    ``tests/test_story_concepts_brief.py``), and ``tfts.llm()``'s queue is
    built for C1's reply. The CLI has no ``--writing`` flag, so the pin goes
    on ``StoryStore.create`` itself, the same way ``test_story_fast_track_story.py``
    and ``test_story_steps.py`` pin it on the profile dict they pass directly."""
    from clipping.aistory.store import StoryStore

    real_create = StoryStore.create

    def create_v2(self, *args, **kwargs):
        kwargs["generation_profile"] = {"writing": "v2", **(kwargs.get("generation_profile") or {})}
        return real_create(self, *args, **kwargs)

    cli.monkeypatch.setattr(StoryStore, "create", create_v2)


def _bind_agent_fakes(cli, tmp_path):
    """Bind ``test_story_fast_track.Fakes`` to the agent step's own module
    (``_patch_run``, the CLI's seam, module docstring): the LLM, the images,
    the voices, ffmpeg and the cover -- the same fakes
    ``test_story_fast_track_story.py`` proves take an agent story to episode
    1 rendered when the runner is called directly."""
    from clipping.aistory.steps import story_fast_track as story_fast_track_module

    fakes = tft.Fakes(tmp_path, runner=tfts.llm(), local=tfts._local())
    _patch_run(cli, story_fast_track_module, **fakes.kwargs())
    return fakes


# --------------------------------------------------------------------- new

def test_new_mode_agent_stores_the_profile_and_the_episode_format(cli):
    story_id = _new_agent(cli, "--format", "serial_90s_v1")

    story = cli.story(story_id)
    assert story["generation_profile"]["mode"] == defaults.MODE_AGENT
    assert story["episode_template_id"] == "serial_90s_v1"


def test_new_without_mode_stays_studio_with_no_mode_key(cli):
    story_id = _new_studio(cli)

    assert "mode" not in cli.story(story_id)["generation_profile"]


def test_new_an_unknown_format_is_a_usage_error_naming_the_shipped_ones(cli):
    code = cli.run("new", "--lang", "fr", "--format", "not_a_template")

    assert code == 2
    err = cli.capsys.readouterr().err
    assert "invalid choice" in err
    for template_id in defaults.EPISODE_TEMPLATE_IDS:
        assert template_id in err
    assert not Path(cli.store.outputs_dir).exists()


# ------------------------------------------------------------ story-fast-track

def test_story_fast_track_on_a_studio_story_is_refused_with_the_api_sentence(cli):
    from clipping.aistory import workflow

    story_id = _new_studio(cli)

    code = cli.run("step", story_id, "story-fast-track")

    assert code == 1
    err = cli.capsys.readouterr().err.strip()
    story = cli.story(story_id)
    try:
        workflow.require_agent_mode(story)
    except workflow.WorkflowError as exc:
        assert err == str(exc)
    else:
        raise AssertionError("a Studio story must refuse require_agent_mode")


def test_story_fast_track_estimate_prints_the_message_and_calls_nothing(cli):
    from clipping.providers import llm

    story_id = _new_agent(cli)

    def no_call(*_args, **_kwargs):
        raise AssertionError("the agent estimate must call nothing")

    cli.monkeypatch.setattr(llm, "run_chain", no_call)

    code = cli.run("step", story_id, "story-fast-track", "--estimate")

    assert code == 0
    out = cli.capsys.readouterr().out.strip()
    # No key configured in this hermetic test: the estimate's own message is
    # the key gate's refusal (story_fast_track_estimate's "stops_at" reason),
    # printed and nothing else -- one line, non-empty, no call was made.
    assert out and len(out.splitlines()) == 1
    assert "key" in out.lower() or "chain" in out.lower()


def test_story_fast_track_is_not_auto_approvable_with_its_own_sentence(cli):
    from clipping.aistory import cli as cli_module

    assert "story-fast-track" not in cli_module.AUTO_APPROVABLE
    assert "story-fast-track" in cli_module._NOT_AUTO_APPROVABLE
    assert "approves by itself, by: agent" in cli_module._NOT_AUTO_APPROVABLE["story-fast-track"]

    story_id = _new_agent(cli)
    code = cli.run("step", story_id, "story-fast-track", "--auto-approve")

    assert code == 2
    assert "approves by itself, by: agent" in cli.capsys.readouterr().err


def test_story_fast_track_runs_the_agent_to_episode_1_rendered(cli, tmp_path):
    _pin_writing_v2(cli)
    story_id = _new_agent(cli)
    _keyed(cli)
    _free_route(cli)
    _bind_agent_fakes(cli, tmp_path)

    code = cli.run("step", story_id, "story-fast-track")

    out = cli.capsys.readouterr().out
    assert code == 0, out
    assert any(line.startswith("🏁 Agent run done") for line in out.splitlines())
    manifest = cli.store.read_episode_doc(story_id, 1, "render_manifest.json")
    assert manifest["output"]["sha256"]


# -------------------------------------------------------------------- agent

def test_agent_command_creates_and_runs_to_episode_1_printing_the_story_id_first(cli, tmp_path):
    """``agent`` = ``new --mode agent ...`` then ``step ID story-fast-track``
    in one call: the fakes allow the whole chain through to episode 1
    rendered (module docstring), and the story's own line -- its id first
    -- is printed before the agent run's lines."""
    _pin_writing_v2(cli)
    _keyed(cli)
    _free_route(cli)
    _bind_agent_fakes(cli, tmp_path)

    code = cli.run("agent", "--lang", "fr", "--seed-text", SEED, "--style", "fruit_drama",
                   "--consistency-mode", "prompt_only")

    out = cli.capsys.readouterr().out
    assert code == 0, out
    lines = out.splitlines()
    story_id = lines[0].split()[0]
    assert lines[0].startswith(story_id)
    story = cli.story(story_id)
    assert story["generation_profile"]["mode"] == defaults.MODE_AGENT
    assert any(line.startswith("🏁 Agent run done") for line in lines)
    manifest = cli.store.read_episode_doc(story_id, 1, "render_manifest.json")
    assert manifest["output"]["sha256"]


# ----------------------------------------------------------------- concepts

def test_concepts_count_1_writes_one_card(cli):
    from clipping.aistory import workflow
    from clipping.providers import llm
    from test_story_episode_steps import FakeLLM

    story_id = _new_studio(cli)
    _keyed(cli)
    runner = FakeLLM(C1=[tfts.C1_REPLY])
    cli.monkeypatch.setattr(llm, "run_chain", runner)

    code = cli.run("step", story_id, "concepts", "--count", "1")

    assert code == 0, cli.capsys.readouterr().err
    assert len(runner.calls) == 1
    assert len(workflow.generated_cards(cli.store, story_id)) == 1
