"""The prompt templates: the batch-a golden (word for word except CLEAN_FRAME), the speaker rules, and
the words a positive prompt never contains."""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from showrunner import prompts as P, store  # noqa: E402
from showrunner.stage0 import matrix as M  # noqa: E402

GOLDEN = os.path.join(ROOT, "docs", "plans", "36-stage0-batch-a-prompts.json")
OLD_CLOSING = "No subtitles, no on-screen text, no black frames."


def _golden() -> dict:
    with open(GOLDEN, encoding="utf-8") as fh:
        return json.load(fh)["prompts"]


def _expected(prompt: str) -> str:
    assert prompt.endswith(OLD_CLOSING)
    return prompt[: -len(OLD_CLOSING)] + P.CLEAN_FRAME


def _built(key: str) -> str:
    name = key.rsplit("_", 2)[0]          # paloma_fr_s33 -> paloma, ex_two_fr_s22 -> ex_two
    if name.startswith("ex_"):
        return M.prompt_exchange_a(name[3:], "fr")
    return M.prompt_path_a(name, "fr")


@pytest.mark.parametrize("key", sorted(_golden()))
def test_dialogue_templates_reproduce_batch_a_word_for_word_except_clean_frame(key):
    assert _built(key) == _expected(_golden()[key]["prompt"])


def test_the_golden_holds_from_story_files_too(tmp_path):
    """The same text from a sheet.md and a universe medium: nothing of the fruit universe lives in code."""
    s = store.Story.create(str(tmp_path), "demo", title="Demo", language="fr")
    for cid in ("marie_jeanne", "rida"):
        c = M.CHARACTERS[cid]
        s.write_text(s.sheet(cid), P.render("character_sheet", oneline=False, name=c["name"], head=c["head"],
                                            voice_en=c["voice"]["en"], voice_fr=c["voice"]["fr"], colour=""))
    cast = {cid: P.character_from_sheet(s.sections(s.sheet(cid))) for cid in ("marie_jeanne", "rida")}
    ex = M.EXCHANGES["two"]
    got = P.dialogue_clip(M.UNIVERSES["fruit"]["medium"], cast, ex["setting"], ex["lines"]["fr"], language="fr")
    assert got == _expected(_golden()["ex_two_fr_s22"]["prompt"])


def _cast(n: int) -> dict:
    return {f"c{k}": P.character(f"Name{k}", f"Name{k}, a test character", f"voice {k}") for k in range(n)}


def test_speaker_rules():
    cast = _cast(4)
    with pytest.raises(P.PromptError, match="at least one line"):
        P.dialogue_clip("M.", cast, "a room", [], language="fr")
    with pytest.raises(P.PromptError, match="at most 3"):
        P.dialogue_clip("M.", cast, "a room", [(f"c{k}", "hi") for k in range(4)], language="fr")
    with pytest.raises(P.PromptError, match="no character sheet"):
        P.dialogue_clip("M.", cast, "a room", [("ghost", "hi")], language="fr")
    with pytest.raises(P.PromptError, match="not in frame"):
        P.dialogue_clip("M.", cast, "a room", [("c1", "hi")], language="fr", in_frame=["c0"])
    with pytest.raises(P.PromptError, match="one line per clip"):
        P.dialogue_clip("M.", cast, "a room", [("c0", "hi"), ("c0", "again")], language="fr")
    # three speakers keep their order; a silent listener in frame turns a single line into an exchange
    three = P.dialogue_clip("M.", cast, "a room", [("c2", "un"), ("c0", "deux"), ("c1", "trois")], language="en")
    assert three.index('"un"') < three.index('"deux"') < three.index('"trois"')
    assert "Name2 says in English" in three
    listener = P.dialogue_clip("M.", cast, "a room", [("c0", "hi")], language="fr", in_frame=["c0", "c1"])
    assert "Name1, a test character." in listener and "They speak in turn" in listener


def test_render_refuses_missing_values_and_keeps_values_untouched():
    with pytest.raises(P.PromptError, match="needs values for: framing"):
        P.render("keyframe", medium="m", heads="h", setting="s", expression="e", who="w")
    # a value's own spacing (a French non-breaking space before "?") is never collapsed
    got = P.dialogue_clip("M.", _cast(1), "a room", [("c0", "C'est qui ?")], language="fr")
    assert '"C\'est qui ?"' in got
    with pytest.raises(P.PromptError, match="placeholder"):
        P.dialogue_clip("M.", _cast(1), "a room", [("c0", "{{oops}}")], language="fr")


def test_no_rendered_prompt_names_what_is_unwanted():
    c = P.character("Paloma", M.CHARACTERS["paloma"]["head"], "bright")
    medium = M.UNIVERSES["fruit"]["medium"]
    rendered = [
        P.dialogue_clip(medium, {"p": c}, "an office", [("p", "Salut")], language="fr"),
        P.dialogue_clip(medium, {"p": c, "q": c}, "an office", [("p", "a"), ("q", "b")], language="fr"),
        P.reaction_clip(medium, c, "an office", "a flash of doubt"),
        P.keyframe(medium, [c], "an office"),
        P.keyframe(medium, [c], "an office", framing="three_quarter"),
        P.keyframe(medium, [c, c, c], "an office"),
        P.turnaround(medium, c),
        P.emotions(medium, c),
    ]
    for text in rendered:
        assert P.unwanted_words(text) == [], text
        assert "{{" not in text and "\n" not in text
    # the template files themselves (notes excluded) never name them either
    for name in P.names():
        assert P.unwanted_words(P.load(name)) == [], name


def test_keyframe_framings():
    a, b = P.character("Ana", "Ana, a pear woman", "v"), P.character("Bo", "Bo, a plum man", "v")
    assert "Ana faces the camera" in P.keyframe("M.", [a], "a café")
    assert "Ana in three-quarter view, turned toward someone just off-screen" in \
        P.keyframe("M.", [a], "a café", framing="three_quarter")
    two = P.keyframe("M.", [a, b], "a café")
    assert "Ana and Bo stand close together" in two and "only these two characters" in two
    with pytest.raises(P.PromptError):
        P.keyframe("M.", [a], "a café", framing="dutch_angle")
    with pytest.raises(P.PromptError):
        P.keyframe("M.", [], "a café")


def test_a_sheet_needs_a_title_a_head_and_a_voice():
    with pytest.raises(P.PromptError, match="title"):
        P.character_from_sheet({"": "", "Head": "x", "Voice (en)": "y"})
    with pytest.raises(P.PromptError, match="Voice"):
        P.character_from_sheet({"": "# Ana", "Head": "Ana, a pear"})
    c = P.character_from_sheet({"": "# Ana", "Head": "Ana, a pear woman.", "Voice (en)": "warm"})
    assert c == {"name": "Ana", "head": "Ana, a pear woman", "voice": "warm"}


def test_master_heads_are_about_seventy_words():
    for cid, c in M.CHARACTERS.items():
        assert 65 <= P.words(c["head"]) <= 80, (cid, P.words(c["head"]))
