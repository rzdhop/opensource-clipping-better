"""AI Story plan 23 stage D2: universes in the concepts step.

A story that chose a universe (``generation_profile.universe``) writes its
C1v2 concept cards with one extra data block: the universe's species pool and
the lead species each of the batch's ten cards is assigned, a deterministic
rotation seeded by ``sha256(story_id + batch + species)`` (a species is used
once per batch while the pool allows). Each cast member names its species, the
card records ``universe`` (its id and the assigned lead), and a brand name in
the card is a told-why retry (DEC-259). A story with no universe -- the C1 and
C1v2 prompts of every story made before this stage -- is untouched, byte for
byte (RC-W2): the existing goldens are run again here.

Stdlib + pytest (DEC-012); the same harness as ``test_story_concepts_brief.py``.
"""

from __future__ import annotations

import copy
import pathlib

import pytest

import test_story_concepts_brief as brief_tests
import test_story_prompts as prompt_tests
import test_story_steps as tss
from clipping.aistory import context, defaults, media_policy, prompts, schemas, templates, universes
from clipping.aistory.steps import concepts as concepts_step
from clipping.aistory.store import StoryStore
from clipping.providers.pacing import estimate_tokens

STYLE_IDS = templates.list_style_ids()
FRUIT_DRAMA = templates.load_style("fruit_drama")
NOW = tss.NOW
SETTINGS = tss.SETTINGS
LINK = tss.LINK
BRIEF = brief_tests.BRIEF
ROOT = pathlib.Path(__file__).resolve().parents[1]
STORY_ID = "0123456789ab"


def _story(store, *, universe="fruits", style="fruit_drama"):
    profile = {"writing": defaults.WRITING_V3}
    if universe:
        profile["universe"] = universe
    return store.create(language="fr", seed_text=BRIEF, style_template_id=style, generation_profile=profile,
                        now=NOW)["story_id"]


def _card(species="kiwi", *, title="Le Couloir des Masques", member_species=True):
    card = brief_tests._card(title=title)
    if member_species:
        for i, member in enumerate(card["cast_sketch"]):
            member["species"] = species if i == 0 else f"{species} cousin"
    return card


# ================================================================ the rotation

@pytest.mark.parametrize("universe", templates.load_universes(), ids=lambda u: u["id"])
def test_the_rotation_is_seeded_and_unique_within_a_batch(universe):
    pool = universe["species"]
    first = universes.assign_species(STORY_ID, 0, pool)
    assert first == universes.assign_species(STORY_ID, 0, list(pool))          # deterministic
    assert len(first) == 10 and set(first) <= set(pool)
    if len(pool) >= 10:
        assert len(set(first)) == 10                                           # unique while the pool allows
    else:
        assert len(set(first[:len(pool)])) == len(pool)                        # a whole ordering, then a fresh one
    assert all(a != b for a, b in zip(first, first[1:]))                       # never twice in a row
    # Seeded by the story and the batch: another of either reorders it (with pools this size, surely).
    assert first != universes.assign_species("ba9876543210", 0, pool)
    assert first != universes.assign_species(STORY_ID, 1, pool)
    # The order of the pool is not the seed: the same set in another order gives the same assignment.
    assert first == universes.assign_species(STORY_ID, 0, list(reversed(pool)))


def test_the_rotation_is_the_sha256_ordering_and_does_not_use_random():
    import hashlib

    pool = ["a", "b", "c"]
    expected = sorted(pool, key=lambda s: hashlib.sha256(f"{STORY_ID}:2:0:{s}".encode()).hexdigest())
    assert universes.assign_species(STORY_ID, 2, pool, size=3) == expected
    assert universes.assign_species(STORY_ID, 2, []) == []
    assert universes.assign_species(STORY_ID, 2, ["only"], size=4) == ["only"] * 4
    source = (ROOT / "clipping" / "aistory" / "universes.py").read_text(encoding="utf-8")
    assert "import random" not in source


def test_card_slots_run_through_batches_of_ten_however_the_runs_are_chunked():
    assert [universes.card_slot(0, call) for call in (1, 2, 10)] == [(0, 0), (0, 1), (0, 9)]
    assert universes.card_slot(10, 1) == (1, 0) and universes.card_slot(7, 5) == (1, 1)
    # Ten one-card runs (agent mode) see the ten species of batch 0, not one species ten times.
    pool = templates.universe("fruits")["species"]
    seen = []
    for n in range(10):
        batch, position = universes.card_slot(n, 1)
        seen.append(universes.assign_species(STORY_ID, batch, pool)[position])
    assert seen == universes.assign_species(STORY_ID, 0, pool) and len(set(seen)) == 10


def test_the_species_block_for_one_fruits_story_is_readable():
    universe = templates.universe("fruits")
    batch = universes.assign_species(STORY_ID, 0, universe["species"])
    block = universes.species_block(universe, batch_species=batch, position=2)
    lines = block.split("\n")
    assert lines[0] == "Universe: Fruits -- every character is an anthropomorphic fruit."
    assert lines[1] == "Species pool: " + ", ".join(universe["species"]) + "."
    assert lines[2] == ("Lead species of this batch's cards, in order: "
                        + ", ".join(f"{i} {s}" for i, s in enumerate(batch, start=1)) + ".")
    assert lines[3].startswith(f"This card's lead species: {batch[2]}. Where the brief already says what")
    assert lines[3].endswith("Give each character its species.")
    assert len(lines) == 4


# ================================================================ the prompt

def test_without_a_universe_c1_and_c1v2_are_byte_identical_the_existing_goldens_hold():
    prompt_tests.test_build_c1_golden_fr_fruit_drama()
    brief_tests.test_build_c1v2_golden_fr()
    pack = context.build_pack(language="fr", template=FRUIT_DRAMA, brief_text=BRIEF)
    assert pack.universe is None
    system, user, schema = prompts.build_c1_v2(pack, style_ids=STYLE_IDS, batch=1, of=10, angle=prompts.C1_ANGLES[0])
    assert schema == schemas.c1_schema(STYLE_IDS)
    assert "species" not in str(schema) and "Species pool" not in user and "Universe:" not in user
    assert schemas.c1_schema(STYLE_IDS) == schemas.c1_schema(STYLE_IDS, species=False)


def test_with_a_universe_the_block_is_the_only_addition_and_the_schema_asks_for_species():
    universe = templates.universe("drinks_sodas")
    batch = universes.assign_species(STORY_ID, 0, universe["species"])
    block = universes.species_block(universe, batch_species=batch, position=0)
    plain = context.build_pack(language="fr", template=FRUIT_DRAMA, brief_text=BRIEF, avoid_titles=["Un Titre"])
    rich = context.build_pack(language="fr", template=FRUIT_DRAMA, brief_text=BRIEF, avoid_titles=["Un Titre"],
                              universe=block)
    args = dict(style_ids=STYLE_IDS, batch=1, of=10, angle=prompts.C1_ANGLES[0])
    sys_plain, user_plain, schema_plain = prompts.build_c1_v2(plain, **args)
    sys_rich, user_rich, schema_rich = prompts.build_c1_v2(rich, **args)
    assert sys_rich == sys_plain
    # The block sits between the style line and the avoid list, as one data block.
    style_line, _, rest = user_plain.partition("\n\nDo not repeat")
    assert user_rich == f"{style_line}\n\n{block}\n\nDo not repeat{rest}"
    member = schema_rich["properties"]["concepts"]["items"]["properties"]["cast_sketch"]["items"]
    assert "species" in member["properties"] and "species" in member["required"]
    assert member["additionalProperties"] is False
    plain_member = schema_plain["properties"]["concepts"]["items"]["properties"]["cast_sketch"]["items"]
    assert "species" not in plain_member["properties"]


@pytest.mark.parametrize("universe", templates.load_universes(), ids=lambda u: u["id"])
def test_the_french_worst_case_input_budget_fits_with_the_species_block(universe):
    """C1v2's own worst case (``test_c1v2_budget_with_a_400_word_brief``: a 400-word French brief, the widest
    style line, 24 avoided titles) plus each universe's block -- the dearest card of the batch -- stays
    inside the unchanged ``INPUT_BUDGET["C1v2"]`` row."""
    import test_story_episode_prompt_budgets as budgets

    brief = budgets._filler(400, round(400 * 5.8))
    avoid = [budgets._filler(8, round(8 * 5.8)) for _ in range(24)]
    batch = universes.assign_species(STORY_ID, 3, universe["species"])
    blocks = [universes.species_block(universe, batch_species=batch, position=n) for n in range(10)]
    worst = 0
    for block in blocks:
        pack = context.build_pack(language="fr", template=FRUIT_DRAMA, brief_text=brief, avoid_titles=avoid,
                                  universe=block)
        assert pack.trimmed == []
        system, user, _schema = prompts.build_c1_v2(pack, style_ids=STYLE_IDS, batch=10, of=10,
                                                    angle=prompts.C1_ANGLES[-1])
        worst = max(worst, estimate_tokens(system, user))
    assert worst <= prompts.INPUT_BUDGET["C1v2"], (universe["id"], worst)
    assert prompts.INPUT_BUDGET["C1v2"] == 1690  # the row is the one DEC-274 measured: no re-pin


# ================================================================ the validator

def test_c1v2_errors_without_a_universe_are_what_they_were_and_ignore_brands():
    card = _card(member_species=False)
    card["title"] = "Coca Fight"
    reply = {"concepts": [card]}
    assert prompts.c1v2_errors(reply, style_ids=STYLE_IDS, brief=BRIEF) == []
    # An unknown key is still refused: the species key belongs to the universe reply schema only.
    assert prompts.c1v2_errors({"concepts": [_card()]}, style_ids=STYLE_IDS, brief=BRIEF)


def test_c1v2_errors_with_a_universe_check_species_and_brands_and_forgive_a_missing_species():
    ok = {"concepts": [_card()]}
    assert prompts.c1v2_errors(ok, style_ids=STYLE_IDS, brief=BRIEF, universe=True) == []
    no_species = {"concepts": [_card(member_species=False)]}
    assert prompts.c1v2_errors(no_species, style_ids=STYLE_IDS, brief=BRIEF, universe=True) == []
    branded = {"concepts": [_card(title="La Guerre du Coca")]}
    errors = prompts.c1v2_errors(branded, style_ids=STYLE_IDS, brief=BRIEF, universe=True)
    assert len(errors) == 1 and "names the brand 'coca'" in errors[0]
    branded_species = copy.deepcopy(ok)
    branded_species["concepts"][0]["cast_sketch"][0]["species"] = "Pepsi can"
    assert any("pepsi" in e for e in prompts.c1v2_errors(branded_species, style_ids=STYLE_IDS, brief=BRIEF,
                                                         universe=True))
    long_species = copy.deepcopy(ok)
    long_species["concepts"][0]["cast_sketch"][0]["species"] = "a very very long species name here"
    assert any("species" in e for e in prompts.c1v2_errors(long_species, style_ids=STYLE_IDS, brief=BRIEF,
                                                           universe=True))


def test_the_stored_card_accepts_the_universe_and_the_species_and_nothing_else():
    now = NOW
    card = dict(_card(), concept_id="gen_01", source="generated", prompt_version=prompts.PROMPT_VERSION,
                created_at=now, language="fr", universe={"id": "fruits", "lead_species": "kiwi"})
    doc = {"$schema": schemas.STORY_CONCEPTS_SCHEMA_NAME, "concepts": [card], "updated_at": now}
    assert schemas.story_concepts_errors(doc) == []
    bad = copy.deepcopy(doc)
    bad["concepts"][0]["universe"] = {"id": "fruits"}
    assert schemas.story_concepts_errors(bad)
    bad = copy.deepcopy(doc)
    bad["concepts"][0]["universe"]["extra"] = 1
    assert schemas.story_concepts_errors(bad)
    plain = copy.deepcopy(doc)
    del plain["concepts"][0]["universe"]
    for member in plain["concepts"][0]["cast_sketch"]:
        del member["species"]
    assert schemas.story_concepts_errors(plain) == []  # a card from before this stage still validates


def test_the_brand_gate_reads_the_stories_own_universe_only(tmp_path):
    store = StoryStore(tmp_path, on_log=lambda line: None)
    with_universe = store.get(_story(store, universe="drinks_sodas", style="viral_3d"))
    without = store.get(_story(store, universe=None))
    reply = {"descriptor": "a tall can wearing a Starbucks apron"}
    errors = universes.brand_gate(with_universe, reply)
    assert errors and "starbucks" in errors[0]
    assert universes.brand_gate(without, reply) == []                     # as before this stage
    assert universes.brand_gate(with_universe, {"descriptor": "a tall can"}) == []


def test_every_text_validator_asks_the_brand_gate():
    """C1v2 (prompts.c1v2_errors), K1, D2 (cast.py), P1, R1, R1v2, D3 (places.py): one told-why retry each."""
    cast = (ROOT / "clipping" / "aistory" / "steps" / "cast.py").read_text(encoding="utf-8")
    places = (ROOT / "clipping" / "aistory" / "steps" / "places.py").read_text(encoding="utf-8")
    assert cast.count("universes.brand_gate(story, reply)") == 2
    assert places.count("universes.brand_gate(story, reply)") == 4
    for call in ("schemas.k1_errors(reply, character[\"name\"]) or universes.brand_gate",
                 "schemas.d2_errors(reply, names, species_world=world is not None) or universes.brand_gate"):
        assert call in cast
    for call in ("schemas.p1_errors(reply) or universes.brand_gate", "schemas.r1_errors(reply) or universes.brand_gate",
                 "schemas.d3_errors(reply, variants, prop_names, names) or universes.brand_gate",
                 "schemas.r1v2_errors(reply, names) or universes.brand_gate"):
        assert call in places


# ================================================================ the step

def test_a_universe_story_sends_the_block_and_records_the_universe_on_the_card(tmp_path):
    store = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = _story(store, universe="fruits")
    ctx, _log = tss._ctx(store, story_id, params={"count": 1}, settings_env=SETTINGS)
    runner = tss.FakeRunner({"concepts": [_card("strawberry")]}, {"kept": True, "missing": []}, link=LINK)
    concepts_step.run(ctx, runner=runner)

    pool = templates.universe("fruits")["species"]
    lead = universes.assign_species(story_id, 0, pool)[0]
    user = runner.calls[0]["user"]
    assert "Universe: Fruits -- every character is an anthropomorphic fruit." in user
    assert f"This card's lead species: {lead}." in user
    assert "Species pool: strawberry, banana," in user
    # One block, between the style line and the (empty here) avoid list; the brief still comes first.
    assert user.index("The user's brief -- binding") < user.index("Visual style:") < user.index("Universe:")
    card = store.read_doc(story_id, "concepts.json")["concepts"][0]
    assert card["universe"] == {"id": "fruits", "lead_species": lead}
    assert [m["species"] for m in card["cast_sketch"]][0] == "strawberry"
    assert schemas.story_concepts_errors(store.read_doc(story_id, "concepts.json")) == []


def test_ten_calls_hand_out_ten_species_and_the_next_batch_starts_a_new_rotation(tmp_path):
    store = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = _story(store, universe="fruits")
    ctx, _log = tss._ctx(store, story_id, settings_env=SETTINGS)
    replies = []
    for call in range(1, 11):
        replies += [{"concepts": [_card("kiwi", title=f"Titre {call}")]}, {"kept": True, "missing": []}]
    runner = tss.FakeRunner(*replies, link=LINK)
    concepts_step.run(ctx, runner=runner)
    cards = store.read_doc(story_id, "concepts.json")["concepts"]
    leads = [c["universe"]["lead_species"] for c in cards]
    pool = templates.universe("fruits")["species"]
    assert leads == universes.assign_species(story_id, 0, pool) and len(set(leads)) == 10
    c1v2_users = [c["user"] for c in runner.calls if "Universe:" in c["user"]]
    assert len(c1v2_users) == 10
    for n, user in enumerate(c1v2_users):
        assert f"This card's lead species: {leads[n]}." in user and f"(call {n + 1} of 10)" in user

    ctx2, _ = tss._ctx(store, story_id, params={"count": 1}, settings_env=SETTINGS)
    runner2 = tss.FakeRunner({"concepts": [_card("kiwi", title="Titre 11")]}, {"kept": True, "missing": []},
                             link=LINK)
    concepts_step.run(ctx2, runner=runner2)
    eleventh = store.read_doc(story_id, "concepts.json")["concepts"][-1]
    assert eleventh["universe"]["lead_species"] == universes.assign_species(story_id, 1, pool)[0]


def test_a_story_without_a_universe_is_written_exactly_as_before_even_on_fruit_drama(tmp_path):
    store = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = _story(store, universe=None)                       # fruit_drama's default universe is not chosen
    assert media_policy.universe(store.get(story_id)) == "fruits"  # the form would pre-select it ...
    ctx, _log = tss._ctx(store, story_id, params={"count": 1}, settings_env=SETTINGS)
    runner = tss.FakeRunner({"concepts": [brief_tests._card()]}, {"kept": True, "missing": []}, link=LINK)
    concepts_step.run(ctx, runner=runner)
    assert "Universe:" not in runner.calls[0]["user"] and "Species pool" not in runner.calls[0]["user"]
    card = store.read_doc(story_id, "concepts.json")["concepts"][0]
    assert "universe" not in card and all("species" not in m for m in card["cast_sketch"])


def test_a_universe_story_without_the_v3_gate_keeps_the_free_c1(tmp_path):
    store = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = store.create(language="fr", seed_text=None, style_template_id="fruit_drama",
                            generation_profile={"universe": "fruits"}, now=NOW)["story_id"]
    ctx, _log = tss._ctx(store, story_id, params={"count": 1})
    runner = tss.FakeRunner(tss.c1_reply(1))
    concepts_step.run(ctx, runner=runner)
    assert "Invent exactly 1 original concept" in runner.calls[0]["user"]
    assert "Universe:" not in runner.calls[0]["user"]
    assert "universe" not in store.read_doc(story_id, "concepts.json")["concepts"][0]


def test_a_brand_in_a_card_is_one_told_why_retry_then_the_clean_card_is_kept(tmp_path):
    store = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = _story(store, universe="drinks_sodas", style="viral_3d")
    ctx, _log = tss._ctx(store, story_id, params={"count": 1}, settings_env=SETTINGS)
    runner = tss.FakeRunner(
        {"concepts": [_card("cola can", title="La Guerre du Coca")]},     # refused: a brand
        {"concepts": [_card("cola can", title="La Guerre du Cola")]},     # the retry, told why
        {"kept": True, "missing": []},                                    # C1J
        link=LINK)
    concepts_step.run(ctx, runner=runner)
    assert len(runner.calls) == 3
    retry = runner.calls[1]["user"]
    assert retry.startswith(runner.calls[0]["user"])
    assert "names the brand 'coca'" in retry and "write the generic thing instead" in retry
    card = store.read_doc(story_id, "concepts.json")["concepts"][0]
    assert card["title"] == "La Guerre du Cola" and card["universe"]["id"] == "drinks_sodas"
