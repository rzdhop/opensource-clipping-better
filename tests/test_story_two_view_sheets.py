"""AI Story plan 23 stage D4: the two-view character sheet.

``generation_profile.sheet_mode`` (absent = ``three_sheet``: today's portrait,
turnaround and expressions) can be ``two_view`` -- ONE 9:16 image per
character, the front on its left half and the back on its right, written into
the ``refs.portrait`` slot (so the keyframes, J2 and the brief read it as they
always read the portrait; the turnaround stays null) -- or
``two_view_expressions``, that sheet and an expressions sheet, an edit of it.

What these tests pin: one image per character in ``two_view`` (the estimate,
``workflow.cast_units`` and the cast step agree); the prompt (a sha256 golden
of what a Fruit Drama character gets, built through the same budget fitting as
the other sheets); the keyframe's role text and J2's note say the sheet shows
the character twice -- only in a two-view mode; the brief's label; a 9:16 upload
accepted in the portrait slot; and that an absent mode changes nothing.

Stdlib + pytest (DEC-012); offline and hermetic (the look tests' fakes).
"""

from __future__ import annotations

import copy
import hashlib
import os

import pytest

from clipping.aistory import defaults, media_policy, prompting, refimages, shots, templates, workflow
from clipping.aistory.store import StoryStore

import test_story_look as tsl
import test_story_shots as tss
from test_story_assets_step import hermetic, store  # noqa: F401 - the assets step's fixtures (hermetic is autouse)
from test_story_look import hermetic as look_hermetic  # noqa: F401 - the look tests' fixture, for the cast runs

NOW = tsl.NOW
FRUIT_DRAMA = templates.load_style("fruit_drama")

LOOK_TEXT = ("a tall kiwi man with a fuzzy brown head, wearing a white linen shirt, thin gold chain, colours brown "
             "and green")
ITEMS = ["thin gold chain", "left-eyebrow scar"]

# The prompt of a Fruit Drama character at the two-view budget (260 words), as the story's cast would
# ask it: the skeleton, the style's rendering and the style's design rules.
# re-pinned 2026-10-06, plan 32 stage 3: the Pixar-style cartoon look of fruit_drama (DEC-315)
TWO_VIEW_FRUIT_DRAMA = "7aefc750e0b2dc6b9330c796dc9d1409c2755f9c5cbcd382ce25f42812d90ff5"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _story(store, *, sheet_mode=None, v2=True):
    """A v2 Fruit Drama story (style locked) with the look tests' documents."""
    story_id = tsl._story(store, v2=v2)
    if sheet_mode is not None:
        store.update(story_id, lambda doc: doc["generation_profile"].update(sheet_mode=sheet_mode), now=NOW)
    return story_id


def _k1_d1_d2_queues(events):
    return tsl.FakeLLM(
        events,
        K1=[tsl._k1("a fuzzy kiwi", ["gold chain", "linen shirt"]), tsl._k1("a sly mango", ["red dress", "crown clip"])],
        D1=[tsl._d1("Né sur la plage.", with_="Mangella"), tsl._d1("Reine du parloir.", with_="Kiwilo")],
        D2=[tsl._d2(175), tsl._d2(160, "mango")])  # DEC-305 F3: one species each


# ================================================================ the profile key

def test_the_profile_takes_the_two_keys_and_absent_is_no_key_at_all(tmp_path):
    from clipping.aistory import store as store_mod

    assert defaults.SHEET_MODES == ("three_sheet", "two_view", "two_view_expressions")
    assert defaults.BODY_RULES == ("human_body", "all_matter")
    assert "sheet_mode" not in defaults.default_generation_profile()
    assert "body_rule" not in defaults.default_generation_profile()
    merged = store_mod._merge_generation_profile({"pipeline": "v2", "sheet_mode": "two_view", "body_rule": "all_matter"})
    assert (merged["sheet_mode"], merged["body_rule"]) == ("two_view", "all_matter")
    for bad in ({"sheet_mode": "four_sheet"}, {"body_rule": "no_skin"}, {"sheet_mode": 1}):
        with pytest.raises(ValueError):
            store_mod._merge_generation_profile(bad)
    # null clears (a PATCH), and the story schema knows both keys.
    cleared = store_mod._merge_generation_profile({"sheet_mode": None, "body_rule": None})
    assert "sheet_mode" not in cleared and "body_rule" not in cleared
    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story = store.create(language="fr", generation_profile={"pipeline": "v2", "sheet_mode": "two_view_expressions"},
                         now=NOW)
    assert story["generation_profile"]["sheet_mode"] == "two_view_expressions"


def test_the_api_model_carries_the_keys_and_drops_the_absent_ones():
    pytest.importorskip("pydantic")
    from web.api.models import GenerationProfileModel

    assert "sheet_mode" not in GenerationProfileModel().model_dump()
    assert "body_rule" not in GenerationProfileModel().model_dump()
    dumped = GenerationProfileModel(sheet_mode="two_view", body_rule="all_matter").model_dump()
    assert dumped["sheet_mode"] == "two_view" and dumped["body_rule"] == "all_matter"


def test_the_sheet_mode_accessor_reads_the_key_only_on_a_v2_story():
    v2 = {"generation_profile": {"pipeline": "v2", "sheet_mode": "two_view"}}
    assert media_policy.sheet_mode(v2) == "two_view" and media_policy.two_view(v2) is True
    # Absent, unknown, or a story not on v2: three sheets, as always.
    assert media_policy.sheet_mode({"generation_profile": {"pipeline": "v2"}}) == "three_sheet"
    assert media_policy.sheet_mode({"generation_profile": {"sheet_mode": "two_view"}}) == "three_sheet"
    assert media_policy.sheet_mode(None) == "three_sheet" and media_policy.two_view({}) is False
    assert [media_policy.sheet_edits(mode) for mode in defaults.SHEET_MODES] == [2, 0, 1]
    assert media_policy.sheet_edits({"generation_profile": {"pipeline": "v2", "sheet_mode": "two_view_expressions"}}) == 1
    assert refimages.character_images(v2) == ("portrait",)
    assert refimages.character_images({"generation_profile": {"pipeline": "v2",
                                                              "sheet_mode": "two_view_expressions"}}) == (
        "portrait", "expressions")
    assert refimages.character_images({}) == refimages.CHARACTER_IMAGES
    assert refimages.character_size(v2, "portrait") == (1080, 1920)
    assert refimages.character_size({}, "portrait") == refimages.PORTRAIT_SIZE == (720, 1280)


# ================================================================ the prompt

def test_the_two_view_prompt_is_the_creators_template_through_the_sheet_budget():
    prompt = prompting.two_view_prompt_v2(FRUIT_DRAMA, look_text=LOOK_TEXT, signature_items=ITEMS)
    # The layout, said once, ahead of the look.
    assert prompt.startswith(
        "A character reference sheet showing two full-body views of the same character side by side, separated by "
        "a clean vertical line in the centre. LEFT HALF: full frontal view head to toe facing the camera. RIGHT "
        "HALF: full back view head to toe. Never cut the character in half across the line. Never more than two "
        "views. Never crop a view. Character: a tall kiwi man")
    assert "with left-eyebrow scar" in prompt
    # The dress rule, the plain background and the closing.
    assert ("Fully dressed from shoulders to feet: a complete top, a complete bottom (trousers, or a skirt or dress "
            "below the knee) and shoes; no bare legs, no visible underwear.") in prompt
    assert ("Plain light grey background, soft uniform light, no text, no grid, no labels. Adult proportions, never "
            "chibi. Vertical 9:16. Clean frame: no captions, lettering, logos or watermarks; the same single "
            "character throughout.") in prompt
    # The style fills what the budget leaves: the rendering, then the design rules.
    # re-pinned 2026-10-06, plan 32 stage 3: the Pixar-style cartoon look of fruit_drama (DEC-315)
    assert "Style: a stylised 3D cartoon animation" in prompt and "The head is one recognisable whole fruit" in prompt
    assert len(prompt.split()) <= prompting.TWO_VIEW_V2_MAX_WORDS
    assert ".," not in prompt and ".." not in prompt and "  " not in prompt
    assert _sha(prompt) == TWO_VIEW_FRUIT_DRAMA, prompt


def test_the_two_view_prompt_keeps_its_skeleton_whole_when_the_budget_is_tight():
    tight = prompting.two_view_prompt_v2(FRUIT_DRAMA, look_text=LOOK_TEXT, signature_items=ITEMS, budget=130)
    # The layout, the look and the dress rule are never cut; the style gives way first.
    assert "Never crop a view." in tight and "no visible underwear." in tight and "Style:" not in tight
    assert tight.endswith("the same single character throughout.")
    roomy = prompting.two_view_prompt_v2(FRUIT_DRAMA, look_text=LOOK_TEXT, signature_items=ITEMS, budget=400,
                                         cues="Distinctive: a deep scar through the left eyebrow.")
    assert roomy.index("Distinctive:") < roomy.index("Style:") and "deep scar" in roomy


def test_the_budget_of_the_sheet_link_and_the_other_sheets_are_untouched():
    from clipping.aistory import prompt_budgets as pb

    # Seedream takes long prompts: the two-view sheet gets the link's own (plan 26 H1 dropped its
    # ceiling); with no link, the default.
    assert pb.two_view_words("fal/seedream-4.5", live={}) == 461 and not hasattr(pb, "TWO_VIEW_CEILING_WORDS")
    assert pb.two_view_words(None) == prompting.TWO_VIEW_V2_MAX_WORDS
    assert pb.sheet_words(None) == prompting.SHEET_V2_MAX_WORDS == 130
    assert pb.SHEET_CEILING_WORDS == 200
    # The portrait of a three-sheet story is still the full-body three-quarter view.
    portrait = prompting.portrait_prompt_v2(FRUIT_DRAMA, look_text=LOOK_TEXT, signature_items=ITEMS)
    assert portrait.startswith("Full-body character reference sheet, head to toe, front three-quarter view")


# ================================================================ the cast: one image per character

def test_a_two_view_story_estimates_and_draws_one_image_per_character(tmp_path, look_hermetic):
    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store, sheet_mode="two_view")
    story = store.get(story_id)
    assert media_policy.sheet_mode(story) == "two_view"

    # 1 image and 0 edits a character (three-sheet: 1 and 2).
    units = workflow.cast_units(store, story, selected=["Kiwilo", "Mangella"])
    assert (units["images"], units["edit_images"]) == (2, 0)
    plain_id = _story(store)
    plain = workflow.cast_units(store, store.get(plain_id), selected=["Kiwilo", "Mangella"])
    assert (plain["images"], plain["edit_images"]) == (2, 4)

    events = tsl.Events()
    llm = _k1_d1_d2_queues(events)
    image = tsl.FakeImage(events)
    tsl._run_cast(store, story_id, llm, events, image)
    # Only the portrait slot, once per character -- no turnaround, no expressions, no edit.
    assert events == ["K1", "D1", "D2", "image:portrait", "K1", "D1", "D2", "image:portrait"]
    assert [request.kind for request in image.requests] == ["image", "image"]
    for request in image.requests:
        assert (request.width, request.height) == (1080, 1920)
        # Plan 26 H1: the core last, after the series, the style and the character.
        assert request.prompt.split("\n\n")[-1].startswith("A character reference sheet showing two full-body views")
        assert "LEFT HALF: full frontal view head to toe" in request.prompt and "Vertical 9:16." in request.prompt
    for doc in store.list_entities(story_id, "characters"):
        assert doc["refs"]["portrait"]["consistency"] == "base"
        assert doc["refs"]["turnaround"] is None and doc["refs"]["expressions"] is None
        assert "portrait" not in workflow.character_missing(store, story_id, doc, story=story)
        assert not {"turnaround", "expressions"} & set(workflow.character_missing(store, story_id, doc))
    # Nothing is left to draw: the estimate is empty and a rerun draws nothing.
    assert workflow.cast_units(store, store.get(story_id))["images"] == 0
    events.clear()
    tsl._run_cast(store, story_id, tsl.FakeLLM(events), events)
    assert events == []
    # No other slot can be made or regenerated in this mode.
    from clipping.aistory.refimages import RefImageError

    char_id = store.list_entities(story_id, "characters")[0]["char_id"]
    with pytest.raises(RefImageError, match="not drawn in this story's sheet mode"):
        refimages.character_image(store, story_id, char_id, "turnaround", env=tsl.SETTINGS,
                                  on_log=lambda line: None, cancel=tsl.CancelToken())


def test_two_view_expressions_adds_one_edit_of_the_sheet(tmp_path, look_hermetic):
    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store, sheet_mode="two_view_expressions")
    units = workflow.cast_units(store, store.get(story_id), selected=["Kiwilo", "Mangella"])
    assert (units["images"], units["edit_images"]) == (2, 2)

    events = tsl.Events()
    image = tsl.FakeImage(events)
    tsl._run_cast(store, story_id, _k1_d1_d2_queues(events), events, image)
    assert events == ["K1", "D1", "D2", "image:portrait", "image:expressions",
                      "K1", "D1", "D2", "image:portrait", "image:expressions"]
    expressions = image.requests[1]
    assert expressions.kind == "image_edit" and expressions.references[0].endswith("portrait.png")
    assert expressions.prompt.split("\n\n")[-1].startswith(prompting.ROLE_TEXT_PORTRAIT)
    for doc in store.list_entities(story_id, "characters"):
        assert doc["refs"]["portrait"] and doc["refs"]["expressions"] and doc["refs"]["turnaround"] is None


def test_the_three_sheet_cast_is_what_it_was(tmp_path, look_hermetic):
    """The mode absent: portrait, turnaround, expressions, the three-quarter portrait at 720x1280."""
    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store)
    events = tsl.Events()
    image = tsl.FakeImage(events)
    tsl._run_cast(store, story_id, _k1_d1_d2_queues(events), events, image)
    assert events[:6] == ["K1", "D1", "D2", "image:portrait", "image:turnaround", "image:expressions"]
    assert (image.requests[0].width, image.requests[0].height) == (720, 1280)
    assert image.requests[0].prompt.split("\n\n")[-1].startswith("Full-body character reference sheet, head to toe")


def test_the_preset_estimate_counts_the_story_s_sheets_and_says_so(look_hermetic):
    default = media_policy.preset_estimate()
    assert media_policy.preset_estimate(story=None) == default
    assert media_policy.preset_estimate(story={"generation_profile": {"pipeline": "v2"}}) == default
    by_mode = default["story"]["sheet_usd_by_mode"]
    assert set(by_mode) == set(defaults.SHEET_MODES)
    assert by_mode["three_sheet"] > by_mode["two_view_expressions"] > by_mode["two_view"] > 0
    assert default["story"]["images"] == 3 * 3 + 2 + 3 and "3 characters × 3 sheets" in default["assumptions"]

    one = media_policy.preset_estimate(story={"generation_profile": {"pipeline": "v2", "sheet_mode": "two_view"}})
    assert one["story"]["images"] == 3 * 1 + 2 + 3 and "3 characters × 1 sheet," in one["assumptions"]
    assert one["story"]["sheets_usd"] == pytest.approx(3 * by_mode["two_view"], abs=1e-3)
    assert one["story_usd"] < default["story_usd"]
    plus = media_policy.preset_estimate(
        story={"generation_profile": {"pipeline": "v2", "sheet_mode": "two_view_expressions"}})
    assert plus["story"]["images"] == 3 * 2 + 2 + 3 and "3 characters × 2 sheets" in plus["assumptions"]
    # The sheet price is flat per image: the larger two-view size costs nothing more (the same edit link's
    # price is the portrait link's here).
    assert by_mode["two_view"] == pytest.approx(
        by_mode["three_sheet"] - 2 * (by_mode["two_view_expressions"] - by_mode["two_view"]))


# ================================================================ the keyframes and J2

def _roles():
    return [("identity", "the tall cylinder"), ("identity", "the short triangle"), ("set", ""), ("turnaround", "x")]


def test_the_role_text_says_the_sheet_shows_the_character_twice_only_in_a_two_view_mode():
    roles = [("identity", "the tall cylinder"), ("identity", "the short triangle"), ("set", "")]
    plain = prompting.role_text(roles)
    assert "twice" not in plain and prompting.role_text(roles, two_view=False) == plain
    twice = prompting.role_text(roles, two_view=True)
    assert ("Image 1 is the tall cylinder's reference (keep identity, proportions and outfit exactly). Image 1 shows "
            "this one character twice, front and back: draw them once. Image 2 is the short triangle's reference "
            "(keep identity, proportions and outfit exactly). Image 2 shows this one character twice, front and "
            "back: draw them once. Image 3 is the set (keep layout and light).") == twice
    assert prompting.role_text(roles, compact=True) == prompting.role_text(roles, compact=True, two_view=False)
    compact = prompting.role_text(roles, compact=True, two_view=True)
    assert "image 1 shows the tall cylinder twice, front and back: draw them once" in compact
    assert compact.endswith("draw them once.") and "image 3" not in compact
    # An expression sheet is not a two-view sheet: no sentence for it.
    assert "twice" not in prompting.role_text([("expressions", "the tall cylinder")], two_view=True)


def _entities(sheet_mode=None):
    entities = tss._v2_entities()
    for doc in entities["characters"].values():
        doc["refs"]["turnaround"] = None
    if sheet_mode is not None:
        entities["sheet_mode"] = sheet_mode
    return entities


def _resolve(entities):
    plan = {"framing": "medium_single", "action": tss._V2_ACTION, "lines": [1], "camera_motion": "hold",
            "modifiers": [], "subjects": ["@char_captain_obvious", "@char_miss_overthink", "#place_clocktown:day"]}
    return shots.resolve_shot(plan, scene=tss._v2_scene(), entities=entities, style_lock=tss.CARTOON_FLAT,
                              consistency_mode="references", v2=True)


def test_a_keyframe_tells_the_model_to_draw_a_two_view_character_once_and_otherwise_never_says_it():
    plain = _resolve(_entities())["image_prompt"]
    assert "draw them once" not in plain and "front and back" not in plain
    for mode in ("two_view", "two_view_expressions"):
        prompt = _resolve(_entities(mode))["image_prompt"]
        assert "Image 1 shows this one character twice, front and back: draw them once." in prompt
        assert "Image 2 shows this one character twice, front and back: draw them once." in prompt
        assert "Image 3 shows" not in prompt  # the set
    # The mode absent, or three_sheet spelled out: the very prompt it was.
    assert _resolve(_entities("three_sheet"))["image_prompt"] == plain


def test_the_episode_context_marks_a_two_view_story_and_no_other(store):
    import test_story_assets_step as tas

    for mode, marked in ((None, None), ("three_sheet", None), ("two_view", "two_view"),
                         ("two_view_expressions", "two_view_expressions")):
        ec = tas._ec(store, _manual_story(store, sheet_mode=mode))
        assert ec.entities.get("sheet_mode") == marked
        assert {"characters", "places", "props"} <= set(ec.entities)
        assert ("sheet_mode" in ec.entities) is (marked is not None)


def test_j2_says_each_sheet_shows_its_character_twice_only_when_it_does():
    from clipping.aistory import prompts

    kwargs = dict(shot_id="sh01", brief="a brief", previous_shot_id="sh00", same_scene=True,
                  sheets=["Kiwilo", "Mangella"])
    _system, plain, _schema = prompts.build_j2(**kwargs)
    assert "twice" not in plain and prompts.build_j2(**kwargs, two_view=False)[1] == plain
    _system, two, _schema = prompts.build_j2(**kwargs, two_view=True)
    assert prompts._J2_TWO_VIEW_NOTE in two and two.replace(" " + prompts._J2_TWO_VIEW_NOTE, "") == plain
    # No sheet sent: nothing to explain.
    assert "twice" not in prompts.build_j2(shot_id="sh01", brief="b", two_view=True)[1]
    assert "twice, front and back" in prompts.j2_prompt_text(**kwargs, two_view=True)


# ================================================================ the brief and the uploads

def _manual_story(store, *, sheet_mode=None):
    """A planned native-speech manual story (v2), its mode set before the storyboard is built."""
    from clipping.aistory.steps import storyboard

    import test_story_assets_step as tas
    import test_story_native_speech_plan as nsp

    story_id = nsp.native_story(store, profile="native_speech_manual")
    # A v2 story is always on references (the fixture's own story predates that rule): its keyframes name the
    # reference images, which is what the two-view sentence goes into.
    store.update(story_id, lambda doc: doc["generation_profile"].update(consistency_mode="references"), now=tas.NOW)
    if sheet_mode is not None:
        store.update(story_id, lambda doc: doc["generation_profile"].update(sheet_mode=sheet_mode), now=tas.NOW)
    storyboard.build_fast(store, story_id, 1, now=tas.NOW, on_log=lambda _line: None)
    tas._approve(store, story_id)
    return story_id


def test_the_shot_brief_labels_the_two_view_sheet_and_the_storyboard_says_draw_once(store):
    import test_story_assets_step as tas
    import test_story_manual_link as tml
    from clipping.aistory.steps import brief

    plain_id = _manual_story(store)
    tml._planted(store, plain_id)
    entry = next(item for item in brief.shot_brief(tas._ec(store, plain_id), platform="flow")["shots"]
                 if item["speaks"] and len(item["references"]) >= 2)
    assert entry["references"][1]["label"].endswith("character sheet (portrait)")

    story_id = _manual_story(store, sheet_mode="two_view")
    tml._planted(store, story_id)
    entry = next(item for item in brief.shot_brief(tas._ec(store, story_id), platform="flow")["shots"]
                 if item["speaks"] and len(item["references"]) >= 2)
    sheets = [ref for ref in entry["references"] if ref["kind"] == "sheet"]
    assert sheets and all(ref["label"].endswith("character sheet (front and back)") for ref in sheets)
    # The stored keyframe prompts carry the role sentence (the storyboard is built with the mode).
    board = tas._board(store, story_id)
    assert any("shows this one character twice, front and back: draw them once." in shot["image_prompt"]
               for shot in board["shots"])
    assert not any("twice, front and back" in shot["image_prompt"] for shot in tas._board(store, plain_id)["shots"])


def test_the_image_brief_lists_one_front_and_back_sheet_per_character_at_9_16(store):
    from clipping.aistory.steps import brief

    plain_id = _manual_story(store)
    plain = brief.image_brief(store, store.get(plain_id))
    slots = {(entry["id"], entry["slot"]) for entry in plain["images"] if entry["kind"] == "sheet"}
    assert {slot for _cid, slot in slots} == {"portrait", "turnaround", "expressions"}

    for mode, expected in (("two_view", {"portrait"}), ("two_view_expressions", {"portrait", "expressions"})):
        story_id = _manual_story(store, sheet_mode=mode)
        sheets = [entry for entry in brief.image_brief(store, store.get(story_id))["images"]
                  if entry["kind"] == "sheet"]
        assert {entry["slot"] for entry in sheets} == expected
        portrait = next(entry for entry in sheets if entry["slot"] == "portrait")
        assert portrait["label"].endswith("character sheet (front and back)")
        assert portrait["size"] == [1080, 1920] and portrait["min_size"] == [540, 960]
        assert portrait["upload_slot"].endswith("/sheet?which=portrait")


def _png(tmp_path, name, size):
    image_module = pytest.importorskip("PIL.Image")
    path = tmp_path / name
    image_module.new("RGB", size, (200, 40, 60)).save(path, format="PNG")
    return os.fspath(path)


def test_a_9_16_sheet_is_accepted_in_the_portrait_slot_and_a_sheet_the_mode_does_not_draw_is_not(store, tmp_path):
    from clipping.aistory import manual_uploads

    import test_story_assets_step as tas

    story_id = _manual_story(store, sheet_mode="two_view")
    store.update(story_id, lambda doc: doc["generation_profile"].update(images="manual"), now=tas.NOW)
    cid = "char_kiwilo"

    done = manual_uploads.accept_image(store, story_id, "characters", cid, "portrait",
                                       _png(tmp_path, "sheet.png", (1080, 1920)))
    assert done["slot"] == "portrait" and done["size"] == [1080, 1920]
    portrait = store.read_entity(story_id, "characters", cid)["refs"]["portrait"]
    assert portrait["source"] == "manual/upload" and portrait["consistency"] == "base"
    # Smaller than half the app's size: refused with the sheet's own size.
    with pytest.raises(manual_uploads.UploadRefused, match=r"a sheet two view is at least 540x960"):
        manual_uploads.accept_image(store, story_id, "characters", cid, "portrait",
                                    _png(tmp_path, "small.png", (360, 640)))
    # The turnaround is not a sheet of this story.
    with pytest.raises(manual_uploads.UploadRefused, match="not a sheet of this story"):
        manual_uploads.accept_image(store, story_id, "characters", cid, "turnaround",
                                    _png(tmp_path, "wide.png", (1280, 720)))
    # The expressions sheet is, in two_view_expressions.
    store.update(story_id, lambda doc: doc["generation_profile"].update(sheet_mode="two_view_expressions"),
                 now=tas.NOW)
    ok = manual_uploads.accept_image(store, story_id, "characters", cid, "expressions",
                                     _png(tmp_path, "expr.png", (1200, 800)))
    assert ok["ref"]["consistency"] == "references"


def test_a_three_sheet_story_still_takes_the_720_portrait(store, tmp_path):
    from clipping.aistory import manual_uploads

    import test_story_assets_step as tas

    story_id = _manual_story(store)
    store.update(story_id, lambda doc: doc["generation_profile"].update(images="manual"), now=tas.NOW)
    done = manual_uploads.accept_image(store, story_id, "characters", "char_kiwilo", "portrait",
                                       _png(tmp_path, "p.png", (720, 1280)))
    assert done["size"] == [720, 1280]
    small = _png(tmp_path, "tiny.png", (200, 300))
    with pytest.raises(manual_uploads.UploadRefused, match="a portrait is at least 360x640"):
        manual_uploads.accept_image(store, story_id, "characters", "char_kiwilo", "portrait", small)
    ok = manual_uploads.accept_image(store, story_id, "characters", "char_kiwilo", "turnaround",
                                     _png(tmp_path, "t.png", (1280, 720)))
    assert ok["slot"] == "turnaround"


# ================================================================ regenerate

def test_regenerating_a_two_view_portrait_redraws_only_the_sheets_the_mode_has(tmp_path, look_hermetic):
    from clipping.aistory import workflow as wf

    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store, sheet_mode="two_view_expressions")
    for char_id, name in (("char_kiwilo", "Kiwilo"),):
        doc = tsl._character(char_id, name, "a fuzzy kiwi", ["gold chain", "linen shirt"])
        doc["look"] = copy.deepcopy(tsl._look())
        doc["refs"]["portrait"] = {"name": "portrait.png", "consistency": "base", "source": "x", "seed": 1,
                                   "created_at": NOW}
        doc["refs"]["turnaround"] = {"name": "turnaround.png", "consistency": "references", "source": "x", "seed": 1,
                                     "created_at": NOW}
        doc["refs"]["expressions"] = {"name": "expressions.png", "consistency": "references", "source": "x",
                                      "seed": 1, "created_at": NOW}
        store.write_entity(story_id, "characters", doc, now=NOW)
    story = store.get(story_id)
    # A portrait asks for itself and the one sheet the mode draws (the old turnaround is left alone).
    units = wf.target_units(store, story, ("character", "char_kiwilo", "image", "portrait"))
    assert (units["images"], units["edit_images"]) == (1, 1)
    two_view = wf.target_units(store, dict(story, generation_profile=dict(story["generation_profile"],
                                                                         sheet_mode="two_view")),
                               ("character", "char_kiwilo", "image", "portrait"))
    assert (two_view["images"], two_view["edit_images"]) == (1, 0)
    three = wf.target_units(store, dict(story, generation_profile={
        k: v for k, v in story["generation_profile"].items() if k != "sheet_mode"}),
        ("character", "char_kiwilo", "image", "portrait"))
    assert (three["images"], three["edit_images"]) == (1, 2)


def test_a_sheet_the_mode_does_not_draw_is_refused_before_a_job_exists(tmp_path, look_hermetic):
    """``check_entity_target``: the regenerate of a turnaround on a two-view story says why, with the slots
    the mode does draw; the three-sheet story accepts it (it asks only for the portrait first)."""
    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store, sheet_mode="two_view")
    doc = tsl._character("char_kiwilo", "Kiwilo", "a fuzzy kiwi", ["gold chain", "linen shirt"])
    doc["refs"]["portrait"] = {"name": "portrait.png", "consistency": "base", "source": "x", "seed": 1,
                               "created_at": NOW}
    store.write_entity(story_id, "characters", doc, now=NOW)
    story = store.get(story_id)
    for slot in ("turnaround", "expressions"):
        with pytest.raises(workflow.WorkflowError, match="is not drawn in this story's sheet mode") as caught:
            workflow.check_entity_target(store, story, ("character", "char_kiwilo", "image", slot))
        assert "two_view: portrait" in str(caught.value)
    workflow.check_entity_target(store, story, ("character", "char_kiwilo", "image", "portrait"))
    plain = _story(store)
    store.write_entity(plain, "characters", doc, now=NOW)
    with pytest.raises(workflow.WorkflowError, match="make Kiwilo's portrait first|Make Kiwilo's portrait first"):
        workflow.check_entity_target(store, store.get(plain), ("character", "char_kiwilo", "image", "turnaround"))
