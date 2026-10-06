"""The set-up block on every set-up writer of a v2 story (AI Story plan 28
stages E1/E2, DEC-305 §8).

The human (2026-10-05): "the prompts for concepts, places, casts must be
upgraded if they are not from the last upgrade batch". The plan-26 image and
clip prompts say the series, the art style, the universe, the audience and
the format; the text writers of the set-up (C1v2, C1J, B1-B3, K1, D1, D2, P0,
P1, R1, D3, R1v2, S1, S2, D4-D6) now read the same in one block
(``context.setup_context``), first, on a v2 story only: a legacy story's
prompt is byte-identical (no block in its pack).

Essential tests only (DEC-234): the block for a real-shaped fruit story, the
gate, one present-on-v2 / absent-on-legacy check per builder, the steps hand
it over, and the input budgets measured on each builder's French worst case
with the block at its own worst (DEC-138's method: + 15 %, rounded up to ten).

Pure and offline; stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import functools

import pytest

import test_story_episode_prompt_budgets as budgets
from clipping.aistory import context, prompts, recipes, schemas, templates, universes
from clipping.providers.pacing import estimate_tokens

FRUIT_DRAMA = templates.load_style("fruit_drama")
SERIAL_V2 = templates.load_episode_template("serial_60s_v2")
STYLE_IDS = templates.list_style_ids()

# The shape of the live fruit story d71852710962 (French, fruit_drama, serial_60s_v2, native speech).
FRUIT_STORY = {
    "story_id": "d71852710962", "title": "Cœurs Sous Clé", "language": "fr",
    "logline": "Un hacker flamboyant pirate une directrice rigide.",
    "premise": "Rida prend les serveurs; Marie-Jeanne riposte.",
    "tone": "Tendu, provocateur.", "genre_tags": ["Romance", "Thriller technologique"],
    "audience": {"age": "13+", "platforms": ["tiktok", "shorts", "reels"]},
    "style_template_id": "fruit_drama", "episode_template_id": "serial_60s_v2",
    "generation_profile": {"tier": 3, "pipeline": "v2", "budget_profile": "native_speech", "universe": "fruits",
                           "writing": "v3"},
    "narrator": {"enabled": False},
}
LEGACY_STORY = dict(FRUIT_STORY, generation_profile={"tier": 1})

# re-pinned 2026-10-06, plan 32 stage 3: the Pixar-style cartoon look of fruit_drama (DEC-315)
FRUIT_BLOCK = (
    "SERIES SET-UP (binding: everything you write must fit it)\n"
    "SERIES: Title: Cœurs Sous Clé. A serialized vertical drama of 8 episodes, written in French. Logline: Un "
    "hacker flamboyant pirate une directrice rigide. Tone: Tendu, provocateur. Genre: Romance, Thriller "
    "technologique.\n"
    "ART STYLE: Fruit Drama. Medium: a stylised 3D cartoon animation in the manner of a Pixar feature, fully "
    "computer-generated. The characters are fruit and vegetable people: each one's whole head IS the fruit itself, "
    "stem, leaves and skin intact, with large expressive cartoon eyes and a wide mouth drawn on the fruit's skin; "
    "cartoon proportions (an oversized fruit head on a slim body) in real-looking human clothes, with human hands; "
    "only their traits and manners are human. Smooth CGI surfaces; never a human face, never real human skin, never "
    "a mask or costume on a person; no live-action footage, no real people, no photographs. Rendering: a stylised "
    "3D cartoon animation in the manner of a Pixar feature — soft rounded forms, clean subsurface-lit fruit skin, "
    "big expressive eyes, warm key light and a soft rim, shallow depth of field, clean cinematic 9:16 framing; no "
    "photorealism, no live-action textures. "
    "Palette: saturated natural fruit colours against warm neutral sets. Never use neon green or hot pink "
    "backgrounds.\n"
    "UNIVERSE: Fruits -- every character is an anthropomorphic fruit; each head is one whole fruit at human head "
    "scale, never a human head.\n"
    "AUDIENCE: Rated 13+. Posted on TikTok, YouTube Shorts and Instagram Reels.\n"
    "FORMAT AND TIMING: 60-second episode (animated beats): each episode runs 55 to 75 seconds. Shots last 5 to 10 "
    "seconds. The characters speak their own lines on camera, inside the clips: one shot carries an exchange of 1 "
    "to 4 lines. No narrator: the characters' own lines carry the story."
)


# ====================================================================== E1: the block

def test_the_block_of_a_fruit_story_says_series_style_universe_audience_and_format():
    assert context.setup_for(FRUIT_STORY, episodes=8) == FRUIT_BLOCK
    hexes = context.setup_for(FRUIT_STORY, episodes=8, hexes=True)
    assert hexes == FRUIT_BLOCK.replace(
        "\nUNIVERSE:", "\nPALETTE COLOURS: Primary: #F2C14E, #E4572E, #3A7D44. Accents: #FFFFFF, #1E1E24.\nUNIVERSE:")


def test_a_human_cast_a_narrator_and_no_bible_yet_are_said_too():
    story = {"language": "en", "style_template_id": "cinematic_real", "episode_template_id": "narrated_drama_60s_v2",
             "generation_profile": {"pipeline": "v2"}, "narrator": {"enabled": True}}
    block = context.setup_for(story)
    assert "SERIES: A serialized vertical drama, written in English." in block
    assert "UNIVERSE: A human cast -- every character is a person." in block
    assert "AUDIENCE" not in block and "Logline" not in block  # nothing the bible has not written yet
    assert ("FORMAT AND TIMING: 60-second narrated drama (animated beats): each episode runs 58 to 78 seconds. Shots "
            "last 5 to 10 seconds. A narrator is heard over the picture and carries 60-85% of the words.") in block
    assert "Medium: a live-action cinematic film with real actors on real sets." in block


def test_the_gate_is_v2_or_writing_v3_and_a_legacy_story_gets_none():
    assert context.wants_setup(FRUIT_STORY)
    assert context.wants_setup({"generation_profile": {"writing": "v3"}, "seed_text": "Rida veut Marie-Jeanne."})
    # store.create stamps writing v3 on every story: without a brief that stamp is read nowhere.
    assert not context.wants_setup({"generation_profile": {"writing": "v3"}, "seed_text": None})
    assert context.wants_setup({"generation_profile": {"pipeline": "v2"}})
    assert not context.wants_setup(LEGACY_STORY) and context.setup_for(LEGACY_STORY) is None


def test_with_the_block_the_bible_section_is_the_premise_alone():
    plain = context.build_pack(language="fr", story=FRUIT_STORY)
    rich = context.build_pack(language="fr", story=FRUIT_STORY, setup=FRUIT_BLOCK)
    assert plain.bible.startswith(FRUIT_STORY["logline"]) and rich.bible == FRUIT_STORY["premise"]
    assert rich.setup == FRUIT_BLOCK and plain.setup is None


# ====================================================================== E2: every builder

CHARACTER = {"name": "Rida", "role": "lead", "one_line": "Un hacker.", "descriptor": "a kiwi head",
             "signature_items": ["a keyboard"],
             "personality": {"traits": ["insolent"], "wants": "gagner", "fears": "perdre", "speech_style": "vif"}}
PLACE = {"name": "Salle serveurs", "one_line": "Le coeur.", "descriptor": "a server room",
         "layout_notes": "racks left", "time_variants": ["day"]}
PROP = {"name": "Clé USB", "one_line": "Les secrets.", "descriptor": "a usb key"}
ENTRY = {"ep": 1, "function": "setup", "summary": "Rida attaque.", "open_hooks_in": [], "open_hooks_out": ["x"],
         "characters": []}
CARD = {"title": "Coeurs", "logline": "Rida aime.", "world": "Un siège.", "hook_formula": "h", "value": "v",
        "retention_mechanics": "r", "style_fit": "fruit_drama",
        "cast_sketch": [{"name": "Rida", "role": "lead", "one_line": "Un hacker."}]}

BUILDERS = {
    "C1v2": lambda pack: prompts.build_c1_v2(pack, style_ids=STYLE_IDS, batch=1, of=10, angle=prompts.C1_ANGLES[0]),
    "B1": lambda pack: prompts.build_b1(pack),
    "B1v3": lambda pack: prompts.build_b1_v3(pack),
    "B2": lambda pack: prompts.build_b2(pack),
    "B3": lambda pack: prompts.build_b3(pack),
    "K1": lambda pack: prompts.build_k1(pack, character=CHARACTER, cast_so_far=[]),
    "D1": lambda pack: prompts.build_d1(pack, character=CHARACTER, others=[]),
    "D2": lambda pack: prompts.build_d2(pack, character=CHARACTER, others=[], rendering="photoreal"),
    "P0": lambda pack: prompts.build_p0(pack, cast=[CHARACTER]),
    "P1": lambda pack: prompts.build_p1(pack, place=PLACE, places_so_far=[]),
    "R1": lambda pack: prompts.build_r1(pack, prop=PROP, cast=[CHARACTER]),
    "D3": lambda pack: prompts.build_d3(pack, place=PLACE, environment_rules="real sets", props=[]),
    "R1v2": lambda pack: prompts.build_r1v2(pack, prop=PROP, owner=None, cast=[], places=[]),
    "S1": lambda pack: prompts.build_s1(pack, episodes=3, cast=[CHARACTER], places=[PLACE]),
    "S1v2": lambda pack: prompts.build_s1(pack, episodes=3, cast=[CHARACTER], places=[PLACE], archetypes=[
        {"id": "betrayal", "premise": "Une trahison.", "pairs_well_with": []}]),
    "S2": lambda pack: prompts.build_s2(pack, entry=ENTRY, arc=[ENTRY], cast=[CHARACTER]),
    "D4": lambda pack: prompts.build_d4(pack, places=[PLACE], props=[]),
    "D5": lambda pack: prompts.build_d5(pack, ep=1, planned=3, entry=ENTRY, previous=None, cast=[CHARACTER],
                                        others=[], places=[PLACE], props=[]),
    "D6": lambda pack: prompts.build_d6(pack, objects=[], props=[], cast=["Rida"]),
}
# The writers whose reply is in the story's language end on the French elision sentence.
FRENCH_REPLY = {"C1v2", "B1", "B1v3", "B2", "B3", "K1", "D1", "P0", "S1", "S1v2", "S2", "D4", "D5", "D6"}


def _packs(story_with_world=True):
    story = dict(FRUIT_STORY, world={"setting_summary": "Un siège.", "rules": ["r"], "time_period": "now",
                                     "recurring_motifs": ["m"]}) if story_with_world else FRUIT_STORY
    kwargs = dict(language="fr", story=story, concept=dict(CARD, cast_sketch=CARD["cast_sketch"]),
                  template=FRUIT_DRAMA, brief_text="Rida veut Marie-Jeanne.")
    return context.build_pack(**kwargs), context.build_pack(setup=FRUIT_BLOCK, **kwargs)


@pytest.mark.parametrize("prompt_id", list(BUILDERS))
def test_every_set_up_writer_reads_the_block_on_v2_and_never_on_legacy(prompt_id):
    legacy, v2 = _packs()
    system_legacy, user_legacy, schema_legacy = BUILDERS[prompt_id](legacy)
    system_v2, user_v2, schema_v2 = BUILDERS[prompt_id](v2)
    assert context.SETUP_HEADING not in user_legacy and prompts._FR_ELISION_SENTENCE not in user_legacy
    assert user_v2.startswith(FRUIT_BLOCK + "\n\n")
    assert system_v2 == system_legacy and schema_v2 == schema_legacy
    assert (prompts._FR_ELISION_SENTENCE in user_v2) == (prompt_id in FRENCH_REPLY)
    # The one-line style gives way to ART STYLE (K1 keeps the voice direction alone).
    assert "Visual style:" not in user_v2
    assert ("Performance: over-acted telenovela" in user_v2) == (prompt_id == "K1")


def test_the_writers_that_lacked_the_world_get_it_and_the_timing_sentence_goes_to_the_season():
    legacy, v2 = _packs()
    for prompt_id in ("K1", "D2", "R1", "D3", "R1v2"):
        assert "World written so far:" not in BUILDERS[prompt_id](legacy)[1]
        assert "World written so far:" in BUILDERS[prompt_id](v2)[1], prompt_id
    for prompt_id, which in (("S1", "each episode"), ("S1v2", "each episode"), ("S2", "this episode"),
                             ("D5", "this episode")):
        assert prompts.SETUP_TIMING_SENTENCE.format(which=which) in BUILDERS[prompt_id](v2)[1]
    assert "Rendering: photoreal" in BUILDERS["D2"](legacy)[1] and "Rendering: photoreal" not in BUILDERS["D2"](v2)[1]


def test_c1j_shares_the_block_and_judges_the_universe_and_the_style():
    card = dict(CARD, cast_sketch=[dict(CARD["cast_sketch"][0], species="kiwi")])
    _s, plain, _ = prompts.build_c1j(language="fr", brief="Rida veut Marie-Jeanne.", card=card)
    _s, rich, schema = prompts.build_c1j(language="fr", brief="Rida veut Marie-Jeanne.", card=card, setup=FRUIT_BLOCK)
    assert rich == f"{FRUIT_BLOCK}\n\n{plain} {prompts.C1J_SETUP_CHECK}"
    assert "- Rida (lead, kiwi): Un hacker." in plain and plain.endswith(prompts.C1J_TOLERANCE)
    assert schema == prompts.c1j_schema()


def test_c1v2_with_the_story_look_names_it_as_the_only_style_fit():
    _legacy, v2 = _packs()
    _s, user, schema = prompts.build_c1_v2(v2, style_ids=["fruit_drama"], batch=1, of=10, angle=prompts.C1_ANGLES[0])
    assert "- style_fit: fruit_drama, the story's chosen visual style\n" in user and "best fits" not in user
    assert schema == schemas.c1_schema(["fruit_drama"])


# ====================================================================== E2: the steps hand it over

def test_the_concepts_step_sends_the_block_to_c1v2_and_c1j_and_fixes_style_fit(tmp_path):
    import test_story_concepts_brief as brief_tests
    import test_story_steps as tss
    from clipping.aistory.steps import concepts as concepts_step
    from clipping.aistory.store import StoryStore

    store = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = brief_tests._v3_story(store, pipeline="v2")
    # A card was chosen before: its bible never reaches the new cards' block.
    store.update(story_id, lambda doc: doc.update(title="Ancien Titre", logline="Une ancienne logline."),
                 now=brief_tests.NOW)
    ctx, _log = tss._ctx(store, story_id, params={"count": 1}, settings_env=brief_tests.SETTINGS)
    runner = tss.FakeRunner({"concepts": [brief_tests._card()]}, {"kept": True, "missing": []},
                            link=brief_tests.LINK)
    concepts_step.run(ctx, runner=runner)
    c1v2, c1j = runner.calls[0]["user"], runner.calls[1]["user"]
    assert c1v2.startswith(context.SETUP_HEADING) and c1j.startswith(context.SETUP_HEADING)
    assert c1v2.split("\n\n")[0] == c1j.split("\n\n")[0]  # one block, shared
    assert "SERIES: A serialized vertical drama, written in French.\n" in c1v2  # no earlier card's bible
    assert "Ancien Titre" not in c1v2 and "ancienne logline" not in c1v2
    assert "- style_fit: fruit_drama, the story's chosen visual style" in c1v2
    assert prompts.C1J_SETUP_CHECK in c1j


# ====================================================================== the input budgets (DEC-138's method)
#
# Each set-up writer on its French worst case with the block at its own worst: the style with the longest texts
# (fruit_drama: its medium and rendering), the format with the most to say (the confrontation: one place in real
# time) on native speech with a narrator, the longest universe line, 12 episodes, a 60-character title, a 30-word
# logline, a 15-word tone, 5 genre tags of 3 words, the audience -- the hexes on the look writers. The bible is the
# premise past its 120-word cut, the world at B2's caps; each builder's own inputs at their caps as its legacy
# worst case measures them (test_story_prompts, test_story_episode_prompt_budgets, test_story_season_archetypes).
# Budget = the worst case + 15 %, rounded up to ten: prompts.SETUP_INPUT_BUDGET.
# Re-recorded on purpose (DEC-305, plan 28): D3 2775 -> 2797, fruit_drama's environment_rules now say a 3D animated villa
# set; prompts.SETUP_INPUT_BUDGET["D3"] 3200 -> 3220 by the rule above.
# Re-recorded on purpose (plan 29 stage 4, DEC-308 point 4): the look writers' asks gained the description line:
# D2 3004 -> 3077, D3 2797 -> 2882, R1v2 2100 -> 2185; SETUP_INPUT_BUDGET 3460 -> 3540, 3220 -> 3320, 2420 -> 2520.

# Re-recorded on purpose (plan 32 stage 3, DEC-315): fruit_drama's look is the Pixar-style cartoon, 16 tokens
# shorter than the photoreal prose in the block (613 -> 597); every figure below fell by that, D2 (the worst of
# all styles) and K1 (the hexes) by less, and each SETUP_INPUT_BUDGET row follows by the rule above.

# Re-measured on purpose (pinned 2026-10-06, plan 32 stage 2: the fruit_drama recipe (DEC-315)): the worst block
# is now a recipe story's, its RECIPE section last (597 -> 777 with the hexes, 757 without); every figure below
# grew by it (about 180), C1v2 by its names line too (2,232 -> 2,521), and each SETUP_INPUT_BUDGET row follows by
# the rule above. A story without a recipe sends the block it always sent (tests/test_story_recipes.py's guard).
MEASURED_SETUP = {
    "C1v2": 2521, "C1J": 2072, "B1": 2135, "B1v3": 2235, "B2": 2540, "B3": 2800,
    "K1": 3824, "P0": 2289, "P1": 2746, "R1": 2892, "S1": 3316, "S1v2": 3732, "S2": 3385,
    "D1": 4060, "D2": 3253, "D3": 3046, "R1v2": 2348, "D4": 2646, "D5": 4222, "D6": 3774,
}


def _worst_story(universe=None, style_id="fruit_drama", recipe=None):
    return {"title": budgets._filler(8, 60), "language": "fr", "logline": budgets._fr(30), "tone": budgets._fr(15),
            "genre_tags": [budgets._fr(3)] * 5, "audience": {"age": "16+", "platforms": ["tiktok", "shorts", "reels"]},
            "narrator": {"enabled": True}, "style_template_id": style_id, "recipe": recipe,
            "generation_profile": {"pipeline": "v2", "universe": universe}}


@functools.lru_cache(maxsize=None)
def worst_block(hexes=False):
    """The longest set-up block any shipped style, universe, format, speech mode and recipe makes (plan 32
    stage 2: with or without a recipe -- the worst is with one, its RECIPE section)."""
    blocks = []
    for style_id in templates.list_style_ids():
        style = templates.load_style(style_id)
        for universe in [None] + [item["id"] for item in templates.load_universes()]:
            for recipe in [None] + recipes.list_recipe_ids():
                story = _worst_story(universe, style_id, recipe)
                for template_id in templates.list_episode_template_ids():
                    template = templates.load_episode_template(template_id)
                    for native in (True, False):
                        blocks.append(context.setup_context(story, style, template, {"native_speech": native},
                                                            episodes=12, hexes=hexes))
    return max(blocks, key=len)


def _story():
    """The bible past its cut and the world at B2's caps (the premise alone shows with the block)."""
    story = budgets._knowledge_story()
    story["premise"] = budgets._fr(130)
    return story


def _pack(hexes=False, **kwargs):
    kwargs.setdefault("story", _story())
    return context.build_pack(language="fr", setup=worst_block(hexes), **kwargs)


def _brief():
    return budgets._filler(400, round(400 * 5.8))


def _concept_at_caps():
    return {"title": budgets._fr(8), "logline": budgets._fr(30), "world": budgets._fr(60),
            "cast_sketch": [{"name": budgets._name(30), "role": "support", "one_line": budgets._fr(25)}] * 5,
            "hook_formula": budgets._fr(25), "value": budgets._fr(25), "retention_mechanics": budgets._fr(25)}


def _cast(n=None):
    """*n* written characters at their caps; by default a story's whole cast but the one being written
    (``workflow.MAX_CAST`` - 1)."""
    from clipping.aistory import workflow

    n = workflow.MAX_CAST - 1 if n is None else n
    return [{"name": budgets._name(30), "role": "support", "one_line": budgets._fr(20),
             "descriptor": budgets._fr(45), "signature_items": [budgets._fr(8)] * 3} for _ in range(n)]


def _places(n=8):
    return [{"name": budgets._name(30), "one_line": budgets._fr(20), "descriptor": budgets._fr(45)} for _ in range(n)]


def _worst_species_block():
    world = max((dict(item, head_kind="fruit or vegetable") for item in templates.load_universes()),
                key=lambda item: len(", ".join(item["species"])))
    return universes.cast_species_block(world, taken=world["species"][:11], own=budgets._fr(4))


def _c1v2():
    universe = max(templates.load_universes(), key=lambda item: len(", ".join(item["species"])))
    batch = universes.assign_species("d71852710962", 3, universe["species"])
    species = universes.species_block(universe, batch_species=batch, position=9)
    avoid = [budgets._filler(8, round(8 * 5.8)) for _ in range(24)]
    pack = context.build_pack(language="fr", template=FRUIT_DRAMA, brief_text=_brief(), avoid_titles=avoid,
                              universe=species, setup=worst_block())
    # Plan 32 stage 2: the recipe's names line in the cast ask (a recipe story's C1v2 is the longer one).
    return prompts.build_c1_v2(pack, style_ids=["storybook_watercolor"], batch=10, of=10,
                               angle=max(prompts.C1_ANGLES, key=len),
                               recipe=max((recipes.load(rid) for rid in recipes.list_recipe_ids()),
                                          key=lambda recipe: len(recipes.c1v2_names_ask(recipe))))


def _c1j():
    card = _concept_at_caps()
    card["cast_sketch"] = [dict(member, species=budgets._fr(4)) for member in card["cast_sketch"]]
    return prompts.build_c1j(language="fr", brief=_brief(), card=card, setup=worst_block())


def _regenerate(fields):
    return {"field": "x", "current": fields, "note": budgets.NOTE}


def _b(part):
    pack = _pack(concept=_concept_at_caps(), note=budgets.NOTE)
    if part == "B1v3":
        pack = _pack(concept=_concept_at_caps(), brief_text=_brief())
        return prompts.build_b1_v3(pack)
    builder = {"B1": prompts.build_b1, "B2": prompts.build_b2, "B3": prompts.build_b3}[part]
    current = {"B1": {"logline": budgets._fr(30), "premise": budgets._fr(120), "tone": budgets._fr(15),
                      "genre_tags": [budgets._fr(3)] * 5},
               "B2": {"setting_summary": budgets._fr(80), "rules": [budgets._fr(25)] * 6,
                      "time_period": budgets._fr(6), "recurring_motifs": [budgets._fr(8)] * 3},
               "B3": {"themes_and_values": [budgets._fr(12)] * 4, "why_come_back": [budgets._fr(20)] * 3}}[part]
    return builder(pack, regenerate=_regenerate(current))


def _k1():
    pack = _pack(hexes=True, template=FRUIT_DRAMA, note=budgets.NOTE, universe=_worst_species_block())
    character = {"name": budgets._name(30), "role": "support", "archetype": budgets._fr(8),
                 "one_line": budgets._fr(25), "signature_hint": budgets._fr(8)}
    current = {"descriptor": budgets._fr(45), "signature_items": [budgets._fr(8)] * 3,
               "personality": {"traits": [budgets._fr(4)] * 5, "wants": budgets._fr(25), "fears": budgets._fr(25),
                               "speech_style": budgets._fr(25)}}
    return prompts.build_k1(pack, character=character, cast_so_far=_cast(), upload_notes=budgets._fr(40),
                            regenerate=_regenerate(current))


def _p0():
    from clipping.aistory import workflow

    return prompts.build_p0(_pack(), cast=[{"name": c["name"], "signature_items": c["signature_items"]}
                                           for c in _cast(workflow.MAX_CAST)])


def _p1():
    pack = _pack(template=FRUIT_DRAMA, note=budgets.NOTE)
    current = {"descriptor": budgets._fr(45), "layout_notes": budgets._fr(60), "time_variants": ["day"] * 3}
    return prompts.build_p1(pack, place={"name": budgets._name(30), "one_line": budgets._fr(20)},
                            places_so_far=_places(), regenerate=_regenerate(current))


def _r1():
    pack = _pack(template=FRUIT_DRAMA, note=budgets.NOTE)
    return prompts.build_r1(pack, prop={"name": budgets._name(30), "one_line": budgets._fr(20)}, cast=_cast(),
                            regenerate=_regenerate({"descriptor": budgets._fr(30), "owner": budgets._name(30)}))


def _arc():
    return [{"ep": i + 1, "function": "escalation", "summary": budgets._fr(25)} for i in range(12)]


def _s1(archetypes=False):
    from clipping.aistory.steps import season as season_step

    picks = season_step._archetype_pick_list("fr") if archetypes else None
    return prompts.build_s1(_pack(), episodes=12, cast=_cast(), places=_places(), archetypes=picks)


def _s2():
    import test_story_season_archetypes as archetype_tests

    arc = _arc()
    current = {"summary": budgets._fr(60), "open_hooks_in": [budgets._fr(15)] * 3,
               "open_hooks_out": [budgets._fr(15)] * 3, "characters": [budgets._name(30)] * 5}
    return prompts.build_s2(_pack(note=budgets.NOTE), entry=arc[0], arc=arc, cast=_cast(),
                            regenerate=_regenerate(current), archetype=archetype_tests._longest_archetype_line("fr"))


def _with_block(fixture, hexes=False):
    """*fixture* (a legacy worst case of test_story_episode_prompt_budgets) rebuilt with the block in its pack
    and the world at B2's caps (the look writers read it with the block)."""
    original = context.build_pack

    def build_pack(**kwargs):
        story = dict(kwargs.get("story") or {})
        story.setdefault("world", budgets._knowledge_story()["world"])
        kwargs["story"] = story
        return original(setup=worst_block(hexes), **kwargs)

    context.build_pack = build_pack
    try:
        return fixture()
    finally:
        context.build_pack = original


WORST = {
    "C1v2": _c1v2, "C1J": _c1j,
    "B1": lambda: _b("B1"), "B1v3": lambda: _b("B1v3"), "B2": lambda: _b("B2"), "B3": lambda: _b("B3"),
    "K1": _k1, "P0": _p0, "P1": _p1, "R1": _r1, "S1": _s1, "S1v2": lambda: _s1(archetypes=True), "S2": _s2,
    "D1": lambda: _with_block(budgets._d1),
    "D2": lambda: max((_with_block(functools.partial(budgets._d2, style), hexes=True) for style in budgets.ALL_STYLES),
                      key=lambda triple: estimate_tokens(*triple[:2])),
    "D3": lambda: max((_with_block(functools.partial(budgets._d3, style), hexes=True) for style in budgets.ALL_STYLES),
                      key=lambda triple: estimate_tokens(*triple[:2])),
    "R1v2": lambda: max((_with_block(functools.partial(budgets._r1v2, style), hexes=True)
                         for style in budgets.ALL_STYLES), key=lambda triple: estimate_tokens(*triple[:2])),
    "D4": lambda: _with_block(budgets._d4), "D5": lambda: _with_block(budgets._d5),
    "D6": lambda: _with_block(budgets._d6),
}


def test_the_worst_block_is_the_fruit_style_on_the_confrontation_format():
    block = worst_block(hexes=True)
    assert "ART STYLE: Fruit Drama." in block and "PALETTE COLOURS:" in block
    assert "Each episode is one continuous scene in one place, in real time." in block
    assert "\nRECIPE: Fruit drama." in block  # plan 32 stage 2: the worst block is a recipe story's
    assert estimate_tokens(block) == 777  # plan 32 stage 2: with the RECIPE section (597 without a recipe)


@pytest.mark.parametrize("prompt_id", list(MEASURED_SETUP))
def test_each_set_up_writer_worst_case_measures_what_is_recorded_and_fits_its_budget(prompt_id):
    system, user, _schema = WORST[prompt_id]()
    assert worst_block(prompt_id in ("K1", "D2", "D3", "R1v2")) in user
    tokens = estimate_tokens(system, user)
    assert tokens == MEASURED_SETUP[prompt_id]
    assert prompts.carries_setup(user)
    budget = prompts.input_budget(prompt_id, setup=True)
    assert budget == prompts.SETUP_INPUT_BUDGET[prompt_id] == -(-round(tokens * 1.15, 1) // 10) * 10
    context.check_budget(system, user, budget=budget)
    # A legacy prompt of the same id keeps its own budget, exactly as before.
    legacy = prompts.input_budget(prompt_id)
    assert legacy == prompts.INPUT_BUDGET.get(prompt_id, context.PACK_TOKEN_BUDGET) <= budget
