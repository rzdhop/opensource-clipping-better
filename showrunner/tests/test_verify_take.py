"""The clip check (stage 1.4): alignment of heard words to scripted lines, on injected word lists; the
ffmpeg decode when numpy is there; the real model only with SHOWRUNNER_STT_TESTS=1."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from showrunner import verify as V  # noqa: E402

FFMPEG = shutil.which("ffmpeg") is not None
HAS_NUMPY = importlib.util.find_spec("numpy") is not None
HAS_WHISPER = importlib.util.find_spec("faster_whisper") is not None


def W(*items):
    """Heard words from ``(word, start, end)`` triples."""
    return [{"word": w, "start": s, "end": e, "prob": 1.0} for w, s, e in items]


# What large-v3 heard on stories/_stage0/a/paloma_fr_s33.mp4 (2026-10-08, this host).
PALOMA = W(("Tu", 0.0, 0.18), ("souris", 0.18, 0.78), ("à", 0.78, 0.84), ("ton", 0.84, 1.04),
           ("téléphone.", 1.04, 1.5), ("C", 1.96, 2.4), ("'est", 2.4, 2.6), ("qui,", 2.6, 2.74), ("le", 2.96, 3.46),
           ("kiwi?", 3.46, 3.94))
PALOMA_LINE = [("paloma", "Tu souris à ton téléphone. C'est qui, le kiwi ?")]


def test_tokens_drop_accents_punctuation_and_split_elisions():
    assert V.tokens("Tu souris à ton téléphone. C'est qui, le kiwi ?") == \
        ["tu", "souris", "a", "ton", "telephone", "c", "est", "qui", "le", "kiwi"]
    assert V.tokens("R… comme Rida ? Non.") == ["r", "comme", "rida", "non"]
    assert V.tokens("t’as") == ["t", "as"]


def test_a_clean_take_is_ok_with_its_speech_span():
    v = V.verify_take("x.mp4", PALOMA_LINE, "fr", words=PALOMA, duration_s=5.04)
    assert v["state"] == "ok" and v["matched"] == 1.0 and v["in_order"]
    assert (v["start_s"], v["end_s"], v["extra_after_s"]) == (0.0, 3.94, 0.0)
    assert v["lines"][0]["speaker"] == "paloma" and v["lines"][0]["heard"] == 10


def test_one_missed_word_still_passes_and_a_wrong_line_does_not():
    missed = [w for w in PALOMA if w["word"] != "ton"]
    assert V.verify_take("x", PALOMA_LINE, "fr", words=missed, duration_s=5.0)["matched"] == 0.9
    wrong = W(("Bonjour", 0.2, 0.6), ("tout", 0.6, 0.9), ("le", 0.9, 1.0), ("monde", 1.0, 1.4))
    v = V.verify_take("x", PALOMA_LINE, "fr", words=wrong, duration_s=5.0)
    assert v["state"] == "mismatch" and v["matched"] < V.MIN_MATCHED


def test_no_words_is_no_speech_and_a_line_ending_at_the_cut_is_late():
    assert V.verify_take("x", PALOMA_LINE, "fr", words=[], duration_s=5.0)["state"] == "no_speech"
    assert V.verify_take("x", PALOMA_LINE, "fr", words=PALOMA, duration_s=4.0)["state"] == "late"


def test_invented_speech_after_the_line_is_measured_not_counted():
    words = PALOMA + W(("Allez,", 4.1, 4.4), ("dis-moi", 4.4, 4.9))
    v = V.verify_take("x", PALOMA_LINE, "fr", words=words, duration_s=6.0)
    assert v["state"] == "ok" and v["end_s"] == 3.94 and v["extra_after_s"] == pytest.approx(0.8)


THREE = [("paloma", "C'est qui, le kiwi ?"), ("marie_jeanne", "Personne, Paloma !"), ("rida", "Personne ? Sympa.")]


def test_three_speakers_in_order_and_a_repeated_word_stays_with_its_line():
    words = W(("C'est", 0.3, 0.5), ("qui,", 0.5, 0.7), ("le", 0.7, 0.8), ("kiwi?", 0.8, 1.2),
              ("Personne,", 2.0, 2.5), ("Paloma!", 2.5, 3.0), ("Personne?", 4.0, 4.6), ("Sympa.", 4.8, 5.3))
    v = V.verify_take("x", THREE, "fr", words=words, duration_s=10.0)
    assert v["state"] == "ok" and v["in_order"]
    assert [(r["speaker"], r["start_s"], r["end_s"]) for r in v["lines"]] == \
        [("paloma", 0.3, 1.2), ("marie_jeanne", 2.0, 3.0), ("rida", 4.0, 5.3)]


def test_a_swapped_or_missing_line_is_a_mismatch():
    swapped = W(("Personne?", 0.3, 0.8), ("Sympa.", 0.9, 1.3), ("C'est", 2.0, 2.2), ("qui", 2.2, 2.4), ("le", 2.4, 2.5),
                ("kiwi", 2.5, 2.9), ("Personne", 3.5, 4.0), ("Paloma", 4.0, 4.5))
    assert V.verify_take("x", THREE, "fr", words=swapped, duration_s=10.0)["state"] == "mismatch"
    missing = W(("C'est", 0.3, 0.5), ("qui", 0.5, 0.7), ("le", 0.7, 0.8), ("kiwi", 0.8, 1.2), ("Personne", 2.0, 2.5),
                ("Paloma", 2.5, 3.0))
    v = V.verify_take("x", THREE, "fr", words=missing, duration_s=10.0)
    assert v["state"] == "mismatch" and v["lines"][2]["start_s"] is None


@pytest.mark.skipif(not (FFMPEG and HAS_NUMPY), reason="ffmpeg and numpy")
def test_decode_gives_mono_16k_float32(tmp_path):
    path = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=2",
                    "-ac", "2", "-ar", "44100", str(path)], check=True, capture_output=True)
    audio = V.decode_audio(str(path))
    assert str(audio.dtype) == "float32" and abs(len(audio) - 32000) <= 160


CLIP = os.path.join(ROOT, "stories", "_stage0", "a", "paloma_fr_s33.mp4")


@pytest.mark.skipif(not (os.environ.get("SHOWRUNNER_STT_TESTS") == "1" and HAS_WHISPER and os.path.exists(CLIP)),
                    reason="real model: SHOWRUNNER_STT_TESTS=1, faster-whisper and the stage-0 clip (≈ 45 s)")
def test_real_model_hears_the_batch_a_take():
    assert V.verify_take(CLIP, PALOMA_LINE, "fr")["state"] == "ok"
