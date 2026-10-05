"""Stock cutaways through the render, the brief, the judge, the estimate and the pack (plan 23
stage B8).

A shot filled with stock footage (``tests/test_story_stock_cutaways.py``: the fakes, the episode)
is cut from its clip as plain ``video`` at every tier -- at tier 1 too, where the render reads no
other clip -- and never carries the clip's own sound (``-an``; no ambience, no native audio). A
render of an episode with no stock cutaway is the one it always was (``shot_clips`` answers None).
The brief calls the shot ``stock`` (excluded from the clips and the keyframes still to upload),
the keyframe judge leaves its frame alone, the estimate says what stock may save and, once the fill
ran, nothing more to make, and the metadata pack credits the footage. Offline and hermetic (the
assets step's own ``hermetic`` fixture, and the stock cutaways' own). Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import test_story_assets_step as tas
import test_story_metadata_step as tms
import test_story_native_speech_plan as nsp
import test_story_render_step as trs
import test_story_stock_cutaways as tsc
from clipping.aistory import media_policy, schemas, stock_cutaways
from clipping.aistory.steps import assets as assets_step
from clipping.aistory.steps import brief as brief_mod
from clipping.aistory.steps import clips, render as render_step
from test_story_assets_step import hermetic, store  # noqa: F401 -- the step's fixtures (hermetic is autouse)
from test_story_stock_cutaways import no_stock_network  # noqa: F401 -- autouse: no stock key, no request

SHOT = tsc.SHOT
NOW = tsc.NOW


def _spy(monkeypatch):
    from clipping.aistory.render import plan as plan_mod

    seen = []
    real = plan_mod.build_render_plan

    def spy(**kwargs):
        plan = real(**kwargs)
        seen.append((kwargs, plan))
        return plan

    monkeypatch.setattr(plan_mod, "build_render_plan", spy)
    return seen


def stock_assets(store, tmp_path, monkeypatch, *, tier=None):
    """The stock episode with its assets made through the step (the fill first), approved:
    ``story_id``."""
    story_id = tsc.stock_episode(store, tmp_path)
    if tier is not None:
        store.update(story_id, lambda doc: doc["generation_profile"].update(tier=tier), now=NOW)
    tsc.patch_stock(monkeypatch)
    summary, _log = tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    assert summary["complete"] is True and summary["stock"]["filled"] == [SHOT]
    trs.approve_assets(store, story_id)
    return story_id


def documents(store, story_id):
    ec = tsc.context(store, story_id)
    script, board = tsc.documents(store, story_id)
    return ec, script, board, store.read_episode_doc(story_id, 1, "assets.json")


# ================================================================== shot_clips

def test_tier_1_reads_no_clip_without_stock(store, tmp_path):
    """The byte-identity guard of every render without stock cutaways: the switch off, or on with no
    shot filled, ``shot_clips`` answers None as it always did."""
    story_id = tsc.stock_episode(store, tmp_path, switch=False)
    ec, script, board, doc = documents(store, story_id)
    assert clips.tier_of(ec) == 1 and render_step.shot_clips(ec, script, board, doc) is None
    tsc.turn_on(store, story_id)
    ec, script, board, doc = documents(store, story_id)
    assert render_step.shot_clips(ec, script, board, doc) is None  # on, nothing filled yet


def test_tier_1_returns_the_videos_of_the_stock_shots_only(store, tmp_path, monkeypatch):
    story_id = stock_assets(store, tmp_path, monkeypatch)
    ec, script, board, doc = documents(store, story_id)
    resolved = render_step.shot_clips(ec, script, board, doc)
    assert list(resolved["videos"]) == [SHOT] and resolved["keep_still"] == {SHOT: False}
    assert resolved["videos"][SHOT]["source"] == "assets/clips/shot_02.stock.mp4"
    assert resolved["filled"] == [] and resolved["native_audio"] == [] and resolved["ambience"] == []
    assert resolved["notes"] == []
    # the inputs carry them at tier 1 and nothing else a tier-1 render did not have
    inputs = render_step.render_inputs(ec, script, board, doc, custom_fonts_dir=tmp_path / "no_custom_fonts",
                                       shot_clips_now=resolved)
    assert list(inputs["videos"]) == [SHOT] and inputs["filled"] == [] and "native_audio" not in inputs
    assert "ambience" not in inputs
    # kept still by the human: the shot is cut from its frame
    doc = dict(doc, shots={SHOT: {"keep_still": True}})
    assert render_step.shot_clips(ec, script, board, doc) is None


def test_a_stock_clip_that_is_not_current_is_no_error_at_tier_1(store, tmp_path, monkeypatch):
    story_id = stock_assets(store, tmp_path, monkeypatch)
    store.update(story_id, lambda doc: doc["generation_profile"].pop("stock_cutaways"), now=NOW)
    ec, script, board, doc = documents(store, story_id)
    assert render_step.shot_clips(ec, script, board, doc) is None


def test_a_stock_shot_never_carries_ambience_or_native_audio_at_tier_3(store, tmp_path, monkeypatch):
    story_id = stock_assets(store, tmp_path, monkeypatch, tier=3)
    ec, script, board, doc = documents(store, story_id)
    assert clips.tier_of(ec) == 3

    def never_read(_path):
        raise AssertionError("a stock clip's sound track is never read")

    monkeypatch.setattr(clips, "clip_has_audio", never_read)
    # an ambience story, and a human who asked this shot to keep its native audio
    monkeypatch.setattr(media_policy, "ambience", lambda _story: True)
    resolved = render_step.shot_clips(ec, script, board, dict(doc, shots={SHOT: {"keep_native_audio": True}}))
    assert list(resolved["videos"]) == [SHOT]
    assert resolved["native_audio"] == [] and resolved["ambience"] == [] and resolved["notes"] == []
    monkeypatch.setattr(media_policy, "ambience", lambda _story: False)
    resolved = render_step.shot_clips(ec, script, board, dict(doc, shots={SHOT: {"keep_native_audio": True}}))
    assert list(resolved["videos"]) == [SHOT] and resolved["native_audio"] == [] and resolved["ambience"] == []


# ============================================================ the render itself

def test_a_stock_shot_is_cut_from_its_clip_as_plain_video_and_never_with_its_sound(store, tmp_path, monkeypatch):
    story_id = stock_assets(store, tmp_path, monkeypatch)
    seen = _spy(monkeypatch)
    summary, _log, _fake = trs.render(store, story_id, tmp_path=tmp_path)
    assert summary["state"] == "completed" and len(seen) == 1
    kwargs, plan = seen[0]
    assert list(kwargs["inputs"]["videos"]) == [SHOT]
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    modes = manifest["shot_modes"]
    assert modes[SHOT] == "video" and {mode for shot_id, mode in modes.items() if shot_id != SHOT} == {"motion"}
    stage = next(item for item in manifest["stages"] if item["id"] == f"S:{SHOT}")
    argv = stage["argv"]
    # the clip is the input, its own sound dropped (-an), no looped still
    assert "-loop" not in argv and "-an" in argv
    assert any(str(item).endswith(".mp4") for item in argv[argv.index("-i") + 1:argv.index("-i") + 2])
    # the audio stages never read the clip: no clip stem, no ambience, no native audio
    assert "native_audio" not in kwargs["inputs"] and "ambience" not in kwargs["inputs"]
    assert not any(stage["id"].startswith(("N:", "V:")) for stage in manifest["stages"])


def test_a_render_without_stock_has_no_video_input_and_no_shot_modes(store, tmp_path, monkeypatch):
    story_id = tsc.stock_episode(store, tmp_path, switch=False)
    tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    trs.approve_assets(store, story_id)
    seen = _spy(monkeypatch)
    summary, _log, _fake = trs.render(store, story_id, tmp_path=tmp_path)
    assert summary["state"] == "completed"
    assert "videos" not in seen[0][0]["inputs"] and "keep_still" not in seen[0][0]["inputs"]
    assert "shot_modes" not in store.read_episode_doc(story_id, 1, "render_manifest.json")


# =================================================================== the brief

def manual_story(store, tmp_path, *, own_images=False):
    """A manual-clips native-speech story planned and approved, one silent shot an establishing wide
    with only its place in its tags, the stock switch on."""
    story_id = nsp.planned_story(store, profile="native_speech_manual")
    if own_images:
        store.update(story_id, lambda doc: doc["generation_profile"].update(images="manual"), now=NOW)
    tsc.turn_on(store, story_id)
    board = tas._board(store, story_id)
    shot = next(item for item in board["shots"] if not item.get("speaks"))
    scene = next(item for item in tas.eps._script(store, story_id)["scenes"] if item["scene_id"] == shot["scene_id"])
    shot["framing"], shot["subject_tags"] = "wide_establishing", [f"#{scene['place_id']}:{scene['time_variant']}"]
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    return story_id, shot["shot_id"]


def test_the_brief_calls_a_stock_shot_stock_and_asks_no_clip_and_no_keyframe_for_it(store, tmp_path):
    story_id, shot_id = manual_story(store, tmp_path, own_images=True)
    ec = tsc.context(store, story_id)
    script, board = tsc.documents(store, story_id)
    assert shot_id in [item["shot_id"] for item in brief_mod.missing_clips(ec, script, board)]
    assert shot_id in [item["shot_id"] for item in brief_mod.missing_keyframes(ec, board)]
    before = brief_mod.shot_brief(ec, platform="flow")
    entry = next(item for item in before["shots"] if item["shot_id"] == shot_id)
    assert entry["state"] == "missing" and entry["stock"] is None and "stock" not in before["counts"]

    result = stock_cutaways.fill(ec, script, board, None, env={}, sources=[tsc.FakeSource()], run=tsc.FakeFfmpeg(),
                                 download=tsc.fake_download, pool=tsc.StockPool(), now=NOW)
    assert result["filled"] == [shot_id]
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    ec = tsc.context(store, story_id)
    script, board = tsc.documents(store, story_id)
    assert shot_id not in [item["shot_id"] for item in brief_mod.missing_clips(ec, script, board)]
    assert shot_id not in [item["shot_id"] for item in brief_mod.missing_keyframes(ec, board)]
    brief = brief_mod.shot_brief(ec, platform="flow")
    entry = next(item for item in brief["shots"] if item["shot_id"] == shot_id)
    assert entry["state"] == "stock"
    assert entry["stock"] == "Stock footage, no generation needed — Pexels, by Ann Fotograf"
    assert brief["counts"]["stock"] == 1 and brief["counts"]["missing"] == len(before["shots"]) - 1
    assert "stock" in brief_mod.STATES
    markdown = brief_mod.render_markdown(brief)
    assert "Stock footage, no generation needed — Pexels, by Ann Fotograf." in markdown and "— stock" in markdown


def test_an_upload_over_a_stock_clip_replaces_its_record_and_its_credit(store, tmp_path, monkeypatch):
    from clipping.aistory import manual_uploads

    story_id, shot_id = manual_story(store, tmp_path)
    ec = tsc.context(store, story_id)
    script, board = tsc.documents(store, story_id)
    stock_cutaways.fill(ec, script, board, None, env={}, sources=[tsc.FakeSource()], run=tsc.FakeFfmpeg(),
                        download=tsc.fake_download, pool=tsc.StockPool(), now=NOW)
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    assert stock_cutaways.write_credits(ec, board)
    stock_file = tsc.clip_file(store, story_id, shot_id)
    assert stock_file.is_file()
    monkeypatch.setattr(manual_uploads, "probe_clip", lambda path, run=None: {
        "duration_s": 5.0, "width": 720, "height": 1280, "audio": False, "video": True})
    ffprobe = lambda argv, **_kwargs: SimpleNamespace(returncode=0, stdout="5.0\n", stderr="")  # noqa: E731
    received = Path(manual_uploads.clips_folder(store, story_id, 1)) / "incoming.mp4"
    received.write_bytes(b"my own clip")
    manual_uploads.accept_clip(store, story_id, 1, shot_id, str(received), filename="mine.mp4", now=NOW,
                               transcribe=lambda *a, **k: ([], "none"), run=ffprobe)
    shot = tsc.shot_of(tas._board(store, story_id), shot_id)
    assert shot["assets"]["clip"]["route"] == "manual" and shot["assets"]["video"].endswith("shot_%s.manual.mp4" % shot_id[2:])
    assert not stock_file.exists()
    assert not (Path(store.episode_dir(story_id, 1)) / "assets" / "stock_credits.json").exists()
    # and the fill never takes the human's clip back
    again = tsc.FakeSource()
    ec = tsc.context(store, story_id)
    script, board = tsc.documents(store, story_id)
    assert stock_cutaways.fill(ec, script, board, None, env={}, sources=[again], run=tsc.FakeFfmpeg(),
                               download=tsc.fake_download, pool=tsc.StockPool(), now=NOW)["filled"] == []
    assert again.asked == []


# ==================================================================== the judge

def test_the_keyframe_judge_skips_a_stock_frame_and_gives_its_neighbour_no_continuity(store, tmp_path, monkeypatch):
    story_id = stock_assets(store, tmp_path, monkeypatch)
    ec, script, board, doc = documents(store, story_id)
    items = assets_step.keyframe_items(ec, board, doc)
    ids = [item[0]["shot_id"] for item in items]
    assert SHOT not in ids and len(ids) == len(board["shots"]) - 1
    after = next(item for item in items if item[0]["shot_id"] == "sh03")
    assert after[3:] == (None, None, None)  # sh02, the shot before it, is a frame of a clip: no previous
    index = [shot["shot_id"] for shot in board["shots"]].index(SHOT)
    assert assets_step.keyframe_item(ec, board, index) is None
    neighbour = assets_step.keyframe_item(ec, board, index + 1)
    assert neighbour[0]["shot_id"] == "sh03" and neighbour[3:] == (None, None, None)


# ================================================================== the estimate

def test_the_estimate_line_before_the_fill_and_after_it_the_shot_costs_nothing(store, tmp_path, monkeypatch):
    story_id = tsc.stock_episode(store, tmp_path)
    ec = tsc.context(store, story_id)
    script, board = tsc.documents(store, story_id)
    monkeypatch.setattr("clipping.stock.clips.available_sources", lambda cfg=None: ["pexels"])
    before = assets_step.asset_units(ec, script, board, env=tas._settings())
    assert before["stock"]["message"].startswith("up to 1 shot may be stock (free, saves ≈ $")
    assert SHOT in before["images"]["shots"]
    tsc.patch_stock(monkeypatch)
    tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage()))
    ec = tsc.context(store, story_id)
    script, board = tsc.documents(store, story_id)
    after = assets_step.asset_units(ec, script, board, env=tas._settings())
    assert after["stock"]["count"] == 0 and after["stock"]["stock"] == [SHOT]
    assert after["stock"]["message"] == "1 shot filled with stock footage (free)."
    assert SHOT not in after["images"]["shots"] and after["images"]["count"] == 0 and after["est_usd"] == 0.0


def test_the_estimate_api_answer_carries_the_line(store, tmp_path, monkeypatch):
    from clipping.aistory import workflow

    story_id = tsc.stock_episode(store, tmp_path)
    monkeypatch.setattr("clipping.stock.clips.available_sources", lambda cfg=None: ["pexels"])
    answer = workflow.assets_estimate(tsc.context(store, story_id), env=tas._settings())
    assert answer["stock"]["shots"] == [SHOT] and answer["stock"]["message"].startswith("up to 1 shot may be stock")


# ===================================================================== the pack

def test_the_metadata_pack_credits_the_stock_footage_only_when_there_is_some(store, tmp_path, monkeypatch):
    story_id = stock_assets(store, tmp_path, monkeypatch)
    trs.render(store, story_id, tmp_path=tmp_path)
    summary, _log, _runner, _cover = tms.run_step(store, story_id, tmp_path=tmp_path)
    pack = tms.pack(store, story_id)
    assert [item["shot_id"] for item in pack["credits"]] == [SHOT] and pack["credits"][0]["provider"] == "pexels"
    assert schemas.metadata_pack_errors(pack) == []
    for entry in pack["platforms"].values():
        lines = entry["description"].split("\n\n")
        assert lines[-1].startswith("Stock footage: Video by Ann Fotograf on Pexels")
        assert lines[-2] == tsc.eps.TEASER  # after the teaser, never before it


def test_a_description_without_credits_is_the_one_it_always_was():
    reply = {"title": "T", "description": "Un secret.", "hashtags": ["a", "b", "c"], "hook_text": "Vote"}
    meta = tms._meta()
    without = meta.platform_entry(reply, ep=1, language="en", teaser="Tomorrow.", now=NOW)
    assert without["description"] == "Un secret.\n\nTomorrow."
    assert meta.platform_entry(reply, ep=1, language="en", teaser="Tomorrow.", now=NOW, credits=[]) == without
    credit = {"credit": "Video by Ann on Pexels (https://www.pexels.com/video/1/), Pexels License"}
    withit = meta.platform_entry(reply, ep=1, language="en", teaser="Tomorrow.", now=NOW, credits=[credit])
    assert withit["description"] == f"Un secret.\n\nTomorrow.\n\nStock footage: {credit['credit']}"
    assert {key: value for key, value in withit.items() if key != "description"} == {
        key: value for key, value in without.items() if key != "description"}


# ============================================================== the dashboard

SRC = Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src"
STORY = SRC / "pages" / "story"


def test_the_dashboard_names_the_stock_state_the_card_s_select_and_the_credit_links():
    # 2026-10-05 (plan 25 stage 3, DEC-301): the Shot list pane is retired; the Handoff card keeps the state.
    pane = (STORY / "episode" / "HandoffCard.jsx").read_text(encoding="utf-8")
    assert "stock: { tone: 'info', label: 'Stock footage' }" in pane and "block.stock" in pane
    card = (STORY / "GenerationProfileCard.jsx").read_text(encoding="utf-8")
    assert "save({ stock_cutaways: value || null })" in card and "STOCK_MATCHING_STYLES = ['cinematic_real']" in card
    assert "Stock footage is live-action" in card
    preview = (STORY / "episode" / "PreviewPane.jsx").read_text(encoding="utf-8")
    assert "Videos provided by" in preview and "https://www.pexels.com" in preview and "https://pixabay.com" in preview
    assert "<StockCredits pack={episode.metadata.pack} />" in preview
    cards = (STORY / "episode" / "storyboard" / "AssetsCards.jsx").read_text(encoding="utf-8")
    assert "estimate.stock && estimate.stock.message" in cards
    chip = (SRC / "components" / "RouteChip.jsx").read_text(encoding="utf-8")
    assert "stock: 'stock'" in chip
