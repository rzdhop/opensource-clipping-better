"""``python main.py --ai-story step <id> rerender --ep N [--dry-run]`` (AI
Story phase 5, plan 11 "Stage 9 -- re-edit API and CLI"; spec 9.3; DEC-114:
one set of rules, two front ends; DEC-009: argparse ``choices=`` mirror the
modules' own closed lists; DEC-012, DEC-161).

Driven through ``clipping.aistory.cli.main(argv)`` against a story store
under ``tmp_path`` (the hidden ``--outputs-dir``), exactly as
``tests/test_story_cli_phase4.py`` drives ``render``. The CLI's own
``_run_step`` calls a step's ``run(ctx)`` with nothing else, so a real run is
driven with stage 7's fake ffmpeg bound onto ``steps.rerender.run`` the same
way ``_patch_render`` binds it onto ``steps.render.run`` (``_patch_rerender``,
duplicated from the phase-4 CLI test so this file stays independently
readable). The fixture episode is stage 9's own -- ``tests/
test_story_metadata_step.py``'s ``rendered`` (phase 3's French story, its
assets made and approved, rendered once with the fake ffmpeg) -- and a
re-edit is applied directly against the store, exactly as the render CLI
tests build their preconditions, since the edit itself is stage 7's, already
proved through ``tests/test_story_reedit.py``; only the new step and its
``--dry-run`` are under test here. Offline and hermetic: stage 8's own
``hermetic`` fixture (imported below).

``--dry-run`` is checked against ``workflow.reedit_changes`` called directly
(RC-M8: the CLI, the route and the runner all read one selection, never a
second implementation) and, separately, by never registering a fake ffmpeg
for it -- had it started a real render, the missing ffmpeg binary in this
sandbox would fail the test.

The new CLI surface (``rerender`` in ``STEPS``, ``--dry-run``,
``workflow.reedit_changes``) is imported inside the tests, so on the parent
commit (``ecd94aa``) each test fails on its own: ``rerender`` is not in
``STEPS`` there and ``--dry-run`` does not exist.

Stdlib + pytest only: this file runs in the CI environment (DEC-012).
"""

from __future__ import annotations

import argparse
import importlib
from types import SimpleNamespace

import pytest

import test_aistory_render_runner as rr
import test_story_episode_steps as eps
import test_story_metadata_step as tms
import test_story_render_step as trs
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used here as they are
from test_story_render_step import built  # noqa: F401 -- the session's episode copies

NOW = eps.NOW


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def cli(monkeypatch, store, capsys):
    """``cli.run(*argv)`` -> exit code, against *store*'s own outputs
    directory. ``hermetic`` (imported above, autouse) isolates the whole
    process (no network, usage/spend under ``tmp_path``); this also clears
    the LLM key gate's own env vars, as the phase-4 CLI test's ``cli``
    fixture does (``rerender`` calls no LLM, but the fixture stays identical
    to every other step's for one story store's worth of isolation)."""
    for name in ("LLM_CHAIN", "ALLOW_SLOW_CHAIN", "ALLOW_PAID"):
        monkeypatch.delenv(name, raising=False)

    def run(*argv):
        module = importlib.import_module("clipping.aistory.cli")
        return module.main([*argv, "--outputs-dir", str(store.outputs_dir)])

    return SimpleNamespace(run=run, store=store, monkeypatch=monkeypatch, capsys=capsys)


def _patch_run(cli, module, **fixed_kwargs):
    """Bind *fixed_kwargs* to *module*.run (the phase-4 CLI test's own
    helper, duplicated so this file stays independently readable): the
    CLI's own ``_run_step`` calls ``module.run(ctx)`` with nothing else
    (``steps._deferred`` looks the function up fresh each time), so this is
    the only seam left for a step's test-only kwargs when driving it through
    the CLI entry rather than calling it directly."""
    real = module.run

    def wrapped(ctx, **_ignored):
        return real(ctx, **fixed_kwargs)

    cli.monkeypatch.setattr(module, "run", wrapped)


def _patch_rerender(cli, tmp_path, *, fake=None):
    from clipping.aistory.steps import rerender as rerender_module

    fake = fake or rr.FakeFFmpeg()
    _patch_run(cli, rerender_module, run_process=rr._fake_run(), popen=fake, clock=fake.clock,
              custom_fonts_dir=tmp_path / "no_custom_fonts")
    return fake


def _manifest(cli, story_id):
    return cli.store.read_episode_doc(story_id, 1, "render_manifest.json")


def _motion_swap(cli, story_id, *, shot_id="sh05", camera_motion="pan_rl"):
    """A motion swap through the workflow (stage 7's own path, proved by
    ``tests/test_story_reedit.py``; here only a precondition of the step
    under test): the image is kept, only the shot's render key moves."""
    from clipping.aistory import workflow

    workflow.patch_storyboard(cli.store, story_id, 1, {"shots": [{"shot_id": shot_id,
                                                                  "camera_motion": camera_motion}]}, now=NOW)
    workflow.approve_storyboard(cli.store, story_id, 1, now=NOW)


def _framing_edit(cli, story_id, *, shot_id="sh04", framing="low_angle"):
    """A framing edit (stage 7): outdates only the shot's image on disk; a
    render (so a re-render) refuses it, naming the shot."""
    from clipping.aistory import workflow

    workflow.patch_storyboard(cli.store, story_id, 1, {"shots": [{"shot_id": shot_id, "framing": framing}]}, now=NOW)
    workflow.approve_storyboard(cli.store, story_id, 1, now=NOW)


# ----------------------------------------------------- the parser's own lists

def _subparser(parser, name):
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return action.choices[name]


def _option(parser, flag):
    return next(a for a in parser._actions if flag in a.option_strings)


def _positional(parser, dest):
    return next(a for a in parser._actions if a.dest == dest and not a.option_strings)


def test_rerender_is_one_of_steps_and_takes_ep_and_dry_run():
    from clipping.aistory import cli as cli_module
    from clipping.aistory import workflow

    # Plan 21 stage 2 appends workflow.AGENT_STEPS after REEDIT_STEPS, so
    # "rerender" is no longer STEPS' last entry -- re-pinned on purpose.
    assert "rerender" == workflow.REEDIT_STEPS[0]
    assert cli_module.STEPS[-1] == workflow.AGENT_STEPS[-1] == "story-fast-track"
    parser = cli_module.build_parser()
    step = _subparser(parser, "step")
    assert "rerender" in _positional(step, "step").choices
    assert _option(step, "--dry-run").const is True  # store_true


def test_rerender_never_takes_auto_approve():
    from clipping.aistory import cli as cli_module

    assert "rerender" not in cli_module.AUTO_APPROVABLE
    assert "nothing to approve" in cli_module._NOT_AUTO_APPROVABLE["rerender"]


# ---------------------------------------------------------------- usage errors

def test_ep_is_required_for_rerender(cli):
    code = cli.run("step", "000000000000", "rerender")

    assert code == 2
    err = cli.capsys.readouterr().err
    assert "--ep is required for 'rerender'" in err


def test_dry_run_applies_to_rerender_only(cli):
    code = cli.run("step", "000000000000", "render", "--ep", "1", "--dry-run")

    assert code == 2
    err = cli.capsys.readouterr().err
    assert "--dry-run applies to 'rerender'" in err and "not to 'render'" in err


def test_subtitles_and_encoder_do_not_apply_to_rerender(cli):
    """``--subtitles``/``--encoder`` stay ``render``'s own: a re-render keeps
    the last render's own (``rerender.run``'s own refusal, proved below by
    ``test_rerender_takes_no_parameters``); the CLI never lets them reach
    it."""
    for flag, value in (("--subtitles", "none"), ("--encoder", "auto")):
        code = cli.run("step", "000000000000", "rerender", "--ep", "1", flag, value)
        assert code == 2
        err = cli.capsys.readouterr().err
        assert f"{flag} applies to" in err and "not to 'rerender'" in err


def test_auto_approve_on_rerender_is_a_usage_error(cli):
    code = cli.run("step", "000000000000", "rerender", "--ep", "1", "--auto-approve")

    assert code == 2
    err = cli.capsys.readouterr().err
    assert "--auto-approve does not apply to 'rerender'" in err and "nothing to approve" in err


# --------------------------------------------------------------------- run

def test_rerender_runs_through_the_cli_and_prints_the_summary(cli, tmp_path, built):
    story_id = tms.rendered(cli.store, tmp_path, built)
    total = len(trs._shots(cli.store, story_id))
    before = _manifest(cli, story_id)
    _patch_rerender(cli, tmp_path)

    code = cli.run("step", story_id, "rerender", "--ep", "1")

    assert code == 0, cli.capsys.readouterr().err
    out = cli.capsys.readouterr().out
    assert any(line.startswith("🎬 Episode 1 re-rendered:") for line in out.splitlines())
    assert f"0 of {total} shots re-rendered" in out and f"{total} reused" in out
    after = _manifest(cli, story_id)
    assert after["reuse"]["shots_rebuilt"] == [] and len(after["reuse"]["shots_reused"]) == total
    assert after["output"]["sha256"] == before["output"]["sha256"]  # nothing to remake: the same bytes
    last_good = cli.store.read_episode_doc(story_id, 1, "render_manifest.last_good.json")
    assert last_good == after


def test_rerender_refuses_without_a_finished_render(cli, tmp_path, built):
    story_id = trs.episode(cli.store, tmp_path, built, kind="approved")

    code = cli.run("step", story_id, "rerender", "--ep", "1")

    assert code == 1
    err = cli.capsys.readouterr().err
    assert "no finished render to re-render" in err and "render it first" in err


def test_rerender_refuses_an_outdated_shot_image_naming_it(cli, tmp_path, built):
    story_id = tms.rendered(cli.store, tmp_path, built)
    _framing_edit(cli, story_id)

    code = cli.run("step", story_id, "rerender", "--ep", "1")

    assert code == 1
    err = cli.capsys.readouterr().err
    assert "sh04" in err and "out of date" in err and "shot:1:sh04" in err


def test_rerender_takes_no_parameters():
    """The runner's own refusal (``rerender.run``), which the CLI -- sending
    ``{}`` always, since no flag of its own reaches ``rerender`` -- never
    triggers; ``workflow.reedit_request`` duplicates its sentence for the
    route and would refuse the same way if a caller sent one some other way
    (the route's request model has no field that could)."""
    from clipping.aistory import workflow

    with pytest.raises(workflow.WorkflowError) as caught:
        workflow.reedit_request({"subtitles": "none"})
    assert "takes no parameters" in str(caught.value) and "subtitles" in str(caught.value)
    assert "render the episode (the render step)" in str(caught.value)


# ----------------------------------------------------------------- dry run

def test_dry_run_prints_the_selection_and_renders_nothing(cli, tmp_path, built):
    story_id = tms.rendered(cli.store, tmp_path, built)
    _motion_swap(cli, story_id)
    before = _manifest(cli, story_id)

    # No fake ffmpeg is registered: a real render attempt would fail on the
    # missing binary in this sandbox, so a green --dry-run also proves it
    # started no process.
    code = cli.run("step", story_id, "rerender", "--ep", "1", "--dry-run")

    assert code == 0, cli.capsys.readouterr().err
    out = cli.capsys.readouterr().out
    assert "sh05: motion" in out
    assert _manifest(cli, story_id) == before


def test_the_dry_run_count_matches_workflow_reedit_changes_directly(cli, tmp_path, built):
    """RC-M8: the CLI's ``--dry-run`` prints exactly what ``workflow.
    reedit_changes`` (the route's and the episode page's own) computes --
    never a second selector."""
    from clipping.aistory import workflow
    from clipping.aistory.steps import episode_common

    story_id = tms.rendered(cli.store, tmp_path, built)
    _motion_swap(cli, story_id)
    ec = episode_common.load_context(cli.store, story_id, 1)
    expected = workflow.reedit_changes(ec)

    code = cli.run("step", story_id, "rerender", "--ep", "1", "--dry-run")

    assert code == 0
    out = cli.capsys.readouterr().out
    assert expected["summary"] in out
    assert f"({len(expected['reuse'])} reused)" in out
    for shot_id in expected["rebuild"]:
        assert f"{shot_id}: {expected['reasons'][shot_id]}" in out


def test_dry_run_refuses_an_outdated_shot_image_naming_it_and_renders_nothing(cli, tmp_path, built):
    story_id = tms.rendered(cli.store, tmp_path, built)
    _framing_edit(cli, story_id)
    before = _manifest(cli, story_id)

    code = cli.run("step", story_id, "rerender", "--ep", "1", "--dry-run")

    assert code == 1
    err = cli.capsys.readouterr().err
    assert "sh04" in err and "out of date" in err
    assert _manifest(cli, story_id) == before


def test_dry_run_refuses_without_a_finished_render(cli, tmp_path, built):
    story_id = trs.episode(cli.store, tmp_path, built, kind="approved")

    code = cli.run("step", story_id, "rerender", "--ep", "1", "--dry-run")

    assert code == 1
    err = cli.capsys.readouterr().err
    assert "no finished render to re-render" in err
