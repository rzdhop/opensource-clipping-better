"""Lip-synced clips (AI Story, DEC-258; the human: "decide for me" --
option B): once a fully animated quality episode's clip is bought, its
characters' lips are moved to the shot's dialogue by Kling LipSync on fal
(``fal-ai/kling-video/lipsync/audio-to-video``, $0.014 per started 5 s), and
the lip-synced take is what ``assets.video`` names -- what the render stages.

- **The adapter** (``clipping/providers/lipsync.py``): the clip and the
  dialogue track are uploaded to fal storage (the key on the initiate call
  only, never on the PUT to the signed URL), then sent as ``video_url`` and
  ``audio_url``; journaled at submit, resumed, never submitted twice; a
  failed upload is proven unbilled. Priced per second of input video,
  rounded up to 5 s.
- **The dialogue track** (``steps/lipsync.py``, real ffmpeg): 24 kHz mono,
  exactly the clip's length, each in-frame line at its offset, silence
  elsewhere; an off-screen speaker's line left out; no such line, no track.
- **The policy and the 10 s cap**: the quality preset lipsyncs a fully
  animated v2 story (``generation_profile.lipsync: none`` turns it off); the
  plan buys no clip longer than 10 s, the storyboard plans past it as two
  shots.
- **The video phase and the one click**, end to end with fakes: the
  record, the file, ``assets.video``, the ledger row in seconds, the feed,
  the summary; a failure keeps the plain clip; a second run redoes nothing;
  a regenerated clip and a re-voiced line are lipsynced again; the render
  manifest stages the lip-synced take; the estimate and the caps count it.
- **RC-M3**: a clip recorded without ``lipsync`` validates and renders as
  before; the dashboard badge reads ``clip.lipsync``.

Every provider is a fake; the only real process is ffmpeg (on PATH here and
in CI). Stdlib + pytest (DEC-012); each test reaches the new behaviour, so
on the parent commit it fails on its own.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_story_ambience as amb
import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_episode_steps as eps
import test_story_fast_track as tft
import test_story_fast_track_one_click as oc
import test_story_keyframe_consistency as kc
import test_story_keyframe_fix as kf
import test_story_keyframe_gate as kg
import test_story_measure as tsm
import test_story_video_phase as tvp
from test_story_assets_step import hermetic, store  # noqa: F401 -- the step's fixtures (hermetic is autouse)
from test_story_keyframe_gate import built, unpaced  # noqa: F401 -- the free Gemini tier's pacing lifted
from test_story_video_phase import timings_path  # noqa: F401 -- the measured clip timings under tmp_path

NOW = eps.NOW
SEEDANCE = tce.SEEDANCE
LIPSYNC_LINK = "fal/kling-lipsync"
LIPSYNC_APP = "fal-ai/kling-video/lipsync/audio-to-video"
QUALITY = oc.QUALITY_SETTINGS
FFMPEG = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]


@pytest.fixture(autouse=True)
def no_lipsync_chain(monkeypatch):
    """The shipped LIPSYNC_CHAIN, whatever the machine's environment says."""
    monkeypatch.delenv("LIPSYNC_CHAIN", raising=False)


def _mods():
    from clipping.aistory import media_policy, schemas
    from clipping.aistory.steps import clips, lipsync, storyboard
    from clipping.providers import generation, lipsync as providers

    return SimpleNamespace(policy=media_policy, schemas=schemas, clips=clips, lipsync=lipsync,
                           storyboard=storyboard, gen=generation, lp=providers)


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ================================================================ the media

def _ffmpeg(*args):
    subprocess.run(FFMPEG + list(args), check=True, capture_output=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    """Real, tiny mp4s: a silent clip (seedance's), a clip with its own sound
    (Veo's), and the lipsync's answer -- another picture with a sound track
    (Kling's: the driving audio), which the kept take must never carry."""
    root = tmp_path_factory.mktemp("lipsync_media")
    video = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    silent, sound, synced = root / "silent.mp4", root / "sound.mp4", root / "synced.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=72x128:rate=12:duration=1", *video, str(silent))
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=72x128:rate=12:duration=1", "-f", "lavfi", "-i",
            "sine=frequency=220:duration=1", *video, "-c:a", "aac", "-shortest", str(sound))
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=72x128:rate=12:duration=1", "-f", "lavfi", "-i",
            "sine=frequency=880:duration=1", *video, "-c:a", "aac", "-shortest", str(synced))
    return SimpleNamespace(root=root, silent=silent, sound=sound, synced=synced)


def _tagged(source, dest, tag) -> str:
    """*source*'s bytes with a trailing MP4 ``free`` box naming *tag*: a valid
    mp4 whose bytes are its own."""
    payload = str(tag).encode()
    Path(dest).write_bytes(Path(source).read_bytes() + struct.pack(">I4s", 8 + len(payload), b"free") + payload)
    return str(dest)


def _wav(path, seconds, *, rate=24000, channels=1, freq=1000.0, amplitude=0.5):
    frames = int(round(seconds * rate))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        data = bytearray()
        for i in range(frames):
            sample = int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / rate))
            data += struct.pack("<h", sample) * channels
        out.writeframes(bytes(data))
    return str(path)


def _samples(path):
    with wave.open(str(path), "rb") as track:
        params = (track.getnchannels(), track.getframerate(), track.getsampwidth(), track.getnframes())
        raw = track.readframes(track.getnframes())
    return params, struct.unpack(f"<{len(raw) // 2}h", raw)


def _loud(samples, start_s, end_s, rate=24000) -> float:
    window = samples[int(start_s * rate):int(end_s * rate)]
    return max((abs(value) for value in window), default=0)


# ================================================================ the fakes

class RealEdge(tsm.Edge):
    """``tsm.Edge`` whose lines are real mp3s as long as their cues say (a
    dialogue track is made from them); each call its own tone from *base*,
    so a line spoken again by another instance is other bytes."""

    def __init__(self, *, base=300, **kwargs):
        super().__init__(**kwargs)
        self.base = base

    def _synth(self, text, voice, audio_path, subs_path=None, **prosody):
        cues = super()._synth(text, voice, audio_path, subs_path, **prosody)
        tone = self.base + 40 * len(self.calls)
        _ffmpeg("-f", "lavfi", "-i", f"sine=frequency={tone}:duration={tsm.edge_seconds(text)}:sample_rate=24000",
                "-ac", "1", "-c:a", "libmp3lame", "-b:a", "32k", "-f", "mp3", str(audio_path))
        return cues


class RealVideo(tvp.FakeVideo):
    """``tvp.FakeVideo`` answering a real mp4 (its own bytes per request)."""

    def __init__(self, source, **kwargs):
        super().__init__(**kwargs)
        self.source = source

    def _answer(self, link, request):
        name = request.extra["name"]
        if name in self.slow_for:
            raise tvp.ProviderError(f"{link.provider}/{link.model}: request still IN_QUEUE after 600s")
        path = os.path.join(request.out_dir, f"{name}.mp4")
        _tagged(self.source, path, f"{name}:{request.seed}:{request.duration_s}:{len(self.requests)}")
        return tvp.GenResult(provider=link.provider, model=link.model, paths=(path,), seed=request.seed)


class FakeLipsync:
    """fal's Kling LipSync, faked: every generate is one submit (journaled
    through ``on_submit``), refused as the real adapter refuses a request it
    cannot buy (``lipsync.check_request``), and answers a real mp4 -- with a
    sound track of its own. A name in *fail_for* is settled without a clip
    after its submit (``RequestFailed``, booked)."""

    def __init__(self, source, *, fail_for=()):
        self.source = source
        self.fail_for = set(fail_for)
        self.requests = []
        self.audio = []
        self.resumed = []

    def estimate(self, link, request):
        return _mods().lp.estimate_for(link, request.duration_s)

    def probe(self, link, **_kwargs):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, on_submit=None, **_kwargs):
        _mods().lp.check_request(link, request)
        self.requests.append(copy.copy(request))
        self.audio.append(_sha(request.extra["audio"]))
        if on_submit is not None:
            on_submit({"request_id": f"lip-{len(self.requests)}", "status_url": "https://queue.fal.run/s",
                       "response_url": "https://queue.fal.run/r"})
        if request.extra["name"] in self.fail_for:
            raise tvp.RequestFailed(f"{link.provider}/{link.model}: request FAILED (synthetic: no face found)")
        return self._answer(link, request)

    def resume(self, link, request, entry, *, credentials, on_log, transport=None, **_kwargs):
        self.resumed.append(entry["request"]["request_id"])
        return self._answer(link, request)

    def _answer(self, link, request):
        os.makedirs(request.out_dir, exist_ok=True)
        path = os.path.join(request.out_dir, f"{request.extra['name']}.mp4")
        _tagged(self.source, path, f"{request.extra['name']}:{len(self.requests)}")
        return tvp.GenResult(provider=link.provider, model=link.model, paths=(path,), seed=None)

    def names(self):
        return [request.extra["name"] for request in self.requests]


def _fakes(tmp_path, media, *, lip=None, video=None, llm=True, tone=300):
    """The quality episode's one-click seams (``oc._quality_fakes``) with real
    voices (from *tone*), real clips and the lipsync link."""
    video = video or RealVideo(media.silent)
    fakes, image = oc._quality_fakes(tmp_path, video=video, llm=llm)
    edge = RealEdge(base=tone)
    fakes.adapters[("tts", "edge")] = edge
    fakes.edge = edge
    lip = lip if lip is not None else FakeLipsync(media.synced)
    fakes.adapters[("lipsync", "fal")] = lip
    return SimpleNamespace(fakes=fakes, image=image, video=video, lip=lip, edge=edge)


def _made(store, tmp_path, media, **kwargs):
    """The quality episode made from scratch by the one click (v2, tier 2,
    every shot a clip on seedance), lipsynced: ``(story_id, seams, summary, log)``."""
    story_id = oc._v2_unmade(store, tmp_path)
    seams = _fakes(tmp_path, media, **kwargs)
    summary, log = tft.run(store, story_id, seams.fakes, settings=QUALITY)
    return story_id, seams, summary, log


def _spoken(store, story_id):
    m = _mods()
    script = eps._script(store, story_id)
    return [shot["shot_id"] for shot in tas._board(store, story_id)["shots"] if m.lipsync.spoken_lines(script, shot)]


def _clip_file(store, story_id, name) -> Path:
    return Path(store.story_dir(story_id)) / "episodes" / "ep01" / "assets" / "clips" / name


def _lip_rows(store, story_id):
    return [row for row in tas._ledger(store, story_id) if row["model"] == LIPSYNC_APP]


# ================================================================ 1. the adapter

class FalStub:
    """fal's storage and queue over the transport seam: records every call."""

    def __init__(self, *, put_status=200):
        self.calls = []
        self.put_status = put_status
        self.uploads = 0

    def __call__(self, method, url, *, headers=None, body=None, timeout=None):
        from clipping.providers.transport import Response

        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body})
        if url.startswith("https://rest.alpha.fal.ai/storage/upload/initiate"):
            self.uploads += 1
            n = self.uploads
            return Response(200, {}, json.dumps({"upload_url": f"https://v3.fal.media/upload/{n}?sig=s{n}",
                                                 "file_url": f"https://v3.fal.media/files/f{n}"}).encode())
        if method == "PUT":
            return Response(self.put_status, {}, b"" if self.put_status < 400 else b'{"detail": "busy"}')
        if method == "POST" and url == f"https://queue.fal.run/{LIPSYNC_APP}":
            return Response(200, {}, json.dumps({"request_id": "rq-1", "status_url": "https://queue.fal.run/st",
                                                 "response_url": "https://queue.fal.run/rs"}).encode())
        if url == "https://queue.fal.run/st":
            return Response(200, {}, b'{"status": "COMPLETED"}')
        if url == "https://queue.fal.run/rs":
            return Response(200, {}, json.dumps({"video": {"url": "https://v3.fal.media/files/out.mp4",
                                                           "content_type": "video/mp4"}}).encode())
        if url == "https://v3.fal.media/files/out.mp4":
            return Response(200, {}, b"\x00\x00\x00\x18ftypmp42synced")
        raise AssertionError(f"unexpected {method} {url}")


def _lip_request(tmp_path, media, *, clip_s=7, audio_bytes=None):
    m = _mods()
    track = tmp_path / "track.wav"
    if audio_bytes is None:
        _wav(track, clip_s)
    else:
        track.write_bytes(audio_bytes)
    return m.gen.GenRequest(kind=m.gen.LIPSYNC, references=(str(media.silent),), duration_s=clip_s,
                            out_dir=str(tmp_path / "out"), extra={"audio": str(track), "name": "shot_03.lipsync"})


def test_the_adapter_uploads_the_clip_and_the_track_then_sends_their_urls_with_the_key_only_to_fal(tmp_path, media):
    m = _mods()
    link = m.gen.parse_generation_chain(m.gen.LIPSYNC, LIPSYNC_LINK)[0]
    stub, submitted = FalStub(), []
    request = _lip_request(tmp_path, media)

    result = m.lp.FAL_LIPSYNC.generate(link, request, credentials={"FAL_KEY": "secret-key"}, on_log=lambda _l: None,
                                       transport=stub, sleep_fn=lambda _s: None, on_submit=submitted.append)

    methods = [(call["method"], call["url"].split("?")[0]) for call in stub.calls]
    assert methods == [
        ("POST", "https://rest.alpha.fal.ai/storage/upload/initiate"), ("PUT", "https://v3.fal.media/upload/1"),
        ("POST", "https://rest.alpha.fal.ai/storage/upload/initiate"), ("PUT", "https://v3.fal.media/upload/2"),
        ("POST", f"https://queue.fal.run/{LIPSYNC_APP}"), ("GET", "https://queue.fal.run/st"),
        ("GET", "https://queue.fal.run/rs"), ("GET", "https://v3.fal.media/files/out.mp4")]
    initiate = [json.loads(call["body"]) for call in stub.calls if "initiate" in call["url"]]
    assert initiate == [{"content_type": "video/mp4", "file_name": "silent.mp4"},
                        {"content_type": "audio/wav", "file_name": "track.wav"}]
    puts = [call for call in stub.calls if call["method"] == "PUT"]
    assert puts[0]["body"] == media.silent.read_bytes() and puts[1]["body"] == Path(request.extra["audio"]).read_bytes()
    assert [call["headers"] for call in puts] == [{"Content-Type": "video/mp4"}, {"Content-Type": "audio/wav"}]
    queue = next(call for call in stub.calls if call["url"].endswith(LIPSYNC_APP))
    assert json.loads(queue["body"]) == {"video_url": "https://v3.fal.media/files/f1",
                                         "audio_url": "https://v3.fal.media/files/f2"}
    # The key rides to fal's API hosts alone: never to the signed upload URL nor the CDN.
    for call in stub.calls:
        keyed = call["headers"].get("Authorization") == "Key secret-key"
        assert keyed == call["url"].startswith(("https://rest.alpha.fal.ai/", "https://queue.fal.run/")), call["url"]
        assert "secret-key" not in call["url"]
    assert submitted[0] == {"request_id": "rq-1", "status_url": "https://queue.fal.run/st",
                            "response_url": "https://queue.fal.run/rs"}
    assert submitted[1]["output"]["url"] == "https://v3.fal.media/files/out.mp4"
    assert [os.path.basename(path) for path in result.paths] == ["shot_03.lipsync.mp4"]
    assert Path(result.paths[0]).read_bytes() == b"\x00\x00\x00\x18ftypmp42synced"


def test_a_request_it_cannot_buy_is_refused_before_anything_is_sent(tmp_path, media):
    m = _mods()
    link = m.gen.parse_generation_chain(m.gen.LIPSYNC, LIPSYNC_LINK)[0]
    for request, words in (
            (_lip_request(tmp_path, media, clip_s=12), "takes clips of 2 to 10 s, not 12"),
            (_lip_request(tmp_path, media, audio_bytes=b"x" * (5 * 1024 * 1024 + 1)), "at most 5242880 bytes"),
            (m.gen.GenRequest(kind=m.gen.LIPSYNC, references=(str(media.silent),), duration_s=7,
                              out_dir=str(tmp_path)), "needs its dialogue track")):
        stub = FalStub()
        with pytest.raises(ValueError, match=re.escape(words)):
            m.lp.FAL_LIPSYNC.generate(link, request, credentials={"FAL_KEY": "k"}, on_log=lambda _l: None,
                                      transport=stub)
        assert stub.calls == []


def test_through_the_cache_a_lipsync_is_booked_once_kept_and_a_failed_upload_is_unbilled(tmp_path, media):
    from clipping.providers import gencache

    m = _mods()
    link = m.gen.parse_generation_chain(m.gen.LIPSYNC, LIPSYNC_LINK)[0]
    booked = []
    cache = gencache.GenCache(str(tmp_path / "gen"), book=booked.append)
    adapters = {("lipsync", "fal"): m.lp.FAL_LIPSYNC}
    request = _lip_request(tmp_path, media)

    def run(transport, req):
        return m.gen.run_generation_chain(m.gen.LIPSYNC, [link], req, env={"FAL_KEY": "k"}, allow_paid=True,
                                          on_log=lambda _l: None, adapters=adapters, transport=transport,
                                          sleep_fn=lambda _s: None, cache=cache)

    result, _answered = run(FalStub(), request)
    assert result.paid and result.meta["booked"]["est_usd"] == pytest.approx(0.028)
    assert [(entry["link"], entry["est_usd"]) for entry in booked] == [(LIPSYNC_LINK, pytest.approx(0.028))]
    again = FalStub()
    kept, _answered = run(again, request)
    assert again.calls == [] and kept.meta["cached"] is True and len(booked) == 1

    # Another track is another request; its upload fails: nothing reached the queue, nothing is booked.
    (tmp_path / "other").mkdir()
    other = _lip_request(tmp_path / "other", media, clip_s=6)
    failing = FalStub(put_status=503)
    with pytest.raises(m.gen.NoRunnableLink):
        run(failing, other)
    assert len(booked) == 1
    assert not any(call["url"].endswith(LIPSYNC_APP) for call in failing.calls)
    key = gencache.request_key(m.gen.LIPSYNC, link, other)
    assert cache.lookup(key)["state"] == gencache.FAILED and cache.lookup(key)["booked"] is None


# ================================================================ 2. the price

def test_the_price_is_per_second_of_input_rounded_up_to_5_s_and_the_key_follows_the_track(tmp_path, media):
    from clipping.providers import gencache, pricing

    m = _mods()
    link = m.gen.parse_generation_chain(m.gen.LIPSYNC, LIPSYNC_LINK)[0]
    price = pricing.PRICES[LIPSYNC_LINK]
    assert (price.unit, price.usd) == ("second", 0.0028) and "$0.014 per 5 s" in price.note
    assert [m.lp.billed_seconds(s) for s in (2, 4, 5, 5.5, 6, 8, 10)] == [5, 5, 5, 10, 10, 10, 10]
    assert m.lp.estimate_for(link, 7).est_usd == pytest.approx(0.028)
    assert m.lp.estimate_for(link, 4).est_usd == pytest.approx(0.014)
    assert m.gen.chain_from_env(m.gen.LIPSYNC, {}) == [link] and m.gen.ENV_NAMES["lipsync"] == "LIPSYNC_CHAIN"
    assert "lipsync" not in m.gen.KINDS  # the Settings page's chain list keeps its five kinds
    # The journal's key: the clip's bytes, the track's bytes and the length -- never the paths.
    one = _lip_request(tmp_path, media)
    moved = dict(vars(one))
    copy_dir = tmp_path / "elsewhere"
    copy_dir.mkdir()
    (copy_dir / "t.wav").write_bytes(Path(one.extra["audio"]).read_bytes())
    moved["extra"] = {"audio": str(copy_dir / "t.wav"), "name": "other"}
    moved["out_dir"] = str(copy_dir)
    same = m.gen.GenRequest(**moved)
    assert gencache.request_key("lipsync", link, one) == gencache.request_key("lipsync", link, same)
    (copy_dir / "t.wav").write_bytes(b"RIFF another track")
    assert gencache.request_key("lipsync", link, one) != gencache.request_key("lipsync", link, same)


# ================================================================ 3. the dialogue track

def _hand_ec():
    return SimpleNamespace(template=None, language="fr", style_lock=None)


def _hand_shot(**extra):
    shot = {"shot_id": "sh02", "scene_id": "s01", "duration_s": 7.0, "lines": ["l01", "l02", "l03"],
            "subject_tags": ["@char_kiwilo", "@char_mangella", "#place_parloir:day"],
            "assets": {"clip": {"state": "current", "clip_s": 7}}}
    shot.update(extra)
    return shot


def _hand_script():
    return {"scenes": [{"scene_id": "s01", "lines": [
        {"line_id": "l01", "speaker": "char_kiwilo"}, {"line_id": "l02", "speaker": "char_broccolia"},
        {"line_id": "l03", "speaker": "char_mangella"}]}]}


def _hand_timeline(*, shot_start=10.0, starts=(10.5, 12.0, 14.0)):
    return {"shots": [{"shot_id": "sh02", "start_s": shot_start}],
            "lines": [{"line_id": f"l0{i + 1}", "start_s": start} for i, start in enumerate(starts)]}


def test_the_dialogue_track_holds_the_in_frame_lines_at_their_offsets_24_khz_mono_and_exactly_the_clip_long(tmp_path):
    m = _mods()
    audio = {"l01": _wav(tmp_path / "l01.wav", 1.5, rate=44100, channels=2),   # resampled to 24 kHz mono
             "l02": _wav(tmp_path / "l02.wav", 1.0),                            # off screen: never in the track
             "l03": _wav(tmp_path / "l03.wav", 2.0, rate=48000)}
    spec = m.lipsync.track_spec(_hand_ec(), _hand_script(), None, _hand_shot(), timeline=_hand_timeline(),
                                audio_of=lambda line: audio[line["line_id"]])
    assert [(row["line_id"], row["at_s"], row["from_s"]) for row in spec["lines"]] == [("l01", 0.5, 0.0),
                                                                                     ("l03", 4.0, 0.0)]
    assert spec["clip_s"] == 7 and spec["tempo"] == 1.0

    out = tmp_path / "track.wav"
    track = m.lipsync.build_track(spec, str(out))

    (channels, rate, width, frames), samples = _samples(out)
    assert (channels, rate, width, frames) == (1, 24000, 2, 7 * 24000)
    assert track["sha256"] == _sha(out) and track["lines"] == ["l01", "l03"] and track["hash"] == spec["hash"]
    assert _loud(samples, 0.0, 0.45) == 0 and _loud(samples, 2.05, 3.95) == 0 and _loud(samples, 6.05, 7.0) == 0
    assert _loud(samples, 0.6, 1.9) > 8000 and _loud(samples, 4.1, 5.9) > 8000
    # Deterministic: the same inputs, the same argv and the same bytes.
    again = m.lipsync.build_track(spec, str(tmp_path / "again.wav"))
    assert again["sha256"] == track["sha256"]
    assert m.lipsync.track_argv(spec, "x.wav") == m.lipsync.track_argv(copy.deepcopy(spec), "x.wav")


def test_an_off_screen_speaker_alone_gives_no_track_and_the_hash_follows_the_audio_and_the_timing(tmp_path):
    m = _mods()
    audio = {line: _wav(tmp_path / f"{line}.wav", 1.0) for line in ("l01", "l02", "l03")}
    script, timeline = _hand_script(), _hand_timeline()
    alone = _hand_shot(subject_tags=["@char_broccolia_twin", "#place_parloir:day"])
    assert m.lipsync.spoken_lines(script, alone) == []
    assert m.lipsync.track_spec(_hand_ec(), script, None, alone, timeline=timeline,
                                audio_of=lambda line: audio[line["line_id"]]) is None
    assert m.lipsync.dialogue_track(_hand_ec(), script, None, alone, str(tmp_path / "none.wav"),
                                    timeline=timeline) is None
    assert not (tmp_path / "none.wav").exists()

    def spec(**kwargs):
        return m.lipsync.track_spec(_hand_ec(), script, None, kwargs.pop("shot", _hand_shot()),
                                    timeline=kwargs.pop("timeline", timeline),
                                    audio_of=lambda line: audio[line["line_id"]])

    base = spec()
    assert spec()["hash"] == base["hash"]
    assert spec(timeline=_hand_timeline(starts=(10.6, 12.0, 14.0)))["hash"] != base["hash"]  # re-timed
    _wav(tmp_path / "l03.wav", 1.0, freq=700.0)  # re-voiced
    assert spec()["hash"] != base["hash"]
    # A clip slowed to cover its shot (DEC-250): the lines play faster, at offsets divided by the stretch.
    stretched = _hand_shot(duration_s=8.75, assets={"clip": {"state": "current", "clip_s": 7, "cover": "stretch"}})
    rows = spec(shot=stretched)
    assert rows["tempo"] == 1.25 and [row["at_s"] for row in rows["lines"]] == [0.4, 3.2]
    assert "atempo=1.25" in " ".join(m.lipsync.track_argv(rows, "t.wav"))
    # A line that starts before the shot is cut at its head; one past the clip's end is left out.
    early = spec(timeline=_hand_timeline(shot_start=10.0, starts=(9.5, 12.0, 17.5)))
    assert [(row["line_id"], row["at_s"], row["from_s"]) for row in early["lines"]] == [("l01", 0.0, 0.5)]


def _stream_md5(path, stream) -> str:
    result = subprocess.run(FFMPEG + ["-i", str(path), "-map", f"0:{stream}", "-f", "md5", "-"], check=True,
                            capture_output=True, text=True)
    return result.stdout.strip()


def test_the_kept_take_has_the_answers_picture_and_the_plain_clips_own_sound_or_none(tmp_path, media):
    """The track only drives the mouths: an ambience clip (Veo's) keeps its own
    sound under the synced picture; a silent clip (seedance's) stays silent --
    the answer's sound track (the driving audio) is never kept."""
    m = _mods()
    with_sound = m.lipsync.remux(str(media.synced), str(media.sound), str(tmp_path / "a.mp4"), plain_has_audio=True)
    assert m.clips.clip_has_audio(with_sound)
    assert _stream_md5(with_sound, "v") == _stream_md5(media.synced, "v")
    assert _stream_md5(with_sound, "a") == _stream_md5(media.sound, "a") != _stream_md5(media.synced, "a")
    silent = m.lipsync.remux(str(media.synced), str(media.silent), str(tmp_path / "b.mp4"), plain_has_audio=False)
    assert not m.clips.clip_has_audio(silent) and _stream_md5(silent, "v") == _stream_md5(media.synced, "v")
    with pytest.raises(m.lipsync.LipsyncError, match="ffmpeg could not keep the clip's own sound"):
        m.lipsync.remux(str(tmp_path / "missing.mp4"), str(media.silent), str(tmp_path / "c.mp4"),
                        plain_has_audio=False)


# ================================================================ 4. the policy and the 10 s cap

def test_the_quality_preset_lipsyncs_a_fully_animated_v2_story_unless_the_story_turns_it_off(store, tmp_path):
    from clipping.providers import budget

    m = _mods()
    profiles = budget.load_profiles()["profiles"]
    assert {name: profile.get("lipsync") for name, profile in profiles.items()} == {
        "free": "none", "one_dollar": "none", "quality": "kling"}
    story_id = oc._v2_unmade(store, tmp_path)
    story = store.get(story_id)
    assert m.policy.fully_animated(story) and m.policy.lipsync(story)
    store.update(story_id, lambda doc: doc["generation_profile"].update(lipsync="none"), now=NOW)
    assert m.policy.lipsync(store.get(story_id)) is False
    store.update(story_id, lambda doc: doc["generation_profile"].update(lipsync="kling", budget_profile="one_dollar"),
                 now=NOW)
    assert m.policy.lipsync(store.get(story_id)) is False  # not fully animated: never
    legacy = tas._episode(store, tmp_path)
    tce._tier(store, legacy, tier=2, budget_profile="quality")
    assert m.policy.lipsync(store.get(legacy)) is False  # not v2
    assert budget._profile_errors("x", {"lipsync": "veo"}) == [
        "profile 'x': lipsync must be one of none, kling, not 'veo'"]


def test_a_lipsyncing_story_buys_no_clip_past_10_s_and_its_storyboard_plans_a_10_5_s_scene_as_two(store,
                                                                                                    monkeypatch):
    m = _mods()
    story_id = amb._v2_storyboard_story(store, tier=2, lipsync=None)  # the quality preset's own: kling
    ec = m.storyboard.episode_common.load_context(store, story_id, 1)
    settings = dict(eps.SETTINGS, VIDEO_CHAIN=SEEDANCE, **tas.FAL)
    assert m.clips.sold_lengths(ec.story, SEEDANCE) == tuple(range(2, 11))
    assert m.clips.longest_clip_s(SEEDANCE) == 12 and m.clips.longest_clip_s(SEEDANCE, story=ec.story) == 10
    assert m.clips.sold_lengths(ec.story, "gemini/veo-3.1-lite") is None  # 8 s at most: nothing to cap
    assert m.storyboard.max_shot_s(ec, settings) == 10
    script = eps._script(store, story_id)
    scene = script["scenes"][1]
    monkeypatch.setattr(m.storyboard, "expected_scene_seconds", lambda _ec, _script, _scene: 10.5)
    limit = m.storyboard.max_shot_s(ec, settings)
    assert m.storyboard.beat_shot_count(ec, script, scene, limit_s=limit, rhythm=False) == (2, 2)
    # Turned off on the story: DEC-250's 12 s again, one shot.
    store.update(story_id, lambda doc: doc["generation_profile"].update(lipsync="none"), now=NOW)
    ec = m.storyboard.episode_common.load_context(store, story_id, 1)
    assert m.storyboard.max_shot_s(ec, settings) == 12
    assert m.storyboard.beat_shot_count(ec, script, scene, limit_s=12, rhythm=False) == (1, 1)


def test_the_plan_buys_10_s_at_most_and_slows_it_over_an_11_s_shot(store, tmp_path, media):
    m = _mods()
    story_id = oc._v2_unmade(store, tmp_path)
    board = copy.deepcopy(tas._board(store, story_id))
    board["shots"][0]["duration_s"] = 11.0
    adapters = _fakes(tmp_path, media).fakes.adapters
    video = m.clips.video_units(tas._ec(store, story_id), eps._script(store, story_id), board, None, env=QUALITY,
                                caps={}, committed_usd=0.0, adapters=adapters)
    row = video["plan"][0]
    assert (row["clip_s"], row["cover"], row["stretch"]) == (10, "stretch", 1.1)  # seedance sells 11 and 12 s
    assert max(item["clip_s"] for item in video["plan"]) <= 10 and video["ready"] is True


# ================================================================ 5. the estimate and the cap

def test_the_estimate_prices_one_lipsync_per_clip_with_an_on_screen_line_and_the_paid_check_counts_it(store,
                                                                                                    tmp_path,
                                                                                                    media):
    m = _mods()
    story_id = oc._v2_unmade(store, tmp_path)
    seams = _fakes(tmp_path, media, llm=False)
    units = tce._units(store, story_id, QUALITY, adapters=seams.fakes.adapters)
    video = units["video"]
    lip = video["lipsync"]
    spoken = _spoken(store, story_id)
    assert spoken and lip["count"] == len(spoken) and lip["counted"] is True and lip["link"] == LIPSYNC_LINK
    assert [row["shot_id"] for row in lip["plan"]] == [row["shot_id"] for row in video["plan"]
                                                      if row["shot_id"] in spoken]
    expected = sum(round(0.0028 * m.lp.billed_seconds(row["clip_s"]), 4) for row in lip["plan"])
    assert lip["est_usd"] == pytest.approx(expected) and lip["seconds"] == sum(r["billed_s"] for r in lip["plan"])
    clips_usd = sum(row["est_usd"] for row in video["plan"])
    assert video["est_usd"] == pytest.approx(clips_usd + lip["est_usd"])
    assert f"+ ${lip['est_usd']:.3f} lip-sync ({lip['count']} clips)" in video["message"]
    assert sorted(lip["skipped"]) == sorted(set(row["shot_id"] for row in video["plan"]) - set(spoken))

    # The fast track's check before anything is bought: the lipsync is a paid part, inside every cap.
    from clipping.aistory.steps import fast_track

    whole = fast_track.whole_episode_units(tas._ec(store, story_id), units, env=QUALITY)
    verdict = fast_track.paid_verdict(whole, ep=1, fully_animated=True)
    assert f"{lip['count']} lip-syncs on {LIPSYNC_LINK} (est ${lip['est_usd']:.3f})" in verdict["parts"]
    assert verdict["est_usd"] == pytest.approx(units["est_usd"] + video["est_usd"])

    # An episode cap the clips fit and the lipsync does not: refused whole, before anything is made.
    cap = units["est_usd"] + clips_usd + lip["est_usd"] / 2
    message = tft.stopped(store, story_id, seams.fakes, settings=dict(QUALITY, PER_EPISODE_CAP_USD=f"{cap:.4f}"))
    assert "would go over a cap" in message and f"{lip['count']} lip-syncs on {LIPSYNC_LINK}" in message
    assert seams.image.requests == [] and seams.video.requests == [] and seams.lip.requests == []


def test_a_story_that_does_not_lipsync_estimates_exactly_as_before(store, tmp_path, media):
    story_id = oc._v2_unmade(store, tmp_path)
    adapters = _fakes(tmp_path, media, llm=False).fakes.adapters
    on = tce._units(store, story_id, QUALITY, adapters=adapters)["video"]
    store.update(story_id, lambda doc: doc["generation_profile"].update(lipsync="none"), now=NOW)
    off = tce._units(store, story_id, QUALITY, adapters=adapters)["video"]
    assert "lipsync" not in off and "lip-sync" not in off["message"]
    assert off["est_usd"] == pytest.approx(on["est_usd"] - on["lipsync"]["est_usd"])
    assert off["plan"] == on["plan"]  # no shot here is past 10 s: the same clips


# ================================================================ 6. the one click, end to end

def test_the_one_click_lipsyncs_every_clip_with_an_on_screen_line_and_renders_the_synced_takes(store, tmp_path,
                                                                                               media):
    m = _mods()
    story_id, seams, summary, log = _made(store, tmp_path, media)

    assert summary["steps"]["render"]["state"] == "completed"
    board = tas._board(store, story_id)
    script = eps._script(store, story_id)
    spoken = _spoken(store, story_id)
    assert spoken and len(seams.video.requests) == len(board["shots"])
    assert sorted(seams.lip.names()) == sorted(f"shot_{shot_id[2:]}.lipsync" for shot_id in spoken)
    rows = {row["shot_id"]: row for row in summary["steps"]["assets"]["video"]["lipsync"]["failed"]}
    assert rows == {}
    lip_summary = summary["steps"]["assets"]["video"]["lipsync"]
    assert sorted(lip_summary["done"]) == sorted(spoken) and lip_summary["link"] == LIPSYNC_LINK
    for shot in board["shots"]:
        shot_id, clip = shot["shot_id"], shot["assets"]["clip"]
        plain = _clip_file(store, story_id, f"shot_{shot_id[2:]}.mp4")
        if shot_id not in spoken:
            assert "lipsync" not in clip and shot["assets"]["video"] == f"assets/clips/shot_{shot_id[2:]}.mp4"
            continue
        record = clip["lipsync"]
        index = seams.lip.names().index(f"shot_{shot_id[2:]}.lipsync")
        request = seams.lip.requests[index]
        billed = m.lp.billed_seconds(clip["clip_s"])
        assert shot["assets"]["video"] == f"assets/clips/shot_{shot_id[2:]}.lipsync.mp4"
        assert record["state"] == "current" and record["link"] == LIPSYNC_LINK
        assert record["clip_sha256"] == _sha(plain) and record["audio_sha256"] == seams.lip.audio[index]
        assert record["est_usd"] == pytest.approx(0.0028 * billed) and record["billed_s"] == billed
        assert record["lines"] == [line["line_id"] for line in m.lipsync.spoken_lines(script, shot)]
        assert record["cache_key"] and record["generated_at"] and "reason" not in record
        assert request.duration_s == clip["clip_s"] <= 10 and len(request.references) == 1
        synced = _clip_file(store, story_id, f"shot_{shot_id[2:]}.lipsync.mp4")
        assert synced.is_file() and m.clips.clip_has_audio(str(synced)) is False  # the driving track is never kept
        assert any(line == f"👄 Shot {shot_id}: lips synced to {len(record['lines'])} "
                           f"line{'' if len(record['lines']) == 1 else 's'} ({clip['clip_s']} s, "
                           f"${record['est_usd']:.3f})" for line in log), shot_id
    assert any(line.startswith(f"👄 Lip-sync on {LIPSYNC_LINK}: {len(spoken)} clip") for line in log)
    # The ledger: one row a lipsync, in seconds rounded up to 5.
    lip_rows = _lip_rows(store, story_id)
    assert len(lip_rows) == len(spoken)
    assert all(row["unit"] == "second" and row["qty"] % 5 == 0 and row["paid"] and row["step"] == "assets"
               and row["est_usd"] == pytest.approx(0.0028 * row["qty"]) for row in lip_rows)
    # The render staged the lip-synced takes; the board validates.
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    staged = {item["id"]: item["source"] for item in manifest["inputs"] if item["role"] == "shot"}
    assert {shot_id: staged[shot_id] for shot_id in spoken} == {
        shot_id: f"assets/clips/shot_{shot_id[2:]}.lipsync.mp4" for shot_id in spoken}
    assert m.schemas.storyboard_errors(board) == []
    # The assets approval fingerprints the lip-synced files.
    doc = tas._assets_doc(store, story_id)
    assert doc["approved"]["by"] == "fast_track"
    # Continue repeats nothing: no clip, no lipsync, no call.
    again = _fakes(tmp_path, media, llm=False)
    tft.run(store, story_id, again.fakes, settings=QUALITY)
    assert again.video.requests == [] and again.lip.requests == []


def test_a_failed_lipsync_keeps_the_plain_clip_names_the_shot_and_the_render_goes_on(store, tmp_path, media):
    story_id = oc._v2_unmade(store, tmp_path)
    spoken = _spoken(store, story_id)
    failing = spoken[0]
    lip = FakeLipsync(media.synced, fail_for={f"shot_{failing[2:]}.lipsync"})
    seams = _fakes(tmp_path, media, lip=lip)

    summary, log = tft.run(store, story_id, seams.fakes, settings=QUALITY)

    assert summary["steps"]["render"]["state"] == "completed"
    shot = next(s for s in tas._board(store, story_id)["shots"] if s["shot_id"] == failing)
    record = shot["assets"]["clip"]["lipsync"]
    assert record["state"] == "failed" and "no face found" in record["reason"]
    assert shot["assets"]["video"] == f"assets/clips/shot_{failing[2:]}.mp4"  # never a missing video
    failed = summary["steps"]["assets"]["video"]["lipsync"]["failed"]
    assert [item["shot_id"] for item in failed] == [failing]
    assert any(line.startswith(f"✖ Lip-sync {failing} failed: ") and line.endswith("Its plain clip is kept.")
               for line in log)
    # Booked: the provider took it, and settled it without a clip.
    assert len(_lip_rows(store, story_id)) == len(spoken)
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    staged = {item["id"]: item["source"] for item in manifest["inputs"] if item["role"] == "shot"}
    assert staged[failing] == f"assets/clips/shot_{failing[2:]}.mp4"


def test_a_regenerated_clip_and_a_re_voiced_line_are_lipsynced_again_and_nothing_else(store, tmp_path, media,
                                                                                     monkeypatch):
    from clipping.aistory.steps import entities, regenerate

    story_id, seams, _summary, _log = _made(store, tmp_path, media)
    spoken = _spoken(store, story_id)
    target = spoken[0]
    before = {shot["shot_id"]: shot["assets"]["clip"]["lipsync"] for shot in tas._board(store, story_id)["shots"]
              if shot["shot_id"] in spoken}

    # The clip regenerate redoes the clip, then its lipsync, on the same gates.
    monkeypatch.setattr(entities, "fresh_seed", lambda: 4242)
    again = _fakes(tmp_path, media, llm=False)
    ctx, log = eps._ctx(store, story_id, step="regenerate", settings=QUALITY,
                        params={"target": f"shot:1:{target}:video", "note": "a slower push-in"})
    result = regenerate.run(ctx, adapters=again.fakes.adapters, time_fn=eps.Clock(0.0), sleep_fn=lambda _s: None)
    assert result["lipsync"] == "current"
    assert again.video.names() == [f"shot_{target[2:]}"] and again.lip.names() == [f"shot_{target[2:]}.lipsync"]
    shot = next(s for s in tas._board(store, story_id)["shots"] if s["shot_id"] == target)
    assert shot["assets"]["video"] == f"assets/clips/shot_{target[2:]}.lipsync.mp4"
    assert shot["assets"]["clip"]["lipsync"]["clip_sha256"] != before[target]["clip_sha256"]
    assert shot["assets"]["clip"]["lipsync"]["clip_sha256"] == _sha(
        _clip_file(store, story_id, f"shot_{target[2:]}.mp4"))

    # A line re-voiced: only its shot's lipsync is stale, and the assets step makes it again.
    m = _mods()
    script = eps._script(store, story_id)
    other = next(shot_id for shot_id in spoken if shot_id != target) if len(spoken) > 1 else target
    board = tas._board(store, story_id)
    other_shot = next(s for s in board["shots"] if s["shot_id"] == other)
    line_id = m.lipsync.spoken_lines(script, other_shot)[0]["line_id"]
    voice = _fakes(tmp_path, media, llm=False, tone=5000)  # another tone: the line's audio is other bytes
    tas._regenerate_line(store, story_id, line_id, adapters=voice.fakes.adapters, settings=QUALITY)
    ec = tas._ec(store, story_id)
    stale = [shot["shot_id"] for shot in tas._board(store, story_id)["shots"] if shot["shot_id"] in spoken
             and not m.lipsync.is_current(ec, eps._script(store, story_id), tas._board(store, story_id), shot,
                                          link=LIPSYNC_LINK)]
    assert stale == [other]
    third = _fakes(tmp_path, media, llm=False)
    summary, log = tas._run(store, story_id, adapters=third.fakes.adapters, settings=QUALITY)
    assert third.video.requests == [] and third.lip.names() == [f"shot_{other[2:]}.lipsync"]
    lip = summary["video"]["lipsync"]
    assert lip["done"] == [other] and lip["reused"] == len(spoken) - 1 and lip["failed"] == []


def test_without_a_lipsync_adapter_or_key_the_clips_are_kept_and_the_estimate_says_so(store, tmp_path, media):
    story_id = oc._v2_unmade(store, tmp_path)
    seams = _fakes(tmp_path, media, llm=False)
    adapters = dict(seams.fakes.adapters)
    adapters.pop(("lipsync", "fal"))
    video = tce._units(store, story_id, QUALITY, adapters=adapters)["video"]
    assert video["lipsync"]["counted"] is False and video["lipsync"]["reason"] == "no adapter for fal lipsync"
    assert f"No lip-sync: {LIPSYNC_LINK}: no adapter for fal lipsync." in video["message"]
    assert video["est_usd"] == pytest.approx(sum(row["est_usd"] for row in video["plan"]))


# ================================================================ 7. RC-M3 and the dashboard

def test_a_clip_recorded_before_the_lipsync_validates_and_a_synced_take_needs_a_current_lipsync(store, tmp_path,
                                                                                                media):
    m = _mods()
    story_id, _seams, _summary, _log = _made(store, tmp_path, media)
    board = tas._board(store, story_id)
    spoken = set(_spoken(store, story_id))
    shot = next(s for s in board["shots"] if s["shot_id"] in spoken)
    # Stored before DEC-258: no lipsync key, the plain clip -- validates unchanged.
    old = copy.deepcopy(board)
    for item in old["shots"]:
        item["assets"]["clip"].pop("lipsync", None)
        item["assets"]["video"] = f"assets/clips/shot_{item['shot_id'][2:]}.mp4"
    assert m.schemas.storyboard_errors(old) == []
    # The lip-synced take named while its lipsync failed is refused.
    bad = copy.deepcopy(board)
    target = next(s for s in bad["shots"] if s["shot_id"] == shot["shot_id"])
    target["assets"]["clip"]["lipsync"]["state"] = "failed"
    assert any("the lip-synced take is set only with a current lipsync" in error
               for error in m.schemas.storyboard_errors(bad))
    # Another shot's take, or a made-up name, is not this shot's clip.
    target["assets"]["video"] = "assets/clips/shot_99.lipsync.mp4"
    assert any("is not" in error for error in m.schemas.storyboard_errors(bad))
    from clipping.aistory import store as store_mod

    assert store_mod.EPISODE_ASSET_NAME_PATTERNS["clips"].fullmatch("shot_03.lipsync.mp4")
    assert not store_mod.EPISODE_ASSET_NAME_PATTERNS["clips"].fullmatch("shot_03.lipsync.wav")


def test_the_episode_page_carries_the_lipsync_and_plays_the_synced_take(store, tmp_path, media):
    from clipping.aistory import workflow

    story_id, _seams, _summary, _log = _made(store, tmp_path, media)
    spoken = _spoken(store, story_id)
    clips = workflow.episode_clips(store, store.get(story_id), 1, env=QUALITY)
    clip = clips["shots"][spoken[0]]
    assert clip["name"] == f"shot_{spoken[0][2:]}.lipsync.mp4"
    assert clip["lipsync"]["state"] == "current" and clip["lipsync"]["link"] == LIPSYNC_LINK
    assert clip["lipsync"]["lines"] and clip["lipsync"]["reason"] is None
    silent = [shot_id for shot_id in clips["shots"] if shot_id not in spoken]
    for shot_id in silent:
        assert "lipsync" not in clips["shots"][shot_id]


def test_the_dashboard_badge_reads_the_clips_lipsync():
    pane = Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src" / "pages" / "story" / "episode"
    controls = (pane / "storyboard" / "ClipControls.jsx").read_text(encoding="utf-8")
    cards = (pane / "storyboard" / "AssetsCards.jsx").read_text(encoding="utf-8")
    assert re.search(r"lipsync\.state === 'current'\)\s*return <Badge tone=\"success\">Lip-synced</Badge>", controls)
    assert '<Badge tone="warning">Lip-sync failed</Badge>' in controls
    assert "{clip.lipsync && <LipsyncBadge lipsync={clip.lipsync} />}" in controls
    # The reason reaches visible text, never only a title (the F8 pattern).
    assert re.search(r"<p className=\"form-hint\">Lip-sync failed: \{clip\.lipsync\.reason", controls)
    assert "video.lipsync.counted" in cards and "lip-sync (" in cards and "No lip-sync: " in cards
