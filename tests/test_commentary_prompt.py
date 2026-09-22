"""The voice-over script prompt.

The last hardcoded Indonesian prompt in the project, and the only one that had
no coverage at all -- because it lived in ``clipping/voiceover.py``, which
imports ``google.genai`` and ``edge_tts`` and therefore cannot be imported in
the pytest-only CI environment (DEC-012).

Moving it into ``clipping.analysis.prompts`` is what makes these tests
possible: that module is stdlib-only, so this file imports it directly and
never touches ``voiceover``. The one assertion about ``voiceover.py`` reads it
as text for the same reason.
"""

import pathlib

from clipping.analysis import prompts

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
VOICEOVER = PROJECT_ROOT / "clipping" / "voiceover.py"

SNIPPET = "We shipped it on a Friday and the pager went off twice that night."


# ------------------------------------------------------- the output language

def test_the_output_language_is_named_in_the_prompt():
    assert "Indonesian" in prompts.commentary_prompt(SNIPPET, language="id")
    assert "English" in prompts.commentary_prompt(SNIPPET, language="en")
    assert "French" in prompts.commentary_prompt(SNIPPET, language="fr")


def test_the_instructions_are_english_whatever_the_output_language():
    """The instructions being Indonesian is what made a French video get
    Indonesian output. The language of the ask and the language of the answer
    are separate things."""
    prompt = prompts.commentary_prompt(SNIPPET, language="id")

    assert "RULES" in prompt
    for indonesian in ("Kamu adalah", "ATURAN", "JANGAN", "berdurasi"):
        assert indonesian not in prompt


def test_an_unknown_language_code_still_produces_a_prompt():
    """language_name falls back to English rather than raising."""
    assert prompts.commentary_prompt(SNIPPET, language="zz")


# --------------------------------------------------------- styles and lengths

def test_every_style_and_length_maps_to_text():
    seen = set()
    for style in prompts.COMMENTARY_STYLES:
        for length in prompts.COMMENTARY_LENGTHS:
            prompt = prompts.commentary_prompt(
                SNIPPET, style=style, language="en", length=length
            )
            assert prompt.strip()
            seen.add(prompt)

    expected = len(prompts.COMMENTARY_STYLES) * len(prompts.COMMENTARY_LENGTHS)
    assert len(seen) == expected, "two combinations produced the same prompt"


def test_the_four_styles_are_the_four_the_cli_offers():
    """--voiceover-style's choices and this table must not drift apart."""
    assert set(prompts.COMMENTARY_STYLES) == {
        "analysis", "reaction", "lesson", "summary"
    }


def test_the_three_lengths_are_the_three_the_cli_offers():
    assert set(prompts.COMMENTARY_LENGTHS) == {"short", "normal", "long"}


def test_an_unknown_style_falls_back_to_analysis():
    fallback = prompts.commentary_prompt(SNIPPET, style="nonsense", language="en")
    analysis = prompts.commentary_prompt(SNIPPET, style="analysis", language="en")
    assert fallback == analysis


def test_an_unknown_length_falls_back_to_normal():
    fallback = prompts.commentary_prompt(SNIPPET, length="nonsense", language="en")
    normal = prompts.commentary_prompt(SNIPPET, length="normal", language="en")
    assert fallback == normal


# --------------------------------------------------------------- the content

def test_the_snippet_is_embedded_verbatim():
    assert SNIPPET in prompts.commentary_prompt(SNIPPET, language="en")


def test_the_prompt_forbids_restating_the_transcript():
    """The whole point of a voice-over is that it adds something."""
    prompt = prompts.commentary_prompt(SNIPPET, language="en").lower()
    assert "do not restate" in prompt


def test_the_prompt_asks_for_the_spoken_words_only():
    """The result is handed straight to text-to-speech; a heading would be read
    aloud."""
    prompt = prompts.commentary_prompt(SNIPPET, language="en").lower()
    assert "nothing else" in prompt


# --------------------------------------------------- the old copy is gone

def test_voiceover_no_longer_defines_its_own_prompt():
    """Read as text, not imported: voiceover.py pulls in google-genai, which
    is absent in CI, and a guard that silently skips is not a guard."""
    source = VOICEOVER.read_text(encoding="utf-8")

    assert "def get_commentary_prompt" not in source
    assert "Kamu adalah" not in source
    assert "commentary_prompt" in source, "voiceover.py must call the shared one"
