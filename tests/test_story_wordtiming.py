"""Word timings of one spoken line in spec 6.4's order of truth (AI Story
phase 4, stage 8; DEC-165): the provider's words, an opt-in alignment of a
transcription to the script's words, or an even split labelled approximate.

Pure functions, no disk. Stdlib + pytest (DEC-012). The module is imported
inside the tests, so on the parent commit each test fails on its own.
"""

from __future__ import annotations

import pytest


def _new():
    from clipping.aistory import wordtiming

    return wordtiming


def _heard(*items):
    return [{"word": word, "start": start, "end": end} for word, start, end in items]


def _starts(words):
    return [word["start"] for word in words]


def test_the_sources_are_the_documents_three_in_spec_order():
    wt = _new()
    from clipping.aistory import schemas

    assert (wt.PROVIDER, wt.ALIGNMENT, wt.EVEN_SPLIT) == schemas.WORD_SOURCES == ("provider", "alignment",
                                                                                  "even_split")


def test_an_even_split_divides_the_duration_over_the_scripts_words():
    wt = _new()
    words = wt.even_split("Tu n'as pas le choix.", 2.5)
    assert [word["word"] for word in words] == ["Tu", "n'as", "pas", "le", "choix."]
    assert _starts(words) == [0.0, 0.5, 1.0, 1.5, 2.0]
    assert words[-1]["end"] == 2.5 and all(w["end"] > w["start"] for w in words)
    assert wt.even_split("", 3.0) == [] and wt.even_split("   ", 3.0) == []


def test_every_matched_word_takes_its_transcribed_span():
    wt = _new()
    words = wt.align("Ce soir, quelqu'un quitte l'île.", _heard(
        ("ce", 0.10, 0.30), ("soir", 0.35, 0.70), ("quelqu'un", 0.80, 1.30), ("quitte", 1.35, 1.70),
        ("l'île", 1.75, 2.20)), 2.4)
    assert [(w["word"], w["start"], w["end"]) for w in words] == [
        ("Ce", 0.1, 0.3), ("soir,", 0.35, 0.7), ("quelqu'un", 0.8, 1.3), ("quitte", 1.35, 1.7),
        ("l'île.", 1.75, 2.2)]


def test_case_accents_typographic_apostrophes_and_punctuation_do_not_count():
    wt = _new()
    assert wt.normalise("L’Île,") == wt.normalise("l'ile") == "l'ile"
    assert wt.normalise("«Chérie!»") == "cherie"
    words = wt.align("Tu n'as pas le choix, chérie.", _heard(
        ("TU", 0.0, 0.2), ("n’as", 0.25, 0.5), ("pas", 0.55, 0.8), ("le", 0.85, 1.0), ("choix", 1.05, 1.4),
        ("Cherie", 1.5, 2.0)), 2.2)
    assert _starts(words) == [0.0, 0.25, 0.55, 0.85, 1.05, 1.5]


def test_words_the_transcription_missed_are_interpolated_between_their_matched_neighbours():
    wt = _new()
    # "vraiment" and "je" were not heard; "te suivre" was heard as one garbled word.
    words = wt.align("Tu crois vraiment que je vais te suivre", _heard(
        ("tu", 0.0, 0.2), ("crois", 0.3, 0.6), ("que", 1.2, 1.4), ("vais", 1.8, 2.0), ("tesuivre", 2.1, 2.8)), 3.0)
    spans = {w["word"]: (w["start"], w["end"]) for w in words}
    assert spans["Tu"] == (0.0, 0.2) and spans["crois"] == (0.3, 0.6) and spans["que"] == (1.2, 1.4)
    # One missing word between "crois" (ends 0.6) and "que" (starts 1.2).
    assert spans["vraiment"] == (0.6, 1.2)
    assert spans["je"] == (1.4, 1.8)
    # The tail after the last match is spread to the end of the line's audio.
    assert spans["te"] == (2.0, 2.5) and spans["suivre"] == (2.5, 3.0)
    assert _starts(words) == sorted(_starts(words))


def test_nothing_matching_is_no_alignment_and_words_are_kept_inside_the_audio():
    wt = _new()
    assert wt.align("Bonjour tout le monde", _heard(("hello", 0.0, 0.4), ("world", 0.5, 0.9)), 1.0) is None
    assert wt.align("Bonjour", [], 1.0) is None
    assert wt.align("", _heard(("a", 0.0, 0.1)), 1.0) == []
    # A transcription running past the audio, or back in time, is clamped.
    words = wt.align("un deux trois", _heard(("un", 0.5, 0.9), ("deux", 0.2, 0.4), ("trois", 1.8, 9.0)), 2.0)
    assert [(w["start"], w["end"]) for w in words] == [(0.5, 0.9), (0.5, 0.5), (1.8, 2.0)]
    assert all(0.0 <= w["start"] <= w["end"] <= 2.0 for w in words)


def test_a_dash_or_an_ellipsis_never_matches_anything():
    wt = _new()
    words = wt.align("Attends — non …", _heard(("attends", 0.0, 0.5), ("—", 0.6, 0.7), ("non", 0.8, 1.0)), 1.4)
    assert [w["word"] for w in words] == ["Attends", "—", "non", "…"]
    assert words[1]["start"] == 0.5 and words[2]["start"] == 0.8 and words[3] == {"word": "…", "start": 1.0,
                                                                                   "end": 1.4}


@pytest.mark.parametrize("sidecar,expected", [
    (None, ("even_split", None)),
    ({"$schema": "line_timing_v1", "words": []}, ("even_split", None)),
    ({"$schema": "line_timing_v1", "words": [{"word": "a", "start": 0, "end": 0.1}]}, ("provider", None)),
    ({"$schema": "line_timing_v1", "words": [{"word": "a", "start": 0, "end": 0.1}], "words_source": "alignment",
      "aligned_by": "groq/whisper-large-v3-turbo"}, ("alignment", "groq/whisper-large-v3-turbo")),
])
def test_the_source_is_read_off_the_sidecar(sidecar, expected):
    assert _new().source_of(sidecar) == expected


def test_line_words_follow_the_order_of_truth_and_an_aligned_sidecar_keeps_the_rest():
    wt = _new()
    provider = {"$schema": "line_timing_v1", "provider": "edge", "voice": "fr-FR-HenriNeural", "duration_s": 1.0,
                "source": "tts_word_timestamps", "words": [{"word": "Salut", "start": 0.0, "end": 0.4}]}
    assert wt.line_words("Salut", 1.0, provider) == ([{"word": "Salut", "start": 0.0, "end": 0.4}], "provider")
    bare = dict(provider, words=[], source="audio_duration_only")
    assert wt.line_words("Salut toi", 1.0, bare) == (wt.even_split("Salut toi", 1.0), "even_split")
    aligned = wt.aligned_sidecar(bare, [{"word": "Salut", "start": 0.1, "end": 0.5}], "groq/whisper-large-v3-turbo")
    assert {k: v for k, v in aligned.items() if k not in ("words", "words_source", "aligned_by")} == {
        k: v for k, v in bare.items() if k != "words"}
    assert aligned["words_source"] == "alignment" and aligned["aligned_by"] == "groq/whisper-large-v3-turbo"
    assert wt.line_words("Salut", 1.0, aligned) == ([{"word": "Salut", "start": 0.1, "end": 0.5}], "alignment")
    assert bare["words"] == []  # never changed in place
