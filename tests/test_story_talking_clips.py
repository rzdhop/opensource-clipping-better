"""Talking clips (AI Story, plan 32 stage 8, DEC-315 §6): on the ``own_gpu``
budget profile a speaking shot's clip is made by ``runpod/s2v_wan22`` -- its
keyframe and its dialogue track, the mouth following the voice -- and every
other shot's by the episode's i2v link, as before. The human heard the first
S2V line live on 2026-10-07 ("c'est très bien pour le lipsync").

- **The policy** (``media_policy.talking_clips``, ``steps/talking.verdict``):
  the profile's key; a shot talks when it has lines, all one character's, its
  framing shows that character alone, and its speech ends inside one 4.8 s
  chunk -- each rule failing on its own.
- **The adapter** (``runpod_comfyui``): the keyframe and the wav travel inline
  as data URLs, to the voice lines' endpoint with that endpoint's key, billed
  at its rate; the clip's muxed sound is dropped before it is kept (the
  ambience exclusion: the render never mixes the dialogue twice).
- **The tables**: one length (5 s), 9:16, a seed, no sound kept, $0.02 a
  second, umt5's prompt window, the key check on the audio endpoint.
- **The estimate and the video phase**: an own_gpu episode prices its talking
  shots on S2V and the rest on i2v; the one click buys each on its link, the
  track on the request, the record's ``talk``, never the S2V link as the
  episode's video link; a story without talking clips plans as before.

Every provider is a fake; ffmpeg is real (on PATH here and in CI). Stdlib +
pytest (DEC-012).
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_runpod_comfyui as trc
import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_episode_steps as eps
import test_story_fast_track as tft
import test_story_fast_track_one_click as oc
import test_story_lipsync as tls
from test_story_assets_step import hermetic, store  # noqa: F401 -- the step's fixtures (hermetic is autouse)
from test_story_keyframe_gate import built, unpaced  # noqa: F401 -- the free Gemini tier's pacing lifted
from test_story_lipsync import media, no_lipsync_chain  # noqa: F401 -- real mp4s; the shipped LIPSYNC_CHAIN
from test_story_video_phase import timings_path  # noqa: F401 -- the measured clip timings under tmp_path

NOW = eps.NOW
S2V = "runpod/s2v_wan22"
WAN = trc.WAN
RUNPOD = {"RUNPOD_API_KEY": "rpa_fake", "RUNPOD_COMFY_ENDPOINT_ID": "ep123"}
OWN = dict(oc.QUALITY_SETTINGS, VIDEO_CHAIN=WAN, PER_EPISODE_CAP_USD="20", DAILY_CAP_USD="50",
           PER_STORY_CAP_USD="50", **RUNPOD)
AUDIO_ENV = dict(trc.ENV, RUNPOD_AUDIO_ENDPOINT_ID="aud9", RUNPOD_AUDIO_API_KEY="rpa_audio",
                 RUNPOD_AUDIO_GPU_USD_PER_HOUR="2.0")


@pytest.fixture(autouse=True)
def no_runpod_env(monkeypatch):
    """No RunPod name of the machine (a .env read into the process) reaches a test."""
    for name in list(os.environ):
        if name.startswith("RUNPOD_"):
            monkeypatch.delenv(name, raising=False)


def _mods():
    from clipping.aistory import media_policy, schemas
    from clipping.aistory.steps import assets, clips, lipsync, talking
    from clipping.providers import budget, gencache, generation, pricing, prompt_limits, runpod_comfyui, video

    return SimpleNamespace(policy=media_policy, schemas=schemas, assets=assets, clips=clips, lipsync=lipsync,
                           talking=talking, budget=budget, gencache=gencache, gen=generation, pricing=pricing,
                           limits=prompt_limits, rp=runpod_comfyui, video=video)


def _own_gpu(store, tmp_path, *, tier=2):
    """The quality v2 episode of the one click (script and storyboard
    approved, nothing made), moved onto the own_gpu profile."""
    story_id = oc._v2_unmade(store, tmp_path)
    tce._tier(store, story_id, tier=tier, budget_profile="own_gpu")
    return story_id


def _wav(path, seconds=1.0):
    return tls._wav(path, seconds)


def _voiced(monkeypatch, tmp_path):
    """Every line voiced by one real wav (``lipsync.line_audio`` faked): the
    estimate reads the lines' audio before the assets step made it."""
    path = _wav(tmp_path / "line.wav")
    monkeypatch.setattr(_mods().lipsync, "line_audio", lambda _ec, _line: path)
    return path


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ================================================================ 1. the policy

def test_the_own_gpu_profile_makes_talking_clips_and_no_other_profile_does(store, tmp_path):
    m = _mods()
    profiles = m.budget.load_profiles()["profiles"]
    assert {name: profile.get("talking_clips") for name, profile in profiles.items()} == {
        "free": None, "one_dollar": None, "quality": None, "native_speech": None, "native_speech_manual": None,
        "own_gpu": "runpod_s2v"}
    assert m.budget.TALKING_CLIPS_MODES == ("none", "runpod_s2v")
    assert m.budget._profile_errors("x", {"talking_clips": "veo"}) == [
        "profile 'x': talking_clips must be one of none, runpod_s2v, not 'veo'"]
    story_id = _own_gpu(store, tmp_path)
    assert m.policy.talking_clips(store.get(story_id)) == "runpod_s2v"
    for change in ({"budget_profile": "quality"}, {"tier": 1}):
        store.update(story_id, lambda doc: doc["generation_profile"].update(**change), now=NOW)
        assert m.policy.talking_clips(store.get(story_id)) == "none", change
        tce._tier(store, story_id, tier=2, budget_profile="own_gpu")
    legacy = tas._episode(store, tmp_path)
    tce._tier(store, legacy, tier=2, budget_profile="own_gpu")
    assert m.policy.talking_clips(store.get(legacy)) == "none"  # not v2


def test_a_shot_talks_only_when_every_rule_holds_and_each_rule_fails_on_its_own(store, tmp_path):
    m = _mods()
    story_id = _own_gpu(store, tmp_path)
    ec, script, board = tas._ec(store, story_id), eps._script(store, story_id), tas._board(store, story_id)
    line = _wav(tmp_path / "line.wav")
    timeline = m.lipsync.episode_timeline(ec, script, board)
    by_id = {shot["shot_id"]: shot for shot in board["shots"]}

    def verdict(shot, **kwargs):
        kwargs.setdefault("timeline", timeline)
        kwargs.setdefault("audio_of", lambda _line: line)
        return m.talking.verdict(ec, script, board, shot, **kwargs)

    talking = verdict(by_id["sh03"])  # a close-up of Kiwilo saying l04
    assert talking["talks"] is True and talking["why"] is None and talking["speaker"] == "char_kiwilo"
    assert 0 < talking["speech_s"] <= m.talking.CHUNK_S
    assert talking["spec"]["clip_s"] == 5 and [row["line_id"] for row in talking["spec"]["lines"]] == ["l04"]
    assert verdict(by_id["sh05"])["talks"] is True  # a medium single of Mangella

    assert verdict(by_id["sh01"])["why"] == m.talking.NO_LINES
    two = dict(by_id["sh05"], lines=["l09", "l12", "l04"])  # Mangella's lines and one of Kiwilo's
    assert verdict(two)["why"] == m.talking.SPEAKERS
    assert verdict(by_id["sh04"])["why"] == m.talking.FRAMING  # a two-shot: one face per talking clip
    crowded = dict(by_id["sh03"], subject_tags=["@char_kiwilo", "@char_mangella"])
    assert verdict(crowded)["why"] == m.talking.NOT_ALONE
    away = dict(by_id["sh03"], subject_tags=["@char_mangella"])
    assert verdict(away)["why"] == m.talking.NOT_ALONE  # the speaker off screen
    long = copy.deepcopy(timeline)
    entry = next(item for item in long["lines"] if item["line_id"] == "l04")
    entry.update(start_s=next(item["start_s"] for item in long["shots"] if item["shot_id"] == "sh03"), duration_s=6.0)
    too_long = verdict(by_id["sh03"], timeline=long)
    assert too_long["why"] == m.talking.TOO_LONG and too_long["speech_s"] > m.talking.CHUNK_S
    assert verdict(by_id["sh03"], audio_of=lambda _line: None)["why"] == m.talking.NO_VOICE
    store.update(story_id, lambda doc: doc["generation_profile"].update(budget_profile="quality"), now=NOW)
    ec = tas._ec(store, story_id)
    assert verdict(by_id["sh03"])["why"] == m.talking.OFF  # another profile
    assert m.talking.why_text({"why": m.talking.TOO_LONG}) == "its speech runs past one 4.8 s chunk"


# ================================================================ 2. the adapter

def _talk_request(tmp_path, *, audio=True, **kwargs):
    keyframe = tmp_path / "shot_03.png"
    keyframe.write_bytes(trc.PNG)
    track = _wav(tmp_path / "shot_03.talk.wav", 2.0) if audio else None
    kwargs.setdefault("duration_s", 5)
    kwargs.setdefault("fps", None)
    return trc.clip(str(keyframe), audio=track, **kwargs)


class Strip:
    """``subprocess.run``'s seam: records the argv and copies the input to the
    output (the picture kept; this fake keeps the bytes)."""

    def __init__(self):
        self.argv = []

    def __call__(self, argv, **_kwargs):
        self.argv.append(argv)
        shutil.copyfile(argv[argv.index("-i") + 1], argv[-1])
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_a_talking_clip_uploads_the_keyframe_and_the_wav_to_the_voice_lines_endpoint_with_its_key(tmp_path):
    m = _mods()
    request = _talk_request(tmp_path)
    transport = trc.FakeTransport(trc.queue("IN_PROGRESS") + [trc.done()])
    strip, log, submitted = Strip(), [], []
    result = m.rp.RUNPOD_COMFY.generate(trc.link(S2V), request, credentials=AUDIO_ENV, on_log=log.append,
                                        transport=transport, sleep_fn=lambda _s: None, time_fn=lambda: 0.0,
                                        on_submit=submitted.append, run=strip)

    run, status = "https://api.runpod.ai/v2/aud9/run", "https://api.runpod.ai/v2/aud9/status/job-1"
    assert transport.urls() == [("POST", run), ("GET", status), ("GET", status)]
    assert all(call["headers"]["Authorization"] == "Bearer rpa_audio" for call in transport.calls)
    body = transport.json(0)["input"]
    wav, png = body["images"]
    assert wav["name"].startswith("rzdhop_") and wav["name"].endswith(".wav")
    assert wav["image"].startswith("data:audio/") and ";base64," in wav["image"]
    assert base64.b64decode(wav["image"].split(",", 1)[1]) == Path(request.audio).read_bytes()
    assert png["image"].startswith("data:image/png;base64,")
    template = m.rp.load_template("s2v_wan22")
    graph = body["workflow"]
    assert graph[template["audio_node"]]["inputs"]["audio"] == wav["name"]
    assert graph[template["image_node"]]["inputs"]["image"] == png["name"]
    assert graph["23"]["inputs"]["length"] == 81 and graph["13"]["inputs"]["seed"] == 11
    assert "{{" not in json.dumps(graph)
    assert submitted[0]["endpoint"] == "aud9"
    # The muxed dialogue is dropped before the clip is kept: the picture copied, no sound.
    assert len(strip.argv) == 1 and "-an" in strip.argv[0] and strip.argv[0][strip.argv[0].index("-c") + 1] == "copy"
    assert result.paths[0].endswith("shot_01.mp4") and Path(result.paths[0]).read_bytes() == trc.MP4
    assert sorted(os.listdir(request.out_dir)) == ["shot_01.mp4"]  # the answer as sent is not kept
    assert result.meta["has_audio"] is False and result.meta["audio_stripped"] is True
    assert result.meta["served_by"] == "audio" and result.meta["endpoint"] == "aud9"
    assert result.meta["gpu_seconds"] == 188.0 and result.meta["billed_usd"] == round(188 * 2.0 / 3600, 4)
    assert result.meta["clip_s"] == 5 and result.meta["frames"] == 81 and result.meta["seed_honoured"] is True
    assert any("with its dialogue track" in line for line in log)
    assert any("188 GPU-s" in line and "$2.0/h" in line for line in log)


def test_the_talking_endpoint_falls_back_to_the_image_one_then_the_video_one_with_their_keys(tmp_path):
    m = _mods()
    image_env = dict(trc.ENV, RUNPOD_IMAGE_ENDPOINT_ID="img7", RUNPOD_IMAGE_API_KEY="rpa_image")
    for env, endpoint, key in ((image_env, "img7", "rpa_image"), (trc.ENV, "ep123", "rpa_fake")):
        transport = trc.FakeTransport([(200, {"id": "job-1"}), trc.done()])
        m.rp.RUNPOD_COMFY.generate(trc.link(S2V), _talk_request(tmp_path), credentials=env, on_log=lambda _l: None,
                                   transport=transport, sleep_fn=lambda _s: None, time_fn=lambda: 0.0, run=Strip())
        assert transport.urls()[0] == ("POST", f"https://api.runpod.ai/v2/{endpoint}/run")
        assert {call["headers"]["Authorization"] for call in transport.calls} == {f"Bearer {key}"}
    checked = m.video.check_key(trc.link(S2V), AUDIO_ENV, transport=trc.FakeTransport([
        (200, {"workers": {"ready": 1}})]))
    assert checked["status"] == "ok" and checked["endpoint"] == "aud9"
    probe = trc.FakeTransport([(200, {"workers": {"ready": 2}})])
    ok, note = m.rp.RUNPOD_COMFY.probe(trc.link(S2V), credentials=AUDIO_ENV, transport=probe)
    assert ok and "aud9" in note and probe.calls[0]["headers"]["Authorization"] == "Bearer rpa_audio"


def test_the_kept_talking_clip_has_no_sound_so_the_render_never_mixes_the_dialogue_as_ambience(tmp_path, media):
    """The ambience exclusion with the real ffmpeg: the worker's mp4 carries
    the track; what the adapter keeps has a picture and no sound track
    (``clips.clip_has_audio``, what the render's ambience reads)."""
    m = _mods()
    assert m.clips.clip_has_audio(str(media.sound)) is True
    answer = trc.done(output={"images": [{"filename": "s2v_00001_.mp4", "type": "base64",
                                          "data": base64.b64encode(media.sound.read_bytes()).decode()}]})
    result = m.rp.RUNPOD_COMFY.generate(trc.link(S2V), _talk_request(tmp_path), credentials=AUDIO_ENV,
                                        on_log=lambda _l: None, transport=trc.FakeTransport([(200, {"id": "job-1"}),
                                                                                            answer]),
                                        sleep_fn=lambda _s: None, time_fn=lambda: 0.0)
    kept = result.paths[0]
    assert m.clips.clip_has_audio(kept) is False and os.path.getsize(kept) > 0
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", kept],
                           capture_output=True, text=True, check=True)
    assert probe.stdout.split() == ["video"]


@pytest.mark.parametrize("spec,change,message", [
    (S2V, {"audio": False}, "its dialogue track is missing"),
    (S2V, {"duration_s": 4}, "sells clips of 5 s"),
    (S2V, {"references": ()}, "one keyframe"),
    (WAN, {}, "takes no dialogue track"),
])
def test_a_talking_clip_that_cannot_be_bought_as_asked_is_refused_before_sending(tmp_path, spec, change, message):
    m = _mods()
    audio = change.pop("audio", True)
    request = _talk_request(tmp_path, audio=audio, **change)
    transport = trc.FakeTransport([])
    with pytest.raises(ValueError) as excinfo:
        m.rp.RUNPOD_COMFY.generate(trc.link(spec), request, credentials=AUDIO_ENV, on_log=print, transport=transport)
    assert message in str(excinfo.value) and transport.calls == []
    not_wav = tmp_path / "track.mp3"
    not_wav.write_bytes(b"ID3")
    mp3 = _talk_request(tmp_path)
    mp3.audio = str(not_wav)
    with pytest.raises(ValueError, match="must be a .wav file"):
        m.rp.RUNPOD_COMFY.generate(trc.link(S2V), mp3, credentials=AUDIO_ENV, on_log=print, transport=transport)
    assert transport.calls == []


def test_the_track_keys_a_talking_clip_by_its_bytes_and_every_other_key_is_unchanged(tmp_path):
    m = _mods()
    request = _talk_request(tmp_path)
    payload = m.gencache.key_payload("video", trc.link(S2V), request)
    assert payload["audio"] == _sha(request.audio)
    other = copy.copy(request)
    other.audio = _wav(tmp_path / "other.wav", 3.0)
    assert m.gencache.request_key("video", trc.link(S2V), other) != m.gencache.request_key("video", trc.link(S2V),
                                                                                           request)
    silent = copy.copy(request)
    silent.audio = None
    assert "audio" not in m.gencache.key_payload("video", trc.link(WAN), silent)
    assert m.gen.GenRequest(kind="video").audio is None


# ================================================================ 3. the tables

def test_the_s2v_link_sells_one_chunk_at_9_16_with_a_seed_no_sound_and_a_price_per_second():
    m = _mods()
    assert m.rp.S2V_TEMPLATES == ("s2v_wan22",) and "s2v_wan22" not in m.rp.TEMPLATES
    rule = m.rp.load_template("s2v_wan22")["frame_rule"]
    assert m.video.CLIP_LENGTHS[S2V] == (5,) and 5 in rule["lengths"]
    assert m.video.AUDIO[S2V] == "never" and S2V in m.video.SEED_HONOURED
    assert m.video.supports_aspect(S2V, "9:16") and not m.video.supports_aspect(S2V, "16:9")
    price = m.pricing.price_for(trc.link(S2V))
    assert (price.unit, price.usd) == ("second", 0.02) and "$0.097 cold for 4.875 s" in price.note
    assert m.gen.is_paid(trc.link(S2V)) and m.limits.limit_for(S2V, live={}).window_tokens == 512
    estimate = m.rp.RUNPOD_COMFY.estimate(trc.link(S2V), m.gen.GenRequest(kind="video", duration_s=5))
    assert estimate.unit == "second" and estimate.qty == 5 and estimate.est_usd == 0.1


# ================================================================ 4. the estimate

def _video(store, story_id, *, adapters=None, settings=OWN):
    adapters = adapters or tce._adapters()
    adapters.setdefault(("video", "runpod"), tce.NeverVideo())
    return tce._units(store, story_id, settings, adapters=adapters)["video"]


def test_an_own_gpu_episode_prices_its_talking_shots_on_s2v_and_every_other_on_i2v(store, tmp_path, monkeypatch):
    m = _mods()
    story_id = _own_gpu(store, tmp_path)
    _voiced(monkeypatch, tmp_path)
    video = _video(store, story_id)
    ec, script, board = tas._ec(store, story_id), eps._script(store, story_id), tas._board(store, story_id)
    found = m.talking.verdicts(ec, script, board)
    talks = [shot_id for shot_id, item in found.items() if item["talks"]]
    assert talks and "sh03" in talks and "sh04" not in talks
    assert video["link"] == WAN and video["ready"] is True
    rows = {row["shot_id"]: row for row in video["plan"]}
    for shot_id in talks:
        row = rows[shot_id]
        assert (row["link"], row["talks"], row["clip_s"], row["est_usd"]) == (S2V, True, 5, 0.1)
        assert "cover" not in row
    others = [row for row in video["plan"] if row["shot_id"] not in talks]
    assert others and all("link" not in row and "talks" not in row for row in others)
    assert video["est_usd"] == pytest.approx(sum(row["est_usd"] for row in video["plan"]))
    assert video["seconds"] == sum(row["clip_s"] for row in video["plan"])
    part = video["talking"]
    assert part["shots"] == talks and part["count"] == len(talks) and part["est_usd"] == pytest.approx(0.1 * len(talks))
    assert part["link"] == S2V and part["status"] == "keyed"
    too_long = [shot_id for shot_id, item in found.items() if item["why"] == m.talking.TOO_LONG]
    assert part["too_long"] == too_long
    words = f"{len(talks)} shots talk on {S2V} (the mouth follows the line)"
    assert words in video["message"] and words in part["message"]
    if too_long:
        assert f"{len(too_long)} too long for one chunk" in video["message"]


def test_a_story_without_talking_clips_plans_its_clips_exactly_as_before(store, tmp_path, monkeypatch):
    m = _mods()
    story_id = _own_gpu(store, tmp_path)
    _voiced(monkeypatch, tmp_path)
    on = _video(store, story_id)
    monkeypatch.setattr(m.policy, "talking_clips", lambda _story: m.policy.TALKING_NONE)
    off = _video(store, story_id)
    assert "talking" not in off and "talk" not in off["message"]
    assert all("link" not in row and "talks" not in row for row in off["plan"])
    kept = {row["shot_id"]: row for row in off["plan"]}
    assert [row for row in on["plan"] if not row.get("talks")] == [kept[row["shot_id"]] for row in on["plan"]
                                                                  if not row.get("talks")]


def test_without_the_s2v_adapter_every_shot_stays_on_the_i2v_link_and_the_estimate_says_why(store, tmp_path,
                                                                                            monkeypatch):
    story_id = _own_gpu(store, tmp_path)
    _voiced(monkeypatch, tmp_path)
    settings = dict(OWN, VIDEO_CHAIN=tce.SEEDANCE, RUNPOD_API_KEY="")
    video = _video(store, story_id, settings=settings)
    assert video["link"] == tce.SEEDANCE and not any(row.get("talks") for row in video["plan"])
    assert video["talking"]["status"] == "skipped" and video["talking"]["shots"] == []
    assert f"{S2V} cannot run now" in video["message"]


# ================================================================ 5. the video phase, end to end

class TalkingVideo(tls.RealVideo):
    """``tls.RealVideo`` for both runpod links: each request answers a real
    mp4; a talking one is checked as the real adapter checks it (one keyframe,
    a wav track on the request) and its track's bytes kept."""

    def __init__(self, source):
        super().__init__(source)
        self.tracks = {}

    def generate(self, link, request, **kwargs):
        if link.model == "s2v_wan22":
            assert request.audio and request.audio.endswith(".wav") and os.path.isfile(request.audio)
            self.tracks[request.extra["name"]] = _sha(request.audio)
        else:
            assert request.audio is None
        return super().generate(link, request, **kwargs)


def _one_click(store, tmp_path, media, story_id, *, video=None):
    seams = tls._fakes(tmp_path, media, video=video or TalkingVideo(media.silent))
    seams.fakes.adapters[("video", "runpod")] = seams.video
    seams.fakes.adapters[("image_edit", "runpod")] = seams.image
    seams.fakes.adapters[("image", "runpod")] = seams.image
    # The i2v link is seedance here (its lengths cover the fixture's 7 s shots); the talking one is RunPod's.
    summary, log = tft.run(store, story_id, seams.fakes, settings=dict(OWN, VIDEO_CHAIN=tce.SEEDANCE))
    return seams, summary, log


def test_the_one_click_buys_a_talking_shot_on_s2v_with_its_track_and_every_other_on_i2v(store, tmp_path, media):
    m = _mods()
    story_id = _own_gpu(store, tmp_path)
    seams, summary, log = _one_click(store, tmp_path, media, story_id)

    assert summary["steps"]["render"]["state"] == "completed", summary
    ec, script, board = tas._ec(store, story_id), eps._script(store, story_id), tas._board(store, story_id)
    found = m.talking.verdicts(ec, script, board)
    talks = sorted(shot_id for shot_id, item in found.items() if item["talks"])
    assert talks and "sh03" in talks
    links = dict(zip(seams.video.names(), seams.video.links))
    assert sorted(name for name, link in links.items() if link == S2V) == [f"shot_{s[2:]}" for s in talks]
    assert all(link == tce.SEEDANCE for name, link in links.items() if f"sh{name[5:]}" not in talks)
    assert len(seams.video.requests) == len(board["shots"])
    for shot in board["shots"]:
        clip = shot["assets"]["clip"]
        if shot["shot_id"] not in talks:
            assert clip["link"] == tce.SEEDANCE and "talk" not in clip
            continue
        name = f"shot_{shot['shot_id'][2:]}"
        request = seams.video.requests[seams.video.names().index(name)]
        assert (clip["link"], clip["clip_s"], clip["est_usd"]) == (S2V, 5, 0.1) and request.duration_s == 5
        assert clip["talk"]["track_hash"] == found[shot["shot_id"]]["spec"]["hash"]
        assert clip["talk"]["audio_sha256"] == seams.video.tracks[name]
        assert clip["talk"]["lines"] == shot["lines"] and clip["talk"]["speech_s"] <= m.talking.CHUNK_S
        assert request.references == (m.assets.shot_image_path(ec, shot),)
    assert m.schemas.storyboard_errors(board) == []
    # The S2V link is never the episode's video link: the other shots stay on theirs.
    doc = tas._assets_doc(store, story_id)
    assert doc["links"]["video"]["link"] == tce.SEEDANCE
    video_summary = summary["steps"]["assets"]["video"]
    assert sorted(video_summary["talking"]["made"]) == talks and video_summary["talking"]["link"] == S2V
    assert any(line.startswith(f"🗣️ {len(talks)} shots talk on {S2V}") for line in log)
    # Continue repeats nothing: every clip, talking or not, is current on its own link.
    again = TalkingVideo(media.silent)
    _one_click(store, tmp_path, media, story_id, video=again)
    assert again.requests == []


def test_a_re_voiced_line_makes_its_talking_clip_stale_and_only_that_clip_is_bought_again(store, tmp_path, media):
    m = _mods()
    story_id = _own_gpu(store, tmp_path)
    _one_click(store, tmp_path, media, story_id)
    ec, script, board = tas._ec(store, story_id), eps._script(store, story_id), tas._board(store, story_id)
    shot = next(item for item in board["shots"] if item["shot_id"] == "sh03")
    assert m.clips.clip_state(ec, shot, script, link=tce.SEEDANCE, tier=2, flags=m.clips.shot_flags(shot, None),
                              image_sha=m.assets._sha256_file(m.assets.shot_image_path(ec, shot))) == "current"
    line = next(item for scene in script["scenes"] for item in scene["lines"] if item["line_id"] == "l04")
    audio = m.assets.line_audio_path(ec, line)
    tls._ffmpeg("-f", "lavfi", "-i", "sine=frequency=990:duration=1.5:sample_rate=24000", "-ac", "1",
                "-c:a", "libmp3lame", "-b:a", "32k", "-f", "mp3", audio + ".new.mp3")
    os.replace(audio + ".new.mp3", audio)
    assert m.clips.clip_state(ec, shot, script, link=tce.SEEDANCE, tier=2, flags=m.clips.shot_flags(shot, None),
                              image_sha=m.assets._sha256_file(m.assets.shot_image_path(ec, shot))) == "stale"
    video = _video(store, story_id, adapters=tls._fakes(tmp_path, media, llm=False).fakes.adapters,
                   settings=dict(OWN, VIDEO_CHAIN=tce.SEEDANCE))
    new = [row for row in video["plan"] if row["why"] not in ("current", "booked") and row["est_usd"] > 0]
    assert [(row["shot_id"], row["link"]) for row in new] == [("sh03", S2V)]


def test_regenerating_a_talking_shots_clip_buys_it_again_on_s2v_with_its_track(store, tmp_path, media,
                                                                              monkeypatch):
    from clipping.aistory import workflow
    from clipping.aistory.steps import entities, regenerate

    m = _mods()
    story_id = _own_gpu(store, tmp_path)
    _one_click(store, tmp_path, media, story_id)
    settings = dict(OWN, VIDEO_CHAIN=tce.SEEDANCE)
    video = TalkingVideo(media.silent)
    adapters = tls._fakes(tmp_path, media, video=video, llm=False).fakes.adapters
    adapters[("video", "runpod")] = video
    estimate = workflow.regenerate_clip_estimate(store, store.get(story_id), regenerate.parse_target(
        "shot:1:sh03:video"), env=settings, adapters=adapters)
    assert (estimate["ready"], estimate["link"], estimate["units"], estimate["est_usd"]) == (
        True, S2V, {"clips": 1, "seconds": 5}, 0.1)

    monkeypatch.setattr(entities, "fresh_seed", lambda: 4242)
    ctx, _log = eps._ctx(store, story_id, step="regenerate", settings=settings,
                         params={"target": "shot:1:sh03:video", "note": "a bigger smile"})
    result = regenerate.run(ctx, adapters=adapters, time_fn=eps.Clock(0.0), sleep_fn=lambda _s: None)

    assert video.links == [S2V] and video.requests[0].seed == 4242 and video.requests[0].duration_s == 5
    assert video.tracks["shot_03"] and result["link"] == S2V and result["clip_s"] == 5
    clip = next(shot for shot in tas._board(store, story_id)["shots"] if shot["shot_id"] == "sh03")["assets"]["clip"]
    assert (clip["state"], clip["link"], clip["note"]) == ("current", S2V, "a bigger smile")
    assert clip["talk"]["audio_sha256"] == video.tracks["shot_03"]
    assert tas._assets_doc(store, story_id)["links"]["video"]["link"] == tce.SEEDANCE
