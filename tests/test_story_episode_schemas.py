"""Tests for the AI Story episode templates and the script/storyboard document
schemas (AI Story phase 3, stage 1; spec 2.7, 2.8, 6.2, 6.3).

Three layers, in order:

1. The two shipped episode templates (``templates/episodes/*.json``) round-trip
   through ``templates.load_episode_template()`` and satisfy
   ``schemas.episode_template_errors()``; every cross-field rule rejects a
   mutated template with a named error.
2. ``episode_script_v1`` and ``storyboard_v1``: a complete minimal document
   validates; every cross-field rule of ``episode_script_errors``,
   ``episode_script_context_errors``, ``storyboard_errors`` and
   ``storyboard_context_errors`` rejects a precise mutation with a named
   error.
3. ``story_bible_v1.episode_template_id`` accepts any shipped episode
   template id and refuses an unshipped one; ``templates.load_episode_template``
   refuses a bad or unknown id before any path is built.

Stdlib + pytest only (DEC-012): this file runs in the CI environment. Fixtures
are built with small helper functions below, no fixture files on disk.
"""

from __future__ import annotations

import copy

import pytest

from clipping.aistory import defaults, schemas, templates

NOW = "2026-09-27T10:00:00+00:00"


def _mutate(doc, fn):
    """A deep copy of *doc* with *fn* applied in place; ``None`` = no change."""
    doc = copy.deepcopy(doc)
    if fn is not None:
        fn(doc)
    return doc


# ======================================================== 1. episode_template_v1

EXPECTED_EPISODE_TEMPLATE_IDS = ("serial_60s_v1", "serial_90s_v1")


def test_exactly_the_two_shipped_episode_template_ids():
    assert tuple(templates.list_episode_template_ids()) == EXPECTED_EPISODE_TEMPLATE_IDS
    assert tuple(sorted(defaults.EPISODE_TEMPLATE_IDS)) == EXPECTED_EPISODE_TEMPLATE_IDS


@pytest.mark.parametrize("template_id", EXPECTED_EPISODE_TEMPLATE_IDS)
def test_every_episode_template_passes_its_schema(template_id):
    tpl = templates.load_episode_template(template_id)
    assert schemas.episode_template_errors(tpl) == []
    assert tpl["template_id"] == template_id
    assert tpl["version"] == 1


def test_load_episode_template_returns_a_deep_copy_each_time():
    a = templates.load_episode_template("serial_60s_v1")
    a["slots"]["body"]["count"].append(99)
    b = templates.load_episode_template("serial_60s_v1")
    assert b["slots"]["body"]["count"] != a["slots"]["body"]["count"]


@pytest.mark.parametrize("bad_id", ["../x", "Serial", "serial_45s_v1", "serial_60s_v1/../secret", "nope", ""])
def test_load_episode_template_rejects_bad_or_unknown_ids(bad_id):
    with pytest.raises(KeyError):
        templates.load_episode_template(bad_id)


def _template_window_order_broken(doc):
    doc["target_s"] = doc["tighten_above_s"]


def _template_range_pair_broken(doc):
    doc["scenes"] = [12, 8]


def _template_slot_function_missing(doc):
    doc["slots"]["body"]["functions"] = ["setup", "rising", "peak"]


def _template_slot_function_duplicated(doc):
    doc["slots"]["hook"]["functions"] = ["hook", "recap"]


def _template_transitions_key_missing(doc):
    del doc["transitions_s"]["slideup"]


def _template_tail_peak_function_unknown(doc):
    doc["tail_peak_functions"] = ["not_a_function"]


def _template_pause_order_broken(doc):
    doc["pauses_s"]["tail_floor"] = 2.0


def _template_scene_count_infeasible_without_recap(doc):
    doc["slots"]["body"]["count"] = [1, 1]


def _template_scene_count_infeasible_with_recap(doc):
    doc["slots"]["recap"]["count"] = [10, 10]


TEMPLATE_BREAKS = {
    "target/tighten order": (_template_window_order_broken, "does not hold"),
    "reversed range pair": (_template_range_pair_broken, "must satisfy 0 <= lo <= hi"),
    "slot function missing": (_template_slot_function_missing, "must equal SCENE_FUNCTIONS"),
    "slot function duplicated": (_template_slot_function_duplicated, "claimed by more than one slot"),
    "transitions_s key missing": (_template_transitions_key_missing, "must equal TRANSITIONS"),
    "tail_peak_functions unknown": (_template_tail_peak_function_unknown, "is not a scene function"),
    "pause order broken": (_template_pause_order_broken, "does not hold"),
    "scene count infeasible without recap": (_template_scene_count_infeasible_without_recap, "without a recap"),
    "scene count infeasible with recap": (_template_scene_count_infeasible_with_recap, "with the recap scene"),
}


@pytest.mark.parametrize("label", list(TEMPLATE_BREAKS), ids=list(TEMPLATE_BREAKS))
def test_a_broken_episode_template_is_refused(label):
    mutate, keyword = TEMPLATE_BREAKS[label]
    doc = _mutate(templates.load_episode_template("serial_60s_v1"), mutate)
    errors = schemas.episode_template_errors(doc)
    assert any(keyword in e for e in errors), errors


def test_story_bible_episode_template_id_stays_an_enum_of_shipped_ids():
    schema = schemas.STORY_BIBLE_SCHEMA["properties"]["episode_template_id"]
    assert set(schema["enum"]) == set(defaults.EPISODE_TEMPLATE_IDS)


# ======================================================== 2a. episode_script_v1

CAST_IDS = ["char_kiwilo", "char_mangella"]
PLACE_ID = "place_beach_camp"
PLACE_VARIANTS = ["day", "night"]
PROP_ID = "prop_coconut_phone"


def _line(line_id, speaker, text="A short line here.", *, duration=1.0):
    return {
        "line_id": line_id, "speaker": speaker, "text": text, "emotion": "neutral",
        "delivery": "calm",
        "timing": {
            "source": "estimated", "duration_s": duration,
            "text_hash": "0123456789abcdef", "voice": None, "audio": None,
        },
    }


def _scene(scene_id, function, *, characters=None, lines=None, sfx_cues=None, props=None,
           on_screen_text=None, state="written", source="E2", summary="Something happens."):
    return {
        "scene_id": scene_id, "function": function, "place_id": PLACE_ID, "time_variant": "day",
        "characters": characters if characters is not None else ["char_kiwilo"],
        "props": props if props is not None else [],
        "summary": summary, "emotion": "neutral", "target_duration_s": 5.0,
        "lines": lines if lines is not None else [],
        "sfx_cues": sfx_cues if sfx_cues is not None else [],
        "on_screen_text": on_screen_text, "state": state, "source": source, "rev": 1,
    }


def _script(**changes):
    """A complete minimal valid script: ep 1, hook + 6 body scenes + cliffhanger."""
    body_functions = ["setup", "rising", "peak", "turn", "setup", "rising"]
    scenes = [_scene(
        "s01", "hook", characters=["char_kiwilo", "char_mangella"],
        lines=[_line("l01", "char_kiwilo")],
        sfx_cues=[{"at": "start", "cue": "waves_soft"}, {"at": "l01", "cue": "gasp_crowd"}],
    )]
    for i, fn in enumerate(body_functions, start=2):
        scenes.append(_scene(f"s0{i}", fn, characters=["char_mangella"], lines=[_line(f"l0{i}", "char_mangella")]))
    scenes.append(_scene("s08", "cliffhanger", characters=["char_kiwilo"], lines=[_line("l08", "char_kiwilo")]))

    doc = {
        "$schema": "episode_script_v1", "ep": 1, "title": "Test Episode", "language": "en",
        "template_id": "serial_60s_v1",
        "hook": {"on_screen_text": "A coconut phone rings out"},
        "scenes": scenes,
        "cliffhanger": {"scene_id": "s08", "reveal": "A shocking reveal.", "cut_to_black": True},
        "next_episode_teaser": "A short teaser for next time.",
        "timing": {
            "total_s": 60.0, "window_s": [55, 80], "target_s": 60, "state": "ok",
            "scenes": {
                s["scene_id"]: {"duration_s": 5.0, "tail_s": 0.6, "hold_s": 0.0, "state": "ok"} for s in scenes
            },
            "flags": [], "estimated_lines": 8, "measured_lines": 0,
        },
        "consistency_report": {"passed": True, "issues": [], "checked_rev": 1, "checked_at": NOW, "stale": False},
        "approved_anyway": None, "approved_at": None, "rev": 1,
        "created_at": NOW, "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def test_a_complete_minimal_script_validates():
    assert schemas.episode_script_errors(_script()) == []


def _swap_scene_ids(doc):
    doc["scenes"][0]["scene_id"], doc["scenes"][1]["scene_id"] = doc["scenes"][1]["scene_id"], doc["scenes"][0]["scene_id"]


def _s00_not_recap(doc):
    doc["scenes"][0]["scene_id"] = "s00"


def _recap_function_wrong_id(doc):
    doc["scenes"][0]["function"] = "recap"


def _cliffhanger_not_last(doc):
    doc["scenes"][-1]["function"], doc["scenes"][-2]["function"] = (
        doc["scenes"][-2]["function"], doc["scenes"][-1]["function"],
    )


def _duplicate_line_id(doc):
    doc["scenes"][1]["lines"][0]["line_id"] = doc["scenes"][0]["lines"][0]["line_id"]


def _speaker_not_in_scene(doc):
    doc["scenes"][1]["lines"][0]["speaker"] = "char_kiwilo"


def _sfx_at_unknown_line(doc):
    doc["scenes"][0]["sfx_cues"] = [{"at": "l99", "cue": "waves_soft"}]


def _cliffhanger_scene_id_wrong(doc):
    doc["cliffhanger"]["scene_id"] = "s01"


def _line_text_too_long(doc):
    doc["scenes"][0]["lines"][0]["text"] = " ".join(["word"] * 23)


def _summary_too_long(doc):
    doc["scenes"][0]["summary"] = " ".join(["word"] * 16)


def _hook_on_screen_text_too_long(doc):
    doc["hook"]["on_screen_text"] = " ".join(["word"] * 7)


def _teaser_too_long(doc):
    doc["next_episode_teaser"] = " ".join(["word"] * 16)


def _stub_scene_has_lines(doc):
    doc["scenes"][1]["state"] = "stub"


def _timing_scene_unknown_key(doc):
    doc["timing"]["scenes"]["s99"] = {"duration_s": 1.0, "tail_s": 0.1, "hold_s": 0.0, "state": "ok"}


def _line_duration_not_positive(doc):
    doc["scenes"][0]["lines"][0]["timing"]["duration_s"] = 0


SCRIPT_BREAKS = {
    "scene ids not increasing": (_swap_scene_ids, "does not strictly increase after"),
    "s00 not a recap scene": (_s00_not_recap, "must have function 'recap'"),
    "recap scene not s00": (_recap_function_wrong_id, "scene_id must be 's00'"),
    "cliffhanger not last": (_cliffhanger_not_last, "must have exactly one 'cliffhanger', last"),
    "duplicate line id": (_duplicate_line_id, "does not strictly increase after 'l01'"),
    "speaker not in scene": (_speaker_not_in_scene, "is not in the scene's characters"),
    "sfx at unknown line": (_sfx_at_unknown_line, "is not 'start' or a line id"),
    "cliffhanger scene_id wrong": (_cliffhanger_scene_id_wrong, "is not the last scene's id"),
    "line text too long": (_line_text_too_long, "expected at most 22"),
    "scene summary too long": (_summary_too_long, "expected at most 15"),
    "hook on_screen_text too long": (_hook_on_screen_text_too_long, "expected at most 6"),
    "teaser too long": (_teaser_too_long, "expected at most 15"),
    "stub scene has lines": (_stub_scene_has_lines, "a stub scene must have no lines"),
    "timing scene unknown key": (_timing_scene_unknown_key, "is not one of the episode's scene ids"),
    "line duration not positive": (_line_duration_not_positive, "must be > 0"),
}


@pytest.mark.parametrize("label", list(SCRIPT_BREAKS), ids=list(SCRIPT_BREAKS))
def test_a_broken_script_is_refused(label):
    mutate, keyword = SCRIPT_BREAKS[label]
    doc = _mutate(_script(), mutate)
    errors = schemas.episode_script_errors(doc)
    assert any(keyword in e for e in errors), errors


# ================================================ 2b. episode_script_context_errors

BASE_CONTEXT_KWARGS = dict(
    cast_ids=CAST_IDS, places={PLACE_ID: PLACE_VARIANTS}, prop_ids=[PROP_ID],
    sfx_cues=["waves_soft", "gasp_crowd"], narrator_enabled=False, max_places=2,
)


def test_a_script_in_its_story_context_validates():
    assert schemas.episode_script_context_errors(_script(), **BASE_CONTEXT_KWARGS) == []


def _ctx_characters(doc):
    doc["scenes"][0]["characters"] = ["char_unknown"]


def _ctx_place(doc):
    doc["scenes"][0]["place_id"] = "place_unknown"


def _ctx_time_variant(doc):
    doc["scenes"][0]["time_variant"] = "midnight"


def _ctx_props(doc):
    doc["scenes"][0]["props"] = ["prop_unknown"]


def _ctx_sfx(doc):
    doc["scenes"][0]["sfx_cues"] = [{"at": "start", "cue": "unknown_cue"}]


def _ctx_narrator(doc):
    doc["scenes"][0]["lines"][0]["speaker"] = "narrator"


def _ctx_max_places(doc):
    doc["scenes"][1]["place_id"] = "place_other"


SCRIPT_CONTEXT_BREAKS = {
    "character not in cast": (_ctx_characters, {}, "is not in the story's cast"),
    "place not in story": (_ctx_place, {}, "is not one of the story's places"),
    "time_variant not a known variant": (_ctx_time_variant, {}, "is not a variant of"),
    "prop not in story": (_ctx_props, {}, "is not in the story's props"),
    "sfx cue not in story": (_ctx_sfx, {}, "is not one of the story's sfx cues"),
    "narrator not enabled": (_ctx_narrator, {}, "'narrator' is not enabled"),
    "too many distinct places": (
        _ctx_max_places,
        {"places": {PLACE_ID: PLACE_VARIANTS, "place_other": ["day"]}, "max_places": 1},
        "more than max_places",
    ),
}


@pytest.mark.parametrize("label", list(SCRIPT_CONTEXT_BREAKS), ids=list(SCRIPT_CONTEXT_BREAKS))
def test_a_script_rejected_by_its_story_context(label):
    mutate, overrides, keyword = SCRIPT_CONTEXT_BREAKS[label]
    doc = _mutate(_script(), mutate)
    kwargs = {**BASE_CONTEXT_KWARGS, **overrides}
    errors = schemas.episode_script_context_errors(doc, **kwargs)
    assert any(keyword in e for e in errors), errors


# ============================================================ 3a. storyboard_v1

def _shot(n, scene_id, line_id, characters, *, framing="medium_single", camera_motion="hold"):
    shot_id = f"sh{n:02d}"
    tags = [f"@{c}" for c in characters] + [f"#{PLACE_ID}:day"]
    return {
        "shot_id": shot_id, "scene_id": scene_id, "order": n,
        "framing": framing, "camera_motion": camera_motion, "modifiers": [],
        "subject_tags": tags, "action": "Something happens on screen.",
        "lines": [line_id] if line_id else [],
        "image_prompt": "a fully resolved prompt", "negative_prompt": "no text, no watermark",
        "prompt_override": None,
        "reference_images": [f"characters/{characters[0]}/refs/portrait.png"] if characters else [],
        "consistency": "references", "duration_s": 3.0, "keep_still": False,
        "motion": {"type": camera_motion, "zoom_from": 1.0, "zoom_to": 1.1, "pan": "none"},
        "video_prompt": None,
        "assets": {"image": None, "video": None, "seed": None, "provider": None, "approved": False},
    }


SCENE_IDS = ["s01", "s02", "s03", "s04", "s05", "s06", "s07", "s08"]
SCENE_CHARACTERS = {
    "s01": ["char_kiwilo", "char_mangella"], "s02": ["char_mangella"], "s03": ["char_mangella"],
    "s04": ["char_mangella"], "s05": ["char_mangella"], "s06": ["char_mangella"], "s07": ["char_mangella"],
    "s08": ["char_kiwilo"],
}


def _storyboard(**changes):
    """One shot per scene, matching ``_script()``'s 8 scenes and 8 lines."""
    shots = [_shot(i, sid, f"l{i:02d}", SCENE_CHARACTERS[sid]) for i, sid in enumerate(SCENE_IDS, start=1)]
    doc = {
        "$schema": "storyboard_v1", "ep": 1,
        "shots": shots,
        "transitions": [{"after": s["shot_id"], "type": "cut", "duration_s": 0.0} for s in shots[:-1]],
        "scenes": {sid: {"source": "t1", "script_rev": 1, "stale": False} for sid in SCENE_IDS},
        "resolved_from": {},
        "approved_at": None, "rev": 1,
    }
    doc.update(changes)
    return doc


def test_a_complete_storyboard_validates():
    assert schemas.storyboard_errors(_storyboard()) == []


def test_a_shot_with_a_not_yet_timed_duration_is_not_flagged():
    doc = _mutate(_storyboard(), lambda d: d["shots"][0].__setitem__("duration_s", 0))
    assert schemas.storyboard_errors(doc) == []


def _sb_duplicate_shot_id(doc):
    doc["shots"][1]["shot_id"] = "sh01"


def _sb_order_mismatch(doc):
    doc["shots"][2]["order"] = 99


def _sb_scene_not_a_key(doc):
    doc["shots"][0]["scene_id"] = "s09"


def _sb_shots_not_contiguous(doc):
    doc["shots"][2]["scene_id"] = doc["shots"][0]["scene_id"]


def _sb_line_reused(doc):
    doc["shots"][1]["lines"] = ["l01"]


def _sb_transition_unknown_shot(doc):
    doc["transitions"][0]["after"] = "sh99"


def _sb_transition_on_last_shot(doc):
    doc["transitions"].append({"after": "sh08", "type": "cut", "duration_s": 0.0})


def _sb_transition_duplicated(doc):
    doc["transitions"].append({"after": "sh01", "type": "dissolve", "duration_s": 0.4})


def _sb_transition_non_cut_within_scene(doc):
    # merge sh01/sh02 into the same scene (still contiguous), then make the
    # transition between them non-cut: spec 6.3 only allows a cut inside a scene.
    doc["shots"][1]["scene_id"] = doc["shots"][0]["scene_id"]
    doc["transitions"][0]["type"] = "dissolve"
    doc["transitions"][0]["duration_s"] = 0.4


def _sb_shot_too_short(doc):
    doc["shots"][0]["duration_s"] = 0.1


def _sb_motion_mismatch(doc):
    doc["shots"][0]["motion"]["type"] = "pull_out"


STORYBOARD_BREAKS = {
    "duplicate shot id": (_sb_duplicate_shot_id, "expected 'sh02'"),
    "order mismatch": (_sb_order_mismatch, "expected 3"),
    "scene not a key of scenes": (_sb_scene_not_a_key, "is not a key of scenes"),
    "shots of a scene not contiguous": (_sb_shots_not_contiguous, "are not contiguous"),
    "line reused across shots": (_sb_line_reused, "appears in more than one shot"),
    "transition names unknown shot": (_sb_transition_unknown_shot, "is not an existing shot"),
    "transition on the last shot": (_sb_transition_on_last_shot, "cannot have a transition"),
    "transition duplicated for a shot": (_sb_transition_duplicated, "already has a transition"),
    "non-cut transition inside a scene": (_sb_transition_non_cut_within_scene, "only cut inside a scene"),
    "shot below the minimum length": (_sb_shot_too_short, "the minimum shot length"),
    "motion type mismatch": (_sb_motion_mismatch, "does not match camera_motion"),
}


@pytest.mark.parametrize("label", list(STORYBOARD_BREAKS), ids=list(STORYBOARD_BREAKS))
def test_a_broken_storyboard_is_refused(label):
    mutate, keyword = STORYBOARD_BREAKS[label]
    doc = _mutate(_storyboard(), mutate)
    errors = schemas.storyboard_errors(doc)
    assert any(keyword in e for e in errors), errors


# ================================================== 3b. storyboard_context_errors

def test_a_storyboard_in_its_script_context_validates():
    errors = schemas.storyboard_context_errors(_storyboard(), _script(), shots_per_scene=(1, 1))
    assert errors == []


def _sb_ctx_unknown_scene(doc):
    doc["scenes"]["s09"] = {"source": "t1", "script_rev": 1, "stale": False}


def _sb_ctx_line_not_in_scene(doc):
    doc["shots"][0]["lines"] = ["l99"]


def _sb_ctx_subject_tag(doc):
    doc["shots"][0]["subject_tags"] = ["@char_unknown"]


STORYBOARD_CONTEXT_BREAKS = {
    "scene not in script": (_sb_ctx_unknown_scene, {}, "not a scene of the script"),
    "line not in scene": (_sb_ctx_line_not_in_scene, {}, "does not belong to scene"),
    "subject tag not in scene": (_sb_ctx_subject_tag, {}, "is not in the scene's"),
    "shot count outside range": (None, {"shots_per_scene": (2, 3)}, "expected 2-3"),
}


@pytest.mark.parametrize("label", list(STORYBOARD_CONTEXT_BREAKS), ids=list(STORYBOARD_CONTEXT_BREAKS))
def test_a_storyboard_rejected_by_its_script_context(label):
    mutate, overrides, keyword = STORYBOARD_CONTEXT_BREAKS[label]
    doc = _mutate(_storyboard(), mutate)
    kwargs = {"shots_per_scene": (1, 1), **overrides}
    errors = schemas.storyboard_context_errors(doc, _script(), **kwargs)
    assert any(keyword in e for e in errors), errors


# ======================================================== 4. story_bible_v1 tie-in

def _bible(**changes):
    doc = {
        "$schema": "story_bible_v1", "story_id": "0123456789ab", "title": "", "language": "fr",
        "seed_text": None, "concept_id": None, "concept": None, "logline": None, "premise": None,
        "tone": None, "genre_tags": [], "world": None, "themes_and_values": [], "audience": None,
        "why_come_back": [], "cast_ids": [], "place_ids": [], "prop_ids": [],
        "style_template_id": None, "episode_template_id": "serial_60s_v1",
        "generation_profile": {"tier": 1, "route": "auto", "consistency_mode": "references", "budget_profile": "free"},
        "narrator": {"enabled": False, "voice": None},
        "approvals": {"concept": None, "bible": None, "style": None, "cast": None, "places": None, "season": None},
        "status": "draft", "created_at": NOW, "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def test_story_bible_accepts_any_shipped_episode_template_id():
    assert schemas.story_bible_errors(_bible(episode_template_id="serial_90s_v1")) == []
    assert schemas.story_bible_errors(_bible(episode_template_id="serial_60s_v1")) == []


def test_story_bible_refuses_an_unshipped_episode_template_id():
    assert schemas.story_bible_errors(_bible(episode_template_id="serial_45s_v1")) != []
