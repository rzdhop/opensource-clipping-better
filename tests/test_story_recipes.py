"""The genre recipe (AI Story plan 32 stage 2, DEC-315): the fruit_drama
record and the writers of a story that names it.

The human's decisions: French telenovela pun names on the species
(``-ito/-ita``, no brands, no plain human first names, one species each),
a fixed cast of 5 to 8 with roles, the beats (recap from episode 2 in at most
6 words, confrontation, the shareable peak, a cliffhanger of at most 40
words), one conflict, the closing "Team X ou Team Y ?" question, the end card
"Partie N demain", over-acted telenovela voices, the content guardrails on by
default.

Pinned here: the record loads and validates; ``recipes.for_story`` is the
gate (None for a story without a recipe, a null one or an unknown id -- never
an error at prompt time); ``store.create`` takes only a shipped recipe; the
names check; each writer carries the recipe only when it is on (the set-up
block's RECIPE section, C1v2's names line and check, E1v3's beats, E3v3's
teaser, M1's line, the pinned comment's question) and the steps hand it over;
THE GUARD -- a story without a recipe sends prompts byte-identical to the ones
recorded before this stage existed; and the recipe-on goldens.

Pure and offline; stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

import test_story_prompts_v3 as v3_tests
from clipping.aistory import context, prompts, recipes, schemas, templates, universes
from test_story_episode_steps import hermetic, store  # noqa: F401 -- phase 3's fixtures, used as they are

FRUIT = recipes.load("fruit_drama")
NOW = "2026-10-06T08:00:00Z"


# ====================================================================== the record

def test_the_shipped_recipe_loads_validates_and_says_what_the_human_decided():
    assert recipes.list_recipe_ids() == ["fruit_drama"]
    assert schemas.recipe_errors(FRUIT) == []
    assert (FRUIT["$schema"], FRUIT["id"], FRUIT["version"]) == ("recipe_v1", "fruit_drama", 1)
    assert FRUIT["naming"]["style"] == "-ito/-ita"
    assert set(FRUIT["naming"]["forbidden"]) == {"brands", "plain human names"}
    assert {example["name"] for example in FRUIT["naming"]["examples"]} >= {"Fraisita", "Bananito", "Citronello",
                                                                             "Avocadina", "Kiwito", "Mangualdine"}
    assert FRUIT["cast"]["size"] == [5, 8] and "schemer" in [role["role"] for role in FRUIT["cast"]["roles"]]
    assert FRUIT["beats"] == {"order": ["recap", "confrontation", "peak", "cliffhanger"], "recap_max_words": 6,
                              "recap_from_episode": 2, "peak_note": "the shareable moment",
                              "cliffhanger_max_words": 40, "one_conflict": True}
    assert FRUIT["closing_question"] == {"template": "Team {a} ou Team {b} ?", "where": ["teaser", "pinned_comment"]}
    assert FRUIT["end_card"] == {"text": "Partie {n} demain", "seconds": 1.5}
    assert FRUIT["format"] == {"seconds": [60, 90], "scenes": [4, 6]}
    assert FRUIT["cadence"] == "one episode a day" and len(FRUIT["plot_seeds"]) == 5
    assert "no sexualisation" in FRUIT["guardrails"]
    # Every example name passes the recipe's own names check.
    assert [recipes.name_refusal(example["name"], FRUIT) for example in FRUIT["naming"]["examples"]] == [""] * 6


@pytest.mark.parametrize("change, mentions", [
    (lambda doc: doc.pop("guardrails"), "$.guardrails: required property missing"),
    (lambda doc: doc.update(extra=1), "$.extra: additional property not allowed"),
    (lambda doc: doc["cast"].update(size=[8, 5]), "$.cast.size: 8 > 5"),
    (lambda doc: doc["beats"].update(order=["recap", "cliffhanger", "peak"]), "the cliffhanger comes last"),
    (lambda doc: doc["beats"].update(cliffhanger_max_words=41), "41 > maximum 40"),
    (lambda doc: doc["closing_question"].update(template="Team {a} ?"), "names {a} and {b}"),
    (lambda doc: doc["end_card"].update(text="Partie demain"), "$.end_card.text: names {n}"),
    (lambda doc: doc["naming"]["examples"].append({"name": "Haribito", "species": "haribo"}), "brand 'haribo'"),
])
def test_a_broken_recipe_is_refused_with_a_readable_error(change, mentions):
    doc = copy.deepcopy(FRUIT)
    change(doc)
    assert any(mentions in error for error in schemas.recipe_errors(doc)), schemas.recipe_errors(doc)


def test_load_refuses_an_unknown_or_malformed_id_before_any_path_is_built():
    for bad in ("nope", "../fruit_drama", "Fruit_drama", "", None, 3):
        with pytest.raises(KeyError):
            recipes.load(bad)
    first = recipes.load("fruit_drama")
    first["cast"]["size"][1] = 99
    assert recipes.load("fruit_drama")["cast"]["size"] == [5, 8]  # a deep copy every time


@pytest.mark.parametrize("story", [None, {}, {"recipe": None}, {"recipe": ""}, {"recipe": "unknown_recipe"},
                                   {"recipe": "../fruit_drama"}, {"recipe": 3}])
def test_for_story_is_none_without_a_shipped_recipe_and_never_raises(story):
    assert recipes.for_story(story) is None


def test_for_story_is_the_recipe_a_story_names():
    assert recipes.for_story({"recipe": "fruit_drama"}) == FRUIT


def test_the_store_takes_only_a_shipped_recipe(tmp_path):
    from clipping.aistory.store import StoryStore

    stories = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    assert stories.create(language="fr", recipe="fruit_drama", now=NOW)["recipe"] == "fruit_drama"
    assert stories.create(language="fr", now=NOW)["recipe"] is None
    with pytest.raises(ValueError, match=r"unknown recipe 'telenovela_x' \(shipped: fruit_drama\)"):
        stories.create(language="fr", recipe="telenovela_x", now=NOW)
    with pytest.raises(ValueError, match="lowercase letters"):
        stories.create(language="fr", recipe="Fruit-Drama", now=NOW)
    assert len(stories.list()) == 2  # nothing created for a refused recipe


# ====================================================================== the names check

@pytest.mark.parametrize("name, why", [
    ("Fraisita", ""), ("Bananito", ""), ("Mangualdine", ""), ("Leandro", ""), ("Ananas", ""), ("Chiquitita", ""),
    ("Mariella", ""), ("Pierrot-Pêche", ""),
    ("Marie", "plain human first name"), ("marie", "plain human first name"), ("Léa", "plain human first name"),
    ("LEA", "plain human first name"), ("Chloé", "plain human first name"), ("Tante Sophie", "plain human first name"),
    ("Jean-Banane", "plain human first name"), ("John", "plain human first name"),
    ("Chiquita", "brand Chiquita"), ("Dole", "brand Dole"), ("Del Monte", "brand Del Monte"),
    ("Delmonte Kiwi", "brand Del Monte"), ("Capri Sun", "brand Capri-Sun"), ("Pom’Potes", "brand Pom'Potes"),
    ("Bonne-Maman Fraise", "brand Bonne Maman"), ("Minute Maid", "brand Minute Maid"), ("Fanta", "brand Fanta"),
])
def test_the_names_check_refuses_a_plain_human_first_name_or_a_brand_whole_word(name, why):
    refusal = recipes.name_refusal(name, FRUIT)
    if not why:
        assert refusal == ""
    else:
        assert why in refusal and refusal.startswith(repr(name))
        assert "rename this character with a French pun on its own species, -ito/-ita style" in refusal


def test_a_first_name_the_brief_gives_is_kept_and_a_brand_never_is():
    brief = "Marie, la matriarche du verger, découvre que Chiquita lui ment."
    assert recipes.name_refusal("Marie", FRUIT, brief=brief) == ""
    assert "plain human first name" in recipes.name_refusal("Paul", FRUIT, brief=brief)
    assert "brand Chiquita" in recipes.name_refusal("Chiquita", FRUIT, brief=brief)


def _card(*names):
    return {"concepts": [{
        "title": "Le verger des secrets", "logline": "Fraisita découvre que sa meilleure amie lui ment.",
        "world": "Un verger familial où chaque héritage cache un mensonge.",
        "cast_sketch": [{"name": name, "role": "support", "one_line": "Un fruit du verger.", "species": "kiwi"}
                        for name in names],
        "hook_formula": "Un testament lu à voix haute.", "value": "La loyauté a un prix.",
        "retention_mechanics": "Qui est le vrai héritier ?", "style_fit": "fruit_drama"}]}


def test_c1v2_refuses_the_names_only_when_the_recipe_is_on():
    card = _card("Fraisita", "Marie", "Chiquita")
    off = prompts.c1v2_errors(card, style_ids=["fruit_drama"], brief="Fraisita veut le verger.", universe=True)
    assert off == []
    on = prompts.c1v2_errors(card, style_ids=["fruit_drama"], brief="Fraisita veut le verger.", universe=True,
                             recipe=FRUIT)
    assert on == [
        "$.concepts[0].cast_sketch[1].name: 'Marie' is a plain human first name; rename this character with a "
        "French pun on its own species, -ito/-ita style (like Fraisita or Bananito)",
        "$.concepts[0].cast_sketch[2].name: 'Chiquita' carries the brand Chiquita; rename this character with a "
        "French pun on its own species, -ito/-ita style (like Fraisita or Bananito), no brand",
    ]
    assert prompts.c1v2_errors(_card("Fraisita", "Bananito", "Kiwito"), style_ids=["fruit_drama"],
                               brief="Fraisita veut le verger.", universe=True, recipe=FRUIT) == []


# ====================================================================== THE GUARD (recipe-less = byte-identical)
#
# A story without a recipe (no field, null, or an id no recipe has) must send exactly the prompts it sent before
# plan 32 stage 2. Each guarded prompt is built on a recipe-less French fixture and pinned by the sha256 of
# json.dumps([system, user, schema]) (a block alone: of its text). recorded at 8b4c352, plan 32 stage 2 -- by these
# same builders before the recipe code existed. Never re-pin one of these: a change here means the recipe gate
# leaks into a story without a recipe; fix the gate.

GUARD_STORY = {
    "story_id": "d71852710962", "title": "Cœurs Sous Clé", "language": "fr",
    "seed_text": "Fraisita, héritière d'un verger, découvre que sa meilleure amie Bananita lui ment.",
    "logline": "Fraisita découvre que sa meilleure amie lui ment sur l'héritage du verger.",
    "premise": "Fraisita hérite du verger; Bananita convoite sa place.",
    "tone": "Tendu, mélodramatique.", "genre_tags": ["Telenovela", "Drame familial"],
    "audience": {"age": "13+", "platforms": ["tiktok", "shorts", "reels"]},
    "style_template_id": "fruit_drama", "episode_template_id": "serial_60s_v2",
    "generation_profile": {"tier": 3, "pipeline": "v2", "budget_profile": "native_speech", "universe": "fruits",
                           "writing": "v3"},
    "narrator": {"enabled": False},
}
GUARD_BRIEF = GUARD_STORY["seed_text"]
GUARD_CHARACTER = {"name": "Fraisita", "role": "lead", "archetype": "l'héritière",
                   "one_line": "Héritière du verger, elle découvre un mensonge.", "signature_hint": "un médaillon"}
GUARD_CAST = [{"name": "Bananita", "role": "support", "one_line": "Sa meilleure amie, qui ment.",
               "descriptor": "banane en tailleur jaune", "signature_items": ["un carnet"]}]
GUARD_M1 = dict(ep=1, story_title="Cœurs Sous Clé", episode_title="Le médaillon",
                hook_text="Elle ment depuis le début", teaser="Demain, Bananita avoue tout.",
                cast_names=["Fraisita", "Bananita"])

# recorded at 8b4c352, plan 32 stage 2 (before the recipe code existed).
GUARD_SHAS = {
    "setup": "d468e334d3d43ec07e02b8443b3c10524b42366e2a1ae4a1945f8bd91e168c1f",
    "setup-hexes": "6fbee06f379f6e15db920cf542183bb4837513db8bd17ebe416c261e94c47556",
    "C1v2": "6010c1243d6e175cd3b1ce7378e32191c2ff95b544902e30ccd6102b8019f4fc",
    "K1": "dca5f8a1e35e0eb43d5a8cec05dab033fa8fd28957bea8f5c8975b098848afe0",
    "E1v3": "a0a951ea281f10a6bc6742498b5f5cab3434b88719c0f9489f5f7ddfe271c583",
    "E1v3-ep2": "97726bb0d3cc532ce2c991f0b40327ad2e1c8c4087b4bf0d009530483941769a",
    "E3v3": "3bf0fd32e3f630e798d14b79d6f751f447529d65577b32deb030e02a51a7315f",
    "E3v3-ep2-teaser": "2b04bcb56105df096f1e22c53ed24d04181cdb271255fcdf5bd103c291c85111",
    "M1": "399ea74bece8ef7f95ebf46797753f6e2ce3935f541e4cef4bed2e23189ed1cd",
}


def _blob(value) -> str:
    text = value if isinstance(value, str) else json.dumps(list(value), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fruits():
    return templates.universe("fruits")


def _guard_c1v2(story, **extra):
    style = templates.load_style("fruit_drama")
    setup = context.setup_for(story, lock=style)
    batch = universes.assign_species(story["story_id"], 0, _fruits()["species"])
    species = universes.species_block(_fruits(), batch_species=batch, position=0)
    pack = context.build_pack(language="fr", template=style, brief_text=GUARD_BRIEF, universe=species, setup=setup)
    return prompts.build_c1_v2(pack, style_ids=["fruit_drama"], batch=1, of=10, angle=prompts.C1_ANGLES[0], **extra)


def _guard_k1(story):
    style = templates.load_style("fruit_drama")
    world = dict(_fruits(), head_kind="fruit or vegetable")
    pack = context.build_pack(language="fr", story=story, template=style,
                              universe=universes.cast_species_block(world, taken=["banana"], own="strawberry"),
                              setup=context.setup_for(story, hexes=True, episodes=8))
    return prompts.build_k1(pack, character=GUARD_CHARACTER, cast_so_far=GUARD_CAST)


def _guard_e3v3(**extra):
    plans = {"hook": v3_tests._fixture_plan(v3_tests.OUTLINE[0]),
             "cliffhanger": v3_tests._fixture_plan(v3_tests.OUTLINE[3])}
    return v3_tests._e3v3(plans=plans, word_budgets={key: plan["max_words"] for key, plan in plans.items()}, **extra)


def _guard_m1(story, **extra):
    return prompts.build_m1(context.build_pack(language="fr", story=story), platform="tiktok", **GUARD_M1, **extra)


def guard_prompts(story, **extra):
    """Every guarded prompt of *story*; *extra* is what the steps hand the recipe-aware builders
    (``recipe=recipes.for_story(story)``)."""
    return {
        "setup": context.setup_for(story),
        "setup-hexes": context.setup_for(story, hexes=True, episodes=8),
        "C1v2": _guard_c1v2(story, **extra),
        "K1": _guard_k1(story),
        "E1v3": v3_tests._e1v3(**extra),
        "E1v3-ep2": v3_tests._e1v3(ep=2, **extra),
        "E3v3": _guard_e3v3(**extra),
        "E3v3-ep2-teaser": v3_tests._e3v3(ep=2, part="teaser", **extra),
        "M1": _guard_m1(story, **extra),
    }


def _without_recipe_field():
    return {key: value for key, value in GUARD_STORY.items() if key != "recipe"}


@pytest.mark.parametrize("story", [_without_recipe_field(), dict(GUARD_STORY, recipe=None),
                                   dict(GUARD_STORY, recipe="unknown_recipe")],
                         ids=["no-field", "null", "unknown-id"])
def test_the_guard_a_story_without_a_recipe_sends_the_prompts_it_sent_before_the_recipe(story):
    # As the steps call them now: the gate's None handed in.
    sent = guard_prompts(story, recipe=recipes.for_story(story))
    assert {key: _blob(value) for key, value in sent.items()} == GUARD_SHAS
    # As any older caller calls them: no recipe argument at all.
    assert {key: _blob(value) for key, value in guard_prompts(story).items()} == GUARD_SHAS


def test_the_guard_the_pinned_comment_without_a_recipe_is_the_teaser_and_the_call():
    from clipping.aistory.steps import metadata

    ec = SimpleNamespace(story=_without_recipe_field(), cast=[{"char_id": "char_a", "name": "Fraisita"},
                                                               {"char_id": "char_b", "name": "Bananita"}])
    script = {"next_episode_teaser": "Demain, Bananita avoue tout.", "scenes": [{"characters": ["char_a", "char_b"]}]}
    assert metadata.closing_question(ec, script) is None
    assert metadata.pinned_comment(script["next_episode_teaser"], 1, "fr", None) == (
        "Demain, Bananita avoue tout. Commente « PARTIE 2 » pour la suite →")


# ====================================================================== the recipe on: goldens

# pinned 2026-10-06, plan 32 stage 2: the fruit_drama recipe (DEC-315)
RECIPE_SHAS = {
    "setup": "74b7765841efdcfd444362b54f71f6e0c2942481e8dd96d0dff6f4cc06709986",
    "setup-hexes": "43b048a113d4f20120bc510b7952deebdd1301dc494ef80756eac94266b75bf3",
    "C1v2": "34734d236f4b6390fc3d62d83a62387886b9aedbe1b72a29b6512ab645109a4c",
    "K1": "20a1d088b66c98af8f924a804d984f994498bad803aa7e70b1e68a3d9945afe5",
    "E1v3": "b8ec1171c1711366d41af00973037bb2af28a578d19b5239e99b4b62c2f61029",
    "E1v3-ep2": "992922a0608ac206da1a8452858e099ffad39cf731b83561b9857863f4c4ce92",
    "E3v3": "4b56f34b7241d3bef61247b1eec7ad780dfa011a29b4acd850b3d07e62c014ff",
    "E3v3-ep2-teaser": "94819e83a537d4190f6fde9df72759e95bc7eb39c311d1ce1c35811032c528a3",
    "M1": "aeca09e0d91c1b8c532d1d99959c58bb0b98ef83f3ee066c0ca3c7fd564d9e42",
}

RECIPE_SECTION = (
    "RECIPE: Fruit drama. Names: a French telenovela pun on the character's own species, -ito/-ita style "
    "(Fraisita, Bananito, Citronello); one species per character; never a brand or a plain human first name. "
    "Fixed cast of 5 to 8 recurring characters: the matriarch, the villain, the schemer, the innocent, the heir, "
    "the best friend and the newcomer. Episodes: 4 to 6 scenes, one conflict: a recap of at most 6 words (from "
    "episode 2), confrontation, peak (the shareable moment), a cliffhanger of at most 40 words, then \"Team X ou "
    "Team Y ?\" and the end card \"Partie N demain\". Voices: over-acted telenovela, crisp and quick. Always: no "
    "sexist trope; no racist trope; no sexualisation; no one judged by body, looks or love life."
)


def _recipe_story():
    return dict(GUARD_STORY, recipe="fruit_drama")


def _recipe_prompts():
    story = _recipe_story()
    return guard_prompts(story, recipe=recipes.for_story(story))


def test_the_recipe_on_prompts_are_byte_identical_to_their_goldens():
    assert {key: _blob(value) for key, value in _recipe_prompts().items()} == RECIPE_SHAS


def test_the_set_up_block_ends_on_the_recipe_section_only_when_the_recipe_is_on():
    on, off = context.setup_for(_recipe_story()), context.setup_for(GUARD_STORY)
    assert on == off + "\n" + RECIPE_SECTION
    assert "RECIPE" not in off
    assert context.estimate_tokens(RECIPE_SECTION) <= 180
    # The pure function reads the story's recipe the same way.
    style = templates.load_style("fruit_drama")
    assert context.setup_context(_recipe_story(), style, None, {}).endswith("\n" + RECIPE_SECTION)
    assert "RECIPE" not in context.setup_context(dict(GUARD_STORY, recipe="nope"), style, None, {})


def test_every_set_up_writer_reads_the_recipe_through_the_block():
    sent = _recipe_prompts()
    for key in ("C1v2", "K1"):
        assert RECIPE_SECTION in sent[key][1] and prompts.carries_setup(sent[key][1])
    assert RECIPE_SECTION in sent["setup-hexes"]


def test_c1v2_asks_for_the_names_and_the_fixed_cast_only_when_the_recipe_is_on():
    _s, on, schema_on = _recipe_prompts()["C1v2"]
    _s, off, schema_off = guard_prompts(GUARD_STORY)["C1v2"]
    line = recipes.c1v2_names_ask(FRUIT)
    assert line == (
        "- names: a French telenovela pun on the character's own species, -ito/-ita style (Fraisita the strawberry, "
        "Bananito the banana); one species per character; never a brand or a plain human first name (a name the "
        "brief gives is kept). The card's characters open the series' fixed cast of 5 to 8 recurring characters: "
        "give 5, in its roles (the matriarch, the villain, the schemer, the innocent, the heir, the best friend and "
        "the newcomer)\n")
    assert line not in off
    assert on.replace(line, "").replace("\n" + RECIPE_SECTION, "") == off
    assert on.index("- cast_sketch:") < on.index(line) < on.index("- hook_formula:")
    assert schema_on == schema_off


def test_e1v3_adds_the_beats_after_its_shape_line_only_when_the_recipe_is_on():
    beats_1, beats_2 = recipes.e1_beats_line(FRUIT, 1), recipes.e1_beats_line(FRUIT, 2)
    assert beats_2 == (
        "The recipe's beats, in order: the recap (at most 6 words on screen), the confrontation (the setup and rising "
        "scenes), the peak (the shareable moment: the scene a viewer sends a friend), the cliffhanger (its reveal at "
        "most 40 words). One conflict carries the whole episode: no subplot, no second conflict. The recipe keeps an "
        "episode to 4 to 6 scenes.\n\n")
    assert "recap" not in beats_1 and beats_1.startswith("The recipe's beats, in order: the confrontation")
    for key, beats in (("E1v3", beats_1), ("E1v3-ep2", beats_2)):
        _s, on, schema_on = _recipe_prompts()[key]
        _s, off, schema_off = guard_prompts(GUARD_STORY)[key]
        assert beats not in off and on.replace(beats, "") == off and schema_on == schema_off
        assert on.index(beats) == on.index("The hook scene:") - len(beats)


def test_e3v3_teaser_ends_on_the_team_question_only_when_the_recipe_is_on():
    ask = recipes.teaser_ask(FRUIT)
    assert ask == ("- teaser: one sentence about the next episode, at most 15 words, story language, ending on the "
                   "question \"Team X ou Team Y ?\" (X and Y: the two characters this episode sets against each other, "
                   "by name)")
    _s, on, schema_on = _recipe_prompts()["E3v3-ep2-teaser"]
    _s, off, schema_off = guard_prompts(GUARD_STORY)["E3v3-ep2-teaser"]
    plain = prompts._E3_V3_KEY_ASKS["teaser"]
    assert plain in off and ask not in off
    assert on == off.replace(plain, ask) and schema_on == schema_off
    # Every framing part at once: only the teaser's ask moves, the cliffhanger stays at 40 words.
    _s, framing, _ = _recipe_prompts()["E3v3"]
    assert framing == guard_prompts(GUARD_STORY)["E3v3"][1].replace(plain, ask)
    assert "- cliffhanger: reveal (story language, at most 40 words)" in framing
    # A call without the teaser carries nothing of the recipe.
    assert v3_tests._e3v3(part="hook", recipe=FRUIT) == v3_tests._e3v3(part="hook")


def test_m1_says_the_teaser_carries_the_question_only_when_the_recipe_is_on():
    _s, on, schema_on = _recipe_prompts()["M1"]
    _s, off, schema_off = guard_prompts(GUARD_STORY)["M1"]
    line = recipes.m1_line(FRUIT)
    assert line == ("The teaser ends on the series' side-taking question (Team X ou Team Y ?); the app adds it after "
                    "your description and pins it in the comments: do not ask it again.\n\n")
    assert line not in off and on.replace(line, "") == off and schema_on == schema_off
    assert on.index(line) + len(line) == on.index("TikTok rules:")
    for forbidden in ("duration", "second", "timestamp", "path", "url", "file"):  # M1's own rule
        assert forbidden not in line.lower()


@pytest.mark.parametrize("teaser, names, expected", [
    ("Demain, Bananita avoue tout.", ["Fraisita", "Bananita", "Kiwito"], "Team Fraisita ou Team Bananita ?"),
    ("Demain tout change. Team Fraisita ou Team Bananita ?", ["Fraisita", "Bananita"], None),
    ("Demain, team Kiwito ou team Citronello ?", ["Fraisita", "Bananita"], None),
    ("Demain, Bananita avoue tout.", ["Fraisita"], None),
    (None, ["Fraisita", "Bananita"], "Team Fraisita ou Team Bananita ?"),
])
def test_the_pinned_comment_asks_the_team_question_when_the_teaser_lacks_it(teaser, names, expected):
    from clipping.aistory.steps import metadata

    assert recipes.closing_question(FRUIT, teaser, names) == expected
    assert recipes.closing_question(None, teaser, names) is None
    comment = metadata.pinned_comment(teaser, 3, "fr", expected)
    call = "Commente « PARTIE 4 » pour la suite →"
    said = " ".join(part for part in (teaser or "", expected or "") if part)
    assert comment == (f"{said} {call}" if said else call)


def test_the_metadata_step_reads_the_question_from_the_story_and_hands_m1_the_recipe(monkeypatch):
    from clipping.aistory.steps import llm_call, metadata

    script = {"title": "Le médaillon", "hook": {"on_screen_text": "Elle ment"}, "rev": 1,
              "next_episode_teaser": "Demain, Bananita avoue tout.",
              "scenes": [{"characters": ["char_a", "char_b"]}]}
    cast = [{"char_id": "char_a", "name": "Fraisita"}, {"char_id": "char_b", "name": "Bananita"}]
    sent = []
    monkeypatch.setattr(llm_call, "call_json", lambda ctx, prompt_id, system, user, schema, **kw: sent.append(user))
    tools = SimpleNamespace(runner=None, time_fn=None)
    for story, has in ((dict(GUARD_STORY, recipe="fruit_drama"), True), (dict(GUARD_STORY, recipe=None), False)):
        ec = SimpleNamespace(story=story, cast=cast, language="fr", ep=1)
        metadata.ask_platform(SimpleNamespace(on_log=lambda line: None), ec, script, "tiktok", tools=tools,
                              announced=set())
        assert (recipes.m1_line(FRUIT) in sent[-1]) is has
        assert metadata.closing_question(ec, script) == ("Team Fraisita ou Team Bananita ?" if has else None)


# ====================================================================== the steps hand it over

def test_the_concepts_step_sends_the_recipe_to_c1v2_and_retries_a_plain_human_name(tmp_path):
    import test_story_concepts_brief as brief_tests
    import test_story_steps as tss
    from clipping.aistory.steps import concepts as concepts_step
    from clipping.aistory.store import StoryStore

    stories = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = brief_tests._v3_story(stories, pipeline="v2")
    stories.update(story_id, lambda doc: doc.update(recipe="fruit_drama"), now=brief_tests.NOW)
    ctx, _log = tss._ctx(stories, story_id, params={"count": 1}, settings_env=brief_tests.SETTINGS)
    good = {"concepts": [brief_tests._card()]}
    bad = copy.deepcopy(good)
    bad["concepts"][0]["cast_sketch"][-1]["name"] = "Sophie"
    runner = tss.FakeRunner(bad, good, {"kept": True, "missing": []}, link=brief_tests.LINK)
    concepts_step.run(ctx, runner=runner)
    first, retry = runner.calls[0]["user"], runner.calls[1]["user"]
    assert recipes.c1v2_names_ask(FRUIT) in first and "\nRECIPE: Fruit drama." in first
    assert "'Sophie' is a plain human first name" in retry


def test_the_concepts_step_without_a_recipe_sends_no_recipe(tmp_path):
    import test_story_concepts_brief as brief_tests
    import test_story_steps as tss
    from clipping.aistory.steps import concepts as concepts_step
    from clipping.aistory.store import StoryStore

    stories = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = brief_tests._v3_story(stories, pipeline="v2")
    ctx, _log = tss._ctx(stories, story_id, params={"count": 1}, settings_env=brief_tests.SETTINGS)
    runner = tss.FakeRunner({"concepts": [brief_tests._card()]}, {"kept": True, "missing": []}, link=brief_tests.LINK)
    concepts_step.run(ctx, runner=runner)
    assert "RECIPE" not in runner.calls[0]["user"] and "- names:" not in runner.calls[0]["user"]


@pytest.mark.parametrize("recipe", ["fruit_drama", None])
def test_the_script_step_hands_e1v3_and_e3v3_the_recipe_only_when_the_story_names_one(store, recipe):  # noqa: F811
    import test_story_episode_steps as eps

    story_id = v3_tests._v3_story(store)
    store.update(story_id, lambda doc: doc.update(recipe=recipe), now=eps.NOW)
    llm = v3_tests._v3_llm()
    eps._run(eps._new().script, store, story_id, llm=llm)
    e1, e3 = llm.of("E1v3")[0]["user"], llm.of("E3v3")[0]["user"]
    assert ("The recipe's beats, in order:" in e1) is (recipe is not None)
    assert ("ending on the question \"Team X ou Team Y ?\"" in e3) is (recipe is not None)
