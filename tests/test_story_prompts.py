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
        "user-facing text in French. Fields marked (English) are for "
        "image and voice models: write them in English."
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
        "fruit_drama, storybook_watercolor, viral_3d\n\n"
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
        "user-facing text in French. Fields marked (English) are for "
        "image and voice models: write them in English."
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
    assert prompts.PROMPT_VERSION == "s7"


def test_max_tokens():
    assert prompts.MAX_TOKENS == {
        "C1": 700, "B1": 400, "B2": 520, "B3": 300,
        "K1": 750, "P0": 420, "P1": 260, "R1": 100, "S1": 950, "S2": 350, "U1": 120,
        "E1": 1450, "E2": 600, "E3": 720, "E4": 800, "T1": 580, "T1r": 150,
        "M1": 330,
        "S3": 720, "F1": 400, "N1": 1430,
        # Phase 7 stage 3a (DEC-226): the look writers.
        "D2": 380, "D3": 300, "R1v2": 220,
        # Phase 7 stage 4 (DEC-227): re-pinned on purpose -- T1 v2 and its re-plan, new ids (v1 rows unchanged).
        "T1v2": 1040, "T1rv2": 520,
        # Phase 7 stage 5a (DEC-228): re-pinned on purpose -- the dossier writer (D1), its French worst case
        # measured (tests/test_story_episode_prompt_budgets.py).
        "D1": 1290,
        # Phase 7 stage 5b (DEC-228): re-pinned on purpose -- the knowledge step's writers (world notes,
        # one episode's timeline, the props registry), each measured on its French worst case
        # (tests/test_story_episode_prompt_budgets.py).
        "D4": 430, "D5": 3330, "D6": 540,
        # Phase 7 stage 5c (DEC-228): re-pinned on purpose -- a v2 story's writing calls, new ids (v1 rows
        # unchanged): E2v2/E3v2 reply as E2/E3; E1v2 adds up to 2 new objects, measured on its French worst
        # case (tests/test_story_prompts_episode.py).
        "E1v2": 1750, "E2v2": 600, "E3v2": 720,
        # Phase 7 stage 5d (DEC-229): re-pinned on purpose -- the memory step's second call, the continuity
        # ledger (L1), measured on its French worst case (tests/test_story_episode_prompt_budgets.py).
        "L1": 690,
        # Phase 7 stage 6a (DEC-230): re-pinned on purpose -- the first-watch judge (J1), measured on its
        # French worst case (tests/test_story_episode_prompt_budgets.py). DEC-248: re-pinned on purpose --
        # J1 version 2's severity per issue (920 -> 970).
        "J1": 970,
        # Phase 7 stage 6b (DEC-230): re-pinned on purpose -- the keyframe judge (J2), its English worst case,
        # under the plan's 160 (same file).
        "J2": 190,  # plan 28 F2, re-pinned on purpose: the framing and sheet issues counted
        # Plan 20 stage 2: re-pinned on purpose -- a v2 story's S1 with the plot archetypes, a new id (the S1
        # row is unchanged), measured on its French worst case (tests/test_story_season_archetypes.py).
        "S1v2": 1150,
        # Plan 22 stage 2 (DEC-274): re-pinned on purpose -- the brief-faithful concept prompts, new ids (C1's
        # own row is unchanged): C1v2 and B1v3 answer their v1 schemas (= their caps); C1J is a short verdict.
        "C1v2": 700, "C1J": 200, "B1v3": 400,
        # Plan 22 stage 3: re-pinned on purpose -- writing v3, new ids (the v1/v2 rows unchanged): E1v3 and
        # J1v3 measured on their French worst cases, E2v3/E3v3 reply as E2/E3 (tests/test_story_prompts_v3.py).
        "E1v3": 2570, "E2v3": 700, "E3v3": 720, "J1v3": 990,
        # Plan 23 stage D5: re-pinned on purpose -- N1v2 (a twist may bring an appearance variant), a new id
        # (N1's row unchanged), measured on N1's French worst case plus two variants
        # (tests/test_story_variant_twist.py).
        "N1v2": 1850,
    }


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
        "K1": "character_write", "P0": "places_props_proposal", "P1": "place_write",
        "R1": "prop_write", "S1": "season_arc_skeleton", "S2": "season_arc_entry",
        "U1": "vision_appearance",
        "E1": "episode_beat_sheet", "E2": "episode_scene_dialogue", "E3": "episode_framing_scenes",
        "E4": "episode_consistency_check", "T1": "storyboard_shots", "T1r": "storyboard_shot_replan",
        "M1": "episode_metadata",
        "S3": "series_memory_entry", "F1": "audience_feedback_digest", "N1": "next_episode_proposals",
        # Phase 7 stage 3a (DEC-226): the look writers.
        "D2": "character_look", "D3": "place_look", "R1v2": "prop_look",
        # Phase 7 stage 4 (DEC-227): re-pinned on purpose -- T1 v2 and its re-plan, new ids (v1 rows unchanged).
        "T1v2": "storyboard_beat_shots", "T1rv2": "storyboard_beat_shot_replan",
        # Phase 7 stage 5a (DEC-228): re-pinned on purpose -- the dossier writer.
        "D1": "character_dossier",
        # Phase 7 stage 5b (DEC-228): re-pinned on purpose -- the knowledge step's writers.
        "D4": "knowledge_world", "D5": "knowledge_timeline", "D6": "knowledge_props",
        # Phase 7 stage 5c (DEC-228): re-pinned on purpose -- a v2 story's writing calls, new ids.
        "E1v2": "episode_beat_sheet_v2", "E2v2": "episode_scene_dialogue_v2", "E3v2": "episode_framing_scenes_v2",
        # Phase 7 stage 5d (DEC-229): re-pinned on purpose -- the continuity ledger (L1).
        "L1": "continuity_ledger",
        # Phase 7 stage 6a (DEC-230): re-pinned on purpose -- the first-watch judge (J1).
        "J1": "first_watch_check",
        # Phase 7 stage 6b (DEC-230): re-pinned on purpose -- the keyframe judge (J2).
        "J2": "keyframe_check",
        # Plan 20 stage 2: re-pinned on purpose -- a v2 story's S1 with the plot archetypes, a new id.
        "S1v2": "season_arc_skeleton_v2",
        # Plan 22 stage 2 (DEC-274): re-pinned on purpose -- C1v2/B1v3 answer their v1 schemas but keep their own
        # schema name (every versioned prompt does, a test fixture's schema_name -> prompt_id lookup relies on it).
        "C1v2": "story_concepts_v2", "C1J": "concept_brief_check", "B1v3": "bible_core_v3",
        # Plan 22 stage 3: re-pinned on purpose -- writing v3's prompts, each its own schema name.
        "E1v3": "episode_beat_sheet_v3", "E2v3": "episode_scene_dialogue_v3", "E3v3": "episode_framing_scenes_v3",
        "J1v3": "first_watch_check_v3",
        # Plan 23 stage D5: re-pinned on purpose -- N1v2, a new id (N1's row unchanged).
        "N1v2": "next_episode_proposals_v2",
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


# Phase 2 (K1/P0/P1/R1/S1/S2/U1): schemas built with representative names/ids
# so their shape (including the enum-constrained fields) is what a real call
# would actually send.
PHASE2_SCHEMAS = [
    schemas.k1_schema(["Mangella"]),
    schemas.p0_schema(["Kiwilo", "Mangella"]),
    schemas.p1_schema(),
    schemas.r1_schema(["Kiwilo"]),
    schemas.s1_schema(8),
    schemas.s2_schema(["Kiwilo", "Mangella"]),
    schemas.u1_schema(),
]
PHASE2_SCHEMA_IDS = ["K1", "P0", "P1", "R1", "S1", "S2", "U1"]
PHASE2_BUILDERS = [
    prompts.build_k1, prompts.build_p0, prompts.build_p1,
    prompts.build_r1, prompts.build_s1, prompts.build_s2, prompts.build_u1,
]


@pytest.mark.parametrize(
    "schema",
    [schemas.c1_schema(STYLE_IDS), schemas.B1_SCHEMA, schemas.B2_SCHEMA, schemas.B3_SCHEMA] + PHASE2_SCHEMAS,
    ids=["C1", "B1", "B2", "B3"] + PHASE2_SCHEMA_IDS,
)
def test_llm_schema_shape(schema):
    assert _walk_llm_schema(schema) == []


# -------------------------------------------------------- no forbidden asks

@pytest.mark.parametrize(
    "builder",
    [prompts.build_c1, prompts.build_b1, prompts.build_b2, prompts.build_b3] + PHASE2_BUILDERS,
)
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
    [schemas.c1_schema(STYLE_IDS), schemas.B1_SCHEMA, schemas.B2_SCHEMA, schemas.B3_SCHEMA] + PHASE2_SCHEMAS,
    ids=["C1", "B1", "B2", "B3"] + PHASE2_SCHEMA_IDS,
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
    # Plan 20 stage 2: re-pinned on purpose -- the cap grew from 20 to 24 with the 14-concept library
    # (14 library titles + the 9 cards one run writes before its last call never cut).
    assert pack.avoid.count(",") == 23  # 24 titles kept


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


# ======================================================================================
# PHASE 2: K1 (character) / P0 (places+props proposal) / P1 (place) / R1 (prop) /
# S1 (season arc skeleton) / S2 (expand one arc entry) / U1 (vision: design reference)
# ======================================================================================
#
# Same conventions as phase 1 above (DEC-062, the truncation guard): K1/P0/S1 get full
# (system, user) goldens; P1/R1/S2/U1 get key-line checks; every new prompt joins the
# truncation guard and the <=1200-token budget check.

KIWILO_SKETCH = TENTAFRUIT_FR["cast_sketch"][0]
MANGELLA_SKETCH = TENTAFRUIT_FR["cast_sketch"][1]

MANGELLA_WRITTEN = {
    "name": "Mangella",
    "role": "lead",
    "one_line": MANGELLA_SKETCH["one_line"],
    "descriptor": "an anthropomorphic mango with smooth orange-yellow skin and a sly grin",
}

UPLOAD_NOTE = "a rough sketch of a green kiwi character wearing a thin gold chain and a white shirt"


def _phase2_bible_story():
    return {
        "logline": TENTAFRUIT_FR["logline"],
        "premise": (
            "Chaque semaine, les concurrents forment des couples pour ne pas etre elimines. "
            "Un telephone en noix de coco annonce les votes du public."
        ),
        "tone": "melodramatique, complice, rapide",
    }


def _phase2_bible_and_world_story():
    story = _phase2_bible_story()
    story["world"] = {
        "setting_summary": "Une île de téléréalité tropicale où des fruits vivent en couples.",
        "rules": ["Un vote hebdomadaire élimine un couple.", "Le coco-phone annonce les résultats."],
        "time_period": "contemporain",
        "recurring_motifs": ["le téléphone en noix de coco", "le bûcher", "le miroir"],
    }
    return story


# --------------------------------------------------------------------------------- K1

def test_build_k1_golden_fr_fruit_drama():
    pack = context.build_pack(language="fr", story=_phase2_bible_story(), template=FRUIT_DRAMA)
    system, user, schema = prompts.build_k1(
        pack, character=KIWILO_SKETCH, cast_so_far=[MANGELLA_WRITTEN], upload_notes=UPLOAD_NOTE,
    )

    expected_system = (
        "You are the head writer of a serialized vertical-video fiction "
        "series for TikTok, YouTube Shorts and Instagram Reels. Each "
        "episode lasts about 60 seconds and ends on a cliffhanger, so "
        "every idea must pay off in seconds and make people come back. "
        "Reply with JSON only, matching the schema. Never output "
        "durations, timestamps or file paths. Never use real people, "
        "brands, studio names or copyrighted characters. Write all "
        "user-facing text in French. Fields marked (English) are for "
        "image and voice models: write them in English."
    )
    expected_user = (
        "Bible written so far:\nSur une île de téléréalité, des fruits forment des couples et "
        "complotent pour survivre au vote hebdomadaire. Chaque semaine, les concurrents forment "
        "des couples pour ne pas etre elimines. Un telephone en noix de coco annonce les votes "
        "du public. Tone: melodramatique, complice, rapide.\n\nVisual style: Fruit Drama — "
        "saturated natural fruit colours against warm neutral sets. Performance: over-acted "
        "telenovela delivery, exaggerated emotion, crisp diction, quick pace.\n\nCharacter "
        "design rule: The head is one recognisable whole fruit or vegetable at human head "
        "scale; the face (eyes, brows, mouth with teeth) is carved into its surface, not "
        "pasted on. Bodies are human, dressed in realistic contemporary clothes that carry "
        "the character's signature items and tell their social status (a torn tee and "
        "backpack vs a black suit and tie). No hands as fruit — hands are human. Keep exact "
        "fruit species, ripeness, colour and outfit identical in every image.\n\nExisting "
        "cast:\n- Mangella (lead): Mangella veut gagner à sa façon, quitte à sacrifier ses "
        "alliances les plus proches. — looks: an anthropomorphic mango with smooth "
        "orange-yellow skin and a sly grin\n\nCharacter to write: Kiwilo (lead, manipulateur "
        "charmeur)\nOne line: Kiwilo séduit tout le monde pour survivre au vote, mais personne "
        "ne sait ce qu'il veut vraiment.\nSignature hint: une fine chaîne en or\n\nDesign "
        "reference supplied by the author: a rough sketch of a green kiwi character wearing a "
        "thin gold chain and a white shirt; follow it.\n\nWrite this character for the "
        "story.\n\nGive:\n- descriptor (English): appearance only, at most 45 words, never "
        "the character's name\n- signature_items (English): 2 to 3 recognisable items or "
        "marks, each at most 8 words\n- personality: 2 to 5 traits (each at most 4 words), "
        "and wants, fears, speech_style, each at most 25 words\n- voice: gender (one of "
        "female, male, neutral), age (one of child, young, adult, elder), 1 to 3 style_tags "
        "(from warm, bright, deep, raspy, soft, fast, slow, smug, nervous, authoritative, "
        "playful, calm), direction (English, at most 20 words), and sample_line (at most 12 "
        "words, in character, no name)\n- relationships: 0 to 5 entries to the existing cast, "
        "each with `with` (an existing cast member) and `relation` (at most 15 words)\n\n"
        "Never use real people, brands, studio names or copyrighted characters. Never mention "
        "the character's own name in the descriptor, the signature items or the sample line."
    )

    assert system == expected_system
    assert user == expected_user
    assert schema == schemas.k1_schema(["Mangella"])


def test_build_k1_regenerate_key_lines():
    pack = context.build_pack(language="en")
    regenerate = {
        "field": "text",
        "current": {"descriptor": "an old descriptor"},
        "note": "make her look older",
    }
    system, user, schema = prompts.build_k1(
        pack, character=KIWILO_SKETCH, cast_so_far=[], regenerate=regenerate,
    )
    assert "Current values:" in user
    assert "- descriptor: an old descriptor" in user
    assert "Rewrite only `text`" in user
    assert "following the author's note: make her look older" in user
    assert schema == schemas.k1_schema([])


def test_build_k1_no_cast_still_produces_a_usable_schema():
    pack = context.build_pack(language="en")
    _, user, schema = prompts.build_k1(pack, character=KIWILO_SKETCH, cast_so_far=[])
    assert "Existing cast:" not in user
    assert schema["properties"]["relationships"]["items"]["properties"]["with"] == {"type": "string"}


# --------------------------------------------------------------------------------- P0

def test_build_p0_golden_fr():
    pack = context.build_pack(language="fr", story=_phase2_bible_and_world_story())
    cast = [
        {"name": "Kiwilo", "signature_items": ["une fine chaîne en or", "une chemise en lin blanc"]},
        {"name": "Mangella", "signature_items": ["une pince à cheveux strass en forme de couronne"]},
    ]
    system, user, schema = prompts.build_p0(pack, cast=cast)

    expected_user = (
        "Bible written so far:\nSur une île de téléréalité, des fruits forment des couples et "
        "complotent pour survivre au vote hebdomadaire. Chaque semaine, les concurrents forment "
        "des couples pour ne pas etre elimines. Un telephone en noix de coco annonce les votes "
        "du public. Tone: melodramatique, complice, rapide.\n\n"
        "World written so far:\nSetting: Une île de téléréalité tropicale où des fruits vivent "
        "en couples.\nRules: Un vote hebdomadaire élimine un couple.; Le coco-phone annonce les "
        "résultats.\nTime period: contemporain\nRecurring motifs: le téléphone en noix de coco, "
        "le bûcher, le miroir\n\nExisting cast:\n- Kiwilo — signature: une fine chaîne en or, "
        "une chemise en lin blanc\n- Mangella — signature: une pince à cheveux strass en forme "
        "de couronne\n\nPropose places and props for this story.\n\nGive:\n- places: 2 to 3 "
        "places, each with a name (at most 5 words) and a one_line description (at most 20 "
        "words)\n- props: 0 to 3 props drawn from the cast's signature items or the bible's "
        "recurring motifs, each with a name (at most 5 words), a one_line description (at most "
        "20 words), and an owner (an existing cast member, or null when it belongs to no one in "
        "particular)\n\nNever use real people, brands, studio names or copyrighted characters."
    )
    assert user == expected_user
    assert "Write all user-facing text in French." in system
    assert schema == schemas.p0_schema(["Kiwilo", "Mangella"])


def test_build_p0_no_cast_still_produces_a_usable_schema():
    pack = context.build_pack(language="en", story=_phase2_bible_and_world_story())
    _, user, schema = prompts.build_p0(pack, cast=[])
    assert "Existing cast:" not in user
    assert schema["properties"]["props"]["items"]["properties"]["owner"] == {"type": ["string", "null"]}


# --------------------------------------------------------------------------------- P1

def test_build_p1_key_lines():
    story = {
        "logline": "A logline.",
        "world": {
            "setting_summary": "A place.", "rules": ["r"], "time_period": "now",
            "recurring_motifs": ["m"],
        },
    }
    pack = context.build_pack(language="en", story=story, template=FRUIT_DRAMA)
    places_so_far = [{
        "name": "The Bonfire Circle", "one_line": "Where eliminated couples say goodbye.",
        "descriptor": "a wide stone circle ringed with dying embers",
    }]
    system, user, schema = prompts.build_p1(
        pack, place={"name": "The Old Pier", "one_line": "Where couples meet after the vote."},
        places_so_far=places_so_far,
    )
    assert "Write all user-facing text in English." in system
    assert "Existing places:" in user
    assert "- The Bonfire Circle: Where eliminated couples say goodbye. — looks:" in user
    assert "Place to write: The Old Pier" in user
    assert "One line: Where couples meet after the vote." in user
    assert "descriptor (English): the place alone, at most 45 words, no people, no characters" in user
    assert "layout_notes (English): what is left, right, back and foreground, at most 60 words" in user
    assert "1 to 3 variants from day, night, dusk, rain, dawn, always including day" in user
    assert schema == schemas.p1_schema()


def test_build_p1_regenerate_key_lines():
    pack = context.build_pack(language="en")
    regenerate = {"field": "text", "current": {"descriptor": "an old descriptor"}, "note": "make it darker"}
    _, user, _ = prompts.build_p1(
        pack, place={"name": "The Old Pier", "one_line": "Where couples meet."},
        places_so_far=[], regenerate=regenerate,
    )
    assert "Current values:" in user
    assert "Rewrite only `text`" in user
    assert "following the author's note: make it darker" in user


# --------------------------------------------------------------------------------- R1

def test_build_r1_key_lines():
    pack = context.build_pack(language="en", template=FRUIT_DRAMA)
    system, user, schema = prompts.build_r1(
        pack, prop={"name": "The Coconut Phone", "one_line": "Announces the weekly vote results."},
        cast=[{"name": "Kiwilo"}, {"name": "Mangella"}],
    )
    assert "Existing cast:\n- Kiwilo\n- Mangella" in user
    assert "Prop to write: The Coconut Phone" in user
    assert "One line: Announces the weekly vote results." in user
    assert "descriptor (English): the object alone, at most 30 words" in user
    assert "owner: an existing cast member this prop belongs to, or null" in user
    assert schema == schemas.r1_schema(["Kiwilo", "Mangella"])


def test_build_r1_regenerate_key_lines():
    pack = context.build_pack(language="en")
    regenerate = {"field": "text", "current": {"descriptor": "an old descriptor"}, "note": "make it shinier"}
    _, user, _ = prompts.build_r1(
        pack, prop={"name": "The Coconut Phone", "one_line": "Announces the vote."},
        cast=[], regenerate=regenerate,
    )
    assert "Current values:" in user
    assert "Rewrite only `text`" in user
    assert "following the author's note: make it shinier" in user


# --------------------------------------------------------------------------------- S1

def test_build_s1_golden_fr():
    pack = context.build_pack(language="fr", story=_phase2_bible_story())
    cast = [
        {"name": "Kiwilo", "role": "lead", "one_line": "Kiwilo séduit tout le monde pour survivre au vote."},
        {"name": "Mangella", "role": "lead", "one_line": "Mangella veut gagner à sa façon."},
    ]
    places = [{"name": "Le Verger Central", "one_line": "La place principale où se réunissent tous les concurrents."}]
    system, user, schema = prompts.build_s1(pack, episodes=8, cast=cast, places=places)

    expected_user = (
        "Bible written so far:\nSur une île de téléréalité, des fruits forment des couples et "
        "complotent pour survivre au vote hebdomadaire. Chaque semaine, les concurrents forment "
        "des couples pour ne pas etre elimines. Un telephone en noix de coco annonce les votes "
        "du public. Tone: melodramatique, complice, rapide.\n\n"
        "Existing cast:\n- Kiwilo (lead): Kiwilo séduit tout le monde pour survivre au vote.\n"
        "- Mangella (lead): Mangella veut gagner à sa façon.\n\nExisting places:\n- Le Verger "
        "Central: La place principale où se réunissent tous les concurrents.\n\nWrite the "
        "season arc skeleton for 8 episodes.\n\nGive exactly 8 entries in `arc`, one per "
        "episode, each with:\n- ep: the episode number, 1 to 8\n- function: one of setup, "
        "escalation, complication, midpoint_twist, crisis, climax_and_reset\n- summary: at "
        "most 25 words\n\nEpisode 1 must be `setup`. Episode 8 must be `climax_and_reset`. "
        "Exactly one episode near the middle must be `midpoint_twist`.\n\nNever use real "
        "people, brands, studio names or copyrighted characters."
    )
    assert user == expected_user
    assert "Write all user-facing text in French." in system
    assert schema == schemas.s1_schema(8)


def test_build_s1_no_cast_or_places():
    pack = context.build_pack(language="en")
    _, user, schema = prompts.build_s1(pack, episodes=3, cast=[], places=[])
    assert "Existing cast:" not in user
    assert "Existing places:" not in user
    assert "Write the season arc skeleton for 3 episodes." in user
    assert schema == schemas.s1_schema(3)


# --------------------------------------------------------------------------------- S2

def _tentafruit_skeleton_arc():
    return [
        {"ep": 1, "function": "setup", "summary": "The couples arrive on the island."},
        {"ep": 2, "function": "midpoint_twist", "summary": "A shocking betrayal is revealed."},
        {"ep": 3, "function": "climax_and_reset", "summary": "The last couple faces the truth."},
    ]


def test_build_s2_key_lines():
    pack = context.build_pack(language="en")
    arc = _tentafruit_skeleton_arc()
    system, user, schema = prompts.build_s2(
        pack, entry=arc[0], arc=arc, cast=[{"name": "Kiwilo"}, {"name": "Mangella"}],
    )
    assert "Existing cast:\n- Kiwilo\n- Mangella" in user
    assert "Season arc so far:" in user
    assert "- ep1 (setup): The couples arrive on the island.  <- expand this one" in user
    assert "- ep2 (midpoint_twist): A shocking betrayal is revealed." in user
    assert "- ep3 (climax_and_reset): The last couple faces the truth." in user
    assert "summary: at most 60 words" in user
    assert "open_hooks_in: 0 to 3 hooks this episode resolves, each at most 15 words" in user
    assert "open_hooks_out: 1 to 3 hooks this episode leaves open, each at most 15 words" in user
    assert "characters: 1 to 5 of the existing cast involved in this episode" in user
    assert schema == schemas.s2_schema(["Kiwilo", "Mangella"])


def test_build_s2_regenerate_key_lines():
    pack = context.build_pack(language="en")
    arc = _tentafruit_skeleton_arc()
    regenerate = {"field": "text", "current": {"summary": "old summary"}, "note": "raise the stakes"}
    _, user, _ = prompts.build_s2(pack, entry=arc[0], arc=arc, cast=[], regenerate=regenerate)
    assert "Current values:" in user
    assert "Rewrite only `text`" in user
    assert "following the author's note: raise the stakes" in user


# --------------------------------------------------------------------------------- U1

def test_build_u1_key_lines():
    system, user, schema = prompts.build_u1(language="fr")
    assert "The story is written in French" in system
    assert "appearance_notes is always in English" in system
    assert "Reply with JSON only, matching the schema" in system
    assert "Never name or identify any real person" in system
    assert "at most 40 words" in user
    assert "body shape, colours, clothing, accessories, distinctive marks" in user
    assert "Do not name or identify any real person" in user
    assert "describe only clothing and colours" in user
    assert schema == schemas.u1_schema()


def test_build_u1_unknown_language_falls_back_to_the_code():
    system, _, _ = prompts.build_u1(language="es")
    assert "The story is written in es" in system


def test_build_u1_has_no_pack_or_name_parameter():
    params = set(inspect.signature(prompts.build_u1).parameters)
    assert "pack" not in params
    assert "name" not in params


# --------------------------------------------------------- ENTITY_REGENERATE grammar

def test_entity_regenerate_grammar():
    assert prompts.ENTITY_REGENERATE == {
        "character": "K1", "place": "P1", "prop": "R1", "season": "S2",
    }


def test_regenerate_targets_untouched_by_phase_2():
    """REGENERATE_TARGETS (the bible's field-level grammar) is unchanged; the
    new entity-level grammar lives in its own dict (spec 9.2)."""
    assert tuple(prompts.REGENERATE_TARGETS.keys()) == ("logline", "premise", "tone", "world", "themes")


# --------------------------------------------------------------- caps and temperatures

def test_phase2_temperatures():
    for prompt_id in ("K1", "P0", "P1", "R1", "S1", "S2"):
        assert prompts.TEMPERATURE[prompt_id] is prompts.WRITING_TEMPERATURE
    assert prompts.TEMPERATURE["U1"] is prompts.ANALYTIC_TEMPERATURE


# ---------------------------------------------------------------- LLM schemas (owner/enum)

def test_k1_schema_relationship_enum_uses_the_given_cast_names():
    schema = schemas.k1_schema(["Mangella", "Broccolia"])
    assert schema["properties"]["relationships"]["items"]["properties"]["with"] == {
        "type": "string", "enum": ["Mangella", "Broccolia"],
    }


def test_p0_schema_owner_enum_includes_null():
    schema = schemas.p0_schema(["Kiwilo"])
    owner = schema["properties"]["props"]["items"]["properties"]["owner"]
    assert owner == {"type": ["string", "null"], "enum": ["Kiwilo", None]}


def test_r1_schema_owner_enum_includes_null():
    schema = schemas.r1_schema(["Kiwilo"])
    assert schema["properties"]["owner"] == {"type": ["string", "null"], "enum": ["Kiwilo", None]}


def test_s2_schema_characters_enum_uses_the_given_cast_names():
    schema = schemas.s2_schema(["Kiwilo", "Mangella"])
    assert schema["properties"]["characters"]["items"] == {"type": "string", "enum": ["Kiwilo", "Mangella"]}


def test_s1_schema_bakes_the_episode_count_into_its_description():
    schema = schemas.s1_schema(5)
    assert schema["properties"]["arc"]["description"] == "exactly 5 entries, one per episode"


# =============================================================== phase-2 post-validators

def _good_k1_doc():
    return {
        "descriptor": "a small round character with smooth green skin and a bright yellow scarf",
        "signature_items": ["a bright yellow scarf", "a small silver whistle"],
        "personality": {
            "traits": ["curious", "quick to laugh", "fiercely loyal"],
            "wants": "To prove herself worthy of the crew's trust.",
            "fears": "Being left behind when the tide turns.",
            "speech_style": "Short bursts, trailing off mid-sentence.",
        },
        "voice": {
            "gender": "female",
            "age": "young",
            "style_tags": ["bright", "fast"],
            "direction": "Quick and bright, a little breathless.",
            "sample_line": "I already know what you're going to say.",
        },
        "relationships": [
            {"with": "Mangella", "relation": "Her oldest rival on the island."},
        ],
    }


def test_k1_errors_good_fixture_passes():
    assert schemas.k1_errors(_good_k1_doc(), "Kiwilo") == []


@pytest.mark.parametrize(
    "mutate, mentions",
    [
        (lambda d: d.__setitem__("descriptor", " ".join(f"w{i}" for i in range(46))), "descriptor"),
        (lambda d: d.__setitem__("signature_items", d["signature_items"][:1]), "signature_items"),
        (lambda d: d["signature_items"].__setitem__(0, " ".join(f"w{i}" for i in range(9))), "signature_items"),
        (lambda d: d["personality"].__setitem__("traits", d["personality"]["traits"][:1]), "traits"),
        (lambda d: d["personality"]["traits"].__setitem__(0, " ".join(f"w{i}" for i in range(5))), "traits"),
        (lambda d: d["personality"].__setitem__("wants", " ".join(f"w{i}" for i in range(26))), "wants"),
        (lambda d: d["personality"].__setitem__("fears", " ".join(f"w{i}" for i in range(26))), "fears"),
        (lambda d: d["personality"].__setitem__("speech_style", " ".join(f"w{i}" for i in range(26))), "speech_style"),
        (lambda d: d["voice"].__setitem__("style_tags", []), "style_tags"),
        (lambda d: d["voice"].__setitem__("direction", " ".join(f"w{i}" for i in range(21))), "direction"),
        (lambda d: d["voice"].__setitem__("sample_line", " ".join(f"w{i}" for i in range(13))), "sample_line"),
        (lambda d: d.__setitem__("relationships", d["relationships"] * 6), "relationships"),
        (lambda d: d["relationships"][0].__setitem__("relation", " ".join(f"w{i}" for i in range(16))), "relation"),
    ],
)
def test_k1_errors_each_violation(mutate, mentions):
    doc = _good_k1_doc()
    mutate(doc)
    errors = schemas.k1_errors(doc, "Kiwilo")
    assert errors, "expected at least one error"
    assert any(mentions in e for e in errors)


def test_k1_errors_name_leak_refused():
    doc = _good_k1_doc()
    doc["descriptor"] = "a Kiwilo-shaped character with green skin and a bright yellow scarf"
    errors = schemas.k1_errors(doc, "Kiwilo")
    assert errors
    assert any("own name" in e for e in errors)


def test_k1_errors_name_leak_is_case_insensitive():
    doc = _good_k1_doc()
    doc["signature_items"] = ["a KIWILO-branded scarf", "a small silver whistle"]
    errors = schemas.k1_errors(doc, "Kiwilo")
    assert errors
    assert any("own name" in e for e in errors)


def test_k1_errors_name_substring_inside_other_words_is_not_a_mention():
    doc = _good_k1_doc()
    doc["descriptor"] = "a lean figure with gold earrings and a long string of beads"
    assert schemas.k1_errors(doc, "Rin") == []


def test_k1_errors_name_as_a_whole_word_is_refused():
    doc = _good_k1_doc()
    doc["descriptor"] = "Rin wears a patched green coat with a frayed collar"
    errors = schemas.k1_errors(doc, "Rin")
    assert errors
    assert any("own name" in e for e in errors)


def test_k1_errors_name_with_possessive_is_refused():
    doc = _good_k1_doc()
    doc["descriptor"] = "Rin's locket hangs from a cord around the neck"
    errors = schemas.k1_errors(doc, "Rin")
    assert errors
    assert any("own name" in e for e in errors)


def test_k1_errors_name_upper_case_is_refused():
    doc = _good_k1_doc()
    doc["descriptor"] = "always called RIN by the crew, sharp-eyed and quick"
    errors = schemas.k1_errors(doc, "Rin")
    assert errors
    assert any("own name" in e for e in errors)


def test_k1_errors_multiword_name_matches_as_a_phrase():
    doc = _good_k1_doc()
    doc["descriptor"] = "captain obvious grins beneath a battered old hat"
    errors = schemas.k1_errors(doc, "Captain Obvious")
    assert errors
    assert any("own name" in e for e in errors)


def test_k1_errors_multiword_name_out_of_order_is_not_a_mention():
    doc = _good_k1_doc()
    doc["descriptor"] = "an obvious captain's hat sits crooked on the head"
    assert schemas.k1_errors(doc, "Captain Obvious") == []


def test_k1_errors_name_accented_case_insensitive():
    doc = _good_k1_doc()
    doc["descriptor"] = "le maire pâton sourit doucement à la foule rassemblée"
    errors = schemas.k1_errors(doc, "Maire Pâton")
    assert errors
    assert any("own name" in e for e in errors)


def test_k1_errors_name_leak_in_signature_item_is_still_caught():
    doc = _good_k1_doc()
    doc["signature_items"] = ["Rin's woven bracelet", "a small tin whistle"]
    errors = schemas.k1_errors(doc, "Rin")
    assert errors
    assert any("signature_items" in e and "own name" in e for e in errors)


def test_k1_errors_name_leak_in_sample_line_is_still_caught():
    doc = _good_k1_doc()
    doc["voice"]["sample_line"] = "Rin never backs down from a challenge."
    errors = schemas.k1_errors(doc, "Rin")
    assert errors
    assert any("voice.sample_line" in e and "own name" in e for e in errors)


def _good_p0_doc():
    return {
        "places": [
            {"name": "The Old Pier", "one_line": "Where the couples meet after the vote to plan their next move."},
            {"name": "The Bonfire Circle", "one_line": "Where eliminated couples say their goodbyes in front of everyone."},
        ],
        "props": [
            {"name": "The Coconut Phone", "one_line": "Announces the weekly vote results to the whole island.", "owner": None},
        ],
    }


def test_p0_errors_good_fixture_passes():
    assert schemas.p0_errors(_good_p0_doc()) == []


@pytest.mark.parametrize(
    "mutate, mentions",
    [
        (lambda d: d.__setitem__("places", d["places"][:1]), "places"),
        (lambda d: d["places"][0].__setitem__("name", " ".join(f"w{i}" for i in range(6))), "name"),
        (lambda d: d["places"][0].__setitem__("one_line", " ".join(f"w{i}" for i in range(21))), "one_line"),
        (lambda d: d.__setitem__("props", d["props"] * 4), "props"),
    ],
)
def test_p0_errors_each_violation(mutate, mentions):
    doc = _good_p0_doc()
    mutate(doc)
    errors = schemas.p0_errors(doc)
    assert errors, "expected at least one error"
    assert any(mentions in e for e in errors)


def _good_p1_doc():
    return {
        "descriptor": "a quiet stretch of beach with white sand and a row of tilted palm trees",
        "layout_notes": (
            "the palm trees line the left side, the water stretches on the right, a bonfire "
            "pit sits in the foreground"
        ),
        "time_variants": ["day", "night"],
    }


def test_p1_errors_good_fixture_passes():
    assert schemas.p1_errors(_good_p1_doc()) == []


@pytest.mark.parametrize(
    "mutate, mentions",
    [
        (lambda d: d.__setitem__("descriptor", " ".join(f"w{i}" for i in range(46))), "descriptor"),
        (lambda d: d.__setitem__("layout_notes", " ".join(f"w{i}" for i in range(61))), "layout_notes"),
        (lambda d: d.__setitem__("time_variants", ["night", "dusk"]), "day"),
        (lambda d: d.__setitem__("time_variants", ["day", "day"]), "duplicate"),
    ],
)
def test_p1_errors_each_violation(mutate, mentions):
    doc = _good_p1_doc()
    mutate(doc)
    errors = schemas.p1_errors(doc)
    assert errors, "expected at least one error"
    assert any(mentions in e for e in errors)


def _good_r1_doc():
    return {"descriptor": "a small brass telephone shaped like a coconut shell with a coiled cord", "owner": None}


def test_r1_errors_good_fixture_passes():
    assert schemas.r1_errors(_good_r1_doc()) == []


def test_r1_errors_descriptor_too_long():
    doc = _good_r1_doc()
    doc["descriptor"] = " ".join(f"w{i}" for i in range(31))
    errors = schemas.r1_errors(doc)
    assert errors
    assert any("descriptor" in e for e in errors)


def _good_s1_doc():
    return {"arc": [
        {"ep": 1, "function": "setup", "summary": "The couples arrive on the island and learn the rules of the weekly vote."},
        {"ep": 2, "function": "midpoint_twist", "summary": "A shocking betrayal is revealed live in front of the whole island."},
        {"ep": 3, "function": "climax_and_reset", "summary": "The last couple standing faces the truth about their own lies."},
    ]}


def test_s1_errors_good_fixture_passes():
    assert schemas.s1_errors(_good_s1_doc(), 3) == []


@pytest.mark.parametrize(
    "mutate, mentions",
    [
        (lambda d: d["arc"][0].__setitem__("summary", " ".join(f"w{i}" for i in range(26))), "summary"),
        (lambda d: d["arc"][0].__setitem__("function", "escalation"), "setup"),
        (lambda d: d["arc"][-1].__setitem__("function", "crisis"), "climax_and_reset"),
        (lambda d: d["arc"][1].__setitem__("function", "escalation"), "midpoint_twist"),
        (lambda d: d["arc"].pop(), "exactly"),
    ],
)
def test_s1_errors_each_violation(mutate, mentions):
    doc = _good_s1_doc()
    mutate(doc)
    errors = schemas.s1_errors(doc, 3)
    assert errors, "expected at least one error"
    assert any(mentions in e for e in errors)


def _good_s2_doc():
    return {
        "summary": "Kiwilo realizes the stolen phone belongs to Mangella, which changes everything about their fragile alliance.",
        "open_hooks_in": ["The phone stolen last episode."],
        "open_hooks_out": ["Who really took the phone in the first place."],
        "characters": ["Kiwilo", "Mangella"],
    }


def test_s2_errors_good_fixture_passes():
    assert schemas.s2_errors(_good_s2_doc()) == []


@pytest.mark.parametrize(
    "mutate, mentions",
    [
        (lambda d: d.__setitem__("summary", " ".join(f"w{i}" for i in range(61))), "summary"),
        (lambda d: d.__setitem__("open_hooks_in", d["open_hooks_in"] * 4), "open_hooks_in"),
        (lambda d: d.__setitem__("open_hooks_out", []), "open_hooks_out"),
        (lambda d: d.__setitem__("characters", []), "characters"),
    ],
)
def test_s2_errors_each_violation(mutate, mentions):
    doc = _good_s2_doc()
    mutate(doc)
    errors = schemas.s2_errors(doc)
    assert errors, "expected at least one error"
    assert any(mentions in e for e in errors)


def _good_u1_doc():
    return {"appearance_notes": "A round green character with a yellow scarf and a small silver whistle around the neck."}


def test_u1_errors_good_fixture_passes():
    assert schemas.u1_errors(_good_u1_doc()) == []


def test_u1_errors_too_long():
    doc = _good_u1_doc()
    doc["appearance_notes"] = " ".join(f"w{i}" for i in range(41))
    errors = schemas.u1_errors(doc)
    assert errors
    assert any("appearance_notes" in e for e in errors)


# ===================================================== phase-2 truncation guard (spec 4.1)
#
# Same rationale as the phase-1 guard above: the largest reply each prompt allows -- every
# stated word limit hit exactly, English fields in English, story-language fields in French
# -- must fit the prompt's MAX_TOKENS cap. S1's fixture uses 12 episodes, its stated maximum
# (spec 2.6 EPISODES_PLANNED_MAX), since that is the worst case the cap must survive.

_FR_K1 = {
    "descriptor": (
        "a plump anthropomorphic peach with soft fuzzy pink-orange skin, a small green stem "
        "curling from her crown, warm freckled cheeks, wide expressive eyes rimmed with pale "
        "lashes, delicate rounded shoulders, a faint pink blush across her face, and short "
        "soft arms that never quite reach"
    ),
    "signature_items": [
        "a cracked wooden locket tied with frayed twine",
        "a pair of mismatched button earrings, always crooked",
        "a faded apron pocket stuffed with dried flowers",
    ],
    "personality": {
        "traits": [
            "douce en apparence, tetue",
            "prete a pardonner vite",
            "curieuse de tout, toujours",
            "genereuse jusqu'a tout s'oublier",
            "nerveuse pres du bucher",
        ],
        "wants": (
            "Elle veut prouver qu'elle merite sa place sur l'ile sans devoir seduire ni mentir "
            "a personne, meme si cela signifie rester seule jusqu'a la finale."
        ),
        "fears": (
            "Elle craint que tout le monde decouvre qu'elle a deja triche une fois et que ce "
            "secret revienne au pire moment possible avant le vote."
        ),
        "speech_style": (
            "Elle parle vite, coupe ses phrases en deux, s'excuse presque toujours apres avoir "
            "dit quelque chose de dur, puis rit pour detendre l'atmosphere autour d'elle."
        ),
    },
    "voice": {
        "gender": "female",
        "age": "young",
        "style_tags": ["warm", "nervous", "playful"],
        "direction": (
            "Warm and slightly breathless, quick to laugh, then suddenly quiet and very "
            "careful with her own words near the end"
        ),
        "sample_line": "Je n'ai jamais vraiment menti, enfin, pas tout a fait, pas cette-fois",
    },
    "relationships": [
        {"with": "Kiwilo", "relation": "Elle admire Kiwilo en secret depuis le debut mais refuse de l'admettre devant les cameras"},
        {"with": "Broccolia", "relation": "Elle protege Broccolia depuis leur toute premiere semaine passee sur l'ile, sans jamais rien dire"},
        {"with": "Pepperino", "relation": "Elle se mefie de Pepperino depuis le jour ou il a trahi sa meilleure amie"},
        {"with": "Avocardo", "relation": "Elle doit presque tout a Avocardo, qui l'a aidee a rester encore dans le jeu"},
        {"with": "Mangella", "relation": "Elle ne pardonne toujours pas a Mangella d'avoir revele son secret devant tout le public"},
    ],
}

_FR_P0 = {
    "places": [
        {"name": "Le Verger de la Reine",
         "one_line": "Un verger secret ou seuls les couples les plus proches se retrouvent pour parler loin des cameras et des votes."},
        {"name": "La Plage aux Coquillages Dores",
         "one_line": "Une plage cachee derriere les rochers ou les concurrents confient leurs vrais secrets loin des micros et des oreilles indiscretes."},
        {"name": "Le Vieux Pont de Pierre",
         "one_line": "Un pont fragile que tout le monde traverse pour rejoindre le bucher de l'elimination chaque semaine, sans regarder en bas."},
    ],
    "props": [
        {"name": "Le Vieux Telephone en Coco", "owner": None,
         "one_line": "Le telephone qui annonce chaque vote hebdomadaire et que tout le monde redoute d'entendre sonner au milieu de la nuit."},
        {"name": "La Chaine en Or Volee", "owner": "Kiwilo",
         "one_line": "La chaine en or que Kiwilo cache depuis le debut et que quelqu'un a discretement volee pendant la derniere ceremonie."},
        {"name": "Le Vieux Journal Intime Cache", "owner": "Mangella",
         "one_line": "Un journal intime plein de secrets que personne n'est jamais cense lire, cache sous une pierre pres du vieux pont."},
    ],
}

_FR_P1 = {
    "descriptor": (
        "a sprawling open-air market square paved with cracked terracotta tiles, rows of "
        "wooden fruit stalls draped in faded striped awnings, strings of paper lanterns "
        "crossing overhead between two crumbling stone archways, and a dry stone fountain "
        "sitting silent at the very center of it all"
    ),
    "layout_notes": (
        "The fruit stalls line the entire left side under the striped awnings, a raised "
        "wooden stage for announcements sits at the very back of the square near the old "
        "wall, the dry stone fountain anchors the foreground center, and the market exit "
        "through the crumbling stone archway sits on the far right side of the frame, half "
        "hidden in shadow"
    ),
    "time_variants": ["day", "night", "dusk"],
}

_FR_R1 = {
    "descriptor": (
        "a small tarnished brass telephone shaped like a hollowed coconut shell, a coiled "
        "cord wrapped twice around its worn wooden base, one cracked earpiece, and a faded "
        "hand-painted red dial"
    ),
    "owner": None,
}

_FR_S1_SUMMARIES = [
    "Kiwilo et Mangella arrivent sur l'ile et decouvrent les regles cruelles du vote hebdomadaire devant tout le public rassemble sous les projecteurs de la ceremonie.",
    "Une toute premiere alliance secrete se forme entre deux couples pendant que Broccolia observe tout depuis son poste de presentatrice sans jamais rien laisser paraitre.",
    "Pepperino seme la discorde en flirtant ouvertement avec plusieurs concurrentes, ce qui met en colere plus d'un couple sur l'ile des le tout premier soir.",
    "Avocardo tente d'acheter secretement des votes avec sa fortune familiale, mais son plan est presque decouvert par Mangella elle-meme juste avant la toute grande ceremonie.",
    "Un premier couple est enfin elimine dans les larmes pendant que Kiwilo commence deja a soupconner un veritable complot general contre tous les nouveaux arrivants.",
    "Kiwilo decouvre que Broccolia cache un lien secret avec un tout ancien concurrent elimine lors d'une saison precedente et jamais vraiment oubliee par toute l'ile.",
    "La tension explose enfin quand Mangella revele publiquement une grave trahison, retournant plusieurs alliances fragiles en plein milieu de l'emission devant tout le public choque.",
    "Un flashback devoile enfin pourquoi Pepperino est vraiment arrive sur cette ile, changeant totalement la facon dont le public entier le voit depuis ce jour-la.",
    "Avocardo perd soudain toute sa fortune apres un pari desastreux, forcant une alliance inattendue avec son pire ennemi jure sur toute l'ile entiere ce soir.",
    "Kiwilo doit enfin choisir entre proteger Mangella ou sauver sa propre place dans le jeu avant le tout vote decisif de cette semaine tres difficile.",
    "Toutes les alliances s'effondrent soudain en meme temps pendant que le vieux telephone en coco annonce un vote final surprenant a toute l'ile enfin reunie.",
    "Le dernier couple debout affronte enfin la verite sur ses propres mensonges pendant que toute l'ile entiere retient son souffle avant le tout dernier vote.",
]
_FR_S1_FUNCTIONS = [
    "setup", "escalation", "escalation", "complication", "complication", "complication",
    "midpoint_twist", "crisis", "crisis", "crisis", "crisis", "climax_and_reset",
]
_FR_S1 = {
    "arc": [
        {"ep": i + 1, "function": _FR_S1_FUNCTIONS[i], "summary": _FR_S1_SUMMARIES[i]}
        for i in range(12)
    ],
}

_FR_S2 = {
    "summary": (
        "Reinette decouvre enfin que le noyau vole appartient en realite a Griotte, ce qui "
        "change absolument tout ce qu'elle croyait vraiment savoir sur leur tres longue amitie "
        "depuis le tout debut de cette longue saison bien difficile, et la pousse finalement a "
        "se confier entierement a Poiron juste avant que la grande recolte n'efface a nouveau "
        "tous leurs souvenirs partages."
    ),
    "open_hooks_in": [
        "Le noyau vole au tout debut de l'episode qui vient tout juste de se terminer",
        "La violente dispute entre Reinette et Griotte pres du tout vieux pont de pierre grise",
    ],
    "open_hooks_out": [
        "Qui a vraiment cache le second noyau sous les vieilles racines du tres grand cerisier",
        "Pourquoi Poiron refuse maintenant de toujours regarder Reinette bien droit dans ses deux grands yeux",
        "Ce que Madame Coing sait deja vraiment sur ce qui s'est vraiment passe cette nuit-la",
    ],
    "characters": ["Reinette", "Griotte", "Poiron", "Madame Coing", "Le Vieux Cerisier"],
}

_EN_U1 = {
    "appearance_notes": (
        "A round-bodied figure with soft rosy-orange skin, short stubby arms, and a wide "
        "friendly face. Wears a faded yellow apron over a striped shirt, mismatched button "
        "earrings, and carries a cracked wooden locket tied with brown twine around the neck"
    ),
}

_PHASE2_LARGEST_REPLIES = {
    "K1": _FR_K1, "P0": _FR_P0, "P1": _FR_P1, "R1": _FR_R1,
    "S1": _FR_S1, "S2": _FR_S2, "U1": _EN_U1,
}
_PHASE2_ERRORS = {
    "K1": lambda doc: schemas.k1_errors(doc, "Nectarina"),
    "P0": schemas.p0_errors,
    "P1": schemas.p1_errors,
    "R1": schemas.r1_errors,
    "S1": lambda doc: schemas.s1_errors(doc, 12),
    "S2": schemas.s2_errors,
    "U1": schemas.u1_errors,
}


@pytest.mark.parametrize("prompt_id", list(_PHASE2_LARGEST_REPLIES))
def test_the_largest_phase2_reply_is_valid(prompt_id):
    reply = _PHASE2_LARGEST_REPLIES[prompt_id]
    assert _PHASE2_ERRORS[prompt_id](reply) == []


def test_the_largest_phase2_replies_hit_every_stated_limit_exactly():
    assert _words(_FR_K1["descriptor"]) == 45
    assert [_words(item) for item in _FR_K1["signature_items"]] == [8, 8, 8]
    assert [_words(t) for t in _FR_K1["personality"]["traits"]] == [4] * 5
    assert _words(_FR_K1["personality"]["wants"]) == 25
    assert _words(_FR_K1["personality"]["fears"]) == 25
    assert _words(_FR_K1["personality"]["speech_style"]) == 25
    assert _words(_FR_K1["voice"]["direction"]) == 20
    assert _words(_FR_K1["voice"]["sample_line"]) == 12
    assert len(_FR_K1["relationships"]) == 5
    assert [_words(r["relation"]) for r in _FR_K1["relationships"]] == [15] * 5

    assert len(_FR_P0["places"]) == 3
    assert [_words(p["name"]) for p in _FR_P0["places"]] == [5] * 3
    assert [_words(p["one_line"]) for p in _FR_P0["places"]] == [20] * 3
    assert len(_FR_P0["props"]) == 3
    assert [_words(p["name"]) for p in _FR_P0["props"]] == [5] * 3
    assert [_words(p["one_line"]) for p in _FR_P0["props"]] == [20] * 3

    assert _words(_FR_P1["descriptor"]) == 45
    assert _words(_FR_P1["layout_notes"]) == 60
    assert len(_FR_P1["time_variants"]) == 3

    assert _words(_FR_R1["descriptor"]) == 30

    assert len(_FR_S1["arc"]) == 12
    assert [_words(e["summary"]) for e in _FR_S1["arc"]] == [25] * 12
    assert _FR_S1["arc"][0]["function"] == "setup"
    assert _FR_S1["arc"][-1]["function"] == "climax_and_reset"
    assert "midpoint_twist" in [e["function"] for e in _FR_S1["arc"]]

    assert _words(_FR_S2["summary"]) == 60
    assert [_words(h) for h in _FR_S2["open_hooks_in"]] == [15] * 2
    assert [_words(h) for h in _FR_S2["open_hooks_out"]] == [15] * 3
    assert len(_FR_S2["characters"]) == 5

    assert _words(_EN_U1["appearance_notes"]) == 40


@pytest.mark.parametrize("prompt_id", list(_PHASE2_LARGEST_REPLIES))
def test_the_largest_phase2_reply_fits_its_cap_but_not_a_much_smaller_one(prompt_id):
    reply = _PHASE2_LARGEST_REPLIES[prompt_id]
    estimate = estimate_tokens(json.dumps(reply, ensure_ascii=False))
    needed = estimate * FRENCH_TOKEN_FACTOR
    cap = prompts.MAX_TOKENS[prompt_id]
    too_small = cap // 2

    assert needed > too_small, (
        f"{prompt_id}: the fixture needs only ~{needed:.0f} tokens, too little to prove the "
        f"{cap}-token cap does any work (a {too_small}-token cap would already fit it)"
    )
    assert needed <= cap, (
        f"{prompt_id}: the largest reply needs ~{needed:.0f} tokens "
        f"({estimate} by chars/4 x {FRENCH_TOKEN_FACTOR}), over its {cap}-token cap"
    )


# ============================================================== phase-2 token budget

def _phase2_large_cast():
    sketches = TENTAFRUIT_FR["cast_sketch"]
    descriptors = [
        "an anthropomorphic kiwi with fuzzy brown skin and bright green flesh visible at the mouth",
        "an anthropomorphic mango with smooth orange-yellow skin and a sly grin",
        "an anthropomorphic broccoli with tightly curled dark green florets and a stern expression",
        "an anthropomorphic red pepper with glossy taut skin and a mischievous smirk",
        "an anthropomorphic avocado with a rough pebbled dark green shell and a large pit-shaped belly",
    ]
    signature_items = [
        ["a thin gold chain", "a white linen shirt"],
        ["a rhinestone tiara hair clip"],
        ["a rhinestone-studded headset microphone"],
        ["heart-shaped sunglasses"],
        ["a signet ring stamped with the family crest"],
    ]
    return [
        {
            "name": sketch["name"], "role": sketch["role"], "one_line": sketch["one_line"],
            "descriptor": descriptors[i], "signature_items": signature_items[i],
        }
        for i, sketch in enumerate(sketches)
    ]


def _phase2_large_places():
    return [
        {"name": "Le Verger Central",
         "one_line": "La place principale où se réunissent tous les concurrents chaque semaine avant le vote."},
        {"name": "La Plage aux Coquillages",
         "one_line": "Une plage isolée où les couples se confient loin des caméras et du public."},
        {"name": "Le Bûcher de l'Élimination",
         "one_line": "L'endroit redouté où un couple est éliminé du jeu chaque semaine."},
    ]


_PHASE2_LONG_UPLOAD_NOTE = (
    "a hand-drawn reference sheet showing a plump green kiwi character wearing a thin gold "
    "chain, a loose white linen shirt open at the collar, and small round glasses, sketched "
    "from three different angles with notes about the fuzzy skin texture in the margins"
)


def test_phase2_builders_stay_within_token_budget_for_the_largest_fixture():
    pack = context.build_pack(language="fr", story=_phase2_bible_and_world_story(), template=FRUIT_DRAMA)
    cast = _phase2_large_cast()
    places = _phase2_large_places()
    arc = [
        {"ep": i + 1, "function": "escalation",
         "summary": "Un episode plein de rebondissements pour tous les personnages sur l'ile cette semaine-la."}
        for i in range(12)
    ]
    arc[0]["function"] = "setup"
    arc[-1]["function"] = "climax_and_reset"
    arc[5]["function"] = "midpoint_twist"

    calls = [
        prompts.build_k1(pack, character=KIWILO_SKETCH, cast_so_far=cast, upload_notes=_PHASE2_LONG_UPLOAD_NOTE),
        prompts.build_p0(pack, cast=cast),
        prompts.build_p1(
            pack, place={"name": places[0]["name"], "one_line": places[0]["one_line"]}, places_so_far=places,
        ),
        prompts.build_r1(
            pack, prop={"name": "Le Vieux Telephone", "one_line": "Le telephone qui annonce les votes."}, cast=cast,
        ),
        prompts.build_s1(pack, episodes=12, cast=cast, places=places),
        prompts.build_s2(pack, entry=arc[0], arc=arc, cast=cast),
    ]
    for system, user, _ in calls:
        tokens = context.check_budget(system, user)
        assert tokens <= context.PACK_TOKEN_BUDGET, f"{tokens} tokens over the {context.PACK_TOKEN_BUDGET}-token budget"


# ==================================================== context.py: cast/places sections

def test_entity_lines_renders_whatever_fields_are_present():
    entities = [
        {
            "name": "Kiwilo", "role": "lead", "one_line": "Kiwilo one line.",
            "descriptor": "a kiwi with green skin and a big smile and round glasses",
        },
        {"name": "Mangella", "signature_items": ["a rhinestone tiara"]},
    ]
    assert context.entity_lines(entities) == (
        "- Kiwilo (lead): Kiwilo one line. — looks: a kiwi with green skin and a big smile "
        "and round glasses\n- Mangella — signature: a rhinestone tiara"
    )


def test_entity_lines_trims_a_long_descriptor_and_marks_it_cut():
    long_descriptor = " ".join(f"w{i}" for i in range(20))
    text = context.entity_lines([{"name": "Kiwilo", "descriptor": long_descriptor}])
    assert text == "- Kiwilo — looks: " + " ".join(f"w{i}" for i in range(12)) + "…"


def test_cast_block_caps_at_max_members_and_reports_the_cut():
    cast = [{"name": f"Char{i}"} for i in range(15)]
    text, cut = context.cast_block(cast)
    assert cut is True
    assert len(text.split("\n")) == 12


def test_cast_block_under_the_cap_is_not_cut():
    cast = [{"name": "Kiwilo"}, {"name": "Mangella"}]
    text, cut = context.cast_block(cast)
    assert cut is False
    assert text == "- Kiwilo\n- Mangella"


def test_places_block_caps_at_max_items_and_reports_the_cut():
    places = [{"name": f"Place{i}", "one_line": "x"} for i in range(10)]
    text, cut = context.places_block(places)
    assert cut is True
    assert len(text.split("\n")) == 8


def test_build_pack_cast_and_places_are_rendered_and_trims_are_named():
    cast = [{"name": f"Char{i}"} for i in range(15)]
    places = [{"name": f"Place{i}", "one_line": "x"} for i in range(10)]
    pack = context.build_pack(language="en", cast=cast, places=places)
    assert "cast" in pack.trimmed
    assert "places" in pack.trimmed
    assert len(pack.cast.split("\n")) == 12
    assert len(pack.places.split("\n")) == 8


def test_build_pack_cast_and_places_default_to_none():
    pack = context.build_pack(language="en")
    assert pack.cast is None
    assert pack.places is None


def test_build_pack_character_design_rules_comes_from_the_template():
    pack = context.build_pack(language="en", template=FRUIT_DRAMA)
    assert pack.character_design_rules == FRUIT_DRAMA["character_design_rules"]


def test_build_pack_character_design_rules_is_none_without_a_template():
    pack = context.build_pack(language="en")
    assert pack.character_design_rules is None


# ================================================= C1v2 budget (plan 22 stage 2)

def test_c1v2_budget_with_a_400_word_brief():
    """C1v2's own worst case (DEC-274, DEC-138's method): a 400-word French
    brief (``context._BRIEF_WORD_LIMIT``, never cut), the widest style line,
    and the story-own avoid list at its cap (``context._AVOID_TITLE_LIMIT``,
    24 titles of 8 words each -- a brief drops the library's titles). Fits
    ``prompts.INPUT_BUDGET["C1v2"]``, which was sized on this same
    measurement (the module's own comment names the number)."""
    import test_story_episode_prompt_budgets as budgets

    brief = budgets._filler(400, round(400 * 5.8))
    avoid = [budgets._filler(8, round(8 * 5.8)) for _ in range(24)]
    pack = context.build_pack(
        language="fr",
        template=FRUIT_DRAMA,
        brief_text=brief,
        avoid_titles=avoid,
    )
    assert pack.trimmed == []  # the worst case must not itself be cut
    system, user, schema = prompts.build_c1_v2(pack, style_ids=STYLE_IDS, batch=10, of=10,
                                               angle=prompts.C1_ANGLES[-1])
    tokens = estimate_tokens(system, user)
    assert tokens <= prompts.INPUT_BUDGET["C1v2"]
