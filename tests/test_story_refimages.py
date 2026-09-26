"""The reference images of a story's cast, places and props (AI Story phase 2,
stage 5; spec 2.3-2.5, 5, 8.1).

``clipping/aistory/refimages.py`` makes a character's portrait (text to
image), turnaround and expressions (edited with the portrait and the design
references as reference images, or -- only when the user chose it -- from
text with the portrait's seed, labelled prompt-only), a place's master plate
and time variants, and a prop's image. When no editor can run it stops before
any generation request (spec 8.1: "stops and asks", never switched
automatically).

Offline and hermetic, as ``test_style_preview.py``: every provider answers
through a fake adapter or a fake transport (the adapters' own
``urllib_transport`` and the OpenAI SDK factory are replaced by stand-ins
that fail the test), the Settings values are test values, and every default
path -- the free-tier counters, the daily spend, the outputs root -- is
redirected under ``tmp_path``. The repository's own ``data/`` files and
``outputs/stories`` are fingerprinted before and after every test.

Stdlib + pytest (the CI environment, DEC-012). The module under test is
imported inside the tests, so on the parent commit each test fails on its
own instead of the file failing to collect.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from clipping.aistory import prompting, schemas, stylelock, templates
from clipping.aistory.ledger import CostLedger
from clipping.aistory.store import StoryStore
from clipping.cancel import Cancelled, CancelToken
from clipping.providers.generation import GenResult
from clipping.providers.transport import APIConnectionError, Response

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-09-26T10:00:00+00:00"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24

GEN_VARS = (
    "FAL_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
    "POLLINATIONS_API_KEY", "IMAGE_CHAIN", "IMAGE_EDIT_CHAIN", "VIDEO_CHAIN", "TTS_CHAIN", "VISION_CHAIN",
    "LOCAL_COMFYUI_URL", "LOCAL_OLLAMA_URL",
    "ALLOW_PAID", "PER_EPISODE_CAP_USD", "DAILY_CAP_USD", "PER_STORY_CAP_USD", "BUDGET_PROFILE",
    "LLM_CHAIN", "ALLOW_SLOW_CHAIN", "MAX_QUEUED_JOBS",
)
REAL_FILES = tuple(ROOT / "data" / name for name in ("usage.json", "spend.json", "chain_test_ledger.json"))
REAL_STORIES = (ROOT / "outputs" / "stories", ROOT / "outputs" / "stories.json")

# Test values only: every request goes to a fake.
FAL = {"FAL_KEY": "test-fal-key"}
GEMINI = {"GOOGLE_API_KEY": "test-google-key"}
PAID_ON = {"ALLOW_PAID": "1"}
COMFY = {"LOCAL_COMFYUI_URL": "http://comfy.test:8188"}
FREE = {"IMAGE_CHAIN": "pollinations/flux"}
LOCAL_EDIT = {"IMAGE_EDIT_CHAIN": "local/comfyui"}
SEEDREAM = {"IMAGE_EDIT_CHAIN": "fal/seedream-4-edit", **FAL}

CHAR = "char_kiwilo"
PLACE = "place_beach_camp"
PROP = "prop_coconut_phone"
KIWI_DESCRIPTOR = "an anthropomorphic kiwi with fuzzy brown skin and bright green flesh visible at the mouth"
KIWI_ITEMS = ["thin gold chain", "white linen shirt", "left-eyebrow scar"]
PLACE_DESCRIPTOR = "a crescent of white sand with palm-leaf huts and a stone bonfire ring"
LAYOUT_NOTES = "huts on the left, the sea on the right, the bonfire ring at the back"
PROP_DESCRIPTOR = "a hollow coconut with a curly cord and a brass dial"

SEEDREAM_PRICE = 0.03
KONTEXT_PRICE = 0.04


def _fingerprint(path: Path):
    if path.is_symlink() or path.exists():
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
        return "present"
    return None


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, tmp_path):
    """No key, chain, cap or limit of the machine reaches a test; no request
    leaves the process; nothing is written outside ``tmp_path``."""
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env (A-049)
    from clipping.providers import adapters, budget, images, limits, local_comfyui

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in GEN_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("LIMIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USAGE_PATH", str(tmp_path / "data" / "usage.json"))
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "data" / "spend.json"))
    limits.reset()
    budget.reset()
    adapters.load_all()

    def no_network(method, url, **_kwargs):
        raise AssertionError(f"a real request was attempted: {method} {url}")

    def no_sdk(**_kwargs):
        raise AssertionError("the OpenAI SDK was reached")

    monkeypatch.setattr(images, "urllib_transport", no_network)
    monkeypatch.setattr(local_comfyui, "urllib_transport", no_network)
    monkeypatch.setattr(images, "_openai_client", no_sdk)
    monkeypatch.setattr(images, "FAL_POLL_INTERVAL_SECONDS", 0.0)

    before = {path: _fingerprint(path) for path in REAL_FILES + REAL_STORIES}
    yield
    assert limits.default_usage_path().startswith(str(tmp_path))
    assert budget.default_spend_path().startswith(str(tmp_path))
    limits.reset()
    budget.reset()
    assert {path: _fingerprint(path) for path in REAL_FILES + REAL_STORIES} == before


@pytest.fixture
def store(tmp_path):
    return StoryStore(tmp_path / "outputs", on_log=lambda line: None)


# ------------------------------------------------------------------ helpers

def _new():
    """The stage-5 module (absent on the parent commit)."""
    from clipping.aistory import refimages

    return refimages


class Log(list):
    def __call__(self, line):
        self.append(str(line))


class FakeImage:
    """An image adapter that records every probe and every request and
    writes a small PNG whose bytes differ from one call to the next."""

    def __init__(self, *, ext="png", reachable=True, seed=None, meta=None, on_generate=None):
        self.ext = ext
        self.reachable = reachable
        self.seed = seed
        self.meta = meta or {}
        self.on_generate = on_generate
        self.probes = []
        self.requests = []

    def estimate(self, link, request):
        return None

    def probe(self, link, **_kwargs):
        self.probes.append(f"{link.provider}/{link.model}")
        if self.reachable:
            return True, "ok"
        return False, "unreachable at http://comfy.test:8188 (connection refused)"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.requests.append(copy.copy(request))
        if self.on_generate is not None:
            self.on_generate(request)
        path = os.path.join(request.out_dir, f"{request.extra['name']}.{self.ext}")
        with open(path, "wb") as fh:
            fh.write(PNG + str(len(self.requests)).encode())
        return GenResult(provider=link.provider, model=link.model, paths=(path,),
                         seed=request.seed if self.seed is None else self.seed, meta=dict(self.meta))

    @property
    def calls(self):
        return len(self.probes) + len(self.requests)


class FakeTransport:
    """Answers by URL substring, first matching rule wins; records every call."""

    def __init__(self, *rules):
        self.rules = list(rules)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        call = {"method": method, "url": url, "headers": dict(headers or {}), "body": body}
        self.calls.append(call)
        for needle, answer in self.rules:
            if needle in url:
                if isinstance(answer, BaseException):
                    raise answer
                status, payload = answer
                if isinstance(payload, (dict, list)):
                    payload = json.dumps(payload).encode()
                return Response(status, {}, payload)
        raise AssertionError(f"unexpected request {method} {url}")

    def urls(self, needle=""):
        return [call["url"] for call in self.calls if needle in call["url"]]

    def posts(self, needle=""):
        return [call for call in self.calls if call["method"] == "POST" and needle in call["url"]]


def fal_rules():
    return [
        ("/status", (200, {"status": "COMPLETED"})),
        ("/requests/", (200, {"images": [{"url": "https://v3.fal.media/files/sheet.png",
                                          "content_type": "image/png"}]})),
        ("fal.media", (200, PNG)),
        ("queue.fal.run", (200, {"request_id": "req-1"})),
    ]


def gemini_rules():
    image = {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()}}
    return [("generativelanguage.googleapis.com", (200, {"candidates": [{"content": {"parts": [image]}}]}))]


def _no_sleep(seconds):
    raise AssertionError(f"the chain tried to sleep {seconds}s")


def _ref(name, consistency="base", *, source="pollinations/flux", seed=4242):
    return {"name": name, "consistency": consistency, "source": source, "seed": seed, "created_at": NOW}


def _character(char_id=CHAR, name="Kiwilo", **changes):
    doc = {
        "$schema": "character_v1", "char_id": char_id, "name": name, "role": "lead",
        "archetype": "charming schemer", "one_line": "A kiwi who lied about the coconut phone.",
        "descriptor": KIWI_DESCRIPTOR, "signature_items": list(KIWI_ITEMS),
        "personality": {"traits": [], "wants": None, "fears": None, "speech_style": None},
        "relationships": {}, "voice": None, "voice_hints": None,
        "refs": {"portrait": None, "turnaround": None, "expressions": None, "extra": [], "uploads": []},
        "ref_seed": None, "prompt_block": None,
        "state": {"alive": True, "location": None, "arc_notes": []},
        "source": "sketch", "approved_at": None, "created_at": NOW, "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def _place(**changes):
    doc = {
        "$schema": "place_v1", "place_id": PLACE, "name": "Beach camp",
        "one_line": "Where the couples sleep and plot.", "descriptor": PLACE_DESCRIPTOR,
        "layout_notes": LAYOUT_NOTES, "time_variants": {"day": None, "night": None},
        "prompt_block": None, "approved_at": None, "created_at": NOW, "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def _prop(**changes):
    doc = {
        "$schema": "prop_v1", "prop_id": PROP, "name": "Coconut phone",
        "one_line": "The only line off the island.", "descriptor": PROP_DESCRIPTOR, "owner_char_id": CHAR,
        "image": None, "prompt_block": None, "approved_at": None, "created_at": NOW, "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def _lock():
    draft = stylelock.build_style_lock(templates.load_style("fruit_drama"), {}, now=NOW)
    return stylelock.lock_style(draft, now=NOW)


def _story(store, *, route="auto", mode="references", character=None):
    """A French story with a locked ``fruit_drama`` style, a written character
    (Kiwilo), a place with a descriptor and a prop."""
    story_id = store.create(language="fr", seed_text="Kiwilo et Mangella sur une île de téléréalité.",
                            style_template_id="fruit_drama", now=NOW)["story_id"]

    def edit(doc):
        doc["generation_profile"]["route"] = route
        doc["generation_profile"]["consistency_mode"] = mode

    store.update(story_id, edit, now=NOW)
    store.write_doc(story_id, "style_lock.json", _lock(), now=NOW, validator=schemas.style_lock_errors)
    store.write_entity(story_id, "characters", character or _character(), now=NOW)
    store.write_entity(story_id, "places", _place(), now=NOW)
    store.write_entity(story_id, "props", _prop(), now=NOW)
    return story_id


def _plant_file(store, story_id, kind, eid, name, data=PNG) -> str:
    src = Path(store.outputs_dir).parent / f"plant-{kind}-{eid}-{name}"
    src.write_bytes(data)
    return store.write_media(story_id, kind, eid, name, str(src))


def _plant_portrait(store, story_id, *, seed=4242, ref_seed="same", name="portrait.png", data=PNG) -> str:
    path = _plant_file(store, story_id, "characters", CHAR, name, data)
    character = store.read_entity(story_id, "characters", CHAR)
    character["refs"]["portrait"] = _ref(name, seed=seed)
    character["ref_seed"] = seed if ref_seed == "same" else ref_seed
    store.write_entity(story_id, "characters", character, now=NOW)
    return path


def _plant_upload(store, story_id, n) -> str:
    name = f"{n:032x}.png"
    path = _plant_file(store, story_id, "characters", CHAR, name, PNG + bytes([n]))
    character = store.read_entity(story_id, "characters", CHAR)
    character["refs"]["uploads"].append({"name": name, "description": None, "uploaded_at": NOW})
    store.write_entity(story_id, "characters", character, now=NOW)
    return path


def _plant_plate(store, story_id, *, seed=777) -> str:
    path = _plant_file(store, story_id, "places", PLACE, "variant_day.png")
    place = store.read_entity(story_id, "places", PLACE)
    place["time_variants"]["day"] = _ref("variant_day.png", seed=seed)
    store.write_entity(story_id, "places", place, now=NOW)
    return path


def _character_image(store, story_id, which, env, *, adapters=None, transport=None, note=None, cancel=None):
    log = Log()
    ref = _new().character_image(store, story_id, CHAR, which, env=env, on_log=log,
                                 cancel=cancel or CancelToken(), note=note, adapters=adapters,
                                 transport=transport, sleep_fn=_no_sleep, time_fn=lambda: 100.0)
    return ref, log


def _refused(call, *args, error=None, **kwargs):
    """Run *call* expecting a RefImageError (or *error*); ``(exception, log)``."""
    m = _new()
    log = Log()
    kwargs.setdefault("cancel", CancelToken())
    with pytest.raises(error or m.RefImageError) as caught:
        call(*args, on_log=log, sleep_fn=_no_sleep, time_fn=lambda: 100.0, **kwargs)
    return caught.value, log


def _refs_dir(store, story_id, kind, eid) -> Path:
    return Path(store.story_dir(story_id)) / kind / eid / "refs"


def _doc_bytes(store, story_id, kind, eid) -> bytes:
    filename = {"characters": "character.json", "places": "place.json", "props": "prop.json"}[kind]
    return (Path(store.story_dir(story_id)) / kind / eid / filename).read_bytes()


def _ledger(store, story_id) -> list:
    path = Path(store.story_dir(story_id)) / "cost_ledger.json"
    return CostLedger(str(path)).entries() if path.exists() else []


def _rows(entries):
    return [(e["step"], e["provider"], e["model"], e["unit"], e["qty"], e["est_usd"], e["paid"], e["ep"])
            for e in entries]


def _spend(tmp_path) -> Path:
    return tmp_path / "data" / "spend.json"


def _usage(tmp_path) -> Path:
    return tmp_path / "data" / "usage.json"


# ======================================================== prompts, no name

def test_the_new_prompt_builders_are_used_and_take_no_name():
    import inspect

    for builder in (prompting.prop_image_prompt, prompting.variant_prompt):
        assert "name" not in inspect.signature(builder).parameters


# ============================================================ the portrait

def test_the_portrait_is_text_to_image_seeded_labelled_base_booked_and_named_after_its_slot(store, tmp_path):
    m = _new()
    story_id = _story(store)
    t2i = FakeImage()

    ref, log = _character_image(store, story_id, "portrait", FREE, adapters={("image", "pollinations"): t2i})

    lock = store.read_doc(story_id, "style_lock.json")
    seed = m.image_seed(story_id, "characters", CHAR)
    [request] = t2i.requests
    assert (request.kind, request.width, request.height, request.seed, request.references) == (
        "image", 720, 1280, seed, ())
    assert request.prompt == prompting.portrait_prompt(lock, descriptor=KIWI_DESCRIPTOR, signature_items=KIWI_ITEMS)
    assert request.negative == prompting.negative_prompt(lock)
    # Appearance, never the name (spec 2.3).
    assert KIWI_DESCRIPTOR in request.prompt and lock["rendering"] in request.prompt
    assert all(item in request.prompt for item in KIWI_ITEMS)
    assert "Kiwilo" not in request.prompt and "Kiwilo" not in request.negative

    assert ref == {"name": "portrait.png", "consistency": "base", "source": "pollinations/flux", "seed": seed,
                   "created_at": ref["created_at"]}
    character = store.read_entity(story_id, "characters", CHAR)
    assert character["refs"]["portrait"] == ref
    assert character["ref_seed"] == seed
    assert character["prompt_block"] == prompting.character_prompt_block(
        lock, descriptor=KIWI_DESCRIPTOR, signature_items=KIWI_ITEMS)
    assert (character["refs"]["turnaround"], character["refs"]["expressions"]) == (None, None)
    assert sorted(p.name for p in _refs_dir(store, story_id, "characters", CHAR).iterdir()) == ["portrait.png"]
    assert (_refs_dir(store, story_id, "characters", CHAR) / "portrait.png").read_bytes() == PNG + b"1"

    assert _rows(_ledger(store, story_id)) == [
        ("character_image:char_kiwilo:portrait", "pollinations", "flux", "image", 1, 0.0, False, None)]
    assert not _spend(tmp_path).exists()
    assert log[0] == "🖼 Kiwilo portrait: 720x1280 on IMAGE_CHAIN, text to image, route auto."
    assert log[-1] == "🖼 Kiwilo portrait via pollinations/flux ($0.000), consistency: base"


def test_the_portrait_reuses_the_characters_seed_and_records_the_seed_the_provider_answered(store):
    story_id = _story(store, character=_character(ref_seed=31337))
    t2i = FakeImage(seed=99)

    ref, _log = _character_image(store, story_id, "portrait", FREE, adapters={("image", "pollinations"): t2i})

    assert t2i.requests[0].seed == 31337
    assert ref["seed"] == 99
    assert store.read_entity(story_id, "characters", CHAR)["ref_seed"] == 99


def test_the_seed_is_deterministic_per_story_and_entity():
    m = _new()

    seeds = {m.image_seed("aaaaaaaaaaaa", kind, eid) for kind, eid in
             (("characters", CHAR), ("places", PLACE), ("props", PROP))}
    assert len(seeds) == 3
    assert m.image_seed("aaaaaaaaaaaa", "characters", CHAR) == m.image_seed("aaaaaaaaaaaa", "characters", CHAR)
    assert m.image_seed("aaaaaaaaaaaa", "characters", CHAR) != m.image_seed("bbbbbbbbbbbb", "characters", CHAR)
    assert all(1 <= seed < 2**31 - 1 for seed in seeds)


def test_a_character_not_yet_written_is_refused_before_anything(store):
    m = _new()
    story_id = _story(store, character=_character(descriptor=None, signature_items=[]))
    t2i = FakeImage()

    error, _log = _refused(m.character_image, store, story_id, CHAR, "portrait", env=FREE,
                           adapters={("image", "pollinations"): t2i})

    assert "write the character first" in str(error)
    assert t2i.calls == 0 and _ledger(store, story_id) == []


def test_an_unknown_image_or_character_is_refused(store):
    m = _new()
    story_id = _story(store)

    error, _log = _refused(m.character_image, store, story_id, CHAR, "extra_01", env=FREE)
    assert "not a character image" in str(error)
    with pytest.raises(KeyError):
        m.character_image(store, story_id, "char_nobody", "portrait", env=FREE, on_log=Log(),
                          cancel=CancelToken())


def test_a_sheet_needs_the_portrait_first(store):
    m = _new()
    story_id = _story(store)
    editor, t2i = FakeImage(), FakeImage()
    adapters = {("image_edit", "local"): editor, ("image", "pollinations"): t2i}

    error, _log = _refused(m.character_image, store, story_id, CHAR, "turnaround", env={**FREE, **LOCAL_EDIT},
                           adapters=adapters)
    assert "make the portrait first" in str(error)

    # A portrait listed but whose file is gone is no portrait.
    character = store.read_entity(story_id, "characters", CHAR)
    character["refs"]["portrait"] = _ref("portrait.png")
    store.write_entity(story_id, "characters", character, now=NOW)
    error, _log = _refused(m.character_image, store, story_id, CHAR, "expressions", env={**FREE, **LOCAL_EDIT},
                           adapters=adapters)
    assert "make the portrait first" in str(error)
    assert editor.calls == 0 and t2i.calls == 0 and _ledger(store, story_id) == []


# ===================================================== sheets, references

def test_a_turnaround_with_an_editor_is_edited_from_the_portrait_and_the_uploads(store):
    story_id = _story(store)
    portrait = _plant_portrait(store, story_id, seed=4242)
    uploads = [_plant_upload(store, story_id, n) for n in (1, 2)]
    before = store.read_entity(story_id, "characters", CHAR)
    editor, t2i = FakeImage(), FakeImage()

    ref, log = _character_image(store, story_id, "turnaround", {**FREE, **LOCAL_EDIT},
                                adapters={("image_edit", "local"): editor, ("image", "pollinations"): t2i})

    lock = store.read_doc(story_id, "style_lock.json")
    [request] = editor.requests
    assert request.references == (portrait, *uploads)
    assert (request.kind, request.width, request.height, request.seed) == ("image_edit", 1280, 720, 4242)
    assert request.prompt == prompting.turnaround_prompt(lock, descriptor=KIWI_DESCRIPTOR,
                                                         signature_items=KIWI_ITEMS)
    assert "Kiwilo" not in request.prompt
    assert t2i.calls == 0  # never the text-to-image chain in references mode
    # Asked once by the readiness check (only a local editor could run), once by the runner.
    assert editor.probes == ["local/comfyui", "local/comfyui"]

    assert ref == {"name": "turnaround.png", "consistency": "references", "source": "local/comfyui",
                   "seed": 4242, "created_at": ref["created_at"]}
    after = store.read_entity(story_id, "characters", CHAR)
    assert after["refs"]["turnaround"] == ref
    for key in ("portrait", "expressions", "extra", "uploads"):
        assert after["refs"][key] == before["refs"][key], key
    assert (after["ref_seed"], after["prompt_block"]) == (before["ref_seed"], before["prompt_block"])
    assert _rows(_ledger(store, story_id)) == [
        ("character_image:char_kiwilo:turnaround", "local", "comfyui", "image", 1, 0.0, False, None)]
    assert log[0] == "🖼 Kiwilo turnaround: 1280x720 on IMAGE_EDIT_CHAIN with 3 reference images, route auto."
    assert log[-1] == "🖼 Kiwilo turnaround via local/comfyui ($0.000), consistency: references"
    assert not any("🟡" in line for line in log)


def test_expressions_are_edited_at_their_own_size(store):
    story_id = _story(store)
    _plant_portrait(store, story_id)
    editor = FakeImage()

    ref, _log = _character_image(store, story_id, "expressions", LOCAL_EDIT,
                                 adapters={("image_edit", "local"): editor})

    lock = store.read_doc(story_id, "style_lock.json")
    assert (editor.requests[0].width, editor.requests[0].height) == (1200, 800)
    assert editor.requests[0].prompt == prompting.expressions_prompt(
        lock, descriptor=KIWI_DESCRIPTOR, signature_items=KIWI_ITEMS)
    assert (ref["name"], ref["consistency"]) == ("expressions.png", "references")


def test_at_most_four_references_the_portrait_first_then_the_oldest_uploads(store):
    story_id = _story(store)
    portrait = _plant_portrait(store, story_id)
    uploads = [_plant_upload(store, story_id, n) for n in (1, 2, 3, 4)]
    editor = FakeImage()

    _ref_, log = _character_image(store, story_id, "turnaround", LOCAL_EDIT,
                                  adapters={("image_edit", "local"): editor})

    assert editor.requests[0].references == (portrait, *uploads[:3])
    assert ("ℹ️ Kiwilo turnaround: 1 design reference(s) left out -- an edit takes at most 4 reference "
            "images (the portrait first).") in log


def test_an_upload_whose_file_is_gone_is_not_sent(store):
    story_id = _story(store)
    portrait = _plant_portrait(store, story_id)
    kept = _plant_upload(store, story_id, 1)
    Path(_plant_upload(store, story_id, 2)).unlink()
    editor = FakeImage()

    _ref_, log = _character_image(store, story_id, "turnaround", LOCAL_EDIT,
                                  adapters={("image_edit", "local"): editor})

    assert editor.requests[0].references == (portrait, kept)
    assert any("design reference 00000000000000000000000000000002.png is missing" in line for line in log)


# =========================================================== stop and ask

def test_no_editor_on_the_route_stops_before_any_call_of_any_kind(store, tmp_path):
    """Route api keeps the local editor out; the paid editors are keyless or
    refused while paid is off. The pre-check alone decides: not even a probe."""
    m = _new()
    story_id = _story(store, route="api")
    _plant_portrait(store, story_id)
    fakes = {provider: FakeImage() for provider in ("local", "gemini", "fal", "openai")}
    adapters = {("image_edit", p): fake for p, fake in fakes.items()}
    t2i = FakeImage()
    adapters[("image", "pollinations")] = t2i
    before = _doc_bytes(store, story_id, "characters", CHAR)
    story_before = store.get(story_id)

    error, log = _refused(m.character_image, store, story_id, CHAR, "turnaround", env={**FREE, **GEMINI},
                          adapters=adapters, error=m.NeedsEditor)

    assert error.reasons == [
        "local/comfyui: route is api",
        "gemini/nano-banana-2-lite: refused: est $0.034 on gemini/nano-banana-2-lite; allow_paid is off "
        "(today $0.00 of $3.00)",
        "fal/seedream-4-edit: no API key (FAL_KEY is not set)",
        "fal/flux-kontext-pro: no API key (FAL_KEY is not set)",
        "gemini/nano-banana-2: refused: est $0.067 on gemini/nano-banana-2; allow_paid is off "
        "(today $0.00 of $3.00)",
    ]
    assert (error.readiness["ready"], error.readiness["route_class"], error.readiness["link"]) == (
        False, "blocked", None)
    assert "Kiwilo turnaround needs an editor" in str(error) and "prompt-only" in str(error)
    assert isinstance(error, m.RefImageError)
    # Zero calls on every adapter, nothing booked, nothing written, the mode untouched.
    assert [fake.calls for fake in fakes.values()] == [0, 0, 0, 0] and t2i.calls == 0
    assert _ledger(store, story_id) == [] and not _spend(tmp_path).exists() and not _usage(tmp_path).exists()
    assert sorted(p.name for p in _refs_dir(store, story_id, "characters", CHAR).iterdir()) == ["portrait.png"]
    assert _doc_bytes(store, story_id, "characters", CHAR) == before
    assert store.get(story_id)["generation_profile"] == story_before["generation_profile"]
    assert log[0].startswith("✋ Kiwilo turnaround needs an editor")


def test_an_unreachable_comfyui_and_no_paid_editor_needs_an_editor_with_zero_generation_calls(store, tmp_path):
    """The shipped IMAGE_EDIT_CHAIN with the real adapters: ComfyUI's server is
    down, Gemini is keyed but paid is off, fal has no key. Only the ComfyUI
    probe is sent -- the question "is the server there" -- and no request."""
    m = _new()
    story_id = _story(store)
    _plant_portrait(store, story_id)
    transport = FakeTransport(("comfy.test", APIConnectionError("connection refused")),
                              *gemini_rules(), *fal_rules())
    before = _doc_bytes(store, story_id, "characters", CHAR)

    error, log = _refused(m.character_image, store, story_id, CHAR, "expressions", env={**GEMINI, **COMFY},
                          transport=transport, error=m.NeedsEditor)

    assert error.reasons[0].startswith("local/comfyui: unreachable at http://comfy.test:8188")
    assert "gemini/nano-banana-2-lite: refused: est $0.034 on gemini/nano-banana-2-lite; allow_paid is off" \
        in error.reasons[1]
    assert error.reasons[2] == "fal/seedream-4-edit: no API key (FAL_KEY is not set)"
    assert transport.urls() == ["http://comfy.test:8188/system_stats"]
    assert _ledger(store, story_id) == [] and not _spend(tmp_path).exists()
    assert _doc_bytes(store, story_id, "characters", CHAR) == before
    assert not any("🔁" in line for line in log)


def test_an_unreachable_local_editor_behind_a_fake_is_probed_once_and_never_asked_to_generate(store):
    m = _new()
    story_id = _story(store)
    _plant_portrait(store, story_id)
    comfy = FakeImage(reachable=False)

    error, _log = _refused(m.character_image, store, story_id, CHAR, "turnaround", env=LOCAL_EDIT,
                           adapters={("image_edit", "local"): comfy}, error=m.NeedsEditor)

    assert error.reasons == ["local/comfyui: unreachable at http://comfy.test:8188 (connection refused)"]
    assert (comfy.probes, comfy.requests) == (["local/comfyui"], [])


def test_prompt_only_mode_makes_the_sheet_from_text_with_the_portraits_seed_and_labels_it(store):
    story_id = _story(store, mode="prompt_only")
    # The portrait's own seed is the one reused, whatever the character's ref_seed says.
    _plant_portrait(store, story_id, seed=4242, ref_seed=1111)
    _plant_upload(store, story_id, 1)
    editor, t2i = FakeImage(), FakeImage()

    ref, log = _character_image(store, story_id, "turnaround", {**FREE, **LOCAL_EDIT},
                                adapters={("image_edit", "local"): editor, ("image", "pollinations"): t2i})

    lock = store.read_doc(story_id, "style_lock.json")
    [request] = t2i.requests
    assert (request.kind, request.seed, request.references, request.width, request.height) == (
        "image", 4242, (), 1280, 720)
    assert request.prompt == prompting.turnaround_prompt(lock, descriptor=KIWI_DESCRIPTOR,
                                                         signature_items=KIWI_ITEMS)
    assert editor.calls == 0
    assert (ref["consistency"], ref["source"], ref["seed"]) == ("prompt_only", "pollinations/flux", 4242)
    assert store.read_entity(story_id, "characters", CHAR)["refs"]["turnaround"] == ref
    assert "🟡 Kiwilo turnaround: consistency: prompt-only" in log
    assert log[0] == ("🖼 Kiwilo turnaround: 1280x720 on IMAGE_CHAIN, text only with the portrait's seed 4242, "
                      "route auto.")
    assert log[-1] == "🖼 Kiwilo turnaround via pollinations/flux ($0.000), consistency: prompt-only"


# ============================================================ the route

def test_route_local_never_calls_an_api_editor(store):
    story_id = _story(store, route="local")
    _plant_portrait(store, story_id)
    comfy, fal = FakeImage(), FakeImage()
    env = {"IMAGE_EDIT_CHAIN": "fal/seedream-4-edit,local/comfyui", **FAL, **PAID_ON}

    ref, log = _character_image(store, story_id, "turnaround", env,
                                adapters={("image_edit", "local"): comfy, ("image_edit", "fal"): fal})

    assert ref["source"] == "local/comfyui"
    assert fal.calls == 0 and len(comfy.requests) == 1
    assert "   ⏭ Skipping fal/seedream-4-edit: route is local." in log


def test_route_local_with_the_local_editor_down_needs_an_editor_and_never_reaches_the_api(store):
    m = _new()
    story_id = _story(store, route="local")
    _plant_portrait(store, story_id)
    comfy, fal = FakeImage(reachable=False), FakeImage()
    env = {"IMAGE_EDIT_CHAIN": "local/comfyui,fal/seedream-4-edit", **FAL, **PAID_ON}

    error, _log = _refused(m.character_image, store, story_id, CHAR, "turnaround", env=env,
                           adapters={("image_edit", "local"): comfy, ("image_edit", "fal"): fal},
                           error=m.NeedsEditor)

    assert "fal/seedream-4-edit: route is local" in error.reasons
    assert fal.calls == 0 and comfy.requests == []


# =============================================================== the money

def test_paid_off_refuses_a_paid_editor_with_the_numbers_and_sends_nothing(store, tmp_path):
    m = _new()
    story_id = _story(store)
    _plant_portrait(store, story_id)
    transport = FakeTransport(*fal_rules())

    error, _log = _refused(m.character_image, store, story_id, CHAR, "turnaround", env=SEEDREAM,
                           transport=transport, error=m.NeedsEditor)

    assert error.reasons == [
        "fal/seedream-4-edit: refused: est $0.030 on fal/seedream-4-edit; allow_paid is off (today $0.00 of $3.00)"]
    assert transport.calls == []  # RC-T3 / RC-P10
    assert _ledger(store, story_id) == [] and not _spend(tmp_path).exists()


def test_paid_on_within_the_caps_sends_one_paid_request_booked_once(store, tmp_path, monkeypatch):
    from clipping.providers import budget
    from clipping.providers.transport import data_url

    booked = []
    real_record = budget.record
    monkeypatch.setattr(budget, "record", lambda est, **kw: booked.append(est) or real_record(est, **kw))
    story_id = _story(store)
    portrait = _plant_portrait(store, story_id, seed=4242)
    uploads = [_plant_upload(store, story_id, n) for n in (1, 2)]
    transport = FakeTransport(*fal_rules())

    ref, log = _character_image(store, story_id, "turnaround", {**SEEDREAM, **PAID_ON}, transport=transport)

    [submit] = transport.posts("queue.fal.run")  # one attempt, never a retry (DEC-106)
    body = json.loads(submit["body"])
    assert body["image_urls"] == [data_url(p) for p in (portrait, *uploads)]
    assert (body["image_size"], body["seed"]) == ({"width": 1280, "height": 720}, 4242)
    assert booked == [SEEDREAM_PRICE]
    assert list(json.loads(_spend(tmp_path).read_text())["days"].values()) == [SEEDREAM_PRICE]
    assert _rows(_ledger(store, story_id)) == [
        ("character_image:char_kiwilo:turnaround", "fal", "fal-ai/bytedance/seedream/v4/edit", "image", 1,
         SEEDREAM_PRICE, True, None)]
    assert not _usage(tmp_path).exists()  # a paid call never touches the free counters
    assert (ref["source"], ref["consistency"]) == ("fal/seedream-4-edit", "references")
    assert "   💸 fal/seedream-4-edit: est $0.030 (paid, allowed)" in log
    assert "   🔁 fal/seedream-4-edit: attempt 1/1" in log
    assert log[-1] == "🖼 Kiwilo turnaround via fal/seedream-4-edit ($0.030 paid), consistency: references"


@pytest.mark.parametrize("caps, spent, expected", [
    ({"PER_STORY_CAP_USD": "0.02"}, 0.0,
     "refused: est $0.030 on fal/seedream-4-edit would bring this story to $0.03 of its $0.02 cap"),
    ({"PER_STORY_CAP_USD": "0.10"}, 0.09,
     "refused: est $0.030 on fal/seedream-4-edit would bring this story to $0.12 of its $0.10 cap"),
    ({"DAILY_CAP_USD": "0.01"}, 0.0,
     "refused: est $0.030 on fal/seedream-4-edit would bring today to $0.03 of the $0.01 daily cap"),
])
def test_a_cap_that_does_not_fit_is_refused_before_any_request(store, tmp_path, caps, spent, expected):
    m = _new()
    story_id = _story(store)
    _plant_portrait(store, story_id)
    if spent:
        CostLedger(str(Path(store.story_dir(story_id)) / "cost_ledger.json")).append(
            step="earlier", provider="fal", model="x", unit="image", qty=1, est_usd=spent, paid=True)
    transport = FakeTransport(*fal_rules())

    error, _log = _refused(m.character_image, store, story_id, CHAR, "turnaround",
                           env={**SEEDREAM, **PAID_ON, **caps}, transport=transport, error=m.NeedsEditor)

    assert error.reasons == [f"fal/seedream-4-edit: {expected}"]
    assert transport.calls == []
    assert len(_ledger(store, story_id)) == (1 if spent else 0) and not _spend(tmp_path).exists()


def test_flux_kontext_takes_only_the_portrait_and_the_log_says_so(store):
    from clipping.providers.transport import data_url

    story_id = _story(store)
    portrait = _plant_portrait(store, story_id)
    _plant_upload(store, story_id, 1)
    transport = FakeTransport(*fal_rules())

    ref, log = _character_image(store, story_id, "turnaround",
                                {"IMAGE_EDIT_CHAIN": "fal/flux-kontext-pro", **FAL, **PAID_ON}, transport=transport)

    [submit] = transport.posts("queue.fal.run")
    assert json.loads(submit["body"])["image_url"] == data_url(portrait)
    assert ref["source"] == "fal/flux-kontext-pro"
    assert _ledger(store, story_id)[0]["est_usd"] == KONTEXT_PRICE
    assert ("ℹ️ Kiwilo turnaround: fal/flux-kontext-pro uses only its first 1 reference image(s); "
            "1 more were sent and not used.") in log


def test_a_paid_gemini_editor_records_that_it_does_not_honour_seeds(store):
    story_id = _story(store)
    _plant_portrait(store, story_id, seed=4242)
    transport = FakeTransport(*gemini_rules())

    ref, log = _character_image(store, story_id, "turnaround",
                                {"IMAGE_EDIT_CHAIN": "gemini/nano-banana-2-lite", **GEMINI, **PAID_ON},
                                transport=transport)

    assert (ref["source"], ref["seed"]) == ("gemini/nano-banana-2-lite", 4242)
    [post] = transport.posts()
    parts = json.loads(post["body"])["contents"][0]["parts"]
    assert len(parts) == 2 and "inline_data" in parts[1]  # the prompt, then the portrait
    assert ("ℹ️ Kiwilo turnaround: gemini/nano-banana-2-lite does not honour seeds; seed 4242 is recorded, "
            "not reproducible.") in log


def test_an_editor_that_runs_and_fails_is_a_failure_not_a_missing_editor(store, tmp_path):
    m = _new()
    story_id = _story(store)
    _plant_portrait(store, story_id)
    transport = FakeTransport(("queue.fal.run", (500, b'{"detail": "boom"}')))
    before = _doc_bytes(store, story_id, "characters", CHAR)

    error, _log = _refused(m.character_image, store, story_id, CHAR, "turnaround", env={**SEEDREAM, **PAID_ON},
                           transport=transport)

    assert not isinstance(error, m.NeedsEditor)
    assert "no link of IMAGE_EDIT_CHAIN could make it on route auto" in str(error)
    [reason] = error.reasons
    assert reason.startswith("fal/seedream-4-edit: HttpStatusError: HTTP 500")
    assert reason.endswith("(paid link: not retried, a second request could be billed again)")
    assert len(transport.posts()) == 1  # one paid attempt (DEC-106)
    assert _ledger(store, story_id) == [] and not _spend(tmp_path).exists()  # nothing answered, nothing booked
    assert _doc_bytes(store, story_id, "characters", CHAR) == before


def test_no_link_of_the_image_chain_names_every_reason_with_the_numbers(store, tmp_path):
    m = _new()
    story_id = _story(store)
    transport = FakeTransport(*gemini_rules(), *fal_rules())
    env = {"IMAGE_CHAIN": "gemini/nano-banana-2-lite,fal/flux-schnell", **GEMINI, **FAL}

    error, log = _refused(m.character_image, store, story_id, CHAR, "portrait", env=env, transport=transport)

    assert not isinstance(error, m.NeedsEditor)
    assert error.reasons == [
        "gemini/nano-banana-2-lite: paid link; allow_paid is off (est $0.034 per image; today $0.00 of the "
        "$3.00 daily cap)",
        "fal/flux-schnell: paid link; allow_paid is off (est $0.003 per image; today $0.00 of the "
        "$3.00 daily cap)",
    ]
    assert "no link of IMAGE_CHAIN could make it on route auto" in str(error)
    assert transport.calls == [] and _ledger(store, story_id) == []
    assert store.read_entity(story_id, "characters", CHAR)["refs"]["portrait"] is None
    assert any(line.startswith("✖ Kiwilo portrait not made:") for line in log)


def test_an_answer_that_is_not_an_image_we_keep_is_booked_but_not_stored(store):
    m = _new()
    story_id = _story(store)

    error, _log = _refused(m.character_image, store, story_id, CHAR, "portrait", env=FREE,
                           adapters={("image", "pollinations"): FakeImage(ext="gif")})

    assert "pollinations/flux: the answer is a .gif file" in str(error)
    assert len(_ledger(store, story_id)) == 1
    assert not _refs_dir(store, story_id, "characters", CHAR).exists() or \
        list(_refs_dir(store, story_id, "characters", CHAR).iterdir()) == []
    assert store.read_entity(story_id, "characters", CHAR)["refs"]["portrait"] is None


def test_a_ledger_that_cannot_be_read_stops_before_anything(store):
    m = _new()
    story_id = _story(store)
    ledger = Path(store.story_dir(story_id)) / "cost_ledger.json"
    ledger.write_text("{not json", encoding="utf-8")
    t2i = FakeImage()

    error, _log = _refused(m.character_image, store, story_id, CHAR, "portrait", env=FREE,
                           adapters={("image", "pollinations"): t2i})

    assert "cost_ledger.json cannot be read" in str(error)
    assert t2i.calls == 0 and ledger.read_text(encoding="utf-8") == "{not json"


def test_a_cancelled_job_stops_before_the_call(store):
    story_id = _story(store)
    token = CancelToken()
    token.cancel()
    t2i = FakeImage()

    with pytest.raises(Cancelled):
        _character_image(store, story_id, "portrait", FREE, adapters={("image", "pollinations"): t2i},
                         cancel=token)

    assert t2i.calls == 0 and _ledger(store, story_id) == []


# ==================================================== regenerate, slots

def test_regenerate_with_a_note_changes_only_that_slot_and_appends_the_note(store):
    story_id = _story(store)
    t2i, editor = FakeImage(), FakeImage()
    adapters = {("image", "pollinations"): t2i, ("image_edit", "local"): editor}
    env = {**FREE, **LOCAL_EDIT}
    for which in ("portrait", "turnaround", "expressions"):
        _character_image(store, story_id, which, env, adapters=adapters)
    refs = _refs_dir(store, story_id, "characters", CHAR)
    before_doc = store.read_entity(story_id, "characters", CHAR)
    before_files = {p.name: p.read_bytes() for p in refs.iterdir()}
    lock_bytes = (Path(store.story_dir(story_id)) / "style_lock.json").read_bytes()

    ref, _log = _character_image(store, story_id, "turnaround", env, adapters=adapters,
                                 note="  make Kiwilo's shirt   whiter ")

    lock = store.read_doc(story_id, "style_lock.json")
    prompt = editor.requests[-1].prompt
    locked = prompting.turnaround_prompt(lock, descriptor=KIWI_DESCRIPTOR, signature_items=KIWI_ITEMS)
    assert prompt == f"{locked} Author's note: make the character's shirt whiter."
    assert "Kiwilo" not in prompt
    after_doc = store.read_entity(story_id, "characters", CHAR)
    assert after_doc["refs"]["turnaround"] == ref != before_doc["refs"]["turnaround"]
    for key in ("portrait", "expressions", "extra", "uploads"):
        assert after_doc["refs"][key] == before_doc["refs"][key], key
    assert (after_doc["ref_seed"], after_doc["prompt_block"]) == (before_doc["ref_seed"], before_doc["prompt_block"])
    after_files = {p.name: p.read_bytes() for p in refs.iterdir()}
    assert sorted(after_files) == ["expressions.png", "portrait.png", "turnaround.png"]
    assert after_files["turnaround.png"] != before_files["turnaround.png"]
    assert {k: v for k, v in after_files.items() if k != "turnaround.png"} == {
        k: v for k, v in before_files.items() if k != "turnaround.png"}
    assert (Path(store.story_dir(story_id)) / "style_lock.json").read_bytes() == lock_bytes


def test_a_note_is_checked_before_anything(store):
    m = _new()
    story_id = _story(store)
    t2i = FakeImage()

    error, _log = _refused(m.character_image, store, story_id, CHAR, "portrait", env=FREE,
                           adapters={("image", "pollinations"): t2i}, note="x" * 301)
    assert "at most 300 characters" in str(error)
    error, _log = _refused(m.character_image, store, story_id, CHAR, "portrait", env=FREE,
                           adapters={("image", "pollinations"): t2i}, note=["not", "text"])
    assert "must be text" in str(error)
    assert t2i.calls == 0

    # An empty note is no note.
    _character_image(store, story_id, "portrait", FREE, adapters={("image", "pollinations"): t2i}, note="   ")
    assert "Author's note" not in t2i.requests[0].prompt


def test_every_cast_name_in_a_note_is_replaced_and_punctuation_is_kept(store):
    story_id = _story(store)
    store.write_entity(story_id, "characters", _character("char_mangella", "Mangella"), now=NOW)
    t2i = FakeImage()

    _character_image(store, story_id, "portrait", FREE, adapters={("image", "pollinations"): t2i},
                     note="MANGELLA should not appear, and neither should kiwilo!")

    assert t2i.requests[0].prompt.endswith(
        "Author's note: the character should not appear, and neither should the character!")


def test_a_slots_file_of_another_extension_is_replaced_and_other_slots_are_kept(store):
    story_id = _story(store)
    _plant_portrait(store, story_id, name="portrait.jpg")
    _plant_file(store, story_id, "characters", CHAR, "turnaround.jpg")
    t2i = FakeImage()

    ref, _log = _character_image(store, story_id, "portrait", FREE, adapters={("image", "pollinations"): t2i})

    assert ref["name"] == "portrait.png"
    assert sorted(p.name for p in _refs_dir(store, story_id, "characters", CHAR).iterdir()) == [
        "portrait.png", "turnaround.jpg"]


def test_an_upload_appended_while_the_image_is_made_is_kept(store):
    story_id = _story(store)
    _plant_upload(store, story_id, 1)
    late = f"{2:032x}.png"

    def upload_meanwhile(_request):
        _plant_upload(store, story_id, 2)

    ref, _log = _character_image(store, story_id, "portrait", FREE,
                                 adapters={("image", "pollinations"): FakeImage(on_generate=upload_meanwhile)})

    character = store.read_entity(story_id, "characters", CHAR)
    assert character["refs"]["portrait"] == ref
    assert [entry["name"] for entry in character["refs"]["uploads"]] == [f"{1:032x}.png", late]


# ================================================================= places

def test_the_master_plate_is_text_to_image_labelled_base(store):
    m = _new()
    story_id = _story(store)
    t2i = FakeImage()
    log = Log()

    ref = m.place_image(store, story_id, PLACE, "day", env=FREE, on_log=log, cancel=CancelToken(),
                        adapters={("image", "pollinations"): t2i}, sleep_fn=_no_sleep)

    lock = store.read_doc(story_id, "style_lock.json")
    [request] = t2i.requests
    assert request.prompt == prompting.master_plate_prompt(lock, place_descriptor=PLACE_DESCRIPTOR,
                                                           time_variant="day")
    assert "Beach camp" not in request.prompt
    assert (request.kind, request.width, request.height, request.references) == ("image", 720, 1280, ())
    assert request.seed == m.image_seed(story_id, "places", PLACE)
    assert (ref["name"], ref["consistency"], ref["source"]) == ("variant_day.png", "base", "pollinations/flux")
    place = store.read_entity(story_id, "places", PLACE)
    assert place["time_variants"] == {"day": ref, "night": None}
    assert place["prompt_block"] == prompting.place_prompt_block(lock, descriptor=PLACE_DESCRIPTOR,
                                                                 layout_notes=LAYOUT_NOTES)
    assert _rows(_ledger(store, story_id)) == [
        ("place_image:place_beach_camp:day", "pollinations", "flux", "image", 1, 0.0, False, None)]
    assert (_refs_dir(store, story_id, "places", PLACE) / "variant_day.png").is_file()


def test_a_night_variant_in_references_mode_is_edited_from_the_plate(store):
    m = _new()
    story_id = _story(store)
    plate = _plant_plate(store, story_id, seed=777)
    editor = FakeImage()

    ref = m.place_image(store, story_id, PLACE, "night", env=LOCAL_EDIT, on_log=Log(), cancel=CancelToken(),
                        adapters={("image_edit", "local"): editor})

    lock = store.read_doc(story_id, "style_lock.json")
    [request] = editor.requests
    assert request.references == (plate,)
    assert (request.kind, request.seed, request.width, request.height) == ("image_edit", 777, 720, 1280)
    assert request.prompt == prompting.variant_prompt(lock, place_descriptor=PLACE_DESCRIPTOR, variant="night")
    assert (ref["name"], ref["consistency"], ref["source"]) == ("variant_night.png", "references", "local/comfyui")
    place = store.read_entity(story_id, "places", PLACE)
    assert place["time_variants"]["night"] == ref
    assert place["time_variants"]["day"]["name"] == "variant_day.png"
    assert _ledger(store, story_id)[0]["step"] == "place_image:place_beach_camp:night"


def test_a_variant_needs_the_master_plate_first(store):
    m = _new()
    story_id = _story(store)
    editor = FakeImage()

    error, _log = _refused(m.place_image, store, story_id, PLACE, "night", env=LOCAL_EDIT,
                           adapters={("image_edit", "local"): editor})

    assert "make the master plate (day) first" in str(error)
    assert editor.calls == 0


def test_a_variant_with_no_editor_needs_an_editor_and_changes_nothing(store, tmp_path):
    m = _new()
    story_id = _story(store)
    _plant_plate(store, story_id)
    before = _doc_bytes(store, story_id, "places", PLACE)
    transport = FakeTransport(*fal_rules())

    error, _log = _refused(m.place_image, store, story_id, PLACE, "night", env=SEEDREAM, transport=transport,
                           error=m.NeedsEditor)

    assert "Beach camp night needs an editor" in str(error)
    assert transport.calls == [] and _ledger(store, story_id) == []
    assert _doc_bytes(store, story_id, "places", PLACE) == before
    assert sorted(p.name for p in _refs_dir(store, story_id, "places", PLACE).iterdir()) == ["variant_day.png"]


def test_a_variant_in_prompt_only_mode_uses_the_plates_seed_and_is_labelled(store):
    m = _new()
    story_id = _story(store, mode="prompt_only")
    _plant_plate(store, story_id, seed=777)
    t2i, editor = FakeImage(), FakeImage()
    log = Log()

    ref = m.place_image(store, story_id, PLACE, "golden_hour", env={**FREE, **LOCAL_EDIT}, on_log=log,
                        cancel=CancelToken(), adapters={("image", "pollinations"): t2i,
                                                        ("image_edit", "local"): editor})

    lock = store.read_doc(story_id, "style_lock.json")
    [request] = t2i.requests
    assert (request.kind, request.seed, request.references) == ("image", 777, ())
    assert request.prompt == prompting.master_plate_prompt(lock, place_descriptor=PLACE_DESCRIPTOR,
                                                           time_variant="golden hour")
    assert editor.calls == 0
    assert (ref["name"], ref["consistency"]) == ("variant_golden_hour.png", "prompt_only")
    # A variant the place did not list yet is added.
    assert store.read_entity(story_id, "places", PLACE)["time_variants"]["golden_hour"] == ref
    assert "🟡 Beach camp golden_hour: consistency: prompt-only" in log


@pytest.mark.parametrize("variant", ["Night", "../day", "", "a" * 21, None, "day/../x"])
def test_a_bad_variant_name_is_refused_before_anything(store, variant):
    m = _new()
    story_id = _story(store)
    t2i = FakeImage()

    error, _log = _refused(m.place_image, store, story_id, PLACE, variant, env=FREE,
                           adapters={("image", "pollinations"): t2i})

    assert "is not a time variant name" in str(error)
    assert t2i.calls == 0


def test_a_place_without_a_descriptor_is_refused(store):
    m = _new()
    story_id = _story(store)
    store.write_entity(story_id, "places", _place(descriptor=None, layout_notes=None), now=NOW)

    error, _log = _refused(m.place_image, store, story_id, PLACE, "day", env=FREE)

    assert "write the place first" in str(error)


# ================================================================== props

def test_a_prop_image_is_text_to_image_labelled_base(store):
    m = _new()
    story_id = _story(store)
    t2i = FakeImage()
    log = Log()

    ref = m.prop_image(store, story_id, PROP, env=FREE, on_log=log, cancel=CancelToken(),
                       adapters={("image", "pollinations"): t2i})

    lock = store.read_doc(story_id, "style_lock.json")
    [request] = t2i.requests
    assert request.prompt == prompting.prop_image_prompt(lock, descriptor=PROP_DESCRIPTOR)
    assert "Coconut phone" not in request.prompt
    assert (request.kind, request.width, request.height) == ("image", 1024, 1024)
    assert request.seed == m.image_seed(story_id, "props", PROP)
    assert (ref["name"], ref["consistency"]) == ("image.png", "base")
    prop = store.read_entity(story_id, "props", PROP)
    assert prop["image"] == ref
    assert prop["prompt_block"] == prompting.prop_prompt_block(lock, descriptor=PROP_DESCRIPTOR)
    assert _rows(_ledger(store, story_id)) == [
        ("prop_image:prop_coconut_phone", "pollinations", "flux", "image", 1, 0.0, False, None)]
    assert log[-1] == "🖼 Coconut phone image via pollinations/flux ($0.000), consistency: base"


def test_a_prop_without_a_descriptor_is_refused(store):
    m = _new()
    story_id = _story(store)
    store.write_entity(story_id, "props", _prop(descriptor=None), now=NOW)

    error, _log = _refused(m.prop_image, store, story_id, PROP, env=FREE)

    assert "write the prop first" in str(error)


# ======================================================== edit readiness

def test_edit_readiness_is_a_no_call_precheck_in_the_style_preview_shape(store, tmp_path):
    from clipping.aistory.steps import style_preview

    m = _new()
    story_id = _story(store, route="api")
    story = store.get(story_id)

    body = m.edit_readiness(story, env={})  # the shipped IMAGE_EDIT_CHAIN, no key at all

    assert set(body) == set(style_preview.estimate({}, route="api"))
    assert (body["ready"], body["route_class"], body["link"], body["est_usd"]) == (False, "blocked", None, 0.0)
    assert [(row["link"], row["reason"]) for row in body["links"]] == [
        ("local/comfyui", "route is api"),
        ("gemini/nano-banana-2-lite", "no API key (GOOGLE_API_KEY is not set)"),
        ("fal/seedream-4-edit", "no API key (FAL_KEY is not set)"),
        ("fal/flux-kontext-pro", "no API key (FAL_KEY is not set)"),
        ("gemini/nano-banana-2", "no API key (GOOGLE_API_KEY is not set)"),
    ]
    assert body["message"].startswith("No link of IMAGE_EDIT_CHAIN can make a reference image on route api: ")
    # An estimate reads; it writes nothing and probes nothing (no_network would fail).
    assert not _usage(tmp_path).exists() and not _spend(tmp_path).exists()


def test_edit_readiness_on_route_auto_names_the_local_editor_probed_when_it_runs(store):
    m = _new()
    story = store.get(_story(store))

    body = m.edit_readiness(story, env=COMFY)

    assert (body["ready"], body["route_class"], body["link"]) == (True, "local", "local/comfyui")
    assert body["links"][0]["reason"] == "probed when it runs"


def test_edit_readiness_reads_the_story_ledger_for_the_story_cap(store):
    m = _new()
    story_id = _story(store)
    CostLedger(str(Path(store.story_dir(story_id)) / "cost_ledger.json")).append(
        step="earlier", provider="fal", model="x", unit="image", qty=1, est_usd=0.09, paid=True)
    env = {**SEEDREAM, **PAID_ON, "PER_STORY_CAP_USD": "0.10"}

    fits = m.edit_readiness(store.get(story_id), env=env)
    read = m.edit_readiness(store.get(story_id), env=env, stories=store)
    two = m.edit_readiness(store.get(story_id), env={**SEEDREAM, **PAID_ON}, qty=2)

    assert (fits["ready"], fits["route_class"], fits["est_usd"]) == (True, "paid", SEEDREAM_PRICE)
    assert read["ready"] is False
    assert read["links"][0]["reason"] == (
        "refused: est $0.030 on fal/seedream-4-edit would bring this story to $0.12 of its $0.10 cap")
    assert (two["est_usd"], two["units"]) == (0.06, {"images": 2})
    assert two["message"] == "2 images on fal/seedream-4-edit, paid: est $0.060."
