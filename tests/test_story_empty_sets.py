"""Plan 29 stage 2 (DEC-308 point 1): a place's plate and a prop's picture
are prompted as an empty set and a lone object, in positive words.

The image links take no negative prompt (A-111), and against ~440 words of
fruit people (the medium, the universe line, the rendering, the character
design rules) the plate's two short negations lost: the plates came back
with fruit people standing in them. The sent plate's core now opens on who
is not there, and the series and style around it say nothing of what the
characters are; a character sheet of the same story still does.
"""

from __future__ import annotations

import test_story_assets_step as tas
from test_story_assets_step import hermetic  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_send_layer import NOW, _live_for, _planned, _plans


def test_a_fruit_story_s_plate_and_prop_are_sent_empty_while_its_sheet_keeps_its_fruit_people(tmp_path,
                                                                                               monkeypatch):
    """Fail-first. The sent plate's core starts with :data:`prompting.PLATE_EMPTY`, the prop's with
    :data:`prompting.PROP_ALONE`; outside that sentence neither says "fruit people", "anthropomorphic" or
    "every character is", and the scale reads as a set built for people with no one in it. The portrait
    sheet of the same story is sent the fruit medium, as before."""
    import test_story_episode_steps as eps
    import test_story_variant_shots as tvs
    from clipping.aistory import imaging, prompt_templates, prompting, refimages
    from clipping.providers import generation as gen

    store = eps.StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = tvs._speech_story(store, sheet_mode="three_sheet")
    env = tas._settings(**tas.FAL)
    place = store.read_entity(story_id, "places", eps.PARLOIR)
    place["look"] = {"layout_map": {"left": "a bamboo chair", "right": "a curtain", "back": "a carved wall",
                                    "foreground": "", "centre": "a lamp"},
                     "scale_note": "A small booth comfortably fitting two people.",
                     "lighting": {"day": "soft daylight"}, "props_here": []}
    store.write_entity(story_id, "places", place, now=NOW)
    prop = store.read_entity(story_id, "props", eps.PHONE)
    prop["look"] = {"scale_cm": 15, "material": "polished coconut shell", "colour": "brown",
                    "scale_phrase": "fits in a hand", "where_when": []}
    store.write_entity(story_id, "props", prop, now=NOW)
    story = store.get(story_id)
    lock = imaging.read_lock(store, story_id, error=refimages.RefImageError)
    assert lock["template_id"] == "fruit_drama"
    labels = {label for role, kind in (("plate", gen.IMAGE), ("prop", gen.IMAGE), ("sheet", gen.IMAGE))
              for label in refimages._chain_labels(story, role, kind, env)}
    _live_for(monkeypatch, labels, 8000)
    plans = _plans(monkeypatch)

    _planned(lambda: refimages.place_image(store, story_id, eps.PARLOIR, "day", env=env, on_log=lambda _l: None,
                                           cancel=None))
    plate = plans[-1].prompt
    _planned(lambda: refimages.prop_image(store, story_id, eps.PHONE, env=env, on_log=lambda _l: None, cancel=None))
    prop_image = plans[-1].prompt
    _planned(lambda: refimages.character_image(store, story_id, eps.KIWILO, "portrait", env=env,
                                               on_log=lambda _l: None, cancel=None))
    sheet = plans[-1].prompt

    fruit_people = prompt_templates._FRUIT_PEOPLE.split(". ")[1]  # "The characters are fruit and vegetable people..."
    for text, opening in ((plate, prompting.PLATE_EMPTY), (prop_image, prompting.PROP_ALONE)):
        assert text.split("\n\n")[-1].startswith(opening)
        rest = text.replace(opening, "").lower()
        for wording in ("fruit people", "fruit and vegetable people", "anthropomorphic", "every character is",
                        "human-like faces", "fruit heads"):
            assert wording not in rest, wording
        assert fruit_people.lower() not in rest and "character design rules" not in rest
    assert "Establishing wide shot of an empty set, day, no people, no characters:" in plate
    assert "built for two people, shown with no one in it" in plate and "fitting" not in plate
    assert plate.endswith(prompting._CONSTRAINTS_NO_PEOPLE)

    assert fruit_people in sheet and "anthropomorphic" in sheet
    assert prompting.PLATE_EMPTY not in sheet and prompting.PROP_ALONE not in sheet

    # A lock that records its universe: the sheet says what every character is, the plate does not.
    fruits = dict(lock, universe={"id": "fruits", "label": {"en": "Fruits"}, "species": ["kiwi", "mango"],
                                  "subject_phrase": "an anthropomorphic fruit"})
    said = {kind: prompt_templates.entity_prompt(story, fruits, kind, doc, "Core.", limit_words=None)["text"]
            for kind, doc in (("character", store.read_entity(story_id, "characters", eps.KIWILO)),
                              ("place", place))}
    assert "Universe: Fruits -- every character is an anthropomorphic fruit" in said["character"]
    assert "Universe:" not in said["place"] and "anthropomorphic" not in said["place"]
