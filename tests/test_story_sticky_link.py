"""One image link per episode (A-087; AI Story phase 6, stage 6).

The live finding: one episode's shots fell through its chain per shot --
Cloudflare drew the heroes as flat mascots, Pollinations as humans. Now the
first link that serves one of an episode's images is its image link
(``assets.json``'s ``links.image``): a later shot waits on it through the
paced rounds (DEC-168) instead of falling through, a link gone for the day
stops before any call with the offer to switch, and only the user switches.

The story, the fakes and the hermetic fixture are the assets step's own
(``tests/test_story_assets_step.py``), the fake clock and the Pollinations
fake the pacing's (``tests/test_story_assets_pacing.py``). Offline and
hermetic: no key, chain, cap or limit of the machine reaches a test, no
request leaves the process. Stdlib + pytest (DEC-012); the modules are
imported inside the tests, so on the parent commit each test fails on its
own.
"""

from __future__ import annotations

import hashlib
import json

import pytest

import test_story_assets_pacing as tsp
import test_story_assets_step as tas
import test_story_episode_steps as eps
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from clipping.aistory import timing
from clipping.providers import images, limits, transport

NOW = tas.NOW
LATER = "2026-10-01T09:00:00+00:00"
CLOUDFLARE_KEYS = {"CLOUDFLARE_API_TOKEN": "test-cf-token", "CLOUDFLARE_ACCOUNT_ID": "test-cf-account"}
CF, POLL = "cloudflare/flux-1-schnell", "pollinations/flux"
BOTH = {"IMAGE_CHAIN": f"{CF},{POLL}", **CLOUDFLARE_KEYS}


@pytest.fixture(autouse=True)
def unpaced_cloudflare(monkeypatch):
    """Cloudflare's per-minute pacing lifted (its daily allowance still counts)."""
    monkeypatch.setenv("LIMIT_CLOUDFLARE_RPM", "0")


def _m():
    from clipping.aistory import workflow
    from clipping.aistory.steps import assets
    from clipping.aistory.steps import render as render_step

    return assets, workflow, render_step


def _adapters(*, cloudflare=None, pollinations=None):
    table = tas._adapters(image=pollinations)
    if cloudflare is not None:
        table[("image", "cloudflare")] = cloudflare
    return table


def _doc_path(store, story_id, name):
    return tas._episode_file(store, story_id, name)


def _drop_links(store, story_id):
    """``assets.json`` as a document written before stage 6: no ``links``."""
    doc = tas._assets_doc(store, story_id)
    doc.pop("links", None)
    store.write_episode_doc(story_id, 1, "assets.json", doc, now=NOW)


def _drop_images(store, story_id, *shot_ids):
    for shot in tas._shots(store, story_id):
        if shot["shot_id"] in shot_ids:
            (_doc_path(store, story_id, "assets") / "shots" / f"shot_{shot['shot_id'][2:]}.png").unlink()


def _made_on(store, story_id):
    return {shot["shot_id"]: f"{shot['assets']['provider']}/{shot['assets']['model']}"
            for shot in tas._shots(store, story_id) if shot["assets"].get("image")}


def _all_on(store, tmp_path, link, **settings):
    """A story whose episode 1 has every image made on *link*."""
    story_id = tas._episode(store, tmp_path)
    fake = tas.FakeImage()
    adapters = _adapters(cloudflare=fake) if link == CF else _adapters(pollinations=fake)
    summary, _log = tas._run(store, story_id, adapters=adapters,
                             settings=tas._settings(IMAGE_CHAIN=link, **CLOUDFLARE_KEYS, **settings))
    assert summary["complete"] is True and set(_made_on(store, story_id).values()) == {link}
    return story_id


# ============================================================ fail-first

def test_a_rate_limited_sticky_link_is_waited_on_and_the_next_link_is_never_called(store, tmp_path):
    """The episode's first image comes from Pollinations: that is its link.
    Its free tier then holds every next shot back (HTTP 402, one image a
    minute); each shot waits for it in the paced rounds and Cloudflare, the
    next link of the chain, is never asked -- today every one of them fell
    through to Cloudflare."""
    assets, _wf, _render = _m()
    story_id = tas._episode(store, tmp_path)
    tsp._leave_six(store, story_id)
    clock = tsp.Clock()
    poll = tsp.Pollinations(clock)
    cloudflare = tas.FakeImage()

    summary, log = tsp._run(store, story_id, adapters=_adapters(cloudflare=cloudflare,
                                                                 pollinations=images.POLLINATIONS),
                            clock=clock, transport=poll, settings=tas._settings(IMAGE_CHAIN=f"{POLL},{CF}",
                                                                                 **CLOUDFLARE_KEYS))

    assert cloudflare.requests == []
    assert poll.answers == [0.0, 60.0, 120.0, 180.0, 240.0, 300.0]
    assert clock.sleeps == [assets.RATE_LIMIT_PAUSE_S] * (tsp.MADE - 1)
    made = _made_on(store, story_id)
    assert [made[f"sh{n:02d}"] for n in range(1, tsp.MADE + 1)] == [POLL] * tsp.MADE
    assert summary["complete"] is True and summary["failed"] == []
    # The first served image made the record, kept in assets.json.
    record = tas._assets_doc(store, story_id)["links"]["image"]
    assert record["link"] == POLL and record["since"] and "switched_from" not in record
    assert f"🔗 Episode 1's image link is now {POLL}: every other shot of it is made on that link alone." in log
    assert summary["image_link"] == {"link": POLL, "mixed": [], "gone": None}


@pytest.mark.parametrize("gone, record", [("allowance", True), ("no_key", False)])
def test_a_gone_sticky_link_stops_before_any_call_with_the_offer(store, tmp_path, monkeypatch, gone, record):
    """Every image of the episode is Cloudflare's -- recorded, or (a document
    from before) derived from the images -- and two shots are to make. With
    Cloudflare's allowance spent for the day, or its key gone, the step
    stops before any call and offers the next runnable link: the link, why,
    the shots a switch remakes, and the price -- today the chain fell
    through to Pollinations."""
    assets, workflow, _render = _m()
    story_id = _all_on(store, tmp_path, CF)
    if not record:
        _drop_links(store, story_id)
    _drop_images(store, story_id, "sh01", "sh02")
    shots = [shot["shot_id"] for shot in tas._shots(store, story_id)]
    redo = [shot_id for shot_id in shots if shot_id not in ("sh01", "sh02")]
    settings = tas._settings(**BOTH)
    if gone == "allowance":
        monkeypatch.setenv("LIMIT_CLOUDFLARE_RPD", "0")
        why = f"daily allowance spent ({limits.default_usage().calls('cloudflare')}/0 today, resets at 00:00 UTC)"
    else:
        settings = tas._settings(IMAGE_CHAIN=BOTH["IMAGE_CHAIN"])
        why = "no API key (CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are not set)"
    cloudflare, poll = tas.FakeImage(), tas.FakeImage()
    ledger_rows = len(tas._ledger(store, story_id))

    message = tas._failed(store, story_id, adapters=_adapters(cloudflare=cloudflare, pollinations=poll),
                          settings=settings)

    assert cloudflare.requests == [] and poll.requests == [] and len(tas._ledger(store, story_id)) == ledger_rows
    assert message.startswith(f"Episode 1's image link {CF} cannot serve now: {why}.")
    assert "nothing was generated or spent" in message
    assert f"switch the episode's image link to {POLL}" in message
    assert f"shots {', '.join(redo[:-1])} and {redo[-1]}, made on {CF}, are made again" in message
    assert f"with the 2 still to make -- {len(shots)} images on {POLL} (free), $0.00." in message
    # The structured offer, before any job (the estimate and the web gate read it).
    ec = tas._ec(store, story_id)
    units = assets.asset_units(ec, eps._script(store, story_id), tas._board(store, story_id), env=settings,
                               adapters=_adapters(cloudflare=cloudflare, pollinations=poll))
    offer = units["images"]["sticky"]["gone"]
    assert {key: offer[key] for key in ("link", "why", "next_link", "next_route_class", "redo", "todo", "qty",
                                        "est_usd", "switch")} == {
        "link": CF, "why": why, "next_link": POLL, "next_route_class": "free", "redo": redo,
        "todo": ["sh01", "sh02"], "qty": len(shots), "est_usd": 0.0, "switch": {"links": {"image": POLL}}}
    assert assets.plan_refusal(ec, units) == offer["message"] == message
    with pytest.raises(workflow.WorkflowError) as caught:
        workflow.assets_gate(ec, env=settings)
    assert caught.value.detail == message


# ================================================================ the rest

def test_a_link_gone_mid_run_fails_the_shots_left_without_the_next_link(store, tmp_path):
    """The link serves the first image, then refuses its key (HTTP 401):
    every shot left fails with the same reason, nothing falls through."""
    story_id = tas._episode(store, tmp_path)
    tsp._leave_six(store, story_id)

    class KeyRevoked(tas.FakeImage):
        def generate(self, link, request, **kwargs):
            if self.requests:
                self.requests.append(request)
                raise transport.HttpStatusError(401, "https://api.cloudflare.com/client/v4/accounts/x/ai/run/m")
            return super().generate(link, request, **kwargs)

    cloudflare, poll = KeyRevoked(), tas.FakeImage()

    summary, log = tas._run(store, story_id, adapters=_adapters(cloudflare=cloudflare, pollinations=poll),
                            settings=tas._settings(**BOTH))

    assert poll.requests == [] and len(cloudflare.requests) == 2
    reasons = {item["reason"] for item in summary["failed"]}
    assert [item["target"] for item in summary["failed"]] == [f"shot:1:sh{n:02d}" for n in range(2, tsp.MADE + 1)]
    assert reasons == {f"its image link {CF} cannot serve now (HttpStatusError: HTTP 401 from "
                       "https://api.cloudflare.com/client/v4/accounts/x/ai/run/m); no other link was tried"}
    gone = summary["image_link"]["gone"]
    assert gone["link"] == CF and gone["next_link"] == POLL and gone["todo"] == [f"sh{n:02d}" for n in range(2, 7)]
    assert sum(1 for line in log if line.startswith("✋ ")) == 1


def test_the_first_served_image_writes_the_record_at_once(store, tmp_path):
    """An episode with an assets.json, no record and no image kept: the
    first image served writes ``links.image`` before the second is asked."""
    story_id = _all_on(store, tmp_path, POLL)
    _drop_links(store, story_id)
    _drop_images(store, story_id, *[shot["shot_id"] for shot in tas._shots(store, story_id)])
    seen = []

    def read_record(_request, _kwargs):
        seen.append((tas._assets_doc(store, story_id).get("links") or {}).get("image"))

    cloudflare, poll = tas.FakeImage(on_generate=read_record), tas.FakeImage()

    summary, _log = tas._run(store, story_id, adapters=_adapters(cloudflare=cloudflare, pollinations=poll),
                             settings=tas._settings(**BOTH))

    assert seen[0] is None and seen[1]["link"] == CF and all(entry == seen[1] for entry in seen[1:])
    assert poll.requests == [] and set(_made_on(store, story_id).values()) == {CF}
    assert tas._assets_doc(store, story_id)["links"] == {"image": seen[1]} and summary["complete"] is True


def test_a_legacy_episode_that_mixes_links_prints_one_note_and_walks_the_chain_as_before(store, tmp_path,
                                                                                         monkeypatch):
    story_id = _all_on(store, tmp_path, POLL)
    _drop_links(store, story_id)
    board = tas._board(store, story_id)
    for shot in board["shots"][::2]:
        shot["assets"].update(provider="cloudflare", model="flux-1-schnell")
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    _drop_images(store, story_id, "sh01", "sh02")
    monkeypatch.setenv("LIMIT_CLOUDFLARE_RPD", "0")

    summary, log = tas._run(store, story_id, adapters=_adapters(cloudflare=tas.FakeImage(),
                                                                pollinations=tas.FakeImage()),
                            settings=tas._settings(**BOTH))

    # As before: Cloudflare's spent day is skipped, Pollinations makes them
    # (its kept answers: the same requests it answered before).
    made = _made_on(store, story_id)
    assert (made["sh01"], made["sh02"]) == (POLL, POLL) and summary["complete"] is True
    notes = [line for line in log if line.startswith("ℹ️ Episode 1 mixes image links")]
    assert notes == [f"ℹ️ Episode 1 mixes image links ({CF} made 11 shots and {POLL} made 11 shots), so it keeps "
                     "none: each shot walks IMAGE_CHAIN as before. Choose one with the assets edit "
                     "{\"links\": {\"image\": \"<link>\"}}: the shots made on the others are then made again on "
                     "it."]
    assert "links" not in tas._assets_doc(store, story_id)


def test_switching_the_link_stales_exactly_the_shots_the_old_link_made_and_keeps_the_storyboard_approval(
        store, tmp_path):
    assets, workflow, _render = _m()
    story_id = _all_on(store, tmp_path, CF)
    workflow.patch_assets(store, story_id, 1, {"shots": [{"shot_id": "sh03", "locked": True}]}, now=NOW)
    workflow.approve_assets(store, story_id, 1, now=NOW)
    board_bytes = _doc_path(store, story_id, "storyboard.json").read_bytes()
    settings = tas._settings(**BOTH)

    for bad, error in (({"image": "fal/flux-schnell"}, f"links.image: 'fal/flux-schnell' is not a link of "
                                                      f"IMAGE_CHAIN (its links: {CF}, {POLL})"),
                       ({"video": "fal/seedance-1-pro-fast"}, "links: unknown key(s) video (editable: image)")):
        with pytest.raises(workflow.WorkflowError) as caught:
            workflow.patch_assets(store, story_id, 1, {"links": bad}, now=LATER, env=settings)
        assert caught.value.detail["errors"] == [error]
    workflow.patch_assets(store, story_id, 1, {"links": {"image": POLL}}, now=LATER, env=settings)

    assert _doc_path(store, story_id, "storyboard.json").read_bytes() == board_bytes  # its approval never moves
    doc = tas._assets_doc(store, story_id)
    assert doc["links"]["image"] == {"link": POLL, "since": LATER, "switched_from": CF}
    ec, board = tas._ec(store, story_id), tas._board(store, story_id)
    states = {shot["shot_id"]: assets.shot_state(ec, shot) for shot in board["shots"]}
    unlocked = [shot_id for shot_id in states if shot_id != "sh03"]
    assert states == {**{shot_id: "stale" for shot_id in unlocked}, "sh03": "locked_stale"}
    assert workflow.assets_approval_state(ec, board, eps._script(store, story_id), doc) == "stale"
    # The next run makes exactly those again, on the new link alone.
    cloudflare, poll = tas.FakeImage(), tas.FakeImage()
    summary, _log = tas._run(store, story_id, adapters=_adapters(cloudflare=cloudflare, pollinations=poll),
                             settings=settings)
    assert cloudflare.requests == [] and poll.requests
    made = _made_on(store, story_id)
    assert {shot_id for shot_id, link in made.items() if link == POLL} == set(unlocked) and made["sh03"] == CF
    assert summary["complete"] is True and tas._board(store, story_id)["approved_at"] == NOW


def test_guard_an_episode_with_no_record_and_one_link_is_unchanged(store, tmp_path):
    """RC-M3 / RC-V1: a document from before stage 6 -- no ``links``, every
    image on one link -- validates, estimates, fingerprints and passes the
    render's gate as it always did, and nothing writes a record into it
    while no image is served."""
    assets, workflow, render_step = _m()
    story_id = _all_on(store, tmp_path, CF)
    _drop_links(store, story_id)
    workflow.approve_assets(store, story_id, 1, now=NOW)
    names = ("assets.json", "storyboard.json", "script.json")
    before = {name: _doc_path(store, story_id, name).read_bytes() for name in names}
    ec, board, script = tas._ec(store, story_id), tas._board(store, story_id), eps._script(store, story_id)
    doc = tas._assets_doc(store, story_id)

    # The fingerprint is phase 4's formula, byte for byte.
    image_shas = {shot["shot_id"]: hashlib.sha256((_doc_path(store, story_id, "assets") / "shots" /
                                                  shot["assets"]["image"].rpartition("/")[2]).read_bytes()).hexdigest()
                  for shot in board["shots"]}
    audio_shas = {line["line_id"]: assets._sha256_file(assets.line_audio_path(ec, line)) for line in tas._lines(script)}
    payload = {
        "v": 1,
        "shots": [[shot["shot_id"], shot["assets"]["prompt_hash"], image_shas[shot["shot_id"]],
                   bool(shot["assets"].get("locked"))] for shot in board["shots"]],
        "lines": [[line["line_id"], timing.text_hash(line["text"]), line["timing"].get("voice"),
                   audio_shas[line["line_id"]]] for line in tas._lines(script)],
        "sfx": [[cue["scene_id"], cue["at"], cue["cue"], cue["pack"], cue["file"], cue["state"]] for cue in doc["sfx"]],
        "bgm": None if not doc["bgm"] else [doc["bgm"]["mood"], doc["bgm"]["file"], doc["bgm"]["sha256"]],
    }
    phase4 = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode("utf-8")).hexdigest()
    assert assets.current_fingerprint(ec, board, script, doc) == phase4 == doc["approved"]["fingerprint"]
    assert assets.assets_fingerprint(board, script, dict(doc, links={"image": {"link": CF, "since": NOW}}),
                                     image_shas=image_shas, audio_shas=audio_shas) != phase4

    # The estimate, the gates and the render's refusal read it as before.
    units = assets.asset_units(ec, script, board, env=tas._settings(**BOTH))
    assert units["images"] == {"shots": [], "count": 0, "kind": "image", "chain": "IMAGE_CHAIN",
                               "consistency": "prompt_only", "route_class": None, "link": None, "est_usd": 0.0,
                               "links": [], "ready": True, "message": "Every shot has its image."}
    assert assets.plan_refusal(ec, units) is None and assets.outdated_images(ec, board) == []
    assert workflow.assets_approval_state(ec, board, script, doc) == "current"
    render_step.require_renderable(ec)
    assert {name: _doc_path(store, story_id, name).read_bytes() for name in names} == before

    # A run that serves no image writes no record.
    summary, _log = tas._run(store, story_id, adapters=_adapters(cloudflare=tas.NeverImage()),
                             settings=tas._settings(**BOTH))
    assert summary["complete"] is True and "links" not in tas._assets_doc(store, story_id)
    assert "image_link" not in summary

