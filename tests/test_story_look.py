"""AI Story phase 7, stage 3a: the structured look of a v2 story.

A character, a place and a prop of a v2 story (``generation_profile.pipeline:
"v2"``) gain an optional ``look`` block -- a character also an optional
``dossier`` -- validated when present and never required, so every document a
legacy story wrote still validates. The look is written by its own call (D2
for a character, D3 for a place, R1v2 for a prop) between the text and the
images, and rendered into words by the pure ``shots.render_look`` /
``render_place`` / ``render_prop``: a character's height is said against the
other characters in the frame, by their handle, never a name.

Stdlib + pytest (DEC-012). The step modules are imported inside the tests, so
on the parent commit each test fails on its own instead of the file failing to
collect. Offline and hermetic: no key, chain, cap or limit of the machine
reaches a test, no request leaves the process, nothing is written outside
``tmp_path``.
"""

from __future__ import annotations

import copy
import os

import pytest

from clipping.aistory import defaults, prompts, schemas, shots, steps, stylelock, templates
from clipping.aistory.store import StoryStore
from clipping.cancel import CancelToken
from clipping.providers.generation import GenResult
from clipping.providers.registry import Link

NOW = "2026-10-01T10:00:00+00:00"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
LINK = Link("gemini", "gemini-test")
TENTAFRUIT = next(c for c in templates.load_concepts() if c["concept_id"] == "tentafruit_island")

# Test values only: every request goes to a fake.
SETTINGS = {
    "LLM_CHAIN": "gemini/gemini-test", "GOOGLE_API_KEY": "test-gemini-key",
    "IMAGE_CHAIN": "local/comfyui", "IMAGE_EDIT_CHAIN": "local/comfyui",
    "TTS_CHAIN": "edge/fr-FR-HenriNeural",
}
GEN_VARS = (
    "FAL_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_PAID_API_KEY", "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID", "POLLINATIONS_API_KEY", "IMAGE_CHAIN", "IMAGE_EDIT_CHAIN", "VIDEO_CHAIN",
    "TTS_CHAIN", "VISION_CHAIN", "LOCAL_COMFYUI_URL", "LOCAL_OLLAMA_URL", "ALLOW_PAID", "PER_EPISODE_CAP_USD",
    "DAILY_CAP_USD", "PER_STORY_CAP_USD", "BUDGET_PROFILE", "LLM_CHAIN", "STORY_LLM_CHAIN", "ALLOW_SLOW_CHAIN",
    "MAX_QUEUED_JOBS",
)


# ------------------------------------------------------------------ documents

def _character(char_id, name, descriptor, items, *, look=None):
    from clipping.aistory.steps import cast

    doc = cast.new_character(char_id, name, "lead", "One line.", archetype="", source="custom", now=NOW)
    doc["descriptor"] = descriptor
    doc["signature_items"] = list(items)
    if look is not None:
        doc["look"] = look
    return doc


def _look(**changes):
    look = {
        "build": "tall narrow cylinder body",
        "silhouette": "upright tube with a square cape",
        "face": "dot eyes, flat line mouth, round glasses",
        "hair": "bald flat top",
        "skin_material": "smooth matte yellow plastic",
        "height_cm": 180,
        "palette": ["yellow", "blue"],
        "wardrobe_sets": [{"id": "daily", "context": "every day", "items": "stiff rectangular blue cape, round glasses"}],
        "season_change": None,
    }
    look.update(changes)
    return look


TALL = _character("char_captain_obvious", "Captain Obvious", "A tall yellow geometric cylinder with a blue cape",
                  ["Stiff rectangular blue cape", "Comically oversized magnifying glass"], look=_look())
SHORT = _character("char_miss_overthink", "Miss Overthink", "A short red triangle character with tiny dot eyes",
                   ["bulging utility belt", "shaking stopwatch"],
                   look=_look(build="small sharp triangle body", silhouette="pointed red wedge", face="tiny dot eyes",
                              hair="none", skin_material="glossy red card", height_cm=90, palette=["red"],
                              wardrobe_sets=[{"id": "daily", "context": "every day",
                                              "items": "bulging utility belt with index cards"}]))


def _place(**changes):
    from clipping.aistory.steps import places

    doc = places.new_place("place_city_square", "City square", "The square.", now=NOW)
    doc.update(descriptor="a city square with a clock tower", layout_notes="newsstand left, lamppost right",
               time_variants={"day": None, "night": None})
    doc.update(changes)
    return doc


def _prop(**changes):
    from clipping.aistory.steps import places

    doc = places.new_prop("prop_toaster", "Giant toaster", "The villain.", None, now=NOW)
    doc.update(descriptor="a chrome toaster with two glowing slots")
    doc.update(changes)
    return doc


PLACE_LOOK = {
    "layout_map": {"left": "a bright yellow newsstand", "right": "a tall lamppost", "back": "the clock tower",
                   "foreground": "", "centre": "an empty park bench"},
    "scale_note": "a wide square, the tower ten people high",
    "lighting": {"day": "flat bright noon light", "night": "orange street lamps, deep blue shadows"},
    "props_here": ["prop_toaster"],
}
PROP_LOOK = {"scale_cm": 40, "material": "polished chrome", "colour": "silver", "scale_phrase": "as big as a suitcase",
             "where_when": [{"ep": 1, "holder_char_id": None, "place_id": "place_city_square", "note": "it lands"}]}


# ================================================================ schemas

def test_v1_documents_still_validate():
    from clipping.aistory.steps import cast

    blank = cast.new_character("char_kiwilo", "Kiwilo", "lead", "A kiwi.", archetype="", source="sketch", now=NOW)
    written = _character("char_kiwilo", "Kiwilo", "a fuzzy kiwi", ["gold chain", "linen shirt"])
    assert "look" not in written and "dossier" not in written
    assert schemas.character_errors(blank) == []
    assert schemas.character_errors(written) == []
    assert schemas.place_errors(_place()) == []
    assert schemas.prop_errors(_prop()) == []


def test_look_block_is_checked_when_present():
    # A good look validates, on every kind.
    assert schemas.character_errors(TALL) == []
    assert schemas.place_errors(_place(look=copy.deepcopy(PLACE_LOOK))) == []
    assert schemas.prop_errors(_prop(look=copy.deepcopy(PROP_LOOK))) == []
    dossier = {
        "backstory": "Grew up in the square.", "goal": "Save the city.", "need": "To be listened to.",
        "fears": "Being wrong.", "secrets": ["He cannot read the clock."],
        "relationships": [{"with": "char_miss_overthink", "history": "Partners for years.", "now": "Annoyed."}],
        "voice": {"patterns": "States the obvious.", "vocabulary": "Plain words.", "catchphrases": ["Obviously."]},
        "arc": "Learns to doubt.",
    }
    assert schemas.character_errors(dict(TALL, dossier=dossier)) == []

    # A wrong height, an over-cap face, a missing look key: refused.
    for bad in (_look(height_cm=600), _look(height_cm=4), _look(height_cm="180"), _look(height_cm=180.5),
                _look(face=" ".join(["round"] * 16)), _look(palette=[]), _look(wardrobe_sets=[])):
        assert schemas.character_errors(dict(TALL, look=bad)), bad
    missing = _look()
    del missing["hair"]
    assert schemas.character_errors(dict(TALL, look=missing))
    errors = schemas.character_errors(dict(TALL, look=_look(face=" ".join(["round"] * 16))))
    assert errors == ["$.look.face: 16 words, expected at most 15"]
    assert schemas.character_errors(dict(TALL, dossier=dict(dossier, secrets=["a", "b", "c"])))
    assert schemas.character_errors(dict(TALL, dossier=dict(dossier, backstory=" ".join(["word"] * 61))))

    assert schemas.place_errors(_place(look=dict(PLACE_LOOK, scale_note=" ".join(["big"] * 16))))
    assert schemas.place_errors(_place(look=dict(PLACE_LOOK, lighting={"Day!": "light"})))
    assert schemas.prop_errors(_prop(look=dict(PROP_LOOK, scale_cm=0)))
    assert schemas.prop_errors(_prop(look=dict(PROP_LOOK, where_when=[{"ep": 0, "holder_char_id": None,
                                                                       "place_id": None, "note": "x"}])))


def test_look_presentation_is_optional_and_capped():
    """A3: a character named Raisinetta was drawn as a man because nothing
    said otherwise. ``look.presentation`` (apparent age and gender
    presentation, at most 8 words) is an optional key of the character look
    schema -- absent, as every look written before it existed, still
    validates; present, it is checked like every other look field."""
    assert "presentation" not in TALL["look"]
    assert schemas.character_errors(TALL) == []  # no presentation at all: still a good look
    assert schemas.character_errors(dict(TALL, look=_look(presentation="woman in her thirties"))) == []
    errors = schemas.character_errors(dict(TALL, look=_look(presentation=" ".join(["word"] * 9))))
    assert errors == ["$.look.presentation: 9 words, expected at most 8"]


# ================================================================ renderers

def _words(text):
    return len(text.split())


def test_render_look_relative_height():
    tall = shots.render_look(TALL, others=[SHORT])
    assert "about twice as tall as the short red triangle character" in tall
    assert "wearing stiff rectangular blue cape, round glasses" in tall
    # The signature item already worn is not said twice; a tool is "with", never "wearing".
    assert tall.lower().count("blue cape") == 1
    assert "with comically oversized magnifying glass" in tall.lower()
    assert "wearing comically" not in tall.lower()
    assert _words(tall) <= 45
    assert "Captain" not in tall and "Obvious" not in tall and "Overthink" not in tall

    short = shots.render_look(SHORT, others=[TALL])
    assert "about half the height of the tall yellow geometric cylinder" in short
    assert _words(short) <= 45
    assert "Captain" not in short and "Overthink" not in short

    same = shots.render_look(TALL, others=[dict(SHORT, look=_look(height_cm=175))])
    assert "about the same height as" in same
    # No other in the frame: no height phrase at all.
    assert "tall as" not in shots.render_look(TALL) and "height of" not in shots.render_look(TALL)


def test_render_look_presentation_leads_when_present():
    """A3: ``render_look`` renders ``presentation`` first, before ``build``
    -- the walk drew a character named Raisinetta as a man because nothing
    said otherwise. A look without one renders exactly as before (A3 is
    additive)."""
    without_presentation = shots.render_look(TALL)
    assert not without_presentation.startswith("woman") and "presentation" not in without_presentation

    with_presentation = shots.render_look(dict(TALL, look=_look(presentation="woman in her thirties")))
    assert with_presentation.startswith("woman in her thirties, ")
    # Dropping presentation again (not touching it) reproduces the old text exactly.
    assert with_presentation == f"woman in her thirties, {without_presentation}"
    assert _words(with_presentation) <= 45


def test_render_place_and_prop_say_no_name():
    place = _place(look=copy.deepcopy(PLACE_LOOK))
    prop = _prop(look=copy.deepcopy(PROP_LOOK))
    wide = shots.render_place(place, "night", "wide_establishing", props=[prop])
    assert "on the left a bright yellow newsstand" in wide and "orange street lamps" in wide
    assert "as big as a suitcase" in wide
    close = shots.render_place(place, "night", "close_up")
    assert "orange street lamps" in close and "newsstand" not in close and "the clock tower" in close
    assert "as big as a suitcase" in shots.render_prop(prop)
    for text in (wide, close, shots.render_prop(prop)):
        assert "City square" not in text and "Giant toaster" not in text


# ================================================================ the cast step (v2 call order)

@pytest.fixture
def hermetic(monkeypatch, tmp_path):
    from clipping.config import PROVIDER_KEYS
    from clipping.providers import adapters, budget, images, limits, llm, local_comfyui, pacing, transport

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in GEN_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("LIMIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LIMIT_EDGE_RPM", "0")
    monkeypatch.setenv("USAGE_PATH", str(tmp_path / "data" / "usage.json"))
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "data" / "spend.json"))
    limits.reset()
    budget.reset()
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    adapters.load_all()

    def no_network(method, url, **_kwargs):
        raise AssertionError(f"a real request was attempted: {method} {url}")

    monkeypatch.setattr(images, "urllib_transport", no_network)
    monkeypatch.setattr(local_comfyui, "urllib_transport", no_network)
    monkeypatch.setattr(transport, "urllib_transport", no_network)
    yield
    limits.reset()
    budget.reset()
    pacing.reset_limiters()


class Events(list):
    pass


class FakeLLM:
    def __init__(self, events, **queues):
        self.events = events
        self.queues = {prompt: list(replies) for prompt, replies in queues.items()}
        self.calls = []
        self._ids = {name: prompt for prompt, name in prompts.SCHEMA_NAMES.items()}

    def __call__(self, chain, **kwargs):
        prompt = self._ids[kwargs["schema_name"]]
        self.calls.append(dict(kwargs, prompt=prompt))
        self.events.append(prompt)
        queue = self.queues.get(prompt) or []
        if not queue:
            raise AssertionError(f"no {prompt} reply queued")
        return copy.deepcopy(queue.pop(0)), LINK

    def of(self, prompt):
        return [call for call in self.calls if call["prompt"] == prompt]


class FakeImage:
    def __init__(self, events):
        self.events = events
        self.requests = []

    def estimate(self, link, request):
        return None

    def probe(self, link, **_kwargs):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.requests.append(copy.copy(request))
        self.events.append(f"image:{request.extra['name']}")
        path = os.path.join(request.out_dir, f"{request.extra['name']}.png")
        with open(path, "wb") as fh:
            fh.write(PNG + str(len(self.requests)).encode())
        return GenResult(provider=link.provider, model=link.model, paths=(path,), seed=request.seed, meta={})


class FakeTTS:
    def estimate(self, link, request):
        return None

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        path = os.path.join(request.out_dir, "sample.mp3")
        with open(path, "wb") as fh:
            fh.write(b"ID3fake")
        return GenResult(provider=link.provider, model=link.model, paths=(path,), meta={"duration_s": 2.5})


def _k1(descriptor, items):
    return {
        "descriptor": descriptor, "signature_items": list(items),
        "personality": {"traits": ["rusé", "charmeur"], "wants": "Gagner.", "fears": "Perdre.",
                        "speech_style": "Court."},
        "voice": {"gender": "male", "age": "adult", "style_tags": ["warm"], "direction": "smug and warm",
                  "sample_line": "Je gagne toujours."},
        "relationships": [],
    }


def _d2(height):
    return {"build": "lean human body", "silhouette": "narrow and upright", "face": "fuzzy round kiwi head",
            "hair": "short brown fuzz", "skin_material": "fuzzy brown kiwi skin", "height_cm": height,
            "palette": ["brown", "green"],
            "wardrobe_sets": [{"id": "daily", "context": "every day", "items": "white linen shirt, gold chain"}],
            "season_change": "", "species": "kiwi"}  # fruit_drama is a species world: D2 names the head (plan 26 stage 7b)


def _story(store, *, v2):
    from clipping.aistory import schemas as schemas_mod

    profile = defaults.default_generation_profile()
    if v2:
        profile["pipeline"] = defaults.PIPELINE_V2
    story_id = store.create(language="fr", style_template_id="fruit_drama", generation_profile=profile,
                            now=NOW)["story_id"]
    concept = templates.localize_concept(TENTAFRUIT, "fr")

    def setup(doc):
        doc["concept_id"] = TENTAFRUIT["concept_id"]
        doc["concept"] = concept
        doc["title"] = concept["title"]
        doc["logline"] = concept["logline"]
        doc["premise"] = "Chaque semaine, un couple est éliminé."
        doc["tone"] = "mélodramatique"
        for key in ("concept", "bible", "style"):
            doc["approvals"][key] = NOW

    store.update(story_id, setup, now=NOW)
    lock = stylelock.lock_style(stylelock.build_style_lock(templates.load_style("fruit_drama"), {}, now=NOW), now=NOW)
    store.write_doc(story_id, "style_lock.json", lock, now=NOW, validator=schemas_mod.style_lock_errors)
    return story_id


def _run_cast(store, story_id, llm, events, image=None):
    from clipping.aistory.steps import cast

    ctx = steps.StepContext(job_id="job000000001", story_id=story_id, step="cast", ep=None,
                            params={"selected": ["Kiwilo", "Mangella"]}, cancel=CancelToken(),
                            settings_env=dict(SETTINGS), outputs_dir=store.outputs_dir, on_log=lambda line: None)
    image = image or FakeImage(events)
    adapters = {("image", "local"): image, ("image_edit", "local"): image, ("tts", "edge"): FakeTTS()}

    def no_sleep(seconds):
        raise AssertionError(f"slept {seconds}s")

    return cast.run(ctx, runner=llm, time_fn=lambda: 100.0, sleep_fn=no_sleep, adapters=adapters)


def _d1(backstory, *, with_=None):
    relationships = [] if with_ is None else [{"with": with_, "history": "Rivaux depuis la saison passée.",
                                                "now": "Alliés par nécessité."}]
    return {"backstory": backstory, "goal": "Gagner l'île.", "need": "Faire confiance.", "fears": "Être trahi.",
            "secrets": ["Il a truqué le premier vote."], "relationships": relationships,
            "voice": {"patterns": "Phrases courtes, pauses.", "vocabulary": "Argot de plage.",
                      "catchphrases": ["Tranquille."]},
            "arc": "Du tricheur au leader loyal."}


# Phase 7 stage 5a (DEC-228): re-pinned on purpose -- a v2 cast now writes the dossier (D1) between K1
# and D2 (A14: K1 -> D1 -> D2 -> sheets), so the estimate counts three calls per new character.
def test_v2_cast_runs_k1_d1_d2_then_the_sheets_and_a_legacy_story_makes_no_d1_or_d2_call(tmp_path, hermetic):
    from clipping.aistory import workflow

    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)

    # v2: the estimate counts K1, D1 and D2 for each new character.
    v2_id = _story(store, v2=True)
    assert workflow.cast_units(store, store.get(v2_id), selected=["Kiwilo", "Mangella"])["llm_calls"] == 6
    events = Events()
    llm = FakeLLM(events, K1=[_k1("a fuzzy kiwi", ["gold chain", "linen shirt"]),
                              _k1("a sly mango", ["red dress", "crown clip"])],
                  D1=[_d1("Né sur la plage.", with_="Mangella"), _d1("Reine du parloir.", with_="Kiwilo")],
                  D2=[_d2(175), _d2(160)])
    image = FakeImage(events)
    _run_cast(store, v2_id, llm, events, image)
    assert events == ["K1", "D1", "D2", "image:portrait", "image:turnaround", "image:expressions",
                      "K1", "D1", "D2", "image:portrait", "image:turnaround", "image:expressions"]
    # The sheets are drawn from the look: a full-body portrait, then edits of it -- each core last, after
    # the series, the style and the character (plan 26 H1).
    portrait, turnaround, expressions = image.requests[:3]
    assert portrait.prompt.startswith("SERIES:")
    assert portrait.prompt.split("\n\n")[-1].startswith("Full-body character reference sheet, head to toe")
    assert "wearing white linen shirt, gold chain" in portrait.prompt and "Kiwilo" not in portrait.prompt
    assert turnaround.prompt.split("\n\n")[-1].startswith("Image 1 is this character's reference")
    assert expressions.prompt.split("\n\n")[-1].startswith("Image 1 is this character's reference")
    assert turnaround.kind == "image_edit" and len(turnaround.references) == 1
    chars = {doc["char_id"]: doc for doc in store.list_entities(v2_id, "characters")}
    assert chars["char_kiwilo"]["look"]["height_cm"] == 175 and chars["char_kiwilo"]["look"]["season_change"] is None
    # The dossier is saved, its relationship mapped from the name to the id -- D1 is shown every other
    # cast member (name, role, one-line), written or not.
    assert chars["char_kiwilo"]["dossier"]["backstory"] == "Né sur la plage."
    assert chars["char_kiwilo"]["dossier"]["relationships"] == [
        {"with": "char_mangella", "history": "Rivaux depuis la saison passée.", "now": "Alliés par nécessité."}]
    assert chars["char_mangella"]["dossier"]["relationships"][0]["with"] == "char_kiwilo"
    first_d1 = llm.of("D1")[0]
    assert "Mangella (lead)" in first_d1["user"] and "Gagner." in first_d1["user"]
    assert first_d1["max_tokens"] == prompts.MAX_TOKENS["D1"]
    # The second D2 is shown the first character's height (one scale for the cast).
    first, second = (call["user"] for call in llm.of("D2"))
    assert "175 cm" not in first and "175 cm" in second
    assert all(call["max_tokens"] == 380 for call in llm.of("D2"))
    # Nothing missing any more: no call left in the estimate, and a rerun calls nothing.
    assert workflow.cast_units(store, store.get(v2_id))["llm_calls"] == 0
    events.clear()
    _run_cast(store, v2_id, FakeLLM(events), events)
    assert events == []
    # A character with its look but no dossier: one call left, D1 alone.
    store.write_entity(v2_id, "characters", {k: v for k, v in chars["char_mangella"].items() if k != "dossier"},
                       now=NOW)
    assert workflow.cast_units(store, store.get(v2_id))["llm_calls"] == 1
    llm = FakeLLM(events, D1=[_d1("Reine du parloir.")])
    _run_cast(store, v2_id, llm, events)
    assert events == ["D1"]
    assert store.read_entity(v2_id, "characters", "char_mangella")["dossier"]["relationships"] == []

    # Legacy: no D1 or D2, in the estimate or the run, and no dossier or look.
    legacy_id = _story(store, v2=False)
    assert workflow.cast_units(store, store.get(legacy_id), selected=["Kiwilo", "Mangella"])["llm_calls"] == 2
    events = Events()
    llm = FakeLLM(events, K1=[_k1("a fuzzy kiwi", ["gold chain", "linen shirt"]),
                              _k1("a sly mango", ["red dress", "crown clip"])])
    image = FakeImage(events)
    _run_cast(store, legacy_id, llm, events, image)
    assert "D1" not in events and "D2" not in events and events[:2] == ["K1", "image:portrait"]
    assert image.requests[0].prompt.startswith("Character portrait, a fuzzy kiwi, wearing gold chain, linen shirt.")
    assert all("look" not in doc and "dossier" not in doc for doc in store.list_entities(legacy_id, "characters"))


def test_v2_places_run_p1_d3_plate_then_r1_r1v2_prop_image(tmp_path, hermetic):
    from clipping.aistory import workflow
    from clipping.aistory.steps import places

    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store, v2=True)
    owner = _character("char_kiwilo", "Kiwilo", "a fuzzy kiwi", ["gold chain", "linen shirt"], look=_d2_look(175))
    store.write_entity(story_id, "characters", owner, now=NOW)
    params = {"places": [{"name": "Plage", "one_line": "La plage."}],
              "props": [{"name": "Coco-téléphone", "one_line": "Le téléphone.", "owner": "Kiwilo"}]}
    assert workflow.places_units(store, store.get(story_id), params)["llm_calls"] == 4

    events = Events()
    llm = FakeLLM(events,
                  P1=[{"descriptor": "a crescent of white sand with palm huts",
                       "layout_notes": "huts left, sea right, bonfire back", "time_variants": ["day", "night"]}],
                  D3=[{"layout_map": {"left": "palm-leaf huts", "right": "the turquoise sea", "back": "a bonfire ring",
                                      "foreground": "", "centre": ""},
                       "scale_note": "a wide beach, huts twice a person's height",
                       "lighting": {"day": "hard tropical sun", "night": "orange bonfire glow"},
                       "props_here": ["Coco-téléphone"]}],
                  R1=[{"descriptor": "a hollow coconut with a curly cord and a brass dial", "owner": "Kiwilo"}],
                  R1v2=[{"scale_cm": 18, "material": "coconut shell and brass", "colour": "brown and gold",
                         "scale_phrase": "fits in one hand", "where_when": [
                             {"ep": 1, "holder": "Kiwilo", "place": "Plage", "note": "rings at dawn"}]}])
    image = FakeImage(events)
    ctx = steps.StepContext(job_id="job000000001", story_id=story_id, step="places", ep=None, params=params,
                            cancel=CancelToken(), settings_env=dict(SETTINGS), outputs_dir=store.outputs_dir,
                            on_log=lambda line: None)
    places.run(ctx, runner=llm, time_fn=lambda: 100.0, adapters={("image", "local"): image})

    assert events == ["P1", "D3", "image:variant_day", "R1", "R1v2", "image:image"]
    plate, prop_image = (request.prompt for request in image.requests)
    # Plan 26 H1: the core last, after the series, the style and the place.
    assert plate.split("\n\n")[-1].startswith("Establishing wide shot of an empty set, day, no people, no characters:")
    assert "on the left palm-leaf huts" in plate and "hard tropical sun" in plate
    assert "Plage" not in plate and "Coco" not in plate
    # A1 (phase 7 quality overhaul): the reference image carries no scale phrase (it invited a hand
    # holding the object for a size reference) -- "fits in one hand" stays out of this prompt, even
    # though R1v2 wrote it as the prop's scale_phrase; render_prop keeps it for a keyframe instead.
    assert prop_image.split("\n\n")[-1].startswith(
        "Reference image of the object alone on a plain surface, nothing holding it")
    assert "fits in one hand" not in prop_image and "a hollow coconut with a curly cord and a brass dial" in prop_image
    # Kiwilo's height went to R1v2; D3's prop and R1v2's names became ids.
    assert "Owner: Kiwilo (lean human body; 175 cm tall)" in llm.of("R1v2")[0]["user"]
    place = store.list_entities(story_id, "places")[0]
    prop = store.list_entities(story_id, "props")[0]
    assert place["look"]["props_here"] == [prop["prop_id"]]
    assert prop["look"]["where_when"] == [{"ep": 1, "holder_char_id": "char_kiwilo", "place_id": place["place_id"],
                                           "note": "rings at dawn"}]
    assert workflow.places_units(store, store.get(story_id))["llm_calls"] == 0


def _d2_look(height):
    return schemas.d2_look(_d2(height))


# Phase 7 stage 5a (DEC-228): re-pinned on purpose -- the text regenerate rewrites the dossier (D1) too,
# between K1 and D2: three calls.
def test_v2_regenerating_a_characters_text_writes_its_look_again(tmp_path, hermetic):
    from clipping.aistory import workflow
    from clipping.aistory.steps import regenerate

    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store, v2=True)
    kiwi = _character("char_kiwilo", "Kiwilo", "a fuzzy kiwi", ["gold chain", "linen shirt"], look=_d2_look(175))
    kiwi["dossier"] = {**_d1("Né sur la plage."), "relationships": []}
    store.write_entity(story_id, "characters", kiwi, now=NOW)
    target = "character:char_kiwilo:text"
    assert workflow.target_units(store, store.get(story_id), regenerate.parse_target(target))["llm_calls"] == 3

    events = Events()
    llm = FakeLLM(events, K1=[_k1("a fuzzy kiwi in a hat", ["gold chain", "straw hat"])],
                  D1=[_d1("Né dans un chapeau.")], D2=[_d2(150)])
    ctx = steps.StepContext(job_id="job000000001", story_id=story_id, step="regenerate", ep=None,
                            params={"target": target, "note": "plus petit"}, cancel=CancelToken(),
                            settings_env=dict(SETTINGS), outputs_dir=store.outputs_dir, on_log=lambda line: None)
    regenerate.run(ctx, runner=llm, time_fn=lambda: 100.0)
    assert events == ["K1", "D1", "D2"]
    assert "Current values:" in llm.of("D2")[0]["user"] and "plus petit" in llm.of("D2")[0]["user"]
    assert "Né sur la plage." in llm.of("D1")[0]["user"] and "plus petit" in llm.of("D1")[0]["user"]
    saved = store.read_entity(story_id, "characters", "char_kiwilo")
    assert saved["look"]["height_cm"] == 150 and saved["dossier"]["backstory"] == "Né dans un chapeau."


def test_the_largest_look_replies_fit_their_caps():
    """The plan's caps (D2 380, D3 300, R1v2 220) hold the largest English
    reply each ask allows: every word and count limit hit, 6 characters a
    word (chars/4; English fields, no French factor). D3 at P1's most time
    variants (3) and its reply's props bound; R1v2 at its where-when bound."""
    import json

    from clipping.providers.pacing import estimate_tokens

    def words(n):
        return " ".join(["abcde"] * n)

    d2 = {"build": words(15), "silhouette": words(12), "face": words(15), "hair": words(12),
          "skin_material": words(12), "height_cm": 175, "palette": [words(3)] * 4,
          "wardrobe_sets": [{"id": "night_out", "context": words(8), "items": words(20)}] * 3,
          "season_change": words(20)}
    d3 = {"layout_map": {key: words(15) for key in schemas.LAYOUT_MAP_KEYS}, "scale_note": words(15),
          "lighting": {variant: words(15) for variant in ("day", "night", "dusk")},
          "props_here": ["x" * 60] * schemas.D3_PROPS_HERE_MAX}
    r1v2 = {"scale_cm": 12.5, "material": words(8), "colour": words(6), "scale_phrase": words(10),
            "where_when": [{"ep": 12, "holder": "x" * 60, "place": "y" * 60, "note": words(12)}]
            * schemas.R1V2_WHERE_WHEN_MAX}
    for prompt_id, reply, cap in (("D2", d2, 380), ("D3", d3, 300), ("R1v2", r1v2, 220)):
        needed = estimate_tokens(json.dumps(reply, ensure_ascii=False))
        assert prompts.MAX_TOKENS[prompt_id] == cap
        assert cap // 2 < needed <= cap, (prompt_id, needed)
