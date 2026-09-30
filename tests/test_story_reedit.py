"""Re-edit operations (AI Story phase 5, stage 7; spec 6.6; DEC-129 as amended,
DEC-141, DEC-142 as amended by stage 6, DEC-154, DEC-155).

The minimum path per edit, with the gates kept -- what each edit outdates,
what it re-times, what it keeps (images, locks, notes, seeds) and which
approvals survive it:

1. a text-only line edit (the words or the delivery of a line; same line
   ids, speakers and emotions): its scene is marked ``retime_only``, never
   ``stale``; the script's approval is cleared and E4 must run again, the
   storyboard's approval is kept (DEC-129 amended for text-only edits); the
   scene is re-timed in place once the line is re-voiced -- on a storyboard
   timed in whole frames and on an old one (which that re-time converts,
   stage 6), shots keep their images, locks and notes. A structural edit (a
   speaker, an emotion) behaves exactly as before;
2. a line re-voiced (``line:<ep>:<lid>``): only that line is spoken, its
   note reaches the voice's direction, its take is persisted in the line's
   entry of ``assets.json`` (pending before the call, so a retry with the
   same note asks the same take);
3. a shot image regenerated with a note: phase 4's path, guarded;
4. a motion swap: the image is kept, only that shot's render key changes,
   DEC-141's refusal stays;
5. a framing, action or prompt edit outdates only that shot's image, a
   render refuses a stale image naming the shot, and a framing edit that
   breaks the cross-scene rules is refused instead of moving a neighbour;
6. a transition change moves no image and re-cuts only the scene it leaves;
7. a scene's ``pays_off`` edited, checked against the hooks open before the
   episode, staling the consistency check but no shot;
8. no re-edit path ever rebuilds the storyboard (``shots.build_storyboard``),
   and every untouched shot's ``assets`` stays byte-identical.

The episode is phase 4's render fixture (``tests/test_story_render_step.py``:
phase 3's French story written by the fake LLM, planned fast, its images made
by the fake image adapter and its lines by the fake Edge, the assets
approved). Offline and hermetic (``tests/test_story_assets_step.py``'s
fixture); no ffmpeg runs (the render step's fakes). ``story.json`` never
changes (RC-E2).

Stdlib + pytest (DEC-012): runs in the CI environment.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib
import json
from pathlib import Path

import pytest

import test_aistory_render_runner as rr
import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_frame_stable_timing as fst
import test_story_measure as tsm
import test_story_render_step as trs
import test_story_shots as sh
import test_tts_adapters as tta
from clipping.aistory import schemas, shots, templates, timing
from clipping.aistory.render import plan as plan_mod
from clipping.providers import generation, tts
from clipping.providers.generation import GenRequest
from clipping.providers.registry import Link
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_render_step import built  # noqa: F401 -- the render step's session copy of the episode

NOW = eps.NOW
LATER = "2026-09-28T09:00:00+00:00"
LATEST = "2026-09-28T10:00:00+00:00"
KIWILO, MANGELLA, BROCCOLIA = eps.KIWILO, eps.MANGELLA, eps.BROCCOLIA
VOICE_IDS = tsm.VOICE_IDS
NEW_WORDS = "Tu me trahis déjà, Mangella, devant tout le monde ?"
FFMPEG = {"version": "6.1.1-test", "machine": "x86_64"}


def _wf():
    return importlib.import_module("clipping.aistory.workflow")


def _m():
    from clipping.aistory.steps import assets, episode_common, episode_regenerate, render, storyboard

    return type("M", (), {"assets": assets, "common": episode_common, "regen": episode_regenerate,
                          "render": render, "storyboard": storyboard})


# ------------------------------------------------------------------ helpers

def _episode(store, tmp_path, built):
    """The render fixture's episode: script, storyboard and assets approved,
    every shot's image and every line's audio on disk."""
    return trs.episode(store, tmp_path, built)


def _script(store, story_id, ep=1):
    return store.read_episode_doc(story_id, ep, "script.json")


def _board(store, story_id, ep=1):
    return store.read_episode_doc(story_id, ep, "storyboard.json")


def _assets_doc(store, story_id):
    return store.read_episode_doc(story_id, 1, "assets.json")


def _ec(store, story_id, ep=1):
    return _m().common.load_context(store, story_id, ep)


def _line(script, line_id):
    return next(line for scene in script["scenes"] for line in scene["lines"] if line["line_id"] == line_id)


def _shot(board, shot_id):
    return next(shot for shot in board["shots"] if shot["shot_id"] == shot_id)


def _assets_bytes(board) -> dict:
    """Every shot's ``assets`` as canonical JSON: byte-identical or not."""
    return {shot["shot_id"]: json.dumps(shot["assets"], sort_keys=True, ensure_ascii=False) for shot in board["shots"]}


def _states(store, story_id) -> dict:
    ec, board = _ec(store, story_id), _board(store, story_id)
    return {shot["shot_id"]: _m().assets.shot_state(ec, shot) for shot in board["shots"]}


def _render_keys(store, story_id) -> dict:
    """``{shot_id: (frames, S-stage cache key)}`` of the render plan the step
    would build now (``render.render_inputs`` hashes the files on disk)."""
    m = _m()
    ec = _ec(store, story_id)
    script, board, doc = _script(store, story_id), _board(store, story_id), _assets_doc(store, story_id)
    inputs = m.render.render_inputs(ec, script, board, doc)
    plan = plan_mod.build_render_plan(**m.render.plan_args(ec, script, board, doc, inputs, subtitles="style",
                                                           encoder="libx264"), ffmpeg=FFMPEG)
    keys = {stage["id"][2:]: stage["cache_key"] for stage in plan["stages"] if stage["kind"] == "shot"}
    return {shot["shot_id"]: (shot["frames"], keys[shot["shot_id"]]) for shot in plan["timeline"]["shots"]}


def _changed(before, after) -> list:
    return sorted(key for key in before if before[key] != after.get(key))


def _check_and_approve_script(store, story_id, ep=1, *, now=LATEST):
    """What running E4 again (passed) and approving the script do: the report
    checked at the script's revision, then ``workflow.approve_script``."""
    script = _script(store, story_id, ep)
    script["consistency_report"].update(stale=False, checked_rev=script["rev"], passed=True, issues=[])
    store.write_episode_doc(story_id, ep, "script.json", script, now=now)
    return _wf().approve_script(store, story_id, ep, now=now)


def _revoice(store, story_id, line_id, *, note=None, edge=None):
    """``regenerate line:1:<line_id>`` with *note*, on the fake Edge; ``(result, log)``."""
    m = _m()
    ctx, log = eps._ctx(store, story_id, step="regenerate", ep=None,
                        params={"target": f"line:1:{line_id}", "note": note}, settings=tas._settings())
    result = m.regen.run(ctx, f"line:1:{line_id}", ("line", 1, line_id), note,
                         adapters=tas._adapters(edge or tsm.Edge()), sleep_fn=lambda _s: None,
                         time_fn=eps.Clock(0.0))
    return result, log


def _audio_sha(store, story_id, line):
    path = Path(store.episode_dir(story_id, 1)) / line["timing"]["audio"]
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _legacy(store, story_id):
    """The episode's storyboard as one timed before whole frames was stored
    (stage 6): no flag, its shots cut to the old timing -- and the script
    re-timed beside it, as it was then."""
    m = _m()
    ec = _ec(store, story_id)
    script, board = _script(store, story_id), _board(store, story_id)
    del board["whole_frames"]
    shots._time_shots(board["shots"], board["transitions"], script, template=ec.template, language=ec.language,
                      style_lock=ec.style_lock, whole_frames=False)
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    m.common.retime(script, ec, board)
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    return board


def _scene_sums(board) -> dict:
    return fst._scene_sums(board)


def _script_scene_durations(script) -> dict:
    return {sid: entry["duration_s"] for sid, entry in script["timing"]["scenes"].items()}


# ====================================================== 1. text-only line edit

def test_a_text_only_line_edit_keeps_the_storyboard_approval_and_marks_its_scene_retime_only(store, tmp_path,
                                                                                            built):
    wf, m = _wf(), _m()
    story_id = _episode(store, tmp_path, built)
    story_before = eps._story_bytes(store, story_id)
    script_before, board_before = _script(store, story_id), _board(store, story_id)
    assert script_before["approved_at"] and board_before["approved_at"]

    wf.patch_script(store, story_id, 1, {"lines": [{"line_id": "l08", "text": NEW_WORDS, "delivery": "froid"}]},
                    now=LATER)

    script, board = _script(store, story_id), _board(store, story_id)
    s02 = eps._scene(script, "s02")
    # The script: the words changed, so its approval goes and E4 must be fresh again (DEC-129).
    assert _line(script, "l08")["text"] == NEW_WORDS and _line(script, "l08")["timing"]["source"] == "estimated"
    assert script["rev"] == script_before["rev"] + 1 and s02["rev"] == eps._scene(script_before, "s02")["rev"] + 1
    assert script["approved_at"] is None and script["approved_anyway"] is None
    assert script["consistency_report"]["stale"] is True
    assert "out of date" in eps_refused(wf.approve_script, store, story_id, 1, now=LATEST)
    # The storyboard: approval, revision and every shot kept; s02 follows the new revision, retime_only, not stale.
    assert board["approved_at"] == board_before["approved_at"] and board["rev"] == board_before["rev"]
    assert board["scenes"]["s02"] == {"source": "fast", "script_rev": s02["rev"], "stale": False,
                                      "retime_only": True}
    assert {sid: entry for sid, entry in board["scenes"].items() if sid != "s02"} == {
        sid: entry for sid, entry in board_before["scenes"].items() if sid != "s02"}
    assert board["shots"] == board_before["shots"]
    assert m.storyboard.stale_scenes(board, script) == set()
    state = wf.episode_view(store, store.get(story_id), 1)["state"]
    assert (state["storyboard"], state["stale_scenes"], state["report"]) == ("approved", [], "stale")
    # the script is timed beside the storyboard as always (the estimate of the new words)
    assert script["timing"] == m.common.retime(copy.deepcopy(script), _ec(store, story_id), board)["timing"]
    assert eps._story_bytes(store, story_id) == story_before


def eps_refused(call, *args, **kwargs) -> str:
    with pytest.raises(_wf().WorkflowError) as caught:
        call(*args, **kwargs)
    return str(caught.value.detail)


@pytest.mark.parametrize("item", [{"speaker": MANGELLA}, {"emotion": "angry"},
                                  {"speaker": MANGELLA, "text": NEW_WORDS}])
def test_a_speaker_or_emotion_edit_stales_its_scene_and_clears_both_approvals_as_before(store, tmp_path, built,
                                                                                       item):
    wf = _wf()
    story_id = _episode(store, tmp_path, built)
    board_before = _board(store, story_id)

    wf.patch_script(store, story_id, 1, {"lines": [dict(item, line_id="l08")]}, now=LATER)

    script, board = _script(store, story_id), _board(store, story_id)
    assert script["approved_at"] is None and board["approved_at"] is None
    assert board["scenes"]["s02"]["stale"] is True and "retime_only" not in board["scenes"]["s02"]
    assert not any(entry.get("stale") for sid, entry in board["scenes"].items() if sid != "s02")
    assert board["shots"] == board_before["shots"]


def test_a_mixed_edit_clears_the_storyboard_approval_but_keeps_the_text_only_scene_planned(store, tmp_path, built):
    wf = _wf()
    story_id = _episode(store, tmp_path, built)

    wf.patch_script(store, story_id, 1, {"lines": [{"line_id": "l08", "text": NEW_WORDS}],
                                         "scenes": [{"scene_id": "s03", "summary": "Mangella piège Broccolia."}]},
                    now=LATER)

    script, board = _script(store, story_id), _board(store, story_id)
    assert board["approved_at"] is None  # the summary is not a text-only edit
    assert board["scenes"]["s03"]["stale"] is True
    assert board["scenes"]["s02"] == {"source": "fast", "script_rev": eps._scene(script, "s02")["rev"],
                                      "stale": False, "retime_only": True}


def test_a_text_edit_never_un_stales_a_scene_planned_from_an_older_revision(store, tmp_path, built):
    wf = _wf()
    story_id = _episode(store, tmp_path, built)
    wf.patch_script(store, story_id, 1, {"lines": [{"line_id": "l08", "emotion": "angry"}]}, now=LATER)
    stale_rev = _board(store, story_id)["scenes"]["s02"]["script_rev"]

    wf.patch_script(store, story_id, 1, {"lines": [{"line_id": "l08", "text": NEW_WORDS}]}, now=LATEST)

    board = _board(store, story_id)
    assert board["scenes"]["s02"]["stale"] is True and board["scenes"]["s02"]["script_rev"] == stale_rev
    assert "retime_only" not in board["scenes"]["s02"] and board["approved_at"] is None


def test_the_re_voice_retimes_the_edited_scene_in_place_keeping_every_shots_image_lock_and_note(store, tmp_path,
                                                                                               built):
    """The whole minimum path of a text-only edit: E4 again and the script
    approved; the line re-voiced -- only it -- and its scene re-timed in
    place (the mark cleared); the assets approved again (their fingerprint
    moved with the new audio); the render runs. The storyboard's approval
    and revision never moved, and no shot lost its image, lock or note."""
    wf, m = _wf(), _m()
    story_id = _episode(store, tmp_path, built)
    # a lock and a regenerate's note, both to be kept
    wf.patch_assets(store, story_id, 1, {"shots": [{"shot_id": "sh05", "locked": True}]}, now=NOW)
    tas._regenerate_shot(store, story_id, "sh07", note="plus sombre", adapters=tas._adapters(image=tas.FakeImage()))
    trs.approve_assets(store, story_id)
    board_before = _board(store, story_id)
    assert _shot(board_before, "sh07")["assets"]["note"] == "plus sombre"
    assert _shot(board_before, "sh05")["assets"]["locked"] is True
    story_before = eps._story_bytes(store, story_id)

    wf.patch_script(store, story_id, 1, {"lines": [{"line_id": "l08", "text": NEW_WORDS}]}, now=LATER)
    edited = _board(store, story_id)
    _check_and_approve_script(store, story_id)
    edge = tsm.Edge()
    _revoice(store, story_id, "l08", edge=edge)

    script, board = _script(store, story_id), _board(store, story_id)
    assert edge.texts() == [NEW_WORDS]  # only that line is spoken
    assert _line(script, "l08")["timing"]["source"] == "tts_word_timestamps"
    assert board["scenes"]["s02"] == {"source": "fast", "script_rev": eps._scene(script, "s02")["rev"],
                                      "stale": False}
    assert (board["approved_at"], board["rev"], board["whole_frames"]) == (
        board_before["approved_at"], board_before["rev"], True)
    # s02 re-timed in place: its shots cut from the new take, exactly as a re-time cuts them
    expected = copy.deepcopy(edited)
    ec = _ec(store, story_id)
    shots.retime_storyboard(expected, script, template=ec.template, language=ec.language, style_lock=ec.style_lock)
    assert [s["duration_s"] for s in board["shots"]] == [s["duration_s"] for s in expected["shots"]]
    assert sum(s["duration_s"] for s in board["shots"] if s["scene_id"] == "s02") != sum(
        s["duration_s"] for s in board_before["shots"] if s["scene_id"] == "s02")
    # one timing: the script's scenes are the storyboard's shot sums
    assert _script_scene_durations(script) == _scene_sums(board)
    # every shot kept its plan, prompt, image, lock and note
    for old, new in zip(board_before["shots"], board["shots"]):
        assert {k: v for k, v in new.items() if k != "duration_s"} == {k: v for k, v in old.items()
                                                                       if k != "duration_s"}
    assert _assets_bytes(board) == _assets_bytes(board_before)

    # the gates, all kept: the assets approval is stale (new words, new audio) until approved again
    assert wf.assets_approval_state(ec, board, script, _assets_doc(store, story_id)) == "stale"
    wf.approve_assets(store, story_id, 1, now=LATEST)
    summary, _log, fake = trs.render(store, story_id, tmp_path=tmp_path)
    assert summary["state"] == "completed"
    assert eps._story_bytes(store, story_id) == story_before


def test_on_an_old_storyboard_the_re_voice_converts_it_as_stage_6_defines(store, tmp_path, built):
    """A storyboard timed before whole frames: the text-only edit keeps it as
    it is; the re-voice's re-time re-cuts every shot (no scene is skipped:
    s02 is retime_only, not stale), so it converts it to whole frames and the
    script is re-timed beside it (``voice_lines.sync_storyboard``)."""
    wf = _wf()
    story_id = _episode(store, tmp_path, built)
    legacy = _legacy(store, story_id)
    assert "whole_frames" not in legacy
    assert any(fst._residue(shot["duration_s"]) > 0.05 for shot in legacy["shots"])  # a real old board

    wf.patch_script(store, story_id, 1, {"lines": [{"line_id": "l08", "text": NEW_WORDS}]}, now=LATER)
    board = _board(store, story_id)
    assert "whole_frames" not in board and board["shots"] == legacy["shots"]
    assert board["scenes"]["s02"]["retime_only"] is True and board["approved_at"] == legacy["approved_at"]
    _check_and_approve_script(store, story_id)
    _revoice(store, story_id, "l08")

    script, board = _script(store, story_id), _board(store, story_id)
    assert board["whole_frames"] is True and "retime_only" not in board["scenes"]["s02"]
    assert all(fst._canonical(shot["duration_s"]) for shot in board["shots"])
    assert _script_scene_durations(script) == _scene_sums(board)
    assert script["timing"] == timing.episode_timing(script, _ec(store, story_id).template, "fr",
                                                     style_lock=store.read_doc(story_id, "style_lock.json"),
                                                     storyboard=board)
    assert _assets_bytes(board) == _assets_bytes(legacy)
    assert board["approved_at"] == legacy["approved_at"] and board["rev"] == legacy["rev"]


def test_retime_storyboard_retimes_a_retime_only_scene_and_clears_its_mark_once_its_lines_are_measured():
    script = fst._script_with_measured_lines()
    board = fst._board(script, sh.FRUIT_DRAMA)
    kwargs = {"template": fst.TEMPLATE, "language": fst.EN, "style_lock": sh.FRUIT_DRAMA}
    sid = script["scenes"][2]["scene_id"]
    scene = script["scenes"][2]
    # a text-only edit of the scene's first line: new words, estimated, the scene one revision on
    line = scene["lines"][0]
    line["text"] = line["text"] + " Encore une fois, plus fort, devant tout le monde."
    line["timing"] = timing.estimated_timing(line["text"], fst.EN)
    scene["rev"] += 1
    board["scenes"][sid].update(script_rev=scene["rev"], retime_only=True)
    # and another scene rewritten for real: stale, skipped
    other = script["scenes"][4]["scene_id"]
    board["scenes"][other]["stale"] = True
    kept = [s["duration_s"] for s in board["shots"] if s["scene_id"] == other]

    assert shots.retime_storyboard(board, script, **kwargs) is True
    # re-timed now (with the estimate), the mark kept until the line is measured
    assert board["scenes"][sid]["retime_only"] is True
    assert fst._scene_sums(board)[sid] == timing.episode_pass(script, fst.TEMPLATE, fst.EN,
                                                              style_lock=sh.FRUIT_DRAMA,
                                                              storyboard=board)[0]["scenes"][sid]["duration_s"]
    assert [s["duration_s"] for s in board["shots"] if s["scene_id"] == other] == kept

    line["timing"] = {"source": "tts_word_timestamps", "duration_s": 3.456, "text_hash": timing.text_hash(line["text"]),
                      "voice": "edge/x", "audio": "assets/voice/l.mp3"}
    assert shots.retime_storyboard(board, script, **kwargs) is True
    assert "retime_only" not in board["scenes"][sid]
    assert schemas.storyboard_errors(board, min_shot_s=fst.TEMPLATE["min_shot_s"]) == []
    # nothing moves on a second re-time
    assert shots.retime_storyboard(board, script, **kwargs) is False


def test_the_storyboard_schema_takes_retime_only_as_an_optional_boolean():
    board = fst._board(fst._script_with_measured_lines(), sh.FRUIT_DRAMA)
    sid = next(iter(board["scenes"]))
    board["scenes"][sid]["retime_only"] = True
    assert schemas.storyboard_errors(board) == []
    board["scenes"][sid]["retime_only"] = "yes"
    assert any("retime_only" in error for error in schemas.storyboard_errors(board))


# ========================================================= 2. re-voice one line

def test_a_line_regenerate_sends_its_note_as_the_voice_direction_and_persists_its_take(store, tmp_path, built):
    story_id = _episode(store, tmp_path, built)
    doc_before, board_before = _assets_doc(store, story_id), _board(store, story_id)
    note = "Plus bas, presque un murmure."
    seen = []
    edge = tsm.Edge(on_call=lambda _edge: seen.append(copy.deepcopy(_assets_doc(store, story_id)["lines"]["l13"])))

    result, log = _revoice(store, story_id, "l13", note=note, edge=edge)

    assert len(edge.calls) == 1 and edge.calls[0]["text"] == _line(_script(store, story_id), "l13")["text"]
    extra = edge.calls[0]["extra"]
    assert extra["direction"] == note and extra["take"] == result["take"]
    # persisted before the call, as a pending take (a retry with the same note asks the same one) ...
    assert seen[0]["pending"]["take"] == result["take"] and seen[0]["pending"]["note"] == note
    assert seen[0]["pending"]["requested_at"]
    # ... and in the line's entry once spoken, tied to the audio it made
    entry = _assets_doc(store, story_id)["lines"]["l13"]
    line = _line(_script(store, story_id), "l13")
    assert entry["take"] == {"id": result["take"], "note": note, "audio_sha256": _audio_sha(store, story_id, line)}
    assert "pending" not in entry and entry["words_source"] == doc_before["lines"]["l13"]["words_source"]
    assert {k: v for k, v in _assets_doc(store, story_id)["lines"].items() if k != "l13"} == {
        k: v for k, v in doc_before["lines"].items() if k != "l13"}
    # Edge cannot follow a spoken direction: said, not silently dropped
    assert any("direction" in line_ and "not applied" in line_ for line_ in log)
    assert not any("has no note" in line_ for line_ in log)
    # no approval moved, no shot touched
    board = _board(store, story_id)
    assert board["approved_at"] == board_before["approved_at"] and board["rev"] == board_before["rev"]
    assert _assets_bytes(board) == _assets_bytes(board_before)


def test_a_failed_line_regenerate_keeps_its_take_pending_and_a_retry_with_the_same_note_asks_it_again(
        store, tmp_path, built):
    story_id = _episode(store, tmp_path, built)
    failing = tsm.Edge(failing={VOICE_IDS[BROCCOLIA]})
    with pytest.raises(eps.steps.StepFailed):
        _revoice(store, story_id, "l13", note="plus fort", edge=failing)
    pending = _assets_doc(store, story_id)["lines"]["l13"]["pending"]
    assert pending["note"] == "plus fort" and pending["take"] == failing.calls[0]["extra"]["take"]

    again = tsm.Edge()
    result, _log = _revoice(store, story_id, "l13", note="plus fort", edge=again)
    assert again.calls[0]["extra"]["take"] == pending["take"] == result["take"]
    entry = _assets_doc(store, story_id)["lines"]["l13"]
    assert entry["take"]["id"] == pending["take"] and "pending" not in entry

    other = tsm.Edge()
    result, _log = _revoice(store, story_id, "l13", note="plus doux", edge=other)
    assert other.calls[0]["extra"]["take"] == result["take"] != pending["take"]
    assert other.calls[0]["extra"]["direction"] == "plus doux"


def test_a_line_regenerate_with_no_note_sends_no_direction(store, tmp_path, built):
    story_id = _episode(store, tmp_path, built)
    edge = tsm.Edge()
    result, _log = _revoice(store, story_id, "l13", edge=edge)
    assert "direction" not in edge.calls[0]["extra"] and edge.calls[0]["extra"]["take"] == result["take"]
    assert _assets_doc(store, story_id)["lines"]["l13"]["take"]["note"] is None


def test_the_assets_step_keeps_a_take_while_its_audio_is_the_one_on_disk(store, tmp_path, built):
    story_id = _episode(store, tmp_path, built)
    result, _log = _revoice(store, story_id, "l13", note="plus fort")
    tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    entry = _assets_doc(store, story_id)["lines"]["l13"]
    assert entry["take"]["id"] == result["take"]

    # the audio replaced by another measurement: the record no longer describes it
    line = _line(_script(store, story_id), "l13")
    (Path(store.episode_dir(story_id, 1)) / line["timing"]["audio"]).write_bytes(b"ID3another-take")
    tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    assert "take" not in _assets_doc(store, story_id)["lines"]["l13"]


def test_the_assets_line_entry_takes_a_take_and_a_pending_take():
    doc = {"$schema": "episode_assets_v1", "ep": 1, "sfx": [], "bgm": None, "approved": None,
           "created_at": NOW, "updated_at": NOW,
           "lines": {"l01": {"words_source": "provider",
                             "take": {"id": "0123456789abcdef", "note": "plus fort", "audio_sha256": "a" * 64}},
                     "l02": {"words_source": "even_split",
                             "pending": {"take": "fedcba9876543210", "note": None, "requested_at": NOW}},
                     # a line never voiced yet, with a regenerate asked
                     "l03": {"pending": {"take": "00000000000000ff", "note": None, "requested_at": NOW}}}}
    assert schemas.episode_assets_errors(doc) == []
    for broken, needle in (
            ({"words_source": "provider", "take": {"id": "xyz", "note": None, "audio_sha256": "a" * 64}}, "id"),
            ({"words_source": "provider", "take": {"id": "0123456789abcdef", "note": None}}, "audio_sha256"),
            ({"take": {"id": "0123456789abcdef", "note": None, "audio_sha256": "a" * 64}}, "words_source"),
            ({}, "words_source")):
        bad = copy.deepcopy(doc)
        bad["lines"]["l04"] = broken
        errors = schemas.episode_assets_errors(bad)
        assert any(needle in error for error in errors), (broken, errors)


def _gemini_answer():
    audio = {"inlineData": {"mimeType": "audio/L16;rate=24000", "data": base64.b64encode(b"\x00\x01" * 240).decode()}}
    return tta.FakeTransport([(200, {"candidates": [{"content": {"parts": [audio]}}]})])


def test_gemini_records_a_direction_without_speaking_it(tmp_path):
    """Tier-2 finding T2-P5-F9 (2026-09-30): sent as Gemini's own style form
    ("Say <note>: <line>"), a French note was read aloud by the free TTS --
    the live l12 came back 8.05 s instead of 3.49 s, transcribed "C'est d'une
    voix glacial et tremblante de colère. Tu as falsifié ...". A note must
    never reach the audio: Gemini, like Edge, records it (with the take) and
    sends the line alone."""
    link = Link("gemini", "flash-lite-tts")
    transport = _gemini_answer()
    request = GenRequest(kind="tts", text="Tu me trahis ?", voice="Kore", out_dir=str(tmp_path),
                         extra={"direction": "  d'une voix glaciale. "})
    log = []
    tts.GEMINI_TTS.generate(link, request, credentials={"GOOGLE_API_KEY": "gk"}, on_log=log.append,
                            transport=transport)
    body = json.loads(transport.calls[0]["body"])
    assert body["contents"][0]["parts"] == [{"text": "Tu me trahis ?"}]
    assert [line for line in log if "direction" in line] == [
        "   ⚠️ a spoken direction is not supported by gemini/flash-lite-tts; recorded, not applied."]

    plain = _gemini_answer()
    quiet = []
    tts.GEMINI_TTS.generate(link, GenRequest(kind="tts", text="Tu me trahis ?", voice="Kore", out_dir=str(tmp_path)),
                            credentials={"GOOGLE_API_KEY": "gk"}, on_log=quiet.append, transport=plain)
    assert json.loads(plain.calls[0]["body"])["contents"][0]["parts"] == [{"text": "Tu me trahis ?"}]
    assert not any("direction" in line for line in quiet)


def test_edge_and_the_local_engines_record_a_direction_without_applying_it(tmp_path, monkeypatch):
    log = []
    request = GenRequest(kind="tts", text="Bonjour", out_dir=str(tmp_path), extra={"direction": "softly"})
    tts.EDGE.generate(Link("edge", "fr-FR-HenriNeural"), request, credentials={}, on_log=log.append,
                      synthesize=tta.fake_synth)
    assert [line for line in log if "direction" in line] == [
        "   ⚠️ a spoken direction is not supported by edge/fr-FR-HenriNeural; recorded, not applied."]
    quiet = []
    tts.EDGE.generate(Link("edge", "fr-FR-HenriNeural"), GenRequest(kind="tts", text="Bonjour", out_dir=str(tmp_path)),
                      credentials={}, on_log=quiet.append, synthesize=tta.fake_synth)
    assert not any("direction" in line for line in quiet)


def test_synthesize_line_hands_the_direction_to_the_request_only_when_given():
    import inspect

    from clipping.aistory import voices

    assert "direction" in inspect.signature(voices.synthesize_line).parameters
    assert inspect.signature(voices.synthesize_line).parameters["direction"].default is None


# ================================================= 3. shot image regenerate

def test_a_shot_image_regenerate_with_a_note_stays_phase_4s_path_and_moves_only_its_render_key(
        store, tmp_path, built, monkeypatch):
    from clipping.aistory.steps import entities

    story_id = _episode(store, tmp_path, built)
    board_before, keys_before = _board(store, story_id), _render_keys(store, story_id)
    monkeypatch.setattr(entities, "fresh_seed", lambda: 4242)
    monkeypatch.setattr(shots, "build_storyboard", _never("build_storyboard"))

    tas._regenerate_shot(store, story_id, "sh05", note="plus sombre", adapters=tas._adapters(image=tas.FakeImage()))

    board = _board(store, story_id)
    new = _shot(board, "sh05")["assets"]
    assert (new["seed"], new["note"], new["pending"]) == (4242, "plus sombre", None)
    assert (board["approved_at"], board["rev"]) == (board_before["approved_at"], board_before["rev"])
    before, after = _assets_bytes(board_before), _assets_bytes(board)
    assert _changed(before, after) == ["sh05"]
    assert _changed(keys_before, _render_keys(store, story_id)) == ["sh05"]
    assert _states(store, story_id)["sh05"] == "current"


def _never(name):
    def refuse(*_args, **_kwargs):
        raise AssertionError(f"a re-edit must never call {name}")

    return refuse


# ============================================================ 4. motion swap

def test_a_motion_swap_keeps_the_image_and_changes_only_that_shots_render_key(store, tmp_path, built):
    wf = _wf()
    story_id = _episode(store, tmp_path, built)
    board_before, keys_before = _board(store, story_id), _render_keys(store, story_id)
    assert _shot(board_before, "sh05")["camera_motion"] == "push_in"  # a setup scene's medium_single: free to move

    wf.patch_storyboard(store, story_id, 1, {"shots": [{"shot_id": "sh05", "camera_motion": "pan_rl"}]}, now=LATER)

    board = _board(store, story_id)
    swapped = _shot(board, "sh05")
    assert swapped["camera_motion"] == "pan_rl" and swapped["motion"]["pan"] == "rl"
    old = _shot(board_before, "sh05")
    assert {k: v for k, v in swapped.items() if k not in ("camera_motion", "motion")} == {
        k: v for k, v in old.items() if k not in ("camera_motion", "motion")}
    assert [s for s in board["shots"] if s["shot_id"] != "sh05"] == [
        s for s in board_before["shots"] if s["shot_id"] != "sh05"]
    assert set(_states(store, story_id).values()) == {"current"}
    keys = _render_keys(store, story_id)
    assert _changed(keys_before, keys) == ["sh05"] and keys["sh05"][0] == keys_before["sh05"][0]
    # the storyboard's own approval goes (DEC-129); the assets approval is still the files' (DEC-155)
    assert board["approved_at"] is None
    assert _wf().assets_approval_state(_ec(store, story_id), board, _script(store, story_id),
                                       _assets_doc(store, story_id)) == "current"
    # DEC-141 kept: a motion the style fixes for the shot is refused, naming it
    detail = eps_refused(wf.patch_storyboard, store, story_id, 1,
                         {"shots": [{"shot_id": "sh03", "camera_motion": "pan_lr"}]}, now=LATER)
    assert "the style moves" in detail and "push_in" in detail


def test_a_motion_swap_never_re_resolves_a_prompt_whose_entity_changed_since(store, tmp_path, built):
    wf = _wf()
    story_id = _episode(store, tmp_path, built)
    board_before = _board(store, story_id)
    kiwi = store.read_entity(story_id, "characters", KIWILO)
    kiwi["descriptor"] = kiwi["descriptor"] + " Now wearing a bright red scarf."
    store.write_entity(story_id, "characters", kiwi, now=LATER)
    shot_id = "sh04"  # s02's medium_two_shot, Kiwilo in it
    assert f"@{KIWILO}" in _shot(board_before, shot_id)["subject_tags"]

    wf.patch_storyboard(store, story_id, 1, {"shots": [{"shot_id": shot_id, "camera_motion": "pan_lr"}]}, now=LATEST)

    shot = _shot(_board(store, story_id), shot_id)
    assert shot["image_prompt"] == _shot(board_before, shot_id)["image_prompt"]
    assert _board(store, story_id)["resolved_from"] == board_before["resolved_from"]
    assert _states(store, story_id)[shot_id] == "current"


# ====================================================== 5. framing / action / prompt

@pytest.mark.parametrize("item", [{"framing": "low_angle"},
                                  {"action": f"@{KIWILO} leans toward @{MANGELLA}, whispering."},
                                  {"prompt_override": "a hand-written prompt of the scene"}])
def test_a_framing_action_or_prompt_edit_outdates_only_that_shots_image(store, tmp_path, built, item):
    wf = _wf()
    story_id = _episode(store, tmp_path, built)
    board_before = _board(store, story_id)

    wf.patch_storyboard(store, story_id, 1, {"shots": [dict(item, shot_id="sh04")]}, now=LATER)

    board = _board(store, story_id)
    states = _states(store, story_id)
    assert states["sh04"] == "stale" and {s for k, s in states.items() if k != "sh04"} == {"current"}
    # the image stays on disk and in the record (a regenerate or the assets step replaces it)
    assert _assets_bytes(board) == _assets_bytes(board_before)


def test_a_render_refuses_a_stale_shot_image_naming_the_shot(store, tmp_path, built):
    """The bug: a framing edit, the storyboard approved again, and the
    render used the old image -- its fingerprint (the recorded prompt hash,
    the image, the lock) never moved. Refused now, naming the shot."""
    wf, m = _wf(), _m()
    story_id = _episode(store, tmp_path, built)
    wf.patch_storyboard(store, story_id, 1, {"shots": [{"shot_id": "sh04", "framing": "low_angle"}]}, now=LATER)
    wf.approve_storyboard(store, story_id, 1, now=LATEST)
    ec = _ec(store, story_id)
    assert wf.assets_approval_state(ec, _board(store, story_id), _script(store, story_id),
                                    _assets_doc(store, story_id)) == "current"

    message, fake = trs.refused(store, story_id)

    assert "sh04" in message and "out of date" in message and "shot:1:sh04" in message
    assert fake.calls == []
    assert m.render.current_render(ec) is False


def test_a_locked_shot_renders_the_image_it_has_after_its_prompt_changed(store, tmp_path, built):
    wf = _wf()
    story_id = _episode(store, tmp_path, built)
    wf.patch_assets(store, story_id, 1, {"shots": [{"shot_id": "sh04", "locked": True}]}, now=NOW)
    wf.patch_storyboard(store, story_id, 1, {"shots": [{"shot_id": "sh04", "framing": "low_angle"}]}, now=LATER)
    wf.approve_storyboard(store, story_id, 1, now=LATEST)
    trs.approve_assets(store, story_id)
    assert _states(store, story_id)["sh04"] == "locked_stale"

    summary, _log, _fake = trs.render(store, story_id, tmp_path=tmp_path)
    assert summary["state"] == "completed"


def test_a_framing_edit_that_breaks_the_cross_scene_rules_is_refused_naming_the_shot_it_would_move(
        store, tmp_path, built):
    wf = _wf()
    story_id = _episode(store, tmp_path, built)
    board = _board(store, story_id)
    path = Path(store.episode_dir(story_id, 1)) / "storyboard.json"
    before = path.read_bytes()
    assert (_shot(board, "sh04")["framing"], _shot(board, "sh05")["framing"]) == ("medium_two_shot", "medium_single")

    # (a) no two shots in a row share a framing: sh05 as sh04 would be moved
    detail = eps_refused(wf.patch_storyboard, store, story_id, 1,
                         {"shots": [{"shot_id": "sh05", "framing": "medium_two_shot"}]}, now=LATER)
    assert "sh05" in detail and "cross-scene rules" in detail and "repeat" in detail
    assert path.read_bytes() == before

    # (b) every three scenes hold a close-up: s04's and s05's only ones gone, s06's last shot would be forced
    detail = eps_refused(wf.patch_storyboard, store, story_id, 1,
                         {"shots": [{"shot_id": "sh11", "framing": "low_angle"},
                                    {"shot_id": "sh13", "framing": "high_angle"}]}, now=LATER)
    assert "sh15" in detail and "close" in detail
    assert path.read_bytes() == before

    # a framing edit that keeps the rules is written, moving no neighbour
    wf.patch_storyboard(store, story_id, 1, {"shots": [{"shot_id": "sh04", "framing": "low_angle"}]}, now=LATER)
    after = _board(store, story_id)
    assert [s["framing"] for s in after["shots"] if s["shot_id"] != "sh04"] == [
        s["framing"] for s in board["shots"] if s["shot_id"] != "sh04"]


def test_a_board_that_already_breaks_a_rule_still_takes_an_unrelated_framing_edit(store, tmp_path, built):
    """Only what the edit itself breaks is refused: a repeat an older board
    already had (hand-edited before this stage) does not block another shot."""
    wf = _wf()
    story_id = _episode(store, tmp_path, built)
    board = _board(store, story_id)
    _shot(board, "sh20")["framing"] = "medium_single"  # repeats sh19, as stored before
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)

    wf.patch_storyboard(store, story_id, 1, {"shots": [{"shot_id": "sh04", "framing": "low_angle"}]}, now=LATER)
    assert _shot(_board(store, story_id), "sh04")["framing"] == "low_angle"


# ======================================================= 6. transition change

def test_a_transition_change_moves_no_image_and_re_cuts_only_the_scene_it_leaves(store, tmp_path, built):
    """Every scene boundary of the episode, every other transition: the
    PATCH re-times the shots (the scene a transition leaves keeps a tail at
    least as long as its blend, spec 6.4), so the shots of that scene may
    move by whole frames -- and nothing else: no image, no motion, no shot of
    another scene (whole frames, stage 6)."""
    wf = _wf()
    story_id = _episode(store, tmp_path, built)
    board = _board(store, story_id)
    script = _script(store, story_id)
    lock = store.read_doc(story_id, "style_lock.json")
    template = templates.load_episode_template(script["template_id"])
    scene_of = {shot["shot_id"]: shot["scene_id"] for shot in board["shots"]}
    order = [shot["shot_id"] for shot in board["shots"]]
    base = _pure_keys(script, board, lock, template)
    boundaries = [t for t in board["transitions"] if scene_of[t["after"]] != scene_of[order[order.index(t["after"]) + 1]]]
    assert len(boundaries) == 9
    moved = 0
    for transition in boundaries:
        for kind in schemas.TRANSITIONS:
            if kind == transition["type"]:
                continue
            edited = copy.deepcopy(board)
            next(t for t in edited["transitions"] if t["after"] == transition["after"]).update(
                type=kind, duration_s=template["transitions_s"][kind])
            assert shots.retime_storyboard(edited, script, template=template, language="fr", style_lock=lock) \
                in (True, False)
            keys = _pure_keys(script, edited, lock, template)
            changed = _changed(base, keys)
            moved += bool(changed)
            leaving = scene_of[transition["after"]]
            for shot_id in changed:
                assert scene_of[shot_id] == leaving, (transition, kind, shot_id)
                assert base[shot_id][0] != keys[shot_id][0], "a key moved without its frames"
            for old, new in zip(board["shots"], edited["shots"]):
                assert {k: v for k, v in new.items() if k != "duration_s"} == {
                    k: v for k, v in old.items() if k != "duration_s"}
    assert moved  # the fixture's tails do sit at their floors: some boundaries re-cut their scene

    # through the PATCH: the workflow's own re-time, the same answer
    between = boundaries[0]
    kind = "cut" if between["type"] != "cut" else "fadeblack"
    keys_before = _render_keys(store, story_id)
    wf.patch_storyboard(store, story_id, 1, {"transitions": [{"after": between["after"], "type": kind}]}, now=LATER)
    changed = _changed(keys_before, _render_keys(store, story_id))
    assert all(scene_of[shot_id] == scene_of[between["after"]] for shot_id in changed)
    assert _assets_bytes(_board(store, story_id)) == _assets_bytes(board)


def _pure_keys(script, board, lock, template) -> dict:
    """``{shot_id: (frames, key)}`` of the render plan over stand-in files
    (``test_story_frame_stable_timing._shot_keys``' inputs, this template)."""
    keys = fst_keys(script, board, lock, template)
    return {shot_id: (value[1], value[3]) for shot_id, value in keys.items()}


def fst_keys(script, board, lock, template):
    saved = fst.TEMPLATE
    fst.TEMPLATE = template
    try:
        return fst._shot_keys(script, board, lock)
    finally:
        fst.TEMPLATE = saved


# ============================================================ 7. pays_off

def _episode_2(store):
    """A continuity story whose episode 2 is written (s04 pays off the phone)
    and planned fast, both documents approved."""
    wf = _wf()
    story_id = eps._continuity_story(store)
    llm = eps._script_llm(E1=[eps._e1_ep2_paying({"s04": [eps.HOOK_PHONE]})], E3=[eps.E3_EP2], E4=[eps.E4_PASSED])
    eps._run(eps._new().script, store, story_id, llm=llm, ep=2)
    wf.approve_script(store, story_id, 2, now=NOW)
    wf.build_fast_storyboard(store, store.get(story_id), 2, now=NOW, on_log=eps.Log())
    wf.approve_storyboard(store, story_id, 2, now=NOW)
    return story_id


def test_a_pays_off_edit_stales_the_check_but_no_shot_and_keeps_the_storyboard_approval(store):
    wf, m = _wf(), _m()
    story_id = _episode_2(store)
    story_before = eps._story_bytes(store, story_id)
    script_before, board_before = _script(store, story_id, 2), _board(store, story_id, 2)
    assert eps._scene(script_before, "s04")["pays_off"] == [eps.HOOK_PHONE]

    wf.patch_script(store, story_id, 2, {"scenes": [{"scene_id": "s04", "pays_off": []},
                                                    {"scene_id": "s05", "pays_off": [eps.HOOK_BETRAY]}]}, now=LATER)

    script, board = _script(store, story_id, 2), _board(store, story_id, 2)
    assert "pays_off" not in eps._scene(script, "s04")  # none is stored as nothing
    assert eps._scene(script, "s05")["pays_off"] == [eps.HOOK_BETRAY]
    assert script["approved_at"] is None and script["consistency_report"]["stale"] is True
    assert script["rev"] == script_before["rev"] + 1
    assert board["approved_at"] == board_before["approved_at"] and board["shots"] == board_before["shots"]
    for sid in ("s04", "s05"):
        assert board["scenes"][sid] == {"source": "fast", "script_rev": eps._scene(script, sid)["rev"],
                                        "stale": False}
    assert m.storyboard.stale_scenes(board, script) == set()
    assert script["timing"] == script_before["timing"]
    assert eps._story_bytes(store, story_id) == story_before


@pytest.mark.parametrize("pays_off,needle", [
    ([eps.HOOK_VOTE], "not a hook open before episode 2"),
    ([eps.HOOK_PHONE, eps.HOOK_BETRAY], "at most 1"),
    ("Qui a volé le téléphone ?", "expected a list"),
    ([3], "expected a list"),
])
def test_a_pays_off_edit_is_checked_against_the_hooks_open_before_the_episode(store, pays_off, needle):
    wf = _wf()
    story_id = _episode_2(store)
    path = Path(store.episode_dir(story_id, 2)) / "script.json"
    before = path.read_bytes()

    detail = eps_refused(wf.patch_script, store, story_id, 2,
                         {"scenes": [{"scene_id": "s05", "pays_off": pays_off}]}, now=LATER)

    assert needle in detail
    assert path.read_bytes() == before


def test_episode_1_has_no_hook_to_pay_off(store):
    wf = _wf()
    story_id = eps._ready_story(store)
    eps._run(eps._new().script, store, story_id, llm=eps._script_llm(E4=[eps.E4_PASSED]))
    detail = eps_refused(wf.patch_script, store, story_id, 1,
                         {"scenes": [{"scene_id": "s02", "pays_off": [eps.HOOK_PHONE]}]}, now=LATER)
    assert "not a hook open before episode 1" in detail


# ================================================================ 8. the guard

def test_no_re_edit_path_rebuilds_the_storyboard_and_untouched_shots_keep_their_assets(
        store, tmp_path, built, monkeypatch):
    """Every re-edit of this stage in turn, ``shots.build_storyboard`` made to
    fail: none calls it, and after each one every shot the edit did not
    name keeps its ``assets`` byte for byte (a T1 re-plan would have wiped
    them all, locks and notes included)."""
    wf = _wf()
    story_id = _episode(store, tmp_path, built)
    wf.patch_assets(store, story_id, 1, {"shots": [{"shot_id": "sh09", "locked": True}]}, now=NOW)
    monkeypatch.setattr(shots, "build_storyboard", _never("build_storyboard"))

    def step(edit, touched=()):
        before = _assets_bytes(_board(store, story_id))
        edit()
        after = _assets_bytes(_board(store, story_id))
        assert {k: v for k, v in after.items() if k not in touched} == {
            k: v for k, v in before.items() if k not in touched}, edit

    step(lambda: wf.patch_script(store, story_id, 1, {"lines": [{"line_id": "l08", "text": NEW_WORDS}]},
                                 now=LATER))
    step(lambda: _check_and_approve_script(store, story_id))
    step(lambda: _revoice(store, story_id, "l08", note="plus froid"))
    step(lambda: _revoice(store, story_id, "l13"))
    step(lambda: tas._regenerate_shot(store, story_id, "sh05", note="plus sombre",
                                      adapters=tas._adapters(image=tas.FakeImage())), touched=("sh05",))
    step(lambda: wf.patch_storyboard(store, story_id, 1, {"shots": [{"shot_id": "sh05", "camera_motion": "pan_rl"}]},
                                     now=LATER))
    step(lambda: wf.patch_storyboard(store, story_id, 1, {"shots": [{"shot_id": "sh04", "framing": "low_angle"}]},
                                     now=LATER))
    step(lambda: wf.patch_storyboard(store, story_id, 1, {"shots": [
        {"shot_id": "sh08", "action": f"@{BROCCOLIA} turns away from @{MANGELLA}."}]}, now=LATER))
    step(lambda: wf.patch_storyboard(store, story_id, 1, {"shots": [
        {"shot_id": "sh10", "prompt_override": "a hand-written prompt"}]}, now=LATER))
    between = next(t for t in _board(store, story_id)["transitions"] if t["after"] == "sh05")
    step(lambda: wf.patch_storyboard(store, story_id, 1, {"transitions": [
        {"after": "sh05", "type": "cut" if between["type"] != "cut" else "dissolve"}]}, now=LATER))
    assert _shot(_board(store, story_id), "sh09")["assets"]["locked"] is True


# ============================================== 9. F8 regenerate-blocked (13b)

def test_metadata_regenerate_is_not_blocked_once_the_episode_is_rendered_and_current(store, tmp_path, built):
    """F8 (phase 5 stage 13b): metadata.require_render's own sentence blocks
    the metadata regenerate controls until the episode is actually
    rendered -- ``_episode`` alone (script/storyboard/assets approved, no
    render yet) still leaves it blocked; a real render (FakeFFmpeg, real
    bytes and sha256, same as every other test here) clears it. assets stays
    unblocked throughout (script and storyboard were already approved)."""
    wf = _wf()
    story_id = _episode(store, tmp_path, built)

    before = wf.episode_view(store, store.get(story_id), 1)["state"]
    assert before["assets_regenerate_blocked"] is None
    assert before["metadata_regenerate_blocked"] == "Episode 1 is not rendered yet: render it first (the render step)."

    summary, _log, _fake = trs.render(store, story_id, tmp_path=tmp_path)
    assert summary["state"] == "completed"

    after = wf.episode_view(store, store.get(story_id), 1)["state"]
    assert after["assets_regenerate_blocked"] is None
    assert after["metadata_regenerate_blocked"] is None
