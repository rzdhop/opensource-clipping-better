"""The v2 character sheets carry the character's distinctive marks and
bearing (AI Story phase 7 follow-up, stage F2; the human, 2026-10-02: "More
prompt context is more accuracy and details").

A sheet prompt was the rendered look, the signature items, the style and the
design rules. K1's descriptor often names a distinctive mark the look does
not keep (a scar, a chipped tooth, a bandaged hand) -- it is identity, so
the sheets must show it; and nothing said how a character holds themselves.
``look.bearing`` (posture, how they carry themselves; at most 10 words) is
a new optional key of the character look, asked of D2 like ``presentation``
(A3), checked like every other look field, read by the keyframes (the
previous commit) and the sheets (``shots.visual_cues``), dropped first when
a sheet's budget is tight.

Stdlib + pytest (DEC-012); offline. The new behaviour is reached inside the
tests, so on the parent commit each fails on its own.
"""

from __future__ import annotations

import copy

import pytest

from clipping.aistory import prompting, schemas, shots, templates

import test_story_look as tsl
import test_story_shots as tss

FRUIT_DRAMA = templates.load_style("fruit_drama")
SCARRED = ("A tall yellow geometric cylinder with a solid rectangular blue cape, wearing oversized round glasses, "
           "sporting a deep scar through the left eyebrow and a chipped corner at the base.")


def _scarred():
    doc = copy.deepcopy(tsl.TALL)
    doc["descriptor"] = SCARRED
    return doc


# ================================================================ the schema and D2

def test_look_bearing_is_optional_capped_and_asked_of_d2():
    """``look.bearing`` validates absent (every look written before it) and
    present up to 10 words; D2's schema offers it, optional like
    ``presentation``; ``d2_look`` keeps a non-empty one; a name in it is a
    leak like in any other visual field; the ask names it."""
    from clipping.aistory import prompts

    assert schemas.LOOK_BEARING_MAX_WORDS == 10
    assert "bearing" not in tsl.TALL["look"] and schemas.character_errors(tsl.TALL) == []
    assert schemas.character_errors(dict(tsl.TALL, look=tsl._look(bearing="stands rigidly straight, chin up"))) == []
    errors = schemas.character_errors(dict(tsl.TALL, look=tsl._look(bearing=" ".join(["word"] * 11))))
    assert errors == ["$.look.bearing: 11 words, expected at most 10"]

    schema = schemas.d2_schema()
    assert "bearing" in schema["properties"] and "bearing" not in schema["required"]
    assert "presentation" not in schema["required"] and "build" in schema["required"]
    reply = tsl._d2(175)
    assert "bearing" not in schemas.d2_look(reply)
    assert schemas.d2_look(dict(reply, bearing="  slouches, hands in pockets ")) ["bearing"] == "slouches, hands in pockets"
    assert "bearing" not in schemas.d2_look(dict(reply, bearing="   "))
    assert schemas.d2_errors(dict(reply, bearing="walks like Kiwilo"), ["Kiwilo"]) == [
        "$.bearing: must not mention a name ('Kiwilo')"]
    assert schemas.d2_errors(dict(reply, bearing=" ".join(["word"] * 11)), []) == [
        "$.bearing: 11 words, expected at most 10"]
    assert "- bearing (optional, at most 10 words)" in prompts._D2_ASK


# ================================================================ the cues

def test_visual_cues_are_the_descriptors_marks_the_look_does_not_say_and_the_bearing():
    """``shots.visual_cues``: a descriptor clause that names a mark (a scar,
    a chip, a bandage ...) and is not already said by the look or the
    signature items, then ``look.bearing``; '' when there is neither; never
    a name."""
    doc = _scarred()
    assert shots.visual_cues(doc) == "Distinctive: a deep scar through the left eyebrow and a chipped corner at the base."
    doc["look"]["bearing"] = "Stands rigidly straight, chin up"
    assert shots.visual_cues(doc) == ("Distinctive: a deep scar through the left eyebrow and a chipped corner at the "
                                      "base. Bearing: stands rigidly straight, chin up.")
    # The marks the look or the items already say are not said twice; a descriptor without a mark says none.
    said = copy.deepcopy(doc)
    said["signature_items"].append("deep scar through the left eyebrow and a chipped corner at the base")
    assert shots.visual_cues(said) == "Bearing: stands rigidly straight, chin up."
    assert shots.visual_cues(tsl.TALL) == ""
    assert shots.visual_cues(dict(tsl.TALL, descriptor="A cylinder. Its face is plain.")) == ""
    # A character without a look yet (its sheets are the legacy ones): nothing either.
    assert shots.visual_cues(dict(_scarred(), look=None)) == ""
    # A mark clause is said whole, lower case, however the descriptor opens it.
    soft = dict(_scarred(), descriptor="A kiwi, with a bandaged left hand and a frayed cap. Smiles a lot.")
    assert shots.visual_cues(soft) == "Distinctive: a bandaged left hand and a frayed cap."
    for name in ("Captain", "Obvious", "Kiwilo"):
        assert name not in shots.visual_cues(doc)


def test_the_sheets_carry_the_cues_when_their_budget_allows_and_drop_them_first():
    """``portrait/turnaround/expressions_prompt_v2(cues=...)``: the cues
    right after the look, before the style. A sheet's value order: the
    head, the look and the tail (never cut), then the cues -- identity --,
    then the rendering (never under its least, 8 words), then the design
    rules; so at today's 130 the cues still fit (the rules give way) and
    under about 90 words they go, before the rendering is cut to its least.
    Without cues the prompt is exactly today's."""
    import re

    import test_aistory_prompting as tap

    def rendering_of(prompt):
        return re.search(r"Style: (.*?)\.(?: |$)", prompt).group(1)

    def portrait(**kwargs):
        return prompting.portrait_prompt_v2(FRUIT_DRAMA, look_text=tap.LOOK_TEXT, signature_items=tap.SIGNATURE_ITEMS,
                                            **kwargs)

    cues = "Distinctive: a deep scar through the left eyebrow. Bearing: stands rigidly straight, chin up."
    for builder in (prompting.portrait_prompt_v2, prompting.turnaround_prompt_v2, prompting.expressions_prompt_v2):
        roomy = builder(FRUIT_DRAMA, look_text=tap.LOOK_TEXT, signature_items=tap.SIGNATURE_ITEMS, budget=200,
                        cues=cues)
        assert cues in roomy and roomy.index("left-eyebrow scar.") < roomy.index(cues) < roomy.index("Style:")
        assert len(roomy.split()) <= 200
        plain = builder(FRUIT_DRAMA, look_text=tap.LOOK_TEXT, signature_items=tap.SIGNATURE_ITEMS, budget=200)
        assert plain == builder(FRUIT_DRAMA, look_text=tap.LOOK_TEXT, signature_items=tap.SIGNATURE_ITEMS, budget=200,
                                cues="")
        # Room for all of it at 200: the rendering is as long with the cues as without.
        assert rendering_of(roomy) == rendering_of(plain)
    # fruit_drama: a 42-word rendering, 78 words of rules. At 130 the cues fit and the rendering keeps its
    # 30 words; the design rules give way.
    tight = portrait(cues=cues)
    assert cues in tight and len(tight.split()) <= 130 and len(rendering_of(tight).split()) == 30
    assert len(tight.split()) > len(portrait().split()) - len(cues.split()) - 1
    # Under about 105 words the rendering could not keep its least beside them: gone, the look and the
    # signature items untouched, the rendering back to what it has without cues; without cues, the same
    # prompt. (Under 90 the rendering itself goes, as it always did: its first clause no longer fits.)
    tiny = portrait(budget=95, cues=cues)
    assert "Distinctive" not in tiny and len(rendering_of(tiny).split()) >= 8 and "left-eyebrow scar" in tiny
    assert tiny == portrait(budget=95)
    seen = []
    for budget in range(200, 94, -1):
        prompt = portrait(budget=budget, cues=cues)
        assert len(prompt.split()) <= budget
        seen.append(cues in prompt)
        assert len(rendering_of(prompt).split()) >= 8
    assert seen == sorted(seen, reverse=True) and True in seen and False in seen  # once gone, gone


def test_refimages_gives_each_sheet_the_characters_cues_and_its_links_budget(monkeypatch, tmp_path):
    """``refimages.character_image`` on a v2 character with a look builds
    its sheet prompt with ``shots.visual_cues`` and the sheet link's budget
    (200 on seedream); captured at the builder, before any chain runs."""
    import test_story_refimages as trf
    from clipping.aistory import refimages

    store = trf.StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = trf._story(store) if hasattr(trf, "_story") else None
    assert story_id is not None, "test_story_refimages has no story fixture to reuse"
    store.update(story_id, lambda doc: doc["generation_profile"].update(pipeline="v2", budget_profile="quality"),
                 now=tss.NOW)
    character = store.list_entities(story_id, "characters")[0]
    character["descriptor"] = SCARRED
    character["look"] = tsl._look(bearing="stands rigidly straight, chin up")
    store.write_entity(story_id, "characters", character, now=tss.NOW)
    seen = {}

    class Captured(Exception):
        pass

    def capture(style_lock, **kwargs):
        seen.update(kwargs)
        raise Captured()

    monkeypatch.setitem(refimages._CHARACTER_PROMPTS_V2, "portrait", capture)
    with pytest.raises(Captured):
        refimages.character_image(store, story_id, character["char_id"], "portrait", env={"FAL_KEY": "k"},
                                  on_log=lambda line: None, cancel=None)
    assert seen["cues"] == shots.visual_cues(character) and "Distinctive: a deep scar" in seen["cues"]
    assert seen["budget"] == 200
