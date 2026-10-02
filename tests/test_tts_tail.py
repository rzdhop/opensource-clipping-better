"""The TTS tail guard (``clipping.providers.tts_tail``): the burst of static
Gemini TTS appends to the very end of a clip is found and cut, never a word.

No real Gemini sample is available, so every signal is built here: speech is
a harmonic voiced signal (f0 gliding 120-250 Hz, six harmonics) under a
syllable-like envelope; a word-final fricative is short high-passed noise
glued to it; the artifact is louder broadband noise. Deterministic (seeded),
stdlib + pytest (the CI environment, DEC-012). The module is imported inside
the tests, so on the parent commit each test fails on its own.
"""

from __future__ import annotations

import array
import ast
import math
import pathlib
import random

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RATE = 24000


def _tail():
    from clipping.providers import tts_tail

    return tts_tail


# ------------------------------------------------------------------ signals

def voiced(seconds, *, rate=RATE, amp=0.5, syllable_s=0.22):
    """Speech-like voiced sound: f0 gliding 120-250 Hz, harmonics 1..6 at
    1/k, a syllable envelope dipping to 15 % between syllables, and a 30 ms
    natural decay at its end."""
    n = int(seconds * rate)
    out, phase = [], 0.0
    for i in range(n):
        t = i / rate
        f0 = 120 + 130 * (0.5 + 0.5 * math.sin(2 * math.pi * 0.7 * t))
        phase += 2 * math.pi * f0 / rate
        wave = sum(math.sin(k * phase) / k for k in range(1, 7)) / 1.9
        env = 0.15 + 0.85 * math.sin(math.pi * ((t % syllable_s) / syllable_s)) ** 2
        decay = min(1.0, (n - i) / (0.03 * rate))
        out.append(amp * wave * max(env, 0.15) * decay)
    return out


def fricative(seconds, *, rate=RATE, amp=0.12, seed=1):
    """A word-final "s"/"ch": high-passed (first-difference) noise with a
    10 ms attack and a 30 ms release."""
    rng = random.Random(seed)
    n = int(seconds * rate)
    white = [rng.gauss(0.0, 1.0) for _ in range(n + 1)]
    out = []
    for i in range(n):
        env = min(1.0, i / (0.01 * rate), (n - i) / (0.03 * rate))
        out.append(amp * 0.5 * (white[i + 1] - white[i]) * env)
    return out


def static(seconds, *, rate=RATE, amp=0.6, seed=2):
    """The artifact: loud broadband noise, abrupt at both ends."""
    rng = random.Random(seed)
    return [amp * rng.uniform(-1.0, 1.0) for _ in range(int(seconds * rate))]


def silence(seconds, *, rate=RATE, seed=3):
    """Near-silence: a dither of +/- 2 LSB, as a codec leaves it."""
    rng = random.Random(seed)
    return [rng.randint(-2, 2) / 32767 for _ in range(int(seconds * rate))]


def pcm(*parts) -> bytes:
    samples = [x for part in parts for x in part]
    return array.array("h", [int(max(-1.0, min(1.0, x)) * 32767) for x in samples]).tobytes()


def samples_of(data: bytes) -> array.array:
    out = array.array("h")
    out.frombytes(data[: len(data) - len(data) % 2])
    return out


def seconds(data: bytes, rate=RATE) -> float:
    return len(data) / 2 / rate


# ------------------------------------------------------------- the detector

@pytest.mark.parametrize("gap_s,static_s", [(0.08, 0.3), (0.12, 0.6), (0.2, 0.9)])
def test_an_artifact_after_a_gap_is_cut_at_the_gap(gap_s, static_s):
    tail = _tail()
    speech = voiced(1.4)
    data = pcm(speech, silence(gap_s), static(static_s))

    out, report = tail.clean(data, RATE)

    assert report["reason"] == "noise_after_gap"
    assert report["version"] == tail.TAIL_GUARD_VERSION == 1
    kept = seconds(out)
    # Cut inside the gap: all of the speech kept, at most 50 ms of the gap.
    assert 1.4 <= kept <= 1.4 + min(gap_s, 0.06), kept
    assert report["kept_s"] == round(kept, 3)
    assert report["original_s"] == round(seconds(data), 3)
    assert report["trimmed_s"] == pytest.approx(report["original_s"] - report["kept_s"], abs=0.002)
    assert report["trimmed_s"] >= static_s


def test_an_artifact_after_a_final_fricative_and_a_gap_keeps_the_fricative():
    tail = _tail()
    data = pcm(voiced(1.3), fricative(0.15), silence(0.1), static(0.5))

    out, report = tail.clean(data, RATE)

    assert report["reason"] == "noise_after_gap"
    assert 1.45 <= seconds(out) <= 1.51


@pytest.mark.parametrize("static_s", [0.3, 0.5, 0.9])
def test_an_artifact_glued_to_the_speech_is_cut_keeping_at_most_120_ms_of_it(static_s):
    tail = _tail()
    data = pcm(voiced(1.5), static(static_s))

    out, report = tail.clean(data, RATE)

    assert report["reason"] == "noise_run"
    kept = seconds(out)
    assert 1.5 <= kept <= 1.5 + 0.13, kept
    assert report["trimmed_s"] >= static_s - 0.13


def test_a_quieter_artifact_in_a_line_with_no_real_silence_is_still_cut():
    """About 26 dB under the speech, with no silence in the line for the
    floor's percentile to land in: it lands in the noise itself."""
    tail = _tail()
    for data, reason in ((pcm(voiced(1.2), static(0.5, amp=0.02)), "noise_run"),
                         (pcm(voiced(1.2), silence(0.1), static(0.5, amp=0.02)), "noise_after_gap")):
        out, report = tail.clean(data, RATE)
        assert report["reason"] == reason and 1.2 <= seconds(out) <= 1.33


def test_a_burst_whose_zcr_dips_in_places_is_still_noise():
    """Noise a little darker than white (one-pole low-passed, ZCR ~0.3):
    a frame here and there dips under the threshold, which one frame alone
    never makes speech."""
    tail = _tail()
    rng, y, dark = random.Random(5), 0.0, []
    for _ in range(int(0.5 * RATE)):
        y = 0.6 * y + 0.4 * rng.uniform(-1.0, 1.0)
        dark.append(1.2 * y)

    out, report = tail.clean(pcm(voiced(1.2), dark), RATE)

    assert report["reason"] == "noise_run" and 1.2 <= seconds(out) <= 1.33


def test_a_fricative_followed_by_a_glued_artifact_keeps_120_ms_of_the_noise_run():
    tail = _tail()
    data = pcm(voiced(1.2), fricative(0.12), static(0.6))

    out, report = tail.clean(data, RATE)

    assert report["reason"] == "noise_run"
    assert 1.2 <= seconds(out) <= 1.2 + 0.13


@pytest.mark.parametrize("fric_s", [0.06, 0.12, 0.18])
def test_a_word_final_fricative_alone_is_not_cut(fric_s):
    tail = _tail()
    data = pcm(voiced(1.2), fricative(fric_s), silence(0.25))

    out, report = tail.clean(data, RATE)

    assert report["reason"] == "none" and report["trimmed_s"] == 0.0
    assert len(out) == len(data)


def test_a_stop_closure_then_a_final_s_is_not_mistaken_for_an_artifact():
    """"...fax", "...texts": a stop's closure (a short silence) before the
    final /s/ looks like speech, gap, noise -- too short a gap and too short
    a noise to be the artifact."""
    tail = _tail()
    data = pcm(voiced(1.2), silence(0.07), fricative(0.16), silence(0.2))

    out, report = tail.clean(data, RATE)

    assert report["reason"] == "none" and len(out) == len(data)


def test_a_clean_line_with_trailing_silence_is_only_faded():
    tail = _tail()
    data = pcm(voiced(1.6), silence(0.4))

    out, report = tail.clean(data, RATE)

    assert report == {"version": 1, "trimmed_s": 0.0, "reason": "none", "original_s": 2.0, "kept_s": 2.0}
    assert len(out) == len(data)
    before, after = samples_of(data), samples_of(out)
    middle = slice(int(0.5 * RATE), int(1.5 * RATE))
    assert after[middle] == before[middle], "only the edges are touched"


def test_the_line_never_ends_or_starts_on_a_click():
    """A fade-in (~5 ms) and a fade-out (~25 ms) at the new end: the first
    and last samples are near 0 and the level ramps."""
    tail = _tail()
    loud = [0.8 * math.sin(2 * math.pi * 180 * i / RATE) + 0.1 for i in range(int(1.0 * RATE))]
    for data in (pcm(loud), pcm(voiced(1.5), static(0.5)), pcm(voiced(1.4), silence(0.1), static(0.4))):
        out, _report = tail.clean(data, RATE)
        s = samples_of(out)
        assert abs(s[0]) <= 50 and abs(s[-1]) <= 50
        head = max(abs(x) for x in s[: int(0.001 * RATE)])
        assert head < 0.25 * 32767
    s = samples_of(tail.clean(pcm(loud), RATE)[0])
    near_end = max(abs(x) for x in s[-int(0.003 * RATE):])
    before_fade = max(abs(x) for x in s[-int(0.06 * RATE):-int(0.03 * RATE)])
    assert near_end < 0.2 * before_fade


def test_an_aligned_end_cuts_what_the_detector_left_150_ms_after_the_last_word():
    """A burst too short, after a gap too short, for the detector alone; the
    alignment says the last word ended with the speech."""
    tail = _tail()
    data = pcm(voiced(1.2), silence(0.07), static(0.15, amp=0.4), silence(0.1))

    _out, alone = tail.clean(data, RATE)
    out, report = tail.clean(data, RATE, speech_end_s=1.18)

    assert alone["reason"] == "none"
    assert report["reason"] == "aligned_end"
    assert 1.2 <= seconds(out) <= 1.33


def test_an_aligned_end_is_ignored_when_speech_follows_it():
    tail = _tail()
    data = pcm(voiced(1.6), silence(0.3))

    out, report = tail.clean(data, RATE, speech_end_s=0.8)

    assert report["reason"] == "none" and len(out) == len(data)


def test_an_aligned_end_never_cuts_pure_trailing_silence():
    tail = _tail()
    data = pcm(voiced(1.2), silence(0.6))

    out, report = tail.clean(data, RATE, speech_end_s=1.2)

    assert report["reason"] == "none" and len(out) == len(data)


def test_an_aligned_end_does_not_move_a_cut_the_detector_made():
    tail = _tail()
    data = pcm(voiced(1.3), silence(0.1), static(0.5))

    _o1, alone = tail.clean(data, RATE)
    _o2, aligned = tail.clean(data, RATE, speech_end_s=1.75)

    assert alone == aligned and alone["reason"] == "noise_after_gap"


# ----------------------------------------------------------------- the limits

def test_never_more_than_1_5_s_is_cut():
    tail = _tail()
    data = pcm(voiced(4.0), silence(0.1), static(1.8))

    out, report = tail.clean(data, RATE)

    assert report["reason"] == "suspect" and report["trimmed_s"] == 0.0
    assert len(out) == len(data)


def test_never_less_than_half_the_line_is_left():
    tail = _tail()
    data = pcm(voiced(0.5), silence(0.1), static(0.9))

    out, report = tail.clean(data, RATE)

    assert report["reason"] == "suspect" and report["trimmed_s"] == 0.0
    assert len(out) == len(data)
    assert report["kept_s"] == report["original_s"]


def test_an_all_noise_file_is_left_whole():
    tail = _tail()
    data = pcm(static(1.2))

    out, report = tail.clean(data, RATE)

    assert report["trimmed_s"] == 0.0 and report["reason"] in ("none", "suspect")
    assert len(out) == len(data)


@pytest.mark.parametrize("data", [b"", b"ab", b"abc", b"\x00\x00" * 24000, b"\x00\x01" * 24000,
                                  b"\x10\x00" * 100],
                         ids=["empty", "one_sample", "odd_bytes", "digital_silence", "constant", "shorter_than_a_frame"])
def test_odd_files_lose_nothing(data):
    tail = _tail()

    out, report = tail.clean(data, RATE)

    assert report["trimmed_s"] == 0.0 and report["reason"] == "none"
    assert len(out) == len(data) - len(data) % 2
    assert report["original_s"] == report["kept_s"] == round((len(data) // 2) / RATE, 3)


def test_another_sample_rate_is_read_as_given():
    tail = _tail()
    rate = 16000
    data = pcm(voiced(1.4, rate=rate), silence(0.12, rate=rate), static(0.5, rate=rate))

    out, report = tail.clean(data, rate)

    assert report["reason"] == "noise_after_gap"
    assert 1.4 <= seconds(out, rate) <= 1.46


def test_analyse_says_what_clean_would_do_and_changes_nothing():
    tail = _tail()
    data = pcm(voiced(1.4), silence(0.1), static(0.5))

    plan = tail.analyse(data, RATE)
    _out, report = tail.clean(data, RATE)

    assert plan["report"] == report
    assert plan["cut_sample"] == len(_out) // 2
    assert {"floor_db", "speech_db", "speech_end_s"} <= set(plan)


# ------------------------------------------------------------ the file helpers

def _wav(path, data, *, rate=RATE, channels=1, width=2):
    import wave

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(data)


def test_clean_wav_rewrites_the_file_in_place_and_leaves_no_temp_file(tmp_path):
    tail = _tail()
    path = tmp_path / "line_01.wav"
    _wav(path, pcm(voiced(1.4), silence(0.1), static(0.5)))

    report = tail.clean_wav(str(path))

    assert report["reason"] == "noise_after_gap"
    pcm_after, rate = tail.read_wav(str(path))
    assert rate == RATE and round(seconds(pcm_after), 3) == report["kept_s"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["line_01.wav"]


@pytest.mark.parametrize("channels,width", [(2, 2), (1, 1)])
def test_clean_wav_leaves_any_other_format_alone(tmp_path, channels, width):
    tail = _tail()
    path = tmp_path / "line_01.wav"
    _wav(path, b"\x01\x02" * 4800, channels=channels, width=width)
    before = path.read_bytes()

    assert tail.clean_wav(str(path)) is None and tail.read_wav(str(path)) is None
    assert path.read_bytes() == before
    (tmp_path / "not.wav").write_bytes(b"ID3 an mp3")
    assert tail.clean_wav(str(tmp_path / "not.wav")) is None


# ---------------------------------------------------------------- the module

def test_the_guard_is_stdlib_only():
    tree = ast.parse((ROOT / "clipping" / "providers" / "tts_tail.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "array", "math", "os", "sys", "tempfile", "wave"}, imported
