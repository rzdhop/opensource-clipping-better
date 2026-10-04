"""The agent run's own card (plan 21 stage 3, DEC-270): ``AgentRunCard.jsx``,
shown on an agent-mode story's workspace only.

Text contracts over the sources (DEC-012: stdlib + pytest only, no JS
runner), in the style of ``tests/test_dashboard_new_story_format.py``: the
dashboard sources are read as text, never through npm or a JS runtime, so
this runs in CI (which installs pytest and nothing else).

The sub_step -> rail-key map (``storySteps.js``'s ``AGENT_PARTS``) is cross-
checked against ``story_fast_track.PARTS`` and ``story_fast_track.LABELS``
-- the runner's own literal -- so the two can never drift apart silently: a
part the runner renames or reorders fails this file first.
"""

from __future__ import annotations

import pathlib
import re

from clipping.aistory.steps import story_fast_track

ROOT = pathlib.Path(__file__).resolve().parents[1]
STORY_SRC = ROOT / "web" / "dashboard" / "src" / "pages" / "story"
CARD = STORY_SRC / "AgentRunCard.jsx"
STEPS = STORY_SRC / "storySteps.js"
WORKSPACE = STORY_SRC / "StoryWorkspace.jsx"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _agent_parts() -> list[dict]:
    src = _read(STEPS)
    match = re.search(r"export const AGENT_PARTS = \[(.*?)\n\]\n", src, re.DOTALL)
    assert match, "AGENT_PARTS not found in storySteps.js"
    entries = re.findall(
        r"\{ subStep: '([a-z_]+)', railKey: (null|'[a-z]+'), label: '([^']+)' \}", match.group(1))
    assert len(entries) == len(story_fast_track.PARTS)  # non-vacuity: every entry was read
    return [{"subStep": name, "railKey": None if rail == "null" else rail.strip("'"), "label": label}
            for name, rail, label in entries]


# ------------------------------------------------------- the sub_step -> rail map

def test_every_part_the_runner_names_is_pinned_in_order_with_its_own_label():
    parts = _agent_parts()
    assert [p["subStep"] for p in parts] == list(story_fast_track.PARTS)
    for part in parts:
        assert part["label"] == story_fast_track.LABELS[part["subStep"]], part


def test_places_proposal_drives_the_places_rail_step_and_episode_has_none():
    by_name = {p["subStep"]: p for p in _agent_parts()}
    assert by_name["places_proposal"]["railKey"] == "places"
    assert by_name["places"]["railKey"] == "places"
    assert by_name["episode"]["railKey"] is None
    # Every other part drives its own identically named rail step.
    for name in ("concepts", "bible", "style", "cast", "season", "knowledge"):
        assert by_name[name]["railKey"] == name


def test_step_of_job_reads_the_agent_run_jobs_sub_step():
    src = _read(STEPS)
    body = src.split("export function stepOfJob(job) {", 1)[1]
    assert "case 'story-fast-track': {" in body
    assert "agentPartOf(job.sub_step)" in body


# ------------------------------------------------------------- AgentRunCard.jsx

def test_the_card_asks_the_estimate_before_the_click_and_confirms_with_it():
    src = _read(CARD)
    assert "const AGENT_STEP = 'story-fast-track'" in src
    assert "fetchStoryEstimate(storyId, AGENT_STEP)" in src
    # The confirm dialog: the estimate's own message, the paid total, the caps line.
    assert "message: [estimate.message, paidTotal, estimate.caps_line].filter(Boolean).join('\\n')" in src
    assert "Total: est $" in src
    assert "confirmLabel: 'Run the agent'" in src
    assert "useConfirm" in src  # Cancel focused first: the kit's own rule, not reimplemented here.


def test_the_click_runs_the_step_with_no_parameters():
    src = _read(CARD)
    assert "await runStoryStep(storyId, AGENT_STEP, {})" in src


def test_the_running_card_names_the_part_from_sub_step():
    src = _read(CARD)
    assert "const part = jobRunning ? agentPartOf(job.sub_step) : null" in src
    assert "`Agent ${part ? `${part.number}/${part.total}: ${part.label}` : '…'}`" in src


def test_a_stop_shows_the_runners_last_line_and_offers_to_continue():
    src = _read(CARD)
    assert "const buttonLabel = stopped ? 'Continue the agent run' : 'Run the agent'" in src
    assert "job.error" in src


def test_episode_one_rendered_links_to_its_review_tab():
    src = _read(CARD)
    assert "const episode1 = (data.episodes || []).find((entry) => entry.ep === EPISODE)" in src
    assert "to={`/story/${storyId}/episodes/${EPISODE}#review`}" in src


# -------------------------------------------------------- StoryWorkspace.jsx

def test_studio_stories_render_nothing_new():
    src = _read(WORKSPACE)
    assert "import AgentRunCard from './AgentRunCard'" in src
    assert "story.generation_profile && story.generation_profile.mode === 'agent'" in src
    assert "{isAgentStory && <AgentRunCard storyId={storyId} data={data} onChange={refresh} />}" in src
