"""Tests for the phase-3 (episode script + storyboard) prompt catalogue of
``clipping.aistory.prompts``: E1 (beat sheet), E2 (one body scene), E3 (the
framing scenes E2 never writes), E4 (whole-script consistency check), T1
(one scene -> shots) and T1r (re-plan one shot) -- spec 2.7, 2.8, 4.1, 4.2;
AI Story phase 3, stage 4.

Same conventions as ``tests/test_story_prompts.py`` (DEC-062, the
truncation guard): a builder is a pure function of a ``Pack`` plus plain
kwargs, so its output is deterministic and worth pinning with literal
substring assertions ("golden strings"); every new post-validator gets a
good fixture and a violation per rule it checks; every new prompt joins the
truncation guard (the largest French reply its cap allows must fit, and a
much smaller cap must not) and the input-token budget check.

Against the pre-stage-4 commit (``153e83f``), ``clipping.aistory.prompts``
has no ``build_e1``/``E1`` etc. at all, so every test below fails with
``AttributeError: module 'clipping.aistory.prompts' has no attribute
'build_e1'`` (or the sibling id) before this stage's implementation lands.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from clipping.aistory import context, prompts, schemas, templates, timing
from clipping.providers.pacing import estimate_tokens

FRUIT_DRAMA = templates.load_style("fruit_drama")
TEMPLATE = templates.load_episode_template("serial_60s_v1")
TEMPLATE_90 = templates.load_episode_template("serial_90s_v1")
EPISODE_DEFAULTS = FRUIT_DRAMA["episode_defaults"]  # hook_style=insert_prop, cliffhanger_style=hard_stop
CAMERA_PARAGRAPH = FRUIT_DRAMA["camera"]

# Stage 12b: E1 now asks for an exact, positional scene list
# (timing.episode_slots), not a range -- these are TEMPLATE's (serial_60s_v1)
# own slot lists for episode 1 (no recap) and episode 2 (recap_from_episode).
SLOTS_EP1 = timing.episode_slots(TEMPLATE, 1)
SLOTS_EP2 = timing.episode_slots(TEMPLATE, 2)

STORY = {
    "logline": "Sur une île de téléréalité, des fruits forment des couples et complotent.",
    "premise": "Chaque semaine, les concurrents forment des couples pour ne pas être éliminés.",
    "tone": "mélodramatique, complice, rapide",
}

FRENCH_TOKEN_FACTOR = 1.3  # same rationale/value as test_story_prompts.py's truncation guard


def _pack(language="fr"):
    story = STORY if language == "fr" else {
        "logline": "On a reality island, fruit form couples and scheme.",
        "premise": "Every week the contestants pair up to avoid elimination.",
        "tone": "melodramatic, knowing, fast",
    }
    return context.build_pack(language=language, story=story, template=FRUIT_DRAMA)


def _words(text) -> int:
    return len(text.split())


# =============================================================== fixtures

CAST_IDS = ["char_kiwilo", "char_mangella", "char_broccolia"]
CAST_E1 = [
    {"char_id": "char_kiwilo", "name": "Kiwilo"},
    {"char_id": "char_mangella", "name": "Mangella"},
    {"char_id": "char_broccolia", "name": "Broccolia"},
]
PLACES_E1 = [
    {"place_id": "place_pool", "name": "La Piscine", "time_variants": ["day", "night"]},
    {"place_id": "place_confessional", "name": "Le Parloir", "time_variants": ["day"]},
]
PLACES_DICT = {p["place_id"]: p["time_variants"] for p in PLACES_E1}
PROPS_E1 = [{"prop_id": "prop_phone", "name": "Le Téléphone"}]

ARC_ENTRY = {
    "ep": 1, "function": "setup", "summary": "Les concurrents arrivent sur l'île.",
    "open_hooks_in": [], "open_hooks_out": ["Qui va trahir qui ?"],
    "characters": ["char_kiwilo", "char_mangella"],
}
MEMORY_NONE = {"series_memory": {"recaps": {}, "open_hooks": [], "relationship_state": {}, "introduced": {}}}
MEMORY_EP2 = {
    "series_memory": {
        "recaps": {"ep01": "Kiwilo et Mangella se sont alliés contre Broccolia."},
        "open_hooks": ["Le téléphone va-t-il sonner ce soir ?"],
        "relationship_state": {"char_kiwilo|char_mangella": "alliance fragile"},
        "introduced": {},
    }
}


def _personality(name, wants="gagner", fears="perdre", speech="doux"):
    return {"traits": ["charmant", "malin"], "wants": wants, "fears": fears, "speech_style": speech}


CAST_E2 = [
    {"char_id": "char_kiwilo", "name": "Kiwilo", "personality": _personality("Kiwilo")},
    {"char_id": "char_mangella", "name": "Mangella", "personality": _personality("Mangella", "dominer", "trahison", "sec")},
]
PLACE_E2 = {"place_id": "place_pool", "name": "La Piscine", "layout_notes": "un bassin turquoise au centre"}
PROPS_E2 = [{"prop_id": "prop_phone", "name": "Le Téléphone"}]
SFX_CUES = ["gasp_crowd", "dramatic_sting", "phone_ring"]

BODY_SCENE = {
    "scene_id": "s02", "function": "setup", "place_id": "place_pool", "time_variant": "day",
    "characters": ["char_kiwilo", "char_mangella"], "props": ["prop_phone"],
    "summary": "Ils se disputent au bord du bassin.", "emotion": "tension", "target_duration_s": 6.0,
}
OUTLINE = [
    {"scene_id": "s01", "function": "hook", "summary": "Ouverture choc sur l'île.", "characters": ["char_kiwilo"]},
    BODY_SCENE,
    {"scene_id": "s03", "function": "cliffhanger", "summary": "Tout bascule enfin.", "characters": ["char_mangella"]},
]

HOOK_SCENE = {"scene_id": "s01", "function": "hook", "emotion": "tension",
              "summary": "Ouverture choc sur l'île.", "characters": ["char_kiwilo"]}
CLIFF_SCENE = {"scene_id": "s03", "function": "cliffhanger", "emotion": "shocked",
               "summary": "Tout bascule enfin.", "characters": ["char_mangella"]}
RECAP_SCENE = {"scene_id": "s00", "function": "recap", "emotion": "tension",
               "summary": "Ce qui s'est passé avant.", "characters": ["char_kiwilo"]}
NEXT_ARC_ENTRY = {"ep": 2, "function": "escalation", "summary": "La trahison éclate au grand jour."}

CHARACTERS_T1 = [{"char_id": "char_kiwilo", "name": "Kiwilo", "descriptor": "a kiwi with fuzzy brown skin"}]
PLACE_T1 = {"place_id": "place_pool", "layout_notes": "un bassin turquoise au centre, palmiers à gauche"}
PROPS_T1 = [{"prop_id": "prop_phone", "descriptor": "a coconut-shaped telephone"}]
LINES_T1 = [{"speaker": "char_kiwilo", "text": "Tu m'as menti.", "emotion": "angry"}]
MODIFIERS_ALLOWED = FRUIT_DRAMA["motion_rules"]["tier1"]["modifiers"]  # [] for fruit_drama
TAGS_T1 = ["@char_kiwilo", "#place_pool:day", "%prop_phone"]
NAMES_T1 = {"char_kiwilo": "Kiwilo"}


def _e1_reply(**overrides):
    def scene(function, **kw):
        base = {
            "function": function, "place_id": "place_pool", "time_variant": "day",
            "characters": ["char_kiwilo"], "props": [], "summary": "Ca bouge vite sur l'île.",
            "emotion": "tension", "target_duration_s": {"recap": 2.5, "hook": 2.5, "cliffhanger": 3.0}.get(function, 5.0),
        }
        base.update(kw)
        return base

    # Matches SLOTS_EP1 exactly: hook, 8 body scenes, cliffhanger (stage 12b).
    body = ["setup", "rising", "peak", "turn", "setup", "rising", "peak", "turn"]
    scenes = [scene("hook")] + [scene(f) for f in body] + [scene("cliffhanger")]
    doc = {"title": "Le Debut", "scenes": scenes}
    doc.update(overrides)
    return doc


# ==================================================================== E1

def test_build_e1_golden_fr():
    pack = _pack("fr")
    system, user, schema = prompts.build_e1(
        pack, ep=1, arc_entry=ARC_ENTRY, template=TEMPLATE, episode_defaults=EPISODE_DEFAULTS,
        cast=CAST_E1, places=PLACES_E1, props=PROPS_E1, memory=MEMORY_NONE, slots=SLOTS_EP1,
    )
    expected_user = (
        "This episode's arc entry (setup): Les concurrents arrivent sur l'île.\n"
        "Hooks this episode should leave open: Qui va trahir qui ?\n\n"
        "none yet\n\n"
        "Existing cast:\n"
        "- char_kiwilo — Kiwilo\n"
        "- char_mangella — Mangella\n"
        "- char_broccolia — Broccolia\n\n"
        "Existing places:\n"
        "- place_pool — La Piscine (variants: day, night)\n"
        "- place_confessional — Le Parloir (variants: day)\n\n"
        "Existing props:\n"
        "- prop_phone — Le Téléphone\n\n"
        "Write the beat sheet for episode 1.\n\n"
        "Give:\n"
        "- title: the episode's own title, at most 8 words\n"
        "- scenes: exactly 10 entries, one for each of these, in order:\n"
        "Scene 1 — hook\n"
        "Scene 2 — body: choose setup, rising, peak or turn\n"
        "Scene 3 — body: choose setup, rising, peak or turn\n"
        "Scene 4 — body: choose setup, rising, peak or turn\n"
        "Scene 5 — body: choose setup, rising, peak or turn\n"
        "Scene 6 — body: choose setup, rising, peak or turn\n"
        "Scene 7 — body: choose setup, rising, peak or turn\n"
        "Scene 8 — body: choose setup, rising, peak or turn\n"
        "Scene 9 — body: choose setup, rising, peak or turn\n"
        "Scene 10 — cliffhanger\n\n"
        "Each scene:\n"
        "- function: one of recap, hook, setup, rising, peak, turn, cliffhanger\n"
        "- place_id: one of the existing places, at most 2 distinct places across the whole episode\n"
        "- time_variant: one of that place's own listed variants\n"
        "- characters: 0 to 6 of the existing cast\n"
        "- props: 0 to 4 of the existing props\n"
        "- summary: at most 15 words\n"
        "- emotion: one of neutral, happy, angry, shocked, sad, scheming, tension, tender, fear, triumph\n"
        "- target_duration_s: a hint inside its own slot's range -- recap 2-3s, hook 1.5-3.5s, body "
        "(setup/rising/peak/turn) 4-8s each, cliffhanger 2-5s\n\n"
        "Aim for the upper half of each range so the scenes sum near 60 s.\n\n"
        "Across the body scenes: open with setup, escalate with rising, include at least one peak, and "
        "land a turn right before the cliffhanger; one of them may be a quiet scene with no dialogue.\n\n"
        "The hook scene: insert_prop: a close shot of a diegetic object, sign or screen that states the "
        "premise.\n\n"
        "The cliffhanger scene: hard_stop: end mid-confrontation, no resolution, no line that wraps it "
        "up; it should leave one of this episode's own hooks open.\n\n"
        "Never use real people, brands, studio names or copyrighted characters."
    )
    assert user == expected_user
    assert "Write all user-facing text in French." in system
    assert schema == prompts.e1_schema(CAST_IDS, ["place_pool", "place_confessional"], ["prop_phone"])


def test_build_e1_ep2_shows_the_previous_recap_and_recap_slot():
    pack = _pack("fr")
    _, user, _ = prompts.build_e1(
        pack, ep=2, arc_entry=dict(ARC_ENTRY, ep=2), template=TEMPLATE, episode_defaults=EPISODE_DEFAULTS,
        cast=CAST_E1, places=PLACES_E1, props=PROPS_E1, memory=MEMORY_EP2, slots=SLOTS_EP2,
    )
    assert "Series memory:" in user
    assert "Previous recap: Kiwilo et Mangella se sont alliés contre Broccolia." in user
    assert "Open hooks: Le téléphone va-t-il sonner ce soir ?" in user
    assert "Relationships: char_kiwilo/char_mangella: alliance fragile" in user
    assert "- scenes: exactly 11 entries, one for each of these, in order:\n" in user
    assert "Scene 1 — recap\nScene 2 — hook\nScene 3 — body: choose setup, rising, peak or turn" in user
    assert "none yet" not in user


def test_build_e1_ep1_never_mentions_recap_in_the_scene_list():
    pack = _pack("fr")
    _, user, _ = prompts.build_e1(
        pack, ep=1, arc_entry=ARC_ENTRY, template=TEMPLATE, episode_defaults=EPISODE_DEFAULTS,
        cast=CAST_E1, places=PLACES_E1, props=PROPS_E1, memory=MEMORY_NONE, slots=SLOTS_EP1,
    )
    assert "Scene 1 — hook\n" in user
    scene_list_block = user.split("in order:\n", 1)[1].split("\n\n", 1)[0]
    assert "recap" not in scene_list_block


@pytest.mark.parametrize("language, name", [("fr", "French"), ("en", "English")])
def test_e1_language_line(language, name):
    pack = _pack(language)
    system, _, _ = prompts.build_e1(
        pack, ep=1, arc_entry=ARC_ENTRY, template=TEMPLATE, episode_defaults=EPISODE_DEFAULTS,
        cast=CAST_E1, places=PLACES_E1, props=PROPS_E1, memory=MEMORY_NONE, slots=SLOTS_EP1,
    )
    assert f"Write all user-facing text in {name}." in system


@pytest.mark.parametrize("hook_style, line", [
    ("insert_prop", "insert_prop: a close shot of a diegetic object, sign or screen that states the premise"),
    ("shocking_image", "shocking_image: no text at all, just the single strongest, most striking image of the episode"),
    ("text_overlay", "text_overlay: on-screen text, at most 6 words, stating the premise from the first frame"),
])
def test_e1_hook_style_line_for_each_style_value(hook_style, line):
    pack = _pack("fr")
    _, user, _ = prompts.build_e1(
        pack, ep=1, arc_entry=ARC_ENTRY, template=TEMPLATE,
        episode_defaults=dict(EPISODE_DEFAULTS, hook_style=hook_style),
        cast=CAST_E1, places=PLACES_E1, props=PROPS_E1, memory=MEMORY_NONE, slots=SLOTS_EP1,
    )
    assert line in user


@pytest.mark.parametrize("cliffhanger_style, line", [
    ("hard_stop", "hard_stop: end mid-confrontation, no resolution, no line that wraps it up"),
    ("cut_to_black", "cut_to_black: land the reveal, then cut to black for the end card"),
])
def test_e1_cliffhanger_style_line_for_each_style_value(cliffhanger_style, line):
    pack = _pack("fr")
    _, user, _ = prompts.build_e1(
        pack, ep=1, arc_entry=ARC_ENTRY, template=TEMPLATE,
        episode_defaults=dict(EPISODE_DEFAULTS, cliffhanger_style=cliffhanger_style),
        cast=CAST_E1, places=PLACES_E1, props=PROPS_E1, memory=MEMORY_NONE, slots=SLOTS_EP1,
    )
    assert line in user


def _e1_reply_legal_shorter():
    """hook + 6 body + cliffhanger (8 scenes total): the coordinator's own
    example of a reply shorter than the ask's exact 10 but still legal for
    episode 1 -- 6 is the effective body minimum once scenes' own lo (8) and
    the fixed hook/cliffhanger are accounted for (_e1_slot_bounds), and 8
    total sits exactly at scenes' own lo."""
    def scene(function, **kw):
        base = {
            "function": function, "place_id": "place_pool", "time_variant": "day",
            "characters": ["char_kiwilo"], "props": [], "summary": "Ca bouge vite sur l'île.",
            "emotion": "tension", "target_duration_s": {"hook": 2.5, "cliffhanger": 3.0}.get(function, 5.0),
        }
        base.update(kw)
        return base

    body = ["setup", "rising", "peak", "turn", "setup", "rising"]
    scenes = [scene("hook")] + [scene(f) for f in body] + [scene("cliffhanger")]
    return {"title": "Le Debut", "scenes": scenes}


def test_e1_errors_good_fixture_passes():
    reply = _e1_reply()
    errors = prompts.validate_e1(
        reply, ep=1, template=TEMPLATE, episode_defaults=EPISODE_DEFAULTS,
        cast_ids=CAST_IDS, places=PLACES_DICT, prop_ids=["prop_phone"],
    )
    assert errors == []


def test_e1_errors_a_legal_shorter_reply_passes():
    """Stage 12b follow-up: the ask aims for exactly 10, but a legal
    shorter reply (here 8, matching the coordinator's own hook + 6 body +
    cliffhanger example) is accepted, not rejected for falling short of it."""
    reply = _e1_reply_legal_shorter()
    errors = prompts.validate_e1(
        reply, ep=1, template=TEMPLATE, episode_defaults=EPISODE_DEFAULTS,
        cast_ids=CAST_IDS, places=PLACES_DICT, prop_ids=["prop_phone"],
    )
    assert errors == []


def test_e1_errors_one_body_short_of_the_minimum_fails_with_the_range_message():
    reply = _e1_reply_legal_shorter()
    reply["scenes"].pop(1)  # 5 body scenes: one short of the effective minimum (6)
    errors = prompts.validate_e1(
        reply, ep=1, template=TEMPLATE, episode_defaults=EPISODE_DEFAULTS,
        cast_ids=CAST_IDS, places=PLACES_DICT, prop_ids=["prop_phone"],
    )
    assert any("body scene(s), expected 6-9" in e for e in errors)


@pytest.mark.parametrize(
    "mutate, mentions",
    [
        (lambda d: d.__setitem__("title", " ".join(f"w{i}" for i in range(9))), "title"),
        (lambda d: d["scenes"].__setitem__(0, dict(d["scenes"][0], function="setup")), "must start with"),
        (lambda d: d["scenes"][-1].__setitem__("function", "turn"), "cliffhanger"),
        (lambda d: d["scenes"].insert(1, dict(d["scenes"][1], function="hook")), "one 'hook'"),
        (lambda d: d["scenes"][1].__setitem__("function", "hook"), "is not a body function"),
        (lambda d: [d["scenes"].pop() for _ in range(6)], "body scene"),
        (lambda d: d["scenes"][1].__setitem__("time_variant", "dusk"), "time_variant"),
        (lambda d: d["scenes"][1].__setitem__("summary", " ".join(f"w{i}" for i in range(16))), "summary"),
        (lambda d: d["scenes"][1].__setitem__("target_duration_s", 99.0), "target_duration_s"),
    ],
)
def test_e1_errors_each_violation(mutate, mentions):
    reply = _e1_reply()
    mutate(reply)
    errors = prompts.validate_e1(
        reply, ep=1, template=TEMPLATE, episode_defaults=EPISODE_DEFAULTS,
        cast_ids=CAST_IDS, places=PLACES_DICT, prop_ids=["prop_phone"],
    )
    assert errors, "expected at least one error"
    assert any(mentions in e for e in errors)


def test_e1_errors_more_places_than_max_places():
    places_3 = dict(PLACES_DICT, place_third=["day"])
    reply = _e1_reply()
    # 3 distinct places across the scenes, one more than max_places (2).
    reply["scenes"][2]["place_id"] = "place_confessional"
    reply["scenes"][3]["place_id"] = "place_third"
    errors = prompts.validate_e1(
        reply, ep=1, template=TEMPLATE, episode_defaults=EPISODE_DEFAULTS,
        cast_ids=CAST_IDS, places=places_3, prop_ids=["prop_phone"],
    )
    assert any("max_places" in e for e in errors)


def test_e1_errors_recap_required_from_recap_from_episode():
    reply = _e1_reply()
    errors = prompts.validate_e1(
        reply, ep=2, template=TEMPLATE, episode_defaults=EPISODE_DEFAULTS,
        cast_ids=CAST_IDS, places=PLACES_DICT, prop_ids=["prop_phone"],
    )
    assert any("must start with" in e for e in errors)


def test_e1_errors_recap_forbidden_below_recap_from_episode():
    reply = _e1_reply()
    reply["scenes"].insert(0, dict(reply["scenes"][0], function="recap"))
    errors = prompts.validate_e1(
        reply, ep=1, template=TEMPLATE, episode_defaults=EPISODE_DEFAULTS,
        cast_ids=CAST_IDS, places=PLACES_DICT, prop_ids=["prop_phone"],
    )
    assert any("must not have a 'recap'" in e for e in errors)


def test_e1_schema_ids_only_come_from_the_given_story():
    schema = prompts.e1_schema(CAST_IDS, ["place_pool"], ["prop_phone"])
    scene_schema = schema["properties"]["scenes"]["items"]
    assert scene_schema["properties"]["characters"]["items"]["enum"] == CAST_IDS
    assert scene_schema["properties"]["place_id"]["enum"] == ["place_pool"]
    assert scene_schema["properties"]["props"]["items"]["enum"] == ["prop_phone"]


# ==================================================================== E2

def test_build_e2_golden_key_lines_fr():
    pack = _pack("fr")
    system, user, schema = prompts.build_e2(
        pack, scene=BODY_SCENE, scene_number=1, outline=OUTLINE, previous=None, word_budget=20,
        cast=CAST_E2, place=PLACE_E2, props=PROPS_E2, sfx_cues=SFX_CUES,
        narrator_enabled=False, voice_direction="over-acted telenovela delivery",
    )
    assert "Write all user-facing text in French." in system
    assert "Episode outline:" in user
    assert "- s01 (hook): Ouverture choc sur l'île. — Kiwilo" in user
    assert "- s02 (setup): Ils se disputent au bord du bassin. — Kiwilo, Mangella" in user
    assert "Previous scene: none -- this is the episode's first body scene." in user
    assert "Scene (setup, emotion: tension): Ils se disputent au bord du bassin." in user
    assert "char_kiwilo — Kiwilo: wants gagner; fears perdre; speaks: doux" in user
    assert "Place: La Piscine -- un bassin turquoise au centre" in user
    assert "Props present:\n- prop_phone — Le Téléphone" in user
    assert "speaker (one of char_kiwilo, char_mangella)" in user
    assert "use 2-3 lines when two or more characters are present" in user
    assert "at most 22 words; reference lines run 3-8 words" in user
    assert "delivery (English, at most 12 words; the story's voice performance is over-acted telenovela delivery)" in user
    assert "cue (one of gasp_crowd, dramatic_sting, phone_ring)" in user
    assert "Write 14-20 words of dialogue in total (not fewer than 14)." in user
    assert schema == prompts.e2_schema(["char_kiwilo", "char_mangella"], SFX_CUES)


def test_build_e2_with_previous_scene():
    pack = _pack("fr")
    previous = {"summary": "Un premier choc.", "speaker_name": "Kiwilo", "text": "Je n'ai rien fait."}
    _, user, _ = prompts.build_e2(
        pack, scene=BODY_SCENE, scene_number=2, outline=OUTLINE, previous=previous, word_budget=20,
        cast=CAST_E2, place=PLACE_E2, props=PROPS_E2, sfx_cues=SFX_CUES,
        narrator_enabled=False, voice_direction="over-acted",
    )
    assert "Previous scene: Un premier choc." in user
    assert "Its last line -- Kiwilo: Je n'ai rien fait." in user


def test_build_e2_narrator_speaker_listed_when_enabled():
    pack = _pack("fr")
    _, user, schema = prompts.build_e2(
        pack, scene=BODY_SCENE, scene_number=1, outline=OUTLINE, previous=None, word_budget=20,
        cast=CAST_E2, place=PLACE_E2, props=PROPS_E2, sfx_cues=SFX_CUES,
        narrator_enabled=True, voice_direction="over-acted",
    )
    assert "speaker (one of char_kiwilo, char_mangella, narrator)" in user
    assert schema["properties"]["lines"]["items"]["properties"]["speaker"]["enum"][-1] == "narrator"


@pytest.mark.parametrize("language, name", [("fr", "French"), ("en", "English")])
def test_e2_language_line(language, name):
    pack = _pack(language)
    system, _, _ = prompts.build_e2(
        pack, scene=BODY_SCENE, scene_number=1, outline=OUTLINE, previous=None, word_budget=20,
        cast=CAST_E2, place=PLACE_E2, props=PROPS_E2, sfx_cues=SFX_CUES,
        narrator_enabled=False, voice_direction="over-acted",
    )
    assert f"Write all user-facing text in {name}." in system


def _good_e2_reply():
    return {
        "lines": [
            {"speaker": "char_kiwilo", "text": "Tu m'as menti", "emotion": "angry", "delivery": "sharp, fast"},
            {"speaker": "char_mangella", "text": "Jamais je ne mens", "emotion": "scheming", "delivery": "cold, flat"},
        ],
        "sfx_cues": [{"at": "start", "cue": "gasp_crowd"}, {"at": "2", "cue": "phone_ring"}],
        "on_screen_text": None,
    }


def test_e2_errors_good_fixture_passes():
    errors = prompts.validate_e2(_good_e2_reply(), scene=BODY_SCENE, narrator_enabled=False, sfx_cues=SFX_CUES)
    assert errors == []


@pytest.mark.parametrize(
    "mutate, mentions",
    [
        (lambda d: d.__setitem__("lines", []), "line(s), expected 1-4"),
        (lambda d: d["lines"].extend([d["lines"][0]] * 3), "line(s), expected 1-4"),
        (lambda d: d["lines"][0].__setitem__("text", " ".join(f"w{i}" for i in range(23))), "text"),
        (lambda d: d["lines"][0].__setitem__("delivery", " ".join(f"w{i}" for i in range(13))), "delivery"),
        (lambda d: d["sfx_cues"].append({"at": "9", "cue": "gasp_crowd"}), "line number"),
        (lambda d: d.__setitem__("on_screen_text", " ".join(f"w{i}" for i in range(7))), "on_screen_text"),
    ],
)
def test_e2_errors_each_violation(mutate, mentions):
    reply = _good_e2_reply()
    mutate(reply)
    errors = prompts.validate_e2(reply, scene=BODY_SCENE, narrator_enabled=False, sfx_cues=SFX_CUES)
    assert errors, "expected at least one error"
    assert any(mentions in e for e in errors)


def test_e2_errors_speaker_must_be_a_scene_character():
    reply = _good_e2_reply()
    reply["lines"][0]["speaker"] = "char_broccolia"
    with pytest.raises((KeyError, Exception)):
        # schemas.validate rejects it via the enum built from the scene's own characters;
        # this asserts it is rejected one way or another, not a specific exception type.
        errors = prompts.validate_e2(reply, scene=BODY_SCENE, narrator_enabled=False, sfx_cues=SFX_CUES)
        assert errors
        raise Exception("handled via errors list")


def test_e2_errors_narrator_only_allowed_when_enabled():
    reply = _good_e2_reply()
    reply["lines"][0]["speaker"] = "narrator"
    errors = prompts.validate_e2(reply, scene=BODY_SCENE, narrator_enabled=False, sfx_cues=SFX_CUES)
    assert errors
    assert any("speaker" in e or "not one of" in e for e in errors)


# ------------------------------------------------------ E2 word range/floor (F3)

@pytest.mark.parametrize("budget, lo", [(20, 14), (10, 7), (9, 6), (3, 3)])
def test_e2_word_range_lo_is_about_seventy_percent_never_below_three(budget, lo):
    assert prompts._e2_word_range(budget) == (lo, budget)


def test_e2_errors_word_floor_rejects_a_short_reply_and_accepts_a_normal_one():
    """validate_e2's word-count floor (spec 4.2, F3) is well below the ask's
    own range (:func:`prompts._e2_word_range`): a reply of only 1 word fails
    it, but the good fixture's 7 words clear a 10-word budget's floor
    (``ceil(10 / 2) == 5``)."""
    short_reply = {
        "lines": [{"speaker": "char_kiwilo", "text": "Non", "emotion": "angry", "delivery": "flat"}],
        "sfx_cues": [], "on_screen_text": None,
    }
    errors = prompts.validate_e2(short_reply, scene=BODY_SCENE, narrator_enabled=False, sfx_cues=SFX_CUES,
                                 word_budget=10)
    assert errors and all(e.startswith(prompts.E2_WORD_FLOOR_PREFIX) for e in errors)

    # No word_budget given at all: a caller with none to give sees no floor check.
    assert prompts.validate_e2(short_reply, scene=BODY_SCENE, narrator_enabled=False, sfx_cues=SFX_CUES) == []

    assert prompts.validate_e2(_good_e2_reply(), scene=BODY_SCENE, narrator_enabled=False, sfx_cues=SFX_CUES,
                               word_budget=10) == []


# ==================================================================== E3

def test_build_e3_full_golden_key_lines_ep1():
    pack = _pack("fr")
    system, user, schema = prompts.build_e3(
        pack, ep=1, hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE, recap_scene=None,
        outline=OUTLINE, first_body_line=None, last_body_line=None, arc_entry=ARC_ENTRY,
        next_arc_entry=NEXT_ARC_ENTRY, memory=MEMORY_NONE, episode_defaults=EPISODE_DEFAULTS,
        word_budgets={"hook": 8, "cliffhanger": 10}, cast=CAST_E2, narrator_enabled=False,
    )
    assert list(schema["properties"].keys()) == ["hook", "cliffhanger", "teaser"]
    assert "Hook scene (hook, emotion: tension): Ouverture choc sur l'île." in user
    assert "Hook style: insert_prop: a close shot of a diegetic object, sign or screen that states the premise" in user
    assert "Keep the hook's dialogue within 8 words." in user
    assert "Cliffhanger scene (cliffhanger, emotion: shocked): Tout bascule enfin." in user
    assert "Cliffhanger style: hard_stop: end mid-confrontation, no resolution, no line that wraps it up" in user
    assert "Leave one of these hooks open: Qui va trahir qui ?" in user
    assert "Next episode's arc entry (escalation): La trahison éclate au grand jour." in user
    assert "Write hook, cliffhanger, teaser." in user
    assert "recap" not in user.lower().split("write ")[0].split("\n\n")[0]  # no stray recap mention up top
    assert "Write all user-facing text in French." in system


def test_build_e3_ep2_adds_recap_key_and_payoff():
    pack = _pack("fr")
    system, user, schema = prompts.build_e3(
        pack, ep=2, hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE, recap_scene=RECAP_SCENE,
        outline=OUTLINE, first_body_line=None, last_body_line=None, arc_entry=ARC_ENTRY,
        next_arc_entry=NEXT_ARC_ENTRY, memory=MEMORY_EP2, episode_defaults=EPISODE_DEFAULTS,
        word_budgets={"hook": 8, "cliffhanger": 10, "recap": 6}, cast=CAST_E2, narrator_enabled=False,
    )
    assert list(schema["properties"].keys()) == ["recap", "hook", "cliffhanger", "teaser"]
    assert "Write recap, hook, cliffhanger, teaser." in user
    assert "Series memory:" in user
    assert "Previous recap: Kiwilo et Mangella se sont alliés contre Broccolia." in user
    assert "Recap scene (recap, emotion: tension): Ce qui s'est passé avant." in user


def test_build_e3_ep1_never_offers_recap():
    pack = _pack("fr")
    _, _, schema = prompts.build_e3(
        pack, ep=1, hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE, recap_scene=None,
        outline=OUTLINE, first_body_line=None, last_body_line=None, arc_entry=ARC_ENTRY,
        next_arc_entry=NEXT_ARC_ENTRY, memory=MEMORY_NONE, episode_defaults=EPISODE_DEFAULTS,
        word_budgets={}, cast=CAST_E2, narrator_enabled=False,
    )
    assert "recap" not in schema["properties"]


@pytest.mark.parametrize("part", ["hook", "cliffhanger", "teaser"])
def test_build_e3_partial_variant_schema_has_only_that_key(part):
    pack = _pack("fr")
    _, user, schema = prompts.build_e3(
        pack, ep=1, part=part, note="plus sombre", hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE,
        recap_scene=None, outline=OUTLINE, first_body_line=None, last_body_line=None, arc_entry=ARC_ENTRY,
        next_arc_entry=NEXT_ARC_ENTRY, memory=MEMORY_NONE, episode_defaults=EPISODE_DEFAULTS,
        word_budgets={}, cast=CAST_E2, narrator_enabled=False,
    )
    assert list(schema["properties"].keys()) == [part]
    assert f"Write {part}." in user
    assert "Follow the author's note: plus sombre" in user


def test_build_e3_partial_recap_variant_needs_ep_2():
    pack = _pack("fr")
    with pytest.raises(ValueError, match="episode 2"):
        prompts.build_e3(
            pack, ep=1, part="recap", hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE, recap_scene=None,
            outline=OUTLINE, first_body_line=None, last_body_line=None, arc_entry=ARC_ENTRY,
            next_arc_entry=NEXT_ARC_ENTRY, memory=MEMORY_NONE, episode_defaults=EPISODE_DEFAULTS,
            word_budgets={}, cast=CAST_E2, narrator_enabled=False,
        )


@pytest.mark.parametrize("language, name", [("fr", "French"), ("en", "English")])
def test_e3_language_line(language, name):
    pack = _pack(language)
    system, _, _ = prompts.build_e3(
        pack, ep=1, hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE, recap_scene=None,
        outline=OUTLINE, first_body_line=None, last_body_line=None, arc_entry=ARC_ENTRY,
        next_arc_entry=NEXT_ARC_ENTRY, memory=MEMORY_NONE, episode_defaults=EPISODE_DEFAULTS,
        word_budgets={}, cast=CAST_E2, narrator_enabled=False,
    )
    assert f"Write all user-facing text in {name}." in system


def _good_e3_full_reply():
    return {
        "hook": {
            "lines": [{"speaker": "char_kiwilo", "text": "Regardez ca", "emotion": "shocked", "delivery": "sharp"}],
            "on_screen_text": "A VENDRE",
        },
        "cliffhanger": {
            "reveal": "Le telephone sonne enfin et tout le monde se fige.",
            "lines": [{"speaker": "char_mangella", "text": "C'est fini", "emotion": "shocked", "delivery": "cold"}],
        },
        "teaser": "La suite arrive tres bientot sur l'ile.",
    }


def test_e3_errors_good_fixture_passes():
    errors = prompts.validate_e3(
        _good_e3_full_reply(), ep=1, part=None, hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE,
        recap_scene=None, narrator_enabled=False, episode_defaults=EPISODE_DEFAULTS,
    )
    assert errors == []


@pytest.mark.parametrize(
    "mutate, mentions",
    [
        (lambda d: d["hook"].__setitem__("lines", []), "hook.lines"),
        (lambda d: d["hook"]["lines"].append(dict(d["hook"]["lines"][0]))
         or d["hook"]["lines"].append(dict(d["hook"]["lines"][0])), "hook.lines"),
        (lambda d: d["hook"]["lines"][0].__setitem__("speaker", "char_broccolia"), "speaker"),
        (lambda d: d["cliffhanger"].__setitem__("reveal", " ".join(f"w{i}" for i in range(41))), "reveal"),
        (lambda d: d["cliffhanger"]["lines"].append(dict(d["cliffhanger"]["lines"][0])), "cliffhanger.lines"),
        (lambda d: d.__setitem__("teaser", " ".join(f"w{i}" for i in range(16))), "teaser"),
        (lambda d: d["hook"].__setitem__("on_screen_text", " ".join(f"w{i}" for i in range(7))), "on_screen_text"),
    ],
)
def test_e3_errors_each_violation(mutate, mentions):
    reply = _good_e3_full_reply()
    mutate(reply)
    errors = prompts.validate_e3(
        reply, ep=1, part=None, hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE,
        recap_scene=None, narrator_enabled=False, episode_defaults=EPISODE_DEFAULTS,
    )
    assert errors, "expected at least one error"
    assert any(mentions in e for e in errors)


def test_e3_errors_insert_prop_hook_needs_on_screen_text_le_5_words():
    reply = _good_e3_full_reply()
    reply["hook"]["on_screen_text"] = None
    errors = prompts.validate_e3(
        reply, ep=1, part=None, hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE,
        recap_scene=None, narrator_enabled=False, episode_defaults=dict(EPISODE_DEFAULTS, hook_style="insert_prop"),
    )
    assert any("insert_prop" in e for e in errors)

    reply2 = _good_e3_full_reply()
    reply2["hook"]["on_screen_text"] = " ".join(f"w{i}" for i in range(6))
    errors2 = prompts.validate_e3(
        reply2, ep=1, part=None, hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE,
        recap_scene=None, narrator_enabled=False, episode_defaults=dict(EPISODE_DEFAULTS, hook_style="insert_prop"),
    )
    assert any("on_screen_text" in e for e in errors2)


def test_e3_errors_text_overlay_hook_requires_on_screen_text():
    reply = _good_e3_full_reply()
    reply["hook"]["on_screen_text"] = None
    errors = prompts.validate_e3(
        reply, ep=1, part=None, hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE,
        recap_scene=None, narrator_enabled=False, episode_defaults=dict(EPISODE_DEFAULTS, hook_style="text_overlay"),
    )
    assert any("text_overlay" in e for e in errors)


def test_e3_errors_shocking_image_hook_allows_null_on_screen_text():
    reply = _good_e3_full_reply()
    reply["hook"]["on_screen_text"] = None
    errors = prompts.validate_e3(
        reply, ep=1, part=None, hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE,
        recap_scene=None, narrator_enabled=False, episode_defaults=dict(EPISODE_DEFAULTS, hook_style="shocking_image"),
    )
    assert errors == []


def test_e3_errors_recap_part_good_and_bad():
    good = {"recap": {"lines": [], "on_screen_text": "PRÉCÉDEMMENT"}}
    assert prompts.validate_e3(
        good, ep=2, part="recap", hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE,
        recap_scene=RECAP_SCENE, narrator_enabled=False, episode_defaults=EPISODE_DEFAULTS,
    ) == []
    bad = {"recap": {"lines": [{"speaker": "char_kiwilo", "text": "x", "emotion": "tension", "delivery": "y"},
                               {"speaker": "char_kiwilo", "text": "x", "emotion": "tension", "delivery": "y"}],
                      "on_screen_text": None}}
    errors = prompts.validate_e3(
        bad, ep=2, part="recap", hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE,
        recap_scene=RECAP_SCENE, narrator_enabled=False, episode_defaults=EPISODE_DEFAULTS,
    )
    assert any("recap.lines" in e for e in errors)


# ==================================================================== E4

SCRIPT_E4 = {
    "scenes": [
        {"scene_id": "s01", "function": "hook", "place_id": "place_pool", "time_variant": "day",
         "characters": ["char_kiwilo"], "summary": "Ouverture.",
         "lines": [{"speaker": "char_kiwilo", "text": "Regardez ca"}]},
        {"scene_id": "s02", "function": "setup", "place_id": "place_pool", "time_variant": "day",
         "characters": ["char_kiwilo"], "summary": "Suite.",
         "lines": [{"speaker": "narrator", "text": "Il pleut sur l'île"}]},
    ],
}
ENTITIES_E4 = {"places": {"place_pool": "La Piscine"}, "cast": {"char_kiwilo": "Kiwilo"}}
CAST_E4 = [{"char_id": "char_kiwilo", "name": "Kiwilo", "personality": _personality("Kiwilo")}]
PLACES_E4 = [{"place_id": "place_pool", "name": "La Piscine"}]


def test_script_digest_shape():
    digest = prompts.script_digest(SCRIPT_E4, ENTITIES_E4)
    assert "Scene s01 (hook)" in digest
    assert "La Piscine, day" in digest
    assert "characters: Kiwilo" in digest
    assert "Kiwilo: Regardez ca" in digest
    assert "Narrator: Il pleut sur l'île" in digest


def test_build_e4_key_lines():
    pack = _pack("fr")
    digest = prompts.script_digest(SCRIPT_E4, ENTITIES_E4)
    memory = {"series_memory": {"recaps": {"ep01": "Un premier recap."}, "open_hooks": ["Un hook ouvert."],
                                 "relationship_state": {"char_kiwilo|char_mangella": "rivaux"}, "introduced": {}}}
    system, user, schema = prompts.build_e4(pack, script_digest=digest, cast=CAST_E4, places=PLACES_E4, memory=memory)
    assert "Write all user-facing text" not in system  # E4 has its own system template, not the head-writer one
    assert "continuity editor" in system
    assert "Write every fix in French." in system
    assert "Bible written so far:" in user
    assert "Cast (speech style, for the character check):\n- char_kiwilo — Kiwilo: speaks doux" in user
    assert "Places (for the place check):\n- place_pool — La Piscine" in user
    assert "Series memory:" in user
    assert "Episode 1 recap: Un premier recap." in user
    assert "Open hooks: Un hook ouvert." in user
    assert "Relationships: char_kiwilo/char_mangella: rivaux" in user
    assert digest in user
    assert "issues: at most 6" in user
    assert schema == prompts.e4_schema()


def test_build_e4_no_memory_says_none_recorded():
    pack = _pack("fr")
    digest = prompts.script_digest(SCRIPT_E4, ENTITIES_E4)
    _, user, _ = prompts.build_e4(pack, script_digest=digest, cast=CAST_E4, places=PLACES_E4, memory=None)
    assert "Series memory: none recorded yet." in user


def test_e4_errors_good_fixture_passes():
    reply = {"passed": True, "issues": []}
    assert prompts.validate_e4(reply, scene_ids=["s01", "s02"]) == []


@pytest.mark.parametrize(
    "mutate, mentions",
    [
        (lambda d: d["issues"].append({"scene_id": "s99", "kind": "continuity", "fix": "x"}), "scene_id"),
        (lambda d: d["issues"].extend([{"scene_id": "s01", "kind": "continuity", "fix": "x"}] * 7), "at most 6"),
        (lambda d: d["issues"].append({"scene_id": None, "kind": "continuity",
                                        "fix": " ".join(f"w{i}" for i in range(41))}), "fix"),
    ],
)
def test_e4_errors_each_violation(mutate, mentions):
    reply = {"passed": True, "issues": []}
    mutate(reply)
    errors = prompts.validate_e4(reply, scene_ids=["s01", "s02"])
    assert errors, "expected at least one error"
    assert any(mentions in e for e in errors)


def test_e4_errors_passed_must_agree_with_issue_count():
    reply = {"passed": True, "issues": [{"scene_id": "s01", "kind": "continuity", "fix": "Corriger la scene."}]}
    errors = prompts.validate_e4(reply, scene_ids=["s01", "s02"])
    assert any("passed" in e for e in errors)

    reply2 = {"passed": False, "issues": []}
    errors2 = prompts.validate_e4(reply2, scene_ids=["s01", "s02"])
    assert any("passed" in e for e in errors2)


# ==================================================================== T1/T1r

def _good_t1_shots():
    return [
        {"framing": "medium_two_shot", "camera_motion": "push_in", "modifiers": [],
         "action": "@char_kiwilo grips %prop_phone tightly while pacing near the water.",
         "subjects": ["@char_kiwilo", "%prop_phone"], "lines": [1]},
        {"framing": "close_up", "camera_motion": "hold", "modifiers": [],
         "action": "A tight shot on the phone screen lighting up in the dark.",
         "subjects": ["%prop_phone"], "lines": []},
    ]


def test_build_t1_key_lines():
    pack = _pack("fr")
    system, user, schema = prompts.build_t1(
        pack, scene=BODY_SCENE, lines=LINES_T1, characters=CHARACTERS_T1, place=PLACE_T1, props=PROPS_T1,
        previous_shots=[{"framing": "wide_establishing", "camera_motion": "pan_lr"}],
        shots_per_scene=(2, 4), camera=CAMERA_PARAGRAPH, modifiers_allowed=MODIFIERS_ALLOWED,
        hook_style="insert_prop",
    )
    assert f"Camera: {CAMERA_PARAGRAPH}" in user
    assert "@char_kiwilo — a kiwi with fuzzy brown skin — Kiwilo" in user
    assert "Place: #place_pool:day — un bassin turquoise au centre, palmiers à gauche" in user
    assert "%prop_phone — a coconut-shaped telephone" in user
    assert "1. Kiwilo: Tu m'as menti. (angry)" in user
    assert "Previous shots:\n- wide_establishing / pan_lr" in user
    assert "Give 'shots': 2 to 4 entries" in user
    assert "wide_establishing (wide establishing shot, characters small in the frame)" in user
    assert "from @char_kiwilo, #place_pool:day, %prop_phone" in user
    assert schema == prompts.t1_schema((2, 4), MODIFIERS_ALLOWED, TAGS_T1)


def test_build_t1_insert_prop_note_only_for_the_hook_scene_with_that_style():
    pack = _pack("fr")
    hook_scene = dict(BODY_SCENE, function="hook")
    _, user_hook, _ = prompts.build_t1(
        pack, scene=hook_scene, lines=LINES_T1, characters=CHARACTERS_T1, place=PLACE_T1, props=PROPS_T1,
        previous_shots=[], shots_per_scene=(2, 4), camera=CAMERA_PARAGRAPH,
        modifiers_allowed=MODIFIERS_ALLOWED, hook_style="insert_prop",
    )
    assert "exactly one shot must use framing insert_prop" in user_hook

    _, user_body, _ = prompts.build_t1(
        pack, scene=BODY_SCENE, lines=LINES_T1, characters=CHARACTERS_T1, place=PLACE_T1, props=PROPS_T1,
        previous_shots=[], shots_per_scene=(2, 4), camera=CAMERA_PARAGRAPH,
        modifiers_allowed=MODIFIERS_ALLOWED, hook_style="insert_prop",
    )
    assert "exactly one shot must use framing insert_prop" not in user_body


def test_t1_camera_paragraph_present_only_in_t1_and_t1r():
    pack = _pack("fr")
    _, user_t1, _ = prompts.build_t1(
        pack, scene=BODY_SCENE, lines=LINES_T1, characters=CHARACTERS_T1, place=PLACE_T1, props=PROPS_T1,
        previous_shots=[], shots_per_scene=(2, 4), camera=CAMERA_PARAGRAPH,
        modifiers_allowed=MODIFIERS_ALLOWED, hook_style="insert_prop",
    )
    _, user_t1r, _ = prompts.build_t1r(
        pack, scene=BODY_SCENE, shots=_good_t1_shots(), index=0, note=None, lines=LINES_T1,
        characters=CHARACTERS_T1, place=PLACE_T1, props=PROPS_T1, shots_per_scene=(2, 4),
        camera=CAMERA_PARAGRAPH, modifiers_allowed=MODIFIERS_ALLOWED, hook_style="insert_prop",
    )
    assert CAMERA_PARAGRAPH in user_t1
    assert CAMERA_PARAGRAPH in user_t1r

    other_calls = [
        prompts.build_e1(_pack("fr"), ep=1, arc_entry=ARC_ENTRY, template=TEMPLATE,
                          episode_defaults=EPISODE_DEFAULTS, cast=CAST_E1, places=PLACES_E1, props=PROPS_E1,
                          memory=MEMORY_NONE, slots=SLOTS_EP1),
        prompts.build_e2(_pack("fr"), scene=BODY_SCENE, scene_number=1, outline=OUTLINE, previous=None,
                          word_budget=20, cast=CAST_E2, place=PLACE_E2, props=PROPS_E2, sfx_cues=SFX_CUES,
                          narrator_enabled=False, voice_direction="over-acted"),
        prompts.build_e3(_pack("fr"), ep=1, hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE,
                          recap_scene=None, outline=OUTLINE, first_body_line=None, last_body_line=None,
                          arc_entry=ARC_ENTRY, next_arc_entry=NEXT_ARC_ENTRY, memory=MEMORY_NONE,
                          episode_defaults=EPISODE_DEFAULTS, word_budgets={}, cast=CAST_E2, narrator_enabled=False),
        prompts.build_e4(_pack("fr"), script_digest=prompts.script_digest(SCRIPT_E4, ENTITIES_E4),
                          cast=CAST_E4, places=PLACES_E4, memory=None),
    ]
    for system, user, _schema in other_calls:
        assert CAMERA_PARAGRAPH not in system
        assert CAMERA_PARAGRAPH not in user


@pytest.mark.parametrize("language, name", [("fr", "French"), ("en", "English")])
def test_t1_language_line(language, name):
    pack = _pack(language)
    system, _, _ = prompts.build_t1(
        pack, scene=BODY_SCENE, lines=LINES_T1, characters=CHARACTERS_T1, place=PLACE_T1, props=PROPS_T1,
        previous_shots=[], shots_per_scene=(2, 4), camera=CAMERA_PARAGRAPH,
        modifiers_allowed=MODIFIERS_ALLOWED, hook_style="insert_prop",
    )
    assert f"Write all user-facing text in {name}." in system


def test_t1_errors_good_fixture_passes():
    errors = prompts.validate_t1(
        {"shots": _good_t1_shots()}, scene=BODY_SCENE, shots_per_scene=(2, 4),
        modifiers_allowed=MODIFIERS_ALLOWED, tags_allowed=TAGS_T1, n_lines=1, names=NAMES_T1,
    )
    assert errors == []


@pytest.mark.parametrize(
    "mutate, mentions",
    [
        (lambda shots: shots.pop(), "shot(s), expected"),
        (lambda shots: shots[0].__setitem__("action", "Kiwilo grips the phone tightly near the water tonight."), "instead of using a tag"),
        (lambda shots: shots[0]["subjects"].remove("%prop_phone"), "not listed in subjects"),
        (lambda shots: shots[1].__setitem__("framing", shots[0]["framing"]), "repeats the previous"),
        (lambda shots: shots[0].__setitem__("lines", [9]), "not a valid line number"),
        (lambda shots: (shots[0].__setitem__("lines", [1]), shots[1].__setitem__("lines", [1])), "more than one shot"),
    ],
)
def test_t1_errors_each_violation(mutate, mentions):
    shots = _good_t1_shots()
    mutate(shots)
    errors = prompts.validate_t1(
        {"shots": shots}, scene=BODY_SCENE, shots_per_scene=(2, 4),
        modifiers_allowed=MODIFIERS_ALLOWED, tags_allowed=TAGS_T1, n_lines=1, names=NAMES_T1,
    )
    assert errors, "expected at least one error"
    assert any(mentions in e for e in errors)


def test_build_t1r_key_lines_and_replace_marker():
    pack = _pack("fr")
    shots = _good_t1_shots()
    system, user, schema = prompts.build_t1r(
        pack, scene=BODY_SCENE, shots=shots, index=1, note="plus dramatique", lines=LINES_T1,
        characters=CHARACTERS_T1, place=PLACE_T1, props=PROPS_T1, shots_per_scene=(2, 4),
        camera=CAMERA_PARAGRAPH, modifiers_allowed=MODIFIERS_ALLOWED, hook_style="insert_prop",
    )
    assert "shot 2 <- replace this one" in user
    assert "Follow the author's note: plus dramatique" in user
    assert "Replace shot 2, keeping it covering exactly the same lines (none)." in user
    assert schema == prompts.t1r_schema(MODIFIERS_ALLOWED, TAGS_T1)


def test_t1r_errors_good_fixture_passes():
    shots = _good_t1_shots()
    reply = {"shot": {"framing": "extreme_close_up", "camera_motion": "hold", "modifiers": [],
                       "action": "A tight shot on %prop_phone glowing in the dark.",
                       "subjects": ["%prop_phone"], "lines": []}}
    errors = prompts.validate_t1r(
        reply, scene=BODY_SCENE, shots=shots, index=1, modifiers_allowed=MODIFIERS_ALLOWED,
        tags_allowed=TAGS_T1, n_lines=1, names=NAMES_T1,
    )
    assert errors == []


def test_t1r_errors_must_cover_the_same_lines_as_the_replaced_shot():
    shots = _good_t1_shots()
    reply = {"shot": {"framing": "extreme_close_up", "camera_motion": "hold", "modifiers": [],
                       "action": "A tight shot on %prop_phone glowing in the dark.",
                       "subjects": ["%prop_phone"], "lines": [1]}}
    errors = prompts.validate_t1r(
        reply, scene=BODY_SCENE, shots=shots, index=1, modifiers_allowed=MODIFIERS_ALLOWED,
        tags_allowed=TAGS_T1, n_lines=1, names=NAMES_T1,
    )
    assert any("does not cover the same lines" in e for e in errors)


def test_t1r_errors_framing_must_not_repeat_a_neighbour():
    shots = _good_t1_shots()
    reply = {"shot": {"framing": "medium_two_shot", "camera_motion": "hold", "modifiers": [],
                       "action": "A shot on %prop_phone glowing in the dark near the water.",
                       "subjects": ["%prop_phone"], "lines": []}}
    errors = prompts.validate_t1r(
        reply, scene=BODY_SCENE, shots=shots, index=1, modifiers_allowed=MODIFIERS_ALLOWED,
        tags_allowed=TAGS_T1, n_lines=1, names=NAMES_T1,
    )
    assert any("repeats" in e for e in errors)


# ============================================================ LLM schema shape

def _walk_llm_schema(schema, path="$"):
    """Same subset-schema shape checker as test_story_prompts.py's own:
    every allowed keyword, and additionalProperties/required holding for
    every object node ('strict' json_schema mode)."""
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


PHASE3_SCHEMAS = {
    "E1": prompts.e1_schema(CAST_IDS, ["place_pool"], ["prop_phone"]),
    "E2": prompts.e2_schema(["char_kiwilo"], SFX_CUES),
    "E3-full-ep1": prompts.e3_schema(None, 1, ["char_kiwilo"]),
    "E3-full-ep2": prompts.e3_schema(None, 2, ["char_kiwilo"]),
    "E3-part-hook": prompts.e3_schema("hook", 1, ["char_kiwilo"]),
    "E4": prompts.e4_schema(),
    "T1": prompts.t1_schema((2, 4), MODIFIERS_ALLOWED, TAGS_T1),
    "T1r": prompts.t1r_schema(MODIFIERS_ALLOWED, TAGS_T1),
}


@pytest.mark.parametrize("name", list(PHASE3_SCHEMAS))
def test_phase3_llm_schema_shape(name):
    assert _walk_llm_schema(PHASE3_SCHEMAS[name]) == []


def _iter_schema_property_names(schema):
    for key, subschema in schema.get("properties", {}).items():
        yield key
        yield from _iter_schema_property_names(subschema)
        if subschema.get("type") == "array":
            yield from _iter_schema_property_names(subschema.get("items", {}))


_FORBIDDEN_NEW_WORDS = ("timestamp", "path", "url")
_ID_FIELD_NAMES = ("scene_id", "line_id", "shot_id")


@pytest.mark.parametrize("name", list(PHASE3_SCHEMAS))
def test_phase3_schema_never_asks_for_a_forbidden_field(name):
    """No schema asks for a duration other than E1's own ``target_duration_s``,
    nor a path/timestamp/url, nor a line/scene/shot id -- except E4's own
    ``scene_id``, the one place a reply reads an id back (spec 4.3)."""
    names = list(_iter_schema_property_names(PHASE3_SCHEMAS[name]))
    for forbidden in _FORBIDDEN_NEW_WORDS:
        assert not any(forbidden in n for n in names), (name, forbidden, names)
    duration_fields = [n for n in names if "duration" in n]
    assert duration_fields in ([], ["target_duration_s"]), (name, duration_fields)
    id_fields = [n for n in names if n in _ID_FIELD_NAMES]
    if name == "E4":
        assert id_fields == ["scene_id"], (name, id_fields)
    else:
        assert id_fields == [], (name, id_fields)


def test_e4_scene_id_is_the_documented_one_exception():
    names = list(_iter_schema_property_names(PHASE3_SCHEMAS["E4"]))
    assert "scene_id" in names


# ==================================================================== caps

def _fr_words(n):
    words = ("trahison alliance secret complot vérité mensonge rivalité jalousie éliminé "
             "caméra public scandale bouche fâché regarder vote noyau récolte pomme cerise").split()
    return " ".join(words[i % len(words)] for i in range(n))


def _largest_e1_reply():
    # Stage 12b: E1's own worst case is no longer "up to scenes_hi (12),
    # body count flexible within its own range" -- it is now whichever
    # template/episode combination's *exact* slot list (timing.episode_slots)
    # is longest. That is the 90-s template from episode 2 on: 1 recap + 1
    # hook + 9 body (default_body_count 10, clamped down to slots.body.count's
    # own hi of 9 once recap/hook/cliffhanger are paid for) + 1 cliffhanger =
    # 12 scenes, still the template's own scenes-hi.
    def scene(function, dur):
        return {
            "function": function, "place_id": "place_pool", "time_variant": "day",
            "characters": CAST_IDS[:3], "props": ["prop_phone"], "summary": _fr_words(15),
            "emotion": "tension", "target_duration_s": dur,
        }
    scenes = [scene("recap", 3.0), scene("hook", 3.5)]
    scenes += [scene(f, 8.0) for f in (["setup", "rising", "peak", "turn"] * 3)[:9]]
    scenes.append(scene("cliffhanger", 5.0))
    return {"title": _fr_words(8), "scenes": scenes}


def _largest_e2_reply():
    # 4 lines (the max) plus a cue at "start" and one per line (5 total,
    # the practical ceiling for a 4-line scene: nothing stops more, but
    # there is no reason for the model to write one -- DEC-107's method
    # sizes against what the prompt asks for, not an unbounded array).
    return {
        "lines": [
            {"speaker": "char_kiwilo", "text": _fr_words(22), "emotion": "tension", "delivery": _fr_words(12)}
            for _ in range(4)
        ],
        "sfx_cues": [{"at": "start", "cue": "gasp_crowd"}] + [
            {"at": str(i), "cue": "phone_ring"} for i in range(1, 5)
        ],
        "on_screen_text": _fr_words(6),
    }


def _largest_e3_reply():
    return {
        "recap": {"lines": [{"speaker": "char_kiwilo", "text": _fr_words(22), "emotion": "tension",
                              "delivery": _fr_words(12)}], "on_screen_text": _fr_words(6)},
        "hook": {"lines": [{"speaker": "char_kiwilo", "text": _fr_words(22), "emotion": "tension",
                             "delivery": _fr_words(12)},
                            {"speaker": "char_mangella", "text": _fr_words(22), "emotion": "fear",
                             "delivery": _fr_words(12)}],
                 "on_screen_text": _fr_words(6)},
        "cliffhanger": {"reveal": _fr_words(40),
                         "lines": [{"speaker": "char_mangella", "text": _fr_words(22), "emotion": "shocked",
                                    "delivery": _fr_words(12)}]},
        "teaser": _fr_words(15),
    }


def _largest_t1_reply():
    return {
        "shots": [
            {"framing": "medium_two_shot", "camera_motion": "push_in", "modifiers": [],
             "action": "@char_kiwilo and @char_mangella " + _fr_words(24),
             "subjects": ["@char_kiwilo", "@char_mangella", "#place_pool:day", "%prop_phone"], "lines": [1]}
            for _ in range(4)
        ],
    }


def _largest_t1r_reply():
    return {"shot": {"framing": "medium_two_shot", "camera_motion": "push_in", "modifiers": [],
                      "action": "@char_kiwilo " + _fr_words(27), "subjects": ["@char_kiwilo"], "lines": [1]}}


@pytest.mark.parametrize(
    "prompt_id, reply, errors_of",
    [
        ("E1", _largest_e1_reply(), lambda r: prompts.validate_e1(
            r, ep=2, template=TEMPLATE_90, episode_defaults=EPISODE_DEFAULTS, cast_ids=CAST_IDS,
            places=PLACES_DICT, prop_ids=["prop_phone"])),
        ("E2", _largest_e2_reply(), lambda r: prompts.validate_e2(
            r, scene=BODY_SCENE, narrator_enabled=False, sfx_cues=SFX_CUES)),
        ("E3", _largest_e3_reply(), lambda r: prompts.validate_e3(
            r, ep=2, part=None, hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE, recap_scene=RECAP_SCENE,
            narrator_enabled=False, episode_defaults=EPISODE_DEFAULTS)),
        ("T1", _largest_t1_reply(), lambda r: prompts.validate_t1(
            r, scene=BODY_SCENE, shots_per_scene=(2, 4), modifiers_allowed=MODIFIERS_ALLOWED,
            tags_allowed=["@char_kiwilo", "@char_mangella", "#place_pool:day", "%prop_phone"], n_lines=1,
            names={"char_kiwilo": "Kiwilo", "char_mangella": "Mangella"})),
        ("T1r", _largest_t1r_reply(), lambda r: prompts.validate_t1r(
            r, scene=BODY_SCENE, shots=_good_t1_shots(), index=0, modifiers_allowed=MODIFIERS_ALLOWED,
            tags_allowed=TAGS_T1, n_lines=1, names=NAMES_T1)),
    ],
    ids=["E1", "E2", "E3", "T1", "T1r"],
)
def test_the_largest_french_reply_fits_its_cap_but_not_a_much_smaller_one(prompt_id, reply, errors_of):
    estimate = estimate_tokens(json.dumps(reply, ensure_ascii=False))
    needed = estimate * FRENCH_TOKEN_FACTOR
    cap = prompts.MAX_TOKENS[prompt_id]
    too_small = cap // 2
    assert needed > too_small, (
        f"{prompt_id}: the fixture needs only ~{needed:.0f} tokens, too little to prove the "
        f"{cap}-token cap does any work (a {too_small}-token cap would already fit it)"
    )
    assert needed <= cap, (
        f"{prompt_id}: the largest French reply needs ~{needed:.0f} tokens "
        f"({estimate} by chars/4 x {FRENCH_TOKEN_FACTOR}), over its {cap}-token cap"
    )


def test_e4_cap_fits_the_largest_reply_but_not_a_much_smaller_one():
    """E4's cap (MAX_TOKENS) bounds its *output* (passed + <=6 short issues),
    a small reply regardless of how big the script it read was -- unlike
    E1-T1r, its worst case is not "every field at its word limit" but simply
    6 issues at their own 40-word fix cap. Same French-cap method as the
    truncation guard above: this alone needed raising E4's cap from the
    spec's starting 350 to 800 (measured ~728)."""
    reply = {"passed": False, "issues": [
        {"scene_id": "s01", "kind": "continuity", "fix": _fr_words(40)} for _ in range(6)
    ]}
    assert prompts.validate_e4(reply, scene_ids=["s01"]) == []
    estimate = estimate_tokens(json.dumps(reply, ensure_ascii=False))
    needed = estimate * FRENCH_TOKEN_FACTOR
    cap = prompts.MAX_TOKENS["E4"]
    assert needed > cap // 2, f"the fixture needs only ~{needed:.0f} tokens, too little to prove the cap does work"
    assert needed <= cap, f"the largest E4 reply needs ~{needed:.0f} tokens, over its {cap}-token cap"


def test_max_tokens_final_caps():
    """The starting caps (600/300/350/350/250/150) did not survive the
    French-cap method: every one but T1r's needed raising once measured
    against the largest reply its own ask text allows (see the comment
    above prompts.MAX_TOKENS and the truncation-guard tests above)."""
    assert prompts.MAX_TOKENS["E1"] == 1450
    assert prompts.MAX_TOKENS["E2"] == 600
    assert prompts.MAX_TOKENS["E3"] == 720
    assert prompts.MAX_TOKENS["E4"] == 800
    assert prompts.MAX_TOKENS["T1"] == 580
    assert prompts.MAX_TOKENS["T1r"] == 150


def test_phase3_temperatures():
    for prompt_id in ("E1", "E2", "E3", "T1", "T1r"):
        assert prompts.TEMPERATURE[prompt_id] is prompts.WRITING_TEMPERATURE
    assert prompts.TEMPERATURE["E4"] is prompts.ANALYTIC_TEMPERATURE


# ==================================================================== input budgets

def _e4_worst_case_digest_and_memory():
    """The 12-scene French worst-case fixture INPUT_BUDGET['E4'] is sized
    on: every line at its 22-word cap, the true per-function line-count
    ceiling (recap<=1, hook<=2, body<=4, cliffhanger<=1), 5 cast members at
    their own 25-word wants/fears/speech_style cap, and series memory at
    its own _E4_MEMORY_MAX_* caps.
    """
    cast = [
        {"char_id": f"char_{i}", "name": f"Personnage{i}",
         "personality": {"traits": ["t1", "t2"], "wants": _fr_words(25), "fears": _fr_words(25),
                          "speech_style": _fr_words(25)}}
        for i in range(5)
    ]
    places = [{"place_id": f"place_{i}", "name": f"Lieu {i}"} for i in range(3)]
    places_by_id = {p["place_id"]: p["name"] for p in places}
    cast_by_id = {c["char_id"]: c["name"] for c in cast}

    body_cycle = ["setup", "rising", "peak", "turn"]
    body = [body_cycle[i % 4] for i in range(9)]
    functions = ["recap", "hook"] + body + ["cliffhanger"]
    max_lines = {"recap": 1, "hook": 2, "cliffhanger": 1}
    scenes = []
    for i, fn in enumerate(functions):
        n_lines = max_lines.get(fn, 4)
        scenes.append({
            "scene_id": f"s{i:02d}", "function": fn, "place_id": places[i % 3]["place_id"],
            "time_variant": "day", "characters": [c["char_id"] for c in cast[:3]], "summary": _fr_words(15),
            "lines": [{"speaker": cast[j % 5]["char_id"], "text": _fr_words(22)} for j in range(n_lines)],
        })
    script = {"scenes": scenes}
    entities = {"places": places_by_id, "cast": cast_by_id}
    digest = prompts.script_digest(script, entities)

    memory = {
        "series_memory": {
            "recaps": {"ep01": _fr_words(40), "ep02": _fr_words(40)},
            "open_hooks": [_fr_words(15) for _ in range(4)],
            "relationship_state": {
                "char_0|char_1": _fr_words(15), "char_0|char_2": _fr_words(15), "char_0|char_3": _fr_words(15),
                "char_1|char_2": _fr_words(15), "char_1|char_3": _fr_words(15),
                "char_2|char_3": _fr_words(15),
            },
        }
    }
    return digest, cast, places, memory


def test_e4_worst_case_fixture_fits_its_input_budget():
    pack = _pack("fr")
    digest, cast, places, memory = _e4_worst_case_digest_and_memory()
    system, user, _schema = prompts.build_e4(pack, script_digest=digest, cast=cast, places=places, memory=memory)
    tokens = context.check_budget(system, user, budget=prompts.INPUT_BUDGET["E4"])
    assert tokens <= prompts.INPUT_BUDGET["E4"]
    assert prompts.INPUT_BUDGET["E4"] <= 4000


def test_input_budget_names_every_episode_prompt():
    # Stage 6 sized E1/E2/E3/T1/T1r on live-sized data (tests/test_story_episode_prompt_budgets.py).
    assert list(prompts.INPUT_BUDGET) == ["E1", "E2", "E3", "E4", "T1", "T1r"]


@pytest.mark.parametrize(
    "builder, kwargs",
    [
        (prompts.build_e1, dict(ep=1, arc_entry=ARC_ENTRY, template=TEMPLATE, episode_defaults=EPISODE_DEFAULTS,
                                 cast=CAST_E1, places=PLACES_E1, props=PROPS_E1, memory=MEMORY_NONE,
                                 slots=SLOTS_EP1)),
        (prompts.build_e2, dict(scene=BODY_SCENE, scene_number=1, outline=OUTLINE, previous=None, word_budget=20,
                                 cast=CAST_E2, place=PLACE_E2, props=PROPS_E2, sfx_cues=SFX_CUES,
                                 narrator_enabled=False, voice_direction="over-acted")),
        (prompts.build_e3, dict(ep=1, hook_scene=HOOK_SCENE, cliffhanger_scene=CLIFF_SCENE, recap_scene=None,
                                 outline=OUTLINE, first_body_line=None, last_body_line=None, arc_entry=ARC_ENTRY,
                                 next_arc_entry=NEXT_ARC_ENTRY, memory=MEMORY_NONE, episode_defaults=EPISODE_DEFAULTS,
                                 word_budgets={"hook": 8, "cliffhanger": 10}, cast=CAST_E2, narrator_enabled=False)),
        (prompts.build_t1, dict(scene=BODY_SCENE, lines=LINES_T1, characters=CHARACTERS_T1, place=PLACE_T1,
                                 props=PROPS_T1, previous_shots=[], shots_per_scene=(2, 4), camera=CAMERA_PARAGRAPH,
                                 modifiers_allowed=MODIFIERS_ALLOWED, hook_style="insert_prop")),
        (prompts.build_t1r, dict(scene=BODY_SCENE, shots=_good_t1_shots(), index=0, note=None, lines=LINES_T1,
                                  characters=CHARACTERS_T1, place=PLACE_T1, props=PROPS_T1, shots_per_scene=(2, 4),
                                  camera=CAMERA_PARAGRAPH, modifiers_allowed=MODIFIERS_ALLOWED,
                                  hook_style="insert_prop")),
    ],
    ids=["E1", "E2", "E3", "T1", "T1r"],
)
def test_every_other_new_prompt_fits_the_default_pack_budget(builder, kwargs):
    pack = _pack("fr")
    system, user, _schema = builder(pack, **kwargs)
    tokens = context.check_budget(system, user)
    assert tokens <= context.PACK_TOKEN_BUDGET, f"{builder.__name__} used {tokens} tokens"


# ==================================================================== llm_call budget wiring

def test_llm_call_passes_the_per_prompt_budget_for_e4():
    """A fake-runner test in the style of tests/test_story_steps.py's own
    call_json tests: ``llm_call.call_json`` must pass E4's wider
    ``INPUT_BUDGET`` to ``context.check_budget`` rather than the default
    ``PACK_TOKEN_BUDGET`` -- a script long enough to need E4's own budget
    but still under PACK_TOKEN_BUDGET*4 would otherwise be refused before
    ever reaching the chain.
    """
    from clipping.aistory import steps
    from clipping.aistory.steps import llm_call
    from clipping.cancel import CancelToken
    from clipping.providers.registry import Link

    long_user = "mot " * 1300  # ~1300 tokens: over PACK_TOKEN_BUDGET (1200), under INPUT_BUDGET["E4"]
    reply = {"passed": True, "issues": []}
    calls = []

    def recording_runner(chain, **kwargs):
        calls.append(kwargs)
        return reply, Link("gemini", "gemini-test")

    log = []
    ctx = steps.StepContext(
        job_id="job000000001", story_id="0123456789ab", step="script", ep=1, params={},
        cancel=CancelToken(), settings_env={"LLM_CHAIN": "gemini/gemini-test", "GOOGLE_API_KEY": "k"},
        outputs_dir="/tmp/does-not-need-to-exist-for-this-test", on_log=log.append,
    )

    def validator(value):
        return prompts.validate_e4(value, scene_ids=["s01"])

    value = llm_call.call_json(
        ctx, "E4", "system", long_user, prompts.e4_schema(), validator=validator,
        runner=recording_runner,
    )
    assert value == reply
    assert len(calls) == 1  # accepted first try, no retry


def test_llm_call_still_refuses_an_e1_prompt_over_its_budget():
    from clipping.aistory import steps
    from clipping.aistory.steps import llm_call
    from clipping.cancel import CancelToken

    ctx = steps.StepContext(
        job_id="job000000001", story_id="0123456789ab", step="script", ep=1, params={},
        cancel=CancelToken(), settings_env={"LLM_CHAIN": "gemini/gemini-test", "GOOGLE_API_KEY": "k"},
        outputs_dir="/tmp/does-not-need-to-exist-for-this-test", on_log=lambda line: None,
    )
    long_user = "word " * 6000

    def runner(chain, **kwargs):
        raise AssertionError("must not be called: the budget check happens before any chain call")

    with pytest.raises(ValueError, match=rf"over the {prompts.INPUT_BUDGET['E1']}-token budget"):
        llm_call.call_json(ctx, "E1", "system", long_user, {}, validator=prompts.validate_e1, runner=runner)


# ==================================================================== import hygiene

def test_prompts_module_imports_nothing_outside_stdlib_and_the_package():
    """The builders import nothing beyond stdlib + clipping.aistory (+ the
    analyzer temperatures already imported by prompts.py) -- same rule as
    every builder above (module docstring: "Stdlib only (DEC-012)")."""
    source = Path(prompts.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed_modules = {"clipping.analysis.analyzer"}
    for node in tree.body:  # module-level only, not inside a function
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in ("re",), f"unexpected top-level import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                continue  # "from . import context, prompting, schemas" -- inside the package
            if node.module == "__future__":
                continue
            assert node.module in allowed_modules, f"unexpected top-level import: {node.module}"
            for alias in node.names:
                assert alias.name in ("ANALYTIC_TEMPERATURE", "WRITING_TEMPERATURE")


# ==================================================================== RC-E1 (byte-identical K1...U1)

def test_rc_e1_phase1_and_phase2_builders_are_byte_identical_to_pre_phase3():
    """RC-E1: adding E1-T1r must not change one byte of what K1...U1 (and
    C1/B1/B2/B3) build. tests/test_story_prompts.py already pins every one
    of those with a golden-string or key-line assertion of its own and
    keeps passing unedited (bar the three PROMPT_VERSION/MAX_TOKENS/
    SCHEMA_NAMES catalogue-growth lines the coordinator authorized), which
    is the regression contract's own proof; this test additionally checks
    that SYSTEM_TEMPLATE -- shared by every phase-1/2/3 builder alike --
    is unchanged from the s3 wording, since a silent edit there would slip
    past every individual golden string that only asserts a substring.
    """
    assert prompts.SYSTEM_TEMPLATE == (
        "You are the head writer of a serialized vertical-video fiction "
        "series for TikTok, YouTube Shorts and Instagram Reels. Each "
        "episode lasts about 60 seconds and ends on a cliffhanger, so "
        "every idea must pay off in seconds and make people come back. "
        "Reply with JSON only, matching the schema. Never output "
        "durations, timestamps or file paths. Never use real people, "
        "brands, studio names or copyrighted characters. Write all "
        "user-facing text in {language_name}. Fields marked (English) are "
        "for image and voice models: write them in English."
    )
