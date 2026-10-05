"""Stock cutaways (plan 23 stage B8): ``generation_profile.stock_cutaways: "on"``
fills the eligible establishing shots of an episode with a stock clip at the start of
the assets step, and never replaces a keyframe or a clip that is there.

The episode is the assets step's own fixture (``tests/test_story_assets_step.py``: a
French story, planned fast, approved); its second shot is made an establishing wide with
no character in it. The stock source is a fake (a ``candidates`` list the test controls),
the download a fake that writes a few bytes, the frame at 0.5 s a fake ffmpeg that writes
a JPEG's first bytes: nothing leaves the machine. Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_story_assets_step as tas
import test_story_episode_steps as eps
from clipping.aistory import defaults, media_policy, schemas, stock_cutaways
from clipping.stock import base as stock_base
from clipping.stock.base import StockClip, StockError, StockPool
from test_story_assets_step import hermetic, store  # noqa: F401 -- the step's fixtures (hermetic is autouse)

NOW = eps.NOW
PLACE_TAG = f"#{tas.PARLOIR}:day"
SHOT = "sh02"  # an establishing wide of the fixture, its two characters taken out
OTHER = "sh06"  # an establishing wide that keeps its characters: never eligible


# ------------------------------------------------------------------ the fakes

@pytest.fixture(autouse=True)
def no_stock_network(monkeypatch):
    """Nothing of the machine's stock configuration reaches a test and no stock request leaves it:
    the keys the project's ``.env`` may carry are removed, the credential-safe opener refuses."""
    for name in ("PEXELS_API_KEY", "PIXABAY_API_KEY", "BROLL_LOCAL_DIR", "BROLL_SOURCES"):
        monkeypatch.delenv(name, raising=False)

    def refuse():
        raise AssertionError("a real stock request was attempted")

    monkeypatch.setattr(stock_base, "opener", refuse)


def clip_of(clip_id="1", *, duration=8.0, provider="pexels", author="Ann Fotograf"):
    return StockClip(provider=provider, id=clip_id, url=f"https://videos.example/{clip_id}.mp4", width=1080,
                     height=1920, duration_s=duration, tags=("booth",), author=author,
                     author_url="https://www.pexels.com/@ann", page_url=f"https://www.pexels.com/video/{clip_id}/",
                     licence="Pexels License", licence_url="https://www.pexels.com/license/")


class FakeSource:
    """A stock source the test controls: records each query it was asked."""

    name = "fake"
    shuffle = False

    def __init__(self, clips=None):
        self.clips = list(clips if clips is not None else [clip_of()])
        self.asked = []

    def available(self, env):
        return True

    def candidates(self, query, *, aspect, min_duration_s, env):
        self.asked.append({"query": query, "aspect": aspect, "min_duration_s": min_duration_s})
        return list(self.clips)


def fake_download(clip, dest):
    with open(dest, "wb") as handle:
        handle.write(b"stock-video-" + clip.key.encode())
    return dest


class FakeFfmpeg:
    """The frame at 0.5 s: writes a JPEG's first bytes where the argv says."""

    def __init__(self, *, fail=False):
        self.argvs = []
        self.fail = fail

    def __call__(self, argv, **_kwargs):
        self.argvs.append(list(argv))
        if self.fail:
            return SimpleNamespace(returncode=1, stderr="no frame", stdout="")
        Path(argv[-1]).write_bytes(b"\xff\xd8\xff\xe0 stock frame")
        return SimpleNamespace(returncode=0, stderr="", stdout="")


# ---------------------------------------------------------------- the episode

def stock_episode(store, tmp_path, *, switch=True):
    """The assets step's episode, planned and approved, its shot ``sh02`` an establishing wide with only
    the place in its tags; ``generation_profile.stock_cutaways`` on unless *switch* is False."""
    story_id = tas._episode(store, tmp_path)
    if switch:
        turn_on(store, story_id)
    board = tas._board(store, story_id)
    shot = next(item for item in board["shots"] if item["shot_id"] == SHOT)
    assert shot["framing"] == "wide_establishing"
    shot["subject_tags"] = [PLACE_TAG]
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    return story_id


def turn_on(store, story_id):
    store.update(story_id, lambda doc: doc["generation_profile"].update(stock_cutaways="on"), now=NOW)


def context(store, story_id):
    return tas._ec(store, story_id)


def documents(store, story_id):
    return tas.eps._script(store, story_id), tas._board(store, story_id)


def run_fill(store, story_id, *, source=None, ffmpeg=None, download=fake_download, pool=None):
    """``fill`` over the episode as it is on disk, the board written after it as the step does."""
    ec = context(store, story_id)
    script, board = documents(store, story_id)
    log = []
    source = source or FakeSource()
    result = stock_cutaways.fill(ec, script, board, None, env={}, sources=[source], run=ffmpeg or FakeFfmpeg(),
                                 download=download, pool=pool or StockPool(), on_log=log.append, now=NOW)
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    return result, board, log, source


def shot_of(board, shot_id=SHOT):
    return next(item for item in board["shots"] if item["shot_id"] == shot_id)


def clip_file(store, story_id, shot_id=SHOT):
    """The shot's stock clip's path (a path that is not there when the clips folder is not either)."""
    try:
        return Path(store.episode_asset_path(story_id, 1, "clips", f"shot_{shot_id[2:]}.stock.mp4"))
    except KeyError:
        return Path("/nonexistent/clips") / f"shot_{shot_id[2:]}.stock.mp4"


def clip_state(store, story_id, shot_id=SHOT, *, ec=None):
    from clipping.aistory.steps import assets, clips

    ec = ec or context(store, story_id)
    script, board = documents(store, story_id)
    shot = shot_of(board, shot_id)
    image = assets.shot_image_path(ec, shot)
    return clips.clip_state(ec, shot, script, link=None, tier=1, flags=clips.shot_flags(shot, None),
                            image_sha=assets._sha256_file(image) if image is not None else None)


# =============================================================== eligibility

@pytest.mark.parametrize("shot, expected", [
    ({"framing": "wide_establishing", "subject_tags": [PLACE_TAG]}, True),
    ({"framing": "wide_establishing", "subject_tags": []}, True),
    ({"framing": "wide_establishing", "subject_tags": [PLACE_TAG], "speaks": False}, True),
    ({"framing": "wide_establishing", "subject_tags": [PLACE_TAG, "@char_kiwilo"]}, False),
    ({"framing": "wide_establishing", "subject_tags": ["%prop_telephone"]}, False),
    ({"framing": "wide_establishing", "subject_tags": [PLACE_TAG], "speaks": True}, False),
    ({"framing": "medium_single", "subject_tags": [PLACE_TAG]}, False),
    ({"framing": "insert_prop", "subject_tags": []}, False),
    ({"framing": "close_up", "subject_tags": ["@char_kiwilo"]}, False),
])
def test_a_shot_is_eligible_only_as_an_establishing_wide_with_no_character_prop_or_speech(shot, expected):
    assert stock_cutaways.eligible(shot, {"scenes": []}) is expected


def test_a_narrator_line_over_the_shot_does_not_make_it_a_speaking_shot():
    shot = {"framing": "wide_establishing", "subject_tags": [PLACE_TAG], "lines": ["l01"]}
    script = {"scenes": [{"scene_id": "s01", "lines": [{"line_id": "l01", "speaker": "narrator", "text": "x"}]}]}
    assert stock_cutaways.eligible(shot, script) is True


def test_the_fixture_s_other_establishing_wide_keeps_its_characters_so_it_is_not_eligible(store, tmp_path):
    story_id = stock_episode(store, tmp_path)
    script, board = documents(store, story_id)
    assert [shot["shot_id"] for shot in board["shots"] if stock_cutaways.eligible(shot, script)] == [SHOT]
    assert OTHER not in [shot["shot_id"] for shot in board["shots"] if stock_cutaways.eligible(shot, script)]


# =================================================================== the query

def test_the_query_is_the_place_name_a_few_descriptor_keywords_and_day_or_night():
    place = {"name": "Le Parloir des Secrets",
             "descriptor": "A dimly lit tropical wooden confession booth with a carved bamboo chair"}
    day = stock_cutaways.query_for(place, "day")
    assert day == "Le Parloir des Secrets dimly lit tropical wooden day"
    assert stock_cutaways.query_for(place, "night") == "Le Parloir des Secrets dimly lit tropical wooden night"
    assert stock_cutaways.query_for(place, "day") == day  # deterministic
    # the name's own words are not repeated, a stop word never leads, an unknown variant says its words
    assert stock_cutaways.query_for({"name": "Wooden booth", "descriptor": "the wooden booth in a jungle"},
                                    "golden_hour") == "Wooden booth jungle golden hour"
    assert stock_cutaways.query_for({"name": "Pool"}, "") == "Pool"


def test_the_query_hash_follows_the_query_and_the_frame():
    assert stock_cutaways.query_hash("a pool day", "9:16") == hashlib.sha256(b"a pool day\n9:16").hexdigest()
    assert stock_cutaways.query_hash("a pool day", "9:16") != stock_cutaways.query_hash("a pool night", "9:16")
    assert stock_cutaways.query_hash("a pool day", "9:16") != stock_cutaways.query_hash("a pool day", "16:9")


# ==================================================================== the fill

def test_the_fill_downloads_a_clip_cuts_a_frame_and_records_both_for_the_eligible_shot_only(store, tmp_path):
    story_id = stock_episode(store, tmp_path)
    source = FakeSource([clip_of("7")])
    ffmpeg = FakeFfmpeg()
    result, board, log, _ = run_fill(store, story_id, source=source, ffmpeg=ffmpeg)

    assert result["filled"] == [SHOT] and result["missing"] == []
    # one search, of the place's query, in the story's frame, for the shot plus 0.3 s
    place = store.read_entity(story_id, "places", tas.PARLOIR)
    shot = shot_of(board)
    query = stock_cutaways.query_for(place, "day")
    assert source.asked == [{"query": query, "aspect": "9:16", "min_duration_s": pytest.approx(shot["duration_s"] + 0.3)}]
    # the file, and a frame at 0.5 s filled to the story's keyframe size
    assert clip_file(store, story_id).read_bytes() == b"stock-video-pexels:7"
    argv = ffmpeg.argvs[0]
    assert argv[argv.index("-ss") + 1] == "0.5" and "scale=720:1280" in argv[argv.index("-vf") + 1]
    frame = Path(store.episode_asset_path(story_id, 1, "shots", "shot_02.jpg"))
    assert frame.read_bytes().startswith(b"\xff\xd8")
    digest = hashlib.sha256(f"{query}\n9:16".encode()).hexdigest()
    assets = shot["assets"]
    assert (assets["route"], assets["source"], assets["provider"], assets["model"], assets["est_usd"]) == (
        "stock", "stock/pexels", "stock", "pexels", 0.0)
    assert assets["image"] == "assets/shots/shot_02.jpg" and assets["prompt_hash"] == digest
    assert assets["video"] == "assets/clips/shot_02.stock.mp4"
    clip = assets["clip"]
    assert (clip["state"], clip["link"], clip["route"], clip["est_usd"], clip["prompt_hash"]) == (
        "current", "stock/pexels", "stock", 0.0, digest)
    assert clip["image_sha256"] == hashlib.sha256(frame.read_bytes()).hexdigest()
    assert clip["sha256"] == hashlib.sha256(clip_file(store, story_id).read_bytes()).hexdigest()
    assert clip["source"]["credit"].startswith("Video by Ann Fotograf on Pexels") and clip["clip_s"] >= 1
    # every other shot is untouched
    assert all(not item["assets"].get("clip") and item["assets"]["image"] is None
               for item in board["shots"] if item["shot_id"] != SHOT)
    assert any("stock footage from pexels" in line for line in log)


def test_the_board_validates_and_the_frame_and_the_clip_read_as_current(store, tmp_path):
    from clipping.aistory.steps import assets

    story_id = stock_episode(store, tmp_path)
    run_fill(store, story_id)
    ec = context(store, story_id)
    board = store.read_episode_doc(story_id, 1, "storyboard.json")  # read back through the schema
    assert schemas.storyboard_errors(board) == []
    shot = shot_of(board)
    assert assets.shot_state(ec, shot) == "current" and clip_state(store, story_id, ec=ec) == "current"
    assert assets.shots_to_make(ec, board) != [] and SHOT not in [item["shot_id"] for item in
                                                                  assets.shots_to_make(ec, board)]


def test_the_schema_knows_the_stock_route_and_the_stock_file_name():
    import re

    assert "stock" in schemas.IMAGE_ROUTES and "stock" in schemas.CLIP_ROUTES
    pattern = re.compile(schemas.SHOT_CLIP_NAME_PATTERN)
    assert all(pattern.match(name) for name in ("shot_02.mp4", "shot_02.lipsync.mp4", "shot_02.manual.mp4",
                                                "shot_02.stock.mp4"))
    assert not pattern.match("shot_02.other.mp4")


def test_a_shot_with_no_match_is_generated_as_usual_with_a_log_line(store, tmp_path):
    story_id = stock_episode(store, tmp_path)
    result, board, log, source = run_fill(store, story_id, source=FakeSource([]))
    assert result["filled"] == [] and [item["shot_id"] for item in result["missing"]] == [SHOT]
    shot = shot_of(board)
    assert shot["assets"]["image"] is None and not shot["assets"].get("clip") and "route" not in shot["assets"]
    assert not clip_file(store, story_id).exists()
    assert any("no stock clip matched" in line and "generated as usual" in line for line in log)
    # a clip too short for the shot is no match either: the source is asked for a longer one
    result, _board, _log, _source = run_fill(store, story_id, source=FakeSource([clip_of(duration=1.0)]))
    assert result["filled"] == []


def test_a_failed_download_or_frame_leaves_the_shot_to_be_generated_and_no_file_behind(store, tmp_path):
    story_id = stock_episode(store, tmp_path)

    def refuse(_clip, _dest):
        raise StockError("download of pexels:1 failed: boom")

    result, board, log, _ = run_fill(store, story_id, download=refuse)
    assert result["filled"] == [] and shot_of(board)["assets"]["image"] is None
    assert any("download of pexels:1 failed" in line for line in log)
    result, board, log, _ = run_fill(store, story_id, ffmpeg=FakeFfmpeg(fail=True))
    assert result["filled"] == [] and shot_of(board)["assets"]["image"] is None
    assert not clip_file(store, story_id).exists()
    assert not list(Path(store.episode_asset_path(story_id, 1, "shots", "shot_02.jpg")).parent.glob(".stock-*"))
    assert any("could not be cut" in line for line in log)


def test_the_switch_off_fills_nothing(store, tmp_path):
    story_id = stock_episode(store, tmp_path, switch=False)
    source = FakeSource()
    result, board, _log, _ = run_fill(store, story_id, source=source)
    assert result == {"filled": [], "kept": [], "missing": [], "credits": []} and source.asked == []
    assert shot_of(board)["assets"]["image"] is None


# ============================================================ never replaces

def _generated_clip(store, story_id, shot_id=SHOT, *, route="paid", state="current"):
    """A shot with a generated clip of its own on disk: its record, its file."""
    board = tas._board(store, story_id)
    shot = shot_of(board, shot_id)
    path = Path(store.episode_asset_path(story_id, 1, "clips", f"shot_{shot_id[2:]}.mp4", create=True))
    path.write_bytes(b"bought clip")
    shot["assets"]["clip"] = {"state": state, "link": "fal/seedance-2.0-fast", "route": route, "clip_s": 4,
                              "est_usd": 0.4, "prompt_hash": "a" * 64, "image_sha256": "b" * 64, "cache_key": None,
                              "generated_at": NOW}
    shot["assets"]["video"] = f"assets/clips/shot_{shot_id[2:]}.mp4"
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    return path


def test_a_current_clip_is_never_replaced_even_with_no_keyframe(store, tmp_path):
    story_id = stock_episode(store, tmp_path)
    path = _generated_clip(store, story_id)
    source = FakeSource()
    result, board, _log, _ = run_fill(store, story_id, source=source)
    assert result["filled"] == [] and source.asked == []
    assert path.read_bytes() == b"bought clip" and shot_of(board)["assets"]["clip"]["route"] == "paid"
    assert not clip_file(store, story_id).exists()


def test_an_uploaded_clip_over_a_stock_shot_is_never_replaced_either(store, tmp_path):
    story_id = stock_episode(store, tmp_path)
    run_fill(store, story_id)
    board = tas._board(store, story_id)
    shot = shot_of(board)
    shot["assets"]["clip"] = dict(shot["assets"]["clip"], route="manual", link="manual/upload", source=None)
    shot["assets"]["clip"].pop("source")
    shot["assets"]["video"] = "assets/clips/shot_02.manual.mp4"
    Path(store.episode_asset_path(story_id, 1, "clips", "shot_02.manual.mp4", create=True)).write_bytes(b"mine")
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    source = FakeSource([clip_of("9")])
    result, after, _log, _ = run_fill(store, story_id, source=source)
    assert result["filled"] == [] and source.asked == []
    assert shot_of(after)["assets"]["video"] == "assets/clips/shot_02.manual.mp4"


def test_a_current_keyframe_a_locked_image_and_a_pending_redraw_are_never_replaced(store, tmp_path):
    story_id = stock_episode(store, tmp_path)
    # every image made as usual (the switch is on but the fake source finds nothing)
    run_fill(store, story_id, source=FakeSource([]))
    tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    before = shot_of(tas._board(store, story_id))["assets"]["image"]
    source = FakeSource()
    result, board, _log, _ = run_fill(store, story_id, source=source)
    assert result["filled"] == [] and source.asked == [] and shot_of(board)["assets"]["image"] == before
    assert shot_of(board)["assets"].get("route") != "stock"
    # a locked image, with no current state
    board = tas._board(store, story_id)
    shot = shot_of(board)
    shot["assets"]["image"], shot["assets"]["locked"] = None, True
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    assert run_fill(store, story_id, source=source)[0]["filled"] == []
    board = tas._board(store, story_id)
    shot_of(board)["assets"]["locked"] = False
    shot_of(board)["assets"]["pending"] = {"seed": 5, "note": None, "requested_at": NOW}
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    assert run_fill(store, story_id, source=source)[0]["filled"] == [] and source.asked == []


def test_a_second_fill_keeps_the_stock_shot_and_asks_nothing(store, tmp_path):
    story_id = stock_episode(store, tmp_path)
    run_fill(store, story_id)
    source = FakeSource([clip_of("5")])
    result, board, _log, _ = run_fill(store, story_id, source=source)
    assert result["filled"] == [] and result["kept"] == [SHOT] and source.asked == []
    assert clip_file(store, story_id).read_bytes() == b"stock-video-pexels:1"


# =============================================================== the state

def test_a_stock_clip_is_current_only_while_the_switch_is_on_the_shot_is_eligible_and_the_query_matches(store,
                                                                                                          tmp_path):
    from clipping.aistory.steps import assets

    story_id = stock_episode(store, tmp_path)
    run_fill(store, story_id)
    assert clip_state(store, story_id) == "current"
    # the place renamed: the query, so the hash, moves
    place = store.read_entity(story_id, "places", tas.PARLOIR)
    store.write_entity(story_id, "places", dict(place, name="La Cabine Secrète"), now=NOW)
    assert clip_state(store, story_id) == "stale"
    store.write_entity(story_id, "places", place, now=NOW)
    assert clip_state(store, story_id) == "current"
    # the scene's time of day changed
    script = tas.eps._script(store, story_id)
    next(scene for scene in script["scenes"] if scene["scene_id"] == "s01")["time_variant"] = "night"
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    assert clip_state(store, story_id) == "stale"
    script["scenes"][0]["time_variant"] = "day"
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    # a character walks into the shot: no longer a cutaway
    board = tas._board(store, story_id)
    shot_of(board)["subject_tags"] = [PLACE_TAG, "@char_kiwilo"]
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    assert clip_state(store, story_id) == "stale"
    assert assets.shot_state(context(store, story_id), shot_of(tas._board(store, story_id))) == "stale"
    board = tas._board(store, story_id)
    shot_of(board)["subject_tags"] = [PLACE_TAG]
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    # the switch off
    store.update(story_id, lambda doc: doc["generation_profile"].pop("stock_cutaways"), now=NOW)
    assert clip_state(store, story_id) == "stale"
    turn_on(store, story_id)
    assert clip_state(store, story_id) == "current"


def test_the_hash_follows_the_story_s_frame(store, tmp_path):
    story_id = stock_episode(store, tmp_path)
    ec = context(store, story_id)
    script, board = documents(store, story_id)
    shot = shot_of(board)
    wide = dataclasses.replace(ec, story=dict(ec.story, generation_profile=dict(ec.story["generation_profile"],
                                                                                pipeline="v2", aspect="16:9")))
    assert media_policy.aspect(wide.story) == "16:9"
    assert stock_cutaways.expected_hash(ec, shot, script) != stock_cutaways.expected_hash(wide, shot, script)
    query = stock_cutaways.query_of(ec, shot, script)
    assert stock_cutaways.expected_hash(wide, shot, script) == stock_cutaways.query_hash(query, "16:9")


def test_a_stale_stock_shot_is_looked_up_again_and_given_up_when_nothing_matches(store, tmp_path):
    story_id = stock_episode(store, tmp_path)
    run_fill(store, story_id)
    place = store.read_entity(story_id, "places", tas.PARLOIR)
    store.write_entity(story_id, "places", dict(place, name="La Cabine Secrète"), now=NOW)
    source = FakeSource([clip_of("2")])
    result, board, _log, _ = run_fill(store, story_id, source=source)
    assert result["filled"] == [SHOT] and source.asked[0]["query"].startswith("La Cabine Secrète")
    assert clip_file(store, story_id).read_bytes() == b"stock-video-pexels:2"
    assert clip_state(store, story_id) == "current"
    # a name nobody has footage of: the shot goes back to being generated, nothing stock left on it
    store.write_entity(story_id, "places", dict(place, name="Le Salon Rose"), now=NOW)
    result, board, _log, _ = run_fill(store, story_id, source=FakeSource([]))
    shot = shot_of(board)
    assert result["filled"] == [] and shot["assets"]["image"] is None and not shot["assets"].get("clip")
    assert shot["assets"]["video"] is None and "route" not in shot["assets"] and not clip_file(store, story_id).exists()


# ============================================================== the step

def patch_stock(monkeypatch, *, source=None):
    """The assets step's own fill, through the fakes: the search, the download and the frame."""
    from clipping.aistory.steps import assets as assets_step

    source = source or FakeSource()

    def search(query, *, aspect, min_duration_s=0.0, sources=None, env=None, pool=None, rng=None):
        found = source.candidates(query, aspect=aspect, min_duration_s=min_duration_s, env=env)
        return pool.take(found) if found else None

    monkeypatch.setattr(stock_base, "search", search)
    monkeypatch.setattr(stock_base, "download", fake_download)
    monkeypatch.setattr(assets_step._Assets, "crop_run", FakeFfmpeg(), raising=False)
    return source


def test_the_assets_step_fills_first_makes_no_image_for_the_stock_shot_and_writes_the_credits(store, tmp_path,
                                                                                              monkeypatch):
    story_id = stock_episode(store, tmp_path)
    source = patch_stock(monkeypatch)
    image = tas.FakeImage()
    summary, log = tas._run(store, story_id, adapters=tas._adapters(image=image))

    assert summary["stock"] == {"filled": [SHOT], "kept": [], "missing": []}
    assert summary["complete"] is True and len(source.asked) == 1
    # no image was asked for the stock shot, every other shot was made
    total = len(tas._board(store, story_id)["shots"])
    assert "shot_02" not in image.names()
    assert summary["shots"]["made"] + summary["shots"]["cached"] == total - 1
    assert summary["shots"]["states"][SHOT] == "current"
    assert any("Stock cutaways: 1 shot(s) filled" in line for line in log)
    # nothing was booked for it: a stock clip is free
    assert all(entry["unit"] != "image" or entry.get("target") != SHOT for entry in tas._ledger(store, story_id))
    # the credits, as files and as the data the pack carries
    folder = Path(store.episode_dir(story_id, 1)) / "assets"
    credits = json.loads((folder / "stock_credits.json").read_text(encoding="utf-8"))
    assert credits["$schema"] == "stock_credits_v1" and [item["shot_id"] for item in credits["credits"]] == [SHOT]
    assert credits["credits"][0]["author"] == "Ann Fotograf" and credits["credits"][0]["provider"] == "pexels"
    text = (folder / "stock_credits.txt").read_text(encoding="utf-8")
    assert text.startswith(f"{SHOT}: Video by Ann Fotograf on Pexels")
    # the board validated and kept its approval
    board = store.read_episode_doc(story_id, 1, "storyboard.json")
    assert board["approved_at"] and shot_of(board)["assets"]["clip"]["route"] == "stock"
    # a second run asks the source nothing and keeps the shot
    summary, _log = tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    assert len(source.asked) == 1 and summary["stock"]["kept"] == [SHOT] and summary["stock"]["filled"] == []


def test_a_story_without_the_switch_runs_the_step_exactly_as_before(store, tmp_path, monkeypatch):
    story_id = stock_episode(store, tmp_path, switch=False)
    source = patch_stock(monkeypatch)
    summary, _log = tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    assert "stock" not in summary and source.asked == []
    folder = Path(store.episode_dir(story_id, 1)) / "assets"
    assert not (folder / "stock_credits.json").exists() and not (folder / "stock_credits.txt").exists()


def test_the_credits_follow_the_current_stock_clips_and_go_with_the_last_one(store, tmp_path):
    story_id = stock_episode(store, tmp_path)
    _result, board, _log, _ = run_fill(store, story_id)
    ec = context(store, story_id)
    credits = stock_cutaways.write_credits(ec, board)
    assert [item["shot_id"] for item in credits] == [SHOT]
    assert stock_cutaways.credit_text(credits).startswith("Stock footage: Video by Ann Fotograf on Pexels")
    assert stock_cutaways.providers_of(credits) == ["pexels"]
    folder = Path(store.episode_dir(story_id, 1)) / "assets"
    assert (folder / "stock_credits.json").exists()
    shot_of(board)["assets"]["clip"] = None
    assert stock_cutaways.write_credits(ec, board) == [] and not (folder / "stock_credits.json").exists()
    assert not (folder / "stock_credits.txt").exists()
    assert stock_cutaways.credit_text([]) == ""


# ============================================================ the profile key

def test_the_profile_key_is_on_or_absent_clearable_and_patchable_any_time(store):
    from clipping.aistory import store as store_mod

    assert defaults.STOCK_CUTAWAYS_MODES == ("on",) and "stock_cutaways" not in defaults.default_generation_profile()
    assert schemas._GENERATION_PROFILE_SCHEMA["properties"]["stock_cutaways"] == {"type": "string", "enum": ["on"]}
    assert store_mod._merge_generation_profile({"stock_cutaways": "on"})["stock_cutaways"] == "on"
    for bad in ({"stock_cutaways": "off"}, {"stock_cutaways": True}, {"stock_cutaways": 1}):
        with pytest.raises(ValueError):
            store_mod._merge_generation_profile(bad)
    assert "stock_cutaways" not in store_mod._merge_generation_profile({"stock_cutaways": None})
    assert "stock_cutaways" in store_mod._PROFILE_CLEARABLE
    # the key comes after the others in every enumeration of the profile's keys
    assert list(store_mod._PROFILE_CHOICES)[-2:] == ["aspect", "stock_cutaways"]
    assert media_policy.stock_cutaways({"generation_profile": {"stock_cutaways": "on"}}) is True
    assert media_policy.stock_cutaways({"generation_profile": {}}) is False and media_policy.stock_cutaways(None) is False


def test_the_api_model_carries_the_key_and_drops_the_absent_one():
    pytest.importorskip("pydantic")
    from web.api.models import GenerationProfileModel

    assert "stock_cutaways" not in GenerationProfileModel().model_dump()
    assert GenerationProfileModel(stock_cutaways="on").model_dump()["stock_cutaways"] == "on"


def test_the_switch_is_patched_and_cleared_on_a_story_at_any_time(store, tmp_path):
    from clipping.aistory import workflow

    story_id = tas._episode(store, tmp_path)
    patched = workflow.patch_story(store, story_id, {"generation_profile": {"stock_cutaways": "on"}}, now=NOW)
    assert patched["generation_profile"]["stock_cutaways"] == "on"
    cleared = workflow.patch_story(store, story_id, {"generation_profile": {"stock_cutaways": None}}, now=NOW)
    assert "stock_cutaways" not in cleared["generation_profile"]


def test_the_estimate_keeps_the_generated_price_and_says_up_to_n_shots_may_be_stock(store, tmp_path, monkeypatch):
    from clipping.aistory.steps import assets as assets_step

    story_id = stock_episode(store, tmp_path, switch=False)
    ec = context(store, story_id)
    script, board = documents(store, story_id)
    plain = assets_step.asset_units(ec, script, board, env=tas._settings())
    assert "stock" not in plain
    turn_on(store, story_id)
    ec = context(store, story_id)
    monkeypatch.setattr("clipping.stock.clips.available_sources", lambda cfg=None: ["pexels"])
    units = assets_step.asset_units(ec, script, board, env=tas._settings())
    assert units["stock"]["shots"] == [SHOT] and units["stock"]["count"] == 1
    assert units["stock"]["message"] == "up to 1 shot may be stock (free, saves ≈ $0.00)"
    # the price is the generated one, every image still counted
    assert units["est_usd"] == plain["est_usd"] and units["images"]["count"] == plain["images"]["count"]
    # without any source it says so, never promising a saving
    monkeypatch.setattr("clipping.stock.clips.available_sources", lambda cfg=None: [])
    assert "no stock source is configured" in assets_step.asset_units(ec, script, board,
                                                                      env=tas._settings())["stock"]["message"]
