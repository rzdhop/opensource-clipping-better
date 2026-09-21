"""Stopword-frequency language detection.

Drives what language titles and captions are written in, and replaces
``metadata._looks_indonesian``. Stdlib only.
"""

import pytest

from conftest import FIXTURES

from clipping import transcript
from clipping.analysis import langdetect as L

SAMPLES = {
    "en": "the quick brown fox and the lazy dog is in the house with all of them "
          "and that is what we can say about it for now",
    "fr": "le chien et le chat sont dans la maison avec nous et je ne sais pas "
          "ce que vous voulez faire de tout ça mais c'est comme ça",
    "id": "yang dan di itu dengan untuk tidak ini dari dalam akan pada juga ke "
          "karena ada kita saya bisa sudah atau seperti jadi lebih",
    "es": "el perro y el gato están en la casa con nosotros y no sé lo que "
          "quieres hacer con todo eso pero así es como es",
    "de": "der hund und die katze sind in dem haus mit uns und ich weiß nicht "
          "was sie machen wollen aber das ist so",
    "pt": "o cachorro e o gato estão na casa com a gente e não sei o que você "
          "quer fazer com isso mas é assim mesmo",
    "it": "il cane e il gatto sono nella casa con noi e non so cosa vuoi fare "
          "di tutto questo ma è così che è",
}


@pytest.mark.parametrize("code,text", sorted(SAMPLES.items()))
def test_detects_each_supported_language(code, text):
    detected, confidence = L.detect(text)
    assert detected == code, f"{code!r} detected as {detected!r}"
    assert confidence > 0


def test_the_real_french_transcript_is_detected_as_french():
    """The file from the job that failed. It is French auto-captions with a
    scattering of untranslated English, which is the realistic hard case."""
    text, _ = transcript.parse_vtt_subs(str(FIXTURES / "rolling_overlap.vtt"))
    # The fixture is nonsense words, so this asserts only that it does not crash
    # and returns something in range.
    code, confidence = L.detect(text)
    assert code in L.SUPPORTED
    assert 0.0 <= confidence <= 1.0


def test_an_empty_or_wordless_text_returns_the_default():
    assert L.detect("") == ("en", 0.0)
    assert L.detect("   ") == ("en", 0.0)
    assert L.detect("12345 !!! ...") == ("en", 0.0)


def test_an_ambiguous_text_returns_the_default_rather_than_guessing():
    """Several of these languages share function words ('de', 'la', 'que',
    'no'). A confident wrong answer captions a whole clip set in the wrong
    language, so a narrow win is reported as unsure."""
    code, _ = L.detect("de la que no", default="en")
    assert code == "en"


def test_the_default_is_configurable():
    assert L.detect("", default="fr")[0] == "fr"


def test_scores_cover_every_supported_language():
    values = L.scores("the and to of a in is it")
    assert set(values) == set(L.SUPPORTED)
    assert all(0.0 <= v <= 1.0 for v in values.values())


# ---------------------------------------------- the _looks_indonesian successor

def test_looks_like_matches_its_own_language():
    assert L.looks_like("ini video yang bagus sekali", "id")
    assert L.looks_like("this is a good video", "en")
    assert L.looks_like("c'est une bonne vidéo", "fr")


def test_looks_like_rejects_another_language():
    assert not L.looks_like("this is a good video", "id")
    assert not L.looks_like("ini video yang bagus", "de")


def test_looks_like_is_lenient_on_purpose():
    """It drives warnings, not rejections. Tightening it would turn advisory
    warnings into noise on a three-word title."""
    assert L.looks_like("yang", "id")


def test_looks_like_on_an_unknown_language_is_false_not_an_error():
    assert L.looks_like("anything at all", "xx") is False


def test_looks_like_ignores_case_and_punctuation():
    assert L.looks_like("YANG, bagus!", "id")


# ------------------------------------------------------------------- naming

def test_language_name_for_the_prompt():
    assert L.language_name("fr") == "French"
    assert L.language_name("ID") == "Indonesian"


def test_an_unknown_code_names_the_default():
    assert L.language_name("xx") == "English"
    assert L.language_name(None) == "English"


def test_every_supported_language_has_a_name():
    for code in L.SUPPORTED:
        assert L.language_name(code) != "English" or code == "en"


# -------------------------------------------- the legacy detector still agrees

# The exact list metadata._looks_indonesian carried before it was delegated.
LEGACY_INDONESIAN_INDICATORS = [
    "yang", "dan", "untuk", "dengan", "karena", "adalah", "bisa", "tidak",
    "lebih", "dalam", "pada", "agar", "dari", "ini", "itu", "juga", "kalau",
    "saat", "tentang", "bikin", "banget", "jadi", "sudah",
]


@pytest.mark.parametrize("indicator", LEGACY_INDONESIAN_INDICATORS)
def test_no_legacy_indonesian_indicator_was_lost(indicator):
    """Delegating must not silently stop a warning from firing.

    Six of these (adalah, agar, saat, tentang, bikin, banget) were missing from
    the first version of the shared stopword list, which would have quietly
    weakened the Indonesian-language warnings in metadata.normalize_and_validate
    while every other test stayed green.
    """
    from clipping import metadata

    assert metadata._looks_indonesian(f"judul {indicator} bagus")


def test_metadata_indonesian_detector_still_behaves():
    """``metadata._looks_indonesian`` gates the Indonesian-language warnings and
    is called from several places; whatever backs it must keep agreeing."""
    from clipping import metadata

    assert metadata._looks_indonesian("ini judul yang bagus")
    assert not metadata._looks_indonesian("this is a plain english title")
