"""The keyframe auto-fix of a v2 story (phase 8 stage B, items 4-6 of the
brief; the human's choice: up to 2 redraws per flagged shot, at most $0.40
per episode).

After J2 in the assets run, every shot whose current verdict fails is
redrawn with a fresh seed and a note made of its verdict -- said in the
prompt's own terms -- and checked again with the shot after it, until it
passes or used its redraws. It stops at the episode's fix budget, a cap or
the paid gates, the step's time budget, or a cancel; it never touches
keyframes the human approved, and a profile without ``keyframe_fix`` (every
one but quality) never redraws. What it did is ``assets.json``'s
``keyframe_fixes`` and ``keyframe_fix_budget``, the step result's
``keyframes.fix`` and the episode page's per-shot view; the assets estimate
counts its ceiling in the paid plan and its caps, before the clips.

The episode is ``tests/test_story_keyframe_consistency.py``'s layered v2
episode on the quality profile: its keyframes on fal's editor (a fake,
$0.04 an image), J2 a scripted fake. Offline and hermetic. Stdlib + pytest
(DEC-012).
"""

from __future__ import annotations

import itertools
import json

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_episode_steps as eps
import test_story_keyframe_consistency as kc
import test_story_keyframe_gate as kg
from clipping.aistory import prompting, shots
from clipping.cancel import Cancelled
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_keyframe_gate import unpaced  # noqa: F401 -- the free Gemini tier's pacing lifted
from test_story_video_phase import timings_path  # noqa: F401 -- the measured clip timings under tmp_path

NOW = eps.NOW
KIWILO = eps.KIWILO
PRICE = 0.04
SETTINGS = tas._settings(**tas.FAL, ALLOW_PAID="1", VISION_CHAIN="gemini/flash-lite")
MISSING = "Kiwilo's coconut phone"


class Judge(kg.FakeVision):
    """J2, scripted: a shot of *fails* fails its first N checks (None:
    every one) with *MISSING* missing, then passes; every other shot
    passes. *on_call(shot_id, count)* runs before each answer."""

    def __init__(self, fails=None, *, on_call=None, issue=None, framing=None):
        self.fails = dict(fails or {})
        self.on_call = on_call
        self.issue = issue
        # Plan 19 stage 3: {shot_id: framing_issue} -- such a shot fails on its framing alone.
        self.framing = dict(framing or {})
        self.seen = {}
        super().__init__(answer=self.reply)

    def reply(self, request):
        shot_id = request.prompt.split("Image 1 is the keyframe of shot ", 1)[1][:4]
        self.seen[shot_id] = self.seen.get(shot_id, 0) + 1
        if self.on_call is not None:
            self.on_call(shot_id, self.seen[shot_id])
        left = self.fails.get(shot_id, 0)
        if left is None or self.seen[shot_id] <= left:
            if shot_id in self.framing:
                return json.dumps({"shows_beat": True, "missing": [], "framing_issue": self.framing[shot_id],
                                   "continuity_issue": None})
            return json.dumps({"shows_beat": True, "missing": [MISSING],
                               "continuity_issue": self.issue.get(shot_id) if self.issue else None})
        return kg.PASS


def _quality(store, tmp_path):
    story_id = kc._layered(store, tmp_path)
    store.update(story_id, lambda doc: doc["generation_profile"].update(budget_profile="quality"), now=NOW)
    return story_id


def _adapters(image=None, vision=None):
    table = tas._adapters()
    table[("image_edit", "fal")] = image or kc.SeededImage(price=PRICE)
    table[("vision", "gemini")] = vision or Judge()
    for provider in ("fal", "gemini", "local"):
        table.setdefault(("video", provider), tce.NeverVideo())
    return table


def _run(store, story_id, *, image=None, vision=None, settings=SETTINGS, clock=None, ctx=None):
    return tas._run(store, story_id, adapters=_adapters(image, vision), settings=settings, clock=clock, ctx=ctx)


def _ceiling(store, story_id):
    """Plan 28 F1: the episode's fix budget -- its shots x 2 redraws x one keyframe on fal ($0.04)."""
    return round(len(tas._shots(store, story_id)) * 2 * PRICE, 4)


def _seeds(monkeypatch, start=4242):
    from clipping.aistory.steps import entities

    counter = itertools.count(start)
    monkeypatch.setattr(entities, "fresh_seed", lambda: next(counter))


def _kiwi(store, story_id):
    return shots.character_handles(tas._ec(store, story_id).entities["characters"])[KIWILO]


def _shot(store, story_id, shot_id):
    return next(shot for shot in tas._shots(store, story_id) if shot["shot_id"] == shot_id)


# ============================================================ 4. the loop

def test_a_flagged_keyframe_is_redrawn_with_a_fresh_seed_and_its_verdict_s_note_until_it_passes(store, tmp_path,
                                                                                                  monkeypatch):
    from clipping.aistory.steps import assets

    story_id = _quality(store, tmp_path)
    _seeds(monkeypatch)
    image, judge = kc.SeededImage(price=PRICE), Judge({"sh05": 1})

    summary, log = _run(store, story_id, image=image, vision=judge)

    shots_total = len(tas._shots(store, story_id))
    assert len(image.requests) == shots_total + 1
    redraw = image.requests[-1]
    # Plan 19 stage 3, re-pinned on purpose: the note ends with the shot's own framing as an order (F3).
    phrase = prompting.FRAMING_PHRASES[_shot(store, story_id, "sh05")["framing"]]
    note = (f"Keyframe check: the frame must show {_kiwi(store, story_id)}'s coconut phone. Frame this as {phrase}, "
            "nothing wider.")
    assert redraw.extra["name"] == "shot_05" and redraw.seed == 4242
    # The note says who it is about in the prompt's own words, never "the character" (it ends with its own
    # period now: the framing order's).
    # Plan 26 H1: the core ends the request (the master before it, as the link fits it).
    assert redraw.prompt.endswith(_shot(store, story_id, "sh05")["image_prompt"] + f" Author's note: {note}")
    # Checked again, with the shot after it (its previous keyframe changed).
    assert judge.seen["sh05"] == 2 and judge.seen["sh06"] == 2 and judge.seen["sh04"] == 1
    shot = _shot(store, story_id, "sh05")
    assert (shot["assets"]["seed"], shot["assets"]["note"], shot["assets"]["pending"]) == (4242, note, None)
    doc = tas._assets_doc(store, story_id)
    fix = doc["keyframe_fixes"]["sh05"]
    assert (fix["redraws"], fix["spent_usd"], fix["gave_up"]) == (1, PRICE, False)
    first, second = fix["history"]
    assert (first["passed"], first["note"]) == (False, None)
    assert first["issue"] == f"missing {MISSING}" and first["image_sha256"] != second["image_sha256"]
    assert (second["passed"], second["issue"], second["note"]) == (True, None, note)
    assert second["image_sha256"] == doc["keyframe_verdicts"]["sh05"]["image_sha256"]
    # Plan 28 F1, re-pinned on purpose: the budget is the episode's shots x 2 redraws x $0.04.
    assert doc["keyframe_fix_budget"] == {"max_redraws_per_shot": 2, "cap_usd": _ceiling(store, story_id),
                                          "spent_usd": PRICE}
    assert summary["keyframes"]["fix"]["fixed"] == ["sh05"] and summary["keyframes"]["fix"]["redraws"] == 1
    assert summary["keyframes"]["fix"]["message"] == "1 keyframe redrawn and fixed, $0.04"
    assert "🛠 Keyframe auto-fix: 1 keyframe redrawn and fixed, $0.04" in log
    assert summary["complete"] is True and summary["failed"] == []
    assert assets.shot_state(tas._ec(store, story_id), shot) == "current"
    images = [row for row in tas._ledger(store, story_id) if row["unit"] == "image"]
    assert len(images) == shots_total + 1 and {row["est_usd"] for row in images} == {PRICE}


def test_a_shot_still_flagged_after_its_redraws_is_given_up_and_never_redrawn_again(store, tmp_path, monkeypatch):
    story_id = _quality(store, tmp_path)
    _seeds(monkeypatch)
    image, judge = kc.SeededImage(price=PRICE), Judge({"sh05": None})

    summary, _log = _run(store, story_id, image=image, vision=judge)

    assert [request.extra["name"] for request in image.requests].count("shot_05") == 3
    assert judge.seen["sh05"] == 3 and judge.seen["sh06"] == 3
    fix = tas._assets_doc(store, story_id)["keyframe_fixes"]["sh05"]
    assert (fix["redraws"], fix["gave_up"], round(fix["spent_usd"], 4)) == (2, True, 2 * PRICE)
    assert [step["passed"] for step in fix["history"]] == [False, False, False]
    assert [step["note"] is None for step in fix["history"]] == [True, False, False]
    assert summary["keyframes"]["fix"]["gave_up"] == ["sh05"]
    assert summary["keyframes"]["fix"]["message"] == "1 still flagged after 2 redraws, $0.08"

    # The next run asks for nothing: the shot gave up on this keyframe, and every verdict is current.
    image, judge = kc.SeededImage(price=PRICE), Judge({"sh05": None})
    summary, _log = _run(store, story_id, image=image, vision=judge)
    assert image.requests == [] and judge.requests == []
    assert summary["keyframes"]["fix"]["message"] == "1 still flagged after 2 redraws, $0.00"


def test_the_fix_budget_is_sized_to_the_episode_and_kept_across_runs(store, tmp_path, monkeypatch):
    """Plan 28 F1 (DEC-305 §5), re-pinned on purpose: the episode's budget is
    its shots x 2 redraws x one keyframe on its link ($0.04) -- it was $0.40
    whatever the shot count, so 5 shots got their redraws and the rest none.
    Every shot now uses its two; one still flagged after them is said with
    the keyframe approval's own sentence, never left silent."""
    story_id = _quality(store, tmp_path)
    _seeds(monkeypatch)
    every = {shot["shot_id"]: None for shot in tas._shots(store, story_id)}
    image = kc.SeededImage(price=PRICE)

    summary, log = _run(store, story_id, image=image, vision=Judge(every))

    total = len(every)
    ceiling = round(total * 2 * PRICE, 4)
    fix = summary["keyframes"]["fix"]
    assert len(image.requests) == total * 3 and fix["redraws"] == 2 * total and fix["spent_usd"] == ceiling
    assert fix["gave_up"] == list(every) and fix["flagged"] == [] and fix["stopped"] is None
    doc = tas._assets_doc(store, story_id)
    assert doc["keyframe_fix_budget"] == {"max_redraws_per_shot": 2, "cap_usd": ceiling, "spent_usd": ceiling}
    refusal = summary["keyframes"]["refusal"]
    assert refusal.startswith(f"Shot sh01 does not match: missing {MISSING}. Shot sh02 does not match: ")
    assert refusal.endswith("Regenerate them, or upload your own.")
    assert f"⛔ Episode 1's keyframes cannot be approved yet. {refusal}" in log
    # Per episode, not per run: the next run redraws nothing more.
    image = kc.SeededImage(price=PRICE)
    summary, _log = _run(store, story_id, image=image, vision=Judge(every))
    assert image.requests == [] and summary["keyframes"]["fix"]["gave_up"] == list(every)


def test_the_fix_stops_where_a_cap_would_refuse_the_redraw(store, tmp_path, monkeypatch):
    story_id = _quality(store, tmp_path)
    _seeds(monkeypatch)

    def spend(shot_id, count):
        if shot_id == "sh01" and count == 1:
            # Another job's spending, once the images are made: it fills the
            # $4.00 episode cap (DEC-242's caps), so the first redraw is refused.
            tas._spent(store, story_id, 3.02)

    image = kc.SeededImage(price=PRICE)
    summary, _log = _run(store, story_id, image=image, vision=Judge({"sh05": None}, on_call=spend))

    shots_total = len(tas._shots(store, story_id))
    assert len(image.requests) == shots_total  # no redraw
    stopped = summary["keyframes"]["fix"]["stopped"]
    assert stopped.startswith("refused: est $0.040 on episode 1's paid images and voices would bring this episode "
                              "to $4.02 of its $4.00 cap"), stopped
    assert summary["keyframes"]["fix"]["flagged"] == ["sh05"]
    assert tas._assets_doc(store, story_id)["keyframe_fixes"]["sh05"]["redraws"] == 0


def test_the_fix_stops_at_the_paid_gates(store, tmp_path, monkeypatch):
    """allow_paid off: the keyframe link is paid, so no redraw is asked."""
    story_id = _quality(store, tmp_path)
    _run(store, story_id)
    doc = tas._assets_doc(store, story_id)
    doc["keyframe_verdicts"]["sh05"]["missing"] = [MISSING]
    store.write_episode_doc(story_id, 1, "assets.json", doc, now=NOW)
    image = kc.SeededImage(price=PRICE)

    summary, _log = _run(store, story_id, image=image, settings=dict(SETTINGS, ALLOW_PAID="0"))

    assert image.requests == []
    assert "allow_paid" in summary["keyframes"]["fix"]["stopped"]
    assert summary["keyframes"]["fix"]["flagged"] == ["sh05"]


def test_the_fix_stops_when_the_step_s_time_budget_cannot_fit_a_redraw(store, tmp_path, monkeypatch):
    story_id = _quality(store, tmp_path)
    clock = eps.Clock(0.0)

    def slow(_shot_id, _count):
        clock.now += 60.0  # 24 checks: 24 of the step's 30 minutes

    image = kc.SeededImage(price=PRICE)
    summary, _log = _run(store, story_id, image=image, vision=Judge({"sh05": None}, on_call=slow), clock=clock)

    assert len(image.requests) == len(tas._shots(store, story_id))
    assert summary["keyframes"]["fix"]["stopped"] == ("the step's 30-minute budget cannot fit another redraw and "
                                                      "its check")
    assert summary["keyframes"]["fix"]["flagged"] == ["sh05"]


def test_a_cancel_stops_the_fix_keeping_what_it_did(store, tmp_path, monkeypatch):
    story_id = _quality(store, tmp_path)
    _seeds(monkeypatch)
    ctx, _log = tas._ctx(store, story_id, settings=SETTINGS)

    def cancel(shot_id, count):
        if shot_id == "sh05" and count == 2:
            ctx.cancel.cancel()  # the user cancels while the redrawn shot is checked

    with pytest.raises(Cancelled):
        _run(store, story_id, vision=Judge({"sh05": None}, on_call=cancel), ctx=ctx)

    fix = tas._assets_doc(store, story_id)["keyframe_fixes"]["sh05"]
    assert fix["redraws"] == 1 and fix["spent_usd"] == PRICE and len(fix["history"]) == 2
    assert _shot(store, story_id, "sh05")["assets"]["seed"] == 4242


def test_the_note_says_who_a_continuity_issue_is_about_in_the_prompt_s_terms():
    from clipping.aistory.steps import assets

    entities = {"characters": {KIWILO: {"name": "Kiwilo", "descriptor": "A fuzzy kiwi fruit head on a body",
                                        "signature_items": []}},
                "places": {"place_x": {"name": "Le Parloir"}},
                "props": {"prop_x": {"name": "Phone", "descriptor": "A coconut phone with flower buttons"}}}
    verdict = {"shows_beat": False, "missing": ["Phone"],
               "continuity_issue": "Kiwilo's suit turned blue in Le Parloir"}
    kiwi = shots.character_handles(entities["characters"])[KIWILO]
    phone = shots.prop_handles(entities["props"])["prop_x"]
    assert (kiwi, phone) == ("the fuzzy kiwi fruit head on a body", "the coconut phone")
    note = assets.correction_note(entities, verdict)
    assert note == (f"Keyframe check: show this shot's action clearly; the frame must show {phone}; correct this: "
                    f"{kiwi}'s suit turned blue in the set")
    # A layered shot keeps a descriptive name in its note (the v2 name map): never "the coconut the object".
    shot = {"image_prompt": "Prompt.", "prompt_layout": "layered_v1"}
    assert assets.effective_prompt(shot, entities, note) == f"Prompt. Author's note: {note}."
    # A legacy shot's note is swept as it always was.
    assert "the coconut the object" in assets.effective_prompt({"image_prompt": "Prompt."}, entities, note)
    assert len(assets.correction_note(entities, dict(verdict, continuity_issue="x " * 200))) <= 300
    # Plan 19 stage 3 (F3): with the flagged shot, the note ends with its framing as an order -- whatever J2
    # found -- and the order survives the cap (J2's part is cut first, once).
    close_up = {"framing": "close_up", "image_prompt": "Prompt.", "prompt_layout": "layered_v1"}
    framed = assets.correction_note(entities, verdict, close_up)
    assert framed == f"{note}. Frame this as tight close-up on the face, nothing wider."
    assert assets.effective_prompt(close_up, entities, framed) == f"Prompt. Author's note: {framed}"
    long = assets.correction_note(entities, dict(verdict, continuity_issue="x " * 200), close_up)
    assert len(long) <= 300 and long.endswith("x… Frame this as tight close-up on the face, nothing wider.")
    assert long.count("…") == 1
    # A shot without a known framing gets the note as before.
    assert assets.correction_note(entities, verdict, {"framing": None}) == note


def test_a_framing_mismatch_note_restates_the_required_framing(store, tmp_path, monkeypatch):
    """Plan 19 stage 3 (F3): the live walk flagged 7 of 15 keyframes for
    their framing, and 10 redraws whose note echoed J2's prose ("correct
    this: Framing is medium shot instead of tight close-up") fixed one. J2
    names a framing miss in ``framing_issue``; the redraw's note restates
    the framing the shot asks, from the shot's own data, in the prompt's own
    words -- never J2's prose -- with "nothing wider" (not on the widest
    framing, where it means nothing)."""
    from clipping.aistory.steps import assets

    entities = {"characters": {}, "places": {}, "props": {}}
    verdict = {"shows_beat": True, "missing": [], "continuity_issue": None,
               "framing_issue": "a medium shot, not a close-up"}
    for framing, phrase in prompting.FRAMING_PHRASES.items():
        note = assets.correction_note(entities, verdict, {"framing": framing})
        tail = "." if framing == "wide_establishing" else ", nothing wider."
        assert note == f"Keyframe check: draw it again with the framing asked. Frame this as {phrase}{tail}"
        assert "medium shot, not a close-up" not in note

    story_id = _quality(store, tmp_path)
    _seeds(monkeypatch)
    image, judge = kc.SeededImage(price=PRICE), Judge({"sh05": 1}, framing={"sh05": "a wide shot, not this"})

    _summary, log = _run(store, story_id, image=image, vision=judge)

    shot = _shot(store, story_id, "sh05")
    phrase = prompting.FRAMING_PHRASES[shot["framing"]]
    tail = "." if shot["framing"] == "wide_establishing" else ", nothing wider."
    note = f"Keyframe check: draw it again with the framing asked. Frame this as {phrase}{tail}"
    assert image.requests[-1].prompt.endswith(shot["image_prompt"] + f" Author's note: {note}")
    fix = tas._assets_doc(store, story_id)["keyframe_fixes"]["sh05"]
    first, second = fix["history"]
    # J2's own field, kept on the verdict and named in the feed and the history like the other findings.
    assert first["issue"] == "framing: a wide shot, not this" and (second["passed"], second["note"]) == (True, note)
    assert any("Shot sh05: framing: a wide shot, not this" in line for line in log)


# ============================================================ who never redraws

def test_a_profile_without_keyframe_fix_never_redraws(store, tmp_path):
    story_id = kc._layered(store, tmp_path)  # the story's own profile: no keyframe_fix
    image = tas.FakeImage()

    summary, _log = kc._run(store, story_id, edit=image, vision=Judge({"sh05": None}))

    assert len(image.requests) == len(tas._shots(store, story_id))
    assert "fix" not in summary["keyframes"]
    doc = tas._assets_doc(store, story_id)
    assert "keyframe_fixes" not in doc and "keyframe_fix_budget" not in doc
    assert doc["keyframe_verdicts"]["sh05"]["missing"] == [MISSING]


def test_approved_keyframes_are_never_redrawn_on_their_own(store, tmp_path, monkeypatch):
    from clipping.aistory import media_policy, workflow

    story_id = _quality(store, tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(media_policy, "keyframe_fix", lambda story: None)
        _run(store, story_id, vision=Judge({"sh05": None}))
    # Plan 28 F1, re-pinned on purpose: no approval goes over a flagged keyframe any more -- approved
    # once it passed, then flagged by a later check, it is still never redrawn on its own.
    kg._pass_verdict(store, story_id, "sh05")
    workflow.approve_keyframes(store, story_id, 1, now=kg.LATER)
    doc = tas._assets_doc(store, story_id)
    doc["keyframe_verdicts"]["sh05"]["missing"] = [MISSING]
    store.write_episode_doc(story_id, 1, "assets.json", doc, now=kg.LATER)
    image = kc.SeededImage(price=PRICE)

    summary, _log = _run(store, story_id, image=image, vision=Judge({"sh05": None}))

    assert image.requests == [] and "fix" not in summary["keyframes"]
    assert summary["keyframes"]["approval"] == "current"


# ============================================================ 6. the estimate

def test_the_estimate_counts_the_fix_ceiling_in_the_paid_plan_and_its_caps(store, tmp_path):
    from clipping.aistory.steps import assets

    story_id = _quality(store, tmp_path)
    shots_total = len(tas._shots(store, story_id))
    units = tce._units(store, story_id, SETTINGS, adapters=_adapters())

    # Plan 28 F1, re-pinned on purpose: the ceiling is the episode's shots x 2 redraws x $0.04, not $0.40.
    ceiling = round(shots_total * 2 * PRICE, 4)
    assert units["keyframe_fix"] == {
        "max_redraws_per_shot": 2, "cap_usd": ceiling, "spent_usd": 0.0, "est_usd": ceiling, "route_class": "paid",
        "link": "fal/seedream-4.5-edit",
        "message": f"up to ${ceiling:.2f} to redraw flagged keyframes (at most 2 redraws a shot, ${ceiling:.2f} an "
                   "episode)"}
    assert units["est_usd"] == round(shots_total * PRICE + ceiling, 4) and units["over_cap"] is None

    # Plan 28 F1, re-pinned on purpose: the ceiling takes what the caps leave once the rest is counted --
    # a cap the images fit cuts it, never refuses the whole plan for redraws it may not need.
    images = round(shots_total * PRICE, 4)
    tight = dict(SETTINGS, PER_EPISODE_CAP_USD=f"{images + 0.30:.2f}")
    units = tce._units(store, story_id, tight, adapters=_adapters())
    assert units["keyframe_fix"]["est_usd"] == pytest.approx(round(float(f"{images + 0.30:.2f}") - images, 4))
    assert units["over_cap"] is None and "cut to what the caps leave" in units["keyframe_fix"]["message"]
    # A cap the images just fit leaves nothing for redraws: a flagged keyframe then stops the run, said.
    units = tce._units(store, story_id, dict(SETTINGS, PER_EPISODE_CAP_USD=f"{images:.2f}"), adapters=_adapters())
    assert units["keyframe_fix"]["est_usd"] == 0.0 and units["over_cap"] is None
    assert assets.plan_refusal(tas._ec(store, story_id), units) is None

    # Once the keyframes are made and approved, no redraw is to come: nothing is reserved.
    _run(store, story_id)
    from clipping.aistory import workflow

    workflow.approve_keyframes(store, story_id, 1, now=kg.LATER)
    units = tce._units(store, story_id, SETTINGS, adapters=_adapters())
    assert units["keyframe_fix"]["est_usd"] == 0.0 and units["est_usd"] == 0.0
    assert units["keyframe_fix"]["message"] == "The keyframes are approved: none is redrawn on its own."


def test_the_clips_are_planned_before_the_fix_ceiling_which_takes_what_the_caps_leave(store, tmp_path, monkeypatch):
    """Plan 28 F1, re-pinned on purpose: the ceiling sized to the episode
    would leave the clips short under the caps -- they are planned first
    now, and the ceiling takes what is left."""
    from clipping.aistory.steps import assets

    story_id = _quality(store, tmp_path)
    tce._tier(store, story_id, tier=2, budget_profile="quality")
    seen = []
    real = assets.clips.video_units

    def video_units(*args, **kwargs):
        seen.append(kwargs["committed_usd"])
        return real(*args, **kwargs)

    monkeypatch.setattr(assets.clips, "video_units", video_units)
    settings = dict(SETTINGS, **tce.VIDEO_API)
    units = tce._units(store, story_id, settings, adapters=_adapters())

    images = len(tas._shots(store, story_id)) * PRICE
    assert seen == [pytest.approx(images)]
    left = 4.0 - images - units["video"]["est_usd"]
    assert units["keyframe_fix"]["est_usd"] == pytest.approx(round(min(images * 2, left), 4))
    assert units["over_cap"] is None


def test_the_fast_track_prices_the_fix_ceiling(store, tmp_path):
    from clipping.aistory.steps import fast_track

    story_id = _quality(store, tmp_path)
    estimate = fast_track.estimate(tas._ec(store, story_id), env=SETTINGS, adapters=_adapters())

    # Plan 28 F1, re-pinned on purpose: the ceiling is the episode's shots x 2 redraws x $0.04.
    images = len(tas._shots(store, story_id)) * PRICE
    ceiling = round(images * 2, 4)
    assert estimate["keyframe_fix"]["est_usd"] == ceiling
    assert f"up to ${ceiling:.2f} to redraw flagged keyframes" in estimate["paid"]["parts"]
    assert estimate["est_usd"] == pytest.approx(images + ceiling)
    # The pure verdict counts it as a paid part, in the total.
    units = {"images": {"count": 0}, "voices": {"voices": [], "paid_usd": 0.0}, "caps": {"allow_paid": True},
             "over_cap": None, "ready": True,
             "keyframe_fix": {"est_usd": 0.4, "route_class": "paid"}}
    verdict = fast_track.paid_verdict(units, ep=1)
    assert verdict["parts"] == ["up to $0.40 to redraw flagged keyframes"] and verdict["est_usd"] == 0.4
    assert verdict["verdict"] == fast_track.PAID_WITHIN_CAPS


# ============================================================ 7. a manual regenerate

def test_a_shot_regenerate_checks_that_shot_and_the_one_after_it_and_never_auto_fixes(store, tmp_path, monkeypatch):
    """The known follow-up of DEC-239 closed: a single shot-image regenerate
    of a v2 episode runs J2 on that shot and on the one after it (its
    previous keyframe changed) once the image is drawn -- and only that:
    the human asked for this very note, so a flagged result is never
    redrawn on its own."""
    story_id = _quality(store, tmp_path)
    _run(store, story_id)
    _seeds(monkeypatch)
    image, judge = kc.SeededImage(price=PRICE), Judge({"sh05": None})

    result = tas._regenerate_shot(store, story_id, "sh05", note="Mangella glares at Kiwilo",
                                  adapters=_adapters(image, judge), settings=SETTINGS)

    assert [request.extra["name"] for request in image.requests] == ["shot_05"]  # no auto-fix
    assert judge.shots() == ["sh05", "sh06"]
    doc = tas._assets_doc(store, story_id)
    shot = _shot(store, story_id, "sh05")
    verdict = doc["keyframe_verdicts"]["sh05"]
    assert verdict["missing"] == [MISSING] and verdict["prompt_version"] == 3  # plan 28 F2, re-pinned
    from clipping.aistory.steps import assets

    assert verdict["image_sha256"] == assets._sha256_file(assets.shot_image_path(tas._ec(store, story_id), shot))
    assert doc["keyframe_verdicts"]["sh06"]["previous_sha256"] == verdict["image_sha256"]
    assert "keyframe_fixes" not in doc
    assert result["keyframes"] == {"judged": ["sh05", "sh06"], "kept": [], "failed": [], "unavailable": None}
    assert shot["assets"]["note"] == "Mangella glares at Kiwilo"


def test_a_legacy_shot_regenerate_asks_no_keyframe_check(store, tmp_path):
    story_id = tas._episode(store, tmp_path)
    tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    judge = Judge()
    table = tas._adapters(image=tas.FakeImage())
    table[("vision", "gemini")] = judge

    result = tas._regenerate_shot(store, story_id, "sh05", adapters=table,
                                  settings=tas._settings(VISION_CHAIN="gemini/flash-lite"))

    assert judge.requests == [] and "keyframes" not in result


# ============================================================ the data contract

def test_the_episode_page_shows_each_shot_s_verdict_and_fix_and_the_fix_budget(store, tmp_path, monkeypatch):
    from clipping.aistory import workflow

    story_id = _quality(store, tmp_path)
    _seeds(monkeypatch)
    _run(store, story_id, vision=Judge({"sh05": 1, "sh07": None}))

    page = workflow.episode_outputs(store, store.get(story_id), 1)["assets"]
    by_id = {shot["shot_id"]: shot for shot in page["shots"]}
    assert page["keyframes"]["fix_budget"] == {"max_redraws_per_shot": 2, "cap_usd": _ceiling(store, story_id),
                                               "spent_usd": round(3 * PRICE, 4)}
    fixed = by_id["sh05"]
    assert fixed["keyframe_verdict"]["passed"] is True and fixed["keyframe_verdict"]["current"] is True
    assert set(fixed["keyframe_verdict"]) == {"passed", "current", "shows_beat", "missing", "continuity_issue",
                                              "checked_at"}
    assert fixed["keyframe_fix"]["redraws"] == 1 and fixed["keyframe_fix"]["gave_up"] is False
    flagged = by_id["sh07"]
    assert flagged["keyframe_verdict"]["passed"] is False and flagged["keyframe_verdict"]["missing"] == [MISSING]
    assert flagged["keyframe_fix"]["gave_up"] is True and flagged["keyframe_fix"]["redraws"] == 2
    assert by_id["sh01"]["keyframe_fix"] is None and by_id["sh01"]["keyframe_verdict"]["passed"] is True

    # A verdict of other images reads not current.
    doc = tas._assets_doc(store, story_id)
    doc["keyframe_verdicts"]["sh01"]["image_sha256"] = "0" * 64
    store.write_episode_doc(story_id, 1, "assets.json", doc, now=NOW)
    page = workflow.episode_outputs(store, store.get(story_id), 1)["assets"]
    assert page["shots"][0]["keyframe_verdict"]["current"] is False


def test_a_legacy_episode_page_has_no_keyframe_fields(store, tmp_path):
    from clipping.aistory import workflow

    story_id = tas._episode(store, tmp_path)
    tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    page = workflow.episode_outputs(store, store.get(story_id), 1)["assets"]
    assert "keyframes" not in page
    assert not any("keyframe_verdict" in shot or "keyframe_fix" in shot for shot in page["shots"])
