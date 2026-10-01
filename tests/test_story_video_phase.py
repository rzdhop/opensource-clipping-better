"""The video phase of the assets step and ``shot:<ep>:<shid>:video`` (AI Story
phase 6, stage 8; spec 2.8, 8.1, 8.5; DEC-152..154, DEC-202, DEC-203, A-087).

At tier >= 2, with the step's ``animate`` param on (its default), the assets
step ends by animating exactly the clips its estimate listed
(``asset_units()["video"]["plan"]``, RC-V6): each on the plan's one link --
never another (RC-V5) -- through the generation cache, so a paid clip is
journaled at submit, bought once and booked in seconds (RC-V3). A clip that
fails is recorded as failed and the next planned shot goes on; the link gone
for now stops the rest with the video offer; a clip still queued when its
poll runs out is collected by the next run, which the estimate prices at $0
(DEC-152). A plan that cannot run stops
the step before any call, images included. ``shot:<ep>:<shid>:video`` makes
one clip again, its fresh seed kept as ``pending`` before the call
(DEC-154). A tier-1 run is the parent commit's, byte for byte (RC-V1).

The story, the fakes and the hermetic fixture are the assets step's own
(``tests/test_story_assets_step.py``), the tier and video settings the
estimate's (``tests/test_story_clip_estimate.py``). Every clip answers
through a counting fake adapter; nothing leaves the process. Stdlib + pytest
(DEC-012); the new behaviour is reached inside the tests, so on the parent
commit each test fails on its own.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_episode_steps as eps
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from clipping.aistory import steps
from clipping.providers import pricing
from clipping.providers.errors import ProviderError
from clipping.providers.gencache import RequestFailed
from clipping.providers.generation import GenResult
from clipping.providers.registry import describe
from clipping.providers.transport import HttpStatusError

NOW = tas.NOW
SEEDANCE, KLING = tce.SEEDANCE, "fal/kling-2.5-turbo-std"
SEEDANCE_PRICE = tce.SEEDANCE_PRICE
MP4 = b"\x00\x00\x00\x18ftypmp42"
# Paid on, caps that hold the one_dollar plan (the episode's $0.30 bounds it).
PAID = {"ALLOW_PAID": "1", "PER_EPISODE_CAP_USD": "0.30", "DAILY_CAP_USD": "5", "PER_STORY_CAP_USD": "5"}
BOTH_VIDEO = {"VIDEO_CHAIN": f"{SEEDANCE},{KLING}", **tas.FAL}

# A tier-1 run's documents (every timestamp masked) and summary, computed on
# the parent commit (b00d527) with this file's fixture before any stage-8 line
# existed: RC-V1's guard.
TIER1_BOARD_SHA = "859441d71cb1595ab00ad8beaa3de9ed03ca546b97fc744fb478809619544cf4"
TIER1_ASSETS_SHA = "1dcdc000b37ba3851e3c22448b880c8566d1d60f2d895d69cd554c49ae272d9e"
TIER1_EPISODE_LEDGER_SHA = "cba8b99eb6a38426ec2a4395e0fb970e6aaa7e5affd4a70fdec1c69c0aeb4459"
TIER1_STORY_LEDGER_SHA = "1ae2004a3c1e537b128d95cfb3b17a91e8a2669123f4710fcc9d87b2aa7164ec"
TIER1_SUMMARY_SHA = "8b52b4407afedca93e1799160ebfcff9cd76d43a110dc43057ca997d0e80a33f"
_VOLATILE = {"ts", "created_at", "updated_at", "generated_at", "approved_at", "since", "requested_at", "at"}


@pytest.fixture(autouse=True)
def timings_path(monkeypatch, tmp_path):
    """The measured clip timings (``data/gen_timings.json``) under tmp_path."""
    monkeypatch.setenv("GEN_TIMINGS_PATH", str(tmp_path / "data" / "gen_timings.json"))


class FakeVideo:
    """The fal video adapter's contract, faked: every generate is one submit
    (journaled through ``on_submit`` as fal's queue does), counted with its
    link, and answers a small mp4 naming the request. A name in *fail_for*
    is settled by the provider without a clip after its submit
    (``RequestFailed``, booked); one in *unauthorized* is refused HTTP 401
    before any submit; one in *slow_for* is still in the queue when its poll
    runs out -- and on every resume until *slow_for* is emptied.
    *on_generate(request)* runs first."""

    def __init__(self, *, fail_for=(), unauthorized=(), slow_for=(), on_generate=None):
        self.fail_for = set(fail_for)
        self.unauthorized = set(unauthorized)
        self.slow_for = set(slow_for)
        self.on_generate = on_generate
        self.requests = []
        self.links = []
        self.resumed = []

    def estimate(self, link, request):
        return pricing.estimate(link, int(request.duration_s))

    def probe(self, link, **_kwargs):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, on_submit=None, **_kwargs):
        self.requests.append(copy.copy(request))
        self.links.append(describe(link))
        if self.on_generate is not None:
            self.on_generate(request)
        name = request.extra["name"]
        if name in self.unauthorized:
            raise HttpStatusError(401, "https://queue.fal.run/fal-ai/test", "Unauthorized")
        if on_submit is not None:
            on_submit({"request_id": f"req-{len(self.requests)}", "status_url": "https://queue.fal.run/s",
                       "response_url": "https://queue.fal.run/r"})
        if name in self.fail_for:
            raise RequestFailed(f"{describe(link)}: request FAILED (synthetic)")
        return self._answer(link, request)

    def resume(self, link, request, entry, *, credentials, on_log, transport=None, **_kwargs):
        self.resumed.append(entry["request"]["request_id"])
        return self._answer(link, request)

    def _answer(self, link, request):
        name = request.extra["name"]
        if name in self.slow_for:
            raise ProviderError(f"{describe(link)}: request still IN_QUEUE after 600s")
        path = os.path.join(request.out_dir, f"{name}.mp4")
        with open(path, "wb") as fh:
            fh.write(MP4 + f"{name}:{request.seed}:{request.duration_s}".encode())
        return GenResult(provider=link.provider, model=link.model, paths=(path,), seed=request.seed)

    def names(self):
        return [request.extra["name"] for request in self.requests]


def _adapters(video=None, *, image=None, edge=None):
    table = tce._adapters(image=image or tas.FakeImage(), edge=edge)
    if video is not None:
        table[("video", "fal")] = video
    return table


def _keyframes(store, tmp_path, *, settings):
    """A tier-2 story whose episode 1 has its images and voices, made with
    ``animate`` off (no clip, no video call): the estimate now is the one a
    run sees."""
    story_id = tas._episode(store, tmp_path)
    tce._tier(store, story_id)
    never = tce.NeverVideo()
    summary, _log = tas._run(store, story_id, adapters=_adapters(never), params={"animate": False},
                             settings=settings)
    assert summary["complete"] is True
    return story_id


def _plan(store, story_id, settings, adapters):
    return tce._units(store, story_id, settings, adapters=adapters)["video"]


def _video_rows(store, story_id):
    return [row for row in tas._ledger(store, story_id) if row["unit"] == "second"]


def _shot(store, story_id, shot_id):
    return next(shot for shot in tas._shots(store, story_id) if shot["shot_id"] == shot_id)


def _mask(value):
    if isinstance(value, dict):
        return {key: ("<t>" if key in _VOLATILE and item is not None else _mask(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask(item) for item in value]
    return value


def _masked_sha(path) -> str:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return hashlib.sha256(json.dumps(_mask(data), sort_keys=True).encode()).hexdigest()


# ============================================================ fail-first

def test_a_tier_2_run_animates_exactly_the_clips_its_estimate_listed_and_books_their_seconds(store, tmp_path):
    """RC-V6: the estimate shown before the run lists the one_dollar plan's
    clips; the run asks for exactly those -- the same shots, in plan order,
    each once at its planned length on the plan's link -- books each as
    seconds x price, keeps the clip at ``assets/clips/shot_NN.mp4`` and
    records it next to the shot's image (the storyboard approval kept)."""
    settings = tce._settings(**PAID)
    story_id = _keyframes(store, tmp_path, settings=settings)
    video = FakeVideo()
    adapters = _adapters(video)
    plan = _plan(store, story_id, settings, adapters)
    assert plan["ready"] is True and plan["link"] == SEEDANCE and plan["count"] == len(plan["plan"]) > 1
    board_before = tas._board(store, story_id)
    ledger_before = len(tas._ledger(store, story_id))

    summary, log = tas._run(store, story_id, adapters=adapters, settings=settings)

    planned = [(row["shot_id"], row["clip_s"]) for row in plan["plan"]]
    assert [(f"sh{name[5:]}", request.duration_s) for name, request in zip(video.names(), video.requests)] == planned
    assert set(video.links) == {SEEDANCE} and len(video.requests) == len(planned)  # one submit per paid clip
    rows = tas._ledger(store, story_id)[ledger_before:]
    assert [(row["provider"], row["unit"], row["qty"], row["paid"]) for row in rows] == [
        ("fal", "second", clip_s, True) for _shot_id, clip_s in planned]
    assert [row["est_usd"] for row in rows] == [pytest.approx(row["est_usd"]) for row in plan["plan"]]
    assert sum(row["est_usd"] for row in rows) == pytest.approx(plan["seconds"] * SEEDANCE_PRICE)

    board = tas._board(store, story_id)
    assert board["approved_at"] == board_before["approved_at"] == NOW
    for shot_id, clip_s in planned:
        shot = _shot(store, story_id, shot_id)
        clip = shot["assets"]["clip"]
        assert (clip["state"], clip["link"], clip["route"], clip["clip_s"]) == ("current", SEEDANCE, "paid", clip_s)
        assert clip["est_usd"] == pytest.approx(clip_s * SEEDANCE_PRICE) and clip["cache_key"]
        assert shot["assets"]["video"] == f"assets/clips/shot_{shot_id[2:]}.mp4"
        path = tas._episode_file(store, story_id, "assets", "clips", f"shot_{shot_id[2:]}.mp4")
        assert path.read_bytes().startswith(MP4)
        image = tas._episode_file(store, story_id, *shot["assets"]["image"].split("/"))
        assert clip["image_sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    unplanned = [shot for shot in board["shots"] if shot["shot_id"] not in dict(planned)]
    assert all("clip" not in shot["assets"] and shot["assets"]["video"] is None for shot in unplanned)
    assert summary["video"] == {
        "animate": True, "planned": len(planned), "made": len(planned), "reused": 0, "failed": [],
        "seconds": plan["seconds"], "usd": pytest.approx(plan["est_usd"]), "link": SEEDANCE, "route": "paid",
        "gone": None}
    assert summary["failed"] == [] and summary["complete"] is True


def test_a_clip_that_fails_is_recorded_failed_and_no_other_link_is_ever_asked(store, tmp_path):
    """The plan's link (seedance) settles one planned clip without an answer:
    that shot's clip is ``failed`` with the reason and its regenerate target,
    its video cleared; kling, the next link of VIDEO_CHAIN, is never asked --
    not for that shot, not for any -- and every other planned shot is still
    animated."""
    settings = tas._settings(**PAID, **BOTH_VIDEO)
    story_id = _keyframes(store, tmp_path, settings=settings)
    plan = _plan(store, story_id, settings, _adapters(FakeVideo()))
    assert plan["link"] == SEEDANCE
    failing = plan["plan"][1]["shot_id"]
    video = FakeVideo(fail_for={f"shot_{failing[2:]}"})

    summary, log = tas._run(store, story_id, adapters=_adapters(video), settings=settings)

    assert set(video.links) == {SEEDANCE}
    assert video.names() == [f"shot_{row['shot_id'][2:]}" for row in plan["plan"]]
    clip = _shot(store, story_id, failing)["assets"]["clip"]
    assert clip["state"] == "failed" and "request FAILED (synthetic)" in clip["reason"]
    assert _shot(store, story_id, failing)["assets"]["video"] is None
    others = [row["shot_id"] for row in plan["plan"] if row["shot_id"] != failing]
    assert all(_shot(store, story_id, shot_id)["assets"]["clip"]["state"] == "current" for shot_id in others)
    assert [item["shot_id"] for item in summary["video"]["failed"]] == [failing]
    assert {"what": f"clip of shot {failing}", "target": f"shot:1:{failing}:video",
            "reason": summary["video"]["failed"][0]["reason"]} in summary["failed"]
    assert "no other link was tried" in summary["video"]["failed"][0]["reason"]
    # The provider held the request: it stays booked (DEC-153), once.
    assert len(_video_rows(store, story_id)) == len(plan["plan"])


def test_one_submit_per_paid_clip_and_a_second_run_with_the_same_inputs_calls_and_books_nothing(store, tmp_path):
    """A run buys each planned clip once. Run again: every clip is current,
    so no video call and no ledger row. A clip file lost from disk is asked
    again as the very request it was, which the generation journal holds
    bought and kept: the estimate prices it $0 ("booked") and the cache
    serves it -- still no call, still $0."""
    settings = tce._settings(**PAID)
    story_id = _keyframes(store, tmp_path, settings=settings)
    video = FakeVideo()
    tas._run(store, story_id, adapters=_adapters(video), settings=settings)
    made = len(video.requests)
    assert made == len(set(request.extra["name"] for request in video.requests)) > 1
    ledger = tas._ledger(store, story_id)

    again = FakeVideo()
    plan = _plan(store, story_id, settings, _adapters(again))
    assert (plan["count"], plan["seconds"], plan["est_usd"]) == (0, 0, 0.0) and len(plan["plan"]) >= made
    summary, _log = tas._run(store, story_id, adapters=_adapters(again), settings=settings)

    assert again.requests == [] and tas._ledger(store, story_id) == ledger
    assert (summary["video"]["made"], summary["video"]["reused"], summary["video"]["usd"]) == (0, len(plan["plan"]),
                                                                                              0.0)

    first = next(shot for shot in tas._shots(store, story_id) if shot["assets"].get("clip"))
    tas._episode_file(store, story_id, *first["assets"]["video"].split("/")).unlink()
    plan = _plan(store, story_id, settings, _adapters(again))
    assert plan["plan"][0] == {"shot_id": first["shot_id"], "clip_s": 2, "est_usd": 0.0, "why": "booked"}
    assert (plan["count"], plan["est_usd"]) == (0, 0.0)
    summary, _log = tas._run(store, story_id, adapters=_adapters(again), settings=settings)

    assert again.requests == [] and tas._ledger(store, story_id) == ledger
    assert _shot(store, story_id, first["shot_id"])["assets"]["clip"]["state"] == "current"
    assert tas._episode_file(store, story_id, *first["assets"]["video"].split("/")).is_file()
    assert summary["video"]["usd"] == 0.0


def test_a_clip_still_generating_when_its_poll_runs_out_is_collected_by_the_next_run_never_bought_twice(
        store, tmp_path):
    """DEC-152: a planned clip still queued when its poll runs out stays
    ``submitted`` in the journal, booked once; its shot says "still
    generating -- press Continue". The next run's estimate keeps it at $0
    (the journal holds it bought, whatever the cap has left) and the run
    collects it: a resume, no second submit, no second ledger row."""
    settings = tce._settings(**PAID)
    story_id = _keyframes(store, tmp_path, settings=settings)
    plan = _plan(store, story_id, settings, _adapters(FakeVideo()))
    slow = plan["plan"][0]["shot_id"]
    video = FakeVideo(slow_for={f"shot_{slow[2:]}"})

    summary, _log = tas._run(store, story_id, adapters=_adapters(video), settings=settings)

    reason = _shot(store, story_id, slow)["assets"]["clip"]["reason"]
    assert "still generating" in reason and "press Continue" in reason
    assert [item["shot_id"] for item in summary["video"]["failed"]] == [slow]
    rows = _video_rows(store, story_id)
    assert len(rows) == len(video.requests) == len(plan["plan"])  # the slow one booked at its submit, once

    video.slow_for.clear()
    video.requests.clear()
    again = _plan(store, story_id, settings, _adapters(video))
    assert again["plan"][0] == {"shot_id": slow, "clip_s": plan["plan"][0]["clip_s"], "est_usd": 0.0,
                                "why": "booked"}
    summary, _log = tas._run(store, story_id, adapters=_adapters(video), settings=settings)

    assert video.requests == [] and len(video.resumed) >= 1 and set(video.resumed) == {"req-1"}
    assert _video_rows(store, story_id) == rows
    clip = _shot(store, story_id, slow)["assets"]["clip"]
    assert clip["state"] == "current" and _shot(store, story_id, slow)["assets"]["video"]
    assert (summary["video"]["made"], summary["video"]["usd"], summary["video"]["failed"]) == (1, 0.0, [])


def test_a_clip_still_generating_is_never_offered_its_regenerate_only_continue_a_settled_one_is(
        store, tmp_path):
    """A clip whose request is still open in the journal (``submitted``: its
    poll ran out) must not be asked again with a new seed -- that would buy a
    second clip while the first is billed. The step's summary and message
    offer only Continue for it (no ``shot:<ep>:<shid>:video``); the
    regenerate is refused before any call, naming Continue, both as a job
    (the runner) and before one exists (the workflow's check, the API's
    409). A clip the provider settled (``failed``, final) stays
    regenerable."""
    from clipping.aistory import workflow
    from clipping.aistory.steps import regenerate

    settings = tce._settings(**PAID)
    story_id = _keyframes(store, tmp_path, settings=settings)
    plan = _plan(store, story_id, settings, _adapters(FakeVideo()))
    slow, settled = plan["plan"][0]["shot_id"], plan["plan"][1]["shot_id"]
    video = FakeVideo(slow_for={f"shot_{slow[2:]}"}, fail_for={f"shot_{settled[2:]}"})

    summary, log = tas._run(store, story_id, adapters=_adapters(video), settings=settings)

    continue_only = "press Continue (the assets step resumes it; nothing is bought again)"
    held = next(item for item in summary["failed"] if item["what"] == f"clip of shot {slow}")
    assert held["target"] is None and continue_only in held["reason"]
    finish = next(line for line in log if line.startswith("⚠️ Episode 1's assets are not complete"))
    assert f"shot:1:{slow}:video" not in finish and continue_only in finish
    assert f"'shot:1:{settled}:video'" in finish  # a settled clip keeps its regenerate target

    submits, rows = len(video.requests), tas._ledger(store, story_id)
    roomy = tce._settings(**dict(PAID, PER_EPISODE_CAP_USD="1.00"))  # the cap is not what refuses it
    ctx, _log = eps._ctx(store, story_id, step="regenerate", settings=roomy,
                         params={"target": f"shot:1:{slow}:video", "note": "again, faster"})
    with pytest.raises(steps.StepFailed) as caught:
        regenerate.run(ctx, adapters=_adapters(video), time_fn=eps.Clock(0.0), sleep_fn=lambda _s: None)
    assert "still generating" in str(caught.value) and "press Continue" in str(caught.value)
    assert len(video.requests) == submits and video.resumed == [] and tas._ledger(store, story_id) == rows
    story = store.get(story_id)
    with pytest.raises(workflow.WorkflowError) as refused:
        workflow.check_episode_target(store, story, regenerate.parse_target(f"shot:1:{slow}:video"))
    assert refused.value.code == workflow.CONFLICT and "press Continue" in str(refused.value)
    assert workflow.check_episode_target(store, story, regenerate.parse_target(f"shot:1:{settled}:video")) is None


def test_the_first_served_clip_records_the_video_link_and_a_link_gone_stops_the_rest_with_the_offer(store, tmp_path):
    """The first clip served writes ``links.video`` before the next is
    asked. When the link then refuses its key (HTTP 401) it is gone for now:
    the shots left fail with the video offer's reason, calling nothing, and
    the next link of the chain is never asked."""
    settings = tas._settings(**PAID, **BOTH_VIDEO)
    story_id = _keyframes(store, tmp_path, settings=settings)
    plan = _plan(store, story_id, settings, _adapters(FakeVideo()))
    ids = [row["shot_id"] for row in plan["plan"]]
    seen = []
    video = FakeVideo(unauthorized={f"shot_{ids[2][2:]}"},
                      on_generate=lambda _request: seen.append(
                          (tas._assets_doc(store, story_id).get("links") or {}).get("video")))

    summary, log = tas._run(store, story_id, adapters=_adapters(video), settings=settings)

    assert seen[0] is None and seen[1]["link"] == SEEDANCE
    assert tas._assets_doc(store, story_id)["links"]["video"] == seen[1]
    assert video.names() == [f"shot_{shot_id[2:]}" for shot_id in ids[:3]] and set(video.links) == {SEEDANCE}
    gone = summary["video"]["gone"]
    assert (gone["kind"], gone["link"], gone["next_link"]) == ("video", SEEDANCE, KLING)
    assert gone["message"].startswith(f"Episode 1's video link {SEEDANCE} cannot serve now: HttpStatusError: "
                                      "HTTP 401")
    assert "image" not in gone["message"]
    assert [item["shot_id"] for item in summary["video"]["failed"]] == ids[2:]
    for shot_id in ids[3:]:
        clip = _shot(store, story_id, shot_id)["assets"]["clip"]
        assert clip["state"] == "failed" and "no other link was tried" in clip["reason"]
    assert gone["message"] in "\n".join(log)


def test_a_tier_2_plan_that_cannot_animate_stops_before_any_call_unless_animate_is_off(store, tmp_path):
    """Tier 2, animate on, the only video link paid and allow_paid off: the
    step stops before its first call -- no voice, no image, no clip -- with
    the clips' reason and the way out. With ``animate`` off the images and
    voices are made and no clip is asked."""
    settings = tce._settings()
    story_id = tas._episode(store, tmp_path)
    tce._tier(store, story_id)
    edge, image, video = tas.tsm.Edge(), tas.FakeImage(), FakeVideo()

    message = tas._failed(store, story_id, adapters=_adapters(video, image=image, edge=edge), settings=settings)

    assert (edge.calls, image.requests, video.requests) == ([], [], [])
    assert "clips cannot be made" in message and "allow_paid is off" in message
    assert "animate off" in message and "Nothing was generated or spent" in message

    summary, _log = tas._run(store, story_id, adapters=_adapters(video, image=image, edge=edge),
                             params={"animate": False}, settings=settings)

    assert edge.calls and image.requests and video.requests == []
    assert summary["complete"] is True and summary["video"]["animate"] is False
    assert all("clip" not in shot["assets"] for shot in tas._shots(store, story_id))


def test_regenerating_a_shots_clip_with_a_note_keeps_a_fresh_seed_pending_then_makes_one_clip(
        store, tmp_path, monkeypatch):
    """``shot:1:sh05:video`` is a regenerate target now (``:frames`` stays a
    later phase's). Its estimate is one clip's seconds x the price on the
    episode's video link. With a note it asks one clip with a fresh seed,
    kept as the clip's ``pending`` before the call (DEC-154), and records it
    current with the note."""
    from clipping.aistory import video_plan, workflow
    from clipping.aistory.steps import entities, regenerate

    settings = tce._settings(**PAID)
    story_id = _keyframes(store, tmp_path, settings=settings)
    assert workflow.check_regenerate_target("shot:1:sh05:video") is None
    with pytest.raises(workflow.WorkflowError) as caught:
        workflow.check_regenerate_target("shot:1:sh05:frames")
    assert caught.value.code == "later_phase"
    shot = _shot(store, story_id, "sh05")
    clip_s = video_plan.requested_seconds(SEEDANCE, shot["duration_s"])
    estimate = workflow.regenerate_clip_estimate(store, store.get(story_id), regenerate.parse_target(
        "shot:1:sh05:video"), env=settings, adapters=_adapters(FakeVideo()))
    assert (estimate["ready"], estimate["link"], estimate["units"]) == (True, SEEDANCE, {"clips": 1,
                                                                                         "seconds": clip_s})
    assert estimate["est_usd"] == pytest.approx(clip_s * SEEDANCE_PRICE)

    monkeypatch.setattr(entities, "fresh_seed", lambda: 4242)
    seen = []
    video = FakeVideo(on_generate=lambda request: seen.append(
        _shot(store, story_id, "sh05")["assets"]["clip"]["pending"]))
    ctx, _log = eps._ctx(store, story_id, step="regenerate", settings=settings,
                         params={"target": "shot:1:sh05:video", "note": "a slower push-in"})

    result = regenerate.run(ctx, adapters=_adapters(video), time_fn=eps.Clock(0.0), sleep_fn=lambda _s: None)

    assert len(video.requests) == 1 and video.requests[0].seed == 4242 and video.requests[0].duration_s == clip_s
    assert "a slower push-in" in video.requests[0].prompt
    assert seen[0]["seed"] == 4242 and seen[0]["note"] == "a slower push-in"
    clip = _shot(store, story_id, "sh05")["assets"]["clip"]
    assert (clip["state"], clip["note"], clip.get("pending"), clip["clip_s"]) == ("current", "a slower push-in",
                                                                                  None, clip_s)
    assert _shot(store, story_id, "sh05")["assets"]["video"] == "assets/clips/shot_05.mp4"
    assert result["target"] == "shot:1:sh05:video" and result["seed"] == 4242
    assert [(row["unit"], row["qty"]) for row in _video_rows(store, story_id)] == [("second", clip_s)]


# ================================================================ guards

def test_guard_a_tier_1_run_calls_no_video_link_and_writes_the_parent_commits_documents(store, tmp_path):
    """RC-V1: a tier-1 story -- even with a keyed paid video chain and paid
    on -- asks no video link, and its storyboard, assets.json, ledgers and
    summary are the parent commit's (timestamps aside)."""
    story_id = tas._episode(store, tmp_path)
    video = FakeVideo()

    summary, _log = tas._run(store, story_id, adapters=_adapters(video), settings=tce._settings(ALLOW_PAID="1"))

    assert video.requests == [] and "video" not in summary
    assert _masked_sha(tas._episode_file(store, story_id, "storyboard.json")) == TIER1_BOARD_SHA
    assert _masked_sha(tas._episode_file(store, story_id, "assets.json")) == TIER1_ASSETS_SHA
    assert _masked_sha(tas._episode_file(store, story_id, "cost_ledger.json")) == TIER1_EPISODE_LEDGER_SHA
    assert _masked_sha(Path(store.story_dir(story_id)) / "cost_ledger.json") == TIER1_STORY_LEDGER_SHA
    assert hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest() == TIER1_SUMMARY_SHA
    assert not tas._episode_file(store, story_id, "assets", "clips").exists()
