"""The confrontation format, ``confrontation_50s_v2`` (plan 22 stage 3).

The human's reference: a 52 s confrontation in one place, in real time,
about 15 shots of 2-5 s, every line spoken on camera. The format:

- window 44-58 s, target 50, 4-6 scenes (hook, 2-3 body beats of 10-16 s, a
  cliffhanger, the recap from episode 2), shots of 2-8 s; plan 28 stage A2
  (DEC-305): the window ends at 59 s (a native episode 1 plans 58 s of clips
  on Veo, the end card 0.6 s more) and 4-5 scenes keep episode 2 at two body
  beats beside the recap;
- ``single_place``: E1 is told one continuous place and ``max_places`` is 1;
- ``scene_transition: "cut"``: every boundary a cut -- on this template
  only, every other one keeps its grammar byte for byte;
- ``narrator_slots: ["recap"]``: the narrator speaks in the recap only
  (DEC-231 amended for this format);
- ``line_words`` [5, 17] (one line, one 8 s shot), ``episode_words``
  [95, 125] spread over the scenes by ``timing.word_budget_v3``,
  ``reaction_shots`` [0, 1];
- the format a new story on a native-speech profile starts on when it names
  none (``defaults.episode_template_for``; the wizard suggests it the same
  way) -- a suggestion, never forced (DEC-268).

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import copy
import pathlib
import re
from types import SimpleNamespace

import pytest

from clipping.aistory import defaults, schemas, templates, timing

ROOT = pathlib.Path(__file__).resolve().parents[1]
STORY_SRC = ROOT / "web" / "dashboard" / "src" / "pages" / "story"
CONFRONTATION = templates.load_episode_template("confrontation_50s_v2")
SERIAL_V2 = templates.load_episode_template("serial_60s_v2")
NEW_KEYS = ("single_place", "scene_transition", "narrator_slots", "line_words", "episode_words", "reaction_shots")
NOW = "2026-10-04T10:00:00+00:00"


def test_the_template_validates_with_the_plans_values():
    tpl = CONFRONTATION
    assert schemas.episode_template_errors(tpl) == []
    assert defaults.EPISODE_TEMPLATE_ID_CONFRONTATION in defaults.EPISODE_TEMPLATE_IDS
    # Re-pinned on purpose (plan 28 stage A2, DEC-305): the window ends at 59 s (8 + 3 x 14 + 8 = 58 s of Veo
    # clips and the 0.6 s end card), and scenes [4, 5] keeps episode 2 at two body beats (6 + 8 + 2 x 14 + 8 = 50 s;
    # three would be 64 s).
    assert (tpl["window_s"], tpl["target_s"], tpl["tighten_above_s"]) == ([44, 59], 50, 55)
    assert (tpl["scenes"], tpl["shots"], tpl["min_shot_s"], tpl["max_shot_s"]) == ([4, 5], [9, 16], 5, 10)
    slots = tpl["slots"]
    assert slots["hook"]["duration_s"] == [5.0, 8.0] and slots["cliffhanger"]["duration_s"] == [6.0, 10.0]
    assert slots["body"]["count"] == [2, 3] and slots["body"]["duration_s"] == [10.0, 16.0]
    assert slots["recap"]["duration_s"] == [5.0, 6.0] and tpl["recap_from_episode"] == 2
    assert tpl["default_body_count"] == 3 and tpl["tail_peak_functions"] == ["peak", "cliffhanger"]
    assert {key: tpl[key] for key in NEW_KEYS} == {
        "single_place": True, "scene_transition": "cut", "narrator_slots": ["recap"], "line_words": [5, 17],
        "episode_words": [95, 125], "reaction_shots": [0, 1]}
    assert timing.episode_slots(tpl, 1) == ["hook", "body", "body", "body", "cliffhanger"]
    assert timing.episode_slots(tpl, 2) == ["recap", "hook", "body", "body", "cliffhanger"]  # re-pinned, as above
    # The keys are optional: no other shipped template has any (their prompts and timing are unchanged).
    for template_id in defaults.EPISODE_TEMPLATE_IDS:
        if template_id != "confrontation_50s_v2":
            assert not set(NEW_KEYS) & set(templates.load_episode_template(template_id)), template_id


@pytest.mark.parametrize("change, message", [
    # Plan 27 stage 2: the most one shot can speak is a 10 s shot's 22 words (was 8 s, 17).
    ({"line_words": [5, 23]}, "$.line_words: [5, 23] must lie within [1, 22]"),
    ({"line_words": [9, 5]}, "$.line_words: [9, 5] must satisfy 0 <= lo <= hi"),
    ({"episode_words": [130, 125]}, "$.episode_words: [130, 125] must satisfy 0 <= lo <= hi"),
    ({"narrator_slots": ["recap", "recap"]}, "$.narrator_slots: each slot at most once"),
    ({"narrator_slots": ["epilogue"]}, "'epilogue' is not one of"),
    ({"scene_transition": "dissolve"}, "'dissolve' is not one of"),
    ({"single_place": False}, "$.single_place: true or absent"),
])
def test_the_new_keys_are_cross_checked(change, message):
    errors = schemas.episode_template_errors(dict(copy.deepcopy(CONFRONTATION), **change))
    assert any(message in error for error in errors), errors


def test_single_place_caps_the_places_at_one():
    from clipping.aistory.steps.episode_common import EpisodeContext

    lock = {"episode_defaults": {"max_places": 2, "shots_per_scene": [2, 4], "hook_style": "insert_prop"}}
    defaults_of = EpisodeContext.episode_defaults.fget
    assert defaults_of(SimpleNamespace(style_lock=lock, template=CONFRONTATION)) == {
        "max_places": 1, "shots_per_scene": [1, 2], "hook_style": "insert_prop"}
    assert defaults_of(SimpleNamespace(style_lock=lock, template=SERIAL_V2))["max_places"] == 2
    v1 = templates.load_episode_template("serial_60s_v1")
    assert defaults_of(SimpleNamespace(style_lock=lock, template=v1)) is lock["episode_defaults"]


def test_the_narrator_speaks_in_the_recap_only_on_this_format():
    from clipping.aistory.steps import script

    def ec(template, narrator=True):
        return SimpleNamespace(narrator=narrator, template=template)

    for function, allowed in (("recap", True), ("hook", False), ("setup", False), ("peak", False),
                              ("cliffhanger", False)):
        assert script.narrator_in(ec(CONFRONTATION), {"function": function}) is allowed, function
        assert script.narrator_in(ec(SERIAL_V2), {"function": function}) is True  # every slot elsewhere
        assert script.narrator_in(ec(CONFRONTATION, narrator=False), {"function": function}) is False
    reply = {"hook": {"lines": [{"speaker": "narrator", "text": "Ce matin-là, tout bascule."}]},
             "recap": {"lines": [{"speaker": "narrator", "text": "Hier, Rouge a vu Nude cacher son capuchon."}]}}
    errors = script.narrator_errors(ec(CONFRONTATION), reply, {"hook": {"function": "hook"},
                                                               "recap": {"function": "recap"}})
    assert errors == ["$.hook.lines[0].speaker: the narrator speaks only in the recap on this format, never in the hook"]
    assert script.narrator_errors(ec(SERIAL_V2), reply, {"hook": {"function": "hook"}}) == []


def _shots(*scene_ids):
    return [{"shot_id": f"sh{i:02d}", "scene_id": sid} for i, sid in enumerate(scene_ids, start=1)]


def test_every_boundary_is_a_cut_on_this_template_and_unchanged_on_the_others():
    shots = _shots("s01", "s02", "s02", "s03")
    scenes = {"s01": {"place_id": "place_hall"}, "s02": {"place_id": "place_hall"}, "s03": {"place_id": "place_cour"}}
    cuts = timing.plan_transitions(shots, scenes, CONFRONTATION)
    assert [t["type"] for t in cuts] == ["cut", "cut", "cut"] and all(t["duration_s"] == 0.0 for t in cuts)
    # serial_60s_v2: a dissolve in the same place, a fade to black across a place change (spec 6.3).
    assert [t["type"] for t in timing.plan_transitions(shots, scenes, SERIAL_V2)] == ["dissolve", "cut", "fadeblack"]
    assert [t["type"] for t in timing.plan_transitions(shots, scenes, SERIAL_V2, cuts_only=True)] == ["cut"] * 3
    # The script's own estimate (no storyboard yet) predicts the same cuts.
    stubs = [{"place_id": "place_hall"}, {"place_id": "place_hall"}, {"place_id": "place_cour"}]
    assert timing._boundary_transitions(stubs, CONFRONTATION) == [("cut", 0.0), ("cut", 0.0)]
    assert timing._boundary_transitions(stubs, SERIAL_V2) == [("dissolve", 0.4), ("fadeblack", 0.4)]


def _scenes(template, ep, pick):
    out = []
    for slot in timing.episode_slots(template, ep):
        function = "setup" if slot == "body" else slot
        lo, hi = template["slots"][slot]["duration_s"]
        out.append({"function": function, "target_duration_s": pick(lo, hi)})
    return out


@pytest.mark.parametrize("ep", [1, 2])
@pytest.mark.parametrize("pick", [lambda lo, hi: lo, lambda lo, hi: (lo + hi) / 2, lambda lo, hi: hi])
def test_the_word_budgets_sum_inside_the_episodes_words(ep, pick):
    scenes = _scenes(CONFRONTATION, ep, pick)
    total = sum(scene["target_duration_s"] for scene in scenes)
    budgets = [timing.word_budget_v3(scene, CONFRONTATION, CONFRONTATION["episode_words"], total_s=total,
                                     native=True) for scene in scenes]
    lo, hi = CONFRONTATION["episode_words"]
    assert lo <= sum(b["words"][0] for b in budgets) <= sum(b["words"][1] for b in budgets) <= hi
    for budget in budgets:
        assert budget["line_words"] == [5, 17]
        assert 1 <= budget["lines"][0] <= budget["lines"][1] <= 4
        # Enough lines for the words: the high end fits in its lines at 17 words each.
        assert budget["words"][0] <= budget["lines"][1] * 17


def test_without_episode_words_the_budget_is_its_seconds_at_2_4_words():
    scene = {"function": "setup", "target_duration_s": 8.0}
    budget = timing.word_budget_v3(scene, SERIAL_V2)
    # 8 s x 2.4 x (1 - (0.35 + 0.6) / 8) = 16.9 -> 16; the low end three quarters of it.
    assert budget == {"words": [12, 16], "lines": [1, 2], "line_words": [5, 22]}
    assert timing.word_budget_v3(scene, SERIAL_V2, native=True)["line_words"] == [5, 17]
    # The v2 budget is untouched (RC-W3): 8 s of French body speech is 17 words, as before. Re-pinned on purpose
    # (plan 28 stage A2, DEC-305): serial_60s_v2's body slot starts at 10 s now, so an 8 s hint is priced at the
    # slot's floor -- 10 s, 22 words -- and the 8 s figure is checked where 8 s is still a body scene (v1's slot).
    assert timing.word_budget("setup", 8.0, "fr", SERIAL_V2) == timing.word_budget("setup", 10.0, "fr", SERIAL_V2) == 22
    assert timing.word_budget("setup", 8.0, "fr", templates.load_episode_template("serial_60s_v1")) == 17


# ------------------------------------------------------------ the suggestion rule (DEC-268)

def test_a_native_speech_profile_starts_on_the_confrontation_format_unless_one_is_named():
    manual = defaults.manual_speech_generation_profile()
    native = dict(manual, budget_profile=defaults.NATIVE_SPEECH_PROFILE)
    for profile in (manual, native):
        assert defaults.episode_template_for(profile) == "confrontation_50s_v2"
        assert defaults.episode_template_for(profile, "serial_60s_v2") == "serial_60s_v2"  # a choice, kept
    assert defaults.episode_template_for(defaults.quality_generation_profile()) == "serial_60s_v2"
    assert defaults.episode_template_for(defaults.default_generation_profile()) == "serial_60s_v1"
    # Not speaking natively (tier 1, or not v2): the pipeline's default, as before.
    assert defaults.episode_template_for(dict(manual, tier=1)) == "serial_60s_v2"
    assert defaults.episode_template_for({k: v for k, v in manual.items() if k != "pipeline"}) == "serial_60s_v1"


def test_the_store_and_the_wizard_follow_the_same_rule(tmp_path):
    from clipping.aistory.store import StoryStore

    stories = StoryStore(str(tmp_path / "stories"))
    manual = stories.create(language="fr", generation_profile=defaults.manual_speech_generation_profile(), now=NOW)
    assert manual["episode_template_id"] == "confrontation_50s_v2"
    chosen = stories.create(language="fr", generation_profile=defaults.manual_speech_generation_profile(),
                            episode_template_id="narrated_drama_60s_v2", now=NOW)
    assert chosen["episode_template_id"] == "narrated_drama_60s_v2"
    quality = stories.create(language="fr", generation_profile=defaults.quality_generation_profile(), now=NOW)
    assert quality["episode_template_id"] == "serial_60s_v2"

    formats = (STORY_SRC / "episodeTemplates.js").read_text(encoding="utf-8")
    assert ("  { id: 'confrontation_50s_v2', label: 'Confrontation 50 s (44–59)', pipeline: 'v2',\n"  # A2 re-pin
            "    help: 'One place, real time: a confrontation, one shot per spoken line.' },") in formats
    profiles = re.search(r"const NATIVE_SPEECH_PROFILES = \[([^\]]*)\]", formats).group(1)
    assert re.findall(r"'([a-z_]+)'", profiles) == list(defaults.NATIVE_SPEECH_PROFILES)
    assert ("return NATIVE_SPEECH_PROFILES.includes(budgetProfile) ? 'confrontation_50s_v2' : null"
            in formats)
    wizard = (STORY_SRC / "NewStoryWizard.jsx").read_text(encoding="utf-8")
    assert "const profileSuggestion = profileSuggestedTemplate(budgetProfile)" in wizard
    assert "const suggestedTemplate = profileSuggestion || styleSuggestedTemplate(chosenStyle, pipeline)" in wizard
    # Preselected, still a choice: the user's pick wins, and the pick or the suggestion is sent.
    assert ("const episodeTemplateId = episodeTemplateChoice || suggestedTemplate || "
            "pipelineDefaultTemplate(pipeline)") in wizard
    assert "episode_template_id: episodeTemplateChoice || suggestedTemplate || null," in wizard
    assert "' Suggested for native speech: one shot per line.'" in wizard
