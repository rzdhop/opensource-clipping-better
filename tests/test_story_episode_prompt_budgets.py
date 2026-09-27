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
MEASURED = {"E1": 1102, "E2": 1440, "E3": 2050, "E4": 3523, "T1": 1100, "T1r": 1218}


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
