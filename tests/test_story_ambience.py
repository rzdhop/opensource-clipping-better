"""Tier-3 ambience (AI Story phase 7 follow-up, stage E; the human's choice,
2026-10-02): every clip brings its own ambience and sound effects -- the
video model's sound is used for AMBIENCE + SFX ONLY, never for dialogue
(each character keeps its pinned TTS voice in every shot) -- on Veo 3.1
lite (``gemini/veo-3.1-lite``, billed on ``GEMINI_PAID_API_KEY``).

This file holds the parts that need no ffmpeg:

- the budget profile's values (``tier3_native_audio: ambience``,
  ``video_link_policy: first_with_audio``) and their validation;
- which story is in ambience mode (``media_policy.ambience``: v2, tier 3,
  its profile says ``ambience``) and the clip link it buys on: the first
  keyed link whose clips always carry sound, else the first keyed link,
  silent, said in the estimate -- never a silent switch.

The new behaviour is reached inside the tests, so on the parent commit each
test fails on its own. Offline and hermetic (the assets step's fixtures);
stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import json

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_clip_estimate import timings_path  # noqa: F401 - the measured clip timings under tmp_path

NOW = tas.NOW
SEEDANCE = "fal/seedance-1-pro-fast"
LTX = "fal/ltx-2.3-fast"
KLING = "fal/kling-2.5-turbo-std"
VEO = "gemini/veo-3.1-lite"
# The shipped default VIDEO_CHAIN's hosted links, in its order.
CHAIN = ",".join((SEEDANCE, LTX, KLING, VEO))
GEMINI = {"GEMINI_PAID_API_KEY": "test-gemini-paid-key"}


def _story(store, tmp_path, *, tier=3, v2=True, budget_profile="quality"):
    """The assets step's story (episode 1 written, planned, approved) at
    *tier* on *budget_profile*, on the v2 pipeline when *v2*."""
    story_id = tas._episode(store, tmp_path)
    tce._tier(store, story_id, tier=tier, budget_profile=budget_profile)
    if v2:
        store.update(story_id, lambda doc: doc["generation_profile"].update(pipeline="v2"), now=NOW)
    return story_id


def _video(store, story_id, **keys):
    settings = tas._settings(VIDEO_CHAIN=CHAIN, ALLOW_PAID="1", PER_EPISODE_CAP_USD="40", **keys)
    return tce._units(store, story_id, settings)["video"]


def _profiles_file(tmp_path, **quality):
    """A copy of the shipped budget profiles with *quality*'s keys over the
    quality profile's."""
    from clipping.providers import budget as budget_mod

    data = budget_mod.load_profiles()
    data["profiles"]["quality"].update(quality)
    path = tmp_path / "budget_profiles.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# =================================================== the profile's values

def test_the_quality_profile_buys_clips_with_sound_and_keeps_their_ambience():
    """The shipped quality profile: ``tier3_native_audio: ambience`` (the
    clip's sound under the dialogue, never in place of it) and
    ``video_link_policy: first_with_audio``; the one-dollar profile keeps
    the cheapest link."""
    from clipping.aistory.steps import clips
    from clipping.providers import budget as budget_mod

    quality = budget_mod.profile_settings("quality")
    assert (quality["tier3_native_audio"], quality["video_link_policy"]) == ("ambience", "first_with_audio")
    assert budget_mod.profile_settings("one_dollar")["video_link_policy"] == "cheapest_available"
    assert budget_mod.TIER3_AUDIO_MODES == ("opt_in", "ambience")
    assert budget_mod.VIDEO_LINK_POLICIES == clips.LINK_POLICIES == (
        "cheapest_available", "first_in_chain", "first_with_audio")


@pytest.mark.parametrize("key,value", [("tier3_native_audio", "dialogue"), ("tier3_native_audio", True),
                                       ("video_link_policy", "loudest"), ("video_link_policy", None)])
def test_a_profile_with_an_unknown_audio_mode_or_link_policy_is_refused(tmp_path, key, value):
    from clipping.providers import budget as budget_mod

    path = _profiles_file(tmp_path, **{key: value})
    with pytest.raises(ValueError) as caught:
        budget_mod.load_profiles(str(path))
    assert key in str(caught.value) and "quality" in str(caught.value)


def test_a_profile_may_name_each_known_audio_mode_and_link_policy(tmp_path):
    from clipping.providers import budget as budget_mod

    for mode in budget_mod.TIER3_AUDIO_MODES:
        for policy in budget_mod.VIDEO_LINK_POLICIES:
            path = _profiles_file(tmp_path, tier3_native_audio=mode, video_link_policy=policy)
            assert budget_mod.load_profiles(str(path))["profiles"]["quality"]["video_link_policy"] == policy


# ====================================================== ambience mode

def test_ambience_mode_is_a_v2_tier_3_story_whose_profile_says_ambience(monkeypatch, tmp_path):
    """Only a v2 story at tier 3 on a profile whose ``tier3_native_audio``
    is ``ambience``: tier 1 and 2 never keep a clip's sound, a legacy story
    keeps today's opt-in, and a profile at ``opt_in`` keeps today's
    behaviour (the clip's sound in place of a shot's lines, per shot)."""
    from clipping.aistory import defaults, media_policy
    from clipping.providers import budget as budget_mod

    def story(**profile):
        base = {"tier": 3, "route": "api", "consistency_mode": "references", "budget_profile": "quality",
                "pipeline": defaults.PIPELINE_V2}
        base.update(profile)
        return {"generation_profile": {key: value for key, value in base.items() if value is not None}}

    assert media_policy.ambience(story()) is True
    assert media_policy.ambience(story(tier=2)) is False
    assert media_policy.ambience(story(tier=1)) is False
    assert media_policy.ambience(story(pipeline=None)) is False
    assert media_policy.ambience(story(budget_profile="one_dollar")) is False
    assert media_policy.ambience(None) is False

    monkeypatch.setattr(budget_mod, "PROFILES_PATH", str(_profiles_file(tmp_path, tier3_native_audio="opt_in")))
    assert media_policy.ambience(story()) is False


def test_the_new_story_preset_is_tier_3_so_its_clips_keep_their_ambience():
    """The quality preset a new story gets with the keys (phase 7, DEC-221)
    is tier 3 now -- "animated + model sound" -- so its clips bring their
    ambience (the human's choice, 2026-10-02); still v2, still every shot a
    clip (``media_policy.fully_animated``)."""
    from clipping.aistory import defaults, media_policy

    profile = defaults.quality_generation_profile()
    assert profile == {"tier": 3, "route": "api", "consistency_mode": "references", "budget_profile": "quality",
                       "pipeline": "v2"}
    story = {"generation_profile": profile}
    assert media_policy.ambience(story) and media_policy.fully_animated(story)


# ======================================================== the clip link

def _row(link, status="keyed", price=0.05):
    return {"link": link, "status": status, "reason": "keyed" if status == "keyed" else "no key",
            "price_per_second": price if status == "keyed" else None}


def test_first_with_audio_picks_the_first_keyed_link_whose_clips_always_carry_sound():
    """``pick_hosted``: with sound wanted, the first keyed link whose clips
    always carry sound (Veo), past cheaper silent ones and past LTX (its
    sound only when asked); none keyed: the first keyed link, as
    ``first_in_chain`` -- the caller says the clips are silent. Without
    sound wanted (tier 2, a legacy story) it is ``first_in_chain``."""
    from clipping.aistory.steps import clips

    rows = [_row(SEEDANCE, price=0.022), _row(LTX, price=0.06), _row(KLING, price=0.042), _row(VEO)]
    assert clips.pick_hosted(rows, clips.FIRST_WITH_AUDIO, want_sound=True)["link"] == VEO
    assert clips.pick_hosted(rows, clips.FIRST_WITH_AUDIO)["link"] == SEEDANCE
    assert clips.pick_hosted(rows, clips.FIRST)["link"] == SEEDANCE
    assert clips.pick_hosted(rows, clips.CHEAPEST, want_sound=True)["link"] == SEEDANCE
    unkeyed = rows[:3] + [_row(VEO, status="skipped")]
    assert clips.pick_hosted(unkeyed, clips.FIRST_WITH_AUDIO, want_sound=True)["link"] == SEEDANCE
    assert clips.pick_hosted([_row(VEO, status="skipped")], clips.FIRST_WITH_AUDIO, want_sound=True) is None


def test_an_ambience_story_buys_its_clips_on_veo_when_its_key_is_set(store, tmp_path):
    """Fail-first. A v2 tier-3 quality story, FAL_KEY and GEMINI_PAID_API_KEY
    set, the shipped chain's order (seedance first): every shot's clip is
    planned on Veo at its price, and the estimate says the clips bring
    their own ambience and sound effects under the dialogue."""
    from clipping.providers import pricing

    story_id = _story(store, tmp_path)
    video = _video(store, story_id, **tas.FAL, **GEMINI)

    assert video["link"] == VEO and video["price_per_second"] == pricing.PRICES[VEO].usd
    assert video["ambience"] == {"sound": True, "note": None}
    assert "own ambience and sound effects" in video["message"] and "No ambience" not in video["message"]
    assert "rendered as at tier 2" not in video["message"]


def test_without_the_veo_key_the_clips_fall_back_to_the_first_keyed_link_and_say_there_is_no_ambience(
        store, tmp_path):
    """No GEMINI_PAID_API_KEY: the first keyed link (seedance, no sound),
    and the estimate says so -- no ambience, and the key that brings it --
    never a silent switch. The plan stays ready: the clips are still made."""
    story_id = _story(store, tmp_path)
    video = _video(store, story_id, **tas.FAL)

    assert video["link"] == SEEDANCE and video["ready"] is True
    assert video["ambience"]["sound"] is False
    note = video["ambience"]["note"]
    assert note.startswith("No ambience:") and "GEMINI_PAID_API_KEY" in note and VEO in note
    assert note in video["message"]
    # Not the opt-in's sentence (A-108): no shot's lines were ever to be replaced.
    assert "rendered as at tier 2" not in video["message"]


def test_an_episode_whose_clips_are_on_a_silent_link_keeps_it_and_says_why_there_is_no_ambience(store, tmp_path):
    """The sticky link (DEC-204, A-087) still holds per episode: an episode
    whose clips are recorded on seedance stays there with the Veo key set,
    and the estimate says the episode has no ambience and why."""
    from clipping.aistory import workflow

    story_id = _story(store, tmp_path)
    settings = tas._settings(VIDEO_CHAIN=CHAIN, ALLOW_PAID="1", **tas.FAL, **GEMINI)
    workflow.patch_assets(store, story_id, 1, {"links": {"video": SEEDANCE}}, now=tce.LATER, env=settings)

    video = _video(store, story_id, **tas.FAL, **GEMINI)
    assert (video["link"], video["source"]) == (SEEDANCE, "record")
    assert video["ambience"]["sound"] is False
    assert "keeps its clips on one link" in video["ambience"]["note"]


@pytest.mark.parametrize("tier,v2", [(2, True), (3, False)])
def test_guard_outside_ambience_mode_the_quality_profile_still_buys_the_first_link_in_the_chain(
        store, tmp_path, tier, v2):
    """Tier 2 (the clip's sound is discarded) and a legacy tier-3 story (the
    opt-in): ``first_with_audio`` is ``first_in_chain``, and the units carry
    no ``ambience`` key -- byte for byte the shape they had."""
    story_id = _story(store, tmp_path, tier=tier, v2=v2)
    video = _video(store, story_id, **tas.FAL, **GEMINI)
    assert video["link"] == SEEDANCE and "ambience" not in video
    if tier == 3:
        assert "rendered as at tier 2" in video["message"]  # A-108's opt-in sentence, as it was


# ================================================= the audio brief (prompt)
#
# An ambience story's clip prompt says what the shot sounds like -- the
# place's ambience, the shot's sound effects -- and that nobody is heard: Veo
# takes no negative prompt (A-103), so the prompt ends on "no music, no
# voices, nobody speaks or sings, no narration". A character who talks is
# still shown (its TTS line plays over the shot), speaking silently. All of
# it inside one word budget, never appended past it.

CLOSING = "no music, no voices, nobody speaks or sings, no narration."
VISUAL = ("The mango: leans across the table and says \"Give it back\" while pointing at the phone. The cylinder "
          "reacts with a small natural movement. Slow push-in toward the subject. The set, the lighting and every "
          "character's look stay exactly as in the first frame. Smooth cartoon motion.")


def test_the_audio_brief_says_the_places_ambience_the_shots_effects_and_that_no_one_is_heard():
    from clipping.aistory import prompting

    prompt = prompting.clip_prompt_with_audio(
        VISUAL, place="a cramped kitchen with checkered tiles (night, rain against the window)",
        sfx=["door slam", "heartbeat"], speakers=["the mango"])

    assert prompt.startswith("The mango: leans across the table")
    assert "Sound: the natural ambience of a cramped kitchen with checkered tiles (night, rain against the window)." \
        in prompt
    assert "Sound effects: door slam, heartbeat." in prompt
    assert "The mango speaks silently: their words are not heard." in prompt
    assert prompt.endswith(CLOSING)
    # The words are never asked for: the quote is gone, the verb made silent.
    assert "Give it back" not in prompt and '"' not in prompt and "says silently" in prompt
    assert len(prompt.split()) <= prompting.CLIP_AUDIO_MAX_WORDS <= 140


def test_the_audio_brief_fits_the_budget_however_long_the_visual_and_the_place_are():
    """The closing and the silence are never cut; a long visual is cut at a
    clause boundary to leave the sound its room; the place is cut to what is
    left; no sound effect, no speaker: those sentences are simply absent."""
    from clipping.aistory import prompting

    long_visual = ", ".join(f"the mango turns slowly toward window number {i}" for i in range(40)) + "."
    long_place = ", ".join(f"shelf {i} full of jars" for i in range(30))
    prompt = prompting.clip_prompt_with_audio(long_visual, place=long_place, sfx=[], speakers=[],
                                              note="Make it snappier")
    assert len(prompt.split()) <= prompting.CLIP_AUDIO_MAX_WORDS
    assert prompt.endswith(CLOSING) and "Make it snappier." in prompt
    assert "Sound: the natural ambience of shelf 0 full of jars" in prompt
    assert "Sound effects" not in prompt and "silently" not in prompt

    two = prompting.clip_prompt_with_audio("Two fruits argue by the pool.", place="a pool", sfx=["splash"],
                                           speakers=["the mango", "the cylinder"])
    assert "The mango and the cylinder speak silently: their words are not heard." in two


@pytest.mark.parametrize("text,expected", [
    ('The mango says "Give it back" and points.', "The mango says silently and points."),
    ("The cylinder whispers to the mango, then shouts.", "The cylinder whispers silently to the mango, then shouts "
                                                          "silently."),
    ("They talk while the music plays.", "They talk silently while the music plays."),
    ("The mango speaks silently.", "The mango speaks silently."),
    ("The mango turns away.", "The mango turns away."),
])
def test_speech_in_the_visual_is_made_silent(text, expected):
    from clipping.aistory import prompting

    assert prompting.silent_speech(text) == expected


def test_build_video_prompt_puts_the_brief_inside_the_clip_prompt_and_nothing_else_moves():
    """``video_plan.build_video_prompt`` with an audio brief: the v2 shot's
    stored clip prompt, the note, then the sound -- the speech cue of the
    opt-in is never added; without a brief, the prompt is today's."""
    from clipping.aistory import prompting, video_plan

    style = {"motion_rules": {"tier2_prompt_suffix": "Smooth cartoon motion."}, "negative_prompt": "blurry"}
    shot = {"shot_id": "sh01", "camera_motion": "push_in", "modifiers": [], "action": "x", "video_prompt": VISUAL,
            "negative_prompt": ""}
    audio = {"place": "a pool (day)", "sfx": ["splash"], "speakers": ["the mango"]}
    prompt, negative = video_plan.build_video_prompt(shot, style, tier=3, note="Slower", audio=audio)
    assert prompt == prompting.clip_prompt_with_audio(VISUAL, note="Slower", **audio)
    assert "The character says" not in prompt and negative == video_plan.build_video_prompt(shot, style, tier=3)[1]
    assert video_plan.build_video_prompt(shot, style, tier=3, note="Slower") == (
        f"{VISUAL[:-1]}. Slower", negative)
    with pytest.raises(ValueError):
        video_plan.build_video_prompt(shot, style, tier=3, lines=("Hello",), audio=audio)
    with pytest.raises(ValueError):
        video_plan.build_video_prompt(shot, style, tier=2, audio=audio)


def _board_shot(store, story_id, scene_id):
    return next(shot for shot in tas._board(store, story_id)["shots"] if shot["scene_id"] == scene_id)


def test_an_ambience_storys_clip_request_carries_its_shots_sound_brief_and_asks_for_sound(store, tmp_path):
    """Fail-first. ``clips.clip_request_parts`` on a v2 tier-3 quality story:
    the shot's place (its descriptor and time of day), its scene's sound
    effects (a cue at the scene's start on the shot holding its first line,
    a cue at a line on the shot holding that line), its speakers in frame
    made silent, the closing; ``native_audio`` asked (a link whose sound is
    optional makes it); a ``keep_native_audio`` pin never voices the lines
    (the lines stay TTS); the hash moves with the brief."""
    from clipping.aistory import shots as shots_mod
    from clipping.aistory.steps import clips
    import test_story_episode_steps as eps

    story_id = _story(store, tmp_path)
    ec = tas._ec(store, story_id)
    script = eps._script(store, story_id)
    scene = next(scene for scene in script["scenes"] if scene["sfx_cues"])
    shot = _board_shot(store, story_id, scene["scene_id"])
    place = ec.entities["places"][scene["place_id"]]
    flags = {"keep_still": False, "animate": False, "keep_native_audio": True}

    parts = clips.clip_request_parts(ec, shot, script, tier=3, flags=flags)
    prompt = parts["prompt"]
    sound = prompt.split("Sound:", 1)[1].lower()
    assert f"ambience of {place['descriptor'].lower()} ({scene['time_variant'].replace('_', ' ')})" in sound
    # The fixture's cues are a stinger and a crowd's gasp: never asked of a clip ("no music, no voices").
    cues = {cue["cue"]: cue["at"] for cue in scene["sfx_cues"]}
    assert set(cues) <= clips.CLIP_SFX_EXCLUDED and "Sound effects" not in prompt
    from clipping.aistory import schemas
    assert clips.CLIP_SFX_EXCLUDED <= {cue for pack in schemas.SFX_PACKS.values() for cue in pack}
    scene["sfx_cues"] = [{"at": at, "cue": "door_slam" if cue == "dramatic_sting" else "heartbeat"}
                         for cue, at in cues.items()]
    with_effects = clips.clip_request_parts(ec, shot, script, tier=3, flags=flags)["prompt"]
    for cue in scene["sfx_cues"]:
        at = cue["at"]
        expected = (at == "start" and shot["lines"][:1] == [scene["lines"][0]["line_id"]]) or at in shot["lines"]
        assert (cue["cue"].replace("_", " ") in with_effects) is expected, (cue, shot["lines"])
    assert "Sound effects:" in with_effects
    handles = shots_mod.character_handles(ec.entities["characters"])
    speaking = {line["speaker"] for line in scene["lines"] if line["line_id"] in shot["lines"]}
    tagged = {tag[1:].split(":")[0] for tag in shot["subject_tags"] if tag.startswith("@")}
    assert speaking & tagged
    for char_id in speaking & tagged:
        assert handles[char_id].lower() in sound and "silently: their words are not heard" in sound
    assert prompt.endswith(CLOSING) and "The character says" not in prompt
    assert parts["native_audio"] is True
    assert parts["hash"] == clips.clip_prompt_hash(prompt, parts["negative"], native_audio=True)


def test_guard_outside_ambience_mode_the_clip_request_is_todays(store, tmp_path):
    """Tier 2 (and a legacy tier-3 story's opt-in): no brief, the prompt the
    parent commit built, ``native_audio`` only for the opt-in's pin."""
    from clipping.aistory import video_plan
    from clipping.aistory.steps import clips
    import test_story_episode_steps as eps

    story_id = _story(store, tmp_path, tier=2)
    ec = tas._ec(store, story_id)
    script = eps._script(store, story_id)
    shot = next(shot for shot in tas._board(store, story_id)["shots"] if shot["lines"])
    flags = {"keep_still": False, "animate": False, "keep_native_audio": True}
    parts = clips.clip_request_parts(ec, shot, script, tier=2, flags=flags)
    assert parts["prompt"] == video_plan.build_video_prompt(shot, ec.style_lock, tier=2)[0]
    assert parts["native_audio"] is False and "Sound:" not in parts["prompt"]

    legacy = _story(store, tmp_path, tier=3, v2=False)
    ec = tas._ec(store, legacy)
    parts = clips.clip_request_parts(ec, shot, eps._script(store, legacy), tier=3, flags=flags)
    assert parts["native_audio"] is True and "The character says" in parts["prompt"] and "Sound:" not in \
        parts["prompt"]


# ================================================ shot length vs the link
#
# Veo sells 4, 6 and 8 s clips; a v2 shot runs 5-12 s (serial_60s_v2). A
# story that animates every shot plans no beat shot longer than the longest
# clip its planned link sells (the T1 v2 plan's max = min(template max, the
# link's longest clip)): a scene past it is two shots. A storyboard planned
# before (or for another link) is planned again where a scene is short of
# beat shots; a shot that is still longer is refused by the clip estimate,
# naming it and how to fix it, instead of buying a clip that cannot cover it.

LONG_SCENES = ("s04", "s07")


def _v2_storyboard_story(store, *, tier=3):
    """A written v2 episode on serial_60s_v2, fully animated (the quality
    profile at *tier*), its prop drawn, two of its scenes (:data:`LONG_SCENES`)
    spoken at more length: ready for the storyboard step."""
    import test_story_episode_steps as eps
    import test_story_storyboard_props as tsp
    from clipping.aistory.steps import episode_common

    story_id = eps._written_script(store)

    def v2(doc):
        doc["generation_profile"].update(pipeline="v2", tier=tier, budget_profile="quality", route="api")
        doc.update(episode_template_id="serial_60s_v2")

    store.update(story_id, v2, now=NOW)
    script = eps._script(store, story_id)
    script["template_id"] = "serial_60s_v2"
    # Two scenes spoken at more length (9-10 s): past Veo's 8 s, inside the template's 12 s.
    longer = "Tu crois vraiment que je vais te suivre jusqu'au bout de cette île maudite, sans rien dire du tout ?"
    for scene in script["scenes"]:
        if scene["scene_id"] in LONG_SCENES:
            scene["lines"][0]["text"] = longer
    ec = episode_common.load_context(store, story_id, 1)
    episode_common.retime(script, ec)
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    tsp._plant_image(store, story_id, "props", eps.PHONE)
    return story_id


def _plan_settings(**keys):
    import test_story_episode_steps as eps

    return dict(eps.SETTINGS, VIDEO_CHAIN=CHAIN, **keys)


def test_the_planned_link_is_the_episodes_or_the_profiles_keys_asked_then_aside(store, tmp_path):
    """``clips.planned_link``: what the storyboard plans its shots for, as
    the estimate would pick the link (calling nothing): the episode's
    recorded link; else the profile's policy among the keyed links; none
    keyed: among every hosted link, keys aside -- the link it will take
    once its key is set. ``longest_clip_s`` is its longest sellable clip."""
    from clipping.aistory import workflow
    from clipping.aistory.steps import clips

    story_id = _story(store, tmp_path)
    ec = tas._ec(store, story_id)
    assert clips.planned_link(ec, _plan_settings(**tas.FAL, **GEMINI)) == VEO
    assert clips.planned_link(ec, _plan_settings(**tas.FAL)) == SEEDANCE
    assert clips.planned_link(ec, _plan_settings()) == VEO
    assert (clips.longest_clip_s(VEO), clips.longest_clip_s(SEEDANCE), clips.longest_clip_s("local/comfyui")) == (
        8, 12, None)
    tier2 = _story(store, tmp_path, tier=2)
    assert clips.planned_link(tas._ec(store, tier2), _plan_settings(**tas.FAL, **GEMINI)) == SEEDANCE

    settings = tas._settings(VIDEO_CHAIN=CHAIN, ALLOW_PAID="1", **tas.FAL, **GEMINI)
    workflow.patch_assets(store, story_id, 1, {"links": {"video": KLING}}, now=tce.LATER, env=settings)
    assert clips.planned_link(tas._ec(store, story_id), _plan_settings(**tas.FAL, **GEMINI),
                              assets_doc=tas._assets_doc(store, story_id)) == KLING


def test_a_fully_animated_v2_storyboard_plans_no_shot_longer_than_its_links_longest_clip(store):
    """Fail-first. On Veo (8 s), T1 v2 is asked two beat shots for every
    scene past 8 s, one for the rest; on seedance (12 s) as before. The
    storyboard's shots then all fit their clip, and the length gate's
    total is the script's (the scenes keep their lengths)."""
    import test_story_episode_steps as eps
    import test_story_storyboard_props as tsp
    from clipping.aistory.steps import storyboard

    m = eps._new()
    story_id = _v2_storyboard_story(store)
    ec = storyboard.episode_common.load_context(store, story_id, 1)
    assert storyboard.max_shot_s(ec, _plan_settings(**tas.FAL, **GEMINI)) == 8
    assert storyboard.max_shot_s(ec, _plan_settings(**tas.FAL)) == 12

    llm = eps.FakeLLM(default={"T1v2": tsp.t1_v2_reply})
    eps._run(m.storyboard, store, story_id, llm=llm, step="storyboard", settings=_plan_settings(**tas.FAL, **GEMINI))

    script = eps._script(store, story_id)
    seconds = {sid: entry["duration_s"] for sid, entry in script["timing"]["scenes"].items()}
    assert any(8 < value <= 12 for value in seconds.values()), seconds  # the fixture reaches the new rule
    per_scene = {}
    for shot in eps._storyboard(store, story_id)["shots"]:
        per_scene[shot["scene_id"]] = per_scene.get(shot["scene_id"], 0) + 1
    for call, scene in zip(llm.calls, script["scenes"]):
        asked = 2 if seconds[scene["scene_id"]] > 8 else 1
        assert per_scene[scene["scene_id"]] == asked, (scene["scene_id"], seconds[scene["scene_id"]])
        assert ("exactly 2 entries" if asked == 2 else "exactly 1 entry") in call["user"]


def test_a_storyboard_planned_for_a_longer_clip_is_planned_again_where_a_scene_is_short_of_beat_shots(store):
    """Planned on seedance (12 s), then the Veo key is set: the storyboard
    estimate counts, and the step plans again, exactly the scenes past 8 s
    that have one beat shot; a complete storyboard on its own link makes no
    call (as before)."""
    import test_story_episode_steps as eps
    import test_story_storyboard_props as tsp
    from clipping.aistory import workflow

    m = eps._new()
    story_id = _v2_storyboard_story(store)
    llm = eps.FakeLLM(default={"T1v2": tsp.t1_v2_reply})
    eps._run(m.storyboard, store, story_id, llm=llm, step="storyboard", settings=_plan_settings(**tas.FAL))
    script = eps._script(store, story_id)
    seconds = {sid: entry["duration_s"] for sid, entry in script["timing"]["scenes"].items()}
    short = sorted(sid for sid, value in seconds.items() if 8 < value <= 12)
    assert short

    again = eps.FakeLLM(default={"T1v2": tsp.t1_v2_reply})
    eps._run(m.storyboard, store, story_id, llm=again, step="storyboard", settings=_plan_settings(**tas.FAL))
    assert again.calls == []
    ec = workflow.episode_context(store, store.get(story_id), 1, step="storyboard")
    assert workflow.storyboard_units(ec, env=_plan_settings(**tas.FAL))["scenes"] == []
    units = workflow.storyboard_units(ec, env=_plan_settings(**tas.FAL, **GEMINI))
    assert sorted(units["scenes"]) == short and units["t1_calls"] == len(short)

    eps._run(m.storyboard, store, story_id, llm=again, step="storyboard",
             settings=_plan_settings(**tas.FAL, **GEMINI))
    assert len(again.calls) == len(short)
    board = eps._storyboard(store, story_id)
    for sid in short:
        assert sum(1 for shot in board["shots"] if shot["scene_id"] == sid) == 2


def _too_long_units(store, story_id, board, **keys):
    from clipping.aistory.steps import assets
    import test_story_episode_steps as eps

    settings = tas._settings(VIDEO_CHAIN=CHAIN, ALLOW_PAID="1", PER_EPISODE_CAP_USD="40", **keys)
    return assets.asset_units(tas._ec(store, story_id), eps._script(store, story_id), board, env=settings,
                              adapters=tce._adapters())


def test_the_clip_estimate_refuses_a_shot_longer_than_the_links_longest_clip_and_says_how_to_fix_it(
        store, tmp_path):
    """Fail-first. A fully animated story on Veo: a shot of 10.5 s cannot be
    one 8 s clip even slowed (DEC-250: at most 1.25x, 10 s) -- the estimate
    names it, its length and the link's longest clip, says to plan the
    storyboard again, and is not ready; the assets step refuses before any
    call (keyframes included). A shot a few frames past 8 s keeps DEC-208's
    held last frame (``held_s``); one of 9.4 s is covered by its clip slowed
    (re-pinned on purpose from the refusal, DEC-250)."""
    import copy

    from clipping.aistory.steps import assets

    story_id = _story(store, tmp_path)
    board = copy.deepcopy(tas._board(store, story_id))
    long_shot, close_shot, slowed_shot = board["shots"][1], board["shots"][2], board["shots"][3]
    long_shot["duration_s"], close_shot["duration_s"], slowed_shot["duration_s"] = 10.5, 8.3, 9.4

    units = _too_long_units(store, story_id, board, **tas.FAL, **GEMINI)
    video = units["video"]
    assert video["link"] == VEO and video["ready"] is False and units["ready"] is False
    sentence = video["too_long"]
    assert f"{long_shot['shot_id']} (10.5 s)" in sentence and "8 s" in sentence and VEO in sentence
    assert "even slowed (at most 10 s)" in sentence
    assert close_shot["shot_id"] not in sentence and slowed_shot["shot_id"] not in sentence
    assert "plan the storyboard again" in sentence and sentence in video["message"]
    rows = {row["shot_id"]: row for row in video["plan"]}
    assert rows[close_shot["shot_id"]]["held_s"] == pytest.approx(0.3) and "cover" not in rows[close_shot["shot_id"]]
    assert rows[slowed_shot["shot_id"]]["cover"] == "stretch" and rows[slowed_shot["shot_id"]]["stretch"] == 1.175
    refusal = assets.plan_refusal(tas._ec(store, story_id), units)
    assert refusal and sentence in refusal and "Nothing was generated or spent" in refusal

    # The same storyboard on seedance (12 s): nothing is too long, nothing slowed.
    fine = _too_long_units(store, story_id, board, **tas.FAL)["video"]
    assert fine["link"] == SEEDANCE and "too_long" not in fine
    assert not any(row.get("cover") for row in fine["plan"])


def test_guard_a_story_that_does_not_animate_every_shot_keeps_the_held_last_frame(store, tmp_path):
    """DEC-208 unchanged where a still may stand in: a key-shots story's
    long shot is planned at the longest clip and held on its last frame."""
    import copy

    story_id = _story(store, tmp_path, budget_profile="one_dollar")
    board = copy.deepcopy(tas._board(store, story_id))
    board["shots"][1]["duration_s"] = 30.0
    video = _too_long_units(store, story_id, board, **tas.FAL)["video"]
    assert "too_long" not in video
    held = [row for row in video["plan"] if row.get("held_s")]
    assert all(row["shot_id"] == board["shots"][1]["shot_id"] for row in held)


# ===================================================== caps and the preset
#
# About $3.3-3.5 a 60 s episode on Veo (keyframes on fal, clips at $0.05 a
# second with their sound): the episode cap goes to $4, the default caps to
# 4 / 12 / 40 (the 1:3:10 ratio kept; the human's saved Settings override
# them), and the preset is priced on the audio link once its key is set.

def test_the_default_caps_are_4_12_40_and_the_quality_profile_caps_an_episode_at_4():
    from clipping.providers import budget as budget_mod

    caps = (budget_mod.PER_EPISODE_CAP_USD, budget_mod.DAILY_CAP_USD, budget_mod.PER_STORY_CAP_USD)
    assert caps == (4.0, 12.0, 40.0) and caps[1] == 3 * caps[0] and caps[2] == 10 * caps[0]
    assert budget_mod.budget_from_env({}) == budget_mod.Budget(False, 4.0, 12.0, 40.0, "free")
    assert budget_mod.budget_from_env({"PER_EPISODE_CAP_USD": "2"}).per_episode_cap_usd == 2.0  # Settings win
    assert budget_mod.profile_settings("quality")["cap_usd"] == 4.0


def test_the_preset_is_priced_on_veo_with_its_sound_when_its_key_is_set():
    """Fail-first. FAL_KEY and GEMINI_PAID_API_KEY set: the preset's clips
    on Veo (720p, $0.05 a second, the template's shots rounded to Veo's
    lengths), its keyframes on fal; the whole episode inside the quality
    profile's $4 cap; the summary says each clip brings its own ambience;
    the keys it needs are said, each with what it is for."""
    from clipping.aistory import media_policy, templates, video_plan
    from clipping.aistory import defaults
    from clipping.providers import budget as budget_mod
    from clipping.providers import pricing

    merged = {**tas.FAL, **GEMINI}
    estimate = media_policy.preset_estimate(merged)
    episode = estimate["episode"]
    template = templates.load_episode_template(defaults.EPISODE_TEMPLATE_ID_V2)
    assert episode["video_link"] == VEO and episode["price_per_second"] == pricing.PRICES[VEO].usd
    assert episode["billed_seconds"] == episode["shots"] * video_plan.requested_seconds(
        VEO, template["target_s"] / episode["shots"])
    assert estimate["episode_usd"] == pytest.approx(episode["billed_seconds"] * pricing.PRICES[VEO].usd
                                                    + episode["shots"] * episode["keyframe_usd"])
    assert 3.0 < estimate["episode_usd"] <= budget_mod.profile_settings("quality")["cap_usd"]
    assert estimate["ambience"] is True
    assert "each clip brings its own ambience and sound effects" in estimate["summary"]
    assert estimate["keys"] == list(media_policy.QUALITY_KEYS)
    assert [(row["key"], row["set"]) for row in estimate["keys_needed"]] == [
        ("FAL_KEY", True), ("GEMINI_PAID_API_KEY", True)]
    assert "images" in estimate["keys_needed"][0]["for"] and "sound" in estimate["keys_needed"][1]["for"]
    assert "GEMINI_PAID_API_KEY for clips with their own sound" in estimate["assumptions"]
    assert "test-gemini-paid-key" not in repr(estimate)


def test_without_the_veo_key_the_preset_is_priced_on_the_silent_link_and_says_what_brings_the_sound():
    from clipping.aistory import media_policy

    estimate = media_policy.preset_estimate({**tas.FAL})
    assert estimate["episode"]["video_link"] == SEEDANCE and estimate["ambience"] is False
    assert estimate["summary"].endswith("no ambience: add GEMINI_PAID_API_KEY for Veo's sound")
    assert [(row["key"], row["set"]) for row in estimate["keys_needed"]] == [
        ("FAL_KEY", True), ("GEMINI_PAID_API_KEY", False)]

    offer = media_policy.new_story_offer({**tas.FAL, "GEMINI_PAID_API_KEY": ""})
    assert offer["quality"] is True and offer["missing_keys"] == []
    assert offer["sound_missing_keys"] == ["GEMINI_PAID_API_KEY"]
    assert offer["estimate"]["episode"]["video_link"] == SEEDANCE
    keyed = media_policy.new_story_offer({**tas.FAL, **GEMINI})
    assert keyed["sound_missing_keys"] == [] and keyed["estimate"]["episode"]["video_link"] == VEO


def test_the_weak_host_advice_names_both_keys_and_what_each_is_for():
    from clipping.aistory import hardware

    text = hardware.recommendations_for("cpu_only")[0]["install_hint"]
    assert "Add FAL_KEY in Settings for the images" in text
    assert "GEMINI_PAID_API_KEY for clips with their own sound (gemini/veo-3.1-lite)" in text
