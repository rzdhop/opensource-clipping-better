"""Plan 28 F3 (DEC-305 section 5, the human: "strict rules to avoid
consistency problems, and all details"): a sheet judge on the cast step.

- Every image the cast step (and the places step, a regenerate) makes for a
  v2 story is judged once, one free vision call (J3): one head a figure,
  the head the species named (never a human head, never a mask), the outfit
  and the signature items, the forbidden colours, the two views of a
  two-view sheet. The verdict is kept on the entity (``sheet_checks``).
- A failed image is drawn again -- a fresh seed, what the judge saw as the
  note -- at most twice, within the story's ceiling (its images x 2 x one
  image on its link), then the step says the plain sentence and the
  approval refuses it.
- An image with no check yet is never approved; an image made before the
  rule keeps its approval and is never judged; the human's own image is
  judged and warned about, never refused.
- Two characters of a species world never share a species.

Stdlib + pytest (DEC-012); the cast is ``tests/test_story_look.py``'s v2
fruit story, every call a fake.
"""

from __future__ import annotations

import copy
import json
import re

import pytest

import test_story_look as tsl
from clipping.aistory import steps
from clipping.aistory.store import StoryStore
from clipping.cancel import CancelToken
from clipping.providers.generation import GenResult
from test_story_look import hermetic  # noqa: F401 -- phase 7's hermetic fixture, used as it is

SETTINGS = dict(tsl.SETTINGS, VISION_CHAIN="gemini/flash-lite")
PASS = json.dumps({"passed": True, "issues": []})
HUMAN_HEAD = "the head is a human head, Kiwilo is a kiwi"


class SheetVision:
    """A vision adapter answering J3 with *answer(request)* (default: every
    image passes), keeping every request."""

    def __init__(self, answer=None):
        self.answer = answer or (lambda request: PASS)
        self.requests = []

    def estimate(self, link, request):
        from clipping.providers import vision

        return vision.GEMINI_VISION.estimate(link, request)

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.requests.append(copy.copy(request))
        return GenResult(provider=link.provider, model=link.model, paths=(), meta={"text": self.answer(request)})

    def asked(self):
        """What each request judged: "Kiwilo's portrait", ..."""
        return [re.match(r".*?Image 1 is (.+?)\.\n", request.prompt, re.S).group(1) for request in self.requests]


def failing(what, issue=HUMAN_HEAD):
    def answer(request):
        if f"Image 1 is {what}." in request.prompt:
            return json.dumps({"passed": False, "issues": [issue]})
        return PASS
    return answer


@pytest.fixture
def unpaced(monkeypatch):
    monkeypatch.setenv("LIMIT_GEMINI_RPM", "0")


def _ctx(store, story_id, *, params=None, log=None, step="cast"):
    log = [] if log is None else log
    return steps.StepContext(job_id="job000000001", story_id=story_id, step=step, ep=None,
                             params=params if params is not None else {"selected": ["Kiwilo", "Mangella"]},
                             cancel=CancelToken(), settings_env=dict(SETTINGS), outputs_dir=store.outputs_dir,
                             on_log=log.append), log


def _adapters(image, vision):
    table = {("image", "local"): image, ("image_edit", "local"): image, ("tts", "edge"): tsl.FakeTTS()}
    if vision is not None:
        table[("vision", "gemini")] = vision
    return table


def _cast(store, story_id, llm, events, *, vision=None, image=None, log=None):
    from clipping.aistory.steps import cast

    ctx, log = _ctx(store, story_id, log=log)
    image = image or tsl.FakeImage(events)

    def no_sleep(seconds):
        raise AssertionError(f"slept {seconds}s")

    summary = cast.run(ctx, runner=llm, time_fn=lambda: 100.0, sleep_fn=no_sleep, adapters=_adapters(image, vision))
    return summary, log, image


def _llm(events, *, second_species="mango"):
    return tsl.FakeLLM(
        events,
        K1=[tsl._k1("a fuzzy kiwi", ["gold chain", "linen shirt"]),
            tsl._k1("a sly mango", ["red dress", "crown clip"])],
        D1=[tsl._d1("Né sur la plage.", with_="Mangella"), tsl._d1("Reine du parloir.", with_="Kiwilo")],
        D2=[tsl._d2(175), tsl._d2(160, second_species)])


def _cast_story(tmp_path):
    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    return store, tsl._story(store, v2=True)


def _approve(store, story_id, char_id):
    from clipping.aistory import workflow

    return workflow.approve_entity(store, story_id, "characters", char_id, now=tsl.NOW)


def _sha(store, story_id, kind, eid, name):
    import hashlib

    with open(store.media_path(story_id, kind, eid, name), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ------------------------------------------------------------------ the ask

def test_the_sheet_judge_asks_the_head_the_species_the_outfit_the_items_and_the_forbidden_colours():
    from clipping.aistory import media_policy, prompts, stylelock, templates
    from clipping.aistory.steps import judge

    lock = stylelock.build_style_lock(templates.load_style("fruit_drama"), {}, now=tsl.NOW)
    story = {"story_id": "s", "style_template_id": "fruit_drama",
             "generation_profile": {"pipeline": "v2", "sheet_mode": "two_view"}}
    doc = tsl._character("char_kiwilo", "Kiwilo", "a fuzzy kiwi", ["gold chain", "linen shirt"],
                         look=dict(tsl._d2(175), species="kiwi", presentation="adult man"))
    assert media_policy.two_view(story)
    text = prompts.j3_prompt_text(what=judge.sheet_what(story, "characters", doc, "portrait"),
                                  brief=judge.sheet_brief(story, "characters", doc, "portrait", lock=lock),
                                  checks=judge.sheet_check_lines(story, "characters", doc, "portrait", lock=lock))
    assert "Image 1 is Kiwilo's character sheet." in text
    assert ("- Kiwilo appears exactly twice, side by side: the front on the left, the back on the right; no third "
            "view and no other character") in text
    assert "- every figure has exactly one head" in text
    assert ("- the head is a whole kiwi at human head scale, the face carved into it: never a human head, never a "
            "mask, a helmet or a costume") in text
    assert "- Kiwilo wears this outfit: white linen shirt, gold chain" in text
    assert "- the signature items above are all there" in text and "- Signature items: gold chain, linen shirt" in text
    assert "- none of these colours is used: neon green, hot pink backgrounds" in text
    assert '"the head is a human head, Gaston is a pineapple"' in text
    # The reply: passed exactly when nothing is wrong, at most 3 issues of 16 words.
    assert prompts.validate_j3({"passed": True, "issues": []}) == []
    assert prompts.validate_j3({"passed": False, "issues": [HUMAN_HEAD]}) == []
    assert prompts.validate_j3({"passed": True, "issues": [HUMAN_HEAD]})
    assert prompts.validate_j3({"passed": False, "issues": []})
    assert prompts.validate_j3({"passed": False, "issues": ["a b"] * 4})
    assert prompts.SCHEMA_NAMES["J3"] == "sheet_check"
    # Its largest reply (3 issues of 16 six-letter words) fits its cap: chars/4, + 15 %, rounded up to ten.
    worst = json.dumps({"passed": False, "issues": [" ".join(["abcdef"] * 16)] * 3})
    assert prompts.MAX_TOKENS["J3"] == 110 == -(-int(len(worst) / 4 * 1.15) // 10) * 10


def test_a_plate_and_a_prop_are_asked_their_layout_light_and_look():
    from clipping.aistory.steps import judge

    story = {"story_id": "s", "style_template_id": "fruit_drama", "generation_profile": {"pipeline": "v2"}}
    place = {"place_id": "place_parloir", "name": "Le Parloir", "descriptor": "a bamboo confession booth",
             "layout_notes": "a chair left, a camera slit right, a palm wall at the back"}
    checks = judge.sheet_check_lines(story, "places", place, "night")
    assert checks == ["the set matches the layout above: what is left, right and at the back",
                      "the light is night", "no person and no character is in it"]
    assert "- Layout: a chair left, a camera slit right, a palm wall at the back" in judge.sheet_brief(
        story, "places", place, "night")
    prop = {"prop_id": "prop_phone", "name": "Coconut phone", "descriptor": "a coconut-shell phone"}
    assert judge.sheet_check_lines(story, "props", prop, "image")[0] == (
        "the image shows this one object, matching the description above")
    assert judge.sheet_what(story, "places", place, "night") == "the set Le Parloir, its night plate (an empty set)"


# --------------------------------------------------------------- the cast step

def test_each_image_the_cast_makes_is_judged_once_and_its_verdict_kept(tmp_path, hermetic, unpaced):
    store, story_id = _cast_story(tmp_path)
    events = tsl.Events()
    vision = SheetVision()
    summary, log, _image = _cast(store, story_id, _llm(events), events, vision=vision)

    assert vision.asked() == ["Kiwilo's portrait", "Kiwilo's turnaround sheet", "Kiwilo's expressions sheet",
                              "Mangella's portrait", "Mangella's turnaround sheet", "Mangella's expressions sheet"]
    assert "sheet_issues" not in summary
    kiwilo = store.read_entity(story_id, "characters", "char_kiwilo")
    entry = kiwilo["sheet_checks"]["portrait"]
    assert entry["passed"] is True and entry["issues"] == [] and entry["version"] == 1 and entry["judged_at"]
    assert entry["image_hash"] == _sha(store, story_id, "characters", "char_kiwilo", kiwilo["refs"]["portrait"]["name"])
    assert entry["link"] == "gemini/flash-lite"
    assert "👁 Kiwilo's portrait: passed" in log
    # Booked, free: one row a check.
    rows = [row for row in store_ledger(store, story_id) if row["step"].startswith("sheet_check:")]
    assert [row["step"] for row in rows][:1] == ["sheet_check:characters:char_kiwilo:portrait"] and len(rows) == 6
    assert all(row["est_usd"] == 0 for row in rows)
    # A run again judges nothing judged already.
    vision.requests.clear()
    _cast(store, story_id, tsl.FakeLLM(events), events, vision=vision)
    assert vision.requests == []


def store_ledger(store, story_id):
    import os

    with open(os.path.join(store.story_dir(story_id), "cost_ledger.json"), encoding="utf-8") as fh:
        return json.load(fh)["entries"]


def test_a_failed_portrait_is_drawn_again_twice_then_refused_in_a_plain_sentence(tmp_path, hermetic, unpaced):
    from clipping.aistory import workflow

    store, story_id = _cast_story(tmp_path)
    events = tsl.Events()
    vision = SheetVision(failing("Kiwilo's portrait"))
    summary, log, image = _cast(store, story_id, _llm(events), events, vision=vision)

    sentence = ("Kiwilo's portrait does not match: the head is a human head, Kiwilo is a kiwi. Regenerate it, or "
                "upload your own.")
    # Drawn three times (twice again, each on a fresh seed with what the judge saw), judged each time; the
    # sheets are drawn once, from the last portrait.
    portraits = [request for request in image.requests[:5] if request.extra["name"] == "portrait"]
    assert events[3:9] == ["image:portrait"] * 3 + ["image:turnaround", "image:expressions", "K1"]
    assert len({request.seed for request in portraits[:3]}) == 3
    assert "Author's note:" not in portraits[0].prompt
    assert portraits[1].prompt.endswith("Author's note: Fix what the last picture got wrong: the head is a human "
                                        "head, the character is a kiwi.")
    assert vision.asked()[:5] == ["Kiwilo's portrait"] * 3 + ["Kiwilo's turnaround sheet",
                                                              "Kiwilo's expressions sheet"]
    assert summary["sheet_issues"] == [sentence]
    assert f"✋ {sentence}" in log
    entry = store.read_entity(story_id, "characters", "char_kiwilo")["sheet_checks"]["portrait"]
    assert entry["passed"] is False and entry["issues"] == [HUMAN_HEAD] and entry["redraws"] == 2

    with pytest.raises(workflow.WorkflowError) as caught:
        _approve(store, story_id, "char_kiwilo")
    assert str(caught.value) == f"Kiwilo cannot be approved yet. {sentence}"
    _approve(store, story_id, "char_mangella")  # the other character passed: approved
    # The fast track's and the CLI's Approve all stop on the same sentence.
    with pytest.raises(workflow.WorkflowError) as caught:
        workflow.approve_complete(store, story_id, ("characters",), raise_refusals=True, now=tsl.NOW)
    assert sentence in str(caught.value)
    # A run again does not draw it again: its redraws are spent; a regenerate starts afresh.
    events.clear()
    _cast(store, story_id, tsl.FakeLLM(events), events, vision=vision)
    assert events == []


def test_an_image_with_no_check_yet_is_never_approved_and_the_step_again_checks_it(tmp_path, hermetic,
                                                                                    unpaced):
    store, story_id = _cast_story(tmp_path)
    events = tsl.Events()
    _summary, log, _image = _cast(store, story_id, _llm(events), events, vision=None)
    assert any(line.startswith("👁 Sheet check of Kiwilo's portrait skipped: no vision link could judge it")
               for line in log)
    from clipping.aistory import workflow

    with pytest.raises(workflow.WorkflowError) as caught:
        _approve(store, story_id, "char_kiwilo")
    assert str(caught.value) == (
        "Kiwilo cannot be approved yet. Kiwilo's portrait has no check yet: run the cast step again (it checks it, "
        "free). Kiwilo's turnaround sheet has no check yet: run the cast step again (it checks it, free). Kiwilo's "
        "expressions sheet has no check yet: run the cast step again (it checks it, free).")
    vision = SheetVision()
    events.clear()
    _cast(store, story_id, tsl.FakeLLM(events), events, vision=vision)
    assert events == [] and len(vision.requests) == 6  # judged, nothing drawn
    _approve(store, story_id, "char_kiwilo")


def test_an_image_made_before_the_rule_keeps_its_approval_and_is_never_judged(tmp_path, hermetic, unpaced):
    store, story_id = _cast_story(tmp_path)
    events = tsl.Events()
    _cast(store, story_id, _llm(events), events, vision=None)
    for char_id in ("char_kiwilo", "char_mangella"):
        doc = store.read_entity(story_id, "characters", char_id)
        doc.pop("sheet_checks")  # as every character drawn before plan 28 F3
        store.write_entity(story_id, "characters", doc, now=tsl.NOW)
    _approve(store, story_id, "char_kiwilo")
    vision = SheetVision()
    _cast(store, story_id, tsl.FakeLLM(events), events, vision=vision)
    assert vision.requests == []
    assert store.read_entity(story_id, "characters", "char_kiwilo")["approved_at"]


def test_the_humans_own_image_is_judged_and_warned_about_never_redrawn_or_refused(tmp_path, hermetic, unpaced):
    from clipping.providers import generation as gen

    store, story_id = _cast_story(tmp_path)
    events = tsl.Events()
    _cast(store, story_id, _llm(events), events, vision=SheetVision())
    doc = store.read_entity(story_id, "characters", "char_kiwilo")
    doc["refs"]["portrait"]["source"] = gen.MANUAL_LINK  # what manual_uploads.accept_image records
    doc["sheet_checks"].pop("portrait")
    store.write_entity(story_id, "characters", doc, now=tsl.NOW)

    vision = SheetVision(failing("Kiwilo's portrait"))
    events.clear()
    _summary, log, _image = _cast(store, story_id, tsl.FakeLLM(events), events, vision=vision)
    assert vision.asked() == ["Kiwilo's portrait"] and events == []
    assert ("⚠️ Kiwilo's portrait, your own image -- the check saw: the head is a human head, Kiwilo is a kiwi. It "
            "is yours: kept, never refused.") in log
    assert store.read_entity(story_id, "characters", "char_kiwilo")["sheet_checks"]["portrait"]["passed"] is False
    _approve(store, story_id, "char_kiwilo")


def test_a_regenerated_image_is_judged_and_drawn_again_when_it_fails(tmp_path, hermetic, unpaced):
    from clipping.aistory.steps import regenerate

    store, story_id = _cast_story(tmp_path)
    events = tsl.Events()
    _cast(store, story_id, _llm(events), events, vision=SheetVision())
    answers = iter([json.dumps({"passed": False, "issues": ["two heads on the left figure"]}), PASS])
    vision = SheetVision(lambda request: next(answers))
    image = tsl.FakeImage(events)
    ctx, log = _ctx(store, story_id, params={"target": "character:char_kiwilo:image:turnaround"}, step="regenerate")
    summary = regenerate.run(ctx, runner=tsl.FakeLLM(events), adapters=_adapters(image, vision))
    assert [request.extra["name"] for request in image.requests] == ["turnaround", "turnaround"]
    assert "sheet_issues" not in summary
    entry = store.read_entity(story_id, "characters", "char_kiwilo")["sheet_checks"]["turnaround"]
    assert entry["passed"] is True and entry["redraws"] == 1
    assert ("🔁 Kiwilo's turnaround sheet does not match (two heads on the left figure): drawing it again "
            "(1 of 2)") in log


# ------------------------------------------------------------------ the ceiling

def test_the_redraw_ceiling_is_the_story_s_images_x_two_x_one_image_and_stops_the_redraws(tmp_path, hermetic,
                                                                                          unpaced, monkeypatch):
    from clipping.aistory.steps import entities, sheet_gate

    store, story_id = _cast_story(tmp_path)
    events = tsl.Events()
    _cast(store, story_id, _llm(events), events, vision=SheetVision())
    story = store.get(story_id)
    # Two characters x three sheets (three_sheet mode), no place, no prop: 6 images x 2 x $0.04 = $0.48.
    monkeypatch.setattr(sheet_gate, "unit_usd", lambda story, env: 0.04)
    assert sheet_gate.image_count(store, story) == 6
    assert sheet_gate.ceiling(store, story, {}) == (0.48, 0.04)

    def spend(doc):
        doc["sheet_checks"]["portrait"]["redraw_usd"] = 0.46  # an earlier image's redraws
    entities.write_character(store, story_id, "char_mangella", spend, now=tsl.NOW)

    def fail(doc):
        doc["sheet_checks"]["turnaround"].update(passed=False, issues=["two heads on the left figure"])
    entities.write_character(store, story_id, "char_kiwilo", fail, now=tsl.NOW)
    image = tsl.FakeImage(events)
    ctx, log = _ctx(store, story_id)
    tools = entities.Tools(adapters=_adapters(image, SheetVision()), time_fn=lambda: 100.0)
    assert sheet_gate.review(ctx, store, "characters", "char_kiwilo", tools=tools) == [
        "Kiwilo's turnaround sheet does not match: two heads on the left figure. Regenerate it, or upload your own."]
    assert image.requests == []
    assert any("The redraw budget for this story's images, $0.48, is spent." in line for line in log)


# --------------------------------------------------------------- the species

def test_two_characters_of_a_species_world_never_share_a_species(tmp_path, hermetic, unpaced):
    store, story_id = _cast_story(tmp_path)
    events = tsl.Events()
    llm = _llm(events)
    llm.queues["D2"].insert(1, tsl._d2(160))  # Mangella first comes back a kiwi too
    _cast(store, story_id, llm, events, vision=SheetVision())

    sentence = ("Mangella cannot be a kiwi: Kiwilo is already a kiwi, and two characters may not share a species "
                "in this world. Give Mangella another species.")
    asked = [call["user"] for call in llm.of("D2")]
    assert len(asked) == 3 and sentence in asked[2]
    assert store.read_entity(story_id, "characters", "char_mangella")["look"]["species"] == "mango"


def test_a_universe_may_let_its_characters_share_a_species():
    from clipping.aistory import universes

    assert universes.shares_species({"id": "fruits"}) is False
    assert universes.shares_species({"id": "twins", "shared_species": True}) is True


# ------------------------------------------------------------------ the tiles

def test_the_cast_and_places_tiles_say_each_image_s_verdict_in_plain_words():
    import pathlib

    src = pathlib.Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src" / "pages" / "story"
    check = (src / "SheetCheck.jsx").read_text(encoding="utf-8")
    for words in ("Checked: it matches.", "Does not match: ${issues}. Regenerate it, or upload your own.",
                  "Not checked yet: run the step again (the check is free).",
                  "Your own image. The check saw: ${issues}. It is yours: kept.", "entity.sheet_checks",
                  "Image does not match", "Check pending", "Images checked"):
        assert words in check, words
    cast = (src / "steps" / "CastStep.jsx").read_text(encoding="utf-8")
    places = (src / "steps" / "PlacesStep.jsx").read_text(encoding="utf-8")
    assert "<SheetCheckBadge entity={character} slots={IMAGE_SLOTS} />" in cast
    assert "<SheetCheckLine entity={character} slot={slot} />" in cast
    assert "<SheetCheckBadge entity={place} slots={Object.keys(place.time_variants)} />" in places
    assert "<SheetCheckBadge entity={prop} slots={['image']} />" in places
    assert "<SheetCheckLine entity={place} slot={variantKey} />" in places
