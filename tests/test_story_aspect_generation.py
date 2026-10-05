"""The story's frame through generation (plan 23 stage B7): every 9:16 byte
as it was, a 16:9 or 1:1 story's images, clips, uploads, cover and pack at
its own frame.

The 9:16 pins below were captured from ``main`` (e464b70) BEFORE any line of
this stage existed (a script calling each adapter's body builder and the
cache key on the requests of ``_request``): the Veo 3.1 lite body is RC-N1's
own sha (``test_video_adapters.LITE_BODY_SHA``), the others pin seedance,
LTX-2.3, LTX-2.5, kling and the speaking Veo links the same way. A 9:16
story's request never carries ``extra["aspect"]``; one that does (``"9:16"``)
builds the same body and the same key.

Fake transports and fake ffmpeg only: nothing leaves the machine.
Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_episode_steps as eps
import test_story_manual_link as tml
import test_story_metadata_step as tms
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_clip_estimate import timings_path  # noqa: F401 - the measured clip timings under tmp_path
from test_story_render_step import built  # noqa: F401 - the session's episode copies

from clipping.aistory import media_policy, platforms, prompting, refimages, schemas
from clipping.aistory.render import filtergraph, profiles
from clipping.providers import gencache, local_comfyui, video
from clipping.providers.generation import VIDEO, GenRequest
from clipping.providers.registry import Link

NOW = tas.NOW
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

# Captured from main e464b70 before this stage (see the module docstring).
PINS_916 = {
    "seedance_720": "d0fe85ef4d084f65340c78dd0bb7f9e62928575940122ff9e65aae1c83e5dd40",
    "seedance_1080": "cf901c3c427b2e4f08c19f8dcbeeebfab8e4ac81b1655b186c90a49e4b2a8157",
    "ltx23": "fe1350dfb896e8d0a0af69840cf01f3db1b75c5a54b6127992774b7e222f9cd0",
    "ltx23_audio": "b0dfe2ca269a6ad1469f8f1f7c642857ab63cd9cda2b5540ffcacb0dd600a212",
    "ltx25_720": "99645b804ce01c62b9a58563bff0f94f822a42edd4cc942fcd8ca0aabc464488",
    "ltx25_1080_audio": "1fd3711c03b06e086c46f90354873bc0b035f7bbc673be7c3b984e0396b9d6ec",
    "kling": "e44e2a6750d924974046c34699e1804bdd4b85b6f506245a4cb6dc004f621e8b",
    "kling_noneg": "2bc11345d84678ac7043c758533e01df27162ae70403c0c3f537455ec4cadc7d",
    "veo_lite": "acf87404a1a44278607585101b97a1262cc86ad594eb83f010f9cfc7ba08a521",  # RC-N1
    "veo_fast_720": "c29ae15043d005ead8b7eb79d6addaae16a39aae97348af218346f644080a407",
    "veo_premium_1080": "3373b2d35bf508414ed55b2a7f6ff17a0a99d7a6ba9f28d2f08d741b70020f9e",
}
KEYS_916 = {
    "key_720": "796eb1d037ecb183216d6bf77c120b544da4e5e09b1b02d1d87534a7ba9dc512",
    "key_1080": "f0c0424763a1b59b72bbf064bc0e4e785779636f2b27cd69a24ee69a206a05bf",
}
FAL_CASES = {
    "seedance_720": ("fal/seedance-1-pro-fast", {"duration_s": 5}),
    "seedance_1080": ("fal/seedance-1-pro-fast", {"duration_s": 5, "extra": {"resolution": "1080p"}}),
    "ltx23": ("fal/ltx-2.3-fast", {"duration_s": 6}),
    "ltx23_audio": ("fal/ltx-2.3-fast", {"duration_s": 6, "native_audio": True}),
    "ltx25_720": ("fal/ltx-2.5-fast", {"duration_s": 6}),
    "ltx25_1080_audio": ("fal/ltx-2.5-fast", {"duration_s": 8, "native_audio": True,
                                              "extra": {"resolution": "1080p"}}),
    "kling": ("fal/kling-2.5-turbo-std", {"duration_s": 5}),
    "kling_noneg": ("fal/kling-2.5-turbo-std", {"duration_s": 10, "negative": None}),
}
VEO_CASES = {
    "veo_lite": ("gemini/veo-3.1-lite", {"duration_s": 4}),
    "veo_fast_720": ("gemini/veo-3.1-fast", {"duration_s": 6}),
    "veo_premium_1080": ("gemini/veo-3.1", {"duration_s": 8, "extra": {"resolution": "1080p"}}),
}


@pytest.fixture
def keyframe(tmp_path):
    path = tmp_path / "shot_01.png"
    path.write_bytes(PNG)
    return str(path)


def _request(keyframe, **kw):
    kw.setdefault("prompt", "the kiwi turns to the camera, slow push in")
    kw.setdefault("negative", "morphing, flicker")
    kw.setdefault("seed", 11)
    kw.setdefault("duration_s", 5)
    kw.setdefault("fps", 24)
    kw.setdefault("native_audio", False)
    kw.setdefault("references", (keyframe,))
    kw.setdefault("out_dir", str(pathlib.Path(keyframe).parent))
    kw.setdefault("width", 720)
    kw.setdefault("height", 1280)
    return GenRequest(kind=VIDEO, **kw)


def _link(spec):
    provider, model = spec.split("/", 1)
    return Link(provider, model)


def _sha(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _with_aspect(kw, aspect):
    return dict(kw, extra=dict(kw.get("extra") or {}, aspect=aspect))


def _body(name, keyframe, kw):
    if name in FAL_CASES:
        spec, _ = FAL_CASES[name]
        return video.FAL_VIDEO._inputs(_link(spec), _request(keyframe, **kw), 11)
    spec, _ = VEO_CASES[name]
    return video.VEO._body(_request(keyframe, **kw), int(kw["duration_s"]), _link(spec))


# ================================================================ 9:16 byte for byte (pinned first)

@pytest.mark.parametrize("name", sorted(PINS_916))
def test_every_9_16_body_is_the_one_main_sent(keyframe, name):
    """RC-N1 and its siblings: a 9:16 request -- no aspect, or ``"9:16"``
    said -- builds the very body main built, byte for byte."""
    kw = (FAL_CASES.get(name) or VEO_CASES[name])[1]
    assert _sha(_body(name, keyframe, kw)) == PINS_916[name]
    assert _sha(_body(name, keyframe, _with_aspect(kw, "9:16"))) == PINS_916[name]


def test_rc_n1_pin_is_the_lite_body_sha_of_the_adapter_tests():
    import test_video_adapters as tva

    assert PINS_916["veo_lite"] == tva.LITE_BODY_SHA


def test_the_cache_key_of_a_9_16_clip_is_unchanged(keyframe):
    """The resolution pattern (DEC-227): the frame joins the key only when it
    is not 9:16, so every key a stored clip is kept under stays."""
    plain = _request(keyframe)
    hd = _request(keyframe, extra={"resolution": "1080p", "name": "shot_01"})
    assert gencache.request_key(VIDEO, "fal/seedance-1-pro-fast", plain) == KEYS_916["key_720"]
    assert gencache.request_key(VIDEO, "fal/seedance-1-pro-fast", hd) == KEYS_916["key_1080"]
    said = _request(keyframe, extra={"aspect": "9:16"})
    assert gencache.request_key(VIDEO, "fal/seedance-1-pro-fast", said) == KEYS_916["key_720"]
    assert "aspect" not in gencache.key_payload(VIDEO, "fal/seedance-1-pro-fast", said)
    wide = _request(keyframe, extra={"aspect": "16:9"})
    assert gencache.key_payload(VIDEO, "fal/seedance-1-pro-fast", wide)["aspect"] == "16:9"
    assert gencache.request_key(VIDEO, "fal/seedance-1-pro-fast", wide) != KEYS_916["key_720"]


# ================================================================ 16:9 and 1:1 bodies

def test_a_16_9_clip_says_its_frame_to_every_link_that_has_a_field(keyframe):
    for name in ("seedance_720", "ltx23", "ltx25_720"):
        body = _body(name, keyframe, _with_aspect(FAL_CASES[name][1], "16:9"))
        plain = _body(name, keyframe, FAL_CASES[name][1])
        assert body["aspect_ratio"] == "16:9" and {**body, "aspect_ratio": "9:16"} == plain
    for name in ("veo_lite", "veo_fast_720"):
        body = _body(name, keyframe, _with_aspect(VEO_CASES[name][1], "16:9"))
        plain = _body(name, keyframe, VEO_CASES[name][1])
        assert body["parameters"]["aspectRatio"] == "16:9"
        assert {**body, "parameters": {**body["parameters"], "aspectRatio": "9:16"}} == plain
    # kling has no field: its clip follows the keyframe, the body is the 9:16 one
    assert _sha(_body("kling", keyframe, _with_aspect(FAL_CASES["kling"][1], "16:9"))) == PINS_916["kling"]


def test_a_1_1_clip_is_sold_by_seedance_and_kling_and_refused_before_sending_elsewhere(keyframe):
    square = _body("seedance_720", keyframe, _with_aspect(FAL_CASES["seedance_720"][1], "1:1"))
    assert square["aspect_ratio"] == "1:1"
    assert _sha(_body("kling", keyframe, _with_aspect(FAL_CASES["kling"][1], "1:1"))) == PINS_916["kling"]
    for spec, kw in (("fal/ltx-2.3-fast", {"duration_s": 6}), ("fal/ltx-2.5-fast", {"duration_s": 6}),
                     ("gemini/veo-3.1-lite", {"duration_s": 4}), ("gemini/veo-3.1-fast", {"duration_s": 4})):
        with pytest.raises(ValueError, match="cannot make 1:1 clips"):
            video.clip_seconds(_link(spec), _request(keyframe, **_with_aspect(kw, "1:1")))


def test_a_local_comfyui_clip_is_9_16_only(tmp_path, keyframe):
    request = _request(keyframe, duration_s=5, extra={"template": local_comfyui.VIDEO_TEMPLATES[0], "aspect": "16:9"})
    with pytest.raises(ValueError, match="renders 9:16 clips only"):
        local_comfyui.COMFYUI_VIDEO.plan(request)


# ================================================================ prompts

def test_the_plate_prompts_say_the_story_s_frame_and_9_16_is_today_s_text():
    from clipping.aistory import templates

    lock = templates.load_style("fruit_drama")  # a template reads as a lock (test_aistory_prompting)
    base = prompting.master_plate_prompt(lock, place_descriptor="a market", time_variant="night")
    assert "Vertical 9:16, horizon" in base
    assert prompting.master_plate_prompt(lock, place_descriptor="a market", time_variant="night", aspect="9:16") == base
    wide = prompting.master_plate_prompt(lock, place_descriptor="a market", time_variant="night", aspect="16:9")
    assert wide == base.replace("Vertical 9:16", "Landscape 16:9")
    v2 = prompting.plate_prompt_v2(lock, place_text="a market with stalls", variant="day")
    assert ". Vertical 9:16. " in v2
    assert prompting.plate_prompt_v2(lock, place_text="a market with stalls", variant="day", aspect="9:16") == v2
    assert prompting.plate_prompt_v2(lock, place_text="a market with stalls", variant="day",
                                     aspect="1:1") == v2.replace("Vertical 9:16", "Square 1:1")
    shot = dict(subjects_block="A kiwi", action="waves", place_block="a market", time_variant="day",
                framing="medium_single")
    legacy = prompting.shot_prompt(lock, **shot)
    assert "Vertical 9:16 composition" in legacy and prompting.shot_prompt(lock, aspect="9:16", **shot) == legacy
    assert "Landscape 16:9 composition" in prompting.shot_prompt(lock, aspect="16:9", **shot)
    # the sheets stay 9:16: references, never the output
    portrait = prompting.portrait_prompt_v2(lock, look_text="a kiwi", signature_items=["a hat"])
    assert portrait.endswith(prompting.CONSTRAINTS_ONE_CHARACTER) and "Vertical 9:16" in portrait
    with pytest.raises(ValueError):
        prompting._frame_phrase("4:3")


def test_a_16_9_story_s_plate_prompt_and_size_follow_its_frame(store):
    story_id = tml.manual_story(store)
    story = store.get(story_id)
    assert refimages.plate_size(story) == refimages.PLATE_SIZE == (720, 1280)
    assert refimages._frame_kwargs(story) == {}
    wide = dict(story, generation_profile=dict(story["generation_profile"], aspect="16:9"))
    square = dict(story, generation_profile=dict(story["generation_profile"], aspect="1:1"))
    assert refimages.plate_size(wide) == (1280, 720) and refimages.plate_size(square) == (1024, 1024)
    assert refimages._frame_kwargs(wide) == {"aspect": "16:9"}
    # the sheets and the props keep their sizes in every frame
    assert refimages.character_size(wide, "portrait") == refimages.character_size(story, "portrait")


# ================================================================ shot images and clips

def _seedance_story(store, tmp_path, aspect=None):
    story_id = tas._episode(store, tmp_path)
    tce._tier(store, story_id, budget_profile="quality")

    def mutate(doc):
        doc["generation_profile"].update(pipeline="v2")
        if aspect:
            doc["generation_profile"]["aspect"] = aspect
    store.update(story_id, mutate, now=NOW)
    return story_id


def _clip(store, story_id, tmp_path):
    from clipping.aistory.steps import assets, clips

    ec = tas._ec(store, story_id)
    script, board = eps._script(store, story_id), tas._board(store, story_id)
    shot = dict(board["shots"][0])
    shot["assets"] = dict(shot["assets"], image="assets/shots/shot_01.png")
    keyframe = tas.Path(store.episode_asset_path(story_id, 1, "shots", "shot_01.png", create=True))
    keyframe.parent.mkdir(parents=True, exist_ok=True)
    keyframe.write_bytes(PNG)
    return assets.clip_request(ec, shot, script, link=tce.SEEDANCE, template=None, clip_s=5, seed=7, note=None,
                               flags=clips.shot_flags(shot, None), tier=2, out_dir=str(tmp_path))


def test_a_9_16_story_s_shot_request_is_the_one_it_always_was(store, tmp_path):
    from clipping.aistory.steps import assets

    story_id = _seedance_story(store, tmp_path)
    ec = tas._ec(store, story_id)
    assert assets.shot_size(ec.story) == assets.SHOT_SIZE
    _parts, request = _clip(store, story_id, tmp_path)
    assert "aspect" not in request.extra and (request.width, request.height) == assets.SHOT_SIZE
    shot = tas._board(store, story_id)["shots"][0]
    parts = assets.request_parts(ec, shot, note=None)
    assert parts["size"] == assets.SHOT_SIZE


def test_a_16_9_story_asks_its_shots_and_clips_at_its_frame(store, tmp_path):
    from clipping.aistory.steps import assets

    story_id = _seedance_story(store, tmp_path, aspect="16:9")
    ec = tas._ec(store, story_id)
    assert assets.shot_size(ec.story) == (1280, 720)
    _parts, request = _clip(store, story_id, tmp_path)
    assert request.extra["aspect"] == "16:9" and (request.width, request.height) == (1280, 720)
    assert video.FAL_VIDEO._inputs(_link(tce.SEEDANCE), request, 7)["aspect_ratio"] == "16:9"
    shot = tas._board(store, story_id)["shots"][0]
    parts = assets.request_parts(ec, shot, note=None)
    assert parts["size"] == (1280, 720)
    base = assets.request_parts(tas._ec(store, _seedance_story(store, tmp_path / "b")), shot, note=None)
    assert parts["hash"] != base["hash"]


def test_the_keyframe_crop_follows_the_frame(tmp_path):
    assert media_policy.keyframe_crop((1024, 1024)) == (576, 1024)
    assert media_policy.keyframe_crop((720, 1280)) is None
    assert media_policy.keyframe_crop((1280, 720), "16:9") is None
    assert media_policy.keyframe_crop((1300, 720), "16:9") == (1280, 720)
    assert media_policy.keyframe_crop((1024, 1024), "1:1") is None
    assert media_policy.keyframe_crop((1023, 1001), "1:1") == (1000, 1000)
    assert media_policy.keyframe_crop((1920, 1200), "16:9") == (1920, 1080)


def test_a_16_9_keyframe_is_cropped_to_16_9(tmp_path, monkeypatch):
    from clipping.aistory.steps import assets

    produced = tmp_path / "made.png"
    produced.write_bytes(PNG)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        pathlib.Path(argv[-1]).write_bytes(PNG)
        return type("Done", (), {"returncode": 0, "stderr": ""})()

    story = {"generation_profile": {"pipeline": "v2", "aspect": "16:9"}}
    monkeypatch.setattr(assets.imagesize, "image_size", lambda path: (1300, 720))
    out = assets.v2_keyframe_source(story, str(produced), out_dir=str(tmp_path), run=fake_run)
    assert out.endswith("keyframe-16x9.png") and "crop=1280:720" in calls[0]


# ================================================================ manual mode

def _info(width, height, **kw):
    return {"video": True, "audio": True, "width": width, "height": height, "duration_s": 8.0, "mp4": True, **kw}


def test_an_upload_is_checked_against_the_story_s_frame_within_2_percent():
    from clipping.aistory import manual_uploads

    assert manual_uploads.clip_refusal(_info(720, 1280), speaks=True) is None
    assert "not 9:16" in manual_uploads.clip_refusal(_info(1280, 720), speaks=True)
    assert manual_uploads.clip_refusal(_info(1280, 720), speaks=True, aspect="16:9") is None
    assert manual_uploads.clip_refusal(_info(1290, 720), speaks=True, aspect="16:9") is None  # within 2 %
    refusal = manual_uploads.clip_refusal(_info(720, 1280), speaks=True, aspect="16:9")
    assert "not 16:9" in refusal and "Pick 16:9 on the platform" in refusal
    assert manual_uploads.clip_refusal(_info(1080, 1080), speaks=False, aspect="1:1") is None
    assert "not 1:1" in manual_uploads.clip_refusal(_info(1280, 720), speaks=False, aspect="1:1")


def test_the_brief_says_the_story_s_frame_and_the_presets_list_their_frames(store):
    from clipping.aistory.steps import brief

    story_id = tml.manual_story(store)
    tml._planted(store, story_id)
    portrait = tml._brief(store, story_id, "flow")
    entry = portrait["shots"][0]
    assert entry["aspect"] == "9:16" and entry["checks"][-1].startswith("9:16, at least ")
    assert "→ set 9:16 →" in portrait["platform"]["where_to_paste"]
    store.update(story_id, lambda doc: doc["generation_profile"].update(aspect="16:9"), now=NOW)
    wide = tml._brief(store, story_id, "flow")
    assert wide["shots"][0]["aspect"] == "16:9" and wide["shots"][0]["checks"][-1].startswith("16:9, at least ")
    assert "→ set 16:9 →" in wide["platform"]["where_to_paste"]
    assert brief.image_size("keyframe", "16:9") == (1920, 1080) and brief.image_size("plate", "1:1") == (1024, 1024)
    assert brief.image_size("portrait", "16:9") == brief.IMAGE_SIZES["portrait"]
    assert brief.image_min_size("keyframe", "16:9") == (640, 360) and brief.image_min_size("keyframe") == (360, 640)
    for name in platforms.PLATFORMS:
        preset = platforms.load(name)
        assert preset["aspects"] == ["9:16", "16:9"] and "{aspect}" in preset["where_to_paste"]
    flow = platforms.load("flow")
    assert platforms.preset_errors(dict(flow, aspects=["16:9"]))
    assert platforms.preset_errors(dict(flow, aspects=["9:16", "4:3"]))
    assert platforms.preset_errors(dict(flow, aspects="9:16"))


def test_a_square_story_is_never_briefed_on_a_platform_that_cannot_make_it(store):
    story_id = tml.manual_story(store)
    tml._planted(store, story_id)
    store.update(story_id, lambda doc: doc["generation_profile"].update(aspect="1:1"), now=NOW)
    with pytest.raises(eps.steps.StepFailed, match="not this story's 1:1"):
        tml._brief(store, story_id, "flow")


# ================================================================ render inputs, the cover, the pack

def test_the_render_gets_the_frame_only_when_it_is_not_9_16(store, tmp_path):
    from clipping.aistory.steps import render

    story_id = _seedance_story(store, tmp_path)
    ec = tas._ec(store, story_id)
    args = render.plan_args(ec, eps._script(store, story_id), tas._board(store, story_id), None, {},
                            subtitles="word_pop", encoder="libx264")
    assert "aspect" not in args
    store.update(story_id, lambda doc: doc["generation_profile"].update(aspect="1:1"), now=NOW)
    ec = tas._ec(store, story_id)
    args = render.plan_args(ec, eps._script(store, story_id), tas._board(store, story_id), None, {},
                            subtitles="word_pop", encoder="libx264")
    assert args["aspect"] == "1:1"


@pytest.mark.parametrize("aspect", [None, "9:16", "16:9", "1:1"])
def test_the_encoder_probe_asks_the_short_side(aspect):
    from types import SimpleNamespace

    from clipping.aistory.steps import render

    asked = []

    def detect(_unused, *, target_h):
        asked.append(target_h)
        return {"name": "libx264", "args": []}

    render.detect_encoder(SimpleNamespace(on_log=lambda line: None), detect=detect, aspect=aspect)
    assert asked == [1080]


def test_a_16_9_episode_s_cover_is_landscape_and_its_pack_says_where_it_goes(store, tmp_path, built):
    from clipping.aistory.steps import metadata

    story_id = tms.rendered(store, tmp_path, built)
    store.update(story_id, lambda doc: doc["generation_profile"].update(aspect="16:9"), now=NOW)
    _summary, _log, _runner, cover = tms.run_step(store, story_id, tmp_path=tmp_path)
    argv = cover.calls[0]["argv"]
    image_rel = argv[argv.index("-i") + 1]
    assert argv == filtergraph.cover_argv(image_rel, metadata.COVER_ASS_REL, "fonts", metadata.COVER_PART_REL,
                                          geometry=profiles.LANDSCAPE)
    assert argv != filtergraph.cover_argv(image_rel, metadata.COVER_ASS_REL, "fonts", metadata.COVER_PART_REL)
    ass = (pathlib.Path(cover.calls[0]["cwd"]) / metadata.COVER_ASS_REL).read_text(encoding="utf-8")
    assert "PlayResX: 1920" in ass and "PlayResY: 1080" in ass
    doc = tms.pack(store, story_id)
    assert doc["aspect"] == "16:9" and "not Shorts" in doc["aspect_note"]
    assert schemas.metadata_pack_errors(doc) == []


def test_a_9_16_pack_and_cover_are_the_ones_they_always_were(store, tmp_path, built):
    from clipping.aistory.steps import metadata

    story_id = tms.rendered(store, tmp_path, built)
    _summary, _log, _runner, cover = tms.run_step(store, story_id, tmp_path=tmp_path)
    argv = cover.calls[0]["argv"]
    image_rel = argv[argv.index("-i") + 1]
    assert argv == filtergraph.cover_argv(image_rel, metadata.COVER_ASS_REL, "fonts", metadata.COVER_PART_REL)
    doc = tms.pack(store, story_id)
    assert "aspect" not in doc and "aspect_note" not in doc
