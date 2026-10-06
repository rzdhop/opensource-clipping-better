"""A legacy story moved onto the v2 pipeline has its reference images drawn
again from the looks (the Regen button's follow-up, 2026-10-02: the Visual
tier card's hint said "run the Cast step again: it ... redraws the sheets",
and nothing did -- the v2 cast step drew only missing files).

A character, place or prop of a v2 story whose look is written only now
(it had images drawn without one: before the switch) loses those images'
refs when its look is written (``cast.apply_d2``, ``places.apply_d3``,
``places.apply_r1v2``), so the same run draws them again from the look --
the portrait, then the sheets from the new portrait; the day plate (the
other time variants, made from the old plate, are left to be made again on
demand); the prop image. The cast and places estimates count those images
before the run (``workflow.cast_units``, ``workflow.places_units``).

A story created on v2 never has an image before its look, and a legacy
story writes no look: neither changes.

The fakes are the look tests' own (``tests/test_story_look.py``). Stdlib +
pytest (DEC-012).
"""

from __future__ import annotations

from clipping.aistory import schemas, steps
from clipping.aistory.store import StoryStore
from clipping.cancel import CancelToken
from test_story_look import (  # noqa: F401 -- hermetic is a fixture
    NOW,
    PNG,
    SETTINGS,
    Events,
    FakeImage,
    FakeLLM,
    _character,
    _d1,
    _d2,
    _place,
    _prop,
    _run_cast,
    _story,
    hermetic,
)

LATER = "2026-10-02T11:00:00+00:00"


def _image_ref(name, consistency="base"):
    """A stored image ref: ``base`` for a portrait, a plate or a prop image,
    ``references`` for what is made from one (a sheet, a time variant)."""
    return {"name": name, "consistency": consistency, "source": "pollinations/flux", "seed": 7, "created_at": NOW}


def _plant(store, story_id, kind, eid, name, tmp_path):
    src = tmp_path / f"src-{eid}-{name}"
    src.write_bytes(PNG + name.encode())
    store.write_media(story_id, kind, eid, name, str(src))


def _drawn_legacy_cast(store, tmp_path):
    """A legacy story with two written characters, each with a portrait and
    both sheets on disk (drawn from the descriptor alone), approved."""
    story_id = _story(store, v2=False)
    for char_id, name, descriptor in (("char_kiwilo", "Kiwilo", "a fuzzy kiwi"),
                                      ("char_mangella", "Mangella", "a sly mango")):
        doc = _character(char_id, name, descriptor, ["gold chain", "linen shirt"])
        doc["refs"].update(portrait=_image_ref("portrait.png"), turnaround=_image_ref("turnaround.png", "references"),
                           expressions=_image_ref("expressions.png", "references"))
        doc["approved_at"] = NOW
        doc["ref_seed"] = 7
        store.write_entity(story_id, "characters", doc, now=NOW)
        for which in ("portrait", "turnaround", "expressions"):
            _plant(store, story_id, "characters", char_id, f"{which}.png", tmp_path)
    return story_id


def _images_and_calls(units):
    """``(llm_calls, images, edit_images)``: the voice samples are the cast
    step's own business here (these characters have no pinned voice)."""
    return units["llm_calls"], units["images"], units["edit_images"]


def _to_v2(store, story_id):
    from clipping.aistory import workflow

    return workflow.patch_story(store, story_id, {"generation_profile": {"pipeline": "v2"}}, now=LATER)


def test_a_legacy_cast_moved_to_v2_is_counted_then_redrawn_from_its_looks(tmp_path, hermetic):
    from clipping.aistory import workflow

    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _drawn_legacy_cast(store, tmp_path)
    # On the legacy pipeline no image is missing.
    assert _images_and_calls(workflow.cast_units(store, store.get(story_id))) == (0, 0, 0)
    story = _to_v2(store, story_id)

    # The estimate counts D1 and D2, and the three images of each character drawn again.
    assert _images_and_calls(workflow.cast_units(store, story)) == (4, 2, 4)

    events = Events()
    # DEC-305 F3: one species each (two characters of a species world never share one).
    llm = FakeLLM(events, D1=[_d1("Né sur la plage."), _d1("Reine du parloir.")], D2=[_d2(175), _d2(160, "mango")])
    image = FakeImage(events)
    _run_cast(store, story_id, llm, events, image)

    assert events == ["D1", "D2", "image:portrait", "image:turnaround", "image:expressions",
                      "D1", "D2", "image:portrait", "image:turnaround", "image:expressions"]
    # Drawn from the look (the v2 prompts), the sheets as edits of the new portrait.
    portrait, turnaround = image.requests[:2]
    # Plan 26 H1: the core last, after the series, the style and the character.
    assert portrait.prompt.split("\n\n")[-1].startswith("Full-body character reference sheet, head to toe")
    assert turnaround.kind == "image_edit" and turnaround.references[0].endswith("portrait.png")
    for doc in store.list_entities(story_id, "characters"):
        assert doc["look"] and all(doc["refs"][which]["created_at"] != NOW
                                   for which in ("portrait", "turnaround", "expressions"))
        assert doc["approved_at"] is None  # drawn again: approved again
    # Nothing left: the estimate is empty and a rerun calls nothing.
    assert _images_and_calls(workflow.cast_units(store, store.get(story_id))) == (0, 0, 0)
    events.clear()
    _run_cast(store, story_id, FakeLLM(events), events)
    assert events == []


def test_a_look_written_to_a_drawn_character_drops_its_images_until_they_are_drawn_again(tmp_path, hermetic):
    from clipping.aistory import workflow
    from clipping.aistory.steps import cast

    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _drawn_legacy_cast(store, tmp_path)
    _to_v2(store, story_id)
    doc = store.read_entity(story_id, "characters", "char_kiwilo")

    cast.apply_d2(doc, _d2(175))

    # The images were drawn without the look: they no longer stand for it.
    assert doc["refs"]["portrait"] is None and doc["refs"]["turnaround"] is None
    assert doc["refs"]["expressions"] is None and doc["ref_seed"] is not None  # the seed is kept
    store.write_entity(story_id, "characters", doc, now=LATER)
    progress = workflow.progress(store, store.get(story_id), env={})
    assert progress["characters"]["char_kiwilo"]["missing"][:3] == ["portrait", "turnaround", "expressions"]
    # A look written again over a look keeps the images it was drawn from.
    cast.apply_d2(doc, _d2(160))
    doc["refs"]["portrait"] = _image_ref("portrait.png")
    cast.apply_d2(doc, _d2(150))
    assert doc["refs"]["portrait"] == _image_ref("portrait.png")


def test_a_v2_story_drawn_after_its_looks_is_never_redrawn(tmp_path, hermetic):
    from clipping.aistory import workflow

    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store, v2=True)
    doc = _character("char_kiwilo", "Kiwilo", "a fuzzy kiwi", ["gold chain", "linen shirt"])
    doc["look"] = schemas.d2_look(_d2(175))
    doc["dossier"] = {**_d1("Né sur la plage."), "relationships": []}
    doc["refs"].update(portrait=_image_ref("portrait.png"), turnaround=_image_ref("turnaround.png", "references"),
                       expressions=_image_ref("expressions.png", "references"))
    store.write_entity(story_id, "characters", doc, now=NOW)
    for which in ("portrait", "turnaround", "expressions"):
        _plant(store, story_id, "characters", "char_kiwilo", f"{which}.png", tmp_path)
    assert _images_and_calls(workflow.cast_units(store, store.get(story_id))) == (0, 0, 0)


def _drawn_legacy_places(store, tmp_path):
    story_id = _story(store, v2=False)
    owner = _character("char_kiwilo", "Kiwilo", "a fuzzy kiwi", ["gold chain", "linen shirt"])
    store.write_entity(story_id, "characters", owner, now=NOW)
    place = _place(time_variants={"day": _image_ref("variant_day.png"), "night": _image_ref("variant_night.png", "references")})
    store.write_entity(story_id, "places", place, now=NOW)
    _plant(store, story_id, "places", place["place_id"], "variant_day.png", tmp_path)
    _plant(store, story_id, "places", place["place_id"], "variant_night.png", tmp_path)
    prop = _prop(image=_image_ref("image.png"), owner_char_id="char_kiwilo")
    store.write_entity(story_id, "props", prop, now=NOW)
    _plant(store, story_id, "props", prop["prop_id"], "image.png", tmp_path)
    return story_id, place["place_id"], prop["prop_id"]


def test_legacy_places_and_props_moved_to_v2_are_counted_then_redrawn_from_their_looks(tmp_path, hermetic):
    from clipping.aistory import workflow
    from clipping.aistory.steps import places

    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id, place_id, prop_id = _drawn_legacy_places(store, tmp_path)
    assert workflow.places_units(store, store.get(story_id))["images"] == 0
    story = _to_v2(store, story_id)

    # D3 and R1v2, and the plate and the prop image drawn again.
    assert workflow.places_units(store, story) == {"llm_calls": 2, "images": 2, "edit_images": 0, "tts_chars": 0}

    events = Events()
    llm = FakeLLM(events,
                  D3=[{"layout_map": {"left": "a newsstand", "right": "a lamppost", "back": "the clock tower",
                                      "foreground": "", "centre": ""},
                       "scale_note": "a wide square", "lighting": {"day": "noon light", "night": "street lamps"},
                       "props_here": []}],
                  R1v2=[{"scale_cm": 40, "material": "chrome", "colour": "silver", "scale_phrase": "a suitcase",
                         "where_when": []}])
    image = FakeImage(events)
    ctx = steps.StepContext(job_id="job000000001", story_id=story_id, step="places", ep=None,
                            params={"places": [], "props": []}, cancel=CancelToken(),
                            settings_env=dict(SETTINGS), outputs_dir=store.outputs_dir, on_log=lambda line: None)
    places.run(ctx, runner=llm, time_fn=lambda: 100.0, adapters={("image", "local"): image})

    assert events == ["D3", "image:variant_day", "R1v2", "image:image"]
    assert image.requests[0].prompt.split("\n\n")[-1].startswith("Establishing wide shot of an empty set, day")
    place = store.read_entity(story_id, "places", place_id)
    # The night variant was made from the old plate: made again on demand from the new one (a shot falls
    # back to the day plate meanwhile).
    assert place["time_variants"]["day"]["created_at"] != NOW and place["time_variants"]["night"] is None
    assert store.read_entity(story_id, "props", prop_id)["image"]["created_at"] != NOW
    assert workflow.places_units(store, store.get(story_id)) == {"llm_calls": 0, "images": 0, "edit_images": 0,
                                                                "tts_chars": 0}
