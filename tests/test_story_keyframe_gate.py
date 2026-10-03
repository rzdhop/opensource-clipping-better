"""The keyframe judge (J2) and the keyframe approval of a v2 episode (AI
Story phase 7, stage 6b; A16, DEC-230, RC-Q3).

In the assets step of a v2 story, once the keyframes exist, J2 checks each
shot's keyframe against what the shot must show and against the previous
shot's keyframe -- one free vision call a shot, stored in ``assets.json``'s
``keyframe_verdicts``, never asked again for the same images. No clip is
bought before ``workflow.approve_keyframes`` (``keyframes:<ep>``) is
current: the plan is held, the keyframes made, and the run stops before the
video phase. A legacy episode is never judged or held.

The episode is stage 8's tier-2 fixture (``tests/test_story_video_phase.py``:
its keyframes and voices made with animate off) put on the v2 pipeline; the
vision adapter is a fake answering J2; the video adapter stage 8's fake.
Offline and hermetic (stage 8's ``hermetic`` fixture). The modules are
imported inside the tests, so on the parent commit each test fails on its
own.

Stdlib + pytest (DEC-012); the API test skips without fastapi, like the
other route tests.
"""

from __future__ import annotations

import copy
import json
import shutil
from types import SimpleNamespace

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_episode_steps as eps
import test_story_video_phase as tvp
from clipping.aistory import defaults
from clipping.providers.generation import GenResult
from test_stories_api_phase4 import api  # noqa: F401 -- the throwaway app (skips without fastapi)
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_cli_phase4 import cli  # noqa: F401 -- the CLI against this test's outputs/
from test_story_video_phase import timings_path  # noqa: F401 -- the measured clip timings under tmp_path

NOW = eps.NOW
LATER = "2026-09-28T09:00:00+00:00"
SETTINGS = tce._settings(**tvp.PAID, VISION_CHAIN="gemini/flash-lite")
PASS = json.dumps({"shows_beat": True, "missing": [], "continuity_issue": None})


class FakeVision:
    """A vision adapter answering each request with *answer(request)* (the
    reply's text; default: every keyframe passes), keeping every request.
    Its estimate is the real Gemini adapter's."""

    def __init__(self, answer=None):
        self.answer = answer or (lambda request: PASS)
        self.requests = []

    def estimate(self, link, request):
        from clipping.providers import vision

        return vision.GEMINI_VISION.estimate(link, request)

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.requests.append(copy.copy(request))
        return GenResult(provider=link.provider, model=link.model, paths=(), meta={"text": self.answer(request)})

    def shots(self):
        return [request.prompt.split("Image 1 is the keyframe of shot ", 1)[1][:4] for request in self.requests]


def _failing(*shot_ids, missing=("the coconut phone",)):
    def answer(request):
        if any(f"Image 1 is the keyframe of shot {shot_id}." in request.prompt for shot_id in shot_ids):
            return json.dumps({"shows_beat": False, "missing": list(missing), "continuity_issue": None})
        return PASS
    return answer


@pytest.fixture(autouse=True)
def unpaced(monkeypatch):
    """The free Gemini tier's pacing lifted (its allowance still counts)."""
    monkeypatch.setenv("LIMIT_GEMINI_RPM", "0")


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    return {"root": tmp_path_factory.mktemp("keyframe_gate_episodes"), "kinds": {}}


def _v2_keyframes(store, tmp_path, built, *, v2=True):
    """A copy of the session's tier-2 episode whose keyframes and voices are
    made (stage 8's, animate off), on the v2 pipeline unless *v2* is False;
    its story id."""
    if "made" not in built["kinds"]:
        story_id = tvp._keyframes(store, tmp_path, settings=SETTINGS)
        copy_dir = built["root"] / "made"
        shutil.copytree(store.outputs_dir, copy_dir, symlinks=True)
        built["kinds"]["made"] = (copy_dir, story_id)
    else:
        copy_dir, story_id = built["kinds"]["made"]
        shutil.copytree(copy_dir, store.outputs_dir, symlinks=True, dirs_exist_ok=True)
    if v2:
        store.update(story_id, lambda doc: doc["generation_profile"].update(pipeline=defaults.PIPELINE_V2), now=NOW)
    return story_id


def _adapters(video=None, vision=None):
    table = tvp._adapters(video or tvp.FakeVideo())
    table[("vision", "gemini")] = vision or FakeVision()
    return table


def _run(store, story_id, *, video=None, vision=None, params=None):
    return tas._run(store, story_id, adapters=_adapters(video, vision), settings=SETTINGS, params=params)


def _wf():
    from clipping.aistory import workflow

    return workflow


def _doc(store, story_id):
    return store.read_episode_doc(story_id, 1, "assets.json")


def _approve_keyframes(store, story_id, **kwargs):
    return _wf().approve_keyframes(store, story_id, 1, now=kwargs.pop("now", LATER), **kwargs)


# ============================================================ RC-Q3

def test_no_clip_bought_before_keyframes_approved_v2(store, tmp_path, built):
    from clipping.aistory.steps import assets

    story_id = _v2_keyframes(store, tmp_path, built)
    shots = [shot["shot_id"] for shot in tas._board(store, story_id)["shots"]]

    # The estimate shows the clips' plan, priced, but held: out of the total and of the readiness.
    units = tce._units(store, story_id, SETTINGS, adapters=_adapters())
    video = units["video"]
    assert video["count"] > 1 and video["est_usd"] > 0 and video["ready"] is True
    assert video["hold"] == ("approve the keyframes first (keyframes:1): no clip of a v2 episode is bought before "
                             "they are approved")
    assert video["message"].endswith(f" Held: {video['hold']}.")
    assert units["est_usd"] == 0.0 and units["ready"] is True
    assert not any(row["kind"] == "video" for row in units["paid_links"])

    # The run (animate on, its default) judges the keyframes and buys no clip.
    fake_video, vision = tvp.FakeVideo(), FakeVision()
    ledger_before = len(tas._ledger(store, story_id))
    summary, log = _run(store, story_id, video=fake_video, vision=vision)
    assert fake_video.requests == [] and tvp._video_rows(store, story_id) == []
    assert summary["video"]["hold"] == video["hold"] and summary["video"]["planned"] == 0
    assert "🎬 No clip in this run: approve the keyframes first (keyframes:1): no clip of a v2 episode is bought " \
           "before they are approved." in log
    assert vision.shots() == shots  # one J2 call per shot, in storyboard order
    verdicts = _doc(store, story_id)["keyframe_verdicts"]
    assert sorted(verdicts) == sorted(shots) and all(entry["shows_beat"] for entry in verdicts.values())
    assert summary["keyframes"] == {"judged": shots, "kept": [], "failed": [], "unavailable": None,
                                    "approval": "none"}
    rows = tas._ledger(store, story_id)[ledger_before:]
    assert [row["unit"] for row in rows] == ["token"] * len(shots)
    assert {(row["step"], row["ep"], row["paid"], row["est_usd"]) for row in rows} == {("assets", 1, False, 0.0)}

    # Approved, the next run buys the plan's clips -- and asks J2 nothing (every verdict is current).
    approved = _approve_keyframes(store, story_id)["keyframes_approved"]
    assert approved["at"] == LATER and approved["anyway"] is False
    ec = tas._ec(store, story_id)
    assert approved["fingerprint"] == assets.keyframes_fingerprint(ec, tas._board(store, story_id))
    plan = tce._units(store, story_id, SETTINGS, adapters=_adapters())["video"]
    assert "hold" not in plan and plan["count"] == video["count"]
    fake_video, vision = tvp.FakeVideo(), FakeVision()
    summary, _log = _run(store, story_id, video=fake_video, vision=vision)
    assert len(fake_video.requests) == video["count"] and vision.requests == []
    assert summary["keyframes"]["kept"] == shots and summary["keyframes"]["approval"] == "current"
    assert len(tvp._video_rows(store, story_id)) == video["count"]


def test_a_changed_keyframe_makes_the_approval_stale_and_holds_the_clips_again(store, tmp_path, built):
    from clipping.aistory.steps import assets

    story_id = _v2_keyframes(store, tmp_path, built)
    _run(store, story_id, params={"animate": False})
    _approve_keyframes(store, story_id)
    ec = tas._ec(store, story_id)
    board = tas._board(store, story_id)
    assert assets.keyframes_state(ec, board, _doc(store, story_id)) == "current"

    # A keyframe made again (same prompt, other bytes): derived stale, never cleared.
    shot = board["shots"][2]
    path = assets.shot_image_path(ec, shot)
    with open(path, "ab") as handle:
        handle.write(b"another take")
    doc = _doc(store, story_id)
    assert assets.keyframes_state(ec, board, doc) == "stale" and doc["keyframes_approved"]["at"] == LATER
    assert assets.clip_hold(ec, board, doc) == ("the keyframes changed since they were approved: approve the "
                                                "keyframes first (keyframes:1), again, before any clip is bought")

    fake_video, vision = tvp.FakeVideo(), FakeVision()
    summary, _log = _run(store, story_id, video=fake_video, vision=vision)
    assert fake_video.requests == []
    # J2 asks again only of that shot and of the one after it (its previous keyframe changed).
    assert vision.shots() == [board["shots"][2]["shot_id"], board["shots"][3]["shot_id"]]
    assert summary["keyframes"]["approval"] == "stale"


def test_a_failed_or_missing_verdict_refuses_the_approval_unless_anyway(store, tmp_path, built):
    wf = _wf()
    story_id = _v2_keyframes(store, tmp_path, built)
    shots = [shot["shot_id"] for shot in tas._board(store, story_id)["shots"]]

    # No vision link: J2 cannot run, said in the feed; nothing is judged.
    table = _adapters()
    del table[("vision", "gemini")]
    summary, log = tas._run(store, story_id, adapters=table, settings=SETTINGS, params={"animate": False})
    assert summary["keyframes"]["unavailable"].startswith("no vision link could judge the keyframes")
    assert any(line.startswith("👁 Keyframe check (J2) stopped: no vision link") for line in log)
    with pytest.raises(wf.WorkflowError) as caught:
        _approve_keyframes(store, story_id)
    assert caught.value.code == wf.CONFLICT
    assert str(caught.value).startswith(f"Episode 1's keyframes are not approved: shots {shots[0]}, ")
    assert "have no current keyframe check (J2): run the assets step again (it checks them, free)" in str(caught.value)

    # J2 finds sh03 short of its prop: refused naming it and what it found; anyway approves, recorded.
    _run(store, story_id, vision=FakeVision(_failing("sh03")), params={"animate": False})
    with pytest.raises(wf.WorkflowError) as caught:
        _approve_keyframes(store, story_id)
    assert str(caught.value) == (
        "Episode 1's keyframes are not approved: the keyframe check (J2) found issues in shot sh03 (does not show "
        "the beat, missing the coconut phone). Make the shots again (regenerate them, with a note) and check again, "
        "or approve anyway.")
    approved = _approve_keyframes(store, story_id, approve_anyway=True)["keyframes_approved"]
    assert approved["anyway"] is True
    # The verdicts and the assets' own approval never move with it.
    assert _doc(store, story_id)["keyframe_verdicts"]["sh03"]["missing"] == ["the coconut phone"]


def test_the_approval_needs_every_keyframe_and_an_approved_storyboard(store, tmp_path, built):
    from clipping.aistory.steps import assets

    wf = _wf()
    story_id = _v2_keyframes(store, tmp_path, built)
    ec = tas._ec(store, story_id)
    shot = tas._board(store, story_id)["shots"][1]
    path = assets.shot_image_path(ec, shot)
    path_bytes = open(path, "rb").read()
    import os

    os.remove(path)
    with pytest.raises(wf.WorkflowError) as caught:
        _approve_keyframes(store, story_id, approve_anyway=True)  # anyway never covers a missing keyframe
    assert str(caught.value) == (f"Episode 1's shot {shot['shot_id']} has no current keyframe: make it (the assets "
                                 f"step, or regenerate shot:1:{shot['shot_id']}) or lock it, then approve the "
                                 "keyframes.")
    with open(path, "wb") as handle:
        handle.write(path_bytes)
    board = tas._board(store, story_id)
    board["approved_at"] = None
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    with pytest.raises(wf.WorkflowError) as caught:
        _approve_keyframes(store, story_id, approve_anyway=True)
    assert str(caught.value).startswith("Approve episode 1's storyboard first")


def test_a_clip_regenerate_is_refused_while_the_keyframes_are_not_approved(store, tmp_path, built):
    from clipping.aistory.steps import regenerate

    wf = _wf()
    story_id = _v2_keyframes(store, tmp_path, built)
    _run(store, story_id, params={"animate": False})
    target = "shot:1:sh01:video"
    parsed = regenerate.parse_target(target)
    with pytest.raises(wf.WorkflowError) as caught:
        wf.check_episode_target(store, store.get(story_id), parsed)
    assert caught.value.code == wf.CONFLICT
    assert str(caught.value) == (f"Cannot regenerate '{target}': approve the keyframes first (keyframes:1): no clip "
                                 "of a v2 episode is bought before they are approved.")
    _approve_keyframes(store, story_id)
    wf.check_episode_target(store, store.get(story_id), parsed)  # no refusal once approved


def test_a_legacy_episode_is_never_judged_held_or_keyframe_approved(store, tmp_path, built):
    wf = _wf()
    story_id = _v2_keyframes(store, tmp_path, built, v2=False)
    video = tce._units(store, story_id, SETTINGS, adapters=_adapters())["video"]
    assert "hold" not in video and "Held" not in video["message"]
    fake_video, vision = tvp.FakeVideo(), FakeVision()
    summary, _log = _run(store, story_id, video=fake_video, vision=vision)
    assert len(fake_video.requests) == video["count"] > 0  # bought as before
    assert vision.requests == [] and "keyframes" not in summary and "hold" not in summary["video"]
    doc = _doc(store, story_id)
    assert "keyframe_verdicts" not in doc and "keyframes_approved" not in doc
    with pytest.raises(wf.WorkflowError) as caught:
        _approve_keyframes(store, story_id)
    assert str(caught.value).startswith("Episode 1's keyframes have no approval of their own")
    page = wf.episode_outputs(store, store.get(story_id), 1)
    assert "keyframes" not in page["assets"]


# ============================================================ J2's request

def test_j2_sends_the_keyframe_the_previous_one_and_what_the_shot_must_show(store, tmp_path, built):
    from clipping.aistory import prompts
    from clipping.aistory.steps import assets

    story_id = _v2_keyframes(store, tmp_path, built)
    vision = FakeVision()
    _run(store, story_id, vision=vision, params={"animate": False})
    ec = tas._ec(store, story_id)
    board = tas._board(store, story_id)
    first, second = vision.requests[:2]
    paths = [assets.shot_image_path(ec, shot) for shot in board["shots"][:2]]
    assert first.images == (paths[0],) and second.images == (paths[1], paths[0])
    assert first.extra == second.extra == {"max_tokens": 110, "temperature": prompts.ANALYTIC_TEMPERATURE}
    assert "Image 2" not in first.prompt and "- continuity_issue: null (this is the episode's first shot)" \
        in first.prompt
    # Phase 8 stage B (J2 version 2), re-pinned on purpose: J2 is told whether image 2 is in the same scene.
    assert board["shots"][0]["scene_id"] == board["shots"][1]["scene_id"]
    assert (f"Image 2 is the keyframe of the shot right before it ({board['shots'][0]['shot_id']}), in the same "
            "scene.") in second.prompt
    shot = board["shots"][1]
    assert f"What shot {shot['shot_id']} must show:\nWhat happens: " in second.prompt
    assert "@char_" not in second.prompt and "#place_" not in second.prompt
    assert second.prompt.endswith('"required":["shows_beat","missing","continuity_issue"],"additionalProperties":'
                                  'false}')
    verdict = _doc(store, story_id)["keyframe_verdicts"][shot["shot_id"]]
    assert verdict["image_sha256"] == assets._sha256_file(paths[1])
    assert verdict["previous_sha256"] == assets._sha256_file(paths[0])
    assert verdict["link"] == "gemini/flash-lite" and verdict["checked_at"]
    # The page shows the approval (a v2 episode's only).
    page = _wf().episode_outputs(store, store.get(story_id), 1)
    # Phase 8 stage B, re-pinned on purpose: the block gains the auto-fix budget (null: none ran).
    assert page["assets"]["keyframes"] == {"approval": "none", "approved_at": None, "anyway": None,
                                           "target": "keyframes:1", "fix_budget": None}


def test_a_stop_mid_check_keeps_every_verdict_judged_so_far(store, tmp_path, built):
    """The step's budget spent between two J2 calls: the step stops naming
    what is left, and the verdicts judged before it are written -- the next
    run asks J2 only for the rest."""
    from clipping.aistory import steps

    story_id = _v2_keyframes(store, tmp_path, built)
    shots = [shot["shot_id"] for shot in tas._board(store, story_id)["shots"]]
    clock = eps.Clock(0.0)

    def slow(request):
        clock.now += 600.0  # each call takes ten minutes of the step's thirty
        return PASS

    with pytest.raises(steps.StepFailed) as caught:
        tas._run(store, story_id, adapters=_adapters(vision=FakeVision(slow)), settings=SETTINGS, clock=clock,
                 params={"animate": False})
    assert "the keyframe check (J2) of shots" in str(caught.value)
    judged = _doc(store, story_id)["keyframe_verdicts"]
    assert sorted(judged) == sorted(shots[:3])

    vision = FakeVision()
    _run(store, story_id, vision=vision, params={"animate": False})
    assert vision.shots() == shots[3:]


MEASURED_J2_TEXT = 831


def test_the_j2_text_at_its_worst_case_fits_the_default_pack_budget():
    """DEC-138's method on J2's text (a vision call with no pack, so no
    INPUT_BUDGET entry, U1's precedent): the brief of a shot at every cap --
    a 400-character action full of tags named by 60-character names (the
    brief keeps 320 characters of it), 8 subjects (5 characters, a place, 2
    props) with 60-character names and 45-word descriptors (100 characters
    shown), 4 staging entries at their 120-character facing and expression
    (60 shown) -- plus the ask and the schema; the images are the adapter's
    (258 tokens each, estimated apart)."""
    from clipping.aistory import context, prompts, schemas
    from clipping.aistory.steps import judge

    def name(i):
        return f"{'N' * 59}{i}"

    chars = {f"char_c{i}": {"name": name(i), "descriptor": " ".join(["descriptive"] * 45)} for i in range(5)}
    props = {f"prop_p{i}": {"name": name(i), "descriptor": " ".join(["material"] * 45)} for i in range(2)}
    places = {"place_set": {"name": name(9)}}
    ec = SimpleNamespace(entities={"characters": chars, "places": places, "props": props})
    tags = [f"@{cid}" for cid in chars] + ["#place_set:night"] + [f"%{pid}" for pid in props]
    shot = {"shot_id": "sh10", "framing": "medium_two_shot", "subject_tags": tags,
            "action": ("@char_c0 hands %prop_p0 to @char_c1 " * 20)[:400],
            "staging": [{"subject": f"@char_c{i}", "position": "left", "facing": "f" * 120, "expression": "e" * 120}
                        for i in range(4)]}
    assert len(shot["subject_tags"]) == 8 and len(shot["action"]) == 400
    text = prompts.j2_prompt_text(shot_id="sh10", brief=judge.keyframe_brief(ec, shot), previous_shot_id="sh09")
    tokens = context.estimate_tokens(text, "")
    assert tokens == MEASURED_J2_TEXT
    assert tokens <= context.PACK_TOKEN_BUDGET
    assert schemas.KEYFRAME_MISSING_MAX == prompts.J2_MISSING_MAX


MEASURED_J2_V2_TEXT = 1145


def test_the_j2_v2_text_at_its_worst_case_fits_the_default_pack_budget():
    """Phase 8 stage B (J2 version 2), DEC-138's method again: the same shot
    at every cap, now with looks -- each of the 5 characters with a look at
    its word caps (presentation 8, build 15, face 15, hair 12, skin 12 words
    of 9 characters: 120 shown) and a 20-word wardrobe set (90 shown) --,
    the 4 identity sheets J2 sends at most (``prompts.J2_MAX_SHEETS``) named
    by 60-character names, and the longest continuity ask (a scene change
    after a previous keyframe)."""
    from clipping.aistory import context, prompts
    from clipping.aistory.steps import judge

    def name(i):
        return f"{'N' * 59}{i}"

    def words(n):
        return " ".join(["eightchr"] * n)

    look = {"presentation": words(8), "build": words(15), "face": words(15), "hair": words(12),
            "skin_material": words(12), "silhouette": words(12), "height_cm": 100, "palette": ["red"],
            "wardrobe_sets": [{"id": "daily", "context": words(8), "items": words(20)}], "season_change": None}
    chars = {f"char_c{i}": {"name": name(i), "descriptor": words(45), "look": look} for i in range(5)}
    props = {f"prop_p{i}": {"name": name(i), "descriptor": " ".join(["material"] * 45)} for i in range(2)}
    places = {"place_set": {"name": name(9)}}
    ec = SimpleNamespace(entities={"characters": chars, "places": places, "props": props})
    tags = [f"@{cid}" for cid in chars] + ["#place_set:night"] + [f"%{pid}" for pid in props]
    shot = {"shot_id": "sh10", "framing": "medium_two_shot", "subject_tags": tags,
            "action": ("@char_c0 hands %prop_p0 to @char_c1 " * 20)[:400],
            "staging": [{"subject": f"@char_c{i}", "position": "left", "facing": "f" * 120, "expression": "e" * 120}
                        for i in range(4)]}
    kc = judge.KeyframeContext(sheets={cid: f"/sheets/{cid}.png" for cid in chars},
                               scenes={"sh10": "s04", "sh09": "s03"})
    request, has_previous = judge.j2_request(ec, shot, "/k/sh10.png", "sh09", "/k/sh09.png", kc)
    assert has_previous and len(request.images) == 2 + prompts.J2_MAX_SHEETS
    assert "never the set or the light, which change with the scene" in request.prompt
    tokens = context.estimate_tokens(request.prompt, "")
    assert tokens == MEASURED_J2_V2_TEXT
    assert tokens <= context.PACK_TOKEN_BUDGET


# ============================================================ the API and the CLI

def test_the_approve_route_takes_keyframes_and_approve_anyway_without_auth(api, tmp_path, built):
    story_id = _v2_keyframes(api.store, tmp_path, built)
    tas._run(api.store, story_id, adapters=_adapters(vision=FakeVision(_failing("sh02"))), settings=SETTINGS,
             params={"animate": False})
    path = f"/api/stories/{story_id}/approve/keyframes:1"

    refused = api.client.post(path)
    assert refused.status_code == 409, refused.text
    assert "shot sh02 (does not show the beat, missing the coconut phone)" in refused.json()["detail"]
    assert api.client.post(f"/api/stories/{story_id}/approve/keyframes:99").status_code == 400

    approved = api.client.post(path, json={"approve_anyway": True})
    assert approved.status_code == 200, approved.text
    keyframes = approved.json()["assets"]["keyframes"]
    assert keyframes["approval"] == "current" and keyframes["anyway"] is True
    assert api.store.read_episode_doc(story_id, 1, "assets.json")["keyframes_approved"]["anyway"] is True
    # approve_anyway is still refused for any document but script:<ep> and keyframes:<ep>.
    other = api.client.post(f"/api/stories/{story_id}/approve/storyboard:1", json={"approve_anyway": True})
    assert other.status_code == 400 and "keyframes:<ep>" in other.json()["detail"]


def test_the_cli_approves_keyframes_and_refuses_any_other_document(cli, tmp_path, built):
    story_id = _v2_keyframes(cli.store, tmp_path, built)
    _run(cli.store, story_id, vision=FakeVision(_failing("sh02")), params={"animate": False})

    assert cli.run("approve", story_id, "keyframes:1") == 1
    assert "shot sh02 (does not show the beat" in cli.capsys.readouterr().err
    assert cli.run("approve", story_id, "keyframes:1", "--anyway") == 0
    out = cli.capsys.readouterr().out
    assert out.startswith("✅ Episode 1's keyframes approved anyway (fingerprint ")
    assert cli.run("approve", story_id, "script:1") == 2
    assert "'script:1' is not keyframes:N" in cli.capsys.readouterr().err
