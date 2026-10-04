"""Plan 20 stage 2: plot archetypes steer a v2 story's season arc.

A v2 story's S1 (prompt id ``S1v2``) is shown the archetype library
(``templates/archetypes``: id, premise, pairs) and picks the season's primary
and at most one secondary that pairs with it; each entry names the one whose
beat it plays. ``season.json`` keeps the choice (``archetypes``) and each
entry's ``archetype``, checked against the library. S2 is told its entry's
beat; the knowledge step's D5 and the episodes' series-memory block name the
archetype on one line, read-only. A legacy (v1) story's S1 and S2 asks are
byte-identical to before: ``tests/test_story_prompts.py::test_build_s1_golden_fr``
pins the S1 text, unedited.

Reuses ``tests/test_story_cast_steps.py``'s cast-approved story and fakes (the
suite's cross-module import pattern). Offline and hermetic, stdlib + pytest.
"""

from __future__ import annotations

import copy
import json

import pytest

from clipping.aistory import context, defaults, prompts, schemas, templates
from clipping.aistory.steps import season as season_step
from clipping.providers.pacing import estimate_tokens

import test_story_cast_steps as tcs
import test_story_episode_prompt_budgets as budgets
import test_story_prompts as tsp
from test_story_cast_steps import hermetic, store  # noqa: F401 -- fixtures

NOW = tcs.NOW

S1V2_REPLY = {
    "archetypes": {"primary": "infidelity", "secondary": "secret_child"},
    "arc": [
        {"ep": 1, "function": "setup", "archetype": "infidelity",
         "summary": "Les couples arrivent sur l'île ; Kiwilo croise le regard de Mangella une seconde de trop."},
        {"ep": 2, "function": "midpoint_twist", "archetype": "secret_child",
         "summary": "Un test de grossesse tombe d'un sac devant tout le monde."},
        {"ep": 3, "function": "climax_and_reset", "archetype": "infidelity",
         "summary": "Le dernier couple affronte la vérité au bûcher."},
    ],
}


def _pairs():
    return {a["id"]: list(a["pairs_well_with"]) for a in templates.load_archetypes()}


def _v2_season_story(store):
    story_id = tcs._season_story(store)
    store.update(story_id, lambda doc: doc["generation_profile"].__setitem__("pipeline", defaults.PIPELINE_V2),
                 now=NOW)
    return story_id


def _season_doc(**extra):
    doc = {
        "$schema": schemas.SEASON_ARC_SCHEMA_NAME, "episodes_planned": 3,
        "arc": [{"ep": ep, "function": function, "summary": "Un résumé.", "open_hooks_in": [],
                 "open_hooks_out": [], "characters": []}
                for ep, function in ((1, "setup"), (2, "midpoint_twist"), (3, "climax_and_reset"))],
        "series_memory": {"recaps": {}, "open_hooks": [], "relationship_state": {}, "introduced": {}},
        "audience_feedback": [], "approved_at": None, "updated_at": NOW,
    }
    doc.update(extra)
    return doc


# ================================================================ the season step

def test_a_v2_story_picks_its_archetypes_and_s2_is_told_each_entrys_beat(store):
    story_id = _v2_season_story(store)
    llm = tcs.FakeLLM(S1v2=[S1V2_REPLY], S2=[tcs.s2("Un."), tcs.s2("Deux."), tcs.s2("Trois.")])

    summary, log = tcs._run(tcs._new().season, store, story_id, step="season", llm=llm, fakes=None,
                            params={"episodes": 3})

    assert summary == {"episodes": 3, "expanded": [1, 2, 3]}
    assert [len(llm.of(p)) for p in ("S1", "S1v2", "S2")] == [0, 1, 3]
    [s1] = llm.of("S1v2")
    user = s1["user"]
    assert "Plot archetypes:\n- betrayal: Un allié juré vend le héros" in user
    assert ("- secret_child: Une grossesse cachée, ou un enfant tenu secret depuis des années, menace une "
            "famille, un mariage et un trône. Pairs with: infidelity, inheritance, forgiveness.") in user
    assert all(f"- {archetype_id}: " in user for archetype_id in templates.list_archetype_ids())
    assert "Write the season arc skeleton for 3 episodes, built on the plot archetypes above." in user
    assert "- secondary: at most one more, among those the primary pairs with" in user
    assert "Episode 1 and episode 3 follow the primary; a secondary you name plays at least one episode." in user
    assert s1["schema_name"] == "season_arc_skeleton_v2"

    season = store.read_doc(story_id, "season.json")
    assert season["archetypes"] == {"primary": "infidelity", "secondary": "secret_child"}
    assert [entry["archetype"] for entry in season["arc"]] == ["infidelity", "secret_child", "infidelity"]
    assert [entry["summary"] for entry in season["arc"]] == ["Un.", "Deux.", "Trois."]
    assert "📅 Season arc: 3 episodes outlined (plot archetypes: infidelity + secret_child)" in log

    first, second, _third = (call["user"] for call in llm.of("S2"))
    assert ("Plot archetype of this episode: L'infidélité. Its `setup` beat: En public, le couple paraît "
            "intouchable ;") in first
    assert ("Plot archetype of this episode: L'enfant caché. Its `midpoint_twist` beat: Retournement : le père "
            "supposé n'est pas le père, et le vrai se tient dans la pièce.\n"
            "Build this entry on that beat, with this story's own characters and world.\n\n"
            "Expand this arc entry into full detail.") in second


def test_regenerating_an_entry_keeps_its_archetype_and_shows_its_beat(store):
    story_id = _v2_season_story(store)
    tcs._run(tcs._new().season, store, story_id, step="season", fakes=None, params={"episodes": 3},
             llm=tcs.FakeLLM(S1v2=[S1V2_REPLY], S2=[tcs.s2("Un."), tcs.s2("Deux."), tcs.s2("Trois.")]))
    llm = tcs.FakeLLM(S2=[tcs.s2("Deux, encore.")])
    ctx, _log = tcs._ctx(store, story_id, step="season")
    tools = tcs._new().entities.Tools(runner=llm, time_fn=lambda: 100.0)

    entry = season_step.expand_entry(ctx, store, 2, tools=tools, note="plus sombre", regenerate=True)

    assert entry["summary"] == "Deux, encore." and entry["archetype"] == "secret_child"
    assert "Its `midpoint_twist` beat: Retournement : le père supposé" in llm.of("S2")[0]["user"]


def test_a_legacy_story_sends_todays_s1_and_s2_asks(store):
    story_id = tcs._season_story(store)
    llm = tcs.FakeLLM(S1=[tcs.S1_REPLY], S2=[tcs.s2("Un."), tcs.s2("Deux."), tcs.s2("Trois.")])

    _summary, log = tcs._run(tcs._new().season, store, story_id, step="season", llm=llm, fakes=None,
                             params={"episodes": 3})

    assert [len(llm.of(p)) for p in ("S1", "S1v2", "S2")] == [1, 0, 3]
    [s1] = llm.of("S1")
    assert s1["schema_name"] == "season_arc_skeleton"
    assert "Plot archetype" not in s1["user"]
    assert s1["user"].endswith(prompts._S1_ASK_TEMPLATE.format(
        episodes=3, functions=", ".join(schemas.ARC_FUNCTIONS)))
    assert not any("Plot archetype" in call["user"] for call in llm.of("S2"))
    season = store.read_doc(story_id, "season.json")
    assert "archetypes" not in season and not any("archetype" in entry for entry in season["arc"])
    assert "📅 Season arc: 3 episodes outlined" in log


def test_a_v2_reply_with_an_unpaired_secondary_is_retried_then_fails(store):
    story_id = _v2_season_story(store)
    bad = copy.deepcopy(S1V2_REPLY)
    bad["archetypes"]["secondary"] = "rigged_contest"  # infidelity does not pair with it
    bad["arc"][1]["archetype"] = "rigged_contest"
    llm = tcs.FakeLLM(S1v2=[bad, bad])

    message, _log = tcs._failed(tcs._new().season, store, story_id, step="season", llm=llm, fakes=None,
                                params={"episodes": 3})

    assert "'rigged_contest' does not pair with 'infidelity'" in message
    assert store.read_doc(story_id, "season.json") is None


# ================================================================ the S1/S2 builders

def test_build_s1_without_archetypes_is_todays_prompt_and_schema():
    pack = context.build_pack(language="fr", story=tsp._phase2_bible_story())
    cast = [{"name": "Kiwilo", "role": "lead", "one_line": "Kiwilo séduit tout le monde."}]
    legacy = prompts.build_s1(pack, episodes=8, cast=cast, places=[])
    assert prompts.build_s1(pack, episodes=8, cast=cast, places=[], archetypes=None) == legacy
    assert legacy[2] == schemas.s1_schema(8)
    assert "Plot archetypes" not in legacy[1] and "archetype" not in json.dumps(legacy[2])


def test_build_s1_with_archetypes_lists_each_and_asks_for_the_pick():
    pack = context.build_pack(language="en")
    archetypes = season_step._archetype_pick_list("en")
    _system, user, schema = prompts.build_s1(pack, episodes=8, cast=[], places=[], archetypes=archetypes)
    assert user.startswith("Plot archetypes:\n- betrayal: A sworn ally sells the hero out for power")
    assert "- rigged_contest: A contest promises fame or a crown" in user
    assert "Pairs with: rigged_contest, infidelity." in user
    assert "- archetype: the primary or the secondary, whose beat this episode plays" in user
    assert schema == schemas.s1_archetype_schema(8, templates.list_archetype_ids())
    assert schema["properties"]["archetypes"]["properties"]["secondary"]["enum"][-1] is None


def test_build_s2_without_an_archetype_is_todays_prompt():
    pack = context.build_pack(language="en")
    arc = tsp._tentafruit_skeleton_arc()
    assert (prompts.build_s2(pack, entry=arc[0], arc=arc, cast=[], archetype=None)
            == prompts.build_s2(pack, entry=arc[0], arc=arc, cast=[]))


def test_entry_archetype_reads_the_entrys_beat_in_the_story_language():
    entry = {"function": "crisis", "archetype": "rigged_contest"}
    assert season_step.entry_archetype(entry, "en") == {
        "label": "Rigged contest",
        "beat": "The underdog is framed for cheating and faces disqualification live, the proof still in her hands."}
    assert season_step.entry_archetype({"function": "crisis"}, "fr") is None
    assert season_step.entry_archetype({"function": "crisis", "archetype": "amnesia"}, "fr") is None


# ================================================================ schemas

def test_s1_archetype_errors_accept_a_good_reply_and_name_each_broken_rule():
    pairs = _pairs()
    assert schemas.s1_archetype_errors(S1V2_REPLY, 3, pairs) == []
    assert schemas.s1_archetype_errors(dict(S1V2_REPLY, archetypes={"primary": "infidelity", "secondary": None},
                                            arc=[dict(e, archetype="infidelity") for e in S1V2_REPLY["arc"]]),
                                       3, pairs) == []

    def broken(change):
        doc = copy.deepcopy(S1V2_REPLY)
        change(doc)
        return schemas.s1_archetype_errors(doc, 3, pairs)

    assert broken(lambda d: d["arc"][0].__setitem__("archetype", "secret_child")) == [
        "$.arc[0].archetype: the first and the last episode follow the primary, 'infidelity'"]
    assert broken(lambda d: d["arc"][1].__setitem__("archetype", "betrayal")) == [
        "$.arc[1].archetype: 'betrayal' is neither the primary nor the secondary",
        "$.archetypes.secondary: 'secret_child' plays no episode; give it one or name none"]
    assert broken(lambda d: d["archetypes"].__setitem__("secondary", "infidelity"))[0].startswith(
        "$.archetypes.secondary: 'infidelity' does not pair with 'infidelity'")
    # S1's own rules still hold: episode 1 is setup.
    assert "$.arc[0].function: episode 1 must be 'setup'" in broken(
        lambda d: d["arc"][0].__setitem__("function", "escalation"))
    # The legacy reply shape is not a v2 reply.
    assert schemas.s1_archetype_errors(tcs.S1_REPLY, 3, pairs) != []


def test_the_season_schema_accepts_archetypes_and_rejects_ids_outside_the_library_or_the_choice():
    legacy = _season_doc()
    assert schemas.season_arc_errors(legacy) == []
    chosen = _season_doc(archetypes={"primary": "infidelity", "secondary": None})
    chosen["arc"][1]["archetype"] = "infidelity"
    assert schemas.season_arc_errors(chosen) == []

    unknown = _season_doc(archetypes={"primary": "amnesia", "secondary": "infidelity"})
    assert schemas.season_arc_errors(unknown) == ["$.archetypes.primary: 'amnesia' is not a shipped plot archetype"]
    same = _season_doc(archetypes={"primary": "betrayal", "secondary": "betrayal"})
    assert schemas.season_arc_errors(same) == ["$.archetypes.secondary: must differ from the primary"]
    outside = copy.deepcopy(chosen)
    outside["arc"][2]["archetype"] = "betrayal"
    assert schemas.season_arc_errors(outside) == [
        "$.arc[2].archetype: 'betrayal' is neither the season's primary nor its secondary"]
    unchosen = _season_doc()
    unchosen["arc"][0]["archetype"] = "infidelity"
    assert schemas.season_arc_errors(unchosen) == ["$.arc[0].archetype: the season chose no archetypes"]
    malformed = _season_doc(archetypes={"primary": "Infidelity!", "secondary": None})
    assert schemas.season_arc_errors(malformed) != []
    extra_key = _season_doc(archetypes={"primary": "infidelity", "secondary": None, "tertiary": "betrayal"})
    assert schemas.season_arc_errors(extra_key) != []


# ================================================================ read-only mentions

def test_the_series_memory_block_names_the_seasons_archetypes_from_episode_2():
    season = _season_doc(archetypes={"primary": "secret_child", "secondary": "inheritance"})
    season["series_memory"]["recaps"] = {"ep01": "Le test tombe du sac."}
    text, cut = context.memory_section(season, 2)
    assert text.splitlines()[:3] == ["Series memory:",
                                     "- Plot archetypes: Secret child (primary), Inheritance (secondary)",
                                     "- Previous recap: Le test tombe du sac."]
    assert not cut
    assert context.memory_section(season, 1) == ("none yet", False)
    legacy = _season_doc()
    legacy["series_memory"]["recaps"] = {"ep01": "Le test tombe du sac."}
    assert context.memory_section(legacy, 2)[0] == "Series memory:\n- Previous recap: Le test tombe du sac."
    alone = _season_doc(archetypes={"primary": "betrayal", "secondary": None})
    assert context.archetype_line(alone) == "- Plot archetypes: Betrayal (primary)"


def test_build_d5_names_the_archetype_and_its_beat_on_one_line_only_when_given():
    pack = context.build_pack(language="fr")
    entry = {"ep": 2, "function": "midpoint_twist", "summary": "Le test tombe.", "open_hooks_in": [],
             "open_hooks_out": [], "characters": []}
    kwargs = dict(ep=2, planned=3, entry=entry, previous=None, cast=[], others=[], places=[], props=[])
    legacy = prompts.build_d5(pack, **kwargs)
    assert prompts.build_d5(pack, archetype=None, **kwargs) == legacy and "Plot archetype" not in legacy[1]
    _system, user, _schema = prompts.build_d5(pack, archetype=season_step.entry_archetype(
        dict(entry, archetype="secret_child"), "fr"), **kwargs)
    assert ("Season arc, episode 2 of 3 (midpoint_twist): Le test tombe.\n\n"
            "Plot archetype: L'enfant caché; this episode's beat: Retournement : le père supposé n'est pas le "
            "père, et le vrai se tient dans la pièce.\n\n") in user


# ================================================================ budgets (DEC-138's method)

def _longest_archetype_line(language):
    """The ``{label, beat}`` with the most characters the library can show."""
    return max((season_step.entry_archetype({"archetype": a, "function": f}, language)
                for a in templates.list_archetype_ids() for f in schemas.ARC_FUNCTIONS),
               key=lambda item: len(item["label"]) + len(item["beat"]))


def _s1v2_worst():
    """S1v2 on its French worst case: the bible past its 120-word cut, the
    world at B2's caps, 12 cast and 8 places with 20-word one-lines, 12
    episodes, the library's seven French premises with their pairs."""
    story = budgets._knowledge_story()
    story.update(budgets.STORY)
    pack = context.build_pack(language="fr", story=story)
    cast = [{"name": budgets._name(12), "role": "support", "one_line": budgets._fr(20)} for _ in range(12)]
    places = [{"name": budgets._name(30), "one_line": budgets._fr(20)} for _ in range(8)]
    return prompts.build_s1(pack, episodes=12, cast=cast, places=places,
                            archetypes=season_step._archetype_pick_list("fr"))


def test_the_s1v2_worst_case_measures_what_is_recorded_and_fits_its_budget():
    worst = context.estimate_tokens(*_s1v2_worst()[:2])
    assert worst == 2301
    budget = prompts.INPUT_BUDGET["S1v2"]
    assert budget == -(-round(worst * 1.15, 1) // 10) * 10 and budget <= 4000
    context.check_budget(*_s1v2_worst()[:2], budget=budget)


def test_the_largest_french_s1v2_reply_fits_its_cap_but_not_a_much_smaller_one():
    longest = max(templates.list_archetype_ids(), key=len)
    second = max((a for a in templates.list_archetype_ids() if a != longest), key=len)
    reply = {"archetypes": {"primary": longest, "secondary": second},
             "arc": [dict(entry, archetype=longest) for entry in tsp._FR_S1["arc"]]}
    reply["arc"] = [{key: entry[key] for key in ("ep", "function", "archetype", "summary")} for entry in reply["arc"]]
    assert [entry["summary"] for entry in reply["arc"]] == [e["summary"] for e in tsp._FR_S1["arc"]]
    needed = estimate_tokens(json.dumps(reply, ensure_ascii=False)) * tsp.FRENCH_TOKEN_FACTOR
    assert needed == pytest.approx(997.1, abs=0.05)
    cap = prompts.MAX_TOKENS["S1v2"]
    assert cap == -(-round(needed * 1.15, 1) // 10) * 10
    assert cap // 2 < needed <= cap
    assert prompts.MAX_TOKENS["S1"] == 950  # a legacy story's cap is untouched


def test_s2_with_the_longest_beat_stays_within_the_pack_budget_on_the_largest_fixture():
    pack = context.build_pack(language="fr", story=tsp._phase2_bible_and_world_story(), template=tsp.FRUIT_DRAMA)
    arc = [{"ep": i + 1, "function": "escalation",
            "summary": "Un episode plein de rebondissements pour tous les personnages sur l'ile cette semaine-la."}
           for i in range(12)]
    system, user, _ = prompts.build_s2(pack, entry=arc[0], arc=arc, cast=tsp._phase2_large_cast(),
                                       archetype=_longest_archetype_line("fr"))
    assert context.check_budget(system, user) <= context.PACK_TOKEN_BUDGET


def test_d5_worst_case_with_the_longest_archetype_line_still_fits_its_budget():
    pack = context.build_pack(language="fr", story=budgets._knowledge_story())
    entry = {"ep": 12, "function": "climax_and_reset", "summary": budgets._at_density(60, budgets.LIVE_ARC_DENSITY),
             "open_hooks_in": [budgets._filler(15, 120)] * 3, "open_hooks_out": [budgets._filler(15, 120)] * 3,
             "characters": []}
    cast = [{"name": budgets._name(60), "role": "support", "goal": budgets._fr(20), "need": budgets._fr(20),
             "secrets": [budgets._fr(20)] * 2, "now": [{"with": budgets._name(60), "now": budgets._fr(15)}] * 3}
            for _ in range(5)]
    system, user, _ = prompts.build_d5(
        pack, ep=12, planned=12, entry=entry, previous=[budgets._fr(25)] * 8, cast=cast,
        others=[{"name": budgets._name(60), "role": "recurring"}] * 7,
        places=[{"name": budgets._name(60), "one_line": budgets._filler(30, 200)}] * 8,
        props=[{"name": budgets._name(60), "owner": budgets._name(60)}] * 8,
        archetype=_longest_archetype_line("fr"))
    assert context.check_budget(system, user, budget=prompts.INPUT_BUDGET["D5"]) <= prompts.INPUT_BUDGET["D5"]
