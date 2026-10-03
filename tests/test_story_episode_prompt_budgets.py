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


# Phase 7 stage 4 (DEC-227, DEC-138's method): T1 v2 and its re-plan on the same
# live-sized data, with what they read beyond T1 at its caps -- the place's
# descriptor (45 words), each of the four lines' 12-word delivery, a 6-word
# on-screen text and 6 sound cues, the prop's look as shots.render_prop says it
# (a 30-word descriptor, material 8, colour 6, scale 10), two shots asked, and
# the previous shot's 45-word action with 4 staging entries at 4 + 4 words (T1r
# v2: the scene's two shots so far at 45 words, a 60-word note). Measured
# (chars / 4): T1v2 1,756, T1rv2 1,787; each budget the worst case + 15 %,
# rounded up to ten. The v1 rows above are untouched (RC-M1).
MEASURED_V2 = {"T1v2": 1756, "T1rv2": 1787}
_DENSITY = LIVE_CHARACTERS["char_kiwilo"]["descriptor"]  # the live descriptor's (words, characters)


def _staging_at_caps():
    return [{"subject": f"@{cid}", "position": "left", "facing": _fr(4), "expression": _fr(4)} for cid in IDS] + [
        {"subject": f"%{PROP['prop_id']}", "position": "back", "facing": _fr(4), "expression": _fr(4)}]


def _t1_v2_kwargs():
    scene = dict(BODY, on_screen_text=_fr(6),
                 sfx_cues=[{"at": BODY["lines"][k % 4]["line_id"], "cue": STYLE["audio"]["sfx_cues"][k % 3]}
                           for k in range(6)])
    return dict(
        scene=scene, lines=[{k: line[k] for k in ("line_id", "speaker", "text", "emotion", "delivery")}
                            for line in BODY["lines"]],
        characters=[{k: c[k] for k in ("char_id", "name", "descriptor")} for c in CAST],
        place=dict({k: LONGEST_PLACE[k] for k in ("place_id", "layout_notes")}, descriptor=_at_density(45, _DENSITY)),
        props=[dict(PROP, look=_at_density(30 + 8 + 6 + 10, _DENSITY))], shots_per_scene=(2, 2),
        camera=STYLE["camera"], modifiers_allowed=list(STYLE["motion_rules"]["tier1"]["modifiers"]),
        hook_style=DEFAULTS["hook_style"])


def _t1_v2():
    previous = [{"framing": "medium_two_shot", "camera_motion": "push_in"},
                {"framing": "medium_single", "camera_motion": "push_in", "action": _fr(45),
                 "staging": _staging_at_caps()}]
    return prompts.build_t1_v2(_pack(), previous_shots=previous, **_t1_v2_kwargs())


def _t1r_v2():
    pack = _pack(NOTE)
    shots = [{"framing": f, "camera_motion": "push_in", "lines": [n, n + 1], "action": _fr(45)}
             for n, f in ((1, "close_up"), (3, "medium_two_shot"))]
    return prompts.build_t1r_v2(pack, shots=shots, index=1, note=pack.note, **_t1_v2_kwargs())


def test_t1_v2_and_t1r_v2_worst_cases_fit_their_measured_budgets():
    for prompt_id, build in (("T1v2", _t1_v2), ("T1rv2", _t1r_v2)):
        system, user, _schema = build()
        tokens = _fits(prompt_id, system, user)
        assert tokens == MEASURED_V2[prompt_id], (prompt_id, tokens)
        assert prompts.INPUT_BUDGET[prompt_id] == -(-round(MEASURED_V2[prompt_id] * 1.15, 1) // 10) * 10
        assert tokens > prompts.INPUT_BUDGET["T1"]  # past T1's own budget: it needs its own


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
#
# D2 re-measured for fix A3 (DEC-226's amendment): the ask gained a
# presentation line (apparent age and gender presentation, optional, at most
# 8 words), which grows the input a little; and again for stage F2 (phase 7
# follow-up): a bearing line (posture, optional, at most 10 words).

MEASURED_LOOK = {"D2": 2096, "D3": 1685, "R1v2": 1014}
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


# ================================================================ phase 7: D1 (the dossier)
#
# Stage 5a (DEC-228, DEC-138's method). The input: the bible past its 120-word
# cut, the world at B2's caps (an 80-word setting, 6 rules at 25 words, a
# 6-word period, 3 motifs -- uncapped, measured at 8 words), K1's text at its
# caps (a 60-character name and archetype, a 200-character one-line, 5 traits
# of 4 words, wants/fears/speech style at 25 French words, 5 relationships at 15
# words), the 11 other cast members (the cast block's cap less the one written)
# with 60-character names and 200-character one-lines, and a regenerate: the
# current dossier at its caps with D1's 3 relationships, and a 60-word note.
# (Not the season arc: at its caps it alone is ~1,430 tokens and took the
# worst case to 4,812, past the spec's 4,000 ceiling -- see build_d1.)
# Budget = worst case + 15 %, rounded up to ten.
#
# The reply: every stated limit of D1's ask hit in French (a 60-word backstory,
# goal/need/fears at 20, 2 secrets at 20, D1_RELATIONSHIPS_MAX (3)
# relationships of a 60-character name, a 30-word history and a 15-word now,
# voice patterns and vocabulary at 20, 2 catchphrases at 10, a 30-word arc),
# chars/4 x 1.3, + 15 %, rounded up to ten.

MEASURED_DOSSIER = {"D1": 3380}
MEASURED_DOSSIER_REPLY = 1116.7
FRENCH_TOKEN_FACTOR = 1.3


def _dossier_at_caps(relationships):
    return {"backstory": _fr(60), "goal": _fr(20), "need": _fr(20), "fears": _fr(20), "secrets": [_fr(20)] * 2,
            "relationships": relationships,
            "voice": {"patterns": _fr(20), "vocabulary": _fr(20), "catchphrases": [_fr(10)] * 2},
            "arc": _fr(30)}


def _d1():
    story = dict(STORY, world={"setting_summary": _fr(80), "rules": [_fr(25)] * 6, "time_period": _fr(6),
                               "recurring_motifs": [_fr(8)] * 3})
    pack = context.build_pack(language="fr", story=story, note=NOTE)
    character = {"name": _name(60), "role": "support", "archetype": _filler(8, 60), "one_line": _filler(30, 200),
                 "personality": {"traits": [_fr(4)] * 5, "wants": _fr(25), "fears": _fr(25),
                                 "speech_style": _fr(25)},
                 "relationships": {f"{_name(59)}{i}": _fr(15) for i in range(5)}}
    others = [{"name": _name(60), "role": "support", "one_line": _filler(30, 200)} for _ in range(11)]
    current = _dossier_at_caps([{"with": _name(60), "history": _fr(30), "now": _fr(15)}] * 3)
    return prompts.build_d1(pack, character=character, others=others,
                            regenerate={"field": "dossier", "current": current, "note": pack.note})


def test_dossier_worst_case_measures_what_is_recorded_and_fits_its_budget():
    worst = _tokens(_d1())
    assert worst == MEASURED_DOSSIER["D1"]
    budget = prompts.INPUT_BUDGET["D1"]
    assert budget == -(-round(worst * 1.15, 1) // 10) * 10
    _fits("D1", *_d1()[:2])


def test_the_largest_french_dossier_reply_fits_its_cap():
    import json

    from clipping.aistory import schemas

    reply = _dossier_at_caps([{"with": _name(60), "history": _fr(30), "now": _fr(15)}]
                             * schemas.D1_RELATIONSHIPS_MAX)
    assert schemas.d1_errors(reply) == []
    needed = context.estimate_tokens("", json.dumps(reply, ensure_ascii=False)) * FRENCH_TOKEN_FACTOR
    assert needed == pytest.approx(MEASURED_DOSSIER_REPLY, abs=0.05)
    cap = prompts.MAX_TOKENS["D1"]
    assert cap == -(-round(needed * 1.15, 1) // 10) * 10


# ================================================================ phase 7: D4/D5/D6 (the knowledge base)
#
# Stage 5b (DEC-228, DEC-138's method), each input at the cap its source sets,
# in French. D4: the bible past its cut, the world at B2's caps, 8 places
# (60-character names, 200-character one-lines, a 45-word descriptor the
# entity line clips to 12 words) and 8 props with a 60-character owner. D5:
# an arc entry with a 60-word summary and 3 + 3 hooks at 120 characters, the 8
# beats of the episode before at 25 words, 5 dossiers in short form at their
# caps (goal, need, 2 secrets, 3 relationships' "now"), 7 other names, 8
# places and 8 props (no bible or world: see build_d5). D6: the bible, 24 new
# objects (2 an episode over 12) each with its 25-word beat, 8 props named in
# all 12 episodes, 8 props in full and 12 names. Budget = worst case + 15 %,
# rounded up to ten.
#
# The replies: every stated limit of each ask hit in French (D4 a 60-word
# geography, 30-word period details, 4 motifs at 12 words; D5 8 beats of a
# 25-word what, a 60-character place, 4 who and 2 objects of 60 characters,
# 2 knows-after at 15 words; D6 5 kept and 3 new props of a 60-character
# name and owner and a 200-character one-line), chars/4 x 1.3, + 15 %,
# rounded up to ten.

MEASURED_KNOWLEDGE = {"D4": 1966, "D5": 3416, "D6": 3094}
MEASURED_KNOWLEDGE_REPLY = {"D4": 373.1, "D5": 2891.2, "D6": 465.4}


def _knowledge_story():
    return dict(STORY, world={"setting_summary": _fr(80), "rules": [_fr(25)] * 6, "time_period": _fr(6),
                              "recurring_motifs": [_fr(8)] * 3})


def _d4():
    pack = context.build_pack(language="fr", story=_knowledge_story())
    places = [{"name": _name(60), "one_line": _filler(30, 200), "descriptor": _at_density(45, _DESCRIPTOR_DENSITY)}
              for _ in range(8)]
    return prompts.build_d4(pack, places=places, props=[{"name": _name(60), "owner": _name(60)}] * 8)


def _d5():
    pack = context.build_pack(language="fr", story=_knowledge_story())
    entry = {"ep": 12, "function": "climax_and_reset", "summary": _at_density(60, LIVE_ARC_DENSITY),
             "open_hooks_in": [_filler(15, 120)] * 3, "open_hooks_out": [_filler(15, 120)] * 3, "characters": []}
    cast = [{"name": _name(60), "role": "support", "goal": _fr(20), "need": _fr(20), "secrets": [_fr(20)] * 2,
             "now": [{"with": _name(60), "now": _fr(15)}] * 3} for _ in range(5)]
    return prompts.build_d5(pack, ep=12, planned=12, entry=entry, previous=[_fr(25)] * 8, cast=cast,
                            others=[{"name": _name(60), "role": "recurring"}] * 7,
                            places=[{"name": _name(60), "one_line": _filler(30, 200)}] * 8,
                            props=[{"name": _name(60), "owner": _name(60)}] * 8)


def _d6():
    pack = context.build_pack(language="fr", story=_knowledge_story())
    objects = ([{"name": _name(60), "new": True, "episodes": [i // 2 + 1], "what": _fr(25)} for i in range(24)]
               + [{"name": _name(60), "new": False, "episodes": list(range(1, 13))}] * 8)
    props = [{"name": _name(60), "one_line": _filler(30, 200), "owner": _name(60)}] * 8
    return prompts.build_d6(pack, objects=objects, props=props, cast=[_name(60)] * 12)


_KNOWLEDGE_BUILDERS = {"D4": _d4, "D5": _d5, "D6": _d6}


def _knowledge_reply(prompt_id):
    if prompt_id == "D4":
        return {"geography": _fr(60), "period_details": _fr(30), "visual_motifs": [_fr(12)] * 4}
    if prompt_id == "D5":
        beat = {"what": _fr(25), "place": _name(60), "who": [_name(60)] * 4, "objects": [_name(60)] * 2,
                "knows_after": [{"who": _name(60), "knows": _fr(15)}] * 2}
        return {"beats": [beat] * 8}
    return {"keep": [_name(60)] * 5,
            "new_props": [{"name": _name(60), "one_line": _filler(20, 200), "owner": _name(60)}] * 3}


@pytest.mark.parametrize("prompt_id", ["D4", "D5", "D6"])
def test_knowledge_worst_cases_measure_what_is_recorded_and_fit_their_budgets(prompt_id):
    worst = _tokens(_KNOWLEDGE_BUILDERS[prompt_id]())
    assert worst == MEASURED_KNOWLEDGE[prompt_id]
    budget = prompts.INPUT_BUDGET[prompt_id]
    assert budget == -(-round(worst * 1.15, 1) // 10) * 10
    assert budget <= 4000  # the spec's ceiling
    _fits(prompt_id, *_KNOWLEDGE_BUILDERS[prompt_id]()[:2])


@pytest.mark.parametrize("prompt_id", ["D4", "D5", "D6"])
def test_the_largest_french_knowledge_reply_fits_its_cap(prompt_id):
    import json

    from clipping.aistory import schemas

    reply = _knowledge_reply(prompt_id)
    errors = {"D4": schemas.d4_errors, "D5": schemas.d5_errors, "D6": schemas.d6_errors}[prompt_id](reply)
    # D5's repeated filler names count as "named twice" in a beat; D6's too: only the counts and caps matter here.
    assert [error for error in errors if "twice" not in error] == []
    needed = context.estimate_tokens("", json.dumps(reply, ensure_ascii=False)) * FRENCH_TOKEN_FACTOR
    assert needed == pytest.approx(MEASURED_KNOWLEDGE_REPLY[prompt_id], abs=0.05)
    cap = prompts.MAX_TOKENS[prompt_id]
    assert cap == -(-round(needed * 1.15, 1) // 10) * 10


# ================================================================ phase 7: E1v2/E2v2/E3v2 (the context slices)
#
# Stage 5c (DEC-228, DEC-138's method). A v2 story's writing calls are their
# v1 worst cases above (episode 2, French, every input at its cap; E1 and E3
# with the continuity inputs) plus the context slice of what they write
# (context.slice_for_episode for E1v2, slice_for_scene for E2v2 and E3v2) and
# the v2 asks. The slice is built from this file's live-sized cast, places and
# prop with everything the knowledge base adds at its cap, in dense French: a
# dossier per character at every cap (2 secrets, a relationship with each of
# the others), a look whose wardrobe set has an 8-word context and 20 items, a
# place look at its caps (5 layout sides, the scale and the light at 15
# words, the prop as set dressing), 8 beats of 25 words in each of the two
# episodes with every character knowing a 15-word fact after each, and every
# character's ledger at its caps. The slice's own word caps bound it whatever
# the cast (context.SCENE_SLICE_MAX_WORDS / EPISODE_SLICE_MAX_WORDS); this
# fixture hits every one of them. E2v2 says the place by name only (the slice
# holds its layout and light). T1 v2's continuity block (context.slice_for_shot:
# each character's wardrobe set, who holds the prop) fits T1v2's own measured
# budget unchanged. Budget = worst case + 15 %, rounded up to ten.

from types import SimpleNamespace  # noqa: E402 -- this section's own names

# Phase 7 stage 6a (DEC-231): E3v2 re-pinned on purpose, 2,924 -> 2,939 (budget 3,370 -> 3,380): its hook
# ask now says the on-screen text is required whatever the hook style (story B shipped none).
# Phase 7 follow-up, stage G: E1v2 and E2v2 re-pinned on purpose, 2,490 -> 2,576 and 2,101 -> 2,187 (budgets
# 2,870 -> 2,970 and 2,420 -> 2,520): each opens with the first-watch rules (prompts.FIRST_WATCH_RULES).
MEASURED_SLICED = {"E1v2": 2576, "E2v2": 2187, "E3v2": 2939}
_PLACE_ID = LONGEST_PLACE["place_id"]


def _sliced_ec():
    def dossier(cid):
        return {"backstory": _fr(60), "goal": _fr(20), "need": _fr(20), "fears": _fr(20), "secrets": [_fr(20)] * 2,
                "relationships": [{"with": other, "history": _fr(30), "now": _fr(15)} for other in IDS if other != cid],
                "voice": {"patterns": _fr(20), "vocabulary": _fr(20), "catchphrases": [_fr(10)] * 2}, "arc": _fr(30)}

    look = {"build": _fr(15), "silhouette": _fr(12), "face": _fr(15), "hair": _fr(12), "skin_material": _fr(12),
            "height_cm": 170, "palette": [_fr(3)] * 4,
            "wardrobe_sets": [{"id": "daily", "context": _fr(8), "items": _fr(20)}], "season_change": None}
    characters = {c["char_id"]: dict(c, dossier=dossier(c["char_id"]), look=look, signature_items=[])
                  for c in CAST}
    places = {p["place_id"]: dict(p, descriptor=_fr(45), look={
        "layout_map": {key: _fr(15) for key in ("left", "right", "back", "foreground", "centre")},
        "scale_note": _fr(15), "lighting": {variant: _fr(15) for variant in p["time_variants"]},
        "props_here": [PROP["prop_id"]]}) for p in PLACES}
    beats = [{"what": _fr(25), "place_id": _PLACE_ID, "who": list(IDS), "objects": [PROP["prop_id"]],
              "knows_after": {cid: _fr(15) for cid in IDS}} for _ in range(8)]
    knowledge = {"timeline": [{"ep": 1, "beats": beats}, {"ep": 2, "beats": beats}],
                 "ledger_seed": {cid: {"location": _PLACE_ID, "wardrobe_set": "daily",
                                       "possessions": [PROP["prop_id"]], "injuries": _fr(10),
                                       "relationship_notes": _fr(20)} for cid in IDS}}
    ec = SimpleNamespace(ep=2, season=MEMORY, entities={"characters": characters, "places": places,
                                                         "props": {PROP["prop_id"]: PROP}})
    return ec, knowledge


def _e1v2():
    ec, knowledge = _sliced_ec()
    slice_text = context.slice_for_episode(ec, knowledge=knowledge, char_ids=IDS)
    return prompts.build_e1_v2(
        _pack(), ep=2, arc_entry=ARC2, template=TEMPLATE_90, episode_defaults=DEFAULTS,
        cast=[{"char_id": c["char_id"], "name": c["name"]} for c in CAST],
        places=[{k: p[k] for k in ("place_id", "name", "time_variants")} for p in PLACES],
        props=[{"prop_id": PROP["prop_id"], "name": PROP["name"]}], memory=MEMORY,
        slots=timing.episode_slots(TEMPLATE_90, 2), open_hooks=OPEN_HOOKS, audience_direction=DIRECTION_AT_CAP,
        slice_text=slice_text)


def _e2v2():
    ec, knowledge = _sliced_ec()
    pack = _pack(NOTE)
    return prompts.build_e2_v2(
        pack, scene=BODY, scene_number=11, outline=SCENES, previous=dict(LINE, summary=_fr(15)), word_budget=18,
        cast=PERSONALITIES, place={k: LONGEST_PLACE[k] for k in ("place_id", "name", "layout_notes")},
        props=[{"prop_id": PROP["prop_id"], "name": PROP["name"]}], sfx_cues=STYLE["audio"]["sfx_cues"],
        narrator_enabled=False, voice_direction=STYLE["audio"]["voice_direction"], note=pack.note,
        slice_text=context.slice_for_scene(ec, BODY, knowledge=knowledge))


def _e3v2(part):
    ec, knowledge = _sliced_ec()
    pack = _pack(None if part is None else NOTE)
    sliced = SCENES[-1] if part in (None, "cliffhanger", "teaser") else {"hook": SCENES[1], "recap": SCENES[0]}[part]
    return prompts.build_e3_v2(
        pack, ep=2, part=part, note=pack.note, hook_scene=SCENES[1], cliffhanger_scene=SCENES[-1],
        recap_scene=SCENES[0], outline=SCENES, first_body_line=LINE, last_body_line=LINE, arc_entry=ARC2,
        next_arc_entry=ARC3, memory=MEMORY, episode_defaults=DEFAULTS,
        word_budgets={"hook": 9, "cliffhanger": 12, "recap": 9}, cast=PERSONALITIES, narrator_enabled=False,
        open_hooks=OPEN_HOOKS[:E3_WORST_HOOKS], slice_text=context.slice_for_scene(ec, sliced, knowledge=knowledge))


def test_the_slices_of_the_v2_fixture_are_at_their_caps():
    ec, knowledge = _sliced_ec()
    scene_slice = context.slice_for_scene(ec, BODY, knowledge=knowledge)
    episode_slice = context.slice_for_episode(ec, knowledge=knowledge, char_ids=IDS)
    assert context.SCENE_SLICE_MAX_WORDS - 8 <= len(scene_slice.split()) <= context.SCENE_SLICE_MAX_WORDS
    assert context.EPISODE_SLICE_MAX_WORDS - 8 <= len(episode_slice.split()) <= context.EPISODE_SLICE_MAX_WORDS


@pytest.mark.parametrize("prompt_id", ["E1v2", "E2v2", "E3v2"])
def test_v2_writing_worst_cases_measure_what_is_recorded_and_fit_their_budgets(prompt_id):
    if prompt_id == "E3v2":
        worst = max(_tokens(_e3v2(part)) for part in (None, "hook", "cliffhanger", "recap", "teaser"))
        for part in (None, "hook", "cliffhanger", "recap", "teaser"):
            _fits("E3v2", *_e3v2(part)[:2])
    else:
        triple = {"E1v2": _e1v2, "E2v2": _e2v2}[prompt_id]()
        worst = _tokens(triple)
        _fits(prompt_id, *triple[:2])
    assert worst == MEASURED_SLICED[prompt_id]
    budget = prompts.INPUT_BUDGET[prompt_id]
    assert budget == -(-round(worst * 1.15, 1) // 10) * 10
    assert budget <= 4000  # the spec's ceiling
    v1 = prompts.INPUT_BUDGET[prompt_id[:2]]
    assert budget > v1  # the slice needs room of its own; the v1 rows are untouched (RC-M1)


def test_t1_v2_with_the_continuity_slice_fits_its_budget():
    ec, knowledge = _sliced_ec()
    ledger = context.ledger_before(knowledge, ec.season, ec.ep)
    continuity = context.slice_for_shot(ec, BODY, None, None, ledger=ledger)
    assert all(f"@{cid} wears" in continuity for cid in IDS) and f"%{PROP['prop_id']} is held by" in continuity
    previous = [{"framing": "medium_two_shot", "camera_motion": "push_in"},
                {"framing": "medium_single", "camera_motion": "push_in", "action": _fr(45),
                 "staging": _staging_at_caps()}]
    system, user, _schema = prompts.build_t1_v2(_pack(), previous_shots=previous, continuity=continuity,
                                                **_t1_v2_kwargs())
    assert continuity in user
    _fits("T1v2", system, user)


# ================================================================ phase 7: L1 (the continuity ledger)
#
# Stage 5d (DEC-229, DEC-138's method). The memory step's second call, after
# S3: the same 12-scene French script digest S3 reads (its own worst case,
# dominant here -- SCENES/NAMES/PLACES are this file's own, as S3's/E4's
# fixtures read them), 3 present characters (CAST) each with
# ``schemas.LOOK_WARDROBE_SETS_RANGE``'s max (3) wardrobe sets at their own
# 8-word context cap, and the ledger as it stood before the episode at the
# ledger's own caps (a 10-word injuries, a 20-word relationship_notes --
# ``schemas.LEDGER_INJURIES_MAX_WORDS`` / ``LEDGER_RELATIONSHIP_NOTES_MAX_WORDS``)
# for 4 places and 4 props -- the episode's own, bounded the way its places
# already are (``schemas.episode_script_context_errors``'s ``max_places``,
# 1-4); nothing bounds its props the same way, so 4 is 5d's own worst-case
# choice, mirrored here (``steps.memory._l1_known_ids``'s own docstring).
# Budget = worst case + 15 %, rounded up to ten.
#
# The reply: every character at every cap too (the ledger's own caps, 4
# possessions each -- the most the input above offers), chars/4 x 1.3, +
# 15 %, rounded up to ten.

MEASURED_L1 = 3407
MEASURED_L1_REPLY = 592.8
_L1_POOL = 4


def _l1_places_and_props():
    from clipping.aistory import schemas

    places = [{"place_id": f"place_{'x' * 39}{i}", "name": _filler(*LIVE_PLACES["place_la_piscine_de_la_trahison"]["name"])}
             for i in range(_L1_POOL)]
    props = [{"prop_id": f"prop_{'x' * 39}{i}", "name": _filler(*LIVE_PROP[1]["name"])} for i in range(_L1_POOL)]
    return places, props, schemas


def _l1_present(schemas):
    sets = [{"id": f"set{i}", "context": _fr(schemas.LOOK_WARDROBE_CONTEXT_MAX_WORDS)}
           for i in range(schemas.LOOK_WARDROBE_SETS_RANGE[1])]
    return [{"char_id": c["char_id"], "name": c["name"], "wardrobe_sets": sets} for c in CAST]


def _l1_previous(schemas, places, props):
    prop_ids = [p["prop_id"] for p in props]
    return {c["char_id"]: {
        "location": places[0]["place_id"], "wardrobe_set": "set0", "possessions": list(prop_ids),
        "injuries": _fr(schemas.LEDGER_INJURIES_MAX_WORDS),
        "relationship_notes": _fr(schemas.LEDGER_RELATIONSHIP_NOTES_MAX_WORDS),
    } for c in CAST}


def _l1_digest():
    return prompts.script_digest({"scenes": SCENES}, {"places": {p["place_id"]: p["name"] for p in PLACES},
                                                       "cast": NAMES})


def _l1():
    places, props, schemas = _l1_places_and_props()
    previous = _l1_previous(schemas, places, props)
    return prompts.build_l1(_pack(), ep=2, script_digest=_l1_digest(), previous=previous,
                            present=_l1_present(schemas), places=places, props=props)


def test_l1_worst_case_measures_what_is_recorded_and_fits_its_budget():
    worst = _tokens(_l1())
    assert worst == MEASURED_L1
    budget = prompts.INPUT_BUDGET["L1"]
    assert budget == -(-round(worst * 1.15, 1) // 10) * 10
    assert budget <= 4000  # the spec's ceiling
    _fits("L1", *_l1()[:2])


def test_the_largest_french_l1_reply_fits_its_cap():
    import json

    from clipping.aistory import schemas

    places, props, schemas = _l1_places_and_props()
    prop_ids = [p["prop_id"] for p in props]
    reply = {"ledger": [
        {"character": c["char_id"], "location": places[0]["place_id"], "wardrobe_set": "set0",
         "possessions": list(prop_ids), "injuries": _fr(schemas.LEDGER_INJURIES_MAX_WORDS),
         "relationship_notes": _fr(schemas.LEDGER_RELATIONSHIP_NOTES_MAX_WORDS)}
        for c in CAST
    ]}
    errors = schemas.l1_errors(reply, char_ids=[c["char_id"] for c in CAST],
                               place_ids=[p["place_id"] for p in places], prop_ids=prop_ids)
    assert errors == []
    needed = context.estimate_tokens("", json.dumps(reply, ensure_ascii=False)) * FRENCH_TOKEN_FACTOR
    assert needed == pytest.approx(MEASURED_L1_REPLY, abs=0.05)
    cap = prompts.MAX_TOKENS["L1"]
    assert cap == -(-round(needed * 1.15, 1) // 10) * 10


# ================================================================ phase 7: J1 and J2 (the judges)
#
# Stages 6a/6b (DEC-230, DEC-138's method). J1 reads what a first-time viewer
# would: the 12-scene French script digest E4 and S3 read (this file's own
# SCENES, its worst case), the previous episode's recap at its cap
# (schemas.RECAP_MAX_WORDS), the hook's on-screen text (6 words) and the
# cliffhanger's reveal (40 words) at theirs, and the objects block at its
# bound: E1 puts at most 4 props in a scene, so 48 mentions over 12 scenes,
# spread over the most props an episode can show -- the knowledge base's
# registry (schemas.KNOWLEDGE_PROPS_MAX, 8) plus E1v2's 2 new objects -- each
# a 60-character name. No bible, cast notes or memory: the viewer knows only
# the episode. Budget = worst case + 15 %, rounded up to ten.
#
# J1's reply: every stated limit hit in French (the three take-aways at 25,
# 30 and 25 words, 6 issues with a 30-word fix, the longest kind and scene
# id), chars/4 x 1.3, + 15 %, rounded up to ten.
#
# J2 (stage 6b; a vision call with no pack, so no INPUT_BUDGET entry, U1's
# precedent): its reply in English (3 missing items of 6 words, a 25-word
# continuity issue, words of 6 characters as D2/D3's English measures),
# chars/4, + 15 %, rounded up to ten, under the plan's 160. Its text at its
# own worst case (the brief at every cap of the shot it reads) is under the
# default pack budget (tests/test_story_keyframe_gate.py).

# J1 version 2 (DEC-248), re-pinned on purpose: the format sentence, the severities, a re-check's earlier
# issues (3195 -> 3463), and each issue's severity in the reply (795.6 -> 842.4).
MEASURED_J1 = 3463
MEASURED_J1_REPLY = 842.4
MEASURED_J2_REPLY = 91
_J1_PROPS = 10
_J1_PROPS_PER_SCENE = 4


def _j1():
    from clipping.aistory import schemas

    digest = prompts.script_digest({"scenes": SCENES}, {"places": {p["place_id"]: p["name"] for p in PLACES},
                                                         "cast": NAMES})
    names = [f"{_filler(8, 59)}{i}" for i in range(_J1_PROPS)]  # 60 characters, each its own
    shown = {}
    for i, scene in enumerate(SCENES):
        for k in range(_J1_PROPS_PER_SCENE):
            shown.setdefault(names[(i * _J1_PROPS_PER_SCENE + k) % _J1_PROPS], []).append(scene["scene_id"])
    objects = [(name, shown[name]) for name in names]
    assert sum(len(ids) for _name, ids in objects) == len(SCENES) * _J1_PROPS_PER_SCENE
    # J1 version 2 (DEC-248): the format sentence at its widest numbers and a re-check's earlier issues at
    # their bound, J1's longest kind each, their fixes cut to the re-check's cap.
    kind = max(schemas.FIRST_WATCH_ISSUE_KINDS, key=len)
    previous = [{"scene_id": "s11", "kind": kind, "fix": _fr(prompts.J1_FIX_MAX_WORDS)}] * prompts.J1_ISSUES_MAX
    return prompts.build_j1(_pack(), ep=2, script_digest=digest, objects=objects, hook_text=_fr(6),
                            reveal=_fr(40), previous_recap=_fr(schemas.RECAP_MAX_WORDS), seconds=999, words=9999,
                            previous_issues=previous)


def test_j1_worst_case_measures_what_is_recorded_and_fits_its_budget():
    worst = _tokens(_j1())
    assert worst == MEASURED_J1
    budget = prompts.INPUT_BUDGET["J1"]
    assert budget == -(-round(worst * 1.15, 1) // 10) * 10
    assert budget <= 4000  # the spec's ceiling
    _fits("J1", *_j1()[:2])


def test_the_largest_french_j1_reply_fits_its_cap():
    import json

    from clipping.aistory import schemas

    kind = max(schemas.FIRST_WATCH_ISSUE_KINDS, key=len)
    reply = {key: _fr(words) for key, words in prompts.J1_SUMMARY_MAX_WORDS.items()}
    reply.update(passed=False, issues=[{"scene_id": "s11", "kind": kind, "severity": "blocking",
                                        "fix": _fr(prompts.J1_FIX_MAX_WORDS)}] * prompts.J1_ISSUES_MAX)
    assert prompts.validate_j1(reply, scene_ids=[s["scene_id"] for s in SCENES]) == []
    needed = context.estimate_tokens("", json.dumps(reply, ensure_ascii=False)) * FRENCH_TOKEN_FACTOR
    assert needed == pytest.approx(MEASURED_J1_REPLY, abs=0.05)
    cap = prompts.MAX_TOKENS["J1"]
    assert cap == -(-round(needed * 1.15, 1) // 10) * 10
    assert prompts.TEMPERATURE["J1"] is prompts.ANALYTIC_TEMPERATURE


def _english(words):
    return " ".join(["abcdef"] * words)


def test_the_largest_j2_reply_fits_its_cap_under_the_plans_160():
    import json

    reply = {"shows_beat": False, "missing": [_english(prompts.J2_MISSING_MAX_WORDS)] * prompts.J2_MISSING_MAX,
             "continuity_issue": _english(prompts.J2_CONTINUITY_MAX_WORDS)}
    assert prompts.validate_j2(reply) == []
    needed = context.estimate_tokens("", json.dumps(reply, ensure_ascii=False))
    assert needed == pytest.approx(MEASURED_J2_REPLY, abs=0.05)
    cap = prompts.MAX_TOKENS["J2"]
    assert cap == -(-round(needed * 1.15, 1) // 10) * 10
    assert cap <= 160
    assert prompts.TEMPERATURE["J2"] is prompts.ANALYTIC_TEMPERATURE
    assert "J2" not in prompts.INPUT_BUDGET
