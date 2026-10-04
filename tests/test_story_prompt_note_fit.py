"""A note at a prompt's tail never puts the prompt over its link's budget
(AI Story, DEC-249; the human's report of 2026-10-03: "shot sh03's keyframe
prompt (326 words) is over fal/seedream-4.5-edit's budget of 320 words" --
the stored prompt was 308 words, the keyframe auto-fix's correction note
added 18 at send time, uncounted).

At request time a layered (v2) prompt that is over its link's budget -- with
the note the keyframe auto-fix or a regenerate appends after the prompt was
built to that budget, or because it was built to another link's budget -- is
resolved again to the room left (``shots.resolve_stored``): the context
layers make room, the note is kept whole, nothing is refused that can fit.
The prompt hash stays the stored prompt's with its note, so what is current
never moves with the room a link leaves. The same holds for a clip prompt
and a re-animate's note. Only when even the ladder's last rung cannot fit
with the note is the shot refused, naming the note and how long a note fits.

Offline and hermetic (the assets step's fixtures); stdlib + pytest
(DEC-012). Each test reaches the new behaviour, so on the parent commit it
fails on its own.
"""

from __future__ import annotations

import pytest

import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_keyframe_consistency as kc
import test_story_keyframe_fix as kf
from clipping.aistory import prompt_budgets, shots
from clipping.aistory.steps import assets, clips
from test_story_assets_step import hermetic, store  # noqa: F401 -- the step's fixtures (hermetic is autouse)
from test_story_keyframe_gate import unpaced  # noqa: F401 -- the free Gemini tier's pacing lifted
from test_story_video_phase import timings_path  # noqa: F401 -- the measured clip timings under tmp_path

SEEDREAM_EDIT, SEEDANCE = "fal/seedream-4.5-edit", "fal/seedance-1-pro-fast"
NOTE = "Keyframe check: show this shot's action clearly; the frame must show tight close-up on the face"
CLIP_NOTE = "pan slowly and keep both hands on the keyboard in frame the whole time"


def _words(text):
    return len(text.split())


def _longest(store, story_id, key="image_prompt"):
    return max(tas._shots(store, story_id), key=lambda shot: _words(shot[key]))


def _at_budget(monkeypatch, shot, *, key="image_prompt", ceiling="KEYFRAME_CEILING_WORDS"):
    """The kind's quality ceiling lowered to exactly the stored prompt's words:
    the prompt fits its link with no word to spare (the live case: 308 words
    of a 320 budget, 18 of note)."""
    monkeypatch.setattr(prompt_budgets, ceiling, _words(shot[key]))
    return _words(shot[key])


# ======================================================= 1. the live failure

def test_the_keyframe_auto_fix_s_note_makes_room_in_the_prompt_instead_of_stopping_the_step(store, tmp_path,
                                                                                            monkeypatch):
    """The human's report: a shot whose stored prompt sits at its link's
    budget is flagged by J2 and redrawn with a correction note. The redraw
    is sent -- the prompt resolved again to the room the note leaves, the
    note whole at its tail, within the budget -- the step completes, the
    feed says what was shortened, and the shot reads current afterwards."""
    story_id = kf._quality(store, tmp_path)
    kf._seeds(monkeypatch)
    flagged = _longest(store, story_id)
    budget = _at_budget(monkeypatch, flagged)
    assert assets.request_parts(tas._ec(store, story_id), flagged, note=None, link=SEEDREAM_EDIT)["over"] is None
    image, judge = kc.SeededImage(price=kf.PRICE), kf.Judge({flagged["shot_id"]: 1})

    summary, log = kf._run(store, story_id, image=image, vision=judge)

    assert summary["complete"] is True and summary["failed"] == []
    assert len(image.requests) == len(tas._shots(store, story_id)) + 1
    redraw = image.requests[-1]
    note = f"Keyframe check: the frame must show {kf._kiwi(store, story_id)}'s coconut phone"
    assert redraw.extra["name"] == f"shot_{flagged['shot_id'][2:]}"
    # DEC-265: the note ends with the shot's own framing as an order, after J2's text.
    assert f" Author's note: {note}. Frame this as " in redraw.prompt
    assert redraw.prompt.endswith(", nothing wider.")
    assert _words(redraw.prompt) <= budget
    assert redraw.prompt != flagged["image_prompt"] + f" Author's note: {note}."  # the context made room
    # The roles, the beat and the constraints are what the prompt is for: never the part that goes.
    assert redraw.prompt.startswith(flagged["image_prompt"].split(". ")[0])
    assert redraw.prompt.split(" Author's note:")[0].endswith(flagged["image_prompt"].split(". ")[-1])
    assert any(line.startswith(f"ℹ️ Shot {flagged['shot_id']}'s keyframe prompt: its note") and "resolved again"
               in line for line in log)
    shot = kf._shot(store, story_id, flagged["shot_id"])
    # DEC-265: the stored note is J2's text followed by the framing order.
    assert shot["assets"]["note"].startswith(f"{note}. Frame this as ") and shot["assets"]["note"].endswith(", nothing wider.")
    assert shot["assets"]["pending"] is None
    assert assets.shot_state(tas._ec(store, story_id), shot) == "current"
    assert summary["keyframes"]["fix"]["fixed"] == [flagged["shot_id"]]


# ============================================= 2. request_parts, by itself

def test_a_note_on_a_prompt_at_its_budget_is_fitted_and_the_hash_stays_the_stored_prompt_s(store, tmp_path,
                                                                                            monkeypatch):
    """``assets.request_parts``: with room, the stored prompt and the note
    as before (no re-fit); at the budget, the prompt resolved again to the
    room the note leaves, ``refit`` saying from and to how many words; the
    hash is the same in both cases -- the stored prompt's with its note."""
    story_id = kc._layered(store, tmp_path)
    ec = tas._ec(store, story_id)
    shot = _longest(store, story_id)
    roomy = assets.request_parts(ec, shot, note=NOTE, link=SEEDREAM_EDIT)
    assert roomy["over"] is None and roomy["refit"] is None
    assert roomy["prompt"] == shot["image_prompt"] + f" Author's note: {NOTE}."

    budget = _at_budget(monkeypatch, shot)
    fitted = assets.request_parts(ec, shot, note=NOTE, link=SEEDREAM_EDIT)
    assert fitted["over"] is None
    assert _words(fitted["prompt"]) <= budget < _words(roomy["prompt"])
    assert fitted["prompt"].endswith(f" Author's note: {NOTE}.")
    assert fitted["refit"] == {"from": _words(roomy["prompt"]), "to": _words(fitted["prompt"]),
                               "note": _words(roomy["prompt"]) - _words(shot["image_prompt"]), "budget": budget}
    assert fitted["hash"] == roomy["hash"]
    assert fitted["references"] == roomy["references"]
    # Without a note the stored prompt fits as it is: nothing is resolved again.
    plain = assets.request_parts(ec, shot, note=None, link=SEEDREAM_EDIT)
    assert plain["refit"] is None and plain["prompt"] == shot["image_prompt"]
    # A user's override is sent as written, whatever its length.
    override = assets.request_parts(ec, dict(shot, prompt_override=" ".join(["word"] * 400)), note=NOTE,
                                    link=SEEDREAM_EDIT)
    assert override["over"] is None and override["refit"] is None and override["prompt"].endswith(f"{NOTE}.")


def test_a_shot_asked_alone_without_its_previous_keyframe_is_fitted_the_same_way(store, tmp_path, monkeypatch):
    """The continuity path (phase 8 stage B): a shot asked without the
    previous keyframe of its scene is resolved alone by the caller; with a
    note at the budget, that prompt is fitted too, without the slot."""
    story_id = kc._layered(store, tmp_path)
    ec = tas._ec(store, story_id)
    board = tas._board(store, story_id)
    index = kc._continuing(board)[0]
    shot = board["shots"][index]
    assert shots.CONTINUITY_REFERENCE in shot["reference_images"]
    budget = _at_budget(monkeypatch, shot)
    alone = shots.resolve_stored(shot, script=eps._script(store, story_id), storyboard=board, entities=ec.entities,
                                 style_lock=ec.style_lock, consistency_mode="references",
                                 budgets=prompt_budgets.for_links(SEEDREAM_EDIT), continuity=False)
    parts = assets.request_parts(ec, shot, note=NOTE, link=SEEDREAM_EDIT,
                                 alone=(alone["image_prompt"], alone["reference_images"]))
    assert parts["over"] is None and parts["refit"] is not None
    assert _words(parts["prompt"]) <= budget and parts["prompt"].endswith(f"{NOTE}.")
    assert shots.CONTINUITY_REFERENCE not in parts["references"] and "continuity" not in parts["prompt"].lower()


def test_a_note_no_shortening_can_make_room_for_is_refused_naming_the_note(store, tmp_path, monkeypatch):
    """When even the ladder's last rung is over the budget with the note,
    the shot is refused -- nothing sent -- and the sentence names the note,
    how long a note fits this shot, and what to do; the stored prompt's own
    sentence stays for a prompt that is over without any note."""
    story_id = kc._layered(store, tmp_path)
    ec = tas._ec(store, story_id)
    shot = _longest(store, story_id)
    budget = _at_budget(monkeypatch, shot)
    shortest = budget - 5

    def no_room(*_args, **kwargs):
        raise shots.PromptOverBudget("keyframe", shortest, kwargs["budgets"].keyframe)

    monkeypatch.setattr(shots, "resolve_stored", no_room)
    parts = assets.request_parts(ec, shot, note=NOTE, link=SEEDREAM_EDIT)
    over = parts["over"]
    assert over.startswith(f"shot {shot['shot_id']}'s keyframe prompt (") and parts["refit"] is None
    assert f"of them its note) is over fal/seedream-4.5-edit's budget of {budget} words" in over
    assert "shorten the note (about 5 words of note fit this shot on fal/seedream-4.5-edit)" in over
    assert over.endswith("or ask again without one; nothing was sent.")
    assert parts["prompt"] == shot["image_prompt"] + f" Author's note: {NOTE}."
    plain = assets.request_parts(ec, dict(shot, image_prompt=" ".join(["word"] * (budget + 9))), note=None,
                                 link=SEEDREAM_EDIT)["over"]
    assert "built for another link" in plain and "refresh the prompts" in plain


def test_a_legacy_shot_keeps_its_note_at_the_tail_as_it_always_did(store, tmp_path):
    """A v1 shot (no ``prompt_layout``) has no budget: its prompt is the
    stored one with the note at its tail, nothing fitted (RC-Q1)."""
    story_id = tas._episode(store, tmp_path)
    ec = tas._ec(store, story_id)
    shot = tas._shots(store, story_id)[0]
    assert not shot.get("prompt_layout")
    parts = assets.request_parts(ec, shot, note=" ".join(["word"] * 60), link=SEEDREAM_EDIT)
    assert parts["over"] is None and parts["refit"] is None
    assert parts["prompt"].startswith(shot["image_prompt"]) and parts["prompt"].endswith("word word.")


# ==================================================================== 3. clips

def test_a_re_animate_s_note_on_a_clip_prompt_at_its_budget_is_fitted_the_same_way(store, tmp_path, monkeypatch):
    """``clips.clip_request_parts``: a layered clip prompt at its link's
    budget with a re-animate's note is resolved again to the room the note
    leaves -- its context layers first -- and sent with the note whole; the
    hash stays the stored prompt's with its note."""
    story_id = kc._layered(store, tmp_path)
    ec = tas._ec(store, story_id)
    script = eps._script(store, story_id)
    flags = {"keep_still": False, "animate": False, "keep_native_audio": False}
    shot = _longest(store, story_id, key="video_prompt")
    roomy = clips.clip_request_parts(ec, shot, script, tier=2, flags=flags, note=CLIP_NOTE, link=SEEDANCE)
    assert roomy["over"] is None and roomy["refit"] is None and roomy["prompt"].endswith(CLIP_NOTE)

    budget = _at_budget(monkeypatch, shot, key="video_prompt", ceiling="CLIP_CEILING_WORDS")
    fitted = clips.clip_request_parts(ec, shot, script, tier=2, flags=flags, note=CLIP_NOTE, link=SEEDANCE)
    assert fitted["over"] is None
    assert _words(fitted["prompt"]) <= budget < _words(roomy["prompt"])
    assert fitted["prompt"].endswith(CLIP_NOTE)
    assert fitted["refit"] == {"from": _words(roomy["prompt"]), "to": _words(fitted["prompt"]),
                               "note": _words(roomy["prompt"]) - _words(shot["video_prompt"]), "budget": budget}
    assert fitted["hash"] == roomy["hash"]
    # The stays-still clause and the style's motion suffix are the clip prompt's fixed parts: kept.
    suffix = shot["video_prompt"].rstrip(".").split(". ")[-1]
    assert fitted["prompt"].split(". " + CLIP_NOTE)[0].endswith(suffix)
    # Without a note the stored prompt fits as it is.
    assert clips.clip_request_parts(ec, shot, script, tier=2, flags=flags, link=SEEDANCE)["refit"] is None


def test_a_clip_note_no_shortening_can_make_room_for_is_refused_naming_the_note(store, tmp_path, monkeypatch):
    story_id = kc._layered(store, tmp_path)
    ec = tas._ec(store, story_id)
    script = eps._script(store, story_id)
    flags = {"keep_still": False, "animate": False, "keep_native_audio": False}
    shot = _longest(store, story_id, key="video_prompt")
    budget = _at_budget(monkeypatch, shot, key="video_prompt", ceiling="CLIP_CEILING_WORDS")

    def no_room(*_args, **kwargs):
        raise shots.PromptOverBudget("clip", budget - 3, kwargs["budgets"].clip)

    monkeypatch.setattr(shots, "resolve_stored", no_room)
    parts = clips.clip_request_parts(ec, shot, script, tier=2, flags=flags, note=CLIP_NOTE, link=SEEDANCE)
    assert parts["over"].startswith(f"shot {shot['shot_id']}'s clip prompt (") and parts["refit"] is None
    assert "of them its note" in parts["over"] and "about 3 words of note fit this shot on fal/seedance-1-pro-fast" \
        in parts["over"] and parts["over"].endswith("nothing was sent.")


# ================================================== 4. shots.resolve_stored

@pytest.mark.parametrize("continuity", [None, False])
def test_resolve_stored_resolves_a_stored_shot_as_refresh_prompts_does(store, tmp_path, continuity):
    """``shots.resolve_stored`` on the storyboard's own budgets gives back
    the stored prompts byte for byte (so a re-fit with room to spare changes
    nothing), reads the shot's continuity from its references unless told,
    and names the shot and the link when it cannot fit."""
    story_id = kc._layered(store, tmp_path)
    ec = tas._ec(store, story_id)
    board, script = tas._board(store, story_id), eps._script(store, story_id)
    shot = board["shots"][kc._continuing(board)[0]]
    resolved = shots.resolve_stored(shot, script=script, storyboard=board, entities=ec.entities,
                                    style_lock=ec.style_lock, consistency_mode="references", continuity=continuity)
    if continuity is None:
        assert resolved["image_prompt"] == shot["image_prompt"]
        assert resolved["reference_images"] == shot["reference_images"]
    else:
        assert shots.CONTINUITY_REFERENCE not in resolved["reference_images"]
    assert resolved["video_prompt"] == shot["video_prompt"]
    with pytest.raises(shots.PromptOverBudget) as caught:
        shots.resolve_stored(shot, script=script, storyboard=board, entities=ec.entities, style_lock=ec.style_lock,
                             consistency_mode="references",
                             budgets=prompt_budgets.for_links(SEEDREAM_EDIT)._replace(keyframe=20))
    assert caught.value.shot_id == shot["shot_id"] and caught.value.link == SEEDREAM_EDIT
    with pytest.raises(KeyError):
        shots.resolve_stored(dict(shot, scene_id="s99"), script=script, storyboard=board, entities=ec.entities,
                             style_lock=ec.style_lock, consistency_mode="references")
