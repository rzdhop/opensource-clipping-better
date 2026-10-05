"""AI Story plan 23 stage D2: the universes.

``templates/universes.json`` (``universes_v1``) says what a story's cast is made
of: ten universes, each with generic species, a ``material_rule`` (the
``{material}`` of a style's all_matter rule), a ``subject_phrase`` and, on
alcohol and gross-out humour, an ``audience_note``. A style lists the universes
it takes (``universes``, ``default_universe``): Fruit Drama fruits and
vegetables (its own rules and its lock otherwise unchanged), the new subject-
neutral ``viral_3d`` all ten. ``generation_profile.universe`` picks one
(``store.create`` refuses one the style does not list); the style lock records it
when the style is approved.

What these tests pin: the catalogue validates and every species is generic (no
denylisted brand); the brand check (whole words, told why); the style keys and
``viral_3d``; the accessor (explicit choice only for anything that is written or
frozen); the profile key through the store and the API model; the lock carries
the universe and its material reaches the all_matter rules; a story with no
universe locks byte for byte as before.

Stdlib + pytest (DEC-012); offline.
"""

from __future__ import annotations

import copy

import pytest

from clipping.aistory import defaults, media_policy, schemas, stylelock, templates, workflow
from clipping.aistory.store import StoryStore

NOW = "2026-10-04T10:00:00+00:00"
LATER = "2026-10-04T11:00:00+00:00"

TEN = ("fruits", "vegetables", "drinks_sodas", "tech_gadgets", "snacks_sweets", "fast_food", "bottles",
       "household_objects", "melting_materials", "gross_funny")
BRANDS = ("coca", "pepsi", "fanta", "sprite", "dr pepper", "mountain dew", "red bull", "monster", "gatorade",
          "starbucks", "iphone", "samsung", "airpods", "playstation", "nintendo", "xbox", "oreo", "nutella",
          "haribo", "m&m", "kinder", "lego")
SPECIES = {
    "fruits": ["strawberry", "banana", "apple", "lemon", "orange", "cherry", "peach", "watermelon", "blueberry",
               "grape", "kiwi", "mango", "pineapple", "coconut", "avocado", "raspberry", "pear", "lime"],
    "vegetables": ["broccoli", "carrot", "corn", "potato", "tomato", "onion", "pepper", "cucumber", "eggplant",
                   "pumpkin", "mushroom"],
    "drinks_sodas": ["cola can", "orange soda", "lemon-lime soda", "cherry-pepper soda", "energy-drink can",
                     "sports-drink bottle", "takeaway coffee cup", "juice box", "milk carton"],
    "tech_gadgets": ["smartphone", "laptop", "headset", "wireless earbuds", "USB key", "smartwatch",
                     "handheld console", "game controller"],
    "snacks_sweets": ["chocolate bar", "donut", "cupcake", "cookie", "gummy bear", "popcorn", "pretzel",
                      "hard candy", "popsicle", "ice-cream cone", "marshmallow", "lollipop"],
    "fast_food": ["burger", "hot dog", "fries", "pizza slice", "taco", "croissant", "bagel", "sushi roll",
                  "nugget", "sandwich"],
    "bottles": ["wine bottle", "beer bottle", "champagne bottle", "whisky bottle", "vodka bottle",
                "tequila bottle", "rum bottle"],
    "household_objects": ["light bulb", "candle", "book", "pencil", "eraser", "ruler", "clock", "mirror",
                          "soap bar", "key", "toothbrush", "pillow"],
    "melting_materials": ["chocolate", "ice cream", "ice cube", "butter", "snowman", "candle wax", "sugar cube",
                          "marshmallow"],
    "gross_funny": ["poop", "fart cloud", "booger", "pimple", "sock", "underwear", "toenail", "armpit hair"],
}


# ================================================================ the catalogue

def test_the_catalogue_validates_and_lists_the_ten_universes_in_order():
    universes = templates.load_universes()
    assert [u["id"] for u in universes] == list(TEN)
    assert defaults.UNIVERSES == TEN
    assert schemas.UNIVERSE_SCHEMA_NAME == "universes_v1"
    for universe in universes:
        assert set(universe["label"]) == {"en", "fr"}
        assert universe["species"] == SPECIES[universe["id"]]
        assert universe["material_rule"] and universe["subject_phrase"]
    # The documents on disk validate as the file itself.
    import json
    doc = json.loads((templates.TEMPLATES_DIR / "universes.json").read_text(encoding="utf-8"))
    assert doc["$schema"] == "universes_v1" and schemas.universes_errors(doc) == []


def test_the_audience_notes_sit_on_alcohol_and_gross_only():
    noted = {u["id"]: u["audience_note"] for u in templates.load_universes() if "audience_note" in u}
    assert set(noted) == {"bottles", "gross_funny"}
    assert noted["bottles"]["en"] == "alcohol: adult audience, no drinking shown as a reward"
    assert noted["gross_funny"]["en"] == "gross-out humour: keep it cartoon-clean, no bodily fluids shown"
    assert all(set(note) == {"en", "fr"} for note in noted.values())


def test_the_material_rules_name_the_matter_of_each_universe():
    rules = {u["id"]: u["material_rule"] for u in templates.load_universes()}
    assert rules["fruits"] == ("the character's own fruit flesh with hyper-detailed natural texture, subsurface "
                               "scattering, pores, seeds, juice reflections and small imperfections")
    assert rules["drinks_sodas"] == ("the can or bottle's own glossy aluminium, glass or plastic with "
                                     "condensation and printed-label texture")
    assert rules["tech_gadgets"] == "the device's own glass, brushed metal and matte plastic with screen glow"
    assert rules["melting_materials"] == "the material's own softening, dripping surface mid-melt"
    assert len(set(rules.values())) == 10


def test_every_species_is_generic_no_denylisted_brand_anywhere_in_the_file():
    assert schemas.BRAND_DENYLIST == BRANDS
    for universe in templates.load_universes():
        assert schemas.brand_hits(universe["species"]) == [], universe["id"]
        assert schemas.brand_hits(universe) == [], universe["id"]
        assert len({s.lower() for s in universe["species"]}) == len(universe["species"])


def test_the_catalogue_schema_refuses_a_broken_file():
    good = {"$schema": "universes_v1", "universes": templates.load_universes()}
    assert schemas.universes_errors(good) == []
    twice = copy.deepcopy(good)
    twice["universes"].append(copy.deepcopy(twice["universes"][0]))
    assert any("used twice" in e for e in schemas.universes_errors(twice))
    branded = copy.deepcopy(good)
    branded["universes"][2]["species"][0] = "Coca-Cola can"
    assert any("coca" in e for e in schemas.universes_errors(branded))
    dup = copy.deepcopy(good)
    dup["universes"][0]["species"].append("Banana")
    assert any("listed twice" in e for e in schemas.universes_errors(dup))
    for key in ("material_rule", "subject_phrase", "label", "species"):
        broken = copy.deepcopy(good)
        del broken["universes"][0][key]
        assert schemas.universes_errors(broken), key
    extra = copy.deepcopy(good)
    extra["universes"][0]["note"] = "x"
    assert schemas.universes_errors(extra)
    assert schemas.universes_errors({"$schema": "universes_v2", "universes": good["universes"]})


def test_the_loader_returns_copies_and_refuses_an_unknown_id():
    first = templates.universe("fruits")
    first["species"].append("kiwi fruit")
    assert templates.universe("fruits")["species"] == SPECIES["fruits"]
    with pytest.raises(KeyError):
        templates.universe("no_such_universe")


# ================================================================ the brand check

@pytest.mark.parametrize("text,brand", [
    ("A Coca-Cola can on the table", "coca"), ("PEPSI", "pepsi"), ("a bottle of Fanta", "fanta"),
    ("sprinkled with Dr Pepper", "dr pepper"), ("Dr-Pepper", "dr pepper"), ("drinks Red Bull", "red bull"),
    ("a Monster Energy can", "monster"), ("an iPhone", "iphone"), ("a Nintendo console", "nintendo"),
    ("M&M's", "m&m"), ("kinder surprise", "kinder"), ("LEGO bricks", "lego"), ("Mountain Dew", "mountain dew"),
    ("Starbucks cup", "starbucks"), ("une Sprite zero", "sprite"),
])
def test_a_denylisted_brand_is_found_whole_word_and_folded(text, brand):
    assert schemas.brand_hits(text) == [brand]
    assert schemas.brand_hits({"a": ["x", {"b": text}]}) == [brand]  # nested replies are walked


@pytest.mark.parametrize("text", [
    "a cola can", "sprinkled sugar", "the scary monster under the bed", "a sprite of light", "legolas",
    "kindergarten", "oreos", "cocaine-free", "a generic smartphone", "orange soda",
])
def test_ordinary_words_are_not_brands(text):
    assert schemas.brand_hits(text) == []


def test_the_brand_error_says_why_and_what_to_do():
    errors = schemas.brand_errors({"title": "Fanta Fight"}, "$.concepts[0]")
    assert errors == ["$.concepts[0]: names the brand 'fanta'; write the generic thing instead (no brand names, "
                      "no trademarked products)"]
    assert schemas.brand_errors({"title": "Cola Fight"}) == []


# ================================================================ the style keys

def test_fruit_drama_takes_fruits_and_vegetables_and_nothing_else_changed():
    template = templates.load_style("fruit_drama")
    assert template["universes"] == ["fruits", "vegetables"] and template["default_universe"] == "fruits"
    assert schemas.style_template_errors(template) == []
    assert template["version"] == 1 and template["default_body_rule"] == "human_body"
    assert template["character_design_rules"].startswith("The head is one recognisable whole fruit or vegetable")


def test_the_other_six_old_styles_list_no_universe():
    for style_id in ("anime", "cartoon_flat", "cinematic_real", "claymation", "family_3d", "storybook_watercolor"):
        template = templates.load_style(style_id)
        assert "universes" not in template and "default_universe" not in template, style_id
        assert media_policy.universe({"style_template_id": style_id}) is None


def test_the_style_schema_checks_the_universe_keys():
    template = templates.load_style("fruit_drama")
    for key, bad in (("universes", ["no_such"]), ("universes", []), ("default_universe", "no_such")):
        assert schemas.style_template_errors(dict(template, **{key: bad})), (key, bad)
    assert any("default_universe" in e for e in schemas.style_template_errors(
        dict(template, universes=["vegetables"], default_universe="fruits")))
    plain = {k: v for k, v in template.items() if k not in ("universes", "default_universe")}
    assert schemas.style_template_errors(plain) == []  # both optional


def test_viral_3d_is_a_subject_neutral_style_with_all_ten_universes():
    template = templates.load_style("viral_3d")
    assert schemas.style_template_errors(template) == []
    assert template["template_id"] == "viral_3d" and template["version"] == 1
    assert template["universes"] == list(TEN) and template["default_universe"] == "fruits"
    assert template["rendering"] == (
        "high-end cinematic 3D render between Pixar/DreamWorks animation and hyperrealism, dramatic cinematic "
        "lighting with volumetric rays and light atmospheric haze, rich saturated palette, layered depth, light "
        "depth of field, main character sharp")
    assert template["default_body_rule"] == "all_matter" and template["default_material"] == "the character's own matter"
    assert "{material}" in template["body_rules"]["all_matter"]
    assert template["sheet_background"] == "light grey"
    assert template["episode_defaults"]["episode_template_id"] == "confrontation_50s_v2"
    # Typography, motion and audio are Fruit Drama's, the font aside.
    fruit = templates.load_style("fruit_drama")
    assert template["motion_rules"] == fruit["motion_rules"] and template["audio"] == fruit["audio"]
    assert {k: v for k, v in template["typography"].items() if k != "font_family" and k != "font_fallback"} == {
        k: v for k, v in fruit["typography"].items() if k != "font_family" and k != "font_fallback"}
    from clipping.aistory.render import fonts as fonts_mod
    record = fonts_mod.resolve_font(template["typography"]["font_family"])
    assert record["file"] == "assets/fonts/LuckiestGuy-Regular.ttf"  # a font of the shipped set, not the fallback
    # Subject-neutral: no fruit, drink or gadget named in the rules a lock would carry.
    prose = " ".join(str(template[k]) for k in ("rendering", "character_design_rules", "environment_rules",
                                                 "negative_prompt"))
    for word in ("fruit", "vegetable", "can ", "gadget"):
        assert word not in prose.lower(), word


def test_viral_3d_renders_a_preview_less_lock_and_locks_both_ways():
    draft = stylelock.build_style_lock(templates.load_style("viral_3d"), {}, now=NOW)
    assert schemas.style_lock_errors(draft) == []
    assert not {"universes", "default_universe", "body_rules", "default_material", "default_body_rule"} & set(draft)
    human = stylelock.lock_style(draft, now=LATER)
    assert human["character_design_rules"] == draft["character_design_rules"]
    matter = stylelock.lock_style(draft, now=LATER, body_rule="all_matter")
    assert "is made of the character's own matter; no human skin anywhere." in matter["character_design_rules"]
    assert "universe" not in matter
    assert media_policy.body_rule({"style_template_id": "viral_3d"}) == "all_matter"


# ================================================================ the profile key

def test_the_profile_key_is_optional_validated_and_clearable():
    assert "universe" not in defaults.default_generation_profile()
    from clipping.aistory import store as store_mod
    profile = store_mod._merge_generation_profile({"universe": "drinks_sodas"})
    assert profile["universe"] == "drinks_sodas"
    for bad in ({"universe": "no_such"}, {"universe": 3}, {"universe": ""}):
        with pytest.raises(ValueError, match="universe"):
            store_mod._merge_generation_profile(bad)
    assert "universe" not in store_mod._merge_generation_profile({"universe": None})
    assert "universe" in store_mod._PROFILE_CLEARABLE
    assert schemas.validate({"tier": 1, "route": "auto", "consistency_mode": "references",
                             "budget_profile": "free", "universe": "fruits"}, schemas._GENERATION_PROFILE_SCHEMA) == []
    assert schemas.validate({"tier": 1, "route": "auto", "consistency_mode": "references",
                             "budget_profile": "free", "universe": "nope"}, schemas._GENERATION_PROFILE_SCHEMA)


def test_the_api_model_carries_the_key_only_when_set():
    pytest.importorskip("pydantic")
    from web.api.models import GenerationProfileModel
    assert "universe" not in GenerationProfileModel().model_dump()
    assert GenerationProfileModel(universe="fruits").model_dump()["universe"] == "fruits"


def test_the_accessor_is_the_stories_own_choice_else_the_styles_default_else_none():
    assert media_policy.universe({"generation_profile": {"universe": "tech_gadgets"}}) == "tech_gadgets"
    assert media_policy.universe({"generation_profile": {}, "style_template_id": "fruit_drama"}) == "fruits"
    assert media_policy.universe({"style_template_id": "viral_3d"}) == "fruits"
    assert media_policy.universe({"style_template_id": "anime"}) is None
    assert media_policy.universe({}) is None and media_policy.universe(None) is None
    assert media_policy.universe({"style_template_id": "no_such_style"}) is None
    assert media_policy.universe({"generation_profile": {"universe": "bogus"}}) is None
    # The lock's own template wins over the story's field (the style being approved).
    assert media_policy.universe({"style_template_id": "anime"}, "viral_3d") == "fruits"
    # Anything written or frozen reads the explicit choice only: a story that never chose is as it was.
    assert media_policy.universe({"style_template_id": "fruit_drama"}, explicit=True) is None
    assert media_policy.universe({"generation_profile": {"universe": "vegetables"}}, explicit=True) == "vegetables"


# ================================================================ create: compatibility

def _store(tmp_path):
    return StoryStore(tmp_path / "outputs", on_log=lambda line: None)


def test_create_accepts_a_universe_the_style_lists(tmp_path):
    store = _store(tmp_path)
    for style, universe in (("fruit_drama", "vegetables"), ("viral_3d", "bottles"), ("viral_3d", "fruits")):
        story = store.create(language="fr", style_template_id=style, generation_profile={"universe": universe},
                             now=NOW)
        assert story["generation_profile"]["universe"] == universe


def test_create_refuses_a_universe_outside_the_styles_list_naming_both(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError) as caught:
        store.create(language="fr", style_template_id="fruit_drama", generation_profile={"universe": "bottles"},
                     now=NOW)
    message = str(caught.value)
    assert "bottles" in message and "fruit_drama" in message and "fruits, vegetables" in message
    with pytest.raises(ValueError, match="accepts: none"):
        store.create(language="fr", style_template_id="anime", generation_profile={"universe": "fruits"}, now=NOW)
    with pytest.raises(ValueError, match="no style chosen"):
        store.create(language="fr", generation_profile={"universe": "fruits"}, now=NOW)
    assert not (tmp_path / "outputs").exists() or not any((tmp_path / "outputs").iterdir())  # nothing is left
    # No universe, any style: as before.
    assert "universe" not in store.create(language="fr", style_template_id="anime", now=NOW)["generation_profile"]


def test_a_patch_to_the_profile_is_checked_against_the_stories_style(tmp_path):
    store = _store(tmp_path)
    story_id = store.create(language="fr", style_template_id="fruit_drama", now=NOW)["story_id"]
    with pytest.raises(workflow.WorkflowError, match="bottles"):
        workflow.patch_story(store, story_id, {"generation_profile": {"universe": "bottles"}}, now=LATER)
    patched = workflow.patch_story(store, story_id, {"generation_profile": {"universe": "vegetables"}}, now=LATER)
    assert patched["generation_profile"]["universe"] == "vegetables"


# ================================================================ the lock

def _story(tmp_path, *, style="fruit_drama", **profile):
    store = _store(tmp_path)
    story_id = store.create(language="fr", style_template_id=style, generation_profile=profile or None,
                            now=NOW)["story_id"]
    draft = stylelock.build_style_lock(templates.load_style(style), {}, now=NOW)
    store.write_doc(story_id, "style_lock.json", draft, now=NOW, validator=schemas.style_lock_errors)
    return store, story_id


def test_the_lock_carries_the_universe_and_its_material_reaches_the_all_matter_rules(tmp_path):
    store, story_id = _story(tmp_path, style="viral_3d", universe="drinks_sodas", body_rule="all_matter")
    workflow.approve_style(store, story_id, now=LATER)
    lock = store.read_doc(story_id, "style_lock.json")
    universe = templates.universe("drinks_sodas")
    assert lock["universe"] == {"id": "drinks_sodas", "label": universe["label"], "species": universe["species"],
                                "subject_phrase": universe["subject_phrase"]}
    assert set(lock["universe"]) == {"id", "label", "species", "subject_phrase"}
    assert ("is made of the can or bottle's own glossy aluminium, glass or plastic with condensation and "
            "printed-label texture; no human skin anywhere.") in lock["character_design_rules"]
    assert "{material}" not in lock["character_design_rules"] and "own matter" not in lock["character_design_rules"]
    assert schemas.style_lock_errors(lock) == [] and lock["locked_at"] == LATER


def test_a_universe_with_human_bodies_is_recorded_and_changes_no_rule(tmp_path):
    store, story_id = _story(tmp_path, style="fruit_drama", universe="vegetables")
    draft = store.read_doc(story_id, "style_lock.json")
    workflow.approve_style(store, story_id, now=LATER)
    lock = store.read_doc(story_id, "style_lock.json")
    assert lock["universe"]["id"] == "vegetables"
    assert {k: v for k, v in lock.items() if k != "universe"} == dict(draft, locked_at=LATER, updated_at=LATER)


def test_a_story_with_no_universe_locks_byte_for_byte_as_before(tmp_path):
    for style in ("fruit_drama", "viral_3d", "anime"):
        store, story_id = _story(tmp_path / style, style=style)
        draft = store.read_doc(story_id, "style_lock.json")
        workflow.approve_style(store, story_id, now=LATER)
        lock = store.read_doc(story_id, "style_lock.json")
        assert "universe" not in lock
        if style != "viral_3d":  # viral_3d's own default rule is all_matter: its rules are the matter's
            assert lock == dict(draft, locked_at=LATER, updated_at=LATER)


def test_lock_style_with_a_universe_record_only_when_given():
    draft = stylelock.build_style_lock(templates.load_style("viral_3d"), {}, now=NOW)
    universe = templates.universe("tech_gadgets")
    locked = stylelock.lock_style(draft, now=LATER, body_rule="all_matter", material=universe["material_rule"],
                                  universe=universe)
    assert locked["universe"]["species"] == SPECIES["tech_gadgets"]
    assert "the device's own glass, brushed metal and matte plastic with screen glow" in locked[
        "character_design_rules"]
    assert "universe" not in stylelock.lock_style(draft, now=LATER)
    assert draft["locked_at"] is None and "universe" not in draft  # a copy is returned
    # The lock schema checks the record.
    assert schemas.style_lock_errors(dict(locked, universe={"id": "x"}))


def test_the_style_endpoint_payload_and_the_universes_endpoint():
    pytest.importorskip("fastapi")
    import asyncio

    from web.api.routes import stories as routes

    payload = asyncio.run(routes.list_universes())
    assert [u["id"] for u in payload["universes"]] == list(TEN)
    assert payload["by_style"] == {
        "fruit_drama": {"universes": ["fruits", "vegetables"], "default": "fruits"},
        "viral_3d": {"universes": list(TEN), "default": "fruits"},
    }
    noted = [u["id"] for u in payload["universes"] if "audience_note" in u]
    assert noted == ["bottles", "gross_funny"]
    # Plan 26 stage 7c: each entry carries its species pool (the Cast tile's Species select); additive.
    assert all(set(u) <= {"id", "label", "species", "audience_note"} for u in payload["universes"])
    assert {u["id"]: u["species"] for u in payload["universes"]} == dict(SPECIES)
    styles = asyncio.run(routes.list_styles())["styles"]
    # The picker's payload keeps its shape: the universes are their own endpoint.
    assert all(set(s) == {"template_id", "version", "name", "palette", "typography", "episode_defaults"}
               for s in styles)
