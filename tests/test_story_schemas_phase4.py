"""Phase-4 documents (AI Story phase 4, stage 3; spec 2.8-2.10, DEC-155).

``clipping/aistory/schemas.py`` gains, for the assets, render and metadata
steps:

- optional keys on a storyboard shot's ``assets`` object (``model``,
  ``consistency``, ``route``, ``prompt_hash``, ``locked``, ``note``,
  ``generated_at``, ``est_usd``, ``cache_key``, ``pending``). The five keys of
  spec 2.8 stay required and the object stays closed, so a phase-3 board
  validates unchanged (RC-A8) while an unknown key still fails;
- ``episode_assets_v1`` (word sources, SFX, BGM, the grid approval),
  ``render_manifest_v1`` (inputs, stages, output) and ``metadata_pack_v1``
  (per platform), each with an ``*_errors`` validator whose every error
  names the rule it broke.

Stdlib + pytest only (DEC-012). No file is touched.
"""

from __future__ import annotations

import copy

import pytest

from clipping.aistory import schemas

NOW = "2026-09-28T10:00:00+00:00"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _mutate(doc, mutate):
    doc = copy.deepcopy(doc)
    mutate(doc)
    return doc


def _has(errors, *keywords):
    return any(all(keyword in error for keyword in keywords) for error in errors)


# ================================================================ closed lists

def test_the_phase4_closed_lists():
    assert schemas.PLATFORMS == ("tiktok", "shorts", "reels")
    assert schemas.IMAGE_ROUTES == ("free", "local", "paid")
    assert schemas.WORD_SOURCES == ("provider", "alignment", "even_split")
    assert schemas.SFX_STATES == ("resolved", "missing")
    assert schemas.RENDER_STAGE_STATES == ("running", "done", "failed", "cancelled", "cached")
    assert set(schemas.RENDER_CACHED_KINDS) <= set(schemas.RENDER_STAGE_KINDS)


def test_the_platform_list_lives_in_one_place():
    audience = schemas.STORY_BIBLE_SCHEMA["properties"]["audience"]
    assert audience["properties"]["platforms"]["items"]["enum"] == list(schemas.PLATFORMS)
    b3 = schemas.B3_SCHEMA["properties"]["audience"]["properties"]["platforms"]["items"]
    assert b3["enum"] == list(schemas.PLATFORMS)


def test_the_new_documents_share_the_episode_bounds():
    for schema in (schemas.EPISODE_ASSETS_SCHEMA, schemas.RENDER_MANIFEST_SCHEMA, schemas.METADATA_PACK_SCHEMA):
        bounds = schema["properties"]["ep"]
        script = schemas.EPISODE_SCRIPT_SCHEMA["properties"]["ep"]
        assert (bounds["minimum"], bounds["maximum"]) == (script["minimum"], script["maximum"])
        assert {"created_at", "updated_at"} <= set(schema["required"])
        assert schema["additionalProperties"] is False


# ============================================================ storyboard shot assets

PHASE3_ASSETS = {"image": None, "video": None, "seed": None, "provider": None, "approved": False}


def _shot(n, scene_id, line_id, assets=None):
    return {
        "shot_id": f"sh{n:02d}", "scene_id": scene_id, "order": n,
        "framing": "medium_single", "camera_motion": "hold", "modifiers": [],
        "subject_tags": ["@char_kiwilo", "#place_beach_camp:day"], "action": "Something happens on screen.",
        "lines": [line_id], "image_prompt": "a fully resolved prompt", "negative_prompt": "no text",
        "prompt_override": None, "reference_images": ["characters/char_kiwilo/refs/portrait.png"],
        "consistency": "prompt_only", "duration_s": 3.0, "keep_still": False,
        "motion": {"type": "hold", "zoom_from": 1.0, "zoom_to": 1.0, "pan": "none"},
        "video_prompt": None,
        "assets": copy.deepcopy(assets if assets is not None else PHASE3_ASSETS),
    }


def _board(assets=None):
    return {
        "$schema": "storyboard_v1", "ep": 1,
        "shots": [_shot(1, "s01", "l04", assets), _shot(2, "s02", "l08")],
        "transitions": [{"after": "sh01", "type": "cut", "duration_s": 0.0}],
        "scenes": {sid: {"source": "t1", "script_rev": 1, "stale": False} for sid in ("s01", "s02")},
        "resolved_from": {}, "approved_at": None, "rev": 1,
        "created_at": NOW, "updated_at": NOW,
    }


PHASE4_ASSETS = {
    "image": "assets/shots/shot_01.png", "video": None, "seed": 424242,
    "provider": "cloudflare", "approved": False,
    "model": "flux-1-schnell", "consistency": "prompt_only", "route": "free", "prompt_hash": SHA_A,
    "locked": False, "note": None, "generated_at": NOW, "est_usd": 0.0, "cache_key": SHA_B,
    "pending": None,
}


def test_the_five_spec_keys_stay_required_and_nothing_else_is():
    schema = schemas.STORYBOARD_SCHEMA["properties"]["shots"]["items"]["properties"]["assets"]
    assert schema["required"] == ["image", "video", "seed", "provider", "approved"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(PHASE4_ASSETS)


def test_a_phase3_board_still_validates():
    assert schemas.storyboard_errors(_board()) == []


def test_a_board_with_every_new_shot_key_validates():
    assert schemas.storyboard_errors(_board(PHASE4_ASSETS)) == []


@pytest.mark.parametrize("key", sorted(set(PHASE4_ASSETS) - set(PHASE3_ASSETS)))
def test_each_new_shot_key_is_optional_on_its_own(key):
    assets = {**PHASE3_ASSETS, key: PHASE4_ASSETS[key]}
    assert schemas.storyboard_errors(_board(assets)) == []


def test_a_pending_regenerate_validates():
    pending = {"seed": 7, "note": "make it darker", "requested_at": NOW}
    assert schemas.storyboard_errors(_board({**PHASE4_ASSETS, "pending": pending})) == []
    pending_without_note = {"seed": 0, "note": None, "requested_at": NOW}
    assert schemas.storyboard_errors(_board({**PHASE4_ASSETS, "pending": pending_without_note})) == []


@pytest.mark.parametrize("image", ["assets/shots/shot_01.jpg", "assets/shots/shot_01.jpeg",
                                   "assets/shots/shot_01.webp"])
def test_a_shot_image_in_any_accepted_format(image):
    assert schemas.storyboard_errors(_board({**PHASE4_ASSETS, "image": image})) == []


SHOT_ASSET_BREAKS = {
    "unknown key": ({"style": "noir"}, ("$.shots[0].assets.style", "additional property not allowed")),
    "route not in the list": ({"route": "api"}, ("$.shots[0].assets.route", "is not one of")),
    "route null": ({"route": None}, ("$.shots[0].assets.route", "expected type string")),
    "consistency base": ({"consistency": "base"}, ("$.shots[0].assets.consistency", "is not one of")),
    "prompt_hash short": ({"prompt_hash": "abc"}, ("$.shots[0].assets.prompt_hash", "does not match")),
    "cache_key upper": ({"cache_key": "A" * 64}, ("$.shots[0].assets.cache_key", "does not match")),
    "locked not bool": ({"locked": "yes"}, ("$.shots[0].assets.locked", "expected type boolean")),
    "est_usd negative": ({"est_usd": -0.01}, ("$.shots[0].assets.est_usd", "< minimum 0")),
    "note too long": ({"note": "x" * 301}, ("$.shots[0].assets.note", "maxLength 300")),
    "pending a string": ({"pending": "7"}, ("$.shots[0].assets.pending", "expected type object/null")),
    "pending without seed": ({"pending": {"note": None, "requested_at": NOW}},
                             ("$.shots[0].assets.pending.seed", "required property missing")),
    "pending seed null": ({"pending": {"seed": None, "note": None, "requested_at": NOW}},
                          ("$.shots[0].assets.pending.seed", "expected type integer")),
    "pending seed negative": ({"pending": {"seed": -1, "note": None, "requested_at": NOW}},
                              ("$.shots[0].assets.pending.seed", "< minimum 0")),
    "pending extra key": ({"pending": {"seed": 1, "note": None, "requested_at": NOW, "route": "free"}},
                          ("$.shots[0].assets.pending.route", "additional property not allowed")),
    "pending without requested_at": ({"pending": {"seed": 1, "note": None}},
                                     ("$.shots[0].assets.pending.requested_at", "required property missing")),
    "image of another shot": ({"image": "assets/shots/shot_02.png"},
                              ("$.shots[0].assets.image", "is not sh01's image")),
    "image outside shots/": ({"image": "assets/voice/shot_01.png"},
                             ("$.shots[0].assets.image", "is not sh01's image")),
    "image traversal": ({"image": "../shot_01.png"}, ("$.shots[0].assets.image", "is not sh01's image")),
    "image gif": ({"image": "assets/shots/shot_01.gif"}, ("$.shots[0].assets.image", "is not sh01's image")),
    "image one digit": ({"image": "assets/shots/shot_1.png"}, ("$.shots[0].assets.image", "is not sh01's image")),
}


@pytest.mark.parametrize("label", list(SHOT_ASSET_BREAKS), ids=list(SHOT_ASSET_BREAKS))
def test_a_broken_shot_asset_record_names_its_rule(label):
    change, keywords = SHOT_ASSET_BREAKS[label]
    errors = schemas.storyboard_errors(_board({**PHASE4_ASSETS, **change}))
    assert _has(errors, *keywords), errors


@pytest.mark.parametrize("key", list(PHASE3_ASSETS))
def test_a_spec_key_is_still_required(key):
    assets = {k: v for k, v in PHASE4_ASSETS.items() if k != key}
    errors = schemas.storyboard_errors(_board(assets))
    assert _has(errors, f"$.shots[0].assets.{key}", "required property missing"), errors


# ================================================================ episode_assets_v1

def _assets(**changes):
    doc = {
        "$schema": "episode_assets_v1", "ep": 1,
        "lines": {
            "l04": {"words_source": "provider"},
            "l08": {"words_source": "alignment", "aligned_by": "groq/whisper-large-v3"},
            "l09": {"words_source": "even_split"},
        },
        "sfx": [
            {"scene_id": "s01", "at": "start", "cue": "waves_soft", "pack": "soap",
             "file": "assets/sfx/soap/waves_soft.wav", "offset_s": 0.0, "state": "resolved"},
            {"scene_id": "s02", "at": "l08", "cue": "gasp_crowd", "pack": "soap",
             "file": None, "offset_s": 6.4, "state": "missing"},
        ],
        "bgm": {
            "mood": "telenovela_tension", "dominant_emotion": "tension",
            "weights_s": {"tension": 31.5, "shocked": 12.0, "neutral": 4.0},
            "file": "assets/bgm/drama/track.mp3", "sha256": SHA_C,
            "licence": "shipped with Clips, source unrecorded",
        },
        "approved": {"at": NOW, "fingerprint": SHA_A},
        "created_at": NOW, "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def test_an_episode_assets_document_validates():
    assert schemas.episode_assets_errors(_assets()) == []


def test_an_unresolved_or_trackless_bgm_and_no_approval_validate():
    assert schemas.episode_assets_errors(_assets(bgm=None, approved=None, sfx=[], lines={})) == []
    trackless = _mutate(_assets(), lambda d: d["bgm"].update(file=None, sha256=None, licence=None))
    assert schemas.episode_assets_errors(trackless) == []


def test_a_tie_for_the_dominant_emotion_is_accepted():
    tie = _mutate(_assets(), lambda d: d["bgm"]["weights_s"].update(shocked=31.5))
    assert schemas.episode_assets_errors(tie) == []


EPISODE_ASSETS_BREAKS = {
    "extra top-level key": (lambda d: d.update(images=[]), ("$.images", "additional property not allowed")),
    "wrong $schema": (lambda d: d.update({"$schema": "assets_v1"}), ("$.$schema", "does not equal const")),
    "line key not a line id": (lambda d: d["lines"].update(line_4={"words_source": "provider"}),
                               ("$.lines", "'line_4' is not a line id")),
    "unknown words source": (lambda d: d["lines"]["l04"].update(words_source="whisper"),
                             ("$.lines.l04.words_source", "is not one of")),
    "aligned_by without alignment": (lambda d: d["lines"]["l04"].update(aligned_by="groq/whisper"),
                                     ("$.lines.l04.aligned_by", "exactly when words_source is 'alignment'")),
    "alignment without aligned_by": (lambda d: d["lines"]["l08"].pop("aligned_by"),
                                     ("$.lines.l08.aligned_by", "exactly when words_source is 'alignment'")),
    "extra line key": (lambda d: d["lines"]["l04"].update(words=[]),
                       ("$.lines.l04.words", "additional property not allowed")),
    "sfx state unknown": (lambda d: d["sfx"][0].update(state="skipped"), ("$.sfx[0].state", "is not one of")),
    "resolved cue without file": (lambda d: d["sfx"][0].update(file=None),
                                  ("$.sfx[0].file", "a 'resolved' cue has a file")),
    "missing cue with a file": (lambda d: d["sfx"][1].update(file="assets/sfx/soap/gasp_crowd.wav"),
                                ("$.sfx[1].file", "a 'resolved' cue has a file")),
    "sfx pack unknown": (lambda d: d["sfx"][0].update(pack="horror"), ("$.sfx[0].pack", "is not one of")),
    "sfx at not start or a line": (lambda d: d["sfx"][0].update(at="end"), ("$.sfx[0].at", "does not match")),
    "sfx absolute file": (lambda d: d["sfx"][0].update(file="/etc/passwd"), ("$.sfx[0].file", "does not match")),
    "sfx negative offset": (lambda d: d["sfx"][0].update(offset_s=-1), ("$.sfx[0].offset_s", "< minimum 0")),
    "bgm weight key not an emotion": (lambda d: d["bgm"]["weights_s"].update(joy=1.0),
                                      ("$.bgm.weights_s", "'joy' is not an emotion")),
    "bgm negative weight": (lambda d: d["bgm"]["weights_s"].update(neutral=-2),
                            ("$.bgm.weights_s.neutral", "seconds >= 0")),
    "bgm dominant not the heaviest": (lambda d: d["bgm"].update(dominant_emotion="shocked"),
                                      ("$.bgm.dominant_emotion", "does not carry the largest weight")),
    "bgm dominant not an emotion": (lambda d: d["bgm"].update(dominant_emotion="joy"),
                                    ("$.bgm.dominant_emotion", "is not one of")),
    "bgm file without sha256": (lambda d: d["bgm"].update(sha256=None),
                                ("$.bgm", "file, sha256 and licence are recorded together")),
    "bgm traversal": (lambda d: d["bgm"].update(file="../../x.mp3"), ("$.bgm.file", "does not match")),
    "approval fingerprint not a sha256": (lambda d: d["approved"].update(fingerprint="abc"),
                                          ("$.approved.fingerprint", "does not match")),
    "approval without its fingerprint": (lambda d: d["approved"].pop("fingerprint"),
                                         ("$.approved.fingerprint", "required property missing")),
    "ep out of bounds": (lambda d: d.update(ep=100), ("$.ep", "> maximum 99")),
}


@pytest.mark.parametrize("label", list(EPISODE_ASSETS_BREAKS), ids=list(EPISODE_ASSETS_BREAKS))
def test_a_broken_episode_assets_document_names_its_rule(label):
    mutate, keywords = EPISODE_ASSETS_BREAKS[label]
    errors = schemas.episode_assets_errors(_mutate(_assets(), mutate))
    assert _has(errors, *keywords), errors


# ================================================================ render_manifest_v1

def _stage(sid, kind, state="done", *, cache_key=None, output=None):
    settled = state in ("done", "cached")
    return {
        "id": sid, "kind": kind, "argv": ["ffmpeg", "-y", "-i", "in/x.png", output or f"{sid}.out"],
        "cache_key": cache_key, "state": state,
        "output": (output or f"{sid}.out") if settled else None,
        "output_sha256": SHA_B if settled else None,
        "seconds": 1.5 if state != "running" else None,
        "stderr_tail": None,
    }


def _manifest(**changes):
    doc = {
        "$schema": "render_manifest_v1", "ep": 1, "profile": "final",
        "params": {"subtitles": "word_pop", "encoder": "libx264"},
        "ffmpeg": {"version": "6.1.1-3ubuntu5", "machine": "aarch64"},
        "font": {"family": "Montserrat", "file": "fonts/Montserrat-Black.ttf", "sha256": SHA_C,
                 "reason": "the template's family is not in custom_fonts/; the shipped Montserrat Black"},
        "inputs": [
            {"role": "shot", "id": "sh01", "source": "episodes/ep01/assets/shots/shot_01.png",
             "staged": f"in/{SHA_A}.png", "sha256": SHA_A},
            {"role": "bgm", "id": None, "source": "assets/bgm/drama/track.mp3",
             "staged": f"in/{SHA_C}.mp3", "sha256": SHA_C},
        ],
        "stages": [
            _stage("S:sh01", "shot", "cached", cache_key=SHA_A, output=f"cache/{SHA_A}.mp4"),
            _stage("S:sh02", "shot", cache_key=SHA_B, output=f"cache/{SHA_B}.mp4"),
            _stage("E", "end_card", cache_key=SHA_C, output=f"cache/{SHA_C}.mp4"),
            _stage("A", "audio_mix", output="mix.wav"),
            _stage("L1", "loudness_measure", output="loudness_1.json"),
            _stage("F", "final", output="episode_pre.mkv"),
            _stage("L2", "loudness_apply", output="episode_final.mp4"),
            _stage("P", "probe", output="probe.json"),
            _stage("M", "framemd5", output="episode_final.framemd5"),
        ],
        "output": {
            "path": "episode_final.mp4", "sha256": SHA_B, "duration_s": 61.2,
            "width": 1080, "height": 1920, "fps": "30/1",
            "loudness": {"i": -14.1, "tp": -1.2, "lra": 6.3},
            "framemd5": {"file": "render/episode_final.framemd5", "sha256": SHA_A},
        },
        "timings": {"started_at": NOW, "finished_at": NOW, "total_s": 212.4},
        "warnings": [],
        "created_at": NOW, "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def test_a_finished_render_manifest_validates():
    assert schemas.render_manifest_errors(_manifest()) == []


def test_a_manifest_written_before_its_first_command_validates():
    doc = _manifest(stages=[_stage("S:sh01", "shot", "running", cache_key=SHA_A)], output=None,
                    timings={"started_at": NOW, "finished_at": None, "total_s": None})
    assert schemas.render_manifest_errors(doc) == []


def test_a_failed_or_cancelled_render_keeps_its_stages():
    failed = _stage("F", "final", "failed")
    failed["stderr_tail"] = "Error while filtering: Invalid argument"
    cancelled = _stage("F", "final", "cancelled")
    for last in (failed, cancelled):
        doc = _manifest(stages=[_stage("A", "audio_mix"), last], output=None)
        assert schemas.render_manifest_errors(doc) == []


def test_a_render_without_text_needs_no_font():
    doc = _manifest(font=None, params={"subtitles": "none", "encoder": "auto"}, profile="golden")
    assert schemas.render_manifest_errors(doc) == []


RENDER_MANIFEST_BREAKS = {
    "unknown stage state": (lambda d: d["stages"][3].update(state="skipped"), ("$.stages[3].state", "is not one of")),
    "unknown stage kind": (lambda d: d["stages"][3].update(kind="upscale"), ("$.stages[3].kind", "is not one of")),
    "stage id listed twice": (lambda d: d["stages"][4].update(id="A"), ("$.stages[4].id", "'A' is listed twice")),
    "stage id with a space": (lambda d: d["stages"][4].update(id="L 1"), ("$.stages[4].id", "does not match")),
    "empty argv": (lambda d: d["stages"][3].update(argv=[]), ("$.stages[3].argv", "minItems 1")),
    "done without output sha": (lambda d: d["stages"][3].update(output_sha256=None),
                                ("$.stages[3]", "names its output and output_sha256")),
    "done without output": (lambda d: d["stages"][3].update(output=None),
                            ("$.stages[3]", "names its output and output_sha256")),
    "running with a result": (lambda d: d.update(output=None) or d["stages"][3].update(state="running"),
                              ("$.stages[3]", "a 'running' stage has no output_sha256 or seconds yet")),
    "cached final pass": (lambda d: d["stages"][5].update(state="cached", cache_key=SHA_A),
                          ("$.stages[5].state", "a 'final' stage is never cached")),
    "cached without a key": (lambda d: d["stages"][0].update(cache_key=None),
                             ("$.stages[0].cache_key", "names the key it was found under")),
    "output while a stage failed": (lambda d: d["stages"][5].update(state="failed", output=None,
                                                                    output_sha256=None),
                                    ("$.output", "['F'] are not done or cached")),
    "loudness not finite": (lambda d: d["output"]["loudness"].update(i=float("nan")),
                            ("$.output.loudness.i", "is not a finite number")),
    "loudness infinite": (lambda d: d["output"]["loudness"].update(tp=float("-inf")),
                          ("$.output.loudness.tp", "is not a finite number")),
    "fps not a ratio": (lambda d: d["output"].update(fps="30"), ("$.output.fps", "does not match")),
    "output absolute path": (lambda d: d["output"].update(path="/app/outputs/episode_final.mp4"),
                             ("$.output.path", "does not match")),
    "subtitles mode unknown": (lambda d: d["params"].update(subtitles="karaoke"),
                               ("$.params.subtitles", "is not one of")),
    "encoder unknown": (lambda d: d["params"].update(encoder="h264_nvenc"), ("$.params.encoder", "is not one of")),
    "profile unknown": (lambda d: d.update(profile="shot"), ("$.profile", "is not one of")),
    "input sha not hex": (lambda d: d["inputs"][0].update(sha256="xyz"), ("$.inputs[0].sha256", "does not match")),
    "input staged traversal": (lambda d: d["inputs"][0].update(staged="../../etc/passwd"),
                               ("$.inputs[0].staged", "does not match")),
    "input role unknown": (lambda d: d["inputs"][0].update(role="font"), ("$.inputs[0].role", "is not one of")),
    "ffmpeg without machine": (lambda d: d["ffmpeg"].pop("machine"), ("$.ffmpeg.machine", "required property")),
    "font without reason": (lambda d: d["font"].pop("reason"), ("$.font.reason", "required property missing")),
    "stderr tail too long": (lambda d: d["stages"][3].update(stderr_tail="x" * 4001),
                             ("$.stages[3].stderr_tail", "maxLength 4000")),
    "extra top-level key": (lambda d: d.update(fingerprint=SHA_A), ("$.fingerprint", "additional property")),
}


@pytest.mark.parametrize("label", list(RENDER_MANIFEST_BREAKS), ids=list(RENDER_MANIFEST_BREAKS))
def test_a_broken_render_manifest_names_its_rule(label):
    mutate, keywords = RENDER_MANIFEST_BREAKS[label]
    errors = schemas.render_manifest_errors(_mutate(_manifest(), mutate))
    assert _has(errors, *keywords), errors


# ================================================================ metadata_pack_v1

def _platform(*, english=True):
    entry = {
        "title": "Kiwilo a menti !", "description": "Mangella découvre tout. La suite demain ?",
        "hashtags": ["#fruitdrama", "#telenovela", "#kiwi"], "hook_text": "Il a menti.",
        "pinned_comment": "La suite demain ? PARTIE 2 →", "cover": "cover.jpg", "written_at": NOW,
    }
    if english:
        entry.update(title_en="Kiwilo lied!", hashtags_en=["#fruitdrama", "#soap", "#kiwi"])
    return entry


def _pack(language="fr", **changes):
    doc = {
        "$schema": "metadata_pack_v1", "ep": 1, "language": language, "script_rev": 4,
        "render_sha256": SHA_B,
        "platforms": {platform: _platform(english=language == "fr") for platform in schemas.PLATFORMS},
        "created_at": NOW, "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def test_a_french_metadata_pack_validates():
    assert schemas.metadata_pack_errors(_pack("fr")) == []


def test_an_english_metadata_pack_validates_without_english_fields():
    assert schemas.metadata_pack_errors(_pack("en")) == []


def test_a_pack_may_hold_some_platforms_only():
    doc = _mutate(_pack(), lambda d: d["platforms"].pop("reels"))
    assert schemas.metadata_pack_errors(doc) == []


METADATA_PACK_BREAKS = {
    "unknown platform": (lambda d: d["platforms"].update(youtube=_platform()),
                         ("$.platforms", "'youtube' is not one of")),
    "french without title_en": (lambda d: d["platforms"]["tiktok"].pop("title_en"),
                                ("$.platforms.tiktok.title_en", "required for a French story")),
    "french without hashtags_en": (lambda d: d["platforms"]["shorts"].pop("hashtags_en"),
                                   ("$.platforms.shorts.hashtags_en", "required for a French story")),
    "english with title_en": (lambda d: d.update(language="en") or d["platforms"]["reels"].pop("hashtags_en"),
                              ("$.platforms.reels.title_en", "only a French story carries English fields")),
    "too few hashtags": (lambda d: d["platforms"]["tiktok"].update(hashtags=["#a", "#b"]),
                         ("$.platforms.tiktok.hashtags", "< minItems 3")),
    "too many hashtags": (lambda d: d["platforms"]["tiktok"].update(hashtags=[f"#t{i}" for i in range(7)]),
                          ("$.platforms.tiktok.hashtags", "> maxItems 6")),
    "hashtag without #": (lambda d: d["platforms"]["tiktok"]["hashtags"].__setitem__(0, "fruitdrama"),
                          ("$.platforms.tiktok.hashtags[0]", "does not match")),
    "hashtag with a space": (lambda d: d["platforms"]["tiktok"]["hashtags"].__setitem__(0, "#fruit drama"),
                             ("$.platforms.tiktok.hashtags[0]", "does not match")),
    "hashtag listed twice": (lambda d: d["platforms"]["tiktok"].update(hashtags=["#a", "#b", "#a"]),
                             ("$.platforms.tiktok.hashtags", "'#a' is listed twice")),
    "empty title": (lambda d: d["platforms"]["tiktok"].update(title=""),
                    ("$.platforms.tiktok.title", "minLength 1")),
    "extra platform key": (lambda d: d["platforms"]["tiktok"].update(music="x"),
                           ("$.platforms.tiktok.music", "additional property not allowed")),
    "cover absolute": (lambda d: d["platforms"]["tiktok"].update(cover="/tmp/cover.jpg"),
                       ("$.platforms.tiktok.cover", "does not match")),
    "render sha not hex": (lambda d: d.update(render_sha256="nope"), ("$.render_sha256", "does not match")),
    "script rev zero": (lambda d: d.update(script_rev=0), ("$.script_rev", "< minimum 1")),
    "language unknown": (lambda d: d.update(language="de"), ("$.language", "is not one of")),
}


@pytest.mark.parametrize("label", list(METADATA_PACK_BREAKS), ids=list(METADATA_PACK_BREAKS))
def test_a_broken_metadata_pack_names_its_rule(label):
    mutate, keywords = METADATA_PACK_BREAKS[label]
    errors = schemas.metadata_pack_errors(_mutate(_pack(), mutate))
    assert _has(errors, *keywords), errors
