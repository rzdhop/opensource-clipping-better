"""Input budgets of the episode prompts, measured on live-sized data (AI Story
phase 3, stage 6), and the spec-2.6 shape of ``series_memory``.

Each prompt's worst case is a 12-scene French episode 2 at the limits the
prompts and the documents set -- every line at 22 words, four lines in every
body scene, 15-word summaries, a 60-word author's note on a regenerate, the
arc entry at its 60-word cap with its hooks at their 120-character cap, the
season's memory past its 150-word cut -- written against a cast, places and a
prop whose text has the **lengths** of the live story ``b1104ec66b05``
(measured 2026-09-27 on a scratch copy: words and characters of every
descriptor, personality field, layout note and name). Only the lengths are
copied; the words are filler, so the estimate (``characters / 4``) is the
live one. On that data, before this stage, E2, E3 and T1r were over the
1,200-token pack budget and E1 and T1 at 92 % of it: ``prompts.INPUT_BUDGET``
now gives each its own, sized at the measured worst case plus 15 %.

Nothing is trimmed to fit: a prompt over its budget still raises before any
link is contacted.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import pytest

from clipping.aistory import context, prompts, templates, timing
from clipping.cancel import CancelToken

STYLE = templates.load_style("fruit_drama")  # the live story's lock carries these same texts
TEMPLATE = templates.load_episode_template("serial_60s_v1")
# Stage 12b: E1's own worst case is the 90-s template from episode 2 on (12
# scenes: 1 recap + 1 hook + 9 body + 1 cliffhanger -- see _e1() below), not
# necessarily this file's own TEMPLATE (used by every other prompt's fixture).
TEMPLATE_90 = templates.load_episode_template("serial_90s_v1")
DEFAULTS = STYLE["episode_defaults"]

# The live story's text lengths, (words, characters).
LIVE_BIBLE_DENSITY = (67, 434)
LIVE_CHARACTERS = {
    "char_kiwilo": {"name": 6, "descriptor": (36, 206), "wants": (19, 120), "fears": (14, 87),
                    "speech_style": (15, 103)},
    "char_mangella": {"name": 8, "descriptor": (36, 215), "wants": (19, 108), "fears": (17, 91),
                      "speech_style": (13, 90)},
    "char_broccolia": {"name": 9, "descriptor": (33, 208), "wants": (16, 107), "fears": (18, 99),
                       "speech_style": (12, 77)},
}
LIVE_PLACES = {
    "place_la_piscine_de_la_trahison": {"name": (5, 25), "layout_notes": (40, 267), "variants": ["day", "dusk", "night"]},
    "place_le_parloir_des_secrets": {"name": (4, 22), "layout_notes": (41, 247), "variants": ["day", "night"]},
}
LIVE_PROP = ("prop_telephone_en_noix_de_coco", {"name": (5, 25), "descriptor": (23, 137)})
LIVE_ARC_DENSITY = (52, 362)  # the longest live arc summary

# The measured worst cases (tokens, chars / 4) and the budgets set on them.
# The larger of the scratch-copy run and this fixture's (they differ by at most 4).
# E1, E3 and E4: phase 5 stage 3's continuity worst cases (MEASURED_CONTINUITY,
# below), which are larger; stage 6 recorded 1,102, 2,050 and 3,523.
MEASURED = {"E1": 1574, "E2": 1440, "E3": 2199, "E4": 3598, "T1": 1100, "T1r": 1218}


def _filler(words, chars):
    """Exactly *words* words and *chars* characters (single spaces)."""
    letters = chars - (words - 1)
    assert letters >= words, (words, chars)
    base, extra = divmod(letters, words)
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    out = []
    for i in range(words):
        n = base + (1 if i < extra else 0)
        out.append("".join(alphabet[(i + j) % 26] for j in range(n)))
    text = " ".join(out)
    assert len(text) == chars and len(text.split()) == words
    return text


def _at_density(words, density):
    return _filler(words, round(words * density[1] / density[0]))


def _fr(n):
    """Dense French filler, the stage-4 tests' own (the episode's lines)."""
    words = ("trahison alliance secret complot vérité mensonge rivalité jalousie éliminé "
             "caméra public scandale bouche fâché regarder vote noyau récolte pomme cerise").split()
    return " ".join(words[i % len(words)] for i in range(n))


def _name(chars):
    return _filler(1, chars).capitalize()


CAST = [
    {"char_id": cid, "name": _name(spec["name"]), "descriptor": _filler(*spec["descriptor"]),
     "personality": {"traits": ["a", "b", "c", "d"], "wants": _filler(*spec["wants"]),
                     "fears": _filler(*spec["fears"]), "speech_style": _filler(*spec["speech_style"])}}
    for cid, spec in LIVE_CHARACTERS.items()
]
IDS = [c["char_id"] for c in CAST]
NAMES = {c["char_id"]: c["name"] for c in CAST}
PLACES = [
    {"place_id": pid, "name": _filler(*spec["name"]), "layout_notes": _filler(*spec["layout_notes"]),
     "time_variants": spec["variants"]}
    for pid, spec in LIVE_PLACES.items()
]
LONGEST_PLACE = max(PLACES, key=lambda p: len(p["layout_notes"]))
PROP = {"prop_id": LIVE_PROP[0], "name": _filler(*LIVE_PROP[1]["name"]),
        "descriptor": _filler(*LIVE_PROP[1]["descriptor"])}

HOOK_AT_CAP = _filler(18, 119)
ARC2 = {"ep": 2, "function": "escalation", "summary": _at_density(60, LIVE_ARC_DENSITY),
        "open_hooks_in": [HOOK_AT_CAP] * 2, "open_hooks_out": [HOOK_AT_CAP] * 3}
ARC3 = dict(ARC2, ep=3)
MEMORY = {"series_memory": {
    "recaps": {"ep01": _fr(40), "ep02": _fr(40)},
    "open_hooks": [HOOK_AT_CAP] * 4,
    "relationship_state": {f"{a}|{b}": _fr(15) for a in IDS for b in IDS if a != b},
    "introduced": {},
}}
STORY = {"logline": _at_density(40, LIVE_BIBLE_DENSITY), "premise": _at_density(60, LIVE_BIBLE_DENSITY),
         "tone": _at_density(30, LIVE_BIBLE_DENSITY)}  # past the pack's 120-word bible cut
NOTE = _fr(60)

FUNCTIONS = ["recap", "hook"] + [["setup", "rising", "peak", "turn"][i % 4] for i in range(9)] + ["cliffhanger"]
LINES_PER = {"recap": 1, "hook": 2, "cliffhanger": 1}


def _scene(i, function):
    return {
        "scene_id": f"s{i:02d}", "function": function, "place_id": LONGEST_PLACE["place_id"], "time_variant": "night",
        "characters": list(IDS), "props": [PROP["prop_id"]], "summary": _fr(15), "emotion": "tension",
        "target_duration_s": 8.0 if function not in LINES_PER else 3.0,
        "lines": [{"line_id": f"l{4 * i + k:02d}", "speaker": IDS[k % 3], "text": _fr(22), "emotion": "tension",
                   "delivery": " ".join(["whispering"] * 12)} for k in range(LINES_PER.get(function, 4))],
        "sfx_cues": [], "on_screen_text": None, "state": "written", "source": "E2", "rev": 1,
    }


SCENES = [_scene(i, fn) for i, fn in enumerate(FUNCTIONS)]
BODY = SCENES[10]
LINE = {"speaker_name": CAST[1]["name"], "text": _fr(22)}
PERSONALITIES = [{"char_id": c["char_id"], "name": c["name"], "personality": c["personality"]} for c in CAST]


def _pack(note=None):
    return context.build_pack(language="fr", story=STORY, note=note)


def _fits(prompt_id, system, user):
    budget = prompts.INPUT_BUDGET[prompt_id]
    tokens = context.check_budget(system, user, budget=budget)
    assert tokens <= budget
    return tokens


def _e1():
    return prompts.build_e1(
        _pack(), ep=2, arc_entry=ARC2, template=TEMPLATE_90, episode_defaults=DEFAULTS,
        cast=[{"char_id": c["char_id"], "name": c["name"]} for c in CAST],
        places=[{k: p[k] for k in ("place_id", "name", "time_variants")} for p in PLACES],
        props=[{"prop_id": PROP["prop_id"], "name": PROP["name"]}], memory=MEMORY,
        slots=timing.episode_slots(TEMPLATE_90, 2))


def _e2():
    pack = _pack(NOTE)
    return prompts.build_e2(
        pack, scene=BODY, scene_number=11, outline=SCENES, previous=dict(LINE, summary=_fr(15)), word_budget=18,
        cast=PERSONALITIES, place={k: LONGEST_PLACE[k] for k in ("place_id", "name", "layout_notes")},
        props=[{"prop_id": PROP["prop_id"], "name": PROP["name"]}], sfx_cues=STYLE["audio"]["sfx_cues"],
        narrator_enabled=False, voice_direction=STYLE["audio"]["voice_direction"], note=pack.note)


def _e3(part):
    pack = _pack(None if part is None else NOTE)
    return prompts.build_e3(
        pack, ep=2, part=part, note=pack.note, hook_scene=SCENES[1], cliffhanger_scene=SCENES[-1],
        recap_scene=SCENES[0], outline=SCENES, first_body_line=LINE, last_body_line=LINE, arc_entry=ARC2,
        next_arc_entry=ARC3, memory=MEMORY, episode_defaults=DEFAULTS,
        word_budgets={"hook": 9, "cliffhanger": 12, "recap": 9}, cast=PERSONALITIES, narrator_enabled=False)


def _e4():
    digest = prompts.script_digest({"scenes": SCENES}, {"places": {p["place_id"]: p["name"] for p in PLACES},
                                                         "cast": NAMES})
    return prompts.build_e4(_pack(), script_digest=digest, cast=PERSONALITIES,
                            places=[{"place_id": p["place_id"], "name": p["name"]} for p in PLACES], memory=MEMORY)


def _t1_kwargs():
    return dict(
        scene=BODY, lines=[{k: line[k] for k in ("speaker", "text", "emotion")} for line in BODY["lines"]],
        characters=[{k: c[k] for k in ("char_id", "name", "descriptor")} for c in CAST],
        place={k: LONGEST_PLACE[k] for k in ("place_id", "layout_notes")},
        props=[PROP], shots_per_scene=tuple(DEFAULTS["shots_per_scene"]), camera=STYLE["camera"],
        modifiers_allowed=list(STYLE["motion_rules"]["tier1"]["modifiers"]), hook_style=DEFAULTS["hook_style"])


def _t1():
    return prompts.build_t1(_pack(), previous_shots=[{"framing": "medium_two_shot", "camera_motion": "push_in"}] * 2,
                            **_t1_kwargs())


def _t1r():
    pack = _pack(NOTE)
    shots = [{"framing": f, "camera_motion": "push_in", "lines": [n]}
             for n, f in enumerate(("close_up", "medium_two_shot", "over_shoulder", "wide_establishing"), start=1)]
    return prompts.build_t1r(pack, shots=shots, index=1, note=pack.note, **_t1_kwargs())


# ================================================================ budgets

def test_the_fixture_copies_the_live_lengths():
    assert [len(c["name"]) for c in CAST] == [6, 8, 9]
    assert (len(CAST[0]["descriptor"].split()), len(CAST[0]["descriptor"])) == (36, 206)
    assert (len(LONGEST_PLACE["layout_notes"].split()), len(LONGEST_PLACE["layout_notes"])) == (40, 267)
    assert all(len(line["text"].split()) == 22 for line in BODY["lines"]) and len(BODY["lines"]) == 4
    assert len(SCENES) == 12 and all(len(scene["summary"].split()) == 15 for scene in SCENES)


def test_every_episode_prompt_has_its_own_budget_sized_on_the_measured_worst_case():
    for prompt_id, measured in MEASURED.items():
        budget = prompts.INPUT_BUDGET[prompt_id]
        assert budget >= measured, prompt_id
        if prompt_id != "E4":
            # measured + 15 %, rounded up to the next ten
            assert budget == -(-round(measured * 1.15, 1) // 10) * 10, prompt_id
    # E4 keeps the budget stage 4 gave it (it still holds), under the spec's 4,000 ceiling.
    assert prompts.INPUT_BUDGET["E4"] == 3900


def test_e1_worst_case_fits_its_budget():
    _fits("E1", *_e1()[:2])


def test_e2_worst_case_fits_its_budget():
    assert _fits("E2", *_e2()[:2]) > context.PACK_TOKEN_BUDGET


@pytest.mark.parametrize("part", [None, "hook", "cliffhanger", "recap", "teaser"])
def test_e3_worst_case_fits_its_budget(part):
    _fits("E3", *_e3(part)[:2])


def test_e4_worst_case_fits_its_budget():
    _fits("E4", *_e4()[:2])


def test_t1_worst_case_fits_its_budget():
    _fits("T1", *_t1()[:2])


def test_t1r_worst_case_fits_its_budget():
    assert _fits("T1r", *_t1r()[:2]) > context.PACK_TOKEN_BUDGET


@pytest.mark.parametrize("prompt_id", ["E1", "E2", "E3", "E4", "T1", "T1r"])
def test_a_prompt_over_its_budget_still_raises_before_any_call(prompt_id):
    from clipping.aistory import steps
    from clipping.aistory.steps import llm_call

    calls = []
    ctx = steps.StepContext(
        job_id="job000000001", story_id="0123456789ab", step="script", ep=1, params={}, cancel=CancelToken(),
        settings_env={"LLM_CHAIN": "gemini/gemini-test", "GOOGLE_API_KEY": "test-key"},
        outputs_dir="/nonexistent", on_log=lambda line: None)
    user = "m" * (4 * prompts.INPUT_BUDGET[prompt_id] + 4)

    with pytest.raises(ValueError, match="over the"):
        llm_call.call_json(ctx, prompt_id, "system", user, {}, validator=lambda value: [],
                           runner=lambda chain, **kwargs: calls.append(kwargs))
    assert calls == []


# ================================================================ series memory (spec 2.6)

SPEC_MEMORY = {"series_memory": {
    "recaps": {"ep01": "Kiwilo et Mangella scellent une alliance secrète."},
    "open_hooks": ["Qui a caché le téléphone ?"],
    "relationship_state": {"char_kiwilo|char_mangella": "publiquement ennemis, secrètement alliés",
                           "char_mangella|char_broccolia": "méfiance glaciale",
                           "not a pair": "ignored", "char_kiwilo|char_broccolia": ""},
    "introduced": {"ep01": ["char_kiwilo"]},
}}


def test_memory_for_episode_2_reads_the_spec_shape():
    text, cut = context.memory_section(SPEC_MEMORY, 2)

    assert not cut
    assert text == (
        "Series memory:\n"
        "- Previous recap: Kiwilo et Mangella scellent une alliance secrète.\n"
        "- Open hooks: Qui a caché le téléphone ?\n"
        "- Relationships: char_kiwilo/char_mangella: publiquement ennemis, secrètement alliés; "
        "char_mangella/char_broccolia: méfiance glaciale"
    )


def test_relationship_pairs_keep_only_spec_shaped_entries():
    assert context.relationship_pairs(SPEC_MEMORY["series_memory"]["relationship_state"]) == [
        ("char_kiwilo", "char_mangella", "publiquement ennemis, secrètement alliés"),
        ("char_mangella", "char_broccolia", "méfiance glaciale"),
    ]
    assert context.relationship_pairs(None) == [] and context.relationship_pairs({}) == []


def test_the_e4_memory_block_reads_the_spec_shape():
    digest = "Scene s01 (hook) -- Lieu, day -- characters: Kiwilo\nUn résumé."
    _system, user, _schema = prompts.build_e4(_pack(), script_digest=digest, cast=PERSONALITIES[:1],
                                              places=[{"place_id": "place_x", "name": "Lieu"}], memory=SPEC_MEMORY)

    assert ("Series memory:\n- Episode 1 recap: Kiwilo et Mangella scellent une alliance secrète.\n"
            "- Open hooks: Qui a caché le téléphone ?\n"
            "- Relationships: char_kiwilo/char_mangella: publiquement ennemis, secrètement alliés; "
            "char_mangella/char_broccolia: méfiance glaciale") in user


def test_e1_and_e3_for_episode_2_read_the_spec_shape():
    _s, user, _ = prompts.build_e1(
        _pack(), ep=2, arc_entry=ARC2, template=TEMPLATE, episode_defaults=DEFAULTS,
        cast=[{"char_id": c["char_id"], "name": c["name"]} for c in CAST],
        places=[{k: p[k] for k in ("place_id", "name", "time_variants")} for p in PLACES],
        props=[{"prop_id": PROP["prop_id"], "name": PROP["name"]}], memory=SPEC_MEMORY,
        slots=timing.episode_slots(TEMPLATE, 2))
    assert "- Relationships: char_kiwilo/char_mangella: publiquement ennemis" in user
    _s, user, _ = prompts.build_e3(
        _pack(), ep=2, part="recap", note=None, hook_scene=SCENES[1], cliffhanger_scene=SCENES[-1],
        recap_scene=SCENES[0], outline=SCENES, first_body_line=None, last_body_line=None, arc_entry=ARC2,
        next_arc_entry=ARC3, memory=SPEC_MEMORY, episode_defaults=DEFAULTS, word_budgets={}, cast=PERSONALITIES,
        narrator_enabled=False)
    assert "- Previous recap: Kiwilo et Mangella scellent une alliance secrète." in user


# ================================================================ phase 5 stage 3: continuity (DEC-138)
#
# From episode 2 on, E1 also lists the hooks open when the episode starts (at
# most schemas.PAYOFF_HOOKS_MAX of them, oldest first), the audience direction
# chosen on the previous episode's feedback and the pays_off ask; E3's recap
# block carries the previous recap and its memory lists the hooks open before
# the episode; E4 lists the hook payoffs (and gains the hook_payoff kind). The
# fold can hold HOOKS_OPENED_MAX (3) hooks for each of the 11 episodes before
# the last one (EPISODES_PLANNED_MAX, 12): 33, each at the 120-character cap,
# in dense French. The audience direction is at its 25-word cap
# (F1_DIRECTION_MAX_WORDS); the relationships are the fixture's own (15-word
# texts, RELATIONSHIP_DELTA_MAX_WORDS). E1 pays off one hook per scene at
# most, so E4's payoffs spread over all 12 scenes.
#
# How many hooks make the worst case differs per prompt, so it is searched
# (every count from 0 to 33, every number of the offered hooks paid off --
# test_each_continuity_worst_case_is_the_maximum_over_every_hook_count):
# E1 shows at most 4, so any 4 or more; E3's memory is cut at 150 words, and
# one hook leaves the most of them to the relationships, the densest text;
# E4 is largest with 3 of its 4 offered hooks paid off (one left in the
# memory block's line, three with their scenes in the payoff block).
#
# Measured (chars / 4): E1 1,574 (T2-P5-F7's longer payoff line; 1,558 before) (HEAD 0ae8efb's own fixture: 1,263, 99 % of
# its 1,270 -- the 1,102 recorded above predates stage 12b's numbered scene
# list), E3 2,199 in full (HEAD: 2,071) and 1,549 for the recap alone (HEAD:
# 1,421), E4 3,598 (HEAD: 3,523; the stage-4 fixture of
# test_story_prompts_episode.py: 3,530 -> 3,605). E1 and E3 take the measured
# worst case + 15 %, rounded up to ten (1,820 -- 1,800 before T2-P5-F7 -- and 2,530); E4's 3,900 still
# holds (+8 %) under the spec's 4,000 ceiling, as stage 6 left it.

from clipping.aistory import schemas  # noqa: E402 -- this section's own names

FOLD_HOOKS_MAX = schemas.HOOKS_OPENED_MAX * (schemas.EPISODES_PLANNED_MAX - 1)


def _hook(i):
    """A distinct hook of exactly HOOK_MAX_LENGTH (120) characters, dense French."""
    text = (f"{i:02d} " + _fr(40))[:schemas.HOOK_MAX_LENGTH - 1] + "?"
    assert len(text) == schemas.HOOK_MAX_LENGTH
    return text


OPEN_HOOKS = [_hook(i) for i in range(FOLD_HOOKS_MAX)]
DIRECTION_AT_CAP = _fr(schemas.F1_DIRECTION_MAX_WORDS)
E3_WORST_HOOKS = 1
E4_WORST_PAID = 3


def _offered(hooks=None):
    """The hooks E1 offers (and E4 shows) of *hooks* (OPEN_HOOKS): the
    oldest PAYOFF_HOOKS_MAX. A function, so that on the parent commit (no
    such constant) each test fails on its own instead of the file failing to
    collect (test_story_prompts_metadata imports this module)."""
    return (OPEN_HOOKS if hooks is None else hooks)[:schemas.PAYOFF_HOOKS_MAX]


def _payoffs(hooks=None, paid=None):
    """One hook paid off per scene (E1's own maximum), across all 12 scenes,
    round-robin over the first *paid* (E4_WORST_PAID) offered hooks, grouped
    by hook as E4 takes them."""
    chosen = _offered(hooks)[:E4_WORST_PAID if paid is None else paid]
    return {hook: [scene["scene_id"] for n, scene in enumerate(SCENES) if n % len(chosen) == i]
            for i, hook in enumerate(chosen)}


# The continuity worst cases, measured (chars / 4) on this fixture.
MEASURED_CONTINUITY = {"E1": 1574, "E3": 2199, "E3-recap": 1549, "E4": 3598}


def _e1_continuity(hooks=None):
    return prompts.build_e1(
        _pack(), ep=2, arc_entry=ARC2, template=TEMPLATE_90, episode_defaults=DEFAULTS,
        cast=[{"char_id": c["char_id"], "name": c["name"]} for c in CAST],
        places=[{k: p[k] for k in ("place_id", "name", "time_variants")} for p in PLACES],
        props=[{"prop_id": PROP["prop_id"], "name": PROP["name"]}], memory=MEMORY,
        slots=timing.episode_slots(TEMPLATE_90, 2), open_hooks=OPEN_HOOKS if hooks is None else hooks,
        audience_direction=DIRECTION_AT_CAP)


def _e3_continuity(part, hooks=None):
    pack = _pack(None if part is None else NOTE)
    return prompts.build_e3(
        pack, ep=2, part=part, note=pack.note, hook_scene=SCENES[1], cliffhanger_scene=SCENES[-1],
        recap_scene=SCENES[0], outline=SCENES, first_body_line=LINE, last_body_line=LINE, arc_entry=ARC2,
        next_arc_entry=ARC3, memory=MEMORY, episode_defaults=DEFAULTS,
        word_budgets={"hook": 9, "cliffhanger": 12, "recap": 9}, cast=PERSONALITIES, narrator_enabled=False,
        open_hooks=OPEN_HOOKS[:E3_WORST_HOOKS] if hooks is None else hooks)


def _e4_continuity(hooks=None, paid=None):
    """E4 checks only against the recaps of the episodes before it: episode 3
    is the first to see both of the fixture's (ep01, ep02) -- its worst case."""
    digest = prompts.script_digest({"scenes": SCENES}, {"places": {p["place_id"]: p["name"] for p in PLACES},
                                                         "cast": NAMES})
    return prompts.build_e4(_pack(), script_digest=digest, cast=PERSONALITIES,
                            places=[{"place_id": p["place_id"], "name": p["name"]} for p in PLACES], memory=MEMORY,
                            ep=3, open_hooks=OPEN_HOOKS if hooks is None else hooks, payoffs=_payoffs(hooks, paid))


def _tokens(triple):
    return context.estimate_tokens(triple[0], triple[1])


def test_the_continuity_fixture_is_at_its_caps():
    assert FOLD_HOOKS_MAX == 33 and len(set(OPEN_HOOKS)) == 33
    assert all(len(hook) == 120 for hook in OPEN_HOOKS)
    assert len(DIRECTION_AT_CAP.split()) == 25
    assert sorted(sid for sids in _payoffs(paid=4).values() for sid in sids) == [s["scene_id"] for s in SCENES]
    _s, user, schema = _e1_continuity()
    assert all(hook in user for hook in _offered()) and OPEN_HOOKS[4] not in user
    assert DIRECTION_AT_CAP in user
    enum = schema["properties"]["scenes"]["items"]["properties"]["pays_off"]["items"]["enum"]
    assert enum == _offered()
    _s, user, schema = _e4_continuity()
    assert all(hook in user for hook in _offered()) and "hook_payoff" in user


@pytest.mark.parametrize("prompt_id", ["E1", "E3", "E3-recap", "E4"])
def test_the_continuity_worst_cases_measure_what_is_recorded_and_fit_their_budgets(prompt_id):
    system, user, _schema = {"E1": _e1_continuity, "E3": lambda: _e3_continuity(None),
                             "E3-recap": lambda: _e3_continuity("recap"), "E4": _e4_continuity}[prompt_id]()
    tokens = _fits(prompt_id.split("-")[0], system, user)
    assert tokens == MEASURED_CONTINUITY[prompt_id]
    assert prompts.INPUT_BUDGET[prompt_id.split("-")[0]] <= 4000


def test_each_continuity_worst_case_is_the_maximum_over_every_hook_count():
    counts = range(FOLD_HOOKS_MAX + 1)
    assert max(_tokens(_e1_continuity(OPEN_HOOKS[:k])) for k in counts) == MEASURED_CONTINUITY["E1"]
    assert max(_tokens(_e3_continuity(None, OPEN_HOOKS[:k])) for k in counts) == MEASURED_CONTINUITY["E3"]
    assert max(_tokens(_e3_continuity("recap", OPEN_HOOKS[:k])) for k in counts) == MEASURED_CONTINUITY["E3-recap"]
    e4 = max(_tokens(_e4_continuity(OPEN_HOOKS[:k], paid)) for k in counts
             for paid in range(min(k, schemas.PAYOFF_HOOKS_MAX) + 1))
    assert e4 == MEASURED_CONTINUITY["E4"]


def test_the_measured_worst_case_of_e1_and_e3_is_the_continuity_one():
    """MEASURED (which the budgets are sized on, above) holds the larger of
    the pre-stage-3 worst case and the continuity one."""
    assert MEASURED["E1"] == max(MEASURED_CONTINUITY["E1"], _tokens(_e1()))
    assert MEASURED["E3"] == max(MEASURED_CONTINUITY["E3"], MEASURED_CONTINUITY["E3-recap"], _tokens(_e3(None)))
    assert MEASURED["E4"] == max(MEASURED_CONTINUITY["E4"], _tokens(_e4()))


@pytest.mark.parametrize("part", ["hook", "cliffhanger", "teaser"])
def test_e3_partials_with_the_continuity_inputs_fit_too(part):
    _fits("E3", *_e3_continuity(part)[:2])


def test_memory_section_lists_the_hooks_it_is_handed():
    """Stage 3: the caller hands memory_section the hooks open when the
    episode starts (series_memory.open_hooks_before); None keeps reading the
    stored list, an empty list lists none, episode 1 is "none yet"."""
    text, cut = context.memory_section(SPEC_MEMORY, 2, open_hooks=["Le vote est-il truqué ?"])
    assert not cut
    assert text == (
        "Series memory:\n"
        "- Previous recap: Kiwilo et Mangella scellent une alliance secrète.\n"
        "- Open hooks: Le vote est-il truqué ?\n"
        "- Relationships: char_kiwilo/char_mangella: publiquement ennemis, secrètement alliés; "
        "char_mangella/char_broccolia: méfiance glaciale"
    )
    text, _cut = context.memory_section(SPEC_MEMORY, 2, open_hooks=[])
    assert "Open hooks" not in text and "- Previous recap:" in text
    assert context.memory_section(SPEC_MEMORY, 2, open_hooks=None) == context.memory_section(SPEC_MEMORY, 2)
    assert context.memory_section(SPEC_MEMORY, 1, open_hooks=["x"]) == ("none yet", False)
    assert context.previous_recap(SPEC_MEMORY, 2) == "Kiwilo et Mangella scellent une alliance secrète."
    assert context.previous_recap(SPEC_MEMORY, 1) is None and context.previous_recap(SPEC_MEMORY, 3) is None


# ================================================================ phase 7: D2/D3/R1v2 (the look)
#
# The look calls of a v2 story (stage 3a), measured the same way (DEC-138):
# every input at the cap its own source document sets, on the style whose
# texts are the longest, French, on a regenerate (the current look at its caps
# and a 60-word note). D2: 11 other characters (the cast block's cap less the
# one drawn) with 60-character names and 15-word builds; K1's text at its caps
# (a 45-word descriptor at the live density, 3 items of 60 characters, a
# 200-character one-line, a 60-character archetype). D3: a 45-word descriptor,
# 60-word layout notes, all 5 time variants, 8 props (D6's registry cap) with
# 60-character names and 200-character one-lines. R1v2: a 30-word descriptor,
# the owner's 15-word build, 12 cast names and 8 place names of 60 characters.
# Budget = worst case + 15 %, rounded up to ten.

MEASURED_LOOK = {"D2": 1987, "D3": 1685, "R1v2": 1014}
ALL_STYLES = [templates.load_style(style_id) for style_id in templates.list_style_ids()]
_DESCRIPTOR_DENSITY = LIVE_CHARACTERS["char_kiwilo"]["descriptor"]


def _look_at_caps():
    return {"build": _filler(15, 90), "silhouette": _filler(12, 72), "face": _filler(15, 90),
            "hair": _filler(12, 72), "skin_material": _filler(12, 72), "height_cm": 175,
            "palette": [_filler(3, 18)] * 4,
            "wardrobe_sets": [{"id": f"set_{i}", "context": _filler(8, 48), "items": _filler(20, 120)}
                              for i in range(3)],
            "season_change": _filler(20, 120)}


def _d2(style):
    pack = context.build_pack(language="fr", story=STORY, template=style, note=NOTE)
    character = {"name": _name(60), "role": "lead", "archetype": _filler(8, 60), "one_line": _filler(30, 200),
                 "descriptor": _at_density(45, _DESCRIPTOR_DENSITY), "signature_items": [_filler(8, 60)] * 3}
    others = [{"name": _name(60), "build": _filler(15, 90), "height_cm": 175} for _ in range(11)]
    return prompts.build_d2(pack, character=character, others=others, rendering=style["rendering"],
                            regenerate={"field": "look", "current": _look_at_caps(), "note": pack.note})


def _d3(style):
    pack = context.build_pack(language="fr", story=STORY, template=style, note=NOTE)
    place = {"name": _name(60), "descriptor": _at_density(45, _DESCRIPTOR_DENSITY),
             "layout_notes": _at_density(60, LIVE_PLACES["place_la_piscine_de_la_trahison"]["layout_notes"]),
             "time_variants": ["day", "night", "dusk", "rain", "dawn"]}
    props = [{"name": _name(60), "one_line": _filler(30, 200)} for _ in range(8)]
    current = {"layout_map": {key: _filler(15, 90) for key in ("left", "right", "back", "foreground", "centre")},
               "scale_note": _filler(15, 90), "lighting": {v: _filler(15, 90) for v in place["time_variants"]},
               "props_here": [f"prop_{'x' * 40}"] * 8}
    return prompts.build_d3(pack, place=place, environment_rules=style["environment_rules"], props=props,
                            regenerate={"field": "look", "current": current, "note": pack.note})


def _r1v2(style):
    pack = context.build_pack(language="fr", story=STORY, template=style, note=NOTE)
    prop = {"name": _name(60), "one_line": _filler(30, 200), "descriptor": _filler(30, 180)}
    owner = {"name": _name(60), "build": _filler(15, 90), "height_cm": 175}
    current = {"scale_cm": 12.5, "material": _filler(8, 48), "colour": _filler(6, 36), "scale_phrase": _filler(10, 60)}
    return prompts.build_r1v2(pack, prop=prop, owner=owner, cast=[{"name": _name(60)}] * 12,
                              places=[{"name": _name(60)}] * 8,
                              regenerate={"field": "look", "current": current, "note": pack.note})


_LOOK_BUILDERS = {"D2": _d2, "D3": _d3, "R1v2": _r1v2}


@pytest.mark.parametrize("prompt_id", ["D2", "D3", "R1v2"])
def test_look_worst_cases_measure_what_is_recorded_and_fit_their_budgets(prompt_id):
    worst = max(_tokens(_LOOK_BUILDERS[prompt_id](style)) for style in ALL_STYLES)
    assert worst == MEASURED_LOOK[prompt_id]
    budget = prompts.INPUT_BUDGET[prompt_id]
    assert budget == -(-round(worst * 1.15, 1) // 10) * 10
    for style in ALL_STYLES:
        _fits(prompt_id, *_LOOK_BUILDERS[prompt_id](style)[:2])
