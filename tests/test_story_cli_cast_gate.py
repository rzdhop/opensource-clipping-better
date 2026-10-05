"""The CLI's cast gate checks the full cast cost before anything is bought
(plan 23 A5 follow-up, RC-V6, DEC-283).

``python main.py --ai-story step <id> cast`` asks ``workflow.generation_budget``
-- the one sum of the portraits and the sheet edits that the API's estimate
and gate check -- before the step runs. A v2 story whose portraits fit but
whose portraits plus sheet edits go over a cap is refused before any LLM call
or portrait, in one plain sentence; a legacy story keeps DEC-117's
stop-and-ask before its edits (the step's, never the gate's).

The harness is ``tests/test_story_cli.py``'s ``studio``: hermetic, the fakes
registered in the one adapter table, no request leaving the process. The fal
image and edit fakes are priced as the real links are (``pricing.estimate``),
so the runner would book their dollars. Every key here is a test value.
Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

import test_story_cli as sc
from test_story_cli import cli, studio  # noqa: F401  (fixtures)
from test_story_look import _d1, _d2

NOON = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def day(studio):  # noqa: F811
    """``spend.json`` under ``tmp_path`` with the day frozen at noon."""
    from clipping.providers import budget

    spend = budget.DailySpend(os.environ["SPEND_PATH"], time_fn=lambda: NOON)
    studio.cli.monkeypatch.setattr(budget, "_DEFAULT_SPEND", spend)
    return spend


def _priced(s):
    """A priced fal image fake beside the studio's fal editor fake, priced too."""
    from clipping.providers import generation, pricing

    def priced(link, request):
        return pricing.estimate(link, 1, width=request.width, height=request.height)

    s.fakes.fal_t2i = sc.FakeImage()
    s.cli.monkeypatch.setitem(generation._ADAPTERS, ("image", "fal"), s.fakes.fal_t2i)
    for fake in (s.fakes.fal_t2i, s.fakes.editor):
        s.cli.monkeypatch.setattr(fake, "estimate", priced)


def _stories(s):
    from clipping.aistory import store as story_store

    return story_store.StoryStore(s.cli.outputs, on_log=lambda line: None)


def _gate_budget(s, story_id, *, env):
    """``workflow.generation_budget`` on the cast of CAST_ARGS, as the gate asks it."""
    from clipping.aistory import workflow

    stories = _stories(s)
    story = stories.get(story_id)
    units = workflow.cast_units(stories, story, selected=["Kiwilo", "Mangella"],
                                custom=[{"name": "Figuette", "role": "support",
                                         "one_line": "Une figue timide qui voit tout."}])
    images = workflow.image_verdict(stories, story, units["images"], env=env) if units["images"] else None
    edit = (workflow.edit_readiness(stories, story, env=env, qty=units["edit_images"])
            if units["edit_images"] else None)
    return units, workflow.generation_budget(stories, story, units, images, edit, env=env)


def test_v2_cast_over_the_daily_cap_with_its_edits_is_refused_before_any_portrait(studio, day):  # noqa: F811
    """$3.70 spent of $4.00: the 3 portraits ($0.12) fit, the 6 sheet edits
    ($0.24) fit, together ($0.36) they do not -- refused before any LLM call
    or image, in the gate's plain sentence (never a structured payload)."""
    s = studio
    story_id = sc._styled(s)
    _stories(s).update(story_id, lambda doc: doc["generation_profile"].update(
        pipeline="v2", budget_profile="quality"), now="2026-10-04T12:00:00+00:00")
    for name, value in (("FAL_KEY", "test-fal-key"), ("ALLOW_PAID", "1"), ("DAILY_CAP_USD", "4.00")):
        s.cli.monkeypatch.setenv(name, value)
    _priced(s)
    day.add(3.70)
    # Every reply the cast would need is queued: only the gate keeps them unasked.
    runner = s.llm(K1=[sc.K1_KIWI, sc.K1_MANGO, sc.K1_FIG], D1=[_d1("Né sur la plage.")] * 3,
                   D2=[_d2(175), _d2(160), _d2(150)])

    code = s.run("step", story_id, "cast", *sc.CAST_ARGS)

    out = s.capsys.readouterr()
    assert (s.fakes.fal_t2i.requests, s.fakes.t2i.requests, s.fakes.editor.requests) == ([], [], [])
    assert code == 1, out
    assert out.err.strip() == (
        "The 3 reference images and 6 edits would go over a cap, so nothing would be generated or spent: "
        "refused: est $0.360 on 3 reference images and 6 edits would bring today to $4.06 of the $4.00 "
        "daily cap.")
    assert "{" not in out.err + out.out and "budget_daily_cap" not in out.err + out.out
    assert runner.calls == [] and s.entity_ids(story_id, "characters") == []
    assert day.today_total() == pytest.approx(3.70)

    units, budget = _gate_budget(s, story_id, env={})
    assert (units["images"], units["edit_images"]) == (3, 6)
    assert budget["blocks"] is True and budget["refusal"]["cap"] == "day"
    assert budget["usd"] == pytest.approx(0.36)


def test_a_legacy_cast_whose_edits_the_budget_refuses_still_runs_and_stops_at_the_sheets(studio, day):  # noqa: F811
    """The guard: a legacy story whose paid editor the story cap refuses is
    not blocked by the gate -- its free portraits are drawn and its sheets
    wait for an editor (DEC-117's stop-and-ask), exactly as before."""
    s = studio
    story_id = sc._styled(s)
    for name, value in (("FAL_KEY", "test-fal-key"), ("ALLOW_PAID", "1"), ("PER_STORY_CAP_USD", "0.02")):
        s.cli.monkeypatch.setenv(name, value)
    _priced(s)

    units, budget = _gate_budget(s, story_id, env={})
    assert (units["images"], units["edit_images"]) == (3, 6)
    assert budget["blocks"] is False and budget["edit_refused"] is False

    s.llm(K1=[sc.K1_KIWI, sc.K1_MANGO, sc.K1_FIG])
    code = s.run("step", story_id, "cast", *sc.CAST_ARGS)

    out = s.capsys.readouterr().out
    assert code == 0
    assert s.entity_ids(story_id, "characters") == sorted(sc.IDS)
    assert (len(s.fakes.t2i.requests), s.fakes.fal_t2i.requests, s.fakes.editor.requests) == (3, [], [])
    for name in ("Kiwilo", "Mangella", "Figuette"):
        assert f"👤 {name}: still missing turnaround, expressions sheet (waiting for an editor)" in out
    assert sc.PROMPT_ONLY_HINT in out
    assert day.today_total() == 0.0
