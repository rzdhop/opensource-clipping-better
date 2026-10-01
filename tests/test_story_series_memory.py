"""Series memory as a fold, and the phase-5 documents (AI Story phase 5, stage 1).

``clipping/aistory/series_memory.py`` keeps ``season.json``'s memory as one
S3 entry per episode (``series_memory.entries["epNN"]``) and derives the
spec-2.6 fields the prompt builders read (``recaps``, ``open_hooks``,
``relationship_state``) with a pure fold. What is pinned here:

- the fold applies the entries in episode order whatever their dict order,
  is idempotent, and replacing one entry then re-folding equals folding from
  scratch; ``merge_entry`` never modifies its input;
- a hook is closed only by its exact text: a near miss is refused, never
  matched;
- ``entry_errors`` refuses an entry over its caps (recap 40 words, hook 120
  characters, 3 hooks opened), an unsorted or malformed pair key, a pair
  naming a character outside the story, a hook closed that is not open;
- the stale check compares the entry's ``script_rev`` with the script's
  ``rev``;
- ``audience_feedback`` items and an arc entry's ``history`` are typed,
  optional and backward compatible: the spec-2.6 feedback item and the live
  seasons' exact shape still validate;
- ``episodes/epNN/proposals.json`` (``next_proposals_v1``) validates, is
  refused for each rule broken once, and goes through the store.

Stdlib + pytest only (DEC-012): this file runs in the CI environment. Every
file lives under ``tmp_path``. The new module is imported by a fixture, so
against the parent commit each test fails on its own.
"""

from __future__ import annotations

import copy
import itertools
import json

import pytest

from clipping.aistory import schemas, store

NOW = "2026-09-29T10:00:00+00:00"
LATER = "2026-09-29T11:00:00+00:00"

KIWI, MANGO, FIG = "char_kiwilo", "char_mangella", "char_figuette"
PAIR_KM = "char_kiwilo|char_mangella"
PAIR_FK = "char_figuette|char_kiwilo"
CAST = (KIWI, MANGO, FIG)

PHONE = "who stole the coconut phone"
VOTE = "who leaves at the vote"
RING = "why the phone rang at night"


@pytest.fixture
def sm():
    from clipping.aistory import series_memory

    return series_memory


# ------------------------------------------------------------------ builders

def _words(n):
    return " ".join(["mot"] * n)


def _entry(**changes):
    doc = {
        "recap": "Kiwilo hid the coconut phone from Mangella.",
        "hooks_opened": [PHONE],
        "hooks_closed": [],
        "relationship_deltas": {PAIR_KM: "rivals"},
        "script_rev": 1,
        "at": NOW,
        "approved_at": None,
    }
    doc.update(changes)
    return doc


EP1 = _entry()
EP2 = _entry(recap="Mangella found the phone; the vote looms.", hooks_opened=[VOTE, RING], hooks_closed=[PHONE],
             relationship_deltas={PAIR_KM: "secret allies", PAIR_FK: "confidants"}, script_rev=3,
             approved_at=LATER)
EP3 = _entry(recap="The night call was Figuette.", hooks_opened=[], hooks_closed=[RING],
             relationship_deltas={PAIR_KM: "enemies again"}, script_rev=2)

ENTRIES = {"ep01": EP1, "ep02": EP2, "ep03": EP3}


# Phase 7 stage 5d (DEC-229, A13/A14): a memory entry's optional ``ledger``,
# the state L1 writes per character present that episode -- the knowledge
# base's own ``ledger_seed`` shape (stage 5b).
def _ledger_state(**changes):
    state = {"location": None, "wardrobe_set": None, "possessions": [], "injuries": None,
             "relationship_notes": None}
    state.update(changes)
    return state

FOLDED = {
    "recaps": {"ep01": EP1["recap"], "ep02": EP2["recap"], "ep03": EP3["recap"]},
    # ep01 opens PHONE; ep02 closes it and opens VOTE, RING; ep03 closes RING.
    "open_hooks": [VOTE],
    # The latest delta per pair, in episode order; keys sorted.
    "relationship_state": {PAIR_FK: "confidants", PAIR_KM: "enemies again"},
}


def _arc_entry(ep):
    functions = ["setup", "escalation", "complication", "midpoint_twist", "crisis", "crisis", "crisis",
                 "climax_and_reset"]
    return {"ep": ep, "function": functions[ep - 1], "summary": f"Episode {ep}: the vote looms.",
            "open_hooks_in": [] if ep == 1 else [PHONE], "open_hooks_out": [PHONE], "characters": [KIWI, MANGO]}


def _live_season():
    """The exact shape of the four live seasons on 2026-09-29: an approved
    8-entry arc, the memory and the feedback empty."""
    return {
        "$schema": "season_arc_v1",
        "episodes_planned": 8,
        "arc": [_arc_entry(ep) for ep in range(1, 9)],
        "series_memory": {"recaps": {}, "open_hooks": [], "relationship_state": {}, "introduced": {}},
        "audience_feedback": [],
        "approved_at": NOW,
        "updated_at": NOW,
    }


def _season_with(entries, **memory):
    """A live-shape season whose memory holds *entries* and the derived
    fields folded by hand (FOLDED for ENTRIES)."""
    doc = _live_season()
    doc["series_memory"] = {**copy.deepcopy(FOLDED), "introduced": {"ep01": [KIWI, MANGO]},
                            "entries": copy.deepcopy(entries)}
    doc["series_memory"].update(copy.deepcopy(memory))
    return doc


def _feedback(**changes):
    # The spec-2.6 item: ep, pasted_at, text, digest.
    item = {"ep": 1, "pasted_at": NOW, "text": "top comments: team Mangella!", "digest": "They love Mangella."}
    item.update(changes)
    return item


def _history(**changes):
    item = {"summary": "Episode 3: the vote looms.", "open_hooks_out": [PHONE], "replaced_at": LATER,
            "source": "proposal"}
    item.update(changes)
    return item


def _proposals(**changes):
    doc = {
        "$schema": "next_proposals_v1",
        "for_ep": 2,
        "based_on": {"memory_ep": 1, "script_rev": 3},
        "characters": [
            {"item_id": "c1", "name": "Papayou", "role": "recurring", "one_line": "A papaya who sells secrets.",
             "archetype": "gossip", "why": "Someone must have seen who took the phone."},
            {"item_id": "c2", "name": "Litchia", "role": "guest", "one_line": "A lychee on holiday.",
             "why": "A stranger raises the stakes of the vote."},
        ],
        "twists": [
            {"item_id": "t1", "target_ep": 3, "summary": "Papayou sold the phone to Figuette.",
             "open_hooks_out": ["who paid Papayou"], "why": "It pays off the phone hook."},
            {"item_id": "t2", "target_ep": 5, "summary": "The vote is rigged.", "open_hooks_out": [],
             "why": "A midpoint reversal."},
        ],
        "decisions": {"c1": "accepted", "t2": "rejected"},
        "created_at": NOW,
        "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def _at(doc, path):
    *parents, last = path.split(".")
    for key in parents:
        doc = doc[int(key)] if isinstance(doc, list) else doc[key]
    return doc, (int(last) if isinstance(doc, list) else last)


def _set(path, value):
    def change(doc):
        parent, key = _at(doc, path)
        parent[key] = copy.deepcopy(value)
    return change


def _drop(path):
    def change(doc):
        parent, key = _at(doc, path)
        del parent[key]
    return change


def _changed(doc, change):
    doc = copy.deepcopy(doc)
    change(doc)
    return doc


# ---------------------------------------------------------------- the fold

def test_the_fold_golden(sm):
    assert sm.fold_memory(ENTRIES) == FOLDED
    assert list(sm.fold_memory(ENTRIES)["relationship_state"]) == [PAIR_FK, PAIR_KM]
    assert sm.DERIVED_FIELDS == ("recaps", "open_hooks", "relationship_state")


@pytest.mark.parametrize("entries", [None, {}])
def test_the_fold_of_no_entries_is_empty(sm, entries):
    assert sm.fold_memory(entries) == {"recaps": {}, "open_hooks": [], "relationship_state": {}}


def test_the_fold_applies_the_entries_in_episode_order_whatever_their_dict_order(sm):
    expected = json.dumps(sm.fold_memory(ENTRIES))
    for order in itertools.permutations(ENTRIES):
        shuffled = {key: ENTRIES[key] for key in order}
        assert json.dumps(sm.fold_memory(shuffled)) == expected, order


def test_the_fold_is_idempotent_and_never_modifies_its_input(sm):
    before = copy.deepcopy(ENTRIES)
    first = sm.fold_memory(ENTRIES)
    second = sm.fold_memory(ENTRIES)
    assert first == second == FOLDED
    assert ENTRIES == before
    first["open_hooks"].append("x")
    first["recaps"]["ep01"] = "x"
    assert sm.fold_memory(ENTRIES) == FOLDED


def test_a_hook_opened_again_stays_open_once_and_closes_once(sm):
    entries = {"ep01": _entry(hooks_opened=[PHONE]), "ep02": _entry(hooks_opened=[PHONE, VOTE]),
               "ep03": _entry(hooks_opened=[], hooks_closed=[PHONE])}
    assert sm.fold_memory(entries)["open_hooks"] == [VOTE]


def test_a_hook_closed_before_it_is_opened_is_refused(sm):
    entries = {"ep01": _entry(hooks_opened=[], hooks_closed=[VOTE]), "ep02": _entry(hooks_opened=[VOTE])}
    problems = sm.fold_errors(entries)
    assert len(problems) == 1 and "ep01" in problems[0] and VOTE in problems[0]
    with pytest.raises(ValueError):
        sm.fold_memory(entries)


NEAR_MISSES = [PHONE + "?", PHONE.capitalize(), " " + PHONE, PHONE + " ", "who stole the coconut-phone",
               "who stole the coco phone"]


@pytest.mark.parametrize("near_miss", NEAR_MISSES)
def test_a_hook_is_closed_only_by_its_exact_text(sm, near_miss):
    entries = {"ep01": EP1, "ep02": _entry(hooks_opened=[], hooks_closed=[near_miss])}

    problems = sm.fold_errors(entries)
    assert problems and repr(near_miss) in problems[0]
    with pytest.raises(ValueError):
        sm.fold_memory(entries)
    assert sm.entry_errors(entries["ep02"], open_hooks=[PHONE], char_ids=CAST) != []
    # The exact text closes it.
    exact = {"ep01": EP1, "ep02": _entry(hooks_opened=[], hooks_closed=[PHONE])}
    assert sm.fold_errors(exact) == []
    assert sm.fold_memory(exact)["open_hooks"] == []


# ------------------------------------------------------------------ merge

def test_merge_entry_writes_the_entry_and_the_folded_fields(sm):
    season = _live_season()
    for ep, entry in ((1, EP1), (2, EP2), (3, EP3)):
        season = sm.merge_entry(season, ep, entry)

    memory = season["series_memory"]
    assert memory["entries"] == ENTRIES
    assert list(memory["entries"]) == ["ep01", "ep02", "ep03"]
    assert {field: memory[field] for field in sm.DERIVED_FIELDS} == FOLDED
    assert memory["introduced"] == {}
    assert schemas.season_arc_errors(season) == []


def test_replacing_an_entry_and_refolding_equals_folding_from_scratch(sm):
    season = _season_with(ENTRIES)
    edited = _entry(recap="Kiwilo lost the coconut phone.", hooks_opened=[PHONE, "where Kiwilo slept"],
                    relationship_deltas={PAIR_KM: "exes"}, script_rev=2)

    merged = sm.merge_entry(season, 1, edited)

    scratch = dict(ENTRIES, ep01=edited)
    memory = merged["series_memory"]
    assert memory["entries"] == scratch
    assert {field: memory[field] for field in sm.DERIVED_FIELDS} == sm.fold_memory(scratch)
    assert memory["open_hooks"] == ["where Kiwilo slept", VOTE]
    # Idempotent: the same entry merged again changes nothing.
    assert sm.merge_entry(merged, 1, edited) == merged
    assert schemas.season_arc_errors(merged) == []


def test_merge_entry_in_any_order_gives_the_same_season(sm):
    # Entries that close nothing, so any merge order is a valid one.
    merges = ((1, EP1), (2, _entry(recap="The vote looms.", hooks_opened=[VOTE], relationship_deltas={})),
              (3, _entry(recap="The phone rang.", hooks_opened=[RING], relationship_deltas={PAIR_FK: "allies"})))
    seasons = []
    for order in itertools.permutations(merges):
        season = _live_season()
        for ep, entry in order:
            season = sm.merge_entry(season, ep, entry)
        seasons.append(json.dumps(season))
    assert len(set(seasons)) == 1


def test_merge_entry_never_modifies_its_input(sm):
    season = _season_with({"ep01": EP1})
    season["series_memory"].update(recaps={"ep01": EP1["recap"]}, open_hooks=[PHONE],
                                   relationship_state={PAIR_KM: "rivals"})
    entry = copy.deepcopy(EP2)
    before_season, before_entry = copy.deepcopy(season), copy.deepcopy(entry)

    merged = sm.merge_entry(season, 2, entry)

    assert season == before_season and entry == before_entry
    merged["series_memory"]["entries"]["ep02"]["hooks_opened"].append("x")
    merged["arc"][0]["summary"] = "x"
    assert entry == before_entry and season == before_season


def test_merge_entry_keeps_everything_the_fold_does_not_own(sm):
    season = _live_season()
    season["series_memory"]["introduced"] = {"ep02": [FIG]}
    season["audience_feedback"] = [_feedback()]

    merged = sm.merge_entry(season, 1, EP1)

    assert merged["series_memory"]["introduced"] == {"ep02": [FIG]}
    for key in ("$schema", "episodes_planned", "arc", "audience_feedback", "approved_at", "updated_at"):
        assert merged[key] == season[key], key


def test_merge_entry_refuses_what_the_fold_refuses(sm):
    season = _season_with(ENTRIES)
    # ep02 closes PHONE; an ep01 that no longer opens it leaves ep02 closing nothing.
    with pytest.raises(ValueError):
        sm.merge_entry(season, 1, _entry(hooks_opened=[VOTE]))
    with pytest.raises(ValueError):
        sm.merge_entry(season, 1, _entry(recap=""))


@pytest.mark.parametrize("ep", [0, 100, True, "1", 1.0, None])
def test_merge_entry_refuses_an_episode_that_is_not_one_to_ninety_nine(sm, ep):
    with pytest.raises(ValueError):
        sm.merge_entry(_live_season(), ep, EP1)


def test_memory_key_and_open_hooks_before(sm):
    assert sm.memory_key(1) == "ep01" and sm.memory_key(12) == "ep12"
    season = _season_with(ENTRIES)
    assert sm.open_hooks_before(season, 1) == []
    assert sm.open_hooks_before(season, 2) == [PHONE]
    assert sm.open_hooks_before(season, 3) == [VOTE, RING]
    assert sm.open_hooks_before(season, 4) == [VOTE]
    assert sm.open_hooks_before(_live_season(), 2) == []


# ---------------------------------------------------------------- fold_ledger

def test_ledger_folds_latest_state_per_character(sm):
    """Stage 5d, DEC-229: the fold starts from the knowledge base's seed,
    then applies each entry's own ``ledger`` in episode order, one character
    at a time -- a character an entry does not name keeps its latest state
    from before. Episode 1 changes only Kiwilo's wardrobe set; episode 2
    only Mangella's location: before episode 3 the fold has both changes;
    before episode 2 it has only episode 1's."""
    seed = {KIWI: _ledger_state(location="place_parloir", wardrobe_set="casual"),
            MANGO: _ledger_state(location="place_parloir", wardrobe_set="casual")}
    ep1 = _entry(ledger={KIWI: _ledger_state(location="place_parloir", wardrobe_set="formal")})
    ep2 = _entry(ledger={MANGO: _ledger_state(location="place_piscine", wardrobe_set="casual")})
    season = {"series_memory": {"entries": {"ep01": ep1, "ep02": ep2}}}
    knowledge = {"ledger_seed": seed}

    assert sm.fold_ledger(season, before_ep=1, knowledge=knowledge) == seed

    before2 = sm.fold_ledger(season, before_ep=2, knowledge=knowledge)
    assert before2[KIWI]["wardrobe_set"] == "formal"
    assert before2[MANGO] == seed[MANGO]  # episode 2 has not been folded in yet

    before3 = sm.fold_ledger(season, before_ep=3, knowledge=knowledge)
    assert before3[KIWI]["wardrobe_set"] == "formal"  # kept from episode 1
    assert before3[MANGO]["location"] == "place_piscine"  # episode 2's own change


def test_fold_ledger_is_never_stored_only_recomputed(sm):
    """Unlike ``recaps``/``open_hooks``/``relationship_state``, the ledger is
    not one of ``DERIVED_FIELDS``: nothing stores it on the season, so an
    entry with no ``ledger`` at all (a legacy story, or a v2 one before
    stage 5d) changes nothing, and a missing knowledge base folds to {}."""
    assert "ledger" not in sm.DERIVED_FIELDS
    season = {"series_memory": {"entries": {"ep01": _entry()}}}
    assert sm.fold_ledger(season, before_ep=2, knowledge=None) == {}
    assert sm.fold_ledger(None, before_ep=5, knowledge={"ledger_seed": {KIWI: _ledger_state()}}) == \
        {KIWI: _ledger_state()}


@pytest.mark.parametrize("ep", [0, -1, 100, "1", 1.0, True])
def test_fold_ledger_refuses_an_episode_that_is_not_one_to_ninety_nine(sm, ep):
    with pytest.raises(ValueError):
        sm.fold_ledger(None, before_ep=ep, knowledge=None)


# --------------------------------------------------------------- entry_errors

def test_a_good_entry_has_no_errors(sm):
    assert sm.entry_errors(EP1, open_hooks=[], char_ids=CAST) == []
    assert sm.entry_errors(EP2, open_hooks=[PHONE], char_ids=CAST) == []
    assert sm.entry_errors(EP3, open_hooks=[VOTE, RING], char_ids=list(CAST)) == []


def test_the_caps_are_inclusive(sm):
    entry = _entry(recap=_words(40), hooks_opened=["h" * 120, VOTE, RING], hooks_closed=["c" * 120])
    assert sm.entry_errors(entry, open_hooks=["c" * 120], char_ids=CAST) == []


def test_a_pair_naming_a_character_outside_the_story_is_refused(sm):
    errors = sm.entry_errors(EP2, open_hooks=[PHONE], char_ids=[KIWI, MANGO])
    assert len(errors) == 1 and FIG in errors[0]


def test_a_hook_closed_that_is_not_open_is_refused(sm):
    errors = sm.entry_errors(EP2, open_hooks=[VOTE], char_ids=CAST)
    assert len(errors) == 1 and PHONE in errors[0]


ENTRY_BREAKS = {
    "recap 41 words": _set("recap", _words(41)),
    "recap empty": _set("recap", ""),
    "recap blank": _set("recap", "   "),
    "hook opened 121 characters": _set("hooks_opened", ["h" * 121]),
    "hook closed 121 characters": _set("hooks_closed", ["c" * 121]),
    "hook blank": _set("hooks_opened", ["  "]),
    "four hooks opened": _set("hooks_opened", [PHONE, VOTE, RING, "a fourth hook"]),
    "a hook opened twice": _set("hooks_opened", [VOTE, VOTE]),
    "a hook opened and closed": _set("hooks_opened", [PHONE]),
    "pair key unsorted": _set("relationship_deltas", {"char_mangella|char_kiwilo": "rivals"}),
    "pair key one character twice": _set("relationship_deltas", {"char_kiwilo|char_kiwilo": "alone"}),
    "pair key not ids": _set("relationship_deltas", {"kiwilo|mangella": "rivals"}),
    "pair key one id": _set("relationship_deltas", {KIWI: "rivals"}),
    "pair key three ids": _set("relationship_deltas", {f"{FIG}|{KIWI}|{MANGO}": "a crowd"}),
    "pair text blank": _set("relationship_deltas", {PAIR_KM: " "}),
    "pair text not a string": _set("relationship_deltas", {PAIR_KM: 3}),
    "script_rev zero": _set("script_rev", 0),
    "script_rev a boolean": _set("script_rev", True),
    "script_rev text": _set("script_rev", "3"),
    "at empty": _set("at", ""),
    "approved_at empty": _set("approved_at", ""),
    "extra key": _set("cliffhanger", "x"),
    "missing approved_at": _drop("approved_at"),
    "missing hooks_closed": _drop("hooks_closed"),
    "hooks as text": _set("hooks_opened", PHONE),
}


@pytest.mark.parametrize("change", list(ENTRY_BREAKS.values()), ids=list(ENTRY_BREAKS))
def test_a_broken_entry_is_refused(sm, change):
    good = EP2  # closes PHONE, opens VOTE and RING
    assert sm.entry_errors(good, open_hooks=[PHONE], char_ids=CAST) == []
    assert sm.entry_errors(_changed(good, change), open_hooks=[PHONE], char_ids=CAST) != []


# ---------------------------------------------------------------- pair keys

def test_pair_key_sorts_its_two_ids(sm):
    assert sm.pair_key(MANGO, KIWI) == PAIR_KM
    assert sm.pair_key(KIWI, MANGO) == PAIR_KM
    assert sm.pair_parts(PAIR_KM) == (KIWI, MANGO)


@pytest.mark.parametrize("a, b", [(KIWI, KIWI), ("kiwilo", MANGO), (KIWI, "place_beach_camp"), (KIWI, None),
                                  (KIWI, f"{MANGO}|{FIG}")])
def test_pair_key_refuses_anything_but_two_distinct_character_ids(sm, a, b):
    with pytest.raises(ValueError):
        sm.pair_key(a, b)


@pytest.mark.parametrize("key", ["char_mangella|char_kiwilo", "char_kiwilo|char_kiwilo", "kiwilo|mangella",
                                 KIWI, f"{FIG}|{KIWI}|{MANGO}", "", None, 3, PAIR_KM + "\n"])
def test_pair_parts_is_none_for_anything_but_a_sorted_pair_key(sm, key):
    assert sm.pair_parts(key) is None


# ------------------------------------------------------------------ staleness

def test_an_entry_is_stale_when_the_script_moved_on(sm):
    entry = _entry(script_rev=3)
    assert sm.entry_is_stale(entry, {"rev": 3}) is False
    assert sm.entry_is_stale(entry, {"rev": 4}) is True
    assert sm.entry_is_stale(entry, {"rev": 2}) is True
    assert sm.entry_is_stale(entry, None) is True  # the script it was written from is gone


def test_is_stale_reads_the_episode_entry(sm):
    season = _season_with(ENTRIES)
    assert sm.is_stale(season, 2, {"rev": 3}) is False
    assert sm.is_stale(season, 2, {"rev": 4}) is True
    assert sm.is_stale(season, 1, {"rev": 1}) is False
    assert sm.entry_for(season, 2) == EP2
    assert sm.entry_for(season, 5) is None
    assert sm.entry_for(_live_season(), 1) is None
    with pytest.raises(KeyError):
        sm.is_stale(season, 5, {"rev": 1})
    with pytest.raises(KeyError):
        sm.is_stale(_live_season(), 1, {"rev": 1})


# ----------------------------------------------------------- season: memory

def test_the_live_season_shape_validates_and_reads_back_unchanged(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    stories = store.StoryStore(str(outputs), on_log=lambda _line: None)
    story_id = stories.create(language="fr", now=NOW)["story_id"]
    live = _live_season()
    assert schemas.season_arc_errors(live) == []

    stories.write_doc(story_id, "season.json", live, now=NOW)
    path = outputs / "stories" / story_id / "season.json"
    on_disk = path.read_bytes()
    read = stories.read_doc(story_id, "season.json")

    assert read == live
    assert "entries" not in read["series_memory"], "nothing adds entries on read or write"
    assert path.read_bytes() == on_disk, "a read never writes"


def test_a_season_with_folded_entries_validates_and_round_trips(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    stories = store.StoryStore(str(outputs), on_log=lambda _line: None)
    story_id = stories.create(language="fr", now=NOW)["story_id"]
    season = _season_with(ENTRIES)
    season["arc"][2]["history"] = [_history()]
    season["audience_feedback"] = [_feedback(), _feedback(ep=2, directions=["a", "b", "c"], chosen_direction=1)]
    assert schemas.season_arc_errors(season) == []

    stories.write_doc(story_id, "season.json", season, now=LATER)

    assert stories.read_doc(story_id, "season.json") == dict(season, updated_at=LATER)


def test_empty_entries_leave_the_derived_fields_free():
    # {} is "no entries yet": the spec-shape fields are as loose as before.
    season = _season_with({}, recaps={"ep01": "a hand-written recap"}, open_hooks=["a hand-written hook"],
                          relationship_state={"char_mangella|char_kiwilo": "unsorted, as before"})
    assert schemas.season_arc_errors(season) == []
    del season["series_memory"]["entries"]
    assert schemas.season_arc_errors(season) == []


@pytest.mark.parametrize("field, value", [
    ("recaps", {"ep01": EP1["recap"], "ep02": EP2["recap"]}),
    ("recaps", dict(FOLDED["recaps"], ep04="an extra recap")),
    ("open_hooks", []),
    ("open_hooks", [VOTE, RING]),
    ("relationship_state", {PAIR_KM: "enemies again"}),
    ("relationship_state", dict(FOLDED["relationship_state"], **{PAIR_KM: "secret allies"})),
])
def test_derived_fields_that_disagree_with_the_entries_are_refused(field, value):
    assert schemas.season_arc_errors(_season_with(ENTRIES)) == []
    errors = schemas.season_arc_errors(_season_with(ENTRIES, **{field: value}))
    assert errors and field in errors[0]


def test_entries_whose_fold_fails_are_refused():
    entries = dict(ENTRIES, ep03=_entry(hooks_opened=[], hooks_closed=[PHONE]))  # PHONE closed at ep02
    errors = schemas.season_arc_errors(_season_with(entries))
    assert errors and "ep03" in " ".join(errors)


@pytest.mark.parametrize("key", ["ep1", "ep00", "ep100", "ep001", "EP01", "episode01", "ep01\n", "1"])
def test_an_entry_key_that_is_not_an_episode_is_refused(key):
    errors = schemas.season_arc_errors(_season_with({key: EP1}, recaps={key: EP1["recap"]},
                                                    open_hooks=[PHONE], relationship_state={PAIR_KM: "rivals"}))
    assert errors and repr(key) in errors[0]


# ------------------------------------------------------ season: audience_feedback

def _season_feedback(*items):
    season = _live_season()
    season["audience_feedback"] = list(items)
    return season


@pytest.mark.parametrize("item", [
    _feedback(),                                            # spec 2.6
    _feedback(digest=_words(60)),
    {"ep": 3, "pasted_at": NOW, "text": "pasted, not digested yet"},
    _feedback(directions=["more Mangella", "a new rival", "the vote now"]),
    _feedback(directions=["a", "b", "c"], chosen_direction=0),
    _feedback(directions=["a", "b", "c"], chosen_direction=2),
    _feedback(directions=["a", "b", "c"], chosen_direction=None),
    _feedback(text="t" * 6000),
], ids=["spec-2.6", "digest-60-words", "no-digest", "directions", "chosen-0", "chosen-2", "chosen-none",
        "text-6000"])
def test_a_good_feedback_item_validates(item):
    assert schemas.season_arc_errors(_season_feedback(item)) == []
    assert schemas.season_arc_errors(_season_feedback(item, _feedback(ep=2))) == []


FEEDBACK_BREAKS = {
    "text 6001 characters": _set("text", "t" * 6001),
    "text empty": _set("text", ""),
    "text blank": _set("text", "   "),
    "digest 61 words": _set("digest", _words(61)),
    "digest blank": _set("digest", " "),
    "two directions": _set("directions", ["a", "b"]),
    "four directions": _set("directions", ["a", "b", "c", "d"]),
    "no directions": _set("directions", []),
    "a blank direction": _set("directions", ["a", " ", "c"]),
    "chosen without directions": _set("chosen_direction", 1),
    "chosen null without directions": _set("chosen_direction", None),
    "chosen out of range": lambda doc: doc.update(directions=["a", "b", "c"], chosen_direction=3),
    "chosen negative": lambda doc: doc.update(directions=["a", "b", "c"], chosen_direction=-1),
    "chosen a boolean": lambda doc: doc.update(directions=["a", "b", "c"], chosen_direction=True),
    "chosen as text": lambda doc: doc.update(directions=["a", "b", "c"], chosen_direction="1"),
    "chosen a float": lambda doc: doc.update(directions=["a", "b", "c"], chosen_direction=1.0),
    "episode zero": _set("ep", 0),
    "episode as text": _set("ep", "1"),
    "missing pasted_at": _drop("pasted_at"),
    "missing text": _drop("text"),
    "extra key": _set("stats", {}),
}


@pytest.mark.parametrize("change", list(FEEDBACK_BREAKS.values()), ids=list(FEEDBACK_BREAKS))
def test_a_broken_feedback_item_is_refused(change):
    assert schemas.season_arc_errors(_season_feedback(_feedback())) == []
    assert schemas.season_arc_errors(_season_feedback(_changed(_feedback(), change))) != []


# ------------------------------------------------------------ season: arc history

def test_an_arc_entry_with_history_validates():
    season = _live_season()
    season["arc"][4]["history"] = [_history(), _history(summary="Episode 5, twice rewritten.", open_hooks_out=[])]
    assert schemas.season_arc_errors(season) == []
    season["arc"][4]["history"] = []
    assert schemas.season_arc_errors(season) == []


HISTORY_BREAKS = {
    "missing replaced_at": _drop("replaced_at"),
    "missing source": _drop("source"),
    "missing summary": _drop("summary"),
    "missing open_hooks_out": _drop("open_hooks_out"),
    "unknown source": _set("source", "edit"),
    "summary 61 words": _set("summary", _words(61)),
    "summary blank": _set("summary", "  "),
    "hook 121 characters": _set("open_hooks_out", ["h" * 121]),
    "replaced_at empty": _set("replaced_at", ""),
    "extra key": _set("item_id", "t1"),
}


@pytest.mark.parametrize("change", list(HISTORY_BREAKS.values()), ids=list(HISTORY_BREAKS))
def test_a_broken_history_item_is_refused(change):
    season = _live_season()
    season["arc"][2]["history"] = [_history()]
    assert schemas.season_arc_errors(season) == []
    season["arc"][2]["history"] = [_changed(_history(), change)]
    assert schemas.season_arc_errors(season) != []


def test_history_that_is_not_a_list_is_refused():
    season = _live_season()
    season["arc"][2]["history"] = [_history()]
    assert schemas.season_arc_errors(season) == []
    season["arc"][2]["history"] = _history()
    assert schemas.season_arc_errors(season) != []


# ------------------------------------------------------------- next_proposals_v1

def test_a_good_proposals_document_validates():
    assert schemas.NEXT_PROPOSALS_SCHEMA_NAME == "next_proposals_v1"
    assert schemas.next_proposals_errors(_proposals()) == []
    assert schemas.next_proposals_errors(_proposals(characters=[], twists=[], decisions={})) == []
    for role in ("lead", "support", "recurring", "guest"):
        doc = _proposals()
        doc["characters"][0]["role"] = role
        assert schemas.next_proposals_errors(doc) == [], role


def _character(item_id, **changes):
    return {"item_id": item_id, "name": f"Name {item_id}", "role": "guest", "one_line": "x", "why": "y", **changes}


def _twist(item_id, **changes):
    return {"item_id": item_id, "target_ep": 3, "summary": "x", "open_hooks_out": [], "why": "y", **changes}


PROPOSALS_BREAKS = {
    "wrong $schema": _set("$schema", "next_proposals_v2"),
    "extra key": _set("surprise", 1),
    "missing created_at": _drop("created_at"),
    "missing updated_at": _drop("updated_at"),
    "missing decisions": _drop("decisions"),
    "for_ep not memory_ep + 1": _set("for_ep", 3),
    "for_ep the memory episode": _set("for_ep", 1),
    "for_ep zero": _set("for_ep", 0),
    "memory_ep as text": _set("based_on.memory_ep", "1"),
    "script_rev zero": _set("based_on.script_rev", 0),
    "based_on extra key": _set("based_on.ep", 1),
    "three characters": _set("characters", [_character("c1"), _character("c2"), _character("c3")]),
    "three twists": _set("twists", [_twist("t1"), _twist("t2"), _twist("t3")]),
    "an unknown role": _set("characters.0.role", "villain"),
    "character name too long": _set("characters.0.name", "n" * 61),
    "character name blank": _set("characters.0.name", "  "),
    "character one_line blank": _set("characters.0.one_line", ""),
    "character archetype empty": _set("characters.0.archetype", ""),
    "character why blank": _set("characters.1.why", " "),
    "character extra key": _set("characters.0.char_id", KIWI),
    "character missing why": _drop("characters.0.why"),
    "item id not a slug": _set("characters.0.item_id", "Char 1"),
    "item id repeated across kinds": _set("twists.0.item_id", "c1"),
    "item id repeated within a kind": _set("characters.1.item_id", "c1"),
    "twist on the memory episode": _set("twists.0.target_ep", 1),
    "twist before the memory episode": _set("twists.0.target_ep", 0),
    "twist summary 61 words": _set("twists.0.summary", _words(61)),
    "twist summary blank": _set("twists.0.summary", " "),
    "twist hook 121 characters": _set("twists.0.open_hooks_out", ["h" * 121]),
    "twist four hooks": _set("twists.0.open_hooks_out", ["a", "b", "c", "d"]),
    "twist hook blank": _set("twists.0.open_hooks_out", [" "]),
    "twist missing target_ep": _drop("twists.0.target_ep"),
    "decision for an unknown item": _set("decisions", {"c9": "accepted"}),
    "decision neither accepted nor rejected": _set("decisions", {"c1": "maybe"}),
    "decision as a boolean": _set("decisions", {"c1": True}),
    "decisions as a list": _set("decisions", ["c1"]),
}


@pytest.mark.parametrize("change", list(PROPOSALS_BREAKS.values()), ids=list(PROPOSALS_BREAKS))
def test_a_broken_proposals_document_is_refused(change):
    assert schemas.next_proposals_errors(_proposals()) == []
    assert schemas.next_proposals_errors(_changed(_proposals(), change)) != []


def test_the_proposals_document_goes_through_the_store(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    stories = store.StoryStore(str(outputs), on_log=lambda _line: None)
    story_id = stories.create(language="fr", now=NOW)["story_id"]
    story_before = stories.get(story_id)
    assert store.EPISODE_PROPOSALS_DOC == "proposals.json"
    assert store.EPISODE_DOC_VALIDATORS[store.EPISODE_PROPOSALS_DOC] is schemas.next_proposals_errors
    assert store.EPISODE_PROPOSALS_DOC in store.EPISODE_DOCS_WITH_TIMESTAMPS

    saved = stories.write_episode_doc(story_id, 2, "proposals.json", _proposals(), now=LATER)

    assert saved == _proposals(updated_at=LATER)
    assert stories.read_episode_doc(story_id, 2, "proposals.json") == saved
    path = outputs / "stories" / story_id / "episodes" / "ep02" / "proposals.json"
    assert json.loads(path.read_text(encoding="utf-8")) == saved
    assert stories.get(story_id) == story_before, "an episode document never touches story.json"
    # It sits in the folder of the episode it proposes for.
    with pytest.raises(schemas.SchemaError):
        stories.write_episode_doc(story_id, 3, "proposals.json", _proposals(), now=LATER)
    assert not (outputs / "stories" / story_id / "episodes" / "ep03").exists()
    with pytest.raises(schemas.SchemaError):
        stories.write_episode_doc(story_id, 2, "proposals.json", _proposals(for_ep=3), now=LATER)
    # A proposals document moved to another episode's folder does not read.
    other = outputs / "stories" / story_id / "episodes" / "ep04"
    other.mkdir()
    (other / "proposals.json").write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(schemas.SchemaError):
        stories.read_episode_doc(story_id, 4, "proposals.json")


# ------------------------------------------- stage 3: the chosen audience direction

DIRECTIONS = ["more Mangella", "a new rival", "the vote now"]


def test_chosen_direction_is_the_text_the_writer_chose_on_that_episodes_feedback(sm):
    """Plan 11 stage 3: E1 of episode N+1 (and N1) is steered by the
    direction chosen when episode N's feedback was approved --
    ``directions[chosen_direction]``; nothing when it was approved with no
    direction (null) or not decided yet (absent)."""
    chosen = _season_feedback(_feedback(directions=DIRECTIONS, chosen_direction=1))
    assert schemas.season_arc_errors(chosen) == []
    assert sm.chosen_direction(chosen, 1) == "a new rival"
    assert sm.chosen_direction(chosen, 2) is None  # another episode's feedback
    assert sm.chosen_direction(_season_feedback(_feedback(directions=DIRECTIONS, chosen_direction=None)), 1) is None
    assert sm.chosen_direction(_season_feedback(_feedback(directions=DIRECTIONS)), 1) is None
    assert sm.chosen_direction(_season_feedback(_feedback()), 1) is None
    assert sm.chosen_direction(_live_season(), 1) is None
    assert sm.chosen_direction(None, 1) is None


def test_chosen_direction_the_latest_decided_feedback_of_the_episode_wins(sm):
    first = _feedback(directions=DIRECTIONS, chosen_direction=0)
    pending = _feedback(pasted_at=LATER, text="more comments", directions=["x", "y", "z"])
    assert sm.chosen_direction(_season_feedback(first, pending), 1) == "more Mangella"  # pending decides nothing
    cleared = _feedback(pasted_at=LATER, directions=["x", "y", "z"], chosen_direction=None)
    assert sm.chosen_direction(_season_feedback(first, cleared), 1) is None  # decided: no direction
    second = _feedback(pasted_at=LATER, directions=["x", "y", "z"], chosen_direction=2)
    assert sm.chosen_direction(_season_feedback(first, second, _feedback(ep=2)), 1) == "z"
    with pytest.raises(ValueError):
        sm.chosen_direction(_live_season(), 0)
