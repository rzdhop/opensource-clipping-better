"""The prompt templates at the send layer (plan 26 stages 3 and 4, H1).

On a v2 story the request a clip is sent with carries the master prompt and
the scene template before today's prompt -- the *core*, unchanged and last --
fitted to the link's whole-prompt words; the core alone is hashed, so a clip
made before stays current and nothing uploaded turns stale. A v1 story's
request is the one it always was (RC-Q1). A local Wan template is bounded by
its umT5 window. A keyframe (stage 4a) is sent the image master and the
scene template before its core the same way, fitted to its link or -- none
recorded yet -- to its role chain's smallest bound; a user's
``prompt_override`` is sent as written.

Stdlib + pytest (DEC-012). No provider call.
"""

from __future__ import annotations

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_keyframe_consistency as kc
import test_story_keyframe_fix as kf
import test_story_native_speech_plan as nsp
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_keyframe_gate import unpaced  # noqa: F401 - the free Gemini tier's pacing lifted
from test_story_video_phase import timings_path  # noqa: F401 - the measured clip timings under tmp_path
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
    """Fail-first. The request's prompt starts with the master (SERIES, or
    the ART STYLE once the series line is the rung that went: the style's
    rules, now one section of their own, outlast it, plan 26 stage 4c) and
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
    assert request.prompt.startswith(("SERIES:", "ART STYLE:"))
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


# ================================================================ the keyframes (stage 4a)

def _spied(monkeypatch):
    """``assets.sent_image_prompt`` recording ``(shot_id, core, sent)`` of each call."""
    from clipping.aistory.steps import assets

    seen = []
    real = assets.sent_image_prompt

    def spy(ec, shot, parts, **kwargs):
        sent = real(ec, shot, parts, **kwargs)
        seen.append((shot["shot_id"], parts["prompt"], sent))
        return sent

    monkeypatch.setattr(assets, "sent_image_prompt", spy)
    return seen


def _requests(image):
    return {request.extra["name"]: request for request in image.requests}


def _roomy(monkeypatch, store, story_id, chars=8000):
    """A live read publishing *chars* for every link of the keyframe chain
    (fal raising its cap): room for the master on top of the core. The
    stored cores are built to the keyframe ceiling all the same (320 words
    under both caps), so nothing made moves."""
    from clipping.aistory.steps import assets
    from clipping.providers import gating, prompt_limits
    from clipping.providers import generation as gen

    ec = tas._ec(store, story_id)
    labels = assets._chain_labels(ec, gen.IMAGE_EDIT, gating.merged_env(kf.SETTINGS))
    assert labels
    live = {label: {"status": prompt_limits.PUBLISHED, "prompt_max_chars": chars, "endpoint": label,
                    "read_at": "2026-10-05T00:00:00+00:00"} for label in labels}
    monkeypatch.setattr(prompt_limits, "read_live", lambda path=None: live)
    return labels


def test_a_made_keyframe_stays_made_and_its_request_carries_the_image_master_before_the_core(store, tmp_path,
                                                                                               monkeypatch):
    """Fail-first. Each keyframe request starts with the image master and
    ends with the core ``request_parts`` built (its hash basis), fitted to
    the chain's smallest bound; every keyframe made is current after."""
    from clipping.aistory import prompt_budgets
    from clipping.aistory.steps import assets
    from clipping.providers import prompt_limits

    story_id = kf._quality(store, tmp_path)
    labels = _roomy(monkeypatch, store, story_id)
    seen = _spied(monkeypatch)
    image = kc.SeededImage(price=kf.PRICE)
    kf._run(store, story_id, image=image)

    requests = _requests(image)
    assert seen and len(seen) == len(requests)
    for shot_id, core, sent in seen:
        request = requests[f"shot_{shot_id[2:]}"]
        assert request.prompt == sent["text"]
        assert request.prompt.startswith(("SERIES:", "ART STYLE:"))
        assert request.prompt.endswith(core) and request.prompt != core
        assert sent["limit"] == prompt_budgets.chain_words(labels[:1] if request_link(store, story_id) else labels)
        assert sent["words"] <= sent["limit"]
        assert all(prompt_limits.fits(label, request.prompt)[0] for label in labels)
    ec = tas._ec(store, story_id)
    for shot in tas._shots(store, story_id):
        assert assets.shot_state(ec, shot) == "current"


def test_a_link_with_no_room_for_the_template_is_sent_the_core_alone(store, tmp_path):
    """The fit's last rung (stage 2): when even the never-dropped sections
    do not fit the link (the table's 3000 characters of Seedream's editor
    here), the core alone is sent -- the template adds no refusal."""
    from clipping.aistory.steps import assets
    from clipping.providers import prompt_limits

    story_id = kf._quality(store, tmp_path)
    ec = tas._ec(store, story_id)
    shot = tas._shots(store, story_id)[1]
    parts = assets.request_parts(ec, shot, note=None, link=None)
    label = "fal/seedream-4.5-edit"
    sent = assets.sent_image_prompt(ec, shot, parts, link=[label], live={})
    assert sent["full_words"] > sent["words"]
    if sent["text"] == parts["prompt"]:
        assert sent["dropped"]
    assert sent["text"].endswith(parts["prompt"]) and prompt_limits.fits(label, sent["text"], live={})[0]
    unbounded = assets.sent_image_prompt(ec, shot, parts, link="manual/upload")
    assert unbounded["limit"] is None and not unbounded["dropped"] and unbounded["text"].startswith("SERIES:")


def test_a_461_word_link_keeps_the_style_and_the_present_looks_before_the_core(store, tmp_path):
    """Fail-first (plan 26 stage 4c, B). Seedream's 3000 characters (461 words): the style's rules are the last
    rung, so the keyframe is sent the style (rendering and palette), each character in the shot, the place and
    the scene before its core, not the core alone -- the ART STYLE paragraph (rules included) and the core
    filled the cap before."""
    from clipping.aistory.steps import assets
    from clipping.providers import prompt_limits

    story_id = kf._quality(store, tmp_path)
    ec = tas._ec(store, story_id)
    shot = tas._shots(store, story_id)[1]
    parts = assets.request_parts(ec, shot, note=None, link=None)
    label = "fal/seedream-4.5-edit"
    sent = assets.sent_image_prompt(ec, shot, parts, link=[label], live={})

    assert sent["limit"] == 461 and sent["words"] <= 461 < sent["full_words"]
    assert sent["text"] != parts["prompt"] and sent["text"].endswith("\n\n" + parts["prompt"])
    assert sent["text"].startswith("ART STYLE: Medium: ")
    assert "Palette: saturated natural fruit colours" in sent["text"]
    assert "(in this shot) is an anthropomorphic character whose head is a whole" in sent["text"]
    assert "PLACE (in this shot):" in sent["text"] and "SCENE:" in sent["text"]
    assert "STYLE RULES" not in sent["text"] and "Character design rules" not in sent["text"]
    assert sent["dropped"][-1] == "style rules" and "art style" not in sent["dropped"]
    assert prompt_limits.fits(label, sent["text"], live={})[0]


def request_link(store, story_id):
    from clipping.aistory.steps import assets

    return assets.recorded_image_link(tas._assets_doc(store, story_id))


def test_a_keyframe_override_is_sent_as_written(store, tmp_path, monkeypatch):
    from clipping.aistory.steps import assets

    story_id = kf._quality(store, tmp_path)
    kf._run(store, story_id)
    board = tas._board(store, story_id)
    shot = next(item for item in board["shots"] if item["shot_id"] == "sh05")
    shot["prompt_override"] = "Kiwilo alone, lit from below, in the parlour."
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    seen = _spied(monkeypatch)
    image = kc.SeededImage(price=kf.PRICE)
    kf._run(store, story_id, image=image)

    assert list(_requests(image)) == ["shot_05"]
    ec = tas._ec(store, story_id)
    assert image.requests[0].prompt == assets.effective_prompt(shot, ec.entities) == seen[0][1]
    assert "Kiwilo" not in image.requests[0].prompt and seen[0][2]["limit"] is None


def test_a_v1_story_s_keyframe_request_is_its_core_byte_for_byte(store, tmp_path, monkeypatch):
    story_id = tas._episode(store, tmp_path)
    seen = _spied(monkeypatch)
    image = tas.FakeImage()
    tas._run(store, story_id, adapters=tas._adapters(image=image))
    requests = _requests(image)
    assert seen and requests
    for shot in tas._shots(store, story_id):
        request = requests.get(f"shot_{shot['shot_id'][2:]}")
        if request is not None:
            assert request.prompt == shot["image_prompt"]
    for _shot_id, core, sent in seen:
        assert sent == {"text": core, "words": len(core.split()), "full_words": len(core.split()), "limit": None,
                        "dropped": []}


def test_the_image_brief_s_keyframe_carries_the_master_fitted_to_its_link(store, tmp_path, monkeypatch):
    from clipping.aistory.steps import assets, brief

    story_id = kf._quality(store, tmp_path)
    _roomy(monkeypatch, store, story_id)
    kf._run(store, story_id)
    ec = tas._ec(store, story_id)
    doc = brief.image_brief(store, store.get(story_id), ec=ec)
    keyframes = [entry for entry in doc["images"] if entry["kind"] == "keyframe"]
    assert keyframes
    link = request_link(store, story_id)
    for entry in keyframes:
        shot = next(item for item in tas._shots(store, story_id) if item["shot_id"] == entry["id"])
        core = assets.request_parts(ec, shot, note=None, link=link)["prompt"]
        assert entry["prompt"].startswith(("SERIES:", "ART STYLE:")) and entry["prompt"].endswith(core)
        assert set(entry["fit"]) == {"limit", "words", "full_words", "dropped"}
        assert entry["fit"]["words"] == len(entry["prompt"].split())
        assert entry["fit"]["limit"] is not None and entry["fit"]["words"] <= entry["fit"]["limit"]
    markdown = brief.render_image_markdown(doc)
    assert all(entry["prompt"] in markdown for entry in keyframes)
    # A keyframe that is the human's own by its mode is held to manual/upload: the template unbounded.
    board, assets_doc = tas._board(store, story_id), tas._assets_doc(store, story_id)
    mine = dict(assets_doc, shot_modes={"sh02": {"image": "manual"}})
    own = next(entry for entry in brief._keyframe_entries(ec, board, mine) if entry["id"] == "sh02")
    assert own["fit"]["limit"] is None and own["fit"]["dropped"] == []
    assert own["fit"]["words"] == own["fit"]["full_words"] >= keyframes[1]["fit"]["words"]


def test_the_keyframe_fit_line_says_keyframe():
    from clipping.aistory.steps import assets

    line = assets._fit_line("sh04", "fal/seedream-4.5-edit", {"limit": 461, "words": 455, "full_words": 700,
                                                              "dropped": ["Chloe"]}, kind="keyframe")
    assert line.startswith("ℹ️ Shot sh04's keyframe prompt: fal/seedream-4.5-edit accepts 461 words: 700 → 455")


# ================================================================ the sheets, plates and props (stage 4b)

class _Stop(Exception):
    """Raised by the fake ``refimages._make``: the plan is all a test reads."""


def _plans(monkeypatch):
    from clipping.aistory import refimages

    plans = []

    def fake_make(stories, story, plan, **_kwargs):
        plans.append(plan)
        raise _Stop()

    monkeypatch.setattr(refimages, "_make", fake_make)
    return plans


def _planned(call):
    try:
        call()
    except _Stop:
        pass


def _live_for(monkeypatch, labels, chars):
    from clipping.providers import prompt_limits

    live = {label: {"status": prompt_limits.PUBLISHED, "prompt_max_chars": chars, "endpoint": label,
                    "read_at": "2026-10-05T00:00:00+00:00"} for label in labels}
    monkeypatch.setattr(prompt_limits, "read_live", lambda path=None: live)


def test_a_sheet_request_carries_the_series_style_and_character_before_the_v2_core(tmp_path, monkeypatch):
    """Fail-first. ``character_image``'s plan: the series, the art style and
    this character's paragraph before the v2 core (the note's tail last),
    fitted to the smallest bound of the sheet chain (a fallback never
    refuses it); with the table's Seedream cap the core alone fits."""
    import test_story_episode_steps as eps
    import test_story_variant_shots as tvs
    from clipping.aistory import imaging, prompt_budgets, refimages
    from clipping.providers import generation as gen
    from clipping.providers import prompt_limits

    store = eps.StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = tvs._speech_story(store)
    story = store.get(story_id)
    env = tas._settings(**tas.FAL)
    lock = imaging.read_lock(store, story_id, error=refimages.RefImageError)
    character = store.read_entity(story_id, "characters", eps.KIWILO)
    labels = refimages._chain_labels(story, "sheet", gen.IMAGE, env)
    assert len(labels) > 1
    core = refimages.character_prompt(story, character, "portrait", env=env, lock=lock)

    def portrait(note=None):
        _planned(lambda: refimages.character_image(store, story_id, eps.KIWILO, "portrait", env=env,
                                                   on_log=lambda _line: None, cancel=None, note=note))

    plans = _plans(monkeypatch)
    _live_for(monkeypatch, labels, 8000)
    portrait()
    prompt = plans[-1].prompt
    assert prompt.startswith("SERIES:") and prompt.endswith(core) and prompt != core
    assert len(prompt.split()) <= prompt_budgets.chain_words(labels)
    assert all(prompt_limits.fits(label, prompt)[0] for label in labels)
    assert "CHARACTER:" in prompt and "Kiwilo" not in prompt
    # A note stays the very tail, after the core.
    portrait(note="Kiwilo sourit")
    assert plans[-1].prompt.endswith(core + " Author's note: the character sourit.")
    assert plans[-1].prompt.startswith("SERIES:")
    # On the table's limits the smallest link of the chain decides; whatever fits, the core ends it.
    _live_for(monkeypatch, labels, 0)
    portrait()
    assert plans[-1].prompt.endswith(core)
    assert all(prompt_limits.fits(label, plans[-1].prompt)[0] for label in labels)


def test_plates_props_and_variant_sheets_carry_their_entity_and_a_v1_story_s_are_their_core(tmp_path, monkeypatch):
    import test_story_episode_steps as eps
    import test_story_variant_shots as tvs
    from clipping.aistory import imaging, refimages, workflow
    from clipping.providers import generation as gen

    store = eps.StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = tvs._speech_story(store, sheet_mode="three_sheet")
    workflow.add_variant(store, story_id, eps.KIWILO, tvs.GHOST, now=NOW)
    story = store.get(story_id)
    env = tas._settings(**tas.FAL)
    lock = imaging.read_lock(store, story_id, error=refimages.RefImageError)
    labels = {label for role, kind in (("plate", gen.IMAGE), ("prop", gen.IMAGE), ("sheet", gen.IMAGE_EDIT))
              for label in refimages._chain_labels(story, role, kind, env)}
    _live_for(monkeypatch, labels, 8000)
    plans = _plans(monkeypatch)

    # The v2 builders draw a place and a prop from their looks.
    place = store.read_entity(story_id, "places", eps.PARLOIR)
    place["look"] = {"layout_map": {"left": "a bamboo chair", "right": "a curtain", "back": "a carved wall",
                                    "foreground": "", "centre": "a lamp"},
                     "scale_note": "a small booth", "lighting": {"day": "soft daylight"}, "props_here": []}
    store.write_entity(story_id, "places", place, now=NOW)
    prop = store.read_entity(story_id, "props", eps.PHONE)
    prop["look"] = {"scale_cm": 15, "material": "polished coconut shell", "colour": "brown",
                    "scale_phrase": "fits in a hand", "where_when": []}
    store.write_entity(story_id, "props", prop, now=NOW)
    story = store.get(story_id)
    _planned(lambda: refimages.place_image(store, story_id, eps.PARLOIR, "day", env=env, on_log=lambda _l: None,
                                           cancel=None))
    core = refimages.place_prompt(store, story, place, "day", env=env, lock=lock)
    assert plans[-1].prompt.startswith("SERIES:") and plans[-1].prompt.endswith(core) and "PLACE:" in plans[-1].prompt

    _planned(lambda: refimages.prop_image(store, story_id, eps.PHONE, env=env, on_log=lambda _l: None, cancel=None))
    core = refimages.prop_prompt(story, prop, env=env, lock=lock)
    assert plans[-1].prompt.startswith("SERIES:") and plans[-1].prompt.endswith(core)
    # A1: the prop's own reference image is told neither its scale nor its owner.
    assert "fits in a hand" not in plans[-1].prompt and "Size:" not in plans[-1].prompt
    assert "belongs to" not in plans[-1].prompt

    character = store.read_entity(story_id, "characters", eps.KIWILO)
    variant = character["variants"][0]
    _planned(lambda: refimages.variant_image(store, story_id, eps.KIWILO, variant["variant_id"], "turnaround",
                                             env=env, on_log=lambda _l: None, cancel=None))
    core = refimages.variant_prompt(story, character, variant, "turnaround", env=env, lock=lock,
                                    names=refimages._entity_names(store, story_id))
    assert plans[-1].prompt.startswith("SERIES:") and plans[-1].prompt.endswith(core)

    # A v1 story: the core alone, byte for byte.
    legacy = {**story, "generation_profile": {**story["generation_profile"], "pipeline": "v1"}}
    sent = refimages._sent(legacy, lock, "character", character, "the core", links=sorted(labels))
    assert sent == {"text": "the core", "words": 2, "full_words": 2, "limit": None, "dropped": []}
