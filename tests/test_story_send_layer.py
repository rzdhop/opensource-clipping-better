"""The clip prompt's template at the send layer (plan 26 stage 3, H1).

On a v2 story the request a clip is sent with carries the master prompt and
the scene template before today's prompt -- the *core*, unchanged and last --
fitted to the link's whole-prompt words; the core alone is hashed, so a clip
made before stays current and nothing uploaded turns stale. A v1 story's
request is the one it always was (RC-Q1). A local Wan template is bounded by
its umT5 window.

Stdlib + pytest (DEC-012). No provider call.
"""

from __future__ import annotations

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_native_speech_plan as nsp
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_shot_modes import _current_api_clip, _speaking

NOW = tas.NOW
FAST = nsp.FAST


def _request(store, story_id, shot, *, link, template=None, tier=3):
    from clipping.aistory.steps import assets, clips

    ec = tas._ec(store, story_id)
    script = tas.eps._script(store, story_id)
    return assets.clip_request(ec, shot, script, link=link, template=template, clip_s=shot.get("clip_s") or 4,
                               seed=7, note=None, flags=clips.shot_flags(shot, None), tier=tier, out_dir="")


def _with_image(store, story_id, shot):
    """*shot* with a keyframe on disk (a v1 shot's request names it)."""
    name = f"shot_{shot['shot_id'][2:]}.png"
    path = tas.Path(store.episode_asset_path(story_id, 1, "shots", name, create=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tas.PNG)
    return dict(shot, assets=dict(shot["assets"], image=f"assets/shots/{name}"))


def test_a_made_clip_stays_current_and_its_request_carries_the_master_before_the_core(store):
    """Fail-first. The request's prompt starts with the master (SERIES) and
    ends with the hashed core; the hash is the core's, as before; the clip
    made with it is still current."""
    from clipping.aistory.steps import clips

    story_id = nsp.planned_story(store)
    shot_id = _speaking(store, story_id, 1)[0]
    shot = _current_api_clip(store, story_id, shot_id)
    ec = tas._ec(store, story_id)
    script = tas.eps._script(store, story_id)
    flags = clips.shot_flags(shot, None)

    parts, request = _request(store, story_id, shot, link=FAST)
    assert request.prompt.startswith("SERIES:")
    assert request.prompt.endswith(parts["prompt"]) and request.prompt != parts["prompt"]
    assert parts["sent"]["text"] == request.prompt and parts["sent"]["limit"] is not None
    assert parts["sent"]["words"] <= parts["sent"]["limit"]
    # The hash is the core's own, as the clip was recorded with.
    assert parts["hash"] == clips.clip_prompt_hash(parts["prompt"], parts["negative"],
                                                   native_audio=parts["native_audio"])
    assert parts["hash"] == shot["assets"]["clip"]["prompt_hash"]
    image_sha = shot["assets"]["clip"]["image_sha256"]
    assert clips.clip_state(ec, shot, script, link=FAST, tier=3, flags=flags, image_sha=image_sha) == "current"


def test_a_v1_story_s_request_is_its_core_byte_for_byte(store, tmp_path):
    story_id = tas._episode(store, tmp_path)
    tce._tier(store, story_id, budget_profile="quality")
    shot = _with_image(store, story_id, tas._board(store, story_id)["shots"][0])
    parts, request = _request(store, story_id, shot, link=tce.SEEDANCE, tier=2)
    assert request.prompt == parts["prompt"]
    assert parts["sent"] == {"text": parts["prompt"], "words": len(parts["prompt"].split()),
                             "full_words": len(parts["prompt"].split()), "limit": None, "dropped": []}


def test_a_230_word_link_gets_a_fitted_prompt_that_still_ends_with_the_core(store):
    from clipping.aistory import prompt_budgets
    from clipping.providers import prompt_limits

    story_id = nsp.planned_story(store)
    board = tas._board(store, story_id)
    assert prompt_budgets.link_words(tce.SEEDANCE, live={}) == 230
    for shot in board["shots"]:
        parts, request = _request(store, story_id, shot, link=tce.SEEDANCE)
        sent = parts["sent"]
        assert sent["limit"] == prompt_budgets.link_words(tce.SEEDANCE)
        assert len(request.prompt.split()) <= sent["limit"]
        assert request.prompt.endswith(parts["prompt"])
        assert prompt_limits.fits(tce.SEEDANCE, request.prompt)[0]
        if sent["full_words"] > sent["limit"]:
            assert sent["dropped"] and sent["words"] < sent["full_words"]


def test_a_local_wan_template_is_bounded_by_its_umt5_window_and_other_local_links_are_not():
    from clipping.aistory import prompt_budgets
    from clipping.providers import prompt_limits

    for template in ("i2v_wan22_5b", "i2v_wan22_14b_lightning"):
        assert prompt_budgets.link_words("local/comfyui", template=template, live={}) == 315
    assert prompt_budgets.link_words("local/comfyui", template="i2v_ltx2", live={}) is None
    assert prompt_budgets.link_words("local/comfyui", live={}) is None
    assert prompt_limits.limit_for("local/comfyui", live={}) is None


def test_the_fit_line_names_the_link_the_words_and_what_was_dropped():
    from clipping.aistory.steps import assets

    line = assets._fit_line("sh04", "gemini/veo-3.1-fast",
                            {"limit": 630, "words": 618, "full_words": 812, "dropped": ["Chloe", "Sam", "the props"]})
    assert "gemini/veo-3.1-fast accepts 630 words: 812 → 618, dropped Chloe, Sam, the props" in line
    assert line.startswith("ℹ️ Shot sh04's clip prompt")
