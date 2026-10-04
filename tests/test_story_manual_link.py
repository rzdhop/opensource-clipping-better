"""The manual link: the human as a provider (plan 22, stage 5).

A story on the ``native_speech_manual`` budget profile names
``manual/upload`` for every speaking and silent clip: the app writes a
crafted shot brief per platform (Google Flow, Higgsfield), the human makes
the clips on their own subscription and uploads them; the native take, the
timing and the render run as for any clip. The link never sends a request
and never books a cent (RC-N4); the estimate shows the clips at $0 with
what they cost in the platform's credits; the assets step lists the clips
still to upload and ends ``awaiting_uploads``.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import json

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_native_speech_plan as nsp
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_clip_estimate import timings_path  # noqa: F401 - the measured clip timings under tmp_path

NOW = tas.NOW
MANUAL = "manual/upload"
PROFILE = "native_speech_manual"


def manual_story(store, **kwargs):
    """A planned native-speech story on the manual profile: its storyboard
    built (fast) and approved, ready for the assets step."""
    return nsp.planned_story(store, profile=PROFILE, **kwargs)


# ================================================================ the provider

class _Counting:
    """A transport, a budget check and a limiter that count every call."""

    def __init__(self):
        self.calls = []

    def transport(self, *args, **kwargs):
        self.calls.append(("transport", args))
        raise AssertionError("a manual link never sends a request")

    def check(self, estimate, link):
        self.calls.append(("budget", estimate))

    def acquire(self, provider):
        self.calls.append(("limiter", provider))


def test_the_manual_link_never_sends_and_never_books(tmp_path):
    """Fail-first (RC-N4). ``manual/upload`` is free, keyless, $0; the
    runner asks it nothing: ``AwaitingUpload`` ends the chain before the
    route, the keys, the budget, the limiter or the journal -- and a link
    after it never stands in for the human."""
    from clipping.providers import adapters, gencache, pricing
    from clipping.providers import generation as gen

    adapters.load_all()
    link = gen.parse_generation_chain(gen.VIDEO, [MANUAL])[0]
    assert gen.is_paid(link) is False and gen.missing_keys(link, {}) == [] and gen.is_manual(link)
    assert pricing.estimate(link, 8).est_usd == 0.0
    for kind in (gen.IMAGE, gen.IMAGE_EDIT, gen.VIDEO):
        assert gen.adapter_for(kind, "manual").estimate(link, gen.GenRequest(kind=kind)) == 0.0
    chain = gen.parse_generation_chain(gen.VIDEO, [MANUAL, "fal/seedance-1-pro-fast"])
    counting = _Counting()
    booked = []
    cache = gencache.GenCache(str(tmp_path / "gen"), book=lambda *a, **k: booked.append(a))
    request = gen.GenRequest(kind=gen.VIDEO, prompt="x", duration_s=8, seed=1, references=("k.png",),
                             out_dir=str(tmp_path), extra={"target": "sh03"})
    with pytest.raises(gen.AwaitingUpload) as caught:
        gen.run_generation_chain(gen.VIDEO, chain, request, env={"FAL_KEY": "k"}, allow_paid=True,
                                 on_log=lambda _line: None, budget_check=counting.check, limiter=counting,
                                 transport=counting.transport, cache=cache)
    assert caught.value.target == "sh03" and "nothing was sent or booked" in str(caught.value)
    assert counting.calls == [] and booked == []
    assert not (tmp_path / "gen").exists() or not any((tmp_path / "gen").rglob("*.json"))


def test_the_manual_profile_names_your_own_clips_and_the_image_switch():
    from clipping.aistory import defaults, media_policy
    from clipping.providers import budget

    profile = budget.profile_settings(PROFILE)
    assert profile["label"] == "Native speech — your own clips" and profile["cap_usd"] == 2.0
    assert set(profile["speech_links"].values()) == {MANUAL} and profile["silent_link"] == MANUAL
    assert (profile["speech_model"], profile["tier3_native_audio"], profile["lipsync"]) == ("fast", "speech", "none")
    assert profile["speech_retake"] == {"max_per_shot": 0, "cap_usd": 0}
    story = {"generation_profile": defaults.manual_speech_generation_profile()}
    assert media_policy.native_speech(story) and media_policy.clips_manual(story)
    assert media_policy.images_manual(story) is False
    assert media_policy.speech_retake(story) == {"max_per_shot": 0, "cap_usd": 0.0}
    # The per-story switch: every image role on the human's upload too.
    manual_images = {"generation_profile": dict(story["generation_profile"], images="manual")}
    assert media_policy.images_manual(manual_images)
    for role in media_policy.ROLES:
        for kind in ("image", "image_edit"):
            assert [f"{link.provider}/{link.model}" for link in
                    media_policy.role_chain(role, kind, {}, manual_images)] == [MANUAL]


def test_the_estimate_shows_your_own_clips_at_zero_with_their_flow_credits():
    """The preset's figure: twelve clips the human makes ($0 here, ≈ 240
    Flow credits on AI Pro), the keyframes the app draws."""
    from clipping.aistory import media_policy

    offer = media_policy.new_story_offer({"FAL_KEY": "k"})
    assert offer["manual"] is True and offer["profile"]["budget_profile"] == PROFILE
    manual = offer["native_speech_manual"]
    assert manual["manual"] is True and manual["clips_usd"] == 0.0 and manual["retake_usd"] == 0.0
    assert manual["episode_usd"] == pytest.approx(manual["keyframes_usd"])
    assert "your own clips (12 shots, ≈ 240 Flow credits on AI Pro" in manual["summary"]
    assert manual["missing_keys"] == []


# ================================================================ the brief

def _planted(store, story_id):
    """Every character portrait, place plate and prop image on disk (the
    fixture's documents name them; the brief lists only files that exist)."""
    import test_story_storyboard_props as tsp

    for char_id in ("char_kiwilo", "char_mangella", "char_broccolia"):
        tsp._plant_image(store, story_id, "characters", char_id, "portrait.jpg")
    for place_id in ("place_le_parloir_des_secrets", "place_la_piscine_de_la_trahison"):
        for name in ("variant_day.jpg", "variant_night.jpg"):
            tsp._plant_image(store, story_id, "places", place_id, name)


def _keyframe(store, story_id, shot_id):
    """A keyframe on disk for *shot_id*, named on its board."""
    board = tas._board(store, story_id)
    shot = next(item for item in board["shots"] if item["shot_id"] == shot_id)
    name = f"shot_{shot_id[2:]}.png"
    path = store.episode_asset_path(story_id, 1, "shots", name, create=True)
    with open(path, "wb") as fh:
        fh.write(tas.PNG)
    shot["assets"]["image"] = f"assets/shots/{name}"
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)


def _brief(store, story_id, platform):
    from clipping.aistory.steps import brief

    return brief.shot_brief(tas._ec(store, story_id), platform=platform)


def test_the_flow_brief_pins_each_shot_s_prompt_length_references_and_checks(store):
    """Fail-first. One entry a shot in order: a speaking shot carries stage
    4's speech prompt (the line quoted in the story's language, its voice,
    no captions), the line and its voice line; Flow sells 8 s clips only
    (said); 9:16; the references cut to Flow's three, the keyframe first
    when it exists, then the speaker's sheet, the listener's, the plate."""
    story_id = manual_story(store)
    _planted(store, story_id)
    board = tas._board(store, story_id)
    speaking = next(shot for shot in board["shots"] if shot.get("speaks") and len(shot["subject_tags"]) >= 3)
    _keyframe(store, story_id, speaking["shot_id"])
    brief = _brief(store, story_id, "flow")
    assert [entry["shot_id"] for entry in brief["shots"]] == [shot["shot_id"] for shot in board["shots"]]
    entry = next(item for item in brief["shots"] if item["shot_id"] == speaking["shot_id"])
    script = tas.eps._script(store, story_id)
    line = next(item for scene in script["scenes"] for item in scene["lines"]
                if item["line_id"] == speaking["lines"][0])
    assert f'says in French, in {entry["voice_line"]}, "{line["text"]}"' in entry["prompt"]
    assert entry["prompt"].endswith("No subtitles, no captions, no on-screen text.")
    speaker = store.read_entity(story_id, "characters", line["speaker"])["name"]
    assert (entry["line"], entry["speaker"]) == (line["text"], speaker)
    assert entry["length_s"] == 8 and "8 s clips only" in entry["length_note"] and entry["aspect"] == "9:16"
    assert [ref["kind"] for ref in entry["references"]] == ["keyframe", "sheet", "sheet"]
    assert entry["references"][1]["label"].endswith("character sheet (portrait)")
    assert entry["mode"].startswith("Frames to Video")
    assert any("lips move on the words" in check for check in entry["checks"])
    assert entry["upload_slot"] == f"/api/stories/{story_id}/episodes/1/shots/{speaking['shot_id']}/clip"
    assert entry["state"] == "missing" and brief["counts"]["missing"] == len(board["shots"])
    assert all(item["length_s"] == 8 for item in brief["shots"])
    silent = next(item for item in brief["shots"] if not item["speaks"])
    assert "nobody speaks or sings" in silent["prompt"] and silent["line"] is None
    assert brief["waiting"] == f"Waiting for {len(board['shots'])} clips — download the brief"


def test_the_higgsfield_brief_names_its_references_and_keeps_veo_for_french(store):
    from clipping.aistory.steps import brief as brief_mod

    story_id = manual_story(store)
    _planted(store, story_id)
    brief = _brief(store, story_id, "higgsfield")
    entry = next(item for item in brief["shots"] if item["speaks"] and len(item["references"]) == 3)
    assert entry["model"] == "veo-3.1" and entry["length_s"] == 8
    assert [ref["kind"] for ref in entry["references"]] == ["sheet", "sheet", "plate"]
    assert "References: image 1 is " in entry["prompt"] and "image 3 is " in entry["prompt"]
    # Kling speaks English and Chinese only: never picked for a French line.
    from clipping.aistory import platforms

    preset = platforms.load("higgsfield")
    assert platforms.model_of(dict(preset, default_model="kling-3.0"), speaks=True, language="fr")[0] == "veo-3.1"
    assert platforms.model_of(dict(preset, default_model="kling-3.0"), speaks=True, language="en")[0] == "kling-3.0"
    markdown = brief_mod.render_markdown(brief)
    assert markdown.startswith("# Shot brief — episode 1") and "```text" in markdown
    assert f"`{entry['references'][0]['file']}`" in markdown


def test_a_bad_preset_is_refused_by_its_schema():
    from clipping.aistory import platforms

    good = platforms.load("flow")
    assert platforms.preset_errors(good) == []
    bad = dict(good, aspect="16:9", models={"x": dict(good["models"]["veo-3.1-fast"], max_references=0)})
    errors = platforms.preset_errors(bad)
    assert any("aspect" in error for error in errors) and any("max_references" in error for error in errors)
    with pytest.raises(platforms.PresetError):
        platforms.load("sora")


# ================================================================ awaiting uploads, resume

def _clip_units(store, story_id):
    return tce._units(store, story_id, tas._settings())["video"]


def test_the_estimate_shows_the_clips_at_zero_and_their_credits(store):
    story_id = manual_story(store)
    video = _clip_units(store, story_id)
    count = len(tas._board(store, story_id)["shots"])
    assert video["ready"] and video["refused"] is None and video["route_class"] == "manual"
    assert video["est_usd"] == 0.0 and video["speech"]["manual_count"] == count
    assert f"your own clips ({count} shots, ≈ {count * 20} Flow credits on AI Pro" in video["message"]


def _host(store, story_id, **kwargs):
    import test_story_native_take as tnt
    from clipping.aistory.steps import assets

    host, log = tnt._host(store, story_id, **kwargs)
    host.open_asset_gates()
    ec = tas._ec(store, story_id)
    units = assets.asset_units(ec, host.script, host.storyboard, env=host.ctx.settings_env, animate=True)
    host.planned_video = units["video"]
    host.video = assets._video_summary(units["video"], animate=True)
    return host, log


def test_the_assets_step_lists_the_missing_clips_and_ends_awaiting_uploads(store):
    """Fail-first. On a manual story the clips are never asked of anything:
    the step lists every shot still missing its clip and ends
    ``awaiting_uploads`` with the list and where the brief is; nothing is
    booked."""
    story_id = manual_story(store)
    host, log = _host(store, story_id)
    doc = host.write_assets_doc()
    doc = host.animate_clips(doc)
    result = host.finish(doc)
    shots = [shot["shot_id"] for shot in tas._board(store, story_id)["shots"]]
    assert result["state"] == "awaiting_uploads" and result["complete"] is False
    uploads = result["uploads"]
    assert [item["shot_id"] for item in uploads["missing"]] == shots and uploads["count"] == len(shots)
    assert uploads["message"] == f"Waiting for {len(shots)} clips — download the brief"
    assert uploads["brief"] == f"/api/stories/{story_id}/episodes/1/brief.zip"
    assert any(line.startswith("✋ Waiting for") for line in log)
    assert tas._ledger(store, story_id) == []


def test_once_every_clip_is_uploaded_the_step_no_longer_waits(store, tmp_path):
    """Resume on upload: with every clip uploaded (one shot kept still), the
    step asks for nothing and ends ready for the approval as today."""
    import shutil

    import test_story_native_take as tnt
    from clipping.aistory import manual_uploads

    tnt._require_ffmpeg()
    story_id = manual_story(store)
    source = tnt.make_clip(tmp_path / "take.mp4", 8)
    folder = manual_uploads.clips_folder(store, story_id, 1)
    shots = tas._board(store, story_id)["shots"]
    for number, shot in enumerate(shots):
        received = f"{folder}/.upload-{number}.part"
        shutil.copyfile(source, received)
        out = manual_uploads.accept_clip(store, story_id, 1, shot["shot_id"], received, filename="t.mp4",
                                         env=tas._settings())
        assert out["state"] in ("approximate", "uploaded")
    assert out["missing"] == [] and out["waiting"] is None
    host, _log = _host(store, story_id)
    doc = host.animate_clips(host.write_assets_doc())
    result = host.finish(doc)
    assert "state" not in result and host.uploads is None


def test_an_8_s_upload_on_a_4_s_plan_is_judged_on_its_real_length(store, tmp_path):
    """The length rule (stage 4) holds for an upload: the line spoken past
    the planned 4 s but inside the 8 s clip is ``ok``, the shot cut 0.3 s
    after the last word."""
    import shutil

    import test_story_native_take as tnt
    from clipping.aistory import manual_uploads

    tnt._require_ffmpeg()
    story_id = manual_story(store)
    shot = next(item for item in tas._board(store, story_id)["shots"] if item.get("speaks") and item["clip_s"] == 4)
    script = tas.eps._script(store, story_id)
    text = next(line["text"] for scene in script["scenes"] for line in scene["lines"]
                if line["line_id"] == shot["lines"][0])
    heard = tnt.words(text, start=5.0 - 0.3 * len(tnt.words(text)) + 0.02)
    received = f"{manual_uploads.clips_folder(store, story_id, 1)}/.upload.part"
    shutil.copyfile(tnt.make_clip(tmp_path / "long.mp4", 8), received)
    out = manual_uploads.accept_clip(store, story_id, 1, shot["shot_id"], received, filename="long.mp4",
                                     env=tas._settings(), transcribe=tnt.Transcriber(heard))
    assert out["take"]["state"] == "ok" and out["take"]["end_s"] == pytest.approx(5.0)
    assert out["take"]["clip_real_s"] == pytest.approx(8.0, abs=0.05)
    assert out["duration_s"] == pytest.approx(5.3, abs=1 / 30)


def test_a_story_whose_images_are_yours_awaits_its_keyframes_and_asks_nothing(store):
    """``images: manual``: no keyframe is asked of anything (the image
    adapters are never called); the step waits for the keyframes first --
    the clips follow once they are approved."""
    story_id = manual_story(store)
    store.update(story_id, lambda doc: doc["generation_profile"].update(images="manual"), now=NOW)
    host, log = _host(store, story_id)
    host.images()
    result = host.finish(host.write_assets_doc())
    shots = tas._board(store, story_id)["shots"]
    assert result["state"] == "awaiting_uploads" and result["uploads"]["keyframes"] == len(shots)
    assert result["uploads"]["clips"] == 0 and result["failed"] == []
    assert result["uploads"]["message"] == f"Waiting for {len(shots)} keyframes — download the brief"
    assert {item["kind"] for item in result["uploads"]["missing"]} == {"keyframe"}
    assert any(line.startswith(f"✋ {len(shots)} keyframes to upload") for line in log)


def test_a_story_switches_its_images_to_yours_and_back_and_keeps_its_speech_model(store):
    """The profile merge takes the stage-5 switch (``images: manual``; null
    clears it) and stage 4's ``speech_model``, as a PATCH sends them."""
    from clipping.aistory import defaults, media_policy, workflow

    story_id = store.create(language="fr", generation_profile=defaults.manual_speech_generation_profile(),
                            now=NOW)["story_id"]
    story = workflow.patch_story(store, story_id, {"generation_profile": {"images": "manual", "speech_model": "lite"}},
                                 now=NOW)
    assert story["generation_profile"]["images"] == "manual" and media_policy.images_manual(story)
    assert story["generation_profile"]["speech_model"] == "lite"
    story = workflow.patch_story(store, story_id, {"generation_profile": {"images": None}}, now=NOW)
    assert "images" not in story["generation_profile"] and not media_policy.images_manual(story)
    with pytest.raises(workflow.WorkflowError):
        workflow.patch_story(store, story_id, {"generation_profile": {"images": "robot"}}, now=NOW)
