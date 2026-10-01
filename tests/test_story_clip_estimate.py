"""Clip documents and the video estimate (AI Story phase 6, stage 7; spec
2.8, 8.1, 8.5; DEC-202, DEC-203, DEC-208, DEC-211).

A story set to ``tier >= 2`` gains a ``video`` part in the assets estimate
(``assets.asset_units``): the one_dollar planner's clips on the episode's
video link, seconds x price, inside the total and the caps; the fast track's
paid check counts it like a paid image. The user's per-shot overrides
(``keep_still``, ``animate``, ``keep_native_audio``) live in ``assets.json``
(``workflow.patch_assets``): the storyboard never moves, and the assets
approval goes stale only when what would be rendered moves. Nothing here
generates a clip. A tier-1 story's estimate and fingerprint are the ones the
parent commit computed, byte for byte (RC-V1, RC-M3).

The story, the fakes and the hermetic fixture are the assets step's own
(``tests/test_story_assets_step.py``). Offline and hermetic; stdlib + pytest
(DEC-012); the new modules are imported inside the tests, so on the parent
commit each test fails on its own.
"""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

import test_story_assets_step as tas
import test_story_episode_steps as eps
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from clipping.aistory import schemas

NOW = tas.NOW
LATER = "2026-10-01T09:00:00+00:00"
SEEDANCE = "fal/seedance-1-pro-fast"
SEEDANCE_PRICE = 0.022  # pricing.PRICES' row, per second at 720p (A-100)
VIDEO_API = {"VIDEO_CHAIN": SEEDANCE, **tas.FAL}

# Computed on the parent commit (eca2673) with this file's fixture, before
# any stage-7 line existed: a tier-1 story's estimate (json.dumps in key
# order, so a moved key counts) and its assets fingerprints -- after a run
# (``links`` recorded, phase 6 stage 6), without ``links`` (a phase-4/5
# document), and the pure function's over hand-made inputs.
#
# DEC-223 (AI Story phase 7 stage 2a): the *_UNITS_SHA constants below were
# recomputed after the budget caps moved from 1/3/10 to 2/6/20 -- asset_units
# embeds the caps (assets.py's "caps" field) so its hash moves with them.
# Proof the move is caused only by the caps, nothing else in this guard: with
# DAILY_CAP_USD/PER_STORY_CAP_USD (and PER_EPISODE_CAP_USD on the free-route
# call) forced back to 1.00/3.00/10.00 via `env=dict(paid, DAILY_CAP_USD=...)`
# -- the test's own settings mechanism, no source edited -- asset_units on
# this same fixture reproduces the OLD pinned values byte for byte:
#   paid:       e1d7f3325f16506dfd79d30da9b2d5835bf6d4e486033139f8d181552446ab03
#   free:       91eecd2fd095d5becc4f201630f83ce36b5659aa006af8bb43cbbd1d65c75951
#   after_run:  ef1a91f980aa0457e607e985ee4415f7b4ebae5e984b535fcb6d1ce05008f264
# The fingerprint constants (current_fingerprint, assets_fingerprint) take no
# env/caps and are unchanged -- verified the same way, byte for byte.
TIER1_PAID_UNITS_SHA = "25339e021855433006083e738c5fb2b549186873534993d40007b56ef7024d54"
TIER1_FREE_UNITS_SHA = "275f78232679994ccc721d0a41c78cc6d7e7092bc1d1f9b5cbb2a62ddfeb45af"
TIER1_UNITS_AFTER_RUN_SHA = "2c542ebfd346bc9bbbbb9665b4bbc3c40d20e2a89f125da397bdddf2d036813d"
TIER1_FP_AFTER_RUN = "ace3809bca051e4c02e5ac90cac73eead1a21e73a8f9cf50a0c47a649174dccd"
TIER1_FP_WITHOUT_LINKS = "85401247ad999e25ea0edac11e06b194b85ce7195c27d4311c448622ae357c4b"
PURE_FP = "8f4570c1156f97a1ef87dbfbf3a8f9d950e659583530e3765fdb4963f0b081eb"
PURE_FP_WITH_LINKS = "0ec674dc0ebb63941460d9dd5bf0aefdbfc51277a7a70904e29e3fcb1701a2d9"


@pytest.fixture(autouse=True)
def timings_path(monkeypatch, tmp_path):
    """The measured clip timings (``data/gen_timings.json``) under tmp_path."""
    monkeypatch.setenv("GEN_TIMINGS_PATH", str(tmp_path / "data" / "gen_timings.json"))


class NeverVideo:
    """Registered on every video link: an estimate never calls one."""

    def estimate(self, link, request):
        raise AssertionError(f"{link.provider}/{link.model}: an estimate never asks the adapter")

    def probe(self, link, **_kwargs):
        raise AssertionError(f"{link.provider}/{link.model} must never be probed here")

    def generate(self, link, request, **_kwargs):
        raise AssertionError(f"{link.provider}/{link.model} must never be called")


def _adapters(**kwargs):
    table = tas._adapters(**kwargs)
    for provider in ("fal", "gemini", "local"):
        table[("video", provider)] = NeverVideo()
    return table


def _tier(store, story_id, tier=2, budget_profile="one_dollar"):
    store.update(story_id, lambda doc: doc["generation_profile"].update(tier=tier, budget_profile=budget_profile),
                 now=NOW)


def _settings(**extra):
    return tas._settings(**VIDEO_API, **extra)


def _units(store, story_id, settings, **kwargs):
    from clipping.aistory.steps import assets

    return assets.asset_units(tas._ec(store, story_id), eps._script(store, story_id), tas._board(store, story_id),
                              env=settings, adapters=kwargs.pop("adapters", None) or _adapters(), **kwargs)


def _patch(store, story_id, *items, now=LATER):
    from clipping.aistory import workflow

    return workflow.patch_assets(store, story_id, 1, {"shots": list(items)}, now=now)


def _fingerprint(store, story_id, doc=None):
    from clipping.aistory.steps import assets

    doc = tas._assets_doc(store, story_id) if doc is None else doc
    return assets.current_fingerprint(tas._ec(store, story_id), tas._board(store, story_id),
                                      eps._script(store, story_id), doc)


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode("utf-8")).hexdigest()


# ============================================================ fail-first

def test_a_tier_2_estimate_prices_the_planners_clips_seconds_times_price_inside_the_total(store, tmp_path):
    """Tier 2, the one_dollar profile, seedance keyed and paid on: the
    estimate lists exactly the planner's key shots (hook, cliffhanger, peak,
    ...) that fit the episode's cap, each at its sellable length
    (``requested_seconds``) times $0.022, and the total holds them. A
    keep_still override in assets.json takes its shot out; a pin is animated
    past the cap, and the caps refuse the plan with the clip numbers."""
    from clipping.aistory import video_plan

    story_id = tas._episode(store, tmp_path)
    _tier(store, story_id)
    script, board = eps._script(store, story_id), tas._board(store, story_id)

    units = _units(store, story_id, _settings(ALLOW_PAID="1", PER_EPISODE_CAP_USD="0.30"))

    video = units["video"]
    functions = {scene["scene_id"]: scene["function"] for scene in script["scenes"]}
    seconds = {line["line_id"]: line["timing"]["duration_s"] for scene in script["scenes"]
               for line in scene["lines"]}
    expected = video_plan.plan_animation(
        board["shots"], functions, {shot["shot_id"]: sum(seconds[lid] for lid in shot["lines"])
                                    for shot in board["shots"]},
        link=SEEDANCE, price_per_second=SEEDANCE_PRICE, cap_usd=0.30, committed_usd=0.0,
        mode="key_shots_within_cap", priority=video_plan.ANIMATE_PRIORITY)
    durations = {shot["shot_id"]: shot["duration_s"] for shot in board["shots"]}
    assert (video["tier"], video["route_class"], video["link"], video["ready"]) == (2, "paid", SEEDANCE, True)
    assert [row["shot_id"] for row in video["plan"]] == [entry["shot_id"] for entry in expected.selected]
    assert 0 < len(video["plan"]) < len(board["shots"])
    for row in video["plan"]:
        assert row["clip_s"] == video_plan.requested_seconds(SEEDANCE, durations[row["shot_id"]])
        assert row["est_usd"] == pytest.approx(row["clip_s"] * SEEDANCE_PRICE)
    assert video["seconds"] == sum(row["clip_s"] for row in video["plan"])
    assert video["est_usd"] == pytest.approx(video["seconds"] * SEEDANCE_PRICE) and video["est_usd"] <= 0.30
    assert units["est_usd"] == pytest.approx(video["est_usd"])  # the images and voices are free
    assert units["over_cap"] is None and units["ready"] is True
    assert video["eta_s"] is None and video["eta_note"] == "no measured history"
    assert {row["shot_id"] for row in video["still"]} == set(durations) - {row["shot_id"] for row in video["plan"]}

    first = video["plan"][0]["shot_id"]
    _patch(store, story_id, {"shot_id": first, "keep_still": True})
    kept = _units(store, story_id, _settings(ALLOW_PAID="1", PER_EPISODE_CAP_USD="0.30"))["video"]
    assert first not in [row["shot_id"] for row in kept["plan"]]
    assert {"shot_id": first, "reason": "keep_still"} in kept["still"]

    # Under $0.04 no clip fits (seedance's shortest is 2 s, $0.044); a pinned shot is animated anyway.
    tight = _settings(ALLOW_PAID="1", PER_EPISODE_CAP_USD="0.04")
    assert _units(store, story_id, tight)["video"]["plan"] == []
    _patch(store, story_id, {"shot_id": "sh05", "animate": True})
    pinned = _units(store, story_id, tight)
    assert pinned["video"]["plan"] == [{"shot_id": "sh05", "clip_s": 3, "est_usd": pytest.approx(0.066),
                                        "why": "pinned"}]
    assert pinned["ready"] is False
    assert "1 clip (3 s on fal/seedance-1-pro-fast, est $0.066)" in pinned["over_cap"]
    assert "of its $0.04 cap" in pinned["over_cap"]


def test_with_allow_paid_off_the_clips_are_not_ready_and_the_fast_track_stops_at_its_paid_check(store, tmp_path):
    """Only hosted video links and allow_paid off: the plan is shown on the
    link it would take, with its price and the reason -- never ready; the
    fast track's paid check stops before any call, naming the clips, as it
    does for a paid image. With no link keyed there is no plan at all."""
    from clipping.aistory.steps import fast_track

    story_id = tas._episode(store, tmp_path)
    _tier(store, story_id)

    units = _units(store, story_id, _settings())

    video = units["video"]
    assert video["ready"] is False and video["link"] == SEEDANCE and "allow_paid is off" in video["message"]
    assert video["plan"] and video["est_usd"] == pytest.approx(video["seconds"] * SEEDANCE_PRICE)
    assert units["ready"] is False and units["est_usd"] == 0.0
    verdict = fast_track.paid_verdict(units, ep=1)
    assert verdict["verdict"] == fast_track.STOPS_BEFORE_PAID and "allow_paid is off" in verdict["stop"]
    assert [part for part in verdict["parts"] if SEEDANCE in part] == [
        f"{len(video['plan'])} clips ({video['seconds']} s) on {SEEDANCE} (est ${video['est_usd']:.3f})"]
    assert verdict["est_usd"] == pytest.approx(video["est_usd"])

    bare = _units(store, story_id, tas._settings(VIDEO_CHAIN=SEEDANCE))["video"]
    assert (bare["ready"], bare["link"], bare["plan"]) == (False, None, [])
    assert "no local ComfyUI is ready" in bare["message"]


def test_a_keep_still_or_animate_swap_never_moves_the_storyboard_and_stales_the_approval_only_when_it_counts(
        store, tmp_path):
    """The overrides go to assets.json: the storyboard's bytes, revision and
    approval never move. The approved fingerprint moves exactly when a
    shot's effective flags move (an override equal to the storyboard's own
    value changes nothing); clearing them (null) brings it back. A bad
    value, or a pin on a shot kept still, is refused whole."""
    from clipping.aistory import workflow

    story_id = tas._episode(store, tmp_path)
    tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    workflow.approve_assets(store, story_id, 1, now=NOW)
    approved = tas._assets_doc(store, story_id)["approved"]["fingerprint"]
    board_file = tas._episode_file(store, story_id, "storyboard.json")
    board_bytes = board_file.read_bytes()
    assert _fingerprint(store, story_id) == approved

    _patch(store, story_id, {"shot_id": "sh02", "keep_still": False})
    assert tas._assets_doc(store, story_id)["shots"] == {"sh02": {"keep_still": False}}
    assert _fingerprint(store, story_id) == approved

    _patch(store, story_id, {"shot_id": "sh02", "keep_still": True}, {"shot_id": "sh05", "animate": True})
    doc = tas._assets_doc(store, story_id)
    assert doc["shots"] == {"sh02": {"keep_still": True}, "sh05": {"animate": True}}
    assert doc["approved"]["fingerprint"] == approved and _fingerprint(store, story_id) != approved
    assert board_file.read_bytes() == board_bytes

    _patch(store, story_id, {"shot_id": "sh02", "keep_still": None}, {"shot_id": "sh05", "animate": None})
    assert "shots" not in tas._assets_doc(store, story_id) and _fingerprint(store, story_id) == approved

    for item, needle in (({"shot_id": "sh02", "keep_still": "yes"},
                          "shots[0].keep_still: expected true, false or null"),
                         ({"shot_id": "sh02", "keep_still": True, "animate": True},
                          "shots[0].animate: shot sh02 is kept still")):
        before = tas._assets_doc(store, story_id)
        with pytest.raises(workflow.WorkflowError) as caught:
            _patch(store, story_id, item)
        assert caught.value.code == workflow.INVALID
        assert any(needle in error for error in caught.value.detail["errors"]), caught.value.detail
        assert tas._assets_doc(store, story_id) == before
    assert board_file.read_bytes() == board_bytes


def test_the_eta_is_the_median_seconds_per_clip_second_of_the_last_twenty(tmp_path):
    from clipping.providers import gen_timings

    timings = gen_timings.GenTimings(str(tmp_path / "gen_timings.json"), time_fn=lambda: 1_790_000_000.0)
    key = gen_timings.timing_key("local/comfyui", "i2v_wan22_5b", "mid")
    assert key == "local/comfyui|i2v_wan22_5b|mid"
    assert timings.eta_s(key, 20) is None

    for wall_s, clip_s in ((100.0, 5), (30.0, 2), (240.0, 4)):  # 20, 15 and 60 s per clip second
        timings.record(key, wall_s, clip_s)

    assert timings.eta_s(key, 20) == pytest.approx(400.0)  # the median, 20 s, x 20 s
    assert timings.eta_s(gen_timings.timing_key(SEEDANCE), 20) is None
    for _ in range(25):
        timings.record(key, 10.0, 5)
    data = json.loads((tmp_path / "gen_timings.json").read_text(encoding="utf-8"))
    assert data["$schema"] == "gen_timings_v1" and len(data["keys"][key]) == 20
    assert data["keys"][key][-1] == {"wall_s": 10.0, "clip_s": 5, "at": "2026-09-21T14:13:20+00:00"}


def test_a_clip_record_is_optional_and_a_video_path_is_the_shots_own_current_clip():
    """The storyboard shot's ``assets.clip`` (next to its image record) and
    ``assets.video`` -- the clip's file, ``assets/clips/shot_NN.mp4`` --
    validate; a video naming another shot's clip, or with no current clip,
    does not. The store keeps clips under the ``clips`` kind."""
    import test_story_schemas_phase4 as tsch

    from clipping.aistory import store as store_mod

    board = tsch._board(tsch.PHASE4_ASSETS)
    assert schemas.storyboard_errors(board) == []
    clip = {"state": "current", "link": SEEDANCE, "route": "paid", "clip_s": 3, "est_usd": 0.066,
            "prompt_hash": "a" * 64, "image_sha256": "b" * 64, "cache_key": "c" * 64,
            "generated_at": NOW}
    board["shots"][0]["assets"].update(clip=clip, video="assets/clips/shot_01.mp4")
    assert schemas.storyboard_errors(board) == []
    other = copy.deepcopy(board)
    other["shots"][0]["assets"]["video"] = "assets/clips/shot_02.mp4"
    assert any("assets.video" in error for error in schemas.storyboard_errors(other))
    failed = copy.deepcopy(board)
    failed["shots"][0]["assets"]["clip"] = dict(clip, state="failed", reason="HTTP 500 from fal")
    assert any("current clip" in error for error in schemas.storyboard_errors(failed))
    assert store_mod.EPISODE_ASSET_NAME_PATTERNS["clips"].fullmatch("shot_01.mp4")
    assert not store_mod.EPISODE_ASSET_NAME_PATTERNS["clips"].fullmatch("shot_01.png")


def test_quality_all_shots_over_the_cap_is_refused_whole_before_any_call(store, tmp_path):
    """Phase 7 stage 4 (DEC-227): the quality profile animates every shot. Over
    the episode's cap the step refuses the whole plan before any call (the
    video adapter here raises on any use), naming the planned seconds, the
    estimate, the cap and what is spent so far -- never a partial pick. Under
    the cap every shot not kept still is planned."""
    from clipping.aistory.steps import assets

    story_id = tas._episode(store, tmp_path)
    _tier(store, story_id, budget_profile="quality")
    board = tas._board(store, story_id)

    over = _units(store, story_id, _settings(ALLOW_PAID="1", PER_EPISODE_CAP_USD="0.30"))
    video = over["video"]
    assert video["mode"] == "all_shots" and sorted(row["shot_id"] for row in video["plan"]) == sorted(
        shot["shot_id"] for shot in board["shots"])
    assert video["est_usd"] > 0.30 and over["ready"] is False
    refusal = assets.plan_refusal(tas._ec(store, story_id), over)
    assert f"{video['seconds']} s on {SEEDANCE}, est ${video['est_usd']:.3f}" in refusal
    assert "$0.00 already spent or committed" in refusal and "of the $0.30 episode cap" in refusal
    assert "whole plan is refused" in refusal and "nothing was generated or spent" in refusal

    _patch(store, story_id, {"shot_id": "sh02", "keep_still": True})
    under = _units(store, story_id, _settings(ALLOW_PAID="1", PER_EPISODE_CAP_USD="20"))
    assert under["ready"] is True and under["video"]["over_cap"] is None
    assert sorted(row["shot_id"] for row in under["video"]["plan"]) == sorted(
        shot["shot_id"] for shot in board["shots"] if shot["shot_id"] != "sh02")
    assert assets.plan_refusal(tas._ec(store, story_id), under) is None


def test_a_1080p_story_asks_seedance_for_1080p_priced_from_its_row(store, tmp_path):
    """The per-story 1080p switch (the human's answer 3): a v2 story with
    ``generation_profile.video_resolution`` 1080p sends seedance
    ``resolution: 1080p`` (``GenRequest.extra``), and its estimate -- the
    plan's and the adapter's -- is priced from the 1080p row; a 720p story
    sends what it always sent and keeps the 720p price."""
    from clipping.aistory.steps import assets, clips
    from clipping.providers import pricing, video as video_providers
    from clipping.providers.registry import Link

    story_id = tas._episode(store, tmp_path)
    _tier(store, story_id, budget_profile="quality")
    store.update(story_id, lambda doc: doc["generation_profile"].update(pipeline="v2", video_resolution="1080p"),
                 now=NOW)
    ec = tas._ec(store, story_id)
    script, board = eps._script(store, story_id), tas._board(store, story_id)
    shot = dict(board["shots"][0])
    shot["assets"] = dict(shot["assets"], image="assets/shots/shot_01.png")
    keyframe = tas.Path(store.episode_asset_path(story_id, 1, "shots", "shot_01.png", create=True))
    keyframe.parent.mkdir(parents=True, exist_ok=True)
    keyframe.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    link = Link("fal", "seedance-1-pro-fast")

    _parts, request = assets.clip_request(ec, shot, script, link=SEEDANCE, template=None, clip_s=5, seed=7,
                                          note=None, flags=clips.shot_flags(shot, None), tier=2,
                                          out_dir=str(tmp_path))
    adapter = video_providers.FalVideoAdapter()
    assert request.extra["resolution"] == "1080p"
    assert adapter._inputs(link, request, 7)["resolution"] == "1080p"
    hd_price = pricing.PRICES["fal/seedance-1-pro-fast@1080p"].usd
    assert hd_price == 0.0486 and adapter.estimate(link, request).est_usd == round(5 * hd_price, 4)
    video = _units(store, story_id, _settings(ALLOW_PAID="1", PER_EPISODE_CAP_USD="20"))["video"]
    assert video["price_per_second"] == hd_price
    assert video["est_usd"] == pytest.approx(video["seconds"] * hd_price)

    store.update(story_id, lambda doc: doc["generation_profile"].pop("video_resolution"), now=NOW)
    ec = tas._ec(store, story_id)
    _parts, request = assets.clip_request(ec, shot, script, link=SEEDANCE, template=None, clip_s=5, seed=7,
                                          note=None, flags=clips.shot_flags(shot, None), tier=2,
                                          out_dir=str(tmp_path))
    assert "resolution" not in request.extra and adapter._inputs(link, request, 7)["resolution"] == "720p"
    assert adapter.estimate(link, request).est_usd == round(5 * SEEDANCE_PRICE, 4)


# ================================================================ guards

def test_guard_a_tier_1_estimate_and_fingerprint_are_the_parent_commits_byte_for_byte(store, tmp_path):
    """RC-V1 / RC-M3: a tier-1 story -- no override, no clip -- gets no
    ``video`` part, the very payload and fingerprints the parent commit
    computed, and its stored documents validate unchanged."""
    from clipping.aistory.steps import assets

    story_id = tas._episode(store, tmp_path)
    ec = tas._ec(store, story_id)
    script, board = eps._script(store, story_id), tas._board(store, story_id)
    paid = tas._settings(IMAGE_CHAIN="fal/flux-schnell,pollinations/flux", **tas.FAL, ALLOW_PAID="1",
                         PER_EPISODE_CAP_USD="2.00")
    fal = tas.NeverImage(price=tas.FAL_PRICE)

    units = assets.asset_units(ec, script, board, env=paid, adapters=tas._adapters(fal=fal))
    free = assets.asset_units(ec, script, board, env=tas._settings(), adapters=tas._adapters())

    assert "video" not in units and "video" not in free
    assert _sha(units) == TIER1_PAID_UNITS_SHA
    assert _sha(free) == TIER1_FREE_UNITS_SHA

    tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    ec = tas._ec(store, story_id)
    script, board, doc = eps._script(store, story_id), tas._board(store, story_id), tas._assets_doc(store, story_id)
    assert schemas.storyboard_errors(board) == [] and schemas.episode_assets_errors(doc) == []
    assert _sha(assets.asset_units(ec, script, board, env=paid, adapters=tas._adapters(fal=fal))) == \
        TIER1_UNITS_AFTER_RUN_SHA
    assert assets.current_fingerprint(ec, board, script, doc) == TIER1_FP_AFTER_RUN
    stored = copy.deepcopy(doc)
    stored.pop("links")
    assert schemas.episode_assets_errors(stored) == []
    assert assets.current_fingerprint(ec, board, script, stored) == TIER1_FP_WITHOUT_LINKS

    pure_board = {"shots": [{"shot_id": "sh01", "keep_still": False,
                             "assets": {"prompt_hash": "a" * 64, "locked": False}}]}
    pure_script = {"scenes": [{"lines": [{"line_id": "l01", "text": "Bonjour.", "timing": {"voice": "edge/x"}}]}]}
    shas = {"image_shas": {"sh01": "1" * 64}, "audio_shas": {"l01": "2" * 64}}
    assert assets.assets_fingerprint(pure_board, pure_script, {"sfx": [], "bgm": None}, **shas) == PURE_FP
    linked = {"sfx": [], "bgm": None, "links": {"image": {"link": "pollinations/flux", "since": "x"}}}
    assert assets.assets_fingerprint(pure_board, pure_script, linked, **shas) == PURE_FP_WITH_LINKS
