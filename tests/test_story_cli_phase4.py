"""``python main.py --ai-story step <id> assets|render|metadata --ep N``, the
``render`` alias and ``fast-track`` (AI Story phase 4, stage 13; spec 9.3,
DEC-114: one set of rules, two front ends; DEC-009: argparse ``choices=``
mirror the modules' own closed lists).

Driven through ``clipping.aistory.cli.main(argv)`` against a story store
under ``tmp_path`` (the hidden ``--outputs-dir``), exactly as
``tests/test_story_cli_episode.py`` drives phases 1-3. The CLI's own
``_run_step`` calls a step's ``run(ctx)`` with nothing else (the module
docstring): the LLM chain is faked at ``clipping.providers.llm.run_chain``
(phase 3's own seam -- ``llm_call.call_json`` looks ``runner`` up fresh when
it is None, so this reaches every LLM call the CLI makes, including
``fast-track``'s own). The image, voice and ffmpeg chains have no such
global seam (``None`` there means "the real thing"), so a test that needs
them monkeypatches the step module's own ``run`` to bind the fakes
``tests/test_story_assets_step.py``, ``test_story_render_step.py``,
``test_story_metadata_step.py`` and ``test_story_fast_track.py`` already
built and proved (``steps._deferred`` looks a step's ``run`` up fresh on
every call, so this is picked up exactly as a real dispatch would pick up
the original). Offline and hermetic: stage 8's own ``hermetic`` fixture
(imported below).

The new CLI surface is imported inside the tests, so on the parent commit
(``18d4566``) each test fails on its own instead of the file failing to
collect.

Stdlib + pytest only: this file runs in the CI environment (DEC-012).
"""

from __future__ import annotations

import argparse
import importlib
from types import SimpleNamespace

import pytest

import test_aistory_render_runner as rr
import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_fast_track as tft
import test_story_measure as tsm
import test_story_metadata_step as tms
import test_story_render_step as trs
from clipping.aistory import schemas
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used here as they are
from test_story_render_step import built  # noqa: F401 -- the session's episode copies


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def cli(monkeypatch, store, capsys):
    """``cli.run(*argv)`` -> exit code, against *store*'s own outputs
    directory. ``hermetic`` (imported above, autouse) isolates the whole
    process (no network, usage/spend under ``tmp_path``); this also clears
    the LLM key gate's own env vars, as ``test_story_cli_episode.py``'s
    ``cli`` fixture does."""
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
    """Bind *fixed_kwargs* to *module*.run: the CLI's own ``_run_step`` calls
    ``module.run(ctx)`` with nothing else (``steps._deferred`` looks the
    function up fresh each time -- the module docstring), so this is the
    only seam left for a step's test-only kwargs (adapters, ffmpeg, fonts,
    a fake LLM runner...) when driving it through the CLI entry rather than
    calling it directly, as the step's own test file does."""
    real = module.run

    def wrapped(ctx, **_ignored):
        return real(ctx, **fixed_kwargs)

    cli.monkeypatch.setattr(module, "run", wrapped)


def _patch_assets(cli, *, image=None, edge=None):
    from clipping.aistory.steps import assets as assets_module

    cli.monkeypatch.setenv("IMAGE_CHAIN", "pollinations/flux")  # the CLI reads os.environ, never Settings
    adapters = tas._adapters(edge or tsm.Edge(), image=image or tas.FakeImage())
    _patch_run(cli, assets_module, adapters=adapters, time_fn=eps.Clock(0.0), sleep_fn=lambda _s: None)


def _patch_render(cli, tmp_path, *, fake=None):
    from clipping.aistory.steps import render as render_module

    fake = fake or rr.FakeFFmpeg()
    _patch_run(cli, render_module, run_process=rr._fake_run(), popen=fake, clock=fake.clock,
              custom_fonts_dir=tmp_path / "no_custom_fonts")
    return fake


def _patch_metadata(cli, tmp_path, *, runner=None, cover=None):
    from clipping.aistory.steps import metadata as metadata_module

    cover = cover or tms.FakeCover()
    _patch_run(cli, metadata_module, runner=runner, run_process=cover, custom_fonts_dir=tmp_path / "no_custom_fonts")
    return cover


# ----------------------------------------------------- the parser's own lists

def _subparser(parser, name):
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return action.choices[name]


def _option(parser, flag):
    return next(a for a in parser._actions if flag in a.option_strings)


def _positional(parser, dest):
    return next(a for a in parser._actions if a.dest == dest and not a.option_strings)


def test_the_parser_choices_mirror_the_modules_own_closed_lists():
    from clipping.aistory import cli as cli_module
    from clipping.aistory.steps import fast_track as fast_track_step
    from clipping.aistory.steps import render as render_step

    parser = cli_module.build_parser()

    step = _subparser(parser, "step")
    assert {"assets", "render", "metadata"} <= set(_positional(step, "step").choices)
    assert tuple(_option(step, "--subtitles").choices) == render_step.SUBTITLE_CHOICES
    assert tuple(_option(step, "--encoder").choices) == render_step.ENCODER_CHOICES

    render_cmd = _subparser(parser, "render")
    assert tuple(_option(render_cmd, "--subtitles").choices) == render_step.SUBTITLE_CHOICES
    assert tuple(_option(render_cmd, "--encoder").choices) == render_step.ENCODER_CHOICES

    fast_track_cmd = _subparser(parser, "fast-track")
    assert tuple(_option(fast_track_cmd, "--storyboard").choices) == fast_track_step.STORYBOARD_CHOICES


# ---------------------------------------------------------------- usage errors

def test_align_words_on_render_is_a_usage_error(cli):
    code = cli.run("step", "000000000000", "render", "--ep", "1", "--align-words")

    assert code == 2
    err = cli.capsys.readouterr().err
    assert "--align-words applies to 'assets'" in err and "not to 'render'" in err


def test_subtitles_on_assets_is_a_usage_error(cli):
    code = cli.run("step", "000000000000", "assets", "--ep", "1", "--subtitles", "word_pop")

    assert code == 2
    err = cli.capsys.readouterr().err
    assert "--subtitles applies to 'render'" in err and "not to 'assets'" in err


def test_encoder_on_metadata_is_a_usage_error(cli):
    code = cli.run("step", "000000000000", "metadata", "--ep", "1", "--encoder", "auto")

    assert code == 2
    err = cli.capsys.readouterr().err
    assert "--encoder applies to 'render'" in err and "not to 'metadata'" in err


def test_ep_missing_for_assets_is_a_usage_error(cli):
    code = cli.run("step", "000000000000", "assets")

    assert code == 2
    assert "--ep is required for 'assets'" in cli.capsys.readouterr().err


def test_auto_approve_on_render_is_a_usage_error(cli):
    code = cli.run("step", "000000000000", "render", "--ep", "1", "--auto-approve")

    assert code == 2
    err = cli.capsys.readouterr().err
    assert "--auto-approve does not apply to 'render'" in err and "nothing to approve" in err


def test_auto_approve_on_metadata_is_a_usage_error(cli):
    code = cli.run("step", "000000000000", "metadata", "--ep", "1", "--auto-approve")

    assert code == 2
    err = cli.capsys.readouterr().err
    assert "--auto-approve does not apply to 'metadata'" in err and "nothing to approve" in err


def test_the_clip_parsers_help_is_unaffected_by_the_new_phase_4_options():
    from clipping import config

    parser = config._build_parser()
    for leaked in ("--align-words", "--subtitles", "--encoder", "--storyboard"):
        assert not any(leaked in action.option_strings for action in parser._actions)


# --------------------------------------------------------------------- assets

def test_assets_runs_through_the_cli_making_images_and_voicing_every_line(cli, tmp_path):
    story_id = tas._episode(cli.store, tmp_path)
    _patch_assets(cli)

    code = cli.run("step", story_id, "assets", "--ep", "1")

    assert code == 0
    out = cli.capsys.readouterr().out
    lines = out.splitlines()
    assert any(line.startswith("🖼 Episode 1's assets (complete):") for line in lines)
    assert lines[-1].startswith(story_id)  # the story line, still, at the end (RC-E2)
    doc = cli.store.read_episode_doc(story_id, 1, "assets.json")
    board = cli.store.read_episode_doc(story_id, 1, "storyboard.json")
    assert doc["lines"] and all(shot["assets"]["image"] for shot in board["shots"])


def test_auto_approve_on_assets_approves_a_complete_current_grid(cli, tmp_path):
    story_id = tas._episode(cli.store, tmp_path)
    _patch_assets(cli)

    code = cli.run("step", story_id, "assets", "--ep", "1", "--auto-approve")

    assert code == 0
    assert "✅ Assets approved." in cli.capsys.readouterr().out
    doc = cli.store.read_episode_doc(story_id, 1, "assets.json")
    assert doc["approved"] is not None


def test_auto_approve_on_assets_refuses_an_incomplete_grid(cli, tmp_path):
    story_id = tas._episode(cli.store, tmp_path)
    _patch_assets(cli, image=tas.FakeImage(fail_for={"shot_01"}))

    code = cli.run("step", story_id, "assets", "--ep", "1", "--auto-approve")

    assert code == 1
    err = cli.capsys.readouterr().err
    assert "sh01" in err and "no current image" in err
    doc = cli.store.read_episode_doc(story_id, 1, "assets.json")
    assert doc["approved"] is None


def test_a_paid_stop_through_the_cli_prints_the_numbers_and_exits_1(cli, tmp_path):
    story_id = tas._episode(cli.store, tmp_path)
    cli.monkeypatch.setenv("IMAGE_CHAIN", "fal/flux-schnell")
    cli.monkeypatch.setenv("FAL_KEY", "test-fal-key")
    cli.monkeypatch.setenv("ALLOW_PAID", "1")
    cli.monkeypatch.setenv("PER_EPISODE_CAP_USD", "0.05")
    from clipping.aistory.steps import assets as assets_module

    fal = tas.FakeImage(price=tas.FAL_PRICE)
    _patch_run(cli, assets_module, adapters=tas._adapters(tsm.Edge(), fal=fal), time_fn=eps.Clock(0.0),
              sleep_fn=lambda _s: None)

    code = cli.run("step", story_id, "assets", "--ep", "1")

    assert code == 1
    err = cli.capsys.readouterr().err
    assert "would bring this episode to" in err and "of its $0.05 cap" in err
    assert "Nothing was generated or spent" in err
    assert fal.requests == []


# --------------------------------------------------------------------- render

def test_render_runs_through_the_cli_via_step(cli, tmp_path, built):
    story_id = trs.episode(cli.store, tmp_path, built, kind="approved")
    _patch_render(cli, tmp_path)

    code = cli.run("step", story_id, "render", "--ep", "1")

    assert code == 0
    out = cli.capsys.readouterr().out
    assert any(line.startswith("🎬 Episode 1 rendered:") for line in out.splitlines())
    manifest = cli.store.read_episode_doc(story_id, 1, "render_manifest.json")
    assert manifest["output"]["sha256"]


def test_the_render_command_is_the_same_as_step_render(monkeypatch, tmp_path, built):
    """``render ID --ep N [...]`` is exactly ``step ID render --ep N [...]``
    (module docstring): both reach ``render.run`` with the same params and
    produce the same manifest and output."""
    from clipping.aistory.steps import render as render_module
    from clipping.aistory.store import StoryStore

    def once(label, build_argv):
        store = StoryStore(tmp_path / label / "outputs", on_log=lambda line: None)
        story_id = trs.episode(store, tmp_path / label, built, kind="approved")
        real = render_module.run
        fake = rr.FakeFFmpeg()
        monkeypatch.setattr(render_module, "run",
                            lambda ctx, **_ignored: real(ctx, run_process=rr._fake_run(), popen=fake,
                                                         clock=fake.clock,
                                                         custom_fonts_dir=tmp_path / label / "no_custom_fonts"))
        module = importlib.import_module("clipping.aistory.cli")
        code = module.main([*build_argv(story_id), "--outputs-dir", str(store.outputs_dir)])
        assert code == 0
        return store.read_episode_doc(story_id, 1, "render_manifest.json")

    via_step = once("a", lambda sid: ["step", sid, "render", "--ep", "1", "--subtitles", "word_pop"])
    via_alias = once("b", lambda sid: ["render", sid, "--ep", "1", "--subtitles", "word_pop"])

    assert via_step["params"] == via_alias["params"] == {"subtitles": "word_pop", "encoder": "libx264"}
    assert via_step["output"] == via_alias["output"]


# ------------------------------------------------------------------- metadata

def test_metadata_runs_through_the_cli_writing_every_platform(cli, tmp_path, built):
    story_id = tms.rendered(cli.store, tmp_path, built)
    _keyed(cli)
    _patch_metadata(cli, tmp_path, runner=tms.llm())

    code = cli.run("step", story_id, "metadata", "--ep", "1")

    assert code == 0
    out = cli.capsys.readouterr().out
    assert any(line.startswith("🏷 Episode 1's metadata") for line in out.splitlines())
    pack = cli.store.read_episode_doc(story_id, 1, "metadata_pack.json")
    assert list(pack["platforms"]) == list(schemas.PLATFORMS)


# ---------------------------------------------------------------- fast-track

def test_fast_track_runs_through_the_cli_from_script_to_metadata(cli, tmp_path):
    from clipping.aistory.steps import fast_track as fast_track_module

    story_id = tft._story(cli.store)
    _keyed(cli)
    fakes = tft.Fakes(tmp_path)
    _patch_run(cli, fast_track_module, **fakes.kwargs())

    code = cli.run("fast-track", story_id, "--ep", "1")

    assert code == 0
    out = cli.capsys.readouterr().out
    assert any(line.startswith("⏩ Fast track of episode 1 done") for line in out.splitlines())
    pack = cli.store.read_episode_doc(story_id, 1, "metadata_pack.json")
    assert list(pack["platforms"]) == list(schemas.PLATFORMS)
    manifest = cli.store.read_episode_doc(story_id, 1, "render_manifest.json")
    assert manifest["output"]["sha256"]


def test_fast_track_needs_ep(cli):
    code = cli.run("fast-track", "000000000000")

    assert code == 2
    assert "--ep" in cli.capsys.readouterr().err
