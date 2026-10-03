"""Gemini lines voiced before the tail guard, cleaned in place by the next
``assets`` run, for free (``voice_lines.LineMeasurement.guard_tails``,
``tts.guard_kept_take``): no line is spoken again (the voices must not
change), the WAV is cut and faded where its static was, its sidecar says so
(``tail_guard``), and the script and the storyboard follow the shorter line
the way a measurement re-times them -- no revision moves, no approval is
cleared. A cached answer kept before the guard is cleaned as it lands in
``assets/voice/``, so a later cache hit cannot bring the noise back.

The episode is ``tests/test_story_assets_step.py``'s (its ``hermetic`` and
``store`` fixtures, used as they are); "before the guard" is an adapter
writing what Gemini answered verbatim, with no ``tail_guard``, as
``GeminiTtsAdapter`` did. Signals are ``tests/test_tts_tail.py``'s.
Stdlib + pytest (the CI environment, DEC-012).
"""

from __future__ import annotations

import hashlib
import json
import os
import wave

import pytest

import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_measure as tsm
import test_tts_tail as ttt
from clipping.providers import tts
from clipping.providers.generation import GenResult
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are

MANGELLA = eps.MANGELLA
NOW = eps.NOW
RATE = 24000
_SIGNALS = {}


def noisy() -> bytes:
    """One second of speech, a 100 ms gap, 400 ms of static: 1.5 s."""
    if "noisy" not in _SIGNALS:
        _SIGNALS["noisy"] = ttt.pcm(ttt.voiced(1.0), ttt.silence(0.1), ttt.static(0.4))
    return _SIGNALS["noisy"]


def ambiguous() -> bytes:
    """Speech, a 70 ms gap, a 150 ms burst: too short for the frames alone."""
    if "ambiguous" not in _SIGNALS:
        _SIGNALS["ambiguous"] = ttt.pcm(ttt.voiced(1.0), ttt.silence(0.07), ttt.static(0.15, amp=0.4),
                                        ttt.silence(0.1))
    return _SIGNALS["ambiguous"]


class LegacyGemini:
    """Gemini TTS as it answered before the tail guard: the PCM written
    verbatim, a ``line_timing_v1`` sidecar with no ``tail_guard``."""

    def __init__(self, pcm=noisy):
        self.pcm = pcm
        self.calls = []

    def estimate(self, link, request):
        return tts.GEMINI_TTS.estimate(link, request)

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.calls.append(request.text)
        name = (request.extra or {}).get("name") or "gemini_flash-lite-tts"
        path = os.path.join(request.out_dir, f"{name}.wav")
        pcm = self.pcm()
        with wave.open(path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(pcm)
        duration = round(len(pcm) / 2 / RATE, 3)
        timing = tts._write_timing(request.out_dir, name, duration_s=duration, words=[], source=tts.SOURCE_DURATION,
                                   provider="gemini", voice=request.voice)
        return GenResult(provider="gemini", model=link.model, paths=(path, timing),
                         meta={"duration_s": duration, "source": tts.SOURCE_DURATION})


# ------------------------------------------------------------------ helpers

def _voiced_before_the_guard(store, tmp_path, *, pcm=noisy):
    """An episode whose Mangella lines were spoken by Gemini before the
    guard (the rest by Edge); returns ``(story_id, her line ids)``."""
    story_id = tas._episode(store, tmp_path)
    tsm._pin(store, story_id, MANGELLA, "gemini", "Kore")
    legacy = LegacyGemini(pcm)
    with pytest.MonkeyPatch.context() as before_the_guard:
        # The code of then: whatever answered was kept as it came.
        before_the_guard.setattr(tts, "guard_kept_take", lambda *args, **kwargs: None, raising=False)
        summary, _log = tas._run(store, story_id, adapters=tas._adapters(image=tas.FakeImage(), gemini=legacy))
    theirs = [line["line_id"] for line in tas._lines(eps._script(store, story_id)) if line["speaker"] == MANGELLA]
    # One call per distinct text: a line repeated word for word is the cache's.
    assert len(theirs) == 5 and sorted(set(legacy.calls)) == sorted({_line(eps._script(store, story_id), line_id)["text"]
                                                                       for line_id in theirs})
    assert summary["failed"] == []
    for line_id in theirs:
        assert "tail_guard" not in _sidecar(store, story_id, line_id)
        assert _sidecar(store, story_id, line_id)["duration_s"] == round(len(pcm()) / 2 / RATE, 3)
    return story_id, theirs


def _voice(store, story_id, name):
    return tas._episode_file(store, story_id, "assets", "voice", name)


def _sidecar(store, story_id, line_id):
    return json.loads(_voice(store, story_id, f"line_{line_id[1:]}.json").read_text(encoding="utf-8"))


def _write_sidecar(store, story_id, line_id, data):
    _voice(store, story_id, f"line_{line_id[1:]}.json").write_text(json.dumps(data), encoding="utf-8")


def _wav_seconds(path):
    with wave.open(str(path)) as wav:
        return wav.getnframes() / wav.getframerate()


def _line(script, line_id):
    return next(line for line in tas._lines(script) if line["line_id"] == line_id)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _voice_files(store, story_id, ext):
    folder = tas._episode_file(store, story_id, "assets", "voice")
    return {path.name: path.read_bytes() for path in sorted(folder.glob(f"*.{ext}"))}


# ================================================================ in place

def test_lines_voiced_before_the_guard_are_cleaned_in_place_on_the_next_assets_run(store, tmp_path):
    story_id, theirs = _voiced_before_the_guard(store, tmp_path)
    script_before, board_before = eps._script(store, story_id), tas._board(store, story_id)
    mp3s, ledger_before = _voice_files(store, story_id, "mp3"), tas._ledger(store, story_id)
    gemini = tsm.NeverCalled()

    summary, log = tas._run(store, story_id, adapters=tas._adapters(gemini=gemini))

    # Nothing is spoken again: the voices must not change.
    assert gemini.calls == 0 and tas._ledger(store, story_id) == ledger_before
    script, board = eps._script(store, story_id), tas._board(store, story_id)
    cut = 0.0
    for line_id in theirs:
        sidecar = _sidecar(store, story_id, line_id)
        guard = sidecar["tail_guard"]
        assert guard["version"] == 2 and guard["reason"] == "noise_after_gap" and guard["original_s"] == 1.5
        assert 1.0 <= guard["kept_s"] <= 1.05
        assert sidecar["duration_s"] == guard["kept_s"]
        assert round(_wav_seconds(_voice(store, story_id, f"line_{line_id[1:]}.wav")), 3) == guard["kept_s"]
        line, old = _line(script, line_id), _line(script_before, line_id)
        assert line["timing"] == dict(old["timing"], duration_s=guard["kept_s"])
        cut += guard["trimmed_s"]
    # The others (Edge) are left exactly as they were.
    assert _voice_files(store, story_id, "mp3") == mp3s
    # Re-timed the way a measurement re-times: no revision, no approval moves.
    assert script["timing"]["total_s"] < script_before["timing"]["total_s"]
    assert (script["rev"], script["approved_at"]) == (script_before["rev"], script_before["approved_at"])
    assert board["approved_at"] == board_before["approved_at"]
    assert [shot["duration_s"] for shot in board["shots"]] != [shot["duration_s"] for shot in board_before["shots"]]
    assert sum(shot["duration_s"] for shot in board["shots"]) < sum(shot["duration_s"] for shot in board_before["shots"])
    assert summary["tails"] == {"checked": 5, "cleaned": 5, "cut_s": round(cut, 3), "suspect": []}
    assert f"🔇 5 Gemini line endings cleaned ({cut:.1f} s of static cut)" in log

    # Once cleaned, never again: a third run touches nothing.
    wavs = _voice_files(store, story_id, "wav")
    again, _log = tas._run(store, story_id, adapters=tas._adapters(gemini=tsm.NeverCalled()))
    assert _voice_files(store, story_id, "wav") == wavs
    assert "tails" not in again, "a run the guard saw no line of keeps the summary it always had"


def test_a_cached_answer_kept_before_the_guard_is_cleaned_as_it_lands(store, tmp_path):
    """The generation cache hands back what Gemini answered then, noise and
    all: the episode's copy is cleaned, the cache's own file is left as it
    was answered, and any later hit is cleaned the same way."""
    story_id, theirs = _voiced_before_the_guard(store, tmp_path)
    cache = {path.name: path.read_bytes() for path in tas._cache_dir(store, story_id).iterdir()}
    for line_id in theirs:
        _voice(store, story_id, f"line_{line_id[1:]}.wav").unlink()  # the audio gone: spoken again
    gemini = tsm.NeverCalled()

    summary, log = tas._run(store, story_id, adapters=tas._adapters(gemini=gemini))

    assert gemini.calls == 0, "every line came back from the cache"
    assert summary["lines"]["measured"] == 5 and summary["failed"] == []
    script = eps._script(store, story_id)
    for line_id in theirs:
        sidecar = _sidecar(store, story_id, line_id)
        assert sidecar["tail_guard"]["reason"] == "noise_after_gap"
        assert _line(script, line_id)["timing"]["duration_s"] == sidecar["duration_s"] == \
            sidecar["tail_guard"]["kept_s"]
        assert round(_wav_seconds(_voice(store, story_id, f"line_{line_id[1:]}.wav")), 3) == sidecar["duration_s"]
    kept = {path.name: path.read_bytes() for path in tas._cache_dir(store, story_id).iterdir()
            if path.name in cache and not path.name.endswith(".json")}
    assert kept and all(cache[name] == data for name, data in kept.items()), "the cache's own files are untouched"
    assert summary["tails"]["cleaned"] == 5
    assert any(line.startswith("🔇 5 Gemini line endings cleaned") for line in log)


def test_an_aligned_line_is_cut_150_ms_after_its_last_word(store, tmp_path):
    """A burst the frames alone leave (short, after a short gap): the line
    whose words were aligned is cut after its last word; the others are only
    faded, their length kept."""
    story_id, theirs = _voiced_before_the_guard(store, tmp_path, pcm=ambiguous)
    aligned, plain = theirs[0], theirs[1]
    sidecar = _sidecar(store, story_id, aligned)
    sidecar.update(words=[{"word": "Bonjour", "start": 0.1, "end": 0.6}, {"word": "toi", "start": 0.65, "end": 0.98}],
                   words_source="alignment", aligned_by="groq/whisper-large-v3-turbo")
    _write_sidecar(store, story_id, aligned, sidecar)

    summary, _log = tas._run(store, story_id, adapters=tas._adapters(gemini=tsm.NeverCalled()))

    guard = _sidecar(store, story_id, aligned)["tail_guard"]
    assert guard["reason"] == "aligned_end" and 1.0 <= guard["kept_s"] <= 1.13
    assert _sidecar(store, story_id, aligned)["words"][-1] == {"word": "toi", "start": 0.65, "end": 0.98}
    other = _sidecar(store, story_id, plain)
    assert other["tail_guard"]["reason"] == "none" and other["duration_s"] == other["tail_guard"]["original_s"]
    assert summary["tails"]["checked"] == 5 and summary["tails"]["cleaned"] == 1


def test_a_voice_regenerate_take_still_describes_the_cleaned_audio(store, tmp_path):
    """``assets.json`` keeps a take only while the audio is the file it made
    (its sha256): cleaning that file in place moves the record with it."""
    story_id, theirs = _voiced_before_the_guard(store, tmp_path)
    line_id = theirs[2]
    path = _voice(store, story_id, f"line_{line_id[1:]}.wav")
    doc = tas._assets_doc(store, story_id)
    doc["lines"][line_id]["take"] = {"id": "0123456789abcdef", "note": "plus doux", "audio_sha256": _sha(path)}
    store.write_episode_doc(story_id, 1, "assets.json", doc, now=NOW)

    tas._run(store, story_id, adapters=tas._adapters(gemini=tsm.NeverCalled()))

    take = tas._assets_doc(store, story_id)["lines"][line_id]["take"]
    assert take == {"id": "0123456789abcdef", "note": "plus doux", "audio_sha256": _sha(path)}
    assert _sidecar(store, story_id, line_id)["tail_guard"]["trimmed_s"] > 0


def test_a_stop_mid_pass_leaves_the_script_in_step_with_every_cleaned_file(store, tmp_path, monkeypatch):
    """Each cleaned line is saved as it is cleaned, as a measurement saves
    each line: a cancel after two leaves those two timed by their cleaned
    files, the others as they were (and due for the next run)."""
    from clipping.cancel import Cancelled

    story_id, theirs = _voiced_before_the_guard(store, tmp_path)
    ctx, _log = tas._ctx(store, story_id)
    real, seen = tts.guard_kept_take, []

    def then_stop(*args, **kwargs):
        seen.append(args[0])
        new = real(*args, **kwargs)
        if len(seen) == 2:
            ctx.cancel.cancel()
        return new

    monkeypatch.setattr(tts, "guard_kept_take", then_stop)
    with pytest.raises(Cancelled):
        tas._run(store, story_id, adapters=tas._adapters(gemini=tsm.NeverCalled()), ctx=ctx)

    script = eps._script(store, story_id)
    guarded = [line_id for line_id in theirs if "tail_guard" in _sidecar(store, story_id, line_id)]
    assert guarded == theirs[:2]
    for line_id in theirs:
        assert _line(script, line_id)["timing"]["duration_s"] == _sidecar(store, story_id, line_id)["duration_s"]
    assert _line(script, theirs[0])["timing"]["duration_s"] < 1.5 == _line(script, theirs[2])["timing"]["duration_s"]


def test_a_line_the_guard_cannot_read_is_left_as_it_is(store, tmp_path):
    """A Gemini sidecar whose audio is no 16-bit mono WAV: nothing is
    touched, nothing fails."""
    story_id, theirs = _voiced_before_the_guard(store, tmp_path)
    path = _voice(store, story_id, f"line_{theirs[0][1:]}.wav")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"\x01\x00\x02\x00" * 36000)
    before = path.read_bytes()

    summary, _log = tas._run(store, story_id, adapters=tas._adapters(gemini=tsm.NeverCalled()))

    assert path.read_bytes() == before and "tail_guard" not in _sidecar(store, story_id, theirs[0])
    assert summary["tails"]["checked"] == 4 and summary["failed"] == []
