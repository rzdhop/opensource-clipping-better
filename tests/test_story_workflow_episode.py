"""The episode rules the API and the CLI share (``clipping/aistory/workflow.py``;
AI Story phase 3, stage 8; spec 3 steps 8-9, 9.1-9.2).

Called directly on a real ``StoryStore`` under ``tmp_path``, seeded with the
stage-6 fixture story (``test_story_episode_steps``: French, ready, three
characters, two places, a prop, an eight-episode arc, a locked fruit_drama
style) whose episode 1 is written by the real ``script`` runner on a fake LLM.
What is covered here: the phase grammar (the episode steps, approvals and
regenerate targets left the later phases), the preconditions of an episode
step, the closed parameter lists, the approval rules of both episode
documents, the script and storyboard edit rules, the episode length a story
may pick, the calls an episode step would make, and the episode view the
story page and the episode page read. ``story.json`` never changes (RC-E2).

Stdlib + pytest only: runs in the CI environment (DEC-012). ``workflow`` is
imported through a fixture, so against the parent commit each test fails on
its own.
"""

from __future__ import annotations

import copy
import importlib
import os
from pathlib import Path

import pytest

from clipping.aistory import shots, templates, timing
from clipping.providers.errors import ProviderError

from test_story_episode_steps import (  # noqa: F401 -- hermetic (autouse) and store are fixtures
    ALL_SCENES,
    BROCCOLIA,
    E1_REPLY,
    E3_FULL,
    E4_ISSUES,
    E4_PASSED,
    KIWILO,
    MANGELLA,
    NOW,
    FakeLLM,
    Log,
    _failed,
    _new,
    _ready_story,
    _run,
    _scene,
    _script,
    _script_llm,
    _story_bytes,
    _storyboard,
    e2_reply,
    hermetic,
    store,
    t1_reply,
)

LATER = "2026-09-27T11:00:00+00:00"
LATEST = "2026-09-27T12:00:00+00:00"
DOWN = ProviderError("every provider failed", [("gemini/gemini-test", "HTTP 503")])


@pytest.fixture
def wf():
    return importlib.import_module("clipping.aistory.workflow")


def _refused(wf, code, call, *args, **kwargs):
    with pytest.raises(wf.WorkflowError) as info:
        call(*args, **kwargs)
    assert info.value.code == code, info.value.detail
    return info.value.detail


def _text(detail) -> str:
    """A refusal's detail as one text: a sentence, or its message and errors."""
    if isinstance(detail, dict):
        return " | ".join([str(detail.get("message"))] + [str(error) for error in detail.get("errors") or []])
    return str(detail)


def _passed(store):
    """A ready story whose episode 1 is written and passed its check."""
    story_id = _ready_story(store)
    _run(_new().script, store, story_id, llm=_script_llm(E4=[E4_PASSED]))
    return story_id


def _issues(store):
    """A ready story whose episode 1 is written; its check found two issues."""
    story_id = _ready_story(store)
    _run(_new().script, store, story_id, llm=_script_llm())
    return story_id


def _partial(store):
    """A ready story whose episode 1 has its beat sheet and every body scene but
    s04; the framing parts and the check never ran."""
    story_id = _ready_story(store)
    queue = [e2_reply, e2_reply, DOWN, e2_reply, e2_reply, e2_reply]
    _failed(_new().script, store, story_id, llm=_script_llm(E2=queue, E3=[DOWN], E4=[]))
    return story_id


def _boarded(wf, store):
    """_passed, then a fast storyboard."""
    story_id = _passed(store)
    wf.build_fast_storyboard(store, store.get(story_id), 1, now=NOW, on_log=Log())
    return story_id


def _approvals(store, story_id):
    story = store.get(story_id)
    return story["approvals"], story["status"]


def _episode_file(store, story_id, name):
    return Path(store.episode_dir(story_id, 1)) / name


def _shots_of(board, sid):
    return [shot for shot in board["shots"] if shot["scene_id"] == sid]


# ================================================================== grammar

def test_the_episode_steps_approvals_and_targets_left_the_later_phases(wf):
    assert wf.PHASE3_STEPS == ("script", "storyboard")
    assert wf.LATER_STEPS == ("assets", "render", "metadata", "memory", "feedback", "propose-next", "rerender",
                              "fast-track", "import")
    assert wf.LATER_APPROVALS == ("assets",)
    # shot:<ep>:<shid> (image, phase 4) and shot:<ep>:<shid>:video (phase 6) are still to come.
    assert wf.LATER_TARGETS == ("shot", "line", "metadata")
    assert wf.SCRIPT_PARAMS == ("measure_voices",) and wf.STORYBOARD_PARAMS == ("fast",)
    assert not wf.is_later_approval("script:1") and not wf.is_later_approval("storyboard:1")
    assert wf.is_later_approval("assets:1")
    assert "episode_template_id" in wf.PATCH_FIELDS


def test_the_episode_targets_joined_the_one_regenerate_grammar(wf):
    from clipping.aistory.steps import regenerate

    for target, parsed in (("scene:1:s03", ("scene", 1, "s03")), ("hook:1", ("hook", 1)),
                           ("cliffhanger:2", ("cliffhanger", 2)), ("teaser:8", ("teaser", 8)),
                           ("shot:1:sh05:plan", ("shot", 1, "sh05"))):
        assert wf.check_regenerate_target(target) is None, target
        assert regenerate.parse_target(target) == parsed, target
    assert set(regenerate.EPISODE_TARGETS) <= set(regenerate.ENTITY_TARGETS)
    for target in ("shot:1:sh03", "shot:1:sh03:video", "line:1:l04", "metadata:1:tiktok"):
        assert "later phase" in _refused(wf, "later_phase", wf.check_regenerate_target, target)
    for target in ("scene:1:s3", "scene:0:s03", "hook:x", "teaser:1:x", "cliffhanger:"):
        detail = _refused(wf, "invalid", wf.check_regenerate_target, target)
        assert "scene:<ep>:<scene_id>" in detail and "shot:<ep>:<shot_id>:plan" in detail


# ============================================================ preconditions

def test_an_episode_step_needs_a_ready_story_an_episode_of_the_season_and_the_recap(wf, store):
    story_id = _ready_story(store)
    story = store.get(story_id)

    assert wf.episode_context(store, story, 1, step="script").ep == 1
    assert "send its number as ep" in _refused(wf, "invalid", wf.episode_context, store, story, None, step="script")
    assert "send its number as ep" in _refused(wf, "invalid", wf.episode_context, store, story, True, step="script")
    for ep in (0, 9):
        assert _refused(wf, "invalid", wf.episode_context, store, story, ep, step="script") == (
            f"The season plans episodes 1 to 8; there is no episode {ep}.")
    detail = _refused(wf, "conflict", wf.episode_context, store, story, 2, step="script")
    assert "recap of episode 1" in detail and "phase 5" in detail
    # A regenerate works on a script that exists: no recap asked.
    assert wf.episode_context(store, story, 2, step="script", require_recap=False).ep == 2

    with_recap = _ready_story(store, recaps={"ep01": "Kiwilo et Mangella se sont alliés en secret."})
    assert wf.episode_context(store, store.get(with_recap), 2, step="script").ep == 2

    store.update(story_id, lambda doc: doc["approvals"].update(season=None), now=NOW)
    detail = _refused(wf, "conflict", wf.episode_context, store, store.get(story_id), 1, step="script")
    assert detail == "The story is not ready yet: approve the cast, the places and the season first."


def test_the_episode_step_parameters_are_closed_lists(wf):
    assert wf.script_request({}) is False and wf.script_request({"measure_voices": True}) is True
    assert wf.script_request({"measure_voices": False}) is False
    for params in ({"measure": True}, {"measure_voices": "yes"}, {"fast": True}):
        _refused(wf, "invalid", wf.script_request, params)
    assert wf.storyboard_request({}) is False and wf.storyboard_request({"fast": True}) is True
    for params in ({"fast": 1}, {"measure_voices": True}):
        _refused(wf, "invalid", wf.storyboard_request, params)


def test_the_storyboard_needs_a_complete_script(wf, store):
    story_id = _ready_story(store)
    ec = wf.episode_context(store, store.get(story_id), 1, step="storyboard")
    assert "has no script yet" in _refused(wf, "conflict", wf.require_complete_script, ec)

    story_id = _partial(store)
    ec = wf.episode_context(store, store.get(story_id), 1, step="storyboard")
    detail = _refused(wf, "conflict", wf.require_complete_script, ec)
    assert "not complete" in detail and "s04" in detail and "hook" in detail and "teaser" in detail
    assert "not complete" in _refused(wf, "conflict", wf.build_fast_storyboard, store, store.get(story_id), 1,
                                      now=NOW, on_log=Log())
    assert _storyboard(store, story_id) is None


# ================================================================ approvals

def test_a_script_is_approved_only_complete_and_freshly_checked(wf, store):
    m = _new()
    story_id = _ready_story(store)
    before = _story_bytes(store, story_id)
    assert _refused(wf, "conflict", wf.approve_script, store, story_id, 1, now=LATER) == (
        "Episode 1 has no script yet: write it first (the script step).")

    queue = [e2_reply, e2_reply, DOWN, e2_reply, e2_reply, e2_reply]
    _failed(m.script, store, story_id, llm=_script_llm(E2=queue, E4=[]))
    detail = _refused(wf, "conflict", wf.approve_script, store, story_id, 1, now=LATER)
    assert "not complete" in detail and "s04" in detail

    _failed(m.script, store, story_id, llm=FakeLLM(E2=[e2_reply], E4=[DOWN]))
    assert "has not been checked yet" in _refused(wf, "conflict", wf.approve_script, store, story_id, 1, now=LATER)

    _run(m.script, store, story_id, llm=FakeLLM(E4=[E4_ISSUES]))
    detail = _refused(wf, "conflict", wf.approve_script, store, story_id, 1, now=LATER)
    assert "2 issues" in detail and "Broccolia parle trop gentiment ici." in detail
    assert "Le vote surprise n'est jamais expliqué." in detail and "approve anyway" in detail

    script = _script(store, story_id)
    stale = copy.deepcopy(script)
    stale["consistency_report"]["stale"] = True
    store.write_episode_doc(story_id, 1, "script.json", stale, now=NOW)
    assert "out of date" in _refused(wf, "conflict", wf.approve_script, store, story_id, 1, approve_anyway=True,
                                     now=LATER)
    older = copy.deepcopy(script)
    older["rev"] = 2
    store.write_episode_doc(story_id, 1, "script.json", older, now=NOW)
    assert "out of date" in _refused(wf, "conflict", wf.approve_script, store, story_id, 1, approve_anyway=True,
                                     now=LATER)

    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    approved = wf.approve_script(store, story_id, 1, approve_anyway=True, now=LATER)

    assert approved["approved_at"] == LATER and approved["approved_anyway"] == LATER
    on_disk = _script(store, story_id)
    assert on_disk["approved_at"] == LATER and on_disk["approved_anyway"] == LATER
    assert on_disk["rev"] == script["rev"] and on_disk["consistency_report"] == script["consistency_report"]
    assert _story_bytes(store, story_id) == before


def test_a_passed_check_approves_and_approve_anyway_is_recorded_only_when_it_was_needed(wf, store):
    story_id = _passed(store)
    before = _story_bytes(store, story_id)

    assert wf.approve_script(store, story_id, 1, now=LATER)["approved_anyway"] is None
    approved = wf.approve_script(store, story_id, 1, approve_anyway=True, now=LATEST)

    assert approved["approved_at"] == LATEST and approved["approved_anyway"] is None
    assert _story_bytes(store, story_id) == before


def test_a_storyboard_is_approved_on_an_approved_script_with_every_scene_current(wf, store):
    m = _new()
    story_id = _passed(store)
    story = store.get(story_id)
    approvals = _approvals(store, story_id)
    assert _refused(wf, "conflict", wf.approve_storyboard, store, story_id, 1, now=LATER) == (
        "Episode 1 has no storyboard yet: plan its shots first (the storyboard step).")

    wf.build_fast_storyboard(store, story, 1, now=NOW, on_log=Log())
    assert "Approve episode 1's script first" in _refused(wf, "conflict", wf.approve_storyboard, store, story_id, 1,
                                                          now=LATER)
    wf.approve_script(store, story_id, 1, now=LATER)

    # A scene with no shots.
    ec = m.common.load_context(store, story_id, 1)
    script, board = _script(store, story_id), _storyboard(store, story_id)
    plans = shots.plans_from_storyboard(board, script)
    plans.pop("s08")
    partial, _notes = m.storyboard.build(ec, script, plans, {sid: "fast" for sid in plans}, board, stale=set(),
                                         now=NOW)
    store.write_episode_doc(story_id, 1, "storyboard.json", partial, now=NOW)
    assert "no shots for scene s08" in _refused(wf, "conflict", wf.approve_storyboard, store, story_id, 1, now=LATER)

    # A scene marked stale, then one planned from another revision of its scene.
    for mutate in (lambda doc: doc["scenes"]["s03"].update(stale=True),
                   lambda doc: doc["scenes"]["s03"].update(script_rev=2)):
        stale = copy.deepcopy(board)
        mutate(stale)
        store.write_episode_doc(story_id, 1, "storyboard.json", stale, now=NOW)
        detail = _refused(wf, "conflict", wf.approve_storyboard, store, story_id, 1, now=LATER)
        assert "s03" in detail and "older version of the script" in detail

    # Prompts resolved before a character changed.
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    kiwi = store.read_entity(story_id, "characters", KIWILO)
    store.write_entity(story_id, "characters", kiwi, now=LATER)
    detail = _refused(wf, "conflict", wf.approve_storyboard, store, story_id, 1, now=LATER)
    assert "prompts are outdated: refresh them" in detail and "Kiwilo" in detail

    refreshed = wf.patch_storyboard(store, story_id, 1, {"refresh_prompts": True}, now=LATER)
    assert refreshed["resolved_from"][KIWILO] == LATER
    approved = wf.approve_storyboard(store, story_id, 1, now=LATEST)

    assert approved["approved_at"] == LATEST and _storyboard(store, story_id)["approved_at"] == LATEST
    assert _script(store, story_id)["approved_at"] == LATER
    assert _approvals(store, story_id) == approvals


# ============================================================ script edits

def test_editing_a_line_re_times_it_stales_the_check_and_clears_both_approvals(wf, store):
    m = _new()
    story_id = _boarded(wf, store)
    wf.approve_script(store, story_id, 1, now=LATER)
    wf.approve_storyboard(store, story_id, 1, now=LATER)
    before = _story_bytes(store, story_id)
    # A measured take of l08, kept on disk whatever the text becomes.
    audio = store.episode_asset_path(story_id, 1, "voice", "line_08.mp3", create=True)
    Path(audio).write_bytes(b"ID3 take")
    script = _script(store, story_id)
    line = _scene(script, "s02")["lines"][0]
    line["timing"] = {"source": "audio_duration_only", "duration_s": 2.5, "text_hash": timing.text_hash(line["text"]),
                      "voice": "edge/fr-FR-HenriNeural", "audio": "assets/voice/line_08.mp3"}
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)

    written = wf.patch_script(store, story_id, 1, {"lines": [{"line_id": "l08",
                                                              "text": "  Tu me trahis déjà, Mangella ?  "}]},
                              now=LATEST)

    script, board = _script(store, story_id), _storyboard(store, story_id)
    s02 = _scene(script, "s02")
    edited = s02["lines"][0]
    assert edited["text"] == "Tu me trahis déjà, Mangella ?" and edited["line_id"] == "l08"
    assert edited["timing"] == timing.estimated_timing(edited["text"], "fr")
    assert os.path.isfile(audio)
    assert s02["rev"] == 2 and s02["source"] == "edit" and script["rev"] == 2
    assert all(scene["rev"] == 1 for scene in script["scenes"] if scene["scene_id"] != "s02")
    assert script["approved_at"] is None and script["approved_anyway"] is None
    assert script["consistency_report"]["stale"] is True
    assert board["approved_at"] is None and board["scenes"]["s02"]["stale"] is True
    assert not any(entry.get("stale") for sid, entry in board["scenes"].items() if sid != "s02")
    ec = m.common.load_context(store, story_id, 1)
    assert script["timing"] == m.common.retime(copy.deepcopy(script), ec, board)["timing"]
    assert written["rev"] == 2 and written["scenes"] == script["scenes"]
    assert _story_bytes(store, story_id) == before


def test_the_other_script_edits_and_the_revisions_they_move(wf, store):
    story_id = _passed(store)
    wf.patch_script(store, story_id, 1, {
        "scenes": [{"scene_id": "s03", "summary": "Broccolia piège Mangella au bord de l'eau.",
                    "on_screen_text": "  Piège  "}],
        "hook_on_screen_text": "Qui part ce soir ?",
        "cliffhanger_reveal": "Le téléphone se tait enfin.",
        "next_episode_teaser": "Demain, tout bascule.",
    }, now=LATER)

    script = _script(store, story_id)
    s03 = _scene(script, "s03")
    assert s03["summary"] == "Broccolia piège Mangella au bord de l'eau." and s03["on_screen_text"] == "Piège"
    assert script["hook"]["on_screen_text"] == "Qui part ce soir ?"
    assert script["cliffhanger"]["reveal"] == "Le téléphone se tait enfin."
    assert script["next_episode_teaser"] == "Demain, tout bascule."
    assert script["rev"] == 2
    assert {scene["scene_id"]: scene["rev"] for scene in script["scenes"]} == {
        sid: 2 if sid in ("s01", "s03", "s08") else 1 for sid in ALL_SCENES}
    assert script["consistency_report"]["stale"] is True

    # The teaser alone moves the script's revision, no scene's.
    wf.patch_script(store, story_id, 1, {"next_episode_teaser": "Demain encore.", "scenes": [
        {"scene_id": "s03", "on_screen_text": None}]}, now=LATER)
    script = _script(store, story_id)
    assert script["rev"] == 3 and _scene(script, "s03")["on_screen_text"] is None
    assert _scene(script, "s03")["rev"] == 3 and _scene(script, "s02")["rev"] == 1

    # The same values again, or nothing at all: nothing is written.
    path = _episode_file(store, story_id, "script.json")
    unchanged = path.read_bytes()
    wf.patch_script(store, story_id, 1, {"next_episode_teaser": "Demain encore."}, now=LATEST)
    wf.patch_script(store, story_id, 1, {}, now=LATEST)
    assert path.read_bytes() == unchanged


@pytest.mark.parametrize("fields,needle", [
    ({"lines": [{"line_id": "l08", "speaker": BROCCOLIA}]}, "speaker 'char_broccolia' is not in the scene's characters"),
    ({"lines": [{"line_id": "l08", "text": " ".join(["mot"] * 23)}]}, "23 words, expected at most 22"),
    ({"lines": [{"line_id": "l77", "text": "Bonjour."}]}, "'l77' is not a line of episode 1"),
    ({"lines": [{"line_id": "l08", "emotion": "furious"}]}, "emotion"),
    ({"lines": [{"line_id": "l08", "volume": 3}]}, "volume"),
    ({"lines": [{"line_id": "l08", "text": "   "}]}, "expected a non-empty text"),
    ({"scenes": [{"scene_id": "s02", "summary": " ".join(["mot"] * 16)}]}, "16 words, expected at most 15"),
    ({"scenes": [{"scene_id": "s42", "summary": "Rien."}]}, "'s42' is not a scene of episode 1"),
    ({"hook_on_screen_text": "un deux trois quatre cinq six sept"}, "7 words, expected at most 6"),
    ({"next_episode_teaser": ""}, "expected a non-empty text"),
    ({"cliffhanger_reveal": None}, "expected a non-empty text"),
    ({"title": "Nouveau titre"}, "cannot be edited"),
])
def test_a_script_edit_the_rules_refuse_is_invalid_and_writes_nothing(wf, store, fields, needle):
    story_id = _passed(store)
    path = _episode_file(store, story_id, "script.json")
    before = path.read_bytes()

    detail = _refused(wf, "invalid", wf.patch_script, store, story_id, 1, fields, now=LATER)

    assert needle in _text(detail)
    assert path.read_bytes() == before


def test_every_error_of_a_script_edit_is_listed_at_once(wf, store):
    story_id = _passed(store)
    detail = _refused(wf, "invalid", wf.patch_script, store, story_id, 1, {"lines": [
        {"line_id": "l08", "speaker": BROCCOLIA}, {"line_id": "l09", "text": " ".join(["mot"] * 23)}]}, now=LATER)

    assert detail["message"] == "The script would not be valid with these values."
    assert any("char_broccolia" in error for error in detail["errors"])
    assert any("23 words" in error for error in detail["errors"])


def test_a_script_edit_needs_a_script(wf, store):
    story_id = _ready_story(store)
    assert "has no script yet" in _refused(wf, "conflict", wf.patch_script, store, story_id, 1,
                                           {"next_episode_teaser": "Demain."}, now=LATER)
    assert "there is no episode 9" in _refused(wf, "invalid", wf.patch_script, store, story_id, 9,
                                               {"next_episode_teaser": "Demain."}, now=LATER)


# ========================================================= storyboard edits

def test_a_framing_edit_re_resolves_only_that_shot(wf, store):
    story_id = _boarded(wf, store)
    wf.approve_script(store, story_id, 1, now=LATER)
    wf.approve_storyboard(store, story_id, 1, now=LATER)
    board = _storyboard(store, story_id)
    shot = next(shot for shot in _shots_of(board, "s02") if shot["framing"] != "wide_establishing")
    framing = "low_angle" if shot["framing"] != "low_angle" else "high_angle"

    written = wf.patch_storyboard(store, story_id, 1, {"shots": [{"shot_id": shot["shot_id"], "framing": framing}]},
                                  now=LATEST)

    after = _storyboard(store, story_id)
    changed = next(s for s in after["shots"] if s["shot_id"] == shot["shot_id"])
    lock = store.read_doc(story_id, "style_lock.json")
    ec = _new().common.load_context(store, story_id, 1)
    scene = _scene(_script(store, story_id), "s02")
    resolved = shots.resolve_shot({"framing": framing, "action": shot["action"], "subjects": shot["subject_tags"]},
                                  scene=scene, entities=ec.entities, style_lock=lock, consistency_mode="prompt_only")
    assert changed["framing"] == framing and changed["image_prompt"] == resolved["image_prompt"]
    assert changed["image_prompt"] != shot["image_prompt"]
    assert changed["motion"] == shots.motion_for(framing, shot["camera_motion"], "setup", lock)
    assert [s for s in after["shots"] if s["shot_id"] != shot["shot_id"]] == [
        s for s in board["shots"] if s["shot_id"] != shot["shot_id"]]
    assert after["rev"] == board["rev"] + 1 and after["approved_at"] is None
    assert written["rev"] == after["rev"]
    # The script's approval and revision never move for a shot.
    script = _script(store, story_id)
    assert script["approved_at"] == LATER and script["rev"] == 1


def test_an_action_edit_takes_the_scenes_tags_and_never_a_name(wf, store):
    story_id = _boarded(wf, store)
    board = _storyboard(store, story_id)
    shot = _shots_of(board, "s02")[-1]

    wf.patch_storyboard(store, story_id, 1, {"shots": [
        {"shot_id": shot["shot_id"], "action": f"  @{KIWILO} leans toward @{MANGELLA}, whispering.  "}]}, now=LATER)

    changed = next(s for s in _storyboard(store, story_id)["shots"] if s["shot_id"] == shot["shot_id"])
    assert changed["action"] == f"@{KIWILO} leans toward @{MANGELLA}, whispering."
    assert {f"@{KIWILO}", f"@{MANGELLA}"} <= set(changed["subject_tags"])
    for name in ("Kiwilo", "Mangella", "Broccolia"):
        assert name not in changed["image_prompt"]


def _boundary(board):
    """The first transition between two scenes, and one inside a scene."""
    scene_of = {shot["shot_id"]: shot["scene_id"] for shot in board["shots"]}
    order = [shot["shot_id"] for shot in board["shots"]]
    between = inside = None
    for transition in board["transitions"]:
        after = transition["after"]
        following = order[order.index(after) + 1]
        if scene_of[after] != scene_of[following]:
            between = between or transition
        else:
            inside = inside or transition
    return between, inside


def test_a_storyboard_edit_the_rules_refuse_is_invalid_and_writes_nothing(wf, store):
    story_id = _boarded(wf, store)
    board = _storyboard(store, story_id)
    shot = _shots_of(board, "s02")[-1]["shot_id"]
    hook_shot = _shots_of(board, "s01")[-1]["shot_id"]
    _between, inside = _boundary(board)
    path = _episode_file(store, story_id, "storyboard.json")
    before = path.read_bytes()

    for fields, needle in (
            ({"shots": [{"shot_id": shot, "action": f"Kiwilo whispers to @{MANGELLA}."}]},
             "names the character 'Kiwilo'"),
            ({"shots": [{"shot_id": shot, "action": "A crowd gathers at Le Parloir des Secrets."}]},
             "names the place 'Le Parloir des Secrets'"),
            ({"shots": [{"shot_id": shot, "action": f"@{BROCCOLIA} watches from afar."}]},
             f"'@{BROCCOLIA}' is not one of scene s02's tags"),
            ({"shots": [{"shot_id": shot, "action": " ".join(["word"] * 31)}]}, "31 words"),
            ({"transitions": [{"after": inside["after"], "type": "dissolve"}]}, "only cut inside a scene"),
            ({"transitions": [{"after": "sh99", "type": "cut"}]}, "'sh99' has no transition"),
            ({"transitions": [{"after": inside["after"], "type": "swirl"}]}, "swirl"),
            ({"shots": [{"shot_id": "sh99", "framing": "close_up"}]}, "'sh99' is not a shot of episode 1"),
            ({"shots": [{"shot_id": shot, "framing": "fisheye"}]}, "fisheye"),
            ({"shots": [{"shot_id": shot, "modifiers": ["handheld"]}]}, "'handheld' is not a modifier the style allows"),
            ({"shots": [{"shot_id": hook_shot, "camera_motion": "pan_rl"}]}, "the style moves"),
            ({"shots": [{"shot_id": shot, "keep_still": "yes"}]}, "keep_still"),
            ({"shots": [{"shot_id": shot, "zoom": 2}]}, "zoom"),
            ({"refresh_prompts": "yes"}, "refresh_prompts"),
    ):
        detail = _refused(wf, "invalid", wf.patch_storyboard, store, story_id, 1, fields, now=LATER)
        assert needle in _text(detail), (fields, detail)
        assert path.read_bytes() == before
    assert "cannot be edited" in _refused(wf, "invalid", wf.patch_storyboard, store, story_id, 1, {"rev": 3},
                                          now=LATER)


def test_a_transition_edit_takes_the_templates_duration_and_re_times_the_shots(wf, store):
    story_id = _boarded(wf, store)
    board = _storyboard(store, story_id)
    between, _inside = _boundary(board)
    script = _script(store, story_id)
    lock = store.read_doc(story_id, "style_lock.json")
    template = templates.load_episode_template("serial_60s_v1")
    kind = "cut" if between["type"] != "cut" else "fadeblack"

    wf.patch_storyboard(store, story_id, 1, {"transitions": [{"after": between["after"], "type": kind}]}, now=LATER)

    after = _storyboard(store, story_id)
    edited = next(t for t in after["transitions"] if t["after"] == between["after"])
    assert edited == {"after": between["after"], "type": kind, "duration_s": template["transitions_s"][kind]}
    expected = copy.deepcopy(board)
    next(t for t in expected["transitions"] if t["after"] == between["after"]).update(
        type=kind, duration_s=template["transitions_s"][kind])
    shots.retime_storyboard(expected, script, template=template, language="fr", style_lock=lock)
    assert [s["duration_s"] for s in after["shots"]] == [s["duration_s"] for s in expected["shots"]]
    ec = _new().common.load_context(store, story_id, 1)
    script = _script(store, story_id)
    assert script["timing"] == _new().common.retime(copy.deepcopy(script), ec, after)["timing"]


def test_keep_still_and_a_prompt_override_are_kept_as_given(wf, store):
    story_id = _boarded(wf, store)
    board = _storyboard(store, story_id)

    wf.patch_storyboard(store, story_id, 1, {"shots": [
        {"shot_id": "sh01", "keep_still": True, "prompt_override": "  a hand-written prompt  "}]}, now=LATER)
    first = _storyboard(store, story_id)["shots"][0]
    assert first["keep_still"] is True and first["prompt_override"] == "a hand-written prompt"
    assert first["image_prompt"] == board["shots"][0]["image_prompt"]

    wf.patch_storyboard(store, story_id, 1, {"shots": [{"shot_id": "sh01", "prompt_override": None}]}, now=LATER)
    assert _storyboard(store, story_id)["shots"][0]["prompt_override"] is None


def test_a_storyboard_edit_needs_a_storyboard(wf, store):
    story_id = _passed(store)
    assert "has no storyboard yet" in _refused(wf, "conflict", wf.patch_storyboard, store, story_id, 1,
                                               {"refresh_prompts": True}, now=LATER)


# ========================================================== episode length

def test_the_episode_length_is_chosen_while_no_episode_has_a_script(wf, store):
    story_id = _ready_story(store)

    story = wf.patch_story(store, story_id, {"episode_template_id": "serial_90s_v1"}, now=LATER)

    assert story["episode_template_id"] == "serial_90s_v1" and story["status"] == "ready"
    assert story["approvals"]["season"] == NOW
    detail = _refused(wf, "invalid", wf.patch_story, store, story_id, {"episode_template_id": "serial_45s_v1"},
                      now=LATER)
    assert "serial_60s_v1, serial_90s_v1" in detail

    wf.patch_story(store, story_id, {"episode_template_id": "serial_60s_v1"}, now=LATER)
    _run(_new().script, store, story_id, llm=_script_llm())
    detail = _refused(wf, "conflict", wf.patch_story, store, story_id, {"episode_template_id": "serial_90s_v1"},
                      now=LATER)
    assert "episode 1 has a script" in detail
    assert store.get(story_id)["episode_template_id"] == "serial_60s_v1"


# =================================================================== units

def test_the_script_units_count_only_what_is_missing(wf, store):
    m = _new()
    story_id = _ready_story(store)
    story = store.get(story_id)

    def units():
        return wf.script_units(wf.episode_context(store, story, 1, step="script"))

    assert units() == {"E1": 1, "E2": 9, "E3": 1, "E4": 1, "E2_range": [5, 9], "llm_calls": 12,
                       "llm_calls_range": [8, 12]}

    queue = [e2_reply, e2_reply, DOWN, e2_reply, e2_reply, e2_reply]
    _failed(m.script, store, story_id, llm=_script_llm(E2=queue, E3=[DOWN], E4=[]))
    assert units() == {"E1": 0, "E2": 1, "E3": 1, "E4": 1, "E2_range": None, "llm_calls": 3,
                       "llm_calls_range": [3, 3]}

    _run(m.script, store, story_id, llm=FakeLLM(E2=[e2_reply], E3=[E3_FULL], E4=[E4_PASSED]))
    assert units()["llm_calls"] == 0

    script = _script(store, story_id)
    script["consistency_report"]["stale"] = True
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    assert units() == {"E1": 0, "E2": 0, "E3": 0, "E4": 1, "E2_range": None, "llm_calls": 1,
                       "llm_calls_range": [1, 1]}


def test_the_storyboard_units_count_the_scenes_t1_would_plan(wf, store):
    m = _new()
    story_id = _ready_story(store)
    story = store.get(story_id)

    def units():
        return wf.storyboard_units(wf.episode_context(store, story, 1, step="storyboard"))

    first = units()
    assert first["t1_calls"] == 0 and "has no script yet" in first["refusal"]

    _run(m.script, store, story_id, llm=_script_llm(E4=[E4_PASSED]))
    assert units() == {"t1_calls": len(ALL_SCENES), "scenes": ALL_SCENES, "refusal": None}
    wf.build_fast_storyboard(store, story, 1, now=NOW, on_log=Log())
    assert units()["t1_calls"] == len(ALL_SCENES)  # a fast plan is planned again by T1
    _run(m.storyboard, store, story_id, llm=FakeLLM(default={"T1": t1_reply}), step="storyboard")
    assert units() == {"t1_calls": 0, "scenes": [], "refusal": None}


# ================================================================== views

def test_the_episode_view_and_the_story_summaries_follow_the_documents(wf, store):
    story_id = _ready_story(store)
    story = store.get(story_id)
    template = {"id": "serial_60s_v1", "window_s": [55, 80], "target_s": 60, "tighten_above_s": 75}

    assert wf.episode_view(store, story, 1) == {
        "ep": 1, "script": None, "storyboard": None, "template": template,
        "state": {"script": "none", "storyboard": "none", "report": "none", "stale_scenes": [],
                  "prompts_outdated": False, "missing": ["beat_sheet"]},
    }
    assert wf.episode_summaries(store, story) == []

    _run(_new().script, store, story_id, llm=_script_llm())
    view = wf.episode_view(store, story, 1)
    script = _script(store, story_id)
    assert view["script"] == script and view["storyboard"] is None and view["template"] == template
    assert view["state"] == {"script": "complete", "storyboard": "none", "report": "issues", "stale_scenes": [],
                             "prompts_outdated": False, "missing": []}
    assert wf.episode_summaries(store, story) == [{
        "ep": 1, "title": E1_REPLY["title"], "script_state": "complete", "storyboard_state": "none",
        "total_s": script["timing"]["total_s"], "timing_state": script["timing"]["state"]}]

    wf.approve_script(store, story_id, 1, approve_anyway=True, now=LATER)
    wf.build_fast_storyboard(store, story, 1, now=LATER, on_log=Log())
    assert wf.episode_view(store, story, 1)["state"]["script"] == "approved"
    assert wf.episode_view(store, story, 1)["state"]["storyboard"] == "complete"
    wf.approve_storyboard(store, story_id, 1, now=LATER)
    assert wf.episode_summaries(store, story)[0]["storyboard_state"] == "approved"

    wf.patch_script(store, story_id, 1, {"lines": [{"line_id": "l08", "text": "Tu me trahis ?"}]}, now=LATEST)
    state = wf.episode_view(store, story, 1)["state"]
    assert state == {"script": "complete", "storyboard": "partial", "report": "stale", "stale_scenes": ["s02"],
                     "prompts_outdated": False, "missing": ["consistency_check"]}

    kiwi = store.read_entity(story_id, "characters", KIWILO)
    store.write_entity(story_id, "characters", kiwi, now=LATEST)
    assert wf.episode_view(store, story, 1)["state"]["prompts_outdated"] is True


def test_the_view_of_a_partial_script_names_what_is_missing(wf, store):
    story_id = _partial(store)
    state = wf.episode_view(store, store.get(story_id), 1)["state"]
    assert state["script"] == "writing" and state["report"] == "none"
    assert state["missing"] == ["s04", "hook", "cliffhanger", "teaser", "consistency_check"]


# ================================================================= targets

def test_an_episode_target_is_checked_against_the_story_before_any_job(wf, store):
    from clipping.aistory.steps import regenerate

    story_id = _ready_story(store)
    story = store.get(story_id)

    def check(target, **kwargs):
        return wf.check_entity_target(store, story, regenerate.parse_target(target), **kwargs)

    for target in ("scene:1:s03", "hook:1", "shot:1:sh01:plan"):
        assert "Episode 1 has no script yet" in _refused(wf, "conflict", check, target)
    assert "there is no episode 9" in _refused(wf, "invalid", check, "scene:9:s03")

    _run(_new().script, store, story_id, llm=_script_llm(E4=[E4_PASSED]))
    for target in ("scene:1:s03", "scene:1:s01", "hook:1", "cliffhanger:1", "teaser:1"):
        assert check(target) is None, target
    assert "has no scene 's42'" in _refused(wf, "not_found", check, "scene:1:s42")
    assert "has no storyboard yet" in _refused(wf, "conflict", check, "shot:1:sh01:plan")
    assert "Episode 2 has no script yet" in _refused(wf, "conflict", check, "scene:2:s03")
    assert "voice" in _refused(wf, "invalid", check, "hook:1", voice={"provider": "edge", "voice_id": "x"})
    parsed = regenerate.parse_target("scene:1:s03")
    assert wf.target_units(store, story, parsed) == {"llm_calls": 1, "images": 0, "edit_images": 0, "tts_chars": 0}
    assert wf.target_needs_editor(story, parsed) is False

    wf.build_fast_storyboard(store, story, 1, now=NOW, on_log=Log())
    assert check("shot:1:sh01:plan") is None
    assert "has no shot 'sh99'" in _refused(wf, "not_found", check, "shot:1:sh99:plan")
    s02_shot = _shots_of(_storyboard(store, story_id), "s02")[0]["shot_id"]
    wf.patch_script(store, story_id, 1, {"lines": [{"line_id": "l08", "text": "Tu me trahis ?"}]}, now=LATER)
    assert "rewritten since its shots were planned" in _refused(wf, "conflict", check, f"shot:1:{s02_shot}:plan")
