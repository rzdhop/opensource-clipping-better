"""Plan 28 stage A2: a native-speech episode's plan always fits its window.

- ``timing.scene_plan`` never plans a scene whose clips sum past its slot: a
  narrator and a character clip that cannot fit is planned the narrator
  alone, a narrator clip that cannot fit the characters' cheapest exchange,
  and a scene no clip fits is a ``timing.PlanError``;
- ``timing.fit_episode_plans`` holds an episode's clips inside its window: on
  a format that is not a narrated one, a plain body scene keeps the narrator
  alone (the peak and the turn keep their character line), then a scene's
  clips are held a second under what they sum to (``clip_cap_s``); the
  script step stores what it changed, so a scene's plan recomputes the same;
- ``timing.episode_slots`` with the story's lengths asks fewer body scenes
  when even the fitted default cannot fit;
- the fit matrix: every shipped v2 format x every link's lengths (manual,
  Veo, kling, seedance, ltx) x the narrator on and off x every style (its
  cliffhanger cutting to black or not, its scene clamp) x episode 1 and 2 x
  French and English fits its window (``timing.plan_floor_preview``), so the
  plan 28 stage A1 refusal never fires on a shipped combination.

Stdlib + pytest (DEC-012); offline and hermetic.
"""

from __future__ import annotations

import copy
import itertools

import pytest

import test_story_episode_steps as eps
from clipping.aistory import defaults, templates, timing
from clipping.aistory.steps import episode_common, script as script_step
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_plan_feasibility import _native_story

FR = "fr"
V2_FORMATS = ("serial_60s_v2", "serial_90s_v2", "narrated_drama_60s_v2", "confrontation_50s_v2")
# Each link's lengths inside the native 5-10 s shot window (clips.link_lengths): an upload is planned at
# native_speech.SPEECH_LENGTHS.
LINK_LENGTHS = {"manual": (6, 8), "veo": (6, 8), "kling": (5, 10), "seedance": (5, 6, 7, 8, 9, 10), "ltx": (6, 8, 10)}


def _scene(function, scene_id="s02", *, characters=("char_rida", "char_marie"), **extra):
    return dict({"scene_id": scene_id, "function": function, "place_id": "place_a", "characters": list(characters),
                 "emotion": "neutral", "target_duration_s": 10.0, "lines": []}, **extra)


def _clips(plan):
    return [shot["clip_s"] for shot in plan["shots"]]


def _with_body(template, lo, hi):
    tight = copy.deepcopy(template)
    tight["slots"]["body"]["duration_s"] = [lo, hi]
    return tight


# ---------------------------------------------------- (a) a scene never stores clips past its slot

def test_a_narrator_and_a_character_line_that_cannot_share_an_11s_slot_on_6_8_is_the_narrator_alone():
    # 6 + 6 s is the cheapest narrator-and-character shape on a 6/8 s link: past 11 s, so the scene is planned
    # again with the narrator alone -- one 8 s clip -- never stored 1 s over its slot (A1 found 12 s in [5, 11]).
    template = _with_body(templates.load_episode_template("serial_60s_v2"), 5.0, 11.0)
    plan = timing.scene_plan(template, _scene("setup", characters=("char_rida",)), lang=FR, native=True,
                             narrator=True, narrator_provider="edge", speakers={"char_rida": None},
                             speech_lengths=(6, 8), silent_lengths=(6, 8))
    assert plan["slot_s"] == [5.0, 11.0]
    assert [line["kind"] for line in plan["lines"]] == ["narrator"] and _clips(plan) == [8]
    assert sum(_clips(plan)) <= plan["slot_s"][1]
    # The same slot without the narrator: one exchange of the two characters, one 8 s clip.
    quiet = timing.scene_plan(template, _scene("setup"), lang=FR, native=True, narrator=False,
                              speakers={"char_rida": None, "char_marie": None}, speech_lengths=(6, 8))
    assert _clips(quiet) == [8] and {line["kind"] for line in quiet["lines"]} == {"character"}


def test_a_narrator_clip_no_slot_holds_falls_back_to_the_characters_cheapest_exchange():
    # The narrator's silent link sells 8 s only, the speech link 5 s: a 6 s hook holds the character's 5 s clip.
    template = copy.deepcopy(templates.load_episode_template("serial_60s_v2"))
    template["slots"]["hook"]["duration_s"] = [5.0, 6.0]
    plan = timing.scene_plan(template, _scene("hook", "s01"), lang=FR, native=True, narrator=True,
                             narrator_provider="edge", speakers={"char_rida": None, "char_marie": None},
                             speech_lengths=(5, 10), silent_lengths=(8,))
    assert [line["kind"] for line in plan["lines"]] == ["character"] and _clips(plan) == [5]


def test_a_scene_no_clip_fits_is_a_plan_error_with_its_arithmetic():
    template = copy.deepcopy(templates.load_episode_template("serial_60s_v1"))  # a 1.5-3.5 s hook
    with pytest.raises(timing.PlanError) as caught:
        timing.scene_plan(template, _scene("hook", "s01"), lang=FR, native=True, narrator=False,
                          speakers={"char_rida": None, "char_marie": None}, speech_lengths=(6, 8))
    error = caught.value
    assert (error.scene_id, error.function, error.need_s, error.slot_hi_s) == ("s01", "hook", 6.0, 3.5)
    assert isinstance(error, ValueError)
    assert timing.plan_slot_refusal(1, error.function, error.need_s, error.slot_hi_s) == (
        "Episode 1 cannot fit: its hook needs at least 6 s of clips on this link, more than the 3.5 s this format "
        "gives it. Pick a format that fits, or let the app choose one.")
    # The preview says so for the whole format (stage A4 hides it from a native story's choices).
    preview = timing.plan_floor_preview(template, 1, ((6, 8), (6, 8)), False)
    assert preview["unplannable"] == {"function": "hook", "need_s": 6.0, "slot_hi_s": 3.5}


def test_a_scene_s_clip_cap_plans_it_as_a_lower_slot_top_would_and_keeps_its_slot():
    template = templates.load_episode_template("serial_60s_v2")
    scene = _scene("rising")
    kwargs = dict(lang=FR, native=True, narrator=False, speakers={"char_rida": None, "char_marie": None},
                  speech_lengths=(6, 8))
    assert _clips(timing.scene_plan(template, scene, **kwargs)) == [8, 6]
    capped = timing.scene_plan(template, dict(scene, clip_cap_s=13.0), **kwargs)
    assert capped["slot_s"] == [10.0, 16.0] and sum(_clips(capped)) <= 13
    lower = _with_body(template, 10.0, 13.0)
    assert {k: v for k, v in capped.items() if k != "slot_s"} == {
        k: v for k, v in timing.scene_plan(lower, scene, **kwargs).items() if k != "slot_s"}
    # Off native the cap is not read: a TTS plan is what it was, byte for byte.
    tts = dict(kwargs, native=False)
    assert timing.scene_plan(template, dict(scene, clip_cap_s=13.0), **tts) == timing.scene_plan(template, scene,
                                                                                                    **tts)


# ---------------------------------------------------- (b) the episode's fit

def _plan_of(template, lengths=(6, 8), *, narrator, lang=FR, style_lock=None):
    def plan_of(scene):
        speaks = narrator and (template.get("narrator_slots") is None
                               or timing.slot_name(scene["function"], template) in template["narrator_slots"])
        return timing.scene_plan(template, scene, lang=lang, native=True, narrator=speaks,
                                 narrator_provider="edge" if speaks else None,
                                 speakers={"char_rida": None, "char_marie": None}, style_lock=style_lock,
                                 speech_lengths=lengths, silent_lengths=lengths)
    return plan_of


def _episode(functions):
    return [_scene(function, f"s{k + 1:02d}") for k, function in enumerate(functions)]


def test_with_the_narrator_on_a_plain_body_scene_keeps_the_narrator_alone_and_the_peak_and_turn_their_line():
    template = templates.load_episode_template("serial_60s_v2")
    scenes = _episode(["hook", "setup", "rising", "peak", "turn", "cliffhanger"])
    plan_of = _plan_of(template, narrator=True)
    # Unfitted: the narrator's hook and cliffhanger (8 s each) and four narrator-and-character scenes (8 + 8 s):
    # 80 s against 75 s.
    assert sum(sum(_clips(plan_of(scene))) for scene in scenes) == 80

    fit = timing.fit_episode_plans(template, scenes, plan_of, window_hi=75)

    assert fit["fits"] and fit["total_s"] == 72
    assert fit["changes"] == {"s02": {"character_line": False}}  # the setup only: one drop is enough
    kinds = {sid: [line["kind"] for line in plan["lines"]] for sid, plan in fit["plans"].items()}
    assert kinds["s02"] == ["narrator"]
    assert kinds["s03"] == kinds["s04"] == kinds["s05"] == ["narrator", "character"]
    assert all("character_line" not in scene for scene in scenes)  # the caller's scenes are never mutated


def test_a_narrated_format_keeps_its_beat_sheet_s_character_lines_and_is_held_by_its_clips():
    template = templates.load_episode_template("narrated_drama_60s_v2")
    scenes = _episode(["hook", "setup", "rising", "peak", "turn", "cliffhanger"])
    for scene in scenes[1:5]:
        scene["character_line"] = True  # E1 gave all four body scenes a character line: 8 + 4 x 16 + 8 = 80 s
    fit = timing.fit_episode_plans(template, scenes, _plan_of(template, narrator=True), window_hi=78)
    assert fit["fits"] and fit["total_s"] <= 78
    assert all("character_line" not in change for change in fit["changes"].values())
    assert set(fit["changes"]) == {"s02"} and "clip_cap_s" in fit["changes"]["s02"]
    assert [line["kind"] for line in fit["plans"]["s02"]["lines"]] == ["narrator", "character"]


def test_the_clip_cap_holds_the_least_watched_scenes_first_and_recomputes_the_same_plan():
    # English pays more words a second: a 10-16 s body scene plans 8 + 8 s on Veo, 80 s an episode on serial_60s_v2.
    template = templates.load_episode_template("serial_60s_v2")
    scenes = _episode(["hook", "setup", "rising", "peak", "turn", "cliffhanger"])
    plan_of = _plan_of(template, narrator=False, lang="en")
    fit = timing.fit_episode_plans(template, scenes, plan_of, window_hi=75, end_card_s=0.6)
    assert fit["fits"] and fit["total_s"] == 74.6
    # The setup and the rising first (16 -> 14 s), then the hook (8 -> 6 s); the peak and the turn untouched.
    assert fit["changes"] == {"s02": {"clip_cap_s": 15.0}, "s03": {"clip_cap_s": 15.0}, "s01": {"clip_cap_s": 7.0}}
    assert [sum(_clips(fit["plans"][scene["scene_id"]])) for scene in scenes] == [6, 14, 14, 16, 16, 8]
    for scene in scenes:
        changed = dict(scene, **fit["changes"].get(scene["scene_id"], {}))
        assert plan_of(changed) == fit["plans"][scene["scene_id"]]  # what the script step stores recomputes
        assert sum(_clips(fit["plans"][scene["scene_id"]])) >= timing.slot_range(scene, template)[0]


def test_a_plan_that_cannot_fit_even_held_is_returned_as_cheap_as_it_got():
    template = templates.load_episode_template("serial_60s_v2")
    scenes = _episode(["hook", "setup", "rising", "peak", "turn", "cliffhanger"])
    fit = timing.fit_episode_plans(template, scenes, _plan_of(template, narrator=False), window_hi=30)
    assert not fit["fits"] and fit["total_s"] == 36  # one 6 s clip a scene: the cheapest there is


def test_the_scene_count_follows_the_floor_only_when_the_default_cannot_fit():
    template = templates.load_episode_template("serial_60s_v2")
    lengths = ((6, 8), (6, 8))
    assert timing.episode_slots(template, 1, lengths=lengths, narrator=True) == timing.episode_slots(template, 1)
    tight = dict(copy.deepcopy(template), window_s=[20, 34], target_s=30, tighten_above_s=32)
    # 4 body scenes at one 6 s clip each is 36 s: one fewer (30 s) fits.
    assert timing.episode_slots(tight, 1, lengths=lengths) == ["hook", "body", "body", "body", "cliffhanger"]
    assert timing.episode_slots(tight, 1) == timing.episode_slots(template, 1)  # without lengths: as before
    assert timing.plan_floor_preview(tight, 1, lengths, False)["scenes"] == 5


# ---------------------------------------------------- (c) the script step stores the fit

def test_the_script_step_stores_what_the_fit_changed_and_the_writer_s_plan_is_the_stored_one(store):
    # serial_60s_v2 with the narrator on, Veo: four body scenes of a narrator and a character line do not fit.
    story_id = _native_story(store, "serial_60s_v2")
    ctx, _log = eps._ctx(store, story_id)
    ec = episode_common.load_episode_context(ctx)
    assert script_step.beat_sheet_slots(ec) == ["hook"] + ["body"] * 4 + ["cliffhanger"]
    reply = dict(eps._e1_reply_for(ec.template), spine=dict(eps.V3_SPINE))
    script = script_step.skeleton(ec, now=eps.NOW)

    script_step.apply_e1(ec, script, reply)

    window_hi = ec.template["window_s"][1]
    assert timing.plan_clip_floor_s(script, ec.template) <= window_hi
    assert script_step.plan_fit_refusal(ec, script) is None
    dropped = [scene for scene in script["scenes"] if scene.get("character_line") is False]
    assert dropped and all(scene["function"] in ("setup", "rising") for scene in dropped)
    for scene in script["scenes"]:
        stored = copy.deepcopy(scene["line_plan"])
        script_step.current_plan(ec, script, scene)  # what E2v3/E3v3 read when they write the scene
        assert scene["line_plan"] == stored  # recomputed from the stored fields: the same plan
        assert sum(shot["clip_s"] for shot in stored["shots"]) <= scene["slot_s"][1]


# ---------------------------------------------------- (d) the fit matrix

def _style_cases():
    """Every shipped style's ``(cliffhanger cuts to black, its scene clamp)``, each distinct case once (the style
    named with it)."""
    cases = {}
    for style_id in templates.list_style_ids():
        lock = templates.load_style(style_id)
        episode_defaults = lock.get("episode_defaults") or {}
        key = (episode_defaults.get("cliffhanger_style") == "cut_to_black",
               tuple(sorted((episode_defaults.get("scene_clamp_s") or {}).items())))
        cases.setdefault(key, (style_id, lock))
    return [(style_id, lock, cut) for (cut, _clamp), (style_id, lock) in cases.items()]


STYLE_CASES = _style_cases()


def test_the_styles_cover_both_cliffhangers_and_a_scene_clamp():
    assert {cut for _style, _lock, cut in STYLE_CASES} == {True, False}
    assert any((lock.get("episode_defaults") or {}).get("scene_clamp_s") for _style, lock, _cut in STYLE_CASES)


@pytest.mark.parametrize("template_id", V2_FORMATS)
def test_every_link_narrator_style_and_episode_fits_the_window(template_id):
    """The fit matrix (plan 28 stage A2): no shipped combination is refused. Speech and silent links are
    crossed with the narrator on (its silent clips are the narrator's); without it only the speech link plans."""
    template = templates.load_episode_template(template_id)
    window_hi = template["window_s"][1]
    refused = []
    combos = 0
    for (speech, silent), narrator, (style_id, lock, cut), ep, lang in itertools.product(
            itertools.product(LINK_LENGTHS, LINK_LENGTHS), (True, False), STYLE_CASES, (1, 2), (FR, "en")):
        if not narrator and silent != speech:
            continue
        combos += 1
        preview = timing.plan_floor_preview(template, ep, (LINK_LENGTHS[speech], LINK_LENGTHS[silent]), narrator,
                                            lang=lang, style_lock=lock, end_card=cut)
        if "unplannable" in preview or preview["floor_s"] > window_hi + 1e-6:
            refused.append((speech, silent, narrator, style_id, ep, lang, preview["floor_s"]))
    assert combos == 30 * len(STYLE_CASES) * 2 * 2
    assert refused == [], refused


@pytest.mark.parametrize("template_id", V2_FORMATS)
def test_a_clamped_style_s_tender_scenes_still_fit_once_fitted(template_id):
    """family_3d raises a peak or tender scene's slot top to 12 s: an episode of tender scenes (the hook and
    the cliffhanger too) on every link still fits once the episode's fit holds it."""
    template = templates.load_episode_template(template_id)
    lock = templates.load_style("family_3d")
    for ep, lengths, narrator in itertools.product((1, 2), LINK_LENGTHS.values(), (True, False)):
        slots = timing.episode_slots(template, ep, lengths=(lengths, lengths), narrator=narrator, style_lock=lock,
                                     end_card=True)
        functions = [template["slots"][slot]["functions"][0] for slot in slots]
        scenes = [dict(scene, emotion="tender") for scene in _episode(functions)]
        end_card = template["end_card_s"] - template["transitions_s"]["fadeblack"]
        fit = timing.fit_episode_plans(template, scenes, _plan_of(template, lengths, narrator=narrator,
                                                                  style_lock=lock),
                                       window_hi=template["window_s"][1], end_card_s=end_card)
        assert fit["fits"], (template_id, ep, lengths, narrator, fit["total_s"])


@pytest.mark.parametrize("template_id", V2_FORMATS)
def test_on_6_8_s_links_in_french_without_the_narrator_no_plan_needs_the_fit(template_id):
    """The human's own path (an upload or Veo, French, no narrator: plan 28 stage B1's new story): the
    templates' own slots fit, so the episode's fit changes nothing (DEC-305's re-slot)."""
    template = templates.load_episode_template(template_id)
    for ep, cut in itertools.product((1, 2), (True, False)):
        preview = timing.plan_floor_preview(template, ep, ((6, 8), (6, 8)), False, end_card=cut)
        assert preview["fitted"] == [], (template_id, ep, cut, preview)
        assert len(timing.episode_slots(template, ep, lengths=((6, 8), (6, 8)), end_card=cut)) == preview["scenes"]
        assert preview["scenes"] == len(timing.episode_slots(template, ep))  # the template's default count
        assert template["window_s"][0] <= preview["floor_s"] <= template["window_s"][1], preview


def test_the_shipped_formats_are_the_v2_ones_the_matrix_covers():
    assert set(V2_FORMATS) == {tid for tid in defaults.EPISODE_TEMPLATE_IDS if tid.endswith("_v2")}
