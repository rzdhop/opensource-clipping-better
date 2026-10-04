"""Native speech: the take of a speaking clip (plan 22, stage 4).

Once a native-speech story's speaking clip is kept, the assets step takes
it, for free: the clip's own sound (real ffmpeg, a small fixture clip) is
transcribed (a fake transcriber here) and aligned against the line; ``ok``
when at least 75 % of the line's words are heard and the last one ends 0.1 s
before the clip does. A take with speech becomes the line's audio -- the
clip's sound from the first word to the last, with an aligned sidecar -- so
the line is measured as any voiced line is, and the shot lasts its clip,
cut 0.3 s after the last word when the clip runs more than a second past
it. ``mismatch`` and ``no_speech`` are flagged; an agent story buys one
retake within its budget's ``speech_retake``, booked; ``stt_unavailable``
splits the words evenly over the planned window and names the missing key.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_native_speech_plan as nsp
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_clip_estimate import timings_path  # noqa: F401 - the measured clip timings under tmp_path

NOW = tas.NOW
FAST = nsp.FAST
SHA = "0" * 64
PAID = {"GEMINI_PAID_API_KEY": "test-gemini-paid-key", "ALLOW_PAID": "1", "PER_EPISODE_CAP_USD": "40",
        "DAILY_CAP_USD": "80", "PER_STORY_CAP_USD": "200"}


def _require_ffmpeg():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.fail("ffmpeg and ffprobe are needed: the take cuts the clip's real sound (apt-get install ffmpeg)")


def make_clip(path, seconds, *, hz=1000.0, sound=True) -> str:
    """A small clip as a speaking model answers one: ``testsrc`` and a sine."""
    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"testsrc=size=64x112:rate=24:duration={seconds:g}"]
    if sound:
        argv += ["-f", "lavfi", "-i", f"sine=frequency={hz:g}:sample_rate=48000:duration={seconds:g}"]
    argv += ["-c:v", "libx264", "-pix_fmt", "yuv420p"] + (["-c:a", "aac"] if sound else ["-an"]) + [os.fspath(path)]
    result = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr[-600:]
    return os.fspath(path)


def words(text, *, start=0.5, each=0.3):
    """A transcription of *text* from *start*, *each* seconds a word (its
    punctuation alone -- a French "?" -- is no word an STT hears)."""
    from clipping.aistory import wordtiming

    spoken = [word for word in text.split() if wordtiming.normalise(word)]
    return [{"word": word, "start": round(start + i * each, 3), "end": round(start + (i + 1) * each - 0.02, 3)}
            for i, word in enumerate(spoken)]


class Transcriber:
    """The STT stand-in: each call answers the next of *answers* (a list of
    words, or a callable of the line); records every path."""

    def __init__(self, *answers, by="groq/whisper-large-v3-turbo"):
        self.answers = list(answers)
        self.by = by
        self.paths = []

    def __call__(self, path, *, language, on_log, cancel):
        self.paths.append(path)
        assert language == "fr"
        return self.answers.pop(0), self.by


class FakeVeo:
    """The speaking link, faked: every generate is one submit (journaled)
    answering the next of *clips* (paths of real mp4s)."""

    def __init__(self, clips):
        self.clips = list(clips)
        self.requests = []

    def estimate(self, link, request):
        from clipping.providers import pricing

        return pricing.estimate(link, int(request.duration_s))

    def probe(self, link, **_kwargs):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, on_submit=None, **_kwargs):
        from clipping.providers.generation import GenResult

        self.requests.append(request)
        if on_submit is not None:
            on_submit({"request_id": f"op-{len(self.requests)}", "status_url": "https://x.test/s",
                       "response_url": "https://x.test/r"})
        path = os.path.join(request.out_dir, f"{request.extra['name']}.mp4")
        shutil.copyfile(self.clips.pop(0), path)
        return GenResult(provider=link.provider, model=link.model, paths=(path,), seed=request.seed)


def _host(store, story_id, *, transcribe=None, settings=None, video=None):
    from clipping.aistory.steps import assets, entities

    ec = tas._ec(store, story_id)
    ctx, log = tas._ctx(store, story_id, settings=tas._settings(**(settings or {})))
    adapters = tas._adapters()
    if video is not None:
        adapters[("video", "gemini")] = video
    tools = entities.Tools(time_fn=eps.Clock(0.0), sleep_fn=lambda _s: None, adapters=adapters, transport=None)
    host = assets._Assets(ctx, ec, tools=tools, transcribe=transcribe)
    host.script, host.storyboard = assets.require_approved(ec)
    host.video = {"made": 0, "reused": 0, "failed": [], "seconds": 0, "usd": 0.0, "link": FAST, "route": "paid"}
    return host, log


def _shot(host, clip_s):
    return next(shot for shot in host.storyboard["shots"] if shot.get("speaks") and shot["clip_s"] == clip_s)


def _line(host, shot):
    return next(line for scene in host.script["scenes"] for line in scene["lines"] if line["line_id"] == shot["lines"][0])


def _put_clip(store, story_id, host, shot, source):
    """*source* kept as *shot*'s current clip, as ``apply_clip`` keeps one."""
    from clipping.aistory.steps import clips

    dest = store.episode_asset_path(story_id, 1, "clips", clips.clip_name(shot["shot_id"]), create=True)
    shutil.copyfile(source, dest)
    shot["assets"]["clip"] = {"state": "current", "link": FAST, "route": "paid", "clip_s": shot["clip_s"],
                              "est_usd": 0.6, "prompt_hash": SHA, "image_sha256": SHA, "cache_key": None,
                              "generated_at": NOW}
    shot["assets"]["video"] = clips.clip_rel(shot["shot_id"])
    host.write_board()


def _probe(path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=noprint_wrappers=1:nokey=1", os.fspath(path)], capture_output=True, text=True)
    return float(out.stdout.strip())


# =================================================================== ok

def test_a_take_that_speaks_its_line_becomes_the_lines_audio_and_the_shot_is_cut_after_its_last_word(store,
                                                                                                    tmp_path):
    """Fail-first. A 6 s clip speaking its line from 0.5 s, 0.3 s a word:
    ``ok`` (every word heard), the line's audio is the clip's sound from its
    first word to its last with its aligned sidecar (words from 0.0), the
    line measured as any voiced line; the clip runs more than a second past
    its last word, so the shot is cut 0.3 s after it; the line starts on the
    episode timeline where its shot starts plus 0.5 s."""
    from clipping.aistory import timing, wordtiming
    from clipping.aistory.steps import assets, voice_lines

    _require_ffmpeg()
    story_id = nsp.planned_story(store)
    probe_host, _log = _host(store, story_id)
    shot = _shot(probe_host, 6)
    text = _line(probe_host, shot)["text"]
    heard = words(text)
    host, log = _host(store, story_id, transcribe=Transcriber(heard))
    shot, line = _shot(host, 6), None
    line = _line(host, shot)
    _put_clip(store, story_id, host, shot, make_clip(tmp_path / "speaks.mp4", 6))
    host.native_take_shot(shot)

    take = tas._board(store, story_id)
    stored = next(item for item in take["shots"] if item["shot_id"] == shot["shot_id"])
    record = stored["assets"]["clip"]["native_speech"]
    end = heard[-1]["end"]
    assert (record["state"], record["matched"], record["start_s"], record["end_s"]) == ("ok", 1.0, 0.5, end)
    assert record["aligned_by"] == "groq/whisper-large-v3-turbo" and record["clip_real_s"] == pytest.approx(6.0, abs=0.05)
    assert stored["duration_s"] == pytest.approx(round(end + 0.3, 3), abs=1 / 30)
    script = eps._script(store, story_id)
    measured = next(item for scene in script["scenes"] for item in scene["lines"] if item["line_id"] == line["line_id"])
    assert voice_lines.is_measured(tas._ec(store, story_id), measured)
    assert measured["timing"]["duration_s"] == pytest.approx(end - 0.5)
    audio = assets.line_audio_path(tas._ec(store, story_id), measured)
    assert audio.endswith(".wav") and _probe(audio) == pytest.approx(end - 0.5, abs=0.05)
    sidecar = assets.read_sidecar(tas._ec(store, story_id), line["line_id"])
    assert wordtiming.source_of(sidecar) == (wordtiming.ALIGNMENT, "groq/whisper-large-v3-turbo")
    assert sidecar["words"][0]["start"] == 0.0 and sidecar["provider"] == "native"
    offsets = timing.line_offsets(script, script["timing"], tas._ec(store, story_id).template, storyboard=take)
    start = sum(item["duration_s"] for item in take["shots"][:stored["order"] - 1])
    assert offsets[line["line_id"]][0] == pytest.approx(start + 0.5, abs=1e-3)
    assert any("its clip speaks its line (100 % of the words heard" in entry for entry in log)


def test_a_clip_that_runs_at_most_a_second_past_its_last_word_keeps_its_length(store, tmp_path):
    _require_ffmpeg()
    story_id = nsp.planned_story(store)
    probe_host, _log = _host(store, story_id)
    text = _line(probe_host, _shot(probe_host, 6))["text"]
    host, _log = _host(store, story_id, transcribe=Transcriber(words(text, start=2.4, each=0.35)))
    shot = _shot(host, 6)
    _put_clip(store, story_id, host, shot, make_clip(tmp_path / "late.mp4", 6))
    host.native_take_shot(shot)
    stored = next(item for item in tas._board(store, story_id)["shots"] if item["shot_id"] == shot["shot_id"])
    assert stored["assets"]["clip"]["native_speech"]["state"] == "ok"
    assert stored["duration_s"] == pytest.approx(6.0, abs=0.05)


def test_a_clip_longer_than_planned_is_judged_against_its_real_length(store, tmp_path):
    """An 8 s clip (an upload) for a line planned at 4 s, its speech ending at
    5.0 s -- past the planned length, inside the clip: ``ok``; the shot is
    cut 0.3 s after the last word."""
    _require_ffmpeg()
    story_id = nsp.planned_story(store)
    probe_host, _log = _host(store, story_id)
    shot = _shot(probe_host, 4)
    text = _line(probe_host, shot)["text"]
    count = len(words(text))
    heard = words(text, start=5.0 - 0.3 * count + 0.02)
    assert heard[-1]["end"] == pytest.approx(5.0)
    host, _log = _host(store, story_id, transcribe=Transcriber(heard))
    shot = _shot(host, 4)
    _put_clip(store, story_id, host, shot, make_clip(tmp_path / "upload.mp4", 8))
    host.native_take_shot(shot)
    stored = next(item for item in tas._board(store, story_id)["shots"] if item["shot_id"] == shot["shot_id"])
    record = stored["assets"]["clip"]["native_speech"]
    assert (record["state"], record["end_s"]) == ("ok", pytest.approx(5.0))
    assert record["clip_real_s"] == pytest.approx(8.0, abs=0.05)
    assert stored["duration_s"] == pytest.approx(5.3, abs=1 / 30)


# =================================================================== flagged

def test_a_take_that_does_not_speak_its_line_is_flagged_and_a_studio_story_is_told_how_to_retake(store, tmp_path):
    _require_ffmpeg()
    story_id = nsp.planned_story(store)
    host, log = _host(store, story_id, transcribe=Transcriber(words("bonjour tout le monde ici")))
    shot = _shot(host, 6)
    _put_clip(store, story_id, host, shot, make_clip(tmp_path / "other.mp4", 6))
    host.native_take_shot(shot, video={"link": FAST, "speech": {"speech_price": 0.10}}, row={"clip_s": 6})
    record = next(item for item in tas._board(store, story_id)["shots"]
                  if item["shot_id"] == shot["shot_id"])["assets"]["clip"]["native_speech"]
    assert record["state"] == "mismatch" and record["matched"] < 0.75 and record["heard"] == "bonjour tout le monde ici"
    assert host.video["takes"]["flagged"][0]["shot_id"] == shot["shot_id"]
    assert any(f"regenerate 'shot:1:{shot['shot_id']}:video' for another take" in entry for entry in log)


def test_a_take_with_no_speech_leaves_the_line_without_audio_and_the_render_says_why(store, tmp_path, monkeypatch):
    from clipping.aistory.steps import render, voice_lines
    from clipping.aistory.steps.llm_call import StepFailed

    _require_ffmpeg()
    story_id = nsp.planned_story(store)
    host, _log = _host(store, story_id, transcribe=Transcriber([]))
    shot = _shot(host, 6)
    _put_clip(store, story_id, host, shot, make_clip(tmp_path / "mute.mp4", 6))
    host.native_take_shot(shot)
    host.write_assets_doc()
    record = next(item for item in tas._board(store, story_id)["shots"]
                  if item["shot_id"] == shot["shot_id"])["assets"]["clip"]["native_speech"]
    assert record["state"] == "no_speech" and record["start_s"] is None
    line = _line(host, shot)
    assert not voice_lines.is_measured(tas._ec(store, story_id), line)
    # The images are not this test's: every shot has a current one.
    still = tmp_path / "still.png"
    still.write_bytes(tas.PNG)
    monkeypatch.setattr(render.assets_step, "shot_image_path", lambda *_args: str(still))
    monkeypatch.setattr(render.assets_step, "outdated_images", lambda *_args, **_kwargs: [])
    with pytest.raises(StepFailed) as caught:
        render.require_renderable(tas._ec(store, story_id))
    assert "no take of" in str(caught.value) and line["line_id"] in str(caught.value)


def test_without_an_stt_key_the_words_split_evenly_over_the_planned_window_and_the_key_is_named(store, tmp_path):
    from clipping.aistory import wordtiming
    from clipping.aistory.steps import assets

    _require_ffmpeg()
    story_id = nsp.planned_story(store)
    host, log = _host(store, story_id)
    shot = _shot(host, 6)
    _put_clip(store, story_id, host, shot, make_clip(tmp_path / "unchecked.mp4", 6))
    host.native_take_shot(shot)
    record = next(item for item in tas._board(store, story_id)["shots"]
                  if item["shot_id"] == shot["shot_id"])["assets"]["clip"]["native_speech"]
    assert (record["state"], record["start_s"], record["end_s"]) == ("stt_unavailable", 0.35, 5.65)
    assert "GROQ_API_KEY" in record["reason"]
    sidecar = assets.read_sidecar(tas._ec(store, story_id), shot["lines"][0])
    assert wordtiming.source_of(sidecar)[0] == wordtiming.EVEN_SPLIT
    assert any("split evenly over the planned window (approximate)" in entry and "GROQ_API_KEY" in entry
               for entry in log)


# =================================================================== the retake

def _retake_story(store, tmp_path, cap_usd=None, monkeypatch=None):
    if cap_usd is not None:
        from clipping.providers import budget as budget_mod

        data = budget_mod.load_profiles()
        data["profiles"]["native_speech"]["speech_retake"]["cap_usd"] = cap_usd
        path = tmp_path / "profiles.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(budget_mod, "PROFILES_PATH", str(path))
    story_id = nsp.planned_story(store, mode="agent")
    return story_id


def _retake_host(store, story_id, tmp_path, monkeypatch, transcribe, veo):
    from clipping.aistory.steps import assets

    monkeypatch.setattr(assets, "keyframe_problem", lambda *_args, **_kwargs: None)
    host, log = _host(store, story_id, transcribe=transcribe, settings=PAID, video=veo)
    host.gates = host.open_asset_gates()
    shot = _shot(host, 6)
    image = store.episode_asset_path(story_id, 1, "shots", f"shot_{shot['shot_id'][2:]}.png", create=True)
    with open(image, "wb") as fh:
        fh.write(tas.PNG)
    shot["assets"]["image"] = f"assets/shots/shot_{shot['shot_id'][2:]}.png"
    _put_clip(store, story_id, host, shot, make_clip(tmp_path / "first.mp4", 6))
    return host, log, shot


def test_an_agent_story_retakes_a_flagged_clip_once_within_its_budget_and_books_it(store, tmp_path, monkeypatch):
    """The first take speaks other words; the agent buys one more clip (a
    fresh seed, the same link), booked at $0.60 on the ledger and in
    ``speech_retakes``; its take speaks the line."""
    _require_ffmpeg()
    story_id = _retake_story(store, tmp_path)
    probe_host, _log = _host(store, story_id)
    text = _line(probe_host, _shot(probe_host, 6))["text"]
    veo = FakeVeo([make_clip(tmp_path / "second.mp4", 6, hz=700.0)])
    host, log, shot = _retake_host(store, story_id, tmp_path, monkeypatch,
                                   Transcriber(words("rien à voir"), words(text)), veo)
    host.native_take_shot(shot, video={"link": FAST, "route_class": "paid", "template": None,
                                       "speech": {"speech_price": 0.10}},
                          row={"shot_id": shot["shot_id"], "clip_s": 6, "link": FAST})
    assert len(veo.requests) == 1 and veo.requests[0].duration_s == 6
    stored = next(item for item in tas._board(store, story_id)["shots"] if item["shot_id"] == shot["shot_id"])
    assert stored["assets"]["clip"]["retakes"] == 1 and stored["assets"]["clip"]["native_speech"]["state"] == "ok"
    retakes = tas._assets_doc(store, story_id)["speech_retakes"]
    assert retakes["spent_usd"] == pytest.approx(0.6) and retakes["shots"] == {shot["shot_id"]: 1}
    booked = [row for row in tas._ledger(store, story_id) if row.get("unit") == "second"]
    assert [round(row["est_usd"], 4) for row in booked] == [0.6]
    assert host.video["takes"]["retaken"] == [shot["shot_id"]]


def test_a_retake_over_the_retake_budget_is_not_bought(store, tmp_path, monkeypatch):
    _require_ffmpeg()
    story_id = _retake_story(store, tmp_path, cap_usd=0.5, monkeypatch=monkeypatch)
    veo = FakeVeo([])
    host, log, shot = _retake_host(store, story_id, tmp_path, monkeypatch, Transcriber(words("rien à voir")), veo)
    host.native_take_shot(shot, video={"link": FAST, "route_class": "paid", "template": None,
                                       "speech": {"speech_price": 0.10}},
                          row={"shot_id": shot["shot_id"], "clip_s": 6, "link": FAST})
    assert veo.requests == []
    assert any("a retake (est $0.600) would bring the retakes to $0.60 of their $0.50" in entry for entry in log)
