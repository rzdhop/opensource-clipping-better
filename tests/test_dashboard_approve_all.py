"""The "Approve all" buttons (plan 28 stage C, the human's ask, DEC-305 section 6).

A text contract over the dashboard sources (CI has no node, like the other
dashboard tests): the Cast and Places steps each carry one "Approve all" that
calls ``POST /stories/{id}/approve-all/{group}`` once and names what it left;
the episode studio's one "Approve all" approves the script, the storyboard,
the keyframes and the assets in that order through ``approveStoryDoc``, stops
at the first refusal and never sets ``approve_anyway`` by itself. Stdlib +
pytest (DEC-012).
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DASH = ROOT / "web" / "dashboard" / "src"
STORY = DASH / "pages" / "story"


def _read(path):
    return path.read_text(encoding="utf-8")


def test_the_cast_and_places_steps_each_have_an_approve_all_that_calls_the_group_route_once():
    api = _read(DASH / "api.js")
    start = api.index("export async function approveStoryGroup(")
    body = api[start:api.index("\n}\n", start)]
    assert "`/stories/${storyId}/approve-all/${group}`" in body and "method: 'POST'" in body

    button = _read(STORY / "ApproveAllGroup.jsx")
    assert "approveStoryGroup(storyId, group)" in button and "'Approve all'" in button
    assert button.count("approveStoryGroup(") == 1  # once per click, then the page refetches
    assert "onChange()" in button and "disabled={disabled || running}" in button
    # Plain words for what is left: "<name> still needs a portrait", never a code.
    assert "still needs" in button and "a portrait" in button and "a daytime picture" in button

    cast = _read(STORY / "steps" / "CastStep.jsx")
    assert '<ApproveAllGroup storyId={storyId} group="cast" disabled={busy} onChange={onChange} />' in cast
    places = _read(STORY / "steps" / "PlacesStep.jsx")
    assert '<ApproveAllGroup storyId={storyId} group="places" disabled={busy} onChange={onChange} />' in places


def test_the_episode_studio_approve_all_goes_in_order_stops_at_a_refusal_and_never_approves_anyway():
    src = _read(STORY / "episode" / "EpisodeApproveAll.jsx")
    order = [src.index(f"target: `{doc}:${{ep}}`") for doc in ("script", "storyboard")]
    order.append(src.index("keyframes.target || `keyframes:${ep}`"))
    order.append(src.index("target: `assets:${ep}`"))
    assert order == sorted(order)
    assert "'Approve all'" in src and "await approveStoryDoc(storyId, item.target)" in src
    assert "break" in src and "Stopped at the" in src and "<StepError" in src
    assert "approve_anyway" not in src
    studio = _read(STORY / "EpisodeStudio.jsx")
    assert "<EpisodeApproveAll storyId={storyId} ep={epNumber} episode={episode}" in studio
