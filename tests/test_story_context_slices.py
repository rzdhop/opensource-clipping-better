"""AI Story phase 7, stage 5c (A13, DEC-228): the context slices a v2 story's
writing calls read.

``context.slice_for_scene`` renders, for one scene, only what concerns the
characters present: their goal, need, the secret relevant to the beat and
catchphrases (dossiers), the relationship history among them, what each
knows so far (the knowledge timeline's ``knows_after`` up to this episode's
previous beats), where each stands (the ledger), the beat's purpose and the
place's layout and light -- every part word-capped, so the whole slice is
bounded by construction (its prompts' budgets are measured on it,
tests/test_story_episode_prompt_budgets.py).

The script step of a v2 story writes with E1v2/E2v2/E3v2, each reading its
slice (the last test, through ``test_story_episode_steps``'s ready story and
fake LLM, the suite's cross-module pattern).

Against the parent commit (e1d459f) ``context`` has no ``slice_for_scene``
and a v2 script calls E1/E2/E3: every test fails (``AttributeError``, then
the E1v2 assertion).

Stdlib + pytest (DEC-012); offline and hermetic, nothing written outside
``tmp_path``.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

from clipping.aistory import context

import test_story_episode_steps as eps
from test_story_episode_steps import hermetic, store  # noqa: F401 -- fixtures (hermetic is autouse)

FR = ("trahison alliance secret complot vérité mensonge rivalité jalousie éliminé caméra public scandale bouche "
      "fâché regarder vote noyau récolte pomme cerise").split()


def _fr(n, offset=0):
    return " ".join(FR[(i + offset) % len(FR)] for i in range(n))


def _dossier(cid, *, secrets, relationships=(), at_caps=False):
    text = (lambda n: _fr(n)) if at_caps else (lambda n: f"{cid} texte")
    return {
        "backstory": text(60), "goal": f"{cid} veut gagner " + (_fr(17) if at_caps else ""),
        "need": text(20), "fears": text(20), "secrets": list(secrets),
        "relationships": [{"with": other, "history": history, "now": now} for other, history, now in relationships],
        "voice": {"patterns": text(20), "vocabulary": text(20),
                  "catchphrases": [_fr(10)] * 2 if at_caps else [f"{cid} phrase"]},
        "arc": text(30),
    }


def _look(n_words=8):
    return {"build": "slim", "silhouette": "tall", "face": "round", "hair": "none", "skin_material": "fuzzy",
            "height_cm": 170, "palette": ["green"],
            "wardrobe_sets": [{"id": "daily", "context": _fr(n_words), "items": _fr(20)},
                              {"id": "gala", "context": "soirée de gala", "items": "a velvet cape"}],
            "season_change": None}


def _character(cid, name, dossier):
    return {"char_id": cid, "name": name, "descriptor": f"{name} the fruit", "signature_items": [],
            "dossier": dossier, "look": _look()}


def _place(pid, name, *, words=15):
    return {"place_id": pid, "name": name, "descriptor": _fr(30), "layout_notes": _fr(40),
            "look": {"layout_map": {key: _fr(words) for key in ("left", "right", "back", "foreground", "centre")},
                     "scale_note": _fr(words), "lighting": {"day": _fr(words), "night": _fr(words)},
                     "props_here": []}}


def _ec(characters, places, *, ep=1, season=None):
    return SimpleNamespace(
        ep=ep, season=season or {"series_memory": {"recaps": {}, "open_hooks": [], "relationship_state": {},
                                                  "introduced": {}}},
        entities={"characters": {doc["char_id"]: doc for doc in characters},
                  "places": {doc["place_id"]: doc for doc in places}, "props": {}})


def _scene(characters, place="place_pool", summary="Ils se disputent au bord du bassin."):
    return {"scene_id": "s02", "function": "rising", "place_id": place, "time_variant": "day",
            "characters": list(characters), "props": [], "summary": summary, "emotion": "tension"}


def _state(location="place_pool", wardrobe="daily"):
    return {"location": location, "wardrobe_set": wardrobe, "possessions": [], "injuries": None,
            "relationship_notes": None}


def test_slice_holds_only_present_entities_within_budget():
    ana, bob, cyd, dee = "char_ana", "char_bob", "char_cyd", "char_dee"
    characters = [
        _character(ana, "Ana", _dossier(ana, secrets=["Ana a caché le téléphone sous la piscine."],
                                        relationships=[(bob, "Rivaux depuis toujours.", "Alliés fragiles."),
                                                       (cyd, "CYD-HISTORY-ABSENT", "CYD-NOW-ABSENT")])),
        _character(bob, "Bob", _dossier(bob, secrets=["Bob triche aux votes."])),
        _character(cyd, "Cyd", _dossier(cyd, secrets=["CYD-SECRET-ABSENT téléphone piscine"])),
        _character(dee, "Dee", _dossier(dee, secrets=["DEE-SECRET-ABSENT téléphone piscine"])),
    ]
    knowledge = {"timeline": [{"ep": 1, "beats": [
        {"what": "Ana cherche le téléphone près de la piscine.", "place_id": "place_pool", "who": [ana, bob],
         "objects": [], "knows_after": {ana: "ANA-FACT", cyd: "CYD-FACT-ABSENT"}},
        {"what": "Cyd et Dee complotent au parloir.", "place_id": "place_parloir", "who": [cyd, dee],
         "objects": [], "knows_after": {}},
    ]}], "ledger_seed": {ana: _state(), bob: _state(wardrobe="gala"), cyd: _state(), dee: _state()}}
    ec = _ec(characters, [_place("place_pool", "La Piscine"), _place("place_parloir", "Le Parloir")])

    text = context.slice_for_scene(ec, _scene([ana, bob]), knowledge=knowledge)

    # The two present characters, their relationship, the beat, the ledger, the place's light.
    assert "Beat: ep 1, beat 1 of 2: Ana cherche le téléphone près de la piscine." in text
    assert "- Ana: goal: char_ana veut gagner" in text and "- Bob: goal: char_bob veut gagner" in text
    assert "hides: Ana a caché le téléphone sous la piscine." in text  # relevant to this beat
    assert "Bob triche aux votes." not in text  # shares no word with the beat: not this beat's secret
    assert "- Ana & Bob: Rivaux depuis toujours.; now: Alliés fragiles." in text
    assert "- Bob: at La Piscine; dressed for soirée de gala" in text
    assert "Place: Light: " in text
    # Nothing of the two absent ones -- not their names, secrets, facts or their side of a relationship.
    for absent in ("Cyd", "Dee", "CYD-", "DEE-"):
        assert absent not in text, absent
    # The beat's own facts are learned after it: not known yet during it.
    assert "ANA-FACT" not in text

    # At every cap (French, every dossier field at its cap, both relationships present, a long layout and
    # recap, every fact): the slice stays within its word cap.
    at_caps = [
        _character(ana, "Ana", _dossier(ana, secrets=[_fr(20)] * 2, at_caps=True,
                                        relationships=[(bob, _fr(30), _fr(15))])),
        _character(bob, "Bob", _dossier(bob, secrets=[_fr(20)] * 2, at_caps=True,
                                        relationships=[(ana, _fr(30), _fr(15))])),
        _character(cyd, "Cyd", _dossier(cyd, secrets=[_fr(20)], at_caps=True)),
    ]
    beats = [{"what": _fr(25), "place_id": "place_pool", "who": [ana, bob], "objects": [],
              "knows_after": {ana: _fr(15), bob: _fr(15)}}] * 8
    big = {"timeline": [{"ep": 1, "beats": beats}, {"ep": 2, "beats": beats}],
           "ledger_seed": {cid: _state() for cid in (ana, bob, cyd)}}
    season = {"series_memory": {"recaps": {"ep01": _fr(40)}, "open_hooks": [], "relationship_state": {},
                                "introduced": {}}}
    ec = _ec(at_caps, [_place("place_pool", "La Piscine", words=15)], ep=2, season=season)
    text = context.slice_for_scene(ec, _scene([ana, bob, cyd], summary=_fr(15)), knowledge=big)
    assert len(text.split()) <= context.SCENE_SLICE_MAX_WORDS
    assert "…" in text  # every part was cut, marked, never silently
    assert all(part in text for part in ("Who is here:", "Between them:", "Known so far:", "Where things stand:",
                                         "Place: Light: "))


def test_knows_so_far_follows_the_timeline():
    ana, bob = "char_ana", "char_bob"
    characters = [_character(ana, "Ana", _dossier(ana, secrets=[])), _character(bob, "Bob", _dossier(bob, secrets=[]))]
    places = [_place(pid, pid) for pid in ("place_a", "place_b", "place_c", "place_d")]

    def beat(place, fact=None):
        return {"what": f"Il se passe quelque chose à {place}.", "place_id": place, "who": [ana], "objects": [],
                "knows_after": {ana: fact} if fact else {}}

    knowledge = {"timeline": [
        {"ep": 1, "beats": [beat("place_a"), beat("place_b"), beat("place_c", "FACT-EP1-BEAT3"), beat("place_d")]},
        {"ep": 2, "beats": [beat("place_a")]},
    ], "ledger_seed": {}}

    def knows(ep, place):
        return context.slice_for_scene(_ec(characters, places, ep=ep), _scene([ana], place=place),
                                       knowledge=knowledge)

    assert "FACT-EP1-BEAT3" not in knows(1, "place_a")  # beat 1: before it
    assert "FACT-EP1-BEAT3" not in knows(1, "place_c")  # beat 3 itself: learned at its end
    assert "- Ana knows: FACT-EP1-BEAT3" in knows(1, "place_d")  # a later beat of the same episode
    assert "- Ana knows: FACT-EP1-BEAT3" in knows(2, "place_a")  # the next episode
    assert "- Ana knows: FACT-EP1-BEAT3" in knows(3, "place_a")  # and on (no beat of ep 3 maps)
    assert context.known_facts(knowledge, 1, None, [ana, bob]) == {ana: [], bob: []}


# ================================================================ the script step (fake LLM)

def test_a_v2_episode_s_e2_call_receives_the_scene_slice(store):
    """The script step of a v2 story (approved knowledge base) writes with
    E1v2/E2v2/E3v2: every E2v2 call reads its scene's slice -- the beat of the
    knowledge timeline it stages, who is here with their dossier -- and the
    no-repeat ask; E3v2's cliffhanger block says it too. A legacy story's E2
    reads none of it."""
    story_id = eps._ready_story(store, v2=True)
    kiwilo = store.read_entity(story_id, "characters", eps.KIWILO)
    kiwilo["dossier"] = {"backstory": "Né sur l'île.", "goal": "Garder le téléphone pour lui seul.",
                         "need": "Faire confiance à Mangella.", "fears": "Le vote.",
                         "secrets": ["Il a caché le téléphone au parloir."], "relationships": [],
                         "voice": {"patterns": "Court.", "vocabulary": "Mielleux.", "catchphrases": ["Moi ? Jamais."]},
                         "arc": "Du mensonge à l'aveu."}
    store.write_entity(story_id, "characters", copy.deepcopy(kiwilo), now=eps.NOW)
    llm = eps._script_llm(v2=True)

    eps._run(eps._new().script, store, story_id, llm=llm)

    assert llm.prompts()[0] == "E1v2" and "E1" not in llm.prompts()
    e1 = llm.of("E1v2")[0]
    assert e1["schema_name"] == "episode_beat_sheet_v2"
    assert "Planned beats of episode 1, in order:\n1. Le téléphone sonne au parloir." in e1["user"]
    e2_calls = llm.of("E2v2")
    assert len(e2_calls) == len(eps.BODY) and llm.of("E2") == []
    for call in e2_calls:
        assert call["schema_name"] == "episode_scene_dialogue_v2"
        assert "Scene context (from the story's knowledge base):\nBeat: ep 1, " in call["user"]
        assert "Never repeat or paraphrase a line already spoken in this episode" in call["user"]
    # s02 (Kiwilo and Mangella at the parloir) stages the timeline's one beat, and reads Kiwilo's dossier.
    s02 = next(call["user"] for call in e2_calls if "Kiwilo propose à Mangella une alliance secrète." in
               call["user"].split("Scene (", 1)[1].split("\n", 1)[0])
    assert "Beat: ep 1, beat 1 of 1: Le téléphone sonne au parloir." in s02
    assert "- Kiwilo: goal: Garder le téléphone pour lui seul.; hides: Il a caché le téléphone au parloir." in s02
    e3 = llm.of("E3v2")[0]["user"]
    cliffhanger = e3.split("Cliffhanger scene (", 1)[1].split("\n\n", 1)[0]
    assert "Never repeat or paraphrase a line already spoken in this episode" in cliffhanger

    # A legacy story's E2 is v1: no slice, no v2 ask.
    legacy = eps._ready_story(store)
    llm = eps._script_llm()
    eps._run(eps._new().script, store, legacy, llm=llm)
    assert llm.of("E2v2") == [] and all("Scene context" not in call["user"] for call in llm.of("E2"))
