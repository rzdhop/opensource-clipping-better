"""Plan 33 stage 4, the "no still" rule at the MCP boundary: every shot of an
episode is a video clip, never a still with camera motion.

- ``story_create`` without a preset or a profile makes a fully animated story
  (own_gpu, tier 3, the v2 pipeline), never the store's tier-1 default; a
  tier under 2, or a budget profile that does not animate every shot, is a
  tool error naming the ones that do -- with or without a preset.
- ``story_options`` lists only the budget profiles that animate every shot.
- ``story_patch`` refuses the same tier and budget profiles.
- ``story_step_start``'s assets run refuses ``animate: false`` with the
  step's own sentence (the keyframe hold is the pause); the tool
  descriptions never offer a still-based option.

Through the in-process MCP client of ``tests/test_mcp_server.py`` (its
fixtures); skipped where ``fastmcp`` is not installed.
"""

import asyncio

import pytest

pytest.importorskip("fastmcp")

from fastmcp import Client  # noqa: E402

from test_mcp_server import backend, call, payload  # noqa: E402,F401 -- the fake backend and the client
from mcp_server.server import build_server  # noqa: E402

ANIMATED = ["quality", "native_speech", "native_speech_manual", "own_gpu"]


def _error(result) -> str:
    assert result.is_error, result
    return result.content[0].text


def _descriptions(server) -> dict:
    async def go():
        async with Client(server) as client:
            return {tool.name: tool.description or "" for tool in await client.list_tools()}
    return asyncio.run(go())


def test_a_story_made_without_a_preset_is_fully_animated_on_the_own_gpu():
    from clipping.aistory import media_policy
    from mcp_server import story_tools

    profile = story_tools.animated_profile()
    assert (profile["tier"], profile["budget_profile"], profile["pipeline"]) == (3, "own_gpu", "v2")
    assert media_policy.fully_animated({"generation_profile": profile}) is True


def test_story_create_defaults_to_clips_and_refuses_a_still_profile(backend):
    server = build_server(backend)
    plain = payload(call(server, "story_create", language="en", seed_text="A kiwi."))
    profile = plain["generation_profile"]
    assert plain["recipe"] is None
    assert (profile["tier"], profile["budget_profile"], profile["pipeline"]) == (3, "own_gpu", "v2")
    # A caller's key replaces the default's one by one, the rest stays animated.
    api = payload(call(server, "story_create", language="en", generation_profile={"budget_profile": "quality"}))
    assert (api["generation_profile"]["tier"], api["generation_profile"]["budget_profile"]) == (3, "quality")

    for sent in ({"tier": 1}, {"budget_profile": "free"}, {"budget_profile": "one_dollar"},
                 {"tier": 1, "budget_profile": "own_gpu"}):
        message = _error(call(server, "story_create", language="en", generation_profile=sent))
        assert message.startswith("Every shot of an episode is a video clip, never a still with camera motion")
        assert message.endswith(f"{', '.join(ANIMATED)}.")
    message = _error(call(server, "story_create", language="en", preset="fruit_drama",
                          generation_profile={"budget_profile": "one_dollar"}))
    assert "budget_profile 'one_dollar' does not animate every shot" in message
    assert {story["story_id"] for story in payload(call(server, "story_list"))} == {plain["story_id"],
                                                                                   api["story_id"]}


def test_story_options_lists_only_the_profiles_that_animate_every_shot(backend):
    options = payload(call(build_server(backend), "story_options"))
    assert [profile["id"] for profile in options["budget_profiles"]] == ANIMATED


def test_story_patch_refuses_a_still_profile_and_takes_the_rest(backend):
    server = build_server(backend)
    sid = payload(call(server, "story_create", language="en", seed_text="A kiwi."))["story_id"]
    for sent in ({"tier": 1}, {"budget_profile": "free"}, {"budget_profile": "one_dollar"}):
        message = _error(call(server, "story_patch", story_id=sid, fields={"generation_profile": sent}))
        assert "Every shot of an episode is a video clip" in message
    story = payload(call(server, "story_get", story_id=sid))["story"]
    assert (story["generation_profile"]["tier"], story["generation_profile"]["budget_profile"]) == (3, "own_gpu")
    patched = payload(call(server, "story_patch", story_id=sid, fields={"generation_profile": {"tier": 2}}))
    assert patched["generation_profile"]["tier"] == 2
    assert payload(call(server, "story_patch", story_id=sid, fields={"title": "Frigo"}))["title"] == "Frigo"


def test_the_assets_run_refuses_animate_false_and_no_tool_offers_a_still(backend):
    from clipping.aistory.steps import assets

    server = build_server(backend)
    sid = payload(call(server, "story_create", language="en", seed_text="A kiwi."))["story_id"]
    run = payload(call(server, "story_step_start", story_id=sid, step="assets", ep=1, params={"animate": False}))
    assert run["state"] == "failed" and run["error"] == f"StepFailed: {assets.ANIMATE_OFF_REFUSAL}"

    descriptions = _descriptions(server)
    for name, text in descriptions.items():
        for still in ("fill_failed_with_motion", "moving still", "makes no clips", "animate (default", "keep_still",
                      "Ken Burns"):
            assert still not in text, (name, still)
    assert "shot:<ep>:<shot_id>:video" in descriptions["story_step_start"]
    assert "A missing or failed clip stops the render" in descriptions["story_step_start"]
    assert "Every shot of every episode is a video clip" in descriptions["story_create"]
