"""Writing v3 (plan 22 stage 3): E1v3, E2v3, E3v3 and J1v3.

The human (2026-10-04): the lines the characters say are "not well written,
not humanly understandable". The v2 asks were why: E2 asked for "1 to 4 short
spoken lines ... reference lines run 3-8 words", E1 for a 15-word summary
with no cause or effect, each E2 call saw only the previous scene's last
line, and nothing asked for complete sentences or the reason behind a
demand. A v2 story whose ``generation_profile.writing`` is "v3" writes on
new prompt ids instead:

- E1v3: the episode's spine first, then cause-and-effect summaries that
  retell its logline; the confrontation format's one continuous place;
- E2v3: every line so far (its last 220 words, the cut named), the spine,
  this scene's and the next scene's summary, and the line rule (complete
  sentences, one job each, the reason said);
- E3v3: the same, and on the confrontation format the cliffhanger's one line
  states the threatened act;
- J1v3: the spine in what it reads, three more kinds.

Pinned here: the four prompts on a small French fixture (goldens), the
dialogue-so-far cut, the spine's caps and schema, the validators, the
measured budgets and reply caps, and the selection -- v3 only with the
stamp; a story without it builds and sends exactly what it did (RC-W3).

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

import test_story_episode_prompt_budgets as budgets
import test_story_episode_steps as eps
from clipping.aistory import context, prompts, schemas, templates, timing
from test_story_episode_steps import hermetic, store  # noqa: F401 -- phase 3's fixtures, used as they are

CONFRONTATION = templates.load_episode_template("confrontation_50s_v2")
SERIAL_V2 = templates.load_episode_template("serial_60s_v2")
STYLE = templates.load_style("fruit_drama")
DEFAULTS = dict(STYLE["episode_defaults"])
MEMORY_NONE = {"series_memory": {"recaps": {}, "open_hooks": [], "relationship_state": {}, "introduced": {}}}
ARC = {"function": "setup", "summary": "Rouge coince Nude dans le hall et exige qu'elle ouvre son capuchon."}
CAST = [{"char_id": "char_rouge", "name": "Rouge"}, {"char_id": "char_nude", "name": "Nude"}]
PERSONALITIES = [
    {"char_id": "char_rouge", "name": "Rouge",
     "personality": {"wants": "rester la reine de l'académie", "fears": "perdre son rang",
                     "speech_style": "tranchante"}},
    {"char_id": "char_nude", "name": "Nude",
     "personality": {"wants": "passer inaperçue", "fears": "être exposée", "speech_style": "brève"}},
]
PLACES = [{"place_id": "place_hall", "name": "Le hall de l'académie", "time_variants": ["day"]}]
SPINE = {
    "logline": "Rouge accuse Nude de cacher sa couleur et jure de la démaquiller devant tous avant la fin du cours.",
    "want": "Rouge veut prouver que Nude est une imposture.",
    "obstacle": "Nude refuse de se découvrir.",
    "stakes": "Nude risque d'être exclue de l'académie.",
    "turn": "Rouge saisit le capuchon de Nude.",
}
OUTLINE = [
    {"scene_id": "s01", "function": "hook", "emotion": "tension", "characters": ["char_rouge", "char_nude"],
     "summary": "Rouge barre la route à Nude dans le hall, parce qu'elle ne l'a jamais vue ouvrir son capuchon."},
    {"scene_id": "s02", "function": "setup", "emotion": "angry", "characters": ["char_rouge", "char_nude"],
     "summary": "Rouge exige que Nude se découvre, car tout le monde ici montre sa couleur."},
    {"scene_id": "s03", "function": "rising", "emotion": "tension", "characters": ["char_rouge", "char_nude"],
     "summary": "Nude refuse; plus elle refuse, plus Rouge est sûre qu'elle cache quelque chose."},
    {"scene_id": "s04", "function": "cliffhanger", "emotion": "shocked", "characters": ["char_rouge", "char_nude"],
     "summary": "Rouge tend la main vers le capuchon et promet de le retirer devant tous."},
]
SO_FAR = [("s01", "Rouge", "Depuis ton inscription, tu n'as jamais osé ouvrir ton capuchon devant nous.")]


def _pack():
    return context.build_pack(language="fr", story={"logline": "Rouge règne sur l'académie des rouges à lèvres."})


def _sha(triple) -> str:
    return hashlib.sha256(json.dumps(list(triple), ensure_ascii=False).encode("utf-8")).hexdigest()


def _e1v3(template=CONFRONTATION, ep=1, **extra):
    defaults = dict(DEFAULTS, max_places=1 if template.get("single_place") else DEFAULTS["max_places"])
    kwargs = dict(ep=ep, arc_entry=ARC, template=template, episode_defaults=defaults, cast=CAST, places=PLACES,
                  props=[], memory=MEMORY_NONE, slots=timing.episode_slots(template, ep), slice_text="")
    kwargs.update(extra)
    return prompts.build_e1_v3(_pack(), **kwargs)


def _budget(native=True):
    return timing.word_budget_v3(dict(OUTLINE[1], target_duration_s=13.0), CONFRONTATION,
                                 CONFRONTATION["episode_words"], total_s=50.0, native=native)


def _e2v3(**extra):
    kwargs = dict(scene=OUTLINE[1], outline=OUTLINE, next_scene=OUTLINE[2], so_far=SO_FAR, spine=SPINE,
                  budget=_budget(), cast=PERSONALITIES, place={"name": "Le hall de l'académie"}, props=[],
                  sfx_cues=["gasp_crowd"], narrator_enabled=False, voice_direction="dramatic", slice_text="",
                  native=True)
    kwargs.update(extra)
    return prompts.build_e2_v3(_pack(), **kwargs)


def _e3v3(**extra):
    kwargs = dict(ep=1, hook_scene=OUTLINE[0], cliffhanger_scene=OUTLINE[3], recap_scene=None, outline=OUTLINE,
                  first_body_line={"speaker_name": "Rouge", "text": "Ouvre ton capuchon devant tout le monde."},
                  last_body_line={"speaker_name": "Rouge", "text": "Plus tu refuses, plus tu me prouves que tu mens."},
                  arc_entry=ARC, next_arc_entry=None, memory=MEMORY_NONE,
                  episode_defaults=dict(DEFAULTS, max_places=1), word_budgets={"hook": 15, "cliffhanger": 25},
                  cast=PERSONALITIES, narrator_enabled=False, slice_text="",
                  so_far=SO_FAR + [("s02", "Rouge", "Ouvre-le, puisque tout le monde ici montre sa couleur.")],
                  spine=SPINE, line_words=(5, 17), single_place=True, native=True)
    kwargs.update(extra)
    return prompts.build_e3_v3(_pack(), **kwargs)


def _j1v3(**extra):
    kwargs = dict(ep=1, script_digest=("Scene s01 (hook) -- Le hall, day -- characters: Rouge, Nude\n"
                                       "Rouge barre la route."),
                  objects=[], hook_text="Elle cache sa couleur", reveal="Rouge arrache le capuchon.", spine=SPINE,
                  seconds=50, words=110)
    kwargs.update(extra)
    return prompts.build_j1_v3(_pack(), **kwargs)


# ================================================================ the goldens

# Plan 24 stage 2 (2026-10-05), re-pinned on purpose (RC-M1): E2v3 and E3v3
# are pinned as the script step now sends them -- with the scene's line plan
# (the seconds, each line's hard cap, the hard total; the framing parts'
# seconds and caps). The prompts built without a plan (any caller that hands
# none in) keep their former bytes, pinned under "-unplanned" with the shas
# E2v3/E3v3 had before this stage.
GOLDENS = {
    "E1v3": "0579bd35879fad084e44d23dfc2b0a54d405fb2bbd26be515393f16eb50a6c8a",
    "E2v3": "213cdce7bff6e0b6f2cdb91015b05222b141e3375d64edd505a09fd2fe27e0a6",
    "E3v3": "5c63d51131b3b9c0293308dca082d1818dd38ab57e29ff05682240a3b0098f05",
    "J1v3": "59b7355c21d68a8dab52178333b4fdc1fc210551918b3be5f1335412a2e23eaf",
    "E2v3-unplanned": "bf903d5c575481093415141036757c13e93fafcf1fee0e7a3aae7bb84ab8c3a9",
    "E3v3-unplanned": "6b066de95e1c58626c88e3261741b5a070291b6af0457d60184c3d00334426cb",
}


def _fixture_plan(scene):
    """The fixture scene's line plan on the confrontation format (native
    speech, no narrator, Veo's 4/6/8 s clips)."""
    return timing.scene_plan(CONFRONTATION, scene, lang="fr", native=True, narrator=False,
                             speakers={"char_rouge": None, "char_nude": None},
                             tail_floor=timing.plan_tail_floor(CONFRONTATION))


def _planned_e2v3():
    plan = _fixture_plan(OUTLINE[1])
    return _e2v3(plan=plan, budget=timing.plan_budget(plan, line_lo=5))


def _planned_e3v3():
    plans = {"hook": _fixture_plan(OUTLINE[0]), "cliffhanger": _fixture_plan(OUTLINE[3])}
    return _e3v3(plans=plans, word_budgets={key: plan["max_words"] for key, plan in plans.items()})


def test_the_v3_prompts_are_byte_identical_to_their_goldens():
    """Each v3 prompt on the small French fixture, pinned whole (system,
    user, schema): a wording change is a decision, re-pinned on purpose."""
    built = {"E1v3": _e1v3(), "E2v3": _planned_e2v3(), "E3v3": _planned_e3v3(), "J1v3": _j1v3(),
             "E2v3-unplanned": _e2v3(), "E3v3-unplanned": _e3v3()}
    assert {prompt_id: _sha(triple) for prompt_id, triple in built.items()} == GOLDENS


def test_e1v3_asks_the_spine_first_and_cause_and_effect_summaries():
    system, user, schema = _e1v3()
    assert user.startswith(prompts.FIRST_WATCH_RULES)
    ask = user.split("Write the beat sheet for episode 1: first its spine, then its scenes.", 1)[1]
    assert ask.index("- spine:") < ask.index("- title:") < ask.index("- scenes:")
    for line in ("  - logline: one complete sentence, at most 30 words: who wants what, what they do, and where it "
                 "leaves them",
                 "  - want: what the main character wants in this episode, at most 15 words",
                 "  - obstacle: who or what stands in their way, at most 15 words",
                 "  - stakes: what they lose if they fail, at most 15 words",
                 "  - turn: the moment the episode turns, at most 20 words",
                 "- summary: one or two complete sentences, at most 30 words, saying what happens and why — what it "
                 "follows from, what is done, what it changes for the next scene. Never a mood, a title or a list.",
                 "Read in order, the summaries retell spine.logline."):
        assert line in ask, line
    assert "at most 15 words\n- emotion" not in ask  # the v2 summary ask is gone
    assert list(schema["properties"]) == ["spine", "title", "scenes"]
    assert list(schema["properties"]["spine"]["properties"]) == list(schemas.SPINE_KEYS)
    assert schema["properties"]["spine"]["required"] == list(schemas.SPINE_KEYS)


def test_e1v3_tells_the_confrontation_format_one_continuous_place():
    _s, user, _ = _e1v3()
    assert "- place_id: one of the existing places, the same one in every scene\n" in user
    assert ("The episode is one continuous scene in one place, in real time; the scenes are its beats, cut together "
            "with no time jump. The antagonist drives it with demands and accusations, each with its reason; the "
            "target answers little; it ends right before the threatened act happens.") in user
    assert "one of them may be a quiet scene with no dialogue" not in user
    # Any other format: its places cap and the quiet scene, no confrontation paragraph.
    _s, other, _ = _e1v3(template=SERIAL_V2)
    assert "at most 2 distinct places across the whole episode" in other
    assert "one continuous scene in one place" not in other
    assert "one of them may be a quiet scene with no dialogue" in other


def test_e2v3_reads_every_line_so_far_the_spine_and_the_next_scene():
    _s, user, schema = _e2v3()
    assert user.startswith(prompts.FIRST_WATCH_RULES)
    assert prompts.spine_block(SPINE) in user
    assert ("The episode's lines so far, in order:\n- s01 Rouge: Depuis ton inscription, tu n'as jamais osé ouvrir "
            "ton capuchon devant nous.") in user
    assert ("This scene (s02, setup, emotion: angry): Rouge exige que Nude se découvre, car tout le monde ici montre "
            "sa couleur.\nNext scene (s03, rising, emotion: tension): Nude refuse;") in user
    assert "Previous scene:" not in user and "Episode outline:" not in user
    assert ("- lines: 2 to 3 lines, each with speaker (one of char_rouge, char_nude), text, emotion (one of ") in user
    assert ("Each text is one or two complete sentences in French, 5 to 17 words, that this person would say aloud "
            "right now.") in user
    assert prompts.LINE_RULE_V3 in user
    for clause in ("a demand, an accusation, a fact the viewer did not know, a refusal, a threat or a reveal",
                   "Say the reason behind every demand or accusation in the line itself ('because…', 'since you…', "
                   "'the more you…, the more…')",
                   "No filler (no lone 'Quoi ?', 'Écoute', a name alone)", "no stage directions in the text"):
        assert clause in prompts.LINE_RULE_V3
    assert "Each line is spoken on camera by its speaker in one shot of at most 8 seconds: at most 17 words." in user
    assert "Write 25-32 words of dialogue in total" in user
    assert "short" not in user.split("Write this scene's dialogue.", 1)[1]
    assert schema["properties"]["lines"]["description"] == "2-3 lines"
    # Not a native-speech story: no one-shot sentence, the default 5-22 words.
    _s, plain, _ = _e2v3(native=False, budget=_budget(native=False))
    assert "one shot of at most 8 seconds" not in plain and "5 to 17 words" in plain  # the template's own line_words


def test_the_dialogue_so_far_keeps_the_last_220_words_and_names_the_cut():
    lines = [(f"s{i:02d}", "Rouge", " ".join([f"mot{i}"] * 22)) for i in range(15)]
    block = prompts.dialogue_so_far(lines)
    kept = block.split("\n")[1:]
    assert len(kept) == 10 and kept[0].startswith("- s05 Rouge: mot5") and kept[-1].startswith("- s14 Rouge:")
    assert block.startswith("The episode's lines so far, in order (the first 5 lines left out: only the last 220 "
                            "words are shown):")
    assert sum(len(line.split(": ", 1)[1].split()) for line in kept) == 220
    assert prompts.dialogue_so_far(lines[:3]).startswith("The episode's lines so far, in order:\n")
    assert prompts.dialogue_so_far([]) == "The episode's lines so far: none yet -- this scene speaks first."
    # One line longer than the cap is still shown: the last line always is.
    assert "mot0" in prompts.dialogue_so_far([("s01", "Rouge", " ".join(["mot0"] * 300))])


def test_e3v3_asks_the_threatened_act_last_on_the_confrontation_format():
    _s, user, schema = _e3v3()
    assert user.startswith(prompts.spine_block(SPINE))
    assert "Episode outline:" not in user  # the spine stands in for it
    assert "The episode's lines so far, in order:\n- s01 Rouge:" in user
    assert prompts.CLIFFHANGER_ACT_V3 in user
    assert ("- cliffhanger: reveal (story language, at most 40 words) and lines (exactly 1: the antagonist's last "
            "line, which states the act they are about to do -- the episode ends right before it happens)") in user
    assert ("Each line: speaker (one of char_rouge, char_nude), text (one or two complete sentences in French, 5 to 17 "
            "words, that this person would say aloud right now)") in user
    assert prompts.LINE_RULE_V3 in user
    assert list(schema["properties"]) == ["hook", "cliffhanger", "teaser"]
    # Another format: 0-1 cliffhanger lines, no act sentence.
    _s, other, _ = _e3v3(single_place=False, line_words=(5, 22), native=False)
    assert prompts.CLIFFHANGER_ACT_V3 not in other and "- cliffhanger: reveal (story language, at most 40 words) " \
        "and lines (0-1)" in other
    # The narrator offered in some parts only is told where it may speak.
    _s, recap, _ = _e3v3(ep=2, recap_scene=dict(OUTLINE[0], scene_id="s00", function="recap"), narrator_enabled=True,
                         narrator_parts=["recap"], memory={"series_memory": dict(MEMORY_NONE["series_memory"],
                                                                                 recaps={"ep01": "Rouge a vu Nude."})})
    assert "The narrator speaks only in the recap." in recap


def test_j1v3_reads_the_spine_and_asks_the_three_new_kinds():
    _s, user, schema = _j1v3()
    assert ("What the episode sets out to tell (its spine):\n- logline: Rouge accuse Nude de cacher sa couleur") in user
    assert user.index("its spine") < user.index("Scene s01 (hook)")
    for line in ("- line_no_progress: a line adds nothing: no new fact, demand, refusal, threat or reveal",
                 "- incomplete_sentence: a line is a fragment, not a sentence a person would say",
                 "- logline_mismatch: the scenes do not tell the episode's logline"):
        assert line in user
    kinds = schema["properties"]["issues"]["items"]["properties"]["kind"]["enum"]
    assert kinds == list(schemas.FIRST_WATCH_ISSUE_KINDS) + ["line_no_progress", "incomplete_sentence",
                                                             "logline_mismatch"]
    assert prompts.J1_V3_PROMPT_VERSION == 3 and prompts.J1_PROMPT_VERSION == 2
    # J1v2 is unchanged: its six kinds, no spine.
    v2 = prompts.build_j1(_pack(), ep=1, script_digest="x", objects=[], hook_text=None, reveal=None)
    assert "line_no_progress" not in v2[1] and v2[2]["properties"]["issues"]["items"]["properties"]["kind"]["enum"] \
        == list(schemas.FIRST_WATCH_ISSUE_KINDS)


def test_the_narrated_template_on_v3_asks_complete_lines_never_short_punches():
    narration = prompts.narration_of(templates.load_episode_template("narrated_drama_60s_v2"))
    e1 = prompts.narration_e1_line_v3(narration)
    e2 = prompts.narration_e2_line_v3(narration)
    for line in (e1, e2):
        assert "the characters speak 2–4 complete lines in the whole episode" in line
        assert "short" not in line and "punch" not in line
    assert e2 in _e2v3(narration=narration)[1]
    assert e1 in _e1v3(template=templates.load_episode_template("narrated_drama_60s_v2"), narration=narration)[1]


# ================================================================ validators

def _e1v3_reply():
    def stub(function, summary):
        return {"function": function, "place_id": "place_hall", "time_variant": "day",
                "characters": ["char_rouge", "char_nude"], "props": [], "summary": summary, "emotion": "tension",
                "target_duration_s": {"hook": 6.0, "cliffhanger": 8.0}.get(function, 12.0)}

    return {"spine": dict(SPINE), "title": "Le capuchon de Nude",
            "scenes": [stub("hook", OUTLINE[0]["summary"]), stub("setup", OUTLINE[1]["summary"]),
                       stub("rising", OUTLINE[2]["summary"]),
                       stub("peak", "Rouge la menace devant toute l'académie réunie dans le hall, parce que son "
                                    "silence obstiné vaut pour elle un aveu."),
                       stub("cliffhanger", OUTLINE[3]["summary"])]}


def _check_e1(reply, template=CONFRONTATION, places=None):
    return prompts.validate_e1_v3(reply, ep=1, template=template, episode_defaults=dict(DEFAULTS, max_places=1),
                                  cast_ids=["char_rouge", "char_nude"],
                                  places=places or {"place_hall": ["day"]}, prop_ids=[])


def test_validate_e1v3_caps_the_spine_and_takes_30_word_summaries():
    reply = _e1v3_reply()
    assert len(reply["scenes"][3]["summary"].split()) > 15
    assert _check_e1(reply) == []
    long_spine = copy.deepcopy(reply)
    long_spine["spine"]["logline"] = " ".join(["mot"] * 31)
    long_spine["spine"]["turn"] = " ".join(["mot"] * 21)
    assert _check_e1(long_spine) == ["$.spine.logline: 31 words, expected at most 30",
                                     "$.spine.turn: 21 words, expected at most 20"]
    long_summary = copy.deepcopy(reply)
    long_summary["scenes"][1]["summary"] = " ".join(["mot"] * 31)
    assert _check_e1(long_summary) == ["$.scenes[1].summary: 31 words, expected at most 30"]
    no_spine = {key: value for key, value in reply.items() if key != "spine"}
    assert any("spine" in error for error in _check_e1(no_spine))
    # E1v2 keeps its 15-word cap (RC-W3).
    v2 = {key: value for key, value in reply.items() if key != "spine"}
    assert any(".summary: " in error for error in prompts.validate_e1(
        v2, ep=1, template=CONFRONTATION, episode_defaults=dict(DEFAULTS, max_places=1),
        cast_ids=["char_rouge", "char_nude"], places={"place_hall": ["day"]}, prop_ids=[], v2=True))


def test_validate_e1v3_holds_the_confrontation_to_one_place():
    reply = _e1v3_reply()
    reply["scenes"][2]["place_id"] = "place_cour"
    errors = _check_e1(reply, places={"place_hall": ["day"], "place_cour": ["day"]})
    assert errors == ["$.scenes: 2 distinct place(s), more than max_places (1)"]


def _line(speaker, text):
    return {"speaker": speaker, "text": text, "emotion": "angry", "delivery": "cold and slow"}


def _e2_reply(*lines):
    return {"lines": list(lines), "sfx_cues": [], "on_screen_text": None}


def _check_e2(reply, *, floor=5, native=True, narrator=False, episode_lines=()):
    return prompts.validate_e2_v3(reply, scene=OUTLINE[1], narrator_enabled=narrator, sfx_cues=["gasp_crowd"],
                                  budget=_budget(native), floor=floor, episode_lines=episode_lines)


GOOD_A = "Ouvre ton capuchon tout de suite, puisque tout le monde ici montre sa couleur."
GOOD_B = "Plus tu refuses, plus je suis sûre que tu caches quelque chose de honteux."


def test_validate_e2v3_takes_complete_lines_inside_the_line_words():
    assert _check_e2(_e2_reply(_line("char_rouge", GOOD_A), _line("char_rouge", GOOD_B))) == []


def test_validate_e2v3_refuses_a_line_one_shot_cannot_speak_and_a_fragment():
    long = " ".join(["mot"] * 18)
    errors = _check_e2(_e2_reply(_line("char_rouge", GOOD_A), _line("char_rouge", long)))
    assert "$.lines[1].text: 18 words, expected at most 17 (one line, one shot of at most 8 s)" in errors
    errors = _check_e2(_e2_reply(_line("char_rouge", GOOD_A), _line("char_nude", "Non, jamais.")))
    assert ("$.lines[1].text: 2 words, expected at least 5: a line is one or two complete sentences a person would "
            "say, never a fragment") in errors
    # Without a template line_words floor the floor is 3 words: a 3-word line is a sentence, a 2-word one not.
    assert not any("expected at least" in e for e in _check_e2(_e2_reply(_line("char_rouge", GOOD_A),
                                                                          _line("char_nude", "Je refuse, Rouge.")),
                                                                floor=3))
    assert any("expected at least 3" in e for e in _check_e2(_e2_reply(_line("char_rouge", GOOD_A),
                                                                       _line("char_nude", "Jamais, Rouge.")), floor=3))


def test_validate_e2v3_refuses_a_line_repeating_an_earlier_one_accents_and_case_folded():
    earlier = "Ouvre ton capuchon tout de suite, puisque tout le monde ici montre sa couleur."
    again = "OUVRE ton capuchon tout de suite, puisque tout le monde ici montre sa couleur !"
    errors = _check_e2(_e2_reply(_line("char_rouge", again), _line("char_rouge", GOOD_B)), episode_lines=[earlier])
    assert any(error.startswith(f"$.lines[0].text: {prompts.DUPLICATE_LINE_PREFIX}") for error in errors)


def test_validate_e2v3_keeps_the_narrators_22_words():
    narration = " ".join(["mot"] * 20)
    reply = _e2_reply(_line("char_rouge", GOOD_A), _line("narrator", narration))
    assert _check_e2(reply, narrator=True) == []
    reply = _e2_reply(_line("char_rouge", GOOD_A), _line("narrator", " ".join(["mot"] * 23)))
    assert "$.lines[1].text: 23 words, expected at most 22" in _check_e2(reply, narrator=True)


def test_validate_e3v3_wants_the_cliffhangers_one_line_on_the_confrontation_format():
    reply = {"hook": {"lines": [_line("char_rouge", "Toi, la nouvelle, tu ne passeras pas sans ouvrir ton capuchon.")],
                      "on_screen_text": "Elle cache sa couleur"},
             "cliffhanger": {"reveal": "Rouge arrache le capuchon.", "lines": []},
             "teaser": "Demain, toute l'académie saura."}
    kwargs = dict(ep=1, part=None, hook_scene=OUTLINE[0], cliffhanger_scene=OUTLINE[3], recap_scene=None,
                  narrator_enabled=False, episode_defaults=dict(DEFAULTS, hook_style="text_overlay"),
                  line_words=(5, 17), floor=5)
    errors = prompts.validate_e3_v3(reply, single_place=True, **kwargs)
    assert errors == ["$.cliffhanger.lines: exactly 1 line on this format -- the antagonist's last, stating the act "
                      "they are about to do"]
    assert prompts.validate_e3_v3(reply, single_place=False, **kwargs) == []
    reply["cliffhanger"]["lines"] = [_line("char_rouge", "Maintenant, je vais retirer ton capuchon devant toute "
                                                         "l'académie.")]
    assert prompts.validate_e3_v3(reply, single_place=True, **kwargs) == []


def test_validate_j1v3_takes_the_new_kinds_and_j1v2_does_not():
    reply = dict(eps.J1_PASSED, passed=False, issues=[
        {"scene_id": "s02", "kind": "line_no_progress", "severity": "blocking", "fix": "Dire ce que Rouge exige."}])
    assert prompts.validate_j1_v3(reply, scene_ids=["s01", "s02"]) == []
    assert any("is not one of" in error for error in prompts.validate_j1(reply, scene_ids=["s01", "s02"]))


# ================================================================ plan 24 stage 2: seconds and hard caps
#
# D-3/D-4: the writer is told the scene's seconds and each line's hard cap
# from the line plan (``timing.scene_plan``); a longer line or scene is
# refused, the error naming the line, its words, its cap and its seconds.

NARRATED = templates.load_episode_template("narrated_drama_60s_v2")
# e7412a3efcc6's shape: a 13 s body scene, native speech, an Edge narrator, a Gemini character.
S02 = dict(OUTLINE[1], characters=["char_rouge"])
HOOK = dict(OUTLINE[0], characters=["char_rouge"])


def _s02_plan():
    return timing.scene_plan(NARRATED, S02, lang="fr", native=True, narrator_provider="edge",
                             speakers={"char_rouge": "gemini"}, tail_floor=timing.plan_tail_floor(NARRATED))


def _hook_plan():
    return timing.scene_plan(NARRATED, HOOK, lang="fr", native=True, narrator_provider="edge",
                             speakers={"char_rouge": "gemini"}, tail_floor=timing.plan_tail_floor(NARRATED))


def _words(n, tag):
    return " ".join(f"{tag}{k}" for k in range(n))


def test_the_e2v3_prompt_of_a_13s_native_scene_names_its_seconds_each_lines_cap_and_the_total():
    plan = _s02_plan()
    assert [line["max_words"] for line in plan["lines"]] == [11, 12] and plan["max_words"] == 23
    _s, user, schema = _e2v3(scene=S02, plan=plan, budget=timing.plan_budget(plan, line_lo=5),
                             cast=PERSONALITIES[:1], narrator_enabled=True)
    block = ("This scene lasts at most 13 s.\n"
             "Line 1 (narrator): at most 11 words, heard over one 6 s shot.\n"
             "Line 2 (Rouge): at most 12 words, spoken in one 6 s shot.\n"
             "Hard limits: 23 words in total; a longer line is refused.\n"
             "Write at least 11 and at most 23 words of dialogue in total.")
    assert block in user
    assert "not fewer than" not in user and "one shot of at most 8 seconds" not in user
    assert "- lines: 2 lines, each with speaker (one of char_rouge, narrator)" in user
    assert "Each text is one or two complete sentences in French, 5 to 12 words" in user
    assert prompts.LINE_RULE_V3 in user


def _check_planned(*lines, plan=None):
    plan = plan or _s02_plan()
    return prompts.validate_e2_v3(_e2_reply(*lines), scene=S02, narrator_enabled=True, sfx_cues=["gasp_crowd"],
                                  budget=timing.plan_budget(plan, line_lo=5), floor=5, plan=plan)


def test_validate_e2v3_refuses_a_line_or_a_scene_over_its_plan_and_takes_one_exactly_at_its_caps():
    assert _check_planned(_line("narrator", _words(11, "n")), _line("char_rouge", _words(12, "c"))) == []
    errors = _check_planned(_line("narrator", _words(11, "n")), _line("char_rouge", _words(15, "c")))
    assert errors[0] == "$.lines[1].text: 15 words, at most 12 (a 6 s shot)"
    errors = _check_planned(_line("narrator", _words(18, "n")), _line("char_rouge", _words(12, "c")))
    assert errors[:2] == ["$.lines[0].text: 18 words, at most 11 (a 6 s shot)",
                          "$.lines: 30 words in total, at most 23 (a 13 s scene)"]
    # A line the plan has no room for is refused, naming the count.
    errors = _check_planned(_line("narrator", _words(5, "n")), _line("char_rouge", _words(5, "c")),
                            _line("char_rouge", _words(5, "d")))
    assert "$.lines[2]: one character line too many: this scene's plan holds 1 character line" in errors
    # Under: the floor error keeps its prefix (the script step's under-only leniency reads it).
    errors = _check_planned(_line("char_rouge", _words(6, "c")))
    assert errors == [f"{prompts.E2_WORD_FLOOR_PREFIX}: 6 in total, expected at least 11 (half of the 23-word plan)"]
    # Without a plan the validator keeps its band (a legacy caller).
    assert _check_e2(_e2_reply(_line("char_rouge", GOOD_A), _line("char_rouge", GOOD_B))) == []


def test_e3v3_names_the_hooks_seconds_and_refuses_a_hook_over_its_cap():
    plan = _hook_plan()
    assert plan["max_words"] == 10 and plan["slot_s"][1] == 6.0
    _s, user, _ = _e3v3(hook_scene=HOOK, narrator_enabled=True, plans={"hook": plan})
    assert "The hook lasts at most 6 s: at most 10 words." in user
    assert "Keep the hook's dialogue within" not in user
    reply = {"hook": {"lines": [_line("narrator", _words(14, "h"))], "on_screen_text": "Elle cache sa couleur"},
             "cliffhanger": {"reveal": "Rouge arrache le capuchon.",
                             "lines": [_line("char_rouge", "Maintenant, je vais retirer ton capuchon devant toute "
                                                           "l'académie.")]},
             "teaser": "Demain, toute l'académie saura."}
    kwargs = dict(ep=1, part=None, hook_scene=HOOK, cliffhanger_scene=OUTLINE[3], recap_scene=None,
                  narrator_enabled=True, episode_defaults=dict(DEFAULTS, hook_style="text_overlay"),
                  line_words=(5, 17), floor=5, single_place=True, plans={"hook": plan})
    errors = prompts.validate_e3_v3(reply, **kwargs)
    assert errors[0] == "$.hook.lines: 14 words in total, at most 10 (a 6 s hook)"
    reply["hook"]["lines"] = [_line("narrator", _words(10, "h"))]
    assert prompts.validate_e3_v3(reply, **kwargs) == []


def test_a_word_cap_error_is_told_whole_and_first_on_the_retry():
    from clipping.aistory.steps import llm_call, script as script_step

    errors = _check_planned(_line("narrator", _words(18, "n")), _line("char_rouge", _words(12, "c")))
    assert script_step.over_cap_errors(errors) == errors[:2]
    assert script_step.over_cap_errors([f"{prompts.E2_WORD_FLOOR_PREFIX}: 6 in total"]) == []
    retry = llm_call.refused_prompt("USER", errors)
    assert retry.startswith("USER\n\nYour previous reply was refused: $.lines[0].text: 18 words, at most 11 (a 6 s "
                            "shot); $.lines: 30 words in total, at most 23 (a 13 s scene)")


# ================================================================ the script document

def test_the_spine_is_an_optional_key_of_the_script_and_capped():
    import test_story_episode_schemas as episode_schemas

    doc = episode_schemas._script()
    assert "spine" not in doc and schemas.episode_script_errors(doc) == []  # RC-M3: stored scripts unchanged
    assert schemas.episode_script_errors(dict(doc, spine=dict(SPINE))) == []
    errors = schemas.episode_script_errors(dict(doc, spine={k: v for k, v in SPINE.items() if k != "turn"}))
    assert any("turn" in error for error in errors)
    errors = schemas.episode_script_errors(dict(doc, spine=dict(SPINE, logline="x" * 301)))
    assert any("maxLength 300" in error for error in errors)
    # A v3 script (it has a spine) summarises a scene in up to 30 words; any other keeps 15.
    thirty = " ".join(["mot"] * 30)
    v3 = dict(copy.deepcopy(doc), spine=dict(SPINE))
    v3["scenes"][1]["summary"] = thirty
    assert schemas.episode_script_errors(v3) == []
    v3["scenes"][1]["summary"] = thirty + " encore"
    assert schemas.episode_script_errors(v3) == ["$.scenes[s02].summary: 31 words, expected at most 30"]
    doc["scenes"][1]["summary"] = " ".join(["mot"] * 16)
    assert schemas.episode_script_errors(doc) == ["$.scenes[s02].summary: 16 words, expected at most 15"]
    doc["scenes"][1]["summary"] = "Something happens."
    # The widened 400-character cap.
    doc["scenes"][0]["summary"] = "é" * 400
    assert schemas.episode_script_errors(doc) == []
    doc["scenes"][0]["summary"] = "é" * 401
    assert any("maxLength 400" in error for error in schemas.episode_script_errors(doc))


# ================================================================ budgets (DEC-138's method)
#
# The v2 worst cases of tests/test_story_episode_prompt_budgets.py (episode 2,
# French, 12 scenes on the 90 s format, every input at its cap, the slices,
# a 60-word note) with what v3 adds at its caps: the spine, 30-word summaries,
# 40 lines of 22 words before the scene (the dialogue so far cut to 220
# words, the cut named), the next scene, the native-speech and narrated lines,
# the narrator offered. Budget = worst case + 15 %, rounded up to ten.

# Plan 24 stage 2 (2026-10-05): E2v3 2,628 -> 2,705 and E3v3 3,475 -> 3,488, planned (_worst_plan below).
MEASURED_V3 = {"E1v3": 2807, "E2v3": 2705, "E3v3": 3488, "J1v3": 4083}
MEASURED_V3_REPLY = {"E1v3": 2232.1, "E1v3-payoff": 2770.3, "J1v3": 854.1}
SPINE_AT_CAPS = {key: budgets._fr(words) for key, words in prompts.SPINE_MAX_WORDS.items()}
SCENES_V3 = [dict(scene, summary=budgets._fr(prompts.SUMMARY_V3_MAX_WORDS)) for scene in budgets.SCENES]
SO_FAR_AT_CAPS = [(f"s{i:02d}", budgets.CAST[i % 3]["name"], budgets._fr(22)) for i in range(40)]
NARRATION = prompts.narration_of(templates.load_episode_template("narrated_drama_60s_v2"))
PARTS = (None, "hook", "cliffhanger", "recap", "teaser")


def _round_budget(measured) -> int:
    return -(-round(measured * 1.15, 1) // 10) * 10


def _tokens(triple) -> int:
    return context.estimate_tokens(triple[0], triple[1])


def _worst_e1v3(template):
    ec, knowledge = budgets._sliced_ec()
    return prompts.build_e1_v3(
        budgets._pack(), ep=2, arc_entry=budgets.ARC2, template=template, episode_defaults=budgets.DEFAULTS,
        cast=[{"char_id": c["char_id"], "name": c["name"]} for c in budgets.CAST],
        places=[{k: p[k] for k in ("place_id", "name", "time_variants")} for p in budgets.PLACES],
        props=[{"prop_id": budgets.PROP["prop_id"], "name": budgets.PROP["name"]}], memory=budgets.MEMORY,
        slots=timing.episode_slots(template, 2), open_hooks=budgets.OPEN_HOOKS,
        audience_direction=budgets.DIRECTION_AT_CAP,
        slice_text=context.slice_for_episode(ec, knowledge=knowledge, char_ids=budgets.IDS), narration=NARRATION)


# Plan 24 stage 2 (2026-10-05), re-measured on purpose: E2v3/E3v3 are sent
# with the scene's line plan (``timing.scene_plan``), so the worst cases are
# planned too -- four lines, the narrator's at 22 words over an 8 s shot, each
# character line the longest name at 17 words in an 8 s shot, a 12.5 s scene;
# the framing parts' seconds and caps in place of "Keep ... within N words".

def _worst_plan(narrator, *, words=None, hi=12.5):
    longest = max((c["char_id"] for c in budgets.PERSONALITIES),
                  key=lambda cid: len(next(c["name"] for c in budgets.PERSONALITIES if c["char_id"] == cid)))
    lines = [{"kind": "narrator", "speaker": "narrator", "seconds": 7.3, "clip_s": 8, "max_words": 22}] if narrator \
        else []
    lines += [{"kind": "character", "speaker": longest, "seconds": 8.0, "clip_s": 8, "max_words": 17}
              for _ in range(4 - len(lines))]
    total = words or sum(line["max_words"] for line in lines)
    return {"slot_s": [3.0, hi], "allowed_speech_s": 11.0, "lines": lines, "max_words": total,
            "min_words": total // 2}


def _worst_e2v3(narrator, native):
    ec, knowledge = budgets._sliced_ec()
    pack = budgets._pack(budgets.NOTE)
    body = SCENES_V3[10]
    plan = _worst_plan(narrator)
    return prompts.build_e2_v3(
        pack, scene=body, outline=SCENES_V3, next_scene=SCENES_V3[11], so_far=SO_FAR_AT_CAPS, spine=SPINE_AT_CAPS,
        budget={"words": [plan["min_words"], plan["max_words"]], "lines": [4, 4], "line_words": [5, 22]},
        cast=budgets.PERSONALITIES, plan=plan,
        place={k: budgets.LONGEST_PLACE[k] for k in ("place_id", "name", "layout_notes")},
        props=[{"prop_id": budgets.PROP["prop_id"], "name": budgets.PROP["name"]}],
        sfx_cues=budgets.STYLE["audio"]["sfx_cues"], narrator_enabled=narrator,
        voice_direction=budgets.STYLE["audio"]["voice_direction"], note=pack.note,
        slice_text=context.slice_for_scene(ec, body, knowledge=knowledge), native=native, narration=NARRATION)


def _worst_e3v3(part, single_place):
    ec, knowledge = budgets._sliced_ec()
    pack = budgets._pack(None if part is None else budgets.NOTE)
    sliced = SCENES_V3[-1] if part in (None, "cliffhanger", "teaser") else {"hook": SCENES_V3[1],
                                                                           "recap": SCENES_V3[0]}[part]
    return prompts.build_e3_v3(
        pack, ep=2, part=part, note=pack.note, hook_scene=SCENES_V3[1], cliffhanger_scene=SCENES_V3[-1],
        recap_scene=SCENES_V3[0], outline=SCENES_V3, first_body_line=budgets.LINE, last_body_line=budgets.LINE,
        arc_entry=budgets.ARC2, next_arc_entry=budgets.ARC3, memory=budgets.MEMORY, episode_defaults=budgets.DEFAULTS,
        word_budgets={"hook": 19, "cliffhanger": 24, "recap": 9}, cast=budgets.PERSONALITIES, narrator_enabled=True,
        plans={key: _worst_plan(True, words=words) for key, words in (("hook", 19), ("cliffhanger", 24),
                                                                       ("recap", 9))},
        open_hooks=budgets.OPEN_HOOKS[:budgets.E3_WORST_HOOKS],
        slice_text=context.slice_for_scene(ec, sliced, knowledge=knowledge), so_far=SO_FAR_AT_CAPS,
        spine=SPINE_AT_CAPS, line_words=(5, 22), single_place=single_place, native=True, narrator_parts=["recap"])


def _worst_j1v3():
    digest = prompts.script_digest({"scenes": SCENES_V3}, {"places": {p["place_id"]: p["name"] for p in budgets.PLACES},
                                                            "cast": budgets.NAMES})
    names = [f"{budgets._filler(8, 59)}{i}" for i in range(10)]
    shown = {}
    for i, scene in enumerate(SCENES_V3):
        for k in range(4):
            shown.setdefault(names[(i * 4 + k) % 10], []).append(scene["scene_id"])
    kind = max(prompts.J1_V3_KINDS, key=len)
    previous = [{"scene_id": "s11", "kind": kind, "fix": budgets._fr(prompts.J1_FIX_MAX_WORDS)}] * prompts.J1_ISSUES_MAX
    return prompts.build_j1_v3(budgets._pack(), ep=2, script_digest=digest, objects=[(n, shown[n]) for n in names],
                               hook_text=budgets._fr(6), reveal=budgets._fr(40), spine=SPINE_AT_CAPS,
                               previous_recap=budgets._fr(schemas.RECAP_MAX_WORDS), seconds=999, words=9999,
                               previous_issues=previous)


def test_the_v3_worst_cases_measure_what_is_recorded_and_fit_their_budgets():
    worst = {
        "E1v3": max(_tokens(_worst_e1v3(budgets.TEMPLATE_90)), _tokens(_worst_e1v3(CONFRONTATION))),
        "E2v3": max(_tokens(_worst_e2v3(narrator, native)) for narrator in (True, False) for native in (True, False)),
        "E3v3": max(_tokens(_worst_e3v3(part, single)) for part in PARTS for single in (True, False)),
        "J1v3": _tokens(_worst_j1v3()),
    }
    assert worst == MEASURED_V3
    for prompt_id, measured in worst.items():
        assert prompts.input_budget(prompt_id) == _round_budget(measured) == prompts.WRITING_V3_INPUT_BUDGET[prompt_id]
        assert prompt_id not in prompts.INPUT_BUDGET  # the v1/v2 registry's rows are untouched (RC-M1)
    # Each builds under its budget (the step's own check, before any call).
    context.check_budget(*_worst_e1v3(budgets.TEMPLATE_90)[:2], budget=prompts.input_budget("E1v3"))
    for part in PARTS:
        context.check_budget(*_worst_e3v3(part, True)[:2], budget=prompts.input_budget("E3v3"))
    context.check_budget(*_worst_j1v3()[:2], budget=prompts.input_budget("J1v3"))
    # Each is over its v2 twin's: the spine and the dialogue so far need room of their own.
    for prompt_id in ("E1v3", "E2v3", "E3v3", "J1v3"):
        assert prompts.input_budget(prompt_id) > prompts.input_budget(prompt_id.replace("v3", "v2") if prompt_id !=
                                                                       "J1v3" else "J1")


def test_the_largest_french_v3_replies_fit_their_caps():
    import test_story_prompts_episode as episode

    new_objects = [{"name": episode._fr_words(4), "one_line": episode._fr_words(15), "owner_char_id": "char_broccolia"},
                   {"name": "vérité " + episode._fr_words(3), "one_line": episode._fr_words(15),
                    "owner_char_id": "char_mangella"}]
    reply = dict(episode._largest_e1_reply(), new_objects=new_objects,
                 spine={key: episode._fr_words(words) for key, words in prompts.SPINE_MAX_WORDS.items()})
    reply["scenes"] = [dict(scene, summary=episode._fr_words(prompts.SUMMARY_V3_MAX_WORDS))
                       for scene in reply["scenes"]]
    check = dict(ep=2, template=episode.TEMPLATE_90, episode_defaults=episode.EPISODE_DEFAULTS,
                 cast_ids=episode.CAST_IDS, places=episode.PLACES_DICT, prop_ids=["prop_phone"])
    assert prompts.validate_e1_v3(reply, **check) == []
    hooks = [episode._fr_hook(i) for i in range(schemas.PAYOFF_HOOKS_MAX)]
    payoff = dict(reply, scenes=[dict(scene, pays_off=[max(hooks, key=len)]) for scene in reply["scenes"]])
    assert prompts.validate_e1_v3(payoff, open_hooks=hooks, **check) == []
    kind = max(prompts.J1_V3_KINDS, key=len)
    j1 = {key: budgets._fr(words) for key, words in prompts.J1_SUMMARY_MAX_WORDS.items()}
    j1.update(passed=False, issues=[{"scene_id": "s11", "kind": kind, "severity": "blocking",
                                     "fix": budgets._fr(prompts.J1_FIX_MAX_WORDS)}] * prompts.J1_ISSUES_MAX)
    assert prompts.validate_j1_v3(j1, scene_ids=[scene["scene_id"] for scene in budgets.SCENES]) == []
    for key, doc, cap in (("E1v3", reply, prompts.MAX_TOKENS["E1v3"]),
                          ("E1v3-payoff", payoff, prompts.E1V3_PAYOFF_MAX_TOKENS),
                          ("J1v3", j1, prompts.MAX_TOKENS["J1v3"])):
        needed = context.estimate_tokens("", json.dumps(doc, ensure_ascii=False)) * episode.FRENCH_TOKEN_FACTOR
        assert needed == pytest.approx(MEASURED_V3_REPLY[key], abs=0.05), key
        assert cap == _round_budget(needed), key
    # E2v3/E3v3 reply in E2's/E3's shape, lines no longer than theirs: E3's cap, the plan's 700 for E2v3.
    assert prompts.MAX_TOKENS["E3v3"] == prompts.MAX_TOKENS["E3"] and prompts.MAX_TOKENS["E2v3"] == 700
    for prompt_id in ("E1v3", "E2v3", "E3v3"):
        assert prompts.TEMPERATURE[prompt_id] is prompts.WRITING_TEMPERATURE
        assert prompt_id in prompts.PREMIUM_PROMPT_IDS
    assert prompts.TEMPERATURE["J1v3"] is prompts.ANALYTIC_TEMPERATURE and "J1v3" in prompts.PREMIUM_PROMPT_IDS


# ================================================================ the selection (RC-W3)

def _v3_story(store, writing="v3"):
    return eps._ready_story(store, v2=True, writing=writing)


def _tag(call, label):
    stub = call["user"].split(f"{label} (", 1)[1].split("\n", 1)[0]
    return hashlib.sha256(stub.encode("utf-8")).hexdigest()[:6]


def e2_v3_reply(call):
    """An E2v3 reply: two complete lines of the scene's own (tagged from
    its "This scene (" line, so no line repeats another of the episode).

    Plan 24 stage 2 (2026-10-05), re-pinned on purpose: the fixture's
    serial_60s_v1 body scene (8 s, two Edge lines) plans 7 words a line, a
    hard cap now -- the first line drops its "gamma" word (8 -> 7)."""
    tag = _tag(call, "This scene")
    speakers = eps._speakers(call)
    return {"lines": [{"speaker": speakers[0], "text": f"Tu mens alpha{tag} beta{tag} depuis ton arrivée.",
                       "emotion": "angry", "delivery": "cold"},
                      {"speaker": speakers[-1], "text": f"Je refuse epsilon{tag} zeta{tag} eta{tag}.",
                       "emotion": "tension", "delivery": "quiet"}],
            "sfx_cues": [], "on_screen_text": None}


E1V3_REPLY = dict(copy.deepcopy(eps.E1_REPLY), spine=dict(SPINE))
# A v3 summary says what happens and why, past E1v2's 15 words.
E1V3_REPLY["scenes"][1]["summary"] = ("Kiwilo propose à Mangella une alliance secrète, parce qu'il sait que le "
                                      "téléphone annoncera bientôt un vote contre eux deux.")


def _v3_llm():
    return eps.FakeLLM(E1v3=[E1V3_REPLY], E3v3=[eps.E3_FULL], E4=[eps.E4_PASSED],
                       default={"E2v3": e2_v3_reply, "J1v3": eps.J1_PASSED})


def test_a_v3_story_writes_and_judges_on_the_v3_prompts_and_keeps_the_spine(store):
    story_id = _v3_story(store)
    llm = _v3_llm()
    summary, _log = eps._run(eps._new().script, store, story_id, llm=llm)
    used = set(llm.prompts())
    assert {"E1v3", "E2v3", "E3v3", "J1v3"} <= used and not used & {"E1v2", "E2v2", "E3v2", "J1", "E1", "E2", "E3"}
    script = eps._script(store, story_id)
    assert script["spine"] == SPINE and len(script["scenes"][1]["summary"].split()) > 15
    assert script["first_watch"]["version"] == 3 and summary["first_watch"] is True
    # Every E2v3 call read the episode's lines before its own scene and the spine.
    calls = llm.of("E2v3")
    assert "The episode's lines so far: none yet" in calls[0]["user"]
    assert "The episode's lines so far, in order:\n- s02 " in calls[1]["user"]
    assert all(prompts.spine_block(SPINE) in call["user"] for call in calls)
    j1_spine = prompts.spine_block(SPINE).replace("The episode's spine:", "What the episode sets out to tell (its spine):")
    assert j1_spine in llm.of("J1v3")[0]["user"]


def test_the_repair_pass_rewrites_a_scene_j1v3_finds_a_line_adding_nothing_in(store):
    """DEC-245/260: a blocking issue of a v3 kind is repaired like the
    others -- its scene written again on E2v3 with the kind in words and the
    fix as the note -- and J1v3 checks again."""
    story_id = _v3_story(store)
    issue = {"scene_id": "s03", "kind": "line_no_progress", "severity": "blocking",
             "fix": "Mangella doit dire ce qu'elle exige de Broccolia et pourquoi."}
    llm = eps.FakeLLM(E1v3=[E1V3_REPLY], E3v3=[eps.E3_FULL], E4=[eps.E4_PASSED, eps.E4_PASSED],
                      J1v3=[dict(eps.J1_PASSED, passed=False, issues=[issue])],
                      default={"E2v3": e2_v3_reply, "J1v3": eps.J1_PASSED})
    summary, _log = eps._run(eps._new().script, store, story_id, llm=llm)
    repaired = [call for call in llm.of("E2v3") if "Follow the author's note: " in call["user"]]
    assert len(repaired) == 1 and "This scene (s03," in repaired[0]["user"]
    assert ("Follow the author's note: First-watch check -- Line adds nothing: Mangella doit dire ce qu'elle exige "
            "de Broccolia et pourquoi.") in repaired[0]["user"]
    assert summary["repairs"][0]["scenes"] == [{"scene_id": "s03", "kinds": ["line_no_progress"], "part": None}]
    assert len(llm.of("J1v3")) == 2 and summary["first_watch"] is True


def test_a_story_without_the_stamp_builds_e1v2_byte_identical(store):
    """RC-W3: a v2 story without ``writing: v3`` (no key, or "v2") sends
    E1v2 exactly as the v2 builder makes it, and judges on J1 version 2."""
    for writing in (None, "v2"):
        story_id = _v3_story(store, writing=writing)
        llm = eps._script_llm(v2=True, E4=[eps.E4_PASSED])
        eps._run(eps._new().script, store, story_id, llm=llm)
        assert "E1v2" in llm.prompts() and not any(p.endswith("v3") for p in llm.prompts())
        call = llm.of("E1v2")[0]
        from clipping.aistory.steps import episode_common, script as script_step

        ctx, _log = eps._ctx(store, story_id)
        ec = episode_common.load_episode_context(ctx)
        cast = script_step.e1_cast(ec)
        expected = prompts.build_e1_v2(
            context.build_pack(language=ec.language, story=ec.story),
            ep=1, arc_entry=ec.arc_entry, template=ec.template, episode_defaults=ec.episode_defaults,
            cast=[{"char_id": doc["char_id"], "name": doc["name"]} for doc in cast],
            places=[{"place_id": pid, "name": ec.entities["places"][pid]["name"], "time_variants": variants}
                    for pid, variants in ec.places.items()],
            props=[{"prop_id": pid, "name": ec.entities["props"][pid]["name"]} for pid in ec.prop_ids],
            memory=ec.season, slots=timing.episode_slots(ec.template, 1), open_hooks=[], audience_direction=None,
            slice_text=context.slice_for_episode(ec, knowledge=script_step.knowledge_of(ec),
                                                 char_ids=[doc["char_id"] for doc in cast]),
            narration=prompts.narration_of(ec.template, ec.narrator))
        assert (call["system"], call["user"]) == expected[:2]
        assert eps._script(store, story_id)["first_watch"]["version"] == 2
        assert "spine" not in eps._script(store, story_id)


def test_an_episode_begun_before_the_stamp_is_finished_and_judged_on_v2(store):
    """An episode beat-sheeted on E1v2 (no spine) whose story turns v3
    keeps the prompts and the judge it began with: E2v2/E3v2, J1 version 2,
    its version-2 report not judged again."""
    story_id = _v3_story(store, writing="v2")
    eps._run(eps._new().script, store, story_id, llm=eps._script_llm(v2=True, E4=[eps.E4_PASSED]))
    store.update(story_id, lambda doc: doc["generation_profile"].update(writing="v3"), now=eps.NOW)
    from clipping.aistory.steps import judge

    script = eps._script(store, story_id)
    story = store.get(story_id)
    assert judge.j1_version(story, script) == 2 and not judge.needs_first_watch(script, judge.j1_version(story, script))
    assert judge.j1_version(story) == 3  # a new beat sheet would be v3
    llm = eps._script_llm(v2=True)
    eps._run(eps._new().script, store, story_id, llm=llm)
    assert llm.prompts() == []  # complete and judged: nothing to write or check


# ================================================================ the dashboard

def test_the_script_pane_shows_the_spine_as_what_happens_above_the_scenes():
    """``ScriptPane.jsx`` (text contract, DEC-012): the spine's logline as
    "What happens" (the kit's Card, CardHeader and CardBody), the want, the
    stakes and the turn beneath; nothing for a script with no spine."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "web" / "dashboard" / "src" / "pages" / "story" / "episode"
           / "ScriptPane.jsx").read_text(encoding="utf-8")
    card = src.split("function SpineCard({ spine }) {", 1)[1].split("\n}\n", 1)[0]
    assert "if (!spine) return null" in card
    assert '<CardHeader icon={BookOpen} title="What happens" subtitle={spine.logline} />' in card
    assert "<Card className=\"story-script-spine\">" in card and "<CardBody>" in card
    assert "const SPINE_ROWS = [['want', 'Wants'], ['stakes', 'At stake'], ['turn', 'Turn']]" in src
    assert "<dd>{spine[key]}</dd>" in card
    body = src.split("export default function ScriptPane(", 1)[1]
    assert body.index("<SpineCard spine={script.spine} />") < body.index("{script.scenes.map((scene) => (")
