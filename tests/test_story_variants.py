"""A character's appearance variants (plan 23 stage D5): the record, its id
and sheet names, the variant sheet as an edit of the base portrait, its seed,
its price, and its own approval.

A variant ("ghost version") is ``{variant_id, label, delta_text, refs,
source, created_at, approved_at}`` on the character (at most three); its id is
a slug of its label fixed at creation; its sheets are ``<slot>_<variant_id>``
beside the base ones, one per slot the story's sheet mode draws, each an edit
of the BASE portrait on the sheet role's edit chain; approving it never
touches the character's own approval. Only a v2 story with a ``sheet_mode``
(or ``generation_profile.variants: "on"``) carries variants.

Stdlib + pytest (DEC-012); offline (the look tests' fakes; the route tests
need fastapi, as every route test).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from clipping.aistory import media_policy, prompting, refimages, schemas, shots, templates, workflow
from clipping.aistory.steps import regenerate
from clipping.aistory.store import MEDIA_NAME_PATTERNS, StoryStore

import test_story_look as tsl
import test_story_storyboard_props as tsp
from test_story_assets_step import hermetic  # noqa: F401 - the assets step's fixture (autouse)
from test_story_look import hermetic as look_hermetic  # noqa: F401 - the look tests' fixture, for the image runs
from test_stories_api_phase2 import api  # noqa: F401 - the route tests' app

NOW = tsl.NOW
KIWI = "char_kiwilo"
GHOST = {"label": "Ghost version", "delta_text": "a translucent pale-blue glowing ghost, feet fading into mist"}
FRUIT_DRAMA = templates.load_style("fruit_drama")
KIWI_LOOK = tsl._look(build="small round kiwi body", silhouette="fuzzy brown oval", face="wide green eyes",
                      hair="short brown fuzz", skin_material="fuzzy brown kiwi skin", height_cm=120,
                      palette=["brown", "green"],
                      wardrobe_sets=[{"id": "daily", "context": "every day", "items": "green sundress, straw sandals"}])


def _ref(name, consistency="base"):
    return {"name": name, "consistency": consistency, "source": "fal/seedream-4.5", "seed": 11, "created_at": NOW}


def kiwi_story(store, *, sheet_mode="three_sheet", variants_on=False, approved=True, mode="references"):
    """A v2 Fruit Drama story (the look tests') with a written kiwi, its look
    and its base portrait on disk; *sheet_mode* (None: absent) and
    *variants_on* set on its profile."""
    story_id = tsl._story(store, v2=True)

    def profile(doc):
        doc["generation_profile"]["consistency_mode"] = mode
        if sheet_mode:
            doc["generation_profile"]["sheet_mode"] = sheet_mode
        if variants_on:
            doc["generation_profile"]["variants"] = "on"

    store.update(story_id, profile, now=NOW)
    doc = tsl._character(KIWI, "Kiwilo", "a fuzzy kiwi woman", ["green sundress", "straw sandals"],
                         look=copy.deepcopy(KIWI_LOOK))
    doc["refs"]["portrait"] = _ref("portrait.png")
    doc["approved_at"] = NOW if approved else None
    store.write_entity(story_id, "characters", doc, now=NOW)
    tsp._plant_image(store, story_id, "characters", KIWI, "portrait.png", data=b"\x89PNG base portrait")
    return story_id


def _store(tmp_path):
    return StoryStore(tmp_path / "outputs", on_log=lambda line: None)


def _make(store, story_id, which, *, image=None, variant_id="ghost_version", seed=None):
    image = image or tsl.FakeImage(tsl.Events())
    adapters = {("image", "local"): image, ("image_edit", "local"): image}
    ref = refimages.variant_image(store, story_id, KIWI, variant_id, which, env=dict(tsl.SETTINGS),
                                  on_log=lambda line: None, cancel=tsl.CancelToken(), adapters=adapters, seed=seed)
    return ref, image


# ================================================================ the record

def _variant(**changes):
    variant = {"variant_id": "ghost_version", "label": "Ghost version", "delta_text": GHOST["delta_text"],
               "refs": {"portrait": None, "turnaround": None, "expressions": None}, "source": "human",
               "created_at": NOW, "approved_at": None}
    variant.update(changes)
    return variant


def test_a_character_validates_with_up_to_three_variants_named_and_labelled_as_edits():
    doc = tsl._character(KIWI, "Kiwilo", "a fuzzy kiwi", ["sundress", "sandals"], look=copy.deepcopy(KIWI_LOOK))
    doc["variants"] = [_variant()]
    assert schemas.character_errors(doc) == []
    # Absent is fine (every character written before), and so is two_view's single slot.
    doc["variants"] = [_variant(refs={"portrait": _ref("portrait_ghost_version.png", "references")})]
    assert schemas.character_errors(doc) == []

    def errors(**changes):
        bad = copy.deepcopy(doc)
        bad["variants"] = [_variant(**changes)]
        return schemas.character_errors(bad)

    assert errors(variant_id="Ghost") and errors(variant_id="1ghost")
    assert errors(delta_text=" ".join(["mist"] * (schemas.VARIANT_DELTA_MAX_WORDS + 1)))
    assert errors(label="x" * (schemas.VARIANT_LABEL_MAX_CHARS + 1))
    assert errors(source="robot")
    assert errors(refs={"portrait": _ref("portrait.png", "references")}), "a base file name in a variant slot"
    assert errors(refs={"portrait": _ref("portrait_ghost_version.png", "base")}), "a variant sheet is never base"
    many = copy.deepcopy(doc)
    many["variants"] = [_variant(variant_id=f"look_{n}") for n in range(schemas.VARIANTS_MAX + 1)]
    assert schemas.character_errors(many)
    twice = copy.deepcopy(doc)
    twice["variants"] = [_variant(), _variant()]
    assert any("used twice" in error for error in schemas.character_errors(twice))


def test_the_variant_sheet_files_are_named_beside_the_base_ones_and_served():
    refs = MEDIA_NAME_PATTERNS["characters"]["refs"]
    for name in ("portrait_ghost_version.png", "turnaround_ghost_version.jpg", "expressions_burned.webp",
                 "portrait.png", "extra_01.png"):
        assert refs.fullmatch(name), name
    for name in ("portrait_Ghost.png", "portrait_.png", "ghost_portrait.png", "portrait_ghost.gif"):
        assert not refs.fullmatch(name), name
    assert re.fullmatch(schemas.REF_IMAGE_NAME_PATTERN, "portrait_ghost_version.png")


def test_variants_exist_only_on_a_v2_story_with_a_sheet_mode_or_the_explicit_switch():
    v2 = {"generation_profile": {"pipeline": "v2"}}
    assert not media_policy.variants_enabled(v2)
    assert media_policy.variants_enabled({"generation_profile": {"pipeline": "v2", "sheet_mode": "three_sheet"}})
    assert media_policy.variants_enabled({"generation_profile": {"pipeline": "v2", "sheet_mode": "two_view"}})
    assert media_policy.variants_enabled({"generation_profile": {"pipeline": "v2", "variants": "on"}})
    assert not media_policy.variants_enabled({"generation_profile": {"sheet_mode": "two_view", "variants": "on"}})


def test_add_variant_slugs_the_label_once_keeps_the_character_approved_and_refuses_what_it_must(tmp_path):
    store = _store(tmp_path)
    story_id = kiwi_story(store)
    made = workflow.add_variant(store, story_id, KIWI, GHOST, now=NOW)
    variant = made["variant"]
    assert variant == {"variant_id": "ghost_version", "label": "Ghost version", "delta_text": GHOST["delta_text"],
                       "refs": {"portrait": None, "turnaround": None, "expressions": None}, "source": "human",
                       "created_at": NOW, "approved_at": None}
    assert made["target"] == "character:char_kiwilo:variant:ghost_version"
    character = store.read_entity(story_id, "characters", KIWI)
    assert character["approved_at"] == NOW, "a variant never reopens the character's approval"
    assert store.get(story_id)["approvals"]["cast"] == NOW
    # The same label again is a new id; a label starting with a digit is made a slug that starts with a letter.
    assert workflow.add_variant(store, story_id, KIWI, GHOST, now=NOW)["variant"]["variant_id"] == "ghost_version_2"
    assert workflow.add_variant(store, story_id, KIWI, {"label": "3 years later", "delta_text": "an older kiwi"},
                                now=NOW)["variant"]["variant_id"] == "v_3_years_later"
    with pytest.raises(workflow.WorkflowError, match="3 appearance variants already") as caught:
        workflow.add_variant(store, story_id, KIWI, GHOST, now=NOW)
    assert caught.value.code == workflow.CONFLICT

    for n, (fields, words) in enumerate(((({"label": "", "delta_text": "x"}), "label"),
                                         ({"label": "Burned", "delta_text": ""}, "delta_text"),
                                         ({"label": "Named", "delta_text": "dressed like Kiwilo's twin"},
                                          "names 'Kiwilo'"),
                                         ({"label": "Long", "delta_text": "mist " * 61}, "at most 60"),
                                         ({"label": "Extra", "delta_text": "x", "seed": 1}, "Unknown"))):
        other_store = _store(tmp_path / f"invalid{n}")
        other = kiwi_story(other_store)
        before = other_store.read_entity(other, "characters", KIWI)
        with pytest.raises(workflow.WorkflowError) as caught:
            workflow.add_variant(other_store, other, KIWI, fields, now=NOW)
        assert caught.value.code == workflow.INVALID
        assert words in str(caught.value) + " ".join(getattr(caught.value, "errors", None) or []), (words, caught.value)
        assert other_store.read_entity(other, "characters", KIWI) == before


def test_a_story_without_variants_refuses_one_before_anything_is_written(tmp_path):
    store = _store(tmp_path)
    story_id = kiwi_story(store, sheet_mode=None)
    before = store.read_entity(story_id, "characters", KIWI)
    with pytest.raises(workflow.WorkflowError, match="carry no appearance variants") as caught:
        workflow.add_variant(store, story_id, KIWI, GHOST, now=NOW)
    assert caught.value.code == workflow.CONFLICT
    assert store.read_entity(story_id, "characters", KIWI) == before
    # The explicit switch, with no sheet mode: three slots, as three_sheet.
    switched = kiwi_story(_store(tmp_path / "on"), sheet_mode=None, variants_on=True)
    assert list(workflow.add_variant(_store(tmp_path / "on"), switched, KIWI, GHOST, now=NOW)["variant"]["refs"]) == [
        "portrait", "turnaround", "expressions"]


@pytest.mark.parametrize("sheet_mode, slots", [
    ("three_sheet", ["portrait", "turnaround", "expressions"]),
    ("two_view", ["portrait"]),
    ("two_view_expressions", ["portrait", "expressions"]),
])
def test_a_variant_has_the_slots_its_story_draws(tmp_path, sheet_mode, slots):
    store = _store(tmp_path)
    story_id = kiwi_story(store, sheet_mode=sheet_mode)
    assert list(workflow.add_variant(store, story_id, KIWI, GHOST, now=NOW)["variant"]["refs"]) == slots
    story = store.get(story_id)
    units = workflow.variant_units(story)
    assert (units["images"], units["edit_images"]) == (0, len(slots))
    assert workflow.variant_sheets_phrase(story) == f"{len(slots)} variant sheet{'' if len(slots) == 1 else 's'}"


# ================================================================ the sheet: an edit of the base portrait

def test_the_variant_prompt_is_the_role_sentence_with_the_delta_then_the_slots_own_skeleton():
    look = shots.render_look({"look": KIWI_LOOK, "signature_items": ["green sundress", "straw sandals"]})
    lock = tsl.stylelock.lock_style(tsl.stylelock.build_style_lock(FRUIT_DRAMA, {}, now=NOW), now=NOW)
    role = ("Image 1 is this character's reference: same character, same identity and proportions, now a "
            "translucent pale-blue glowing ghost, feet fading into mist.")
    for which, two_view, head in (("portrait", False, "Full-body character reference sheet, head to toe"),
                                  ("portrait", True, "A character reference sheet showing two full-body views"),
                                  ("turnaround", False, "Turnaround sheet of this character, four full-body views"),
                                  ("expressions", False, "Every cell shows the same head as image 1")):
        prompt = prompting.variant_prompt_v2(lock, which=which, delta_text=GHOST["delta_text"], look_text=look,
                                             signature_items=["green sundress", "straw sandals"], two_view=two_view)
        assert prompt.startswith(f"{role} {head}"), (which, prompt[:200])
        assert "fuzzy brown kiwi skin" in prompt
    # The delta is never cut, however tight the budget.
    tight = prompting.variant_prompt_v2(lock, which="portrait", delta_text=GHOST["delta_text"], look_text=look,
                                        signature_items=[], budget=40)
    assert tight.startswith(role)
    with pytest.raises(ValueError):
        prompting.variant_prompt_v2(lock, which="portrait", delta_text="  ", look_text=look, signature_items=[])


@pytest.mark.parametrize("sheet_mode, which, size", [
    ("three_sheet", "portrait", (720, 1280)),
    ("three_sheet", "turnaround", (1280, 720)),
    ("two_view", "portrait", (1080, 1920)),
])
def test_a_variant_sheet_is_one_edit_of_the_base_portrait_booked_on_its_own_step(tmp_path, look_hermetic,
                                                                                    sheet_mode, which, size):
    store = _store(tmp_path)
    story_id = kiwi_story(store, sheet_mode=sheet_mode)
    workflow.add_variant(store, story_id, KIWI, GHOST, now=NOW)
    ref, image = _make(store, story_id, which)
    [request] = image.requests
    assert request.kind == "image_edit"
    base = store.media_path(story_id, "characters", KIWI, "portrait.png")
    assert list(request.references) == [base], "the BASE portrait alone, image 1"
    assert (request.width, request.height) == size
    # Plan 26 H1: the core last, after the series, the style and the character in its variant.
    assert request.prompt.split("\n\n")[-1].startswith(
        "Image 1 is this character's reference: same character, same identity and proportions, now a translucent "
        "pale-blue glowing ghost")
    assert request.seed == refimages.variant_seed(story_id, KIWI, "ghost_version")
    assert ref["name"] == f"{which}_ghost_version.png" and ref["consistency"] == "references"
    character = store.read_entity(story_id, "characters", KIWI)
    assert character["variants"][0]["refs"][which] == ref
    assert character["refs"]["portrait"]["name"] == "portrait.png", "the base sheets are never touched"
    ledger = json.loads((Path(store.story_dir(story_id)) / "cost_ledger.json").read_text(encoding="utf-8"))
    assert [row["step"] for row in ledger["entries"]] == [f"character_image:{KIWI}:variant:ghost_version:{which}"]


def test_the_variant_seed_is_stable_and_its_own(tmp_path):
    seed = refimages.variant_seed("story1", KIWI, "ghost_version")
    assert seed == refimages.variant_seed("story1", KIWI, "ghost_version")
    assert seed == refimages.image_seed("story1", "characters", f"{KIWI}:ghost_version")
    assert seed != refimages.image_seed("story1", "characters", KIWI)
    assert seed != refimages.variant_seed("story1", KIWI, "burned")


def test_a_sheet_the_mode_does_not_draw_an_unknown_variant_and_no_portrait_are_refused_before_any_call(
        tmp_path, look_hermetic):
    store = _store(tmp_path)
    story_id = kiwi_story(store, sheet_mode="two_view")
    workflow.add_variant(store, story_id, KIWI, GHOST, now=NOW)
    image = tsl.FakeImage(tsl.Events())
    with pytest.raises(refimages.RefImageError, match="not drawn in this story's sheet mode"):
        _make(store, story_id, "turnaround", image=image)
    with pytest.raises(refimages.RefImageError, match="has no appearance variant 'burned'"):
        _make(store, story_id, "portrait", image=image, variant_id="burned")
    plain = kiwi_story(_store(tmp_path / "plain"), sheet_mode=None)
    with pytest.raises(refimages.RefImageError, match="carry no appearance variants"):
        refimages.variant_image(_store(tmp_path / "plain"), plain, KIWI, "ghost_version", "portrait",
                                env=dict(tsl.SETTINGS), on_log=lambda line: None, cancel=tsl.CancelToken())
    assert image.requests == []


def test_the_target_its_price_and_its_gate_are_the_sheets(tmp_path):
    store = _store(tmp_path)
    story_id = kiwi_story(store)
    workflow.add_variant(store, story_id, KIWI, GHOST, now=NOW)
    story = store.get(story_id)
    target = "character:char_kiwilo:variant:ghost_version"
    parsed = regenerate.parse_target(target)
    assert parsed == ("character", KIWI, "variant", "ghost_version")
    assert target in [t.replace("<char_id>", KIWI).replace("<variant_id>", "ghost_version")
                      for t in regenerate.ENTITY_TARGETS]
    workflow.check_regenerate_target(target)
    workflow.check_entity_target(store, story, parsed)
    assert workflow.target_units(store, story, parsed) == {"llm_calls": 0, "images": 0, "edit_images": 3,
                                                          "tts_chars": 0}
    assert workflow.target_needs_editor(story, parsed) is True
    with pytest.raises(workflow.WorkflowError, match="no appearance variant 'burned'"):
        workflow.check_entity_target(store, story, ("character", KIWI, "variant", "burned"))
    # Prompt-only consistency: images from text, no editor.
    store.update(story_id, lambda doc: doc["generation_profile"].update(consistency_mode="prompt_only"), now=NOW)
    story = store.get(story_id)
    assert workflow.target_units(store, story, parsed)["images"] == 3
    assert workflow.target_needs_editor(story, parsed) is False


# ================================================================ the approval

def test_a_variant_is_approved_on_its_own_once_every_sheet_is_made_and_a_redraw_clears_only_it(
        tmp_path, look_hermetic):
    store = _store(tmp_path)
    story_id = kiwi_story(store, sheet_mode="two_view_expressions")
    workflow.add_variant(store, story_id, KIWI, GHOST, now=NOW)
    with pytest.raises(workflow.WorkflowError, match=r"missing: portrait, expressions") as caught:
        workflow.approve_variant(store, story_id, KIWI, "ghost_version", now=NOW)
    assert caught.value.code == workflow.CONFLICT
    _make(store, story_id, "portrait")
    _make(store, story_id, "expressions")
    character = workflow.approve_variant(store, story_id, KIWI, "ghost_version", now="2026-10-05T09:00:00+00:00")
    assert character["variants"][0]["approved_at"] == "2026-10-05T09:00:00+00:00"
    assert character["approved_at"] == NOW
    _make(store, story_id, "portrait", seed=99)
    character = store.read_entity(story_id, "characters", KIWI)
    assert character["variants"][0]["approved_at"] is None and character["approved_at"] == NOW
    assert character["variants"][0]["variant_id"] == "ghost_version", "the id is never renamed"
    with pytest.raises(workflow.WorkflowError) as caught:
        workflow.approve_variant(store, story_id, KIWI, "burned", now=NOW)
    assert caught.value.code == workflow.NOT_FOUND
    assert workflow.parse_variant_approval("variant:char_kiwilo:ghost_version") == (KIWI, "ghost_version")
    assert workflow.parse_variant_approval("variant:char_kiwilo") is None


def test_the_variant_regenerate_draws_every_slot_on_one_seed_and_never_touches_the_character(
        tmp_path, look_hermetic):
    from clipping.aistory import steps

    store = _store(tmp_path)
    story_id = kiwi_story(store)
    workflow.add_variant(store, story_id, KIWI, GHOST, now=NOW)
    image = tsl.FakeImage(tsl.Events())
    ctx = steps.StepContext(job_id="job000000001", story_id=story_id, step="regenerate", ep=None,
                            params={"target": "character:char_kiwilo:variant:ghost_version", "note": None},
                            cancel=tsl.CancelToken(), settings_env=dict(tsl.SETTINGS), outputs_dir=store.outputs_dir,
                            on_log=lambda line: None)
    summary = regenerate.run(ctx, runner=None, time_fn=lambda: 100.0, sleep_fn=lambda _s: None,
                             adapters={("image", "local"): image, ("image_edit", "local"): image})
    assert summary["images"] == {"portrait": "references", "turnaround": "references", "expressions": "references"}
    assert [request.extra["name"] for request in image.requests] == [
        "portrait_ghost_version", "turnaround_ghost_version", "expressions_ghost_version"]
    assert {request.seed for request in image.requests} == {refimages.variant_seed(story_id, KIWI, "ghost_version")}
    assert store.read_entity(story_id, "characters", KIWI)["approved_at"] == NOW


# ================================================================ the routes

def test_the_routes_add_price_queue_and_approve_a_variant(api):
    import test_stories_api_phase2 as p2

    p2._settings(api, dict(p2.BASE, FAL_KEY="test-fal-key", ALLOW_PAID="1", DAILY_CAP_USD="4.00"))
    store = api.store
    story_id = kiwi_story(store)
    store.update(story_id, lambda doc: doc["generation_profile"].update(budget_profile="quality"), now=NOW)
    url = p2._url(story_id)
    response = api.client.post(f"{url}/characters/{KIWI}/variants", json=GHOST)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["target"] == "character:char_kiwilo:variant:ghost_version"
    assert api.client.post(f"{url}/characters/{KIWI}/variants",
                           json={"label": "Bad", "delta_text": ""}).status_code == 400

    estimate = api.client.get(f"{url}/estimate/regenerate", params={"target": body["target"]}).json()
    assert estimate["variant_sheets"] == 3 and estimate["message"].startswith("3 variant sheets: "), estimate
    assert estimate["units"]["edit_images"] == 3
    assert estimate["est_usd"] == pytest.approx(3 * 0.04), "three edits at the sheet edit link's price"

    response = api.client.post(f"{url}/regenerate", json={"target": body["target"]})
    assert response.status_code == 201, response.text
    job = api.jobs.get_job(response.json()["id"])
    assert api.routes._job_doc(job["step"], job["params"]) == "variant:char_kiwilo:ghost_version"
    assert api.submitted == [job["id"]]
    from web.api.models import JobStatus

    api.jobs.set_status(job["id"], JobStatus.AWAITING_APPROVAL)

    assert api.client.post(f"{url}/approve/variant:char_kiwilo:ghost_version").status_code == 409
    doc = store.read_entity(story_id, "characters", KIWI)
    for slot in ("portrait", "turnaround", "expressions"):
        tsp._plant_image(store, story_id, "characters", KIWI, f"{slot}_ghost_version.png")
        doc["variants"][0]["refs"][slot] = _ref(f"{slot}_ghost_version.png", "references")
    store.write_entity(story_id, "characters", doc, now=NOW)
    response = api.client.post(f"{url}/approve/variant:char_kiwilo:ghost_version")
    assert response.status_code == 200, response.text
    assert response.json()["variants"][0]["approved_at"] and response.json()["approved_at"] == NOW
    assert api.jobs.get_job(job["id"])["status"] == "completed"
    assert api.client.get(f"{url}/media/characters/{KIWI}/portrait_ghost_version.png").status_code == 200
