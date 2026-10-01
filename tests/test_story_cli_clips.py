"""``main.py --ai-story step <id> assets --ep N [--tier T] [--route R]
[--no-animate] [--estimate]`` and ``render [--fill-failed-with-motion]``
(AI Story phase 6, stage 11; spec 9.3; DEC-114: one set of rules, two front
ends; DEC-209).

``--tier``/``--route`` patch the story's ``generation_profile`` through the
workflow function the API's ``PATCH /stories/{id}`` calls, and print it,
before anything runs (there is no run-level override); ``--estimate`` prints
the assets estimate -- its video part included -- and runs nothing;
``--no-animate`` and ``--fill-failed-with-motion`` reach the step's params.
Keys and chains come from the process environment only (DEC-114).

Driven through ``clipping.aistory.cli.main(argv)`` against a story store
under ``tmp_path``, as ``tests/test_story_cli_phase4.py`` drives it; the
step runner is replaced by a recorder, so no step runs. Offline and
hermetic (stage 8's ``hermetic`` fixture). Stdlib + pytest (DEC-012); the
new flags are reached inside the tests, so on the parent commit each test
fails on its own.
"""

from __future__ import annotations

import importlib
import json

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_video_phase import timings_path  # noqa: F401 -- the measured clip timings under tmp_path


@pytest.fixture
def cli(monkeypatch, store, capsys):
    """``run(*argv)`` -> exit code; ``ran`` lists every step the runner was
    asked for (``(step, ep, params)``): none runs."""
    from clipping.aistory import steps

    for name in ("LLM_CHAIN", "ALLOW_SLOW_CHAIN", "ALLOW_PAID"):
        monkeypatch.delenv(name, raising=False)
    ran = []

    def recorder(step, ctx):
        ran.append((step, ctx.ep, dict(ctx.params)))
        raise steps.StepFailed("recorded, not run")

    monkeypatch.setattr(steps, "run", recorder)

    def run(*argv):
        return importlib.import_module("clipping.aistory.cli").main([*argv, "--outputs-dir", str(store.outputs_dir)])

    return run, ran


def test_tier_and_route_patch_the_profile_print_it_and_the_estimate_runs_nothing(cli, store, tmp_path, capsys,
                                                                                monkeypatch):
    """``--tier 2 --route api --estimate``: the story's profile becomes tier
    2 on route api (printed), then the assets estimate is printed -- the
    clips on seedance, the process env's VIDEO_CHAIN, priced -- and no step
    runs, no generation is called (``hermetic`` refuses any request)."""
    run, ran = cli
    story_id = tas._episode(store, tmp_path)
    tce._tier(store, story_id, tier=1)  # the one_dollar profile, still tier 1
    for name, value in tce.VIDEO_API.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("IMAGE_CHAIN", "pollinations/flux")
    capsys.readouterr()

    code = run("step", story_id, "assets", "--ep", "1", "--tier", "2", "--route", "api", "--estimate")

    out, err = capsys.readouterr()
    assert code == 0, err
    assert ran == []
    profile = store.get(story_id)["generation_profile"]
    assert (profile["tier"], profile["route"]) == (2, "api")
    assert (f"⚙️ Generation profile: tier 2, route api, consistency {profile['consistency_mode']}, "
            f"budget {profile['budget_profile']}") in out
    estimate = json.loads(out[out.index("{"):])
    assert (estimate["step"], estimate["video"]["route"], estimate["video"]["link"]) == ("assets", "api", tce.SEEDANCE)
    assert estimate["video"]["count"] >= 1 and estimate["video"]["est_usd"] > 0


def test_no_animate_and_fill_failed_with_motion_reach_the_steps_params(cli, store, tmp_path):
    run, ran = cli
    story_id = tas._episode(store, tmp_path)

    assert run("step", story_id, "assets", "--ep", "1", "--no-animate") == 1
    assert run("step", story_id, "render", "--ep", "1", "--fill-failed-with-motion") == 1
    assert run("render", story_id, "--ep", "1", "--fill-failed-with-motion") == 1
    assert run("step", story_id, "render", "--ep", "1") == 1

    assert ran == [("assets", 1, {"animate": False}), ("render", 1, {"fill_failed_with_motion": True}),
                   ("render", 1, {"fill_failed_with_motion": True}), ("render", 1, {})]
    assert run("step", story_id, "render", "--ep", "1", "--no-animate") == 2  # assets only
