"""Tests for clipping.aistory.prompts and clipping.aistory.context (spec 4,
4.1, 4.2; stage 5 of the phase-1 plan).

Golden strings are asserted literally, the same convention as
``test_aistory_prompting.py``: the builders assemble fixed skeletons around
pack fields, so for a fixed input the output is fully deterministic and worth
pinning exactly. Both modules ran against ``69186fe`` fail with
``ModuleNotFoundError`` (neither existed yet); a change to one word of a
golden builder is expected to fail its golden test.
"""

from __future__ import annotations

import inspect
import json
import re

import pytest

from clipping.analysis import analyzer
from clipping.aistory import context, prompts, schemas, templates
from clipping.providers.pacing import estimate_tokens

STYLE_IDS = templates.list_style_ids()
FRUIT_DRAMA = templates.load_style("fruit_drama")

_FORBIDDEN_WORDS = ("duration", "timestamp", "path", "seconds")


def _concept(concept_id, language="fr"):
    raw = next(c for c in templates.load_concepts() if c["concept_id"] == concept_id)
    return templates.localize_concept(raw, language)


TENTAFRUIT_FR = _concept("tentafruit_island", "fr")


# ============================================================= golden strings

def test_build_c1_golden_fr_fruit_drama():
    pack = context.build_pack(
        language="fr",
        template=FRUIT_DRAMA,
        seed_text=(
            "Une bande de fruits anthropomorphes vit sur une ile de "
            "téléréalité et complote sans cesse."
        ),
        avoid_titles=["L'Île Tentafruit", "Le Verger de l'Héritage"],
    )
    system, user, schema = prompts.build_c1(pack, style_ids=STYLE_IDS, batch=3, of=10)

    expected_system = (
        "You are the head writer of a serialized vertical-video fiction "
        "series for TikTok, YouTube Shorts and Instagram Reels. Each "
        "episode lasts about 60 seconds and ends on a cliffhanger, so "
        "every idea must pay off in seconds and make people come back. "
        "Reply with JSON only, matching the schema. Never output "
        "durations, timestamps or file paths. Never use real people, "
        "brands, studio names or copyrighted characters. Write all "
        "user-facing text in French."
    )
    expected_user = (
        "Visual style: Fruit Drama — saturated natural fruit colours "
        "against warm neutral sets. Performance: over-acted telenovela "
        "delivery, exaggerated emotion, crisp diction, quick pace.\n\n"
        "Seed idea from the user: Une bande de fruits anthropomorphes vit "
        "sur une ile de téléréalité et complote sans cesse.\n\n"
        "Do not repeat or closely imitate these existing titles: L'Île "
        "Tentafruit, Le Verger de l'Héritage\n\n"
        "Invent exactly 1 original concept for a new serialized "
        "vertical-video fiction series (call 3 of 10).\n\n"
        "Give:\n"
        "- title: at most 8 words\n"
        "- logline: one sentence, at most 30 words\n"
        "- world: the setting and premise, at most 60 words\n"
        "- cast_sketch: 3 to 5 characters, each with a name, a role (one "
        "of lead, support, recurring, guest), and a one-line description, "
        "at most 25 words\n"
        "- hook_formula: what makes someone stop scrolling on episode 1\n"
        "- value: the real substance this story carries (a dilemma, a "
        "lesson, a truth about people)\n"
        "- retention_mechanics: why someone comes back for episode 2\n"
        "- style_fit: the visual style that best fits this concept, one "
        "of anime, cartoon_flat, cinematic_real, claymation, family_3d, "
        "fruit_drama, storybook_watercolor\n\n"
        "Never use real people, brands, studio names or copyrighted "
        "characters."
    )

    assert system == expected_system
    assert user == expected_user
    assert schema == schemas.c1_schema(STYLE_IDS)


def test_build_b1_golden_fr_tentafruit():
    pack = context.build_pack(language="fr", concept=TENTAFRUIT_FR)
    system, user, schema = prompts.build_b1(pack)

    expected_system = (
        "You are the head writer of a serialized vertical-video fiction "
        "series for TikTok, YouTube Shorts and Instagram Reels. Each "
        "episode lasts about 60 seconds and ends on a cliffhanger, so "
        "every idea must pay off in seconds and make people come back. "
        "Reply with JSON only, matching the schema. Never output "
        "durations, timestamps or file paths. Never use real people, "
        "brands, studio names or copyrighted characters. Write all "
        "user-facing text in French."
    )
    expected_user = (
        "Chosen concept:\n"
        "Title: L'Île Tentafruit\n"
        "Logline: Sur une île de téléréalité, des fruits forment des "
        "couples et complotent pour survivre au vote hebdomadaire.\n"
        "World: Chaque semaine, les concurrents forment des couples pour "
        "ne pas être éliminés, et un téléphone en noix de coco annonce "
        "les votes du public. Sous les paillettes, tout le monde ment à "
        "tout le monde pour rester à l'antenne.\n"
        "Main line: Au fil de la saison, alliances et trahisons se "
        "succèdent jusqu'au vote final qui ne laissera qu'un seul couple "
        "debout.\n"
        "Cast:\n"
        "- Kiwilo (lead): Kiwilo séduit tout le monde pour survivre au "
        "vote, mais personne ne sait ce qu'il veut vraiment.\n"
        "- Mangella (lead): Mangella veut gagner à sa façon, quitte à "
        "sacrifier ses alliances les plus proches.\n"
        "- Broccolia (recurring): Broccolia anime le jeu et connaît tous "
        "les secrets, mais ne les révèle qu'à son avantage.\n"
        "- Pepperino (support): Pepperino collectionne les cœurs sans "
        "jamais penser aux conséquences de ses choix.\n"
        "- Avocardo (support): Avocardo croit que sa fortune familiale "
        "lui garantit la victoire, quoi qu'il arrive.\n"
        "Hook formula: Chaque épisode s'ouvre sur un objet qui annonce "
        "l'enjeu de la semaine — un bulletin de vote, un message sur le "
        "téléphone en noix de coco — en moins de deux secondes.\n"
        "Value: La loyauté face à l'ambition, et ce que les gens sont "
        "prêts à faire pour être aimés.\n"
        "Retention mechanics: Éliminations, alliances et trahisons ; un "
        "vote hebdomadaire dont tout le monde débat en commentaires.\n\n"
        "Write the story bible's core fields for this concept.\n\n"
        "Give:\n"
        "- logline: one sentence, at most 30 words\n"
        "- premise: 2 to 6 sentences, at most 120 words\n"
        "- tone: at most 15 words\n"
        "- genre_tags: 2 to 5 tags, each at most 3 words\n\n"
        "Never use real people, brands, studio names or copyrighted "
        "characters."
    )

    assert system == expected_system
    assert user == expected_user
    assert schema == schemas.B1_SCHEMA


def test_golden_prompts_fail_when_a_word_changes():
    """Demonstrates that the golden assertions are load-bearing: a single
    reworded instruction line breaks the pinned string."""
    pack = context.build_pack(language="fr", concept=TENTAFRUIT_FR)
    _, user, _ = prompts.build_b1(pack)
    mutated = user.replace("Give:", "Provide:")
    assert mutated != user


# ------------------------------------------------------- B2 / B3 key lines

def _story_after_b1():
    return {
        "logline": TENTAFRUIT_FR["logline"],
        "premise": (
            "Chaque semaine, les concurrents forment des couples pour ne "
            "pas être éliminés. Un téléphone en noix de coco annonce les "
            "votes du public. Sous les paillettes, tout le monde ment à "
            "tout le monde pour rester à l'antenne."
        ),
        "tone": "mélodramatique, complice, rapide",
    }


def test_build_b2_key_lines():
    story = _story_after_b1()
    pack = context.build_pack(language="fr", concept=TENTAFRUIT_FR, story=story)
    system, user, schema = prompts.build_b2(pack)

    assert "Write all user-facing text in French." in system
    assert "Chosen concept:" in user
    assert "Bible written so far:" in user
    assert story["tone"] in user
    assert "setting_summary: at most 80 words" in user
    assert "rules: 4 to 6 rules, each at most 25 words" in user
    assert "time_period: at most 6 words" in user
    assert "recurring_motifs: exactly 3 motifs" in user
    assert schema == schemas.B2_SCHEMA
    # The instructions (as opposed to the French data above them) are
    # plain ASCII English -- no French wording was added to the ask itself.
    assert user.rsplit("\n\n", 1)[-1].isascii()


def test_build_b3_key_lines():
    story = _story_after_b1()
    story["world"] = {
        "setting_summary": "Une île de téléréalité tropicale où des fruits vivent en couples.",
        "rules": ["Un vote hebdomadaire élimine un couple.", "Le coco-phone annonce les résultats."],
        "time_period": "contemporain",
        "recurring_motifs": ["le téléphone en noix de coco", "le bûcher", "le miroir"],
    }
    pack = context.build_pack(language="fr", concept=TENTAFRUIT_FR, story=story)
    system, user, schema = prompts.build_b3(pack)

    assert "Bible written so far:" in user
    assert "World written so far:" in user
    assert "contemporain" in user
    assert "themes_and_values: 2 to 4 themes, each at most 12 words" in user
    assert "1 to 3 platforms (from tiktok, shorts, reels), no duplicates" in user
    assert "why_come_back: exactly 3 lines, each at most 20 words" in user
    assert schema == schemas.B3_SCHEMA


def test_build_b1_regenerate_key_lines():
    pack = context.build_pack(language="fr", concept=TENTAFRUIT_FR)
    regenerate = {
        "field": "tone",
        "current": {"tone": "mélodramatique, complice, rapide", "genre_tags": ["soap", "survie", "comédie"]},
        "note": "plus sombre",
    }
    system, user, schema = prompts.build_b1(pack, regenerate=regenerate)

    assert "Current values:" in user
    assert "- tone: mélodramatique, complice, rapide" in user
    assert "- genre_tags: soap, survie, comédie" in user
    assert "Rewrite only `tone`" in user
    assert "following the author's note: plus sombre" in user
    assert "keep every other field exactly as it is" in user
    assert schema == schemas.B1_SCHEMA


# ------------------------------------------------------------------ language

@pytest.mark.parametrize("language, name", [("fr", "French"), ("en", "English")])
def test_output_language_is_named_in_the_system_prompt(language, name):
    pack = context.build_pack(language=language, concept=TENTAFRUIT_FR)
    system, _, _ = prompts.build_b1(pack)
    assert f"Write all user-facing text in {name}." in system


@pytest.mark.parametrize("builder", [prompts.build_b1, prompts.build_b2, prompts.build_b3])
def test_instructions_are_english_only_when_the_pack_carries_no_data(builder):
    """With nothing in the pack, the user message is pure instruction text
    (no French data mixed in) -- and it must be plain English/ASCII."""
    pack = context.build_pack(language="fr")
    _, user, _ = builder(pack)
    assert user.isascii()
    assert "Never use real people" in user


def test_c1_instructions_are_english_only_when_the_pack_carries_no_data():
    pack = context.build_pack(language="fr")
    _, user, _ = prompts.build_c1(pack, style_ids=STYLE_IDS, batch=1, of=1)
    assert user.isascii()
    assert "Invent exactly 1 original concept " in user


# --------------------------------------------------------- caps and versions

def test_prompt_version():
    assert prompts.PROMPT_VERSION == "s2"


def test_max_tokens():
    assert prompts.MAX_TOKENS == {"C1": 700, "B1": 400, "B2": 520, "B3": 300}


def test_generate_ten_is_ten_calls_of_one_concept():
    assert (prompts.C1_CALLS, prompts.C1_CONCEPTS_PER_CALL, schemas.C1_CONCEPTS_PER_CALL) == (10, 1, 1)
    assert schemas.c1_schema(STYLE_IDS)["properties"]["concepts"]["description"] == "exactly 1 concept(s)"


def test_temperatures_are_the_analyzers_own_objects():
    assert prompts.TEMPERATURE["C1"] is prompts.IDEATION_TEMPERATURE == 0.9
    assert prompts.TEMPERATURE["B1"] is analyzer.WRITING_TEMPERATURE
    assert prompts.TEMPERATURE["B2"] is analyzer.WRITING_TEMPERATURE
    assert prompts.TEMPERATURE["B3"] is analyzer.WRITING_TEMPERATURE
    assert prompts.ANALYTIC_TEMPERATURE is analyzer.ANALYTIC_TEMPERATURE
    assert prompts.WRITING_TEMPERATURE is analyzer.WRITING_TEMPERATURE


def test_schema_names():
    assert prompts.SCHEMA_NAMES == {
        "C1": "story_concepts", "B1": "bible_core", "B2": "bible_world", "B3": "bible_values",
    }


# --------------------------------------------------------- REGENERATE_TARGETS

def test_regenerate_targets_keys():
    assert tuple(prompts.REGENERATE_TARGETS.keys()) == ("logline", "premise", "tone", "world", "themes")


@pytest.mark.parametrize(
    "field, schema",
    [
        ("logline", schemas.B1_SCHEMA),
        ("premise", schemas.B1_SCHEMA),
        ("tone", schemas.B1_SCHEMA),
        ("world", schemas.B2_SCHEMA),
        ("themes", schemas.B3_SCHEMA),
    ],
)
def test_regenerate_target_keys_exist_in_their_schema(field, schema):
    prompt_id, keys = prompts.REGENERATE_TARGETS[field]
    for key in keys:
        assert key in schema["properties"], f"{field} -> {prompt_id}.{key} is not a real field"


def test_regenerate_targets_prompt_ids():
    assert prompts.REGENERATE_TARGETS["logline"][0] == "B1"
    assert prompts.REGENERATE_TARGETS["premise"][0] == "B1"
    assert prompts.REGENERATE_TARGETS["tone"][0] == "B1"
    assert prompts.REGENERATE_TARGETS["world"][0] == "B2"
    assert prompts.REGENERATE_TARGETS["themes"][0] == "B3"


# --------------------------------------------------------------- LLM schemas

def _walk_llm_schema(schema, path="$"):
    """Every allowed keyword recursively, and additionalProperties/required
    holding for every object node (spec: 'strict' json_schema mode)."""
    errors = []
    allowed = {"type", "properties", "required", "additionalProperties", "items", "enum", "description"}
    extra = set(schema.keys()) - allowed
    if extra:
        errors.append(f"{path}: uses disallowed keyword(s) {sorted(extra)}")

    if schema.get("type") == "object":
        if schema.get("additionalProperties") is not False:
            errors.append(f"{path}: additionalProperties must be False")
        properties = schema.get("properties", {})
        if schema.get("required") != list(properties):
            errors.append(f"{path}: required must equal every property, in order")
        for key, subschema in properties.items():
            errors.extend(_walk_llm_schema(subschema, f"{path}.{key}"))
    elif schema.get("type") == "array" and "items" in schema:
        errors.extend(_walk_llm_schema(schema["items"], f"{path}[]"))

    return errors


@pytest.mark.parametrize(
    "schema",
    [schemas.c1_schema(STYLE_IDS), schemas.B1_SCHEMA, schemas.B2_SCHEMA, schemas.B3_SCHEMA],
    ids=["C1", "B1", "B2", "B3"],
)
def test_llm_schema_shape(schema):
    assert _walk_llm_schema(schema) == []


# -------------------------------------------------------- no forbidden asks

@pytest.mark.parametrize("builder", [prompts.build_c1, prompts.build_b1, prompts.build_b2, prompts.build_b3])
def test_no_builder_signature_asks_for_forbidden_params(builder):
    params = set(inspect.signature(builder).parameters)
    for forbidden in _FORBIDDEN_WORDS:
        assert not any(forbidden in p for p in params)


def _iter_schema_property_names(schema):
    for key, subschema in schema.get("properties", {}).items():
        yield key
        yield from _iter_schema_property_names(subschema)
        if subschema.get("type") == "array":
            yield from _iter_schema_property_names(subschema.get("items", {}))


@pytest.mark.parametrize(
    "schema",
    [schemas.c1_schema(STYLE_IDS), schemas.B1_SCHEMA, schemas.B2_SCHEMA, schemas.B3_SCHEMA],
    ids=["C1", "B1", "B2", "B3"],
)
def test_no_schema_property_asks_for_forbidden_fields(schema):
    names = list(_iter_schema_property_names(schema))
    for forbidden in _FORBIDDEN_WORDS:
        assert not any(forbidden in name for name in names)


# ================================================================== context

def test_trim_words_untouched_below_limit():
    text = "one two three"
    assert context.trim_words(text, 5) == (text, False)


def test_trim_words_cuts_and_marks():
    text = " ".join(f"w{i}" for i in range(10))
    cut, was_cut = context.trim_words(text, 4)
    assert was_cut is True
    assert cut == "w0 w1 w2 w3…"


def test_style_line_from_template():
    assert context.style_line(FRUIT_DRAMA) == (
        "Visual style: Fruit Drama — saturated natural fruit colours "
        "against warm neutral sets. Performance: over-acted telenovela "
        "delivery, exaggerated emotion, crisp diction, quick pace."
    )


def test_style_line_from_lock():
    from clipping.aistory import stylelock

    lock = stylelock.build_style_lock(FRUIT_DRAMA, now="2026-01-01T00:00:00+00:00")
    assert context.style_line(lock) == context.style_line(FRUIT_DRAMA)


def test_concept_block_localized_library_concept():
    block = context.concept_block(TENTAFRUIT_FR)
    assert block.startswith("Title: L'Île Tentafruit\n")
    assert "Main line: " in block
    assert "- Kiwilo (lead): " in block
    assert block.count("\n- ") == 5  # the five cast members


def test_concept_block_generated_card_has_no_main_line():
    card = {
        "title": "A Title",
        "logline": "A logline.",
        "world": "A world.",
        "cast_sketch": [{"name": "Ann", "role": "lead", "one_line": "Ann does things."}],
        "hook_formula": "A hook.",
        "value": "A value.",
        "retention_mechanics": "A mechanic.",
    }
    block = context.concept_block(card)
    assert "Main line" not in block
    assert "- Ann (lead): Ann does things." in block


def test_bible_summary_from_whatever_is_present():
    assert context.bible_summary({"logline": "L.", "premise": None, "tone": None}) == "L."
    assert context.bible_summary({}) == ""


def test_bible_summary_trims_to_120_words():
    long_premise = " ".join(f"w{i}" for i in range(200))
    summary = context.bible_summary({"logline": None, "premise": long_premise, "tone": None})
    assert len(summary.split()) == 120
    assert summary.endswith("…")


# ------------------------------------------------------------------- Pack

def test_build_pack_defaults_are_none():
    pack = context.build_pack(language="en")
    assert pack.language_name == "English"
    assert pack.style is None
    assert pack.concept is None
    assert pack.bible is None
    assert pack.world is None
    assert pack.seed is None
    assert pack.note is None
    assert pack.avoid is None
    assert pack.trimmed == []


def test_build_pack_trims_seed_note_and_avoid():
    seed = " ".join(f"s{i}" for i in range(200))
    note = " ".join(f"n{i}" for i in range(100))
    avoid = [f"Title {i}" for i in range(30)]
    pack = context.build_pack(language="en", seed_text=seed, note=note, avoid_titles=avoid)
    assert "seed" in pack.trimmed
    assert "note" in pack.trimmed
    assert "avoid" in pack.trimmed
    assert len(pack.seed.split()) == 120
    assert len(pack.note.split()) == 60
    assert pack.avoid.count(",") == 19  # 20 titles kept


def test_build_pack_bible_trim_is_named():
    long_premise = " ".join(f"w{i}" for i in range(200))
    pack = context.build_pack(language="en", story={"logline": None, "premise": long_premise, "tone": None})
    assert "bible" in pack.trimmed
    assert len(pack.bible.split()) == 120


def test_build_pack_world_block():
    story = {
        "world": {
            "setting_summary": "A place.",
            "rules": ["Rule one.", "Rule two."],
            "time_period": "now",
            "recurring_motifs": ["a", "b", "c"],
        }
    }
    pack = context.build_pack(language="en", story=story)
    assert "Setting: A place." in pack.world
    assert "Rules: Rule one.; Rule two." in pack.world
    assert "Time period: now" in pack.world
    assert "Recurring motifs: a, b, c" in pack.world


# --------------------------------------------------------- token budget (spec 4.1)

def _large_fixture():
    style_ids = STYLE_IDS
    seed_text = (
        "Une bande de fruits anthropomorphes vit sur une ile de la "
        "telerealite et complote sans cesse pour survivre au vote "
        "hebdomadaire du public qui regarde chaque semaine. " * 20
    )[:2000]
    note = " ".join(
        ("Rends l'ambiance un peu plus sombre et plus tendue pour la "
         "suite je pense vraiment que ca ira mieux comme ca honnetement "
         "pour tout le monde sur le plateau ").split() * 3
    )
    avoid_titles = [f"Titre Existant Numero {i}" for i in range(25)]

    concept = {
        "title": "Un Titre De Concept Assez Long Pour Le Test",
        "logline": (
            "Une logline assez longue pour tester la limite de mots dans "
            "le bloc concept qui sera utilisee dans le pack final du test."
        ),
        "world": (
            "Un monde tres detaille avec beaucoup d'elements de contexte "
            "pour que le bloc concept soit realiste et suffisamment long "
            "pour peser sur le budget de tokens total du prompt construit."
        ),
        "main_line": (
            "Une ligne directrice de saison assez longue elle aussi pour "
            "ajouter du poids au pack de contexte utilise dans les tests."
        ),
        "cast_sketch": [
            {"name": "Kiwilo", "role": "lead", "one_line": "Kiwilo charme tout le monde pour survivre au vote, mais personne ne sait ce qu'il veut vraiment au fond de lui."},
            {"name": "Mangella", "role": "lead", "one_line": "Mangella veut gagner a sa facon, quitte a sacrifier ses alliances les plus proches et les plus anciennes."},
            {"name": "Broccolia", "role": "recurring", "one_line": "Broccolia anime le jeu et connait tous les secrets, mais ne les revele qu'a son avantage personnel."},
            {"name": "Pepperino", "role": "support", "one_line": "Pepperino collectionne les coeurs sans jamais penser aux consequences de ses choix sur les autres."},
            {"name": "Avocardo", "role": "support", "one_line": "Avocardo croit que sa fortune familiale lui garantit la victoire, quoi qu'il arrive dans le jeu."},
        ],
        "hook_formula": "Chaque episode s'ouvre sur un objet qui annonce l'enjeu de la semaine en moins de deux secondes montre a l'ecran.",
        "value": "La loyaute face a l'ambition, et ce que les gens sont prets a faire pour etre aimes des autres.",
        "retention_mechanics": "Eliminations, alliances et trahisons ; un vote hebdomadaire dont tout le monde debat en commentaires en ligne.",
    }
    story = {
        "logline": concept["logline"],
        "premise": (
            "Chaque semaine, les concurrents forment des couples pour ne "
            "pas etre elimines. Un telephone en noix de coco annonce les "
            "votes du public. Sous les paillettes, tout le monde ment a "
            "tout le monde pour rester a l'antenne. Les alliances se font "
            "et se defont sans arret."
        ),
        "tone": "melodramatique, complice, rapide, plein d'auto-derision",
        "world": {
            "setting_summary": (
                "Une ile de telerealite tropicale ou des fruits "
                "personnifies vivent en couples sous surveillance "
                "permanente des cameras et du public qui vote chaque "
                "semaine pour eliminer un couple du jeu."
            ),
            "rules": [
                "Les fruits sont des personnes ; personne ne le commente jamais a l'ecran.",
                "Un vote hebdomadaire du public elimine un couple de la competition.",
                "Le telephone en noix de coco annonce les resultats du vote chaque semaine.",
                "Rompre un couple en public coute cher en image et en argent.",
                "Les alliances secretes sont tolerees tant qu'elles restent invisibles aux cameras.",
            ],
            "time_period": "contemporain",
            "recurring_motifs": [
                "le telephone en noix de coco", "le bucher de l'elimination", "le miroir des coulisses",
            ],
        },
    }
    return dict(
        language="fr",
        concept=concept,
        template=FRUIT_DRAMA,
        seed_text=seed_text,
        note=note,
        avoid_titles=avoid_titles,
        story=story,
    ), style_ids


def test_largest_fixture_stays_within_token_budget_for_every_builder():
    pack_kwargs, style_ids = _large_fixture()
    pack = context.build_pack(**pack_kwargs)

    assert set(pack.trimmed) >= {"seed", "note", "avoid"}

    builders = [
        (prompts.build_c1, dict(style_ids=style_ids, batch=5, of=5)),
        (prompts.build_b1, {}),
        (prompts.build_b2, {}),
        (prompts.build_b3, {}),
    ]
    for builder, kwargs in builders:
        system, user, _ = builder(pack, **kwargs)
        tokens = context.check_budget(system, user)
        assert tokens <= context.PACK_TOKEN_BUDGET, f"{builder.__name__} used {tokens} tokens"


def test_check_budget_raises_above_the_budget():
    with pytest.raises(ValueError, match=r"\d+ estimated tokens"):
        context.check_budget("x" * 10000, "y" * 10000)


# ==================================================================== schemas

def _good_c1_doc():
    def one_concept(title, style_fit):
        return {
            "title": title,
            "logline": "A short logline that is comfortably under the thirty word limit for this concept.",
            "world": (
                "A short world description that stays comfortably inside "
                "the sixty word limit set for this fixture, used only to "
                "exercise the post-validator in this test file, nothing more."
            ),
            "cast_sketch": [
                {"name": "Ann", "role": "lead", "one_line": "Ann wants something and is willing to lie to get it, which is the whole show."},
                {"name": "Bo", "role": "support", "one_line": "Bo is loyal to Ann until it stops being convenient for him personally."},
                {"name": "Cy", "role": "recurring", "one_line": "Cy hosts the game and knows more than anyone else lets on."},
            ],
            "hook_formula": "Opens on a ticking clock.",
            "value": "Loyalty versus ambition.",
            "retention_mechanics": "A weekly vote.",
            "style_fit": style_fit,
        }
    return {"concepts": [one_concept("Title One", "fruit_drama")]}


def test_c1_errors_good_fixture_passes():
    assert schemas.c1_errors(_good_c1_doc(), STYLE_IDS) == []


@pytest.mark.parametrize(
    "mutate, mentions",
    [
        (lambda d: d["concepts"].pop(), "concepts"),
        (lambda d: d["concepts"].append(dict(d["concepts"][0], title="Title Two")), "concepts"),
        (lambda d: d["concepts"][0].__setitem__("title", " ".join(f"w{i}" for i in range(9))), "title"),
        (lambda d: d["concepts"][0].__setitem__("logline", " ".join(f"w{i}" for i in range(31))), "logline"),
        (lambda d: d["concepts"][0].__setitem__("world", " ".join(f"w{i}" for i in range(61))), "world"),
        (lambda d: d["concepts"][0].__setitem__("cast_sketch", d["concepts"][0]["cast_sketch"][:2]), "cast_sketch"),
        (lambda d: d["concepts"][0]["cast_sketch"][0].__setitem__("one_line", " ".join(f"w{i}" for i in range(26))), "one_line"),
        (lambda d: d["concepts"][0].__setitem__("style_fit", "not_a_real_style"), "style_fit"),
    ],
)
def test_c1_errors_each_violation(mutate, mentions):
    doc = _good_c1_doc()
    mutate(doc)
    errors = schemas.c1_errors(doc, STYLE_IDS)
    assert errors, "expected at least one error"
    assert any(mentions in e for e in errors)


def _good_b1_doc():
    return {
        "logline": "A short logline that stays comfortably under the thirty word limit for this bible fixture.",
        "premise": (
            "The islanders pair up every week to survive the vote. A "
            "coconut phone announces the results. Everyone lies to "
            "everyone else to stay on screen."
        ),
        "tone": "melodramatic, self-aware, fast",
        "genre_tags": ["soap", "survival", "comedy"],
    }


def test_b1_errors_good_fixture_passes():
    assert schemas.b1_errors(_good_b1_doc()) == []


@pytest.mark.parametrize(
    "mutate, mentions",
    [
        (lambda d: d.__setitem__("logline", " ".join(f"w{i}" for i in range(31))), "logline"),
        (lambda d: d.__setitem__("premise", "Only one sentence here."), "premise"),
        (lambda d: d.__setitem__("premise", " ".join(f"w{i}" for i in range(121)) + "."), "premise"),
        (lambda d: d.__setitem__("tone", " ".join(f"w{i}" for i in range(16))), "tone"),
        (lambda d: d.__setitem__("genre_tags", ["only_one"]), "genre_tags"),
        (lambda d: d.__setitem__("genre_tags", ["one two three four"]), "genre_tags"),
    ],
)
def test_b1_errors_each_violation(mutate, mentions):
    doc = _good_b1_doc()
    mutate(doc)
    errors = schemas.b1_errors(doc)
    assert errors, "expected at least one error"
    assert any(mentions in e for e in errors)


def _good_b2_doc():
    return {
        "setting_summary": "A tropical reality-show island where anthropomorphic fruit contestants live as couples under constant camera surveillance.",
        "rules": [
            "Fruits are people; nobody comments on it.",
            "A weekly public vote eliminates one couple.",
            "The coconut phone announces the vote results.",
            "Breaking up publicly costs a contestant their image.",
        ],
        "time_period": "contemporary",
        "recurring_motifs": ["the coconut phone", "the elimination bonfire", "the backstage mirror"],
    }


def test_b2_errors_good_fixture_passes():
    assert schemas.b2_errors(_good_b2_doc()) == []


@pytest.mark.parametrize(
    "mutate, mentions",
    [
        (lambda d: d.__setitem__("setting_summary", " ".join(f"w{i}" for i in range(81))), "setting_summary"),
        (lambda d: d.__setitem__("rules", d["rules"][:3]), "rules"),
        (lambda d: d["rules"].__setitem__(0, " ".join(f"w{i}" for i in range(26))), "rules"),
        (lambda d: d.__setitem__("time_period", " ".join(f"w{i}" for i in range(7))), "time_period"),
        (lambda d: d.__setitem__("recurring_motifs", d["recurring_motifs"][:2]), "recurring_motifs"),
    ],
)
def test_b2_errors_each_violation(mutate, mentions):
    doc = _good_b2_doc()
    mutate(doc)
    errors = schemas.b2_errors(doc)
    assert errors, "expected at least one error"
    assert any(mentions in e for e in errors)


def _good_b3_doc():
    return {
        "themes_and_values": ["loyalty versus ambition", "what people do for approval"],
        "audience": {"age": "13+", "platforms": ["tiktok", "shorts"]},
        "why_come_back": [
            "The weekly vote everyone argues about.",
            "A new alliance breaks every episode.",
            "The coconut phone always changes everything.",
        ],
    }


def test_b3_errors_good_fixture_passes():
    assert schemas.b3_errors(_good_b3_doc()) == []


@pytest.mark.parametrize(
    "mutate, mentions",
    [
        (lambda d: d.__setitem__("themes_and_values", ["only one theme"]), "themes_and_values"),
        (lambda d: d["themes_and_values"].__setitem__(0, " ".join(f"w{i}" for i in range(13))), "themes_and_values"),
        (lambda d: d["audience"].__setitem__("platforms", ["tiktok", "tiktok"]), "platforms"),
        (lambda d: d.__setitem__("why_come_back", d["why_come_back"][:2]), "why_come_back"),
        (lambda d: d["why_come_back"].__setitem__(0, " ".join(f"w{i}" for i in range(21))), "why_come_back"),
    ],
)
def test_b3_errors_each_violation(mutate, mentions):
    doc = _good_b3_doc()
    mutate(doc)
    errors = schemas.b3_errors(doc)
    assert errors, "expected at least one error"
    assert any(mentions in e for e in errors)


def test_post_validators_report_missing_key_via_schema_validate():
    doc = _good_b1_doc()
    del doc["tone"]
    errors = schemas.b1_errors(doc)
    assert any("tone" in e and "required" in e for e in errors)


# ============================================================ truncation guard
#
# 2026-09-26, live: every C1 reply to "Generate 10 more" on a French story was
# cut off mid-JSON at the 500-token cap (two full French cards need ~900-1,100
# output tokens), and the chain fell through to a paid link. The fakes of the
# step tests answered complete JSON, so nothing caught it. This does: for each
# prompt, the largest reply the prompt allows -- realistic French prose with
# every field that has a stated word limit exactly AT that limit, and the
# largest count of every list -- must fit the prompt's cap.

# French tokenises at ~1.3x the chars/4 estimate (pacing.estimate_tokens) on
# Gemini: it runs ~1.3x longer than the same English, and chars/4 is an English
# rule of thumb. The reply is measured the way call_json's ✍️ line measures it.
FRENCH_TOKEN_FACTOR = 1.3

# One French card. The fields with a stated limit sit exactly at it; the ones
# the prompt gives no limit (hook_formula, value, retention_mechanics, names)
# are sized like the shipped library's French cards (hooks run 12-32 words,
# values 5-17, retention lines 11-21).
_FR_CONCEPT = {
    "title": "La Nuit Où le Verger Perdit la Mémoire",
    "logline": (
        "Quand une pomme amnésique se réveille dans le verger rival, elle doit découvrir qui "
        "l'a trahie avant la grande récolte, sans savoir quels fruits mentent ni pourquoi tous "
        "la craignent."
    ),
    "world": (
        "Deux vergers ennemis se partagent une vallée où les fruits parlent, votent et se marient, "
        "mais oublient tout à chaque récolte. Seuls les noyaux gardent la mémoire, cachés sous les "
        "racines du vieux cerisier. Quiconque en avale un retrouve ses souvenirs et découvre aussi "
        "ceux des autres, ce qui rend chaque secret dangereux, chaque alliance fragile et chaque "
        "récolte terrifiante."
    ),
    "cast_sketch": [
        {"name": "Reinette", "role": "lead", "one_line": (
            "Pomme amnésique et têtue, elle se réveille couverte d'une sève inconnue et refuse de "
            "croire quiconque tant qu'elle n'a pas retrouvé son propre noyau enfoui.")},
        {"name": "Griotte", "role": "lead", "one_line": (
            "Cerise du verger rival, charmeuse et pressée, elle prétend être la meilleure amie de "
            "Reinette mais cache le noyau qui prouverait le contraire depuis longtemps.")},
        {"name": "Le Vieux Cerisier", "role": "recurring", "one_line": (
            "Arbre millénaire qui garde les noyaux sous ses racines, il parle en énigmes et "
            "n'aide jamais deux fois le même fruit pendant une seule saison.")},
        {"name": "Poiron", "role": "support", "one_line": (
            "Poire maladroite et sincère, il suit Reinette partout, note tout dans un carnet et se "
            "souvient de détails que tout le monde voudrait enfin oublier.")},
        {"name": "Madame Coing", "role": "guest", "one_line": (
            "Juge redoutée de la récolte, elle décide quels fruits restent sur l'arbre et semble "
            "savoir exactement ce que Reinette a fait pendant la saison dernière.")},
    ],
    "hook_formula": (
        "Chaque épisode s'ouvre sur un noyau avalé en gros plan, puis un souvenir volé qui "
        "contredit tout ce qu'on croyait."
    ),
    "value": "Ce qu'on devient quand on oublie qui on a blessé.",
    "retention_mechanics": "Chaque noyau révèle un secret, et le public parie sur le prochain traître.",
    "style_fit": "fruit_drama",
}

_FR_B1 = {
    "logline": _FR_CONCEPT["logline"],
    "premise": (
        "Reinette se réveille un matin sous le vieux cerisier du verger rival, couverte d'une sève "
        "qui n'est pas la sienne et incapable de se rappeler la veille. Tout le monde semble la "
        "connaître, mais personne ne raconte la même chose sur ce qu'elle a fait cette nuit-là. "
        "Griotte se présente comme sa meilleure amie et l'aide à chercher son noyau, la seule "
        "mémoire que la récolte n'efface jamais. Chaque noyau avalé rend un souvenir, mais aussi un "
        "secret qui appartient à quelqu'un d'autre. Plus Reinette se souvient, plus elle comprend "
        "que la trahison qu'elle cherche est peut-être la sienne. La récolte arrive dans sept jours, "
        "Madame Coing a déjà choisi son coupable, et personne ne compte la laisser parler avant."
    ),
    "tone": ("mélodramatique et rapide, plein de trahisons, de faux souvenirs et d'un humour noir "
             "parfaitement assumé"),
    "genre_tags": ["mystère très sombre", "comédie noire fruitée", "soap de verger",
                   "thriller de l'amnésie", "drame de récolte"],
}

_FR_B2 = {
    "setting_summary": (
        "Une vallée partagée entre deux vergers ennemis, séparés par une rivière de sirop que "
        "personne ne traverse sans y laisser quelque chose de précieux. Les fruits y vivent comme "
        "des familles, votent, se marient et se jalousent, mais la grande récolte efface leurs "
        "souvenirs à chaque saison. Sous les racines du vieux cerisier, des milliers de noyaux "
        "gardent ce que la vallée a oublié, et ceux qui les avalent découvrent des vérités que "
        "personne ne voulait revoir en plein jour."
    ),
    "rules": [
        ("Les fruits oublient tout à chaque récolte, sauf ce qui reste enfermé dans leur propre "
         "noyau, caché sous les racines du vieux cerisier millénaire, loin."),
        ("Avaler le noyau d'un autre fruit donne tous ses souvenirs, mais laisse sur la peau une "
         "tache de sève que tout le monde peut voir."),
        ("Personne ne traverse la rivière de sirop sans perdre un souvenir, choisi au hasard par "
         "le courant qui passe sous le vieux pont ce jour-là."),
        ("Madame Coing décide seule quels fruits restent sur l'arbre, et sa décision ne peut "
         "jamais être contestée en public, même par les deux vergers réunis."),
        ("Un fruit tombé de l'arbre avant la récolte perd son nom et devient un étranger pour tout "
         "le verger jusqu'au retour du printemps, sans exception."),
        ("Le vieux cerisier répond à une seule question par fruit et par saison, toujours sous la "
         "forme d'une énigme très obscure et souvent très cruelle."),
    ],
    "time_period": "une saison de récolte, sept jours",
    "recurring_motifs": ["le noyau avalé en gros plan", "la tache de sève qui trahit",
                         "la cloche de la récolte"],
}

_FR_B3 = {
    "themes_and_values": [
        "ce qu'on devient vraiment quand on oublie qui on a blessé hier",
        "la loyauté mise à l'épreuve par des souvenirs volés à nos proches",
        "le pardon a-t-il encore un sens sans la mémoire de la faute",
        "la vérité qu'on cherche est parfois celle qu'on fuyait depuis toujours, hélas",
    ],
    "audience": {"age": "13+", "platforms": ["tiktok", "shorts", "reels"]},
    "why_come_back": [
        ("Chaque épisode révèle un souvenir volé qui retourne une alliance que le public croyait "
         "solide et sincère depuis des semaines."),
        ("La récolte approche et tout le public parie en commentaires sur le fruit qui a vraiment "
         "trahi Reinette cette nuit-là."),
        ("Le vieux cerisier ne répond qu'une fois par saison, et sa prochaine énigme peut tout "
         "changer d'un coup pour Reinette."),
    ],
}


def _words(text):
    return len(text.split())


def _c1_concepts_asked_for():
    """How many concepts one C1 call asks for, read from the prompt as sent --
    the reply to measure is the reply to *that* prompt."""
    _, user, _ = prompts.build_c1(context.build_pack(language="fr"), style_ids=STYLE_IDS, batch=1, of=1)
    return int(re.search(r"Invent exactly (\d+) original concept", user).group(1))


def _largest_french_reply(prompt_id):
    if prompt_id == "C1":
        return {"concepts": [_FR_CONCEPT] * _c1_concepts_asked_for()}
    return {"B1": _FR_B1, "B2": _FR_B2, "B3": _FR_B3}[prompt_id]


def test_the_largest_french_replies_are_valid_and_at_every_limit_their_prompt_states():
    """The fixtures cannot shrink unnoticed: each is accepted by its
    post-validator, and each limited field sits exactly at the limit the
    prompt text states."""
    pack = context.build_pack(language="fr", concept=TENTAFRUIT_FR)
    _, c1_user, _ = prompts.build_c1(context.build_pack(language="fr"), style_ids=STYLE_IDS, batch=1, of=1)
    for line in ("- title: at most 8 words", "- logline: one sentence, at most 30 words",
                 "- world: the setting and premise, at most 60 words", "3 to 5 characters",
                 "one-line description, at most 25 words"):
        assert line in c1_user, line
    assert schemas.c1_errors({"concepts": [_FR_CONCEPT]}, STYLE_IDS) == []
    assert [_words(_FR_CONCEPT[f]) for f in ("title", "logline", "world")] == [8, 30, 60]
    assert [_words(m["one_line"]) for m in _FR_CONCEPT["cast_sketch"]] == [25] * 5

    _, b1_user, _ = prompts.build_b1(pack)
    for line in ("at most 30 words", "2 to 6 sentences, at most 120 words", "tone: at most 15 words",
                 "2 to 5 tags, each at most 3 words"):
        assert line in b1_user, line
    assert schemas.b1_errors(_FR_B1) == []
    assert (_words(_FR_B1["logline"]), _words(_FR_B1["premise"]), _words(_FR_B1["tone"])) == (30, 120, 15)
    assert [_words(tag) for tag in _FR_B1["genre_tags"]] == [3] * 5

    _, b2_user, _ = prompts.build_b2(pack)
    for line in ("setting_summary: at most 80 words", "4 to 6 rules, each at most 25 words",
                 "time_period: at most 6 words", "exactly 3 motifs"):
        assert line in b2_user, line
    assert schemas.b2_errors(_FR_B2) == []
    assert _words(_FR_B2["setting_summary"]) == 80
    assert [_words(rule) for rule in _FR_B2["rules"]] == [25] * 6
    assert (_words(_FR_B2["time_period"]), len(_FR_B2["recurring_motifs"])) == (6, 3)

    _, b3_user, _ = prompts.build_b3(pack)
    for line in ("2 to 4 themes, each at most 12 words", "1 to 3 platforms",
                 "exactly 3 lines, each at most 20 words"):
        assert line in b3_user, line
    assert schemas.b3_errors(_FR_B3) == []
    assert [_words(theme) for theme in _FR_B3["themes_and_values"]] == [12] * 4
    assert [_words(line) for line in _FR_B3["why_come_back"]] == [20] * 3
    assert len(_FR_B3["audience"]["platforms"]) == 3


@pytest.mark.parametrize("prompt_id", ["C1", "B1", "B2", "B3"])
def test_the_largest_french_reply_fits_its_cap(prompt_id):
    reply = _largest_french_reply(prompt_id)
    estimate = estimate_tokens(json.dumps(reply, ensure_ascii=False))
    needed = estimate * FRENCH_TOKEN_FACTOR
    cap = prompts.MAX_TOKENS[prompt_id]
    assert needed <= cap, (
        f"{prompt_id}: the largest French reply needs ~{needed:.0f} tokens "
        f"({estimate} by chars/4 x {FRENCH_TOKEN_FACTOR}), over its {cap}-token cap"
    )
