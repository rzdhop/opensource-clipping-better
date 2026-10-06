"""The fruit drama's own format and the two render switches behind its recipe
(AI Story plan 32 stage 4, DEC-315).

- ``templates/episodes/fruit_drama_75s_v2.json``: window 60-90 s, target 75 s, 4-6 scenes of 1-2 shots of
  5-10 s, one silent reaction shot a scene, recap from episode 2, the 1.5 s end card with its call to action:
  it validates, ``format_fit.choose_format`` accepts it on the clip links the own-GPU and native-speech
  profiles use, episode 1 has 5 scenes and episode 2 six with the recap, the fruit_drama preset sets it and
  the fruit_drama style suggests it;
- the end card on a ``hard_stop`` style: the script's ``cliffhanger.cut_to_black`` (which the timing and the
  render read) is set from the style's cliffhanger OR the story's recipe (``recipes.ends_on_card``), and the card
  reads the recipe's line ("Partie {n} demain", n = the next part) in place of the template's call to action;
- the hook's on-screen text is burned for ``insert_prop`` when the recipe is on;
- a story without a recipe renders exactly as before: no card on a hard_stop, no hook text, the template's CTA.

Pure and offline (the render plan is built, ffmpeg never runs); stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import copy
import itertools
from types import SimpleNamespace

import pytest

from clipping.aistory import defaults, format_fit, presets, recipes, schemas, templates, timing
from clipping.aistory.render import golden, plan as plan_mod, subtitles, timeline as timeline_mod
from clipping.aistory.steps import clips as clips_step
from clipping.aistory.steps import script as script_step
from clipping.providers import video as video_providers

FORMAT = "fruit_drama_75s_v2"
TEMPLATE = templates.load_episode_template(FORMAT)
FRUIT = recipes.load("fruit_drama")
FFMPEG = {"version": "6.1.1-test", "machine": "x86_64"}
HOOK_TEXT = "SHE KNEW"


# ============================================================ the format

def test_the_template_validates_and_says_what_the_recipe_asks():
    assert schemas.episode_template_errors(TEMPLATE) == []
    assert FORMAT in defaults.EPISODE_TEMPLATE_IDS and FORMAT in templates.list_episode_template_ids()
    assert (TEMPLATE["window_s"], TEMPLATE["target_s"]) == ([60, 90], 75)
    assert TEMPLATE["scenes"] == [4, 6] and TEMPLATE["scenes"] == FRUIT["format"]["scenes"]
    assert TEMPLATE["window_s"] == FRUIT["format"]["seconds"]
    assert (TEMPLATE["shots_per_scene"], TEMPLATE["min_shot_s"], TEMPLATE["max_shot_s"]) == ([1, 2], 5, 10)
    assert TEMPLATE["reaction_shots"] == [1, 1] and TEMPLATE["recap_from_episode"] == 2
    assert TEMPLATE["end_card_s"] == FRUIT["end_card"]["seconds"] == 1.5 and TEMPLATE["end_card_cta"] is True
    assert TEMPLATE["recap_from_episode"] == FRUIT["beats"]["recap_from_episode"]
    assert "Plan 32" in TEMPLATE["notes"] and "DEC-315" in TEMPLATE["notes"]
    # No narrator fields: the fruits speak in their own voices.
    assert {"narrator_share", "character_lines", "narrator_slots"}.isdisjoint(TEMPLATE)


def test_episode_1_has_five_scenes_and_episode_2_six_with_the_recap():
    assert timing.episode_slots(TEMPLATE, 1) == ["hook", "body", "body", "body", "cliffhanger"]
    assert timing.episode_slots(TEMPLATE, 2) == ["recap", "hook", "body", "body", "body", "cliffhanger"]
    assert timing.episode_slots(TEMPLATE, 7) == timing.episode_slots(TEMPLATE, 2)
    lo, hi = TEMPLATE["scenes"]
    for ep in (1, 2):
        assert lo <= len(timing.episode_slots(TEMPLATE, ep)) <= hi


def test_the_scene_functions_are_the_recipe_beats():
    assert FRUIT["beats"]["order"] == ["recap", "confrontation", "peak", "cliffhanger"]
    functions = {slot: TEMPLATE["slots"][slot]["functions"] for slot in TEMPLATE["slots"]}
    assert functions["body"] == ["setup", "rising", "peak", "turn"]
    assert functions["recap"] == ["recap"] and functions["cliffhanger"] == ["cliffhanger"]


def test_the_preset_sets_the_format_and_the_style_suggests_it():
    assert presets.apply("fruit_drama")["episode_template_id"] == FORMAT
    assert defaults.EPISODE_TEMPLATE_ID_FRUIT_DRAMA == FORMAT
    style = templates.load_style("fruit_drama")
    assert style["episode_defaults"]["episode_template_id"] == FORMAT
    assert style["version"] == 1


def test_choose_format_accepts_it_on_the_preset_profile_and_the_native_speech_ones():
    preset_profile = presets.apply("fruit_drama")["generation_profile"]
    profiles = [preset_profile, defaults.manual_speech_generation_profile(),
                dict(defaults.quality_generation_profile(), budget_profile=defaults.NATIVE_SPEECH_PROFILE)]
    for profile, language in itertools.product(profiles, ("fr", "en")):
        assert format_fit.format_refusal(profile, FORMAT, language=language,
                                         style_template_id="fruit_drama") is None, (profile, language)
        assert format_fit.choose_format(profile, FORMAT, language=language,
                                        style_template_id="fruit_drama") == FORMAT
    fit, hidden = format_fit.formats_that_fit(defaults.manual_speech_generation_profile(), language="fr",
                                              style_template_id="fruit_drama")
    assert FORMAT in fit and FORMAT not in hidden


def test_every_clip_link_s_lengths_fit_the_window_with_and_without_the_recipe_s_card():
    """Every video link's clip lengths (Veo, kling, seedance, ltx and the RunPod templates of the own GPU), in both
    languages, on both episodes, with the end card the recipe adds (1.5 s less the 0.4 s fade) and without it."""
    links = sorted(video_providers.CLIP_LENGTHS) + ["manual/upload", None]
    window_hi = TEMPLATE["window_s"][1]
    refused = []
    for link, ep, language, card in itertools.product(links, (1, 2), ("fr", "en"), (False, True)):
        lengths = clips_step.link_lengths(link)
        preview = timing.plan_floor_preview(TEMPLATE, ep, (lengths, lengths), False, lang=language, end_card=card)
        if "unplannable" in preview or preview["floor_s"] > window_hi + 1e-6:
            refused.append((link, ep, language, card, preview.get("unplannable"), preview["floor_s"]))
    assert refused == [], refused


def test_a_default_plan_on_6_8_s_clips_lands_inside_the_window():
    for ep in (1, 2):
        preview = timing.plan_floor_preview(TEMPLATE, ep, ((6, 8), (6, 8)), False, end_card=True)
        assert TEMPLATE["window_s"][0] <= preview["floor_s"] <= TEMPLATE["window_s"][1], preview
        assert preview["scenes"] == len(timing.episode_slots(TEMPLATE, ep)) and preview["fitted"] == []


# ============================================================ the script's flag

def _ec(story_recipe, cliffhanger_style):
    story = {"recipe": story_recipe}
    return SimpleNamespace(story=story, ep=1, language="fr", template=TEMPLATE,
                           episode_defaults={"cliffhanger_style": cliffhanger_style, "hook_style": "insert_prop"})


@pytest.mark.parametrize("recipe, style, expected", [
    ("fruit_drama", "hard_stop", True),      # the recipe asks for the card
    ("fruit_drama", "cut_to_black", True),
    (None, "hard_stop", False),              # as before: no card
    (None, "cut_to_black", True),            # as before: the card after the fade
    ("no_such_recipe", "hard_stop", False),  # an unknown id is no recipe, never an error
])
def test_the_script_s_cut_to_black_follows_the_style_or_the_recipe(recipe, style, expected):
    assert recipes.ends_on_card({"recipe": recipe}, style) is expected
    script = script_step.skeleton(_ec(recipe, style), now="2026-10-06T00:00:00Z")
    assert script["cliffhanger"]["cut_to_black"] is expected


def test_the_recipe_s_card_line_is_its_own_text_with_the_next_part():
    assert recipes.end_card_line(FRUIT, 2) == "Partie 2 demain"
    assert recipes.end_card_line(FRUIT, 5) == "Partie 5 demain"
    assert recipes.end_card_line(None, 2) is None
    assert recipes.end_card_line({"end_card": None}, 2) is None


# ============================================================ the render

def _plan(tmp_path, *, recipe, hook_text=None, language="fr", ep=2, template=None):
    """The render plan of the golden fixture (a card after its last shot: its board is the one cut for it), built
    without ffmpeg."""
    docs = golden.build_documents()
    docs["script"]["hook"] = {"on_screen_text": hook_text}
    if template is not None:
        docs["template"] = template
    story = {"story_id": "0123456789ab", "title": "Fraisita et les autres", "language": language}
    if recipe:
        story["recipe"] = recipe
    inputs = golden.write_sources(tmp_path)
    return plan_mod.build_render_plan(**docs, story=story, ep=ep, inputs=inputs, ffmpeg=FFMPEG, profile="golden")


def _file(plan, path):
    return next((item["text"] for item in plan["files"] if item["path"] == path), None)


def test_a_recipe_story_gets_the_card_with_the_recipe_s_line_on_a_hard_stop_style(tmp_path):
    plan = _plan(tmp_path, recipe="fruit_drama", ep=1)
    assert plan["timeline"]["end_card"] is not None
    card = _file(plan, plan_mod.END_CARD_ASS_REL)
    assert "Partie 2 demain" in card  # episode 1: the next part is 2
    assert "PARTIE 2" in card and "Fraisita et les autres" in card  # the headline and the title stay
    assert "Commente" not in card and "Comment " not in card  # the template's call to action is replaced
    assert any(stage["kind"] == "end_card" for stage in plan["stages"])


def test_the_card_line_counts_the_next_part_and_is_the_recipe_s_french_in_english_too(tmp_path):
    plan = _plan(tmp_path, recipe="fruit_drama", ep=4, language="en")
    card = _file(plan, plan_mod.END_CARD_ASS_REL)
    assert "Partie 5 demain" in card and "PART 5" in card


def test_a_story_without_a_recipe_keeps_the_template_s_call_to_action(tmp_path):
    template = copy.deepcopy(golden.TEMPLATE)
    template["end_card_cta"] = True
    plan = _plan(tmp_path, recipe=None, ep=1, template=template, language="fr")
    card = _file(plan, plan_mod.END_CARD_ASS_REL)
    assert "Commente « PARTIE 2 » pour la suite" in card and "demain" not in card


def test_a_recipe_less_card_is_the_one_it_always_was(tmp_path):
    with_none = _plan(tmp_path, recipe=None, hook_text=HOOK_TEXT)
    assert _plan(tmp_path, recipe="no_such_recipe", hook_text=HOOK_TEXT) == with_none  # an unknown id is no recipe
    card = _file(with_none, plan_mod.END_CARD_ASS_REL)
    assert card == subtitles.end_card_ass("fr", 3, "Fraisita et les autres", _typography(with_none),
                                          with_none["timeline"]["end_card"]["duration_s"], cta=False)


def _typography(plan):
    typography = dict(golden.STYLE_LOCK["typography"])
    typography["font_family"] = plan["font"]["family"]
    return typography


def _hard_stop_documents():
    docs = golden.build_documents()
    docs["script"]["cliffhanger"]["cut_to_black"] = False
    docs["script"]["hook"] = {"on_screen_text": HOOK_TEXT}
    line = timeline_mod.build_timeline(docs["script"], docs["storyboard"], docs["template"], "fr",
                                       style_lock=docs["style_lock"])
    return docs, line


def _subtitles(docs, line, **kwargs):
    typography = dict(docs["style_lock"]["typography"], font_family="Montserrat ExtraBold")
    return subtitles.build_subtitles_ass(
        timeline=line, script=docs["script"], subtitle_mode="word_pop", language="fr", hook_style="insert_prop",
        ai_label_enabled=False, palette=docs["style_lock"]["palette"], typography=typography, **kwargs)[0]


def test_a_recipe_less_hard_stop_has_no_card_and_no_hook_text():
    docs, line = _hard_stop_documents()
    assert line["end_card"] is None and line["shots"][-1]["transition_after"] is None
    assert HOOK_TEXT not in _subtitles(docs, line).upper()  # insert_prop: the prop shows, no text is burned
    assert _subtitles(docs, line) == _subtitles(docs, line, burn_hook_text=False)


def test_burn_hook_text_burns_the_text_for_insert_prop_and_changes_nothing_else():
    docs, line = _hard_stop_documents()
    off, on = _subtitles(docs, line), _subtitles(docs, line, burn_hook_text=True)
    assert HOOK_TEXT in on.upper() and HOOK_TEXT not in off.upper()
    extra = [row for row in on.splitlines() if row not in off.splitlines()]
    assert extra and all(HOOK_TEXT in row.upper() or row.startswith("Style:") for row in extra), extra
    docs["script"]["hook"] = {"on_screen_text": None}
    assert _subtitles(docs, line, burn_hook_text=True) == off  # no text, nothing burned


def test_the_hook_text_is_burned_for_insert_prop_only_with_the_recipe(tmp_path):
    assert golden.STYLE_LOCK["episode_defaults"]["hook_style"] == "insert_prop"
    off = _file(_plan(tmp_path, recipe=None, hook_text=HOOK_TEXT), plan_mod.SUBTITLES_REL)
    on = _file(_plan(tmp_path, recipe="fruit_drama", hook_text=HOOK_TEXT), plan_mod.SUBTITLES_REL)
    assert HOOK_TEXT not in off.upper()
    assert HOOK_TEXT in on.upper()
    # The recipe adds only the hook's lines: everything else of the document is the recipe-less one.
    extra = [line for line in on.splitlines() if line not in off.splitlines()]
    assert extra and all(HOOK_TEXT in line.upper() or line.startswith("Style:") for line in extra), extra


def test_a_recipe_with_no_hook_text_burns_nothing(tmp_path):
    plan = _plan(tmp_path, recipe="fruit_drama", hook_text=None)
    assert HOOK_TEXT not in _file(plan, plan_mod.SUBTITLES_REL)
    assert _file(plan, plan_mod.SUBTITLES_REL) == _file(
        _plan(tmp_path, recipe=None, hook_text=None), plan_mod.SUBTITLES_REL)


def test_the_timeline_draws_the_template_s_card_after_the_last_shot_when_the_script_asks(tmp_path):
    docs = golden.build_documents()
    template = copy.deepcopy(docs["template"])
    template["end_card_s"] = 1.5
    docs["script"]["cliffhanger"]["cut_to_black"] = True
    line = timeline_mod.build_timeline(docs["script"], docs["storyboard"], template, "fr",
                                       style_lock=docs["style_lock"])
    card = line["end_card"]
    last = line["shots"][-1]
    assert card["duration_s"] == 1.5
    assert last["transition_after"] == {"type": "fadeblack", "duration_s": 0.4}
    assert card["start_s"] == round(last["start_s"] + last["duration_s"] - 0.4, 3)
    docs["script"]["cliffhanger"]["cut_to_black"] = False
    hard = timeline_mod.build_timeline(docs["script"], docs["storyboard"], template, "fr",
                                       style_lock=docs["style_lock"])
    assert hard["end_card"] is None and hard["shots"][-1]["transition_after"] is None
    assert round(line["total_s"] - hard["total_s"], 3) == 1.1  # the card less the fade into it


def test_the_render_step_hands_the_recipe_over_only_when_the_story_has_one():
    from clipping.aistory.steps import render as render_step

    def args(recipe):
        story = {"title": "T", "recipe": recipe, "generation_profile": {}, "language": "fr"}
        ec = SimpleNamespace(story=story, story_id="abc", ep=1, language="fr",
                             style_lock=templates.load_style("fruit_drama"), template=TEMPLATE)
        return render_step.plan_args(ec, {}, {}, {}, {}, subtitles=None, encoder="libx264")

    assert args(None)["story"] == {"story_id": "abc", "title": "T", "language": "fr"}  # the arguments it always had
    assert args("fruit_drama")["story"] == {"story_id": "abc", "title": "T", "language": "fr", "recipe": "fruit_drama"}
