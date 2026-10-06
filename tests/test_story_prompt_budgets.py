"""Each v2 prompt fills its own link's budget (AI Story phase 7 follow-up,
stage F2; the human, 2026-10-02: "More prompt context is more accuracy and
details that make the story good. ... some providers have limited prompt
size so check that also").

Stage F1 (``clipping.providers.prompt_limits``) knows each link's limit and
refuses an over-limit prompt at dispatch. Here the builders take their word
budget from the link the request goes to (``clipping.aistory.prompt_budgets``:
the link's own, bounded by a quality ceiling per kind; today's number when no
link is known), the storyboard builds every shot to its episode's planned
links, a prompt that cannot fit even on the ladder's last rung is refused
naming the shot and the link (never sent cut or over the limit), and the
assets step checks each stored prompt against the link it actually uses.

Offline and hermetic (the assets step's fixtures); stdlib + pytest
(DEC-012). The new behaviour is reached inside the tests, so on the parent
commit each fails on its own.
"""

from __future__ import annotations

import pytest

from clipping.aistory import prompting, shots, templates
from clipping.providers import prompt_limits

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_shots as tss
import test_story_shots_crowded as crowded
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_clip_estimate import timings_path  # noqa: F401 - the measured clip timings under tmp_path

NOW = tas.NOW
SEEDREAM_EDIT, NANO, FLUX_CF = "fal/seedream-4.5-edit", "gemini/nano-banana-2-lite", "cloudflare/flux-1-schnell"
VEO, SEEDANCE = "gemini/veo-3.1-lite", "fal/seedance-1-pro-fast"
KLING, LTX = "fal/kling-2.5-turbo-std", "fal/ltx-2.3-fast"
NO_LIVE = {}  # the table alone, whatever a real data/provider_limits.json holds
GEMINI = {"GEMINI_PAID_API_KEY": "test-gemini-paid-key"}


def _live(label, chars):
    """A live fal read publishing *chars* for *label* (``prompt_limits.limit_for``'s *live*)."""
    return {label: {"status": prompt_limits.PUBLISHED, "prompt_max_chars": chars, "endpoint": label,
                    "read_at": "2026-10-02T00:00:00+00:00"}}


# ================================================== the budget of each kind

def test_each_kind_takes_its_links_budget_bounded_by_its_ceiling_and_todays_number_without_a_link():
    """``prompt_budgets``: a known link gives ``prompt_limits.budget_words``
    bounded by the kind's quality ceiling; no link gives the number every v2
    prompt was built to until now; a live limit smaller than the ceiling
    wins over it."""
    from clipping.aistory import prompt_budgets as pb

    # The keyframe: seedream's 3000 chars (461 words) and nano-banana's 8192 tokens are both over the ceiling.
    assert pb.keyframe_words(SEEDREAM_EDIT, live=NO_LIVE) == pb.KEYFRAME_CEILING_WORDS == 320
    assert pb.keyframe_words(NANO, live=NO_LIVE) == 320
    assert pb.keyframe_words(None) == prompting.KEYFRAME_V2_MAX_WORDS == 220
    assert pb.keyframe_words(FLUX_CF, live=NO_LIVE) == 315
    assert prompt_limits.budget_words(FLUX_CF, default=0, live=NO_LIVE) == 315
    assert pb.keyframe_words(SEEDREAM_EDIT, live=_live(SEEDREAM_EDIT, 400)) == 61
    # The clip: every hosted video link is over the ceiling; the ambience clip adds the brief's share.
    assert pb.clip_words(VEO, live=NO_LIVE) == pb.CLIP_CEILING_WORDS == 160
    assert pb.clip_words(SEEDANCE, live=NO_LIVE) == pb.clip_words(KLING, live=NO_LIVE) == 160
    assert pb.clip_words(None) == prompting.CLIP_V2_MAX_WORDS == 80
    assert prompting.CLIP_AUDIO_SHARE_WORDS == prompting.CLIP_AUDIO_MAX_WORDS - prompting.CLIP_V2_MAX_WORDS == 60
    assert pb.clip_audio_words(VEO, live=NO_LIVE) == pb.CLIP_AUDIO_CEILING_WORDS == 220
    assert pb.clip_audio_words(None) == prompting.CLIP_AUDIO_MAX_WORDS == 140
    assert pb.clip_audio_words(SEEDANCE, live=_live(SEEDANCE, 650)) == 100
    # The sheets, plates and props on seedream get more room than their fixed caps: the sheet up to its
    # ceiling; the plate and the prop the link's own (plan 26 H1 dropped their ceilings, no hash on them).
    seedream = "fal/seedream-4.5"
    assert pb.sheet_words(seedream, live=NO_LIVE) == pb.SHEET_CEILING_WORDS == 200 > prompting.SHEET_V2_MAX_WORDS
    assert pb.plate_words(seedream, live=NO_LIVE) == pb.prop_words(seedream, live=NO_LIVE) == 461
    assert pb.plate_words(seedream, live=_live(seedream, 650)) == 100 < prompting.PLATE_V2_MAX_WORDS
    assert not hasattr(pb, "PLATE_CEILING_WORDS") and not hasattr(pb, "PROP_CEILING_WORDS")
    assert (pb.sheet_words(None), pb.plate_words(None), pb.prop_words(None)) == (130, 150, 80)
    # Plan 29 stage 4 (DEC-308 point 4, amending DEC-247), re-pinned on purpose: an element with its written
    # description gets 100 words more -- the sheet's ceiling 300, the fixed numbers 230 / 250 / 180 / 360 -- and
    # the plate and the prop stay the link's own; the numbers above (no description) are unchanged.
    assert pb.sheet_words(seedream, live=NO_LIVE, described=True) == pb.SHEET_DESCRIBED_CEILING_WORDS == 300
    assert (pb.sheet_words(None, described=True), pb.plate_words(None, described=True),
            pb.prop_words(None, described=True), pb.two_view_words(None, described=True)) == (230, 250, 180, 360)
    assert (prompting.SHEET_V2_DESCRIBED_MAX_WORDS, prompting.PLATE_V2_DESCRIBED_MAX_WORDS,
            prompting.PROP_V2_DESCRIBED_MAX_WORDS) == (230, 250, 180)
    assert pb.plate_words(seedream, live=NO_LIVE, described=True) == 461
    # The pair the shots module is given: the numbers and the links they came from.
    budgets = pb.for_links(SEEDREAM_EDIT, VEO, live=NO_LIVE)
    assert budgets == prompting.Budgets(320, 160, SEEDREAM_EDIT, VEO)
    assert pb.for_links() == prompting.Budgets() == prompting.Budgets(220, 80, None, None)
    # A ceiling never exceeds what any link of the quality preset accepts (words at 6.5 characters).
    for label in (SEEDREAM_EDIT, NANO):
        assert prompt_limits.budget_words(label, default=0, live=NO_LIVE) >= pb.KEYFRAME_CEILING_WORDS
    for label in (VEO, SEEDANCE, KLING, LTX):
        assert prompt_limits.budget_words(label, default=0, live=NO_LIVE) >= pb.CLIP_AUDIO_CEILING_WORDS


def test_every_v2_builder_takes_its_budget():
    """The pure builders keep today's output by default and fill a larger
    budget when given one: a long clip motion is cut at 80 words and kept
    whole at 160; the audio brief keeps a long visual whole at 220; a sheet,
    a plate and a prop keep more of the style at their ceilings."""
    style = templates.load_style("fruit_drama")
    motion = ("the anthropomorphic kiwi lifts the coconut telephone to his ear, listens, frowns, then slowly lowers "
              "it while the mango leans in from the right, both of them turning toward the curtain as it stirs, "
              "the kiwi's free hand reaching for the chain at his neck")
    args = dict(subject="the anthropomorphic kiwi and the anthropomorphic mango", motion=motion,
                camera_phrase=prompting.CAMERA_PHRASES["push_in"], secondary="the mango reacts with a small movement")
    short = prompting.layered_clip_prompt(style, **args)
    wide = prompting.layered_clip_prompt(style, budget=160, **args)
    assert short == prompting.layered_clip_prompt(style, budget=prompting.CLIP_V2_MAX_WORDS, **args)
    assert len(short.split()) <= 80 < len(wide.split()) <= 160
    assert "reaching for the chain at his neck" in wide and "reaching for the chain at his neck" not in short

    visual = ", ".join(f"the mango turns slowly toward window number {i}" for i in range(20)) + "."
    place = "a cramped kitchen with checkered tiles (night, rain against the window)"
    narrow = prompting.clip_prompt_with_audio(visual, place=place, sfx=["door slam"], speakers=["the mango"])
    roomy = prompting.clip_prompt_with_audio(visual, place=place, sfx=["door slam"], speakers=["the mango"], budget=220)
    assert len(narrow.split()) <= 140 < len(roomy.split()) <= 220
    assert "window number 19" in roomy and "window number 19" not in narrow
    assert roomy.endswith(prompting.AUDIO_CLOSING) and "Sound effects: door slam." in roomy

    import test_aistory_prompting as tap
    sheet = prompting.portrait_prompt_v2(style, look_text=tap.LOOK_TEXT, signature_items=tap.SIGNATURE_ITEMS)
    sheet_wide = prompting.portrait_prompt_v2(style, look_text=tap.LOOK_TEXT, signature_items=tap.SIGNATURE_ITEMS,
                                              budget=200)
    assert len(sheet.split()) <= 130 < len(sheet_wide.split()) <= 200
    assert sheet_wide.endswith(prompting.CONSTRAINTS_ONE_CHARACTER)
    place_text = ("a luxurious turquoise swimming pool surrounded by white wooden loungers and glowing tiki torches. "
                  "Layout: on the left a row of loungers, on the right the tiki bar, at the back the villa's terrace, "
                  "in the centre the pool. Light: warm golden hour light raking across the water. Scale: a pool "
                  "twenty people long. Set dressing: coconut shell telephone (brown coconut, fits in one hand).")
    plate = prompting.plate_prompt_v2(style, place_text=place_text, variant="golden_hour")
    plate_wide = prompting.plate_prompt_v2(style, place_text=place_text, variant="golden_hour", budget=220)
    assert len(plate.split()) <= 150 < len(plate_wide.split()) <= 220
    assert "twenty people long" in plate_wide
    prop_text = "a coconut shell telephone with glowing tropical flower buttons, carved coconut shell, brown and pink"
    prop = prompting.prop_prompt_v2(style, prop_text=prop_text)
    prop_wide = prompting.prop_prompt_v2(style, prop_text=prop_text, budget=120)
    assert len(prop.split()) <= 80 < len(prop_wide.split()) <= 120
    assert prop_wide.endswith(prompting._CONSTRAINTS_OBJECT)


# ========================================== the shots built to their links

def _links(image_link=SEEDREAM_EDIT, video_link=VEO, *, live=NO_LIVE):
    from clipping.aistory import prompt_budgets as pb

    return pb.for_links(image_link, video_link, live=live)


def test_a_crowded_keyframe_keeps_its_full_roles_and_looks_on_a_link_with_room():
    """The crowded shot of test_story_shots_crowded ran down the ladder to
    fit 220 words (compact roles, short props); built for seedream (320) it
    keeps the full role sentences, and never exceeds the link's budget."""
    plan, scene, entities = crowded._crowded()
    resolved = shots.resolve_shot(plan, scene=scene, entities=entities, style_lock=tss.CARTOON_FLAT,
                                  consistency_mode="references", v2=True, budgets=_links())
    prompt = resolved["image_prompt"]
    assert prompt.startswith("Image 1 is the tall yellow geometric cylinder's reference")
    assert 220 < len(prompt.split()) <= 320, len(prompt.split())
    assert prompt_limits.fits(SEEDREAM_EDIT, prompt, live=NO_LIVE)[0]
    assert prompt.endswith(prompting.CONSTRAINTS_KEYFRAME)
    # Without budgets: today's shape, under 220.
    before = crowded._resolve(plan, scene, entities)["image_prompt"]
    assert before.startswith("Reference images: 1 ") and len(before.split()) <= 220
    # The clip prompt on Veo: inside its 160 words, and fitting the link.
    assert len(resolved["video_prompt"].split()) <= 160
    assert prompt_limits.fits(VEO, resolved["video_prompt"], live=NO_LIVE)[0]


@pytest.mark.parametrize("image_link,video_link", [
    (SEEDREAM_EDIT, VEO), (NANO, SEEDANCE), (SEEDREAM_EDIT, KLING), (FLUX_CF, LTX), (None, None),
])
def test_every_built_v2_prompt_fits_its_planned_link(image_link, video_link):
    """The gap F1 left: ``gating.link_summary`` could call a link runnable
    for a prompt the dispatch would refuse. Built to its links' budgets, a
    v2 storyboard's every keyframe prompt and clip prompt ``fits()`` them
    and stays inside its budget (the default when no link is known)."""
    template_v2 = templates.load_episode_template("serial_60s_v2")
    script = tss._v2_script()
    plans = tss._v2_plans()
    budgets = _links(image_link, video_link)
    doc, _notes = shots.build_storyboard(script, plans, {sid: "t1" for sid in plans}, entities=tss.ENTITIES,
                                         style_lock=tss.FRUIT_DRAMA, template=template_v2, language=tss.EN,
                                         consistency_mode="references", now=NOW, v2=True,
                                         shots_per_scene=template_v2["shots_per_scene"], budgets=budgets)
    for shot in doc["shots"]:
        assert len(shot["image_prompt"].split()) <= budgets.keyframe, shot["shot_id"]
        assert len(shot["video_prompt"].split()) <= budgets.clip, shot["shot_id"]
        if image_link:
            assert prompt_limits.fits(image_link, shot["image_prompt"], live=NO_LIVE)[0], shot["shot_id"]
        if video_link:
            assert prompt_limits.fits(video_link, shot["video_prompt"], live=NO_LIVE)[0], shot["shot_id"]
    refreshed = shots.refresh_prompts(doc, script, entities=tss.ENTITIES, style_lock=tss.FRUIT_DRAMA,
                                      consistency_mode="references", v2=True, budgets=budgets)
    assert [s["image_prompt"] for s in refreshed["shots"]] == [s["image_prompt"] for s in doc["shots"]]


def test_a_prompt_that_cannot_fit_its_link_is_refused_naming_the_shot_and_the_link():
    """Past the ladder's last rung a keyframe prompt was sent anyway; now it
    is refused: ``shots.PromptOverBudget`` (a ValueError) from
    ``resolve_shot``, named with the shot and the link by
    ``build_storyboard`` and ``refresh_prompts``. A clip prompt whose fixed
    parts alone are over its budget is refused the same way."""
    plan, scene, entities = crowded._crowded()
    tiny = _links(live=_live(SEEDREAM_EDIT, 260))  # 40 words: under the shortest rung of a crowded shot
    assert tiny.keyframe == 40
    with pytest.raises(shots.PromptOverBudget) as caught:
        shots.resolve_shot(plan, scene=scene, entities=entities, style_lock=tss.CARTOON_FLAT,
                           consistency_mode="references", v2=True, budgets=tiny)
    assert caught.value.kind == "keyframe" and caught.value.budget == 40 and caught.value.words > 40
    assert "the shot's keyframe prompt cannot fit its link's budget of 40 words" in str(caught.value)
    assert isinstance(caught.value, ValueError)

    template_v2 = templates.load_episode_template("serial_60s_v2")
    script, plans = tss._v2_script(), tss._v2_plans()
    kwargs = dict(entities=tss.ENTITIES, style_lock=tss.FRUIT_DRAMA, template=template_v2, language=tss.EN,
                  consistency_mode="references", now=NOW, v2=True, shots_per_scene=template_v2["shots_per_scene"])
    with pytest.raises(shots.PromptOverBudget) as caught:
        shots.build_storyboard(script, plans, {sid: "t1" for sid in plans}, budgets=tiny, **kwargs)
    message = str(caught.value)
    assert message.startswith("shot sh01's keyframe prompt cannot fit fal/seedream-4.5-edit's budget of 40 words")
    assert "nothing was sent" in message and "prompt-limits" in message
    assert caught.value.shot_id == "sh01" and caught.value.link == SEEDREAM_EDIT

    doc, _notes = shots.build_storyboard(script, plans, {sid: "t1" for sid in plans}, budgets=_links(), **kwargs)
    with pytest.raises(shots.PromptOverBudget) as caught:
        shots.refresh_prompts(doc, script, entities=tss.ENTITIES, style_lock=tss.FRUIT_DRAMA,
                              consistency_mode="references", v2=True, budgets=tiny)
    assert str(caught.value).startswith("shot sh01's keyframe prompt cannot fit fal/seedream-4.5-edit's budget")

    # A clip budget under its fixed parts (the camera, the stays-still clause, the style's suffix).
    short_clip = _links(SEEDREAM_EDIT, VEO, live=_live(VEO, 100))
    assert short_clip.clip == 15 and short_clip.keyframe == 320
    with pytest.raises(shots.PromptOverBudget) as caught:
        shots.build_storyboard(script, plans, {sid: "t1" for sid in plans}, budgets=short_clip, **kwargs)
    assert str(caught.value).startswith("shot sh01's clip prompt cannot fit gemini/veo-3.1-lite's budget of 15 words")


# ======================================== the steps: planned links, checks

def _quality_story(store, tmp_path, *, mode="references"):
    """The assets step's story (episode 1 written, planned, approved) on the
    quality preset, tier 3, v2 -- the fixture test_story_ambience uses."""
    story_id = tas._episode(store, tmp_path, mode=mode)
    tce._tier(store, story_id, tier=3, budget_profile="quality")
    store.update(story_id, lambda doc: doc["generation_profile"].update(pipeline="v2"), now=NOW)
    return story_id


def _settings(**keys):
    import test_story_episode_steps as eps

    return dict(eps.SETTINGS, VIDEO_CHAIN=",".join((SEEDANCE, LTX, KLING, VEO)), **keys)


def test_the_episodes_budgets_come_from_its_planned_links(store, tmp_path, monkeypatch):
    """``clips.episode_budgets``: the keyframe's budget from the episode's
    image link -- its recorded ``links.image``, else the keyframe role
    chain's first link -- and the clip's from ``clips.planned_link``; a
    legacy story keeps the defaults (its prompts have no budget)."""
    from clipping.aistory import workflow
    from clipping.aistory.steps import clips

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))  # no live limit of the machine
    story_id = _quality_story(store, tmp_path)
    ec = tas._ec(store, story_id)
    budgets = clips.episode_budgets(ec, _settings(**tas.FAL, **GEMINI))
    assert budgets == prompting.Budgets(320, 220 - 60, SEEDREAM_EDIT, VEO)
    assert clips.episode_budgets(ec, _settings(**tas.FAL)).video_link == SEEDANCE

    settings = tas._settings(VIDEO_CHAIN=",".join((SEEDANCE, LTX, KLING, VEO)), ALLOW_PAID="1", **tas.FAL, **GEMINI)
    workflow.patch_assets(store, story_id, 1, {"links": {"image": NANO, "video": KLING}}, now=tce.LATER, env=settings)
    recorded = clips.episode_budgets(tas._ec(store, story_id), _settings(**tas.FAL, **GEMINI),
                                     assets_doc=tas._assets_doc(store, story_id))
    assert (recorded.image_link, recorded.video_link) == (NANO, KLING)

    legacy = tas._episode(store, tmp_path)
    assert clips.episode_budgets(tas._ec(store, legacy), _settings(**tas.FAL, **GEMINI)) == prompting.Budgets()


def test_the_storyboard_step_builds_to_the_planned_links_and_refuses_what_cannot_fit(store, tmp_path, monkeypatch):
    """The fast build of a v2 episode's storyboard writes prompts built to
    the planned links (seedream's 320 words for the keyframes: a crowded
    shot keeps its full roles); with a live limit too small for the shots,
    the step fails naming the shot and the link, and writes nothing."""
    import test_story_episode_steps as eps
    from clipping.aistory import steps

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    story_id = _quality_story(store, tmp_path)
    m = eps._new()
    board = m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=eps.Log())
    assert all(len(shot["image_prompt"].split()) <= 320 for shot in board["shots"])
    assert all(prompt_limits.fits(SEEDREAM_EDIT, shot["image_prompt"])[0] for shot in board["shots"])
    rev = board["rev"]

    prompt_limits.record_live(_live(SEEDREAM_EDIT, 260))
    with pytest.raises(steps.StepFailed) as caught:
        m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=eps.Log())
    message = str(caught.value)
    # Walk follow-up F5: building fast again plans every scene again, so the
    # first shot is a new one, numbered after the board's highest id.
    first = f"sh{max(shots.shot_number(shot['shot_id']) for shot in board['shots']) + 1:02d}"
    assert f"shot {first}'s keyframe prompt cannot fit fal/seedream-4.5-edit's budget of 40 words" in message
    assert tas._board(store, story_id)["rev"] == rev


def test_the_assets_step_refuses_a_stored_prompt_the_link_cannot_take(store, tmp_path, monkeypatch):
    """A stored v2 prompt built to another link, or whose link's limit moved
    since: ``assets.request_parts`` says why it cannot be sent to the link
    it would go to now (the F1 dispatch check stays the backstop); so does
    ``clips.clip_request_parts`` for the clip prompt, whose ambience brief
    takes its share of the link's budget rather than a fixed 140 words."""
    import test_story_episode_steps as eps
    from clipping.aistory.steps import assets, clips

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    story_id = _quality_story(store, tmp_path)
    eps._new().storyboard.build_fast(store, story_id, 1, now=NOW, on_log=eps.Log())
    tas._approve(store, story_id)
    ec = tas._ec(store, story_id)
    script = eps._script(store, story_id)
    shot = next(shot for shot in tas._board(store, story_id)["shots"] if shot["lines"])

    fine = assets.request_parts(ec, shot, note=None, link=SEEDREAM_EDIT)
    assert fine["over"] is None
    # The link's limit moved under the stored prompt: refused, with what to do.
    prompt_limits.record_live(_live(SEEDREAM_EDIT, 260))
    over = assets.request_parts(ec, shot, note=None, link=SEEDREAM_EDIT)
    assert over["over"].startswith(f"shot {shot['shot_id']}'s keyframe prompt")
    assert "fal/seedream-4.5-edit accepts 260 characters" in over["over"] and "refresh the prompts" in over["over"]
    assert over["hash"] == fine["hash"]  # what decides "current" never moves with the link
    prompt_limits.record_live({SEEDREAM_EDIT: {"status": prompt_limits.NOT_PUBLISHED, "endpoint": SEEDREAM_EDIT}})
    # A stored prompt over the budget but under the limit (built for a roomier link): resolved again to this
    # link's budget and sent (DEC-249 -- until it, refused); the hash stays the stored prompt's. A user's override
    # is sent as written.
    long_prompt = dict(shot, image_prompt=" ".join(["word"] * 330))
    fitted = assets.request_parts(ec, long_prompt, note=None, link=SEEDREAM_EDIT)
    assert fitted["over"] is None and fitted["prompt"] == shot["image_prompt"]
    assert fitted["refit"] == {"from": 330, "to": len(shot["image_prompt"].split()), "note": 0, "budget": 320}
    assert fitted["hash"] != fine["hash"] and fitted["hash"] == assets.request_parts(
        ec, long_prompt, note=None, link=None)["hash"]
    assert assets.request_parts(ec, dict(shot, prompt_override=" ".join(["word"] * 330)), note=None,
                                link=SEEDREAM_EDIT)["over"] is None
    assert assets.request_parts(ec, long_prompt, note=None, link=None)["over"] is None

    flags = {"keep_still": False, "animate": False, "keep_native_audio": False}
    parts = clips.clip_request_parts(ec, shot, script, tier=3, flags=flags, link=VEO)
    assert parts["over"] is None and len(parts["prompt"].split()) <= 220
    assert parts["hash"] == clips.clip_prompt_hash(parts["prompt"], parts["negative"], native_audio=True)
    # The brief's share: a long visual keeps more of itself on Veo (220) than with no link known (140).
    long_visual = dict(shot, video_prompt=", ".join(f"the kiwi turns toward window {i}" for i in range(30)) + ".")
    roomy = clips.clip_request_parts(ec, long_visual, script, tier=3, flags=flags, link=VEO)["prompt"]
    narrow = clips.clip_request_parts(ec, long_visual, script, tier=3, flags=flags)["prompt"]
    assert 140 < len(roomy.split()) <= 220 and len(narrow.split()) <= 140
    assert roomy.endswith(prompting.AUDIO_CLOSING)
    prompt_limits.record_live(_live(VEO, 100))
    refused = clips.clip_request_parts(ec, shot, script, tier=3, flags=flags, link=VEO)
    assert refused["over"].startswith(f"shot {shot['shot_id']}'s clip prompt")
    assert "gemini/veo-3.1-lite" in refused["over"]
